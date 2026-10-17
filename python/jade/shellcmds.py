# The JadeOS shell's commands. Each is  fn(shell, args, stdin, out, err) → exit status (None = 0)
# and may be a plain function or `async def`. They work on the board's own filesystem through jade.fs.
import sys, gc, time
from jade import fs
from jade.shell import ShellError, _why

CMDS = {}


def cmd(*names):
    def deco(fn):
        for n in names:
            CMDS[n] = fn
        return fn
    return deco


# ---------------------------------------------------------------- helpers
def getopts(args, spec=""):
    # POSIX-ish short options: -la, -n5, -n 5. `spec` lists the options that take a value: "n:c:".
    # → (opts dict, positional list). A lone "-" and anything after "--" is positional; "-5" counts as -n5 when n takes a value.
    takes = set(c for i, c in enumerate(spec) if i + 1 < len(spec) and spec[i + 1] == ":")
    o = {}
    pos = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos.extend(args[i + 1:])
            break
        if len(a) > 1 and a[0] == "-" and not a.startswith("--"):
            if a[1:].isdigit() and "n" in takes:
                o["n"] = a[1:]
                i += 1
                continue
            k = 1
            while k < len(a):
                c = a[k]
                if c in takes:
                    v = a[k + 1:]
                    if not v:
                        i += 1
                        if i >= len(args):
                            raise ShellError("option -%s needs a value" % c)
                        v = args[i]
                    o[c] = v
                    break
                o[c] = True
                k += 1
        elif a.startswith("--") and len(a) > 2:
            o[a[2:]] = True
        else:
            pos.append(a)
        i += 1
    return o, pos


def split_lines(text):
    # lines keeping their newline
    out = []
    i = 0
    n = len(text)
    while i < n:
        j = text.find("\n", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j + 1])
        i = j + 1
    return out


class Src:
    # the lines of the named files, or of stdin; `status` becomes 1 if a file can't be read
    def __init__(self, sh, files, inp, err, name="stdin"):
        self.sh, self.files, self.inp, self.err, self.name = sh, files, inp, err, name
        self.status = 0

    def __iter__(self):
        if not self.files:
            for l in split_lines(self.inp or ""):
                yield self.name, l
            return
        for f in self.files:
            if f == "-":
                for l in split_lines(self.inp or ""):
                    yield "-", l
                continue
            p = self.sh.path(f)
            try:
                if fs.isdir(p):
                    self.err.write("%s: Is a directory\n" % f)
                    self.status = 1
                    continue
                fh = fs.open(p)
            except OSError as e:
                self.err.write("%s: %s\n" % (f, _why(e)))
                self.status = 1
                continue
            try:
                while True:
                    l = fh.readline()
                    if not l:
                        break
                    yield f, l
            finally:
                fh.close()


def human(n):
    f = float(n)
    for u in ("B", "K", "M", "G"):
        if f < 1024 or u == "G":
            return ("%d%s" % (f, u)) if u == "B" else ("%.1f%s" % (f, u) if f < 10 else "%d%s" % (f, u))
        f /= 1024
    return str(n)


def mtime_str(t):
    if not t:
        return "-"
    try:
        x = time.localtime(t)
        return "%04d-%02d-%02d %02d:%02d" % x[:5]
    except (OverflowError, ValueError, OSError):
        return "-"


# ---------------------------------------------------------------- navigation and listing
@cmd("pwd")
def c_pwd(sh, args, inp, out, err):
    out.write(sh.cwd + "\n")


@cmd("cd")
def c_cd(sh, args, inp, out, err):
    t = sh.path(args[0] if args else sh.env.get("HOME", "/"))
    if not fs.exists(t):
        raise OSError(2)
    if not fs.isdir(t):
        raise OSError(20)
    sh.cwd = t
    sh.env["PWD"] = t


def _entries(path):
    out = []
    for e in fs.ilistdir(path):
        name, kind = e[0], e[1]
        size = e[3] if len(e) > 3 else -1
        full = path.rstrip("/") + "/" + name
        if size < 0 or kind == 0:
            try:
                st = fs.stat(full)
                size, kind = st[6], (st[0] & 0xF000)
            except OSError:
                size = 0
        out.append((name, bool(kind & 0x4000), size, full))
    return out


def _ls_line(o, name, isdir, size, full, shortname=None):
    if "l" in o:
        try:
            mt = fs.stat(full)[8]
        except OSError:
            mt = 0
        return "%s %8s  %s  %s%s" % ("d" if isdir else "-", human(size) if "h" in o else size, mtime_str(mt), shortname or name, "/" if isdir else "")
    return (shortname or name) + ("/" if isdir else "")


