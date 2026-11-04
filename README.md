# rove

one Rove interface for your account, game preview and Luau scripts. same UI in the browser and mac app. still a wip.

needs Python 3.12+, CMake, a C++17 compiler, Rust and Node.js. macOS is the main dev platform.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-session.txt
python3.12 runtime/build.py
cargo build --release --locked --manifest-path rust-client/Cargo.toml
cargo build --release --locked --manifest-path animation-codec/Cargo.toml
npm --prefix ui ci
npm --prefix ui run build
mkdir -p secrets
```

put your cookie in `secrets/roblox-cookie.txt`, then run `chmod 600 secrets/roblox-cookie.txt`. that folder stays out of git.

```sh
.venv/bin/python rove.py
```

open **Workspace** for the game view, script editor and output. Play joins a game; Leave disconnects it. terminal sessions appear in the same workspace.

terminal only: `.venv/bin/python scripts/terminal_client.py --no-web`. `:view` opens Rove.

`ui/` is the only frontend. tests and preview fixtures live in `tests/` ([dev checks](tests/README.md)).

rendering, physics and game scripts are incomplete. some games won't load and the reported disconnects aren't fixed yet. this isn't the official Roblox client.

[runtime / optional tools](runtime/README.md) · [credits](THIRD_PARTY.md)
