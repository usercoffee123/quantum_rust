# Rust simulator reconstruction and fresh performance results

The subsequent [optimization pass](OPTIMIZATION_RESULTS.md) improves the controlled
gate and structured rotation kernels and adds a fresh comparison with optimized Aer.
The measurements below preserve the initial reconstructed implementation.

November 17, 2026. The missing Rust simulator is implemented and runnable. All 36
surviving integration tests pass in debug and release builds, and 2,880 full-state
Qiskit comparisons pass. Rust was faster in all 16 configurations of the new FP64
benchmark across two circuit families, two sizes, two worker counts and fusion
on/off. These are fresh measurements of reconstructed source, separate from the
historical reports.

## Implemented code

| File | Responsibility |
| --- | --- |
| [src/lib.rs](src/lib.rs) | Gate API, state ownership, checked allocation, validation, norms and probabilities |
| [src/precision.rs](src/precision.rs) | FP64, FP32 and FP16 storage and arithmetic policy |
| [src/execution.rs](src/execution.rs) | Reusable worker pool, safe pair traversal and specialized gates |
| [src/circuit.rs](src/circuit.rs) | Exact CX compression and compilation statistics |
| [src/fusion.rs](src/fusion.rs) | Dense fusion and structured Ry/Rz quartet kernel |
| [src/bin/statevector.rs](src/bin/statevector.rs) | JSON lines CLI, benchmark timers, probes and binary export |

The engine uses safe Rust with no state-sized scratch buffer for gate application.
FP16 stores four bytes per complex amplitude, computes in FP32 and rounds on
writeback. Unfused threaded execution preserves the serial arithmetic order.
Fusion composes in FP64 and avoids intermediate storage roundings. Validation
precedes mutation, including selected-precision coefficient checks and original
gates that might otherwise disappear during compression.

The implementation restores the documented 256 MiB default state limit, explicit
memory overrides, a 65,536 amplitude parallel threshold, independent optimization
and fusion flags, full-state or probe output, optional norm calculation and binary
FP64 little-endian export. It retains finite nonunitary matrix support.

## Validation

| Check | Result |
| --- | --- |
| Rust debug integration tests | 36 passed |
| Rust release integration tests | 36 passed |
| Clippy with warnings denied | Passed for all targets |
| Rust formatting | Passed |
| Existing Python tooling tests | 20 passed |
| Qiskit parity | 2,880 full-state comparisons passed |
| Benchmark correctness | Full states checked for every one of 16 configurations; timed probes checked separately |
| Release Bell example | Correct nonzero amplitudes at indices 0 and 3 |

Parity uses the surviving 240-circuit suite across FP64/FP32/FP16, one/four
workers, and optimization plus fusion disabled/enabled. Multiworker parity forces
the parallel threshold to zero. The suite includes every supported gate, arbitrary
complex matrices, controls in both bit orders, random circuits, interference and
the 14-qubit large-stride case. Existing precision-specific tolerances were retained.
Worst amplitude errors were approximately 9.25e-15 in FP64, 2.22e-6 in FP32 and
0.00174 in FP16; these are suite-specific results.

## Measured performance

Median gate-execution times over five samples following one warmup, FP64 throughout.
The Python reference is installed Qiskit 2.2.3 `quantum_info.Statevector` with NumPy
2.0.2. Numerical library thread environment limits were set to one. The table shows
Rust with CX optimization and fusion enabled; compression did not shorten CX runs
in these workloads.

| Qubits | Circuit | Gates | Python ms | Rust one worker ms | Ratio | Rust four workers ms | Ratio |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | Mixed gates | 80 | 7.126 | 1.243 | 5.74× | 0.586 | 12.16× |
| 16 | Scrambled Bell | 176 | 21.363 | 5.934 | 3.60× | 1.834 | 11.65× |
| 20 | Mixed gates | 84 | 1,611.239 | 23.071 | 69.84× | 7.059 | 228.26× |
| 20 | Scrambled Bell | 220 | 4,310.666 | 116.640 | 36.96× | 33.777 | 127.62× |

