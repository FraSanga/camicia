#include <vector>
#include <string>
#include <cstdio>
#include <cstdlib>

#include "boinc_db.h"
#include "error_numbers.h"
#include "filesys.h"
#include "sched_msgs.h"
#include "validate_util.h"
#include "assimilate_handler.h"

using std::vector;
using std::string;

const char* outdir = "../results";

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