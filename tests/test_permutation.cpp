#include <iostream>
#include <string>
#include <vector>
#include <algorithm>
#include "../tools/worker/core/permutation.hpp"
#include "test_data_gen.hpp"

using namespace std;

static string deckToString(const vector<string>& deck) {
    string res = "";
    for (const auto& s : deck) {
        if (s == "2") res += "2";
        else res += s;
    }
    return res;
}

int main() {
    int passed = 0;
    int failed = 0;

    for (const auto& tc : test_cases) {
        cout << "Testing: " << tc.description << endl;

        int128 expected_index = stringTo128(tc.index_str);
        
        vector<string> deck = getNthPermutation(expected_index);
        string deck_str = deckToString(deck);
        
        if (deck_str != tc.deck_str) {
            cout << "  [FAIL] Index -> Deck mismatch!" << endl;
            cout << "    Expected: " << tc.deck_str << endl;
            cout << "    Actual:   " << deck_str << endl;
            failed++;
            continue;
        }

        cout << "  [PASS]" << endl;
        passed++;
    }

    cout << "\nResults: " << passed << " PASSED, " << failed << " FAILED" << endl;
    return failed > 0 ? 1 : 0;
}
