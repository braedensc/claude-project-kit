#!/usr/bin/env python3
"""The health watch: one pass of every check that should reach a person when something
goes wrong and nothing else would say so (KIT-236, KIT-238).

    python3 scripts/pipeline_watch.py run              # one pass: what the scheduled job runs
    python3 scripts/pipeline_watch.py run --dry-run    # measure and say; send and write nothing
    python3 scripts/pipeline_watch.py status           # the last pass, per kind
    python3 scripts/pipeline_watch.py install          # a person: load the hourly LaunchAgent
    python3 scripts/pipeline_watch.py uninstall
    python3 scripts/pipeline_watch.py --selftest

Settings: the same `update.conf` as scripts/pipeline_update.py (its watch keys are listed in
update.conf.example). Guide: docs/UPDATE-OPERATOR.md, "The health watch".

WHY.  On 2026-10-04 a Claude Code update changed what it tells hooks, and every desktop
session's guards misjudged it for six days; nobody was told (KIT-214). The same week the
dispatcher was three releases behind with nothing saying so. This job looks for that class
of fault every hour and tells the owner when the answer CHANGES — once per problem, and once
more when it clears.

WHAT IT CHECKS, one KIND each (a kind is what an alert is about):
  hooks       the kit hooks reported that they could not tell which checkout a session runs
              in (the Stop hook's `session-*__root-unresolved` records, last 24 hours)
  updates     `pipeline_update.py review` rows that are BEHIND, need a RESTART, or could not
              be measured — the dispatcher and Homebrew rows; the kit checkout is listed but
              never pages, because it moves with every merge. Re-measured every 20 hours.
  release     a dispatcher release newer than the version DISPATCHER_VERSION pins
  dispatcher  the dispatcher not answering /version on this machine, two passes running
  front-door  PUBLIC_STATUS_URL not answering 200 while the internet answers, two passes
              running. With no internet the kind is UNKNOWN and pages nothing: a laptop in a
              background wake has no network, and that is not an outage.
  disk        less than DISK_FLOOR_GB free on /
  temp        this account's temp space holds more than TEMP_CEILING_GB: Claude Code's
              per-session scratch (/tmp/claude-<uid>) and its own TMPDIR. On 4 Nov 2026 the
              first held 49.5 GB before the disk ran out, with nothing saying so (KIT-461).
              Another account's (the dispatcher's) cannot be read from here, and is not
              counted.
  conflict-waker  the owner's conflict waker, where CONFLICT_WAKER_STATE_DIR names its state
              directory: its heartbeat an hour old, a problem, or none, two passes running.
              Slack only, through the status file: it opens no GitHub issue (KIT-339)
  watcher-page  the watcher's urgent page job (KIT-549), where WATCHER_PAGE_HEARTBEAT_FILE names its
              page-heartbeat.json: older than WATCHER_PAGE_MAX_AGE_MINUTES, unreadable, in another
              shape, missing, or reporting that a page could not be sent (and by which route), two
              passes running. Slack only, through the status file: it opens no GitHub issue
  push-connection  the bridge's push-connection job (KIT-561), where PUSH_HEARTBEAT_FILE names its
              bridge-push-heartbeat.json as a file this account can read: older than ten minutes,
              unreadable, in another shape, missing, not connected to Slack, or silent for five
              minutes, two passes running. Slack only, through the status file: it opens no GitHub
              issue
  credentials the yearly credentials' expiry dates, which the owner writes down in
              CREDENTIAL_EXPIRIES (names and dates, never a token): any date within
              CREDENTIAL_WARN_DAYS, or past, fires. Upkeep: a GitHub issue, never a Slack page
              (KIT-579). Empty: no dates are watched, and every pass says so.
  memory      the machine's free memory under MEMORY_FREE_FLOOR_PCT, or swap over SWAP_CEILING_GB, two
              passes running (KIT-609). Read from macOS's own tools, no sudo; unreadable is unknown. A
              concern, not an outage: advisory in the status file, no GitHub issue, no page. The
              stand-up says one line about it.
  burn        the usage job's weekly window projected to its reset (BURN_WEEK_PCT), or its 5-hour window
              at BURN_FIVE_HOUR_PCT (KIT-609). Reads only BURN_USAGE_FILE; a figure it cannot read is
              unknown, never zero. Advisory like `memory`. Empty: not watched.
  notifier, monitor  the human-action notifier and the heartbeat monitor, where
              NOTIFIER_HEARTBEAT_FILE and MONITOR_HEARTBEAT_FILE name their heartbeats as files
              this account can read: an hour old, unreadable or missing, two passes running
              (KIT-286). Each opens a GitHub issue, since Slack is what a dead notifier cannot
              reach, and a dead monitor cannot page about the others.
A last kind, `watch-job` — this job itself stopped — cannot be reported by the job. The
heartbeat monitor watches the status file this job writes and pages when it goes stale.

WHERE IT SAYS SO.
  * GitHub: when a kind changes between firing and ok, it dispatches ALERT_WORKFLOW in
    ALERT_REPO (a PRIVATE repo — alert text carries machine state). That workflow opens or
    closes one issue per kind as github-actions[bot], with an @mention, so the owner is
    notified. It then waits for that run and checks it succeeded: "dispatch accepted" is not
    "issue filed". A failed send is not recorded as sent, so the next pass sends it again.
  * Slack: it writes HEALTH_STATUS_FILE (readable by the dispatcher's role account). The
    heartbeat monitor reads it and pages through the notifier, as it does for the daemons.
  Either one empty means that channel is OFF, and every pass says so.

EXIT CODES — contract §13.
  0   every kind is ok, and every change was delivered
  1   a change could NOT be delivered (it is sent again next pass)
  2   usage or config — nothing was checked
  3   refused — an agent environment asked to install or uninstall
  4   something could not be measured, and nothing is firing
  10  something is firing (and was delivered, or was already delivered)

WHAT IT NEVER DOES.  Change anything it reports on, read a credential, or update a
component. It reports; `pipeline_update.py` and a person update.
"""
import argparse
import calendar
import json
import os
import plistlib
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline_update as pu  # noqa: E402

se = pu.se
EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_FIRING = 0, 1, 2, 3, 4, 10
OK, FIRING, UNKNOWN = "ok", "firing", "unknown"

STATUS_SCHEMA = "pipeline-health-status/1"
STATE_SCHEMA = "pipeline-health-watch-state/1"
KINDS = ("hooks", "updates", "release", "dispatcher", "front-door", "disk", "temp",
         "conflict-waker", "watcher-page", "push-connection", "notifier", "monitor", "credentials",
         "memory", "burn")
# Read here for the heartbeat monitor, which cannot (KIT-339): the conflict waker is the
# owner's LaunchAgent, and its heartbeat sits in the owner's home. It reaches Slack through
# the status file alone. It opens no GitHub issue: the alert workflow knows a fixed list of
# kinds, and a deployed copy that does not know this one would fail every send.
SLACK_ONLY_KINDS = ("conflict-waker", "watcher-page", "push-connection", "memory", "burn")
WAKER_SCHEMA = "pr-conflict-waker-heartbeat/1"
# The watcher's page job (KIT-519) writes this beat. A page that could not be sent shows in it as
# `could-not-page` and the routes that failed, and nothing else reads it (KIT-549). The route names
# are a closed list, so nothing the file says is repeated but those two words.
PAGE_SCHEMA = "pipeline-watcher-page-heartbeat/1"
PAGE_ROUTES = ("slack", "github")
# The bridge's push-connection job (KIT-561) beats about every 30 seconds while it runs; the job's own
# fields say whether it is connected and when Slack was last heard from (a ping counts). Fixed words
# here: nothing the file says is repeated.
PUSH_SCHEMA = "pipeline-bridge-push-heartbeat/1"
PUSH_STALE_SECONDS = 600
PUSH_HEARD_SECONDS = 300
# The waker runs every 300 s, and a pass can run up to its 40-minute session timeout.
WAKER_STALE_SECONDS = 3600
# The notifier and the heartbeat monitor (KIT-286): the two clerks that page about everything
# else, which nothing else pages about. Each runs every few minutes, so an hour without a
# heartbeat is far past a late pass. Read from the file the conf names, where this account can
# read it; empty means that job is not watched, and the pass says so.
CLERKS = {
    "notifier": ("NOTIFIER_HEARTBEAT_FILE", "pipeline-notifier-heartbeat/1", "notifier",
                 "Pages, including this one's Slack side, may not be reaching you."),
    "monitor": ("MONITOR_HEARTBEAT_FILE", "pipeline-heartbeat-monitor-heartbeat/1",
                "heartbeat monitor", "Nothing is watching the daemons' heartbeats."),
}
CLERK_STALE_SECONDS = 3600
# Upkeep, not outage. These open and close their GitHub issue like any kind, but never make
# the status file a problem, so the heartbeat monitor never pages Slack about them: a Slack
# ping must mean something is broken (owner decision 2026-10-27, KIT-264). The status file
# still lists them, under `advisory`, for the tech lead and `status`.
UPKEEP_KINDS = ("updates", "release", "credentials", "memory", "burn")
UPDATE_REVIEW_EVERY = 20 * 3600       # the registry and Homebrew need not be asked hourly
DEBOUNCE = 2                          # a liveness kind fires after this many failing passes
HOOK_EVIDENCE_SECONDS = 24 * 3600
NET_PROBE_URL = "https://registry.npmjs.org/"
UNRESOLVED_SUFFIX = "__root-unresolved"
SUMMARY_MAX = 3500
RUN_FIND_TRIES = 12                   # x 5 s: how long to look for the run a dispatch started
RUN_WATCH_TIMEOUT = 300
TEMP_WALK_SECONDS = 60                # how long one pass may spend counting temp space

def diagnose(conf):
    """The read-only next step every alert carries. The Slack tech lead runs as the
    dispatcher's role account with an exact-command grant, so what it can read is the status
    file (written world-readable for that reason); the commands are for the owner's shell."""
    where = conf.get("HEALTH_STATUS_FILE") or "(no status file configured)"
    return ("Diagnose (read-only): the full findings are in %s. In Slack, ask the tech lead to "
            "read it and explain. In the kit checkout: `python3 scripts/pipeline_update.py "
            "review` and `python3 scripts/pipeline_watch.py status`. Anything that needs sudo, "
            "launchctl or an installer is printed for you, never run for you." % where)


def say(msg=""):
    se.say(msg)


