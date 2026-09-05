#ifndef CAMICIA_INT128_IO_HPP
#define CAMICIA_INT128_IO_HPP

#include <algorithm>
#include <string>

typedef __int128_t int128;

// Decimal string <-> __int128_t conversions. BOINC's own APIs have no native
// 128-bit support, so every index that crosses a file/DB/CLI boundary in this
// project is serialized by hand via these.

// CA-L3 fix: previously skipped any non-digit character silently instead of
// rejecting the string, so junk like "12x3" or "1e9" quietly parsed as a
// different, smaller number (123 / 19) instead of being caught by a caller's
// own validation. Now returns -1 (never a value a legal index can take --
// every real index is >= 0) for anything that isn't a plain, non-empty
// decimal string, including a leading sign. Also caps the digit count: a
// legal index has at most ~21 digits (MAX_INDEX is ~6.5e20), and letting an
// arbitrarily long digit string accumulate is undefined behavior once it
// overflows signed __int128 (possible past ~39 digits) -- 38 is a generous
// cap that rejects deliberately malformed input long before that point.
inline int128 stringTo128(const std::string& s) {
    if (s.empty() || s.size() > 38) return -1;
    int128 res = 0;
    for (char c : s) {
        if (c < '0' || c > '9') return -1;
        res = res * 10 + (c - '0');
    }
    return res;
}

inline std::string int128ToString(int128 n) {
    // CA-L3 fix: n < 0 used to fall through both branches below and
    // silently return "" (the `while (n > 0)` body never runs and the
    // `n == 0` fast path doesn't match). A caller round-tripping a
    // formerly-invalid stringTo128() result straight back through this
    // function for a diagnostic message -- e.g. worker.cpp's CA-M2 "invalid
    // index range" report, which prints exactly the malformed value it
    // rejected -- would then get an empty field instead of a readable one.
    // Every legitimate index is >= 0, so this is only ever reached with a
    // negative value in that kind of error-reporting context; print it with
    // a leading '-' rather than asserting, so the diagnostic stays useful
    // instead of the process crashing while trying to report the problem.
    bool neg = n < 0;
    unsigned __int128 un = neg ? (unsigned __int128)(-n) : (unsigned __int128)n;
    if (un == 0) return "0";
    std::string s = "";
    while (un > 0) {
        s += (char)((un % 10) + '0');
        un /= 10;
    }
    if (neg) s += '-';
    std::reverse(s.begin(), s.end());
    return s;
}

#endif
