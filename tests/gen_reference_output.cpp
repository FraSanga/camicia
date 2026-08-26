// Test-only helper: replays worker.cpp's own simulate-and-record loop over
// a given index range and writes the same plain output format it does (the
// `out` file / templates/simulator_out.xml). Used by test_verify_sample.sh
// to produce known-correct ground truth to check verify_sample against --
// not shipped as part of the project's own toolchain.

#include <cstdio>
#include <string>
#include <vector>

#include "int128_io.hpp"
#include "permutation.hpp"
#include "engine.hpp"

using namespace std;

int main(int argc, char** argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s <start> <end> <outfile>\n", argv[0]);
        return 2;
    }
    int128 start = stringTo128(argv[1]);
    int128 end = stringTo128(argv[2]);

    int128 bestIndex = 0;
    long long bestCards = 0, bestTricks = 0;
    vector<string> loopLines;

    for (int128 i = start; i <= end; i++) {
        vector<string> deck = getNthPermutation(i);
        vector<string> a(deck.begin(), deck.begin() + 26);
        vector<string> b(deck.begin() + 26, deck.end());
        CamiciaGame game(a, b);
        GameResult res = game.simulate();

        if (res.status == "finished") {
            if (res.cards > bestCards) {
                bestCards = res.cards;
                bestTricks = res.tricks;
                bestIndex = i;
            }
        } else if (res.status == "loop") {
            loopLines.push_back("loop," + int128ToString(i) + "," +
                                 to_string(res.cards) + "," + to_string(res.tricks));
        }
    }

    FILE* out = fopen(argv[3], "w");
    if (!out) {
        fprintf(stderr, "cannot open %s for writing\n", argv[3]);
        return 1;
    }
    if (bestCards > 0) {
        fprintf(out, "finished,%s,%lld,%lld\n", int128ToString(bestIndex).c_str(), bestCards, bestTricks);
    }
    for (const auto& l : loopLines) {
        fprintf(out, "%s\n", l.c_str());
    }
    fclose(out);
    return 0;
}
