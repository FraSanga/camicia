# Camicia

A [BOINC](https://boinc.berkeley.edu/) distributed-computing project exhaustively searching every
deal of Beggar-My-Neighbour for games that loop forever, and for the longest game that ever ends.

## What this is

This project plays out Beggar-My-Neighbour, a card game about as simple as they get: two players,
no strategy, no choices to make. Once the deck is dealt, the entire game plays out on its own. It's
named *Camicia* after the traditional Italian card game in the same family (also called *Straccia
Camicia*), simply because its creator is Italian; the two games actually differ in rules (the Italian
version's penalty cards are A/2/3, this project's are the English game's A/K/Q/J).

That simplicity is deceptive. It's already known that some deals never end: the game settles into a
repeating cycle and continues forever, and this is proven fact, not conjecture, for specific real
deals. What's not known is how common that actually is, or what the longest possible game that
*does* end looks like, across the roughly 6.5x10^20 distinct ways a 52-card deck can be arranged.
There's no formula or shortcut that answers this: the only way to know a given deal's fate for
certain is to play it out (or simulate it) move by move, and the search space is far too large for
any single computer to get through in a useful amount of time.

That's what Camicia does. It ranks and walks through the entire permutation space methodically (no
brute-force shuffling, no repeats, no gaps), simulates each deal, and records every one that loops
forever and every new record for game length, distributing the work across volunteers' computers via
BOINC the same way as other projects do.

## How it works, briefly

- **Indexing, not generating.** Each of the ~6.5x10^20 distinct deals has a unique index; the worker
  app converts an index directly into the Nth deal via combinatorial ranking, so a workunit is just a
  small `(startIndex, endIndex)` range, never an actual list of permutations.
- **One simulation per deal.** A small C++ engine plays out each deal turn by turn. A repeated game
  state proves that deal loops forever; otherwise the deal finishes, and its card/trick count gets
  compared against the running best.
- **BOINC daemons** generate work, validate results from multiple volunteers before trusting
  them, and assimilate validated results into the project's running output log.

## Running your own instance

This is the full loop for standing up a fresh Camicia server from scratch, useful for local
development or running your own independent instance. It assumes Docker and Docker Compose are
already installed.

### 1. Configure and start the stack

Copy `.env.example` to `.env` and fill in the required values, then bring the containers up:

```bash
docker compose up -d --build
```

To rebuild from a clean slate later:

```bash
docker compose down
docker compose up -d --build
```

### 2. Create the BOINC project

Run BOINC's `make_project` inside the running server container:

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

`--key_dir` points at the dedicated `SERVER_VOLUME_KEYS` bind mount, kept separate from
`SERVER_VOLUME_PROJECTS` specifically so the code-signing and upload keys survive a from-scratch
wipe of `projects/`. If keys already exist there from a previous setup, `make_project` finds and
reuses them instead of generating new ones.

Then deploy the app:

```bash
cd tools && ./tools.sh
```

### 3. Everything else is automatic

That same `tools/tools.sh` command is the whole edit-deploy loop from here on, for any future change
under `tools/`: it compiles the worker/assimilator/work_generator, registers a new signed app
version, and restarts the daemons in place. No separate `xadd`, code-signing, or version-registration
step, and it never recreates the project.

Workunits are generated automatically too: the `work_generator` daemon keeps the job queue full on
its own by slicing up the permutation index space, no manual step needed. A manual one-off workunit
(for testing a different app, for example) looks like this:

```bash
docker exec -it --user <PROJECTS_USER> <SERVER_CONTAINER_NAME> \
  bash -c "cd <SERVER_VOLUME_PROJECTS_DIR>/camicia && ./bin/create_work --appname <app> \
    --wu_name <unique-test-name> --wu_template templates/<app>_in.xml \
    --result_template templates/<app>_out.xml <input.txt>"
```

### 4. Attach a test client

Spin up a Linux BOINC client in its own container and attach it to your local project to see the
whole pipeline run end to end:

```bash
docker run -d --name linux_user --net host boinc/client
docker exec linux_user boinccmd --project_attach http://<DOMAIN>/camicia <user-token>
docker exec linux_user boinccmd --project http://<DOMAIN>/camicia update
docker logs -f linux_user
```

## Documentation

- [`RUNBOOK.md`](RUNBOOK.md): operational reference, disaster recovery, and what to do when
  something breaks.
- [`SECURITY.md`](SECURITY.md): the project's security posture and how to report a vulnerability.
