# Rust CPU and cache profile

Measured 2026-11-08 on the Apple M4 host, using the saved 25-qubit, 34,750-gate fusion-insensitive workload. **The pair traversal and controlled swaps dominate CPU time. Data locality and the CX traversal are the clearest optimization targets; JSON handling is negligible in the sampled window.**

## Build and measurement scope

The profiling binary is an optimized release build (`opt-level=3`) with line symbols and frame pointers, in a separate `target/profiling` directory. The existing benchmark binary and simulator implementation were not changed. Symbols/frame pointers can change performance, so these results identify hotspots rather than replace the earlier 249.933-second timing.

Instruments Time Profiler sampled a 10-second early window of the exact saved request, FP64 and four Rayon workers. Separate two-second CPU-bottleneck and L1D-counter recordings used the same request. Limits intentionally terminate the launched process; no completed four-minute run or end-to-end profiled latency is claimed. Counter summaries filter to the simulator process and timestamps at or after one second to reduce startup contamination. These windows cover repeated CX execution, not a thermal profile of the entire run.

An initial 30-second counter capture failed while saving due to insufficient disk space. Its identified 4.5 GiB temporary ktrace and incomplete output were removed. The bounded two-second recordings succeeded. Raw XML exports are gzip compressed; usable traces and summaries are retained.

## Where CPU time goes

The ten-second trace contains 35.819 seconds of weighted samples across threads. These are **sampled CPU-time shares**, not fractions of wall time or independent instruction costs.

| Sample attribution | Share |
|---|---:|
| Pair-traversal subtree, including callbacks and Rayon beneath it | 99.66% |
| Rayon outside that subtree | 0.25% |
| Other startup/allocation/runtime paths | 0.08% |
| JSON parsing/serialization | 0.01% |

The inclusive traversal figure includes a small amount of initial H preparation. The dominant exclusive source attributions are:

| Leaf source attribution | Share of weighted samples |
|---|---:|
| CX control-bit test (`src/lib.rs:250`) | 44.29% |
| Inner paired traversal (`src/execution.rs:124`) | 18.15% |
| Inlined `std::mem::swap` | 10.30% |
| Small-stride traversal (`src/execution.rs:108`) | 7.45% |
| Enumerate/zip iterator sites, combined | about 13.7% |

Optimized inline line attribution and sampling skid can move costs between neighboring operations. A sample on the control test does not prove that a branch misprediction consumed that time, and an iterator source location does not prove an iterator abstraction survives as runtime overhead. These figures identify the hot loop; they are not an additive model of individual instructions.

About 84.8% of sample weight was on performance cores and 15.2% on efficiency cores. The OS migrated work; four requested workers do not imply fixed assignment to four performance cores. This is another potential contributor to timing variation, not an isolated explanation of previous changes.

## Hardware cache counters

The installed Instruments L1D Cache Metrics configuration was selected explicitly. Its exported schemas confirm `metrics "l1d_metrics" EL0`, rather than just CPU Bottlenecks. The vendor metric definitions describe speculative load/store operations, L1D misses, and dirty-line writebacks toward shared L2.

For simulator rows after the one-second cutoff:

| L1D metric | Event count |
|---|---:|
| Load µops, speculative | 20,232,328,248 |
| Load-miss events, speculative | 2,148,406,870 |
| Store µops, speculative | 5,450,087,093 |
| Store-miss events, speculative | 2,015,131,054 |
| L1D dirty-line writebacks | 1,068,289,216 |

The event-count ratios are **10.62% load misses per load µop** and **36.97% store misses per store µop**. They are not whole-program hit rates, unique cache lines, percentages of time stalled, or L2/DRAM miss rates. Speculation, prefetching, instruction mix, and the instrument's event definitions matter. Absolute totals apply only to the selected recorded intervals.

The separate CPU-bottleneck capture reports an approximately **71.8% Instruction Processing Bottleneck metric**, versus 2.1% Instruction Delivery and 0.5% Discarded. These are Apple's instruction-bandwidth metrics, not a wall-time breakdown. Together with the cache sweep they support a data-access/processing bottleneck; they do not establish that all stalls are DRAM bandwidth stalls. No L2 miss rate, LLC miss rate, or direct memory-controller bandwidth was captured.

## Cache-size and access-pattern experiments

Reported cache geometry:

- Cache line: 128 bytes, holding eight FP64 complex amplitudes.
- Performance-core L1D: 128 KiB per core.
- Performance-cluster shared L2: 16 MiB.
- Efficiency-cluster shared L2: 4 MiB.
- The 25-qubit FP64 state is 512 MiB, 32 times the performance L2 capacity. One CX swaps half the amplitudes, so even that active subset greatly exceeds L2.

The new `examples/profile_cache.rs` pre-touches the entire state, reuses the executor and allocation, warms each gate batch, then measures three repeated batches. Allocation, initialization, and output are outside timers. Repeated identical CX gates preserve the same memory-access work even when pairs of gates cancel mathematically. No value-dependent shortcut is used. Four-thread mode forces the parallel path; very small states expose fewer than four tasks. One-thread mode uses the existing distinct serial implementation, so that comparison is not a pure thread-scaling experiment.

Selected four-thread sweep rows, using target n−1 and control (target+n/2) mod n:

