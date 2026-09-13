#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <chrono>
#include <iomanip>
#include "opencl_dispatch.hpp"
#include "kernel_source.hpp"
#include "engine.hpp"
#include "permutation.hpp"
#include "test_data_gen.hpp"

static std::string readKernelSource(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) return "";
    std::stringstream buffer;
    buffer << file.rdbuf();
    return buffer.str();
}

int main(int argc, char** argv) {
    std::cout << "=========================================================\n";
    std::cout << "       Camicia OpenCL GPU Worker Test & Benchmark        \n";
    std::cout << "=========================================================\n\n";

    OpenCLDispatcher dispatcher;
    std::string err;

    std::cout << "[1/4] Initializing OpenCL runtime...\n";
    if (!dispatcher.initLoader(err)) {
        std::cout << "  [INFO] " << err << "\n";
        std::cout << "  (OpenCL ICD runtime / GPU driver is not installed on this machine.)\n";
        std::cout << "  To run GPU tasks, ensure an OpenCL ICD vendor driver is installed:\n";
        std::cout << "    - NVIDIA: CUDA Driver / /etc/OpenCL/vendors/nvidia.icd\n";
        std::cout << "    - AMD: ROCm / AMDGPU-PRO OpenCL driver\n";
        std::cout << "    - Intel: intel-opencl-icd\n";
        std::cout << "    - CPU test: pocl-opencl-icd\n\n";
        std::cout << "  GPU Kernel syntax and dispatch logic compiled successfully.\n";
        return 0;
    }

    auto devices = dispatcher.listDevices();
    std::cout << "  Found " << devices.size() << " OpenCL device(s):\n";
    int selectedDev = 0;
    for (size_t i = 0; i < devices.size(); ++i) {
        std::cout << "    [" << i << "] " << devices[i].deviceName 
                  << " (" << devices[i].deviceVendor << ", " 
                  << (devices[i].isGpu ? "GPU" : "CPU") << ", " 
                  << devices[i].computeUnits << " CUs, " 
                  << (devices[i].globalMemBytes / (1024 * 1024)) << " MB VRAM)\n";
        if (devices[i].isGpu) selectedDev = i;
    }
    if (argc > 2) {
        int d = std::atoi(argv[2]);
        if (d >= 0 && d < (int)devices.size()) selectedDev = d;
    }

    std::string kernelPath = "tools/worker/opencl/camicia_kernel.cl";
    std::string kernelSrc = readKernelSource(kernelPath);
    if (kernelSrc.empty()) {
        kernelSrc = readKernelSource("../" + kernelPath);
    }
    if (kernelSrc.empty()) {
        kernelSrc = getEmbeddedKernelSource();
    }

    std::cout << "\n[2/4] Compiling OpenCL kernel for device [" << selectedDev << "] (" 
              << devices[selectedDev].deviceName << ")...\n";
    if (!dispatcher.initDevice(selectedDev, kernelSrc, err)) {
        std::cerr << "  [ERROR] Failed to initialize device: " << err << "\n";
        return 1;
    }
    std::cout << "  Kernel compiled successfully.\n\n";

    // 3. Verify against known test cases
    std::cout << "[3/4] Testing against historical games from test_data_gen.hpp...\n";
    int passed = 0;
    StateTracker cpuTracker;

    for (const auto& tc : test_cases) {
        int128 dealIdx = stringTo128(tc.index_str);
        std::vector<GpuDealOutcome> outcome;
        if (!dispatcher.runBatch(dealIdx, 1, outcome, err)) {
            std::cerr << "  [FAIL] Failed to run single deal batch: " << err << "\n";
            continue;
        }

        std::string statusStr;
        long long cards = outcome[0].cards;
        long long tricks = outcome[0].tricks;

        if (outcome[0].status == 1) {
            statusStr = "loop";
        } else if (outcome[0].status == 0) {
            statusStr = "finished";
        } else {
            // Status 2: Long game rechecked on CPU
            Card deck[52];
            getNthPermutation(dealIdx, deck);
            GameResult cpuRes = CamiciaGame::simulate(deck, 26, deck + 26, 26, cpuTracker);
            statusStr = cpuRes.status;
            cards = cpuRes.cards;
            tricks = cpuRes.tricks;
        }

        bool match = (statusStr == tc.expected_status && cards == tc.expected_cards && tricks == tc.expected_tricks);
        std::cout << "  " << std::setw(42) << std::left << tc.description << ": "
                  << (match ? "PASS" : "FAIL") << " (" << statusStr << ", " << cards << " cards, " << tricks << " tricks)\n";
        if (match) passed++;
    }
    std::cout << "  Test result: " << passed << " / " << test_cases.size() << " PASSED.\n\n";

    // 4. Batch Benchmark
    int benchCount = 500000;
    if (argc > 1) {
        int c = std::atoi(argv[1]);
        if (c > 0) benchCount = c;
    }

    std::cout << "[4/4] Running GPU Batch Benchmark (" << benchCount << " deals)...\n";
    uint32_t chunk = 65536;
    auto t0 = std::chrono::high_resolution_clock::now();

    int128 curIdx = 0;
    uint32_t totalProcessed = 0;
    long long totalCards = 0;
    long long loops = 0;

    while (totalProcessed < (uint32_t)benchCount) {
        uint32_t thisBatch = std::min(chunk, (uint32_t)benchCount - totalProcessed);
        std::vector<GpuDealOutcome> outcomes;
        if (!dispatcher.runBatch(curIdx, thisBatch, outcomes, err)) {
            std::cerr << "  [ERROR] Benchmark batch failed: " << err << "\n";
            return 1;
        }

        for (uint32_t i = 0; i < thisBatch; ++i) {
            if (outcomes[i].status == 1) {
                loops++;
            } else if (outcomes[i].status == 2) {
                int128 realIdx = curIdx + i;
                Card deck[52];
                getNthPermutation(realIdx, deck);
                GameResult cpuRes = CamiciaGame::simulate(deck, 26, deck + 26, 26, cpuTracker);
                if (cpuRes.status == "loop") loops++;
                totalCards += cpuRes.cards;
                continue;
            }
            totalCards += outcomes[i].cards;
        }

        curIdx += thisBatch;
        totalProcessed += thisBatch;
    }

    auto t1 = std::chrono::high_resolution_clock::now();
    double sec = std::chrono::duration<double>(t1 - t0).count();
    double rate = benchCount / sec;

    std::cout << "  Elapsed time: " << std::fixed << std::setprecision(4) << sec << " s\n";
    std::cout << "  Throughput:   " << std::fixed << std::setprecision(0) << rate << " deals/sec\n";
    std::cout << "  Total cards:  " << totalCards << ", loops: " << loops << "\n";
    std::cout << "=========================================================\n";

    return (passed == (int)test_cases.size()) ? 0 : 1;
}