@cmd("ls", "dir")
def c_ls(sh, args, inp, out, err):
    o, files = getopts(args)
    files = files or ["."]
    status = 0
    many = len(files) > 1 or "R" in o

    def listing(show, path, label):
        try:
            ents = _entries(path)
        except OSError as e:
            err.write("ls: %s: %s\n" % (label, _why(e)))
            return 1
        if "a" not in o:
            ents = [e for e in ents if not e[0].startswith(".")]
        if "S" in o:
            ents.sort(key=lambda e: -e[2])
        else:
            ents.sort(key=lambda e: e[0].lower())
        if show:
            out.write(label + ":\n")
        for name, d, size, full in ents:
            out.write(_ls_line(o, name, d, size, full) + "\n")
        if "R" in o:
            for name, d, size, full in ents:
                if d:
                    out.write("\n")
                    listing(True, full, (label.rstrip("/") + "/" + name) if label != "." else "./" + name)
        return 0

    for k, f in enumerate(files):
        p = sh.path(f)
        if not fs.exists(p):
            err.write("ls: %s: No such file or directory\n" % f)
            status = 1
            continue
        if fs.isdir(p) and "d" not in o:
            if many and k:
                out.write("\n")
            status |= listing(many, p, f)
        else:
            st = fs.stat(p)
            out.write(_ls_line(o, p.rsplit("/", 1)[-1], bool(st[0] & 0x4000), st[6], p, f) + "\n")
    return status


@cmd("stat")
def c_stat(sh, args, inp, out, err):
    status = 0
    for f in args:
        try:
            st = fs.stat(sh.path(f))
        except OSError as e:
            err.write("stat: %s: %s\n" % (f, _why(e)))
            status = 1
            continue
        out.write("  File: %s\n  Size: %d\n  Type: %s\n Mtime: %s\n" % (f, st[6], "directory" if st[0] & 0x4000 else "file", mtime_str(st[8])))
    return status


def _du(path):
    total = 0
    for name, d, size, full in _entries(path):
        total += _du(full) if d else size
    return total


@cmd("du")
def c_du(sh, args, inp, out, err):
    o, files = getopts(args)
    hum = "h" in o
    for f in files or ["."]:
        p = sh.path(f)
        if not fs.exists(p):
            err.write("du: %s: No such file or directory\n" % f)
            continue
        if fs.isdir(p):
            if "s" in o:
                out.write("%s\t%s\n" % (human(_du(p)) if hum else _du(p), f))
            else:
                def walk(path, label):
                    t = 0
                    for name, d, size, full in sorted(_entries(path), key=lambda e: e[0]):
                        lab = label.rstrip("/") + "/" + name
                        if d:
                            t += walk(full, lab)
                        else:
                            t += size
                            if "a" in o:
                                out.write("%s\t%s\n" % (human(size) if hum else size, lab))
                    out.write("%s\t%s\n" % (human(t) if hum else t, label))
                    return t
                walk(p, f)
        else:
            s = fs.stat(p)[6]
            out.write("%s\t%s\n" % (human(s) if hum else s, f))


