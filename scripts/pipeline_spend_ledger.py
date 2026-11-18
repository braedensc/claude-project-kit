#!/usr/bin/env python3
"""The spend ledgers: a row for every model call that no dispatcher log records (KIT-844,
KIT-687, contract §30).

    from pipeline_spend_ledger import row_from_result, append, read
    row_from_result(caller, doc, billing, model)  a row from the claude CLI's JSON output
    append(path, row, now)         THE ONE WRITER: one line at the ledger's end, in one write
    read(path, caller, since)      THE ONE READER: (note, calls), or why there are none to count

    python3 scripts/pipeline_spend_ledger.py --selftest

WHY.  The usage job (contract §17) counts what the dispatcher's sessions spent from their own
logs. Four callers start the claude CLI themselves and leave no such log: the morning job's
planner call, the stand-up's turn, the tech lead's turns and the conflict waker's fix sessions.
So each keeps a small ledger of its own, in its own state folder, and the usage job reads every
one of them through `read()`, beside the logs.

A ROW.  One model call: when it ended, who made it, the model, the CLI's own cost estimate, the
tokens as the CLI's output reports them, the CLI's session id when it gave one, and how the call
was paid for. Fields only: numbers, closed words, a model name that matches a strict pattern, a
session id and a time. A cost the caller could not read is null, never 0.

THE WRITER.  `append()` stamps the row's time, checks the row, and adds it as one line at the end
of the ledger, in one write, never through a link. It refuses a row that is not one, and a ledger
inside a git working tree, and writes nothing then. A caller says in its own output that a row
could not be added: the call happened, and its spend is then missing from the usage file.

THE READER.  `read()` gives a ledger's calls since a time, and a note: `read`, `partly-read`,
`could-not-read` or `off`, and why, in fixed words. A ledger not written yet, in a folder that
exists, has no calls yet. A ledger that cannot be read gives no calls at all: None, never an empty
list, so nothing can count it as zero. A line that is not a row of this ledger is left out and
said, unless a row older than the time asked for comes after it: appends come in time order, so
that line is older still. Past READ_MAX bytes only the ledger's end is read, and an end that does
not reach back to the time asked for is said too.

WHAT IT NEVER DOES (asserted in --selftest).  It opens no socket and starts no process. `read()`
opens nothing for writing, and `append()` writes nothing but its one line.
"""
import argparse
import calendar
import errno
import json
import math
import os
import re
import stat
import sys
import time

SCHEMA = "pipeline-spend-ledger/1"
SCHEMA_NAME = "pipeline-spend-ledger"
EXIT_USAGE = 2

# Who keeps a ledger, and its file's name in that caller's own state folder (contract §30).
CALLERS = ("morning", "standup", "tech_lead", "conflict_waker")
FILE_NAMES = {"morning": "morning-spend.jsonl", "standup": "standup-spend.jsonl",
              "tech_lead": "tech-lead-spend.jsonl",
              "conflict_waker": "conflict-waker-spend.jsonl"}
# How a call was paid for, from the credential the caller handed the CLI.
BILLING = ("plan", "key", "unknown")
# Each token count a row keeps, and the key the CLI's `usage` gives it under.
TOKENS = (("input", "input_tokens"), ("output", "output_tokens"),
          ("cache_read", "cache_read_input_tokens"),
          ("cache_write", "cache_creation_input_tokens"))
ROW_KEYS = ("at", "billing", "caller", "model", "run_id", "schema", "tokens", "usd")

READ, PARTLY, COULD_NOT, OFF = "read", "partly-read", "could-not-read", "off"
# A row is about 300 bytes. A longer line is not one, and the reader reads at most a ledger's
# last READ_MAX bytes: over ten thousand rows, more than a month of any caller's calls.
LINE_MAX = 2048
READ_MAX = 4 << 20
OTHER = "other"

