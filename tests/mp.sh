#!/bin/sh
# Run a script on the stock MicroPython unix port with JadeOS importable and the device filesystem under a scratch dir.
#   tests/mp.sh tests/unix/test_idx.py
ROOT=$(cd "$(dirname "$0")/.." && pwd)
MP=${MICROPYTHON:-/home/claude/src/micropython/ports/unix/build-standard/micropython}
export JADEOS_ROOT=${JADEOS_ROOT:-$(mktemp -d)}
export MICROPYPATH=".frozen:$ROOT/python:$ROOT/python/lib:$ROOT/tests/unix"
exec "$MP" "$@"
