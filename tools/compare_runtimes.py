#!/usr/bin/env python3
"""Compare Rust, Python Statevector and Aer with one fresh-state timing policy.

Default: python tools/compare_runtimes.py
FP32/FP64 Rust and Aer suite: ./compare --suite --qubits 26
Plan without imports/build/allocation: python tools/compare_runtimes.py --dry-run
Redisplay saved results: python tools/compare_runtimes.py --report PATH/results.json
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import signal
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MIB = 1 << 20
LIMITS = {'f64': (1e-10, 1e-10, 1e-10), 'f32': (3e-5, 3e-5, 1e-5),
          'f16': (.03, .04, .03)}  # relative L2, scaled peak error, norm drift


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def summary(samples):
    return dict(median_seconds=statistics.median(samples), min_seconds=min(samples),
                max_seconds=max(samples), samples_seconds=samples)


def engine_order(names, trial, seed):
    """Shuffle once per case, then rotate for balanced first/last placement."""
    order = list(names)
    random.Random(seed).shuffle(order)
    offset = trial % len(order)
    return order[offset:] + order[:offset]


def gate_list(case, config):
    # These modules are stdlib-only until their explicit numerical-library loader.
    import benchmark_aer as families
    import compare_qiskit as ref
    n, family = case['num_qubits'], case['family']
    target = {'low': 0, 'middle': n // 2, 'high': n - 1}[config['target']]
    if family in ('scrambled_bell', 'random_matching'):
        return families.entangled_gates(n, config['layers'], config['seed'], family)
    if family == 'mixed':
        return ref.workload(n, target, config['depth'], True)
    if family == 'diagonal':
        return ([dict(gate='h', target=q) for q in range(n)] +
                [dict(gate='rz', target=target, theta=.123 + i * .017)
                 if i % 4 == 3 else dict(gate=['z', 's', 't'][i % 4], target=target)
                 for i in range(config['depth'])])
    preparation = families.entangled_gates(n, 0, config['seed'], 'scrambled_bell')
    return preparation + [dict(gate='cx', control=i % n, target=(i + n // 2) % n)
                          for i in range(config['depth'])]


def validation_probes(case, config, gates):
    """Select deterministic probes, including occupied indices for sparse CX states."""
    n = case['num_qubits']
    size = 1 << n
    rng = random.Random(config['seed'])
    if case['family'] != 'cx_stride':
        return sorted({0, 1, size - 1, *rng.sample(range(size), min(64, size))})

    half = n // 2
    choices = 1 << (n - half)
    low_mask = (1 << half) - 1
    # The first n gates prepare Bell pairs (and one free bit for odd n).
    # Each CX in the body is an invertible permutation of basis indices.
    cx_bits = [(g['control'], g['target']) for g in gates[n:]]

    def permuted(choice, mismatch):
        low = choice & low_mask
        high = (low ^ mismatch) | (choice & ~low_mask)
        index = low | (high << half)
        for control, target in cx_bits:
            index ^= ((index >> control) & 1) << target
        return index

    occupied = [0, *rng.sample(range(1, choices), min(32, choices - 1))]
    empty = rng.sample(range(size - choices), min(67 - len(occupied), size - choices))
    probes = [permuted(choice, 0) for choice in occupied]
    # A nonzero mismatch between paired bits places the index outside the support.
    probes.extend(permuted(i % choices, i // choices + 1) for i in empty)
    return sorted(probes)


def admission(case, config):
    """Budget the largest state and conservative reference/scratch working set."""
    import benchmark_sizes as sizes
    n = case['num_qubits']
    # Python and the correctness reference use FP64, even for low-precision Rust.
    payload = 16 * (1 << n)
    memory = sizes.memory_snapshot()
    # Probe-only Aer cases run one backend at a time and retain only probes
    # from the FP64 reference. Full-state and Python cases need extra copies.
    probe_only_aer = config['engines'] == ['aer'] and n > config['full_check_max_qubits']
    states = 4 if probe_only_aer else 8
    working = states * payload
    reason = sizes.skip_reason(payload, config['max_state_mib'] * MIB, memory,
                               config['reserve_mib'] * MIB, .25)
    if not reason:
        reason = sizes.skip_reason(working, states * config['max_state_mib'] * MIB,
                                   memory, config['reserve_mib'] * MIB, .25)
    return reason, dict(state_bytes=payload, estimated_working_bytes=working,
                        snapshot=memory)


def ratios(row):
    """Only calculate speed ratios after correctness passes and precision matches."""
    if row.get('status') != 'passed':
        return {}
    rust = row['timings']['rust']['median_seconds']
    if rust <= 0:
        return {}
    values = {}
    if row['precision'] == 'f64' and 'python' in row['timings']:
        values['python_over_rust'] = row['timings']['python']['median_seconds'] / rust
    if row['precision'] != 'f16' and 'aer' in row['timings']:
        values['aer_over_rust'] = row['timings']['aer']['median_seconds'] / rust
    return values


def table(rows):
    headings = ['Qubits', 'Circuit', 'Mode', 'Workers', 'Fusion', 'CX opt', 'Rust ms',
                'Python ms*', 'Aer ms', 'Py/Rust', 'Aer/Rust', 'Check']
    values = []
    for row in rows:
        times = row.get('timings', {})
        def ms(engine):
            return f"{times[engine]['median_seconds'] * 1000:.3f}" if engine in times else '-'
        speed = ratios(row)
        values.append([str(row['num_qubits']), row['family'], row['precision'],
                       str(row['threads']), 'on' if row['fusion'] else 'off',
                       'on' if row['optimize_cx'] else 'off', ms('rust'), ms('python'), ms('aer'),
                       f"{speed['python_over_rust']:.2f}x" if 'python_over_rust' in speed else '-',
                       f"{speed['aer_over_rust']:.2f}x" if 'aer_over_rust' in speed else '-',
                       row['status'] + ('/' + row['validation_scope'] if row.get('status') == 'passed' else '')])
    widths = [max(len(h), *(len(v[i]) for v in values)) if values else len(h)
              for i, h in enumerate(headings)]
    def line(cells):
        return ' | '.join(v.ljust(w) for v, w in zip(cells, widths))
    return '\n'.join([line(headings), '-+-'.join('-' * w for w in widths)] + [line(v) for v in values])


def write_outputs(directory, report):
    atomic_json(directory / 'results.json', report)
    rows = report['results']
    with (directory / 'results.csv').open('w', newline='') as stream:
        columns = ['num_qubits', 'family', 'precision', 'threads', 'fusion', 'optimize_cx',
                   'status', 'validation_scope', 'reason']
        columns += [f'{e}_{v}' for e in ('rust', 'python', 'aer')
                    for v in ('median_seconds', 'min_seconds', 'max_seconds')]
        columns += ['python_over_rust', 'aer_over_rust', 'rust_gate_median_seconds',
                    'rust_compile_median_seconds']
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            flat = {k: row.get(k, '') for k in columns}
            for e, t in row.get('timings', {}).items():
                for key in ('median_seconds', 'min_seconds', 'max_seconds'):
                    flat[f'{e}_{key}'] = t[key]
            flat.update(ratios(row))
            flat['rust_gate_median_seconds'] = row.get('rust_gate', {}).get('median_seconds', '')
            flat['rust_compile_median_seconds'] = row.get('rust_compile', {}).get('median_seconds', '')
            writer.writerow(flat)
    text = ['# Runtime comparison', '',
            'Primary times are median fresh-state request latencies in milliseconds. '
            'Ratios above one favor Rust; below one favor the other backend. '
            'Only passing cases with equal amplitude precision receive ratios.', '',
            '```text', table(rows), '```', '',
            '*Python is Qiskit quantum_info.Statevector, always FP64 with numerical '
            'library threads limited to one. Its ratios at multiple Rust workers use '
            'different requested CPU resources. Aer uses the row precision and worker count; '
            'it is the optimized simulator comparison. Aer has no FP16 mode here.', '',
            'Request timing includes fresh allocation, initialization, execution and the same '
            'complex probes. Rust parsing, worker-pool creation and compilation are included; '
            'Python Statevector allocation/evolution/probe extraction and Aer job handling/fusion '
            'are included. Imports, circuit construction, process startup and reference checks '
            'are excluded. JSON request encoding precedes the Rust timer. Rust gate and '
            'compilation samples are separately recorded and are not substituted into ratios.', '',
            'Engine order is shuffled deterministically then rotated per trial. Warmups are '
            'discarded. Full-state validation uses binary export outside timing through the '
            'configured cap; larger cases use complex probes only. Validation failure suppresses '
            'speed ratios. No automatic renormalization or global-phase alignment is applied.', '',
            'Fusion defaults on, including structured Ry/Rz and specialized controlled/diagonal '
            'Rust kernels. Aer fusion matches the switch and retains its normal maximum block '
            'size. CX compression defaults off; enabling it changes executed gate count and '
            'is separately labelled. Clifford workloads still force Aer statevector execution.', '',
            'CPU affinity and temperature are uncontrolled. Raw samples, execution order, '
            'source/binary/circuit hashes, memory snapshots, backend metadata, accuracy metrics '
            'and configuration are in results.json. Every original circuit/request is saved '
            'under cases/. Explicit skips and errors have no invented timing values.', '',
            'Run complete: ' + str(report['complete']), '']
    for row in rows:
        if row.get('reason'):
            text.append(f"- {row['id']}: {row['status']}: {row['reason']}")
    (directory / 'REPORT.md').write_text('\n'.join(text) + '\n')


class RustSession:
    """One native process; each line creates a new state and execution context."""
    def __init__(self, binary):
        self.proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)

    def run(self, request):
        encoded = json.dumps(request) + '\n'
        start = time.perf_counter()
        self.proc.stdin.write(encoded)
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('Rust worker stopped: ' + self.proc.stderr.read())
        value = json.loads(line)
        elapsed = time.perf_counter() - start
        if 'error' in value:
            raise RuntimeError(value['error'])
        return value, elapsed

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
        self.proc.stderr.close()
        if self.proc.returncode:
            raise RuntimeError(f'Rust exited {self.proc.returncode}')


def accuracy(actual, expected, precision, norm=None):
    import numpy as np
    actual, expected = np.asarray(actual), np.asarray(expected)
    if actual.shape != expected.shape or not actual.size:
        raise ValueError('wrong number of validation amplitudes')
    delta = actual - expected
    expected_norm = float(np.vdot(expected, expected).real)
    actual_norm = float(np.vdot(actual, actual).real)
    relative = float(np.linalg.norm(delta)) / math.sqrt(expected_norm) if expected_norm else float(np.linalg.norm(delta))
    error = float(np.max(np.abs(delta)))
    peak = float(np.max(np.abs(expected)))
    scaled = error / peak if peak else error
    norm_error = abs(norm - 1) if norm is not None else None
    limits = LIMITS[precision]
    passed = (all(math.isfinite(v) for v in (relative, scaled)) and
              relative <= limits[0] and scaled <= limits[1] and
              (norm_error is None or math.isfinite(norm_error) and norm_error <= limits[2]))
    fidelity = (float(abs(np.vdot(expected, actual))**2 / (expected_norm * actual_norm))
                if expected_norm and actual_norm else None)
    # Failed nonfinite metrics must still be checkpointable as strict JSON.
    clean = lambda x: x if x is None or math.isfinite(x) else None
    return dict(passed=bool(passed), max_absolute_error=clean(error), relative_l2_error=clean(relative),
                scaled_peak_error=clean(scaled), norm_drift=clean(norm_error), normalized_fidelity=clean(fidelity))


def worker(job_path, output_path):
    """One isolated Python process per case, configured before any native imports."""
    job = json.loads(job_path.read_text())
    case, config, request = job['case'], job['config'], job['request']
    import compare_qiskit as ref  # stdlib only; establishes BLAS defaults
    os.environ['OMP_NUM_THREADS'] = str(case['threads'])
    os.environ['OMP_THREAD_LIMIT'] = str(case['threads'])
    for name in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS'):
        os.environ[name] = '1'
    import numpy as np
    import qiskit
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector
    ref.np, ref.QuantumCircuit = np, QuantumCircuit
    selected = config['engines']
    aer_module, aer_class = None, None
    if 'aer' in selected:
        import qiskit_aer as aer_module
        from qiskit_aer import AerSimulator as aer_class
    n, precision = case['num_qubits'], case['precision']
    circuit = ref.circuit(n, request['gates'])
    instruction = circuit.to_instruction()
    probes = request['probe_indices']
    full_check = n <= config['full_check_max_qubits']

    def backend(mode):
        return aer_class(method='statevector', device='CPU',
                         precision='double' if mode == 'f64' else 'single',
                         max_parallel_threads=case['threads'], max_parallel_experiments=1,
                         max_parallel_shots=1, fusion_enable=case['fusion'],
                         enable_truncation=False, statevector_parallel_threshold=0,
                         zero_threshold=0., max_memory_mb=config['max_state_mib'])

    def aer_call(engine, qc):
        start = time.perf_counter()
        value = engine.run(qc, shots=1, seed_simulator=config['seed']).result()
        data = value.data(0)
        elapsed = time.perf_counter() - start
        if not value.success:
            raise RuntimeError(str(value))
        metadata = value.results[0].metadata
        if (metadata['method'] != 'statevector' or metadata['device'] != 'CPU' or
                metadata['num_qubits'] != n or metadata['parallel_state_update'] != case['threads']):
            raise RuntimeError('Aer backend settings did not match the requested case')
        return data, elapsed, metadata

    # Gold state/probes are computed once, outside every timing sample, in FP64.
    if 'python' in selected:
        expected_state = Statevector.from_instruction(circuit).data
        expected_probes = expected_state[probes].copy()
    else:
        gold_circuit = circuit.copy()
        if full_check:
            gold_circuit.save_statevector()
        else:
            gold_circuit.save_amplitudes(probes)
        gold, _, _ = aer_call(backend('f64'), gold_circuit)
        expected_state = np.asarray(gold['statevector']).copy() if full_check else None
        expected_probes = expected_state[probes].copy() if full_check else np.asarray(gold['amplitudes']).copy()
        del gold, gold_circuit
    if not full_check:
        expected_state = None
    rust = RustSession(job['binary'])
    row = dict(case, status='running', validation_scope='full' if full_check else 'probes',
               versions=dict(python=sys.version, qiskit=qiskit.__version__, numpy=np.__version__,
                             aer=aer_module.__version__ if aer_module else None),
               thread_environment={name: os.environ.get(name) for name in ('OMP_NUM_THREADS', 'OMP_THREAD_LIMIT', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')},
               validation={}, unsupported={}, trial_order=[], raw_samples={}, timings={})
    engines = ['rust'] + [e for e in selected if not (e == 'aer' and precision == 'f16')]
    if 'aer' in selected and precision == 'f16':
        row['unsupported']['aer'] = 'Aer does not support FP16 statevector storage'
    matched_aer = backend(precision) if 'aer' in engines else None
    probe_circuit = circuit.copy()
    if matched_aer:
        probe_circuit.save_amplitudes(probes)
    samples = {e: [] for e in engines}
    gate_samples, compile_samples = [], []
    try:
        # Binary export avoids full-state JSON expansion and is never timed.
        with tempfile.TemporaryDirectory(prefix='runtime-validation-') as tmp:
            path = Path(tmp) / 'state.bin'
            validation_request = dict(request, include_norm=True)
            if full_check:
                validation_request['amplitudes_output'] = str(path)
            value, _ = rust.run(validation_request)
            actual = np.fromfile(path, dtype='<c16') if full_check else ref.complex_array(value['probes'])
            row['validation']['rust'] = accuracy(actual, expected_state if full_check else expected_probes,
                                                   precision, value['norm_sqr'])
            del actual
        if matched_aer:
            check = circuit.copy()
            if full_check:
                check.save_statevector()
            else:
                check.save_amplitudes(probes)
            data, _, metadata = aer_call(matched_aer, check)
            actual = np.asarray(data['statevector']) if full_check else np.asarray(data['amplitudes'])
            norm = float(np.vdot(actual, actual).real) if full_check else None
            row['validation']['aer'] = accuracy(actual, expected_state if full_check else expected_probes, precision, norm)
            row['aer_metadata'] = metadata
            del actual, data, check
        if 'python' in engines:
            row['validation']['python'] = dict(passed=True, reference='Qiskit FP64 Statevector')
        expected_state = None
        if not all(v['passed'] for v in row['validation'].values()):
            row.update(status='accuracy_failed', reason='preflight accuracy budget exceeded')
            atomic_json(output_path, row)
            return
        failed = False
        for trial in range(config['warmup'] + config['repetitions']):
            order = engine_order(engines, trial, config['seed'])
            measured = trial >= config['warmup']
            row['trial_order'].append(dict(trial=trial, measured=measured, engines=order))
            for engine in order:
                if engine == 'rust':
                    value, elapsed = rust.run(request)
                    actual = ref.complex_array(value['probes'])
                    if measured:
                        gate_samples.append(value['seconds'][0])
                        compile_samples.append(value.get('compilation', {}).get('seconds', 0.))
                    row['compilation'] = value.get('compilation')
                    row['rust_parallel_threshold'] = value['parallel_threshold']
                elif engine == 'python':
                    start = time.perf_counter()
                    state = Statevector.from_int(0, 1 << n)
                    evolved = state.evolve(instruction)
                    actual = evolved.data[probes].copy()
                    elapsed = time.perf_counter() - start
                    del state, evolved
                else:
                    value, elapsed, metadata = aer_call(matched_aer, probe_circuit)
                    actual = np.asarray(value['amplitudes'])
                    row['aer_metadata'] = metadata
                comparison_precision = 'f64' if engine == 'python' else precision
                check = accuracy(actual, expected_probes, comparison_precision)
                row.setdefault('probe_checks', []).append(dict(trial=trial, engine=engine, **check))
                if not check['passed']:
                    failed = True
                if measured:
                    samples[engine].append(elapsed)
            row['raw_samples'] = samples
            atomic_json(output_path, row)
        row['timings'] = {e: summary(s) for e, s in samples.items()}
        row['rust_gate'], row['rust_compile'] = summary(gate_samples), summary(compile_samples)
        row['status'] = 'accuracy_failed' if failed else 'passed'
        if failed:
            row['reason'] = 'timed probe accuracy budget exceeded'
        row['ratios'] = ratios(row)
        atomic_json(output_path, row)
    finally:
        rust.close()


def run_worker(command, log, timeout):
    """On timeout/interruption kill this worker's process group, including Rust."""
    proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=os.name == 'posix')
    try:
        return proc.wait(timeout=timeout)
    except BaseException:
        if os.name == 'posix':
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
        proc.wait()
        raise


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--suite', action='store_true',
                   help='Compare Rust and Qiskit Aer in FP32 and FP64 across five circuit families')
    p.add_argument('--qubits', type=int, nargs='+', default=[16, 20])
    p.add_argument('--threads', type=int, nargs='+')
    p.add_argument('--precisions', choices=list(LIMITS), nargs='+')
    p.add_argument('--families', choices=['mixed', 'scrambled_bell', 'random_matching', 'cx_stride', 'diagonal'],
                   nargs='+')
    p.add_argument('--engines', choices=['python', 'aer'], nargs='+')
    p.add_argument('--fusion', choices=['on', 'off', 'both'], default='on')
    p.add_argument('--optimize-cx', action='store_true')
    p.add_argument('--target', choices=['low', 'middle', 'high'], default='high')
    p.add_argument('--layers', type=int, default=8)
    p.add_argument('--depth', type=int, default=64)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--repetitions', type=int, default=5)
    p.add_argument('--warmup', type=int, default=1)
    p.add_argument('--full-check-max-qubits', type=int, default=20)
    p.add_argument('--max-state-mib', type=int,
                   help='State size cap in MiB (default 256, or at least the requested FP64 state size with --suite)')
    p.add_argument('--reserve-mib', type=int, default=1024)
    p.add_argument('--timeout', type=float, default=600, help='Maximum seconds per isolated case')
    p.add_argument('--output', type=Path, help='New or empty results directory')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--report', type=Path, help='Redisplay saved results.json without running simulations')
    p.add_argument('--worker-job', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.worker_job:
        worker(args.worker_job, args.worker_output)
        return 0
    if args.report:
        saved = json.loads(args.report.read_text())
        print(table(saved['results']))
        print('Primary times: fresh-state requests. Python always FP64/one numerical thread. Ratios >1 favor Rust.')
        return 0
    if any(not 6 <= n <= 30 for n in args.qubits):
        p.error('qubits must be between 6 and 30')
    default_families = ['mixed', 'scrambled_bell']
    if args.suite:
        default_families += ['random_matching', 'cx_stride', 'diagonal']
    args.families = args.families or default_families
    args.threads = args.threads or ([4] if args.suite else [1, 4])
    args.precisions = args.precisions or (['f32', 'f64'] if args.suite else ['f64'])
    args.engines = args.engines or (['aer'] if args.suite else ['python', 'aer'])
    if args.max_state_mib is None:
        args.max_state_mib = max(256, (16 << max(args.qubits)) // MIB) if args.suite else 256
    if (any(t < 1 for t in args.threads) or
            not 1 <= args.repetitions <= 100 or not 0 <= args.warmup <= 100 or
            args.layers < 1 or args.depth < 1 or args.max_state_mib < 1 or args.reserve_mib < 0 or
            not 0 <= args.full_check_max_qubits <= 30 or args.timeout <= 0):
        p.error('qubits 6..30, positive threads/depth/layers/budgets/timeout, repetitions 1..100, warmup 0..100')
    config = {k: v for k, v in vars(args).items() if k not in ('worker_job', 'worker_output', 'output', 'report')}
    config['engines'] = list(dict.fromkeys(config['engines']))
    fusion = [True, False] if args.fusion == 'both' else [args.fusion == 'on']
    cases = []
    for n, family, threads, precision, fused in itertools.product(
            dict.fromkeys(args.qubits), dict.fromkeys(args.families), dict.fromkeys(args.threads),
            dict.fromkeys(args.precisions), fusion):
        case = dict(num_qubits=n, family=family, threads=threads, precision=precision,
                    fusion=fused, optimize_cx=args.optimize_cx)
        case['id'] = f'{n}q-{family}-{precision}-{threads}t-fusion-{int(fused)}'
        reason, memory = admission(case, config)
        cases.append(dict(case, status='skipped' if reason else 'planned', reason=reason,
                          memory_admission=memory))
    if args.dry_run:
        print(table(cases))
        for case in cases:
            if case['reason']:
                print(case['id'] + ': ' + case['reason'])
        return 0
    output = (args.output or ROOT / 'benchmark-results' / ('compare-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))).resolve()
    if output.exists() and any(output.iterdir()):
        p.error('output directory must be empty; use --report to read existing results')
    output.mkdir(parents=True, exist_ok=True)
    case_dir = output / 'cases'
    case_dir.mkdir()
    result = dict(schema_version=1, complete=False, configuration=config, results=[],
                  metadata=dict(started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                platform=platform.platform(), machine=platform.machine(), logical_cpus=os.cpu_count(),
                                harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    write_outputs(output, result)
    # Build once, outside all worker timers. Cargo metadata respects target-dir overrides.
    import compare_qiskit as ref
    binary = None
    try:
        if any(c['status'] == 'planned' for c in cases):
            binary = ref.binary()
            result['metadata'].update(binary=str(binary), binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                source_sha256=hashlib.sha256(b''.join(f.read_bytes() for f in sorted((ROOT / 'src').rglob('*.rs')))).hexdigest(),
                rustc=ref.command('rustc', '--version'), cargo_lock_sha256=hashlib.sha256((ROOT/'Cargo.lock').read_bytes()).hexdigest())
        for case in cases:
            reason, memory = admission(case, config)  # recheck live headroom immediately before each worker
            case['memory_admission'] = memory
            if reason:
                case.update(status='skipped', reason=reason)
            elif case['status'] != 'skipped':
                gates = gate_list(case, config)
                n = case['num_qubits']
                probes = validation_probes(case, config, gates)
                request = dict(num_qubits=n, gates=gates, threads=case['threads'], precision=case['precision'],
                               fusion=case['fusion'], optimize=case['optimize_cx'], memory_limit_bytes=args.max_state_mib * MIB,
                               include_state=False, include_norm=False, probe_indices=probes)
                request_path = case_dir / (case['id'] + '.request.json')
                atomic_json(request_path, request)
                job_path = case_dir / (case['id'] + '.job.json')
                worker_path = case_dir / (case['id'] + '.result.json')
                case.update(request_sha256=hashlib.sha256(request_path.read_bytes()).hexdigest(),
                            gate_count=len(gates), circuit_sha256=hashlib.sha256(json.dumps(gates, sort_keys=True).encode()).hexdigest(),
                            request_path=str(request_path.relative_to(output)), status='running')
                result['results'].append(case)
                write_outputs(output, result)
                atomic_json(job_path, dict(case=case, config=config, request=request, binary=str(binary)))
                print('RUN ' + case['id'], flush=True)
                log_path = case_dir / (case['id'] + '.log')
                with log_path.open('w') as log:
                    command = [sys.executable, str(Path(__file__).resolve()), '--worker-job', str(job_path),
                               '--worker-output', str(worker_path)]
                    try:
                        code = run_worker(command, log, args.timeout)
                        if worker_path.exists():
                            case.update(json.loads(worker_path.read_text()))
                        if code or case['status'] == 'running':
                            case.update(status='error', reason=f'worker exited {code}; see {log_path.name}')
                    except subprocess.TimeoutExpired:
                        case.update(status='error', reason=f'case exceeded {args.timeout:g} seconds')
                print(table([case]), flush=True)
                write_outputs(output, result)
                continue
            result['results'].append(case)
            write_outputs(output, result)
        result['complete'] = True
    except BaseException as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        write_outputs(output, result)
        raise
    write_outputs(output, result)
    print('\n' + table(result['results']))
    print('\n*Python Statevector: FP64, one numerical thread; Aer matches precision/workers. Ratios >1 favor Rust.')
    print('Saved ' + str(output / 'REPORT.md'))
    return int(any(c['status'] in ('error', 'accuracy_failed') for c in result['results']))


if __name__ == '__main__':
    raise SystemExit(main())
