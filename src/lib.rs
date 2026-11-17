//! CPU statevector simulation with bounded storage and optional circuit compilation.
#![forbid(unsafe_code)]
mod circuit;
mod execution;
mod fusion;
pub mod precision;
pub use circuit::{CompilationStats, CompileOptions, CompiledCircuit};
pub use execution::CpuExecutor;
pub use precision::{Amplitude, Complex16, Complex32, Complex64, Precision};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone)]
pub struct SimError(pub String);
impl std::fmt::Display for SimError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for SimError {}
fn error(message: impl Into<String>) -> SimError {
    SimError(message.into())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "gate", rename_all = "lowercase", deny_unknown_fields)]
pub enum Gate {
    H {
        target: usize,
    },
    X {
        target: usize,
    },
    Y {
        target: usize,
    },
    Z {
        target: usize,
    },
    S {
        target: usize,
    },
    T {
        target: usize,
    },
    Rx {
        target: usize,
        theta: f64,
    },
    Ry {
        target: usize,
        theta: f64,
    },
    Rz {
        target: usize,
        theta: f64,
    },
    Cx {
        control: usize,
        target: usize,
    },
    Cz {
        control: usize,
        target: usize,
    },
    U {
        target: usize,
        matrix: [[Complex64; 2]; 2],
    },
}
impl Gate {
    pub(crate) fn target(&self) -> usize {
        match *self {
            Self::H { target }
            | Self::X { target }
            | Self::Y { target }
            | Self::Z { target }
            | Self::S { target }
            | Self::T { target }
            | Self::Rx { target, .. }
            | Self::Ry { target, .. }
            | Self::Rz { target, .. }
            | Self::Cx { target, .. }
            | Self::Cz { target, .. }
            | Self::U { target, .. } => target,
        }
    }
    pub(crate) fn wires(&self) -> Vec<usize> {
        match *self {
            Self::Cx { control, target } | Self::Cz { control, target } => vec![target, control],
            _ => vec![self.target()],
        }
    }
    pub(crate) fn validate(&self, n: usize) -> Result<(), SimError> {
        if self.target() >= n {
            return Err(error("target qubit outside register"));
        }
        match *self {
            Self::Cx { control, target } | Self::Cz { control, target }
                if control >= n || control == target =>
            {
                return Err(error("invalid control qubit"));
            }
            Self::Rx { theta, .. } | Self::Ry { theta, .. } | Self::Rz { theta, .. }
                if !theta.is_finite() =>
            {
                return Err(error("angle must be finite"));
            }
            _ => {}
        }
        if self
            .matrix()
            .iter()
            .flatten()
            .any(|z| !z.re.is_finite() || !z.im.is_finite())
        {
            return Err(error("matrix must be finite"));
        }
        Ok(())
    }
    pub(crate) fn matrix(&self) -> [[Complex64; 2]; 2] {
        let z = Complex64::new(0., 0.);
        let o = Complex64::new(1., 0.);
        let i = Complex64::new(0., 1.);
        match *self {
            Self::H { .. } => {
                let h = Complex64::new(std::f64::consts::FRAC_1_SQRT_2, 0.);
                [[h, h], [h, -h]]
            }
            Self::X { .. } | Self::Cx { .. } => [[z, o], [o, z]],
            Self::Y { .. } => [[z, -i], [i, z]],
            Self::Z { .. } | Self::Cz { .. } => [[o, z], [z, -o]],
            Self::S { .. } => [[o, z], [z, i]],
            Self::T { .. } => [
                [o, z],
                [z, Complex64::from_polar(1., std::f64::consts::FRAC_PI_4)],
            ],
            Self::Rx { theta, .. } => {
                let (s, c) = (theta / 2.).sin_cos();
                [
                    [Complex64::new(c, 0.), Complex64::new(0., -s)],
                    [Complex64::new(0., -s), Complex64::new(c, 0.)],
                ]
            }
            Self::Ry { theta, .. } => {
                let (s, c) = (theta / 2.).sin_cos();
                [
                    [Complex64::new(c, 0.), Complex64::new(-s, 0.)],
                    [Complex64::new(s, 0.), Complex64::new(c, 0.)],
                ]
            }
            Self::Rz { theta, .. } => [
                [Complex64::from_polar(1., -theta / 2.), z],
                [z, Complex64::from_polar(1., theta / 2.)],
            ],
            Self::U { matrix, .. } => matrix,
        }
    }
}

pub struct TypedStateVector<A: Amplitude> {
    pub(crate) n: usize,
    pub(crate) data: Vec<A>,
}
pub type StateVector64 = TypedStateVector<Complex64>;
pub type StateVector32 = TypedStateVector<Complex32>;
pub type StateVector16 = TypedStateVector<Complex16>;
pub type StateVector = StateVector64;
impl<A: Amplitude> TypedStateVector<A> {
    pub fn new(n: usize) -> Result<Self, SimError> {
        Self::with_memory_limit(n, 256 << 20)
    }
    pub fn with_memory_limit(n: usize, limit: usize) -> Result<Self, SimError> {
        if n >= usize::BITS as usize {
            return Err(error("register width overflows address space"));
        }
        let len = 1usize << n;
        let bytes = len
            .checked_mul(std::mem::size_of::<A>())
            .ok_or_else(|| error("state byte size overflow"))?;
        if bytes > limit || bytes > isize::MAX as usize {
            return Err(error("state exceeds memory limit"));
        }
        let mut data = Vec::new();
        data.try_reserve_exact(len)
            .map_err(|e| error(format!("state allocation failed: {e}")))?;
        data.resize(len, A::zero());
        data[0] = A::one();
        Ok(Self { n, data })
    }
    pub fn num_qubits(&self) -> usize {
        self.n
    }
    pub fn state_bytes(&self) -> usize {
        self.data.len() * std::mem::size_of::<A>()
    }
    pub fn precision(&self) -> Precision {
        A::PRECISION
    }
    pub fn amplitudes(&self) -> &[A] {
        &self.data
    }
    pub fn probabilities(&self) -> Vec<f64> {
        self.data
            .iter()
            .map(|a| a.as_complex64().norm_sqr())
            .collect()
    }
    pub fn norm_sqr(&self) -> f64 {
        let (mut sum, mut correction) = (0., 0.);
        for a in &self.data {
            let y = a.as_complex64().norm_sqr() - correction;
            let t = sum + y;
            correction = (t - sum) - y;
            sum = t;
        }
        sum
    }
    pub fn apply(&mut self, gate: &Gate) -> Result<(), SimError> {
        self.apply_all(std::slice::from_ref(gate))
    }
    pub fn apply_all(&mut self, gates: &[Gate]) -> Result<(), SimError> {
        validate::<A>(self.n, gates)?;
        for g in gates {
            execution::apply(&mut self.data, g, false);
        }
        Ok(())
    }
}
pub(crate) fn validate<A: Amplitude>(n: usize, gates: &[Gate]) -> Result<(), SimError> {
    for g in gates {
        g.validate(n)?;
        if g.matrix()
            .iter()
            .flatten()
            .any(|z| !A::coefficient_finite(*z))
        {
            return Err(error("coefficient overflows compute precision"));
        }
    }
    Ok(())
}
