<?php
// Discord Account Linking page
//
// Allows logged-in BOINC volunteers to connect their Discord account to
// their Camicia account. For security, a 6-digit verification PIN is sent
// to their registered email address (rate-limited to 1 request per 60 seconds).
// Once received, the volunteer submits `/link <pin>` to CamiciaBot on
// Discord to complete the link, receive the "Volunteer" server role, and unlock
// +2 daily /lucky rolls.

require_once('../inc/util.inc');
require_once('../inc/translation.inc');
require_once('../inc/email.inc');
require_once('../inc/bootstrap.inc');

db_init();
$db = BoincDb::get();

$user = get_logged_in_user();

function mask_email($email) {
    $parts = explode('@', $email);
    if (count($parts) !== 2) return '***';
    $name = $parts[0];
    $domain = $parts[1];
    $masked = strlen($name) <= 2 ? $name[0] . '***' : $name[0] . '***' . substr($name, -1);
    return $masked . '@' . $domain;
}

$msg_success = '';
$msg_error = '';

// Verify table existence
$table_res = $db->do_query("SHOW TABLES LIKE 'camicia_discord_links'");
$table_exists = ($table_res && $table_res->num_rows > 0);
if ($table_res) $table_res->free();

if (!$table_exists) {
    page_head(tra("Link Discord Account"));
    echo '<div class="alert alert-warning">'
        . tra("Discord linking is currently being set up. Please check back shortly!")
        . '</div>';
    page_tail();
    exit;
}

$uid = (int)$user->id;

// Fetch current link status
$link_data = null;
$res = $db->do_query("SELECT * FROM camicia_discord_links WHERE boinc_user_id = $uid");
if ($res && $res->num_rows > 0) {
    $link_data = $res->fetch_assoc();
    $res->free();
}

$is_linked = ($link_data && !empty($link_data['linked_at']));

// Handle live status polling from browser
if (isset($_GET['action']) && $_GET['action'] === 'status') {
    header('Content-Type: application/json');
    if ($is_linked) {
        echo json_encode([
            'linked' => true,
            'discord_id' => $link_data['discord_id'],
            'discord_username' => $link_data['discord_username'],
        ]);
    } else {
        echo json_encode(['linked' => false]);
    }
    exit;
}

if (isset($_GET['just_linked']) && $is_linked) {
    $msg_success = tra("Your Discord account has been linked successfully! Welcome to the Camicia volunteers.");
}

