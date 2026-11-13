animation codec
===============

small rust helper that reads an RBXM KeyframeSequence from stdin and writes
JSON clip data to stdout. it decodes data only; it does not run scripts.

build from the repo root:

  cargo build --release --locked --manifest-path animation-codec/Cargo.toml

run it with an asset you already have:

  animation-codec/target/release/animation-codec < animation.rbxm > animation.json

it exports keyframes, poses, facial NumberPose channels, markers, timing,
looping and priority. rig-data bytes are kept intact but are not interpreted.
CurveAnimation and unsupported structures return errors.

Cargo.lock pins the rbx_binary and rbx_dom_weak dependencies.
