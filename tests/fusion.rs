use qiskit_rust::{
    Amplitude, CompileOptions, CompiledCircuit, Complex16, Complex32, Complex64, CpuExecutor, Gate,
    TypedStateVector,
};

fn compile(n: usize, gates: &[Gate]) -> CompiledCircuit {
    CompiledCircuit::with_options(
        n,
        gates,
        CompileOptions {
            optimize_cx: false,
            fusion: true,
        },
    )
    .unwrap()
}

fn compare<A: Amplitude>(n: usize, gates: &[Gate], tolerance: f64) {
    let circuit = compile(n, gates);
    assert!(circuit.stats().fused_blocks > 0);
    assert!(circuit.stats().execution_operations < gates.len());
    let mut reference = TypedStateVector::<A>::new(n).unwrap();
    reference.apply_all(gates).unwrap();
    for threads in [1, 4] {
        let executor = CpuExecutor::new(threads)
            .unwrap()
            .with_parallel_threshold(0);
        let mut state = TypedStateVector::<A>::new(n).unwrap();
        executor.run_compiled(&mut state, &circuit).unwrap();
        let error = state
            .amplitudes()
            .iter()
            .zip(reference.amplitudes())
            .map(|(a, b)| (a.as_complex64() - b.as_complex64()).norm())
            .fold(0.0, f64::max);
        assert!(
            error < tolerance,
            "n={n}, threads={threads}, precision={:?}, error={error}",
            A::PRECISION
        );
        assert!((state.norm_sqr() - reference.norm_sqr()).abs() < 10.0 * tolerance);
    }
}

fn pair_gates(a: usize, b: usize) -> Vec<Gate> {
    vec![
        Gate::H { target: a },
        Gate::Ry {
            target: b,
            theta: 0.31,
        },
        Gate::Cx {
            control: b,
            target: a,
        },
        Gate::Rz {
            target: a,
            theta: -0.83,
        },
        Gate::S { target: b },
        Gate::T { target: a },
        Gate::Cz {
            control: a,
            target: b,
        },
        Gate::Rx {
            target: b,
            theta: 0.73,
        },
        Gate::Y { target: a },
        Gate::Cx {
            control: a,
            target: b,
        },
        Gate::Z { target: b },
        Gate::X { target: a },
    ]
}

#[test]
fn fused_pairs_cover_bit_orders_strides_and_precisions() {
    // Includes small stripes, within-chunk splits, large strides and highest bit.
    for (n, a, b) in [(2, 0, 1), (5, 4, 0), (14, 0, 13), (14, 13, 12), (14, 2, 9)] {
        let mut gates: Vec<_> = (0..n)
            .map(|target| Gate::Ry {
                target,
                theta: 0.51 + target as f64 * 0.03,
            })
            .collect();
        gates.extend(pair_gates(a, b));
        compare::<Complex64>(n, &gates, 1e-12);
        compare::<Complex32>(n, &gates, 3e-6);
        compare::<Complex16>(n, &gates, 3e-3);
    }
}

#[test]
fn one_qubit_composition_preserves_phase_and_nonunitary_matrices() {
    let gates = vec![
        Gate::H { target: 0 },
        Gate::Rz {
            target: 0,
            theta: 0.82,
        },
        Gate::U {
            target: 0,
            matrix: [
                [Complex64::new(0.0, 0.5), Complex64::new(0.2, -0.1)],
                [Complex64::new(-0.3, 0.4), Complex64::new(0.7, 0.0)],
            ],
        },
        Gate::Ry {
            target: 0,
            theta: -1.1,
        },
    ];
    for target in [0, 13] {
        let mut shifted = gates.clone();
        for gate in &mut shifted {
            match gate {
                Gate::H { target: t }
                | Gate::Rz { target: t, .. }
                | Gate::U { target: t, .. }
                | Gate::Ry { target: t, .. } => *t = target,
                _ => unreachable!(),
            }
        }
        compare::<Complex64>(target + 1, &shifted, 1e-12);
        compare::<Complex32>(target + 1, &shifted, 3e-6);
        compare::<Complex16>(target + 1, &shifted, 3e-3);
    }
}

