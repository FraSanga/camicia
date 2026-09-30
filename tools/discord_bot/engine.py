import math
import random
from collections import deque
from typing import Dict, List, Optional, Tuple

TOTAL_PERMUTATIONS = 653534134886878245000  # (52!) / ((4!)^4 * 36!)

# Multinomial helper: n! / (c0! * c1! * c2! * c3! * c4!)
def _multinomial(counts: List[int]) -> int:
    current_n = sum(counts)
    res = 1
    # Only need first 4 terms because math.comb(counts[4], counts[4]) == 1
    for i in range(4):
        if counts[i] > 0:
            res *= math.comb(current_n, counts[i])
            current_n -= counts[i]
    return res


def get_nth_permutation(n: int) -> str:
    """
    Converts a 128-bit index n (0 <= n < TOTAL_PERMUTATIONS) into a 52-card deal
    using combinatorial unranking. Identical to tools/worker/core/permutation.cpp.
    Symbols: 'A', 'K', 'Q', 'J', '-' (representing 2-10).
    """
    if n < 0 or n >= TOTAL_PERMUTATIONS:
        raise ValueError(f"Index {n} out of range [0, {TOTAL_PERMUTATIONS})")

    counts = [4, 4, 4, 4, 36]  # A, K, Q, J, -
    symbols = ['A', 'K', 'Q', 'J', '-']
    deck = []

    for _ in range(52):
        # Fast-path: once all 16 face cards are placed, the rest are '-'
        if counts[0] == 0 and counts[1] == 0 and counts[2] == 0 and counts[3] == 0:
            deck.extend(['-'] * (52 - len(deck)))
            break

        for s in range(5):
            if counts[s] == 0:
                continue

            counts[s] -= 1
            num = _multinomial(counts)

            if n < num:
                deck.append(symbols[s])
                break
            else:
                n -= num
                counts[s] += 1

    return "".join(deck)


def get_random_deal_index() -> int:
    """Generates a cryptographically uniform random deal index across the entire Camicia search space."""
    return random.SystemRandom().randint(0, TOTAL_PERMUTATIONS - 1)


# Penalty value mapping
PENALTY = {'A': 4, 'K': 3, 'Q': 2, 'J': 1, '-': 0}


def simulate(deck_str: str) -> Dict[str, any]:
    """
    Simulates a Beggar-My-Neighbour game turn-by-turn with cycle detection.
    Matches tools/worker/core/engine.cpp bit-for-bit.
    Returns:
        {
            "status": "finished" | "loop",
            "cards": int,
            "tricks": int,
            "winner": 1 | 2 | 0,
        }
    """
    if len(deck_str) != 52:
        raise ValueError(f"Deck must have exactly 52 cards, got {len(deck_str)}")

    deck_a = deque(deck_str[:26])
    deck_b = deque(deck_str[26:])
    pile = deque()

    seen_states = set()
    total_cards = 0
    total_tricks = 0

    turn = 0  # 0: Player 1 (A), 1: Player 2 (B)
    penalty_remaining = 0
    last_payment_player = -1

    while True:
        # Check cycle detection only at trick boundaries (penaltyRemaining == 0 and pile empty)
        if penalty_remaining == 0 and len(pile) == 0:
            state = (turn, tuple(deck_a), tuple(deck_b))
            if state in seen_states:
                return {
                    "status": "loop",
                    "cards": total_cards,
                    "tricks": total_tricks,
                    "winner": 0,
                }
            seen_states.add(state)

        active_deck = deck_a if turn == 0 else deck_b
        opponent_deck = deck_b if turn == 0 else deck_a

        if len(active_deck) == 0:
            winner = 2 if turn == 0 else 1
            if len(pile) == 0:
                return {
                    "status": "finished",
                    "cards": total_cards,
                    "tricks": total_tricks,
                    "winner": winner,
                }
            while len(pile) > 0:
                opponent_deck.append(pile.popleft())
            total_tricks += 1
            if len(opponent_deck) == 52:
                return {
                    "status": "finished",
                    "cards": total_cards,
                    "tricks": total_tricks,
                    "winner": winner,
                }
            turn = 1 - turn
            penalty_remaining = 0
            last_payment_player = -1
            continue

        played_card = active_deck.popleft()
        pile.append(played_card)
        total_cards += 1

        penalty = PENALTY.get(played_card, 0)
        if penalty > 0:
            penalty_remaining = penalty
            last_payment_player = turn
            turn = 1 - turn
        else:
            if penalty_remaining > 0:
                penalty_remaining -= 1
                if penalty_remaining == 0:
                    winner_deck = deck_a if last_payment_player == 0 else deck_b
                    while len(pile) > 0:
                        winner_deck.append(pile.popleft())
                    total_tricks += 1
                    if len(winner_deck) == 52:
                        winner = 1 if last_payment_player == 0 else 2
                        return {
                            "status": "finished",
                            "cards": total_cards,
                            "tricks": total_tricks,
                            "winner": winner,
                        }
                    turn = last_payment_player
                    last_payment_player = -1
            else:
                turn = 1 - turn


# Empirical cumulative probability thresholds from Beggar-My-Neighbour research
# (Mann & Su 2008 / Camicia 64-bucket macro-distribution)
def get_rarity(cards: int, is_loop: bool) -> Tuple[str, int, str]:
    """
    Returns (tier_name, hex_color, percentile_string).
    """
    if is_loop:
        return (
            "♾️ INFINITE LOOP",
            0x9B59B6,  # Royal Purple
            "Top 0.000001% (Astronomically Rare!)",
        )

    if cards > 4000:
        return ("🔴 MYTHIC", 0xE74C3C, "Top 0.005% of all deals")
    elif cards > 1500:
        return ("🟠 LEGENDARY", 0xE67E22, "Top 0.04% of all deals")
    elif cards > 800:
        return ("🟣 EPIC", 0x9B59B6, "Top 0.45% of all deals")
    elif cards > 400:
        return ("🔵 RARE", 0x3498DB, "Top 4.2% of all deals")
    elif cards > 150:
        return ("🟢 UNCOMMON", 0x2ECC71, "Top 28% of all deals")
    else:
        return ("⚪ COMMON", 0x95A5A6, "Standard length (median ~70 cards)")
