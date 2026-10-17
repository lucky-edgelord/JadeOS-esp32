# Networking: Wi-Fi station from the config, a setup access point when there is none, or the
# emulated Ethernet card when running in QEMU. The device answers as <hostname>.local (mDNS).
import time, asyncio
from jade import config, sysinfo
from jade.log import log


async def _wait(cond, seconds):
    for _ in range(seconds * 10):
        if cond():
            return True
        await asyncio.sleep_ms(100)
    return cond()


async def up():
    try:
        import network
    except ImportError:
        sysinfo.net_info.update(mode="host", ip="127.0.0.1")
        return sysinfo.net_info
    cfg = config.load()
    info = sysinfo.net_info
    try:
        network.hostname(cfg["hostname"])
    except (AttributeError, ValueError, OSError):
        pass
    info["hostname"] = cfg["hostname"] + ".local"

    w = cfg["wifi"]
    if w["ssid"]:
        sta = network.WLAN(network.WLAN.IF_STA)
        sta.active(True)
        try:
            sta.connect(w["ssid"], w["password"])
        except OSError as e:
            log("net: connect failed", e)
        if await _wait(sta.isconnected, 20):
            info.update(mode="wifi", ip=sta.ifconfig()[0], ssid=w["ssid"])
            log("net: wifi", w["ssid"], info["ip"])
            try:
                import ntptime
                ntptime.settime()
            except Exception:
                pass
            return info
        log("net: could not join", w["ssid"], "- starting the setup access point")
        sta.active(False)

    ap = network.WLAN(network.WLAN.IF_AP)
    ap.active(True)
    ssid = "JadeOS-" + "".join("%02X" % b for b in ap.config("mac")[-2:])
    ap.config(essid=ssid, password=w["ap_password"], authmode=3)
    info.update(mode="ap", ip=ap.ifconfig()[0], ssid=ssid)
    log("net: access point", ssid, info["ip"], "password", w["ap_password"])
    return info
