#!/usr/bin/env python3
"""The daily stand-up (KIT-349): a morning rundown and an afternoon wrap in the owner's DM,
worded from the status file (contract §16), the way a team's stand-up would be.

    python3 scripts/pipeline_standup.py preview --kind morning|afternoon [--config P] [--at T]
    python3 scripts/pipeline_standup.py run [--config PATH] [--dry-run]
    python3 scripts/pipeline_standup.py --selftest

THE MORNING says first what needs the owner's OK, each as a clear ask: what it is, what it
holds up, and the exact reply that approves it, or where to act when no reply can (a merge,
a label). With nothing to ask it says so in one line. Then what is in progress, then what
moved since the last morning. THE AFTERNOON says what shipped today, each piece's start and
finish, and then what still waits on the owner.

Everything comes from the status file's fields. A stale, missing or unreadable file is said
instead of old news, a source the status job could not read is named, and a quiet day says
what was checked.

THE WRITER (pipeline_voice) words the sections after the asks, from the same fields, and the
plain text goes out when it is off or fails a check (the owner's decision 2). The asks are put
ahead of its text and never pass through it, so each reply stays exact.

WHO POSTS.  The bridge, from its own one-minute loop, through `bridge_tick`: it asks `due`,
`compose`s the text, `claim`s the rundown (saved BEFORE the post, so one that cannot be
recorded is never sent and one that is sent is never sent twice), posts the text through the
pass's own DM path, so the post is the bridge's own, then calls `mark_sent` and `save_record`,
or `release` if the post failed. While the stand-up is on (`is_on`), the bridge skips each
piece's start message: the afternoon wrap lists it. `run` does the same by hand, through the
bridge's Slack client, for trying it out; the bridge has no record of that post.

WHEN.  `rundown_times` is the one place the two times and the days come from: the settings
today (Monday to Saturday by default, Sundays off), MVP 2's working window later (its days, its
start and its end). A rundown is due once a day, from its time until `late_by_minutes` after
it. Later than that it is missed, and never sent as old news.

THE RECORD (`record_file`) holds, for each kind, the last day it went out, when, its channel
and ts, and whether the writer or the plain text worded it. The missed-rundown check (KIT-286)
and the tech lead's wake (KIT-356) read it.

ROOM FOR MVP 2.  Its "Needs your OK today" items join the asks through ASK_SOURCES, each with
how much of today's plan it holds up. "Today's plan" goes first in MORNING_SECTIONS, and
"spend against the day's limit" into AFTERNOON_SECTIONS.

OFF BY DEFAULT: no settings file, or "on": false, and nothing is ever due.

EXIT CODES of `run` and `preview` (contract §13):
  0  a rundown went out from a current status file, or none was due (it says which, off too)
  3  a rundown said the status file was stale, missing or partly read, or one was missed
  1  a rundown was due and could not be sent
  2  the settings are wrong
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pipeline_bridge_config as bridge_config  # noqa: E402
import pipeline_bridge_transport as transport  # noqa: E402
import pipeline_notify_local as notify  # noqa: E402
import pipeline_status_local as status_job  # noqa: E402
import pipeline_voice as voice  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3

SETTINGS_SCHEMA = "pipeline-standup-settings/1"
RECORD_SCHEMA = "pipeline-standup-record/1"
STATUS_SCHEMA = "pipeline-status/1"
DEFAULT_SETTINGS_FILE = "~/.stage-e/standup.json"
DEFAULTS = {
    "on": False,
    # Local times, 24-hour, and the days it posts. MVP 2's working window replaces all three
    # (rundown_times): Monday to Saturday by default, Sundays off.
    "morning_at": "09:00",
    "afternoon_at": "15:00",
    "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "status_file": "~/.stage-e/status/pipeline-status.json",
    "bridge_config": "~/.stage-e/bridge.json",
    "record_file": "~/.stage-e/state/standup-record.json",
    # The status job writes every 15 minutes, so a file older than this is old news.
    "stale_after_minutes": 60,
    # A rundown is due from its time until this much later; after that it is missed.
    "late_by_minutes": 180,
}
KINDS = ("morning", "afternoon")
# Day names in datetime.weekday() order.
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# A runaway cap on one section's lines, far above a real day; the rest are counted.
MAX_LINES = 40
SOURCE_WORDS = {"tracker": "Linear", "github": "GitHub", "reviews": "The review records"}
_CLOCK_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]*-[0-9]+\b")


class StandupError(Exception):
    """Settings or a record that cannot be used, said in words."""


# --------------------------------------------------------------------------- #
# Settings and times
# --------------------------------------------------------------------------- #
def _clock(value):
    m = _CLOCK_RE.match(value) if isinstance(value, str) else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def validate_settings(raw, path="(settings)"):
    """The settings with every default filled in, or StandupError naming every problem."""
    if not isinstance(raw, dict):
        raise StandupError("%s: the stand-up's settings must be a JSON object" % path)
    problems = []
    if raw.get("schema") != SETTINGS_SCHEMA:
        problems.append("'schema' must be %r" % SETTINGS_SCHEMA)
    unknown = sorted(set(raw) - set(DEFAULTS) - {"schema"})
    if unknown:
        problems.append("unknown keys: %s" % ", ".join(unknown))
    s = dict(DEFAULTS)
    s.update((k, v) for k, v in raw.items() if k in DEFAULTS)
    if not isinstance(s["on"], bool):
        problems.append("'on' must be true or false")
    for key in ("morning_at", "afternoon_at"):
        if _clock(s[key]) is None:
            problems.append("'%s' must be a time of day as HH:MM, 24-hour: %r" % (key, s[key]))
    days = s["days"]
    if (not isinstance(days, list) or not days or len(set(map(str, days))) != len(days)
            or any(d not in DAY_NAMES for d in days)):
        problems.append("'days' must name each day it posts once, from %s: %r"
                        % (", ".join(DAY_NAMES), days))
    for key in ("status_file", "bridge_config", "record_file"):
        if not isinstance(s[key], str) or not os.path.isabs(os.path.expanduser(s[key])):
            problems.append("'%s' must be an absolute path (~/ is allowed): %r" % (key, s[key]))
    for key, top in (("stale_after_minutes", 1440), ("late_by_minutes", 720)):
        if isinstance(s[key], bool) or not isinstance(s[key], int) or not 1 <= s[key] <= top:
            problems.append("'%s' must be a whole number of minutes from 1 to %d: %r"
                            % (key, top, s[key]))
    morning, afternoon = _clock(s["morning_at"]), _clock(s["afternoon_at"])
    if morning and afternoon:
        gap = (afternoon[0] - morning[0]) * 60 + afternoon[1] - morning[1]
        if gap <= 0:
            problems.append("'afternoon_at' must come after 'morning_at'")
        elif isinstance(s["late_by_minutes"], int) and s["late_by_minutes"] >= gap:
            problems.append("'late_by_minutes' must be less than the %d minutes between the "
                            "two times, or a late morning would go out in the afternoon" % gap)
    if problems:
        raise StandupError("%s: %s" % (path, "; ".join(problems)))
    s["path"] = path
    s["why_off"] = None if s["on"] else "\"on\" is false in %s" % path
    return s


def load_settings(path=None):
    """The settings. No file is the default, and the default is off, said by name."""
    path = os.path.expanduser(path or DEFAULT_SETTINGS_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return dict(DEFAULTS, path=path, why_off="there is no settings file at %s" % path)
    except (OSError, ValueError) as exc:
        raise StandupError("the stand-up's settings at %s could not be read: %s" % (path, exc))
    return validate_settings(raw, path)


def _local(now):
    """`now` on the Mac's own clock, aware: an epoch, or a datetime (naive is local)."""
    if isinstance(now, datetime):
        return now.astimezone()
    return datetime.fromtimestamp(float(now)).astimezone()


def rundown_times(settings, day):
    """{kind: aware local datetime} on `day`, or {} on a day it does not post. The ONE place
    the times and days come from: the settings today; MVP 2's working window later, its days,
    its start and its end."""
    if DAY_NAMES[day.weekday()] not in settings["days"]:
        return {}
    out = {}
    for kind in KINDS:
        hour, minute = _clock(settings[kind + "_at"])
        out[kind] = datetime(day.year, day.month, day.day, hour, minute).astimezone()
    return out


def _sent_on(record, kind, day):
    return ((record or {}).get(kind) or {}).get("day") == day.isoformat()


def due(now, settings, record):
    """The kind due now, "morning" or "afternoon", or None. Each is due once a day, from its
    time until late_by_minutes after it, until it is sent. Off, nothing is ever due."""
    if not settings.get("on"):
        return None
    now = _local(now)
    window = timedelta(minutes=settings["late_by_minutes"])
    for kind, at in rundown_times(settings, now.date()).items():
        if not _sent_on(record, kind, now.date()) and at <= now <= at + window:
            return kind
    return None


def missed(now, settings, record):
    """The kinds whose window passed today with nothing sent. They are said, never sent."""
    if not settings.get("on"):
        return []
    now = _local(now)
    window = timedelta(minutes=settings["late_by_minutes"])
    return [kind for kind, at in rundown_times(settings, now.date()).items()
            if not _sent_on(record, kind, now.date()) and now > at + window]


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #
def load_record(settings):
    """The record, or an empty one when there is none yet. One that cannot be read raises:
    without it nobody can tell whether today's rundown already went out."""
    path = os.path.expanduser(settings["record_file"])
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"schema": RECORD_SCHEMA}
    except (OSError, ValueError) as exc:
        raise StandupError("the stand-up's record at %s could not be read: %s" % (path, exc))
    if not isinstance(data, dict) or data.get("schema") != RECORD_SCHEMA:
        raise StandupError("%s is not a %s record" % (path, RECORD_SCHEMA))
    return data


