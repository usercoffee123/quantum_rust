# Simulator test and performance analysis

Run date: November 8, 2026. All project tests passed. The analysis includes **720 benchmark configurations** and **3,600 full-state Qiskit parity comparisons**. The expanded run exposed a norm-reduction error, which was fixed before the final tests and sweeps.

The main findings on this machine are:

- Four workers were best at 16 qubits. At 18–20 qubits, the best aggregate settings used 8–10 workers and delivered approximately 4.1–4.8× median speedup over Rust serial execution.
- At 20 qubits, FP32 used half the FP64 state memory with modest runtime gains. FP16 used half the FP32 state memory but took about 1.37–1.59× as long in matched workloads.
- Forcing four workers at 14 qubits helped these workloads, while forcing eight workers provided little benefit. The current threshold is conservative for four workers here; any tuning should account for worker count, precision, and target position.
- The one discovered failure was in norm reporting, not the gate kernels. Compensated summation fixed it without changing state amplitudes or weakening tolerances.

## Test results

| Check | Result |
| --- | --- |
| Rust debug, all targets | 19 tests passed; example target compiled |
| Rust release, all targets | Same 19 tests passed under optimization |
| Rust doc tests | Command passed; no executable doc tests are defined |
| Formatting | `cargo fmt --check` passed |
| Lints | Clippy, all targets, warnings denied: passed |
| Qiskit parity | 240 circuits × 3 precisions × 5 worker counts = 3,600 passes |
| Default-threshold performance sweep | 450 configurations passed amplitude-probe and norm checks |
| Forced-parallel performance sweep | 270 configurations passed amplitude-probe and norm checks |

Rust tests cover pair scheduling, complete amplitude coverage, all small controlled-gate bit orders, precision storage and rounding, FP16 underflow, validation, memory limits, CLI behavior, and exact serial/threaded agreement. Qiskit parity checks every amplitude and probability for its small circuits, including a 14-qubit large-stride case. The upstream Qiskit SDK test suite itself was not run.

## Failure found and corrected

The initial 20-qubit FP64 mixed workload failed the norm check by `1.42794665e-11` against a `1e-11` tolerance. The norm used a left-to-right FP64 sum of roughly one million positive terms. It now uses Kahan compensated summation. A regression test reproduces that circuit and requires norm error below `1e-13`; it passes in debug and release. Stored amplitudes and gate kernels are unchanged, and tolerances were not relaxed. All final results below come from reruns after the fix.

The harness also checkpoints completed measurements before each case and reuses one timed deterministic Qiskit reference per workload across Rust precision/worker variants. This avoids repeating the same reference measurement 15 times while retaining its five raw samples. Only the current reference state is retained. The first failed run is preserved in `benchmark-results/2026-11-08-full/initial-norm-failure.log`.

## Hardware and method

- Apple M4 (`Mac16,12`), 10 physical/logical CPU cores, 16 GiB RAM, macOS ARM64.
- `rustc 1.98.0 (88d9e12ae 2026-08-18)`; Python 3.14.6; NumPy 2.5.4; Qiskit 2.7.0.dev0+9ab2106.
- Qiskit submodule revision: `9ab2106352f603bf411e1387a758f6d9667c7c1c`.
- Rust source SHA-256: `ca94a66f5324bfbc058c53935a278e40b8fc7c31c0b7091bdc84a4bac38a5bcc`.
- Cargo lock SHA-256: `3fceadc768aa457d0fd6375d3c7a1b79d538c8f9e0d4acd3ee3dae7363d866cf`.
- Default sweep: 10, 14, 16, 18, 20 qubits; FP64/FP32/FP16; 1/2/4/8/10 workers; low/middle/high targets; rotation and mixed controlled-gate workloads.
- Forced sweep: 10, 14, 16 qubits with the same precision/worker/workload matrix and scheduling threshold zero.
- Each Rust configuration and each shared Qiskit reference baseline has one configured warmup and five timed repetitions. Rust also executes an untimed preflight and final verification run. The 32 workload gates are preceded by one H per qubit; total timed gates are therefore `32 + num_qubits`.
- The final sweeps contain 3,600 timed Rust repetitions and 240 timed Qiskit repetitions across 48 distinct reference workloads; reference samples are shared across equivalent Rust configurations.
- Gate execution includes validation, dispatch, synchronization, and internal scratch/output allocation. State initialization, worker-pool creation, circuit construction, IPC, and result serialization are excluded.
- Timed sweeps ran sequentially after tests, with no other test suite launched concurrently. CPU frequency, thermal state, affinity, and unrelated system activity were not controlled.
- Qiskit numerical-library thread environment limits were requested at one. Rust uses the configured workers, with serial fallback below 65,536 amplitudes. The effective native-library worker count was not independently profiled.

**The reference is `qiskit.quantum_info.Statevector`, not Qiskit Aer.** Qiskit always uses the FP64 reference path here. FP32/FP16 comparisons use different numerical precision, and multiworker Rust comparisons use different requested CPU resources. Rust-versus-Rust scaling is the appropriate thread-efficiency comparison.

