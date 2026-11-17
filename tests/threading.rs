use qiskit_rust::{
    Amplitude, Complex16, Complex32, Complex64, CpuExecutor, Gate, TypedStateVector,
};

fn check<A: Amplitude>() {
    let executors: Vec<_> = [2, 4]
        .into_iter()
        .map(|n| CpuExecutor::new(n).unwrap().with_parallel_threshold(0))
        .collect();
    for n in [1, 5, 14] {
        let mut gates = Vec::new();
        for target in 0..n {
            gates.extend([
                Gate::Ry {
                    target,
                    theta: 0.43,
                },
                Gate::Rz {
                    target,
                    theta: -0.71,
                },
            ]);
        }
        for target in 0..n {
            gates.extend([
                Gate::H { target },
                Gate::X { target },
                Gate::Y { target },
                Gate::Z { target },
                Gate::S { target },
                Gate::T { target },
                Gate::Rx {
                    target,
                    theta: 0.91,
                },
                Gate::Ry {
                    target,
                    theta: -1.37,
                },
                Gate::Rz {
                    target,
                    theta: 0.19,
                },
                Gate::U {
                    target,
                    matrix: [
                        [Complex64::new(0.6, 0.0), Complex64::new(0.0, 0.8)],
                        [Complex64::new(0.0, 0.8), Complex64::new(0.6, 0.0)],
                    ],
                },
            ]);
            for control in 0..n {
                if control != target {
                    gates.extend([Gate::Cx { control, target }, Gate::Cz { control, target }]);
                }
            }
        }
        let mut reference = TypedStateVector::<A>::new(n).unwrap();
        reference.apply_all(&gates).unwrap();
        for executor in &executors {
            let mut state = TypedStateVector::<A>::new(n).unwrap();
            executor.run(&mut state, &gates).unwrap();
            assert_eq!(
                state
                    .amplitudes()
                    .iter()
                    .map(|a| a.as_complex64())
                    .collect::<Vec<_>>(),
                reference
                    .amplitudes()
                    .iter()
                    .map(|a| a.as_complex64())
                    .collect::<Vec<_>>(),
                "precision {:?}, qubits {n}, threads {}",
                state.precision(),
                executor.threads()
            );
        }
    }
}

#[test]
fn threaded_matches_serial_in_every_precision_and_bit_order() {
    check::<Complex64>();
    check::<Complex32>();
    check::<Complex16>();
}

#[test]
fn defaults_and_threshold_fallback_preserve_results() {
    assert!(CpuExecutor::new(0).is_err());
    let serial = CpuExecutor::new(1).unwrap();
    let parallel = CpuExecutor::new(3).unwrap();
    assert_eq!(serial.threads(), 1);
    assert_eq!(parallel.threads(), 3);
    for n in [3, 17] {
        // Below and above the default parallel threshold.
        let gates: Vec<_> = (0..n)
            .map(|target| Gate::H { target })
            .chain([
                Gate::Ry {
                    target: n - 1,
                    theta: 0.71,
                },
                Gate::Rz {
                    target: n / 2,
                    theta: -1.23,
                },
                Gate::Cx {
                    control: 0,
                    target: n - 1,
                },
                Gate::Cx {
                    control: n - 1,
                    target: 0,
                },
                Gate::Cz {
                    control: n - 1,
                    target: n / 2,
                },
            ])
            .collect();
        let mut a = TypedStateVector::<Complex64>::new(n).unwrap();
        let mut b = TypedStateVector::<Complex64>::new(n).unwrap();
        serial.run(&mut a, &gates).unwrap();
        parallel.run(&mut b, &gates).unwrap();
        assert_eq!(a.amplitudes(), b.amplitudes());
    }
}

#[test]
fn invalid_parallel_batch_does_not_mutate_state() {
    let executor = CpuExecutor::new(2).unwrap().with_parallel_threshold(0);
    let mut state = TypedStateVector::<Complex32>::new(3).unwrap();
    let before = state.amplitudes().to_vec();
    assert!(
        executor
            .run(
                &mut state,
                &[
                    Gate::H { target: 0 },
                    Gate::Cx {
                        control: 2,
                        target: 2
                    }
                ]
            )
            .is_err()
    );
    assert_eq!(state.amplitudes(), before);
}
