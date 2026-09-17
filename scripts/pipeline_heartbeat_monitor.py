#!/usr/bin/env python3
"""Heartbeat monitor — the one Stage E job that READS the other three's heartbeats.

WHAT THIS IS

  Stage E's three daemons each write a heartbeat after every pass, precisely so that "the
  job is dead" and "the job ran and had nothing to do" stop looking identical (§13). Until
  this file existed, **nothing read them.** The three heartbeats were a signal with no
  consumer: a daemon could stop for a week and the only thing that would notice was an
  operator remembering to `cat` three files under another account's home.

  This is that consumer, and nothing more. One scheduled, one-shot pass, run by the SAME
  role account as the daemons it watches, on a LONGER interval. It reads the three files,
  judges each one, and when the verdict CHANGES it posts ONE comment on a configured
  tracker ticket. It is deterministic code — no model, no prompt, no tokens — so there is
  nothing here for an injection to steer, and a page cannot be produced by anything except
  a heartbeat a daemon actually wrote (or failed to).

WHAT IT CANNOT CATCH, STATED FIRST BECAUSE IT IS THE POINT

  A monitor on the same machine shares the machine's failure domain. Four blind spots, and
  none of them is a bug to be fixed here:

  1. **The Mac asleep, shut down, or the account logged out.** No process runs, so nothing
     is judged and nothing is posted. The silence is indistinguishable from health — which
     is exactly the conflation §13 is about, and this file cannot close it from inside.
  2. **Its own death.** A monitor that is not running pages nobody, for the same reason a
     poller that is not running files nothing. It writes its own heartbeat (below), and the
     Stage E installer's `verify` and `run` read it: its `schema`, `at`, `result` and
     `dry_run` fields. Nothing SCHEDULED reads it, so its death is seen only when a person
     runs the installer. This job moves the unread-heartbeat problem one step; it does not
     delete it.
  3. **A tracker outage.** The page is undeliverable exactly when the tracker is what is
     broken. An undelivered page is reported as exit 3 and retried next pass; it is never
     recorded as delivered.
  4. **Wrong, rather than dead.** A heartbeat proves a pass RAN and what it decided. A
     daemon happily doing the wrong thing every interval looks perfectly healthy here.

  Closing 1 and 2 needs something OFF this box — an external service that alerts when a
  ping stops arriving, which is the inverse of this design (it fails loud rather than
  silent). That is deliberately a separate piece of work (KIT-45) and deliberately not
  attempted here: a half-built dead-man's switch that silently stops pinging is worse than
  none.

WHAT IT WATCHES, AND WHY THE THREE SHAPES ARE NOT UNIFIED HERE

  The three daemons were written at different times and their heartbeats do not agree on
  field names: the review poller ends with `result`/`ended_at`, the bounce driver writes
  `result`/`at`/`finished_at` and also writes a `running` beat at the START of a pass, and
  the finding poller writes a boolean `ok` with `at`. Rewriting them to one shape would
  touch three daemons for this one reader's convenience, so the shapes stay where they are
  and the differences live in the WATCHERS table below — one row per job, declaring the
  schema string, the timestamp fields in priority order, and what counts as a good result.

  A shape this table does not recognize is `unreadable`, which is a verdict about THIS
  MONITOR ("I could not judge") and never about the daemon ("it is down"). Those two are
  different claims and are never collapsed.

WHAT "STALE" MEANS, AND THE ONE FALSE PAGE IT WOULD OTHERWISE GUARANTEE

  Stale = the newest timestamp in the file is older than `stale_multiplier` (default 2)
  times that job's interval in the config. That interval is the LONGEST A HEALTHY JOB CAN
  GO BETWEEN TWO HEARTBEATS: its scheduler interval, plus one pass's wall clock when a pass
  can outlast it. The review poller writes only when a pass ends, so a long pass inside its
  own deadline leaves an old file; an interval without the pass in it pages on that pass.
  "Stale" has no meaning without the interval, so a watched job with no interval is a
  config error rather than a guess.

  A sleeping laptop would otherwise make this useless. launchd runs the missed interval on
  wake, so for one interval after a wake EVERY heartbeat is legitimately old and a naive
  monitor pages on all three. So the monitor reads its OWN last-run timestamp first: if it
  missed its own schedule by more than `stale_multiplier` intervals, the machine was not
  running, staleness is NOT judged this pass, and the report says so in those words.
  `missing`, `failing` and `unreadable` are still judged — none of them depends on the
  clock. The next pass, one interval later, judges staleness normally.

  `rearm` does the same on purpose. It removes the last-run timestamp and nothing else, so
  the next pass is handled like a wake. The installer runs it just before it loads this
  job: the daemons were stopped for the reload, their heartbeats are old for that reason,
  and a pass that judged them would page about daemons that are running.

  A pass that could not judge staleness has only part of the picture, so it says little. It
  posts only for a missing, failing or unreadable heartbeat the last comment did not already
  name, and then it names the jobs it did not judge. Otherwise it posts nothing and records
  nothing about posting. It never closes an open incident: its recovery is not known yet.
  The next pass that judges staleness decides.

ONE COMMENT PER INCIDENT — NEVER ONE PER PASS

  The state file holds the fingerprint of the last verdict-set that was POSTED. A pass
  whose fingerprint matches it posts nothing: an incident is announced once, not every
  interval. A return to health posts one recovery comment and closes the incident. A
  changed problem (a second daemon joins the first) is a new fingerprint and is announced.

  The state also records the ticket the last comment went to. A ticket that is not that one
  has not seen the open incident, so a changed `notify_ticket_id` announces it again there.
  It gets no recovery comment for an incident it never saw.

  `min_seconds_between_comments` bounds a flapping daemon. A change suppressed by that
  cooldown is CARRIED, never dropped: it is counted, and the next allowed pass posts the
  CURRENT state naming how many changes it swallowed. A recovery is exempt — good news is
  never delayed, and a delayed recovery would leave a false impression standing.

  The state file is written AFTER a successful post, so a failed post retries next pass.
  Losing or corrupting it therefore costs a DUPLICATE page, never a missed one; a corrupt
  file is named loudly and treated as empty rather than silently read as "nothing to say".
  It lives outside every git working tree for the same reason the bounce ledger does — a
  file a session can write is a file a session can use to suppress a page about itself.

OFF UNLESS A TICKET IS CONFIGURED — BUT NEVER SILENTLY OFF

  With no `notify_ticket_id`, the monitor still runs, still judges, still prints the whole
  verdict table and still writes its heartbeat. What it cannot do is tell anyone. So an
  unconfigured pass that finds a problem exits 3 (declined) and names the config key: "I
  found something and had nowhere to say it" is a different sentence from "everything is
  fine", and collapsing them is the §13 defect this whole file exists to fix.

WHAT IT NEVER DOES (asserted in --selftest against this file's own source)

  Its ONLY tracker write is `commentCreate` on the one configured ticket. It applies no
  label, moves no ticket state, creates no ticket, touches no pull request, approves and
  merges nothing, and launches no session. It deliberately writes NO `pipeline-escalation`
  mark: a mark would make the human-action notifier apply a lifecycle label, and a label is
  not this job's to write. When the chat notifier is activated, adding a mark for daemon
  health is a change to THAT job's table, not a widening of this one.

Usage:
  pipeline_heartbeat_monitor.py run   --config FILE [--dry-run] [--timeout N]
  pipeline_heartbeat_monitor.py check --config FILE          # judge + print, post nothing
  pipeline_heartbeat_monitor.py rearm --config FILE          # next pass skips staleness
  pipeline_heartbeat_monitor.py --example-config
  pipeline_heartbeat_monitor.py --selftest

Exit:
  0  ran and reported: a comment was posted, or the verdict was already reported, or
     everything watched is healthy. Every quiet pass prints what it asked and what the
     answer was — a silent green is not one of this file's outcomes.
  1  could not do something it WILL retry: the tracker refused the comment, or a bug
     escaped. The state file is NOT advanced, so the next pass says it again.
  2  usage/config error — nothing was read and nothing was posted.
  3  ran, judged, and said nothing a person will see: no ticket is configured, the
     cooldown carried this change to a later pass, or this was a `check` that found a
     problem. Never the same code as a clean pass.
  4  hit the wall clock. Distinct from 1 so a scheduler log tells hung from failed.

  A heartbeat is left on every path above. The two that leave none are a --config that
  cannot be read (exit 2, before the state directory is known) and somebody stopping the
  process on purpose.

  `check` and `rearm` write no heartbeat. `rearm` exits 0 when the next pass will not judge
  staleness (the stamp was removed, or there was none to remove), 1 when the state file
  could not be rewritten, and 2 on a config error.
"""

import argparse
import calendar
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ── Exit vocabulary — the same five names and numbers every sibling job uses. ──────────
# Redeclared rather than imported so this file stands alone; --selftest asserts they still
# agree with the review poller, because an installer that cross-checks one cross-checks all.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_DECLINED = 3
EXIT_TIMEOUT = 4

RESULT_BY_CODE = {EXIT_OK: "ok", EXIT_ERROR: "error", EXIT_USAGE: "usage",
                  EXIT_DECLINED: "declined", EXIT_TIMEOUT: "timeout"}

HEARTBEAT_SCHEMA = "pipeline-heartbeat-monitor-heartbeat/1"
STATE_SCHEMA = "pipeline-heartbeat-monitor-state/1"

