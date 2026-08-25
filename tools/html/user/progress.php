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

// records_longest.txt: one line "<cards> <tricks> <deal_index> <wu_name> <unix_time>"
function read_longest_record() {
    $path = '../../records_longest.txt';
    if (!file_exists($path)) return null;
    $parts = explode(' ', trim(file_get_contents($path)));
    if (count($parts) < 5) return null;
    return array(
        'cards' => (int)$parts[0],
        'tricks' => (int)$parts[1],
        'deal_index' => $parts[2],
        'wu_name' => $parts[3],
        'found_at' => (int)$parts[4],
    );
}

// records_loops.txt: append-only, one line per loop "<deal_index> <wu_name> <unix_time>"
function read_loops_found() {
    $path = '../../records_loops.txt';
    if (!file_exists($path)) return array();
    $loops = array();
    foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $parts = explode(' ', trim($line));
        if (count($parts) < 3) continue;
        $loops[] = array(
            'deal_index' => $parts[0],
            'wu_name' => $parts[1],
            'found_at' => (int)$parts[2],
        );
    }
    return array_reverse($loops); // newest first
}

$stats = read_progress_stats();
$longest = read_longest_record();
$loops_found = read_loops_found();

$search_space_blocks = $stats ? (int)$stats['search_space_blocks'] : FALLBACK_SEARCH_SPACE_BLOCKS;
$blocks_confirmed_total = $stats ? (int)$stats['blocks_confirmed_total'] : 0;
$pct = $search_space_blocks > 0 ? ($blocks_confirmed_total / $search_space_blocks) * 100 : 0;
$pct_display = ($pct > 0 && $pct < 0.001) ? "&lt;0.001" : number_format($pct, 3);
// A meaningful minimum width so the fill is visible at all at these scales
// (the real percentage is astronomically close to 0 for a long time).
$meter_pct = max($pct, 0.05);

$today_pace = $stats ? $stats['today_pace'] : array('confirmed' => 0, 'rechecking' => 0, 'waiting' => 0);
$pace_total = max(1, $today_pace['confirmed'] + $today_pace['rechecking'] + $today_pace['waiting']);

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
.progress-page .hero-number { font-family: Georgia, serif; font-size: 84px; line-height: 1; color: var(--gold); margin: 0; font-variant-numeric: tabular-nums; }
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
.progress-page .pipeline { border: 1px solid var(--gold-dim); border-radius: 14px; padding: 26px 28px 24px; margin-bottom: 40px; }
.progress-page .segbar { display: flex; height: 22px; border-radius: 6px; overflow: hidden; gap: 2px; background: var(--felt); }
.progress-page .seg { height: 100%; }
.progress-page .seglegend { display: flex; gap: 22px; flex-wrap: wrap; margin-top: 16px; font-size: 13px; color: var(--cream-dim); }
.progress-page .seglegend span { display: inline-flex; align-items: center; gap: 8px; }
.progress-page .swatch { width: 11px; height: 11px; border-radius: 3px; }
.progress-page .seglegend b { color: var(--cream); font-variant-numeric: tabular-nums; }
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
.progress-page .legend-note .chip { display: inline-flex; width: 18px; height: 24px; font-size: 10px; vertical-align: -6px; margin: 0 4px; }
.progress-page .discovery-tag { display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; padding: 3px 9px; border-radius: 999px; margin-bottom: 10px; }
.progress-page .discovery-tag.camicia { background: var(--gold); color: var(--felt); }
.progress-page .discovery-tag.reference { background: transparent; border: 1px solid var(--cream-dim); color: var(--cream-dim); }
.progress-page .source-note { font-size: 12px; color: var(--cream-dim); margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--felt-line); line-height: 1.6; }
.progress-page .source-note a { color: var(--gold); }
.progress-page .empty-state { background: var(--felt-2); border-radius: 10px; padding: 20px 22px; text-align: center; color: var(--cream-dim); font-size: 13.5px; line-height: 1.6; margin-top: 16px; }
.progress-page .empty-state b { color: var(--cream); }
.progress-page .loop-list { display: flex; flex-direction: column; gap: 2px; background: var(--felt-line); border-radius: 10px; overflow: hidden; border: 1px solid var(--felt-line); margin-top: 10px; }
.progress-page .loop-row { background: var(--felt-2); padding: 14px 18px; display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; font-size: 13px; }
.progress-page .loop-row b { color: var(--gold); font-family: Georgia, serif; }
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
    <p class="hero-sub"><?php echo tra("%1 possible blocks in total", number_format($search_space_blocks)); ?></p>
    <div class="meter"><div class="meter-fill" style="width:<?php echo $meter_pct; ?>%"></div></div>
    <p class="meter-caption"><?php echo tra("%1 blocks confirmed out of %2", number_format($blocks_confirmed_total), number_format($search_space_blocks)); ?></p>
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

  <section class="pipeline">
    <h2 class="section-title"><?php echo tra("Today's pace"); ?></h2>
    <p class="section-sub"><?php echo tra("What happened to the blocks worked on in the last 24 hours"); ?></p>
    <div class="segbar">
      <div class="seg" style="background:var(--gold);width:<?php echo round($today_pace['confirmed'] / $pace_total * 100, 2); ?>%"></div>
      <div class="seg" style="background:var(--ruby);width:<?php echo round($today_pace['rechecking'] / $pace_total * 100, 2); ?>%"></div>
      <div class="seg" style="background:var(--gold-pale);width:<?php echo round($today_pace['waiting'] / $pace_total * 100, 2); ?>%"></div>
    </div>
    <div class="seglegend">
      <span><i class="swatch" style="background:var(--gold)"></i><?php echo tra("Confirmed"); ?> &middot; <b><?php echo number_format($today_pace['confirmed']); ?></b></span>
      <span><i class="swatch" style="background:var(--ruby)"></i><?php echo tra("Being rechecked (the two volunteers disagreed)"); ?> &middot; <b><?php echo number_format($today_pace['rechecking']); ?></b></span>
      <span><i class="swatch" style="background:var(--gold-pale)"></i><?php echo tra("Waiting on a second result"); ?> &middot; <b><?php echo number_format($today_pace['waiting']); ?></b></span>
    </div>
  </section>

  <section>
    <h2 class="section-title" style="margin-bottom:16px"><?php echo tra("Discoveries"); ?></h2>

    <div class="discovery">
