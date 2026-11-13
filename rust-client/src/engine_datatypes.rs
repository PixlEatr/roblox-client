//! Rust implementation for the small immutable signed-16-bit engine datatypes.
//!
//! The official Luau VM and its userdata/metatable API remain owned by the
//! native binding layer. These functions move the value representation and
//! arithmetic into Rust. The C ABI functions validate shapes and return status
//! codes; they never call Lua or unwind across the C boundary.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;

pub const STATUS_OK: i32 = 0;
pub const STATUS_NULL: i32 = 1;
pub const STATUS_SHAPE: i32 = 2;
pub const STATUS_DIVIDE_BY_ZERO: i32 = 3;
pub const OP_ADD: i32 = 0;
pub const OP_SUBTRACT: i32 = 1;
pub const OP_MULTIPLY: i32 = 2;
pub const OP_DIVIDE: i32 = 3;

/// Matches the native `integer_values::Vector` layout (four-byte dimension
/// and three four-byte signed slots) so the Lua adapter can copy values
/// without converting through JSON or creating a second representation.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Int16Vector {
    pub dimension: i32,
    pub values: [i32; 3],
}

impl Int16Vector {
    pub fn new(dimension: i32, values: [f64; 3]) -> Result<Self, DatatypeError> {
        if !matches!(dimension, 2 | 3) {
            return Err(DatatypeError::Shape);
        }
        let mut converted = values.map(narrow);
        if dimension == 2 {
            converted[2] = 0;
        }
        Ok(Self {
            dimension,
            values: converted,
        })
    }

    pub fn zero(dimension: i32) -> Result<Self, DatatypeError> {
        Self::new(dimension, [0.0; 3])
    }

    pub fn is_valid(&self) -> bool {
        if !matches!(self.dimension, 2 | 3) {
            return false;
        }
        let n = self.dimension as usize;
        self.values[..n]
            .iter()
            .all(|v| (i16::MIN as i32..=i16::MAX as i32).contains(v))
            && (self.dimension == 3 || self.values[2] == 0)
    }

    pub fn component(&self, index: usize) -> Option<i16> {
        (self.is_valid() && index < self.dimension as usize).then(|| self.values[index] as i16)
    }

    pub fn checked_eq(&self, other: &Self) -> Result<bool, DatatypeError> {
        if !self.is_valid() || !other.is_valid() {
            return Err(DatatypeError::Shape);
        }
        Ok(self.dimension == other.dimension
            && self.values[..self.dimension as usize] == other.values[..other.dimension as usize])
    }
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Region3int16 {
    pub minimum: Int16Vector,
    pub maximum: Int16Vector,
}

impl Region3int16 {
    pub fn new(
        minimum: Option<Int16Vector>,
        maximum: Option<Int16Vector>,
    ) -> Result<Self, DatatypeError> {
        let minimum = minimum.unwrap_or(Int16Vector::zero(3)?);
        let maximum = maximum.unwrap_or(Int16Vector::zero(3)?);
        if !minimum.is_valid()
            || minimum.dimension != 3
            || !maximum.is_valid()
            || maximum.dimension != 3
        {
            return Err(DatatypeError::Shape);
        }
        Ok(Self { minimum, maximum })
    }

