# Metal FP32 statevector prototype on Apple M4

November 20, 2026. The Metal prototype ran the 27-qubit, 3,243-gate random-matching circuit in **22.17 seconds**. Rust CPU took **33.83 seconds** in the paired FP32 run, and Qiskit Aer CPU took **100.98 seconds** in a separate FP32 run of the same circuit. Metal was **1.53× as fast as Rust CPU** and **4.55× as fast as Aer CPU** on this workload. [Metal and Rust timings](benchmark-results/metal-fp32-27q-long/results.json) · [Aer timing](benchmark-results/metal-fp32-27q-long/aer.json)

## Why start with fused rotations

The Instruments profile of the 26-qubit Rust FP64 random-matching circuit attributed **62.8% of sampled CPU time** to fused execution. A structured two-qubit rotation updates independent groups of four amplitudes, so the same operation can run across many GPU threads. Metal's shading language supports FP32 arithmetic, and the prior FP64 comparison stays on the CPU because Metal has no native `double` arithmetic. [CPU profile](benchmark-results/profile-suite-random26-f64/REPORT.md) · [Metal Shading Language specification](https://developer.apple.com/metal/Metal-Shading-Language-Specification.pdf)

## What was built

- A standalone Metal host executable reads FP32 probe-only JSON requests. It compiles the kernels at startup and translates H, CX, Ry, and Rz into dispatches. It combines adjacent Ry/Rz operations on one wire and pairs those combinations on two wires when the gate sequence permits. [Metal host](tools/metal_statevector.mm)
- Metal kernels update disjoint pairs or quads of complex FP32 amplitudes in place. The host initializes one shared buffer to the zero basis state, schedules the gates in order, and reads the requested amplitudes after the GPU finishes. At 27 qubits the buffer holds 134,217,728 complex amplitudes and occupies **1 GiB**. [Metal kernels](tools/metal_statevector.metal)
- A comparison runner builds both executables, sends them the same seeded random-matching circuit, alternates engine order across trials, and saves the exact request, samples, error metrics, operation counts, and build hashes. The CPU path uses four requested workers and the fused multiply-add optimization in `e0f9f31`. Both paths use fusion and leave CX compression disabled. [Benchmark runner](tools/benchmark_metal.py)

## Timing and accuracy

| Qubits | Layers | Source gates | Measured requests per engine | Rust CPU FP32 | Metal GPU FP32 | Qiskit Aer CPU FP32 | Aer time divided by Metal time |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | 8 | 252 | 1 | 2.07 ms | 7.99 ms | 4.26 ms | 0.53× |
| 27 | 8 | 563 | 3 | 5.81 s | 4.22 s | 14.58 s | 3.45× |
| 27 | 48 | 3,243 | 1 | 33.83 s | 22.17 s | 100.98 s | 4.55× |

The Aer measurements used Qiskit Aer FP32 on the CPU with fusion enabled and the
same source gates and amplitude indices as Metal. They were collected separately
from the paired Rust and Metal runs. Aer used one state-update worker at 12
qubits and four for the 27-qubit measurements. A ratio above one favors Metal.
[12-qubit Aer measurement](benchmark-results/metal-fp32-smoke/aer.json) · [563-gate Aer measurements](benchmark-results/random27-current/results.json) · [3,243-gate Aer measurement](benchmark-results/metal-fp32-27q-long/aer.json)

Times cover one fresh request from submission through probe response. They include JSON handling, allocation, initialization, gate compilation and execution, and probe output. Binary builds, process startup, and Metal pipeline compilation happen before the timing starts. The eight-layer cases each had one workload warmup. The 48-layer case had one measured request per engine after both processes were initialized.

On the 563-gate circuit, the three Metal samples ranged from **4.09 to 4.33 seconds** and the three Rust samples ranged from **5.78 to 6.02 seconds**. On the 3,243-gate circuit, Metal issued **1,323 operations**, including **624 structured two-qubit kernels**. Rust compiled the same source circuit to **1,326 execution operations**. The recorded GPU command-buffer interval was **22.12 seconds** out of **22.17 seconds** of elapsed request time. That points toward measuring kernel and memory costs before investing in host-side overhead for this workload. The system swap-out counter stayed unchanged during the Metal benchmarks.

