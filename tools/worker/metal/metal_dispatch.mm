#if defined(__APPLE__)

#import <Metal/Metal.h>
#import <Foundation/Foundation.h>
#include "metal_dispatch.hpp"
#include <cstring>
#include <iostream>

struct MetalSimParams {
    uint64_t base_hi;
    uint64_t base_lo;
    uint32_t total_deals;
    uint32_t pad;
};

struct MetalImpl {
    id<MTLDevice> device = nil;
    id<MTLCommandQueue> commandQueue = nil;
    id<MTLComputePipelineState> pipelineState = nil;
    id<MTLBuffer> nCrBuffer = nil;
    id<MTLBuffer> outBuffer = nil;
    uint32_t currentOutBufferSize = 0;
    std::string deviceName;
};

MetalDispatcher::MetalDispatcher() : impl(nullptr) {}

MetalDispatcher::~MetalDispatcher() {
    cleanup();
}

void MetalDispatcher::cleanup() {
    if (impl) {
        auto* m = (MetalImpl*)impl;
        delete m;
        impl = nullptr;
    }
}

std::string MetalDispatcher::getDeviceName() const {
    if (impl) {
        return ((MetalImpl*)impl)->deviceName;
    }
    return "";
}

std::vector<MetalDeviceInfo> MetalDispatcher::listDevices() {
    std::vector<MetalDeviceInfo> result;
    @autoreleasepool {
        NSArray<id<MTLDevice>>* devices = MTLCopyAllDevices();
        if (devices && devices.count > 0) {
            for (NSUInteger i = 0; i < devices.count; ++i) {
                id<MTLDevice> dev = devices[i];
                MetalDeviceInfo info;
                info.index = (int)i;
                info.deviceName = [dev.name UTF8String];
                info.isLowPower = dev.isLowPower;
                info.isRemovable = dev.isRemovable;
                result.push_back(info);
            }
        } else {
            id<MTLDevice> defaultDev = MTLCreateSystemDefaultDevice();
            if (defaultDev) {
                MetalDeviceInfo info;
                info.index = 0;
                info.deviceName = [defaultDev.name UTF8String];
                info.isLowPower = defaultDev.isLowPower;
                info.isRemovable = defaultDev.isRemovable;
                result.push_back(info);
            }
        }
    }
    return result;
}

bool MetalDispatcher::initDevice(int deviceIndex, const std::string& kernelSource, std::string& errorMsg) {
    cleanup();

    @autoreleasepool {
        id<MTLDevice> selectedDevice = nil;
        NSArray<id<MTLDevice>>* devices = MTLCopyAllDevices();
        if (devices && deviceIndex >= 0 && deviceIndex < (int)devices.count) {
            selectedDevice = devices[deviceIndex];
        } else {
            selectedDevice = MTLCreateSystemDefaultDevice();
        }

        if (!selectedDevice) {
            errorMsg = "No Metal-compatible GPU device found (MTLCreateSystemDefaultDevice returned nil).";
            return false;
        }

        auto* m = new MetalImpl();
        m->device = selectedDevice;
        m->deviceName = [selectedDevice.name UTF8String];

        m->commandQueue = [selectedDevice newCommandQueue];
        if (!m->commandQueue) {
            errorMsg = "Failed to create Metal command queue.";
            delete m;
            return false;
        }

        NSError* error = nil;
        NSString* sourceStr = [NSString stringWithUTF8String:kernelSource.c_str()];
        id<MTLLibrary> library = [selectedDevice newLibraryWithSource:sourceStr options:nil error:&error];
        if (!library) {
            errorMsg = "Metal shader compilation failed: ";
            if (error) {
                errorMsg += [error.localizedDescription UTF8String];
            }
            delete m;
            return false;
        }

        id<MTLFunction> kernelFunc = [library newFunctionWithName:@"camicia_simulate_batch"];
        if (!kernelFunc) {
            errorMsg = "Metal kernel function 'camicia_simulate_batch' not found in library.";
            delete m;
            return false;
        }

        m->pipelineState = [selectedDevice newComputePipelineStateWithFunction:kernelFunc error:&error];
        if (!m->pipelineState) {
            errorMsg = "Failed to create Metal compute pipeline state: ";
            if (error) {
                errorMsg += [error.localizedDescription UTF8String];
            }
            delete m;
            return false;
        }

        uint64_t nCr_table[53 * 53];
        for (int n = 0; n <= 52; ++n) {
            for (int r = 0; r <= 52; ++r) {
                nCr_table[n * 53 + r] = (uint64_t)nCr(n, r);
            }
        }
        m->nCrBuffer = [selectedDevice newBufferWithBytes:nCr_table length:sizeof(nCr_table) options:MTLResourceStorageModeShared];
        if (!m->nCrBuffer) {
            errorMsg = "Failed to allocate Metal buffer for nCr table.";
            delete m;
            return false;
        }

        impl = m;
        return true;
    }
}

