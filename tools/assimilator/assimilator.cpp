#include <vector>
#include <string>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <unistd.h>
#include <sys/wait.h>

#include "boinc_db.h"
#include "error_numbers.h"
#include "filesys.h"
#include "sched_msgs.h"
#include "validate_util.h"
#include "assimilate_handler.h"
#include "int128_io.hpp"

using std::vector;
using std::string;

const char* outdir = "../results";

// Camicia's declared permutation-index space: legal deal indices are
// [0, MAX_INDEX], i.e. MAX_INDEX+1 distinct 52-card deals (4x A/K/Q/J +
// 36 number cards). Same value work_generator.cpp/worker.cpp already
// hardcode (kept as an independent copy here, not shared via a header, so
// this fix doesn't touch those files' builds).
static const char* MAX_INDEX_STR = "653534134886878244999";

// Authoritative spot-check gate, run once per workunit right before its
// canonical result becomes part of results.txt -- see run_verify_sample()
// below for how it's invoked, and tools/verify_sample/verify_sample.cpp's
// own header comment for the full design rationale (what this can/can't
// catch, why the sample size is a confidence/defect-rate policy choice
// independent of block size). C = 99.99%, p = 0.001% chosen deliberately:
// at ~921k samples this costs a few seconds of CPU per workunit -- utterly
// negligible next to the hours a real client spends producing the block
// in the first place -- so there's no reason to pick anything looser.
// Not "./verify_sample": this daemon's actual runtime cwd is
// tmp_<hostname>/ (BOINC's own daemon framework chdirs there after
// bin/start launches it, same reason outdir below is "../results" and
// not "results"), a sibling of bin/ under the project root, not bin/
// itself, where tools.sh/publish_version.sh actually compile this to.
const char* VERIFY_SAMPLE_BIN = "../bin/verify_sample";
const char* VERIFY_CONFIDENCE = "0.9999";
const char* VERIFY_DEFECT_RATE = "0.00001";

// wu.name is either "simulator_<start>_<end>" (work_generator.cpp's own
// naming, zero-padded decimal -- stringTo128()-compatible either way) or,
// for a redispatched WU, "simulator_<start>_<end>_r<timestamp>" (see
// work_generator.cpp's redispatch_rejected_wus()). <start> is always the
// field between the 1st and 2nd underscore, <end> always the field between
// the 2nd and 3rd underscore (or end-of-string, if there's no redispatch
// suffix) -- splitting on first/last underscore instead (as this used to)
// grabs "<start>_<end>" as start and "r<timestamp>" as end for a
// redispatched WU, since its last underscore comes after <end>, not before
// it. Same bug class, and same fix, as work_generator.cpp's own
// recover_cursor_from_workunits() (CA-L4).
bool parse_wu_range(const char* wu_name, string& start, string& end) {
    string name(wu_name);
    size_t first_us = name.find_first_of('_');
    if (first_us == string::npos) return false;
    size_t second_us = name.find_first_of('_', first_us + 1);
    if (second_us == string::npos) return false;
    size_t third_us = name.find_first_of('_', second_us + 1);
    start = name.substr(first_us + 1, second_us - first_us - 1);
    end = (third_us == string::npos)
        ? name.substr(second_us + 1)
        : name.substr(second_us + 1, third_us - second_us - 1);
    return !start.empty() && !end.empty();
}

