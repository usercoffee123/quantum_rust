use crate::{Amplitude, CompilationStats, Complex64 as C, Gate, execution};
use rayon::prelude::*;

type Matrix = [[C; 4]; 4];
pub(crate) enum Operation {
    Gate(Gate),
    Fused {
        wires: Vec<usize>,
        matrix: Box<Matrix>,
        source: Vec<Gate>,
        structured: Option<Structured>,
    },
}
// Real Ry mixing on each wire followed by the tensor product of Rz phases.
pub(crate) struct Structured {
    rotations: [[C; 2]; 2],
    phases: [C; 4],
}
impl Operation {
    pub(crate) fn apply<A: Amplitude>(&self, data: &mut [A], parallel: bool) {
        match self {
            Self::Gate(g) => execution::apply(data, g, parallel),
            Self::Fused {
                wires,
                matrix,
                source,
                structured,
            } => {
                if matrix.iter().flatten().any(|&v| !A::coefficient_finite(v)) {
                    for g in source {
                        execution::apply(data, g, parallel);
                    }
                    return;
                }
                if wires.len() == 1 {
                    let m = matrix.map(|r| r.map(A::coefficient));
                    execution::pairs(data, wires[0], parallel, |_, a, b| {
                        let x = a.load();
                        let y = b.load();
                        *a = A::store(m[0][0] * x + m[0][1] * y);
                        *b = A::store(m[1][0] * x + m[1][1] * y);
                    });
                } else if let Some(s) = structured {
                    let [[c0, s0], [c1, s1]] = s.rotations.map(|r| r.map(A::coefficient));
                    let ns0 = A::coefficient(-s.rotations[0][1]);
                    let ns1 = A::coefficient(-s.rotations[1][1]);
                    let phases = s.phases.map(A::coefficient);
                    quads(data, wires[0], wires[1], parallel, |a, b, c, d| {
                        let (x, y, z, w) = (a.load(), b.load(), c.load(), d.load());
                        let u = A::real_mix(c0, x, ns0, y);
                        let v = A::real_mix(s0, x, c0, y);
                        let p = A::real_mix(c0, z, ns0, w);
                        let q = A::real_mix(s0, z, c0, w);
                        *a = A::store(phases[0] * A::real_mix(c1, u, ns1, p));
                        *b = A::store(phases[1] * A::real_mix(c1, v, ns1, q));
                        *c = A::store(phases[2] * A::real_mix(s1, u, c1, p));
                        *d = A::store(phases[3] * A::real_mix(s1, v, c1, q));
                    });
                } else {
                    let m = matrix.map(|r| r.map(A::coefficient));
                    quads(data, wires[0], wires[1], parallel, |a, b, c, d| {
                        let x = [a.load(), b.load(), c.load(), d.load()];
                        let y =
                            m.map(|r| ((r[0] * x[0] + r[1] * x[1]) + r[2] * x[2]) + r[3] * x[3]);
                        *a = A::store(y[0]);
                        *b = A::store(y[1]);
                        *c = A::store(y[2]);
                        *d = A::store(y[3]);
                    });
                }
            }
        }
    }
}
fn quads<A: Amplitude, F: Fn(&mut A, &mut A, &mut A, &mut A) + Sync + Send>(
    data: &mut [A],
    low: usize,
    high: usize,
    parallel: bool,
    f: F,
) {
    let lo = 1 << low;
    let hi = 1 << high;
    let block = |chunk: &mut [A]| {
        let (lower, upper) = chunk.split_at_mut(hi);
        let stripe = |(l, h): (&mut [A], &mut [A])| {
            let (a, b) = l.split_at_mut(lo);
            let (c, d) = h.split_at_mut(lo);
            if parallel && lo > 4096 {
                a.par_chunks_mut(4096)
                    .zip(b.par_chunks_mut(4096))
                    .zip(c.par_chunks_mut(4096))
                    .zip(d.par_chunks_mut(4096))
                    .for_each(|(((a, b), c), d)| {
                        for (((a, b), c), d) in a.iter_mut().zip(b).zip(c).zip(d) {
                            f(a, b, c, d);
                        }
                    });
            } else {
                for (((a, b), c), d) in a.iter_mut().zip(b).zip(c).zip(d) {
                    f(a, b, c, d);
                }
            }
        };
        if parallel && hi > 8192 {
            lower
                .par_chunks_mut(2 * lo)
                .zip(upper.par_chunks_mut(2 * lo))
                .with_min_len((4096 / (2 * lo)).max(1))
                .for_each(stripe);
        } else {
            lower
                .chunks_mut(2 * lo)
                .zip(upper.chunks_mut(2 * lo))
                .for_each(stripe);
        }
    };
    if parallel && hi <= 4096 {
        data.par_chunks_mut(8192).for_each(|chunk| {
            chunk.chunks_mut(2 * hi).for_each(&block);
        });
    } else if parallel {
        data.par_chunks_mut(2 * hi).for_each(block);
    } else {
        data.chunks_mut(2 * hi).for_each(block);
    }
}
pub(crate) fn collect(gates: &[Gate], stats: &mut CompilationStats) -> Vec<Operation> {
    let mut ops = Vec::new();
    let mut start = 0;
    while start < gates.len() {
        let mut wires = gates[start].wires();
        let mut end = start + 1;
        while end < gates.len() && end - start < 32 {
            let next = gates[end].wires();
            let mut union = wires.clone();
            for q in next {
                if !union.contains(&q) {
                    union.push(q);
                }
            }
            if union.len() > 2 {
                break;
            }
            wires = union;
            end += 1;
        }
        wires.sort_unstable();
        let source = &gates[start..end];
        let mixing = source.iter().any(|g| {
            matches!(
                g,
                Gate::H { .. } | Gate::Rx { .. } | Gate::Ry { .. } | Gate::U { .. }
            )
        });
        if mixing && source.len() >= if wires.len() == 1 { 2 } else { 4 } {
            let matrix = compose(source, &wires);
            if matrix.iter().flatten().all(|&z| crate::circuit::finite(z)) {
                let structured = recognize(source, &wires);
                stats.fused_blocks += 1;
                stats.fused_source_gates += source.len();
                stats.structured_fused_blocks += usize::from(structured.is_some());
                ops.push(Operation::Fused {
                    wires,
                    matrix: Box::new(matrix),
                    source: source.to_vec(),
                    structured,
                });
            } else {
                ops.extend(source.iter().cloned().map(Operation::Gate));
            }
        } else {
            ops.extend(source.iter().cloned().map(Operation::Gate));
        }
        start = end;
    }
    ops
}
fn compose(gates: &[Gate], wires: &[usize]) -> Matrix {
    let z = C::new(0., 0.);
    let mut m = [[z; 4]; 4];
    let dim = 1 << wires.len();
    for (i, row) in m.iter_mut().enumerate().take(dim) {
        row[i] = C::new(1., 0.);
    }
    for g in gates {
        let target = wires.iter().position(|&q| q == g.target()).unwrap();
        let mask = 1 << target;
        let u = g.matrix();
        for row in 0..dim {
            if row & mask != 0 {
                continue;
            }
            if let Gate::Cx { control, .. } | Gate::Cz { control, .. } = *g {
                let c = wires.iter().position(|&q| q == control).unwrap();
                if row & (1 << c) == 0 {
                    continue;
                }
            }
            let a = m[row];
            let b = m[row | mask];
            for col in 0..dim {
                m[row][col] = u[0][0] * a[col] + u[0][1] * b[col];
                m[row | mask][col] = u[1][0] * a[col] + u[1][1] * b[col];
            }
        }
    }
    m
}
fn recognize(gates: &[Gate], wires: &[usize]) -> Option<Structured> {
    if wires.len() != 2 || gates.len() != 4 {
        return None;
    }
    let mut angles = [[0.; 2]; 2];
    let mut seen = [0; 2];
    for g in gates {
        let q = wires.iter().position(|&q| q == g.target())?;
        match *g {
            Gate::Ry { theta, .. } if seen[q] == 0 => {
                angles[q][0] = theta;
                seen[q] = 1;
            }
            Gate::Rz { theta, .. } if seen[q] == 1 => {
                angles[q][1] = theta;
                seen[q] = 2;
            }
            _ => return None,
        }
    }
    if seen != [2, 2] {
        return None;
    }
    let rotations = angles.map(|[ry, _]| {
        let (s, c) = (ry / 2.).sin_cos();
        [C::new(c, 0.), C::new(s, 0.)]
    });
    let phases = std::array::from_fn(|i| {
        C::from_polar(
            1.,
            (if i & 1 == 0 { -1. } else { 1. }) * angles[0][1] / 2.
                + (if i & 2 == 0 { -1. } else { 1. }) * angles[1][1] / 2.,
        )
    });
    Some(Structured { rotations, phases })
}
