#include <iostream>
#include <iomanip>
#include <vector>
#include <string>
#include <chrono>
#include <thread>
#include <atomic>
#include <mutex>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <algorithm>

#if __has_include("permutation.hpp")
#include "permutation.hpp"
#include "engine.hpp"
#include "int128_io.hpp"
#elif __has_include("core/permutation.hpp")
#include "core/permutation.hpp"
#include "core/engine.hpp"
#include "core/int128_io.hpp"
#elif __has_include("tools/worker/core/permutation.hpp")
#include "tools/worker/core/permutation.hpp"
#include "tools/worker/core/engine.hpp"
#include "tools/worker/core/int128_io.hpp"
#endif

#if __has_include("opencl_dispatch.hpp")
#include "opencl_dispatch.hpp"
#include "kernel_source.hpp"
#elif __has_include("opencl/opencl_dispatch.hpp")
#include "opencl/opencl_dispatch.hpp"
#include "opencl/kernel_source.hpp"
#elif __has_include("tools/worker/opencl/opencl_dispatch.hpp")
#include "tools/worker/opencl/opencl_dispatch.hpp"
#include "tools/worker/opencl/kernel_source.hpp"
#endif

#if __has_include("metal_dispatch.hpp")
#include "metal_dispatch.hpp"
#include "metal_kernel_source.hpp"
#elif __has_include("metal/metal_dispatch.hpp")
#include "metal/metal_dispatch.hpp"
#include "metal/metal_kernel_source.hpp"
#elif __has_include("tools/worker/metal/metal_dispatch.hpp")
#include "tools/worker/metal/metal_dispatch.hpp"
#include "tools/worker/metal/metal_kernel_source.hpp"
#endif

struct BenchmarkResult {
    std::string name;
    int threads = 1;
    uint32_t batchSize = 0;
    uint64_t count = 0;
    double elapsedSeconds = 0.0;
    double dealsPerSec = 0.0;
    double speedup = 1.0;
    long long maxCards = 0;
    int128 maxCardsIndex = 0;
    uint64_t loopsFound = 0;
    uint64_t fallbacks = 0;
    bool success = false;
    std::string notes;
};

static std::string formatTime(double seconds) {
    if (seconds <= 0) return "--";
    uint64_t totalSec = (uint64_t)seconds;
    uint64_t hrs = totalSec / 3600;
    uint64_t mins = (totalSec % 3600) / 60;
    uint64_t secs = totalSec % 60;
    char buf[64];
    if (hrs > 0) {
        snprintf(buf, sizeof(buf), "%lluh %02llum %02llus", (unsigned long long)hrs, (unsigned long long)mins, (unsigned long long)secs);
    } else if (mins > 0) {
        snprintf(buf, sizeof(buf), "%02llum %02llus", (unsigned long long)mins, (unsigned long long)secs);
    } else {
        snprintf(buf, sizeof(buf), "%.2fs", seconds);
    }
    return std::string(buf);
}

static std::string formatNumber(uint64_t n) {
    std::string s = std::to_string(n);
    int insertPosition = (int)s.length() - 3;
    while (insertPosition > 0) {
        s.insert(insertPosition, ",");
        insertPosition -= 3;
    }
    return s;
}

