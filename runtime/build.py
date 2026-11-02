#!/usr/bin/env python3
"""Build the bundled Luau libraries and the host. No other checkout needed."""
from pathlib import Path
import subprocess
HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
# Keep generated files out of the source folders.
subprocess.run(["cmake", "-S", str(HERE), "-B", str(BUILD), "-DCMAKE_BUILD_TYPE=Release"], check=True)
subprocess.run(["cmake", "--build", str(BUILD), "--parallel", "4"], check=True)
print(BUILD / "luau-host")
