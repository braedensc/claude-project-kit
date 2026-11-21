#!/usr/bin/env python3
"""The runaway stop on messages to a person's chat (KIT-936): one check every sender passes.

    python3 scripts/pipeline_chat_cap.py --selftest

On 20 Nov 2026 the notifier came back after an outage and sent about 30 messages to the
owner's chat in a row, days old, mostly the same alert. KIT-935 makes the notifier say what it
owes in one message. This module is the other half: whatever any job decides to send, past a
limit per chat it is HELD and summed up, never sent one by one.

WHO PASSES IT. Every chat write in the kit goes through one of two clients, and each calls
`guarded_send` before it sends:

  * pipeline_bridge_transport.SlackClient.post (and start_stream): the bridge's replies and
    reach-outs, the tech lead's turns, the stand-up, the status replies, the sprint
    documents' message, the push job's answers. They all hold one SlackClient.
  * pipeline_notify_local.ChatClient.post_message: the notifier's pages, its thread notes and
    its relay.

The selftest reads the scripts and fails when a third file builds a `chat.postMessage` address,
so a new sender cannot appear without this check naming it (or the exception below).
The one exception is the watcher's urgent page (pipeline_watcher_page.py): it runs as its own
account, which cannot read this record, and its page is the safety system's last route to the
owner (once per change of answer, KIT-519). A runaway stop must never hold that one.

THE RULE. Per chat (the channel id a message goes to), at most `max` messages in any
`window_minutes`. The message that would be the next is HELD: kept whole in the record, and not
sent. The first hold of an episode sends ONE notice, which says the limit was reached, what the
held one is about and when the rest will come. After the window the held ones go out together
as ONE digest message, by whichever sender next passes the check, or by `flush_due` from a job
that runs on a timer. A held message is sent once: the digest clears it from the record in the
same locked step that sends it.

THE RECORD. `<state_dir>/chat-cap.json`, under an exclusive lock on `chat-cap.lock`, so the
count holds across jobs and processes. A record that cannot be read, or a lock that cannot be
had, HOLDS: `CannotCheck` is raised and nothing is sent, because a check that cannot count
cannot say a message is safe to send (contract 13: could-not is not nothing-to-do). The
caller's own failure path keeps the message (a page is retried next pass; a bridge post is
a Slack failure).

HEALTH. Reaching the limit writes `<state_dir>/chat-cap-trips.json` (the last twenty trips) and
puts a line on stderr. Like every cap in the pipeline it is a runaway stop: normal use must
never reach it, and reaching it is a defect to look at (KIT-936).

THE DEFAULT, 20 messages in 10 minutes. The repo holds no log or ledger of real traffic
(those live in the pipeline account's private state), so this is a ceiling worked out from the
code's own limits, and it errs high. An owner-driven conversation is the fastest normal
traffic: one reply per turn and a turn takes tens of seconds, so 10 minutes of nonstop chat is
about 10 to 20 messages. Pages come on top, a handful at a time: the notifier's own
per-pass flood guard is 25 (max_events_per_pass), and its pages are one a ticket event. The
stand-up is two a day. 20 is at the top of that range. Change it in the part's settings file
(`chat_cap_max`, `chat_cap_window_minutes`) after looking at what the trip file says.

Standard library only; runs on the Mac's python3 3.9.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time

SCHEMA = "pipeline-chat-cap/1"
TRIPS_SCHEMA = "pipeline-chat-cap-trips/1"
RECORD_NAME = "chat-cap.json"
LOCK_NAME = "chat-cap.lock"
TRIPS_NAME = "chat-cap-trips.json"
DEFAULT_MAX = 20
DEFAULT_WINDOW_MINUTES = 10
MAX_HELD = 100            # per chat; past it a message is counted, not kept, and the digest says so
LOCK_SECONDS = 30
DIGEST_MAX_CHARS = 30000  # Slack takes about 40000 in one message
SUBJECT_CHARS = 100
TRIPS_KEPT = 20

SETTING_KEYS = ("chat_cap_max", "chat_cap_window_minutes")


class HeldToken(str):
    """The stand-in a held message answers with (KIT-941): timestamp-shaped, so a caller has
    something to record, but never a message anywhere. A caller that keeps a record of what it
    posted tests for this type and records the message as held instead; nothing may look a held
    token up as a real message."""


class CannotCheck(Exception):
    """The record cannot be read or locked. Nothing was sent, and nothing was kept."""


def settings(cfg):
    """The check's settings from a part's config, or None for a config that names no state
    folder (a hand-built one in a selftest; every loaded config names it)."""
    if not isinstance(cfg, dict) or not cfg.get("state_dir"):
        return None
    return {"state_dir": cfg["state_dir"],
            "max": cfg.get("chat_cap_max", DEFAULT_MAX),
            "window": cfg.get("chat_cap_window_minutes", DEFAULT_WINDOW_MINUTES) * 60.0}


def validate(raw, errors):
    """The two setting values from a raw config, defaults filled in; a bad one is named in
    `errors`, never quietly replaced."""
    out = {}
    for key, default in (("chat_cap_max", DEFAULT_MAX),
                         ("chat_cap_window_minutes", DEFAULT_WINDOW_MINUTES)):
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100000:
            errors.append("config %r must be a whole number from 1 to 100000, got %r"
                          % (key, value))
            value = default
        out[key] = value
    return out


# -- the record ------------------------------------------------------------------------

def _paths(state_dir):
    return (os.path.join(state_dir, RECORD_NAME), os.path.join(state_dir, LOCK_NAME),
            os.path.join(state_dir, TRIPS_NAME))


@contextlib.contextmanager
def _locked(state_dir, seconds=LOCK_SECONDS, sleep=time.sleep, clock=time.time):
    _, lock_path, _ = _paths(state_dir)
    try:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        handle = open(lock_path, "a")
    except OSError as exc:
        raise CannotCheck("the chat limit's lock file %s cannot be opened (%s)"
                          % (lock_path, type(exc).__name__))
    try:
        until = clock() + seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if clock() >= until:
                    raise CannotCheck("another job held the chat limit's lock for %d seconds"
                                      % seconds)
                sleep(0.05)
        yield
    finally:
        handle.close()


def _read(state_dir):
    path = _paths(state_dir)[0]
    if not os.path.exists(path):
        return {"schema": SCHEMA, "chats": {}, "trips": []}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CannotCheck("the chat limit's record %s cannot be read (%s)"
                          % (path, type(exc).__name__))
    if (not isinstance(doc, dict) or doc.get("schema") != SCHEMA
            or not isinstance(doc.get("chats"), dict) or not isinstance(doc.get("trips"), list)):
        raise CannotCheck("the chat limit's record %s is not in the shape %s" % (path, SCHEMA))
    for entry in doc["chats"].values():
        if (not isinstance(entry, dict) or not isinstance(entry.get("sent"), list)
                or not isinstance(entry.get("held"), list)):
            raise CannotCheck("the chat limit's record %s has an entry in no known shape" % path)
    return doc


def _write(state_dir, doc):
    path = _paths(state_dir)[0]
    try:
        fd, tmp = tempfile.mkstemp(prefix=".chat-cap-", dir=state_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=1, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise CannotCheck("the chat limit's record %s cannot be written (%s)"
                          % (path, type(exc).__name__))


def _entry(doc, key):
    entry = doc["chats"].setdefault(key, {"sent": [], "held": [], "release_at": None,
                                           "noticed": False, "dropped": 0, "via": ""})
    entry.setdefault("dropped", 0)
    entry.setdefault("via", "")
    entry.setdefault("noticed", False)
    entry.setdefault("release_at", None)
    return entry


def _clock_words(at):
    return time.strftime("%H:%M UTC", time.gmtime(at))


def subject_of(text):
    """What a message is about, for the notice and the digest: its first line, short."""
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line[:SUBJECT_CHARS] + ("…" if len(line) > SUBJECT_CHARS else "")
    return "(an empty message)"


def _hash(key, thread, text):
    return hashlib.sha256(("%s\0%s\0%s" % (key, thread or "", text)).encode("utf-8")).hexdigest()[:16]


def notice_text(st, entry, held_text, now):
    return ("I have sent %d messages in %d minutes, which is my limit, so I am holding the "
            "next ones instead of sending them one by one. The first held one is about: %s. "
            "They will come together in one message at about %s. Nothing is lost."
            % (st["max"], int(st["window"] // 60), subject_of(held_text),
               _clock_words(entry["release_at"])))


def digest_text(st, entry, now):
    held = entry["held"]
    total = sum(h.get("n", 1) for h in held) + entry.get("dropped", 0)
    lines = ["%d message%s held back since %s, because I sent more than %d in %d minutes. "
             "Here they are together, oldest first."
             % (total, "" if total == 1 else "s", _clock_words(held[0]["at"]) if held else "?",
                st["max"], int(st["window"] // 60))]
    lines.append("These were held, so a reply under one of them is not tied to it: reply here.")
    lines.append("About: " + "; ".join(h["subject"] for h in held[:8])
                 + ("; and %d more" % (len(held) - 8) if len(held) > 8 else "") + ".")
    if entry.get("dropped"):
        lines.append("%d more were not kept, because %d were already held." % (entry["dropped"],
                                                                              MAX_HELD))
    room = max(120, (DIGEST_MAX_CHARS - sum(len(x) for x in lines)) // max(1, len(held)) - 40)
    shortened = False
    for i, h in enumerate(held, 1):
        body = h["text"]
        if len(body) > room:
            body, shortened = body[:room] + "…", True
        lines.append("%d. (%s%s) %s" % (i, _clock_words(h["at"]),
                                        ", sent %d times" % h["n"] if h.get("n", 1) > 1 else "",
                                        body))
    if shortened:
        lines.append("Some of these were shortened to fit one message.")
    return "\n\n".join(lines)


def _trip(st, key, entry, now, sent_in_window):
    """Reaching the limit, recorded where the health watch can read it."""
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "chat": key,
            "max": st["max"], "window_minutes": int(st["window"] // 60),
            "sent_in_window": sent_in_window, "held": 0}
    return line


def _write_trips(st, doc):
    path = _paths(st["state_dir"])[2]
    body = {"schema": TRIPS_SCHEMA, "trips": doc["trips"][-TRIPS_KEPT:]}
    try:
        fd, tmp = tempfile.mkstemp(prefix=".chat-cap-trips-", dir=st["state_dir"])
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=1, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        sys.stderr.write("NOTE: could not write %s: %s\n" % (path, type(exc).__name__))


def _prune(entry, now, window):
    entry["sent"] = [min(float(t), now) for t in entry["sent"]
                     if isinstance(t, (int, float)) and now - float(t) < window]


def _send_digest(st, doc, key, entry, plain, now):
    """Send the held messages as ONE message and clear them in the same step. A failed send
    leaves them held, whole."""
    plain(digest_text(st, entry, now))
    entry["held"], entry["dropped"] = [], 0
    entry["release_at"], entry["noticed"] = None, False
    entry["held_total"] = 0
    entry["sent"].append(now)


def guarded_send(st, key, text, send, plain, thread=None, via="", keep=True, now=None):
    """Pass one message through the check.

    `send()` posts the message and returns what the client returns; `plain(text)` posts a
    notice or digest, top-level, to the same chat. Returns ("sent", reply) or
    ("held", token) where `token` is a timestamp-shaped string, unique in this record, for a
    caller that needs something to record. `keep=False` counts the hold but does not keep the
    text: for a message the caller will send again whole (a streamed answer that fell back).
    Raises CannotCheck when it cannot count, with nothing sent and nothing kept; whatever
    `send` or `plain` raises comes out unchanged, with the count not taken."""
    now = time.time() if now is None else now
    with _locked(st["state_dir"]):
        doc = _read(st["state_dir"])
        entry = _entry(doc, key)
        _prune(entry, now, st["window"])
        if via and not entry["held"]:
            entry["via"] = via
        if (not entry["held"] and entry["release_at"] is not None
                and now >= entry["release_at"]):
            # An episode of holds that kept nothing (a stream start, an "on it" line) has no
            # digest to end it. Its time has come, so the next trip is a trip (review of KIT-937).
            entry["release_at"], entry["noticed"] = None, False
            entry["held_total"] = 0
        if entry["held"] and entry["release_at"] is not None and now >= entry["release_at"]:
            _send_digest(st, doc, key, entry, plain, now)
            _write(st["state_dir"], doc)
        if entry["held"] or len(entry["sent"]) >= st["max"]:
            return "held", _hold(st, doc, key, entry, text, thread, now, keep, plain)
        reply = send()
        entry["sent"].append(now)
        _write(st["state_dir"], doc)
        return "sent", reply


def _hold(st, doc, key, entry, text, thread, now, keep, plain):
    first = not entry["held"] and entry["release_at"] is None
    if first:
        entry["release_at"] = now + st["window"]
    token = HeldToken("%.6f" % (now + 0.000001 * (len(entry["held"]) + entry.get("dropped", 0))))
    entry["held_total"] = entry.get("held_total", 0) + 1
    if keep:
        h = _hash(key, thread, text)
        same = [x for x in entry["held"] if x.get("h") == h]
        if same:
            same[0]["n"] = same[0].get("n", 1) + 1
        elif len(entry["held"]) >= MAX_HELD:
            entry["dropped"] = entry.get("dropped", 0) + 1
        else:
            entry["held"].append({"h": h, "at": now, "subject": subject_of(text), "text": text,
                                  "thread": thread, "n": 1})
    if first:
        doc["trips"].append(_trip(st, key, entry, now, len(entry["sent"])))
        doc["trips"] = doc["trips"][-TRIPS_KEPT:]
        sys.stderr.write("CHAT LIMIT REACHED: %s got %d messages in %d minutes; holding the "
                         "rest. This is a runaway stop: look at what is sending.\n"
                         % (key, st["max"], int(st["window"] // 60)))
    # The trip says how many messages this episode has held so far (KIT-941): every hold of the
    # episode, kept or not, updates the newest trip of this chat.
    mine = [t for t in doc["trips"] if t.get("chat") == key]
    if mine:
        mine[-1]["held"] = entry["held_total"]
    if not entry["noticed"]:
        try:
            plain(notice_text(st, entry, text, now))
            entry["noticed"] = True
            entry["sent"].append(now)       # the notice is a message too, and counts
        except Exception as exc:            # noqa: BLE001 - the hold stands, the digest says it
            sys.stderr.write("NOTE: the chat limit's notice could not be sent (%s)\n"
                             % type(exc).__name__)
    _write(st["state_dir"], doc)
    _write_trips(st, doc)
    return token


def flush_due(st, via, send_for, now=None):
    """For a job that runs on a timer: every chat of `via` whose held messages are past their
    time goes out as one digest. `send_for(chat)` returns the `plain(text)` for that chat.
    Returns (digests sent, problems). A failed one stays held; a record that cannot be read
    is a problem named, never a quiet zero."""
    now = time.time() if now is None else now
    sent, problems = 0, []
    try:
        with _locked(st["state_dir"]):
            doc = _read(st["state_dir"])
            for key in sorted(doc["chats"]):
                entry = _entry(doc, key)
                if (not entry["held"] or entry["via"] != via or entry["release_at"] is None
                        or now < entry["release_at"]):
                    continue
                try:
                    _send_digest(st, doc, key, entry, send_for(key), now)
                    sent += 1
                    _write(st["state_dir"], doc)
                except CannotCheck:
                    raise
                except Exception as exc:    # noqa: BLE001 - it stays held
                    problems.append("%s: the digest could not be sent (%s); it stays held"
                                    % (key, type(exc).__name__))
    except CannotCheck as exc:
        problems.append(str(exc))
    return sent, problems


def read_trips(path):
    """(trips, problem) from a trip record at `path` (the file itself or a copy of it): the
    trips as a list of dicts, or None and a short plain reason. The reason is "absent" for a
    file that does not exist, because the cap writes the file only at its first trip, so absent
    can mean none yet; every other reason is a record that could not be read (KIT-941)."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, "absent"
    except (OSError, ValueError) as exc:
        return None, "cannot be read (%s)" % type(exc).__name__
    if (not isinstance(doc, dict) or doc.get("schema") != TRIPS_SCHEMA
            or not isinstance(doc.get("trips"), list)):
        return None, "is not in the shape %s" % TRIPS_SCHEMA
    for t in doc["trips"]:
        if (not isinstance(t, dict) or not isinstance(t.get("at"), str)
                or not isinstance(t.get("chat"), str) or not isinstance(t.get("max"), int)
                or not isinstance(t.get("window_minutes"), int)):
            return None, "has a trip in no known shape"
    return doc["trips"], None