<?php if ($longest): ?>
      <span class="discovery-tag camicia"><?php echo tra("Found by Camicia"); ?></span>
      <p class="section-sub" style="margin-bottom:2px"><?php echo tra("The longest game found so far"); ?></p>
      <p class="move-count"><?php echo tra("%1 hands played", number_format($longest['tricks'])); ?></p>
      <div class="discovery-meta">
        <span><?php echo tra("Confirmed on %1", date('F j, Y', $longest['found_at'])); ?></span>
        <span><?php echo tra("Deal #%1", $longest['deal_index']); ?></span>
      </div>
<?php else: ?>
      <p class="section-sub" style="margin-bottom:2px"><?php echo tra("The longest game found so far"); ?></p>
      <div class="empty-state"><?php echo tra("Camicia hasn't confirmed any games yet -- check back once the search is running."); ?></div>
<?php endif; ?>
      <p class="section-sub" style="margin:16px 0 4px">
        <?php echo tra("Below, a playable example showing %1how%2 a game works, built on the same game engine.", "<em>", "</em>"); ?>
      </p>
      <div class="game-table" id="gameTable"></div>
      <div class="status-line" id="gameStatus">Ready. Press Play to watch it, or step through it one move at a time.</div>
      <div class="game-controls">
        <button class="ctrl-btn" id="resetBtn" title="Back to the start">|&#9664; Reset</button>
        <button class="ctrl-btn" id="backBtn" title="One step back">&#9664; Back</button>
        <button class="ctrl-btn play-btn" id="playBtn" title="Play / Pause">&#9654; Play</button>
        <button class="ctrl-btn" id="fwdBtn" title="One step forward">Forward &#9654;</button>
      </div>
      <p class="legend-note">
        <?php echo tra("Only aces, kings, queens and jacks determine the outcome of the game -- suit never matters either, only rank."); ?>
        <span class="chip honor">K</span> <?php echo tra("affects the result, a generic"); ?> <span class="chip plain"></span> <?php echo tra("doesn't: that's why every other card is shown the same."); ?>
      </p>
    </div>

    <div class="discovery">
      <span class="discovery-tag reference"><?php echo tra("Historical reference &mdash; not found by Camicia"); ?></span>
      <p class="section-sub" style="margin-bottom:2px"><?php echo tra("The first documented loop for this game"); ?></p>
      <p class="move-count"><?php echo tra("474 moves, then enters a cycle of 66 deals"); ?></p>
      <div class="discovery-meta">
        <span><?php echo tra("Discovered by %1", "Brayden Casella"); ?></span>
        <span><?php echo tra("February 10, 2024"); ?></span>
      </div>
      <div class="deal-static" id="dealVizCasella"></div>
      <p class="source-note">
        <?php echo tra("Shown as an example of how a loop found by Camicia will appear below -- this specific loop was discovered with a different method, not by this project. Source:"); ?>
        <a href="https://arxiv.org/abs/2403.13855" target="_blank" rel="noopener">Casella et al., "A Non-Terminating Game of Beggar-My-Neighbor", 2024</a>.
      </p>
    </div>

    <div class="discovery">
      <p class="section-sub" style="margin-bottom:2px"><?php echo tra("Loops found by Camicia"); ?></p>