// Single or multi-threaded CPU benchmark
BenchmarkResult benchmarkCpu(int128 startIndex, uint64_t count, int numThreads) {
    BenchmarkResult res;
    res.name = (numThreads > 1) ? ("CPU (" + std::to_string(numThreads) + " threads)") : "CPU (Single-thread)";
    res.threads = numThreads;
    res.count = count;

    std::atomic<uint64_t> totalLoops{0};
    std::mutex resultMutex;
    long long globalMaxCards = 0;
    int128 globalMaxCardsIndex = 0;

    auto tStart = std::chrono::high_resolution_clock::now();

    if (numThreads <= 1) {
        StateTracker tracker;
        Card d[52];
        for (uint64_t i = 0; i < count; ++i) {
            int128 dealIdx = startIndex + i;
            getNthPermutation(dealIdx, d);
            GameResult gr = CamiciaGame::simulate(d, 26, d + 26, 26, tracker);
            if (gr.status == "finished") {
                if (gr.cards > globalMaxCards) {
                    globalMaxCards = gr.cards;
                    globalMaxCardsIndex = dealIdx;
                }
            } else if (gr.status == "loop") {
                totalLoops.fetch_add(1, std::memory_order_relaxed);
            }
        }
    } else {
        std::vector<std::thread> workers;
        uint64_t chunkSize = count / numThreads;
        for (int t = 0; t < numThreads; ++t) {
            uint64_t tStartIdx = t * chunkSize;
            uint64_t tCount = (t == numThreads - 1) ? (count - tStartIdx) : chunkSize;
            int128 tBase = startIndex + tStartIdx;

            workers.emplace_back([tBase, tCount, &totalLoops, &resultMutex, &globalMaxCards, &globalMaxCardsIndex]() {
                StateTracker tracker;
                Card d[52];
                long long localMaxCards = 0;
                int128 localMaxIndex = 0;
                uint64_t localLoops = 0;

                for (uint64_t i = 0; i < tCount; ++i) {
                    int128 dealIdx = tBase + i;
                    getNthPermutation(dealIdx, d);
                    GameResult gr = CamiciaGame::simulate(d, 26, d + 26, 26, tracker);
                    if (gr.status == "finished") {
                        if (gr.cards > localMaxCards) {
                            localMaxCards = gr.cards;
                            localMaxIndex = dealIdx;
                        }
                    } else if (gr.status == "loop") {
                        localLoops++;
                    }
                }

                if (localLoops > 0) totalLoops.fetch_add(localLoops, std::memory_order_relaxed);
                if (localMaxCards > 0) {
                    std::lock_guard<std::mutex> lock(resultMutex);
                    if (localMaxCards > globalMaxCards) {
                        globalMaxCards = localMaxCards;
                        globalMaxCardsIndex = localMaxIndex;
                    }
                }
            });
        }
        for (auto& w : workers) {
            if (w.joinable()) w.join();
        }
    }

    auto tEnd = std::chrono::high_resolution_clock::now();
    res.elapsedSeconds = std::chrono::duration<double>(tEnd - tStart).count();
    res.dealsPerSec = (res.elapsedSeconds > 0) ? ((double)count / res.elapsedSeconds) : 0.0;
    res.maxCards = globalMaxCards;
    res.maxCardsIndex = globalMaxCardsIndex;
    res.loopsFound = totalLoops.load();
    res.success = true;

    return res;
}

// OpenCL GPU benchmark
BenchmarkResult benchmarkOpenCL(int128 startIndex, uint64_t count, int deviceIndex, uint32_t batchSize) {
    BenchmarkResult res;
    res.name = "OpenCL GPU (Batch " + std::to_string(batchSize) + ")";
    res.threads = 1;
    res.batchSize = batchSize;
    res.count = count;

    OpenCLDispatcher dispatcher;
    std::string err;
    if (!dispatcher.initLoader(err)) {
        res.notes = "Loader init failed: " + err;
        return res;
    }

    auto devices = dispatcher.listDevices();
    if (devices.empty()) {
        res.notes = "No OpenCL devices found.";
        return res;
    }

    if (deviceIndex < 0 || deviceIndex >= (int)devices.size()) {
        deviceIndex = 0;
        for (size_t i = 0; i < devices.size(); ++i) {
            if (devices[i].isGpu) { deviceIndex = (int)i; break; }
        }
    }

    res.name = "OpenCL [" + devices[deviceIndex].deviceName + "] (Batch " + std::to_string(batchSize) + ")";

    std::string kernelSrc = getEmbeddedKernelSource();
    if (!dispatcher.initDevice(deviceIndex, kernelSrc, err)) {
        res.notes = "Device init / compile failed: " + err;
        return res;
    }

    StateTracker cpuTracker;
    Card d[52];
    std::vector<GpuDealOutcome> outcomes;
    uint64_t processed = 0;
    uint64_t totalLoops = 0;
    uint64_t fallbacks = 0;
    long long maxCards = 0;
    int128 maxCardsIndex = 0;

    auto tStart = std::chrono::high_resolution_clock::now();

    while (processed < count) {
        uint32_t curBatch = (uint32_t)std::min<uint64_t>((uint64_t)batchSize, count - processed);
        int128 curBase = startIndex + processed;

        if (!dispatcher.runBatch(curBase, curBatch, outcomes, err)) {
            res.notes = "runBatch failed: " + err;
            return res;
        }

        for (uint32_t i = 0; i < curBatch; ++i) {
            int128 dealIdx = curBase + i;
            if (outcomes[i].status == 0) {
                if ((long long)outcomes[i].cards > maxCards) {
                    maxCards = outcomes[i].cards;
                    maxCardsIndex = dealIdx;
                }
            } else if (outcomes[i].status == 1) {
                totalLoops++;
            } else if (outcomes[i].status == 2) {
                fallbacks++;
                getNthPermutation(dealIdx, d);
                GameResult gr = CamiciaGame::simulate(d, 26, d + 26, 26, cpuTracker);
                if (gr.status == "finished" && gr.cards > maxCards) {
                    maxCards = gr.cards;
                    maxCardsIndex = dealIdx;
                } else if (gr.status == "loop") {
                    totalLoops++;
                }
            }
        }
        processed += curBatch;
    }

    auto tEnd = std::chrono::high_resolution_clock::now();
    res.elapsedSeconds = std::chrono::duration<double>(tEnd - tStart).count();
    res.dealsPerSec = (res.elapsedSeconds > 0) ? ((double)count / res.elapsedSeconds) : 0.0;
    res.maxCards = maxCards;
    res.maxCardsIndex = maxCardsIndex;
    res.loopsFound = totalLoops;
    res.fallbacks = fallbacks;
    res.success = true;

    return res;
}

