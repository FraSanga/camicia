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

// Camicia: longevity badges -- account age tiers, gated on recent activity.
//
// Unlike badge_assign_credit.php's total_credit ladder (permanent milestones,
// total_credit never decreases), this one is NOT permanent: expavg_credit>1
// is the same "recently active" threshold stock BOINC already uses elsewhere
// (e.g. default_uotd_candidates_query()) and it decays if you stop
// crunching, so this badge rewards *sustained* participation, not just an
// old dormant account -- go inactive long enough and you lose it, same as
// badge_assign.php's RAC-percentile badges. Age only ever grows, but the
// activity gate is re-checked every run.

require_once("../inc/util_ops.inc");

$badge_levels = array(30, 90, 180, 365, 730);  // days
$badge_level_names = array(
    "newcomer", "regular", "devoted", "veteran", "legend"
);
$badge_images = array_map(function($n) { return "longevity_$n.png"; }, $badge_level_names);

function get_longevity_badges($badge_level_names, $badge_images) {
    $badges = array();
    $n = count($badge_level_names);
    for ($i = 0; $i < $n; $i++) {
        $badges[$i] = get_badge(
            "longevity_".$badge_level_names[$i],
            ucfirst($badge_level_names[$i])." (longevity)",
            $badge_images[$i]
        );
    }
    return $badges;
}

// decide which tier to assign, if any -- highest level first.
// No activity (expavg_credit<=1) means no badge at all, regardless of age.
//
function assign_longevity_badge($is_user, $item, $levels, $badges) {
    if ($item->expavg_credit <= 1) {
        unassign_badges($is_user, $item, $badges, -1);
        return;
    }
    $age_days = (time() - $item->create_time) / 86400;
    for ($i = count($levels) - 1; $i >= 0; $i--) {
        if ($age_days >= $levels[$i]) {
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
    $n = 0;
    $maxid = $is_user ? BoincUser::max("id") : BoincTeam::max("id");
    while ($n <= $maxid) {
        $m = $n + 1000;
        if ($is_user) {
            $items = BoincUser::enum_fields("id, create_time, expavg_credit", "id>=$n and id<$m");
        } else {
            $items = BoincTeam::enum_fields("id, create_time, expavg_credit", "id>=$n and id<$m");
        }
        foreach ($items as $item) {
            assign_longevity_badge($is_user, $item, $badge_levels, $badges);
        }
        $n = $m;
    }
}

echo "Starting: ", time_str(time()), "\n";

$badges = get_longevity_badges($badge_level_names, $badge_images);
assign_all(true, $badge_levels, $badges);
assign_all(false, $badge_levels, $badges);

echo "Finished: ", time_str(time()), "\n";

?>
