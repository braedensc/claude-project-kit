#!/usr/bin/env python3
"""The push-connection job (KIT-560, slice 2 of KIT-557): an owner's DM is answered at once.

    python3 scripts/pipeline_bridge_push.py run --config PATH
    python3 scripts/pipeline_bridge_push.py --selftest

The bridge's own pass runs once a minute, so a message could wait a minute before anything read it.
This job holds Slack's Socket Mode connection (an outbound WebSocket, no public address) and hands
each owner DM to the SAME code the pass uses, at once. It is OFF unless the config says `push_mode`.
The regular pass stays: it is the catch-up for a message sent while the Mac slept or the
connection was down. The installer, the manifest and the owner's Slack steps are the next slice.

WHAT IT DOES FOR ONE EVENT
  1. The reader thread ACKNOWLEDGES the envelope the moment it arrives, before anything is done
     with it, and passes it on. A second thread does the work, so a long turn never delays an ack.
  2. Only a message from a listed owner, in their own DM with this app, and by a person (no bot, no
     subtype) is taken. Everything else is acknowledged and ignored.
  3. Within seconds, and before it waits for anything, the thread under that message shows Slack's
     own status line (`assistant.threads.setStatus`). As the turn calls its tools the status
     changes to a FIXED phrase for each tool (PHRASES), never the model's words. If Slack refuses
     the status, the thread gets one short "On it." line instead, which the answer replaces.
  4. It takes the bridge's lock, waiting while a pass holds it, loads the bridge's state, and hands
     the message to `Pass.handle_once`: a command, a bare yes or no and the tech lead's turn all
     go the way a pass sends them, and code decides them exactly as there. The turn's reply goes in
     a thread under the owner's message; the bridge's own yes/no questions stay top-level, where a
     yes is looked for. A reply inside that thread continues its conversation; a new top-level
     message starts a new one.
  5. A message already `handled` (by a pass, or by an earlier delivery) is never answered again. The
     state is read under the lock, so a message both this job and a pass see is answered once.

THE CONNECTION. It opens with the app-level token (`apps.connections.open`), reads with the
standard-library client (pipeline_ws_client), and on a disconnect, a refresh or a drop opens again,
waiting 1, 2, 4 ... 60 seconds between failed tries and starting over after a `hello`. The
address Slack gives carries a one-time ticket, so it is never printed or kept, nor is a token.

THE HEARTBEAT (`bridge-push-heartbeat.json` in the bridge's state directory) says whether the
connection is up, when Slack was last heard, how many events were handled and how many times it
reconnected. A reader that cannot read it says UNKNOWN, never a pass.
"""
import json
import os
import queue
import sys
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_bridge as bridge  # noqa: E402
import pipeline_tech_lead_tools as tools  # noqa: E402
import pipeline_ws_client as ws  # noqa: E402
from pipeline_bridge_config import (EXIT_OK, EXIT_USAGE, key_of, lock_path,  # noqa: E402
                                    iso, _awake_clock)

BridgeError = bridge.BridgeError

HEARTBEAT_SCHEMA = "pipeline-bridge-push-heartbeat/1"
HEARTBEAT_FILE = "bridge-push-heartbeat.json"
BACKOFF = (1, 2, 4, 8, 16, 32, 60)          # seconds between failed tries to connect
READ_SECONDS = 1.0                          # how long one read waits before the job looks around
HEARTBEAT_EVERY = 30.0                      # seconds between beats while nothing changes
LOCK_WAIT_SECONDS = 600                     # how long one message waits for a pass to finish
SEEN_MAX = 500                              # envelopes and messages remembered against a redelivery
TOOL_POLL_SECONDS = 0.4                     # how often the tool note is looked at while a turn runs
WORKING = "working on it"
ON_IT = "On it."
COULD_NOT_YET = "I hit a problem with that and will try again in a minute."
# The status line each tool shows, by name (KIT-560): fixed, never the model's own words. A test
# holds this table to the tool server's own, so a new tool without a phrase fails it.
PHRASES = {
    "where_things_stand": "checking the plans", "ticket": "reading a ticket",
    "search_tickets": "searching the tickets", "guide": "reading a guide",
    "pipeline_file": "reading the queue's files", "reach": "checking what the pipeline can land",
    "plan": "lining up a plan question", "replan": "lining up a re-plan question",
    "approve": "lining up an approval question", "pause": "lining up a pause question",
    "resume": "lining up a resume question", "cancel": "lining up a cancel question",
    "retry": "lining up a retry question", "start": "lining up a start question",
    "answer": "lining up an answer", "answer_plan": "lining up an answer to a plan's question",
}


def status_text(phrase):
    """What Slack is asked to show: it prints the app's name, then this."""
    return "is %s…" % phrase


# ══════════════════════════════════════════════════════════════════════════════════════
# One event: is it ours to answer?
# ══════════════════════════════════════════════════════════════════════════════════════
def owner_message(envelope, cfg, dms):
    """(channel, msg) for a message an owner wrote in their own DM with this app, else None. `dms`
    is the bridge's state of {member: DM channel}. Nothing else is ever answered: an event of
    another kind, a channel that is not a member's DM, a bot, an edit or a delete, a message with
    no text, and any user not listed."""
    if not isinstance(envelope, dict) or envelope.get("type") != "events_api":
        return None
    payload = envelope.get("payload")
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict) or event.get("type") != "message":
        return None
    if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id") \
            or event.get("app_id"):
        return None
    user, channel, ts, text = (event.get(k) for k in ("user", "channel", "ts", "text"))
    if not all(isinstance(v, str) and v for v in (user, channel, ts, text)):
        return None
    if user not in cfg["dm_user_ids"] or dms.get(user) != channel:
        return None
    msg = {"ts": ts, "text": text, "user": user, "type": "message"}
    thread = event.get("thread_ts")
    if isinstance(thread, str) and thread and thread != ts:
        msg["thread_ts"] = thread
    return channel, msg


# ══════════════════════════════════════════════════════════════════════════════════════
# The bridge's lock, shared with the pass
# ══════════════════════════════════════════════════════════════════════════════════════
class LockBusy(Exception):
    pass


@contextmanager
def bridge_lock(cfg, wait, sleep=time.sleep, clock=time.monotonic):
    """The bridge's lock file, held for one message: the pass and this job never run at once. A
    pass holding it is waited for, up to `wait` seconds, then LockBusy."""
    import fcntl
    os.makedirs(cfg["state_dir"], exist_ok=True)
    handle = open(lock_path(cfg), "a")
    until = clock() + wait
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if clock() >= until:
                    raise LockBusy()
                sleep(0.2)
        yield
    finally:
        handle.close()


