#!/usr/bin/env python3
"""The cleanup probe: did a run leave anything behind on the role account? (KIT-468)

A PERSON runs it, twice around one run of each kind (a dispatched coding session, a Stage E review, a planning run), and
once to compare. It changes nothing: `snapshot` lists and stats and prints one JSON document; `compare` reads two of them.

    # as the role account, from `/`, the script fed on stdin (the role cannot read your home):
    (cd / && sudo -u <ROLE_ACCOUNT> /usr/bin/python3 -I - snapshot --label before-coding \\
        --workspace-dir <dispatcher worktrees> --dispatcher-home <dispatcher home> \\
        < scripts/pipeline_cleanup_probe.py > before-coding.json)
    #   ...the run, then the sweep's pass, then the same command with --label after-coding > after-coding.json
    python3 scripts/pipeline_cleanup_probe.py compare before-coding.json after-coding.json

WHAT A SNAPSHOT HOLDS. For each area, the names of its entries, two levels deep (`project` and `project/session`), with their
size and file count, and how much it could not read: the dispatcher's worktrees, its home (one folder per ticket), Claude Code's scratch (`/tmp/claude-<uid>`),
the account's TMPDIR, `~/.stage-e` and `~/.stage-a` (the jobs' state), and `~/.claude/projects` (transcripts, which a
retention rule keeps and the verdict does not judge). Names, sizes and counts only: no file is opened and no symlink followed.

WHAT `compare` SAYS. An entry, at either level, in the second snapshot that the first did not have is LEFT BEHIND, except a
retained area and, under ~/.stage-e and ~/.stage-a, a job log (the sweep trims those, so a job's log can appear or grow). Exit 0: nothing left. 1: something left. 4: a snapshot could not read part
of an area, so "nothing left" is not known. 2: usage. 3: refused. `snapshot` is refused in an agent environment; `compare`
reads two files and is not.
"""
import argparse
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import time

AGENT_ENV_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PROJECT_DIR", "AI_AGENT")
SCHEMA = "cleanup-probe/1"
EX_OK, EX_LEFT, EX_USAGE, EX_REFUSED, EX_UNKNOWN = 0, 1, 2, 3, 4
RETAINED = ("claude-transcripts",)             # kept by a stated rule, so never judged


def refuse_if_agent(env=None):
    found = [m for m in AGENT_ENV_MARKERS if m in (os.environ if env is None else env)]
    if found:
        sys.stderr.write("REFUSED: a snapshot runs as the role account, and this is an agent environment (%s set). "
                         "A PERSON runs it, in a terminal.\n" % ", ".join(found))
        return True
    return False


def size_of(root):
    """(bytes, files, unreadable) of everything under `root`, symlinks counted and never followed."""
    size = files = unreadable = 0
    stack = [root]
    while stack:
        p = stack.pop()
        try:
            st = os.lstat(p)
        except OSError:
            unreadable += 1
            continue
        size += getattr(st, "st_blocks", 0) * 512 or st.st_size
        if stat.S_ISDIR(st.st_mode):
            try:
                stack.extend(e.path for e in os.scandir(p))
            except OSError:
                unreadable += 1
        else:
            files += 1
    return size, files, unreadable


def measure(path):
    """({entry: {"bytes", "files"}}, unreadable) for the entries of `path` and of each folder in it (`top` and `top/child`).
    A missing folder is empty, and says so by being absent; one that cannot be listed is unreadable. Symlinks are counted,
    never followed."""
    out, unreadable = {}, 0
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name)
    except FileNotFoundError:
        return None, 0
    except OSError:
        return {}, 1
    for entry in entries:
        size, files, bad = size_of(entry.path)
        out[entry.name] = {"bytes": size, "files": files}
        unreadable += bad
        if entry.is_dir(follow_symlinks=False):
            try:
                children = sorted(os.scandir(entry.path), key=lambda e: e.name)
            except OSError:
                continue                            # already counted as unreadable above
            for child in children:
                csize, cfiles, _bad = size_of(child.path)
                out[entry.name + "/" + child.name] = {"bytes": csize, "files": cfiles}
    return out, unreadable


def areas_of(workspace_dir, dispatcher_home, home, tmp, uid, scratch_root=None):
    """[(label, path, retained)]: the places a finished run must leave empty-handed."""
    return [("worktrees", workspace_dir, False), ("dispatcher-home", dispatcher_home, False),
            ("claude-scratch", scratch_root or "/tmp/claude-%d" % uid, False), ("tmpdir", tmp, False),
            ("stage-e", os.path.join(home, ".stage-e"), False), ("stage-a", os.path.join(home, ".stage-a"), False),
            ("claude-transcripts", os.path.join(home, ".claude", "projects"), True)]