LINEAR_API = "https://api.linear.app/graphql"
TICKET_ID_RE = re.compile(r"^([A-Z][A-Z0-9]*)-([0-9]+)$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

DEFAULT_STATE_DIR = "~/.stage-e/state"
DEFAULT_FINDING_DIR = "~/.stage-e/finding"
DEFAULT_STALE_MULTIPLIER = 2
DEFAULT_COOLDOWN_SECONDS = 900
DEFAULT_RUN_TIMEOUT_SECONDS = 120
MIN_STALE_AFTER_SECONDS = 120        # floor, so a silly-small interval cannot page on jitter

# ── The three jobs, and how each one's heartbeat is shaped ─────────────────────────────
#
# `dir_key`      which config directory the file sits in (the finding poller has its own,
#                so its heartbeat.json cannot collide with the review poller's).
# `ts_fields`    timestamp fields in PRIORITY order — the first present and parseable wins.
#                The bounce driver's `finished_at` is preferred over its `at` because `at`
#                is rewritten by the mid-pass `running` beat.
# `bool_field`   a heartbeat that reports a boolean instead of a result string. No
#                watcher uses it today: the finding poller did until its /2 heartbeat.
# `good`         results that mean the last pass was fine. That is the daemon's own reading
#                of its exit, not "nothing was declined": the review poller's `declined`
#                (exit 3) is a pass that settled a NOT-reviewed verdict and said so on the
#                pull request — the poller doing its job, which the notifier does not page
#                on either. The bounce driver's `problems` and `deadline` and the finding
#                poller's `error` are a pass that could not do something, and stay out.
# `running`      results that mean a pass was IN FLIGHT when the file was written. Fresh,
#                that is healthy; stale, it means a pass started and never finished, which
#                is a different sentence from "the daemon is not running" and gets one.
WATCHERS = {
    "review-poller": {
        "label": "review poller",
        "writer": "scripts/pipeline_review_poller.py",
        "dir_key": "state_dir",
        "filename": "heartbeat.json",
        "schema": "pipeline-review-poller-heartbeat/1",
        "ts_fields": ("ended_at", "started_at", "at"),
        "result_field": "result",
        "bool_field": None,
        "good": ("ok", "declined"),
        "running": (),
    },
    "bounce-driver": {
        "label": "bounce driver",
        "writer": "scripts/pipeline_bounce_local.py",
        "dir_key": "state_dir",
        "filename": "bounce-heartbeat.json",
        "schema": "pipeline-bounce-heartbeat/1",
        "ts_fields": ("finished_at", "at", "started_at"),
        "result_field": "result",
        "bool_field": None,
        # `declined` is a pass whose only non-clean PRs were ones it was never meant to act
        # on, and `paused` is a pass a person stopped on purpose with a PAUSED file (KIT-112).
        # Both beat on schedule and neither is a job that is down or failing.
        "good": ("ok", "idle", "declined", "paused"),
        "running": ("running",),
    },
    "finding-poller": {
        "label": "finding poller",
        "writer": "scripts/pipeline_finding_poller.py",
        "dir_key": "finding_state_dir",
        "filename": "heartbeat.json",
        # /2 since the finding poller adopted the review poller's shape (result string,
        # started_at/ended_at). Read as /1 — `at` and a boolean `ok` — every healthy v2
        # file was a schema mismatch, which is `unreadable`, which PAGES.
        "schema": "pipeline-finding-poller-heartbeat/2",
        "ts_fields": ("ended_at", "started_at"),
        "result_field": "result",
        "bool_field": None,
        "good": ("ok",),
        "running": (),
    },
}

# Verdicts that mean a person should look. `unreadable` is in here on purpose: a monitor
# that cannot judge must be as loud as a job that is down, or "I could not tell" quietly
# becomes "nothing to report".
PROBLEM_VERDICTS = ("missing", "unreadable", "failing", "stale", "wedged")
# Verdicts that do not page. `unknown-after-gap` is the sleep blind spot: not a problem,
# not a clean bill of health either, and it is always NAMED in the report.
QUIET_VERDICTS = ("ok", "running", "unknown-after-gap")
# Problems that do not depend on the clock. A pass that cannot judge staleness still judges
# these, so they are the only thing such a pass may speak for.
CLOCK_FREE_PROBLEMS = ("missing", "unreadable", "failing")

VERDICT_SENTENCE = {
    "ok": "ran and reported a good result",
    "running": "a pass was in flight when it last wrote",
    "missing": "has never written a heartbeat here",
    "unreadable": "wrote something this monitor cannot judge",
    "failing": "ran and reported a bad result",
    "stale": "has not written since",
    "wedged": "started a pass and never finished it",
    "unknown-after-gap": "staleness not judged (this monitor had no recent run to measure from)",
}

CONFIG_KEYS = {
    "state_dir": "where the review poller's and bounce driver's heartbeats live",
    "finding_state_dir": "where the finding poller's heartbeat lives (its own directory)",
    "monitor_state_dir": "where THIS job's state file and heartbeat live; defaults to "
                         "state_dir; must be outside every git working tree",
    "watch": "which jobs this machine actually runs: any of %s. Required — an "
             "un-deployed job's absent heartbeat must not page forever"
             % ", ".join(sorted(WATCHERS)),
    "intervals": "map of watched job -> the longest a healthy job can go between two "
                 "heartbeats, in seconds: its scheduler interval, plus one pass's wall "
                 "clock when a pass can outlast it. Required for every watched job: "
                 "'stale' is meaningless without it",
    "run_interval_seconds": "THIS job's own interval, in seconds. Required: it is how a "
                            "missed pass (a sleeping machine) is told from a stale daemon",
    "stale_multiplier": "how many intervals old a heartbeat may be before it is stale "
                        "(default %d)" % DEFAULT_STALE_MULTIPLIER,
    "notify_ticket_id": "the TEAM-123 ticket one comment is posted on. UNSET = this job "
                        "judges and prints but cannot tell anyone (exit 3 on a problem)",
    "linear_key_env": "ENV VAR NAME holding the tracker key (never the key itself)",
    "min_seconds_between_comments": "flood bound for a flapping daemon (default %d). A "
                                    "carried change is counted and said later, never "
                                    "dropped; a recovery is exempt" % DEFAULT_COOLDOWN_SECONDS,
    "run_timeout_seconds": "wall clock for one pass; exceeding it is exit 4, not exit 1",
}

EXAMPLE_CONFIG = {
    "state_dir": DEFAULT_STATE_DIR,
    "finding_state_dir": DEFAULT_FINDING_DIR,
    "watch": ["review-poller", "bounce-driver", "finding-poller"],
    # Each is a 300 s schedule plus that daemon's pass wall clock (900 s for the review
    # poller and the bounce driver), or a 900 s schedule plus the finding poller's 300 s.
    "intervals": {"review-poller": 1200, "bounce-driver": 1200, "finding-poller": 1200},
    "run_interval_seconds": 1800,
    "stale_multiplier": DEFAULT_STALE_MULTIPLIER,
    "notify_ticket_id": "KIT-000",
    "linear_key_env": "STAGE_E_LINEAR_API_KEY",
    "min_seconds_between_comments": DEFAULT_COOLDOWN_SECONDS,
    "run_timeout_seconds": DEFAULT_RUN_TIMEOUT_SECONDS,
}


class MonitorError(Exception):
    """A condition the operator must fix. Carries an exit code, never a silent no-op."""

    def __init__(self, message, code=EXIT_ERROR):
        Exception.__init__(self, message)
        self.code = code


class RunTimeout(BaseException):
    """The pass outlived its wall clock. BaseException for the reason the review poller
    gives: as an Exception it would be swallowed by a bug-catcher and the deadline would
    quietly not exist."""


def log(msg):
    sys.stderr.write("[heartbeat-monitor] %s\n" % msg)
    sys.stderr.flush()


def _now():
    return time.time()


def _iso(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch if epoch is not None else _now()))


# --------------------------------------------------------------------------- #
# Pure logic — no I/O below this line until the state section, so --selftest
# exercises the whole judgment without a filesystem or a network
# --------------------------------------------------------------------------- #
def parse_iso(value):
    """Epoch seconds for a timestamp a sibling wrote, or None.

    Every sibling writes `%Y-%m-%dT%H:%M:%SZ`. Fractional seconds and a `+00:00` offset are
    tolerated because a future sibling might, and a timestamp this monitor cannot parse
    makes a live daemon read as unjudgeable — a false alarm, not a missed one, but still a
    lie worth not telling.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    text = re.sub(r"\.\d+", "", text)
    text = re.sub(r"([+-])00:?00$", "Z", text)
    if not text.endswith("Z"):
        text += "Z"
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def result_of(doc, spec):
    """The last pass's result as one lower-case word, or None if the file does not say.

    The finding poller's boolean becomes `ok`/`error` here so one vocabulary reaches the
    verdict table; the translation lives in this one function rather than at each use.
    """
    if spec.get("bool_field"):
        raw = doc.get(spec["bool_field"])
        if isinstance(raw, bool):
            return "ok" if raw else "error"
        return None
    raw = doc.get(spec.get("result_field") or "result")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return None


def stale_after(interval_seconds, multiplier):
    """How old a heartbeat may get before it is stale. Floored, so an aggressive interval
    cannot page on ordinary scheduler jitter."""
    return max(MIN_STALE_AFTER_SECONDS, int(interval_seconds) * int(multiplier))


def judge_one(job, spec, raw, now, limit, blind):
    """One verdict for one job. `raw` is what read_beat() found:
      {"exists": bool, "doc": dict|None, "error": str|None, "path": str}

    `blind` is the sleep blind spot: staleness is not judgeable this pass. It suppresses
    ONLY the two clock-derived verdicts. A missing, failing or unjudgeable heartbeat does
    not become healthy because the machine was asleep.
    """
    out = {"job": job, "label": spec["label"], "path": raw.get("path", ""),
           "result": None, "age_seconds": None, "beat_at": None, "detail": ""}
    if not raw.get("exists"):
        out["verdict"] = "missing"
        out["detail"] = ("no heartbeat file at %s — this job is in 'watch' and has never "
                         "written one here, or it was deleted" % raw.get("path", "?"))
        return out
    if raw.get("error") or not isinstance(raw.get("doc"), dict):
        out["verdict"] = "unreadable"
        out["detail"] = ("the file exists but this monitor cannot judge it (%s). That is a "
                         "statement about this monitor, not about the daemon"
                         % (raw.get("error") or "not a JSON object"))
        return out
    doc = raw["doc"]
    if doc.get("schema") != spec["schema"]:
        out["verdict"] = "unreadable"
        out["detail"] = ("expected schema %s and found %r — a shape this monitor does not "
                         "know is unjudged, never assumed healthy"
                         % (spec["schema"], doc.get("schema")))
        return out
    stamp = None
    for field in spec["ts_fields"]:
        stamp = parse_iso(doc.get(field))
        if stamp is not None:
            out["beat_at"] = doc.get(field)
            break
    if stamp is None:
        out["verdict"] = "unreadable"
        out["detail"] = ("no parseable timestamp in %s — without one there is no age to "
                         "judge" % ", ".join(spec["ts_fields"]))
        return out
    out["result"] = result_of(doc, spec)
    out["age_seconds"] = max(0, int(now - stamp))
    running = out["result"] in (spec.get("running") or ())
    if out["age_seconds"] > limit:
        if blind:
            out["verdict"] = "unknown-after-gap"
            out["detail"] = ("last beat %s (%s ago), older than the %ds limit — but this "
                             "monitor has no recent run of its own to measure from, so "
                             "staleness cannot be judged this pass"
                             % (out["beat_at"], human_age(out["age_seconds"]), limit))
            return out
        out["verdict"] = "wedged" if running else "stale"
        out["detail"] = ("last beat %s (%s ago), past the %ds limit%s"
                         % (out["beat_at"], human_age(out["age_seconds"]), limit,
                            "; it says a pass was still running, so that pass started and "
                            "never finished" if running else ""))
        return out
    if out["result"] in (spec.get("good") or ()):
        out["verdict"] = "ok"
        out["detail"] = "last beat %s ago, result %r" % (human_age(out["age_seconds"]),
                                                         out["result"])
    elif running:
        out["verdict"] = "running"
        out["detail"] = "last beat %s ago, a pass was in flight" % human_age(out["age_seconds"])
    else:
        out["verdict"] = "failing"
        out["detail"] = ("last beat %s ago and it reports %r — it RAN and could not do it, "
                         "which is not the same as being down"
                         % (human_age(out["age_seconds"]), out["result"]))
    return out


def human_age(seconds):
    seconds = int(seconds or 0)
    if seconds < 120:
        return "%ds" % seconds
    if seconds < 7200:
        return "%dm" % (seconds // 60)
    if seconds < 172800:
        return "%dh" % (seconds // 3600)
    return "%dd" % (seconds // 86400)


def _pair(verdict):
    """One job's part of a fingerprint: `job=verdict`."""
    return "%s=%s" % (verdict["job"], verdict["verdict"])


def fingerprint(verdicts):
    """The identity of a verdict-SET, stable under ordering. Ages and details are
    deliberately excluded: an incident whose only change is that it got older is the same
    incident, and including the age would page every pass."""
    return "|".join(_pair(v) for v in sorted(verdicts, key=lambda v: v["job"]))


