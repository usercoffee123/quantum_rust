#!/usr/bin/env python3
"""Matched FP64 CPU requests against Aer on dense, highly entangled circuits."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

import benchmark_sizes as sizes
import compare_qiskit as reference


def entangled_gates(n, layers, seed, family):
    """Maximal initial half-cut entanglement, followed by non-Clifford scrambling."""
    rng = random.Random(seed)
    cut = n // 2
    gates = []
    for q in range(cut):
        gates += [{'gate': 'h', 'target': q}, {'gate': 'cx', 'control': q, 'target': q + cut}]
    if n % 2:
        gates.append({'gate': 'h', 'target': n - 1})
    for _ in range(layers):
        for q in range(n):
            gates += [{'gate': 'ry', 'target': q, 'theta': rng.uniform(-math.pi, math.pi)},
                      {'gate': 'rz', 'target': q, 'theta': rng.uniform(-math.pi, math.pi)}]
        groups = [list(range(cut)), list(range(cut, n))] if family == 'scrambled_bell' else [list(range(n))]
        for group in groups:
            rng.shuffle(group)
            for i in range(0, len(group) - 1, 2):
                a, b = group[i:i + 2]
                gates.append({'gate': 'cx', 'control': a, 'target': b})
    return gates


def entropy_from_density(rho, np):
    values = np.linalg.eigvalsh(rho).real
    values = np.clip(values, 0, None)
    values /= values.sum()
    positive = values[values > 0]
    return float(-np.sum(positive * np.log2(positive)))


def entropies(state, n, np):
    k = min(5, n // 2)
    # Exact reduced density matrices; no full 12|13 Schmidt decomposition.
    low = state.reshape(-1, 1 << k)
    rho_low = low.T @ low.conj()
    high = state.reshape(1 << k, -1)
    rho_high = high @ high.conj().T
    return {'subsystem_qubits': k, 'maximum_bits': k,
            'low_qubit_entropy_bits': entropy_from_density(rho_low, np),
            'high_qubit_entropy_bits': entropy_from_density(rho_high, np)}


def full_comparison(expected, actual, np):
    left, right, real, imag = [], [], [], []
    max_error = 0.0
    for start in range(0, len(expected), 1 << 18):
        a, b = expected[start:start + (1 << 18)], actual[start:start + (1 << 18)]
        left.append(float(np.vdot(a, a).real))
        right.append(float(np.vdot(b, b).real))
        overlap = np.vdot(a, b)
        real.append(float(overlap.real)); imag.append(float(overlap.imag))
        max_error = max(max_error, float(np.max(np.abs(a - b))))
    an, bn = math.fsum(left), math.fsum(right)
    fidelity = abs(complex(math.fsum(real), math.fsum(imag)))**2 / (an * bn)
    assert abs(an - 1) < 1e-10 and abs(bn - 1) < 1e-10
    assert max_error < 1e-11 and fidelity > 1 - 1e-10
    return {'max_amplitude_error': max_error, 'aer_norm': an, 'rust_norm': bn,
            'normalized_fidelity': fidelity, 'amplitudes_compared': len(expected)}


class RustSession:
    def __init__(self, executable):
        self.proc = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)

    def run(self, request):
        payload = json.dumps(request) + '\n'
        start = time.perf_counter()
        self.proc.stdin.write(payload); self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('Rust worker exited: ' + self.proc.stderr.read())
        value = json.loads(line)
        elapsed = time.perf_counter() - start
        if 'error' in value:
            raise RuntimeError(value['error'])
        return value, elapsed

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill(); self.proc.wait()
        self.proc.stdout.close(); self.proc.stderr.close()


def make_backend(threads, fusion, AerSimulator):
    return AerSimulator(method='statevector', device='CPU', precision='double',
                        max_parallel_threads=threads, max_parallel_experiments=1,
                        max_parallel_shots=1, fusion_enable=fusion, enable_truncation=False,
                        max_memory_mb=4096, zero_threshold=0.0)


def aer_run(backend, circuit, threads, n):
    start = time.perf_counter()
    result = backend.run(circuit, shots=1, seed_simulator=42).result()
    data = result.data(0)
    elapsed = time.perf_counter() - start
    assert result.success, str(result)
    metadata = result.results[0].metadata
    assert metadata['method'] == 'statevector' and metadata['device'] == 'CPU'
    assert metadata['num_qubits'] == n
    assert metadata['parallel_state_update'] == threads, metadata
    return data, elapsed, metadata


def worker(args):
    # Import numerical libraries only after fixing OpenMP limits for this worker.
    os.environ['OMP_NUM_THREADS'] = str(args.worker_threads)
    os.environ['OMP_THREAD_LIMIT'] = str(args.worker_threads)
    reference.load_qiskit()
    import qiskit_aer
    from qiskit_aer import AerSimulator
    np = reference.np
    reason = sizes.skip_reason(3 << 30, 4 << 30, sizes.memory_snapshot(), 1 << 30, .25) if args.qubits == 25 else None
    if reason:
        raise RuntimeError('Insufficient headroom for full-state verification: ' + reason)
    executable = reference.binary()
    session = RustSession(executable)
    rows = []
    destination = args.output / f'threads-{args.worker_threads}.json'
    metadata = {'threads': args.worker_threads, 'precision': 'f64', 'qubits': args.qubits,
                'layers': args.layers, 'seed': args.seed, 'repetitions': args.repetitions, 'warmup': args.warmup,
                'aer_version': qiskit_aer.__version__, 'qiskit_version': reference.qiskit.__version__,
                'qiskit_commit': reference.command('git', 'rev-parse', 'HEAD', cwd=reference.SUBMODULE),
                'platform': platform.platform(), 'rustc': reference.command('rustc', '--version'),
                'environment': {k: os.environ.get(k) for k in ['OMP_NUM_THREADS', 'OMP_THREAD_LIMIT', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS']},
                'source_sha256': hashlib.sha256(b''.join((reference.ROOT / p).read_bytes() for p in
                    ['src/lib.rs', 'src/precision.rs', 'src/execution.rs', 'src/bin/statevector.rs'])).hexdigest(),
                'scope': 'fresh-state request wall latency including allocation/initialization, gate execution and identical amplitude-probe output; Rust worker-process startup and circuit construction excluded; Rust JSON parsing, pool creation and Aer job dispatch included'}

    def checkpoint(complete=False):
        destination.write_text(json.dumps({'complete': complete, 'metadata': metadata, 'results': rows}, indent=2) + '\n')

    try:
        session.run({'num_qubits': 0, 'gates': [], 'include_state': False, 'include_norm': False})
        for family in args.families:
            n, threads = args.qubits, args.worker_threads
            gates = entangled_gates(n, args.layers, args.seed, family)
            args.output.joinpath(f'{family}-circuit.json').write_text(json.dumps({'num_qubits': n, 'gates': gates}, indent=2) + '\n')
            rng = random.Random(args.seed + 991)
            probes = sorted({0, (1 << n) - 1, *rng.sample(range(1 << n), min(64, 1 << n))})
            request = {'num_qubits': n, 'gates': gates, 'precision': 'f64', 'threads': threads,
                       'memory_limit_bytes': 1 << 30, 'include_state': False, 'include_norm': False,
                       'probe_indices': probes}
            circuit = reference.circuit(n, gates)
            probe_circuit = circuit.copy(); probe_circuit.save_amplitudes(probes)
            backend = make_backend(threads, True, AerSimulator)
            validation_circuit = circuit.copy(); validation_circuit.save_statevector()
            print(f'VALIDATE {family}, n={n}, threads={threads}, gates={len(gates)}', flush=True)
            data, _, validation_metadata = aer_run(backend, validation_circuit, threads, n)
            state = np.asarray(data['statevector'], dtype=np.complex128)
            assert state.dtype.itemsize == 16
            expected_probes = state[probes].copy()
            path = args.output / f'.verify-{threads}.bin'
            try:
                session.run({**request, 'include_norm': True, 'amplitudes_output': str(path)})
                assert path.stat().st_size == (1 << n) * 16
                mapped = np.memmap(path, dtype='<c16', mode='r', shape=(1 << n,))
                try:
                    accuracy = full_comparison(state, mapped, np)
                finally:
                    mapped._mmap.close()
            finally:
                path.unlink(missing_ok=True)
            entropy = entropies(state, n, np)
            threshold = 4.8 if n >= 12 else .5 * entropy['maximum_bits']
            assert min(entropy['low_qubit_entropy_bits'], entropy['high_qubit_entropy_bits']) > threshold
            if family == 'scrambled_bell':
                entropy['half_cut_entropy_bits_by_construction'] = n // 2
            del state, data, validation_circuit
            row = {'family': family, 'num_qubits': n, 'threads': threads, 'precision': 'f64',
                   'gate_count': len(gates), 'two_qubit_gates': sum(g['gate'] == 'cx' for g in gates),
                   'circuit_depth': circuit.depth(), 'circuit_sha256': hashlib.sha256(json.dumps(gates, sort_keys=True).encode()).hexdigest(),
                   'accuracy': accuracy, 'entanglement': entropy, 'aer_validation_metadata': validation_metadata}
            rust_times, aer_times = [], []
            for trial in range(args.warmup + args.repetitions):
                print(f'TIME {family}, threads={threads}, trial={trial+1}', flush=True)
                if trial % 2:
                    aer_data, at, am = aer_run(backend, probe_circuit, threads, n)
                    rust, rt = session.run(request)
                else:
                    rust, rt = session.run(request)
                    aer_data, at, am = aer_run(backend, probe_circuit, threads, n)
                assert rust['precision'] == 'f64' and rust['threads'] == threads and rust['compute_bits'] == 64
                np.testing.assert_allclose(reference.complex_array(rust['probes']), expected_probes, atol=1e-11, rtol=1e-8)
                np.testing.assert_allclose(aer_data['amplitudes'], expected_probes, atol=1e-11, rtol=1e-8)
                if trial >= args.warmup:
                    rust_times.append(rt); aer_times.append(at)
            row.update(rust=reference.summary(rust_times), aer_fusion_on=reference.summary(aer_times),
                       aer_metadata=am, aer_over_rust_ratio=reference.summary(aer_times)['median_seconds'] / reference.summary(rust_times)['median_seconds'])
            # Diagnostic only: primary comparison retains Aer default fusion.
            plain = make_backend(threads, False, AerSimulator)
            plain_times = []
            for trial in range(args.warmup + args.repetitions):
                data, elapsed, plain_metadata = aer_run(plain, probe_circuit, threads, n)
                np.testing.assert_allclose(data['amplitudes'], expected_probes, atol=1e-11, rtol=1e-8)
                if trial >= args.warmup: plain_times.append(elapsed)
            row.update(aer_fusion_off=reference.summary(plain_times), aer_fusion_off_metadata=plain_metadata)
            rows.append(row); checkpoint()
            print(f"RESULT {family} threads={threads}: Rust={row['rust']['median_seconds']:.3f}s Aer={row['aer_fusion_on']['median_seconds']:.3f}s ratio={row['aer_over_rust_ratio']:.3f}", flush=True)
        checkpoint(complete=True)
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qubits', type=int, default=25)
    parser.add_argument('--layers', type=int, default=8)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--threads', type=int, nargs='+', default=[1, 4])
    parser.add_argument('--families', choices=['scrambled_bell', 'random_matching'], nargs='+', default=['scrambled_bell', 'random_matching'])
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--warmup', type=int, default=1)
    parser.add_argument('--output', type=Path, default=reference.ROOT / 'benchmark-results/2026-11-08-aer25')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--worker-threads', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 6 <= args.qubits <= 25 or args.layers < 1 or args.repetitions < 1 or args.warmup < 0 or any(t < 1 for t in args.threads):
        parser.error('qubits must be 6..25 and valid positive layers/repetitions/threads')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args)
        return
    for threads in args.threads:
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--worker-threads', str(threads),
                   '--qubits', str(args.qubits), '--layers', str(args.layers), '--seed', str(args.seed),
                   '--families', *args.families, '--repetitions', str(args.repetitions), '--warmup', str(args.warmup),
                   '--output', str(args.output)]
        subprocess.run(command, check=True)
    results = [json.loads((args.output / f'threads-{t}.json').read_text()) for t in args.threads]
    assert all(r['complete'] for r in results)
    for family in args.families:
        assert len({row['circuit_sha256'] for result in results for row in result['results'] if row['family'] == family}) == 1
    (args.output / 'results.json').write_text(json.dumps({'complete': True, 'workers': results}, indent=2) + '\n')


if __name__ == '__main__':
    main()
