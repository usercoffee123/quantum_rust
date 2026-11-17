#!/usr/bin/env python3
"""Fresh FP64 request-latency comparison with optimized Aer and optional old Rust binary."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

# Fix OpenMP/BLAS settings before either native backend is imported.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qubits', type=int, nargs='+', default=[20, 22])
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--layers', type=int, default=8)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--baseline-binary', type=Path)
    parser.add_argument('--output', type=Path, default=Path('benchmark-results/retry-aer/results.json'))
    args = parser.parse_args()
    if any(not 6 <= n <= 24 for n in args.qubits) or args.threads < 1 or args.layers < 1 or not 1 <= args.repetitions <= 100:
        parser.error('qubits 6..24; positive threads/layers; repetitions 1..100')
    import compare_qiskit as ref
    import benchmark_aer as helpers
    os.environ['OMP_NUM_THREADS'] = str(args.threads)
    os.environ['OMP_THREAD_LIMIT'] = str(args.threads)
    import numpy as np
    import qiskit
    from qiskit import QuantumCircuit
    import qiskit_aer
    from qiskit_aer import AerSimulator
    ref.np, ref.QuantumCircuit = np, QuantumCircuit
    from verify_rebuild import Session
    binary = ref.binary()
    rust = Session(binary)
    old = Session(args.baseline_binary.resolve()) if args.baseline_binary else None
    backend = AerSimulator(method='statevector', device='CPU', precision='double',
                           max_parallel_threads=args.threads, max_parallel_experiments=1,
                           max_parallel_shots=1, fusion_enable=True, enable_truncation=False,
                           statevector_parallel_threshold=0, zero_threshold=0.)
    result = dict(complete=False, metadata=dict(
        date=time.strftime('%Y-%m-%d'), platform=platform.platform(), threads=args.threads,
        layers=args.layers, repetitions=args.repetitions, warmup=1, qiskit=qiskit.__version__,
        aer=qiskit_aer.__version__, numpy=np.__version__, python=sys.version,
        source_sha256=hashlib.sha256(b''.join(f.read_bytes() for f in sorted((ref.ROOT/'src').rglob('*.rs')))).hexdigest(),
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        baseline_sha256=hashlib.sha256(args.baseline_binary.read_bytes()).hexdigest() if old else None,
        timing_scope='Fresh FP64 state, execution and equivalent amplitude probes. Rust parsing/pool/compilation and Aer job/fusion included. Imports, circuit construction, native process startup, full validation excluded. Engine order rotates; BLAS limits one; OpenMP/state workers matched.'), results=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        tmp = args.output.with_suffix('.tmp')
        tmp.write_text(json.dumps(result, indent=2)+'\n')
        tmp.replace(args.output)

    def aer_run(circuit, n):
        start = time.perf_counter()
        value = backend.run(circuit, shots=1, seed_simulator=42).result()
        data = value.data(0)
        elapsed = time.perf_counter()-start
        assert value.success
        meta = value.results[0].metadata
        assert meta['parallel_state_update'] == args.threads, meta
        assert meta['num_qubits'] == n
        return data, elapsed, meta

    try:
        for n in args.qubits:
            for family in ('scrambled_bell', 'random_matching'):
                gates = helpers.entangled_gates(n, args.layers, 42, family)
                circuit = ref.circuit(n, gates)
                probes = sorted({0, 1, (1 << n)-1, *np.random.default_rng(42).integers(0, 1 << n, 64).tolist()})
                request = dict(num_qubits=n, gates=gates, precision='f64', threads=args.threads,
                               fusion=True, optimize=False, include_state=False,
                               include_norm=False, probe_indices=probes)
                full = circuit.copy()
                full.save_statevector()
                data, _, _ = aer_run(full, n)
                expected = np.asarray(data['statevector']).copy()
                expected_probes = expected[probes].copy()
                errors = {}
                for name, session in [('rust', rust)] + ([('old_rust', old)] if old else []):
                    with tempfile.TemporaryDirectory() as d:
                        path = Path(d)/'state.bin'
                        session.run(dict(request, amplitudes_output=str(path)))
                        actual = np.fromfile(path, dtype='<c16')
                        np.testing.assert_allclose(actual, expected, atol=1e-11, rtol=1e-9)
                        errors[name] = float(np.max(np.abs(actual-expected)))
                del expected, actual, data, full
                probe_circuit = circuit.copy()
                probe_circuit.save_amplitudes(probes)
                samples = {'rust': [], 'aer': []}
                if old:
                    samples['old_rust'] = []
                names = list(samples)
                metadata = None
                for trial in range(args.repetitions+1):
                    order = names[trial % len(names):]+names[:trial % len(names)]
                    for name in order:
                        if name == 'aer':
                            data, elapsed, metadata = aer_run(probe_circuit, n)
                            actual = data['amplitudes']
                        else:
                            value, elapsed = (old if name == 'old_rust' else rust).run(request)
                            actual = ref.complex_array(value['probes'])
                        np.testing.assert_allclose(actual, expected_probes, atol=1e-11, rtol=1e-9)
                        if trial:
                            samples[name].append(elapsed)
                row = dict(num_qubits=n, family=family, gates=len(gates), full_state_max_errors=errors,
                           timings={name: ref.summary(values) for name, values in samples.items()},
                           aer_metadata=metadata)
                row['aer_over_rust_ratio'] = row['timings']['aer']['median_seconds']/row['timings']['rust']['median_seconds']
                if old:
                    row['old_over_new_ratio'] = row['timings']['old_rust']['median_seconds']/row['timings']['rust']['median_seconds']
                result['results'].append(row)
                save()
                print(json.dumps(row), flush=True)
        result['complete'] = True
        save()
    finally:
        rust.close()
        if old:
            old.close()
    print(args.output)


if __name__ == '__main__':
    main()
