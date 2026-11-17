#!/usr/bin/env python3
"""Memory-aware Rust performance sweep, including every size from 4 to 30 qubits."""
from __future__ import annotations

import argparse
import cmath
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import statistics
import subprocess
import sys
import time

import compare_qiskit as reference

MIB = 1 << 20
GIB = 1 << 30
ACCURACY = {
    "f64": {"scaled_amplitude": 1e-11, "norm": 1e-11},
    "f32": {"scaled_amplitude": 3e-5, "norm": 1e-5},
    "f16": {"scaled_amplitude": 2e-2, "norm": 2e-2},
}


def output(*args):
    return subprocess.check_output(args, text=True, timeout=5)


def memory_snapshot():
    """Conservative reclaimable-memory estimate, not a reservation guarantee."""
    try:
        if sys.platform == "darwin":
            pressure = output("memory_pressure", "-Q")
            vm = output("vm_stat")
            physical = int(re.search(r"system has (\d+)", pressure).group(1))
            page = int(re.search(r"page size of (\d+) bytes", vm).group(1))
            pages = sum(int(v) for v in re.findall(
                r"^Pages (?:free|inactive|speculative|purgeable):\s+(\d+)\.", vm, re.M))
            swap = {k: int(v) for k, v in re.findall(r"^(Swapins|Swapouts):\s+(\d+)\.", vm, re.M)}
            return {"physical_bytes": physical, "available_bytes": pages * page,
                    "method": "macOS free+inactive+speculative+purgeable pages (estimate)", **swap}
        if sys.platform.startswith("linux"):
            info = dict(re.findall(r"^(MemTotal|MemAvailable):\s+(\d+) kB", Path('/proc/meminfo').read_text(), re.M))
            return {"physical_bytes": int(info['MemTotal']) * 1024,
                    "available_bytes": int(info['MemAvailable']) * 1024, "method": "Linux MemAvailable"}
    except (OSError, subprocess.SubprocessError, AttributeError, KeyError, ValueError):
        pass
    return {"physical_bytes": None, "available_bytes": None, "method": "unavailable"}


def skip_reason(state_bytes, limit_bytes, memory, reserve_bytes, fraction):
    if state_bytes > limit_bytes:
        return f"state needs {state_bytes} bytes; configured limit is {limit_bytes}"
    physical, available = memory['physical_bytes'], memory['available_bytes']
    if physical is not None and state_bytes > int(physical * fraction):
        return f"state exceeds {fraction:g} of physical RAM"
    if available is not None and state_bytes + reserve_bytes > available:
        return f"state plus {reserve_bytes} bytes headroom exceeds current available-memory estimate"
    if (physical is None or available is None) and state_bytes > 256 * MIB:
        return "memory information unavailable; large allocations are disabled"
    return None