// Apple Metal GPU benchmark (macOS only)
BenchmarkResult benchmarkMetal(int128 startIndex, uint64_t count, int deviceIndex, uint32_t batchSize) {
    BenchmarkResult res;
    res.name = "Apple Metal (Batch " + std::to_string(batchSize) + ")";
    res.threads = 1;
    res.batchSize = batchSize;
    res.count = count;

#if defined(__APPLE__)
    MetalDispatcher dispatcher;
    std::string err;
    std::string kernelSrc = getEmbeddedMetalKernelSource();
    if (!dispatcher.initDevice(deviceIndex >= 0 ? deviceIndex : 0, kernelSrc, err)) {
        res.notes = "Metal init failed: " + err;
        return res;
    }

    res.name = "Metal [" + dispatcher.getDeviceName() + "] (Batch " + std::to_string(batchSize) + ")";

    StateTracker cpuTracker;
    Card d[52];
    std::vector<MetalDealOutcome> outcomes;
    uint64_t processed = 0;
    uint64_t totalLoops = 0;
    uint64_t fallbacks = 0;
    long long maxCards = 0;
    int128 maxCardsIndex = 0;

    auto tStart = std::chrono::high_resolution_clock::now();

    while (processed < count) {
        uint32_t curBatch = (uint32_t)std::min<uint64_t>((uint64_t)batchSize, count - processed);
        int128 curBase = startIndex + processed;

        if (!dispatcher.runBatch(curBase, curBatch, outcomes, err)) {
            res.notes = "runBatch failed: " + err;
            return res;
        }

        for (uint32_t i = 0; i < curBatch; ++i) {
            int128 dealIdx = curBase + i;
            if (outcomes[i].status == 0) {
                if ((long long)outcomes[i].cards > maxCards) {
                    maxCards = outcomes[i].cards;
                    maxCardsIndex = dealIdx;
                }
            } else if (outcomes[i].status == 1) {
                totalLoops++;
            } else if (outcomes[i].status == 2) {
                fallbacks++;
                getNthPermutation(dealIdx, d);
                GameResult gr = CamiciaGame::simulate(d, 26, d + 26, 26, cpuTracker);
                if (gr.status == "finished" && gr.cards > maxCards) {
                    maxCards = gr.cards;
                    maxCardsIndex = dealIdx;
                } else if (gr.status == "loop") {
                    totalLoops++;
                }
            }
        }
        processed += curBatch;
    }

    auto tEnd = std::chrono::high_resolution_clock::now();
    res.elapsedSeconds = std::chrono::duration<double>(tEnd - tStart).count();
    res.dealsPerSec = (res.elapsedSeconds > 0) ? ((double)count / res.elapsedSeconds) : 0.0;
    res.maxCards = maxCards;
    res.maxCardsIndex = maxCardsIndex;
    res.loopsFound = totalLoops;
    res.fallbacks = fallbacks;
    res.success = true;
#else
    res.notes = "Metal is only supported on macOS.";
#endif

    return res;
}

