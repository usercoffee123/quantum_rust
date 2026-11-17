#!/usr/bin/env python3
"""Measure a deep 25-qubit entangled workload, targeting ~240 s in Rust."""
import argparse
import json
import os
from pathlib import Path

import benchmark_aer as bench


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layers', type=int, default=350)
    parser.add_argument('--output', type=Path, default=Path('benchmark-results/four-minute-25q'))
    args = parser.parse_args()
    if args.layers < 1:
        parser.error('--layers must be positive')
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    threads, layers = 4, args.layers
    gates = bench.entangled_gates(25, layers, 42, 'scrambled_bell')
    probes = [0, 1, 1234567, (1 << 25) - 1]
    request = dict(num_qubits=25, gates=gates, precision='f64', threads=threads,
                   memory_limit_bytes=1 << 30, include_state=False, include_norm=False,
                   probe_indices=probes)
    (out / 'request.json').write_text(json.dumps(request) + '\n')
    result = dict(qubits=25, layers=layers, threads=threads, precision='f64',
                  family='scrambled_bell', seed=42, gate_count=len(gates),
                  cx_count=sum(g['gate'] == 'cx' for g in gates),
                  half_cut_entropy_bits_by_construction=12, complete=False)
    def save():
        (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    save()
    session = bench.RustSession(bench.reference.binary())
    try:
        session.run(dict(num_qubits=0, gates=[], include_state=False, include_norm=False))
        print(f'RUN Rust: {layers} layers, {len(gates)} gates', flush=True)
        response, elapsed = session.run(request)
        result.update(rust_seconds=elapsed, rust_probes=response['probes'])
        save()
        print(f'Rust completed in {elapsed:.3f} seconds', flush=True)
    finally:
        session.close()
    os.environ['OMP_NUM_THREADS'] = str(threads)
    os.environ['OMP_THREAD_LIMIT'] = str(threads)
    bench.reference.load_qiskit()
    from qiskit_aer import AerSimulator
    circuit = bench.reference.circuit(25, gates)
    circuit.save_amplitudes(probes)
    backend = bench.make_backend(threads, True, AerSimulator)
    print('RUN Aer: same circuit, FP64, four threads, fusion enabled', flush=True)
    data, elapsed, metadata = bench.aer_run(backend, circuit, threads, 25)
    expected = bench.reference.complex_array(response['probes'])
    bench.reference.np.testing.assert_allclose(data['amplitudes'], expected, atol=1e-11, rtol=1e-8)
    result.update(aer_seconds=elapsed, aer_metadata=metadata,
                  max_probe_error=float(max(abs(data['amplitudes'] - expected))),
                  aer_version=__import__('qiskit_aer').__version__, complete=True)
    save()
    print(f'Aer completed in {elapsed:.3f} seconds; probe parity passed', flush=True)


if __name__ == '__main__':
    main()
