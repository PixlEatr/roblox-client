pub fn large() -> bool {
    std::env::var("RCR_LUAU_LARGE_TEST").as_deref() == Ok("1")
}
pub fn source(normal: usize) -> usize {
    normal
}
pub fn time_ms() -> u32 {
    if large() {
        30000
    } else {
        2000
    }
}
pub fn memory_mb() -> u32 {
    512
}
pub fn codec(operation: u32) -> usize {
    if matches!(operation, 0 | 1) {
        // 32 MiB accommodates the Base64 representation of a 16 MiB HTTP body.
        32 * 1024 * 1024
    } else {
        256 * 1024
    }
}
