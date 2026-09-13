#include "opencl_dispatch.hpp"
#include <iostream>
#include <fstream>
#include <sstream>
#include <cstring>

#if defined(_WIN32)
#include <windows.h>
#define DLSYM(h, sym) GetProcAddress((HMODULE)(h), sym)
#define DLCLOSE(h) FreeLibrary((HMODULE)(h))
#else
#include <dlfcn.h>
#define DLSYM(h, sym) dlsym(h, sym)
#define DLCLOSE(h) dlclose(h)
#endif

OpenCLDispatcher::OpenCLDispatcher() = default;

OpenCLDispatcher::~OpenCLDispatcher() {
    cleanup();
}

void OpenCLDispatcher::cleanup() {
    if (outBuffer && pfn_clReleaseMemObject) pfn_clReleaseMemObject(outBuffer);
    if (nCrBuffer && pfn_clReleaseMemObject) pfn_clReleaseMemObject(nCrBuffer);
    if (kernel && pfn_clReleaseKernel) pfn_clReleaseKernel(kernel);
    if (program && pfn_clReleaseProgram) pfn_clReleaseProgram(program);
    if (queue && pfn_clReleaseCommandQueue) pfn_clReleaseCommandQueue(queue);
    if (context && pfn_clReleaseContext) pfn_clReleaseContext(context);

    outBuffer = nullptr;
    nCrBuffer = nullptr;
    kernel = nullptr;
    program = nullptr;
    queue = nullptr;
    context = nullptr;
    currentOutBufferSize = 0;

    if (libHandle) {
        DLCLOSE(libHandle);
        libHandle = nullptr;
    }
}

bool OpenCLDispatcher::initLoader(std::string& errorMsg) {
    if (libHandle) return true;

#if defined(_WIN32)
    libHandle = (void*)LoadLibraryA("OpenCL.dll");
#else
    const char* candidates[] = {
        "libOpenCL.so.1",
        "libOpenCL.so",
        "/usr/lib/x86_64-linux-gnu/libOpenCL.so.1",
        "/usr/lib/wsl/lib/libOpenCL.so.1",
        nullptr
    };
    for (int i = 0; candidates[i]; ++i) {
        libHandle = dlopen(candidates[i], RTLD_NOW);
        if (libHandle) break;
    }
#endif

    if (!libHandle) {
        errorMsg = "OpenCL runtime library not found (libOpenCL.so.1 or OpenCL.dll).";
        return false;
    }

#define BIND_FN(name) do { \
    pfn_##name = (decltype(pfn_##name))DLSYM(libHandle, #name); \
    if (!pfn_##name) { \
        errorMsg = std::string("Failed to bind OpenCL symbol: ") + #name; \
        return false; \
    } \
} while (0)

    BIND_FN(clGetPlatformIDs);
    BIND_FN(clGetPlatformInfo);
    BIND_FN(clGetDeviceIDs);
    BIND_FN(clGetDeviceInfo);
    BIND_FN(clCreateContext);
    BIND_FN(clCreateCommandQueue);
    BIND_FN(clCreateBuffer);
    BIND_FN(clCreateProgramWithSource);
    BIND_FN(clBuildProgram);
    BIND_FN(clGetProgramBuildInfo);
    BIND_FN(clCreateKernel);
    BIND_FN(clSetKernelArg);
    BIND_FN(clEnqueueNDRangeKernel);
    BIND_FN(clEnqueueReadBuffer);
    BIND_FN(clReleaseKernel);
    BIND_FN(clReleaseProgram);
    BIND_FN(clReleaseMemObject);
    BIND_FN(clReleaseCommandQueue);
    BIND_FN(clReleaseContext);
