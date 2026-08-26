<?php

require_once("../inc/util_ops.inc");

$user = get_logged_in_user_ops();
if ($user) admin_error_page("already logged in");
admin_page_head("Log in");

$next_url = sanitize_local_url(get_str('next_url', true));

// Camicia fix: get_str('next_url', true) returns null when the GET param is
// absent (the common case -- most links into the ops area don't pass one),
// and sanitize_local_url() passes null straight through. strlen(null) is
// deprecated as of PHP 8.1, which is what showed up here.
if (strlen($next_url ?? '') == 0) $next_url = "index.php";

print_login_form_ops($next_url);
admin_page_tail();
?>
