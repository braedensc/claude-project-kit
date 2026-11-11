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
  1. scripts/pipeline_usage_runs.py reads the dispatcher's session logs, Stage E's bounce
     ledger and review records, and the idea gate's record of planning runs, into runs: each
     priced (its share of its session's running totals; a crash's zeroed total unpriced,
     never zero), classified, timed and signed in. Nothing a session can write decides what a
     run was.
  2. This job counts the runs: spend per local day, and since the plan's weekly reset by kind
     of work and by piece; how long sessions ran; Anthropic's newest word on each usage window;
     how each session signed in; the runs that never reached the model; and the runs the plan's
     limit cut off (a rejected window, then an error or no result), newest first.
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
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_schemas  # noqa: E402  (the one definition of "conforms", contract §12)
import pipeline_usage_runs as usage_runs  # noqa: E402  (the reading half: the runs themselves)
from pipeline_usage_runs import (  # noqa: E402
    COULD_NOT, KINDS, OFF, PARTLY, READ, SOURCES, TICKET_RE, WORD_OTHER, _iso, line, scan,
    unmatched_placeholders)

USAGE_SCHEMA = "pipeline-usage/1"
SCHEMA_NAME = "pipeline-usage"

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3
OK, PARTIAL, FAILED = "ok", "partial", "failed"

DEFAULT_DISPATCHER_HOME = "~/.cyrus"
DEFAULT_STAGE_E_STATE = "~/.stage-e/state"
DEFAULT_STAGE_A_STATE = "~/.stage-a/state"
DEFAULT_OUT = "~/.stage-e/usage/usage.json"
DEFAULT_LOOKBACK_DAYS = 8
MAX_LOOKBACK_DAYS = 31

LABELS = {"tech_lead": "tech lead", "review": "review", "planner": "planner", "coding": "coding",
          "bounce_fix": "bounce fix", "conflict_fix": "conflict fix", "catch_up": "catch-up",
          "other_later": "other later", "judge": "judge"}

# What a day or the week is totalled by. The dispatcher's runs are classified into KINDS; the
# watcher's judge is not a dispatcher run, so its calls are counted in from its own record
# and get a kind of their own here (KIT-569). KINDS itself is left alone: it is the list
# of kinds a run the plan's limit cut off can have, and a judge call is never one.
JUDGE_KIND = "judge"
COST_KINDS = KINDS + (JUDGE_KIND,)
# The judge's calls file (scripts/pipeline_watcher_judge.py, contract §17): times and dollars only.
JUDGE_SCHEMA = "pipeline-watcher-judge-calls/1"
JUDGE_FILE_CAP = 4 * 1024 * 1024
JUDGE_CALLS_CAP = 20000
JUDGE_NOTE_STATES = (READ, COULD_NOT, OFF)

# The plan's own sign-in: no key at all ("none", what a setup token reports) or the
# claude.ai login ("oauth"). Every other word is listed, an unknown one included: the
# plan's windows cannot be confirmed for it, and a key is billed by the token.
PLAN_SOURCES = ("none", "oauth")
# What the file counts a run as when its log names no sign-in source: no init line, or one that
# names none.
KEY_UNSEEN = "not seen"

# Bounds, so no log can leave the document malformed, as schemas/pipeline-usage.schema.json
# allows: at most this many are listed, or kept by name.
PIECES_CAP = 1000
API_KEY_RUNS_CAP = 500
MODELS_CAP = 64
# A runaway cap, well above a normal week's count of runs the plan's limit cut off. Past it the
# newest are listed and `limit_stops_over_cap` says how many were left out.
LIMIT_STOPS_CAP = 200

# Python's `$` also matches before a final line break, so a whole-value pattern ends
# with `\Z`: a team key with a line break on its end is refused, not kept.
TEAM_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}\Z")