class RecordingSlack(object):
    """The Slack client the Pass is given for one message: every call goes through, and each post
    is remembered (channel, thread, ts), so the job knows whether the thread was answered."""

    def __init__(self, inner):
        self.inner, self.posts = inner, []

    def post(self, channel, thread_ts, text):
        ts = self.inner.post(channel, thread_ts, text)
        self.posts.append((channel, thread_ts, ts))
        return ts

    def __getattr__(self, name):
        return getattr(self.inner, name)


# ══════════════════════════════════════════════════════════════════════════════════════
# The job
# ══════════════════════════════════════════════════════════════════════════════════════
class PushJob(object):
    def __init__(self, cfg, slack, tracker, app_token, connect=None, clock=time.time,
                 sleep=None, say=None, pass_factory=None, lock_wait=LOCK_WAIT_SECONDS,
                 tools_cfg=None):
        self.cfg, self.slack, self.tracker, self.app_token = cfg, slack, tracker, app_token
        self.connect = connect or (lambda url: ws.connect(url, (".slack.com",), timeout=10))
        self.stop = threading.Event()
        # A pause that a stop cuts short, unless the caller hands in its own.
        self.clock, self.sleep = clock, sleep or self.stop.wait
        self.say = say or (lambda line: sys.stdout.write(line + "\n"))
        self.secrets = [v for v in (app_token,) if v]
        self.pass_factory = pass_factory or self.make_pass
        self.lock_wait = lock_wait
        self.tools_cfg = tools_cfg                  # the tool server's config, for the tool note
        self.work = queue.Queue()
        self.seen = OrderedDict()
        self.lock = threading.Lock()                # the heartbeat's numbers
        self.connected, self.last_heard, self.handled, self.connects = False, None, 0, 0
        self.error, self.beat_at = None, 0.0

    # -- saying and the heartbeat ---------------------------------------------------------
    def log(self, line):
        for secret in self.secrets:
            line = line.replace(secret, "[token]")
        self.say(line)

    def beat(self, force=False):
        """The heartbeat, whole and atomic; best effort. A beat that cannot be written is said and
        never stops the job."""
        now = self.clock()
        if not force and now - self.beat_at < HEARTBEAT_EVERY:
            return
        self.beat_at = now
        with self.lock:
            doc = {"schema": HEARTBEAT_SCHEMA, "at": iso(now), "awake_clock": _awake_clock(),
                   "connected": self.connected,
                   "last_heard_at": iso(self.last_heard) if self.last_heard else None,
                   "handled": self.handled, "reconnects": max(0, self.connects - 1),
                   "error": self.error}
        path = os.path.join(self.cfg["state_dir"], HEARTBEAT_FILE)
        try:
            os.makedirs(self.cfg["state_dir"], exist_ok=True)
            bridge.notify._atomic_write_json(path, doc)
        except OSError as exc:
            self.log("  could not write the heartbeat (%s)" % type(exc).__name__)

    def set(self, **fields):
        with self.lock:
            for k, v in fields.items():
                setattr(self, k, v)
        self.beat(force=True)

    # -- the connection -----------------------------------------------------------------------
    def run(self):
        """Connect, read, and connect again, until stopped. Returns when `stop` is set."""
        worker = threading.Thread(target=self.worker, daemon=True)
        worker.start()
        failures = 0
        try:
            while not self.stop.is_set():
                try:
                    url = self.slack.open_socket_url(self.app_token)
                    sock = self.connect(url)
                except (BridgeError, ws.WSError) as exc:
                    # Said in fixed words: neither error holds an address or a token.
                    self.set(connected=False, error="could not connect: %s" % str(exc)[:120])
                    wait = BACKOFF[min(failures, len(BACKOFF) - 1)]
                    failures += 1
                    self.log("  could not open the connection (%s); trying again in %ds"
                             % (str(exc)[:120], wait))
                    self.wait(wait)
                    continue
                self.connects += 1
                got_hello = self.read(sock)
                if got_hello:
                    failures = 0
                self.set(connected=False)
                if not got_hello:
                    wait = BACKOFF[min(failures, len(BACKOFF) - 1)]
                    failures += 1
                    self.wait(wait)
        finally:
            self.stop.set()
            self.work.put(None)
            worker.join(5)
            self.set(connected=False)

    def wait(self, seconds):
        """A pause between tries; a stop cuts the default one short."""
        if not self.stop.is_set():
            self.sleep(seconds)

    def read(self, sock):
        """Read one connection until it ends. True when it said hello (it was a real connection)."""
        hello = False
        try:
            while not self.stop.is_set():
                try:
                    got = sock.recv(timeout=READ_SECONDS)
                except ws.WSError as exc:
                    self.set(error="the connection ended: %s" % str(exc)[:120])
                    return hello
                self.beat()
                if got is None:
                    continue
                kind, data = got
                if kind == "close":
                    self.set(error="Slack closed the connection")
                    return hello
                if kind != "text":
                    continue
                with self.lock:
                    self.last_heard = self.clock()
                try:
                    envelope = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(envelope, dict):
                    continue
                if envelope.get("type") == "hello":
                    hello = True
                    self.set(connected=True, error=None)
                    continue
                if envelope.get("type") == "disconnect":
                    self.log("  Slack asked to reconnect (%s)"
                             % str(envelope.get("reason"))[:40])
                    self.set(error="Slack asked to reconnect")
                    return hello
                # Acknowledged at once, before anything is done with it.
                eid = envelope.get("envelope_id")
                if isinstance(eid, str) and eid:
                    try:
                        sock.send_text(json.dumps({"envelope_id": eid}))
                    except ws.WSError as exc:
                        self.set(error="could not acknowledge: %s" % str(exc)[:120])
                        return hello
                    if self.remember("e:" + eid):
                        continue
                self.work.put(envelope)
        finally:
            sock.close()
        return hello

    def remember(self, key):
        """True when `key` was seen (a redelivery); else it is remembered, the oldest forgotten."""
        if key in self.seen:
            return True
        self.seen[key] = True
        while len(self.seen) > SEEN_MAX:
            self.seen.popitem(last=False)
        return False

    # -- the work ----------------------------------------------------------------------------
    def worker(self):
        while True:
            envelope = self.work.get()
            if envelope is None:
                return
            try:
                self.handle(envelope)
            except Exception as exc:                                   # noqa: BLE001
                self.log("  an event could not be handled (%s)" % type(exc).__name__)

    def handle(self, envelope):
        """One envelope. Nothing is answered that the bridge's state already holds as handled."""
        try:
            state, _ = bridge.load_state(self.cfg)
        except BridgeError as exc:
            self.log("  an event waits for the next pass: %s" % str(exc)[:120])
            return
        got = owner_message(envelope, self.cfg, state.get("dms") or {})
        if got is None:
            return     # not ours; and before the bridge's first pass no DM channel is known at all
        channel, msg = got
        k = key_of(channel, msg["ts"])
        if k in state["handled"] or self.remember("m:" + k):
            return
        self.answer(channel, msg)

    def answer(self, channel, msg):
        """The reply to one owner message: status first, then the bridge's own handling under its
        lock, in a thread under the message."""
        root = msg.get("thread_ts") or msg["ts"]
        started = self.clock()
        shown, on_it = self.show_status(channel, root, status_text(WORKING)), None
        if not shown:
            on_it = self.on_it_line(channel, root)
        feed = threading.Event()
        if shown:
            threading.Thread(target=self.follow_tools, args=(channel, root, started, feed),
                             daemon=True).start()
        posted, failed = [], False
        try:
            try:
                with bridge_lock(self.cfg, self.lock_wait, sleep=self.sleep):
                    posted = self.hand_over(channel, msg, root)
            except LockBusy:
                failed = True
                self.log("  a pass held the lock for %ds, so message %s waits for the next pass"
                         % (self.lock_wait, msg["ts"]))
            except BridgeError as exc:
                failed = True
                self.log("  message %s could not be handled yet: %s" % (msg["ts"], str(exc)[:160]))
        finally:
            feed.set()
        with self.lock:
            self.handled += 1 if not failed else 0
        if failed:
            self.say_plain(channel, root, COULD_NOT_YET)
        if shown:
            self.show_status(channel, root, "")      # it clears itself on a reply; this covers none
        if on_it:
            try:
                self.slack.delete_message(channel, on_it)
            except BridgeError as exc:
                self.log("  could not remove the 'on it' line (%s)" % str(exc)[:80])
        self.beat(force=True)
        return posted

    def make_pass(self, slack, state):
        """The bridge's own Pass, set up for one message the way a pass sets itself up (run)."""
        p = bridge.Pass(self.cfg, slack, self.tracker, state, False, False, self.clock(),
                        sys.stdout)
        return p

    def hand_over(self, channel, msg, root):
        """Under the lock: the state read fresh, the message checked once more, then the Pass's own
        handling. Returns the posts it made."""
        state, first = bridge.load_state(self.cfg)
        k = key_of(channel, msg["ts"])
        if first or k in state["handled"]:
            return []                                  # a pass answered it while this waited
        recorder = RecordingSlack(self.slack)
        p = self.pass_factory(recorder, state)
        p.standup_on = bridge.standup.is_on(cfg=self.cfg)
        p.me = recorder.auth_test()
        if p.me.get("user_id") != self.cfg["bot_user_id"]:
            raise BridgeError("the Slack token answers to another bot than bot_user_id")
        if self.tracker.viewer_id() != self.cfg["owner_user_id"]:
            raise BridgeError("the tracker key is not the owner's")
        p.dm_channels = {channel}
        if self.cfg["dm_user_ids"]:
            p.dm_posts = bridge.load_dm_posts(self.cfg)
        # The history and threads of the DM, as a pass reads them, so a yes finds its question.
        gathered = {m.get("ts"): m for m in p.gather(channel)}
        live = dict(gathered.get(msg["ts"]) or msg)
        p.handle_once(channel, live)
        p.save()
        return recorder.posts

    # -- Slack's lines under the thread ------------------------------------------------------------
    def show_status(self, channel, root, text):
        try:
            self.slack.set_status(channel, root, text)
            return True
        except BridgeError as exc:
            if text:
                self.log("  Slack would not show a status line (%s)" % str(exc)[:80])
            return False

    def on_it_line(self, channel, root):
        try:
            return self.slack.post(channel, root, ON_IT)
        except BridgeError:
            return None

    def say_plain(self, channel, root, text):
        try:
            self.slack.post(channel, root, text)
        except BridgeError as exc:
            self.log("  could not post in the thread (%s)" % str(exc)[:80])

    def follow_tools(self, channel, root, started, done):
        """While a turn runs: the status shows a fixed phrase for the tool in use. The note the
        tool server writes holds a tool's name and a time; only a name of PHRASES, written after
        this message arrived, is used."""
        path = None
        if self.tools_cfg and self.tools_cfg.get("outbox_dir"):
            path = os.path.join(os.path.dirname(os.path.normpath(self.tools_cfg["outbox_dir"])),
                                tools.TOOL_NOW)
        last = WORKING
        while path and not done.wait(TOOL_POLL_SECONDS):
            try:
                with open(path, encoding="utf-8") as fh:
                    note = json.load(fh)
                name, at = note.get("tool"), note.get("at")
            except (OSError, ValueError, AttributeError):
                continue
            if not (isinstance(at, (int, float)) and at >= started):
                continue
            phrase = PHRASES.get(name, WORKING) if isinstance(name, str) else WORKING
            if phrase != last:
                last = phrase
                self.show_status(channel, root, status_text(phrase))


