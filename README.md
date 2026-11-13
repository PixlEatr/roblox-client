# rove

a roblox client i'm working on in rust, using the official luau vm. it has world replication, scripting, local physics and a browser preview. the engine bindings still have c++ in them.

you'll need rust 1.88+, cmake, python 3 for the build step, and a c++17 compiler. run these from this folder:

```sh
cargo build --release --locked --manifest-path rust-client/Cargo.toml --bin rove
rust-client/target/release/rove run --snapshot tests/fixtures/world_snapshot.json --script examples/hello.luau --json
```

that runs a tiny script against a local test world. for the browser preview:

```sh
rust-client/target/release/rove serve --snapshot tests/fixtures/world_snapshot.json
```

`client-core` holds the world and networking, `rust-client` runs the client, `runtime` has the native bindings, and `ui` has the preview. `animation-codec` is the separate animation helper.

`--lightweight` is optional on live joins. it drops visual/audio asset properties to save ram, so scripts can't read those properties in that mode. this is still a work in progress.

to run the rust tests:

```sh
cargo test --locked --manifest-path client-core/Cargo.toml --features transport-test
cargo test --locked --manifest-path rust-client/Cargo.toml
```

upstream code and notices are listed in [THIRD_PARTY.md](THIRD_PARTY.md).
