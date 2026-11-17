import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_sizes as sweep


class SizeSweepTests(unittest.TestCase):
    def test_blocked_rss_instrumentation_is_detected_before_running_cases(self):
        with patch.object(sweep.sys, 'platform', 'darwin'), patch.object(sweep.subprocess, 'run') as run:
            run.return_value.returncode = 1
            run.return_value.stderr = 'time: sysctl kern.clockrate: Operation not permitted'
            self.assertFalse(sweep.peak_rss_supported())

    def test_all_integer_sizes_and_memory_boundaries(self):
        memory = {'physical_bytes': 16 * sweep.GIB, 'available_bytes': 7 * sweep.GIB}
        counts = {p: [] for p in sweep.ACCURACY}
        for n in range(4, 31):
            for p, width in sweep.reference.STORAGE_BYTES.items():
                allowed = sweep.skip_reason(width * (1 << n), 4 * sweep.GIB, memory, 2 * sweep.GIB, .25) is None
                if allowed:
                    counts[p].append(n)
        self.assertEqual(counts['f64'], list(range(4, 29)))
        self.assertEqual(counts['f32'], list(range(4, 30)))
        self.assertEqual(counts['f16'], list(range(4, 31)))

    def test_live_memory_and_unknown_memory_prevent_large_allocations(self):
        memory = {'physical_bytes': 16 * sweep.GIB, 'available_bytes': 3 * sweep.GIB}
        self.assertIsNotNone(sweep.skip_reason(2 * sweep.GIB, 4 * sweep.GIB, memory, 2 * sweep.GIB, .25))
        unknown = {'physical_bytes': None, 'available_bytes': None}
        self.assertIsNotNone(sweep.skip_reason(sweep.GIB, 4 * sweep.GIB, unknown, 0, .25))
        self.assertIsNone(sweep.skip_reason(128 * sweep.MIB, 256 * sweep.MIB, unknown, 0, .25))

    def test_probe_mapping_and_uniform_spectators(self):
        active = [0, 3, 6]
        self.assertEqual(sweep.scatter(5, active), 65)
        local = sweep.local_reference(active, [])
        indices, expected = sweep.validation_probes(7, active, local)
        self.assertEqual(len(set(indices)), 16)
        self.assertTrue(all(abs(v - 2**(-7/2)) < 1e-15 for v in expected))
        for local_index, global_index in enumerate(indices[:8]):
            self.assertEqual(global_index, sweep.scatter(local_index, active))

    def test_reference_controlled_phase_and_norm(self):
        active = [0, 2, 3]
        phase = sweep.local_reference(active, [{'gate': 'cz', 'control': 0, 'target': 3}])
        for i, v in enumerate(phase):
            self.assertAlmostEqual(v.real, (-1 if i & 1 and i & 4 else 1) / math.sqrt(8))
        for n in range(4, 31):
            active, body = sweep.body_gates(n, 32)
            state = sweep.local_reference(active, body)
            self.assertAlmostEqual(sum(abs(v)**2 for v in state), 1.0, places=13)

    def test_scaled_accuracy_and_norm_are_both_checked(self):
        expected = [complex(2**(-30/2))] * 16
        exact = sweep.accuracy_metrics(expected, expected, 1.0, 'f16')
        self.assertTrue(exact['passed'])
        bad = sweep.accuracy_metrics([2*v for v in expected], expected, 4.0, 'f16')
        self.assertFalse(bad['passed'])
        self.assertAlmostEqual(bad['normalized_active_fidelity'], 1.0)
        self.assertAlmostEqual(bad['scaled_probe_error'], 1.0)
        self.assertFalse(sweep.accuracy_metrics(expected, expected, 1.1, 'f16')['passed'])


if __name__ == '__main__':
    unittest.main()
