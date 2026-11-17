use crate::{Amplitude, CompiledCircuit, Gate, SimError, TypedStateVector, error, validate};
use rayon::prelude::*;

pub struct CpuExecutor {
    pool: Option<rayon::ThreadPool>,
    threads: usize,
    threshold: usize,
}
impl CpuExecutor {
    pub fn new(threads: usize) -> Result<Self, SimError> {
        if threads == 0 {
            return Err(error("threads must be positive"));
        }
        let pool = if threads == 1 {
            None
        } else {
            Some(
                rayon::ThreadPoolBuilder::new()
                    .num_threads(threads)
                    .build()
                    .map_err(|e| error(e.to_string()))?,
            )
        };
        Ok(Self {
            pool,
            threads,
            threshold: 65536,
        })
    }
    pub fn threads(&self) -> usize {
        self.threads
    }
    pub fn parallel_threshold(&self) -> usize {
        self.threshold
    }
    pub fn with_parallel_threshold(mut self, threshold: usize) -> Self {
        self.threshold = threshold;
        self
    }
    pub fn run<A: Amplitude>(
        &self,
        state: &mut TypedStateVector<A>,
        gates: &[Gate],
    ) -> Result<(), SimError> {
        validate::<A>(state.n, gates)?;
        self.execute(state, |state, parallel| {
            for gate in gates {
                apply(&mut state.data, gate, parallel);
            }
        });
        Ok(())
    }
    pub fn run_compiled<A: Amplitude>(
        &self,
        state: &mut TypedStateVector<A>,
        circuit: &CompiledCircuit,
    ) -> Result<(), SimError> {
        if state.n != circuit.n {
            return Err(error("compiled circuit register mismatch"));
        }
        validate::<A>(state.n, &circuit.source)?;
        self.execute(state, |state, parallel| {
            for op in &circuit.operations {
                op.apply(&mut state.data, parallel);
            }
        });
        Ok(())
    }
    fn execute<A: Amplitude>(
        &self,
        state: &mut TypedStateVector<A>,
        f: impl FnOnce(&mut TypedStateVector<A>, bool) + Send,
    ) {
        if let Some(pool) = &self.pool
            && state.data.len() >= self.threshold
        {
            pool.install(|| f(state, true));
        } else {
            f(state, false);
        }
    }
}

