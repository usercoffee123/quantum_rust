use qiskit_rust::{
    Amplitude, CompileOptions, CompiledCircuit, Complex16, Complex32, Complex64, CpuExecutor, Gate,
    Precision, SimError, TypedStateVector,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    io::{self, BufRead, BufWriter, Write},
    path::PathBuf,
    time::Instant,
};

fn yes() -> bool {
    true
}
fn one() -> usize {
    1
}
fn threshold() -> usize {
    65536
}
fn budget() -> usize {
    256 << 20
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Benchmark {
    repetitions: usize,
    #[serde(default)]
    warmup: usize,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    num_qubits: usize,
    gates: Vec<Gate>,
    #[serde(default)]
    precision: Precision,
    #[serde(default = "one")]
    threads: usize,
    #[serde(default = "threshold")]
    parallel_threshold: usize,
    #[serde(default = "budget")]
    memory_limit_bytes: usize,
    #[serde(default = "yes")]
    include_state: bool,
    #[serde(default = "yes")]
    include_norm: bool,
    #[serde(default)]
    probe_indices: Vec<usize>,
    #[serde(default)]
    amplitudes_output: Option<PathBuf>,
    #[serde(default)]
    optimize: bool,
    #[serde(default)]
    fusion: bool,
    #[serde(default)]
    benchmark: Option<Benchmark>,
}
fn run<A: Amplitude>(r: Request) -> Result<Value, Box<dyn std::error::Error>> {
    let executor = CpuExecutor::new(r.threads)?.with_parallel_threshold(r.parallel_threshold);
    let start = Instant::now();
    let compiled = if r.optimize || r.fusion {
        Some(CompiledCircuit::with_options(
            r.num_qubits,
            &r.gates,
            CompileOptions {
                optimize_cx: r.optimize,
                fusion: r.fusion,
            },
        )?)
    } else {
        None
    };
    let compilation_seconds = start.elapsed().as_secs_f64();
    let execute = |s: &mut TypedStateVector<A>| -> Result<(), SimError> {
        if let Some(c) = &compiled {
            executor.run_compiled(s, c)
        } else {
            executor.run(s, &r.gates)
        }
    };
    let mut state = TypedStateVector::<A>::with_memory_limit(r.num_qubits, r.memory_limit_bytes)?;
    if r.probe_indices
        .iter()
        .any(|&i| i >= state.amplitudes().len())
    {
        return Err("probe index outside state".into());
    }
    let mut seconds = Vec::new();
    if let Some(b) = &r.benchmark {
        if b.repetitions == 0 || b.repetitions > 10000 || b.warmup > 10000 {
            return Err("invalid benchmark repetitions/warmup".into());
        }
        execute(&mut state)?; // preflight validates before collecting any timings
        for i in 0..b.warmup + b.repetitions {
            state.reset();
            let start = Instant::now();
            execute(&mut state)?;
            let t = start.elapsed().as_secs_f64();
            if i >= b.warmup {
                seconds.push(t);
            }
        }
    } else {
        let start = Instant::now();
        execute(&mut state)?;
        seconds.push(start.elapsed().as_secs_f64());
    }
    let pair = |a: A| {
        let c = a.as_complex64();
        [c.re, c.im]
    };
    let mut result = json!({"precision":r.precision,"num_qubits":r.num_qubits,"state_bytes":state.state_bytes(),
        "compute_bits":if r.precision==Precision::F64 {64}else{32},"accumulation_bits":64,
        "threads":r.threads,"parallel_threshold":r.parallel_threshold,"seconds":seconds,
        "norm_sqr":if r.include_norm {Some(state.norm_sqr())}else{None},
        "probes":r.probe_indices.iter().map(|&i|pair(state.amplitudes()[i])).collect::<Vec<_>>()});
    if r.include_state {
        result["amplitudes"] = json!(
            state
                .amplitudes()
                .iter()
                .copied()
                .map(pair)
                .collect::<Vec<_>>()
        );
        result["probabilities"] = json!(state.probabilities());
    }
    if let Some(c) = compiled {
        result["compilation"] = json!({"seconds":compilation_seconds,"stats":c.stats()});
    }
    if let Some(path) = r.amplitudes_output {
        let mut file = BufWriter::new(std::fs::File::create(&path)?);
        for a in state.amplitudes() {
            let c = a.as_complex64();
            file.write_all(&c.re.to_le_bytes())?;
            file.write_all(&c.im.to_le_bytes())?;
        }
        file.flush()?;
        result["amplitudes_output"] =
            json!({"path":path,"format":"complex-f64-le","bytes":state.amplitudes().len()*16});
    }
    Ok(result)
}
fn process(line: &str) -> Result<Value, Box<dyn std::error::Error>> {
    let r: Request = serde_json::from_str(line)?;
    match r.precision {
        Precision::F64 => run::<Complex64>(r),
        Precision::F32 => run::<Complex32>(r),
        Precision::F16 => run::<Complex16>(r),
    }
}
fn main() {
    let mut failed = false;
    let mut out = BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let result = match line {
            Ok(line) => process(&line),
            Err(e) => Err(e.into()),
        };
        let value = match result {
            Ok(v) => v,
            Err(e) => {
                failed = true;
                json!({"error":e.to_string()})
            }
        };
        if writeln!(out, "{value}").and_then(|_| out.flush()).is_err() {
            std::process::exit(1);
        }
    }
    if failed {
        std::process::exit(1);
    }
}
