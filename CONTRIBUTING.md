# Contributing to Camicia

Thank you for your interest in contributing to **Camicia**! 

Camicia is a [BOINC](https://boinc.berkeley.edu/) distributed-computing project exhaustively searching every deal of Beggar-My-Neighbour for games that loop forever, and for the longest game that ever ends.

---

## 🔒 Security & Workflow Policy

Our staging and production deployment runners execute directly on dedicated server infrastructure. To protect project infrastructure and maintain system security:

1. **Pull Requests from external forks do NOT automatically trigger deployment or CI runs.**
2. Untrusted code is never executed on our self-hosted runners.
3. Every external pull request is reviewed manually by project maintainers before being merged into the `staging` branch for staging verification.

---

## 🛠️ Development & Branching Model

- **`staging`**: The active integration branch. All feature branches and external pull requests should target `staging`.
- **`main`**: The production branch. Only tested, verified code from `staging` is promoted to `main`.
- **Feature Branches**: Create a dedicated branch for your work:
  ```bash
  git checkout -b feature/your-feature-name staging
  ```

---

## 🧪 Local Testing Guide

Camicia requires a C++17-compliant compiler (such as **GCC 9+**, **Clang 10+**, or **MinGW-w64** on Windows).

Before submitting a pull request, verify that all test suites pass locally.

### 1. Core Permutation & Math Tests
Validates the Lehmer code and 128-bit bijection between permutation indices and 40-card decks:
```bash
g++ -std=c++17 -O2 tests/test_permutation.cpp tools/worker/core/permutation.cpp \
  -o test_permutation -I tools/worker/core
./test_permutation
```

### 2. Game Engine Rules & Cycle Tests
Tests the card game state machine, trick resolution, and cycle detection:
```bash
g++ -std=c++17 -O2 -DCAMICIA_TESTING tests/test_engine.cpp \
  tools/worker/core/permutation.cpp tools/worker/core/engine.cpp \
  -o test_engine -I tools/worker/core
./test_engine
```

### 3. Engine Property-Based Tests
Verifies invariants such as deterministic replay, trick preservation, and game state transitions:
```bash
g++ -std=c++17 -O2 -DCAMICIA_TESTING tests/test_engine_properties.cpp \
  tools/worker/core/permutation.cpp tools/worker/core/engine.cpp \
  -o test_engine_properties -I tools/worker/core
./test_engine_properties
```

### 4. Small-Deck Mathematical Verification
Verifies game engine outcomes against a brute-force state tree on a miniature deck:
```bash
g++ -std=c++17 -O2 tests/test_engine_small_deck.cpp \
  tools/worker/core/engine.cpp \
  -o test_engine_small_deck -I tools/worker/core
./test_engine_small_deck
```

### 5. Standalone Worker Execution (Smoke Test)
You can build and test the worker application locally **without needing any BOINC libraries** by using the `-DCAMICIA_STANDALONE` flag:

```bash
# Linux (x86_64)
g++ -std=c++17 -O3 -DCAMICIA_STANDALONE \
  tools/worker/worker.cpp tools/worker/core/*.cpp tools/worker/opencl/*.cpp \
  -o worker_app \
  -I tools/worker -I tools/worker/core -I tools/worker/opencl -I tools/worker/opencl/include \
  -pthread -ldl

# macOS (Apple Silicon / Clang)
clang++ -std=c++17 -O3 -DCAMICIA_STANDALONE \
  tools/worker/worker.cpp tools/worker/core/*.cpp tools/worker/opencl/*.cpp tools/worker/metal/*.mm \
  -o worker_app \
  -I tools/worker -I tools/worker/core -I tools/worker/opencl -I tools/worker/opencl/include -I tools/worker/metal \
  -framework Metal -framework Foundation -pthread

# Windows (MinGW-w64)
g++ -std=c++17 -O3 -DCAMICIA_STANDALONE \
  tools/worker/worker.cpp tools/worker/core/*.cpp tools/worker/opencl/*.cpp \
  -o worker_app.exe \
  -I tools/worker -I tools/worker/core -I tools/worker/opencl -I tools/worker/opencl/include \
  -pthread
```

Run a sample deal range (`0` to `1000`):
```bash
echo "0 1000" > in
./worker_app
cat out
```

---

## 🎯 Code Guidelines & Best Practices

1. **Strict Determinism**:
   - In volunteer computing, deterministic results are critical. Given the same deal index range, the worker application must produce **byte-for-byte identical output** regardless of operating system (Linux, Windows, macOS) or compute backend (CPU, OpenCL GPU, Apple Metal).
2. **Minimal Dependencies in Core Engine**:
   - Code inside `tools/worker/core/` must remain pure, dependency-free C++17 to ensure effortless cross-compilation across x86_64, aarch64 (ARM64), Windows, and macOS.
3. **No Secrets in Commits**:
   - Never commit `.env` files, private keys (`keys/`), passwords, or build artifacts.

---

## 📤 Submitting a Pull Request

1. Push your branch to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```
2. Open a Pull Request on GitHub against the **`staging`** branch.
3. Include a clear summary of your changes, the rationale behind them, and confirmation that all local tests pass.
4. Maintainers will review the PR, test it in an isolated staging environment, and coordinate merging.

---

## 📜 Code of Conduct

All contributors and participants are expected to adhere to our [Code of Conduct](CODE_OF_CONDUCT.md). Please report any unacceptable behavior to [moderators@camicia.dev](mailto:moderators@camicia.dev).

Thank you for helping push the frontiers of distributed computing! 🃏
