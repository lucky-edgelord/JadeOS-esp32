# JadeOS-ESP32

A standalone agentic OS for the **ESP32-S3 N16R8** (16 MB flash, 8 MB octal PSRAM): MicroPython + the
[BreezyBox](https://github.com/valdanylchuk/breezybox) shell sharing one LittleFS, a C vault indexer,
a Search → Reason → Act agent loop with a tool registry, a remote "cortex" (any OpenAI-compatible chat
API, with token budgeting), and a token-authenticated REST API (Microdot).

The **JadeOS Deck** dashboard is a single HTML file (`docs/index.html`) served from GitHub Pages – it is a
client that talks to your device, nothing is served from the ESP32 itself.

## Flash
Download `jadeos-esp32s3-n16r8.bin` from the latest release (or the `ci-results` branch) and:

    esptool.py --chip esp32s3 -b 460800 write_flash 0x0 jadeos-esp32s3-n16r8.bin

## First run
1. Connect over USB serial (UART REPL), then set Wi-Fi and your model key:
   `from jade import config; c=config.load(); c['wifi']...; c['cortex']['key']='...'; config.save()`
2. The token for the API is in `/root/jade/config.json` (printed at boot).
3. Open the Deck on GitHub Pages and enter the device address and token.

Chrome 142+ asks permission for "local network access" when a public page talks to a LAN device – allow it.
Firefox/Safari may block this; save `docs/index.html` locally and open it from disk instead.

## Shell commands (BreezyBox)
`jade status`, `jade ask <question>`, `jade tool <name>`, `jade-search <terms>`, `jade-py <expr>`; the vault
is plain Markdown under `/root/vault`, shared by the shell, Python and C.

## Tests
`MPY=/path/to/micropython tests/run_all.sh` runs C sanitizers, the Python modules on the MicroPython unix
port, the API end to end with a fake LLM, and the dashboard in headless Chromium. CI additionally builds
the firmware and boots it in Espressif's ESP32-S3 QEMU (`tests/qemu/run_qemu.py`).

See `THIRD_PARTY.md` for licences.
