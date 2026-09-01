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

function credit_to_ops($credit, &$ops, &$unit) {
    // 100 units of credit is 86400*10^9 ops
    // 1 unit of credit is 864*10^9 ops
    $ops_trillion = ($credit*864)/1000;
    $unit = "trillion";
    $ops = $ops_trillion;
    if ($ops_trillion > 1000) {
        $ops = $ops_trillion/1000;
        $unit = "quadrillion";
    }
    if ($ops_trillion > 1000000) {
        $ops = $ops_trillion/1000000;
        $unit = "quintillion";
    }
    if ($ops_trillion > 1000000000) {
        $ops = $ops_trillion/1000000000;
        $unit = "sextillion";
    }
    $ops = number_format($ops, 2);
}

function credit_string($credit, $bolden) {
    $cobbs = number_format($credit, 0);

    credit_to_ops($credit, $ops, $unit);

    if ($bolden) {
        $lbold="[[";
        $rbold="]]";
    } else {
        $lbold="";        $rbold="";
    }
    return " $lbold$cobbs Cobblestones$rbold of computation ($ops $unit floating-point operations)";
}

// Camicia addition: a short code proving a certificate was genuinely
// issued by this project, without any database lookup at verification
// time -- see the header comment on CERT_VERIFY_SECRET in project.inc.
// Format: <user id in base36>-<8 hex chars of an HMAC over "cert:<id>">.
// The base36 half lets verify_cert.php recover the user id directly from
// the code itself (no need to also pass it separately in a link/QR); the
// HMAC half proves whoever printed this code knew CERT_VERIFY_SECRET.
// Deliberately NOT a signature over credit/name/date -- those change
// over time (credit keeps growing) and there's nowhere this project
// durably records "what a given certificate claimed at the moment it was
// issued", so verification instead means "this code really was issued by
// Camicia for this account", checked against that account's current,
// live stats -- consistent with the certificate itself always rendering
// current, live data rather than a frozen historical snapshot.
function cert_verify_code($user_id) {
    if (!defined('CERT_VERIFY_SECRET')) return null;
    $uid_b36 = strtoupper(base_convert($user_id, 10, 36));
    $check = strtoupper(substr(hash_hmac('sha256', "cert:$user_id", CERT_VERIFY_SECRET), 0, 8));
    return "$uid_b36-$check";
}

// Camicia addition: same scheme as cert_verify_code() above, for
// cert_team.php. Deliberately a different HMAC message ("cert_team:<id>"
// vs "cert:<id>") rather than a format marker (e.g. a leading "T") baked
// into the code string -- a marker would need its own escape from the
// base36 alphabet, which already uses every character a marker could be.
// Keying off the HMAC message instead means cert_verify_code_check() can
// try both and see which one (if either) actually matches, with no risk
// of an ambiguous or colliding code format, and zero change needed to
// already-issued user certificates' codes.
function cert_verify_code_team($team_id) {
    if (!defined('CERT_VERIFY_SECRET')) return null;
    $tid_b36 = strtoupper(base_convert($team_id, 10, 36));
    $check = strtoupper(substr(hash_hmac('sha256', "cert_team:$team_id", CERT_VERIFY_SECRET), 0, 8));
    return "$tid_b36-$check";
}

// Returns ['type' => 'user'|'team', 'id' => <int>] if the code is genuine
// (its HMAC matches either message), or null if it's malformed, matches
// neither, or CERT_VERIFY_SECRET isn't configured.
function cert_verify_code_check($code) {
    if (!defined('CERT_VERIFY_SECRET')) return null;
    $code = strtoupper(trim($code));
    $parts = explode('-', $code, 2);
    if (count($parts) != 2) return null;
    list($id_b36, $given_check) = $parts;
    if (!preg_match('/^[0-9A-Z]+$/', $id_b36) || !preg_match('/^[0-9A-F]{8}$/', $given_check)) {
        return null;
    }
    $id = (int)base_convert($id_b36, 36, 10);
    if ($id <= 0) return null;
    $expected_user_check = strtoupper(substr(hash_hmac('sha256', "cert:$id", CERT_VERIFY_SECRET), 0, 8));
    if (hash_equals($expected_user_check, $given_check)) {
        return array('type' => 'user', 'id' => $id);
    }
    $expected_team_check = strtoupper(substr(hash_hmac('sha256', "cert_team:$id", CERT_VERIFY_SECRET), 0, 8));
    if (hash_equals($expected_team_check, $given_check)) {
        return array('type' => 'team', 'id' => $id);
    }
    return null;
}

// show button to download certificate as PNG
// Camicia: restyled to match the certificate's own gold/felt palette
// (cert1.php's own <style> block defines .cbtn); still a plain <button>
// with no framework dependency, since these pages don't use bootstrap.
// Text-only (no icon) and hardcoded English, not tra() -- the whole
// certificate page is deliberately kept English-only regardless of
// visitor locale (it's a document that gets shared/posted outside the
// project, e.g. on social media, so its wording stays consistent no
// matter who's viewing it -- see cert1.php's own header comment).
//
// The share-to-Facebook/X buttons this used to sit next to were removed
// (kept adding friction/clutter to a page whose only real job is "prove
// I did this and let me save a copy" -- verify_cert.php already covers
// authenticity for anyone a certificate gets shared with).
//
function show_download_button() {
    echo '
        <div class="cert-controls">
        <button id="export" class="cbtn primary">Download as image</button>
        <script src="html2canvas.min.js"></script>
        <script type="text/javascript">
            const $btn = document.querySelector("#export");
            const $toExport = document.querySelector("#certificate");

            $btn.addEventListener("click", () => {
                html2canvas($toExport)
                .then(canvas => {
                    var dataURL = canvas.toDataURL("image/png");
                    downloadImage(dataURL, "camicia_certificate.png");
                });
            });

            function downloadImage(data, filename = "untitled.png") {
                var a = document.createElement("a");
                a.href = data;
                a.download = filename;
                a.click();
            }
        </script>
    ';
}
?>