def save_record(settings, record):
    """Write the record whole, or not at all: a temp file beside it, then a rename."""
    path = os.path.expanduser(settings["record_file"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".standup-record.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def claim(kind, record, settings, now):
    """Record, and save, that `kind` goes out today, BEFORE it is posted. A record that cannot
    be saved raises, and then nothing may be posted: a rundown that went out unrecorded would
    go out again on every pass until its window closed. Returns what the record held before,
    for `release`."""
    previous = record.get(kind)
    now = _local(now)
    record["schema"] = RECORD_SCHEMA
    record[kind] = {"day": now.date().isoformat(), "claimed_at": _iso(now)}
    save_record(settings, record)
    return previous


def release(kind, record, settings, previous):
    """Undo a claim after a post that failed, so the rundown stays due. If this save fails
    too, the claim stands: the rundown is missed rather than sent twice."""
    if previous is None:
        record.pop(kind, None)
    else:
        record[kind] = previous
    save_record(settings, record)


def mark_sent(kind, channel, ts, record, now, composed=None):
    """Record that `kind` went out now, where (channel, ts) and how it was worded, over its
    claim. The caller saves the record (save_record)."""
    now, composed = _local(now), composed or {}
    record["schema"] = RECORD_SCHEMA
    record[kind] = {"day": now.date().isoformat(), "sent_at": _iso(now), "channel": channel,
                    "ts": ts, "by": composed.get("by"), "why": composed.get("why"),
                    "status_written_at": composed.get("status_written_at"),
                    "problem": composed.get("problem")}
    return record


# --------------------------------------------------------------------------- #
# Reading the status file
# --------------------------------------------------------------------------- #
def _parse(stamp):
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else None


def _iso(when):
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hm(when, now):
    """A time as the owner reads it: 14:02 today, "yesterday 16:02", else "Mon 16:02"."""
    local = when.astimezone()
    if local.date() == now.date():
        return local.strftime("%H:%M")
    if local.date() == (now - timedelta(days=1)).date():
        return "yesterday " + local.strftime("%H:%M")
    return local.strftime("%a %H:%M")


def _ago(delta):
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 120:
        return _count(minutes, "minute") + " ago"
    if minutes < 48 * 60:
        return _count(minutes // 60, "hour") + " ago"
    return _count(minutes // 1440, "day") + " ago"


def _count(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def read_status(settings, now):
    """(status, None), or (None, what is wrong in words). Old news is never returned."""
    path = os.path.expanduser(settings["status_file"])
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, "there's no status file at %s" % path
    except ValueError:
        return None, "the status file at %s isn't readable JSON" % path
    except OSError as exc:
        return None, "the status file at %s can't be read (%s)" % (path, exc.strerror or exc)
    if not isinstance(data, dict) or data.get("schema") != STATUS_SCHEMA:
        return None, "the file at %s isn't a status file I can read" % path
    written = _parse(data.get("written_at"))
    if written is None:
        return None, "the status file at %s doesn't say when it was written" % path
    if now - written > timedelta(minutes=settings["stale_after_minutes"]):
        return None, "the status file was last written at %s, %s" % (
            _hm(written, now), _ago(now - written))
    return data, None


def source_notes(status, now):
    """(lines, blind): a line for each source the status job did not read whole, and
    whether Linear went unread, which leaves nothing to say about any plan."""
    lines, blind = [], False
    for name in ("tracker", "github", "reviews"):
        src = (status.get("sources") or {}).get(name) or {}
        state = src.get("state")
        if state not in ("could-not-read", "partly-read"):
            continue
        when = _parse(src.get("read_at")) or _parse(status.get("written_at"))
        at = _hm(when, now) if when else "its last pass"
        reason = (" (%s)" % src["reason"]) if src.get("reason") else ""
        who = SOURCE_WORDS[name]
        if state == "could-not-read" and name == "tracker":
            blind = True
            lines.append("%s couldn't be read at %s%s, so I can't say what needs you or what's "
                         "in progress." % (who, at, reason))
        elif state == "could-not-read":
            lines.append("%s couldn't be read at %s%s, so some of this may be behind."
                         % (who, at, reason))
        else:
            lines.append("%s was read only in part at %s%s, so some of this may be behind."
                         % (who, at, reason))
    return lines, blind


# --------------------------------------------------------------------------- #
# What needs the owner
# --------------------------------------------------------------------------- #
class Ask(object):
    """One thing that needs the owner's OK: what it is, what it holds up, and either the
    exact reply that approves it or where to act when no reply can. MVP 2's orchestrator adds
    its "Needs your OK today" items as Asks through ASK_SOURCES, with how much of today's plan
    each holds up in `holds_up`."""

    def __init__(self, what, holds_up, reply="", where=""):
        self.what, self.holds_up, self.reply, self.where = what, holds_up, reply, where

    def line(self):
        act = ("To approve, reply `%s`." % self.reply) if self.reply else self.where
        return "%s. It holds up %s. %s" % (self.what, self.holds_up, act)


def _closed(item):
    return item.get("state_type") in ("completed", "canceled")


def _plan_progress(plan):
    pieces = plan.get("pieces") or []
    done = sum(1 for p in pieces if p.get("state_type") == "completed")
    return "plan %s (%d of %s done)" % (plan["id"], done, _count(len(pieces), "piece"))


def _ready_to_merge(pr, reviews_off):
    review = (pr.get("review") or {}).get("state")
    return (pr.get("draft") is not True
            and (pr.get("checks") or {}).get("result") == "passing"
            and pr.get("mergeable") == "mergeable"
            and (review in ("clean", "notes")
                 or (reviews_off and review in ("none", "not-reviewed"))))


def asks_from_status(status, settings, now):
    """The status file's own asks: a plan waiting for approval, a piece stuck on the owner,
    a pull request waiting on a person's check, and one ready to merge."""
    asks = []
    reviews_off = ((status.get("sources") or {}).get("reviews") or {}).get("state") == "off"
    for plan in status.get("plans") or []:
        pieces = plan.get("pieces") or []
        if _closed(plan) or not pieces:
            continue
        if (plan.get("state_type") in ("triage", "backlog")
                and not any(p.get("started_at") for p in pieces)):
            asks.append(Ask("Plan %s, %s, waits for your approval" % (plan["id"], plan["title"]),
                            "its %s" % _count(len(pieces), "piece"),
                            reply="approve %s" % plan["id"]))
            continue
        for piece in pieces:
            if _closed(piece):
                continue
            if set(piece.get("flags") or ()) & {"agent:needs-human", "agent:blocked"}:
                where = "Answer it in its thread here, or on the ticket"
                asks.append(Ask("%s, %s, is stuck until you answer it" % (
                    piece["id"], piece["title"]), _plan_progress(plan),
                    where=where + (": " + piece["url"] if piece.get("url") else ".")))
            for pr in piece.get("prs") or []:
                if pr.get("state") != "open":
                    continue
                name = "Pull request #%d for %s, %s," % (pr["number"], piece["id"], piece["title"])
                waiting = [c.get("name") for c in (pr.get("checks") or {}).get("required") or ()
                           if c.get("waits_on_person") and c.get("state") != "passing"]
                if waiting:
                    asks.append(Ask("%s waits on you for %s" % (name, " and ".join(waiting)),
                                    "its merge, and so %s" % _plan_progress(plan),
                                    where="See it on GitHub: %s" % pr["url"]))
                elif _ready_to_merge(pr, reviews_off):
                    asks.append(Ask("%s is ready to merge" % name, _plan_progress(plan),
                                    where="Merge it on GitHub: %s" % pr["url"]))
    return asks


# Where the asks come from, in order. MVP 2 appends its "Needs your OK today" source here.
ASK_SOURCES = [asks_from_status]


def gather_asks(status, settings, now):
    asks = []
    for source in ASK_SOURCES:
        asks.extend(source(status, settings, now) or ())
    return asks


# --------------------------------------------------------------------------- #
# The sections after the asks
# --------------------------------------------------------------------------- #
def _events(status):
    """Every dated change in the file: (when, what, words)."""
    out = []
    for plan in status.get("plans") or []:
        if _parse(plan.get("created_at")):
            out.append((_parse(plan["created_at"]), "plan-filed",
                        "plan %s was filed: %s" % (plan["id"], plan["title"])))
        if _parse(plan.get("completed_at")):
            out.append((_parse(plan["completed_at"]), "plan-finished",
                        "plan %s finished: %s" % (plan["id"], plan["title"])))
        for piece in plan.get("pieces") or []:
            label = "%s, %s" % (piece["id"], piece["title"])
            if _parse(piece.get("started_at")):
                out.append((_parse(piece["started_at"]), "started", label + ", started"))
            if _parse(piece.get("completed_at")):
                out.append((_parse(piece["completed_at"]), "finished", label + ", finished"))
            if _parse(piece.get("canceled_at")):
                out.append((_parse(piece["canceled_at"]), "canceled", label + ", was canceled"))
            for pr in piece.get("prs") or []:
                if _parse(pr.get("opened_at")):
                    out.append((_parse(pr["opened_at"]), "opened", "pull request #%d opened, "
                                "for %s" % (pr["number"], piece["id"])))
                if _parse(pr.get("merged_at")):
                    out.append((_parse(pr["merged_at"]), "merged", "pull request #%d merged, "
                                "for %s" % (pr["number"], label)))
    out.sort(key=lambda e: e[0])
    return out


def _capped(lines):
    if len(lines) <= MAX_LINES:
        return lines
    return lines[:MAX_LINES] + ["and %d more" % (len(lines) - MAX_LINES)]


def _pr_words(pr):
    n, state = pr.get("number"), pr.get("state")
    if state != "open":
        return "pull request #%d %s" % (n, {"merged": "merged", "closed": "closed"}.get(
            state, "in a state GitHub didn't say"))
    bits = ["pull request #%d %s" % (n, "is a draft" if pr.get("draft") else "is open")]
    bits.append({"passing": "checks pass", "failing": "checks fail",
                 "pending": "checks are running", "waiting": "checks wait on you",
                 "none": "no required checks"}.get((pr.get("checks") or {}).get("result"),
                                                    "checks not known"))
    review = (pr.get("review") or {}).get("state")
    if review in ("clean", "notes", "findings", "partial", "not-reviewed"):
        bits.append({"clean": "review found nothing", "notes": "review left notes",
                     "findings": "review found problems", "partial": "review was partial",
                     "not-reviewed": "not reviewed yet"}[review])
    if pr.get("mergeable") == "conflicting":
        bits.append("it has a conflict")
    return ", ".join(bits)


def section_in_progress(status, now, since):
    lines = []
    for plan in status.get("plans") or []:
        for piece in plan.get("pieces") or []:
            if piece.get("state_type") != "started":
                continue
            prs = piece.get("prs") or []
            work = "; ".join(_pr_words(pr) for pr in prs) or "no pull request yet"
            lines.append("%s, %s (plan %s): %s" % (piece["id"], piece["title"], plan["id"], work))
    return ("In progress:", _capped(lines)) if lines else None


def section_moved(status, now, since):
    lines = ["%s %s" % (_hm(at, now), words) for at, _k, words in _events(status)
             if since < at <= now]
    # After a day off, "yesterday" would be wrong: Monday's says "since Saturday".
    when = ("yesterday" if since.astimezone().date() >= (now - timedelta(days=1)).date()
            else since.astimezone().strftime("%A"))
    return ("Moved since %s:" % when, _capped(lines)) if lines else None


def section_shipped(status, now, since):
    lines = ["%s %s" % (_hm(at, now), words) for at, kind, words in _events(status)
             if kind in ("merged", "plan-finished") and since <= at <= now]
    return ("Shipped today:", _capped(lines)) if lines else None


def section_started_finished(status, now, since):
    """Each piece's start and finish today. One that finished today says when it started,
    whatever the day; one that started today and is still going says it started."""
    rows = []
    for plan in status.get("plans") or []:
        for piece in plan.get("pieces") or []:
            label = "%s, %s" % (piece["id"], piece["title"])
            started, finished = _parse(piece.get("started_at")), _parse(piece.get("completed_at"))
            if finished and since <= finished <= now:
                rows.append((finished, "%s %s, finished%s" % (
                    _hm(finished, now), label,
                    (" (started %s)" % _hm(started, now)) if started else "")))
            elif started and since <= started <= now:
                rows.append((started, "%s %s, started" % (_hm(started, now), label)))
    lines = [line for _at, line in sorted(rows, key=lambda r: r[0])]
    return ("Started and finished today:", _capped(lines)) if lines else None


# The sections after the asks, in order, each (title, lines) or None for nothing to say.
# MVP 2 puts "today's plan" first in the morning, and "spend against the day's limit" in the
# afternoon.
MORNING_SECTIONS = [section_in_progress, section_moved]
AFTERNOON_SECTIONS = [section_shipped, section_started_finished]


def _since(kind, now, record):
    """Where "since yesterday" starts: the last morning that went out, if within three
    days, else 24 hours back. The afternoon's day starts at midnight."""
    if kind == "afternoon":
        return datetime(now.year, now.month, now.day).astimezone()
    last = _parse(((record or {}).get("morning") or {}).get("sent_at"))
    if last and now - timedelta(days=3) <= last < now:
        return last
    return now - timedelta(hours=24)


def _slack_safe(text):
    """Slack's three control characters escaped, so nothing in a rundown can be read as a
    mention, a link or a broadcast: it shows as written. The status file's titles cannot hold
    them (contract §16), so this guards the writer's words and anything added later."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _checked(status, now):
    counts = status.get("counts") or {}
    return "I checked %s, %s and %s at %s." % (
        _count(counts.get("plans", 0), "plan"), _count(counts.get("pieces", 0), "piece"),
        _count(counts.get("pull_requests", 0), "pull request"),
        _hm(_parse(status["written_at"]), now))


def compose(kind, now, settings, record=None, bridge_cfg=None, writer=None):
    """The rundown `kind` for `now`, as a dict:
      text    what to post: the opening, the asks, then the writer's words for the rest
      plain   the same with the plain words throughout, for a caller that falls back
      by, why, cost_usd   what the writer did ("model" or "fallback", and why)
      problem             what the rundown had to say instead of news, or None
      status_written_at   the status file's time, or None
    The writer runs only when both `bridge_cfg` (its settings) and `writer` are given."""
    if kind not in KINDS:
        raise StandupError("a rundown is %s, not %r" % (" or ".join(KINDS), kind))
    now = _local(now)
    greeting = "Good morning." if kind == "morning" else "Afternoon wrap."
    status, problem = read_status(settings, now)
    if status is None:
        plain = _slack_safe("%s There's no stand-up this %s: %s. I won't pass on old news." % (
            greeting, kind, problem))
        return {"kind": kind, "text": plain, "plain": plain, "by": "fallback",
                "why": "no current status file", "cost_usd": None, "problem": problem,
                "status_written_at": None}
    written = _parse(status["written_at"])
    lead = ["%s Here's %s, from the status file written at %s." % (
        greeting, "today's stand-up" if kind == "morning" else "what happened today",
        _hm(written, now))]
    notes, blind = source_notes(status, now)
    lead.extend(notes)
    result = {"kind": kind, "problem": " ".join(notes) or None,
              "status_written_at": status["written_at"]}
    if blind:
        plain = _slack_safe("\n\n".join(lead))
        result.update(text=plain, plain=plain, by="fallback", why="Linear could not be read",
                      cost_usd=None)
        return result
    asks = gather_asks(status, settings, now)
    head = "Needs your OK:" if kind == "morning" else "Still waiting on you:"
    asks_block = (head + "\n" + "\n".join("%d. %s" % (i, a.line())
                                          for i, a in enumerate(asks, 1))) if asks else (
        "Nothing needs your OK." if kind == "morning" else "Nothing is waiting on you.")
    since = _since(kind, now, record)
    funcs = MORNING_SECTIONS if kind == "morning" else AFTERNOON_SECTIONS
    sections = [s for s in (f(status, now, since) for f in funcs) if s]
    if sections:
        narrative = "\n\n".join(title + "\n" + "\n".join("- " + line for line in lines)
                                for title, lines in sections)
    elif kind == "morning":
        narrative = "Nothing is in progress, and nothing moved since yesterday. " + _checked(
            status, now)
    else:
        narrative = "Nothing shipped today, and no piece started or finished. " + _checked(
            status, now)
    if writer is not None and bridge_cfg is not None:
        facts = {"rundown": kind, "status_written_at": _hm(written, now),
                 "sections": [{"title": t, "items": lines} for t, lines in sections],
                 "checked": status.get("counts") or {}}
        keep = sorted(set(_TICKET_RE.findall(narrative))) + sorted(voice.fact_links([narrative]))
        try:
            worded = writer(bridge_cfg, "standup-" + kind, facts, fallback=narrative,
                            keep=keep, now=now.timestamp())
        except Exception as exc:  # noqa: BLE001 — a fault in the writer never costs a rundown
            worded = {"text": narrative, "by": "fallback",
                      "why": "the writer failed (%s)" % type(exc).__name__, "cost_usd": None}
    else:
        worded = {"text": narrative, "by": "fallback", "why": "the writer was not asked",
                  "cost_usd": None}
    order = (lambda body: lead + [asks_block, body]) if kind == "morning" else (
        lambda body: lead + [body, asks_block])
    result.update(text=_slack_safe("\n\n".join(order(worded.get("text") or narrative))),
                  plain=_slack_safe("\n\n".join(order(narrative))), by=worded.get("by"),
                  why=worded.get("why"), cost_usd=worded.get("cost_usd"))
    return result


# --------------------------------------------------------------------------- #
# The bridge's hook (KIT-349)
# --------------------------------------------------------------------------- #
def is_on(path=None):
    """True when the settings turn the stand-up on. Settings that cannot be read count as
    off here, so each piece's start message still goes out; `bridge_tick` says why."""
    try:
        return bool(load_settings(path)["on"])
    except StandupError:
        return False


def bridge_tick(p):
    """The bridge's one call each pass. `p` is the bridge's Pass. When a rundown is due, it
    is composed, claimed (the record saved first), posted through `p.post` into the owner's
    DM (the first of dm_user_ids) with no kind, so the post is the bridge's own and the
    writer does not run twice, then recorded. Returns (outcome, problem): the outcome in a
    word, and a sentence for the end of the pass when something could not be done, else
    None. Settings or a record it cannot use raise StandupError."""
    settings = load_settings()
    if not settings["on"]:
        return "off", None
    record = load_record(settings)
    kind = due(p.now, settings, record)
    if kind is None:
        return "not-due", None
    users = p.cfg.get("dm_user_ids") or []
    channel = (p.state.get("dms") or {}).get(users[0]) if users else None
    if not isinstance(channel, str) or not channel:
        p.say("  stand-up: the %s rundown is due, and the owner's DM is not open yet; it "
              "stays due" % kind)
        return "no-dm", None
    # The writer as the pass's own post() would run it: its timeout cut to what is left of
    # the pass, none below WRITER_MIN_SECONDS, and none in a dry run.
    writer, wcfg = None, None
    if not p.dry and p.cfg.get("writer") is True:
        seconds = p.writer_seconds()
        if seconds >= bridge_config.WRITER_MIN_SECONDS:
            writer = p.writer
            wcfg = dict(p.cfg, writer_timeout_seconds=min(
                seconds, p.cfg["writer_timeout_seconds"]))
    composed = compose(kind, p.now, settings, record, wcfg, writer)
    text = composed["plain"] if p.mentions_chat_bot(composed["text"]) else composed["text"]
    if p.dry:
        p.say("  [dry-run] stand-up: would claim and post the %s rundown (%d chars)"
              % (kind, len(text)))
        return "dry-run", None
    try:
        previous = claim(kind, record, settings, p.now)
    except OSError as exc:
        return "could-not-claim", ("the stand-up's %s rundown was not posted: its record at %s "
                                   "could not be saved (%s)" % (kind, settings["record_file"],
                                                                exc))
    try:
        ts = p.post(channel, None, text)
    except transport.BridgeError as exc:
        try:
            release(kind, record, settings, previous)
            after = "it stays due"
        except OSError:
            after = "its record could not be put back, so it is missed today"
        return "could-not-send", ("the stand-up's %s rundown could not be posted (%s); %s"
                                  % (kind, exc, after))
    p.save()
    mark_sent(kind, channel, ts, record, p.now, composed)
    try:
        save_record(settings, record)
    except OSError as exc:
        return "sent-unrecorded", ("the stand-up's %s rundown went out, and its details could "
                                   "not be recorded (%s); it will not go out again today"
                                   % (kind, exc))
    p.counts["standup"] = p.counts.get("standup", 0) + 1
    p.say("  stand-up: posted the %s rundown in the owner's DM (worded by %s)"
          % (kind, composed["by"]))
    return "sent", None


# --------------------------------------------------------------------------- #
# By hand: run and preview
# --------------------------------------------------------------------------- #
def post_dm(bridge_cfg, text, client=None):
    """Post `text` as a message of its own in the owner's DM, through the bridge's own Slack
    client. Returns (channel, ts). The token stays in the env var the bridge config names."""
    users = bridge_cfg.get("dm_user_ids") or []
    if not users:
        raise StandupError("the bridge's config names no DM member (dm_user_ids)")
    if client is None:
        client = transport.SlackClient(bridge_cfg,
                                       bridge_config.credential(bridge_cfg, "slack_token_env"))
    channel = client.open_dm(users[0])
    if not channel:
        raise StandupError("Slack would not open the DM with %s" % users[0])
    return channel, client.post(channel, None, text)


def _next_due(now, settings, record):
    today = rundown_times(settings, now.date())
    for kind, at in today.items():
        if at > now and not _sent_on(record, kind, now.date()):
            return "the %s rundown is next, at %s" % (kind, at.strftime("%H:%M"))
    lead = "today's rundowns are done" if today else "it doesn't post today"
    for ahead in range(1, 8):
        day = now.date() + timedelta(days=ahead)
        times = rundown_times(settings, day)
        if times:
            return "%s; the next is %s morning, at %s" % (
                lead, "tomorrow's" if ahead == 1 else day.strftime("%A") + "'s",
                times["morning"].strftime("%H:%M"))
    return lead


def run(settings, dry_run=False, now=None, client=None, writer=None, bridge_cfg=None,
        out=None):
    """One pass by hand: send the rundown that is due, if one is, and say what happened."""
    out = out or sys.stdout
    now = _local(time.time() if now is None else now)
    if not settings.get("on"):
        out.write("OFF: the stand-up is off (%s). Nothing is sent.\n" % settings["why_off"])
        return EXIT_OK
    record = load_record(settings)
    code = EXIT_OK
    for kind in missed(now, settings, record):
        out.write("MISSED: the %s rundown was due at %s, and its window has passed. It is not "
                  "sent late.\n" % (kind, rundown_times(settings, now.date())[kind].strftime(
                      "%H:%M")))
        code = EXIT_PARTIAL
    kind = due(now, settings, record)
    if kind is None:
        out.write("nothing due: %s.\n" % _next_due(now, settings, record))
        return code
    try:
        if bridge_cfg is None:
            bridge_cfg = bridge_config.load_config(os.path.expanduser(settings["bridge_config"]))
    except (transport.BridgeError, OSError, ValueError) as exc:
        out.write("COULD NOT SEND the %s rundown: the bridge's config: %s\n" % (kind, exc))
        return EXIT_FAILED
    secrets = bridge_config.live_secrets(bridge_cfg)
    composed = compose(kind, now, settings, record, bridge_cfg,
                       voice.write if writer is None else writer)
    text = notify.redact(composed["text"], secrets)
    if dry_run:
        out.write(text + "\n\n[dry-run] would post the %s rundown (%d characters, worded by "
                  "%s). Nothing was sent or recorded.\n" % (kind, len(text), composed["by"]))
        return EXIT_PARTIAL if composed["problem"] else code
    try:
        previous = claim(kind, record, settings, now)
    except OSError as exc:
        out.write("COULD NOT SEND the %s rundown: its record at %s could not be saved (%s), "
                  "and a rundown that is not recorded would go out again.\n" % (
                      kind, settings["record_file"], exc))
        return EXIT_FAILED
    try:
        channel, ts = post_dm(bridge_cfg, text, client)
    except (transport.BridgeError, StandupError) as exc:
        try:
            release(kind, record, settings, previous)
            stays = "It stays due until %s." % (rundown_times(settings, now.date())[kind]
                                                 + timedelta(minutes=settings[
                                                     "late_by_minutes"])).strftime("%H:%M")
        except OSError:
            stays = "Its record could not be put back, so it is missed today."
        out.write("COULD NOT SEND the %s rundown: %s. %s\n" % (
            kind, notify.redact(str(exc), secrets), stays))
        return EXIT_FAILED
    mark_sent(kind, channel, ts, record, now, composed)
    try:
        save_record(settings, record)
    except OSError as exc:
        out.write("SENT the %s rundown (channel %s, ts %s), but its details could not be "
                  "recorded (%s). The record still says it went out today, so it will not go "
                  "out again.\n" % (kind, channel, ts, exc))
        return EXIT_FAILED
    out.write("SENT the %s rundown (%d characters, worded by %s%s).\n" % (
        kind, len(text), composed["by"], (": " + composed["why"]) if composed.get("why") else ""))
    if composed["problem"]:
        out.write("  it said: %s\n" % composed["problem"])
        return EXIT_PARTIAL
    return code


def preview(settings, kind, now=None, out=None):
    """The plain rundown as it would read now, printed. Nothing is sent, recorded or written."""
    out = out or sys.stdout
    now = _local(time.time() if now is None else now)
    try:
        record = load_record(settings)
    except StandupError as exc:
        out.write("note: %s; previewing as if nothing had gone out yet.\n" % exc)
        record = {}
    composed = compose(kind, now, settings, record)
    out.write(composed["plain"] + "\n")
    return EXIT_PARTIAL if composed["problem"] else EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    for name in ("run", "preview"):
        p = sub.add_parser(name)
        p.add_argument("--config", default=DEFAULT_SETTINGS_FILE)
        if name == "run":
            p.add_argument("--dry-run", action="store_true")
        else:
            p.add_argument("--kind", choices=KINDS, required=True)
            p.add_argument("--at", help="a local time to preview at, as 2026-11-04T09:00")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.cmd:
        parser.print_help()
        return EXIT_USAGE
    try:
        settings = load_settings(args.config)
    except StandupError as exc:
        sys.stderr.write("REFUSED: %s\n" % exc)
        return EXIT_USAGE
    try:
        if args.cmd == "preview":
            at = None
            if args.at:
                try:
                    at = datetime.fromisoformat(args.at)
                except ValueError:
                    sys.stderr.write("REFUSED: --at must look like 2026-11-04T09:00\n")
                    return EXIT_USAGE
            return preview(settings, args.kind, at)
        return run(settings, dry_run=args.dry_run)
    except StandupError as exc:
        sys.stderr.write("COULD NOT RUN: %s\n" % exc)
        return EXIT_FAILED


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def _fx_pr(n, state="open", opened=None, merged=None, checks="passing", waiting=(),
           review="clean", mergeable="mergeable", draft=False):
    required = [{"name": "Kit checks", "waits_on_person": False,
                 "state": {"passing": "passing", "failing": "failing"}.get(checks, "pending")}]
    required += [{"name": w, "state": "missing", "waits_on_person": True} for w in waiting]
    return {"repo": "example-org/kit", "number": n,
            "url": "https://github.com/example-org/kit/pull/%d" % n, "source": "github",
            "error": None, "state": state, "draft": draft, "opened_at": opened,
            "updated_at": merged or opened, "merged_at": merged, "closed_at": merged,
            "head_sha": "a" * 12, "base": "main", "mergeable": mergeable,
            "merge_state": "clean" if mergeable == "mergeable" else "dirty", "labels": [],
            "checks": {"result": checks, "required": required},
            "review": {"state": review, "at": opened, "max_severity": None, "findings": 0,
                       "current": True}}


_STATE_NAMES = {"backlog": "Backlog", "unstarted": "Todo", "started": "In Progress",
                "completed": "Done", "canceled": "Canceled", "triage": "Triage"}


def _fx_piece(ident, title, state_type="started", started=None, completed=None, flags=(),
              prs=()):
    return {"id": ident, "title": title, "url": "https://linear.app/x/issue/" + ident.lower(),
            "state": _STATE_NAMES[state_type], "state_type": state_type, "started_at": started,
            "completed_at": completed, "canceled_at": None, "updated_at": completed or started,
            "flags": list(flags), "prs_complete": True, "prs": list(prs)}


def _fx_plan(ident, title, state_type="started", pieces=(), created=None, completed=None):
    return {"id": ident, "title": title, "url": "https://linear.app/x/issue/" + ident.lower(),
            "team": ident.split("-")[0], "state": _STATE_NAMES[state_type],
            "state_type": state_type, "created_at": created, "updated_at": created,
            "completed_at": completed, "canceled_at": None, "pieces_complete": True,
            "pieces": list(pieces)}


def _fx_status(now, plans, written_ago=5, sources=None):
    written = now - timedelta(minutes=written_ago)
    src = dict((name, {"state": "read", "asked": 1, "answered": 1, "reason": None,
                       "read_at": _iso(written)}) for name in ("tracker", "github", "reviews"))
    src.update(sources or {})
    pieces = [p for pl in plans for p in pl["pieces"]]
    prs = [pr for p in pieces for pr in p["prs"]]
    counts = {"plans": len(plans), "pieces": len(pieces), "pull_requests": len(prs),
              "conflicting": sum(1 for pr in prs if pr["mergeable"] == "conflicting")}
    for state in ("open", "merged", "closed", "unknown"):
        counts[state] = sum(1 for pr in prs if pr["state"] == state)
    whole = all(v["state"] == "read" for v in src.values())
    return {"schema": STATUS_SCHEMA, "written_at": _iso(written),
            "started_at": _iso(written - timedelta(seconds=20)),
            "result": "ok" if whole else "partial", "error": None,
            "lookback_hours": 72, "sources": src, "counts": counts, "plans": plans,
            "findings": [], "firing": []}


def cases_bridge(kit):
    """The bridge's hook (KIT-349), on the bridge's own fixtures: a due rundown goes out once
    in the owner's DM as the bridge's own post, a dry run claims nothing, a failed post is
    said and stays due, and while the stand-up is on a piece's start has no message of its
    own. The bridge's selftest calls this, and so does this module's own, each with a kit."""
    import pipeline_standup as su   # the copy the bridge imports, whichever runs this
    from pipeline_bridge import ALICE, DM, EXIT_OK as BRIDGE_OK
    from pipeline_bridge_config import key_of, state_path
    with kit as tmp:
        check, now, run, dmworld, in_dm = kit.take("check", "now", "run", "dmworld", "in_dm")
        saved_tz, saved_default = os.environ.get("TZ"), su.DEFAULT_SETTINGS_FILE
        # A test pass must never read the role account's own settings, or write its record.
        check("standup hook: the kit keeps every test pass off the real settings",
              (saved_default.startswith(tmp), os.path.exists(saved_default)), (True, False))
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            def setup(name, morning="08:30", afternoon="15:00", late=180, **over):
                c, s, t = dmworld(name, **over)          # its baseline pass reads no stand-up
                home = os.path.join(tmp, name + "-standup")
                os.makedirs(home)
                status = os.path.join(home, "status.json")
                with open(os.path.join(home, "standup.json"), "w", encoding="utf-8") as fh:
                    json.dump({"schema": SETTINGS_SCHEMA, "on": True, "morning_at": morning,
                               "afternoon_at": afternoon, "late_by_minutes": late,
                               "days": list(DAY_NAMES), "status_file": status,
                               "record_file": os.path.join(home, "record.json"),
                               "bridge_config": os.path.join(home, "bridge.json")}, fh)
                when = datetime.fromtimestamp(now).astimezone()
                ago = lambda **kw: _iso(when - timedelta(**kw))  # noqa: E731
                with open(status, "w", encoding="utf-8") as fh:
                    json.dump(_fx_status(when, [_fx_plan(
                        "PROD-70", "Calm plan", "started", created=ago(days=2), pieces=[
                            _fx_piece("PROD-71", "Steady piece", started=ago(hours=3), prs=[
                                _fx_pr(371, opened=ago(hours=2), checks="pending",
                                       review="not-reviewed")])])]), fh)
                su.DEFAULT_SETTINGS_FILE = os.path.join(home, "standup.json")
                return c, s, t, home

            def record_of(home):
                path = os.path.join(home, "record.json")
                if not os.path.exists(path):
                    return None
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)

            # Due: posted once, in the owner's DM, kept as this app's own post.
            c, s, t, home = setup("standup-due")
            before = len(s.posts)
            code, _out = run(c, s, t)
            new = s.posts[before:]
            with open(state_path(c), encoding="utf-8") as fh:
                posted = json.load(fh).get("posted") or {}
            sent = (record_of(home) or {}).get("morning") or {}
            again, _o = run(c, s, t, at=now + 60)
            check("standup hook: a due rundown goes out once, in the owner's DM, as this "
                  "app's own post",
                  (code, [(ch, th) for ch, th, _x in new],
                   bool(new) and new[0][2].startswith("Good morning. Here's today's stand-up"),
                   key_of(DM, sent.get("ts")) in posted, sent.get("channel"), again,
                   len(s.posts) - before),
                  (BRIDGE_OK, [(DM, None)], True, True, DM, BRIDGE_OK, 1))

            # A dry run: nothing claimed, nothing posted.
            c, s, t, home = setup("standup-dry")
            before = len(s.posts)
            code, out = run(c, s, t, dry=True)
            check("standup hook: a dry run claims and posts nothing",
                  (code, len(s.posts) - before, record_of(home),
                   "[dry-run] stand-up: would claim and post the morning rundown" in out),
                  (BRIDGE_OK, 0, None, True))

            # A post that fails: said at the end of the pass, and still due on the next.
            c, s, t, home = setup("standup-fail")
            s.fail.add("chat.postMessage")
            code, out = run(c, s, t)
            s.fail.discard("chat.postMessage")
            held = record_of(home) or {}
            before = len(s.posts)
            again, _o = run(c, s, t, at=now + 60)
            check("standup hook: a post that fails is said, and the rundown stays due",
                  (code != BRIDGE_OK, "morning rundown could not be posted" in out,
                   "it stays due" in out, "morning" in held, again, len(s.posts) - before),
                  (True, True, True, False, BRIDGE_OK, 1))

            # On but not due: a piece's start is handed over with no message of its own.
            c, s, t, home = setup("standup-quiet-start", morning="20:00", afternoon="21:00",
                                  late=30, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            s.add(ALICE, "approve PROD-50", channel=DM)
            run(c, s, t)
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            texts = [x for _th, x in in_dm(s)]
            check("standup hook: while it is on, a piece's start is handed over with no "
                  "message of its own",
                  (any("Approved PROD-50" in x for x in texts),
                   any("Started PROD-51" in x for x in texts),
                   any(x.startswith("Good morning") for x in texts), t.delegations),
                  (True, False, False, [("uuid-51", "agent-1")]))
        finally:
            su.DEFAULT_SETTINGS_FILE = saved_default
            if saved_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved_tz
            time.tzset()


class _FakePass(object):
    """The parts of the bridge's Pass that bridge_tick uses, for the paths a real pass
    rarely reaches."""

    def __init__(self, now, dms, writer_seconds=60, mentions=False):
        self.now, self.dry, self.lines, self.posts, self.saved = now, False, [], [], 0
        self.cfg = {"dm_user_ids": ["U-OWNER"], "writer": True, "writer_timeout_seconds": 60}
        self.state, self.counts = {"dms": dms}, {}
        self._seconds, self._mentions, self.writer_calls = writer_seconds, mentions, []

    def say(self, line):
        self.lines.append(line)

    def save(self):
        self.saved += 1

    def writer_seconds(self):
        return self._seconds

    def mentions_chat_bot(self, text):
        return self._mentions

    def writer(self, cfg, kind, facts, fallback, keep=(), bind="", runner=None, now=None):
        self.writer_calls.append(cfg.get("writer_timeout_seconds"))
        return {"text": "WORDED", "by": "model", "why": None, "cost_usd": 0.001}

    def post(self, channel, thread_ts, text, kind="", bind=""):
        self.posts.append((channel, thread_ts, text, kind, bind))
        return "1800.000001"


class _FakeSlack(object):
    def __init__(self, fail=None):
        self.fail, self.opened, self.posts = fail, [], []

    def open_dm(self, user_id):
        self.opened.append(user_id)
        return "D-OWNER"

    def post(self, channel, thread_ts, text):
        if self.fail:
            raise transport.BridgeError(self.fail)
        self.posts.append((channel, thread_ts, text))
        return "1730.%06d" % len(self.posts)


def _selftest():
    import inspect
    import io
    import pipeline_bridge as bridge
    import jsonschema_mini

    checks, failures = [0], []

    def check(name, cond, detail=""):
        checks[0] += 1
        if not cond:
            failures.append("%s: %s" % (name, detail))

    tmp = tempfile.mkdtemp(prefix="standup-selftest.")
    status_path = os.path.join(tmp, "status", "pipeline-status.json")
    os.makedirs(os.path.dirname(status_path))
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schemas",
                               "pipeline-status.schema.json")
    with open(schema_path, encoding="utf-8") as fh:
        status_schema = json.load(fh)

    def settings_with(**over):
        raw = {"schema": SETTINGS_SCHEMA, "on": True, "status_file": status_path,
               "record_file": os.path.join(tmp, "state", "standup-record.json"),
               "bridge_config": os.path.join(tmp, "bridge.json")}
        raw.update(over)
        return validate_settings(raw, "selftest")

    def put(doc):
        errors = jsonschema_mini.validate(doc, status_schema)
        check("fixture-matches-the-status-schema", not errors,
              "; ".join("%s %s" % (e["path"], e["message"]) for e in errors[:3]))
        with open(status_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    # --- the settings: off by default, every problem at once, one place for the times ---
    s0 = load_settings(os.path.join(tmp, "nothing-here.json"))
    check("no-settings-file-is-off-by-name", s0["on"] is False and "no settings file"
          in s0["why_off"] and due(time.time(), s0, {}) is None, repr(s0.get("why_off")))
    try:
        validate_settings({"schema": "x", "on": "yes", "morning_at": "9am",
                           "afternoon_at": "08:00", "status_file": "rel.json", "color": 1})
        said = ""
    except StandupError as exc:
        said = str(exc)
    check("settings-say-every-problem", all(w in said for w in (
        "'schema'", "unknown keys: color", "'on'", "'morning_at'", "'status_file'")), said)
    try:
        validate_settings({"schema": SETTINGS_SCHEMA, "morning_at": "10:00",
                           "afternoon_at": "09:00"})
        said = ""
    except StandupError as exc:
        said = str(exc)
    check("afternoon-must-follow-morning", "must come after" in said, said)
    try:
        validate_settings({"schema": SETTINGS_SCHEMA, "morning_at": "09:00",
                           "afternoon_at": "11:00", "late_by_minutes": 120})
        said = ""
    except StandupError as exc:
        said = str(exc)
    check("a-late-morning-cannot-reach-the-afternoon", "less than the 120 minutes" in said, said)
    check("defaults-are-off-at-nine-and-three", (DEFAULTS["on"], DEFAULTS["morning_at"],
                                                 DEFAULTS["afternoon_at"]) == (False, "09:00",
                                                                               "15:00"))
    s = settings_with()
    day = datetime(2026, 11, 4, 9, 5).astimezone()
    times = rundown_times(settings_with(morning_at="07:30", afternoon_at="13:45"), day.date())
    check("rundown-times-come-from-the-settings",
          [(t.hour, t.minute, t.date()) for t in times.values()]
          == [(7, 30, day.date()), (13, 45, day.date())], repr(times))

    # --- due and missed ---
    at = lambda h, m=0: datetime(2026, 11, 4, h, m).astimezone()  # noqa: E731
    sent_morning = {"morning": {"day": "2026-11-04"}}
    both = {"morning": {"day": "2026-11-04"}, "afternoon": {"day": "2026-11-04"}}
    check("due-in-each-window-and-not-before",
          [due(at(8, 59), s, {}), due(at(9), s, {}), due(at(9, 5), s, {}),
           due(at(9, 5), s, sent_morning), due(at(15, 1), s, sent_morning),
           due(at(15, 1), s, both)] == [None, "morning", "morning", None, "afternoon", None])
    check("late-is-missed-never-sent", (due(at(12, 1), s, {}), missed(at(12, 1), s, {}),
                                        missed(at(12, 1), s, sent_morning))
          == (None, ["morning"], []))
    check("a-new-day-is-due-again", due(datetime(2026, 11, 5, 9, 1).astimezone(), s,
                                        both) == "morning")
    check("off-is-never-due", (due(at(9, 5), dict(s, on=False), {}),
                               missed(at(13), dict(s, on=False), {})) == (None, []))
    # Days: Monday to Saturday by default (4 Nov 2026 is a Wednesday, the 8th a Sunday).
    sun = lambda h, m=0: datetime(2026, 11, 8, h, m).astimezone()  # noqa: E731
    mon = lambda h, m=0: datetime(2026, 11, 9, h, m).astimezone()  # noqa: E731
    check("sunday-is-off-by-default", (rundown_times(s, sun(9).date()), due(sun(9, 5), s, {}),
                                       missed(sun(13), s, {}), due(mon(9, 5), s, {}))
          == ({}, None, [], "morning"))
    only_sunday = settings_with(days=["sun"])
    check("days-are-a-setting", (due(sun(9, 5), only_sunday, {}),
                                 due(mon(9, 5), only_sunday, {})) == ("morning", None))
    for bad_days in (["funday"], [], ["mon", "mon"], "mon"):
        try:
            validate_settings({"schema": SETTINGS_SCHEMA, "days": bad_days})
            said = ""
        except StandupError as exc:
            said = str(exc)
        check("days-are-checked", "'days' must name each day it posts once" in said,
              "%r: %s" % (bad_days, said))
    saturday_done = {"morning": {"day": "2026-11-07"}, "afternoon": {"day": "2026-11-07"}}
    check("the-next-rundown-skips-days-off",
          (_next_due(datetime(2026, 11, 7, 16, 0).astimezone(), s, saturday_done),
           _next_due(sun(10), s, {}))
          == ("today's rundowns are done; the next is Monday's morning, at 09:00",
              "it doesn't post today; the next is tomorrow's morning, at 09:00"))

    # --- the morning: asks first, then in progress, then moved since yesterday ---
    now = at(9, 5)
    ago = lambda **kw: _iso(now - timedelta(**kw))  # noqa: E731
    plans = [
        _fx_plan("KIT-420", "Faster reviews", "backlog", created=ago(hours=20), pieces=[
            _fx_piece("KIT-421", "Cache the diff", "backlog"),
            _fx_piece("KIT-422", "Reuse the clone", "backlog")]),
        _fx_plan("KIT-400", "Status everywhere", "started", created=ago(days=3), pieces=[
            _fx_piece("KIT-410", "Status job", "completed", started=ago(days=2),
                      completed=ago(hours=17), prs=[_fx_pr(265, "merged", ago(days=2),
                                                           ago(hours=17, minutes=2))]),
            _fx_piece("KIT-411", "Install step", started=ago(hours=23), prs=[
                _fx_pr(266, opened=ago(hours=22), checks="waiting",
                       waiting=("Hooks change guard",))]),
            _fx_piece("KIT-413", "Wrap message", started=ago(minutes=35), prs=[
                _fx_pr(272, opened=ago(minutes=10), checks="pending", review="not-reviewed")]),
            _fx_piece("KIT-414", "Ready piece", started=ago(days=2), prs=[
                _fx_pr(273, opened=ago(days=2))]),
            _fx_piece("KIT-415", "Stuck piece", started=ago(days=2), flags=("agent:needs-human",)),
            _fx_piece("KIT-416", "Later piece", "backlog"),
            _fx_piece("KIT-418", "Tangled piece", started=ago(days=2), prs=[
                _fx_pr(275, opened=ago(days=2), mergeable="conflicting")])]),
        _fx_plan("KIT-399", "Old plan", "completed", created=ago(days=9), completed=ago(days=4),
                 pieces=[_fx_piece("KIT-398", "Old piece", "completed", started=ago(days=8),
                                   completed=ago(days=4))]),
    ]
    put(_fx_status(now, plans))
    record = {"schema": RECORD_SCHEMA, "morning": {"day": "2026-11-03",
                                                   "sent_at": ago(hours=24, minutes=4)}}
    m = compose("morning", now, s, record)
    text = m["text"]
    first = lambda needle: text.find(needle)  # noqa: E731
    check("morning-opens-then-asks-first", text.startswith("Good morning. Here's today's "
                                                           "stand-up, from the status file "
                                                           "written at 09:00.")
          and 0 < first("Needs your OK:") < first("In progress:") < first(
              "Moved since yesterday:"), text[:400])
    check("an-approval-ask-says-what-holds-and-the-exact-reply",
          "Plan KIT-420, Faster reviews, waits for your approval. It holds up its 2 pieces. "
          "To approve, reply `approve KIT-420`." in text, text)
    check("a-ready-pull-request-says-where-to-merge",
          "Pull request #273 for KIT-414, Ready piece, is ready to merge. It holds up plan "
          "KIT-400 (1 of 7 pieces done). Merge it on GitHub: "
          "https://github.com/example-org/kit/pull/273" in text, text)
    check("a-check-waiting-on-a-person-is-an-ask",
          "Pull request #266 for KIT-411, Install step, waits on you for Hooks change guard. "
          "It holds up its merge, and so plan KIT-400" in text, text)
    check("a-stuck-piece-is-an-ask", "KIT-415, Stuck piece, is stuck until you answer it."
          in text and "on the ticket: https://linear.app/x/issue/kit-415" in text, text)
    asks_part = text[first("Needs your OK:"):first("In progress:")]
    check("only-open-things-are-asked", not any(x in asks_part for x in (
        "KIT-399", "KIT-410", "KIT-413", "#265", "#272", "#275")), asks_part)
    progress = text[first("In progress:"):first("Moved since yesterday:")]
    check("in-progress-is-the-started-pieces", all(x in progress for x in (
        "KIT-411", "KIT-413", "KIT-414", "KIT-415")) and not any(x in progress for x in (
            "KIT-410", "KIT-416", "KIT-421")), progress)
    check("in-progress-says-each-pull-request",
          "pull request #272 is open, checks are running, not reviewed yet" in progress
          and "KIT-415, Stuck piece (plan KIT-400): no pull request yet" in progress
          and "pull request #275 is open, checks pass, review found nothing, it has a conflict"
          in progress, progress)
    moved = text[first("Moved since yesterday:"):]
    check("moved-is-since-the-last-morning", all(x in moved for x in (
        "plan KIT-420 was filed", "pull request #265 merged", "KIT-410, Status job, finished",
        "KIT-413, Wrap message, started", "pull request #266 opened")) and not any(
            x in moved for x in ("KIT-414, Ready piece, started", "KIT-398", "plan KIT-399")),
          moved)
    check("moved-reads-in-the-owners-time", "yesterday 16:03 pull request #265 merged" in moved
          and "08:30 KIT-413, Wrap message, started" in moved, moved)
    check("plain-is-the-text-without-a-writer", m["plain"] == m["text"] and m["by"] ==
          "fallback" and m["problem"] is None, repr(m["by"]))
    banned = sorted(set(w.lower() for w in bridge.BANNED_WORDS_RE.findall(
        text + "\n" + m["plain"])))
    check("no-word-the-owners-dm-keeps-out", not banned, banned)

    # --- the writer words the rest, never the asks ---
    seen = {}

    def fake_writer(cfg, kind, facts, fallback, keep=(), bind="", runner=None, now=None):
        seen.update(cfg=cfg, kind=kind, facts=facts, fallback=fallback, keep=list(keep))
        return {"text": "WRITTEN WORDS", "by": "model", "why": None, "cost_usd": 0.002}

    w = compose("morning", now, s, record, bridge_cfg={"writer": True}, writer=fake_writer)
    check("the-writer-words-what-follows-the-asks",
          w["text"].endswith("WRITTEN WORDS")
          and "To approve, reply `approve KIT-420`." in w["text"]
          and w["text"].index("Needs your OK:") < w["text"].index("WRITTEN WORDS")
          and w["by"] == "model" and w["plain"] == m["text"], w["text"][-200:])
    check("the-asks-never-pass-through-the-writer", "approve KIT-420" not in seen["fallback"]
          and "approve KIT-420" not in json.dumps(seen["facts"])
          and seen["kind"] == "standup-morning", seen.get("fallback", "")[:200])
    check("the-writer-must-keep-every-id", all(x in seen["keep"] for x in (
        "KIT-410", "KIT-413")), seen.get("keep"))
    real = compose("morning", now, s, record, bridge_cfg={"writer": False},
                   writer=voice.write)
    check("a-writer-that-is-off-sends-the-plain-words", real["by"] == "fallback"
          and real["text"] == m["text"], repr((real["by"], real["why"])))

    # --- the afternoon: shipped, started and finished, then still waiting ---
    later = at(15, 5)
    plans[1]["pieces"].append(_fx_piece("KIT-417", "Quick fix", "completed",
                                        started=_iso(at(10, 2)), completed=_iso(at(13, 31)),
                                        prs=[_fx_pr(274, "merged", _iso(at(10, 40)),
                                                    _iso(at(13, 30)))]))
    plans[1]["pieces"].append(_fx_piece("KIT-419", "Long piece", "completed",
                                        started=_iso(at(10, 15) - timedelta(days=1)),
                                        completed=_iso(at(14, 2))))
    put(_fx_status(later, plans))
    a = compose("afternoon", later, s, record)
    at_text = a["text"]
    check("afternoon-order", at_text.startswith("Afternoon wrap. Here's what happened today")
          and at_text.find("Shipped today:") < at_text.find("Started and finished today:")
          < at_text.find("Still waiting on you:"), at_text)
    shipped = at_text[at_text.find("Shipped today:"):at_text.find("Started and finished")]
    check("shipped-is-today-only", "13:30 pull request #274 merged, for KIT-417, Quick fix"
          in shipped and "#265" not in shipped, shipped)
    sf = at_text[at_text.find("Started and finished today:"):at_text.find("Still waiting")]
    check("each-piece-start-and-finish-today", all(x in sf for x in (
        "13:31 KIT-417, Quick fix, finished (started 10:02)",
        "14:02 KIT-419, Long piece, finished (started yesterday 10:15)",
        "08:30 KIT-413, Wrap message, started")) and "KIT-410" not in sf
          and "KIT-417, Quick fix, started" not in sf, sf)
    # Slack's control characters are escaped wherever they come from, so no rundown can
    # mention anyone, make a link or broadcast.
    loud = compose("afternoon", later, s, record, bridge_cfg={"writer": True},
                   writer=lambda *a, **k: {"text": "Done <!channel> <@U999|x> & more",
                                           "by": "model", "why": None, "cost_usd": 0.0})
    check("slack-markup-is-escaped", "&lt;!channel&gt; &lt;@U999|x&gt; &amp; more"
          in loud["text"] and "<" not in loud["text"] and "<" not in loud["plain"],
          loud["text"][-120:])
    check("still-waiting-closes-the-wrap", "To approve, reply `approve KIT-420`."
          in at_text[at_text.find("Still waiting on you:"):], at_text[-300:])

    # --- nothing needs you, and a quiet day says what it checked ---
    calm = [_fx_plan("KIT-500", "Calm plan", "started", created=ago(days=5), pieces=[
        _fx_piece("KIT-501", "Done long ago", "completed", started=ago(days=5),
                  completed=ago(days=4))])]
    put(_fx_status(now, calm))
    q = compose("morning", now, s, record)["text"]
    check("nothing-needs-you-is-one-line", "\n\nNothing needs your OK.\n\n" in q, q)
    check("a-quiet-day-says-what-was-checked",
          "Nothing is in progress, and nothing moved since yesterday. I checked 1 plan, "
          "1 piece and 0 pull requests at 09:00." in q, q)
    put(_fx_status(later, calm))
    qa = compose("afternoon", later, s, record)["text"]
    check("a-quiet-afternoon-says-so-too", "Nothing shipped today, and no piece started or "
          "finished." in qa and qa.endswith("Nothing is waiting on you."), qa)

    # --- after a day off, Monday's morning says since when ---
    monday = mon(9, 5)
    put(_fx_status(monday, [_fx_plan("KIT-600", "Weekend plan", "started",
                                     created=_iso(monday - timedelta(days=9)), pieces=[
        _fx_piece("KIT-601", "Saturday piece", started=_iso(datetime(
            2026, 11, 7, 14, 0).astimezone()))])]))
    after_off = compose("morning", monday, s, {"schema": RECORD_SCHEMA, "morning": {
        "day": "2026-11-07", "sent_at": _iso(datetime(2026, 11, 7, 9, 1).astimezone())}})
    check("monday-says-since-saturday", "Moved since Saturday:\n- Sat 14:00 KIT-601, "
          "Saturday piece, started" in after_off["text"], after_off["text"])

    # --- old news, no news, a source unread ---
    put(_fx_status(now, plans, written_ago=180))
    st = compose("morning", now, s, record)
    check("a-stale-file-is-said-instead-of-old-news",
          "There's no stand-up this morning: the status file was last written at 06:05, "
          "3 hours ago. I won't pass on old news." in st["text"] and "KIT-4" not in st["text"]
          and st["problem"], st["text"])
    os.unlink(status_path)
    gone = compose("afternoon", later, s, record)
    check("a-missing-file-is-said", "there's no status file at " + status_path in gone["text"],
          gone["text"])
    with open(status_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    check("an-unreadable-file-is-said", "isn't readable JSON" in compose(
        "morning", now, s, record)["text"])
    put(_fx_status(now, [], sources={"tracker": {
        "state": "could-not-read", "asked": 1, "answered": 0, "reason": "HTTP 503",
        "read_at": None}}))
    blind = compose("morning", now, s, record)
    check("unread-linear-is-said-and-nothing-is-claimed",
          "Linear couldn't be read at 09:00 (HTTP 503), so I can't say what needs you"
          in blind["text"] and "Nothing needs your OK" not in blind["text"]
          and "Nothing is in progress" not in blind["text"] and blind["problem"], blind["text"])
    put(_fx_status(now, plans, sources={"github": {
        "state": "partly-read", "asked": 4, "answered": 3, "reason": "rate limited",
        "read_at": _iso(now - timedelta(minutes=5))}}))
    part = compose("morning", now, s, record)
    check("partly-read-github-is-said-and-the-rest-goes-on",
          "GitHub was read only in part at 09:00 (rate limited)" in part["text"]
          and "Needs your OK:" in part["text"] and part["problem"], part["text"][:300])

    # --- by hand: run sends once, records it, and says every nothing ---
    put(_fx_status(now, plans))
    bcfg = {"dm_user_ids": ["U-OWNER"], "slack_token_env": "STANDUP_SELFTEST_SLACK",
            "linear_key_env": "STANDUP_SELFTEST_LINEAR", "writer": False}
    os.environ["STANDUP_SELFTEST_SLACK"] = "standup-selftest-not-a-real-value-7f3a"
    try:
        slack, buf = _FakeSlack(), io.StringIO()
        off = run(dict(s, on=False, why_off="test"), now=now, client=slack, bridge_cfg=bcfg,
                  out=buf)
        check("run-says-off-and-sends-nothing", off == EXIT_OK and buf.getvalue().startswith(
            "OFF:") and not slack.posts, buf.getvalue())
        buf = io.StringIO()
        code = run(s, now=now, client=slack, bridge_cfg=bcfg, out=buf)
        rec = load_record(s)
        check("run-sends-the-due-rundown-once-in-the-dm",
              code == EXIT_OK and slack.opened == ["U-OWNER"] and len(slack.posts) == 1
              and slack.posts[0][:2] == ("D-OWNER", None)
              and slack.posts[0][2].startswith("Good morning.")
              and buf.getvalue().startswith("SENT the morning rundown"), buf.getvalue())
        check("the-record-says-where-and-how",
              rec["morning"]["day"] == "2026-11-04" and rec["morning"]["channel"] == "D-OWNER"
              and rec["morning"]["ts"] == "1730.000001" and rec["morning"]["by"] == "fallback"
              and rec["schema"] == RECORD_SCHEMA, repr(rec))
        buf = io.StringIO()
        again = run(s, now=at(9, 20), client=slack, bridge_cfg=bcfg, out=buf)
        check("a-sent-rundown-is-never-sent-twice", again == EXIT_OK and len(slack.posts) == 1
              and "the afternoon rundown is next, at 15:00" in buf.getvalue(), buf.getvalue())
        failing, buf = _FakeSlack(fail="channel_not_found"), io.StringIO()
        code = run(s, now=at(15, 2), client=failing, bridge_cfg=bcfg, out=buf)
        check("a-post-that-fails-is-said-and-stays-due",
              code == EXIT_FAILED and "COULD NOT SEND the afternoon rundown" in buf.getvalue()
              and due(at(15, 3), s, load_record(s)) == "afternoon", buf.getvalue())
        buf = io.StringIO()
        late = run(s, now=at(18, 30), client=slack, bridge_cfg=bcfg, out=buf)
        check("a-missed-rundown-is-said-not-sent", late == EXIT_PARTIAL
              and "MISSED: the afternoon rundown" in buf.getvalue() and len(slack.posts) == 1,
              buf.getvalue())
        plans[1]["pieces"][2]["title"] = "Leaks standup-selftest-not-a-real-value-7f3a here"
        put(_fx_status(now, plans))
        buf, red = io.StringIO(), _FakeSlack()
        run(dict(s, record_file=os.path.join(tmp, "state2", "r.json")), now=now, client=red,
            bridge_cfg=bcfg, out=buf)
        check("a-live-secret-never-reaches-the-dm", red.posts and
              "standup-selftest-not-a-real-value-7f3a" not in red.posts[0][2], red.posts[:1])
        put(_fx_status(now, plans, written_ago=200))
        buf, stale_slack = io.StringIO(), _FakeSlack()
        code = run(dict(s, record_file=os.path.join(tmp, "state3", "r.json")), now=now,
                   client=stale_slack, bridge_cfg=bcfg, out=buf)
        check("a-stale-rundown-goes-out-and-exits-3", code == EXIT_PARTIAL
              and stale_slack.posts and "no stand-up this morning" in stale_slack.posts[0][2],
              buf.getvalue())
        # Claimed before it is posted: a record that cannot be saved means no post at all, and
        # a post whose details cannot be saved still never goes out twice.
        put(_fx_status(now, plans))
        real_save, calls = globals()["save_record"], {"n": 0}

        def save_fails(settings_, record_):
            raise OSError(28, "No space left on device")

        def second_save_fails(settings_, record_):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(28, "No space left on device")
            return real_save(settings_, record_)

        buf, unsaved = io.StringIO(), _FakeSlack()
        globals()["save_record"] = save_fails
        try:
            code = run(dict(s, record_file=os.path.join(tmp, "state5", "r.json")), now=now,
                       client=unsaved, bridge_cfg=bcfg, out=buf)
        finally:
            globals()["save_record"] = real_save
        check("a-record-that-cannot-be-saved-never-posts", code == EXIT_FAILED
              and not unsaved.posts and "could not be saved" in buf.getvalue(), buf.getvalue())
        buf, once = io.StringIO(), _FakeSlack()
        s6 = dict(s, record_file=os.path.join(tmp, "state6", "r.json"))
        globals()["save_record"] = second_save_fails
        try:
            first = run(s6, now=now, client=once, bridge_cfg=bcfg, out=buf)
            again = run(s6, now=at(9, 10), client=once, bridge_cfg=bcfg, out=io.StringIO())
        finally:
            globals()["save_record"] = real_save
        check("a-post-whose-record-fails-never-posts-twice", (first, again, len(once.posts))
              == (EXIT_FAILED, EXIT_OK, 1) and "will not go out again" in buf.getvalue(),
              buf.getvalue())
        # The MISSED line takes its time from rundown_times, so MVP 2's window shows its own.
        real_times, buf = globals()["rundown_times"], io.StringIO()
        globals()["rundown_times"] = lambda settings_, day: {
            "morning": datetime(day.year, day.month, day.day, 7, 0).astimezone(),
            "afternoon": datetime(day.year, day.month, day.day, 13, 0).astimezone()}
        try:
            run(dict(s, record_file=os.path.join(tmp, "state7", "r.json")), now=at(18, 30),
                client=_FakeSlack(), bridge_cfg=bcfg, out=buf)
        finally:
            globals()["rundown_times"] = real_times
        check("missed-says-the-window-time", "MISSED: the morning rundown was due at 07:00"
              in buf.getvalue() and "MISSED: the afternoon rundown was due at 13:00"
              in buf.getvalue(), buf.getvalue())
    finally:
        os.environ.pop("STANDUP_SELFTEST_SLACK", None)
    bad = os.path.join(tmp, "state", "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("[]")
    try:
        load_record(dict(s, record_file=bad))
        said = ""
    except StandupError as exc:
        said = str(exc)
    check("a-record-that-cannot-be-read-stops-a-send", "is not a " + RECORD_SCHEMA in said, said)

    # --- the bridge's hook: the paths a real pass rarely reaches, then the real pass ---
    hook_settings = os.path.join(tmp, "hook-settings.json")
    with open(hook_settings, "w", encoding="utf-8") as fh:
        json.dump({"schema": SETTINGS_SCHEMA, "on": True, "status_file": status_path,
                   "record_file": os.path.join(tmp, "hook", "record.json")}, fh)
    put(_fx_status(now, plans))
    saved_default = globals()["DEFAULT_SETTINGS_FILE"]
    globals()["DEFAULT_SETTINGS_FILE"] = hook_settings
    try:
        shut = _FakePass(now.timestamp(), dms={})
        check("hook-waits-for-the-owners-dm", bridge_tick(shut) == ("no-dm", None)
              and not shut.posts and "not open yet" in shut.lines[-1], shut.lines)
        rushed = _FakePass(now.timestamp(), dms={"U-OWNER": "D-OWNER"}, writer_seconds=5,
                           mentions=True)
        outcome = bridge_tick(rushed)
        rec = load_record(load_settings(hook_settings))["morning"]
        check("hook-posts-as-the-pass-own-plain-post", outcome == ("sent", None)
              and [p[:2] + p[3:] for p in rushed.posts] == [("D-OWNER", None, "", "")]
              and rushed.posts[0][2].startswith("Good morning.") and rushed.saved == 1
              and (rec["channel"], rec["ts"]) == ("D-OWNER", "1800.000001"), rushed.posts)
        check("hook-skips-the-writer-with-too-little-time", rushed.writer_calls == [], "")
        roomy = _FakePass(now.timestamp(), dms={"U-OWNER": "D-OWNER"}, writer_seconds=30,
                          mentions=True)
        os.unlink(os.path.join(tmp, "hook", "record.json"))
        bridge_tick(roomy)
        check("hook-cuts-the-writer-to-the-pass-and-posts-plain-on-a-mention",
              roomy.writer_calls == [30] and "WORDED" not in roomy.posts[0][2],
              (roomy.writer_calls, roomy.posts[:1]))
        os.unlink(os.path.join(tmp, "hook", "record.json"))
        real_save = globals()["save_record"]
        globals()["save_record"] = lambda *_a: (_ for _ in ()).throw(OSError(13, "denied"))
        try:
            unsaved = _FakePass(now.timestamp(), dms={"U-OWNER": "D-OWNER"})
            got = bridge_tick(unsaved)
        finally:
            globals()["save_record"] = real_save
        check("hook-posts-nothing-it-cannot-claim", got[0] == "could-not-claim"
              and "could not be saved" in (got[1] or "") and not unsaved.posts, got)
    finally:
        globals()["DEFAULT_SETTINGS_FILE"] = saved_default

    def check_eq(name, got, want):
        check(name, got == want, "got %r, want %r" % (got, want))

    cases_bridge(bridge.Kit(check_eq, tempfile.mkdtemp(prefix="standup-bridge.")))

    # --- the parts the bridge calls, and the parts this calls, pinned ---
    def params(fn):
        return list(inspect.signature(fn).parameters)

    check("the-interface-the-bridge-calls", (
        params(due), params(compose)[:4], params(mark_sent)[:5], params(load_settings),
        params(load_record), params(save_record), params(rundown_times), params(claim),
        params(release)) == (
        ["now", "settings", "record"], ["kind", "now", "settings", "record"],
        ["kind", "channel", "ts", "record", "now"], ["path"], ["settings"],
        ["settings", "record"], ["settings", "day"], ["kind", "record", "settings", "now"],
        ["kind", "record", "settings", "previous"]))
    check("the-pass-parts-the-hook-uses", all(callable(getattr(bridge.Pass, n, None)) for n in (
        "post", "save", "say", "writer_seconds", "mentions_chat_bot", "rundown"))
          and params(bridge.Pass.post)[:6] == ["self", "channel", "thread_ts", "text", "kind",
                                               "bind"]
          and (params(bridge_tick), params(is_on)) == (["p"], ["path"]))
    check("the-slack-client-this-posts-through", (params(transport.SlackClient.post),
                                                   params(transport.SlackClient.open_dm))
          == (["self", "channel", "thread_ts", "text"], ["self", "user_id"]))
    check("the-writer-this-calls", params(voice.write)[:8] == [
        "cfg", "kind", "facts", "fallback", "keep", "bind", "runner", "now"])
    check("the-status-file-this-reads", (STATUS_SCHEMA, DEFAULTS["status_file"]) == (
        status_job.STATUS_SCHEMA, status_job.DEFAULT_STATUS_FILE))
    check("room-for-mvp-2", (ASK_SOURCES, MORNING_SECTIONS, AFTERNOON_SECTIONS) == (
        [asks_from_status], [section_in_progress, section_moved],
        [section_shipped, section_started_finished]))
    # The command line, end to end: preview prints the plain rundown and changes nothing; run
    # with no settings file says OFF; settings that are wrong are refused by name.
    on_path, bad_path = os.path.join(tmp, "on.json"), os.path.join(tmp, "bad.json")
    with open(on_path, "w", encoding="utf-8") as fh:
        json.dump({"schema": SETTINGS_SCHEMA, "on": True, "status_file": status_path,
                   "record_file": os.path.join(tmp, "state4", "r.json")}, fh)
    with open(bad_path, "w", encoding="utf-8") as fh:
        json.dump({"schema": SETTINGS_SCHEMA, "morning_at": "9am"}, fh)
    put(_fx_status(now, plans))
    saved, sys.stdout = sys.stdout, io.StringIO()
    try:
        rc_preview = main(["preview", "--kind", "morning", "--config", on_path,
                           "--at", "2026-11-04T09:05"])
        shown = sys.stdout.getvalue()
        rc_off = main(["run", "--config", os.path.join(tmp, "absent.json")])
        said_off = sys.stdout.getvalue()[len(shown):]
    finally:
        sys.stdout = saved
    saved_err, sys.stderr = sys.stderr, io.StringIO()
    try:
        rc_bad = main(["run", "--config", bad_path])
    finally:
        sys.stderr = saved_err
    check("preview-prints-and-changes-nothing", rc_preview == EXIT_OK
          and shown.startswith("Good morning.") and "approve KIT-420" in shown
          and not os.path.exists(os.path.join(tmp, "state4", "r.json")), shown[:200])
    check("run-with-no-settings-says-off", rc_off == EXIT_OK and said_off.startswith("OFF:")
          and "no settings file" in said_off, said_off)
    check("wrong-settings-are-refused", rc_bad == EXIT_USAGE)

    if failures:
        for f in failures:
            print("FAIL " + f)
        print("standup selftest: %d of %d checks failed" % (len(failures), checks[0]))
        return EXIT_FAILED
    print("OK: standup selftest (%d checks)" % checks[0])
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
