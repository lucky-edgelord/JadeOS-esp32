# The JadeOS shell on stock MicroPython: parsing, pipes, redirects, globs and the commands, on a scratch file system.
import asyncio
from t import check, done
from jade import fs, shell, vault, config

S = shell.Shell()


def sh(line, stdin=None, shl=None):
    s = shl or S

    async def go():
        b = shell.Buf()
        st = await s.run(line, b, None, stdin)
        return st, b.value()
    return asyncio.run(go())


def ok(line, want, msg=None, stdin=None):
    st, out = sh(line, stdin)
    check(st == 0 and out == want, "%s → %r (status %d), wanted %r" % (msg or line, out, st, want))
    return out


def fails(line, frag, msg=None):
    st, out = sh(line)
    check(st != 0 and frag in out, "%s should fail with %r: got status %d %r" % (msg or line, frag, st, out))


# ---- words, quotes, variables ----
ok("echo hello world", "hello world\n")
ok("echo 'a  b' \"c  d\" e\\ f", "a  b c  d e f\n", "quotes and escapes")
ok("echo $HOME ${USER} $UNSET.", "/ jade .\n", "variables; an unset one vanishes")
ok("X=5 ; echo $X", "5\n", "assignment")
ok("echo \"$X x\" '$X'", "5 x $X\n", "double quotes expand, single do not")
ok("echo -n a; echo b", "ab\n", "echo -n and ;")
ok("echo -e 'a\\tb'", "a\tb\n", "echo -e")
ok("echo a # comment", "a\n", "comments")
ok("false || echo yes", "yes\n", "||")
ok("true && echo yes", "yes\n", "&&")
ok("false && echo no; echo after", "after\n", "&& skips")
ok("true; echo $?", "0\n", "$?")
ok("false; echo $?", "1\n", "$? after failure")
fails("echo 'open", "unterminated", "unterminated quote")
fails("echo a |", "empty", "dangling pipe")
fails("nosuchcmd", "command not found", "unknown command")
st, _ = sh("nosuchcmd")
check(st == 127, "127 for unknown commands")
fails("echo a &", "not supported", "background")

# ---- files ----
ok("mkdir /t", "")
ok("cd /t; pwd", "/t\n")
ok("echo hello > a.txt; cat a.txt", "hello\n", "redirect and cat")
ok("echo more >> a.txt; cat a.txt", "hello\nmore\n", "append")
ok("cat < a.txt", "hello\nmore\n", "stdin redirect")
ok("cat -n a.txt", "     1\thello\n     2\tmore\n", "cat -n")
ok("pwd", "/t\n", "cwd persists")
ok("cd /; ls t", "a.txt\n", "ls a folder")
ok("cd t; mkdir -p x/y/z; ls", "a.txt\nx/\n", "mkdir -p, ls marks folders")
ok("touch b.md c.md; ls *.md", "b.md\nc.md\n", "glob")
ok("echo *.md", "b.md c.md\n", "glob in echo")
ok("echo '*.md'", "*.md\n", "quoted glob stays")
ok("echo nomatch*", "nomatch*\n", "no match keeps the pattern")
ok("ls -a /t", "a.txt\nb.md\nc.md\nx/\n", "ls -a (no dotfiles yet)")
ok("echo hidden > .h; ls", "a.txt\nb.md\nc.md\nx/\n", "dotfiles hidden")
ok("ls -a", ".h\na.txt\nb.md\nc.md\nx/\n", "ls -a shows dotfiles")
out = sh("ls -l a.txt")[1]
check(out.startswith("- ") and "11" in out and out.rstrip().endswith("a.txt"), "ls -l: " + out)
ok("cp a.txt d.txt; cat d.txt", "hello\nmore\n", "cp")
ok("mkdir sub; cp a.txt sub; ls sub", "a.txt\n", "cp into a folder")
ok("cp -r sub sub2; ls sub2", "a.txt\n", "cp -r")
fails("cp sub sub3", "use -r", "cp of a folder without -r")
ok("mv d.txt e.txt; ls | grep -c txt", "2\n", "mv and a pipe")
ok("mv e.txt sub; ls sub", "a.txt\ne.txt\n", "mv into a folder")
fails("cat nope", "No such file", "cat missing")
fails("rm nope", "No such file", "rm missing")
ok("rm -f nope", "", "rm -f ignores missing")
fails("rm sub", "Is a directory", "rm folder")
ok("rm -r sub sub2; ls", "a.txt\nb.md\nc.md\nx/\n", "rm -r")
fails("rm /", "refusing", "rm /")
ok("rmdir x/y/z; ls x/y", "", "rmdir")
ok("find . -name '*.md'", "./b.md\n./c.md\n", "find -name")
ok("find x -type d", "x\nx/y\n", "find -type d")
ok("find . -maxdepth 1 -type f -name 'a*'", "./a.txt\n", "find -maxdepth")
out = sh("du -s .")[1]
check(out.split("\t")[0].isdigit() and out.rstrip().endswith("."), "du -s: " + out)
out = sh("df")[1]
check("flash" in out and "%" in out, "df: " + out)
out = sh("stat a.txt")[1]
check("Size: 11" in out and "file" in out, "stat: " + out)

