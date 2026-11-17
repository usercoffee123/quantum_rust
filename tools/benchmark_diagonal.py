#!/usr/bin/env python3
"""Compare specialized diagonal gates with Gate::U in the same release binary."""
import argparse
import cmath
import hashlib
import json
import math
import platform
import statistics
from pathlib import Path

import compare_qiskit as reference


def matrices(gates):
    out = []
    for g in gates:
        name = g["gate"]
        if name == "h":
            out.append(g)
            continue
        phases = {"z": (1, -1), "s": (1, 1j),
                  "t": (1, cmath.exp(1j * math.pi / 4))}
        if name == "rz":
            low, high = cmath.exp(-1j * g["theta"] / 2), cmath.exp(1j * g["theta"] / 2)
        else:
            low, high = phases[name]
        encode = lambda z: [complex(z).real, complex(z).imag]
        out.append({"gate": "u", "target": g["target"],
                    "matrix": [[encode(low), [0, 0]], [[0, 0], encode(high)]]})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qubits", type=int, nargs="+", default=[16, 20])
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--precisions", choices=list(reference.TOLERANCES), nargs="+", default=["f64", "f32", "f16"])
    parser.add_argument("--gates", choices=["z", "s", "t", "rz"], nargs="+", default=["z", "s", "t", "rz"])
    parser.add_argument("--depth", type=int, default=64)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--output", type=Path, default=reference.ROOT / "benchmark-results/diagonal.json")
    args = parser.parse_args()
    if any(not 1 <= n <= 24 for n in args.qubits) or any(t < 1 for t in args.threads):
        parser.error("qubits must be 1..24 and threads positive")
    if args.depth < 1 or not 1 <= args.repetitions <= 10000 or not 0 <= args.warmup <= 10000:
        parser.error("depth must be positive, repetitions 1..10000, warmup 0..10000")
    executable = reference.binary()
    # NumPy/Qiskit are not needed for this matched Rust comparison. Qiskit
    # correctness is covered separately by compare_qiskit.py parity.
    rows = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for n in args.qubits:
        for precision in args.precisions:
            for threads in args.threads:
                for target in sorted({0, n // 2, n - 1}):
                    for name in args.gates:
                        preparation = [{"gate": "h", "target": q} for q in range(n)]
                        gates = [{"gate": name, "target": target} for _ in range(args.depth)]
                        if name == "rz":
                            for i, gate in enumerate(gates):
                                gate["theta"] = 0.219 + i * 0.011
                        probes = sorted({0, (1 << n) - 1, (1 << target), (1 << n) // 3})
                        request = {"num_qubits": n, "precision": precision, "threads": threads,
                                   "include_state": False, "probe_indices": probes,
                                   "benchmark": {"repetitions": args.repetitions, "warmup": args.warmup}}
                        specialized = dict(request, gates=preparation + gates)
                        general = dict(request, gates=preparation + matrices(gates))
                        reverse = len(rows) % 2
                        actual = reference.rust_results(executable, [general, specialized] if reverse else [specialized, general])
                        baseline, optimized = actual if reverse else actual[::-1]
                        errors = [abs(complex(*a) - complex(*b)) for a, b in zip(optimized["probes"], baseline["probes"])]
                        tol = reference.TOLERANCES[precision]
                        assert max(errors) <= tol["atol"]
                        assert abs(optimized["norm_sqr"] - baseline["norm_sqr"]) <= tol["norm"]
                        row = {"num_qubits": n, "precision": precision, "threads": threads, "target": target,
                               "gate": name, "preparation_gates": n, "diagonal_gates": args.depth,
                               "specialized": reference.summary(optimized["seconds"]),
                               "general_matrix": reference.summary(baseline["seconds"]),
                               "max_probe_difference": max(errors),
                               "norm_difference": abs(optimized["norm_sqr"] - baseline["norm_sqr"]),
                               "state_bytes": optimized["state_bytes"]}
                        row["speedup"] = row["general_matrix"]["median_seconds"] / row["specialized"]["median_seconds"]
                        rows.append(row)
                        args.output.write_text(json.dumps({"metadata_complete": False, "results": rows}, indent=2) + "\n")
                        print(f"n={n} {precision} threads={threads} target={target} {name}: {row['speedup']:.2f}x", flush=True)
    metadata = {"baseline": "Gate::U general matrix kernel in the same binary",
                "timing_scope": "whole gate sequence, including identical H preparation; excludes initialization, pool creation, IPC, export",
                "order": "alternating specialized/general first by configuration",
                "platform": platform.platform(), "rustc": reference.command("rustc", "--version"),
                "source_sha256": hashlib.sha256(b"".join((reference.ROOT / p).read_bytes() for p in
                    ["src/lib.rs", "src/precision.rs", "src/execution.rs"])).hexdigest(),
                "repetitions": args.repetitions, "warmup": args.warmup}
    args.output.write_text(json.dumps({"metadata_complete": True, "metadata": metadata, "results": rows}, indent=2) + "\n")
    print(f"Saved {len(rows)} matched comparisons to {args.output}")
    for name in args.gates:
        print(name, "median speedup", round(statistics.median(r["speedup"] for r in rows if r["gate"] == name), 2))


if __name__ == "__main__":
    main()
