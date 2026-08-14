#ifndef CAMICIA_INT128_IO_HPP
#define CAMICIA_INT128_IO_HPP

#include <algorithm>
#include <string>

typedef __int128_t int128;

// Decimal string <-> __int128_t conversions. BOINC's own APIs have no native
// 128-bit support, so every index that crosses a file/DB/CLI boundary in this
// project is serialized by hand via these.

inline int128 stringTo128(const std::string& s) {
    int128 res = 0;
    for (char c : s) {
        if (c >= '0' && c <= '9') {
            res = res * 10 + (c - '0');
        }
    }
    return res;
}

inline std::string int128ToString(int128 n) {
    if (n == 0) return "0";
    std::string s = "";
    while (n > 0) {
        s += (char)((n % 10) + '0');
        n /= 10;
    }
    std::reverse(s.begin(), s.end());
    return s;
}

#endif