# ---- text tools ----
fs.write("/t/words.txt", "pear\napple\nbanana\napple\ncherry\n")
ok("sort words.txt", "apple\napple\nbanana\ncherry\npear\n", "sort")
ok("sort -r words.txt | head -n 2", "pear\ncherry\n", "sort -r | head")
ok("sort words.txt | uniq", "apple\nbanana\ncherry\npear\n", "uniq")
ok("sort words.txt | uniq -c | sort -n -r | head -1", "      2 apple\n", "uniq -c")
ok("sort -u words.txt | wc -l", "      4\n".replace("      4", "%7d" % 4), "sort -u | wc -l")
ok("head -n 2 words.txt", "pear\napple\n", "head")
ok("head -2 words.txt", "pear\napple\n", "head -2")
ok("tail -n 2 words.txt", "apple\ncherry\n", "tail")
ok("tail -1 words.txt", "cherry\n", "tail -1")
ok("grep an words.txt", "banana\n", "grep")
ok("grep -c apple words.txt", "2\n", "grep -c")
ok("grep -n apple words.txt", "2:apple\n4:apple\n", "grep -n")
ok("grep -v apple words.txt | wc -l", "%7d\n" % 3, "grep -v")
ok("grep -i PEAR words.txt", "pear\n", "grep -i")
ok("grep '^b.n' words.txt", "banana\n", "grep regex")
ok("grep -F 'a.p' words.txt || echo none", "none\n", "grep -F is literal")
ok("grep -l apple words.txt a.txt", "words.txt\n", "grep -l")
ok("grep -r hello /t", "/t/a.txt:hello\n", "grep -r")
ok("echo 'a,b,c' | cut -d , -f 2", "b\n", "cut -f")
ok("echo abcdef | cut -c 2-4", "bcd\n", "cut -c")
ok("echo hello | tr a-z A-Z", "HELLO\n", "tr")
ok("echo hello | tr -d l", "heo\n", "tr -d")
ok("echo a b c | wc -w", "%7d\n" % 3, "wc -w")
ok("printf 'x\\ny\\n' | wc -l", "%7d\n" % 2, "printf and wc -l")
ok("printf '%s=%d %x\\n' a 5 255", "a=5 ff\n", "printf formats")
ok("echo hi | tee t1.txt | cat; cat t1.txt", "hi\nhi\n", "tee")
ok("seq 3", "1\n2\n3\n", "seq")
ok("seq 2 2 6", "2\n4\n6\n", "seq with step")
ok("basename /a/b/c.txt .txt", "c\n")
ok("dirname /a/b/c.txt", "/a/b\n")
ok("echo hello | md5sum", "b1946ac92492d2347c6235b4d2611184  -\n", "md5sum")
ok("echo hello | sha256sum", "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03  -\n", "sha256sum")
ok("echo hello | cksum", "3015617425 6\n", "cksum matches the POSIX tool")
ok("cksum /t/empty 2>/dev/null; touch empty; cksum empty", "4294967295 0 empty\n", "cksum of an empty file")
ok("echo hello | base64", "aGVsbG8K\n", "base64")
ok("echo aGVsbG8K | base64 -d", "hello\n", "base64 -d")
out = sh("echo AB > hx; hexdump -C hx")[1]
check(out.startswith("00000000  41 42 0a") and out.rstrip().endswith("|AB.|"), "hexdump: " + out)

