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

If the environment needs recreation:

```sh
python3 -m venv .venv-rebuild
.venv-rebuild/bin/python -m pip install -r tools/requirements-retry-aer.txt
```

Use `QISKIT_BENCH_PYTHON=/path/to/python ./compare` to select a different Python
environment. The old pinned-submodule tools remain available for their historical
experiments; this runner uses the installed Qiskit and Aer packages and records
their actual versions.

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
