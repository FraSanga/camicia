// Exhaustive small-deck stress test for CamiciaGame::simulate(), a
// companion to test_engine_properties.cpp. Plays out EVERY distinct
// arrangement of a tiny deck, not just a random sample -- exercises edge
// cases (an immediately empty hand, a penalty chain that outlasts a hand,
// a tie at the very first trick) that random 52-card sampling is unlikely
// to hit early, without needing 52! coverage. This checks robustness (no
// crash, a sane result), not rule correctness beyond that -- there is no
// independent oracle here, see test_engine_properties.cpp's header.
//
// Deliberately compiled WITHOUT -DCAMICIA_TESTING: engine.cpp's
// card-conservation assert is hardcoded to exactly 52 cards
// (deckA.size() + deckB.size() + pile.size() == 52) and would fire
// immediately on any smaller deck regardless of correctness.
#include <algorithm>
#include <cstdio>
#include <string>
#include <vector>
#include "engine.hpp"

static int passed = 0;
static int failed = 0;

static void check(bool ok, const std::string& what) {
    if (ok) {
        passed++;
    } else {
        printf("  [FAIL] %s\n", what.c_str());
        failed++;
    }
}

static std::vector<std::string> multiset(int aces, int kings, int queens, int jacks, int numbers) {
    std::vector<std::string> deck;
    for (int i = 0; i < aces; i++) deck.push_back("A");
    for (int i = 0; i < kings; i++) deck.push_back("K");
    for (int i = 0; i < queens; i++) deck.push_back("Q");
    for (int i = 0; i < jacks; i++) deck.push_back("J");
    for (int i = 0; i < numbers; i++) deck.push_back("2");
    return deck;
}

int main() {
    printf("Testing: exhaustive small-deck stress test\n");
    // 2 aces + 2 numbers, 2-2 split: small enough to enumerate every
    // distinct permutation in milliseconds, large enough to still trigger
    // a penalty-payment chain.
    std::vector<std::string> deck = multiset(2, 0, 0, 0, 2);
    std::sort(deck.begin(), deck.end());
    int count = 0;
    do {
        std::vector<std::string> a(deck.begin(), deck.begin() + 2);
        std::vector<std::string> b(deck.begin() + 2, deck.end());

        CamiciaGame game(a, b);
        GameResult res = game.simulate();

        check(res.status == "finished" || res.status == "loop",
              "status was neither finished nor loop");
        check(res.cards >= 0 && res.tricks >= 0, "negative counters");
        count++;
    } while (std::next_permutation(deck.begin(), deck.end()));
    printf("  (%d distinct small-deck arrangements exercised)\n", count);

    printf("\nResults: %d PASSED, %d FAILED\n", passed, failed);
    return failed > 0 ? 1 : 0;
}
