#!/usr/bin/env python3
"""Audit and benchmark a stride-CX body with a small entangled preparation."""
import collections
import json
import os
from pathlib import Path

import benchmark_aer as b


def main():
    out = Path('benchmark-results/fusion-insensitive-25q')
    out.mkdir(parents=True, exist_ok=True)
    os.environ['OMP_NUM_THREADS'] = '4'
    os.environ['OMP_THREAD_LIMIT'] = '4'
    b.reference.load_qiskit()
    from qiskit_aer import AerSimulator
    np = b.reference.np
    prep = b.entangled_gates(25, 0, 42, 'scrambled_bell')
    body = [dict(gate='cx', control=i % 25, target=(i + 12) % 25) for i in range(1000)]
    gates = prep + body
    probes = [0, 1, 1234567, (1 << 25) - 1]
    req = dict(num_qubits=25, gates=gates, precision='f64', threads=4,
               memory_limit_bytes=1 << 30, include_state=False, include_norm=False,
               probe_indices=probes)
    (out / 'request.json').write_text(json.dumps(req) + '\n')
    circuit = b.reference.circuit(25, gates)
    probe = circuit.copy(); probe.save_amplitudes(probes)
    on = b.make_backend(4, True, AerSimulator)
    off = b.make_backend(4, False, AerSimulator)
    # Inspect the CX-only body separately without removing optimized settings.
    audit = b.reference.circuit(25, body); audit.save_amplitudes([0])
    verbose = b.make_backend(4, True, AerSimulator)
    verbose.set_options(fusion_verbose=True)
    _, _, body_meta = b.aer_run(verbose, audit, 4, 25)
    assert body_meta['fusion']['applied'] is False, body_meta['fusion']
    _, _, whole_meta = b.aer_run(verbose, probe, 4, 25)
    ops = whole_meta['fusion'].get('output_ops', [])
    counts = dict(collections.Counter(op['name'] for op in ops))
    print('AUDIT: body fusion applied=False; whole output operations:', counts, flush=True)
    session = b.RustSession(b.reference.binary())
    try:
        session.run(dict(num_qubits=0, gates=[], include_state=False, include_norm=False))
        full = circuit.copy(); full.save_statevector()
        data, _, _ = b.aer_run(on, full, 4, 25)
        state = np.asarray(data['statevector'], dtype=np.complex128)
        path = out / '.verify.bin'
        try:
            response, _ = session.run({**req, 'amplitudes_output': str(path)})
            mapped = np.memmap(path, dtype='<c16', mode='r', shape=(1 << 25,))
            try:
                accuracy = b.full_comparison(state, mapped, np)
            finally:
                mapped._mmap.close()
        finally:
            path.unlink(missing_ok=True)
        entropy = b.entropies(state, 25, np)
        expected = state[probes].copy()
        del state, data, full
        times = {k: [] for k in ['rust', 'aer_on', 'aer_off']}
        for trial in range(4):
            print(f'TRIAL {trial}: ' + ('warmup' if trial == 0 else 'measured'), flush=True)
            names = ['rust', 'aer_on', 'aer_off']
            names = names[trial % 3:] + names[:trial % 3]
            for name in names:
                if name == 'rust':
                    value, seconds = session.run(req)
                    amps = b.reference.complex_array(value['probes'])
                else:
                    value, seconds, metadata = b.aer_run(on if name == 'aer_on' else off, probe, 4, 25)
                    amps = value['amplitudes']
                np.testing.assert_allclose(amps, expected, atol=1e-11, rtol=1e-8)
                if trial:
                    times[name].append(seconds)
        result = dict(complete=True, qubits=25, threads=4, precision='f64',
                      gate_count=len(gates), preparation_gates=len(prep), body_gates=len(body),
                      body_fusion_metadata=body_meta['fusion'],
                      whole_fusion_operation_counts=counts,
                      whole_fusion_metadata={k: v for k, v in whole_meta['fusion'].items() if k != 'output_ops'},
                      accuracy=accuracy, entanglement=entropy,
                      timings={k: b.reference.summary(v) for k, v in times.items()},
                      aer_version=__import__('qiskit_aer').__version__)
        (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2), flush=True)
    finally:
        session.close()


if __name__ == '__main__':
    main()
