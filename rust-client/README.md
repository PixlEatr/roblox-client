# persistent luau worker

```sh
cargo build --release --locked --manifest-path rust-client/Cargo.toml
```

builds the C++ bindings and bundled Luau libraries through CMake, then links them into `target/release/headless-luau`.

normally started by `scripts/terminal_client.py`. use the root readme for setup. cross compilation isn't supported yet.
