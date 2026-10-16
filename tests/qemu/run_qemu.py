#!/usr/bin/env python3
"""Boot the JadeOS QEMU firmware in Espressif's ESP32-S3 emulator and test it end to end.

    python3 tests/qemu/run_qemu.py --firmware build/firmware.bin [--qemu qemu-system-xtensa]

What it does:
  1. pads the firmware to a 16 MB flash image and boots it (8 MB octal PSRAM, emulated Ethernet with
     the device's port 80 forwarded to localhost:18081, UART0 = BreezyBox, UART1 = Python REPL)
  2. starts the fake model API on the host (the device reaches it at 10.0.2.2)
  3. through the Python REPL: points Jade at the fake model, sets a known API token, soft-resets
  4. runs tests/test_api.py against the emulated device
  5. drives the BreezyBox shell on UART0: ls, df, free, cat, and the jade bridge commands
Everything the emulator prints is saved to --logs.
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print("ok:", msg.split(":")[0], flush=True)
    else:
        failed += 1
        print("FAIL:", msg, flush=True)


class Serial:
    """A QEMU serial port exposed as a TCP server; collects everything it prints."""

    def __init__(self, port, name, logdir):
        self.name = name
        self.buf = b""
        self.lock = threading.Lock()
        self.log = open(os.path.join(logdir, name + ".log"), "wb")
        for _ in range(100):
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("could not connect to " + name)
        self.sock.settimeout(0.2)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while True:
            try:
                d = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not d:
                return
            with self.lock:
                self.buf += d
            self.log.write(d)
            self.log.flush()

    def mark(self):
        with self.lock:
            return len(self.buf)

    def since(self, mark):
        with self.lock:
            return self.buf[mark:].decode("utf-8", "replace")

    def wait(self, text, timeout, mark=0):
        end = time.time() + timeout
        while time.time() < end:
            if text in self.since(mark):
                return True
            time.sleep(0.1)
        return False

    def write(self, data, slow=False):
        if isinstance(data, str):
            data = data.encode()
        if slow:   # the emulated UART FIFO is small: feed it gently
            for i in range(0, len(data), 32):
                self.sock.sendall(data[i:i + 32])
                time.sleep(0.03)
        else:
            self.sock.sendall(data)


def shell(uart0, cmd, timeout=60, until="$ "):
    m = uart0.mark()
    uart0.write(cmd + "\r", slow=True)
    uart0.wait(until, timeout, m + len(cmd))
    time.sleep(0.3)
    out = uart0.since(m)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--firmware", required=True)
    ap.add_argument("--qemu", default=shutil.which("qemu-system-xtensa") or "qemu-system-xtensa")
    ap.add_argument("--logs", default=os.path.join(ROOT, "qemu-logs"))
    ap.add_argument("--boot-timeout", type=int, default=240)
    a = ap.parse_args()
    os.makedirs(a.logs, exist_ok=True)
    work = tempfile.mkdtemp(prefix="jade-qemu-")
    flash = os.path.join(work, "flash.bin")
    shutil.copy(a.firmware, flash)
    with open(flash, "ab") as f:      # erased flash reads 0xFF (zeros would corrupt LittleFS's format)
        f.write(b"\xff" * (16 * 1024 * 1024 - os.path.getsize(flash)))

    llm = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools/fake_llm.py"), "18080"], stdout=subprocess.DEVNULL)
    qemu_cmd = [a.qemu, "-nographic", "-machine", "esp32s3", "-m", "8M",
                "-global", "driver=ssi_psram,property=is_octal,value=true",
                "-drive", "file=%s,if=mtd,format=raw" % flash,
                "-nic", "user,model=open_eth,hostfwd=tcp:127.0.0.1:18081-:80",
                "-serial", "tcp:127.0.0.1:15550,server,nowait",
                "-serial", "tcp:127.0.0.1:15551,server,nowait",
                "-monitor", "none", "-no-reboot"]
    print(" ".join(qemu_cmd), flush=True)
    qlog = open(os.path.join(a.logs, "qemu.log"), "wb")
    qemu = subprocess.Popen(qemu_cmd, stdout=qlog, stderr=subprocess.STDOUT)
    try:
        uart0 = Serial(15550, "uart0-breezybox", a.logs)
        uart1 = Serial(15551, "uart1-python", a.logs)

        # --- boot
        t0 = time.time()
        check(uart0.wait("JadeOS: starting the BreezyBox shell", a.boot_timeout), "board start-up ran (UART0)")
        check(uart0.wait("LittleFS:", 60), "BreezyBox mounted LittleFS: " + uart0.since(0)[-300:])
        check(uart1.wait("JadeOS ready", a.boot_timeout), "Jade started (UART1): " + uart1.since(0)[-800:])
        print("boot took %.0f s" % (time.time() - t0), flush=True)
        boot = uart1.since(0)
        check("net: ethernet 10.0.2." in boot, "got an address from the emulated network")

        # --- configure through the Python REPL, then soft-reset
        uart1.write(b"\x03\x03")
        check(uart1.wait(">>> ", 30, len(boot)), "Ctrl-C reaches the REPL")
        m = uart1.mark()
        code = ("from jade import config; c=config.load(); c['cortex']['url']='http://10.0.2.2:18080/v1/chat/completions'; "
                "c['cortex']['key']='test-key'; c['api']['token']='qemu-token'; config.save(); print('CONFIGURED', c['vault'])\r")
        uart1.write(code, slow=True)
        check(uart1.wait("CONFIGURED /root/vault", 30, m), "config saved from the REPL: " + uart1.since(m)[-300:])
        m = uart1.mark()
        uart1.write(b"import os; print('LS', os.listdir('/'), os.listdir('/root'), os.getcwd())\r", slow=True)
        check(uart1.wait("LS ['root']", 20, m) and "'jade'" in uart1.since(m) and "/root" in uart1.since(m),
              "Python sees the shared filesystem: " + uart1.since(m)[-300:])
        m = uart1.mark()
        uart1.write(b"import jadecore; print('PLAT', jadecore.platform, jadecore.search('/root/vault', 'welcome vault')[0][0])\r", slow=True)
        check(uart1.wait("PLAT esp32 /root/vault/Welcome.md", 20, m), "C indexer on the device: " + uart1.since(m)[-300:])
        m = uart1.mark()
        uart1.write(b"\x04")   # soft reset → _boot.py → main.py → jade.app
        check(uart1.wait("JadeOS ready", 120, m), "restarted after soft reset: " + uart1.since(m)[-500:])
        time.sleep(1)

        # --- the HTTP API, through QEMU's port forward
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tests/test_api.py"), "--no-spawn", "--url", "http://127.0.0.1:18081",
                            "--token", "qemu-token", "--llm-port", "18080"], capture_output=True, text=True, timeout=1800)
        print(r.stdout[-6000:], r.stderr[-3000:], flush=True)
        with open(os.path.join(a.logs, "test_api.log"), "w") as f:
            f.write(r.stdout + r.stderr)
        check(r.returncode == 0, "API tests on the emulated ESP32-S3")

        # --- the BreezyBox shell on UART0
        uart0.write("\r")
        time.sleep(1)
        out = shell(uart0, "ls /root/vault")
        check("Welcome.md" in out and "Projects" in out, "shell ls sees notes Python wrote: " + out[-300:])
        out = shell(uart0, "cat /root/vault/Projects/Garden.md")
        check("Plant **tomatoes** in May" in out, "shell cat: " + out[-300:])
        out = shell(uart0, "df")
        check("KB" in out or "total" in out.lower() or "used" in out.lower(), "df: " + out[-300:])
        out = shell(uart0, "free")
        check(any(w in out.lower() for w in ("free", "heap", "psram")), "free: " + out[-300:])
        out = shell(uart0, "jade-search tomatoes")
        check("Projects/Garden.md:5" in out, "jade-search through the bridge: " + out[-400:])
        out = shell(uart0, "jade-py 6*7")
        check("42" in out, "jade-py: " + out[-200:])
        out = shell(uart0, "jade ask what about tomatoes?", timeout=120)
        check("You asked: what about tomatoes?" in out and "Projects/Garden.md" in out, "jade ask (shell → Python → model): " + out[-600:])
        out = shell(uart0, "jade tool current_time")
        check("20" in out, "jade tool: " + out[-200:])
        out = shell(uart0, "echo from-shell > /root/vault/shell-note.md")
        out = shell(uart0, "jade-search from-shell")
        check("shell-note.md" in out, "a note written by the shell is searchable: " + out[-300:])
        out = shell(uart0, "wifi status")
        check("managed by Jade" in out, "wifi command replaced: " + out[-300:])
        out = shell(uart0, "jade status")
        check('"name": "JadeOS"' in out or '"name":"JadeOS"' in out, "jade status: " + out[-300:])
        st = out
        try:
            import json, re
            js = json.loads(re.search(r"\{.*\}", st, re.S).group(0))
            print("device status:", json.dumps(js, indent=1), flush=True)
            check(js.get("heap_free", 0) > 1000000, "MicroPython heap is in PSRAM (%s bytes free)" % js.get("heap_free"))
            check(js.get("storage_total", 0) > 10 * 1024 * 1024, "statvfs reports the 12 MB storage partition (%s)" % js.get("storage_total"))
        except Exception as e:
            check(False, "could not parse jade status: %s" % e)
    finally:
        qemu.terminate()
        llm.terminate()
        try:
            qemu.wait(5)
        except Exception:
            qemu.kill()
        shutil.rmtree(work, ignore_errors=True)
    print("qemu: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
