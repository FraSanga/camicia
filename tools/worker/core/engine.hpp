#ifndef CAMICIA_ENGINE_HPP
#define CAMICIA_ENGINE_HPP

#include <cstdint>
#include <string>
#include <vector>
#include <memory>
#include <unordered_set>

enum class Card : uint8_t {
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
    uint8_t winner = 0; // 0 = loop/none, 1 = Player 1 (A), 2 = Player 2 (B)
};

// Fixed-capacity ring buffer for zero heap allocations.
// Maximum cards in play in standard Beggar-My-Neighbour is 52 (so 64 capacity is always sufficient).
struct CardQueue {
    Card data[64];
    uint8_t head = 0;
    uint8_t tail = 0;
    uint8_t count = 0;

    inline bool empty() const noexcept { return count == 0; }
    inline uint8_t size() const noexcept { return count; }
    inline void clear() noexcept { head = tail = count = 0; }

    inline void push_back(Card c) noexcept {
        data[tail] = c;
        tail = (tail + 1) & 63;
        count++;
    }

    inline Card pop_front() noexcept {
        Card c = data[head];
        head = (head + 1) & 63;
        count--;
        return c;
    }

    inline Card front() const noexcept {
        return data[head];
    }
};

struct GameStateFingerprint {
    uint64_t hi, lo;
    inline bool operator==(const GameStateFingerprint& other) const noexcept {
        return hi == other.hi && lo == other.lo;
    }
};

struct GameStateHash {
    inline size_t operator()(const GameStateFingerprint& s) const noexcept {
        return static_cast<size_t>(s.hi);
    }
};

// Reusable state tracker for cycle detection.
// Uses an epoch-indexed open-addressing flat hash table (O(1) clear, zero per-deal allocations)
// with fallback to an unordered_set for rare pathologically long games (>3000 tricks).
class StateTracker {
public:
    static constexpr size_t CAP = 4096;
    static constexpr size_t MAX_FLAT = 3072; // 75% load factor

    StateTracker();
    void clear() noexcept;
    bool insert(GameStateFingerprint s);

private:
    struct Entry {
        uint64_t hi = 0;
        uint64_t lo = 0;
        uint32_t epoch = 0;
    };
    std::unique_ptr<Entry[]> table;
    uint32_t currentEpoch = 1;
    size_t count = 0;
    std::unordered_set<GameStateFingerprint, GameStateHash> overflowSet;
};

class CamiciaGame {
public:
    using State = GameStateFingerprint;
    using StateHash = GameStateHash;

    CamiciaGame(const std::vector<std::string>& playerA, const std::vector<std::string>& playerB);
    CamiciaGame(const Card* playerA, size_t sizeA, const Card* playerB, size_t sizeB);

    GameResult simulate();
    GameResult simulate(StateTracker& tracker);

    // High-performance static simulation avoiding object instantiation
    static GameResult simulate(const Card* playerA, size_t sizeA, const Card* playerB, size_t sizeB, StateTracker& tracker);

    static Card stringToCard(const std::string& s) noexcept;
    static int getPenalty(Card card) noexcept;
    static State fingerprintState(int turn, const CardQueue& a, const CardQueue& b) noexcept;

private:
    CardQueue deckA;
    CardQueue deckB;
};

#endif
