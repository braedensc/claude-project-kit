#!/usr/bin/env python3
"""The usage job: one file that says what the pipeline spent, read from the dispatcher's own
logs (KIT-422, contract §17).

    python3 scripts/pipeline_usage_local.py run --config <usage.json> [--dry-run] [--print]
    python3 scripts/pipeline_usage_local.py --example-config
    python3 scripts/pipeline_usage_local.py --selftest

WHY.  The pipeline cannot pace itself, or stop a runaway, until something measures what it
spends. The dispatcher already logs every session's own cost estimate and Anthropic's usage
events, and a hand count of one week's logs (3 Nov) priced the week and split it by kind of
work. This job is that count, made repeatable. It is the reader and nothing else: a later
queue reads the file to decide when new work may start, and this job decides nothing.

WHAT ONE PASS DOES.  It runs with no AI, no network and no credential, as the role account,
like the status job. It reads files, and it writes one.
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
  3. One document in the shape of contract §17, checked against
     schemas/pipeline-usage.schema.json, written atomically and world-readable (0644).

FIELDS ONLY.  Numbers, ticket ids, model names that match a strict pattern, words from closed
lists, and times to the second in UTC. No prompt, reply, comment or title reaches the file or
the output, and no error line quotes an exception's own words: a log's text can ride in one.
Every value taken from a log passes a sanitizer first, so no log can make the document fail
its schema and stop the file.

WHEN A SOURCE FAILS.  That source is written as `could-not-read` (or `partly-read`) with the
reason, and the file is still written. A source that is not read is unknown, never zero: a
reader says "I couldn't check". The logs are the one source the figures cannot do without.

EXIT CODES (contract §17, by §13's rule).
  0  every source was read, and the file was written
  3  the file was written, and at least one source was not read whole: the file says which
  1  the logs could not be read, no source could be, the pass crashed, or the file could not
     be written or did not conform (a document that does not conform is never written)
  2  usage or config: nothing was read, and nothing was written

WHAT IT NEVER DOES (asserted in --selftest).  It opens no socket, starts no process, reads no
credential and no environment variable of its own (the local day follows the machine's time
zone), and writes no file but its own: every path it writes is under the folder of the `out`
file.
"""
import argparse
import calendar
import datetime
import json
import math
import os
import pwd
import re
import stat
import sys
import time
import traceback
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_schemas  # noqa: E402  (the one definition of "conforms", contract §12)

USAGE_SCHEMA = "pipeline-usage/1"
SCHEMA_NAME = "pipeline-usage"

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3
OK, PARTIAL, FAILED = "ok", "partial", "failed"
READ, PARTLY, COULD_NOT, OFF = "read", "partly-read", "could-not-read", "off"
SOURCES = ("logs", "ledger", "planner")

DEFAULT_DISPATCHER_HOME = "~/.cyrus"
DEFAULT_STAGE_E_STATE = "~/.stage-e/state"
DEFAULT_STAGE_A_STATE = "~/.stage-a/state"
DEFAULT_OUT = "~/.stage-e/usage/usage.json"
DEFAULT_LOOKBACK_DAYS = 8
MAX_LOOKBACK_DAYS = 31

# The kinds of work a run can be, in the order the file lists them. The words are the file's;
# LABELS are what the summary says.
KINDS = ("tech_lead", "review", "planner", "coding", "bounce_fix", "conflict_fix", "catch_up",
         "other_later")
LABELS = {"tech_lead": "tech lead", "review": "review", "planner": "planner", "coding": "coding",
          "bounce_fix": "bounce fix", "conflict_fix": "conflict fix", "catch_up": "catch-up",
          "other_later": "other later"}
CHAT_PREFIX = "slack-"

# The SDK's own closed list of sign-in sources, as it names them (init's `apiKeySource`).
# Anything else is "other"; no init line is "not seen". The first three name a key.
KEY_SOURCES = ("ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key", "none", "user",
               "project", "org", "temporary", "oauth")
KEY_OTHER, KEY_UNSEEN = "other", "not seen"
# The plan's own sign-in: no key at all ("none", what a setup token reports) or the
# claude.ai login ("oauth"). Every other word is listed, an unknown one included: the
# plan's windows cannot be confirmed for it, and a key is billed by the token.
PLAN_SOURCES = ("none", "oauth")
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

# Bounds, so no log can stall the pass or leave the file malformed. A line longer than
# LINE_MAX is passed over unread; a state file is read to STATE_MAX and no further.
LINE_MAX = 8 << 20
STATE_MAX = 64 << 20
REASON_CAP = 300
PIECES_CAP = 1000
API_KEY_RUNS_CAP = 500
MODELS_CAP = 64
# A day's edges move an hour twice a year: a week that starts this close to the window's
# first midnight is covered.
DST_SLACK = 7200

# Python's `$` also matches before a final line break, so every whole-value pattern ends
# with `\Z`: an id with a line break on its end is refused, not kept. The bounds keep every
# id and name inside what schemas/pipeline-usage.schema.json allows, so no folder name or
# log value can make the document fail it.
RUN_FILE = re.compile(r"^session-(.+)-([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}"
                      r"-[0-9]{3}Z)\.jsonl\Z")
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
TEAM_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}\Z")
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._\[\]-]{0,63}\Z")
TIME_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
                     r"(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:?[0-9]{2})\Z")
# Times a log can carry: before 2020 or after 2099 is not a time this job keeps.
EPOCH_LOW, EPOCH_HIGH = 1577836800.0, 4102444800.0
_UNSAFE_RE = re.compile(r"[<>&|`]")

CONFIG_KEYS = {
    "dispatcher_home": "the dispatcher's folder, whose logs/ holds every session's log; "
                       "absolute or ~/ (default %s)" % DEFAULT_DISPATCHER_HOME,
    "stage_e_state_dir": "Stage E's state_dir, whose bounce-ledger.jsonl and outcomes/ say "
                         "which later run was a bounce fix or a conflict fix (default %s); "
                         "empty turns this source off" % DEFAULT_STAGE_E_STATE,
    "stage_a_state_dir": "the idea gate's state_dir, whose seen.json names each planning "
                         "run's ticket (default %s); empty turns this source off"
                         % DEFAULT_STAGE_A_STATE,
    "reviews_team_key": "the team KEY of the reviews team, e.g. \"REV\": a run in a folder "
                        "that starts with KEY- is a review (required)",
    "out": "where the file is written; absolute or ~/, outside every git working tree "
           "(default %s)" % DEFAULT_OUT,
    "lookback_days": "how many local days the file covers, 1-%d (default %d); the plan's "
                     "week is only reported when this reaches back to its start"
                     % (MAX_LOOKBACK_DAYS, DEFAULT_LOOKBACK_DAYS),
}

EXAMPLE_CONFIG = {
    "dispatcher_home": DEFAULT_DISPATCHER_HOME,
    "stage_e_state_dir": DEFAULT_STAGE_E_STATE,
    "stage_a_state_dir": DEFAULT_STAGE_A_STATE,
    "reviews_team_key": "REV",
    "out": DEFAULT_OUT,
    "lookback_days": DEFAULT_LOOKBACK_DAYS,
}


