<?php
// Included directly by html/user/index.php (see the file_exists() check
// there) inside a <p>...</p> it wraps around this output -- so this file
// should just print, not define a function or echo its own <p> tags.
// Wrapped in tra() so a translation can be added the same way any other
// string is: an entry in tools/languages/translation_fixes.inc, keyed on
// this exact English text.
echo tra("Camicia is a distributed-computing project that exhaustively searches every distinct deal of a 52-card deck, looking for games of Beggar-My-Neighbour (known in Italy as Camicia) that loop forever, or that take unusually long to end. Every volunteer's computer tests a different slice of the deck, so together we cover every possible deal exactly once.");
