#ifndef CAMICIA_HISTOGRAM_HPP
#define CAMICIA_HISTOGRAM_HPP

#include <cstdint>

// 64 buckets total:
// Buckets 0-39: 0-399 cards (width 10: [0..9], [10..19], ..., [390..399])
// Buckets 40-55: 400-1999 cards (width 100: [400..499], ..., [1900..1999])
// Buckets 56-63: 2000-5999 cards (width 500: [2000..2499], ..., [5500..5999])
// (Any game >= 6000 is clamped into bucket 63)

inline uint8_t get_bucket_index(uint32_t cards) {
    if (cards < 400) {
        return static_cast<uint8_t>(cards / 10);
    } else if (cards < 2000) {
        return static_cast<uint8_t>(40 + (cards - 400) / 100);
    } else if (cards < 6000) {
        return static_cast<uint8_t>(56 + (cards - 2000) / 500);
    } else {
        return 63;
    }
}

struct SummaryStats {
    uint64_t totalDeals = 0;
    uint64_t totalCards = 0;
    uint64_t totalTricks = 0;
    uint64_t totalCardsSq = 0;
    uint64_t p1Wins = 0;
    uint64_t buckets[64] = {0};
};

#endif // CAMICIA_HISTOGRAM_HPP
