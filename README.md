# Minimal Rust state-vector simulator

Set up the wheel-based environment and run the comparison from the repository
root. The pinned dependencies were tested with Python 3.9.6 on ARM64 macOS.

```sh
python3 -m venv .venv-rebuild
.venv-rebuild/bin/python -m pip install -r tools/requirements-retry-aer.txt
./compare
```

The runner builds the Rust release binary, compares it with Python Qiskit
Statevector and Qiskit Aer, validates the results, and saves a report, CSV and
raw samples. See [BENCHMARKING.md](BENCHMARKING.md) for timing, precision,
threads and fusion controls.

The Rust implementation was reconstructed on November 17, 2026 from the surviving
tests and reports. Build and run it with:

```sh
cargo test --locked --all-targets
cargo build --release --locked
cargo run --release --example bell
echo '{"num_qubits":2,"threads":4,"fusion":true,"gates":[{"gate":"h","target":0},{"gate":"cx","control":0,"target":1}]}' | target/release/statevector
```

For the broader precision and worker-count parity suite, use the same Python
environment. If you only need parity, install `tools/requirements-rebuild.txt`.

```sh
.venv-rebuild/bin/python tools/verify_rebuild.py
```

This harness checks all three storage precisions, serial/threaded execution, and optimization
on/off against Qiskit's full state, then benchmarks identical FP64 circuits.
See [the reconstruction results](REBUILD_RESULTS.md) and the
[subsequent kernel optimizations and matched Aer comparison](OPTIMIZATION_RESULTS.md).
A CPU simulator with selectable FP64, FP32, and experimental FP16 storage.
The engine is Rust. Python provides reference comparisons and profiling tools;
the unified runner uses installed packages, while the historical runners require
the historical Qiskit source checkout.

Supported: zero-state initialization, H/X/Y/Z/S/T, RX/RY/RZ, CX/CZ, arbitrary finite
2×2 matrices, full probabilities, and norm checks. Qubit zero is the least-significant
amplitude-index bit. Gates update a contiguous buffer in place, using safe Rust.
Matrices supplied to `Gate::U` must be unitary if norm preservation is desired;
matrix finiteness is checked but unitarity is not.

This version has one crate, serial reference kernels, configurable Rayon workers,
CX compression, and gate fusion. SIMD-specific kernels, sampling/collapse, and GPU
execution remain future work.

Z, S, T, and RZ use specialized diagonal paths automatically. Z/S operate directly
on stored signs and components, including FP16; T updates only target-one amplitudes;
RZ applies two phase multiplications per pair. `Gate::U` retains the general matrix
implementation as a reference. See [the diagonal optimization results](DIAGONAL_OPTIMIZATION_REPORT.md).

## Choose precision

Precision refers to each real/imaginary component, not the whole complex amplitude.

| Mode | Storage per complex amplitude | Gate arithmetic | Probability/norm reduction |
|---|---:|---|---|
| `f64` (default) | 16 bytes | FP64 | FP64 |
| `f32` | 8 bytes | FP32 | FP64 |
| `f16` (experimental) | 4 bytes | FP32, rounded to FP16 on writeback | FP64 |

In Rust, select `StateVector64`, `StateVector32`, or `StateVector16` when creating
the state. `StateVector` remains an alias for `StateVector64` for compatibility.
These aliases use one generic implementation specialized at compile time.

```rust
use qiskit_rust::{Gate, StateVector32};

fn main() -> Result<(), qiskit_rust::SimError> {
    let mut state = StateVector32::new(10)?;
    state.apply(&Gate::H { target: 0 })?;
    Ok(())
}
```

For the CLI, add `"precision":"f32"`, `"f64"`, or `"f16"` to the JSON request.
Each request selects a fresh state; this does not convert an already-running state.
The state stays in its selected storage format during execution: FP16 does not
allocate a full-size FP32 shadow buffer. Coefficients are prepared in FP64 then
rounded to the selected arithmetic type before the gate kernel. Exported JSON
amplitudes are promoted to FP64 for transport, outside the timed region.

