#!/usr/bin/env python3
"""The owner's live run of one runner lane, on one test project (KIT-916; docs/RUNNER.md, "Proving a
lane live").

    python3 scripts/pipeline_runner_live.py check --lane code --project app --ticket ENG-12
    python3 scripts/pipeline_runner_live.py run   --lane code --project app --ticket ENG-12
    python3 scripts/pipeline_runner_live.py off   --lane code --project app
    python3 scripts/pipeline_runner_live.py --selftest

    Options: --conf runner.conf · --queue-conf queue.conf · --probe <the probe's --out folder>
             --minutes N (the lane's own time limit) · --after-minutes N · --stage-e-state <folder>
             --acceptance-config <file>

WHAT IT IS.  A person's tool, run in their own terminal, never by a session. It proves one lane of our
own runner (code, fix, review, plan or acceptance) on one test project, from off to on and back to off,
and checks each step on the way. No project is named here: the lane, the project and the ticket are its
arguments, so a new user proves a lane on a project of their own before a real one moves.

    1. CHECK every prerequisite, and name each one missing in words: the runner installed, switched on and
       its job loaded; the folder its job runs from one the role account cannot change (the root-owned
       copy, KIT-919); the kit clone at main's head; the project's clone with an HTTPS origin the role
       account's git reaches; RUNNER_STORE set for the queue, in queue.conf and installed; each key file
       the role account's and mode 0600, looked at and never read; for a lane that pushes, the push token
       pushing to the project in a dry run; for a lane with ticket tools, the session writer's key answering
       as its user and the project's session brief naming the tools; the role-account probe's last result
       passing, at a commit at or after main's last change to the session kinds; the project's
       delivery.json on main passing the kit's validator; now inside the queue's working window; the lane
       off now, and the settings with it on taken by the runner's own loader; for the coding lane, no other
       approved ticket waiting; and the ticket ready for its chain. Anything it cannot read is UNKNOWN,
       never OK. `check` stops here and changes nothing.
    2. SHOW exactly what `run` switches on, and ask for one typed yes.
    3. SWITCH that one lane on for that one project: `projects.<project>.lanes.<lane>` in the runner's
       settings, written as the role account, judged by the runner's own loader first, read back, and
       taken only once the runner says so in its store's lanes.json.
    4. WAIT cheaply for the chain the lane's live proof describes, with a time limit: one read of the
       store a poll, as the role account, and GitHub only once the store says there is something to see.
    5. CHECK that the run record, the tracker history, the pull request and the ledger agree, where that
       proof asks.
    6. SWITCH the lane back off by the same setting, confirm the runner took it, and watch that nothing
       of that lane starts after.
    7. PRINT one result block to post on the ticket, with everything that did not work.

A failed step, an interrupt (Ctrl-C, a closed terminal) or a surprise always ends with step 6. If even
that cannot be confirmed, it says so loudly, exits 6, and names the one command that switches it off:
`off` does only that, and asks no yes, since it only makes things safer.

NEVER A CREDENTIAL HERE.  No key or token ever reaches this script's own process, its output or a command
line. Each key file is looked at with `stat`, never opened. Two checks need a key to be used, and each runs
inside the role account's own processes, as the runner itself does: the dry-run push hands the push token
to git through the adapter's own helper, and the session writer's key is asked who it is by the runner's
own code. The tracker is read only by the runner's own reader (pipeline_runner.LinearTracker), run as the
role account from the kit clone the runner runs from, with the runner's own key file: only fields come back
(ids, states, times, label names, verdict marks), never a title, a body or a key. GitHub is read with `gh`,
under your own login, which this script never sees. Every role-account call is `sudo -n`: it asks for your
password once, with `sudo -v`, and keeps that session alive while it waits.

Refused under a model (any agent-environment marker) and as root, before it reads anything.
Exits: 0 the chain ran, everything agreed, the lane is off · 1 a step failed or did not agree; the lane is
off · 2 arguments or confs · 3 refused · 4 a prerequisite is missing or could not be read; nothing changed ·
5 sudo was refused; nothing changed · 6 the lane could not be confirmed off: do what it says · 10 you did
not type yes; nothing changed.
"""
import argparse
import base64
import calendar
import json
import os
import plistlib
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pipeline_agent_env import AGENT_ENV_MARKERS  # noqa: E402  (the one list, imported, never copied)
import pipeline_harness_claude as harness  # noqa: E402  (git's helper for the push token, as a session gets it)
import pipeline_notifier_setup as ns  # noqa: E402  (the role-file read and write, and a person's login)
import pipeline_queue_setup as qs  # noqa: E402  (queue.conf's reader, and `~/` made the role's home)
import pipeline_review_local as review_local  # noqa: E402  (Stage E's review marker and its coverage line)
import pipeline_runner as runner_job  # noqa: E402  (its own loader judges the switched settings)
import pipeline_runner_setup as setup  # noqa: E402  (runner.conf, and where the settings live)
import pipeline_runner_store as store  # noqa: E402  (the kinds, callers and shapes of the store)
import pipeline_session_tools as session_tools  # noqa: E402  (the five ticket tools' names)
import pipeline_window as window  # noqa: E402  (the working window, as the queue judges it)
from pipeline_stage_e_setup import SetupError, _dispatcher_plist  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REFUSED, EXIT_MISSING, EXIT_NOPRIV, EXIT_STILL_ON, EXIT_NOT_YES = (
    0, 1, 2, 3, 4, 5, 6, 10)
OK, MISSING, UNKNOWN, NOT_NEEDED = "OK", "MISSING", "UNKNOWN", "NOT NEEDED"
AGREE, DISAGREE, NOT_CHECKED, BY_HAND = "AGREE", "DISAGREE", "NOT CHECKED", "BY HAND"
POLL_SECONDS = 20          # one store read a poll; well inside sudo's five-minute session
CONFIRM_SECONDS = 120      # how long the runner may take to say it took a switch
QUIET_SECONDS = 180        # after the switch off: nothing of the lane may start
STORE_MISSES = 3           # store reads that fail in a row before it is said
ROLE_PYTHON = "/usr/bin/python3"
TIME_SLACK = 2             # seconds: a record's time is whole seconds, a tracker's has milliseconds
WRITER_MOVE_BEFORE = 600   # the runner moves the ticket to its working state just before a code run starts
GITHUB_TOKENISH = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?\Z")
GITHUB_ANY = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?\Z")

# Each lane: the runner's kind, the job that asks for it, how, its time limits (minutes), the steps the
# runner's own part takes (the lane stays on until they are seen), the steps after the switch off, and the
# one thing the owner does once the lane is on.
SPEC = {
    "code": {"kind": "code", "caller": "queue", "action": "start", "minutes": 120, "after": 90,
             "chain": ("asked", "started", "pin", "pull request", "ended"), "after_chain": ("reviewed",),
             "owner": "Move %s into its approved state (Todo) yourself, in the tracker, now. Its start is the "
                      "one this run watches: the queue asks the runner, and the session opens a pull request."},
    "fix": {"kind": "fix", "caller": "fix-driver", "action": "resume", "minutes": 90, "after": 0,
            "chain": ("asked", "started", "pushed", "ended"), "after_chain": (),
            "owner": "Nothing to do now: Stage E's bounce driver sends the review's findings on %s's pull "
                     "request to its own session, as a resume, on its next pass. The review must have findings "
                     "at or above the threshold."},
    "review": {"kind": "review", "caller": "review", "action": "start", "minutes": 90, "after": 0,
               "chain": ("asked", "started", "ended", "reviewed"), "after_chain": (),
               "owner": "Nothing to do now: the review job asks for a review of %s's pull request on its next "
                        "pass. It must not be reviewed at its head already, or a re-review must be asked for."},
    "plan": {"kind": "plan", "caller": "planner", "action": "start", "minutes": 60, "after": 0,
             "chain": ("asked", "started", "ended"), "after_chain": (),
             "owner": "Move the idea %s to Plan it yourself, in the tracker, now."},
    "acceptance": {"kind": "acceptance", "caller": "acceptance", "action": "start", "minutes": 90, "after": 0,
                   "chain": ("asked", "started", "ended", "verdict"), "after_chain": (),
                   "owner": "Nothing to do now: the acceptance job asks for %s's check on its next pass, once "
                            "its plan's last pull request is ready."},
}
LANES = ("code", "fix", "review", "plan", "acceptance")
PUSH_LANES = ("code", "fix")      # the kinds that hold the push token (the session kinds' own rule)
WRITER_LANES = tuple(k for k in LANES if k in session_tools.STAGES)   # the kinds given the five ticket tools
BRIEF = "docs/SESSION-BRIEF.md"   # the project's session brief, which must name those tools
DRY_RUN_REF = "refs/heads/runner-live-dry-run-"   # a branch name no push ever creates: the push is a dry run


class Missing(Exception):
    """A prerequisite that is not there. Its words say what to do."""


class Unknown(Exception):
    """Something that could not be read. Never taken as OK."""


class Interrupted(Exception):
    """Ctrl-C, a closed terminal or a stop: the lane is switched off."""


# --------------------------------------------------------------------------- #
# Everything it touches outside itself, so the selftest can stand in for each
# --------------------------------------------------------------------------- #
class Res(object):
    def __init__(self, rc, out="", err=""):
        self.rc, self.out, self.err = rc, out or "", err or ""

    ok = property(lambda self: self.rc == 0)


class IO(object):
    def env(self):
        return os.environ

    def uid(self):
        return os.geteuid()

    def user(self):
        return ns.invoking_user()

    def say(self, text=""):
        print(text)
        sys.stdout.flush()

    def ask(self, question):
        try:
            return input(question)
        except EOFError:
            return ""

    def sudo_validate(self):
        """`sudo -v`: its password prompt on your terminal, nothing captured."""
        try:
            return subprocess.run(["sudo", "-v"]).returncode
        except OSError:
            return 127

    def run(self, argv, stdin=None, timeout=120):
        """One command, from `/`: the role account cannot stand in your folder."""
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout, cwd="/",
                               universal_newlines=True)
        except subprocess.TimeoutExpired:
            return Res(124, "", "timed out after %d seconds" % timeout)
        except OSError as exc:
            return Res(127, "", type(exc).__name__)
        return Res(p.returncode, p.stdout, p.stderr)

    def now(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)

    def role_home(self, account):
        import pwd
        return pwd.getpwnam(account).pw_dir

    def role_scripts(self, clone):
        """Where the role account's programs import the kit from: the clone the runner runs from."""
        return [clone + "/scripts"]

    def read_text(self, path):
        """A file anyone may read (the runner's job file, root's, mode 0644), or None."""
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(1 << 20)
        except (OSError, UnicodeDecodeError):
            return None


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def epoch(text):
    """A store, GitHub or tracker time (UTC, whole seconds or with a fraction) as whole seconds, or None."""
    if not isinstance(text, str):
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d{1,9})?Z\Z", text)
    try:
        return calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) if m else None
    except ValueError:
        return None


def hms(seconds):
    return time.strftime("%H:%M:%S", time.gmtime(seconds)) if seconds is not None else "-"


