# Development

Start with the [setup guide](setup.md). Build and run through `rove.py` so new terminals restore the configured native library path.

## Project map

| Folder | Purpose |
| --- | --- |
| [`scripts/`](../scripts/README.md) | Python launcher, sessions, replication, physics, and platform helpers; the module guide links to each area |
| [`runtime/`](../runtime/README.md) | C++ Luau host and engine bindings |
| [`rust-client/`](../rust-client/README.md) | Persistent Rust runtime supervisor |
| `animation-codec/` | Animation data decoder |
| `ui/` | The single browser frontend, also used by the Mac wrapper |
| `tests/` | Offline tests, fixtures, and preview harness |
| `vendor/`, `native/`, `inputs/` | Bundled dependencies and public reference data |
| `secrets/`, `outputs/` | Generated local state; never committed |

Tests stay in `tests/`; new user-facing instructions belong in `docs/`. Keep root entry points limited to `rove.py` and the Windows shortcut `rove.cmd`.

An existing research folder may also contain older `.command`/`.cmd` shortcuts, `ida/`, `references/`, `dist/`, and generated files. Those are local tools or artifacts, not additional setup steps. Use `rove.py` as the common entry point. The published source has a smaller root; local research and account data stay in place so existing tools can still find them.

Historical notes in local research folders live in `docs/archive/`. Start with the [documentation index](README.md) for current guides.

## Checks

Run `python rove.py check` for the focused portability suite. [tests/README.md](../tests/README.md) describes the broader suites and known failures. They are not equivalent to a live-server test.

The GitHub Actions matrix builds C++, Rust, and the animation codec, then runs offline checks separately on Windows, Linux, and macOS. See the repository Actions tab for actual results. A configured workflow is not proof of a passing Windows run.

Windows setup uses native MSVC. Developers can alternatively cross-compile with `runtime/cmake/windows-mingw.cmake`, `ROVE_CMAKE_TOOLCHAIN_FILE`, and the matching Cargo target linker; those builds do not validate Windows execution.

Third-party licenses and provenance remain with each dependency. See [THIRD_PARTY.md](../THIRD_PARTY.md).

The old archive-wide `SHA256SUMS` snapshot was removed because it became stale whenever source changed. Git identifies source revisions; dependency provenance and the runtime integrity monitor remain separate.
