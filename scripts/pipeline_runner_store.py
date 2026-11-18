#!/usr/bin/env python3
"""The runner's store: start, resume and stop requests, and one record per run (KIT-642,
contract section 28).

    python3 scripts/pipeline_runner_store.py --selftest

WHAT IT IS.  One folder the runner owns (scripts/pipeline_runner.py), outside every git working
tree. Jobs ask the runner for a session by writing one request file into it. The runner answers
each request, and keeps one record for each run. When the tracker on the machine is built, these
move into it; until then this is the whole store.

    <store>/requests/<id>.json     waiting requests. A caller writes one with ask(); nothing else
    <store>/taken/<id>.json        a request the runner acted on, with its answer
    <store>/runs/<run>/run.json    one record per run: the runner's alone to write
    <store>/runs/<run>/...         that run's own files: its command's inputs and its output
    <store>/trees/<run>.json       the processes the runner remembered of a run, to kill what is
                                   left when it ends: the runner's alone, never in the run's own
                                   folder, which the run's processes write (KIT-739)
    <store>/outputs/<run>.json     the session's final message, kept by the runner when the run
                                   ends, for the caller that asked for it (KIT-858): the runner's
                                   alone, for the same reason

WHO MAY ASK.  Five jobs, each for its own kinds of session (CALLERS):

    queue        the queue's starter    code
    review       the review job         review
    planner      the planner job        plan
    fix-driver   the fix driver         fix and conflict, which are resumes of a code run
    acceptance   the acceptance job     acceptance

A caller names itself, and its word is not the authority. Under one account any process that can
write the store can name any caller, so `caller` is reporting: it narrows what a request may ask.
The runner checks every request against the tracker's history before it starts anything
(scripts/pipeline_runner.py). What keeps a session from writing a request at all is the walls below,
and its sandbox. What this file checks is the shape, and that the named caller may ask for that kind.

TEXT AND THE FINAL MESSAGE (KIT-858).  A resume carries its text, and a start may carry one too: the
facts its caller's own code wrote, which the runner hands the session fenced as data, after the
ticket. A stop carries none. When a run ends, the runner keeps the session's final message, its last
OUTPUT_MAX characters, in outputs/<run>.json. The caller reads it with run_output(): a document,
None when the run left no result at all, or StoreError when one is there and cannot be read. The
message is the session's own words, so it is data to its caller and never part of a run's record.

THE WALLS.  A session must never write here: a request it wrote could start a session nobody
approved. So the store must be an absolute path, outside every git working tree, a real folder
(not a link), owned by this account, and writable by no one else. walls() says what is wrong, and
nothing reads or writes a store that fails it.

ABSENCE IS NOT FAILURE (contract section 13).  read_runs() gives `None`, never an empty list, when
the runs folder cannot be listed, and names each record it could not read.

Standard library only. It starts no process and opens no socket.
"""
import json
import os
import re
import stat
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

REQUEST_SCHEMA = "pipeline-runner-request/1"
RUN_SCHEMA = "pipeline-runner-run/1"
ACTIONS = ("start", "resume", "stop")
KINDS = ("code", "fix", "conflict", "review", "plan", "acceptance")
# Each caller's own kinds. A caller may start, resume or stop only these.
CALLERS = {
    "queue": ("code",),
    "review": ("review",),
    "planner": ("plan",),
    "fix-driver": ("fix", "conflict"),
    "acceptance": ("acceptance",),
}
STARTS = ("code", "review", "plan", "acceptance")        # fix and conflict are only resumes
# The review job's name as a caller, which is also its one kind (KIT-644): one spelling, which the
# review job's own code uses rather than writing the word out.
REVIEW = "review"
# A run of kind K may be resumed as kind R when K is in RESUMES[R].
RESUMES = {
    "code": ("code",),
    "fix": ("code", "fix", "conflict"),
    "conflict": ("code", "fix", "conflict"),
    "review": ("review",),
    "plan": ("plan",),
    "acceptance": ("acceptance",),
}
# The usage file's kind for each (scripts/pipeline_usage_runs.py KINDS), for the usage job.
USAGE_KINDS = {"code": "coding", "fix": "bounce_fix", "conflict": "conflict_fix",
               "review": "review", "plan": "planner", "acceptance": "other_later"}

RUNNING, STOPPING = "running", "stopping"
STATES = (RUNNING, STOPPING, "ended", "stopped", "interrupted", "failed")
LIVE = (RUNNING, STOPPING)
# Why a run ended. The runner's limits add their own (KIT-643). `session-gone`: a resume whose
# session the program no longer has (KIT-764).
REASONS = ("ended", "stopped", "interrupted", "could-not-start", "session-gone")
LIMIT_REASONS = ("limit:time", "limit:memory", "limit:disk")
# Why the runner itself started a run: a request, its one resume after an interruption, or its
# one fresh start after a resume found its session gone (KIT-764).
STARTED_FOR = ("request", "interrupted", "session-gone")
RUNNERS_OWN = ("interrupted", "session-gone")
AUTO_RESUME = ("pending", "started", "refused")
# How a run was let start: the owner's move or creation, a resume of a run of the same session,
# or a fresh session in the workspace of a run whose session was gone.
HOW = ("move", "creation", "resumed-run", "fresh-session")
FOLLOWS = ("resumed-run", "fresh-session")              # a run that carries on another run

TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
REQUEST_RE = re.compile(r"^rq-[0-9a-f]{32}\Z")
RUN_RE = re.compile(r"^run-[0-9a-f]{32}\Z")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
ACTOR_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
BRANCH_RE = re.compile(r"^(feat|fix|chore|refactor|docs)/[a-z0-9][a-z0-9-]*\Z")
TEXT_MAX = 20000          # a start's or a resume's text, written by the caller's own code
WHY_MAX = 300
DOC_MAX = 1 << 20         # no request or record is larger than this
# KIT-858: the session's final message, as the runner keeps it. Written without ASCII escapes, each
# character takes at most 6 bytes (a control character's \u escape), so even the worst message
# keeps its document under DOC_MAX.
OUTPUT_SCHEMA = "pipeline-runner-output/1"
OUTPUT_MAX = 150000
OUTPUT_KEYS = ("schema", "run", "at", "is_error", "subtype", "text", "cut")
SUBTYPE_RE = re.compile(r"^[a-z_]{0,40}\Z")
REQUEST_KEYS = ("schema", "id", "at", "caller", "action", "kind", "project", "ticket", "run",
                "text")
RUN_KEYS = ("schema", "run", "request", "caller", "kind", "project", "ticket", "resumes",
            "started_for", "approved_by", "approved_at", "how", "session_id", "workspace",
            "branch", "pin", "pid", "state", "started_at", "ended_at", "seconds", "exit",
            "reason", "why", "cost_usd", "turns", "stop_asked_at", "auto_resume", "resumed_by",
            "gone_session")


class StoreError(Exception):
    """The store cannot be used. The words name what is wrong, never a file's content."""


def iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def may(caller, action, kind):
    """True when this caller may ask for this action on this kind at all."""
    if not isinstance(caller, str) or caller not in CALLERS or action not in ACTIONS \
            or kind not in CALLERS[caller]:
        return False
    return action != "start" or kind in STARTS


def _plain(text, limit):
    """One line of at most `limit` characters with no control character."""
    return (isinstance(text, str) and 0 < len(text) <= limit
            and not any(ord(c) < 32 or ord(c) == 127 for c in text))


def request_problems(doc):
    """Everything wrong with a request's shape, in words. Empty when it is well formed."""
    if not isinstance(doc, dict):
        return ["the request is not an object"]
    bad = []
    extra = sorted(k for k in doc if k not in REQUEST_KEYS)
    if extra:
        bad.append("the request holds a field it may not hold")
    missing = [k for k in REQUEST_KEYS if k not in doc]
    if missing:
        bad.append("the request lacks %s" % missing[0])
        return bad
    if doc["schema"] != REQUEST_SCHEMA:
        bad.append("the request's schema is not %s" % REQUEST_SCHEMA)
    if not (isinstance(doc["id"], str) and REQUEST_RE.match(doc["id"])):
        bad.append("the request's id is not one")
    if not (isinstance(doc["at"], str) and TIME_RE.match(doc["at"])):
        bad.append("the request's time is not one")
    if not (isinstance(doc["caller"], str) and doc["caller"] in CALLERS):
        bad.append("the caller is not one of the five that may ask")
    if doc["action"] not in ACTIONS:
        bad.append("the action is not start, resume or stop")
    if doc["kind"] not in KINDS:
        bad.append("the kind is not one the runner knows")
    if not (isinstance(doc["project"], str) and PROJECT_RE.match(doc["project"])):
        bad.append("the project is not a project key")
    if not (isinstance(doc["ticket"], str) and TICKET_RE.match(doc["ticket"])):
        bad.append("the ticket is not a ticket id")
    if doc["action"] == "start":
        if doc["run"] is not None:
            bad.append("a start names no run")
    elif not (isinstance(doc["run"], str) and RUN_RE.match(doc["run"])):
        bad.append("a %s names the run it is for" % doc["action"])
    text_ok = (isinstance(doc["text"], str) and 0 < len(doc["text"]) <= TEXT_MAX
               and "\0" not in doc["text"])
    if doc["action"] == "resume":
        if not text_ok:
            bad.append("a resume carries its text, at most %d characters" % TEXT_MAX)
    elif doc["action"] == "start":
        if doc["text"] is not None and not text_ok:      # KIT-858: a start's facts, when it has any
            bad.append("a start's text, when it carries one, is 1 to %d characters" % TEXT_MAX)
    elif doc["text"] is not None:
        bad.append("a stop carries no text")
    if not bad and not may(doc["caller"], doc["action"], doc["kind"]):
        bad.append("the %s job may not ask to %s a %s session"
                   % (doc["caller"], doc["action"], doc["kind"]))
    return bad


def _opt(doc, key, ok):
    return doc[key] is None or ok(doc[key])


def _num(v, low=0):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= low


