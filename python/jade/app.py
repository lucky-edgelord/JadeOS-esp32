# JadeOS start-up: network, vault, shell bridge and HTTP API, all on one asyncio loop.
# /root/main.py runs this; create /root/jade/SAFE to boot to a bare REPL instead.
import asyncio, gc
from jade import config, vault, net, bridge, api, sysinfo
from jade.log import log
import jade


async def main(port=None):
    cfg = config.load()
    log("JadeOS", jade.VERSION, "starting")
    vault.ensure()
    info = await net.up()
    log("JadeOS ready: http://%s%s (token in /root/jade/config.json)" % (
        info.get("ip") or "?", "" if (port or cfg["api"]["port"]) == 80 else ":%d" % (port or cfg["api"]["port"])))
    gc.collect()
    asyncio.create_task(bridge.serve())
    await api.serve(port)


def run(port=None):
    try:
        import os
        os.stat(config.HOME + "/SAFE")
        log("safe mode: /root/jade/SAFE exists, not starting Jade")
        return
    except OSError:
        pass
    try:
        asyncio.run(main(port))
    except KeyboardInterrupt:
        log("stopped")
    finally:
        asyncio.new_event_loop()
