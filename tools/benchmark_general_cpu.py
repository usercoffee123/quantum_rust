#!/usr/bin/env python3
"""Interleave release statevector binaries on seeded circuit families."""

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile

FAMILIES = {
    "rotation": ("rx", "ry", "rz", "h"),
    "entangling": ("cx", "cx", "cz", "h"),
    "diagonal": ("rz", "cz", "s", "t"),
    "mixed": ("ry", "cx", "rz", "cz", "h"),
}


def request(n, precision, family, layout, length, threads):
    rng = random.Random(2718)
    gates = [{"gate": "h", "target": wire} for wire in range(n)]
    for index in range(length):
        if layout != "local":
            wires = range(n)
        else:
            # Alternate runs on low wires with operations spanning the register.
            wires = range(min(n, 10)) if index % 16 < 12 else range(n)
        target, control = rng.sample(wires, 2)
        kind = rng.choice(FAMILIES[family])
        gate = {"gate": kind, "target": target}
        if kind in ("cx", "cz"):
            if layout == "low-control":
                target, control = max(1, target), 0
                gate["target"] = target
            gate["control"] = control
        if kind in ("rx", "ry", "rz"):
            gate["theta"] = rng.uniform(-1, 1)
        gates.append(gate)
    return dict(num_qubits=n, gates=gates, precision=precision, threads=threads,
                fusion=True, optimize=True, include_state=False, include_norm=False,
                memory_limit_bytes=(1 << n) * 16)


def measure(binary, data):
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout:
        stdin.write((json.dumps(data) + "\n").encode())
        stdin.seek(0)
        with subprocess.Popen([binary], stdin=stdin, stdout=stdout) as process:
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, process.args)
        stdout.seek(0)
        row = json.load(stdout)
    rss = usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    stats = row["compilation"]["stats"]
    return {
        "gate_seconds": row["seconds"][0],
        "peak_rss_bytes": rss,
        # For comparing plans. Sparse gates touch fewer bytes and small states can stay cached.
        "modeled_pass_bytes": 2 * row["state_bytes"] * stats["execution_operations"],
        "stats": stats,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binaries", nargs="+", help="Executables, baseline first")
    parser.add_argument("--qubits", nargs="+", type=int, default=[16, 23])
    parser.add_argument("--gates", type=int, default=96)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--layouts", nargs="+", choices=("global", "local", "low-control"),
                        default=["global", "local"])
    parser.add_argument("--families", nargs="+", choices=tuple(FAMILIES), default=list(FAMILIES))
    args = parser.parse_args()
    if min(args.qubits) < 2 or min(args.threads, args.rounds, args.gates) < 1:
        parser.error("use at least two qubits and positive threads, rounds and gate count")
    for n in args.qubits:
        for precision in ("f32", "f64"):
            for family in args.families:
                for layout in args.layouts:
                    data = request(n, precision, family, layout, args.gates, args.threads)
                    runs = [[] for _ in args.binaries]
                    for trial in range(args.rounds):
                        order = range(len(runs))
                        if trial % 2:
                            order = reversed(order)
                        for i in order:
                            runs[i].append(measure(args.binaries[i], data))
                    medians = [statistics.median(r["gate_seconds"] for r in rows) for rows in runs]
                    print(json.dumps(dict(qubits=n, precision=precision, family=family,
                                          layout=layout, binaries=args.binaries, runs=runs,
                                          median_seconds=medians,
                                          speedup=[medians[0] / value for value in medians])),
                          flush=True)


if __name__ == "__main__":
    main()
