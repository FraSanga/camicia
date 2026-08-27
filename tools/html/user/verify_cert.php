<?php
// Looks up a certificate verification code printed on cert1.php (see
// html/inc/cert.inc's cert_verify_code()/cert_verify_code_check()) and
// reports whether it was genuinely issued by this project. Pure crypto
// check against CERT_VERIFY_SECRET plus a live lookup of the account's
// current stats -- there's no stored record of individual certificates,
// so this can't reproduce exactly what a specific downloaded image
// claimed at the moment it was issued, only that the code itself is real
// and (if the account still exists) what that account looks like now.

require_once('../inc/util.inc');
require_once('../inc/translation.inc');
require_once('../inc/cert.inc');

check_get_args(array('code'));

page_head(tra("Verify a certificate"));

$code = get_str('code', true);

echo "<p>".tra("Camicia certificates carry a short verification code so anyone can confirm one was genuinely issued by this project, without needing any special tools.")."</p>";

if (!$code) {
    echo "
    <form method=\"get\" action=\"verify_cert.php\">
    <p>".tra("Enter the certificate number shown on the certificate (for example, %1):", "<code>3F-A1B2C3D4</code>")."</p>
    <input type=\"text\" name=\"code\" size=\"20\" placeholder=\"XX-XXXXXXXX\" style=\"font-family:monospace\">
    <input type=\"submit\" value=\"".tra("Check")."\">
    </form>
    ";
} else {
    $code_display = htmlspecialchars($code);
    if (!defined('CERT_VERIFY_SECRET')) {
        echo "<p>".tra("Certificate verification isn't configured on this project.")."</p>";
    } else {
        $user_id = cert_verify_code_check($code);
        if ($user_id === null) {
            echo "
            <div style=\"border:1px solid #a83349; border-radius:8px; padding:16px 20px; background:#fdf0f2\">
            <b>&#10060; ".tra("Not a valid Camicia certificate number.")."</b>
            <p>".tra("\"%1\" doesn't match a certificate this project ever issued.", $code_display)."</p>
            </div>
            ";
        } else {
            $cert_user = BoincUser::lookup_id($user_id);
            if (!$cert_user) {
                echo "
                <div style=\"border:1px solid #c9a227; border-radius:8px; padding:16px 20px; background:#fdf8ea\">
                <b>&#9989; ".tra("This is a genuine Camicia certificate number.")."</b>
                <p>".tra("The account it was issued to has since been deleted, so its details can no longer be shown -- but the code itself is real.")."</p>
                </div>
                ";
            } else {
                $join = gmdate('j F Y', $cert_user->create_time);
                $credit_display = number_format($cert_user->total_credit, 0);
                $name_html = htmlspecialchars($cert_user->name);
                echo "
                <div style=\"border:1px solid #16302a; border-radius:8px; padding:16px 20px; background:#eef6f0\">
                <b>&#9989; ".tra("Genuine Camicia certificate.")."</b>
                <p>".tra("Issued to %1, a participant since %2 with %3 units of credit contributed as of today.", "<b>$name_html</b>", $join, "<b>$credit_display</b>")."</p>
                </div>
                ";
            }
        }
    }
}

page_tail();
?>
