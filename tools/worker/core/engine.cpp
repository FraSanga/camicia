#include "engine.hpp"
#include <algorithm>
#include <cstring>
#ifdef CAMICIA_TESTING
#include <cassert>
#endif

StateTracker::StateTracker()
    : table(std::make_unique<Entry[]>(CAP)), currentEpoch(1), count(0) {}

void StateTracker::clear() noexcept {
    count = 0;
    if (!overflowSet.empty()) {
        overflowSet.clear();
    }
    currentEpoch++;
    if (currentEpoch == 0) {
        std::memset(table.get(), 0, sizeof(Entry) * CAP);
        currentEpoch = 1;
    }
}

bool StateTracker::insert(GameStateFingerprint s) {
    size_t idx = (s.hi ^ (s.lo >> 32)) & (CAP - 1);
    while (table[idx].epoch == currentEpoch) {
        if (table[idx].hi == s.hi && table[idx].lo == s.lo) {
            return false;
        }
        idx = (idx + 1) & (CAP - 1);
    }

    if (count >= MAX_FLAT) {
        return overflowSet.insert(s).second;
    }

    table[idx].hi = s.hi;
    table[idx].lo = s.lo;
    table[idx].epoch = currentEpoch;
    count++;
    return true;
}

CamiciaGame::CamiciaGame(const std::vector<std::string>& playerA, const std::vector<std::string>& playerB) {
    for (const auto& s : playerA) deckA.push_back(stringToCard(s));
    for (const auto& s : playerB) deckB.push_back(stringToCard(s));
}

CamiciaGame::CamiciaGame(const Card* playerA, size_t sizeA, const Card* playerB, size_t sizeB) {
    for (size_t i = 0; i < sizeA; ++i) deckA.push_back(playerA[i]);
    for (size_t i = 0; i < sizeB; ++i) deckB.push_back(playerB[i]);
}

Card CamiciaGame::stringToCard(const std::string& s) noexcept {
    if (s == "A") return Card::ACE;
    if (s == "K") return Card::KING;
    if (s == "Q") return Card::QUEEN;
    if (s == "J") return Card::JACK;
    return Card::NUMBER;
}

int CamiciaGame::getPenalty(Card card) noexcept {
    switch (card) {
        case Card::ACE: return 4;
        case Card::KING: return 3;
        case Card::QUEEN: return 2;
        case Card::JACK: return 1;
        default: return 0;
    }
}

CamiciaGame::State CamiciaGame::fingerprintState(
    int turn, const CardQueue& a, const CardQueue& b
) noexcept {
    static constexpr uint64_t FNV_PRIME = 1099511628211ULL;
    static constexpr uint64_t SEED_HI = 0xcbf29ce484222325ULL; // FNV-1a 64-bit offset basis
    static constexpr uint64_t SEED_LO = 0x9E3779B97F4A7C15ULL; // distinct odd constant (2^64/phi)

    uint64_t hi = SEED_HI, lo = SEED_LO;
    auto mix = [&](uint64_t byte) {
        hi = (hi ^ byte) * FNV_PRIME;
        lo = (lo ^ byte) * FNV_PRIME;
    };
    mix(static_cast<uint64_t>(turn));
    for (uint8_t i = 0, idx = a.head; i < a.count; ++i, idx = (idx + 1) & 63) {
        mix(static_cast<uint64_t>(a.data[idx]));
    }
    mix(0xFFu); // separator: guarantees distinct (a, b) splits can't collide by shifting cards across it
    for (uint8_t i = 0, idx = b.head; i < b.count; ++i, idx = (idx + 1) & 63) {
        mix(static_cast<uint64_t>(b.data[idx]));
    }
    return {hi, lo};
}

GameResult CamiciaGame::simulate() {
    StateTracker tracker;
    return simulate(tracker);
}

GameResult CamiciaGame::simulate(StateTracker& tracker) {
    Card a[64], b[64];
    size_t sizeA = deckA.size();
    size_t sizeB = deckB.size();
    for (size_t i = 0; i < sizeA; ++i) a[i] = deckA.data[(deckA.head + i) & 63];
    for (size_t i = 0; i < sizeB; ++i) b[i] = deckB.data[(deckB.head + i) & 63];
    return simulate(a, sizeA, b, sizeB, tracker);
}

GameResult CamiciaGame::simulate(const Card* playerA, size_t sizeA, const Card* playerB, size_t sizeB, StateTracker& seenStates) {
    CardQueue deckA, deckB, pile;
    for (size_t i = 0; i < sizeA; ++i) deckA.push_back(playerA[i]);
    for (size_t i = 0; i < sizeB; ++i) deckB.push_back(playerB[i]);

    seenStates.clear();
    long long totalCardsPlayed = 0;
    long long totalTricks = 0;
    
    int turn = 0;
    int penaltyRemaining = 0;
    int lastPaymentPlayer = -1;

    while (true) {
#ifdef CAMICIA_TESTING
        assert(deckA.size() + deckB.size() + pile.size() == 52);
#endif
        if (penaltyRemaining == 0 && pile.empty()) {
            State currentState = fingerprintState(turn, deckA, deckB);
            if (!seenStates.insert(currentState)) {
                return {"loop", totalCardsPlayed, totalTricks, 0};
            }
        }

        CardQueue& activeDeck = (turn == 0) ? deckA : deckB;
        CardQueue& opponentDeck = (turn == 0) ? deckB : deckA;

        if (activeDeck.empty()) {
            uint8_t winner = (turn == 0) ? 2 : 1;
            if (pile.empty()) return {"finished", totalCardsPlayed, totalTricks, winner};
            while (!pile.empty()) opponentDeck.push_back(pile.pop_front());
            totalTricks++;
            if (opponentDeck.size() == 52) return {"finished", totalCardsPlayed, totalTricks, winner};
            turn = 1 - turn;
            penaltyRemaining = 0;
            lastPaymentPlayer = -1;
            continue;
        }

        Card playedCard = activeDeck.pop_front();
        pile.push_back(playedCard);
        totalCardsPlayed++;

        int penalty = getPenalty(playedCard);
        if (penalty > 0) {
            penaltyRemaining = penalty;
            lastPaymentPlayer = turn;
            turn = 1 - turn;
        } else {
            if (penaltyRemaining > 0) {
                penaltyRemaining--;
                if (penaltyRemaining == 0) {
                    CardQueue& winnerDeck = (lastPaymentPlayer == 0) ? deckA : deckB;
                    while (!pile.empty()) winnerDeck.push_back(pile.pop_front());
                    totalTricks++;
                    if (winnerDeck.size() == 52) {
                        uint8_t winner = (lastPaymentPlayer == 0) ? 1 : 2;
                        return {"finished", totalCardsPlayed, totalTricks, winner};
                    }
                    turn = lastPaymentPlayer;
                    lastPaymentPlayer = -1;
                }
            } else {
                turn = 1 - turn;
            }
        }
    }
}