def run_problems(doc):
    """Everything wrong with a run record's shape, in words. Empty when it is well formed."""
    if not isinstance(doc, dict):
        return ["the record is not an object"]
    if sorted(doc) != sorted(RUN_KEYS):
        return ["the record's fields are not the run record's"]
    t = lambda v: isinstance(v, str) and bool(TIME_RE.match(v))   # noqa: E731
    checks = (
        ("schema", doc["schema"] == RUN_SCHEMA),
        ("run", isinstance(doc["run"], str) and bool(RUN_RE.match(doc["run"]))),
        ("request", _opt(doc, "request", lambda v: isinstance(v, str) and bool(REQUEST_RE.match(v)))),
        ("caller", isinstance(doc["caller"], str) and (doc["caller"] in CALLERS or (
            doc["caller"] == "runner" and doc["started_for"] in RUNNERS_OWN))),
        ("kind", doc["kind"] in KINDS),
        ("project", isinstance(doc["project"], str) and bool(PROJECT_RE.match(doc["project"]))),
        ("ticket", isinstance(doc["ticket"], str) and bool(TICKET_RE.match(doc["ticket"]))),
        ("resumes", _opt(doc, "resumes", lambda v: isinstance(v, str) and bool(RUN_RE.match(v)))),
        ("started_for", doc["started_for"] in STARTED_FOR),
        ("approved_by", isinstance(doc["approved_by"], str) and bool(ACTOR_RE.match(doc["approved_by"]))),
        ("approved_at", _opt(doc, "approved_at", t)),
        ("how", doc["how"] in HOW),
        ("session_id", isinstance(doc["session_id"], str) and bool(UUID_RE.match(doc["session_id"]))),
        ("workspace", isinstance(doc["workspace"], str) and os.path.isabs(doc["workspace"])),
        ("branch", isinstance(doc["branch"], str) and bool(BRANCH_RE.match(doc["branch"]))),
        ("pin", _opt(doc, "pin", lambda v: isinstance(v, str) and os.path.isabs(v))),
        ("pid", _opt(doc, "pid", lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 1)),
        ("state", doc["state"] in STATES),
        ("started_at", t(doc["started_at"])),
        ("ended_at", _opt(doc, "ended_at", t)),
        ("seconds", _opt(doc, "seconds", _num)),
        ("exit", _opt(doc, "exit", lambda v: isinstance(v, int) and not isinstance(v, bool))),
        ("reason", _opt(doc, "reason", lambda v: v in REASONS or v in LIMIT_REASONS)),
        ("why", _opt(doc, "why", lambda v: _plain(v, WHY_MAX))),
        ("cost_usd", _opt(doc, "cost_usd", _num)),
        ("turns", _opt(doc, "turns", lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0)),
        ("stop_asked_at", _opt(doc, "stop_asked_at", t)),
        ("auto_resume", _opt(doc, "auto_resume", lambda v: v in AUTO_RESUME)),
        ("resumed_by", _opt(doc, "resumed_by", lambda v: isinstance(v, str) and bool(RUN_RE.match(v)))),
        ("gone_session", _opt(doc, "gone_session",
                              lambda v: isinstance(v, str) and bool(UUID_RE.match(v)))),
    )
    bad = ["the record's %s is not valid" % name for name, ok in checks if not ok]
    if not bad:
        live = doc["state"] in LIVE
        at_limit = doc["state"] == STOPPING and doc["reason"] in LIMIT_REASONS
        if live != (doc["ended_at"] is None) or (live and doc["reason"] is not None and not at_limit) \
                or (not live and doc["reason"] is None):
            bad.append("a live run has no end, and an ended one has its end and reason")
        if (doc["resumes"] is None) != (doc["how"] not in FOLLOWS):
            bad.append("a resumed run names the run it resumed, and only it")
        fresh = doc["how"] == "fresh-session"
        if fresh != (doc["gone_session"] is not None) or fresh != (doc["started_for"] == "session-gone"):
            bad.append("a fresh start names the session it could not resume, and only it")
    return bad


# --------------------------------------------------------------------------- #
# The walls, and whole-file writes
# --------------------------------------------------------------------------- #
def inside_git_worktree(path):
    cur = os.path.realpath(path)
    while True:
        if os.path.lexists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def walls(path):
    """What makes `path` no place for the store, in words: empty when it may hold it."""
    if not isinstance(path, str) or not os.path.isabs(path) or "\0" in path:
        return ["the store must be an absolute path"]
    if inside_git_worktree(path if os.path.lexists(path) else os.path.dirname(path)):
        return ["the store is inside a git working tree, where a session can write"]
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return ["the store does not exist"]
    except OSError as exc:
        return ["the store could not be looked at (%s)" % type(exc).__name__]
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return ["the store is a link or not a folder"]
    if st.st_uid != os.getuid():
        return ["the store belongs to another account"]
    if st.st_mode & 0o077:
        return ["others can read or write the store: it must be mode 0700"]
    return []


FOLDERS = ("requests", "taken", "runs", "trees", "outputs")


def prepare(store):
    """Make the store and its five folders, mode 0700, and check every one. Returns problems."""
    for path in (store,) + tuple(os.path.join(store, name) for name in FOLDERS):
        if not os.path.lexists(path):
            try:
                os.makedirs(path, mode=0o700)
            except OSError as exc:
                return ["the store could not be made (%s)" % type(exc).__name__]
        bad = walls(path)
        if bad:
            return bad
    return []


