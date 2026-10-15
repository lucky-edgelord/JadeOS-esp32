# Settings in /root/jade/config.json. Missing keys fall back to DEFAULTS, so old files keep working.
import json, os

HOME = "/root/jade"
PATH = HOME + "/config.json"

DEFAULTS = {
    "hostname": "jade",
    "wifi": {"ssid": "", "password": "", "ap_password": "jadeos-setup"},
    "api": {"port": 80, "token": "", "origins": "*"},
    "vault": "/root/vault",
    "cortex": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "key": "",
        "max_tokens": 600,          # per reply
        "context_tokens": 6000,     # prompt budget per call (history is trimmed to fit)
        "daily_tokens": 200000,     # stop calling the model after this many tokens a day
        "temperature": 0.4,
        "timeout": 60,
        "tls_verify": False,        # True needs /root/jade/ca.pem
    },
    "agent": {"max_steps": 5, "context_hits": 4, "history": 12, "persona": ""},
    "shell": {
        "allow": ["ls", "cat", "head", "tail", "wc", "cksum", "df", "du", "free", "date", "pwd",
                  "echo", "printf", "mkdir", "cp", "mv", "rm", "help"],
    },
}

SECRETS = (("wifi", "password"), ("api", "token"), ("cortex", "key"))


def _merge(base, over):
    out = {}
    for k, v in base.items():
        if isinstance(v, dict):
            out[k] = _merge(v, over.get(k, {}) if isinstance(over.get(k), dict) else {})
        else:
            out[k] = over.get(k, v)
    for k, v in over.items():
        if k not in out:
            out[k] = v
    return out


def makedirs(path):
    cur = ""
    for part in path.strip("/").split("/"):
        cur += "/" + part
        try:
            os.mkdir(cur)
        except OSError:
            pass


def _token():
    return "".join("%02x" % b for b in os.urandom(16))


_cfg = None


def load(reload=False):
    global _cfg
    if _cfg is not None and not reload:
        return _cfg
    data = {}
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        pass
    _cfg = _merge(DEFAULTS, data)
    if not _cfg["api"]["token"]:
        _cfg["api"]["token"] = _token()
        save()
    return _cfg


def save():
    makedirs(HOME)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_cfg, f)
    os.rename(tmp, PATH)


def public():
    # the config without secrets (they show as "set" or "")
    out = json.loads(json.dumps(load()))
    for sec, key in SECRETS:
        out[sec][key] = "set" if out[sec][key] else ""
    return out


def update(changes):
    # merge a partial config; secrets sent as "set" are left alone
    cfg = load()
    for sec, val in changes.items():
        if isinstance(val, dict) and isinstance(cfg.get(sec), dict):
            for k, v in val.items():
                if (sec, k) in SECRETS and v == "set":
                    continue
                cfg[sec][k] = v
        else:
            cfg[sec] = val
    save()
    return public()
