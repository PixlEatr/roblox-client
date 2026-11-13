#include <cstddef>
#include <cstdlib>
#include <new>

// These functions are deliberately isolated in a separate static archive.
// Rust integration tests pull the archive in only when exercising the opt-in
// allocator probe; production runtime code never calls them.
extern "C" void* rove_probe_malloc(std::size_t size) noexcept {
    return std::malloc(size);
}

extern "C" void* rove_probe_realloc(void* pointer, std::size_t size) noexcept {
    return std::realloc(pointer, size);
}

extern "C" void rove_probe_free(void* pointer) noexcept {
    std::free(pointer);
}

extern "C" void* rove_probe_new(std::size_t size) noexcept {
    try {
        return ::operator new(size, std::nothrow);
    } catch (...) {
        return nullptr;
    }
}

extern "C" void rove_probe_delete(void* pointer) noexcept {
    ::operator delete(pointer);
}
