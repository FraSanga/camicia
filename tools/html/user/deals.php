<?php
// Volunteer Deals ("My Deals" / "The Deal Registry")
//
// Shows a volunteer's lifetime contribution to exploring the 6.535x10^20
// permutation space: total deals simulated and verified, percentage of the 52-card
// deck space, role breakdown (Explorer vs Verifier), personal longest game record,
// loops discovered, and recent verified blocks.
//
// Accessible via:
// - /deals.php (shows logged-in user's deals, or global registry if logged out)
// - /deals.php?userid=123 (shareable link to volunteer #123)
// - /deals.php?q=username (search by username or user id)
// - /deals.php?all=1 (forces the global deal registry / leaderboard)

require_once('../inc/util.inc');
require_once('../inc/translation.inc');

db_init();
$db = BoincDb::get();

define('TOTAL_SEARCH_SPACE', '653534134886878244999');

$logged_in_user = get_logged_in_user(false);
$userid = get_int('userid', true);
$search_query = get_str('q', true);
$show_all = get_int('all', true);

function is_valid_boinc_user($user) {
    if (!$user) return false;
    if (isset($user->authenticator) && strstr($user->authenticator, 'deleted')) return false;
    return true;
}

// Access control: block logged-out users from viewing individual volunteer deals or searching
if (($userid || ($search_query !== null && $search_query !== '')) && !$logged_in_user) {
    get_logged_in_user(true); // redirects to login_form.php?next_url=...
}

$user_not_found = false;
$target_user = null;

// Search resolution (only reachable by logged-in users)
if ($search_query !== null && $search_query !== '') {
    $search_query = trim($search_query);
    if (is_numeric($search_query)) {
        $userid = (int)$search_query;
    } else {
        $escaped = BoincDb::escape_string($search_query);
        $res = $db->do_query("SELECT id FROM user WHERE name LIKE '%$escaped%' LIMIT 1");
        if ($res && $row = $res->fetch_row()) {
            $userid = (int)$row[0];
        } else {
            $user_not_found = true;
        }
        if ($res) $res->free();
    }
}

// User lookup
if ($userid) {
    $u = BoincUser::lookup_id($userid);
    if (is_valid_boinc_user($u)) {
        $target_user = $u;
    } else {
        $user_not_found = true;
    }
} elseif (!$show_all && $logged_in_user && ($search_query === null || $search_query === '')) {
    // Default to logged-in user when no specific user requested, no search, and not forcing global view
    $target_user = $logged_in_user;
}

