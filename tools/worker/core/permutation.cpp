#include "permutation.hpp"
#include <cassert>

typedef __int128_t int128;

static int128 nCr_table[53][53];
static bool table_initialized = false;

static void init_table() {
    if (table_initialized) return;
    for (int n = 0; n <= 52; ++n) {
        nCr_table[n][0] = 1;
        for (int r = 1; r <= n; ++r) {
            nCr_table[n][r] = nCr_table[n - 1][r - 1] + nCr_table[n - 1][r];
        }
    }
    table_initialized = true;
}

static inline int128 fast_multinomial(const int counts[5]) {
    int current_n = counts[0] + counts[1] + counts[2] + counts[3] + counts[4];
    int128 res = 1;
    // The 5th count (counts[4]) has nCr(c4, c4) == 1, so we only need to multiply first 4 terms
    for (int i = 0; i < 4; ++i) {
        if (counts[i] > 0) {
            res *= nCr_table[current_n][counts[i]];
            current_n -= counts[i];
        }
    }
    return res;
}

void getNthPermutation(int128 n, Card* out) {
    init_table();
    int counts[5] = {4, 4, 4, 4, 36}; // A, K, Q, J, -
    const Card symbols[] = {Card::ACE, Card::KING, Card::QUEEN, Card::JACK, Card::NUMBER};

    {
        int total_counts[5] = {4, 4, 4, 4, 36};
        int128 total_permutations = fast_multinomial(total_counts);
        assert(n >= 0 && n < total_permutations &&
               "getNthPermutation: index out of range of the 52-card permutation space");
    }

    for (int i = 0; i < 52; ++i) {
        // Fast-path: once all 16 face cards are placed, the rest are guaranteed to be NUMBER cards
        if (counts[0] == 0 && counts[1] == 0 && counts[2] == 0 && counts[3] == 0) {
            for (int j = i; j < 52; ++j) out[j] = Card::NUMBER;
            return;
        }

        for (int s = 0; s < 5; ++s) {
            if (counts[s] == 0) continue;

            counts[s]--;
            int128 num = fast_multinomial(counts);

            if (n < num) {
                out[i] = symbols[s];
                break;
            } else {
                n -= num;
                counts[s]++;
            }
        }
    }
}

void getNthPermutation(int128 n, std::array<Card, 52>& out) {
    getNthPermutation(n, out.data());
}

std::vector<std::string> getNthPermutation(int128 n) {
    Card cards[52];
    getNthPermutation(n, cards);
    std::vector<std::string> result;
    result.reserve(52);
    for (int i = 0; i < 52; ++i) {
        switch (cards[i]) {
            case Card::ACE: result.push_back("A"); break;
            case Card::KING: result.push_back("K"); break;
            case Card::QUEEN: result.push_back("Q"); break;
            case Card::JACK: result.push_back("J"); break;
            default: result.push_back("2"); break;
        }
    }
    return result;
}
