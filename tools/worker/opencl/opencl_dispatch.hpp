#ifndef CAMICIA_OPENCL_DISPATCH_HPP
#define CAMICIA_OPENCL_DISPATCH_HPP

#include <string>
#include <vector>
#include <cstdint>
#define CL_TARGET_OPENCL_VERSION 120
#if __has_include("CL/cl.h")
#include "CL/cl.h"
#elif __has_include("opencl/include/CL/cl.h")
#include "opencl/include/CL/cl.h"
#elif __has_include("include/CL/cl.h")
#include "include/CL/cl.h"
#endif
#include "engine.hpp"
#include "int128_io.hpp"

struct GpuDealOutcome {
    uint32_t status;
    uint32_t cards;
    uint32_t tricks;
    uint32_t pad;
    uint64_t index_hi;
    uint64_t index_lo;
};

struct GpuDeviceProfile {
    int platformIndex;
    int deviceIndex;
    std::string platformName;
    std::string deviceName;
    std::string deviceVendor;
    bool isGpu;
    uint32_t computeUnits;
    uint64_t globalMemBytes;
};

class OpenCLDispatcher {
public:
    OpenCLDispatcher();
    ~OpenCLDispatcher();

    // Dynamically loads OpenCL library and enumerates all devices
    bool initLoader(std::string& errorMsg);

    // List all detected OpenCL devices
    std::vector<GpuDeviceProfile> listDevices() const;

    // Initialize context, command queue, and compile kernel for a specific device index
    bool initDevice(int deviceIndex, const std::string& kernelSource, std::string& errorMsg);

    // Execute a batch of deals on GPU starting at baseIndex
    bool runBatch(int128 baseIndex, uint32_t batchSize, std::vector<GpuDealOutcome>& outcomes, std::string& errorMsg);

    void cleanup();

private:
    void* libHandle = nullptr;

    // OpenCL API Function Pointers (dynamic binding)
    cl_int (*pfn_clGetPlatformIDs)(cl_uint, cl_platform_id*, cl_uint*) = nullptr;
    cl_int (*pfn_clGetPlatformInfo)(cl_platform_id, cl_platform_info, size_t, void*, size_t*) = nullptr;
    cl_int (*pfn_clGetDeviceIDs)(cl_platform_id, cl_device_type, cl_uint, cl_device_id*, cl_uint*) = nullptr;
    cl_int (*pfn_clGetDeviceInfo)(cl_device_id, cl_device_info, size_t, void*, size_t*) = nullptr;
    cl_context (*pfn_clCreateContext)(const cl_context_properties*, cl_uint, const cl_device_id*, void (*)(const char*, const void*, size_t, void*), void*, cl_int*) = nullptr;
    cl_command_queue (*pfn_clCreateCommandQueue)(cl_context, cl_device_id, cl_command_queue_properties, cl_int*) = nullptr;
    cl_mem (*pfn_clCreateBuffer)(cl_context, cl_mem_flags, size_t, void*, cl_int*) = nullptr;
    cl_program (*pfn_clCreateProgramWithSource)(cl_context, cl_uint, const char**, const size_t*, cl_int*) = nullptr;
    cl_int (*pfn_clBuildProgram)(cl_program, cl_uint, const cl_device_id*, const char*, void (*)(cl_program, void*), void*) = nullptr;
    cl_int (*pfn_clGetProgramBuildInfo)(cl_program, cl_device_id, cl_program_build_info, size_t, void*, size_t*) = nullptr;
    cl_kernel (*pfn_clCreateKernel)(cl_program, const char*, cl_int*) = nullptr;
    cl_int (*pfn_clSetKernelArg)(cl_kernel, cl_uint, size_t, const void*) = nullptr;
    cl_int (*pfn_clEnqueueNDRangeKernel)(cl_command_queue, cl_kernel, cl_uint, const size_t*, const size_t*, const size_t*, cl_uint, const cl_event*, cl_event*) = nullptr;
    cl_int (*pfn_clEnqueueReadBuffer)(cl_command_queue, cl_mem, cl_bool, size_t, size_t, void*, cl_uint, const cl_event*, cl_event*) = nullptr;
    cl_int (*pfn_clReleaseKernel)(cl_kernel) = nullptr;
    cl_int (*pfn_clReleaseProgram)(cl_program) = nullptr;
    cl_int (*pfn_clReleaseMemObject)(cl_mem) = nullptr;
    cl_int (*pfn_clReleaseCommandQueue)(cl_command_queue) = nullptr;
    cl_int (*pfn_clReleaseContext)(cl_context) = nullptr;

    std::vector<cl_platform_id> platforms;
    std::vector<cl_device_id> allDevices;
    std::vector<GpuDeviceProfile> deviceProfiles;

    cl_device_id activeDevice = nullptr;
    cl_context context = nullptr;
    cl_command_queue queue = nullptr;
    cl_program program = nullptr;
    cl_kernel kernel = nullptr;
    cl_mem nCrBuffer = nullptr;
    cl_mem outBuffer = nullptr;
    size_t currentOutBufferSize = 0;
};

#endif
