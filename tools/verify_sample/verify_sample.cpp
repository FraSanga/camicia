// Statistical spot-check verifier for a submitted workunit's results.
//
// Why sampling instead of just re-simulating the whole block: a block is
// range_size deals (1e9 by default) and sample_bitwise_validator already
// gets byte-identical agreement from quorum-of-2 for free, so this tool
// isn't a replacement for that -- it's for a human who wants an *offline*,
// much cheaper second opinion on a block that's already been assimilated,
// without paying the cost of a full re-simulation.
//
// Why this can't just diff a sampled deal's outcome against a recorded
// value: worker.cpp (see its main loop) only ever writes out two kinds of
// lines -- one "loop,<index>,<cards>,<tricks>" per loop found, and at most
// one "finished,<index>,<cards>,<tricks>" for the single BEST finished deal
// in the whole block. Every other "finished" deal (the overwhelming
// majority -- the whole reason it's a "best") is computed, compared, and
// discarded in the same loop iteration, with zero trace left anywhere.
// There is no per-deal ground truth to look up for those.
//
// So instead of ground-truth comparison, this checks each sampled deal for
// *consistency* with the block's own recorded invariants:
//   - a sampled deal whose true outcome is "loop" must appear in the
//     recorded loop lines;
//   - a sampled deal whose true outcome is "finished" must not beat the
//     recorded best (if it does, the recorded "best" wasn't actually the
//     maximum -- a real inconsistency); and if the sampled index IS the
//     recorded best, its cards/tricks must match exactly.
// A tampered or buggy worker that fabricates/suppresses results has to get
// lucky on every single sampled deal to hide from this, which is exactly
// what gives the sampling formula below its meaning.
//
// How many deals to sample: this is a standard assurance-sampling question
// ("how big a sample catches a defect rate of at least p, with confidence
// C, if defects are spread uniformly through the population"), solved by
// Floyd's algorithm for the actual draw and the usual
//   n = ceil( ln(1-C) / ln(1-p) )
// approximation for the sample size (exact for sampling with replacement;
// a very close upper bound for without-replacement as long as n << N,
// which holds for any p worth using here -- see compute_sample_size()).
// This means the tool cannot promise "there is no wrong deal anywhere in
// this block" -- only "if at least a p-fraction of the block were wrong,
// we'd have caught it with C confidence". A single needle in a billion-deal
// haystack (p = 1/N) is NOT realistically catchable this way -- n would
// have to approach N itself. --defect-rate is a policy choice about the
// smallest *rate* of corruption worth worrying about, not an absolute
// count; pick it to match the failure modes you actually expect (a bug
// affecting a random subset of deals), not deliberate single-deal fraud.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <string>
#include <vector>
#include <random>
#include <unordered_set>
#include <fstream>
#include <sstream>
#include <optional>

#include "int128_io.hpp"
#include "permutation.hpp"
#include "engine.hpp"

using namespace std;

struct RecordedBest {
    int128 index;
    long long cards;
    long long tricks;
};

struct RecordedOutput {
    optional<RecordedBest> best;
    unordered_set<string> loopIndices; // int128ToString(index) -> present
};

// Parses the plain per-WU output format worker.cpp itself writes (the `out`
// file / templates/simulator_out.xml), i.e. lines "finished,<idx>,<c>,<t>"
// or "loop,<idx>,<c>,<t>" -- NOT results.txt's format, which additionally
// prefixes every line with "<wu_name>,". To feed a results.txt line in,
// strip the prefix first, e.g.:
//   grep '^simulator_0000000000_0000999999,' results.txt | cut -d, -f2- > wu_out.txt
static bool parse_result_file(const string& path, RecordedOutput& out, string& err) {
    ifstream f(path);
    if (!f) {
        err = "cannot open result file: " + path;
        return false;
    }
    string line;
    int lineNo = 0;
    while (getline(f, line)) {
        lineNo++;
        if (line.empty()) continue;
        stringstream ss(line);
        string status, idxStr, cardsStr, tricksStr;
        if (!getline(ss, status, ',') || !getline(ss, idxStr, ',') ||
            !getline(ss, cardsStr, ',') || !getline(ss, tricksStr, ',')) {
            err = "malformed line " + to_string(lineNo) + " in " + path + ": " + line;
            return false;
        }
        long long cards = atoll(cardsStr.c_str());
        long long tricks = atoll(tricksStr.c_str());
        if (status == "finished") {
            if (out.best.has_value()) {
                err = "result file has more than one 'finished' line (only one is ever written per WU) -- wrong file, or a results.txt with more than one WU's lines mixed in?";
                return false;
            }
            out.best = RecordedBest{stringTo128(idxStr), cards, tricks};
        } else if (status == "loop") {
            out.loopIndices.insert(idxStr);
        } else {
            err = "unrecognized status '" + status + "' on line " + to_string(lineNo);
            return false;
        }
    }
    return true;
}

