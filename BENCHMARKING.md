# Compare Python and Rust runtimes

Run this from the project directory:

```sh
./compare
```

The launcher uses the installed `.venv-rebuild` environment, builds Rust in release
mode once, and prints one table comparing Rust, Python Qiskit Statevector and
optimized Qiskit Aer. Defaults are 16 and 20 qubits, one and four workers, FP64,
mixed and scrambled-Bell circuits, fusion enabled, one warmup and five measured
requests per engine. Each case runs in an isolated Python worker, sequentially.

The table reports milliseconds. `Py/Rust` and `Aer/Rust` divide the other runtime
by Rust's runtime: above one favors Rust; below one favors the other backend.
There is no minimum speed requirement. Actual runs may differ from the saved
examples because affinity, temperature and unrelated desktop activity are uncontrolled.

## Common comparisons

```sh
./compare --suite --qubits 20
./compare --suite --qubits 20 22 --repetitions 3 --output benchmark-results/matched-suite
./compare --suite --qubits 26 --dry-run
```

The suite runs Rust and Qiskit Aer at matching FP32 and FP64 precision with
four workers by default. Its circuit families cover random entangling
connectivity, scrambled Bell pairs, mixed gates, CX strides, and diagonal
gates. Qiskit Aer is the native simulator called through Python. The pure
Qiskit Statevector path is FP64 and can be requested separately with
`--engines python`. The dry run checks memory admission and lists cases before
allocation. Use `--families`, `--depth`, or `--layers` to narrow a large run or
increase its circuit depth.

