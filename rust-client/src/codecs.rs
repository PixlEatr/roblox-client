//! Binary-safe source-script codecs. These callbacks never access a Lua state.
use base64::{engine::general_purpose::STANDARD, Engine};
use blake2::{digest::consts::U32, Blake2b, Digest};
use std::{
    ffi::{c_char, c_int, c_void},
    ptr, slice,
    sync::OnceLock,
};

// Keep zstd decompression bounded at 32 MiB. HTTP Base64 uses the separate
// operation input limits in test_limits so 16 MiB responses can be decoded.
const MAX_CODEC_SIZE: usize = 32 * 1024 * 1024;

type CompressBound = unsafe extern "C" fn(usize) -> usize;
type Compress = unsafe extern "C" fn(*mut u8, usize, *const u8, usize, c_int) -> usize;
type Decompress = unsafe extern "C" fn(*mut u8, usize, *const u8, usize) -> usize;
type FrameContentSize = unsafe extern "C" fn(*const u8, usize) -> u64;
type IsError = unsafe extern "C" fn(usize) -> u32;

struct Zstd {
    compress_bound: CompressBound,
    compress: Compress,
    decompress: Decompress,
    frame_content_size: FrameContentSize,
    is_error: IsError,
}

#[cfg(unix)]
unsafe extern "C" {
    fn dlopen(filename: *const c_char, flag: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
}

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn LoadLibraryA(filename: *const c_char) -> *mut c_void;
    fn GetProcAddress(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
}
#[cfg(windows)]
unsafe fn dlopen(filename: *const c_char, _flag: c_int) -> *mut c_void {
    LoadLibraryA(filename)
}
#[cfg(windows)]
unsafe fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void {
    GetProcAddress(handle, symbol)
}

fn zstd() -> Option<&'static Zstd> {
    static ZSTD: OnceLock<Option<Zstd>> = OnceLock::new();
    ZSTD.get_or_init(|| {
        for library in [
            b"/opt/homebrew/lib/libzstd.dylib\0".as_slice(),
            b"/usr/local/lib/libzstd.dylib\0",
            b"libzstd.dylib\0",
            b"libzstd.so.1\0",
            b"libzstd.so\0",
            b"libzstd.dll\0",
            b"zstd.dll\0",
        ] {
            let handle = unsafe { dlopen(library.as_ptr().cast(), 2) };
            if handle.is_null() {
                continue;
            }
            unsafe {
                let compress_bound = dlsym(handle, c"ZSTD_compressBound".as_ptr());
                let compress = dlsym(handle, c"ZSTD_compress".as_ptr());
                let decompress = dlsym(handle, c"ZSTD_decompress".as_ptr());
                let frame_content_size = dlsym(handle, c"ZSTD_getFrameContentSize".as_ptr());
                let is_error = dlsym(handle, c"ZSTD_isError".as_ptr());
                if compress_bound.is_null()
                    || compress.is_null()
                    || decompress.is_null()
                    || frame_content_size.is_null()
                    || is_error.is_null()
                {
                    continue;
                }
                return Some(Zstd {
                    compress_bound: std::mem::transmute::<*mut c_void, CompressBound>(
                        compress_bound,
                    ),
                    compress: std::mem::transmute::<*mut c_void, Compress>(compress),
                    decompress: std::mem::transmute::<*mut c_void, Decompress>(decompress),
                    frame_content_size: std::mem::transmute::<*mut c_void, FrameContentSize>(
                        frame_content_size,
                    ),
                    is_error: std::mem::transmute::<*mut c_void, IsError>(is_error),
                });
            }
        }
        None
    })
    .as_ref()
}

