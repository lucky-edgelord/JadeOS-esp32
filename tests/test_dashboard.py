#!/usr/bin/env python3
"""The dashboard (docs/index.html) in headless Chromium, against the unix-port device + fake model.

    python3 tests/test_dashboard.py [--shots DIR]
"""
import argparse
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_api  # noqa: E402  (client + process helpers)
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = test_api.ROOT
passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", msg, flush=True)


def serve_docs(port):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=os.path.join(ROOT, "docs"))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    a = ap.parse_args()
    subprocess.run([sys.executable, os.path.join(ROOT, "tools/build_dashboard.py")], check=True, stdout=subprocess.DEVNULL)
    llm_port, dev_port, web_port = 18090, 18091, 18092
    data = tempfile.mkdtemp(prefix="jade-dash-")
    procs = [subprocess.Popen([sys.executable, os.path.join(ROOT, "tools/fake_llm.py"), str(llm_port)], stdout=subprocess.DEVNULL)]
    log = open(os.path.join(data, "device.log"), "w")
    procs.append(subprocess.Popen([test_api.MP, os.path.join(ROOT, "tests/unix/device.py"), data, str(dev_port),
                                   "http://127.0.0.1:%d/v1/chat/completions" % llm_port], stdout=log, stderr=subprocess.STDOUT))
    srv = serve_docs(web_port)
    dev = "http://127.0.0.1:%d" % dev_port
    shots = a.shots
    if shots:
        os.makedirs(shots, exist_ok=True)
    try:
        if not test_api.wait_up(dev, 60):
            print(open(os.path.join(data, "device.log")).read())
            return 1
        c = test_api.Client(dev, "test-token")
        c.req("PUT", "/api/vault/note", {"path": "Projects/Garden.md", "text": "---\ntags: [garden]\n---\n# Garden plan\nPlant **tomatoes** in May, see [[Seeds]].\n- [ ] buy compost #todo\n"})
        c.req("PUT", "/api/vault/note", {"path": "Seeds.md", "text": "# Seeds\nTomato and basil seeds.\n"})

        with sync_playwright() as p:
            exe = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") and not os.environ.get("CI") else None
            try:
                browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            except Exception:
                browser = p.chromium.launch(executable_path=shutil.which("chromium") or exe, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1366, "height": 860})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "fonts.g" not in m.text and "net::ERR" not in m.text and "status of 40" not in m.text else None)
            page.route("https://fonts.googleapis.com/**", lambda r: r.abort())
            page.route("https://fonts.gstatic.com/**", lambda r: r.abort())

            # --- first visit: the connect sheet
            page.goto("http://127.0.0.1:%d/" % web_port)
            page.wait_for_selector("#connect:not([hidden])", timeout=10000)
            check(page.is_visible("#cToken"), "connect sheet asks for the token")
            page.fill("#cUrl", "127.0.0.1:%d" % dev_port)
            page.fill("#cToken", "wrong")
            page.click("#connectForm button[type=submit]")
            page.wait_for_function("document.querySelector('#cMsg').textContent.includes('refused')", timeout=10000)
            check(True, "a wrong token is reported")
            page.fill("#cToken", "test-token")
            page.click("#connectForm button[type=submit]")
            page.wait_for_selector("#connect[hidden]", state="attached", timeout=15000)
            page.wait_for_function("document.querySelector('#devDot').classList.contains('ok')", timeout=10000)
            check("127.0.0.1" in page.text_content("#devName") or "jade" in page.text_content("#devName"), "connected: " + page.text_content("#devName"))

            # --- the avatar
            page.wait_for_function("typeof Jade !== 'undefined' && (Jade.ready || document.querySelector('.jade-fallback'))", timeout=60000)
            live2d = page.evaluate("Jade.ready")
            check(live2d, "Live2D Jade is on stage (WebGL)")
            if live2d:
                page.wait_for_timeout(1500)
                px = page.evaluate("""() => { const c = document.querySelector('.jade-canvas'); const g = c.getContext('webgl2', {preserveDrawingBuffer:true}) || c.getContext('webgl');
                    return c.width > 0 && c.height > 0 }""")
                check(px, "avatar canvas has a size")
            if shots:
                page.screenshot(path=os.path.join(shots, "1-desktop-connected.png"))

            # --- chat: plain question, vault context, reply bar
            page.fill("#prompt", "what about tomatoes?")
            page.press("#prompt", "Enter")
            page.wait_for_selector(".msg-ai .replybar", timeout=30000)
            reply = page.text_content(".msg-ai:last-child .md")
            check(reply.startswith("You asked: what about tomatoes?") and "Projects/Garden.md" in reply, "chat reply: " + reply)
            check(page.is_visible(".msg-ai:last-child .chip[data-open='Projects/Garden.md']"), "note chip under the reply")
            check(page.text_content("#jadeMood") in ("done", "online"), "Jade looks happy after answering: " + page.text_content("#jadeMood"))
            # streamed tool steps
            page.fill("#prompt", "search my notes for compost")
            page.press("#prompt", "Enter")
            page.wait_for_function("document.querySelectorAll('.msg-ai').length >= 2 && document.querySelector('.msg-ai:last-child .replybar')", timeout=30000)
            check(page.text_content(".msg-ai:last-child .tool .tname") == "search_vault" and page.text_content(".msg-ai:last-child .tool .tstat") == "ok", "tool card from the stream")
            check(page.text_content(".msg-ai:last-child .md").startswith("Tool said: Projects/Garden.md"), "tool answer")
            # copy / thumbs / retry
            page.context.grant_permissions(["clipboard-read", "clipboard-write"])
            page.click(".msg-ai:last-child .replybar [data-a=copy]")
            clip = page.evaluate("navigator.clipboard.readText()")
            check(clip.startswith("Tool said:"), "copy puts the reply on the clipboard: " + clip[:40])
            page.click(".msg-ai:last-child .replybar [data-a=up]")
            check(page.get_attribute(".msg-ai:last-child .replybar [data-a=up]", "aria-pressed") == "true", "thumbs up toggles")
            n_before = page.evaluate("document.querySelectorAll('.msg-ai').length")
            page.click(".msg-ai:last-child .replybar [data-a=retry]")
            page.wait_for_function("document.querySelector('.msg-ai:last-child .replybar') && !document.querySelector('.msg-ai:last-child .caret')", timeout=30000)
            check(page.evaluate("document.querySelectorAll('.msg-ai').length") == n_before, "retry replaces the last answer")
            # clicking the note chip opens it in the vault
            page.click(".msg-ai .chip[data-open='Projects/Garden.md']")
            page.wait_for_selector("#noteView h1", timeout=10000)
            check(page.text_content("#noteView h1") == "Garden plan" and page.text_content("#notePath") == "Projects/Garden.md", "note chip opens the note")
            check(page.is_visible("#noteView a.wl[data-note='Seeds']") and page.is_visible("#noteView .tag"), "wikilinks and tags rendered")
            check("buy compost" in page.text_content("#outline") and "garden" in page.text_content("#outline"), "outline shows tasks and tags")
            page.click("#noteView a.wl[data-note='Seeds']")
            page.wait_for_function("document.querySelector('#notePath').textContent === 'Seeds.md'", timeout=10000)
            check(True, "wikilink navigation")
            check("Projects/Garden.md" in page.text_content("#outline"), "backlinks listed")

            # --- vault: edit + save, search, new note
            page.click("#vEdit")
            page.fill("#noteEdit", "# Seeds\nTomato, basil and **chili** seeds.\n")
            page.click("#vSave")
            page.wait_for_function("document.querySelector('#noteView').textContent.includes('chili')", timeout=10000)
            st, _, n = c.get("/api/vault/note?path=Seeds.md")
            check("**chili**" in n["text"], "edit saved on the device")
            page.fill("#vSearch", "#todo")
            page.wait_for_selector(".hit", timeout=10000)
            check(page.text_content(".hit .p").startswith("Projects/Garden.md"), "search results")
            page.on("dialog", lambda d: d.accept("Inbox/From the deck.md") if d.type == "prompt" else d.accept())
            page.click("#vNew")
            page.wait_for_selector("#noteEdit:not([hidden])", timeout=10000)
            page.fill("#noteEdit", "# From the deck\nhello")
            page.click("#vSave")
            page.wait_for_function("document.querySelector('#notePath').textContent === 'Inbox/From the deck.md'", timeout=10000)
            st, _, n = c.get("/api/vault/note?path=Inbox/From%20the%20deck.md")
            check(st == 200 and n["text"] == "# From the deck\nhello", "new note created on the device")
            page.click("#vDelete")
            page.click("#vDelete")
            page.wait_for_function("document.querySelector('#notePath').textContent === '—'", timeout=10000)
            st, _, _ = c.get("/api/vault/note?path=Inbox/From%20the%20deck.md")
            check(st == 404, "delete (with confirm click)")
            if shots:
                page.click("#vRefresh")
                page.evaluate("openNote('Projects/Garden.md')")
                page.wait_for_timeout(800)
                page.screenshot(path=os.path.join(shots, "2-desktop-vault.png"))

            # --- shell
            page.click(".tab[data-tab=shell]")
            page.fill("#termIn", "echo hello from the deck")
            page.press("#termIn", "Enter")
            page.wait_for_function("[...document.querySelectorAll('#term .out')].some(o => o.textContent.includes('hello from the deck'))", timeout=10000)
            check(True, "shell echo")
            page.fill("#termIn", "reboot")
            page.press("#termIn", "Enter")
            page.wait_for_function("document.querySelector('#term .out:last-child').classList.contains('err')", timeout=10000)
            check("not allowed" in page.text_content("#term .out:last-child"), "shell refusal shown")
            page.press("#termIn", "ArrowUp")
            check(page.input_value("#termIn") == "reboot", "shell history")

            # --- system
            page.click(".tab[data-tab=system]")
            page.wait_for_function("document.querySelector('#stats').textContent.includes('Python heap')", timeout=10000)
            check("Tokens today" in page.text_content("#cortexStats"), "cortex budget card")
            check(page.input_value("[name='cortex.model']") == "llama-3.3-70b-versatile", "config loaded")
            page.fill("[name='agent.context_hits']", "2")
            page.click("#cfgForm button[type=submit]")
            page.wait_for_function("document.querySelector('#cfgMsg').textContent.startsWith('saved')", timeout=10000)
            st, _, cfg = c.get("/api/system/config")
            check(cfg["agent"]["context_hits"] == 2 and cfg["cortex"]["key"] == "set", "settings saved, key untouched")
            page.wait_for_function("document.querySelectorAll('#toolsList .tool').length >= 8", timeout=10000)
            page.click("#toolsList .tool:has(.tname:text('current_time')) summary")
            page.click("#toolsList form[data-tool=current_time] button[type=submit]")
            page.wait_for_function("document.querySelector('form[data-tool=current_time] pre').textContent.startsWith('20')", timeout=10000)
            check(True, "run a tool from the tools card")
            check("agent:" in page.text_content("#logs"), "device log shown")
            if shots:
                page.screenshot(path=os.path.join(shots, "3-desktop-system.png"))

            # --- persistence: reload remembers the device and the chat
            page.reload()
            page.wait_for_function("document.querySelector('#devDot').classList.contains('ok')", timeout=15000)
            page.wait_for_selector(".msg-ai", timeout=10000)
            check(page.evaluate("document.querySelectorAll('.msg-user').length") >= 2, "chat history reloaded from the device")
            page.click("#btnClear")
            page.click("#btnClear")
            page.wait_for_selector(".examples", timeout=10000)
            st, _, h = c.get("/api/agent/history")
            check(h["history"] == [], "forget clears the device's history")

            # --- phone layout
            m = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            m.on("pageerror", lambda e: errors.append(str(e)))
            m.route("https://fonts.googleapis.com/**", lambda r: r.abort())
            m.route("https://fonts.gstatic.com/**", lambda r: r.abort())
            m.goto("http://127.0.0.1:%d/#device=%s&token=test-token" % (web_port, dev))
            m.wait_for_function("document.querySelector('#devDot').classList.contains('ok')", timeout=15000)
            check(m.is_visible("#prompt") and m.is_visible(".tab[data-tab=chat]") and not m.is_visible("#tree"), "phone: chat screen with a bottom tab bar")
            check("token" not in m.url, "token removed from the address bar")
            m.wait_for_function("typeof Jade !== 'undefined' && (Jade.ready || document.querySelector('.jade-fallback'))", timeout=60000)
            m.wait_for_timeout(800)
            if shots:
                m.screenshot(path=os.path.join(shots, "4-phone-chat.png"))
            m.tap(".tab[data-tab=vault]")
            m.wait_for_selector(".tree-row", timeout=10000)
            check(m.is_visible("#tree") and not m.is_visible("#prompt"), "phone: vault screen")
            m.tap(".tree-row[data-path='Seeds.md']")
            m.wait_for_selector("#noteView h1", timeout=10000)
            check(m.is_visible("#noteView") and not m.is_visible("#tree"), "phone: note opens full screen")
            if shots:
                m.screenshot(path=os.path.join(shots, "5-phone-note.png"))
            m.tap("#vBack")
            check(m.is_visible("#tree"), "phone: back to the list")
            overflow = m.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            check(not overflow, "phone: no horizontal scroll")

            # --- demo mode needs no device
            d = browser.new_page(viewport={"width": 1200, "height": 800})
            d.on("pageerror", lambda e: errors.append(str(e)))
            d.goto("http://127.0.0.1:%d/" % web_port)
            d.evaluate("localStorage.clear()")
            d.reload()
            d.wait_for_selector("#connect:not([hidden])")
            d.click("#cDemo")
            d.wait_for_function("document.querySelector('#devName').textContent.includes('demo')", timeout=10000)
            d.fill("#prompt", "tomatoes")
            d.press("#prompt", "Enter")
            d.wait_for_selector(".msg-ai .replybar", timeout=15000)
            check("Projects/Garden.md" in d.text_content(".msg-ai .md"), "demo mode answers from the demo vault")

            check(not errors, "no page errors: %s" % errors[:5])
            browser.close()
    finally:
        for pr in procs:
            pr.terminate()
        srv.shutdown()
        if failed:
            print(open(os.path.join(data, "device.log")).read()[-3000:])
        shutil.rmtree(data, ignore_errors=True)
    print("dashboard: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
