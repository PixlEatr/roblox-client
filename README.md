# roblox client

custom roblox client with a luau runtime, terminal, browser preview and mac app. still a wip.

needs Python 3.12+, CMake, a C++17 compiler, Rust and Node.js. macOS is the main dev platform.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-session.txt
python3.12 runtime/build.py
cargo build --release --locked --manifest-path rust-client/Cargo.toml
cargo build --release --locked --manifest-path animation-codec/Cargo.toml
npm --prefix runtime/launcher ci
npm --prefix runtime/launcher run build
mkdir -p secrets
```

put your cookie in `secrets/roblox-cookie.txt`, then run `chmod 600 secrets/roblox-cookie.txt`. that folder stays out of git.

```sh
.venv/bin/python scripts/terminal_client.py
```

paste a place id or private server link. `:view` opens the game preview, `:quit` leaves. the terminal has no session time limit by default.

for the browser launcher: `.venv/bin/python scripts/launcher.py`.

rendering, physics and game scripts are incomplete. some games won't load and the reported disconnects aren't fixed yet. this isn't the official Roblox client.

[runtime / optional tools](runtime/README.md) · [credits](THIRD_PARTY.md)
