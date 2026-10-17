# JadeOS start-up on stock MicroPython: network, vault, HTTP API + Deck, and a serial shell, one asyncio loop.
# main.py on the board is just:  import jade.app; jade.app.run()
# Create /jade/SAFE (touch /jade/SAFE) to boot to a bare REPL instead; Ctrl-C also stops it.
import asyncio, gc, sys
from jade import config, vault, net, api, sysinfo, fs, shell
from jade.log import log
import jade


async def console():
    # the shell on the USB/UART console, next to the web server. Skipped when there is no stdin to read.
    try:
        rd = asyncio.StreamReader(sys.stdin)
    except Exception as e:
        log("console: off (%s)" % e)
        return
    sh = shell.Shell()
    while not sh.stop:
        sys.stdout.write(sh.prompt())
        try:
            line = await rd.readline()
        except Exception:
            return
        if not line:
            await asyncio.sleep_ms(200)
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        try:
            await sh.run(line.strip())
        except Exception as e:
            print("shell:", e)
    log("console: exit; the web server keeps running")


async def main(port=None, serial=True):
    cfg = config.load()
    log("JadeOS", jade.VERSION, "starting")
    vault.ensure()
    info = await net.up()
    p = port or cfg["api"]["port"]
    log("JadeOS ready: http://%s%s  (token: cat /jade/config.json)" % (info.get("ip") or "?", "" if p == 80 else ":%d" % p))
    gc.collect()
    if serial:
        asyncio.create_task(console())
    await api.serve(port)


def run(port=None, serial=True):
    if fs.exists(fs.HOME + "/SAFE"):
        log("safe mode: /jade/SAFE exists, not starting Jade")
        return
    try:
        asyncio.run(main(port, serial))
    except KeyboardInterrupt:
        log("stopped")
    finally:
        asyncio.new_event_loop()