CONFIG_KEYS = {
    "dispatcher_home": "the dispatcher's folder, whose logs/ holds every session's log; "
                       "absolute or ~/ (default %s)" % DEFAULT_DISPATCHER_HOME,
    "stage_e_state_dir": "Stage E's state_dir, whose bounce-ledger.jsonl and outcomes/ say "
                         "which later run was a bounce fix or a conflict fix (default %s); "
                         "empty turns this source off" % DEFAULT_STAGE_E_STATE,
    "stage_a_state_dir": "the idea gate's state_dir, whose seen.json names each planning "
                         "run's ticket (default %s); empty turns this source off"
                         % DEFAULT_STAGE_A_STATE,
    "judge_file": "the watcher judge's judge-calls.json, whose calls the judge's cost is read "
                  "from (default: empty, which turns this source off)",
    "reviews_team_key": "optional: the KEY of an old Reviews team, e.g. \"REV\": a run in a folder "
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
    "judge_file": "",
    "reviews_team_key": "REV",
    "out": DEFAULT_OUT,
    "lookback_days": DEFAULT_LOOKBACK_DAYS,
}


class ConfigError(Exception):
    """Bad arguments or config (exit 2): nothing was read, and nothing is written."""


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
    # KIT-596: optional. A review on a work team is told by the review poller's own record;
    # this key only names an old Reviews team, whose folders are still reviews.
    team = raw.get("reviews_team_key", "")
    if not isinstance(team, str) or not (team == "" or TEAM_RE.match(team)):
        raise ConfigError("config 'reviews_team_key' must be a team KEY, e.g. \"REV\", or "
                          "left out")
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
        "judge_file": resolve_path(raw.get("judge_file", ""), "judge_file", off_when_empty=True),
        "reviews_team_key": team,
        "out": out,
        "lookback_days": days,
    }



# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #
def blank_totals():
    return {"usd": 0.0, "runs": 0, "unpriced": 0, "seconds": 0,
            "by_kind": dict((k, {"runs": 0, "usd": 0.0}) for k in COST_KINDS)}


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


def judge_note(state, reason=None):
    return {"state": state, "reason": line(reason) if reason else None}


def judge_time(text):
    """A call's time as epoch seconds, or None for anything but the judge's own format."""
    if not isinstance(text, str) or not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z", text):
        return None
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return None


def read_judge(path):
    """(note, calls) from the judge's record: every call as a run of kind `judge`. The job
    reads only each call's time and dollars, never the judge's words. A record that is not
    there to read, or not the record this job knows, is `could-not-read`, with no call
    counted: the file says it could not check, never that the judge spent nothing. Every
    reason is fixed words: a record's own text is not quoted."""
    if not path:
        return judge_note(OFF), []
    try:
        if os.path.getsize(path) > JUDGE_FILE_CAP:
            return judge_note(COULD_NOT, "the judge's record is too large to read"), []
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return judge_note(COULD_NOT, "the judge's record could not be read, so its cost is "
                                     "not counted"), []
    rows = record.get("calls") if isinstance(record, dict) else None
    if (not isinstance(record, dict) or record.get("schema") != JUDGE_SCHEMA
            or not isinstance(rows, list)):
        return judge_note(COULD_NOT, "the judge's record is not one this job knows, so its "
                                     "cost is not counted"), []
    if len(rows) > JUDGE_CALLS_CAP:
        return judge_note(COULD_NOT, "the judge's record holds more calls than this job "
                                     "reads, so its cost is not counted"), []
    calls = []
    for row in rows:
        at = judge_time(row.get("at")) if isinstance(row, dict) else None
        usd = row.get("usd") if isinstance(row, dict) else None
        priced = (isinstance(usd, (int, float)) and not isinstance(usd, bool)
                  and math.isfinite(usd) and usd >= 0)
        if (at is None or set(row) != {"at", "usd"} or not (priced or usd is None)):
            return judge_note(COULD_NOT, "the judge's record holds a call this job does not "
                                         "know, so its cost is not counted"), []
        calls.append({"last": float(at), "cost": float(usd) if priced else None,
                      "seconds": 0, "kind": JUDGE_KIND})
    return judge_note(READ), calls


def empty_doc(started, now, sources, result, error, judge_state=None):
    return {"schema": USAGE_SCHEMA, "written_at": _iso(now()), "started_at": _iso(started),
            "result": result, "error": error, "sources": sources,
            "judge": judge_state or judge_note(OFF), "week": unknown_week(),
            "days": [], "pieces": [], "status": {"windows": {}, "events_per_day": None},
            "sign_in": {"counts": {}, "api_key_runs": []}, "never_reached_model": None,
            "limit_stops": [], "limit_stops_over_cap": None}


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