class ConfigError(Exception):
    """Bad arguments or config (exit 2): nothing was read, and nothing is written."""


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
    It is built from this job's words, counts and class names, never from a log's."""
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


def local_date(when):
    return datetime.date.fromtimestamp(when).isoformat()


def local_midnight(day):
    return time.mktime((day.year, day.month, day.day, 0, 0, 0, 0, 0, -1))


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def account_home():
    """This account's home, from the account database, never $HOME: a shell whose $HOME was
    moved must not move where the dispatcher's logs or the job's own file are looked for
    (the way scripts/pipeline_dispatch_local.py resolves a `~` path)."""
    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        home = ""
    if not home or not os.path.isabs(home):
        raise ConfigError("this account has no home in the account database, so a path that "
                          "starts with ~/ cannot be resolved: use an absolute path")
    return home


def resolve_path(raw, key, off_when_empty=False):
    """An absolute path from the config: `~/` is this account's home. Never a relative path,
    and never `~name`: both would depend on where the job happened to be started."""
    if not isinstance(raw, str):
        raise ConfigError("config %r must be a path" % key)
    if raw == "" and off_when_empty:
        return ""
    path = os.path.join(account_home(), raw[2:]) if raw == "~" or raw.startswith("~/") else raw
    if not os.path.isabs(path):
        raise ConfigError("config %r must be an absolute path (or start with ~/)%s"
                          % (key, ", or empty to turn it off" if off_when_empty else ""))
    return os.path.realpath(path)


def inside_git_worktree(path):
    """True when `path`, or a folder above it, holds `.git`: a session can write a worktree,
    and a usage file written there could be one a session wrote."""
    cur = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError("could not read --config %s: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ConfigError("--config must hold one JSON object")
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        raise ConfigError("unknown config key(s): %s (see --example-config)" % ", ".join(unknown))
    team = raw.get("reviews_team_key")
    if not isinstance(team, str) or not TEAM_RE.match(team):
        raise ConfigError("config 'reviews_team_key' is required: the reviews team's KEY, "
                          "e.g. \"REV\"")
    days = raw.get("lookback_days", DEFAULT_LOOKBACK_DAYS)
    if (not isinstance(days, int) or isinstance(days, bool)
            or not 1 <= days <= MAX_LOOKBACK_DAYS):
        raise ConfigError("config 'lookback_days' must be an integer from 1 to %d"
                          % MAX_LOOKBACK_DAYS)
    out = raw.get("out", DEFAULT_OUT)
    if not isinstance(out, str):
        raise ConfigError("config 'out' must be a path")
    out_dir = resolve_path(os.path.dirname(out) if os.path.basename(out) else out, "out")
    out = os.path.join(out_dir, os.path.basename(out))
    if not os.path.basename(out) or os.path.isdir(out):
        raise ConfigError("config 'out' must name a file, not a folder")
    if inside_git_worktree(out_dir):
        raise ConfigError("config 'out' (%s) must be a file outside every git working tree: a "
                          "session can write a worktree" % out)
    return {
        "dispatcher_home": resolve_path(raw.get("dispatcher_home", DEFAULT_DISPATCHER_HOME),
                                        "dispatcher_home"),
        "stage_e_state_dir": resolve_path(raw.get("stage_e_state_dir", DEFAULT_STAGE_E_STATE),
                                          "stage_e_state_dir", off_when_empty=True),
        "stage_a_state_dir": resolve_path(raw.get("stage_a_state_dir", DEFAULT_STAGE_A_STATE),
                                          "stage_a_state_dir", off_when_empty=True),
        "reviews_team_key": team,
        "out": out,
        "lookback_days": days,
    }


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
    A question the source could not answer is a problem, in this job's own words."""

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
    log chose reaches the file as a key."""
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
           "weekly": None, "event_times": []}
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
            if window and (window not in run["events"] or at >= run["events"][window]["at"]):
                run["events"][window] = event
            # The week is read from the newest weekly event that names a reset, which is not
            # always the newest weekly event.
            if window == "seven_day" and event["resets_at"] is not None and (
                    run["weekly"] is None or at >= run["weekly"]["at"]):
                run["weekly"] = event
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


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #
def blank_totals():
    return {"usd": 0.0, "runs": 0, "unpriced": 0, "seconds": 0,
            "by_kind": dict((k, {"runs": 0, "usd": 0.0}) for k in KINDS)}


def add_run(totals, r):
    totals["runs"] += 1
    totals["seconds"] += r["seconds"]
    kind = totals["by_kind"][r["kind"]]
    kind["runs"] += 1
    if r["cost"] is None:
        totals["unpriced"] += 1
    else:
        totals["usd"] += r["cost"]
        kind["usd"] += r["cost"]


def finish_totals(totals):
    totals["usd"] = round(totals["usd"], 6)
    for kind in totals["by_kind"].values():
        kind["usd"] = round(kind["usd"], 6)
    return totals


def unknown_week():
    return {"known": False, "starts_at": None, "resets_at": None, "usd": None, "runs": None,
            "unpriced": None, "seconds": None, "by_kind": None, "by_model": None}


def empty_doc(started, now, sources, result, error):
    return {"schema": USAGE_SCHEMA, "written_at": _iso(now()), "started_at": _iso(started),
            "result": result, "error": error, "sources": sources, "week": unknown_week(),
            "days": [], "pieces": [], "status": {"windows": {}, "events_per_day": None},
            "sign_in": {"counts": {}, "api_key_runs": []}, "never_reached_model": None}


def judge(sources):
    """ok, partial or failed, from the sources' states. The logs are the figures: without
    them the file says nothing, however the other sources read."""
    states = [s["state"] for s in sources.values() if s["state"] != OFF]
    if sources["logs"]["state"] == COULD_NOT or (states and all(s == COULD_NOT for s in states)):
        return FAILED
    return OK if all(s == READ for s in states) else PARTIAL


def cap_models(by_model):
    """At most MODELS_CAP models, as the schema allows: the costliest keep their names and
    the rest are folded into `other`, so the spend still adds up. A log that names more
    models than that is not a log the file may refuse to be written for."""
    if len(by_model) <= MODELS_CAP:
        return by_model
    rest = dict(by_model)
    folded = rest.pop(WORD_OTHER, 0.0)
    ranked = sorted(rest.items(), key=lambda kv: (-kv[1], kv[0]))
    kept = dict(ranked[:MODELS_CAP - 1])
    kept[WORD_OTHER] = round(folded + sum(v for _name, v in ranked[MODELS_CAP - 1:]), 6)
    return kept


def workspace_kind(ws, cfg, planners):
    """A piece's kind is its workspace's: review, planner, or coding for any other ticket."""
    if ws.startswith(cfg["reviews_team_key"] + "-"):
        return "review"
    return "planner" if planners and ws in planners else "coding"


def summarize(cfg, scanned, now, started):
    """The document one pass would write. A source that failed is said in it; nothing here
    raises for a source that is down."""
    src = scanned["sources"]
    runs = scanned["runs"]
    if runs is None:
        sources = dict((name, src[name].record()) for name in SOURCES)
        return empty_doc(started, now, sources, judge(sources), None)
    now_at = now()
    today = datetime.date.fromtimestamp(now_at)
    dates = [today - datetime.timedelta(days=i) for i in range(cfg["lookback_days"] - 1, -1, -1)]
    names = [d.isoformat() for d in dates]
    listed_days = set(names)
    window_start = local_midnight(dates[0])
    for r in runs:
        r["date"] = local_date(r["last"])
    inside = [r for r in runs if r["date"] in listed_days]
    events = [e for r in runs for e in r["events"].values()]
    times = [t for r in runs for t in r["event_times"] if local_date(t) in listed_days]
    # The plan's week starts at the newest weekly event's reset, less seven days. A reset that
    # has passed names a week that is over, and a lookback that stops short of its start
    # cannot total it: either way the week is unknown, never a guess.
    weekly = sorted((r["weekly"] for r in runs if r["weekly"]), key=lambda e: e["at"])
    week, week_runs, why = unknown_week(), [], None
    if weekly:
        resets = weekly[-1]["resets_at"]
        begins = resets - 7 * 86400
        # Else that week is over, or has not begun: unknown, and nothing to say about it.
        current = begins <= now_at < resets
        if current and begins < window_start - DST_SLACK:
            why = ("the %d-day lookback does not reach the start of the plan's week: raise "
                   "lookback_days to at least 8" % cfg["lookback_days"])
        elif current:
            week_runs = [r for r in inside if r["last"] >= begins]
            totals = blank_totals()
            by_model = {}
            for r in week_runs:
                add_run(totals, r)
                for name, usd in r["models"].items():
                    by_model[name] = round(by_model.get(name, 0.0) + usd, 6)
            week = dict(finish_totals(totals), known=True, starts_at=_iso(begins),
                        resets_at=_iso(resets), by_model=cap_models(by_model))
    days = []
    for name in names:
        totals = blank_totals()
        for r in inside:
            if r["date"] == name:
                add_run(totals, r)
        days.append(dict(finish_totals(totals), date=name))
    pieces = {}
    for r in week_runs:
        if TICKET_RE.match(r["workspace"]):
            p = pieces.setdefault(r["workspace"], {"id": r["workspace"], "usd": 0.0, "runs": 0,
                                                   "last": 0.0})
            p["runs"] += 1
            p["usd"] += r["cost"] or 0.0
            p["last"] = max(p["last"], r["last"])
    listed = sorted(pieces.values(), key=lambda p: (-p["usd"], -p["last"], p["id"]))[:PIECES_CAP]
    pieces = [{"id": p["id"], "kind": workspace_kind(p["id"], cfg, scanned["planners"]),
               "usd": round(p["usd"], 6), "runs": p["runs"], "last_at": _iso(p["last"])}
              for p in listed]
    windows = {}
    for e in sorted(events, key=lambda e: e["at"]):
        if e["window"]:
            windows[e["window"]] = {
                "status": e["status"], "utilization": e["utilization"],
                "surpassed": e["surpassed"],
                "resets_at": _iso(e["resets_at"]) if e["resets_at"] is not None else None,
                "seen_at": _iso(e["at"])}
    counts, keyed = {}, []
    for r in inside:
        word = r["key_source"] or KEY_UNSEEN
        counts[word] = counts.get(word, 0) + 1
        if word not in PLAN_SOURCES + (KEY_UNSEEN,) and TICKET_RE.match(r["workspace"]):
            keyed.append({"id": r["workspace"], "at": _iso(r["start"])})
    keyed.sort(key=lambda k: (k["at"], k["id"]), reverse=True)
    real = set(r["started"] for r in runs + scanned["baselines"] if r["started"])
    never = len([p for p in scanned["pendings"] if local_date(p["start"]) in listed_days
                 and (p["started"] is None or p["started"] not in real)])
    if why:
        src["logs"].fail(why)
    sources = dict((name, src[name].record()) for name in SOURCES)
    return {"schema": USAGE_SCHEMA, "written_at": _iso(now()), "started_at": _iso(started),
            "result": judge(sources), "error": None, "sources": sources, "week": week,
            "days": days, "pieces": pieces,
            "status": {"windows": windows,
                       "events_per_day": round(len(times) / float(cfg["lookback_days"]), 1)},
            "sign_in": {"counts": counts, "api_key_runs": keyed[:API_KEY_RUNS_CAP]},
            "never_reached_model": never}


def build(cfg, now, started):
    return summarize(cfg, scan(cfg, now), now, started)


def crashed(cfg, started, now, exc):
    """The document a pass that could not finish writes: every source that is on not known
    to be read, and where it stopped. The reason is the exception's class and the line it
    came from, never its message: a log's text can ride in one."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = "%s (line %d)" % (frames[-1][2], frames[-1][1]) if frames else "an unknown place"
    why = line("the pass stopped on an unexpected error: %s in %s" % (type(exc).__name__, where))
    sources = {}
    for name in SOURCES:
        off = (name == "ledger" and not cfg["stage_e_state_dir"]) or (
            name == "planner" and not cfg["stage_a_state_dir"])
        sources[name] = ({"state": OFF, "asked": 0, "answered": 0, "reason": None,
                          "read_at": None} if off else
                         {"state": COULD_NOT, "asked": 0, "answered": 0, "reason": why,
                          "read_at": None})
    return empty_doc(started, now, sources, FAILED, why)


