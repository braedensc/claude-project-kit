#!/usr/bin/env python3
"""The usage runs: the dispatcher's session logs, and the Stage E and idea-gate records beside
them, read into priced, classified, timed runs (KIT-422, contract §17).

    python3 scripts/pipeline_usage_runs.py --selftest

This is the reading half of the usage job, scripts/pipeline_usage_local.py, which imports it and
counts the runs into the usage file. It has no command of its own. It reads files, and it
writes nothing: no file, no socket, no process.

WHAT IT READS.
  1. The dispatcher's session logs, `<dispatcher_home>/logs/<workspace>/session-<id>-<UTC
     start>.jsonl`, modified within `lookback_days` plus one day. Only `sdk-message` lines and
     the first `session-metadata` line are read, and only for numbers, ids and words from
     closed lists. A run's own spend is its share of its session's running totals: the last
     `result` line's totals less the previous run's. A crash's zeroed total is unpriced,
     never zero. A reply's own `usage` is never used: it is logged as the reply starts and
     undercounts the output (measured).
  2. Stage E's bounce ledger and review records, and the idea gate's record of planning runs.
     They say which later run was a bounce fix or a conflict fix, and which ticket is a
     planning run. Nothing a session can write decides what a run was.

WHAT IT GIVES.  Each log file is one run: its kind (tech lead, review, planner, coding, bounce
fix, conflict fix, catch-up or other later), its own spend and tokens, its length in seconds,
how it signed in, and the usage events it heard. Beside them: the placeholders no run follows,
and for each source a record of what it was asked and what it answered.

THE SHAPE.  `scan(cfg, now)` reads everything once and returns a dict. `runs` are the runs the
window holds (`None` when the logs folder could not be listed), each a dict: `workspace`,
`kind`, `how` (what told its kind), `cost` (None when unpriced, never 0), `tokens`, `models`,
`seconds`, `last`, `key_source`, `events`, `weekly` and `event_times`. `baselines` are older
runs read for their total only and never counted. `pendings` are the placeholders, and
`unmatched_placeholders(scanned)` picks out the runs that never reached the model. `sources`
holds a record for each of `logs`, `ledger` and `planner`.

FIELDS ONLY.  Numbers, ticket ids, model names that match a strict pattern, and words from
closed lists. No prompt, reply, comment or title is kept, and no error line quotes an
exception's own words. Every value taken from a log passes a sanitizer first, so no log can
make the usage file fail its schema and stop it.

WHEN A SOURCE FAILS.  That source's record says `could-not-read` (or `partly-read`) with the
reason, in this module's own words. A source that is not read is unknown, never zero.

WHAT IT NEVER DOES (asserted in --selftest).  It opens no socket, starts no process, reads no
credential and no environment variable, and writes nothing at all.
"""
import argparse
import calendar
import datetime
import json
import math
import os
import re
import stat
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))

EXIT_USAGE = 2

READ, PARTLY, COULD_NOT, OFF = "read", "partly-read", "could-not-read", "off"
SOURCES = ("logs", "ledger", "planner")

# The kinds of work a run can be, in the order the usage file lists them.
KINDS = ("tech_lead", "review", "planner", "coding", "bounce_fix", "conflict_fix", "catch_up",
         "other_later")
CHAT_PREFIX = "slack-"

# The SDK's own closed list of sign-in sources, as it names them (init's `apiKeySource`).
# Anything else is "other". A run whose log names none keeps None.
KEY_SOURCES = ("ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key", "none", "user",
               "project", "org", "temporary", "oauth")
KEY_OTHER = "other"
# Anthropic's usage windows, and the words a window's status can have (rate_limit_info).
WINDOW_TYPES = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet",
                "seven_day_overage_included", "overage")
STATUS_WORDS = ("allowed", "allowed_warning", "rejected")
WORD_OTHER = "other"

# The re-prompts the bounce driver and the catch-up job send start with a fixed line. Held
# here, as the status job holds the review poller's record shape: those modules exit when
# their own parts are missing. The selftest reads the producers' source and asserts these
# are still what they write. A conflict re-prompt's record line says "sent by the Stage E
# bounce driver", so it also holds the bounce mark: the conflict marks are looked for first.
# Matched inside the process only. The kind is kept; never a word of the prompt.
PROMPT_MARKS = (("conflict_fix", ("Stage E conflict fix ", "stage-e-conflict/1")),
                ("bounce_fix", ("Stage E bounce ", "stage-e-bounce/1")),
                ("catch_up", ("Catch-up receipt:", "**Catch-up.**")))
PROMPT_SCAN = 4 << 20
# The ledger's rows that name a delivered re-prompt (scripts/pipeline_bounce_local.py).
LEDGER_KINDS = {"delivered": "bounce_fix", "conflict-delivered": "conflict_fix"}
# A re-prompt's run starts this long before, and this long after, its ledger row.
MATCH_BEFORE, MATCH_AFTER = 60, 30 * 60
MODEL_FIELDS = ("costUSD", "inputTokens", "outputTokens", "cacheReadInputTokens",
                "cacheCreationInputTokens")
# A session's total at the window's edge holds every turn before the window, so a run whose
# session has older runs needs the last total of the nearest of them, or its share would be
# the whole session so far. At most this many of them are read, newest first, for a total
# only, and they are never counted as runs.
BASELINE_FILES = 3

# Bounds, so no log can stall the pass or leave the usage file malformed. A line longer than
# LINE_MAX is passed over unread; a state file is read to STATE_MAX and no further.
LINE_MAX = 8 << 20
STATE_MAX = 64 << 20
REASON_CAP = 300

# Python's `$` also matches before a final line break, so every whole-value pattern ends
# with `\Z`: an id with a line break on its end is refused, not kept. The bounds keep every
# id and name inside what schemas/pipeline-usage.schema.json allows, so no folder name or
# log value can make the usage file fail it.
RUN_FILE = re.compile(r"^session-(.+)-([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}"
                      r"-[0-9]{3}Z)\.jsonl\Z")
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._\[\]-]{0,63}\Z")
TIME_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
                     r"(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:?[0-9]{2})\Z")
# Times a log can carry: before 2020 or after 2099 is not a time this module keeps.
EPOCH_LOW, EPOCH_HIGH = 1577836800.0, 4102444800.0
_UNSAFE_RE = re.compile(r"[<>&|`]")

# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _iso(epoch_seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def epoch(text):
    """A log's time as seconds since 1970, or None: ISO with a zone, between 2020 and 2099.
    Anything else is not kept, so no date a log writes can be out of range downstream."""
    if not isinstance(text, str):
        return None
    m = TIME_RE.match(text)
    if not m:
        return None
    try:
        whole = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                  int(m.group(4)), int(m.group(5)), int(m.group(6)),
                                  tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return None
    offset = 0
    if m.group(8) != "Z":
        digits = m.group(8)[1:].replace(":", "")
        offset = (1 if m.group(8)[0] == "+" else -1) * (int(digits[:2]) * 3600
                                                       + int(digits[2:]) * 60)
    when = whole + (float("0." + m.group(7)) if m.group(7) else 0.0) - offset
    return when if EPOCH_LOW <= when < EPOCH_HIGH else None


def file_start(stamp):
    """A run file's start from its name: 2026-11-02T10-15-30-123Z."""
    return epoch("%s:%s:%s.%sZ" % (stamp[:13], stamp[14:16], stamp[17:19], stamp[20:23]))


def finite(value, low, high):
    """A number from a log, or None: not a boolean, not NaN or infinite, inside [low, high]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def money(value):
    n = finite(value, 0.0, 1e7)
    return None if n is None else round(n, 6)


def ratio(value):
    n = finite(value, 0.0, 1000.0)
    return None if n is None else round(n, 4)


def _hidden(ch):
    """A character a reader does not see: a control, format, private-use or surrogate code
    point (Unicode categories Cc, Cf, Co, Cs) or a variation selector."""
    point = ord(ch)
    return (unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs")
            or 0xFE00 <= point <= 0xFE0F or 0xE0100 <= point <= 0xE01EF)


def line(text, cap=REASON_CAP):
    """Our own reason or error text, on one line, without markup or hidden characters, capped.
    It is built from this code's own words, counts and class names, never from a log's."""
    s = _UNSAFE_RE.sub(" ", "".join(" " if _hidden(ch) else ch for ch in str(text)))
    s = " ".join(s.split())
    return s if len(s) <= cap else s[:cap - 1].rstrip() + "…"


def key_word(value):
    if value in KEY_SOURCES:
        return value
    return KEY_OTHER if value else None


def window_word(value):
    if value in WINDOW_TYPES:
        return value
    return WORD_OTHER if value else None


def status_word(value):
    return value if value in STATUS_WORDS else WORD_OTHER




# --------------------------------------------------------------------------- #
# Reading files, bounded
# --------------------------------------------------------------------------- #
def open_regular(path):
    """A regular file opened read-only, without blocking and never through a final link, and
    judged on the open file itself: a pipe or link swapped in after a look cannot hang or
    redirect the pass. OSError for anything that is not a regular file."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("not a regular file")
    return fd


def log_lines(path):
    """A file's lines, none longer than LINE_MAX (a longer one is dropped without being
    held), so no line can make the pass slow or large. OSError if it cannot be read."""
    fd = open_regular(path)
    try:
        held, skipping = b"", False
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            parts = (held + chunk).split(b"\n")
            held = parts.pop()
            for piece in parts:
                # The first piece after an overlong line was dropped is that line's rest.
                if skipping:
                    skipping = False
                elif len(piece) <= LINE_MAX:
                    yield piece
            if len(held) > LINE_MAX:
                held, skipping = b"", True
        if held and not skipping and len(held) <= LINE_MAX:
            yield held
    finally:
        os.close(fd)


