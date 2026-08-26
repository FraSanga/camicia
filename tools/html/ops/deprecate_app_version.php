#!/usr/bin/env php
<?php
// Deprecates a specific app_version so BOINC's scheduler stops handing it
// out immediately, rather than relying only on sched_shmem.cpp's passive
// per-plan_class logic (a higher version_num in the same plan_class
// supersedes a lower one only in memory, once the feeder reloads -- it
// does not stop an already-cached bad app_version_id from being resent to
// a host that already has it, see sched_version.cpp's resend path). Same
// effect html/ops/manage_app_versions.php's UI checkbox already gives you
// by hand, exposed here as a direct command so
// tools/deploy_rollback.sh's --restore path can call it without a human
// clicking anything -- see that script's own header comment for why.
//
// Usage: deprecate_app_version.php <version, e.g. 1.05>

$cli_only = true;
require_once("../inc/util_ops.inc");

db_init();

define('APPNAME', 'simulator');

if ($argc < 2) {
    fwrite(STDERR, "Usage: deprecate_app_version.php <version, e.g. 1.05>\n");
    exit(1);
}

$version_str = $argv[1];
if (!preg_match('/^(\d+)\.(\d+)$/', $version_str, $m)) {
    fwrite(STDERR, "Bad version format: $version_str (expected e.g. 1.05)\n");
    exit(1);
}
// Same encoding tools/update_versions' own parse_version() uses:
// MAJOR*100 + MINOR.
$version_num = ((int)$m[1]) * 100 + (int)$m[2];

$db = BoincDb::get();
$app = BoincApp::lookup("name='" . APPNAME . "'");
if (!$app) {
    fwrite(STDERR, "App '" . APPNAME . "' not found\n");
    exit(1);
}

$result = $db->do_query("
    update app_version set deprecated=1
    where appid = " . (int)$app->id . " and version_num = $version_num and deprecated = 0
");
if (!$result) {
    fwrite(STDERR, "Query failed\n");
    exit(1);
}

$affected = $db->affected_rows();
echo date(DATE_RFC822), ": deprecated $affected app_version row(s) for $version_str (appid=" . (int)$app->id . ", version_num=$version_num)\n";

?>