def atomic_write(path, doc):
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder, mode=0o755)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def dollars(value):
    return "$%.2f" % value


def summary_lines(doc, dry_run):
    out = ["usage job: %s%s" % (doc["written_at"], " (dry run)" if dry_run else "")]
    for name in SOURCES:
        src = doc["sources"][name]
        out.append("  %-8s %-15s asked %d, answered %d%s"
                   % (name, src["state"], src["asked"], src["answered"],
                      (": " + src["reason"]) if src["reason"] else ""))
    week = doc["week"]
    if week["known"]:
        out.append("  week     %s over %d runs (%d unpriced), %.1f hours, %s to %s"
                   % (dollars(week["usd"]), week["runs"], week["unpriced"],
                      week["seconds"] / 3600.0, week["starts_at"], week["resets_at"]))
        out.append("  by kind  " + ", ".join(
            "%s %s" % (LABELS[k], dollars(week["by_kind"][k]["usd"])) for k in KINDS))
    elif doc["sources"]["logs"]["state"] != COULD_NOT:
        out.append("  week     unknown: no usable weekly event in the logs, or the lookback "
                   "does not reach it")
    if doc["days"]:
        today = doc["days"][-1]
        out.append("  today    %s over %d runs (%d unpriced), %.1f hours"
                   % (dollars(today["usd"]), today["runs"], today["unpriced"],
                      today["seconds"] / 3600.0))
        out.append("  sign-in  %s; %d run(s) listed as signed in with a key" % (
            ", ".join("%s %d" % kv for kv in sorted(doc["sign_in"]["counts"].items())) or "none",
            len(doc["sign_in"]["api_key_runs"])))
    if doc["never_reached_model"] is not None:
        out.append("  never reached the model: %d" % doc["never_reached_model"])
    if doc["error"]:
        out.append("  error: %s" % doc["error"])
    out.append("  result: %s" % doc["result"])
    return out


