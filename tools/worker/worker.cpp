#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <algorithm>
#include <cstdio>
#include "boinc_api.h"
#include "engine.hpp"
#include "permutation.hpp"
#include "int128_io.hpp"
#include "filesys.h"

using namespace std;

#define CHECKPOINT_FILE "camicia_state"
#define LOOPS_FILE "camicia_loops"
#define INPUT_FILENAME "in"
#define OUTPUT_FILENAME "out"

// Test-only override: forces a checkpoint on every iteration instead of
// waiting on BOINC's own (real-time-based) boinc_time_to_checkpoint(),
// which a short-lived test process has no reliable way to trigger.
// Production never sets this env var, so this is a no-op there -- see
// tests/test_resume.sh's "orphaned camicia_loops" scenario for the actual
// regression test this exists for (cce5745's fix only has an observable
// effect once a checkpoint genuinely appends to LOOPS_FILE mid-run, which
// a purely file-seeded test can't reach on its own).
static bool should_checkpoint() {
    if (getenv("CAMICIA_FORCE_CHECKPOINT")) return true;
    return boinc_time_to_checkpoint();
}

struct GameResultInfo {
    int128 index;
    long long cards;
    long long tricks;
};

struct WorkerState {
    int128 currentIndex;
    int128 startIndex;
    int128 endIndex;
    GameResultInfo bestFinished;
    std::vector<GameResultInfo> loops;
};

// loopsAppended tracks how many of state.loops are already durably written
// to LOOPS_FILE (from earlier in this same run, or reloaded from a resume --
// see main()). Checkpointing used to rewrite every already-recorded loop's
// text on every single call (O(total loops so far) per checkpoint, not
// O(new loops)); a loop-dense workunit could mean rewriting hundreds of
// thousands of lines repeatedly throughout a run. Now only the delta since
// the last checkpoint gets appended.
//
// Ordering matters for crash-safety: the loops-file append happens BEFORE
// the header's currentIndex is atomically advanced past those indices, so a
// crash between the two never lets the header claim an index is "done" when
// its loop result isn't durably on disk yet. That ordering alone would still
// risk a duplicate on resume (reprocessing an index whose loop already made
// it to LOOPS_FILE before the crash, appending it a second time) -- what
// actually prevents that is the dedup guard in main()'s loop, which only
// push_back()s a loop if its index is past the highest one already known
// (freshly found or reloaded from LOOPS_FILE at resume). That guard is what
// makes this whole scheme safe regardless of exactly when a crash lands
// relative to the append/header-write boundary, not the ordering by itself.
void do_checkpoint(WorkerState& state, size_t& loopsAppended) {
    if (state.loops.size() > loopsAppended) {
        string loops_resolved;
        boinc_resolve_filename_s(LOOPS_FILE, loops_resolved);
        FILE* lf = boinc_fopen(loops_resolved.c_str(), "a");
        if (lf) {
            for (size_t i = loopsAppended; i < state.loops.size(); ++i) {
                const auto& l = state.loops[i];
                fprintf(lf, "%s %lld %lld\n", int128ToString(l.index).c_str(), l.cards, l.tricks);
            }
            fclose(lf);
            loopsAppended = state.loops.size();
        }
    }

    string resolved_name;
    boinc_resolve_filename_s(CHECKPOINT_FILE, resolved_name);
    FILE* f = boinc_fopen("temp", "w");
    if (!f) return;

    string cur = int128ToString(state.currentIndex + 1);
    string start = int128ToString(state.startIndex);
    string end = int128ToString(state.endIndex);
    string bestIdx = int128ToString(state.bestFinished.index);

    fprintf(f, "%s %s %s %s %lld %lld\n",
            cur.c_str(), start.c_str(), end.c_str(),
            bestIdx.c_str(), state.bestFinished.cards, state.bestFinished.tricks);

    fclose(f);
    boinc_rename("temp", resolved_name.c_str());
    boinc_checkpoint_completed();
}

