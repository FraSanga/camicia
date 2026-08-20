# Database migrations

One-off scripts that make a schema/data change to the *live* project database,
outside the normal `tools/tools.sh` deploy loop. Everything else under
`tools/` (worker/assimilator/work_generator, `config.xml`, `project.inc`,
etc.) gets redeployed on every `tools.sh` run; these do not, because they're
not idempotent app/config pushes -- they're one-time structural changes to
data that already exists.

This is a distinct category from `tools/html/ops/create_forums.php`, which is also a
manually-run, not-auto-deployed script, but seeds *new* rows (forum
categories) rather than altering the shape of existing tables. Both share
the same "deploy by hand, then run by hand, once" convention documented in
each script's own header comment.

## Convention

- One file per migration, named `NNNN_short_description.sh`, numbered in the
  order they were written/applied. Zero-padded to 4 digits.
- Each script's own header comment documents: what it changes, why, the
  exact `docker cp`/`chown`/`chmod` deploy commands, and any preconditions
  (backup first, stop daemons first, etc.) specific to that migration.
- Migrations are meant to be safe to re-run (check the current state before
  altering, skip anything already applied) where practical, so a
  partially-applied run can just be re-run after fixing whatever stopped it.
- Nothing here contains secrets -- these are safe to be public, same as the
  rest of `tools/`.

## Applying to a freshly bootstrapped project

If you ever rebuild this project from scratch (new hardware, disaster
recovery -- see the project's `DEPLOYMENT.md`), run every migration here, in
order, once, after `make_project` and the first `tools/tools.sh` deploy, and
before the project goes live / real volunteers attach. On an existing
project that's already had some of these applied, only run the ones not yet
marked done below.

## Log

| # | Name | What | Why | Applied to production |
|---|------|------|-----|------------------------|
| 0001 | [`widen_workunit_result_ids`](0001_widen_workunit_result_ids.sh) | Widens `workunit.id`, `result.id`, and their known cross-reference columns (`result.workunitid`, `workunit.canonical_resultid`, `assignment.workunitid`, `assignment.resultid`) from 32-bit `int` to 64-bit `bigint unsigned` | Stock BOINC's IDs are signed 32-bit AUTO_INCREMENT (~2.147 billion ceiling); Camicia's own workload needs ~6.5×10¹¹ workunits to fully cover its index space -- ~300x that ceiling. Pre-launch, before any real volunteer data exists, is the only time this is a cheap fix. | **Applied 2026-08-16** -- verified locally first, then on production: row counts/AUTO_INCREMENT counters preserved exactly, daemons resumed cleanly |
