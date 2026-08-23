#!/usr/bin/env php
<?php
// Camicia forum setup. Sourced in tools/ (not hand-edited in the generated
// project tree) so it's reviewable/reproducible instead of a one-off DB
// change made by hand. Deployed by tools.sh to html/ops/create_forums.php
// (its require_once("../inc/...") paths only resolve correctly one level
// under html/), but NOT run automatically on every deploy: verified against
// a real local deploy that category/forum DO have unique keys (`cat1` on
// (name, is_helpdesk); `pct` on (parent_type, category, title)) despite
// db/schema.sql showing none. Run this by hand, once (a second run is safe:
// create_category()/create_forum() catch the duplicate-key failure and
// reuse the existing row instead of erroring):
//   docker exec --user boincadm boinc_server bash -c \
//     'cd ~/projects/camicia/html/ops && php create_forums.php'
// RUN AS A CLI SCRIPT, NOT VIA A BROWSER (matches upstream BOINC's own
// html/ops/create_forums.php, which this is adapted from).

$cli_only = true;
require_once("../inc/forum_db.inc");
require_once("../inc/util_ops.inc");

function create_category($orderID, $name, $is_helpdesk) {
    $db = BoincDB::get();
    $name_escaped = BoincDb::escape_string($name);
    $q = "(orderID, lang, name, is_helpdesk) values ($orderID, 1, '$name_escaped', $is_helpdesk)";
    // Modern PHP/mysqli throws mysqli_sql_exception on a failed query by
    // default instead of returning false, so a duplicate-key failure (a
    // second run of this script) has to be caught here or the fallback
    // lookup below is unreachable dead code and the script just crashes.
    $result = false;
    $insert_error = "";
    try {
        $result = $db->insert("category", $q);
    } catch (\mysqli_sql_exception $e) {
        $insert_error = $e->getMessage();
    }
    if (!$result) {
        $cat = BoincCategory::lookup("name='$name_escaped' and is_helpdesk=$is_helpdesk");
        if ($cat) return $cat->id;
        echo "can't create category\n";
        echo $insert_error ?: $db->base_error();
        exit();
    }
    return $db->insert_id();
}

// $post_min_total_credit: 0 = anyone can post. Nonzero = poster must have
// earned at least that much credit first, i.e. actually run the BOINC
// client -- a spam deterrent that costs nothing to set up, but deliberately
// left at 0 for forums where a brand-new, not-yet-computing volunteer
// legitimately needs to post (News, Help & Troubleshooting).
//
function create_forum($category, $orderID, $title, $description, $is_dev_blog=0, $post_min_total_credit=0) {
    $db = BoincDB::get();
    $title_escaped = BoincDb::escape_string($title);
    $description_escaped = BoincDb::escape_string($description);
    $q = "(category, orderID, title, description, is_dev_blog, post_min_total_credit) " .
        "values ($category, $orderID, '$title_escaped', '$description_escaped', $is_dev_blog, $post_min_total_credit)";
    // See create_category()'s comment: modern PHP/mysqli throws on a failed
    // query by default, so the duplicate-key case has to be caught here too.
    $result = false;
    $insert_error = "";
    try {
        $result = $db->insert("forum", $q);
    } catch (\mysqli_sql_exception $e) {
        $insert_error = $e->getMessage();
    }
    if (!$result) {
        $forum = BoincForum::lookup("category=$category and title='$title_escaped'");
        if ($forum) return $forum->id;
        echo "can't create forum\n";
        echo $insert_error ?: $db->base_error();
        exit();
    }
    return $db->insert_id();
}

db_init();

$catid = create_category(0, "", 0);
create_forum($catid, 0, "News", "News from this project", 1);
create_forum($catid, 1, "Science & Results", "Discussion of the search: found loops, long games, and the project's science", 0, 1);
create_forum($catid, 2, "Number crunching", "Credit, leaderboards, CPU performance", 0, 1);
create_forum($catid, 3, "Cafe", "Meet and greet other participants", 0, 1);

$catid = create_category(1, "Getting started", 1);
create_forum($catid, 0, "Help & Troubleshooting", "Installing and running BOINC, account questions, and any other issues");

?>
