import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from statistics import median
import sys

root = Path.cwd()
sys.path.insert(0, str(root / 'tools'))
from compare_runtimes import RustSession, engine_order

output = root / 'benchmark-results/cx22-compression'
workspace = tempfile.TemporaryDirectory(prefix='qiskit-cx-grain-')
build = Path(workspace.name)
for name in ('Cargo.toml', 'Cargo.lock'):
    shutil.copy2(root / name, build / name)
shutil.copytree(root / 'src', build / 'src')
execution = build / 'src/execution.rs'
source = execution.read_text()
start = source.index('pub(crate) fn pairs')
end = source.index('pub(crate) fn apply')
binaries = {}
for grain in (4096, 2048, 8192):
    traversal = re.sub(
        r'4096|8192', lambda match: str(int(match[0]) * grain // 4096),
        source[start:end],
    )
    execution.write_text(source[:start] + traversal + source[end:])
    subprocess.run(
        ['cargo', 'build', '--release', '--locked', '--offline',
         '--target-dir', str(build / 'target')],
        cwd=build, check=True,
    )
    binary = build / f'statevector-{grain}'
    shutil.copy2(build / 'target/release/statevector', binary)
    binaries[str(grain)] = binary
exact = next((output / 'cases').glob('*.request.json'))
request = dict(json.loads(exact.read_text()), optimize=False)
cases = {'cx_stride': request, 'cx_stride_compressed': dict(request, optimize=True)}
for control, target in [(0, 20), (10, 21), (21, 0), (21, 10)]:
    cases[f'cx_{control}_to_{target}'] = dict(
        request, fusion=False,
        gates=[{'gate': 'h', 'target': control}] + [
            {'gate': 'cx', 'control': control, 'target': target}
        ] * 63,
        probe_indices=sorted({0, 1 << control, (1 << control) | (1 << target), (1 << 22) - 1}),
    )

sessions = {name: RustSession(str(path)) for name, path in binaries.items()}
results = {}
try:
    for case, request in cases.items():
        timings = {name: {'request_seconds': [], 'gate_seconds': []} for name in sessions}
        expected = None
        for trial in range(9):
            for name in engine_order(sessions, trial, 42):
                value, elapsed = sessions[name].run(request)
                if expected is None:
                    expected = value['probes']
                assert expected == value['probes'], (case, name, trial)
                if trial >= 2:
                    timings[name]['request_seconds'].append(elapsed)
                    timings[name]['gate_seconds'].append(value['seconds'][0])
        results[case] = {'request': request, 'timings': timings}
        print(case, {name: round(1e3 * median(samples['gate_seconds']), 3)
                     for name, samples in timings.items()}, flush=True)
finally:
    for session in sessions.values():
        session.close()

(output / 'grain-results.json').write_text(json.dumps({
    'binaries': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in binaries.items()},
    'warmups': 2, 'repetitions': 7, 'worker_count': 4,
    'scope': 'Rust FP64, paired traversal grain 2048/4096/8192, outer grains twice these values',
    'cases': results,
}, indent=2) + '\n')
