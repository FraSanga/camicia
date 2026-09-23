#!/usr/bin/env php
<?php
// Periodic stats snapshot for the public progress page
// (html/user/progress.php). Runs every 15 min via a <task> in
// tools/config.xml -- deliberately NOT queried live on every pageview:
// these are aggregate queries against tables that only grow, and nobody
// visiting the page needs second-by-second freshness (see the page's own
// design discussion). Writes html/user/progress_stats.json atomically
// (temp file + rename, same pattern used elsewhere in this codebase, e.g.
// worker.cpp's do_checkpoint()) so a visitor never sees a half-written file
// mid-update.
//
// The two "Discoveries" numbers (longest game, loops found) are NOT
// computed here -- they're tracked incrementally by
// tools/assimilator/assimilator.cpp the instant each result is assimilated
// (see its own comment), into ../../records_longest.txt and
// ../../records_loops.txt. progress.php reads those two tiny files
// directly; this script only ever produces the period-based aggregates
// that genuinely need a real query over a time window.

$cli_only = true;
require_once("../inc/util_ops.inc");
require_once("../inc/user.inc");

db_init();

define('APPNAME', 'simulator');
define('RANGE_SIZE', 1000000000); // work_generator.cpp's --range_size
// (MAX_INDEX+1) / RANGE_SIZE, rounded up. MAX_INDEX_STR is defined in
// tools/work_generator/work_generator.cpp; kept as a literal constant
// here rather than computed at runtime since it never changes and PHP's
// native ints can't hold it exactly anyway (it's a 128-bit value).
define('SEARCH_SPACE_BLOCKS', 653534134887);
define('TOTAL_DEAL_SPACE', '653534134886878244999');

// do_query() returns null on a genuine query failure (connection dropped,
// deadlock, syntax error) and a mysqli_result on success -- see
// db_conn.inc's own do_query(). Every query scalar() runs here is a bare
// aggregate (count(*)/sum(...)), which always returns exactly one row even
// when nothing matches, so a real "no rows" case never reaches the
// $row ? ... : 0 fallback below in practice; that branch only exists as a
// defensive fallback. The one case that matters is failure: without
// $query_failed, a transient DB hiccup would silently produce the same "0"
// a genuinely-empty aggregate does, and the script would go on to publish a
// fresh-looking (generated_at updates normally) but wrong, zeroed stats
// file over whatever good data was already there.
$query_failed = false;
function scalar($db, $sql) {
    global $query_failed;
    $result = $db->do_query($sql);
    if (!$result) {
        $query_failed = true;
        return 0;
    }
    $row = $result->fetch_row();
    $result->free();
    return $row ? $row[0] : 0;
}

$db = BoincDb::get();

