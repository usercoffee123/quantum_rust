#!/usr/bin/env python3
"""Rerun the exact saved four-minute CX workload against optimized Aer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform

import benchmark_aer as b


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',type=Path,default=b.reference.ROOT/'benchmark-results/four-minute-fusion-insensitive-25q/request.json')
    parser.add_argument('--output',type=Path,default=b.reference.ROOT/'benchmark-results/2026-11-09-saved-rerun')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    payload=args.request.read_bytes();request=json.loads(payload)
    n=request['num_qubits'];threads=request['threads'];gates=request['gates'];probes=request['probe_indices']
    assert n==25 and threads==4 and request['precision']=='f64'
    assert not request['include_state'] and not request['include_norm']
    assert gates[:25]==b.entangled_gates(25,0,42,'scrambled_bell')
    body=gates[25:]
    assert len(body)==34725
    assert all(g==dict(gate='cx',control=i%25,target=(i+12)%25)for i,g in enumerate(body))
    (args.output/'request.json').write_bytes(payload)
    result=dict(complete=False,source_commit=b.reference.command('git','rev-parse','HEAD'),
                request_sha256=hashlib.sha256(payload).hexdigest(),platform=platform.platform(),
                num_qubits=n,threads=threads,precision='f64',gate_count=len(gates),
                source_sha256=hashlib.sha256(b''.join((b.reference.ROOT/p).read_bytes()for p in
                    ['src/lib.rs','src/precision.rs','src/execution.rs','src/bin/statevector.rs'])).hexdigest(),
                scope='single fresh-state request per engine, no workload warmup; allocation, initialization, execution and equivalent probe output included; native process startup, imports, circuit construction and client serialization excluded; Rust parsing/pool construction and Aer job dispatch included',
                measurements={})
    def save():
        (args.output/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    save()
    os.environ['OMP_NUM_THREADS']=str(threads);os.environ['OMP_THREAD_LIMIT']=str(threads)
    b.reference.load_qiskit()
    from qiskit_aer import AerSimulator
    np=b.reference.np
    # Exact independent reference for the Bell preparation and CX permutations.
    indices=np.array([sum(((x>>j)&1)<<j for j in range(12)) |
                      sum(((x>>j)&1)<<(j+12) for j in range(12)) |
                      (((x>>12)&1)<<24)for x in range(8192)],dtype=np.uint32)
    for g in body:
        indices^=((indices>>g['control'])&1)<<g['target']
    support=set(map(int,indices));assert len(support)==8192
    expected=np.array([2**(-6.5) if i in support else 0 for i in probes],dtype=np.complex128)
    def validate(amps):
        np.testing.assert_allclose(amps,expected,atol=1e-12,rtol=1e-10)
        return float(np.max(np.abs(amps-expected)))
    circuit=b.reference.circuit(n,gates);circuit.save_amplitudes(probes)
    backend=b.make_backend(threads,True,AerSimulator)
    backend.set_options(fusion_verbose=False)
    result.update(aer_version=__import__('qiskit_aer').__version__,qiskit_version=b.reference.qiskit.__version__,
                  qiskit_commit=b.reference.command('git','rev-parse','HEAD',cwd=b.reference.SUBMODULE),
                  independent_probes=len(probes),aer_fusion_verbose=False)
    session=b.RustSession(b.reference.binary())
    try:
        session.run(dict(num_qubits=0,gates=[],include_state=False,include_norm=False))
        print(f'RUN Rust release: {len(gates)} gates, FP64, {threads} threads',flush=True)
        value,elapsed=session.run(request)
        assert value['precision']=='f64' and value['threads']==threads and value['compute_bits']==64
        result['measurements']['rust']=dict(seconds=elapsed,max_probe_error=validate(b.reference.complex_array(value['probes'])))
        save();print(f'RESULT Rust {elapsed:.3f} seconds',flush=True)
    finally:
        session.close()
    print('RUN Aer: same gates, FP64, four threads, fusion on, verbose off',flush=True)
    data,elapsed,metadata=b.aer_run(backend,circuit,threads,n)
    result['measurements']['aer']=dict(seconds=elapsed,max_probe_error=validate(data['amplitudes']),metadata=metadata)
    result.update(complete=True,rust_over_aer_ratio=result['measurements']['rust']['seconds']/elapsed)
    save();print(f'RESULT Aer {elapsed:.3f} seconds',flush=True)


if __name__=='__main__':main()
