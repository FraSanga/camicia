#include <vector>
#include <string>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>

#include "boinc_db.h"
#include "error_numbers.h"
#include "filesys.h"
#include "sched_msgs.h"
#include "validate_util.h"
#include "assimilate_handler.h"

using std::vector;
using std::string;

const char* outdir = "../results";

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
    WORKUNIT& wu, vector<RESULT>& /*results*/, RESULT& canonical_result
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