def span(seconds):
    if seconds is None:
        return "-"
    seconds = int(seconds)
    return "%dm %02ds" % (seconds // 60, seconds % 60) if seconds >= 60 else "%ds" % seconds


def short(text, n=12):
    return (text or "")[:n]


def as_role(ctx, script, stdin=None, timeout=120):
    """One shell command as the role account, never asking: `sudo -n`, from `/`."""
    return ctx.io.run(["sudo", "-n", "-u", ctx.role, "-H", "/bin/sh", "-c", "cd / && " + script],
                      stdin, timeout)


def role_python(ctx, program, args, timeout=180):
    """A program (on stdin) run as the role account with Apple's Python, isolated, its arguments one JSON
    document. Returns the one JSON document it prints, or raises Unknown."""
    res = as_role(ctx, "%s -I - %s" % (ROLE_PYTHON, shlex.quote(json.dumps(args, sort_keys=True))),
                  stdin=program, timeout=timeout)
    try:
        doc = json.loads((res.out or "").strip().splitlines()[-1]) if res.ok else None
    except (ValueError, IndexError):
        doc = None
    if not isinstance(doc, dict):
        # Never the child's words: a traceback can quote what it read. Only the class of what it raised.
        last = (res.err.strip().splitlines() or [""])[-1]
        cls = re.match(r"^([A-Za-z_][A-Za-z0-9_.]{0,80})(?::|\Z)", last)
        raise Unknown("a read as %s gave no answer (exit %d%s)" % (ctx.role, res.rc, ", %s" % cls.group(1)
                                                                    if cls and not res.ok else ""))
    return doc


def read_role_file(ctx, path):
    """(text, mode) as the role account holds the file; text None when it is absent."""
    res = as_role(ctx, ns.CONFIG_READ_SH.replace("@F@", shlex.quote(path)))
    if res.rc == 9:
        return None, ""
    if not res.ok:
        raise Unknown("%s could not be read as %s (exit %d)" % (path, ctx.role, res.rc))
    first, _sep, rest = res.out.partition("\n")
    return rest, first.split("=", 1)[1] if first.startswith("mode=") else ""


def under_home(ctx, path):
    return qs.with_home(path, ctx.home) if isinstance(path, str) else path


# --------------------------------------------------------------------------- #
# The role-account readers. Each is a fixed program; only fields come back.
# --------------------------------------------------------------------------- #
# The store, read through the store's own module from the kit clone the runner runs from: the lanes the
# runner says it takes, one caller's requests for one kind (their text left out), the project's runs, the pin
# of a live run of the ticket (its budget, mode and criteria counted, never its words), the final message of
# an ended run (its size and error flag, never its words), and a few files a lane's ledger lives in.
SNAPSHOT_PY = r'''
import json, os, sys
a = json.loads(sys.argv[1])
sys.path[:0] = a["scripts"]
import pipeline_runner_store as store
out = {"schema": "runner-live-snapshot/1"}
sd = a["store"]
try:
    out["lanes"], out["lanes_state"] = store.read_lanes(sd), "read"
except store.StoreError as exc:
    out["lanes"], out["lanes_state"] = None, "could not be read: %s" % exc
if a.get("lanes_only"):
    print(json.dumps(out, sort_keys=True))
    raise SystemExit(0)
if a.get("caller"):
    try:
        got = store.asked(sd, a["caller"], a["kind"], a.get("since"))
        rows = []
        for r in got["asked"]:
            q = r["request"]
            if q["project"] == a["project"]:
                row = dict((k, q.get(k)) for k in ("id", "at", "caller", "action", "kind", "project", "ticket", "run"))
                row["answer"] = r["answer"]
                rows.append(row)
        out["asked"] = {"state": "read", "rows": rows, "unread": got["unread"]}
    except (store.StoreError, ValueError) as exc:
        out["asked"] = {"state": "could not be read: %s" % exc}
got = store.read_runs(sd)
runs = None if got["runs"] is None else [r for r in got["runs"] if r["project"] == a["project"]]
out["runs"] = {"state": got["state"], "why": got["why"], "rows": runs}
pins, outputs = {}, {}
for r in runs or []:
    if r["ticket"] != a.get("ticket"):
        continue
    if r["state"] in store.LIVE and r["pin"]:
        try:
            doc = store.read_json(r["pin"])
            b, t = doc.get("budget") or {}, doc.get("ticket") or {}
            n = lambda v: len(v) if isinstance(v, list) else None
            pins[r["run"]] = {"state": "read", "dispatch_id": doc.get("dispatch_id"),
                              "session_mode": doc.get("session_mode"), "branch": doc.get("branch"),
                              "max_turns": b.get("maxTurns"), "max_usd": b.get("maxUsd"),
                              "max_minutes": b.get("maxMinutes"), "state_id": t.get("state_id"),
                              "criteria": n(t.get("acceptance_criteria")), "out_of_scope": n(t.get("out_of_scope"))}
        except (store.StoreError, AttributeError) as exc:
            pins[r["run"]] = {"state": "could not be read: %s" % exc}
    if r["state"] not in store.LIVE:
        try:
            doc = store.run_output(sd, r["run"])
            outputs[r["run"]] = None if doc is None else {
                "is_error": doc["is_error"], "subtype": doc["subtype"], "cut": doc["cut"],
                "chars": len(doc["text"] or ""), "model": doc["model"]}
        except (store.StoreError, ValueError) as exc:
            outputs[r["run"]] = {"state": "could not be read: %s" % exc}
out["pins"], out["outputs"] = pins, outputs
out["exists"] = dict((p, os.path.lexists(p)) for p in a.get("exists") or [])
files = {}
for p in a.get("json") or []:
    try:
        with open(p, encoding="utf-8") as fh:
            files[p] = {"state": "read", "doc": json.loads(fh.read(8 << 20))}
    except FileNotFoundError:
        files[p] = {"state": "absent"}
    except (OSError, ValueError) as exc:
        files[p] = {"state": "could not be read (%s)" % type(exc).__name__}
for p, want in sorted((a.get("jsonl") or {}).items()):
    try:
        rows = []
        with open(p, encoding="utf-8") as fh:
            for line in fh.read(32 << 20).splitlines():
                row = json.loads(line) if line.strip() else None
                if isinstance(row, dict) and all(row.get(k) == v for k, v in want.items()):
                    rows.append(row)
        files[p] = {"state": "read", "rows": rows}
    except FileNotFoundError:
        files[p] = {"state": "absent"}
    except (OSError, ValueError) as exc:
        files[p] = {"state": "could not be read (%s)" % type(exc).__name__}
for p in a.get("records") or []:
    try:
        names = sorted(x for x in os.listdir(p) if x.endswith(".json"))[:50]
        docs = []
        for x in names:
            with open(os.path.join(p, x), encoding="utf-8") as fh:
                d = json.loads(fh.read(1 << 20))
            docs.append({"name": x, "plan": d.get("plan") if isinstance(d, dict) else None,
                         "schema": d.get("schema") if isinstance(d, dict) else None})
        files[p] = {"state": "read", "records": docs}
    except FileNotFoundError:
        files[p] = {"state": "absent"}
    except (OSError, ValueError) as exc:
        files[p] = {"state": "could not be read (%s)" % type(exc).__name__}
if a.get("ledger"):
    import pipeline_acceptance_poller as poller
    try:
        rows = poller.load_ledger(a["ledger"])
        files[a["ledger"]] = {"state": "read", "rows": [dict(r, key="%s@%s" % k) for k, r in rows.items()
                                                        if k[0] == a.get("plan")]}
    except poller.LedgerError as exc:
        files[a["ledger"]] = {"state": "could not be read: %s" % exc}
out["files"] = files
print(json.dumps(out, sort_keys=True))
'''

# The tracker, read by the runner's own reader, as the role account, with the runner's own key file. Only
# fields come back: the ticket's ids, state, creator, labels (names) and whole history (who moved it, from
# and to which state, which labels were added), and from an epic's comments the acceptance verdict marks, read
# by the acceptance poller's own reader, with each one's author.
TRACKER_PY = r'''
import json, sys
a = json.loads(sys.argv[1])
sys.path[:0] = a["scripts"]
import pipeline_runner as r
out = {"schema": "runner-live-tracker/1", "tickets": {}, "marks": {}}
cfg, bad = r.load_file(a["settings"])
if bad:
    out["state"] = "could not read: the runner's own settings do not load (%d problem(s))" % len(bad)
    print(json.dumps(out, sort_keys=True))
    raise SystemExit(0)
tr = r.LinearTracker(cfg["linear_key_file"])
dig = lambda o, *p: o if not p else dig(o.get(p[0]) if isinstance(o, dict) else None, *p[1:])
for t in a["tickets"]:
    try:
        node, raw = tr.issue(t), tr.history(t)
    except r.TrackerError as exc:
        out["tickets"][t] = {"state": "could not read: %s" % str(exc)[:200]}
        continue
    hist = None
    if isinstance(raw, list) and all(isinstance(e, dict) for e in raw):
        hist = [{"at": e.get("createdAt"), "actor": e.get("actorId"), "from": e.get("fromStateId"),
                 "to": e.get("toStateId"), "added": e.get("addedLabelIds") or []} for e in raw]
    out["tickets"][t] = {"state": "read", "identifier": node.get("identifier"), "id": node.get("id"),
                         "state_id": dig(node, "state", "id"), "creator": dig(node, "creator", "id"),
                         "created_at": node.get("createdAt"), "parent": dig(node, "parent", "identifier"),
                         "labels": sorted(str(n.get("name")) for n in (dig(node, "labels", "nodes") or [])
                                          if isinstance(n, dict)),
                         "label_ids": sorted(str(n.get("id")) for n in (dig(node, "labels", "nodes") or [])
                                             if isinstance(n, dict)),
                         "history": hist}
Q = "query LiveRunMarks($id: String!) { issue(id: $id) { comments(first: 100) { nodes { createdAt body user { id } } } } }"
for epic in a.get("marks_of") or []:
    import pipeline_acceptance_poller as poller
    try:
        nodes = dig(tr.gql(Q, {"id": epic}), "issue", "comments", "nodes")
    except Exception as exc:
        out["marks"][epic] = {"state": "could not read (%s)" % type(exc).__name__}
        continue
    marks = []
    for n in nodes or []:
        m = poller.read_verdict_mark(n.get("body") if isinstance(n, dict) else None)
        if m:
            marks.append(dict(m, at=n.get("createdAt"), author=dig(n, "user", "id")))
    out["marks"][epic] = {"state": "read" if isinstance(nodes, list) else "could not read", "marks": marks}
if a.get("writer"):
    st, key = cfg.get("session_tools") or {}, None
    try:
        key = r.tl_tools.read_key(st["key_file"])
        who = dig(r.status_job.linear_graphql(r.Q_VIEWER, {}, key), "viewer", "id")
        out["writer"] = {"state": "read", "id": who if isinstance(who, str) else None}
    except Exception as exc:
        out["writer"] = {"state": "could not read (%s)" % type(exc).__name__}
    key = None
W = ("query LiveRunWaiting($team: String!, $states: [ID!]) { issues(first: 50, filter: "
     "{ team: { key: { eq: $team } }, state: { id: { in: $states } } }) { nodes { identifier } } }")
if a.get("waiting"):
    try:
        nodes = dig(tr.gql(W, a["waiting"]), "issues", "nodes")
        out["waiting"] = {"state": "read" if isinstance(nodes, list) else "could not read",
                          "tickets": sorted(str(n.get("identifier")) for n in nodes or [] if isinstance(n, dict))}
    except Exception as exc:
        out["waiting"] = {"state": "could not read (%s)" % type(exc).__name__}
out["state"] = "read"
print(json.dumps(out, sort_keys=True))
'''

# Whether the folder the runner's job runs from could be changed by the role account: the runner's own guard
# launcher's judgement, as the role account, from that folder (KIT-919 makes it a root-owned copy).
ENGINE_PY = r'''# live-run:engine
import json, sys
a = json.loads(sys.argv[1])
sys.path.insert(0, a["engine"] + "/scripts")
try:
    import pipeline_runner_guard as g
    out = {"state": "read", "problems": g.engine_problems(a["engine"])}
except Exception as exc:
    out = {"state": "could not read (%s)" % type(exc).__name__}
print(json.dumps(out))
'''

# Shell looks, as the role account. Each prints fixed words; none prints a file's content.
GIT_KIT_SH = ('# live-run:git-kit\nc=@C@; h=$(git -C "$c" rev-parse --verify HEAD 2>/dev/null) || h=none; '
              'm=$(git -C "$c" ls-remote origin refs/heads/main 2>/dev/null | cut -f1) || m=; '
              'k=$(git -C "$c" log -1 --format=%H HEAD -- session-kinds 2>/dev/null) || k=; '
              'echo "head $h"; echo "main ${m:-none}"; echo "kinds ${k:-none}"')
GIT_PROJECT_SH = ('# live-run:git-project\nr=@R@; b=@B@; [ -d "$r" ] || { echo "absent"; exit 0; }; '
                  'u=$(git -C "$r" config --get remote.origin.url 2>/dev/null) || u=; '
                  'p=$(git -C "$r" remote get-url --push origin 2>/dev/null) || p=; '
                  'm=$(git -C "$r" ls-remote origin "refs/heads/$b" 2>/dev/null | cut -f1) || m=; '
                  'l=$(git -C "$r" rev-parse --verify "refs/remotes/origin/$b" 2>/dev/null) || l=; '
                  'echo "url ${u:-none}"; echo "push ${p:-none}"; echo "remote ${m:-none}"; echo "local ${l:-none}"')
KEY_SH = ('# live-run:key\nf=@F@; [ -e "$f" ] || { echo "absent"; exit 0; }; '
          '{ [ -f "$f" ] && [ ! -L "$f" ]; } || { echo "not-a-file"; exit 0; }; '
          'case "$(uname)" in Darwin) stat -f "stat %Lp %Su %z" "$f";; *) stat -c "stat %a %U %s" "$f";; esac')
# A dry-run push with the push token, handed to git exactly as a session gets it: every stored helper cleared,
# then the adapter's one helper for github.com, which reads the token from git's own environment. Nothing is
# pushed, no repository hook runs, and nothing git says is printed: only its exit.
PUSH_SH = ('# live-run:push\nr=@R@; f=@F@; [ -d "$r" ] && [ -f "$f" ] || { echo "push missing"; exit 0; }; '
           '@ENV@=$(cat "$f") || { echo "push unread"; exit 0; }; export @ENV@; '
           'GIT_TERMINAL_PROMPT=0 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=@K0@ '
           'GIT_CONFIG_VALUE_0=@V0@ GIT_CONFIG_KEY_1=@K1@ GIT_CONFIG_VALUE_1=@V1@ '
           'git -C "$r" -c core.hooksPath=/dev/null push --dry-run --quiet origin @SRC@:@DST@ >/dev/null 2>&1; '
           'echo "push $?"')
ANCESTOR_SH = ('# live-run:ancestor\nc=@C@; git -C "$c" merge-base --is-ancestor @K@ @P@ 2>/dev/null; '
               'r=$?; echo "ancestor $r"')


# --------------------------------------------------------------------------- #
# Where everything is
# --------------------------------------------------------------------------- #
class Ctx(object):
    """One live run: its arguments, the confs, and what it saw, in order."""

    def __init__(self, io, args):
        self.io, self.args = io, args
        self.lane, self.project, self.ticket = args.lane, args.project, getattr(args, "ticket", None)
        self.spec = SPEC[self.lane]
        self.kind = self.spec["kind"]
        self.conf = self.role = self.home = self.settings_path = self.clone = None
        self.settings = self.cfg = self.proj = self.store_dir = self.slug = None
        self.role_scripts, self.https = None, False
        self.rows = []                 # (name, state, words) of the check
        self.steps = dict((s, None) for s in self.spec["chain"] + self.spec["after_chain"])
        self.agree = []                # (name, result, words)
        self.problems = []             # what did not work, in words
        self.notes = []
        self.on_at = self.on_seen = self.off_at = self.off_seen = None
        self.request = self.run = self.pr = self.pin = self.code_run = None
        self.before = {}               # what it read before the switch (a head, labels)
        self.tracker_doc = None
        self.started_after = []
        self.refused_after = []
        self.misses = 0

    def say(self, text=""):
        self.io.say(text)

    def store_work(self):
        return under_home(self, (self.settings or {}).get("work_dir") or "/nowhere")

    def problem(self, text):
        if text not in self.problems:
            self.problems.append(text)


def load(ctx):
    """The confs and the installed settings, or raise Missing / Unknown / SetupError."""
    a = ctx.args
    conf, errors = setup.load_conf(a.conf, ctx.io.user())
    if errors:
        raise SetupError("%s has %d problem(s), the first: %s" % (a.conf, len(errors), errors[0]))
    ctx.conf, ctx.role = conf, conf["ROLE_ACCOUNT"]
    try:
        ctx.home = ctx.io.role_home(ctx.role)
    except KeyError:
        raise SetupError("the role account %s does not exist on this Mac" % ctx.role)
    ctx.settings_path = under_home(ctx, setup.settings_path(conf))
    ctx.clone = under_home(ctx, conf["ROLE_KIT_CLONE"].rstrip("/"))
    ctx.role_scripts = ctx.io.role_scripts(ctx.clone)
    text, _mode = read_role_file(ctx, ctx.settings_path)
    if text is None:
        raise Missing("no runner settings are installed at %s: run the runner's installer first" % ctx.settings_path)
    try:
        ctx.settings = json.loads(text)
    except ValueError:
        raise Missing("%s is not JSON, so nothing can be switched in it" % ctx.settings_path)
    ctx.cfg, bad = runner_job.load_config(qs.with_home(ctx.settings, ctx.home))
    if not isinstance(ctx.settings, dict) or not isinstance(ctx.settings.get("projects"), dict) \
            or ctx.project not in ctx.settings["projects"]:
        raise Missing("the runner's settings have no project %s: add it to PROJECTS in runner.conf and run "
                      "the installer" % ctx.project)
    if bad:
        ctx.notes.append("the installed settings do not load as they are: %s" % bad[0])
    ctx.proj = (ctx.cfg or {}).get("projects", {}).get(ctx.project) or {}
    ctx.store_dir = under_home(ctx, ctx.settings.get("store_dir"))


# --------------------------------------------------------------------------- #
# 1. The prerequisites
# --------------------------------------------------------------------------- #
def row_runner(ctx):
    """The runner installed, current and switched on, and its job loaded: its own installer's `check`."""
    res = ctx.io.run([sys.executable, os.path.join(HERE, "pipeline_runner_setup.py"), "check", "--conf",
                      os.path.abspath(ctx.args.conf)], timeout=600)
    try:
        doc = json.loads(res.out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise Unknown("the runner's installer check gave no rows (exit %d)" % res.rc)
    if not (isinstance(doc, dict) and doc.get("schema") == setup.ROWS_SCHEMA and isinstance(doc.get("rows"), list)):
        raise Unknown("the runner's installer check gave no rows (exit %d)" % res.rc)
    rows = dict((r.get("step"), r) for r in doc["rows"] if isinstance(r, dict))
    if not rows:
        raise Unknown("the runner's installer check could not run: %s" % (doc.get("why") or "no reason"))
    for step in ("home", "kit", "settings", "job"):
        r = rows.get(step)
        if r is None or r.get("outcome") == setup.UNKNOWN:
            raise Unknown("its installer's `%s` row: %s" % (step, (r or {}).get("detail") or "not reported"))
        if r.get("outcome") != setup.ALREADY_DONE:
            raise Missing("its installer's `%s` row is %s: %s. Run the runner's installer" % (
                step, r.get("outcome"), short(r.get("detail"), 240)))
    if doc.get("left_off"):
        raise Missing("the runner is switched off (%s). Set enabled to true in %s" % (
            short(doc["left_off"], 200), ctx.settings_path))
    return "installed, current, switched on, and its job loaded"


def git_lines(res):
    return dict(ln.split(" ", 1) for ln in res.out.splitlines() if " " in ln) if res.ok else None


def row_kit_clone(ctx):
    """The kit clone the runner runs from, at main's head."""
    res = as_role(ctx, GIT_KIT_SH.replace("@C@", shlex.quote(ctx.clone)))
    got = git_lines(res)
    if not got or got.get("head", "none") == "none":
        raise Unknown("the kit clone %s could not be read as %s (exit %d)" % (ctx.clone, ctx.role, res.rc))
    if got.get("main", "none") == "none":
        raise Unknown("main's head could not be read from the kit clone's origin, as %s" % ctx.role)
    ctx.before["kit_head"], ctx.before["kinds_commit"] = got["head"], got.get("kinds")
    if got["head"] != got["main"]:
        raise Missing("the kit clone %s is at %s and main is at %s: let Stage E's run move it, then check again"
                      % (ctx.clone, short(got["head"]), short(got["main"])))
    return "at main's head, %s" % short(got["head"])


def row_project_clone(ctx):
    """The project's own clone: an HTTPS origin for fetch and push, which the role account's git reaches."""
    repo, base = ctx.proj.get("repo"), ctx.proj.get("base") or "main"
    if not repo:
        raise Missing("project %s names no clone" % ctx.project)
    res = as_role(ctx, GIT_PROJECT_SH.replace("@R@", shlex.quote(repo)).replace("@B@", shlex.quote(base)))
    if res.ok and res.out.strip() == "absent":
        raise Missing("the project's clone %s is not there" % repo)
    got = git_lines(res)
    if not got:
        raise Unknown("the project's clone %s could not be read as %s (exit %d)" % (repo, ctx.role, res.rc))
    url, push = got.get("url", "none"), got.get("push", "none")
    m, anyway = GITHUB_TOKENISH.match(url), GITHUB_ANY.match(url)
    if anyway:                          # the repository is known either way, so GitHub's rows can still be read
        ctx.slug = "%s/%s" % (anyway.group(1), anyway.group(2))
    if not (m and GITHUB_TOKENISH.match(push)):
        raise Missing("the project's clone %s fetches or pushes over %s, not HTTPS. A session's git runs in "
                      "Claude Code's sandbox, where SSH cannot connect: as %s, git -C %s remote set-url origin "
                      "https://github.com/%s.git" % (repo, url if not m else push, ctx.role, repo,
                                                     ctx.slug or "<owner>/<repo>"))
    ctx.https = True
    if got.get("remote", "none") == "none":
        raise Missing("the role account's git cannot reach %s's origin (branch %s): every start would wait, "
                      "since the runner fetches before each one" % (repo, base))
    behind = "" if got.get("local") == got.get("remote") else " (its origin/%s is behind; the runner fetches " \
        "it at each start)" % base
    return "%s, HTTPS, reached as %s%s" % (ctx.slug, ctx.role, behind)


def row_queue_store(ctx):
    """RUNNER_STORE in queue.conf names the runner's store, and the installed usage job reads it."""
    path = ctx.args.queue_conf
    try:
        with open(path, encoding="utf-8") as fh:
            values, errors = qs.parse_conf(fh.read(), os.path.basename(path))
    except OSError as exc:
        raise Unknown("%s could not be read (%s)" % (path, type(exc).__name__))
    if errors:
        raise Unknown("%s has %d problem(s), the first: %s" % (path, len(errors), errors[0]))
    ctx.before["queue_conf"] = values
    want = os.path.realpath(ctx.store_dir or "/nowhere")
    named = values.get("RUNNER_STORE", "")
    if not named or os.path.realpath(under_home(ctx, named)) != want:
        raise Missing("RUNNER_STORE in %s %s, not the runner's store %s: set it, then run the queue's installer, "
                      "so the runner's runs count against the day and the week" % (
                          path, "is empty" if not named else "names %s" % named, ctx.settings.get("store_dir")))
    usage = under_home(ctx, values.get("USAGE_CONFIG") or qs.CONF_DEFAULTS["USAGE_CONFIG"])
    text, _mode = read_role_file(ctx, usage)
    try:
        doc = json.loads(text) if text is not None else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        raise Missing("the installed usage job's settings %s are %s: run the queue's installer" % (
            usage, "absent" if text is None else "not JSON"))
    got = doc.get("runner_store") or ""
    if not got or os.path.realpath(under_home(ctx, got)) != want:
        raise Missing("the installed usage job does not read the runner's store yet (%s): run the queue's "
                      "installer" % usage)
    return "queue.conf and the installed usage job name %s" % ctx.settings.get("store_dir")


def key_files(ctx):
    """[(what, path, needed)]: every key file the settings name, and the ones this lane cannot run without."""
    cfg = ctx.cfg or {}
    st = cfg.get("session_tools") or {}
    out = [("the runner's tracker key", cfg.get("linear_key_file"), True)]
    if st.get("key_file") or ctx.lane in WRITER_LANES:
        out.append(("the session writer's key", st.get("key_file"), ctx.lane in WRITER_LANES))
    if cfg.get("push_token_file") or ctx.lane in PUSH_LANES:
        out.append(("the push token", cfg.get("push_token_file"), ctx.lane in PUSH_LANES))
    return out


def row_keys(ctx):
    """Each key file: there, a file, the role account's, mode 0600, and of a key's size. Looked at with `stat`,
    never opened."""
    said = []
    for what, path, needed in key_files(ctx):
        if not path:
            if needed:
                raise Missing("%s's file is not set in %s: a %s session cannot run without it" % (
                    what, ctx.settings_path, ctx.kind))
            continue
        res = as_role(ctx, KEY_SH.replace("@F@", shlex.quote(path)))
        word = res.out.strip() if res.ok else ""
        if word in ("absent", "not-a-file"):
            raise Missing("%s's file %s is %s" % (what, path, "not there" if word == "absent" else
                                                   "a link or not a file"))
        m = re.match(r"^stat ([0-7]{3,4}) (\S+) (\d+)\Z", word)
        if not m:
            raise Unknown("%s's file %s could not be looked at as %s (exit %d)" % (what, path, ctx.role, res.rc))
        mode, owner, size = m.group(1)[-3:], m.group(2), int(m.group(3))
        if owner != ctx.role:
            raise Missing("%s's file %s belongs to %s, not %s" % (what, path, owner, ctx.role))
        if mode != "600":
            raise Missing("%s's file %s is mode %s: chmod 600 it, as %s" % (what, path, mode, ctx.role))
        if not 0 < size <= runner_job.PUSH_TOKEN_MAX:
            raise Missing("%s's file %s is %s" % (what, path, "empty" if not size else "too large to hold one key"))
        said.append(what)
    return "%s: each the role account's, mode 0600 (none read)" % ", ".join(said)


def row_push(ctx):
    """The push token can push to the project: a dry run, as the role account, as a session's git would push."""
    if ctx.lane not in PUSH_LANES:
        raise NotNeededRow("a %s session does not push" % ctx.kind)
    path, repo = (ctx.cfg or {}).get("push_token_file"), ctx.proj.get("repo")
    if not (path and repo and ctx.slug and ctx.https):
        raise Unknown("waits on the keys row, and on the project clone row's HTTPS origin")
    (k0, v0), (k1, v1) = harness.GIT_PUSH_CONFIG
    script = PUSH_SH
    for mark, value in (("@R@", repo), ("@F@", path), ("@K0@", k0), ("@V0@", v0), ("@K1@", k1), ("@V1@", v1),
                        ("@SRC@", "refs/remotes/origin/%s" % (ctx.proj.get("base") or "main")),
                        ("@DST@", DRY_RUN_REF + "%08x" % int(ctx.io.now()))):
        script = script.replace(mark, shlex.quote(value))
    res = as_role(ctx, script.replace("@ENV@", harness.PUSH_TOKEN_ENV), timeout=180)
    m = re.search(r"^push (\S+)$", res.out or "", re.M) if res.ok else None
    if not m:
        raise Unknown("the dry-run push could not be made as %s (exit %d)" % (ctx.role, res.rc))
    if m.group(1) != "0":
        raise Missing("the push token cannot push to %s: %s" % (ctx.slug, {
            "missing": "the clone or the token's file is not there", "unread": "its file could not be read"}.get(
            m.group(1), "git's dry run was refused (exit %s). Give the token contents and pull requests on %s"
                        % (m.group(1), ctx.slug))))
    return "a dry-run push to %s with it succeeds; nothing was pushed" % ctx.slug


def row_writer_key(ctx):
    """The session writer's key answers as `writer_user_id`: the session tools refuse everything otherwise."""
    if ctx.lane not in WRITER_LANES:
        raise NotNeededRow("a %s session gets no ticket tools" % ctx.kind)
    st = (ctx.cfg or {}).get("session_tools")
    if not st:
        raise Missing("session_tools is not set in %s: a %s session reaches its ticket only through its tools"
                      % (ctx.settings_path, ctx.kind))
    got = tracker(ctx, [], writer=True).get("writer") or {}
    if got.get("state") != "read":
        raise Unknown("the session writer's key could not be asked who it is: %s" % (got.get("state") or "no answer"))
    if got.get("id") != st["writer_user_id"]:
        raise Missing("the session writer's key answers as %s, not as writer_user_id %s" % (
            got.get("id") or "nobody", st["writer_user_id"]))
    return "it answers as %s, the session writer" % got["id"]


def row_engine(ctx):
    """The folder the runner's job runs from: the role account can change none of it, or the runner holds every
    start (docs/RUNNER.md, "Guards from the engine's own folder"). The root-owned copy is KIT-919's."""
    plist = _dispatcher_plist(ctx.conf["JOB_LABEL"])
    text = ctx.io.read_text(plist)
    try:
        doc = plistlib.loads(text.encode("utf-8")) if text is not None else None
        args = " ".join(str(a) for a in doc["ProgramArguments"])
        home = (doc.get("EnvironmentVariables") or {}).get("HOME") or ctx.home
    except (ValueError, TypeError, KeyError, AttributeError, plistlib.InvalidFileException):
        raise Unknown("the runner's job file %s could not be read" % plist)
    m = re.search(r'"([^"]+)/scripts/pipeline_runner\.py"', args) or re.search(r"(\S+)/scripts/pipeline_runner\.py",
                                                                                 args)
    if not m:
        raise Unknown("the runner's job file %s names no runner script" % plist)
    engine = m.group(1).replace("${HOME}", home).replace("$HOME", home)
    got = role_python(ctx, ENGINE_PY, {"engine": engine})
    if got.get("state") != "read":
        raise Unknown("whether %s could change %s %s" % (ctx.role, engine, got.get("state")))
    if got.get("problems"):
        raise Missing("the runner runs from %s, which %s could change (%s): the root-owned copy of the kit (KIT-919) "
                      "is not in place, and the runner holds every start until it is" % (
                          engine, ctx.role, short(str(got["problems"][0]), 160)))
    return "%s: the role account can change none of it" % engine


def gh_file(ctx, path):
    """A file of the project on its base branch, as GitHub has it now, or None when it is not there."""
    if not ctx.slug:
        raise Unknown("waits on the project clone row")
    base = ctx.proj.get("base") or "main"
    res = ctx.io.run(["gh", "api", "repos/%s/contents/%s?ref=%s" % (ctx.slug, path, base)], timeout=120)
    if not res.ok:
        if "404" in res.err or "Not Found" in res.err:
            return None
        raise Unknown("GitHub could not be read with gh for %s's %s" % (ctx.slug, path))
    try:
        return base64.b64decode(json.loads(res.out)["content"]).decode("utf-8")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise Unknown("GitHub's answer for %s's %s is not a file" % (ctx.slug, path))


def row_delivery(ctx):
    """The project's delivery.json on its base branch passes the kit's own validator."""
    base = ctx.proj.get("base") or "main"
    text = gh_file(ctx, "delivery.json")
    if text is None:
        raise Missing("%s has no delivery.json on %s: the runner refuses every start of such a project" % (
            ctx.slug, base))
    try:
        ctx.before["delivery"] = json.loads(text)
    except ValueError:
        raise Missing("%s's delivery.json on %s is not JSON" % (ctx.slug, base))
    with tempfile.TemporaryDirectory(prefix="runner-live-") as tmp:
        path = os.path.join(tmp, "delivery.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        res = ctx.io.run([sys.executable, os.path.join(HERE, "check_delivery_config.py"), "--json", "--config", path,
                          "--repo-root", KIT], timeout=300)
    try:
        got = json.loads(res.out)
        ok, errors = got["ok"], got.get("errors") or []
    except (ValueError, KeyError, TypeError):
        raise Unknown("the kit's validator gave no verdict on %s's delivery.json (exit %d)" % (ctx.slug, res.rc))
    if not ok:
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        raise Missing("%s's delivery.json on %s fails the kit's validator: %d error(s), the first [%s] %s" % (
            ctx.slug, base, len(errors), first.get("rule"), short(first.get("message"), 160)))
    return "%s's delivery.json on %s passes the kit's validator" % (ctx.slug, base)


def row_brief(ctx):
    """The project's session brief names the five ticket tools, the only way such a session reaches its ticket."""
    if ctx.lane not in WRITER_LANES:
        raise NotNeededRow("a %s session gets no ticket tools" % ctx.kind)
    text = gh_file(ctx, BRIEF)
    if text is None:
        raise Missing("%s has no %s on %s" % (ctx.slug, BRIEF, ctx.proj.get("base") or "main"))
    missing = [t for t in session_tools.TOOL_NAMES if "`%s`" % t not in text]
    if missing:
        raise Missing("%s's %s does not name the ticket tools %s: port the kit's brief" % (
            ctx.slug, BRIEF, ", ".join(missing)))
    return "%s names all five ticket tools" % BRIEF


def row_window(ctx):
    """Now is inside the queue's working window, as the queue judges it."""
    path = (ctx.cfg or {}).get("queue_config") or under_home(
        ctx, (ctx.before.get("queue_conf") or {}).get("QUEUE_CONFIG") or qs.CONF_DEFAULTS["QUEUE_CONFIG"])
    text, _mode = read_role_file(ctx, path)
    try:
        raw = json.loads(text).get("window") if text is not None else None
    except (ValueError, AttributeError):
        raw = None
    if raw is None:
        raise Unknown("the queue's installed settings %s %s, so its window is not known" % (
            path, "are absent" if text is None else "hold no window"))
    cfg, errors = window.config(raw)
    if errors:
        raise Unknown("the queue's window in %s does not load: %s" % (path, errors[0]))
    w = window.window_at(cfg, ctx.io.now())
    if not w["open"]:
        raise Missing("the working window is closed now (%s); it opens next at %s" % (w["why"], w["next_opens_at"]))
    return "open now, until %s" % w["closes_at"]


def row_todo(ctx):
    """No other approved ticket waits in the project's approved code states: with the lane on, the queue would
    start it on the runner too."""
    if ctx.lane != "code":
        raise NotNeededRow("only the coding lane starts what waits in Todo")
    team = ((ctx.before.get("delivery") or {}).get("linear") or {}).get("teamKey")
    states = (ctx.proj.get("approval_states") or {}).get("code") or []
    if not (team and states):
        raise Unknown("the project's team or its approved code states are not known (the delivery and switch rows)")
    got = tracker(ctx, [], waiting={"team": team, "states": states}).get("waiting") or {}
    if got.get("state") != "read":
        raise Unknown("what waits in %s's approved states could not be read: %s" % (team, got.get("state")))
    others = [t for t in got["tickets"] if t != ctx.ticket]
    if others:
        raise Missing("%d other approved ticket(s) wait in %s (%s): with the lane on, the queue would start them on "
                      "the runner too. Move them out first" % (len(others), team, ", ".join(others[:5])))
    return "no other approved ticket waits in %s" % team


def probe_path(ctx):
    given = ctx.args.probe or (ctx.conf["RUNNER_DIR"].rstrip("/") + "/probe")
    return under_home(ctx, given.rstrip("/")) + "/results.json"


def row_probe(ctx):
    """The role-account probe's last result: every wall PASS, as the role account, at a commit that holds
    main's last change to the session kinds."""
    path = probe_path(ctx)
    text, _mode = read_role_file(ctx, path)
    if text is None:
        raise Missing("no probe results at %s: run the probe as %s with --out %s" % (
            path, ctx.role, os.path.dirname(path)))
    try:
        doc = json.loads(text)
        meta, rows = doc["meta"], doc["rows"]
        walls = [r for r in rows if r.get("class") == "wall"]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Unknown("the probe's results %s are not the probe's" % path)
    if meta.get("account") != ctx.role:
        raise Missing("the probe at %s ran as %s, not as %s" % (path, meta.get("account"), ctx.role))
    failed = [r.get("row") for r in walls if r.get("verdict") != "PASS"]
    if not walls or failed:
        raise Missing("the probe's last result did not pass: %s" % (
            "no wall was measured" if not walls else "%d wall(s) not PASS, the first %s" % (len(failed), failed[0])))
    commit = meta.get("commit")
    if not (isinstance(commit, str) and re.match(r"^[0-9a-f]{40}\Z", commit)):
        raise Missing("the probe's results name no commit: run the probe again from the kit clone (it records "
                      "its commit since KIT-916)")
    kinds = ctx.before.get("kinds_commit")
    if not (isinstance(kinds, str) and re.match(r"^[0-9a-f]{40}\Z", kinds)):
        raise Unknown("main's last change to the session kinds is not known (the kit clone row did not read it)")
    res = as_role(ctx, ANCESTOR_SH.replace("@C@", shlex.quote(ctx.clone)).replace("@K@", kinds).replace("@P@", commit))
    m = re.search(r"^ancestor (\d+)$", res.out or "", re.M) if res.ok else None
    if not m or m.group(1) not in ("0", "1"):
        raise Unknown("whether the probe's commit %s holds the session kinds' %s could not be told" % (
            short(commit), short(kinds)))
    if m.group(1) != "0":
        raise Missing("the probe ran at %s, before main's last change to the session kinds (%s): run it again"
                      % (short(commit), short(kinds)))
    return "%d of %d walls PASS, as %s, at %s (%s)" % (len(walls), len(walls), ctx.role, short(commit),
                                                      meta.get("when") or "time not said")


def row_lane_off(ctx):
    """The lane is off now, in the settings and in what the runner says it takes."""
    flags = store.lane_flags((ctx.settings["projects"].get(ctx.project) or {}).get("lanes"))
    if flags is None:
        raise Missing("project %s's lanes in %s are not true or false for each kind" % (ctx.project, ctx.settings_path))
    if flags[ctx.kind]:
        raise Missing("the %s lane is already on for %s: this run proves a switch from off. Switch it off first:"
                      " %s" % (ctx.kind, ctx.project, off_command(ctx)))
    snap = snapshot(ctx, lanes_only=True)
    if snap["lanes_state"] != "read":
        raise Unknown("what the runner says it takes %s" % snap["lanes_state"])
    if store.lane_on(snap["lanes"], ctx.project, ctx.kind):
        raise Unknown("the settings say off, but the runner still says it takes the %s lane: wait one pass" % ctx.kind)
    return "off in the settings, and the runner takes none of it"


def composed(ctx, on):
    """The installed settings with only this lane changed, and the runner's own loader's problems with them."""
    doc = json.loads(json.dumps(ctx.settings))
    proj = doc["projects"][ctx.project]
    flags = store.lane_flags(proj.get("lanes")) or dict.fromkeys(store.KINDS, False)
    flags[ctx.kind] = bool(on)
    proj["lanes"] = flags
    _cfg, bad = runner_job.load_config(qs.with_home(doc, ctx.home))
    return doc, bad


def row_switch(ctx):
    """With this lane on, the runner's own loader takes the settings: it needs what this lane needs."""
    _doc, bad = composed(ctx, True)
    if bad:
        raise Missing("with the %s lane on, the runner's own loader refuses the settings: %s" % (
            ctx.kind, "; ".join(bad[:3])))
    return "the runner's own loader takes the settings with it on"


def row_ticket(ctx):
    """The ticket is ready for this lane's chain, and the runner's own reader reads it."""
    t = tracker(ctx, [ctx.ticket])
    node = t["tickets"].get(ctx.ticket) or {}
    if node.get("state") != "read":
        raise Unknown("the runner's own reader could not read %s: %s" % (ctx.ticket, node.get("state") or t["state"]))
    ctx.before["labels"], ctx.before["ticket"] = node.get("labels") or [], node
    snap = snapshot(ctx, all_runs=True)
    if snap["runs"]["rows"] is None:
        raise Unknown("the runner's runs could not be read: %s" % snap["runs"]["why"])
    mine = [r for r in snap["runs"]["rows"] if r["ticket"] == ctx.ticket]
    approved = (ctx.proj.get("approval_states") or {})
    if ctx.lane in ("code", "plan"):
        states = approved.get(ctx.kind) or []
        if node.get("state_id") in states:
            raise Missing("%s is in an approved %s state already: move it back out first, so the move this run "
                          "watches is a new one" % (ctx.ticket, ctx.kind))
        live = [r for r in mine if r["kind"] == ctx.kind and r["state"] in store.LIVE]
        if live:
            raise Missing("a %s run of %s is still going (%s)" % (ctx.kind, ctx.ticket, live[-1]["run"]))
        if ctx.lane == "code":
            ws = os.path.join(ctx.store_work(), "code", ctx.ticket)
            if snapshot(ctx, exists=[ws])["exists"].get(ws):
                raise Missing("a code workspace for %s is there already (%s): a code start needs a fresh one" % (
                    ctx.ticket, ws))
        return "%s, read by the runner's own reader; not approved yet" % ctx.ticket
    if ctx.lane in ("fix", "review"):
        code = coded(mine)
        if code is None:
            raise Missing("%s was not coded by a code run of this runner's: the %s lane follows one" % (
                ctx.ticket, ctx.kind))
        ctx.code_run = code
        pr = pr_for_branch(ctx, code["branch"])
        if pr is None:
            raise Missing("no pull request on %s's branch %s" % (ctx.ticket, code["branch"]))
        if pr.get("state") != "OPEN":
            raise Missing("%s's pull request #%s is %s, not open" % (ctx.ticket, pr.get("number"), pr.get("state")))
        ctx.pr, ctx.before["head"] = pr, pr.get("headRefOid")
        return "%s, coded by run %s; pull request #%s, open, at %s" % (
            ctx.ticket, code["run"], pr["number"], short(pr.get("headRefOid")))
    return "%s, read by the runner's own reader" % ctx.ticket


def coded(runs):
    """The ticket's newest code run of this runner's that it started on an approval, and has ended."""
    done = [r for r in runs if r["kind"] == "code" and r["how"] in store.APPROVALS and r["state"] not in store.LIVE]
    return done[-1] if done else None


class NotNeededRow(Exception):
    """A row this lane does not need."""


# In order: a row may read what an earlier one learned (the repository, main's session-kinds commit, the
# project's delivery.json), and says UNKNOWN when that row could not learn it.
ROWS = (("runner", "row_runner"), ("engine", "row_engine"), ("kit clone", "row_kit_clone"),
        ("project clone", "row_project_clone"), ("queue store", "row_queue_store"), ("keys", "row_keys"),
        ("push", "row_push"), ("writer key", "row_writer_key"), ("probe", "row_probe"),
        ("delivery", "row_delivery"), ("brief", "row_brief"), ("window", "row_window"),
        ("lane off", "row_lane_off"), ("switch", "row_switch"), ("todo", "row_todo"), ("ticket", "row_ticket"))


def ready(rows):
    """Only OK and NOT NEEDED are ready: MISSING and UNKNOWN both stop the run."""
    return bool(rows) and all(state in (OK, NOT_NEEDED) for _n, state, _w in rows)


def check(ctx):
    """Every row, read in order; a row that cannot be read is UNKNOWN, never OK. True when all are OK."""
    ctx.rows = []
    for name, fn in ROWS:
        try:
            state, words = OK, globals()[fn](ctx)
        except NotNeededRow as exc:
            state, words = NOT_NEEDED, str(exc)
        except Missing as exc:
            state, words = MISSING, str(exc)
        except Unknown as exc:
            state, words = UNKNOWN, str(exc)
        except Exception as exc:                                  # noqa: BLE001  (a surprise is never OK)
            state, words = UNKNOWN, "unexpected %s: %s" % (type(exc).__name__, short(str(exc), 200))
        ctx.rows.append((name, state, words))
        ctx.say("  %-14s %-10s %s" % (name, state, words))
    return ready(ctx.rows)


# --------------------------------------------------------------------------- #
# The readers the steps share
# --------------------------------------------------------------------------- #
def snapshot(ctx, since=None, all_runs=False, exists=(), **extra):
    args = {"scripts": ctx.role_scripts, "store": ctx.store_dir, "project": ctx.project, "ticket": ctx.ticket,
            "caller": None if all_runs else ctx.spec["caller"], "kind": ctx.kind, "since": since,
            "exists": list(exists)}
    args.update(extra)
    doc = role_python(ctx, SNAPSHOT_PY, args)
    if doc.get("schema") != "runner-live-snapshot/1":
        raise Unknown("the store's reader answered with something else")
    return doc


def tracker(ctx, tickets, marks_of=(), writer=False, waiting=None):
    doc = role_python(ctx, TRACKER_PY, {"scripts": ctx.role_scripts, "settings": ctx.settings_path,
                                        "tickets": list(tickets), "marks_of": list(marks_of), "writer": writer,
                                        "waiting": waiting})
    if doc.get("schema") != "runner-live-tracker/1":
        raise Unknown("the tracker's reader answered with something else")
    if doc.get("state") != "read":
        raise Unknown("the runner's own reader %s" % doc.get("state"))
    return doc


def gh_json(ctx, argv):
    if not ctx.slug:
        raise Unknown("GitHub cannot be read: the project's repository is not known (the project clone row)")
    res = ctx.io.run(["gh"] + argv, timeout=120)
    try:
        return json.loads(res.out) if res.ok else None
    except ValueError:
        return None


def pr_for_branch(ctx, branch):
    got = gh_json(ctx, ["pr", "list", "--repo", ctx.slug, "--head", branch, "--state", "all", "--json",
                        "number,url,state,createdAt,headRefName,headRefOid,author"])
    if got is None:
        raise Unknown("GitHub could not be read with gh for %s" % ctx.slug)
    got = [p for p in got if isinstance(p, dict) and p.get("headRefName") == branch]
    return sorted(got, key=lambda p: p.get("number") or 0)[-1] if got else None


def pr_view(ctx, number):
    return gh_json(ctx, ["pr", "view", str(number), "--repo", ctx.slug, "--json",
                         "number,state,createdAt,headRefName,headRefOid,comments,commits"])


def review_comments(view, since=None):
    """Stage E's comments on a pull request, oldest first: each one's time, author, and its coverage line."""
    out = []
    for c in (view or {}).get("comments") or []:
        body = c.get("body") if isinstance(c, dict) else None
        if not isinstance(body, str) or review_local.REVIEW_MARKER not in body.split("\n", 1)[0]:
            continue
        at = epoch(c.get("createdAt"))
        if since is not None and (at is None or at < since):
            continue
        out.append({"at": at, "author": (c.get("author") or {}).get("login"),
                    "coverage": review_local.coverage_marker(body)})
    return sorted(out, key=lambda c: c["at"] or 0)


def off_command(ctx):
    return "python3 scripts/pipeline_runner_live.py off --lane %s --project %s%s" % (
        ctx.lane, ctx.project, "" if ctx.args.conf == setup.DEFAULT_CONF else " --conf %s" % ctx.args.conf)


# --------------------------------------------------------------------------- #
# 3 and 6. The switch: one setting, as the runner's docs define it
# --------------------------------------------------------------------------- #
def keep_sudo(ctx):
    """Keep the sudo session alive (`sudo -n -v`). False when it has ended."""
    return ctx.io.run(["sudo", "-n", "-v"], timeout=30).ok


def set_lane(ctx, on):
    """Write the lane on or off, as the role account, and read it back. Raises Missing / Unknown."""
    text, _mode = read_role_file(ctx, ctx.settings_path)
    if text is None:
        raise Missing("the runner's settings %s are gone" % ctx.settings_path)
    try:
        ctx.settings = json.loads(text)
        ctx.settings["projects"][ctx.project]
    except (ValueError, KeyError, TypeError):
        raise Missing("the runner's settings %s no longer hold project %s" % (ctx.settings_path, ctx.project))
    doc, bad = composed(ctx, on)
    if bad and on:
        raise Missing("the runner's own loader refuses the settings with the lane on: %s" % bad[0])
    body = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    res = as_role(ctx, ns.CONFIG_WRITE_SH.replace("@F@", shlex.quote(ctx.settings_path)), stdin=body)
    back, mode = read_role_file(ctx, ctx.settings_path) if res.ok else (None, "")
    if back != body or mode != "600":
        raise Unknown("%s could not be written as %s, or reading it back does not match (exit %d, mode %s)" % (
            ctx.settings_path, ctx.role, res.rc, mode or "-"))
    ctx.settings = doc
    if bad:
        ctx.notes.append("the settings did not load before the switch off either: %s" % bad[0])


def confirm(ctx, on):
    """Wait until the runner says, in its store, that it takes (or no longer takes) the lane. The time it
    was first seen, or None."""
    end = ctx.io.now() + CONFIRM_SECONDS
    while True:
        try:
            snap = snapshot(ctx, lanes_only=True)
            if snap["lanes_state"] == "read" and store.lane_on(snap["lanes"], ctx.project, ctx.kind) == on:
                return ctx.io.now()
        except Unknown:
            pass
        if ctx.io.now() >= end:
            return None
        ctx.io.sleep(5)


def switch_on(ctx):
    ctx.on_at = ctx.io.now()
    set_lane(ctx, True)
    ctx.say("Switched on: lanes.%s for %s in %s, at %s UTC." % (ctx.kind, ctx.project, ctx.settings_path,
                                                               hms(ctx.on_at)))
    ctx.on_seen = confirm(ctx, True)
    if ctx.on_seen is None:
        raise Missing("the runner did not say it takes the lane within %d seconds" % CONFIRM_SECONDS)
    ctx.say("The runner takes it (seen %s)." % hms(ctx.on_seen))


def switch_off(ctx):
    """Switch the lane off and confirm the runner took it. Never raises. True when confirmed. A second
    Ctrl-C cannot stop it half way."""
    old = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):                 # not the main thread: nothing to mask
        old = None
    try:
        for attempt in (1, 2):
            if not keep_sudo(ctx):
                ctx.say("Your sudo session has ended. Your Mac password, to switch the lane off:")
                if ctx.io.sudo_validate() != 0:
                    ctx.problem("sudo was refused, so the lane could not be switched off")
                    return False
            try:
                ctx.off_at = ctx.io.now()
                set_lane(ctx, False)
                break
            except (Missing, Unknown) as exc:
                if attempt == 2:
                    ctx.problem("the lane could not be switched off: %s" % exc)
                    return False
        ctx.say("Switched off: lanes.%s for %s, at %s UTC." % (ctx.kind, ctx.project, hms(ctx.off_at)))
        ctx.off_seen = confirm(ctx, False)
        if ctx.off_seen is None:
            ctx.problem("the runner did not say it no longer takes the lane within %d seconds" % CONFIRM_SECONDS)
            return False
        ctx.say("The runner no longer takes it (seen %s)." % hms(ctx.off_seen))
        return True
    except Exception as exc:                                      # noqa: BLE001  (never past this point)
        ctx.problem("switching the lane off failed: %s: %s" % (type(exc).__name__, short(str(exc), 200)))
        return False
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)


