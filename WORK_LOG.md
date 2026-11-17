# Project investigation and environment setup work log

Work performed November 16, 2026, in `/Users/computer/Documents/qiskit_rust`.

## Project investigation

Inventoried the project directory, including hidden configuration files, Rust tests and examples, Python tools and tests, reports, dependency manifests, and the copied Python environment. Checked for repository history and local agent instructions. The folder has no `.git` directory, so Git status and commit history were unavailable.

Read the surviving project source, tests, examples, manifests, and reports to determine the simulator's behavior and reconstruction requirements. Inspected Python environment metadata for clues about the original project location. Generated files and dependency metadata were inspected where useful for recovery; compiled bytecode and copied executables were not treated as recoverable Rust source.

Identified the project as a Rust CPU quantum statevector simulator with:

- FP64, FP32, and experimental FP16 amplitude storage.
- In-place gate execution with qubit zero as the least significant index bit.
- H, X, Y, Z, S, T, Rx, Ry, Rz, CX, CZ, and finite complex 2×2 matrices.
- Serial execution and reusable Rayon worker pools.
- Specialized diagonal gate kernels.
- Optional compression of consecutive CX runs using binary linear algebra.
- Dense one and two qubit gate fusion, plus specialized eligible Ry/Rz blocks.
- A JSON lines executable for execution, probes, state export, and timing.
- Python correctness comparisons with Qiskit and performance comparisons with Aer.
- macOS Instruments capture and analysis tools.

Confirmed the following components are missing:

- The entire `src/` implementation directory.
- Git history and remote configuration.
- The `vendor/qiskit/` reference checkout.
- The `benchmark-results/` directory, including raw results, exact saved requests, profiles, and preserved benchmark binaries.
- The `target/` build directory.
- Several linked design and comparison documents.

The copied `.venv/` is incomplete. Its metadata identifies the original project location as `/Users/ccastorena/Documents/repos/qiskit_rust` and its editable Qiskit checkout under that folder. This is a lead for searching the original machine or backups; those original files have not been recovered.

Counted 36 surviving Rust integration tests, 13 Python tools, and four Python test files. An intermediate chat update incorrectly said 33 Rust integration tests; the verified count is 36.

## Recovery document

Created [RECOVERY_PLAN.md](RECOVERY_PLAN.md) with:

- The simulator's purpose and numerical conventions.
- An inventory of surviving and missing components.
- Recovery leads from environment metadata and recorded revisions.
- A proposed source layout based on filenames and symbols referenced by surviving files.
- A reconstruction sequence: serial engine, precision modes, worker executor, CX compression, dense and structured fusion, and CLI.
- Public API requirements and acceptance tests for each stage.
- Historical documentation inconsistencies and benchmark reproduction limits.
- Environment and validation steps.

Used the local `pages:write-page` skill's writing guidance for that recovery document. Saved it locally; no ChatGPT Page or Space was created or edited.

Checked local Markdown links to identify referenced files that no longer exist. Parsed all 17 Python source files under `tools/`, including its tests, with Python's AST parser; all parsed successfully.

## Tests run during investigation

Ran:

```sh
python3 -B -m unittest discover -s tools/tests -v
```

All 20 tests passed. They cover circuit generation, memory planning, reference calculations, result merging, and profiling analysis. They do not execute the missing Rust simulator.

Attempted the Rust all-target test command during initial environment inspection, but `cargo` was not installed or on PATH at that time. No Rust simulator tests have passed or run against a reconstructed implementation.

## Rust installation

Checked the available tools and platform. The machine is ARM64 macOS, with full Xcode already selected at `/Applications/Xcode.app/Contents/Developer`. Apple's C compiler and linker were available, so no additional native compiler installation was needed. The existing system Python is 3.9.6.

Downloaded the official rustup installer from `https://sh.rustup.rs` to `/private/tmp/qiskit-rustup-init.sh` and ran:

```sh
sh /private/tmp/qiskit-rustup-init.sh -y --profile default --default-toolchain stable --no-modify-path
```

Installed the stable `aarch64-apple-darwin` toolchain through rustup in the user's home directory, including Cargo, rustfmt, Clippy, and the standard default-profile components.

Verified versions:

| Tool | Installed version |
| --- | --- |
| rustc | 1.100.0, commit `0ef1bc386`, November 9, 2026 |
| Cargo | 1.100.0, commit `3acc79c0c` |
| rustfmt | 1.11.0-stable |
| Clippy | 0.1.100 |
| Apple clang | 21.0.0 |

