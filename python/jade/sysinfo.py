# Device status for the dashboard and the device_status tool.
import gc, os, sys, time
import jade
from jade import config

BOOT = time.ticks_ms()
net_info = {"mode": "none", "ip": "", "ssid": "", "hostname": ""}


def uptime_s():
    return time.ticks_diff(time.ticks_ms(), BOOT) // 1000


def status():
    gc.collect()
    out = {
        "name": "JadeOS",
        "version": jade.VERSION,
        "platform": sys.platform,
        "micropython": "%d.%d.%d" % sys.implementation.version[:3],
        "uptime_s": uptime_s(),
        "heap_free": gc.mem_free(),
        "heap_used": gc.mem_alloc(),
        "net": net_info,
    }
    try:
        s = os.statvfs("/root")
        out["storage_total"] = s[0] * s[2]
        out["storage_free"] = s[0] * s[3]
    except OSError:
        pass
    try:
        import esp32
        out["internal_free"] = esp32.idf_heap_info(esp32.HEAP_DATA)[0][2] if esp32.idf_heap_info(esp32.HEAP_DATA) else 0
        out["mcu_temp_c"] = esp32.mcu_temperature()
    except (ImportError, AttributeError, OSError):
        pass
    try:
        import machine
        out["cpu_mhz"] = machine.freq() // 1000000
    except (ImportError, AttributeError):
        pass
    try:
        from jade.cortex import cortex
        out["cortex"] = cortex.status()
    except Exception:
        pass
    return out
