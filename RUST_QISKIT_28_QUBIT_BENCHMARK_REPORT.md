# Rust and Qiskit Aer 28 Qubit Precision Benchmark

**Benchmark date: November 14, 2026.**

On the same 28-qubit scrambled-Bell circuit, the Rust simulator completed FP64 execution in **26 minutes 23 seconds**, compared with **47 minutes 29 seconds** for Qiskit Aer. Rust FP32 completed in **15 minutes 12 seconds**, compared with **41 minutes 29 seconds** for Aer FP32. These observations correspond to Rust speedups of **1.80× in FP64** and **2.73× in FP32**.

The circuit contains 486 scrambling layers and 34,048 source gates. All four configurations used four CPU workers on an Apple M4 with 16 GiB RAM. Each configuration has one complete timing sample; the results describe this circuit and machine rather than a repeated-run performance distribution.

## Runtime results

| Simulator | Precision | Runtime | Elapsed seconds | Peak sampled process tree RSS |
| --- | --- | --- | --- | --- |
| Rust | FP64 | 26:22.79 | 1,582.793 | 4.018 GiB |
| Qiskit Aer | FP64 | 47:29.42 | 2,849.422 | 4.163 GiB |
| Rust | FP32 | 15:11.73 | 911.734 | 2.046 GiB |
| Qiskit Aer | FP32 | 41:28.67 | 2,488.674 | 2.163 GiB |

| Comparison | Runtime ratio | Reduction in elapsed time |
| --- | --- | --- |
| Rust FP64 against Aer FP64 | 1.80× faster | 44.45% |
| Rust FP32 against Aer FP32 | 2.73× faster | 63.36% |
| Rust FP32 against Rust FP64 | 1.74× faster | 42.40% |
| Aer FP32 against Aer FP64 | 1.15× faster | 12.66% |

Speedup is calculated as the comparison runtime divided by the faster runtime. The reduction in elapsed time uses the comparison runtime as its denominator. Rust FP64 saved approximately **21 minutes 7 seconds** relative to Aer FP64; Rust FP32 saved approximately **26 minutes 17 seconds** relative to Aer FP32.

FP32 reduced both simulators’ state storage from 4 GiB to 2 GiB. It produced a larger runtime improvement in Rust than in Aer in this experiment. The runtime measurements alone do not identify the hardware or implementation cause of that difference.

## Circuit and hardware

| Property | Value |
| --- | --- |
| Qubits | 28 |
| Circuit family | Scrambled Bell |
| Scrambling layers | 486 |
| Source gates | 34,048 |
| CX gates | 6,818 |
| Random seed | 42 |
| Large circuit amplitude probes | 66 |
| CPU | Apple M4, 10 physical cores |
| Physical memory | 16 GiB |
| Workers per simulation | 4 |
| Operating system | macOS 26.3.1, ARM64 |

The circuit begins with 14 Bell pairs connecting the two halves of the register. Each subsequent layer applies Ry and Rz rotations to every qubit, followed by shuffled CX pairings within each half. These operations preserve maximal entanglement across the 14|14 partition. Both backends execute dense statevectors.

Every configuration receives the same gate order, angles, seed, register width, and probe indices. FP64 and FP32 mean 64 and 32 bits per real component, respectively. A complex amplitude therefore occupies 16 bytes in FP64 and 8 bytes in FP32.

## Software and simulation settings

| Component | Version or setting |
| --- | --- |
| Rust compiler | rustc 1.100.0, release build |
| Qiskit | 2.6.2 |
| Qiskit Aer | 0.18.2 |
| NumPy | 2.5.4 |
| Python benchmark environment | CPython 3.13.12 |
| Aer method and device | Statevector, CPU |
| Aer precision | double for FP64; single for FP32 |
| Rust fusion | Enabled, including automatic eligible Ry/Rz specialization |
| Rust CX compression | Disabled |
| Aer fusion | Enabled, normal maximum of five fused qubits |
| Aer qubit truncation | Disabled |
| Aer zero threshold | 0 |
| Parallel experiments and shots in Aer | Limited to one each |

OpenMP thread limits were fixed at four. BLAS and related numerical-library thread limits were fixed at one. Rust used a four-worker executor. Aer metadata confirmed four workers for state updates and confirmed that fusion was applied. The Aer statevector parallel threshold was explicitly set to zero; at 28 qubits, both this setting and its usual threshold select parallel execution.

The Rust build automatically recognizes eligible two-wire blocks containing one Ry followed by one Rz on each wire. It applies real rotation mixing followed by precombined complex phases, with one load and one store per amplitude quartet. Other blocks retain the dense kernel. This comparison measures the complete current Rust simulator against Aer; it does not isolate the contribution of that kernel from other differences between the simulators.

## Timing method