// Spawns verify_sample directly via fork/exec (no shell involved, so
// nothing here needs escaping) against one already-assimilated-candidate
// result file, capturing its stdout (the human-readable inconsistency
// report, if any) for write_verify_rejected() to record on failure.
// Deliberately never passes --seed: the whole point is that which deals get sampled is
// unpredictable from outside the server, so a cheat client can't
// precompute just the checked subset and fabricate the rest -- letting
// verify_sample fall through to its own random_device default is what
// keeps that true. Returns the child's exit status (0 = consistent,
// nonzero = verify_sample found at least one inconsistency or failed to
// run at all), or -1 if fork/exec itself failed.
int run_verify_sample(
    const string& start, const string& end, const string& result_file,
    string& output_capture
) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;

    pid_t pid = fork();
    if (pid < 0) {
        close(pipefd[0]);
        close(pipefd[1]);
        return -1;
    }

    if (pid == 0) {
        // child: stdout+stderr both go to the pipe, so a crash/usage
        // error shows up in the captured report too, not just a
        // consistency failure.
        dup2(pipefd[1], STDOUT_FILENO);
        dup2(pipefd[1], STDERR_FILENO);
        close(pipefd[0]);
        close(pipefd[1]);
        execl(
            VERIFY_SAMPLE_BIN, VERIFY_SAMPLE_BIN,
            "--start", start.c_str(),
            "--end", end.c_str(),
            "--result-file", result_file.c_str(),
            "--confidence", VERIFY_CONFIDENCE,
            "--defect-rate", VERIFY_DEFECT_RATE,
            (char*)nullptr
        );
        // execl only returns on failure (e.g. binary missing) -- _exit,
        // not exit/return, to avoid running any parent-process atexit
        // handlers twice in the forked child.
        _exit(127);
    }

    // parent
    close(pipefd[1]);
    char buf[4096];
    ssize_t n;
    // CA-L2 fix: cap how much of the child's output actually gets kept --
    // verify_sample's report isn't client-driven, so this isn't a real
    // attacker lever, but a runaway verify_sample bug (e.g. a print-per-deal
    // loop) could otherwise grow this string unboundedly and OOM the
    // assimilator. Still drain the pipe past the cap so the child never
    // blocks writing to a full pipe.
    static const size_t OUTPUT_CAPTURE_CAP = 512 * 1024;
    while ((n = read(pipefd[0], buf, sizeof(buf))) > 0) {
        if (output_capture.size() < OUTPUT_CAPTURE_CAP) {
            output_capture.append(buf, n);
        }
    }
    close(pipefd[0]);

    int status;
    waitpid(pid, &status, 0);
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return 1; // killed/signaled -- treat as a failure, not a pass
}

// Fires a push notification via bin/notify.sh's notify() shell function,
// which only *defines* that function rather than calling it -- so this
// sources it, then calls it with $1/$2/$3 (bash's own positional
// parameters) rather than string-interpolating title/message into the
// script text. That's what lets a WU name or a multi-line verify_sample
// report contain arbitrary characters with zero C++-side escaping. Runs
// synchronously (like run_verify_sample() above) rather than true
// fire-and-forget: this only ever runs on a loop-related rejection, which
// should be rare-to-never, and losing the one alert that matters most to
// a stray zombie-avoidance shortcut isn't worth it.
void send_notify(const string& title, const string& message, const string& priority) {
    pid_t pid = fork();
    if (pid < 0) return;
    if (pid == 0) {
        FILE* devnull = fopen("/dev/null", "w");
        if (devnull) {
            dup2(fileno(devnull), STDOUT_FILENO);
            dup2(fileno(devnull), STDERR_FILENO);
        }
        execl("/bin/bash", "bash", "-c",
            ". ./notify.sh && notify \"$1\" \"$2\" \"$3\"",
            "bash", title.c_str(), message.c_str(), priority.c_str(),
            (char*)nullptr
        );
        _exit(127);
    }
    int status;
    waitpid(pid, &status, 0);
}

