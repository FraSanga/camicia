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

// Camicia: Beta Tester badge -- one-off, permanent, hand-curated.
//
// Unlike every other badge script, this one has no algorithmic
// eligibility rule: "beta tester" means "a real person who actually
// helped test on staging before the public launch", which can't be
// inferred from any DB field -- there's no way to tell a genuine
// tester's account from an internal test account by querying alone.
//
// The curated list is real people's email addresses -- PII that must
// never enter this repo's git history. Camicia/camicia is public, and
// git history is effectively permanent and unredactable once pushed
// (the exact reason this project has had to scrub other sensitive
// commits out of its own history before). So the list lives ONLY on
// each server's local disk, deployed by hand (same as
// badge_discovery_state.json/badge_founder_state.json -- runtime state
// this repo never tracks), never through the normal git-based deploy
// pipeline. This script itself contains zero real personal data and is
// safe to be public; only the plain-text file it reads is sensitive,
// and that file is never committed anywhere.
//
// Matches by email_addr against whichever environment this runs in, and
// additionally requires email_validated=1 (same real-person-behind-the-
// address check the founder badge uses) before awarding -- registering
// with a matching address isn't enough on its own. Most testers won't
// have a production account yet even right after launch (they tested on
// staging, not prod), so this can't be a one-time import: it keeps
// re-checking every day, badging anyone in the file the moment they show
// up here with a verified email, and leaving everyone else (unregistered
// or unverified) pending indefinitely, however long that takes.

require_once("../inc/util_ops.inc");

// One email per line, '#'-prefixed lines and blank lines ignored. Not
// git-tracked -- deploy by hand, see comment above. Missing file is a
// normal, expected state (nothing curated yet), not an error.
$beta_testers_path = "../../beta_testers.txt";

echo "Starting: ", time_str(time()), "\n";

if (!file_exists($beta_testers_path)) {
    echo "No beta_testers.txt on this server -- nothing curated yet, nothing to do.\n";
    echo "Finished: ", time_str(time()), "\n";
    exit();
}

$lines = file($beta_testers_path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
$emails = array();
foreach ($lines as $line) {
    $line = trim($line);
    if ($line === '' || $line[0] === '#') continue;
    $emails[] = $line;
}

if (empty($emails)) {
    echo "beta_testers.txt exists but has no emails yet -- nothing to do.\n";
    echo "Finished: ", time_str(time()), "\n";
    exit();
}

$badge = get_badge("beta_tester", "Beta Tester", "beta_tester.png");

foreach ($emails as $email) {
    $user = BoincUser::lookup("email_addr='".BoincDb::escape_string($email)."'");
    if (!$user) {
        echo "Beta tester $email hasn't registered here yet -- still pending\n";
        continue;
    }
    if (!$user->email_validated) {
        echo "Beta tester $email has registered but not yet verified their email -- still pending\n";
        continue;
    }
    assign_badge(true, $user, $badge);
    echo "Awarded Beta Tester to $user->name (ID $user->id), $email\n";
}

echo "Finished: ", time_str(time()), "\n";

?>
