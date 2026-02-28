#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <algorithm>
#include <cstdio>
#include "boinc_api.h"
#include "engine.hpp"
#include "permutation.hpp"
#include "filesys.h"

using namespace std;

#define CHECKPOINT_FILE "camicia_state"
#define INPUT_FILENAME "in"
#define OUTPUT_FILENAME "out"

int128 stringTo128(string s) {
    int128 res = 0;
    for (char c : s) {
        if (c >= '0' && c <= '9') {
            res = res * 10 + (c - '0');
        }
    }
    return res;
}

string int128ToString(int128 n) {
    if (n == 0) return "0";
    string s = "";
    while (n > 0) {
        s += (char)((n % 10) + '0');
        n /= 10;
    }
    reverse(s.begin(), s.end());
    return s;
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

void do_checkpoint(WorkerState& state) {
    string resolved_name;
    boinc_resolve_filename_s(CHECKPOINT_FILE, resolved_name);
    FILE* f = boinc_fopen("temp", "w");
    if (!f) return;

    string cur = int128ToString(state.currentIndex);
    string start = int128ToString(state.startIndex);
    string end = int128ToString(state.endIndex);
    string bestIdx = int128ToString(state.bestFinished.index);
    
    fprintf(f, "%s %s %s %s %lld %lld %zu\n", 
            cur.c_str(), start.c_str(), end.c_str(), 
            bestIdx.c_str(), state.bestFinished.cards, state.bestFinished.tricks,
            state.loops.size());
            
    for (const auto& l : state.loops) {
        fprintf(f, "%s %lld %lld\n", int128ToString(l.index).c_str(), l.cards, l.tricks);
    }
    
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
        size_t loopCount;
        if (fscanf(chkpt, "%s %s %s %s %lld %lld %zu", 
                   cur, start, end, bestIdxStr, 
                   &state.bestFinished.cards, &state.bestFinished.tricks, 
                   &loopCount) == 7) {
            state.currentIndex = stringTo128(cur);
            state.startIndex = stringTo128(start);
            state.endIndex = stringTo128(end);
            state.bestFinished.index = stringTo128(bestIdxStr);
            
            for (size_t i = 0; i < loopCount; ++i) {
                char lIdxStr[128];
                long long lCards, lTricks;
                if (fscanf(chkpt, "%s %lld %lld", lIdxStr, &lCards, &lTricks) == 3) {
                    state.loops.push_back({stringTo128(lIdxStr), lCards, lTricks});
                }
            }
            resumed = true;
        }
        fclose(chkpt);
    }

    if (!resumed) {
        FILE* in = boinc_fopen(input_path, "r");
        if (!in) {
            fprintf(stderr, "Missing input file\n");
            boinc_finish(1);
        }
        char startStr[128], endStr[128];
        if (fscanf(in, "%s %s", startStr, endStr) != 2) {
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
            state.loops.push_back({state.currentIndex, res.cards, res.tricks});
        }

        if (boinc_time_to_checkpoint()) {
            do_checkpoint(state);
        }
        
        double fraction = (double)(state.currentIndex - state.startIndex) / (double)totalToProcess;
        boinc_fraction_done(fraction);
    }

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