FP16 is IEEE binary16, not BF16. It loses information on every write, can underflow
small amplitudes, and can accumulate substantial error in deep circuits. Norm drift
is reported without automatic renormalization. FP16 is an accuracy-qualified
experiment, and smaller storage does not guarantee faster execution. Unsupported
precision names are rejected rather than silently falling back.

## Choose CPU threads

`apply` and `apply_all` remain serial. To use a persistent worker pool, create a
`CpuExecutor` and reuse it across states and circuit runs:

```rust
use qiskit_rust::{CpuExecutor, Gate, StateVector32};

fn main() -> Result<(), qiskit_rust::SimError> {
    let executor = CpuExecutor::new(4)?;
    let mut state = StateVector32::new(18)?;
    executor.run(&mut state, &[Gate::H { target: 17 }])?;
    Ok(())
}
```

One thread uses the serial path without a worker pool; zero threads is an error.
By default, states with fewer than 65,536 amplitudes stay serial even when the
executor has multiple workers. This is a provisional scheduling threshold, not
a hardware-independent optimum. `with_parallel_threshold(0)` forces the parallel
path for testing, and a larger threshold can avoid overhead on cheap workloads.

Workers receive disjoint amplitude pairs using safe mutable slices. Low target
bits are grouped into coarse chunks. High target bits split corresponding lower
and upper slices into paired chunks, so even the highest bit can use multiple
workers. CX and CZ traverse active control-one pairs. Gates retain their order
and coefficients, and norm and
probability reductions remain serial FP64. Threaded gate results are tested for
exact agreement with the serial implementation in all three storage modes.

The CLI accepts `"threads":4` and optional `"parallel_threshold":65536`. The default
remains one thread. A request creates one executor outside the timed region and
reuses its workers for preflight, warmup, and benchmark repetitions. Each request
reports the configured pool size; a small state can still take the serial fallback.

## Run the Rust code

```sh
cargo test --locked
cargo run --example bell
cargo build --release --locked
```

The library has no Python or system BLAS dependency. State buffers default to a
256 MiB limit (up to 24 qubits at FP64, 25 at FP32, 26 at FP16); `StateVector::with_memory_limit` allows an
explicit override. Checked dimensions and fallible allocation reject oversized
requests. The budget covers the state buffer, not JSON, probabilities, Qiskit's
scratch memory, or overall resident memory.

## Optional circuit optimization and fusion

Add `"optimize": true` to a CLI request to compile the input once before
preflight, warmups, and timed repetitions. Optimization is disabled by default. Add `"fusion": true` to enable dense
one-/two-qubit fusion independently, or combine both flags.
The response adds `compilation.seconds` and `compilation.stats` (input/output
gate counts, CX-run count, and compressed-run count). The existing `seconds`
samples measure execution of the compiled gates and exclude compilation;
end-to-end request latency includes compilation.

The native Rust compiler represents each consecutive CX-only run as a small
binary linear map and uses exact Gauss-Jordan row elimination to synthesize
an equivalent CX sequence. It keeps the original run unless the replacement
has fewer gates. During CX compression, every non-CX gate, including CZ, is a
boundary and remains unchanged. Optional dense fusion runs after this step.
CX compression reduces gate count; it does not use a hardware cost model or
guarantee faster execution on every circuit.
No Python, BLAS, or Qiskit dependency is required for compilation or execution.

Library callers can compile once and reuse the immutable circuit:

```rust
use qiskit_rust::{CompiledCircuit, CpuExecutor, Gate, StateVector64};

fn main() -> Result<(), qiskit_rust::SimError> {
    let gates = vec![Gate::Cx { control: 0, target: 1 }; 100];
    let circuit = CompiledCircuit::new(2, &gates)?;
    let executor = CpuExecutor::new(4)?;
    let mut state = StateVector64::new(2)?;
    executor.run_compiled(&mut state, &circuit)?;
    Ok(())
}
```

