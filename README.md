# Rove

A work-in-progress Roblox runtime with a terminal and an optional browser workspace for Luau scripts and a game preview.

## Start here

1. [Download the source ZIP](https://github.com/alexkkork/roblox-client/archive/refs/heads/main.zip) and extract it, or clone this repository.
2. Install the prerequisites: [Windows](docs/setup.md#windows--native-terminal), [macOS](docs/setup.md#macos), or [Linux](docs/setup.md#linux).
3. Open a terminal in the extracted folder and use the commands below.

| | Windows | macOS / Linux |
| --- | --- | --- |
| Set up once | `py rove.py setup` | `python3 rove.py setup` |
| Check offline | `py rove.py check` | `python3 rove.py check` |
| Start | `py rove.py` | `python3 rove.py` |

Setup creates an empty, private `secrets/roblox-cookie.txt`. Add your account session there before joining. Keep that file local.

Windows and Linux start in the terminal. macOS opens the browser UI. Use `rove.py terminal` for terminal mode on any system; `:quit` leaves the game. Setup is only needed the first time or after updating the source.

**Windows uses a native source build.** Build and offline-test results are available in [GitHub Actions](https://github.com/alexkkork/roblox-client/actions/workflows/portability.yml). Rendering, physics, and game scripts are incomplete, and some games or sessions may fail.

## Find your way

| I want to… | Open |
| --- | --- |
| Fix a setup problem | [Troubleshooting](docs/setup.md#troubleshooting) |
| Find a command | [Command reference](docs/setup.md#commands) |
| Change the code | [Project map](docs/development.md) |
| Find physics, scripting, or session code | [Python module guide](scripts/README.md) |
| Run tests | [Test guide](tests/README.md) |

[All guides](docs/README.md) · [Third-party credits](THIRD_PARTY.md)
