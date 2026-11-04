#!/bin/zsh
cd -- "${0:A:h}" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"
rove_pause=1
if [[ "${1-}" == --no-pause ]]; then
  rove_pause=0
  shift
fi
rove_python=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,12))' 2>/dev/null; then
    rove_python="$candidate"
    break
  fi
done
if [[ -n "$rove_python" ]]; then
  "$rove_python" rove.py setup "$@"
  rove_result=$?
else
  print 'Install Python 3.12 or newer first. See README.md.'
  rove_result=1
fi
if (( rove_pause )) && [[ -t 0 ]]; then
  read '?Press Return to close. '
fi
exit "$rove_result"
