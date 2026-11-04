# Persistent Luau worker

For normal setup, use `rove.py setup` from the repository root. It builds this worker along with the C++ host and animation codec. See the [Windows, macOS, and Linux setup guide](../docs/setup.md).

To rebuild only this component after installing the prerequisites:

```sh
cargo build --release --locked --manifest-path rust-client/Cargo.toml
```

This builds the C++ bindings and bundled Luau libraries through CMake, then links them into `rust-client/target/release/headless-luau` (`headless-luau.exe` on Windows).

The session runtime starts and manages the worker. Launch Rove through `rove.py`; you normally do not need to start this binary yourself.

Windows uses native MSVC. For the optional MinGW cross-build configuration and its validation limits, see [Development](../docs/development.md#checks). Cross-compiling does not verify execution on Windows.