def read_json_file(path):
    """A state file as JSON. OSError if it cannot be read, ValueError if it is not JSON, is
    not UTF-8 or is longer than STATE_MAX."""
    fd = open_regular(path)
    try:
        data = b""
        while len(data) <= STATE_MAX:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    if len(data) > STATE_MAX:
        raise ValueError("it is longer than %d bytes" % STATE_MAX)
    try:
        return json.loads(data.decode("utf-8"))
    except RecursionError:
        raise ValueError("it is nested too deeply")


class Source(object):
    """What one source was asked this pass, and what it answered (contract §17, a source).
    A question the source could not answer is a problem, in this code's own words."""

    def __init__(self, now, off=False):
        self.now, self.off = now, off
        self.asked = self.answered = 0
        self.read_at = None
        self.problems = []

    def ask(self):
        self.asked += 1

    def answer(self):
        self.answered += 1
        self.read_at = _iso(self.now())

    def fail(self, why):
        self.problems.append(why)

    def record(self):
        if self.off:
            return {"state": OFF, "asked": 0, "answered": 0, "reason": None, "read_at": None}
        if not self.problems:
            state, reason = READ, None
        else:
            state = PARTLY if self.answered else COULD_NOT
            first = self.problems[0]
            reason = line(first if len(self.problems) == 1 else
                          "%s (and %d more)" % (first, len(self.problems) - 1))
        return {"state": state, "asked": self.asked, "answered": self.answered,
                "reason": reason, "read_at": self.read_at}


# --------------------------------------------------------------------------- #
# One run's log
# --------------------------------------------------------------------------- #
def prompt_kind(message):
    """The kind of a run's opening prompt, from the fixed openings above; never its text."""
    inner = message.get("message")
    content = inner.get("content") if isinstance(inner, dict) else None
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        texts = []
    blob = "\n".join(t for t in texts if isinstance(t, str))[:PROMPT_SCAN]
    for kind, marks in PROMPT_MARKS:
        if any(mark in blob for mark in marks):
            return kind
    return "unmarked" if blob else None


def usage_figures(raw):
    """A result's `modelUsage` as {model: [cost, input, output, cache read, cache write]}.
    A name that is not a plain model name is "other": its spend still counts, and no text a
    log chose reaches the usage file as a key."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for name, fig in raw.items():
        if not isinstance(fig, dict):
            continue
        key = name if isinstance(name, str) and MODEL_RE.match(name) else WORD_OTHER
        vec = [money(fig.get(MODEL_FIELDS[0])) or 0.0]
        vec += [finite(fig.get(f), 0.0, 1e13) or 0.0 for f in MODEL_FIELDS[1:]]
        before = out.get(key, [0.0] * len(MODEL_FIELDS))
        out[key] = [a + b for a, b in zip(before, vec)]
    return out


def parse_run(path):
    """One run's numbers: when it began and ended, how it signed in, the kind of its opening
    prompt, its last result's running totals, and the usage events it heard. Never a
    message's text. OSError if the file cannot be read."""
    run = {"started": None, "first_at": None, "last_at": None, "key_source": None,
           "prompt": None, "total": None, "zeroed": False, "usage": {}, "events": {},
           "weekly": None, "event_times": [], "limit_stop": None}
    # The newest rejection, and how the first result after it ended (None: no result yet).
    rejected, after = None, None
    for raw in log_lines(path):
        try:
            entry = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "session-metadata":
            if run["started"] is None and isinstance(entry.get("startedAt"), str):
                run["started"] = entry["startedAt"][:64]
            continue
        message = entry.get("message") if entry.get("type") == "sdk-message" else None
        if not isinstance(message, dict):
            continue
        at = epoch(entry.get("timestamp"))
        if at is not None:
            run["first_at"] = at if run["first_at"] is None else min(run["first_at"], at)
            run["last_at"] = at if run["last_at"] is None else max(run["last_at"], at)
        kind = message.get("type")
        if kind == "system" and message.get("subtype") == "init":
            if run["key_source"] is None:
                run["key_source"] = key_word(message.get("apiKeySource"))
        elif kind == "user" and run["prompt"] is None and message.get(
                "parent_tool_use_id") is None:
            run["prompt"] = prompt_kind(message)
        elif kind == "result":
            # A result's figures are running totals for the whole session, the SDK's own. A
            # reply's own usage line is not a total: it is logged as the reply starts,
            # before most of its output. The last result in a file is the file's.
            total = money(message.get("total_cost_usd"))
            run["zeroed"] = message.get("is_error") is True and not total
            run["total"] = None if run["zeroed"] else total
            run["usage"] = {} if run["zeroed"] else usage_figures(message.get("modelUsage"))
            if rejected is not None:
                after = "error" if message.get("is_error") is True else "ok"
        elif kind == "rate_limit_event" and at is not None:
            info = message.get("rate_limit_info")
            info = info if isinstance(info, dict) else {}
            event = {"at": at, "window": window_word(info.get("rateLimitType")),
                     "status": status_word(info.get("status")),
                     "utilization": ratio(info.get("utilization")),
                     "resets_at": finite(info.get("resetsAt"), EPOCH_LOW, EPOCH_HIGH),
                     "surpassed": ratio(info.get("surpassedThreshold"))}
            run["event_times"].append(at)
            window = event["window"]
            if event["status"] == "rejected" and (rejected is None or at >= rejected["at"]):
                rejected, after = event, None
            if window and (window not in run["events"] or at >= run["events"][window]["at"]):
                run["events"][window] = event
            # The week is read from the newest weekly event that names a reset, which is not
            # always the newest weekly event.
            if window == "seven_day" and event["resets_at"] is not None and (
                    run["weekly"] is None or at >= run["weekly"]["at"]):
                run["weekly"] = event
    # The plan's limit cut this run off when a window rejected it and the run then ended in an
    # error, or never ended. A rejection followed by a success is a run that went on, and a
    # warning is not a rejection. Only words from the closed lists and numbers are kept.
    if rejected is not None and after != "ok":
        run["limit_stop"] = {"window": rejected["window"] or WORD_OTHER,
                             "resets_at": rejected["resets_at"]}
    return run


