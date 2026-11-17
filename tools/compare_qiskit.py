#!/usr/bin/env python3
"""Parity and timing against the actual editable Qiskit submodule installation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time

# Limit Qiskit's numerical-library workers before import. Rust workers are explicit.
for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ[variable] = "1"
os.environ["QISKIT_IN_PARALLEL"] = "TRUE"
os.environ["QISKIT_FORCE_THREADS"] = "FALSE"

ROOT = Path(__file__).resolve().parents[1]
SUBMODULE = ROOT / "vendor" / "qiskit"


def command(*args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True, timeout=30).strip()


def qiskit_dirty_state():
    # A status scan of a large editable build can stall. Unknown is distinct
    # from a clean checkout; optional metadata must not discard timing results.
    try:
        return bool(subprocess.check_output(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "status", "--porcelain"],
            cwd=SUBMODULE, text=True, timeout=5,
        ).strip())
    except (subprocess.SubprocessError, OSError):
        print("Qiskit dirty-state check unavailable; recorded as null.", file=sys.stderr)
        return None


def load_qiskit():
    global np, QuantumCircuit, Statevector, qiskit
    # Import the pinned source explicitly, even when the interpreter disables
    # editable-install .pth hooks. The native extension must still be built.
    if not (SUBMODULE / "qiskit" / "__init__.py").is_file():
        raise RuntimeError("Initialize the reference: git submodule update --init --recursive")
    sys.path.insert(0, str(SUBMODULE))
    try:
        import numpy as np
        import qiskit
        from qiskit import QuantumCircuit
        from qiskit.quantum_info import Statevector
    except ImportError as exc:
        raise RuntimeError(
            "Install the submodule first: python -m pip install -e ./vendor/qiskit"
        ) from exc
    if not Path(qiskit.__file__).resolve().is_relative_to(SUBMODULE.resolve()):
        raise RuntimeError(f"Expected the Qiskit submodule, imported {qiskit.__file__}")


def binary():
    subprocess.run(["cargo", "build", "--release", "--locked"], cwd=ROOT, check=True)
    # Respect Cargo's target-directory override, including absolute paths.
    metadata = json.loads(command("cargo", "metadata", "--no-deps", "--format-version", "1"))
    suffix = ".exe" if os.name == "nt" else ""
    return Path(metadata["target_directory"]) / "release" / f"statevector{suffix}"


def rust_results(executable, requests):
    result = subprocess.run(
        [str(executable)], input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Rust simulator failed:\n{result.stdout}\n{result.stderr}")
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    if len(rows) != len(requests):
        raise RuntimeError("Rust simulator returned the wrong number of results")
    return rows


def circuit(num_qubits, gates):
    qc = QuantumCircuit(num_qubits)
    for gate in gates:
        name = gate["gate"]
        if name == "u":
            matrix = np.asarray(gate["matrix"], dtype=float)
            qc.unitary(matrix[..., 0] + 1j * matrix[..., 1], [gate["target"]])
        elif name in ("cx", "cz"):
            getattr(qc, name)(gate["control"], gate["target"])
        elif name in ("rx", "ry", "rz"):
            getattr(qc, name)(gate["theta"], gate["target"])
        else:
            getattr(qc, name)(gate["target"])
    return qc


def random_gates(n, depth, seed):
    rng = random.Random(seed)
    names = ["h", "x", "y", "z", "s", "t", "rx", "ry", "rz"]
    if n > 1:
        names += ["cx", "cz"]
    gates = []
    for _ in range(depth):
        name = rng.choice(names)
        gate = {"gate": name, "target": rng.randrange(n)}
        if name in ("rx", "ry", "rz"):
            gate["theta"] = rng.uniform(-4 * 3.141592653589793, 4 * 3.141592653589793)
        elif name in ("cx", "cz"):
            gate["control"] = rng.choice([q for q in range(n) if q != gate["target"]])
        gates.append(gate)
    return gates


def complex_array(pairs):
    pairs = np.asarray(pairs)
    return pairs[..., 0] + 1j * pairs[..., 1]


def parity_cases():
    yield "zero-qubits", 0, []
    for n in (1, 2, 3, 5):
        yield f"zero-{n}", n, []
        for target in range(n):
            # Exercise phases and rotations on a nontrivial complex input.
            prefix = [{"gate": "ry", "target": q, "theta": 0.37 * (q + 1)}
                      for q in range(n)]
            prefix += [{"gate": "rz", "target": target, "theta": -0.91}]
            for name in ("h", "x", "y", "z", "s", "t", "rx", "ry", "rz"):
                gate = {"gate": name, "target": target}
                if name in ("rx", "ry", "rz"):
                    gate["theta"] = -1.234
                yield f"{name}-{n}-{target}", n, prefix + [gate]
            # Arbitrary complex unitary, independent of built-in gate formulas.
            rng = np.random.default_rng(100 * n + target)
            matrix, _ = np.linalg.qr(rng.normal(size=(2, 2)) + 1j * rng.normal(size=(2, 2)))
            encoded = np.stack((matrix.real, matrix.imag), axis=-1).tolist()
            yield f"unitary-{n}-{target}", n, prefix + [{"gate": "u", "target": target, "matrix": encoded}]
            for control in range(n):
                if control == target:
                    continue
                for name in ("cx", "cz"):
                    yield f"{name}-{n}-{control}-{target}", n, prefix + [
                        {"gate": name, "target": target, "control": control}]
        if n > 1:
            yield f"ghz-{n}", n, [{"gate": "h", "target": 0}] + [
                {"gate": "cx", "control": 0, "target": q} for q in range(1, n)]
    for n in range(1, 9):
        for seed in range(8):
            yield f"random-{n}-{seed}", n, random_gates(n, 120, seed)
    yield "large-stride-pairs", 14, [{"gate": "h", "target": q} for q in range(14)] + [
        {"gate": "ry", "target": 13, "theta": 0.713},
        {"gate": "rz", "target": 7, "theta": -0.391},
        {"gate": "cx", "control": 0, "target": 13},
        {"gate": "cx", "control": 13, "target": 0},
        {"gate": "cz", "control": 13, "target": 7},
    ]
    # Long interference/uncompute sequence with an independently known final state.
    forward = random_gates(6, 200, 993)
    inverse = []
    for gate in reversed(forward):
        gate = gate.copy()
        if "theta" in gate:
            gate["theta"] *= -1
        elif gate["gate"] in ("s", "t"):
            gate = {"gate": "rz", "target": gate["target"],
                    "theta": -3.141592653589793 / (2 if gate["gate"] == "s" else 4)}
            # Rz differs from inverse S/T by a global phase: compare to Qiskit,
            # rather than asserting literal |0> amplitude here.
        inverse.append(gate)
    yield "interference-uncompute", 6, forward + inverse


# Accuracy budgets for this small-circuit regression suite, not universal guarantees.
TOLERANCES = {
    "f64": {"atol": 1e-11, "rtol": 1e-11, "norm": 1e-11},
    "f32": {"atol": 3e-6, "rtol": 3e-5, "norm": 1e-5},
    "f16": {"atol": 3e-3, "rtol": 2e-2, "norm": 2e-2},
}
STORAGE_BYTES = {"f16": 4, "f32": 8, "f64": 16}


def run_parity(executable, precisions, threads):
    cases = list(parity_cases())
    expected_states = [Statevector.from_instruction(circuit(n, gates)).data for _, n, gates in cases]
    for precision in precisions:
        for workers in threads:
            requests = [{"num_qubits": n, "gates": gates, "precision": precision, "threads": workers, "parallel_threshold": 0} for _, n, gates in cases]
            actual = rust_results(executable, requests)
            tol = TOLERANCES[precision]
            worst = drift = tvd = 0.0
            min_fidelity = 1.0
            for (name, n, _), row, expected in zip(cases, actual, expected_states):
                amplitudes = complex_array(row["amplitudes"])
                np.testing.assert_allclose(amplitudes, expected, atol=tol["atol"], rtol=tol["rtol"], err_msg=name)
                np.testing.assert_allclose(row["probabilities"], np.abs(expected) ** 2,
                                           atol=tol["atol"], rtol=tol["rtol"], err_msg=name)
                np.testing.assert_allclose(row["norm_sqr"], 1, atol=tol["norm"], rtol=0, err_msg=name)
                assert row["precision"] == precision
                assert row["threads"] == workers
                assert row["state_bytes"] == STORAGE_BYTES[precision] * (1 << n)
                worst = max(worst, float(np.max(np.abs(amplitudes - expected))))
                drift = max(drift, abs(row["norm_sqr"] - 1))
                fidelity = float(abs(np.vdot(expected, amplitudes)) ** 2 /
                                 (np.vdot(expected, expected).real * row["norm_sqr"]))
                min_fidelity = min(min_fidelity, fidelity)
                tvd = max(tvd, float(np.sum(np.abs(np.asarray(row["probabilities"]) /
                                                 row["norm_sqr"] - np.abs(expected) ** 2)) / 2))
            print(f"PASS: {len(cases)} {precision} threads={workers} cases vs Qiskit FP64; max amplitude error {worst:.3g}, "
                  f"norm drift {drift:.3g}, min normalized fidelity {min_fidelity:.9f}, "
                  f"max normalized probability TVD {tvd:.3g}")
    print(f"Qiskit {qiskit.__version__}, submodule {command('git', 'rev-parse', 'HEAD', cwd=SUBMODULE)}")


def workload(n, target, depth, mixed):
    gates = []
    for i in range(depth):
        if mixed and n > 1 and i % 4 == 3:
            gates.append({"gate": "cx" if i % 8 == 3 else "cz",
                          "control": (target + 1) % n, "target": target})
        else:
            gates.append({"gate": "ry" if i % 2 == 0 else "rz",
                          "theta": 0.123 + i * 0.017, "target": target})
    # Populate all qubits first, so benchmarks don't rely on a mostly-zero state.
    return [{"gate": "h", "target": q} for q in range(n)] + gates


def summary(values):
    return {"median_seconds": statistics.median(values), "min_seconds": min(values),
            "max_seconds": max(values), "samples_seconds": values}


def run_bench(executable, args):
    records = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    reference_key = None
    reference_state = None
    reference_times = None
    for n in args.qubits:
        # Match the Rust default state buffer budget; avoid accidental huge allocations.
        if not 1 <= n <= 24:
            raise ValueError("benchmark qubits must be in 1..=24 (256 MiB FP64 state limit)")
    for n in args.qubits:
        for target in sorted({0, n // 2, n - 1}):
            for mixed in (False, True):
                for precision in args.precisions:
                    for workers in args.threads:
                        gates = workload(n, target, args.depth, mixed)
                        instruction = circuit(n, gates).to_instruction()
                        probes = sorted({0, (1 << n) - 1, *random.Random(n).sample(range(1 << n), min(16, 1 << n))})
                        request = {"num_qubits": n, "gates": gates, "precision": precision, "threads": workers,
                                   "parallel_threshold": args.parallel_threshold, "include_state": False,
                                   "probe_indices": probes,
                                   "benchmark": {"repetitions": args.repetitions, "warmup": args.warmup}}
                        # Preserve completed samples and the pending case even
                        # if a later accuracy check fails or the run is stopped.
                        args.output.write_text(json.dumps({
                            "metadata_complete": False,
                            "pending_case": {"num_qubits": n, "target": target,
                                             "mixed": mixed, "precision": precision, "threads": workers},
                            "results": records,
                        }, indent=2) + "\n")
                        qiskit_times = []

                        def time_qiskit():
                            last = None
                            for trial in range(args.warmup + args.repetitions):
                                # Allocation/zero initialization and circuit construction are excluded.
                                # Evolve's own output/scratch allocation remains part of its cost.
                                last = None
                                state = Statevector.from_int(0, 1 << n)
                                start = time.perf_counter()
                                last = state.evolve(instruction)
                                elapsed = time.perf_counter() - start
                                if trial >= args.warmup:
                                    qiskit_times.append(elapsed)
                            return last

                        # Each deterministic circuit has one FP64 reference.
                        # Reuse its timed samples across Rust modes/workers;
                        # retain only the current workload's final state.
                        key = (n, target, mixed)
                        if key == reference_key:
                            final, qiskit_times = reference_state, reference_times
                            rust = rust_results(executable, [request])[0]
                        elif len(records) % 2:
                            reference_state = None
                            final = time_qiskit()
                            rust = rust_results(executable, [request])[0]
                        else:
                            reference_state = None
                            rust = rust_results(executable, [request])[0]
                            final = time_qiskit()
                        reference_key, reference_state, reference_times = key, final, qiskit_times
                        tol = TOLERANCES[precision]
                        probe_error = float(np.max(np.abs(complex_array(rust["probes"]) - final.data[probes])))
                        np.testing.assert_allclose(complex_array(rust["probes"]), final.data[probes],
                                                   atol=tol["atol"], rtol=tol["rtol"])
                        np.testing.assert_allclose(rust["norm_sqr"], np.vdot(final.data, final.data).real,
                                                   atol=tol["norm"], rtol=0)
                        rust_stats, qiskit_stats = summary(rust["seconds"]), summary(qiskit_times)
                        speedup = qiskit_stats["median_seconds"] / rust_stats["median_seconds"]
                        record = {"num_qubits": n, "target": target, "workload": "mixed" if mixed else "rotations",
                                  "gate_count": len(gates), "precision": precision, "threads": rust["threads"],
                                  "parallel_threshold": rust["parallel_threshold"],
                                  "rust_compute_bits": rust["compute_bits"], "accumulation_bits": rust["accumulation_bits"],
                                  "state_bytes": rust["state_bytes"], "qiskit_state_bytes": 16 * (1 << n),
                                  "max_probe_error": probe_error, "norm_drift": abs(rust["norm_sqr"] - 1),
                                  "rust": rust_stats, "qiskit_statevector": qiskit_stats,
                                  "qiskit_over_rust_median_ratio": speedup}
                        records.append(record)
                        print(f"{precision} threads={workers} n={n:2} target={target:2} {record['workload']:9} "
                              f"Rust={rust_stats['median_seconds']*1000:9.3f} ms "
                              f"Qiskit={qiskit_stats['median_seconds']*1000:9.3f} ms ratio={speedup:.2f}x",
                              flush=True)
                        del final
    serial_times = {(r["num_qubits"], r["target"], r["workload"], r["precision"]): r["rust"]["median_seconds"]
                    for r in records if r["threads"] == 1}
    for row in records:
        key = (row["num_qubits"], row["target"], row["workload"], row["precision"])
        baseline = serial_times.get(key)
        row["rust_speedup_over_one_thread"] = baseline / row["rust"]["median_seconds"] if baseline else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"metadata_complete": False, "results": records}, indent=2) + "\n")
    report = {
        "metadata_complete": True,
        "metadata": {"baseline": "Qiskit quantum_info.Statevector, not Aer", "qiskit_precision": "complex FP64 reference",
                     "rust_precisions": args.precisions, "accuracy_budgets": TOLERANCES,
                     "rust_execution": "configurable CPU pool", "rust_threads": args.threads,
                     "parallel_threshold": args.parallel_threshold, "qiskit_numerical_thread_limit": 1,
                     "qiskit_reference_reuse": "one timed FP64 baseline per qubit/target/workload, shared across Rust precisions and worker counts",
                     "qiskit_version": qiskit.__version__, "numpy_version": np.__version__,
                     "qiskit_commit": command("git", "rev-parse", "HEAD", cwd=SUBMODULE),
                     "qiskit_source": str(Path(qiskit.__file__).resolve()),
                     "qiskit_dirty": qiskit_dirty_state(),
                     "rustc": command("rustc", "--version"), "python": sys.version,
                     "platform": platform.platform(), "machine": platform.machine(),
                     "processor": platform.processor(), "logical_cpus": os.cpu_count(),
                     "cargo_lock_sha256": hashlib.sha256((ROOT / "Cargo.lock").read_bytes()).hexdigest(),
                     "rust_source_sha256": hashlib.sha256((ROOT / "src/lib.rs").read_bytes() + (ROOT / "src/precision.rs").read_bytes() + (ROOT / "src/execution.rs").read_bytes()).hexdigest(),
                     "warmup": args.warmup, "repetitions": args.repetitions,
                     "timing_scope": "gate execution, including per-call dispatch/validation and internal scratch; excludes state initialization, circuit construction, IPC, and result extraction"},
        "results": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved raw samples and configuration to {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    parity = sub.add_parser("parity")
    parity.add_argument("--precisions", choices=list(TOLERANCES), nargs="+", default=["f64", "f32", "f16"])
    parity.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4])
    bench = sub.add_parser("bench")
    bench.add_argument("--threads", type=int, nargs="+", default=[1])
    bench.add_argument("--parallel-threshold", type=int, default=1 << 16)
    bench.add_argument("--precisions", choices=list(TOLERANCES), nargs="+", default=["f64"])
    bench.add_argument("--qubits", type=int, nargs="+", default=[12, 16, 20])
    bench.add_argument("--depth", type=int, default=64)
    bench.add_argument("--repetitions", type=int, default=7)
    bench.add_argument("--warmup", type=int, default=2)
    bench.add_argument("--output", type=Path, default=ROOT / "benchmark-results" / "qiskit.json")
    args = parser.parse_args()
    if args.mode == "bench" and (not 1 <= args.repetitions <= 10000
                                 or not 0 <= args.warmup <= 10000 or args.depth < 1):
        parser.error("depth must be positive, repetitions 1..10000, warmup 0..10000")
    if any(workers < 1 for workers in args.threads):
        parser.error("thread counts must be positive")
    if args.mode == "bench" and args.parallel_threshold < 0:
        parser.error("parallel threshold cannot be negative")
    load_qiskit()
    executable = binary()
    if args.mode == "parity":
        run_parity(executable, args.precisions, args.threads)
    else:
        run_bench(executable, args)


if __name__ == "__main__":
    main()