The default active Rust toolchain is `stable-aarch64-apple-darwin`.

Fetched all dependencies pinned in the existing lockfile:

```sh
/Users/computer/.cargo/bin/cargo fetch --locked
```

The download succeeded. This populated Cargo's cache without changing the project's dependency requirements or lockfile. No Python packages, Qiskit checkout, Aer installation, or replacement virtual environment were installed.

## Compiler verification

Created a temporary Rust program under `/private/tmp`, compiled it using edition 2024, linked it with the available native tools, and executed it successfully. Its output was:

```text
Rust compiler and linker work
```

The temporary program and binary were cleaned up automatically when the temporary directory context exited. This verifies the toolchain, not the missing simulator.

## Persistent PATH configuration

Checked the current PATH and the standard zsh startup files. Rust's bin directory was absent from PATH, and `.zshenv`, `.zprofile`, `.zshrc`, and `.profile` did not exist at the checked home-directory paths.

Created `/Users/computer/.zshenv` containing:

```sh
# Rust toolchain installed by rustup
. "$HOME/.cargo/env"
```

The rustup environment script adds `$HOME/.cargo/bin` only when it is not already present. Verified a subsequent shell resolves:

```text
cargo -> /Users/computer/.cargo/bin/cargo
rustc -> /Users/computer/.cargo/bin/rustc
```

Both version commands succeeded through PATH. An already-open terminal can load the change with:

```sh
source ~/.zshenv
```

## Files and system changes

Project files added:

- `RECOVERY_PLAN.md`: investigation findings and reconstruction specification.
- `WORK_LOG.md`: this record of completed work.

Changes outside the project:

- Installed rustup and the stable Rust toolchain under the user's home directory.
- Downloaded the locked Rust dependencies into Cargo's cache.
- Created `~/.zshenv` to load rustup's PATH setup.
- Downloaded the installer to `/private/tmp/qiskit-rustup-init.sh`.

Home-directory installation, startup-file modification, and network downloads used the available approval mechanism. No existing simulator source, tests, examples, Python tools, manifests, lockfile, or historical reports were edited. No Git repository was initialized and no commits were created.

## Current status

The Rust toolchain and native build tools are ready, dependencies are cached, and Rust works through PATH. Investigation and environment setup are complete. The missing simulator has not yet been reimplemented, and no historical benchmark results have been reproduced.

The next implementation milestone is restoring the serial FP64 engine and passing `tests/statevector.rs`, followed by the remaining stages in [RECOVERY_PLAN.md](RECOVERY_PLAN.md).

## November 17 implementation update

Reconstructed the missing simulator in six Rust files under `src/`: public state
and gate APIs, precision storage and arithmetic, safe parallel gate execution,
CX compression, dense and structured fusion, and the JSON lines executable.
All 36 existing integration tests pass in debug and release; formatting, Clippy
with warnings denied, and the release Bell example also pass.

Created `.venv-rebuild/` and installed NumPy 2.0.2 and Qiskit 2.2.3 plus their
dependencies. Added the environment to `.gitignore` and pinned its packages in
`tools/requirements-rebuild.txt`. The incomplete historical environment and old
pinned-source comparison tools remain unchanged.

Added `tools/verify_rebuild.py` to run the existing 240 parity circuits against an
installed Qiskit wheel, across three precisions, two worker counts and optimization
on/off. All 2,880 comparisons pass. All 20 existing Python tooling tests pass too.

Ran 16 FP64 performance configurations at 16 and 20 qubits. Every configuration
passed full-state verification and ran faster than Python Qiskit Statevector in
this measurement. At 20 qubits, fused one-worker Rust was about 37–70× faster;
four-worker Rust was about 128–228× faster. These execution-path ratios are not Aer
comparisons; timing scope and sample limitations are documented with the results.

Saved raw results under `benchmark-results/rebuild/results.json` and the run log
at `/private/tmp/qiskit-rebuild-verification.log`. Final check logs are at
`/private/tmp/qiskit-rebuild-debug.log`, `qiskit-rebuild-release.log`,
`qiskit-rebuild-clippy.log`, `qiskit-rebuild-format.log` and `qiskit-rebuild-bell.log`.
Temporary logs can be removed by system cleanup.