#[test]
fn mixed_blocks_and_dense_state_match_unfused_execution() {
    let mut gates: Vec<_> = (0..6)
        .map(|target| Gate::Ry {
            target,
            theta: 0.6 + target as f64 * 0.1,
        })
        .collect();
    for i in 0..20 {
        gates.extend(pair_gates(i % 6, (i + 1 + i / 6) % 6));
    }
    compare::<Complex64>(6, &gates, 1e-12);
    compare::<Complex32>(6, &gates, 1e-5);
    compare::<Complex16>(6, &gates, 2e-2);
}

#[test]
fn sparse_blocks_remain_specialized_and_invalid_source_is_rejected() {
    let pure = vec![
        Gate::Cx {
            control: 0,
            target: 1
        };
        100
    ];
    assert_eq!(compile(2, &pure).stats().fused_blocks, 0);
    let diagonal = vec![
        Gate::Rz {
            target: 0,
            theta: 0.2,
        },
        Gate::Cz {
            control: 0,
            target: 1,
        },
        Gate::T { target: 1 },
        Gate::Z { target: 0 },
    ];
    assert_eq!(compile(2, &diagonal).stats().fused_blocks, 0);
    let identity = CompiledCircuit::with_options(
        2,
        &pure,
        CompileOptions {
            optimize_cx: true,
            fusion: true,
        },
    )
    .unwrap();
    assert_eq!(identity.stats().execution_operations, 0);
    let mut bad = pair_gates(0, 1);
    bad.push(Gate::Rx {
        target: 2,
        theta: 0.2,
    });
    assert!(
        CompiledCircuit::with_options(
            2,
            &bad,
            CompileOptions {
                optimize_cx: false,
                fusion: true
            }
        )
        .is_err()
    );
    // Overflow in composed FP64 coefficients leaves source gates intact.
    let huge = Gate::U {
        target: 0,
        matrix: [
            [Complex64::new(1e300, 0.0), Complex64::new(0.0, 0.0)],
            [Complex64::new(0.0, 0.0), Complex64::new(1e300, 0.0)],
        ],
    };
    assert_eq!(compile(1, &[huge.clone(), huge]).stats().fused_blocks, 0);
}

#[test]
fn selected_precision_validation_precedes_any_mutation() {
    let mut gates = pair_gates(0, 1);
    gates.push(Gate::U {
        target: 0,
        matrix: [
            [Complex64::new(1e100, 0.0), Complex64::new(0.0, 0.0)],
            [Complex64::new(0.0, 0.0), Complex64::new(1.0, 0.0)],
        ],
    });
    let compiled = compile(2, &gates);
    let mut state = TypedStateVector::<Complex32>::new(2).unwrap();
    let before = state.amplitudes().to_vec();
    assert!(
        CpuExecutor::new(4)
            .unwrap()
            .run_compiled(&mut state, &compiled)
            .is_err()
    );
    assert_eq!(before, state.amplitudes());
}

#[test]
fn composed_coefficient_overflow_falls_back_to_source_at_execution() {
    let gate = Gate::U {
        target: 0,
        matrix: [
            [Complex64::new(1e20, 0.0), Complex64::new(0.0, 0.0)],
            [Complex64::new(0.0, 0.0), Complex64::new(1e20, 0.0)],
        ],
    };
    let gates = vec![gate.clone(), gate];
    let compiled = compile(1, &gates);
    assert_eq!(compiled.stats().fused_blocks, 1);
    let mut reference = TypedStateVector::<Complex32>::new(1).unwrap();
    reference.apply_all(&gates).unwrap();
    let mut actual = TypedStateVector::<Complex32>::new(1).unwrap();
    CpuExecutor::new(4)
        .unwrap()
        .with_parallel_threshold(0)
        .run_compiled(&mut actual, &compiled)
        .unwrap();
    for (a, b) in actual.amplitudes().iter().zip(reference.amplitudes()) {
        assert_eq!(a.re.to_bits(), b.re.to_bits());
        assert_eq!(a.im.to_bits(), b.im.to_bits());
    }
}

