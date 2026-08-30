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

using std::vector;
using std::string;

const char* outdir = "../results";

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

// wu.name is always "simulator_<start>_<end>" (work_generator.cpp's own
// naming, zero-padded decimal -- stringTo128()-compatible either way).
// Split on the first/last underscore rather than assuming exactly one
// field between them, matching the same defensive convention
// work_generator.cpp's own resume-cursor logic already uses (find_last_of
// for the end field).
bool parse_wu_range(const char* wu_name, string& start, string& end) {
    string name(wu_name);
    size_t first_us = name.find_first_of('_');
    size_t last_us = name.find_last_of('_');
    if (first_us == string::npos || last_us == string::npos || first_us >= last_us) {
        return false;
    }
    start = name.substr(first_us + 1, last_us - first_us - 1);
    end = name.substr(last_us + 1);
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
    while ((n = read(pipefd[0], buf, sizeof(buf))) > 0) {
        output_capture.append(buf, n);
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
    sprintf(path, "%s/verify_rejections.log", outdir);
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
    sprintf(batch_dir, "%s/%d", outdir, wu.batch);
    int retval = boinc_mkdir(batch_dir);
    if (retval) return retval;
    sprintf(path, "%s/%s_verify_rejected", batch_dir, wu.name);
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

// Parses one already-read results.txt line (before the wu.name prefix is
// added below) and updates the record files above when it's a new
// longest finished game or any loop at all.
void track_result_line(const char* wu_name, const char* line) {
    char status[16];
    if (sscanf(line, "%15[^,],", status) != 1) return;

    char deal_index[64];
    long long cards, tricks;
    if (!strcmp(status, "finished")) {
        if (sscanf(line, "finished,%63[^,],%lld,%lld", deal_index, &cards, &tricks) == 3) {
            maybe_update_longest_record(wu_name, cards, tricks, deal_index);
        }
    } else if (!strcmp(status, "loop")) {
        if (sscanf(line, "loop,%63[^,],%lld,%lld", deal_index, &cards, &tricks) == 3) {
            record_loop_found(wu_name, deal_index);
        }
    }
}

int write_error(WORKUNIT &wu, char* p) {
    char batch_dir[1024];
    char path[1024];
    sprintf(batch_dir, "%s/%d", outdir, wu.batch);
    int retval = boinc_mkdir(batch_dir);
    if (retval) return retval;
    sprintf(path, "%s/%s_error", batch_dir, wu.name);
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
            sprintf(buf, "get_output_file_infos() failed: %d\n", retval);
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
            sprintf(buf, "assimilate: canonical result has no output files\n");
            return write_error(wu, buf);
        }
        string wu_start, wu_end;
        if (!parse_wu_range(wu.name, wu_start, wu_end)) {
            sprintf(buf, "assimilate: couldn't parse start/end from wu.name '%s'\n", wu.name);
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
        sprintf(buf, "%s/results.txt", outdir);
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
        sprintf(buf_err, "0x%x\n", wu.error_mask);
        return write_error(wu, buf_err);
    }
    
    return 0;
}