def snapshot(label, areas, account, now=None):
    doc = {"schema": SCHEMA, "label": label, "account": account,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now)), "areas": {}}
    for name, path, retained in areas:
        entries, unreadable = measure(path)
        doc["areas"][name] = {"path": path, "retained": retained, "present": entries is not None,
                              "entries": entries or {}, "unreadable": unreadable}
    return doc


LOG_AREAS = ("stage-e", "stage-a")


def is_log(area, name):
    """A job's log, or a rotated copy of one (`x.log`, `x.log.1`), under a jobs' state area only: a log that appears
    anywhere else is a leftover like any other file."""
    return area in LOG_AREAS and re.search(r"\.log(\.[0-9]+)?$", os.path.basename(name)) is not None


def well_formed(doc):
    try:
        return (doc.get("schema") == SCHEMA and isinstance(doc.get("label"), str) and all(
            isinstance(v["entries"], dict) and isinstance(v["unreadable"], int) and "retained" in v and "present" in v
            and all(isinstance(e["bytes"], int) and isinstance(e["files"], int) for e in v["entries"].values())
            for v in doc["areas"].values()))
    except (AttributeError, KeyError, TypeError):
        return False


def compare(before, after):
    """(exit code, lines). LEFT BEHIND is an entry only the second snapshot has, at either level, outside the retained
    areas and the jobs' logs; an entry under a new parent is named by the parent."""
    if not (well_formed(before) and well_formed(after)):
        return EX_USAGE, ["not a well-formed %s snapshot" % SCHEMA]
    lines, left, unknown = [], [], []
    for name in sorted(set(before["areas"]) | set(after["areas"])):
        a, b = before["areas"].get(name), after["areas"].get(name)
        if a is None or b is None:
            unknown.append("%s is in only one snapshot" % name)
            continue
        if a["unreadable"] or b["unreadable"]:
            unknown.append("%s could not be read whole (%d + %d parts)" % (name, a["unreadable"], b["unreadable"]))
        new = sorted(set(b["entries"]) - set(a["entries"]))
        gone = sorted(set(a["entries"]) - set(b["entries"]))
        if a["retained"] or b["retained"]:
            lines.append("%-18s retained by rule: %d entries added, %d removed (not judged)" % (name, len(new), len(gone)))
            continue
        kept = [n for n in new if not is_log(name, n) and ("/" not in n or n.split("/")[0] in a["entries"])]
        for n in kept:
            e = b["entries"][n]
            left.append("%s/%s (%.1f MB, %d files)" % (name, n, e["bytes"] / 1e6, e["files"]))
        for n in sorted(set(a["entries"]) & set(b["entries"])):
            grown = b["entries"][n]["files"] - a["entries"][n]["files"]
            if (grown > 0 or b["entries"][n]["bytes"] > a["entries"][n]["bytes"]) and "/" not in n and not is_log(name, n) \
                    and not [k for k in kept if k.startswith("%s/%s/" % (name, n)) or k == "%s/%s" % (name, n)]:
                lines.append("%-18s NOTE %s grew by %d files, %.1f KB (an existing folder: not judged)" % (
                    name, n, grown, (b["entries"][n]["bytes"] - a["entries"][n]["bytes"]) / 1e3))
        lines.append("%-18s %d added (%d of them job logs), %d removed%s" % (
            name, len(new), len([n for n in new if is_log(name, n)]), len(gone), "" if b["present"] else "  [folder absent]"))
    lines.append("")
    lines += ["LEFT BEHIND: " + x for x in left] + ["NOT KNOWN: " + x for x in unknown]
    if left:
        lines.append("%d entr%s left behind between %s and %s." % (len(left), "y" if len(left) == 1 else "ies",
                                                                   before["label"], after["label"]))
        return EX_LEFT, lines
    if unknown:
        lines.append("Nothing left that could be seen, but part of it could not be read: not proven.")
        return EX_UNKNOWN, lines
    lines.append("Nothing left behind between %s and %s." % (before["label"], after["label"]))
    return EX_OK, lines