# The reasons read() gives, in fixed words. NO_FOLDER is one the usage job adds its config key to.
NO_FOLDER = "the folder that should hold it does not exist"
NOT_A_FOLDER = "the folder that should hold it is not a folder"
NOT_ALLOWED = "this account may not read it"
A_LINK = "it is a link, and a ledger is never read through one"
NOT_REGULAR = "it is not a regular file"
NOT_OPENED = "it could not be opened"
SHORT = ("only its last %d MiB were read, and they do not reach back to the time asked for"
         % (READ_MAX >> 20))

# Python's `$` also matches before a final line break, so every whole-value pattern ends with
# `\Z`. MODEL_RE is the usage file's own (scripts/pipeline_usage_runs.py), which the selftest
# holds it to.
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._\[\]-]{0,63}\Z")
RUN_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


# --------------------------------------------------------------------------- #
# A row
# --------------------------------------------------------------------------- #
def _iso(epoch_seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def _epoch(text):
    """A row's time as seconds since 1970, or None for anything but UTC to the second."""
    if not isinstance(text, str) or not TIME_RE.match(text):
        return None
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def row_problems(row):
    """What keeps `row` from being a row of a spend ledger, in fixed words: [] when it is one.
    The rules of schemas/pipeline-spend-ledger.schema.json, which the selftest holds this to. One
    more: a number too large for a float (JSON's Infinity) is no cost."""
    if not isinstance(row, dict):
        return ["a row is one JSON object"]
    out = []
    if tuple(sorted(row)) != ROW_KEYS:
        out.append("a row has exactly the keys " + ", ".join(ROW_KEYS))
    if row.get("schema") != SCHEMA:
        out.append("its schema is " + SCHEMA)
    if _epoch(row.get("at")) is None:
        out.append("its time is UTC to the second")
    if not any(row.get("caller") == c for c in CALLERS):
        out.append("its caller is one of " + ", ".join(CALLERS))
    model = row.get("model")
    if model is not None and not (isinstance(model, str) and MODEL_RE.match(model)):
        out.append("its model is a plain model name, or null")
    usd = row.get("usd")
    if usd is not None and not (_number(usd) and usd >= 0):
        out.append("its cost is a number of dollars, 0 or more, or null")
    tokens = row.get("tokens")
    if not (isinstance(tokens, dict) and sorted(tokens) == sorted(s for s, _k in TOKENS)
            and all(v is None or _count(v) for v in tokens.values())):
        out.append("its tokens are input, output, cache_read and cache_write, each a count or "
                   "null")
    run_id = row.get("run_id")
    if run_id is not None and not (isinstance(run_id, str) and RUN_ID_RE.match(run_id)):
        out.append("its run id is a session id, or null")
    if not any(row.get("billing") == b for b in BILLING):
        out.append("its billing is one of " + ", ".join(BILLING))
    return out


def _model_of(doc):
    """The model that carried the call: of those the CLI's `modelUsage` names, the one it gives the
    most cost, then the most output, then the last name. `other` for a name that is not a plain
    one. None when the output names no model."""
    usage = doc.get("modelUsage") if isinstance(doc, dict) else None
    if not isinstance(usage, dict):
        return None
    best, rank = None, None
    for name, figures in usage.items():
        figures = figures if isinstance(figures, dict) else {}
        cost, out = figures.get("costUSD"), figures.get("outputTokens")
        key = (cost if _number(cost) else -1.0, out if _number(out) else -1.0, name)
        if rank is None or key > rank:
            best, rank = name, key
    if best is None:
        return None
    return best if MODEL_RE.match(best) else OTHER


def _usd_of(doc):
    """`total_cost_usd`, or None when the output gave none to trust: absent, not a finite number,
    below zero, or a zero from a call that did not end in success (as the usage job reads a
    crash's zeroed total, contract §17)."""
    usd = doc.get("total_cost_usd") if isinstance(doc, dict) else None
    if not (_number(usd) and usd >= 0):
        return None
    subtype = doc.get("subtype") if isinstance(doc.get("subtype"), str) else ""
    if usd == 0 and (doc.get("is_error") is not False or subtype.startswith("error")):
        return None
    return round(float(usd), 6)


def _tokens_of(doc):
    usage = doc.get("usage") if isinstance(doc, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return dict((slot, usage[key] if _count(usage.get(key)) else None) for slot, key in TOKENS)


def row_from_result(caller, doc, billing, model=None):
    """A row for one call, from the CLI's JSON output `doc`: the parsed object, or None when the
    call gave none (a run that prints text, timed out, or never finished). `model` is what the
    caller asked for, used only when the output names no model. Only numbers, the model's name
    and the session id are taken from `doc`: never a word of its text. The row's time is
    append()'s to stamp. ValueError for a caller or billing word this module does not know."""
    if not any(caller == c for c in CALLERS):
        raise ValueError("caller must be one of " + ", ".join(CALLERS))
    if not any(billing == b for b in BILLING):
        raise ValueError("billing must be one of " + ", ".join(BILLING))
    named = _model_of(doc)
    if named is None and isinstance(model, str) and model:
        named = model if MODEL_RE.match(model) else OTHER
    sid = doc.get("session_id") if isinstance(doc, dict) else None
    return {"schema": SCHEMA, "caller": caller, "model": named, "usd": _usd_of(doc),
            "tokens": _tokens_of(doc),
            "run_id": sid if isinstance(sid, str) and RUN_ID_RE.match(sid) else None,
            "billing": billing}


def path_in(folder, caller):
    """`caller`'s ledger in its own state folder."""
    return os.path.join(folder, FILE_NAMES[caller])


# --------------------------------------------------------------------------- #
# The one writer
# --------------------------------------------------------------------------- #
def inside_git_worktree(path):
    """True when `path`, or a folder above it, holds `.git`: a session can write a worktree, and
    a ledger there could hold rows a session wrote."""
    cur = os.path.realpath(path)
    while True:
        if os.path.lexists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def append(path, row, now=None):
    """THE ONE WRITER: `row` (from row_from_result), stamped with the time and added as one line at
    the end of the ledger at `path`, in one write, never through a link; a ledger it makes is
    mode 644. A last line a crash left without its line break gets one first, so the torn row
    stays one line and this row its own. Returns the row as written. ValueError for a row that is
    not one, or a ledger inside a git working tree: nothing is written. OSError when the ledger
    cannot be written."""
    stamped = dict(row, at=_iso(time.time() if now is None else now))
    problems = row_problems(stamped)
    if problems:
        raise ValueError("not a spend ledger row: " + problems[0])
    if inside_git_worktree(os.path.dirname(os.path.abspath(path))):
        raise ValueError("a spend ledger is never inside a git working tree: a session could "
                         "write it")
    data = (json.dumps(stamped, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")
    flags = os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd, made = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o644), True
    except FileExistsError:
        fd, made = os.open(path, flags), False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, NOT_REGULAR)
        if made:
            os.fchmod(fd, 0o644)
        if info.st_size and os.pread(fd, 1, info.st_size - 1) != b"\n":
            data = b"\n" + data
        if os.write(fd, data) != len(data):
            raise OSError(errno.EIO, "the row was not written whole")
        os.fsync(fd)
    finally:
        os.close(fd)
    return stamped


# --------------------------------------------------------------------------- #
# The one reader
# --------------------------------------------------------------------------- #
def _note(state, reason=None):
    return {"state": state, "reason": reason}


def _why_not(exc):
    if exc.errno in (errno.EACCES, errno.EPERM):
        return NOT_ALLOWED
    if exc.errno == errno.ELOOP:
        return A_LINK
    return NOT_OPENED


def _row_of(raw, caller):
    """The row one line holds, with its time as `epoch` too, or None: a line that is too long,
    not UTF-8, not JSON, not a row, or another caller's row."""
    if len(raw) > LINE_MAX:
        return None
    try:
        row = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if row_problems(row) or row["caller"] != caller:
        return None
    return dict(row, epoch=_epoch(row["at"]))


def _tail(fd, size):
    """(the ledger's last READ_MAX bytes, where they start)."""
    start = max(0, size - READ_MAX)
    os.lseek(fd, start, os.SEEK_SET)
    chunks, left = [], size - start
    while left > 0:
        chunk = os.read(fd, min(left, 1 << 20))
        if not chunk:
            break
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks), start


def read(path, caller, since):
    """THE ONE READER: (note, calls) for the ledger at `path`, which holds `caller`'s rows.

    `calls` are the rows from `since` (seconds since 1970) on, oldest first, each with its time as
    `epoch` too. They are None when the ledger is off or could not be read: unknown, never an
    empty list. `note` is {"state", "reason"}, the reason in fixed words, never a line of the
    ledger: `off` for no path; `could-not-read` when no row could be trusted; `partly-read` when
    some lines were not this ledger's rows, or its end did not reach back to `since`; else `read`.
    A ledger not written yet, in a folder that exists, is `read` with no calls."""
    if not path:
        return _note(OFF), None
    try:
        if not stat.S_ISDIR(os.stat(os.path.dirname(path)).st_mode):
            return _note(COULD_NOT, NOT_A_FOLDER), None
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return _note(COULD_NOT, NO_FOLDER), None
        return _note(COULD_NOT, _why_not(exc)), None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return _note(READ), []                 # no call recorded yet
        return _note(COULD_NOT, _why_not(exc)), None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return _note(COULD_NOT, NOT_REGULAR), None
        data, start = _tail(fd, info.st_size)
    except OSError as exc:
        return _note(COULD_NOT, _why_not(exc)), None
    finally:
        os.close(fd)
    lines = data.split(b"\n")
    if start:
        lines = lines[1:]             # cut by the tail, or the oldest row read: left unread
    calls, bad, reached = [], 0, False
    for raw in lines:
        if not raw.strip():
            continue
        row = _row_of(raw, caller)
        if row is None:
            bad += 1
        elif row["epoch"] < since:
            # Appends come in time order, so every line before this row is older still.
            bad, reached = 0, True
        else:
            calls.append(row)
    calls.sort(key=lambda r: r["epoch"])
    why = []
    if bad:
        why.append("%d line%s in it %s not a row of this ledger, so %s spend is not counted"
                   % (bad, "" if bad == 1 else "s", "is" if bad == 1 else "are",
                      "its" if bad == 1 else "their"))
    if start and not reached:
        why.append(SHORT)
    if not why:
        return _note(READ), calls
    if not calls and not reached:
        return _note(COULD_NOT, "; ".join(why)), None
    return _note(PARTLY, "; ".join(why)), calls


# --------------------------------------------------------------------------- #
# --selftest — offline, in one run folder; the clock passed in
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_spend_ledger.py",
                                description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "spend-ledger-selftest-run-")
    p.print_help()
    return EXIT_USAGE