// Handle actions
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_tokens($user->authenticator);
    $action = $_POST['action'] ?? '';

    if ($action === 'send_code') {
        if ($is_linked) {
            $msg_error = tra("Your account is already linked to Discord. Please unlink first if you wish to change accounts.");
        } else {
            // Check 60-second rate limit
            $can_send = true;
            $seconds_remaining = 0;
            if ($link_data && !empty($link_data['pin_requested_at'])) {
                $last_req = strtotime($link_data['pin_requested_at']);
                $diff = time() - $last_req;
                if ($diff < 60) {
                    $can_send = false;
                    $seconds_remaining = 60 - $diff;
                }
            }

            if (!$can_send) {
                $msg_error = sprintf(
                    tra("Please wait %d seconds before requesting another verification code."),
                    $seconds_remaining
                );
            } else {
                // Generate a 6-digit PIN guaranteed unique among all active unexpired pending requests
                do {
                    $pin = sprintf('%06d', random_int(100000, 999999));
                    $check = $db->do_query("
                        SELECT id FROM camicia_discord_links 
                        WHERE pin = '$pin' AND linked_at IS NULL AND pin_expires_at > NOW()
                    ");
                    $collision = ($check && $check->num_rows > 0);
                    if ($check) $check->free();
                } while ($collision);

                $query = "
                    INSERT INTO camicia_discord_links 
                        (boinc_user_id, pin, pin_requested_at, pin_expires_at)
                    VALUES 
                        ($uid, '$pin', NOW(), DATE_ADD(NOW(), INTERVAL 15 MINUTE))
                    ON DUPLICATE KEY UPDATE 
                        pin = VALUES(pin),
                        pin_requested_at = VALUES(pin_requested_at),
                        pin_expires_at = VALUES(pin_expires_at)
                ";
                $db->do_query($query);

                // Build email body
                $subject = "[" . PROJECT . "] Discord Verification Code: $pin";
                $body_txt = email_greeting($user) . "\n"
                    . "Your verification code to link your Discord account with Camicia is:\n\n"
                    . "    " . $pin . "\n\n"
                    . "To complete linking, send this code to CamiciaBot on Discord:\n"
                    . "- In the #bot-commands channel on our Discord server, or\n"
                    . "- In a Direct Message (DM) to CamiciaBot\n\n"
                    . "Command:\n"
                    . "    /link " . $pin . "\n\n"
                    . "This code will expire in 15 minutes.\n"
                    . "If you did not request this verification, you can safely ignore this email.\n"
                    . email_footer();

                $body_html = '
                <div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
                    <div style="background: #1b4332; padding: 24px; text-align: center; color: #ffffff;">
                        <h2 style="margin: 0; font-size: 24px; color: #f1c40f; letter-spacing: 1px;">' . htmlspecialchars(PROJECT) . '</h2>
                        <p style="margin: 8px 0 0 0; font-size: 14px; opacity: 0.9;">Discord Account Verification</p>
                    </div>
                    <div style="padding: 32px 24px; color: #2c3e50; line-height: 1.6;">
                        <p style="font-size: 16px; margin-top: 0;">Hi <strong>' . htmlspecialchars($user->name) . '</strong>,</p>
                        <p>You requested to link your Discord account with your Camicia volunteer profile. Here is your verification code:</p>
                        <div style="margin: 28px 0; text-align: center;">
                            <div style="display: inline-block; background: #f8f9fa; border: 2px dashed #1b4332; border-radius: 8px; padding: 16px 36px;">
                                <span style="font-family: monospace; font-size: 36px; font-weight: bold; letter-spacing: 6px; color: #1b4332;">' . $pin . '</span>
                            </div>
                            <p style="font-size: 13px; color: #7f8c8d; margin-top: 10px;">Expires in 15 minutes</p>
                        </div>
                        <h4 style="margin: 24px 0 12px; font-size: 15px; color: #1b4332; text-transform: uppercase; letter-spacing: 0.5px;">How to Complete Linking:</h4>
                        <ol style="padding-left: 20px; margin: 0 0 24px 0;">
                            <li style="margin-bottom: 8px;">Open the <strong>#bot-commands</strong> channel in our Discord server (or message <strong>CamiciaBot</strong> in DMs).</li>
                            <li style="margin-bottom: 8px;">Type the following command and press Enter:
                                <div style="background: #2b2b2b; color: #f1c40f; padding: 8px 12px; border-radius: 4px; font-family: monospace; font-size: 14px; margin-top: 6px;">/link ' . $pin . '</div>
                            </li>
                            <li>The bot will verify the code and automatically grant you the <strong>Volunteer</strong> role!</li>
                        </ol>
                        <p style="font-size: 13px; color: #95a5a6; border-top: 1px solid #ecf0f1; padding-top: 16px; margin-bottom: 0;">
                            If you did not request this link, no action is needed. Your account remains completely secure.
                        </p>
                    </div>
                </div>';

                if (send_email($user, $subject, $body_txt, $body_html)) {
                    $msg_success = sprintf(
                        tra("A 6-digit verification code has been sent to your email (%s). Please check your inbox and run /link in Discord."),
                        mask_email($user->email_addr)
                    );
                    // Refresh link data
                    $res = $db->do_query("SELECT * FROM camicia_discord_links WHERE boinc_user_id = $uid");
                    if ($res && $res->num_rows > 0) {
                        $link_data = $res->fetch_assoc();
                        $res->free();
                    }
                } else {
                    $msg_error = tra("Could not send email. Please verify that your email address is correct or try again in a few moments.");
                }
            }
        }
    } elseif ($action === 'unlink') {
        if ($is_linked) {
            $db->do_query("DELETE FROM camicia_discord_links WHERE boinc_user_id = $uid");
            $msg_success = tra("Your Discord account has been unlinked successfully.");
            $is_linked = false;
            $link_data = null;
        }
    }
}

// Calculate cooldown for the button
$cooldown_seconds = 0;
if (!$is_linked && $link_data && !empty($link_data['pin_requested_at'])) {
    $diff = time() - strtotime($link_data['pin_requested_at']);
    if ($diff < 60) {
        $cooldown_seconds = 60 - $diff;
    }
}

page_head(tra("Discord Account Linking"));

if ($msg_success) {
    echo '<div class="alert alert-success"><i class="glyphicon glyphicon-ok-sign"></i> ' . htmlspecialchars($msg_success) . '</div>';
}
if ($msg_error) {
    echo '<div class="alert alert-danger"><i class="glyphicon glyphicon-exclamation-sign"></i> ' . htmlspecialchars($msg_error) . '</div>';
}

echo '<div class="row">';
echo '<div class="col-md-8 col-md-offset-2">';

