# The JadeOS shell: a small POSIX-flavoured shell written in MicroPython, working on the board's own filesystem.
#
#   cmd arg "quoted arg" 'literal' $VAR ${VAR} $? ~ *.md ?x [a-c]   words, variables, ~, globs
#   a | b            pipes (each stage's output is the next one's input)
#   a ; b   a && b   a || b                                          lists
#   > file  >> file  < file  2> file  2>&1                           redirections
#   X=1  export X=1  cd  pwd  ...                                    environment and working directory
#   # comment
#
# The commands (ls, cat, grep, find, cp, wget, jade ask ...) are in jade.shellcmds. Not here: job control (&),
# subshells, loops and functions; use `py` or `run script.py` for those.
import sys
from jade import fs

MAX_OUT = 64 * 1024     # a captured result stops at this size (the API and the agent get a note)


class Buf:
    # collects output; stops growing at `limit`
    def __init__(self, limit=MAX_OUT):
        self.parts = []
        self.n = 0
        self.limit = limit
        self.cut = False

    def write(self, s):
        if self.cut:
            return
        if self.n + len(s) > self.limit:
            s = s[:max(0, self.limit - self.n)]
            self.cut = True
        self.parts.append(s)
        self.n += len(s)

    def value(self):
        v = "".join(self.parts)
        return v + "\n[output cut at %d bytes]" % self.limit if self.cut else v


class Console:
    # straight to the serial console
    def write(self, s):
        sys.stdout.write(s)


class _Null:
    def write(self, s):
        pass


NULL = _Null()


class ShellError(Exception):
    pass


# ---------------------------------------------------------------- parsing
OPS = ("&&", "||", ">>", "2>>", "2>&1", "2>", "|", ";", ">", "<", "&")