## CPU scaling

Times below are milliseconds: the median of the six per-workload median runtimes for each state size, precision, and configured worker count. “Best workers” minimizes that aggregate runtime; speedup is the median of the six matched ratios against one Rust thread. Small differences between worker counts are not statistically established by five samples.

| Qubits | Mode | 1 worker ms | 2 workers ms | 4 workers ms | 8 workers ms | 10 workers ms | Best workers | Speedup |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | f64 | 0.034 | 0.038 | 0.038 | 0.038 | 0.038 | 1 | 1.00× |
| 10 | f32 | 0.033 | 0.037 | 0.037 | 0.037 | 0.037 | 1 | 1.00× |
| 10 | f16 | 0.052 | 0.059 | 0.059 | 0.059 | 0.059 | 1 | 1.00× |
| 14 | f64 | 0.609 | 0.619 | 0.615 | 0.615 | 0.611 | 1 | 1.00× |
| 14 | f32 | 0.593 | 0.579 | 0.583 | 0.572 | 0.579 | 8 | 1.02× |
| 14 | f16 | 0.929 | 0.919 | 0.976 | 0.918 | 0.917 | 10 | 1.01× |
| 16 | f64 | 2.682 | 1.336 | 0.829 | 0.868 | 1.212 | 4 | 3.25× |
| 16 | f32 | 2.705 | 1.344 | 0.770 | 0.865 | 1.321 | 4 | 3.47× |
| 16 | f16 | 4.058 | 2.177 | 1.188 | 1.272 | 1.659 | 4 | 3.55× |
| 18 | f64 | 11.272 | 5.668 | 3.363 | 2.566 | 3.007 | 8 | 4.07× |
| 18 | f32 | 11.188 | 5.643 | 3.133 | 2.600 | 2.410 | 10 | 4.48× |
| 18 | f16 | 17.051 | 9.071 | 5.133 | 4.383 | 4.258 | 10 | 4.07× |
| 20 | f64 | 47.035 | 23.759 | 13.775 | 10.819 | 10.365 | 10 | 4.48× |
| 20 | f32 | 45.294 | 23.372 | 13.308 | 9.811 | 9.936 | 8 | 4.48× |
| 20 | f16 | 71.297 | 37.688 | 20.528 | 14.752 | 14.489 | 10 | 4.82× |

For 10 and 14 qubits, the default configuration runs serially regardless of pool size. Apparent differences between configured worker counts in those rows reflect measurement variation rather than parallel speedup. At 16 qubits and above, multiple workers take the parallel path.

## Scheduling threshold

The following ratios are **forced-parallel runtime / default runtime**, computed from matched workloads. Above 1 means forcing parallel execution was slower. Both runs are independent measurements, so ratios near 1 should be treated cautiously.

| Qubits | Mode | 4-worker forced/default | 8-worker forced/default |
| --- | --- | --- | --- |
| 10 | f64 | 1.43× | 1.86× |
| 10 | f32 | 1.32× | 1.65× |
| 10 | f16 | 1.24× | 1.62× |
| 14 | f64 | 0.55× | 1.09× |
| 14 | f32 | 0.47× | 1.03× |
| 14 | f16 | 0.46× | 0.96× |

The 16-qubit cases are a control: both default and forced settings already use parallel execution with multiple workers. This experiment assesses the current threshold; it does not search every possible cutoff or establish a universal optimum.

## Precision and memory

20-qubit execution times, aggregated across the same six workload medians:

| Workers | FP64 ms | FP32 ms | FP16 ms | FP16 / FP32 runtime |
| --- | --- | --- | --- | --- |
| 1 | 47.035 | 45.294 | 71.297 | 1.58× |
| 2 | 23.759 | 23.372 | 37.688 | 1.59× |
| 4 | 13.775 | 13.308 | 20.528 | 1.57× |
| 8 | 10.819 | 9.811 | 14.752 | 1.50× |
| 10 | 10.365 | 9.936 | 14.489 | 1.37× |

State-buffer payload only:

| Qubits | FP64 | FP32 | FP16 |
| --- | --- | --- | --- |
| 10 | 0.015625 MiB | 0.0078125 MiB | 0.00390625 MiB |
| 14 | 0.25 MiB | 0.125 MiB | 0.0625 MiB |
| 16 | 1 MiB | 0.5 MiB | 0.25 MiB |
| 18 | 4 MiB | 2 MiB | 1 MiB |
| 20 | 16 MiB | 8 MiB | 4 MiB |

For the 20-qubit rotation workload, using the worker count with the lowest median across its three target positions:

| Mode | Workers | Mean cost per timed gate (µs) | Gates/s | Logical read/write GB/s |
| --- | --- | --- | --- | --- |
| f64 | 10 | 201.8 | 4,957 | 166.3 |
| f32 | 8 | 200.3 | 4,992 | 83.8 |
| f16 | 8 | 297.3 | 3,363 | 28.2 |

