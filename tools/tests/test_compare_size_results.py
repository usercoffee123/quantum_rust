from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compare_size_results as comparison


class ComparisonTests(unittest.TestCase):
    def test_matches_precision_and_size_without_inventing_missing_times(self):
        rows = [dict(num_qubits=n, precision=p, threads=4, status='passed', state_bytes=128,
                     timing={'median_seconds': t}) for n,p,t in [(4,'f64',2.0),(4,'f32',1.0),(30,'f16',10.0)]]
        refs = {'4': {'status': 'passed', 'timing': {'median_seconds': 8.0}},
                '30': {'status': 'skipped', 'reason': 'memory'}}
        result = comparison.comparison_rows(rows,refs)
        self.assertEqual(result[0]['qiskit_over_rust_ratio'],4.0)
        self.assertEqual(result[1]['qiskit_over_rust_ratio'],8.0)
        self.assertEqual(result[2]['status'],'not_compared')
        self.assertNotIn('qiskit_over_rust_ratio',result[2])

    def test_rust_skip_is_not_counted_as_a_comparison(self):
        rows=[dict(num_qubits=30,precision='f64',threads=4,status='skipped',state_bytes=16<<30)]
        refs={'30':{'status':'passed','timing':{'median_seconds':20}}}
        result=comparison.comparison_rows(rows,refs)[0]
        self.assertEqual(result['status'],'not_compared')
        self.assertIsNone(result['rust_timing'])


if __name__=='__main__':
    unittest.main()
