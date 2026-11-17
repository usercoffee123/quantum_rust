# Rust quantum simulator recovery plan

Assessment date: November 16, 2026.

Update November 17, 2026: the Rust source has now been reconstructed. This document
preserves the original assessment and reconstruction specification. See
[REBUILD_RESULTS.md](REBUILD_RESULTS.md) for the implemented state and fresh validation.

This project implemented a Rust CPU quantum statevector simulator and a Python validation and benchmarking suite. Its entire Rust source directory is missing. The surviving tests and documentation specify enough behavior to reconstruct a compatible implementation, including the latest structured fusion optimization. Exact original source and historical benchmark artifacts require a backup or original repository.

## What the project did

The engine stored the complex amplitude of each computational basis state in a contiguous buffer of length `2^num_qubits`. It initialized the register to zero, applied gates in place, and returned amplitudes, probabilities, norms, or selected amplitude probes. Qubit zero was the least significant index bit.

Supported gates were H, X, Y, Z, S, T, Rx, Ry, Rz, CX, CZ, and arbitrary finite complex 2×2 matrices. Arbitrary matrices could be nonunitary. The simulator preserved global phase and did not automatically renormalize states.

| Mode | Bytes per complex amplitude | Gate arithmetic | Norm and probability calculations |
| --- | ---: | --- | --- |
| FP64 | 16 | FP64 | FP64 |
| FP32 | 8 | FP32 | FP64 |
| Experimental FP16 | 4 | FP32 with FP16 writeback | FP64 |

The library had serial execution and a reusable Rayon worker pool. Default parallel scheduling began at 65,536 amplitudes. The CLI defaulted to one worker and supported an explicit threshold. The state buffer defaulted to a 256 MiB allocation limit, with checked dimensions and explicit overrides.

Optional compilation compressed consecutive CX runs using binary linear algebra, then optionally fused gates into one or two qubit operations. The latest version automatically specialized eligible blocks containing one Ry followed by one Rz on each of two wires. Python constructed circuits, compared results with Qiskit and Aer, measured accuracy and performance, and analyzed macOS Instruments profiles. Python was not required by the Rust engine.

## Surviving files and missing components

| Component | Current state | Recovery significance |
| --- | --- | --- |
| `Cargo.toml`, `Cargo.lock` | Present | Crate name, dependencies, versions, binary target |
| Seven files in `tests/` | Present, 36 integration tests | Executable public API and numerical requirements |
| Two Rust examples | Present | Bell state usage and cache benchmark behavior |
| Thirteen Python tools and four Python test files | Present | CLI contracts, circuits, references, profiling and analysis |
| Eleven root Markdown documents | Present | Architecture, historical results, optimization details |
| `src/` | Entirely absent | Simulator implementation must be recovered or rewritten |
| `.git/` | Absent | No local commits, reflog, remote configuration or submodule gitlink |
| `vendor/qiskit/` | Absent | Reference source and native extension must be restored |
| `benchmark-results/` | Absent | Raw measurements, exact requests, saved executables and profiles unavailable |
| `target/` | Absent | No compiled Rust binary available for recovery |
| `.venv/` | Small incomplete copy | Contains metadata and launchers, not a working dependency installation |

Explicitly referenced missing documentation includes `STATE_VECTOR_SIMULATOR_DESIGN.md`, `QISKIT_RUNTIME_COMPARISON.md`, `DEEP_CIRCUIT_COMPARISON.md`, and `LONG_FUSION_INSENSITIVE_COMPARISON.md`. The Qiskit review mentions three original design documents, but the remaining files do not establish all their filenames.

The dependency set is `half`, `num-complex`, `num-traits`, `rayon`, `serde`, and `serde_json`; edition 2024. The declared executable is `statevector` at `src/bin/statevector.rs`.

## Recover originals before reconstructing

The copied Python environment identifies the original project location as:

```text
/Users/ccastorena/Documents/repos/qiskit_rust
```

Its Qiskit editable installation points to that location's `vendor/qiskit`. Search the original machine, repository host, Time Machine, cloud backups, and transfer archives for that folder, especially `src/`, `.git/`, and `benchmark-results/`. This path is a recovery lead from metadata, not a location verified on the original machine.

The `.gitmodules` URL is for the upstream Qiskit dependency, not the missing parent project. Cloning Qiskit will not recover this Rust simulator. Without the parent `.git`, `git submodule update --init --recursive` in the README cannot work as written.

An early Qiskit reference revision is recorded repeatedly as `9ab2106352f603bf411e1387a758f6d9667c7c1c`. It is sufficient to identify the earlier reference checkout if available upstream. The November 14 benchmark instead records Qiskit 2.6.2 and a Python 3.13 environment; do not assume the earlier checkout reproduces that newest experiment.

Historical reports include hashes of concatenated source files. These can verify recovered originals when the file order and revision match; hashes cannot regenerate missing code. Python bytecode belongs to benchmark tooling and contains no Rust implementation.

## Reconstruct the source in this order

