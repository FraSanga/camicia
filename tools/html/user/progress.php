<?php
// Public progress page for the search -- how much of the deal space has
// been explored/confirmed, recent volunteer activity, and the project's
// two headline findings (longest game, any loop found).
//
// Deliberately reads pre-computed data only, never queries the DB live:
// - progress_stats.json (this directory) is written every 15 min by
//   html/ops/generate_progress_stats.php (a <task> in tools/config.xml) --
//   aggregate queries over a growing table don't need to run once per
//   visitor, and nobody notices a few minutes of staleness here.
// - records_longest.txt / records_loops.txt (project root) are updated
//   incrementally by tools/assimilator/assimilator.cpp the instant each
//   relevant result is assimilated -- effectively immediate, no batching
//   delay, and never require rescanning the ever-growing results.txt.
// See those two files' own comments for the write side of this.
//
// Scope note: the client-side game-replay demo's status strings (below,
// in the <script>) are left as plain English rather than routed through
// tra() -- they're decorative "what's happening in the demo" text, not
// core page content. Worth revisiting if full translation is wanted later.

require_once('../inc/util.inc');
require_once('../inc/translation.inc');

check_get_args(array());

// (MAX_INDEX+1) / RANGE_SIZE, rounded up -- see
// tools/work_generator/work_generator.cpp's MAX_INDEX_STR. Used only as a
// fallback before generate_progress_stats.php has ever run.
define('FALLBACK_SEARCH_SPACE_BLOCKS', 653534134887);

function read_progress_stats() {
    $path = 'progress_stats.json';
    if (!file_exists($path)) return null;
    $json = json_decode(file_get_contents($path), true);
    return $json ?: null;
}

// records_longest.txt: one line "<cards> <tricks> <deal_index> <wu_name> <unix_time> [<userid> <hostid>]"
function read_longest_record() {
    $path = '../../records_longest.txt';
    if (!file_exists($path)) return null;
    $parts = explode(' ', trim(file_get_contents($path)));
    if (count($parts) < 5) return null;
    $uid = isset($parts[5]) ? (int)$parts[5] : 0;
    $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
    $cards = (int)$parts[0];
    return array(
        'cards' => $cards,
        'tricks' => (int)$parts[1],
        'deal_index' => $parts[2],
        'wu_name' => $parts[3],
        'found_at' => (int)$parts[4],
        'userid' => $uid,
        'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
        'is_world_record' => ($cards > 8344),
    );
}

// records_longest_history.txt: append-only, every new record from Day 1
function read_longest_history() {
    $path = '../../records_longest_history.txt';
    if (!file_exists($path)) return array();
    $history = array();
    foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $parts = explode(' ', trim($line));
        if (count($parts) < 5) continue;
        $uid = isset($parts[5]) ? (int)$parts[5] : 0;
        $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
        $cards = (int)$parts[0];
        $history[] = array(
            'cards' => $cards,
            'tricks' => (int)$parts[1],
            'deal_index' => $parts[2],
            'wu_name' => $parts[3],
            'found_at' => (int)$parts[4],
            'userid' => $uid,
            'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
            'is_world_record' => ($cards > 8344),
        );
    }
    usort($history, function($a, $b) {
        return $b['cards'] <=> $a['cards'];
    });
    return $history;
}

// records_loops.txt: append-only, one line per loop "<deal_index> <wu_name> <unix_time> [<userid> <hostid>]"
function read_loops_found() {
    $path = '../../records_loops.txt';
    if (!file_exists($path)) return array();
    $loops = array();
    foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $parts = explode(' ', trim($line));
        if (count($parts) < 3) continue;
        $uid = isset($parts[3]) ? (int)$parts[3] : 0;
        $user = $uid > 0 ? BoincUser::lookup_id($uid) : null;
        $loops[] = array(
            'deal_index' => $parts[0],
            'wu_name' => $parts[1],
            'found_at' => (int)$parts[2],
            'userid' => $uid,
            'user_html' => $user ? user_links($user, BADGE_HEIGHT_SMALL) : "",
        );
    }
    return array_reverse($loops); // newest first
}

$stats = read_progress_stats();
$longest = ($stats && !empty($stats['longest_current'])) ? $stats['longest_current'] : read_longest_record();
$longest_history = ($stats && !empty($stats['longest_history'])) ? $stats['longest_history'] : read_longest_history();
$loops_found = ($stats && !empty($stats['loops'])) ? $stats['loops'] : read_loops_found();

if (!$longest && !empty($longest_history)) {
    $longest = $longest_history[0];
}

if ($longest && empty($longest['user_html']) && !empty($longest['userid'])) {
    $u = BoincUser::lookup_id($longest['userid']);
    if ($u) $longest['user_html'] = user_links($u, BADGE_HEIGHT_SMALL);
}
foreach ($longest_history as &$rec) {
    if (empty($rec['user_html']) && !empty($rec['userid'])) {
        $u = BoincUser::lookup_id($rec['userid']);
        if ($u) $rec['user_html'] = user_links($u, BADGE_HEIGHT_SMALL);
    }
}
unset($rec);
foreach ($loops_found as &$loop) {
    if (empty($loop['user_html']) && !empty($loop['userid'])) {
        $u = BoincUser::lookup_id($loop['userid']);
        if ($u) $loop['user_html'] = user_links($u, BADGE_HEIGHT_SMALL);
    }
}
unset($loop);

define('TOTAL_DEAL_SPACE_NUM', 6.53534134886878245e20);

$blocks_confirmed_total = $stats ? (int)($stats['blocks_confirmed_total'] ?? 0) : 0;
$deals_confirmed_total = $stats ? (float)($stats['deals_confirmed_total'] ?? ($blocks_confirmed_total * 1000000000)) : 0;

$pct = $deals_confirmed_total > 0 ? ($deals_confirmed_total / TOTAL_DEAL_SPACE_NUM) * 100 : 0;
if ($pct > 0 && $pct < 0.00000000000001) {
    $pct_display = "&lt;0.00000000000001";
} elseif ($pct > 0 && $pct < 0.001) {
    $pct_display = sprintf("%.14f", $pct);
    $pct_display = rtrim(rtrim($pct_display, '0'), '.');
    if ($pct_display === '' || $pct_display === '0') $pct_display = "&lt;0.00000000000001";
} else {
    $pct_display = number_format($pct, 3);
}
// A meaningful minimum width so the fill is visible at all at these scales
// (the real percentage is astronomically close to 0 for a long time).
$meter_pct = max($pct, 0.05);