def refusal_lines(problems):
    """A document that does not conform, said by where and by which rule. Never the value:
    a message about a value quotes it."""
    out = []
    for p in problems[:10]:
        where, _, rest = p.partition(" ")
        rule = re.search(r"\[([A-Za-z]+)\]\Z", rest)
        out.append("    %s [%s]" % (line(where, 120), rule.group(1) if rule else "?"))
    return out


def run(cfg, dry_run=False, show=False, now=None, say=print):
    now = now or time.time
    started = now()
    try:
        doc = build(cfg, now, started)
    except Exception as exc:  # noqa: BLE001 — a bug must still leave a file that says so
        doc = crashed(cfg, started, now, exc)
    for text in summary_lines(doc, dry_run):
        say(text)
    problems = check_schemas.document_problems(doc, SCHEMA_NAME)
    if problems:
        say("  REFUSED: the document does not conform to schemas/pipeline-usage.schema.json, "
            "so nothing was written:")
        for text in refusal_lines(problems):
            say(text)
        return EXIT_ERROR
    if show:
        say(json.dumps(doc, indent=1, sort_keys=True))
    if dry_run:
        say("  dry run: would write %s" % cfg["out"])
    else:
        try:
            atomic_write(cfg["out"], doc)
        except (OSError, ValueError) as exc:
            say("  COULD NOT WRITE %s: %s" % (cfg["out"], type(exc).__name__))
            return EXIT_ERROR
        say("  written to %s" % cfg["out"])
    return {OK: EXIT_OK, PARTIAL: EXIT_PARTIAL}.get(doc["result"], EXIT_ERROR)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_usage_local.py",
                                description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--example-config", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--dry-run", action="store_true",
                   help="read everything and check the document; write nothing")
    r.add_argument("--print", action="store_true", dest="show", help="also print the document")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2))
        return EXIT_OK
    if args.cmd != "run":
        p.print_help()
        return EXIT_USAGE
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print("CONFIG: %s" % exc)
        return EXIT_USAGE
    return run(cfg, dry_run=args.dry_run, show=args.show)