// Appends one line per rejection to a standing audit trail, deliberately
// separate from the per-WU _verify_rejected file below: the point isn't
// re-reading one WU's detail, it's being able to grep this file later for
// the same host/user id showing up across *multiple, unrelated*
// rejections -- see assimilator.cpp's design discussion on why a single
// rejection can't distinguish a shared worker.cpp bug from a fabricated
// result (two independent hosts agreeing on the same wrong answer is
// already what quorum-of-2 is supposed to make astronomically unlikely by
// chance), but a repeated pattern for the same account is a much stronger
// signal, worth a human's judgment call via manage_user.php rather than
// anything automatic here. Logs every contributing result's host/user id,
// not just the canonical one -- `results` used to be an unused parameter
// (see the old /*results*/ comment this replaces) specifically because
// nothing needed it before this.
void log_verify_rejection(
    WORKUNIT& wu, vector<RESULT>& results, bool loop_related
) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/verify_rejections.log", outdir);
    FILE* f = fopen(path, "a");
    if (!f) return;
    fprintf(f, "%ld %s %s", (long)time(0), wu.name, loop_related ? "loop" : "best");
    for (const RESULT& r : results) {
        fprintf(f, " host=%lld/user=%lld", (long long)r.hostid, (long long)r.userid);
    }
    fprintf(f, "\n");
    fclose(f);
}

// Same shape as write_error() above, but a distinct filename/suffix so
// this specific failure class -- and only this one -- is what
// work_generator's redispatch scan watches for (it globs *_verify_rejected
// specifically, never the generic *_error files the other failure paths
// still use, which aren't things a redispatch would fix).
int write_verify_rejected(WORKUNIT &wu, const string& detail) {
    char batch_dir[1024];
    char path[1024];
    snprintf(batch_dir, sizeof(batch_dir), "%s/%d", outdir, wu.batch);
    int retval = boinc_mkdir(batch_dir);
    if (retval) return retval;
    snprintf(path, sizeof(path), "%s/%s_verify_rejected", batch_dir, wu.name);
    FILE* f = fopen(path, "a");
    if (!f) return ERR_FOPEN;
    fprintf(f, "%s", detail.c_str());
    fclose(f);
    return 0;
}

// records_longest.txt / records_loops.txt: tiny plain-text side files
// tracking the project's two headline findings (longest finished game,
// every loop found), updated right here as each result line is already
// being read -- not by periodically rescanning results.txt, which is kept
// forever and only grows. Deliberately plain space-separated fields, not
// JSON: a deal index exceeds 64 bits (MAX_INDEX is ~6.5e20), so it can
// only ever be carried as a string, and nothing here needs to parse it
// back as a number -- keeping this dependency-free (no JSON library) and
// cheap. html/ops/generate_progress_stats.php and html/user/progress.php
// are what turn these into what the progress page actually shows.
void maybe_update_longest_record(
    const char* wu_name, long long cards, long long tricks, const char* deal_index
) {
    const char* path = "../records_longest.txt";
    long long best_cards = -1;
    FILE* f = fopen(path, "r");
    if (f) {
        if (fscanf(f, "%lld", &best_cards) != 1) best_cards = -1;
        fclose(f);
    }
    if (cards <= best_cards) return;

    char tmp_path[256];
    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", path);
    FILE* tmp = fopen(tmp_path, "w");
    if (!tmp) return;
    fprintf(tmp, "%lld %lld %s %s %ld\n", cards, tricks, deal_index, wu_name, (long)time(0));
    fclose(tmp);
    rename(tmp_path, path);
}

void record_loop_found(const char* wu_name, const char* deal_index) {
    FILE* f = fopen("../records_loops.txt", "a");
    if (!f) return;
    fprintf(f, "%s %s %ld\n", deal_index, wu_name, (long)time(0));
    fclose(f);
}

