#!/usr/bin/env python3
"""The Slack bridge watches the notifier (KIT-285): one that stopped, or fails, is said.

    python3 scripts/pipeline_bridge_watch.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports each one back, so `pipeline_bridge.X` is still the very same
object. Its cases run in the bridge's own selftest, end to end through one pass. This
module's own selftest pins that it holds no HTTP call and no GraphQL write of its own,
so Slack and the tracker stay behind the bridge's two clients.

WHAT IS HERE
  The judging    the notifier's heartbeat and its schedule, read each pass: quiet, failing
                 or unreadable (`judge_notifier`), and a gap in the bridge's own passes read
                 as the Mac asleep.
  What it says   each problem once, as this app, and its end once more, from a record of
                 its own beside the state.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_config import (DM_RE, Deadline, EXIT_USAGE, clock, epoch,  # noqa: E402
                                    iso)

# KIT-285: the notifier is quiet once its newest heartbeat is older than this many of its
# own run intervals plus this many seconds: two runs missed, and a third interval for the
# longest one run may take (its installer keeps a run's clock under one interval).
NOTIFIER_QUIET_INTERVALS, NOTIFIER_QUIET_SLACK = 3, 120
WATCH_SCHEMA = "pipeline-bridge-notifier-watch/1"
NOTIFIER_SCRIPT = "pipeline_notify_local.py"
NOTIFIER_PROBLEMS = ("quiet", "failing", "unreadable")
# What the bridge says about it, as its own app: times and one command, never ticket text.
NOTIFIER_CHECK = ("To check it, run `python3 scripts/pipeline_notifier_setup.py verify` in "
                  "your kit checkout.")
NOTIFIER_SAYS = {
    "quiet": "I haven't heard from the notifier since %s, so you may be missing pages. ",
    "never": ("I haven't seen the notifier run on its schedule once since %s, so you may "
              "be missing pages. "),
    "failing": ("The notifier is running, but its runs have been failing since %s, so a "
                "page may not have reached you. "),
    "unreadable": ("I can't read the notifier's heartbeat or its schedule, so I can't tell "
                   "whether your pages are going out. "),
}
NOTIFIER_BACK = "The notifier is back: its run at %s finished cleanly."
# After "can't read": the notifier may never have stopped, so it is not called back.
NOTIFIER_READABLE = ("I can read the notifier's heartbeat and its schedule again: its run at "
                     "%s finished cleanly.")


def notifier_heartbeat_path(cfg):
    """The notifier's heartbeat, in its own state dir, beside the pings file it records."""
    return notify.heartbeat_path({"state_dir": os.path.dirname(cfg["pings_file"])})


def read_notifier(cfg):
    """(interval, why, beat): the notifier's plist and heartbeat as this pass finds them
    (KIT-285). No heartbeat file is beat None; a plist that is not there is a why, since the
    config names it."""
    try:
        with open(cfg["notifier_plist"], "rb") as fh:
            interval, why = notifier_interval(fh.read())
    except OSError as exc:
        interval, why = None, "its plist %s could not be read (%s)" % (
            cfg["notifier_plist"], type(exc).__name__)
    path, beat = notifier_heartbeat_path(cfg), None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            beat = json.load(fh)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        why = why or "its heartbeat %s could not be read (%s)" % (path, type(exc).__name__)
    return interval, why, beat


def watch_path(cfg):
    return os.path.join(cfg["state_dir"], "bridge-notifier-watch.json")


def load_watch(cfg):
    """(record, note): what the notifier watch has said, and when it last looked (KIT-285).
    It is a file of its own, so the state file still means what it did: none is a first
    pass, which answers nothing already in the window. A record that cannot be read starts
    again, and says so: each problem it had already said may be said once more, and a
    notifier back before one is said again goes unsaid."""
    path, fresh = watch_path(cfg), {"schema": WATCH_SCHEMA}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return fresh, None
    except (OSError, ValueError) as exc:
        return fresh, "its record %s could not be read (%s), so it starts again" % (
            path, type(exc).__name__)
    if not isinstance(doc, dict) or doc.get("schema") != WATCH_SCHEMA:
        return fresh, "its record %s is not a %s document, so it starts again" % (
            path, WATCH_SCHEMA)
    if not isinstance(doc.get("said_kinds", []), list):
        return fresh, ("its record %s does not list what it has said, so it starts again"
                       % path)
    return doc, None


