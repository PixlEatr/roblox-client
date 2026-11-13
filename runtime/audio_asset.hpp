#pragma once
#include <cstddef>
#include <cstdint>
#include <future>
#include <chrono>

namespace audio_asset {
struct Info {
    double duration=0; double sample_rate=0; uint32_t channels=0; uint64_t frames=0;
    std::chrono::steady_clock::time_point available_at{};
};
// Validates and decodes a sample of the supplied asset. Never opens a device.
Info inspect(const char* data,size_t size);
std::shared_future<Info> prepare(const char* data,size_t size);
}