# --------------------------------------------------------------------------- #
# 4. The chain, lane by lane
# --------------------------------------------------------------------------- #
def mark(ctx, step, said, at=None):
    """A step seen, once, with when and what."""
    if ctx.steps.get(step) is None:
        ctx.steps[step] = {"at": ctx.io.now() if at is None else at, "said": said}
        ctx.say("  %s %-13s %s" % (hms(ctx.steps[step]["at"]), step, said))


def the_request(ctx, snap):
    """The newest request this lane's job made for the ticket since the switch on."""
    asked = snap.get("asked") or {}
    if asked.get("state") != "read":
        raise Unknown("the job's requests %s" % asked.get("state"))
    rows = [r for r in asked["rows"] if r["ticket"] == ctx.ticket and r["action"] == ctx.spec["action"]]
    return rows[-1] if rows else None


def run_of(snap, run_id):
    rows = (snap.get("runs") or {}).get("rows")
    if rows is None:
        raise Unknown("the runner's runs could not be read: %s" % (snap.get("runs") or {}).get("why"))
    found = [r for r in rows if r["run"] == run_id]
    return found[0] if found else None


def follow_run(ctx, snap):
    """asked, started and ended, from the store. Returns the run once it has ended, else None. A refusal,
    or a run that did not end well, is a failure (Missing with the runner's words)."""
    req = the_request(ctx, snap)
    if req is None:
        return None
    ctx.request = req
    mark(ctx, "asked", "request %s, by the %s job" % (req["id"], req["caller"]), epoch(req["at"]))
    ans = req.get("answer")
    if ans is None:
        return None
    if not ans.get("ok"):
        raise Missing("the runner refused it: %s" % (ans.get("why") or "no reason given"))
    run = run_of(snap, ans.get("run"))
    if run is None:
        return None
    ctx.run = run
    how = "approved by %s (%s)" % (run["approved_by"], run["how"]) if run["how"] in store.APPROVALS else \
        "resuming run %s, on %s's approval" % (run["resumes"], run["approved_by"]) if run["how"] in store.FOLLOWS \
        else "let start by its code run, on %s's approval" % run["approved_by"]
    mark(ctx, "started", "run %s, %s" % (run["run"], how), epoch(run["started_at"]))
    if run["state"] in store.LIVE:
        return None
    return run


