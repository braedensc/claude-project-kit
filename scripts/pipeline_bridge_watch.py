#!/usr/bin/env python3
"""The Slack bridge watches the notifier (KIT-285): one that stopped, or fails, is said.

    python3 scripts/pipeline_bridge_watch.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports each one back, so `pipeline_bridge.X` is still the very same
object. Its cases sit below its selftest marker, moved from the bridge's (KIT-312, part
3), and the bridge's selftest runs them too, most of them end to end through one pass.
Its own selftest runs them as well, and pins that it holds no HTTP call and no GraphQL
write of its own, so Slack and the tracker stay behind the bridge's two clients.

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
    "unreadable": ("I can't read the notifier's check-in or its schedule, so I can't tell "
                   "whether your pages are going out. "),
}
NOTIFIER_BACK = "The notifier is back: its run at %s finished cleanly."
# After "can't read": the notifier may never have stopped, so it is not called back.
NOTIFIER_READABLE = ("I can read the notifier's check-in and its schedule again: its run at "
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
# Selftest — offline: this module's cases, moved here from the bridge's selftest (KIT-312),
# and its pins. The bridge's selftest runs these cases too, each in its place among the
# others, with one kit for all of them.
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_watch(kit):
    """The notifier watch (KIT-285): its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    import io
    import time
    import pipeline_bridge_watch as watch_module
    from pipeline_bridge import (ALICE, BOB, CHAN, EXAMPLE_CONFIG, EXIT_ERROR, EXIT_OK,
                                 EXIT_TIMEOUT, OWNER, STATE_SCHEMA, _config_error,
                                 run_command, state_path)
    with kit as tmp:
        check, now, run, world = kit.take("check", "now", "run", "world")
        # 13b. THE NOTIFIER WATCH (KIT-285). Its heartbeat is judged on every pass: a problem
        #      is said once where the owner looks, its end once more, and with no notifier
        #      named the watch is OFF. The bridge runs every minute, as launchd runs it at
        #      its default 60 s, so a longer gap is a sleep. Each case runs alone, so one
        #      that raises fails as itself. Times are pinned on a UTC clock.
        outcome = kit.outcome

        import plistlib
        every = kit.every
        quiet = 3 * every + 120                       # the bound the bridge must derive
        check_line = ("To check it, run `python3 scripts/pipeline_notifier_setup.py verify` "
                      "in your kit checkout.")
        quiet_text = ("I haven't heard from the notifier since 8:53am, so you may be missing "
                      "pages. " + check_line)
        unreadable_text = ("I can't read the notifier's check-in or its schedule, so I "
                           "can't tell whether your pages are going out. " + check_line)
        beat, notifier_plist, nworld = kit.take("beat", "notifier_plist", "nworld")

        def mine(s):
            """The bridge's messages of their own, never a reply: the watch's."""
            return [x for ch, th, x in s.posts if th is None and ch == CHAN]

        def minutes(c, s, t, start, end):
            """The bridge's passes every minute from start to end, both in: the last log."""
            out, at = "", start
            while at <= end:
                out = run(c, s, t, at=at)[1]
                at += 60
            return out

        def story(c, s, t, hb, beats, until):
            """The bridge every minute from now to now + until, with the notifier writing
            each of `beats`, (seconds after now, exit, dry), when its time comes. At each
            beat's first pass: how many of the watch's messages are out, and that pass's
            log."""
            todo, seen, at = sorted(beats), [], now
            while at <= now + until:
                due = [b for b in todo if now + b[0] <= at]
                for b in due:
                    beat(hb, now + b[0], b[1], dry=b[2])
                    todo.remove(b)
                out = run(c, s, t, at=at)[1]
                if due:
                    seen.append((len(mine(s)), out))
                at += 60
            return seen

        saved_tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            def case_off():
                c, s, t = world("watch-off")
                # Where the notifier's heartbeat would be: OFF reads none of it.
                os.makedirs(c["state_dir"], exist_ok=True)
                with open(os.path.join(c["state_dir"], "notifier-heartbeat.json"), "w") as fh:
                    fh.write("{not json")
                run(c, s, t)
                code, out = run(c, s, t, at=now + 5000)
                return (code, "notifier watch: OFF (notifier_plist is unset" in out, s.posts,
                        os.path.exists(watch_path(c)))
            check("watch: with no notifier named each pass says the watch is OFF, reads no "
                  "heartbeat, records nothing and posts nothing", outcome(case_off),
                  (EXIT_OK, True, [], False))

            def case_quiet():
                c, s, t, hb = nworld("watch-quiet")
                beat(hb, now)
                out = minutes(c, s, t, now, now + quiet)
                steps = [len(mine(s))]
                run(c, s, t, at=now + quiet + 1)
                steps.append(len(mine(s)))
                minutes(c, s, t, now + quiet + 61, now + quiet + 361)
                steps.append(len(mine(s)))
                return (steps, mine(s),
                        "quiet after 1020 s (3 x its 300 s interval + 120 s)" in out)
            check("watch: nothing newer than 3 of its 300 s intervals + 120 s is quiet: said "
                  "once, never every pass, and not again after a restart",
                  outcome(case_quiet), ([0, 1, 1], [quiet_text], True))

            def case_sleep():
                c, s, t, hb = nworld("watch-sleep")
                beat(hb, now)
                minutes(c, s, t, now, now + 240)
                woke = now + 240 + 1500                       # both jobs slept
                first = run(c, s, t, at=woke)[1]
                minutes(c, s, t, woke + 60, woke + quiet)
                counts = [len(mine(s))]
                run(c, s, t, at=woke + quiet + 1)
                counts.append(len(mine(s)))
                return (counts, mine(s), "notifier watch: waiting" in first,
                        "counting from this pass" in first)
            check("watch: after a sleep it waits for the notifier's next run, then counts "
                  "quiet from the wake", outcome(case_sleep),
                  ([0, 1], [quiet_text], True, True))

            def case_never():
                c, s, t, _hb = nworld("watch-never")
                minutes(c, s, t, now, now + quiet)
                counts = [len(mine(s))]
                for at in (now + quiet + 1, now + quiet + 61):
                    run(c, s, t, at=at)
                    counts.append(len(mine(s)))
                return counts, mine(s)
            check("watch: a notifier that never wrote a heartbeat is quiet once the bound has "
                  "passed since the watch began", outcome(case_never),
                  ([0, 1, 1], ["I haven't seen the notifier run on its schedule once since "
                               "8:53am, so you may be missing pages. " + check_line]))

            def case_failing():
                c, s, t, hb = nworld("watch-failing")
                seen = story(c, s, t, hb, ((0, 3, False), (300, 3, False), (600, 1, False),
                                           (900, 0, False)), 960)
                return [n for n, _o in seen] + [len(mine(s))], mine(s)
            check("watch: a second failed run is said once, from the first one's time, and "
                  "the next clean run says it is back, once", outcome(case_failing),
                  ([0, 1, 1, 2, 2],
                   ["The notifier is running, but its runs have been failing since 8:53am, "
                    "so a page may not have reached you. " + check_line,
                    "The notifier is back: its run at 9:08am finished cleanly."]))

            def case_blip():
                c, s, t, hb = nworld("watch-blip")
                seen = story(c, s, t, hb, ((0, 1, False), (300, 0, False), (600, 3, False),
                                           (900, 3, False)), 900)
                return [n for n, _o in seen], mine(s)
            check("watch: one failed run, then a clean one, says nothing, and the count "
                  "starts again", outcome(case_blip),
                  ([0, 0, 0, 1], ["The notifier is running, but its runs have been failing "
                                  "since 9:03am, so a page may not have reached you. "
                                  + check_line]))

            def case_rehearsal():
                c, s, t, hb = nworld("watch-rehearsal")
                seen = story(c, s, t, hb, ((0, 3, False), (300, 3, False), (400, 0, True),
                                           (600, 0, False)), 600)
                return ([n for n, _o in seen],
                        ["notifier watch: rehearsal" in o for _n, o in seen])
            check("watch: a --dry-run heartbeat is no clean run, and changes nothing",
                  outcome(case_rehearsal), ([0, 1, 1, 2], [False, False, True, False]))

            def case_rehearsal_fresh():
                c, s, t, hb = nworld("watch-rehearsal-fresh")
                seen = story(c, s, t, hb, ((0, 0, False), (100, 0, True)), 600)
                return mine(s), "notifier watch: rehearsal" in seen[1][1]
            check("watch: a --dry-run heartbeat with nothing said yet says nothing",
                  outcome(case_rehearsal_fresh), ([], True))

            def unreadable_once(name, plist_doc=None, body=None, plist_gone=False):
                c, s, t, hb = nworld("watch-" + name, plist_doc)
                if body is not None:
                    with open(hb, "w") as fh:
                        fh.write(body)
                if plist_gone:
                    os.remove(c["notifier_plist"])
                code1, out = run(c, s, t)
                code2, _o = run(c, s, t, at=now + 60)
                return code1, code2, mine(s), "notifier watch: unreadable" in out

            def stamped(**over):
                return json.dumps(dict({"schema": notify.HEARTBEAT_SCHEMA, "at": iso(now),
                                        "dry": False, "exit": 0}, **over))
            not_notifier = dict(notifier_plist, ProgramArguments=[
                "/bin/sh", "-c", "exec /usr/bin/python3 $HOME/kit/scripts/pipeline_bridge.py"])
            no_interval = dict((k, v) for k, v in notifier_plist.items()
                               if k != "StartInterval")
            for name, slug, kw in (
                    ("its heartbeat is not JSON", "not-json", {"body": "{not json"}),
                    ("its heartbeat is another job's", "other-schema",
                     {"body": stamped(schema="pipeline-bridge-heartbeat/1")}),
                    ("its heartbeat has no time", "no-time", {"body": stamped(at=None)}),
                    ("its heartbeat is stamped in the future", "future",
                     {"body": stamped(at=iso(now + 121))}),
                    ("its heartbeat is a JSON list", "json-list", {"body": "[]"}),
                    ("its plist is gone", "plist-gone", {"plist_gone": True}),
                    ("its plist runs another job", "plist-other", {"plist_doc": not_notifier}),
                    ("its plist names no interval", "plist-no-interval",
                     {"plist_doc": no_interval}),
                    ("its plist's interval is true, not a number", "plist-true-interval",
                     {"plist_doc": dict(notifier_plist, StartInterval=True)}),
                    ("its plist's interval is 0", "plist-zero-interval",
                     {"plist_doc": dict(notifier_plist, StartInterval=0)}),
                    ("its plist's interval is below 0", "plist-negative-interval",
                     {"plist_doc": dict(notifier_plist, StartInterval=-5)})):
                check("watch: %s: said once, as can't tell, never as healthy" % name,
                      outcome(lambda: unreadable_once(slug, **kw)),
                      (EXIT_OK, EXIT_OK, [unreadable_text], True))
            check("watch: a heartbeat a minute ahead of this clock is read, not refused",
                  outcome(lambda: unreadable_once("skew", body=stamped(at=iso(now + 60)))),
                  (EXIT_OK, EXIT_OK, [], False))

            # KIT-332: with members listed for DMs, the watch speaks in each listed DM the
            # bridge already reads, where the owner looks, not in the first channel. Until a
            # listed DM is read (its first pass is a baseline), the first channel hears it,
            # and the watch never opens a DM itself.
            def dm_said(s):
                return sorted((ch, x) for ch, th, x in s.posts if th is None and ch[:1] == "D")

            def case_dm_quiet():
                c, s, t, hb = nworld("watch-dm", dm_user_ids=[ALICE, BOB])
                beat(hb, now)
                minutes(c, s, t, now, now + quiet)
                run(c, s, t, at=now + quiet + 1)
                return mine(s), dm_said(s)
            check("watch: in DM mode a problem is said in each listed member's DM, not in the "
                  "channel (KIT-332)", outcome(case_dm_quiet),
                  ([], [("D0ALICE001", quiet_text), ("D0BOB00001", quiet_text)]))

            def case_dm_first_pass():
                c, s, t, _hb = nworld("watch-dm-first", no_interval, dm_user_ids=[ALICE])
                code, _out = run(c, s, t)
                return code, mine(s), dm_said(s), s.reads.count("conversations.open")
            check("watch: before any listed DM is read, the first channel hears it, and the "
                  "watch opens no DM of its own", outcome(case_dm_first_pass),
                  (EXIT_OK, [unreadable_text], [], 1))

            def case_dm_back():
                c, s, t, hb = nworld("watch-dm-back", dm_user_ids=[ALICE])
                beat(hb, now)
                minutes(c, s, t, now, now + quiet)
                run(c, s, t, at=now + quiet + 1)
                beat(hb, now + quiet + 30)
                run(c, s, t, at=now + quiet + 61)
                return [x[:24] for _ch, x in dm_said(s)], mine(s)
            check("watch: in DM mode the notifier coming back is said in the DM too",
                  outcome(case_dm_back),
                  (["I haven't heard from the", "The notifier is back: it"], []))

            def case_unreadable_back():
                c, s, t, hb = nworld("watch-unreadable-back")
                with open(hb, "w") as fh:
                    fh.write("{not json")
                story(c, s, t, hb, ((300, 0, False),), 360)
                return mine(s)[1:]
            check("watch: a heartbeat it can read again, and clean, is said once to be "
                  "readable again, never called back", outcome(case_unreadable_back),
                  ["I can read the notifier's check-in and its schedule again: its run at "
                   "8:58am finished cleanly."])

            def case_post_fails():
                c, s, t, _hb = nworld("watch-post-fails")
                minutes(c, s, t, now, now + quiet)
                s.fail.add("chat.postMessage")
                code1, out1 = run(c, s, t, at=now + quiet + 1)
                s.fail.discard("chat.postMessage")
                code2, _o = run(c, s, t, at=now + quiet + 61)
                return code1, "could not post in Slack" in out1, len(mine(s)), code2
            check("watch: a post that fails makes the pass exit 1, and the next pass says it",
                  outcome(case_post_fails), (EXIT_ERROR, True, 1, EXIT_OK))

            # Stage E review: a different problem after the first is said too, each kind at
            # most once until a clean run, and the end is said as the end of the last one.
            def failing_since(when):
                return ("The notifier is running, but its runs have been failing since %s, so "
                        "a page may not have reached you. " % when + check_line)

            def quiet_since(when):
                return ("I haven't heard from the notifier since %s, so you may be missing "
                        "pages. " % when + check_line)

            def back_at(when):
                return "The notifier is back: its run at %s finished cleanly." % when

            def readable_at(when):
                return ("I can read the notifier's check-in and its schedule again: its run "
                        "at %s finished cleanly." % when)

            def drive(c, s, t, hb, plan, start, end, logs=None):
                """The bridge every minute from now + start to now + end. Before a pass at an
                offset in `plan`, the heartbeat becomes plan[offset]: (exit, its run's offset
                from now), or None for one it cannot read. Each pass's exit code; with a list
                for `logs`, each pass's log goes on it too."""
                codes = []
                for off in range(start, end + 1, 60):
                    if off in plan and plan[off] is None:
                        with open(hb, "w") as fh:
                            fh.write("{not json")
                    elif off in plan:
                        beat(hb, now + plan[off][1], plan[off][0])
                    code, out = run(c, s, t, at=now + off)
                    codes.append(code)
                    if logs is not None:
                        logs.append(out)
                return codes

            def changed(slug, plan, end):
                """What it posted, and each word its logs give for what it said before."""
                c, s, t, hb = nworld("watch-then-" + slug)
                logs, mark = [], "; said so in Slack, after it said "
                drive(c, s, t, hb, plan, 0, end, logs)
                return mine(s), [ln.split(mark, 1)[1].split(";")[0].split(" |")[0]
                                 for out in logs for ln in out.splitlines() if mark in ln]
            for name, slug, plan, end, want, first in (
                    ("can't tell then quiet", "unreadable-quiet",
                     {0: (0, 0), 60: None, 1260: (0, 0), 1500: (0, 1500)}, 1560,
                     [unreadable_text, quiet_text, back_at("9:18am")], "can't read"),
                    ("quiet then failing", "quiet-failing",
                     {0: (0, 0), 1200: (3, 1200), 1500: (3, 1500), 1800: (0, 1800)}, 1860,
                     [quiet_text, failing_since("9:13am"), back_at("9:23am")], "quiet"),
                    ("failing then quiet", "failing-quiet",
                     {0: (3, 0), 300: (3, 300), 1500: (0, 1500)}, 1560,
                     [failing_since("8:53am"), quiet_since("8:58am"), back_at("9:18am")],
                     "failing"),
                    ("can't tell then failing", "unreadable-failing",
                     {0: None, 300: (3, 300), 600: (3, 600), 900: (0, 900)}, 960,
                     [unreadable_text, failing_since("8:58am"), back_at("9:08am")], "can't read"),
                    ("quiet then can't tell", "quiet-unreadable",
                     {0: (0, 0), 1200: None, 1500: (0, 1500)}, 1560,
                     [quiet_text, unreadable_text, readable_at("9:18am")], "quiet")):
                check("watch: %s: both said, once each, and the end matches the last; the log "
                      "names the first in the owner's words" % name,
                      outcome(lambda: changed(slug, plan, end)), (want, [first]))

            def case_alternating():
                c, s, t, hb = nworld("watch-alternating")
                plan = dict((off, (0, -5000)) for off in range(0, 1021, 60))   # waiting
                for k, off in enumerate(range(1080, 2821, 60)):     # thirty passes, then
                    plan[off] = ((0, -5000), None, (3, off))[k % 3]  # quiet, can't, failing
                drive(c, s, t, hb, plan, 0, 2820)
                during = len(mine(s))
                drive(c, s, t, hb, {2880: (0, 2880)}, 2880, 2940)
                return during, mine(s)
            check("watch: a problem that changes kind every pass is said at most once a kind "
                  "until a clean run", outcome(case_alternating),
                  (3, [quiet_since("7:30am"), unreadable_text, failing_since("9:13am"),
                       back_at("9:41am")]))

            def case_again():
                c, s, t, hb = nworld("watch-again")
                drive(c, s, t, hb, {0: (0, 0), 1200: (0, 1200)}, 0, 2280)
                return mine(s)
            check("watch: a problem that ends, then comes again, is said again",
                  outcome(case_again),
                  [quiet_text, back_at("9:13am"), quiet_since("9:13am")])

            def case_second_post_fails():
                c, s, t, hb = nworld("watch-second-post-fails")
                plan = {0: None, 300: (3, 300), 600: (3, 600)}
                drive(c, s, t, hb, plan, 0, 540)
                s.fail.add("chat.postMessage")
                codes = drive(c, s, t, hb, plan, 600, 600)
                s.fail.discard("chat.postMessage")
                code, out = run(c, s, t, at=now + 660)
                return (codes + [code], mine(s),
                        "; said so in Slack, after it said can't read" in out)
            check("watch: a second problem whose post fails is said by the next pass, and its "
                  "log names the first in the owner's words", outcome(case_second_post_fails),
                  ([EXIT_ERROR, EXIT_OK], [unreadable_text, failing_since("8:58am")], True))

            # Review round 2. A record it cannot write is named as that: Slack was not called.
            def case_record_named():
                c, s, t, hb = nworld("watch-record-named")
                beat(hb, now)                          # a healthy notifier: nothing to say
                os.makedirs(watch_path(c))             # its record's path is a folder
                code, out = run(c, s, t)
                return (code, s.posts, "could not save the notifier watch's record %s (Is a "
                        "directory); until it can, it says nothing new in Slack"
                        % watch_path(c) in out, "could not post" in out)
            check("watch: a record it cannot write is named as that, never as a post that "
                  "failed", outcome(case_record_named), (EXIT_ERROR, [], True, False))

            # A rehearsal overwrites the heartbeat. Silence is counted from the last scheduled
            # run the watch saw, and the message names that run, never the rehearsal.
            def case_rehearsal_late():
                c, s, t, hb = nworld("watch-rehearsal-late")
                beat(hb, now)                          # its last scheduled run; then it stops
                minutes(c, s, t, now, now + 540)
                beat(hb, now + 600, dry=True)          # a rehearsal, typed or its installer's
                minutes(c, s, t, now + 600, now + quiet)
                steps = [len(mine(s))]
                run(c, s, t, at=now + quiet + 1)
                steps.append(len(mine(s)))
                return steps, mine(s)
            check("watch: a rehearsal while the notifier is stopped does not put the alarm "
                  "off, and the alarm names its last scheduled run",
                  outcome(case_rehearsal_late), ([0, 1], [quiet_text]))

            def case_rehearsal_only():
                c, s, t, hb = nworld("watch-rehearsal-only")
                beat(hb, now - 30, dry=True)           # never loaded: its installer's dry run
                minutes(c, s, t, now, now + quiet)
                steps = [len(mine(s))]
                run(c, s, t, at=now + quiet + 1)
                steps.append(len(mine(s)))
                return steps, mine(s)
            check("watch: with only a rehearsal and no scheduled run seen, it says it has seen "
                  "none since it began", outcome(case_rehearsal_only),
                  ([0, 1], ["I haven't seen the notifier run on its schedule once since "
                            "8:53am, so you may be missing pages. " + check_line]))

            # The Mac's clock was an hour fast, and time sync puts it back. Its own stamps
            # from the fast clock never hold the watch silent.
            def case_clock_back():
                c, s, t, hb = nworld("watch-clock-back")
                fast = now + 3600
                for i in range(10):                    # ten minutes on the fast clock
                    if i % 5 == 0:
                        beat(hb, fast + 60 * i)
                    run(c, s, t, at=fast + 60 * i)
                first, wake = None, None
                for i in range(10, 80):                # the right clock; it stops at minute 25
                    if i % 5 == 0 and i <= 25:
                        beat(hb, now + 60 * i)
                    out = run(c, s, t, at=now + 60 * i)[1]
                    wake = out if wake is None else wake
                    if mine(s) and first is None:
                        first = i
                return first, "this Mac's clock moved back" in wake, mine(s)
            check("watch: a clock that moved back starts the count again, so a notifier that "
                  "stops after it is said on time", outcome(case_clock_back),
                  (43, True, ["I haven't heard from the notifier since 9:18am, so you may be "
                              "missing pages. " + check_line]))

            def case_future_said_beat():
                c, s, t, hb = nworld("watch-future-said-beat")
                with open(hb, "w") as fh:
                    fh.write(stamped(at=iso(now + 7200)))    # two hours ahead: can't tell
                run(c, s, t)
                beat(hb, now + 60)                     # its next run, on this clock
                run(c, s, t, at=now + 90)
                return mine(s)
            check("watch: a heartbeat it had when it spoke, stamped ahead of this clock, does "
                  "not hold the next clean run back", outcome(case_future_said_beat),
                  [unreadable_text, "I can read the notifier's check-in and its schedule "
                                    "again: its run at 8:54am finished cleanly."])

            def case_gap_run_limit():
                c, s, t, hb = nworld("watch-gap-run-limit", run_timeout_seconds=300)
                beat(hb, now)
                run(c, s, t)
                at_gap = run(c, s, t, at=now + 420)[1]   # 2 x its 60 s + its 300 s run limit
                past_gap = run(c, s, t, at=now + 841)[1]
                return ["counting from this pass" in o for o in (at_gap, past_gap)]
            check("watch: the run limit in its config counts toward the gap read as a sleep",
                  outcome(case_gap_run_limit), [False, True])

            def case_unsaved():
                c, s, t, _hb = nworld("watch-unsaved")
                minutes(c, s, t, now, now + quiet)
                s.add(ALICE, "plan PROD-6")            # the rest of the pass still runs
                # record_watch reads save_watch where it lives (KIT-407).
                real = watch_module.save_watch

                def broken(_cfg, _rec):
                    raise OSError("simulated: the state dir is read-only")
                watch_module.save_watch = broken
                try:
                    codes = [run_command(c, False, 30, slack=s, tracker=t, out=io.StringIO(),
                                         now=now + quiet + 1 + 60 * i) for i in range(3)]
                finally:
                    watch_module.save_watch = real
                return codes, len(mine(s)), len([1 for _c, th, _x in s.posts if th])
            check("watch: a record it cannot write posts nothing, so it never posts every "
                  "pass, and the rest of the pass still runs", outcome(case_unsaved),
                  ([EXIT_ERROR] * 3, 0, 1))

            def case_deadline():
                c, s, t, _hb = nworld("watch-deadline")
                minutes(c, s, t, now, now + quiet)
                real_post = s.post

                def cut_short(_channel, _thread, _text):
                    s.post = real_post
                    raise Deadline()
                s.post = cut_short
                code1 = run_command(c, False, 30, slack=s, tracker=t, out=io.StringIO(),
                                    now=now + quiet + 1)
                code2 = run(c, s, t, at=now + quiet + 61)[0]
                return code1, code2, len(mine(s))
            check("watch: a post the deadline cut short is said by the next pass",
                  outcome(case_deadline), (EXIT_TIMEOUT, EXIT_OK, 1))

            def case_stopped_passes():
                c, s, t, _hb = nworld("watch-stopped-passes")
                t.viewer = "someone-else"          # every pass stops at the key check
                codes = [run(c, s, t, at=now + 60 * i)[0] for i in range(19)]
                return sorted(set(codes)), len(mine(s)), os.path.exists(state_path(c))
            check("watch: passes the tracker stops still keep the watch's clock, and say it, "
                  "in a record of its own: they leave no bridge state",
                  outcome(case_stopped_passes), ([EXIT_USAGE], 1, False))

            def case_first_pass_kept():
                c, s, t, hb = nworld("watch-first-pass")
                beat(hb, now)
                s.add(ALICE, "plan PROD-5")            # in the window before the first pass
                t.viewer = "someone-else"              # so the first pass stops at the key
                code1 = run(c, s, t)[0]
                t.viewer = OWNER                       # the owner puts the right key in
                code2, out = run(c, s, t, at=now + 60)
                return (code1, code2, "first pass:" in out,
                        [x for _c, th, x in s.posts if th is not None])
            check("watch: a first pass the key check stopped leaves the next pass a first "
                  "pass, which answers nothing already in the window",
                  outcome(case_first_pass_kept), (EXIT_USAGE, EXIT_OK, True, []))

            def case_dry():
                c, s, t, _hb = nworld("watch-dry")
                minutes(c, s, t, now, now + quiet)
                before = [open(p).read() for p in (state_path(c), watch_path(c))]
                code, out = run(c, s, t, dry=True, at=now + quiet + 1)
                return (code, s.posts, "would post" in out and "would say so" in out,
                        [open(p).read() for p in (state_path(c), watch_path(c))] == before)
            check("watch: the bridge's own dry run posts nothing and records nothing",
                  outcome(case_dry), (EXIT_OK, [], True, True))

            def case_sleep_dead():
                c, s, t, hb = nworld("watch-sleep-dead")
                beat(hb, now)                          # its last run; then it stops
                minutes(c, s, t, now, now + quiet + 60)
                said = len(mine(s))
                wake = now + quiet + 60 + 1500         # the Mac slept 25 minutes
                minutes(c, s, t, wake, wake + 1500)    # 25 minutes awake, and still dead
                after = len(mine(s))
                beat(hb, wake + 1530)                  # it runs again, cleanly
                minutes(c, s, t, wake + 1560, wake + 1680)
                return said, after, mine(s)
            check("watch: a dead notifier is not said to be back after a sleep; its next "
                  "clean run is, once", outcome(case_sleep_dead),
                  (1, 1, [quiet_text,
                          "The notifier is back: its run at 10:01am finished cleanly."]))

            def case_back_needs_newer():
                c, s, t, hb = nworld("watch-back-newer")
                beat(hb, now)
                minutes(c, s, t, now, now + quiet + 60)
                with open(c["notifier_plist"], "wb") as fh:      # now it runs hourly
                    plistlib.dump(dict(notifier_plist, StartInterval=3600), fh)
                out = minutes(c, s, t, now + quiet + 120, now + quiet + 420)
                still = len(mine(s))
                beat(hb, now + 1470)
                minutes(c, s, t, now + 1500, now + 1560)
                return (still, "back waits for a run newer than 2025-10-09T08:53:20Z" in out,
                        mine(s))
            check("watch: back is said only for a clean run newer than the one it had when it "
                  "spoke", outcome(case_back_needs_newer),
                  (1, True, [quiet_text, "The notifier is back: its run at 9:17am finished "
                                         "cleanly."]))

            def case_short_nap():
                c, s, t, hb = nworld("watch-short-nap")
                beat(hb, now)
                minutes(c, s, t, now, now + 240)
                wake = now + 240 + 800                 # 13 minutes asleep: under the bound
                woke = run(c, s, t, at=wake)[1]        # the bridge wakes before the notifier
                beat(hb, wake + 30)                    # the notifier's own run after the wake
                minutes(c, s, t, wake + 60, wake + 600)
                return mine(s), "notifier watch: waiting" in woke
            check("watch: a short sleep is no alarm: the bridge waits for the notifier's run "
                  "after the wake", outcome(case_short_nap), ([], True))

            def case_slow_bridge():
                c, s, t, hb = nworld("watch-slow-bridge", run_interval_seconds=1800)
                beat(hb, now)                          # its last run; then it stops
                for i in range(4):                     # the bridge every 30 minutes
                    run(c, s, t, at=now + 1800 * i)
                return mine(s)
            check("watch: a bridge that runs every 30 minutes still says a dead notifier is "
                  "quiet, once", outcome(case_slow_bridge), [quiet_text])

            def case_gap():
                c, s, t, hb = nworld("watch-gap")
                beat(hb, now)
                run(c, s, t)
                at_gap = run(c, s, t, at=now + 240)[1]   # 2 x its 60 s + its 120 s run limit
                past_gap = run(c, s, t, at=now + 481)[1]
                return ["counting from this pass" in o for o in (at_gap, past_gap)]
            check("watch: a gap of over 2 of its intervals + its run limit is a sleep, and no "
                  "less", outcome(case_gap), [False, True])

            check("watch: it may run at most every half of the notifier's interval, at its "
                  "120 s run limit, to tell a sleep from silence",
                  outcome(lambda: [slowest_run_interval(v, 120) for v in (300, 60, 3600)]),
                  [150, 30, 1800])

            def case_two_channels():
                c, s, t, _hb = nworld("watch-two-channels", channel_ids=[CHAN, "C0000000BB"])
                minutes(c, s, t, now, now + quiet + 60)
                return [ch for ch, th, _x in s.posts if th is None]
            check("watch: it posts in the first channel only", outcome(case_two_channels),
                  [CHAN])

            def case_record():
                c, s, t, _hb = nworld("watch-record")
                minutes(c, s, t, now, now + quiet + 60)
                doc = json.load(open(watch_path(c)))
                return (oct(os.stat(watch_path(c)).st_mode & 0o777), doc.get("schema"),
                        doc.get("said"), "notifier" in json.load(open(state_path(c))))
            check("watch: what it said is in a record of its own, mode 600, and not in the "
                  "state", outcome(case_record),
                  ("0o600", "pipeline-bridge-notifier-watch/1", "quiet", False))

            def record_starts_again(slug, record):
                c, s, t, hb = nworld("watch-record-" + slug)
                beat(hb, now)
                os.makedirs(c["state_dir"], exist_ok=True)
                with open(watch_path(c), "w") as fh:
                    fh.write(record)
                code, out = run(c, s, t)
                return code, "so it starts again" in out, mine(s)
            for name, slug, record in (
                    ("is not JSON", "not-json", "{not json"),
                    ("is another document", "other", json.dumps({"schema": STATE_SCHEMA})),
                    ("is a JSON list", "json-list", "[]"),
                    ("keeps what it said in no list", "said-kinds", json.dumps(
                        {"schema": "pipeline-bridge-notifier-watch/1", "said": "quiet",
                         "said_kinds": 5}))):
                check("watch: a record of its own that %s starts again, and says so" % name,
                      outcome(lambda: record_starts_again(slug, record)), (EXIT_OK, True, []))

            def case_clock():
                at = epoch("2026-10-27T07:02:00Z")
                return [clock(epoch(t), at) for t in (
                    "2026-10-27T07:02:00Z", "2026-10-27T12:00:00Z", "2026-10-27T00:05:00Z",
                    "2026-10-26T23:30:00Z")]
            check("watch: times read as a person says them, with the day when not today",
                  outcome(case_clock), ["7:02am", "12:00pm", "12:05am", "11:30pm on Oct 26"])

            check("config: notifier_plist must be an absolute path to a .plist",
                  outcome(lambda: ["'notifier_plist'" in _config_error(dict(
                      EXAMPLE_CONFIG, notifier_plist=v, state_dir=os.path.join(
                          tmp, "np%d" % i))) for i, v in enumerate((
                              "notifier.plist", "/etc/notifier.json", 7,
                              "/Library/LaunchDaemons/com.example.notifier.plist"))]),
                  [True, True, True, False])
            check("config: run_interval_seconds must be a positive whole number",
                  outcome(lambda: ["'run_interval_seconds'" in _config_error(dict(
                      EXAMPLE_CONFIG, run_interval_seconds=v, state_dir=os.path.join(
                          tmp, "ri%d" % i))) for i, v in enumerate((True, 0, -60, "60", 60))]),
                  [True, True, True, True, False])
        finally:
            if saved_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved_tz
            time.tzset()


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__),
                                                 [cases_watch])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_watch
        sys.exit(pipeline_bridge_watch.selftest())
    print("usage: pipeline_bridge_watch.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