// CA-M1 fix: records_longest.txt/records_loops.txt were previously
// updated straight from an already-read results.txt line with zero
// validation -- deal_index (a free-form string) and cards/tricks
// (unbounded long long) went straight into the two files
// html/user/progress.php displays as the project's headline findings.
// This runs downstream of quorum-of-2 + verify_sample's own random
// re-simulation gate (see assimilate_handler() below), but neither of
// those is guaranteed to catch a single fabricated "here's my best" line
// in an otherwise-honest block: verify_sample spot-checks a random
// SAMPLE of the block's own deals for internal consistency, it doesn't
// specifically re-derive and cross-check the claimed "best" line itself.
//
// Validates:
// - deal_index is pure ASCII digits (stringTo128() silently *skips*
//   non-digit characters rather than rejecting them, so "12x3" would
//   otherwise quietly become 123 instead of being caught here) and
//   parses to a value actually inside Camicia's declared permutation
//   space, [0, MAX_INDEX].
// - cards/tricks are non-negative.
// - tricks <= cards: a real invariant, not a heuristic guess -- traced
//   through engine.cpp's simulate(): every trick collects at least one
//   already-played card off the pile (RESULT_finished/loop lines can
//   only be produced by that function), so cumulative tricks won can
//   never exceed cumulative cards played.
//
// Deliberately does NOT impose a numeric ceiling on cards/tricks
// themselves -- the reachable-state space is astronomically large and
// legitimately long games are the entire point of this project, so
// there's no defensible bound to pick without risking rejecting a real
// record. The deal_index range check is the meaningful defense: it
// forces any accepted record to correspond to an actual possible deal.
bool valid_result_line(const char* deal_index, long long cards, long long tricks) {
    if (cards < 0 || tricks < 0 || tricks > cards) return false;
    if (!deal_index[0]) return false;
    for (const char* p = deal_index; *p; p++) {
        if (*p < '0' || *p > '9') return false;
    }
    static const int128 max_index = stringTo128(MAX_INDEX_STR);
    return stringTo128(deal_index) <= max_index;
}

// Parses one already-read results.txt line (before the wu.name prefix is
// added below) and updates the record files above when it's a new
// longest finished game or any loop at all.
void track_result_line(const char* wu_name, const char* line) {
    char status[16];
    if (sscanf(line, "%15[^,],", status) != 1) return;

    char deal_index[64];
    long long cards, tricks;
    if (!strcmp(status, "finished")) {
        if (sscanf(line, "finished,%63[^,],%lld,%lld", deal_index, &cards, &tricks) == 3
            && valid_result_line(deal_index, cards, tricks)) {
            maybe_update_longest_record(wu_name, cards, tricks, deal_index);
        }
    } else if (!strcmp(status, "loop")) {
        if (sscanf(line, "loop,%63[^,],%lld,%lld", deal_index, &cards, &tricks) == 3
            && valid_result_line(deal_index, cards, tricks)) {
            record_loop_found(wu_name, deal_index);
        }
    }
}

int write_error(WORKUNIT &wu, char* p) {
    char batch_dir[1024];
    char path[1024];
    snprintf(batch_dir, sizeof(batch_dir), "%s/%d", outdir, wu.batch);
    int retval = boinc_mkdir(batch_dir);
    if (retval) return retval;
    snprintf(path, sizeof(path), "%s/%s_error", batch_dir, wu.name);
    FILE* f = fopen(path, "a");
    if (!f) return ERR_FOPEN;
    fprintf(f, "%s", p);
    fclose(f);
    return 0;
}

int assimilate_handler_init(int argc, char** argv) {
    for (int i=1; i<argc; i++) {
        if (!strcmp(argv[i], "--outdir")) {
            outdir = argv[++i];
        } else {
            fprintf(stderr, "bad arg %s\n", argv[i]);
        }
    }
    return 0;
}

void assimilate_handler_usage() {
    // describe the project specific arguments here
    fprintf(stderr,
        "    Custom options:\n"
        "    [--outdir X]  output dir for result files\n"
    );
}

