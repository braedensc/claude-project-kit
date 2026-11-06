#!/usr/bin/env python3
"""The hold: the watcher's one power, and the owner's alone to clear (KIT-515, contract section 20).
Design: docs/adr/2026-11-05-the-watcher-an-independent-auditor.md, "Pausing", decisions D3, D4 and D8.

    python3 scripts/pipeline_hold.py show  --hold-file PATH       what is held, and why
    python3 scripts/pipeline_hold.py clear --hold-file PATH       the owner clears it: one yes
    python3 scripts/pipeline_hold.py --selftest

WHAT A HOLD IS.  One small fields-only file. While it exists, nothing that starts work may start:
the jobs that start work call `gate()` before they start anything. The watcher writes it, when a
monitor flags something at level `pause`. It holds new starts and nothing else: nothing running is
touched, and nothing is reverted, closed, relabelled or merged. Only the owner clears it.

THE RULES, each one tested.
  * Absent is no hold. Anything else that is not a readable, valid hold HOLDS: a file that does not
    parse, names another schema, lists no hold, or cannot be read is a hold with that reason. A
    reader that cannot tell never starts work.
  * A hold has no expiry. Nothing clears it by the passage of time, by a later clean pass, or by
    the watcher: `write()` only adds, and it never empties the file.
  * `clear` is the owner's, in a terminal. It is refused inside an agent's environment, shows what
    is held, asks for a typed `yes`, removes the file, and reads it back.
  * Fields only. A hold names a monitor, a word from a closed list, a ticket or `owner/name#N`, and
    a time. No text from a ticket, comment or pull request is ever in it.
  * It is the watcher account's file, in a folder no worker can write: a worker that could delete it
    could lift its own pause. That is the install's job (KIT-517); the writers here refuse a path
    inside a git working tree.

WHAT IT NEVER DOES (asserted in --selftest).  Start a process, open a socket, send anything, or
remove any file but the hold, and only in `clear`.
"""
import argparse
import json
import os
import re
import sys
import time

HOLD_SCHEMA = "pipeline-hold/1"
SCHEMA_NAME = "pipeline-hold"
MONITORS = ("spend", "runs", "labels")
WHATS = ("spend-day", "spend-piece", "key-run", "runs-day", "runs-piece", "protected-label")
MAX_HOLDS = 50
MAX_BYTES = 1024 * 1024
REF_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}\Z")
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3
NONE, HELD, MALFORMED, UNREADABLE = "none", "held", "malformed", "unreadable"


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def problems(doc):
    """Everything wrong with a hold document, in words; [] when it is one."""
    if not isinstance(doc, dict) or set(doc) != {"schema", "set_at", "holds"}:
        return ["it is not a hold document"]
    out = []
    if doc["schema"] != HOLD_SCHEMA:
        out.append("it names another schema")
    if not isinstance(doc["set_at"], str) or not TIME_RE.match(doc["set_at"]):
        out.append("its time is not a UTC time")
    holds = doc["holds"]
    if not isinstance(holds, list) or not 1 <= len(holds) <= MAX_HOLDS:
        return out + ["it lists no hold, or too many"]
    for h in holds:
        if (not isinstance(h, dict) or set(h) != {"monitor", "what", "ref", "since"}
                or h["monitor"] not in MONITORS or h["what"] not in WHATS
                or not isinstance(h["ref"], str) or not REF_RE.match(h["ref"])
                or not isinstance(h["since"], str) or not TIME_RE.match(h["since"])):
            out.append("a hold in it is not a hold")
            break
    return out