For fusion, use `CompiledCircuit::with_options(num_qubits, &gates,
CompileOptions { optimize_cx: true, fusion: true })` and execute with
`CpuExecutor::run_compiled`. `CompiledCircuit::new` retains CX compression only.
`gates()` returns the gates after CX compression but before fusion; executing
that slice directly bypasses dense fusion.

The fusion collector groups consecutive gates on at most two qubits, with a
32-gate cap per block. It uses FP64 composition without removing global phase.
One-qubit blocks need at least two gates; two-qubit blocks need at least four.
Blocks without H/RX/RY/U keep their specialized permutation/diagonal paths.
Each accepted block executes as a 2x2 or 4x4 matrix in one statevector pass,
using the selected storage and arithmetic precision, disjoint mutable slices,
and only fixed-size local scratch. No full-state copy or dense full-system
operator is built. These conservative rules are not a calibrated hardware cost
model and do not guarantee a speedup for every circuit.

The compilation statistics include `execution_operations`, `fused_blocks`, and
`fused_source_gates`; `output_gates` remains the gate count before dense fusion.
Statistics describe the compiled plan. Nonfinite FP64 compositions keep their
source gates. If fused coefficients overflow the selected compute precision,
execution falls back to those source gates. Source-gate validation still occurs
before any state mutation. Nonunitary finite U matrices remain supported.

Fusion changes arithmetic order and removes intermediate storage roundings,
particularly in FP16, so fused results are numerically equivalent within error
budgets rather than bit-identical. Disable fusion for original per-gate rounding.
CX compression alone still preserves the stored amplitudes exactly.

Compilation validates the entire original circuit before rewriting, uses at most
one machine-word bitset per qubit for the binary map, and requires fewer than
`usize::BITS` qubits. Execution rejects a compiled-circuit/state register-width
mismatch before mutation. CX compression only permutes stored amplitudes, so
it preserves FP64, FP32, and FP16 values without introducing new rounding.

On the saved 25-qubit FP64 CX-heavy workload, native compilation reduced
34,175 gates to 347. Three fresh-state requests had a median latency of 2.469 s,
including compilation (about 0.2 ms), and all 258 probes matched the original
run exactly. This is circuit compression, not a per-gate kernel speedup;
see the [measurement details](benchmark-results/2026-11-12-native-compiler/REPORT.md).

On a separate 25-qubit FP64, four-thread rotation-heavy circuit, fusion alone
reduced median request latency from 5.568 s to 3.288 s (1.69x), including
compilation, with CX compression disabled. See the [fusion implementation and
validation report](benchmark-results/2026-11-12-fusion/REPORT.md).

## Set up the historical Qiskit source checkout

The current repository has a `.gitmodules` declaration and lacks a tracked
submodule entry. To use the historical source-based tools, check out the
revision recorded in the original Aer comparison manually. [Historical Aer report](AER_25_QUBIT_COMPARISON.md)

```sh
git clone https://github.com/Qiskit/qiskit.git vendor/qiskit
git -C vendor/qiskit checkout --detach 9ab2106352f603bf411e1387a758f6d9667c7c1c
python3 -m venv .venv
.venv/bin/python -m pip install -e ./vendor/qiskit
```

Use Python 3.11 or newer, a Rust toolchain meeting the checkout's manifest
minimum, and the platform's native compiler and linker. The editable installation
builds Qiskit's native extension. The legacy comparison script verifies that
imports resolve inside `vendor/qiskit`.

## Historical source-based parity tests

```sh
.venv/bin/python tools/compare_qiskit.py parity
# Select a subset:
.venv/bin/python tools/compare_qiskit.py parity --precisions f32 f16
# Exercise selected worker counts (the default is 1, 2, and 4):
.venv/bin/python tools/compare_qiskit.py parity --threads 1 4
```