    pub fn checked_eq(&self, other: &Self) -> Result<bool, DatatypeError> {
        if !valid_region(self) || !valid_region(other) {
            return Err(DatatypeError::Shape);
        }
        Ok(self.minimum.values == other.minimum.values
            && self.maximum.values == other.maximum.values)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DatatypeError {
    Shape,
    DivideByZero,
    Operation,
}

/// Conversion observed by the Studio 0.745 constructor probes.
///
/// Values are truncated toward zero, reduced modulo 65536, then interpreted
/// as signed 16-bit. The two out-of-range guards preserve the observed
/// arm64 floating-to-integer behavior at the signed and unsigned 64-bit
/// conversion boundaries.
pub fn narrow(number: f64) -> i32 {
    if number.is_nan() || number <= -9_223_372_036_854_775_808.0 {
        return 0;
    }
    if number >= 18_446_744_073_709_551_616.0 {
        return -1;
    }
    let mut wrapped = number.trunc() % 65_536.0;
    if wrapped < 0.0 {
        wrapped += 65_536.0;
    }
    let value = wrapped as i32;
    if value >= 32_768 {
        value - 65_536
    } else {
        value
    }
}

fn scalar_float(value: f64) -> f64 {
    (value as f32) as f64
}

pub fn unary_negate(value: Int16Vector) -> Result<Int16Vector, DatatypeError> {
    if !value.is_valid() {
        return Err(DatatypeError::Shape);
    }
    let mut result = value;
    for component in &mut result.values[..result.dimension as usize] {
        *component = narrow(-(*component as f64));
    }
    Ok(result)
}

pub fn vector_arithmetic(
    operation: i32,
    left: Int16Vector,
    right: Int16Vector,
) -> Result<Int16Vector, DatatypeError> {
    if !left.is_valid()
        || !right.is_valid()
        || left.dimension != right.dimension
        || !matches!(operation, OP_ADD | OP_SUBTRACT | OP_MULTIPLY | OP_DIVIDE)
    {
        return Err(DatatypeError::Shape);
    }
    let mut result = left;
    for i in 0..left.dimension as usize {
        let a = left.values[i] as f64;
        let b = right.values[i] as f64;
        if operation == OP_DIVIDE && left.dimension == 2 && b == 0.0 {
            return Err(DatatypeError::DivideByZero);
        }
        result.values[i] = narrow(apply(operation, a, b));
    }
    Ok(result)
}

/// Scalar arithmetic follows the native callback's asymmetric coercions:
/// scalar operands are first converted to f32, and multiplication with a
/// Vector3int16 narrows the scalar once more before component multiplication.
pub fn scalar_arithmetic(
    operation: i32,
    vector: Int16Vector,
    scalar: f64,
    scalar_is_left: bool,
) -> Result<Int16Vector, DatatypeError> {
    if !vector.is_valid()
        || !matches!(operation, OP_MULTIPLY | OP_DIVIDE)
        || (scalar_is_left && operation != OP_MULTIPLY)
    {
        return Err(DatatypeError::Operation);
    }
    let mut scalar = scalar_float(scalar);
    if operation == OP_MULTIPLY && vector.dimension == 3 {
        scalar = narrow(scalar) as f64;
    }
    let mut result = vector;
    for i in 0..vector.dimension as usize {
        let component = vector.values[i] as f64;
        let (left, right) = if scalar_is_left {
            (scalar, component)
        } else {
            (component, scalar)
        };
        if operation == OP_DIVIDE && vector.dimension == 2 && right == 0.0 {
            return Err(DatatypeError::DivideByZero);
        }
        result.values[i] = narrow(apply(operation, left, right));
    }
    Ok(result)
}

fn apply(operation: i32, left: f64, right: f64) -> f64 {
    match operation {
        OP_ADD => left + right,
        OP_SUBTRACT => left - right,
        OP_MULTIPLY => left * right,
        OP_DIVIDE => left / right,
        _ => f64::NAN,
    }
}

fn valid_region(value: &Region3int16) -> bool {
    value.minimum.is_valid()
        && value.minimum.dimension == 3
        && value.maximum.is_valid()
        && value.maximum.dimension == 3
}

fn status_for(result: Result<(), DatatypeError>) -> i32 {
    match result {
        Ok(()) => STATUS_OK,
        Err(DatatypeError::Shape | DatatypeError::Operation) => STATUS_SHAPE,
        Err(DatatypeError::DivideByZero) => STATUS_DIVIDE_BY_ZERO,
    }
}

fn ffi_guard(operation: impl FnOnce() -> i32) -> i32 {
    catch_unwind(AssertUnwindSafe(operation)).unwrap_or(STATUS_SHAPE)
}

/// Narrow a scalar without allocating or crossing back into Lua.
#[no_mangle]
pub extern "C" fn rove_integer_narrow(value: f64) -> i32 {
    ffi_guard(|| narrow(value))
}

/// Construct a vector from three already Lua-coerced numbers. For a 2D value,
/// the third component is ignored and stored as zero.
///
/// # Safety
/// `numbers` must reference three readable doubles and `result` one writable
/// `Int16Vector`, unless the corresponding pointer is null (which is checked).
#[no_mangle]
pub unsafe extern "C" fn rove_integer_vector_construct(
    dimension: i32,
    numbers: *const f64,
    result: *mut Int16Vector,
) -> i32 {
    ffi_guard(|| {
        if numbers.is_null() || result.is_null() {
            return STATUS_NULL;
        }
        let values = unsafe { [*numbers, *numbers.add(1), *numbers.add(2)] };
        let Ok(mut vector) = Int16Vector::new(dimension, values) else {
            return STATUS_SHAPE;
        };
        if dimension == 2 {
            vector.values[2] = 0;
        }
        unsafe {
            ptr::write(result, vector);
        }
        STATUS_OK
    })
}

/// Apply a vector/vector operation. The caller must check for `STATUS_DIVIDE_BY_ZERO`
/// after returning to C++ before raising a Lua error.
///
/// # Safety
/// Each nonnull pointer must point to a valid, aligned value of its declared type.
#[no_mangle]
pub unsafe extern "C" fn rove_integer_vector_binary(
    operation: i32,
    left: *const Int16Vector,
    right: *const Int16Vector,
    result: *mut Int16Vector,
) -> i32 {
    ffi_guard(|| {
        if left.is_null() || right.is_null() || result.is_null() {
            return STATUS_NULL;
        }
        let left = unsafe { ptr::read(left) };
        let right = unsafe { ptr::read(right) };
        match vector_arithmetic(operation, left, right) {
            Ok(value) => {
                unsafe {
                    ptr::write(result, value);
                }
                STATUS_OK
            }
            Err(error) => status_for(Err(error)),
        }
    })
}

/// Apply multiplication/division by a scalar. `scalar_is_left` supports the
/// native scalar-times-vector multiplication case. Scalar-left division is an
/// operation error, matching the native Lua callback.
///
/// # Safety
/// `vector` must point to a valid, aligned value and `result` to writable space.
#[no_mangle]
pub unsafe extern "C" fn rove_integer_vector_scalar(
    operation: i32,
    vector: *const Int16Vector,
    scalar: f64,
    scalar_is_left: u8,
    result: *mut Int16Vector,
) -> i32 {
    ffi_guard(|| {
        if vector.is_null() || result.is_null() {
            return STATUS_NULL;
        }
        if scalar_is_left > 1 {
            return STATUS_SHAPE;
        }
        let vector = unsafe { ptr::read(vector) };
        match scalar_arithmetic(operation, vector, scalar, scalar_is_left != 0) {
            Ok(value) => {
                unsafe {
                    ptr::write(result, value);
                }
                STATUS_OK
            }
            Err(error) => status_for(Err(error)),
        }
    })
}

/// Construct a region. A null min or max pointer means the omitted Lua
/// argument and therefore selects the all-zero Vector3int16 default.
///
/// # Safety
/// Nonnull vector pointers must be readable/aligned; `result` must be writable.
#[no_mangle]
pub unsafe extern "C" fn rove_region3int16_construct(
    minimum: *const Int16Vector,
    maximum: *const Int16Vector,
    result: *mut Region3int16,
) -> i32 {
    ffi_guard(|| {
        if result.is_null() {
            return STATUS_NULL;
        }
        let minimum = if minimum.is_null() {
            None
        } else {
            Some(unsafe { ptr::read(minimum) })
        };
        let maximum = if maximum.is_null() {
            None
        } else {
            Some(unsafe { ptr::read(maximum) })
        };
        match Region3int16::new(minimum, maximum) {
            Ok(value) => {
                unsafe {
                    ptr::write(result, value);
                }
                STATUS_OK
            }
            Err(error) => status_for(Err(error)),
        }
    })
}

/// Return 1 for equal regions, 0 for unequal regions, and a negative status
/// for malformed inputs.
///
/// # Safety
/// Nonnull region pointers must be readable and aligned.
#[no_mangle]
pub unsafe extern "C" fn rove_region3int16_equal(
    left: *const Region3int16,
    right: *const Region3int16,
) -> i32 {
    ffi_guard(|| {
        if left.is_null() || right.is_null() {
            return -STATUS_NULL;
        }
        let left = unsafe { ptr::read(left) };
        let right = unsafe { ptr::read(right) };
        match left.checked_eq(&right) {
            Ok(true) => 1,
            Ok(false) => 0,
            Err(_) => -STATUS_SHAPE,
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn narrowing_matches_signed_16_wrap_and_studio_conversion_guards() {
        for (input, expected) in [
            (0.0, 0),
            (32767.9, 32767),
            (32768.0, -32768),
            (-32769.0, 32767),
            (65535.9, -1),
            (65536.0, 0),
            (-65537.0, -1),
            (f64::NAN, 0),
            (f64::INFINITY, -1),
            (f64::NEG_INFINITY, 0),
            (-9_223_372_036_854_775_808.0, 0),
            (18_446_744_073_709_551_616.0, -1),
        ] {
            assert_eq!(narrow(input), expected, "input {input}");
        }
    }

    #[test]
    fn constructor_uses_active_dimensions_and_keeps_unused_component_zero() {
        assert_eq!(
            Int16Vector::new(2, [1.9, -2.9, 90.0]).unwrap(),
            Int16Vector {
                dimension: 2,
                values: [1, -2, 0]
            }
        );
        assert_eq!(
            Int16Vector::new(3, [1.9, -2.9, 90.0]).unwrap().values,
            [1, -2, 90]
        );
        assert_eq!(Int16Vector::new(4, [0.0; 3]), Err(DatatypeError::Shape));
    }

    #[test]
    fn vector_operators_wrap_and_require_equal_dimensions() {
        let a = Int16Vector::new(3, [32767.0, -32768.0, 9.0]).unwrap();
        let b = Int16Vector::new(3, [1.0, -1.0, 2.0]).unwrap();
        assert_eq!(
            vector_arithmetic(OP_ADD, a, b).unwrap().values,
            [-32768, 32767, 11]
        );
        assert_eq!(
            vector_arithmetic(OP_SUBTRACT, a, b).unwrap().values,
            [32766, -32767, 7]
        );
        assert_eq!(
            vector_arithmetic(OP_MULTIPLY, a, b).unwrap().values,
            [32767, -32768, 18]
        );
        assert_eq!(
            vector_arithmetic(
                OP_DIVIDE,
                Int16Vector::new(3, [3.0, -6.0, 7.0]).unwrap(),
                Int16Vector::new(3, [2.0, 4.0, 2.0]).unwrap(),
            )
            .unwrap()
            .values,
            [1, -1, 3]
        );
        assert_eq!(
            vector_arithmetic(
                OP_DIVIDE,
                Int16Vector::new(2, [7.0, -8.0, 0.0]).unwrap(),
                Int16Vector::new(2, [2.0, 3.0, 0.0]).unwrap(),
            )
            .unwrap()
            .values,
            [3, -2, 0]
        );
        assert_eq!(
            unary_negate(Int16Vector::new(3, [-32768.0, 32767.0, 0.0]).unwrap())
                .unwrap()
                .values,
            [-32768, -32767, 0]
        );
        let two = Int16Vector::new(2, [1.0, 2.0, 0.0]).unwrap();
        assert_eq!(vector_arithmetic(OP_ADD, a, two), Err(DatatypeError::Shape));
    }

    #[test]
    fn division_preserves_two_dimensional_error_and_three_dimensional_conversion() {
        let two = Int16Vector::new(2, [4.0, 0.0, 0.0]).unwrap();
        let zero2 = Int16Vector::zero(2).unwrap();
        assert_eq!(
            vector_arithmetic(OP_DIVIDE, two, zero2),
            Err(DatatypeError::DivideByZero)
        );
        let three = Int16Vector::new(3, [4.0, -4.0, 0.0]).unwrap();
        let zero3 = Int16Vector::zero(3).unwrap();
        assert_eq!(
            vector_arithmetic(OP_DIVIDE, three, zero3).unwrap().values,
            [-1, 0, 0]
        );
        assert_eq!(
            scalar_arithmetic(OP_DIVIDE, three, 0.0, false)
                .unwrap()
                .values,
            [-1, 0, 0]
        );
    }

    #[test]
    fn scalar_operations_apply_native_float_and_dimension_specific_rules() {
        let two = Int16Vector::new(2, [3.0, -4.0, 0.0]).unwrap();
        assert_eq!(
            scalar_arithmetic(OP_MULTIPLY, two, 2.5, false)
                .unwrap()
                .values,
            [7, -10, 0]
        );
        assert_eq!(
            scalar_arithmetic(OP_MULTIPLY, two, 2.5, true)
                .unwrap()
                .values,
            [7, -10, 0]
        );
        assert_eq!(
            scalar_arithmetic(
                OP_MULTIPLY,
                Int16Vector::new(2, [1.0, 1.0, 0.0]).unwrap(),
                16_777_217.0,
                false,
            )
            .unwrap()
            .values,
            [0, 0, 0]
        );
        assert_eq!(
            scalar_arithmetic(OP_DIVIDE, two, 2.0, false)
                .unwrap()
                .values,
            [1, -2, 0]
        );
        let three = Int16Vector::new(3, [3.0, -4.0, 7.0]).unwrap();
        assert_eq!(
            scalar_arithmetic(OP_MULTIPLY, three, 2.9, false)
                .unwrap()
                .values,
            [6, -8, 14]
        );
        assert_eq!(
            scalar_arithmetic(OP_MULTIPLY, three, 65537.0, true)
                .unwrap()
                .values,
            [3, -4, 7]
        );
        assert_eq!(
            scalar_arithmetic(OP_DIVIDE, three, 2.0, true),
            Err(DatatypeError::Operation)
        );
        assert_eq!(
            scalar_arithmetic(OP_ADD, two, 2.0, false),
            Err(DatatypeError::Operation)
        );
    }

    #[test]
    fn regions_default_optional_ends_and_compare_components() {
        let zero = Int16Vector::zero(3).unwrap();
        assert_eq!(
            Region3int16::new(None, None).unwrap(),
            Region3int16 {
                minimum: zero,
                maximum: zero
            }
        );
        let min = Int16Vector::new(3, [-1.0, -2.0, -3.0]).unwrap();
        let max = Int16Vector::new(3, [1.0, 2.0, 3.0]).unwrap();
        let region = Region3int16::new(Some(min), Some(max)).unwrap();
        assert!(region.checked_eq(&region).unwrap());
        assert!(!region
            .checked_eq(&Region3int16::new(Some(max), Some(min)).unwrap())
            .unwrap());
        assert_eq!(
            Region3int16::new(Some(Int16Vector::zero(2).unwrap()), None),
            Err(DatatypeError::Shape)
        );
    }

    #[test]
    fn ffi_returns_status_for_null_shape_and_divide_errors() {
        assert_eq!(std::mem::size_of::<Int16Vector>(), 16);
        assert_eq!(std::mem::size_of::<Region3int16>(), 32);
        let mut result = Int16Vector::default();
        let values = [-32769.0, 32768.0, 0.0];
        assert_eq!(
            unsafe { rove_integer_vector_construct(2, values.as_ptr(), &mut result) },
            STATUS_OK
        );
        assert_eq!(result.values, [32767, -32768, 0]);
        assert_eq!(
            unsafe { rove_integer_vector_construct(4, values.as_ptr(), &mut result) },
            STATUS_SHAPE
        );
        assert_eq!(
            unsafe { rove_integer_vector_construct(2, ptr::null(), &mut result) },
            STATUS_NULL
        );
        let zero = Int16Vector::zero(2).unwrap();
        assert_eq!(
            unsafe { rove_integer_vector_binary(OP_DIVIDE, &result, &zero, &mut result) },
            STATUS_DIVIDE_BY_ZERO
        );
        let default = Region3int16::new(None, None).unwrap();
        assert_eq!(unsafe { rove_region3int16_equal(&default, &default) }, 1);
        assert_eq!(
            unsafe { rove_region3int16_equal(ptr::null(), &default) },
            -STATUS_NULL
        );
    }
}
