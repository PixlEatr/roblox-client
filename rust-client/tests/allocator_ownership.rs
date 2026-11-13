#![cfg(all(target_os = "macos", feature = "allocator-mimalloc"))]

// Pull the package library link directives into this integration-test binary.
extern crate headless_luau;

use std::ffi::c_void;

#[global_allocator]
static TEST_ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

unsafe extern "C" {
    fn mi_is_in_heap_region(pointer: *const c_void) -> bool;
    fn rove_probe_malloc(size: usize) -> *mut c_void;
    fn rove_probe_realloc(pointer: *mut c_void, size: usize) -> *mut c_void;
    fn rove_probe_free(pointer: *mut c_void);
    fn rove_probe_new(size: usize) -> *mut c_void;
    fn rove_probe_delete(pointer: *mut c_void);
}

fn is_mimalloc_owned(pointer: *const c_void) -> bool {
    // `mi_is_in_heap_region` is the ownership probe under test and accepts an
    // arbitrary pointer. Do not use `mi_usable_size` on an unclassified pointer.
    unsafe { mi_is_in_heap_region(pointer) }
}

#[test]
fn rust_and_cpp_allocations_report_their_actual_mimalloc_ownership() {
    let rust_control = vec![0x5a_u8; 4096];
    let rust_owned = is_mimalloc_owned(rust_control.as_ptr().cast());
    assert!(
        rust_owned,
        "Rust global allocator control allocation is not in mimalloc"
    );

    let expected_native = cfg!(feature = "allocator-mimalloc-native");

    let malloc_ptr = unsafe { rove_probe_malloc(4096) };
    assert!(!malloc_ptr.is_null());
    let malloc_owned = is_mimalloc_owned(malloc_ptr);
    unsafe { rove_probe_free(malloc_ptr) };
    assert_eq!(
        malloc_owned, expected_native,
        "C++ malloc ownership did not match the configured mode"
    );

    let realloc_initial = unsafe { rove_probe_malloc(512) };
    assert!(!realloc_initial.is_null());
    let realloc_ptr = unsafe { rove_probe_realloc(realloc_initial, 8192) };
    if realloc_ptr.is_null() {
        // Standard realloc leaves the original allocation live on failure.
        unsafe { rove_probe_free(realloc_initial) };
        panic!("C++ realloc probe allocation failed");
    }
    let realloc_owned = is_mimalloc_owned(realloc_ptr);
    unsafe { rove_probe_free(realloc_ptr) };
    assert_eq!(
        realloc_owned, expected_native,
        "C++ realloc ownership did not match the configured mode"
    );

    let new_ptr = unsafe { rove_probe_new(4096) };
    assert!(!new_ptr.is_null());
    let new_owned = is_mimalloc_owned(new_ptr);
    unsafe { rove_probe_delete(new_ptr) };
    assert_eq!(
        new_owned, expected_native,
        "C++ operator new ownership did not match the configured mode"
    );
}
