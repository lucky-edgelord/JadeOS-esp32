#!/usr/bin/env python3
"""End-to-end test of Jade's HTTP API, against the unix-port "device" (default) or a real/emulated one.

    python3 tests/test_api.py                          # spawns fake LLM + unix-port device
    python3 tests/test_api.py --url http://127.0.0.1:18081 --token T --llm-port 18080 --no-spawn
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MP = os.environ.get("MICROPYTHON_JADE", "/home/claude/src/micropython/ports/unix/build-jade/micropython-jade")

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", msg, flush=True)


class Client:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def req(self, method, path, body=None, token=True, headers=None, raw=False, timeout=60):
        h = dict(headers or {})
        if token:
            h["Authorization"] = "Bearer " + (self.token if token is True else token)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                content = resp.read()
                return resp.status, dict(resp.headers), (content if raw else json.loads(content or b"null"))
        except urllib.error.HTTPError as e:
            content = e.read()
            try:
                content = json.loads(content)
            except ValueError:
                pass
            return e.code, dict(e.headers), content

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.req("POST", path, body or {}, **kw)


def wait_up(url, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url + "/api/ping", timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def llm_requests(llm_port):
    with urllib.request.urlopen("http://127.0.0.1:%d/_requests" % llm_port) as r:
        return json.loads(r.read())


def llm_reset(llm_port):
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/_reset" % llm_port, data=b"{}", method="POST")).read()


def run(c, llm_port, host_shell):
    # --- ping, auth, CORS
    st, h, b = c.get("/api/ping", token=False)
    check(st == 200 and b["name"] == "JadeOS", "ping is public: %s %s" % (st, b))
    check(h.get("Access-Control-Allow-Origin") == "*", "CORS on responses: %s" % h)
    st, h, b = c.get("/api/system/status", token=False)
    check(st == 401 and h.get("Access-Control-Allow-Origin") == "*", "no token → 401 with CORS: %s" % st)
    st, _, _ = c.get("/api/system/status", token="wrong-token")
    check(st == 401, "wrong token → 401")
    st, _, b = c.get("/api/system/status?token=" + c.token, token=False)
    check(st == 200, "token in the query string works")
    st, h, b = c.req("OPTIONS", "/api/agent/think", token=False, raw=True,
                     headers={"Origin": "https://lucky-edgelord.github.io", "Access-Control-Request-Method": "POST",
                              "Access-Control-Request-Headers": "authorization,content-type",
                              "Access-Control-Request-Private-Network": "true"})
    check(st == 200 and "Authorization" in h.get("Access-Control-Allow-Headers", "") and
          h.get("Access-Control-Allow-Private-Network") == "true", "preflight: %s %s" % (st, h))

    # --- status / config
    st, _, s = c.get("/api/system/status")
    check(st == 200 and s["heap_free"] > 0 and "cortex" in s and "uptime_s" in s, "status: %s" % s)
    st, _, cfg = c.get("/api/system/config")
    check(cfg["cortex"]["key"] == "set" and cfg["api"]["token"] == "set", "secrets hidden: %s" % cfg["cortex"])
    st, _, cfg = c.req("PUT", "/api/system/config", {"agent": {"context_hits": 3}, "cortex": {"key": "set"}})
    check(st == 200 and cfg["agent"]["context_hits"] == 3 and cfg["cortex"]["key"] == "set", "config update keeps secrets")

    # --- vault
    st, _, t = c.get("/api/vault/tree")
    check(st == 200 and any(n["name"] == "Welcome.md" for n in t["tree"]), "welcome note created: %s" % t)
    garden = "---\ntags: [garden]\n---\n# Garden plan\nPlant **tomatoes** in May, see [[Seeds]].\n- [ ] buy compost #todo\n"
    st, _, b = c.req("PUT", "/api/vault/note", {"path": "Projects/Garden.md", "text": garden})
    check(st == 200 and b["size"] == len(garden.encode()), "write note: %s" % b)
    c.req("PUT", "/api/vault/note", {"path": "Seeds.md", "text": "# Seeds\nTomato and basil seeds. Projects/Garden.md links here.\n"})
    c.req("PUT", "/api/vault/note", {"path": "Ünïcode ✓.md", "text": "Grüße 🌱 tomatoes\n"})
    st, _, n = c.get("/api/vault/note?path=Projects/Garden.md&outline=1")
    check(st == 200 and n["text"] == garden and n["outline"]["tags"] == ["garden", "todo"], "read note + outline: %s" % n.get("outline"))
    check(n["outline"]["links"][0]["target"] == "Seeds" and n["outline"]["tasks"][0]["text"] == "buy compost #todo", "outline links/tasks")
    st, _, o = c.get("/api/vault/outline?path=Seeds.md")
    check(st == 200 and o["backlinks"] == [{"path": "Projects/Garden.md", "line": 5}], "backlinks: %s" % o.get("backlinks"))
    st, _, b = c.get("/api/vault/note?path=" + urllib.request.quote("Ünïcode ✓.md"))
    check(st == 200 and b["text"] == "Grüße 🌱 tomatoes\n", "unicode note: %s" % b)
    st, _, b = c.get("/api/vault/search?q=tomatoes&k=5")
    paths = [h["path"] for h in b["hits"]]
    check(st == 200 and paths[0] == "Projects/Garden.md" and "Ünïcode ✓.md" in paths, "search: %s" % b)
    check(b["hits"][0]["snippet"] == "Plant tomatoes in May, see Seeds.", "plain snippet: %r" % b["hits"][0]["snippet"])
    st, _, b = c.get("/api/vault/search?q=%23todo")
    check([h["path"] for h in b["hits"]] == ["Projects/Garden.md"], "tag search: %s" % b)
    st, _, b = c.get("/api/vault/resolve?target=Seeds")
    check(b["path"] == "Seeds.md", "resolve link")
    for bad in ("../etc/passwd", "/../x", ".hidden.md", "a/../../b.md"):
        st, _, b = c.req("PUT", "/api/vault/note", {"path": bad, "text": "x"})
        check(st == 400, "path escape refused: %s → %s" % (bad, st))
    st, _, b = c.get("/api/vault/note?path=nope.md")
    check(st == 404, "missing note 404")
    st, _, b = c.post("/api/vault/move", {"from": "Seeds.md", "to": "Archive/Seeds.md"})
    check(st == 200 and b["path"] == "Archive/Seeds.md", "move: %s" % b)
    st, _, b = c.post("/api/vault/mkdir", {"path": "Empty"})
    st, _, b = c.req("DELETE", "/api/vault/note?path=Empty")
    check(st == 200, "delete empty folder")
    st, _, b = c.req("DELETE", "/api/vault/note?path=Archive")
    check(st == 400, "won't delete a non-empty folder")
    big = "x" * 60000
    st, _, b = c.req("PUT", "/api/vault/note", {"path": "Big.md", "text": big})
    st2, _, b2 = c.get("/api/vault/note?path=Big.md")
    check(st == 200 and st2 == 200 and len(b2["text"]) == 60000, "60 KB note round trip")
    c.req("DELETE", "/api/vault/note?path=Big.md")

    # --- tools
    st, _, b = c.get("/api/tools")
    names = [t["name"] for t in b["tools"]]
    check({"search_vault", "read_note", "write_note", "run_shell", "remember"} <= set(names), "tools: %s" % names)
    st, _, b = c.post("/api/tools/run", {"name": "read_note", "args": {"path": "Projects/Garden"}})
    check(b["ok"] and "Plant tomatoes in May" in b["output"] and "**" not in b["output"], "read_note tool: %s" % b)
    st, _, b = c.post("/api/tools/run", {"name": "nope", "args": {}})
    check(not b["ok"], "unknown tool")
    st, _, b = c.post("/api/tools/run", {"name": "write_note", "args": {"path": "x.md"}})
    check(not b["ok"] and "missing argument" in b["output"], "missing argument: %s" % b)

    # --- shell whitelist
    st, _, b = c.post("/api/system/shell", {"cmd": "echo hello"})
    check(st == 200 and b["status"] == 0 and "hello" in b["output"], "shell echo: %s" % b)
    for bad in ("reboot", "echo hi; rm -rf /", "cat /root/jade/config.json | wc", "rm /root/jade/config.json", "echo $(id)", "ls > /root/x"):
        st, _, b = c.post("/api/system/shell", {"cmd": bad})
        check(st == 403, "shell refuses %r → %s %s" % (bad, st, b))

    # --- agent
    llm_reset(llm_port)
    st, _, r = c.post("/api/agent/think", {"prompt": "what about tomatoes?"})
    check(st == 200 and r["reply"].startswith("You asked: what about tomatoes?"), "think: %s" % r)
    check("Projects/Garden.md" in r["reply"] and r["notes"][0] == "Projects/Garden.md", "vault context reached the model: %s" % r["reply"])
    req = llm_requests(llm_port)[-1]
    check(req["auth"] == "Bearer test-key" and req["body"]["tools"] and "Plant tomatoes" in req["body"]["messages"][0]["content"], "model request had key, tools, note text")
    st, _, r = c.post("/api/agent/think", {"prompt": "and again"})
    check("history: 1" in r["reply"], "history carried: %s" % r["reply"])
    st, _, r = c.post("/api/agent/think", {"prompt": "fresh", "history": False})
    check("history: 0" in r["reply"], "history off: %s" % r["reply"])
    st, _, r = c.post("/api/agent/think", {"prompt": "search my notes for compost"})
    kinds = [s["type"] for s in r["steps"]]
    check(r["reply"].startswith("Tool said: Projects/Garden.md") and kinds == ["search", "tool", "result", "reply"], "tool loop: %s %s" % (r["reply"], kinds))
    st, _, r = c.post("/api/agent/think", {"prompt": "save a note saying buy more seeds"})
    st2, _, n = c.get("/api/vault/note?path=Inbox/From Jade.md".replace(" ", "%20"))
    check(st2 == 200 and "buy more seeds" in n["text"], "agent wrote a note: %s %s" % (r["reply"], n))
    st, _, r = c.post("/api/agent/think", {"prompt": "run free"})
    check(r["steps"][2]["ok"] if len(r["steps"]) > 2 else False, "agent ran a whitelisted command: %s" % r)
    st, _, r = c.post("/api/agent/think", {"prompt": "run reboot"})
    check(len(r["steps"]) > 2 and not r["steps"][2]["ok"] and "not allowed" in r["steps"][2]["output"], "agent's shell is whitelisted too: %s" % r["steps"])
    st, _, r = c.post("/api/agent/think", {"prompt": "loop forever"})
    tools_used = sum(1 for s in r["steps"] if s["type"] == "tool")
    check(tools_used == 4 and r["reply"].startswith("Tool said"), "max_steps stops the loop: %d %s" % (tools_used, r["reply"]))
    t0 = time.time()
    st, _, r = c.post("/api/agent/think", {"prompt": "ratelimit please"})
    check(r["reply"].startswith("You asked: ratelimit") and time.time() - t0 >= 0.9, "429 retried after Retry-After: %s" % r)
    st, _, r = c.post("/api/agent/think", {"prompt": "boom"})
    check(st == 200 and r.get("error") == "http 500", "model error reported: %s" % r)
    st, h, raw = c.req("POST", "/api/agent/think?stream=1", {"prompt": "search my notes for basil"}, raw=True)
    events = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    check(st == 200 and [e["type"] for e in events] == ["search", "tool", "result", "reply", "done"] and
          "x-ndjson" in h.get("Content-Type", ""), "streamed events: %s" % [e["type"] for e in events])
    st, _, hist = c.get("/api/agent/history")
    check(len(hist["history"]) >= 2 and hist["history"][-1]["role"] == "assistant", "history endpoint")
    st, _, b = c.get("/api/system/status")
    check(b["cortex"]["usage"]["calls"] >= 10 and b["cortex"]["usage"]["prompt"] > 0, "token usage counted: %s" % b["cortex"]["usage"])
    # budget: shrink the daily budget below today's usage
    c.req("PUT", "/api/system/config", {"cortex": {"daily_tokens": 10}})
    n_before = len(llm_requests(llm_port))
    st, _, r = c.post("/api/agent/think", {"prompt": "hello?"})
    check(r.get("error") == "budget" and len(llm_requests(llm_port)) == n_before, "daily budget stops calls: %s" % r)
    c.req("PUT", "/api/system/config", {"cortex": {"daily_tokens": 200000}})
    st, _, b = c.req("DELETE", "/api/agent/history")
    st, _, hist = c.get("/api/agent/history")
    check(hist["history"] == [], "history cleared")

    # --- shell bridge (the unix device plays the shell side through /api/_test/bridge)
    if host_shell:
        st, _, b = c.post("/api/_test/bridge", {"line": "search tomatoes"})
        check(b["status"] == 0 and "Projects/Garden.md:5" in b["reply"], "bridge search: %s" % b)
        st, _, b = c.post("/api/_test/bridge", {"line": "ask what about tomatoes?"})
        check(b["status"] == 0 and b["reply"].startswith("You asked: what about tomatoes?") and "(notes: Projects/Garden.md" in b["reply"], "bridge ask: %s" % b)
        st, _, b = c.post("/api/_test/bridge", {"line": "py 6*7"})
        check(b == {"status": 0, "reply": "42"}, "bridge py: %s" % b)
        st, _, b = c.post("/api/_test/bridge", {"line": "py 1/0"})
        check(b["status"] == 1 and "ZeroDivisionError" in b["reply"], "bridge py error: %s" % b)
        st, _, b = c.post("/api/_test/bridge", {"line": "tool current_time"})
        check(b["status"] == 0 and b["reply"][:2] == "20", "bridge tool: %s" % b)
        st, _, b = c.post("/api/_test/bridge", {"line": "frobnicate"})
        check(b["status"] == 2 and "unknown jade command" in b["reply"], "bridge unknown: %s" % b)
        st, _, b = c.post("/api/_test/bridge", {"line": "status"})
        check(b["status"] == 0 and json.loads(b["reply"])["name"] == "JadeOS", "bridge status")

    # --- logs
    st, _, b = c.get("/api/system/logs?n=200")
    check(st == 200 and any("agent:" in l for l in b["lines"]), "logs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--token", default="test-token")
    ap.add_argument("--llm-port", type=int, default=18080)
    ap.add_argument("--port", type=int, default=18081)
    ap.add_argument("--no-spawn", action="store_true")
    a = ap.parse_args()
    procs = []
    data = None
    try:
        if not a.no_spawn:
            procs.append(subprocess.Popen([sys.executable, os.path.join(ROOT, "tools/fake_llm.py"), str(a.llm_port)], stdout=subprocess.DEVNULL))
            data = tempfile.mkdtemp(prefix="jade-dev-")
            log = open(os.path.join(data, "device.log"), "w")
            procs.append(subprocess.Popen([MP, os.path.join(ROOT, "tests/unix/device.py"), data, str(a.port),
                                           "http://127.0.0.1:%d/v1/chat/completions" % a.llm_port], stdout=log, stderr=subprocess.STDOUT))
            a.url = "http://127.0.0.1:%d" % a.port
        if not wait_up(a.url, 60):
            print("device did not come up")
            if data:
                print(open(os.path.join(data, "device.log")).read())
            return 1
        run(Client(a.url, a.token), a.llm_port, host_shell=not a.no_spawn)
        if data and procs[1].poll() is not None:
            check(False, "device process died")
    finally:
        for p in procs:
            p.terminate()
        if data:
            if failed:
                print(open(os.path.join(data, "device.log")).read()[-4000:])
            shutil.rmtree(data, ignore_errors=True)
    print("api: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
