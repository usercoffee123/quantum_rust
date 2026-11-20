"""Timing-policy, failure handling, and optional real-backend integration checks."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compare_runtimes as c


class RuntimeComparisonTests(unittest.TestCase):
    def row(self, precision='f64', status='passed'):
        return dict(id='case', num_qubits=8, family='mixed', precision=precision,
                    threads=4, fusion=True, optimize_cx=False, status=status,
                    validation_scope='full', timings={
                        name: c.summary([value, value, value])
                        for name, value in [('rust', .01), ('python', .1), ('aer', .02)]})

    def test_ratios_require_passing_checks_and_equal_precision(self):
        self.assertEqual(c.ratios(self.row()), {'python_over_rust': 10., 'aer_over_rust': 2.})
        self.assertEqual(c.ratios(self.row('f32')), {'aer_over_rust': 2.})
        self.assertEqual(c.ratios(self.row('f16')), {})
        self.assertEqual(c.ratios(self.row(status='accuracy_failed')), {})
        self.assertEqual(c.ratios(self.row(status='error')), {})

    def test_rotating_order_balances_engine_positions(self):
        names = ['rust', 'python', 'aer']
        orders = [c.engine_order(names, i, 42) for i in range(3)]
        for position in range(3):
            self.assertEqual({order[position] for order in orders}, set(names))
        self.assertEqual(c.engine_order(names, 0, 42), c.engine_order(names, 3, 42))

    def test_sparse_cx_probes_cover_occupied_and_empty_amplitudes(self):
        config = dict(seed=42, depth=64, target='high')
        for n, occupied in [(6, 8), (7, 16), (21, 33), (22, 33)]:
            with self.subTest(qubits=n):
                case = dict(num_qubits=n, family='cx_stride')
                gates = c.gate_list(case, config)
                # Enumerate the support gate by gate from |0>, independently of
                # the harness's direct Bell preparation and sampled mismatches.
                support = {0}
                for gate in gates:
                    target_mask = 1 << gate['target']
                    if gate['gate'] == 'h':
                        support |= {index ^ target_mask for index in support}
                    else:
                        control_mask = 1 << gate['control']
                        support = {index ^ target_mask if index & control_mask else index
                                   for index in support}
                probes = c.validation_probes(case, config, gates)
                self.assertEqual(probes, c.validation_probes(case, config, gates))
                self.assertEqual(probes, sorted(set(probes)))
                self.assertEqual(len(probes), min(67, 1 << n))
                self.assertTrue(all(0 <= index < 1 << n for index in probes))
                self.assertIn(0, probes)
                self.assertEqual(len(support.intersection(probes)), occupied)

    def test_checkpoint_retains_skips_and_failure_without_ratios(self):
        failed = self.row(status='accuracy_failed')
        skipped = dict(self.row(status='skipped'), timings={}, reason='memory limit')
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            c.write_outputs(base, dict(complete=False, results=[failed, skipped]))
            saved = json.loads((base/'results.json').read_text())
            self.assertFalse(saved['complete'])
            self.assertEqual(saved['results'][1]['reason'], 'memory limit')
            self.assertNotIn('10.00x', (base/'REPORT.md').read_text())
            self.assertIn('python_over_rust', (base/'results.csv').read_text())

    def test_dry_run_never_builds_launches_or_writes(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d)/'no-files'
            with mock.patch.object(c, 'admission', return_value=(None, {})), \
                 mock.patch.object(c.subprocess, 'Popen', side_effect=AssertionError('must not launch')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(c.main(['--dry-run', '--qubits', '8', '--output', str(output)]), 0)
            self.assertFalse(output.exists())

    def test_suite_defaults_plan_both_precisions_for_five_families(self):
        output = io.StringIO()
        with mock.patch.object(c, 'admission', return_value=(None, {})) as admission, \
             contextlib.redirect_stdout(output):
            self.assertEqual(c.main(['--suite', '--dry-run', '--qubits', '26']), 0)
        cases = [call.args[0] for call in admission.call_args_list]
        self.assertEqual(len(cases), 10)
        self.assertEqual({(case['family'], case['precision']) for case in cases},
                         {(family, precision)
                          for family in ('mixed', 'scrambled_bell', 'random_matching', 'cx_stride', 'diagonal')
                          for precision in ('f32', 'f64')})
        self.assertTrue(all(case['num_qubits'] == 26 and case['threads'] == 4 for case in cases))
        config = admission.call_args.args[1]
        self.assertEqual(config['engines'], ['aer'])
        self.assertEqual(config['max_state_mib'], 1024)

    def test_aer_probe_admission_budgets_sequential_state_buffers(self):
        import benchmark_sizes as sizes
        config = dict(engines=['aer'], full_check_max_qubits=20, max_state_mib=1024,
                      reserve_mib=1024)
        memory = dict(physical_bytes=16 << 30, available_bytes=5 << 30)
        with mock.patch.object(sizes, 'memory_snapshot', return_value=memory):
            case = dict(num_qubits=26)
            reason, estimate = c.admission(case, config)
            self.assertIsNone(reason)
            self.assertEqual(estimate['estimated_working_bytes'], 4 << 30)
            for override in (dict(full_check_max_qubits=26), dict(engines=['python', 'aer'])):
                reason, estimate = c.admission(case, dict(config, **override))
                self.assertIsNotNone(reason)
                self.assertEqual(estimate['estimated_working_bytes'], 8 << 30)
            memory['available_bytes'] -= 1
            reason, _ = c.admission(case, config)
            self.assertIn('headroom', reason)

    def test_timeout_terminates_only_launched_worker_group(self):
        proc = mock.Mock(pid=123, returncode=None)
        proc.wait.side_effect = [subprocess.TimeoutExpired(['worker'], .1), -9]
        with mock.patch.object(c.os, 'name', 'posix'), \
             mock.patch.object(c.subprocess, 'Popen', return_value=proc) as launch, \
             mock.patch.object(c.os, 'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                c.run_worker(['worker'], io.StringIO(), .1)
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        kill.assert_called_once_with(123, c.signal.SIGKILL)
        self.assertEqual(proc.wait.call_count, 2)


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'numerical environment required')
class AccuracyTests(unittest.TestCase):
    def test_low_amplitudes_still_detect_zeroed_state_and_phase_error(self):
        import numpy as np
        expected = np.full(16, 2**-15, dtype=complex)
        self.assertTrue(c.accuracy(expected, expected, 'f16', 1.)['passed'])
        self.assertFalse(c.accuracy(expected*0, expected, 'f16', 0.)['passed'])
        self.assertFalse(c.accuracy(-expected, expected, 'f64', 1.)['passed'])
        self.assertFalse(c.accuracy(expected, expected, 'f16', 1.1)['passed'])
        value = c.accuracy(np.full(16, complex(float('nan'), 0)), expected, 'f64')
        self.assertFalse(value['passed'])
        json.dumps(value, allow_nan=False)


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ['numpy', 'qiskit', 'qiskit_aer'])
                     and shutil.which('cargo'), 'Rust and installed comparison environment required')
class RealBackendTests(unittest.TestCase):
    def test_cli_saves_full_validation_matching_samples_and_exact_request(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)/'results'
            proc = subprocess.run([sys.executable, str(Path(c.__file__)), '--qubits', '6',
                                   '--threads', '4', '--families', 'scrambled_bell', '--layers', '1',
                                   '--repetitions', '2', '--warmup', '1', '--output', str(dest)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout+proc.stderr)
            result = json.loads((dest/'results.json').read_text())
            self.assertTrue(result['complete'])
            row = result['results'][0]
            self.assertEqual(row['status'], 'passed')
            self.assertEqual(row['validation_scope'], 'full')
            for engine in ['rust', 'python', 'aer']:
                self.assertEqual(len(row['timings'][engine]['samples_seconds']), 2)
                self.assertTrue(row['validation'][engine]['passed'])
            self.assertEqual(row['aer_metadata']['parallel_state_update'], 4)
            self.assertEqual(row['thread_environment']['OMP_NUM_THREADS'], '4')
            request = json.loads((dest/row['request_path']).read_text())
            self.assertTrue(request['fusion'])
            self.assertFalse(request['optimize'])
            self.assertFalse(request['include_norm'])
            self.assertEqual(row['compilation']['stats']['input_gates'], len(request['gates']))
            self.assertTrue((dest/'REPORT.md').exists())
            self.assertTrue((dest/'results.csv').exists())


if __name__ == '__main__':
    unittest.main()