/// # Safety
/// Input must address `length` readable bytes and output slots must be writable.
/// Each successful output allocation must be released exactly once.
pub unsafe extern "C" fn apply(
    operation: u32,
    input: *const u8,
    length: usize,
    output: *mut *mut u8,
    output_length: *mut usize,
    parameter: c_int,
) -> i32 {
    if output.is_null()
        || output_length.is_null()
        || input.is_null()
        || length > crate::test_limits::codec(operation)
    {
        return 1;
    }
    // SAFETY: the C++ binding supplies readable VM data and writable output
    // slots, and retains the input until this synchronous call returns.
    let input = unsafe { slice::from_raw_parts(input, length) };
    let result = match operation {
        0 => STANDARD.encode(input).into_bytes(),
        1 => match STANDARD.decode(input) {
            Ok(result) => result,
            Err(_) => return 2,
        },
        2 => {
            let Some(zstd) = zstd() else { return 3 };
            // SAFETY: the output allocation has the capacity required by zstd;
            // input remains live for this synchronous call. Legacy calls pass
            // zero for the library default, EncodingService passes a level.
            let capacity = unsafe { (zstd.compress_bound)(input.len()) };
            let mut result = vec![0; capacity];
            let written = unsafe {
                (zstd.compress)(
                    result.as_mut_ptr(),
                    result.len(),
                    input.as_ptr(),
                    input.len(),
                    parameter,
                )
            };
            if unsafe { (zstd.is_error)(written) } != 0 {
                return 2;
            }
            result.truncate(written);
            result
        }
        3 | 4 | 5 => {
            let Some(zstd) = zstd() else { return 3 };
            let capacity = if operation == 3 {
                MAX_CODEC_SIZE
            } else {
                // Both Zstd sentinel values for unknown size and invalid headers
                // are greater than the output cap.
                let size = unsafe { (zstd.frame_content_size)(input.as_ptr(), input.len()) };
                if size > MAX_CODEC_SIZE as u64 {
                    return 2;
                }
                size as usize
            };
            if operation == 4 {
                (capacity as u32).to_le_bytes().to_vec()
            } else {
                let mut result = vec![0; capacity];
                // SAFETY: both buffers are valid for the supplied lengths.
                let written = unsafe {
                    (zstd.decompress)(
                        result.as_mut_ptr(),
                        result.len(),
                        input.as_ptr(),
                        input.len(),
                    )
                };
                if unsafe { (zstd.is_error)(written) } != 0
                    || (operation == 5 && written != capacity)
                {
                    return 2;
                }
                result.truncate(written);
                result
            }
        }
        // Both EncodingService hash methods use the same binary digest.
        // The parameter is the Roblox Enum.HashAlgorithm value.
        6 | 7 => match parameter {
            0 => Blake2b::<U32>::digest(input).to_vec(),
            1 => blake3::hash(input).as_bytes().to_vec(),
            2 => md5::Md5::digest(input).to_vec(),
            3 => sha1::Sha1::digest(input).to_vec(),
            4 => sha2::Sha256::digest(input).to_vec(),
            _ => return 2,
        },
        100 => crate::network::apply(input),
        101 => crate::host_io::set_clipboard(input),
        _ => return 1,
    };
    let mut result = result.into_boxed_slice();
    unsafe {
        *output = result.as_mut_ptr();
        *output_length = result.len();
    }
    std::mem::forget(result);
    0
}

/// # Safety
/// Return the original allocation and length from `apply`, exactly once.
pub unsafe extern "C" fn release(output: *mut u8, length: usize) {
    if !output.is_null() {
        // SAFETY: the native caller returns each allocation exactly once with
        // the original length, after copying it into a VM-owned string.
        unsafe {
            drop(Box::from_raw(ptr::slice_from_raw_parts_mut(output, length)));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn binary_and_empty_buffers_roundtrip_and_invalid_input_fails() {
        for input in [b"".as_slice(), b"abc", b"\x00\xff\xfehello"] {
            let mut encoded = ptr::null_mut();
            let mut count = 0;
            assert_eq!(
                unsafe { apply(0, input.as_ptr(), input.len(), &mut encoded, &mut count, 0) },
                0
            );
            let mut decoded = ptr::null_mut();
            let mut decoded_count = 0;
            assert_eq!(
                unsafe { apply(1, encoded, count, &mut decoded, &mut decoded_count, 0) },
                0
            );
            assert_eq!(
                unsafe { slice::from_raw_parts(decoded, decoded_count) },
                input
            );
            unsafe {
                release(encoded, count);
                release(decoded, decoded_count);
            }
        }
        let mut output = ptr::null_mut();
        let mut length = 0;
        assert_eq!(
            unsafe { apply(1, b"??".as_ptr(), 2, &mut output, &mut length, 0) },
            2
        );
    }

    #[test]
    fn base64_roundtrips_payloads_larger_than_the_old_codec_cap() {
        let input = vec![b'x'; 300 * 1024];
        let mut encoded = ptr::null_mut();
        let mut encoded_length = 0;
        assert_eq!(
            unsafe {
                apply(
                    0,
                    input.as_ptr(),
                    input.len(),
                    &mut encoded,
                    &mut encoded_length,
                    0,
                )
            },
            0
        );
        assert!(encoded_length > 256 * 1024);

        let mut decoded = ptr::null_mut();
        let mut decoded_length = 0;
        assert_eq!(
            unsafe {
                apply(
                    1,
                    encoded,
                    encoded_length,
                    &mut decoded,
                    &mut decoded_length,
                    0,
                )
            },
            0
        );
        assert_eq!(decoded_length, input.len());
        assert_eq!(
            unsafe { slice::from_raw_parts(decoded, decoded_length) },
            input
        );
        unsafe {
            release(encoded, encoded_length);
            release(decoded, decoded_length);
        }
    }
}