# --------------------------------------------------------------------------- #
# --selftest — offline, in a temp folder only; the clock and the zone stood in for
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

    # The local day follows the machine's zone, so the selftest fixes one with no zone
    # database behind it: UTC+3 (POSIX writes the sign the other way round).
    saved_zone = os.environ.get("TZ")
    os.environ["TZ"] = "TST-3"
    time.tzset()
    try:
        _cases(check)
    finally:
        if saved_zone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved_zone
        time.tzset()
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check):
    import builtins
    import contextlib
    import inspect
    import io
    import socket
    import tempfile

    T = calendar.timegm((2026, 11, 3, 12, 0, 0))          # 2026-11-03T12:00:00Z, 15:00 local
    LEAK = "SENTINEL-LEAK-CHECK-0f3a"                      # text no field may carry
    OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
    MISSING = "<missing>"

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

    def go(cfg, **kw):
        said = []
        code = run(cfg, now=lambda: T, say=said.append, **kw)
        return code, said

    def written(cfg):
        try:
            with open(cfg["out"], encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def text_of(cfg):
        try:
            with open(cfg["out"], encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def dig(doc, *path):
        for key in path:
            try:
                doc = doc[key]
            except (KeyError, IndexError, TypeError):
                return MISSING
        return doc

    def kinds(**spent):
        out = dict((k, {"runs": 0, "usd": 0.0}) for k in KINDS)
        for k, (n, usd) in spent.items():
            out[k] = {"runs": n, "usd": usd}
        return out

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

    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        # -- a healthy pass ------------------------------------------------------------
        cfg, ids = std_world(os.path.join(tmp, "std"))
        inputs = (os.path.join(tmp, "std", "disp"), cfg["stage_e_state_dir"],
                  cfg["stage_a_state_dir"])
        before = snapshot(*inputs)
        code, said = go(cfg)
        doc = written(cfg)
        check("a healthy pass writes the file and exits 0",
              (code, dig(doc, "result"), dig(doc, "error")), (EXIT_OK, OK, None))
        check("…a file that conforms to the contract's schema",
              check_schemas.document_problems(doc, SCHEMA_NAME) if doc else ["nothing written"],
              [])
        try:
            mode = (oct(os.stat(cfg["out"]).st_mode & 0o777),
                    sorted(os.listdir(os.path.dirname(cfg["out"]))))
        except OSError as exc:
            mode = ("nothing was written: %s" % exc, None)
        check("…world-readable for the role account's readers (0644), no temp file left",
              mode, ("0o644", ["usage.json"]))
        check("…it only reads: every log, ledger and record is left as it was found",
              snapshot(*inputs), before)
        check("…every source read, each saying what it was asked and what it answered",
              [(s, dig(doc, "sources", s, "state"), dig(doc, "sources", s, "asked"),
                dig(doc, "sources", s, "answered"), dig(doc, "sources", s, "read_at"))
               for s in SOURCES],
              [("logs", READ, 48, 48, "2026-11-03T12:00:00Z"),
               ("ledger", READ, 3, 3, "2026-11-03T12:00:00Z"),
               ("planner", READ, 1, 1, "2026-11-03T12:00:00Z")])
        check("…and every pass says it, in the sources' own words",
              ([l.split()[0] for l in said[1:4]], said[-2], said[-1].startswith("  written to ")),
              (["logs", "ledger", "planner"], "  result: ok", True))

        got = facts(os.path.join(tmp, "facts"))
        diff = dict((k, (got.get(k), want)) for k, want in EXPECT.items() if got.get(k) != want)
        check("each run is the kind, own spend, output tokens and length its log was written "
              "to say", (diff, sorted(set(got) - set(EXPECT))), ({}, []))
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

        check("one entry for each local date in the lookback, oldest first, today last",
              [d["date"] for d in dig(doc, "days")],
              ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31",
               "2026-11-01", "2026-11-02", "2026-11-03"])
        by_date = dict((d["date"], d) for d in dig(doc, "days") if isinstance(d, dict))

        def day(date):
            return dict((k, v) for k, v in by_date.get(date, {}).items() if k != "date")
        check("a quiet day is zeros, with every kind present",
              day("2026-10-29"), {"usd": 0.0, "runs": 0, "unpriced": 0, "seconds": 0,
                                  "by_kind": kinds()})
        check("a run before the week began is still in its day", day("2026-10-28"),
              {"usd": 0.8, "runs": 1, "unpriced": 0, "seconds": 30,
               "by_kind": kinds(coding=(1, 0.8))})
        check("a day's spend, runs, unpriced runs, seconds and kinds", day("2026-11-02"),
              {"usd": 5.7, "runs": 12, "unpriced": 1, "seconds": 1180,
               "by_kind": kinds(review=(1, 0.4), planner=(1, 1.1), coding=(1, 2.0),
                                bounce_fix=(2, 0.6), conflict_fix=(2, 0.5), catch_up=(1, 0.05),
                                other_later=(4, 1.05))})
        check("a run counts on the local date of its last line: 22:30 UTC is the next day "
              "at UTC+3", day("2026-11-03"),
              {"usd": 1.9, "runs": 8, "unpriced": 1, "seconds": 320,
               "by_kind": kinds(tech_lead=(3, 0.5), review=(1, 0.3), coding=(3, 0.7),
                                other_later=(1, 0.4))})
        week = dig(doc, "week")
        check("the week starts at the newest weekly event's reset, less seven days",
              (dig(week, "known"), dig(week, "starts_at"), dig(week, "resets_at")),
              (True, "2026-10-30T09:00:00Z", "2026-11-06T09:00:00Z"))
        check("…its spend, runs, unpriced runs and seconds: a run before the week is left out",
              [dig(week, k) for k in ("usd", "runs", "unpriced", "seconds")],
              [10.6, 21, 2, 2100])
        check("…by kind, and the kinds add up to it",
              (dig(week, "by_kind"),
               round(sum(k["usd"] for k in dig(week, "by_kind").values()), 6)),
              (kinds(tech_lead=(3, 0.5), review=(2, 0.7), planner=(1, 1.1), coding=(5, 5.7),
                     bounce_fix=(2, 0.6), conflict_fix=(2, 0.5), catch_up=(1, 0.05),
                     other_later=(5, 1.45)), 10.6))
        check("…by model, a name that is not a plain one counting as other",
              dig(week, "by_model"), {OPUS: 10.1, SONNET: 0.5, "other": 0.0})
        check("the week's pieces: costliest first, ticket ids only; a chat and a folder that "
              "is not a ticket are never listed",
              [(p["id"], p["kind"], p["usd"], p["runs"], p["last_at"])
               for p in dig(doc, "pieces")],
              [("ENG-10", "coding", 4.3, 7, "2026-11-02T20:05:00Z"),
               ("ENG-11", "coding", 2.9, 4, "2026-11-02T12:30:50Z"),
               ("ENG-20", "planner", 1.1, 1, "2026-11-02T11:02:00Z"),
               ("REV-9", "review", 0.7, 2, "2026-11-03T10:01:30Z"),
               ("ENG-14", "coding", 0.5, 1, "2026-11-03T05:00:30Z"),
               ("ENG-13", "coding", 0.4, 1, "2026-11-03T07:01:00Z"),
               ("ENG-17", "coding", 0.0, 1, "2026-11-03T11:00:00Z")])
        check("the newest event for each window, and a window or word the list lacks is "
              "other", dig(doc, "status", "windows"), {
                  "five_hour": {"status": "allowed_warning", "utilization": 0.3,
                                "surpassed": 0.25, "resets_at": "2026-11-03T14:00:00Z",
                                "seen_at": "2026-11-03T09:00:20Z"},
                  "seven_day": {"status": "allowed", "utilization": 0.21, "surpassed": None,
                                "resets_at": "2026-11-06T09:00:00Z",
                                "seen_at": "2026-11-03T10:00:30Z"},
                  "overage": {"status": "rejected", "utilization": 1.0, "surpassed": None,
                              "resets_at": "2026-11-30T00:00:00Z",
                              "seen_at": "2026-11-02T16:01:20Z"},
                  "other": {"status": "other", "utilization": None, "surpassed": None,
                            "resets_at": None, "seen_at": "2026-11-02T20:00:50Z"}})
        check("…and how many events a day the dispatcher hears", dig(doc, "status",
                                                                     "events_per_day"), 0.9)
        check("how each run signed in; a word the SDK's list lacks is other, no init line is "
              "not seen", dig(doc, "sign_in", "counts"),
              {"none": 17, "ANTHROPIC_API_KEY": 2, "apiKeyHelper": 1, "other": 1,
               "not seen": 1})
        check("a run on a ticket that signed in any way but the plan's own (a key, or a word "
              "the SDK's list lacks) is listed by ticket id and time, newest first; a chat "
              "run is counted and never listed",
              dig(doc, "sign_in", "api_key_runs"),
              [{"id": "ENG-10", "at": "2026-11-02T20:00:00Z"},
               {"id": "ENG-11", "at": "2026-11-02T11:30:00Z"},
               {"id": "ENG-11", "at": "2026-11-02T09:00:00Z"}])
        check("runs that never reached the model: only the placeholders no run follows, and "
              "only on a day in the lookback", dig(doc, "never_reached_model"), 2)
        text = text_of(cfg)
        code, shown = go(cfg, show=True)
        check("FIELDS ONLY: no prompt, reply, result, title or folder name reaches the file, "
              "the summary or --print",
              (bool(text), LEAK in text, any(LEAK in s for s in said),
               any(LEAK in s for s in shown), any(s.startswith("{") for s in shown)),
              (True, False, False, False, True))

        # -- the week unknown: never zero, never a guess -------------------------------------
        UNKNOWN_WEEK = {"known": False, "starts_at": None, "resets_at": None, "usd": None,
                        "runs": None, "unpriced": None, "seconds": None, "by_kind": None,
                        "by_model": None}
        for label, weekly in (("no weekly event at all", "none"),
                              ("a weekly reset that has passed, so that week is over", "stale"),
                              ("a weekly reset more than a week ahead, so that week has not "
                               "begun", "far")):
            cfg_w, _ = std_world(os.path.join(tmp, "week-" + weekly), weekly=weekly)
            code, said = go(cfg_w)
            doc_w = written(cfg_w)
            check("%s: the week is unknown, every figure of it null, and the days stand" % label,
                  (code, dig(doc_w, "week"), dig(doc_w, "pieces"), len(dig(doc_w, "days")),
                   dig(doc_w, "days", 6, "usd")), (EXIT_OK, UNKNOWN_WEEK, [], 8, 5.7))
        check("…and an unknown week is said in the summary, not left blank",
              any("week     unknown" in s for s in said), True)
        cfg_s, _ = std_world(os.path.join(tmp, "week-short"), lookback_days=3)
        code, said = go(cfg_s)
        doc_s = written(cfg_s)
        check("a lookback that stops short of the week's start cannot total it: the week is "
              "unknown, the logs are partly read and say why, exit 3",
              (code, dig(doc_s, "week"), dig(doc_s, "sources", "logs", "state"),
               str(dig(doc_s, "sources", "logs", "reason")).startswith(
                   "the 3-day lookback does not reach the start of the plan's week"),
               len(dig(doc_s, "days"))), (EXIT_PARTIAL, UNKNOWN_WEEK, PARTLY, True, 3))

        # -- sources that fail: said, and the exit code says it too ---------------------------
        cfg_f, _ = std_world(os.path.join(tmp, "nologs"),
                             dispatcher_home=os.path.join(tmp, "no-such-dispatcher"))
        code, said = go(cfg_f)
        doc_f = written(cfg_f)
        check("logs that cannot be listed: could-not-read, exit 1, a failed file with no figure",
              (code, dig(doc_f, "result"),
               [dig(doc_f, "sources", "logs", k) for k in ("state", "asked", "answered")],
               dig(doc_f, "days"), dig(doc_f, "pieces"), dig(doc_f, "never_reached_model"),
               dig(doc_f, "week")),
              (EXIT_ERROR, FAILED, [COULD_NOT, 1, 0], [], [], None, UNKNOWN_WEEK))
        check("…the reason names the cause and the config key to check, and no path",
              (dig(doc_f, "sources", "logs", "reason"), tmp in text_of(cfg_f)),
              ("the dispatcher's logs folder could not be listed (FileNotFoundError): is "
               "dispatcher_home right?", False))
        check("…and the other sources are still read",
              [dig(doc_f, "sources", s, "state") for s in ("ledger", "planner")], [READ, READ])
        cfg_g, _ = std_world(os.path.join(tmp, "alldown"),
                             dispatcher_home=os.path.join(tmp, "no-such-dispatcher"),
                             stage_e_state_dir=os.path.join(tmp, "no-e"),
                             stage_a_state_dir=os.path.join(tmp, "no-a"))
        code, said = go(cfg_g)
        doc_g = written(cfg_g)
        check("no source that is on can be read: failed, exit 1, each one named",
              (code, dig(doc_g, "result"), [dig(doc_g, "sources", s, "state") for s in SOURCES]),
              (EXIT_ERROR, FAILED, [COULD_NOT] * 3))
        cfg_h, ids_h = std_world(os.path.join(tmp, "nostate"),
                                 stage_e_state_dir=os.path.join(tmp, "no-e"),
                                 stage_a_state_dir=os.path.join(tmp, "no-a"))
        code, said = go(cfg_h)
        doc_h = written(cfg_h)
        check("state folders that do not exist: partial, exit 3, each source says which config "
              "key to check or to empty",
              (code, dig(doc_h, "result"), [dig(doc_h, "sources", s, "state") for s in SOURCES],
               dig(doc_h, "sources", "ledger", "reason"),
               dig(doc_h, "sources", "planner", "reason")),
              (EXIT_PARTIAL, PARTIAL, [READ, COULD_NOT, COULD_NOT],
               "Stage E's state folder does not exist: is stage_e_state_dir right? Set it to "
               "\"\" to turn this source off",
               "the idea gate's state folder does not exist: is stage_a_state_dir right? Set "
               "it to \"\" to turn this source off"))
        got_h = facts_for(cfg_h, ids_h)
        check("a source that was not read leaves its kinds to be told from what is left: a "
              "bounce by its prompt, a conflict fix with no mark, a planning run as coding",
              (got_h["K2"][:2], got_h["K3"][:2], got_h["P1"][:2]),
              (("bounce_fix", "prompt"), ("other_later", "no mark"), ("coding", "first run")))
        cfg_o, _ = std_world(os.path.join(tmp, "off"), stage_e_state_dir="",
                             stage_a_state_dir="")
        code, said = go(cfg_o)
        doc_o = written(cfg_o)
        check("a source the config turns off is off, asks nothing, and does not make the pass "
              "partial",
              (code, dig(doc_o, "result"),
               [(dig(doc_o, "sources", s, "state"), dig(doc_o, "sources", s, "asked"),
                 dig(doc_o, "sources", s, "reason")) for s in SOURCES]),
              (EXIT_OK, OK, [(READ, 48, None), (OFF, 0, None), (OFF, 0, None)]))
        cfg_p, _ = std_world(os.path.join(tmp, "partly"))
        with open(os.path.join(cfg_p["stage_e_state_dir"], "outcomes", "broken.json"), "w") as fh:
            fh.write("{")
        code, said = go(cfg_p)
        doc_p = written(cfg_p)
        check("a review record that cannot be read: the ledger is partly read, said, exit 3",
              (code, [dig(doc_p, "sources", "ledger", k)
                      for k in ("state", "asked", "answered", "reason")]),
              (EXIT_PARTIAL, [PARTLY, 4, 3, "a review record could not be read"]))
        cfg_t, _ = std_world(os.path.join(tmp, "torn"))
        with open(os.path.join(cfg_t["stage_e_state_dir"], "bounce-ledger.jsonl"), "a") as fh:
            fh.write('{"at": "2026-11-02T21:00:00Z", "repo": "acme/ap\n[1, 2]\n')
        code, said = go(cfg_t)
        doc_t = written(cfg_t)
        check("ledger lines that are not rows, a torn line among them: partly read, counted, "
              "exit 3",
              (code, [dig(doc_t, "sources", "ledger", k)
                      for k in ("state", "asked", "answered", "reason")]),
              (EXIT_PARTIAL, [PARTLY, 3, 2, "2 line(s) of the bounce ledger could not be read"]))
        for n, (label, content) in enumerate((("a record that is not JSON", "not json"),
                                              ("a record with no triggers", "{}"))):
            cfg_a, _ = std_world(os.path.join(tmp, "planner-%d" % n))
            with open(os.path.join(cfg_a["stage_a_state_dir"], "seen.json"), "w") as fh:
                fh.write(content)
            code, said = go(cfg_a)
            doc_a = written(cfg_a)
            check("the idea gate's %s: could-not-read, exit 3, and no planning run is guessed"
                  % label,
                  (code, [dig(doc_a, "sources", "planner", k)
                          for k in ("state", "asked", "answered")]),
                  (EXIT_PARTIAL, [COULD_NOT, 1, 0]))
        cfg_l, _ = std_world(os.path.join(tmp, "odd-entries"))
        odd = os.path.join(cfg_l["dispatcher_home"], "logs", "ENG-14")
        oddities = [os.path.join(odd, "session-%s-2026-11-03T05-%02d-00-000Z.jsonl" % (sid(8), m))
                    for m in (10, 20, 30)]
        os.mkdir(oddities[0])
        os.mkfifo(oddities[1])
        os.symlink(os.path.join(tmp, "elsewhere.jsonl"), oddities[2])
        for path in oddities:
            os.utime(path, (T, T), follow_symlinks=False)
        code, said = go(cfg_l)
        doc_l = written(cfg_l)
        check("a folder, a pipe and a link named like run files are asked and not answered, "
              "never opened: partly read, exit 3, and the pass does not hang",
              (code, [dig(doc_l, "sources", "logs", k)
                      for k in ("state", "asked", "answered", "reason")]),
              (EXIT_PARTIAL, [PARTLY, 51, 48, "a log file could not be read (and 2 more)"]))

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

        # -- hostile logs cannot make the file fail its own schema ----------------------------
        cfg_x = {"dispatcher_home": os.path.join(tmp, "hostile", "disp"),
                 "stage_e_state_dir": "", "stage_a_state_dir": "", "reviews_team_key": "REV",
                 "out": os.path.join(tmp, "hostile", "out", "usage.json"), "lookback_days": 8}
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
        code, said = go(cfg_x)
        doc_x = written(cfg_x)
        check("hostile logs (70 model names, junk names, NaN and Infinity, fields of the wrong "
              "type, an impossible date, lines that are not JSON) still give a conforming file, "
              "exit 0", (code, check_schemas.document_problems(doc_x, SCHEMA_NAME)
                         if doc_x else ["nothing written"]), (EXIT_OK, []))
        check("…past 64 models the costliest keep their names and the rest fold into other, "
              "the spend adding up",
              (len(dig(doc_x, "week", "by_model")), "other" in dig(doc_x, "week", "by_model"),
               round(sum(dig(doc_x, "week", "by_model").values()), 6)), (64, True, 32.85))
        check("…a sign-in source that is not a word is other, and an event that is not a "
              "window or a word is other with nothing else kept",
              (dig(doc_x, "sign_in", "counts"), dig(doc_x, "status", "windows", "other")),
              ({"other": 1}, {"status": "other", "utilization": None, "surpassed": None,
                              "resets_at": None, "seen_at": "2026-11-03T08:00:05Z"}))
        check("…and none of it is in the file",
              (LEAK in text_of(cfg_x), "x" * 100 in text_of(cfg_x)), (False, False))
        capped = cap_models(dict(("m%03d" % n, 0.01 * (n + 1)) for n in range(70)))
        check("past 64 models the costliest keep their names and the rest fold into other",
              (len(capped), "m069" in capped, "m000" in capped, round(sum(capped.values()), 6)),
              (64, True, False, 24.85))
        real_cap = globals()["cap_models"]
        globals()["cap_models"] = lambda by_model: by_model
        try:
            code, said = go(cfg_x)
        finally:
            globals()["cap_models"] = real_cap
        check("mutant: no cap on models is caught, for the file would be refused",
              (code, any("REFUSED" in s for s in said)), (EXIT_ERROR, True))

        # -- a document that does not conform is never written --------------------------------
        cfg_r, _ = std_world(os.path.join(tmp, "refused"))
        go(cfg_r)
        good = text_of(cfg_r)
        real_iso = globals()["_iso"]
        globals()["_iso"] = lambda t: "yesterday"
        try:
            code, said = go(cfg_r)
        finally:
            globals()["_iso"] = real_iso
        refused_at = [i for i, s in enumerate(said) if "REFUSED" in s]
        after = said[refused_at[0] + 1:] if refused_at else ["<no refusal said>"]
        check("a document that does not conform is refused: exit 1, said by where and by rule "
              "and never by value, and the old file stands",
              (code, bool(refused_at), any("yesterday" in s for s in after),
               all(re.match(r"^    \S+ \[[A-Za-z]+\]$", s) for s in after),
               bool(good) and text_of(cfg_r) == good), (EXIT_ERROR, True, False, True, True))

        # -- a pass that crashes ---------------------------------------------------------------
        cfg_k, _ = std_world(os.path.join(tmp, "crash"))

        def boom(*_a, **_k):
            raise ValueError("a prompt said " + LEAK)
        real_summarize = globals()["summarize"]
        globals()["summarize"] = boom
        try:
            code, said = go(cfg_k)
        finally:
            globals()["summarize"] = real_summarize
        doc_k = written(cfg_k)
        check("a pass that crashes still writes a failed file that says where, exit 1",
              (code, dig(doc_k, "result"), [dig(doc_k, "sources", s, "state") for s in SOURCES],
               bool(re.match(r"^the pass stopped on an unexpected error: ValueError in boom "
                             r"\(line [0-9]+\)$", str(dig(doc_k, "error")))),
               dig(doc_k, "error") == dig(doc_k, "sources", "logs", "reason")),
              (EXIT_ERROR, FAILED, [COULD_NOT] * 3, True, True))
        check("…and the exception's own words, which may be a log's, reach neither the file "
              "nor the output", (LEAK in text_of(cfg_k), any(LEAK in s for s in said)),
              (False, False))
        crash_doc = crashed(dict(cfg, stage_e_state_dir="", stage_a_state_dir=""), T,
                            lambda: T, ValueError("x"))
        check("…a crash keeps a source the config turned off off, and the file still conforms",
              ([crash_doc["sources"][s]["state"] for s in SOURCES],
               check_schemas.document_problems(crash_doc, SCHEMA_NAME)),
              ([COULD_NOT, OFF, OFF], []))

        # -- dry run, and a file that cannot be written ----------------------------------------
        cfg_d = dict(cfg, out=os.path.join(tmp, "dry", "usage.json"))
        code, said = go(cfg_d, dry_run=True)
        check("--dry-run reads and checks everything, writes nothing, and says where it "
              "would", (code, os.path.exists(os.path.dirname(cfg_d["out"])),
                        any("would write" in s for s in said),
                        any(s.startswith("{") for s in said)),
              (EXIT_OK, False, True, False))
        code, said = go(cfg_d, dry_run=True, show=True)
        check("…and --print adds the document it would write",
              any(s.startswith("{") and USAGE_SCHEMA in s for s in said), True)
        blocked = os.path.join(tmp, "blocked")
        open(blocked, "w").close()
        code, said = go(dict(cfg, out=os.path.join(blocked, "usage.json")))
        check("an out file whose folder cannot be made is exit 1, and said",
              (code, any("COULD NOT WRITE" in s for s in said)), (EXIT_ERROR, True))
        real_replace = os.replace

        def refuse(_src, _dst):
            raise OSError("no space left on device")
        os.replace = refuse
        try:
            code, said = go(dict(cfg, out=os.path.join(tmp, "full", "usage.json")))
        finally:
            os.replace = real_replace
        check("a write that fails part-way is exit 1, said, and leaves no temp file behind",
              (code, any("COULD NOT WRITE" in s for s in said),
               os.listdir(os.path.join(tmp, "full"))), (EXIT_ERROR, True, []))

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

        # -- the ledger's window, a review's key, and the pass's verdict --------------------
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
        check("the pass's verdict: the logs are the figures, so a pass without them failed",
              [judge({"logs": {"state": a}, "ledger": {"state": b}, "planner": {"state": c}})
               for a, b, c in ((READ, READ, READ), (READ, OFF, OFF), (READ, PARTLY, COULD_NOT),
                               (PARTLY, READ, READ), (COULD_NOT, READ, READ),
                               (COULD_NOT, COULD_NOT, OFF))],
              [OK, OK, PARTIAL, PARTIAL, FAILED, FAILED])

        # -- config -------------------------------------------------------------------------
        def conf_file(doc):
            conf = os.path.join(tmp, "usage-%d.json" % len(os.listdir(tmp)))
            with open(conf, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            return conf

        def refused(doc):
            try:
                load_config(conf_file(doc))
            except ConfigError as exc:
                return str(exc)
            return ""
        example = dict(EXAMPLE_CONFIG, out=os.path.join(tmp, "o", "usage.json"),
                       dispatcher_home=os.path.join(tmp, "d"),
                       stage_e_state_dir=os.path.join(tmp, "e"),
                       stage_a_state_dir=os.path.join(tmp, "a"))
        cfg_e = load_config(conf_file(example))
        check("the example config loads", (cfg_e["reviews_team_key"], cfg_e["lookback_days"],
                                           cfg_e["out"]),
              ("REV", DEFAULT_LOOKBACK_DAYS, os.path.join(tmp, "o", "usage.json")))
        check("an unknown key is refused", "unknown config key" in refused(dict(example, x=1)),
              True)
        check("the reviews team key is required, and is a team key",
              ["reviews_team_key" in refused(d) for d in (
                  dict((k, v) for k, v in example.items() if k != "reviews_team_key"),
                  dict(example, reviews_team_key="rev"), dict(example, reviews_team_key=5))],
              [True, True, True])
        check("lookback_days is a whole number of days from 1 to 31",
              ["lookback_days" in refused(dict(example, lookback_days=v))
               for v in (0, 32, True, 1.5, "8")]
              + [refused(dict(example, lookback_days=v)) for v in (1, 31)],
              [True] * 5 + ["", ""])
        check("a relative path is refused, for the out file and for each source",
              ["absolute" in refused(dict(example, **{k: "some/where"}))
               for k in ("out", "dispatcher_home", "stage_e_state_dir", "stage_a_state_dir")],
              [True] * 4)
        worktree = os.path.join(tmp, "worktree")
        os.makedirs(os.path.join(worktree, ".git"))
        os.makedirs(os.path.join(tmp, "is-a-folder"))
        check("an out file inside a git working tree is refused, and so is a folder",
              ["git working tree" in refused(dict(example, out=os.path.join(
                  worktree, "out", "usage.json"))),
               "not a folder" in refused(dict(example, out=os.path.join(tmp, "is-a-folder")))],
              [True, True])
        check("empty turns a state source off, and only a state source",
              [load_config(conf_file(dict(example, stage_e_state_dir="",
                                          stage_a_state_dir="")))[k]
               for k in ("stage_e_state_dir", "stage_a_state_dir")]
              + ["absolute" in refused(dict(example, dispatcher_home=""))], ["", "", True])
        saved_home = os.environ.get("HOME")
        os.environ["HOME"] = os.path.join(tmp, "not-my-home")
        try:
            tilde, bare = resolve_path("~/x/y", "k"), resolve_path("~", "k")
        finally:
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
        home_db = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
        check("~ is this account's home from the account database, never $HOME",
              (tilde, bare), (os.path.realpath(os.path.join(home_db, "x", "y")), home_db))
        check("~name is not a path this job resolves",
              "absolute" in refused(dict(example, out="~root/usage.json")), True)

        captured = io.StringIO()
        bad = conf_file(dict(example, x=1, out=os.path.join(tmp, "never", "usage.json")))
        with contextlib.redirect_stdout(captured):
            code = main(["run", "--config", bad])
        check("a refused config is exit 2, says why, and writes nothing",
              (code, "CONFIG: unknown config key" in captured.getvalue(),
               os.path.exists(os.path.join(tmp, "never"))), (EXIT_USAGE, True, False))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = main([])
        check("no command is a usage error", code, EXIT_USAGE)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = main(["--example-config"])
        check("--example-config prints every key, as this job's own defaults",
              (code, json.loads(captured.getvalue()) == EXAMPLE_CONFIG,
               sorted(EXAMPLE_CONFIG) == sorted(CONFIG_KEYS)), (EXIT_OK, True, True))
        cfg_m, _ = std_world(os.path.join(tmp, "main"))
        conf = conf_file(dict(example, dispatcher_home=cfg_m["dispatcher_home"],
                              stage_e_state_dir=cfg_m["stage_e_state_dir"],
                              stage_a_state_dir=cfg_m["stage_a_state_dir"],
                              out=os.path.join(tmp, "main", "out", "usage.json")))
        real_time = time.time
        time.time = lambda: float(T)
        try:
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = main(["run", "--config", conf, "--dry-run"])
        finally:
            time.time = real_time
        check("the command line runs a pass end to end: config, pass, summary, nothing written",
              (code, "usage job: 2026-11-03T12:00:00Z (dry run)" in captured.getvalue(),
               "  result: ok" in captured.getvalue(), LEAK in captured.getvalue(),
               os.path.exists(os.path.join(tmp, "main", "out"))),
              (EXIT_OK, True, True, False, False))

        # -- what it never does ---------------------------------------------------------------
        cfg_n, _ = std_world(os.path.join(tmp, "never-does"))
        out_dir = os.path.realpath(os.path.dirname(cfg_n["out"]))
        wrote, net = [], []
        real_open, real_os_open = builtins.open, os.open
        writers = ("replace", "rename", "unlink", "remove", "rmdir", "mkdir", "makedirs",
                   "chmod", "utime", "symlink", "link", "truncate")
        real_os = dict((n, getattr(os, n)) for n in writers)

        def note(*paths):
            for p in paths:
                if isinstance(p, (str, bytes)):
                    wrote.append(os.path.realpath(os.fsdecode(p)))

        def spy_open(file, mode="r", *a, **k):
            if any(c in str(mode) for c in "wax+"):
                note(file)
            return real_open(file, mode, *a, **k)

        def spy_os_open(path, flags, *a, **k):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
                note(path)
            return real_os_open(path, flags, *a, **k)

        def spy_os(name):
            def spy(*args, **kwargs):
                note(*args[:2])
                return real_os[name](*args, **kwargs)
            return spy

        def no_net(*_a, **_k):
            net.append("tried")
            raise OSError("no network in the selftest")
        own_connect = socket.socket.__dict__.get("connect")
        real_create, real_lookup = socket.create_connection, socket.getaddrinfo
        builtins.open, os.open = spy_open, spy_os_open
        for name in writers:
            setattr(os, name, spy_os(name))
        socket.socket.connect, socket.create_connection, socket.getaddrinfo = (
            no_net, no_net, no_net)
        try:
            code, said = go(cfg_n)
        finally:
            builtins.open, os.open = real_open, real_os_open
            for name in writers:
                setattr(os, name, real_os[name])
            if own_connect is None:
                del socket.socket.connect
            else:
                socket.socket.connect = own_connect
            socket.create_connection, socket.getaddrinfo = real_create, real_lookup
        check("it writes nothing but its own file: every path it writes is under the out "
              "folder, and the file is among them",
              (bool(wrote), all(p == out_dir or p.startswith(out_dir + os.sep) for p in wrote),
               os.path.realpath(cfg_n["out"]) in wrote), (True, True, True))
        check("it opens no socket, and the pass still ran", (net, code), ([], EXIT_OK))
        source = inspect.getsource(sys.modules[__name__]).split("\ndef selftest():")[0]
        check("the job opens no socket, starts no process and reads no environment variable: "
              "none of those is in its source",
              [w for w in ("import socket", "urllib", "http.client", "subprocess", "shutil",
                           "os.system", "os.popen", "os.environ", "getenv", "os.fork",
                           "os.exec", "os.spawn") if w in source], [])
        check("it writes one file: one open for writing, one atomic replace, one clean-up, "
              "and no other remove, rename or copy",
              (len(re.findall(r"open\([^)]*\"w\"", source)), source.count("os.replace("),
               source.count("os.unlink("),
               [w for w in ("os.remove(", "os.rename(", "rmdir(", "copyfile", "os.link(",
                            "os.symlink(", "os.truncate(") if w in source]),
              (1, 1, 1, []))

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

    # -- mutants: a check that cannot fail checks nothing -------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        original = dict((n, globals()[n]) for n in ("parse_run", "price", "PROMPT_MARKS"))

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
        check("…and with every mutant undone the job is as it was",
              facts(os.path.join(tmp, "clean")) == EXPECT, True)


if __name__ == "__main__":
    sys.exit(main())