def write_json(path, doc, ensure_ascii=True):
    """One file, whole or not at all, mode 0600, never through a link. `ensure_ascii` False writes
    text as UTF-8 rather than escapes, for a document whose size is bounded by its characters."""
    tmp = "%s.%s.tmp" % (path, uuid.uuid4().hex[:12])
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True, ensure_ascii=ensure_ascii)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    """The document at `path`, or StoreError. Never through a link; a regular file of at most
    DOC_MAX bytes."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise StoreError("it could not be opened (%s)" % type(exc).__name__)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > DOC_MAX:
            raise StoreError("it is not a regular file of at most %d bytes" % DOC_MAX)
        with os.fdopen(fd, encoding="utf-8", closefd=False) as fh:
            return json.load(fh)
    except (OSError, ValueError, RecursionError) as exc:
        raise StoreError("it could not be read (%s)" % type(exc).__name__)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
def ask(store, caller, action, kind, project, ticket, run=None, text=None, now=None):
    """Write one request, and return its id. ValueError, in words, when it is not one this caller
    may write, or the store fails its walls. The caller's own code writes `text`; the runner hands
    it to the session fenced as data."""
    doc = {"schema": REQUEST_SCHEMA, "id": "rq-" + uuid.uuid4().hex,
           "at": iso(time.time() if now is None else now), "caller": caller, "action": action,
           "kind": kind, "project": project, "ticket": ticket, "run": run, "text": text}
    bad = request_problems(doc)
    if bad:
        raise ValueError(bad[0])
    folder = os.path.join(store, "requests")
    bad = walls(store) or walls(folder)
    if bad:
        raise ValueError(bad[0])
    write_json(os.path.join(folder, doc["id"] + ".json"), doc)
    return doc["id"]


def pending(store):
    """[(id, doc or None, problems)] oldest first. StoreError when the folder cannot be listed."""
    folder = os.path.join(store, "requests")
    bad = walls(folder)
    if bad:
        raise StoreError(bad[0])
    try:
        names = sorted(os.listdir(folder))
    except OSError as exc:
        raise StoreError("the requests could not be listed (%s)" % type(exc).__name__)
    out = []
    for name in names:
        if not name.endswith(".json") or not REQUEST_RE.match(name[:-5]):
            continue
        try:
            doc = read_json(os.path.join(folder, name))
        except StoreError as exc:
            out.append((name[:-5], None, ["the request could not be read: %s" % exc]))
            continue
        try:
            problems = request_problems(doc)
        except (TypeError, ValueError, AttributeError):   # never one file stopping every pass
            problems = ["the request could not be judged"]
        out.append((name[:-5], doc, problems))
    return sorted(out, key=lambda i: ((i[1] or {}).get("at") if isinstance(i[1], dict)
                                      and isinstance(i[1].get("at"), str) else "", i[0]))


def settle(store, request_id, doc, ok, run, why, now):
    """Record the runner's answer to a request, then remove it from the waiting ones."""
    write_json(os.path.join(store, "taken", request_id + ".json"),
               {"request": doc, "answer": {"at": iso(now), "ok": bool(ok), "run": run,
                                           "why": why[:WHY_MAX] if why else ""}})
    try:
        os.unlink(os.path.join(store, "requests", request_id + ".json"))
    except FileNotFoundError:
        pass


def taken_request(store, request_id):
    """A request the runner acted on, as it was asked (KIT-764: a fresh start reads its words back
    from here). None when it is not there, cannot be read, or is not a well-formed request."""
    if not (isinstance(request_id, str) and REQUEST_RE.match(request_id)):
        return None
    try:
        doc = read_json(os.path.join(store, "taken", request_id + ".json"))
    except StoreError:
        return None
    req = doc.get("request") if isinstance(doc, dict) else None
    try:
        return req if isinstance(req, dict) and not request_problems(req) else None
    except (TypeError, ValueError, AttributeError):
        return None


def answer(store, request_id):
    """The runner's answer to a request: {"ok", "run", "why", "at"}, or None while it waits."""
    if not (isinstance(request_id, str) and REQUEST_RE.match(request_id)):
        raise ValueError("not a request id")
    path = os.path.join(store, "taken", request_id + ".json")
    if not os.path.lexists(path):
        return None
    doc = read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("answer"), dict):
        raise StoreError("the answer is not an object")
    return doc["answer"]


# --------------------------------------------------------------------------- #
# Run records
# --------------------------------------------------------------------------- #
def run_dir(store, run_id):
    if not (isinstance(run_id, str) and RUN_RE.match(run_id)):
        raise ValueError("not a run id")
    return os.path.join(store, "runs", run_id)


def tree_path(store, run_id):
    """Where the runner keeps the processes it remembered of a run (KIT-739): in its own folder,
    never the run's, whose files the run's own processes write. What it holds is the limits
    module's record (scripts/pipeline_runner_limits.py)."""
    if not (isinstance(run_id, str) and RUN_RE.match(run_id)):
        raise ValueError("not a run id")
    return os.path.join(store, "trees", run_id + ".json")


def write_run(store, rec):
    """The runner's one way to write a record. ValueError when it is not well formed."""
    bad = run_problems(rec)
    if bad:
        raise ValueError(bad[0])
    folder = run_dir(store, rec["run"])
    if not os.path.isdir(folder):
        os.makedirs(folder, mode=0o700)
    write_json(os.path.join(folder, "run.json"), rec)


def read_run(store, run_id):
    """One run's record, or None when there is none or it is not well formed."""
    try:
        doc = read_json(os.path.join(run_dir(store, run_id), "run.json"))
        return doc if not run_problems(doc) else None
    except (StoreError, TypeError, ValueError, AttributeError):
        return None