def end_step(ctx, run):
    mark(ctx, "ended", "%s (%s, exit %s), %s, %s USD, %s turns" % (
        run["state"], run["reason"], run["exit"], span(run["seconds"]), run["cost_usd"], run["turns"]),
        epoch(run["ended_at"]))
    if run["state"] != "ended" or run["reason"] != "ended":
        raise Missing("the run did not end well: %s, %s%s" % (run["state"], run["reason"],
                                                            ": %s" % run["why"] if run["why"] else ""))


def watch_code(ctx, snap):
    run = follow_run(ctx, snap)
    live = ctx.run
    if live is not None and ctx.steps["pin"] is None:
        pin = (snap.get("pins") or {}).get(live["run"])
        if pin and pin.get("state") == "read":
            ctx.pin = pin
            mark(ctx, "pin", "mode %s; budget %s turns, %s USD, %s min; %s criteria, %s out of scope" % (
                pin["session_mode"], pin["max_turns"], pin["max_usd"], pin["max_minutes"], pin["criteria"],
                pin["out_of_scope"]))
    unread = None
    if live is not None and ctx.pr is None:
        try:
            pr = pr_for_branch(ctx, live["branch"])
        except Unknown as exc:              # GitHub this poll: the run's own end is still read below
            pr, unread = None, str(exc)
            ctx.misses += 1
        if pr is not None:
            ctx.pr = pr
            mark(ctx, "pull request", "#%s on %s, opened %s" % (pr["number"], pr["headRefName"],
                                                               hms(epoch(pr.get("createdAt")))), epoch(pr.get("createdAt")))
    if run is None:
        return False
    end_step(ctx, run)
    if ctx.steps["pin"] is None:
        ctx.problem("the run's pin was not seen while it ran (it ended between two looks)")
    if ctx.pr is None:
        raise Missing("the run ended and no pull request is open on its branch %s%s" % (
            run["branch"], " (%s)" % unread if unread else ""))
    return True


def after_code(ctx):
    """Stage E reviews the pull request: its comment, after the pull request opened."""
    view = pr_view(ctx, ctx.pr["number"])
    if view is None:
        return False
    got = review_comments(view, since=epoch(ctx.pr.get("createdAt")))
    if not got:
        return False
    c = got[0]
    cov = c["coverage"] or {}
    mark(ctx, "reviewed", "Stage E's comment by %s, coverage %s, at head %s" % (
        c["author"], cov.get("coverage") or "not marked", short(cov.get("head"))), c["at"])
    if cov.get("coverage") not in ("full", "partial"):
        ctx.problem("Stage E did not review it: its comment's coverage is %s%s" % (
            cov.get("coverage") or "not marked", " (%s)" % cov["reason"] if cov.get("reason") else ""))
    return True


def watch_fix(ctx, snap):
    run = follow_run(ctx, snap)
    if ctx.run is not None and ctx.steps["pushed"] is None:
        view = pr_view(ctx, ctx.pr["number"])
        head = (view or {}).get("headRefOid")
        if head and head != ctx.before.get("head"):
            ctx.pr = dict(ctx.pr, headRefOid=head, commits=view.get("commits") or [])
            mark(ctx, "pushed", "pull request #%s moved from %s to %s" % (ctx.pr["number"], short(ctx.before.get("head")),
                                                                          short(head)))
    if run is None:
        return False
    end_step(ctx, run)
    if ctx.steps["pushed"] is None:
        view = pr_view(ctx, ctx.pr["number"]) or {}
        if view.get("headRefOid") and view["headRefOid"] != ctx.before.get("head"):
            ctx.pr = dict(ctx.pr, headRefOid=view["headRefOid"], commits=view.get("commits") or [])
            mark(ctx, "pushed", "pull request #%s moved to %s" % (ctx.pr["number"], short(view["headRefOid"])))
        else:
            raise Missing("the fix run ended and the pull request's head did not move: nothing was pushed")
    return True


def watch_review(ctx, snap):
    run = follow_run(ctx, snap)
    if run is None:
        return False
    if ctx.steps["ended"] is None:
        end_step(ctx, run)
    view = pr_view(ctx, ctx.pr["number"])
    got = review_comments(view, since=epoch(run["started_at"]))
    if not got:
        return False
    c = got[0]
    cov = c["coverage"] or {}
    ctx.before["review_comment"] = c
    mark(ctx, "reviewed", "the published comment by %s, coverage %s, at head %s" % (
        c["author"], cov.get("coverage") or "not marked", short(cov.get("head"))), c["at"])
    return True


def watch_plan(ctx, snap):
    run = follow_run(ctx, snap)
    if run is None:
        return False
    end_step(ctx, run)
    out = (snap.get("outputs") or {}).get(run["run"])
    if not out or out.get("state") or out.get("is_error") or not out.get("chars"):
        raise Missing("the plan run left %s, so the planner has no plan document to read" % (
            "no final message" if not out else "a message it could not read" if out.get("state") else
            "an error" if out.get("is_error") else "an empty message"))
    ctx.before["output"] = out
    return True


def acceptance_files(ctx):
    """The acceptance job's settings, as installed: its run state, its ledger and the look tool's records."""
    path = under_home(ctx, ctx.args.acceptance_config)
    text, _m = read_role_file(ctx, path)
    try:
        cfg = json.loads(text) if text is not None else None
    except ValueError:
        cfg = None
    if not isinstance(cfg, dict) or not cfg.get("state_dir"):
        raise Missing("the acceptance job's settings %s are %s" % (path, "absent" if text is None else "not its"))
    state = under_home(ctx, cfg["state_dir"])
    return {"run_state": os.path.join(state, "acceptance-run.json"),
            "ledger": under_home(ctx, cfg.get("ledger") or os.path.join(state, "acceptance-ledger.json")),
            "looks": under_home(ctx, cfg.get("look_record_dir") or "")}


def watch_acceptance(ctx, snap):
    run = follow_run(ctx, snap)
    if run is None:
        return False
    if ctx.steps["ended"] is None:
        end_step(ctx, run)
    files = ctx.before.get("acceptance") or acceptance_files(ctx)
    ctx.before["acceptance"] = files
    got = snapshot(ctx, json=[files["run_state"]])["files"][files["run_state"]]
    if got.get("state") != "read":
        raise Unknown("the acceptance job's run state %s" % got.get("state"))
    checks = [c for c in ((got["doc"] or {}).get("checks") or {}).values()
              if isinstance(c, dict) and c.get("request") == ctx.request["id"]]
    if not checks:
        return False
    check_rec = checks[0]
    ctx.before["check"] = check_rec
    led = snapshot(ctx, ledger=files["ledger"], plan=check_rec.get("plan"))["files"].get(files["ledger"]) or {}
    if led.get("state") != "read":
        raise Unknown("the acceptance ledger %s" % led.get("state"))
    rows = [r for r in led["rows"] if r.get("head") == check_rec.get("head")]
    verdict = (rows[0].get("verdict") or {}) if rows else {}
    if verdict.get("status") not in ("written", "failed"):
        return False
    ctx.before["verdict"] = verdict
    mark(ctx, "verdict", "%s for %s at %s, its comment %s" % (verdict.get("word"), check_rec.get("plan"),
                                                               short(check_rec.get("head")), verdict.get("status")))
    if verdict.get("status") != "written":
        raise Missing("the verdict comment could not be posted")
    return True


# Each lane's steps and checks, by name: looked up when used, so the selftest's mutants reach them.
WATCH = dict((lane, "watch_" + lane) for lane in LANES)
AFTER = {"code": "after_code"}


def waiting_for(ctx, names):
    left = [s for s in names if ctx.steps.get(s) is None]
    return left[0] if left else None


def watch(ctx):
    """Wait for the runner's part of the chain. None when it is done, else what went wrong, in words."""
    deadline = ctx.on_at + 60 * ctx.minutes
    while True:
        if not keep_sudo(ctx):
            return "your sudo session ended while it waited, so the store could not be read"
        try:
            snap = snapshot(ctx, since=ctx.on_at - 5)
            ctx.misses = 0
            if globals()[WATCH[ctx.lane]](ctx, snap):
                return None
        except Missing as exc:
            return str(exc)
        except Unknown as exc:
            ctx.misses += 1
            if ctx.misses == STORE_MISSES:
                ctx.problem("a read failed %d times in a row while it waited: %s" % (STORE_MISSES, exc))
        if ctx.io.now() >= deadline:
            return "%s did not happen within %d minutes of the switch on" % (
                waiting_for(ctx, ctx.spec["chain"]) or "the chain's end", ctx.minutes)
        ctx.io.sleep(POLL_SECONDS)