| State | Gate latency | Logical throughput |
|---|---:|---:|
| 4 MiB (18 qubits) | 35.70 µs | 109.4 GiB/s |
| 16 MiB (20 qubits) | 89.06 µs | 175.4 GiB/s |
| 64 MiB (22 qubits) | 822.78 µs | 76.0 GiB/s |
| 256 MiB (24 qubits) | 3,474.38 µs | 72.0 GiB/s |
| 512 MiB (25 qubits) | 6,942.74 µs | 72.0 GiB/s |

Logical throughput counts useful amplitude reads plus writes: a CX reads and writes half the state, totaling one state-size of logical bytes per gate. It is **not measured DRAM bandwidth**. Cache-line transfers, write allocation, prefetch traffic, and repeated accesses can change physical traffic. Target/control positions also change across sizes, so the table is evidence consistent with a capacity effect, not a perfectly isolated cache-capacity experiment.

A second sweep holds the 25-qubit state and four workers fixed, and measures every pair used by the actual stride-CX workload:

| Target | Control | Median latency per CX |
|---:|---:|---:|
| 12 | 0 | 10.664 ms |
| 13 | 1 | 10.594 ms |
| 14 | 2 | 9.571 ms |
| 15 | 3 | 5.921 ms |
| 16 | 4 | 5.351 ms |
| 17 | 5 | 5.255 ms |

The 25 pair medians average 6.718 ms per gate in this microbenchmark. This is not the full-circuit runtime: it repeats one pair at a time, has explicit warmups, and excludes request overhead.

Control bits 0–2 select stripes within a cache line. A cache line can be fetched and dirtied even though only some amplitudes in it are swapped, reducing useful bytes per line. For larger control bits, entire lines can be inactive. The timing gap is consistent with this mechanism, but no per-target hardware-counter experiment was done to isolate it from scheduling and other effects. High targets also use the nested Rayon chunk path, and several high-target pairs are slower than the best pair.

## Concrete next optimizations

1. **Specialize CX traversal for the control bit.** `pairs` visits every target-bit pair and the callback checks control each time, although only half the pairs swap. A control-aware traversal could visit only active pairs and avoid scheduling completely inactive blocks. It must preserve disjoint mutable slices and correctness for control above/below target.
2. **Tune the paired chunk footprint.** GRAIN is 4,096 amplitudes: two FP64 chunks total 128 KiB, equal to a performance core's entire L1D before stack and other data. Test smaller chunks against scheduling cost; this profile does not establish the best grain value.
3. **Address low-control cache-line utilization.** Compare specialized contiguous/striped CX kernels and cache blocking. Measure physical counters again rather than assuming wider SIMD alone removes unnecessary line transfers.
4. **Treat gate fusion as a separate optimization.** It remains promising for the rotation-heavy workloads where Aer won. The present CX workload limits opportunities to reduce repeated state passes through fusion.

The sample gives little reason to prioritize JSON handling or the outer request/pool setup for this long workload. Rayon coordination inside the traversal is included in the inclusive subtree; it has not been shown to be zero-cost. No simulator optimization has been implemented in this profiling task.

## Reproduction and artifacts

Build the optimized profiling binaries:

```sh
CARGO_TARGET_DIR=target/profiling CARGO_PROFILE_RELEASE_DEBUG=1 \
RUSTFLAGS='-C force-frame-pointers=yes' \
  cargo build --release --locked --bin statevector --example profile_cache
```

Use `xcrun xctrace record` with template `Time Profiler`, a 10-second limit, the saved request as `--target-stdin`, and `target/profiling/release/statevector` as the launched binary. For cache counters, generate the installed-template variant:

```sh
python3 tools/make_l1d_template.py benchmark-results/rust-profile-25q/l1d-metrics.tracetemplate
```

Record that template with a two-second limit, then export `MetricTable` using `xctrace export`. Profiling requires Instruments process/counter access. Verify the exported counting mode, because changing an unrelated legacy profile setting did not select L1D events.

```sh
target/profiling/release/examples/profile_cache > cache-sweep.jsonl
target/profiling/release/examples/profile_cache --stride-profile > stride-target-sweep.jsonl
python3 tools/summarize_time_profile.py
python3 tools/summarize_cpu_metrics.py benchmark-results/rust-profile-25q/l1d-metrics.xml.gz
```

Artifacts:

- [Build, source/binary hashes, request hash, and cache geometry](benchmark-results/rust-profile-25q/metadata.json)
- [CPU sample summary](benchmark-results/rust-profile-25q/time-summary.json)
- [L1D event summary](benchmark-results/rust-profile-25q/l1d-metrics.summary.json)
- [Pipeline metric summary](benchmark-results/rust-profile-25q/counter-metrics.summary.json)
- [Cache-size sweep](benchmark-results/rust-profile-25q/cache-sweep.jsonl)
- [Exact-workload pair sweep](benchmark-results/rust-profile-25q/stride-target-sweep.jsonl)
- Native `.trace` files and compressed raw XML exports are in the same directory.

Validation: all 22 Rust tests and 10 Python tooling tests passed; formatting and Clippy with warnings denied passed. The profiling example and analysis scripts compile/parse successfully. The simulator's core source hash matches the long benchmark baseline.
