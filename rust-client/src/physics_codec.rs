//! Compact position and velocity encoders matching the recovered native fields.
//!
//! These functions only encode bounded vectors. They do not implement a network
//! transport or authentication flow.

use std::fmt;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PhysicsCodecError {
    InvalidVector,
}

impl fmt::Display for PhysicsCodecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("vector components must be finite and have magnitude below 1e9")
    }
}

impl std::error::Error for PhysicsCodecError {}

fn checked_vector(input: [f64; 3]) -> Result<[f32; 3], PhysicsCodecError> {
    if !input.iter().all(|x| x.is_finite() && x.abs() < 1.0e9) {
        return Err(PhysicsCodecError::InvalidVector);
    }
    Ok(input.map(|x| x as f32))
}

/// Return `frexp(x).1`, clamped to the nonnegative compact-codec range.
///
/// Deriving this from the IEEE-754 representation avoids logarithm rounding at
/// exact powers of two, which select different wire widths.
fn frexp_exponent_clamped(x: f32, max: i32) -> i32 {
    let bits = x.to_bits() & 0x7fff_ffff;
    let encoded_exponent = ((bits >> 23) & 0xff) as i32;
    let exponent = if encoded_exponent != 0 {
        encoded_exponent - 127 + 1
    } else {
        let fraction = bits & 0x7f_ffff;
        if fraction == 0 {
            0
        } else {
            (31 - fraction.leading_zeros() as i32) - 148
        }
    };
    exponent.clamp(0, max)
}

fn quantize(component: f32, exponent: i32, scale: i32) -> i32 {
    let divisor = (2.0_f32).powi(exponent);
    let normalized = (component / divisor).clamp(-1.0, 1.0);
    let scaled = normalized * scale as f32;
    let rounded = scaled + if component >= 0.0 { 0.5_f32 } else { -0.5_f32 };
    rounded as i32
}

/// Encode a position using the compact 10-, 16-, or 21-bit component format.
pub fn encode_position(input: [f64; 3]) -> Result<Vec<u8>, PhysicsCodecError> {
    let vector = checked_vector(input)?;
    let maximum = vector.iter().fold(0.0_f32, |m, x| m.max(x.abs()));
    let exponent = frexp_exponent_clamped(maximum, 31);
    let width = if exponent <= 4 {
        10
    } else if exponent <= 10 {
        16
    } else {
        21
    };
    let scale = (1_i32 << width) - 1;
    let [x, y, z] = vector.map(|v| quantize(v, exponent, scale));
    let header = ((exponent as u8) << 3)
        | (if x < 0 { 4 } else { 0 })
        | (if y < 0 { 2 } else { 0 })
        | (if z < 0 { 1 } else { 0 });
    let mask = (1_u32 << width) - 1;
    let (x, y, z) = ((x as u32) & mask, (y as u32) & mask, (z as u32) & mask);
    let mut out = vec![header];
    match width {
        10 => out.extend_from_slice(&((x << 20) | (y << 10) | z).to_le_bytes()),
        16 => {
            out.extend_from_slice(&(x as u16).to_le_bytes());
            out.extend_from_slice(&(y as u16).to_le_bytes());
            out.extend_from_slice(&(z as u16).to_le_bytes());
        }
        21 => {
            out.extend_from_slice(&((x << 11) | (y >> 10)).to_le_bytes());
            out.extend_from_slice(&(z | ((y & 1023) << 21)).to_le_bytes());
        }
        _ => unreachable!(),
    }
    Ok(out)
}