def save_watch(cfg, rec):
    """Mode 600 and atomic, as the state is."""
    old = os.umask(0o077)
    try:
        notify._atomic_write_json(watch_path(cfg), rec)
    finally:
        os.umask(old)


def quiet_after(interval):
    """How old the notifier's newest heartbeat may get before it is quiet (KIT-285)."""
    return NOTIFIER_QUIET_INTERVALS * interval + NOTIFIER_QUIET_SLACK


def sleep_gap(run_interval, run_timeout):
    """The gap in this job's own passes it reads as the Mac asleep, or the job stopped: two
    of its runs missed, and the longest one pass may take (KIT-285)."""
    return 2 * run_interval + run_timeout


def slowest_run_interval(interval, run_timeout):
    """The longest interval this job may run at and still tell the Mac's sleep from a quiet
    notifier that runs every `interval` s. A sleep it cannot see, up to sleep_gap, must
    leave two of the notifier's intervals of the quiet bound: one for the newest
    heartbeat's age when the Mac slept, one for the notifier's own run after it wakes."""
    return (quiet_after(interval) - 2 * interval - run_timeout) // 2


def notifier_interval(data):
    """(seconds, why): how often launchd runs the notifier, from its plist's bytes. A plist
    that does not run the notifier, or names no StartInterval, is a why: the heartbeat
    beside it would be judged against the wrong clock."""
    import plistlib
    try:
        doc = plistlib.loads(data)
    except Exception as exc:                                       # noqa: BLE001
        return None, "its plist could not be parsed (%s)" % type(exc).__name__
    args = doc.get("ProgramArguments") if isinstance(doc, dict) else None
    if not isinstance(args, list) or not any(NOTIFIER_SCRIPT in str(a) for a in args):
        return None, "its plist does not run %s" % NOTIFIER_SCRIPT
    every = doc.get("StartInterval")
    if isinstance(every, bool) or not isinstance(every, int) or every <= 0:
        return None, "its plist names no StartInterval"
    return every, None


def judge_notifier(interval, why, beat, now, since=None, real=None):
    """(verdict, at, detail): the notifier as its two files show it, by one rule for this
    job and for the installer's `verify` (KIT-285).

      unreadable  `why` names a file that could not be read, or the heartbeat is not the
                  notifier's, has no time, or is stamped in the future: never healthy (§13)
      quiet       nothing newer than quiet_after(interval), counted from the heartbeat's
                  `at`, or from `since` when that is later: this job was not running either
      waiting     no heartbeat yet, or none since `since`, and not yet quiet: a heartbeat
                  from before this job began counting says nothing about now
      rehearsal   its newest beat is a --dry-run, which says nothing of the scheduled runs
      ok          its newest real run exited 0
      failing     its newest real run exited anything else

    `beat` is the heartbeat document, None when there is no file; `at` is its time. The
    watch passes `since`, and `real`: the newest scheduled run it has seen, in seconds, or
    None. A --dry-run beat overwrites the heartbeat, so the watch counts silence from `real`
    and names it, never the rehearsal. `verify` keeps no record, so it counts from the beat."""
    if why:
        return "unreadable", None, why
    at = None
    if beat is not None:
        if not isinstance(beat, dict) or beat.get("schema") != notify.HEARTBEAT_SCHEMA:
            return "unreadable", None, ("its heartbeat is not a %s document"
                                        % notify.HEARTBEAT_SCHEMA)
        at = epoch(beat.get("at"))
        if at is None:
            return "unreadable", None, "its heartbeat has no readable `at` time"
        if at > now + NOTIFIER_QUIET_SLACK:
            return "unreadable", None, ("its heartbeat is stamped %s, in the future"
                                        % beat.get("at"))
    dry = at is not None and bool(beat.get("dry"))
    ran = real if dry and since is not None else at
    heard = [t for t in (ran, since) if t is not None]
    limit = quiet_after(interval)
    if not heard or now - max(heard) > limit:
        return "quiet", ran, ("nothing from it since %s, over %d s" % (iso(ran), limit)
                              if ran is not None else "no heartbeat from it" if at is None
                              else "no scheduled run from it, only a --dry-run at %s"
                              % iso(at))
    if dry:
        return "rehearsal", at, ("its newest heartbeat, at %s, is a --dry-run, which says "
                                 "nothing about its scheduled runs" % iso(at))
    if at is None:
        return "waiting", None, "no heartbeat from it yet"
    if since is not None and since > at:
        return "waiting", at, ("its newest heartbeat, at %s, is from before %s, when this "
                               "job began counting: its next run says how it is"
                               % (iso(at), iso(since)))
    if beat.get("exit") == 0:
        return "ok", at, "its run at %s exited 0" % iso(at)
    return "failing", at, "its run at %s exited %r" % (iso(at), beat.get("exit"))


