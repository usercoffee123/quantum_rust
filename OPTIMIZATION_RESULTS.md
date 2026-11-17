# Kernel optimization and matched Aer results

November 17, 2026. A second implementation pass improved the Rust controlled-gate
traversal and structured rotation kernel. In a fresh matched comparison, the new
binary was 1.48–1.86× faster than the previous Rust binary and 1.35–1.79× faster
than optimized Qiskit Aer on four tested circuits. These are observed medians,
not a guarantee for other circuits or machines.

## Changes

[src/execution.rs](src/execution.rs) now traverses only control-one pairs for CX
and CZ. When the control bit is above the target, it skips entire inactive halves.
When below, it selects active stripes within the paired halves. Coarse task
grouping avoids creating tiny nested parallel jobs. The implementation retains
safe, disjoint mutable slices and exact serial/threaded gate results.

[src/precision.rs](src/precision.rs) adds real-coefficient scaling in the selected
compute precision. [src/fusion.rs](src/fusion.rs) uses it for structured Ry mixing
instead of general complex multiplication. Rz phases still use complex arithmetic.
This removes arithmetic with known zero imaginary coefficients. FP16 retains FP32
computation and FP16 writeback.

A new independent traversal test covers control and target bits 0, 1, 11, 12, 13
and 16 in every distinct ordering, in serial and parallel modes, on a 17-qubit
buffer containing distinct amplitudes. This checks active-pair coverage across
the scheduler's chunk boundaries.

## Matched comparison with Aer

Five measured fresh-state requests followed one warmup. The order of new Rust,
Aer and old Rust rotated between trials. All used FP64 and four state workers,
the same gate sequence and the same 67 amplitude probes. Rust fusion was enabled
and CX compression disabled. Aer retained fusion with its normal five-qubit
maximum; result metadata confirmed both fusion and four state-update workers.

| Qubits | Circuit | Gates | Old Rust ms | New Rust ms | Aer ms | Old Rust / new | Aer / new |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 | Scrambled Bell | 420 | 76.262 | 43.081 | 71.117 | 1.77× | 1.65× |
| 20 | Random matching | 420 | 77.486 | 41.572 | 74.373 | 1.86× | 1.79× |
| 22 | Scrambled Bell | 454 | 347.949 | 225.466 | 304.239 | 1.54× | 1.35× |
| 22 | Random matching | 462 | 372.465 | 251.866 | 350.928 | 1.48× | 1.39× |

The circuits use eight scrambling layers and seed 42. Before timing, both Rust
binaries were checked against every complex amplitude of the Aer state. Maximum
absolute errors were below 7.2e-18. Every timed request also passed its probe check.

The request timer includes allocation, initialization, execution and equivalent
probe output. Rust JSON parsing, pool creation and circuit compilation are included;
Aer job submission and fusion are included. Imports, circuit construction, full
validation and native process startup are excluded. BLAS limits were one; OpenMP
and state-update workers were four. The engines ran sequentially. CPU affinity and
thermal conditions were not controlled, and five samples provide limited evidence
about variability.

A preliminary pass containing only control-aware traversal improved the previous
Rust binary by about 5–8%, but Aer remained faster in three of four configurations.
Those results are preserved separately. The final table measures both optimizations
together and does not isolate each one's contribution.

## Python comparison and correctness

The full 2,880-comparison Qiskit parity suite passed again for the new source,
covering three precisions, one/four workers, and compilation plus fusion on/off.
All 37 Rust tests passed in debug and release builds, all 20 Python tooling tests
passed, and formatting and Clippy with warnings denied passed.

A fresh run of the Python Statevector benchmark also passed full-state checks for
all 16 benchmark configurations. Selected fused four-worker results:

| Qubits | Circuit | Rust gate time ms | Python Statevector ms | Python / Rust |
| --- | --- | ---: | ---: | ---: |
| 16 | Mixed | 0.434 | 7.000 | 16.13× |
| 16 | Scrambled Bell | 1.126 | 21.101 | 18.74× |
| 20 | Mixed | 7.211 | 1,327.633 | 184.10× |
| 20 | Scrambled Bell | 20.854 | 3,570.347 | 171.21× |

These Python ratios use the gate-execution timing policy of
[tools/verify_rebuild.py](tools/verify_rebuild.py), which differs from the matched
request policy of the Aer table. They also compare four Rust workers with Python
numerical libraries limited to one. They should not be substituted for the Aer
comparison or interpreted as a universal Rust/Python language speed ratio.

## Environment and reproduction

ARM64 macOS, rustc 1.100.0, Python 3.9.6, NumPy 2.0.2, Qiskit 2.2.3 and Aer 0.17.2.
The Aer version is the compatible wheel installed in the reconstruction environment;
this is a new comparison, not reproduction of the historical Aer 0.18.2 reports.

```sh
cargo test --locked --all-targets
cargo test --locked --release --all-targets
cargo clippy --locked --all-targets -- -D warnings
cargo fmt --check
python3 -B -m unittest discover -s tools/tests -v

.venv-rebuild/bin/python -m pip install -r tools/requirements-retry-aer.txt
.venv-rebuild/bin/python tools/benchmark_rebuild_aer.py
.venv-rebuild/bin/python tools/verify_rebuild.py --output benchmark-results/retry-python/results.json
```

To include the preserved old binary in the matched comparison, add
`--baseline-binary benchmark-results/retry-aer/before-statevector` and select a new
`--output` path. Without that optional argument the harness compares current Rust
with Aer only.

Artifacts, locally ignored under the existing benchmark convention:

- [Final Aer samples and metadata](benchmark-results/retry-aer/results.json).
- [Control traversal preliminary results](benchmark-results/retry-aer/control-traversal-results.json).
- [Fresh Python parity and performance results](benchmark-results/retry-python/results.json).
- `benchmark-results/retry-aer/before-statevector`: executable from before this pass.

Both final result sets were verified to match the current source and executable
hashes. The source hash, concatenating sorted Rust source files, is
`0ebc5e4ee1876badad43f7d637d28b22aede0099ba6d5408d7e06fb829d1a695`.
