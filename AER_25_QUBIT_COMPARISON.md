# Matched 25-qubit comparison against Qiskit Aer

Measured 2026-11-08 on Apple M4 (10 cores), 16 GiB RAM, macOS 26.3.1. **Aer was faster in all four matched FP64 comparisons**, by 1.14–1.68×. The previous 41× Rust advantage against Python Qiskit `Statevector` does not carry over to these optimized Aer workloads.

Both engines simulate the same 25-qubit circuits with the same requested CPU thread count. Aer reports the matching state-update thread count in every result. Primary Aer results retain its default gate fusion, confirmed applied. Each cell is the median of three measured requests following one warmup; engine order alternates between trials.

| Circuit | Threads per engine | Rust seconds | Aer seconds | Aer speedup |
|---|---:|---:|---:|---:|
| scrambled_bell | 1 | 10.720 | 9.389 | 1.14× |
| random_matching | 1 | 10.674 | 9.264 | 1.15× |
| scrambled_bell | 4 | 5.564 | 3.320 | 1.68× |
| random_matching | 4 | 5.788 | 3.468 | 1.67× |

## Circuits and correctness

Each circuit has 521 gates, including 108 CX gates, depth 26, eight rotation/entangling layers, and seeded random angles (seed 42). All 25 qubits are active. Each FP64 state occupies 512 MiB.

- `scrambled_bell`: starts with 12 Bell pairs crossing the 12|13 partition, then applies random Ry/Rz rotations and entangling matchings within each side. These local unitaries preserve exactly 12 ebits across that partition by construction.
- `random_matching`: starts from the same Bell preparation, then uses unrestricted random matchings across all qubits and Ry/Rz rotations. It exercises a broader connectivity pattern.

Full-state validation occurs outside performance timing, comparing all 33,554,432 complex amplitudes for every circuit/thread combination. Maximum absolute amplitude error was 1.86e-18 or less, norm error below 1.6e-15, and normalized fidelity rounded to 1.0. Timed trials also verify amplitude probes against that reference.

Reduced-state entropy was computed numerically for the lowest and highest five qubits (maximum 5 bits):

| Circuit | Low-five entropy | High-five entropy |
|---|---:|---:|
| scrambled_bell | 5.000000 | 4.946828 |
| random_matching | 4.999319 | 4.996894 |

The 12-ebit claim is from the Bell/local-unitary construction; a full half-partition eigendecomposition was not performed.

## Fusion diagnostic and improvement opportunity

Disabling Aer fusion gives the following diagnostic. These slower configurations are not the primary comparison.

| Circuit | Threads | Aer fusion off seconds | Fusion speedup (off/on) |
|---|---:|---:|---:|
| scrambled_bell | 1 | 16.852 | 1.79× |
| random_matching | 1 | 16.941 | 1.83× |
| scrambled_bell | 4 | 7.323 | 2.21× |
| random_matching | 4 | 7.848 | 2.26× |

Fusion substantially improves Aer on these workloads. Implementing gate fusion is a promising next optimization for Rust: combine compatible operations to reduce repeated passes through a 512 MiB state. This experiment does not isolate fusion as the sole cause of the overall engine difference; their kernels and scheduling also differ.

## Timing scope and limitations

These are fresh-state request wall latencies: allocation/initialization, circuit execution, and equivalent small amplitude-probe outputs. Circuit construction, process startup, full-state validation/export, entropy calculations, and norm scans are excluded. The Rust native process remains alive, but request parsing and per-request Rayon pool creation are included. Aer backend objects persist; job dispatch and internal circuit handling are included. Client-side Rust request serialization is outside its timer. This measures the exposed request paths, not isolated gate kernels.

Both engines use FP64 CPU statevectors, without noise or measurement sampling workloads. Aer uses `method=statevector`, `device=CPU`, `precision=double`, one parallel experiment/shot, truncation disabled, and fusion enabled. Separate worker processes set OpenMP thread limits before numerical imports; BLAS/Accelerate diagnostic threads are limited to one. CPU affinity and thermal state are not controlled. The engines run sequentially, without competing benchmark jobs.

This is two circuit families at one seed and one size, with three samples each. It broadens the earlier shallow circuit but does not establish general performance across algorithms, hardware, precisions, or sizes. The earlier 41× measurement used a different circuit, Python `Statevector`, and unequal thread counts; it is not comparable to this table.

## Reproduction and artifacts

- Aer 0.18.2; editable Qiskit 2.7.0.dev0+9ab2106, submodule commit `9ab2106352f603bf411e1387a758f6d9667c7c1c`.
- Rust `1.98.0 (88d9e12ae 2026-08-18)`; optimized release binary. NumPy 2.5.4.
- [Runner](tools/benchmark_aer.py), [Aer requirement](tools/requirements-aer.txt), [raw samples and metadata](benchmark-results/2026-11-08-aer25/results.json), [run log](benchmark-results/2026-11-08-aer25/run.log). Exact circuit JSON files accompany the results.
- Core/CLI source SHA-256: `f5faa20d634f72ccdccade043a4d95b2b0512b9037a1fded692ae51d1d15dc75` (concatenated lib.rs, precision.rs, execution.rs, bin/statevector.rs).

```sh
.venv/bin/python -m pip install -r tools/requirements-aer.txt
PYTHONPYCACHEPREFIX=/private/tmp/qiskit-rust-aer-pycache CARGO_NET_OFFLINE=true \
  .venv/bin/python tools/benchmark_aer.py --qubits 25 --layers 8 \
  --threads 1 4 --repetitions 3 --warmup 1 \
  --output benchmark-results/2026-11-08-aer25
```

All 22 Rust tests and 10 Python tooling tests passed before the benchmark; Clippy passed with warnings denied. The new CLI binary state export and optional norm scan allow full parity checks without large JSON transfers and comparable small-result timing. Default CLI behavior is preserved.
