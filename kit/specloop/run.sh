#!/bin/sh
# Portable launcher for the specloop engine: finds a Python 3.8+ interpreter.
# Override the interpreter with SPECLOOP_PYTHON. Exits 0 when Python is missing so
# that a broken setup never blocks a Claude Code session.
DIR=$(cd "$(dirname "$0")" && pwd)
if [ -n "$SPECLOOP_PYTHON" ]; then
  exec "$SPECLOOP_PYTHON" "$DIR/engine.py" "$@"
fi
for py in python3 python "py -3"; do
  if $py -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    exec $py "$DIR/engine.py" "$@"
  fi
done
echo "specloop: Python 3.8+ not found (set SPECLOOP_PYTHON)" >&2
exit 0