The mixed circuit prepares every qubit with H, then applies 64 rotations and
controlled gates targeting the highest qubit and its neighbor. Scrambled Bell
prepares Bell pairs across the half-register cut and applies four seeded Ry/Rz
and local entangling layers. That circuit uses the structured fusion kernel:
32 recognized blocks at 16 qubits and 40 at 20 qubits. Both engines receive the
same source gates, angles and register width. Every benchmark checks the full
complex state; maximum absolute errors across the listed fused configurations
were below 2.13e-17.

Without fusion, one-worker Rust still outperformed Python by 2.13× and 3.37× at
16 qubits and 29.15× and 34.50× at 20 qubits. Four-worker fusion improved the
20-qubit mixed workload from 17.309 ms unfused to 7.059 ms fused, and scrambled
Bell from 44.369 ms to 33.777 ms.

Rust fresh-request medians, including parsing, worker-pool creation, compilation,
allocation, execution and probe response, were 7.268 ms for the fused four-worker
20-qubit mixed circuit and 34.003 ms for scrambled Bell. Worker process startup and
client request encoding are excluded from these separately recorded request times.

## Timing interpretation

These compare the restored library against Python Qiskit Statevector, not Qiskit
Aer. Rust gate timers exclude initial allocation, compilation, pool construction,
serialization, norms and probes. Python timers exclude circuit construction and
initial zero-state creation, but include the allocations performed by `evolve`.
The ratios therefore describe these exposed execution paths rather than matched
low-level kernels. Four-worker ratios also use different requested CPU resources;
one-worker results are included for comparison.

Qiskit shows a large runtime increase from 16 to 20 qubits in this environment.
The benchmark does not isolate its cause. Python baselines run first and are reused
across Rust settings; order is not randomized. Affinity, temperature and unrelated
desktop activity were not controlled. The small Python tooling test suite was run
during the overall verification session. Five samples support these observed
workload results, not a universal speed guarantee. Historical Aer results cannot
be transferred to this reconstruction.

## Environment and artifacts

The run used ARM64 macOS 27.2.1, ten reported logical CPUs, rustc 1.100.0 and
Python 3.9.6. Rust used the normal release profile, without native-CPU flags or
unsafe code. The new `.venv-rebuild/` is separate from the incomplete historical
environment; its dependencies are pinned in
[tools/requirements-rebuild.txt](tools/requirements-rebuild.txt).

[Raw results](benchmark-results/rebuild/results.json) contain every sample,
compiler and package versions, circuit hashes, source/binary hashes, parity errors,
compilation statistics and separate request timings. Raw benchmark results remain
locally ignored by Git, following the existing repository convention.

The source hash for this run, concatenating sorted Rust files under `src/`, is
`7b1f3f79aa3bcb2f8a53cb241a90ea1a04277aec0846cdb62930a7f86bb928a5`.

## Run and reproduce

```sh
cargo build --release --locked
cargo run --release --example bell
echo '{"num_qubits":2,"threads":4,"fusion":true,"gates":[{"gate":"h","target":0},{"gate":"cx","control":0,"target":1}]}' | target/release/statevector

cargo test --locked --all-targets
cargo test --locked --release --all-targets
cargo clippy --locked --all-targets -- -D warnings
cargo fmt --check
python3 -B -m unittest discover -s tools/tests -v

python3 -m venv .venv-rebuild
.venv-rebuild/bin/python -m pip install -r tools/requirements-rebuild.txt
.venv-rebuild/bin/python tools/verify_rebuild.py
```

Use `--mode parity` for correctness alone, or `--mode bench` for performance alone.
`--qubits`, `--repetitions`, `--warmup` and `--output` select the new benchmark's
scope. It limits sizes to 4–22 qubits. The old pinned-submodule tools remain
unchanged; this harness explicitly imports the installed Qiskit wheel instead.
