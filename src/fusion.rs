use crate::{Amplitude, CompilationStats, Complex64 as C, Gate, execution};
use rayon::prelude::*;

// A contiguous 64 KiB tile with FP64 leaves room for other L1 data.
const TILE_AMPLITUDES: usize = 4096;
const MIN_TILED_AMPLITUDES: usize = 1 << 17;
const MIN_ROTATION_RUN: usize = 8;

type Matrix = [[C; 4]; 4];
pub(crate) enum Operation {
    Gate(Gate),
    Tiled(Vec<Operation>),
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
    fn tile_local(&self) -> bool {
        let fits = |wire| 1usize << wire < TILE_AMPLITUDES;
        match self {
            Self::Gate(gate) => gate.wires().into_iter().all(fits),
            Self::Fused { wires, .. } => wires.iter().copied().all(fits),
            Self::Tiled(_) => false,
        }
    }

    pub(crate) fn apply<A: Amplitude>(&self, data: &mut [A], parallel: bool) {
        match self {
            Self::Gate(g) => execution::apply(data, g, parallel),
            Self::Tiled(ops) => {
                let apply_tile = |tile: &mut [A]| {
                    for op in ops {
                        op.apply(tile, false);
                    }
                };
                if data.len() < MIN_TILED_AMPLITUDES {
                    for op in ops {
                        op.apply(data, parallel);
                    }
                } else if parallel {
                    data.par_chunks_mut(TILE_AMPLITUDES).for_each(apply_tile);
                } else {
                    data.chunks_mut(TILE_AMPLITUDES).for_each(apply_tile);
                }
            }
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
fn is_rotation(gate: &Gate) -> bool {
    matches!(
        gate,
        Gate::H { .. } | Gate::Rx { .. } | Gate::Ry { .. } | Gate::Rz { .. }
    )
}

fn collect_rotation_run(
    gates: &[Gate],
    n: usize,
    ops: &mut Vec<Operation>,
    stats: &mut CompilationStats,
) {
    let mut by_wire = vec![Vec::new(); n];
    for gate in gates {
        by_wire[gate.target()].push(gate.clone());
    }
    flush_rotations(by_wire, ops, stats);
}

fn flush_rotations(
    by_wire: Vec<Vec<Gate>>,
    ops: &mut Vec<Operation>,
    stats: &mut CompilationStats,
) {
    let active: Vec<_> = by_wire
        .into_iter()
        .enumerate()
        .filter(|(_, gates)| !gates.is_empty())
        .collect();
    for group in active.chunks(2) {
        let wires: Vec<_> = group.iter().map(|(wire, _)| *wire).collect();
        // Different wires commute. Keep the original order within each wire.
        let source: Vec<_> = group
            .iter()
            .flat_map(|(_, gates)| gates.iter().cloned())
            .collect();
        if source.len() == 1 || source.iter().all(|gate| matches!(gate, Gate::Rz { .. })) {
            ops.extend(source.into_iter().map(Operation::Gate));
            continue;
        }
        push_fused(source, wires, ops, stats);
    }
}

fn push_fused(
    source: Vec<Gate>,
    wires: Vec<usize>,
    ops: &mut Vec<Operation>,
    stats: &mut CompilationStats,
) {
    let matrix = compose(&source, &wires);
    if matrix.iter().flatten().all(|&z| crate::circuit::finite(z)) {
        let structured = recognize(&source, &wires);
        stats.fused_blocks += 1;
        stats.fused_source_gates += source.len();
        stats.structured_fused_blocks += usize::from(structured.is_some());
        ops.push(Operation::Fused {
            wires,
            matrix: Box::new(matrix),
            source,
            structured,
        });
    } else {
        ops.extend(source.into_iter().map(Operation::Gate));
    }
}

fn is_controlled(gate: &Gate) -> bool {
    matches!(gate, Gate::Cx { .. } | Gate::Cz { .. })
}

fn collect_across_controls(
    gates: &[Gate],
    n: usize,
    ops: &mut Vec<Operation>,
    stats: &mut CompilationStats,
) {
    let mut pending = vec![Vec::new(); n];
    for gate in gates {
        let (control, target) = match *gate {
            Gate::Cx { control, target } | Gate::Cz { control, target } => (control, target),
            _ => {
                pending[gate.target()].push(gate.clone());
                continue;
            }
        };
        // Flush both dependencies before the controlled gate. Pending rotations
        // on every other wire commute with it and can stay queued.
        let wires = vec![control.min(target), control.max(target)];
        let mut source = std::mem::take(&mut pending[wires[0]]);
        source.append(&mut pending[wires[1]]);
        source.push(gate.clone());
        if source.len() >= 4 && source[..source.len() - 1].iter().any(is_mixing) {
            push_fused(source, wires, ops, stats);
        } else {
            ops.extend(source.into_iter().map(Operation::Gate));
        }
    }
    flush_rotations(pending, ops, stats);
}

fn is_mixing(gate: &Gate) -> bool {
    matches!(
        gate,
        Gate::H { .. } | Gate::Rx { .. } | Gate::Ry { .. } | Gate::U { .. }
    )
}

pub(crate) fn collect(gates: &[Gate], n: usize, stats: &mut CompilationStats) -> Vec<Operation> {
    let mut ops = Vec::new();
    let mut rotation_runs = Vec::new();
    if n >= MIN_TILED_AMPLITUDES.ilog2() as usize {
        let mut offset = 0;
        for run in gates.chunk_by(|left, right| is_rotation(left) == is_rotation(right)) {
            if run.len() >= MIN_ROTATION_RUN && is_rotation(&run[0]) {
                let wires = run
                    .iter()
                    .fold(0usize, |mask, gate| mask | (1 << gate.target()));
                // Dense two-wire kernels need enough gate reuse to pay for their
                // arithmetic. A layer of independent H gates has no such reuse.
                if run.len() >= 2 * wires.count_ones() as usize
                    && run.iter().any(|gate| !matches!(gate, Gate::Rz { .. }))
                {
                    rotation_runs.push(offset..offset + run.len());
                }
            }
            offset += run.len();
        }
    }
    let mut rotation_runs = rotation_runs.into_iter().peekable();
    let mut start = 0;
    while start < gates.len() {
        if let Some(run) = rotation_runs.next_if(|run| run.start == start) {
            let mut end = run.end;
            while let Some(next) =
                rotation_runs.next_if(|next| gates[end..next.start].iter().all(is_controlled))
            {
                end = next.end;
            }
            if end == run.end {
                collect_rotation_run(&gates[run], n, &mut ops, stats);
            } else {
                end += gates[end..]
                    .iter()
                    .take_while(|gate| is_controlled(gate))
                    .count();
                collect_across_controls(&gates[start..end], n, &mut ops, stats);
            }
            start = end;
            continue;
        }
        let mut wires = gates[start].wires();
        let mut end = start + 1;
        while end < gates.len()
            && end - start < 32
            && rotation_runs.peek().is_none_or(|run| run.start != end)
        {
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
        if source.iter().any(is_mixing) && source.len() >= if wires.len() == 1 { 2 } else { 4 } {
            push_fused(source.to_vec(), wires, &mut ops, stats);
        } else {
            ops.extend(source.iter().cloned().map(Operation::Gate));
        }
        start = end;
    }
    if n < MIN_TILED_AMPLITUDES.ilog2() as usize {
        return ops;
    }
    // Three passes amortize per-tile dispatch. Bound the group so long runs
    // still expose scheduling boundaries and preserve cheap gate kernels.
    let mut planned = Vec::new();
    let mut remaining = ops.into_iter().peekable();
    while let Some(op) = remaining.next() {
        if !op.tile_local() {
            planned.push(op);
            continue;
        }
        let mut group = vec![op];
        while group.len() < 32 && remaining.peek().is_some_and(Operation::tile_local) {
            group.extend(remaining.next());
        }
        if group.len() >= 3 {
            stats.tiled_blocks += 1;
            stats.tiled_operations += group.len();
            planned.push(Operation::Tiled(group));
        } else {
            planned.extend(group);
        }
    }
    planned
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