@cmd("df")
def c_df(sh, args, inp, out, err):
    o, files = getopts(args)
    s = fs.statvfs("/")
    total, free = s[0] * s[2], s[0] * s[3]
    used = total - free
    f = human if "h" in o else (lambda n: str(n // 1024))
    out.write("Filesystem       %6s %6s %6s Use%% Mounted on\n" % ("Size" if "h" in o else "1K-blk", "Used", "Avail"))
    out.write("flash             %6s %6s %6s %3d%% /\n" % (f(total), f(used), f(free), (used * 100 // total) if total else 0))


@cmd("free")
def c_free(sh, args, inp, out, err):
    o, _ = getopts(args)
    gc.collect()
    f = human if "h" in o else (lambda n: str(n // 1024))
    a, fr = gc.mem_alloc(), gc.mem_free()
    out.write("%-14s %9s %9s %9s\n" % ("", "total", "used", "free"))
    out.write("%-14s %9s %9s %9s\n" % ("Python heap:", f(a + fr), f(a), f(fr)))
    try:
        import esp32
        for kind, label in ((esp32.HEAP_DATA, "Internal RAM:"), (esp32.HEAP_EXEC, "Code RAM:")):
            info = esp32.idf_heap_info(kind)
            if info:
                tot = sum(x[0] for x in info)
                fre = sum(x[1] for x in info)
                out.write("%-14s %9s %9s %9s\n" % (label, f(tot), f(tot - fre), f(fre)))
    except (ImportError, AttributeError, OSError):
        pass


# ---------------------------------------------------------------- files
@cmd("cat")
def c_cat(sh, args, inp, out, err):
    o, files = getopts(args)
    if not files and inp is None:
        return 0
    n = 0
    src = Src(sh, files, inp, err)
    for _, l in src:
        n += 1
        out.write(("%6d\t" % n) + l if "n" in o else l)
    return src.status


@cmd("head")
def c_head(sh, args, inp, out, err):
    o, files = getopts(args, "n:c:")
    n = int(o.get("n", 10))
    multi = len(files) > 1
    status = 0
    for f in (files or [None]):
        if multi:
            out.write("==> %s <==\n" % f)
        src = Src(sh, [f] if f else [], inp, err)
        k = 0
        for _, l in src:
            if k >= n:
                break
            out.write(l)
            k += 1
        status |= src.status
    return status


@cmd("tail")
def c_tail(sh, args, inp, out, err):
    o, files = getopts(args, "n:c:")
    n = int(o.get("n", 10))
    multi = len(files) > 1
    status = 0
    for f in (files or [None]):
        if multi:
            out.write("==> %s <==\n" % f)
        src = Src(sh, [f] if f else [], inp, err)
        buf = []
        for _, l in src:
            buf.append(l)
            if len(buf) > n:
                del buf[0]
        for l in buf:
            out.write(l)
        status |= src.status
    return status


@cmd("wc")
def c_wc(sh, args, inp, out, err):
    o, files = getopts(args)
    want = [k for k in "lwc" if k in o] or ["l", "w", "c"]
    total = [0, 0, 0]
    status = 0
    for f in (files or [None]):
        src = Src(sh, [f] if f else [], inp, err)
        cnt = [0, 0, 0]
        for _, l in src:
            cnt[0] += 1 if l.endswith("\n") else 0
            cnt[1] += len(l.split())
            cnt[2] += len(l.encode())
        status |= src.status
        for i in range(3):
            total[i] += cnt[i]
        out.write(" ".join("%7d" % cnt["lwc".index(k)] for k in want) + ((" " + f) if f else "") + "\n")
    if len(files) > 1:
        out.write(" ".join("%7d" % total["lwc".index(k)] for k in want) + " total\n")
    return status


@cmd("touch")
def c_touch(sh, args, inp, out, err):
    for f in args:
        p = sh.path(f)
        if not fs.exists(p):
            fs.open(p, "w").close()


@cmd("mkdir")
def c_mkdir(sh, args, inp, out, err):
    o, dirs = getopts(args)
    if not dirs:
        raise ShellError("missing operand")
    for d in dirs:
        p = sh.path(d)
        if "p" in o:
            fs.makedirs(p)
        else:
            fs.mkdir(p)


@cmd("rmdir")
def c_rmdir(sh, args, inp, out, err):
    for d in args:
        fs.rmdir(sh.path(d))


@cmd("rm")
def c_rm(sh, args, inp, out, err):
    o, files = getopts(args)
    force = "f" in o
    if not files and not force:
        raise ShellError("missing operand")
    status = 0
    for f in files:
        p = sh.path(f)
        if p == "/":
            err.write("rm: refusing to remove /\n")
            status = 1
            continue
        if not fs.exists(p):
            if not force:
                err.write("rm: %s: No such file or directory\n" % f)
                status = 1
            continue
        if fs.isdir(p):
            if "r" in o or "R" in o:
                fs.rmtree(p)
            else:
                err.write("rm: %s: Is a directory (use -r)\n" % f)
                status = 1
        else:
            fs.remove(p)
    return status


def _copy_file(a, b):
    with fs.open(a, "rb") as src, fs.open(b, "wb") as dst:
        while True:
            chunk = src.read(1024)
            if not chunk:
                break
            dst.write(chunk)


def _copy(a, b, rec):
    if fs.isdir(a):
        if not rec:
            raise ShellError("%s is a directory (use -r)" % a)
        if b == a or b.startswith(a.rstrip("/") + "/"):
            raise ShellError("cannot copy a directory into itself")
        try:
            fs.mkdir(b)
        except OSError:
            pass
        for name, d, size, full in _entries(a):
            _copy(full, b.rstrip("/") + "/" + name, rec)
    else:
        _copy_file(a, b)


@cmd("cp")
def c_cp(sh, args, inp, out, err):
    o, p = getopts(args)
    if len(p) < 2:
        raise ShellError("usage: cp [-r] SRC... DEST")
    dst = sh.path(p[-1])
    srcs = p[:-1]
    for s in srcs:
        a = sh.path(s)
        if not fs.exists(a):
            err.write("cp: %s: No such file or directory\n" % s)
            return 1
        b = dst
        if fs.isdir(dst):
            b = dst.rstrip("/") + "/" + a.rsplit("/", 1)[-1]
        elif len(srcs) > 1:
            raise ShellError("target is not a directory")
        _copy(a, b, "r" in o or "R" in o)


@cmd("mv")
def c_mv(sh, args, inp, out, err):
    o, p = getopts(args)
    if len(p) < 2:
        raise ShellError("usage: mv SRC... DEST")
    dst = sh.path(p[-1])
    for s in p[:-1]:
        a = sh.path(s)
        if not fs.exists(a):
            err.write("mv: %s: No such file or directory\n" % s)
            return 1
        b = dst
        if fs.isdir(dst):
            b = dst.rstrip("/") + "/" + a.rsplit("/", 1)[-1]
        elif len(p) > 2:
            raise ShellError("target is not a directory")
        if a == b:
            continue
        if fs.isdir(a) and (b + "/").startswith(a.rstrip("/") + "/"):
            raise ShellError("cannot move a directory into itself")
        if fs.isfile(b):
            fs.remove(b)
        fs.rename(a, b)


@cmd("find")
def c_find(sh, args, inp, out, err):
    roots = []
    i = 0
    while i < len(args) and not args[i].startswith("-"):
        roots.append(args[i])
        i += 1
    name = iname = typ = None
    maxd = 99
    while i < len(args):
        a = args[i]
        v = args[i + 1] if i + 1 < len(args) else None
        if a == "-name":
            name = v
        elif a == "-iname":
            iname = v.lower()
        elif a == "-type":
            typ = v
        elif a == "-maxdepth":
            maxd = int(v)
        else:
            raise ShellError("unknown option " + a)
        i += 2
    from jade.shell import fnmatch
    for r in roots or ["."]:
        base = sh.path(r)
        if not fs.exists(base):
            err.write("find: %s: No such file or directory\n" % r)
            continue

        def visit(path, label, depth):
            isd = fs.isdir(path)
            nm = path.rsplit("/", 1)[-1]
            ok = (name is None or fnmatch(nm, name)) and (iname is None or fnmatch(nm.lower(), iname)) and (typ is None or (typ == "d") == isd)
            if ok:
                out.write(label + "\n")
            if isd and depth < maxd:
                for ent in sorted(fs.ilistdir(path), key=lambda e: e[0]):
                    visit(path.rstrip("/") + "/" + ent[0], label.rstrip("/") + "/" + ent[0], depth + 1)
        visit(base, r, 0)


# ---------------------------------------------------------------- text
@cmd("echo")
def c_echo(sh, args, inp, out, err):
    nl = True
    esc = False
    while args and args[0] in ("-n", "-e", "-ne", "-en"):
        if "n" in args[0]:
            nl = False
        if "e" in args[0]:
            esc = True
        args = args[1:]
    s = " ".join(args)
    if esc:
        s = _unescape(s)
    out.write(s + ("\n" if nl else ""))


def _unescape(s):
    r = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            d = s[i + 1]
            m = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "a": "\a", "0": "\0", "e": "\x1b"}
            if d in m:
                r += m[d]
                i += 2
                continue
        r += c
        i += 1
    return r


@cmd("printf")
def c_printf(sh, args, inp, out, err):
    if not args:
        raise ShellError("usage: printf FORMAT [ARGS]")
    fmt = _unescape(args[0])
    vals = args[1:]
    res = ""
    i = 0
    vi = 0
    while i < len(fmt):
        c = fmt[i]
        if c != "%":
            res += c
            i += 1
            continue
        j = i + 1
        while j < len(fmt) and fmt[j] in "-+ #0123456789.":
            j += 1
        if j >= len(fmt):
            res += fmt[i:]
            break
        conv = fmt[j]
        spec = fmt[i:j]
        if conv == "%":
            res += "%"
        else:
            v = vals[vi] if vi < len(vals) else ""
            vi += 1
            if conv in "di":
                res += (spec + "d") % int(v or 0)
            elif conv in "xXo":
                res += (spec + conv) % int(v or 0)
            elif conv in "feg":
                res += (spec + conv) % float(v or 0)
            elif conv == "c":
                res += v[:1]
            else:
                res += (spec + "s") % v
        i = j + 1
    out.write(res)


def _lower_pat(p):
    # lower-case a regex for case-insensitive matching, leaving escapes like \S and \D alone
    out = ""
    i = 0
    while i < len(p):
        if p[i] == "\\" and i + 1 < len(p):
            out += p[i:i + 2]
            i += 2
        else:
            out += p[i].lower()
            i += 1
    return out


def _regex(pat):
    import re
    try:
        return re.compile(pat)
    except Exception:
        raise ShellError("bad pattern: " + pat)


@cmd("grep", "egrep")
def c_grep(sh, args, inp, out, err):
    o, p = getopts(args, "e:")
    pat = o.get("e")
    if pat is None:
        if not p:
            raise ShellError("usage: grep [-ivncrlFH] PATTERN [FILE...]")
        pat = p.pop(0)
    ignore = "i" in o
    fixed = "F" in o
    rx = None if fixed else _regex(_lower_pat(pat) if ignore else pat)   # MicroPython's re has no ignore-case flag
    needle = pat.lower() if ignore else pat
    files = p
    if "r" in o or "R" in o:
        exp = []
        for f in files or ["."]:
            path = sh.path(f)
            if fs.isdir(path):
                for full, d in fs.walk(path):
                    if not d:
                        exp.append(full)
            else:
                exp.append(f)
        files = exp
    show = len(files) > 1 or "r" in o or "R" in o or "H" in o
    total = 0
    src = Src(sh, files, inp, err)
    lastf = None
    count = {}
    lineno = {}
    for f, l in src:
        lineno[f] = lineno.get(f, 0) + 1
        line = l.rstrip("\n")
        hay = line.lower() if ignore else line
        m = (needle in hay) if fixed else (rx.search(hay) is not None)
        if "v" in o:
            m = not m
        if not m:
            continue
        total += 1
        count[f] = count.get(f, 0) + 1
        if "l" in o:
            if f != lastf:
                out.write(f + "\n")
                lastf = f
            continue
        if "c" in o:
            continue
        pre = ""
        if show:
            pre += f + ":"
        if "n" in o:
            pre += "%d:" % lineno[f]
        out.write(pre + line + "\n")
    if "c" in o:
        for f in (files or ["stdin"]):
            out.write(("%s:" % f if show else "") + "%d\n" % count.get(f if files else "stdin", 0))
    return 0 if total or src.status == 0 and "c" in o else 1


@cmd("sort")
def c_sort(sh, args, inp, out, err):
    o, files = getopts(args)
    src = Src(sh, files, inp, err)
    lines = [l if l.endswith("\n") else l + "\n" for _, l in src]
    if "n" in o:
        def key(l):
            try:
                return float(l.split()[0]) if l.split() else 0.0
            except ValueError:
                return 0.0
        lines.sort(key=key)
    else:
        lines.sort()
    if "r" in o:
        lines.reverse()
    if "u" in o:
        u = []
        for l in lines:
            if not u or u[-1] != l:
                u.append(l)
        lines = u
    for l in lines:
        out.write(l)
    return src.status


@cmd("uniq")
def c_uniq(sh, args, inp, out, err):
    o, files = getopts(args)
    src = Src(sh, files, inp, err)
    prev, n = None, 0
    for _, l in src:
        if l == prev:
            n += 1
            continue
        if prev is not None:
            out.write(("%7d " % n if "c" in o else "") + prev)
        prev, n = l, 1
    if prev is not None:
        out.write(("%7d " % n if "c" in o else "") + prev)
    return src.status


@cmd("cut")
def c_cut(sh, args, inp, out, err):
    o, files = getopts(args, "d:f:c:")
    d = o.get("d", "\t")

    def ranges(spec):
        r = []
        for part in spec.split(","):
            a, _, b = part.partition("-")
            r.append((int(a) if a else 1, int(b) if b else 9999) if "-" in part else (int(a), int(a)))
        return r

    fr = ranges(o["f"]) if "f" in o else None
    cr = ranges(o["c"]) if "c" in o else None
    if not fr and not cr:
        raise ShellError("usage: cut -d DELIM -f LIST | -c LIST")
    src = Src(sh, files, inp, err)
    for _, l in src:
        l = l.rstrip("\n")
        if fr:
            parts = l.split(d)
            sel = [parts[i - 1] for a, b in fr for i in range(a, min(b, len(parts)) + 1)]
            out.write(d.join(sel) + "\n")
        else:
            out.write("".join(l[a - 1:b] for a, b in cr) + "\n")
    return src.status


@cmd("tee")
def c_tee(sh, args, inp, out, err):
    o, files = getopts(args)
    text = inp or ""
    out.write(text)
    for f in files:
        with fs.open(sh.path(f), "a" if "a" in o else "w") as fh:
            fh.write(text)


@cmd("tr")
def c_tr(sh, args, inp, out, err):
    o, p = getopts(args)

    def expand(s):
        r = ""
        i = 0
        while i < len(s):
            if i + 2 < len(s) and s[i + 1] == "-":
                r += "".join(chr(c) for c in range(ord(s[i]), ord(s[i + 2]) + 1))
                i += 3
            else:
                r += s[i]
                i += 1
        return r
    if "d" in o:
        if not p:
            raise ShellError("usage: tr -d SET")
        dele = expand(p[0])
        out.write("".join(c for c in (inp or "") if c not in dele))
        return
    if len(p) < 2:
        raise ShellError("usage: tr SET1 SET2")
    a, b = expand(p[0]), expand(p[1])
    b = b + b[-1] * (len(a) - len(b)) if b else b
    out.write("".join(b[a.index(c)] if c in a else c for c in (inp or "")))


@cmd("basename")
def c_basename(sh, args, inp, out, err):
    b = args[0].rstrip("/").rsplit("/", 1)[-1] if args else ""
    if len(args) > 1 and b.endswith(args[1]):
        b = b[:-len(args[1])]
    out.write(b + "\n")


@cmd("dirname")
def c_dirname(sh, args, inp, out, err):
    p = args[0].rstrip("/") if args else ""
    out.write((p.rsplit("/", 1)[0] or ("/" if p.startswith("/") else ".")) if "/" in p else ".")
    out.write("\n")


@cmd("seq")
def c_seq(sh, args, inp, out, err):
    a = [int(x) for x in args]
    lo, st, hi = (1, 1, a[0]) if len(a) == 1 else (a[0], 1, a[1]) if len(a) == 2 else (a[0], a[1], a[2])
    i = lo
    while (i <= hi) if st > 0 else (i >= hi):
        out.write("%d\n" % i)
        i += st


@cmd("true")
def c_true(sh, args, inp, out, err):
    return 0


@cmd("false")
def c_false(sh, args, inp, out, err):
    return 1


@cmd("test", "[")
def c_test(sh, args, inp, out, err):
    a = list(args)
    if a and a[-1] == "]":
        a.pop()
    neg = False
    if a and a[0] == "!":
        neg = True
        a = a[1:]
    r = _test(sh, a)
    return 0 if r != neg else 1


def _test(sh, a):
    if not a:
        return False
    if len(a) == 1:
        return a[0] != ""
    if len(a) == 2:
        op, v = a
        if op == "-e":
            return fs.exists(sh.path(v))
        if op == "-f":
            return fs.isfile(sh.path(v))
        if op == "-d":
            return fs.isdir(sh.path(v))
        if op == "-s":
            return fs.exists(sh.path(v)) and fs.stat(sh.path(v))[6] > 0
        if op == "-z":
            return v == ""
        if op == "-n":
            return v != ""
        raise ShellError("unknown test " + op)
    if len(a) == 3:
        x, op, y = a
        if op in ("=", "=="):
            return x == y
        if op == "!=":
            return x != y
        try:
            i, j = int(x), int(y)
        except ValueError:
            raise ShellError("integer expected")
        return {"-eq": i == j, "-ne": i != j, "-lt": i < j, "-le": i <= j, "-gt": i > j, "-ge": i >= j}[op]
    raise ShellError("too many arguments")


@cmd("hexdump", "xxd")
def c_hexdump(sh, args, inp, out, err):
    o, files = getopts(args)
    for f in files:
        off = 0
        with fs.open(sh.path(f), "rb") as fh:
            while True:
                b = fh.read(16)
                if not b:
                    break
                hexs = " ".join("%02x" % c for c in b)
                out.write("%08x  %-47s  |%s|\n" % (off, hexs, "".join(chr(c) if 32 <= c < 127 else "." for c in b)))
                off += len(b)


def _hash(name):
    def fn(sh, args, inp, out, err):
        import hashlib
        status = 0
        for f in (args or ["-"]):
            h = getattr(hashlib, name)()
            if f == "-":
                h.update((inp or "").encode())
            else:
                try:
                    with fs.open(sh.path(f), "rb") as fh:
                        while True:
                            b = fh.read(1024)
                            if not b:
                                break
                            h.update(b)
                except OSError as e:
                    err.write("%s: %s: %s\n" % (name, f, _why(e)))
                    status = 1
                    continue
            out.write("%s  %s\n" % ("".join("%02x" % c for c in h.digest()), f))
        return status
    return fn


CMDS["md5sum"] = _hash("md5")
CMDS["sha1sum"] = _hash("sha1")
CMDS["sha256sum"] = _hash("sha256")


@cmd("cksum")
def c_cksum(sh, args, inp, out, err):
    # POSIX cksum: CRC-32 (polynomial 0x04C11DB7, MSB first) over the bytes, then the length bytes
    tbl = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
            c &= 0xFFFFFFFF
        tbl.append(c)
    status = 0
    for f in (args or ["-"]):
        crc = 0
        n = 0

        def feed(buf):
            nonlocal crc, n
            for b in buf:
                crc = ((crc << 8) & 0xFFFFFFFF) ^ tbl[((crc >> 24) ^ b) & 0xFF]
            n += len(buf)
        try:
            if f == "-":
                feed((inp or "").encode())
            else:
                with fs.open(sh.path(f), "rb") as fh:
                    while True:
                        b = fh.read(512)
                        if not b:
                            break
                        feed(b)
        except OSError as e:
            err.write("cksum: %s: %s\n" % (f, _why(e)))
            status = 1
            continue
        ln = n
        tail = bytearray()
        while ln:
            tail.append(ln & 0xFF)
            ln >>= 8
        feed(bytes(tail))
        n -= len(tail)
        out.write("%d %d%s\n" % (~crc & 0xFFFFFFFF, n, "" if f == "-" else " " + f))
    return status


@cmd("base64")
def c_base64(sh, args, inp, out, err):
    import binascii
    o, files = getopts(args)
    if files:
        with fs.open(sh.path(files[0]), "rb") as fh:
            data = fh.read()
    else:
        data = (inp or "").encode()
    if "d" in o:
        out.write(binascii.a2b_base64(data).decode())
    else:
        out.write(binascii.b2a_base64(data).decode())


# ---------------------------------------------------------------- environment and system
@cmd("env", "printenv")
def c_env(sh, args, inp, out, err):
    for k in sorted(sh.env):
        out.write("%s=%s\n" % (k, sh.env[k]))


@cmd("export", "set")
def c_export(sh, args, inp, out, err):
    if not args:
        return c_env(sh, args, inp, out, err)
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            sh.env[k] = v


@cmd("unset")
def c_unset(sh, args, inp, out, err):
    for a in args:
        sh.env.pop(a, None)


@cmd("history")
def c_history(sh, args, inp, out, err):
    for i, l in enumerate(sh.history):
        out.write("%4d  %s\n" % (i + 1, l))


@cmd("clear", "cls")
def c_clear(sh, args, inp, out, err):
    out.write("\x1b[2J\x1b[H")


@cmd("exit", "quit", "logout")
def c_exit(sh, args, inp, out, err):
    sh.stop = True
    return int(args[0]) if args else sh.status


@cmd("date")
def c_date(sh, args, inp, out, err):
    t = time.localtime()
    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    mons = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    out.write("%s %s %2d %02d:%02d:%02d %d\n" % (days[t[6]], mons[t[1] - 1], t[2], t[3], t[4], t[5], t[0]))


@cmd("uptime")
def c_uptime(sh, args, inp, out, err):
    from jade import sysinfo
    s = sysinfo.uptime_s()
    out.write("up %dd %02d:%02d:%02d\n" % (s // 86400, s // 3600 % 24, s // 60 % 60, s % 60))


@cmd("sleep")
async def c_sleep(sh, args, inp, out, err):
    import asyncio
    await asyncio.sleep_ms(int(float(args[0]) * 1000) if args else 0)


@cmd("hostname")
def c_hostname(sh, args, inp, out, err):
    from jade import config
    out.write(config.load()["hostname"] + "\n")


@cmd("uname")
def c_uname(sh, args, inp, out, err):
    v = sys.implementation
    out.write("%s MicroPython %s %s\n" % (sys.platform, ".".join(str(x) for x in v.version[:3]), getattr(v, "_machine", "")))


@cmd("which", "type")
def c_which(sh, args, inp, out, err):
    st = 0
    for a in args:
        if a in CMDS:
            out.write("%s: shell command\n" % a)
        else:
            err.write("%s: not found\n" % a)
            st = 1
    return st


@cmd("help", "?")
def c_help(sh, args, inp, out, err):
    names = sorted(set(CMDS))
    out.write("JadeOS shell. Commands:\n")
    line = " "
    for n in names:
        if len(line) + len(n) + 1 > 78:
            out.write(line + "\n")
            line = " "
        line += " " + n
    out.write(line + "\n\nPipes (|), lists (; && ||), redirects (> >> < 2>), $VARS and *.globs work.\n`jade ask <question>` talks to Jade; `py <code>` runs Python.\n")


@cmd("reboot", "reset")
def c_reboot(sh, args, inp, out, err):
    out.write("rebooting...\n")
    import machine
    machine.reset()


@cmd("gc")
def c_gc(sh, args, inp, out, err):
    before = gc.mem_free()
    gc.collect()
    out.write("freed %d bytes (%d free)\n" % (gc.mem_free() - before, gc.mem_free()))


@cmd("py", "python")
def c_py(sh, args, inp, out, err):
    # py <expression or statements> | py script.py [args]
    if not args:
        raise ShellError("usage: py <code>   |   py <script.py> [args]")
    if args[0].endswith(".py") and fs.isfile(sh.path(args[0])):
        return _run_file(sh, args, out, err)
    code = " ".join(args)
    g = {"__name__": "__jadepy__"}
    try:
        r = eval(code, g)
        if r is not None:
            out.write(repr(r) + "\n")
    except SyntaxError:
        exec(code, g)


def _run_file(sh, args, out, err):
    src = fs.read(sh.path(args[0]))
    saved = sys.argv if hasattr(sys, "argv") else None
    try:
        sys.argv = list(args)
    except Exception:
        pass
    # print() from the script goes to the console; the shell captures nothing from it
    exec(compile(src, args[0], "exec"), {"__name__": "__main__"})
    if saved is not None:
        try:
            sys.argv = saved
        except Exception:
            pass


@cmd("run")
def c_run(sh, args, inp, out, err):
    if not args:
        raise ShellError("usage: run <script.py> [args]")
    return _run_file(sh, args, out, err)


@cmd("mip")
async def c_mip(sh, args, inp, out, err):
    import mip
    if not args or args[0] != "install" or len(args) < 2:
        raise ShellError("usage: mip install <package> [--target /lib]")
    target = fs.real("/lib")
    pk = [a for a in args[1:] if not a.startswith("-")]
    if "--target" in args:
        target = fs.real(args[args.index("--target") + 1])
    mip.install(pk[0], target=target)


# ---------------------------------------------------------------- network
@cmd("ifconfig", "ip")
def c_ifconfig(sh, args, inp, out, err):
    try:
        import network
    except ImportError:
        out.write("no network on this host\n")
        return 1
    for label, mk in (("wifi (station)", lambda: network.WLAN(network.WLAN.IF_STA)), ("wifi (access point)", lambda: network.WLAN(network.WLAN.IF_AP))):
        try:
            w = mk()
        except Exception:
            continue
        if not w.active():
            continue
        ip, mask, gw, dns = w.ifconfig()
        out.write("%s: ip %s mask %s gw %s dns %s%s\n" % (label, ip, mask, gw, dns, ("  connected" if w.isconnected() else "  not connected") if label.startswith("wifi (s") else ""))


@cmd("wifi")
async def c_wifi(sh, args, inp, out, err):
    import network
    from jade import config
    sub = args[0] if args else "status"
    sta = network.WLAN(network.WLAN.IF_STA)
    if sub == "scan":
        sta.active(True)
        for ssid, bssid, ch, rssi, auth, hid in sorted(sta.scan(), key=lambda x: -x[3]):
            out.write("%4d dBm  ch %-2d  %s\n" % (rssi, ch, ssid.decode() if isinstance(ssid, bytes) else ssid))
    elif sub == "connect":
        if len(args) < 2:
            raise ShellError("usage: wifi connect <ssid> [password]")
        import asyncio
        cfg = config.load()
        cfg["wifi"]["ssid"] = args[1]
        cfg["wifi"]["password"] = args[2] if len(args) > 2 else ""
        config.save()
        sta.active(True)
        sta.connect(args[1], cfg["wifi"]["password"])
        for _ in range(200):
            if sta.isconnected():
                break
            await asyncio.sleep_ms(100)
        out.write(("connected: %s\n" % sta.ifconfig()[0]) if sta.isconnected() else "not connected (saved; check the password)\n")
        return 0 if sta.isconnected() else 1
    elif sub == "status":
        c_ifconfig(sh, [], inp, out, err)
    else:
        raise ShellError("usage: wifi scan | connect <ssid> [password] | status")


@cmd("wget", "curl")
async def c_wget(sh, args, inp, out, err):
    o, p = getopts(args, "o:O:X:d:H:")
    if not p:
        raise ShellError("usage: wget [-o FILE] URL   |   curl [-X METHOD] [-d DATA] URL")
    from jade import httpc
    method = o.get("X", "POST" if "d" in o else "GET")
    body = o["d"].encode() if "d" in o else None
    headers = {}
    if "H" in o:
        k, _, v = o["H"].partition(":")
        headers[k.strip()] = v.strip()
    status, rh, raw = await httpc._request(method, p[0], body, headers, False)
    dest = o.get("o") or o.get("O")
    if dest:
        fs.write(sh.path(dest), raw)
        out.write("saved %d bytes to %s\n" % (len(raw), dest))
    else:
        out.write(raw.decode())
    return 0 if status < 400 else 22


# ---------------------------------------------------------------- Jade
HELP_JADE = """jade ask <question>     ask Jade (searches the vault, may use tools)
jade search <words>     search the vault
jade tool <name> [json] run one of Jade's tools (jade tools lists them)
jade status             memory, storage, network, model budget
jade history | clear    show / forget the conversation"""


@cmd("jade")
async def c_jade(sh, args, inp, out, err):
    import json
    from jade import vault, tools, sysinfo
    sub = args[0] if args else "help"
    rest = " ".join(args[1:]).strip()
    if sub in ("help", "-h", "--help"):
        out.write(HELP_JADE + "\n")
        return 0
    if sub == "ask":
        q = rest or (inp or "").strip()
        if not q:
            raise ShellError("usage: jade ask <question>")
        from jade.agent import agent
        res = await agent.think(q)
        notes = res.get("notes") or []
        out.write(res["reply"] + (("\n\n(notes: %s)" % ", ".join(notes)) if notes else "") + "\n")
        return 1 if res.get("error") else 0
    if sub == "search":
        hits = await vault.asearch(rest or (inp or "").strip(), 8)
        if not hits:
            out.write("no matches\n")
            return 1
        for h in hits:
            out.write("%-32s %4d  %s\n" % (h["path"] + ":" + str(h["line"]), h["score"], h["snippet"]))
        return 0
    if sub == "tool":
        name, _, a = rest.partition(" ")
        ok, text = await tools.run(name, a or "{}")
        out.write(text + "\n")
        return 0 if ok else 1
    if sub == "tools":
        for t in tools.listing():
            out.write("%-14s %s\n" % (t["name"], t["description"]))
        return 0
    if sub == "status":
        out.write(json.dumps(sysinfo.status()) + "\n")
        return 0
    if sub == "history":
        from jade.agent import agent
        out.write(("\n".join("%s: %s" % (m["role"], m["content"]) for m in agent.history) or "(empty)") + "\n")
        return 0
    if sub == "clear":
        from jade.agent import agent
        agent.clear()
        out.write("forgotten\n")
        return 0
    err.write("unknown jade command: %s\n%s\n" % (sub, HELP_JADE))
    return 2


def _alias(sub):
    async def fn(sh, args, inp, out, err):
        return await c_jade(sh, [sub] + list(args), inp, out, err)
    return fn


for _n in ("ask", "search", "tool", "status"):
    CMDS["jade-" + _n] = _alias(_n)
CMDS["jade-py"] = c_py


@cmd("note")
def c_note(sh, args, inp, out, err):
    # note <path> [text...]: print a note, or append text to it (the vault, not the file system)
    from jade import vault
    if not args:
        raise ShellError("usage: note <path> [text to append]")
    path = args[0]
    if len(args) == 1 and inp is None:
        out.write(vault.read(path))
        return
    text = " ".join(args[1:]) if len(args) > 1 else (inp or "")
    vault.write(path, text.rstrip("\n") + "\n", append=True)