Each case saves the exact Rust request under `cases/`. Use that request for
the [macOS profiling workflow](README.md#automated-macos-profiling) once the
timing run finishes.

```sh
# Fast check of the complete pipeline:
./compare --qubits 12 --threads 4 --repetitions 3

# Representative comparison with all three backends:
./compare --qubits 16 20 --threads 4 --repetitions 3

# Equal-precision FP64/FP32 comparison with Aer, plus Python FP64 reference times:
./compare --qubits 20 --threads 1 4 --precisions f64 f32

# Isolate the benefit of fusion on both Rust and Aer:
./compare --qubits 20 --threads 4 --fusion both

# Broader entangling connectivity:
./compare --qubits 20 --families scrambled_bell random_matching

# Controlled-gate and diagonal diagnostics, including target position:
./compare --qubits 20 --families cx_stride diagonal --target high --depth 256

# Explicitly measure CX circuit compression as well:
./compare --qubits 20 --families cx_stride --depth 1000 --optimize-cx

# Use only one Python backend alongside Rust:
./compare --engines python
./compare --engines aer

# Inspect allocations and explicit memory skips without building or native imports:
./compare --qubits 16 20 24 28 --dry-run

# Choose a new output directory, or redisplay its saved table later:
./compare --output benchmark-results/my-comparison
./compare --report benchmark-results/my-comparison/results.json
```

Run `./compare --help` for all controls, including seeds, circuit layers, memory
budget, validation cap and per-case timeout. Existing nonempty output directories
are refused so previous results remain available. Without `--output`, every run
gets a new timestamped directory.

## Follow-up benchmark matrix

Start with the original 20-qubit random-matching case to complete the
three-engine comparison. Keep eight layers, seed 42, FP64, four requested Rust
and Aer workers, fusion enabled, and CX compression disabled. Every backend
gets one warmup and three measured requests. The 20-qubit case checks the full
complex state.

```sh
./compare --qubits 20 --families random_matching --threads 4 --precisions f64 \
  --engines python aer --layers 8 --seed 42 --fusion on --warmup 1 --repetitions 3
```

For a focused Rust/Aer comparison, run random-matching and CX-stride circuits
at 20 and 22 qubits, with fusion on and off. The 20-qubit cases use full-state
validation and the 22-qubit cases use probe validation at the default cap.
The 22-qubit FP64 state is 64 MiB per engine.

```sh
./compare --qubits 20 22 --threads 4 --precisions f64 \
  --families random_matching cx_stride --engines aer --fusion both \
  --depth 64 --layers 8 --seed 42 --warmup 1 --repetitions 3
```

Report the median and sample range for every passing row. The random-matching
case tests a dense state with rotations and entangling gates across the register.
The CX-stride case starts from Bell pairs and then permutes their computational
basis support with CX gates. At 22 qubits, the runner propagates paired and
mismatched Bell indices through the CX sequence to select 33 expected nonzero
and 34 expected zero locations deterministically. The backend comparison then
checks the complex amplitudes at those locations.

### 22-qubit CX compression

Run the CX-stride case with Rust CX compression enabled and full-state
validation. Both engines receive the same source circuit and run in FP64 with
four workers and fusion enabled.

```sh
./compare --qubits 22 --threads 4 --families cx_stride --engines aer \
  --fusion on --optimize-cx --full-check-max-qubits 22 \
  --warmup 1 --repetitions 5
```

The compiler reduced 86 source gates to 26 execution operations. All
4,194,304 amplitudes passed comparison with Aer, with a maximum complex
amplitude error of 2.43e-17. The five measured fresh requests had a 34.76 ms
Rust median (29.02–47.17 ms) and a 46.53 ms Aer median (42.12–77.07 ms).
Aer metadata confirmed fusion was applied. The uncompressed matrix case
recorded medians of 77.82 ms for Rust and 44.24 ms for Aer.

A separate Rust-only sweep compared paired traversal grains of 2,048, 4,096
and 8,192 amplitudes, with two warmups and seven measured samples per binary and
workload. The 8,192 setting had the lowest median gate time on the CX-stride
circuit, while the directed CX cases favored different settings and sample
ranges overlapped. The default remains 4,096 amplitudes.

## Metal FP32 prototype

On a Mac with Metal and Xcode, compare the FP32 Rust CPU engine with the Metal
prototype on a random-matching circuit. [Metal results and remaining work](METAL_FP32_REPORT.md)

```sh
.venv-rebuild/bin/python tools/benchmark_metal.py --qubits 27 --layers 8 \
  --output benchmark-results/metal-fp32-27q
```

The prototype implements H, CX, Ry and Rz and combines adjacent rotations into
one- or two-qubit kernels. It keeps one state buffer on the GPU between
dispatches and reads only the requested amplitudes after execution. Request
timings include allocation, initialization, gate processing and probe output.
The CPU and GPU binaries build before timing, and Metal pipeline compilation
happens before warmups. The runner checks every amplitude through 14 qubits
and 67 deterministic probes at larger sizes against the Rust FP32 result.
Use a terminal with GPU access if the current execution sandbox denies Metal.

## Python environment

Create the wheel-based environment from a checkout using a Python interpreter
that supports the pinned packages. The reported reconstruction used Python 3.9.6
on ARM64 macOS.

```sh
python3 -m venv .venv-rebuild
.venv-rebuild/bin/python -m pip install -r tools/requirements-retry-aer.txt
```

Use `QISKIT_BENCH_PYTHON=/path/to/python ./compare` to select a different Python
environment. The unified runner records the versions of its installed Qiskit and
Aer packages. The historical source-based tools require a separate checkout at
`vendor/qiskit`. [Historical setup instructions](README.md#set-up-the-historical-qiskit-source-checkout)

## What the times include

The primary comparison uses fresh-state requests. Each engine allocates and
initializes a new state, executes the identical source circuit and returns the
same complex amplitude probes. Rust parsing, worker-pool creation and compilation
are included. Python Statevector initialization, evolution and probe extraction
are included. Aer job handling and fusion are included. Imports, circuit
construction, native process startup, and correctness checks are excluded.

Rust's internal gate timer and compilation timer are also saved as diagnostics.
They are not used in the headline ratios. This avoids comparing a Rust kernel
time against Python's larger request time. JSON request encoding happens before
the Rust request timer; response parsing is included.

Engine order is shuffled deterministically using the seed, then rotated between
trials. All engines run sequentially. Warmup results are discarded. Every engine
gets its own timing samples for each configuration, rather than silently reusing
a Python baseline collected under a different run order.

Python Statevector always uses FP64 with numerical-library threads limited to one.
It is a separate baseline from Aer. Python ratios appear only for FP64 Rust; at
multiple Rust workers those ratios still compare different requested resources.
Aer matches the row's FP64 or FP32 precision and requested workers. Aer has no
FP16 storage mode, so that result is explicitly unsupported and has no speed ratio.
The Workers column is a configured count: small Rust states can use the documented
serial fallback. Its threshold and Aer's confirmed state-update count are saved.

## Optimizations and lessons retained

The runner follows the methods and distinctions in [the original matched Aer
report](AER_25_QUBIT_COMPARISON.md), [the performance report](PERFORMANCE_REPORT.md),
[the fusion diagnostic](FUSION_INSENSITIVE_COMPARISON.md), [the size sweep](SIZE_SWEEP_REPORT.md),
and [the subsequent kernel results](OPTIMIZATION_RESULTS.md).

- Fusion defaults on for both Rust and Aer. Rust automatically uses its structured
  Ry/Rz kernel where eligible, real-coefficient arithmetic, control-aware CX/CZ
  traversal and specialized diagonal kernels. Compilation statistics verify which
  fused blocks were selected.
- CX compression defaults off. Turning it on is labelled separately because it
  reduces executed gate count; it is not a per-gate kernel improvement.
- Aer is forced to CPU statevector simulation, including Clifford CX workloads.
  Truncation is disabled, the zero threshold is zero, and parallel experiments and
  shots are limited to one. Fusion keeps its normal block-size limit. Actual
  state-update workers and fusion metadata are recorded.
- OpenMP limits are set in each isolated worker before importing numerical
  libraries. BLAS and Accelerate limits are one to avoid nested oversubscription.
- Full complex states and norm drift are validated outside timing through 20
  qubits by default. Rust exports binary FP64 values rather than giant JSON states.
  Above the cap, the table explicitly says `probes`, not `full`. Use
  `--full-check-max-qubits` to adjust the cap if memory permits.
- Accuracy uses relative L2 and scaled peak error as well as norm drift, so tiny
  amplitudes cannot pass just because a fixed absolute tolerance is large. It
  preserves global phase and never renormalizes the simulated state. Any failed
  preflight or timed probe check suppresses speed ratios and makes the command fail.
- Memory admission checks the largest FP64 reference buffer, a conservative eight
  times payload estimate for reference/scratch work, live available memory and
  headroom. These are estimates, not reservations. Unsafe cases become explicit
  skips. Timeouts terminate the isolated worker's process group, including Rust.

The mixed circuit is a structured local-gate workload, scrambled Bell preserves
maximal half-cut entanglement by construction, and random matching broadens
connectivity. CX-stride and diagonal circuits are specialized diagnostics. Use
multiple families before making a general performance claim.

## Saved results

Each output directory contains:

| Artifact | Contents |
| --- | --- |
| `REPORT.md` | Side-by-side table and timing interpretation |
| `results.csv` | Median/min/max times, ratios, status and Rust gate/compile diagnostics |
| `results.json` | All raw samples, trial order, accuracy metrics, versions and metadata |
| `cases/*.request.json` | Exact circuits, gates, angles, probes, precision and optimization flags |
| `cases/*.job.json` | Isolated worker configuration |
| `cases/*.result.json` | Worker checkpoints and final per-case results |
| `cases/*.log` | Dependency, backend or timeout diagnostics |

Source, binary, lockfile, harness, circuit and request hashes make the measured
inputs identifiable. Progress is checkpointed after every case and every measured
trial. Errors and skips remain visible, with no fabricated durations. The existing
Git convention ignores new benchmark directories by default. Selected recorded
runs are included in the initial repository snapshot; virtual environments, build
output and the preserved platform-specific executable remain local.

The implementation is [tools/compare_runtimes.py](tools/compare_runtimes.py).
Its correctness and orchestration tests are in
[tools/tests/test_compare_runtimes.py](tools/tests/test_compare_runtimes.py).
Run the whole Python tooling suite in the installed environment with:

```sh
.venv-rebuild/bin/python -B -m unittest discover -s tools/tests -v
```