// Everything that touches the DB is wrapped in one try/catch: confirmed
// live (2026-08-31, against a real closed/dropped connection) that on this
// project's actual PHP 8.2 / mysqli setup, a failing query throws an
// uncaught mysqli_sql_exception (or a plain Error, for a fully-dead
// connection) rather than making $db_conn->query() return false the way
// db_conn.inc's own do_query() fallback expects -- no mysqli_report() call
// anywhere in this project's inc/ overrides PHP 8.1+'s throw-on-error
// default. scalar()'s own null check below is kept as defense in depth
// (it's the documented do_query() contract, and would matter if that
// default ever changed), but the catch here is what actually protects
// against the failure mode this script hits in practice: without it, any
// transient DB hiccup mid-run crashed with a raw stack trace to stderr
// AND a nonzero exit -- which incidentally already skipped the publish
// step (it never got that far), but with none of the other queries this
// run needed getting a chance to run either, and no clean single-line
// record of what happened.
try {

$app = BoincApp::lookup("name='" . APPNAME . "'");
$appid = $app ? (int)$app->id : 0;

// -------- lifetime totals: deals & blocks confirmed ever --------
// Using camicia_completed_ranges guarantees permanent, unpurged totals
// that survive BOINC's 7-day db_purge, matching the Deal Registry.
$blocks_confirmed_total = (int)scalar($db, "SELECT count(*) FROM camicia_completed_ranges");
$deals_confirmed_total = (float)scalar($db, "SELECT coalesce(sum(range_end - range_start + 1), 0) FROM camicia_completed_ranges");

// -------- per-period stats --------
$periods = [
    'day'   => 86400,
    'week'  => 7 * 86400,
    'month' => 30 * 86400,
    'year'  => 365 * 86400,
];
$now = time();
$period_stats = [];
foreach ($periods as $key => $seconds) {
    $cutoff = $now - $seconds;
    $volunteers = (int)scalar($db, "
        select count(distinct userid) from result
        where appid = $appid and received_time > $cutoff
    ");
    $confirmed = (int)scalar($db, "
        select count(*) from camicia_completed_ranges
        where assimilated_at > $cutoff
    ");
    $deals_confirmed = (float)scalar($db, "
        select coalesce(sum(range_end - range_start + 1), 0) from camicia_completed_ranges
        where assimilated_at > $cutoff
    ");
    // outcome = 1 (CLIENT_RESULT_SUCCESS): only count CPU time from
    // results that actually completed successfully.
    $cpu_hours = (float)scalar($db, "
        select coalesce(sum(cpu_time), 0) from result
        where appid = $appid and received_time > $cutoff and outcome = 1
    ") / 3600.0;
    $period_stats[$key] = [
        'volunteers' => $volunteers,
        'blocks_confirmed' => $confirmed,
        'deals_confirmed' => $deals_confirmed,
        'cpu_hours' => round($cpu_hours, 1),
    ];
}

// -------- discoveries & hall of fame milestones --------
$longest_current = null;
$longest_history = [];
$loops_found = [];

// Current champion longest game (highest cards, then most recent)
$res = $db->do_query("
    SELECT cards, tricks, deal_index, wu_name, discovered_at, userid, hostid, is_world_record
    FROM camicia_discoveries
    WHERE discovery_type = 'longest'
    ORDER BY cards DESC, discovered_at DESC
    LIMIT 1
");
if ($res && ($row = $res->fetch_assoc())) {
    $uid = (int)$row['userid'];
    $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
    $longest_current = [
        'cards' => (int)$row['cards'],
        'tricks' => (int)$row['tricks'],
        'deal_index' => $row['deal_index'],
        'wu_name' => $row['wu_name'],
        'found_at' => (int)$row['discovered_at'],
        'userid' => $uid,
        'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
        'is_world_record' => (bool)$row['is_world_record'],
    ];
    $res->free();
}

// All-time record progression / Hall of Fame milestones from Day 1
$res = $db->do_query("
    SELECT cards, tricks, deal_index, wu_name, discovered_at, userid, hostid, is_world_record
    FROM camicia_discoveries
    WHERE discovery_type = 'longest'
    ORDER BY cards DESC, discovered_at DESC
");
if ($res) {
    while ($row = $res->fetch_assoc()) {
        $uid = (int)$row['userid'];
        $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
        $longest_history[] = [
            'cards' => (int)$row['cards'],
            'tricks' => (int)$row['tricks'],
            'deal_index' => $row['deal_index'],
            'wu_name' => $row['wu_name'],
            'found_at' => (int)$row['discovered_at'],
            'userid' => $uid,
            'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
            'is_world_record' => (bool)$row['is_world_record'],
        ];
    }
    $res->free();
}

// Loops found: newest first
$res = $db->do_query("
    SELECT cards, tricks, deal_index, wu_name, discovered_at, userid, hostid
    FROM camicia_discoveries
    WHERE discovery_type = 'loop'
    ORDER BY discovered_at DESC
");
if ($res) {
    while ($row = $res->fetch_assoc()) {
        $uid = (int)$row['userid'];
        $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
        $loops_found[] = [
            'cards' => (int)$row['cards'],
            'tricks' => (int)$row['tricks'],
            'deal_index' => $row['deal_index'],
            'wu_name' => $row['wu_name'],
            'found_at' => (int)$row['discovered_at'],
            'userid' => $uid,
            'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
        ];
    }
    $res->free();
}

// -------- top explorers (cached for deals.php leaderboard) --------
$top_explorers = [];
$res = $db->do_query("
    SELECT 
        user_id, 
        COUNT(*) as total_ranges,
        COALESCE(SUM(range_end - range_start + 1), 0) as total_deals,
        COALESCE(MAX(max_cards), 0) as top_cards,
        COALESCE(SUM(loops_count), 0) as total_loops,
        MAX(assimilated_at) as last_seen
    FROM camicia_completed_ranges
    GROUP BY user_id
    ORDER BY total_ranges DESC
    LIMIT 25
");
if ($res) {
    while ($row = $res->fetch_assoc()) {
        $uid = (int)$row['user_id'];
        $u = BoincUser::lookup_id($uid);
        $name = $u ? trim($u->name) : '';
        $is_valid = ($u && !empty($name) && strpos($name, '[deleted]') === false);
        $user_name = $u ? $u->name : "User #" . $uid;
        $user_html = $is_valid ? user_links($u, BADGE_HEIGHT_SMALL) : ("User #" . $uid);
        $top_explorers[] = [
            'user_id' => $uid,
            'user_name' => $user_name,
            'user_html' => $user_html,
            'is_valid' => $is_valid,
            'total_ranges' => (int)$row['total_ranges'],
            'total_deals' => (string)$row['total_deals'],
            'top_cards' => (int)$row['top_cards'],
            'total_loops' => (int)$row['total_loops'],
            'last_seen' => (int)$row['last_seen'],
        ];
    }
    $res->free();
}

$stats = [
    'generated_at' => $now,
    'search_space_blocks' => SEARCH_SPACE_BLOCKS,
    'total_deal_space' => TOTAL_DEAL_SPACE,
    'deals_confirmed_total' => $deals_confirmed_total,
    'blocks_confirmed_total' => $blocks_confirmed_total,
    'periods' => $period_stats,
    'longest_current' => $longest_current,
    'longest_history' => $longest_history,
    'loops' => $loops_found,
    'top_explorers' => $top_explorers,
];

} catch (Throwable $e) {
    fwrite(STDERR, date(DATE_RFC822) . ": ERROR: " . $e->getMessage() . " -- leaving progress_stats.json unchanged\n");
    exit(1);
}

// Skip the publish entirely on a failed query rather than overwrite a good
// existing progress_stats.json with one full of zeros -- a transient DB
// hiccup during one of this task's 15-minute cycles should leave the last
// known-good file in place for visitors, not silently replace it with a
// fresh-looking (generated_at still updates) but wrong snapshot. The next
// cycle 15 minutes later self-heals once the DB is reachable again. (In
// practice, on this project's real PHP/mysqli setup, this branch is the
// defensive fallback -- the catch above is what actually fires today, see
// its own comment.)
if ($query_failed) {
    fwrite(STDERR, date(DATE_RFC822) . ": ERROR: one or more queries failed, leaving progress_stats.json unchanged\n");
    exit(1);
}

$final_path = "../user/progress_stats.json";
$tmp_path = "$final_path.tmp";
file_put_contents($tmp_path, json_encode($stats, JSON_PRETTY_PRINT));
rename($tmp_path, $final_path);

echo date(DATE_RFC822), ": wrote progress_stats.json ($deals_confirmed_total deals, $blocks_confirmed_total blocks confirmed total)\n";

?>
