# Benchmark agent progress

## 2026-11-20 18:03:04 EST

- Added the `./compare --suite --qubits N` preset for Rust and Qiskit Aer in FP32 and FP64. The preset requests four workers and five circuit families by default.
- Kept the live memory headroom check and budgeted four FP64 state buffers for isolated Aer probe-only cases. Python Statevector and full-state validation retain an eight-buffer estimate.
- All 10 runtime comparison tests passed. A six-qubit smoke run passed all 10 cases with full-state validation and saved JSON, CSV, a Markdown report, and exact requests.
- A 26-qubit dry run reported insufficient live memory headroom. It requires 4 GiB of estimated working memory plus the default 1 GiB reserve.
- Reviewing the command paths, compatibility with existing options, and the artifacts passed to the profiler.

## 2026-11-20 18:03:35 EST

- Verified that the existing comparison command still plans its FP64 cases at one and four workers.
- Verified family overrides and both fusion settings in a 25-qubit suite dry run. All eight requested combinations fit the current memory estimate.
- Redisplayed the completed six-qubit run with `./compare --report` and verified the generated requests are available for profiling.
- The worker count is a request. Rust applies its existing parallel threshold of 65,536 amplitudes, so the six-qubit timing smoke verifies workflow and accuracy.
- Benchmark implementation and review complete. `git diff --check` passed.