The per-gate figure divides full workload time by its 52 gates; it is not an isolated gate microbenchmark. Logical bandwidth assumes one state read and write per gate for these generic matrix kernels. It can include cache traffic and is not measured DRAM bandwidth.


FP16 has one-quarter of FP64 state storage and one-half of FP32 storage. Gate arithmetic is FP32 for FP16/FP32, and FP64 for FP64. Norms and probabilities use FP64 calculations/reductions in all modes. These payload numbers do not measure process peak RSS, Qiskit scratch memory, JSON buffers, or thread stacks.

## Numerical accuracy

Worst full-state parity metrics below were the same at all tested worker counts. Fidelity and probability TVD use normalized copies for comparison; simulator amplitudes are not automatically renormalized.

| Mode | Max amplitude error | Max norm drift | Min normalized fidelity | Max normalized TVD |
| --- | --- | --- | --- | --- |
| f64 | 9.24e-15 | 6.88e-15 | 1.000000000 | 5.66e-15 |
| f32 | 2.21e-06 | 4.42e-06 | 1.000000000 | 2.18e-07 |
| f16 | 0.00174 | 0.00403 | 0.999995051 | 0.00142 |

Across the performance sweeps, which check selected amplitudes and the full norm:

| Mode | Max probe error | Max norm drift |
| --- | --- | --- |
| f64 | 4.93e-17 | 8.88e-16 |
| f32 | 5.92e-09 | 2.85e-07 |
| f16 | 7.05e-05 | 0.000694 |

These are workload-specific regression results, not guarantees for arbitrary depth or state size. Large benchmark states are checked through probes rather than full-vector comparisons. FP16 can lose small amplitudes and accumulates quantization error; the tests do not establish FP16 reliability for 30+ qubits or arbitrary deep circuits.

## Interpreting the Qiskit ratios

The serial FP64 comparison is the closest precision/resource match in this harness:

| Qubits | Qiskit median workload ms | Median Qiskit / Rust serial ratio |
| --- | --- | --- |
| 18 | 19.675 | 1.83× |
| 20 | 717.248 | 15.60× |

The reference latency changes sharply between these state sizes. This affects the reported ratios and should be profiled before attributing it to allocation, array layout, caches, or a specific numerical library. It is not evidence of a similar advantage over Aer.

## Measurement limits and next actions

- 39 of 450 default-sweep configurations had a Rust sample range greater than 25% of its median. Raw min/max and all samples are retained. Prefer broad scaling trends over rankings separated by a few percent.
- Keep one-thread execution as the correctness and scaling reference. Select a worker count from measurements at the intended state size rather than assuming all 10 cores are best.
- Treat FP32 primarily as a storage/accuracy tradeoff until its measured runtime advantage is consistent. FP16 storage savings are real, but conversion and writeback costs can outweigh those savings; profiling is needed to separate causes.
- Profile high-target, diagonal, and controlled-gate workloads. Specialized diagonal kernels and fewer full-state passes through fusion are concrete next optimization candidates; this run did not measure their benefit.
- Add a separately pinned Qiskit Aer baseline before making optimized-simulator performance claims. Add broader random/deep end-to-end workloads before generalizing beyond these local gate sequences.

## Reproduce and inspect

```sh
cargo fmt --check
cargo test --locked --all-targets
cargo test --locked --doc
cargo test --locked --release --all-targets
cargo clippy --locked --all-targets -- -D warnings
.venv/bin/python tools/compare_qiskit.py parity --precisions f64 f32 f16 --threads 1 2 4 8 10
.venv/bin/python tools/compare_qiskit.py bench --precisions f64 f32 f16 --threads 1 2 4 8 10 --qubits 10 14 16 18 20 --depth 32 --repetitions 5 --warmup 1 --output benchmark-results/2026-11-08-full/default-threshold.json
.venv/bin/python tools/compare_qiskit.py bench --precisions f64 f32 f16 --threads 1 2 4 8 10 --qubits 10 14 16 --depth 32 --repetitions 5 --warmup 1 --parallel-threshold 0 --output benchmark-results/2026-11-08-full/forced-parallel.json
```

This run used `PYTHONPYCACHEPREFIX=/private/tmp/qiskit-rust-analysis-pycache` to isolate generated Python bytecode. Import/cache setup is outside the timed region. Raw artifacts are local and ignored by Git:

- [Default-threshold raw timings](benchmark-results/2026-11-08-full/default-threshold.json)
- [Forced-parallel raw timings](benchmark-results/2026-11-08-full/forced-parallel.json)
- [Full parity output](benchmark-results/2026-11-08-full/parity.log)
- [Rust debug output](benchmark-results/2026-11-08-full/rust-debug.log)
- [Rust release output](benchmark-results/2026-11-08-full/rust-release.log)
- [Clippy output](benchmark-results/2026-11-08-full/clippy.log)

A null `qiskit_dirty` value in a raw report means the optional source-status scan was unavailable; it does not certify a clean checkout. The submodule commit and source hashes are still recorded.
