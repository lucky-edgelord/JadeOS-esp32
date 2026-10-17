# A tiny log: printed, kept in RAM for the dashboard, and appended to /jade/logs/jade.log
# (rotated to jade.log.1 at 32 KB).
import time
from jade import fs

DIR = fs.HOME + "/logs"
PATH = DIR + "/jade.log"
MAX = 32 * 1024
_recent = []


def _stamp():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]


def log(*parts):
    line = _stamp() + " " + " ".join(str(p) for p in parts)
    print(line)
    _recent.append(line)
    if len(_recent) > 100:
        del _recent[0]
    try:
        try:
            if fs.stat(PATH)[6] > MAX:
                fs.rename(PATH, PATH + ".1")
        except OSError:
            fs.makedirs(DIR)
        with fs.open(PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def recent(n=50):
    return _recent[-n:]