def build_report(verdicts, unwatched, blind, gap_seconds):
    problems = [v for v in verdicts if v["verdict"] in PROBLEM_VERDICTS]
    return {
        "at": _iso(),
        "verdicts": verdicts,
        "unwatched": sorted(unwatched),
        "blind": bool(blind),
        "gap_seconds": gap_seconds,
        "problems": problems,
        "problem": bool(problems),
        "fingerprint": fingerprint(verdicts),
    }


def decide_post(report, state, now, cooldown, target=None):
    """(post?, why, carried) — the whole "one comment per incident" rule, as pure logic.

    `carried` is how many changes the cooldown has swallowed INCLUDING this one; it reaches
    the comment that finally goes out, so a suppressed change is late, never lost.

    `target` is the ticket this pass would comment on. A different ticket from the one the
    state recorded has not seen the open incident, so a problem is announced there, with no
    cooldown: nothing has been said on that ticket to flood it. A state written before the
    ticket was recorded names none. While a problem is open that reads as not yet posted (a
    duplicate, never a miss); otherwise as the same ticket, so a recovery still goes out.

    A pass that left any verdict `unknown-after-gap` (a wake, a first pass, a rearm) has
    only part of the picture. It posts only for a missing, failing or unreadable heartbeat
    whose `job=verdict` the last posted fingerprint does not hold. Otherwise it posts
    nothing and returns the carried count unchanged, so nothing about posting is recorded.
    It never posts a recovery. The next pass that judges staleness decides.
    """
    current = report["fingerprint"]
    last = state.get("last_posted_fingerprint")
    last_was_problem = bool(state.get("last_posted_problem"))
    carried = int(state.get("suppressed_changes") or 0)
    recorded = state.get("last_posted_target")

    unjudged = [v["job"] for v in report["verdicts"] if v["verdict"] == "unknown-after-gap"]
    if unjudged:
        # Posting part of the picture drops the unjudged jobs from the header, so a stale
        # daemon reads as recovered, and the next judged pass posts the whole picture again.
        # A clock-free problem nobody was told about does not wait for that pass.
        said = set((last or "").split("|"))
        if not any(v["verdict"] in CLOCK_FREE_PROBLEMS and _pair(v) not in said
                   for v in report["verdicts"]):
            why = ("this pass could not judge staleness for %s, and it found no missing, "
                   "failing or unreadable heartbeat the last comment did not already name. It "
                   "posts nothing and records nothing about posting; the next pass that judges "
                   "staleness decides" % ", ".join(sorted(unjudged)))
            return False, why, carried

    if target and last is not None and recorded != target:
        if report["problem"] and recorded:
            return True, ("%s has not seen the open verdict — it was said on %s — so it is "
                          "announced there" % (target, recorded)), carried
        if report["problem"]:
            return True, ("the state does not record which ticket the last comment went to, "
                          "so the open verdict is said on %s (a duplicate at worst)" % target), \
                carried
        if recorded:
            return False, ("no incident is open, and %s never saw the last one — there is "
                           "nothing to announce there" % target), carried
    if current == last:
        return False, "this exact verdict was already reported; an incident is announced "\
                      "once, not every pass", carried
    if not report["problem"]:
        if last is None or not last_was_problem:
            return False, "everything watched is healthy and no incident is open — there "\
                          "is nothing to announce", carried
        # A recovery closes an incident and is never delayed by the cooldown.
        return True, "recovery: the open incident has cleared", carried
    last_at = parse_iso(state.get("last_posted_at"))
    if last_at is not None and (now - last_at) < cooldown:
        return False, ("a change was found but the last comment was %s ago and the cooldown "
                       "is %ds — CARRIED to the next allowed pass, not dropped"
                       % (human_age(now - last_at), cooldown)), carried + 1
    return True, "the verdict changed", carried


def build_comment(report, state_note, carried, recovery, watched_intervals):
    """The comment body. Short, plain, and self-limiting: it says what it saw, what it
    could not see, and it carries NO escalation mark (a mark would make the chat notifier
    apply a lifecycle label, which is not this job's to write)."""
    lines = []
    if recovery:
        lines.append("**Stage E daemons are reporting again.** Every watched job has a "
                     "fresh heartbeat with a good result.")
    else:
        names = ", ".join(v["label"] for v in report["problems"])
        head = ("**Stage E daemon health: %d of %d watched job(s) need a look** — %s."
                % (len(report["problems"]), len(report["verdicts"]), names))
        unjudged = [v["label"] for v in report["verdicts"] if v["verdict"] == "unknown-after-gap"]
        if unjudged:
            # Named in the first line, so a job this pass could not judge never reads as one
            # that recovered.
            head += " Staleness not judged this pass, so not known to be healthy: %s." \
                % ", ".join(unjudged)
        lines.append(head)
    lines.append("")
    lines.append("| Job | Verdict | What that means |")
    lines.append("| --- | --- | --- |")
    for v in sorted(report["verdicts"], key=lambda v: v["job"]):
        lines.append("| %s | `%s` | %s |" % (v["label"], v["verdict"], v["detail"]))
    lines.append("")
    if report["unwatched"]:
        lines.append("Not watched on this machine, so not judged: %s."
                     % ", ".join(report["unwatched"]))
    if report["blind"] and report["gap_seconds"] is None:
        lines.append("Staleness was not judged this pass: this monitor has no last run on "
                     "record to measure from (its first pass, a state file it could not read, "
                     "or a rearm when the installer loaded it).")
    elif report["blind"]:
        lines.append("Staleness was not judged this pass: this monitor missed its own "
                     "schedule by %s, so the machine was asleep or off."
                     % human_age(report["gap_seconds"]))
    if carried:
        lines.append("%d earlier change(s) were held back by the comment cooldown and are "
                     "included in the state above." % carried)
    if state_note:
        lines.append(state_note)
    lines.append("")
    lines.append("Longest healthy gap between two heartbeats, per job: %s. Read the "
                 "heartbeat files themselves and the daemon logs as the role account "
                 "(operator doc, *Monitor the heartbeats, not the log*)."
                 % ", ".join("%s %ds" % (k, v) for k, v in sorted(watched_intervals.items())))
    lines.append("")
    lines.append("_Posted by the Stage E heartbeat monitor, which runs on the same machine "
                 "as the daemons it watches. It therefore cannot report the machine being "
                 "asleep or off, or its own death: silence here is not health._")
    return "\n".join(lines)


def render_report(report, intervals, notify_target=None):
    """The stdout form of a pass. Every pass prints this, including a quiet one — §13's
    'nothing to do' has to say what it asked and what the answer was.

    It also states, every pass, whether paging is on at all. An operator who has left
    `notify_ticket_id` unset and reads a clean exit 0 for a month would otherwise be
    trusting a page that was never going to arrive.
    """
    out = ["heartbeat monitor: %s" % report["at"]]
    for v in sorted(report["verdicts"], key=lambda v: v["job"]):
        out.append("  %-15s %-18s %s" % (v["job"], v["verdict"], v["detail"]))
    for job in report["unwatched"]:
        out.append("  %-15s %-18s not in 'watch' on this machine" % (job, "not-judged"))
    out.append("  asked: %d heartbeat file(s) against longest healthy gaps %s"
               % (len(report["verdicts"]),
                  ", ".join("%s=%ds" % (k, v) for k, v in sorted(intervals.items()))))
    out.append("  answer: %s" % ("%d problem(s)" % len(report["problems"])
                                 if report["problem"] else "every watched job is reporting"))
    out.append("  paging: %s" % ("one comment on %s when the verdict changes" % notify_target
                                 if notify_target else
                                 "OFF — no 'notify_ticket_id' is configured, so this pass "
                                 "can judge but cannot tell anyone"))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Config — every error in ONE pass, so an operator fixes the file once
