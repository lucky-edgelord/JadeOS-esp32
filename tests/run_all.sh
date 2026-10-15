#!/bin/bash
# Every host-side test: the C core under sanitizers, the MicroPython unix-port build with Jade frozen
# in, jadecore through MicroPython, the HTTP API end to end, and the dashboard in a headless browser.
# (The firmware itself is tested in Espressif's ESP32-S3 emulator: tests/qemu/run_qemu.py.)
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
export MPY=${MPY:-/home/claude/src/micropython}
export MICROPYTHON_JADE=$MPY/ports/unix/build-jade/micropython-jade
OUT=$(mktemp -d)
fails=0
run() {
    local name=$1; shift
    echo "=== $name"
    if "$@"; then echo "--- $name: ok"; else echo "--- $name: FAILED"; fails=$((fails+1)); fi
}
cd "$ROOT"
J=firmware/modules/jadecore
run "indexer (ASan+UBSan)" sh -c "gcc -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -Werror -g -fsanitize=address,undefined -fno-sanitize-recover=all tests/c/test_idx.c $J/jade_idx.c -o $OUT/idx && $OUT/idx"
run "bridge (TSan)" sh -c "gcc -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -Werror -g -O1 -fsanitize=thread tests/c/test_bridge.c $J/jade_bridge.c -o $OUT/br -lpthread && $OUT/br"
run "bridge (ASan+UBSan)" sh -c "gcc -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -Werror -g -O1 -fsanitize=address,undefined -fno-sanitize-recover=all tests/c/test_bridge.c $J/jade_bridge.c -o $OUT/br2 -lpthread && $OUT/br2"
run "unix port build" tests/unix/build.sh
run "jadecore in MicroPython" "$MICROPYTHON_JADE" tests/unix/test_jadecore.py "$OUT/jv"
run "HTTP API end to end" python3 tests/test_api.py
if [ -f tests/test_dashboard.py ]; then
    run "dashboard (headless Chromium)" python3 tests/test_dashboard.py
fi
rm -rf "$OUT"
echo "=== $fails suite(s) failed"
exit $fails