// n = ceil( ln(1-C) / ln(1-p) ), clamped to [1, N]. See the file header for
// the derivation and its limits.
static uint64_t compute_sample_size(double confidence, double defectRate, uint64_t N) {
    double n = log(1.0 - confidence) / log(1.0 - defectRate);
    uint64_t sampleSize = (uint64_t)ceil(n);
    if (sampleSize < 1) sampleSize = 1;
    if (sampleSize > N) sampleSize = N;
    return sampleSize;
}

// Floyd's algorithm: picks n distinct values uniformly from [0, N) in O(n)
// time/memory regardless of how close n is to N (unlike naive
// draw-and-reject sampling, which degrades badly as n approaches N).
static vector<uint64_t> sample_distinct_offsets(uint64_t N, uint64_t n, uint64_t seed) {
    mt19937_64 rng(seed);
    unordered_set<uint64_t> chosen;
    vector<uint64_t> result;
    chosen.reserve(n * 2);
    result.reserve(n);
    for (uint64_t j = N - n; j < N; ++j) {
        uniform_int_distribution<uint64_t> dist(0, j);
        uint64_t t = dist(rng);
        if (chosen.count(t)) {
            chosen.insert(j);
            result.push_back(j);
        } else {
            chosen.insert(t);
            result.push_back(t);
        }
    }
    return result;
}

struct Anomaly {
    int128 index;
    string detail;
};

static GameResult simulate_deal(int128 index) {
    vector<string> deck = getNthPermutation(index);
    vector<string> a(deck.begin(), deck.begin() + 26);
    vector<string> b(deck.begin() + 26, deck.end());
    CamiciaGame game(a, b);
    return game.simulate();
}

static void check_deal(int128 index, const RecordedOutput& recorded, vector<Anomaly>& anomalies) {
    GameResult res = simulate_deal(index);
    string idxStr = int128ToString(index);

    if (res.status == "loop") {
        if (!recorded.loopIndices.count(idxStr)) {
            anomalies.push_back({index,
                "true outcome is a loop (cards=" + to_string(res.cards) +
                ", tricks=" + to_string(res.tricks) +
                ") but no matching 'loop," + idxStr + ",...' line is recorded"});
        }
        return;
    }

    // "finished"
    if (!recorded.best.has_value()) {
        anomalies.push_back({index,
            "true outcome is 'finished' (cards=" + to_string(res.cards) +
            ") but the WU recorded no finished line at all"});
        return;
    }
    if (index == recorded.best->index) {
        if (res.cards != recorded.best->cards || res.tricks != recorded.best->tricks) {
            anomalies.push_back({index,
                "recorded best-finished value mismatch: recorded cards=" +
                to_string(recorded.best->cards) + " tricks=" + to_string(recorded.best->tricks) +
                ", true cards=" + to_string(res.cards) + " tricks=" + to_string(res.tricks)});
        }
    } else if (res.cards > recorded.best->cards) {
        anomalies.push_back({index,
            "true outcome is 'finished' with cards=" + to_string(res.cards) +
            ", which beats the recorded best (cards=" + to_string(recorded.best->cards) +
            " at index " + int128ToString(recorded.best->index) +
            ") -- the recorded best is not actually the maximum"});
    }
}

void usage(const char* name) {
    fprintf(stderr,
        "Camicia spot-check verifier: instead of re-simulating an entire\n"
        "assimilated block, samples a random subset of its deals and checks\n"
        "each one for consistency with the block's recorded output (see the\n"
        "comment at the top of verify_sample.cpp for exactly what that means\n"
        "and what it can/can't catch).\n\n"
        "Usage: %s --start S --end E --result-file PATH [OPTION]...\n\n"
        "Required:\n"
        "  --start S            First deal index of the block (inclusive, decimal)\n"
        "  --end E              Last deal index of the block (inclusive, decimal)\n"
        "  --result-file PATH   Plain per-WU output file (worker.cpp's own 'out'\n"
        "                       format: 'finished,<idx>,<c>,<t>' / 'loop,<idx>,<c>,<t>'\n"
        "                       lines). NOT results.txt directly -- strip that\n"
        "                       file's leading '<wu_name>,' prefix first, e.g.:\n"
        "                         grep '^WU_NAME,' results.txt | cut -d, -f2- > out.txt\n\n"
        "Options:\n"
        "  --confidence C       Target confidence, 0 < C < 1 (default: 0.99)\n"
        "  --defect-rate P      Smallest fraction of the block worth being able to\n"
        "                       catch, 0 < P < 1 (default: 0.0001, i.e. 0.01%%).\n"
        "                       This is a policy choice, not a measured quantity --\n"
        "                       see the file header comment before changing it.\n"
        "  --samples N          Use this sample size directly instead of computing\n"
        "                       one from --confidence/--defect-rate\n"
        "  --seed N             RNG seed (default: random, from random_device)\n"
        "  --estimate-only      Print the computed sample size and exit, without\n"
        "                       simulating anything\n"
        "  -h, --help           This message\n",
        name
    );
}

