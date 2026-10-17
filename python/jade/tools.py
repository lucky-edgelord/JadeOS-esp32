# Jade's tools. Each is a plain (or async) function with a JSON-schema description, registered with
# @tool. The agent offers them to the model as OpenAI-style function tools; the API, the dashboard and
# the shell (`jade tool <name> {json}`) can run them directly.
import json, time, gc
from jade import vault, config, shell

REGISTRY = {}
MAX_OUT = 3000   # characters of tool output handed back to the model


def tool(name, description, params=None, required=None):
    def deco(fn):
        REGISTRY[name] = {"fn": fn, "name": name, "description": description,
                          "params": params or {}, "required": required or []}
        return fn
    return deco


def specs():
    # the "tools" list for the chat API
    out = []
    for t in REGISTRY.values():
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": {"type": "object", "properties": t["params"], "required": t["required"]}}})
    return out


def listing():
    return [{"name": t["name"], "description": t["description"], "params": t["params"],
             "required": t["required"]} for t in REGISTRY.values()]


class ToolError(Exception):
    pass


def _coerce(t, args):
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    for r in t["required"]:
        if r not in args:
            raise ToolError("missing argument: " + r)
    out = {}
    for k, v in args.items():
        spec = t["params"].get(k)
        if spec is None:
            continue   # models sometimes invent arguments; ignore them
        typ = spec.get("type")
        try:
            if typ == "integer":
                v = int(v)
            elif typ == "boolean":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
            elif typ == "string":
                v = v if isinstance(v, str) else json.dumps(v)
        except (ValueError, TypeError):
            raise ToolError("bad value for " + k)
        out[k] = v
    return out


async def run(name, args):
    # → (ok, text)
    t = REGISTRY.get(name)
    if not t:
        return False, "unknown tool: " + name
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return False, "arguments are not valid JSON"
    try:
        res = t["fn"](**_coerce(t, args))
        if hasattr(res, "send"):   # a coroutine
            res = await res
    except (ToolError, vault.VaultError) as e:
        return False, str(e)
    except Exception as e:   # a tool must never take the agent down
        return False, "%s: %s" % (type(e).__name__, e)
    if not isinstance(res, str):
        res = json.dumps(res)
    if len(res) > MAX_OUT:
        res = res[:MAX_OUT] + "\n[…%d more characters]" % (len(res) - MAX_OUT)
    return True, res


# ---------------------------------------------------------------- built-in tools
@tool("search_vault", "Full-text search of the user's Markdown notes. Returns matching notes with the best line.",
      {"query": {"type": "string", "description": "words or #tags to look for"},
       "k": {"type": "integer", "description": "how many notes (default 5)"}}, ["query"])
async def search_vault(query, k=5):
    hits = await vault.asearch(query, min(max(k, 1), 10))
    if not hits:
        return "no notes match " + repr(query)
    return "\n".join("%s (line %d): %s" % (h["path"], h["line"], h["snippet"]) for h in hits)


@tool("read_note", "Read a note as plain text.",
      {"path": {"type": "string", "description": "note path in the vault, e.g. Projects/Plan.md"}}, ["path"])
def read_note(path):
    if not path.lower().endswith(".md") and not vault.exists(path):
        found = vault.resolve_link(path)
        if found:
            path = found
    return vault.plain(path, 6000)


@tool("write_note", "Create or replace a note.",
      {"path": {"type": "string"}, "text": {"type": "string", "description": "the whole note in Markdown"}},
      ["path", "text"])
def write_note(path, text):
    if not path.lower().endswith(".md"):
        path += ".md"
    st = vault.write(path, text)
    return "saved %s (%d bytes)" % (st["path"], st["size"])


@tool("append_note", "Add text to the end of a note (created if missing).",
      {"path": {"type": "string"}, "text": {"type": "string"}}, ["path", "text"])
def append_note(path, text):
    if not path.lower().endswith(".md"):
        path += ".md"
    if not text.startswith("\n") and vault.exists(path):
        text = "\n" + text
    st = vault.write(path, text, append=True)
    return "appended to %s (now %d bytes)" % (st["path"], st["size"])


@tool("list_notes", "List the notes in a folder of the vault.",
      {"folder": {"type": "string", "description": "folder path, empty for the whole vault"}})
def list_notes(folder=""):
    names = vault.files(folder)
    if not names:
        return "no notes"
    more = len(names) - 60
    return "\n".join(names[:60]) + ("\n[…%d more]" % more if more > 0 else "")


@tool("note_outline", "A note's headings, links, tags, tasks and frontmatter, without reading it all.",
      {"path": {"type": "string"}}, ["path"])
def note_outline(path):
    return vault.outline(path)


@tool("remember", "Save a short fact about the user or the world to Jade's memory note for later.",
      {"fact": {"type": "string"}}, ["fact"])
def remember(fact):
    t = time.localtime()
    vault.write("Jade/Memory.md", "- %04d-%02d-%02d %s\n" % (t[0], t[1], t[2], fact.strip()), append=True)
    return "remembered"


@tool("run_shell", "Run one allowed JadeOS shell command on the device (ls, cat, df, free, date, ...).",
      {"command": {"type": "string"}}, ["command"])
async def run_shell(command):
    ok, why = shell_allowed(command)
    if not ok:
        raise ToolError(why)
    st, out = await shell.run(command, shell.Shell(allow=config.load()["shell"]["allow"]))
    return ("exit %d\n" % st if st else "") + (out or "(no output)")


@tool("device_status", "Free memory, storage, uptime, network and model budget of the device.")
def device_status():
    from jade import sysinfo
    return sysinfo.status()


@tool("current_time", "The device's date and time.")
def current_time():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % t[:6]


# ---------------------------------------------------------------- shell whitelist
BAD = (";", "&&", "||", "|", "`", "$(", ">", "<", "\n", "&")


def shell_allowed(command):
    # one allowed command, no chaining, redirection or substitution; rm only inside the vault
    c = command.strip()
    if not c:
        return False, "empty command"
    for b in BAD:
        if b in c:
            return False, "not allowed in a command: " + b
    words = c.split()
    allow = config.load()["shell"]["allow"]
    if words[0] not in allow:
        return False, "command not allowed: %s (allowed: %s)" % (words[0], " ".join(allow))
    if words[0] in ("rm", "mv", "cp"):
        vr = vault.root()
        for w in words[1:]:
            if w.startswith("-"):
                continue
            if not w.startswith("/") or ".." in w or not (w + "/").startswith(vr + "/") or w.rstrip("/") == vr:
                return False, "%s only works on absolute paths inside %s" % (words[0], vr)
    return True, ""