#[test]
fn long_blocks_are_bounded_and_reusable() {
    let gates = vec![
        Gate::Ry {
            target: 0,
            theta: 0.07
        };
        70
    ];
    let compiled = compile(1, &gates);
    assert_eq!(compiled.stats().fused_blocks, 3);
    assert_eq!(compiled.stats().fused_source_gates, 70);
    compare::<Complex64>(1, &gates, 1e-12);
}

fn compare_structured_prepared<A: Amplitude>(n: usize, a: usize, b: usize, tolerance: f64) {
    let mut preparation = Vec::new();
    for target in 0..n {
        preparation.push(Gate::Ry {
            target,
            theta: 0.51 + 0.03 * target as f64,
        });
        preparation.push(Gate::Rz {
            target,
            theta: 0.13 * target as f64,
        });
    }
    preparation.push(Gate::Cx {
        control: a,
        target: b,
    });
    for threads in [1, 4] {
        let executor = CpuExecutor::new(threads)
            .unwrap()
            .with_parallel_threshold(0);
        let mut reference = TypedStateVector::<A>::new(n).unwrap();
        reference.apply_all(&preparation).unwrap();
        let mut actual = TypedStateVector::<A>::new(n).unwrap();
        actual.apply_all(&preparation).unwrap();
        for i in 0..12 {
            let angle = 0.17 + i as f64 * 0.29;
            let mut gates = vec![
                Gate::Ry {
                    target: a,
                    theta: angle,
                },
                Gate::Rz {
                    target: a,
                    theta: -0.7 * angle,
                },
                Gate::Ry {
                    target: b,
                    theta: -1.1 * angle,
                },
                Gate::Rz {
                    target: b,
                    theta: 0.9 * angle,
                },
            ];
            if i % 2 == 0 {
                gates.swap(1, 2);
            }
            let circuit = compile(n, &gates);
            assert_eq!(circuit.stats().structured_fused_blocks, 1);
            reference.apply_all(&gates).unwrap();
            executor.run_compiled(&mut actual, &circuit).unwrap();
            // Entangle between blocks; the fast kernel must accept arbitrary input.
            let cx = Gate::Cx {
                control: b,
                target: a,
            };
            reference.apply_all(std::slice::from_ref(&cx)).unwrap();
            actual.apply_all(std::slice::from_ref(&cx)).unwrap();
        }
        let error = actual
            .amplitudes()
            .iter()
            .zip(reference.amplitudes())
            .map(|(a, b)| (a.as_complex64() - b.as_complex64()).norm())
            .fold(0.0, f64::max);
        assert!(error < tolerance, "n={n}, threads={threads}, error={error}");
        assert!((actual.norm_sqr() - reference.norm_sqr()).abs() < 10.0 * tolerance);
    }
}

#[test]
fn structured_ry_rz_matches_unfused_across_strides_and_precisions() {
    for (n, a, b) in [(2, 0, 1), (5, 4, 0), (14, 0, 13), (14, 13, 12), (14, 2, 9)] {
        compare_structured_prepared::<Complex64>(n, a, b, 2e-12);
        compare_structured_prepared::<Complex32>(n, a, b, 1e-5);
        compare_structured_prepared::<Complex16>(n, a, b, 3e-2);
    }
}

#[test]
fn structured_recognition_preserves_gate_order_and_dense_fallback() {
    let eligible = vec![
        Gate::Ry {
            target: 0,
            theta: 0.41,
        },
        Gate::Rz {
            target: 0,
            theta: 0.32,
        },
        Gate::Ry {
            target: 1,
            theta: -0.71,
        },
        Gate::Rz {
            target: 1,
            theta: -0.29,
        },
    ];
    for mode in 0..4 {
        let mut gates = eligible.clone();
        match mode {
            0 => gates.swap(0, 1), // Rz before Ry must remain dense.
            1 => {
                gates[3] = Gate::Cx {
                    control: 0,
                    target: 1,
                }
            }
            2 => {
                gates[0] = Gate::Rx {
                    target: 0,
                    theta: 0.41,
                }
            }
            _ => {
                gates[3] = Gate::Ry {
                    target: 1,
                    theta: 0.29,
                }
            }
        }
        assert_eq!(compile(2, &gates).stats().structured_fused_blocks, 0);
        compare::<Complex64>(2, &gates, 1e-12);
    }
}
