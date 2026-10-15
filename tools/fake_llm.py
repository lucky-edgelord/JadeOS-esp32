#!/usr/bin/env python3
"""A scripted OpenAI-compatible chat API for testing Jade without a real model.

    python3 tools/fake_llm.py [port]       POST /v1/chat/completions, GET /_requests, POST /_reset

What it answers depends on the last user message:
    "search my notes for X"   → asks for the search_vault tool, then summarises its output
    "save a note saying X"    → asks for write_note, then confirms
    "run <cmd>"               → asks for run_shell, then reports
    "ratelimit"               → 429 (Retry-After: 1) once, then a normal answer
    "boom"                    → 500 every time
    "loop forever"            → keeps asking for tools (tests max_steps)
    anything else             → "You asked: <prompt> | context: <notes in the system prompt>"
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQUESTS = []
STATE = {"ratelimited": False}
LOCK = threading.Lock()


def call(name, args, n=1):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_%d" % n, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def answer(body):
    msgs = body["messages"]
    last = msgs[-1]
    users = [m for m in msgs if m["role"] == "user"]
    prompt = users[-1]["content"] if users else ""
    if last["role"] == "tool":
        if "loop forever" in prompt and body.get("tools"):
            return call("current_time", {}, len(msgs))
        return {"role": "assistant", "content": "Tool said: " + last["content"][:120]}
    m = re.search(r"search my notes for (.+)", prompt)
    if m:
        return call("search_vault", {"query": m.group(1)})
    m = re.search(r"save a note saying (.+)", prompt)
    if m:
        return call("write_note", {"path": "Inbox/From Jade", "text": "# From Jade\n" + m.group(1) + "\n"})
    m = re.search(r"\brun (.+)", prompt)
    if m:
        return call("run_shell", {"command": m.group(1)})
    if "loop forever" in prompt and body.get("tools"):
        return call("current_time", {})
    system = msgs[0]["content"] if msgs[0]["role"] == "system" else ""
    notes = re.findall(r"^- (\S+\.md) \(line", system, re.M)
    hist = sum(1 for m in msgs if m["role"] == "user") - 1
    return {"role": "assistant", "content": "You asked: %s | context: %s | history: %d" % (prompt, ",".join(notes) or "none", hist)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, headers=None):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if self.path.endswith("chunked"):
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(0, len(data), 50):
                part = data[i:i + 50]
                self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_GET(self):
        if self.path == "/_requests":
            with LOCK:
                self.send(200, REQUESTS)
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        if self.path == "/_reset":
            with LOCK:
                REQUESTS.clear()
                STATE["ratelimited"] = False
            return self.send(200, {"ok": True})
        try:
            body = json.loads(raw)
        except ValueError:
            return self.send(400, {"error": "bad json"})
        with LOCK:
            REQUESTS.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        prompt = [m for m in body["messages"] if m["role"] == "user"][-1]["content"]
        if "boom" in prompt:
            return self.send(500, {"error": {"message": "the fake model exploded"}})
        if "ratelimit" in prompt and not STATE["ratelimited"]:
            STATE["ratelimited"] = True
            return self.send(429, {"error": {"message": "slow down"}}, {"Retry-After": "1"})
        msg = answer(body)
        self.send(200, {"id": "x", "object": "chat.completion", "model": body.get("model"),
                        "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
                        "usage": {"prompt_tokens": len(raw) // 4, "completion_tokens": 10, "total_tokens": len(raw) // 4 + 10}})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("fake LLM on http://127.0.0.1:%d/v1/chat/completions" % port, flush=True)
    srv.serve_forever()
