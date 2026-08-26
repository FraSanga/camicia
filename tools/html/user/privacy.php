<?php
// Dedicated privacy policy page -- until now, privacy was only covered by
// a short, incomplete paragraph in terms_of_use.txt (no mention of host
// data, third-party processors, full-database backups, or cookies). This
// replaces that with an accurate account of what the codebase actually
// does, verified against schema.sql/util.inc/db_backup.sh/etc. rather
// than generic boilerplate -- every claim below traces back to a real
// file. Same page_head()/page_tail()/tra() structure as about.php.
require_once('../inc/util.inc');
require_once('../inc/translation.inc');

check_get_args(array());

page_head(tra("Privacy Policy"));

echo "
<p>".tra("This page explains what personal data %1 collects, why, who else sees or receives it, how long it's kept, and what rights you have over it -- written to be accurate about what this specific project's code actually does, not a generic template.", PROJECT)."</p>

<h3>".tra("Who is responsible for your data")."</h3>
<p>".tra("%1 is developed and run by %2, reachable at %3. There is no separate company behind the project -- %2 is personally responsible for the data described on this page.", PROJECT, COPYRIGHT_HOLDER, "<a href=\"mailto:".SYS_ADMIN_EMAIL."\">".SYS_ADMIN_EMAIL."</a>")."</p>

<h3>".tra("What we collect, and why")."</h3>
<p>".tra("Creating an account collects an email address and a password, stored as a salted hash rather than in plain text. A display name, country, postal code, personal URL, and preferred venue are all optional -- you choose whether to add them. This is collected because it's necessary to create and operate your account (providing your login, occasional account-related email, and letting the project run at all).")."</p>
<p>".tra("Once you attach a computer, the BOINC client software running on it reports information needed to send it appropriate work: its IP address, operating system, CPU/GPU model and benchmark scores, and memory/disk size. This is standard behavior of the BOINC software itself, not something specific to %1 -- it's necessary so the project can actually distribute work to your computer correctly.", PROJECT)."</p>

<h3>".tra("What's publicly visible")."</h3>
<p>".tra("Some information is visible to any visitor of the site by default, not just logged-in users: your country (shown as a flag next to forum posts), your optional personal URL, any forum posts you make, your team membership, and any badges you've earned. Your email address, postal code, and the technical details of your computers are not shown publicly this way.")."</p>

<h3>".tra("Cookies")."</h3>
<p>".tra("Logging in sets one cookie containing your account's authentication token (not your email or password) -- either for your browser session only, or for one year if you choose \"remember me\" at login. It's marked HttpOnly (inaccessible to page scripts) and Secure when the site is served over HTTPS. A second, non-identifying cookie remembers your interface language preference. Neither is used for advertising or tracking, and %1 runs no analytics or tracking scripts of any kind.", PROJECT)."</p>

<h3>".tra("Statistics exports")."</h3>
<p>".tra("Once a day, %1 publishes a snapshot of account, computer, and team statistics (username, country, credit, team membership, and for computers, hardware and performance details) as downloadable files at %2, for use by BOINC-wide statistics tracking sites. This is standard behavior of the BOINC software itself: the files are openly reachable by anyone or anything that requests them, whether or not a particular statistics site is actively doing so right now. Your email address, password, and authentication details are never included. Your computers appear in this export only if your account preferences have \"show your computers to others\" enabled; you can turn this off at any time in your account settings.", PROJECT, "<code>/stats/</code>")."</p>

<h3>".tra("Who else receives data, and why")."</h3>
<p>".tra("A few outside services are used to run the project, each for a specific, narrow purpose:")."</p>
<ul>
<li>".tra("%1 (email delivery): your email address, to send account-related messages such as password resets and account-deletion confirmations.", "Brevo")."</li>
<li>".tra("%1 (spam prevention): behavioral signals from your browser, shown only on account creation and similar forms, to filter automated abuse.", "Google reCAPTCHA")."</li>
<li>".tra("%1 (spam filtering) and %2 (signup filtering): the content of forum posts, private messages, and profile text, or signup details, to catch spam before it's published.", "Akismet", "StopForumSpam")."</li>
<li>".tra("%1 (network routing): if enabled on this deployment, visitor traffic (including IP addresses) passes through Cloudflare's network before reaching the server.", "Cloudflare")."</li>
</ul>
<p>".tra("Some of these services are based outside the European Union/EEA and may process data there. None of them are permitted to use your data for anything beyond the specific purpose listed above, and %1 does not sell or share data with anyone for advertising or marketing purposes.", PROJECT)."</p>

<h3>".tra("Backups")."</h3>
<p>".tra("The project's database -- including account information, computer details, forum posts, and private messages, not just computational results -- is backed up daily to Google Drive and to a physically separate local drive, so the project can recover from hardware failure or a compromised server. Backups are not used for any other purpose and are not accessible to anyone but the project operator.")."</p>

<h3>".tra("How long we keep it")."</h3>
<p>".tra("Account and computer data is kept for as long as your account is active. If you delete your account, it is fully and irreversibly erased -- not just deactivated -- along with your computers, forum posts, private messages, credit history, and profile. A small pseudonymous record (just an internal ID and timestamp, no personal information) is kept for up to 60 days afterward so other BOINC projects you're also part of can be notified of the deletion, then it too is removed automatically. Backups taken before a deletion may still contain your data until they themselves are rotated out.")."</p>

<h3>".tra("Your rights")."</h3>
<p>".tra("If you're in the European Union or another jurisdiction with similar protections, you have the right to access, correct, export, or erase your data, to object to or restrict some of the processing described above, and to lodge a complaint with your local data protection authority. You can delete your account and all associated data yourself at any time from your account settings; for anything else, contact %1.", "<a href=\"mailto:".SYS_ADMIN_EMAIL."\">".SYS_ADMIN_EMAIL."</a>")."</p>
";

page_tail();
?>
