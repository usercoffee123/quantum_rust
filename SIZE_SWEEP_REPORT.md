# State-vector performance sweep: 4–30 qubits

Run date: November 8, 2026. The benchmark now covers **every integer qubit count from 4 through 30**, in FP64, FP32, and FP16. The full run contains 81 configurations: **78 measured and passing**, with **3 explicit resource skips**. The follow-up FP16 29-qubit measurement also passed.

## Largest completed cases

| Mode | Largest measured state | State payload | Peak child RSS | Median gate-sequence time |
| --- | --- | --- | --- | --- |
| F64 | 28 qubits | 4 GiB | 4.002 GiB | 3.410 s |
| F32 | 29 qubits | 4 GiB | 4.002 GiB | 6.149 s |
| F16 | 30 qubits | 4 GiB | 4.002 GiB | 17.056 s |

FP16 at 30 qubits allocated and updated a dense buffer of **1,073,741,824 complex amplitudes**. It was executed, not extrapolated. The state alone used 4 GiB; peak RSS includes the Rust runtime, workers, allocator overhead, and final norm/probe inspection.

## Limits and workload

- Host: Apple M4, 10 logical CPU cores, 16 GiB physical RAM, macOS 26.3.1 ARM64. Rust execution used four workers.
- State limit: 4 GiB, also bounded by 25% of physical RAM and a live reclaimable-memory estimate with 1 GiB reserved headroom. One child process runs at a time. Memory estimates are advisory, not OS reservations.
- Each circuit applies H to every qubit, followed by 12 mixed RX/RY/RZ/CX/CZ gates across low, middle, and high bits. Total timed gates are therefore `qubits + 12`; the largest cases do not use a fixed circuit length.
- The remaining spectator qubits stay in a uniform product state. The simulator still allocates and traverses the full dense state, but these are structured performance workloads rather than arbitrary highly entangled random circuits.
- Three timed samples per case, no additional configured warmup. The CLI also performs an untimed preflight and a final verification run, so each case executes five gate sequences in total.
- Gate timings exclude initialization, pool creation, IPC, norm/probe checks, and export. Per-process wall time and peak RSS are separately retained in JSON. Output exports 16 probe amplitudes rather than the entire state.
- No simulator core changes were needed to reach these sizes. The runner explicitly raises the existing per-request memory limit. The default library limit remains 256 MiB.

## Full sweep

The table shows the median of three whole gate-sequence executions, in milliseconds. Skips have no timing value. Tiny states are dominated by dispatch/timer overhead and should not be used to infer kernel throughput.

| Qubits | FP64 ms | FP32 ms | FP16 ms |
| --- | --- | --- | --- |
| 4 | 0.000333 | 0.0005 | 0.000709 |
| 5 | 0.000625 | 0.000584 | 0.001167 |
| 6 | 0.001333 | 0.001584 | 0.001875 |
| 7 | 0.002708 | 0.002667 | 0.004541 |
| 8 | 0.004083 | 0.005 | 0.0075 |
| 9 | 0.008458 | 0.008209 | 0.013917 |
| 10 | 0.016958 | 0.01725 | 0.028875 |
| 11 | 0.03225 | 0.035958 | 0.063042 |
| 12 | 0.066625 | 0.075042 | 0.112541 |
| 13 | 0.13925 | 0.1455 | 0.242459 |
| 14 | 0.298416 | 0.288666 | 0.493334 |
| 15 | 0.606542 | 0.602541 | 0.996125 |
| 16 | 0.445667 | 0.425334 | 0.658459 |
| 17 | 0.909292 | 0.839625 | 1.34571 |
| 18 | 1.84575 | 1.76658 | 2.671 |
| 19 | 3.748 | 3.50988 | 5.45975 |
| 20 | 7.81546 | 7.332 | 11.7203 |
| 21 | 17.5429 | 15.7498 | 24.3929 |
| 22 | 39.2325 | 31.6653 | 55.0467 |
| 23 | 85.5868 | 66.0327 | 105.363 |
| 24 | 183.122 | 140.186 | 223.038 |
| 25 | 396.433 | 288.833 | 460.861 |
| 26 | 788.738 | 615.728 | 966.496 |
| 27 | 1653.75 | 1342.03 | 2095.71 |
| 28 | 3410.08 | 2912.94 | 4519.53 |
| 29 | skipped (8 GiB state) | 6149.42 | 19869.3 |
| 30 | skipped (16 GiB state) | skipped (8 GiB state) | 17056.2 |

**Keep the original 29-qubit FP16 point in context:** its three samples were unusually variable. A focused follow-up is shown below rather than replacing the original measurement.

| FP16 case | Samples, seconds | Median, seconds |
| --- | --- | --- |
| 29 qubits, initial sweep | 9.965, 22.012, 19.869 | 19.869 |
| 29 qubits, focused recheck | 8.184, 8.182, 8.612 | 8.184 |
| 30 qubits, initial sweep | 29.593, 17.056, 16.804 | 17.056 |