def read_runs(store):
    """Every run, for the runner and the usage job: {"state", "runs", "why"}. `state` is "read",
    "partly-read" (a record could not be read, and is named in `why`) or "could-not-read", where
    `runs` is None, never an empty list."""
    folder = os.path.join(store, "runs")
    bad = walls(folder)
    if bad:
        return {"state": "could-not-read", "runs": None, "why": bad[0]}
    try:
        names = sorted(n for n in os.listdir(folder) if RUN_RE.match(n))
    except OSError as exc:
        return {"state": "could-not-read", "runs": None,
                "why": "the runs could not be listed (%s)" % type(exc).__name__}
    runs, unread = [], []
    for name in names:
        rec = read_run(store, name)
        if rec is None:
            unread.append(name)
        else:
            runs.append(rec)
    runs.sort(key=lambda r: (r["started_at"], r["run"]))
    if unread:
        return {"state": "partly-read", "runs": runs,
                "why": "%d record(s) could not be read, the first %s" % (len(unread), unread[0])}
    return {"state": "read", "runs": runs, "why": ""}


# --------------------------------------------------------------------------- #
# The session's final message (KIT-858)
# --------------------------------------------------------------------------- #
def output_path(store, run_id):
    """Where the runner keeps a run's final message: in its own folder, never the run's, whose
    files the run's own processes write."""
    if not (isinstance(run_id, str) and RUN_RE.match(run_id)):
        raise ValueError("not a run id")
    return os.path.join(store, "outputs", run_id + ".json")


def output_problems(doc):
    """Everything wrong with a kept final message's shape, in words. Empty when it is well formed."""
    if not isinstance(doc, dict):
        return ["the output is not an object"]
    if sorted(doc) != sorted(OUTPUT_KEYS):
        return ["the output's fields are not an output's"]
    text = doc["text"]
    checks = (
        ("schema", doc["schema"] == OUTPUT_SCHEMA),
        ("run", isinstance(doc["run"], str) and bool(RUN_RE.match(doc["run"]))),
        ("at", isinstance(doc["at"], str) and bool(TIME_RE.match(doc["at"]))),
        ("is_error", isinstance(doc["is_error"], bool)),
        ("subtype", isinstance(doc["subtype"], str) and bool(SUBTYPE_RE.match(doc["subtype"]))),
        ("text", text is None or (isinstance(text, str) and len(text) <= OUTPUT_MAX)),
        ("cut", isinstance(doc["cut"], bool) and not (doc["cut"] and text is None)),
    )
    return ["the output's %s is not valid" % name for name, ok in checks if not ok]


def write_output(store, run_id, is_error, subtype, text, now):
    """Keep a run's final message: the runner's one way to write it, when the run ends. `text` is
    the program's result text, or None when its result carried none. A longer one keeps its last
    OUTPUT_MAX characters and says it was cut, since the block a caller reads ends the message. A
    character UTF-8 cannot carry (a lone surrogate) becomes '?'. ValueError when the run id or a
    field is not an output's."""
    cut = isinstance(text, str) and len(text) > OUTPUT_MAX
    if isinstance(text, str):
        text = text[-OUTPUT_MAX:].encode("utf-8", "replace").decode("utf-8")
    else:
        text = None
    doc = {"schema": OUTPUT_SCHEMA, "run": run_id, "at": iso(now), "is_error": bool(is_error),
           "subtype": subtype if isinstance(subtype, str) and SUBTYPE_RE.match(subtype) else "",
           "text": text, "cut": cut}
    bad = output_problems(doc)
    if bad:
        raise ValueError(bad[0])
    write_json(output_path(store, run_id), doc, ensure_ascii=False)
    return doc


def run_output(store, run_id):
    """A run's final message, as the runner kept it: the document, or None when there is none (the
    run has not ended, or it left no result at all). StoreError when one is there and cannot be
    read, or is not an output: "could not read it" is never "none" (contract section 13). Read it
    once the run's record says it has ended."""
    path = output_path(store, run_id)
    if not os.path.lexists(path):
        return None
    doc = read_json(path)
    bad = output_problems(doc)
    if bad:
        raise StoreError(bad[0])
    if doc["run"] != run_id:
        raise StoreError("the output is another run's")
    return doc


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    from scratch_run import contained
    return contained(_selftest, "runner-store-selftest-")


