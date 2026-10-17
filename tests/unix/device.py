# Runs JadeOS on the stock MicroPython unix port as if it were the device:
#   JADEOS_ROOT=<data dir> micropython tests/unix/device.py <api port> <llm url>
# <data dir> plays the flash (the device's whole filesystem lives under it).
import sys, os

port, llm = int(sys.argv[1]), sys.argv[2]
from jade import config, fs
for d in (fs.HOME, fs.VAULT, fs.WWW):
    fs.makedirs(d)
cfg = config.load()
cfg["cortex"]["url"] = llm
cfg["cortex"]["key"] = "test-key"
cfg["api"]["token"] = "test-token"
cfg["api"]["port"] = port
config.save()

from jade import app
app.run(port, serial=False)
