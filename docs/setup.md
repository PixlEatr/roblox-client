# Setup

Rove builds from source. Use a local folder with all files downloaded, then open a terminal in that folder. Setup never joins a game and can be run again after an interrupted build.

## Windows — native terminal

Install these prerequisites:

- 64-bit Python 3.12 or newer, with the Python launcher (`py`).
- Stable Rust with the MSVC toolchain, from [rustup](https://rustup.rs/).
- CMake on PATH and Git.
- Visual Studio Build Tools with **Desktop development with C++**.
- OpenSSL 3 and zstd from [vcpkg](https://github.com/microsoft/vcpkg).

For a new vcpkg installation, run in PowerShell:

```powershell
git clone https://github.com/microsoft/vcpkg C:\vcpkg
C:\vcpkg\bootstrap-vcpkg.bat
C:\vcpkg\vcpkg.exe install openssl:x64-windows zstd:x64-windows
```

Then, from the Rove folder:

```powershell
py rove.py setup
notepad secrets\roblox-cookie.txt
py rove.py check
py rove.py
```

Setup locates Visual Studio and the default `C:\vcpkg` installation. For a different vcpkg location, use `py rove.py setup --native-prefix D:\vcpkg\installed\x64-windows`. It remembers that path in `.rove-platform.json`; keep the dependency folder installed. Use x64 Python, Rust, and libraries together.

No WSL, Node.js, or desktop app is needed for the Windows terminal. The local script console uses authenticated loopback TCP, and private files use Windows permissions. Optional source tracing is unavailable on Windows.

## macOS

Install Python 3.12+, CMake, Node.js, Xcode command-line tools, and stable Rust. With Homebrew available:

```sh
xcode-select --install
brew install python@3.12 cmake node openssl@3 zstd
```

Install Rust from [rustup](https://rustup.rs/), then:

```sh
python3.12 rove.py setup
# Edit secrets/roblox-cookie.txt using your text editor.
python3.12 rove.py check
python3.12 rove.py
```

The default is the browser UI. `python3.12 rove.py terminal` opens only the terminal. The native Mac wrapper remains an [optional developer tool](../runtime/README.md).

## Linux

On Ubuntu 24.04:

```sh
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-dev cmake g++ pkg-config libssl-dev libzstd-dev
```

Install stable Rust from [rustup](https://rustup.rs/), then:

```sh
python3 rove.py setup
# Edit secrets/roblox-cookie.txt using your text editor.
python3 rove.py check
python3 rove.py
```

For clipboard support, install `wl-clipboard` on Wayland or `xclip` on X11. Clipboard commands need a desktop session.

## Commands

| Command | What it does |
| --- | --- |
| `rove.py setup` | Installs Python dependencies, builds the runtime and animation codec, and runs an offline preflight. Also builds the UI on macOS. |
| `rove.py check` | Runs the focused offline portability/runtime tests and preflight; no account needed. |
| `rove.py terminal` | Opens the terminal; paste a place ID or invite. `:quit` leaves. |
| `rove.py ui` | Opens the existing browser workspace. On Windows/Linux, first install Node.js and run `rove.py setup --with-ui`. |
| `rove.py --help` | Lists commands without loading an account or starting a session. |

Prefix these commands with `py` on Windows or `python3` on macOS/Linux. Terminal options such as `--place` and `--duration` follow `terminal`. `terminal --help` lists them.

## Troubleshooting

- **Python too old:** select Python 3.12+ explicitly, for example `py -3.12 rove.py setup`.
- **Compiler missing:** on Windows, install the C++ workload, then rerun setup. Setup finds Visual Studio automatically.
- **DLL not found:** rerun setup with the correct `--native-prefix`; the folder must contain OpenSSL 3 and zstd under `bin/`.
- **Account file rejected:** run setup to apply private permissions, then edit `secrets/roblox-cookie.txt`. Do not put credentials in commands or issues.
- **Browser UI missing:** install Node.js, then run `rove.py setup --with-ui`.
- **Slow build:** try `rove.py setup --jobs 2`. All source files must be present locally, including files in cloud-backed folders.
- **A game fails or disconnects:** a successful offline check does not establish game compatibility. The runtime remains incomplete.

Setup does not install the official Roblox app. Optional built-in artwork can use an existing content folder; the runtime does not bundle it. No account is required for builds or offline checks.
