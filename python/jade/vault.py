# The vault: Markdown notes under config "vault" (default /root/vault).
# Every path the API or a tool gives is relative to the vault and is checked so it can't escape it.
import os, jadecore
from jade import config


class VaultError(Exception):
    pass


def root():
    return config.load()["vault"].rstrip("/") or "/root/vault"


def ensure():
    config.makedirs(root())
    if not _listdir(root()):
        write("Welcome.md", WELCOME)


WELCOME = """# Welcome to JadeOS

This vault lives on the ESP32's flash. Jade searches it before she answers.

- Write notes in Markdown, link them with [[Welcome]], tag them with #ideas.
- Ask Jade from the dashboard, the API, or the shell: `jade ask what's in my vault?`
"""


def clean(rel):
    # vault-relative path → (relative, absolute); rejects anything leaving the vault
    rel = (rel or "").replace("\\", "/")
    parts = []
    for p in rel.split("/"):
        if p in ("", "."):
            continue
        if p == ".." or p.startswith("."):
            raise VaultError("bad path: " + rel)
        parts.append(p)
    r = "/".join(parts)
    return r, root() + ("/" + r if r else "")


def _listdir(path):
    try:
        return list(os.ilistdir(path))
    except OSError:
        return []


def _isdir(path):
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


def exists(rel):
    try:
        os.stat(clean(rel)[1])
        return True
    except OSError:
        return False


def tree(rel="", depth=6):
    # [{"name", "path", "dir", "size", "children"?}], folders first
    r, path = clean(rel)
    if not _isdir(path):
        raise VaultError("not a folder: " + rel)
    out = []
    for ent in sorted(_listdir(path), key=lambda e: (e[1] != 0x4000, e[0].lower())):
        name, kind = ent[0], ent[1]
        if name.startswith("."):
            continue
        p = (r + "/" + name) if r else name
        if kind == 0x4000:
            node = {"name": name, "path": p, "dir": True}
            node["children"] = tree(p, depth - 1) if depth > 1 else []
        else:
            try:
                size = os.stat(path + "/" + name)[6]
            except OSError:
                size = 0
            node = {"name": name, "path": p, "dir": False, "size": size}
        out.append(node)
    return out


def files(rel="", ext=".md"):
    # every file path under rel (vault-relative)
    out = []
    stack = [clean(rel)[0]]
    while stack:
        r = stack.pop()
        for ent in _listdir(clean(r)[1]):
            if ent[0].startswith("."):
                continue
            p = (r + "/" + ent[0]) if r else ent[0]
            if ent[1] == 0x4000:
                stack.append(p)
            elif not ext or p.lower().endswith(ext):
                out.append(p)
    out.sort()
    return out


def read(rel, limit=None):
    r, path = clean(rel)
    try:
        with open(path) as f:
            return f.read(limit) if limit else f.read()
    except OSError:
        raise VaultError("no such note: " + rel)


def stat(rel):
    r, path = clean(rel)
    try:
        st = os.stat(path)
    except OSError:
        raise VaultError("no such note: " + rel)
    return {"path": r, "size": st[6], "mtime": st[8], "dir": st[0] & 0x4000 != 0}


def write(rel, text, append=False):
    r, path = clean(rel)
    if not r or _isdir(path):
        raise VaultError("not a note: " + rel)
    parent = path.rsplit("/", 1)[0]
    config.makedirs(parent)
    if append:
        with open(path, "a") as f:
            f.write(text)
    else:
        tmp = parent + "/.jade-tmp"     # write then rename, so a reset never leaves half a note
        with open(tmp, "w") as f:
            f.write(text)
        os.rename(tmp, path)
    return stat(r)


def mkdir(rel):
    r, path = clean(rel)
    config.makedirs(path)
    return stat(r)


def delete(rel):
    r, path = clean(rel)
    if not r:
        raise VaultError("refusing to delete the vault")
    if _isdir(path):
        if _listdir(path):
            raise VaultError("folder not empty: " + rel)
        os.rmdir(path)
    else:
        try:
            os.remove(path)
        except OSError:
            raise VaultError("no such note: " + rel)


def move(src, dst):
    a, pa = clean(src)
    b, pb = clean(dst)
    if not a or not b:
        raise VaultError("bad move")
    try:
        os.stat(pa)
    except OSError:
        raise VaultError("no such note: " + src)
    config.makedirs(pb.rsplit("/", 1)[0])
    os.rename(pa, pb)
    return stat(b)


def search(query, k=8, snip=160):
    base = root() + "/"
    out = []
    for path, score, line, snippet in jadecore.search(root(), query, k, snip):
        out.append({"path": path[len(base):] if path.startswith(base) else path,
                    "score": score, "line": line, "snippet": snippet})
    return out


KINDS = {1: "headings", 2: "links", 4: "tags", 8: "tasks", 16: "meta"}


def outline(rel):
    r, path = clean(rel)
    try:
        items = jadecore.scan(path)
    except OSError:
        raise VaultError("no such note: " + rel)
    out = {"path": r, "headings": [], "links": [], "tags": [], "tasks": [], "meta": {}}
    for kind, a, b, n, line in items:
        if kind == 1:
            out["headings"].append({"text": a, "level": n, "line": line})
        elif kind == 2:
            out["links"].append({"target": a, "alias": b, "embed": bool(n), "line": line})
        elif kind == 4:
            if a not in out["tags"]:
                out["tags"].append(a)
        elif kind == 8:
            out["tasks"].append({"text": a, "done": bool(n), "line": line})
        elif kind == 16:
            out["meta"][a] = b
            if a == "tags":   # frontmatter tags count as tags too
                for t in b.replace("[", " ").replace("]", " ").replace(",", " ").split():
                    t = t.strip("#\"'")
                    if t and t not in out["tags"]:
                        out["tags"].append(t)
    return out


def resolve_link(target):
    # [[Target]] → the note it points to (by path or by file name), or None
    t = target.strip()
    if not t.lower().endswith(".md"):
        t += ".md"
    if exists(t):
        return clean(t)[0]
    name = t.rsplit("/", 1)[-1].lower()
    for p in files():
        if p.rsplit("/", 1)[-1].lower() == name:
            return p
    return None


def backlinks(rel):
    r = clean(rel)[0]
    name = r.rsplit("/", 1)[-1][:-3].lower() if r.lower().endswith(".md") else r.lower()
    out = []
    for p in files():
        if p == r:
            continue
        try:
            items = jadecore.scan(clean(p)[1], 2)
        except OSError:
            continue
        for _, a, b, n, line in items:
            tgt = a.lower()
            if tgt.endswith(".md"):
                tgt = tgt[:-3]
            if tgt == name or tgt == r.lower()[:-3]:
                out.append({"path": p, "line": line})
                break
    return out


def plain(rel, limit=2000):
    # a note as plain text for a model prompt
    return jadecore.strip(read(rel, limit))
