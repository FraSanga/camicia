// work_generator: keeps the BOINC job queue fed with ranges of the
// Camicia permutation-index space (0 .. MAX_INDEX inclusive) without
// requiring a human to run bin/create_work by hand.
//
// --app X            app name (default "simulator")
// --range_size N      permutations per workunit, decimal string (default 1000000000)
// --cushion N         maintain at least this many unsent job instances (default 20)
// --in_template_file  input template filename under templates/ (default "simulator_in.xml")
// --out_template_file output template filename under templates/ (default "simulator_out.xml")
// -d N                log verbosity level (0..4)

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <string>

#include "backend_lib.h"
#include "boinc_db.h"
#include "error_numbers.h"
#include "filesys.h"
#include "parse.h"
#include "str_replace.h"
#include "util.h"

#include "sched_config.h"
#include "sched_util.h"
#include "sched_util_basic.h"
#include "sched_msgs.h"

#include "int128_io.hpp"

// Last valid permutation index (inclusive); the full space is
// [0, MAX_INDEX], i.e. MAX_INDEX+1 distinct 52-card deals. Matches
// tests/test_data_gen.hpp's "Last possible index" case.
static const char* MAX_INDEX_STR = "653534134886878244999";

#define REPLICATION_FACTOR 2
    // require 2 independent results per WU so sample_bitwise_validator's
    // quorum actually cross-checks results instead of trusting a single
    // client's output

// Cost per deal, in the same fpops units BOINC's own DEFAULT_RSC_FPOPS_EST/
// BOUND use -- make_job() was leaving those at BOINC's generic placeholder
// defaults (3.6e12 / 8.64e13 total, regardless of range_size), which claim
// an 1e9-deal WU takes ~11 minutes. Measured against the real engine
// (engine.cpp/permutation.cpp, -O3, sequential indices exactly as
// worker.cpp iterates them, same as this project's own client benchmark
// calibration): 250 randomly-chosen blocks across the whole index space
// (two independent runs, 100 and 150 blocks, ~6.5M deals total) average
// ~149,000 fpops/deal against this host's measured p_fpops (BOINC's own
// client/whetstone.cpp benchmark, 8 runs, ~5.33e9 FLOPS) -- i.e. an
// 1e9-deal WU actually takes 6-9 hours, not 11 minutes. The client aborts
// a task once elapsed_time > rsc_fpops_bound/(avg_ncpus*p_fpops)
// (client/app.cpp), with no correction for this gap, so the old constants
// meant most WUs across the space would hit that wall and get killed
// mid-run. FPOPS_PER_DEAL matches the measured average closely enough
// that legitimately faster regions of the space just finish early (BOINC
// already handles that fine); the slowest block seen across both sampling
// runs was ~1.5x the average, well inside FPOPS_BOUND_FACTOR's margin.
#define FPOPS_PER_DEAL 1.5e5
#define FPOPS_BOUND_FACTOR 10
    // Bound stays well under DEFAULT_DELAY_BOUND (7 days) even for the
    // slowest observed region, so a WU that's genuinely stuck (not just
    // slow) still gets caught instead of the bound becoming meaningless.

const char* app_name = "simulator";
const char* in_template_file = "simulator_in.xml";
const char* out_template_file = "simulator_out.xml";
int128 range_size = 0;
long cushion = 20;

char* in_template;
DB_APP app;
int128 max_index;

// zero-pad to 21 digits (enough for any index up to MAX_INDEX, which is
// itself 21 digits) so that lexical VARCHAR ordering of workunit.name
// matches numeric ordering of the index.
static std::string int128ToPadded(int128 n) {
    std::string s = int128ToString(n);
    if (s.size() < 21) s = std::string(21 - s.size(), '0') + s;
    return s;
}

// --- crash-safe cursor, persisted in a dedicated singleton-row table ---

static int ensure_cursor_table() {
    return boinc_db.do_query(
        "CREATE TABLE IF NOT EXISTS camicia_wu_cursor ("
        "    id INT PRIMARY KEY,"
        "    next_index DECIMAL(24,0) NOT NULL,"
        "    exhausted TINYINT NOT NULL DEFAULT 0,"
        "    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
        ") ENGINE=InnoDB"
    );
}

