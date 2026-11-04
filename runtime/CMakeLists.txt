cmake_minimum_required(VERSION 3.16)
project(LocalLuauHost LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
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

file(READ "${CMAKE_CURRENT_SOURCE_DIR}/class-constructors-745.json" registry)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/class-constructors-745.json")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/class_registry_data.hpp"
    "static constexpr const char* kConstructorJson = R\"ROBLOX(${registry})ROBLOX\";\n")

find_package(Threads REQUIRED)
add_library(rove_lz4 STATIC "${CMAKE_CURRENT_SOURCE_DIR}/../vendor/lz4/lz4.c")
target_include_directories(rove_lz4 PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/../vendor/lz4")
set(HOST_SOURCES
    luau_host.cpp random_compat.cpp engine_bindings.cpp world_bindings.cpp
    source_bindings.cpp source_scheduler.cpp authored_bindings.cpp spatial_values.cpp
    physical_properties.cpp color3_bindings.cpp vector2_bindings.cpp udim_bindings.cpp
    range_bindings.cpp brickcolor_bindings.cpp datetime_bindings.cpp sequence_bindings.cpp
    closure_bindings.cpp json_bindings.cpp movement_bindings.cpp)
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
