#!/usr/bin/env python3
"""Compare dense FP32 random-matching circuits on Rust CPU and Metal GPU."""
import argparse
from contextlib import ExitStack
import hashlib
import math
import os
from pathlib import Path
import random
from statistics import median
import subprocess

from benchmark_aer import entangled_gates
from benchmark_sizes import memory_snapshot, skip_reason
from compare_qiskit import binary
from compare_runtimes import RustSession, accuracy, atomic_json, engine_order, summary


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qubits', type=int, default=27)
    parser.add_argument('--layers', type=int, default=8)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--warmup', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 6 <= args.qubits <= 27 or min(args.layers, args.threads, args.repetitions) < 1 or args.warmup < 0:
        parser.error('use 6–27 qubits, positive layers, threads and repetitions, and nonnegative warmups')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('use a new or empty output directory')
    working = 2 * (8 << args.qubits)
    memory = memory_snapshot()
    if reason := skip_reason(working, 2 << 30, memory, 1 << 30, .25):
        parser.error(reason)
    for name in ('OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[name] = '1'
    import numpy as np

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact = ROOT / 'target/metal/statevector'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'tools/metal_statevector.mm'
    shader = ROOT / 'tools/metal_statevector.metal'
    subprocess.run(['xcrun', 'clang++', '-O3', '-std=c++17', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                    '-framework', 'Foundation', '-framework', 'Metal', str(source), '-o', str(artifact)], check=True)
    cpu = binary()
    n = args.qubits
    gates = entangled_gates(n, args.layers, 42, 'random_matching')
    size = 1 << n
    probes = (list(range(size)) if n <= 14 else
              sorted({0, 1, size - 1, *random.Random(42).sample(range(size), 64)}))
    request = dict(num_qubits=n, precision='f32', gates=gates, threads=args.threads,
                   include_state=False, include_norm=False, optimize=False, fusion=True,
                   memory_limit_bytes=8 << n, probe_indices=probes)
    atomic_json(output / 'request.json', request)
    results = dict(complete=False, qubits=n, gates=len(gates), layers=args.layers,
                   threads=args.threads, warmup=args.warmup, repetitions=args.repetitions,
                   validation_scope='full' if len(probes) == size else 'probes',
                   memory_before=memory, samples={'rust': [], 'metal': []}, trials=[],
                   sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (source, shader)},
                   cpu_binary_sha256=hashlib.sha256(cpu.read_bytes()).hexdigest(),
                   metal_binary_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    with ExitStack() as stack:
        sessions = {}
        for name, command in [('rust', (cpu,)), ('metal', (artifact, shader))]:
            session = RustSession(*command)
            stack.callback(session.close)
            sessions[name] = session
            session.run(dict(request, num_qubits=0, gates=[], probe_indices=[0]))
        for trial in range(args.repetitions + args.warmup):
            row = dict(order=engine_order(sessions, trial, 42), measured=trial >= args.warmup, seconds={})
            values = {}
            for name in row['order']:
                print(f'RUN {name}, trial {trial + 1}, {len(gates)} gates', flush=True)
                value, elapsed = sessions[name].run(request)
                assert math.isfinite(elapsed) and elapsed > 0
                row['seconds'][name] = elapsed
                if row['measured']:
                    results['samples'][name].append(elapsed)
                values[name] = value
                print(f'{name} {elapsed:.3f} s', flush=True)
            arrays = {name: np.asarray(value['probes']) for name, value in values.items()}
            row['accuracy'] = accuracy(*(arrays[name][:, 0] + 1j * arrays[name][:, 1]
                                         for name in ('metal', 'rust')), 'f32')
            row['gpu_seconds'] = values['metal']['gpu_seconds']
            results['device'] = values['metal']['device']
            results['metal_operations'] = values['metal']['operations']
            results['metal_structured_blocks'] = values['metal']['structured_blocks']
            results['rust_compilation'] = values['rust']['compilation']['stats']
            results['trials'].append(row)
            atomic_json(output / 'results.json', results)
            if not row['accuracy']['passed']:
                raise RuntimeError(f'Metal and Rust amplitudes differ: {row["accuracy"]}')
    results['timings'] = {name: summary(values) for name, values in results['samples'].items()}
    results['speedup'] = median(results['samples']['rust']) / median(results['samples']['metal'])
    results.update(complete=True, memory_after=memory_snapshot())
    atomic_json(output / 'results.json', results)
    print(f'Rust/Metal median ratio {results["speedup"]:.3f}×. Saved {output / "results.json"}')


if __name__ == '__main__':
    main()