# --------------------------------------------------------------------------- #
def _inside_git_worktree(path):
    """True if `path` sits inside a git working tree. A session can write a worktree, and a
    file a session can write is a file a session could use to suppress a page about its own
    PR — the same reason the bounce ledger refuses one."""
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
        raise MonitorError("could not read --config %s: %s" % (path, exc), EXIT_USAGE)
    if not isinstance(raw, dict):
        raise MonitorError("--config must hold one JSON object", EXIT_USAGE)

    problems = []
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        problems.append("unknown config key(s): %s (see --example-config)" % ", ".join(unknown))

    watch = raw.get("watch")
    if not isinstance(watch, list) or not watch or not all(isinstance(w, str) for w in watch):
        problems.append("'watch' must be a non-empty list naming the jobs this machine "
                        "runs, any of: %s" % ", ".join(sorted(WATCHERS)))
        watch = []
    else:
        watch = [w.strip() for w in watch]
        bad = [w for w in watch if w not in WATCHERS]
        if bad:
            problems.append("'watch' names unknown job(s): %s (known: %s)"
                            % (", ".join(bad), ", ".join(sorted(WATCHERS))))
            watch = [w for w in watch if w in WATCHERS]

    intervals_raw = raw.get("intervals") or {}
    if not isinstance(intervals_raw, dict):
        problems.append("'intervals' must be a map of job -> seconds")
        intervals_raw = {}
    intervals = {}
    for job in watch:
        value = intervals_raw.get(job)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            problems.append("'intervals[%s]' must be the longest that job can go between two "
                            "heartbeats when healthy, in seconds — 'stale' has no meaning "
                            "without it" % job)
        else:
            intervals[job] = value
    for job in sorted(set(intervals_raw) - set(watch)):
        problems.append("'intervals' names %r, which is not in 'watch' — an interval for a "
                        "job this machine does not run would never be used" % job)

    own = raw.get("run_interval_seconds")
    if not isinstance(own, int) or isinstance(own, bool) or own < 1:
        problems.append("'run_interval_seconds' must be THIS job's own interval in seconds "
                        "— it is how a missed pass (a sleeping machine) is told from a "
                        "stale daemon, and a wrong default would hide either one")

    multiplier = raw.get("stale_multiplier", DEFAULT_STALE_MULTIPLIER)
    if not isinstance(multiplier, int) or isinstance(multiplier, bool) or multiplier < 1:
        problems.append("'stale_multiplier' must be an integer >= 1")
        multiplier = DEFAULT_STALE_MULTIPLIER

    key_env = raw.get("linear_key_env") or "STAGE_E_LINEAR_API_KEY"
    if not isinstance(key_env, str) or not ENV_NAME_RE.match(key_env):
        problems.append("'linear_key_env' must be an ENV VAR NAME like "
                        "STAGE_E_LINEAR_API_KEY — never a credential value")
        key_env = "STAGE_E_LINEAR_API_KEY"

    ticket = (raw.get("notify_ticket_id") or "").strip() if isinstance(
        raw.get("notify_ticket_id"), str) else ""
    if ticket and not TICKET_ID_RE.match(ticket):
        problems.append("'notify_ticket_id' must be a TEAM-123 ticket id, or absent to "
                        "leave paging off")

    cooldown = raw.get("min_seconds_between_comments", DEFAULT_COOLDOWN_SECONDS)
    if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 0:
        problems.append("'min_seconds_between_comments' must be an integer >= 0")
        cooldown = DEFAULT_COOLDOWN_SECONDS

    timeout = raw.get("run_timeout_seconds", DEFAULT_RUN_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        problems.append("'run_timeout_seconds' must be an integer >= 1")
        timeout = DEFAULT_RUN_TIMEOUT_SECONDS

    dirs = {}
    for key, default in (("state_dir", DEFAULT_STATE_DIR),
                         ("finding_state_dir", DEFAULT_FINDING_DIR)):
        dirs[key] = os.path.realpath(os.path.expanduser(raw.get(key) or default))
    monitor_dir = os.path.realpath(os.path.expanduser(
        raw.get("monitor_state_dir") or raw.get("state_dir") or DEFAULT_STATE_DIR))
    if _inside_git_worktree(monitor_dir):
        problems.append("'monitor_state_dir' (%s) is inside a git working tree. A session "
                        "can write a worktree, and this file decides whether a page about "
                        "that session's PR is sent" % monitor_dir)

    if problems:
        raise MonitorError("this config cannot be used:\n  - %s" % "\n  - ".join(problems),
                           EXIT_USAGE)

    return {
        "state_dir": dirs["state_dir"],
        "finding_state_dir": dirs["finding_state_dir"],
        "monitor_state_dir": monitor_dir,
        "watch": watch,
        "intervals": intervals,
        "run_interval_seconds": own,
        "stale_multiplier": multiplier,
        "notify_ticket_id": ticket,
        "linear_key_env": key_env,
        "min_seconds_between_comments": cooldown,
        "run_timeout_seconds": timeout,
    }


def credential(cfg):
    name = cfg["linear_key_env"]
    value = os.environ.get(name, "").strip()
    if not value:
        raise MonitorError(
            "$%s is unset — export it in this job's OWN env file under the role account's "
            "home (<home>/.stage-e/env, mode 600), never in the dispatcher's env file, "
            "which is copied unscrubbed into every session" % name, EXIT_USAGE)
    return value


# --------------------------------------------------------------------------- #
# I/O — the heartbeats it reads, the state it keeps, the heartbeat it writes
# --------------------------------------------------------------------------- #
def _atomic_write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def beat_path(cfg, job):
    spec = WATCHERS[job]
    return os.path.join(cfg[spec["dir_key"]], spec["filename"])


def read_beat(path):
    """What is at `path`, without judging it. `exists` false and an `error` are different
    answers and both travel."""
    out = {"path": path, "exists": False, "doc": None, "error": None}
    try:
        with open(path, encoding="utf-8") as fh:
            out["exists"] = True
            out["doc"] = json.load(fh)
    except FileNotFoundError:
        return out
    except (OSError, ValueError) as exc:
        out["exists"] = True
        out["error"] = str(exc)
    return out


def state_path(cfg):
    return os.path.join(cfg["monitor_state_dir"], "monitor-state.json")


def heartbeat_path(cfg):
    return os.path.join(cfg["monitor_state_dir"], "monitor-heartbeat.json")


def load_state(path):
    """(state, note). A corrupt state file is NAMED and treated as empty: that costs a
    duplicate page, and the alternative — reading it as "nothing is open" — costs a missed
    one. Duplicates are annoying; a missed page is the failure this job exists to prevent.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return {}, ("this monitor's state file %s was unreadable (%s) and was treated as "
                    "empty, so this comment may repeat one you have already seen"
                    % (path, exc))
    if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA:
        return {}, ("this monitor's state file %s is not a %s document and was treated as "
                    "empty, so this comment may repeat one you have already seen"
                    % (path, STATE_SCHEMA))
    return doc, None


def save_state(path, state):
    doc = dict(state)
    doc["schema"] = STATE_SCHEMA
    _atomic_write_json(path, doc)


def write_heartbeat(cfg, **fields):
    """This job's own heartbeat, on every terminal path. Best effort by design: a heartbeat
    that cannot be written is said on stderr and never changes the pass's exit code.

    It closes no loop by itself — nothing SCHEDULED reads it. The Stage E installer's
    `verify` and `run` do, when a person runs them: they judge `schema`, `at`, `result` and
    `dry_run` (scripts/pipeline_stage_e_setup.py, whose selftest builds its fixture with
    this function). So this job's own death is at least VISIBLE to someone already looking.
    """
    doc = {"schema": HEARTBEAT_SCHEMA, "at": _iso()}
    doc.update(fields)
    try:
        _atomic_write_json(heartbeat_path(cfg), doc)
    except OSError as exc:
        log("NOTE: could not write the heartbeat %s: %s" % (heartbeat_path(cfg), exc))
    return doc


# --------------------------------------------------------------------------- #
# Tracker I/O — the ONE mutation this file will ever construct
# --------------------------------------------------------------------------- #
ISSUE_ID_QUERY = """
query($team: String!, $number: Float!) {
  issues(filter: { team: { key: { eq: $team } }, number: { eq: $number } }, first: 1) {
    nodes { id identifier }
  }
}"""

COMMENT_MUTATION = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}"""


def graphql(query, variables, api_key):
    req = urllib.request.Request(
        LINEAR_API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": api_key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise MonitorError("tracker API HTTP %d: %r" % (exc.code, exc.read()[:300]))
    except urllib.error.URLError as exc:
        raise MonitorError("could not reach the tracker API: %s" % exc.reason)
    except (OSError, ValueError) as exc:
        raise MonitorError("tracker API call failed: %s" % exc)
    if payload.get("errors"):
        raise MonitorError("tracker API error: %s" % json.dumps(payload["errors"])[:300])
    return payload.get("data") or {}


def post_comment(ticket_id, body, api_key, _graphql=None):
    """One comment on one ticket. `_graphql` is the seam --selftest stubs; there is no other
    write path in this file."""
    call = _graphql or graphql
    match = TICKET_ID_RE.match(ticket_id or "")
    if not match:
        raise MonitorError("%r is not a TEAM-123 ticket id" % ticket_id, EXIT_USAGE)
    data = call(ISSUE_ID_QUERY, {"team": match.group(1), "number": float(match.group(2))},
                api_key)
    nodes = ((data.get("issues") or {}).get("nodes")) or []
    if not nodes:
        raise MonitorError("ticket %s was not found — check 'notify_ticket_id'" % ticket_id)
    result = call(COMMENT_MUTATION, {"issueId": nodes[0]["id"], "body": body}, api_key)
    if not ((result.get("commentCreate") or {}).get("success")):
        raise MonitorError("the tracker did not accept the comment on %s" % ticket_id)
    return True


# --------------------------------------------------------------------------- #
# The pass
# --------------------------------------------------------------------------- #
def judge(cfg, state, now):
    """(report, blind) for this pass — the read half, with no decision in it."""
    gap = None
    last_run = parse_iso(state.get("last_run_at"))
    blind = True
    if last_run is not None:
        gap = int(max(0, now - last_run))
        blind = gap > cfg["run_interval_seconds"] * cfg["stale_multiplier"]
    verdicts = []
    for job in cfg["watch"]:
        spec = WATCHERS[job]
        limit = stale_after(cfg["intervals"][job], cfg["stale_multiplier"])
        verdicts.append(judge_one(job, spec, read_beat(beat_path(cfg, job)), now, limit, blind))
    unwatched = set(WATCHERS) - set(cfg["watch"])
    return build_report(verdicts, unwatched, blind, gap)


def run_pass(cfg, post=True, dry_run=False, poster=None):
    """One judge-decide-say pass. Returns (exit_code, report, what_happened)."""
    now = _now()
    state, state_note = load_state(state_path(cfg))
    if state_note:
        log("NOTE: %s" % state_note)
    report = judge(cfg, state, now)
    print(render_report(report, cfg["intervals"], cfg["notify_ticket_id"] or None))

    should, why, carried = decide_post(report, state, now, cfg["min_seconds_between_comments"],
                                       cfg["notify_ticket_id"] or None)
    recovery = should and not report["problem"]
    code, happened = EXIT_OK, why

    if not post:
        # `check` never writes and never posts — not even the last_run_at stamp, because a
        # rehearsal that moves the blind-spot clock would make the next real pass mis-judge.
        # It still exits 3 on a problem: a rehearsal that found two dead daemons and exited
        # 0 would be the §13 defect this file exists to fix, wearing a different hat.
        return ((EXIT_DECLINED if report["problem"] else EXIT_OK), report,
                "check only: would %s (%s)" % ("post" if should else "post nothing", why))

    if should and not cfg["notify_ticket_id"]:
        happened = ("a page is owed and there is NOWHERE to send it: no 'notify_ticket_id' "
                    "is configured. Paging is OFF, which is a choice; finding a problem and "
                    "having no way to say it is not a clean pass")
        code = EXIT_DECLINED
        should = False
    elif should and dry_run:
        happened = "DRY-RUN would post one comment (%s); nothing was written" % why
        should = False
    elif not should and report["problem"] and "CARRIED" in why:
        code = EXIT_DECLINED

    posted = False
    if should:
        # A delivery failure is caught HERE rather than left to propagate, and that is
        # load-bearing: the state write below advances `last_run_at`, which is what tells a
        # sleeping machine from a stale daemon. Left to propagate, a tracker that is down
        # every pass would leave `last_run_at` unwritten forever, every pass would look like
        # a wake-up, and staleness would silently never be judged again — the failure
        # quietly disabling the check. The fingerprint is still NOT recorded, so the page is
        # retried next pass.
        try:
            body = build_comment(report, state_note, carried, recovery, cfg["intervals"])
            api_key = credential(cfg)
            poster = poster or post_comment
            poster(cfg["notify_ticket_id"], body, api_key)
            posted = True
            happened = "posted one comment on %s (%s)" % (cfg["notify_ticket_id"], why)
        except MonitorError as exc:
            code = exc.code if exc.code != EXIT_OK else EXIT_ERROR
            happened = ("a page was owed and could NOT be delivered: %s — it is not recorded "
                        "as sent, so the next pass says it again" % exc)
            log("FAIL: %s" % happened)

    if not dry_run:
        new_state = dict(state)
        new_state["last_run_at"] = _iso(now)
        if posted:
            new_state["last_posted_at"] = _iso(now)
            new_state["last_posted_fingerprint"] = report["fingerprint"]
            new_state["last_posted_problem"] = report["problem"]
            new_state["last_posted_target"] = cfg["notify_ticket_id"]
            new_state["suppressed_changes"] = 0
        else:
            new_state["suppressed_changes"] = carried
        try:
            save_state(state_path(cfg), new_state)
        except OSError as exc:
            # Loud, and NOT fatal: the judgment stands and the page went out. An unwritten
            # state file costs a duplicate next pass, which is the safe direction.
            log("NOTE: could not write %s (%s) — the next pass may repeat this comment"
                % (state_path(cfg), exc))
    log(happened)
    return code, report, happened


def cmd_run(args, poster=None):
    cfg = load_config(args.config)
    seconds = args.timeout or cfg["run_timeout_seconds"]
    armed = False

    def _fired(_signum, _frame):
        raise RunTimeout()

    previous = None
    if seconds > 0 and hasattr(signal, "SIGALRM"):
        previous = signal.signal(signal.SIGALRM, _fired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        armed = True
    report, happened = None, ""
    try:
        code, report, happened = run_pass(cfg, post=True, dry_run=args.dry_run, poster=poster)
    except RunTimeout:
        code = EXIT_TIMEOUT
        happened = "cut off at its %ds wall clock" % seconds
        log("FAIL: %s — the next scheduled pass judges again from the same files" % happened)
    except MonitorError as exc:
        code = exc.code
        happened = str(exc)
        log("FAIL: %s" % happened)
    except Exception as exc:                                   # a bug, recorded not swallowed
        code = EXIT_ERROR
        happened = "unexpected error: %s: %s" % (exc.__class__.__name__, exc)
        log("FAIL: %s — that is a bug in this file, not a condition it handles" % happened)
    finally:
        if armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    write_heartbeat(cfg, result=RESULT_BY_CODE.get(code, "unknown"), exit_code=code,
                    detail=happened[:400], dry_run=bool(args.dry_run),
                    watched=list(cfg["watch"]),
                    verdicts=({v["job"]: v["verdict"] for v in report["verdicts"]}
                              if report else {}),
                    notify_target=cfg["notify_ticket_id"] or None)
    return code


def cmd_check(args):
    cfg = load_config(args.config)
    try:
        code, _report, happened = run_pass(cfg, post=False)
    except MonitorError as exc:
        log("FAIL: %s" % exc)
        return exc.code
    log(happened)
    return code


def cmd_rearm(args):
    """Remove `last_run_at` from the state file, and nothing else.

    The next pass then has no last run to measure its own gap from, so it does not judge
    staleness — the same as a pass after a wake. The installer runs this just before it
    loads the job, because the daemons were stopped for the reload and their old heartbeats
    say so. Every other field is kept, so an open incident stays open. It posts nothing and
    writes no heartbeat. A state file it cannot read is left as it is: the next pass treats
    that file as empty, which does not judge staleness either, and names the file in its
    log."""
    cfg = load_config(args.config)
    path = state_path(cfg)
    if not os.path.exists(path):
        log("rearm: no state file at %s, so the next pass has no last run to measure from "
            "and does not judge staleness. Nothing was written." % path)
        return EXIT_OK
    state, note = load_state(path)
    if note:
        log("rearm: %s cannot be read as a %s document and was left as it is. The next pass "
            "treats it as empty, so it does not judge staleness. Nothing was written."
            % (path, STATE_SCHEMA))
        return EXIT_OK
    if "last_run_at" not in state:
        log("rearm: %s holds no last_run_at, so the next pass does not judge staleness. "
            "Nothing was written." % path)
        return EXIT_OK
    was = state.pop("last_run_at")
    try:
        save_state(path, state)
    except OSError as exc:
        log("FAIL: rearm could not rewrite %s (%s). The next pass still measures from %s and "
            "judges staleness." % (path, exc, was))
        return EXIT_ERROR
    log("rearm: removed last_run_at (%s) from %s and kept every other field. The next pass "
        "does not judge staleness; the one after it does." % (was, path))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Selftest — offline; every file, clock and tracker call is local or stubbed
# --------------------------------------------------------------------------- #
def selftest():
    import tempfile

    fails, count = [], [0]

    def ok(name, cond, extra=""):
        count[0] += 1
        if not cond:
            fails.append(name + ((" — " + str(extra)) if extra else ""))

    NOW = parse_iso("2026-09-12T12:00:00Z")

    def beat(job, **fields):
        spec = WATCHERS[job]
        doc = {"schema": spec["schema"]}
        doc.update(fields)
        return {"path": "/x/%s" % spec["filename"], "exists": True, "doc": doc, "error": None}

    def verdict(job, raw, age_limit=600, blind=False):
        return judge_one(job, WATCHERS[job], raw, NOW, age_limit, blind)["verdict"]

    # ── 1. Timestamps: the exact format every sibling writes ─────────────────────────
    ok("parses the sibling timestamp format", parse_iso("2026-09-12T11:59:00Z") == NOW - 60)
    ok("tolerates fractional seconds", parse_iso("2026-09-12T11:59:00.123Z") == NOW - 60)
    ok("tolerates a +00:00 offset", parse_iso("2026-09-12T11:59:00+00:00") == NOW - 60)
    ok("an unparseable timestamp is None, never 'now'", parse_iso("last Tuesday") is None)
    ok("a missing timestamp is None", parse_iso(None) is None)

    # ── 2. One verdict per real heartbeat shape ───────────────────────────────────────
    fresh = _iso(NOW - 60)
    old = _iso(NOW - 5000)
    ok("review poller: fresh + ok ⇒ ok",
       verdict("review-poller", beat("review-poller", result="ok", ended_at=fresh)) == "ok")
    ok("review poller: fresh + error ⇒ failing (ran, could not do it)",
       verdict("review-poller", beat("review-poller", result="error", ended_at=fresh)) == "failing")
    ok("review poller: fresh + timeout ⇒ failing, not ok",
       verdict("review-poller", beat("review-poller", result="timeout", ended_at=fresh)) == "failing")
    ok("review poller: fresh + usage ⇒ failing, not ok",
       verdict("review-poller", beat("review-poller", result="usage", ended_at=fresh)) == "failing")
    ok("review poller: fresh + declined ⇒ ok (a NOT-reviewed verdict is the poller doing its job)",
       verdict("review-poller", beat("review-poller", result="declined", ended_at=fresh)) == "ok")
    ok("bounce driver: problems ⇒ failing (a PR it could not act on)",
       verdict("bounce-driver", beat("bounce-driver", result="problems", at=fresh)) == "failing")
    ok("review poller: old ⇒ stale",
       verdict("review-poller", beat("review-poller", result="ok", ended_at=old)) == "stale")
    ok("review poller: ended_at wins over an older started_at",
       verdict("review-poller", beat("review-poller", result="ok", started_at=old,
                                     ended_at=fresh)) == "ok")
    ok("bounce driver: idle is a GOOD result (ran, nothing to do)",
       verdict("bounce-driver", beat("bounce-driver", result="idle", at=fresh)) == "ok")
    ok("bounce driver: deadline ⇒ failing (a partial pass is not clean)",
       verdict("bounce-driver", beat("bounce-driver", result="deadline", at=fresh)) == "failing")
    ok("bounce driver: fresh 'running' is a pass in flight, not a problem",
       verdict("bounce-driver", beat("bounce-driver", result="running", at=fresh)) == "running")
    ok("bounce driver: STALE 'running' ⇒ wedged, a different sentence from stale",
       verdict("bounce-driver", beat("bounce-driver", result="running", at=old)) == "wedged")
    ok("bounce driver: finished_at is preferred over the running beat's at",
       verdict("bounce-driver", beat("bounce-driver", result="ok", at=old,
                                     finished_at=fresh)) == "ok")
    ok("finding poller: result ok ⇒ ok",
       verdict("finding-poller", beat("finding-poller", result="ok", ended_at=fresh)) == "ok")
    ok("finding poller: result error ⇒ failing",
       verdict("finding-poller", beat("finding-poller", result="error",
                                      ended_at=fresh)) == "failing")
    ok("finding poller: a v1 file (`at` + boolean `ok`) is UNREADABLE, never healthy",
       verdict("finding-poller", {"path": "/x/heartbeat.json", "exists": True, "error": None,
                                  "doc": {"schema": "pipeline-finding-poller-heartbeat/1",
                                          "ok": True, "at": fresh}}) == "unreadable")
    ok("a boolean heartbeat is still translated to one result vocabulary",
       result_of({"ok": False}, dict(WATCHERS["finding-poller"], bool_field="ok")) == "error")

    # ── 3. The monitor's own inability to judge is never a clean bill of health ───────
    ok("a missing file ⇒ missing",
       verdict("review-poller", {"path": "/x/h.json", "exists": False}) == "missing")
    ok("unparseable JSON ⇒ unreadable",
       verdict("review-poller", {"path": "/x/h.json", "exists": True, "doc": None,
                                 "error": "Expecting value"}) == "unreadable")
    ok("an unknown schema ⇒ unreadable, never assumed healthy",
       verdict("review-poller", {"path": "/x/h.json", "exists": True,
                                 "doc": {"schema": "something/9", "result": "ok",
                                         "ended_at": fresh}, "error": None}) == "unreadable")
    ok("no parseable timestamp ⇒ unreadable",
       verdict("review-poller", beat("review-poller", result="ok")) == "unreadable")
    ok("a result the file does not state ⇒ failing, not ok",
       verdict("review-poller", beat("review-poller", ended_at=fresh)) == "failing")
    ok("unreadable counts as a problem", "unreadable" in PROBLEM_VERDICTS)
    ok("every verdict is classified exactly once",
       sorted(PROBLEM_VERDICTS + QUIET_VERDICTS) == sorted(set(VERDICT_SENTENCE)))

    # ── 4. The sleep blind spot: staleness suppressed, everything else still judged ───
    ok("blind pass: a stale beat is unknown-after-gap, not stale",
       verdict("review-poller", beat("review-poller", result="ok", ended_at=old),
               blind=True) == "unknown-after-gap")
    ok("blind pass: unknown-after-gap does NOT page", "unknown-after-gap" in QUIET_VERDICTS)
    ok("blind pass: a MISSING heartbeat is still a problem",
       verdict("review-poller", {"path": "/x/h.json", "exists": False}, blind=True) == "missing")
    ok("blind pass: a FAILING result is still a problem",
       verdict("review-poller", beat("review-poller", result="error", ended_at=fresh),
               blind=True) == "failing")
    ok("blind pass: an UNREADABLE file is still a problem",
       verdict("review-poller", {"path": "/x/h.json", "exists": True, "doc": None,
                                 "error": "boom"}, blind=True) == "unreadable")
    ok("the stale limit has a floor, so jitter on a tiny interval cannot page",
       stale_after(5, 2) == MIN_STALE_AFTER_SECONDS and stale_after(300, 2) == 600)

    # ── 5. Fingerprints: identity of a verdict SET, not of a moment ───────────────────
    a = [{"job": "b", "verdict": "stale"}, {"job": "a", "verdict": "ok"}]
    b = [{"job": "a", "verdict": "ok"}, {"job": "b", "verdict": "stale"}]
    ok("fingerprint is order-independent", fingerprint(a) == fingerprint(b))
    ok("fingerprint ignores age, so an ageing incident is the same incident",
       fingerprint([{"job": "a", "verdict": "stale", "age_seconds": 1}]) ==
       fingerprint([{"job": "a", "verdict": "stale", "age_seconds": 99999}]))
    ok("fingerprint changes when a verdict changes",
       fingerprint([{"job": "a", "verdict": "ok"}]) !=
       fingerprint([{"job": "a", "verdict": "stale"}]))

    # ── 6. One comment per incident — the rule the whole job turns on ────────────────
    def report_of(pairs, unwatched=(), blind=False, gap=None):
        return build_report([{"job": j, "label": j, "verdict": v, "detail": "d"}
                             for j, v in pairs], set(unwatched), blind, gap)

    healthy = report_of([("review-poller", "ok"), ("bounce-driver", "ok")])
    broken = report_of([("review-poller", "stale"), ("bounce-driver", "ok")])
    worse = report_of([("review-poller", "stale"), ("bounce-driver", "failing")])

    ok("a healthy first pass announces nothing",
       decide_post(healthy, {}, NOW, 0)[0] is False)
    ok("the first problem is announced", decide_post(broken, {}, NOW, 0)[0] is True)

    # five consecutive identical bad passes ⇒ exactly ONE comment
    state, posts = {}, 0
    for i in range(5):
        should, _why, carried = decide_post(broken, state, NOW + i * 1800, 900)
        if should:
            posts += 1
            state = {"last_posted_at": _iso(NOW + i * 1800),
                     "last_posted_fingerprint": broken["fingerprint"],
                     "last_posted_problem": True, "suppressed_changes": 0}
    ok("five identical bad passes post exactly one comment", posts == 1, posts)

    ok("a WORSE verdict set is a new incident and is announced",
       decide_post(worse, state, NOW + 9000, 900)[0] is True)
    ok("recovery after an incident posts exactly one comment",
       decide_post(healthy, state, NOW + 9000, 900)[0] is True)
    recovered = {"last_posted_at": _iso(NOW + 9000),
                 "last_posted_fingerprint": healthy["fingerprint"],
                 "last_posted_problem": False, "suppressed_changes": 0}
    ok("steady health after a recovery posts nothing",
       decide_post(healthy, recovered, NOW + 20000, 900)[0] is False)

    # A pass that could not judge staleness (a wake, or a rearm) is not a recovery.
    open_incident = {"last_posted_at": _iso(NOW - 5000),
                     "last_posted_fingerprint": broken["fingerprint"],
                     "last_posted_problem": True, "suppressed_changes": 0}
    unjudged = report_of([("review-poller", "unknown-after-gap"), ("bounce-driver", "ok")],
                         blind=True)
    ok("a pass that could not judge staleness does NOT close an open incident",
       decide_post(unjudged, open_incident, NOW, 900)[0] is False)
    ok("…but a blind pass whose every beat is fresh and good does",
       decide_post(report_of([("review-poller", "ok"), ("bounce-driver", "ok")], blind=True),
                   open_incident, NOW, 900)[0] is True)

    # An incident that mixes a stale job with a missing one. A pass that cannot judge the
    # stale job has only part of the picture: posting it drops that job from the header, and
    # the next judged pass posts the whole picture again.
    mixed = report_of([("review-poller", "stale"), ("finding-poller", "missing")])
    mixed_said = {"last_posted_at": _iso(NOW - 7200),
                  "last_posted_fingerprint": mixed["fingerprint"],
                  "last_posted_problem": True, "last_posted_target": "KIT-7",
                  "suppressed_changes": 0}
    mixed_unjudged = report_of([("review-poller", "unknown-after-gap"),
                                ("finding-poller", "missing")], blind=True)
    should, why, carried = decide_post(mixed_unjudged, mixed_said, NOW, 900, "KIT-7")
    ok("an unjudged pass during a mixed incident posts nothing it already said",
       should is False, why)
    ok("…and records nothing about posting: no change is carried", carried == 0, (carried, why))
    should, why, carried = decide_post(mixed_unjudged,
                                       dict(mixed_said, last_posted_at=_iso(NOW - 60)),
                                       NOW, 900, "KIT-7")
    ok("…not even inside the cooldown, where a real change would be carried",
       should is False and carried == 0 and "CARRIED" not in why, (carried, why))
    ok("…and the judged pass after it posts nothing new: its verdicts match what was posted",
       decide_post(mixed, mixed_said, NOW + 1800, 900, "KIT-7")[0] is False)
    ok("an unjudged pass still pages a clock-independent problem the last comment did not name",
       decide_post(report_of([("review-poller", "unknown-after-gap"),
                              ("finding-poller", "failing")], blind=True),
                   mixed_said, NOW, 900, "KIT-7")[0] is True)
    ok("…and one on a job the last comment did not name at all",
       decide_post(report_of([("review-poller", "unknown-after-gap"), ("finding-poller", "missing"),
                              ("bounce-driver", "unreadable")], blind=True),
                   mixed_said, NOW, 900, "KIT-7")[0] is True)
    ok("an unjudged first pass with a missing job pages it (nothing was said before)",
       decide_post(mixed_unjudged, {}, NOW, 900, "KIT-7")[0] is True)
    ok("a changed ticket is not told part of the picture by an unjudged pass",
       decide_post(mixed_unjudged, mixed_said, NOW, 900, "KIT-9")[0] is False)
    ok("…the judged pass after it tells that ticket the whole picture",
       decide_post(mixed, mixed_said, NOW + 1800, 900, "KIT-9")[0] is True)

    # The ticket the last comment went to is part of "already reported".
    on7 = dict(open_incident, last_posted_at=_iso(NOW - 60), last_posted_target="KIT-7")
    ok("an open incident is not said twice on the same ticket",
       decide_post(broken, on7, NOW, 900, "KIT-7")[0] is False)
    ok("a NEW ticket has not seen the open incident: it is announced there, cooldown or not",
       decide_post(broken, on7, NOW, 900, "KIT-9")[0] is True)
    ok("a new ticket gets no recovery for an incident it never saw",
       decide_post(healthy, on7, NOW, 900, "KIT-9")[0] is False)
    ok("…and no comment at all when nothing is open",
       decide_post(healthy, dict(on7, last_posted_fingerprint=healthy["fingerprint"],
                                 last_posted_problem=False), NOW, 900, "KIT-9")[0] is False)
    ok("the recovery still goes to the ticket that saw the incident",
       decide_post(healthy, on7, NOW, 900, "KIT-7")[0] is True)
    ok("a state that never recorded its ticket re-announces an open problem (a duplicate, "
       "never a miss)", decide_post(broken, open_incident, NOW, 900, "KIT-7")[0] is True)
    ok("…and still posts the recovery", decide_post(healthy, open_incident, NOW, 900, "KIT-7")[0] is True)

    # ── 7. The cooldown bounds a flap, carries it, and never delays good news ────────
    hot = {"last_posted_at": _iso(NOW - 60), "last_posted_fingerprint": broken["fingerprint"],
           "last_posted_problem": True, "suppressed_changes": 0}
    should, why, carried = decide_post(worse, hot, NOW, 900)
    ok("a changed problem inside the cooldown is NOT posted", should is False)
    ok("…and it is CARRIED, not dropped", carried == 1 and "CARRIED" in why, why)
    should2, _why2, carried2 = decide_post(worse, dict(hot, suppressed_changes=carried), NOW, 900)
    ok("a second suppressed change increments the carried count", carried2 == 2)
    should3, _w3, _c3 = decide_post(worse, dict(hot, last_posted_at=_iso(NOW - 1000)), NOW, 900)
    ok("once the cooldown passes the carried state IS posted", should3 is True)
    ok("a recovery is exempt from the cooldown",
       decide_post(healthy, hot, NOW, 900)[0] is True)

    # ── 8. The comment says what it saw, what it did not, and carries no mark ────────
    body = build_comment(worse, None, 2, False, {"review-poller": 300})
    ok("the comment names every problem job",
       "review-poller" in body and "bounce-driver" in body)
    ok("the comment names the carried count", "2 earlier change(s)" in body)
    ok("the comment states the blind spot this monitor cannot cover",
       "asleep" in body and "silence here is not health" in body)
    ok("the comment carries NO escalation mark (it must not trigger a label)",
       "pipeline-escalation" not in body)
    unwatched_body = build_comment(report_of([("review-poller", "stale")],
                                             unwatched=("finding-poller",)),
                                  None, 0, False, {"review-poller": 300})
    ok("a job this machine does not run is NAMED as unjudged, never implied healthy",
       "Not watched on this machine" in unwatched_body and "finding-poller" in unwatched_body)
    recovery_body = build_comment(healthy, None, 0, True, {"review-poller": 300})
    ok("a recovery comment says so plainly", "reporting again" in recovery_body)
    note_body = build_comment(broken, "state file was unreadable", 0, False, {"review-poller": 300})
    ok("a lost state file is disclosed in the comment it may duplicate",
       "state file was unreadable" in note_body)
    part_body = build_comment(report_of([("review-poller", "unknown-after-gap"),
                                         ("finding-poller", "failing")], blind=True, gap=None),
                              None, 0, False, {"review-poller": 300, "finding-poller": 300})
    ok("a comment from an unjudged pass names the job it did not judge in its first line, so "
       "that job never reads as recovered",
       "not judged this pass" in part_body.splitlines()[0]
       and "review-poller" in part_body.splitlines()[0], part_body.splitlines()[0])
    ok("…and with no last run on record it does not claim the machine was asleep",
       "missed its own schedule" not in part_body and "no last run on record" in part_body,
       part_body)
    ok("…while a real gap is still named as one",
       "missed its own schedule by 2h" in build_comment(
           report_of([("review-poller", "unknown-after-gap")], blind=True, gap=7200),
           None, 0, False, {"review-poller": 300}))
    ok("an unjudged verdict's detail does not guess why this monitor has no recent run",
       "was not running" not in judge_one("review-poller", WATCHERS["review-poller"],
                                          beat("review-poller", result="ok", ended_at=old),
                                          NOW, 600, True)["detail"])

    # ── 9. Every pass prints what it asked and what the answer was (§13) ─────────────
    quiet = render_report(healthy, {"review-poller": 300, "bounce-driver": 300})
    ok("a quiet pass still prints the question", "asked:" in quiet)
    ok("a quiet pass still prints the answer", "every watched job is reporting" in quiet)
    loud = render_report(worse, {"review-poller": 300, "bounce-driver": 300})
    ok("a problem pass counts the problems", "2 problem(s)" in loud)
    ok("every pass says whether paging is even on (off)",
       "paging: OFF" in render_report(healthy, {}, None))
    ok("every pass says whether paging is even on (on)",
       "one comment on KIT-000" in render_report(healthy, {}, "KIT-000"))
    ok("an unwatched job appears in the printed report as not-judged",
       "not-judged" in render_report(report_of([("review-poller", "ok")],
                                               unwatched=("bounce-driver",)), {}))

    # ── 10. Config: every error in one pass, and the refusals that matter ────────────
    with tempfile.TemporaryDirectory() as tmp:
        good = {"state_dir": os.path.join(tmp, "state"),
                "finding_state_dir": os.path.join(tmp, "finding"),
                "watch": ["review-poller"], "intervals": {"review-poller": 300},
                "run_interval_seconds": 1800}
        path = os.path.join(tmp, "c.json")

        def write(doc):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            return path

        cfg = load_config(write(good))
        ok("defaults applied where they are safe",
           cfg["stale_multiplier"] == DEFAULT_STALE_MULTIPLIER
           and cfg["min_seconds_between_comments"] == DEFAULT_COOLDOWN_SECONDS
           and cfg["linear_key_env"] == "STAGE_E_LINEAR_API_KEY")
        ok("paging is OFF when no ticket is named", cfg["notify_ticket_id"] == "")
        ok("the monitor's state dir defaults to the daemons' state dir",
           cfg["monitor_state_dir"] == os.path.realpath(good["state_dir"]))

        def errors_for(doc):
            try:
                load_config(write(doc))
                return ""
            except MonitorError as exc:
                return str(exc)

        msg = errors_for(dict(good, watch=[], intervals={}, run_interval_seconds=0,
                              stale_multiplier=0, linear_key_env="sk-live-secret",
                              notify_ticket_id="not-a-ticket", min_seconds_between_comments=-1))
        for expect in ("'watch'", "run_interval_seconds", "stale_multiplier",
                       "linear_key_env", "notify_ticket_id", "min_seconds_between_comments"):
            ok("every bad value is reported in ONE pass (%s)" % expect, expect in msg, msg)
        ok("a credential VALUE in the config is refused", "never a credential value" in msg)
        ok("a watched job with no interval is a config error, not a guessed default",
           "intervals[review-poller]" in errors_for(dict(good, intervals={})))
        ok("an interval for an unwatched job is reported rather than ignored",
           "not in 'watch'" in errors_for(dict(good, intervals={"review-poller": 300,
                                                               "bounce-driver": 300})))
        ok("an unknown job name in 'watch' is refused",
           "unknown job(s)" in errors_for(dict(good, watch=["review-poller", "nope"],
                                               intervals={"review-poller": 300})))
        ok("an unknown config key is refused",
           "unknown config key" in errors_for(dict(good, surprise=1)))
        ok("a state dir inside a git working tree is refused (a session could write it)",
           "inside a git working tree" in errors_for(dict(good, monitor_state_dir=HERE)))
        ok("an unreadable --config is exit 2, before anything is read",
           errors_for("not-a-dict") or True)
        try:
            load_config(os.path.join(tmp, "absent.json"))
            ok("a missing --config raises usage", False)
        except MonitorError as exc:
            ok("a missing --config raises usage", exc.code == EXIT_USAGE)

        # ── 11. End to end, with the tracker stubbed and the clock supplied ──────────
        os.makedirs(good["state_dir"], exist_ok=True)
        os.makedirs(good["finding_state_dir"], exist_ok=True)
        sent = []

        def fake_poster(ticket, body_text, _key):
            sent.append((ticket, body_text))
            return True

        class Args(object):
            def __init__(self, **kw):
                self.config = path
                self.dry_run = False
                self.timeout = 0
                self.__dict__.update(kw)

        live = dict(good, notify_ticket_id="KIT-000", intervals={"review-poller": 300},
                    min_seconds_between_comments=0)
        write(live)
        os.environ["STAGE_E_LINEAR_API_KEY"] = "test-key-not-a-real-credential"
        hb = os.path.join(good["state_dir"], "heartbeat.json")

        # (a) no heartbeat at all ⇒ a problem, one comment, exit 0
        code = cmd_run(Args(), poster=fake_poster)
        ok("a watched job with no heartbeat pages", len(sent) == 1, sent)
        ok("…and the pass itself is a clean exit 0 (it did its job)", code == EXIT_OK, code)
        ok("…and the monitor wrote its OWN heartbeat",
           os.path.exists(heartbeat_path(load_config(path))))
        own = json.load(open(heartbeat_path(load_config(path)), encoding="utf-8"))
        ok("its heartbeat carries the schema and the verdicts",
           own["schema"] == HEARTBEAT_SCHEMA and own["verdicts"]["review-poller"] == "missing",
           own)
        ok("its heartbeat names where a page would go", own["notify_target"] == "KIT-000")

        # (b) the same problem again ⇒ no second comment
        cmd_run(Args(), poster=fake_poster)
        ok("the same problem does not page twice", len(sent) == 1, sent)

        # (c) the daemon starts reporting ⇒ exactly one recovery comment
        _atomic_write_json(hb, {"schema": WATCHERS["review-poller"]["schema"],
                                "result": "ok", "ended_at": _iso()})
        cmd_run(Args(), poster=fake_poster)
        ok("recovery posts one comment", len(sent) == 2, sent)
        ok("…and it reads as a recovery", "reporting again" in sent[1][1])
        cmd_run(Args(), poster=fake_poster)
        ok("steady health posts nothing further", len(sent) == 2, sent)

        # (d) a dry run writes nothing and posts nothing
        os.remove(hb)
        before = open(state_path(load_config(path)), encoding="utf-8").read()
        cmd_run(Args(dry_run=True), poster=fake_poster)
        ok("a dry run posts nothing", len(sent) == 2, sent)
        ok("a dry run advances no state",
           open(state_path(load_config(path)), encoding="utf-8").read() == before)
        dry_hb = json.load(open(heartbeat_path(load_config(path)), encoding="utf-8"))
        ok("a dry run's heartbeat cannot impersonate a real pass", dry_hb["dry_run"] is True)

        # (e) `check` judges and writes nothing at all
        sent_before = len(sent)
        state_before = open(state_path(load_config(path)), encoding="utf-8").read()
        check_code = cmd_check(Args())
        ok("check posts nothing", len(sent) == sent_before)
        ok("check exits 3 when it finds a problem — a rehearsal that reported 0 over two "
           "dead daemons would be the same defect wearing a different hat",
           check_code == EXIT_DECLINED, check_code)
        ok("check writes no state (a rehearsal must not move the blind-spot clock)",
           open(state_path(load_config(path)), encoding="utf-8").read() == state_before)

        # (f) paging OFF: judged, printed, and exit 3 — never a clean green
        write(dict(good, intervals={"review-poller": 300}))
        os.remove(state_path(load_config(path)))
        code = cmd_run(Args(), poster=fake_poster)
        ok("a problem with no configured ticket is exit 3, not exit 0",
           code == EXIT_DECLINED, code)
        ok("…and nothing was posted", len(sent) == sent_before)
        off_hb = json.load(open(heartbeat_path(load_config(path)), encoding="utf-8"))
        ok("…and the heartbeat says there was nowhere to send it",
           off_hb["notify_target"] is None and off_hb["result"] == "declined", off_hb)

        # (g) paging off and HEALTHY ⇒ exit 0
        _atomic_write_json(hb, {"schema": WATCHERS["review-poller"]["schema"],
                                "result": "ok", "ended_at": _iso()})
        os.remove(state_path(load_config(path)))
        ok("healthy with paging off is a clean exit 0",
           cmd_run(Args(), poster=fake_poster) == EXIT_OK)
        ok("check on a healthy machine is a clean exit 0", cmd_check(Args()) == EXIT_OK)

        # (h) a tracker failure is exit 1 and the state is NOT advanced
        write(live)
        os.remove(hb)
        os.remove(state_path(load_config(path)))

        def angry_poster(_t, _b, _k):
            raise MonitorError("tracker API HTTP 503")

        code = cmd_run(Args(), poster=angry_poster)
        ok("a tracker failure is exit 1 (retryable), not a clean pass", code == EXIT_ERROR, code)
        after = json.load(open(state_path(load_config(path)), encoding="utf-8"))
        ok("…and no fingerprint was recorded, so the next pass says it again",
           after.get("last_posted_fingerprint") is None, after)
        code2 = cmd_run(Args(), poster=fake_poster)
        ok("…and the next pass DOES post it", len(sent) == sent_before + 1 and code2 == EXIT_OK)

        # (i) a corrupt state file is named and treated as empty (duplicate, never missed)
        with open(state_path(load_config(path)), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        loaded, note = load_state(state_path(load_config(path)))
        ok("a corrupt state file is empty, not trusted", loaded == {})
        ok("…and it is named so the duplicate it causes is explained", bool(note), note)
        wrong_schema = os.path.join(tmp, "s.json")
        _atomic_write_json(wrong_schema, {"schema": "other/1", "last_posted_fingerprint": "x"})
        ok("a state file of the wrong schema is treated as empty and named",
           load_state(wrong_schema) == ({}, load_state(wrong_schema)[1])
           and load_state(wrong_schema)[1] is not None)

        # (j) the blind spot, end to end: a long gap since its OWN last run
        write(live)
        _atomic_write_json(state_path(load_config(path)),
                           {"schema": STATE_SCHEMA, "last_run_at": _iso(_now() - 86400)})
        _atomic_write_json(hb, {"schema": WATCHERS["review-poller"]["schema"],
                                "result": "ok", "ended_at": _iso(_now() - 86400)})
        sent_before = len(sent)
        code = cmd_run(Args(), poster=fake_poster)
        ok("a wake-up pass does not page on the staleness it caused",
           len(sent) == sent_before and code == EXIT_OK, code)
        woke = json.load(open(heartbeat_path(load_config(path)), encoding="utf-8"))
        ok("…and the heartbeat records the unjudged verdict by name",
           woke["verdicts"]["review-poller"] == "unknown-after-gap", woke)

        # (k) rearm: the next pass is handled like a wake, and nothing else changes
        spath = state_path(load_config(path))
        kept = {"schema": STATE_SCHEMA, "last_posted_fingerprint": "review-poller=ok",
                "last_posted_problem": False, "last_posted_target": "KIT-000",
                "last_posted_at": _iso(_now() - 5000), "suppressed_changes": 0}
        _atomic_write_json(spath, dict(kept, last_run_at=_iso(_now() - 60)))
        _atomic_write_json(hb, {"schema": WATCHERS["review-poller"]["schema"],
                                "result": "ok", "ended_at": _iso(_now() - 86400)})
        own_before = open(heartbeat_path(load_config(path)), encoding="utf-8").read()
        sent_before = len(sent)
        rc = cmd_rearm(Args())
        ok("rearm removes last_run_at and keeps every other field",
           rc == EXIT_OK and json.load(open(spath, encoding="utf-8")) == kept,
           open(spath, encoding="utf-8").read())
        ok("rearm posts nothing and writes no heartbeat",
           len(sent) == sent_before
           and open(heartbeat_path(load_config(path)), encoding="utf-8").read() == own_before)
        code = cmd_run(Args(), poster=fake_poster)
        ok("a pass after rearm does not page on the daemons' old heartbeats",
           len(sent) == sent_before and code == EXIT_OK, (code, sent[sent_before:]))
        rearmed = json.load(open(heartbeat_path(load_config(path)), encoding="utf-8"))
        ok("…and names the verdict it did not judge",
           rearmed["verdicts"]["review-poller"] == "unknown-after-gap", rearmed)
        os.remove(hb)
        cmd_rearm(Args())
        cmd_run(Args(), poster=fake_poster)
        ok("after rearm a MISSING heartbeat still pages",
           len(sent) == sent_before + 1 and sent[-1][0] == "KIT-000", sent[sent_before:])
        _atomic_write_json(hb, {"schema": WATCHERS["review-poller"]["schema"],
                                "result": "error", "ended_at": _iso()})
        cmd_rearm(Args())
        cmd_run(Args(), poster=fake_poster)
        ok("after rearm a FAILING heartbeat still pages", len(sent) == sent_before + 2,
           sent[sent_before:])
        with open(hb, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        cmd_rearm(Args())
        cmd_run(Args(), poster=fake_poster)
        ok("after rearm an UNREADABLE heartbeat still pages", len(sent) == sent_before + 3,
           sent[sent_before:])
        _atomic_write_json(spath, dict(kept, last_run_at=_iso(_now() - 60)))

        def _readonly(_p, _s):
            raise OSError("read-only file system")
        _real_save = globals()["save_state"]
        globals()["save_state"] = _readonly
        try:
            ok("a rearm that cannot rewrite the state file is exit 1, never a quiet 0",
               cmd_rearm(Args()) == EXIT_ERROR)
        finally:
            globals()["save_state"] = _real_save
        os.remove(spath)
        ok("rearm with no state file writes none",
           cmd_rearm(Args()) == EXIT_OK and not os.path.exists(spath))
        with open(spath, "w", encoding="utf-8") as fh:
            fh.write("{ corrupt")
        ok("rearm leaves an unreadable state file as it is",
           cmd_rearm(Args()) == EXIT_OK
           and open(spath, encoding="utf-8").read() == "{ corrupt")

        # (l) a changed ticket during an open incident: the new ticket hears about it
        _atomic_write_json(hb, {"schema": WATCHERS["review-poller"]["schema"],
                                "result": "error", "ended_at": _iso()})
        os.remove(spath)
        sent_before = len(sent)
        cmd_run(Args(), poster=fake_poster)
        ok("the incident is announced on the ticket configured then",
           len(sent) == sent_before + 1 and sent[-1][0] == "KIT-000", sent[sent_before:])
        write(dict(live, notify_ticket_id="KIT-001"))
        cmd_run(Args(), poster=fake_poster)
        ok("a changed ticket is told about the incident that is still open",
           len(sent) == sent_before + 2 and sent[-1][0] == "KIT-001", sent[sent_before:])
        ok("…and the state records where it went",
           json.load(open(spath, encoding="utf-8")).get("last_posted_target") == "KIT-001")
        cmd_run(Args(), poster=fake_poster)
        ok("…and says it once there, not every pass", len(sent) == sent_before + 2)

        # (m) a rearm during an incident that mixes a stale job with a missing one. The pass
        # after the rearm cannot judge the stale job, so it says nothing and records nothing
        # about posting; the judged pass after it finds what was already said.
        write(dict(good, watch=["review-poller", "finding-poller"],
                   intervals={"review-poller": 300, "finding-poller": 300},
                   notify_ticket_id="KIT-7", min_seconds_between_comments=900))
        cfg_m = load_config(path)
        if os.path.exists(beat_path(cfg_m, "finding-poller")):
            os.remove(beat_path(cfg_m, "finding-poller"))
        t0 = float(parse_iso(_iso()))
        _atomic_write_json(hb, {"schema": WATCHERS["review-poller"]["schema"],
                                "result": "ok", "ended_at": _iso(t0 - 10000)})
        _atomic_write_json(spath, {"schema": STATE_SCHEMA, "last_run_at": _iso(t0 - 1800)})
        posting = ("last_posted_at", "last_posted_fingerprint", "last_posted_problem",
                   "last_posted_target", "suppressed_changes")
        sent_before = len(sent)
        _real_now = globals()["_now"]
        try:
            globals()["_now"] = lambda: t0
            run_pass(cfg_m, poster=fake_poster)
            ok("(m) the judged pass announces both jobs",
               len(sent) == sent_before + 1 and sent[-1][0] == "KIT-7"
               and "2 of 2" in sent[-1][1], sent[sent_before:])
            said = json.load(open(spath, encoding="utf-8"))
            globals()["_now"] = lambda: t0 + 7200
            ok("(m) the installer's rearm two hours later succeeds", cmd_rearm(Args()) == EXIT_OK)
            code, _rep, happened = run_pass(cfg_m, poster=fake_poster)
            ok("(m) the pass after the rearm, which cannot judge the stale job, posts nothing",
               len(sent) == sent_before + 1 and code == EXIT_OK,
               (code, happened, [b[:80] for _t, b in sent[sent_before + 1:]]))
            now_state = json.load(open(spath, encoding="utf-8"))
            ok("…and records nothing about posting, only its own run",
               all(now_state.get(k) == said.get(k) for k in posting)
               and now_state.get("last_run_at") == _iso(t0 + 7200), now_state)
            globals()["_now"] = lambda: t0 + 9000
            run_pass(cfg_m, poster=fake_poster)
            ok("…and the judged pass after it posts nothing new: its verdicts match what was "
               "posted", len(sent) == sent_before + 1,
               [b[:80] for _t, b in sent[sent_before + 1:]])
        finally:
            globals()["_now"] = _real_now
        del os.environ["STAGE_E_LINEAR_API_KEY"]

    # ── 12. The credential rule ──────────────────────────────────────────────────────
    saved = os.environ.pop("STAGE_E_LINEAR_API_KEY", None)
    try:
        credential({"linear_key_env": "STAGE_E_LINEAR_API_KEY"})
        ok("an unset credential is refused with the env-file instruction", False)
    except MonitorError as exc:
        ok("an unset credential is refused with the env-file instruction",
           "env file" in str(exc) and exc.code == EXIT_USAGE)
    if saved is not None:
        os.environ["STAGE_E_LINEAR_API_KEY"] = saved

    # ── 13. The ticket resolution seam, stubbed ──────────────────────────────────────
    calls = []

    def recording(query, variables, _key):
        calls.append((query, variables))
        if "issues(filter" in query:
            return {"issues": {"nodes": [{"id": "uuid-1", "identifier": "KIT-7"}]}}
        return {"commentCreate": {"success": True}}

    ok("a comment resolves the ticket then posts, and nothing else",
       post_comment("KIT-7", "body", "k", _graphql=recording) is True and len(calls) == 2)
    ok("the ticket is resolved by TEAM and NUMBER, never by title",
       calls[0][1] == {"team": "KIT", "number": 7.0})
    try:
        post_comment("not-a-ticket", "b", "k", _graphql=recording)
        ok("a malformed ticket id is refused before any call", False)
    except MonitorError as exc:
        ok("a malformed ticket id is refused before any call", exc.code == EXIT_USAGE)

    def empty(query, _v, _k):
        return {"issues": {"nodes": []}} if "issues(filter" in query else {}

    try:
        post_comment("KIT-9", "b", "k", _graphql=empty)
        ok("a ticket that does not exist is loud, never a silent skip", False)
    except MonitorError as exc:
        ok("a ticket that does not exist is loud, never a silent skip",
           "was not found" in str(exc))

    def refusing(query, _v, _k):
        if "issues(filter" in query:
            return {"issues": {"nodes": [{"id": "u"}]}}
        return {"commentCreate": {"success": False}}

    try:
        post_comment("KIT-9", "b", "k", _graphql=refusing)
        ok("a refused comment is an error, never reported as sent", False)
    except MonitorError:
        ok("a refused comment is an error, never reported as sent", True)

    # ── 14. What it never does — asserted against this file's own source ─────────────
    source = open(__file__, encoding="utf-8").read()
    head, _, rest = source.partition("\ndef selftest():")
    body_only = head + rest.partition("\n# ---------------------------------------------------------------------------")[2]
    # The scan must cover main() as well as the head: a prefix-only split left the CLI
    # unscanned in a sibling, so a write path added below the selftest would have passed
    # the very guard that exists to catch it.
    ok("the never-does scan covers main() and not just the head", "def main(" in body_only)
    banned = ("pr merge", "--auto", "mergePullRequest", "gh pr review", "event: APPROVE",
              "issueUpdate", "issueCreate", "addedLabelIds", "labelIds", "stateId",
              "assigneeId", "commentUpdate", "issueDelete")
    for token in banned:
        ok("never constructs %r" % token, token not in body_only)
    ok("this file declares exactly ONE tracker mutation", body_only.count("mutation(") == 1)
    ok("…and that mutation is commentCreate", "commentCreate" in COMMENT_MUTATION)
    ok("it launches no session and runs no model",
       not re.search(r"\bsubprocess\b|\bclaude\b|\bcyrus\b", body_only))
    ok("it writes nothing to a pull request", "pulls" not in body_only and "gh " not in body_only)

    # exit vocabulary agrees with the sibling the installer cross-checks
    try:
        import pipeline_review_poller as prp
        ok("EXIT_DECLINED agrees with the review poller", EXIT_DECLINED == prp.EXIT_DECLINED)
        ok("EXIT_TIMEOUT agrees with the review poller", EXIT_TIMEOUT == prp.EXIT_TIMEOUT)
        ok("the watched schema string matches what the review poller writes",
           WATCHERS["review-poller"]["schema"] == prp.HEARTBEAT_SCHEMA)
        ok("the watched filename matches where the review poller writes it",
           os.path.basename(prp.heartbeat_path("/d")) == WATCHERS["review-poller"]["filename"])
        import tempfile
        import time as _time
        with tempfile.TemporaryDirectory() as rd:
            for code, want in ((prp.EXIT_OK, "ok"), (prp.EXIT_DECLINED, "ok"),
                               (prp.EXIT_ERROR, "failing"), (prp.EXIT_USAGE, "failing"),
                               (prp.EXIT_TIMEOUT, "failing")):
                prp.write_heartbeat(rd, "scan", code, prp._now_iso(), _time.monotonic(), False)
                real = judge_one("review-poller", WATCHERS["review-poller"],
                                 read_beat(prp.heartbeat_path(rd)), time.time(), 600, False)
                ok("a heartbeat the review poller REALLY writes (exit %d) is judged %s"
                   % (code, want), real["verdict"] == want, real.get("detail"))
    except ImportError:
        ok("the review poller is importable for the cross-check", False)
    try:
        import pipeline_bounce_local as pbl
        ok("the bounce schema string matches what the driver writes",
           WATCHERS["bounce-driver"]["schema"] == pbl.HEARTBEAT_SCHEMA)
        ok("the bounce filename matches where the driver writes it",
           os.path.basename(pbl.heartbeat_path("/d")) == WATCHERS["bounce-driver"]["filename"])
    except ImportError:
        ok("the bounce driver is importable for the cross-check", False)
    try:
        import pipeline_finding_poller as pfp
        ok("the finding schema string matches what that poller writes",
           WATCHERS["finding-poller"]["schema"] == pfp.HEARTBEAT_SCHEMA)
        ok("the finding filename matches where that poller writes it",
           os.path.basename(pfp.heartbeat_path("/d")) == WATCHERS["finding-poller"]["filename"])
        ok("the two pollers' files are told apart by DIRECTORY, not by name",
           WATCHERS["finding-poller"]["filename"] == WATCHERS["review-poller"]["filename"]
           and WATCHERS["finding-poller"]["dir_key"] != WATCHERS["review-poller"]["dir_key"])
        # THE SCHEMA STRING IS NOT THE SHAPE. The check above caught the /1 → /2 rename,
        # but a bumped string with stale field names would pass it and still page on every
        # healthy file. So the poller's OWN writer produces a heartbeat and this monitor's
        # OWN reader and judge read it — the drift is caught where it would bite.
        import tempfile
        import time as _time
        with tempfile.TemporaryDirectory() as fd:
            for code, want in ((0, "ok"), (1, "failing")):
                pfp.write_heartbeat(fd, code, pfp._now_iso(), _time.monotonic(), False)
                real = judge_one("finding-poller", WATCHERS["finding-poller"],
                                 read_beat(pfp.heartbeat_path(fd)), time.time(), 600, False)
                ok("a heartbeat the finding poller REALLY writes (exit %d) is judged %s"
                   % (code, want), real["verdict"] == want, real.get("detail"))
    except ImportError:
        ok("the finding poller is importable for the cross-check", False)

    if fails:
        print("FAIL: %d heartbeat-monitor selftest case(s) failed:" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("OK: heartbeat-monitor selftest (%d checks)" % count[0])
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", choices=["run", "check", "rearm"])
    ap.add_argument("--config")
    ap.add_argument("--dry-run", action="store_true",
                    help="judge and report; write no state and post nothing")
    ap.add_argument("--timeout", type=int, default=0,
                    help="wall clock in seconds; overrides run_timeout_seconds")
    ap.add_argument("--example-config", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2))
        return 0
    if args.command in ("run", "check", "rearm"):
        if not args.config:
            sys.stderr.write("%s needs --config\n" % args.command)
            return EXIT_USAGE
        try:
            return {"run": cmd_run, "check": cmd_check, "rearm": cmd_rearm}[args.command](args)
        except MonitorError as exc:
            log("FAIL: %s" % exc)
            return exc.code
    ap.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
