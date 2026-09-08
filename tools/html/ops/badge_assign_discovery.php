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

// Camicia: discovery badges -- Loop Finder and Longest Game.
//
// Both are one-off achievement badges: get_badge() once, assign_badge()
// on a match, and -- unlike the credit/longevity ladders -- NEVER
// unassign_badges(). These are permanent; once earned, always kept.
//
// records_loops.txt (assimilator.cpp's record_loop_found()) is
// append-only -- every loop ever found stays as its own line forever --
// so this is a resumable line-count cursor: every distinct finder gets
// the badge, no threshold beyond "found one, ever."
//
// records_longest.txt (maybe_update_longest_record()) is the OPPOSITE:
// it's overwritten in place every time (fopen "w" + rename), so it only
// ever holds the single current best -- there's no history to scan. The
// real gate here isn't "beat Camicia's own last record", it's "beat the
// real world's": the highest documented finite Beggar-My-Neighbour game
// is Nessler 2022, 8344 cards (tests/test_data_gen.hpp). Camicia's own
// local search will climb through many small internal bests long before
// (if ever) actually crossing that, so this script just tracks one
// scalar (the last cards value already evaluated) to avoid re-processing
// the same standing record every single day.
//
// Both attribute wu_name -> user via
// workunit.canonical_resultid -> result.hostid -> host.userid, which can
// legitimately fail (db_purge deletes old workunit/result rows after
// ~7 days; a host or account can later be deleted) -- skipped and
// logged, never fatal.

require_once("../inc/util_ops.inc");

// Real-world record as of this writing: Nessler 2022, 8344 cards / 1164
// tricks (tests/test_data_gen.hpp). Update by hand if that file ever
// gains a longer documented finite game.
define('REAL_WORLD_RECORD_CARDS', 8344);

$state_path = "../../badge_discovery_state.json";

function load_state($path) {
    $default = array('loop_lines_processed' => 0, 'longest_last_cards' => -1);
    if (!file_exists($path)) return $default;
    $j = json_decode(file_get_contents($path), true);
    if (!is_array($j)) return $default;
    return array_merge($default, $j);
}

function save_state($path, $state) {
    file_put_contents($path, json_encode($state));
}

// wu_name -> BoincUser, or null if unattributable for any reason
// (purged workunit/result, deleted host, deleted account).
//
function attribute_wu_to_user($wu_name) {
    $wu = BoincWorkunit::lookup("name='".BoincDb::escape_string($wu_name)."'");
    if (!$wu || !$wu->canonical_resultid) return null;
    $result = BoincResult::lookup_id($wu->canonical_resultid);
    if (!$result) return null;
    $host = BoincHost::lookup_id($result->hostid);
    if (!$host) return null;
    return BoincUser::lookup_id($host->userid);
}

function process_loops($badge, &$state) {
    $path = "../../records_loops.txt";
    if (!file_exists($path)) return;
    $lines = file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    $n = count($lines);
    $start = $state['loop_lines_processed'];
    if ($start >= $n) return;
    for ($i = $start; $i < $n; $i++) {
        $parts = preg_split('/\s+/', trim($lines[$i]));
        if (count($parts) < 2) continue;
        $wu_name = $parts[1];
        $user = attribute_wu_to_user($wu_name);
        if (!$user) {
            echo "Loop find in $wu_name: can't attribute to a user (purged or deleted) -- skipping\n";
            continue;
        }
        assign_badge(true, $user, $badge);
        echo "Awarded Loop Finder to $user->name (ID $user->id), wu $wu_name\n";
    }
    $state['loop_lines_processed'] = $n;
}

function process_longest($badge, &$state) {
    $path = "../../records_longest.txt";
    if (!file_exists($path)) return;
    $line = trim(@file_get_contents($path));
    if (!$line) return;
    $parts = preg_split('/\s+/', $line);
    if (count($parts) < 4) return;
    $cards = (float)$parts[0];
    $wu_name = $parts[3];

    if ($cards <= $state['longest_last_cards']) return;  // already evaluated
    $state['longest_last_cards'] = $cards;  // mark evaluated either way -- avoid a retry loop

    if ($cards <= REAL_WORLD_RECORD_CARDS) return;  // not a real-world record (yet)

    $user = attribute_wu_to_user($wu_name);
    if (!$user) {
        echo "New real-world record ($cards cards, wu $wu_name) but can't attribute to a user -- skipping\n";
        return;
    }
    assign_badge(true, $user, $badge);
    echo "Awarded Longest Game to $user->name (ID $user->id): $cards cards, wu $wu_name\n";
}

echo "Starting: ", time_str(time()), "\n";

$state = load_state($state_path);

$loop_badge = get_badge("discovery_loop", "Loop Finder", "discovery_loop.png");
$longest_badge = get_badge("discovery_longest", "Longest Game", "discovery_longest.png");

process_loops($loop_badge, $state);
process_longest($longest_badge, $state);

save_state($state_path, $state);

echo "Finished: ", time_str(time()), "\n";

?>