int assimilate_handler(
    WORKUNIT& wu, vector<RESULT>& results, RESULT& canonical_result
) {
    int retval;
    char buf[1024];
    retval = boinc_mkdir(outdir);
    if (retval) return retval;

    if (wu.canonical_resultid) {
        vector<OUTPUT_FILE_INFO> output_files;
        retval = get_output_file_infos(canonical_result, output_files);
        if (retval) {
            snprintf(buf, sizeof(buf), "get_output_file_infos() failed: %d\n", retval);
            return write_error(wu, buf);
        }

        // Authoritative gate: verify a random sample of this block's own
        // deals against the canonical result's output BEFORE any of it
        // becomes part of results.txt. This is the actual immutability
        // boundary (nothing below this point ever gets deleted or
        // reopened -- see rotate_results.sh/CLAUDE.md), so it's the right
        // and only place to reject a bad result: a failure here means the
        // canonical result simply never gets in, not that something
        // already-recorded needs undoing.
        //
        // Fails closed on every early-exit path (can't parse wu.name,
        // zero output files) -- an authoritative check that silently
        // no-ops on the inputs it doesn't understand isn't actually
        // authoritative. wu.name is always our own work_generator's
        // "simulator_<start>_<end>" though, so these paths should never
        // actually trigger outside of a deeper bug worth surfacing anyway.
        if (output_files.empty()) {
            snprintf(buf, sizeof(buf), "assimilate: canonical result has no output files\n");
            return write_error(wu, buf);
        }
        string wu_start, wu_end;
        if (!parse_wu_range(wu.name, wu_start, wu_end)) {
            snprintf(buf, sizeof(buf), "assimilate: couldn't parse start/end from wu.name '%s'\n", wu.name);
            return write_error(wu, buf);
        }
        string verify_output;
        int verify_status = run_verify_sample(
            wu_start, wu_end, output_files[0].path, verify_output
        );
        if (verify_status != 0) {
            // Loop-related vs. best-related is a real, not cosmetic,
            // distinction -- see verify_sample.cpp's anomaly messages:
            // every one that involves a loop claim (found-but-unflagged,
            // or flagged-but-false) is the only kind of message that ever
            // contains the word "loop"; every best-related message talks
            // about cards/tricks/"the recorded best" instead. A block can
            // in principle raise both kinds at once -- treat it as
            // loop-related (the louder path) if any anomaly is.
            bool loop_related = verify_output.find("loop") != string::npos;

            log_verify_rejection(wu, results, loop_related);

            if (loop_related) {
                // Deliberately does NOT write a corrected/confirmed loop
                // line into results.txt automatically, even though
                // verify_sample's own re-simulation already knows the
                // true answer -- writing straight into the permanent,
                // immutable scientific record with no human sign-off is
                // the same class of hard-to-reverse action as an account
                // ban, and belongs in the same "software escalates, a
                // person decides" bucket. A human can hand-verify and
                // record it immediately from this alert if they want to
                // move faster than waiting on the redispatch below.
                char title[256];
                snprintf(title, sizeof(title),
                    "Camicia: possible loop found in %s", wu.name);
                send_notify(title, verify_output, "urgent");
            }

            char header[256];
            snprintf(header, sizeof(header),
                "assimilate: verify_sample rejected this result (exit %d):\n",
                verify_status
            );
            string full_msg = string(header) + verify_output;
            return write_verify_rejected(wu, full_msg);
        }

        // Safe only because config.xml runs a single, unsharded assimilator
        // daemon -- writes are strictly sequential from one process. If a
        // second --mod-sharded instance is ever added to relieve backlog,
        // concurrent buffered fopen("a") writes from two processes can
        // interleave mid-flush and corrupt results.txt; switch to raw
        // O_APPEND writes or per-shard output files first.
        snprintf(buf, sizeof(buf), "%s/results.txt", outdir);
        FILE* f_out = fopen(buf, "a");
        if (!f_out) {
            fprintf(stderr, "Error opening %s\n", buf);
            return ERR_FOPEN; 
        }

        for (const OUTPUT_FILE_INFO& fi: output_files) {
            FILE* f_in = fopen(fi.path.c_str(), "r");
            if (f_in) {
                // getline grows its buffer as needed, so a line longer than
                // any fixed-size stack buffer still comes back whole instead
                // of being silently split across multiple prefixed rows.
                char* line = nullptr;
                size_t line_cap = 0;
                ssize_t len;
                while ((len = getline(&line, &line_cap, f_in)) != -1) {
                    track_result_line(wu.name, line);
                    fprintf(f_out, "%s,%s", wu.name, line);
                    if (len == 0 || line[len - 1] != '\n') fprintf(f_out, "\n");
                }
                free(line);
                fclose(f_in);
            } else {
                fclose(f_out);
                fprintf(stderr, "Error while reading %s\n", fi.path.c_str());
                return ERR_FOPEN; 
            }
        }
        fclose(f_out);
    } else {
        char buf_err[1024];
        snprintf(buf_err, sizeof(buf_err), "0x%x\n", wu.error_mask);
        return write_error(wu, buf_err);
    }

    return 0;
}