// Reconstruct the cursor from the highest-numbered dispatched workunit,
// for the case where camicia_wu_cursor is empty (first run, or restored
// from a partial backup). Names are zero-padded so lexical MAX == numeric MAX.
static int128 recover_cursor_from_workunits() {
    char buf[512];
    snprintf(buf, sizeof(buf),
        "SELECT name FROM workunit WHERE name LIKE '%s\\_%%' ORDER BY name DESC LIMIT 1",
        app_name
    );
    if (boinc_db.do_query(buf)) return 0;
    MYSQL_RES* rp = mysql_store_result(boinc_db.mysql);
    if (!rp) return 0;
    MYSQL_ROW row = mysql_fetch_row(rp);
    int128 result = 0;
    if (row && row[0]) {
        // name format: simulator_<start>_<end> -> resume just past <end>
        std::string name(row[0]);
        size_t last_us = name.find_last_of('_');
        if (last_us != std::string::npos) {
            result = stringTo128(name.substr(last_us + 1)) + 1;
        }
    }
    mysql_free_result(rp);
    return result;
}

static int load_cursor(int128& next_index, bool& exhausted) {
    if (boinc_db.do_query("SELECT next_index, exhausted FROM camicia_wu_cursor WHERE id=1")) {
        return ERR_DB_NOT_FOUND;
    }
    MYSQL_RES* rp = mysql_store_result(boinc_db.mysql);
    if (!rp) return ERR_DB_NOT_FOUND;
    MYSQL_ROW row = mysql_fetch_row(rp);
    if (!row) {
        mysql_free_result(rp);
        return ERR_DB_NOT_FOUND;
    }
    next_index = stringTo128(row[0] ? row[0] : "0");
    exhausted = row[1] && atoi(row[1]) != 0;
    mysql_free_result(rp);
    return 0;
}

static int save_cursor(int128 next_index, bool exhausted) {
    char buf[512];
    snprintf(buf, sizeof(buf),
        "UPDATE camicia_wu_cursor SET next_index='%s', exhausted=%d WHERE id=1",
        int128ToString(next_index).c_str(), exhausted ? 1 : 0
    );
    return boinc_db.do_query(buf);
}

static int init_cursor_if_missing() {
    int128 next_index;
    bool exhausted;
    if (load_cursor(next_index, exhausted) == 0) return 0;

    int128 recovered = recover_cursor_from_workunits();
    char buf[512];
    snprintf(buf, sizeof(buf),
        "INSERT INTO camicia_wu_cursor (id, next_index, exhausted) VALUES (1, '%s', %d)",
        int128ToString(recovered).c_str(), recovered > max_index ? 1 : 0
    );
    return boinc_db.do_query(buf);
}

// --- job creation ---