def first_started(path):
    """A placeholder's, or a run's, start from its first line: the dispatcher's own metadata.
    Every run leaves a placeholder file, and both carry the run's start. OSError if the file
    cannot be read."""
    fd = open_regular(path)
    try:
        head = os.read(fd, 65536).split(b"\n", 1)[0]
    finally:
        os.close(fd)
    try:
        entry = json.loads(head.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None
    value = entry.get("startedAt") if isinstance(entry, dict) else None
    return value[:64] if isinstance(value, str) else None


# --------------------------------------------------------------------------- #
# The records Stage E and the idea gate keep
# --------------------------------------------------------------------------- #
def read_planner(cfg, now):
    """(planning-run tickets or None, source). The idea gate's seen-set names each planning
    run's ticket. A record that does not exist yet is an answer: no planning run."""
    src = Source(now, off=not cfg["stage_a_state_dir"])
    if src.off:
        return set(), src
    src.ask()
    if not os.path.isdir(cfg["stage_a_state_dir"]):
        src.fail("the idea gate's state folder does not exist: is stage_a_state_dir right? "
                 "Set it to \"\" to turn this source off")
        return None, src
    try:
        doc = read_json_file(os.path.join(cfg["stage_a_state_dir"], "seen.json"))
    except FileNotFoundError:
        src.answer()
        return set(), src
    except (OSError, ValueError):
        src.fail("the idea gate's seen.json could not be read")
        return None, src
    triggers = doc.get("triggers") if isinstance(doc, dict) else None
    if not isinstance(triggers, dict):
        src.fail("the idea gate's seen.json holds no triggers")
        return None, src
    src.answer()
    tickets = set()
    for rec in triggers.values():
        ticket = rec.get("run_ticket") if isinstance(rec, dict) else None
        if isinstance(ticket, str) and TICKET_RE.match(ticket):
            tickets.add(ticket)
    return tickets, src


def read_ledger(cfg, now):
    """([(time, ticket, kind)], source): each re-prompt the bounce driver delivered, with the
    ticket from the review poller's record of that pull request. A ledger or record that
    does not exist yet is an answer: no re-prompt yet. One that cannot be read is not."""
    src = Source(now, off=not cfg["stage_e_state_dir"])
    if src.off:
        return [], src
    state = cfg["stage_e_state_dir"]
    if not os.path.isdir(state):
        src.ask()
        src.fail("Stage E's state folder does not exist: is stage_e_state_dir right? Set it "
                 "to \"\" to turn this source off")
        return [], src
    tickets, outcomes = {}, os.path.join(state, "outcomes")
    src.ask()
    try:
        names = sorted(n for n in os.listdir(outcomes) if n.endswith(".json"))
        src.answer()
    except FileNotFoundError:
        names = []
        src.answer()
    except OSError:
        names = []
        src.fail("Stage E's review records could not be listed")
    for name in names:
        src.ask()
        try:
            rec = read_json_file(os.path.join(outcomes, name))
        except (OSError, ValueError):
            src.fail("a review record could not be read")
            continue
        src.answer()
        ticket = rec.get("ticket_id") if isinstance(rec, dict) else None
        repo, pr = (rec.get("repo"), rec.get("pr")) if isinstance(rec, dict) else (None, None)
        if (isinstance(ticket, str) and TICKET_RE.match(ticket) and isinstance(repo, str)
                and isinstance(pr, int) and not isinstance(pr, bool)):
            tickets[(repo.lower(), pr)] = ticket
    rows, torn = [], 0
    src.ask()
    try:
        for raw in log_lines(os.path.join(state, "bounce-ledger.jsonl")):
            try:
                row = json.loads(raw.decode("utf-8", "replace"))
            except (ValueError, RecursionError):
                row = None
            if not isinstance(row, dict):
                torn += 1
                continue
            outcome, repo, pr = row.get("outcome"), row.get("repo"), row.get("pr")
            kind = LEDGER_KINDS.get(outcome) if isinstance(outcome, str) else None
            when = epoch(row.get("at")) if kind else None
            ticket = tickets.get((repo.lower(), pr)) if (
                isinstance(repo, str) and isinstance(pr, int)
                and not isinstance(pr, bool)) else None
            if kind and when is not None and ticket:
                rows.append((when, ticket, kind))
        if torn:
            src.fail("%d line(s) of the bounce ledger could not be read" % torn)
        else:
            src.answer()
    except FileNotFoundError:
        src.answer()
    except OSError:
        src.fail("the bounce ledger could not be read")
    rows.sort()
    return rows, src


# --------------------------------------------------------------------------- #
# The logs
# --------------------------------------------------------------------------- #
def read_logs(cfg, now, src):
    """(runs in the window, older runs read for a baseline total, placeholders, the first
    run of each workspace), or None when the logs folder could not be listed. Each run file
    and placeholder read is a question; a folder that cannot be listed, or a name that is
    not a regular file, is one asked and not answered."""
    logs = os.path.join(cfg["dispatcher_home"], "logs")
    cutoff = now() - (cfg["lookback_days"] + 1) * 86400
    try:
        with os.scandir(logs) as it:
            folders = sorted(e.name for e in it if e.is_dir(follow_symlinks=False))
    except OSError as exc:
        src.ask()
        src.fail("the dispatcher's logs folder could not be listed (%s): is dispatcher_home "
                 "right?" % type(exc).__name__)
        return None
    runs, pendings, firsts, every = [], [], {}, {}
    for ws in folders:
        folder = os.path.join(logs, ws)
        try:
            with os.scandir(folder) as it:
                entries = sorted((e.name, e.is_file(follow_symlinks=False),
                                  e.stat(follow_symlinks=False).st_mtime) for e in it)
        except OSError:
            src.ask()
            src.fail("a workspace folder could not be listed")
            continue
        for name, regular, mtime in entries:
            m = RUN_FILE.match(name)
            start = file_start(m.group(2)) if m else None
            if not m or start is None:
                continue
            sid = m.group(1)
            path = os.path.join(folder, name)
            if sid != "pending":
                # The first run of a folder is the earliest by name, not the earliest this
                # window holds: an old ticket's later run is not its first.
                firsts[ws] = min(firsts.get(ws, (start, sid)), (start, sid))
                every.setdefault((ws, sid), []).append((start, path, mtime >= cutoff, regular))
            if mtime < cutoff:
                continue
            src.ask()
            try:
                if not regular:
                    raise OSError("not a regular file")
                if sid == "pending":
                    pendings.append({"started": first_started(path), "start": start})
                else:
                    run = parse_run(path)
                    run.update(workspace=ws, session=sid, start=start, path=path,
                               baseline=False)
                    runs.append(run)
            except OSError:
                src.fail("a log file could not be read")
                continue
            src.answer()
    runs.sort(key=lambda r: (r["start"], r["path"]))
    # A run's share is its total less the session's previous run's, so a session's first run
    # in the window needs the nearest older runs' last total: its own total holds the whole
    # session before it, and a long chat thread would count itself again every day.
    baselines, seen = [], set()
    for r in runs:
        if r["session"] in seen:
            continue
        seen.add(r["session"])
        older = sorted((f for f in every[(r["workspace"], r["session"])]
                        if not f[2] and f[0] < r["start"]), reverse=True)[:BASELINE_FILES]
        for start, path, _in_window, regular in older:
            src.ask()
            try:
                if not regular:
                    raise OSError("not a regular file")
                run = parse_run(path)
            except OSError:
                src.fail("an older log, read for its total, could not be read")
                continue
            src.answer()
            run.update(workspace=r["workspace"], session=r["session"], start=start, path=path,
                       baseline=True)
            baselines.append(run)
    return runs, baselines, pendings, firsts


# --------------------------------------------------------------------------- #
# What a run was, and what it cost
# --------------------------------------------------------------------------- #
def classify(runs, cfg, planners, reprompts, firsts):
    """Each run's kind, and how it was told: by its folder, the idea gate's record, being the
    folder's first run, a ledger row, or its opening prompt. In that order. Nothing a
    session can write decides a run's kind, except an opening prompt's fixed words."""
    for r in runs:
        ws = r["workspace"]
        if ws.startswith(CHAT_PREFIX):
            r["kind"], r["how"] = "tech_lead", "folder"
        elif ws.startswith(cfg["reviews_team_key"] + "-"):
            r["kind"], r["how"] = "review", "folder"
        elif planners and ws in planners:
            r["kind"], r["how"] = "planner", "record"
        elif firsts.get(ws) == (r["start"], r["session"]):
            r["kind"], r["how"] = "coding", "first run"
        else:
            hit = [kind for at, ticket, kind in reprompts if ticket == ws
                   and at - MATCH_BEFORE <= r["start"] <= at + MATCH_AFTER]
            if hit:
                r["kind"], r["how"] = hit[0], "ledger"
            elif r["prompt"] in ("conflict_fix", "bounce_fix", "catch_up"):
                r["kind"], r["how"] = r["prompt"], "prompt"
            else:
                r["kind"], r["how"] = "other_later", "no mark"


def price(runs, baselines):
    """Each run's own share of its session's running totals, from its last result: that total
    less the previous run's, per model as well. A smaller figure means the session started
    again, so the whole figure is the share. A run with no usable total (a crash's zeroed
    one, or none yet) is unpriced and is never the next run's base. A run's share is None,
    never 0, when it cannot be priced."""
    chains = {}
    for r in baselines + runs:
        chains.setdefault(r["session"], []).append(r)
    for chain in chains.values():
        chain.sort(key=lambda r: (r["start"], r["path"]))
        prev_total, prev_usage = None, {}
        for r in chain:
            r["cost"], r["models"] = None, {}
            r["tokens"] = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            if r["total"] is None:
                continue
            restart = prev_total is not None and r["total"] < prev_total
            r["cost"] = round(r["total"] if prev_total is None or restart
                              else r["total"] - prev_total, 6)
            for name, figs in r["usage"].items():
                before = prev_usage.get(name)
                whole = before is None or restart or any(a < b for a, b in zip(figs, before))
                share = figs if whole else [a - b for a, b in zip(figs, before)]
                r["models"][name] = round(share[0], 6)
                for slot, value in zip(("input", "output", "cache_read", "cache_write"),
                                       share[1:]):
                    r["tokens"][slot] += int(round(value))
            prev_total = r["total"]
            prev_usage = dict(prev_usage, **r["usage"])


def scan(cfg, now):
    """Everything one pass reads, priced and classified. Pure over files: --selftest calls
    it, and compares each run with what its log was written to say."""
    planners, planner_src = read_planner(cfg, now)
    reprompts, ledger_src = read_ledger(cfg, now)
    logs_src = Source(now)
    found = read_logs(cfg, now, logs_src)
    scanned = {"planners": planners, "reprompts": reprompts, "runs": None, "baselines": [],
               "pendings": [], "sources": {"logs": logs_src, "ledger": ledger_src,
                                           "planner": planner_src}}
    if found is None:
        return scanned
    runs, baselines, pendings, firsts = found
    classify(runs, cfg, planners, reprompts, firsts)
    price(runs, baselines)
    for r in runs:
        r["seconds"] = (int(max(0.0, r["last_at"] - r["first_at"]))
                        if r["last_at"] is not None else 0)
        r["last"] = r["last_at"] if r["last_at"] is not None else r["start"]
    scanned.update(runs=runs, baselines=baselines, pendings=pendings)
    return scanned




def unmatched_placeholders(scanned):
    """The placeholders no run follows. Every run leaves one beside its log, so one that no
    run's start matches, or whose first line gave no start, is a run that never reached the
    model."""
    real = set(r["started"] for r in (scanned["runs"] or []) + scanned["baselines"]
               if r["started"])
    return [p for p in scanned["pendings"] if p["started"] is None or p["started"] not in real]


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_usage_runs.py", description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "usage-runs-selftest-run-")
    p.print_help()
    return EXIT_USAGE


# --------------------------------------------------------------------------- #
# --selftest — offline, in a temp folder only; the clock stood in for
# --------------------------------------------------------------------------- #
def selftest():
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        good = got == want
        print("[%s] %s%s" % ("PASS" if good else "FAIL", name,
                             "" if good else "  (got %r, want %r)" % (got, want)))
        if not good:
            fails.append(name)

    _cases(check)
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def selftest_kit():
    """The synthetic logs the selftests write, and what watches a pass for writes and sockets.
    This module's selftest and scripts/pipeline_usage_local.py's both use them, so the two
    halves of the usage job are tried on the same world."""
    import builtins
    import socket
    import types

    T = calendar.timegm((2026, 11, 3, 12, 0, 0))          # 2026-11-03T12:00:00Z, 15:00 local
    LEAK = "SENTINEL-LEAK-CHECK-0f3a"                      # text no field may carry
    OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"

    def U(text):
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))

    def ms(t):
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"

    def stamp(t):
        return time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime(t)) + "-000Z"

    def sid(n):
        return "%08d-0000-4000-8000-%012d" % (n, n)

    def figures(total, model):
        """What a result's modelUsage holds for a session whose running total is `total`:
        every figure follows the total, so a run's share of each is known by arithmetic."""
        def r(x):
            return int(round(x))
        return {model: {"costUSD": total, "inputTokens": r(total * 10),
                        "outputTokens": r(total * 100), "cacheReadInputTokens": r(total * 1000),
                        "cacheCreationInputTokens": r(total * 10)}}

    def jsonl(path, rows, mtime):
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write((row if isinstance(row, str) else json.dumps(row)) + "\n")
        os.utime(path, (mtime, mtime))

    def write_run(logs, folder, session, start, dur=60, total=None, key="none", prompt=None,
                  model=OPUS, crash=False, error=False, events=(), init=True, reply=True,
                  extra_usage=None, placeholder=True, junk=()):
        """One run's log, as the dispatcher writes it, and the placeholder beside it. Every
        text a model or a person writes carries LEAK, and no field may."""
        folder_path = os.path.join(logs, folder)
        os.makedirs(folder_path, exist_ok=True)
        rows = [{"type": "session-metadata", "sessionId": session, "startedAt": ms(start),
                 "title": LEAK}]

        def sdk(offset, message):
            rows.append({"type": "sdk-message", "timestamp": ms(start + offset),
                         "message": message})
        if init:
            sdk(0, {"type": "system", "subtype": "init", "model": model, "apiKeySource": key,
                    "cwd": "/work/" + LEAK, "tools": [LEAK]})
        if prompt is not False:
            sdk(0, {"type": "user", "parent_tool_use_id": None, "message": {
                "role": "user", "content": [{"type": "text",
                                             "text": (prompt or "plain words ") + LEAK}]}})
        if reply:
            # A reply's own usage is logged as the reply starts: one output token, whatever
            # the reply goes on to say.
            sdk(dur // 2, {"type": "assistant", "parent_tool_use_id": None, "message": {
                "id": "msg-" + session[:8], "model": model,
                "content": [{"type": "text", "text": "reply " + LEAK}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 1,
                          "cache_creation_input_tokens": 1}}})
        for offset, info in events:
            sdk(offset, {"type": "rate_limit_event", "rate_limit_info": info})
        rows.extend(junk)
        if crash:
            sdk(dur, {"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "total_cost_usd": 0, "result": LEAK, "modelUsage": figures(0.0, model)})
        elif total is not None:
            sdk(dur, {"type": "result", "subtype": "error_max_turns" if error else "success",
                      "is_error": error, "total_cost_usd": total, "result": "final " + LEAK,
                      "modelUsage": dict(figures(total, model), **(extra_usage or {}))})
        name = "session-%s-%s.jsonl" % (session, stamp(start))
        jsonl(os.path.join(folder_path, name), rows, start + dur)
        if placeholder:
            jsonl(os.path.join(folder_path, "session-pending-%s.jsonl" % stamp(start)),
                  [{"type": "session-metadata", "startedAt": ms(start)}], start)
        return os.path.join(folder_path, name)

    CONFLICT = ("**Stage E conflict fix 1 of 3** — an automated re-prompt from the bounce "
                "driver for PR #5. `main` moved after this pull request went green.\n"
                "_stage-e-conflict/1 acme/app#5 episode=1 k=1 — conflict fix 1 of 3, sent by "
                "the Stage E bounce driver in answer to the conflict monitor's request._ ")
    BOUNCE = ("**Stage E bounce %d of 3** — an automated re-prompt from the bounce driver for "
              "PR #5.\n_stage-e-bounce/1 acme/app#5 n=%d — bounce %d of 3, sent by the Stage E "
              "bounce driver._ ")
    CATCHUP = ("**Catch-up.** This delegation was never picked up, so it is being sent "
               "again.\n\nCatch-up receipt: abc123 ")

    def week_event(resets):
        return {"status": "allowed", "rateLimitType": "seven_day", "utilization": 0.21,
                "resetsAt": U(resets)}

    def std_runs(weekly):
        """(label, folder, session, start, kwargs) for every run of the standard world. The
        weekly event is the plan's reset: "ok" names one still ahead, "none" has no weekly
        event at all, "stale" one whose week is over, "far" one whose week has not begun."""
        newest = {"ok": [(30, week_event("2026-11-06T09:00:00Z"))], "none": [],
                  "stale": [(30, week_event("2026-11-03T11:00:00Z"))],
                  "far": [(30, week_event("2026-11-20T09:00:00Z"))]}[weekly]
        older = [] if weekly == "none" else [(100, week_event("2026-11-02T09:00:00Z"))]
        weird = {"status": LEAK, "rateLimitType": LEAK, "utilization": -1, "resetsAt": 1e15,
                 "surpassedThreshold": "high"}
        return [
            # the tech lead: one thread of two turns, and a thread that signed in with a key
            ("C1", "slack-Ev1", sid(1), "2026-11-03T08:00:00Z",
             dict(dur=30, total=0.25, model=SONNET, events=[(10, {
                 "status": "allowed", "rateLimitType": "five_hour", "utilization": 0.12,
                 "resetsAt": U("2026-11-03T14:00:00Z")})])),
            ("C2", "slack-Ev1", sid(1), "2026-11-03T09:00:00Z",
             dict(dur=60, total=0.40, model=SONNET, events=[(20, {
                 "status": "allowed_warning", "rateLimitType": "five_hour", "utilization": 0.30,
                 "surpassedThreshold": 0.25, "resetsAt": U("2026-11-03T14:00:00Z")})])),
            ("C3", "slack-Ev2", sid(2), "2026-11-02T22:30:00Z",
             dict(dur=20, total=0.10, model=SONNET, key="ANTHROPIC_API_KEY")),
            # a review, twice
            ("V1", "REV-9", sid(3), "2026-11-02T10:00:00Z", dict(dur=60, total=0.40)),
            ("V2", "REV-9", sid(3), "2026-11-03T10:00:00Z",
             dict(dur=90, total=0.70, events=newest)),
            # a planning run, with an event that names no window
            ("P1", "ENG-20", sid(4), "2026-11-02T11:00:00Z",
             dict(dur=120, total=1.10, events=[(10, {"status": "allowed"})])),
            # a coding ticket: first run, a bounce and a conflict fix the ledger names, a
            # conflict fix and a bounce its prompt names, a catch-up, and a reply
            ("K1", "ENG-10", sid(5), "2026-11-01T10:00:00Z",
             dict(dur=600, total=3.00, events=older)),
            ("K2", "ENG-10", sid(5), "2026-11-02T15:02:00Z",
             dict(dur=100, total=3.50, prompt=BOUNCE % (1, 1, 1))),
            ("K3", "ENG-10", sid(5), "2026-11-02T16:00:30Z",
             dict(dur=100, total=3.75, events=[(50, {
                 "status": "rejected", "rateLimitType": "overage", "utilization": 1.0,
                 "resetsAt": U("2026-11-30T00:00:00Z")})])),
            ("K4", "ENG-10", sid(5), "2026-11-02T17:00:00Z",
             dict(dur=100, total=4.00, prompt=CONFLICT)),
            ("K5", "ENG-10", sid(5), "2026-11-02T18:00:00Z",
             dict(dur=100, total=4.10, prompt=BOUNCE % (2, 2, 2))),
            ("K6", "ENG-10", sid(5), "2026-11-02T19:00:00Z",
             dict(dur=100, total=4.15, error=True, prompt=CATCHUP)),
            ("K7", "ENG-10", sid(5), "2026-11-02T20:00:00Z",
             dict(dur=300, total=4.30, key=LEAK, events=[(50, weird)], extra_usage={
                 LEAK: {"costUSD": 0.0, "inputTokens": 0, "outputTokens": 0,
                        "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}})),
            # a key, a crash, a smaller total (the session started again)
            ("A1", "ENG-11", sid(6), "2026-11-02T09:00:00Z",
             dict(dur=60, total=2.00, key="ANTHROPIC_API_KEY")),
            ("A2", "ENG-11", sid(6), "2026-11-02T10:30:00Z", dict(dur=40, crash=True)),
            ("A3", "ENG-11", sid(6), "2026-11-02T11:30:00Z",
             dict(dur=50, total=2.60, key="apiKeyHelper")),
            ("A4", "ENG-11", sid(6), "2026-11-02T12:30:00Z", dict(dur=50, total=0.30)),
            # an old session: its run in the window is a later run, and costs its own share
            ("B0", "ENG-13", sid(7), "2026-10-20T10:00:00Z",
             dict(dur=600, total=5.00, placeholder=False)),
            ("B1", "ENG-13", sid(7), "2026-11-03T07:00:00Z", dict(dur=60, total=5.40)),
            ("E14", "ENG-14", sid(8), "2026-11-03T05:00:00Z", dict(dur=30, total=0.50)),
            # before the week began, inside the lookback
            ("E15", "ENG-15", sid(9), "2026-10-28T10:00:00Z", dict(dur=30, total=0.80)),
            # a run still going: no init, no reply, no result
            ("E17", "ENG-17", sid(10), "2026-11-03T11:00:00Z",
             dict(dur=0, init=False, reply=False)),
            # a folder that is not a ticket id
            ("N1", "notes-" + LEAK, sid(11), "2026-11-03T06:00:00Z", dict(dur=30, total=0.20)),
        ]

    def std_world(root, weekly="ok", **cfg_over):
        """The standard world under `root`: 22 runs the window holds, one older log read for
        its total, the placeholders beside them, and three placeholders no run follows, plus
        Stage E's ledger and review record and the idea gate's record."""
        logs = os.path.join(root, "disp", "logs")
        e_dir, a_dir = os.path.join(root, "state-e"), os.path.join(root, "state-a")
        ids = {}
        for label, folder, session, start, kw in std_runs(weekly):
            write_run(logs, folder, session, U(start), **kw)
            ids[(folder, U(start))] = label
        # placeholders no real run follows: two in the window, one older than it, and one the
        # window reads but whose day is not in the lookback
        orphans = os.path.join(logs, "ENG-12")
        os.makedirs(orphans)
        jsonl(os.path.join(orphans, "session-pending-2026-11-03T06-50-00-000Z.jsonl"),
              [{"type": "session-metadata", "startedAt": "2026-11-03T06:50:00.000Z"}],
              U("2026-11-03T06:50:00Z"))
        jsonl(os.path.join(orphans, "session-pending-2026-11-03T06-55-00-000Z.jsonl"), [],
              U("2026-11-03T06:55:00Z"))
        jsonl(os.path.join(orphans, "session-pending-2026-10-01T10-00-00-000Z.jsonl"),
              [{"type": "session-metadata", "startedAt": "2026-10-01T10:00:00.000Z"}],
              U("2026-10-01T10:00:00Z"))
        jsonl(os.path.join(orphans, "session-pending-2026-10-26T20-00-00-000Z.jsonl"),
              [{"type": "session-metadata", "startedAt": "2026-10-26T20:00:00.000Z"}],
              U("2026-10-26T20:00:00Z"))
        os.makedirs(os.path.join(e_dir, "outcomes"))
        with open(os.path.join(e_dir, "outcomes", "acme__app__pr-5.json"), "w") as fh:
            json.dump({"outcome_schema": "pipeline-review-outcome/1", "repo": "acme/app",
                       "pr": 5, "ticket_id": "ENG-10", "summary": LEAK,
                       "findings": [{"summary": LEAK}]}, fh)
        ledger = [
            {"at": "2026-11-02T15:00:00Z", "repo": "acme/app", "pr": 5, "outcome": "delivered"},
            {"at": "2026-11-02T16:00:00Z", "repo": "acme/app", "pr": 5,
             "outcome": "conflict-delivered"},
            # rows that name no delivery decide nothing, and a pull request with no review
            # record has no ticket
            {"at": "2026-11-02T16:59:50Z", "repo": "acme/app", "pr": 5, "outcome": "conflict"},
            {"at": "2026-11-02T17:00:00Z", "repo": "acme/app", "pr": 5, "outcome": "spent"},
            {"at": "2026-11-02T18:00:00Z", "repo": "acme/other", "pr": 99,
             "outcome": "delivered"},
            {"at": "2026-11-02T20:00:00Z", "repo": "acme/app", "pr": 5,
             "outcome": "send-failed"},
        ]
        jsonl(os.path.join(e_dir, "bounce-ledger.jsonl"), ledger, 0)
        os.makedirs(a_dir)
        with open(os.path.join(a_dir, "seen.json"), "w") as fh:
            json.dump({"triggers": {
                "ENG-19": {"idea": "ENG-19", "run_ticket": "ENG-20", "note": LEAK},
                "ENG-29": {"idea": "ENG-29", "run_ticket": "not a ticket " + LEAK}}}, fh)
        cfg = {"dispatcher_home": os.path.join(root, "disp"), "stage_e_state_dir": e_dir,
               "stage_a_state_dir": a_dir, "reviews_team_key": "REV",
               "out": os.path.join(root, "out", "usage.json"), "lookback_days": 8}
        cfg.update(cfg_over)
        return cfg, ids

    def hostile_world(root):
        """One run whose log is hostile: 70 model names and three junk ones, NaN and Infinity,
        fields of the wrong type, an impossible date, and lines that are not JSON."""
        cfg_x = {"dispatcher_home": os.path.join(root, "disp"),
                 "stage_e_state_dir": "", "stage_a_state_dir": "", "reviews_team_key": "REV",
                 "out": os.path.join(root, "out", "usage.json"), "lookback_days": 8}
        crowd = dict(("m%03d" % n, {"costUSD": 0.01 * (n + 1), "outputTokens": 1})
                     for n in range(70))
        crowd.update({"x" * 5000: {"costUSD": 1.0}, "line\nbreak": {"costUSD": 1.0},
                      "": {"costUSD": 1.0}})
        nasty = {"status": {"a": 1}, "rateLimitType": ["five_hour"], "utilization": float("inf"),
                 "resetsAt": float("nan"), "surpassedThreshold": True}
        write_run(os.path.join(cfg_x["dispatcher_home"], "logs"), "ENG-40", sid(40),
                  U("2026-11-03T08:00:00Z"), total=5.0, extra_usage=crowd, key={"x": 1},
                  events=[(5, nasty), (6, week_event("2026-11-06T09:00:00Z"))],
                  junk=[json.dumps({"type": "sdk-message", "timestamp": "2026-02-30T00:00:00Z",
                                    "message": {"type": "result", "is_error": "yes",
                                                "total_cost_usd": "9", "modelUsage": []}}),
                        "not json at all", json.dumps([1, 2, 3]),
                        '{"type": "sdk-message", "timestamp": "2026-11-03T08:00:30Z", '
                        '"message": {"type": "rate_limit_event", "rate_limit_info": '
                        '{"rateLimitType": "five_hour", "resetsAt": 1e999, "utilization": NaN}}}'])
        return cfg_x

    class Watch(object):
        """While open, notes every path written and every socket tried: the proof of what a
        pass never does."""
        writers = ("replace", "rename", "unlink", "remove", "rmdir", "mkdir", "makedirs",
                   "chmod", "utime", "symlink", "link", "truncate")

        def __init__(self):
            self.wrote, self.net = [], []

        def note(self, *paths):
            for p in paths:
                if isinstance(p, (str, bytes)):
                    self.wrote.append(os.path.realpath(os.fsdecode(p)))

        def __enter__(self):
            watch = self
            self.real_open, self.real_os_open = builtins.open, os.open
            self.real_os = dict((n, getattr(os, n)) for n in self.writers)

            def spy_open(file, mode="r", *a, **k):
                if any(c in str(mode) for c in "wax+"):
                    watch.note(file)
                return watch.real_open(file, mode, *a, **k)

            def spy_os_open(path, flags, *a, **k):
                if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
                    watch.note(path)
                return watch.real_os_open(path, flags, *a, **k)

            def spy_os(name):
                def spy(*args, **kwargs):
                    watch.note(*args[:2])
                    return watch.real_os[name](*args, **kwargs)
                return spy

            def no_net(*_a, **_k):
                watch.net.append("tried")
                raise OSError("no network in the selftest")
            self.own_connect = socket.socket.__dict__.get("connect")
            self.real_create, self.real_lookup = socket.create_connection, socket.getaddrinfo
            builtins.open, os.open = spy_open, spy_os_open
            for name in self.writers:
                setattr(os, name, spy_os(name))
            socket.socket.connect, socket.create_connection, socket.getaddrinfo = (
                no_net, no_net, no_net)
            return self

        def __exit__(self, *_exc):
            builtins.open, os.open = self.real_open, self.real_os_open
            for name in self.writers:
                setattr(os, name, self.real_os[name])
            if self.own_connect is None:
                del socket.socket.connect
            else:
                socket.socket.connect = self.own_connect
            socket.create_connection, socket.getaddrinfo = self.real_create, self.real_lookup
            return False

    return types.SimpleNamespace(
        T=T, LEAK=LEAK, OPUS=OPUS, SONNET=SONNET, U=U, sid=sid, write_run=write_run,
        week_event=week_event, std_world=std_world, hostile_world=hostile_world,
        CONFLICT=CONFLICT, BOUNCE=BOUNCE, CATCHUP=CATCHUP, Watch=Watch)


def _cases(check):
    import contextlib
    import inspect
    import io
    import tempfile

    kit = selftest_kit()
    T, LEAK, OPUS, U, sid = kit.T, kit.LEAK, kit.OPUS, kit.U, kit.sid
    write_run, std_world = kit.write_run, kit.std_world
    CONFLICT, BOUNCE, CATCHUP, Watch = kit.CONFLICT, kit.BOUNCE, kit.CATCHUP, kit.Watch
    hostile_world = kit.hostile_world

    # What each run's log was written to say: (kind, how, own spend, output tokens, seconds).
    EXPECT = {
        "C1": ("tech_lead", "folder", 0.25, 25, 30), "C2": ("tech_lead", "folder", 0.15, 15, 60),
        "C3": ("tech_lead", "folder", 0.10, 10, 20), "V1": ("review", "folder", 0.40, 40, 60),
        "V2": ("review", "folder", 0.30, 30, 90), "P1": ("planner", "record", 1.10, 110, 120),
        "K1": ("coding", "first run", 3.00, 300, 600),
        "K2": ("bounce_fix", "ledger", 0.50, 50, 100),
        "K3": ("conflict_fix", "ledger", 0.25, 25, 100),
        "K4": ("conflict_fix", "prompt", 0.25, 25, 100),
        "K5": ("bounce_fix", "prompt", 0.10, 10, 100),
        "K6": ("catch_up", "prompt", 0.05, 5, 100),
        "K7": ("other_later", "no mark", 0.15, 15, 300),
        "A1": ("coding", "first run", 2.00, 200, 60),
        "A2": ("other_later", "no mark", None, 0, 40),
        "A3": ("other_later", "no mark", 0.60, 60, 50),
        "A4": ("other_later", "no mark", 0.30, 30, 50),
        "B1": ("other_later", "no mark", 0.40, 40, 60),
        "E14": ("coding", "first run", 0.50, 50, 30),
        "E15": ("coding", "first run", 0.80, 80, 30),
        "E17": ("coding", "first run", None, 0, 0),
        "N1": ("coding", "first run", 0.20, 20, 30),
    }

    def facts_for(cfg, ids):
        scanned = scan(cfg, lambda: T)
        out = {}
        for r in scanned["runs"] or []:
            out[ids[(r["workspace"], r["start"])]] = (
                r["kind"], r["how"], r["cost"], r["tokens"]["output"], r["seconds"])
        return out

    def facts(root):
        """Every run of a fresh standard world as the job reads it, for the checks and for
        the mutants: a mutant is caught when this is no longer EXPECT."""
        cfg, ids = std_world(root)
        return facts_for(cfg, ids)

    def snapshot(*folders):
        """Every file under the folders, with its size and modification time: what a job that
        only reads leaves exactly as it found it."""
        out = []
        for folder in folders:
            for base, _dirs, files in os.walk(folder):
                for name in files:
                    path = os.path.join(base, name)
                    st = os.lstat(path)
                    out.append((path, st.st_size, st.st_mtime_ns))
        return sorted(out)

    # Runs the plan's limit cut off (KIT-470): one run for each rule. Each is its own ticket, so
    # the label is read back from the workspace.
    LIMIT_AT = "2026-11-03T14:00:00Z"
    LIMIT_RUNS = (
        # label, workspace, run arguments
        ("rejected then an error", "ENG-61", dict(total=0.5, error=True, events=[(10, {
            "status": "rejected", "rateLimitType": "five_hour", "utilization": 1.0,
            "resetsAt": U(LIMIT_AT)})])),
        ("rejected and never ended", "ENG-62", dict(events=[(10, {
            "status": "rejected", "rateLimitType": "seven_day", "utilization": 1.0,
            "resetsAt": U("2026-11-06T09:00:00Z")})])),
        ("rejected then a success", "ENG-63", dict(total=0.5, events=[(10, {
            "status": "rejected", "rateLimitType": "five_hour", "resetsAt": U(LIMIT_AT)})])),
        ("only a warning, then an error", "ENG-64", dict(total=0.5, error=True, events=[(10, {
            "status": "allowed_warning", "rateLimitType": "five_hour", "utilization": 0.9,
            "surpassedThreshold": 0.9, "resetsAt": U(LIMIT_AT)})])),
        ("rejected with a reset that is no time, then a crash", "ENG-65", dict(
            crash=True, events=[(10, {"status": "rejected", "rateLimitType": "seven_day_opus",
                                      "resetsAt": LEAK})])),
        ("rejected in a window the list lacks, then an error", "ENG-66", dict(
            total=0.5, error=True, events=[(10, {"status": "rejected", "rateLimitType": LEAK})])),
    )

    def limit_facts(root):
        """What the reader says of each run in LIMIT_RUNS: its limit_stop, by label."""
        logs = os.path.join(root, "disp", "logs")
        names = {}
        for n, (label, folder, kw) in enumerate(LIMIT_RUNS):
            start = U("2026-11-03T06:00:00Z") + n * 600
            write_run(logs, folder, sid(60 + n), start, **kw)
            names[(folder, start)] = label
        cfg_l = {"dispatcher_home": os.path.join(root, "disp"), "stage_e_state_dir": "",
                 "stage_a_state_dir": "", "reviews_team_key": "REV",
                 "out": os.path.join(root, "out", "usage.json"), "lookback_days": 8}
        return dict((names[(r["workspace"], r["start"])], r["limit_stop"])
                    for r in scan(cfg_l, lambda: T)["runs"])

    LIMIT_EXPECT = {
        "rejected then an error": {"window": "five_hour", "resets_at": U(LIMIT_AT)},
        "rejected and never ended": {"window": "seven_day", "resets_at": U("2026-11-06T09:00:00Z")},
        "rejected then a success": None,
        "only a warning, then an error": None,
        "rejected with a reset that is no time, then a crash": {"window": "seven_day_opus",
                                                                "resets_at": None},
        "rejected in a window the list lacks, then an error": {"window": "other",
                                                               "resets_at": None},
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        # -- a healthy world, read ------------------------------------------------------------
        cfg, ids = std_world(os.path.join(tmp, "std"))
        inputs = (os.path.join(tmp, "std", "disp"), cfg["stage_e_state_dir"],
                  cfg["stage_a_state_dir"])
        before = snapshot(*inputs)
        scanned = scan(cfg, lambda: T)
        recs = dict((s, scanned["sources"][s].record()) for s in SOURCES)
        check("a read only reads: every log, ledger and record is left as it was found",
              snapshot(*inputs), before)
        check("every source read, each saying what it was asked and what it answered",
              [(s, recs[s]["state"], recs[s]["asked"], recs[s]["answered"], recs[s]["read_at"])
               for s in SOURCES],
              [("logs", READ, 48, 48, "2026-11-03T12:00:00Z"),
               ("ledger", READ, 3, 3, "2026-11-03T12:00:00Z"),
               ("planner", READ, 1, 1, "2026-11-03T12:00:00Z")])
        check("a placeholder no run follows is a run that never reached the model: three of the "
              "25 read, one of them with no start, and none of the 22 that match",
              (sorted(str(p["started"]) for p in unmatched_placeholders(scanned)),
               len(scanned["pendings"])),
              (["2026-10-26T20:00:00.000Z", "2026-11-03T06:50:00.000Z", "None"], 25))
        by_label = dict((ids[(r["workspace"], r["start"])], r) for r in scanned["runs"])
        check("how a run signed in is kept as the SDK's word, other for a word its list lacks, "
              "and none for a run with no init line",
              dict((k, by_label[k]["key_source"]) for k in ("A1", "A3", "C3", "K7", "E17", "V1")),
              {"A1": "ANTHROPIC_API_KEY", "A3": "apiKeyHelper", "C3": "ANTHROPIC_API_KEY",
               "K7": "other", "E17": None, "V1": "none"})
        check("a run's usage events: its newest per window, its newest weekly one that names a "
              "reset, and a time for each, one that names no window included",
              (by_label["C2"]["events"]["five_hour"]["surpassed"],
               by_label["V2"]["weekly"]["resets_at"], by_label["P1"]["events"],
               len(by_label["P1"]["event_times"]), by_label["C1"]["weekly"]),
              (0.25, U("2026-11-06T09:00:00Z"), {}, 1, None))
        flat = json.dumps(
            [dict((k, v) for k, v in r.items() if k not in ("workspace", "path"))
             for r in scanned["runs"] + scanned["baselines"]]
            + scanned["pendings"] + list(recs.values()), sort_keys=True)
        check("FIELDS ONLY at the run level: no prompt, reply, result, title, working folder or "
              "tool name is kept in a run, a placeholder or a source, only a folder's own name",
              (LEAK in flat, [r["workspace"] for r in scanned["runs"] if LEAK in r["workspace"]]),
              (False, ["notes-" + LEAK]))

        got = facts_for(cfg, ids)
        diff = dict((k, (got.get(k), want)) for k, want in EXPECT.items() if got.get(k) != want)
        check("each run is the kind, own spend, output tokens and length its log was written "
              "to say", (diff, sorted(set(got) - set(EXPECT))), ({}, []))
        check("the world holds a run of every kind the module lists, and of no other",
              sorted(set(v[0] for v in got.values())), sorted(KINDS))
        check("a crash's zeroed total is unpriced, never zero, and not the next run's base",
              (got["A2"][2], got["A3"][2]), (None, 0.60))
        check("a smaller total means the session started again: the run costs its whole total",
              got["A4"][2], 0.30)
        check("a run still going has no result yet: unpriced", got["E17"][2], None)
        check("an error result that does carry a total is priced", got["K6"][2], 0.05)
        check("a session's run in the window costs its own share, not the whole session so "
              "far: the older log is read for its total", got["B1"][2], 0.40)
        check("…and is not a run: the window holds 22 and no more",
              (len(got), "B0" in got), (22, False))
        check("the first run is the first by file name: an old ticket's later run is not "
              "coding", got["B1"][:2], ("other_later", "no mark"))
        check("a ledger row outranks the prompt that also names the bounce", got["K2"][:2],
              ("bounce_fix", "ledger"))
        check("a conflict fix's record line says 'Stage E bounce driver', and it is still a "
              "conflict fix", got["K4"][:2], ("conflict_fix", "prompt"))
        check("a ledger row that names no delivery, or a pull request with no review record, "
              "decides nothing", (got["K7"][:2], got["K5"][:2]),
              (("other_later", "no mark"), ("bounce_fix", "prompt")))
        check("tokens are the result's own running totals, never the replies' usage lines",
              [got[k][3] for k in ("K1", "K2", "K3")], [300, 50, 25])

        # -- sources that fail, read ----------------------------------------------------------
        def rec(cfg_w, source):
            r = scan(cfg_w, lambda: T)["sources"][source].record()
            return (r["state"], r["asked"], r["answered"], r["reason"])
        gone = os.path.join(tmp, "gone")
        check("a logs folder that cannot be listed is could-not-read, and no run is read",
              (scan(dict(cfg, dispatcher_home=gone), lambda: T)["runs"],
               rec(dict(cfg, dispatcher_home=gone), "logs")),
              (None, (COULD_NOT, 1, 0, "the dispatcher's logs folder could not be listed "
                                       "(FileNotFoundError): is dispatcher_home right?")))
        cfg_h, ids_h = std_world(os.path.join(tmp, "nostate"),
                                 stage_e_state_dir=gone, stage_a_state_dir=gone)
        check("a state folder that does not exist is could-not-read and names the config key; "
              "one the config empties is off and asks nothing",
              (rec(cfg_h, "ledger"), rec(cfg_h, "planner"),
               rec(dict(cfg, stage_e_state_dir="", stage_a_state_dir=""), "ledger"),
               rec(dict(cfg, stage_e_state_dir="", stage_a_state_dir=""), "planner")),
              ((COULD_NOT, 1, 0, "Stage E's state folder does not exist: is stage_e_state_dir "
                                 "right? Set it to \"\" to turn this source off"),
               (COULD_NOT, 1, 0, "the idea gate's state folder does not exist: is "
                                 "stage_a_state_dir right? Set it to \"\" to turn this source "
                                 "off"),
               (OFF, 0, 0, None), (OFF, 0, 0, None)))
        got_h = facts_for(cfg_h, ids_h)
        check("a source that was not read leaves its kinds to be told from what is left: a "
              "bounce by its prompt, a conflict fix with no mark, a planning run as coding",
              (got_h["K2"][:2], got_h["K3"][:2], got_h["P1"][:2]),
              (("bounce_fix", "prompt"), ("other_later", "no mark"), ("coding", "first run")))

        w_rec, w_torn, w_gate, w_none, w_odd = (
            std_world(os.path.join(tmp, n))[0] for n in ("rec", "torn", "gate", "none", "odd"))
        with open(os.path.join(w_rec["stage_e_state_dir"], "outcomes", "broken.json"), "w") as fh:
            fh.write("{")
        with open(os.path.join(w_torn["stage_e_state_dir"], "bounce-ledger.jsonl"), "a") as fh:
            fh.write('{"at": "2026-11-02T21:00:00Z", "repo": "acme/ap\n[1, 2]\n')
        for cfg_w, text in ((w_gate, "not json"), (w_none, "{}")):
            with open(os.path.join(cfg_w["stage_a_state_dir"], "seen.json"), "w") as fh:
                fh.write(text)
        check("a review record that cannot be read, or ledger lines that are not rows, leave the "
              "ledger partly read, said and counted",
              (rec(w_rec, "ledger"), rec(w_torn, "ledger")),
              ((PARTLY, 4, 3, "a review record could not be read"),
               (PARTLY, 3, 2, "2 line(s) of the bounce ledger could not be read")))
        check("an idea-gate record that is not JSON, or holds no triggers, is could-not-read: "
              "no planning run is guessed",
              (rec(w_gate, "planner"), rec(w_none, "planner")),
              ((COULD_NOT, 1, 0, "the idea gate's seen.json could not be read"),
               (COULD_NOT, 1, 0, "the idea gate's seen.json holds no triggers")))
        odd = os.path.join(w_odd["dispatcher_home"], "logs", "ENG-14")
        oddities = [os.path.join(odd, "session-%s-2026-11-03T05-%02d-00-000Z.jsonl" % (sid(8), m))
                    for m in (10, 20, 30)]
        os.mkdir(oddities[0])
        os.mkfifo(oddities[1])
        os.symlink(os.path.join(tmp, "elsewhere.jsonl"), oddities[2])
        for path in oddities:
            os.utime(path, (T, T), follow_symlinks=False)
        check("a folder, a pipe and a link named like run files are asked and not answered, "
              "never opened: the logs are partly read, and the read does not hang",
              rec(w_odd, "logs"),
              (PARTLY, 51, 48, "a log file could not be read (and 2 more)"))

        # -- an older log is read for its total, and no more than three of them ---------------
        cfg_c = {"dispatcher_home": os.path.join(tmp, "cap", "disp"), "stage_e_state_dir": "",
                 "stage_a_state_dir": "", "reviews_team_key": "REV",
                 "out": os.path.join(tmp, "cap", "out", "usage.json"), "lookback_days": 8}
        logs_c = os.path.join(cfg_c["dispatcher_home"], "logs")
        for folder, session, plan in (
                ("ENG-16", sid(20), ((1.0, False), (2.0, False), (None, True), (None, True),
                                     (None, True))),
                ("ENG-18", sid(21), ((3.0, False), (None, True), (None, True)))):
            for n, (total, crash) in enumerate(plan):
                write_run(logs_c, folder, session, U("2026-10-%02dT10:00:00Z" % (4 + n)),
                          total=total, crash=crash, placeholder=False)
            write_run(logs_c, folder, session, U("2026-11-03T08:00:00Z"), total=9.0,
                      placeholder=False)
        reads, real_parse = [], parse_run

        def spy(path):
            reads.append((os.path.basename(os.path.dirname(path)),
                          re.search(r"([0-9]{4}-[0-9]{2}-[0-9]{2})T", path).group(1)))
            return real_parse(path)
        globals()["parse_run"] = spy
        try:
            scanned_c = scan(cfg_c, lambda: T)
        finally:
            globals()["parse_run"] = real_parse
        check("a session's older logs are read for their total newest first, three at most",
              sorted(reads),
              sorted([("ENG-16", "2026-11-03"), ("ENG-16", "2026-10-08"),
                      ("ENG-16", "2026-10-07"), ("ENG-16", "2026-10-06"),
                      ("ENG-18", "2026-11-03"), ("ENG-18", "2026-10-04"),
                      ("ENG-18", "2026-10-05"), ("ENG-18", "2026-10-06")]))
        check("…three crashes leave no base, so the run costs its whole total; a crash among "
              "them is skipped for the priced log before it; and none is counted as a run",
              (dict((r["workspace"], r["cost"]) for r in scanned_c["runs"]),
               len(scanned_c["runs"]), len(scanned_c["baselines"])),
              ({"ENG-16": 9.0, "ENG-18": 6.0}, 2, 6))


        # -- hostile logs, read ---------------------------------------------------------------
        read_x = scan(hostile_world(os.path.join(tmp, "hostile")), lambda: T)
        h = read_x["runs"][0]
        try:
            json.dumps([h["total"], h["usage"], h["events"], h["weekly"], h["event_times"]],
                       allow_nan=False)
            finite_only = True
        except ValueError:
            finite_only = False
        check("hostile logs, read: a sign-in source that is not a word is other, the last good "
              "result stands over the junk before it, models are plain names or other, an event "
              "that names no window or word is other with no number kept, nothing is NaN or "
              "infinite, and the logs still read whole",
              (h["key_source"], h["total"], len(h["usage"]),
               all(k == "other" or MODEL_RE.match(k) for k in h["usage"]), h["usage"]["other"][0],
               h["events"]["other"], h["events"]["five_hour"], h["weekly"]["resets_at"],
               finite_only, read_x["sources"]["logs"].record()["state"]),
              ("other", 5.0, 72, True, 3.0,
               {"at": U("2026-11-03T08:00:05Z"), "window": "other", "status": "other",
                "utilization": None, "resets_at": None, "surpassed": None},
               {"at": U("2026-11-03T08:00:30Z"), "window": "five_hour", "status": "other",
                "utilization": None, "resets_at": None, "surpassed": None},
               U("2026-11-06T09:00:00Z"), True, READ))
        with Watch() as watch:
            scan(cfg, lambda: T)
        check("a read writes nothing and opens no socket", (watch.wrote, watch.net), ([], []))

    # -- the small readers, one at a time ------------------------------------------------------
    check("a log's time: a zone is required, a fraction and an offset are honoured, and a time "
          "before 2020, after 2099 or impossible is refused",
          [epoch(x) for x in ("2026-11-03T08:00:00.500Z", "2026-11-03T10:00:00+02:00",
                              "2026-11-03T08:00:00", "2019-12-31T23:59:59Z",
                              "2100-01-01T00:00:00Z", "2026-13-03T08:00:00Z",
                              "2026-11-03T08:00:00Z\n", None, 5)],
          [U("2026-11-03T08:00:00Z") + 0.5, U("2026-11-03T08:00:00Z"), None, None, None, None,
           None, None, None])
    check("a run file's start is the stamp in its name",
          file_start("2026-11-02T10-15-30-123Z"), U("2026-11-02T10:15:30Z") + 0.123)
    check("a number from a log is not a boolean, NaN, infinity, negative or past its bound",
          [finite(x, 0.0, 10.0) for x in (True, float("nan"), float("inf"), -1, 11, 5, "5",
                                          None, 2.5)],
          [None, None, None, None, None, 5.0, None, None, 2.5])
    check("spend and ratios are rounded and bounded",
          (money(0.1234567891), money(-1), money(1e9), ratio(0.123456), ratio(5000)),
          (0.123457, None, None, 0.1235, None))
    check("our own lines lose markup and hidden characters, keep to one line, and are capped",
          (line("a\nb <c> & d | e `f` " + chr(0xAD) + chr(0x202E) + chr(0x200D) + "g"),
           len(line("x" * 400)), line("x" * 400).endswith("…")), ("a b c d e f g", 300, True))
    check("a sign-in source is the SDK's word, anything else is other, and none given is none",
          [key_word(x) for x in ("ANTHROPIC_API_KEY", "oauth", "/login managed key", LEAK, "",
                                 None, 5, ["x"])],
          ["ANTHROPIC_API_KEY", "oauth", "/login managed key", "other", None, None, "other",
           "other"])
    check("a usage window and a status are from closed lists, anything else is other",
          ([window_word(x) for x in ("five_hour", LEAK, "", None)],
           [status_word(x) for x in ("allowed", "rejected", LEAK, None)]),
          (["five_hour", "other", None, None], ["allowed", "rejected", "other", "other"]))
    check("a result's models: plain names kept, other names merged as other, junk passed over",
          usage_figures({OPUS: {"costUSD": 1.5, "inputTokens": 3, "outputTokens": 4,
                                "cacheReadInputTokens": 5, "cacheCreationInputTokens": 6},
                         "Bad Name": {"costUSD": 0.5, "outputTokens": 1},
                         LEAK: {"costUSD": 0.25}, "junk": 7,
                         "claude-haiku": {"costUSD": "lots"}}),
          {OPUS: [1.5, 3.0, 4.0, 5.0, 6.0], "other": [0.75, 0.0, 1.0, 0.0, 0.0],
           "claude-haiku": [0.0, 0.0, 0.0, 0.0, 0.0]})

    def said(content):
        return {"message": {"content": content}}
    check("the kind of an opening prompt, as a string or as text blocks: conflict marks before "
          "bounce marks; an unmarked prompt, none and an empty one",
          [prompt_kind(said(x)) for x in (CONFLICT, [{"type": "text", "text": BOUNCE % (1, 1, 1)}],
                                          CATCHUP, "plain words",
                                          [{"type": "tool_result", "content": "x"}], None, "")],
          ["conflict_fix", "bounce_fix", "catch_up", "unmarked", None, None, None])

    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        path = os.path.join(tmp, "lines.jsonl")
        with open(path, "wb") as fh:
            fh.write(b"first\n" + b"x" * (LINE_MAX + 100) + b"\nsecond\n" + b"y" * 5)
        check("a line longer than the bound is passed over without being held, the lines "
              "around it are kept, and a last line with no break is kept",
              list(log_lines(path)), [b"first", b"second", b"yyyyy"])
        link, fifo = os.path.join(tmp, "link"), os.path.join(tmp, "fifo")
        os.symlink(path, link)
        os.mkfifo(fifo)
        opened = []
        for target in (link, fifo, tmp):
            try:
                os.close(open_regular(target))
                opened.append("opened")
            except OSError:
                opened.append("refused")
        check("only a regular file is opened: a link, a pipe and a folder are refused, never "
              "followed or waited on", opened, ["refused", "refused", "refused"])
        broken = []
        for data in (b"\xff\xfe", b"[" * 200000, b""):
            with open(os.path.join(tmp, "state.json"), "wb") as fh:
                fh.write(data)
            try:
                read_json_file(os.path.join(tmp, "state.json"))
                broken.append("read")
            except ValueError:
                broken.append("ValueError")
        check("a state file that is not UTF-8, is nested too deeply or is empty is a "
              "ValueError, never a crash", broken, ["ValueError"] * 3)

        # -- the ledger's window, and a review's key ----------------------------------------
        rows = [(1000.0, "ENG-1", "bounce_fix")]

        def kind_of(start, ws="ENG-1", prompt=None, planners=frozenset(), reprompts=rows,
                    firsts=None):
            r = {"workspace": ws, "start": start, "session": "s", "prompt": prompt}
            classify([r], {"reviews_team_key": "REV"}, planners, reprompts,
                     firsts if firsts is not None else {ws: (0.0, "s0")})
            return r["kind"], r["how"]
        check("a ledger row names the run that starts from 60 seconds before it to 30 minutes "
              "after it, and no other", [kind_of(s)[0] for s in (939.0, 940.0, 2800.0, 2801.0)],
              ["other_later", "bounce_fix", "bounce_fix", "other_later"])
        check("…and only for its own ticket", kind_of(1000.0, ws="ENG-2")[0], "other_later")
        check("a review is the reviews key, a dash and a number: REVX-1 and REV are not",
              [kind_of(5.0, ws=w, reprompts=[])[0] for w in ("REV-9", "REVX-1", "REV")],
              ["review", "other_later", "other_later"])
        check("a planning run is told by the idea gate's record, and with no record it is not "
              "guessed", [kind_of(5.0, ws="ENG-3", planners={"ENG-3"}, reprompts=[])[0],
                          kind_of(5.0, ws="ENG-3", planners=None, reprompts=[])[0]],
              ["planner", "other_later"])
        check("the first run of a folder is coding, by the earliest file name",
              kind_of(5.0, ws="ENG-4", reprompts=[], firsts={"ENG-4": (5.0, "s")}),
              ("coding", "first run"))

        # -- the runs the plan's limit cut off -----------------------------------------------
        check("a run is limit-stopped when a window rejected it and it then ended in an error or "
              "never ended, with the window's word and reset; a rejection then a success, and a "
              "warning, are not; a reset that is no time is null; a window the list lacks is "
              "other", limit_facts(os.path.join(tmp, "limit")), LIMIT_EXPECT)
        check("a limit stop keeps no text from the log",
              LEAK in json.dumps(limit_facts(os.path.join(tmp, "limit-leak"))), False)

        # -- the producers' words, which this job holds copies of, are still theirs ---------
        def source_of(name):
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                return fh.read()
        bounce, catchup = (source_of("pipeline_bounce_local.py"),
                           source_of("pipeline_catchup_poller.py"))
        review, idea_gate = (source_of("pipeline_review_poller.py"),
                             source_of("pipeline_plan_poller.py"))
        chat = source_of("pipeline_chat_lane_setup.py")
        check("the re-prompts' fixed openings are still what their producers write",
              [m for kind, marks in PROMPT_MARKS for m in marks
               if m not in (catchup if kind == "catch_up" else bounce)], [])
        check("the ledger's words and file name, the review record's ticket and the idea "
              "gate's record are still theirs",
              [needle for text, needle in (
                  (bounce, 'outcome="delivered"'), (bounce, 'outcome="conflict-delivered"'),
                  (bounce, '"bounce-ledger.jsonl"'), (review, '"ticket_id"'),
                  (review, "__pr-%d.json"), (idea_gate, '"seen.json"'),
                  (idea_gate, '"run_ticket"'), (idea_gate, '"triggers"'))
               if needle not in text], [])
        check("the ledger rows this job reads are the words it holds", sorted(LEDGER_KINDS),
              ["conflict-delivered", "delivered"])
        head = "^session-(.+)-([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}"
        tail = "-[0-9]{3}Z)\\.jsonl\\Z"
        check("a run file's name is the one the chat lane's own reader parses",
              (head in chat, tail in chat, head in RUN_FILE.pattern, tail in RUN_FILE.pattern),
              (True, True, True, True))

        # -- the reader opens no socket, starts no process and writes nothing -----------------
        source = inspect.getsource(sys.modules[__name__]).split("\ndef selftest():")[0]
        check("the reader opens no socket, starts no process, reads no environment variable and "
              "writes nothing: none of those is in its source",
              ([w for w in ("import socket", "urllib", "http.client", "subprocess", "shutil",
                            "os.system", "os.popen", "os.environ", "getenv", "os.fork",
                            "os.exec", "os.spawn") if w in source],
               [w for w in ("os.replace(", "os.unlink(", "os.remove(", "os.rename(",
                            "os.makedirs(", "os.mkdir(", "rmdir(", "copyfile", "os.link(",
                            "os.symlink(", "os.truncate(", "os.chmod(", "os.utime(", "O_WRONLY",
                            "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC") if w in source],
               re.findall(r"open\([^)]*[\"'][wax+]", source)), ([], [], []))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = main([])
        check("no command is a usage error: the reader has none but --selftest", code, EXIT_USAGE)

    # -- mutants: a check that cannot fail checks nothing -------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        original = dict((n, globals()[n]) for n in ("parse_run", "price", "PROMPT_MARKS"))
        status_word_original = status_word

        def reply_usage_parse(path):
            """parse_run, with a run's usage summed from its replies' own usage lines."""
            run = original["parse_run"](path)
            sums, model = [0.0, 0.0, 0.0, 0.0, 0.0], None
            for raw in log_lines(path):
                try:
                    entry = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                m = entry.get("message") if isinstance(entry, dict) else None
                if isinstance(m, dict) and m.get("type") == "assistant":
                    inner = m.get("message") or {}
                    use = inner.get("usage") or {}
                    model = inner.get("model") or model
                    for slot, field in ((1, "input_tokens"), (2, "output_tokens"),
                                        (3, "cache_read_input_tokens"),
                                        (4, "cache_creation_input_tokens")):
                        sums[slot] += use.get(field, 0)
            if model and run["total"] is not None:
                sums[0] = run["total"]
                run["usage"] = {model: sums}
            return run

        def whole_total_price(runs, baselines):
            original["price"](runs, baselines)
            for r in runs:
                r["cost"] = r["total"]

        def crash_is_zero_parse(path):
            run = original["parse_run"](path)
            if run["zeroed"]:
                run["total"], run["zeroed"] = 0.0, False
            return run

        def caught(name, **patched):
            saved = dict((k, globals()[k]) for k in patched)
            globals().update(patched)
            try:
                return facts(os.path.join(tmp, name)) != EXPECT
            finally:
                globals().update(saved)
        check("mutant: a run's tokens summed from its replies' own usage lines is caught",
              caught("m1", parse_run=reply_usage_parse), True)
        check("mutant: a run priced at its whole running total, with no base, is caught",
              caught("m2", price=whole_total_price), True)
        check("mutant: a crash counted as zero is caught",
              caught("m3", parse_run=crash_is_zero_parse), True)
        check("mutant: the bounce marks looked for before the conflict marks is caught",
              caught("m4", PROMPT_MARKS=tuple(sorted(original["PROMPT_MARKS"],
                                                     key=lambda kv: kv[0] != "bounce_fix"))),
              True)
        def any_rejection_parse(path):
            """parse_run, marking a run on any rejection, whatever the run did next."""
            run = original["parse_run"](path)
            hit = [e for e in run["events"].values() if e["status"] == "rejected"]
            if hit and run["limit_stop"] is None:
                run["limit_stop"] = {"window": hit[0]["window"] or WORD_OTHER,
                                     "resets_at": hit[0]["resets_at"]}
            return run

        def needs_a_result_parse(path):
            """parse_run, marking a run only when a result follows: one that never ended is
            left unmarked."""
            run = original["parse_run"](path)
            if run["total"] is None and not run["zeroed"]:
                run["limit_stop"] = None
            return run

        def reset_guessed_parse(path):
            """parse_run, with a reset that is no time taken as the run's last line."""
            run = original["parse_run"](path)
            if run["limit_stop"] and run["limit_stop"]["resets_at"] is None:
                run["limit_stop"] = dict(run["limit_stop"], resets_at=run["last_at"])
            return run

        def limit_caught(name, **patched):
            saved = dict((k, globals()[k]) for k in patched)
            globals().update(patched)
            try:
                return limit_facts(os.path.join(tmp, name)) != LIMIT_EXPECT
            finally:
                globals().update(saved)
        check("mutant: a run marked on any rejection, a success after it or not, is caught",
              limit_caught("m5", parse_run=any_rejection_parse), True)
        check("mutant: a warning counted as a rejection is caught",
              limit_caught("m6", status_word=lambda v: "rejected" if v in (
                  "rejected", "allowed_warning") else status_word_original(v)), True)
        check("mutant: a run that never ended left unmarked is caught",
              limit_caught("m7", parse_run=needs_a_result_parse), True)
        check("mutant: a reset that is no time guessed rather than null is caught",
              limit_caught("m8", parse_run=reset_guessed_parse), True)
        check("…and with every mutant undone the job is as it was",
              facts(os.path.join(tmp, "clean")) == EXPECT, True)


if __name__ == "__main__":
    sys.exit(main())