int main(int argc, char* argv[]) {
    uint64_t count = 100000; // default 100k
    int128 startIndex = 0;
    int threads = (int)std::thread::hardware_concurrency();
    if (threads == 0) threads = 4;
    int targetDevice = -1;
    uint32_t batchSize = 65536;

    bool runAll = false;
    bool runCpuOnly = false;
    bool runOpenclOnly = false;
    bool runMetalOnly = false;
    bool sweepBatch = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--count" && i + 1 < argc) {
            count = std::stoull(argv[++i]);
        } else if (arg == "--start" && i + 1 < argc) {
            startIndex = stringTo128(argv[++i]);
        } else if (arg == "--threads" && i + 1 < argc) {
            threads = std::atoi(argv[++i]);
            runCpuOnly = true;
        } else if (arg == "--batch-size" && i + 1 < argc) {
            batchSize = (uint32_t)std::stoul(argv[++i]);
        } else if (arg == "--device" && i + 1 < argc) {
            targetDevice = std::atoi(argv[++i]);
        } else if (arg == "--cpu") {
            runCpuOnly = true;
        } else if (arg == "--opencl") {
            runOpenclOnly = true;
        } else if (arg == "--metal") {
            runMetalOnly = true;
        } else if (arg == "--sweep") {
            sweepBatch = true;
        } else if (arg == "--all") {
            runAll = true;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: " << argv[0] << " [options]\n"
                      << "Options:\n"
                      << "  --count <N>       Number of deals to simulate (default: 100000)\n"
                      << "  --start <index>   Starting 128-bit permutation index (default: 0)\n"
                      << "  --threads <T>     Number of CPU worker threads (default: hardware concurrency)\n"
                      << "  --batch-size <B>  GPU batch size (default: 65536)\n"
                      << "  --device <N>      GPU device index (default: auto)\n"
                      << "  --cpu             Run CPU benchmark only\n"
                      << "  --opencl          Run OpenCL GPU benchmark only\n"
                      << "  --metal           Run Apple Metal GPU benchmark only\n"
                      << "  --sweep           Sweep GPU batch sizes (16k..256k)\n"
                      << "  --all             Run all available backends (default)\n";
            return 0;
        }
    }

    if (!runCpuOnly && !runOpenclOnly && !runMetalOnly) {
        runAll = true;
    }

    std::cout << "================================================================================\n"
              << "                    Camicia Simulation Engine Benchmark\n"
              << "================================================================================\n"
              << "Deals to simulate:   " << formatNumber(count) << " deals\n"
              << "Start index:         " << int128ToString(startIndex) << "\n"
              << "CPU Threads:         " << threads << " (detected logical cores: " << std::thread::hardware_concurrency() << ")\n"
              << "Default Batch Size:  " << formatNumber(batchSize) << "\n"
              << "================================================================================\n\n";

    std::vector<BenchmarkResult> results;
    BenchmarkResult baselineCpu;

    // 1. Single-threaded CPU baseline
    if (runAll || (runCpuOnly && threads == 1)) {
        std::cout << ">>> Running CPU (Single-threaded) baseline...\n";
        auto r = benchmarkCpu(startIndex, count, 1);
        baselineCpu = r;
        results.push_back(r);
        std::cout << "    Elapsed: " << std::fixed << std::setprecision(3) << r.elapsedSeconds << " s | "
                  << "Throughput: " << std::fixed << std::setprecision(0) << formatNumber((uint64_t)r.dealsPerSec) << " deals/s | "
                  << "Max cards: " << r.maxCards << " (deal " << int128ToString(r.maxCardsIndex) << ")\n\n";
    }

    // 2. Multi-threaded CPU
    if (runAll || (runCpuOnly && threads > 1)) {
        int t = runCpuOnly ? threads : (int)std::thread::hardware_concurrency();
        if (t > 1) {
            std::cout << ">>> Running CPU (Multi-threaded: " << t << " threads)...\n";
            auto r = benchmarkCpu(startIndex, count, t);
            if (baselineCpu.dealsPerSec > 0) {
                r.speedup = r.dealsPerSec / baselineCpu.dealsPerSec;
            }
            results.push_back(r);
            std::cout << "    Elapsed: " << std::fixed << std::setprecision(3) << r.elapsedSeconds << " s | "
                      << "Throughput: " << std::fixed << std::setprecision(0) << formatNumber((uint64_t)r.dealsPerSec) << " deals/s | "
                      << "Speedup: " << std::fixed << std::setprecision(2) << r.speedup << "x ("
                      << std::fixed << std::setprecision(1) << (r.speedup / t * 100.0) << "% scaling efficiency)\n\n";
        }
    }

    // 3. OpenCL GPU
    if (runAll || runOpenclOnly) {
        std::vector<uint32_t> batchesToTest = {batchSize};
        if (sweepBatch) batchesToTest = {16384, 32768, 65536, 131072, 262144};

        for (uint32_t b : batchesToTest) {
            std::cout << ">>> Running OpenCL GPU (Batch size: " << formatNumber(b) << ")...\n";
            auto r = benchmarkOpenCL(startIndex, count, targetDevice, b);
            if (r.success) {
                if (baselineCpu.dealsPerSec > 0) {
                    r.speedup = r.dealsPerSec / baselineCpu.dealsPerSec;
                }
                results.push_back(r);
                std::cout << "    Elapsed: " << std::fixed << std::setprecision(3) << r.elapsedSeconds << " s | "
                          << "Throughput: " << std::fixed << std::setprecision(0) << formatNumber((uint64_t)r.dealsPerSec) << " deals/s | "
                          << "Speedup: " << std::fixed << std::setprecision(2) << r.speedup << "x vs 1-thread CPU\n";
                if (r.fallbacks > 0) {
                    std::cout << "    Host CPU fallbacks (>10k cards): " << r.fallbacks << "\n";
                }
                std::cout << "\n";
            } else {
                std::cout << "    [OpenCL skipped: " << r.notes << "]\n\n";
            }
        }
    }

    // 4. Apple Metal GPU (macOS)