def _selftest():  # noqa: C901  (one battery, read top to bottom)
    import tempfile
    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    def refused(fn):
        try:
            fn()
        except (ValueError, StoreError) as exc:
            return str(exc)
        return "not refused"

    root = os.path.realpath(tempfile.mkdtemp(prefix="store-"))
    store = os.path.join(root, "store")
    expect("a missing store is said", walls(store), ["the store does not exist"])
    expect("prepare makes the store and its folders", prepare(store), [])
    expect("each folder is mode 0700", sorted({stat.S_IMODE(os.stat(os.path.join(store, d)).st_mode)
                                                for d in ("",) + FOLDERS}), [0o700])
    expect("a run's remembered tree is in the runner's own folder, not the run's",
           (tree_path(store, "run-" + "a" * 32), tree_path(store, "run-" + "a" * 32).startswith(
               run_dir(store, "run-" + "a" * 32))),
           (os.path.join(store, "trees", "run-" + "a" * 32 + ".json"), False))
    expect("a tree path is only ever a run's", refused(lambda: tree_path(store, "../x")), "not a run id")

    # The walls: a git working tree, a link, a folder others can write.
    git_tree = os.path.join(root, "repo")
    os.makedirs(os.path.join(git_tree, ".git"))
    expect("a store in a git working tree is refused", prepare(os.path.join(git_tree, "s")),
           ["the store is inside a git working tree, where a session can write"])
    link = os.path.join(root, "link")
    os.symlink(store, link)
    expect("a store reached through a link is refused", walls(link),
           ["the store is a link or not a folder"])
    loose = os.path.join(root, "loose")
    os.makedirs(loose)
    os.chmod(loose, 0o777)
    expect("a store others can write is refused", walls(loose),
           ["others can read or write the store: it must be mode 0700"])
    os.chmod(loose, 0o755)
    expect("a store others can read is refused too: its runs hold the fenced ticket", walls(loose),
           ["others can read or write the store: it must be mode 0700"])
    expect("a relative store is refused", walls("store"), ["the store must be an absolute path"])

    # Who may ask for what.
    expect("the queue may start code", may("queue", "start", "code"), True)
    expect("the queue may not start a review", may("queue", "start", "review"), False)
    expect("the fix driver may not start a fix: a fix is a resume", may("fix-driver", "start", "fix"), False)
    expect("the fix driver may resume as a fix", may("fix-driver", "resume", "fix"), True)
    expect("the fix driver may resume as a conflict fix", may("fix-driver", "resume", "conflict"), True)
    expect("an unknown caller may do nothing", may("session", "start", "code"), False)
    expect("each caller may stop its own kinds",
           [may(c, "stop", k) for c in CALLERS for k in CALLERS[c]], [True] * 6)
    expect("every usage kind is one the usage job counts", _usage_kinds_known(), True)

    rid = ask(store, "queue", "start", "code", "app", "ENG-1", now=1700000000)
    expect("ask writes one waiting request", [i[0] for i in pending(store)], [rid])
    doc = pending(store)[0][1]
    expect("the request is well formed", request_problems(doc), [])
    expect("a start may carry its caller's text, up to the limit (KIT-858)",
           request_problems(dict(doc, caller="planner", kind="plan", text="x" * TEXT_MAX)), [])
    expect("the caller's own clock is not asked for: the time is the writer's", doc["at"],
           "2023-11-14T22:13:20Z")
    expect("a request file is mode 0600",
           stat.S_IMODE(os.stat(os.path.join(store, "requests", rid + ".json")).st_mode), 0o600)
    expect("no answer while it waits", answer(store, rid), None)
    with open(os.path.join(store, "taken", "rq-" + "e" * 32 + ".json"), "w") as fh:
        fh.write("[]")
    expect("an answer file that is not an object is named, never a crash",
           refused(lambda: answer(store, "rq-" + "e" * 32)), "the answer is not an object")
    for name, fn, want in (
            ("another job's kind", lambda: ask(store, "review", "start", "code", "app", "ENG-1"),
             "the review job may not ask to start a code session"),
            ("an unknown caller", lambda: ask(store, "session", "start", "code", "app", "ENG-1"),
             "the caller is not one of the five that may ask"),
            ("a bad ticket id", lambda: ask(store, "queue", "start", "code", "app", "eng-1"),
             "the ticket is not a ticket id"),
            ("a bad project key", lambda: ask(store, "queue", "start", "code", "App", "ENG-1"),
             "the project is not a project key"),
            ("a resume with no run", lambda: ask(store, "fix-driver", "resume", "fix", "app", "ENG-1",
                                                 text="x"), "a resume names the run it is for"),
            ("a resume with no text", lambda: ask(store, "fix-driver", "resume", "fix", "app", "ENG-1",
                                                  run="run-" + "a" * 32), "a resume carries its text, at most 20000 characters"),
            ("a stop with text", lambda: ask(store, "queue", "stop", "code", "app", "ENG-1",
                                             run="run-" + "a" * 32, text="x"),
             "a stop carries no text"),
            ("a start's text too long", lambda: ask(store, "planner", "start", "plan", "app", "ENG-1",
                                                    text="x" * (TEXT_MAX + 1)),
             "a start's text, when it carries one, is 1 to 20000 characters"),
            ("a start's text that is empty", lambda: ask(store, "planner", "start", "plan", "app",
                                                         "ENG-1", text=""),
             "a start's text, when it carries one, is 1 to 20000 characters"),
            ("a text too long", lambda: ask(store, "fix-driver", "resume", "fix", "app", "ENG-1",
                                            run="run-" + "a" * 32, text="x" * (TEXT_MAX + 1)),
             "a resume carries its text, at most 20000 characters"),
            ("a store inside a git tree", lambda: ask(os.path.join(git_tree, "s"), "queue", "start",
                                                      "code", "app", "ENG-1"),
             "the store is inside a git working tree, where a session can write")):
        expect("ask refuses %s" % name, refused(fn), want)
    expect("no refused ask wrote a request", len(pending(store)), 1)

    # A request with a field it may not hold, or a broken file, is said, not acted on.
    with open(os.path.join(store, "requests", "rq-" + "b" * 32 + ".json"), "w") as fh:
        fh.write("{not json")
    forged = dict(doc, id="rq-" + "c" * 32, extra=1)
    write_json(os.path.join(store, "requests", forged["id"] + ".json"), forged)
    odd = dict(doc, id="rq-" + "a" * 32, caller=[])
    write_json(os.path.join(store, "requests", odd["id"] + ".json"), odd)
    expect("a caller that is a list is named, never a crash", may([], "start", "code"), False)
    got = {i[0]: i[2][:1] for i in pending(store)}
    expect("…and the request is named, not a crash of every pass", got["rq-" + "a" * 32],
           ["the caller is not one of the five that may ask"])
    expect("a broken request is named", got["rq-" + "b" * 32],
           ["the request could not be read: it could not be read (JSONDecodeError)"])
    expect("an extra field is named", got["rq-" + "c" * 32],
           ["the request holds a field it may not hold"])

    settle(store, rid, doc, True, "run-" + "d" * 32, "started", 1700000001)
    expect("a settled request leaves the waiting ones", rid in [i[0] for i in pending(store)], False)
    expect("its answer is kept", answer(store, rid),
           {"at": "2023-11-14T22:13:21Z", "ok": True, "run": "run-" + "d" * 32, "why": "started"})
    expect("…and the request as it was asked, for a fresh start to read back (KIT-764)",
           (taken_request(store, rid), taken_request(store, "rq-" + "0" * 32), taken_request(store, "../x")),
           (doc, None, None))
    write_json(os.path.join(store, "taken", "rq-" + "9" * 32 + ".json"),
               {"request": dict(doc, caller="session"), "answer": {}})
    expect("…never one that is not a request a caller may make",
           taken_request(store, "rq-" + "9" * 32), None)

    # Run records.
    rec = dict((k, None) for k in RUN_KEYS)
    rec.update(schema=RUN_SCHEMA, run="run-" + "e" * 32, request=rid, caller="queue", kind="code",
               project="app", ticket="ENG-1", started_for="request", approved_by="owner-1",
               approved_at="2023-11-14T22:00:00Z", how="move",
               session_id="0b0e5e2c-1111-4222-8333-944445555666", workspace=os.path.join(root, "w"),
               branch="feat/eng-1-x", state=RUNNING, started_at="2023-11-14T22:13:21Z")
    expect("a running record is well formed", run_problems(rec), [])
    write_run(store, rec)
    expect("it reads back", read_run(store, rec["run"]), rec)
    expect("a record that names an end while running is refused",
           refused(lambda: write_run(store, dict(rec, ended_at="2023-11-14T22:20:00Z"))),
           "a live run has no end, and an ended one has its end and reason")
    expect("a record for a caller that may not ask is refused",
           refused(lambda: write_run(store, dict(rec, caller="runner"))), "the record's caller is not valid")
    expect("the runner's own resume after an interruption names it",
           run_problems(dict(rec, caller="runner", started_for="interrupted", how="resumed-run",
                             resumes="run-" + "f" * 32)), [])
    fresh = dict(rec, caller="runner", started_for="session-gone", how="fresh-session",
                 resumes="run-" + "f" * 32, gone_session="0b0e5e2c-1111-4222-8333-944445555777")
    expect("its fresh start after a resume found its session gone names that session (KIT-764)",
           run_problems(fresh), [])
    fresh_rule = ["a fresh start names the session it could not resume, and only it"]
    expect("…and a fresh start that names no gone session is not a record",
           run_problems(dict(fresh, gone_session=None)), fresh_rule)
    expect("…nor is any other run that names one",
           run_problems(dict(rec, gone_session=fresh["gone_session"])), fresh_rule)
    expect("…nor a gone session that is not a session id",
           run_problems(dict(fresh, gone_session="../x")), ["the record's gone_session is not valid"])
    expect("a resume whose session was gone ends with that reason",
           run_problems(dict(rec, state="ended", ended_at="2023-11-14T22:20:00Z",
                             reason="session-gone", why="gone", how="resumed-run",
                             resumes="run-" + "f" * 32, auto_resume="pending")), [])
    stopping = dict(rec, state=STOPPING, stop_asked_at="2023-11-14T22:20:00Z")
    live_rule = ["a live run has no end, and an ended one has its end and reason"]
    expect("a run being stopped at a limit carries that limit",
           run_problems(dict(stopping, reason="limit:memory")), [])
    expect("a running run carries no reason, a limit included",
           run_problems(dict(rec, reason="limit:memory")), live_rule)
    expect("a run being stopped on request carries no reason yet",
           run_problems(dict(stopping, reason="stopped")), live_rule)
    expect("only the three limits are limits",
           run_problems(dict(stopping, reason="limit:anything")), ["the record's reason is not valid"])
    expect("a why with a line break is refused",
           run_problems(dict(rec, state="ended", ended_at="2023-11-14T22:20:00Z", reason="ended",
                             why="two\nlines")), ["the record's why is not valid"])
    ended = dict(rec, run="run-" + "1" * 32, state="ended", ended_at="2023-11-14T22:20:00Z",
                 reason="ended", why="the program ended with exit 0", exit=0, seconds=399.0,
                 cost_usd=0.25, turns=7, started_at="2023-11-14T22:14:00Z")
    write_run(store, ended)
    got = read_runs(store)
    expect("read_runs gives every run, oldest first", (got["state"], [r["run"] for r in got["runs"]]),
           ("read", [rec["run"], ended["run"]]))
    with open(os.path.join(store, "runs", "run-" + "2" * 32 + ".tmp"), "w") as fh:
        fh.write("x")                        # not a run folder: passed over
    os.makedirs(os.path.join(store, "runs", "run-" + "3" * 32))
    with open(os.path.join(store, "runs", "run-" + "3" * 32, "run.json"), "w") as fh:
        fh.write("[]")
    os.makedirs(os.path.join(store, "runs", "run-" + "4" * 32))
    write_json(os.path.join(store, "runs", "run-" + "4" * 32, "run.json"),
               dict(ended, run="run-" + "4" * 32, caller={}))
    expect("a record whose caller is an object is not a record", run_problems(dict(ended, caller={})),
           ["the record's caller is not valid"])
    got = read_runs(store)
    expect("a record that cannot be read is named, and the rest are given",
           (got["state"], len(got["runs"]), got["why"]),
           ("partly-read", 2, "2 record(s) could not be read, the first run-" + "3" * 32))
    os.chmod(os.path.join(store, "runs"), 0o777)
    got = read_runs(store)
    expect("a runs folder that fails its walls gives None, never an empty list",
           (got["state"], got["runs"]), ("could-not-read", None))
    os.chmod(os.path.join(store, "runs"), 0o700)

    # The session's final message, kept by the runner when a run ends (KIT-858).
    one, two = "run-" + "5" * 32, "run-" + "6" * 32
    expect("a final message is kept in the runner's own folder, never the run's",
           (output_path(store, one), output_path(store, one).startswith(run_dir(store, one))),
           (os.path.join(store, "outputs", one + ".json"), False))
    expect("an output path is only ever a run's", refused(lambda: output_path(store, "../x")),
           "not a run id")
    expect("a run that left no result has none: None, never an empty message",
           run_output(store, one), None)
    message = "Here is the plan.\n```json\n{\"x\": \"é ✓ </untrusted-x>\"}\n```"
    write_output(store, one, False, "success", message, 1700000100)
    expect("the final message reads back exactly, whole and not cut", run_output(store, one),
           {"schema": OUTPUT_SCHEMA, "run": one, "at": "2023-11-14T22:15:00Z", "is_error": False,
            "subtype": "success", "text": message, "cut": False})
    expect("an output file is mode 0600", stat.S_IMODE(os.stat(output_path(store, one)).st_mode), 0o600)
    write_output(store, two, False, "success", "x" * OUTPUT_MAX + "the block at the end", 1700000100)
    got = run_output(store, two)
    expect("a longer message keeps its end, where its block is, and says it was cut",
           (len(got["text"]), got["text"].endswith("the block at the end"), got["cut"]),
           (OUTPUT_MAX, True, True))
    for name, text in (("control characters", "\x01" * OUTPUT_MAX),
                       ("characters past the first plane", "\U0001f600" * OUTPUT_MAX)):
        write_output(store, two, False, "success", text, 1700000100)
        expect("the largest message of %s still fits a document the store reads back" % name,
               (os.path.getsize(output_path(store, two)) <= DOC_MAX, run_output(store, two)["text"] == text),
               (True, True))
    write_output(store, two, True, "error_max_turns", None, 1700000100)
    got = run_output(store, two)
    expect("a result that carried no message is kept as one with none, never an empty one",
           (got["text"], got["is_error"], got["subtype"], got["cut"]), (None, True, "error_max_turns", False))
    write_output(store, two, False, "success", "a\ud800b", 1700000100)
    expect("a character UTF-8 cannot carry becomes '?', never a crash", run_output(store, two)["text"], "a?b")
    write_output(store, two, False, "Not A Subtype", "x", 1700000100)
    expect("a subtype that is not one is kept as none", run_output(store, two)["subtype"], "")
    expect("an output for no run is refused", refused(lambda: write_output(store, "run-x", False, "", "x", 0)),
           "the output's run is not valid")
    with open(output_path(store, two), "w") as fh:
        fh.write("{not json")
    expect("an output that cannot be read is an error, never none",
           refused(lambda: run_output(store, two)), "it could not be read (JSONDecodeError)")
    write_json(output_path(store, two), run_output(store, one))
    expect("an output filed under another run is an error", refused(lambda: run_output(store, two)),
           "the output is another run's")
    write_json(output_path(store, two), dict(run_output(store, one), run=two, extra=1))
    expect("an output with a field it may not hold is an error",
           refused(lambda: run_output(store, two)), "the output's fields are not an output's")
    expect("an output cut with no text is not one",
           output_problems(dict(run_output(store, one), text=None, cut=True)), ["the output's cut is not valid"])

    if failures:
        print("FAIL: pipeline_runner_store selftest")
        for f in failures:
            print("  - " + f)
        return 1
    print("OK: pipeline_runner_store selftest")
    return 0


def _usage_kinds_known():
    """The usage job's own list of kinds holds every one this store names (KIT-422)."""
    sys.path.insert(0, HERE)
    import pipeline_usage_runs as usage
    return set(USAGE_KINDS.values()) <= set(usage.KINDS) and set(USAGE_KINDS) == set(KINDS)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        sys.path.insert(0, HERE)
        return selftest()
    print(__doc__.split("\n\n")[0])
    print("usage: pipeline_runner_store.py --selftest   (the runner and its callers import it)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
