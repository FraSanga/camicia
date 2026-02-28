#include <iostream>
#include <string>
#include <vector>
#include <algorithm>
#include "../tools/worker/core/permutation.hpp"
#include "test_data_gen.hpp"

using namespace std;

typedef __int128_t int128;

static int128 stringTo128(string s) {
    int128 res = 0;
    for (char c : s) {
        if (c >= '0' && c <= '9') {
            res = res * 10 + (c - '0');
        }
    }
    return res;
}

static string int128ToString(int128 n) {
    if (n == 0) return "0";
    string s = "";
    while (n > 0) {
        s += (char)((n % 10) + '0');
        n /= 10;
    }
    reverse(s.begin(), s.end());
    return s;
}

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
