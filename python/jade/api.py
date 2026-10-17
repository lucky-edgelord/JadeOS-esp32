# Jade's HTTP API (Microdot). The dashboard is a static page on GitHub Pages, so every answer carries
# CORS headers (including Chrome's Private Network Access header for pages reaching a LAN device),
# and every /api route except /api/ping needs the token: "Authorization: Bearer <token>".
import asyncio, json
from microdot import Microdot, Request, Response
from jade import config, vault, tools, sysinfo, fs
from jade.agent import agent
from jade.log import log, recent

Request.max_content_length = 128 * 1024
Request.max_body_length = 128 * 1024
Request.max_readline = 4 * 1024

app = Microdot()

import microdot.microdot as _md
_print_exception = _md.print_exception
# expected errors (bad paths, missing notes) are answered as JSON; don't dump their tracebacks
_md.print_exception = lambda e: None if isinstance(e, vault.VaultError) else _print_exception(e)
PUBLIC = ("/api/ping",)


def cors_headers(req):
    origins = config.load()["api"]["origins"] or "*"
    origin = req.headers.get("Origin", "") if req else ""
    if origins == "*":
        allow = "*"
    else:
        allowed = [o.strip() for o in origins.split(",")]
        allow = origin if origin in allowed else allowed[0]
    h = {"Access-Control-Allow-Origin": allow,
         "Access-Control-Allow-Headers": "Authorization, Content-Type",
         "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
         "Access-Control-Max-Age": "600",
         "Vary": "Origin"}
    if req and req.headers.get("Access-Control-Request-Private-Network"):
        h["Access-Control-Allow-Private-Network"] = "true"
    return h


def _options(req):
    return cors_headers(req)


app.options_handler = _options


def _token_ok(req):
    want = config.load()["api"]["token"]
    got = req.headers.get("Authorization", "")
    if got.startswith("Bearer "):
        got = got[7:]
    else:
        got = req.args.get("token", "")
    # constant-time-ish compare
    if len(got) != len(want):
        return False
    diff = 0
    for a, b in zip(got, want):
        diff |= ord(a) ^ ord(b)
    return diff == 0


@app.before_request
def _auth(req):
    if req.path.startswith("/api/") and req.path not in PUBLIC and not _token_ok(req):
        return {"error": "unauthorized"}, 401


@app.after_request
def _cors(req, res):
    res.headers.update(cors_headers(req))
    return res


@app.after_error_request
def _cors_err(req, res):
    res.headers.update(cors_headers(req))
    return res


def err(msg, status=400):
    return {"error": msg}, status


def _join_surrogates(s):
    # MicroPython's json turns "\\ud83c\\udf31" into two broken characters; join such pairs first
    out = []
    i = 0
    while True:
        j = s.find("\\ud", i)
        if j < 0 or j + 12 > len(s):
            break
        k = j
        while k > 0 and s[k - 1] == "\\":
            k -= 1
        try:
            hi = int(s[j + 2:j + 6], 16)
            lo = int(s[j + 8:j + 12], 16) if s[j + 6:j + 8] == "\\u" else 0
        except ValueError:
            hi = lo = 0
        if (j - k) % 2 == 0 and 0xD800 <= hi < 0xDC00 and 0xDC00 <= lo < 0xE000:
            out.append(s[i:j])
            out.append(chr(0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00)))
            i = j + 12
        else:
            out.append(s[i:j + 3])
            i = j + 3
    out.append(s[i:])
    return "".join(out)


def body(req):
    try:
        raw = req.body.decode() if req.body else ""
        if "\\ud" in raw:
            raw = _join_surrogates(raw)
        b = json.loads(raw) if raw else {}
        return b if isinstance(b, dict) else {}
    except (ValueError, UnicodeError):
        return {}


@app.errorhandler(vault.VaultError)
def _vault_error(req, e):
    return err(str(e), 404 if "no such" in str(e) else 400)


@app.errorhandler(404)
def _nf(req):
    return err("not found", 404)


@app.errorhandler(Exception)
def _boom(req, e):
    log("api: %s %s failed: %s: %s" % (req.method, req.path, type(e).__name__, e))
    return err("%s: %s" % (type(e).__name__, e), 500)


# ---------------------------------------------------------------- system
@app.get("/api/ping")
def ping(req):
    return {"name": "JadeOS", "version": __import__("jade").VERSION, "hostname": config.load()["hostname"]}


@app.get("/api/system/status")
def status(req):
    return sysinfo.status()


@app.get("/api/system/config")
def get_config(req):
    return config.public()


@app.put("/api/system/config")
def put_config(req):
    b = body(req)
    if not isinstance(b, dict):
        return err("expected an object")
    return config.update(b)


@app.get("/api/system/logs")
def logs(req):
    return {"lines": recent(int(req.args.get("n", 50)))}


