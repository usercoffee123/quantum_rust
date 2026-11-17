use qiskit_rust::{
    Amplitude, Complex16, Complex32, Complex64, Gate, Precision, StateVector, StateVector16,
    StateVector32, TypedStateVector,
};

fn compare<A: Amplitude>(amplitude_tolerance: f64, norm_tolerance: f64) {
    let gates: Vec<_> = (0..100)
        .flat_map(|i| {
            [
                Gate::H { target: i % 4 },
                Gate::Rx {
                    target: (i + 1) % 4,
                    theta: 0.371,
                },
                Gate::Ry {
                    target: i % 4,
                    theta: -0.831,
                },
                Gate::Rz {
                    target: (i + 2) % 4,
                    theta: 1.283,
                },
                Gate::Cx {
                    control: i % 4,
                    target: (i + 1) % 4,
                },
                Gate::Cz {
                    control: i % 4,
                    target: (i + 3) % 4,
                },
            ]
        })
        .collect();
    let mut reference = StateVector::new(4).unwrap();
    reference.apply_all(&gates).unwrap();
    let mut state = TypedStateVector::<A>::new(4).unwrap();
    state.apply_all(&gates).unwrap();
    for (a, b) in state.amplitudes().iter().zip(reference.amplitudes()) {
        assert!((a.as_complex64() - b).norm() < amplitude_tolerance);
    }
    assert!((state.norm_sqr() - 1.0).abs() < norm_tolerance);
}

#[test]
fn all_precisions_execute_entangling_circuits() {
    compare::<Complex64>(1e-12, 1e-12);
    compare::<Complex32>(1e-5, 1e-5);
    compare::<Complex16>(0.02, 0.03);
}

#[test]
fn storage_and_memory_budget_follow_selected_precision() {
    assert_eq!(size_of::<Complex16>(), 4);
    assert_eq!(size_of::<Complex32>(), 8);
    assert_eq!(size_of::<Complex64>(), 16);
    assert_eq!(StateVector16::new(4).unwrap().state_bytes(), 64);
    assert_eq!(StateVector32::new(4).unwrap().state_bytes(), 128);
    assert_eq!(StateVector::new(4).unwrap().state_bytes(), 256);
    assert!(StateVector16::with_memory_limit(4, 64).is_ok());
    assert!(StateVector32::with_memory_limit(4, 64).is_err());
    assert!(StateVector::with_memory_limit(4, 64).is_err());
    assert_eq!(StateVector16::new(1).unwrap().precision(), Precision::F16);
    assert_eq!(StateVector32::new(1).unwrap().precision(), Precision::F32);
    assert_eq!(StateVector::new(1).unwrap().precision(), Precision::F64);
}

#[test]
fn fp16_stores_half_rounding_and_subnormal_underflow() {
    let mut state = StateVector16::new(1).unwrap();
    state.apply(&Gate::H { target: 0 }).unwrap();
    let h = qiskit_rust::precision::f16::from_f32(std::f32::consts::FRAC_1_SQRT_2);
    assert_eq!(state.amplitudes()[0].re, h);
    assert_eq!(state.amplitudes()[1].re, h);
    assert!((state.norm_sqr() - 1.0).abs() > 1e-5); // Never silently renormalized.
    for (power, expected) in [(24, 2f64.powi(-24)), (26, 0.0)] {
        let mut state = StateVector16::new(1).unwrap();
        state
            .apply(&Gate::Ry {
                target: 0,
                theta: 2.0 * 2f64.powi(-power),
            })
            .unwrap();
        assert_eq!(state.amplitudes()[1].as_complex64().re, expected);
    }
}

#[test]
fn fp32_compute_does_not_silently_use_fp64() {
    // A finite non-unitary test matrix exposes coefficient rounding directly.
    let matrix = [
        [
            Complex64::new(1.0 + 2f64.powi(-30), 0.0),
            Complex64::new(0.0, 0.0),
        ],
        [Complex64::new(0.0, 0.0), Complex64::new(1.0, 0.0)],
    ];
    let gate = Gate::U { target: 0, matrix };
    let mut a = StateVector::new(1).unwrap();
    let mut b = StateVector32::new(1).unwrap();
    a.apply(&gate).unwrap();
    b.apply(&gate).unwrap();
    assert!(a.amplitudes()[0].re > 1.0);
    assert_eq!(b.amplitudes()[0].re, 1.0);
    let huge = Gate::U {
        target: 0,
        matrix: [[Complex64::new(1e100, 0.0); 2]; 2],
    };
    assert!(b.apply(&huge).is_err());
    assert!(StateVector16::new(1).unwrap().apply(&huge).is_err());
}
