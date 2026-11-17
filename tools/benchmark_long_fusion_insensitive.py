#!/usr/bin/env python3
"""Calibrate a ~4-minute Rust workload and compare matched Aer fusion on/off."""
import collections
import hashlib
import json
import os
from pathlib import Path
import platform

import benchmark_aer as b


def main():
    out = Path('benchmark-results/four-minute-fusion-insensitive-25q')
    out.mkdir(parents=True, exist_ok=True)
    prep = b.entangled_gates(25, 0, 42, 'scrambled_bell')
    def gates_for(count):
        return prep + [dict(gate='cx', control=i % 25, target=(i + 12) % 25) for i in range(count)]
    def request(gates, probes):
        return dict(num_qubits=25, gates=gates, precision='f64', threads=4,
                    memory_limit_bytes=1 << 30, include_state=False, include_norm=False,
                    probe_indices=probes)
    result = dict(complete=False, qubits=25, threads=4, precision='f64',
                  platform=platform.platform(), target_rust_seconds=240,
                  timing_scope='single fresh-state request per configuration; process startup and circuit construction excluded; parsing, allocation, initialization, execution and probe result included',
                  source_sha256=hashlib.sha256(b''.join((b.reference.ROOT / p).read_bytes() for p in
                      ['src/lib.rs', 'src/precision.rs', 'src/execution.rs', 'src/bin/statevector.rs'])).hexdigest())
    def save():
        (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    save()
    session = b.RustSession(b.reference.binary())
    try:
        session.run(dict(num_qubits=0, gates=[], include_state=False, include_norm=False))
        print('CALIBRATE Rust: 3000 CX body gates', flush=True)
        _, seconds = session.run(request(gates_for(3000), [0, (1 << 25)-1]))
        count = max(25, round(3000 * 240 / seconds / 25) * 25)
        result['calibration'] = dict(body_gates=3000, rust_seconds=seconds,
                                     selected_body_gates=count)
        print(f'CALIBRATION: {seconds:.3f}s; selected {count} CX body gates', flush=True)
        save()
        gates = gates_for(count)
        os.environ['OMP_NUM_THREADS'] = '4'
        os.environ['OMP_THREAD_LIMIT'] = '4'
        b.reference.load_qiskit()
        from qiskit_aer import AerSimulator
        np = b.reference.np
        # Independent sparse reference: H creates 8192 equal positive amplitudes;
        # every subsequent CX permutes basis indices exactly. No floating gate math.
        indices = np.array([sum(((x >> j) & 1) << j for j in range(12)) |
                            sum(((x >> j) & 1) << (j+12) for j in range(12)) |
                            (((x >> 12) & 1) << 24) for x in range(8192)], dtype=np.uint32)
        for i in range(count):
            control, target = i % 25, (i + 12) % 25
            indices ^= ((indices >> control) & 1) << target
        support = set(map(int, indices))
        assert len(support) == 8192
        import random
        rng = random.Random(42)
        nonzero = rng.sample(sorted(support), 128)
        zeros = []
        while len(zeros) < 128:
            candidate = rng.randrange(1 << 25)
            if candidate not in support and candidate not in zeros:
                zeros.append(candidate)
        probes = sorted({0, (1 << 25)-1, *nonzero, *zeros})
        expected = np.array([2**(-6.5) if i in support else 0 for i in probes], dtype=np.complex128)
        req = request(gates, probes)
        text = json.dumps(req) + '\n'
        (out / 'request.json').write_text(text)
        result.update(gate_count=len(gates), preparation_gates=len(prep), body_gates=count,
                      request_sha256=hashlib.sha256(text.encode()).hexdigest(),
                      reference=dict(method='independent exact sparse basis permutation',
                                     support_size=len(support), probes=len(probes),
                                     selected_nonzero_probes=128, selected_zero_probes=128),
                      measurements={})
        save()
        def validate(amps):
            np.testing.assert_allclose(amps, expected, atol=1e-12, rtol=1e-10)
            return float(np.max(np.abs(amps-expected)))
        print(f'RUN Rust: {len(gates)} gates, {len(probes)} probes', flush=True)
        value, elapsed = session.run(req)
        result['measurements']['rust'] = dict(seconds=elapsed, max_probe_error=validate(b.reference.complex_array(value['probes'])))
        save(); print(f'RESULT Rust {elapsed:.3f}s', flush=True)
        circuit = b.reference.circuit(25, gates); circuit.save_amplitudes(probes)
        for fusion in [True, False]:
            name = 'aer_on' if fusion else 'aer_off'
            backend = b.make_backend(4, fusion, AerSimulator)
            # Verbose output provides an exact operation-count audit on the actual
            # measured circuit; its generation/materialization is included in latency.
            backend.set_options(fusion_verbose=fusion)
            print(f'RUN {name}', flush=True)
            value, elapsed, metadata = b.aer_run(backend, circuit, 4, 25)
            fm = metadata['fusion']
            counts = dict(collections.Counter(op['name'] for op in fm.get('output_ops', [])))
            fm = {k:v for k,v in fm.items() if k != 'output_ops'}
            result['measurements'][name] = dict(seconds=elapsed, max_probe_error=validate(value['amplitudes']),
                                               fusion_metadata=fm, fusion_operation_counts=counts,
                                               reported_threads=metadata['parallel_state_update'])
            if fusion:
                assert counts.get('cx', 0) >= count - 25, counts
                assert counts.get('unitary', 0) <= 25, counts
            save(); print(f'RESULT {name} {elapsed:.3f}s; ops={counts}', flush=True)
        # Exact Schmidt entropy for a uniform binary-linear-subspace state.
        basis = [(1 << j) | (1 << (j+12)) for j in range(12)] + [1 << 24]
        for i in range(count):
            c, t = i % 25, (i + 12) % 25
            basis = [v ^ (((v >> c) & 1) << t) for v in basis]
        def rank(values):
            pivots = {}
            for v in values:
                while v:
                    bit = v.bit_length()-1
                    if bit in pivots:
                        v ^= pivots[bit]
                    else:
                        pivots[bit] = v
                        break
            return len(pivots)
        def entropy(mask):
            return rank([v & mask for v in basis]) + rank([v & (((1 << 25)-1) ^ mask) for v in basis]) - rank(basis)
        result['entanglement_exact_bits'] = dict(
            method='GF(2) ranks of the uniform linear-subspace state; S(A)=rank(projA)+rank(projB)-rank(full)',
            low_five=entropy((1 << 5)-1), high_five=entropy(((1 << 5)-1) << 20),
            low_twelve=entropy((1 << 12)-1))
        result.update(complete=True, aer_version=__import__('qiskit_aer').__version__,
                      qiskit_version=b.reference.qiskit.__version__)
        save()
    finally:
        session.close()


if __name__ == '__main__':
    main()