def workspace_kind(ws, cfg, planners, reviewers=frozenset()):
    """A piece's kind is its workspace's: review, planner, or coding for any other ticket."""
    if ws in (reviewers or ()) or (cfg.get("reviews_team_key")
                                   and ws.startswith(cfg["reviews_team_key"] + "-")):
        return "review"
    return "planner" if planners and ws in planners else "coding"


def summarize(cfg, scanned, now, started):
    """The document one pass would write. A source that failed is said in it; nothing here
    raises for a source that is down."""
    src = scanned["sources"]
    runs = scanned["runs"]
    note, judged = read_judge(cfg.get("judge_file") or "")
    if runs is None:
        sources = dict((name, src[name].record()) for name in SOURCES)
        return empty_doc(started, now, sources, judge(sources), None, note)
    now_at = now()
    today = datetime.date.fromtimestamp(now_at)
    dates = [today - datetime.timedelta(days=i) for i in range(cfg["lookback_days"] - 1, -1, -1)]
    names = [d.isoformat() for d in dates]
    listed_days = set(names)
    window_start = local_midnight(dates[0])
    for r in runs:
        r["date"] = local_date(r["last"])
    inside = [r for r in runs if r["date"] in listed_days]
    for c in judged:
        c["date"] = local_date(c["last"])
    judged = [c for c in judged if c["date"] in listed_days]
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
        # Both are instants, and local_midnight already allows for daylight saving, so
        # the edge is exact: a week that starts before the window cannot be totalled.
        if current and begins < window_start:
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
            for c in judged:
                if begins <= c["last"]:
                    add_run(totals, c)
            week = dict(finish_totals(totals), known=True, starts_at=_iso(begins),
                        resets_at=_iso(resets), by_model=cap_models(by_model))
    days = []
    for name in names:
        totals = blank_totals()
        for r in inside + judged:
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
    pieces = [{"id": p["id"], "kind": workspace_kind(p["id"], cfg, scanned["planners"],
                                                     scanned.get("reviewers")),
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
    stopped = sorted((r for r in inside if r["limit_stop"]),
                     key=lambda r: (r["last"], r["workspace"]), reverse=True)
    stops = [{"ticket": r["workspace"] if TICKET_RE.match(r["workspace"]) else None,
              "kind": r["kind"], "ended_at": _iso(r["last"]),
              "window": r["limit_stop"]["window"],
              "resets_at": (_iso(r["limit_stop"]["resets_at"])
                            if r["limit_stop"]["resets_at"] is not None else None)}
             for r in stopped[:LIMIT_STOPS_CAP]]
    never = len([p for p in unmatched_placeholders(scanned)
                 if local_date(p["start"]) in listed_days])
    if why:
        src["logs"].fail(why)
    sources = dict((name, src[name].record()) for name in SOURCES)
    result = judge(sources)
    if note["state"] == COULD_NOT and result == OK:
        result = PARTIAL
    return {"schema": USAGE_SCHEMA, "written_at": _iso(now()), "started_at": _iso(started),
            "result": result, "error": None, "sources": sources, "judge": note, "week": week,
            "days": days, "pieces": pieces,
            "status": {"windows": windows,
                       "events_per_day": round(len(times) / float(cfg["lookback_days"]), 1)},
            "sign_in": {"counts": counts, "api_key_runs": keyed[:API_KEY_RUNS_CAP]},
            "never_reached_model": never, "limit_stops": stops,
            "limit_stops_over_cap": max(0, len(stopped) - LIMIT_STOPS_CAP)}


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
    on = bool(cfg.get("judge_file"))
    return empty_doc(started, now, sources, FAILED, why,
                     judge_note(COULD_NOT, "the pass stopped before the judge's record was "
                                           "read") if on else judge_note(OFF))


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
            "%s %s" % (LABELS[k], dollars(week["by_kind"][k]["usd"])) for k in COST_KINDS))
    elif doc["sources"]["logs"]["state"] != COULD_NOT:
        out.append("  week     unknown: no usable weekly event in the logs, or the lookback "
                   "does not reach it")
    out.append("  judge    %s%s" % (doc["judge"]["state"],
                                  (": " + doc["judge"]["reason"]) if doc["judge"]["reason"]
                                  else ""))
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
    if doc["limit_stops"]:
        out.append("  stopped by the plan's limit: %d run(s) listed%s"
                   % (len(doc["limit_stops"]), (" (and %d more, past the cap)"
                                               % doc["limit_stops_over_cap"])
                                              if doc["limit_stops_over_cap"] else ""))
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
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "usage-local-selftest-run-")
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
    import contextlib
    import inspect
    import io
    import tempfile

    MISSING = "<missing>"
    kit = usage_runs.selftest_kit()
    T, LEAK, OPUS, SONNET, sid = kit.T, kit.LEAK, kit.OPUS, kit.SONNET, kit.sid
    std_world, hostile_world, Watch = kit.std_world, kit.hostile_world, kit.Watch

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
        out = dict((k, {"runs": 0, "usd": 0.0}) for k in COST_KINDS)
        for k, (n, usd) in spent.items():
            out[k] = {"runs": n, "usd": usd}
        return out


    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        # -- a healthy pass ------------------------------------------------------------
        cfg, _ = std_world(os.path.join(tmp, "std"))
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
        # The week's edge, to the second (Stage E's review of 27006b6): a lookback whose first
        # midnight comes after the week's start cannot total that week, however close, so
        # the week is unknown. One that starts at that midnight holds the whole week.
        for label, resets, want in (
                ("a week that starts half an hour before the lookback's first midnight",
                 "2026-11-06T20:30:00Z", (EXIT_PARTIAL, UNKNOWN_WEEK, PARTLY, True)),
                ("a week that starts at the lookback's first midnight, to the second",
                 "2026-11-06T21:00:00Z", (EXIT_OK, "2026-10-30T21:00:00Z", "read", False))):
            edge = os.path.join(tmp, "week-edge-" + resets[11:13] + resets[14:16])
            cfg_e, _ = std_world(edge, weekly="none", lookback_days=4)
            kit.write_run(os.path.join(edge, "disp", "logs"), "ENG-31", sid(31),
                          kit.U("2026-11-03T09:00:00Z"), total=0.5,
                          events=[(10, kit.week_event(resets))])
            code, _said = go(cfg_e)
            doc_e = written(cfg_e)
            week_e = dig(doc_e, "week")
            got = (code, week_e if want[1] == UNKNOWN_WEEK else dig(doc_e, "week", "starts_at"),
                   dig(doc_e, "sources", "logs", "state"),
                   str(dig(doc_e, "sources", "logs", "reason")).startswith(
                       "the 4-day lookback does not reach the start of the plan's week"))
            check("%s: %s" % (label, "the week is unknown, the logs partly read, exit 3"
                              if want[0] == EXIT_PARTIAL else "the week is known from it"),
                  got, want)

        # -- the judge's cost counts into the day and the week (KIT-569) ----------------------
        base_cfg, _ = std_world(os.path.join(tmp, "judge-base"))
        go(base_cfg)
        base = written(base_cfg)
        base_days = dict((d["date"], d) for d in dig(base, "days"))
        check("with no judge_file the judge note is off, and the day has a judge kind of zero",
              (dig(base, "judge"), dig(base, "result"), dig(base, "days", 7, "by_kind", "judge")),
              ({"state": "off", "reason": None}, OK, {"runs": 0, "usd": 0.0}))

        def judge_world(name, record, **cfg_kw):
            """The healthy world plus a judge record: `record` is the file's JSON, or a string
            that is written as it stands, or None for no file at all."""
            jcfg, _ = std_world(os.path.join(tmp, name))
            jcfg["judge_file"] = os.path.join(tmp, name + "-judge-calls.json")
            if record is not None:
                with open(jcfg["judge_file"], "w", encoding="utf-8") as fh:
                    fh.write(record if isinstance(record, str) else json.dumps(record))
            code_j, said_j = go(jcfg)
            return code_j, said_j, written(jcfg), jcfg

        def rows(*calls):
            return {"schema": JUDGE_SCHEMA, "calls": [{"at": a, "usd": u} for a, u in calls],
                    "words": LEAK}
        code_j, said_j, doc_j, cfg_j = judge_world("judge-read", rows(
            ("2026-11-03T10:00:00Z", 0.04), ("2026-11-03T10:30:00Z", None),
            ("2026-11-01T08:00:00Z", 0.03), ("2026-10-29T08:00:00Z", 0.02),
            ("2026-10-20T08:00:00Z", 0.5), ("2026-11-03T22:30:00Z", 0.7)))
        days_j = dict((d["date"], d) for d in dig(doc_j, "days"))
        check("a read judge record is read, the pass is ok, and the file conforms",
              (code_j, dig(doc_j, "judge"), dig(doc_j, "result"),
               check_schemas.document_problems(doc_j, SCHEMA_NAME)),
              (EXIT_OK, {"state": "read", "reason": None}, OK, []))
        check("…its calls count as runs of kind judge in their local day, an unpriced one as "
              "unpriced, and the day's dollars include them",
              dict((k, v) for k, v in days_j["2026-11-03"].items() if k != "by_kind"),
              dict((k, v) for k, v in dict(base_days["2026-11-03"], usd=1.94, runs=10,
                                           unpriced=2).items() if k != "by_kind"))
        check("…and the day's kinds say it: two judge runs, four cents",
              days_j["2026-11-03"]["by_kind"]["judge"], {"runs": 2, "usd": 0.04})
        check("…a call on an earlier day counts there, a call before the lookback and one "
              "after today count nowhere",
              (round(days_j["2026-11-01"]["usd"] - base_days["2026-11-01"]["usd"], 6),
               days_j["2026-10-29"]["usd"], days_j["2026-10-29"]["runs"],
               sum(d["by_kind"]["judge"]["runs"] for d in doc_j["days"])),
              (0.03, 0.02, 1, 4))
        week_j, week_b = dig(doc_j, "week"), dig(base, "week")
        check("…the week counts the calls since its start: a call before that is in its "
              "day only",
              (round(week_j["usd"] - week_b["usd"], 6), week_j["runs"] - week_b["runs"],
               week_j["unpriced"] - week_b["unpriced"], week_j["by_kind"]["judge"]),
              (0.07, 3, 1, {"runs": 3, "usd": 0.07}))
        check("…and the dollars still add up from the kinds",
              (round(sum(k["usd"] for k in week_j["by_kind"].values()), 6) == week_j["usd"],
               all(round(sum(k["usd"] for k in d["by_kind"].values()), 6) == d["usd"]
                   for d in doc_j["days"])), (True, True))
        check("…and none of the record's other text is in the file or the summary",
              (LEAK in json.dumps(doc_j), LEAK in " ".join(said_j)), (False, False))
        check("…the summary says the judge was read",
              any(l.startswith("  judge    read") for l in said_j), True)

        unreadable = (
            ("no file", None),
            ("not json", "{not json"),
            ("not a record", json.dumps([1, 2])),
            ("another schema", json.dumps(dict(rows(("2026-11-03T10:00:00Z", 0.04)),
                                                schema="something-else/1"))),
            ("calls missing", json.dumps({"schema": JUDGE_SCHEMA, "days": {}})),
            ("a row of text", json.dumps(dict(rows(), calls=["x"]))),
            ("a row with a word of its own", json.dumps({"schema": JUDGE_SCHEMA, "calls": [
                {"at": "2026-11-03T10:00:00Z", "usd": 0.04, "note": LEAK}]})),
            ("a time that is not UTC to the second", json.dumps(rows(("2026-11-03 10:00", 1)))),
            ("a negative cost", json.dumps(rows(("2026-11-03T10:00:00Z", -1)))),
            ("a cost that is a boolean", json.dumps(rows(("2026-11-03T10:00:00Z", True)))),
            ("a cost that is a word", json.dumps(rows(("2026-11-03T10:00:00Z", LEAK)))),
            ("a cost that is not a number", "{\"schema\": \"%s\", \"calls\": [{\"at\": "
             "\"2026-11-03T10:00:00Z\", \"usd\": NaN}]}" % JUDGE_SCHEMA),
            ("a cost that is infinite", "{\"schema\": \"%s\", \"calls\": [{\"at\": "
             "\"2026-11-03T10:00:00Z\", \"usd\": Infinity}]}" % JUDGE_SCHEMA),
            ("too many calls", json.dumps(rows(*[("2026-11-03T10:00:00Z", 0)]
                                                * (JUDGE_CALLS_CAP + 1)))),
        )
        got_u = []
        for i, (why_u, record_u) in enumerate(unreadable):
            code_u, _said_u, doc_u, _cfg_u = judge_world("judge-bad-%d" % i, record_u)
            got_u.append((why_u, code_u, dig(doc_u, "judge", "state"), dig(doc_u, "result"),
                          dig(doc_u, "days") == base["days"], dig(doc_u, "week") == base["week"],
                          check_schemas.document_problems(doc_u, SCHEMA_NAME),
                          LEAK in json.dumps(doc_u)))
        check("a judge record that is missing or not the record this job knows is "
              "could-not-read: nothing is counted, the result is partial, and no word of "
              "it reaches the file",
              got_u, [(w, EXIT_PARTIAL, COULD_NOT, PARTIAL, True, True, [], False)
                      for w, _r in unreadable])
        code_u, said_u, doc_u, _cfg_u = judge_world("judge-bad-say", None)
        check("…and the summary and the file say why in fixed words",
              (dig(doc_u, "judge", "reason"),
               any(l.startswith("  judge    could-not-read: ") for l in said_u)),
              ("the judge's record could not be read, so its cost is not counted", True))
        big = os.path.join(tmp, "judge-big.json")
        with open(big, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rows()) + " " * JUDGE_FILE_CAP)
        check("a record past the size this job reads is could-not-read, not read whole",
              read_judge(big)[0]["state"], COULD_NOT)
        check("the judge is a kind of its own, and the dispatcher's kinds are unchanged",
              (COST_KINDS[-1], COST_KINDS[:-1] == KINDS, JUDGE_KIND in KINDS),
              ("judge", True, False))

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
        cfg_h, _ = std_world(os.path.join(tmp, "nostate"),
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

        # -- hostile logs cannot make the file fail its own schema ----------------------------
        cfg_x = hostile_world(os.path.join(tmp, "hostile"))
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

        # -- the runs the plan's limit cut off (KIT-470) ---------------------------------------
        U = kit.U
        limit_logs = os.path.join(tmp, "limits", "disp", "logs")

        def rejected(window, resets, status="rejected"):
            return [(10, {"status": status, "rateLimitType": window, "utilization": 1.0,
                          "resetsAt": resets})]
        for folder, session, start, kw in (
                ("ENG-71", 71, "2026-11-03T06:00:00Z", dict(
                    total=0.5, error=True, events=rejected("five_hour", U("2026-11-03T14:00:00Z")))),
                ("ENG-72", 72, "2026-11-03T08:00:00Z", dict(
                    events=rejected("seven_day", U("2026-11-06T09:00:00Z")))),
                ("slack-Ev9", 73, "2026-11-03T09:00:00Z", dict(
                    total=0.5, error=True, events=[(10, {"status": "rejected",
                                                         "rateLimitType": "five_hour"})])),
                ("ENG-74", 74, "2026-11-02T10:00:00Z", dict(
                    crash=True, events=rejected("overage", LEAK))),
                ("ENG-75", 75, "2026-11-03T07:00:00Z", dict(
                    total=0.5, events=rejected("five_hour", U("2026-11-03T14:00:00Z")))),
                ("ENG-76", 76, "2026-11-03T07:30:00Z", dict(
                    total=0.5, error=True, events=rejected(
                        "five_hour", U("2026-11-03T14:00:00Z"), "allowed_warning"))),
                ("ENG-77", 77, "2026-10-25T13:00:00Z", dict(
                    total=0.5, error=True, events=rejected("five_hour", U("2026-11-03T14:00:00Z"))))):
            kit.write_run(limit_logs, folder, sid(session), U(start), **kw)
        cfg_c = {"dispatcher_home": os.path.join(tmp, "limits", "disp"), "stage_e_state_dir": "",
                 "stage_a_state_dir": "", "reviews_team_key": "REV",
                 "out": os.path.join(tmp, "limits", "out", "usage.json"), "lookback_days": 8}
        STOPS = [
            {"ticket": None, "kind": "tech_lead", "ended_at": "2026-11-03T09:01:00Z",
             "window": "five_hour", "resets_at": None},
            {"ticket": "ENG-72", "kind": "coding", "ended_at": "2026-11-03T08:00:30Z",
             "window": "seven_day", "resets_at": "2026-11-06T09:00:00Z"},
            {"ticket": "ENG-71", "kind": "coding", "ended_at": "2026-11-03T06:01:00Z",
             "window": "five_hour", "resets_at": "2026-11-03T14:00:00Z"},
            {"ticket": "ENG-74", "kind": "coding", "ended_at": "2026-11-02T10:01:00Z",
             "window": "overage", "resets_at": None}]

        def stop_facts(**patched):
            """The file's limit_stops and the count past the cap for the limits world, with
            module names patched for a mutant."""
            saved = dict((k, globals()[k]) for k in patched if k in globals())
            globals().update(patched)
            try:
                code_c, _ = go(cfg_c)
            finally:
                for k in patched:
                    if k in saved:
                        globals()[k] = saved[k]
                    else:
                        del globals()[k]
            doc_c = written(cfg_c)
            return (code_c, dig(doc_c, "limit_stops"), dig(doc_c, "limit_stops_over_cap"))
        code, got, over = stop_facts()
        doc_c = written(cfg_c)
        check("the runs a rejected window and then an error (or no result) cut off are listed "
              "newest first, a chat with no ticket, a reset that is no time or none null; a "
              "rejection then a success, a warning, and a run outside the lookback are not",
              (code, got, over), (EXIT_OK, STOPS, 0))
        check("…the file conforms, and no text of the log is in it",
              (check_schemas.document_problems(doc_c, SCHEMA_NAME), LEAK in text_of(cfg_c)),
              ([], False))
        check("…and the summary says how many",
              any(s.startswith("  stopped by the plan's limit: 4 run(s)") for s in go(cfg_c)[1]),
              True)
        check("no run was stopped in the standard world, though one heard a rejection and went on",
              (dig(doc, "limit_stops"), dig(doc, "limit_stops_over_cap")), ([], 0))
        check("a pass that could not read the logs knows no stops: none listed, the count null",
              (dig(doc_f, "limit_stops"), dig(doc_f, "limit_stops_over_cap")), ([], None))
        code, got, over = stop_facts(LIMIT_STOPS_CAP=2)
        check("past the cap the newest are listed and the count left out is said, not dropped",
              (code, got, over), (EXIT_OK, STOPS[:2], 2))
        schema_l = check_schemas.load_schema(SCHEMA_NAME)
        check("the cap, the kinds and the windows are the schema's",
              (LIMIT_STOPS_CAP == schema_l["properties"]["limit_stops"]["maxItems"],
               list(KINDS) == schema_l["$defs"]["limit_stop"]["properties"]["kind"]["enum"],
               sorted(usage_runs.WINDOW_TYPES + (WORD_OTHER,))
               == sorted(schema_l["$defs"]["limit_stop"]["properties"]["window"]["enum"])),
              (True, True, True))
        real_sorted = sorted
        real_summarize = summarize

        def uncapped(cfg_u, scanned_u, now_u, started_u):
            """summarize, as if the runaway cap were not there."""
            globals()["LIMIT_STOPS_CAP"] = 10 ** 6
            try:
                return real_summarize(cfg_u, scanned_u, now_u, started_u)
            finally:
                globals()["LIMIT_STOPS_CAP"] = 2
        globals()["LIMIT_STOPS_CAP"] = 2
        try:
            capless = stop_facts(summarize=uncapped)
        finally:
            globals()["LIMIT_STOPS_CAP"] = 200
        check("mutant: a cap that cuts nothing is caught", capless != (EXIT_OK, STOPS[:2], 2), True)
        for label, patched in (
                ("a chat thread named as a ticket", dict(TICKET_RE=re.compile(".*"))),
                ("a list oldest first", dict(sorted=lambda it, key=None, reverse=False:
                                             real_sorted(it, key=key))),
                ("a run outside the lookback listed", dict(local_date=lambda t: "2026-11-03"))):
            check("mutant: %s is caught" % label,
                  stop_facts(**patched) != (EXIT_OK, STOPS, 0), True)
        real_scan = scan

        def mark_every_run(cfg_m, now_m):
            scanned_m = real_scan(cfg_m, now_m)
            for r in scanned_m["runs"] or []:
                r["limit_stop"] = {"window": "five_hour", "resets_at": None}
            return scanned_m
        check("mutant: every run marked as stopped is caught",
              stop_facts(scan=mark_every_run) != (EXIT_OK, STOPS, 0), True)
        check("…and with every mutant undone the list is as it was",
              stop_facts(), (EXIT_OK, STOPS, 0))

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


    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
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
        check("KIT-596 the reviews team key is optional, and when given is a team key",
              ["reviews_team_key" in refused(d) for d in (
                  dict((k, v) for k, v in example.items() if k != "reviews_team_key"),
                  dict(example, reviews_team_key="rev"), dict(example, reviews_team_key=5))],
              [False, True, True])
        check("KIT-596 a review on a work team is told by the review poller's record",
              (workspace_kind("ENG-12", {"reviews_team_key": ""}, set(), {"ENG-12"}),
               workspace_kind("ENG-13", {"reviews_team_key": ""}, set(), {"ENG-12"}),
               workspace_kind("REV-4", {"reviews_team_key": "REV"}, set())),
              ("review", "coding", "review"))
        check("lookback_days is a whole number of days from 1 to 31",
              ["lookback_days" in refused(dict(example, lookback_days=v))
               for v in (0, 32, True, 1.5, "8")]
              + [refused(dict(example, lookback_days=v)) for v in (1, 31)],
              [True] * 5 + ["", ""])
        check("a relative path is refused, for the out file and for each source",
              ["absolute" in refused(dict(example, **{k: "some/where"}))
               for k in ("out", "dispatcher_home", "stage_e_state_dir", "stage_a_state_dir",
                         "judge_file")],
              [True] * 5)
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
                                          stage_a_state_dir="", judge_file="")))[k]
               for k in ("stage_e_state_dir", "stage_a_state_dir", "judge_file")]
              + ["absolute" in refused(dict(example, dispatcher_home=""))], ["", "", "", True])
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
        with Watch() as watch:
            code, said = go(cfg_n)
        check("it writes nothing but its own file: every path it writes is under the out "
              "folder, and the file is among them",
              (bool(watch.wrote),
               all(p == out_dir or p.startswith(out_dir + os.sep) for p in watch.wrote),
               os.path.realpath(cfg_n["out"]) in watch.wrote), (True, True, True))
        check("it opens no socket, and the pass still ran", (watch.net, code), ([], EXIT_OK))
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

        # -- the words the reader holds are the schema's --------------------------------------
        schema = check_schemas.load_schema(SCHEMA_NAME)
        defs, props = schema["$defs"], schema["properties"]
        check("the closed lists the reader holds are the schema's: kinds, sign-in words, usage "
              "windows and their status words",
              (list(COST_KINDS) == defs["kind_totals"]["required"],
               sorted(usage_runs.KEY_SOURCES + (usage_runs.KEY_OTHER, KEY_UNSEEN))
               == sorted(props["sign_in"]["properties"]["counts"]["propertyNames"]["enum"]),
               sorted(usage_runs.WINDOW_TYPES + (WORD_OTHER,))
               == sorted(props["status"]["properties"]["windows"]["properties"]),
               sorted(usage_runs.STATUS_WORDS + (WORD_OTHER,))
               == sorted(defs["window_event"]["properties"]["status"]["enum"])),
              (True, True, True, True))


if __name__ == "__main__":
    sys.exit(main())
