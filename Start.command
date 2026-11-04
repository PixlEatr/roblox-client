#!/bin/zsh
cd -- "${0:A:h}" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"
for rove_python in .venv/bin/python python3.13 python3.12 python3; do
  if command -v "$rove_python" >/dev/null && "$rove_python" -c 'import sys; raise SystemExit(sys.version_info < (3,12))' 2>/dev/null; then
    exec "$rove_python" scripts/start_ui.py "$@"
  fi
done
print 'Install Python 3.12 or newer first. See README.md.'
exit 1
