//! Storage precision is separate from arithmetic and reduction precision.
pub use half::f16;
use num_complex::Complex;
pub use num_complex::{Complex32, Complex64};
use serde::{Deserialize, Serialize};
use std::ops::{Add, Mul};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Precision {
    F16,
    F32,
    #[default]
    F64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
#[repr(C)]
pub struct Complex16 {
    pub re: f16,
    pub im: f16,
}

pub trait Amplitude: Copy + Send + Sync + std::fmt::Debug + 'static {
    type Compute: Copy + Send + Sync + Add<Output = Self::Compute> + Mul<Output = Self::Compute>;
    const PRECISION: Precision;
    fn load(self) -> Self::Compute;
    fn store(value: Self::Compute) -> Self;
    fn coefficient(value: Complex64) -> Self::Compute;
    fn coefficient_finite(value: Complex64) -> bool;
    /// Multiply by a real coefficient prepared with `coefficient` (imaginary part zero).
    #[inline]
    fn real_scale(coefficient: Self::Compute, value: Self::Compute) -> Self::Compute {
        coefficient * value
    }
    /// Combine two amplitudes using coefficients with zero imaginary parts.
    #[inline]
    fn real_mix(
        left: Self::Compute,
        x: Self::Compute,
        right: Self::Compute,
        y: Self::Compute,
    ) -> Self::Compute {
        Self::real_scale(left, x) + Self::real_scale(right, y)
    }
    fn as_complex64(self) -> Complex64;
    fn negated(self) -> Self;
    fn times_i(self) -> Self;
    fn zero() -> Self {
        Self::store(Self::coefficient(Complex64::new(0., 0.)))
    }
    fn one() -> Self {
        Self::store(Self::coefficient(Complex64::new(1., 0.)))
    }
}
macro_rules! native {
    ($t:ty,$real:ty,$precision:ident) => {
        impl Amplitude for $t {
            type Compute = Self;
            const PRECISION: Precision = Precision::$precision;
            #[inline]
            fn load(self) -> Self {
                self
            }
            #[inline]
            fn store(value: Self) -> Self {
                value
            }
            #[inline]
            fn coefficient(v: Complex64) -> Self {
                Self::new(v.re as $real, v.im as $real)
            }
            #[inline]
            fn real_scale(coefficient: Self, value: Self) -> Self {
                Self::new(coefficient.re * value.re, coefficient.re * value.im)
            }
            #[cfg(any(target_arch = "aarch64", target_feature = "fma"))]
            #[inline]
            fn real_mix(left: Self, x: Self, right: Self, y: Self) -> Self {
                Self::new(
                    left.re.mul_add(x.re, right.re * y.re),
                    left.re.mul_add(x.im, right.re * y.im),
                )
            }
            fn coefficient_finite(v: Complex64) -> bool {
                (v.re as $real).is_finite() && (v.im as $real).is_finite()
            }
            #[inline]
            fn as_complex64(self) -> Complex64 {
                Complex64::new(self.re as f64, self.im as f64)
            }
            #[inline]
            fn negated(self) -> Self {
                -self
            }
            #[inline]
            fn times_i(self) -> Self {
                Self::new(-self.im, self.re)
            }
        }
    };
}
native!(Complex<f64>, f64, F64);
native!(Complex<f32>, f32, F32);
impl Amplitude for Complex16 {
    type Compute = Complex32;
    const PRECISION: Precision = Precision::F16;
    #[inline]
    fn load(self) -> Complex32 {
        Complex32::new(self.re.to_f32(), self.im.to_f32())
    }
    #[inline]
    fn store(v: Complex32) -> Self {
        Self {
            re: f16::from_f32(v.re),
            im: f16::from_f32(v.im),
        }
    }
    #[inline]
    fn coefficient(v: Complex64) -> Complex32 {
        Complex32::new(v.re as f32, v.im as f32)
    }
    fn coefficient_finite(v: Complex64) -> bool {
        <Complex32 as Amplitude>::coefficient_finite(v)
    }
    #[inline]
    fn real_scale(coefficient: Complex32, value: Complex32) -> Complex32 {
        <Complex32 as Amplitude>::real_scale(coefficient, value)
    }
    #[inline]
    fn real_mix(left: Complex32, x: Complex32, right: Complex32, y: Complex32) -> Complex32 {
        <Complex32 as Amplitude>::real_mix(left, x, right, y)
    }
    #[inline]
    fn as_complex64(self) -> Complex64 {
        Complex64::new(self.re.to_f64(), self.im.to_f64())
    }
    #[inline]
    fn negated(self) -> Self {
        Self {
            re: f16::from_bits(self.re.to_bits() ^ 0x8000),
            im: f16::from_bits(self.im.to_bits() ^ 0x8000),
        }
    }
    #[inline]
    fn times_i(self) -> Self {
        Self {
            re: f16::from_bits(self.im.to_bits() ^ 0x8000),
            im: self.re,
        }
    }
}
