# JadeOS-ESP32

A standalone agentic OS for the **ESP32-S3 N16R8** (16 MB flash, 8 MB octal PSRAM) that runs on **stock
MicroPython**: no custom firmware, no C. Everything is Python files on the board's own LittleFS:

- a **shell** written in Python (pipes, redirects, globs, variables, ~80 commands, `jade` command group),
- a **Markdown vault indexer** (search, outline, links, tags, tasks) in Python,
- a Search → Reason → Act **agent loop** with a tool registry and a remote "cortex" (any OpenAI-compatible
  chat API, with token budgeting),
- a token-authenticated **REST API** ([Microdot](https://github.com/miguelgrinberg/microdot)),
- the **JadeOS Deck** (dashboard + Live2D avatar), served by the device itself from flash.

## Install
1. Flash stock MicroPython for the **ESP32_GENERIC_S3** board, `SPIRAM_OCT` variant (v1.29 or newer), from
   micropython.org/download/ESP32_GENERIC_S3. It makes a ~14 MB LittleFS on a 16 MB module on its own.

       esptool.py --chip esp32s3 -b 460800 write_flash -z 0 ESP32_GENERIC_S3-SPIRAM_OCT-*.bin

2. Copy JadeOS over USB (`pip install mpremote`):

       python3 tools/install.py            # full Deck with avatar, ~650 KB gzipped
       python3 tools/install.py --light    # small dashboard, ~23 KB gzipped
       python3 tools/install.py --no-deck  # API only; use the Deck on GitHub Pages instead

## First run
1. `mpremote repl`, then set Wi-Fi and your model key:
   `from jade import config; c=config.load(); c['wifi']['ssid']='...'; c['wifi']['password']='...'; c['cortex']['key']='...'; config.save()`
   Without Wi-Fi settings the board opens a setup access point.
2. Reboot. The boot log prints the address; the API token is in `/jade/config.json`
   (`cat /jade/config.json` in the JadeOS shell).
3. Open `http://<address>/` (or `http://jade.local/`): the Deck is served by the board and already points at it,
   you only paste the token. The Deck on GitHub Pages also works (it asks for the device address).

Chrome 142+ asks permission for "local network access" when a public HTTPS page talks to a LAN device: allow it.
Firefox/Safari may block that; use the Deck served by the device itself, which has no such problem.

## Shell
On the serial console the JadeOS shell runs next to the web server (`ls /vault`, `grep -i tomato /vault`,
`jade ask <question>`, `jade status`, `help`). The vault is plain Markdown under `/vault`; files under `/jade`
(config, logs, history), the Deck under `/www`, libraries under `/lib`. `touch /jade/SAFE` boots to a bare REPL.

## Tests
`MICROPYTHON=/path/to/ports/unix/build-standard/micropython tests/run_all.sh` runs the indexer, the shell, the
API end to end (fake model) and the Deck in headless Chromium, all on the stock MicroPython unix port. They
do **not** cover real ESP32 hardware (Wi-Fi, PSRAM, flash, USB console): treat the first boot on a
board as the real test.

See `THIRD_PARTY.md` for licences.
