<?php
// This file is part of BOINC.
// http://boinc.berkeley.edu
// Copyright (C) 2023 University of California
//
// BOINC is free software; you can redistribute it and/or modify it
// under the terms of the GNU Lesser General Public License
// as published by the Free Software Foundation,
// either version 3 of the License, or (at your option) any later version.
//
// BOINC is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
// See the GNU Lesser General Public License for more details.
//
// You should have received a copy of the GNU Lesser General Public License
// along with BOINC.  If not, see <http://www.gnu.org/licenses/>.

// show certificate for a team: creation date, total credit
// Projects can customize this:
// https://github.com/BOINC/boinc/wiki/Web-configuration-file#certificate-related-constants
//
// Camicia: restyled to match cert1.php's own felt/gold/cream identity
// (same template, team-appropriate wording) instead of BOINC's plain
// font-tag default, and added a verification code -- see
// html/inc/cert.inc's cert_verify_code_team() for how/why. Also drops the
// call to show_share_buttons(), which upstream's version still made: that
// function was removed project-wide when cert1.php was restyled (see
// cert.inc's own comment), leaving this page fatally erroring on every
// load for any user whose account has a team -- confirmed live before
// this fix, not just suspected.
//
// Deliberately English-only (no tra() on the certificate text), same
// reasoning as cert1.php's own header comment: this is a document meant
// to be downloaded and shared outside the project, not a normal
// translated site page.

require_once("../inc/util.inc");
require_once("../inc/translation.inc");
require_once("../inc/cert.inc");

if (DISABLE_TEAMS) error_page("Teams are disabled");

$border = get_str("border", true);
$show_border = ($border != "no");

// Same convention as upstream and as cert1.php: the URL carries a user_id
// (not a team_id) so a copy-pasted link resolves to that specific user's
// team rather than silently redirecting to whoever's currently logged
// in -- user.inc's own "Team" certificate link relies on this exact
// fallback (bare cert_team.php, no query string) for the common case.
//
$user_id = get_int('user_id', true);
if (!$user_id) {
    $user = get_logged_in_user();
    Header(sprintf('Location: %s/cert_team.php?user_id=%d%s',
        url_base(), $user->id, $show_border?'':'&border=no'
    ));
    exit;
}
$user = BoincUser::lookup_id($user_id);
if (!$user) {
    error_page(tra("No such account."));
}
$team = BoincTeam::lookup_id($user->teamid);
if (!$team) {
    error_page(tra("This account is not a member of a team."));
}

$join = gmdate('j F Y', $team->create_time);
$today = gmdate('j F Y', time());
$credit_display = number_format($team->total_credit, 0);