# ══════════════════════════════════════════════════════════════════════════════════════
# The command line
# ══════════════════════════════════════════════════════════════════════════════════════
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        from scratch_run import contained
        return contained(_selftest, "bridge-push-selftest-run-")
    if len(argv) != 3 or argv[0] != "run" or argv[1] != "--config":
        sys.stderr.write("usage: pipeline_bridge_push.py run --config PATH | --selftest\n")
        return EXIT_USAGE
    try:
        cfg = bridge.load_config(argv[2])
        if cfg["push_mode"] is not True:
            sys.stderr.write("push_mode is off in the config, so there is nothing to run\n")
            return EXIT_USAGE
        slack = bridge.SlackClient(cfg, bridge.credential(cfg, "slack_token_env"))
        tracker = bridge.TrackerClient(bridge.credential(cfg, "linear_key_env"),
                                       cfg["linear_key_env"])
        app_token = bridge.credential(cfg, "push_token_env")
    except BridgeError as exc:
        sys.stderr.write("FAIL: %s\n" % exc)
        return exc.code
    tools_cfg = None
    if cfg.get("turn_tools_config"):
        tools_cfg, errors = tools.load_config(os.path.expanduser(cfg["turn_tools_config"]))
        if errors:
            tools_cfg = None            # no tool phrases; the job still answers
    job = PushJob(cfg, slack, tracker, app_token, tools_cfg=tools_cfg)
    try:
        job.run()
    except KeyboardInterrupt:
        job.stop.set()
    return EXIT_OK


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline: a fake Slack, scripted connections, and one real WebSocket over loopback
# ══════════════════════════════════════════════════════════════════════════════════════
def _selftest(tmp=None):
    return selftest_body()