The harness builds Rust in release mode and compares every complex amplitude,
probabilities, and norm against `qiskit.quantum_info.Statevector`. It covers empty
states, each supported gate at several bit positions, both control/target orders,
arbitrary complex unitaries, GHZ states, seeded random circuits, and uncompute.
All three modes are compared against a Qiskit FP64 reference, including global phase.
The small-circuit suite uses the following explicit regression budgets; they do not
guarantee accuracy for arbitrary circuit sizes or depths:

| Mode | Amplitude/probability absolute tolerance | Relative tolerance | Norm drift tolerance |
|---|---:|---:|---:|
| FP64 | `1e-11` | `1e-11` | `1e-11` |
| FP32 | `3e-6` | `3e-5` | `1e-5` |
| FP16 | `3e-3` | `2e-2` | `2e-2` |

The harness also reports worst amplitude error, norm drift, minimum normalized
fidelity, and total variation distance of normalized probability distributions.
Normalization is used only to calculate comparison metrics; the simulator's state
is not renormalized.
Rust tests separately check known states, exhaustive small controlled-gate basis
transitions, a dense reference matrix application, inverse rotations, invalid inputs,
and memory bounds. Parity tests need Python; `cargo test` remains standalone.
Precision tests also verify storage sizes, per-mode allocation limits, FP32
coefficient rounding, and FP16 subnormal preservation/underflow.
Parity runs force the parallel threshold to zero for multiworker cases, so small
circuits actually exercise the threaded kernels. A 14-qubit case exercises work
partitioning within large-stride blocks. Rust tests also cover the default fallback,
the default parallel path, all control/target orders, and pair-coverage invariants.

## Performance tests

For a **matched FP64 comparison against optimized Qiskit Aer at 25 qubits**:

```sh
.venv/bin/python -m pip install -r tools/requirements-aer.txt
.venv/bin/python tools/benchmark_aer.py --qubits 25 --layers 8 --threads 1 4 --repetitions 3 --warmup 1
```

This runs two seeded 521-gate circuits: maximally entangled Bell pairs scrambled
within each side of the 12|13 partition, and random unrestricted entangling matchings
with non-Clifford rotations on every qubit. Full states are compared against Aer
outside the timed region; exact reduced-state entropies check entanglement. The
Bell construction preserves 12 ebits across the half partition by construction.
Temporary binary state exports avoid multi-gigabyte JSON and are deleted after
verification.

Primary measurements use equal requested CPU thread counts, FP64 on both engines,
Aer CPU `statevector` mode, default Aer fusion enabled, and equivalent amplitude-probe
output. Fresh-state allocation and initialization are included on both sides.
Rust request timing includes parsing and pool construction; Aer request timing
includes job submission and internal circuit handling. Process startup and circuit
construction are excluded. Trials alternate engine order. Separate worker processes
set OpenMP limits before importing Aer, and the runner asserts Aer's reported
state-update thread count. Fusion-disabled Aer timings are diagnostic only.
Results are checkpointed under `benchmark-results/2026-11-08-aer25/`.
See [measured results and methodology](AER_25_QUBIT_COMPARISON.md): Aer was 1.14–1.68× faster on these matched workloads.

For **every integer qubit count from 4 through 30**, use the memory-aware size sweep:

```sh
python3 tools/benchmark_sizes.py --dry-run
python3 tools/benchmark_sizes.py
# Larger states, with live memory checks, four workers, and Qiskit through 20 qubits:
.venv/bin/python tools/benchmark_sizes.py --max-state-mib 4096 --memory-reserve-mib 1024 --qiskit-check --qiskit-max-qubits 20
# Tests for resource planning and the large-state reference model:
python3 -m unittest discover -s tools/tests -v
```

