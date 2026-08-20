<?php
// Camicia-specific corrections to strings shipped broken in BOINC's own
// compiled translation files (html/languages/compiled/*.po.inc, generated
// once at `make_project` bootstrap from whatever BOINC version is pinned --
// see images/server/Dockerfile's BOINC_COMMIT -- and never regenerated
// since). Deployed by tools.sh alongside a one-line addition to
// html/inc/translation.inc (tools/languages/translation.inc in this same
// directory) that require_once()s this file right after that file's own
// loop finishes loading every compiled language file for the current
// request -- so entries here simply overwrite the specific broken keys.
// Everything else in every language keeps coming from upstream untouched,
// including through a future BOINC version bump: unlike tracking whole
// compiled files (which would silently re-freeze every language at
// today's snapshot on every future bump), this only ever touches the
// exact strings listed here.
//
// Add an entry keyed exactly like the compiled files
// ($language_lookup_array["<locale>"]["<exact English source string>"] =
// "<corrected translation>";) whenever a real broken string is found and
// confirmed against the live site -- don't add speculative/unverified
// fixes. If BOINC_COMMIT is ever bumped, re-check whether upstream has
// already fixed the same string in the new snapshot -- if so, the entry
// here just becomes a harmless no-op (same value either way), not
// something that needs removing.

// it_IT -- found 2026-08-20, confirmed against the live site:
// - a stray literal "1" left sitting in front of the %1 placeholder
//   (translator typo), rendering as e.g. "1Accedi" instead of a plain
//   "Accedi" link.
// - "%s" used instead of BOINC's own %1/%2/... convention -- tra()'s own
//   substitution (html/inc/translation.inc) only understands %1/%2/etc,
//   so a literal "%s" never gets replaced and shows through as-is.
$language_lookup_array["it_IT"]["Already joined? %1Log in%2."] = "Già effettuato l'accesso? %1Accedi%2.";
$language_lookup_array["it_IT"]["About %1"] = "Informazioni su %1";