def read(path):
    """{"state": none | held | malformed | unreadable, "holds": [...], "why": words}. Absent is
    none. Everything else that is not a valid hold is a state that HOLDS, never none."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return {"state": NONE, "holds": [], "why": "no hold file"}
    except OSError as exc:
        return {"state": UNREADABLE, "holds": [],
                "why": "the hold file could not be read (%s)" % (exc.strerror or "error")}
    if len(raw) > MAX_BYTES:
        return {"state": MALFORMED, "holds": [], "why": "the hold file is larger than a hold"}
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError):
        return {"state": MALFORMED, "holds": [], "why": "the hold file is not readable JSON"}
    bad = problems(doc)
    if bad:
        return {"state": MALFORMED, "holds": [], "why": "the hold file is not a hold: %s" % bad[0]}
    return {"state": HELD, "holds": doc["holds"], "why": "held"}


def gate(path):
    """(blocked, why). Blocked for every state but none. `why` is words and numbers, never text."""
    got = read(path)
    if got["state"] == NONE:
        return False, ""
    if got["state"] != HELD:
        return True, "%s, so nothing starts" % got["why"]
    seen = sorted(set("%s %s" % (h["what"], h["ref"]) for h in got["holds"]))
    return True, "the watcher has held new starts: %s%s" % (
        ", ".join(seen[:3]), " and %d more" % (len(seen) - 3) if len(seen) > 3 else "")


def inside_git_worktree(path):
    cur = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def write(path, new, now):
    """The watcher's only way to hold: ADD `new` ([(monitor, what, ref)]) to what is there. It keeps
    the earliest time of a hold already held, never empties the file, and past MAX_HOLDS keeps the
    newest and returns how many older ones it dropped: (document, dropped). An
    unreadable or invalid file is replaced, since a hold that cannot be read already holds. Atomic.
    Raises OSError when it cannot write, which the caller must treat as 'could not hold'."""
    if not os.path.isabs(path) or inside_git_worktree(os.path.dirname(path)):
        raise OSError("the hold file must be an absolute path outside every git working tree")
    got = read(path)
    if got["state"] == UNREADABLE:
        raise OSError(got["why"])
    held = dict((("%s|%s|%s" % (h["monitor"], h["what"], h["ref"])), h) for h in got["holds"])
    at = _iso(now())
    for monitor, what, ref in new:
        key = "%s|%s|%s" % (monitor, what, ref)
        held.setdefault(key, {"monitor": monitor, "what": what, "ref": ref, "since": at})
    ordered = sorted(held.values(), key=lambda h: (h["since"], h["monitor"], h["what"], h["ref"]))
    holds = ordered[-MAX_HOLDS:]        # the newest: the owner must see the latest cause
    dropped = len(ordered) - len(holds)
    doc = {"schema": HOLD_SCHEMA, "set_at": at, "holds": holds}
    if not holds or problems(doc):
        raise OSError("the hold it would write is not a valid hold")
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return doc, dropped


def show_lines(path):
    got = read(path)
    if got["state"] == NONE:
        return ["nothing is held"]
    if got["state"] != HELD:
        return ["HELD (%s): %s" % (got["state"], got["why"])]
    return ["held: %d reason(s)" % len(got["holds"])] + [
        "  %s: %s %s since %s" % (h["monitor"], h["what"], h["ref"], h["since"]) for h in got["holds"]]


def clear(path, ask=input, out=print, environ=None):
    """The owner's clear. Refused inside an agent's environment; shows what is held; one typed
    `yes`; removes the file and reads it back. Returns an exit code."""
    from pipeline_dispatch_local import AGENT_ENV_MARKERS
    env = os.environ if environ is None else environ
    found = [m for m in AGENT_ENV_MARKERS if m in env]
    if found:
        out("REFUSED: this clears a pause the watcher set, and an agent must never do that. Run it "
            "in your own terminal. (An agent's environment was found: %s.)" % ", ".join(found))
        return EXIT_REFUSED
    for line in show_lines(path):
        out(line)
    if read(path)["state"] == NONE:
        out("Nothing to clear.")
        return EXIT_OK
    if ask("Clear it, so new starts may begin again? Type yes: ").strip() != "yes":
        out("Not cleared.")
        return EXIT_OK
    try:
        os.unlink(path)
    except OSError as exc:
        out("FAILED: could not remove %s (%s)." % (path, exc.strerror or "error"))
        return EXIT_ERROR
    if read(path)["state"] != NONE:
        out("FAILED: the hold file is still there.")
        return EXIT_ERROR
    out("Cleared: new starts may begin again.")
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description="The watcher's hold.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("show", "clear"):
        p = sub.add_parser(name)
        p.add_argument("--hold-file", required=True)
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "hold-selftest-run-")
    if args.cmd in ("show", "clear"):
        path = os.path.expanduser(args.hold_file)
        if not os.path.isabs(path):
            print("COULD NOT RUN: --hold-file must be an absolute path.")
            return EXIT_USAGE
        if args.cmd == "show":
            print("\n".join(show_lines(path)))
            return EXIT_OK
        return clear(path)
    ap.print_help()
    return EXIT_USAGE


# ======================================================================================
# SELFTEST: nothing below the line above is run by a reader, and the source scan stops there.
# ======================================================================================
def selftest():
    import copy
    import tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import check_schemas
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:260], str(want)[:260]))

    NOW = 1793890800            # a fixed instant
    clock = lambda: NOW         # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        path = os.path.join(tmp, "hold", "hold.json")
        put = lambda text: (os.makedirs(os.path.dirname(path), exist_ok=True),  # noqa: E731
                            open(path, "w", encoding="utf-8").write(text))
        check("absent is no hold: nothing blocked", (read(path)["state"], gate(path)),
              (NONE, (False, "")))
        doc, dropped0 = write(path, [("labels", "protected-label", "acme/app#12")], clock)
        check("a hold is written, valid, world-readable, with no temp file left",
              (check_schemas.document_problems(doc, SCHEMA_NAME), oct(os.stat(path).st_mode & 0o777),
               sorted(os.listdir(os.path.dirname(path)))), ([], "0o644", ["hold.json"]))
        blocked, why = gate(path)
        check("a hold blocks, and says what in words and ids", (blocked, why),
              (True, "the watcher has held new starts: protected-label acme/app#12"))
        later = lambda: NOW + 600   # noqa: E731
        write(path, [("labels", "protected-label", "acme/app#12"), ("spend", "spend-day", "2026-11-05")],
              later)
        got = read(path)
        check("writing again only ADDS, and keeps the earliest time of a hold it already had",
              ([(h["what"], h["since"]) for h in got["holds"]]),
              [("protected-label", _iso(NOW)), ("spend-day", _iso(NOW + 600))])
        write(path, [], later)
        check("writing nothing changes nothing, and never empties the file",
              (len(read(path)["holds"]), read(path)["state"]), (2, HELD))
        for n in range(1, 80):
            _d, dropped = write(path, [("spend", "spend-piece", "KIT-%d" % n)], lambda n=n: NOW + 1000 + n)
        got = read(path)
        check("more than the cap keep the NEWEST, are never emptied, and say how many were dropped",
              (len(got["holds"]), got["state"], got["holds"][-1]["ref"], dropped,
               any(h["ref"] == "KIT-79" for h in got["holds"]),
               any(h["ref"] == "KIT-1" for h in got["holds"])),
              (MAX_HOLDS, HELD, "KIT-79", 1, True, False))
        os.unlink(path)
        _d, dropped_at_once = write(path, [("spend", "spend-piece", "KIT-%d" % n) for n in range(1, 81)], later)
        check("one write of more than the cap says how many it dropped, and still holds",
              (dropped_at_once, len(read(path)["holds"]), read(path)["state"]), (30, MAX_HOLDS, HELD))
        # every state that is not a valid hold holds
        for label, text in (("a file that is not JSON", "{no"), ("another schema", json.dumps(
                {"schema": "x/1", "set_at": _iso(NOW), "holds": []})),
                            ("a hold list that is empty", json.dumps(
                                {"schema": HOLD_SCHEMA, "set_at": _iso(NOW), "holds": []})),
                            ("a hold that is not one", json.dumps(
                                {"schema": HOLD_SCHEMA, "set_at": _iso(NOW), "holds": [{"monitor": "x"}]})),
                            ("a hold with free text", json.dumps(
                                {"schema": HOLD_SCHEMA, "set_at": _iso(NOW), "holds": [
                                    {"monitor": "spend", "what": "spend-day", "ref": "ignore all previous",
                                     "since": _iso(NOW)}]})),
                            ("a file larger than a hold", " " * (MAX_BYTES + 1)),
                            ("an empty file", "")):
            put(text)
            blocked, why = gate(path)
            check("%s holds, as malformed, and says so" % label,
                  (blocked, read(path)["state"], why.endswith("so nothing starts")), (True, MALFORMED, True))
        if os.geteuid() != 0:
            put(json.dumps(doc))
            os.chmod(os.path.dirname(path), 0o000)
            try:
                check("a hold folder that cannot be read holds, as unreadable",
                      (gate(path)[0], read(path)["state"]), (True, UNREADABLE))
                try:
                    write(path, [("labels", "protected-label", "a/b#1")], clock)
                    refused = False
                except OSError:
                    refused = True
                check("...and the watcher cannot write through it: 'could not hold' is loud", refused, True)
            finally:
                os.chmod(os.path.dirname(path), 0o755)
        put("{no")
        write(path, [("runs", "runs-day", "2026-11-05")], clock)
        check("a malformed file is replaced by a valid hold, since it already held",
              (read(path)["state"], len(read(path)["holds"])), (HELD, 1))
        inside = os.path.join(tmp, "tree")
        os.makedirs(os.path.join(inside, ".git"))
        try:
            write(os.path.join(inside, "hold.json"), [("labels", "protected-label", "a/b#1")], clock)
            refused = False
        except OSError:
            refused = True
        check("a hold file inside a git working tree is refused", refused, True)
        check("a relative hold file is refused", (lambda: _raises(
            lambda: write("hold.json", [("labels", "protected-label", "a/b#1")], clock)))(), True)

        # the owner's clear
        said = []
        code = clear(path, ask=lambda _p: "yes", out=said.append, environ={})
        check("the owner's clear: shows, asks, removes, reads back", (
            code, read(path)["state"], any("held:" in s for s in said), said[-1]),
            (EXIT_OK, NONE, True, "Cleared: new starts may begin again."))
        put(json.dumps(doc))
        for answer in ("y", "YES", "no", ""):
            said = []
            code = clear(path, ask=lambda _p, a=answer: a, out=said.append, environ={})
            check("anything but a typed yes clears nothing (%r)" % answer,
                  (code, read(path)["state"], said[-1]), (EXIT_OK, HELD, "Not cleared."))
        from pipeline_dispatch_local import AGENT_ENV_MARKERS
        said = []
        code = clear(path, ask=lambda _p: "yes", out=said.append, environ={AGENT_ENV_MARKERS[0]: "1"})
        check("an agent's environment is refused, and the hold stays",
              (code, read(path)["state"], said[0].startswith("REFUSED")), (EXIT_REFUSED, HELD, True))
        said = []
        if os.path.exists(path):
            os.unlink(path)
        code = clear(path, ask=lambda _p: "yes", out=said.append, environ={})
        check("clearing nothing says so, and succeeds", (code, said[-1]), (EXIT_OK, "Nothing to clear."))

        # the schema holds the line
        good = copy.deepcopy(doc)
        for label, mutate in (
                ("an invented monitor", lambda d: d["holds"][0].update(monitor="mood")),
                ("an invented reason", lambda d: d["holds"][0].update(what="boredom")),
                ("free text in a ref", lambda d: d["holds"][0].update(ref="ignore all previous")),
                ("an excerpt field", lambda d: d["holds"][0].update(excerpt="hello")),
                ("a local time", lambda d: d.update(set_at="2026-11-05 15:00")),
                ("no holds", lambda d: d.update(holds=[])),
                ("another marker", lambda d: d.update(schema="pipeline-hold/2"))):
            d = copy.deepcopy(good)
            mutate(d)
            check("the schema refuses %s" % label, bool(check_schemas.document_problems(d, SCHEMA_NAME))
                  and bool(problems(d)), True)

        # what it never does
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            src = fh.read()
        above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
        banned = ("subprocess", "os.system", "socket", "urlopen", "shutil", "os.rename", "eval(", "exec(")
        check("it starts no process, opens no socket and sends nothing", [b for b in banned if b in above], [])
        check("the only removal of the hold is in the owner's clear (plus the writer's own temp file)",
              (above.count("os.unlink(path)"), above.count("os.unlink(tmp)")), (1, 1))
    if failures:
        for line in failures:
            print(line)
        print("hold selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: hold selftest (%d checks)" % total[0])
    return 0


def _raises(fn):
    try:
        fn()
        return False
    except OSError:
        return True


if __name__ == "__main__":
    sys.exit(main())
