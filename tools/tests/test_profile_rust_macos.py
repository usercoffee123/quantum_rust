"""Portable analysis/planning tests; never launches Instruments or a simulator."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

SPEC = importlib.util.spec_from_file_location('profile_rust_macos', Path(__file__).resolve().parents[1] / 'profile_rust_macos.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)

CPU = '''<trace-query-result><node><row>
<sample-time>1500000000</sample-time><process id="p" fmt="statevector (7)"/>
<core id="c" fmt="CPU 6 (P Core)"/><thread id="t" fmt="worker-0"/>
<weight id="w">1000000</weight><tagged-backtrace id="s"><backtrace>
<frame id="f" name="qiskit_rust::hot"><source line="12"><path id="path">/src/hot.rs</path></source></frame>
<frame name="qiskit_rust::parent"/><frame ref="f"/>
</backtrace></tagged-backtrace></row><row>
<sample-time>1600000000</sample-time><process ref="p"/><core ref="c"/><thread ref="t"/>
<weight ref="w"/><tagged-backtrace ref="s"/></row><row>
<sample-time>1700000000</sample-time><process fmt="other (8)"/><weight>9000000000</weight>
<tagged-backtrace ref="s"/></row></node></trace-query-result>'''


def counter_xml():
    # Deliberately reorder schema columns: analysis must not depend on offsets.
    fields = ['process', 'metric-value', 'duration', 'timestamp', 'is-ratio', 'metric-display-name', 'documentation']
    schema = '<schema name="MetricTable">' + ''.join(f'<col><mnemonic>{f}</mnemonic></col>' for f in fields) + '</schema>'
    def row(name, value, duration, ratio=False, stamp=1500000000, process='statevector (7)'):
        return f'<row><process fmt="{process}"/><fixed-decimal>{value}</fixed-decimal><duration>{duration}</duration><start-time>{stamp}</start-time><boolean>{int(ratio)}</boolean><string>{name}</string><sentinel/></row>'
    return '<trace>' + schema + row('Load Micro-operations (Speculative)', 100, 10) + row('L1D Load Misses (Speculative)', 10, 10) + row('Ratio', .2, 10, True) + row('Ratio', .8, 30, True) + row('Ratio', .99, 100, True, process='other (8)') + row('Ratio', .99, 100, True, stamp=1) + '</trace>'


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_function_exclusive_inclusive_references_and_process_filter(self):
        source = self.base / 'cpu.xml'
        source.write_text(CPU)
        with mock.patch.object(p.shutil, 'which', return_value=None):
            summary, stacks = p.summarize_time(source)
        self.assertEqual(summary['samples'], 2)
        by_name = {f['function']: f for f in summary['functions']}
        self.assertEqual(by_name['qiskit_rust::hot']['exclusive_percent'], 100)
        # A recursive function only receives one inclusive contribution per sample.
        self.assertEqual(by_name['qiskit_rust::hot']['inclusive_percent'], 100)
        self.assertEqual(by_name['qiskit_rust::parent']['exclusive_percent'], 0)
        self.assertEqual(by_name['qiskit_rust::parent']['inclusive_percent'], 100)
        self.assertEqual(summary['source_lines'][0]['location'], '/src/hot.rs:12')
        self.assertEqual(sum(stacks.values()), 2000000)
        self.assertEqual(summary['threads'][0]['thread'], 'worker-0')
        with self.assertRaisesRegex(ValueError, 'No CPU samples'):
            p.summarize_time(source, skip_seconds=2)

    def test_counter_schema_filter_ratio_weighting_and_event_ratio(self):
        source = self.base / 'l1d.xml'
        source.write_text(counter_xml())
        result = p.summarize_metrics(source)
        self.assertAlmostEqual(result['metrics']['Ratio']['value'], .65)
        self.assertEqual(result['metrics']['Ratio']['rows'], 2)
        self.assertAlmostEqual(result['event_ratios']['l1d_load_misses_per_load_uop'], .1)
        self.assertEqual(result['unparsed_rows'], 0)

    def test_time_profile_supports_frames_directly_under_tagged_backtrace(self):
        source = self.base / 'cpu.xml'
        source.write_text(CPU.replace('<backtrace>', '').replace('</backtrace>', ''))
        with mock.patch.object(p.shutil, 'which', return_value=None):
            summary, _ = p.summarize_time(source)
        self.assertEqual(summary['samples'], 2)
        self.assertEqual(summary['functions'][0]['function'], 'qiskit_rust::hot')

    def test_counter_shared_ids(self):
        source = self.base / 'l1d.xml'
        xml = counter_xml().replace('<process fmt="statevector (7)"/>', '<process id="proc" fmt="statevector (7)"/>', 1)
        # Subsequent references resolve against earlier process definitions.
        first, rest = xml.split('<process id="proc" fmt="statevector (7)"/>', 1)
        source.write_text(first + '<process id="proc" fmt="statevector (7)"/>' + rest.replace('<process fmt="statevector (7)"/>', '<process ref="proc"/>'))
        self.assertAlmostEqual(p.summarize_metrics(source)['metrics']['Ratio']['value'], .65)

    def test_flamegraph_is_well_formed_and_escapes_symbols(self):
        dest = self.base / 'graph.svg'
        p.write_flamegraph({('root', 'hot<&>'): 1000000}, dest)
        root = ET.parse(dest).getroot()
        self.assertIn('svg', root.tag)
        self.assertIn('hot&lt;&amp;&gt;', dest.read_text())

    def test_template_changes_only_counting_mode(self):
        source, dest = self.base / 'source.plist', self.base / 'out.plist'
        source.write_bytes(plistlib.dumps({'$objects': [b'untouched', json.dumps({'selectedCountingMode': {}, 'keep': 7}).encode()]}))
        p.make_template(source, dest, 'dtlb')
        data = plistlib.loads(dest.read_bytes())
        self.assertEqual(data['$objects'][0], b'untouched')
        config = json.loads(data['$objects'][1])
        self.assertEqual(config['keep'], 7)
        self.assertEqual(config['selectedCountingMode']['countingMode'], 'data_address_translation_metrics')

    def test_toc_rejects_wrong_counter_mode(self):
        toc = self.base / 'toc.xml'
        toc.write_text('<trace-toc><run><summary><duration>2</duration><end-reason>Time limit reached</end-reason></summary><data><table schema="MetricTable"/><table counting-mode="bottleneck bottlenecks EL0"/></data></run></trace-toc>')
        self.assertEqual(p.inspect_toc(toc, 'pipeline')['recorded_seconds'], 2)
        with self.assertRaisesRegex(ValueError, 'did not confirm'):
            p.inspect_toc(toc, 'l1d')

    def test_commands_use_argument_arrays_and_correct_schema(self):
        command = p.record_command('Time Profiler', 20, '/a trace', '/a bin', '/a request', '/a output')
        self.assertEqual(command[-1], '/a bin')
        self.assertIn('/a request', command)
        command = p.export_command('/a trace', 'MetricTable', '/a output')
        self.assertIn('table[@schema="MetricTable"]', command[-3])

    def test_dry_run_does_not_build_record_or_write(self):
        request, output = self.base / 'in.json', self.base / 'out'
        request.write_text(json.dumps({'num_qubits': 2, 'gates': [], 'amplitudes_output': '/must/not/write', 'benchmark': {'repetitions': 9}}))
        with mock.patch.object(p.subprocess, 'Popen', side_effect=AssertionError('must not execute')), contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(p.main([str(request), '--output', str(output), '--dry-run', '--precision', 'f32']), 0)
        plan = json.loads(printed.getvalue())
        self.assertEqual(plan['precision'], 'f32')
        self.assertFalse(output.exists())
        self.assertEqual(len(plan['captures']), 5)
        self.assertEqual(plan['counter_modes'], ['pipeline', 'l1d', 'dtlb', 'itlb'])

    def test_saved_benchmark_request_is_selected_and_hash_checked(self):
        request = self.base / 'saved.request.json'
        content = b'{"num_qubits":22,"precision":"f32","threads":4,"gates":[]}'
        request.write_bytes(content)
        (self.base / 'results.json').write_text(json.dumps({'results': [
            {'id': 'saved', 'request_path': request.name,
             'request_sha256': hashlib.sha256(content).hexdigest()},
            {'id': 'skipped', 'status': 'skipped'},
        ]}))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(p.main(['--benchmark-run', str(self.base), '--dry-run']), 0)
        plan = json.loads(printed.getvalue())
        self.assertEqual(plan['original_request'], str(request.resolve()))
        self.assertEqual(plan['precision'], 'f32')
        self.assertEqual(plan['threads'], 4)
        request.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            p.benchmark_request(self.base, 'saved')

    def test_report_lists_available_and_unavailable_counter_modes(self):
        output = self.base / 'report'
        output.mkdir()
        (self.base / 'l1d.xml').write_text(counter_xml())
        p.analyze(self.base, output, 'statevector', 1, 5,
                  {'plan': {'counter_modes': ['l1d', 'itlb']}, 'system': {'hw.pagesize': '16384'}})
        report = (output / 'REPORT.md').read_text()
        self.assertIn('| L1D Cache Metrics | Available |', report)
        self.assertIn('| Instruction Address Translation Metrics | Unavailable for this capture |', report)
        self.assertIn('| Data Address Translation Metrics | Unrequested |', report)
        self.assertIn('| `hw.pagesize` | 16384 |', report)
        analysis = json.loads((output / 'analysis.json').read_text())
        self.assertEqual(analysis['unavailable_counter_modes'], ['itlb'])

    def test_active_worker_guard_prevents_build_and_record(self):
        request, output = self.base / 'in.json', self.base / 'capture'
        request.write_text(json.dumps({'num_qubits': 2, 'gates': []}))
        active = mock.Mock(returncode=0, stdout='260\n', stderr='')
        with mock.patch.object(p.sys, 'platform', 'darwin'), \
             mock.patch.object(p.subprocess, 'run', return_value=active), \
             mock.patch.object(p, 'run_logged', side_effect=AssertionError('must not build or record')), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                p.main([str(request), '--output', str(output)])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(output.exists())

    def test_analyze_only_reports_missing_data(self):
        output = self.base / 'analysis'
        with contextlib.redirect_stdout(io.StringIO()):
            code = p.main(['--analyze-only', str(self.base), '--output', str(output)])
        self.assertEqual(code, 1)
        self.assertIn('CPU export not found', (output / 'REPORT.md').read_text())


if __name__ == '__main__':
    unittest.main()
