# runtime stuff

For normal setup, use `python rove.py setup` from the repo root; see [the setup guide](../docs/setup.md). The commands below are optional developer tools. The standalone C++ build is `python runtime/build.py` and uses bundled Luau source.

```sh
runtime/build/luau-host --source runtime/smoke.luau -- 2 5
.venv/bin/python scripts/runtime_preflight.py
```

Preflight checks the dependencies offline. `rove.py setup` builds both the C++ and Rust workers automatically.

`runtime/build/luau-host` handles individual jobs. `rust-client/target/release/headless-luau` keeps the world and scripts running. edits are local unless a supported replication path sends them.

new bits include more engine bindings, GUI layouts, fonts and input handling. still a work in progress.

extra tools:

- `python3.12 scripts/build_desktop.py` builds `~/Applications/Rove.app` on Apple Silicon. keep the repo where it is after building.
- `.venv/bin/python tests/preview.py --showcase --allow-webgl` opens an offline preview fixture. built-in Roblox mesh/texture files need the Roblox app installed.
- `:saveinstance map.rbxlx` exports the runtime's available world to `rove exports/` using the bundled USSI script.
- `python3.12 scripts/build_decompiler.py` downloads and builds the pinned Tovek decompiler. needs Rust nightly. decompilation is optional.

`--framework-init` is an old Nothingness-specific research option. it needs a local calibration file; the normal launcher only enables it when that file exists. it isn't a general join flag.

the optional external sUNC suite is not bundled. its expected source is listed in `tests/fixtures/sunc/manifest.json`.

this is a partial runtime, not an OS sandbox or a promise of Roblox compatibility.