The default range is 4..30 inclusive, in FP64, FP32, and FP16. The default state
budget is 256 MiB; higher counts are explicit `skipped` records, not fabricated
timings. Increase `--max-state-mib` only for a machine with sufficient memory.
Every case is also limited to a quarter of physical RAM and must leave the specified
headroom in the live available-memory estimate. `--physical-memory-fraction` can
explicitly raise that fraction up to one-half. The available-memory check is an
estimate, not an OS reservation. A 30-qubit state alone needs 16 GiB at FP64, 8 GiB
at FP32, or 4 GiB at FP16. Default safety checks therefore require more RAM than the
state payload. `--dry-run` reports the plan without allocating states.

The circuit prepares every qubit with H, then applies `--depth` mixed gates (default
12) to low, middle, and high target bits. Its remaining qubits stay in a uniform
product state. An independent eight-amplitude FP64 reference predicts selected
global amplitudes at every size, avoiding a second exponential allocation. Error
is scaled relative to the expected peak amplitude, so the check stays meaningful
when amplitudes become tiny. Full norm drift is checked separately. This structured
oracle does not certify arbitrary large circuits or replace broader parity tests.

`--qiskit-check` cross-checks the reduced reference with Qiskit for every size.
`--qiskit-max-qubits 20` additionally measures equivalent full Qiskit state-vector
runs through that count, reusing each FP64 reference timing across Rust modes.
Larger Qiskit references are marked skipped; no Qiskit timing is extrapolated.
Without those options the size sweep requires only Python's standard library and
the Rust toolchain. Qiskit comparisons use its `Statevector`, not Aer.

Results go to `benchmark-results/sizes-4-30.json` and are checkpointed after every
case. They include raw gate-execution samples, process wall time, memory estimates,
accuracy metrics, explicit skips/failures, and peak RSS when supported on macOS.
If native RSS instrumentation is blocked, RSS is null and the measurements still
run. Initialization, pool creation, IPC, norm/probe checks, and export are outside
the gate timer. H preparation is inside it, so the total gate count grows as
`num_qubits + depth`. The default uses four workers, three timed samples, and no
additional configured warmup; the CLI still performs an untimed preflight.
Use `--threads`, `--repetitions`, and `--warmup` to expand the matrix.

The completed [4–30-qubit sweep report](SIZE_SWEEP_REPORT.md) includes all 81 planned
size/precision combinations, the measured 30-qubit FP16 case, resource skips, and
raw timings/CSV data.

The [Qiskit runtime comparison](QISKIT_RUNTIME_COMPARISON.md) pairs those Rust
measurements with equivalent Qiskit FP64 circuits through 25 qubits. To extend the
recorded comparisons under the same memory policy:

```sh
.venv/bin/python tools/compare_size_results.py
```

For the earlier target-position and worker-count sweeps:

```sh
.venv/bin/python tools/compare_qiskit.py bench
# A shorter run:
.venv/bin/python tools/compare_qiskit.py bench --qubits 10 14 18 --depth 32 --repetitions 5 --warmup 1
# Compare storage/accuracy/performance across all modes:
.venv/bin/python tools/compare_qiskit.py bench --precisions f64 f32 f16 --qubits 10 14 --output benchmark-results/precisions.json
# Measure thread scaling in every storage mode:
.venv/bin/python tools/compare_qiskit.py bench --precisions f64 f32 f16 --threads 1 2 4 --qubits 14 18 --depth 32 --repetitions 5 --output benchmark-results/threading.json
```

To compare the specialized diagonal paths with equivalent general matrices in the
same release binary (Python standard library only):

```sh
python3 tools/benchmark_diagonal.py
python3 tools/benchmark_diagonal.py --gates rz --precisions f16 --qubits 20 --threads 1 4
```

This comparison includes identical H preparation and alternates which variant runs
first. It reports whole-sequence speedup and preserves raw timing samples.