int main(int argc, char** argv) {
    boinc_init();

    WorkerState state = {0, 0, 0, {0, 0, 0}, {}};
    char input_path[512], chkpt_path[512], output_path[512];
    
    boinc_resolve_filename(INPUT_FILENAME, input_path, sizeof(input_path));
    boinc_resolve_filename(CHECKPOINT_FILE, chkpt_path, sizeof(chkpt_path));
    boinc_resolve_filename(OUTPUT_FILENAME, output_path, sizeof(output_path));

    FILE* chkpt = boinc_fopen(chkpt_path, "r");
    bool resumed = false;
    if (chkpt) {
        char cur[128], start[128], end[128], bestIdxStr[128];
        if (fscanf(chkpt, "%127s %127s %127s %127s %lld %lld",
                   cur, start, end, bestIdxStr,
                   &state.bestFinished.cards, &state.bestFinished.tricks) == 6) {
            state.currentIndex = stringTo128(cur);
            state.startIndex = stringTo128(start);
            state.endIndex = stringTo128(end);
            state.bestFinished.index = stringTo128(bestIdxStr);
            resumed = true;
        }
        fclose(chkpt);
    }

    // LOOPS_FILE is independently authoritative for which loops have
    // already been durably recorded -- read whatever complete lines
    // currently exist in it, regardless of exactly when the last
    // checkpoint's header write landed relative to the last loops-file
    // append (see do_checkpoint()'s own comment on why that ordering alone
    // isn't what guarantees no duplicates). A malformed/partial trailing
    // line, from a crash mid-append, is silently skipped by fscanf's own
    // failure check -- the same behavior the original single-file format
    // already relied on for a truncated final entry.
    size_t loopsAppended = 0;
    if (resumed) {
        string loops_resolved;
        boinc_resolve_filename_s(LOOPS_FILE, loops_resolved);
        FILE* lf = boinc_fopen(loops_resolved.c_str(), "r");
        if (lf) {
            char lIdxStr[128];
            long long lCards, lTricks;
            while (fscanf(lf, "%127s %lld %lld", lIdxStr, &lCards, &lTricks) == 3) {
                state.loops.push_back({stringTo128(lIdxStr), lCards, lTricks});
            }
            fclose(lf);
        }
        loopsAppended = state.loops.size();
    } else {
        // Not resuming (first run, or CHECKPOINT_FILE missing/unreadable
        // e.g. a crash or full disk mid-write) -- any LOOPS_FILE content
        // from an earlier attempt is now orphaned: we're about to
        // reprocess this range from scratch (state.startIndex), which
        // would re-find the same loops and, since do_checkpoint() only
        // ever appends, duplicate them alongside the stale entries.
        // Truncate so this run starts from a clean file, matching
        // loopsAppended staying 0 here.
        string loops_resolved;
        boinc_resolve_filename_s(LOOPS_FILE, loops_resolved);
        FILE* lf = boinc_fopen(loops_resolved.c_str(), "w");
        if (lf) fclose(lf);
    }

    if (!resumed) {
        FILE* in = boinc_fopen(input_path, "r");
        if (!in) {
            fprintf(stderr, "Missing input file\n");
            boinc_finish(1);
        }
        char startStr[128], endStr[128];
        if (fscanf(in, "%127s %127s", startStr, endStr) != 2) {
            fprintf(stderr, "Invalid input format\n");
            boinc_finish(1);
        }
        state.startIndex = stringTo128(startStr);
        state.currentIndex = state.startIndex;
        state.endIndex = stringTo128(endStr);
        fclose(in);
    }

    int128 totalToProcess = state.endIndex - state.startIndex;
    if (totalToProcess == 0) totalToProcess = 1;

    for (; state.currentIndex <= state.endIndex; state.currentIndex++) {
        std::vector<std::string> deck = getNthPermutation(state.currentIndex);
        std::vector<std::string> a(deck.begin(), deck.begin() + 26);
        std::vector<std::string> b(deck.begin() + 26, deck.end());
        
        CamiciaGame game(a, b);
        GameResult res = game.simulate();

        if (res.status == "finished") {
            if (res.cards > state.bestFinished.cards) {
                state.bestFinished.cards = res.cards;
                state.bestFinished.tricks = res.tricks;
                state.bestFinished.index = state.currentIndex;
            }
        } else if (res.status == "loop") {
            // Dedup guard: after a resume that reloaded already-durable
            // loops from LOOPS_FILE, reprocessing can land on an index
            // whose loop is already known (see the comments on
            // do_checkpoint()/the resume-read above for why that can
            // happen even with the append-before-header-update ordering).
            // Indices only ever increase, and loops are recorded in that
            // same increasing order, so comparing against the last known
            // entry is sufficient -- no need to search the whole vector.
            if (state.loops.empty() || state.currentIndex > state.loops.back().index) {
                state.loops.push_back({state.currentIndex, res.cards, res.tricks});
            }
        }

        if (should_checkpoint()) {
            do_checkpoint(state, loopsAppended);
        }
        
        double fraction = (double)(state.currentIndex - state.startIndex) / (double)totalToProcess;
        boinc_fraction_done(fraction);
    }

    // Output order must stay deterministic (single-threaded, ascending
    // currentIndex): sample_bitwise_validator hashes the whole output file
    // byte-for-byte with no order-independence, so if this loop or the
    // writes below are ever parallelized/reordered, replica outputs will
    // silently stop matching and affected WUs will never reach quorum.
    FILE* out = boinc_fopen(output_path, "w");
    if (out) {
        if (state.bestFinished.cards > 0) {
            fprintf(out, "finished,%s,%lld,%lld\n", 
                    int128ToString(state.bestFinished.index).c_str(), 
                    state.bestFinished.cards, state.bestFinished.tricks);
        }
        for (const auto& l : state.loops) {
            fprintf(out, "loop,%s,%lld,%lld\n", 
                    int128ToString(l.index).c_str(), l.cards, l.tricks);
        }
        fclose(out);
    }

    boinc_finish(0);
    return 0;
}