def after(ctx, failed):
    """With the lane off: the steps after it, and the watch that nothing of the lane starts. Both share one
    loop, so the quiet watch lasts at least QUIET_SECONDS and covers the whole wait."""
    start = ctx.io.now()
    names = () if failed else ctx.spec["after_chain"]
    deadline = start + 60 * ctx.after_minutes
    while True:
        if names and waiting_for(ctx, names):
            try:
                globals()[AFTER[ctx.lane]](ctx)
            except (Missing, Unknown) as exc:
                ctx.problem(str(exc))
                names = ()
        quiet = ctx.io.now() - start >= QUIET_SECONDS
        if quiet and not (names and waiting_for(ctx, names)):
            break
        if names and ctx.io.now() >= deadline:
            ctx.problem("%s did not happen within %d minutes of the switch off" % (waiting_for(ctx, names),
                                                                                   ctx.after_minutes))
            names = ()
            continue
        if not keep_sudo(ctx) and ctx.io.sudo_validate() != 0:
            ctx.problem("sudo was refused, so nothing after the switch off could be watched")
            return False
        ctx.io.sleep(POLL_SECONDS)
    return quiet_check(ctx)


def quiet_check(ctx):
    """Nothing of the lane started after the runner took the switch off. A request answered after it must
    have been refused. A run that started between the write and the runner's taking it is said, not failed."""
    try:
        snap = snapshot(ctx, since=(ctx.off_at or ctx.io.now()) - 5)
    except Unknown as exc:
        ctx.problem("whether anything started after the switch off could not be read: %s" % exc)
        return False
    rows = snap["runs"]["rows"]
    if rows is None:
        ctx.problem("whether anything started after the switch off could not be read: %s" % snap["runs"]["why"])
        return False
    seen = ctx.off_seen or ctx.io.now()
    for r in rows:
        at = epoch(r["started_at"])
        if r["kind"] != ctx.kind or at is None or at < (ctx.off_at or seen):
            continue
        if at > seen:
            ctx.started_after.append(r["run"])
        else:
            ctx.notes.append("run %s started at %s, after the write and before the runner took the switch off"
                             % (r["run"], hms(at)))
    for q in (snap.get("asked") or {}).get("rows") or []:
        ans = q.get("answer") or {}
        if ans.get("ok") and epoch(ans.get("at")) is not None and epoch(ans["at"]) > seen:
            if ans.get("run") not in ctx.started_after:
                ctx.started_after.append(ans.get("run"))
        elif ans and not ans.get("ok") and epoch(q["at"]) is not None and epoch(q["at"]) >= (ctx.off_at or seen):
            ctx.refused_after.append(ans.get("why") or "no reason")
    if ctx.started_after:
        ctx.problem("%d run(s) of the %s lane started after the switch off: %s" % (
            len(ctx.started_after), ctx.kind, ", ".join(ctx.started_after)))
        return False
    return True


# --------------------------------------------------------------------------- #
# 5. Do they agree
# --------------------------------------------------------------------------- #
def agreed(ctx, name, ok, words):
    """AGREE, DISAGREE, NOT CHECKED (`ok` None: it could not be read or judged, so it never passes) or BY HAND
    (`ok` BY_HAND: the proof leaves this comparison to a person, and the result lists it)."""
    ctx.agree.append((name, ok if ok == BY_HAND else AGREE if ok is True else NOT_CHECKED if ok is None
                      else DISAGREE, words))


def history_of(ctx, ticket):
    node = (ctx.tracker_doc or {}).get("tickets", {}).get(ticket) or {}
    if node.get("state") != "read" or node.get("history") is None:
        return None, node
    return sorted(((epoch(e["at"]), e) for e in node["history"] if epoch(e.get("at")) is not None),
                  key=lambda p: p[0]), node


def agree_code(ctx):
    run, pr = ctx.run, ctx.pr
    owners = (ctx.cfg or {}).get("owner_user_ids") or []
    writer = ((ctx.cfg or {}).get("session_tools") or {}).get("writer_user_id")
    hist, node = history_of(ctx, ctx.ticket)
    states = (ctx.proj.get("approval_states") or {}).get("code") or []
    at = epoch(run["approved_at"])
    if hist is None:
        agreed(ctx, "who approved", None, "the ticket's history could not be read whole: %s" % (
            node.get("state") or "not read"))
    elif run["how"] == "move":
        moves = [e for t, e in hist if e["actor"] == run["approved_by"] and e["to"] in states
                 and at is not None and abs(t - at) <= TIME_SLACK]
        agreed(ctx, "who approved", bool(moves) and run["approved_by"] in owners,
               "run: %s, by a move at %s; the tracker %s" % (run["approved_by"], run["approved_at"],
                                                              "holds that move into an approved state" if moves else
                                                              "holds no such move by that user at that time"))
    elif run["how"] == "creation":
        same = node.get("creator") == run["approved_by"] and at is not None and \
            abs((epoch(node.get("created_at")) or 0) - at) <= TIME_SLACK
        agreed(ctx, "who approved", same and run["approved_by"] in owners,
               "run: %s, by creating it at %s; the tracker says %s made it at %s" % (
                   run["approved_by"], run["approved_at"], node.get("creator"), node.get("created_at")))
    else:
        agreed(ctx, "who approved", BY_HAND, "approved %s, which the tracker's history does not hold: compare "
               "with that record" % ("in Slack" if run["how"] == "slack" else "by the validator"))
    started = epoch(run["started_at"])
    working = (ctx.pin or {}).get("state_id")
    if hist is not None:
        moved = [(t, e) for t, e in hist if writer and e["actor"] == writer and e["to"]
                 and started is not None and started - WRITER_MOVE_BEFORE <= t <= started + TIME_SLACK]
        right = [p for p in moved if working is None or p[1]["to"] == working]
        agreed(ctx, "what ran", bool(right) and run["kind"] == "code" and run["ticket"] == ctx.ticket
               and pr["headRefName"] == run["branch"] and (ctx.pin is None or ctx.pin["dispatch_id"] == run["run"]),
               "a code run of %s on %s; the pull request's branch is %s; the session writer moved the ticket %s" % (
                   run["ticket"], run["branch"], pr["headRefName"],
                   "into its working state %ds before the start" % (started - right[0][0]) if right else
                   "into no working state just before the start"))
    else:
        agreed(ctx, "what ran", None, "the ticket's history could not be read whole")
    ended, opened = epoch(run["ended_at"]), epoch(pr.get("createdAt"))
    length = (ended - started) if ended is not None and started is not None else None
    ok = length is not None and run["seconds"] is not None and abs(run["seconds"] - length) <= TIME_SLACK \
        and opened is not None and started <= opened <= ended + TIME_SLACK
    agreed(ctx, "for how long", ok, "the run: %s to %s, %s (its record says %s); the pull request opened %s" % (
        hms(started), hms(ended), span(length), span(run["seconds"]), hms(opened)))


def agree_fix(ctx):
    run, code = ctx.run, ctx.code_run
    resumed = run["how"] in store.FOLLOWS and run["resumes"] is not None
    same = run["how"] == "resumed-run" and run["session_id"] == code["session_id"]
    agreed(ctx, "the same session", resumed and (same or run["how"] == "fresh-session"),
           "run %s %s run %s; session %s %s" % (run["run"], "resumes" if resumed else "does not resume",
                                                run["resumes"], short(run["session_id"], 8),
                                                "is the coding session's" if same else
                                                "is a fresh one: the coding session was gone" if run["how"] == "fresh-session"
                                                else "is another session's"))
    path = os.path.join(under_home(ctx, ctx.args.stage_e_state), "bounce-ledger.jsonl")
    got = snapshot(ctx, jsonl={path: {"repo": ctx.slug, "pr": ctx.pr["number"]}})["files"].get(path) or {}
    if got.get("state") != "read":
        agreed(ctx, "the bounce ledger", None, "%s %s" % (path, got.get("state") or "was not read"))
    else:
        rows = got["rows"]
        sent = [r for r in rows if r.get("outcome") == "delivered" and r.get("via") == "runner"
                and r.get("ref") == ctx.request["id"]]
        spent = [r for r in rows if r.get("outcome") == "spent" and sent and r.get("bounce_no") == sent[0].get("bounce_no")]
        agreed(ctx, "the bounce ledger", bool(sent and spent),
               "%s: %s" % (path, "bounce %s spent and delivered to the runner as request %s" % (
                   sent[0].get("bounce_no"), ctx.request["id"]) if sent and spent else
                   "no spent and delivered row names request %s" % ctx.request["id"]))
        ctx.before["spent"] = spent[0] if spent else None
    started, ended = epoch(run["started_at"]), epoch(run["ended_at"])
    head = ctx.pr.get("headRefOid")
    commits = [c for c in ctx.pr.get("commits") or [] if isinstance(c, dict) and c.get("oid") == head]
    pushed = epoch(commits[0].get("committedDate")) if commits else None
    from_head = (ctx.before.get("spent") or {}).get("head_sha")
    agreed(ctx, "the fix push", pushed is not None and started - TIME_SLACK <= pushed <= ended + TIME_SLACK
           and head != from_head, "the head moved from %s to %s, committed %s, inside the run's %s to %s" % (
               short(from_head or ctx.before.get("head")), short(head), hms(pushed), hms(started), hms(ended)))


def agree_review(ctx):
    run = ctx.run
    agreed(ctx, "what ran", run["kind"] == "review" and run["how"] == "code-run" and run["ticket"] == ctx.ticket,
           "a review run of %s, let start by its code run (%s)" % (run["ticket"], run["how"]))
    view = pr_view(ctx, ctx.pr["number"])
    allc = review_comments(view)
    mine = ctx.before.get("review_comment")
    old = [c for c in allc if mine is None or (c["at"] or 0) < (mine["at"] or 0)]
    cov = (mine or {}).get("coverage") or {}
    if mine is None or not old:
        agreed(ctx, "same shape and marks", None if mine else False,
               "no comment of the old lane's on #%s to compare with" % ctx.pr["number"] if mine else
               "no published comment was seen")
    else:
        o = old[-1]["coverage"] or {}
        agreed(ctx, "same shape and marks", bool(cov) and bool(o) and set(cov) <= set(o) | {"withheld", "reason"},
               "the runner's comment: marker on line 1, coverage %s; the old lane's: coverage %s" % (
                   cov.get("coverage") or "not marked", o.get("coverage") or "not marked"))
    path = qs.with_home(os.path.join(ctx.args.stage_e_state, "outcomes", "%s__pr-%d.json" % (
        ctx.slug.replace("/", "__"), ctx.pr["number"])), ctx.home)
    got = snapshot(ctx, json=[path])["files"].get(path) or {}
    if got.get("state") != "read":
        agreed(ctx, "no review ticket", None, "Stage E's outcome record %s %s" % (path, got.get("state")))
    else:
        filed = (got["doc"] or {}).get("review_ticket")
        agreed(ctx, "no review ticket", not filed, "Stage E's outcome record says %s" % (
            "review ticket %s" % filed if filed else "no review ticket"))


def agree_plan(ctx):
    """The old lane files a planning ticket and puts its routing label on that ticket. A plan request for
    the idea itself is the runner lane's path, on which the planner files neither (its own selftest)."""
    run = ctx.run
    agreed(ctx, "no planning ticket, no routing label",
           run["ticket"] == ctx.ticket and ctx.request["ticket"] == ctx.ticket and ctx.request["caller"] == "planner",
           "the planner's request and the run's record name the idea %s itself, so no planning ticket was made to "
           "carry a routing label" % run["ticket"])
    hist, node = history_of(ctx, ctx.ticket)
    before, after_ = sorted(ctx.before.get("labels") or []), sorted(node.get("labels") or [])
    if before != after_:
        ctx.notes.append("the idea's labels changed during the run: before %s; after %s" % (
            ", ".join(before) or "none", ", ".join(after_) or "none"))
    out = ctx.before.get("output") or {}
    agreed(ctx, "the plan document", BY_HAND, "the run left a %d-character final message for the planner; compare "
           "the tree it filed with the old lane's tree for the same idea, by hand" % (out.get("chars") or 0))


def agree_acceptance(ctx):
    run, rec, verdict = ctx.run, ctx.before.get("check") or {}, ctx.before.get("verdict") or {}
    files = ctx.before.get("acceptance") or {}
    agreed(ctx, "what ran", run["kind"] == "acceptance" and run["ticket"] == ctx.ticket
           and rec.get("piece") == ctx.ticket and rec.get("request") == ctx.request["id"],
           "an acceptance run of %s, from request %s; the job's check record names piece %s and request %s" % (
               run["ticket"], ctx.request["id"], rec.get("piece"), rec.get("request")))
    looks = os.path.join(files.get("looks") or "/nowhere", ctx.request["id"], rec.get("plan") or "none")
    got = snapshot(ctx, records=[looks])["files"].get(looks) or {}
    recs = got.get("records") or []
    agreed(ctx, "the look tool's record", got.get("state") == "read" and bool(recs) and all(
        r.get("plan") == rec.get("plan") for r in recs),
        "%s: %s" % (looks, "%d look run(s), each for plan %s" % (len(recs), rec.get("plan")) if recs else
                    got.get("state") or "no record"))
    epic = (ctx.before.get("ticket") or {}).get("parent")
    marks = ((ctx.tracker_doc or {}).get("marks") or {}).get(epic) or {"state": "not read: the piece has no parent"}
    match = [m for m in marks.get("marks") or [] if m.get("plan") == rec.get("plan") and m.get("head") == rec.get("head")]
    agreed(ctx, "the verdict comment", None if marks.get("state") != "read" else bool(match) and all(
        m["verdict"] == verdict.get("word") for m in match),
        "the epic's verdict marks for %s at %s: %s; the ledger says %s" % (
            rec.get("plan"), short(rec.get("head")), ", ".join("%s by %s" % (m["verdict"], m.get("author"))
                                                              for m in match) or "none", verdict.get("word")))


AGREE_FN = dict((lane, "agree_" + lane) for lane in LANES)


def agreement(ctx):
    try:
        tickets, marks = [ctx.ticket], []
        if ctx.lane == "acceptance":
            parent = ((ctx.before.get("ticket") or {}).get("parent"))
            marks = [parent] if parent else []
        ctx.tracker_doc = tracker(ctx, tickets, marks)
    except Unknown as exc:
        ctx.tracker_doc = None
        ctx.notes.append("the tracker could not be read for the checks: %s" % exc)
    try:
        globals()[AGREE_FN[ctx.lane]](ctx)
    except (Missing, Unknown) as exc:
        agreed(ctx, "the checks", None, str(exc))
    except Exception as exc:                                      # noqa: BLE001  (said, never passed)
        agreed(ctx, "the checks", None, "unexpected %s: %s" % (type(exc).__name__, short(str(exc), 200)))


# --------------------------------------------------------------------------- #
# 7. The result block
# --------------------------------------------------------------------------- #
def result_block(ctx, failed, off_ok, quiet_ok):
    """(text, passed). Passed only when every step happened, nothing went wrong, the lane is off, nothing
    started after it, and every check AGREEs or is one the proof leaves to a person (BY HAND, listed)."""
    unchecked = [w for _n, r, w in ctx.agree if r in (DISAGREE, NOT_CHECKED)]
    by_hand = [(n, w) for n, r, w in ctx.agree if r == BY_HAND]
    passed = bool(ctx.agree) and not failed and off_ok and quiet_ok and not unchecked and not ctx.problems
    if not off_ok:
        head = "THE LANE MAY STILL BE ON. Run: %s" % off_command(ctx)
    elif passed:
        head = "PASSED: every step happened, every check it made agreed, and the lane is off%s" % (
            "; %d comparison(s) are left to you, below" % len(by_hand) if by_hand else "")
    else:
        head = "FAILED: %s" % (failed or (ctx.problems[0] if ctx.problems else None) or
                               (unchecked[0] if unchecked else "the chain did not finish"))
    lines = ["### Live run: the %s lane, project %s, ticket %s" % (ctx.kind, ctx.project, ctx.ticket), "",
             "**Result: %s.**" % head,
             "Run with scripts/pipeline_runner_live.py, kit %s, on %s UTC." % (
                 short(ctx.before.get("kit_head")) or "unknown", time.strftime("%Y-%m-%d", time.gmtime(ctx.on_at or 0))),
             "",
             "**The switch.** lanes.%s for %s: on at %s (the runner took it %s), off at %s (taken %s). %s" % (
                 ctx.kind, ctx.project, hms(ctx.on_at), hms(ctx.on_seen), hms(ctx.off_at), hms(ctx.off_seen),
                 "Nothing of the lane started after it%s." % (
                     "; %d request(s) after it were refused (%s)" % (len(ctx.refused_after), ctx.refused_after[0])
                     if ctx.refused_after else "") if quiet_ok else
                 "Something of the lane started after it, or that could not be read."),
             "", "| Step | When (UTC) | What was seen |", "| --- | --- | --- |"]
    for name in ctx.spec["chain"] + ctx.spec["after_chain"]:
        st = ctx.steps.get(name)
        lines.append("| %s | %s | %s |" % (name, hms(st["at"]) if st else "-",
                                           st["said"].replace("|", "/") if st else "not seen"))
    lines += ["", "| Check | Result | Detail |", "| --- | --- | --- |"]
    for n, r, w in ctx.agree or [("the checks", NOT_CHECKED, "the chain did not finish, so nothing was compared")]:
        lines.append("| %s | %s | %s |" % (n, r, w.replace("|", "/")))
    bad = ([failed] if failed else []) + [p for p in ctx.problems if p != failed] + \
        ["%s: %s" % (n, w) for n, r, w in ctx.agree if r in (DISAGREE, NOT_CHECKED)]
    lines += ["", "**Did not work.**" if bad else "**Did not work:** nothing."]
    lines += ["- %s" % b for b in bad]
    if by_hand:
        lines += ["", "**Left to you.**"] + ["- %s: %s" % (n, w) for n, w in by_hand]
    if ctx.notes:
        lines += ["", "**Notes.**"] + ["- %s" % n for n in ctx.notes]
    return "\n".join(lines), passed


# --------------------------------------------------------------------------- #
# The commands
# --------------------------------------------------------------------------- #
def refused(io):
    env = io.env()
    found = [m for m in AGENT_ENV_MARKERS if m in env]
    if found:
        return ("REFUSED: this is an agent's environment (%s set). This tool switches a lane of your runner, "
                "so a person runs it, in their own terminal. Nothing was read or changed." % ", ".join(found))
    import pipeline_root_guard as rg      # KIT-693: the one refusal every installer shares
    return rg.refusal(io.uid())