$verify_code = cert_verify_code_team($team->id);
$name_html = htmlspecialchars($team->name);
?>
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Certificate of Computation</title>
<style>
:root {
    --felt: #0f1f1a; --felt-2: #16302a; --felt-line: #1d3a32;
    --gold: #c9a227; --gold-dim: #8c7a3e;
    --parchment: #ede3cb; --cream-dim: #b7ae95;
    --ink: #16302a;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    background: var(--felt);
    background-image:
        repeating-linear-gradient(45deg, var(--felt-line) 0, var(--felt-line) 1px, transparent 1px, transparent 48px),
        repeating-linear-gradient(-45deg, var(--felt-line) 0, var(--felt-line) 1px, transparent 1px, transparent 48px);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    padding: 48px 20px 64px;
}
.cert-wrap { max-width: 900px; margin: 0 auto; }
#certificate {
    background: var(--parchment);
    background-image: radial-gradient(ellipse at top left, #f2e9d3, var(--parchment) 60%);
    color: var(--ink);
    border-radius: 4px;
    <?php if ($show_border): ?>box-shadow: 0 30px 60px -20px rgba(0,0,0,.55);<?php endif; ?>
    padding: 14px;
    position: relative;
}
.cert-inner {
    <?php if ($show_border): ?>border: 2px solid var(--gold);<?php endif; ?>
    border-radius: 2px;
    padding: 48px 56px 40px;
    position: relative;
}
<?php if ($show_border): ?>
.cert-inner::before {
    content: "";
    position: absolute; inset: 8px;
    border: 1px solid var(--gold-dim);
    pointer-events: none;
}
.corner { position: absolute; width: 26px; height: 26px; border: 2px solid var(--gold); }
.corner.tl { top: 2px; left: 2px; border-right: none; border-bottom: none; }
.corner.tr { top: 2px; right: 2px; border-left: none; border-bottom: none; }
.corner.bl { bottom: 2px; left: 2px; border-right: none; border-top: none; }
.corner.br { bottom: 2px; right: 2px; border-left: none; border-top: none; }
<?php endif; ?>
.seal { display: flex; justify-content: center; margin-bottom: 18px; }
.seal svg { width: 46px; height: 46px; }
.cert-title {
    font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
    font-size: 40px; text-align: center; margin: 0 0 6px; color: var(--ink);
}
.cert-subtitle {
    text-align: center; font-size: 12.5px; letter-spacing: .14em;
    text-transform: uppercase; color: var(--gold-dim); font-weight: 600;
    margin: 0 0 34px;
}
.cert-lede { text-align: center; font-size: 15px; color: var(--ink); opacity: .8; margin: 0 0 6px; }
.cert-name-row { text-align: center; }
.cert-name {
    font-family: Georgia, serif; font-size: 30px; color: var(--ink);
    margin: 10px 0 24px; padding-bottom: 10px; border-bottom: 1px solid var(--gold-dim);
    display: inline-block; min-width: 60%;
}
.cert-body {
    text-align: center; font-size: 15px; line-height: 1.7; color: var(--ink);
    max-width: 56ch; margin: 0 auto 30px; opacity: .85;
}
.cert-body b { color: var(--ink); font-weight: 700; opacity: 1; }
.cert-footer { display: flex; justify-content: space-between; align-items: flex-end; margin-top: 10px; }
.cert-sig .name {
    font-family: Georgia, serif; font-style: italic; font-size: 20px; color: var(--ink);
    border-bottom: 1px solid var(--gold-dim); padding-bottom: 4px; margin-bottom: 4px; display: inline-block;
}
.cert-sig .role { font-size: 11.5px; color: var(--gold-dim); letter-spacing: .04em; }
.cert-date { text-align: right; }
.cert-date .date { font-family: Georgia, serif; font-size: 15px; color: var(--ink); }
.cert-date .label { font-size: 11px; color: var(--gold-dim); letter-spacing: .04em; text-transform: uppercase; margin-top: 3px; }
.cert-serial {
    text-align: center; margin-top: 26px; font-size: 10.5px; color: var(--gold-dim);
    letter-spacing: .06em; font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.cert-controls { display: flex; justify-content: center; gap: 10px; flex-wrap: wrap; margin-top: 28px; }
.cbtn {
    font: inherit; font-size: 13px; font-weight: 600; border-radius: 999px; padding: 10px 20px;
    cursor: pointer; display: inline-flex; align-items: center; gap: 8px;
    border: 1px solid var(--gold-dim); background: var(--felt-2); color: #ede3cb;
}
.cbtn:focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }
.cbtn.primary { background: var(--gold); color: var(--felt); border-color: var(--gold); }
</style>
</head>
<body>

<div class="cert-wrap">
<div id="certificate">
<div class="cert-inner">
<?php if ($show_border): ?>
    <div class="corner tl"></div><div class="corner tr"></div>
    <div class="corner bl"></div><div class="corner br"></div>
<?php endif; ?>

<div class="seal">
<svg viewBox="0 0 64 64" role="img" aria-label="<?php echo PROJECT; ?>">
  <rect x="2" y="2" width="60" height="60" rx="14" fill="#16302a" stroke="#8c7a3e" stroke-width="2.5"/>
  <path d="M 20,14 L 28,21 L 32,17.5 L 36,21 L 44,14 L 53,23 L 45,30 L 45,51 L 19,51 L 19,30 L 11,23 Z" fill="#ede3cb" stroke="#8c7a3e" stroke-width="1"/>
  <text x="32" y="26.5" text-anchor="middle" dominant-baseline="central" font-family="Georgia, serif" font-size="11" fill="#a83349">&#9830;</text>
  <circle cx="32" cy="36" r="2" fill="#c9a227"/>
  <circle cx="32" cy="45" r="2" fill="#c9a227"/>
</svg>
</div>

<p class="cert-title">Certificate of Computation</p>
<p class="cert-subtitle"><?php echo PROJECT; ?></p>

<p class="cert-lede">This certifies that team</p>
<div class="cert-name-row"><span class="cert-name"><?php echo $name_html; ?></span></div>

<p class="cert-body">
has contributed computing time to <?php echo PROJECT; ?>'s search since <?php echo $join; ?>, contributing <b><?php echo $credit_display; ?></b> units of credit toward the search.
</p>

<div class="cert-footer">
    <div class="cert-sig">
<?php if (defined("CERT_SIGNATURE")): ?>
        <img src="<?php echo CERT_SIGNATURE; ?>"><br>
<?php endif; ?>
<?php if (defined("CERT_DIRECTOR_NAME")): ?>
        <div class="name"><?php echo CERT_DIRECTOR_NAME; ?></div>
        <div class="role">Director, <?php echo PROJECT; ?></div>
<?php endif; ?>
    </div>
    <div class="cert-date">
        <div class="date"><?php echo $today; ?></div>
        <div class="label">Issued</div>
    </div>
</div>

<?php if ($verify_code): ?>
<p class="cert-serial">Certificate No. <?php echo $verify_code; ?></p>
<?php endif; ?>

</div>
</div>

<?php
show_download_button();
?>
</div>

</body>
</html>
