#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace source_crypto {
using Digest = std::array<std::uint8_t, 32>;

inline std::uint32_t rotate_right(std::uint32_t value, unsigned bits) {
    return (value >> bits) | (value << (32 - bits));
}

inline Digest sha256(const std::uint8_t* input, std::size_t length) {
    static constexpr std::array<std::uint32_t, 64> constants = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
    };
    std::vector<std::uint8_t> data(input, input + length);
    const std::uint64_t bit_length = static_cast<std::uint64_t>(length) * 8;
    data.push_back(0x80);
    while (data.size() % 64 != 56) data.push_back(0);
    for (int shift = 56; shift >= 0; shift -= 8) data.push_back(static_cast<std::uint8_t>(bit_length >> shift));
    std::array<std::uint32_t, 8> state = {
        0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19,
    };
    for (std::size_t offset = 0; offset < data.size(); offset += 64) {
        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16; ++index) {
            const auto* block = data.data() + offset + index * 4;
            words[index] = (static_cast<std::uint32_t>(block[0]) << 24)
                | (static_cast<std::uint32_t>(block[1]) << 16)
                | (static_cast<std::uint32_t>(block[2]) << 8) | block[3];
        }
        for (std::size_t index = 16; index < 64; ++index) {
            const auto s0 = rotate_right(words[index-15], 7) ^ rotate_right(words[index-15], 18) ^ (words[index-15] >> 3);
            const auto s1 = rotate_right(words[index-2], 17) ^ rotate_right(words[index-2], 19) ^ (words[index-2] >> 10);
            words[index] = words[index-16] + s0 + words[index-7] + s1;
        }
        auto a=state[0];auto b=state[1];auto c=state[2];auto d=state[3];
        auto e=state[4];auto f=state[5];auto g=state[6];auto h=state[7];
        for (std::size_t index = 0; index < 64; ++index) {
            const auto sum1 = rotate_right(e,6) ^ rotate_right(e,11) ^ rotate_right(e,25);
            const auto choose = (e & f) ^ (~e & g);
            const auto first = h + sum1 + choose + constants[index] + words[index];
            const auto sum0 = rotate_right(a,2) ^ rotate_right(a,13) ^ rotate_right(a,22);
            const auto majority = (a & b) ^ (a & c) ^ (b & c);
            const auto second = sum0 + majority;
            h=g;g=f;f=e;e=d+first;d=c;c=b;b=a;a=first+second;
        }
        state[0]+=a;state[1]+=b;state[2]+=c;state[3]+=d;
        state[4]+=e;state[5]+=f;state[6]+=g;state[7]+=h;
    }
    Digest digest{};
    for (std::size_t index = 0; index < state.size(); ++index) {
        digest[index*4] = static_cast<std::uint8_t>(state[index] >> 24);
        digest[index*4+1] = static_cast<std::uint8_t>(state[index] >> 16);
        digest[index*4+2] = static_cast<std::uint8_t>(state[index] >> 8);
        digest[index*4+3] = static_cast<std::uint8_t>(state[index]);
    }
    return digest;
}

inline Digest sha256(const std::string& input) {
    return sha256(reinterpret_cast<const std::uint8_t*>(input.data()), input.size());
}

inline Digest hmac_sha256(const std::string& input, const std::string& key) {
    std::string normalized = key;
    if (normalized.size() > 64) {
        const auto digest = sha256(normalized);
        normalized.assign(reinterpret_cast<const char*>(digest.data()), digest.size());
    }
    normalized.resize(64, 0);
    std::string inner(64, 0), outer(64, 0);
    for (std::size_t index = 0; index < 64; ++index) {
        inner[index] = static_cast<char>(static_cast<std::uint8_t>(normalized[index]) ^ 0x36);
        outer[index] = static_cast<char>(static_cast<std::uint8_t>(normalized[index]) ^ 0x5c);
    }
    inner += input;
    const auto inner_digest = sha256(inner);
    outer.append(reinterpret_cast<const char*>(inner_digest.data()), inner_digest.size());
    return sha256(outer);
}

inline std::string hex(const Digest& digest) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result(64, '0');
    for (std::size_t index = 0; index < digest.size(); ++index) {
        result[index*2] = digits[digest[index] >> 4];
        result[index*2+1] = digits[digest[index] & 15];
    }
    return result;
}
}