Created [REBUILD_RESULTS.md](REBUILD_RESULTS.md), added current build and verification
instructions to README, and marked the recovery assessment as historical.
The simulator is now implemented and runnable. Original source and historical
benchmark artifacts have not been recovered, and fresh Aer performance has not
been measured.

## November 17 second optimization pass

On the request to try again, rechecked the implementation and release tests, then
optimized CX/CZ to visit only active control-one pairs and changed the structured
Ry/Rz kernel to use real-coefficient scaling. Added a 17-qubit traversal regression
test spanning both bit orders and chunk boundaries. All 37 Rust tests now pass in
debug and release, and Clippy and formatting pass.

Installed Aer 0.17.2 and its dependencies in `.venv-rebuild/`. Added
`tools/benchmark_rebuild_aer.py` and `tools/requirements-retry-aer.txt` for a matched
FP64 fresh-request comparison with four workers and fusion enabled on both sides.
Preserved the previous Rust executable under `benchmark-results/retry-aer/`.

Measured four 20/22-qubit circuit cases with five samples and rotating engine order.
The final Rust binary was 1.48–1.86× faster than the previous binary and 1.35–1.79×
faster than Aer in those observed medians. Full complex states and timed probes
passed comparison. The preliminary control-only optimization results are preserved.

Reran all 2,880 Python parity comparisons and the 16-configuration Python performance
suite against the final source. All passed, as did all 20 Python tooling tests.
Verified source and executable hashes against both final measurement files.
Saved the findings, limitations, and reproduction commands in
[OPTIMIZATION_RESULTS.md](OPTIMIZATION_RESULTS.md) and linked it from README and
the initial reconstruction report.

## Unified runtime comparison tool

Read the original comparison, optimization, precision and size-sweep reports again
and implemented `tools/compare_runtimes.py` with the executable `./compare` launcher.
It presents Rust, Python Statevector and Aer runtimes in a single table, with CSV,
strict JSON checkpoints, a readable report and exact saved circuit requests.

The main ratios now use fresh-state request times for every engine. Rust gate and
compilation times remain separate diagnostics. Isolated workers configure numerical
threads before imports, rotate engine order, discard warmups, validate full complex
states outside timing, and check probes on every trial. Fusion defaults on; CX
compression remains an explicit labelled switch. Precision mismatches and failed
accuracy checks suppress ratios. Memory admission, explicit skips, per-case
timeouts and process-group cleanup retain the earlier tooling safeguards.

Added tests for timing-order balance, precision and failure gating, checkpoint
contents, allocation-free dry runs, worker timeout cleanup, scaled numerical
accuracy, and a real three-backend integration case. All 27 Python tooling tests
passed in the installed environment. Exercised FP64/FP32/FP16, fusion on/off,
Aer FP16 unsupported records, and probe-only validation on CX/diagonal workloads.

Created `BENCHMARKING.md` with commands and an explanation of the timing policy,
and linked it from README. The simulator kernels and old comparison tools remain
unchanged in this tooling pass.

Ran the unified comparison at 16/20 qubits, four workers, three measured samples
and one warmup for mixed and scrambled-Bell circuits. All four full-state cases
passed. Saved the table and samples under `benchmark-results/unified-example/`.
Checked the report, CSV row counts, Aer worker counts, sample counts, and exact
request and harness hashes. Also verified redisplaying the table with `--report`.

## Final default command verification

Ran the exact `./compare` command with all defaults: eight cases at 16/20 qubits,
one/four workers, mixed and scrambled-Bell circuits, one warmup and five measured
samples per engine. All eight passed full-state validation. Verified the CSV,
worker counts, sample counts, harness hash and exact request hashes.

Saved default-run report: `benchmark-results/compare-20261117-173952/REPORT.md`.

## GitHub repository setup

Initialized this project as a Git repository on `main` and configured the SSH remote
as `git@github.com:usercoffee123/quantum_rust.git`. Verified SSH access and that the
remote initially had no branches. Recorded GitHub's official published SSH host
keys to resolve this machine's first-connection host verification.

Configured the repository-local commit author as `usercoffee123` with
`usercoffee123@users.noreply.github.com`; global Git identity settings were left
unchanged. The initial snapshot includes source, tests, scripts, documents and
recorded textual benchmark artifacts. Python environments, Cargo build outputs,
bytecode caches and the preserved compiled baseline executable remain local.
