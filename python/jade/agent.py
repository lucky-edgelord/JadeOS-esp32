# Jade's agent loop: Search → Reason → Act.
#
#   Search: the C indexer finds the notes most related to the prompt; their best lines (and the top
#           note's text) go into the prompt as context, so small questions need no tool call at all.
#   Reason: the remote cortex answers, or asks for tools.
#   Act:    requested tools run on the device; their output goes back to the model; repeat up to
#           agent.max_steps times.
#
# Conversation history stays in RAM (and /jade/history.json) and is trimmed by the cortex's
# token budget.
import json, time
from jade import config, fs, vault, tools
from jade.cortex import cortex, BudgetError
from jade.httpc import HTTPError
from jade.log import log

HISTORY = config.HOME + "/history.json"

PERSONA = ("You are Jade, a warm, quick-witted assistant living on a tiny ESP32-S3 computer (JadeOS). "
           "You keep the user's notes in a Markdown vault on the device. Answer briefly and plainly. "
           "Use the vault context below when it helps, and the tools to read, search or change notes "
           "or to run allowed device commands. Never invent note contents: read them.")


class Agent:
    def __init__(self):
        self.history = []
        self.busy = False
        self.load()

    def load(self):
        try:
            with fs.open(HISTORY) as f:
                self.history = json.load(f)
        except (OSError, ValueError):
            self.history = []

    def save(self):
        n = config.load()["agent"]["history"] * 2
        self.history = self.history[-n:]
        try:
            config.makedirs(config.HOME)
            with fs.open(HISTORY, "w") as f:
                json.dump(self.history, f)
        except OSError:
            pass

    def clear(self):
        self.history = []
        self.save()

    async def context(self, prompt):
        # Search step: vault notes related to the prompt, as plain text
        k = config.load()["agent"]["context_hits"]
        try:
            hits = await vault.asearch(prompt, k, 200)
        except OSError:
            hits = []
        if not hits:
            return "", []
        lines = ["Notes in the vault that may be relevant:"]
        for h in hits:
            lines.append("- %s (line %d): %s" % (h["path"], h["line"], h["snippet"]))
        try:
            top = vault.plain(hits[0]["path"], 1500)
            lines.append("\nStart of %s:\n%s" % (hits[0]["path"], top))
        except Exception:
            pass
        return "\n".join(lines), [h["path"] for h in hits]

    async def think(self, prompt, on_event=None, use_history=True):
        # → {"reply", "steps": [...], "notes": [...], "usage": {...}}
        def emit(kind, **data):
            data["type"] = kind
            if on_event:
                try:
                    on_event(data)
                except Exception:
                    pass
            return data

        cfg = config.load()["agent"]
        if self.busy:
            return {"reply": "I'm still working on the last request. Try again in a moment.", "error": "busy", "steps": [], "notes": []}
        self.busy = True
        t0 = time.ticks_ms()
        steps = []
        try:
            ctx, notes = await self.context(prompt)
            steps.append(emit("search", notes=notes))
            system = (cfg.get("persona") or PERSONA)
            t = time.localtime()
            system += "\nDevice time: %04d-%02d-%02d %02d:%02d." % t[:5]
            if ctx:
                system += "\n\n" + ctx
            msgs = [{"role": "system", "content": system}]
            if use_history:
                msgs += self.history
            msgs.append({"role": "user", "content": prompt})
            new = [msgs[-1]]
            reply = ""
            for step in range(cfg["max_steps"]):
                last = step == cfg["max_steps"] - 1
                msg = await cortex.chat(msgs, None if last else tools.specs())
                calls = msg.get("tool_calls") or []
                if not calls:
                    reply = msg.get("content") or ""
                    break
                amsg = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
                msgs.append(amsg)
                new.append(amsg)
                for call in calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    steps.append(emit("tool", name=name, args=fn.get("arguments", "")))
                    ok, out = await tools.run(name, fn.get("arguments") or "{}")
                    steps.append(emit("result", name=name, ok=ok, output=out[:500]))
                    tmsg = {"role": "tool", "tool_call_id": call.get("id", name), "content": out}
                    msgs.append(tmsg)
                    new.append(tmsg)
            else:
                reply = reply or "I ran out of steps before finishing."
            new.append({"role": "assistant", "content": reply})
            if use_history:
                # keep only the user turn and the final answer: tool chatter would eat the budget
                self.history += [new[0], new[-1]]
                self.save()
            steps.append(emit("reply", text=reply))
            log("agent: %d steps, %d ms" % (len(steps), time.ticks_diff(time.ticks_ms(), t0)))
            return {"reply": reply, "steps": steps, "notes": notes, "usage": cortex.usage.as_dict(),
                    "ms": time.ticks_diff(time.ticks_ms(), t0)}
        except BudgetError as e:
            return {"reply": str(e), "error": "budget", "steps": steps, "notes": []}
        except HTTPError as e:
            log("agent: model API error", e.status)
            return {"reply": "The model API answered with an error (%d)." % e.status, "error": "http %d" % e.status, "detail": e.body[:300], "steps": steps, "notes": []}
        except Exception as e:
            log("agent: failed:", type(e).__name__, e)
            return {"reply": "I couldn't reach my cortex: %s" % (str(e) or type(e).__name__), "error": type(e).__name__, "steps": steps, "notes": []}
        finally:
            self.busy = False


agent = Agent()
