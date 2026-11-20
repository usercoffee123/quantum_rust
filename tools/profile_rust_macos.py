#!/usr/bin/env python3
"""Profile a statevector JSON request using macOS Instruments; stdlib only.

Run --dry-run first. Captures are bounded windows, not full-circuit timings.
Use --analyze-only DIR to re-analyze exports without running the simulator.
Use --benchmark-run DIR to select a saved comparison case.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import shlex
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
COUNTERS = {
    'pipeline': ('bottleneck', 'bottlenecks', 'CPU Bottlenecks'),
    'l1d': ('metrics', 'l1d_metrics', 'L1D Cache Metrics'),
    'dtlb': ('metrics', 'data_address_translation_metrics', 'Data Address Translation Metrics'),
    'itlb': ('metrics', 'instruction_address_translation_metrics', 'Instruction Address Translation Metrics'),
}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def open_xml(path):
    return gzip.open(path, 'rb') if path.suffix == '.gz' else path.open('rb')


def demangle(names):
    names = sorted(set(names))
    rustfilt = shutil.which('rustfilt')
    llvm = shutil.which('llvm-cxxfilt')
    xcrun = shutil.which('xcrun') if sys.platform == 'darwin' else None
    command = [rustfilt] if rustfilt else [llvm] if llvm else [xcrun, 'llvm-cxxfilt'] if xcrun else None
    if not command or not names:
        return dict(zip(names, names))
    try:
        result = subprocess.run(command, input='\n'.join(names) + '\n', text=True,
                                capture_output=True, timeout=30, check=True)
        decoded = result.stdout.splitlines()
        if len(decoded) == len(names):
            return dict(zip(names, decoded))
    except (OSError, subprocess.SubprocessError):
        pass
    return dict(zip(names, names))


def summarize_time(path, process='statevector', skip_seconds=1.0):
    with open_xml(path) as stream:
        root = ET.parse(stream).getroot()
    ids = {e.attrib['id']: e for e in root.iter() if 'id' in e.attrib}

    def resolve(e):
        if e is None:
            return None
        return ids[e.attrib['ref']] if 'ref' in e.attrib else e

    names = demangle(e.attrib['name'] for e in root.iter('frame') if 'name' in e.attrib)
    exclusive, inclusive, lines, cores, threads, regions = (collections.Counter() for _ in range(6))
    stacks = collections.Counter()
    total = excluded = samples = 0
    first = last = None
    for row in root.iter('row'):
        proc = resolve(row.find('process'))
        if proc is None or proc.attrib.get('fmt', '').split(' (')[0] != process:
            continue
        tagged = resolve(row.find('tagged-backtrace'))
        weight = resolve(row.find('weight'))
        stamp = resolve(row.find('sample-time'))
        if tagged is None or weight is None or stamp is None:
            continue
        timestamp, ns = int(stamp.text), int(weight.text)
        if timestamp < skip_seconds * 1e9:
            excluded += ns
            continue
        backtrace = resolve(tagged.find('backtrace'))
        if backtrace is None:
            backtrace = tagged
        frames = [resolve(f) for f in backtrace.findall('frame')]
        if not frames:
            continue
        symbols = [names.get(f.attrib.get('name', '?'), f.attrib.get('name', '?')) for f in frames]
        exclusive[symbols[0]] += ns
        # Recursive/repeated frames count at most once per sampled stack.
        for symbol in set(symbols):
            inclusive[symbol] += ns
        src = frames[0].find('source')
        if src is not None:
            source_path = resolve(src.find('path'))
            location = f"{source_path.text if source_path is not None else '?'}:{src.attrib.get('line', '?')}"
            lines[(symbols[0], location)] += ns
        core = resolve(row.find('core'))
        thread = resolve(row.find('thread'))
        cores[core.attrib.get('fmt', '?') if core is not None else '?'] += ns
        threads[thread.attrib.get('fmt', '?') if thread is not None else '?'] += ns
        joined = ' '.join(symbols)
        if 'apply_fused' in joined or 'fusion::quads' in joined:
            region = 'Fused execution (dense or separable subtree)'
        elif 'circuit::' in joined or 'fusion::compose' in joined or 'fusion::collect' in joined:
            region = 'Circuit compilation (inclusive subtree)'
        elif any(name in joined for name in ('apply_validated', 'execution::pairs',
                                             'execution::controlled_pairs', 'execution::apply', '::matrix')):
            region = 'Specialized gate kernels (inclusive subtree)'
        elif 'rayon' in joined:
            region = 'Rayon outside recognized kernel subtrees'
        elif 'serde' in joined:
            region = 'JSON outside recognized kernel subtrees'
        else:
            region = 'Other runtime/allocation/system code'
        regions[region] += ns
        stacks[tuple(s.replace(';', ',').replace('\n', ' ') for s in reversed(symbols))] += ns
        total += ns
        samples += 1
        first = timestamp if first is None else min(first, timestamp)
        last = timestamp if last is None else max(last, timestamp)
    if not total:
        raise ValueError(f'No CPU samples for {process} after {skip_seconds:g}s in {path}')

    def entries(counter, label):
        return [{label: k, 'sampled_cpu_seconds': v / 1e9, 'percent': 100 * v / total}
                for k, v in counter.most_common()]

    functions = [{'function': name, 'exclusive_cpu_seconds': exclusive[name] / 1e9,
                  'exclusive_percent': 100 * exclusive[name] / total,
                  'inclusive_cpu_seconds': inclusive[name] / 1e9,
                  'inclusive_percent': 100 * inclusive[name] / total}
                 for name in sorted(inclusive, key=lambda n: (exclusive[n], inclusive[n]), reverse=True)]
    application = [f for f in functions if 'qiskit_rust::' in f['function'] or
                   f['function'].startswith('statevector::')]
    summary = {'process': process, 'skip_seconds': skip_seconds, 'samples': samples,
               'sampled_cpu_seconds': total / 1e9, 'excluded_startup_cpu_seconds': excluded / 1e9,
               'first_sample_seconds': first / 1e9, 'last_sample_seconds': last / 1e9,
               'functions': functions,
               'application_functions': application,
               'inclusive_functions': sorted(functions, key=lambda f: f['inclusive_cpu_seconds'], reverse=True),
               'source_lines': [{'function': k[0], 'location': k[1], 'percent': 100 * v / total,
                                 'sampled_cpu_seconds': v / 1e9} for k, v in lines.most_common()],
               'cores': entries(cores, 'core'), 'threads': entries(threads, 'thread'),
               'regions': entries(regions, 'region')}
    return summary, stacks


def summarize_metrics(path, process='statevector', skip_seconds=1.0):
    """Stream MetricTable rows, resolving shared IDs and using schema mnemonics."""
    ids, columns, metrics = {}, {}, {}
    skipped = 0
    with open_xml(path) as stream:
        for _, e in ET.iterparse(stream, events=['end']):
            if 'id' in e.attrib:
                ids[e.attrib['id']] = (e.text, e.attrib.get('fmt', ''))
            if e.tag == 'schema' and e.attrib.get('name') == 'MetricTable':
                columns = {c.findtext('mnemonic'): i for i, c in enumerate(e.findall('col'))}
            if e.tag != 'row':
                continue
            cells = list(e)

            def value(name):
                cell = cells[columns[name]]
                return ids[cell.attrib['ref']] if 'ref' in cell.attrib else (cell.text, cell.attrib.get('fmt', ''))

            try:
                if value('process')[1].split(' (')[0] != process or int(value('timestamp')[0]) < skip_seconds * 1e9:
                    e.clear()
                    continue
                name = value('metric-display-name')[0]
                duration = int(value('duration')[0])
                measured = float(value('metric-value')[0])
                ratio = value('is-ratio')[0] == '1'
                doc = value('documentation')[0]
            except (KeyError, IndexError, ValueError, TypeError):
                skipped += 1
                e.clear()
                continue
            entry = metrics.setdefault(name, dict(rows=0, total=0.0, duration_ns=0,
                                                  weighted=0.0, is_ratio=ratio, documentation=doc))
            if entry['is_ratio'] != ratio:
                raise ValueError(f'Inconsistent metric type: {name}')
            entry['rows'] += 1
            entry['total'] += measured
            entry['duration_ns'] += duration
            entry['weighted'] += measured * duration
            e.clear()
    if not metrics:
        raise ValueError(f'No hardware metrics for {process} after {skip_seconds:g}s in {path}')
    result = {}
    for name, m in metrics.items():
        v = (m['weighted'] / m['duration_ns'] if m['duration_ns'] else None) if m['is_ratio'] else m['total']
        result[name] = {'value': v, 'is_ratio': m['is_ratio'], 'rows': m['rows'],
                        'documentation': m['documentation']}
    derived = {}
    for label, numerator, denominator in [
        ('l1d_load_misses_per_load_uop', 'L1D Load Misses (Speculative)', 'Load Micro-operations (Speculative)'),
        ('l1d_store_misses_per_store_uop', 'L1D Store Misses (Speculative)', 'Store Micro-operations (Speculative)'),
    ]:
        if numerator in result and denominator in result and result[denominator]['value']:
            derived[label] = result[numerator]['value'] / result[denominator]['value']
    return {'process': process, 'skip_seconds': skip_seconds, 'unparsed_rows': skipped,
            'metrics': result, 'event_ratios': derived}


def write_flamegraph(stacks, output):
    tree = {'weight': 0, 'children': {}}
    for stack, weight in stacks.items():
        node = tree
        node['weight'] += weight
        for name in stack:
            node = node['children'].setdefault(name, {'weight': 0, 'children': {}})
            node['weight'] += weight
    total = tree['weight']
    depth = max((len(s) for s in stacks), default=1)
    width, height = 1400, 24 + 19 * (depth + 1)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<style>text{font:11px monospace;pointer-events:none}rect:hover{stroke:#111;stroke-width:2}</style>',
           '<rect width="100%" height="100%" fill="white"/>',
           '<text x="8" y="15">Sampled CPU stacks: root at bottom; hover for function and weight</text>']

    def draw(node, left, level):
        x = left
        for name, child in sorted(node['children'].items()):
            w = width * child['weight'] / total
            if w >= 0.2:
                y = height - 19 * (level + 1)
                color = int(hashlib.sha256(name.encode()).hexdigest()[:2], 16)
                label = escape(name)
                svg.append(f'<g><title>{label}: {child["weight"]/1e9:.6f} sampled CPU s ({100*child["weight"]/total:.2f}%)</title>'
                           f'<rect x="{x:.3f}" y="{y}" width="{w:.3f}" height="18" fill="rgb(245,{100+color//3},{45+color//5})"/>')
                if w >= 50:
                    svg.append(f'<text x="{x+3:.3f}" y="{y+13}">{escape(name[:max(1,int((w-6)/7))])}</text>')
                svg.append('</g>')
                draw(child, x, level + 1)
            x += w
    draw(tree, 0, 0)
    svg.append('</svg>')
    output.write_text('\n'.join(svg))


def make_template(source, output, mode):
    template = plistlib.loads(source.read_bytes())
    count = 0
    for i, obj in enumerate(template['$objects']):
        if not isinstance(obj, bytes):
            continue
        try:
            config = json.loads(obj)
        except (ValueError, UnicodeError):
            continue
        if isinstance(config, dict) and 'selectedCountingMode' in config:
            analysis, counting, display = COUNTERS[mode]
            config['selectedCountingMode'] = dict(analysisMode=analysis, countingMode=counting)
            config['selectedCountingModeDisplayName'] = display
            template['$objects'][i] = json.dumps(config).encode()
            count += 1
    if not count:
        raise ValueError('Installed CPU Counters template has no recognized counting-mode configuration')
    output.write_bytes(plistlib.dumps(template, fmt=plistlib.FMT_BINARY))


def system_snapshot():
    """Read-only context for thermal/memory/swap changes; failures stay explicit."""
    result = {}
    for name, command in [('vm_stat', ['vm_stat']), ('memory_pressure', ['memory_pressure', '-Q']),
                          ('thermal', ['pmset', '-g', 'therm'])]:
        try:
            value = subprocess.run(command, capture_output=True, text=True, timeout=5)
            result[name] = {'exit_code': value.returncode, 'output': value.stdout.strip(),
                            'error': value.stderr.strip()}
        except (OSError, subprocess.SubprocessError) as exc:
            result[name] = {'error': str(exc)}
    return result


def run_logged(command, log, env=None, timeout=180, accepted=(0,)):
    """No shell interpolation. Interrupt/timeout only terminates this command group."""
    print('+', ' '.join(map(str, command)), flush=True)
    start = time.perf_counter()
    proc = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, cwd=ROOT, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            output, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            output, _ = proc.communicate()
        log.write_text(output)
        raise
    log.write_text(output)
    if proc.returncode not in accepted:
        raise RuntimeError(f'Command exited {proc.returncode}; see {log}')
    return {'command': list(map(str, command)), 'exit_code': proc.returncode,
            'elapsed_seconds': time.perf_counter() - start}


def record_command(template, seconds, trace, binary, request, stdout):
    return ['xcrun', 'xctrace', 'record', '--template', str(template), '--time-limit', f'{seconds:g}s',
            '--output', str(trace), '--target-stdin', str(request), '--target-stdout', str(stdout),
            '--launch', '--', str(binary)]


def export_command(trace, schema, output):
    return ['xcrun', 'xctrace', 'export', '--input', str(trace), '--xpath',
            f'/trace-toc/run[@number="1"]/data/table[@schema="{schema}"]', '--output', str(output)]


def inspect_toc(path, mode):
    root = ET.parse(path).getroot()
    reason = root.findtext('.//summary/end-reason', '')
    duration = root.findtext('.//summary/duration')
    tables = root.findall('.//data/table')
    schema = 'time-profile' if mode == 'cpu' else 'MetricTable'
    if not any(t.attrib.get('schema') == schema for t in tables):
        raise ValueError(f'Expected {schema} missing from trace; do not interpret this capture as {mode}')
    # Check the actual selected counting-mode attribute, not the list of
    # available modeler modes (which can mention modes that were not captured).
    counting_modes = [shlex.split(t.attrib['counting-mode']) for t in tables if 'counting-mode' in t.attrib]
    if mode != 'cpu':
        analysis, counting, _ = COUNTERS[mode]
        if [analysis, counting, 'EL0'] not in counting_modes:
            raise ValueError(f'Trace did not confirm requested {analysis} {counting} EL0 mode')
    return {'recorded_seconds': float(duration) if duration else None,
            'end_reason': reason, 'confirmed_counting_modes': counting_modes}



def compress(path):
    destination = path.with_suffix(path.suffix + '.gz')
    with path.open('rb') as src, gzip.open(destination, 'wb') as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return destination


def find_export(directory, names):
    for name in names:
        for suffix in ('.xml.gz', '.xml'):
            path = directory / (name + suffix)
            if path.exists():
                return path
    return None


def analyze(directory, output, process, skip_seconds, top, metadata):
    errors = []
    cpu = find_export(directory, ['cpu', 'time-samples'])
    summary = None
    if cpu:
        try:
            summary, stacks = summarize_time(cpu, process, skip_seconds)
            write_json(output / 'cpu.summary.json', summary)
            (output / 'stacks.folded').write_text(''.join(';'.join(stack) + f' {ns}\n' for stack, ns in sorted(stacks.items())))
            write_flamegraph(stacks, output / 'flamegraph.svg')
        except (ValueError, ET.ParseError, KeyError) as exc:
            errors.append(f'CPU analysis: {exc}')
    else:
        errors.append('CPU export not found')
    counters = {}
    requested_modes = metadata.get('plan', {}).get('counter_modes')
    for mode in COUNTERS:
        path = find_export(directory, [mode])
        if path:
            try:
                counters[mode] = summarize_metrics(path, process, skip_seconds)
                write_json(output / f'{mode}.summary.json', counters[mode])
            except (ValueError, ET.ParseError, KeyError) as exc:
                errors.append(f'{mode} analysis: {exc}')
    report = ['# Rust macOS profiling report', '',
              'These are bounded sampling/counter windows, not completed-circuit timings. '
              f'Analysis selects process `{process}` and timestamps >= {skip_seconds:g} seconds. '
              'Each recording launches a separate fresh-state request. The recording limit intentionally ends the target.', '',
              'Build, request hashes, recorded intervals, commands and stage errors are in `metadata.json`. '
              'Raw `.trace` files open in Instruments. Raw XML exports are gzip compressed. '
              'Capture metadata includes before/after memory, swap and thermal snapshots when available. '
              'Optional `disassembly.txt` shows demangled machine code with source line numbers.', '']

    def safe(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')

    if requested_modes is not None:
        report += ['## Counter availability', '',
                   '| Mode | Status |', '|---|---|']
        for mode, (_, _, title) in COUNTERS.items():
            status = 'Available' if mode in counters else (
                'Unavailable for this capture' if mode in requested_modes else 'Unrequested')
            report.append(f'| {title} | {status} |')
        report += ['']
    if metadata.get('system'):
        report += ['## Host hardware', '', '| Property | Bytes |', '|---|---:|']
        for name, value in metadata['system'].items():
            report.append(f'| `{safe(name)}` | {safe(value) if value else "Unavailable"} |')
        report += ['']

    if summary:
        report += ['## CPU functions', '',
                   f'{summary["samples"]:,} samples; {summary["sampled_cpu_seconds"]:.3f} weighted CPU seconds across threads. '
                   f'{summary["excluded_startup_cpu_seconds"]:.3f} sampled CPU seconds excluded by startup cutoff.', '',
                   'Exclusive samples land in that function; inclusive samples have it anywhere on the stack. '
                   'Inclusive shares overlap and must not be added. Inlining/skid can shift source attribution. '
                   'These are running CPU samples, not wall-time stall or lock-wait measurements.', '',
                   '| Function | Exclusive | Inclusive |', '|---|---:|---:|']
        for item in summary['functions'][:top]:
            report.append(f'| `{safe(item["function"])}` | {item["exclusive_percent"]:.2f}% | {item["inclusive_percent"]:.2f}% |')
        report += ['', '## Application function callers (inclusive ranking)', '',
                   '| Rust function | Inclusive | Exclusive |', '|---|---:|---:|']
        for item in sorted(summary['application_functions'], key=lambda f: f['inclusive_cpu_seconds'], reverse=True)[:top]:
            report.append(f'| `{safe(item["function"])}` | {item["inclusive_percent"]:.2f}% | {item["exclusive_percent"]:.2f}% |')
        report += ['', '## Source-line hotspots', '', '| Location | Exclusive sampled share |', '|---|---:|']
        for item in summary['source_lines'][:top]:
            report.append(f'| `{safe(item["location"])}` | {item["percent"]:.2f}% |')
        for heading, key, label in [('Execution regions', 'regions', 'region'), ('CPU cores', 'cores', 'core'), ('Threads', 'threads', 'thread')]:
            report += ['', f'## {heading}', '', '| Attribution | Sampled share |', '|---|---:|']
            for item in summary[key]:
                report.append(f'| {safe(item[label])} | {item["percent"]:.2f}% |')
        report += ['', '[Open the hoverable CPU flame graph](flamegraph.svg). '
                   '`stacks.folded` contains nanosecond-weighted stacks for other flame-graph tools. '
                   'Full, untruncated rankings are in `cpu.summary.json`.', '']
    for mode, values in counters.items():
        report += [f'## {COUNTERS[mode][2]}', '', '| Vendor metric | Value | Meaning |', '|---|---:|---|']
        for name, item in values['metrics'].items():
            v = item['value']
            displayed = 'unavailable' if v is None else (f'{100*v:.3f}%' if item['is_ratio'] else f'{v:,.0f}')
            meaning = 'Duration-weighted vendor ratio' if item['is_ratio'] else 'Summed events in selected window'
            report.append(f'| {safe(name)} | {displayed} | {meaning} |')
        for name, value in values['event_ratios'].items():
            report.append(f'\n{name}: **{100*value:.3f}%** (event-count ratio).')
        report += ['']
    report += ['## Interpretation and next experiments', '',
               '- Start with high exclusive application functions, then inspect their inclusive callers and hot source lines in Instruments.',
               '- Compare P-core/E-core distribution and thread shares across runs before attributing runtime variation to a kernel change.',
               '- L1 speculative miss/uop ratios are not unique cache-line hit rates, percentages of time stalled, L2/DRAM miss rates or measured bandwidth.',
               '- Pipeline bottleneck categories are Apple instruction-bandwidth metrics, not wall-time fractions. TLB metrics describe address translation, not directly cache capacity.',
               '- Compare FP64/FP32, thread counts, fusion settings and bit positions using separate output directories. The same capture duration is not the same amount of executed work.',
               '- Follow suspected locality problems with a controlled chunk/state-size sweep. Use `examples/profile_cache.rs` for the existing CX microbenchmark; logical throughput there is not physical DRAM bandwidth.',
               '- No L2/LLC or memory-controller bandwidth is inferred when those hardware counters were not captured. A profile identifies candidates; verify changes with uninstrumented repeated benchmarks.', '']
    capture_errors = metadata.get('errors', [])
    if capture_errors:
        report += ['## Capture errors', '', *[f'- {error}' for error in capture_errors], '']
    if errors:
        report += ['## Analysis errors', '', *[f'- {error}' for error in errors], '']
    (output / 'REPORT.md').write_text('\n'.join(report))
    write_json(output / 'analysis.json', {'cpu_available': summary is not None,
                                         'counter_modes': list(counters),
                                         'unavailable_counter_modes': [
                                             mode for mode in (requested_modes or []) if mode not in counters],
                                         'errors': errors})
    return errors


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('request', nargs='?', type=Path, help='One JSON request (pretty JSON accepted)')
    p.add_argument('--benchmark-run', type=Path, metavar='DIR',
                   help='Select an exact saved request from a comparison output directory')
    p.add_argument('--case-id', help='Case ID from benchmark results, required if multiple requests exist')
    p.add_argument('--output', type=Path, help='New capture directory, or analysis destination')
    p.add_argument('--time-seconds', type=float, default=20)
    p.add_argument('--counter-seconds', type=float, default=2)
    p.add_argument('--skip-seconds', type=float, default=1, help='Exclude startup samples/events before this timestamp')
    p.add_argument('--counters', nargs='*', choices=list(COUNTERS), default=list(COUNTERS))
    p.add_argument('--precision', choices=['f64', 'f32', 'f16'])
    p.add_argument('--threads', type=int)
    p.add_argument('--top', type=int, default=25)
    p.add_argument('--binary', type=Path, default=ROOT / 'target/profiling/release/statevector')
    p.add_argument('--skip-build', action='store_true')
    p.add_argument('--disassembly', action='store_true', help='Also save demangled, line-numbered machine code')
    p.add_argument('--dry-run', action='store_true', help='Print plan without creating files, building or recording')
    p.add_argument('--allow-concurrent', action='store_true', help='Allow capture despite another statevector worker (distorts timings)')
    p.add_argument('--analyze-only', type=Path, metavar='DIR', help='Analyze existing XML exports without building or recording')
    p.add_argument('--process', default='statevector', help='Exact exported process name for analysis')
    return p


def benchmark_request(directory, case_id):
    directory = directory.resolve()
    rows = json.loads((directory / 'results.json').read_text())['results']
    cases = {row['id']: row for row in rows if row.get('request_path')}
    if case_id is None and len(cases) == 1:
        case_id = next(iter(cases))
    if case_id not in cases:
        choices = ', '.join(cases) or 'none'
        raise ValueError(f'Choose a saved --case-id. Available cases: {choices}')
    case = cases[case_id]
    path = (directory / case['request_path']).resolve()
    if directory not in path.parents:
        raise ValueError('saved request path must be inside the benchmark run directory')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != case['request_sha256']:
        raise ValueError(f'saved benchmark request hash differs for {case_id}')
    return path, content


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not 1 <= args.time_seconds <= 120 or not 1 <= args.counter_seconds <= 5:
        p.error('CPU window must be 1..120s and counter window 1..5s (counter traces grow quickly)')
    if args.skip_seconds < 0 or args.top < 1 or (args.threads is not None and args.threads < 1):
        p.error('skip-seconds must be nonnegative; top and threads must be positive')
    if sum(bool(value) for value in (args.request, args.benchmark_run, args.analyze_only)) != 1:
        p.error('choose one request, --benchmark-run DIR, or --analyze-only DIR')
    if args.case_id and not args.benchmark_run:
        p.error('--case-id requires --benchmark-run')
    if args.analyze_only:
        source = args.analyze_only.resolve()
        output = (args.output or source).resolve()
        if args.dry_run:
            print(json.dumps({'analyze': str(source), 'output': str(output)}, indent=2))
            return 0
        output.mkdir(parents=True, exist_ok=True)
        meta_path = source / 'metadata.json'
        metadata = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        errors = analyze(source, output, args.process, args.skip_seconds, args.top, metadata)
        if output != source:
            write_json(output / 'metadata.json', {'analysis_source': str(source), 'source_metadata': metadata})
        print(output / 'REPORT.md')
        return bool(errors)
    if not args.skip_build and args.binary.resolve() != ROOT / 'target/profiling/release/statevector':
        p.error('--binary requires --skip-build; the managed build always uses target/profiling')
    if args.benchmark_run:
        request_path, original_content = benchmark_request(args.benchmark_run, args.case_id)
    else:
        request_path = args.request.resolve()
        original_content = request_path.read_bytes()
    request = json.loads(original_content)
    if not isinstance(request, dict) or 'num_qubits' not in request or not isinstance(request.get('gates'), list):
        p.error('request must be an object containing num_qubits and a gates array')
    # Avoid giant output or writing to an export path embedded in the input.
    request.update(include_state=False, include_norm=False)
    request.pop('amplitudes_output', None)
    request.pop('benchmark', None)
    if args.precision:
        request['precision'] = args.precision
    if args.threads:
        request['threads'] = args.threads
    output = (args.output or ROOT / 'benchmark-results' / ('profile-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S'))).resolve()
    binary = args.binary.resolve()
    if output.exists() and any(output.iterdir()):
        p.error('capture output directory must be empty; use --analyze-only to revisit exports')
    stages = [('cpu', 'Time Profiler', args.time_seconds)]
    stages += [(mode, output / f'{mode}.tracetemplate', args.counter_seconds) for mode in dict.fromkeys(args.counters)]
    plan = {'output': str(output), 'binary': str(binary), 'qubits': request['num_qubits'],
            'original_request': str(request_path), 'gate_count': len(request['gates']),
            'precision': request.get('precision', 'f64'), 'threads': request.get('threads', 1),
            'fusion': request.get('fusion', False), 'optimize': request.get('optimize', False),
            'build': not args.skip_build, 'skip_seconds': args.skip_seconds,
            'counter_modes': list(dict.fromkeys(args.counters)),
            'captures': [record_command(template, seconds, output / f'{name}.trace', binary,
                                        output / 'request.json', output / f'{name}.stdout.jsonl')
                         for name, template, seconds in stages]}
    print(json.dumps(plan, indent=2), flush=True)
    if args.dry_run:
        return 0
    if sys.platform != 'darwin':
        p.error('recording requires macOS and Xcode Instruments; analyze-only is portable')
    if args.skip_seconds >= min([args.time_seconds] + ([args.counter_seconds] if args.counters else [])):
        p.error('startup cutoff must be smaller than capture windows')
    if not args.allow_concurrent:
        active = subprocess.run(['pgrep', '-x', 'statevector'], capture_output=True, text=True)
        if active.returncode == 0:
            p.error('statevector is already running; wait for the benchmark, or explicitly use --allow-concurrent')
        if active.returncode not in (0, 1):
            p.error('could not check active workers; inspect processes and use --allow-concurrent if appropriate')
    if shutil.disk_usage(output.parent if output.parent.exists() else ROOT).free < 3 << 30:
        p.error('need at least 3 GiB free for bounded Instruments traces')
    output.mkdir(parents=True, exist_ok=True)
    effective = json.dumps(request) + '\n'
    (output / 'request.json').write_text(effective)
    metadata = {'plan': plan, 'platform': sys.platform, 'original_request': str(request_path),
                'original_request_sha256': hashlib.sha256(original_content).hexdigest(),
                'effective_request_sha256': hashlib.sha256(effective.encode()).hexdigest(),
                'build': 'release opt-level=3, debuginfo=2, frame pointers, no stripping; separate target/profiling',
                'stages': {}, 'errors': []}
    write_json(output / 'metadata.json', metadata)
    if not args.skip_build:
        env = dict(os.environ, CARGO_TARGET_DIR=str(ROOT / 'target/profiling'),
                   CARGO_PROFILE_RELEASE_DEBUG='2', CARGO_PROFILE_RELEASE_OPT_LEVEL='3', CARGO_PROFILE_RELEASE_STRIP='none')
        env['RUSTFLAGS'] = (env.get('RUSTFLAGS', '') + ' -C force-frame-pointers=yes').strip()
        # Building uses its own directory; normal target/release remains untouched.
        run_logged(['cargo', 'build', '--release', '--locked', '--bin', 'statevector'], output / 'build.log', env, 600)
        binary = ROOT / 'target/profiling/release/statevector'
        # A previous build's dSYM can have the wrong UUID after source changes.
        run_logged(['xcrun', 'dsymutil', binary, '-o', str(binary) + '.dSYM'], output / 'symbols.log')
    if not binary.is_file():
        raise FileNotFoundError(binary)
    if args.disassembly:
        run_logged(['xcrun', 'llvm-objdump', '--disassemble', '--demangle', '--line-numbers', binary],
                   output / 'disassembly.txt')
    metadata['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    metadata['source_sha256'] = hashlib.sha256(b''.join(f.read_bytes() for f in sorted((ROOT / 'src').rglob('*.rs')))).hexdigest()
    developer = Path(subprocess.check_output(['xcode-select', '-p'], text=True).strip())
    template_source = developer.parent / 'Applications/Instruments.app/Contents/Resources/templates/CPU Counters.tracetemplate'
    metadata['counter_template_source'] = str(template_source)
    metadata['system'] = {}
    for key in ['hw.memsize', 'hw.pagesize', 'hw.cachelinesize',
                'hw.perflevel0.l1icachesize', 'hw.perflevel0.l1dcachesize', 'hw.perflevel0.l2cachesize',
                'hw.perflevel1.l1icachesize', 'hw.perflevel1.l1dcachesize', 'hw.perflevel1.l2cachesize']:
        info = subprocess.run(['sysctl', '-n', key], capture_output=True, text=True)
        metadata['system'][key] = info.stdout.strip() if info.returncode == 0 else None
    for name, template, seconds in stages:
        try:
            metadata.setdefault('runtime_snapshots', {})[name] = {'before': system_snapshot()}
            if name != 'cpu':
                make_template(template_source, template, name)
            if shutil.disk_usage(output).free < 1 << 30:
                raise RuntimeError('Free disk space fell below 1 GiB; stopping further captures')
            trace = output / f'{name}.trace'
            command = record_command(template, seconds, trace, binary, output / 'request.json', output / f'{name}.stdout.jsonl')
            status = run_logged(command, output / f'{name}.record.log', timeout=seconds + 180, accepted=(0, 54))
            toc = output / f'{name}.toc.xml'
            run_logged(['xcrun', 'xctrace', 'export', '--input', trace, '--toc', '--output', toc], output / f'{name}.toc.log')
            status.update(inspect_toc(toc, name))
            if status['exit_code'] == 54 and 'time limit' not in status['end_reason'].lower():
                raise RuntimeError(f'Unexpected exit 54: {status["end_reason"]}')
            exported = output / f'{name}.xml'
            run_logged(export_command(trace, 'time-profile' if name == 'cpu' else 'MetricTable', exported), output / f'{name}.export.log')
            compress(exported)
            metadata['stages'][name] = status
            metadata['runtime_snapshots'][name]['after'] = system_snapshot()
        except (OSError, ValueError, RuntimeError, ET.ParseError, subprocess.SubprocessError) as exc:
            metadata['errors'].append(f'{name}: {exc}')
            print(f'{name} capture failed: {exc}', file=sys.stderr, flush=True)
        write_json(output / 'metadata.json', metadata)
    errors = analyze(output, output, args.process, args.skip_seconds, args.top, metadata)
    print(f'Report: {output / "REPORT.md"}', flush=True)
    return bool(errors or metadata['errors'])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Profiling failed: {exc}', file=sys.stderr)
        sys.exit(1)
