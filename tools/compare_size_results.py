#!/usr/bin/env python3
"""Compare a recorded Rust size sweep with equivalent Qiskit Statevector runs."""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path

import benchmark_sizes as sizes
import compare_qiskit as reference


def comparison_rows(rust_rows, qiskit_by_size):
    rows = []
    for original in rust_rows:
        row = {k: original[k] for k in ['num_qubits', 'precision', 'threads', 'status', 'state_bytes']}
        row['rust_status'] = row.pop('status')
        row['rust_timing'] = original.get('timing')
        row['qiskit_reference'] = qiskit_by_size.get(str(row['num_qubits']),
                                                   {'status': 'skipped', 'reason': 'not measured'})
        if row['rust_status'] == 'passed' and row['qiskit_reference']['status'] == 'passed':
            row['status'] = 'compared'
            row['qiskit_over_rust_ratio'] = row['qiskit_reference']['timing']['median_seconds'] / row['rust_timing']['median_seconds']
        else:
            row['status'] = 'not_compared'
        rows.append(row)
    return rows


def save(path, metadata, rust_rows, references, complete=False):
    rows = comparison_rows(rust_rows, references)
    data = {'complete': complete, 'metadata': metadata, 'qiskit_by_size': references, 'results': rows}
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=reference.ROOT / 'benchmark-results/2026-11-08-sizes/results.json')
    parser.add_argument('--max-qubits', type=int, default=30)
    parser.add_argument('--output', type=Path, default=reference.ROOT / 'benchmark-results/2026-11-08-sizes/qiskit-comparison.json')
    args = parser.parse_args()
    recorded = json.loads(args.input.read_text())
    if not recorded['complete'] or not 4 <= args.max_qubits <= 30:
        parser.error('input must be complete and max qubits within 4..30')
    config = recorded['metadata']['configuration']
    if args.max_qubits > config['max_qubits']:
        parser.error('maximum cannot exceed the recorded Rust range')
    current_hash = hashlib.sha256(b''.join((reference.ROOT / p).read_bytes() for p in
        ['src/lib.rs', 'src/precision.rs', 'src/execution.rs'])).hexdigest()
    if current_hash != recorded['metadata']['source_sha256']:
        parser.error('Rust source changed since input measurements; rerun the size sweep first')
    reference.load_qiskit()
    revision = reference.command('git', 'rev-parse', 'HEAD', cwd=reference.SUBMODULE)
    if revision != recorded['metadata'].get('qiskit_commit'):
        parser.error('Qiskit revision differs from the recorded run')
    bench_args = argparse.Namespace(**config)
    bench_args.qiskit_max_qubits = args.max_qubits
    metadata = {'rust_input': str(args.input), 'rust_configuration': config,
                'rust_source_sha256': current_hash, 'qiskit_commit': revision,
                'qiskit_version': reference.qiskit.__version__, 'qiskit_precision': 'f64',
                'qiskit_numerical_thread_limit': 1,
                'measurement_policy': 'reuse recorded references; measure missing sizes with the same circuit/repetitions/warmup and memory policy',
                'comparison_limitations': 'recorded Rust times vs Qiskit times, measured at different moments; Rust uses configured workers; lower precision is not equal accuracy',
                'memory_estimate_multiplier': 8, 'requested_max_qubits': args.max_qubits}
    references = {}
    for row in recorded['results']:
        if row['qiskit_reference']['status'] == 'passed':
            references[str(row['num_qubits'])] = {**copy.deepcopy(row['qiskit_reference']), 'timing_origin': 'recorded size sweep'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save(args.output, metadata, recorded['results'], references)
    for n in range(config['min_qubits'], args.max_qubits + 1):
        if str(n) in references:
            print(f'REUSE Qiskit n={n}', flush=True)
            continue
        active, body = sizes.body_gates(n, config['depth'])
        local = sizes.local_reference(active, body)
        gates = [{'gate': 'h', 'target': q} for q in range(n)] + body
        print(f'CHECK Qiskit n={n}', flush=True)
        result = sizes.qiskit_benchmark(n, gates, active, local, bench_args)
        result['timing_origin'] = 'comparison extension' if result['status'] == 'passed' else 'not measured'
        references[str(n)] = result
        save(args.output, metadata, recorded['results'], references)
        detail = f"median={result['timing']['median_seconds']:.6f}s" if result['status'] == 'passed' else result['reason']
        print(f"{result['status'].upper()} Qiskit n={n} {detail}", flush=True)
    save(args.output, metadata, recorded['results'], references, complete=True)
    rows = comparison_rows(recorded['results'], references)
    with args.output.with_suffix('.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['qubits', 'rust_precision', 'rust_threads', 'status', 'rust_median_seconds', 'qiskit_fp64_median_seconds', 'ratio'])
        for row in rows:
            writer.writerow([row['num_qubits'], row['precision'], row['threads'], row['status'],
                (row['rust_timing'] or {}).get('median_seconds', ''),
                row['qiskit_reference'].get('timing', {}).get('median_seconds', ''), row.get('qiskit_over_rust_ratio', '')])
    print(f"Saved {sum(r['status'] == 'compared' for r in rows)} measured comparisons to {args.output}")


if __name__ == '__main__':
    main()
