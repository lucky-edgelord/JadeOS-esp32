# The port's modules (without its _boot.py, which mounts a FAT/LittleFS partition we don't have)
freeze("$(PORT_DIR)/modules", ("apa106.py", "espnow.py", "flashbdev.py", "inisetup.py", "machine.py"))
include("$(MPY_DIR)/extmod/asyncio")
require("bundle-networking")
# Jade
include("../../../python/manifest.py")
