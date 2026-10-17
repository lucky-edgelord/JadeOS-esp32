# The device's filesystem, as JadeOS sees it: plain MicroPython `os` and `open` on the board's own
# LittleFS (the flash partition MicroPython mounts at "/"). Paths are always absolute, "/vault/Welcome.md".
#
# ROOT is "" on the board. The tests (MicroPython unix port) set JADEOS_ROOT to a scratch folder, so the
# whole device filesystem lives under it and nothing outside is touched.
import os

ROOT = ""
if hasattr(os, "getenv"):
    ROOT = (os.getenv("JADEOS_ROOT") or "").rstrip("/")

HOME = "/jade"           # config, logs, history, usage
VAULT = "/vault"         # the Markdown notes
WWW = "/www"             # the Deck (client page), served by the device itself
LIB = "/lib"             # extra Python libraries


def real(path):
    return ROOT + path if ROOT else path


def norm(path, cwd="/"):
    # absolute, no "." or ".." or doubled slashes: "a/../b" in /x → "/x/b"
    if not path.startswith("/"):
        path = cwd.rstrip("/") + "/" + path
    out = []
    for p in path.split("/"):
        if p == "" or p == ".":
            continue
        if p == "..":
            if out:
                out.pop()
            continue
        out.append(p)
    return "/" + "/".join(out)


def open(path, mode="r", *a, **kw):
    return __builtins_open(real(path), mode, *a, **kw)


__builtins_open = __import__("builtins").open


def stat(path):
    return os.stat(real(path) or "/")


def ilistdir(path):
    return os.ilistdir(real(path) or "/")


def listdir(path):
    return os.listdir(real(path) or "/")


def mkdir(path):
    os.mkdir(real(path))


def rmdir(path):
    os.rmdir(real(path))


def remove(path):
    os.remove(real(path))


def rename(a, b):
    os.rename(real(a), real(b))


def statvfs(path="/"):
    return os.statvfs(real(path) or "/")


def exists(path):
    try:
        stat(path)
        return True
    except OSError:
        return False


def isdir(path):
    try:
        return stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


def isfile(path):
    try:
        return stat(path)[0] & 0x8000 != 0
    except OSError:
        return False


def makedirs(path):
    cur = ""
    for part in path.strip("/").split("/"):
        if not part:
            continue
        cur += "/" + part
        try:
            mkdir(cur)
        except OSError:
            pass


def write(path, data, append=False):
    # write atomically (temp file, then rename) unless appending
    makedirs(path.rsplit("/", 1)[0])
    if append:
        with open(path, "ab" if isinstance(data, (bytes, bytearray)) else "a") as f:
            f.write(data)
        return
    tmp = path + ".tmp"
    with open(tmp, "wb" if isinstance(data, (bytes, bytearray)) else "w") as f:
        f.write(data)
    rename(tmp, path)


def read(path, limit=-1):
    with open(path) as f:
        return f.read(limit) if limit >= 0 else f.read()


def walk(path):
    # yields (path, is_dir) depth first, directories before their contents
    for name, kind, *_ in ilistdir(path):
        full = path.rstrip("/") + "/" + name
        d = bool(kind & 0x4000)
        yield full, d
        if d:
            for x in walk(full):
                yield x


def rmtree(path):
    for name, kind, *_ in list(ilistdir(path)):
        full = path.rstrip("/") + "/" + name
        if kind & 0x4000:
            rmtree(full)
        else:
            remove(full)
    rmdir(path)