def selftest():
    import socket
    import subprocess
    import tempfile

    import check_schemas
    import pipeline_usage_runs as usage_runs

    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        good = got == want
        print("[%s] %s%s" % ("PASS" if good else "FAIL", name,
                             "" if good else "  (got %r, want %r)" % (got, want)))
        if not good:
            fails.append(name)

    T = calendar.timegm((2026, 11, 18, 9, 12, 3, 0, 0, 0))
    DAY = 86400
    SID = "0b6c3e1e-1d2f-4a5b-8c9d-0e1f2a3b4c5d"
    WORDS = "SENTINEL-WORDS-0f3a"          # the model's text: no row may carry it
    DOC = {"type": "result", "subtype": "success", "is_error": False, "result": WORDS,
           "session_id": SID, "num_turns": 1, "total_cost_usd": 0.0412345678,
           "usage": {"input_tokens": 1200, "output_tokens": 340, "cache_read_input_tokens": 8000,
                     "cache_creation_input_tokens": 0, "service_tier": "standard"},
           "modelUsage": {"claude-haiku-4-5": {"costUSD": 0.0011, "outputTokens": 20},
                          "claude-sonnet-5-5": {"costUSD": 0.0401, "outputTokens": 320}}}

    # -- a row from the CLI's output ----------------------------------------------------------
    row = row_from_result("morning", DOC, "plan", model="sonnet")
    check("a row from the CLI's JSON: the costliest model, its cost, its tokens and its session",
          row, {"schema": SCHEMA, "caller": "morning", "model": "claude-sonnet-5-5",
                "usd": 0.041235, "tokens": {"input": 1200, "output": 340, "cache_read": 8000,
                                            "cache_write": 0},
                "run_id": SID, "billing": "plan"})
    check("…and no word of the model's text is in it", WORDS in json.dumps(row), False)
    bare = row_from_result("conflict_waker", None, "unknown", model="sonnet")
    check("no JSON output: the cost, the tokens and the session unknown, never zero; the model "
          "asked for",
          (bare["usd"], bare["tokens"], bare["run_id"], bare["model"]),
          (None, {"input": None, "output": None, "cache_read": None, "cache_write": None}, None,
           "sonnet"))
    check("no JSON output and no model asked for: the model is unknown",
          row_from_result("conflict_waker", None, "unknown")["model"], None)
    check("a model asked for that is not a plain name is other",
          row_from_result("tech_lead", None, "plan", model="Sonnet 5")["model"], OTHER)
    check("the costliest model with a name that is not plain is other, never its name",
          row_from_result("tech_lead", dict(DOC, modelUsage={"Claude <b>": {"costUSD": 1}}),
                          "plan")["model"], OTHER)
    check("a model the output names wins over the one asked for",
          row_from_result("tech_lead", DOC, "plan", model="opus")["model"], "claude-sonnet-5-5")
    check("with no costs, the model with the most output carries the call",
          row_from_result("standup", dict(DOC, modelUsage={"a-1": {"outputTokens": 5},
                                                           "b-1": {"outputTokens": 9}}),
                          "plan")["model"], "b-1")
    errs = [row_from_result("standup", dict(DOC, is_error=True, subtype="error_during_execution",
                                            total_cost_usd=v), "plan")["usd"] for v in (0, 0.5)]
    check("an error's zero total is no total (unpriced); an error that gave a total is priced",
          errs, [None, 0.5])
    check("a zero total with no word of success is no total either",
          row_from_result("standup", {"total_cost_usd": 0}, "plan")["usd"], None)
    odd = [row_from_result("standup", dict(DOC, total_cost_usd=v), "plan")["usd"]
           for v in (float("nan"), float("inf"), -0.5, True, "0.1", None)]
    check("a cost that is not a finite number, 0 or more, is unknown, never zero",
          odd, [None] * 6)
    toks = row_from_result("standup", dict(DOC, usage={"input_tokens": -1, "output_tokens": True,
                                                       "cache_read_input_tokens": 1.5,
                                                       "cache_creation_input_tokens": "9"}),
                           "plan")["tokens"]
    check("a token count that is not a whole number, 0 or more, is unknown",
          toks, {"input": None, "output": None, "cache_read": None, "cache_write": None})
    check("a session id that is not one is left out",
          row_from_result("standup", dict(DOC, session_id="../../x"), "plan")["run_id"], None)
    refused = []
    for caller, billing in (("dispatcher", "plan"), ("morning", "free"), (["morning"], "plan")):
        try:
            row_from_result(caller, DOC, billing)
            refused.append(False)
        except ValueError:
            refused.append(True)
    check("a caller or a billing word this module does not know is refused",
          refused, [True, True, True])
    check("every caller has its own ledger file, each a .jsonl",
          (sorted(FILE_NAMES) == sorted(CALLERS), len(set(FILE_NAMES.values())),
           all(n.endswith(".jsonl") for n in FILE_NAMES.values())), (True, 4, True))
    check("the model pattern is the usage file's own",
          MODEL_RE.pattern, usage_runs.MODEL_RE.pattern)

    # -- the hand rules and the schema agree ----------------------------------------------------
    good = dict(row, at=_iso(T))

    def changed(key, value):
        out = dict(good)
        if value is KeyError:
            out.pop(key)
        else:
            out[key] = value
        return out
    malformed = [
        ("another marker", changed("schema", "pipeline-spend-ledger/2")),
        ("a local time", changed("at", "2026-11-18T09:12:03+01:00")),
        ("a time with a line break on its end", changed("at", _iso(T) + "\n")),
        ("a caller nobody defined", changed("caller", "dispatcher")),
        ("a model name with a space", changed("model", "claude sonnet")),
        ("a model name in capitals", changed("model", "Claude-Sonnet")),
        ("a model name past 64", changed("model", "m" * 65)),
        ("a negative cost", changed("usd", -0.01)),
        ("a cost as text", changed("usd", "0.04")),
        ("a token slot left out", changed("tokens", {"input": 1, "output": 1, "cache_read": 1})),
        ("a token slot of its own", changed("tokens", dict(good["tokens"], words=1))),
        ("a negative token count", changed("tokens", dict(good["tokens"], input=-1))),
        ("a token count with a fraction", changed("tokens", dict(good["tokens"], output=1.5))),
        ("a run id in capitals", changed("run_id", SID.upper())),
        ("a run id with a line break on its end", changed("run_id", SID + "\n")),
        ("a billing word nobody defined", changed("billing", "free")),
        ("a field of its own", changed("words", WORDS)),
        ("the billing left out", changed("billing", KeyError)),
    ]
    check("a whole row passes the hand rules and the schema",
          (row_problems(good), check_schemas.document_problems(good, SCHEMA_NAME)), ([], []))
    split = [(why, bool(row_problems(bad_row)),
              bool(check_schemas.document_problems(bad_row, SCHEMA_NAME)))
             for why, bad_row in malformed]
    check("every malformed row is refused by the hand rules and by the schema alike",
          [s for s in split if s[1:] != (True, True)], [])
    check("a null model, cost, run id and token count pass both",
          [bool(row_problems(v)) or bool(check_schemas.document_problems(v, SCHEMA_NAME))
           for v in (dict(bare, at=_iso(T)), changed("usd", None), changed("model", None))],
          [False, False, False])
    check("a cost past what a float holds is refused by the hand rules",
          bool(row_problems(changed("usd", float("inf")))), True)

    # -- the one writer -----------------------------------------------------------------------
    calls_seen = []
    real_socket, real_popen = socket.socket, subprocess.Popen

    def no_socket(*a, **k):
        calls_seen.append("socket")
        raise AssertionError("a socket was opened")

    def no_process(*a, **k):
        calls_seen.append("process")
        raise AssertionError("a process was started")
    socket.socket, subprocess.Popen = no_socket, no_process
    try:
        root = os.path.realpath(tempfile.mkdtemp(prefix="ledgers-"))
        led = path_in(root, "morning")
        first = append(led, row, now=T)
        second = append(led, row_from_result("morning", None, "plan", model="sonnet"),
                        now=T + 60)
        with open(led, "rb") as fh:
            lines = fh.read().split(b"\n")
        check("append() adds one line per row, and stamps each with its own time",
              (len(lines), lines[-1], json.loads(lines[0])["at"], json.loads(lines[1])["at"],
               first["at"], second["usd"]), (3, b"", _iso(T), _iso(T + 60), _iso(T), None))
        check("…a ledger it makes is mode 644, so the usage job can read it",
              oct(os.stat(led).st_mode & 0o777), "0o644")
        before = os.path.getsize(led)
        try:
            append(led, dict(row, usd=-1), now=T)
            took = True
        except ValueError:
            took = False
        check("a row that is not one is refused, and nothing is written",
              (took, os.path.getsize(led)), (False, before))
        repo = os.path.join(root, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        os.makedirs(os.path.join(repo, "state"))
        inside = os.path.join(repo, "state", FILE_NAMES["morning"])
        try:
            append(inside, row, now=T)
            took = True
        except ValueError:
            took = False
        check("a ledger inside a git working tree is refused, and nothing is written",
              (took, os.path.exists(inside)), (False, False))
        target = os.path.join(root, "elsewhere.txt")
        with open(target, "w") as fh:
            fh.write("keep\n")
        linked = path_in(root, "standup")
        os.symlink(target, linked)
        try:
            append(linked, row_from_result("standup", DOC, "plan"), now=T)
            took = True
        except OSError:
            took = False
        with open(target) as fh:
            kept = fh.read()
        check("a ledger that is a link is never written through", (took, kept), (False, "keep\n"))
        torn = path_in(root, "tech_lead")
        with open(torn, "w") as fh:
            fh.write('{"schema": "pipeline-spend-ledger/1", "at": "2026-11-18T0')   # a crash
        append(torn, row_from_result("tech_lead", DOC, "plan"), now=T)
        note, calls = read(torn, "tech_lead", T - DAY)
        check("a torn last line gets a line break first: the new row is whole, the torn one said",
              (note["state"], len(calls), calls[0]["usd"]), (PARTLY, 1, 0.041235))
    finally:
        socket.socket, subprocess.Popen = real_socket, real_popen

    # -- the one reader -----------------------------------------------------------------------
    real_open = os.open
    opened_for_writing = []

    def watched_open(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            opened_for_writing.append(path)
        return real_open(path, flags, *a, **k)

    def ledger(name, rows, caller="tech_lead"):
        """A ledger in its own folder: each item a row dict (written as one JSON line) or raw text."""
        folder = os.path.join(root, name)
        os.makedirs(folder)
        path = path_in(folder, caller)
        with open(path, "w", encoding="utf-8") as fh:
            for item in rows:
                fh.write((item if isinstance(item, str) else json.dumps(item)) + "\n")
        return path

    def at(when, caller="tech_lead", **more):
        out = dict(row_from_result(caller, DOC, "plan"), at=_iso(when))
        out.update(more)
        return out

    os.open = watched_open
    try:
        check("no path is off: no calls, unknown", read("", "tech_lead", T), (_note(OFF), None))
        check("a folder that does not exist could not be read: no calls, never an empty list",
              read(os.path.join(root, "nowhere", "x.jsonl"), "tech_lead", T),
              (_note(COULD_NOT, NO_FOLDER), None))
        os.makedirs(os.path.join(root, "empty"))
        check("a ledger not written yet, in a folder that exists, is read: no call yet",
              read(path_in(os.path.join(root, "empty"), "tech_lead"), "tech_lead", T),
              (_note(READ), []))
        three = ledger("three", [at(T - 3 * DAY), at(T), at(T - DAY)])
        note, calls = read(three, "tech_lead", T - 2 * DAY)
        check("the rows from the time asked for, oldest first, each with its time",
              (note, [c["epoch"] for c in calls], [c["at"] for c in calls]),
              (_note(READ), [T - DAY, T], [_iso(T - DAY), _iso(T)]))
        mixed = ledger("mixed", [at(T), at(T, caller="standup"), "not json", "{}", at(T + 1)])
        note, calls = read(mixed, "tech_lead", T - DAY)
        check("lines that are not this ledger's rows are left out and said; the rest are counted",
              (note, len(calls)),
              (_note(PARTLY, "3 lines in it are not a row of this ledger, so their spend is not "
                             "counted"), 2))
        only_bad = ledger("only-bad", ["not json", at(T, caller="morning")])
        check("a ledger with no row to trust could not be read: no calls, never zero",
              read(only_bad, "tech_lead", T - DAY),
              (_note(COULD_NOT, "2 lines in it are not a row of this ledger, so their spend is "
                                "not counted"), None))
        old_tear = ledger("old-tear", ['{"schema": "pipe', at(T - 5 * DAY), at(T)])
        check("a torn line before a row older than the time asked for is older still: read",
              read(old_tear, "tech_lead", T - DAY)[0], _note(READ))
        late_tear = ledger("late-tear", [at(T - 5 * DAY), '{"schema": "pipe', at(T)])
        check("a torn line after the last older row may be a call in the window: said",
              read(late_tear, "tech_lead", T - DAY)[0]["state"], PARTLY)
        long_line = ledger("long-line", [json.dumps(at(T)) + " " * LINE_MAX, at(T)])
        check("a line longer than a row can be is not one",
              read(long_line, "tech_lead", T - DAY)[0]["state"], PARTLY)
        nan = ledger("nan", ['{"at": "%s", "billing": "plan", "caller": "tech_lead", "model": '
                             'null, "run_id": null, "schema": "%s", "tokens": {"cache_read": '
                             'null, "cache_write": null, "input": null, "output": null}, "usd": '
                             'NaN}' % (_iso(T), SCHEMA), at(T)])
        check("a row whose cost is NaN is not one", read(nan, "tech_lead", T - DAY)[0]["state"],
              PARTLY)
        unpriced = ledger("unpriced", [at(T, usd=None)])
        note, calls = read(unpriced, "tech_lead", T - DAY)
        check("a call whose cost is unknown is read as unknown, never as zero",
              (note, calls[0]["usd"]), (_note(READ), None))
        os.symlink(three, os.path.join(root, "empty", FILE_NAMES["standup"]))
        check("a ledger that is a link could not be read",
              read(os.path.join(root, "empty", FILE_NAMES["standup"]), "standup", T),
              (_note(COULD_NOT, A_LINK), None))
        os.makedirs(path_in(os.path.join(root, "empty"), "morning"))
        check("a folder where the ledger should be could not be read",
              read(path_in(os.path.join(root, "empty"), "morning"), "morning", T),
              (_note(COULD_NOT, NOT_REGULAR), None))
        flat = os.path.join(root, "flat")
        with open(flat, "w") as fh:
            fh.write("x")
        check("a ledger whose folder is a file could not be read",
              read(os.path.join(flat, FILE_NAMES["morning"]), "morning", T)[0]["state"],
              COULD_NOT)
        if os.geteuid() != 0:
            shut = ledger("shut", [at(T)])
            os.chmod(shut, 0)
            check("a ledger this account may not read could not be read",
                  read(shut, "tech_lead", T - DAY), (_note(COULD_NOT, NOT_ALLOWED), None))
            os.chmod(shut, 0o644)
            closed = ledger("closed", [at(T)])
            os.chmod(os.path.dirname(closed), 0)
            check("…and so is one in a folder it may not open, said as such, not as missing",
                  read(closed, "tech_lead", T - DAY), (_note(COULD_NOT, NOT_ALLOWED), None))
            os.chmod(os.path.dirname(closed), 0o755)
            home = os.path.join(root, "a-home")
            os.makedirs(os.path.join(home, "state"))
            os.chmod(home, 0)
            check("…and one whose folder sits in a home it may not enter, as the waker's does "
                  "for the role account", read(path_in(os.path.join(home, "state"),
                                                       "conflict_waker"), "conflict_waker", T),
                  (_note(COULD_NOT, NOT_ALLOWED), None))
            os.chmod(home, 0o755)
        filler = json.dumps(at(T - 30 * DAY))
        many = (READ_MAX // (len(filler) + 1)) + 50
        big = ledger("big", [filler] * many + [at(T - DAY), at(T)])
        note, calls = read(big, "tech_lead", T - 2 * DAY)
        check("past %d MiB only the end is read, and an end that reaches back is read whole"
              % (READ_MAX >> 20), (note, len(calls)), (_note(READ), 2))
        note, calls = read(big, "tech_lead", T - 40 * DAY)
        check("…an end that does not reach back to the time asked for is said, its rows counted",
              (note, len(calls) > 10000), (_note(PARTLY, SHORT), True))
        check("read() opens nothing for writing", opened_for_writing, [])
    finally:
        os.open = real_open
    check("nothing opened a socket or started a process", calls_seen, [])

    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
