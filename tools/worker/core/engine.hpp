#ifndef CAMICIA_ENGINE_HPP
#define CAMICIA_ENGINE_HPP

#include <string>
#include <vector>
#include <deque>
#include <set>

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
    
    struct State {
        std::vector<Card> a, b;
        int turn;
        bool operator<(const State& other) const {
            if (turn != other.turn) return turn < other.turn;
            if (a != other.a) return a < other.a;
            return b < other.b;
        }
    };
};

#endif
