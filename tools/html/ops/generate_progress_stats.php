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
require_once("../inc/badge.inc");

db_init();

define('APPNAME', 'simulator');
define('RANGE_SIZE', 1000000000); // work_generator.cpp's --range_size
// (MAX_INDEX+1) / RANGE_SIZE, rounded up. MAX_INDEX_STR is defined in
// tools/work_generator/work_generator.cpp; kept as a literal constant
// here rather than computed at runtime since it never changes and PHP's
// native ints can't hold it exactly anyway (it's a 128-bit value).
define('SEARCH_SPACE_BLOCKS', 653534134887);

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

// -------- lifetime total: blocks (workunits) confirmed ever --------
// A block is "confirmed" once it has a canonical result, i.e. two
// independent volunteers' output matched byte-for-byte
// (sample_bitwise_validator). Joining to that specific result's
// received_time (rather than workunit.mod_time, which updates on any
// change) is what makes the per-period version below actually mean
// "confirmed in this window", not just "touched in this window".
$blocks_confirmed_total = (int)scalar($db, "
    select count(*) from workunit w
    join result r on w.canonical_resultid = r.id
    where w.appid = $appid and w.canonical_resultid != 0
");

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
        select count(*) from workunit w
        join result r on w.canonical_resultid = r.id
        where w.appid = $appid and w.canonical_resultid != 0 and r.received_time > $cutoff
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
        'cpu_hours' => round($cpu_hours, 1),
    ];
}

// -------- today's pace: where blocks touched in the last 24h currently stand --------
// "Rechecking": still unconfirmed, but already has more than the normal 2
// results -- BOINC's own stock redundancy handling already generates extra
// replicas automatically when the first pair doesn't match (nothing to do
// with adaptive replication, which this project deliberately does not use
// -- see that discussion). "Waiting": still unconfirmed, at or under 2
// results, i.e. genuinely just waiting on its second independent result.
$day_ago = $now - 86400;
$rechecking = (int)scalar($db, "
    select count(*) from workunit w
    where w.appid = $appid and w.canonical_resultid = 0
    and w.mod_time > $day_ago
    and (select count(*) from result r where r.workunitid = w.id) > 2
");
$waiting = (int)scalar($db, "
    select count(*) from workunit w
    where w.appid = $appid and w.canonical_resultid = 0
    and w.mod_time > $day_ago
    and (select count(*) from result r where r.workunitid = w.id) <= 2
");

// -------- discoveries & hall of fame milestones --------
$longest_current = null;
$longest_history = [];
$loops_found = [];

$has_disc_table = false;
$res = $db->do_query("SHOW TABLES LIKE 'camicia_discoveries'");
if ($res) {
    if ($res->num_rows > 0) {
        $has_disc_table = true;
    }
    $res->free();
}

if ($has_disc_table) {
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
} else {
    // Fallback to flat files
    $longest_path = "../../records_longest.txt";
    if (file_exists($longest_path)) {
        $parts = preg_split('/\s+/', trim(file_get_contents($longest_path)));
        if (count($parts) >= 5) {
            $uid = isset($parts[5]) ? (int)$parts[5] : 0;
            $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
            $cards = (int)$parts[0];
            $longest_current = [
                'cards' => $cards,
                'tricks' => (int)$parts[1],
                'deal_index' => $parts[2],
                'wu_name' => $parts[3],
                'found_at' => (int)$parts[4],
                'userid' => $uid,
                'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
                'is_world_record' => ($cards > 8344),
            ];
        }
    }

    $history_path = "../../records_longest_history.txt";
    if (file_exists($history_path)) {
        foreach (file($history_path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
            $parts = preg_split('/\s+/', trim($line));
            if (count($parts) >= 5) {
                $uid = isset($parts[5]) ? (int)$parts[5] : 0;
                $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
                $cards = (int)$parts[0];
                $longest_history[] = [
                    'cards' => $cards,
                    'tricks' => (int)$parts[1],
                    'deal_index' => $parts[2],
                    'wu_name' => $parts[3],
                    'found_at' => (int)$parts[4],
                    'userid' => $uid,
                    'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
                    'is_world_record' => ($cards > 8344),
                ];
            }
        }
        usort($longest_history, function($a, $b) {
            return $b['cards'] <=> $a['cards'];
        });
    }

    $loops_path = "../../records_loops.txt";
    if (file_exists($loops_path)) {
        foreach (file($loops_path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
            $parts = preg_split('/\s+/', trim($line));
            if (count($parts) >= 3) {
                $uid = isset($parts[3]) ? (int)$parts[3] : 0;
                $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
                $loops_found[] = [
                    'deal_index' => $parts[0],
                    'wu_name' => $parts[1],
                    'found_at' => (int)$parts[2],
                    'userid' => $uid,
                    'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
                ];
            }
        }
        $loops_found = array_reverse($loops_found);
    }
}

$stats = [
    'generated_at' => $now,
    'search_space_blocks' => SEARCH_SPACE_BLOCKS,
    'blocks_confirmed_total' => $blocks_confirmed_total,
    'periods' => $period_stats,
    'today_pace' => [
        'confirmed' => $period_stats['day']['blocks_confirmed'],
        'rechecking' => $rechecking,
        'waiting' => $waiting,
    ],
    'longest_current' => $longest_current,
    'longest_history' => $longest_history,
    'loops' => $loops_found,
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

echo date(DATE_RFC822), ": wrote progress_stats.json ($blocks_confirmed_total blocks confirmed total)\n";

?>