Benchmarks use identical circuits. The default is FP64 in both implementations;
`--precisions` selects additional Rust modes against the FP64 Qiskit reference.
Lower-precision timing ratios compare different numerical precision and must be
interpreted alongside recorded error and storage sizes. The sweep stays within
24 qubits to bound the FP64 reference state, even if Rust's storage is smaller.
Benchmarks sweep low,
middle, and high target bits through rotation and mixed controlled-gate workloads.
All qubits are placed in superposition before the measured sequence. The requested
`--depth` counts workload gates; each run also includes `num_qubits` preparation H
gates, and the report records the total. The script sets numerical-library thread
limits to one before importing NumPy/Qiskit. Rust uses the requested `--threads`
counts (default one); the Qiskit reference remains limited to one numerical worker.
Multiworker Rust versus Qiskit ratios are therefore not equal-resource comparisons.
Use the separately recorded Rust speedup over one thread to assess thread scaling.

Each implementation measures gate execution internally. State initialization,
Python circuit construction and `to_instruction`, process startup, JSON transport,
and result serialization are excluded. Rust's validation/dispatch and Qiskit's
instruction interpretation plus internal output/scratch allocation remain timed.
Worker-pool construction is also excluded; dispatch and gate synchronization are timed.
This is a library execution comparison, not a matched low-level kernel benchmark.
Warmups are discarded and the initial Rust/Qiskit execution order alternates between
workloads. Timed samples for the deterministic Qiskit FP64 reference are reused across
Rust precision and worker-count combinations for the same workload. Only one reference
state is retained. Raw samples plus medians/min/max and environment metadata are saved to
`benchmark-results/qiskit.json`. Probe amplitudes and norms are checked for every
benchmark case; run the full parity command as well.
Each result records Rust storage/compute/accumulation precision, state bytes,
Qiskit reference bytes, probe error, norm drift, worker count, scheduling threshold,
and `rust_speedup_over_one_thread` when the same sweep includes a one-thread run.
Source-status scans are bounded; `qiskit_dirty: null` means the dirty-state check
was unavailable, rather than that the checkout was clean. Timing samples are saved
before optional source-status metadata is collected.
Speedups can be below one; no minimum performance is asserted. Set
`--parallel-threshold 0` to measure forced scheduling on smaller states.

See [the full test and performance analysis](PERFORMANCE_REPORT.md) for the November 8,
2026 run, including precision tradeoffs, thread scaling, and the norm-reduction fix.

The printed ratio is Qiskit's median divided by Rust's median. **The baseline is
Qiskit's Python `Statevector`, not Qiskit Aer.** These numbers do not establish
performance against Aer. The harness intentionally has no fragile timing threshold
or pass/fail speed requirement. Hardware and memory effects can change results.

## JSON command-line interface

The `statevector` binary accepts one JSON request per line on stdin and emits one
JSON result per line. Complex values are `[real, imaginary]` pairs.

```sh
echo '{"num_qubits":2,"gates":[{"gate":"h","target":0},{"gate":"cx","control":0,"target":1}]}' | target/release/statevector
```

Optional request fields: `precision` (default `"f64"`), `threads` (default one),
`parallel_threshold` (default 65,536 amplitudes), `memory_limit_bytes`, `include_state` (default true),
`probe_indices`, and `benchmark: {"repetitions": 7, "warmup": 2}`. Turn off
`include_state` for large benchmarks to avoid exporting the full state. Responses
include squared norm, optional amplitudes/probabilities, probes, and timing samples.
Invalid requests emit an `error` result and cause a nonzero process exit; subsequent
valid lines are still processed. Unknown JSON fields are rejected.

With the optional `benchmark` object, the CLI resets one allocated state buffer
to the all-zero basis state before each warmup and repetition. The internal
samples measure gate execution, and the response reports the final repetition's
state. The separate `./compare` runner measures fresh-state request latency.

`include_norm: false` omits the full norm scan and returns `norm_sqr: null` for
small-output request benchmarks. The default remains true. Optional
`amplitudes_output` writes interleaved real/imaginary FP64 little-endian values to
the specified file, outside internal benchmark timers. This exports the full
state without JSON expansion; the response records the format and byte count.

A [350-layer highly entangled workload](FOUR_MINUTE_CIRCUIT.md) measured 4 min 5 sec in Rust at 25 qubits, FP64, four threads.

