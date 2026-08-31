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
];

// Skip the publish entirely on a failed query rather than overwrite a good
// existing progress_stats.json with one full of zeros -- a transient DB
// hiccup during one of this task's 15-minute cycles should leave the last
// known-good file in place for visitors, not silently replace it with a
// fresh-looking (generated_at still updates) but wrong snapshot. The next
// cycle 15 minutes later self-heals once the DB is reachable again.
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
