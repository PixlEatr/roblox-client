# runtime stuff

build from the repo root with `python3.12 runtime/build.py`. it uses the bundled Luau source.

```sh
runtime/build/luau-host --source runtime/smoke.luau -- 2 5
.venv/bin/python scripts/runtime_preflight.py
```

preflight checks the dependencies offline. build the Rust worker too, as shown in the main readme.

`runtime/build/luau-host` handles individual jobs. `rust-client/target/release/headless-luau` keeps the world and scripts running. edits are local unless a supported replication path sends them.

extra tools:

- `python3.12 scripts/build_desktop.py` builds `~/Applications/Rove.app` on Apple Silicon. keep the repo where it is after building.
- `.venv/bin/python tests/preview.py --showcase --allow-webgl` opens an offline preview fixture. built-in Roblox mesh/texture files need the Roblox app installed.
- `:saveinstance map.rbxlx` exports the runtime's available world to `rove exports/` using the bundled USSI script.
- `python3.12 scripts/build_decompiler.py` downloads and builds the pinned Tovek decompiler. needs Rust nightly. decompilation is optional.

`--framework-init` is an old Nothingness-specific research option. it needs a local calibration file; the normal launcher only enables it when that file exists. it isn't a general join flag.

the optional external sUNC suite is not bundled. its expected source is listed in `tests/fixtures/sunc/manifest.json`.

this is a partial runtime, not an OS sandbox or a promise of Roblox compatibility.