$period_json = $stats ? json_encode($stats['periods']) : json_encode(array(
    'day' => array('volunteers' => 0, 'blocks_confirmed' => 0, 'cpu_hours' => 0),
    'week' => array('volunteers' => 0, 'blocks_confirmed' => 0, 'cpu_hours' => 0),
    'month' => array('volunteers' => 0, 'blocks_confirmed' => 0, 'cpu_hours' => 0),
    'year' => array('volunteers' => 0, 'blocks_confirmed' => 0, 'cpu_hours' => 0),
));

page_head(tra("Search progress"));
?>
<style>
.progress-page {
    --felt: #0f1f1a; --felt-2: #16302a; --felt-line: #1d3a32;
    --gold: #c9a227; --gold-dim: #8c7a3e; --gold-pale: #4a4326;
    --cream: #ede3cb; --cream-dim: #b7ae95; --ruby: #a83349;
    --ink-on-cream: #16302a;
    background: var(--felt); color: var(--cream);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    -webkit-font-smoothing: antialiased;
    border-radius: 12px; padding: 40px; margin: 16px 0;
    background-image:
        repeating-linear-gradient(45deg, var(--felt-line) 0, var(--felt-line) 1px, transparent 1px, transparent 48px),
        repeating-linear-gradient(-45deg, var(--felt-line) 0, var(--felt-line) 1px, transparent 1px, transparent 48px);
}
.progress-page * { box-sizing: border-box; }
.progress-page .eyebrow { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--gold); font-weight: 700; margin: 0 0 12px; }
.progress-page h1 { font-family: Georgia, 'Iowan Old Style', 'Palatino Linotype', serif; font-size: 34px; line-height: 1.2; margin: 0 0 16px; color: var(--cream); }
.progress-page .lede { font-size: 15.5px; line-height: 1.65; color: var(--cream-dim); max-width: 68ch; margin: 0; }
.progress-page h2.section-title { font-family: Georgia, serif; font-size: 19px; margin: 0 0 4px; color: var(--cream); }
.progress-page p.section-sub { font-size: 13.5px; color: var(--cream-dim); margin: 0 0 20px; line-height: 1.55; }
.progress-page .hero { border: 1px solid var(--gold-dim); border-radius: 16px; background: linear-gradient(180deg, var(--felt-2), var(--felt)); padding: 40px 40px 36px; margin: 40px 0; text-align: center; }
.progress-page .hero-label { font-size: 13px; letter-spacing: .08em; text-transform: uppercase; color: var(--cream-dim); margin: 0 0 10px; font-weight: 600; }
.progress-page .hero-number { font-family: Georgia, serif; font-size: clamp(24px, 4.2vw, 68px); line-height: 1.15; color: var(--gold); margin: 0; font-variant-numeric: tabular-nums; white-space: nowrap; }
.progress-page .hero-sub { font-size: 14px; color: var(--cream-dim); margin: 12px 0 26px; }
.progress-page .meter { height: 14px; border-radius: 999px; background: var(--felt); border: 1px solid var(--felt-line); overflow: hidden; max-width: 640px; margin: 0 auto; }
.progress-page .meter-fill { height: 100%; background: linear-gradient(90deg, var(--gold-dim), var(--gold)); border-radius: 999px 0 0 999px; }
.progress-page .meter-caption { font-size: 12.5px; color: var(--cream-dim); margin-top: 10px; font-variant-numeric: tabular-nums; }
.progress-page .period-block { margin-bottom: 40px; }
.progress-page .period-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 18px; }
.progress-page .period-toggle { display: inline-flex; background: var(--felt-2); border: 1px solid var(--felt-line); border-radius: 999px; padding: 3px; gap: 2px; }
.progress-page .period-toggle button { font: inherit; font-size: 12.5px; font-weight: 600; color: var(--cream-dim); background: transparent; border: none; border-radius: 999px; padding: 7px 16px; cursor: pointer; }
.progress-page .period-toggle button.active { background: var(--gold); color: var(--felt); }
.progress-page .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 2px; background: var(--felt-line); border: 1px solid var(--felt-line); border-radius: 14px; overflow: hidden; }
.progress-page .stat-tile { background: var(--felt-2); padding: 22px 20px; }
.progress-page .stat-value { font-family: Georgia, serif; font-size: 32px; color: var(--gold); font-variant-numeric: tabular-nums; margin: 0 0 6px; }
.progress-page .stat-label { font-size: 12.5px; color: var(--cream-dim); }