The 12-qubit test compared all **4,096 complex amplitudes** against Rust FP32. Both 27-qubit circuits compared **67 deterministic complex-amplitude probes** per request against the same reference. Maximum observed relative L2 errors across the checked amplitudes were **4.89e-7** at 12 qubits, **7.11e-7** for the shorter 27-qubit run, and **2.01e-6** for the longer run. All checks passed, along with the 33 Python tooling tests. The larger results establish agreement at the sampled amplitudes.

## 29-qubit follow-up

All three FP32 backends ran the same 605-gate, eight-layer random-matching circuit with fusion enabled. Rust and Aer used four CPU workers. The benchmark ran one fresh request per backend sequentially and checked the Metal and Aer results against 67 Rust FP32 amplitudes. [Exact circuit request](benchmark-results/fp32-29q/request.json) · [Raw timings and checks](benchmark-results/fp32-29q/results.json)

| Backend | Precision | State size | First run | Rerun |
|---|---|---:|---:|---:|
| Rust CPU | FP32 | 4 GiB | 30.04 s | 29.04 s |
| Qiskit Aer CPU | FP32 | 4 GiB | 69.73 s | 75.60 s |
| Metal GPU | FP32 | 4 GiB | 17.84 s | 18.24 s |
| Rust CPU | FP64 | 8 GiB | Skipped for memory headroom | Skipped for memory headroom |
| Qiskit Aer CPU | FP64 | 8 GiB | Skipped for memory headroom | Skipped for memory headroom |

Metal was **1.68× as fast as Rust CPU** and **3.91× as fast as Aer CPU** in the first run. The system recorded swap-outs during that Metal window. In the rerun, Metal was **1.59× as fast as Rust CPU** and **4.14× as fast as Aer CPU**, with no new swap-outs recorded. All amplitude comparisons passed. Available memory before the FP64 checks was at most **6.97 GiB**, below the **8 GiB** state size. [Rerun measurements and memory checks](benchmark-results/fp32-29q-rerun/results.json)

## Reproduce the measurements

Run from the repository root with Xcode, access to a Metal GPU, and the benchmark Python environment. Use a new output directory each time. [Environment setup](BENCHMARKING.md#python-environment)

```sh
.venv-rebuild/bin/python tools/benchmark_metal.py --qubits 12 --layers 8 \
  --repetitions 1 --output benchmark-results/metal-fp32-smoke

.venv-rebuild/bin/python tools/benchmark_metal.py --qubits 27 --layers 8 \
  --output benchmark-results/metal-fp32-27q

.venv-rebuild/bin/python tools/benchmark_metal.py --qubits 27 --layers 48 \
  --warmup 0 --repetitions 1 --output benchmark-results/metal-fp32-27q-long
```

## Remaining work

1. **Measure when Metal pays off.** Repeat the 48-layer comparison with warmups and at least three samples per engine. Sweep qubit count and circuit depth using the same FP32 source requests. The current measurements show a CPU win at 12 qubits and a GPU win at 27 qubits.
2. **Find the GPU bottleneck.** Use Metal GPU profiling to measure time per kernel family, effective memory traffic, and GPU occupancy. Test threadgroup size and index layout first, then consider fusing more operations per statevector pass if the measurements show memory traffic dominates.
3. **Expand numerical coverage.** Compare full states for a broader set of small circuits, including reversed CX wires and rotations on widely separated wire pairs. For deep 27-qubit cases, compare additional deterministic probes and check norm drift against an independent FP64 reference.
4. **Integrate a supported backend if the gains hold.** Extend gate and output coverage, share circuit compilation where practical, and expose an opt-in Metal backend through the Rust CLI. Define when to select CPU based on measured crossover, precision, gate coverage and available GPU memory. The current entry point is the standalone FP32 benchmark.