def temp_dir():
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return os.path.realpath(r.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    return os.path.realpath(os.environ.get("TMPDIR") or "/tmp")


def main(argv=None, env=None):
    p = argparse.ArgumentParser(description="Did a run leave anything behind on the role account? (KIT-468)")
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("snapshot", help="list and stat the role account's areas; print JSON")
    s.add_argument("--label", required=True)
    s.add_argument("--workspace-dir", required=True)
    s.add_argument("--dispatcher-home", required=True)
    c = sub.add_parser("compare", help="what the second snapshot has that the first did not")
    c.add_argument("before")
    c.add_argument("after")
    args = p.parse_args(argv)
    if args.selftest:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "cleanup-probe-selftest-run-")
    if args.cmd == "snapshot":
        if refuse_if_agent(env):
            return EX_REFUSED
        uid = os.getuid()
        home = pwd.getpwuid(uid).pw_dir
        doc = snapshot(args.label, areas_of(args.workspace_dir, args.dispatcher_home, home, temp_dir(), uid),
                       pwd.getpwuid(uid).pw_name)
        sys.stdout.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return EX_OK
    if args.cmd == "compare":
        docs = []
        for path in (args.before, args.after):
            try:
                with open(path, encoding="utf-8") as fh:
                    docs.append(json.load(fh))
            except (OSError, ValueError) as exc:
                sys.stderr.write("could not read %s: %s\n" % (path, exc))
                return EX_USAGE
        code, lines = compare(*docs)
        print("\n".join(lines))
        return code
    p.print_usage(sys.stderr)
    return EX_USAGE


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    import contextlib
    import inspect
    import io
    import shutil
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        ok = got == want
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            fails.append(name)

    def put(path, data=b"x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    def quiet(fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return fn(), out.getvalue()

    with tempfile.TemporaryDirectory(prefix="probe-") as root:
        root = os.path.realpath(root)
        ws, dh, home, tmp = (os.path.join(root, n) for n in ("worktrees", "dispatcher", "home", "tmp"))
        scratch = os.path.join(root, "claude-scratch")
        for d in (ws, dh, home, tmp, scratch):
            os.makedirs(d)
        areas = areas_of(ws, dh, home, tmp, 4242, scratch_root=scratch)
        put(ws + "/ENG-1/f", b"y" * 5000)
        put(home + "/.stage-e/poller.log", b"z" * 100)
        put(home + "/.stage-e/state/telemetry/a.json", b"{}")
        put(home + "/.claude/projects/-x/t.jsonl", b"t")
        before = snapshot("before", areas, "role", now=0)
        check("a snapshot holds the schema, every area, sizes and counts, and no file's content",
              (before["schema"], sorted(before["areas"]), before["areas"]["worktrees"]["entries"]["ENG-1"]["files"],
               before["areas"]["worktrees"]["entries"]["ENG-1"]["bytes"] >= 5000, "yyyy" in json.dumps(before)),
              (SCHEMA, sorted(n for n, _p, _r in areas), 1, True, False))
        check("an absent folder is an area that is not present, not an error and not unreadable",
              (before["areas"]["tmpdir"]["present"], before["areas"]["claude-scratch"]["entries"]), (True, {}))
        code, lines = compare(before, snapshot("after", areas, "role", now=1))
        check("an identical pair has nothing left", (code, lines[-1]), (EX_OK, "Nothing left behind between before and after."))

        # a run that cleaned up: its entries came and went, only a log grew, transcripts were added
        put(ws + "/ENG-2/f")
        put(home + "/.stage-e/poller.log", b"z" * 9999)
        put(home + "/.stage-e/poller.log.1", b"old")
        put(home + "/.stage-e/new-job.log", b"a job that logs for the first time")
        put(home + "/.stage-e/state/telemetry/b.json", b"a file the review poller keeps per PR")
        shutil.rmtree(ws + "/ENG-1")                       # a worktree that was there before the run, and went
        put(home + "/.claude/projects/-y/t.jsonl", b"t")
        os.remove(ws + "/ENG-2/f")
        os.rmdir(ws + "/ENG-2")
        code, lines = compare(before, snapshot("after", areas, "role", now=1))
        check("a grown log, a rotated copy, a new log folder, a new transcript and a removed worktree are not left behind",
              (code, any("LEFT BEHIND" in l for l in lines), any("retained by rule: 2 entries added" in l for l in lines),
               any(l.startswith("worktrees") and "0 added" in l and "2 removed" in l for l in lines)),
              (EX_OK, False, True, True))
        check("a folder that only gained files is noted as growth, not judged",
              any("NOTE state grew by 1 files" in l for l in lines), True)

        # a run that left things
        put(ws + "/ENG-3/f", b"w" * 2048)
        put(scratch + "/-proj-ENG-3/s/x")
        put(tmp + "/pipeline-voice-ab12cd34/h")
        put(dh + "/ENG-3/attachments/.keep", b"")
        code, lines = compare(before, snapshot("after", areas, "role", now=1))
        left = [l for l in lines if l.startswith("LEFT BEHIND")]
        check("each folder a run left is named with its size, in its area", (code, len(left), sorted(
            l.split()[2].split("/")[0] for l in left)), (EX_LEFT, 4, ["claude-scratch", "dispatcher-home", "tmpdir", "worktrees"]))
        check("…and the verdict line counts them", lines[-1].startswith("4 entries left behind between before and after"), True)

        for leftover in (ws + "/ENG-3", scratch + "/-proj-ENG-3", tmp + "/pipeline-voice-ab12cd34", dh + "/ENG-3"):
            shutil.rmtree(leftover)

        # a leftover INSIDE a folder that was already there is a leftover too (Stage E's medium)
        put(scratch + "/-proj-reused/old-session/x")
        nested_before = snapshot("nested-before", areas, "role", now=3)
        put(scratch + "/-proj-reused/new-session/x", b"z" * 4096)
        code, lines = compare(nested_before, snapshot("nested-after", areas, "role", now=4))
        check("a session folder left under a project folder that already existed is LEFT BEHIND, named by its path",
              (code, [l for l in lines if l.startswith("LEFT BEHIND")][0].startswith("LEFT BEHIND: claude-scratch/-proj-reused/new-session")),
              (EX_LEFT, True))
        shutil.rmtree(scratch + "/-proj-reused/new-session")
        put(ws + "/job.log")
        put(tmp + "/v0.1")
        code, lines = compare(nested_before, snapshot("log-after", areas, "role", now=5))
        check("a log or a version-like name outside the jobs' state is a leftover like any file",
              (code, sorted(l.split()[2].split("(")[0] for l in lines if l.startswith("LEFT BEHIND"))),
              (EX_LEFT, ["tmpdir/v0.1", "worktrees/job.log"]))
        os.remove(ws + "/job.log")
        os.remove(tmp + "/v0.1")
        shutil.rmtree(scratch + "/-proj-reused")

        # what cannot be read is never read as clean: a listing that fails, under any uid
        real_scandir = os.scandir

        def scandir(p="."):
            if p == home + "/.stage-e":
                raise PermissionError(13, "Permission denied")
            return real_scandir(p)
        os.scandir = scandir
        try:
            blind = snapshot("blind", areas, "role", now=2)
        finally:
            os.scandir = real_scandir
        code, lines = compare(before, blind)
        check("an unreadable area makes the pair NOT KNOWN (exit 4) when nothing visible was left, never clean",
              (code, any("NOT KNOWN: stage-e" in l for l in lines)), (EX_UNKNOWN, True))
        broken = json.loads(json.dumps(before))
        del broken["areas"]["tmpdir"]["entries"]
        truncated = json.loads(json.dumps(before))
        truncated["areas"]["worktrees"]["entries"]["ENG-1"] = {"bytes": 1}
        check("a document that is not a snapshot, or is cut short, is a usage error (exit 2), never a verdict",
              [compare({}, before)[0], compare(before, broken)[0], compare(before, truncated)[0],
               compare(before, dict(before, label=None))[0]], [EX_USAGE] * 4)
        check("an area in one snapshot only is NOT KNOWN",
              compare(before, dict(before, areas={k: v for k, v in before["areas"].items() if k != "tmpdir"}))[0],
              EX_UNKNOWN)

        # the command line
        a, b = root + "/a.json", root + "/b.json"
        put(a, json.dumps(before).encode())
        put(b, json.dumps(snapshot("after", areas, "role", now=1)).encode())
        got, out = quiet(lambda: main(["compare", a, b]))
        check("compare: the command reads two files and exits by the verdict", (got, "Nothing left" in out), (EX_OK, True))
        got, _o = quiet(lambda: main(["compare", a, root + "/missing.json"]))
        check("compare: a missing file is a usage error", got, EX_USAGE)
        for marker in AGENT_ENV_MARKERS:
            got, out = quiet(lambda m=marker: main(["snapshot", "--label", "x", "--workspace-dir", ws, "--dispatcher-home", dh],
                                                   env={m: ""}))
            check("snapshot: refused under %s, even set empty, and prints no snapshot" % marker,
                  (got, "{" in out), (EX_REFUSED, False))

    check("a snapshot opens no file: neither measure nor snapshot calls open()",
          ["open(" in inspect.getsource(f) for f in (measure, snapshot)], [False, False])
    src = open(os.path.abspath(__file__), encoding="utf-8").read().split("\n# ---")[0]
    check("it changes nothing: no write, remove, rename or mutating call outside the selftest",
          [w for w in ("os.remove", "os.rename", "os.replace", "shutil", "os.chmod", "os.makedirs", "open(path, \"w", ".write(")
           if w in src.replace("sys.stdout.write", "").replace("sys.stderr.write", "")], [])
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
