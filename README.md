# Rove

A work-in-progress Roblox runtime with a terminal and an optional browser workspace for Luau scripts and a game preview.

## Start here

Install the [prerequisites for your system](docs/setup.md), then run these commands from the project folder:

| | Windows | macOS / Linux |
| --- | --- | --- |
| Set up once | `py rove.py setup` | `python3 rove.py setup` |
| Check offline | `py rove.py check` | `python3 rove.py check` |
| Start | `py rove.py` | `python3 rove.py` |

Setup creates an empty, private `secrets/roblox-cookie.txt`. Add your account session there before joining. Keep that file local.

Windows and Linux start in the terminal. macOS opens the existing browser UI. Use `rove.py terminal` for terminal mode on any system; `:quit` leaves the game. Windows also accepts `rove.cmd setup`, `rove.cmd check`, and `rove.cmd`.

**Windows is a native source build, not a packaged app.** Windows execution is still being validated. Rendering, physics, and game scripts are incomplete, and some games or sessions may fail.

## Find your way

- [Setup and troubleshooting](docs/setup.md)
- [Project map and development](docs/development.md)
- [Runtime and optional tools](runtime/README.md)
- [Third-party credits](THIRD_PARTY.md)

The browser workspace is in `ui/`; runtime code is in `runtime/` and `rust-client/`; Python session code is in `scripts/`; offline checks and fixtures are in `tests/`. You only need `rove.py` to get started.