bool MetalDispatcher::runBatch(int128 startIndex, uint32_t batchSize, std::vector<MetalDealOutcome>& outcomes, std::string& errorMsg) {
    if (!impl) {
        errorMsg = "Metal dispatcher not initialized.";
        return false;
    }

    auto* m = (MetalImpl*)impl;

    @autoreleasepool {
        if (!m->outBuffer || m->currentOutBufferSize < batchSize) {
            m->outBuffer = [m->device newBufferWithLength:batchSize * sizeof(MetalDealOutcome) options:MTLResourceStorageModeShared];
            if (!m->outBuffer) {
                errorMsg = "Failed to allocate Metal output buffer.";
                return false;
            }
            m->currentOutBufferSize = batchSize;
        }

        MetalSimParams params;
        params.base_hi = (uint64_t)(startIndex >> 64);
        params.base_lo = (uint64_t)startIndex;
        params.total_deals = batchSize;
        params.pad = 0;

        id<MTLCommandBuffer> commandBuffer = [m->commandQueue commandBuffer];
        if (!commandBuffer) {
            errorMsg = "Failed to create Metal command buffer.";
            return false;
        }

        id<MTLComputeCommandEncoder> encoder = [commandBuffer computeCommandEncoder];
        if (!encoder) {
            errorMsg = "Failed to create Metal compute command encoder.";
            return false;
        }

        [encoder setComputePipelineState:m->pipelineState];
        [encoder setBytes:&params length:sizeof(MetalSimParams) atIndex:0];
        [encoder setBuffer:m->nCrBuffer offset:0 atIndex:1];
        [encoder setBuffer:m->outBuffer offset:0 atIndex:2];

        NSUInteger w = m->pipelineState.threadExecutionWidth;
        if (w == 0) w = 32;
        NSUInteger maxThreads = m->pipelineState.maxTotalThreadsPerThreadgroup;
        NSUInteger threadsPerGroup = ((maxThreads / w) * w);
        if (threadsPerGroup > 256) threadsPerGroup = 256;
        if (threadsPerGroup == 0) threadsPerGroup = 32;

        MTLSize threadsPerThreadgroup = MTLSizeMake(threadsPerGroup, 1, 1);
        MTLSize threadgroups = MTLSizeMake((batchSize + threadsPerGroup - 1) / threadsPerGroup, 1, 1);

        [encoder dispatchThreadgroups:threadgroups threadsPerThreadgroup:threadsPerThreadgroup];
        [encoder endEncoding];

        [commandBuffer commit];
        [commandBuffer waitUntilCompleted];

        if (commandBuffer.status == MTLCommandBufferStatusError) {
            errorMsg = "Metal command buffer execution error: ";
            if (commandBuffer.error) {
                errorMsg += [commandBuffer.error.localizedDescription UTF8String];
            }
            return false;
        }

        outcomes.resize(batchSize);
        std::memcpy(outcomes.data(), m->outBuffer.contents, batchSize * sizeof(MetalDealOutcome));

        return true;
    }
}

#endif
