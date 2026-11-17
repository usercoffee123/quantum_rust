use crate::fusion::{Operation, collect};
use crate::{Complex64, Gate, SimError, error};
use serde::Serialize;

#[derive(Clone, Copy, Debug, Default)]
pub struct CompileOptions {
    pub optimize_cx: bool,
    pub fusion: bool,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct CompilationStats {
    pub input_gates: usize,
    pub output_gates: usize,
    pub cx_runs: usize,
    pub compressed_cx_runs: usize,
    pub execution_operations: usize,
    pub fused_blocks: usize,
    pub fused_source_gates: usize,
    pub structured_fused_blocks: usize,
}
pub struct CompiledCircuit {
    pub(crate) n: usize,
    pub(crate) source: Vec<Gate>,
    gates: Vec<Gate>,
    pub(crate) operations: Vec<Operation>,
    stats: CompilationStats,
}
impl CompiledCircuit {
    pub fn new(n: usize, gates: &[Gate]) -> Result<Self, SimError> {
        Self::with_options(
            n,
            gates,
            CompileOptions {
                optimize_cx: true,
                fusion: false,
            },
        )
    }
    pub fn with_options(
        n: usize,
        gates: &[Gate],
        options: CompileOptions,
    ) -> Result<Self, SimError> {
        if n >= usize::BITS as usize {
            return Err(error("register width overflows address space"));
        }
        for g in gates {
            g.validate(n)?;
        }
        let mut stats = CompilationStats {
            input_gates: gates.len(),
            ..Default::default()
        };
        let mut output = Vec::new();
        let mut start = 0;
        while start < gates.len() {
            if !matches!(gates[start], Gate::Cx { .. }) {
                output.push(gates[start].clone());
                start += 1;
                continue;
            }
            let mut end = start + 1;
            while end < gates.len() && matches!(gates[end], Gate::Cx { .. }) {
                end += 1;
            }
            stats.cx_runs += 1;
            let replacement = if options.optimize_cx {
                compress(n, &gates[start..end])
            } else {
                Vec::new()
            };
            if options.optimize_cx && replacement.len() < end - start {
                stats.compressed_cx_runs += 1;
                output.extend(replacement);
            } else {
                output.extend_from_slice(&gates[start..end]);
            }
            start = end;
        }
        stats.output_gates = output.len();
        let operations = if options.fusion {
            collect(&output, &mut stats)
        } else {
            output.iter().cloned().map(Operation::Gate).collect()
        };
        stats.execution_operations = operations.len();
        Ok(Self {
            n,
            source: gates.to_vec(),
            gates: output,
            operations,
            stats,
        })
    }
    pub fn gates(&self) -> &[Gate] {
        &self.gates
    }
    pub fn stats(&self) -> &CompilationStats {
        &self.stats
    }
}
fn compress(n: usize, gates: &[Gate]) -> Vec<Gate> {
    let mut rows: Vec<usize> = (0..n).map(|i| 1 << i).collect();
    for g in gates {
        if let Gate::Cx { control, target } = *g {
            rows[target] ^= rows[control];
        }
    }
    let mut elimination = Vec::new();
    for col in 0..n {
        if rows[col] & (1 << col) == 0 {
            let pivot = (col + 1..n)
                .find(|&r| rows[r] & (1 << col) != 0)
                .expect("CX map is invertible");
            for (control, target) in [(pivot, col), (col, pivot), (pivot, col)] {
                rows[target] ^= rows[control];
                elimination.push(Gate::Cx { control, target });
            }
        }
        for r in 0..n {
            if r != col && rows[r] & (1 << col) != 0 {
                rows[r] ^= rows[col];
                elimination.push(Gate::Cx {
                    control: col,
                    target: r,
                });
            }
        }
    }
    elimination.reverse();
    elimination
}
pub(crate) fn finite(z: Complex64) -> bool {
    z.re.is_finite() && z.im.is_finite()
}