Doubling to [700 layers](DEEP_CIRCUIT_COMPARISON.md) measured 7:55 in Rust and 4:56 in Aer, with the same 25 qubits, FP64, and four threads.

See the [benchmark checkpoint](BENCHMARK_CHECKPOINT.md) and [fusion-insensitive CX comparison](FUSION_INSENSITIVE_COMPARISON.md) for the current status and a controlled fusion diagnostic.

The [scaled fusion-insensitive workload](LONG_FUSION_INSENSITIVE_COMPARISON.md) measured 4:10 in Rust, 4:32 in Aer with fusion/audit enabled, and 4:22 in the fusion-disabled control.

[CPU and cache profiling](RUST_CPU_CACHE_PROFILE.md) identifies the CX traversal hotspot, hardware L1D event ratios, and measured state-size/target effects.

## Automated macOS profiling

Use the Instruments wrapper to profile a saved comparison case or CLI request.
Start with a dry run to confirm the case and capture settings.

```sh
./compare --suite --qubits 20 --layers 256 --families random_matching \
  --output benchmark-results/profile-input

python3 tools/profile_rust_macos.py --benchmark-run benchmark-results/profile-input \
  --case-id 20q-random_matching-f64-4t-fusion-1 --dry-run

python3 tools/profile_rust_macos.py --benchmark-run benchmark-results/profile-input \
  --case-id 20q-random_matching-f64-4t-fusion-1 \
  --output benchmark-results/profile-20q-f64
```

Use the matching FP32 case ID to compare profiles across precisions. A positional
request JSON path also works. The profiler verifies the saved request hash when
selecting a case from a comparison directory.

Requires macOS and full Xcode with Instruments. `rustfilt`, when installed, makes
Rust names readable; otherwise raw symbols remain available. The script builds
release code with optimization level 3, full debug information and frame pointers
in `target/profiling`, leaving the regular benchmark binary untouched. It refuses
to record while another `statevector` worker is active unless explicitly passed
`--allow-concurrent`. Do not use that override for performance comparisons.

Default captures are a 20-second Time Profiler window and separate two-second
CPU-bottleneck, L1 data-cache, data-TLB and instruction-TLB windows.
`--counters` selects modes. `--time-seconds`, `--counter-seconds` and
`--skip-seconds` control the capture windows and
startup exclusion. The script checks the actual selected hardware counting mode
in the exported trace before interpreting its events. Unsupported counters remain
explicit in the counter availability table. Captures stop at the recording limit
or when the request finishes. Use a deep enough circuit to leave useful samples
after the startup cutoff.

Each output directory contains `REPORT.md`, exclusive/inclusive function rankings,
source-line hotspots, thread/P-core/E-core distributions, a hoverable
`flamegraph.svg`, folded stacks, vendor counter summaries and event ratios, raw
Instruments traces, compressed XML, hashes, commands, and memory/thermal context.
Optional disassembly supports checking vectorization and hot-loop instructions.
Counter captures can grow quickly: durations are capped at five seconds, disk
headroom is checked, and existing capture directories are not overwritten.

For offline analysis of existing exports, without building or executing Rust:

```sh
python3 tools/profile_rust_macos.py --analyze-only benchmark-results/profile-20q-f64
python3 -m unittest discover -s tools/tests -p 'test_profile_rust_macos.py' -v
```

The effective profiling request preserves precision, threads, gates, fusion and
CX-compression settings, but suppresses full-state/norm output, removes binary
export paths and disables repeated benchmark requests. The output directory
contains the effective request and records hashes for both versions. CPU samples describe running CPU time,
not wall-time waits; speculative L1 miss/uop ratios are not DRAM miss rates or
stall-time percentages. Use uninstrumented timing runs to validate optimizations.

When circuit fusion is enabled, eligible two-qubit blocks with one Ry followed by one Rz on each wire automatically use the structured arithmetic kernel. Other blocks use the general dense kernel. No additional build feature is required.
