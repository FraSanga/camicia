#ifndef CAMICIA_KERNEL_SOURCE_HPP
#define CAMICIA_KERNEL_SOURCE_HPP

inline const char* getEmbeddedKernelSource() {
    return R"CL_CODE(
// OpenCL C Kernel for Camicia (Beggar-My-Neighbour) simulation
// Exhaustively searches a batch of permutations for loops and longest finished games.

typedef ulong u64;
typedef uchar u8;

typedef struct {
    u64 hi;
    u64 lo;
} uint128;

inline uint128 add128(uint128 a, uint128 b) {
    uint128 r;
    r.lo = a.lo + b.lo;
    r.hi = a.hi + b.hi + (r.lo < a.lo ? 1UL : 0UL);
    return r;
}

inline uint128 sub128(uint128 a, uint128 b) {
    uint128 r;
    r.hi = a.hi - b.hi - (a.lo < b.lo ? 1UL : 0UL);
    r.lo = a.lo - b.lo;
    return r;
}

inline bool lt128(uint128 a, uint128 b) {
    return (a.hi < b.hi) || (a.hi == b.hi && a.lo < b.lo);
}

// 128-bit by 64-bit multiplication using OpenCL native mul_hi
inline uint128 mul128_64(uint128 a, u64 b) {
    uint128 r;
    r.lo = a.lo * b;
    r.hi = a.hi * b + mul_hi(a.lo, b);
    return r;
}

inline uint128 fast_multinomial(const int counts[5], __constant const u64* nCr_table) {
    int current_n = counts[0] + counts[1] + counts[2] + counts[3] + counts[4];
    uint128 res;
    res.hi = 0;
    res.lo = 1;

    for (int i = 0; i < 4; ++i) {
        if (counts[i] > 0) {
            u64 val = nCr_table[current_n * 53 + counts[i]];
            res = mul128_64(res, val);
            current_n -= counts[i];
        }
    }
    return res;
}

inline void getNthPermutationCards(uint128 n, u8* deck, __constant const u64* nCr_table) {
    int counts[5] = {4, 4, 4, 4, 36};
    const u8 symbols[5] = {1, 13, 12, 11, 0}; // ACE, KING, QUEEN, JACK, NUMBER

    for (int i = 0; i < 52; ++i) {
        if (counts[0] == 0 && counts[1] == 0 && counts[2] == 0 && counts[3] == 0) {
            for (int j = i; j < 52; ++j) deck[j] = 0;
            return;
        }

        for (int s = 0; s < 5; ++s) {
            if (counts[s] == 0) continue;
            counts[s]--;
            uint128 num = fast_multinomial(counts, nCr_table);

            if (lt128(n, num)) {
                deck[i] = symbols[s];
                break;
            } else {
                n = sub128(n, num);
                counts[s]++;
            }
        }
    }
}

typedef struct {
    u8 data[64];
    u8 head;
    u8 tail;
    u8 count;
} Queue;

inline void q_init(Queue* q) { q->head = q->tail = q->count = 0; }
inline bool q_empty(const Queue* q) { return q->count == 0; }
inline void q_push(Queue* q, u8 c) {
    q->data[q->tail] = c;
    q->tail = (q->tail + 1) & 63;
    q->count++;
}
inline u8 q_pop(Queue* q) {
    u8 c = q->data[q->head];
    q->head = (q->head + 1) & 63;
    q->count--;
    return c;
}

inline int getPenalty(u8 card) {
    switch (card) {
        case 1:  return 4; // ACE
        case 13: return 3; // KING
        case 12: return 2; // QUEEN
        case 11: return 1; // JACK
        default: return 0;
    }
}

typedef struct {
    u64 hi;
    u64 lo;
} Fingerprint;

inline Fingerprint fingerprint(int turn, const Queue* a, const Queue* b) {
    const u64 FNV_PRIME = 1099511628211UL;
    u64 hi = 0xcbf29ce484222325UL;
    u64 lo = 0x9E3779B97F4A7C15UL;

    #define MIX(byte) do { \
        u64 val = (u64)(byte); \
        hi = (hi ^ val) * FNV_PRIME; \
        lo = (lo ^ val) * FNV_PRIME; \
    } while (0)

    MIX(turn);
    for (u8 i = 0, idx = a->head; i < a->count; ++i, idx = (idx + 1) & 63) {
        MIX(a->data[idx]);
    }
    MIX(0xFF);
    for (u8 i = 0, idx = b->head; i < b->count; ++i, idx = (idx + 1) & 63) {
        MIX(b->data[idx]);
    }
    #undef MIX

    Fingerprint fp = {hi, lo};
    return fp;
}

