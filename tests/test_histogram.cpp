#include <iostream>
#include <cassert>
#include <vector>
#include "../tools/worker/core/histogram.hpp"
#include "../tools/worker/core/engine.hpp"
#include "../tools/worker/core/permutation.hpp"
#include "../tools/worker/core/int128_io.hpp"

void test_bucket_boundaries() {
    std::cout << "Testing bucket boundaries..." << std::endl;

    // Phase 1: Width 10 (0..399 -> buckets 0..39)
    assert(get_bucket_index(0) == 0);
    assert(get_bucket_index(9) == 0);
    assert(get_bucket_index(10) == 1);
    assert(get_bucket_index(19) == 1);
    assert(get_bucket_index(390) == 39);
    assert(get_bucket_index(399) == 39);

    // Phase 2: Width 100 (400..1999 -> buckets 40..55)
    assert(get_bucket_index(400) == 40);
    assert(get_bucket_index(499) == 40);
    assert(get_bucket_index(500) == 41);
    assert(get_bucket_index(1900) == 55);
    assert(get_bucket_index(1999) == 55);

    // Phase 3: Width 500 (2000..5999 -> buckets 56..63)
    assert(get_bucket_index(2000) == 56);
    assert(get_bucket_index(2499) == 56);
    assert(get_bucket_index(2500) == 57);
    assert(get_bucket_index(5500) == 63);
    assert(get_bucket_index(5999) == 63);

    // Phase 4: Extreme tail (>= 6000 -> clamped to bucket 63)
    assert(get_bucket_index(6000) == 63);
    assert(get_bucket_index(8344) == 63);
    assert(get_bucket_index(100000) == 63);

    std::cout << "  Bucket boundary tests PASSED!" << std::endl;
}

void test_invariants_and_determinism() {
    std::cout << "Testing invariants and determinism over sample range..." << std::endl;

    // Range around the known Casella loop: [MID - 200, MID + 200] = 401 deals
    int128 mid = stringTo128("472460898658889399111");
    int128 start = mid - 200;
    int128 end = mid + 200;
    uint64_t totalDealsExpected = 401;

    SummaryStats stats1;
    uint64_t loops1 = 0;
    StateTracker tracker;
    Card deck[52];

    for (int128 idx = start; idx <= end; ++idx) {
        getNthPermutation(idx, deck);
        GameResult res = CamiciaGame::simulate(deck, 26, deck + 26, 26, tracker);
        if (res.status == "finished") {
            assert(res.winner == 1 || res.winner == 2);
            stats1.totalDeals++;
            stats1.totalCards += res.cards;
            stats1.totalTricks += res.tricks;
            stats1.totalCardsSq += (uint64_t)res.cards * res.cards;
            if (res.winner == 1) {
                stats1.p1Wins++;
            }
            stats1.buckets[get_bucket_index((uint32_t)res.cards)]++;
        } else if (res.status == "loop") {
            assert(res.winner == 0);
            loops1++;
        }
    }

    // Invariant 1: totalDeals + loops == total range
    assert(stats1.totalDeals + loops1 == totalDealsExpected);
    std::cout << "  Invariant 1 (totalDeals + loops == total range): " 
              << stats1.totalDeals << " + " << loops1 << " == " << totalDealsExpected << " PASSED!" << std::endl;

    // Invariant 2: sum(buckets) == totalDeals
    uint64_t bucketSum = 0;
    for (int b = 0; b < 64; ++b) {
        bucketSum += stats1.buckets[b];
    }
    assert(bucketSum == stats1.totalDeals);
    std::cout << "  Invariant 2 (sum(buckets) == totalDeals): " 
              << bucketSum << " == " << stats1.totalDeals << " PASSED!" << std::endl;

    // Invariant 3: p1Wins <= totalDeals
    assert(stats1.p1Wins <= stats1.totalDeals);
    std::cout << "  Invariant 3 (p1Wins <= totalDeals): " 
              << stats1.p1Wins << " <= " << stats1.totalDeals << " PASSED!" << std::endl;

    // Invariant 4: totalCardsSq >= totalCards (since cards >= 1 for finished deals)
    assert(stats1.totalCardsSq >= stats1.totalCards);
    std::cout << "  Invariant 4 (totalCardsSq >= totalCards): PASSED!" << std::endl;

    // Test determinism: re-run same range, check exact bitwise equality
    SummaryStats stats2;
    uint64_t loops2 = 0;
    for (int128 idx = start; idx <= end; ++idx) {
        getNthPermutation(idx, deck);
        GameResult res = CamiciaGame::simulate(deck, 26, deck + 26, 26, tracker);
        if (res.status == "finished") {
            stats2.totalDeals++;
            stats2.totalCards += res.cards;
            stats2.totalTricks += res.tricks;
            stats2.totalCardsSq += (uint64_t)res.cards * res.cards;
            if (res.winner == 1) {
                stats2.p1Wins++;
            }
            stats2.buckets[get_bucket_index((uint32_t)res.cards)]++;
        } else if (res.status == "loop") {
            loops2++;
        }
    }

    assert(loops1 == loops2);
    assert(stats1.totalDeals == stats2.totalDeals);
    assert(stats1.totalCards == stats2.totalCards);
    assert(stats1.totalTricks == stats2.totalTricks);
    assert(stats1.totalCardsSq == stats2.totalCardsSq);
    assert(stats1.p1Wins == stats2.p1Wins);
    for (int b = 0; b < 64; ++b) {
        assert(stats1.buckets[b] == stats2.buckets[b]);
    }
    std::cout << "  Determinism test PASSED!" << std::endl;
}

int main() {
    std::cout << "=== Running test_histogram ===" << std::endl;
    test_bucket_boundaries();
    test_invariants_and_determinism();
    std::cout << "=== All test_histogram checks PASSED! ===" << std::endl;
    return 0;
}
