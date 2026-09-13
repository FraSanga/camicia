#ifndef CAMICIA_PERMUTATION_HPP
#define CAMICIA_PERMUTATION_HPP

#include <vector>
#include <string>
#include <array>
#include "int128_io.hpp"
#include "engine.hpp"

// Zero-allocation versions writing 52 cards directly into destination
void getNthPermutation(int128 n, Card* out);
void getNthPermutation(int128 n, std::array<Card, 52>& out);

// Backwards-compatible string version
std::vector<std::string> getNthPermutation(int128 n);

#endif
