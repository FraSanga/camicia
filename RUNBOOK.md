# Runbook

The versioned, in-repo operational reference for anyone (including a future you) who needs to react
to an incident without already holding all the context in their head.

## Contents

- [Alerts: what to do for each one](#alerts-what-to-do-for-each-one)
- [Disaster recovery: rebuilding the project from scratch on a new server](#disaster-recovery-rebuilding-the-project-from-scratch-on-a-new-server)
  - [0. What you need before starting](#0-what-you-need-before-starting)
  - [1. Recreate `.env`](#1-recreate-env)
  - [2. Recover `keys/`](#2-recover-keys)
  - [3. Bring the stack up and bootstrap the project](#3-bring-the-stack-up-and-bootstrap-the-project)
  - [4. Recover the database content](#4-recover-the-database-content)
  - [5. Recover `results/`](#5-recover-results)
  - [6. Deploy](#6-deploy)
  - [7. Re-run the ID-width migration](#7-re-run-the-id-width-migration)
  - [8. What you do *not* need to do, because the dump already covers it](#8-what-you-do-not-need-to-do-because-the-dump-already-covers-it)
  - [9. Verify, don't assume](#9-verify-dont-assume)
  - [10. Setting up rclone access to Google Drive](#10-setting-up-rclone-access-to-google-drive)
- [Staging: tearing down and rebuilding](#staging-tearing-down-and-rebuilding)
  - [1. Tear down the stack and its data](#1-tear-down-the-stack-and-its-data)
  - [2. Bring the stack back up on the latest code](#2-bring-the-stack-back-up-on-the-latest-code)
  - [3. Fix ownership on the fresh bind mounts](#3-fix-ownership-on-the-fresh-bind-mounts)
  - [4. Bootstrap a fresh BOINC project](#4-bootstrap-a-fresh-boinc-project)
  - [5. Deploy the app](#5-deploy-the-app)
  - [6. Re-run the ID-width migration](#6-re-run-the-id-width-migration)
  - [7. Create the forums](#7-create-the-forums)
  - [8. Verify, don't assume](#8-verify-dont-assume)
- [Restoring from a backup](#restoring-from-a-backup)
  - [Restoring the database](#restoring-the-database)
  - [Recovering a results segment](#recovering-a-results-segment)
- [Rolling back a bad deploy](#rolling-back-a-bad-deploy)
  - [Already automatic](#already-automatic)
  - [If the automatic rollback itself failed](#if-the-automatic-rollback-itself-failed)
  - [Rolling back a deploy discovered bad after the fact](#rolling-back-a-deploy-discovered-bad-after-the-fact)
  - [If another deploy has already happened since the bad one](#if-another-deploy-has-already-happened-since-the-bad-one)
- [Daemon is down / crash-looping](#daemon-is-down--crash-looping)
  - [1. Read that daemon's own log first](#1-read-that-daemons-own-log-first)
  - [2. Match what it says against causes actually seen on this project](#2-match-what-it-says-against-causes-actually-seen-on-this-project)
  - [3. Confirm it actually stays up](#3-confirm-it-actually-stays-up)
- [Stopping a daemon for maintenance](#stopping-a-daemon-for-maintenance)
  - [1. Disable it in `config.xml` first](#1-disable-it-in-configxml-first)
  - [2. Stop the process itself](#2-stop-the-process-itself)
  - [3. Bring it back afterward](#3-bring-it-back-afterward)
  - [A side effect worth knowing](#a-side-effect-worth-knowing)
- [Database issues](#database-issues)
  - [1. Confirm it's actually the database](#1-confirm-its-actually-the-database)
  - [2. Disk space](#2-disk-space)
  - [3. Corruption after an unclean shutdown](#3-corruption-after-an-unclean-shutdown)
  - [4. Schema limits](#4-schema-limits)
- [External service problems](#external-service-problems)
- [Rotating/losing a secret or key](#rotatinglosing-a-secret-or-key)
  - [`code_sign_private` (signs every app version clients trust)](#code_sign_private-signs-every-app-version-clients-trust)
  - [`upload_private`](#upload_private)
  - [`OPS_PASS` (the ops panel's HTTP Basic Auth password)](#ops_pass-the-ops-panels-http-basic-auth-password)
  - [Database credentials](#database-credentials)
  - [Google Drive rclone connection (`keys/rclone.conf`)](#google-drive-rclone-connection-keysrcloneconf)

## Alerts: what to do for each one

Every push notification this project can send goes through `bin/notify.sh` to the ntfy.sh topic
in `NTFY_TOPIC`. This is every title that function is ever called with, grouped by the script that
sends it, so a notification on your phone can go straight to an action instead of you having to
find and read the source first.

**Disk and memory** (`disk_space_check.sh` / `memory_check.sh`, both daily):
- **Camicia: disk default** / **Camicia: disk high**: filesystem backing the project dir is at
  80%+/90%+ used. `db_backup.sh`, `rotate_results.sh`, and `rotate_daemon_logs.sh` all already
  bound their own growth (7 kept dumps, 14 kept log rotations), but rotated `results/results_*.gz`
  segments and `db_purge`'s `archives/` (both kept forever by design) are the most likely genuine
  cause on a long-running project. SSH in, `df -h`, then `du -sh results/ db_backups/ archives/
  log_*` to see what actually grew. In a genuine emergency, `archives/` (unlike `results/`, never
  delete that) can be safely pruned once `backup_usb.sh`/`backup_offsite_gdrive.sh` have a copy:
  nothing in this project or in BOINC's own source ever reads it back.
- **Camicia: memory default** / **Camicia: memory high**: host is approaching the thresholds where
  `earlyoom` starts killing processes (configured via `/etc/default/earlyoom`: SIGTERM at ≤5% mem
  and ≤10% swap, SIGKILL at ≤2.5% mem and ≤5% swap; it's set to avoid killing `mariadbd`/`mysqld`
  and prefer killing compiler processes first, so this check's own thresholds, ≤20%/≤10%, are meant
  to give a heads-up before that ever triggers). `free -h` and `docker stats` to see what's using
  it; `journalctl -u earlyoom` to check whether it's already acted.

**Backups** (all daily):
- **Camicia: DB backup failed** (`db_backup.sh`): `mysqldump` or `gzip` failed; the message names
  the actual error. Re-run `bin/db_backup.sh` by hand inside the container to see it live; the disk
  alert above and the DB container being unreachable are the two most likely causes.
- **Camicia: USB backup failed** (`backup_usb.sh`): mirroring `keys/`, `db_backups/`, `results/` to
  the physically separate USB drive failed. It skips silently (no alert) if the drive just isn't
  mounted, so a real failure means it was working before. Check the mount, then re-run by hand.
- **Camicia: offsite backup failed** (`backup_offsite_gdrive.sh`): the `rclone` sync to Google
  Drive failed, most often an expired OAuth token. See "Rotating/losing a secret or key" →
  [Google Drive rclone connection](#google-drive-rclone-connection-keysrcloneconf) below.

**Rotation** (both daily):
- **Camicia: results rotation failed** (`rotate_results.sh`): the `mv` or the `gzip` of
  `results/results.txt` failed; the uncompressed file is left in place either way and retried
  automatically the next day. Check disk space first (see above); the message names the exact file.
- **Camicia: daemon log rotation failed** (`rotate_daemon_logs.sh`): same failure, for one daemon's
  debug log under `log_<hostname>/`. Not urgent: the daemon's own file descriptor is unaffected;
  worst case that log grows past its threshold again tomorrow.

**Daemons** (`daemon_health_check.sh`, periodic):
- **Camicia: daemon(s) restarted** (default priority): one or more of the 6 core daemons were down
  and `bin/start` brought them back automatically. Not urgent, but a daemon that keeps dying and
  quietly restarting is a symptom worth tracking: check `daemon_health_alerts.log` for a repeating
  pattern. See [Daemon is down / crash-looping](#daemon-is-down--crash-looping) below.
- **Camicia: daemon(s) DOWN** (high priority): still missing after `bin/start` was already tried
  automatically. Needs manual intervention now; see
  [Daemon is down / crash-looping](#daemon-is-down--crash-looping) below.

**Deploys** (`tools.sh` / `deploy_rollback.sh`):
- **Camicia: deploy FAILED**: the deploy failed before any pre-deploy backup was taken, and the
  automatic fallback already restarted the daemons on the unmodified pre-deploy state, so nothing
  was actually changed. Nothing urgent for the running project; read the GitHub Actions run's log
  for the real error under the stage the notification names, fix it, and redeploy.
- **Camicia: deploy FAILED, daemons DOWN**: same as above, but the automatic daemon restart also
  failed. Urgent: run
  `docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c 'cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/start'`
  by hand first, then debug the deploy failure.
- **Camicia: deploy rolled back** / **deploy rolled back + republished** (default priority): a
  deploy failed partway *after* a backup was taken, and the automatic rollback fully succeeded, with
  or without needing to publish a replacement app version. No action needed; the message says
  exactly what was restored.
- **Camicia: rollback PARTIAL** / **rollback INCOMPLETE** / **rollback INCOMPLETE, daemons DOWN** /
  **rollback FAILED** (high priority): the automatic rollback itself hit a problem, ranging from no
  pre-deploy backup existing to a replacement app version failing to publish. Always urgent and
  always manual; each variant needs something different, so read the notification body itself
  before acting. See [Rolling back a bad deploy](#rolling-back-a-bad-deploy) below.

## Disaster recovery: rebuilding the project from scratch on a new server

Use this when the production machine itself is gone (hardware failure, fire/theft, anything that
takes the whole box) and you're standing up a replacement from backups. This is **restoring real
data**, not resetting to empty. If you actually want to wipe everything and start over (e.g. before
a public launch), that's a different, deliberately destructive procedure, not this one.

Read this whole section once before running anything: steps 4 and 5 both depend on which backups
actually survived, and that changes what you do next.

### 0. What you need before starting

- A fresh Linux host (production currently runs Ubuntu) with Docker and Docker Compose installed.
- This git repo, cloned to `~/camicia` specifically: `deploy.yml` hardcodes that path (`cd
  ~/camicia`), so it needs to match exactly, not just "cloned somewhere."
- This host registered as a GitHub Actions self-hosted runner with the `production` label
  (`runs-on: [self-hosted, production]` in `deploy.yml`), installed as a systemd service so it
  survives a reboot. GitHub generates the exact registration commands (including a short-lived
  token) fresh each time from the repo's Settings > Actions > Runners > New self-hosted runner.
- At least one of these three ways to recover `.env`/`keys/`/`db_backups/`:
  - Physical access to the offline `.env` USB stick (see
    [Rotating/losing a secret or key](#rotatinglosing-a-secret-or-key) below for the mount recipe)
  - A Google account able to sign in to the Google Drive holding the offsite backup
  - The local `backup1tb` USB drive, if it physically survived

You do not need all three; see step 2's branches.

### 1. Recreate `.env`

Without this, nothing else below works: `docker-compose.yml` and every script under `tools/` read
it directly.

`env_latest` only exists on the stick because `tools/backup_env_to_usb.sh` was run by hand after
the last real `.env` change. It's deliberately never automated: a GitHub Actions compromise already
has direct filesystem access to the live `.env` (`deploy.yml` runs on this same box via a
self-hosted runner), so an automated, network-reachable backup of it would protect against
approximately nothing from that direction; a USB stick that's only ever plugged into this one
machine sidesteps that instead. Keep it current going forward, using the same mount point as below:

```bash
tools/backup_env_to_usb.sh /mnt/env_backup
```

- **If the offline USB stick survived**: mount it and copy `env_latest` to the repo root as `.env`.
  Confirm the device name first: it will vary depending on the machine.

  ```bash
  lsblk
  sudo mount -o uid=1000,gid=1000 /dev/<device> /mnt/env_backup
  cp /mnt/env_backup/env_latest .env
  ```

- **If it didn't**: `.env` itself is not recoverable from any of the other backups (see
  [Rotating/losing a secret or key](#rotatinglosing-a-secret-or-key) below). Reconstruct it from
  `.env.example`'s variable names.
  Each one falls into one of four cases:
  - **Freely regenerated, nothing external depends on the old value**: `MARIADB_ROOT_PASSWORD`,
    `MARIADB_PASSWORD`, `OPS_PASS`, and `NTFY_TOPIC` (just a random string with no account behind
    it at all; regenerate it and resubscribe your phone to the new topic).
  - **Recoverable by logging into the external account that issued it**: `CLOUDFLARE_TUNNEL_TOKEN`
    (Cloudflare dashboard), `SMTP_*` (your SMTP provider), `RECAPTCHA_SITE_KEY`/
    `RECAPTCHA_SECRET_KEY` (Google reCAPTCHA admin console), and `AKISMET_KEY` (your Akismet
    account).
  - **`CODE_SIGN_KEY_PASSPHRASE`: leave it blank.** It's a GitHub Actions `production`-Environment
    secret, write-only, confirmed via `gh secret list`, no login anywhere reveals it. Production's
    `.env` was never meant to carry a real value here either way; a non-blank one would actually
    overwrite the value `deploy.yml` already injects correctly on every real deploy. (A real value
    here is only useful for local dev, to test the encrypted-key flow instead of the plaintext-key
    convenience mode.)
  - **Genuinely unrecoverable**: `CERT_VERIFY_SECRET`. Regenerating it means every certificate
    issued before the loss stops validating on `verify_cert.php`.

  `SERVER_VOLUME_PROJECTS`, `SERVER_VOLUME_PROJECTS_DIR`, `SERVER_VOLUME_KEYS`, and
  `SERVER_VOLUME_KEYS_DIR` fall outside all three cases above: they are not secrets, just the host
  and container paths for the two bind mounts in `docker-compose.yml`. One constraint applies when
  choosing them: `SERVER_VOLUME_KEYS` must not be a subdirectory of `SERVER_VOLUME_PROJECTS`.
  `docker-compose.yml` binds them as two independent mounts specifically so that step 3's
  `--delete_prev_inst` wipes `SERVER_VOLUME_PROJECTS` without also wiping `SERVER_VOLUME_KEYS`;
  nesting one inside the other would defeat that.

  `SERVER_VOLUME_PROJECTS_DIR` and `SERVER_VOLUME_KEYS_DIR` are the container side of the same two
  mounts: the paths `SERVER_VOLUME_PROJECTS` and `SERVER_VOLUME_KEYS` appear at once inside the
  container. No constraint applies to them: whatever path you pick, `fix_permissions.sh` changes
  its ownership to `<PROJECTS_USER>` on every container start and every `tools.sh` deploy, so
  permissions are never a concern.

### 2. Recover `keys/`

Two independent sources; use whichever survived. Both need to end up populated with
`code_sign_private.gpg`, `code_sign_public`, `upload_private.gpg`, `upload_public`.

- **From the local `backup1tb` USB drive**: no credentials needed.

  ```bash
  lsblk
  sudo mkdir -p /mnt/backup1tb
  sudo mount -o ro /dev/<device> /mnt/backup1tb
  cp -r /mnt/backup1tb/keys ./keys
  ```

- **From Google Drive**, if it didn't survive: see "Setting up rclone access to Google Drive"
  below, then:

  ```bash
  rclone copy --config rclone.conf camicia-gdrive:camicia-backup/keys ./keys
  ```

Neither `code_sign_private` nor `upload_private` can be decrypted by hand: their passphrases only
exist as GitHub Actions secrets, never retrievable once set. Both only become usable through an
actual `deploy.yml` run, not a manual `./tools.sh`. The difference is what happens afterward:
`upload_private` stays decrypted on disk continuously (see
[Rotating/losing a secret or key](#rotatinglosing-a-secret-or-key) below for why), while
`code_sign_private` goes right back to being encrypted at rest once that run's
`update_versions` step is done with it.

### 3. Bring the stack up and bootstrap the project

```bash
docker compose up -d --build
```

Then bootstrap the project. Replace every `<...>` placeholder with the real value from your
recovered `.env`:

```bash
docker exec -it -u <PROJECTS_USER> -e USER=<PROJECTS_USER> <SERVER_CONTAINER_NAME> \
    /usr/local/src/boinc/tools/make_project \
    --srcdir /usr/local/src/boinc \
    --project_root <SERVER_VOLUME_PROJECTS_DIR>/camicia \
    --key_dir <SERVER_VOLUME_KEYS_DIR> \
    --url_base http://<DOMAIN> \
    --delete_prev_inst \
    --drop_db_first \
    --db_host <DATABASE_CONTAINER_NAME> \
    --db_user root \
    --db_pass <MARIADB_ROOT_PASSWORD> \
    --db_name <MARIADB_DATABASE> \
    camicia
```

`--key_dir` must point at the `keys/` you recovered in step 2, not the default location inside
`--project_root`: `make_project` finds the existing keys there and reuses them instead of
generating new ones. `--delete_prev_inst` wipes and recreates the entire `<SERVER_VOLUME_PROJECTS_DIR>/camicia` tree.

### 4. Recover the database content

`db_backups/` lives inside the project tree, at `<SERVER_VOLUME_PROJECTS>/camicia/db_backups/`,
not at the repo root. Get the newest `*.sql.gz` in there, whichever source has it:

- **From the local `backup1tb` USB drive**: no credentials needed.

  ```bash
  lsblk
  sudo mkdir -p /mnt/backup1tb
  sudo mount -o ro /dev/<device> /mnt/backup1tb
  mkdir -p <SERVER_VOLUME_PROJECTS>/camicia/db_backups
  cp /mnt/backup1tb/db_backups/<newest>.sql.gz <SERVER_VOLUME_PROJECTS>/camicia/db_backups/
  ```

- **From Google Drive**, if it didn't survive: see "Setting up rclone access to Google Drive"
  below, then:

  ```bash
  rclone copy --config rclone.conf camicia-gdrive:camicia-backup/db_backups <SERVER_VOLUME_PROJECTS>/camicia/db_backups
  ```

Then restore it:

```bash
zcat <SERVER_VOLUME_PROJECTS>/camicia/db_backups/<newest>.sql.gz | docker exec -i <DATABASE_CONTAINER_NAME> mariadb \
    -uroot -p<MARIADB_ROOT_PASSWORD> <MARIADB_DATABASE>
```

### 5. Recover `results/`

Same as `db_backups/` above: this lives at `<SERVER_VOLUME_PROJECTS>/camicia/results/`, not the
repo root. Plain files, no restore process needed, just get the newest copy in there:

- **From the local `backup1tb` USB drive**: no credentials needed.

  ```bash
  lsblk
  sudo mkdir -p /mnt/backup1tb
  sudo mount -o ro /dev/<device> /mnt/backup1tb
  cp -r /mnt/backup1tb/results <SERVER_VOLUME_PROJECTS>/camicia/results
  ```

- **From Google Drive**, if it didn't survive: see "Setting up rclone access to Google Drive"
  below, then:

  ```bash
  rclone copy --config rclone.conf camicia-gdrive:camicia-backup/results <SERVER_VOLUME_PROJECTS>/camicia/results
  ```

### 6. Deploy

Trigger the real `deploy.yml` workflow:

```bash
gh workflow run deploy.yml --ref main
```

This deploys all app code/config and re-injects every secret from the `.env` you recovered in
step 1.

### 7. Re-run the ID-width migration

Safe to run regardless of whether your restored dump already has the widened columns: it checks
current state before altering, and no-ops if already applied.

```bash
docker cp tools/migrations/0001_widen_workunit_result_ids.sh <SERVER_CONTAINER_NAME>:<SERVER_VOLUME_PROJECTS_DIR>/camicia/bin/
docker exec <SERVER_CONTAINER_NAME> chown <PROJECTS_USER>:<PROJECTS_USER> <SERVER_VOLUME_PROJECTS_DIR>/camicia/bin/0001_widen_workunit_result_ids.sh
docker exec <SERVER_CONTAINER_NAME> chmod +x <SERVER_VOLUME_PROJECTS_DIR>/camicia/bin/0001_widen_workunit_result_ids.sh
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia/bin && ./0001_widen_workunit_result_ids.sh"
```

### 8. What you do *not* need to do, because the dump already covers it

Unlike a from-scratch reset (a different, deliberately destructive procedure), restoring a real
backup dump already brings back forum categories/posts, the `ENROLL`/`STATSEXPORT` consent flags,
and every other DB-level setting exactly as they were. `create_forums.php` and
`manage_consent_types.php` are for a dump-less fresh install, not this.

### 9. Verify, don't assume

Both services healthy:

```bash
docker compose ps
```

All 6 daemons running (the same check `deploy.yml`'s own post-deploy health check does):

```bash
for d in assimilator sample_bitwise_validator work_generator feeder transitioner file_deleter; do
    docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> pgrep -f "^$d " > /dev/null \
        && echo "OK: $d" || echo "MISSING: $d is not running"
done
```

The site actually loads, and a login with a real pre-existing account works (proves the restored DB
is really being read, not just present).

`results.txt` was really restored, not just left empty by `make_project`:

```bash
tail -5 <SERVER_VOLUME_PROJECTS>/camicia/results/results.txt
```

Check the lines look like real, well-formed result entries (not empty, not truncated mid-line), and
that the file's size/modification time match the backup you actually restored, not a fresh empty
one `make_project` may have created before step 5 copied the real one into place.

If this is a genuine full rebuild, run a real end-to-end client test before calling it done, not
just confirming the daemons started:

```bash
docker run -d --name linux_user --net host boinc/client
docker exec linux_user boinccmd --project_attach http://<DOMAIN>/camicia <user-token>
docker exec linux_user boinccmd --project http://<DOMAIN>/camicia update
docker logs -f linux_user
```

Confirm the client actually fetches, computes, and reports a workunit successfully, not just that
it attaches.

### 10. Setting up rclone access to Google Drive

Referenced from steps 2, 4, and 5 above whenever the local `backup1tb` copy didn't survive and
Google Drive is the only remaining source. `rclone` is only installed *inside* the `boinc_server`
container image, not on the host, so a fresh host needs it installed here first (official install
script, same one `images/server/Dockerfile` uses to build the container image itself):

```bash
curl -fsSL https://rclone.org/install.sh | sudo bash
```

Two files, both required together, neither useful alone:

- **`rclone.conf`** is the actual remote configuration (the `camicia-gdrive` remote definition and
  its OAuth token), encrypted at rest. Its raw content starts with `# Encrypted rclone
  configuration File` / `RCLONE_ENCRYPT_V0:`, not a readable config file.
- **`rclone_config_pass`** is the plain-text passphrase that decrypts it, nothing else. On its own
  it decrypts nothing.

Both live in `keys/` normally, so if you already recovered `keys/` in step 2, you already have
both. If `rclone` runs against `rclone.conf` without the passphrase supplied, it hangs waiting for
an interactive password prompt instead of failing cleanly. Always supply it via the
`RCLONE_CONFIG_PASS` environment variable, not typed at a prompt:

```bash
export RCLONE_CONFIG_PASS=$(cat keys/rclone_config_pass)
```

After that, every `rclone ... --config keys/rclone.conf camicia-gdrive:camicia-backup/...` command
in steps 2, 4, and 5 above works normally.

**If `rclone.conf` and `rclone_config_pass` are also lost** (e.g. they only ever existed on the lost
box, and the local `backup1tb` copy didn't survive either): rebuild both from scratch. You need
access to the Google Cloud Console project this project's Drive backups use, but not the exact
original OAuth client credentials: Google Drive tracks file access at the Cloud Console project
level, not per individual client, so a freshly created client under the same project can still see
the existing `camicia-backup` folder.

Create a new OAuth 2.0 Client ID in that Cloud Console project (APIs & Services > Credentials), or
reuse an existing one if you still have its client ID and secret.

Run this in a real, interactive terminal, not a script: it starts a local web server and waits for
you to complete the Google login in a browser, so it needs to stay running for as long as that
takes.

```bash
rclone authorize "drive" "<CLIENT_ID>" "<CLIENT_SECRET>" --drive-scope drive.file
```

Open the printed link, log in with the Google account this project's Drive backups live under, and
approve access. `rclone` then prints a JSON token blob.

Save that token into a file exactly as printed, not a shell variable: pasting it inline on a
command line lets the shell strip its quotes and silently corrupts it.

```bash
cat > /tmp/rclone_token.json << 'EOF'
<paste the printed {...} blob here>
EOF
```

Create `keys/rclone.conf` from it.

```bash
rclone config create camicia-gdrive drive \
    client_id="<CLIENT_ID>" client_secret="<CLIENT_SECRET>" \
    scope=drive.file token="$(cat /tmp/rclone_token.json)" \
    --config keys/rclone.conf --non-interactive
rm /tmp/rclone_token.json
```

Generate a new passphrase and encrypt the config with it:

```bash
openssl rand -base64 32 > keys/rclone_config_pass
rclone config encryption set --config keys/rclone.conf --password-command "cat keys/rclone_config_pass"
```

This new passphrase must also replace the `RCLONE_CONFIG_PASS` GitHub Actions `production`
Environment secret. Otherwise the next `deploy.yml` run (step 6) overwrites `keys/rclone_config_pass`
with the old, now-wrong value.

Confirm it actually works before relying on it: this should list `camicia-backup` without asking
for anything else.

```bash
export RCLONE_CONFIG_PASS=$(cat keys/rclone_config_pass)
rclone lsd --config keys/rclone.conf camicia-gdrive:
```

## Staging: tearing down and rebuilding

Use this to reset the staging environment to a blank slate: wipes its database, code-signing keys,
and all project data. Everything else needed to reach staging at all, the `staging` branch, the
`camicia-staging-runner` self-hosted runner, `deploy-staging.yml`, the Cloudflare Tunnel, Access,
and DNS, stays untouched and does not need redoing.

Start already logged into the production machine over SSH.

### 1. Tear down the stack and its data

```bash
cd ~/camicia-staging
docker compose -p camicia-staging --profile cloudflare down -v
rm -rf <SERVER_VOLUME_PROJECTS> <SERVER_VOLUME_KEYS>
```

### 2. Bring the stack back up on the latest code

```bash
cd ~/camicia-staging
git fetch origin staging
git reset --hard origin/staging
docker compose -p camicia-staging --profile cloudflare up -d --build
```

### 3. Fix ownership on the fresh bind mounts

A brand-new bind mount starts root-owned, and nothing fixes this until the project already exists
or a deploy has run, neither of which has happened yet.

```bash
docker exec <SERVER_CONTAINER_NAME> bash -c 'chown <PROJECTS_USER>:<PROJECTS_USER> <SERVER_VOLUME_PROJECTS_DIR> <SERVER_VOLUME_KEYS_DIR>'
```

### 4. Bootstrap a fresh BOINC project

Same `--key_dir`/`--delete_prev_inst` reasoning as
[Disaster Recovery step 3](#3-bring-the-stack-up-and-bootstrap-the-project). Replace every `<...>`
placeholder with the real value from `~/camicia-staging/.env`:

```bash
docker exec -it -u <PROJECTS_USER> -e USER=<PROJECTS_USER> <SERVER_CONTAINER_NAME> \
    /usr/local/src/boinc/tools/make_project \
    --srcdir /usr/local/src/boinc \
    --project_root <SERVER_VOLUME_PROJECTS_DIR>/camicia \
    --key_dir <SERVER_VOLUME_KEYS_DIR> \
    --url_base http://<DOMAIN> \
    --delete_prev_inst \
    --drop_db_first \
    --db_host <DATABASE_CONTAINER_NAME> \
    --db_user root \
    --db_pass <MARIADB_ROOT_PASSWORD> \
    --db_name <MARIADB_DATABASE> \
    camicia
```

### 5. Deploy the app

```bash
cd ~/camicia-staging/tools
bash tools.sh
```

`publish_version.sh`'s own git-diff gate decides whether the worker source actually needs
recompiling, and on a fresh bootstrap it usually finds no change since the last real commit, so it
skips publishing entirely, even though this fresh database has no app version registered at all.
Force one:

```bash
bash publish_version.sh --force
```

### 6. Re-run the ID-width migration

Same reasoning as [Disaster Recovery step 7](#7-re-run-the-id-width-migration): this comes from upstream BOINC's own schema, not
something already fixed, so every fresh bootstrap needs it once.

```bash
cd ~/camicia-staging
docker cp tools/migrations/0001_widen_workunit_result_ids.sh <SERVER_CONTAINER_NAME>:<SERVER_VOLUME_PROJECTS_DIR>/camicia/bin/
docker exec <SERVER_CONTAINER_NAME> chown <PROJECTS_USER>:<PROJECTS_USER> <SERVER_VOLUME_PROJECTS_DIR>/camicia/bin/0001_widen_workunit_result_ids.sh
docker exec <SERVER_CONTAINER_NAME> chmod +x <SERVER_VOLUME_PROJECTS_DIR>/camicia/bin/0001_widen_workunit_result_ids.sh
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia/bin && ./0001_widen_workunit_result_ids.sh"
```

### 7. Create the forums

```bash
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia/html/ops && php create_forums.php"
```

### 8. Verify, don't assume

Same checks as [Disaster Recovery step 9](#9-verify-dont-assume), adapted for staging's own container/project names:

```bash
cd ~/camicia-staging
docker compose -p camicia-staging ps
```

```bash
for d in assimilator sample_bitwise_validator work_generator feeder transitioner file_deleter; do
    docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> pgrep -f "^$d " > /dev/null \
        && echo "OK: $d" || echo "MISSING: $d is not running"
done
```

The site loads at `<DOMAIN>` (behind Access), and `/camicia_cgi/*` / `/camicia/download/*` /
`/camicia/get_project_config.php` / the bare `/camicia/` page still bypass it correctly, unaffected
by this reset since Access configuration lives on Cloudflare's side, not in anything this
procedure touches.

## Restoring from a backup

Different from Disaster Recovery above: the project and the host it runs on are both fine, you just
need to put back the database or a results segment from an existing backup, usually because of an
accidental bad migration, corrupted data, or something deleted by mistake. Backups already live
locally in the project tree; there's no need to reach for the USB drive or Google Drive unless the
local copies themselves are gone too, in which case see
[Disaster Recovery step 4](#4-recover-the-database-content) and
[step 5](#5-recover-results) instead.

### Restoring the database

This overwrites the live database, so stop the daemons first, they're actively reading and writing
it:

```bash
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/stop"
```

`db_backup.sh` keeps the newest 7 daily dumps; list them and pick one:

```bash
ls -la <SERVER_VOLUME_PROJECTS>/camicia/db_backups/
```

Same restore command as [Disaster Recovery step 4](#4-recover-the-database-content):

```bash
zcat <SERVER_VOLUME_PROJECTS>/camicia/db_backups/<chosen>.sql.gz | docker exec -i <DATABASE_CONTAINER_NAME> mariadb \
    -uroot -p<MARIADB_ROOT_PASSWORD> <MARIADB_DATABASE>
```

Restart the daemons:

```bash
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/start"
```

Verify rather than assume: check the daemons actually came back and the site still responds, same
checks as [Disaster Recovery step 9](#9-verify-dont-assume).

`db_backup.sh` dumps the whole database with no table exclusions, so `camicia_wu_cursor` (the
`work_generator` cursor) comes back from the same dump as `workunit`/`result`, consistent with
everything else, just older, not partially rolled back. Whatever was issued or reported between the
chosen dump and now is genuinely gone.

### Recovering a results segment

Reading an old, already-rotated segment needs no restore at all: `rotate_results.sh` never deletes
one, they're just sitting there gzip'd:

```bash
zcat <SERVER_VOLUME_PROJECTS>/camicia/results/results_<timestamp>.txt.gz | less
```

The live `results.txt` itself, whatever's been assimilated since the last rotation, up to 100MB or
a day's worth, is different: nothing keeps a point-in-time copy of it the way `db_backups/` does for
the database. The only copy anywhere else is whatever `backup_usb.sh`/`backup_offsite_gdrive.sh`
last mirrored, up to a day stale. If it's gone (deleted, corrupted), pull that last mirrored copy
back the same way [Disaster Recovery step 5](#5-recover-results) does, just naming the one file
instead of the whole directory:

```bash
cp /mnt/backup1tb/results/results.txt <SERVER_VOLUME_PROJECTS>/camicia/results/results.txt
```

or

```bash
rclone copy --config rclone.conf camicia-gdrive:camicia-backup/results/results.txt <SERVER_VOLUME_PROJECTS>/camicia/results/
```

Whatever the assimilator appended between that last backup and the moment `results.txt` was lost is
gone for good; there's no way to recover it after the fact.

## Rolling back a bad deploy

`deploy.yml` only ever runs by hand (`workflow_dispatch`, never on push) and always deploys
whatever `main` currently is (`git reset --hard origin/main`). Two rollback cases already happen
automatically without you doing anything; a third needs a person.

### Already automatic

- **A deploy fails partway, after the pre-deploy backup was taken.** `tools.sh`'s own exit trap
  calls `deploy_rollback.sh --restore` itself. See the [Alerts](#alerts-what-to-do-for-each-one)
  entries for "Camicia: deploy rolled back" (worked) versus "Camicia: rollback PARTIAL/INCOMPLETE/
  FAILED" (didn't, see below).
- **A deploy finishes but leaves the project unhealthy** (a daemon didn't come back up, or the
  user site doesn't respond). `deploy.yml`'s own "Verify deployment health" step catches this
  (checks all 6 daemons by name and an HTTP request to the site) and its next step runs
  `bash tools/tools.sh --restore` on failure, which delegates to the exact same
  `deploy_rollback.sh --restore` path.

Either way, `--restore` does three things: restores `assimilator/`, `worker/`, `work_generator/`,
`templates/`, `html/`, `bin/`, `config.xml`, `project.xml`, and `terms_of_use.txt` from a tarball
taken right before the deploy overwrote them; republishes the worker as a new app version and
deprecates the broken one, *only if* the failed run had actually published one (a published app
version is immutable, so a plain file revert can't undo it, see `publish_version.sh`); and
restarts the daemons. It deliberately does **not** touch `results/`, `db_backups/`, `apps/`, or
`download/` (excluded from the backup itself), and it does not undo a database migration: those
live under `tools/migrations/` and are run by hand, once, outside `tools.sh` entirely (see
`tools/migrations/README.md`), so nothing in the deploy path could have caused one to run in the
first place.

### If the automatic rollback itself failed

These are the "Camicia: rollback PARTIAL/INCOMPLETE/FAILED" alerts. What's actually broken differs
per case; the notification body says which:

- **No pre-deploy backup found**: `deploy_rollback.sh --backup` never ran (the failure happened
  before that stage) or its output file is gone. There's nothing to restore from. Fall back to
  restarting daemons by hand and fixing forward instead:
  ```bash
  docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c 'cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/start'
  ```
- **Files restored, but publishing a replacement app version failed**: the broken version is still
  live and being served to clients. Rerun it by hand from the same place `deploy_rollback.sh` does,
  to see the real compile/sign error:
  ```bash
  cd ~/camicia/tools
  bash publish_version.sh --force
  ```
  Once that succeeds, deprecate the broken version yourself (the version string, e.g. `1.05`, is in
  the notification body):
  ```bash
  docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia/html/ops && ./deprecate_app_version.php <VERSION>"
  ```
- **Files restored (and a replacement published, if one was needed), but the daemons didn't come
  back up**: run `bin/start` by hand, same command as above, then see
  [Daemon is down / crash-looping](#daemon-is-down--crash-looping) if it still doesn't come up.

### Rolling back a deploy discovered bad after the fact

The health check only catches a daemon being down or the site not responding: it won't catch a
deploy that's technically healthy but has a real bug found hours or days later. `deploy_rollback.sh`
always overwrites its backup with the *most recent* deploy's pre-deploy state, so this only works
if no other deploy has run since the bad one:

```bash
cd ~/camicia
bash tools/tools.sh --restore
```

To check what's actually live right now before deciding, or to confirm this is even the deploy you
think it is:

```bash
cat ~/.camicia_deploy_state_camicia/deployed_sha
```

(only written after a deploy that passed the health check, so it always names a known-good commit).

### If another deploy has already happened since the bad one

The backup above is gone, overwritten by the newer deploy. Going back further means moving `main`
itself, then redeploying through the normal pipeline, never by resetting the server's checkout
directly:

```bash
git revert <bad-commit-sha>
git push origin main
```

(`git revert` more than one commit by passing more than one SHA, or a range.)

then trigger the real workflow, same as [Disaster Recovery step 6](#6-deploy):

```bash
gh workflow run deploy.yml --ref main
```

## Daemon is down / crash-looping

`daemon_health_check.sh` (hourly) already retries `bin/start` on its own and notifies either way,
see [Alerts](#alerts-what-to-do-for-each-one) above; a one-off restart usually needs nothing further
from you. The tell that it's actually crash-looping, not a single blip, is the same daemon showing up
in a *later* hour's alert too: something is killing it again before the next check, and another
restart alone won't fix that.

### 1. Read that daemon's own log first

The notification and `daemon_health_alerts.log` both say that a daemon died, not why. Each daemon
logs its own exit reason to its own file:

```bash
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c "tail -50 <SERVER_VOLUME_PROJECTS_DIR>/camicia/log_*/<daemon>.log"
```

### 2. Match what it says against causes actually seen on this project

- **A DB connectivity error, especially if several daemons died at the same moment**: confirmed by
  actually stopping the database container on staging: all 6 daemons queried it as part of their
  main loop and every one exited within seconds, not just a subset. All log a generic `Database
  error: Lost connection to server during query` first; `feeder`, `transitioner`, `work_generator`,
  and `sample_bitwise_validator` then also log their own specific line: `DB connection lost,
  exiting`; `WU enum error: Error 2013; exiting` (MySQL's own "lost connection during query");
  `can't load cursor`; `can't find app simulator`, respectively. `assimilator` and `file_deleter`
  exit on the generic message alone. Check the database container is actually up first
  (`docker compose ps`), then see [Database issues](#database-issues) below.

- **`transitioner` specifically, logging `can't read key`**: `upload_private` is missing.
  `transitioner`, unlike every other daemon, reads it unconditionally at startup regardless of any
  upload-certificate setting. `tools.sh` already self-heals this from the encrypted
  `upload_private.gpg` backup on every deploy, using `UPLOAD_KEY_PASSPHRASE` injected directly by
  `deploy.yml` from the `production` GitHub Environment secret, so it shouldn't recur from a normal
  deploy, but it can still happen if the plaintext file was deleted by hand or the key volume itself
  was touched outside a deploy. The real fix is exactly that: trigger a normal redeploy and let it
  self-heal:

  ```bash
  gh workflow run deploy.yml --ref main
  ```

  This is also the *only* way to fix it unless you personally have `UPLOAD_KEY_PASSPHRASE` recorded
  somewhere yourself, outside GitHub: GitHub Actions secrets are write-only, `gh secret list` shows
  only the name and last-updated time, never the value, confirmed live, so there's no way to read it
  back out of GitHub to run the decrypt by hand. If you do have it recorded elsewhere, this is the
  same command `tools.sh` itself runs:

  ```bash
  docker exec -i <SERVER_CONTAINER_NAME> bash -c \
      "gpg --batch --yes --passphrase-fd 0 -o <SERVER_VOLUME_KEYS_DIR>/upload_private --decrypt <SERVER_VOLUME_KEYS_DIR>/upload_private.gpg" \
      <<< "<UPLOAD_KEY_PASSPHRASE>"
  docker exec <SERVER_CONTAINER_NAME> bash -c \
      "chown <PROJECTS_USER>:<PROJECTS_USER> <SERVER_VOLUME_KEYS_DIR>/upload_private && chmod 600 <SERVER_VOLUME_KEYS_DIR>/upload_private"
  ```

- **A filesystem or permission error** (`Permission denied`, `missing dir`, `opendir() failed`):
  usually means something under `upload/`, `download/`, or `log_<hostname>/` lost the ownership
  `fix_permissions.sh` sets, most often after a manual `docker cp` or a restore that didn't go
  through the normal deploy path. Re-run it, the same call `tools.sh` itself makes on every deploy:

  ```bash
  docker exec \
      -e SERVER_VOLUME_PROJECTS_DIR=<SERVER_VOLUME_PROJECTS_DIR> \
      -e PROJECTS_USER=<PROJECTS_USER> \
      -e OPS_USER=<OPS_USER> \
      -e OPS_PASS=<OPS_PASS> \
      -e SERVER_HOSTNAME=<SERVER_HOSTNAME> \
      <SERVER_CONTAINER_NAME> /usr/local/bin/fix_permissions.sh
  ```

- **A crash on every restart attempt, right after a deploy**: the just-published binary itself is
  bad. See [Rolling back a bad deploy](#rolling-back-a-bad-deploy) above.

### 3. Confirm it actually stays up

A single successful restart doesn't prove the cause is gone, only that the daemon started this time.
Wait past `daemon_health_check.sh`'s next hourly run, or trigger it by hand instead of waiting:

```bash
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia/bin && ./daemon_health_check.sh"
```

Either way, see it list as OK before considering this resolved:

```bash
for d in assimilator sample_bitwise_validator work_generator feeder transitioner file_deleter; do
    docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> pgrep -f "^$d " > /dev/null \
        && echo "OK: $d" || echo "MISSING: $d is not running"
done
```

## Stopping a daemon for maintenance

Stop exactly one daemon, not the whole project, and make sure nothing brings it back until you
want it back. `bin/start`/`bin/stop` only support enabling or disabling the whole project, there's
no per-daemon flag on the command line; do it in `config.xml` and by hand instead.

### 1. Disable it in `config.xml` first

Every `<daemon>` entry supports a `<disabled>` element: `bin/start`'s own daemon-launching loop
skips any daemon where it's set, so nothing, cron-triggered or run by hand, will start it again
until this is removed. Add it to the specific daemon's block (`transitioner` shown, applies the
same way to any of the 6):

```xml
<daemon>
    <cmd>transitioner -d 3 </cmd>
    <disabled>1</disabled>
</daemon>
```

This alone stops nothing yet, it only stops anything from launching it again.

### 2. Stop the process itself

Each daemon has its own pid file. Stop it with the same signal `bin/stop` uses internally,
`SIGHUP`, not `SIGTERM`:

```bash
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c \
    'kill -HUP $(cat <SERVER_VOLUME_PROJECTS_DIR>/camicia/pid_<hostname>/<daemon>.pid)'
```

The other 5 daemons are unaffected.

### 3. Bring it back afterward

Remove the `<disabled>` line from `config.xml`, then:

```bash
docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c \
    'cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/start'
```

Confirm it actually came back the same way
[Daemon is down / crash-looping step 3](#3-confirm-it-actually-stays-up) does.

### A side effect worth knowing

Disabling the daemon this way stops it being restarted, but `daemon_health_check.sh` doesn't read
`config.xml`, it only checks whether the process is running, so it will keep sending a real
`Camicia: daemon(s) DOWN` alert every hour the daemon stays disabled. If that alert noise isn't
wanted either, the same `<disabled>1</disabled>` element works on `<task>` entries too
(`bin/start`'s task-running loop has the identical skip logic); disable
`daemon_health_check.sh`'s own task entry the same way for the maintenance window.

`config.xml` on the live server gets wholesale-replaced by the next deploy either way, so this
never strictly needs remembering to revert before then, but don't rely on that: clean it up right
after maintenance.

## Database issues

Single MariaDB container, no replication, so replication lag or failover isn't something this
project has to reason about. What follows is what can actually go wrong here, and when to repair in
place versus stop and use [Restoring from a backup](#restoring-from-a-backup) instead.

### 1. Confirm it's actually the database

Daemons logging `Database error: Lost connection to server during query` (see "Daemon is down /
crash-looping" above) almost always mean the container itself, not the daemon, is the problem:

```bash
docker compose ps
docker inspect <DATABASE_CONTAINER_NAME> --format '{{.State.Health.Status}}'
```

If it isn't `healthy`, that's the root cause. Bring it back
(`docker start <DATABASE_CONTAINER_NAME>`), then follow "Daemon is down / crash-looping" above: a DB
restart takes every daemon down with it, and none of them come back on their own.

### 2. Disk space

The database's own volume and the project checkout live on the same disk on this host, confirmed;
the existing disk alert already covers it, there's no separate check needed here.

### 3. Corruption after an unclean shutdown

The realistic way this happens on a single-host setup like this is a host crash, an OOM kill, or a
hard `docker kill` mid-write, not concurrent writers stepping on each other. Confirmed live: MariaDB's
own InnoDB crash recovery runs automatically on the next start and handles the ordinary case (an
unflushed transaction at the moment of the crash) with no action needed:

```bash
docker logs <DATABASE_CONTAINER_NAME> --tail 50
```

Look for `Starting table crash recovery...` followed by `Crash table recovery finished.`, with
nothing else in between. If it instead reports table corruption it can't recover from on its own,
try a repair:

```bash
docker exec <DATABASE_CONTAINER_NAME> mariadb-check -uroot -p<MARIADB_ROOT_PASSWORD> --auto-repair --all-databases
```

If that doesn't fully clear it, or the container won't start at all, stop there and go to "Restoring
from a backup" instead of trying further variations: a restore loses at most a day of data
(`db_backup.sh` runs daily), but a repair that half-works can leave inconsistent data with no clean
point left to roll back to.

### 4. Schema limits

`tools/migrations/0001_widen_workunit_result_ids.sh` already widened `workunit.id`/`result.id` past
BOINC's default `int` AUTO_INCREMENT ceiling (about 2.147 billion) for this project's own workload.
Check any table you suspect is approaching its own ceiling with `SHOW TABLE STATUS LIKE '<table>'`'s
`Auto_increment` column; the fix is the same shape, a new numbered script under `tools/migrations/`,
not a one-off hand fix, see that directory's own `README.md` for the convention.

## External service problems

This project depends on a few external services with no local health check and no alerting; if
something depending on one breaks, check that service's own dashboard first, not just this box.

- **Site unreachable externally, but `docker compose ps` and every daemon are healthy**: check the
  Cloudflare dashboard for the Tunnel's own connection status and the Access policy, not just this
  host; the failure could be entirely on Cloudflare's side, or a revoked `CLOUDFLARE_TUNNEL_TOKEN`.
- **Users report never receiving an email**: check your SMTP provider's own delivery dashboard/logs;
  nothing on this box logs a failed send.
- **Nobody can create an account, or account creation seems to hang rather than fail outright**:
  check the Google reCAPTCHA admin console for the key's status.
- **Forum or profile spam suddenly increases**: check your Akismet account for the key's status.

## Rotating/losing a secret or key

### `code_sign_private` (signs every app version clients trust)

Do this if a compromise is suspected: someone else getting this key means they could sign a
malicious app version that every attached client would trust and run. Follows
[BOINC's own Code Signing wiki page](https://github.com/BOINC/boinc/wiki/Code-signing); step 5
below is its documented rotation process specifically, not something invented for this project.

BOINC caps code-signing at 1024-bit RSA project-wide (`lib/crypt.h`'s `MAX_RSA_MODULUS_BITS`, and
`crypt_prog -genkey` refuses anything else), so rotating means a fresh 1024-bit pair, not a stronger
one; a bigger key was never on the table.

1. Generate somewhere isolated, not directly on this box: a throwaway container built from the same
   `camicia-server` image, run with `--network none`, gives real container-level network isolation
   without needing to physically air-gap anything.

   ```bash
   bin/crypt_prog -genkey 1024 code_sign_private code_sign_public
   ```

2. Before moving the new private key anywhere, keep an encrypted, checksum-verified offline copy of
   it somewhere outside this host (a USB stick), still on the isolated machine from step 1:

   ```bash
   gpg -c --cipher-algo AES256 -o code_sign_private.gpg code_sign_private
   sha256sum code_sign_private code_sign_private.gpg
   gpg --decrypt code_sign_private.gpg > code_sign_private.check
   diff code_sign_private code_sign_private.check && rm code_sign_private.check
   ```

3. On the server: back up the *old* key files in place first, don't delete them (dated suffix):

   ```bash
   docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c '
       cp <SERVER_VOLUME_KEYS_DIR>/code_sign_private <SERVER_VOLUME_KEYS_DIR>/code_sign_private.old_<date>
       cp <SERVER_VOLUME_KEYS_DIR>/code_sign_public <SERVER_VOLUME_KEYS_DIR>/code_sign_public.old_<date>
   '
   ```

   Then copy the new plaintext keypair over yourself, your own `scp`, never through anything that
   logs or stores it. `keys/` is a bind mount, so `scp` to the *host* path (`<SERVER_VOLUME_KEYS>`,
   not the `_DIR` container path) lands the files where the container sees them too, no `docker cp`
   needed:

   ```bash
   scp code_sign_private code_sign_public <user>@<host>:<SERVER_VOLUME_KEYS>/
   ```

   Then fix ownership and permissions explicitly, don't assume `scp` preserved them correctly:

   ```bash
   docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c '
       chown <PROJECTS_USER>:<PROJECTS_USER> <SERVER_VOLUME_KEYS_DIR>/code_sign_private <SERVER_VOLUME_KEYS_DIR>/code_sign_public
       chmod 600 <SERVER_VOLUME_KEYS_DIR>/code_sign_private
       chmod 640 <SERVER_VOLUME_KEYS_DIR>/code_sign_public
   '
   ```

4. Verify with a real sign/verify round-trip before trusting it, and confirm it genuinely fails
   against the *old* public key too, proving this is a real replacement, not an accidental restore
   of the old one:

   ```bash
   docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c '
       cd <SERVER_VOLUME_PROJECTS_DIR>/camicia
       echo test > /tmp/t
       bin/sign_executable /tmp/t <SERVER_VOLUME_KEYS_DIR>/code_sign_private > /tmp/t.sig
       bin/crypt_prog -verify /tmp/t /tmp/t.sig <SERVER_VOLUME_KEYS_DIR>/code_sign_public
       bin/crypt_prog -verify /tmp/t /tmp/t.sig <SERVER_VOLUME_KEYS_DIR>/code_sign_public.old_<date>
       rm /tmp/t /tmp/t.sig
   '
   ```

5. For any client already attached under the old key: without this, it gets told to reattach the
   next time it contacts the scheduler. `sched/handle_request.cpp` looks for
   `<SERVER_VOLUME_KEYS_DIR>/old_key_N`/`signature_N`/`signature_stripped_N` files (`N` = 0, 1, ...,
   the next free number if this project has rotated before) and, if the client's currently-trusted
   key matches one, serves it a signature proving the new key is legitimate, so it can upgrade trust
   on its own.

   The signature must be made over the exact text the scheduler actually sends and the client
   actually verifies against: the key file's content with its trailing newline stripped
   (`sched/sched_main.cpp` reads the key file, then calls `strip_whitespace()` before ever using it),
   not the raw file. Signing the raw file produces a signature that `crypt_prog -verify` still calls
   valid, since that only checks the exact bytes it's given, not what a real client actually receives
   and hashes; every real client rejects a signature made this way with "New code signing key
   doesn't validate". Sign the stripped text, not the file:

   ```bash
   docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c '
       cd <SERVER_VOLUME_PROJECTS_DIR>/camicia
       cp <SERVER_VOLUME_KEYS_DIR>/code_sign_public.old_<date> <SERVER_VOLUME_KEYS_DIR>/old_key_0
       printf %s "$(cat <SERVER_VOLUME_KEYS_DIR>/code_sign_public)" > /tmp/code_sign_public_stripped
       bin/sign_executable /tmp/code_sign_public_stripped <SERVER_VOLUME_KEYS_DIR>/code_sign_private.old_<date> \
           > <SERVER_VOLUME_KEYS_DIR>/signature_0
       printf %s "$(cat <SERVER_VOLUME_KEYS_DIR>/signature_0)" > <SERVER_VOLUME_KEYS_DIR>/signature_stripped_0
       rm /tmp/code_sign_public_stripped
   '
   ```

6. Re-encrypt it at rest, matching every deploy's expectation, then remove the plaintext copy:

   ```bash
   docker exec --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash -c '
       gpg -c --cipher-algo AES256 -o <SERVER_VOLUME_KEYS_DIR>/code_sign_private.gpg <SERVER_VOLUME_KEYS_DIR>/code_sign_private
       rm <SERVER_VOLUME_KEYS_DIR>/code_sign_private
   '
   ```

7. If the passphrase itself changed too, update the `production` GitHub Environment secret (and
   local `.env`, only if local dev should also use the new one):

   ```bash
   gh secret set CODE_SIGN_KEY_PASSPHRASE --env production
   ```

8. Verify through a real deploy, not just locally: the next published app version is the actual
   proof this worked end to end.

### `upload_private`

Do this if a compromise is suspected: someone else getting this key could forge an upload
certificate for any workunit, letting a spoofed client upload a file with no proof the host was
actually assigned that work. `dont_generate_upload_certificates`/`ignore_upload_certificates` are
both `0` here (BOINC's own default), so this is genuinely active, not a hypothetical concern.

Generate, back up offline, install, and verify the new key exactly as in `code_sign_private` steps
1-4 above, with two differences:

- It can never be encrypted at rest: `sched/transitioner.cpp` reads it unconditionally at every
  startup. It has to stay a continuously-present plaintext file, matching `upload_public`. Skip
  `code_sign_private`'s step 6 (re-encrypt) entirely; keep `upload_private.gpg` only as an
  offline/offsite backup artifact of the key, never the live copy.

- There is no `old_key_N`-style upgrade path, and none is needed: no client ever independently
  caches or verifies this key. `transitioner` signs each result's upload certificate once, at
  result-creation time; `sched/file_upload_handler.cpp` checks it once, at upload time, against
  whichever single `upload_public` file currently exists, with no fallback to older keys. This means
  any result already created (and so already signed) before the swap, but not yet uploaded, gets
  rejected outright (`ERR_PERMANENT "invalid signature"`) the moment the new key is installed. Check
  how many results are currently in flight, and wait for it to reach zero, or as close as practical,
  before installing the new key:

  ```bash
  docker exec <DATABASE_CONTAINER_NAME> mariadb -uroot -p<MARIADB_ROOT_PASSWORD> <MARIADB_DATABASE> -e \
      "SELECT COUNT(*) FROM result WHERE server_state=4;"
  ```

  (`server_state=4` is `RESULT_SERVER_STATE_IN_PROGRESS`: sent to a client, not yet reported.)
  `bin/start`/`bin/stop` only enable or disable the whole project, there's no way to pause just new
  dispatches while waiting. A result that does get rejected isn't lost: `transitioner` eventually
  notices it never came back and issues a fresh one, signed with the new key.

### `OPS_PASS` (the ops panel's HTTP Basic Auth password)

Low-risk to rotate: nothing else reads it (confirmed, it's a plain `.env` value, not a GitHub
secret), it only gates `html/ops/`. Change it in `.env`, then re-run the same `fix_permissions.sh`
call "Daemon is down / crash-looping" above uses to fix permissions, since that's also what
generates `.htpasswd`:

```bash
docker exec \
    -e SERVER_VOLUME_PROJECTS_DIR=<SERVER_VOLUME_PROJECTS_DIR> \
    -e PROJECTS_USER=<PROJECTS_USER> \
    -e OPS_USER=<OPS_USER> \
    -e OPS_PASS=<OPS_PASS> \
    -e SERVER_HOSTNAME=<SERVER_HOSTNAME> \
    <SERVER_CONTAINER_NAME> /usr/local/bin/fix_permissions.sh
```

`htpasswd -b -c` fully regenerates the file every time (confirmed: the old password stops
validating immediately, not just eventually), so there's no stale-entry cleanup step needed.

### Database credentials

Two separate credentials exist: `MARIADB_ROOT_PASSWORD` and `MARIADB_USER`/`MARIADB_PASSWORD`.
Confirmed only the first actually matters: `config.xml`'s `<db_user>` is `root`, and nothing in this
project's own code references `MARIADB_USER`/`MARIADB_PASSWORD` at all beyond the database
container's own first-boot bootstrap, so rotating those two has no real effect either way.

Changing `.env`'s `MARIADB_ROOT_PASSWORD` alone does nothing on its own: MariaDB only applies that
variable on a brand-new data volume, and `config.xml`'s own `<db_passwd>`, what every daemon and
script here actually authenticates with, is set once by `make_project` and never touched by any
later `.env` change or `tools.sh` deploy. Both need updating together:

```bash
docker exec <DATABASE_CONTAINER_NAME> mariadb -uroot -p<MARIADB_ROOT_PASSWORD> -e \
    "ALTER USER 'root'@'%' IDENTIFIED BY '<new password>', 'root'@'localhost' IDENTIFIED BY '<new password>';"
```

Then update `config.xml`'s `<db_passwd>` to match, and update `MARIADB_ROOT_PASSWORD` in `.env` too
(so a future `docker compose up`/volume recreate stays consistent).

Restarting the daemons afterward isn't optional cleanup, it's the actual proof this worked:
`ALTER USER` doesn't drop already-open connections, so every daemon keeps running fine on its old,
already-established one right up until it needs a fresh connection, silently masking a
`config.xml` update that didn't actually take. Confirmed live: daemons stayed healthy immediately
after the password change alone, and only failed once actually restarted (still pointed at the old
password), then recovered once `config.xml` was updated to match. Restart them the same way
"Daemon is down / crash-looping" above does, and confirm they actually come back before considering
this done.

Confirmed working end to end on staging: the multi-user `ALTER USER` syntax above, the old password
failing immediately after, `config.xml`'s independence from `.env` (daemons broke on restart until
it was updated), and full recovery once it matched.

### Google Drive rclone connection (`keys/rclone.conf`)

Symptom: `rclone` commands against `camicia-gdrive` fail with `invalid_grant: maybe token expired?`,
or `bin/backup_offsite_gdrive.sh` logs a `FAILED` line and the daily Drive backup stops advancing.

Cause: the OAuth grant behind `keys/rclone.conf` was revoked or expired, most commonly by removing
its access at `myaccount.google.com/connections` by mistake, easy to do if more than one rclone
OAuth client exists under the same Google account. If the Google Cloud Console project's OAuth
consent screen ever shows Publishing status "Testing" rather than "In production", that alone
expires every refresh token after 7 days regardless of anything else, check that first.

`rclone` and `keys/rclone.conf` only exist inside the `boinc_server` container, not on the host, so
the config-reading and config-patching steps below run there. Get a shell in it first:

```bash
docker exec -it --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash
```

`client_id`/`client_secret` don't need to change, only the token does. Decrypt the existing config
to read them out:

```bash
export RCLONE_CONFIG_PASS=$(cat <SERVER_VOLUME_KEYS_DIR>/rclone_config_pass)
rclone config show camicia-gdrive --config <SERVER_VOLUME_KEYS_DIR>/rclone.conf
```

Get a fresh token for that same client. This step needs a real web browser, which the container
doesn't have, so run it on your own machine instead, in a real interactive terminal, not a script:
it starts a local web server and waits for you to complete the Google login in a browser.

```bash
rclone authorize "drive" "<CLIENT_ID>" "<CLIENT_SECRET>" --drive-scope drive.file
```

Open the printed link, sign in with the correct Google account (easy to get wrong if signed into
more than one), approve access, and `rclone` prints a JSON token blob. Save it to a file, not a
shell variable, same reasoning as
[Disaster Recovery step 10](#10-setting-up-rclone-access-to-google-drive): pasting it inline on a command line
lets the shell strip its quotes and silently corrupt it.

```bash
cat > /tmp/rclone_token.json << 'EOF'
<paste the printed {...} blob here>
EOF
```

Copy that file into the container, then get a shell in it again to use it:

```bash
docker cp /tmp/rclone_token.json <SERVER_CONTAINER_NAME>:/home/<PROJECTS_USER>/rclone_token.json
docker exec -it --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> bash
```

Patch just the token into the existing remote. This leaves `client_id`/`client_secret`/`scope`
untouched, no need to rebuild the config from scratch.

```bash
export RCLONE_CONFIG_PASS=$(cat <SERVER_VOLUME_KEYS_DIR>/rclone_config_pass)
rclone config update camicia-gdrive token="$(cat ~/rclone_token.json)" --config <SERVER_VOLUME_KEYS_DIR>/rclone.conf --non-interactive
rm ~/rclone_token.json
```

Confirm it actually works, then check for a real gap: a failed sync doesn't retry itself, so any
daily run that failed while the token was broken needs to be caught up by hand. Still inside the
container:

```bash
rclone lsl --config <SERVER_VOLUME_KEYS_DIR>/rclone.conf camicia-gdrive:camicia-backup/db_backups | tail -3
ls -la <SERVER_VOLUME_PROJECTS_DIR>/camicia/db_backups/ | tail -3
```

If the newest local file isn't the newest one listed on Drive, run the real backup once by hand to
close the gap, still inside the container:

```bash
bash <SERVER_VOLUME_PROJECTS_DIR>/camicia/bin/backup_offsite_gdrive.sh
```
