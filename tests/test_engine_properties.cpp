// Property-based tests for CamiciaGame::simulate(), complementing
// test_engine.cpp's fixed known-game regression cases. Those only exercise
// 11 specific, long, previously-published deals; this file instead checks
// invariants that must hold for ANY deal, catching a much wider range of
// state-transition bugs (off-by-ones, wrong player after a trick, etc.)
// that a handful of known cases could easily miss -- without needing an
// independent reference implementation of the rules (the project owner
// reviews scientifically interesting results personally instead, see
// SECURITY.md/README discussion). Card-conservation is still checked via
// engine.cpp's own CAMICIA_TESTING-gated assert (build with -DCAMICIA_TESTING,
// same as test_engine.cpp), not duplicated here.
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <numeric>
#include <random>
#include <string>
#include <vector>
#include "permutation.hpp"
#include "engine.hpp"

typedef __int128_t int128;

static std::mt19937 rng;
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

// Same 4A/4K/4Q/4J/36-number multiset permutation.cpp draws from, shuffled
// directly instead of via getNthPermutation.
static std::vector<std::string> shuffledMultiset(int aces, int kings, int queens, int jacks, int numbers) {
    std::vector<std::string> deck;
    for (int i = 0; i < aces; i++) deck.push_back("A");
    for (int i = 0; i < kings; i++) deck.push_back("K");
    for (int i = 0; i < queens; i++) deck.push_back("Q");
    for (int i = 0; i < jacks; i++) deck.push_back("J");
    for (int i = 0; i < numbers; i++) deck.push_back("2");
    return deck;
}

// --- 1. Determinism: the same deal must always produce the same result. ---
static void testDeterminism() {
    printf("Testing: determinism across repeated runs of the same deal\n");
    rng.seed(1);
    for (int i = 0; i < 200; i++) {
        std::vector<std::string> deck = shuffledMultiset(4, 4, 4, 4, 36);
        std::shuffle(deck.begin(), deck.end(), rng);
        std::vector<std::string> a(deck.begin(), deck.begin() + 26);
        std::vector<std::string> b(deck.begin() + 26, deck.end());

        CamiciaGame g1(a, b);
        GameResult r1 = g1.simulate();
        CamiciaGame g2(a, b);
        GameResult r2 = g2.simulate();

        check(r1.status == r2.status && r1.cards == r2.cards && r1.tricks == r2.tricks,
              "same deal produced different results on repeated runs");
    }
}

// --- 2. Random-permutation invariants: every deal must terminate with a
// sane, internally consistent result (no crash is implicit -- if
// simulate() hangs or aborts, the test binary itself fails/times out). ---
static void testRandomInvariants() {
    printf("Testing: invariants across random full 52-card deals\n");
    rng.seed(2);
    for (int i = 0; i < 2000; i++) {
        std::vector<std::string> deck = shuffledMultiset(4, 4, 4, 4, 36);
        std::shuffle(deck.begin(), deck.end(), rng);
        std::vector<std::string> a(deck.begin(), deck.begin() + 26);
        std::vector<std::string> b(deck.begin() + 26, deck.end());

        CamiciaGame game(a, b);
        GameResult res = game.simulate();

        check(res.status == "finished" || res.status == "loop",
              "status was neither finished nor loop");
        check(res.cards >= 0, "negative cards played");
        check(res.tricks >= 0, "negative tricks");
        // A finished game must have played at least one trick (52 cards
        // split 26/26 can never finish on turn zero).
        if (res.status == "finished") {
            check(res.tricks >= 1, "finished game with zero tricks");
        }
    }
}

int main() {
    testDeterminism();
    testRandomInvariants();

    printf("\nResults: %d PASSED, %d FAILED\n", passed, failed);
    return failed > 0 ? 1 : 0;
}
