<?php

// Camicia project web config. Sourced in tools/ (not hand-edited in the
// generated project tree) so branding/ToS settings survive a redeploy or a
// disaster-recovery rebuild instead of living only in the gitignored
// projects/camicia/ tree. Deployed by tools.sh to html/project/project.inc.
//
// add tra() around visible strings to make them translatable
// see inc/translation.inc and
// https://github.com/BOINC/boinc/wiki/TranslateProject for details

require_once("../inc/util.inc");

//-------------- Project name and owner

define("PROJECT", "Camicia");
define("COPYRIGHT_HOLDER", "Francesco Sangalli");
// Read by html/user/cert1.php for the signature line on certificates
// (a stock BOINC customization hook -- see cert1.php's own header
// comment/the wiki link there -- left unset by make_project by default).
define("CERT_DIRECTOR_NAME", COPYRIGHT_HOLDER);

//-------------- URLs and directories

$master_url = project_config_val("master_url");
define("URL_BASE", $master_url);
define("IMAGE_PATH", "../user_profile/images/");
define("IMAGE_URL", "user_profile/images/");
define("PROFILE_PATH", "../user_profile/");
define("PROFILE_URL", "user_profile/");
define("LANGUAGE_FILE", "languages.txt");

//-------------- contact info

// Real addresses, routed via Cloudflare Email Routing (camicia.dev's MX
// records point at Cloudflare, which forwards to the project owner's real
// inbox -- no mailbox hosted here). Previously these were built as
// "admin@$master_url", which is a bug: $master_url is a full URL
// (e.g. "https://staging.camicia.dev/camicia/"), not a bare domain, so the
// result was never a valid, deliverable email address.
define("SYS_ADMIN_EMAIL", "admin@camicia.dev");
define("UOTD_ADMIN_EMAIL", "admin@camicia.dev");
    // who gets emails about user of the day pool running low?
define("POST_REPORT_EMAILS", "moderator@camicia.dev");
    // Email addresses separated by pipe ( | ) that will receive user reports
    // of offensive forum posts.

//-------------- PHP error reporting
// Show PHP errors in web pages.
// Comment this out when your project is public.
display_errors();

//-------------- CSS styling

// add your own stylesheets (included after bootstrap)
//define("STYLESHEET", "aaa.css");
//define("STYLESHEET2", "bbb.css");
// Relative to $url_base's root (html/user/), same convention as
// STYLESHEET/STYLESHEET2 above -- html/inc/util.inc (Camicia override)
// prepends $url_base at emission time so this resolves correctly from
// every directory depth (html/ops/, html/admin/, forum pages, ...), not
// just the one this happens to be tested from.
define("SHORTCUT_ICON", "img/favicon.svg");     // browser tab icon
define('DARK_MODE', true);      // enable dark mode if selected by user

//-------------- enable/disable web features

define('UNIQUE_USER_NAME', true);
define("FORUM_QA_MERGED_MODE", true);
    // Set to true to merge Message boards and Q&A section
define ("DISABLE_PROFILES", false);
    // reCAPTCHA v2 is live and verified (see RECAPTCHA_SITE_KEY/SECRET_KEY
    // injection in tools.sh) -- create_profile.php already gates on
    // recaptcha_public_key()/recaptcha_private_key(), so this was the one
    // remaining precondition from
    // https://github.com/BOINC/boinc/wiki/ProtectionFromSpam
define("USE_STOPFORUMSPAM", true);
    // use http://www.stopforumspam.com to suppress spammer accounts
define('VALIDATE_EMAIL_TO_POST', true);
    // require a confirmed email address before a user can post to forums,
    // reply, send private messages, or report posts (html/inc/util.inc's
    // own default is false; this project.inc load happens early enough,
    // via util.inc's own require_once of this file, that defining it here
    // wins over that default -- verified against the include chain).
define("RPC_DEBUG", false);
    // Enables PHP error messages in public Web RPCs
define("TERMSOFUSE_FILE", "../../terms_of_use.txt");
    // Defines the location of the terms of use file. Default location is in the project directory.