if ($is_linked) {
    // -------------------------------------------------------------------------
    // ALREADY LINKED VIEW
    // -------------------------------------------------------------------------
    $d_name = !empty($link_data['discord_username']) ? htmlspecialchars($link_data['discord_username']) : ('ID: ' . $link_data['discord_id']);
    $linked_date = !empty($link_data['linked_at']) ? date_str(strtotime($link_data['linked_at'])) : tra('Unknown');

    echo '
    <div class="panel panel-success">
        <div class="panel-heading" style="background-color: #1b4332; color: #ffffff;">
            <h3 class="panel-title" style="font-weight: bold;">
                <i class="glyphicon glyphicon-link"></i> ' . tra("Discord Account Connected") . '
            </h3>
        </div>
        <div class="panel-body" style="padding: 24px;">
            <div style="display: flex; align-items: center; margin-bottom: 20px;">
                <div style="background: #e8f5e9; border-radius: 50%; width: 56px; height: 56px; display: flex; align-items: center; justify-content: center; margin-right: 18px;">
                    <span style="font-size: 28px;">🏅</span>
                </div>
                <div>
                    <h4 style="margin: 0 0 4px 0; color: #1b4332; font-weight: bold;">' . tra("Volunteer Status Active") . '</h4>
                    <p style="margin: 0; color: #7f8c8d;">' . sprintf(tra("Linked to Discord user %s"), '<strong>@' . $d_name . '</strong>') . ' &middot; ' . sprintf(tra("Connected on %s"), $linked_date) . '</p>
                </div>
            </div>

            <div class="well well-sm" style="background: #fafafa; border-left: 4px solid #1b4332; margin-bottom: 24px;">
                <h5 style="margin-top: 4px; font-weight: bold; color: #1b4332;">' . tra("Your Active Discord Perks") . ':</h5>
                <ul style="margin-bottom: 4px; padding-left: 20px;">
                    <li><strong>' . tra("Volunteer Server Role") . '</strong>: ' . tra("Granted automatically on our official Discord server.") . '</li>
                    <li><strong>' . tra("Bonus /lucky Rolls") . '</strong>: ' . tra("5 attempts per day on the Beggar-My-Neighbour mini-game (unlocked +2 daily rolls!).") . '</li>
                    <li><strong>' . tra("Community Attribution") . '</strong>: ' . tra("Your Discord handle will be recognized in discoveries and records.") . '</li>
                </ul>
            </div>

            <form method="POST" action="discord_link.php" onsubmit="return confirm(\'' . tra("Are you sure you want to unlink your Discord account? You will lose the Volunteer role and extra /lucky rolls.") . '\');">
                ' . form_tokens($user->authenticator) . '
                <input type="hidden" name="action" value="unlink">
                <button type="submit" class="btn btn-danger">
                    <i class="glyphicon glyphicon-remove"></i> ' . tra("Unlink Discord Account") . '
                </button>
                <a href="' . url_base() . 'home.php" class="btn btn-default" style="margin-left: 8px;">
                    ' . tra("Back to Account") . '
                </a>
            </form>
        </div>
    </div>';

} else {
    // -------------------------------------------------------------------------
    // NOT LINKED VIEW
    // -------------------------------------------------------------------------
    echo '
    <div class="panel panel-default">
        <div class="panel-heading" style="background-color: #1b4332; color: #ffffff;">
            <h3 class="panel-title" style="font-weight: bold;">
                <i class="glyphicon glyphicon-link"></i> ' . tra("Connect Your Discord Account") . '
            </h3>
        </div>
        <div class="panel-body" style="padding: 24px;">
            <p style="font-size: 15px;">
                ' . tra("Connect your Camicia BOINC account with our Discord community to receive the official <strong>Volunteer</strong> role and unlock exclusive community perks!") . '
            </p>

            <div class="row" style="margin: 20px 0;">
                <div class="col-sm-4 text-center">
                    <div style="background: #fdfefe; border: 1px solid #e1e8ed; border-radius: 8px; padding: 16px; min-height: 140px;">
                        <span style="font-size: 32px;">🏅</span>
                        <h5 style="font-weight: bold; margin-top: 10px; color: #1b4332;">' . tra("Volunteer Role") . '</h5>
                        <small style="color: #7f8c8d;">' . tra("Stand out in our official server as an active contributor.") . '</small>
                    </div>
                </div>
                <div class="col-sm-4 text-center">
                    <div style="background: #fdfefe; border: 1px solid #e1e8ed; border-radius: 8px; padding: 16px; min-height: 140px;">
                        <span style="font-size: 32px;">🎲</span>
                        <h5 style="font-weight: bold; margin-top: 10px; color: #1b4332;">' . tra("5 Daily /lucky Rolls") . '</h5>
                        <small style="color: #7f8c8d;">' . tra("Get +2 extra attempts every day to discover cycles and records.") . '</small>
                    </div>
                </div>
                <div class="col-sm-4 text-center">
                    <div style="background: #fdfefe; border: 1px solid #e1e8ed; border-radius: 8px; padding: 16px; min-height: 140px;">
                        <span style="font-size: 32px;">⭐</span>
                        <h5 style="font-weight: bold; margin-top: 10px; color: #1b4332;">' . tra("Hall of Fame") . '</h5>
                        <small style="color: #7f8c8d;">' . tra("Automatic Discord tagging when your host breaks milestones.") . '</small>
                    </div>
                </div>
            </div>

            <hr>

            <h4 style="font-weight: bold; color: #1b4332; margin-top: 20px;">' . tra("Step 1: Request Your Verification Code") . '</h4>
            <p>' . sprintf(
                tra("To protect your account, we will send a 6-digit one-time code to your registered email: <code>%s</code>."),
                htmlspecialchars(mask_email($user->email_addr))
            ) . '</p>

            <form method="POST" action="discord_link.php" style="margin: 16px 0;">
                ' . form_tokens($user->authenticator) . '
                <input type="hidden" name="action" value="send_code">
                <button type="submit" id="btn-send-code" class="btn btn-primary" ' . ($cooldown_seconds > 0 ? 'disabled' : '') . ' style="background-color: #1b4332; border-color: #143527;">
                    <i class="glyphicon glyphicon-envelope"></i> ' . ($cooldown_seconds > 0 ? sprintf(tra("Resend Code in %ds"), $cooldown_seconds) : tra("Send Verification Code via Email")) . '
                </button>
            </form>';

    if ($cooldown_seconds > 0) {
        echo '
        <script>
        (function() {
            var seconds = ' . $cooldown_seconds . ';
            var btn = document.getElementById("btn-send-code");
            var timer = setInterval(function() {
                seconds--;
                if (seconds <= 0) {
                    clearInterval(timer);
                    btn.disabled = false;
                    btn.innerHTML = \'<i class="glyphicon glyphicon-envelope"></i> ' . tra("Send Verification Code via Email") . '\';
                } else {
                    btn.innerHTML = \'<i class="glyphicon glyphicon-envelope"></i> ' . tra("Resend Code in") . ' \' + seconds + \'s\';
                }
            }, 1000);
        })();
        </script>';
    }

    $pin_display = ($link_data && !empty($link_data['pin'])) ? htmlspecialchars($link_data['pin']) : ('<em>' . tra("YOUR-CODE") . '</em>');

    echo '
            <h4 style="font-weight: bold; color: #1b4332; margin-top: 28px;">' . tra("Step 2: Confirm Code on Discord") . '</h4>
            <p>' . tra("Once you receive the 6-digit code in your email:") . '</p>
            <ol style="font-size: 15px; line-height: 1.8;">
                <li>' . tra("Open the Camicia Discord server (in the <code>#bot-commands</code> channel) or message <strong>CamiciaBot</strong> in a Direct Message.") . '</li>
                <li>' . tra("Type the command:") . '
                    <div style="background: #2b2b2b; color: #f1c40f; padding: 10px 14px; border-radius: 4px; font-family: monospace; font-size: 15px; margin: 8px 0; display: inline-block;">
                        /link ' . $pin_display . '
                    </div>
                    <div style="font-size: 13px; color: #7f8c8d; margin-top: 4px;">' . tra("Tip: When typing <code>/link</code> in Discord, select the <code>code</code> parameter and enter your 6 digits.") . '</div>
                </li>
                <li>' . tra("The bot will verify the code and instantly grant you the <strong>Volunteer</strong> role!") . '</li>
            </ol>
            <div class="alert alert-info" style="margin-top: 15px; margin-bottom: 0;">
                <i class="glyphicon glyphicon-refresh" style="animation: spin 2s linear infinite;"></i>
                ' . tra("Waiting for verification... This page will update automatically once verified on Discord.") . '
            </div>
            <style>
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            </style>
        </div>
    </div>';

    // Auto-detect link completion in real-time
    echo '
    <script>
    (function() {
        var checkTimer = setInterval(function() {
            fetch("discord_link.php?action=status")
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data && data.linked) {
                        clearInterval(checkTimer);
                        window.location.href = "discord_link.php?just_linked=1";
                    }
                })
                .catch(function(e) {});
        }, 3000);
    })();
    </script>';
}

echo '</div>'; // col
echo '</div>'; // row

page_tail();
