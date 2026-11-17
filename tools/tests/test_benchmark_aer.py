from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_aer as bench


class EntangledCircuitTests(unittest.TestCase):
    def test_scrambling_preserves_maximal_half_cut_by_locality(self):
        gates = bench.entangled_gates(25, 8, 42, 'scrambled_bell')
        self.assertEqual(len(gates), 521)
        self.assertEqual(sum(g['gate']=='cx' for g in gates), 108)
        crossings = [g for g in gates if g['gate']=='cx' and (g['control']<12) != (g['target']<12)]
        self.assertEqual(crossings,[{'gate':'cx','control':q,'target':q+12} for q in range(12)])
        self.assertEqual({g['target'] for g in gates if g['gate']=='ry'},set(range(25)))

    def test_random_matching_is_reproducible_and_distinct(self):
        a=bench.entangled_gates(25,8,42,'random_matching')
        self.assertEqual(a,bench.entangled_gates(25,8,42,'random_matching'))
        self.assertNotEqual(a,bench.entangled_gates(25,8,43,'random_matching'))
        self.assertNotEqual(a,bench.entangled_gates(25,8,42,'scrambled_bell'))
        for gate in a:
            if gate['gate']=='cx': self.assertNotEqual(gate['control'],gate['target'])


if __name__=='__main__':
    unittest.main()
