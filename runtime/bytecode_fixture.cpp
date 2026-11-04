#include <Luau/Compiler.h>

#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <filesystem>
#include "platform_support.hpp"

int compile_main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: luau-source-to-bytecode SOURCE_PATH OUTPUT_PATH\n";
        return 2;
    }
    std::ifstream input(std::filesystem::u8path(argv[1]), std::ios::binary | std::ios::ate);
    if (!input) return 3;
    auto size = input.tellg();
    if (size < 0 || size > 128 * 1024) return 3;
    std::string source(static_cast<size_t>(size), '\0');
    input.seekg(0);
    if (!source.empty() && !input.read(source.data(), static_cast<std::streamsize>(source.size()))) return 3;
    std::string bytecode = Luau::compile(source);
    if (bytecode.size() > 128 * 1024) return 4;
    std::ofstream out(std::filesystem::u8path(argv[2]), std::ios::binary | std::ios::trunc);
    if (!out || !out.write(bytecode.data(), static_cast<std::streamsize>(bytecode.size()))) return 4;
    return 0;
}
#ifdef _WIN32
int wmain(int argc,wchar_t** argv) { return host_wide_main(argc,argv,compile_main); }
#else
int main(int argc,char** argv) { return compile_main(argc,argv); }
#endif
