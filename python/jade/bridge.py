# The Python end of the shell bridge: answers `jade ...` commands typed in the BreezyBox shell.
#
#   jade ask <question>          jade-ask <question>
#   jade search <words>          jade-search <words>
#   jade tool <name> [json]      jade-tool <name> [json]
#   jade py <python>             jade-py <python>      (expression or statements)
#   jade status | history | clear | tools | help
import asyncio, json, jadecore
from jade import vault, tools, sysinfo
from jade.log import log

HELP = """jade ask <question>     ask Jade (searches the vault, may use tools)
jade search <words>     search the vault
jade tool <name> [json] run one of Jade's tools (jade tools lists them)
jade py <code>          run Python on the device
jade status             memory, storage, network, model budget
jade history | clear    show / forget the conversation"""


def _py(code):
    # an expression gives its repr; statements give "ok" (print() output goes to the Python REPL)
    g = {"__name__": "__jade__"}
    try:
        return repr(eval(code, g))
    except SyntaxError:
        exec(code, g)
        return "ok"


async def handle(line):
    # → (status, text)
    cmd, _, rest = line.strip().partition(" ")
    rest = rest.strip()
    if cmd in ("", "help", "-h", "--help"):
        return 0, HELP
    if cmd == "ask":
        if not rest:
            return 2, "usage: jade ask <question>"
        from jade.agent import agent
        res = await agent.think(rest)
        notes = res.get("notes") or []
        tail = ("\n\n(notes: %s)" % ", ".join(notes)) if notes else ""
        return (1 if res.get("error") else 0), res["reply"] + tail
    if cmd == "search":
        hits = vault.search(rest, 8)
        if not hits:
            return 1, "no matches"
        return 0, "\n".join("%-32s %4d  %s" % (h["path"] + ":" + str(h["line"]), h["score"], h["snippet"]) for h in hits)
    if cmd == "tool":
        name, _, args = rest.partition(" ")
        ok, out = await tools.run(name, args or "{}")
        return (0 if ok else 1), out
    if cmd == "tools":
        return 0, "\n".join("%-14s %s" % (t["name"], t["description"]) for t in tools.listing())
    if cmd == "py":
        try:
            return 0, str(_py(rest))
        except Exception as e:
            return 1, "%s: %s" % (type(e).__name__, e)
    if cmd == "status":
        return 0, json.dumps(sysinfo.status())
    if cmd == "history":
        from jade.agent import agent
        return 0, "\n".join("%s: %s" % (m["role"], m["content"]) for m in agent.history) or "(empty)"
    if cmd == "clear":
        from jade.agent import agent
        agent.clear()
        return 0, "forgotten"
    return 2, "unknown jade command: %s\n%s" % (cmd, HELP)


async def _serve_one(rid, line):
    try:
        st, text = await handle(line)
    except Exception as e:
        st, text = 1, "error: %s: %s" % (type(e).__name__, e)
    if not jadecore.bridge_reply(rid, text, st):
        log("bridge: reply to", rid, "came too late")


async def serve(poll_ms=50):
    log("bridge: listening for shell commands")
    while True:
        req = jadecore.bridge_take()
        if req:
            # each request runs on its own, so a slow `ask` doesn't block `status`
            asyncio.create_task(_serve_one(req[0], req[1]))
        else:
            await asyncio.sleep_ms(poll_ms)
