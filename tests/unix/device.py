# Runs JadeOS on the MicroPython unix port as if it were the device:
#   micropython-jade tests/unix/device.py <data dir> <api port> <llm url>
# <data dir> plays the flash: its root/ folder is the device's /root.
# Also adds /api/_test/bridge, which plays the BreezyBox shell side of the bridge.
import sys, os, json, jadecore, _thread, asyncio

data, port, llm = sys.argv[1], int(sys.argv[2]), sys.argv[3]
for d in (data, data + "/root"):
    try:
        os.mkdir(d)
    except OSError:
        pass
os.umount("/")
os.mount(jadecore.VfsJade(data), "/")
os.chdir("/root")

from jade import config
cfg = config.load()
cfg["cortex"]["url"] = llm
cfg["cortex"]["key"] = "test-key"
cfg["api"]["token"] = "test-token"
cfg["api"]["port"] = port
config.save()

from jade import api, app


@api.app.post("/api/_test/bridge")
async def test_bridge(req):
    # what `jade <line>` in the shell does: blocks a (non-Python) thread until Python answers
    line = req.json["line"]
    out = []
    _thread.start_new_thread(lambda: out.append(jadecore.bridge_call(line, int(req.json.get("timeout", 10000)))), ())
    while not out:
        await asyncio.sleep_ms(10)
    return {"status": out[0][0], "reply": out[0][1]}


app.run(port)