//-------------- Project-specific preferences

define('COLOR_PREFS', false);
    // user can select screensaver color scheme
define('GFX_CPU_PREFS', false);
    // user can limit % CPU used by screensaver
    // (lower frame rate if exceeded)
    // This is probably irrelevant if your screensaver uses OpenGL
define('APP_SELECT_PREFS', false);
    // user can choose which apps to run
define('NON_GRAPHICAL_PREF', false);
    // user can choose to run faster non-graphical app versions if available
define('MAX_JOBS_PREF', false);
    // user can choose max # of jobs in progress
define('MAX_CPUS_PREF', false);
    // user can choose max # of CPUs to use

//-------------- PHPMailer

// Real SMTP credentials are kept out of this (git-tracked) file. tools.sh
// writes them into smtp_credentials.inc.php at the project root -- generated
// from SMTP_* in .env, same pattern as ntfy_topic -- and vendors the
// PHPMailer library itself (tools/phpmailer/) into html/inc/PHPMailer/. If
// that credentials file isn't present (e.g. a dev/test deploy with no SMTP
// configured), make_php_mailer() is deliberately left undefined so
// send_email() (html/inc/email.inc) falls back to PHP's mail() -- which
// silently no-ops here since there's no local MTA, same as before SMTP was
// configured at all.
$smtp_credentials_file = __DIR__ . "/../../smtp_credentials.inc.php";
if (file_exists($smtp_credentials_file)) {
    require_once($smtp_credentials_file);
    function make_php_mailer() {
        $mail = new \PHPMailer\PHPMailer\PHPMailer();
        $mail->isSMTP();
        $mail->SMTPAuth = true;
        $mail->SMTPSecure = "tls";
        $mail->Host = SMTP_HOST;
        $mail->Port = SMTP_PORT;
        $mail->Username = SMTP_USERNAME;
        $mail->Password = SMTP_PASSWORD;
        $mail->setFrom(SMTP_FROM_EMAIL, SMTP_FROM_NAME);
        $mail->addReplyTo(SMTP_FROM_EMAIL, SMTP_FROM_NAME);
        return $mail;
    }
}

//-------------- Certificate verification

// Real HMAC secret is kept out of this (git-tracked) file, same pattern as
// the SMTP credentials just above -- tools.sh writes it into
// cert_verify_secret.inc.php at the project root, from CERT_VERIFY_SECRET
// in .env. If not present, cert.inc's cert_verify_code() simply returns
// null and cert1.php doesn't print a verification code/link at all.
$cert_verify_secret_file = __DIR__ . "/../../cert_verify_secret.inc.php";
if (file_exists($cert_verify_secret_file)) {
    require_once($cert_verify_secret_file);
}

//-------------- Header and footer

// Put your project title and logo here
// If you include any links, prepend URL with $prefix
// if you need to open divs here close them in project_footer()
//
function project_banner($title, $prefix, $is_main) {
    if ($is_main) {
        echo '<img class="img-responsive" style="width:100%" src="img/camicia_banner.svg" alt="Camicia">';
    }
    sample_navbar(secure_url_base(), get_logged_in_user(false), false);
    if ($title) {
        echo "<h2>$title</h2>\n";
    }
}
//$fixed_navbar = true;

function project_footer($show_return, $show_date, $prefix) {
    echo sprintf(
        '<center><a href="%s"><img src=%s/%s alt="%s"></a></center>',
        'https://boinc.berkeley.edu',
        secure_url_base(),
        'img/pb_boinc.gif',
        'Powered by BOINC'
    );
    echo sprintf('<p><center>&copy; %s %s</center>',
        gmdate("Y "),
        COPYRIGHT_HOLDER
    );
    if ($show_date) {
        echo sprintf('<center><small>%s %s</small><center>',
            tra('Generated'),
            time_str(time())
        );
    }
}

//-------------- Caching
// format: "host:port:weight"
//define("MEMCACHE_SERVERS", "127.0.0.1:11211:50");

//-------------- Ops access control

