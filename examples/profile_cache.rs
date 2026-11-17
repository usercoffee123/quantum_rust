//! Repeated CX gates on a pre-touched state; reports timings, not hardware counters.
use qiskit_rust::{CpuExecutor, Gate, StateVector64};
use serde_json::json;
use std::{hint::black_box, time::Instant};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let stride_profile = std::env::args().any(|arg| arg == "--stride-profile");
    let sizes = if stride_profile {
        vec![25]
    } else {
        vec![10, 12, 14, 16, 18, 20, 22, 24, 25]
    };
    let workers = if stride_profile { vec![4] } else { vec![1, 4] };
    for n in sizes {
        for &threads in &workers {
            let executor = CpuExecutor::new(threads)?.with_parallel_threshold(0);
            let mut state = StateVector64::with_memory_limit(n, 1 << 30)?;
            // Touch the full state before measuring; avoid lazy first-touch costs.
            executor.run(&mut state, &[Gate::H { target: 0 }])?;
            let targets = if stride_profile {
                (0..n).collect::<Vec<_>>()
            } else {
                vec![0, n / 2, n - 1]
            };
            for target in targets {
                let control = if stride_profile {
                    (target + n - 12) % n
                } else {
                    (target + n / 2) % n
                };
                let repetitions = ((256 << 20) / state.state_bytes()).clamp(16, 1024);
                let gates = vec![Gate::Cx { control, target }; repetitions];
                executor.run(&mut state, &gates)?;
                let mut samples = Vec::new();
                for _ in 0..3 {
                    let start = Instant::now();
                    executor.run(black_box(&mut state), black_box(&gates))?;
                    samples.push(start.elapsed().as_secs_f64());
                    black_box(state.amplitudes());
                }
                samples.sort_by(f64::total_cmp);
                println!(
                    "{}",
                    json!({"qubits":n,"threads":threads,"target":target,
                    "control":control,"state_bytes":state.state_bytes(),
                    "gates_per_trial":repetitions,"samples_seconds":samples,
                    "median_seconds_per_gate":samples[1]/repetitions as f64,
                    "logical_gib_per_second":state.state_bytes() as f64 * repetitions as f64 / samples[1] / (1u64<<30) as f64})
                );
            }
        }
    }
    Ok(())
}
