# JadeOS boot (frozen). BreezyBox has already mounted the LittleFS partition at /root through
# ESP-IDF; here MicroPython gets the same files through VfsJade, so Python, the shell and the C
# indexer share one filesystem.
import gc, os, sys, jadecore

try:
    os.umount("/")
except OSError:
    pass
os.mount(jadecore.VfsJade(), "/")
try:
    os.chdir("/root")
except OSError:
    pass
for d in ("/root/lib", "/root/jade", "/root/vault"):
    try:
        os.mkdir(d)
    except OSError:
        pass
sys.path.append("/root/lib")

try:
    os.stat("/root/main.py")
except OSError:
    with open("/root/main.py", "w") as f:
        f.write("# Starts Jade. Edit freely; create /root/jade/SAFE to skip.\nimport jade.app\njade.app.run()\n")

gc.collect()
