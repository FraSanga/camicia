#!/usr/bin/env php
<?php
// This file is part of BOINC.
// http://boinc.berkeley.edu
// Copyright (C) 2008 University of California
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

// Camicia fix: adapted from stock BOINC's badge_assign_custom.php template
// (per-subproject total-credit ladder), simplified to a single "total"
// ladder since this project has one app -- no <credit_by_app/>/$sub_projects
// wiring needed. Also replaces that template's own threshold values
// (50M..500B) -- checked against real-world BOINC credit data before
// picking these: PrimeGrid's own top individual users sit around ~1B
// (primegrid.fandom.com/wiki/Credit_and_Badges), and the single highest
// known credit total across the *entire* BOINC ecosystem, any project,
// years of continuous cross-project computing, is ~5.68B
// (einsteinathome.org/content/credits-earned-seem-high) -- so stock's own
// 250B/500B top tiers are roughly 50-100x beyond anything any volunteer
// has ever actually achieved, on any project, ever. These thresholds
// instead top out at 1B, matching the real, community-recognized
// "1 billion club" milestone, so the top tier is a genuine aspirational
// target rather than a mathematical impossibility.
//
// Permanent milestones by design (unlike badge_assign.php's RAC-percentile
// badges, already running): total_credit never decreases, so once a tier
// is earned it's kept forever -- only ever upgrades, never lost for
// taking a break from crunching.

require_once("../inc/util_ops.inc");

$badge_levels = array(
    100000, 500000, 2000000, 10000000, 50000000, 200000000, 500000000,
    1000000000
);
$badge_level_names = array(
    "bronze", "silver", "gold", "amethyst", "turquoise", "sapphire",
    "ruby", "emerald"
);
// images located in html/user/img/, credit_<name>.png
//
$badge_images = array_map(function($n) { return "credit_$n.png"; }, $badge_level_names);

function get_credit_badges($badge_level_names, $badge_images) {
    $badges = array();
    $n = count($badge_level_names);
    for ($i = 0; $i < $n; $i++) {
        $badges[$i] = get_badge(
            "credit_".$badge_level_names[$i],
            ucfirst($badge_level_names[$i])." tier (total credit)",
            $badge_images[$i]
        );
    }
    return $badges;
}

// decide which tier to assign, if any -- highest level first so the
// user/team gets their single best tier and every lower one is removed
//
function assign_credit_badge($is_user, $item, $levels, $badges) {
    for ($i = count($levels) - 1; $i >= 0; $i--) {
        if ($item->total_credit >= $levels[$i]) {
            assign_badge($is_user, $item, $badges[$i]);
            unassign_badges($is_user, $item, $badges, $i);
            return;
        }
    }
    unassign_badges($is_user, $item, $badges, -1);
}

// Scan through all the users/teams, 1000 at a time
//
function assign_all($is_user, $badge_levels, $badges) {
    $kind = $is_user ? "user" : "team";
    $n = 0;
    $maxid = $is_user ? BoincUser::max("id") : BoincTeam::max("id");
    while ($n <= $maxid) {
        $m = $n + 1000;
        if ($is_user) {
            $items = BoincUser::enum_fields("id, total_credit", "id>=$n and id<$m and total_credit>0");
        } else {
            $items = BoincTeam::enum_fields("id, total_credit", "id>=$n and id<$m and total_credit>0");
        }
        foreach ($items as $item) {
            assign_credit_badge($is_user, $item, $badge_levels, $badges);
        }
        $n = $m;
    }
}

echo "Starting: ", time_str(time()), "\n";

$badges = get_credit_badges($badge_level_names, $badge_images);
assign_all(true, $badge_levels, $badges);
assign_all(false, $badge_levels, $badges);

echo "Finished: ", time_str(time()), "\n";

?>