// Returns 0 on success (including "already dispatched by a previous,
// interrupted run" - detected via the workunit.name UNIQUE constraint).
static int make_job(int128 start, int128 end) {
    DB_WORKUNIT wu;
    char name[256], path[MAXPATHLEN];
    const char* infiles[1];
    int retval;

    snprintf(name, sizeof(name), "%s_%s_%s",
        app_name, int128ToPadded(start).c_str(), int128ToPadded(end).c_str());

    retval = config.download_path(name, path);
    if (retval) return retval;
    FILE* f = fopen(path, "w");
    if (!f) return ERR_FOPEN;
    fprintf(f, "%s %s\n", int128ToString(start).c_str(), int128ToString(end).c_str());
    fclose(f);

    // end - start + 1, not range_size: the last WU in the space is
    // truncated to max_index (see main_loop() below) and is genuinely
    // shorter, so this keeps its declared cost accurate too instead of
    // overclaiming it the way a fixed range_size-based constant would.
    // The difference is at most range_size (1e9 by default), well within
    // double's exact-integer range, so this int128->double conversion
    // never loses precision here.
    double ndeals = (double)(end - start + 1);

    wu.clear();
    wu.appid = app.id;
    safe_strcpy(wu.name, name);
    wu.rsc_fpops_est = ndeals * FPOPS_PER_DEAL;
    wu.rsc_fpops_bound = wu.rsc_fpops_est * FPOPS_BOUND_FACTOR;
    wu.rsc_memory_bound = DEFAULT_RSC_MEMORY_BOUND;
    wu.rsc_disk_bound = DEFAULT_RSC_DISK_BOUND;
    wu.delay_bound = DEFAULT_DELAY_BOUND;
    wu.min_quorum = REPLICATION_FACTOR;
    wu.target_nresults = REPLICATION_FACTOR;
    wu.max_error_results = REPLICATION_FACTOR * 4;
    wu.max_total_results = REPLICATION_FACTOR * 8;
    wu.max_success_results = REPLICATION_FACTOR * 4;
    infiles[0] = name;

    char tmpl_path[MAXPATHLEN];
    snprintf(tmpl_path, sizeof(tmpl_path), "templates/%s", out_template_file);
    retval = create_work(
        wu, in_template, tmpl_path, config.project_path(tmpl_path),
        infiles, 1, config
    );
    if (retval) {
        // Could be a genuine failure, or this exact range was already
        // dispatched by a run that crashed after create_work() succeeded
        // but before the cursor commit. Distinguish by looking the name up.
        DB_WORKUNIT existing;
        char clause[300];
        snprintf(clause, sizeof(clause), "where name='%s'", name);
        if (existing.lookup(clause) == 0) {
            log_messages.printf(MSG_NORMAL,
                "%s already exists; treating as already dispatched\n", name);
            return 0;
        }
        return retval;
    }
    return 0;
}

static void main_loop() {
    while (1) {
        check_stop_daemons();

        int128 next_index;
        bool exhausted;
        if (load_cursor(next_index, exhausted)) {
            log_messages.printf(MSG_CRITICAL, "can't load cursor\n");
            exit(1);
        }

        if (exhausted) {
            static bool logged = false;
            if (!logged) {
                log_messages.printf(MSG_NORMAL,
                    "Index space exhausted at %s permutations; no more work to generate.\n",
                    int128ToString(max_index + 1).c_str());
                logged = true;
            }
            daemon_sleep(3600);
            continue;
        }

        long n;
        int retval = count_unsent_results(n, app.id);
        if (retval) {
            log_messages.printf(MSG_CRITICAL,
                "count_unsent_results() failed: %s\n", boincerror(retval));
            exit(retval);
        }

        if (n >= cushion) {
            daemon_sleep(10);
            continue;
        }

        int njobs = (cushion - n + REPLICATION_FACTOR - 1) / REPLICATION_FACTOR;
        log_messages.printf(MSG_DEBUG, "Making up to %d jobs\n", njobs);

        for (int i = 0; i < njobs; i++) {
            if (next_index > max_index) {
                exhausted = true;
                save_cursor(next_index, exhausted);
                break;
            }
            int128 start = next_index;
            int128 end = start + range_size - 1;
            if (end > max_index) end = max_index;

            retval = make_job(start, end);
            if (retval) {
                log_messages.printf(MSG_CRITICAL,
                    "can't make job for range %s-%s: %s\n",
                    int128ToString(start).c_str(), int128ToString(end).c_str(),
                    boincerror(retval));
                exit(retval);
            }

            next_index = end + 1;
            if (save_cursor(next_index, next_index > max_index)) {
                log_messages.printf(MSG_CRITICAL, "can't save cursor\n");
                exit(1);
            }
        }

        double now = dtime();
        while (1) {
            daemon_sleep(5);
            double x;
            retval = min_transition_time(x);
            if (retval) {
                log_messages.printf(MSG_CRITICAL,
                    "min_transition_time failed: %s\n", boincerror(retval));
                exit(retval);
            }
            if (x > now) break;
        }
    }
}

void usage(char* name) {
    fprintf(stderr,
        "Camicia work generator: keeps the job queue fed with permutation-index\n"
        "ranges until the full search space (0 .. %s) is exhausted.\n\n"
        "Usage: %s [OPTION]...\n\n"
        "Options:\n"
        "  [ --app X                  Application name (default: simulator)\n"
        "  [ --range_size N           Permutations per workunit (default: 1000000000)\n"
        "  [ --cushion N              Unsent job instances to maintain (default: 20)\n"
        "  [ --in_template_file X     Input template filename (default: simulator_in.xml)\n"
        "  [ --out_template_file X    Output template filename (default: simulator_out.xml)\n"
        "  [ -d X ]                   Sets debug level to X.\n",
        MAX_INDEX_STR, name
    );
}

