# The remote cortex: an OpenAI-compatible chat API (Groq by default) with a token budget.
#
# Two limits keep the ESP32 (and the bill) safe:
#   context_tokens — each call's prompt is trimmed to fit (oldest history first, then tool output)
#   daily_tokens   — once the day's usage (as reported by the API) passes it, calls are refused
# Token counts before a call are estimates (about 4 characters per token); usage after a call is
# what the API reports.
import json, time, asyncio
from jade import config, fs, httpc
from jade.log import log

USAGE = config.HOME + "/usage.json"


class BudgetError(Exception):
    pass


def estimate(obj):
    if isinstance(obj, str):
        return len(obj) // 4 + 1
    return len(json.dumps(obj)) // 4 + 1


def _today():
    t = time.localtime()
    return "%04d-%02d-%02d" % t[:3]


class Usage:
    def __init__(self):
        self.day = _today()
        self.prompt = 0
        self.completion = 0
        self.calls = 0
        try:
            with fs.open(USAGE) as f:
                d = json.load(f)
            if d.get("day") == self.day:
                self.prompt, self.completion, self.calls = d["prompt"], d["completion"], d["calls"]
        except (OSError, ValueError, KeyError):
            pass

    def roll(self):
        if _today() != self.day:
            self.day = _today()
            self.prompt = self.completion = self.calls = 0

    @property
    def total(self):
        return self.prompt + self.completion

    def add(self, prompt, completion):
        self.roll()
        self.prompt += prompt
        self.completion += completion
        self.calls += 1
        try:
            config.makedirs(config.HOME)
            with fs.open(USAGE, "w") as f:
                json.dump(self.as_dict(), f)
        except OSError:
            pass

    def as_dict(self):
        return {"day": self.day, "prompt": self.prompt, "completion": self.completion, "calls": self.calls}


def fit(messages, budget, keep_last=1):
    # Trim a message list to roughly `budget` tokens: drop the oldest non-system turns (never the last
    # `keep_last`), then shorten long tool results. Returns a new list.
    msgs = [dict(m) for m in messages]   # copies: shortening must not touch the caller's history
    system = [m for m in msgs if m.get("role") == "system"]
    rest = [m for m in msgs if m.get("role") != "system"]

    def total():
        return sum(estimate(m.get("content") or "") + estimate(m.get("tool_calls") or "") + 4 for m in system + rest)

    while total() > budget and len(rest) > keep_last:
        dropped = rest.pop(0)
        # a tool result without its assistant call (or the reverse) confuses the API: drop the group
        while rest and rest[0].get("role") == "tool":
            rest.pop(0)
        if dropped.get("role") == "assistant" and dropped.get("tool_calls"):
            while rest and rest[0].get("role") == "tool":
                rest.pop(0)
    if total() > budget:
        for m in rest + system:
            c = m.get("content")
            if isinstance(c, str) and len(c) > 800:
                over = (total() - budget) * 4
                keep = max(400, len(c) - over)
                m["content"] = c[:keep] + "\n[…cut to fit the token budget]"
                if total() <= budget:
                    break
    return system + rest


class Cortex:
    def __init__(self):
        self.usage = Usage()
        self.last_error = None

    def cfg(self):
        return config.load()["cortex"]

    def status(self):
        c = self.cfg()
        self.usage.roll()
        return {"model": c["model"], "url": c["url"], "key_set": bool(c["key"]),
                "usage": self.usage.as_dict(), "daily_tokens": c["daily_tokens"],
                "left_today": max(0, c["daily_tokens"] - self.usage.total), "last_error": self.last_error}

    async def chat(self, messages, tools=None):
        c = self.cfg()
        self.usage.roll()
        if self.usage.total >= c["daily_tokens"]:
            raise BudgetError("daily token budget used up (%d of %d)" % (self.usage.total, c["daily_tokens"]))
        msgs = fit(messages, c["context_tokens"])
        body = {"model": c["model"], "messages": msgs, "max_tokens": c["max_tokens"],
                "temperature": c["temperature"]}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {}
        if c["key"]:
            headers["Authorization"] = "Bearer " + c["key"]
        est = estimate(msgs) + (estimate(tools) if tools else 0)
        for attempt in range(3):
            try:
                res = await httpc.request("POST", c["url"], body, headers, c["timeout"], c["tls_verify"])
                break
            except httpc.HTTPError as e:
                self.last_error = "HTTP %d" % e.status
                if e.status == 429 and attempt < 2:
                    wait = 2 ** attempt * 2
                    try:
                        wait = min(20, float(e.headers.get("retry-after", wait)))
                    except ValueError:
                        pass
                    log("cortex: rate limited, waiting", wait, "s")
                    await asyncio.sleep(wait)
                    continue
                if e.status >= 500 and attempt < 2:
                    await asyncio.sleep(2)
                    continue
                raise
        if not isinstance(res, dict) or not res.get("choices"):
            self.last_error = "bad response"
            raise ValueError("unexpected reply from the model API")
        u = res.get("usage") or {}
        self.usage.add(u.get("prompt_tokens", est), u.get("completion_tokens", estimate(res["choices"][0].get("message", {}).get("content") or "")))
        self.last_error = None
        return res["choices"][0]["message"]


cortex = Cortex()
