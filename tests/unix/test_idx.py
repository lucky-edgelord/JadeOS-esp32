# The vault indexer (jade.idx) on stock MicroPython. Run with JADEOS_ROOT=<scratch dir>.
import sys, os
from t import check, done
from jade import fs, idx

root = fs.ROOT
assert root, "set JADEOS_ROOT"


def put(rel, text):
    fs.write("/" + rel, text)


V = "/idx"
put("idx/Projects/Garden plan.md", "---\ntags: [garden, spring]\nstatus: active\n---\n# Garden plan\nPlant **tomatoes** in May.\n## Watering\nWater the tomatoes every morning. #garden\n- [ ] buy seeds\n- [x] dig beds\nSee [[Seeds|seed list]] and [notes](Other.md).\n")
put("idx/Seeds.md", "# Seeds\nTomato, basil, chili.\n```\n#notatag [[NotALink]]\n```\nInline `#code [[nope]]` here. Real #tag-one and #nested/tag.\n")
put("idx/Journal/2026-10-14.md", "Today I thought about nothing in particular.\nLong line: ")
put("idx/.hidden/secret.md", "tomatoes tomatoes tomatoes")
put("idx/notes.txt", "tomatoes but not markdown")
put("idx/Long.md", "x" * 2900 + " zebrafish\n")
put("idx/a/b/c/d/e/f/g/h/i/j/Deep.md", "tomatoes deep")

h = idx.search(V, "Tomatoes watering", 5, 80)
check(len(h) >= 1, "hits: %r" % (h,))
check(h and h[0][0].endswith("Garden plan.md"), "best hit is the garden plan: %r" % (h[:1],))
check(h and h[0][2] == 7, "best line is the '## Watering' heading (7): %r" % (h[:1],))
check(h and h[0][3] == "Watering", "snippet is 'Watering': %r" % (h[:1],))
for i, x in enumerate(h):
    check(".hidden" not in x[0] and "notes.txt" not in x[0] and "Deep.md" not in x[0], "hidden, non-md and too-deep files are skipped: " + x[0])
    if i:
        check(h[i - 1][1] >= x[1], "sorted by score")
hz = idx.search(V, "zebrafish", 5, 40)
check(len(hz) == 1 and hz[0][0].endswith("Long.md") and hz[0][2] == 1, "a term far past the chunk size is found on line 1: %r" % (hz,))
check(len(hz) == 1 and len(hz[0][3]) <= 40, "snippet cut to snip_len")
check(len(idx.search(V, "#garden", 5, 80)) == 1 and idx.search(V, "#garden")[0][0].endswith("Garden plan.md"), "#tag search finds inline and frontmatter tags")
check(idx.search(V, "#gard") == [], "#tag search needs the whole tag")
hs = idx.search(V, "seeds", 1, 80)
check(len(hs) == 1 and hs[0][0].endswith("Seeds.md"), "k=1 keeps the best (title match): %r" % (hs,))
check(idx.search(V, "a") == [], "one-letter queries match nothing")
try:
    idx.search("/no/such/dir", "tomatoes")
    check(False, "missing root should raise")
except OSError:
    check(True)
t = idx.terms("Hello, WORLD! #Tag/sub hello x  verylongtermverylongtermverylongtermzz")
check(t[:3] == ["hello", "world", "#tag/sub"] and len(t) == 4 and len(t[3]) == idx.TERM_MAX, "terms: %r" % (t,))

p = V + "/Projects/Garden plan.md"
s = idx.scan(p)
check((idx.META, "tags", "[garden, spring]", 0, 2) in s and (idx.META, "status", "active", 0, 3) in s, "frontmatter: %r" % (s,))
check((idx.HEADING, "Garden plan", "", 1, 5) in s and (idx.HEADING, "Watering", "", 2, 7) in s, "headings")
check((idx.TASK, "buy seeds", "", 0, 9) in s and (idx.TASK, "dig beds", "", 1, 10) in s, "tasks")
check((idx.LINK, "Seeds", "seed list", 0, 11) in s and (idx.LINK, "Other.md", "notes", 0, 11) in s, "links")
check((idx.TAG, "garden", "", 0, 8) in s, "tags")
s2 = idx.scan(V + "/Seeds.md", idx.TAG | idx.LINK)
txt = repr(s2)
check("notatag" not in txt and "NotALink" not in txt and "nope" not in txt and "'code'" not in txt, "fenced and inline code are skipped: " + txt)
check(any(x[1] == "tag-one" for x in s2) and any(x[1] == "nested/tag" for x in s2), "tags with - and /: " + txt)
try:
    idx.scan("/no/such.md")
    check(False, "missing file should raise")
except OSError:
    check(True)

md = "---\ntitle: x\n---\n\n\n# Title\n\nSome **bold** and `code` with [[Target|alias]], [[Plain#Head]], [link](http://x.y) and ![pic](a.png).\n> quoted <b>html</b>\n\n\n\n```\n# not a heading\n```\n   - item\n"
want = "title: x\n\nTitle\n\nSome bold and code with alias, Plain, link and pic.\nquoted html\n\n# not a heading\n   - item"
got = idx.strip(md)
check(got == want, "strip:\n---got---\n%s\n---want---\n%s" % (got, want))
check(idx.strip("") == "", "strip empty")
check(idx.strip("# T\n**b** [[x|y]]") == "T\nb y", "strip short")

# async search gives the loop back and returns the same hits
import asyncio


async def main():
    return await idx.asearch(V, "Tomatoes watering", 5, 80)


check(asyncio.run(main()) == h, "asearch == search")
fs.rmtree(V)
done("idx")
