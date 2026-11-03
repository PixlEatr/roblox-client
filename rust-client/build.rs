use std::{env, path::PathBuf, process::Command};

fn checked(command: &mut Command) {
    let status = command.status().expect("could not start native build tool");
    assert!(status.success(), "native build failed: {command:?}");
}

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap())
        .parent()
        .unwrap()
        .to_path_buf();
    let runtime = root.join("runtime");
    let vendor = root.join("vendor/luau");
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let archives = out.join("luau");
    checked(Command::new("cmake")
        .arg("-S").arg(&runtime).arg("-B").arg(&archives)
        .arg("-DCMAKE_BUILD_TYPE=Release"));
    let jobs = env::var("NUM_JOBS").unwrap_or("2".into());
    checked(Command::new("cmake")
        .arg("--build").arg(&archives).args(["--target", "luau-libraries", "--parallel", &jobs]));
    println!("cargo:rerun-if-changed={}", runtime.join("CMakeLists.txt").display());
    let registry_path = runtime.join("class-constructors-745.json");
    let registry =
        std::fs::read_to_string(&registry_path).expect("missing class constructor registry");
    assert!(!registry.contains(")ROBLOX\""));
    std::fs::write(
        out.join("class_registry_data.hpp"),
        format!("static constexpr const char* kConstructorJson = R\"ROBLOX({registry})ROBLOX\";\n"),
    )
    .expect("cannot generate class constructor header");
    println!("cargo:rerun-if-changed={}", registry_path.display());
    assert_eq!(
        env::var("HOST").unwrap(),
        env::var("TARGET").unwrap(),
        "cross compilation requires rebuilding the pinned native Luau archives"
    );
    let mut objects = Vec::new();
    for source in [
        "luau_host",
        "world_bindings",
        "engine_bindings",
        "random_compat",
        "source_bindings",
        "source_scheduler",
        "authored_bindings",
        "spatial_values",
        "physical_properties",
        "color3_bindings",
        "vector2_bindings",
        "udim_bindings",
        "range_bindings",
        "brickcolor_bindings",
        "datetime_bindings",
        "sequence_bindings",
        "closure_bindings",
        "json_bindings",
        "movement_bindings",
    ] {
        let file = runtime.join(format!("{source}.cpp"));
        let object = out.join(format!("{source}.o"));
        println!("cargo:rerun-if-changed={}", file.display());
        let mut compiler = Command::new(env::var_os("CXX").unwrap_or("c++".into()));
        compiler
            .args(["-std=c++17", "-O2", "-DNDEBUG", "-DLOCAL_LUAU_FFI", "-c"])
            .arg(&file)
            .arg("-o")
            .arg(&object);
        for include in ["VM", "Compiler", "Ast", "Config", "Common"] {
            compiler.arg("-I").arg(vendor.join(include).join("include"));
        }
        compiler.arg("-I").arg(runtime.join("vendor"));
        compiler.arg("-I").arg(vendor.join("VM/src"));
        compiler.arg("-I").arg(&out);
        checked(&mut compiler);
        objects.push(object);
    }
    for header in [
        "world_bindings.hpp",
        "collection_bindings.inc",
        "client_service_bindings.inc",
        "client_service_state.hpp",
        "tween_bindings.inc",
        "physics_force_bindings.inc",
        "humanoid_states.inc",
        "cframe_transforms.inc",
        "cframe_orientation.inc",
        "vector3_methods.inc",
        "vector_math_compat.hpp",
        "smooth_damp.inc",
        "input_capabilities.inc",
        "input_state.hpp",
        "context_action_state.hpp",
        "context_action_bindings.inc",
        "input_bindings.inc",
        "body_position_bindings.inc",
        "linear_velocity_bindings.inc",
        "input_enum_data.inc",
        "gui_layout.inc",
        "render_surface.inc",
        "geometry_enums.inc",
        "enum_root.inc",
        "export_enums.inc",
        "security_capabilities.inc",
        "service_lookup_events.inc",
        "engine_pose.inc",
        "animation_state.hpp",
        "tween_state.hpp",
        "render_step_state.hpp",
        "render_step_bindings.inc",
        "tween_playback.inc",
        "animation_bindings.inc",
        "bindable_functions.inc",
        "remote_client_callbacks.inc",
        "script_thread_integrity.hpp",
        "protected_script_string.hpp",
        "protected_script_load.hpp",
        "sha256.hpp",
        "engine_bindings.hpp",
        "random_compat.hpp",
        "vendor/json.hpp",
        "source_bindings.hpp",
        "local_source_trace.hpp",
        "local_compat.inc",
        "memory_categories.inc",
        "vm_debug_compat.inc",
        "network_compat.inc",
        "host_io_compat.inc",
        "http_instance.inc",
        "local_test_limits.hpp",
        "drawing_compat.inc",
        "extended_crypto.inc",
        "instance_compat.inc",
        "local_workspace.inc",
        "source_scheduler.hpp",
        "script_execution.hpp",
        "authored_bindings.hpp",
        "integer_vectors.inc",
        "spatial_values.hpp",
        "physical_properties.hpp",
        "http_url_encode.inc",
        "ray_bindings.inc",
        "camera_projection.inc",
        "model_pivots.inc",
        "part_transforms.inc",
        "attachment_transforms.inc",
        "joint_graph.inc",
        "joint_poses.inc",
        "part_motion_queries.inc",
        "part_mass_properties.inc",
        "material_densities.inc",
        "color3_bindings.hpp",
        "vector2_bindings.hpp",
        "udim_bindings.hpp",
        "range_bindings.hpp",
        "brickcolor_bindings.hpp",
        "datetime_bindings.hpp",
        "sequence_bindings.hpp",
        "closure_bindings.hpp",
        "json_bindings.hpp",
        "movement_bindings.hpp",
        "humanoid_jump.inc",
        "humanoid_health.inc",
    ] {
        println!("cargo:rerun-if-changed={}", runtime.join(header).display());
    }
    println!("cargo:rerun-if-changed={}", vendor.display());
    println!("cargo:rerun-if-env-changed=CXX");
    println!("cargo:rerun-if-env-changed=AR");
    checked(
        Command::new(env::var_os("AR").unwrap_or("ar".into()))
            .arg("crs")
            .arg(out.join("libheadless_luau_bridge.a"))
            .args(objects),
    );
    println!("cargo:rustc-link-search=native={}", out.display());
    println!("cargo:rustc-link-lib=static=headless_luau_bridge");
    println!("cargo:rustc-link-search=native={}", archives.display());
    for lib in [
        "Luau.Compiler",
        "Luau.VM",
        "Luau.Bytecode",
        "Luau.Config",
        "Luau.Ast",
        "Luau.Common",
    ] {
        let file = archives.join(format!("lib{lib}.a"));
        assert!(
            file.is_file(),
            "native build did not produce {}",
            file.display()
        );
        println!("cargo:rerun-if-changed={}", file.display());
        println!("cargo:rustc-link-lib=static={lib}");
    }
    if env::var("CARGO_CFG_TARGET_OS").unwrap() == "macos" {
        println!("cargo:rustc-link-lib=c++");
    } else {
        println!("cargo:rustc-link-lib=stdc++");
    }
    println!("cargo:rustc-link-lib=pthread");
}