def parse(argv):
    p = argparse.ArgumentParser(prog="pipeline_runner_live.py", description=__doc__.split("\n")[0])
    p.add_argument("command", choices=("check", "run", "off"))
    p.add_argument("--lane", required=True, choices=LANES)
    p.add_argument("--project", required=True)
    p.add_argument("--ticket")
    p.add_argument("--conf", default=setup.DEFAULT_CONF)
    p.add_argument("--queue-conf", default=qs.DEFAULT_CONF)
    p.add_argument("--probe", help="the folder the probe wrote (its --out); default <RUNNER_DIR>/probe")
    p.add_argument("--minutes", type=int)
    p.add_argument("--after-minutes", type=int)
    p.add_argument("--stage-e-state", default="~/.stage-e/state")
    p.add_argument("--acceptance-config", default="~/.stage-e/acceptance/acceptance.json")
    args = p.parse_args(argv)
    if not store.PROJECT_RE.match(args.project):
        p.error("--project must be a project key of the runner's settings")
    if args.command != "off" and not (args.ticket and store.TICKET_RE.match(args.ticket)):
        p.error("--ticket must be a ticket id, such as ENG-12")
    for name in ("minutes", "after_minutes"):
        v = getattr(args, name)
        if v is not None and not 1 <= v <= 24 * 60:
            p.error("--%s must be from 1 to 1440" % name.replace("_", "-"))
    return args


