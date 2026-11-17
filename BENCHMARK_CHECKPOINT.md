# Simulator benchmark checkpoint

Recorded 2026-11-08. We have a Rust CPU statevector simulator with FP64, FP32, and FP16 storage/FP32 computation, parity tooling, and performance runners. Qiskit is a pinned submodule; comparisons now use installed Qiskit Aer 0.18.2 CPU statevector mode rather than only Python Qiskit Statevector.

## What has actually been measured

The old 41× FP64 advantage at 25 qubits was specific to a shallow circuit, Python Statevector, and unequal thread counts. It does not establish an advantage over optimized Aer.

Matched comparisons use FP64, the same requested thread count, and Aer metadata confirming the state-update thread count. Aer retains default gate fusion.

| Circuit | Threads each | Rust | Aer | Aer speedup |
|---|---:|---:|---:|---:|
| Scrambled Bell, 8 layers | 1 | 10.720 s | 9.389 s | 1.14× |
| Random matching, 8 layers | 1 | 10.674 s | 9.264 s | 1.15× |
| Scrambled Bell, 8 layers | 4 | 5.564 s | 3.320 s | 1.68× |
| Random matching, 8 layers | 4 | 5.788 s | 3.468 s | 1.67× |
| Scrambled Bell, 350 layers | 4 | 245.306 s | 161.789 s | 1.52× |
| Scrambled Bell, 700 layers | 4 | 475.163 s | 295.935 s | 1.61× |
| Same 700 layers, fresh rerun | 4 | 557.497 s | 313.555 s | 1.78× |

All rows use 25 qubits. Eight-layer rows are medians of three measurements after a warmup. Longer rows are individual requests without workload warmup; run-to-run variation is substantial and its cause has not been isolated.

The 350-layer workload has 21,725 gates / 4,212 CX gates. The 700-layer workload has 43,425 gates / 8,412 CX gates. The scrambled-Bell family preserves 12 ebits across a 12|13 cut by construction. FP64 storage is 512 MiB per state.

## Correctness and timing limits

The eight-layer comparisons checked all 33,554,432 amplitudes per circuit/thread combination, with maximum error below 1.86e-18. Long-circuit runs checked four amplitude probes, not full states; those passed. All 22 Rust tests and 10 Python tooling tests passed at the initial matched benchmark checkpoint, along with Clippy.

Timers include fresh-state allocation/initialization, execution, and small amplitude-probe output. Circuit construction and process startup are excluded. Rust parsing and per-request pool creation are included; Aer job submission/internal circuit handling are included. These are request latencies, not isolated gate kernel measurements. Tests ran sequentially on an Apple M4 with 16 GiB RAM; affinity/thermal state were not controlled.

## What the fusion evidence means

In the eight-layer experiment, disabling Aer fusion made Aer about 1.8–2.2× slower. Fusion was reported applied in the primary comparisons. Our Rust simulator applies gates individually, so reducing repeated statevector passes is a plausible major opportunity. This does not prove fusion explains the entire speed difference: native kernels, scheduling, and API overhead also differ.

## Next experiment: constrain fusion explicitly

Construct a 25-qubit entangled circuit with a full-register barrier between every gate in Aer. The mathematical gate sequence is identical in Rust; barriers have no statevector action. If Aer respects those barriers during fusion, there can be no multi-gate fused block. Keep fusion enabled, inspect metadata, and compare against fusion disabled as a control. First use a modest circuit with warmups and repeated measurements, instead of another 10-minute single trial.

This is a deliberately constrained diagnostic of execution without fusion opportunities. It complements the optimized Aer comparison; it does not replace it or imply ordinary workloads cannot benefit from fusion. Barrier processing adds Aer overhead, so it must be disclosed. If the barrier experiment still fuses, do not label it fusion-free: investigate or report the limitation.

## Existing reports and artifacts

- [Matched Aer methodology and full-state validation](AER_25_QUBIT_COMPARISON.md)
- [Four-minute Rust workload](FOUR_MINUTE_CIRCUIT.md)
- [700-layer comparisons and rerun](DEEP_CIRCUIT_COMPARISON.md)
- [Initial matched raw results](benchmark-results/2026-11-08-aer25/results.json)
- [Fresh long-run raw results](benchmark-results/deep-700-layer-25q-rerun/results.json)

This file was written before starting the fusion-constrained experiment. Its results will be recorded separately.
