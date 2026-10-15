#!/bin/sh
# Build the MicroPython unix port with Jade's C module and frozen Python (the test "device").
set -e
MPY=${MPY:-/home/claude/src/micropython}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
make -C "$MPY/ports/unix" -j2 BUILD=build-jade PROG=micropython-jade \
    USER_C_MODULES="$ROOT/firmware/modules" FROZEN_MANIFEST="$ROOT/tests/unix/manifest.py" "$@" | grep -iE "warning|error" || true
test -x "$MPY/ports/unix/build-jade/micropython-jade"