#undef BIND_FN

    // Enumerate platforms & devices
    cl_uint numP = 0;
    cl_int err = pfn_clGetPlatformIDs(0, nullptr, &numP);
    if (err != CL_SUCCESS || numP == 0) {
        errorMsg = "No OpenCL platforms found on host.";
        return false;
    }

    platforms.resize(numP);
    pfn_clGetPlatformIDs(numP, platforms.data(), nullptr);

    allDevices.clear();
    deviceProfiles.clear();

    for (cl_uint p = 0; p < numP; ++p) {
        char pName[256] = {0};
        pfn_clGetPlatformInfo(platforms[p], CL_PLATFORM_NAME, sizeof(pName), pName, nullptr);

        cl_uint numD = 0;
        err = pfn_clGetDeviceIDs(platforms[p], CL_DEVICE_TYPE_ALL, 0, nullptr, &numD);
        if (err != CL_SUCCESS || numD == 0) continue;

        std::vector<cl_device_id> devs(numD);
        pfn_clGetDeviceIDs(platforms[p], CL_DEVICE_TYPE_ALL, numD, devs.data(), nullptr);

        for (cl_uint d = 0; d < numD; ++d) {
            char dName[256] = {0}, dVendor[256] = {0};
            cl_device_type dType = 0;
            cl_uint computeUnits = 0;
            cl_ulong globalMem = 0;

            pfn_clGetDeviceInfo(devs[d], CL_DEVICE_NAME, sizeof(dName), dName, nullptr);
            pfn_clGetDeviceInfo(devs[d], CL_DEVICE_VENDOR, sizeof(dVendor), dVendor, nullptr);
            pfn_clGetDeviceInfo(devs[d], CL_DEVICE_TYPE, sizeof(dType), &dType, nullptr);
            pfn_clGetDeviceInfo(devs[d], CL_DEVICE_MAX_COMPUTE_UNITS, sizeof(computeUnits), &computeUnits, nullptr);
            pfn_clGetDeviceInfo(devs[d], CL_DEVICE_GLOBAL_MEM_SIZE, sizeof(globalMem), &globalMem, nullptr);

            GpuDeviceProfile profile;
            profile.platformIndex = (int)p;
            profile.deviceIndex = (int)allDevices.size();
            profile.platformName = pName;
            profile.deviceName = dName;
            profile.deviceVendor = dVendor;
            profile.isGpu = (dType & CL_DEVICE_TYPE_GPU) != 0;
            profile.computeUnits = computeUnits;
            profile.globalMemBytes = globalMem;

            allDevices.push_back(devs[d]);
            deviceProfiles.push_back(profile);
        }
    }

    if (allDevices.empty()) {
        errorMsg = "OpenCL platform found, but no usable devices detected.";
        return false;
    }

    return true;
}

std::vector<GpuDeviceProfile> OpenCLDispatcher::listDevices() const {
    return deviceProfiles;
}

