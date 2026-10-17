# The vault indexer and Markdown scanner, in plain MicroPython.
#
# - search(root, query): ranked full-text search over the .md files under root (streams each file line by line,
#   so memory stays flat however big the vault is);
# - scan(path): a note's structure (headings, links, tags, tasks, frontmatter);
# - strip(text): Markdown → plain text for a model prompt.
#
# The rules and weights are the ones the old C indexer used, so results are the same.
from jade import fs

HEADING, LINK, TAG, TASK, META = 1, 2, 4, 8, 16
ALL = 31
LINE_MAX = 512          # longer lines are read in chunks
MAX_TERMS = 8
TERM_MAX = 32
MAX_DEPTH = 8


def _word(c):
    return ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9") or c == "_" or c == "-" or c >= "\x80"


def _space(c):
    return c == " " or c == "\t"


def _lc(s):
    return s.lower()   # MicroPython folds ASCII only, which is what the ranking wants


def terms(query):
    out = []
    q = query
    n = len(q)
    i = 0
    while i < n and len(out) < MAX_TERMS:
        while i < n and not _word(q[i]) and q[i] != "#":
            i += 1
        if i >= n:
            break
        t = ""
        tag = False
        if q[i] == "#":
            tag = True
            t = "#"
            i += 1
        while i < n and (_word(q[i]) or (tag and q[i] == "/")):
            if len(t) < TERM_MAX:
                t += q[i]
            i += 1
        t = _lc(t)
        if len(t) < 2:
            continue
        if t not in out:
            out.append(t)
    return out


def _count(low, term, cap):
    # occurrences of term in a lower-cased line (at most cap); "#tag" must end at a word boundary
    n = 0
    tl = len(term)
    p = 0
    while n < cap:
        p = low.find(term, p)
        if p < 0:
            break
        if term[0] == "#":
            after = low[p + tl] if p + tl < len(low) else ""
            before = low[p - 1] if p > 0 else " "
            if not (after and (_word(after) or after == "/")) and not _word(before):
                n += 1
        else:
            n += 1
        p += tl
    return n


def _tag_word(low, tag):
    # whole-word match of a bare tag in a frontmatter "tags:" value
    tl = len(tag)
    p = 0
    while True:
        p = low.find(tag, p)
        if p < 0:
            return False
        before = low[p - 1] if p > 0 else " "
        after = low[p + tl] if p + tl < len(low) else ""
        if not _word(before) and before != "/" and not (after and (_word(after) or after == "/")):
            return True
        p += tl


def line_text(s):
    # the visible text of a line: leading Markdown markers and spaces removed
    n = len(s)
    i = 0
    while i < n and _space(s[i]):
        i += 1
    while i < n and s[i] in "#>":
        i += 1
    while i < n and _space(s[i]):
        i += 1
    if i + 1 < n and s[i] in "-*+" and _space(s[i + 1]):
        i += 2
    if i + 3 <= n and s[i] == "[" and s[i + 1] in " xX" and s[i + 2] == "]":
        i += 3
    while i < n and _space(s[i]):
        i += 1
    e = n
    while e > i and _space(s[e - 1]):
        e -= 1
    return s[i:e]


class _Lines:
    # lines of a file without their newline; a line longer than LINE_MAX comes in chunks (cont=True after the first)
    def __init__(self, path):
        self.f = fs.open(path)
        self.n = 0
        self.cont = False
        self.partial = False
        self.bytes = 0

    def next(self):
        self.cont = self.partial
        s = self.f.readline(LINE_MAX)
        if not s:
            return None
        self.bytes += len(s)
        self.partial = not s.endswith("\n") and len(s) >= LINE_MAX
        if not self.cont:
            self.n += 1
        s = s.rstrip("\n")
        if s.endswith("\r"):
            s = s[:-1]
        return s

    def close(self):
        self.f.close()


def _ends_md(name):
    n = name.lower()
    return n.endswith(".md") or n.endswith(".markdown")


def _search_file(path, ts, snip_len):
    # → (score, best_line, snippet) or None
    try:
        r = _Lines(path)
    except OSError:
        return None
    base = path.rsplit("/", 1)[-1]
    name = _lc(base)
    d = name.rfind(".")
    if d >= 0:
        name = name[:d]
    score = 0
    mask = 0
    nt = len(ts)
    for t in range(nt):
        if ts[t][0] != "#" and ts[t] in name:   # tags never match names
            score += 12
            mask |= 1 << t
    best = 0
    best_line = 0
    snip = ""
    in_meta = False
    try:
        while True:
            line = r.next()
            if line is None:
                break
            if r.n == 1 and not r.cont and line == "---":
                in_meta = True
                continue
            if in_meta and not r.cont and line == "---":
                in_meta = False
                continue
            low = _lc(line)
            heading = (not r.cont) and line[:1] == "#" and line[1:2] in (" ", "#")
            ls = 0
            distinct = 0
            for t in range(nt):
                c = _count(low, ts[t], 3)
                if not c and ts[t][0] == "#" and in_meta and low.startswith("tags:"):
                    c = 1 if _tag_word(low[5:], ts[t][1:]) else 0   # tags: [a, b] in frontmatter
                if c:
                    ls += c * (4 if heading else 1)
                    distinct += 1
                    mask |= 1 << t
            if not ls:
                continue
            ls += (distinct - 1) * 3
            if ls > 24:
                ls = 24
            score += ls
            if ls > best:
                best = ls
                best_line = r.n
                snip = strip(line_text(line))[:snip_len]
    finally:
        r.close()
    if not mask:
        return None
    coverage = 0
    for t in range(nt):
        coverage += (mask >> t) & 1
    score += coverage * 8 + (10 if coverage == nt else 0)
    return score, best_line, snip