// Result structure written back to host buffers
// Status: 0 = Finished, 1 = Loop, 2 = Needs CPU Verification (e.g. >10,000 cards)
typedef struct {
    uint status;
    uint cards;
    uint tricks;
    uint pad;
    u64 index_hi;
    u64 index_lo;
} DealOutcome;

__kernel void camicia_simulate_batch(
    u64 base_hi,
    u64 base_lo,
    uint total_deals,
    __constant const u64* nCr_table,
    __global DealOutcome* out_deals
) {
    uint gid = get_global_id(0);
    if (gid >= total_deals) return;

    uint128 deal_offset = {0, (u64)gid};
    uint128 base_idx = {base_hi, base_lo};
    uint128 deal_idx = add128(base_idx, deal_offset);

    u8 deck[52];
    getNthPermutationCards(deal_idx, deck, nCr_table);

    Queue deckA, deckB, pile;
    q_init(&deckA); q_init(&deckB); q_init(&pile);

    for (int i = 0; i < 26; ++i) q_push(&deckA, deck[i]);
    for (int i = 26; i < 52; ++i) q_push(&deckB, deck[i]);

    #define HASH_CAP 128
    u64 seen_hi[HASH_CAP];
    u64 seen_lo[HASH_CAP];
    for (int i = 0; i < HASH_CAP; ++i) {
        seen_hi[i] = 0;
        seen_lo[i] = 0;
    }
    int recordedStates = 0;

    uint totalCardsPlayed = 0;
    uint totalTricks = 0;
    int turn = 0;
    int penaltyRemaining = 0;
    int lastPaymentPlayer = -1;

    while (true) {
        if (penaltyRemaining == 0 && q_empty(&pile)) {
            Fingerprint fp = fingerprint(turn, &deckA, &deckB);

            uint idx = (uint)((fp.hi ^ (fp.lo >> 32)) & (HASH_CAP - 1));
            bool found = false;
            while (seen_hi[idx] != 0 || seen_lo[idx] != 0) {
                if (seen_hi[idx] == fp.hi && seen_lo[idx] == fp.lo) {
                    found = true;
                    break;
                }
                idx = (idx + 1) & (HASH_CAP - 1);
            }

            if (found) {
                // Verified Loop
                out_deals[gid].status = 1;
                out_deals[gid].cards = totalCardsPlayed;
                out_deals[gid].tricks = totalTricks;
                out_deals[gid].index_hi = deal_idx.hi;
                out_deals[gid].index_lo = deal_idx.lo;
                return;
            }

            if (recordedStates < HASH_CAP * 3 / 4) {
                seen_hi[idx] = fp.hi;
                seen_lo[idx] = fp.lo;
                recordedStates++;
            }
        }

        if (totalCardsPlayed >= 10000) {
            // Very long game: flag for CPU host verification to avoid GPU thread timeout
            out_deals[gid].status = 2;
            out_deals[gid].cards = totalCardsPlayed;
            out_deals[gid].tricks = totalTricks;
            out_deals[gid].index_hi = deal_idx.hi;
            out_deals[gid].index_lo = deal_idx.lo;
            return;
        }

        Queue* active = (turn == 0) ? &deckA : &deckB;
        Queue* opponent = (turn == 0) ? &deckB : &deckA;

        if (q_empty(active)) {
            if (q_empty(&pile)) {
                // Game Finished
                out_deals[gid].status = 0;
                out_deals[gid].cards = totalCardsPlayed;
                out_deals[gid].tricks = totalTricks;
                out_deals[gid].index_hi = deal_idx.hi;
                out_deals[gid].index_lo = deal_idx.lo;
                return;
            }
            while (!q_empty(&pile)) q_push(opponent, q_pop(&pile));
            totalTricks++;
            if (opponent->count == 52) {
                out_deals[gid].status = 0;
                out_deals[gid].cards = totalCardsPlayed;
                out_deals[gid].tricks = totalTricks;
                out_deals[gid].index_hi = deal_idx.hi;
                out_deals[gid].index_lo = deal_idx.lo;
                return;
            }
            turn = 1 - turn;
            penaltyRemaining = 0;
            lastPaymentPlayer = -1;
            continue;
        }

        u8 playedCard = q_pop(active);
        q_push(&pile, playedCard);
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
                    Queue* winner = (lastPaymentPlayer == 0) ? &deckA : &deckB;
                    while (!q_empty(&pile)) q_push(winner, q_pop(&pile));
                    totalTricks++;
                    if (winner->count == 52) {
                        out_deals[gid].status = 0;
                        out_deals[gid].cards = totalCardsPlayed;
                        out_deals[gid].tricks = totalTricks;
                        out_deals[gid].index_hi = deal_idx.hi;
                        out_deals[gid].index_lo = deal_idx.lo;
                        return;
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

)CL_CODE";
}

#endif