#ifdef CAMICIA_TEST_RECORDS
// Standalone regression test for the CA-M1 validation gate
// (valid_result_line() / track_result_line()) -- compiled with
// -DCAMICIA_TEST_RECORDS, and deliberately NOT linked against BOINC's own
// sched/assimilator.cpp (which normally supplies main() and calls
// assimilate_handler()), so this file's own main() below is the real
// entry point instead. Works because none of the functions under test
// touch BOINC types -- they take plain C strings/numbers and read/write
// local text files only; assimilate_handler()/write_error()/etc are still
// compiled into this binary (their referenced BOINC symbols are resolved
// by the same libsched.a/libboinc.a link line as the real binary) but
// never called. Zero cost in the production build (this whole block
// compiles out without the flag). Self-contained, no BOINC/DB/network
// dependency -- can run anywhere, same spirit as
// tests/test_permutation.cpp/test_engine.cpp (though those live in
// tests/ since they're pure host-side; this one stays here since it's
// gated behind a flag on the real daemon source rather than a separate
// TU including a shared header).
#include <sys/stat.h>

static bool file_contains(const char* path, const char* needle) {
    FILE* f = fopen(path, "r");
    if (!f) return false;
    char buf[4096];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = 0;
    return strstr(buf, needle) != nullptr;
}

static long long file_line_count(const char* path) {
    FILE* f = fopen(path, "r");
    if (!f) return 0;
    long long n = 0;
    int c;
    while ((c = fgetc(f)) != EOF) if (c == '\n') n++;
    fclose(f);
    return n;
}

