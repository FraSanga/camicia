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

// tools/about.php
$language_lookup_array["it_IT"]["%1 is a %2BOINC%3 distributed-computing project. It exhaustively searches every distinct deal of a 52-card deck, looking for games of Beggar-My-Neighbour, known in Italy as %1, that loop forever, or that take unusually long to end."] = "%1 è un progetto di calcolo distribuito %2BOINC%3. Analizza in modo esaustivo ogni possibile smazzata di un mazzo di 52 carte, alla ricerca di partite a Beggar-My-Neighbour, conosciuto in Italia come %1, che non finiscono mai o che durano insolitamente a lungo.";
$language_lookup_array["it_IT"]["Why search for this?"] = "Perché cercare questo?";
$language_lookup_array["it_IT"]["Beggar-My-Neighbour looks like a simple children's game, but whether every possible deal is guaranteed to eventually end is an open question in mathematics. %1 checks every distinct deal exactly once, distributing the work across volunteers' computers, to help find out."] = "Beggar-My-Neighbour sembra un semplice gioco per bambini, ma se ogni possibile smazzata sia garantita a finire prima o poi è una domanda ancora aperta in matematica. %1 analizza ogni smazzata possibile esattamente una volta, distribuendo il lavoro tra i computer dei volontari, per aiutare a scoprirlo.";
$language_lookup_array["it_IT"]["What we've found so far"] = "Cosa abbiamo scoperto finora";
$language_lookup_array["it_IT"]["Most deals finish, some taking thousands of tricks. In 2024, %1 found the first confirmed game that loops forever: a deal that returns to an identical board state after 474 cards played, meaning it can never end."] = "La maggior parte delle smazzate finisce, alcune dopo migliaia di prese. Nel 2024, %1 ha trovato la prima partita confermata che non finisce mai: una smazzata che torna a uno stato identico dopo 474 carte giocate, il che significa che non può mai terminare.";
$language_lookup_array["it_IT"]["Who runs %1?"] = "Chi gestisce %1?";
$language_lookup_array["it_IT"]["%1 is developed and run by %2."] = "%1 è sviluppato e gestito da %2.";

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