The recheck supports approximately doubling execution time from 29 to 30 qubits for this workload. The initial 29-qubit median was disturbed; its exact cause was not profiled. Frequency, affinity, temperature, and unrelated host activity were not controlled. Three samples give limited statistical confidence. Recorded system-wide swap-in/out counters did not increase during these cases; that does not establish which process caused timing variation.

## Numerical validation

For this circuit family, only three qubits are mixed after uniform preparation. An independent eight-amplitude FP64 simulator computes those qubits, and a uniform spectator factor predicts selected amplitudes in the full state at any size. The runner checks both spectator-extreme versions of every active basis pattern, plus the full Rust norm. This avoids allocating another exponential state. It does not constitute full-vector parity for arbitrary 30-qubit circuits.

Probe error is scaled by the largest expected amplitude, rather than using a fixed absolute tolerance that would become meaningless for tiny amplitudes. FP64 uses scaled error/norm limits of `1e-11`; FP32 uses `3e-5` / `1e-5`; FP16 uses `0.02` / `0.02`. The state is never automatically renormalized. Normalized active-state fidelity and probability TVD are supplementary metrics only.

| Mode | Worst scaled probe error | Worst norm drift | Minimum normalized active fidelity |
| --- | --- | --- | --- |
| F64 | 5.23e-16 | 4.44e-16 | 1.000000000 |
| F32 | 2.66e-07 | 1.46e-07 | 1.000000000 |
| F16 | 0.00195 | 0.0027 | 0.999997503 |

The 30-qubit FP16 case had scaled probe error `0.00188306` and norm drift `0.00269508` (0.270%). Passing this shallow structured circuit does not guarantee FP16 accuracy for other circuits, larger depths, or amplitudes below the binary16 range.

## Qiskit coverage

- The reduced three-qubit reference was cross-checked against the pinned Qiskit submodule at every size: 27 reduced-state comparisons.
- Full Qiskit `Statevector` execution and full-vector reference checks ran at every integer size from 4 through 20: 17 distinct reference circuits, each with three timed repetitions. Each reference timing is reused across Rust precision modes.
- Qiskit references above 20 qubits are explicit skipped reference records. No full 30-qubit Qiskit state was allocated or timed.
- Qiskit uses FP64 with numerical-library thread environment limits requested at one. Rust uses four workers and its selected storage precision. Consequently, the recorded ratios are library-execution comparisons with different requested CPU resources and, for FP32/FP16, different precision. This is not a comparison against Aer.

## Explicit resource skips

| Qubits | Mode | Required state payload | Reason |
| --- | --- | --- | --- |
| 29 | F64 | 8 GiB | Exceeds the configured 4 GiB state limit |
| 30 | F64 | 16 GiB | Exceeds the configured 4 GiB state limit |
| 30 | F32 | 8 GiB | Exceeds the configured 4 GiB state limit |

A 30-qubit FP64 state consumes all 16 GiB of this machine before runtime overhead. The sweep supports that configuration on a larger machine by raising the state budget and, where appropriate, the physical-memory fraction; it does not bypass the live-memory/headroom checks.

## Added tooling and checks

- `tools/benchmark_sizes.py`: inclusive size range, precision/worker controls, configurable memory budget, dynamic admission checks, bounded case runtime, atomic checkpoints, explicit status for every configuration, peak RSS when supported, and independent numerical reference checks.
- Six Python tests cover memory boundary arithmetic, every size/mode, unknown/live memory protection, RSS permission detection, index/scaling correctness, reference norm, and accuracy checks that do not hide norm drift.
- The existing 21 Rust tests and Clippy checks passed. No Rust core changes were made in this work.

## Reproduce

```sh
python3 -m unittest discover -s tools/tests -v
python3 tools/benchmark_sizes.py --dry-run
.venv/bin/python tools/benchmark_sizes.py --min-qubits 4 --max-qubits 30 --threads 4 --precisions f64 f32 f16 --depth 12 --repetitions 3 --warmup 0 --max-state-mib 4096 --memory-reserve-mib 1024 --qiskit-check --qiskit-max-qubits 20 --output benchmark-results/2026-11-08-sizes/results.json
```

This run used `PYTHONPYCACHEPREFIX=/private/tmp/qiskit-rust-sizes-pycache` and offline Cargo dependency resolution. Native peak-RSS collection ran outside the sandbox because macOS `time -l` requires a blocked sysctl there. The runner detects that restriction and falls back to null RSS when needed; it does not classify a successful simulation as a failure because optional instrumentation is unavailable.

Rust source SHA-256: `635731abd23a5db08bed88d1f32451f078d1958a7937036b2730c238b103d5ec`.
Qiskit revision: `9ab2106352f603bf411e1387a758f6d9667c7c1c`.

- [Full raw data](benchmark-results/2026-11-08-sizes/results.json)
- [CSV results](benchmark-results/2026-11-08-sizes/results.csv)
- [Full execution log](benchmark-results/2026-11-08-sizes/run.log)
- [29-qubit FP16 recheck](benchmark-results/2026-11-08-sizes/fp16-29-recheck.json)
- [Python test output](benchmark-results/2026-11-08-sizes/python-tests.log)
