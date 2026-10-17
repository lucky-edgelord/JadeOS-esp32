# A tiny test harness for the MicroPython unix port: check(cond, msg); done() prints the totals and exits.
import sys

passed = 0
failed = 0


def check(cond, msg=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", msg)


def done(name):
    print("%s: %d passed, %d failed" % (name, passed, failed))
    sys.exit(1 if failed else 0)
