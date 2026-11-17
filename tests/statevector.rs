use qiskit_rust::{Complex64 as C, Gate, StateVector};

fn assert_close(actual: &[C], expected: &[C]) {
    assert_eq!(actual.len(), expected.len());
    for (index, (a, b)) in actual.iter().zip(expected).enumerate() {
        assert!((*a - *b).norm() < 1e-12, "index {index}: {a} != {b}");
    }
}

#[test]
fn zero_state_and_zero_qubits() {
    for n in 0..6 {
        let state = StateVector::new(n).unwrap();
        assert_eq!(state.num_qubits(), n);
        assert_eq!(state.amplitudes().len(), 1 << n);
        assert_eq!(state.amplitudes()[0], C::new(1.0, 0.0));
        assert_eq!(state.norm_sqr(), 1.0);
        assert_eq!(state.probabilities().iter().sum::<f64>(), 1.0);
    }
}

#[test]
fn large_state_norm_does_not_accumulate_linear_sum_error() {
    // Regression from the 20-qubit mixed benchmark. An ordinary left-to-right
    // FP64 sum drifts beyond 1e-11 even though the unitary state is accurate.
    let mut state = StateVector::new(20).unwrap();
    let mut gates: Vec<_> = (0..20).map(|target| Gate::H { target }).collect();
    for i in 0..32 {
        let theta = 0.123 + i as f64 * 0.017;
        gates.push(if i % 4 == 3 {
            if i % 8 == 3 {
                Gate::Cx {
                    control: 0,
                    target: 19,
                }
            } else {
                Gate::Cz {
                    control: 0,
                    target: 19,
                }
            }
        } else if i % 2 == 0 {
            Gate::Ry { target: 19, theta }
        } else {
            Gate::Rz { target: 19, theta }
        });
    }
    state.apply_all(&gates).unwrap();
    assert!(
        (state.norm_sqr() - 1.0).abs() < 1e-13,
        "norm = {}",
        state.norm_sqr()
    );
}

#[test]
fn bell_and_ghz() {
    let h = std::f64::consts::FRAC_1_SQRT_2;
    for n in 2..6 {
        let mut state = StateVector::new(n).unwrap();
        state.apply(&Gate::H { target: 0 }).unwrap();
        for target in 1..n {
            state.apply(&Gate::Cx { control: 0, target }).unwrap();
        }
        let mut expected = vec![C::new(0.0, 0.0); 1 << n];
        expected[0] = C::new(h, 0.0);
        expected[(1 << n) - 1] = C::new(h, 0.0);
        assert_close(state.amplitudes(), &expected);
    }
}

#[test]
fn controlled_gates_all_bit_orders_and_basis_inputs() {
    for control in 0..4 {
        for target in 0..4 {
            if control == target {
                continue;
            }
            for basis in 0usize..16 {
                for is_cx in [true, false] {
                    let mut state = StateVector::new(4).unwrap();
                    for bit in 0..4 {
                        if basis & (1 << bit) != 0 {
                            state.apply(&Gate::X { target: bit }).unwrap();
                        }
                    }
                    let gate = if is_cx {
                        Gate::Cx { control, target }
                    } else {
                        Gate::Cz { control, target }
                    };
                    state.apply(&gate).unwrap();
                    let mut expected = vec![C::new(0.0, 0.0); 16];
                    let enabled = basis & (1 << control) != 0;
                    let out = if is_cx && enabled {
                        basis ^ (1 << target)
                    } else {
                        basis
                    };
                    let sign = if !is_cx && enabled && basis & (1 << target) != 0 {
                        -1.0
                    } else {
                        1.0
                    };
                    expected[out] = C::new(sign, 0.0);
                    assert_close(state.amplitudes(), &expected);
                }
            }
        }
    }
}

#[test]
fn arbitrary_matrix_against_independent_dense_embedding() {
    let u = [
        [C::new(0.3, 0.1), C::new(-0.7, 0.2)],
        [C::new(0.2, -0.4), C::new(0.1, 0.8)],
    ];
    for target in 0..5 {
        let mut state = StateVector::new(5).unwrap();
        for q in 0..5 {
            state
                .apply(&Gate::Ry {
                    target: q,
                    theta: 0.31 * (q + 1) as f64,
                })
                .unwrap();
            state
                .apply(&Gate::Rz {
                    target: q,
                    theta: -0.43 * q as f64,
                })
                .unwrap();
        }
        let before = state.amplitudes().to_vec();
        // Explicit full matrix-vector multiplication, intentionally O(4^n).
        let mut expected = vec![C::new(0.0, 0.0); before.len()];
        for (row, out) in expected.iter_mut().enumerate() {
            for (col, input) in before.iter().enumerate() {
                if row & !(1 << target) == col & !(1 << target) {
                    *out += u[(row >> target) & 1][(col >> target) & 1] * input;
                }
            }
        }
        state.apply(&Gate::U { target, matrix: u }).unwrap();
        assert_close(state.amplitudes(), &expected);
    }
}

#[test]
fn rotations_and_inverses_preserve_state() {
    for target in 0..4 {
        let mut state = StateVector::new(4).unwrap();
        state
            .apply_all(&[
                Gate::H { target },
                Gate::T { target },
                Gate::Cx {
                    control: target,
                    target: (target + 1) % 4,
                },
            ])
            .unwrap();
        let before = state.amplitudes().to_vec();
        state
            .apply_all(&[
                Gate::Rx {
                    target,
                    theta: 0.73,
                },
                Gate::Ry {
                    target,
                    theta: -1.23,
                },
                Gate::Rz {
                    target,
                    theta: 2.01,
                },
                Gate::Rz {
                    target,
                    theta: -2.01,
                },
                Gate::Ry {
                    target,
                    theta: 1.23,
                },
                Gate::Rx {
                    target,
                    theta: -0.73,
                },
            ])
            .unwrap();
        assert_close(state.amplitudes(), &before);
        assert!((state.norm_sqr() - 1.0).abs() < 1e-12);
    }
}

#[test]
fn invalid_inputs_fail_before_mutation() {
    let mut state = StateVector::new(2).unwrap();
    let before = state.amplitudes().to_vec();
    for gate in [
        Gate::X { target: 2 },
        Gate::Cx {
            control: 0,
            target: 0,
        },
        Gate::Cz {
            control: 2,
            target: 0,
        },
        Gate::Rx {
            target: 0,
            theta: f64::NAN,
        },
        Gate::Rz {
            target: 0,
            theta: f64::INFINITY,
        },
        Gate::U {
            target: 0,
            matrix: [[C::new(f64::NAN, 0.0); 2]; 2],
        },
    ] {
        assert!(state.apply_all(&[Gate::H { target: 0 }, gate]).is_err());
        assert_eq!(state.amplitudes(), before);
    }
    assert!(StateVector::new(usize::MAX).is_err());
    assert!(StateVector::with_memory_limit(usize::BITS as usize, usize::MAX).is_err());
    assert!(StateVector::with_memory_limit(10, 1024).is_err());
    assert!(StateVector::with_memory_limit(0, 15).is_err());
}