#if defined(__APPLE__)
    if (runAll || runMetalOnly) {
        std::vector<uint32_t> batchesToTest = {batchSize};
        if (sweepBatch) batchesToTest = {16384, 32768, 65536, 131072, 262144};

        for (uint32_t b : batchesToTest) {
            std::cout << ">>> Running Apple Metal GPU (Batch size: " << formatNumber(b) << ")...\n";
            auto r = benchmarkMetal(startIndex, count, targetDevice, b);
            if (r.success) {
                if (baselineCpu.dealsPerSec > 0) {
                    r.speedup = r.dealsPerSec / baselineCpu.dealsPerSec;
                }
                results.push_back(r);
                std::cout << "    Elapsed: " << std::fixed << std::setprecision(3) << r.elapsedSeconds << " s | "
                          << "Throughput: " << std::fixed << std::setprecision(0) << formatNumber((uint64_t)r.dealsPerSec) << " deals/s | "
                          << "Speedup: " << std::fixed << std::setprecision(2) << r.speedup << "x vs 1-thread CPU\n";
                if (r.fallbacks > 0) {
                    std::cout << "    Host CPU fallbacks (>10k cards): " << r.fallbacks << "\n";
                }
                std::cout << "\n";
            } else {
                std::cout << "    [Metal skipped: " << r.notes << "]\n\n";
            }
        }
    }
#endif

    // Summary Table
    if (!results.empty()) {
        std::cout << "====================================================================================================\n"
                  << "                                         BENCHMARK SUMMARY                                          \n"
                  << "====================================================================================================\n"
                  << std::left << std::setw(36) << "Backend"
                  << std::right << std::setw(8) << "Threads"
                  << std::setw(16) << "Deals/sec"
                  << std::setw(10) << "Speedup"
                  << std::setw(15) << "10^9 WU Est."
                  << std::setw(15) << "5x10^8 WU Est."
                  << "\n"
                  << "----------------------------------------------------------------------------------------------------\n";

        for (const auto& r : results) {
            double dps = r.dealsPerSec;
            double t1B = (dps > 0) ? (1e9 / dps) : 0.0;
            double t500M = (dps > 0) ? (5e8 / dps) : 0.0;

            std::cout << std::left << std::setw(36) << r.name.substr(0, 35)
                      << std::right << std::setw(8) << r.threads
                      << std::setw(16) << formatNumber((uint64_t)dps)
                      << std::setw(9) << std::fixed << std::setprecision(2) << r.speedup << "x"
                      << std::setw(15) << formatTime(t1B)
                      << std::setw(15) << formatTime(t500M)
                      << "\n";
        }
        std::cout << "====================================================================================================\n";
    }

    return 0;
}
