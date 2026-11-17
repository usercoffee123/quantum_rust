use qiskit_rust::{
    Amplitude, Complex16, Complex32, Complex64 as C, CpuExecutor, Gate, TypedStateVector,
};

fn check<A: Amplitude>() {
    let executor = CpuExecutor::new(4).unwrap().with_parallel_threshold(0);
    for n in [1, 5, 14] {
        let preparation: Vec<_> = (0..n)
            .map(|target| Gate::Ry {
                target,
                theta: 0.71 + target as f64 * 0.09,
            })
            .collect();
        for target in 0..n {
            let mut reference = TypedStateVector::<A>::new(n).unwrap();
            let mut serial = TypedStateVector::<A>::new(n).unwrap();
            let mut threaded = TypedStateVector::<A>::new(n).unwrap();
            for state in [&mut reference, &mut serial, &mut threaded] {
                state.apply_all(&preparation).unwrap();
            }
            let z = C::new(0.0, 0.0);
            let one = C::new(1.0, 0.0);
            let mut cases = vec![
                (Gate::Z { target }, [one, -one]),
                (Gate::S { target }, [one, C::new(0.0, 1.0)]),
                (
                    Gate::T { target },
                    [one, C::from_polar(1.0, std::f64::consts::FRAC_PI_4)],
                ),
            ];
            for theta in [-0.0, 1e-12, -0.73, 1e20] {
                cases.push((
                    Gate::Rz { target, theta },
                    [
                        C::from_polar(1.0, -theta / 2.0),
                        C::from_polar(1.0, theta / 2.0),
                    ],
                ));
            }
            for (gate, diagonal) in cases {
                reference
                    .apply(&Gate::U {
                        target,
                        matrix: [[diagonal[0], z], [z, diagonal[1]]],
                    })
                    .unwrap();
                serial.apply(&gate).unwrap();
                executor.run(&mut threaded, &[gate]).unwrap();
                for ((a, b), c) in reference
                    .amplitudes()
                    .iter()
                    .zip(serial.amplitudes())
                    .zip(threaded.amplitudes())
                {
                    assert_eq!(
                        a.as_complex64(),
                        b.as_complex64(),
                        "reference/serial, n={n}, target={target}"
                    );
                    assert_eq!(
                        b.as_complex64(),
                        c.as_complex64(),
                        "serial/threaded, n={n}, target={target}"
                    );
                }
            }
        }
    }
}

#[test]
fn diagonal_kernels_match_general_matrix_in_every_precision() {
    check::<C>();
    check::<Complex32>();
    check::<Complex16>();
}

#[test]
fn half_sign_and_quarter_turn_preserve_storage_bits() {
    use qiskit_rust::precision::f16;
    // Include subnormals and both signs of zero. No FP32 conversion or rounding
    // should be needed for exact sign flips and component permutations.
    for re in [0, 0x8000, 1, 0x8001, 0x3c00, 0xbc00] {
        for im in [0, 0x8000, 1, 0x8001, 0x3800] {
            let a = Complex16 {
                re: f16::from_bits(re),
                im: f16::from_bits(im),
            };
            let neg = a.negated();
            let phase = a.times_i();
            assert_eq!(neg.re.to_bits(), re ^ 0x8000);
            assert_eq!(neg.im.to_bits(), im ^ 0x8000);
            assert_eq!(phase.re.to_bits(), im ^ 0x8000);
            assert_eq!(phase.im.to_bits(), re);
        }
    }
}
