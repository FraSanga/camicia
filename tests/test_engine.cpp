#include <iostream>
#include <string>
#include <vector>
#include "../tools/worker/core/permutation.hpp"
#include "../tools/worker/core/engine.hpp"
#include "test_data_gen.hpp"

using namespace std;

int main() {
    int passed = 0;
    int failed = 0;

    for (const auto& tc : test_cases) {
        cout << "Testing: " << tc.description << endl;

        int128 index = stringTo128(tc.index_str);
        vector<string> deck = getNthPermutation(index);
        vector<string> a(deck.begin(), deck.begin() + 26);
        vector<string> b(deck.begin() + 26, deck.end());

        // If card conservation is ever violated, this aborts via the
        // CAMICIA_TESTING-gated assert in engine.cpp rather than silently
        // returning a wrong result.
        CamiciaGame game(a, b);
        GameResult res = game.simulate();

        bool ok = true;
        if (res.status != tc.expected_status) {
            cout << "  [FAIL] status mismatch! expected=" << tc.expected_status
                 << " actual=" << res.status << endl;
            ok = false;
        }
        if (res.cards != tc.expected_cards) {
            cout << "  [FAIL] cards mismatch! expected=" << tc.expected_cards
                 << " actual=" << res.cards << endl;
            ok = false;
        }
        if (res.tricks != tc.expected_tricks) {
            cout << "  [FAIL] tricks mismatch! expected=" << tc.expected_tricks
                 << " actual=" << res.tricks << endl;
            ok = false;
        }

        if (ok) {
            cout << "  [PASS]" << endl;
            passed++;
        } else {
            failed++;
        }
    }

    cout << "\nResults: " << passed << " PASSED, " << failed << " FAILED" << endl;
    return failed > 0 ? 1 : 0;
}