@app.post("/api/system/shell")
async def shell(req):
    from jade import shell as jsh
    cmd = body(req).get("cmd", "")
    ok, why = tools.shell_allowed(cmd)
    if not ok:
        return err(why, 403)
    st, out = await jsh.run(cmd, jsh.Shell(allow=config.load()["shell"]["allow"]))
    log("api: shell", cmd, "->", st)
    return {"cmd": cmd, "status": st, "output": out}


@app.post("/api/system/reboot")
async def reboot(req):
    async def later():
        await asyncio.sleep(1)
        import machine
        machine.reset()
    asyncio.create_task(later())
    return {"rebooting": True}


# ---------------------------------------------------------------- vault
@app.get("/api/vault/tree")
def tree(req):
    return {"root": vault.root(), "tree": vault.tree(req.args.get("path", ""))}


@app.get("/api/vault/note")
def get_note(req):
    path = req.args.get("path", "")
    info = vault.stat(path)
    if info["dir"]:
        return err("that is a folder")
    info["text"] = vault.read(path)
    if req.args.get("outline"):
        info["outline"] = vault.outline(path)
        info["backlinks"] = vault.backlinks(path)
    return info


@app.put("/api/vault/note")
def put_note(req):
    b = body(req)
    if "path" not in b or "text" not in b:
        return err("need path and text")
    return vault.write(b["path"], b["text"], append=bool(b.get("append")))


@app.delete("/api/vault/note")
def del_note(req):
    vault.delete(req.args.get("path", ""))
    return {"deleted": req.args.get("path", "")}


@app.post("/api/vault/mkdir")
def mkdir(req):
    return vault.mkdir(body(req).get("path", ""))


@app.post("/api/vault/move")
def move(req):
    b = body(req)
    return vault.move(b.get("from", ""), b.get("to", ""))


@app.get("/api/vault/search")
async def search(req):
    q = req.args.get("q", "")
    if not q.strip():
        return err("need q")
    k = min(max(int(req.args.get("k", 8)), 1), 50)
    return {"query": q, "hits": await vault.asearch(q, k)}


@app.get("/api/vault/outline")
def outline(req):
    path = req.args.get("path", "")
    o = vault.outline(path)
    o["backlinks"] = vault.backlinks(path)
    return o


@app.get("/api/vault/resolve")
def resolve(req):
    return {"target": req.args.get("target", ""), "path": vault.resolve_link(req.args.get("target", ""))}


# ---------------------------------------------------------------- agent & tools
@app.post("/api/agent/think")
async def think(req):
    b = body(req)
    prompt = (b.get("prompt") or "").strip()
    if not prompt:
        return err("need prompt")
    use_history = b.get("history", True) is not False
    if not req.args.get("stream"):
        return await agent.think(prompt, use_history=use_history)

    # streamed: one JSON object per line as each step happens (application/x-ndjson)
    events = []
    flag = asyncio.Event()

    def on_event(e):
        events.append(e)
        flag.set()

    async def run():
        res = await agent.think(prompt, on_event, use_history)
        res["type"] = "done"
        events.append(res)
        flag.set()

    asyncio.create_task(run())

    class Stream:   # MicroPython has no async generators: an async iterator class instead
        done = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.done:
                raise StopAsyncIteration
            while not events:
                await flag.wait()
                flag.clear()
            e = events.pop(0)
            if e.get("type") == "done":
                self.done = True
            return (json.dumps(e) + "\n").encode()

    return Response(Stream(), headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})


@app.get("/api/agent/history")
def history(req):
    return {"history": agent.history}


@app.delete("/api/agent/history")
def clear_history(req):
    agent.clear()
    return {"cleared": True}


@app.get("/api/tools")
def list_tools(req):
    return {"tools": tools.listing()}


@app.post("/api/tools/run")
async def run_tool(req):
    b = body(req)
    ok, out = await tools.run(b.get("name", ""), b.get("args") or {})
    return {"ok": ok, "output": out}


# ---------------------------------------------------------------- the Deck (client page), from flash
# /www/index.html.gz (or plain /www/index.html) is served at / ; it is public, the API is what needs the token.
def _deck(req):
    gz = fs.real("/www/index.html.gz")
    if fs.isfile("/www/index.html.gz"):
        return Response.send_file(gz, compressed=True, content_type="text/html", max_age=3600)
    if fs.isfile("/www/index.html"):
        return Response.send_file(fs.real("/www/index.html"), content_type="text/html", max_age=3600)
    return Response("<h1>JadeOS</h1><p>No Deck on this device yet. Open the Deck from GitHub Pages and point it here, "
                    "or copy <code>index.html.gz</code> to <code>/www</code>.</p>", 404,
                    {"Content-Type": "text/html"})


@app.get("/")
def deck_root(req):
    return _deck(req)


@app.get("/deck")
def deck_alias(req):
    return _deck(req)


async def serve(port=None):
    port = port or config.load()["api"]["port"]
    log("api: listening on port", port)
    await app.start_server(host="0.0.0.0", port=port)
