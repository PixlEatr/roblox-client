Animation asset codec
=====================

This is a data-only KeyframeSequence loader used by scripts/animation_assets.py.
It links pinned rbx_binary 3.0.1 and rbx_dom_weak 4.2.0; Cargo.lock pins the
transitive dependencies. The upstream library is MIT licensed:
https://github.com/rojo-rbx/rbx-dom

Build and test from this directory:

  cargo build --release --locked
  cargo test --release --locked

The binary reads one uncompressed RBXM from stdin and writes JSON clip data.
The Python wrapper additionally handles bounded gzip decompression, public
asset delivery, approved HTTPS CDN redirects, and a five-second decoder limit.
It never attaches an account cookie. Unsupported formats and failed decodes
raise AnimationAssetError; they do not return an empty animation.

Keyframe time/name, pose paths, CFrame components, weights, easing enums,
markers, clip duration, loop state and priority are exported. Missing enum
metadata stays null. The six AnimationRigData binary fields are preserved
byte-for-byte with interpreted=false; retargeting is not implemented.
CurveAnimation and other clip types are currently rejected.

Facial NumberPose channels are exported separately as number_poses on each
keyframe. The supported hierarchy is a Folder named FaceControls under the
Head Pose. Each scalar sample retains its full path, stored value, weight,
easing style and easing direction. Values are not clamped during decoding;
clamping to the face-control range belongs to evaluation. Nested NumberPoses,
unknown children, and nonfinite values are rejected. The hierarchy follows:
https://create.roblox.com/docs/reference/engine/classes/NumberPose

The expanded captured-asset check decodes all 25 requested assets, including
10 that previously failed on facial data. It preserves 4,108 scalar samples;
the count matches the NumberPose instance count in the binary containers.
See outputs/runtime-analysis/animation-facial-decode.json for digests and counts.

The real downloaded test asset contains 61 keyframes, 976 pose samples, and
one AnimationRigData record. Its duration is approximately 2.50002 seconds.
The fetch/decode path was tested end to end against that public asset.
Only counts and digests are in outputs/runtime-analysis/animation-asset-decode.json;
the downloaded asset and decoded data stay under secrets/animation-assets/.

The fixture tests verify a non-identity rotation, pose position, unsorted
keyframe times, marker value, loop/priority, and preserved rig data. Malformed
or oversized chunk headers are rejected before DOM decoding. Python tests
cover URL validation, gzip expansion, corrupt input, redirect boundaries, and
decoder timeout behavior.
Known decoder failure codes and HTTP status numbers now survive the worker
queue, without forwarding response bodies or arbitrary subprocess diagnostics.

The persistent runtime now requests assets through AnimationAssetQueue, using
two background workers and a bounded, deduplicated cache. Only timeline data
is sent to the VM: timing, names, markers, loop state and priority. Pose and rig
data remain available from the codec but are not evaluated by this runtime.

Local Animator:LoadAnimation and Humanoid/AnimationController proxies create
distinct AnimationTrack instances. Playback supports Play/Stop, speed and
weight adjustments, seek while playing, looping, AnimationPriority, active
track lookup, AnimationPlayed, Stopped/Ended, keyframes and marker signals.
Length remains zero until decoding succeeds. Failed assets stop pending
tracks and are reported explicitly. Asset loading never blocks the packet
loop. Timeline evaluation uses the host steady clock and bounded event work.

This is a local playback timeline, not visible avatar animation. Joint posing,
weight/priority blending of joints, retargeting and network animation
replication remain unimplemented. Natural completion currently ends without
an additional automatic fade; explicit Stop(fadeTime) is supported. Exact
engine edge-case fidelity has not been established against a live animator.

Python tests: test_animation_assets, test_animation_asset_queue and
test_animation_runtime (the last requires the rebuilt persistent native VM).
The native tests execute authored Luau, not a Python emulation of tracks.
