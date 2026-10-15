#!/usr/bin/env python3
"""Builds the one-file dashboard for GitHub Pages: web/dashboard.html + the Jade avatar → docs/index.html.

    python3 tools/build_dashboard.py [--no-avatar]

The avatar (PixiJS, Live2D Cubism Core, pixi-live2d-display and the jadecat model) is inlined from
web/live2d-jadecat.html and web/jade-avatar.js, the same code Jade Vault uses. --no-avatar leaves it
out (a small static face is shown instead) for a light build.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(avatar=True, out=os.path.join(ROOT, "docs", "index.html")):
    src = open(os.path.join(ROOT, "web", "dashboard.html"), encoding="utf-8").read()
    if avatar:
        runtime = open(os.path.join(ROOT, "web", "live2d-jadecat.html"), encoding="utf-8").read()
        js = open(os.path.join(ROOT, "web", "jade-avatar.js"), encoding="utf-8").read()
    else:
        runtime, js = "", ""
    assert "<!--@LIVE2D@-->" in src and "/*@JADE_AVATAR@*/" in src
    html = src.replace("<!--@LIVE2D@-->", runtime).replace("/*@JADE_AVATAR@*/", js)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out, len(html.encode())


if __name__ == "__main__":
    out, size = build("--no-avatar" not in sys.argv)
    print("wrote %s (%.1f KB)" % (os.path.relpath(out, ROOT), size / 1024))