Each configuration ran sequentially in a fresh isolated worker, so large statevectors did not coexist. No profiler or additional test workload ran concurrently. Idle sleep was prevented throughout the benchmark.

The Rust timer includes request encoding, process startup, JSON parsing, worker-pool creation, circuit compilation, state allocation and initialization, execution, and probe output. The Aer timer includes backend execution and result retrieval, including state allocation and initialization, fusion, circuit execution, and the same probe output. Aer Python imports, backend construction, and Python circuit construction occur before its execution timer. Total worker wall time is also retained in the raw results.

No full-state export or full norm calculation is included in the large-run timing. Peak resident memory is sampled approximately once per second across each worker and its children; it is a sampled process footprint, rather than an exact allocator peak.

The full-run order was Rust FP64, Aer FP64, Rust FP32, and Aer FP32. CPU placement and unrelated desktop activity were not controlled. With one sample per configuration and a fixed order, the comparison does not quantify run-to-run variance or exclude ordering effects.

## Calibration and actual duration

The initial target was approximately 20 minutes for Rust FP64. A 32-layer, 28-qubit pilot took **79.077 seconds**, selecting 486 layers with an estimated duration of **1,200.98 seconds**.

The complete Rust FP64 run actually took **1,582.79 seconds**, or 26:22.79—approximately **31.8% longer** than the pilot estimate. The selected circuit was retained for every configuration to preserve an identical workload. No specific cause of this calibration drift was established.

## Numerical accuracy

Before the long runs, all **4,096 complex amplitudes** of a 12-qubit, 32-layer entangled circuit were compared against Aer FP64. Every configuration passed the configured checks.

| Configuration | Relative L2 error against Aer FP64 across the full small state |
| --- | --- |
| Rust FP64 | 2.79 × 10⁻¹⁵ |
| Rust FP32 | 1.22 × 10⁻⁶ |
| Aer FP32 | 1.52 × 10⁻⁶ |

For the complete 28-qubit circuit, the same **66 complex amplitude probes** were compared directly against Aer FP64. Comparing complex values checks phase differences as well as amplitude differences.

| Configuration | Relative L2 error across probes | Maximum absolute amplitude error |
| --- | --- | --- |
| Rust FP64 | 1.66 × 10⁻¹⁴ | 2.25 × 10⁻¹⁸ |
| Rust FP32 | 6.54 × 10⁻⁶ | 7.88 × 10⁻¹⁰ |
| Aer FP32 | 4.58 × 10⁻⁶ | 6.86 × 10⁻¹⁰ |

All large-run probe checks passed. The FP32 outputs differ from the FP64 reference, as the recorded errors show. The large-circuit checks sample 66 amplitudes and do not establish full-state fidelity.

## Memory activity and clocks

| Configuration | System swap-ins in pages | System swap-outs in pages | Sleep or clock gap detected |
| --- | --- | --- | --- |
| Rust FP64 | 12 | 0 | No |
| Aer FP64 | 101 | 1,076 | No |
| Rust FP32 | 24 | 0 | No |
| Aer FP32 | 48 | 0 | No |

With a 16 KiB system page size, the swap-outs recorded during Aer FP64 amount to **16.81 MiB**. These are system-wide counters and cannot identify whether the simulator or another process paged. They are retained as a limitation of the desktop comparison; no paging penalty has been estimated or subtracted from the timings.

## Interpretation

The current Rust implementation completed this workload faster than Qiskit Aer at both precisions. Rust FP32 had the shortest observed runtime and reduced state storage by half relative to FP64, while its large-circuit probe error remained approximately 6.54 × 10⁻⁶ relative to Aer FP64.

The results support using the automatic specialization for this recognized gate pattern and show a substantial advantage for this circuit family on the tested M4. They do not establish the same ratios for arbitrary circuits, other hardware, or different thread counts. Broader circuit coverage and alternating repeated runs would be needed to assess that wider performance claim.

## Reproducibility artifacts

All benchmark artifacts are saved under `benchmark-results/2026-11-14-28q-precision-comparison/` in the local `qiskit_rust` repository.

| File | Contents |
| --- | --- |
| `results.json` | Raw timings, calibration, backend settings, accuracy, and memory counters |
| `request.json` | Exact selected circuit and probe indices |
| `metadata.json` | Hardware, compiler, source hashes, binary hash, and environment details |
| `verification.json` | Source, binary, request, precision, threading, and clock checks |
| `requirements.txt` | Pinned Python environment |
| `run.py` and `worker.py` | Checkpointed measurement harness and isolated simulation worker |
| `statevector` | Exact Rust release binary used |
| `REPORT.txt` and `report.py` | Detailed technical report and its generator |

The source, preserved binary, and circuit-request hashes were checked after the runs and matched the recorded metadata.

