<?php
// Two things live in this one file, both using the exact same mechanism:
// project-specific translation strings (tra() calls in our own code, e.g.
// tools/project_description.php) that don't exist in BOINC's own compiled
// translations at all, and corrections to strings that DO exist there but
// shipped broken. Deployed by tools.sh alongside a one-line addition to
// html/inc/translation.inc (tools/languages/translation.inc in this same
// directory) that require_once()s this file right after that file's own
// loop finishes loading every compiled language file for the current
// request -- so entries here simply add to, or overwrite specific keys
// in, $language_lookup_array. Everything else in every language keeps
// coming from upstream untouched, including through a future BOINC
// version bump: unlike tracking whole compiled files (which would
// silently re-freeze every language at today's snapshot on every future
// bump), this only ever touches the exact strings listed here.
//
// Add an entry keyed exactly like the compiled files
// ($language_lookup_array["<locale>"]["<exact English source string>"] =
// "<translation>";) whenever a real broken string is found and confirmed
// against the live site, or a new tra()-wrapped string of ours needs a
// translation -- don't add speculative/unverified fixes. If BOINC_COMMIT
// is ever bumped, re-check whether upstream has already fixed the same
// bug in the new snapshot -- if so, that particular entry just becomes a
// harmless no-op (same value either way), not something that needs
// removing.

//-------------- Our own strings (not in BOINC's translations at all)

// tools/project_description.php's homepage blurb.
$language_lookup_array["it_IT"]["Camicia is a distributed-computing project that exhaustively searches every distinct deal of a 52-card deck, looking for games of Beggar-My-Neighbour (known in Italy as Camicia) that loop forever, or that take unusually long to end. Every volunteer's computer tests a different slice of the deck, so together we cover every possible deal exactly once."] = "Camicia è un progetto di calcolo distribuito che analizza in modo esaustivo ogni possibile smazzata di un mazzo di 52 carte, alla ricerca di partite a Beggar-My-Neighbour (il gioco della Camicia) che non finiscono mai o che durano insolitamente a lungo. Il computer di ogni volontario analizza una porzione diversa del mazzo, così da coprire ogni smazzata possibile esattamente una volta.";

//-------------- Corrections to strings shipped broken upstream

// it_IT -- found 2026-08-20, confirmed against the live site:
// - a stray literal "1" left sitting in front of the %1 placeholder
//   (translator typo), rendering as e.g. "1Accedi" instead of a plain
//   "Accedi" link.
// - "%s" used instead of BOINC's own %1/%2/... convention -- tra()'s own
//   substitution (html/inc/translation.inc) only understands %1/%2/etc,
//   so a literal "%s" never gets replaced and shows through as-is.
$language_lookup_array["it_IT"]["Already joined? %1Log in%2."] = "Già effettuato l'accesso? %1Accedi%2.";
$language_lookup_array["it_IT"]["About %1"] = "Informazioni su %1";
