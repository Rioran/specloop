#!/bin/sh
# Install, update or remove specloop in a project (macOS, Linux, Git Bash on Windows).
# Usage: ./install.sh [TARGET] [options]   (see ./install.sh --help)
DIR=$(cd "$(dirname "$0")" && pwd)
if [ -n "$SPECLOOP_PYTHON" ]; then
  exec "$SPECLOOP_PYTHON" "$DIR/install.py" "$@"
fi
for py in python3 python "py -3"; do
  if $py -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    exec $py "$DIR/install.py" "$@"
  fi
done
echo "specloop: Python 3.8+ is required (or set SPECLOOP_PYTHON)" >&2
exit 1