The following module layout follows filenames referenced by the tools and symbols in the profiler. Internal boundaries beyond those references are proposed reconstruction choices.

```text
src/
  lib.rs              Public API, errors, gates and state storage
  precision.rs        Amplitude trait, storage types and arithmetic
  execution.rs        CpuExecutor and disjoint pair traversal
  circuit.rs          CX compression and compilation statistics
  fusion.rs           Block collection, composition and quartet execution
  bin/statevector.rs  JSON lines protocol and benchmark timing
```

### 1 Restore the serial mathematical engine

Export `Gate`, `SimError`, `Complex64`, `StateVector`, and `StateVector64`. Implement `new`, `with_memory_limit`, `num_qubits`, `amplitudes`, `state_bytes`, `apply`, `apply_all`, `probabilities`, and `norm_sqr`.

One qubit matrices update amplitude pairs whose indices differ in the target bit. CX swaps a pair only when its control bit is set. CZ negates amplitudes with both operand bits set. Check every gate in a batch before any mutation, including finite coefficients, finite angles, valid qubits, and distinct control and target.

Use checked shifts and byte multiplication, enforce the budget, and allocate fallibly. Zero qubits is valid and stores one amplitude equal to one. Use compensated FP64 norm summation: `tests/statevector.rs` includes a million-amplitude regression that rejects an ordinary inaccurate running sum.

Acceptance: `tests/statevector.rs` and the Bell example.

### 2 Restore precision behavior and diagonal kernels

Export `Amplitude`, `Precision`, `TypedStateVector<A>`, `Complex16`, `Complex32`, `StateVector16`, and `StateVector32`. Re-export `half::f16` through `qiskit_rust::precision::f16`. FP16 complex storage must occupy exactly four bytes and have accessible `re` and `im` fields.

Prepare coefficients in FP64, then convert to the arithmetic type. FP32 must perform FP32 arithmetic. Reject source coefficients that overflow that arithmetic type before mutation. FP16 uses FP32 arithmetic and rounds each gate's stored result; preserve subnormals and the documented underflow behavior.

Z and S use exact sign changes and component permutations, including FP16 signed zeros. T updates target-one amplitudes. Rz multiplies each pair member by its phase. Retain the general matrix implementation for reference comparisons. `Amplitude` needs the externally tested `PRECISION`, `as_complex64`, `negated`, and `times_i` behavior.

Acceptance: `tests/precision.rs` and `tests/diagonal.rs`.

### 3 Restore the worker executor

Implement `CpuExecutor::new`, `threads`, `with_parallel_threshold`, and `run`. One worker follows the serial path without a pool; zero is an error. Reuse an owned pool across calls.

Use safe disjoint slices for pairs. Low target bits can share coarse chunks; high target bits require splitting lower and upper halves into corresponding smaller chunks so the highest bit still uses multiple workers. Control checks must use global amplitude indices. Preserve each amplitude's arithmetic order so threaded and serial unfused execution agree exactly across all three precisions.

Acceptance: `tests/threading.rs`, including forced parallel execution and default threshold cases.

### 4 Restore circuit compression

Export `CompiledCircuit`, `CompileOptions`, and `CompilationStats`. Implement `new`, `with_options`, `gates`, `stats`, and `CpuExecutor::run_compiled`.

Compile consecutive CX runs into a GF(2) linear map, reduce it with row elimination, and synthesize the equivalent CX sequence in the correct order. Keep the original run unless the synthesized sequence is shorter. Treat every non-CX gate as a boundary. Validate the original input before rewriting; cancellation must not conceal an invalid gate. Reject register widths at or above `usize::BITS` and compiled circuit/state width mismatches before mutation.

Statistics need `input_gates`, `output_gates`, `cx_runs`, and `compressed_cx_runs`. `CompiledCircuit::new` enables CX compression alone. Compression preserves stored amplitudes exactly in every precision.

Acceptance: `tests/circuit.rs`, including the independent basis-bit map and long CX workload.

### 5 Restore dense and structured fusion

Collect consecutive gates acting on at most two qubits, capped at 32 source gates per block. One qubit blocks need at least two gates; two qubit blocks need at least four. Blocks without H, Rx, Ry, or U retain their specialized gate paths. Compose coefficients in FP64, preserve global phase, and execute accepted blocks with bounded local scratch and a single state pass.

Retain source gates for fallback: nonfinite FP64 composition prevents fusion; a fused matrix that overflows selected arithmetic precision falls back at execution. Source coefficients must still be validated for the selected precision before any mutation.

Extend statistics with `execution_operations`, `fused_blocks`, `fused_source_gates`, and `structured_fused_blocks`. Keep `gates()` as the post-CX, pre-fusion list.

Recognize exactly one Ry followed by one Rz on each of two wires, allowing interleaving between wires while preserving order on each wire. Apply real rotation mixing and combined complex phases to quartets. Rz before Ry, Rx, additional Ry, or an entangling gate must retain dense fallback. The structured tests prepare entangled inputs, so the kernel cannot assume product states.

