<?php
// Discord Account Linking page
//
// Allows logged-in BOINC volunteers to connect their Discord account to
// their Camicia account. For security, a 6-digit verification code is sent
// to their registered email address (rate-limited to 1 request per 60 seconds).
// Once received, the volunteer submits `/link <code>` to CamiciaBot on
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

$is_linked = ($link_data && !empty($link_data['linked_at']) && empty($link_data['unlinked_at']));

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
        } elseif ($link_data && !empty($link_data['unlinked_at'])) {
            $msg_error = tra("Unlinking is currently being finalized. Please wait a few moments before requesting a new code.");
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
                // Generate a 6-digit code guaranteed unique among all active unexpired pending requests
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
                        pin_expires_at = VALUES(pin_expires_at),
                        linked_at = NULL,
                        unlinked_at = NULL,
                        discord_id = NULL,
                        discord_username = NULL
                ";
                $db->do_query($query);

                // Build email body
                $subject = "[" . PROJECT . "] Discord Verification Code: $pin";
                $body_txt = email_greeting($user) . "\n"
                    . "Your verification code to link your Discord account with Camicia is:\n\n"
                    . "    " . $pin . "\n\n"
                    . "To complete linking, send this command in a Direct Message (DM) to CamiciaBot on Discord:\n\n"
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
                            <li style="margin-bottom: 8px;">Open a Direct Message (DM) to <strong>CamiciaBot</strong> on Discord.</li>
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
                        tra("A 6-digit verification code has been sent to your email (%s). Please check your inbox and run /link on Discord."),
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
            // Queue the unlink event in the database. The Discord bot's 30s background worker
            // will detect this, remove the Volunteer role on Discord, and clean up the row.
            // This keeps Discord bot credentials completely off the web server.
            $db->do_query("UPDATE camicia_discord_links SET unlinked_at = NOW(), linked_at = NULL WHERE boinc_user_id = $uid");
            $msg_success = tra("Your Discord account has been unlinked successfully. Your server role will be updated shortly.");
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
?>
<style>
.discord-page {
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
.discord-page * { box-sizing: border-box; }
.discord-page .eyebrow { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--gold); font-weight: 700; margin: 0 0 12px; }
.discord-page h1 { font-family: Georgia, 'Iowan Old Style', 'Palatino Linotype', serif; font-size: 32px; line-height: 1.2; margin: 0 0 14px; color: var(--cream); }
.discord-page .lede { font-size: 15px; line-height: 1.65; color: var(--cream-dim); max-width: 68ch; margin: 0 0 30px; }
.discord-page .card-box { background: var(--felt-2); border: 1px solid var(--felt-line); border-radius: 14px; padding: 26px 28px; margin-bottom: 24px; }
.discord-page .card-box h3 { font-family: Georgia, serif; font-size: 20px; color: var(--cream); margin: 0 0 12px; }
.discord-page .card-box h4 { font-family: Georgia, serif; font-size: 16.5px; color: var(--gold); margin: 22px 0 10px; }
.discord-page .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 2px; background: var(--felt-line); border: 1px solid var(--felt-line); border-radius: 14px; overflow: hidden; margin-bottom: 28px; }
.discord-page .stat-tile { background: var(--felt-2); padding: 20px 18px; text-align: center; }
.discord-page .stat-icon { font-size: 28px; margin-bottom: 8px; }
.discord-page .stat-title { font-family: Georgia, serif; font-size: 16px; color: var(--gold); margin: 0 0 6px; font-weight: 600; }
.discord-page .stat-desc { font-size: 12.5px; color: var(--cream-dim); line-height: 1.5; margin: 0; }
.discord-page .code-box { background: #08120e; border: 1.5px solid var(--gold-dim); border-radius: 8px; padding: 10px 18px; font-family: monospace; font-size: 16px; color: var(--gold); display: inline-block; margin: 8px 0; letter-spacing: 0.5px; }
.discord-page .btn-gold { font: inherit; font-size: 13.5px; font-weight: 600; background: var(--gold); color: var(--felt); border: 1px solid var(--gold); border-radius: 999px; padding: 10px 24px; cursor: pointer; transition: all 0.15s ease; text-decoration: none; display: inline-flex; align-items: center; gap: 8px; }
.discord-page .btn-gold:hover { background: #ffe082; border-color: #ffe082; color: var(--felt); text-decoration: none; }
.discord-page .btn-gold:disabled { opacity: 0.45; cursor: not-allowed; }
.discord-page .btn-ruby { font: inherit; font-size: 13px; font-weight: 600; background: var(--ruby); color: var(--cream); border: 1px solid #d94b63; border-radius: 999px; padding: 9px 22px; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; transition: all 0.15s ease; }
.discord-page .btn-ruby:hover { background: #c23d57; color: #ffffff; text-decoration: none; }
.discord-page .btn-ghost { font: inherit; font-size: 13px; font-weight: 600; background: transparent; color: var(--cream-dim); border: 1px solid var(--felt-line); border-radius: 999px; padding: 9px 20px; text-decoration: none; margin-left: 8px; display: inline-flex; align-items: center; transition: all 0.15s ease; }
.discord-page .btn-ghost:hover { color: var(--cream); border-color: var(--gold-dim); text-decoration: none; }
.discord-page .alert-banner { background: var(--felt-2); border-radius: 10px; padding: 14px 18px; color: var(--cream); margin-bottom: 24px; display: flex; align-items: center; gap: 12px; font-size: 14px; }
.discord-page .alert-banner.success { border: 1px solid var(--gold); background: #132a22; }
.discord-page .alert-banner.danger { border: 1px solid var(--ruby); background: #261217; }
.discord-page .alert-banner.info { border: 1px solid var(--gold-dim); background: #13251e; }
.discord-page ol { padding-left: 20px; margin: 12px 0 20px; line-height: 1.8; color: var(--cream); font-size: 14px; }
.discord-page ol li { margin-bottom: 10px; }
.discord-page .perks-list { list-style: none; padding: 0; margin: 14px 0 22px; }
.discord-page .perks-list li { padding: 9px 0; border-bottom: 1px solid var(--felt-line); font-size: 14px; display: flex; align-items: center; gap: 10px; color: var(--cream); }
.discord-page .perks-list li:last-child { border-bottom: none; }
.discord-page .active-badge { display: inline-flex; align-items: center; gap: 8px; background: #132a22; border: 1px solid var(--gold); border-radius: 999px; padding: 6px 16px; font-size: 13px; color: var(--gold); font-weight: 600; margin-bottom: 16px; }
@keyframes spin-camicia { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
.discord-page .spinner { display: inline-block; animation: spin-camicia 2s linear infinite; }
</style>

<div class="discord-page">
  <p class="eyebrow">Camicia &middot; <?php echo tra("Community"); ?></p>
  <h1><?php echo tra("Connect Your Discord Account"); ?></h1>
  <p class="lede">
    <?php echo tra("Link your BOINC account with our Discord community to receive the Volunteer server role, unlock 5 daily /lucky mini-game rolls, and get attributed in discoveries."); ?>
  </p>

<?php if ($msg_success): ?>
  <div class="alert-banner success">
    <i class="glyphicon glyphicon-ok-sign" style="color: var(--gold); font-size: 18px;"></i>
    <span><?php echo htmlspecialchars($msg_success); ?></span>
  </div>
<?php endif; ?>

<?php if ($msg_error): ?>
  <div class="alert-banner danger">
    <i class="glyphicon glyphicon-exclamation-sign" style="color: #ff6b81; font-size: 18px;"></i>
    <span><?php echo htmlspecialchars($msg_error); ?></span>
  </div>
<?php endif; ?>

<?php if ($is_linked): ?>
  <?php
    $d_name = !empty($link_data['discord_username']) ? htmlspecialchars($link_data['discord_username']) : ('ID: ' . $link_data['discord_id']);
    $linked_date = !empty($link_data['linked_at']) ? date_str(strtotime($link_data['linked_at'])) : tra('Unknown');
  ?>
  <div class="card-box" style="border-color: var(--gold-dim);">
    <div class="active-badge">
      <span>🏅</span> <?php echo tra("Volunteer Status Active"); ?>
    </div>
    <h3><?php echo sprintf(tra("Linked as @%s"), $d_name); ?></h3>
    <p style="color: var(--cream-dim); font-size: 13px; margin: 0 0 18px;">
      <?php echo sprintf(tra("Connected to your Camicia account on %s"), $linked_date); ?>
    </p>

    <ul class="perks-list">
      <li>
        <span style="font-size: 18px;">🏅</span>
        <div><strong><?php echo tra("Volunteer Server Role"); ?></strong> &mdash; <?php echo tra("Active on our official Discord server."); ?></div>
      </li>
      <li>
        <span style="font-size: 18px;">🎲</span>
        <div><strong><?php echo tra("5 Daily /lucky Rolls"); ?></strong> &mdash; <?php echo tra("Unlocked +2 bonus attempts on the Beggar-My-Neighbour simulator."); ?></div>
      </li>
      <li>
        <span style="font-size: 18px;">⭐</span>
        <div><strong><?php echo tra("Milestone Attribution"); ?></strong> &mdash; <?php echo tra("Your Discord handle is tagged in #records-and-loops when you hit records."); ?></div>
      </li>
    </ul>

    <form method="POST" action="discord_link.php" onsubmit="return confirm('<?php echo tra("Are you sure you want to unlink your Discord account? You will lose the Volunteer role and extra /lucky rolls."); ?>');" style="margin: 0;">
      <?php echo form_tokens($user->authenticator); ?>
      <input type="hidden" name="action" value="unlink">
      <button type="submit" class="btn-ruby">
        <i class="glyphicon glyphicon-remove"></i> <?php echo tra("Unlink Discord Account"); ?>
      </button>
      <a href="<?php echo url_base(); ?>home.php" class="btn-ghost">
        <?php echo tra("Back to Account"); ?> &rarr;
      </a>
    </form>
  </div>

<?php else: ?>
  <div class="stat-grid">
    <div class="stat-tile">
      <div class="stat-icon">🏅</div>
      <div class="stat-title"><?php echo tra("Volunteer Role"); ?></div>
      <p class="stat-desc"><?php echo tra("Stand out as a verified computing contributor in our server."); ?></p>
    </div>
    <div class="stat-tile">
      <div class="stat-icon">🎲</div>
      <div class="stat-title"><?php echo tra("5 Rolls / Day"); ?></div>
      <p class="stat-desc"><?php echo tra("Get +2 extra daily attempts on /lucky to discover records."); ?></p>
    </div>
    <div class="stat-tile">
      <div class="stat-icon">⭐</div>
      <div class="stat-title"><?php echo tra("Hall of Fame"); ?></div>
      <p class="stat-desc"><?php echo tra("Automatic Discord tagging when your host sets milestone games."); ?></p>
    </div>
  </div>

  <div class="card-box">
    <h4><?php echo tra("Step 1: Request Verification Code"); ?></h4>
    <p style="color: var(--cream-dim); font-size: 14px; margin-bottom: 16px;">
      <?php echo sprintf(
        tra("To verify your account securely, we will send a 6-digit one-time code to your registered email: <code>%s</code>."),
        htmlspecialchars(mask_email($user->email_addr))
      ); ?>
    </p>

    <form method="POST" action="discord_link.php" style="margin: 12px 0 20px;">
      <?php echo form_tokens($user->authenticator); ?>
      <input type="hidden" name="action" value="send_code">
      <button type="submit" id="btn-send-code" class="btn-gold" <?php echo ($cooldown_seconds > 0 ? 'disabled' : ''); ?>>
        <i class="glyphicon glyphicon-envelope"></i>
        <span><?php echo ($cooldown_seconds > 0 ? sprintf(tra("Resend Code in %ds"), $cooldown_seconds) : tra("Send Verification Code via Email")); ?></span>
      </button>
    </form>

    <?php if ($cooldown_seconds > 0): ?>
    <script>
    (function() {
        var seconds = <?php echo (int)$cooldown_seconds; ?>;
        var btn = document.getElementById("btn-send-code");
        var timer = setInterval(function() {
            seconds--;
            if (seconds <= 0) {
                clearInterval(timer);
                btn.disabled = false;
                btn.innerHTML = '<i class="glyphicon glyphicon-envelope"></i> <?php echo tra("Send Verification Code via Email"); ?>';
            } else {
                btn.innerHTML = '<i class="glyphicon glyphicon-envelope"></i> <?php echo tra("Resend Code in"); ?> ' + seconds + 's';
            }
        }, 1000);
    })();
    </script>
    <?php endif; ?>

    <hr style="border-color: var(--felt-line); margin: 28px 0 24px;">

    <h4><?php echo tra("Step 2: Confirm Code on Discord"); ?></h4>
    <p style="color: var(--cream-dim); font-size: 14px;">
      <?php echo tra("Once you receive the 6-digit code in your email:"); ?>
    </p>

    <ol>
      <li><?php echo tra("Send a Direct Message (DM) to <strong>CamiciaBot</strong> on Discord."); ?></li>
      <li>
        <?php echo tra("Type the command:"); ?>
        <br>
        <div class="code-box">/link &lt;code&gt;</div>
        <div style="font-size: 12.5px; color: var(--cream-dim); margin-top: 4px;">
          <?php echo tra("Replace <code>&lt;code&gt;</code> with the 6-digit code from your email (e.g. <code>/link 123456</code>), or simply send the 6 digits directly."); ?>
        </div>
      </li>
      <li><?php echo tra("The bot will verify your code and instantly award you the <strong>Volunteer</strong> role!"); ?></li>
    </ol>

    <div class="alert-banner info" style="margin-top: 20px; margin-bottom: 0;">
      <span class="spinner">⟳</span>
      <span><?php echo tra("Waiting for verification... This page will update automatically once verified on Discord."); ?></span>
    </div>
  </div>

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
  </script>
<?php endif; ?>

</div>

<?php
page_tail();