def _walk(root):
    # .md files under root, depth first; names starting with "." are skipped
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(fs.ilistdir(d))
        except OSError:
            continue
        dirs = []
        for e in entries:
            name, kind = e[0], e[1]
            if name.startswith("."):
                continue
            full = d.rstrip("/") + "/" + name
            if kind == 0:   # some file systems do not say
                try:
                    kind = fs.stat(full)[0] & 0xF000
                except OSError:
                    continue
            if kind & 0x4000:
                if depth + 1 < MAX_DEPTH:
                    dirs.append((full, depth + 1))
            elif kind & 0x8000 and _ends_md(name):
                yield full
        stack.extend(reversed(dirs))


def _better(score, path, o):
    return score > o[1] or (score == o[1] and path < o[0])


def search_iter(root, query, k=8, snip=160):
    # a generator that yields None between files (so a caller can give the CPU back) and finally returns
    # the hits through StopIteration.value: [(path, score, line, snippet)], best first
    ts = terms(query)
    hits = []
    if not ts or k <= 0:
        return hits
    root = root.rstrip("/") or "/"
    if not fs.isdir(root):
        raise OSError(2)
    since = 0
    for path in _walk(root):
        res = _search_file(path, ts, snip)
        if res:
            score, line, sn = res
            hit = (path, score, line, sn)
            pos = len(hits)
            while pos > 0 and _better(score, path, hits[pos - 1]):
                pos -= 1
            if pos < k:
                hits.insert(pos, hit)
                if len(hits) > k:
                    hits.pop()
        since += 1
        if since >= 4:
            since = 0
            yield None
    return hits


def search(root, query, k=8, snip=160):
    g = search_iter(root, query, k, snip)
    try:
        while True:
            next(g)
    except StopIteration as e:
        return e.value


async def asearch(root, query, k=8, snip=160):
    import asyncio
    g = search_iter(root, query, k, snip)
    try:
        while True:
            next(g)
            await asyncio.sleep_ms(0)
    except StopIteration as e:
        return e.value


# ---------------------------------------------------------------- structure scan
def _inline(l, kinds, out, lineno):
    n = len(l)
    i = 0
    while i < n:
        c = l[i]
        if c == "`":   # skip inline code
            j = i + 1
            while j < n and l[j] != "`":
                j += 1
            i = j + 1
            continue
        if kinds & LINK and c == "[" and i + 1 < n and l[i + 1] == "[":
            j = i + 2
            bar = -1
            while j + 1 < n and not (l[j] == "]" and l[j + 1] == "]"):
                if l[j] == "|" and bar < 0:
                    bar = j
                j += 1
            if j + 1 < n:
                ts = i + 2
                te = bar if bar >= 0 else j
                h = ts
                while h < te and l[h] != "#":   # drop #heading
                    h += 1
                if h > ts:
                    out.append((LINK, l[ts:h], l[bar + 1:j] if bar >= 0 else "", 1 if i > 0 and l[i - 1] == "!" else 0, lineno))
                i = j + 2
                continue
        if kinds & LINK and c == "[" and (i == 0 or l[i - 1] != "!"):   # [text](target.md)
            j = i + 1
            while j < n and l[j] != "]":
                j += 1
            if j + 1 < n and l[j + 1] == "(":
                k = j + 2
                while k < n and l[k] != ")" and l[k] != " ":
                    k += 1
                t = l[j + 2:k]
                if len(t) > 3 and t[-3:].lower() == ".md" and ":" not in t[:12]:
                    out.append((LINK, t, l[i + 1:j], 0, lineno))
                if k < n:
                    i = k + 1
                    continue
        if kinds & TAG and c == "#" and (i == 0 or _space(l[i - 1]) or l[i - 1] in "(,") and i + 1 < n and ("a" <= l[i + 1] <= "z" or "A" <= l[i + 1] <= "Z"):
            j = i + 1
            while j < n and (_word(l[j]) or l[j] == "/"):
                j += 1
            e = j
            while e > i + 1 and l[e - 1] in "/-":
                e -= 1
            out.append((TAG, l[i + 1:e], "", 0, lineno))
            i = j
            continue
        i += 1