int main() {
    int failures_early = 0;
    auto check_early = [&](const char* label, bool cond) {
        printf("%s %s\n", cond ? "PASS" : "FAIL", label);
        if (!cond) failures_early++;
    };

    // parse_wu_range() regression test: a redispatched WU's name
    // (simulator_<start>_<end>_r<timestamp>) used to make this grab
    // "<start>_<end>" as start and "r<timestamp>" as end (splitting on the
    // first/last underscore, but the last underscore in a redispatch name
    // comes after <end>, not before it) -- same bug class as
    // work_generator.cpp's CA-L4.
    {
        string start, end;
        bool ok = parse_wu_range(
            "simulator_000000000000000000000_000000000000000001000000",
            start, end
        );
        check_early("normal name parses", ok);
        check_early("normal name: start field", start == "000000000000000000000");
        check_early("normal name: end field", end == "000000000000000001000000");
    }
    {
        string start, end;
        bool ok = parse_wu_range(
            "simulator_000000000000000000000_000000000000000001000000_r1756761234",
            start, end
        );
        check_early("redispatch name parses", ok);
        check_early("redispatch name: start field", start == "000000000000000000000");
        check_early("redispatch name: end field is <end>, not the timestamp",
            end == "000000000000000001000000");
    }
    if (failures_early > 0) {
        printf("\nFAILED (%d, parse_wu_range)\n", failures_early);
        return 1;
    }

    char tmpl[] = "/tmp/camicia_ca_m1_test_XXXXXX";
    char* tmpdir = mkdtemp(tmpl);
    if (!tmpdir) {
        fprintf(stderr, "mkdtemp failed\n");
        return 1;
    }
    char subdir[300], longest_path[300], loops_path[300];
    snprintf(subdir, sizeof(subdir), "%s/sub", tmpdir);
    snprintf(longest_path, sizeof(longest_path), "%s/records_longest.txt", tmpdir);
    snprintf(loops_path, sizeof(loops_path), "%s/records_loops.txt", tmpdir);
    mkdir(subdir, 0755);
    if (chdir(subdir) != 0) {
        fprintf(stderr, "chdir failed\n");
        return 1;
    }
    // track_result_line()'s two sinks resolve "../records_longest.txt" /
    // "../records_loops.txt" relative to cwd -- from subdir/, that's
    // tmpdir/records_longest.txt and tmpdir/records_loops.txt.

    int failures = 0;
    auto check = [&](const char* label, bool cond) {
        printf("%s %s\n", cond ? "PASS" : "FAIL", label);
        if (!cond) failures++;
    };

    // 1) legit finished line -> becomes the record (best_cards starts at -1)
    track_result_line("wu_a", "finished,1000,500,250\n");
    check("legit finished line recorded",
        file_contains(longest_path, "500 250 1000 wu_a"));

    // 2) legit loop line -> appended
    track_result_line("wu_b", "loop,2000,10,5\n");
    check("legit loop line recorded", file_contains(loops_path, "2000 wu_b"));

    // 3) deal_index with embedded junk -> rejected. Before this fix,
    //    stringTo128() would have silently skipped the 'x' and stored 123.
    track_result_line("wu_evil1", "finished,12x3,999,999\n");
    check("junk deal_index rejected (record unchanged)",
        file_contains(longest_path, "500 250 1000 wu_a"));
    check("junk deal_index never written",
        !file_contains(longest_path, "12x3") && !file_contains(longest_path, "123 999 999"));

    // 4) out-of-range deal_index (MAX_INDEX + 1) -> rejected
    track_result_line("wu_evil2", "finished,653534134886878245000,999999,999999\n");
    check("out-of-range deal_index rejected",
        file_contains(longest_path, "500 250 1000 wu_a"));

    // 5) negative cards -> rejected
    track_result_line("wu_evil3", "finished,3000,-5,2\n");
    check("negative cards rejected", file_contains(longest_path, "500 250 1000 wu_a"));

    // 6) tricks > cards -> rejected (the traced-through-engine.cpp invariant)
    track_result_line("wu_evil4", "finished,4000,10,999\n");
    check("tricks>cards rejected", file_contains(longest_path, "500 250 1000 wu_a"));

    // 7) a genuinely better finished line -> DOES become the new record
    //    (confirms the gate doesn't collaterally block legitimate updates)
    track_result_line("wu_c", "finished,5000,600,300\n");
    check("better legit record accepted", file_contains(longest_path, "600 300 5000 wu_c"));

    // 8) a worse finished line -> still correctly ignored (pre-existing
    //    maybe_update_longest_record() behavior, unaffected by this fix)
    track_result_line("wu_d", "finished,6000,1,1\n");
    check("worse legit record still ignored", file_contains(longest_path, "600 300 5000 wu_c"));

    // 9) a second, independent loop -> appended alongside the first
    track_result_line("wu_e", "loop,7000,20,10\n");
    check("second loop appended", file_contains(loops_path, "7000 wu_e"));
    check("first loop still present", file_contains(loops_path, "2000 wu_b"));
    check("exactly 2 loop lines (evil lines never reached record_loop_found)",
        file_line_count(loops_path) == 2);

    remove(longest_path);
    char longest_tmp_path[320];
    snprintf(longest_tmp_path, sizeof(longest_tmp_path), "%s.tmp", longest_path);
    remove(longest_tmp_path);
    remove(loops_path);
    rmdir(subdir);
    rmdir(tmpdir);

    if (failures == 0) {
        printf("\nPASSED\n");
        return 0;
    }
    printf("\nFAILED (%d)\n", failures);
    return 1;
}
#endif