// Authorize access to administrative pages.
// You can check for login, IP address, or whatever you want.
//
function auth_ops_example() {
    // if running from cmdline, skip checks
    //
    if (!$_SERVER['REMOTE_ADDR']) {
        return;
    }

    // example: require login as a particular user (id 1 in this case)
    //
    if (0) {
        auth_ops_userid(array(1));
        return;
    }

    // example: require that logged-in user have ADMIN or DEV flags
    // set in their forum_prefs.privilege
    //
    if (0) {
        auth_ops_privilege();
        return;
    }
}

//-------------- Customizable functions

function show_profile_heading1() {
    return tra("Your personal background.");
}

function show_profile_question1() {
    return tra("Tell us about yourself. You could tell us where you're from, your age, occupation, hobbies, or anything else about yourself.");
}

function show_profile_heading2() {
    return tra("Your opinions about %1", PROJECT);
}

function show_profile_question2() {
    return tra("Tell us your thoughts about %1<ol>
    <li>Why do you run %1?
    <li>What are your views about the project?
    <li>Any suggestions?
    </ol>", PROJECT);
}

function project_workunit($wu){
    // shown in the workunit page
}

function project_user_summary($user){
    // shown in the user summary page
}

function project_user_page_private($user){
    // shown in the private account page
}

function project_forum_post_rules() {
    // additional forum post rules
    return "";
}

function project_delete_account($user) {
    // a project defined function for deleting users if neither
    // obfuscate_account($user) or wipe_account($user) in
    // inc/delete_account.inc meets the need of the project
    die("This function must be implemented before it can be used");
}

//-------------- Delete Account
// TODO: get rid of this. Doesn't need to be configurable, or even exist.
//define("DELETE_DELAY", 2);
    // When deleting an account, invalidate the authenticator and then wait this
    // many seconds before proceeding with the delete.  This is intended to give the
    // an existing scheduler request sufficient time to complete.  Some projects
    // might want to increase this to a longer time.  Simply uncomment and set the
    // delay to what the project needs.

//-------------- Support for per-app credit

if (0) {

// show project-specific credit on user/team pages
//
function show_app_credit_user($user, $app_name, $appids) {
    $t = 0;
    $a = 0;
    $n = 0;
    foreach ($appids as $appid) {
        $cu = BoincCreditUser::lookup(
            "userid=$user->id and appid=$appid and credit_type=0"
        );
        if ($cu) {
            $t += $cu->total;
            $a += $cu->expavg;
            $n += $cu->njobs;
        }
    }
    row2("$app_name credit",
        format_credit_large($t)." total, ".
        format_credit($a)." average".
        " ($n tasks)"
    );
}
function show_app_credit_team($team, $app_name, $appids) {
    $t = 0;
    $a = 0;
    $n = 0;
    foreach ($appids as $appid) {
        $ct = BoincCreditTeam::lookup(
            "teamid=$team->id and appid=$appid and credit_type=0"
        );
        if ($ct) {
            $t += $ct->total;
            $a += $ct->expavg;
            $n += $ct->njobs;
        }
    }
    row2("$app_name credit",
        format_credit_large($t)." total, ".
        format_credit($a)." average".
        " ($n tasks)"
    );
}

// a list of "sub-projects", used in the display of per-app credit and badges.
// A subproject is:
// - a set of 1 or more apps; an app can belong to at most 1 subproject.
// - a name, shown on the web site
// - a short name, used in badge names.  Don't use "total".
//
$sub_projects = array(
    array("name" => "Remote Test", "short_name" => "RT", "appids" => array(16)),
    array("name" => "Uppercase", "short_name" => "UC", "appids" => array(1, 25)),
);

function project_user_credit($user){
    global $sub_projects;
    foreach ($sub_projects as $sp) {
        show_app_credit_user($user, $sp["name"], $sp["appids"]);
    }
}

function project_team_credit($team) {
    global $sub_projects;
    foreach ($sub_projects as $sp) {
        show_app_credit_team($team, $sp["name"], $sp["appids"]);
    }
}

}   // if(0)

?>