def body_gates(n, depth):
    active = [0, n // 2, n - 1]
    gates = []
    for i in range(depth):
        target = active[i % 3]
        if i % 4 == 3:
            gates.append({"gate": "cx" if i % 8 == 3 else "cz", "target": target,
                          "control": active[(i + 1) % 3]})
        else:
            gates.append({"gate": ["ry", "rz", "rx"][i % 3], "target": target,
                          "theta": 0.123 + i * 0.017})
    return active, gates


def local_reference(active, gates):
    """Independent eight-amplitude FP64 oracle; spectators remain in |+>."""
    state = [complex(1 / math.sqrt(8))] * 8
    for g in gates:
        target = active.index(g['target'])
        mask = 1 << target
        if g['gate'] in ('cx', 'cz'):
            control = 1 << active.index(g['control'])
            if g['gate'] == 'cx':
                for i in range(8):
                    if i & control and not i & mask:
                        state[i], state[i | mask] = state[i | mask], state[i]
            else:
                for i in range(8):
                    if i & control and i & mask:
                        state[i] = -state[i]
            continue
        theta = g['theta']
        co, si = math.cos(theta / 2), math.sin(theta / 2)
        if g['gate'] == 'rx':
            u = ((co, -1j * si), (-1j * si, co))
        elif g['gate'] == 'ry':
            u = ((co, -si), (si, co))
        else:
            u = ((cmath.exp(-1j * theta / 2), 0), (0, cmath.exp(1j * theta / 2)))
        # Different indexing from Rust's blocked/chunked pair traversal.
        result = [0j] * 8
        for row in range(8):
            low = row & ~mask
            result[row] = u[(row >> target) & 1][0] * state[low] + u[(row >> target) & 1][1] * state[low | mask]
        state = result
    return state


def scatter(local, active):
    return sum(((local >> bit) & 1) << qubit for bit, qubit in enumerate(active))


def validation_probes(n, active, local):
    scale = 2 ** (-(n - 3) / 2)
    spectator_mask = ((1 << n) - 1) ^ sum(1 << bit for bit in active)
    indices = [scatter(i, active) for i in range(8)]
    indices += [i | spectator_mask for i in indices]
    return indices, [v * scale for v in local] * 2


def accuracy_metrics(actual, expected, norm, precision):
    error = max(abs(a - b) for a, b in zip(actual, expected))
    scaled = error / max(abs(v) for v in expected)
    drift = abs(norm - 1)
    tol = ACCURACY[precision]
    finite = math.isfinite(scaled) and math.isfinite(drift)
    a, b = actual[:8], expected[:8]
    an, bn = sum(abs(v)**2 for v in a), sum(abs(v)**2 for v in b)
    fidelity = abs(sum(v.conjugate() * w for v, w in zip(b, a)))**2 / (an * bn) if an and bn else None
    tvd = .5 * sum(abs(abs(v)**2 / an - abs(w)**2 / bn) for v, w in zip(a, b)) if an and bn else None
    return {"max_probe_error": error, "scaled_probe_error": scaled, "norm_drift": drift,
            "normalized_active_fidelity": fidelity, "normalized_active_tvd": tvd,
            "passed": finite and scaled <= tol['scaled_amplitude'] and drift <= tol['norm']}


def peak_rss_supported():
    if sys.platform != 'darwin':
        return False
    try:
        result = subprocess.run(['/usr/bin/time', '-l', '/usr/bin/true'], capture_output=True, text=True, timeout=5)
        return result.returncode == 0 and 'maximum resident set size' in result.stderr
    except (OSError, subprocess.SubprocessError):
        return False


def run_request(executable, request, timeout, measure_rss=False):
    command = [str(executable)]
    if measure_rss:
        command = ['/usr/bin/time', '-l', *command]
    start = time.perf_counter()
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=os.name == 'posix')
    try:
        stdout, stderr = proc.communicate(json.dumps(request) + '\n', timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.communicate()
        raise TimeoutError(f"case exceeded {timeout} seconds")
    if proc.returncode:
        raise RuntimeError(f"simulator exited {proc.returncode}: {stdout}\n{stderr}")
    result = json.loads(stdout)
    peak = re.search(r"(\d+)\s+maximum resident set size", stderr)
    return result, {"wall_seconds": time.perf_counter() - start,
                    "peak_rss_bytes": int(peak.group(1)) if peak else None,
                    "rss_source": "macOS time -l, whole child process" if peak else None}


def qiskit_benchmark(n, gates, active, local, args):
    if n > args.qiskit_max_qubits:
        return {"status": "skipped", "reason": f"full-state reference capped at {args.qiskit_max_qubits} qubits"}
    # Account for reference state, output, tensor temporaries, and verification.
    estimated = 8 * 16 * (1 << n)
    reason = skip_reason(estimated, args.max_state_mib * MIB, memory_snapshot(),
                         args.memory_reserve_mib * MIB, args.physical_memory_fraction)
    if reason:
        return {"status": "skipped", "reason": "Qiskit estimated working memory: " + reason}
    instruction = reference.circuit(n, gates).to_instruction()
    times = []
    final = None
    for trial in range(args.warmup + args.repetitions):
        final = None
        state = reference.Statevector.from_int(0, 1 << n)
        start = time.perf_counter()
        final = state.evolve(instruction)
        elapsed = time.perf_counter() - start
        if trial >= args.warmup:
            times.append(elapsed)
    indices = reference.np.arange(1 << n, dtype=reference.np.uint64)
    local_indices = sum(((indices >> q) & 1) << bit for bit, q in enumerate(active))
    expected = reference.np.asarray(local)[local_indices] * 2**(-(n - 3) / 2)
    reference.np.testing.assert_allclose(final.data, expected, rtol=1e-12, atol=1e-14)
    return {"status": "passed", "precision": "f64", "timing": reference.summary(times),
            "estimated_working_bytes": estimated, "verification": "full vector vs factored analytic oracle"}


def write_report(path, metadata, rows, complete=False):
    report = {"complete": complete, "metadata": metadata, "results": rows,
              "counts": {status: sum(r['status'] == status for r in rows)
                         for status in ['passed', 'accuracy_failed', 'error', 'skipped', 'planned']}}
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--min-qubits', type=int, default=4)
    parser.add_argument('--max-qubits', type=int, default=30)
    parser.add_argument('--precisions', choices=list(ACCURACY), nargs='+', default=['f64', 'f32', 'f16'])
    parser.add_argument('--threads', type=int, nargs='+', default=[4])
    parser.add_argument('--depth', type=int, default=12)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--warmup', type=int, default=0)
    parser.add_argument('--max-state-mib', type=int, default=256)
    parser.add_argument('--memory-reserve-mib', type=int, default=2048)
    parser.add_argument('--physical-memory-fraction', type=float, default=0.25)
    parser.add_argument('--case-timeout', type=int, default=900)
    parser.add_argument('--qiskit-check', action='store_true')
    parser.add_argument('--qiskit-max-qubits', type=int, default=0)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output', type=Path, default=reference.ROOT / 'benchmark-results/sizes-4-30.json')
    args = parser.parse_args()
    if not 4 <= args.min_qubits <= args.max_qubits <= 30:
        parser.error('qubit range must be within 4..30')
    if any(t < 1 for t in args.threads) or args.depth < 1 or not 1 <= args.repetitions <= 10000 or not 0 <= args.warmup <= 10000:
        parser.error('threads/depth must be positive, repetitions 1..10000, warmup 0..10000')
    if args.max_state_mib < 1 or args.memory_reserve_mib < 0 or not 0 < args.physical_memory_fraction <= .5 or args.case_timeout < 1:
        parser.error('invalid memory budget, headroom, physical fraction, or timeout')
    if not 0 <= args.qiskit_max_qubits <= 30:
        parser.error('Qiskit reference cap must be 0..30')
    if args.qiskit_check or args.qiskit_max_qubits:
        reference.load_qiskit()
    metadata = {"platform": platform.platform(), "logical_cpus": os.cpu_count(),
                "initial_memory": memory_snapshot(), "configuration": vars(args) | {"output": str(args.output)},
                "accuracy_budgets": ACCURACY,
                "oracle": "independent FP64 three-qubit evolution times uniform spectator factor",
                "qiskit_oracle_cross_check": args.qiskit_check,
                "source_sha256": hashlib.sha256(b''.join((reference.ROOT / p).read_bytes() for p in
                    ['src/lib.rs', 'src/precision.rs', 'src/execution.rs'])).hexdigest(),
                "cargo_lock_sha256": hashlib.sha256((reference.ROOT / 'Cargo.lock').read_bytes()).hexdigest(),
                "timing_scope": "gate sequence including H preparation; excludes state initialization, pool setup, IPC, norm/probes and export",
                "rustc": reference.command('rustc', '--version')}
    if args.qiskit_check or args.qiskit_max_qubits:
        metadata.update(qiskit_version=reference.qiskit.__version__,
                        qiskit_commit=reference.command('git', 'rev-parse', 'HEAD', cwd=reference.SUBMODULE),
                        qiskit_numerical_thread_limit=1)
    executable = None if args.dry_run else reference.binary()
    measure_rss = peak_rss_supported() if not args.dry_run else False
    metadata['peak_rss_available'] = measure_rss
    rows = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.output, metadata, rows)
    for n in range(args.min_qubits, args.max_qubits + 1):
        active, body = body_gates(n, args.depth)
        local = local_reference(active, body)
        if args.qiskit_check:
            mapped = [{**g, 'target': active.index(g['target']),
                       **({'control': active.index(g['control'])} if 'control' in g else {})} for g in body]
            expected = reference.Statevector.from_instruction(reference.circuit(3,
                [{'gate': 'h', 'target': i} for i in range(3)] + mapped)).data
            reference.np.testing.assert_allclose(local, expected, atol=1e-13, rtol=1e-13)
        probes, expected = validation_probes(n, active, local)
        gates = [{'gate': 'h', 'target': q} for q in range(n)] + body
        qiskit_result = {"status": "skipped", "reason": "disabled or dry run"}
        if not args.dry_run and args.qiskit_max_qubits:
            qiskit_result = qiskit_benchmark(n, gates, active, local, args)
        for precision in args.precisions:
            state_bytes = reference.STORAGE_BYTES[precision] * (1 << n)
            for threads in args.threads:
                memory = memory_snapshot()
                row = {"num_qubits": n, "precision": precision, "threads": threads,
                       "state_bytes": state_bytes, "gate_count": len(gates), "active_qubits": active,
                       "memory_before": memory, "qiskit_reference": qiskit_result}
                reason = skip_reason(state_bytes, args.max_state_mib * MIB, memory,
                                     args.memory_reserve_mib * MIB, args.physical_memory_fraction)
                if reason:
                    row.update(status='skipped', reason=reason)
                elif args.dry_run:
                    row['status'] = 'planned'
                else:
                    request = {"num_qubits": n, "precision": precision, "threads": threads,
                               "gates": gates, "include_state": False, "probe_indices": probes,
                               "memory_limit_bytes": args.max_state_mib * MIB,
                               "benchmark": {"repetitions": args.repetitions, "warmup": args.warmup}}
                    print(f"RUN n={n} {precision} threads={threads} state={state_bytes / GIB:.3f} GiB", flush=True)
                    try:
                        result, process = run_request(executable, request, args.case_timeout, measure_rss)
                        assert result['state_bytes'] == state_bytes and result['precision'] == precision
                        actual = [complex(*v) for v in result['probes']]
                        assert len(actual) == len(expected)
                        metrics = accuracy_metrics(actual, expected, result['norm_sqr'], precision)
                        timing = reference.summary(result['seconds'])
                        row.update(status='passed' if metrics['passed'] else 'accuracy_failed',
                                   accuracy=metrics, timing=timing, process=process,
                                   norm_sqr=result['norm_sqr'], parallel_threshold=result['parallel_threshold'],
                                   probes={"indices": probes, "actual": result['probes'],
                                           "expected": [[v.real, v.imag] for v in expected]},
                                   gates_per_second=len(gates) / timing['median_seconds'])
                        if qiskit_result['status'] == 'passed':
                            row['qiskit_over_rust_ratio'] = qiskit_result['timing']['median_seconds'] / timing['median_seconds']
                    except (RuntimeError, TimeoutError, OSError, ValueError, TypeError, OverflowError, AssertionError) as exc:
                        row.update(status='error', reason=str(exc))
                    row['memory_after'] = memory_snapshot()
                rows.append(row)
                write_report(args.output, metadata, rows)
                detail = f"median={row['timing']['median_seconds']:.6f}s" if 'timing' in row else row.get('reason', '')
                print(f"{row['status'].upper()} n={n} {precision} threads={threads} {detail}", flush=True)
    write_report(args.output, metadata, rows, complete=True)
    print(f"Saved {len(rows)} size/precision/worker configurations to {args.output}")
    if any(r['status'] in ['error', 'accuracy_failed'] for r in rows):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