def _now(env):
    return getattr(env, "now", time.time)()


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _parse_iso(text):
    try:
        return float(calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return None


def _home(env):
    return env.home


def state_path(env):
    return os.path.join(_home(env), ".pipeline-update", "watch-state.json")


def load_state(env):
    try:
        with open(state_path(env), encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and doc.get("schema") == STATE_SCHEMA:
            return doc
    except (OSError, ValueError):
        pass
    return {"schema": STATE_SCHEMA, "kinds": {}}


def _atomic_write(path, doc, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# The checks — each returns {"kind", "state", "summary"}
# --------------------------------------------------------------------------- #
def finding(kind, state, summary):
    return {"kind": kind, "state": state, "summary": summary}


def check_hooks(env, conf):
    """The Stop hook leaves `session-<id>__root-unresolved` in the anchor checkout's
    `.claude/.stop-pr-nag/` when it cannot tell which worktree a session runs in. A record
    from the last 24 hours fires. It is the one structured trace the hooks leave; the
    SessionStart warning reaches only the person watching that session."""
    roots = pu.split_list(conf.get("HOOK_CHECKOUTS")) or [conf.get("KIT_CHECKOUT") or KIT]
    now, hits = _now(env), []
    for root in roots:
        nag = os.path.join(root, ".claude", ".stop-pr-nag")
        try:
            names = os.listdir(nag)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return finding("hooks", UNKNOWN, "could not read %s: %s" % (nag, exc))
        for name in names:
            if not name.endswith(UNRESOLVED_SUFFIX):
                continue
            try:
                age = now - os.path.getmtime(os.path.join(nag, name))
            except OSError:
                continue
            if age <= HOOK_EVIDENCE_SECONDS:
                hits.append("%s (%s)" % (root, name[:-len(UNRESOLVED_SUFFIX)]))
    if hits:
        return finding("hooks", FIRING, (
            "In the last 24 hours the Stop hook could not tell which worktree %d session(s) "
            "ran in, so their guards judged the wrong checkout: %s. A harness change is the "
            "usual cause (KIT-214). Start a desktop session in a worktree: its first screen "
            "says what the hooks see." % (len(hits), "; ".join(sorted(hits)[:10]))))
    return finding("hooks", OK, "no unresolved session in the last 24 hours")


def _paging(rows):
    """The review rows that fire `updates`. A dispatcher that cannot be measured is not one:
    that is the `dispatcher` kind's to say."""
    return [r for r in rows if r["state"] in (pu.BEHIND, pu.RESTART)
            or (r["state"] == pu.UNKNOWN and not r["component"].startswith("dispatcher"))]


def check_updates(env, conf, state):
    now = _now(env)
    cached = state.get("review") or {}
    # The cache serves only while it says nothing is wrong. Anything it says IS wrong is
    # re-measured every pass, so a fix clears within the hour whichever channel carried the
    # alert (KIT-249, KIT-251). Healthy, it spares the registry and Homebrew an hourly question.
    if (cached.get("at") and now - cached["at"] < UPDATE_REVIEW_EVERY
            and not _paging(cached.get("rows") or [])):
        rows = cached.get("rows") or []
    else:
        rows = pu.review_dispatcher(env, conf) + pu.review_brew(env, conf)
        unmeasured = any(r["state"] == pu.UNKNOWN and r["component"].startswith("dispatcher")
                         for r in rows)
        # A measurement missing the dispatcher is never cached: cached, it would read as
        # "nothing behind" for 20 hours while the dispatcher is still behind (KIT-251).
        if not unmeasured:
            state["review"] = {"at": now, "rows": rows}
    paging = _paging(rows)
    if not paging:
        if any(r["state"] == pu.UNKNOWN and r["component"].startswith("dispatcher") for r in rows):
            # Neither a page nor a clear: a dispatcher mid-restart (an update stops it) must
            # not close an open `updates` alert (KIT-251).
            return finding("updates", UNKNOWN, "the dispatcher's version could not be read this "
                           "pass, so updates were not judged; the `dispatcher` kind says if it is down")
        return finding("updates", OK, "nothing behind")
    lines = ["%s: %s %s -> %s; update: %s" % (r["component"], r["state"], r["installed"],
                                               r["target"], r["command"] or r["note"])
             for r in paging]
    return finding("updates", FIRING, "Needs updating:\n" + "\n".join(lines))


def check_release(env, conf):
    if not conf.get("DISPATCHER_SERVICE") or conf.get("DISPATCHER_VERSION") == "latest":
        return finding("release", OK, "no pinned dispatcher version")
    latest = pu.registry_latest(env, conf["DISPATCHER_PACKAGE"])
    if latest is None:
        return finding("release", UNKNOWN, "the package registry did not say the latest version")
    pin = conf["DISPATCHER_VERSION"]
    if pu.vkey(latest) > pu.vkey(pin):
        return finding("release", FIRING, (
            "%s %s is out; DISPATCHER_VERSION pins %s. Compare the two versions before raising "
            "the pin (the 0.2.69 -> 0.2.73 review found boundary changes no version number "
            "shows, KIT-235)." % (conf["DISPATCHER_PACKAGE"], latest, pin)))
    return finding("release", OK, "the pin %s is the latest" % pin)


def check_credentials(env, conf):
    """The yearly credentials (KIT-579): no API says a token's own expiry and nothing
    warns as it nears, so the owner writes each date down in CREDENTIAL_EXPIRIES and this kind
    warns CREDENTIAL_WARN_DAYS ahead, and again once it has passed. A date is exact, so there is
    no debounce. Names and dates only: the conf holds no secret and neither does the alert."""
    import datetime
    clocks, problems = pu.credential_clocks(conf.get("CREDENTIAL_EXPIRIES"))
    if problems:
        return finding("credentials", UNKNOWN, "CREDENTIAL_EXPIRIES could not be read this pass, "
                                               "so no date was judged: " + "; ".join(problems))
    if not clocks:
        return finding("credentials", OK, "CREDENTIAL_EXPIRIES is not set: no credential dates are "
                                          "watched here")
    today = datetime.datetime.fromtimestamp(_now(env), datetime.timezone.utc).date()
    warn = int(conf.get("CREDENTIAL_WARN_DAYS") or 30)
    due = sorted(((when - today).days, name, when) for name, when in clocks
                 if (when - today).days <= warn)
    if not due:
        soonest = min((when, name) for name, when in clocks)
        return finding("credentials", OK, "none expires within %d days; the next is %s on %s" % (
            warn, soonest[1], soonest[0].isoformat()))
    lines = []
    for days, name, when in due:
        if days < 0:
            lines.append("%s expired on %s (%d day%s ago)" % (name, when.isoformat(), -days,
                                                              "" if days == -1 else "s"))
        elif days == 0:
            lines.append("%s expires today (%s)" % (name, when.isoformat()))
        else:
            lines.append("%s expires on %s (in %d day%s)" % (name, when.isoformat(), days,
                                                             "" if days == 1 else "s"))
    out = finding("credentials", FIRING, (
        "A credential is close to its end, or past it:\n" + "\n".join(lines)
        + "\nRotate it, then move its date in update.conf. The steps are in the runbook and on the "
          "tickets named in docs/UPDATE-OPERATOR.md."))
    # Which credentials are due. A change in the set sends again, so the open issue names the
    # new one too (it adds a comment and pages once more); the day counts changing do not.
    out["key"] = ",".join(name for _days, name, _when in due)
    return out


# The machine and the budget (KIT-609). Concerns, not outages: both are advisory in the status file, open no GitHub
# issue and page nobody; the stand-up reads them and says one line only when one is firing. Numbers only: the
# summaries carry percentages, gigabytes and times, nothing a file or a session wrote.
SWAP_RE = re.compile(r"\bused = ([0-9]+(?:\.[0-9]+)?)([KMGT])\b")
FREE_RE = re.compile(r"free percentage:\s*([0-9]{1,3})%")
SWAP_UNIT_GB = {"K": 1 / 1048576.0, "M": 1 / 1024.0, "G": 1.0, "T": 1024.0}
USAGE_SCHEMA = "pipeline-usage/1"
USAGE_MAX_AGE_SECONDS = 2 * 3600       # the usage job writes every 15 minutes
WEEK_SECONDS = 7 * 86400
BURN_MIN_ELAPSED = 12 * 3600           # earlier in the week a projection is noise
TREND_DAYS = 14


class MemoryUnreadable(Exception):
    pass


def read_memory(env):
    """(free percentage, swap used in GB) from macOS's own tools, without sudo. MemoryUnreadable, in words, when
    either cannot be read or does not look as expected: never a guessed number."""
    got = env.runner.read(["/usr/bin/memory_pressure"], timeout=20)
    m = FREE_RE.search(got.out) if got.ok else None
    if not m or not 0 <= int(m.group(1)) <= 100:
        raise MemoryUnreadable("memory_pressure did not give the free percentage")
    swap = env.runner.read(["/usr/sbin/sysctl", "vm.swapusage"], timeout=10)
    s = SWAP_RE.search(swap.out) if swap.ok else None
    if not s:
        raise MemoryUnreadable("sysctl vm.swapusage did not give the swap used")
    return int(m.group(1)), float(s.group(1)) * SWAP_UNIT_GB[s.group(2)]


def _trend(state, now, free_pct, swap_gb):
    """The week's lows, kept day by day for the sprint review: the lowest free percentage and the most swap seen."""
    days = state.setdefault("memory_trend", {})
    day = days.setdefault(_iso(now)[:10], {"min_free_pct": free_pct, "max_swap_gb": swap_gb})
    day["min_free_pct"] = min(day["min_free_pct"], free_pct)
    day["max_swap_gb"] = round(max(day["max_swap_gb"], swap_gb), 2)
    for old in sorted(days)[:-TREND_DAYS]:
        del days[old]


def check_memory(env, conf, state):
    """Memory (KIT-609): the free percentage under MEMORY_FREE_FLOOR_PCT, or swap over SWAP_CEILING_GB, on two passes
    running; one brief spike says nothing. A reading that cannot be taken is unknown, never fine."""
    read = getattr(env, "memory_read", None) or (lambda: read_memory(env))
    try:
        free_pct, swap_gb = read()
    except MemoryUnreadable as exc:
        _debounced(state, "memory", False)       # an unreadable pass breaks the run: two spikes must be consecutive
        return finding("memory", UNKNOWN, "could not read the machine's memory this pass: %s" % exc)
    _trend(state, _now(env), free_pct, swap_gb)
    floor, ceiling = int(conf["MEMORY_FREE_FLOOR_PCT"]), int(conf["SWAP_CEILING_GB"])
    low, swapping = free_pct < floor, swap_gb > ceiling
    now_line = "%d%% of memory free, %.1f GB of swap used" % (free_pct, swap_gb)
    if not _debounced(state, "memory", low or swapping):
        return finding("memory", OK, now_line + (" (past a limit once; it says so only if the next pass is too)"
                                                 if low or swapping else ""))
    parts = []
    if low:
        parts.append("only %d%% of memory is free (the floor is %d%%)" % (free_pct, floor))
    if swapping:
        parts.append("%.1f GB of swap is in use (the ceiling is %d GB)" % (swap_gb, ceiling))
    return finding("memory", FIRING, "The machine is short of memory: %s, on two passes in a row. This check only reports it: the "
                                     "queue's own room check decides whether new work starts, and work already running is "
                                     "not touched." % " and ".join(parts))


def burn_projection(util, resets_at, now):
    """The weekly window's use at the reset if the week goes on as it has: (projected fraction, elapsed seconds), or
    None when the week is too young to project. ValueError when the figures cannot be believed."""
    start = resets_at - WEEK_SECONDS
    if not isinstance(util, (int, float)) or isinstance(util, bool) or not 0 <= util <= 1.5:
        raise ValueError("the weekly window's use is not a figure")
    if not start <= now < resets_at:
        raise ValueError("the weekly window's reset is not the current week's")
    elapsed = now - start
    if elapsed < BURN_MIN_ELAPSED:
        return None
    return util * WEEK_SECONDS / elapsed, elapsed


def check_burn(env, conf):
    """The burn rate (KIT-609): the weekly window's use projected to its reset from use so far and time left, firing
    at BURN_WEEK_PCT, and the 5-hour window when it is at BURN_FIVE_HOUR_PCT. Reads only what the usage job already
    records. A figure that cannot be read is unknown, never zero."""
    path = conf.get("BURN_USAGE_FILE") or ""
    if not path:
        return finding("burn", OK, "BURN_USAGE_FILE is not set: the burn rate is not watched here")
    path = os.path.expanduser(path)
    now = _now(env)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return finding("burn", UNKNOWN, "could not read the usage file this pass, so the burn rate is not judged")
    written = _parse_iso(doc.get("written_at")) if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or doc.get("schema") != USAGE_SCHEMA or written is None or doc.get("result") == "failed":
        return finding("burn", UNKNOWN, "the usage file is not one this watch can read, or its last pass failed")
    if now - written > USAGE_MAX_AGE_SECONDS:
        return finding("burn", UNKNOWN, "the usage file was last written %d minutes ago, so the burn rate is not judged"
                       % ((now - written) // 60))
    windows = ((doc.get("status") or {}).get("windows") or {}) if isinstance(doc.get("status"), dict) else {}
    lines = []
    five = windows.get("five_hour") if isinstance(windows.get("five_hour"), dict) else {}
    line5 = int(conf["BURN_FIVE_HOUR_PCT"])
    reset5 = _parse_iso(five.get("resets_at"))
    util5 = five.get("utilization")
    five_readable = isinstance(util5, (int, float)) and not isinstance(util5, bool) and reset5 is not None
    if isinstance(five.get("utilization"), (int, float)) and not isinstance(five.get("utilization"), bool) \
            and reset5 and reset5 > now and five["utilization"] * 100 >= line5:
        lines.append("The 5-hour window is %d%% used (the line is %d%%)." % (round(five["utilization"] * 100), line5))
    week = windows.get("seven_day") if isinstance(windows.get("seven_day"), dict) else {}
    reset7 = _parse_iso(week.get("resets_at"))
    if reset7 is None:
        if lines:
            return _burn_firing(lines)
        return finding("burn", UNKNOWN, "the usage file has no reset time for the weekly window, so its burn rate is not judged")
    try:
        got = burn_projection(week.get("utilization"), reset7, now)
    except ValueError as exc:
        if lines:
            return _burn_firing(lines)
        return finding("burn", UNKNOWN, "%s, so the burn rate is not judged" % exc)
    line7 = int(conf["BURN_WEEK_PCT"])
    if got is None:
        used = week["utilization"] * 100
        if used >= line7:
            lines.append("The weekly window is already %d%% used, only %.0f hours into the week (the line is %d%%)."
                         % (round(used), (now - (reset7 - WEEK_SECONDS)) / 3600.0, line7))
        if lines:
            return _burn_firing(lines)
        return _burn_ok(five_readable, "%d%% of the weekly window used; it is too early in the week to project" % round(used))
    projected, elapsed = got
    if projected * 100 >= line7:
        lines.append("At this pace the weekly window will be %d%% used at its reset (%d%% used, %.1f days into the week; the line is %d%%)."
                     % (round(projected * 100), round(week["utilization"] * 100), elapsed / 86400.0, line7))
    if lines:
        return _burn_firing(lines)
    return _burn_ok(five_readable, "%d%% of the weekly window used, %d%% projected at the reset" % (round(week["utilization"] * 100), round(projected * 100)))


def _burn_ok(five_readable, said):
    """Fine on the weekly window; but a 5-hour figure that could not be read is not a clear: unknown, with the weekly
    reading said (the figure was there to be read, so its absence is news)."""
    if not five_readable:
        return finding("burn", UNKNOWN, "the 5-hour window's figure could not be read, so it is not judged (%s)" % said)
    return finding("burn", OK, said)


def _burn_firing(lines):
    return finding("burn", FIRING, " ".join(lines) + " Nothing is stopped by this: the watcher's own limits and the daily "
                                                      "pacing (if it is on) apply as before.")


def _debounced(state, kind, failing):
    rec = state.setdefault("kinds", {}).setdefault(kind, {})
    rec["fails"] = (rec.get("fails") or 0) + 1 if failing else 0
    return rec["fails"] >= DEBOUNCE


def check_dispatcher(env, conf, state):
    if not conf.get("DISPATCHER_SERVICE"):
        return finding("dispatcher", OK, "no dispatcher on this machine")
    version = pu.dispatcher_version(env, conf)
    if version:
        _debounced(state, "dispatcher", False)
        return finding("dispatcher", OK, "answers, version %s" % version)
    if _debounced(state, "dispatcher", True):
        return finding("dispatcher", FIRING, (
            "The dispatcher has not answered http://127.0.0.1:%s/version for %d passes running. "
            "Nothing delegated will start. Check: `launchctl print system/%s` (state, last exit); "
            "start it: `sudo launchctl bootstrap system /Library/LaunchDaemons/%s.plist`."
            % (conf["DISPATCHER_PORT"], DEBOUNCE, conf["DISPATCHER_SERVICE"],
               conf["DISPATCHER_SERVICE"])))
    return finding("dispatcher", UNKNOWN, "did not answer this pass; one more and it fires")


def check_front_door(env, conf, state):
    url = conf.get("PUBLIC_STATUS_URL")
    if not url:
        return finding("front-door", OK, "PUBLIC_STATUS_URL is not set: not checked")
    code = getattr(env, "http_status", http_status)(url)
    if code == 200:
        _debounced(state, "front-door", False)
        return finding("front-door", OK, "answers 200")
    if getattr(env, "http_status", http_status)(NET_PROBE_URL) is None:
        return finding("front-door", UNKNOWN, (
            "%s did not answer, and neither did the internet: no network on this pass "
            "(a background wake has none), so this is not judged" % url))
    if _debounced(state, "front-door", True):
        return finding("front-door", FIRING, (
            "%s answered %s for %d passes running while the internet answers: the tracker's "
            "webhooks are not reaching the dispatcher. 530/1033 means the tunnel is down; 502 "
            "the front door is up and the dispatcher is not." % (url, code or "nothing", DEBOUNCE)))
    return finding("front-door", UNKNOWN, "answered %s this pass; one more and it fires" % code)


def check_disk(env, conf):
    free = getattr(env, "disk_free", None)
    try:
        free_bytes = free() if free else shutil.disk_usage("/").free
    except OSError as exc:
        return finding("disk", UNKNOWN, "could not read free space: %s" % exc)
    floor = int(conf["DISK_FLOOR_GB"])
    gb = free_bytes / float(1 << 30)
    if gb < floor:
        return finding("disk", FIRING, "%.1f GB free on /, below the %d GB floor. Worktrees, "
                                       "transcripts and logs grow until something fails to "
                                       "write." % (gb, floor))
    return finding("disk", OK, "%.0f GB free" % gb)


def temp_areas(env):
    """[(label, path)]: the temp areas this account owns — its Claude Code sessions' scratch,
    and its own TMPDIR. Another account's (the dispatcher's) cannot be read from here."""
    areas = getattr(env, "temp_areas", None)
    if areas is not None:
        return areas()
    tmp = ""
    if sys.platform == "darwin":
        # A LaunchAgent may start with no TMPDIR set: ask macOS for this account's own folder.
        got = env.runner.read(["getconf", "DARWIN_USER_TEMP_DIR"], timeout=10)
        tmp = got.out.strip() if got.ok else ""
    tmp = tmp or env.environ.get("TMPDIR") or "/tmp"
    out = [("session scratch", os.path.realpath("/tmp/claude-%d" % os.getuid())),
           ("TMPDIR", os.path.realpath(tmp))]
    # Where TMPDIR is /tmp itself (Linux), the scratch is inside it: count it once.
    if out[0][1].startswith(out[1][1].rstrip("/") + "/"):
        out = out[1:]
    return out


def _locked_by_owner(path, uid):
    """A folder this account owns but has taken its own read or search bit from (a tool's
    `chmod 000`): what it holds is this account's and cannot be counted. macOS also refuses
    to list some folders whose owner bits are set (an app's `com.apple.*/TemporaryItems`);
    those are the system's to keep, not this account's work, and are left out."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return st.st_uid == uid and (st.st_mode & 0o500) != 0o500


def measure(path, stop_at, deadline):
    """(bytes, sizes, complete): what this account's entries under `path` hold, counted in
    allocated blocks as `du` counts them, symlinks never followed. `sizes` maps each top-level
    entry to its bytes. It stops once the total passes `stop_at` or time.time() passes
    `deadline`, and `complete` says whether it saw everything: a folder this account locked
    makes it incomplete, an entry removed while it counted does not. A missing `path` holds
    0."""
    uid = os.getuid()
    total, sizes, hidden = 0, {}, 0
    try:
        top = list(os.scandir(path))
    except FileNotFoundError:
        return 0, {}, True
    for entry in top:
        try:
            if entry.stat(follow_symlinks=False).st_uid != uid:
                continue                 # another account's, in a shared /tmp: not ours to count
        except FileNotFoundError:
            continue
        except OSError:
            hidden += 1
            continue
        size, stack = 0, [entry]
        while stack:
            e = stack.pop()
            try:
                st = e.stat(follow_symlinks=False)
                size += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                if e.is_dir(follow_symlinks=False):
                    stack.extend(os.scandir(e.path))
            except FileNotFoundError:
                continue                 # removed while counted: nothing left to count
            except PermissionError:
                hidden += _locked_by_owner(e.path, uid)
                continue
            except OSError:
                hidden += 1
                continue
            if total + size > stop_at or time.time() > deadline:
                sizes[entry.name] = size
                return total + size, sizes, False
        sizes[entry.name] = size
        total += size
    return total, sizes, hidden == 0


def check_temp(env, conf):
    line = int(conf["TEMP_CEILING_GB"])
    limit = line * float(1 << 30)
    count = getattr(env, "temp_measure", None) or measure
    deadline = time.time() + TEMP_WALK_SECONDS
    total, largest, complete = 0, [], True
    for label, path in temp_areas(env):
        try:
            got, sizes, whole = count(path, limit - total, deadline)
        except OSError as exc:
            return finding("temp", UNKNOWN, "could not measure %s (%s): %s" % (label, path, exc))
        total += got
        complete = complete and whole
        largest += [(size, os.path.join(path, name)) for name, size in sizes.items()]
        if total > limit:
            break
    gb = total / float(1 << 30)
    if total > limit:
        top = ", ".join("%s (%.1f GB)" % (p, s / float(1 << 30))
                        for s, p in sorted(largest, reverse=True)[:3])
        return finding("temp", FIRING, (
            "This account's temp space holds %s%.1f GB, past the %d GB line. Largest: %s. A "
            "session's scratch folder is safe to remove once that session has ended, and a "
            "restart clears both areas. The dispatcher's account keeps its own, which this "
            "watch cannot read." % ("" if complete else "at least ", gb, line, top)))
    if not complete:
        return finding("temp", UNKNOWN, "counted %.1f GB but could not see all of it: a folder "
                                        "this account locked, or the %d s ran out"
                                        % (gb, TEMP_WALK_SECONDS))
    return finding("temp", OK, "%.1f GB in session scratch and TMPDIR" % gb)


def _waker_problem(path, now):
    """What is wrong with the conflict waker's heartbeat at `path`, or None when it is
    checking in with a good result."""
    try:
        with open(path, encoding="utf-8") as fh:
            beat = json.load(fh)
    except FileNotFoundError:
        return "the conflict waker has never written its heartbeat, %s" % path
    except (OSError, ValueError) as exc:
        return "the conflict waker's heartbeat %s cannot be read (%s)" % (path, exc)
    if not isinstance(beat, dict) or beat.get("schema") != WAKER_SCHEMA:
        return "the conflict waker's heartbeat %s is not in a shape this watch knows" % path
    stamp = next((t for t in (_parse_iso(beat.get(k)) for k in ("finished_at", "at"))
                  if t is not None), None)
    if stamp is None:
        return "the conflict waker's heartbeat %s has no time on it" % path
    result = beat.get("result")
    if now - stamp > WAKER_STALE_SECONDS:
        return ("the conflict waker started a pass %s and never finished it" if
                result == "running" else "the conflict waker has not checked in since %s") % (
                    _iso(stamp))
    if result in ("problems", "error"):
        return "the conflict waker's last pass reported %s" % result
    return None


def check_conflict_waker(env, conf, state):
    """The owner's conflict waker (KIT-115), where CONFLICT_WAKER_STATE_DIR names its state
    directory, and said off where it does not (KIT-339). Debounced like the dispatcher: on a
    wake from sleep this hourly pass can run before the waker's first."""
    folder = conf.get("CONFLICT_WAKER_STATE_DIR")
    if not folder:
        return finding("conflict-waker", OK, "CONFLICT_WAKER_STATE_DIR is not set: no conflict "
                                             "waker is watched on this machine")
    problem = _waker_problem(os.path.join(folder, "heartbeat.json"), _now(env))
    if problem is None:
        _debounced(state, "conflict-waker", False)
        return finding("conflict-waker", OK, "checking in")
    if _debounced(state, "conflict-waker", True):
        return finding("conflict-waker", FIRING, (
            "%s, for %d passes running. A pull request that conflicts is not fixed by itself "
            "until it runs again. Check: `python3 scripts/pipeline_conflict_waker_setup.py "
            "status`." % (problem[0].upper() + problem[1:], DEBOUNCE)))
    return finding("conflict-waker", UNKNOWN, "%s this pass; one more and it fires" % problem)


def _page_problem(path, now, max_age):
    """What is wrong with the watcher's page job's heartbeat at `path`, or None when it is checking
    in and its last pass sent every page. Fields only: the words are ours."""
    try:
        with open(path, encoding="utf-8") as fh:
            beat = json.load(fh)
    except FileNotFoundError:
        return "the watcher's page job has never written its heartbeat, %s" % path
    except (OSError, ValueError) as exc:
        return "the watcher's page job's heartbeat %s cannot be read (%s)" % (path, exc)
    if not isinstance(beat, dict) or beat.get("schema") != PAGE_SCHEMA:
        return "the watcher's page job's heartbeat %s is not in a shape this watch knows" % path
    stamp = _parse_iso(beat.get("at"))
    if stamp is None:
        return "the watcher's page job's heartbeat %s has no time on it" % path
    if now - stamp > max_age:
        return "the watcher's page job has not checked in since %s" % _iso(stamp)
    result = beat.get("result")
    if result == "could-not-page":
        routes = beat.get("routes") if isinstance(beat.get("routes"), dict) else {}
        failed = sorted(r for r in PAGE_ROUTES if routes.get(r) == "failed")
        return "the watcher's page job could not send a page%s" % (
            " by %s" % " and ".join(failed) if failed else "")
    if result != "ok":
        return "the watcher's page job's last pass reported a result this watch does not know"
    return None


def check_watcher_page(env, conf, state):
    """The watcher's page job (KIT-549), where WATCHER_PAGE_HEARTBEAT_FILE names its heartbeat, and
    said off where it does not. Debounced like the waker: on a wake from sleep this hourly pass can
    run before the job's first. Slack only: a pager that cannot page reaches you by this route."""
    path = conf.get("WATCHER_PAGE_HEARTBEAT_FILE")
    if not path:
        return finding("watcher-page", OK, "WATCHER_PAGE_HEARTBEAT_FILE is not set: the watcher's "
                                           "page job is not watched here")
    age = int(conf.get("WATCHER_PAGE_MAX_AGE_MINUTES") or 120) * 60
    problem = _page_problem(path, _now(env), age)
    if problem is None:
        _debounced(state, "watcher-page", False)
        return finding("watcher-page", OK, "checking in, and every page went")
    if _debounced(state, "watcher-page", True):
        return finding("watcher-page", FIRING, (
            "%s, for %d passes running. The watcher cannot reach you until it can. Check: `python3 "
            "scripts/pipeline_watcher_page.py check --config <its config>`." % (
                problem[0].upper() + problem[1:], DEBOUNCE)))
    return finding("watcher-page", UNKNOWN, "%s this pass; one more and it fires" % problem)


def _push_problem(path, now):
    """What is wrong with the bridge's push-connection job's heartbeat at `path`, or None when it
    is checking in, connected, and has heard from Slack lately. Fields only: the words are ours."""
    try:
        with open(path, encoding="utf-8") as fh:
            beat = json.load(fh)
    except FileNotFoundError:
        return "the push-connection job has never written its heartbeat, %s" % path
    except (OSError, ValueError) as exc:
        return "the push-connection job's heartbeat %s cannot be read (%s)" % (path, exc)
    if not isinstance(beat, dict) or beat.get("schema") != PUSH_SCHEMA:
        return "the push-connection job's heartbeat %s is not in a shape this watch knows" % path
    stamp = _parse_iso(beat.get("at"))
    if stamp is None:
        return "the push-connection job's heartbeat %s has no time on it" % path
    if now - stamp > PUSH_STALE_SECONDS:
        return "the push-connection job has not checked in since %s" % _iso(stamp)
    if beat.get("connected") is not True:
        return "the push-connection job is not connected to Slack"
    heard = _parse_iso(beat.get("last_heard_at"))
    if heard is None or now - heard > PUSH_HEARD_SECONDS:
        return "the push-connection job has not heard from Slack since %s" % (
            _iso(heard) if heard is not None else "it started")
    return None


def check_push_connection(env, conf, state):
    """The bridge's push-connection job (KIT-561), where PUSH_HEARTBEAT_FILE names its heartbeat,
    and said off where it does not. Debounced like the waker: on a wake from sleep this hourly pass
    can run before the job has reconnected. Slack only: the bridge falls back to its once-a-minute
    pass, so a DM is still answered, only not at once."""
    path = conf.get("PUSH_HEARTBEAT_FILE")
    if not path:
        return finding("push-connection", OK, "PUSH_HEARTBEAT_FILE is not set: the bridge's "
                                               "push-connection job is not watched here")
    problem = _push_problem(path, _now(env))
    if problem is None:
        _debounced(state, "push-connection", False)
        return finding("push-connection", OK, "connected, and Slack was heard from lately")
    if _debounced(state, "push-connection", True):
        return finding("push-connection", FIRING, (
            "%s, for %d passes running. DMs are still answered once a minute by the bridge's own "
            "pass, only not at once. Check: `python3 scripts/pipeline_bridge_setup.py verify "
            "--conf <its conf>`." % (problem[0].upper() + problem[1:], DEBOUNCE)))
    return finding("push-connection", UNKNOWN, "%s this pass; one more and it fires" % problem)


def _clerk_problem(label, path, schema, now):
    """What is wrong with a clerk's heartbeat at `path`, or None when it is checking in. Only
    that it is alive and current is judged here: its results are the monitor's to page."""
    try:
        with open(path, encoding="utf-8") as fh:
            beat = json.load(fh)
    except FileNotFoundError:
        return "the %s has never written its heartbeat, %s" % (label, path)
    except (OSError, ValueError) as exc:
        return "the %s's heartbeat %s cannot be read (%s)" % (label, path, exc)
    if not isinstance(beat, dict) or beat.get("schema") != schema:
        return "the %s's heartbeat %s is not in a shape this watch knows" % (label, path)
    stamp = _parse_iso(beat.get("at"))
    if stamp is None:
        return "the %s's heartbeat %s has no time on it" % (label, path)
    if now - stamp > CLERK_STALE_SECONDS:
        return "the %s has not checked in since %s" % (label, _iso(stamp))
    return None


def check_clerk(env, conf, state, kind):
    """The notifier or the heartbeat monitor (KIT-286), where its heartbeat file is named, and
    said off where it is not. Debounced like the waker: on a wake from sleep this hourly pass
    can run before the job's first."""
    key, schema, label, effect = CLERKS[kind]
    path = conf.get(key)
    if not path:
        return finding(kind, OK, "%s is not set: the %s is not watched here" % (key, label))
    problem = _clerk_problem(label, path, schema, _now(env))
    if problem is None:
        _debounced(state, kind, False)
        return finding(kind, OK, "checking in")
    if _debounced(state, kind, True):
        return finding(kind, FIRING, "%s, for %d passes running. %s If %s is a copy, the "
                       "copy may have stopped being refreshed." % (
                           problem[0].upper() + problem[1:], DEBOUNCE, effect, path))
    return finding(kind, UNKNOWN, "%s this pass; one more and it fires" % problem)


# Cloudflare answers 403 to Python's default user agent ("Python-urllib/…") and 200 to a
# named one (measured on the reference front door, 2026-10-10). Without this every probe of
# a Cloudflare-fronted door would read as an outage.
USER_AGENT = "pipeline-health-watch/1"


def _request(url):
    import urllib.request
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def http_status(url, timeout=8):
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(_request(url), timeout=timeout) as resp:  # noqa: S310 (fixed URLs)
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def _clip(text):
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX] + " … (cut)"


def send_github(env, conf, kind, state, summary, claimed=None):
    """(delivered, detail). Dispatch, then find that run and wait for it: an accepted
    dispatch whose run failed has filed nothing."""
    repo, wf = conf["ALERT_REPO"], conf["ALERT_WORKFLOW"]
    started = _now(env)
    argv = ["gh", "workflow", "run", wf, "-R", repo, "--ref", "main", "-f", "kind=" + kind,
            "-f", "state=" + state, "-f", "summary=" + _clip(summary + "\n\n" + diagnose(conf))]
    list_argv = ["gh", "run", "list", "-R", repo, "--workflow", wf, "--event", "workflow_dispatch",
                 "--limit", "5", "--json", "databaseId,createdAt"]

    def listed():
        got = env.runner.read(list_argv, timeout=30)
        try:
            return json.loads(got.out) if got.ok else []
        except ValueError:
            return []
    # A run already claimed by an earlier send in this pass is not this dispatch's run.
    # Without this, a second kind could watch the first kind's finished run and count it as
    # its own, so its own failed run would never be retried (KIT-249). Kept in memory, so it
    # cannot fail the way a pre-dispatch listing can (KIT-251).
    claimed = set() if claimed is None else claimed
    out = env.runner.write("tell GitHub that %s is %s" % (kind, state), argv, timeout=60)
    if out.skipped:
        return True, "dry run"
    if not out.ok:
        return False, "the dispatch was refused: %s" % (out.err or out.out).strip()[:160]
    run_id = None
    for _ in range(RUN_FIND_TRIES):
        runs = listed()
        fresh = [r for r in runs if str(r.get("databaseId")) not in claimed
                 and (_parse_iso(r.get("createdAt")) or 0) >= started - 30]
        if fresh:
            run_id = str(sorted(fresh, key=lambda r: r.get("createdAt", ""))[-1]["databaseId"])
            break
        env.sleep(5)
    if run_id is None:
        return False, "the dispatch was accepted but no run appeared in %d s" % (RUN_FIND_TRIES * 5)
    claimed.add(run_id)
    watch = env.runner.read(["gh", "run", "watch", run_id, "-R", repo, "--exit-status"],
                            timeout=RUN_WATCH_TIMEOUT)
    if not watch.ok:
        return False, "run %s did not succeed: %s" % (run_id, (watch.err or watch.out).strip()[-160:])
    return True, "run %s succeeded" % run_id


def write_status(env, conf, findings, now, github_note, dry_run):
    path = conf.get("HEALTH_STATUS_FILE")
    if not path:
        return "OFF (HEALTH_STATUS_FILE is empty): the heartbeat monitor has nothing to read"
    lit = sorted(f["kind"] for f in findings if f["state"] == FIRING)
    firing = [k for k in lit if k not in UPKEEP_KINDS]
    advisory = [k for k in lit if k in UPKEEP_KINDS]
    unknown = sorted(f["kind"] for f in findings if f["state"] == UNKNOWN)
    doc = {"schema": STATUS_SCHEMA, "written_at": _iso(now),
           # Outage kinds only: this is what the monitor turns into a Slack ping.
           "result": "problem" if firing else "ok",
           "firing": firing, "advisory": advisory, "unknown": unknown,
           # One identity per firing SET: a new kind firing pages again; a summary that only
           # gets older does not.
           "fingerprint": ",".join(firing) or "ok",
           "findings": [f for f in findings if f["state"] != OK],
           "github": github_note, "diagnose": diagnose(conf)}
    if dry_run:
        return "dry run: would write %s" % path
    try:
        _atomic_write(path, doc, mode=0o644)
        os.chmod(os.path.dirname(path), 0o755)
    except OSError as exc:
        return "COULD NOT WRITE %s: %s" % (path, exc)
    return "written to %s" % path


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def run_pass(env, conf, dry_run=False):
    state = load_state(env)
    now = _now(env)
    findings = [check_hooks(env, conf), check_updates(env, conf, state),
                check_release(env, conf), check_dispatcher(env, conf, state),
                check_front_door(env, conf, state), check_disk(env, conf), check_temp(env, conf),
                check_conflict_waker(env, conf, state), check_watcher_page(env, conf, state),
                check_push_connection(env, conf, state), check_credentials(env, conf),
                check_memory(env, conf, state), check_burn(env, conf)]
    findings += [check_clerk(env, conf, state, kind) for kind in CLERKS]
    failed, notes, claimed = [], [], set()
    github_on = bool(conf.get("ALERT_REPO"))
    for f in findings:
        rec = state.setdefault("kinds", {}).setdefault(f["kind"], {})
        if f["state"] == UNKNOWN or f["kind"] in SLACK_ONLY_KINDS:
            continue                     # not judged, or the status file alone carries it
        sent = rec.get("sent", OK)       # nothing sent yet reads as "ok": silence on a healthy start
        if f["state"] == sent and f.get("key") == rec.get("sent_key"):
            continue
        if not github_on:
            notes.append("%s is now %s — GitHub alerts are OFF (ALERT_REPO is empty)"
                         % (f["kind"], f["state"]))
            if not dry_run:
                rec["sent"], rec["sent_key"] = f["state"], f.get("key")
            continue
        delivered, detail = send_github(env, conf, f["kind"], f["state"], f["summary"], claimed)
        notes.append("%s is now %s — GitHub: %s" % (f["kind"], f["state"], detail))
        if delivered and not dry_run:
            rec["sent"], rec["sent_key"], rec["since"] = f["state"], f.get("key"), _iso(now)
        elif not delivered:
            failed.append(f["kind"])
    github_note = ("could not deliver: " + ", ".join(failed)) if failed else (
        "on" if github_on else "OFF")
    status_note = write_status(env, conf, findings, now, github_note, dry_run)
    if not dry_run:
        state["last_run_at"] = _iso(now)
        try:
            _atomic_write(state_path(env), state)
        except OSError as exc:
            notes.append("could not save this pass's state: %s" % exc)
            failed.append("state")
    say("PIPELINE HEALTH WATCH — %s%s" % (_iso(now), " (dry run)" if dry_run else ""))
    for f in findings:
        say("  %-8s %-11s %s" % (f["state"].upper(), f["kind"], f["summary"].splitlines()[0][:110]))
    for n in notes:
        say("  -> %s" % n)
    say("  status file: %s" % status_note)
    if not github_on:
        say("  GitHub alerts: OFF (ALERT_REPO is empty) — a firing kind reaches nobody by GitHub")
    if failed or status_note.startswith("COULD NOT"):
        return EX_FAILED
    states = [f["state"] for f in findings]
    return EX_FIRING if FIRING in states else (EX_UNKNOWN if UNKNOWN in states else EX_OK)


def status(env):
    state = load_state(env)
    if not state.get("last_run_at"):
        say("No pass has run yet (no %s)." % state_path(env))
        return EX_UNKNOWN
    say("Last pass: %s" % state["last_run_at"])
    for kind in KINDS:
        rec = (state.get("kinds") or {}).get(kind) or {}
        say("  %-11s last sent: %-7s since %s%s" % (kind, rec.get("sent", "ok"),
                                                   rec.get("since", "-"),
                                                   ("  (failing passes: %d)" % rec["fails"])
                                                   if rec.get("fails") else ""))
    return EX_OK


# --------------------------------------------------------------------------- #
# The LaunchAgent
# --------------------------------------------------------------------------- #
def agent_plist(conf, python, kit, path_env, home):
    log = os.path.join(home, ".pipeline-update", "watch.log")
    return {
        "Label": conf["WATCH_LABEL"],
        # `--conf` belongs to the top-level parser, so it goes BEFORE `run`; after it, argparse
        # refuses it and every pass exits 2 (KIT-249). Absolute, because launchd runs from `/`.
        "ProgramArguments": [python, os.path.join(kit, "scripts", "pipeline_watch.py"),
                             "--conf", os.path.abspath(conf["__source__"]), "run"],
        "StartInterval": int(conf["WATCH_INTERVAL_SECONDS"]),
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": path_env, "HOME": home},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    }


def _path_env():
    dirs = []
    for tool in ("brew", "gh", "npm", "git"):
        found = shutil.which(tool)
        if found and os.path.dirname(found) not in dirs:
            dirs.append(os.path.dirname(found))
    return ":".join(dirs + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"])


def refuse_if_agent(action, environ):
    """The update script's refusal, naming THIS script's command for the person to run."""
    pu.refuse_if_agent(action, environ, script="pipeline_watch.py",
                       why="It loads or removes a job that runs as you every hour; a session\n"
                           "  does not install its own supervision.",
                       read_only="run --dry-run")


def _load(env, domain, label, plist):
    """Bootstrap the job once launchd has let go of the old one, retrying only EIO.

    launchd keeps a booted-out job's record through its SIGTERM-to-SIGKILL window, and a
    bootstrap inside that window answers `Bootstrap failed: 5`. Before KIT-251 a reinstall
    could hit it and leave the watch unloaded. The same rule the update script applies to the
    dispatcher, in the user's own domain and without root."""
    r = env.runner
    target = "%s/%s" % (domain, label)
    polls = int((se.DEFAULT_EXIT_TIMEOUT + se.EXIT_TIMEOUT_HEADROOM) / se.BOOTOUT_POLL_SECONDS)
    said = ""
    for attempt in range(1, se.BOOTSTRAP_ATTEMPTS + 1):
        for _ in range(polls):
            if r.dry_run or not r.read(["launchctl", "print", target]).ok:
                break
            se._pause(se.BOOTOUT_POLL_SECONDS)
        else:
            raise pu.UpdateError("launchd still holds %s after %d s, so loading it again would "
                                 "fail. Check: launchctl print %s"
                                 % (label, polls * se.BOOTOUT_POLL_SECONDS, target))
        boot = r.write("load %s" % label, ["launchctl", "bootstrap", domain, plist])
        if boot.ok or boot.skipped:
            return
        said = (boot.err or boot.out).strip() or "bootstrap said nothing"
        if not se._EIO.search(said):
            break
        se._pause(se.BOOTSTRAP_RETRY_SECONDS)
    raise pu.UpdateError("launchctl bootstrap failed: %s. The job is NOT loaded; run install "
                         "again." % said.splitlines()[0][:160])


def install(env, conf, uninstall_only=False):
    refuse_if_agent("uninstall" if uninstall_only else "install", env.environ)
    if sys.platform != "darwin" and not getattr(env, "any_platform", False):
        raise pu.UpdateError("the scheduled job is a launchd LaunchAgent: macOS only. Elsewhere, "
                             "run `pipeline_watch.py run` hourly from cron or a systemd timer.")
    label = conf["WATCH_LABEL"]
    plist = os.path.join(_home(env), "Library", "LaunchAgents", label + ".plist")
    domain = "gui/%d" % os.getuid()
    r = env.runner
    bootout = ["launchctl", "bootout", "%s/%s" % (domain, label)]
    if uninstall_only:
        r.write("stop %s if it is loaded" % label, bootout)
        if os.path.exists(plist) and not r.dry_run:
            os.remove(plist)
        say("Uninstalled %s (plist removed: %s)." % (label, plist))
        return EX_OK
    # The preview sends NOTHING: its runner is put in dry-run mode for the pass, not only the
    # pass's own bookkeeping. Before KIT-249 it dispatched the alert for real, before the yes.
    say("A dry pass first, so you see what the job will report. It sends nothing:")
    saved, r.dry_run = r.dry_run, True
    try:
        run_pass(env, conf, dry_run=True)
    finally:
        r.dry_run = saved
    doc = agent_plist(conf, sys.executable, KIT, _path_env(), _home(env))
    say("")
    say("The job: %s every %ds, as you, from %s" % (label, doc["StartInterval"], KIT))
    say("  it runs `pipeline_watch.py run`, logs to %s, and changes nothing it reports on." % doc["StandardOutPath"])
    if env.ask("Type yes to install it: ").strip().lower() != "yes":
        say("Not confirmed. Nothing was installed, and a job already loaded was left running.")
        return EX_FIRING
    # Only after the yes: a "no" must not leave a working job stopped.
    r.write("stop %s if it is loaded" % label, bootout)
    if not r.dry_run:
        os.makedirs(os.path.dirname(plist), exist_ok=True)
        # launchd does not create the log's folder, and a job whose log cannot be opened does
        # not start (KIT-249).
        os.makedirs(os.path.dirname(doc["StandardOutPath"]), mode=0o700, exist_ok=True)
        with open(plist, "wb") as fh:
            plistlib.dump(doc, fh)
    _load(env, domain, label, plist)
    say("Installed. Check it with: launchctl print %s/%s ; python3 scripts/pipeline_watch.py status"
        % (domain, label))
    return EX_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None, env=None):
    p = argparse.ArgumentParser(prog="pipeline_watch.py", description=__doc__.split("\n")[0])
    p.add_argument("--conf", default=os.path.join(KIT, "update.conf"))
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    run = sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "watch-selftest-run-")
    if not args.cmd:
        p.print_help()
        return EX_USAGE
    env = env or pu.Env()
    try:
        if args.cmd in ("install", "uninstall"):
            refuse_if_agent(args.cmd, env.environ)
        if args.cmd == "status":
            return status(env)
        conf = pu.load_conf(args.conf)
        if args.cmd == "run":
            if args.dry_run:
                env.runner.dry_run = True
            return run_pass(env, conf, dry_run=args.dry_run)
        return install(env, conf, uninstall_only=args.cmd == "uninstall")
    except pu.Refusal as exc:
        say(str(exc))
        return EX_REFUSED
    except pu.ConfError as exc:
        say("CONFIG: %s" % exc)
        return EX_USAGE
    except pu.UpdateError as exc:
        say("FAILED: %s" % exc)
        return EX_FAILED


# --------------------------------------------------------------------------- #
# --selftest — offline; every transport stubbed
# --------------------------------------------------------------------------- #
def selftest():
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        ok = got == want
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            fails.append(name)

    quiet = []
    se.say = lambda msg="": quiet.append(msg)
    pauses = []
    se._pause = lambda seconds: pauses.append(seconds)   # waits are walked, never slept
    T = 1791700000.0

    def conf(**over):
        values = {"DISPATCHER_SERVICE": "com.test.dispatcher", "ROLE_ACCOUNT": "_testdispatch",
                  "DISPATCHER_ENV_FILE": "/opt/test-dispatch/.env", "DISPATCHER_VERSION": "0.2.73",
                  "BREW_CASKS": "", "BREW_FORMULAE": "", "KIT_CHECKOUT": "/kit",
                  "ALERT_REPO": "example-org/ops-repo", "PUBLIC_STATUS_URL": "https://door.example.com/status"}
        values.update(over)
        c, errors = pu.validate_conf({k: v for k, v in values.items() if v is not None})
        assert not errors, errors
        return c

    class FakeGitHub(se.FakeRunner):
        """`gh run list` as GitHub answers it: a dispatch's run is listed only AFTER that
        dispatch. Each successful `gh workflow run` adds one run, shaped like the scripted
        one, with the next id (newest first, as gh lists them)."""

        def __init__(self, answers, lag=0):
            se.FakeRunner.__init__(self, answers)
            self.lag, self.lists, self.visible_from = lag, 0, []

        def _exec(self, argv, stdin, timeout, cwd=None):
            line = se._fmt(argv)
            if "gh run list" in line:
                template = next((json.loads(out)[0] for needle, rc, out in self.answers
                                 if "gh run list" in needle and rc == 0 and json.loads(out)), None)
                if template is not None:
                    self.lists += 1
                    # `lag` listings pass before a dispatch's run shows up, as on GitHub.
                    runs = [dict(template, databaseId=template["databaseId"] + i)
                            for i, start in enumerate(self.visible_from) if self.lists > start]
                    return se.Result(0, json.dumps(runs[::-1]), "")
            res = se.FakeRunner._exec(self, argv, stdin, timeout, cwd)
            if "gh workflow run" in line and res.rc == 0:
                self.visible_from.append(self.lists + self.lag)
            return res

    class GuiLaunchd(FakeGitHub):
        """launchd in the user's domain: a booted-out job lingers for `linger` polls, and each
        bootstrap answers the next code in `boots` (the last repeating)."""

        def __init__(self, answers, linger=0, boots=(0,), err="Bootstrap failed: 5: Input/output error"):
            FakeGitHub.__init__(self, answers)
            self.linger, self.boots, self.err, self.attempts = linger, list(boots), err, 0

        def _exec(self, argv, stdin, timeout, cwd=None):
            line = se._fmt(argv)
            if line.startswith("launchctl print gui/"):
                if self.linger > 0:
                    self.linger -= 1
                    return se.Result(0, "state = running", "")
                return se.Result(113, "", "Could not find service")
            if line.startswith("launchctl bootout gui/"):
                return se.Result(0, "", "")
            if line.startswith("launchctl bootstrap gui/"):
                rc = self.boots[min(self.attempts, len(self.boots) - 1)]
                self.attempts += 1
                return se.Result(rc, "", "" if rc == 0 else self.err)
            return FakeGitHub._exec(self, argv, stdin, timeout, cwd)

    def make_env(home, version="0.2.73", latest="0.2.73", door=200, net=200, free_gb=100,
                 gh=(), now=T, answer="yes", environ=None, runner=None):
        table = [("npm view cyrus-ai version", 0, latest + "\n")] + list(gh)
        env = pu.Env(runner=runner(table) if runner else FakeGitHub(table),
                     http=lambda url: {"cyrus_cli_version": version} if version and url.endswith("/version") else None,
                     sleep=lambda s: None, ask=lambda q: answer,
                     environ={} if environ is None else environ, home=home)
        env.now = lambda: now
        env.disk_free = lambda: free_gb * (1 << 30)
        env.memory_read = lambda: (60, 0.0)
        env.temp_areas = lambda: [("session scratch", os.path.join(home, "no-scratch")),
                                  ("TMPDIR", os.path.join(home, "no-tmpdir"))]
        env.http_status = lambda url: door if "door" in url else net
        env.any_platform = True
        return env

    def gh_ok(now):
        """GitHub accepting a dispatch and its run succeeding, created just after `now`."""
        return [("gh workflow run", 0, ""),
                ("gh run list", 0, json.dumps([{"databaseId": 77, "createdAt": _iso(now + 5)}])),
                ("gh run watch 77", 0, "")]

    with tempfile.TemporaryDirectory() as home:
        status_file = os.path.join(home, "shared", "pipeline-health", "status.json")
        c = conf(HEALTH_STATUS_FILE=status_file)

        # -- a healthy first pass: nothing sent, the status file says ok ----------------
        env = make_env(home)
        check("a healthy pass exits 0", run_pass(env, c), EX_OK)
        check("…and sends nothing (silence on a healthy start)", env.runner.writes, [])
        doc = json.load(open(status_file))
        check("…and writes the status file the monitor reads",
              (doc["schema"], doc["result"], doc["fingerprint"]), (STATUS_SCHEMA, "ok", "ok"))
        check("…readable by another account (0644)", oct(os.stat(status_file).st_mode & 0o777), "0o644")

        # -- the machine and the budget (KIT-609) ------------------------------------------------------------
        def mem_pass(readings, **conf_over):
            """One pass per reading (free %, swap GB) or exception, in one state: the findings of the last."""
            cc = conf(HEALTH_STATUS_FILE=status_file, **conf_over)
            env_ = make_env(home)
            last = None
            for r in readings:
                def read(r=r):
                    if isinstance(r, Exception):
                        raise r
                    return r
                env_.memory_read = read
                st = load_state(env_)
                last = check_memory(env_, cc, st)
                _atomic_write(state_path(env_), st)
            return last, load_state(env_)
        state_file_ = state_path(make_env(home))
        if os.path.exists(state_file_):
            os.unlink(state_file_)
        f, st = mem_pass([(60, 0.5)])
        check("memory: plenty free is ok and says the numbers", (f["state"], f["summary"]), ("ok", "60% of memory free, 0.5 GB of swap used"))
        os.unlink(state_file_)
        f, st = mem_pass([(6, 0.5)])
        check("memory: one pass under the floor is a spike, not an alert", (f["state"], "only if the next pass is too" in f["summary"]), ("ok", True))
        f, st = mem_pass([(6, 0.5)])
        check("memory: the second pass in a row fires, with how far under and what the system does",
              (f["state"], "only 6% of memory is free (the floor is 10%)" in f["summary"], "This check only reports it" in f["summary"], "New starts are held" in f["summary"]), ("firing", True, True, False))
        f, st = mem_pass([(60, 0.5)])
        check("memory: a good pass clears it", f["state"], "ok")
        os.unlink(state_file_)
        f, st = mem_pass([(50, 5.2), (50, 5.2)])
        check("memory: swap over the ceiling on two passes fires, and names the swap only",
              (f["state"], "5.2 GB of swap is in use (the ceiling is 4 GB)" in f["summary"], "free" in f["summary"].split("swap")[0].split("short of memory:")[1]), ("firing", True, False))
        os.unlink(state_file_)
        f, st = mem_pass([(6, 0.5), MemoryUnreadable("x"), (6, 0.5)])
        check("memory: a spike, an unreadable pass and another spike are not consecutive, so they never fire", f["state"], "ok")
        os.unlink(state_file_)
        f, st = mem_pass([(6, 0.5), (60, 0.5), (6, 0.5)])
        check("memory: a spike, a good pass and another spike never fire (the count starts over)", f["state"], "ok")
        os.unlink(state_file_)
        f, st = mem_pass([MemoryUnreadable("memory_pressure did not give the free percentage")])
        check("memory: a reading that cannot be taken is unknown, never fine", (f["state"], "could not read" in f["summary"]), ("unknown", True))
        f, st = mem_pass([(12, 0.5)], MEMORY_FREE_FLOOR_PCT="15")
        f, st = mem_pass([(12, 0.5)], MEMORY_FREE_FLOOR_PCT="15")
        check("memory: the floor lives in the conf", f["state"], "firing")
        check("memory: the week's lows are kept day by day for the sprint review",
              [(d, v["min_free_pct"], v["max_swap_gb"]) for d, v in st["memory_trend"].items()], [(_iso(T)[:10], 12, 0.5)])
        os.unlink(state_file_)
        mem_pass([(40, 1.0), (20, 3.0), (55, 0.2)])
        check("memory: the day's lowest free and most swap are kept, not the last",
              [(v["min_free_pct"], v["max_swap_gb"]) for v in load_state(make_env(home))["memory_trend"].values()], [(20, 3.0)])
        os.unlink(state_file_)
        check("memory: the real reader parses macOS's own words",
              (read_memory(type("E", (), {"runner": se.FakeRunner([
                  ("memory_pressure", 0, "The system has 17179869184 (1048576 pages)\nSystem-wide memory free percentage: 53%\n"),
                  ("vm.swapusage", 0, "vm.swapusage: total = 2048.00M  used = 1186.25M  free = 861.75M  (encrypted)\n")])})())[0],
               round(read_memory(type("E", (), {"runner": se.FakeRunner([
                   ("memory_pressure", 0, "System-wide memory free percentage: 53%\n"),
                   ("vm.swapusage", 0, "vm.swapusage: total = 2048.00M  used = 1186.25M  free = 861.75M  (encrypted)\n")])})())[1], 3)),
              (53, 1.158))
        for label, table in (("no free percentage", [("memory_pressure", 0, "nothing useful\n"), ("vm.swapusage", 0, "used = 1.00M\n")]),
                             ("a failing tool", [("memory_pressure", 1, ""), ("vm.swapusage", 0, "used = 1.00M\n")]),
                             ("no swap figure", [("memory_pressure", 0, "free percentage: 40%\n"), ("vm.swapusage", 0, "garbage\n")]),
                             ("a percentage over 100", [("memory_pressure", 0, "free percentage: 140%\n"), ("vm.swapusage", 0, "used = 1.00M\n")])):
            try:
                read_memory(type("E", (), {"runner": se.FakeRunner(table)})())
                got_ = "read"
            except MemoryUnreadable:
                got_ = "unreadable"
            check("memory: %s is unreadable, never a guess" % label, got_, "unreadable")

        usage_f = os.path.join(home, "burn-usage.json")
        week_reset = T + 3 * 86400            # four days into the week at T

        def usage_doc(week_util=0.38, five_util=0.2, resets=None, written=None, **over):
            doc = {"schema": USAGE_SCHEMA, "written_at": _iso(written or T - 300), "result": "ok", "status": {"windows": {
                "five_hour": {"utilization": five_util, "resets_at": _iso(T + 3600)},
                "seven_day": {"utilization": week_util, "resets_at": _iso(resets or week_reset)}}}}
            doc.update(over)
            with open(usage_f, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)

        def burn(**conf_over):
            return check_burn(make_env(home), conf(BURN_USAGE_FILE=usage_f, **conf_over))
        usage_doc()
        f = burn()
        check("burn: 38% used four days in projects to 66%, which is ok, and says both", (f["state"], f["summary"]),
              ("ok", "38% of the weekly window used, 66% projected at the reset"))
        usage_doc(week_util=0.60)
        f = burn()
        check("burn: 60% used four days in projects to 105% and fires, with the line and what is done about it",
              (f["state"], "At this pace the weekly window will be 105% used at its reset (60% used, 4.0 days into the week; the line is 90%)." in f["summary"],
               "Nothing is stopped by this" in f["summary"]), ("firing", True, True))
        check("burn: the threshold lives in the conf", burn(BURN_WEEK_PCT="120")["state"], "ok")
        usage_doc(five_util=0.95)
        f = burn()
        check("burn: the 5-hour window near its limit fires on its own", (f["state"], "The 5-hour window is 95% used (the line is 90%)." in f["summary"]), ("firing", True))
        usage_doc(five_util=0.95, resets=T + 30 * 86400)
        check("burn: a weekly reset that is not this week's is unknown, but a 5-hour firing still says so", burn()["state"], "firing")
        usage_doc(resets=T + 30 * 86400)
        check("burn: a weekly reset that is not this week's is unknown, never judged", burn()["state"], "unknown")
        usage_doc(week_util=0.5, resets=T + 6.8 * 86400)
        check("burn: early in the week it does not project (too little time to say)", (burn()["state"], "too early in the week" in burn()["summary"]), ("ok", True))
        usage_doc(week_util=0.95, resets=T + 6.8 * 86400)
        check("burn: early in the week, a window already past the line still fires, saying so",
              (burn()["state"], "already 95% used, only 5 hours into the week" in burn()["summary"]), ("firing", True))
        usage_doc()
        doc_ = json.load(open(usage_f))
        del doc_["status"]["windows"]["five_hour"]
        json.dump(doc_, open(usage_f, "w"))
        check("burn: a 5-hour figure that cannot be read is unknown, saying the weekly reading, never a plain ok",
              (burn()["state"], "5-hour window's figure could not be read" in burn()["summary"], "projected at the reset" in burn()["summary"]),
              ("unknown", True, True))
        doc_["status"]["windows"]["seven_day"]["utilization"] = 0.60
        json.dump(doc_, open(usage_f, "w"))
        check("burn: …but a weekly projection over the line fires whatever the 5-hour figure", burn()["state"], "firing")
        usage_doc(five_util="lots")
        check("burn: a 5-hour figure that is not a number is unknown too", burn()["state"], "unknown")
        usage_doc(written=T - 3 * 3600)
        check("burn: a stale usage file is unknown, with its age", (burn()["state"], "last written 180 minutes ago" in burn()["summary"]), ("unknown", True))
        usage_doc(result="failed")
        check("burn: a failed usage pass is unknown", burn()["state"], "unknown")
        usage_doc(week_util=None)
        check("burn: a missing figure is unknown, never zero", burn()["state"], "unknown")
        usage_doc(week_util=True)
        check("burn: a boolean is not a figure", burn()["state"], "unknown")
        os.unlink(usage_f)
        check("burn: no usage file is unknown", burn()["state"], "unknown")
        check("burn: with no file named it is not watched, and says so", check_burn(make_env(home), conf())["summary"].startswith("BURN_USAGE_FILE is not set"), True)
        for key, bad_value in (("MEMORY_FREE_FLOOR_PCT", "0"), ("SWAP_CEILING_GB", "x"), ("SWAP_CEILING_GB", "0"), ("SWAP_CEILING_GB", "9999"), ("MEMORY_FREE_FLOOR_PCT", "90"), ("BURN_WEEK_PCT", "10"), ("BURN_FIVE_HOUR_PCT", "101"), ("BURN_USAGE_FILE", "rel.json")):
            try:
                conf(**{key: bad_value})
                got_ = "accepted"
            except AssertionError as exc:
                got_ = key in str(exc)
            check("conf: %s=%s is refused, by key" % (key, bad_value), got_, True)
        usage_doc(week_util=0.60)
        env_b = make_env(home)
        cb = conf(HEALTH_STATUS_FILE=status_file, BURN_USAGE_FILE=usage_f)
        run_pass(env_b, cb)
        doc_b = json.load(open(status_file))
        check("burn and memory are advisory: a firing one never makes the status file a problem, and opens no GitHub issue",
              (doc_b["result"], "burn" in doc_b["advisory"], "burn" in doc_b["firing"], env_b.runner.writes), ("ok", True, False, []))
        os.unlink(usage_f)
        os.unlink(state_path(make_env(home)))

        # -- upkeep kinds open their GitHub issue but never page Slack (KIT-264) ----------
        # Owner decision 2026-10-27: a Slack ping must mean something is broken. Pending
        # updates and a newer release are upkeep: GitHub only, listed under `advisory`.
        up_home = os.path.join(home, "upkeep")
        os.makedirs(up_home)
        up_file = os.path.join(up_home, "status.json")
        cu = conf(HEALTH_STATUS_FILE=up_file)
        env = make_env(up_home, version="0.2.69", gh=gh_ok(T))
        code = run_pass(env, cu)
        doc = json.load(open(up_file))
        check("pending updates open their GitHub issue but leave the status file ok",
              (code, any("kind=updates" in w["argv"] for w in env.runner.writes),
               doc["result"], doc["firing"], doc.get("advisory"), doc["fingerprint"]),
              (EX_FIRING, True, "ok", [], ["updates"], "ok"))
        import pipeline_heartbeat_monitor as hbm
        seen = hbm.judge_one(hbm.HEALTH_WATCH_JOB, hbm.WATCHERS[hbm.HEALTH_WATCH_JOB],
                             hbm.read_beat(up_file), hbm.parse_iso(doc["written_at"]), 10800,
                             False)
        check("…so the heartbeat monitor reads it as ok, and pages nobody in Slack",
              seen["verdict"], "ok")
        env = make_env(up_home, version="0.2.69", latest="0.2.74", free_gb=1,
                       gh=gh_ok(T + 3600), now=T + 3600)
        run_pass(env, cu)
        doc = json.load(open(up_file))
        check("…an outage kind beside them still makes it a problem, naming only that kind",
              (doc["result"], doc["firing"], doc["advisory"]),
              ("problem", ["disk"], ["release", "updates"]))

        # -- the dispatcher stops: one pass is not enough, two fire, once ---------------
        env = make_env(home, version=None, gh=gh_ok(T))
        check("one silent pass from the dispatcher is UNKNOWN, not a page",
              (run_pass(env, c), env.runner.writes), (EX_UNKNOWN, []))
        env = make_env(home, version=None, gh=gh_ok(T + 3600), now=T + 3600)
        code = run_pass(env, c)
        sent = [w["argv"] for w in env.runner.writes]
        check("the second silent pass fires and tells GitHub once",
              (code, len(sent), "kind=dispatcher" in sent[0], "state=firing" in sent[0]),
              (EX_FIRING, 1, True, True))
        check("…and the status file now names it",
              json.load(open(status_file))["firing"], ["dispatcher"])
        env = make_env(home, version=None, gh=gh_ok(T + 7200), now=T + 7200)
        run_pass(env, c)
        check("a third silent pass sends nothing more (one page per problem)", env.runner.writes, [])
        env = make_env(home, gh=gh_ok(T + 10800), now=T + 10800)
        run_pass(env, c)
        sent = [w["argv"] for w in env.runner.writes]
        check("when it answers again, GitHub is told it cleared",
              (len(sent), "state=ok" in sent[0] if sent else False), (1, True))

        # -- delivery that fails is not recorded as sent ---------------------------------
        bad_gh = [("gh workflow run", 0, ""),
                  ("gh run list", 0, json.dumps([{"databaseId": 78, "createdAt": _iso(T + 20000)}])),
                  ("gh run watch 78", 1, "")]
        env = make_env(home, free_gb=1, gh=bad_gh, now=T + 20000)
        check("a GitHub run that failed is exit 1 (not delivered)", run_pass(env, c), EX_FAILED)
        env = make_env(home, free_gb=1, gh=gh_ok(T + 23600), now=T + 23600)
        run_pass(env, c)
        check("…so the next pass sends it again",
              any("kind=disk" in w["argv"] for w in env.runner.writes), True)

        # -- the front door: no internet is UNKNOWN, never an outage ---------------------
        env = make_env(home, door=None, net=None, free_gb=100, gh=gh_ok(T + 30000), now=T + 30000)
        run_pass(env, c)
        env2 = make_env(home, door=None, net=None, free_gb=100, gh=gh_ok(T + 33600), now=T + 33600)
        code = run_pass(env2, c)
        check("a front door unreachable with no internet never pages (background wake)",
              any("kind=front-door" in w["argv"] for w in env.runner.writes + env2.runner.writes), False)
        env3 = make_env(home, door=530, net=200, gh=gh_ok(T + 37200), now=T + 37200)
        run_pass(env3, c)
        env4 = make_env(home, door=530, net=200, gh=gh_ok(T + 40800), now=T + 40800)
        run_pass(env4, c)
        check("a front door answering 530 twice while the internet works fires",
              any("kind=front-door" in w["argv"] and "state=firing" in w["argv"]
                  for w in env4.runner.writes), True)

    with tempfile.TemporaryDirectory() as home:
        # -- hooks: a fresh root-unresolved record fires, an old one does not -------------
        kit = os.path.join(home, "kit")
        nag = os.path.join(kit, ".claude", ".stop-pr-nag")
        os.makedirs(nag)
        rec = os.path.join(nag, "session-abc" + UNRESOLVED_SUFFIX)
        open(rec, "w").write("1")
        os.utime(rec, (T - 3600, T - 3600))
        c = conf(KIT_CHECKOUT=kit)
        env = make_env(home)
        check("a root-unresolved record from the last day fires `hooks`",
              check_hooks(env, c)["state"], FIRING)
        os.utime(rec, (T - 3 * 86400, T - 3 * 86400))
        check("…and one older than a day does not", check_hooks(env, c)["state"], OK)
        open(os.path.join(nag, "feat_x__no-pr"), "w").write("1")
        check("other Stop-hook records are not mistaken for it", check_hooks(env, c)["state"], OK)

        # -- release and updates --------------------------------------------------------
        check("a release newer than the pin fires `release`",
              check_release(make_env(home, latest="0.2.74"), c)["state"], FIRING)
        check("the pin being latest is ok", check_release(make_env(home), c)["state"], OK)
        check("`latest` (no pin) never fires release",
              check_release(make_env(home, latest="9.9.9"), conf(DISPATCHER_VERSION="latest"))["state"], OK)
        check("an old dispatcher fires `updates`",
              check_updates(make_env(home, version="0.2.69"), c, {})["state"], FIRING)
        st = {}
        check_updates(make_env(home, version="0.2.73"), c, st)
        cached = check_updates(make_env(home, version="0.2.69", now=T + 3600), c, st)
        check("a healthy review is cached for hours, not re-run every pass", cached["state"], OK)
        fresh = check_updates(make_env(home, version="0.2.69", now=T + 21 * 3600), c, st)
        check("…and re-measured after the cache expires", fresh["state"], FIRING)
        # A review that says something is wrong is re-measured every pass, whichever channel
        # carried the alert: here GitHub never recorded it (KIT-251).
        st = {}
        check_updates(make_env(home, version="0.2.69"), c, st)
        check("a firing review is re-measured every pass, even with no alert recorded as sent",
              check_updates(make_env(home, version="0.2.73", now=T + 3600), c, st)["state"], OK)
        st_open = {"kinds": {"updates": {"sent": FIRING}}}
        check_updates(make_env(home, version="0.2.69"), c, st_open)
        st_open["kinds"]["updates"]["sent"] = FIRING
        check("while an `updates` alert is open, a fix clears it on the next pass, not the "
              "cache's expiry", check_updates(make_env(home, version="0.2.73", now=T + 3600), c,
                                              st_open)["state"], OK)
        check("a dispatcher that does not answer neither fires nor clears `updates`",
              check_updates(make_env(home, version=None), c, {})["state"], UNKNOWN)
        # AN UPDATE STOPS THE DISPATCHER (KIT-251). A pass in that window must not close the open
        # alert, nor cache "nothing behind" for 20 hours.
        st = {"kinds": {"updates": {"sent": FIRING}}}
        check_updates(make_env(home, version="0.2.69"), c, st)
        review_before = json.dumps(st.get("review"), sort_keys=True)
        mid = check_updates(make_env(home, version=None, now=T + 3600), c, st)
        check("…an open alert survives a pass that could not read the dispatcher",
              (mid["state"], json.dumps(st.get("review"), sort_keys=True) == review_before),
              (UNKNOWN, True))
        check("…and the next pass that can read it still fires",
              check_updates(make_env(home, version="0.2.69", now=T + 7200), c, st)["state"], FIRING)

        # -- channels OFF are said, never silent ----------------------------------------
        quiet[:] = []
        env = make_env(home, free_gb=1)
        run_pass(env, conf(ALERT_REPO="", KIT_CHECKOUT=kit))
        check("with ALERT_REPO empty the pass says GitHub is OFF",
              any("GitHub alerts: OFF" in q for q in quiet), True)
        check("…and with no status file it says the monitor has nothing to read",
              any("HEALTH_STATUS_FILE is empty" in q for q in quiet), True)

        # -- two kinds in one pass each watch their OWN run (KIT-249) -------------------------
        # `updates` is sent first and its run succeeds; `disk`'s run fails. Before the fix, the
        # disk send could pick the updates run, which had already succeeded, and record disk as
        # sent, so its failed run was never retried.
        two_home = os.path.join(home, "two")
        os.makedirs(two_home)
        two_gh = [("gh workflow run", 0, ""),
                  ("gh run list", 0, json.dumps([{"databaseId": 200, "createdAt": _iso(T + 5)}])),
                  ("gh run watch 200", 0, ""), ("gh run watch 201", 1, "the run failed")]
        # GitHub lists each run one listing late, so the disk send's first look finds only the
        # updates run: it must not take it.
        env = make_env(two_home, version="0.2.69", free_gb=1, gh=two_gh,
                       runner=lambda table: FakeGitHub(table, lag=1))
        code = run_pass(env, conf(KIT_CHECKOUT=kit))
        kinds = load_state(env).get("kinds") or {}
        check("two kinds sent in one pass: each is judged by its own run",
              (code, kinds.get("updates", {}).get("sent"), kinds.get("disk", {}).get("sent", OK)),
              (EX_FAILED, FIRING, OK))

        # -- temp space (KIT-461): past the line it fires, naming the largest; a count that
        #    could not run or finish is UNKNOWN; under the line it clears -------------------
        t_home = os.path.join(home, "temp")
        os.makedirs(t_home)
        t_file = os.path.join(t_home, "status.json")
        ct = conf(HEALTH_STATUS_FILE=t_file)
        G = 1 << 30

        def counted(scratch, tmpdir, whole=True):
            def count(path, stop_at, deadline):
                if isinstance(scratch, Exception):
                    raise scratch
                return (sum(scratch.values()), scratch, whole) if path == "/s" else \
                       (sum(tmpdir.values()), tmpdir, whole)
            return count

        env = make_env(t_home, gh=gh_ok(T))
        env.temp_areas = lambda: [("session scratch", "/s"), ("TMPDIR", "/t")]
        env.temp_measure = counted({"sess-a": 7 * G, "sess-b": 2 * G}, {"stage-e-x": 3 * G})
        code = run_pass(env, ct)
        doc = json.load(open(t_file))
        said = next((f["summary"] for f in doc["findings"] if f["kind"] == "temp"), "")
        check("temp space past the line fires, pages like disk, and tells GitHub once",
              (code, doc["result"], doc["firing"],
               [("kind=temp" in w["argv"], "state=firing" in w["argv"]) for w in env.runner.writes]),
              (EX_FIRING, "problem", ["temp"], [(True, True)]))
        check("…naming the total, the line and the largest folder first",
              ("12.0 GB, past the 10 GB line" in said, said.index("/s/sess-a (7.0 GB)")
               < said.index("/t/stage-e-x (3.0 GB)") if "/t/stage-e-x" in said else False),
              (True, True))
        check("…and saying what it cannot see: the dispatcher's own temp space",
              "dispatcher's account keeps its own" in said, True)
        env = make_env(t_home, gh=gh_ok(T + 3600), now=T + 3600)
        env.temp_areas = lambda: [("session scratch", "/s"), ("TMPDIR", "/t")]
        env.temp_measure = counted(PermissionError(13, "Permission denied"), {})
        run_pass(env, ct)
        doc = json.load(open(t_file))
        check("a count that cannot run is UNKNOWN: no clear, no new page",
              (env.runner.writes, doc["unknown"], doc["firing"]), ([], ["temp"], []))
        env = make_env(t_home, gh=gh_ok(T + 7200), now=T + 7200)
        env.temp_areas = lambda: [("session scratch", "/s"), ("TMPDIR", "/t")]
        env.temp_measure = counted({"sess-a": G}, {}, whole=False)
        run_pass(env, ct)
        check("a count that runs out of time under the line is UNKNOWN, not a clear",
              (env.runner.writes, json.load(open(t_file))["unknown"]), ([], ["temp"]))
        env = make_env(t_home, gh=gh_ok(T + 10800), now=T + 10800)
        env.temp_areas = lambda: [("session scratch", "/s"), ("TMPDIR", "/t")]
        env.temp_measure = counted({"sess-a": G}, {"x": G})
        run_pass(env, ct)
        check("under the line it clears, and GitHub is told so",
              [("kind=temp" in w["argv"], "state=ok" in w["argv"]) for w in env.runner.writes],
              [(True, True)])
        check("TEMP_CEILING_GB defaults to 10, and anything but a positive whole number is a "
              "config error",
              (ct["TEMP_CEILING_GB"],
               [any("TEMP_CEILING_GB" in e for e in pu.validate_conf(dict(
                   {k: v for k, v in ct.items() if isinstance(v, str)}, TEMP_CEILING_GB=v))[1])
                for v in ("0", "ten", "1.5")]), ("10", [True, True, True]))

        # The page job's two settings (KIT-549): a heartbeat path must be absolute, and the age must be a
        # whole number of minutes from 5 to 10080, both ends included.
        base_c = {k: v for k, v in ct.items() if isinstance(v, str)}

        def page_errors(**over):
            return [e for e in pu.validate_conf(dict(base_c, **over))[1]
                    if "WATCHER_PAGE" in e]
        check("WATCHER_PAGE_MAX_AGE_MINUTES defaults to 120, and only 5 to 10080 whole minutes pass",
              (ct["WATCHER_PAGE_MAX_AGE_MINUTES"],
               [bool(page_errors(WATCHER_PAGE_MAX_AGE_MINUTES=v))
                for v in ("0", "4", "5", "10080", "10081", "two hours", "1.5", "")]),
              ("120", [True, True, False, False, True, True, True, True]))
        check("WATCHER_PAGE_HEARTBEAT_FILE: empty and absolute pass, relative is a config error",
              [bool(page_errors(WATCHER_PAGE_HEARTBEAT_FILE=v)) for v in ("", "/srv/w/page-heartbeat.json",
                                                                          "page-heartbeat.json")],
              [False, False, True])

        # The real count: allocated blocks per top-level entry, symlinks never followed.
        tree = os.path.join(t_home, "tree")
        os.makedirs(os.path.join(tree, "a", "deep"))
        with open(os.path.join(tree, "a", "deep", "f"), "wb") as fh:
            fh.write(b"x" * 300000)
        with open(os.path.join(tree, "b"), "wb") as fh:
            fh.write(b"y" * 100000)
        with open(os.path.join(t_home, "outside"), "wb") as fh:
            fh.write(b"z" * 2000000)
        os.symlink(os.path.join(t_home, "outside"), os.path.join(tree, "link"))
        total, sizes, whole = measure(tree, 1 << 40, time.time() + 60)
        check("the real count adds each top-level entry's blocks and never follows a symlink",
              (whole, sizes["a"] >= 300000, sizes["b"] >= 100000, sizes["link"] < 100000,
               total == sum(sizes.values())), (True, True, True, True, True))
        total, sizes, whole = measure(tree, 50000, time.time() + 60)
        check("…stops once the total passes the line, and says it did not see everything",
              (whole, total > 50000), (False, True))
        check("…and a folder that is not there holds nothing, which is a full count",
              measure(os.path.join(t_home, "absent"), 1, time.time() + 60), (0, {}, True))
        # What the count cannot see: a folder this account locked hides its own work, so the
        # count is not whole. A folder macOS will not list for an app (owner bits set), and
        # an entry removed mid-count, are not this account's to count and leave it whole.
        # The listing is refused by a stand-in, so the cases hold for root too; only the
        # folder's own mode bits tell a lock from macOS's refusal.
        locked = os.path.join(tree, "a", "locked")
        os.makedirs(os.path.join(locked, "inner"))
        real_scandir = os.scandir

        def counted_with(refused, mode):
            def scandir(p="."):
                if p in refused:
                    raise refused[p]
                return real_scandir(p)
            os.chmod(locked, mode)
            os.scandir = scandir
            try:
                return measure(tree, 1 << 40, time.time() + 60)[2], check_temp(env, ct)
            finally:
                os.scandir = real_scandir
                os.chmod(locked, 0o700)
        env = make_env(t_home, gh=gh_ok(T + 14400), now=T + 14400)
        env.temp_areas = lambda: [("TMPDIR", tree)]
        refuse = {locked: PermissionError(13, "Permission denied")}
        whole, found = counted_with(refuse, 0)
        check("a folder this account locked (mode 000) makes the count incomplete", whole, False)
        check("…so under the line it reads UNKNOWN and says why",
              (found["state"], "could not see all of it" in found["summary"]), (UNKNOWN, True))
        whole, found = counted_with(
            dict(refuse, **{os.path.join(tree, "a", "deep"): FileNotFoundError(2, "gone")}), 0o700)
        check("…while one macOS will not list despite its owner bits, and one removed "
              "mid-count, leave it whole", (whole, found["state"]), (True, OK))
        saved_platform = sys.platform
        try:
            sys.platform = "darwin"
            env = make_env(t_home, gh=[("getconf DARWIN_USER_TEMP_DIR", 0, "/var/folders/zz/T/\n")])
            del env.temp_areas
            mac = temp_areas(env)
            sys.platform = "linux"
            env = make_env(t_home, environ={"TMPDIR": "/tmp"})
            del env.temp_areas
            shared = temp_areas(env)
        finally:
            sys.platform = saved_platform
        check("on macOS the TMPDIR counted is the account's own, asked of getconf",
              [area for area in mac if area[0] == "TMPDIR"],
              [("TMPDIR", os.path.realpath("/var/folders/zz/T/"))])
        check("where TMPDIR is /tmp itself, the session scratch inside it is counted once",
              [label for label, _ in shared], ["TMPDIR"])
        # A kind the alert workflow does not list is refused there, and that kind's issue is
        # never filed: every kind this job sends needs its option and its fixed title. A
        # Slack-only kind is never sent there.
        with open(os.path.join(KIT, "templates", "workflows", "pipeline-alert.yml")) as fh:
            alert = fh.read()
        check("every kind this job sends is an option of the alert workflow, with a fixed title",
              [k for k in KINDS if k not in SLACK_ONLY_KINDS
               and (("- %s\n" % k) not in alert or ("'%s': " % k) not in alert)], [])

        # -- the conflict waker (KIT-339): off unless named; fires on the second bad pass;
        #    reaches Slack through the status file alone, and never opens a GitHub issue -----
        wk_dir = os.path.join(home, "waker", "state")
        os.makedirs(wk_dir)
        wk_beat = os.path.join(wk_dir, "heartbeat.json")

        def waker_beat(**fields):
            with open(wk_beat, "w") as fh:
                json.dump(dict({"schema": WAKER_SCHEMA}, **fields), fh)
        env, st_w, cw = make_env(home), {}, conf(CONFLICT_WAKER_STATE_DIR=wk_dir)
        check("the waker is said off where CONFLICT_WAKER_STATE_DIR is not set",
              check_conflict_waker(env, conf(), st_w)["summary"].split(":")[0],
              "CONFLICT_WAKER_STATE_DIR is not set")
        waker_beat(result="idle", at=_iso(T - 60), finished_at=_iso(T - 60))
        check("a waker checking in is ok", check_conflict_waker(env, cw, st_w)["state"], OK)
        stale = []
        for name, fields in (("stale", {"result": "ok", "finished_at": _iso(T - 7200)}),
                             ("wedged", {"result": "running", "at": _iso(T - 7200)}),
                             ("problems", {"result": "problems", "finished_at": _iso(T - 60)}),
                             ("never", None)):
            if fields is None:
                os.remove(wk_beat)
            else:
                waker_beat(**fields)
            st_w = {}
            two = [check_conflict_waker(env, cw, st_w) for _ in range(2)]
            stale.append((name, two[0]["state"], two[1]["state"]))
        check("a stale, wedged, failing or silent waker is unknown on one pass, fires on two",
              stale, [(n, UNKNOWN, FIRING) for n in ("stale", "wedged", "problems", "never")])
        wk_home = os.path.join(home, "waker-pass")
        os.makedirs(wk_home)
        wk_status = os.path.join(home, "waker-status.json")
        env = make_env(wk_home, gh=gh_ok(T))
        for _ in range(2):
            run_pass(env, conf(CONFLICT_WAKER_STATE_DIR=wk_dir, HEALTH_STATUS_FILE=wk_status,
                               KIT_CHECKOUT=kit))
        check("…its firing reaches the status file the monitor reads, and no GitHub issue",
              (json.load(open(wk_status))["firing"], env.runner.writes), (["conflict-waker"], []))

        # -- the watcher's page job (KIT-549): off unless its heartbeat is named; a stale, silent,
        #    unreadable, foreign or failing one fires on the second bad pass; it names the route that
        #    failed, and reaches Slack through the status file alone --------------------------------
        pg_dir = os.path.join(home, "pager")
        os.makedirs(pg_dir)
        pg_beat = os.path.join(pg_dir, "page-heartbeat.json")

        def pager_beat(**fields):
            with open(pg_beat, "w") as fh:
                json.dump(dict({"schema": PAGE_SCHEMA}, **fields), fh)
        env, st_p, cp = make_env(home), {}, conf(WATCHER_PAGE_HEARTBEAT_FILE=pg_beat)
        check("the page job is said off where WATCHER_PAGE_HEARTBEAT_FILE is not set",
              check_watcher_page(env, conf(), st_p)["summary"].split(":")[0],
              "WATCHER_PAGE_HEARTBEAT_FILE is not set")
        pager_beat(result="ok", at=_iso(T - 60), routes={"slack": "ok", "github": "ok"})
        check("a page job checking in, every page sent, is ok", check_watcher_page(env, cp, st_p)["state"], OK)
        bad = []
        for name, fields in (("stale", {"result": "ok", "at": _iso(T - 3 * 3600)}),
                             ("could-not-page", {"result": "could-not-page", "at": _iso(T - 60),
                                                 "routes": {"slack": "failed", "github": "ok"}}),
                             ("an unknown result", {"result": "fine, thanks", "at": _iso(T - 60)}),
                             ("no time", {"result": "ok"}),
                             ("another shape", "other"),
                             ("unreadable", "{ nope"),
                             ("never", None)):
            if fields is None:
                os.remove(pg_beat)
            elif fields == "other":
                with open(pg_beat, "w") as fh:
                    json.dump({"schema": "other/1", "result": "ok", "at": _iso(T)}, fh)
            elif fields == "{ nope":
                with open(pg_beat, "w") as fh:
                    fh.write(fields)
            else:
                pager_beat(**fields)
            st_p = {}
            two = [check_watcher_page(env, cp, st_p) for _ in range(2)]
            bad.append((name, two[0]["state"], two[1]["state"]))
        check("a stale, failing, foreign, unreadable or silent page job is unknown on one pass, fires on two",
              bad, [(n, UNKNOWN, FIRING) for n in ("stale", "could-not-page", "an unknown result", "no time",
                                                   "another shape", "unreadable", "never")])
        pager_beat(result="could-not-page", at=_iso(T - 60),
                   routes={"slack": "failed", "github": "failed", "x<script>": "failed"})
        st_p = {}
        said_p = [check_watcher_page(env, cp, st_p) for _ in range(2)][1]["summary"]
        check("a failing page job names the routes that failed, and only those",
              ("by github and slack" in said_p, "<script>" in said_p), (True, False))
        pager_beat(result="ok", at=_iso(T - 90 * 60))
        check("the age is the configured one: 90 minutes is fine under 120, stale under 60",
              (check_watcher_page(env, cp, {})["state"],
               check_watcher_page(env, conf(WATCHER_PAGE_HEARTBEAT_FILE=pg_beat,
                                            WATCHER_PAGE_MAX_AGE_MINUTES="60"), {})["state"]), (OK, UNKNOWN))
        pager_beat(result="could-not-page", at=_iso(T - 60), routes={"slack": "failed"})
        pg_home = os.path.join(home, "pager-pass")
        os.makedirs(pg_home)
        pg_status = os.path.join(home, "pager-status.json")
        env = make_env(pg_home, gh=gh_ok(T))
        for _ in range(2):
            run_pass(env, conf(WATCHER_PAGE_HEARTBEAT_FILE=pg_beat, HEALTH_STATUS_FILE=pg_status,
                               KIT_CHECKOUT=kit))
        check("...its firing reaches the status file the monitor reads, and no GitHub issue",
              (json.load(open(pg_status))["firing"], env.runner.writes), (["watcher-page"], []))
        with open(os.path.join(KIT, "templates", "workflows", "pipeline-alert.yml")) as fh:
            check("...and it is a Slack-only kind, so the alert workflow need not know it",
                  ("watcher-page" in SLACK_ONLY_KINDS, "watcher-page" in fh.read()), (True, False))

        # -- the bridge's push-connection job (KIT-561): off unless its heartbeat is named; a stale,
        #    disconnected, silent, unreadable, foreign or missing one fires on the second bad pass; it
        #    repeats nothing the file says, and reaches Slack through the status file alone ---------
        pc_dir = os.path.join(home, "push")
        os.makedirs(pc_dir)
        pc_beat = os.path.join(pc_dir, "bridge-push-heartbeat.json")

        def push_beat(**fields):
            doc = {"schema": PUSH_SCHEMA, "at": _iso(T - 20), "connected": True,
                   "last_heard_at": _iso(T - 40), "error": None}
            doc.update(fields)
            with open(pc_beat, "w") as fh:
                json.dump(doc, fh)
        env, st_c, cc = make_env(home), {}, conf(PUSH_HEARTBEAT_FILE=pc_beat)
        check("the push job is said off where PUSH_HEARTBEAT_FILE is not set",
              check_push_connection(env, conf(), st_c)["summary"].split(":")[0],
              "PUSH_HEARTBEAT_FILE is not set")
        push_beat()
        check("a push job connected and recently heard is ok",
              check_push_connection(env, cc, st_c)["state"], OK)
        bad = []
        for name, make in (("stale", lambda: push_beat(at=_iso(T - 3600))),
                           ("not connected", lambda: push_beat(connected=False,
                                                               error="could not connect: <b>x</b>")),
                           ("silent", lambda: push_beat(last_heard_at=_iso(T - 3600))),
                           ("never heard", lambda: push_beat(last_heard_at=None)),
                           ("no time", lambda: push_beat(at=None)),
                           ("another shape", lambda: push_beat(schema="other/1")),
                           ("unreadable", lambda: open(pc_beat, "w").write("{ nope")),
                           ("never written", lambda: os.remove(pc_beat))):
            make()
            st_c = {}
            two = [check_push_connection(env, cc, st_c) for _ in range(2)]
            bad.append((name, two[0]["state"], two[1]["state"]))
        check("a stale, disconnected, silent, foreign, unreadable or missing push job is unknown on "
              "one pass and fires on two",
              bad, [(n, UNKNOWN, FIRING) for n in ("stale", "not connected", "silent", "never heard",
                                                   "no time", "another shape", "unreadable",
                                                   "never written")])
        push_beat(connected=False, error="could not connect: <script>alert(1)</script>")
        st_c = {}
        said_c = [check_push_connection(env, cc, st_c) for _ in range(2)][1]["summary"]
        check("a disconnected push job is named in our words, and nothing the file says is repeated",
              ("not connected to Slack" in said_c, "<script>" in said_c, "once a minute" in said_c),
              (True, False, True))
        push_beat(connected=False)
        pc_home = os.path.join(home, "push-pass")
        os.makedirs(pc_home)
        pc_status = os.path.join(home, "push-status.json")
        env = make_env(pc_home, gh=gh_ok(T))
        for _ in range(2):
            run_pass(env, conf(PUSH_HEARTBEAT_FILE=pc_beat, HEALTH_STATUS_FILE=pc_status,
                               KIT_CHECKOUT=kit))
        check("...its firing reaches the status file the monitor reads, and no GitHub issue",
              (json.load(open(pc_status))["firing"], env.runner.writes), (["push-connection"], []))
        with open(os.path.join(KIT, "templates", "workflows", "pipeline-alert.yml")) as fh:
            check("...and it is a Slack-only kind, so the alert workflow need not know it",
                  ("push-connection" in SLACK_ONLY_KINDS, "push-connection" in fh.read()),
                  (True, False))
        check("a relative PUSH_HEARTBEAT_FILE is refused by name",
              ([e for e in pu.validate_conf(dict(base_c, PUSH_HEARTBEAT_FILE="push.json"))[1]
                if "PUSH_HEARTBEAT_FILE" in e] != [],
               [e for e in pu.validate_conf(dict(base_c, PUSH_HEARTBEAT_FILE="/srv/push.json"))[1]
                if "PUSH_HEARTBEAT_FILE" in e]), (True, []))

        # -- the yearly credentials (KIT-579): the owner writes each date down; the kind
        #    warns CREDENTIAL_WARN_DAYS ahead and after, names only, and is upkeep -------------
        import datetime as _dt
        day0 = _dt.datetime.fromtimestamp(T, _dt.timezone.utc).date()

        def on(days):
            return (day0 + _dt.timedelta(days=days)).isoformat()

        def clocks_for(*pairs, **over):
            return conf(CREDENTIAL_EXPIRIES=",".join("%s:%s" % (n, on(d)) for n, d in pairs), **over)
        env = make_env(home)
        check("with no dates set the kind is ok, and says nothing is watched",
              (check_credentials(env, conf())["state"],
               "CREDENTIAL_EXPIRIES is not set" in check_credentials(env, conf())["summary"]),
              (OK, True))
        far = check_credentials(env, clocks_for(("dispatcher-token", 200), ("domain", 340)))
        check("dates beyond the warning are ok, and the next one is named",
              (far["state"], "dispatcher-token on %s" % on(200) in far["summary"]), (OK, True))
        near = check_credentials(env, clocks_for(("dispatcher-token", 200), ("github-token", 12)))
        check("a date inside the warning fires, names only that one, and counts the days",
              (near["state"], "github-token expires on %s (in 12 days)" % on(12) in near["summary"],
               "dispatcher-token" in near["summary"]), (FIRING, True, False))
        check("the edge: exactly the warning's last day fires, one day more does not",
              (check_credentials(env, clocks_for(("a", 30)))["state"],
               check_credentials(env, clocks_for(("a", 31)))["state"]), (FIRING, OK))
        words = check_credentials(env, clocks_for(("late", -3), ("today", 0), ("tomorrow", 1),
                                                  ("yesterday", -1)))["summary"]
        check("a date past, today and tomorrow are said in plain words, singular and plural right",
              ("late expired on %s (3 days ago)" % on(-3) in words,
               "today expires today (%s)" % on(0) in words, "tomorrow expires on %s (in 1 day)" % on(1) in words,
               "yesterday expired on %s (1 day ago)" % on(-1) in words), (True, True, True, True))
        check("CREDENTIAL_WARN_DAYS moves the line: 45 days out fires under 60 and not under 30",
              (check_credentials(env, clocks_for(("a", 45), CREDENTIAL_WARN_DAYS="60"))["state"],
               check_credentials(env, clocks_for(("a", 45), CREDENTIAL_WARN_DAYS="30"))["state"]),
              (FIRING, OK))
        TOKENISH = "ghp_" + "A1b2C3d4E5" * 3
        raw = dict(conf(), CREDENTIAL_EXPIRIES="ok-one:%s,%s:2027-01-01,two:2027-02-30" % (on(300), TOKENISH))
        said_bad = check_credentials(env, raw)
        check("a value in the conf that is not a name and a date is UNKNOWN, judged nothing, and never repeated",
              (said_bad["state"], TOKENISH in said_bad["summary"], "entry 2" in said_bad["summary"],
               "entry 3" in said_bad["summary"]), (UNKNOWN, False, True, True))

        def cred_errors(**over):
            vals, errs = pu.validate_conf(dict({"KIT_CHECKOUT": "/kit"}, **over))
            return errs
        check("the conf accepts none, a name and a date, and two of them",
              [cred_errors(CREDENTIAL_EXPIRIES=v) for v in ("", "a:2027-07-27", "a:2027-07-27, b-2:2027-09-08")],
              [[], [], []])
        refused_c = [cred_errors(CREDENTIAL_EXPIRIES=v) for v in (
            "A:2027-07-27", "a:2027-7-27", "a:2027-02-30", "a", ":2027-07-27", "a:2027-07-27,a:2027-08-01",
            "x" * 33 + ":2027-07-27", TOKENISH, ",".join("n%d:2027-07-27" % i for i in range(31)))]
        check("…and refuses a bad case, a short date, an impossible date, no date, no name, a repeat, a long name, a token and 31 entries",
              [bool(e) for e in refused_c], [True] * 9)
        check("…without ever printing the refused value",
              [TOKENISH in " ".join(e) for e in refused_c], [False] * 9)
        check("CREDENTIAL_WARN_DAYS defaults to 30, and only 14 to 365 whole days pass",
              (conf()["CREDENTIAL_WARN_DAYS"],
               [bool(cred_errors(CREDENTIAL_WARN_DAYS=v)) for v in ("13", "14", "365", "366", "0", "soon", "")]),
              ("30", [True, False, False, True, True, True, True]))
        cr_home = os.path.join(home, "credentials")
        os.makedirs(cr_home)
        cr_file = os.path.join(cr_home, "status.json")
        cc = clocks_for(("dispatcher-token", 5), HEALTH_STATUS_FILE=cr_file)
        env = make_env(cr_home, gh=gh_ok(T))
        code = run_pass(env, cc)
        doc = json.load(open(cr_file))
        check("a date inside the warning opens its GitHub issue, and leaves the status file ok: upkeep, no Slack page",
              (code, any("kind=credentials" in w["argv"] and "state=firing" in w["argv"] for w in env.runner.writes),
               doc["result"], doc["firing"], doc["advisory"]), (EX_FIRING, True, "ok", [], ["credentials"]))
        check("…and nothing in the alert or the status file carries more than names and dates",
              ("ghp_" in json.dumps(doc), "ghp_" in json.dumps([w["argv"] for w in env.runner.writes])), (False, False))
        env = make_env(cr_home, gh=gh_ok(T + 3600), now=T + 3600)
        code = run_pass(env, clocks_for(("dispatcher-token", 340), HEALTH_STATUS_FILE=cr_file))
        check("…and a moved date closes it again",
              (code, any("kind=credentials" in w["argv"] and "state=ok" in w["argv"] for w in env.runner.writes)),
              (EX_OK, True))
        k_home = os.path.join(home, "credentials-set")
        os.makedirs(k_home)

        def k_pass(pairs, hours):
            envk = make_env(k_home, gh=gh_ok(T + hours * 3600), now=T + hours * 3600)
            run_pass(envk, clocks_for(*pairs, HEALTH_STATUS_FILE=os.path.join(k_home, "s.json")))
            return [w["argv"] for w in envk.runner.writes]
        first = k_pass([("a", 5), ("b", 200)], 0)
        same = k_pass([("a", 5), ("b", 200)], 1)
        later_days = k_pass([("a", 5), ("b", 200)], 25)
        second_due = k_pass([("a", 5), ("b", 20)], 26)
        counting = k_pass([("a", 4), ("b", 19)], 27)
        gone = k_pass([("a", 4), ("b", 300)], 28)
        closed = k_pass([("a", 300), ("b", 300)], 29)
        check("the issue opens once, is sent again only when a second credential comes due or one leaves, and closes",
              [(len(w), any("state=firing" in x for x in w) if w else None) for w in
               (first, same, later_days, second_due, counting, gone, closed)],
              [(1, True), (0, None), (0, None), (1, True), (0, None), (1, True), (1, False)])
        check("…and the second send names both credentials",
              ("a expires" in json.dumps(second_due) and "b expires" in json.dumps(second_due)), True)
        with open(os.path.join(KIT, "templates", "workflows", "pipeline-alert.yml")) as fh:
            alert_c = fh.read()
        check("…the kind is one the alert workflow lists, as an option and a fixed title",
              (("          - credentials\n" in alert_c), ("'credentials': " in alert_c),
               "credentials" in KINDS, "credentials" in UPKEEP_KINDS, "credentials" in SLACK_ONLY_KINDS),
              (True, True, True, True, False))

        # -- the notifier and the heartbeat monitor (KIT-286): the clerks watch each other.
        #    Off unless their heartbeat file is named; a stale, unreadable or silent one fires
        #    on the second bad pass, opens its GitHub issue, and closes it on recovery ---------
        hb_dir = os.path.join(home, "clerks")
        os.makedirs(hb_dir)
        beats = {"notifier": (os.path.join(hb_dir, "notifier-heartbeat.json"),
                              "pipeline-notifier-heartbeat/1", "NOTIFIER_HEARTBEAT_FILE"),
                 "monitor": (os.path.join(hb_dir, "monitor-heartbeat.json"),
                             "pipeline-heartbeat-monitor-heartbeat/1", "MONITOR_HEARTBEAT_FILE")}

        def clerk_beat(kind, age=60, schema=None, at=True):
            path, want, _key = beats[kind]
            doc = {"schema": schema or want}
            if at:
                doc["at"] = _iso(T - age)
            with open(path, "w") as fh:
                json.dump(doc, fh)
        env = make_env(home)
        for kind, (path, _schema, key) in beats.items():
            check("%s is said off where %s is not set" % (kind, key),
                  (check_clerk(env, conf(), {}, kind)["state"],
                   check_clerk(env, conf(), {}, kind)["summary"].split(":")[0]), (OK, key + " is not set"))
            clerk_beat(kind)
            check("%s checking in is ok" % kind,
                  check_clerk(env, conf(**{key: path}), {}, kind)["state"], OK)
            outcomes = []
            for name, setup in (("stale", lambda: clerk_beat(kind, age=7200)),
                                ("other schema", lambda: clerk_beat(kind, schema="x/1")),
                                ("no time", lambda: clerk_beat(kind, at=False)),
                                ("never", lambda: os.remove(path))):
                setup()
                st_c = {}
                two = [check_clerk(env, conf(**{key: path}), st_c, kind)["state"] for _ in range(2)]
                outcomes.append((name, two[0], two[1]))
            check("a %s beat that is stale, in another shape, timeless or missing is unknown on "
                  "one pass and fires on two" % kind, outcomes,
                  [(n, UNKNOWN, FIRING) for n in ("stale", "other schema", "no time", "never")])
        # One kind per pass: a fake GitHub holds one run to watch, and a real one two kinds in
        # one pass is judged above (KIT-249).
        for kind, (path, _schema, key) in beats.items():
            cl_home = os.path.join(home, "clerks-pass-" + kind)
            os.makedirs(cl_home)
            cl_status = os.path.join(cl_home, "status.json")
            cl_conf = conf(HEALTH_STATUS_FILE=cl_status, KIT_CHECKOUT=kit, **{key: path})
            clerk_beat(kind, age=7200)
            env = make_env(cl_home, gh=gh_ok(T))
            for _ in range(2):
                run_pass(env, cl_conf)
            check("a stale %s opens its own GitHub issue, once, and reaches the status file the "
                  "monitor reads" % kind,
                  ([("state=firing" in w["argv"]) for w in env.runner.writes
                    if "kind=" + kind in w["argv"]], json.load(open(cl_status))["firing"]),
                  ([True], [kind]))
            clerk_beat(kind)
            env = make_env(cl_home, gh=gh_ok(T))
            run_pass(env, cl_conf)
            check("on recovery the %s issue is closed, once" % kind,
                  [("state=ok" in w["argv"]) for w in env.runner.writes
                   if "kind=" + kind in w["argv"]], [True])

        # A heartbeat file must be an absolute path, or the conf is refused by name (KIT-286).
        rel = pu.validate_conf({"DISPATCHER_SERVICE": "com.test.dispatcher",
                                "ROLE_ACCOUNT": "_testdispatch",
                                "DISPATCHER_ENV_FILE": "/opt/test-dispatch/env",
                                "DISPATCHER_VERSION": "0.2.73", "KIT_CHECKOUT": "/kit",
                                "NOTIFIER_HEARTBEAT_FILE": "state/hb.json"})[1]
        check("a relative NOTIFIER_HEARTBEAT_FILE is refused by name",
              [e for e in rel if "NOTIFIER_HEARTBEAT_FILE" in e] != [], True)

        # -- dry run sends and writes nothing ----------------------------------------------
        env = make_env(home, free_gb=1, gh=gh_ok(T + 50000), now=T + 50000)
        env.runner.dry_run = True
        before = json.dumps(load_state(env), sort_keys=True)
        run_pass(env, conf(HEALTH_STATUS_FILE=os.path.join(home, "dry.json"), KIT_CHECKOUT=kit),
                 dry_run=True)
        check("--dry-run applies no send", env.runner.applied, [])
        check("…writes no status file", os.path.exists(os.path.join(home, "dry.json")), False)
        check("…and leaves the state alone", json.dumps(load_state(env), sort_keys=True), before)

        # -- the installer ---------------------------------------------------------------
        c = conf(KIT_CHECKOUT=kit)
        c["__source__"] = "/kit/update.conf"
        check("install is refused in an agent environment",
              main(["--conf", "/nonexistent", "install"], env=make_env(home, environ={"CLAUDECODE": "1"})),
              EX_REFUSED)
        try:
            refuse_if_agent("install", {"CLAUDECODE": "1"})
            said = ""
        except pu.Refusal as exc:
            said = str(exc)
        check("…and the refusal names this script's own command, not the update script's",
              ("python3 scripts/pipeline_watch.py install" in said, "pipeline_update.py" in said,
               "dispatcher" in said), (True, False, False))
        env = make_env(home, answer="no")
        check("install without a typed yes installs nothing",
              (install(env, c), os.path.exists(os.path.join(home, "Library", "LaunchAgents",
                                                            c["WATCH_LABEL"] + ".plist"))),
              (EX_FIRING, False))
        # THE PREVIEW SENDS NOTHING (KIT-249). With something firing and GitHub on, the old
        # preview dispatched the alert before the yes; a "no" then left an issue open that
        # nothing closes, and a "yes" paged twice.
        preview_home = os.path.join(home, "preview")
        os.makedirs(preview_home)
        launchd = [("launchctl bootout", 0, ""), ("launchctl bootstrap", 0, "")]
        firing_conf = conf(KIT_CHECKOUT=kit)
        firing_conf["__source__"] = "/kit/update.conf"
        for answer in ("no", "yes"):
            env = make_env(preview_home, version="0.2.69", gh=gh_ok(T) + launchd, answer=answer)
            install(env, firing_conf)
            check("the install preview sends no alert, answered %s" % answer,
                  "gh workflow run" in env.runner.applied, False)
        # A fresh home, so the folder is the install's doing and not an earlier pass's.
        check("…a yes stops any old job, then loads the new one",
              [a for a in env.runner.applied if a.startswith("launchctl")],
              ["launchctl bootout", "launchctl bootstrap"])
        check("…and creates the folder the job's log goes in (launchd will not)",
              os.path.isdir(os.path.join(preview_home, ".pipeline-update")), True)
        env = make_env(preview_home, gh=launchd, answer="no")
        install(env, c)
        check("…and a no leaves a job that is already loaded running",
              "launchctl bootout" in env.runner.applied, False)

        # A REINSTALL WAITS FOR LAUNCHD TO LET GO, AND RETRIES EIO (KIT-251). launchd keeps a
        # booted-out job's record for a while; a bootstrap inside that window answers EIO.
        def reinstall(**knobs):
            env = make_env(preview_home, runner=lambda table: GuiLaunchd(table, **knobs))
            pauses[:] = []
            try:
                return install(env, c), env.runner.attempts, len(pauses), ""
            except pu.UpdateError as exc:
                return None, env.runner.attempts, len(pauses), str(exc)
        code, attempts, waited, _ = reinstall(linger=2, boots=(1, 0))
        check("a reinstall waits for the old job to leave, then retries one EIO",
              (code, attempts, waited >= 2), (EX_OK, 2, True))
        code, attempts, _, said = reinstall(boots=(1,), err="Bootstrap failed: 37: Operation already in progress")
        check("…a failure that is not EIO is not retried, and says the job is not loaded",
              (code, attempts, "NOT loaded" in said), (None, 1, True))
        code, attempts, _, said = reinstall(linger=10 ** 6)
        check("…and a job launchd never lets go of stops it before any bootstrap",
              (code, attempts, "still holds" in said), (None, 0, True))
        env = make_env(home, gh=[("launchctl bootout", 0, ""), ("launchctl bootstrap", 0, "")])
        code = install(env, c)
        plist = os.path.join(home, "Library", "LaunchAgents", c["WATCH_LABEL"] + ".plist")
        doc = plistlib.load(open(plist, "rb"))
        check("install writes the LaunchAgent and loads it in the user's own domain",
              (code, doc["StartInterval"], doc["ProgramArguments"][2:],
               any(w["argv"][:2] == ["launchctl", "bootstrap"] and w["argv"][2].startswith("gui/")
                   for w in env.runner.writes)),
              (EX_OK, 3600, ["--conf", "/kit/update.conf", "run"], True))
        # THE JOB'S OWN COMMAND LINE, through the real parser (KIT-249): before this, the plist
        # put `--conf` after `run`, argparse refused it, and every pass exited 2 while the
        # assertion above checked that same broken order.
        job_conf = os.path.join(home, "job.conf")
        with open(job_conf, "w", encoding="utf-8") as fh:
            fh.write("".join("%s=%s\n" % (k, v) for k, v in c.items() if not k.startswith("__")))
        argv = list(doc["ProgramArguments"][2:])
        argv[1] = job_conf
        try:
            job_home = os.path.join(home, "job")
            os.makedirs(job_home)
            parsed = main(argv, env=make_env(job_home))
        except SystemExit as exc:
            parsed = "argparse refused it: exit %s" % exc.code
        check("the job's command line is one the script accepts, and runs a pass",
              parsed in (EX_OK, EX_FIRING, EX_UNKNOWN), True)
        check("the job's PATH ends with the system directories",
              doc["EnvironmentVariables"]["PATH"].endswith("/usr/bin:/bin:/usr/sbin:/sbin"), True)
        # launchd runs the job from `/`, so the settings path it is given must be absolute
        # however `--conf` was typed (KIT-247).
        with open(os.path.join(home, "watch-rel.conf"), "w", encoding="utf-8") as fh:
            fh.write("".join("%s=%s\n" % (k, v) for k, v in conf(KIT_CHECKOUT=kit).items()
                             if not k.startswith("__")))
        here = os.getcwd()
        try:
            os.chdir(home)
            code = main(["--conf", "watch-rel.conf", "install"],
                        env=make_env(home, gh=[("launchctl bootout", 0, ""),
                                               ("launchctl bootstrap", 0, "")]))
        finally:
            os.chdir(here)
        doc = plistlib.load(open(plist, "rb"))
        check("install with a relative --conf gives the job an absolute settings path",
              (code, os.path.isabs(doc["ProgramArguments"][3]),
               os.path.realpath(doc["ProgramArguments"][3])),
              (EX_OK, True, os.path.realpath(os.path.join(home, "watch-rel.conf"))))

    check("the probe names itself: Cloudflare refuses Python's default user agent",
          _request("https://door.example.com/status").get_header("User-agent"), USER_AGENT)

    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