/// Encode velocity; magnitudes below the native 0.001 cutoff use one zero byte.
pub fn encode_velocity(input: [f64; 3]) -> Result<Vec<u8>, PhysicsCodecError> {
    let vector = checked_vector(input)?;
    let maximum = vector.iter().fold(0.0_f32, |m, x| m.max(x.abs()));
    if maximum < 0.001_f32 {
        return Ok(vec![0]);
    }
    let exponent = frexp_exponent_clamped(maximum, 14);
    let [x, y, z] = vector.map(|v| quantize(v, exponent, 2047));
    let header = (((exponent as u32) << 4) | ((z as u32) & 15)) + 16;
    let body = ((x as u32) << 20) | (((y as u32) & 4095) << 8) | (((z as u32) >> 4) & 255);
    let mut out = vec![header as u8];
    out.extend_from_slice(&body.to_le_bytes());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::{encode_position, encode_velocity, PhysicsCodecError};

    fn bytes(hex: &str) -> Vec<u8> {
        hex.as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let digit = |c: u8| match c {
                    b'0'..=b'9' => c - b'0',
                    b'a'..=b'f' => c - b'a' + 10,
                    _ => panic!("invalid fixture hex"),
                };
                digit(pair[0]) * 16 + digit(pair[1])
            })
            .collect()
    }

    #[test]
    fn pinned_position_wire_fixtures() {
        let cases = [
            ([0.0, 0.0, 0.0], "0000000000"),
            ([1.0, -1.0, 0.5], "0a00010820"),
            ([16.0, -8.0, 2.0], "2a008000c00010"),
            ([1024.0, -256.0, 128.0], "5a0007008000000200"),
        ];
        for (input, expected) in cases {
            assert_eq!(
                encode_position(input).unwrap(),
                bytes(expected),
                "{input:?}"
            );
        }
    }

    #[test]
    fn position_width_boundaries_and_sign_flags() {
        assert_eq!(encode_position([15.999, -3.25, 0.5]).unwrap().len(), 5);
        assert_eq!(encode_position([16.0, -8.0, 2.0]).unwrap().len(), 7);
        assert_eq!(encode_position([1023.999, -511.0, 127.0]).unwrap().len(), 7);
        assert_eq!(encode_position([1024.0, -256.0, 128.0]).unwrap().len(), 9);
        let negative = encode_position([-16384.0, 8192.0, -4096.0]).unwrap();
        assert_eq!(negative.len(), 9);
        assert_eq!(negative[0] & 7, 5);
    }

    #[test]
    fn pinned_velocity_wire_fixtures_and_cutoff() {
        let cases = [
            ([0.0, 0.0, 0.0], "00"),
            ([0.0009, 0.0, 0.0], "00"),
            ([0.001, 0.0, 0.0], "1000002000"),
            ([1.0, -1.0, 0.5], "2020000c40"),
            ([-1.0, -2.0, -3.0], "31a0000ce0"),
        ];
        for (input, expected) in cases {
            assert_eq!(
                encode_velocity(input).unwrap(),
                bytes(expected),
                "{input:?}"
            );
        }
    }

    #[test]
    fn invalid_and_out_of_range_vectors_are_rejected() {
        for input in [
            [f64::NAN, 0.0, 0.0],
            [0.0, f64::INFINITY, 0.0],
            [0.0, f64::NEG_INFINITY, 0.0],
            [1.0e9, 0.0, 0.0],
            [-1.0e9, 0.0, 0.0],
        ] {
            assert_eq!(
                encode_position(input),
                Err(PhysicsCodecError::InvalidVector)
            );
            assert_eq!(
                encode_velocity(input),
                Err(PhysicsCodecError::InvalidVector)
            );
        }
    }

    #[test]
    fn largest_allowed_values_stay_within_codec_widths() {
        for value in [
            -999_999_999.0,
            -16_384.0,
            -0.001,
            0.001,
            16_384.0,
            999_999_999.0,
        ] {
            let vector = [value, -value, value / 3.0];
            let position = encode_position(vector).unwrap();
            let velocity = encode_velocity(vector).unwrap();
            assert!(matches!(position.len(), 5 | 7 | 9));
            assert!(matches!(velocity.len(), 1 | 5));
        }
    }
}
