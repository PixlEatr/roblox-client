cmake_minimum_required(VERSION 3.16)
project(LocalLuauHost LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Cargo.toml is the single package-version source for the Rust worker and the
# C++ VM bridge. Generate the bridge constant from that same manifest.
set(ROVE_CARGO_MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/../rust-client/Cargo.toml")
file(STRINGS "${ROVE_CARGO_MANIFEST}" ROVE_CARGO_VERSION_LINE
    REGEX "^version = \"[0-9]+[.][0-9]+[.][0-9]+\"$")
list(LENGTH ROVE_CARGO_VERSION_LINE ROVE_CARGO_VERSION_COUNT)
if(NOT ROVE_CARGO_VERSION_COUNT EQUAL 1)
    message(FATAL_ERROR "Expected one semantic package version in ${ROVE_CARGO_MANIFEST}")
endif()
string(REGEX REPLACE "^version = \"([^\"]+)\"$" "\\1"
    ROVE_BUILD_VERSION "${ROVE_CARGO_VERSION_LINE}")
configure_file("${CMAKE_CURRENT_SOURCE_DIR}/build_version.hpp.in"
    "${CMAKE_CURRENT_BINARY_DIR}/build_version.hpp" @ONLY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${ROVE_CARGO_MANIFEST}" "${CMAKE_CURRENT_SOURCE_DIR}/build_version.hpp.in")

if(MSVC)
    add_compile_options(/utf-8 /bigobj)
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS NOMINMAX)
endif()
set(LUAU "${CMAKE_CURRENT_SOURCE_DIR}/../vendor/luau")

foreach(lib Common Ast Bytecode Compiler Config VM)
    file(GLOB sources CONFIGURE_DEPENDS "${LUAU}/${lib}/src/*.cpp")
    add_library(Luau.${lib} STATIC ${sources})
    foreach(headers Common Ast Bytecode Compiler Config VM)
        target_include_directories(Luau.${lib} PUBLIC "${LUAU}/${headers}/include")
    endforeach()
endforeach()
add_custom_target(luau-libraries DEPENDS Luau.Compiler Luau.VM Luau.Bytecode Luau.Config Luau.Ast Luau.Common)

set(registry_path "${CMAKE_CURRENT_SOURCE_DIR}/class-constructors-745.json")
set(registry_generator "${CMAKE_CURRENT_SOURCE_DIR}/scripts_generate_class_registry.py")
find_package(Python3 COMPONENTS Interpreter REQUIRED)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${registry_path}" "${registry_generator}")
execute_process(
    COMMAND "${Python3_EXECUTABLE}" -B "${registry_generator}" "${registry_path}"
            "${CMAKE_CURRENT_BINARY_DIR}/class_registry_data.hpp"
    RESULT_VARIABLE registry_generator_status
    ERROR_VARIABLE registry_generator_error)
if(NOT registry_generator_status EQUAL 0)
    message(FATAL_ERROR "Failed to index constructor registry: ${registry_generator_error}")
endif()

find_package(Threads REQUIRED)
add_library(rove_lz4 STATIC "${CMAKE_CURRENT_SOURCE_DIR}/../vendor/lz4/lz4.c")
target_include_directories(rove_lz4 PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/../vendor/lz4")
set(HOST_SOURCES
    luau_host.cpp actor_runtime.cpp shared_table_bindings.cpp legacy_value_bindings.cpp random_compat.cpp engine_bindings.cpp world_bindings.cpp
    source_bindings.cpp source_scheduler.cpp authored_bindings.cpp spatial_values.cpp
    physical_properties.cpp color3_bindings.cpp vector2_bindings.cpp udim_bindings.cpp
    range_bindings.cpp brickcolor_bindings.cpp datetime_bindings.cpp sequence_bindings.cpp
    closure_bindings.cpp json_bindings.cpp movement_bindings.cpp audio_asset.cpp rust_engine_bridge.cpp rust_world_view.cpp)
add_executable(luau-host ${HOST_SOURCES})
add_library(headless_luau_bridge STATIC ${HOST_SOURCES})
target_compile_definitions(headless_luau_bridge PRIVATE LOCAL_LUAU_FFI)
# Keep artifact paths stable for Cargo and Python, including multi-config generators.
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}")
foreach(target rove_lz4 headless_luau_bridge luau-host Luau.Common Luau.Ast Luau.Bytecode Luau.Compiler Luau.Config Luau.VM)
    set_target_properties(${target} PROPERTIES
        ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
        ARCHIVE_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_BINARY_DIR}"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
        RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_BINARY_DIR}")
endforeach()
foreach(host luau-host headless_luau_bridge)
    target_include_directories(${host} PRIVATE vendor "${LUAU}/VM/src" "${CMAKE_CURRENT_BINARY_DIR}")
    target_link_libraries(${host} PRIVATE ${CMAKE_DL_LIBS})
    if(WIN32)
        target_link_libraries(${host} PRIVATE bcrypt)
    endif()
    if(NOT APPLE)
        target_link_libraries(${host} PRIVATE rove_lz4)
    endif()
endforeach()
add_executable(luau-source-to-bytecode bytecode_fixture.cpp)
if(MINGW)
    target_link_options(luau-host PRIVATE -municode)
    target_link_options(luau-source-to-bytecode PRIVATE -municode)
endif()
foreach(host luau-host headless_luau_bridge luau-source-to-bytecode)
    target_link_libraries(${host} PRIVATE Luau.Compiler Luau.VM Luau.Bytecode Luau.Config Luau.Ast Luau.Common Threads::Threads)
endforeach()

set_target_properties(luau-source-to-bytecode PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_BINARY_DIR}")

# Explicit opt-in target: normal client builds do not compile the fixture.
# Checks remain active in Release builds; the test does not use assert().
add_executable(world-storage-isolation EXCLUDE_FROM_ALL tests/world_storage_isolation.cpp)
target_include_directories(world-storage-isolation PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" vendor)
target_link_libraries(world-storage-isolation PRIVATE headless_luau_bridge
    Luau.Compiler Luau.VM Luau.Bytecode Luau.Config Luau.Ast Luau.Common Threads::Threads)

add_executable(shared-json-construction EXCLUDE_FROM_ALL tests/shared_json_construction.cpp)
target_include_directories(shared-json-construction PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" vendor)

add_custom_target(constructor-registry-generator-tests
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_class_registry_generator.py"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")

add_executable(constructor-registry-lazy EXCLUDE_FROM_ALL tests/constructor_registry_lazy.cpp)
target_include_directories(constructor-registry-lazy PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" vendor)
target_compile_definitions(constructor-registry-lazy PRIVATE
    "CONSTRUCTOR_REGISTRY_PATH=\"${registry_path}\"")
target_link_libraries(constructor-registry-lazy PRIVATE headless_luau_bridge
    Luau.Compiler Luau.VM Luau.Bytecode Luau.Config Luau.Ast Luau.Common Threads::Threads)

add_executable(rust-world-provider-bundle EXCLUDE_FROM_ALL tests/rust_world_provider_bundle.cpp)
target_include_directories(rust-world-provider-bundle PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" vendor)
target_link_libraries(rust-world-provider-bundle PRIVATE headless_luau_bridge
    Luau.Compiler Luau.VM Luau.Bytecode Luau.Config Luau.Ast Luau.Common Threads::Threads)

# Used only by the opt-in Rust allocator ownership integration probe. The
# archive is linked into the Rust test binary only when referenced there.
add_library(mimalloc_ownership_probe STATIC EXCLUDE_FROM_ALL tests/mimalloc_ownership_probe.cpp)
set_target_properties(mimalloc_ownership_probe PROPERTIES
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
    ARCHIVE_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_BINARY_DIR}")
