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

// Camicia: Founder badge -- a one-off, permanent badge for the first
// FOUNDER_COUNT real, *verified* users to register after this feature
// went live.
//
// Self-snapshotting floor: the first time this script ever runs in a
// given environment, it records the current max user.id as a floor and
// saves it to state -- nothing that existed before "now" ever qualifies,
// regardless of whether that's leftover pre-launch test data or (as
// planned here) a deliberate account wipe done before opening
// registration to the public. Every later run just re-checks "who's
// registered since the floor AND has a validated email
// (email_validated=1, set by validate_email_addr.php -- proof there's a
// real person behind the address, not a bot/spam signup), ordered by
// registration id, capped at FOUNDER_COUNT". There's no
// email-validated-*time* column, only the flag, so ranking is by
// original registration order among the validated pool -- someone can
// register early and validate late and still rank on their original id,
// they just don't count until they do validate. Monotonic and
// idempotent: re-running never un-awards anyone even as new users keep
// registering/validating past the cap. Users only: a "founding team"
// isn't really the same kind of personal milestone.

require_once("../inc/util_ops.inc");

define('FOUNDER_COUNT', 100);

$state_path = "../../badge_founder_state.json";

function load_state($path) {
    if (!file_exists($path)) return null;
    $j = json_decode(file_get_contents($path), true);
    if (!is_array($j) || !isset($j['floor_id'])) return null;
    return $j;
}

function save_state($path, $state) {
    file_put_contents($path, json_encode($state));
}

echo "Starting: ", time_str(time()), "\n";

$state = load_state($state_path);
if ($state === null) {
    $floor = (int)BoincUser::max("id");
    $state = array('floor_id' => $floor);
    save_state($state_path, $state);
    echo "First run: snapshotting floor_id=$floor -- no one before this qualifies\n";
}

$badge = get_badge("founder", "Founding Volunteer", "founder.png");

$users = BoincUser::enum_fields(
    "id, name",
    "id > ".$state['floor_id']." and email_validated=1 order by id limit ".FOUNDER_COUNT
);
foreach ($users as $u) {
    assign_badge(true, $u, $badge);
    echo "Awarded Founding Volunteer to $u->name (ID $u->id)\n";
}

echo "Finished: ", time_str(time()), "\n";

?>