def selftest_body():
    import base64
    import hashlib
    import io
    import socket
    import tempfile

    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    ALICE, BOB, OWNER, ME, CHAN, DM = (bridge.ALICE, bridge.BOB, bridge.OWNER, bridge.ME,
                                       bridge.CHAN, bridge.DM)
    NOW = 1760000000.0
    ROOTDIR = tempfile.mkdtemp(prefix="push-")

    # -- a world: the bridge's state with a baselined DM, the fakes, a tool server config ---------
    class PushSlack(bridge.FakeSlack):
        """The bridge's fake Slack, with the three calls the job adds, recorded in order."""

        def __init__(self, now, status_ok=True):
            bridge.FakeSlack.__init__(self, now)
            self.calls, self.status_ok, self.deleted = [], status_ok, []
            self.posted_at = {}

        def open_socket_url(self, app_token):
            self.calls.append(("open", app_token))
            return "wss://wss-primary.slack.com/link/?ticket=SECRETTICKET"

        def set_status(self, channel, thread_ts, status):
            self.calls.append(("status", channel, thread_ts, status))
            if not self.status_ok:
                raise BridgeError("Slack assistant.threads.setStatus refused: method_deprecated")

        def delete_message(self, channel, ts):
            self.calls.append(("delete", channel, ts))
            self.deleted.append(ts)

        def post(self, channel, thread_ts, text):
            ts = bridge.FakeSlack.post(self, channel, thread_ts, text)
            self.calls.append(("post", channel, thread_ts, text))
            return ts

    counter = [0]

    def world(**over):
        counter[0] += 1
        root = os.path.join(ROOTDIR, "w%d" % counter[0])
        os.makedirs(os.path.join(root, "state"))
        key = os.path.join(root, "key")
        with open(key, "w") as fh:
            fh.write("lin_api_" + "k" * 30 + "\n")
        os.chmod(key, 0o600)
        ob = os.path.join(root, "tl", "outbox")
        os.makedirs(ob, mode=0o700)
        conf = os.path.join(root, "tl", "tools.json")
        with open(conf, "w") as fh:
            json.dump({"team_keys": ["PROD"], "linear_key_file": key, "outbox_dir": ob}, fh)
        raw = dict(bridge.EXAMPLE_CONFIG, channel_ids=[CHAN], owner_user_id=OWNER, bot_user_id=ME,
                   state_dir=os.path.join(root, "state"), dm_user_ids=[ALICE],
                   ask_tech_lead=True, chat_bot_user_id=bridge.CHATBOT, back_room_channel_id=CHAN,
                   tech_lead_turn=True, turn_bin="/opt/bin/claude", turn_tools_config=conf,
                   push_mode=True, act=True, agent_user_name="Agent")
        raw.update(over)
        cfg = bridge.validate_config(raw)
        state = bridge.new_state()
        state["dms"][ALICE] = DM
        bridge.save_state(cfg, state)
        tcfg, errs = tools.load_config(conf)
        slack = PushSlack(NOW)
        return cfg, slack, bridge.FakeTracker(), tcfg, ob

    class FakeTurn(object):
        def __init__(self, *answers, **kw):
            self.answers, self.calls, self.during = list(answers), [], kw.get("during")

        def __call__(self, cfg, text, session, now, **kw):
            self.calls.append({"text": text, "session": session})
            if self.during:
                self.during()
            a = self.answers.pop(0) if self.answers else {}
            return dict({"text": None, "why": "", "session_id": None, "resumed": False,
                         "cost": None, "turns": None}, **a)

    SID = "11111111-1111-4111-8111-111111111111"
    SID2 = "22222222-2222-4222-8222-222222222222"

    def factory(turn):
        def make(recorder, state):
            p = bridge.Pass(CFG[0], recorder, TRACKER[0], state, False, False, NOW, io.StringIO())
            p.run_turn = turn
            return p
        return make
    CFG, TRACKER = [None], [None]

    def job_for(cfg, slack, tracker, tcfg, turn, **kw):
        CFG[0], TRACKER[0] = cfg, tracker
        lines = []
        job = PushJob(cfg, slack, tracker, "xapp-SECRETAPPTOKEN", clock=lambda: NOW,
                      sleep=lambda s: None, say=lines.append, pass_factory=factory(turn),
                      lock_wait=kw.pop("lock_wait", 2), tools_cfg=tcfg, **kw)
        job.lines = lines
        return job

    def event(ts, text="what about PROD-6?", user=ALICE, channel=DM, thread=None, eid=None,
              **extra):
        ev = dict({"type": "message", "channel_type": "im", "user": user, "channel": channel,
                   "ts": ts, "text": text}, **extra)
        if thread:
            ev["thread_ts"] = thread
        return {"type": "events_api", "envelope_id": eid or "env-" + ts,
                "payload": {"event": ev, "event_id": "Ev" + ts}}

    # -- 1. the filter: only an owner's own DM message is ours --------------------------------------
    dms = {ALICE: DM}
    cfg0 = bridge.validate_config(dict(bridge.EXAMPLE_CONFIG, channel_ids=[CHAN], owner_user_id=OWNER,
                                       bot_user_id=ME, state_dir=os.path.join(ROOTDIR, "f"),
                                       dm_user_ids=[ALICE]))
    mine = owner_message(event("1.1"), cfg0, dms)
    check("filter: an owner's DM message is taken, a top-level one with no thread",
          mine, (DM, {"ts": "1.1", "text": "what about PROD-6?", "user": ALICE, "type": "message"}))
    check("filter: a reply in a thread carries its thread, a message that opens one does not",
          (owner_message(event("1.2", thread="1.1"), cfg0, dms)[1].get("thread_ts"),
           "thread_ts" in owner_message(event("1.1", thread="1.1"), cfg0, dms)[1]), ("1.1", False))
    ignored = [owner_message(e, cfg0, dms) for e in (
        event("2.1", user=BOB), event("2.2", channel="D0OTHER001"), event("2.3", subtype="message_changed"),
        event("2.4", bot_id="B1"), event("2.5", app_id="A1"), event("2.6", text=""),
        event("2.7", channel_type="channel"),
        dict(event("2.8"), type="slash_commands"), {"type": "hello"}, "x", None,
        {"type": "events_api", "payload": {"event": {"type": "reaction_added"}}},
        {"type": "events_api", "payload": []})]
    check("filter: another person, another channel, an edit, a bot, no text, a channel, another "
          "kind of event and junk are all ignored", ignored, [None] * 13)
    check("filter: the owner of a DM the bridge has not opened is ignored",
          owner_message(event("3.1"), cfg0, {}), None)

    # -- 2. the phrases: fixed, and every tool has one -------------------------------------------------
    check("phrases: every tool of the tool server has one, and none more",
          sorted(PHRASES), sorted(tools.TOOLS))
    check("phrases: each is plain words, short, with no mention or markup",
          [p for p in PHRASES.values() if len(p) > 60 or any(c in p for c in "<>@`*_#")], [])
    check("phrases: the status line is the phrase with an ellipsis",
          status_text("checking the plans"), "is checking the plans…")

    # -- 3. one message, end to end through the bridge's own Pass ------------------------------------------
    cfg, slack, tracker, tcfg, ob = world()
    turn = FakeTurn({"text": "PROD-6 is ready.", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.handle(event("1760000001.000100"))
    posts = [c for c in slack.calls if c[0] == "post"]
    check("reply: the status shows first, then the reply goes in a thread under the message",
          ([c[0] for c in slack.calls][:2], slack.calls[0][2:], posts[-1][2:]),
          (["status", "post"], ("1760000001.000100", "is working on it…"),
           ("1760000001.000100", "PROD-6 is ready.")))
    check("reply: the status is cleared at the end, and no 'on it' line was made",
          (slack.calls[-1], [c for c in posts if c[3] == ON_IT]),
          (("status", DM, "1760000001.000100", ""), []))
    st = json.load(open(bridge.state_path(cfg)))
    check("reply: the message is recorded as handled, so a pass leaves it, and the turn's "
          "session is the thread's",
          ("%s:1760000001.000100" % DM in st["handled"],
           sorted(st["turn_sessions"]), turn.calls[0]["session"]),
          (True, ["%s:1760000001.000100" % DM], None))
    # the owner replies inside that thread: the same conversation. A new top-level message: a new one.
    turn.answers += [{"text": "Still ready.", "session_id": SID, "resumed": True},
                     {"text": "A new topic.", "session_id": SID2}]
    job.handle(event("1760000002.000100", text="and the plan?", thread="1760000001.000100"))
    job.handle(event("1760000003.000100", text="something else"))
    check("thread: a reply inside the thread resumes its session; a new message starts another",
          ([c["session"] and c["session"]["id"] for c in turn.calls],
           sorted(json.load(open(bridge.state_path(cfg)))["turn_sessions"])),
          ([None, SID, None], sorted(["%s:1760000001.000100" % DM, "%s:1760000003.000100" % DM])))
    check("thread: every reply is under its own conversation's root",
          [c[2] for c in slack.calls if c[0] == "post"],
          ["1760000001.000100", "1760000001.000100", "1760000003.000100"])

    # -- 4. a message the bridge already holds is never answered again -------------------------------------
    n_before = len(slack.calls)
    job.handle(event("1760000001.000100"))                      # the same message, redelivered
    job.handle(event("1760000009.000100", eid="env-new-9"))
    job.handle(event("1760000009.000100", eid="env-new-9-retry"))   # Slack's retry: a new envelope
    check("redelivery: the same message again, by envelope or by another envelope, is answered "
          "once and the handled one not at all",
          (len(turn.calls), len(slack.calls) - n_before > 0), (4, True))
    cfg, slack, tracker, tcfg, ob = world()
    state = json.load(open(bridge.state_path(cfg)))
    state["handled"]["%s:1760000005.000100" % DM] = {"at": NOW, "what": "answered by a turn"}
    json.dump(state, open(bridge.state_path(cfg), "w"))
    turn = FakeTurn({"text": "x", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.handle(event("1760000005.000100"))
    check("redelivery: a message a pass already handled gets no status, no reply, no turn",
          (slack.calls, turn.calls), ([], []))

    # -- 5. a status Slack refuses: one 'on it' line, replaced by the answer -----------------------------------
    cfg, slack, tracker, tcfg, ob = world()
    slack.status_ok = False
    turn = FakeTurn({"text": "Here it is.", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.handle(event("1760000011.000100"))
    on_it_ts = [c for c in slack.calls if c[0] == "post" and c[3] == ON_IT]
    check("fallback: Slack refuses the status, so the thread gets one 'on it' line, then the "
          "answer, and the line goes",
          ([c[0] for c in slack.calls], len(on_it_ts), on_it_ts[0][2], slack.deleted),
          (["status", "post", "post", "delete"], 1, "1760000011.000100", [slack.deleted[0]]))
    check("fallback: the deleted line is the 'on it' one, not the answer",
          [x for x in slack.messages if x.get("ts") == slack.deleted[0]][0]["text"], ON_IT)
    cfg, slack, tracker, tcfg, ob = world()
    slack.status_ok = False
    turn = FakeTurn({"text": None, "why": "the turn timed out"})
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.handle(event("1760000012.000100"))
    said = [c[3] for c in slack.calls if c[0] == "post"]
    check("fallback: a turn that fails says so in plain words in the thread, and the 'on it' line goes",
          (said[0], said[1].startswith(bridge.FORWARD_NO_ANSWER_SAYS), len(slack.deleted)),
          (ON_IT, True, 1))

    # -- 6. a fixed phrase for each tool the turn calls ------------------------------------------------------------
    cfg, slack, tracker, tcfg, ob = world()
    note = os.path.join(os.path.dirname(ob), tools.TOOL_NOW)

    def writes_notes():
        for name in ("where_things_stand", "ticket", "no_such_tool"):
            with open(note, "w") as fh:
                json.dump({"tool": name, "at": time.time() + 5}, fh)
            time.sleep(0.9)

    turn = FakeTurn({"text": "Done.", "session_id": SID}, during=writes_notes)
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.clock = time.time
    job.handle(event("1760000013.000100"))
    statuses = [c[3] for c in slack.calls if c[0] == "status"]
    check("tools: the status follows the tool in use, with a fixed phrase, and a name that is no "
          "tool's falls back to the working phrase, never to the note's words",
          statuses[:4], ["is working on it…", "is checking the plans…", "is reading a ticket…",
                         "is working on it…"])
    with open(note, "w") as fh:
        json.dump({"tool": "guide", "at": 1.0}, fh)
    old_slack = PushSlack(NOW)
    job_for(cfg, old_slack, tracker, tcfg, FakeTurn({"text": "x", "session_id": SID})).handle(
        event("1760000014.000100"))
    check("tools: a note from before the message arrived is ignored",
          [c[3] for c in old_slack.calls if c[0] == "status"], ["is working on it…", ""])

    # -- 6b. more refusals: no state yet, a message handled while it waited, a tracker key that is not
    #        the owner's, and a stale tool note with a turn slow enough to be polled -----------------
    cfg, slack, tracker, tcfg, ob = world()
    os.remove(bridge.state_path(cfg))
    turn = FakeTurn({"text": "never", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.handle(event("1760000051.000100"))
    check("first: with no state yet the pass baselines first: no status, no reply, no turn",
          (slack.calls, turn.calls), ([], []))
    cfg, slack, tracker, tcfg, ob = world()
    holder = open(lock_path(cfg), "a")
    fcntl_mod = __import__("fcntl")
    fcntl_mod.flock(holder.fileno(), fcntl_mod.LOCK_EX | fcntl_mod.LOCK_NB)

    def pass_got_there_first():
        st_ = json.load(open(bridge.state_path(cfg)))
        st_["handled"]["%s:1760000052.000100" % DM] = {"at": NOW, "what": "answered by a turn"}
        json.dump(st_, open(bridge.state_path(cfg), "w"))
        holder.close()
    turn = FakeTurn({"text": "never", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn, lock_wait=5)
    job.sleep = time.sleep
    threading.Timer(0.5, pass_got_there_first).start()
    job.handle(event("1760000052.000100"))
    check("lock: a message a pass handled while this one waited is not answered twice (the state "
          "is read again under the lock); its status is cleared",
          (turn.calls, [c[0] for c in slack.calls]), ([], ["status", "status"]))
    cfg, slack, tracker, tcfg, ob = world()
    turn = FakeTurn({"text": "never", "session_id": SID})
    job = job_for(cfg, slack, bridge.FakeTracker(viewer="someone-else"), tcfg, turn)
    job.handle(event("1760000053.000100"))
    check("owner: a tracker key that is not the owner's answers nothing and says it will try again",
          (turn.calls, [c[3] for c in slack.calls if c[0] == "post"]), ([], [COULD_NOT_YET]))
    cfg, slack, tracker, tcfg, ob = world()
    note = os.path.join(os.path.dirname(ob), tools.TOOL_NOW)
    with open(note, "w") as fh:
        json.dump({"tool": "guide", "at": 1.0}, fh)
    turn = FakeTurn({"text": "x", "session_id": SID}, during=lambda: time.sleep(1.0))
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.handle(event("1760000054.000100"))
    check("tools: a note from before the message arrived is ignored even while the turn is polled",
          [c[3] for c in slack.calls if c[0] == "status"], ["is working on it…", ""])
    cfg, slack, tracker, tcfg, ob = world()
    turn = FakeTurn({"text": "Asked.", "session_id": SID},
                    during=lambda: tools.record({"outbox_dir": ob}, "plan", "PROD-6"))
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.handle(event("1760000055.000100"))
    asked = [(c[2], c[3] == "Asked." or c[3].startswith("*Plan this?* PROD-6"))
             for c in slack.calls if c[0] == "post"]
    check("thread: the turn's reply is in the thread, and the bridge's own question stays in the "
          "DM itself, where a yes is looked for",
          asked, [("1760000055.000100", True), (None, True)])

    # -- 7. the lock is shared with the pass --------------------------------------------------------------------------------
    cfg, slack, tracker, tcfg, ob = world()
    import fcntl
    holder = open(lock_path(cfg), "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    release = threading.Timer(0.5, holder.close)
    turn = FakeTurn({"text": "After the pass.", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn, lock_wait=5)
    job.sleep = time.sleep
    release.start()
    t0 = time.monotonic()
    job.handle(event("1760000015.000100"))
    release.join()
    check("lock: a message waits for a pass to finish, shows its status at once, and is answered after",
          (time.monotonic() - t0 >= 0.4, slack.calls[0][0], [c[3] for c in slack.calls
                                                           if c[0] == "post"]),
          (True, "status", ["After the pass."]))
    cfg, slack, tracker, tcfg, ob = world()
    holder = open(lock_path(cfg), "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    turn = FakeTurn({"text": "never", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn, lock_wait=0)
    job.handle(event("1760000016.000100"))
    st = json.load(open(bridge.state_path(cfg)))
    check("lock: a lock held past the wait says so in the thread and leaves the message for the "
          "pass; no turn ran",
          ([c[3] for c in slack.calls if c[0] == "post"], turn.calls,
           "%s:1760000016.000100" % DM in st["handled"]), ([COULD_NOT_YET], [], False))
    holder.close()
    # the pass itself holds off while the job holds the lock (KIT-270's rule: no heartbeat)
    cfg, slack, tracker, tcfg, ob = world()
    seen_pass = []

    def during():
        out = io.StringIO()
        seen_pass.append(bridge.run_command(cfg, False, 30, slack=bridge.FakeSlack(NOW),
                                            tracker=bridge.FakeTracker(), out=out, now=NOW))
    turn = FakeTurn({"text": "ok", "session_id": SID}, during=during)
    job = job_for(cfg, slack, tracker, tcfg, turn)
    job.handle(event("1760000017.000100"))
    check("lock: a pass that starts while the job answers does nothing (exit 3), and writes no heartbeat",
          (seen_pass, os.path.exists(bridge.heartbeat_path(cfg))), ([bridge.EXIT_DECLINED], False))

    # -- 8. the connection: acks at once, reconnects with a growing wait, hello resets it ------------------------------------
    class FakeWS(object):
        def __init__(self, frames):
            self.frames, self.sent, self.closed = list(frames), [], False

        def recv(self, timeout=None):
            if not self.frames:
                raise ws.WSError("the connection closed")
            f = self.frames.pop(0)
            if isinstance(f, Exception):
                raise f
            return f

        def send_text(self, text):
            self.sent.append(json.loads(text))

        def close(self):
            self.closed = True

    def text(doc):
        return ("text", json.dumps(doc))

    cfg, slack, tracker, tcfg, ob = world()
    waits = []
    s1 = FakeWS([text({"type": "hello"}), text(event("1760000021.000100", eid="E1")),
                 text({"type": "disconnect", "reason": "refresh_requested"})])
    s2 = FakeWS([text({"type": "hello"}), ("close", (1001, "bye"))])
    s3 = FakeWS([text({"type": "hello"}), text(dict(event("1760000022.000100", eid="E2")))])
    socks = [s1, s2, s3]
    attempts = []

    def connect(url):
        attempts.append(url)
        if len(attempts) in (1, 2, 4):
            raise ws.WSError("could not reach wss-primary.slack.com")
        return socks.pop(0)

    turn = FakeTurn({"text": "Hi.", "session_id": SID}, {"text": "Hi again.", "session_id": SID2})
    job = job_for(cfg, slack, tracker, tcfg, turn, connect=connect)
    job.sleep = lambda s: waits.append(s)
    original_read = job.read

    def stop_after(sock):
        got = original_read(sock)
        if not socks:
            job.stop.set()
        return got
    job.read = stop_after
    job.run()
    check("connect: failed tries wait 1 then 2 seconds; a connection that said hello starts the "
          "wait over (the next failure waits 1 again); a disconnect or a close reconnects at once",
          waits, [1, 2, 1])
    check("connect: each envelope was acknowledged by its id on the connection it came on, and "
          "every connection was closed",
          (s1.sent, s2.sent, s3.sent, [s.closed for s in (s1, s2, s3)]),
          ([{"envelope_id": "E1"}], [], [{"envelope_id": "E2"}], [True] * 3))
    check("connect: the address Slack gave was used, and it carried a ticket",
          (len(attempts), all("SECRETTICKET" in e for e in attempts)), (6, True))
    beat = json.load(open(os.path.join(cfg["state_dir"], HEARTBEAT_FILE)))
    check("heartbeat: written, with the schema, an awake clock, the connection's state and counts",
          (beat["schema"], isinstance(beat["awake_clock"], float), beat["connected"],
           beat["last_heard_at"] is not None, beat["handled"] >= 1, beat["reconnects"] >= 1),
          (HEARTBEAT_SCHEMA, True, False, True, True, True))
    check("log: neither the app token nor the connection's ticket appears in anything said",
          [l for l in job.lines if "SECRETAPPTOKEN" in l or "SECRETTICKET" in l or "xapp-" in l], [])

    # an ack comes before the handler ever runs
    cfg, slack, tracker, tcfg, ob = world()
    order = []
    sock = FakeWS([text({"type": "hello"}), text(event("1760000031.000100", eid="E9"))])
    orig_send = sock.send_text
    sock.send_text = lambda t: (order.append("ack"), orig_send(t))[1]
    turn = FakeTurn({"text": "ok", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn, connect=lambda u: sock)
    job.handle_real = job.handle
    job.handle = lambda env: (order.append("handle"), job.handle_real(env))[1]
    job.read(sock)
    job.work.put(None)
    job.worker()
    check("ack: the envelope is acknowledged before it is handled", order[:2], ["ack", "handle"])
    check("ack: the ack carries the envelope id and nothing else", sock.sent, [{"envelope_id": "E9"}])
    cfg, slack, tracker, tcfg, ob = world()
    sock = FakeWS([text({"type": "hello"}), text(event("1760000032.000100", eid="E7")),
                   text(event("1760000032.000100", eid="E7")), text({"type": "interactive",
                                                                   "envelope_id": "E8"})])
    turn = FakeTurn({"text": "once", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn)
    handled_envelopes = []
    job.handle_real = job.handle
    job.handle = lambda env: (handled_envelopes.append(env.get("envelope_id")),
                              job.handle_real(env))[1]
    job.read(sock)
    job.work.put(None)
    job.worker()
    check("ack: a redelivered envelope is acknowledged again but answered once; an event of "
          "another kind is acknowledged and ignored",
          (sock.sent, len(turn.calls), handled_envelopes),
          ([{"envelope_id": "E7"}, {"envelope_id": "E7"}, {"envelope_id": "E8"}], 1,
           ["E7", "E8"]))

    # -- 8b. the pass is the catch-up, and it answers the same way (the hooks in handle and take_turn) ----------------------------
    def pass_world(**over):
        cfg_, slack_, tracker_, _t, _ob = world(**over)
        slack_.profiles[ALICE] = dict(slack_.profiles.get(ALICE) or {}, id=ALICE, team_id=bridge.TEAM)
        return cfg_, slack_, tracker_

    def pass_run(cfg_, slack_, tracker_, turn_):
        bridge.Pass.run_turn = turn_
        try:
            return bridge.run_once(cfg_, slack_, tracker_, False, now=NOW, out=io.StringIO())
        finally:
            del bridge.Pass.run_turn

    cfg, slack, tracker = pass_world()
    top = slack.add(ALICE, "what about PROD-6?", channel=DM, at=NOW - 30)
    left_over, real_once = [], bridge.Pass.handle_once

    def watch_once(self, channel, msg):
        try:
            return real_once(self, channel, msg)
        finally:
            left_over.append((self.reply_channel, self.reply_thread))
    bridge.Pass.handle_once = watch_once
    try:
        pass_run(cfg, slack, tracker, FakeTurn({"text": "Caught up.", "session_id": SID}))
    finally:
        bridge.Pass.handle_once = real_once
    check("catch-up: the thread a reply goes to is for one message only: none is left set after it",
          (len(left_over) >= 1, set(left_over)), (True, {(None, None)}))
    st = json.load(open(bridge.state_path(cfg)))
    check("catch-up: with push_mode on, the pass answers a DM message in a thread under it, and "
          "remembers the thread as the conversation",
          ([(c, t, x) for c, t, x in slack.posts if x == "Caught up."], sorted(st["turn_threads"]),
           sorted(st["turn_sessions"])), ([(DM, top, "Caught up.")], ["%s:%s" % (DM, top)],
                                          ["%s:%s" % (DM, top)]))
    reply = slack.add(ALICE, "and the plan?", thread=top, channel=DM, at=NOW - 20)
    turn = FakeTurn({"text": "Same thread.", "session_id": SID, "resumed": True})
    pass_run(cfg, slack, tracker, turn)
    check("catch-up: an owner's reply in that thread is answered, in the same thread, in the same "
          "conversation",
          ([(c, t, x) for c, t, x in slack.posts if x == "Same thread."],
           turn.calls[0]["session"]["id"]), ([(DM, top, "Same thread.")], SID))
    root_post = slack.post(DM, None, "A question of the bridge's own.")
    other = slack.add(ALICE, "a reply under a post the turn never answered in", thread=root_post,
                      channel=DM, at=NOW - 10)
    n_posts = len(slack.posts)
    turn = FakeTurn({"text": "should not run", "session_id": SID})
    pass_run(cfg, slack, tracker, turn)
    check("catch-up: a reply in a thread the turn never answered in is read as before: not answered",
          (turn.calls, [x.startswith("I can't pass that on to a dev: I have no record")
                        for c, t, x in slack.posts[n_posts:]]), ([], [True]))
    cfg, slack, tracker = pass_world(push_mode=False)
    top = slack.add(ALICE, "what about PROD-6?", channel=DM, at=NOW - 30)
    pass_run(cfg, slack, tracker, FakeTurn({"text": "As always.", "session_id": SID}))
    slack.add(ALICE, "and in a thread?", thread=top, channel=DM, at=NOW - 20)
    turn = FakeTurn({"text": "never", "session_id": SID})
    pass_run(cfg, slack, tracker, turn)
    st = json.load(open(bridge.state_path(cfg)))
    check("off: with push_mode off the reply is top-level, no thread is remembered, and a reply "
          "inside a thread is ignored, as before",
          ([(c, t) for c, t, x in slack.posts if x == "As always."], st["turn_threads"], turn.calls),
          ([(DM, None)], {}, []))

    # -- 9. the config: off by default, the job refuses to run off, keys are checked ----------------------------------------
    check("config: push_mode is off by default and the token name has a default",
          (cfg0["push_mode"], cfg0["push_token_env"]), (False, "BRIDGE_PUSH_APP_TOKEN"))
    refused = io.StringIO()
    real_err, sys.stderr = sys.stderr, refused
    try:
        conf_off = os.path.join(ROOTDIR, "off.json")
        json.dump(dict(bridge.EXAMPLE_CONFIG, channel_ids=[CHAN], owner_user_id=OWNER,
                       bot_user_id=ME, state_dir=os.path.join(ROOTDIR, "off")), open(conf_off, "w"))
        code = main(["run", "--config", conf_off])
        code_usage = main(["run"])
    finally:
        sys.stderr = real_err
    check("config: run with push_mode off, or with bad arguments, refuses (usage) and says why",
          (code, code_usage, "push_mode is off" in refused.getvalue()),
          (EXIT_USAGE, EXIT_USAGE, True))
    for bad, want in ((dict(push_mode="yes"), "config 'push_mode' must be true or false"),
                      (dict(push_token_env="lower"), "config 'push_token_env' must be an ENV VAR NAME"),
                      (dict(push_token_env="BRIDGE_SLACK_BOT_TOKEN"), "may not be the bot token's"),
                      (dict(push_mode=True, ask_tech_lead=True), "needs 'tech_lead_turn' on")):
        try:
            bridge.validate_config(dict(bridge.EXAMPLE_CONFIG, channel_ids=[CHAN],
                                        owner_user_id=OWNER, bot_user_id=ME,
                                        state_dir=os.path.join(ROOTDIR, "bad"), **bad))
            got = "valid"
        except BridgeError as exc:
            got = str(exc)
        check("config: %s" % list(bad)[0], want in got, True)

    # -- 10. one real WebSocket over loopback: hello, an event, its ack, the reply -----------------------------------------------
    cfg, slack, tracker, tcfg, ob = world()
    GUID = ws.GUID
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got_ack = []

    def serve():
        conn, _a = srv.accept()
        conn.settimeout(10)
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(4096)
        key = [l.split(":", 1)[1].strip() for l in data.decode().split("\r\n")
               if l.lower().startswith("sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
        conn.sendall(ws.encode_frame(ws.OP_TEXT, json.dumps({"type": "hello"}).encode(),
                                     mask=False))
        conn.sendall(ws.encode_frame(ws.OP_TEXT, json.dumps(
            event("1760000041.000100", eid="EL")).encode(), mask=False))
        buf = bytearray()
        while len(buf) < 6 or len(buf) < 6 + (buf[1] & 0x7F):
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        if len(buf) >= 6:
            n, mask = buf[1] & 0x7F, bytes(buf[2:6])
            got_ack.append(json.loads(bytes(b ^ mask[i % 4]
                                            for i, b in enumerate(bytes(buf[6:6 + n])))))
        time.sleep(0.5)
        conn.close()
    server = threading.Thread(target=serve, daemon=True)
    server.start()
    turn = FakeTurn({"text": "Over the wire.", "session_id": SID})
    job = job_for(cfg, slack, tracker, tcfg, turn,
                  connect=lambda url: ws.connect("ws://127.0.0.1:%d/x?ticket=SECRETTICKET" % port,
                                                 loopback_ok=True, timeout=5))
    job.sleep = lambda seconds: time.sleep(0.05)
    runner = threading.Thread(target=job.run, daemon=True)
    runner.start()
    for _ in range(100):
        if turn.calls and got_ack:
            break
        time.sleep(0.1)
    job.stop.set()
    runner.join(10)
    server.join(10)
    srv.close()
    check("loopback: a real WebSocket carries hello and an event, the ack comes back by id, and "
          "the reply is posted in the thread",
          (got_ack, [c[3] for c in slack.calls if c[0] == "post"], runner.is_alive()),
          ([{"envelope_id": "EL"}], ["Over the wire."], False))
    check("loopback: nothing of the connection's ticket is in what was said",
          [l for l in job.lines if "SECRETTICKET" in l], [])

    if failures:
        print("FAIL: pipeline_bridge_push selftest (%d of %d cases failed)" % (len(failures),
                                                                              cases[0]))
        for line in failures:
            print("  - " + line[:700])
        return 1
    print("OK: pipeline_bridge_push selftest (%d cases)" % cases[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