.progress-page .discovery { border: 1px solid var(--gold-dim); border-radius: 14px; padding: 28px; margin-bottom: 24px; }
.progress-page .discovery-meta { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12.5px; color: var(--cream-dim); margin: 14px 0 20px; }
.progress-page .discovery-meta b { color: var(--cream); }
.progress-page .move-count { font-family: Georgia, serif; font-size: 26px; color: var(--gold); font-variant-numeric: tabular-nums; }
.progress-page .chip { width: 28px; height: 38px; border-radius: 4px; display: flex; align-items: center; justify-content: center; }
.progress-page .chip.honor { background: var(--cream); border: 1.5px solid var(--gold); color: var(--ink-on-cream); font-family: Georgia, serif; font-size: 14px; font-weight: 700; }
.progress-page .chip.plain { background: var(--felt-line); border: 1px solid var(--felt-line); opacity: .55; }
.progress-page .deal-static { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.progress-page .deal-row { display: flex; flex-wrap: wrap; gap: 3px; }
.progress-page .deal-row-label { font-size: 11px; color: var(--cream-dim); width: 100%; margin-bottom: 3px; letter-spacing: .04em; text-transform: uppercase; }
.progress-page .game-table { display: flex; justify-content: center; align-items: flex-start; gap: 20px; flex-wrap: wrap; margin-top: 10px; }
.progress-page .zone-col { text-align: center; }
.progress-page .zone-label { font-size: 11px; color: var(--cream-dim); letter-spacing: .04em; text-transform: uppercase; margin-bottom: 6px; }
.progress-page .zone-count { font-family: Georgia, serif; font-size: 20px; color: var(--cream); margin-bottom: 8px; font-variant-numeric: tabular-nums; }
.progress-page .zone-col.active .zone-count { color: var(--gold); }
.progress-page .card-slots { display: grid; grid-template-columns: repeat(8, 18px); grid-auto-rows: 24px; gap: 2px; justify-content: center; padding: 6px; border-radius: 8px; }
.progress-page .zone-col.pile .card-slots { border: 1.5px dashed var(--gold-dim); }
.progress-page .slot { width: 18px; height: 24px; border-radius: 3px; display: flex; align-items: center; justify-content: center; font-family: Georgia, serif; font-weight: 700; font-size: 10px; }
.progress-page .slot.honor { background: var(--cream); border: 1.5px solid var(--gold); color: var(--ink-on-cream); }
.progress-page .slot.plain { background: var(--felt-line); border: 1px solid var(--felt-line); opacity: .55; }
.progress-page .slot.empty { background: transparent; border: 1px dashed rgba(237,227,203,.14); }
.progress-page .status-line { text-align: center; font-size: 13.5px; color: var(--cream-dim); margin: 16px 0 4px; min-height: 20px; }
.progress-page .status-line b { color: var(--gold); }
.progress-page .game-controls { display: flex; justify-content: center; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.progress-page .ctrl-btn { font: inherit; font-size: 13px; font-weight: 600; background: var(--felt-2); color: var(--cream); border: 1px solid var(--gold-dim); border-radius: 999px; padding: 8px 16px; cursor: pointer; }
.progress-page .ctrl-btn:disabled { opacity: .4; }
.progress-page .ctrl-btn.play-btn { background: var(--gold); color: var(--felt); border-color: var(--gold); padding: 8px 22px; }
.progress-page .legend-note { font-size: 12px; color: var(--cream-dim); margin-top: 18px; line-height: 1.6; }
.progress-page .legend-note .chip { display: inline-flex; width: 18px; height: 24px; font-size: 10px; vertical-align: middle; margin: 0 4px; }
.progress-page .discovery-tag { display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; padding: 3px 9px; border-radius: 999px; margin-bottom: 10px; }
.progress-page .discovery-tag.camicia { background: var(--gold); color: var(--felt); }
.progress-page .discovery-tag.world-record { background: var(--ruby); color: var(--cream); border: 1px solid #d94b63; }
.progress-page .discovery-tag.reference { background: transparent; border: 1px solid var(--cream-dim); color: var(--cream-dim); }
.progress-page .source-note { font-size: 12px; color: var(--cream-dim); margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--felt-line); line-height: 1.6; }
.progress-page .source-note a { color: var(--gold); }
.progress-page .empty-state { background: var(--felt-2); border-radius: 10px; padding: 20px 22px; text-align: center; color: var(--cream-dim); font-size: 13.5px; line-height: 1.6; margin-top: 16px; }
.progress-page .empty-state b { color: var(--cream); }
.progress-page .hall-of-fame-table { width: 100%; border-collapse: separate; border-spacing: 0; margin-top: 10px; font-size: 13px; background: var(--felt-2); border-radius: 10px; overflow: hidden; border: 1px solid var(--felt-line); }
.progress-page .hall-of-fame-table th { background: var(--felt); color: var(--gold); padding: 12px 14px; text-align: left; font-weight: 600; font-size: 11px; letter-spacing: .05em; text-transform: uppercase; border-bottom: 1px solid var(--felt-line); }
.progress-page .hall-of-fame-table td { padding: 10px 14px; border-bottom: 1px solid var(--felt-line); color: var(--cream); vertical-align: middle; }
.progress-page .hall-of-fame-table tr:last-child td { border-bottom: none; }
.progress-page .hall-of-fame-table tr:hover td { background: rgba(201, 162, 39, 0.05); }
.progress-page .hall-of-fame-table td a, .progress-page .loop-row a { color: var(--gold); text-decoration: none; font-weight: 600; }
.progress-page .hall-of-fame-table td a:hover, .progress-page .loop-row a:hover { text-decoration: underline; }
.progress-page .deal-id-cell { font-family: monospace; font-size: 12px; word-break: break-all; }
.progress-page .table-pagination { display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 14px; font-size: 13px; color: var(--cream-dim); }
.progress-page .table-pagination .ctrl-btn { padding: 5px 14px; font-size: 12px; }
.progress-page .loop-list { display: flex; flex-direction: column; gap: 2px; background: var(--felt-line); border-radius: 10px; overflow: hidden; border: 1px solid var(--felt-line); margin-top: 10px; }
.progress-page .loop-row { background: var(--felt-2); padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap; font-size: 13px; }
.progress-page .loop-row b { color: var(--gold); font-family: Georgia, serif; }
@media (max-width: 640px) {
    .progress-page { padding: 24px 16px; }
    .progress-page .hero { padding: 28px 16px 24px; margin: 24px 0; }
    .progress-page .hero-number { font-size: clamp(16px, 5.5vw, 32px); }
}
</style>

<div class="progress-page">
  <p class="eyebrow">Camicia &middot; <?php echo tra("search progress"); ?></p>
  <h1><?php echo tra("How much we've explored, so far"); ?></h1>
  <p class="lede">
    <?php echo tra("The search space is divided into blocks of a billion deals each. Two volunteers compute the same block independently, and the block is only confirmed once their results match exactly."); ?>
  </p>

  <section class="hero">
    <p class="hero-label"><?php echo tra("Search space explored and confirmed"); ?></p>
    <p class="hero-number"><?php echo $pct_display; ?>%</p>
    <p class="hero-sub"><?php echo tra("6.535 &times; 10^20 total deals in the search space"); ?></p>
    <div class="meter"><div class="meter-fill" style="width:<?php echo $meter_pct; ?>%"></div></div>
    <p class="meter-caption"><?php echo tra("%1 deals simulated and verified across %2 blocks", number_format($deals_confirmed_total), number_format($blocks_confirmed_total)); ?></p>
    <p style="margin:16px 0 0;font-size:13.5px">
      <a href="deals.php?all=1" style="color:var(--gold);text-decoration:none;font-weight:600">
        <?php echo tra("Explore all verified ranges in the Deal Registry &rarr;"); ?>
      </a>
    </p>
  </section>

  <section class="period-block">
    <div class="period-head">
      <div>
        <h2 class="section-title"><?php echo tra("Project activity"); ?></h2>
        <p class="section-sub" style="margin-bottom:0"><?php echo tra("Change the period to see volunteers' contribution"); ?></p>
      </div>
      <div class="period-toggle" id="periodToggle">
        <button data-period="day"><?php echo tra("Today"); ?></button>
        <button data-period="week" class="active"><?php echo tra("7 days"); ?></button>
        <button data-period="month"><?php echo tra("30 days"); ?></button>
        <button data-period="year"><?php echo tra("Year"); ?></button>
      </div>
    </div>
    <div class="stat-grid">
      <div class="stat-tile"><p class="stat-value" id="statVolunteers">&mdash;</p><p class="stat-label"><?php echo tra("volunteers active in this period"); ?></p></div>
      <div class="stat-tile"><p class="stat-value" id="statValidated">&mdash;</p><p class="stat-label"><?php echo tra("blocks confirmed in this period"); ?></p></div>
      <div class="stat-tile"><p class="stat-value" id="statCpu">&mdash;</p><p class="stat-label"><?php echo tra("CPU hours donated in this period"); ?></p></div>
    </div>
  </section>



  <section>
    <h2 class="section-title" style="margin-bottom:16px"><?php echo tra("Discoveries"); ?></h2>

    <div class="discovery" id="vizCard">
<?php if ($longest): ?>
      <div id="vizTagContainer">
        <?php if (!empty($longest['is_world_record'])): ?>
        <span class="discovery-tag world-record"><?php echo tra("World record breakthrough (>8,344 cards)"); ?></span>
        <?php else: ?>
        <span class="discovery-tag camicia"><?php echo tra("Found by Camicia"); ?></span>
        <?php endif; ?>
      </div>
      <p class="section-sub" id="vizSubtitle" style="margin-bottom:2px"><?php echo tra("The longest game found so far"); ?></p>
      <p class="move-count" id="vizMoveCount">
        <?php echo tra("%1 cards played", number_format($longest['cards'])); ?>
        <span style="font-size:18px;color:var(--cream-dim)">&middot; <?php echo tra("%1 tricks", number_format($longest['tricks'])); ?></span>
      </p>
      <div class="discovery-meta" id="vizMeta">
        <span id="vizMetaDate"><?php echo tra("Confirmed on %1", date('d/m/Y', $longest['found_at'])); ?></span>
        <span id="vizMetaDeal" style="word-break:break-all"><?php echo tra("Deal #%1", $longest['deal_index']); ?></span>
        <span id="vizMetaAuthor" <?php if (empty($longest['user_html'])) echo 'style="display:none"'; ?>><?php echo tra("Discovered by %1", $longest['user_html'] ?? ''); ?></span>
      </div>
<?php else: ?>
      <p class="section-sub" style="margin-bottom:2px"><?php echo tra("The longest game found so far"); ?></p>
      <div class="empty-state"><?php echo tra("Camicia hasn't confirmed any games yet -- check back once the search is running."); ?></div>
<?php endif; ?>
      <p class="section-sub" id="vizPlaybackDesc" style="margin:16px 0 4px">
        <?php if ($longest): ?>
        <?php echo tra("Below, %1the actual longest game found%2, played back move by move.", "<em>", "</em>"); ?>
        <?php else: ?>
        <?php echo tra("No game to play back yet -- check back once the search has confirmed one."); ?>
        <?php endif; ?>
      </p>
<?php if ($longest): ?>
      <div class="game-table" id="gameTable"></div>
      <div class="status-line" id="gameStatus"><?php echo tra("Ready. Press Play to watch it, or step through it one move at a time."); ?></div>
      <div class="game-controls">
        <button class="ctrl-btn" id="resetBtn" title="<?php echo tra("Back to the start"); ?>">&#9198; <?php echo tra("Reset"); ?></button>
        <button class="ctrl-btn" id="backBtn" title="<?php echo tra("One step back"); ?>">&#9664; <?php echo tra("Back"); ?></button>
        <button class="ctrl-btn play-btn" id="playBtn" title="<?php echo tra("Play / Pause"); ?>">&#9654; <?php echo tra("Play"); ?></button>
        <button class="ctrl-btn" id="fwdBtn" title="<?php echo tra("One step forward"); ?>"><?php echo tra("Forward"); ?> &#9654;</button>
      </div>
      <p class="legend-note">
        <?php echo tra("Only aces, kings, queens and jacks determine the outcome of the game -- suit never matters either, only rank."); ?>
        <span class="chip honor">K</span> <?php echo tra("affects the result, a generic"); ?> <span class="chip plain"></span> <?php echo tra("doesn't: that's why every other card is shown the same."); ?>
      </p>
<?php endif; ?>
    </div>

    <div class="discovery">
      <h2 class="section-title" style="margin-bottom:2px"><?php echo tra("Hall of fame"); ?></h2>
      <p class="section-sub" style="margin-bottom:16px"><?php echo tra("The history of every record set on Camicia from day 1"); ?></p>
<?php if (!empty($longest_history)): ?>
      <div style="overflow-x:auto">
        <table class="hall-of-fame-table" id="hofTable">
          <thead>
            <tr>
              <th><?php echo tra("Confirmed on"); ?></th>
              <th><?php echo tra("Cards"); ?></th>
              <th><?php echo tra("Tricks"); ?></th>
              <th><?php echo tra("Deal #"); ?></th>
              <th><?php echo tra("Discovered by"); ?></th>
              <th style="text-align:right"></th>
            </tr>
          </thead>
          <tbody>
<?php foreach ($longest_history as $rec): ?>
            <tr>
              <td><?php echo date('d/m/Y', $rec['found_at']); ?></td>
              <td><b><?php echo number_format($rec['cards']); ?></b></td>
              <td><?php echo number_format($rec['tricks']); ?></td>
              <td><span class="deal-id-cell"><?php echo htmlspecialchars($rec['deal_index']); ?></span></td>
              <td><?php echo !empty($rec['user_html']) ? $rec['user_html'] : tra("Anonymous"); ?></td>
              <td style="text-align:right">
                <button class="ctrl-btn replay-deal-btn"
                  data-deal="<?php echo htmlspecialchars($rec['deal_index']); ?>"
                  data-type="longest"
                  data-cards="<?php echo (int)$rec['cards']; ?>"
                  data-tricks="<?php echo (int)$rec['tricks']; ?>"
                  data-date="<?php echo date('d/m/Y', $rec['found_at']); ?>"
                  data-author="<?php echo htmlspecialchars($rec['user_html'] ?? ''); ?>"
                  data-wr="<?php echo !empty($rec['is_world_record']) ? '1' : '0'; ?>"
                  title="<?php echo tra("Load into visualizer"); ?>"
                  style="padding:4px 12px;font-size:11.5px">&#9654; <?php echo tra("Replay"); ?></button>
              </td>
            </tr>
<?php endforeach; ?>
          </tbody>
        </table>
      </div>
      <div id="hofPagination" class="table-pagination" style="display:none">
        <button id="hofPrevBtn" class="ctrl-btn">&larr; <?php echo tra("Previous"); ?></button>
        <span id="hofPageInfo"></span>
        <button id="hofNextBtn" class="ctrl-btn"><?php echo tra("Next"); ?> &rarr;</button>
      </div>
<?php else: ?>
      <div class="empty-state"><?php echo tra("No historical records recorded yet -- check back once the search is running."); ?></div>
<?php endif; ?>
    </div>

    <div class="discovery">
      <span class="discovery-tag reference"><?php echo tra("Historical reference &mdash; not found by Camicia"); ?></span>
      <p class="section-sub" style="margin-bottom:2px"><?php echo tra("The first documented loop for this game"); ?></p>
      <p class="move-count"><?php echo tra("474 moves, then enters a cycle of 66 deals"); ?></p>
      <div class="discovery-meta">
        <span><?php echo tra("Discovered by %1", "Brayden Casella"); ?></span>
        <span>10/02/2024</span>
      </div>
      <div class="deal-static" id="dealVizCasella"></div>
      <p class="source-note">
        <?php echo tra("Shown as an example of how a loop found by Camicia will appear below -- this specific loop was discovered with a different method, not by this project. Source:"); ?>
        <a href="https://arxiv.org/abs/2403.13855" target="_blank" rel="noopener">Casella et al., "A Non-Terminating Game of Beggar-My-Neighbor", 2024</a>.
      </p>
    </div>

    <div class="discovery">
      <h2 class="section-title" style="margin-bottom:2px"><?php echo tra("Loops found by Camicia"); ?></h2>
      <p class="section-sub" style="margin-bottom:16px"><?php echo tra("Non-terminating loop games discovered by volunteers"); ?></p>
<?php if (!empty($loops_found)): ?>
      <div style="overflow-x:auto">
        <table class="hall-of-fame-table" id="loopsTable">
          <thead>
            <tr>
              <th><?php echo tra("Confirmed on"); ?></th>
              <th><?php echo tra("Deal #"); ?></th>
              <th><?php echo tra("Discovered by"); ?></th>
              <th style="text-align:right"></th>
            </tr>
          </thead>
          <tbody>
<?php foreach ($loops_found as $loop): ?>
            <tr>
              <td><?php echo date('d/m/Y', $loop['found_at']); ?></td>
              <td><span class="deal-id-cell"><?php echo htmlspecialchars($loop['deal_index']); ?></span></td>
              <td><?php echo !empty($loop['user_html']) ? $loop['user_html'] : tra("Anonymous"); ?></td>
              <td style="text-align:right">
                <button class="ctrl-btn replay-deal-btn"
                  data-deal="<?php echo htmlspecialchars($loop['deal_index']); ?>"
                  data-type="loop"
                  data-cards="0"
                  data-tricks="0"
                  data-date="<?php echo date('d/m/Y', $loop['found_at']); ?>"
                  data-author="<?php echo htmlspecialchars($loop['user_html'] ?? ''); ?>"
                  data-wr="0"
                  title="<?php echo tra("Load into visualizer"); ?>"
                  style="padding:4px 12px;font-size:11.5px">&#9654; <?php echo tra("Replay"); ?></button>
              </td>
            </tr>
<?php endforeach; ?>
          </tbody>
        </table>
      </div>
      <div id="loopsPagination" class="table-pagination" style="display:none">
        <button id="loopsPrevBtn" class="ctrl-btn">&larr; <?php echo tra("Previous"); ?></button>
        <span id="loopsPageInfo"></span>
        <button id="loopsNextBtn" class="ctrl-btn"><?php echo tra("Next"); ?> &rarr;</button>
      </div>
<?php else: ?>
      <div class="empty-state"><?php echo tra("Camicia hasn't found a loop of its own yet. %1The search continues%2 -- every block explored shrinks the space still left to check.", "<b>", "</b>"); ?></div>
<?php endif; ?>
    </div>
  </section>
</div>

<script>
(function() {
  var periodData = <?php echo $period_json; ?>;
  var periodToggle = document.getElementById('periodToggle');
  function showPeriod(key) {
    var d = periodData[key] || { volunteers: 0, blocks_confirmed: 0, cpu_hours: 0 };
    document.getElementById('statVolunteers').textContent = d.volunteers.toLocaleString('en-US');
    document.getElementById('statValidated').textContent = d.blocks_confirmed.toLocaleString('en-US');
    document.getElementById('statCpu').textContent = d.cpu_hours.toLocaleString('en-US');
  }
  periodToggle.addEventListener('click', function(e) {
    var btn = e.target.closest('button');
    if (!btn) return;
    periodToggle.querySelectorAll('button').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    showPeriod(btn.dataset.period);
  });
  showPeriod('week');

  // Renders one hand as a static row of chips: honor cards (A/K/Q/J) shown
  // by rank only (suit never affects the outcome), every other card
  // collapsed to an identical plain chip.
  function renderHand(entries) {
    return entries.map(function(e) {
      return e ? '<div class="chip honor">' + e + '</div>' : '<div class="chip plain"></div>';
    }).join('');
  }

  // Casella's actual documented starting hands (arXiv:2403.13855 /
  // richardpmann.com records) -- rank only, matching the paper's own notation.
  var casellaA = [].concat('---K---Q-KQAJ-----AAJ--J--'.split('')).map(function(c) { return c === '-' ? null : c; });
  var casellaB = [].concat('----------Q----KQ-J-----KA'.split('')).map(function(c) { return c === '-' ? null : c; });
  document.getElementById('dealVizCasella').innerHTML =
    '<div class="deal-row-label">' + <?php echo json_encode(tra("Hand A")); ?> + '</div><div class="deal-row">' + renderHand(casellaA) + '</div>' +
    '<div class="deal-row-label" style="margin-top:8px">' + <?php echo json_encode(tra("Hand B")); ?> + '</div><div class="deal-row">' + renderHand(casellaB) + '</div>';

  // ---- Playable animation, driven by the exact same rules as
  // tools/worker/core/engine.cpp's CamiciaGame::simulate().
<?php if ($longest): ?>
  // Reconstructs the actual deal from its index, the same multinomial-
  // ranking algorithm as tools/worker/core/permutation.cpp, ported here
  // (not called into from PHP) so the record's own real deck drives this
  // widget instead of a fixed illustrative example. Deal indices exceed
  // 64 bits (up to ~6.5e20), hence BigInt throughout -- JS's native
  // arbitrary-precision integer type, no library needed. Verified against
  // this project's own test vectors (tests/test_data_gen.hpp), including
  // both the first and last possible indices and every real historical
  // record in that file.
  function buildNCrTable() {
    var table = [];
    for (var n = 0; n <= 52; n++) {
      table.push(new Array(53).fill(0n));
      table[n][0] = 1n;
      for (var r = 1; r <= n; r++) {
        table[n][r] = table[n - 1][r - 1] + table[n - 1][r];
      }
    }
    return table;
  }
  var nCrTable = buildNCrTable();

  function fastMultinomial(counts) {
    var n = counts.reduce(function(a, b) { return a + b; }, 0);
    var res = 1n, currentN = n;
    for (var i = 0; i < 5; i++) {
      if (counts[i] > 0) {
        res *= nCrTable[currentN][counts[i]];
        currentN -= counts[i];
      }
    }
    return res;
  }

  // Returns an array of 52 single-character cards ('A'/'K'/'Q'/'J'/'2'),
  // same shape and same '2' convention worker.cpp/permutation.cpp use for
  // every non-honor card -- rank never matters beyond honor-or-not, so
  // there's nothing else to distinguish.
  function getNthPermutation(indexStr) {
    var n = BigInt(indexStr);
    var counts = [4, 4, 4, 4, 36];
    var symbols = ['A', 'K', 'Q', 'J', '2'];
    var result = [];
    for (var i = 0; i < 52; i++) {
      for (var s = 0; s < 5; s++) {
        if (counts[s] === 0) continue;
        counts[s]--;
        var num = fastMultinomial(counts);
        if (n < num) {
          result.push(symbols[s]);
          break;
        } else {
          n -= num;
          counts[s]++;
        }
      }
    }
    return result;
  }

  var realDeck = getNthPermutation(<?php echo json_encode($longest['deal_index']); ?>);
  // Same split worker.cpp itself uses: first 26 dealt to A, last 26 to B,
  // no interleaving.
  var deckA = realDeck.slice(0, 26);
  var deckB = realDeck.slice(26);

  function penaltyOf(c) { return { A: 4, K: 3, Q: 2, J: 1 }[c] || 0; }

  function makeEngine(from) {
    var deckA, deckB, pile, turn, penaltyRemaining, lastPaymentPlayer, totalCardsPlayed, totalTricks;
    if (from.handA) {
      deckA = from.handA.slice(); deckB = from.handB.slice(); pile = [];
      turn = 0; penaltyRemaining = 0; lastPaymentPlayer = -1;
      totalCardsPlayed = 0; totalTricks = 0;
    } else {
      deckA = from.deckA.slice(); deckB = from.deckB.slice(); pile = from.pile.slice();
      turn = from.turn; penaltyRemaining = from.penaltyRemaining; lastPaymentPlayer = from.lastPaymentPlayer;
      totalCardsPlayed = from.totalCardsPlayed; totalTricks = from.totalTricks;
    }
    function step() {
      var activeDeck = turn === 0 ? deckA : deckB;
      var opponentDeck = turn === 0 ? deckB : deckA;
      if (activeDeck.length === 0) {
        if (pile.length === 0) return { done: true };
        opponentDeck.push.apply(opponentDeck, pile); pile = []; totalTricks++;
        if (opponentDeck.length === 52) return { done: true };
        turn = 1 - turn; penaltyRemaining = 0; lastPaymentPlayer = -1;
        return { done: false, event: 'sweep' };
      }
      var played = activeDeck.shift();
      pile.push(played); totalCardsPlayed++;
      var penalty = penaltyOf(played);
      if (penalty > 0) {
        penaltyRemaining = penalty; lastPaymentPlayer = turn; turn = 1 - turn;
        return { done: false, event: 'penalty' };
      }
      if (penaltyRemaining > 0) {
        penaltyRemaining--;
        if (penaltyRemaining === 0) {
          var winnerDeck = lastPaymentPlayer === 0 ? deckA : deckB;
          winnerDeck.push.apply(winnerDeck, pile); pile = []; totalTricks++;
          if (winnerDeck.length === 52) return { done: true };
          turn = lastPaymentPlayer; lastPaymentPlayer = -1;
          return { done: false, event: 'trickWon' };
        }
        return { done: false, event: 'pay' };
      }
      turn = 1 - turn;
      return { done: false, event: 'lead' };
    }
    return {
      step: step,
      state: function() { return { deckA: deckA, deckB: deckB, pile: pile, turn: turn, totalCardsPlayed: totalCardsPlayed, totalTricks: totalTricks }; },
      fullState: function() { return { deckA: deckA.slice(), deckB: deckB.slice(), pile: pile.slice(), turn: turn, penaltyRemaining: penaltyRemaining, lastPaymentPlayer: lastPaymentPlayer, totalCardsPlayed: totalCardsPlayed, totalTricks: totalTricks }; },
    };
  }

  function renderSlots(cards) {
    var filled = cards.map(function(c) {
      return c === '2' ? '<div class="slot plain"></div>' : '<div class="slot honor">' + c + '</div>';
    }).join('');
    var empty = new Array(52 - cards.length + 1).join('<div class="slot empty"></div>');
    return filled + empty;
  }

  function renderGameTable(state) {
    document.getElementById('gameTable').innerHTML =
      '<div class="zone-col ' + (state.turn === 0 ? 'active' : '') + '"><p class="zone-label">' + <?php echo json_encode(tra("Player A")); ?> + '</p><p class="zone-count">' + state.deckA.length + '</p><div class="card-slots">' + renderSlots(state.deckA) + '</div></div>' +
      '<div class="zone-col pile"><p class="zone-label">' + <?php echo json_encode(tra("On the table")); ?> + '</p><p class="zone-count">' + state.pile.length + '</p><div class="card-slots">' + renderSlots(state.pile) + '</div></div>' +
      '<div class="zone-col ' + (state.turn === 1 ? 'active' : '') + '"><p class="zone-label">' + <?php echo json_encode(tra("Player B")); ?> + '</p><p class="zone-count">' + state.deckB.length + '</p><div class="card-slots">' + renderSlots(state.deckB) + '</div></div>';
  }

  var statusEl = document.getElementById('gameStatus');
  var playBtn = document.getElementById('playBtn');
  var backBtn = document.getElementById('backBtn');
  var fwdBtn = document.getElementById('fwdBtn');
  var resetBtn = document.getElementById('resetBtn');
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function formatNum(n) {
    return Number(n).toLocaleString('en-US');
  }

  var statusTplLoadedDeal = <?php echo json_encode(tra("Loaded deal #%1. Press Play to watch it, or step through it one move at a time.")); ?>;
  var statusTplGameOver = <?php echo json_encode(tra("Game over! %1 cards played across %2 tricks.")); ?>;
  var statusTplPenalty = <?php echo json_encode(tra("%1 played: player %2 must pay cards")); ?>;
  var statusTplTrickWon = <?php echo json_encode(tra("Player %1 wins the cards on the table")); ?>;
  var statusTplSweep = <?php echo json_encode(tra("No cards left to play: the cards on the table pass to the opponent")); ?>;
  var statusTplReady = <?php echo json_encode(tra("Ready. Press Play to watch it, or step through it one move at a time.")); ?>;
  var statusTplTurn = <?php echo json_encode(tra("Player %1's turn")); ?>;

  function statusFor(state, event, done) {
    if (done) return statusTplGameOver.replace('%1', '<b>' + formatNum(state.totalCardsPlayed) + '</b>').replace('%2', '<b>' + formatNum(state.totalTricks) + '</b>');
    if (event === 'penalty') return statusTplPenalty.replace('%1', '<b>' + state.pile[state.pile.length - 1] + '</b>').replace('%2', state.turn === 0 ? 'A' : 'B');
    if (event === 'trickWon') return statusTplTrickWon.replace('%1', '<b>' + (state.turn === 0 ? 'A' : 'B') + '</b>');
    if (event === 'sweep') return statusTplSweep;
    if (event === null) return statusTplReady;
    return statusTplTurn.replace('%1', state.turn === 0 ? 'A' : 'B');
  }

  var CHECKPOINT_EVERY = 25;
  var checkpoints, current, playing = false, timer = null;
  var playBtnText = '▶ ' + <?php echo json_encode(tra("Play")); ?>;
  var pauseBtnText = '⏸ ' + <?php echo json_encode(tra("Pause")); ?>;

  function render() {
    renderGameTable(current.state);
    statusEl.innerHTML = statusFor(current.state, current.event, current.done);
    backBtn.disabled = current.step === 0;
    fwdBtn.disabled = current.done;
    playBtn.disabled = current.done;
  }

  function goToStep(target) {
    if (target < 0) target = 0;
    if (target === current.step) return true;
    var cp = checkpoints[0];
    for (var i = 0; i < checkpoints.length; i++) { if (checkpoints[i].step <= target) cp = checkpoints[i]; else break; }
    var engine = makeEngine(cp.full);
    var step = cp.step, state = engine.state(), full = cp.full, event = cp.event, done = cp.done;
    while (step < target && !done) {
      var result = engine.step();
      step++;
      state = engine.state();
      full = engine.fullState();
      event = result.event || null;
      done = !!result.done;
      if (step % CHECKPOINT_EVERY === 0 || done) {
        if (!checkpoints.some(function(c) { return c.step === step; })) checkpoints.push({ step: step, full: full, event: event, done: done });
      }
    }
    current = { step: step, state: state, event: event, done: done };
    render();
    return !done;
  }

  function stepForward() { return goToStep(current.step + 1); }
  function stepBack() { goToStep(current.step - 1); }

  function stopPlaying() {
    playing = false;
    if (timer) { clearTimeout(timer); timer = null; }
    playBtn.textContent = playBtnText;
  }

  function startPlaying() {
    playing = true;
    playBtn.textContent = pauseBtnText;
    (function loop() {
      if (!playing) return;
      if (!stepForward()) { stopPlaying(); return; }
      timer = setTimeout(loop, reduceMotion ? 0 : 220);
    })();
  }

  function resetGame() {
    stopPlaying();
    var engine = makeEngine({ handA: deckA, handB: deckB });
    var full = engine.fullState();
    checkpoints = [{ step: 0, full: full, event: null, done: false }];
    current = { step: 0, state: engine.state(), event: null, done: false };
    render();
  }

  playBtn.addEventListener('click', function() { playing ? stopPlaying() : startPlaying(); });
  backBtn.addEventListener('click', function() { stopPlaying(); stepBack(); });
  fwdBtn.addEventListener('click', function() { stopPlaying(); stepForward(); });
  resetBtn.addEventListener('click', resetGame);

  function loadDeal(dealInfo) {
    stopPlaying();
    var dealStr = (typeof dealInfo === 'object') ? dealInfo.deal : dealInfo;
    realDeck = getNthPermutation(dealStr);
    deckA = realDeck.slice(0, 26);
    deckB = realDeck.slice(26);
    resetGame();
    if (typeof dealInfo === 'object') {
      updateVisualizerHeader(dealInfo);
    }
    statusEl.innerHTML = statusTplLoadedDeal.replace('%1', '<b>' + dealStr + '</b>');
    var el = document.getElementById('vizCard');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  var championDeal = {
    deal: <?php echo json_encode($longest['deal_index'] ?? ''); ?>,
    cards: <?php echo (int)($longest['cards'] ?? 0); ?>,
    tricks: <?php echo (int)($longest['tricks'] ?? 0); ?>,
    date: <?php echo json_encode(!empty($longest['found_at']) ? date('d/m/Y', $longest['found_at']) : ''); ?>,
    author: <?php echo json_encode($longest['user_html'] ?? ''); ?>,
    isWr: <?php echo !empty($longest['is_world_record']) ? 'true' : 'false'; ?>,
    type: 'champion'
  };

  var vizTagContainer = document.getElementById('vizTagContainer');
  var vizSubtitle = document.getElementById('vizSubtitle');
  var vizMoveCount = document.getElementById('vizMoveCount');
  var vizMetaDate = document.getElementById('vizMetaDate');
  var vizMetaDeal = document.getElementById('vizMetaDeal');
  var vizMetaAuthor = document.getElementById('vizMetaAuthor');
  var vizPlaybackDesc = document.getElementById('vizPlaybackDesc');

  function updateVisualizerHeader(info) {
    if (!vizTagContainer) return;
    var isChampion = (info.deal === championDeal.deal);

    if (isChampion) {
      if (championDeal.isWr) {
        vizTagContainer.innerHTML = '<span class="discovery-tag world-record"><?php echo tra("World record breakthrough (>8,344 cards)"); ?></span>';
      } else {
        vizTagContainer.innerHTML = '<span class="discovery-tag camicia"><?php echo tra("Found by Camicia"); ?></span>';
      }
      vizSubtitle.innerHTML = '<?php echo tra("The longest game found so far"); ?>';
      vizMoveCount.innerHTML = formatNum(championDeal.cards) + ' <?php echo tra("cards played"); ?> <span style="font-size:18px;color:var(--cream-dim)">&middot; ' + formatNum(championDeal.tricks) + ' <?php echo tra("tricks"); ?></span>';
      vizPlaybackDesc.innerHTML = '<?php echo tra("Below, %1the actual longest game found%2, played back move by move.", "<em>", "</em>"); ?>';
      if (vizMetaDate) vizMetaDate.textContent = '<?php echo tra("Confirmed on"); ?> ' + championDeal.date;
      if (vizMetaDeal) vizMetaDeal.textContent = '<?php echo tra("Deal #%1", ""); ?>' + championDeal.deal;
      if (vizMetaAuthor) {
        if (championDeal.author) {
          vizMetaAuthor.style.display = '';
          vizMetaAuthor.innerHTML = '<?php echo tra("Discovered by"); ?> ' + championDeal.author;
        } else {
          vizMetaAuthor.style.display = 'none';
        }
      }
    } else if (info.type === 'loop') {
      vizTagContainer.innerHTML = '<span class="discovery-tag camicia"><?php echo tra("Loop Discovery"); ?></span>';
      vizSubtitle.innerHTML = '<?php echo tra("Non-terminating loop game"); ?>';
      vizMoveCount.innerHTML = '<?php echo tra("Infinite loop"); ?> <span style="font-size:18px;color:var(--cream-dim)">&middot; <?php echo tra("never terminates"); ?></span>';
      vizPlaybackDesc.innerHTML = '<?php echo tra("Below, the non-terminating game, played back move by move."); ?>';
      if (vizMetaDate) vizMetaDate.textContent = info.date ? '<?php echo tra("Confirmed on"); ?> ' + info.date : '';
      if (vizMetaDeal) vizMetaDeal.textContent = '<?php echo tra("Deal #%1", ""); ?>' + info.deal;
      if (vizMetaAuthor) {
        if (info.author) {
          vizMetaAuthor.style.display = '';
          vizMetaAuthor.innerHTML = '<?php echo tra("Discovered by"); ?> ' + info.author;
        } else {
          vizMetaAuthor.style.display = 'none';
        }
      }
    } else {
      var isWr = (info.wr === '1' || info.wr === 1 || info.isWr);
      if (isWr) {
        vizTagContainer.innerHTML = '<span class="discovery-tag world-record"><?php echo tra("World record breakthrough (>8,344 cards)"); ?></span>';
      } else {
        vizTagContainer.innerHTML = '<span class="discovery-tag" style="background:var(--felt);border:1px solid var(--gold-dim);color:var(--gold)"><?php echo tra("Historical Milestone"); ?></span>';
      }
      vizSubtitle.innerHTML = '<?php echo tra("Historical record milestone"); ?>';
      vizMoveCount.innerHTML = formatNum(info.cards) + ' <?php echo tra("cards played"); ?> <span style="font-size:18px;color:var(--cream-dim)">&middot; ' + formatNum(info.tricks) + ' <?php echo tra("tricks"); ?></span>';
      vizPlaybackDesc.innerHTML = '<?php echo tra("Below, the historical milestone record, played back move by move."); ?>';
      if (vizMetaDate) vizMetaDate.textContent = info.date ? '<?php echo tra("Confirmed on"); ?> ' + info.date : '';
      if (vizMetaDeal) vizMetaDeal.textContent = '<?php echo tra("Deal #%1", ""); ?>' + info.deal;
      if (vizMetaAuthor) {
        if (info.author) {
          vizMetaAuthor.style.display = '';
          vizMetaAuthor.innerHTML = '<?php echo tra("Discovered by"); ?> ' + info.author;
        } else {
          vizMetaAuthor.style.display = 'none';
        }
      }
    }
  }

  document.addEventListener('click', function(e) {
    var btn = e.target.closest('.replay-deal-btn');
    if (!btn || !btn.dataset.deal) return;
    loadDeal({
      deal: btn.dataset.deal,
      type: btn.dataset.type || 'longest',
      cards: btn.dataset.cards || 0,
      tricks: btn.dataset.tricks || 0,
      date: btn.dataset.date || '',
      author: btn.dataset.author || '',
      wr: btn.dataset.wr || '0'
    });
  });

  resetGame();
<?php endif; ?>

  function paginateTable(tableId, paginationId, prevBtnId, nextBtnId, pageInfoId, pageSize) {
    var table = document.getElementById(tableId);
    if (!table) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    var rows = Array.from(tbody.querySelectorAll('tr'));
    if (rows.length <= pageSize) return;

    var paginationEl = document.getElementById(paginationId);
    var prevBtn = document.getElementById(prevBtnId);
    var nextBtn = document.getElementById(nextBtnId);
    var pageInfo = document.getElementById(pageInfoId);
    if (!paginationEl || !prevBtn || !nextBtn || !pageInfo) return;

    paginationEl.style.display = 'flex';
    var currentPage = 1;
    var totalPages = Math.ceil(rows.length / pageSize);

    function updatePage() {
      var start = (currentPage - 1) * pageSize;
      var end = start + pageSize;
      rows.forEach(function(row, idx) {
        row.style.display = (idx >= start && idx < end) ? '' : 'none';
      });
      pageInfo.textContent = '<?php echo tra("Page"); ?> ' + currentPage + ' <?php echo tra("of"); ?> ' + totalPages + ' (' + rows.length + ' <?php echo tra("total"); ?>)';
      prevBtn.disabled = currentPage === 1;
      nextBtn.disabled = currentPage === totalPages;
    }

    prevBtn.addEventListener('click', function() {
      if (currentPage > 1) {
        currentPage--;
        updatePage();
        var target = table.closest('.discovery') || table;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });

    nextBtn.addEventListener('click', function() {
      if (currentPage < totalPages) {
        currentPage++;
        updatePage();
        var target = table.closest('.discovery') || table;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });

    updatePage();
  }

  paginateTable('hofTable', 'hofPagination', 'hofPrevBtn', 'hofNextBtn', 'hofPageInfo', 25);
  paginateTable('loopsTable', 'loopsPagination', 'loopsPrevBtn', 'loopsNextBtn', 'loopsPageInfo', 25);
})();
</script>
<?php
page_tail();
?>