// Each callback owns an entire pair; no aliasing or state-size scratch buffer.
pub(crate) fn pairs<A: Amplitude, F: Fn(usize, &mut A, &mut A) + Send + Sync>(
    data: &mut [A],
    target: usize,
    parallel: bool,
    f: F,
) {
    let stride = 1usize << target;
    if !parallel {
        for (block, chunk) in data.chunks_exact_mut(2 * stride).enumerate() {
            let (lo, hi) = chunk.split_at_mut(stride);
            for (j, (a, b)) in lo.iter_mut().zip(hi).enumerate() {
                f(block * 2 * stride + j, a, b);
            }
        }
    } else if stride <= 4096 {
        let grain = (8192usize).max(2 * stride);
        data.par_chunks_mut(grain)
            .enumerate()
            .for_each(|(k, chunk)| {
                for (block, c) in chunk.chunks_exact_mut(2 * stride).enumerate() {
                    let (lo, hi) = c.split_at_mut(stride);
                    for (j, (a, b)) in lo.iter_mut().zip(hi).enumerate() {
                        f(k * grain + block * 2 * stride + j, a, b);
                    }
                }
            });
    } else {
        data.par_chunks_mut(2 * stride)
            .enumerate()
            .for_each(|(block, chunk)| {
                let (lo, hi) = chunk.split_at_mut(stride);
                lo.par_chunks_mut(4096)
                    .zip(hi.par_chunks_mut(4096))
                    .enumerate()
                    .for_each(|(k, (lo, hi))| {
                        for (j, (a, b)) in lo.iter_mut().zip(hi).enumerate() {
                            f(block * 2 * stride + k * 4096 + j, a, b);
                        }
                    });
            });
    }
}
// Skip control-zero regions altogether while retaining disjoint ownership.
fn controlled_pairs<A: Amplitude, F: Fn(&mut A, &mut A) + Send + Sync>(
    data: &mut [A],
    target: usize,
    control: usize,
    parallel: bool,
    f: F,
) {
    let control_stride = 1usize << control;
    if control > target {
        let block = |chunk: &mut [A]| {
            pairs(
                &mut chunk[control_stride..],
                target,
                parallel && control_stride > 4096,
                |_, a, b| f(a, b),
            );
        };
        if parallel && control_stride <= 4096 {
            data.par_chunks_mut(8192).for_each(|chunk| {
                chunk.chunks_mut(2 * control_stride).for_each(&block);
            });
        } else if parallel {
            data.par_chunks_mut(2 * control_stride).for_each(block);
        } else {
            data.chunks_mut(2 * control_stride).for_each(block);
        }
    } else {
        let target_stride = 1usize << target;
        let block = |chunk: &mut [A]| {
            let (lo, hi) = chunk.split_at_mut(target_stride);
            let grain = 8192usize.max(2 * control_stride);
            let active = |(lo, hi): (&mut [A], &mut [A])| {
                for (lo, hi) in lo
                    .chunks_mut(2 * control_stride)
                    .zip(hi.chunks_mut(2 * control_stride))
                {
                    for (a, b) in lo[control_stride..]
                        .iter_mut()
                        .zip(&mut hi[control_stride..])
                    {
                        f(a, b);
                    }
                }
            };
            if parallel && target_stride > grain {
                lo.par_chunks_mut(grain)
                    .zip(hi.par_chunks_mut(grain))
                    .for_each(active);
            } else {
                active((lo, hi));
            }
        };
        if parallel && target_stride <= 4096 {
            data.par_chunks_mut(8192).for_each(|chunk| {
                chunk.chunks_mut(2 * target_stride).for_each(&block);
            });
        } else if parallel {
            data.par_chunks_mut(2 * target_stride).for_each(block);
        } else {
            data.chunks_mut(2 * target_stride).for_each(block);
        }
    }
}
pub(crate) fn apply<A: Amplitude>(data: &mut [A], gate: &Gate, parallel: bool) {
    let t = gate.target();
    match *gate {
        Gate::X { .. } => pairs(data, t, parallel, |_, a, b| std::mem::swap(a, b)),
        Gate::Cx { control, .. } => controlled_pairs(data, t, control, parallel, std::mem::swap),
        Gate::Z { .. } => pairs(data, t, parallel, |_, _, b| *b = b.negated()),
        Gate::Cz { control, .. } => {
            controlled_pairs(data, t, control, parallel, |_, b| *b = b.negated())
        }
        Gate::S { .. } => pairs(data, t, parallel, |_, _, b| *b = b.times_i()),
        _ => {
            let m = gate.matrix().map(|row| row.map(A::coefficient));
            if matches!(gate, Gate::T { .. }) {
                pairs(data, t, parallel, |_, _, b| {
                    *b = A::store(m[1][1] * b.load())
                });
            } else if matches!(gate, Gate::Rz { .. }) {
                pairs(data, t, parallel, |_, a, b| {
                    let x = a.load();
                    let y = b.load();
                    *a = A::store(m[0][0] * x);
                    *b = A::store(m[1][1] * y);
                });
            } else {
                pairs(data, t, parallel, |_, a, b| {
                    let x = a.load();
                    let y = b.load();
                    *a = A::store(m[0][0] * x + m[0][1] * y);
                    *b = A::store(m[1][0] * x + m[1][1] * y);
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Complex64;

    #[test]
    fn controlled_traversal_covers_active_pairs_across_chunk_boundaries() {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(4)
            .build()
            .unwrap();
        let original: Vec<_> = (0..1usize << 17)
            .map(|i| Complex64::new(i as f64, -(i as f64)))
            .collect();
        for target in [0, 1, 11, 12, 13, 16] {
            for control in [0, 1, 11, 12, 13, 16] {
                if control == target {
                    continue;
                }
                for parallel in [false, true] {
                    let mut actual = original.clone();
                    pool.install(|| {
                        controlled_pairs(&mut actual, target, control, parallel, std::mem::swap)
                    });
                    for (index, amplitude) in actual.iter().enumerate() {
                        let source = if index & (1 << control) != 0 {
                            index ^ (1 << target)
                        } else {
                            index
                        };
                        assert_eq!(
                            *amplitude, original[source],
                            "target={target}, control={control}, parallel={parallel}, index={index}"
                        );
                    }
                }
            }
        }
    }
}