Acceptance: all of `tests/fusion.rs`. Fused low precision results use tolerances because fusion changes intermediate rounding.

### 6 Restore the CLI contract

Read one JSON request per stdin line and flush one JSON response per line. Reject unknown fields and unsupported precision names. After any invalid request, continue processing later lines, but return a nonzero process exit status overall.

Requests include `num_qubits`, `gates`, `precision`, `threads`, `parallel_threshold`, `memory_limit_bytes`, `include_state`, `include_norm`, `probe_indices`, `amplitudes_output`, `optimize`, `fusion`, and optional `benchmark` with `repetitions` and `warmup`. Defaults are FP64, one worker, a 65,536 amplitude threshold, 256 MiB, full state output, norm enabled, and optimization/fusion disabled.

Responses must supply the tested amplitudes and probabilities, ordered probes, `norm_sqr`, `precision`, `state_bytes`, `compute_bits`, `accumulation_bits`, `threads`, `parallel_threshold`, and `seconds`. Complex JSON values are `[real, imaginary]`. With norm disabled return `norm_sqr: null`. Compilation adds a separate duration and statistics. Binary exports contain interleaved real and imaginary FP64 little-endian values in every storage mode, with export byte count and format metadata.

Construct the pool and compile outside gate timers. Benchmark repetitions run on fresh zero states; initialization, probes, norm scans, serialization and binary exports remain outside those timers. Preserve untimed preflight/warmup/final verification behavior described in the reports. The exact no-benchmark timing-array default is not fully specified by the tests and should be made explicit in the rebuilt implementation.

Acceptance: `tests/cli.rs`, then the existing Python harnesses.

## Tooling and documentation corrections

`tools/benchmark_sizes.py --dry-run` currently tries to hash missing Rust sources before producing its plan. It becomes usable after source restoration, or can be adjusted to report missing source metadata explicitly.

`tools/rerun_saved_comparison.py` requires a missing exact request, assumes 34,725 CX body gates, and calls `git rev-parse` on the parent. Other reports describe 34,150 body gates or different totals. Preserve these as distinct historical workloads; do not silently substitute one and claim exact reproduction.

`tools/benchmark_aer.py` restricts the CLI to 6–25 qubits and FP64. Its circuit generator can construct 28-qubit circuits, but reproducing the November 14 comparison needs the missing dedicated `run.py` and `worker.py`, or a new harness supporting FP32, 28 qubits and the documented timing/verification policy.

`tools/compare_size_results.py` requires saved inputs and exact source/revision matches. Reconstructed source needs new size measurements before running comparisons; historical hashes must not be bypassed to relabel new results as old ones.

The README introduction and November 8 checkpoint say fusion is future work; later README sections, surviving fusion tests, and the November 14 report establish that it was implemented. Use the latest tests to define the restoration target. Sampling, measurement collapse, GPU execution, and broad Qiskit compiler interoperability remain future ideas, with no implementation contract in the surviving tests.

## Validation and environment

On November 16, the following command passed all 20 Python tooling tests:

```sh
python3 -B -m unittest discover -s tools/tests -v
```

These exercise circuit construction, resource planning, reference calculations, result merging and profile analysis. They do not execute the missing simulator. There are 36 surviving Rust integration tests; Rust checks could not run because `cargo` and `rustc` are unavailable on the current PATH, and `src/` is absent.

Install a Rust toolchain supporting edition 2024 and the locked dependencies, then preserve the existing lockfile while testing the reconstructed code:

```sh
cargo fmt --check
cargo test --locked --all-targets
cargo test --locked --release --all-targets
cargo clippy --locked --all-targets -- -D warnings
cargo run --example bell
```

Create a fresh Python environment in a new directory rather than reuse the incomplete copy. Restore the selected Qiskit source into `vendor/qiskit`, build its editable installation, and install `tools/requirements-aer.txt` for Aer comparisons. The harness explicitly checks that Qiskit imports from that source tree. Pin environment versions separately for historical experiments; the copied environment has no complete dependency set to freeze.

After Rust checks pass, run Qiskit parity for all three precisions and worker counts 1, 2 and 4. Then use small repeated benchmarks before large allocations. The reports describe up to 4 GiB state payloads and long jobs; these are later reproduction targets.

The latest surviving report records a 28-qubit, 486-layer, 34,048-gate scrambled-Bell circuit with seed 42, four workers and fusion enabled. It reports Rust FP64 at 1,582.793 s versus Aer FP64 at 2,849.422 s, and Rust FP32 at 911.734 s versus Aer FP32 at 2,488.674 s. Those are historical single-run observations on an M4. Their raw data and exact binaries are absent, so a reconstructed implementation requires fresh correctness and performance measurements.

## Recovery outcome

The surviving folder supports a behavioral reconstruction of the simulator and most benchmark workflows. It does not support exact source recovery, verification of historical timings, or recovery of the missing design prose. The first implementation milestone is the serial FP64 engine passing the statevector tests; the complete target is all 36 existing integration tests plus Qiskit parity, including structured fusion.