class WatchMixin(object):
    """Pass's watch on the notifier (KIT-312)."""

    # -- the notifier (KIT-285) ----------------------------------------------------
    def watch_notifier(self):
        """Judge the notifier's heartbeat (judge_notifier). A problem is said once where
        the owner looks (watch_destinations: the DMs, else the first channel), and so is
        each other kind the first time it comes, and its end once more. Its record is a
        file of its own, written through, so a pass that stops later keeps the watch's
        clock, and a first pass that stops is still a first pass."""
        if not self.cfg["notifier_plist"]:
            self.notifier = "OFF (notifier_plist is unset, so no notifier is watched here)"
            return
        rec, note = load_watch(self.cfg)
        notes = [note] if note else []
        interval, why, beat = read_notifier(self.cfg)
        since, last = rec.get("since"), rec.get("checked_at")
        gap = sleep_gap(self.cfg["run_interval_seconds"], self.cfg["run_timeout_seconds"])
        # A stamp of its own more than NOTIFIER_QUIET_SLACK ahead of this clock means the
        # Mac's clock moved back since it was written. Kept, it would hold the watch silent,
        # or hold back "back", until the clock caught up with it.
        ahead = self.now + NOTIFIER_QUIET_SLACK
        for key in ("said_beat", "bad_at", "real_at"):
            if (epoch(rec.get(key)) or 0) > ahead:
                rec[key] = None
        if not all(isinstance(t, (int, float)) and not isinstance(t, bool)
                   for t in (since, last)):
            rec["since"] = self.now
        elif max(since, last) > ahead:
            rec["since"] = self.now
            notes.append("counting from this pass: this Mac's clock moved back, to before "
                         "this job's own last pass")
        elif self.now - last > gap:
            # This job was not running (the Mac asleep, or the job stopped), so silence is
            # counted from now, and a heartbeat from before now waits for the next one.
            rec["since"] = self.now
            notes.append("counting from this pass: this job's own last pass was %d s ago, "
                         "over %d s" % (self.now - last, gap))
        rec["checked_at"] = self.now
        # The newest scheduled run it has seen: a --dry-run beat overwrites the heartbeat,
        # and silence is counted from this, never from the rehearsal.
        if (isinstance(beat, dict) and beat.get("schema") == notify.HEARTBEAT_SCHEMA
                and not beat.get("dry")
                and (epoch(rec.get("real_at")) or 0) < (epoch(beat.get("at")) or 0) <= ahead):
            rec["real_at"] = beat["at"]
        verdict, at, detail = judge_notifier(interval, why, beat, self.now, rec["since"],
                                             epoch(rec.get("real_at")))
        if verdict == "failing":
            # The notifier sends again what it could not send, so one failed run is not
            # said. A second one is, whenever it comes.
            rec["bad_at"] = rec.get("bad_at") or beat["at"]
            if rec["bad_at"] == beat["at"]:
                verdict = "failed once"
                detail += ("; one failed run is not said, since the notifier tries again: a "
                           "second one is")
        elif verdict == "ok":
            rec["bad_at"] = None
        # Back only after a clean run newer than the heartbeat it had when it said the
        # problem: that one is from before, and says nothing about a comeback. Until then a
        # kind not yet said is said when it first comes, and each of the three is said at
        # most once, however often it changes.
        held, told = epoch(rec.get("said_beat")), ""
        if verdict in NOTIFIER_PROBLEMS and verdict not in rec.get("said_kinds", []):
            was = rec.get("said")
            self.say_notifier(rec, verdict, beat, self.notifier_text(verdict, at, rec),
                              bind=NOTIFIER_CHECK, kind="notifier-" + verdict)
            # What it said before, in the owner's words: quiet, failing or can't read.
            after = ", after it said %s" % ("can't read" if was == "unreadable" else was)
            told = "; %s so in Slack%s" % ("would say" if self.dry else "said",
                                           after if was else "")
        elif verdict == "ok" and rec.get("said") and (held is None or at > held):
            readable = rec["said"] == "unreadable"
            self.say_notifier(rec, None, None, (NOTIFIER_READABLE if readable else
                                                NOTIFIER_BACK) % clock(at, self.now),
                              kind="notifier-readable" if readable else "notifier-back",
                              fact="readable again" if readable else "back")
            told = "; %s in Slack that %s" % ("would say" if self.dry else "said",
                                              "it can read it again" if readable else
                                              "it is back")
        elif verdict == "ok" and rec.get("said"):
            told = ("; said in Slack already, and back waits for a run newer than %s"
                    % rec["said_beat"])
        elif rec.get("said"):
            told = "; said in Slack already"
        self.record_watch(rec)
        self.notifier = "%s: %s%s%s%s" % (
            verdict, detail, "; quiet after %d s (%d x its %d s interval + %d s)" % (
                quiet_after(interval), NOTIFIER_QUIET_INTERVALS, interval,
                NOTIFIER_QUIET_SLACK) if interval else "", told,
            "".join("; " + n for n in notes))

    def notifier_text(self, verdict, at, rec):
        if verdict == "failing":
            text = NOTIFIER_SAYS["failing"] % clock(epoch(rec["bad_at"]), self.now)
        elif verdict == "quiet" and at is not None:
            text = NOTIFIER_SAYS["quiet"] % clock(at, self.now)
        elif verdict == "quiet":
            text = NOTIFIER_SAYS["never"] % clock(rec.get("since") or self.now, self.now)
        else:
            text = NOTIFIER_SAYS["unreadable"]
        # The command to check it is the bind, after one space, as it always read (KIT-297).
        return text.rstrip(" ")

    def say_notifier(self, rec, said, beat, text, bind="", kind="", fact=None):
        """Recorded, then posted: a record that cannot be written posts nothing, so a broken
        state dir can never post this on every pass. A post that fails, or that the deadline
        cuts short, puts the record back, so the next pass says it: once more at worst.
        `said` is the problem said, kept in `said_kinds` with each one said before it since
        the last clean run; None is that run, which clears them. `fact` is what the writer
        is told of a clean run: after "can't read" it is never "back" (KIT-297 review)."""
        before = (rec.get("said"), rec.get("said_beat"), list(rec.get("said_kinds", [])))
        rec["said"] = said
        rec["said_beat"] = beat.get("at") if said and isinstance(beat, dict) else None
        rec["said_kinds"] = before[2] + [said] if said else []
        self.record_watch(rec)
        try:
            for channel in self.watch_destinations():
                self.post(channel, None, text, kind=kind, bind=bind, join=" ",
                          facts={"notifier": said or fact or "back"})
        except (BridgeError, Deadline):
            rec["said"], rec["said_beat"], rec["said_kinds"] = before
            self.record_watch(rec)
            raise

    def watch_destinations(self):
        """Where the notifier watch speaks (KIT-332): in each listed member's DM the bridge
        already reads, since in DM mode that is where the owner looks, and in the first
        channel when no member is listed or no listed DM is read yet. The watch never opens
        a DM itself: a DM read for the first time is a baseline (open_dms), and one the
        watch put in the state would skip it."""
        dms = []
        for uid in self.cfg["dm_user_ids"]:
            channel = self.state["dms"].get(uid)
            if isinstance(channel, str) and DM_RE.match(channel) and channel not in dms:
                dms.append(channel)
        return dms or [self.cfg["channel_ids"][0]]

    def record_watch(self, rec):
        """Write-through, as save() is; the bridge's own dry run records nothing."""
        if not self.dry:
            save_watch(self.cfg, rec)


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline. This module's cases run in the bridge's own selftest, end to end
# through one pass; this one pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__))


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    print("usage: pipeline_bridge_watch.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
