#!/usr/bin/env python3
"""Put JadeOS on an ESP32 that runs stock MicroPython (needs `pip install mpremote`).

    python3 tools/install.py [--port /dev/ttyACM0] [--no-deck] [--light]

Copies python/jade, python/lib/microdot and main.py, and the Deck (gzipped) to /www, over USB.
Without a Deck on the device, the Deck on GitHub Pages still works against it.
--light installs the small dashboard (no avatar, ~23 KB) instead of the full Deck (~650 KB).
"""
import argparse, os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def mp(port, *args):
    cmd = ["mpremote"] + (["connect", port] if port else []) + list(args)
    print("+", " ".join(cmd[1:]))
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--no-deck", action="store_true")
    ap.add_argument("--light", action="store_true")
    a = ap.parse_args()
    py = os.path.join(ROOT, "python")
    for d in ("lib", "lib/jade", "lib/microdot", "www"):     # /lib is on MicroPython's default import path
        subprocess.run(["mpremote"] + (["connect", a.port] if a.port else []) + ["fs", "mkdir", ":" + d],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in sorted(os.listdir(os.path.join(py, "jade"))):
        if name.endswith(".py"):
            mp(a.port, "fs", "cp", os.path.join(py, "jade", name), ":lib/jade/" + name)
    for name in sorted(os.listdir(os.path.join(py, "lib", "microdot"))):
        if name.endswith(".py"):
            mp(a.port, "fs", "cp", os.path.join(py, "lib", "microdot", name), ":lib/microdot/" + name)
    mp(a.port, "fs", "cp", os.path.join(py, "main.py"), ":main.py")
    if not a.no_deck:
        import build_dashboard
        out = os.path.join(tempfile.mkdtemp(), "index.html")
        build_dashboard.build(avatar=not a.light, out=out)
        mp(a.port, "fs", "cp", out + ".gz", ":www/index.html.gz")
    mp(a.port, "reset")
    print("Installed. Watch the boot log with: mpremote connect %s repl" % (a.port or "auto"))


if __name__ == "__main__":
    main()