int main(int argc, char** argv) {
    int retval;
    char buf[256];
    std::string range_size_str = "1000000000";

    for (int i = 1; i < argc; i++) {
        if (is_arg(argv[i], "d")) {
            if (!argv[++i]) { usage(argv[0]); exit(1); }
            int dl = atoi(argv[i]);
            log_messages.set_debug_level(dl);
            if (dl == 4) g_print_queries = true;
        } else if (!strcmp(argv[i], "--app")) {
            app_name = argv[++i];
        } else if (!strcmp(argv[i], "--range_size")) {
            range_size_str = argv[++i];
        } else if (!strcmp(argv[i], "--cushion")) {
            cushion = atol(argv[++i]);
        } else if (!strcmp(argv[i], "--in_template_file")) {
            in_template_file = argv[++i];
        } else if (!strcmp(argv[i], "--out_template_file")) {
            out_template_file = argv[++i];
        } else if (is_arg(argv[i], "h") || is_arg(argv[i], "help")) {
            usage(argv[0]);
            exit(0);
        } else {
            log_messages.printf(MSG_CRITICAL, "unknown command line argument: %s\n\n", argv[i]);
            usage(argv[0]);
            exit(1);
        }
    }

    range_size = stringTo128(range_size_str);
    max_index = stringTo128(MAX_INDEX_STR);
    if (range_size <= 0) {
        log_messages.printf(MSG_CRITICAL, "--range_size must be positive\n");
        exit(1);
    }
    if (range_size > max_index + 1) {
        // Sanity bound, not an operational limit: a range larger than the
        // entire search space can only be an operator typo (e.g. an extra
        // zero), so fail loudly instead of silently accepting it.
        log_messages.printf(MSG_CRITICAL,
            "--range_size (%s) is larger than the entire index space (%s permutations) "
            "-- this looks like a typo\n",
            range_size_str.c_str(), int128ToString(max_index + 1).c_str());
        exit(1);
    }
    if (cushion <= 0) {
        log_messages.printf(MSG_CRITICAL, "--cushion must be positive\n");
        exit(1);
    }
    if (cushion > 100000) {
        // Same idea: not a real operational ceiling, just a typo guard.
        log_messages.printf(MSG_CRITICAL,
            "--cushion (%ld) is implausibly large -- this looks like a typo\n", cushion);
        exit(1);
    }

    retval = config.parse_file();
    if (retval) {
        log_messages.printf(MSG_CRITICAL, "Can't parse config.xml: %s\n", boincerror(retval));
        exit(1);
    }

    retval = boinc_db.open(config.db_name, config.db_host, config.db_user, config.db_passwd);
    if (retval) {
        log_messages.printf(MSG_CRITICAL, "can't open db: %s\n", boinc_db.error_string());
        exit(1);
    }

    snprintf(buf, sizeof(buf), "where name='%s'", app_name);
    if (app.lookup(buf)) {
        log_messages.printf(MSG_CRITICAL, "can't find app %s\n", app_name);
        exit(1);
    }

    snprintf(buf, sizeof(buf), "templates/%s", in_template_file);
    if (read_file_malloc(config.project_path(buf), in_template)) {
        log_messages.printf(MSG_CRITICAL, "can't read input template %s\n", buf);
        exit(1);
    }

    if (ensure_cursor_table()) {
        log_messages.printf(MSG_CRITICAL, "can't create camicia_wu_cursor table\n");
        exit(1);
    }
    if (init_cursor_if_missing()) {
        log_messages.printf(MSG_CRITICAL, "can't initialize cursor\n");
        exit(1);
    }

    log_messages.printf(MSG_NORMAL,
        "Starting: range_size=%s cushion=%ld max_index=%s\n",
        range_size_str.c_str(), cushion, MAX_INDEX_STR);

    main_loop();
}
