# Rove

A work-in-progress Roblox runtime with a terminal and an optional browser workspace for Luau scripts and a game preview.

## Start here

[Download the source ZIP](https://github.com/alexkkork/roblox-client/archive/refs/heads/main.zip) and extract it, or clone this repository. Install the [prerequisites for your system](docs/setup.md), then run these commands from the project folder:

| | Windows | macOS / Linux |
| --- | --- | --- |
| Set up once | `py rove.py setup` | `python3 rove.py setup` |
| Check offline | `py rove.py check` | `python3 rove.py check` |
| Start | `py rove.py` | `python3 rove.py` |

Setup creates an empty, private `secrets/roblox-cookie.txt`. Add your account session there before joining. Keep that file local.

Windows and Linux start in the terminal. macOS opens the existing browser UI. Use `rove.py terminal` for terminal mode on any system; `:quit` leaves the game. Windows also accepts `.\rove.cmd setup`, `.\rove.cmd check`, and `.\rove.cmd`.

**Windows uses a native source build.** Build and offline-test results are available in [GitHub Actions](https://github.com/alexkkork/roblox-client/actions/workflows/portability.yml). Rendering, physics, and game scripts are incomplete, and some games or sessions may fail.

## Find your way

- [Guides: setup, troubleshooting, project map, and tests](docs/README.md)
- [Third-party credits](THIRD_PARTY.md)

The browser workspace is in `ui/`; runtime code is in `runtime/` and `rust-client/`; Python session code is in `scripts/`; offline checks and fixtures are in `tests/`. You only need `rove.py` to get started.