def scan(path, kinds=ALL):
    # → [(kind, a, b, n, line)]; raises OSError if the file can't be opened
    r = _Lines(path)
    out = []
    in_meta = False
    in_fence = False
    try:
        while True:
            l = r.next()
            if l is None:
                break
            ln = r.n
            if r.cont:
                if not in_fence and not in_meta:
                    _inline(l, kinds, out, ln)
                continue
            if ln == 1 and l == "---":
                in_meta = True
                continue
            if in_meta:
                if l == "---" or l == "...":
                    in_meta = False
                    continue
                if kinds & META:
                    c = l.find(":")
                    if c > 0 and not _space(l[0]):
                        out.append((META, l[:c].rstrip(" \t"), l[c + 1:].lstrip(" \t"), 0, ln))
                continue
            s = 0
            while s < len(l) and _space(l[s]):
                s += 1
            if l[s:s + 3] in ("```", "~~~"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if l[:1] == "#":
                lvl = 0
                while lvl < len(l) and l[lvl] == "#":
                    lvl += 1
                if lvl <= 6 and lvl < len(l) and _space(l[lvl]):
                    if kinds & HEADING:
                        out.append((HEADING, line_text(l), "", lvl, ln))
                    _inline(l[lvl:], kinds & ~TAG, out, ln)   # '#' here is not a tag
                    continue
            if kinds & TASK and s < len(l) and l[s] in "-*+" and l[s + 1:s + 3] == " [" and l[s + 3:s + 4] in (" ", "x", "X") and l[s + 4:s + 5] == "]":
                out.append((TASK, line_text(l), "", 0 if l[s + 3] == " " else 1, ln))
            _inline(l, kinds, out, ln)
    finally:
        r.close()
    return out


# ---------------------------------------------------------------- strip to plain text
def strip(text):
    lines = text.split("\n")
    out = []
    blank = False
    in_fence = False
    in_meta = False
    first = True
    for raw in lines:
        l = raw[:-1] if raw.endswith("\r") else raw
        s = l.lstrip(" \t")
        is_fence = len(s) >= 3 and s[:3] in ("```", "~~~")
        meta_edge = l == "---" and (first or in_meta)
        first = False
        if meta_edge:
            in_meta = not in_meta
            continue
        if is_fence:
            in_fence = not in_fence
            continue
        if in_fence:   # code is kept as it is
            o = l
        else:
            n = len(l)
            k = 0
            while k < n and _space(l[k]):   # indentation of list items is kept; heading and quote markers go
                k += 1
            o = l[:k]
            if k < n and l[k] == "#":
                h = k
                while h < n and l[h] == "#":
                    h += 1
                if h < n and _space(l[h]):
                    k = h + 1
            while k < n and l[k] == ">":
                k += 1
                if k < n and _space(l[k]):
                    k += 1
            buf = []
            while k < n:
                c = l[k]
                if c == "*" or c == "`":
                    k += 1
                    continue
                if c == "!" and k + 1 < n and l[k + 1] == "[" and not (k + 2 < n and l[k + 2] == "["):
                    k += 1   # image: keep the alt text via the link rule below
                    continue
                if c == "[" and k + 1 < n and l[k + 1] == "[":   # [[target|alias]]
                    j = k + 2
                    bar = 0
                    while j + 1 < n and not (l[j] == "]" and l[j + 1] == "]"):
                        if l[j] == "|" and not bar:
                            bar = j
                        j += 1
                    if j + 1 < n:
                        a = bar + 1 if bar else k + 2
                        b = j
                        if not bar:
                            h = a
                            while h < b and l[h] != "#":
                                h += 1
                            b = h
                        buf.append(l[a:b])
                        k = j + 2
                        continue
                if c == "[":   # [text](url)
                    j = k + 1
                    while j < n and l[j] != "]":
                        j += 1
                    if j + 1 < n and l[j + 1] == "(":
                        u = j + 2
                        while u < n and l[u] != ")":
                            u += 1
                        if u < n:
                            buf.append(l[k + 1:j].replace("*", "").replace("`", ""))
                            k = u + 1
                            continue
                if c == "<" and k + 1 < n and (("a" <= l[k + 1] <= "z") or ("A" <= l[k + 1] <= "Z") or l[k + 1] in "/!"):
                    j = k + 1
                    while j < n and l[j] != ">" and l[j] != "<":
                        j += 1
                    if j < n and l[j] == ">":
                        k = j + 1
                        continue
                buf.append(c)
                k += 1
            o += "".join(buf)
        o = o.rstrip(" \t")
        if not o:
            if blank or not out:   # no leading blank lines, no runs of them
                continue
            blank = True
        else:
            blank = False
        out.append(o)
    return "\n".join(out).rstrip(" \t\n")
