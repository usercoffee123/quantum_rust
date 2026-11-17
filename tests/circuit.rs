use qiskit_rust::{
    Amplitude, CompilationStats, CompiledCircuit, Complex16, Complex32, Complex64, CpuExecutor,
    Gate, TypedStateVector,
};

fn map(n: usize, gates: &[Gate]) -> Vec<usize> {
    // Independent column representation: propagate each basis-bit input.
    (0..n)
        .map(|bit| {
            let mut value = 1usize << bit;
            for gate in gates {
                if let Gate::Cx { control, target } = *gate {
                    value ^= ((value >> control) & 1) << target;
                } else {
                    panic!("expected CX-only circuit");
                }
            }
            value
        })
        .collect()
}

#[test]
fn random_linear_maps_match_on_every_basis_bit() {
    let mut seed = 17u64;
    for n in [2, 3, 5, 12, 25, 31, usize::BITS as usize - 1] {
        for length in [1, 7, 100, 2000] {
            let mut gates = Vec::new();
            for _ in 0..length {
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                let control = (seed >> 32) as usize % n;
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                let target = ((seed >> 32) as usize % (n - 1) + control + 1) % n;
                gates.push(Gate::Cx { control, target });
            }
            let compiled = CompiledCircuit::new(n, &gates).unwrap();
            assert!(compiled.gates().len() <= gates.len());
            assert_eq!(
                map(n, &gates),
                map(n, compiled.gates()),
                "n={n}, length={length}"
            );
        }
    }
}

#[test]
fn identity_fallback_boundaries_and_invalid_inputs() {
    let cx = Gate::Cx {
        control: 1,
        target: 0,
    };
    let cancelled = CompiledCircuit::new(2, &[cx.clone(), cx.clone()]).unwrap();
    assert!(cancelled.gates().is_empty());
    assert_eq!(cancelled.stats().compressed_cx_runs, 1);
    let single = CompiledCircuit::new(2, std::slice::from_ref(&cx)).unwrap();
    assert_eq!(single.gates().len(), 1);
    assert_eq!(single.stats().compressed_cx_runs, 0);
    let separated = CompiledCircuit::new(2, &[cx.clone(), Gate::H { target: 1 }, cx]).unwrap();
    assert_eq!(separated.gates().len(), 3);
    assert!(matches!(separated.gates()[1], Gate::H { target: 1 }));
    assert_eq!(separated.stats().cx_runs, 2);
    for gate in [
        Gate::Cx {
            control: 0,
            target: 0,
        },
        Gate::Cx {
            control: 2,
            target: 0,
        },
        Gate::H { target: 2 },
        Gate::Rz {
            target: 0,
            theta: f64::NAN,
        },
    ] {
        assert!(CompiledCircuit::new(2, &[gate.clone(), gate]).is_err());
    }
    assert!(CompiledCircuit::new(usize::BITS as usize, &[]).is_err());
    assert!(CompiledCircuit::new(0, &[]).unwrap().gates().is_empty());
    let mut state = TypedStateVector::<Complex64>::new(3).unwrap();
    let before = state.amplitudes().to_vec();
    assert!(
        CpuExecutor::new(4)
            .unwrap()
            .run_compiled(&mut state, &cancelled)
            .is_err()
    );
    assert_eq!(state.amplitudes(), before);
    let _: &CompilationStats = cancelled.stats();
}

fn compare<A: Amplitude + PartialEq>() {
    let n = 6;
    let mut gates = Vec::new();
    for target in 0..n {
        gates.push(Gate::Ry {
            target,
            theta: 0.17 * (target + 1) as f64,
        });
        gates.push(Gate::Rz {
            target,
            theta: -0.37,
        });
    }
    for section in 0..3 {
        for i in 0..200 {
            gates.push(Gate::Cx {
                control: i % n,
                target: (i + 1 + section) % n,
            });
        }
        gates.push(Gate::Cz {
            control: 0,
            target: 4,
        });
        gates.push(Gate::H { target: section });
    }
    // Preserve arbitrary complex nonunitary U and global phase behavior too.
    gates.push(Gate::U {
        target: 1,
        matrix: [
            [Complex64::new(0.0, 0.5), Complex64::new(0.2, 0.1)],
            [Complex64::new(-0.1, 0.3), Complex64::new(0.0, 0.7)],
        ],
    });
    let compiled = CompiledCircuit::new(n, &gates).unwrap();
    assert!(compiled.gates().len() < gates.len());
    let mut reference = TypedStateVector::<A>::new(n).unwrap();
    reference.apply_all(&gates).unwrap();
    for threads in [1, 2, 4] {
        let executor = CpuExecutor::new(threads)
            .unwrap()
            .with_parallel_threshold(0);
        // Reuse the same compilation across multiple fresh states/executors.
        for _ in 0..2 {
            let mut state = TypedStateVector::<A>::new(n).unwrap();
            executor.run_compiled(&mut state, &compiled).unwrap();
            assert_eq!(state.amplitudes(), reference.amplitudes());
        }
    }
}

#[test]
fn compiled_execution_is_bit_exact_in_all_precisions() {
    compare::<Complex64>();
    compare::<Complex32>();
    compare::<Complex16>();
}

#[test]
fn long_stride_cx_workload_has_the_same_linear_map() {
    let gates: Vec<_> = (0..34150)
        .map(|i| Gate::Cx {
            control: i % 25,
            target: (i + 12) % 25,
        })
        .collect();
    let circuit = CompiledCircuit::new(25, &gates).unwrap();
    assert!(circuit.gates().len() < 1000);
    assert_eq!(map(25, &gates), map(25, circuit.gates()));
}