int main(int argc, char** argv) {
    string startStr, endStr, resultFile;
    double confidence = 0.99;
    double defectRate = 0.0001;
    long long samplesOverride = -1;
    uint64_t seed = random_device{}();
    bool estimateOnly = false;

    for (int i = 1; i < argc; i++) {
        auto next = [&](const char* flag) -> const char* {
            if (i + 1 >= argc) {
                fprintf(stderr, "%s requires an argument\n", flag);
                exit(2);
            }
            return argv[++i];
        };
        if (!strcmp(argv[i], "--start")) {
            startStr = next("--start");
        } else if (!strcmp(argv[i], "--end")) {
            endStr = next("--end");
        } else if (!strcmp(argv[i], "--result-file")) {
            resultFile = next("--result-file");
        } else if (!strcmp(argv[i], "--confidence")) {
            confidence = atof(next("--confidence"));
        } else if (!strcmp(argv[i], "--defect-rate")) {
            defectRate = atof(next("--defect-rate"));
        } else if (!strcmp(argv[i], "--samples")) {
            samplesOverride = atoll(next("--samples"));
        } else if (!strcmp(argv[i], "--seed")) {
            seed = strtoull(next("--seed"), nullptr, 10);
        } else if (!strcmp(argv[i], "--estimate-only")) {
            estimateOnly = true;
        } else if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
            usage(argv[0]);
            return 0;
        } else {
            fprintf(stderr, "unknown argument: %s\n\n", argv[i]);
            usage(argv[0]);
            return 2;
        }
    }

    if (startStr.empty() || endStr.empty() || (resultFile.empty() && !estimateOnly)) {
        fprintf(stderr, "--start, --end, and --result-file are required\n\n");
        usage(argv[0]);
        return 2;
    }
    if (confidence <= 0.0 || confidence >= 1.0) {
        fprintf(stderr, "--confidence must be strictly between 0 and 1\n");
        return 2;
    }
    if (defectRate <= 0.0 || defectRate >= 1.0) {
        fprintf(stderr, "--defect-rate must be strictly between 0 and 1\n");
        return 2;
    }

    int128 start = stringTo128(startStr);
    int128 end = stringTo128(endStr);
    if (end < start) {
        fprintf(stderr, "--end must be >= --start\n");
        return 2;
    }
    int128 blockSizeFull = end - start + 1;
    // Sampling below draws offsets as uint64_t -- fine for any range_size
    // this project has ever used (1e9 by default; the entire 52-card space
    // is far larger, but no single WU spans anywhere near that).
    const int128 UINT64_MAX_AS_128 = (int128)UINT64_MAX;
    if (blockSizeFull > UINT64_MAX_AS_128) {
        fprintf(stderr, "block is too large for this tool's sampling (max ~1.8e19 deals per WU)\n");
        return 2;
    }
    uint64_t N = (uint64_t)blockSizeFull;

    uint64_t n = (samplesOverride > 0) ? (uint64_t)samplesOverride : compute_sample_size(confidence, defectRate, N);
    if (n > N) n = N;

    if (estimateOnly) {
        printf("block size N = %s\n", int128ToString(blockSizeFull).c_str());
        printf("sample size n = %llu (%.4f%% of the block)\n",
               (unsigned long long)n, 100.0 * (double)n / (double)N);
        if (samplesOverride <= 0) {
            printf("(computed for confidence=%.4f, defect-rate=%.6f)\n", confidence, defectRate);
        }
        return 0;
    }

    RecordedOutput recorded;
    string err;
    if (!parse_result_file(resultFile, recorded, err)) {
        fprintf(stderr, "%s\n", err.c_str());
        return 2;
    }

    bool fullVerification = (n >= N);
    vector<uint64_t> offsets;
    if (fullVerification) {
        offsets.resize(N);
        for (uint64_t i = 0; i < N; ++i) offsets[i] = i;
    } else {
        offsets = sample_distinct_offsets(N, n, seed);
    }

    vector<Anomaly> anomalies;
    for (uint64_t off : offsets) {
        check_deal(start + (int128)off, recorded, anomalies);
    }

    printf("Checked %llu / %s deals (%.4f%%)%s.\n",
           (unsigned long long)offsets.size(), int128ToString(blockSizeFull).c_str(),
           100.0 * (double)offsets.size() / (double)N,
           fullVerification ? " -- sample size reached the full block, this was an exhaustive check" : "");

    if (anomalies.empty()) {
        if (!fullVerification) {
            printf("No inconsistencies found. If at least %.4f%% of this block's deals were "
                   "wrong, this run had a >= %.2f%% chance of catching at least one -- finding "
                   "none is %.2f%% confidence the true defect rate is below that.\n",
                   defectRate * 100.0, confidence * 100.0, confidence * 100.0);
        } else {
            printf("No inconsistencies found across the entire block.\n");
        }
        return 0;
    }

    printf("\n%zu inconsistenc%s found:\n", anomalies.size(), anomalies.size() == 1 ? "y" : "ies");
    for (const auto& a : anomalies) {
        printf("  index %s: %s\n", int128ToString(a.index).c_str(), a.detail.c_str());
    }
    return 1;
}
