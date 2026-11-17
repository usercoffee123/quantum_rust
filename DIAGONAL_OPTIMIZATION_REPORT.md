# Diagonal gate optimization results

November 8, 2026. Z, S, T, and RZ now have specialized execution paths. Compared with the retained general matrix kernel, all 144 final measured configurations were faster in this run. The public gate API, precision choices, and threading settings are unchanged.

## Implementation

- Z negates only amplitudes whose target bit is one. S swaps and negates their real/imaginary components. These operations act directly on storage, including FP16, without explicitly promoting the state components to FP32.
- T phase-multiplies only amplitudes whose target bit is one; the other amplitudes are left untouched.
- RZ phase-multiplies both members of each amplitude pair, replacing the general four-coefficient matrix expression with two complex multiplications.
- CZ also uses the exact storage sign operation. Its speedup was not measured in this comparison.
- All paths reuse the existing safe pair scheduler. FP16 T/RZ arithmetic remains FP32 with rounding on writeback; FP32/FP64 behavior and compensated norm reporting remain intact.
- `Gate::U` retains the general matrix path and serves as an explicit reference for tests and timing. No fusion or new runtime mode is introduced.

## Verification

- 21 Rust tests pass in both debug and release builds; Clippy with warnings denied and formatting checks pass.
- New tests compare specialized gates against equivalent `Gate::U` matrices after each gate, for every target in 1-, 5-, and 14-qubit states, across all three precisions and serial/four-worker execution. Tested RZ angles include negative zero, very small values, negative values, and a large finite value.
- FP16 bit tests verify exact sign changes and component permutations for signed zeros, subnormals, and ordinary values.
- All 3,600 Qiskit comparisons pass: 240 circuits × FP64/FP32/FP16 × 1/2/4/8/10 workers. The existing numerical tolerances were retained.
- All 144 performance cases passed norm and amplitude-probe comparisons against their general-matrix counterparts.
- Maximum benchmark probe difference: `4.11e-18`; maximum norm difference: `1.78e-15`. The Python-generated phase coefficients can differ slightly from Rust's coefficient preparation; Rust reference tests use the same Rust formulas and require exact numeric equality.

## Matched performance comparison

The baseline is the general `Gate::U` kernel in the same release binary, with the same matrix, state size, precision, thread count, and scheduler. This is a specialized/general comparison, not a before/after checkout benchmark and not a comparison against Qiskit Aer. Dispatch and coefficient preparation differ between named gates and `Gate::U`.

Each circuit contains H preparation on every qubit followed by 64 diagonal gates. Preparation is identical and included in both timings. Initialization, worker-pool creation, JSON transport, and result export are excluded. Each variant has one configured warmup and five timed repetitions, plus the CLI preflight/final verification. Variant execution order alternates by configuration. These are whole-sequence speedups, not isolated single-gate speedups.

The sweep covers 16/20 qubits, all three precisions, 1/4 workers, low/middle/high targets, and Z/S/T/RZ: 144 matched configurations and 1,440 timed circuit executions. Measurements ran sequentially after verification. Host: Apple M4, macOS ARM64; raw metadata records the toolchain and source hash. As in the earlier report, frequency, affinity, thermal state, and unrelated system activity were not controlled.

| Gate | Median speedup | Minimum | Maximum |
| --- | --- | --- | --- |
| Z | 1.82× | 1.50× | 4.68× |
| S | 2.96× | 1.27× | 4.70× |
| T | 2.06× | 1.49× | 3.67× |
| RZ | 1.60× | 1.11× | 2.07× |

Each speedup divides the general-matrix median runtime by the specialized median runtime. Values above one favor specialization. The following table takes the median of the three target-position speedups in each group:

| Qubits | Mode | Workers | Z | S | T | RZ |
| --- | --- | --- | --- | --- | --- | --- |
| 16 | f64 | 1 | 1.84× | 3.29× | 2.90× | 2.00× |
| 16 | f64 | 4 | 1.77× | 2.70× | 2.20× | 1.53× |
| 16 | f32 | 1 | 1.84× | 3.91× | 3.67× | 2.04× |
| 16 | f32 | 4 | 1.75× | 3.03× | 2.15× | 1.66× |
| 16 | f16 | 1 | 4.55× | 4.46× | 1.76× | 1.20× |
| 16 | f16 | 4 | 3.44× | 3.45× | 1.73× | 1.18× |
| 20 | f64 | 1 | 1.76× | 2.91× | 2.64× | 1.93× |
| 20 | f64 | 4 | 1.70× | 2.86× | 2.19× | 1.54× |
| 20 | f32 | 1 | 1.77× | 3.46× | 3.24× | 1.95× |
| 20 | f32 | 4 | 1.71× | 1.97× | 2.24× | 1.63× |
| 20 | f16 | 1 | 3.80× | 3.83× | 1.71× | 1.16× |
| 20 | f16 | 4 | 3.61× | 3.71× | 1.70× | 1.14× |

FP16 benefits especially from the exact Z/S storage operations. RZ benefits less because it still loads and stores the whole state and retains FP16 conversion costs. Neither conclusion implies the same gain for arbitrary circuit mixes or state sizes.

## Regression discovered during tuning

The first specialized RZ loop loaded/stored the lower amplitude before loading the upper amplitude. The initial sweep found FP16 RZ regressions on target 19 of a 20-qubit state: approximately 0.71× serial and 0.69× with four workers versus the general kernel. Those results were not accepted as the final implementation.

The final loop loads both amplitudes before either store. A focused rerun with nine samples and two warmups measured approximately 1.17–1.20× across the three target positions and both worker counts. The complete final sweep was then rerun; its minimum RZ speedup was 1.11×. This demonstrates sensitivity to the loop shape on the tested compiler/hardware; no instruction-level profiling was performed to establish the exact mechanism.

## Reproduce

```sh
cargo test --locked --all-targets
cargo test --locked --release --all-targets
cargo clippy --locked --all-targets -- -D warnings
.venv/bin/python tools/compare_qiskit.py parity --precisions f64 f32 f16 --threads 1 2 4 8 10
python3 tools/benchmark_diagonal.py --qubits 16 20 --threads 1 4 --repetitions 5 --warmup 1 --output benchmark-results/2026-11-08-diagonal/matched.json
```

The matched benchmark needs only Python standard-library modules and the Rust toolchain. Qiskit parity still requires the editable submodule installation. `--gates rz` selects a focused comparison. This run isolated generated Python bytecode with `PYTHONPYCACHEPREFIX=/private/tmp/qiskit-rust-diagonal-pycache`.

Final Rust source SHA-256: `635731abd23a5db08bed88d1f32451f078d1958a7937036b2730c238b103d5ec`.

- [Final raw matched results](benchmark-results/2026-11-08-diagonal/matched.json)
- [Final parity log](benchmark-results/2026-11-08-diagonal/parity.log)
- [Debug test log](benchmark-results/2026-11-08-diagonal/debug-tests.log)
- [Release test log](benchmark-results/2026-11-08-diagonal/release-tests.log)
- [Initial results showing the regression](benchmark-results/2026-11-08-diagonal/initial-matched.json)
- [Focused load-order rerun](benchmark-results/2026-11-08-diagonal/rz-load-order.json)

The earlier [full performance report](PERFORMANCE_REPORT.md) remains a historical baseline for its recorded source hash. Its generic-diagonal logical-bandwidth estimates do not describe the new specialized kernels.