def main(argv=None, io=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--selftest"]:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "runner-live-selftest-")
    io = io or IO()
    why = refused(io)
    if why:
        io.say(why)
        return EXIT_REFUSED
    try:
        args = parse(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    ctx = Ctx(io, args)
    ctx.minutes = args.minutes or ctx.spec["minutes"]
    ctx.after_minutes = args.after_minutes or ctx.spec["after"]
    if io.sudo_validate() != 0:
        io.say("NO SUDO: it reads and switches the runner's settings as the role account, which needs your "
               "Mac password. Nothing was read or changed.")
        return EXIT_NOPRIV
    try:
        load(ctx)
    except SetupError as exc:
        io.say("FAILED: %s" % exc)
        return EXIT_USAGE
    except (Missing, Unknown) as exc:
        io.say("CANNOT START: %s" % exc)
        return EXIT_MISSING
    if args.command == "off":
        return command_off(ctx)
    io.say("The %s lane, project %s, ticket %s: the prerequisites (nothing is changed)" % (
        ctx.kind, ctx.project, ctx.ticket))
    ready = check(ctx)
    if not ready:
        io.say("\nNot ready: each row above that is not OK says what to do. Nothing was changed.")
        return EXIT_MISSING
    if args.command == "check":
        io.say("\nReady. `run` switches the lane on, after your yes.")
        return EXIT_OK
    return command_run(ctx)


def command_off(ctx):
    if not ctx.settings["projects"][ctx.project].get("lanes", {}).get(ctx.kind):
        ctx.say("The %s lane is off for %s in %s already." % (ctx.kind, ctx.project, ctx.settings_path))
    ok = switch_off(ctx)
    for p in ctx.problems:
        ctx.say("PROBLEM: %s" % p)
    return EXIT_OK if ok else EXIT_STILL_ON


def _raise_stop(signum, _frame):
    raise Interrupted("stopped by signal %d" % signum)


def command_run(ctx):
    s = ctx.spec
    ctx.say("")
    ctx.say("What `run` does, and nothing else:")
    ctx.say("  1. Switch lanes.%s ON for project %s, in %s, as %s. No other lane, project or key changes." % (
        ctx.kind, ctx.project, ctx.settings_path, ctx.role))
    ctx.say("  2. Then you: %s" % (s["owner"] % ctx.ticket))
    ctx.say("  3. Wait up to %d minutes for: %s." % (ctx.minutes, ", ".join(s["chain"])))
    ctx.say("  4. Switch lanes.%s OFF again, by the same setting, %s; watch %d minutes that nothing of it starts."
            % (ctx.kind, "and wait up to %d minutes for: %s" % (ctx.after_minutes, ", ".join(s["after_chain"]))
               if s["after_chain"] else "at once", QUIET_SECONDS // 60))
    ctx.say("  5. Check that the run record, the tracker, the pull request and the ledger agree, and print the "
            "result.")
    ctx.say("A failure or Ctrl-C at any point switches the lane off first.")
    if ctx.io.ask("Type yes to switch the %s lane on for %s: " % (ctx.kind, ctx.project)).strip() != "yes":
        ctx.say("Not yes, so nothing was changed.")
        return EXIT_NOT_YES
    olds = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            olds[sig] = signal.signal(sig, _raise_stop)
        except (ValueError, OSError):
            pass
    failed = None
    try:
        switch_on(ctx)
        ctx.say("")
        ctx.say("NOW: %s" % (s["owner"] % ctx.ticket))
        failed = watch(ctx)
    except (Missing, Unknown) as exc:
        failed = str(exc)
    except (KeyboardInterrupt, Interrupted) as exc:
        failed = "stopped by you (%s)" % (exc or "Ctrl-C")
    except Exception as exc:                                      # noqa: BLE001  (a surprise still ends off)
        failed = "unexpected %s: %s" % (type(exc).__name__, short(str(exc), 200))
    finally:
        off_ok = switch_off(ctx)
        for sig, old in olds.items():
            signal.signal(sig, old)
    quiet_ok = False
    if off_ok:
        try:
            quiet_ok = after(ctx, failed)
        except (KeyboardInterrupt, Interrupted):
            ctx.problem("you stopped the watch after the switch off, so it is not complete")
            quiet_ok = quiet_check(ctx)
    if not failed and ctx.run is not None:
        agreement(ctx)
    text, passed = result_block(ctx, failed, off_ok, quiet_ok)
    ctx.say("")
    ctx.say("---- result: post this on the lane's live-proof ticket ----")
    ctx.say(text)
    ctx.say("---- end of result ----")
    return EXIT_STILL_ON if not off_ok else EXIT_OK if passed else EXIT_FAILED


# --------------------------------------------------------------------------- #
# --selftest: offline. The role account's home is a folder, its file reads and writes and its two Python
# readers run for real under /bin/sh, and the store is the real store module's. The runner, the lane's job,
# Stage E, GitHub, the tracker and the role account's git and stat looks are stood in for. A mutant for each
# rule: a check that cannot fail checks nothing.
# --------------------------------------------------------------------------- #
ROLE, OWNER, WRITER, DISP = "_exrunner", "owner-1", "writer-1", "disp-1"
T0 = calendar.timegm((2026, 11, 20, 9, 0, 0, 0, 0, 0))
H0, H1, KIT_HEAD, KINDS_COMMIT = "a" * 40, "b" * 40, "c" * 40, "d" * 40
SLUG, PR_NO = "acme/app", 7
PLANTED = "gh" + "p_" + "LIVERUNSELFTEST" + "Q" * 25        # built at runtime: no fixture looks like a key
TICKETS = {"code": "ENG-12", "fix": "ENG-12", "review": "ENG-12", "plan": "ENG-40", "acceptance": "ENG-50"}
STUB_RUNNER = '''# A stand-in for pipeline_runner in the live-run tool's selftest: the tracker is a data file.
import json
DATA = %r


class TrackerError(Exception):
    pass


def _data():
    with open(DATA) as fh:
        return json.load(fh)


def load_file(path):
    return (None, ["bad"]) if _data().get("settings_bad") else (
        {"linear_key_file": "/nowhere", "session_tools": {"key_file": "/nowhere-either", "writer_user_id": "w"}}, [])


class LinearTracker(object):
    def __init__(self, key_file):
        self.key_file = key_file

    def _one(self, t):
        d = _data()
        if d.get("down"):
            raise TrackerError("the tracker did not answer")
        if t not in d["tickets"]:
            raise TrackerError("the tracker has no ticket " + t)
        return d["tickets"][t]

    def issue(self, t):
        return self._one(t)["node"]

    def history(self, t):
        return self._one(t)["history"]

    def gql(self, query, variables):
        d = _data()
        if d.get("down"):
            raise TrackerError("the tracker did not answer")
        if "issues(" in query:
            return {"issues": {"nodes": [{"identifier": t} for t in d.get("todo") or []]}}
        return {"issue": {"comments": {"nodes": d["comments"].get(variables["id"], [])}}}


Q_VIEWER = "query RunnerViewer { viewer { id } }"


class tl_tools(object):
    @staticmethod
    def read_key(path):
        return "not-a-key"


class status_job(object):
    @staticmethod
    def linear_graphql(query, variables, key):
        return {"viewer": {"id": _data().get("writer_viewer")}}
'''
FLAGS = {
    "installer": "ok", "kit_main": KIT_HEAD, "origin": "https://github.com/acme/app.git", "reach": True,
    "runner_store": "~/.pipeline-runner/store", "usage_store": "~/.pipeline-runner/store",
    "keys": {}, "probe_walls": "PASS", "probe_commit": KIT_HEAD, "probe_account": ROLE,
    "engine": "/opt/kit-engine", "engine_problems": [], "push": "0", "writer_viewer": WRITER, "todo": [],
    "delivery": "ok", "brief_tools": True, "window_start": "08:00",
    "ancestor": "0", "lanes": None, "session_tools": True, "ticket_state": "st-backlog", "workspace": False,
    "refuse": None, "silent": False, "frozen": None, "stray": False, "ignore_off": False, "move_in_history": True,
    "no_review": False, "interrupt_at": None, "lose_sudo_at": None, "ledger_ref": "same", "review_ticket": None,
    "mark_word": "passed", "gh_down": False, "no_old_review": False, "no_push": False, "no_role": False,
    "gh_after_on": False,
}


def _put(path, text, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)


class World(object):
    """One test project on a stand-in Mac: the role account's home, the runner's settings and store, a stand-in
    runner that answers requests by its lanes as the real one does, the lane's own job and Stage E as events in
    time, and GitHub and the tracker as data."""

    def __init__(self, root, lane="code", **flags):
        self.f = dict(FLAGS, **flags)
        self.lane, self.kind, self.ticket = lane, SPEC[lane]["kind"], TICKETS[lane]
        self.root, self.home = root, os.path.join(root, "role")
        self.rd = os.path.join(self.home, ".pipeline-runner")
        self.store, self.work = os.path.join(self.rd, "store"), os.path.join(self.rd, "work")
        self.settings = os.path.join(self.rd, "runner.json")
        self.se = os.path.join(self.home, ".stage-e")
        self.acc = os.path.join(self.se, "acceptance")
        self.pins = os.path.join(self.home, ".claude", "pipeline", "pins")
        self.stub, self.tracker_file = os.path.join(root, "stub"), os.path.join(root, "tracker.json")
        self.conf, self.qconf = os.path.join(root, "owner", "runner.conf"), os.path.join(root, "owner", "queue.conf")
        self.now, self.sudo_live, self.validations, self.writes = T0, False, 0, 0
        self.on_seen = self.off_seen = self.asked = self.strayed = self.run_id = self.code_run = None
        self.move_at, self.events, self.prs = None, set(), {}
        self.tracker = {"tickets": {}, "comments": {}}
        _put(self.conf, "ROLE_ACCOUNT=%s\nCLAUDE_BIN=/opt/claude/bin/claude\nOWNER_USER_IDS=%s\n"
                        "DISPATCHER_USER_IDS=%s\nPROJECTS=app=~/repos/app@main\n" % (ROLE, OWNER, DISP))
        _put(self.qconf, "RUNNER_STORE=%s\n" % self.f["runner_store"] if self.f["runner_store"] else "# none\n")
        lanes = dict.fromkeys(store.KINDS, False)
        lanes.update(self.f["lanes"] or {})
        doc = {"enabled": True, "store_dir": "~/.pipeline-runner/store", "work_dir": "~/.pipeline-runner/work",
               "session_home": "~/.pipeline-runner/home", "claude_bin": "/opt/claude/bin/claude",
               "token_env": "CLAUDE_WRITER_OAUTH_TOKEN", "linear_key_file": "~/.pipeline-runner/tracker-key",
               "owner_user_ids": [OWNER], "dispatcher_user_ids": [DISP],
               "push_token_file": "~/.pipeline-runner/push-token", "queue_config": "~/.stage-e/queue/queue.json",
               "look_config_dir": "~/.stage-e/acceptance/look-configs",
               "projects": {"app": {"repo": "~/repos/app", "base": "main", "lanes": lanes, "approval_states": {
                   "code": ["st-todo"], "plan": ["st-planit"], "acceptance": ["st-acc"]}}}}
        if self.f["session_tools"]:
            doc["session_tools"] = {"key_file": "~/.pipeline-runner/writer-key", "writer_user_id": WRITER}
        _put(self.settings, json.dumps(doc, indent=2, sort_keys=True) + "\n")
        with open(self.settings) as fh:
            self.installed = fh.read()
        for d in (self.work, os.path.join(self.rd, "home"), self.pins):
            os.makedirs(d, exist_ok=True)
        for name in ("tracker-key", "writer-key", "push-token"):      # real files, each holding a planted value
            _put(os.path.join(self.rd, name), PLANTED + "\n")
        if store.prepare(self.store):
            raise AssertionError("the selftest's store could not be made")
        walls = [{"row": "%s:tools" % k, "class": "wall", "verdict": "PASS"} for k in ("code", "review")]
        walls[-1]["verdict"] = self.f["probe_walls"]
        meta = {"account": self.f["probe_account"], "when": "2026-11-19 20:00:00 UTC", "walls": 2}
        if self.f["probe_commit"]:
            meta["commit"] = self.f["probe_commit"]
        _put(os.path.join(self.rd, "probe", "results.json"), json.dumps({"meta": meta, "rows": walls}))
        _put(os.path.join(self.se, "usage", "usage-config.json"), json.dumps({"runner_store": self.f["usage_store"]}))
        days = dict((d, {"start": self.f["window_start"], "hours": 8})
                    for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"))
        _put(os.path.join(self.se, "queue", "queue.json"), json.dumps(
            {"window": {"time_zone": "UTC", "days": days, "grace_minutes": 0}}))
        self.tracker.update(writer_viewer=self.f["writer_viewer"], todo=self.f["todo"])
        os.makedirs(self.stub, exist_ok=True)
        _put(os.path.join(self.stub, "pipeline_runner.py"), STUB_RUNNER % self.tracker_file)
        self.node(self.ticket, self.f["ticket_state"])
        if self.f["workspace"]:
            os.makedirs(os.path.join(self.work, "code", self.ticket))
        if lane in ("fix", "review"):
            self.node(self.ticket, "st-review")
            self.code_run = self.record("code", self.ticket, "queue", "rq-" + "1" * 32, started=T0 - 7200,
                                        ended=T0 - 5400, approved_at=T0 - 7300)
            self.prs[PR_NO] = {"number": PR_NO, "url": "https://github.com/%s/pull/%d" % (SLUG, PR_NO),
                               "state": "OPEN", "createdAt": store.iso(T0 - 6000),
                               "headRefName": self.code_run["branch"], "headRefOid": H0,
                               "author": {"login": "pusher"}, "comments": [],
                               "commits": [{"oid": H0, "committedDate": store.iso(T0 - 6100)}]}
            if lane == "review" and not self.f["no_old_review"]:
                self.comment(T0 - 3600)
        if lane == "acceptance":
            self.tracker["tickets"][self.ticket]["node"]["parent"] = {"identifier": "ENG-49"}
            self.history(self.ticket, OWNER, "st-backlog", "st-acc", T0 - 600)
            _put(os.path.join(self.acc, "acceptance.json"), json.dumps(
                {"state_dir": "~/.stage-e/acceptance", "look_record_dir": "~/.stage-e/acceptance/looks"}))
        if self.f["no_role"]:
            os.chmod(self.settings, 0)
        self.save()

    # -- the tracker and GitHub, as data ----------------------------------- #
    def node(self, t, state):
        self.tracker["tickets"].setdefault(t, {"history": []})["node"] = {
            "id": "uuid-" + t, "identifier": t, "state": {"id": state}, "creator": {"id": OWNER},
            "createdAt": store.iso(T0 - 86400), "parent": None, "title": "add the thing",
            "labels": {"nodes": [{"id": "lb-1", "name": "effort:M"}]}}

    def history(self, t, actor, frm, to, at):
        self.tracker["tickets"][t]["history"].append({
            "createdAt": store.iso(at).replace("Z", ".250Z"), "actorId": actor, "fromStateId": frm, "toStateId": to,
            "addedLabelIds": [], "removedLabelIds": []})
        self.tracker["tickets"][t]["node"]["state"] = {"id": to}

    def save(self):
        with open(self.tracker_file, "w") as fh:
            json.dump(self.tracker, fh)

    def comment(self, at, coverage="full"):
        body = "## Stage E review %s\n%s\n\nA finding." % (review_local.REVIEW_MARKER,
                                                            review_local.coverage_line(coverage, H0))
        self.prs[PR_NO]["comments"].append({"author": {"login": "stage-e"}, "body": body, "createdAt": store.iso(at)})

    def stamp(self, folder, rid):
        """The store judges a request's age by its file's time, so a file the stand-ins write carries the
        stand-in clock's."""
        path = os.path.join(self.store, folder, rid + ".json")
        if os.path.exists(path):
            os.utime(path, (self.now, self.now))
        return rid

    def plist(self):
        command = ('set -a; . "$HOME/.stage-e/env"; set +a; exec /usr/bin/python3 "%s/scripts/pipeline_runner.py" '
                   'run --config "$HOME/.pipeline-runner/runner.json" # kit %s' % (self.f["engine"], KIT_HEAD))
        return plistlib.dumps({"Label": "local.pipeline-runner", "UserName": ROLE,
                               "EnvironmentVariables": {"HOME": self.home},
                               "ProgramArguments": ["/bin/sh", "-c", command]}).decode("utf-8")

    def gh_api(self, path):
        f, base = self.f, "repos/%s/contents/" % SLUG
        if f["gh_down"]:
            return Res(1, "", "gh: could not reach GitHub")
        if path == base + "delivery.json?ref=main" and f["delivery"] != "absent":
            text = json.dumps({"version": 1, "linear": {"teamKey": "ENG"}}) if f["delivery"] != "example" else \
                open(os.path.join(KIT, "delivery.example.json")).read()
            return Res(0, json.dumps({"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}))
        if path == base + BRIEF + "?ref=main":
            names = session_tools.TOOL_NAMES if f["brief_tools"] else session_tools.TOOL_NAMES[:3]
            text = "# The session brief\n\nYour tools: %s.\n" % ", ".join("`%s`" % t for t in names)
            return Res(0, json.dumps({"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}))
        return Res(1, "", "gh: Not Found (HTTP 404)")

    def validator(self, argv):
        if self.f["delivery"] == "example":           # the kit's own validator, run for real
            p = subprocess.run(argv, capture_output=True, universal_newlines=True, timeout=300)
            return Res(p.returncode, p.stdout, p.stderr)
        bad = self.f["delivery"] == "invalid"
        return Res(1 if bad else 0, json.dumps({"ok": not bad, "warnings": [], "notes": [], "errors": [
            {"rule": "budgets.perEffort", "message": "perEffort may only lower the cap"}] if bad else []}))

    def gh(self, argv):
        if self.f["gh_down"] or (self.f["gh_after_on"] and self.on_seen is not None):
            return Res(1, "", "gh: could not reach GitHub")
        if argv[:2] == ["pr", "list"]:
            head = argv[argv.index("--head") + 1]
            return Res(0, json.dumps([dict((k, v) for k, v in p.items() if k not in ("comments", "commits"))
                                      for p in self.prs.values() if p["headRefName"] == head]))
        if argv[:2] == ["pr", "view"] and int(argv[2]) in self.prs:
            return Res(0, json.dumps(self.prs[int(argv[2])]))
        return Res(1, "", "gh: not stood in for")

    def installer(self):
        rows = [{"step": s, "outcome": setup.ALREADY_DONE, "detail": "fine"} for s in ("home", "kit", "settings", "job")]
        left = None
        if self.f["installer"] == "job":
            rows[3] = {"step": "job", "outcome": setup.WOULD_CHANGE, "detail": "would load it; now: not loaded"}
        elif self.f["installer"] == "unknown":
            rows[1] = {"step": "kit", "outcome": setup.UNKNOWN, "detail": "NOT MEASURED: no live sudo"}
        elif self.f["installer"] == "left_off":
            left = "left off: its settings say enabled is false"
        return Res(10 if left else 0, json.dumps({"schema": setup.ROWS_SCHEMA, "exit": 0, "rows": rows,
                                                  "left_off": left, "why": None}))

    def shell(self, tag, script):
        f = self.f
        if tag == "git-kit":
            return Res(0, "head %s\nmain %s\nkinds %s\n" % (KIT_HEAD, f["kit_main"], KINDS_COMMIT))
        if tag == "git-project":
            return Res(0, "url %s\npush %s\nremote %s\nlocal %s\n" % (f["origin"], f["origin"],
                                                                      H0 if f["reach"] else "none", H0))
        if tag == "key":
            name = next((n for n in ("tracker-key", "writer-key", "push-token") if n in script), "")
            return Res(0, f["keys"].get(name, "stat 600 %s 41" % ROLE) + "\n")
        if tag == "push":
            if "credential.https://github.com.helper" not in script or "--dry-run" not in script:
                return Res(0, "push 99\n")         # only the adapter's helper, and only a dry run, may push
            return Res(0, "push %s\n" % f["push"])
        if tag == "engine":
            return Res(0, json.dumps({"state": "read", "problems": f["engine_problems"]}) + "\n")
        if tag == "ancestor":
            return Res(0, "ancestor %s\n" % f["ancestor"])
        return Res(1, "", "no such look")

    # -- the stand-in runner and the lane's job ---------------------------- #
    def record(self, kind, ticket, caller, rid, started, ended=None, how="move", approved_at=None, resumes=None,
               session=None, pin=None, branch=None):
        number = len(os.listdir(os.path.join(self.store, "runs"))) + 1
        rec = {"schema": store.RUN_SCHEMA, "run": "run-%032x" % number, "request": rid, "caller": caller,
               "kind": kind, "project": "app", "ticket": ticket, "resumes": resumes, "started_for": "request",
               "approved_by": OWNER, "approved_at": store.iso(approved_at) if approved_at else None, "how": how,
               "session_id": session or "0000%04x-0000-4000-8000-%012x" % (number, int(started)),
               "workspace": os.path.join(self.work, kind, ticket),
               "branch": branch or runner_job.branch_name(kind, ticket, "add the thing"), "pin": pin,
               "pid": None if ended else 4242, "state": "ended" if ended else store.RUNNING,
               "started_at": store.iso(started), "ended_at": store.iso(ended) if ended else None,
               "seconds": (ended - started) if ended else None, "exit": 0 if ended else None,
               "reason": "ended" if ended else None, "why": None, "cost_usd": 1.25 if ended else None,
               "turns": 30 if ended else None, "stop_asked_at": None, "auto_resume": None, "resumed_by": None,
               "gone_session": None}
        store.write_run(self.store, rec)
        return rec

    def tick(self):
        """One pass of the stand-in runner, as the real one makes it: its settings read again, the lanes it takes
        said in its store, then the lane's job, then each waiting request answered by its lane."""
        f, t = self.f, self.now
        if f["lose_sudo_at"] is not None and t >= f["lose_sudo_at"]:
            self.sudo_live, f["lose_sudo_at"] = False, None
        with open(self.settings) as fh:
            doc = json.load(fh)
        flags = store.lane_flags(doc["projects"]["app"].get("lanes")) or {}
        on = bool(doc.get("enabled")) and bool(flags.get(self.kind))
        frozen = f["frozen"] == "always" or (f["frozen"] == "after-on" and self.on_seen is not None)
        if not frozen:
            store.write_lanes(self.store, {"schema": store.LANES_SCHEMA, "enabled": bool(doc.get("enabled")),
                                           "work_dir": os.path.realpath(self.work),
                                           "look_config_dir": os.path.realpath(os.path.join(self.acc, "look-configs")),
                                           "projects": {"app": [k for k in store.KINDS if flags.get(k)]}})
            if on and self.on_seen is None:
                self.on_seen = t
            if not on and self.on_seen is not None and self.off_seen is None:
                self.off_seen = t
        if self.on_seen is not None and self.asked is None and on and t >= self.on_seen + 30:
            self.asked = self.stamp("requests", self.ask(self.ticket))
        if f["stray"] and self.off_seen is not None and self.strayed is None and t >= self.off_seen + 20:
            self.strayed = self.stamp("requests", self.ask("ENG-13" if self.lane in ("code", "plan") else self.ticket,
                                                           stray=True))
        for rid, req, problems in store.pending(self.store):
            if problems:
                continue
            if not (on or (f["ignore_off"] and rid == self.strayed)):
                store.settle(self.store, rid, req, False, None, "the %s lane is off for project app" % req["kind"], t)
            elif f["refuse"]:
                store.settle(self.store, rid, req, False, None, f["refuse"], t)
            elif not f["silent"]:
                rec = self.start(req)
                store.settle(self.store, rid, req, True, rec["run"], "", t)
            self.stamp("taken", rid)
        self.progress()
        self.save()

    def ask(self, ticket, stray=False):
        t = self.now
        if self.lane == "code":
            if not stray and self.f["move_in_history"]:
                self.history(ticket, OWNER, "st-backlog", "st-todo", t - 3)
            self.move_at = self.move_at or t - 3
            return store.ask(self.store, "queue", "start", "code", "app", ticket, now=t)
        if self.lane == "plan":
            if not stray:
                self.history(ticket, OWNER, "st-idea", "st-planit", t - 3)
            self.move_at = self.move_at or t - 3
            return store.ask(self.store, "planner", "start", "plan", "app", ticket, text="Planning-run trigger: x",
                             now=t)
        if self.lane == "review":
            return store.ask(self.store, "review", "start", "review", "app", ticket, text="facts", now=t,
                             tier="standard")
        if self.lane == "acceptance":
            rid = store.new_request_id()
            if not stray:
                _put(os.path.join(self.acc, "acceptance-run.json"), json.dumps({"schema": "x", "checks": {
                    "ENG-49@" + H0: {"plan": "ENG-49", "head": H0, "request": rid, "piece": ticket, "via": "runner"}}}))
            return store.ask(self.store, "acceptance", "start", "acceptance", "app", ticket, text="facts", now=t,
                             request_id=rid)
        rid = store.ask(self.store, "fix-driver", "resume", "fix", "app", ticket, run=self.code_run["run"],
                        text="Fix the findings.", now=t)
        if not stray:
            ref = rid if self.f["ledger_ref"] == "same" else "rq-" + "9" * 32
            ledger = os.path.join(self.se, "state", "bounce-ledger.jsonl")
            os.makedirs(os.path.dirname(ledger), exist_ok=True)
            with open(ledger, "a") as fh:
                for row in ({"outcome": "spent", "bounce_no": 1, "head_sha": H0, "ticket_id": ticket},
                            {"outcome": "delivered", "bounce_no": 1, "via": "runner", "ref": ref}):
                    fh.write(json.dumps(dict(row, schema="pipeline-bounce-ledger/1", at=store.iso(t), repo=SLUG,
                                             pr=PR_NO)) + "\n")
        return rid

    def start(self, req):
        t, kind, ticket = self.now, req["kind"], req["ticket"]
        pin = None
        if kind in ("code", "fix"):
            pin = os.path.join(self.pins, "pin-%d.json" % t)
            _put(pin, json.dumps({"session_mode": "ticket", "budget": {"maxTurns": 40, "maxUsd": 5.0, "maxMinutes": 60},
                                  "ticket": {"state_id": "st-working", "title": "add the thing",
                                             "acceptance_criteria": ["a", "b", "c"], "out_of_scope": ["x"]}}))
        if kind == "code":
            if ticket in self.tracker["tickets"]:
                self.history(ticket, WRITER, "st-todo", "st-working", t - 2)
            rec = self.record(kind, ticket, req["caller"], req["id"], t, approved_at=self.move_at or t - 3, pin=pin)
        elif kind == "fix":
            c = self.code_run
            rec = self.record(kind, ticket, req["caller"], req["id"], t, how="resumed-run", resumes=c["run"],
                              session=c["session_id"], approved_at=epoch(c["approved_at"]), pin=pin,
                              branch=c["branch"])
        elif kind == "review":
            rec = self.record(kind, ticket, req["caller"], req["id"], t, how="code-run",
                              approved_at=epoch(self.code_run["approved_at"]))
        else:
            rec = self.record(kind, ticket, req["caller"], req["id"], t, approved_at=self.move_at or T0 - 600)
        if pin:
            with open(pin) as fh:
                doc = json.load(fh)
            doc.update(dispatch_id=rec["run"], branch=rec["branch"])
            _put(pin, json.dumps(doc))
        if self.run_id is None and ticket == self.ticket and req["id"] != self.strayed:
            self.run_id = rec["run"]
        return rec

    def progress(self):
        f, t = self.f, self.now
        rec = store.read_run(self.store, self.run_id) if self.run_id else None
        if rec and rec["state"] == store.RUNNING:
            began = epoch(rec["started_at"])
            if t >= began + 60 and "mid" not in self.events:
                self.events.add("mid")
                if self.lane == "code":
                    self.prs[PR_NO] = {"number": PR_NO, "url": "u", "state": "OPEN", "createdAt": store.iso(t),
                                       "headRefName": rec["branch"], "headRefOid": H0, "author": {"login": "pusher"},
                                       "comments": [], "commits": [{"oid": H0, "committedDate": store.iso(t)}]}
                elif self.lane == "fix" and not f["no_push"]:
                    self.prs[PR_NO].update(headRefOid=H1)
                    self.prs[PR_NO]["commits"].append({"oid": H1, "committedDate": store.iso(t)})
                elif self.lane == "acceptance":
                    _put(os.path.join(self.acc, "looks", rec["request"], "ENG-49", "001-x.json"),
                         json.dumps({"schema": "pipeline-look-record/1", "plan": "ENG-49"}))
            if t >= began + 120:
                store.write_run(self.store, dict(rec, state="ended", reason="ended", exit=0, ended_at=store.iso(t),
                                                 seconds=t - began, cost_usd=1.25, turns=30, pid=None))
                if rec["pin"] and os.path.exists(rec["pin"]):
                    os.unlink(rec["pin"])
                if self.lane == "plan":
                    store.write_output(self.store, rec["run"], False, "success", "Here is the plan.", t)
        if rec and rec["state"] == "ended":
            ended = epoch(rec["ended_at"])
            if self.lane == "code" and not f["no_review"] and "review" not in self.events and t >= ended + 90:
                self.events.add("review")
                self.comment(t)
            if self.lane == "review" and "published" not in self.events and t >= ended + 40:
                self.events.add("published")
                self.comment(t)
                _put(os.path.join(self.se, "state", "outcomes", "acme__app__pr-%d.json" % PR_NO),
                     json.dumps({"review_ticket": f["review_ticket"], "head_sha": H0}))
            if self.lane == "acceptance" and "verdict" not in self.events and t >= ended + 30:
                import pipeline_acceptance_poller as poller
                self.events.add("verdict")
                _put(os.path.join(self.acc, "acceptance-ledger.json"), json.dumps({
                    "schema": poller.LEDGER_SCHEMA, "rows": [{
                        "plan": "ENG-49", "head": H0, "status": "started", "at": store.iso(t - 200),
                        "verdict": {"word": "passed", "status": "written", "at": store.iso(t)}}]}))
                self.tracker["comments"].setdefault("ENG-49", []).append({
                    "createdAt": store.iso(t), "user": {"id": "acceptance-bot"},
                    "body": poller.verdict_mark(f["mark_word"], "ENG-49", H0, False) + "\nThe check passed."})


class FakeIO(IO):
    """The seam, stood in for: `sudo -n -u` runs the role's command for real under /bin/sh with the role's home,
    except the git and stat looks, which the world answers; `gh` and the installer's check are the world's."""

    def __init__(self, w, typed="yes", env=None, uid=501, sudo_rc=0):
        self.w, self.typed, self._env, self._uid, self.sudo_rc = w, typed, env or {}, uid, sudo_rc
        self.said, self.asked, self.calls = [], [], []

    def env(self):
        return self._env

    def uid(self):
        return self._uid

    def user(self):
        return "selftest-person"

    def say(self, text=""):
        self.said.append(text)

    def ask(self, question):
        self.asked.append(question)
        return self.typed

    def sudo_validate(self):
        self.w.validations += 1
        self.w.sudo_live = self.sudo_rc == 0 or self.w.sudo_live
        return self.sudo_rc

    def run(self, argv, stdin=None, timeout=120):
        self.calls.append(list(argv))
        w = self.w
        if argv[:3] == ["sudo", "-n", "-v"]:
            return Res(0 if w.sudo_live else 1, "", "" if w.sudo_live else "sudo: a password is required")
        if argv[:3] == ["sudo", "-n", "-u"]:
            if not w.sudo_live or argv[3] != ROLE:
                return Res(1, "", "sudo: a password is required")
            script = argv[-1]
            tag = re.search(r"# live-run:([a-z-]+)", script + "\n" + (stdin or ""))
            if tag:
                return w.shell(tag.group(1), script)
            if "cat >" in script:
                w.writes += 1
            p = subprocess.run(["/bin/sh", "-c", script], input=stdin, capture_output=True, universal_newlines=True,
                               env={"HOME": w.home, "PATH": "/usr/bin:/bin"}, timeout=timeout)
            return Res(p.returncode, p.stdout, p.stderr)
        if argv[:2] == ["gh", "api"]:
            return w.gh_api(argv[2])
        if argv[:1] == ["gh"]:
            return w.gh(argv[1:])
        if len(argv) > 1 and argv[1].endswith("pipeline_runner_setup.py"):
            return w.installer()
        if len(argv) > 1 and argv[1].endswith("check_delivery_config.py"):
            return w.validator(argv)
        return Res(127, "", "not stood in for")

    def read_text(self, path):
        return self.w.plist() if path == _dispatcher_plist("local.pipeline-runner") else None

    def now(self):
        return self.w.now

    def sleep(self, seconds):
        self.w.now += seconds
        self.w.tick()
        at = self.w.f["interrupt_at"]
        if at is not None and self.w.on_seen is not None and self.w.now >= self.w.on_seen + at:
            self.w.f["interrupt_at"] = None
            raise KeyboardInterrupt()

    def role_home(self, account):
        if account != ROLE:
            raise KeyError(account)
        return self.w.home

    def role_scripts(self, clone):
        return [self.w.stub, HERE]


def selftest():
    import tempfile
    fails, ran = [], [0]

    def check_(name, got, want):
        ran[0] += 1
        print("[%s] %s%s" % ("PASS" if got == want else "FAIL", name, "" if got == want else
                             "  (got %r, want %r)" % (got, want)))
        if got != want:
            fails.append(name)
    try:
        _cases(check_, tempfile.mkdtemp(prefix="live-"))
    except Exception as exc:                                      # noqa: BLE001
        import traceback
        traceback.print_exc()
        check_("the battery ran to its end", "%s: %s" % (type(exc).__name__, str(exc)[:300]), "")
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check_, tmp):  # noqa: C901  (one battery, read top to bottom)
    me, count = sys.modules[__name__], [0]

    def world(lane="code", **flags):
        count[0] += 1
        return World(os.path.join(tmp, "w%d" % count[0]), lane, **flags)

    def go(w, command="run", typed="yes", env=None, uid=501, sudo_rc=0, extra=()):
        io = FakeIO(w, typed, env, uid, sudo_rc)
        argv = [command, "--lane", w.lane, "--project", "app", "--conf", w.conf, "--queue-conf", w.qconf]
        argv += (["--ticket", w.ticket] if command != "off" else []) + list(extra)
        return main(argv, io), io

    def lane_now(w):
        with open(w.settings) as fh:
            return json.load(fh)["projects"]["app"]["lanes"][w.kind]

    def result(io):
        text = "\n".join(io.said)
        return text.split("---- result", 1)[1] if "---- result" in text else ""

    def rows(io):
        """{row: state} from the check's printed rows: `  <name, padded to 14> <state, padded to 10> <words>`."""
        out = {}
        for ln in io.said:
            name, state = ln[2:16].strip(), ln[17:27].strip()
            if ln.startswith("  ") and state in (OK, MISSING, UNKNOWN, NOT_NEEDED):
                out[name] = state
        return out

    def case(name, thunk, want, *mutants):
        """The case, then each mutant against it: a mutant must change what the case sees."""
        got = thunk()
        check_(name, got, want)
        for label, patches in mutants:
            undo = [(obj, attr, getattr(obj, attr)) for obj, attr, _v in patches]
            for obj, attr, value in patches:
                setattr(obj, attr, value)
            try:
                other = thunk()
            except Exception as exc:                              # noqa: BLE001
                other = ("raised", type(exc).__name__)
            finally:
                for obj, attr, value in reversed(undo):
                    setattr(obj, attr, value)
            check_("mutant: %s is caught" % label, other != got, True)

    # 1. Refused under a model and as root, before anything is read or asked.
    def agent():
        w = world()
        rc, io = go(w, env={"CLAUDECODE": "1"})
        return rc, io.calls, w.validations, io.said[0].startswith("REFUSED")
    case("an agent's environment is refused before anything is read", agent, (EXIT_REFUSED, [], 0, True),
         ("the agent refusal dropped", [(me, "AGENT_ENV_MARKERS", ())]))
    case("root is refused before anything is read", lambda: (lambda r: (r[0], r[1].calls))(go(world(), uid=0)),
         (EXIT_REFUSED, []), ("the root refusal dropped", [(me, "refused", lambda io: None)]))

    # 2. Arguments, and a sudo that is refused: nothing is read.
    case("a lane it does not know, or a run with no ticket, is a usage error",
         lambda: [main(["run", "--lane", "deploy", "--project", "app", "--ticket", "ENG-1"], FakeIO(world())),
                  main(["run", "--lane", "code", "--project", "app"], FakeIO(world()))], [EXIT_USAGE, EXIT_USAGE])
    case("sudo refused: exit 5, and no role-account read", lambda: (lambda r: (r[0], r[1].calls))(
        go(world(), sudo_rc=1)), (EXIT_NOPRIV, []))

    # 3. check: every prerequisite OK on a ready Mac, and nothing changed.
    def ready_check():
        w = world()
        rc, io = go(w, "check")
        with open(w.settings) as fh:
            same = fh.read() == w.installed
        return rc, set(rows(io).values()), len(rows(io)), w.writes, same, lane_now(w)
    case("check on a ready Mac: all sixteen rows OK, exit 0, nothing written", ready_check,
         (EXIT_OK, {OK}, 16, 0, True, False),
         ("a check that switches", [(me, "row_switch", lambda ctx: (set_lane(ctx, True), "ok")[1])]))

    # 4. Each prerequisite missing, one at a time: its row says so, and nothing is switched.
    def broken(lane="code", **flags):
        w = world(lane, **flags)
        rc, io = go(w)
        return rc, dict((k, v) for k, v in rows(io).items() if v not in (OK, NOT_NEEDED)), w.writes, io.asked

    def broken_io(lane="code", **flags):
        _rc, io = go(world(lane, **flags))
        return io, dict((k, v) for k, v in rows(io).items() if v not in (OK, NOT_NEEDED))
    case("the runner's job not loaded: MISSING, nothing written, nothing asked",
         lambda: broken(installer="job"), (EXIT_MISSING, {"runner": MISSING}, 0, []))
    case("the installer could not measure a row: UNKNOWN, never OK", lambda: broken(installer="unknown")[:2],
         (EXIT_MISSING, {"runner": UNKNOWN}))
    case("the runner switched off: MISSING", lambda: broken(installer="left_off")[1], {"runner": MISSING})
    case("the kit clone behind main: MISSING", lambda: broken(kit_main="e" * 40)[1], {"kit clone": MISSING},
         ("the clone's head not compared", [(me, "row_kit_clone", lambda ctx: (
             ctx.before.update(kinds_commit=KINDS_COMMIT), "ok")[1])]))
    case("an SSH origin: MISSING, and the push's dry run waits on it; GitHub's rows still read",
         lambda: broken(origin="git@github.com:acme/app.git")[1], {"project clone": MISSING, "push": UNKNOWN},
         ("the origin's scheme not judged", [(me, "GITHUB_TOKENISH", re.compile(
             r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?\Z"))]))
    case("origin not reached by the role account's git: MISSING", lambda: broken(reach=False)[1],
         {"project clone": MISSING})
    case("RUNNER_STORE unset for the queue: MISSING", lambda: broken(runner_store="")[1], {"queue store": MISSING},
         ("RUNNER_STORE not read", [(me, "row_queue_store", lambda ctx: "ok")]))
    case("the installed usage job not reading the store yet: MISSING",
         lambda: broken(usage_store="~/elsewhere")[1], {"queue store": MISSING})
    case("the push token's file mode 0644: MISSING", lambda: broken(keys={"push-token": "stat 644 %s 41" % ROLE})[1],
         {"keys": MISSING},
         ("the keys' modes not judged", [(me, "row_keys", lambda ctx: "ok")]))
    case("the tracker key absent, the writer key another account's, the push token empty: each MISSING",
         lambda: [broken(keys={"tracker-key": "absent"})[1], broken(keys={"writer-key": "stat 600 someone 41"})[1],
                  broken(keys={"push-token": "stat 600 %s 0" % ROLE})[1]], [{"keys": MISSING}] * 3)
    case("a lane with no push and no ticket tools needs no dry run, writer key, brief or Todo check",
         lambda: (lambda r: (r[1], sorted(k for k, v in rows(r[0]).items() if v == NOT_NEEDED)))(
             broken_io("plan", push="128", writer_viewer="someone", brief_tools=False, todo=["ENG-77"])),
         ({}, ["brief", "push", "todo", "writer key"]))
    case("the push token cannot push to the project, by dry run: MISSING", lambda: broken(push="128")[1],
         {"push": MISSING},
         ("the dry run not made", [(me, "row_push", lambda ctx: "ok")]))
    case("the dry run hands git the token by the adapter's own helper, as a session's git gets it",
         lambda: rows(go(world(), "check")[1]).get("push"), OK,
         ("a dry run without the adapter's helper", [(harness, "GIT_PUSH_CONFIG", (("credential.helper", ""),
                                                                                    ("credential.helper", "x")))]),
         ("a push that is not a dry run", [(me, "PUSH_SH", PUSH_SH.replace("--dry-run ", ""))]))
    case("the session writer's key answers as another user: MISSING", lambda: broken(writer_viewer="someone")[1],
         {"writer key": MISSING},
         ("the writer's answer not compared", [(me, "row_writer_key", lambda ctx: "ok")]))
    case("the runner runs from a folder the role account could change: MISSING, naming the root-owned copy",
         lambda: (lambda r: (r[1], "root-owned copy of the kit (KIT-919)" in "\n".join(r[0].said)))(
             broken_io(engine_problems=["/opt/kit-engine is writable by this account"])),
         ({"engine": MISSING}, True),
         ("the engine not judged", [(me, "row_engine", lambda ctx: "ok")]))
    case("the project's delivery.json absent on main, or failing the validator: MISSING",
         lambda: [broken(delivery="absent")[1], broken(delivery="invalid")[1]],
         [{"delivery": MISSING, "todo": UNKNOWN}, {"delivery": MISSING}],
         ("the validator's verdict not read", [(me, "row_delivery", lambda ctx: (ctx.before.update(
             delivery={"linear": {"teamKey": "ENG"}}), "ok")[1])]))
    case("the kit's own validator, run for real on the example delivery.json, fails it with its first rule",
         lambda: (lambda r: (r[1], "fails the kit's validator" in "\n".join(r[0].said)))(
             broken_io(delivery="example")), ({"delivery": MISSING}, True))
    case("the project's session brief without the ticket tools: MISSING",
         lambda: broken(brief_tools=False)[1], {"brief": MISSING},
         ("the brief not read", [(me, "row_brief", lambda ctx: "ok")]))
    case("outside the working window: MISSING, saying when it opens", lambda: broken(window_start="16:00")[1],
         {"window": MISSING},
         ("the window not judged", [(me, "row_window", lambda ctx: "ok")]))
    case("another approved ticket waits in Todo: MISSING, naming it",
         lambda: (lambda r: (r[1], "ENG-77" in "\n".join(r[0].said)))(broken_io(todo=["ENG-12", "ENG-77"])),
         ({"todo": MISSING}, True),
         ("what waits not read", [(me, "row_todo", lambda ctx: "ok")]))
    case("the probe's last result with a failed wall: MISSING", lambda: broken(probe_walls="FAIL")[1],
         {"probe": MISSING})
    case("the probe ran at a commit before main's session kinds: MISSING", lambda: broken(ancestor="1")[1],
         {"probe": MISSING},
         ("the commit's age not judged", [(me, "ANCESTOR_SH", "# live-run:none\necho ancestor 0")]))
    case("the probe's results name no commit, or ran as another account: MISSING",
         lambda: [broken(probe_commit=None)[1], broken(probe_account="someone")[1]],
         [{"probe": MISSING}, {"probe": MISSING}])
    case("whether the probe's commit holds the kinds cannot be told: UNKNOWN", lambda: broken(ancestor="128")[1],
         {"probe": UNKNOWN})
    case("the lane already on: MISSING, and the run never asks", lambda: (lambda r: (r[1], r[3]))(
        broken(lanes={"code": True})), ({"lane off": MISSING}, []),
         ("the lane's state not judged", [(me, "row_lane_off", lambda ctx: "ok")]))
    case("no session tools: the writer's key, its check and the runner's own loader each say so",
         lambda: broken(session_tools=False)[1], {"keys": MISSING, "writer key": MISSING, "switch": MISSING},
         ("the loader not asked", [(me, "row_switch", lambda ctx: "ok")]))
    case("the ticket approved already, or its workspace there: MISSING",
         lambda: [broken(ticket_state="st-todo")[1], broken(workspace=True)[1]],
         [{"ticket": MISSING}, {"ticket": MISSING}])
    case("the settings unreadable as the role account: it cannot start, and nothing changes",
         lambda: (lambda w: (lambda r: (r[0], r[1].said[-1].startswith("CANNOT START"), r[1].asked))(go(w)))(
             world(no_role=True)), (EXIT_MISSING, True, []))
    case("UNKNOWN is never ready; NOT NEEDED is",
         lambda: (ready([("runner", OK, ""), ("probe", UNKNOWN, "")]),
                  ready([("runner", OK, ""), ("push", NOT_NEEDED, "")])),
         (False, True), ("UNKNOWN read as ready", [(me, "ready", lambda rows: True)]))

    # 5. run: one typed yes, or nothing changes.
    def not_yes():
        w = world()
        rc, io = go(w, typed="y")
        return rc, w.writes, lane_now(w), len(io.asked)
    case("anything but a typed yes changes nothing", not_yes, (EXIT_NOT_YES, 0, False, 1))

    # 6. The coding lane, end to end: on, the owner's move, a run, its pin, a pull request, its end, the switch
    #    off, Stage E's review, and the three checks.
    def code_run(extra=(), **flags):
        w = world("code", **flags)
        rc, io = go(w, extra=extra)
        return w, rc, io, result(io)

    def happy():
        w, rc, io, text = code_run(stray=True)
        unseen = [ln.split("|")[1].strip() for ln in text.splitlines() if ln.startswith("| ") and "not seen" in ln]
        leaked = PLANTED in "\n".join(io.said) or any(PLANTED in " ".join(c) for c in io.calls)
        return (rc, lane_now(w), w.writes, "**Result: PASSED" in text, unseen, text.count("| AGREE |"),
                "mode ticket; budget 40 turns, 5.0 USD, 60 min; 3 criteria, 1 out of scope" in text,
                "Nothing of the lane started after it; 1 request(s) after it were refused (the code lane is off "
                "for project app)." in text, w.on_seen is not None and w.off_seen is not None, leaked)
    case("the coding lane: every step seen, three checks agree, the lane off at the end, a request after it "
         "refused, and no key in any output or command line",
         happy, (EXIT_OK, False, 2, True, [], 3, True, True, True, False),
         ("the switch off dropped", [(me, "set_lane", lambda ctx, on, real=set_lane: real(ctx, on) if on else None)]),
         ("the checks skipped", [(me, "agree_code", lambda ctx: None)]))
    case("the runner refuses the start: FAILED in its words, and the lane off",
         lambda: (lambda r: (r[1], lane_now(r[0]), "FAILED: the runner refused it: the queue's judge does not admit"
                             in r[3]))(code_run(refuse="the queue's judge does not admit ENG-12 (held)")),
         (EXIT_FAILED, False, True))
    case("Ctrl-C while it waits: the lane off, and said",
         lambda: (lambda r: (r[1], lane_now(r[0]), "stopped by you" in r[3]))(code_run(interrupt_at=50)),
         (EXIT_FAILED, False, True),
         ("no switch off after a failure", [(me, "switch_off", lambda ctx: True)]))
    case("a runner that never answers: the time limit ends it, the lane off",
         lambda: (lambda r: (r[1], lane_now(r[0]), "started did not happen within 5 minutes" in r[3]))(
             code_run(("--minutes", "5"), silent=True)), (EXIT_FAILED, False, True))
    case("a start after the switch off: FAILED, named",
         lambda: (lambda r: (r[1], "started after the switch off" in r[3]))(code_run(stray=True, ignore_off=True)),
         (EXIT_FAILED, True),
         ("nothing-after not checked", [(me, "quiet_check", lambda ctx: True)]))
    case("the tracker holds no move of the owner's: DISAGREE on who approved",
         lambda: (lambda r: (r[1], "| who approved | DISAGREE |" in r[3]))(code_run(move_in_history=False)),
         (EXIT_FAILED, True),
         ("the approval not compared", [(me, "agree_code", lambda ctx: agreed(ctx, "who approved", True, "x"))]))
    case("sudo ends while it waits: your password is asked, and the lane goes off",
         lambda: (lambda r: (r[1], lane_now(r[0]), r[0].validations, "sudo session ended" in r[3]))(
             code_run(lose_sudo_at=T0 + 60)), (EXIT_FAILED, False, 2, True))
    case("the runner never says it takes the lane: FAILED, and the lane written off",
         lambda: (lambda r: (r[1], lane_now(r[0]), "did not say it takes the lane" in r[3]))(
             code_run(("--minutes", "5"), frozen="always")), (EXIT_FAILED, False, True),
         ("the runner's say not awaited", [(me, "confirm", lambda ctx, on: ctx.io.now())]))
    case("the switch off cannot be confirmed: exit 6, and the one command that switches it off",
         lambda: (lambda r: (r[1], "THE LANE MAY STILL BE ON. Run: python3 scripts/pipeline_runner_live.py off "
                                   "--lane code --project app" in r[3]))(code_run(frozen="after-on")),
         (EXIT_STILL_ON, True))
    case("Stage E never reviews the pull request: FAILED, named",
         lambda: (lambda r: (r[1], "reviewed did not happen within 5 minutes of the switch off" in r[3]))(
             code_run(("--after-minutes", "5"), no_review=True)), (EXIT_FAILED, True))
    case("GitHub that cannot be read: the check stops at the rows that read it, and nothing is switched",
         lambda: (lambda r: (r[1], lane_now(r[0]), r[0].writes))(code_run(gh_down=True)), (EXIT_MISSING, False, 0))
    case("GitHub lost while it waits: the pull request never seen, FAILED, the lane off",
         lambda: (lambda r: (r[1], lane_now(r[0]), "no pull request is open on its branch" in r[3]))(
             code_run(gh_after_on=True)), (EXIT_FAILED, False, True))

    # 7. The other lanes, each end to end, and one disagreement each.
    def lane_run(lane, **flags):
        w = world(lane, **flags)
        rc, io = go(w)
        return rc, lane_now(w), result(io)
    case("the fix lane: a resume of the coding session, its push, and the bounce ledger agree",
         lambda: (lambda r: (r[0], r[1], r[2].count("| AGREE |"), "**Result: PASSED" in r[2]))(lane_run("fix")),
         (EXIT_OK, False, 3, True),
         ("the ledger not read", [(me, "agree_fix", lambda ctx: agreed(ctx, "the bounce ledger", True, "skipped"))]))
    case("the fix lane: a ledger row naming another request DISAGREEs",
         lambda: "| the bounce ledger | DISAGREE |" in lane_run("fix", ledger_ref="other")[2], True)
    case("the fix lane: nothing pushed is FAILED",
         lambda: (lambda r: (r[0], "nothing was pushed" in r[2]))(lane_run("fix", no_push=True)), (EXIT_FAILED, True))
    case("the review lane: the published comment, its marks beside the old lane's, and no review ticket",
         lambda: (lambda r: (r[0], r[1], r[2].count("| AGREE |"), "**Result: PASSED" in r[2]))(lane_run("review")),
         (EXIT_OK, False, 3, True),
         ("the review ticket not read", [(me, "agree_review", lambda ctx: None)]))
    case("the review lane: a review ticket filed DISAGREEs; no old comment to compare is NOT CHECKED",
         lambda: ["| no review ticket | DISAGREE |" in lane_run("review", review_ticket="ENG-99")[2],
                  "| same shape and marks | NOT CHECKED |" in lane_run("review", no_old_review=True)[2]], [True, True])
    case("the plan lane: the idea itself planned, its final message kept, the tree left to you",
         lambda: (lambda r: (r[0], r[1], "**Result: PASSED" in r[2], "| the plan document | BY HAND |" in r[2],
                             "**Left to you.**" in r[2]))(lane_run("plan")), (EXIT_OK, False, True, True, True),
         ("BY HAND read as a failure", [(me, "BY_HAND", DISAGREE)]))
    case("the acceptance lane: the run, the look tool's record and the verdict comment agree",
         lambda: (lambda r: (r[0], r[1], r[2].count("| AGREE |"), "**Result: PASSED" in r[2]))(
             lane_run("acceptance")), (EXIT_OK, False, 3, True),
         ("the verdict marks not read", [(me, "agree_acceptance", lambda ctx: None)]))
    case("the acceptance lane: a verdict mark that says otherwise DISAGREEs",
         lambda: "| the verdict comment | DISAGREE |" in lane_run("acceptance", mark_word="held")[2], True)

    # 8. off: one command that only switches the lane off, and confirms it.
    def off():
        w = world(lanes={"code": True})
        w.tick()
        rc, io = go(w, "off")
        w2 = world(lanes={"code": True}, frozen="after-on")
        w2.tick()                                    # the runner says it takes it, then says nothing more
        rc2, _io = go(w2, "off")
        return rc, lane_now(w), rc2, lane_now(w2), io.asked
    case("off: switched off and confirmed, with no yes asked; unconfirmed is exit 6", off,
         (EXIT_OK, False, EXIT_STILL_ON, False, []))

    # 9. Only fields leave the role account: no title, no comment's body, no session's words.
    def fields_only():
        out = []
        for lane in ("code", "plan"):
            w = world(lane)
            _rc, io = go(w)
            out.append("\n".join(io.said))
        said = "\n".join(out)
        return [s in said for s in ("Here is the plan", "A finding.", "add the thing", "Planning-run trigger")]
    case("the session's words, a comment's body and a ticket's title never reach the output", fields_only,
         [False, False, False, False])


if __name__ == "__main__":
    sys.exit(main())