// =========================================================================
// DATA FETCHING: INDIVIDUAL VOLUNTEER DEALS
// =========================================================================
if ($target_user) {
    $uid = (int)$target_user->id;

    // 1. Explorer stats (primary solver / canonical)
    $res = $db->do_query("
        SELECT 
            COUNT(*) as ranges_count,
            COALESCE(SUM(range_end - range_start + 1), 0) as deals_count,
            COALESCE(MAX(max_cards), 0) as max_cards,
            COALESCE(SUM(loops_count), 0) as loops_count,
            MIN(assimilated_at) as first_conquered,
            MAX(assimilated_at) as last_conquered
        FROM camicia_completed_ranges
        WHERE user_id = $uid
    ");
    $exp_stats = $res ? $res->fetch_assoc() : [];
    if ($res) $res->free();

    // 2. Verifier stats (quorum validator)
    $res = $db->do_query("
        SELECT 
            COUNT(*) as ranges_count,
            COALESCE(SUM(range_end - range_start + 1), 0) as deals_count,
            COALESCE(MAX(max_cards), 0) as max_cards,
            COALESCE(SUM(loops_count), 0) as loops_count,
            MIN(assimilated_at) as first_conquered,
            MAX(assimilated_at) as last_conquered
        FROM camicia_completed_ranges
        WHERE verifier_user_id = $uid
    ");
    $ver_stats = $res ? $res->fetch_assoc() : [];
    if ($res) $res->free();

    $exp_ranges = (int)($exp_stats['ranges_count'] ?? 0);
    $ver_ranges = (int)($ver_stats['ranges_count'] ?? 0);
    $total_ranges = $exp_ranges + $ver_ranges;

    $exp_deals = (float)($exp_stats['deals_count'] ?? 0);
    $ver_deals = (float)($ver_stats['deals_count'] ?? 0);
    $total_deals = $exp_deals + $ver_deals;

    $personal_max_cards = max(
        (int)($exp_stats['max_cards'] ?? 0),
        (int)($ver_stats['max_cards'] ?? 0)
    );

    $total_loops = (int)($exp_stats['loops_count'] ?? 0) + (int)($ver_stats['loops_count'] ?? 0);

    // Timestamps
    $f1 = (int)($exp_stats['first_conquered'] ?? 0);
    $f2 = (int)($ver_stats['first_conquered'] ?? 0);
    $first_ts = ($f1 > 0 && $f2 > 0) ? min($f1, $f2) : max($f1, $f2);

    $l1 = (int)($exp_stats['last_conquered'] ?? 0);
    $l2 = (int)($ver_stats['last_conquered'] ?? 0);
    $last_ts = max($l1, $l2);

    // Cosmic fraction
    $cosmic_pct = '0';
    if ($total_deals > 0) {
        $pct_num = ($total_deals / 6.53534134886878245e20) * 100;
        $cosmic_pct = sprintf("%.14f", $pct_num);
        $cosmic_pct = rtrim(rtrim($cosmic_pct, '0'), '.');
        if ($cosmic_pct === '' || $cosmic_pct === '0') $cosmic_pct = '<0.00000000000001';
    }

    // 3. Personal best game details
    $best_game = null;
    if ($personal_max_cards > 0) {
        $res = $db->do_query("
            SELECT range_start, range_end, best_deal_index, max_cards, max_tricks, assimilated_at
            FROM camicia_completed_ranges
            WHERE (user_id = $uid OR verifier_user_id = $uid) AND max_cards = $personal_max_cards
            ORDER BY assimilated_at ASC
            LIMIT 1
        ");
        if ($res && $row = $res->fetch_assoc()) {
            $best_game = $row;
        }
        if ($res) $res->free();
    }

    // 4. Recent conquered ranges (up to 20)
    $recent_ranges = [];
    $res = $db->do_query("
        SELECT range_start, range_end, best_deal_index, max_cards, max_tricks, loops_count, user_id, verifier_user_id, assimilated_at
        FROM camicia_completed_ranges
        WHERE user_id = $uid OR verifier_user_id = $uid
        ORDER BY assimilated_at DESC
        LIMIT 20
    ");
    if ($res) {
        while ($row = $res->fetch_assoc()) {
            $partner_id = ((int)$row['user_id'] === $uid) ? (int)$row['verifier_user_id'] : (int)$row['user_id'];
            $partner_user = $partner_id > 0 ? BoincUser::lookup_id($partner_id) : null;
            $partner_valid = is_valid_boinc_user($partner_user);
            $row['is_explorer'] = ((int)$row['user_id'] === $uid);
            $row['partner_id'] = $partner_id;
            $row['partner_user'] = $partner_user;
            $row['partner_valid'] = $partner_valid;
            $recent_ranges[] = $row;
        }
        $res->free();
    }

    $page_title = tra("%1's Deals", $target_user->name);
} elseif (!$user_not_found) {
    // =========================================================================
    // DATA FETCHING: GLOBAL OVERVIEW & LEADERBOARD
    // =========================================================================
    // Top 25 Explorers
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
            $u = BoincUser::lookup_id((int)$row['user_id']);
            $is_valid = is_valid_boinc_user($u);
            $row['is_valid'] = $is_valid;
            $row['user_name'] = $u ? $u->name : "User #" . $row['user_id'];
            if ($logged_in_user) {
                $row['user_html'] = $is_valid ? user_links($u, BADGE_HEIGHT_SMALL) : ("User #" . $row['user_id']);
            } else {
                $row['user_html'] = htmlspecialchars($row['user_name']);
            }
            $top_explorers[] = $row;
        }
        $res->free();
    }

    $page_title = tra("Deal Registry");
} else {
    $page_title = tra("Volunteer Not Found");
}

page_head($page_title);
?>
<style>
.deals-page {
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
.deals-page * { box-sizing: border-box; }
.deals-page .eyebrow { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--gold); font-weight: 700; margin: 0 0 12px; }
.deals-page h1 { font-family: Georgia, 'Iowan Old Style', 'Palatino Linotype', serif; font-size: 34px; line-height: 1.2; margin: 0 0 16px; color: var(--cream); }
.deals-page .lede { font-size: 15.5px; line-height: 1.65; color: var(--cream-dim); max-width: 72ch; margin: 0 0 24px; }
.deals-page h2.section-title { font-family: Georgia, serif; font-size: 20px; margin: 0 0 4px; color: var(--cream); }
.deals-page p.section-sub { font-size: 13.5px; color: var(--cream-dim); margin: 0 0 20px; line-height: 1.55; }

/* Top search bar */
.deals-search-bar {
    display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 32px;
    background: var(--felt-2); border: 1px solid var(--felt-line);
    border-radius: 10px; padding: 8px; max-width: 600px;
}
.deals-search-bar input {
    flex: 1; min-width: 200px; background: var(--felt); border: 1px solid var(--felt-line);
    border-radius: 6px; padding: 8px 14px; font-size: 14px; color: var(--cream);
}
.deals-search-bar input:focus { outline: none; border-color: var(--gold); }
.deals-search-bar button {
    background: var(--gold); color: var(--felt); border: none;
    border-radius: 6px; padding: 8px 18px; font-weight: 600; font-size: 13px; cursor: pointer;
}
.deals-search-bar button:hover { background: #dcb336; }
.deals-search-bar a.all-link {
    display: inline-flex; align-items: center; color: var(--cream-dim);
    font-size: 13px; padding: 0 10px; text-decoration: none;
}
.deals-search-bar a.all-link:hover { color: var(--gold); }

/* Hero Banner */
.deals-page .hero {
    border: 1px solid var(--gold-dim); border-radius: 16px;
    background: linear-gradient(180deg, var(--felt-2), var(--felt));
    padding: 36px 40px 32px; margin: 24px 0 36px; text-align: center;
}
.deals-page .hero-label { font-size: 13px; letter-spacing: .08em; text-transform: uppercase; color: var(--cream-dim); margin: 0 0 10px; font-weight: 600; }
.deals-page .hero-number { font-family: Georgia, serif; font-size: 64px; line-height: 1.1; color: var(--gold); margin: 0 0 8px; font-variant-numeric: tabular-nums; word-break: break-all; }
.deals-page .hero-sub { font-size: 14.5px; color: var(--cream-dim); margin: 0; }

/* Stat grid */
.deals-page .stat-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 2px;
    background: var(--felt-line); border: 1px solid var(--felt-line);
    border-radius: 14px; overflow: hidden; margin-bottom: 36px;
}
.deals-page .stat-tile { background: var(--felt-2); padding: 22px 20px; }
.deals-page .stat-value { font-family: Georgia, serif; font-size: 28px; color: var(--gold); font-variant-numeric: tabular-nums; margin: 0 0 6px; }
.deals-page .stat-label { font-size: 12.5px; color: var(--cream-dim); line-height: 1.4; }
.deals-page .stat-sub { font-size: 11.5px; color: var(--cream-dim); opacity: 0.8; margin-top: 4px; }

/* Box card */
.deals-card {
    border: 1px solid var(--gold-dim); border-radius: 14px; padding: 28px;
    background: var(--felt-2); margin-bottom: 32px;
}

/* Badges */
.role-badge {
    display: inline-block; font-size: 11px; font-weight: 700; letter-spacing: .05em;
    text-transform: uppercase; padding: 3px 8px; border-radius: 4px;
}
.role-explorer { background: var(--gold); color: var(--felt); }
.role-verifier { background: var(--cream-dim); color: var(--felt); }
.loop-badge { background: var(--ruby); color: var(--cream); font-weight: 700; padding: 2px 6px; border-radius: 4px; font-size: 11px; }

/* Tables */
.deals-table-wrap { overflow-x: auto; margin-top: 12px; }
.deals-table { width: 100%; border-collapse: collapse; font-size: 13.5px; text-align: left; }
.deals-table th {
    background: var(--felt-line); color: var(--cream); font-weight: 600;
    padding: 12px 14px; border-bottom: 2px solid var(--gold-dim);
}
.deals-table td {
    padding: 12px 14px; border-bottom: 1px solid var(--felt-line);
    color: var(--cream); vertical-align: middle;
}
.deals-table tr:hover td { background: rgba(201, 162, 39, 0.05); }
.deals-table td a { color: var(--gold); text-decoration: none; }
.deals-table td a:hover { text-decoration: underline; }
.deals-table .mono { font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 12px; }

.deals-meta-link {
    font-size: 12.5px; color: var(--cream-dim); margin-top: 8px; display: inline-block;
}
.deals-meta-link a { color: var(--gold); text-decoration: none; }
.deals-meta-link a:hover { text-decoration: underline; }
</style>

<div class="deals-page">

  <?php if ($logged_in_user): ?>
  <!-- Search & Navigation Bar (only visible to logged-in users) -->
  <form method="GET" action="deals.php" class="deals-search-bar">
    <input type="text" name="q" placeholder="<?php echo tra("Search volunteer by username or ID"); ?>" value="<?php echo htmlspecialchars($search_query ?? ''); ?>">
    <button type="submit"><?php echo tra("Search"); ?></button>
    <?php if ($target_user || $user_not_found): ?>
    <a href="deals.php?all=1" class="all-link"><?php echo tra("Deal Registry"); ?></a>
    <?php endif; ?>
    <?php if (!$target_user || $target_user->id !== $logged_in_user->id): ?>
    <a href="deals.php" class="all-link"><?php echo tra("My Deals"); ?></a>
    <?php endif; ?>
  </form>
  <?php endif; ?>

<?php if ($user_not_found): ?>
  <!-- ========================================================================= -->
  <!-- VIEW: VOLUNTEER NOT FOUND / DELETED                                       -->
  <!-- ========================================================================= -->
  <div style="background: rgba(168, 51, 73, 0.15); border: 1px solid var(--ruby); border-radius: 12px; padding: 36px 28px; text-align: center; margin: 40px auto; max-width: 580px;">
    <h2 style="color: var(--cream); font-family: Georgia, serif; font-size: 24px; margin: 0 0 12px;"><?php echo tra("Volunteer Not Found"); ?></h2>
    <p style="color: var(--cream-dim); font-size: 15px; margin: 0 0 24px; line-height: 1.6;">
      <?php echo tra("This user doesn't exist or has been deleted."); ?>
    </p>
    <a href="deals.php?all=1" style="display:inline-block; background: var(--gold); color: var(--felt); font-weight: 600; font-size: 13.5px; padding: 10px 20px; border-radius: 6px; text-decoration: none;">
      &larr; <?php echo tra("Return to Deal Registry"); ?>
    </a>
  </div>

<?php elseif ($target_user): ?>
  <!-- ========================================================================= -->
  <!-- VIEW: INDIVIDUAL VOLUNTEER DEALS                                          -->
  <!-- ========================================================================= -->
  <p class="eyebrow"><?php echo tra("Volunteer Deals"); ?></p>
  <h1><?php echo user_links($target_user, BADGE_HEIGHT_MEDIUM); ?></h1>
  <p class="lede">
    <?php echo tra("Deals for volunteer #%1.", $target_user->id); ?>
  </p>

  <section class="hero">
    <p class="hero-label"><?php echo tra("Deals Explored & Verified"); ?></p>
    <p class="hero-number"><?php echo number_format($total_deals); ?></p>
    <p class="hero-sub">
      <?php echo tra("%1% of all possible 52-card deals (6.535x10^20)", $cosmic_pct); ?>
    </p>
  </section>

  <div class="stat-grid">
    <div class="stat-tile">
      <p class="stat-value"><?php echo number_format($total_ranges); ?></p>
      <p class="stat-label"><?php echo tra("verified deal blocks"); ?></p>
      <p class="stat-sub"><?php echo tra("%1 Explorer &middot; %2 Verifier", number_format($exp_ranges), number_format($ver_ranges)); ?></p>
    </div>
    <div class="stat-tile">
      <p class="stat-value"><?php echo $personal_max_cards > 0 ? number_format($personal_max_cards) : "&mdash;"; ?></p>
      <p class="stat-label"><?php echo tra("cards in personal longest game"); ?></p>
      <p class="stat-sub"><?php echo tra("uncovered across all assigned blocks"); ?></p>
    </div>
    <div class="stat-tile">
      <p class="stat-value"><?php echo number_format($total_loops); ?></p>
      <p class="stat-label"><?php echo tra("infinite loops discovered"); ?></p>
      <p class="stat-sub"><?php echo tra("rare non-terminating deals"); ?></p>
    </div>
    <div class="stat-tile">
      <p class="stat-value" style="font-size:22px;margin-top:4px">
        <?php echo $first_ts > 0 ? date('d/m/Y', $first_ts) : "&mdash;"; ?>
      </p>
      <p class="stat-label"><?php echo tra("first block completed"); ?></p>
      <p class="stat-sub">
        <?php echo $last_ts > 0 ? tra("last active: %1", date('d/m/Y', $last_ts)) : ""; ?>
      </p>
    </div>
  </div>

  <?php if ($best_game): ?>
  <section class="deals-card">
    <h2 class="section-title"><?php echo tra("Personal Champion Game"); ?></h2>
    <p class="section-sub"><?php echo tra("The longest finite game found in any range verified by this volunteer"); ?></p>
    <div style="display:flex;gap:24px;flex-wrap:wrap;align-items:baseline;margin:16px 0">
      <div>
        <span style="font-family:Georgia,serif;font-size:32px;color:var(--gold);font-weight:700"><?php echo number_format($best_game['max_cards']); ?></span>
        <span style="color:var(--cream-dim);font-size:15px"> <?php echo tra("cards played"); ?></span>
      </div>
      <div>
        <span style="font-family:Georgia,serif;font-size:24px;color:var(--cream);font-weight:700"><?php echo number_format($best_game['max_tricks']); ?></span>
        <span style="color:var(--cream-dim);font-size:15px"> <?php echo tra("tricks"); ?></span>
      </div>
      <div style="color:var(--cream-dim);font-size:13.5px">
        <?php echo tra("Confirmed on %1", date('d/m/Y', $best_game['assimilated_at'])); ?>
      </div>
    </div>
    <p style="font-size:13px;color:var(--cream-dim);margin:0">
      <?php echo tra("Deal index:"); ?> <span class="mono" style="color:var(--cream)"><?php echo htmlspecialchars($best_game['best_deal_index']); ?></span>
    </p>
  </section>
  <?php endif; ?>

  <section class="deals-card">
    <h2 class="section-title"><?php echo tra("Recent Verified Blocks"); ?></h2>
    <p class="section-sub"><?php echo tra("Latest verified blocks of deals assimilated for this volunteer"); ?></p>

    <?php if (count($recent_ranges) > 0): ?>
    <div class="deals-table-wrap">
      <table class="deals-table">
        <thead>
          <tr>
            <th><?php echo tra("Role"); ?></th>
            <th><?php echo tra("Permutation Range"); ?></th>
            <th><?php echo tra("Longest Game in Block"); ?></th>
            <th><?php echo tra("Loops"); ?></th>
            <th><?php echo tra("Verification Partner"); ?></th>
            <th><?php echo tra("Confirmed"); ?></th>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($recent_ranges as $r): ?>
          <tr>
            <td>
              <?php if ($r['is_explorer']): ?>
              <span class="role-badge role-explorer"><?php echo tra("Explorer"); ?></span>
              <?php else: ?>
              <span class="role-badge role-verifier"><?php echo tra("Verifier"); ?></span>
              <?php endif; ?>
            </td>
            <td>
              <span class="mono"><?php echo htmlspecialchars($r['range_start']); ?></span> &rarr;
              <span class="mono"><?php echo htmlspecialchars($r['range_end']); ?></span>
            </td>
            <td>
              <b><?php echo number_format($r['max_cards']); ?></b> <?php echo tra("cards"); ?>
              <span style="color:var(--cream-dim);font-size:12px">(<?php echo number_format($r['max_tricks']); ?> <?php echo tra("tricks"); ?>)</span>
            </td>
            <td>
              <?php if ((int)$r['loops_count'] > 0): ?>
              <span class="loop-badge"><?php echo $r['loops_count']; ?> <?php echo tra("loop"); ?></span>
              <?php else: ?>
              <span style="color:var(--cream-dim)">&mdash;</span>
              <?php endif; ?>
            </td>
            <td>
              <?php if ($r['partner_valid']): ?>
              <?php echo user_links($r['partner_user'], BADGE_HEIGHT_SMALL); ?>
              <?php elseif ($r['partner_id'] > 0): ?>
              <span style="color:var(--cream-dim)"><?php echo tra("User #%1", $r['partner_id']); ?></span>
              <?php else: ?>
              <span style="color:var(--cream-dim)">&mdash;</span>
              <?php endif; ?>
            </td>
            <td>
              <?php echo date('d/m/Y H:i', (int)$r['assimilated_at']); ?>
            </td>
          </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
    <?php else: ?>
    <p style="color:var(--cream-dim);margin:16px 0">
      <?php echo tra("No confirmed blocks recorded for this volunteer yet. Once workunits are validated and assimilated, their completed ranges will appear here."); ?>
    </p>
    <?php endif; ?>
  </section>

<?php else: ?>
  <!-- ========================================================================= -->
  <!-- VIEW: GLOBAL OVERVIEW & LEADERBOARD                                       -->
  <!-- ========================================================================= -->
  <p class="eyebrow"><?php echo tra("Camicia &middot; Master Deal Registry"); ?></p>
  <h1><?php echo tra("The Deal Registry"); ?></h1>
  <p class="lede">
    <?php echo tra("Every permutation of the 52-card deck simulated by Camicia is permanently recorded and credited to the volunteers who explored and validated it."); ?>
  </p>

  <section class="deals-card">
    <h2 class="section-title"><?php echo tra("Top Deal Explorers"); ?></h2>
    <p class="section-sub">
      <?php echo tra("Volunteers ranked by number of confirmed deal blocks"); ?> &middot;
      <a href="progress.php" style="color:var(--gold);text-decoration:none;font-weight:600"><?php echo tra("View global search progress &rarr;"); ?></a>
    </p>

    <?php if (count($top_explorers) > 0): ?>
    <div class="deals-table-wrap">
      <table class="deals-table">
        <thead>
          <tr>
            <th style="width:60px">#</th>
            <th><?php echo tra("Volunteer"); ?></th>
            <th><?php echo tra("Blocks Verified"); ?></th>
            <th><?php echo tra("Deals Simulated"); ?></th>
            <th><?php echo tra("Personal Best"); ?></th>
            <th><?php echo tra("Loops"); ?></th>
            <?php if ($logged_in_user): ?>
            <th><?php echo tra("Last Active"); ?></th>
            <th></th>
            <?php endif; ?>
          </tr>
        </thead>
        <tbody>
          <?php $rank = 1; foreach ($top_explorers as $exp): ?>
          <tr>
            <td><b style="color:var(--gold)">#<?php echo $rank++; ?></b></td>
            <td><b><?php echo $exp['user_html']; ?></b></td>
            <td><?php echo number_format($exp['total_ranges']); ?></td>
            <td><?php echo number_format($exp['total_deals']); ?></td>
            <td>
              <?php if ((int)$exp['top_cards'] > 0): ?>
              <b><?php echo number_format($exp['top_cards']); ?></b> <?php echo tra("cards"); ?>
              <?php else: ?>
              &mdash;
              <?php endif; ?>
            </td>
            <td>
              <?php if ((int)$exp['total_loops'] > 0): ?>
              <span class="loop-badge"><?php echo $exp['total_loops']; ?></span>
              <?php else: ?>
              &mdash;
              <?php endif; ?>
            </td>
            <?php if ($logged_in_user): ?>
            <td><?php echo date('d/m/Y', (int)$exp['last_seen']); ?></td>
            <td>
              <?php if ($exp['is_valid']): ?>
              <a href="deals.php?userid=<?php echo $exp['user_id']; ?>" style="background:var(--gold);color:var(--felt);padding:4px 10px;border-radius:4px;font-size:12px;font-weight:600;text-decoration:none">
                <?php echo tra("Deals &rarr;"); ?>
              </a>
              <?php endif; ?>
            </td>
            <?php endif; ?>
          </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
    <?php else: ?>
    <p style="color:var(--cream-dim);margin:16px 0">
      <?php echo tra("No completed blocks recorded yet. Once workunits are processed, leading explorers will appear here."); ?>
    </p>
    <?php endif; ?>
  </section>
<?php endif; ?>

</div>

<?php
page_tail();
?>