def tokenize(line):
    # → [("w", parts, glob), ("op", "|"), ...]
    # parts: a list of literal strings and ("$", name, quoted) variables, expanded just before the command runs
    # (so `X=5; echo $X` works). glob = True when the word has an unquoted * ? [
    toks = []
    i = 0
    n = len(line)
    parts = []
    glob = False

    def flush():
        nonlocal parts, glob
        if parts:
            toks.append(("w", parts, glob))
        parts, glob = [], False

    while i < n:
        c = line[i]
        if c in " \t\r\n":
            flush()
            i += 1
            continue
        if c == "#" and not parts:
            break
        if c == "'":
            j = line.find("'", i + 1)
            if j < 0:
                raise ShellError("unterminated '")
            parts.append(line[i + 1:j])
            i = j + 1
            continue
        if c == '"':
            i += 1
            buf = ""
            while True:
                if i >= n:
                    raise ShellError('unterminated "')
                d = line[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\" and i + 1 < n and line[i + 1] in '"\\$`':
                    buf += line[i + 1]
                    i += 2
                    continue
                if d == "$":
                    name, i2 = _varname(line, i)
                    if name is None:
                        buf += "$"
                    else:
                        parts.append(buf)
                        buf = ""
                        parts.append(("$", name, True))
                    i = i2
                    continue
                buf += d
                i += 1
            parts.append(buf)
            continue
        if c == "\\":
            if i + 1 < n:
                parts.append(line[i + 1])
                i += 2
            else:
                i += 1
            continue
        if c == "$":
            name, i2 = _varname(line, i)
            if name is None:
                parts.append("$")
            else:
                parts.append(("$", name, False))
            i = i2
            continue
        if c == "~" and not parts and (i + 1 >= n or line[i + 1] in "/ \t"):
            parts.append(("$", "HOME", True))
            i += 1
            continue
        op = None
        if c in "|;&<>" or (c == "2" and not parts and line[i:i + 2] == "2>"):
            for o in OPS:
                if line.startswith(o, i):
                    op = o
                    break
        if op:
            flush()
            toks.append(("op", op))
            i += len(op)
            continue
        if c in "*?[":
            glob = True
        parts.append(c)
        i += 1
    flush()
    return toks


def _varname(line, i):
    # line[i] == "$" → (name, next index), or (None, i + 1) for a lone $
    n = len(line)
    if i + 1 >= n:
        return None, i + 1
    c = line[i + 1]
    if c == "{":
        j = line.find("}", i + 2)
        if j < 0:
            raise ShellError("bad ${")
        return line[i + 2:j], j + 1
    if c == "?":
        return "?", i + 2
    j = i + 1
    while j < n and (line[j].isalpha() or line[j] == "_" or (j > i + 1 and line[j].isdigit())):
        j += 1
    if j == i + 1:
        return None, i + 1
    return line[i + 1:j], j


def expand_word(parts, env, status):
    # → (text, vanishes). An unquoted $VAR that is unset (or empty) on its own makes no argument at all.
    out = ""
    have = False
    for p in parts:
        if isinstance(p, str):
            out += p
            have = True
        else:
            v = str(status) if p[1] == "?" else env.get(p[1], "")
            out += v
            if p[2] or v:
                have = True
    return out, not have


def parse(toks):
    # → [(sep, [cmd, ...])] where sep is ";", "&&" or "||" (how it joins the previous one) and
    # cmd = {"argv": [(text, glob)], "redir": [(op, target)]}
    seq = []
    sep = ";"
    pipe = []
    cmd = {"argv": [], "redir": []}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t[0] == "w":
            cmd["argv"].append((t[1], t[2]))
        else:
            op = t[1]
            if op in (">", ">>", "<", "2>", "2>>"):
                if i + 1 >= len(toks) or toks[i + 1][0] != "w":
                    raise ShellError("missing file after " + op)
                cmd["redir"].append((op, toks[i + 1][1]))
                i += 1
            elif op == "2>&1":
                cmd["redir"].append((op, ""))
            elif op == "|":
                if not cmd["argv"]:
                    raise ShellError("empty command before |")
                pipe.append(cmd)
                cmd = {"argv": [], "redir": []}
            elif op in (";", "&&", "||"):
                if cmd["argv"] or pipe:
                    if not cmd["argv"]:
                        raise ShellError("empty command after |")
                    pipe.append(cmd)
                    seq.append((sep, pipe))
                elif op != ";":
                    raise ShellError("nothing before " + op)
                pipe = []
                cmd = {"argv": [], "redir": []}
                sep = op
            elif op == "&":
                raise ShellError("background jobs (&) are not supported")
        i += 1
    if cmd["argv"] or pipe:
        if not cmd["argv"]:
            raise ShellError("empty command after |")
        pipe.append(cmd)
        seq.append((sep, pipe))
    elif sep != ";":
        raise ShellError("nothing after " + sep)
    return seq


# ---------------------------------------------------------------- globbing
def fnmatch(name, pat):
    return _fm(name, 0, pat, 0)


def _fm(s, si, p, pi):
    while pi < len(p):
        c = p[pi]
        if c == "*":
            while pi < len(p) and p[pi] == "*":
                pi += 1
            if pi == len(p):
                return True
            for k in range(si, len(s) + 1):
                if _fm(s, k, p, pi):
                    return True
            return False
        if si >= len(s):
            return False
        if c == "?":
            si += 1
            pi += 1
        elif c == "[":
            j = p.find("]", pi + 2)
            if j < 0:
                if s[si] != "[":
                    return False
                si += 1
                pi += 1
                continue
            body = p[pi + 1:j]
            neg = body[:1] in ("!", "^")
            if neg:
                body = body[1:]
            hit = False
            k = 0
            while k < len(body):
                if k + 2 < len(body) and body[k + 1] == "-":
                    if body[k] <= s[si] <= body[k + 2]:
                        hit = True
                    k += 3
                else:
                    if body[k] == s[si]:
                        hit = True
                    k += 1
            if hit == neg:
                return False
            si += 1
            pi = j + 1
        else:
            if c == "\\" and pi + 1 < len(p):
                pi += 1
                c = p[pi]
            if s[si] != c:
                return False
            si += 1
            pi += 1
    return si == len(s)


def glob_expand(pattern, cwd):
    # a pattern like "notes/*.md" → sorted matching paths (relative if the pattern was relative); no match → []
    absolute = pattern.startswith("/")
    parts = [p for p in pattern.split("/") if p != ""]
    cands = ["/" if absolute else ""]
    for idx, part in enumerate(parts):
        last = idx == len(parts) - 1
        nxt = []
        has = any(ch in part for ch in "*?[")
        for base in cands:
            real = fs.norm(base or ".", cwd) if base != "/" else "/"
            if not has:
                p = (base.rstrip("/") + "/" + part) if base else part
                if fs.exists(fs.norm(p, cwd)):
                    nxt.append(p)
                continue
            try:
                names = sorted(e[0] for e in fs.ilistdir(real))
            except OSError:
                continue
            for name in names:
                if name.startswith(".") and not part.startswith("."):
                    continue
                if fnmatch(name, part):
                    p = (base.rstrip("/") + "/" + name) if base else name
                    if last or fs.isdir(fs.norm(p, cwd)):
                        nxt.append(p)
        cands = nxt
        if not cands:
            break
    return cands


# ---------------------------------------------------------------- the shell
class Shell:
    def __init__(self, cwd="/", env=None, allow=None):
        self.cwd = cwd
        self.env = {"HOME": "/", "PATH": "/lib", "USER": "jade", "SHELL": "jadesh", "PWD": cwd}
        if env:
            self.env.update(env)
        self.status = 0
        self.history = []
        self.allow = allow          # None = every command; otherwise a list of command names
        self.cmds = None
        self.stop = False

    def path(self, p):
        return fs.norm(p, self.cwd)

    def _commands(self):
        if self.cmds is None:
            from jade import shellcmds
            self.cmds = shellcmds.CMDS
        return self.cmds

    def prompt(self):
        return "jade:%s$ " % self.cwd

    def expand_args(self, argv):
        out = []
        for parts, glob in argv:
            text, gone = expand_word(parts, self.env, self.status)
            if gone:
                continue
            if glob:
                m = glob_expand(text, self.cwd)
                if m:
                    out.extend(m)
                    continue
            out.append(text)
        return out

    async def run(self, line, out=None, err=None, stdin=None):
        # runs one input line → exit status; output goes to `out` (a Buf by default)
        if out is None:
            out = Buf()
        if err is None:
            err = out
        hide = line.startswith(" ")
        line = line.strip()
        if not line:
            return self.status
        if not hide:
            self.history.append(line)
            if len(self.history) > 50:
                del self.history[0]
        try:
            seq = parse(tokenize(line))
        except ShellError as e:
            err.write("jadesh: %s\n" % e)
            self.status = 2
            return 2
        status = self.status
        for sep, pipe in seq:
            if (sep == "&&" and status != 0) or (sep == "||" and status == 0):
                continue
            status = await self._pipeline(pipe, out, err, stdin)
            self.status = status
            if self.stop:
                break
        return status

    async def _pipeline(self, pipe, out, err, stdin):
        data = stdin
        n = len(pipe)
        status = 0
        for k, cmd in enumerate(pipe):
            last = k == n - 1
            sink = out if last else Buf()
            status = await self._command(cmd, sink, err, data)
            if not last:
                data = sink.value()
        return status

    async def _command(self, cmd, out, err, stdin):
        argv = self.expand_args(cmd["argv"])
        if not argv:
            return 0
        # NAME=value prefix assignments (alone: set shell variables)
        env_set = {}
        while argv and "=" in argv[0] and _ident(argv[0].split("=", 1)[0]):
            k, v = argv.pop(0).split("=", 1)
            env_set[k] = v
        if not argv:
            self.env.update(env_set)
            return 0
        # redirections
        sink, esink = out, err
        files = []
        try:
            for op, target in cmd["redir"]:
                if op == "2>&1":
                    esink = sink
                    continue
                target = expand_word(target, self.env, self.status)[0]
                t = self.path(target)
                if op == "<":
                    if t == "/dev/null":
                        stdin = ""
                    else:
                        with fs.open(t) as f:
                            stdin = f.read()
                else:
                    fh = NULL if t == "/dev/null" else fs.open(t, "a" if op.endswith(">>") else "w")
                    if fh is not NULL:
                        files.append(fh)
                    if op.startswith("2"):
                        esink = fh
                    else:
                        sink = fh
        except OSError as e:
            for fh in files:
                fh.close()
            err.write("jadesh: %s: %s\n" % (target, _why(e)))
            return 1
        name = argv[0]
        saved = None
        if env_set:
            saved = dict(self.env)
            self.env.update(env_set)
        try:
            if self.allow is not None and name not in self.allow:
                esink.write("jadesh: %s: not allowed here\n" % name)
                return 126
            fn = self._commands().get(name)
            if fn is None:
                esink.write("jadesh: %s: command not found\n" % name)
                return 127
            try:
                r = fn(self, argv[1:], stdin, sink, esink)
                if hasattr(r, "send"):
                    r = await r
                return 0 if r is None else int(r)
            except KeyboardInterrupt:
                raise
            except OSError as e:
                esink.write("%s: %s\n" % (name, _why(e)))
                return 1
            except ShellError as e:
                esink.write("%s: %s\n" % (name, e))
                return 2
            except Exception as e:
                esink.write("%s: %s: %s\n" % (name, type(e).__name__, e))
                return 1
        finally:
            if saved is not None:
                self.env = saved
            for fh in files:
                fh.close()


def _ident(name):
    # a valid variable name: letters, digits and _, not starting with a digit
    if not name or name[0].isdigit():
        return False
    for c in name:
        if not (c.isalpha() or c.isdigit() or c == "_"):
            return False
    return True


def _why(e):
    n = e.args[0] if e.args else 0
    return {2: "No such file or directory", 13: "Permission denied", 17: "File exists", 20: "Not a directory",
            21: "Is a directory", 22: "Invalid argument", 28: "No space left on device", 39: "Directory not empty",
            30: "Read-only file system"}.get(n, str(e))


async def run(line, shell=None):
    # one line, captured: → (status, output). The API and the agent's run_shell use this.
    sh = shell or Shell()
    b = Buf()
    st = await sh.run(line, b)
    return st, b.value()


def run_sync(line, shell):
    # from the Python REPL (no event loop running): runs a line and prints its output
    import asyncio
    return asyncio.run(shell.run(line, Console()))


def interact(shell=None):
    # a blocking prompt for the Python REPL:  >>> import jade.shell; jade.shell.interact()
    sh = shell or Shell()
    while not sh.stop:
        try:
            line = input(sh.prompt())
        except (EOFError, KeyboardInterrupt):
            print()
            break
        try:
            run_sync(line, sh)
        except KeyboardInterrupt:
            print("^C")