bool OpenCLDispatcher::initDevice(int deviceIndex, const std::string& kernelSource, std::string& errorMsg) {
    if (deviceIndex < 0 || deviceIndex >= (int)allDevices.size()) {
        errorMsg = "Invalid device index specified.";
        return false;
    }

    activeDevice = allDevices[deviceIndex];
    cl_int err = CL_SUCCESS;

    context = pfn_clCreateContext(nullptr, 1, &activeDevice, nullptr, nullptr, &err);
    if (err != CL_SUCCESS) {
        errorMsg = "Failed to create OpenCL context: code " + std::to_string(err);
        return false;
    }

    queue = pfn_clCreateCommandQueue(context, activeDevice, 0, &err);
    if (err != CL_SUCCESS) {
        errorMsg = "Failed to create OpenCL command queue: code " + std::to_string(err);
        return false;
    }

    // Build program
    const char* src = kernelSource.c_str();
    size_t len = kernelSource.size();
    program = pfn_clCreateProgramWithSource(context, 1, &src, &len, &err);
    if (err != CL_SUCCESS) {
        errorMsg = "Failed to create OpenCL program: code " + std::to_string(err);
        return false;
    }

    err = pfn_clBuildProgram(program, 1, &activeDevice, "-cl-fast-relaxed-math", nullptr, nullptr);
    if (err != CL_SUCCESS) {
        size_t logSize = 0;
        pfn_clGetProgramBuildInfo(program, activeDevice, CL_PROGRAM_BUILD_LOG, 0, nullptr, &logSize);
        std::string buildLog(logSize, '\0');
        pfn_clGetProgramBuildInfo(program, activeDevice, CL_PROGRAM_BUILD_LOG, logSize, &buildLog[0], nullptr);
        errorMsg = "OpenCL Kernel Build Failed:\n" + buildLog;
        return false;
    }

    kernel = pfn_clCreateKernel(program, "camicia_simulate_batch", &err);
    if (err != CL_SUCCESS) {
        errorMsg = "Failed to create OpenCL kernel 'camicia_simulate_batch': code " + std::to_string(err);
        return false;
    }

    // Precompute and upload nCr table (53x53 ulong entries)
    std::vector<uint64_t> hostTable(53 * 53, 0);
    for (int n = 0; n <= 52; ++n) {
        hostTable[n * 53 + 0] = 1;
        for (int r = 1; r <= n; ++r) {
            hostTable[n * 53 + r] = hostTable[(n - 1) * 53 + (r - 1)] + hostTable[(n - 1) * 53 + r];
        }
    }

    nCrBuffer = pfn_clCreateBuffer(context, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR,
                                   sizeof(uint64_t) * hostTable.size(), hostTable.data(), &err);
    if (err != CL_SUCCESS) {
        errorMsg = "Failed to allocate constant nCr table buffer: code " + std::to_string(err);
        return false;
    }

    return true;
}

bool OpenCLDispatcher::runBatch(int128 baseIndex, uint32_t batchSize, std::vector<GpuDealOutcome>& outcomes, std::string& errorMsg) {
    if (!kernel || !queue) {
        errorMsg = "OpenCL dispatcher not initialized.";
        return false;
    }

    cl_int err = CL_SUCCESS;
    size_t neededBytes = sizeof(GpuDealOutcome) * batchSize;

    if (!outBuffer || currentOutBufferSize < neededBytes) {
        if (outBuffer) pfn_clReleaseMemObject(outBuffer);
        outBuffer = pfn_clCreateBuffer(context, CL_MEM_WRITE_ONLY, neededBytes, nullptr, &err);
        if (err != CL_SUCCESS) {
            errorMsg = "Failed to allocate GPU output buffer: code " + std::to_string(err);
            return false;
        }
        currentOutBufferSize = neededBytes;
    }

    uint64_t base_hi = (uint64_t)(baseIndex >> 64);
    uint64_t base_lo = (uint64_t)(baseIndex & 0xFFFFFFFFFFFFFFFFUL);

    pfn_clSetKernelArg(kernel, 0, sizeof(uint64_t), &base_hi);
    pfn_clSetKernelArg(kernel, 1, sizeof(uint64_t), &base_lo);
    pfn_clSetKernelArg(kernel, 2, sizeof(uint32_t), &batchSize);
    pfn_clSetKernelArg(kernel, 3, sizeof(cl_mem), &nCrBuffer);
    pfn_clSetKernelArg(kernel, 4, sizeof(cl_mem), &outBuffer);

    size_t globalWorkSize = batchSize;
    err = pfn_clEnqueueNDRangeKernel(queue, kernel, 1, nullptr, &globalWorkSize, nullptr, 0, nullptr, nullptr);
    if (err != CL_SUCCESS) {
        errorMsg = "Kernel launch failed: code " + std::to_string(err);
        return false;
    }

    outcomes.resize(batchSize);
    err = pfn_clEnqueueReadBuffer(queue, outBuffer, CL_TRUE, 0, neededBytes, outcomes.data(), 0, nullptr, nullptr);
    if (err != CL_SUCCESS) {
        errorMsg = "Failed to read back GPU results: code " + std::to_string(err);
        return false;
    }

    return true;
}