def status(st):
    """(held messages, chats with any) read without the lock, for a heartbeat line; None when
    the record cannot be read."""
    try:
        doc = _read(st["state_dir"])
    except CannotCheck:
        return None
    held = [e for e in doc["chats"].values() if e.get("held")]
    return sum(sum(h.get("n", 1) for h in e["held"]) for e in held), len(held)


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline
# ══════════════════════════════════════════════════════════════════════════════════════

def _worker(argv):
    """A second process for the selftest: send `n` messages to one chat, each appended to a log."""
    state_dir, log, n, tag = argv[0], argv[1], int(argv[2]), argv[3]
    st = {"state_dir": state_dir, "max": 12, "window": 600.0}

    def post(text):
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(text.splitlines()[0][:60] + "\n")
    for i in range(n):
        body = "%s %d" % (tag, i)
        guarded_send(st, "D1", body, lambda body=body: post(body), post, via="t", now=1000.0 + i)
    return 0


def _selftest():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scratch_run import contained
    return contained(_selftest_body, "chat-cap-selftest-")


def _selftest_body():
    import re
    import subprocess
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    root = tempfile.mkdtemp(prefix="cap-")
    counter = [0]

    def fresh(**over):
        counter[0] += 1
        d = os.path.join(root, "s%d" % counter[0])
        os.makedirs(d)
        return dict({"state_dir": d, "max": 3, "window": 600.0}, **over)

    class Sink(object):
        def __init__(self):
            self.sent = []

        def post(self, text):
            self.sent.append(text)

        def one(self, name):
            return lambda: self.post(name)

    # 1. Under the limit everything goes, in order, once each.
    st, sink = fresh(), Sink()
    got = [guarded_send(st, "D1", "m%d" % i, sink.one("m%d" % i), sink.post, now=100.0 + i)[0]
           for i in range(3)]
    check("under the limit every message is sent", (got, sink.sent), (["sent"] * 3, ["m0", "m1", "m2"]))

    # 2. Past it: nothing is sent one by one; ONE notice; the rest held.
    got = [guarded_send(st, "D1", "extra %d" % i, sink.one("extra %d" % i), sink.post,
                        now=110.0 + i)[0] for i in range(5)]
    notices = [s for s in sink.sent if s.startswith("I have sent")]
    check("past the limit the rest are held", got, ["held"] * 5)
    check("past the limit exactly one notice goes out, and no held message",
          (len(sink.sent), len(notices), any(s.startswith("extra") for s in sink.sent)),
          (4, 1, False))
    check("the notice says when and what", ("about: extra 0" in notices[0], "UTC" in notices[0]),
          (True, True))
    trips = json.load(open(_paths(st["state_dir"])[2]))
    check("reaching the limit is recorded for the health watch",
          (trips["schema"], len(trips["trips"]), trips["trips"][0]["max"]), (TRIPS_SCHEMA, 1, 3))

    # 3. Before the window ends nothing more goes; after it, ONE digest with all of them, once.
    check("a message sent while held is held in order, not sent",
          guarded_send(st, "D1", "later", sink.one("later"), sink.post, now=200.0)[0], "held")
    out = guarded_send(st, "D1", "after the window", sink.one("after the window"), sink.post,
                       now=111.0 + 600.0)
    digests = [s for s in sink.sent if "held back since" in s]
    check("after the window the held ones go as one digest, then the new message",
          (out[0], len(digests), sink.sent[-1]), ("sent", 1, "after the window"))
    check("the digest carries every held message whole, oldest first",
          [("extra %d" % i) in digests[0] for i in range(5)] + [("later" in digests[0])],
          [True] * 6)
    check("the digest counts them", digests[0].startswith("6 messages held back"), True)
    again = guarded_send(st, "D1", "one more", sink.one("one more"), sink.post, now=1500.0)
    check("a held message is never sent twice",
          (sum(1 for s in sink.sent if "extra 3" in s), again[0]), (1, "sent"))

    # 4. A repeat of one held message is counted, not kept twice.
    st2, s2 = fresh(max=1), Sink()
    guarded_send(st2, "D1", "a", s2.one("a"), s2.post, now=1.0)
    for _ in range(3):
        guarded_send(st2, "D1", "same", s2.one("same"), s2.post, now=2.0)
    held = json.load(open(_paths(st2["state_dir"])[0]))["chats"]["D1"]["held"]
    check("an identical held message is counted once with its times", (len(held), held[0]["n"]), (1, 3))

    # 5. A record that cannot be read holds: nothing sent, nothing lost.
    st3, s3 = fresh(), Sink()
    guarded_send(st3, "D1", "x", s3.one("x"), s3.post, now=1.0)
    with open(_paths(st3["state_dir"])[0], "w") as fh:
        fh.write("{not json")
    try:
        guarded_send(st3, "D1", "y", s3.one("y"), s3.post, now=2.0)
        raised = False
    except CannotCheck:
        raised = True
    check("an unreadable record holds rather than sends", (raised, s3.sent), (True, ["x"]))
    with open(_paths(st3["state_dir"])[0], "w") as fh:
        json.dump({"schema": "other/1", "chats": {}, "trips": []}, fh)
    try:
        guarded_send(st3, "D1", "y", s3.one("y"), s3.post, now=2.0)
        raised = False
    except CannotCheck:
        raised = True
    check("a record in another shape holds too", (raised, s3.sent), (True, ["x"]))

    # 6. A lock another job keeps holds, after a short wait.
    st4, s4 = fresh(), Sink()
    os.makedirs(st4["state_dir"], exist_ok=True)
    with open(_paths(st4["state_dir"])[1], "a") as other:
        fcntl.flock(other.fileno(), fcntl.LOCK_EX)
        try:
            with _locked(st4["state_dir"], seconds=0, sleep=lambda s: None):
                raised = False
        except CannotCheck:
            raised = True
    check("a lock held by another job is a hold, not a send", (raised, s4.sent), (True, []))

    # 7. A send that fails takes no count and keeps nothing.
    st5 = fresh(max=1)

    def boom():
        raise OSError("down")
    try:
        guarded_send(st5, "D1", "z", boom, lambda t: None, now=1.0)
    except OSError:
        pass
    s5 = Sink()
    check("a failed send does not use up the limit",
          guarded_send(st5, "D1", "z", s5.one("z"), s5.post, now=2.0)[0], "sent")

    # 8. flush_due: only after its time, only for its own sender, one digest.
    st6, s6 = fresh(max=1), Sink()
    guarded_send(st6, "D1", "a", s6.one("a"), s6.post, via="notifier", now=1.0)
    guarded_send(st6, "D1", "b", s6.one("b"), s6.post, via="notifier", now=2.0)
    check("before its time nothing is flushed", flush_due(st6, "notifier", lambda k: s6.post, now=100.0), (0, []))
    check("another sender's held ones are not flushed here",
          flush_due(st6, "bridge", lambda k: s6.post, now=1000.0), (0, []))
    n_before = len(s6.sent)
    check("at its time one digest goes", flush_due(st6, "notifier", lambda k: s6.post, now=1000.0), (1, []))
    check("…and only one", (len(s6.sent) - n_before, "held back since" in s6.sent[-1]), (1, True))
    check("…and a second flush finds nothing", flush_due(st6, "notifier", lambda k: s6.post, now=2000.0), (0, []))
    bad = fresh()
    with open(_paths(bad["state_dir"])[0], "w") as fh:
        fh.write("[")
    got = flush_due(bad, "notifier", lambda k: s6.post, now=1.0)
    check("a flush that cannot read its record says so", (got[0], len(got[1])), (0, 1))

    # 9. A failed digest stays held, whole.
    st7, s7 = fresh(max=1), Sink()
    guarded_send(st7, "D1", "a", s7.one("a"), s7.post, via="v", now=1.0)
    guarded_send(st7, "D1", "b", s7.one("b"), s7.post, via="v", now=2.0)
    got = flush_due(st7, "v", lambda k: boom, now=1000.0)
    held = json.load(open(_paths(st7["state_dir"])[0]))["chats"]["D1"]["held"]
    check("a digest that could not be sent leaves the held message held", (got[0], len(got[1]), len(held)), (0, 1, 1))

    # 10. More held than may be kept: counted, said.
    st8, s8 = fresh(max=1), Sink()
    guarded_send(st8, "D1", "a", s8.one("a"), s8.post, via="v", now=1.0)
    for i in range(MAX_HELD + 3):
        guarded_send(st8, "D1", "m%d" % i, s8.one("m"), s8.post, via="v", now=2.0)
    flush_due(st8, "v", lambda k: s8.post, now=1000.0)
    check("past the keeping limit the digest still counts every one",
          s8.sent[-1].startswith("%d messages held back" % (MAX_HELD + 3)), True)

    # 11. keep=False counts the hold but keeps no text.
    st9, s9 = fresh(max=1), Sink()
    guarded_send(st9, "D1", "a", s9.one("a"), s9.post, now=1.0)
    out = guarded_send(st9, "D1", "streamed", s9.one("s"), s9.post, keep=False, now=2.0)
    check("a hold with keep off keeps nothing", (out[0], json.load(open(_paths(st9["state_dir"])[0]))["chats"]["D1"]["held"]), ("held", []))

    # 12. One chat's count does not touch another's.
    st10, s10 = fresh(max=1), Sink()
    guarded_send(st10, "D1", "a", s10.one("a"), s10.post, now=1.0)
    check("each chat has its own count", guarded_send(st10, "D2", "b", s10.one("b"), s10.post, now=1.0)[0], "sent")

    # 13. THE BURST: 30 messages from two processes make at most the limit plus one notice.
    st11 = fresh(max=12)
    log = os.path.join(root, "burst.log")
    script = os.path.abspath(__file__)
    procs = [subprocess.Popen([sys.executable, script, "--worker", st11["state_dir"], log, "15", tag])
             for tag in ("jobA", "jobB")]
    codes = [p.wait(timeout=120) for p in procs]
    lines = open(log, encoding="utf-8").read().splitlines()
    notices = [x for x in lines if x.startswith("I have sent")]
    held = json.load(open(_paths(st11["state_dir"])[0]))["chats"]["D1"]["held"]
    check("a burst of 30 from two processes gives the limit plus one notice",
          (codes, len(lines), len(notices), sum(h["n"] for h in held)), ([0, 0], 13, 1, 18))

    # 14. Every sender of chat.postMessage is one this module knows.
    here = os.path.dirname(os.path.abspath(__file__))
    senders = sorted(f for f in os.listdir(here) if f.endswith(".py") and f != "pipeline_chat_cap.py"
                     and re.search(r'/chat\.postMessage"', open(os.path.join(here, f), encoding="utf-8").read()))
    check("only the two gated clients and the watcher's own page build a chat.postMessage address",
          senders, ["pipeline_bridge_transport.py", "pipeline_notify_local.py", "pipeline_watcher_page.py"])
    for name in ("pipeline_bridge_transport.py", "pipeline_notify_local.py"):
        src = open(os.path.join(here, name), encoding="utf-8").read()
        check("%s calls the check" % name, "guarded_send(" in src, True)

    # 14b. Every place a SlackClient is built hands it a loaded config (a hand-built one has no state
    #      folder, and a client with none has no limit).
    for name in ("pipeline_bridge.py", "pipeline_bridge_push.py", "pipeline_standup.py"):
        src = open(os.path.join(here, name), encoding="utf-8").read()
        args = set(re.findall(r"SlackClient\((\w+)", src)) - {"object"}
        check("%s builds its SlackClient from a loaded config" % name,
              args <= {"cfg", "bridge_cfg", "cfg_for"}, True)

    # 14c. An episode of holds that kept nothing does not leave the next trip unrecorded.
    st12, s12 = fresh(max=1), Sink()
    guarded_send(st12, "D1", "a", s12.one("a"), s12.post, now=1.0)
    guarded_send(st12, "D1", "stream", s12.one("s"), s12.post, keep=False, now=2.0)
    guarded_send(st12, "D1", "b", s12.one("b"), s12.post, now=700.0)      # a new window opens
    guarded_send(st12, "D1", "c", s12.one("c"), s12.post, now=701.0)      # a second trip
    trips = json.load(open(_paths(st12["state_dir"])[2]))["trips"]
    check("a second trip after a keep-off episode is recorded and noticed",
          (len(trips), len([x for x in s12.sent if x.startswith("I have sent")])), (2, 2))

    # 15. Settings: defaults, and a bad value is named.
    errs = []
    check("defaults", validate({}, errs), {"chat_cap_max": DEFAULT_MAX, "chat_cap_window_minutes": DEFAULT_WINDOW_MINUTES})
    validate({"chat_cap_max": 0, "chat_cap_window_minutes": "10"}, errs)
    check("a bad value is named, twice", len(errs), 2)
    # KIT-941: a trip says how many messages the episode held; a held token is its own type.
    st9 = fresh()
    sink9 = Sink()
    for i in range(3):
        guarded_send(st9, "D9", "m%d" % i, sink9.one("m%d" % i), sink9.post, now=100.0 + i)
    toks = [guarded_send(st9, "D9", "h%d" % i, sink9.one("h"), sink9.post, now=110.0 + i)[1]
            for i in range(4)]
    trips9, why9 = read_trips(_paths(st9["state_dir"])[2])
    check("a held message answers with a HeldToken, never a plain string",
          [type(t) is HeldToken for t in toks], [True] * 4)
    check("the trip counts every message the episode held, and there is one trip",
          (why9, len(trips9), trips9[0]["held"], trips9[0]["chat"]), (None, 1, 4, "D9"))
    guarded_send(st9, "D9", "t", lambda: None, sink9.post, via="x", keep=False, now=115.0)
    check("a hold that keeps nothing still counts",
          read_trips(_paths(st9["state_dir"])[2])[0][0]["held"], 5)
    guarded_send(st9, "D9", "after", sink9.one("after"), sink9.post, now=900.0)
    guarded_send(st9, "D9", "n1", sink9.one("n1"), sink9.post, now=901.0)
    for i in range(3):
        guarded_send(st9, "D9", "again", sink9.one("a"), sink9.post, now=902.0 + i)
    again = read_trips(_paths(st9["state_dir"])[2])[0]
    check("a second episode is a second trip with its own count",
          (len(again), again[0]["held"], again[1]["held"]), (2, 5, 3))
    bad = os.path.join(st9["state_dir"], "bad.json")
    check("a missing trip record is absent",
          read_trips(os.path.join(st9["state_dir"], "none.json")), (None, "absent"))
    with open(bad, "w") as fh:
        fh.write("{")
    check("an unreadable trip record is a problem, not none",
          read_trips(bad)[0:1] + (read_trips(bad)[1].startswith("cannot be read"),), (None, True))
    with open(bad, "w") as fh:
        json.dump({"schema": TRIPS_SCHEMA, "trips": [{"at": 1}]}, fh)
    check("a trip in no known shape is a problem", read_trips(bad)[0], None)

    check("a config with no state folder has no check", settings({}), None)
    check("a loaded config's settings", settings({"state_dir": "/x", "chat_cap_max": 5, "chat_cap_window_minutes": 2}),
          {"state_dir": "/x", "max": 5, "window": 120.0})

    if failures:
        print("FAIL: pipeline_chat_cap selftest (%d of %d cases failed)" % (len(failures), cases[0]))
        for line in failures:
            print("  - " + line[:600])
        return 1
    print("OK: pipeline_chat_cap selftest (%d cases)" % cases[0])
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return _selftest()
    if argv and argv[0] == "--worker":
        return _worker(argv[1:])
    print("usage: pipeline_chat_cap.py --selftest (the Slack client and the notifier import this module)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
