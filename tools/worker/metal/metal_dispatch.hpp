#pragma once

#include <string>
#include <vector>
#include <cstdint>
#include "permutation.hpp"

struct MetalDealOutcome {
    uint32_t status;
    uint32_t cards;
    uint32_t tricks;
    uint32_t pad;
    uint64_t index_hi;
    uint64_t index_lo;
};

struct MetalDeviceInfo {
    int index;
    std::string deviceName;
    bool isLowPower;
    bool isRemovable;
};

#if defined(__APPLE__)

class MetalDispatcher {
public:
    MetalDispatcher();
    ~MetalDispatcher();

    bool initDevice(int deviceIndex, const std::string& kernelSource, std::string& errorMsg);
    bool runBatch(int128 startIndex, uint32_t batchSize, std::vector<MetalDealOutcome>& outcomes, std::string& errorMsg);
    void cleanup();

    std::string getDeviceName() const;
    static std::vector<MetalDeviceInfo> listDevices();

private:
    void* impl; // Opaque pointer to Objective-C++ Metal state
};

#else

// Stubs for non-macOS compilation (Linux, Windows)
class MetalDispatcher {
public:
    MetalDispatcher() = default;
    ~MetalDispatcher() = default;

    inline bool initDevice(int, const std::string&, std::string& errorMsg) {
        errorMsg = "Metal is only supported on macOS.";
        return false;
    }

    inline bool runBatch(int128, uint32_t, std::vector<MetalDealOutcome>&, std::string& errorMsg) {
        errorMsg = "Metal is only supported on macOS.";
        return false;
    }

    inline void cleanup() {}
    inline std::string getDeviceName() const { return ""; }
    inline static std::vector<MetalDeviceInfo> listDevices() { return {}; }
};

#endif
