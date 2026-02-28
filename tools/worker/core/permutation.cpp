#include "permutation.hpp"
#include <map>

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

static int128 fast_multinomial(int counts[5]) {
    int n = 0;
    for (int i = 0; i < 5; ++i) n += counts[i];
    int128 res = 1;
    int current_n = n;
    for (int i = 0; i < 5; ++i) {
        if (counts[i] > 0) {
            res *= nCr_table[current_n][counts[i]];
            current_n -= counts[i];
        }
    }
    return res;
}

std::vector<std::string> getNthPermutation(int128 n) {
    init_table();
    int counts[5] = {4, 4, 4, 4, 36}; // A, K, Q, J, -
    const char symbols[] = {'A', 'K', 'Q', 'J', '-'};
    std::vector<std::string> result;
    result.reserve(52);
    
    for (int i = 0; i < 52; ++i) {
        for (int s = 0; s < 5; ++s) {
            if (counts[s] == 0) continue;
            
            counts[s]--;
            int128 num = fast_multinomial(counts);
            
            if (n < num) {
                if (symbols[s] == '-') result.push_back("2");
                else result.push_back(std::string(1, symbols[s]));
                break;
            } else {
                n -= num;
                counts[s]++;
            }
        }
    }
    return result;
}
