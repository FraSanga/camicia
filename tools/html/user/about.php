<?php
// About page -- linked from the navbar (html/inc/bootstrap.inc's "About %1"
// entry) but never shipped by stock BOINC; projects are expected to create
// it themselves. Was 404ing until this was added. Structured the same way
// as BOINC's own info.php (page_head/page_tail wrapper, tra()-wrapped
// content with PROJECT/COPYRIGHT_HOLDER substitution) rather than as a
// bare include like project_description.php, since this is a standalone
// page reached by its own link, not something embedded inside another
// page's markup.

require_once('../inc/util.inc');
require_once('../inc/translation.inc');

check_get_args(array());

page_head(tra("About %1", PROJECT));

echo "
<p>".tra("%1 is a %2BOINC%3 distributed-computing project. It exhaustively searches every distinct deal of a 52-card deck, looking for games of Beggar-My-Neighbour, known in Italy as %1, that loop forever, or that take unusually long to end.", PROJECT, "<a href=\"https://boinc.berkeley.edu\">", "</a>")."

<h3>".tra("Why search for this?")."</h3>
<p>".tra("Beggar-My-Neighbour looks like a simple children's game, but whether every possible deal is guaranteed to eventually end is an open question in mathematics. %1 checks every distinct deal exactly once, distributing the work across volunteers' computers, to help find out.", PROJECT)."

<h3>".tra("What we've found so far")."</h3>
<p>".tra("Most deals finish, some taking thousands of tricks. In 2024, %1 found the first confirmed game that loops forever: a deal that returns to an identical board state after 474 cards played, meaning it can never end.", PROJECT)."

<h3>".tra("Who runs %1?", PROJECT)."</h3>
<p>".tra("%1 is developed and run by %2.", PROJECT, COPYRIGHT_HOLDER)."
";

page_tail();
?>