<?php if (count($loops_found) > 0): ?>
      <div class="loop-list">
<?php foreach ($loops_found as $loop): ?>
        <div class="loop-row">
          <span><?php echo tra("Deal #%1", $loop['deal_index']); ?></span>
          <span><b><?php echo $loop['wu_name']; ?></b></span>
          <span><?php echo date('F j, Y', $loop['found_at']); ?></span>
        </div>
<?php endforeach; ?>
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
    '<div class="deal-row-label">Hand A</div><div class="deal-row">' + renderHand(casellaA) + '</div>' +
    '<div class="deal-row-label" style="margin-top:8px">Hand B</div><div class="deal-row">' + renderHand(casellaB) + '</div>';

  // ---- Playable animation, driven by the exact same rules as
  // tools/worker/core/engine.cpp's CamiciaGame::simulate().
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

  // A short, playable example deal (not the real record above) -- finishes
  // in 66 card-plays / 9 tricks, verified against this exact algorithm.
  var demoA = ['Q','N','N','J','N','N','Q','N','N','N','N','N','A','N','K','A','Q','N','N','K','N','J','N','N','J','A'];
  var demoB = ['N','N','N','N','N','N','N','A','N','K','N','N','N','N','Q','N','N','N','N','N','J','N','K','N','N','N'];

  function renderSlots(cards) {
    var filled = cards.map(function(c) {
      return c === 'N' ? '<div class="slot plain"></div>' : '<div class="slot honor">' + c + '</div>';
    }).join('');
    var empty = new Array(52 - cards.length + 1).join('<div class="slot empty"></div>');
    return filled + empty;
  }

  function renderGameTable(state) {
    document.getElementById('gameTable').innerHTML =
      '<div class="zone-col ' + (state.turn === 0 ? 'active' : '') + '"><p class="zone-label">Player A</p><p class="zone-count">' + state.deckA.length + '</p><div class="card-slots">' + renderSlots(state.deckA) + '</div></div>' +
      '<div class="zone-col pile"><p class="zone-label">On the table</p><p class="zone-count">' + state.pile.length + '</p><div class="card-slots">' + renderSlots(state.pile) + '</div></div>' +
      '<div class="zone-col ' + (state.turn === 1 ? 'active' : '') + '"><p class="zone-label">Player B</p><p class="zone-count">' + state.deckB.length + '</p><div class="card-slots">' + renderSlots(state.deckB) + '</div></div>';
  }

  var statusEl = document.getElementById('gameStatus');
  var playBtn = document.getElementById('playBtn');
  var backBtn = document.getElementById('backBtn');
  var fwdBtn = document.getElementById('fwdBtn');
  var resetBtn = document.getElementById('resetBtn');
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function statusFor(state, event, done) {
    if (done) return 'Game over! <b>' + state.totalCardsPlayed + '</b> cards played across <b>' + state.totalTricks + '</b> tricks.';
    if (event === 'penalty') return '<b>' + state.pile[state.pile.length - 1] + '</b> played: player ' + (state.turn === 0 ? 'A' : 'B') + ' must pay cards';
    if (event === 'trickWon') return 'Player <b>' + (state.turn === 0 ? 'A' : 'B') + '</b> wins the cards on the table';
    if (event === 'sweep') return 'No cards left to play: the cards on the table pass to the opponent';
    if (event === null) return 'Ready. Press Play to watch it, or step through it one move at a time.';
    return "Player " + (state.turn === 0 ? 'A' : 'B') + "'s turn";
  }

  var CHECKPOINT_EVERY = 25;
  var checkpoints, current, playing = false, timer = null;

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
    playBtn.textContent = '▶ Play';
  }

  function startPlaying() {
    playing = true;
    playBtn.textContent = 'Pause';
    (function loop() {
      if (!playing) return;
      if (!stepForward()) { stopPlaying(); return; }
      timer = setTimeout(loop, reduceMotion ? 0 : 220);
    })();
  }

  function resetGame() {
    stopPlaying();
    var engine = makeEngine({ handA: demoA, handB: demoB });
    var full = engine.fullState();
    checkpoints = [{ step: 0, full: full, event: null, done: false }];
    current = { step: 0, state: engine.state(), event: null, done: false };
    render();
  }

  playBtn.addEventListener('click', function() { playing ? stopPlaying() : startPlaying(); });
  backBtn.addEventListener('click', function() { stopPlaying(); stepBack(); });
  fwdBtn.addEventListener('click', function() { stopPlaying(); stepForward(); });
  resetBtn.addEventListener('click', resetGame);

  resetGame();
})();
</script>
<?php
page_tail();
?>
