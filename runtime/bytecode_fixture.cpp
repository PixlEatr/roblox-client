#include <Luau/Compiler.h>

#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: luau-source-to-bytecode SOURCE_PATH OUTPUT_PATH\n";
        return 2;
    }
    std::ifstream input(argv[1], std::ios::binary | std::ios::ate);
    if (!input) return 3;
    auto size = input.tellg();
    if (size < 0 || size > 128 * 1024) return 3;
    std::string source(static_cast<size_t>(size), '\0');
    input.seekg(0);
    if (!source.empty() && !input.read(source.data(), static_cast<std::streamsize>(source.size()))) return 3;
    std::string bytecode = Luau::compile(source);
    if (bytecode.size() > 128 * 1024) return 4;
    std::ofstream out(argv[2], std::ios::binary | std::ios::trunc);
    if (!out || !out.write(bytecode.data(), static_cast<std::streamsize>(bytecode.size()))) return 4;
    return 0;
}
