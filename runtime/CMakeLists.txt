cmake_minimum_required(VERSION 3.16)
project(LocalLuauHost LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
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
add_executable(luau-host
    luau_host.cpp random_compat.cpp engine_bindings.cpp world_bindings.cpp
    source_bindings.cpp source_scheduler.cpp authored_bindings.cpp spatial_values.cpp
    physical_properties.cpp color3_bindings.cpp vector2_bindings.cpp udim_bindings.cpp
    range_bindings.cpp brickcolor_bindings.cpp datetime_bindings.cpp sequence_bindings.cpp
    closure_bindings.cpp json_bindings.cpp movement_bindings.cpp)
target_include_directories(luau-host PRIVATE vendor "${LUAU}/VM/src" "${CMAKE_CURRENT_BINARY_DIR}")
add_executable(luau-source-to-bytecode bytecode_fixture.cpp)
foreach(host luau-host luau-source-to-bytecode)
    target_link_libraries(${host} PRIVATE Luau.Compiler Luau.VM Luau.Bytecode Luau.Config Luau.Ast Luau.Common Threads::Threads)
endforeach()