# ---- test / [ ----
ok("test -f a.txt && echo file", "file\n", "test -f")
ok("[ -d x ] && echo dir", "dir\n", "[ -d ]")
ok("[ -e nope ] || echo missing", "missing\n", "[ -e ]")
ok("[ a = a ] && [ 3 -lt 5 ] && echo ok", "ok\n", "string and integer tests")
ok("[ ! -z abc ] && echo nonempty", "nonempty\n", "! -z")

# ---- environment, history, misc ----
ok("export Y=7; echo $Y", "7\n", "export")
out = sh("env")[1]
check("Y=7" in out and "HOME=/" in out, "env: " + out)
ok("unset Y; echo [$Y]", "[]\n", "unset")
check("echo hello" in sh("history")[1], "history")
check("cat" in sh("help")[1] and "grep" in sh("help")[1], "help lists commands")
check(sh("which ls")[0] == 0 and sh("which nosuch")[0] == 1, "which")
check(sh("date")[1].count(":") == 2, "date: " + sh("date")[1])
check(sh("uname")[1].startswith(("linux", "esp32")), "uname: " + sh("uname")[1])
out = sh("free")[1]
check("Python heap" in out, "free: " + out)
check(sh("py 1+2")[1] == "3\n", "py expression")
fs.write("/t/s.py", "x = 6 * 7\n")
check(sh("py s.py")[0] == 0, "py script")
check(sh("py \"x = 5; assert x == 5\"")[0] == 0, "py statements")
st, out = sh("py 1/0")
check(st == 1 and "ZeroDivisionError" in out, "py error is reported: " + out)
check(sh("sleep 0.05")[0] == 0, "sleep")

# ---- an allow-list restricts the commands ----
R = shell.Shell(allow=["echo", "ls"])
st, out = sh("rm /t/a.txt", None, R)
check(st == 126 and "not allowed" in out and fs.exists("/t/a.txt"), "allow-list blocks rm: %r" % out)
check(sh("echo ok", None, R) == (0, "ok\n"), "allow-list lets echo through")

# ---- output is capped ----
fs.write("/t/big.txt", "0123456789abcdef\n" * 5000)
st, out = sh("cat big.txt")
check(len(out) < 70000 and "output cut" in out, "output capped: %d" % len(out))

# ---- Jade: vault, search, tools ----
config.load()["vault"] = "/vault"
vault.ensure()
vault.write("Garden.md", "# Garden\nPlant tomatoes in May. #todo\n")
out = sh("jade search tomatoes")[1]
check("Garden.md:2" in out, "jade search: " + out)
check(sh("jade search zzzzzz")[0] == 1, "jade search finds nothing → 1")
check("note_outline" in sh("jade tools")[1], "jade tools")
st, out = sh("jade tool search_vault '{\"query\": \"tomatoes\"}'")
check(st == 0 and "Garden" in out, "jade tool: " + out)
check("Garden" in sh("note Garden.md")[1], "note prints")
sh("note Garden.md 'new line'")
check("new line" in vault.read("Garden.md"), "note appends")
check(sh("jade nonsense")[0] == 2, "unknown jade command")
check("jade ask" in sh("jade help")[1], "jade help")
check(sh("jade-search tomatoes")[0] == 0, "jade-search alias")

# ---- interactive loop (the REPL helper) ----
shl = shell.Shell()
check(shl.prompt() == "jade:/$ ", "prompt")
sh("cd /t", None, shl)
check(shl.prompt() == "jade:/t$ ", "prompt follows cd")
sh("exit", None, shl)
check(shl.stop, "exit stops the loop")

fs.rmtree("/t")
done("shell")
