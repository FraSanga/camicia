#include "engine.hpp"
#include <algorithm>
#ifdef CAMICIA_TESTING
#include <cassert>
#endif

CamiciaGame::CamiciaGame(const std::vector<std::string>& playerA, const std::vector<std::string>& playerB) {
    for (const auto& s : playerA) deckA.push_back(stringToCard(s));
    for (const auto& s : playerB) deckB.push_back(stringToCard(s));
}

Card CamiciaGame::stringToCard(const std::string& s) {
    if (s == "A") return Card::ACE;
    if (s == "K") return Card::KING;
    if (s == "Q") return Card::QUEEN;
    if (s == "J") return Card::JACK;
    return Card::NUMBER;
}

int CamiciaGame::getPenalty(Card card) {
    switch (card) {
        case Card::ACE: return 4;
        case Card::KING: return 3;
        case Card::QUEEN: return 2;
        case Card::JACK: return 1;
        default: return 0;
    }
}

// FNV-1a 64-bit, computed twice with different seeds over the same byte
// sequence (turn, then every card of deckA, a separator, then every card
// of deckB) to produce two independent 64-bit words -- see the State
// comment in engine.hpp for why a hash instead of exact bit-packing.
CamiciaGame::State CamiciaGame::fingerprintState(
    int turn, const std::deque<Card>& a, const std::deque<Card>& b
) {
    static constexpr uint64_t FNV_PRIME = 1099511628211ULL;
    static constexpr uint64_t SEED_HI = 0xcbf29ce484222325ULL; // FNV-1a 64-bit offset basis
    static constexpr uint64_t SEED_LO = 0x9E3779B97F4A7C15ULL; // distinct odd constant (2^64/phi)

    uint64_t hi = SEED_HI, lo = SEED_LO;
    auto mix = [&](uint64_t byte) {
        hi = (hi ^ byte) * FNV_PRIME;
        lo = (lo ^ byte) * FNV_PRIME;
    };
    mix(static_cast<uint64_t>(turn));
    for (Card c : a) mix(static_cast<uint64_t>(c));
    mix(0xFFu); // separator: guarantees distinct (a, b) splits can't collide by shifting cards across it
    for (Card c : b) mix(static_cast<uint64_t>(c));
    return {hi, lo};
}

GameResult CamiciaGame::simulate() {
    std::unordered_set<State, StateHash> seenStates;
    long long totalCardsPlayed = 0;
    long long totalTricks = 0;
    
    int turn = 0;
    int penaltyRemaining = 0;
    int lastPaymentPlayer = -1;

    while (true) {
#ifdef CAMICIA_TESTING
        // Only compiled into test builds (-DCAMICIA_TESTING) -- zero cost in
        // the production worker binary. Every card must be in exactly one of
        // the two decks or the pile at all times.
        assert(deckA.size() + deckB.size() + pile.size() == 52);
#endif
        if (penaltyRemaining == 0 && pile.empty()) {
            // The rule "not counting number cards" is interpreted here as:
            // The position of number cards matters, but their specific value doesn't.
            // deckA/deckB already store all 2-10 as the single NUMBER value,
            // so the fingerprint below reflects that automatically.
            State currentState = fingerprintState(turn, deckA, deckB);

            if (!seenStates.insert(currentState).second) {
                return {"loop", totalCardsPlayed, totalTricks};
            }
        }

        std::deque<Card>& activeDeck = (turn == 0) ? deckA : deckB;
        std::deque<Card>& opponentDeck = (turn == 0) ? deckB : deckA;

        if (activeDeck.empty()) {
            if (pile.empty()) return {"finished", totalCardsPlayed, totalTricks};
            for (Card c : pile) opponentDeck.push_back(c);
            pile.clear();
            totalTricks++;
            if (opponentDeck.size() == 52) return {"finished", totalCardsPlayed, totalTricks};
            turn = 1 - turn;
            penaltyRemaining = 0;
            lastPaymentPlayer = -1;
            continue;
        }

        Card playedCard = activeDeck.front();
        activeDeck.pop_front();
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
                    std::deque<Card>& winnerDeck = (lastPaymentPlayer == 0) ? deckA : deckB;
                    for (Card c : pile) winnerDeck.push_back(c);
                    pile.clear();
                    totalTricks++;
                    if (winnerDeck.size() == 52) return {"finished", totalCardsPlayed, totalTricks};
                    turn = lastPaymentPlayer;
                    lastPaymentPlayer = -1;
                }
            } else {
                turn = 1 - turn;
            }
        }
    }
}
