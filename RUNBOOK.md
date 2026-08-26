# Runbook

Skeleton only -- sections to fill in later, not yet written. Distinct from
the personal deployment notes referenced elsewhere in this repo
(`DEPLOYMENT.md`), which stay off-repo; this is meant to be the versioned,
in-repo operational reference for anyone (including a future you) who
needs to react to an incident without already holding all the context in
their head.

## Disaster recovery: rebuilding the project from scratch on a new server

<!-- TODO: tie together db_backup.sh / backup_offsite_gdrive.sh /
     backup_usb.sh / tools/migrations into one ordered procedure. -->

## Restoring from a backup

<!-- TODO: how to actually decompress/restore a db_backups/*.sql.gz or a
     results/*.gz segment, and how to verify the restore succeeded. -->

## Rolling back a bad deploy

<!-- TODO: reference tools/deploy_rollback.sh and tools/publish_version.sh
     -- what's automatic already, what still needs a human. -->

## Daemon is down / crash-looping

<!-- TODO: diagnosis steps beyond what daemon_health_check.sh already
     automates. -->

## Database issues

<!-- TODO: common failure modes, how to check replication/corruption,
     when to restore vs. repair in place. -->

## Rotating/losing a secret or key

<!-- TODO: code_sign_private, upload_private, OPS_PASS, DB credentials --
     what actually needs re-issuing vs. re-deploying. -->

## Who to contact

<!-- TODO -->
