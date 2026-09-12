<?php
// Custom 404 page, wired in via Apache's ErrorDocument directive
// (images/server/camicia.httpd.conf) so a bad/missing URL under /camicia/
// gets this instead of Apache's bare stock "Not Found" text. Deliberately
// takes no input and does no argument validation -- it's a catch-all error
// handler, reachable with any arbitrary bad path or query string, and must
// never itself error out.

require_once('../inc/util.inc');

http_response_code(404);

page_head(tra("Page Not Found"));

echo "
<p>".tra("The page you're looking for doesn't exist. It may have moved, or the link might be out of date.")."</p>
<p><a href=\"index.php\">".tra("Return to the %1 home page", PROJECT)."</a></p>
";

page_tail();
?>
