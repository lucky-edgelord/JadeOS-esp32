# jadecore on the MicroPython unix port: VfsJade, search/scan/strip through it, and the bridge.
# Run: micropython-jade tests/unix/test_jadecore.py <scratch dir>
import sys, os, jadecore, _thread, time

passed = 0
failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", msg)


base = sys.argv[1]
# the host side: make <base>/root before switching the root filesystem
try:
    os.mkdir(base)
except OSError:
    pass
try:
    os.mkdir(base + "/root")
except OSError:
    pass

os.umount("/")
os.mount(jadecore.VfsJade(base), "/")
os.chdir("/root")
check(os.getcwd() == "/root", "cwd /root: " + os.getcwd())

# files and directories
os.mkdir("vault")
os.mkdir("/root/vault/Projects")
with open("vault/Projects/Garden.md", "w") as f:
    f.write("---\ntags: [garden]\n---\n# Garden\nPlant **tomatoes** in May. See [[Seeds]].\n- [ ] buy seeds #todo\n")
with open("/root/vault/Seeds.md", "w") as f:
    f.write("# Seeds\nTomato, basil.\n")
check(sorted(os.listdir("/root/vault")) == ["Projects", "Seeds.md"], "listdir " + str(os.listdir("/root/vault")))
check(os.listdir("/") == ["root"], "root lists only root: " + str(os.listdir("/")))
check(os.stat("/")[0] & 0x4000, "stat / is a dir")
check(os.stat("vault")[0] == 0x4000, "stat dir")
st = os.stat("vault/Seeds.md")
check(st[0] == 0x8000 and st[6] == 23, "stat file " + str(st))
os.chdir("vault/Projects")
check(os.getcwd() == "/root/vault/Projects", "relative chdir")
with open("../Seeds.md") as f:
    check(f.read().startswith("# Seeds"), ".. resolved")
os.chdir("./../..//vault/.")
check(os.getcwd() == "/root/vault", "messy path normalised: " + os.getcwd())
os.chdir("/")
check(os.getcwd() == "/", "chdir /")
os.chdir("root")
try:
    os.chdir("vault/Seeds.md")
    check(False, "chdir into a file")
except OSError as e:
    check(e.errno == 20, "ENOTDIR")
try:
    open("nope.md")
    check(False, "missing file")
except OSError as e:
    check(e.errno == 2, "ENOENT")
with open("vault/b.bin", "wb") as f:
    f.write(b"\x00\x01\x02" * 10)
with open("vault/b.bin", "rb") as f:
    f.seek(3)
    check(f.read(3) == b"\x00\x01\x02" and f.tell() == 6, "binary seek/read")
with open("vault/b.bin", "a") as f:
    f.write("x")
check(os.stat("vault/b.bin")[6] == 31, "append")
os.rename("vault/b.bin", "vault/c.bin")
with open("vault/d.bin", "w") as f:
    f.write("d")
os.rename("vault/c.bin", "vault/d.bin")
check(os.stat("vault/d.bin")[6] == 31 and "c.bin" not in os.listdir("vault"), "rename replaces")
os.remove("vault/d.bin")
os.mkdir("vault/tmp")
os.rmdir("vault/tmp")
check(sorted(os.listdir("vault")) == ["Projects", "Seeds.md"], "remove/rmdir")
with open("vault/Ünïcode.md", "w") as f:
    f.write("Grüße 🌱\n")
with open("vault/Ünïcode.md") as f:
    check(f.read() == "Grüße 🌱\n", "unicode name and text")
for line in open("vault/Seeds.md"):
    check(line == "# Seeds\n", "iterate lines")
    break
check(len(os.statvfs("/root")) == 10, "statvfs answers")

# imports through the VFS
os.mkdir("lib")
with open("lib/jvtest_mod.py", "w") as f:
    f.write("X = 42\n")
sys.path.append("/root/lib")
import jvtest_mod
check(jvtest_mod.X == 42, "import from /root/lib")

# read-only mount
os.umount("/")
os.mount(jadecore.VfsJade(base), "/", readonly=True)
try:
    open("/root/x", "w")
    check(False, "write on read-only")
except OSError as e:
    check(e.errno == 30, "EROFS")
os.umount("/")
os.mount(jadecore.VfsJade(base), "/")
os.chdir("/root")

# indexer through vault paths (the prefix is mapped)
hits = jadecore.search("/root/vault", "tomatoes")
check(len(hits) == 1 and hits[0][0] == "/root/vault/Projects/Garden.md", "search paths are vault paths: " + str(hits))
check(hits[0][3] == "Plant tomatoes in May. See Seeds.", "snippet is plain text " + repr(hits[0][3]))
check(jadecore.search("/root/vault", "#todo")[0][0].endswith("Garden.md"), "tag search")
check(jadecore.search("/root/vault", "zzzz") == [], "no hits")
try:
    jadecore.search("/root/missing", "tomatoes")
    check(False, "missing root")
except OSError:
    check(True, "")
s = jadecore.scan("/root/vault/Projects/Garden.md")
kinds = [k for (k, *_r) in s]
check((jadecore.META, "tags", "[garden]", 0, 2) in s, "meta " + str(s))
check((jadecore.HEADING, "Garden", "", 1, 4) in s, "heading")
check((jadecore.LINK, "Seeds", "", 0, 5) in s, "link")
check((jadecore.TASK, "buy seeds #todo", "", 0, 6) in s, "task")
check((jadecore.TAG, "todo", "", 0, 6) in s, "tag")
check(jadecore.scan("/root/vault/Projects/Garden.md", jadecore.LINK) == [(jadecore.LINK, "Seeds", "", 0, 5)], "kinds mask")
check(jadecore.strip("# T\n**b** [[x|y]]") == "T\nb y", "strip")
check(jadecore.strip("") == "", "strip empty")

# bridge: a "shell" thread asks, this thread answers
got = []


def shell_side():
    got.append(jadecore.bridge_call("ask what is 2+2", 3000))
    got.append(jadecore.bridge_call("nobody answers", 200))


_thread.start_new_thread(shell_side, ())
req = None
for _ in range(200):
    req = jadecore.bridge_take()
    if req:
        break
    time.sleep_ms(5)
check(req is not None and req[1] == "ask what is 2+2", "take " + str(req))
check(jadecore.bridge_reply(req[0], "four", 0), "reply delivered")
check(not jadecore.bridge_reply(req[0], "again"), "second reply refused")
for _ in range(200):
    if len(got) == 2:
        break
    time.sleep_ms(10)
check(got[0] == (0, "four"), "shell got the reply: " + str(got))
check(len(got) == 2 and got[1] == (-2, None), "unanswered request times out")
check(jadecore.bridge_pending() == 0, "nothing pending")

# shell (host fallback runs /bin/sh)
st, out = jadecore.shell("printf 'a\\377b\\342\\202'")
check(st == 0 and out == "a?b", "shell output made valid UTF-8: " + repr(out))
check(jadecore.shell("exit 3")[0] == 3, "exit status")

print("jadecore: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
