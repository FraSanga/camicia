# Camicia BOINC Project

This repository contains the steps and commands needed to configure, deploy, and test the **Camicia** BOINC project using Docker.

---

## 1. Initial Setup

Start the environment using Docker Compose. Make sure to clean up before rebuilding the containers:

```bash
docker compose down
docker compose up -d --build
```

## 2. Project Creation

Create the basic BOINC project structure by running the `make_project` tool inside the server container.

```bash
docker exec -it -u <PROJECTS_USER> -e USER=<PROJECTS_USER> <SERVER_CONTAINER_NAME> /
/usr/local/src/boinc/tools/make_project /
--srcdir /usr/local/src/boinc /
--project_root <SERVER_VOLUME_PROJECTS_DIR>/camicia /
--url_base http://<DOMAIN> /
--delete_prev_inst /
--drop_db_first /
--db_host <DATABASE_CONTAINER_NAME> /
--db_user root /
--db_pass <MARIADB_ROOT_PASSWORD> /
--db_name <MARIADB_DATABASE> /
camicia
```

### Post-Creation Steps

After generating the project, perform the following steps:

1. Run `tools.sh`
2. Restart the containers *(temporary for now)*

## 3. Configuration Files & Resources

Below are the main configuration files and helpful links to the official BOINC documentation:

* **[config.xml](https://github.com/BOINC/boinc/wiki/ProjectConfigFile)**: Main project configuration file.
* **[Translations](https://github.com/BOINC/boinc/wiki/TranslateProject)**: Guide for translating the web interface.
* **project.xml**: Declare apps and platforms in this file.
* **project.inc**: Include file for project-specific web customizations. (`/html/project`)

## 4. Applications and Versioning

### 4.1 Database Registration (xadd)

Use the [xadd tool](https://github.com/BOINC/boinc/wiki/XaddTool) to add your app/platform definitions to the database:

```bash
docker exec -it --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> /
bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/xadd"
```

### 4.2 App Folder Creation, Code Signing, and Version Registration (automated)

`tools/tools.sh` now handles all of this for the `simulator` app on every deploy: it auto-increments
the version directory under `apps/simulator/<version>/x86_64-pc-linux-gnu/`, stages the freshly
compiled binary there under a version-qualified name (`worker_app_<version>`, e.g. `worker_app_1.05`)
alongside a matching `version.xml` generated on the fly, and runs `./bin/update_versions --noconfirm`,
which auto-signs the binary with `keys/code_sign_private` (no separate `crypt_prog -sign` step needed).
Redeploying always registers a new version for two independent reasons: `update_versions` silently
skips a version directory it's already seen (why the version number bumps every run), and separately,
BOINC treats `download/<physical_name>` as immutable once served — reusing the same filename across
versions would make `update_versions` refuse to register the moment the binary's actual bytes change,
which is why the physical filename is version-qualified too.

The manual steps below are kept for reference (e.g. if you add a second app that isn't wired into
`tools.sh`) — for `simulator`, just run `tools/tools.sh`.

**App folder creation** (this is where a compiled app binary goes):
```bash
docker exec -it --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> /
mkdir -p <SERVER_VOLUME_PROJECTS_DIR>/camicia/apps/<app>/<version>/<platform>
```

**Code signing** (`update_versions` does this automatically if no `.sig` file is present; only needed
manually if you're placing a pre-signed binary yourself — see the
[Code Signing documentation](https://github.com/BOINC/boinc/wiki/CodeSigning)):
```bash
crypt_prog -sign <exe-path> <code_sign_private-path> > <exe-path.sig>
```

**Version registration**, so BOINC detects the [new app version](https://github.com/BOINC/boinc/wiki/AppVersionNew):
```bash
docker exec -it --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> /
bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/update_versions --noconfirm"
```

## 5. Creating Workunits

For `simulator`, this is automatic: `tools/work_generator/work_generator.cpp` runs as a daemon
(started/stopped via `bin/start`/`bin/stop` like the other daemons) that slices the permutation index
space (0 to 653534134886878244999) into ranges and keeps the queue topped up on its own — no manual
step needed. Tune its batch size and queue depth via the `work_generator` `<daemon>` entry in
`tools/config.xml` (`--range_size`, `--cushion`).

For one-off/manual workunits (e.g. testing a different app), place your input file (e.g.,
`<input.txt>`) inside the `/download` directory, then run:

```bash
docker exec -it --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> /
bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/create_work --appname <app> --wu_name <unique-test-name> --wu_template templates/<app>_in.xml --result_template templates/<app>_out.xml <input.txt>"
```

## 6. Linux Client Testing

You can test the entire workflow by spinning up a Linux BOINC client on Docker and attaching it to your project:

```bash
docker run -d --name linux_user --net host boinc/client
docker exec linux_user boinccmd --project_attach http://<DOMAIN>/camicia <user-token>
docker exec linux_user boinccmd --project http://<DOMAIN>/camicia update
docker logs -f linux_user
```