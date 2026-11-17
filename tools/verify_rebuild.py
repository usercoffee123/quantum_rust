#!/usr/bin/env python3
"""Validate and benchmark the rebuilt Rust engine against an installed Qiskit wheel.

This is a new comparison, not reproduction of the missing pinned-source results.
Uses the surviving circuit generators and tolerance policy without changing them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

import compare_qiskit as ref  # Sets numerical thread limits before NumPy imports.
import benchmark_aer as families
import numpy as np
import qiskit
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

ref.np, ref.qiskit, ref.QuantumCircuit, ref.Statevector = np, qiskit, QuantumCircuit, Statevector
ROOT = ref.ROOT


class Session:
    def __init__(self, binary):
        self.proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, bufsize=1)

    def run(self, request):
        payload = json.dumps(request) + '\n'
        start = time.perf_counter()
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('Rust worker exited unexpectedly')
        value = json.loads(line)
        elapsed = time.perf_counter() - start
        if 'error' in value:
            raise RuntimeError(value['error'])
        return value, elapsed

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=30)
        self.proc.stdout.close()
        if self.proc.returncode:
            raise RuntimeError('Rust worker returned nonzero exit status')


def parity(session):
    cases = list(ref.parity_cases())
    expected = [Statevector.from_instruction(ref.circuit(n, gates)).data
                for _, n, gates in cases]
    rows = []
    for precision, tolerance in ref.TOLERANCES.items():
        for threads in (1, 4):
            for fused in (False, True):
                worst = drift = 0.0
                for (name, n, gates), target in zip(cases, expected):
                    row, _ = session.run(dict(num_qubits=n, gates=gates, precision=precision,
                                              threads=threads, parallel_threshold=0,
                                              fusion=fused, optimize=fused))
                    actual = ref.complex_array(row['amplitudes'])
                    np.testing.assert_allclose(actual, target, atol=tolerance['atol'],
                                               rtol=tolerance['rtol'], err_msg=name)
                    np.testing.assert_allclose(row['probabilities'], np.abs(target)**2,
                                               atol=tolerance['atol'], rtol=tolerance['rtol'])
                    assert abs(row['norm_sqr'] - 1) <= tolerance['norm'], name
                    worst = max(worst, float(np.max(np.abs(actual - target))))
                    drift = max(drift, abs(row['norm_sqr'] - 1))
                rows.append(dict(precision=precision, threads=threads, fusion=fused,
                                 optimize=fused, circuits=len(cases),
                                 max_amplitude_error=worst, max_norm_drift=drift))
                print('PARITY', rows[-1], flush=True)
    return rows


def bench(session, args, save):
    rows = []
    for n in args.qubits:
        for family in ('mixed', 'scrambled_bell'):
            gates = (ref.workload(n, n - 1, 64, True) if family == 'mixed'
                     else families.entangled_gates(n, 4, 42, 'scrambled_bell'))
            instruction = ref.circuit(n, gates).to_instruction()
            expected = Statevector.from_instruction(ref.circuit(n, gates)).data
            probes = sorted({0, 1, (1 << n) - 1, (1 << n) // 3})
            python_times = []

            def python_trial():
                state = Statevector.from_int(0, 1 << n)
                start = time.perf_counter()
                result = state.evolve(instruction)
                seconds = time.perf_counter() - start
                np.testing.assert_allclose(result.data[probes], expected[probes], atol=1e-11, rtol=1e-11)
                return seconds

            # One baseline per identical circuit, reused for all Rust configurations.
            for _ in range(args.warmup):
                python_trial()
            for trial in range(args.repetitions):
                python_times.append(python_trial())
            baseline = ref.summary(python_times)
            for threads in (1, 4):
                for fused in (False, True):
                    request = dict(num_qubits=n, gates=gates, precision='f64', threads=threads,
                                   include_state=False, include_norm=False, probe_indices=probes,
                                   fusion=fused, optimize=fused)
                    # Check the full state independently of timing, without JSON expansion.
                    with tempfile.TemporaryDirectory(prefix='qiskit-rebuild-') as tmp:
                        path = Path(tmp) / 'state.bin'
                        session.run(dict(request, amplitudes_output=str(path)))
                        actual = np.fromfile(path, dtype='<c16')
                        np.testing.assert_allclose(actual, expected, atol=1e-11, rtol=1e-11)
                        error = float(np.max(np.abs(actual - expected)))
                    for _ in range(args.warmup):
                        session.run(request)
                    times, requests = [], []
                    for _ in range(args.repetitions):
                        response, wall = session.run(request)
                        np.testing.assert_allclose(ref.complex_array(response['probes']), expected[probes],
                                                   atol=1e-11, rtol=1e-11)
                        times.append(response['seconds'][0])
                        requests.append(wall)
                    timing = ref.summary(times)
                    row = dict(num_qubits=n, family=family, gates=len(gates), threads=threads,
                               precision='f64', fusion=fused, optimize=fused,
                               qiskit=baseline, rust=timing, rust_request=ref.summary(requests),
                               qiskit_over_rust=baseline['median_seconds']/timing['median_seconds'],
                               max_full_state_error=error,
                               compilation=response.get('compilation'),
                               circuit_sha256=hashlib.sha256(json.dumps(gates, sort_keys=True).encode()).hexdigest())
                    rows.append(row)
                    save(rows)
                    print(f"BENCH n={n} {family} threads={threads} fusion={fused}: "
                          f"Rust {timing['median_seconds']:.6f}s, Python {baseline['median_seconds']:.6f}s, "
                          f"ratio {row['qiskit_over_rust']:.2f}x", flush=True)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['parity', 'bench', 'all'], default='all')
    p.add_argument('--qubits', nargs='+', type=int, default=[16, 20])
    p.add_argument('--repetitions', type=int, default=5)
    p.add_argument('--warmup', type=int, default=1)
    p.add_argument('--output', type=Path, default=ROOT / 'benchmark-results/rebuild/results.json')
    args = p.parse_args()
    if any(not 4 <= n <= 22 for n in args.qubits) or not 1 <= args.repetitions <= 100 or not 0 <= args.warmup <= 100:
        p.error('qubits must be 4..22, repetitions 1..100, warmup 0..100')
    binary = ref.binary()
    result = dict(complete=False, metadata=dict(
        reference='installed Qiskit quantum_info.Statevector wheel, not Aer or the historical submodule',
        python=sys.version, qiskit=qiskit.__version__, numpy=np.__version__, platform=platform.platform(),
        machine=platform.machine(), logical_cpus=os.cpu_count(), rustc=ref.command('rustc', '--version'),
        date=time.strftime('%Y-%m-%d'), repetitions=args.repetitions, warmup=args.warmup,
        source_sha256=hashlib.sha256(b''.join(f.read_bytes() for f in sorted((ROOT/'src').rglob('*.rs')))).hexdigest(),
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        numerical_thread_limits={k: os.environ.get(k) for k in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS']},
        timing_scope='Rust gate timer excludes allocation, compilation, pool creation, JSON and probes; Python evolve timer excludes initial state/circuit creation but includes output/scratch allocation. Rust fresh-request timings are separately recorded. Python baselines measured first and reused; engine order not randomized. FP64 throughout benchmarks.'))
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save(rows=None):
        if rows is not None:
            result['benchmarks'] = rows
        tmp = args.output.with_suffix('.tmp')
        tmp.write_text(json.dumps(result, indent=2) + '\n')
        tmp.replace(args.output)

    session = Session(binary)
    try:
        if args.mode in ('parity', 'all'):
            result['parity'] = parity(session)
            save()
        if args.mode in ('bench', 'all'):
            bench(session, args, save)
        result['complete'] = True
        save()
    finally:
        session.close()
    print(args.output)


if __name__ == '__main__':
    main()
