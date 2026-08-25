#ifndef CAMICIA_ENGINE_HPP
#define CAMICIA_ENGINE_HPP

#include <cstdint>
#include <string>
#include <vector>
#include <deque>
#include <unordered_set>

enum class Card {
    ACE = 1,
    KING = 13,
    QUEEN = 12,
    JACK = 11,
    NUMBER = 0
};

struct GameResult {
    std::string status; // "finished" or "loop"
    long long cards;
    long long tricks;
};

class CamiciaGame {
public:
    CamiciaGame(const std::vector<std::string>& playerA, const std::vector<std::string>& playerB);
    GameResult simulate();

private:
    std::deque<Card> deckA;
    std::deque<Card> deckB;
    std::deque<Card> pile;

    Card stringToCard(const std::string& s);
    int getPenalty(Card card);

    // A game state used to be stored as-is (two std::vector<Card>, up to 26
    // elements each, plus turn) in a std::set -- ~300+ bytes and 3 heap
    // allocations per entry, with an O(n) comparison on every tree
    // operation. This instead stores two independent 64-bit FNV-1a
    // fingerprints of the same state (turn + full deckA + full deckB) --
    // a fixed 16 bytes, zero heap allocations, O(1) equality/hash. Exact
    // bit-packing without any collision risk would need a 256-bit integer
    // (up to 26 cards x 2 hands x 3 bits, plus lengths, is 167 bits), so
    // this trades an astronomically small chance of a false "already seen"
    // (~n^2/2^129 for n states recorded in one deal) for a large,
    // consistent win in memory and speed. See fingerprintState() in
    // engine.cpp.
    struct State {
        uint64_t hi, lo;
        bool operator==(const State& other) const {
            return hi == other.hi && lo == other.lo;
        }
    };
    struct StateHash {
        size_t operator()(const State& s) const noexcept {
            return static_cast<size_t>(s.hi);
        }
    };
    static State fingerprintState(int turn, const std::deque<Card>& a, const std::deque<Card>& b);
};

#endif
