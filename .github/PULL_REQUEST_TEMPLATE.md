## Description
<!-- Provide a brief description of the changes introduced by this pull request. -->

## Target Branch
- [ ] This PR targets the **`staging`** branch (not `main`).

## Type of Change
<!-- Please check the options that apply: -->
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] Performance optimization (engine, worker, or GPU kernel speedup)
- [ ] New feature or improvement
- [ ] Documentation update
- [ ] CI/CD or build tooling update

## Verification & Testing
<!-- As documented in CONTRIBUTING.md, external PRs do not automatically trigger CI on our self-hosted runners. Please confirm local test execution: -->
- [ ] I have compiled and tested these changes locally.
- [ ] `test_permutation`: Validates 128-bit bijection and Lehmer code.
- [ ] `test_engine`: Validates game engine rules and cycle detection.
- [ ] `test_engine_properties`: Validates invariants and deterministic replay.
- [ ] `test_engine_small_deck`: Validates exhaustive tree match on miniature decks.
- [ ] Standalone worker smoke test (`-DCAMICIA_STANDALONE`): Produced valid output.

## Determinism & Security
- [ ] **Strict Determinism**: Engine/worker changes produce bit-for-bit identical results across CPU, OpenCL, and Apple Metal backends.
- [ ] **No Secrets**: No private keys (`keys/`), passwords, or `.env` files are included.
- [ ] All comments and documentation are aligned with these changes.
