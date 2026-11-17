# Fusion-insensitive 25-qubit comparison

Recorded 2026-11-08. A stride-CX workload shows no observed speed benefit from Aer gate fusion in this experiment. This is a narrow diagnostic, complementing the broader circuits where optimized Aer won.

## Results

Same Apple M4 host, FP64 CPU statevectors, four threads per engine, one warmup and three measured requests, with execution order rotated across Rust, Aer fusion on, and Aer fusion off. Aer confirmed four state-update threads.

| Engine/configuration | Median seconds | Sample range seconds |
|---|---:|---:|
| Rust | 6.972 | 6.956–7.289 |
| Aer, default fusion on | 7.574 | 7.225–7.647 |
| Aer, fusion off control | 7.177 | 7.089–7.777 |

Rust was 1.09× faster than default Aer and 1.03× faster than fusion-disabled Aer by these medians. The differences are small and ranges overlap; three trials do not establish a statistically reliable general advantage. Fusion-on Aer was about 5.5% slower than fusion-off by medians, rather than faster. This is an observation, not proof that fusion cannot help under any conditions.

## Circuit and fusion audit

The circuit uses 25 active qubits and 1,025 gates: 25 preparation gates create 12 cross-partition Bell pairs plus an H on the remaining qubit, followed by 1,000 CX gates. Body gate i has control i mod 25 and target (i+12) mod 25. It uses no barriers or special fusion configuration; default fusion remains enabled.

The CX-only body audited separately reports `fusion.applied=false`. This audit checks compiler behavior; starting the body alone from zero is not the correctness or performance workload. All reported comparative timings include the entangled preparation.

For the complete circuit, verbose fusion output contains 13 unitary operations, 987 individual CX operations, and the final amplitude save. The small preparation/prefix still fuses. Therefore the **complete circuit is fusion-insensitive, not strictly fusion-free**. The 1,000-gate body has no fusion in isolation, and the full workload's on/off measurements show no observed benefit. Claiming fusion never ran on the complete circuit would be incorrect.

An attempted full-register barrier between gates did not stop Aer fusion, so barriers were rejected as evidence of a fusion-free comparison.

The resulting low-five and high-five subsystem entropies are both 5.0 bits, the maximum for those cuts. Full half-partition entropy was not computed; after global CX gates the preparation's original 12-ebit half-cut value is not assumed preserved.

This is a Clifford/CX-heavy circuit selected to suppress fusion benefits. Both engines are explicitly forced to statevector simulation, not a stabilizer method; it does not represent general non-Clifford algorithm performance. Our previous rotation-heavy benchmarks remain relevant and favor Aer.

## Correctness and scope

All 33,554,432 amplitudes were compared outside timing. Maximum absolute amplitude error was 1.39e-17; normalized fidelity was 0.9999999999999998. Both norms were within 5e-16 of one. Every timed request also passed amplitude-probe parity.

Timers include fresh state allocation/initialization, execution, and equivalent probe output. Process startup and circuit construction are excluded. Rust parsing/pool creation and Aer job dispatch are included. Full-state exports, entropies, and verbose fusion audits are outside the timed trials. There are no intermediate saves/barriers or extra synchronization instructions imposed on only one engine in the measured circuit. This remains a request-level comparison, not isolated kernel profiling.

Aer version is 0.18.2, CPU statevector with FP64 and default fusion options; precision/thread settings match Rust. No affinity or thermal controls were applied.

## Files and reproduction

- [Where the project stood before this experiment](BENCHMARK_CHECKPOINT.md)
- [Runner](tools/benchmark_fusion_insensitive.py)
- [Exact circuit request](benchmark-results/fusion-insensitive-25q/request.json)
- [Raw samples, correctness, entropy, and fusion metadata](benchmark-results/fusion-insensitive-25q/results.json)
- [Earlier optimized Aer comparisons](AER_25_QUBIT_COMPARISON.md)

```sh
PYTHONPYCACHEPREFIX=/private/tmp/qiskit-rust-aer-pycache CARGO_NET_OFFLINE=true \
  .venv/bin/python tools/benchmark_fusion_insensitive.py
```

The runner asserts that the body-only fusion audit reports no fusion, validates the entire entangled state against Rust, then measures the full circuit in all three configurations.
