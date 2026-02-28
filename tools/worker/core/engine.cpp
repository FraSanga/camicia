#include "engine.hpp"
#include <algorithm>

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

GameResult CamiciaGame::simulate() {
    std::set<State> seenStates;
    long long totalCardsPlayed = 0;
    long long totalTricks = 0;
    
    int turn = 0;
    int penaltyRemaining = 0;
    int lastPaymentPlayer = -1;

    while (true) {
        if (penaltyRemaining == 0 && pile.empty()) {
            State currentState;
            currentState.turn = turn;
            // The rule "not counting number cards" is interpreted here as:
            // The position of number cards matters, but their specific value doesn't.
            // So we store the full sequence but with all 2-10 mapped to NUMBER.
            for (Card c : deckA) currentState.a.push_back(c);
            for (Card c : deckB) currentState.b.push_back(c);
            
            if (seenStates.count(currentState)) {
                return {"loop", totalCardsPlayed, totalTricks};
            }
            seenStates.insert(currentState);
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
