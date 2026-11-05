# setup

[download](https://github.com/alexkkork/roblox-client/archive/refs/heads/main.zip), unzip, then open a terminal in the Rove folder. first build takes a bit. the compiler is locking in.

## Windows — native terminal

install **64-bit Python 3.12+** (with `py`), [Rust](https://rustup.rs/) (MSVC), Git, CMake on PATH, and Visual Studio Build Tools with **Desktop development with C++**. then run this once in PowerShell:

```powershell
git clone https://github.com/microsoft/vcpkg C:\vcpkg
C:\vcpkg\bootstrap-vcpkg.bat
C:\vcpkg\vcpkg.exe install openssl:x64-windows zstd:x64-windows
```

back in the Rove folder:

```powershell
py rove.py setup
notepad secrets\roblox-cookie.txt
py rove.py
```

## macOS

with [Homebrew](https://brew.sh/) installed:

```sh
xcode-select --install
brew install python@3.12 cmake node openssl@3 zstd
```

install [Rust](https://rustup.rs/), then run `python3.12 rove.py setup`.

## Linux

on Ubuntu 24.04:

```sh
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-dev cmake g++ pkg-config libssl-dev libzstd-dev
```

install [Rust](https://rustup.rs/), then run `python3 rove.py setup`.

## join

after setup, put your Roblox session cookie in `secrets/roblox-cookie.txt` and save it. keep it private; don't paste it into issues or screenshots.

run `py rove.py` on Windows, `python3.12 rove.py` on Mac, or `python3 rove.py` on Linux. Mac opens the browser UI; Windows/Linux open the terminal. paste a place ID or invite; `:quit` leaves the terminal session.

## commands

use the same Python command from above before each one:

- `rove.py setup` — build/update; safe to rerun if interrupted.
- `rove.py check` — offline checks, no account needed.
- `rove.py terminal` — terminal only.
- `rove.py --help` — more options.

## troubleshooting

- build failing? check Python is 3.12+ and all source files are downloaded. try `rove.py setup --jobs 2`.
- Windows DLL error? keep everything x64. custom vcpkg folder: `py rove.py setup --native-prefix D:\vcpkg\installed\x64-windows`.
- want the browser UI on Windows/Linux? install Node.js, run `rove.py setup --with-ui`, then `rove.py ui`.
- Linux clipboard? install `wl-clipboard` (Wayland) or `xclip` (X11); needs a desktop session.
- cookie permissions rejected? rerun setup, then edit the file again.
- game disconnects? Rove is still unfinished; passing offline checks doesn't mean every game works.
