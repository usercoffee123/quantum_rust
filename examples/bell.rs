use qiskit_rust::{Gate, StateVector};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut state = StateVector::new(2)?;
    state.apply_all(&[
        Gate::H { target: 0 },
        Gate::Cx {
            control: 0,
            target: 1,
        },
    ])?;
    println!("amplitudes: {:?}", state.amplitudes());
    println!("probabilities: {:?}", state.probabilities());
    Ok(())
}
