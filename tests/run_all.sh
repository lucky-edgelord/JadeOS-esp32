#!/bin/bash
# Every host-side test, on the stock MicroPython unix port (no custom firmware): the indexer, the shell,
# the HTTP API end to end (with a fake model) and the Deck in headless Chromium.
#   MICROPYTHON=/path/to/ports/unix/build-standard/micropython tests/run_all.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
export MICROPYTHON=${MICROPYTHON:-/home/claude/src/micropython/ports/unix/build-standard/micropython}
fails=0
run() {
    local name=$1; shift
    echo "=== $name"
    if "$@"; then echo "--- $name: ok"; else echo "--- $name: FAILED"; fails=$((fails+1)); fi
}
cd "$ROOT"
run "indexer" tests/mp.sh tests/unix/test_idx.py
run "shell" tests/mp.sh tests/unix/test_shell.py
run "HTTP API end to end" timeout 600 python3 tests/test_api.py
if [ -f tests/test_dashboard.py ]; then
    run "Deck (headless Chromium)" timeout 600 python3 tests/test_dashboard.py
fi
echo "=== $fails suite(s) failed"
exit $fails
