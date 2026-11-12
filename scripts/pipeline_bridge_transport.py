#!/usr/bin/env python3
"""The Slack bridge's transport: its error, its one HTTP call and its Slack client.

    python3 scripts/pipeline_bridge_transport.py --selftest

WHY A MODULE OF ITS OWN (KIT-312). scripts/pipeline_bridge.py was one file of more than
12,000 lines, so Stage E withheld it from every review it was in, and every bridge feature
conflicted with every other. It is split by concern, with no change in behaviour, and each
module keeps its own tests. This was the first part (KIT-388): the Slack side of the
bridge's transports. The tracker client is in pipeline_bridge_tracker.py.

WHAT IS HERE
  BridgeError   the bridge's one error, carrying the exit code a pass ends on.
  _http_json    the one HTTP call: JSON in and out, and a redirect refused, so the
                Authorization header never goes where a redirect points.
  SlackClient   the bridge app's side of Slack. Every `ok: false` is raised, never read as
                empty: a token that cannot see the channel must not look like a channel
                where nobody asked.

pipeline_bridge imports all three, so `pipeline_bridge.BridgeError` and
`pipeline_bridge.SlackClient` are these very objects. The tracker client calls its own
module's name for `_http_json` (pipeline_bridge_tracker), which is this same function: a
test standing in for the tracker's HTTP replaces it there, and one standing in for
Slack's replaces it here.

  AnswerStream  an answer shown as it is written (KIT-565): it sends a line only after the line
                is whole and a caller's judge has passed it, and ends by making the message
                hold exactly the words the whole-answer path posts.

It makes no tracker call. Its Slack writes are a message (`chat.postMessage`), the opening
of a member's DM with the app (`conversations.open`), the thread's status line and the
removal of the "on it" line (KIT-560), and the streamed answer (`chat.startStream`,
`chat.appendStream`, `chat.stopStream`, and `chat.update` to make it exact: KIT-565); the
selftest pins them all.
"""
import json
import os
import queue
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The notifier's exit codes, as the bridge's are (pipeline_bridge.py imports both).
import pipeline_notify_local as notify  # noqa: E402

EXIT_ERROR = notify.EXIT_ERROR
MAX_THREAD_PAGES = 10


class BridgeError(Exception):
    def __init__(self, message, code=EXIT_ERROR):
        Exception.__init__(self, message)
        self.code = code


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the Authorization header wherever it named."""
    def redirect_request(self, *_a, **_k):
        raise urllib.error.HTTPError(_a[0].full_url, 307, "redirect refused", None, None)


_OPENER = urllib.request.build_opener(_RefuseRedirect)


def _http_json(url, headers, payload=None, timeout=20, form=False):
    """The answer's JSON. A form call's payload goes form-encoded, every other call's as JSON:
    a JSON body labelled as a form is what Slack refuses with invalid_form_data (KIT-616)."""
    if payload is None:
        data = None
    elif form:
        data = urllib.parse.urlencode(payload).encode("utf-8")
    else:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="GET" if payload is None else "POST")
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


class SlackClient(object):
    """The bridge app's side of Slack. Every `ok: false` is RAISED, never read as empty:
    a token that cannot see the channel must not look like a channel where nobody asked."""

    def __init__(self, cfg, token):
        self.base = cfg["chat_api_base"]
        self.token = token

    def _get(self, method, params):
        try:
            doc = _http_json("%s/%s?%s" % (self.base, method, urllib.parse.urlencode(params)),
                             {"Authorization": "Bearer %s" % self.token})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BridgeError("Slack %s could not be read (%s)" % (method, type(exc).__name__))
        if not isinstance(doc, dict) or not doc.get("ok"):
            raise BridgeError("Slack %s refused: %s" % (
                method, (doc or {}).get("error") if isinstance(doc, dict) else "no JSON"))
        return doc

    def auth_test(self):
        doc = self._get("auth.test", {})
        return {"user_id": doc.get("user_id"), "team_id": doc.get("team_id"),
                "bot_id": doc.get("bot_id")}

    def history(self, channel, oldest, max_pages):
        out, cursor = [], ""
        for _ in range(max_pages):
            params = {"channel": channel, "oldest": "%.6f" % oldest, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            doc = self._get("conversations.history", params)
            out.extend(m for m in (doc.get("messages") or []) if isinstance(m, dict))
            cursor = (doc.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                return out
        raise BridgeError("channel %s has more than %d pages of history in the window — "
                          "refusing to guess which messages were read" % (channel, max_pages))

    def recent(self, channel, before, limit):
        """The newest `limit` messages before `before`, newest first: ONE page, with no
        oldest bound, so lookback_days never limits it (KIT-298: the DM's recent conversation
        under a forwarded question). It is context only, so no cursor is followed."""
        doc = self._get("conversations.history", {"channel": channel, "latest": before,
                                                  "inclusive": "false", "limit": limit})
        return [m for m in (doc.get("messages") or []) if isinstance(m, dict)]

    def replies(self, channel, ts):
        out, cursor = [], ""
        for _ in range(MAX_THREAD_PAGES):
            params = {"channel": channel, "ts": ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            doc = self._get("conversations.replies", params)
            out.extend(m for m in (doc.get("messages") or []) if isinstance(m, dict))
            cursor = (doc.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                return out
        raise BridgeError("thread %s has more than %d pages of replies" % (ts, MAX_THREAD_PAGES))

    def user(self, user_id):
        return self._get("users.info", {"user": user_id}).get("user") or {}

    def open_dm(self, user_id):
        """The id of a member's DM with this app, from `conversations.open` (KIT-295). It
        answers the same DM every time. It needs `im:write`; without it the refusal names
        the scope, so a missing one is never read as a member with no DM."""
        try:
            doc = _http_json("%s/conversations.open" % self.base,
                             {"Authorization": "Bearer %s" % self.token,
                              "Content-Type": "application/json; charset=utf-8"},
                             {"users": user_id})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BridgeError("Slack conversations.open failed (%s)" % type(exc).__name__)
        if not isinstance(doc, dict) or not doc.get("ok"):
            error = (doc or {}).get("error") if isinstance(doc, dict) else "no JSON"
            if error == "missing_scope":
                raise BridgeError("Slack conversations.open refused: missing_scope. The "
                                  "bridge app needs im:write to open a DM: paste the "
                                  "manifest `compose` prints into the app and reinstall it "
                                  "(card CK-B1)")
            raise BridgeError("Slack conversations.open refused: %s" % error)
        channel = doc.get("channel")
        return channel.get("id") if isinstance(channel, dict) else None

    def _call(self, method, payload, token=None, form=False):
        """One Slack write that answers `ok`, or BridgeError in fixed words (KIT-560). The
        address and the payload are never in an error."""
        headers = {"Authorization": "Bearer %s" % (token or self.token),
                   "Content-Type": ("application/x-www-form-urlencoded" if form
                                    else "application/json; charset=utf-8")}
        try:
            # Only a form call names `form`, so a JSON call reaches _http_json (and every
            # stand-in other modules' selftests put there) exactly as before (KIT-616).
            if form:
                doc = _http_json("%s/%s" % (self.base, method), headers, payload, form=True)
            else:
                doc = _http_json("%s/%s" % (self.base, method), headers, payload)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BridgeError("Slack %s failed (%s)" % (method, type(exc).__name__))
        if not isinstance(doc, dict) or not doc.get("ok"):
            raise BridgeError("Slack %s refused: %s"
                              % (method, (doc or {}).get("error") if isinstance(doc, dict)
                                 else "?"))
        return doc

    def open_socket_url(self, app_token):
        """The wss:// address Slack's Socket Mode connection opens at (KIT-560), asked for with
        the app-level token. It carries a one-time ticket, so the caller never prints it."""
        url = self._call("apps.connections.open", {}, token=app_token, form=True).get("url")
        if not (isinstance(url, str) and url.startswith("wss://")):
            raise BridgeError("Slack apps.connections.open gave no wss:// address")
        return url

    def set_status(self, channel, thread_ts, status):
        """Slack's own status line under a thread (KIT-560); "" clears it. It clears itself when
        the app replies."""
        self._call("assistant.threads.setStatus",
                   {"channel_id": channel, "thread_ts": thread_ts, "status": status})

    def delete_message(self, channel, ts):
        """One of this app's own messages (KIT-560): the "on it" line the answer replaces."""
        self._call("chat.delete", {"channel": channel, "ts": ts})

    def start_stream(self, channel, thread_ts, text, user_id, team_id):
        """Open a streamed message in a thread with its first text (KIT-565); its `ts`. The
        recipient is the member whose DM it is. Slack's own refusal word is the error."""
        doc = self._call("chat.startStream", {
            "channel": channel, "thread_ts": thread_ts, "markdown_text": text,
            "recipient_user_id": user_id, "recipient_team_id": team_id})
        if not isinstance(doc.get("ts"), str) or not doc["ts"]:
            raise BridgeError("Slack chat.startStream gave no message")
        return doc["ts"]

    def append_stream(self, channel, ts, text):
        self._call("chat.appendStream", {"channel": channel, "ts": ts, "markdown_text": text})

    def stop_stream(self, channel, ts):
        self._call("chat.stopStream", {"channel": channel, "ts": ts})

    def update_message(self, channel, ts, text):
        """One of this app's own messages, made to say `text` (KIT-565)."""
        self._call("chat.update", {"channel": channel, "ts": ts, "text": text})

    def post(self, channel, thread_ts, text):
        payload = {"channel": channel, "text": text, "unfurl_links": False,
                   "unfurl_media": False}
        if thread_ts:
            payload["thread_ts"] = thread_ts     # none: a message of its own (KIT-285)
        try:
            doc = _http_json("%s/chat.postMessage" % self.base,
                             {"Authorization": "Bearer %s" % self.token,
                              "Content-Type": "application/json; charset=utf-8"}, payload)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BridgeError("Slack chat.postMessage failed (%s)" % type(exc).__name__)
        if not isinstance(doc, dict) or not doc.get("ok") or not doc.get("ts"):
            raise BridgeError("Slack chat.postMessage refused: %s"
                              % ((doc or {}).get("error") if isinstance(doc, dict) else "?"))
        return doc["ts"]


# ══════════════════════════════════════════════════════════════════════════════════════
# A streamed answer (KIT-565)
# ══════════════════════════════════════════════════════════════════════════════════════
class AnswerStream(object):
    """An answer shown in its thread as it is written, with nothing raw in it.

    The model's text is fed in as it arrives. A line is sent only once it is whole (its newline
    has come), and only after `judge` has passed it: judge(raw line) is (held, text), where
    `held` is a reason ("" when none) that stops the stream, and `text` is the line as it may be
    shown, or None when the line is left out. A left-out line leaves nothing in its place. The
    lines the whole-answer path would fold or trim (blank lines at either end, a run of them after
    a left-out line) are folded or trimmed the same way here, so the lines sent are the lines that
    path would post, up to its cuts.

    It ends one of three ways, and each leaves Slack holding exactly one message:
      settle(words)   the stream stops and the message is updated to `words`, the whole-answer
                      path's text, always: the stream is Slack's markdown and that path posts
                      Slack's own markup, so the closing update is what makes the two the same
                      even when the lines sent already read as those words;
      replace(text)   the stream stops and the message holds `text` instead of anything streamed
                      (the held-back line: nothing of the answer stays in Slack);
      discard()       the stream stops and the message is removed (a turn that gave no answer).

    With `threaded` the text is handed to a worker that judges and sends it, so a slow Slack never
    holds up the reader of the CLI's output (and its timeout); the three endings wait for the worker
    to finish first. Without it, as the selftest runs it, each piece is handled where it is fed.
    Each returns True when Slack now holds the target message, False when it does not and the
    caller must post it. Slack refusing any call is never an error here: `why` says it, in
    Slack's own word, the stream stops, and the caller's whole-answer post stands.

    Nothing here knows what a filter is, which words are banned or what a secret looks like; the
    judge does. No call is made until a line has passed it."""

    JOIN_SECONDS = 60

    def __init__(self, slack, channel, thread_ts, judge, user_id, team_id, max_chars, read_max,
                 threaded=False):
        self.slack, self.channel, self.thread_ts = slack, channel, thread_ts
        self.judge, self.user_id, self.team_id = judge, user_id, team_id
        self.max_chars, self.read_max = max_chars, read_max
        self.ts = None                    # the streaming message, until it is ended
        self.final_ts = None              # the message that holds the answer, once ended
        self.started = False
        self.lines = []                   # what Slack was sent, line by line
        self.size = 0                     # the length of "\n".join(self.lines)
        self.raw = 0                      # raw characters taken in, to the read cap
        self.pending = ""                 # the start of a line whose end has not come
        self.blanks = 0                   # blank lines waiting for a line to follow them
        self.last_blank = False
        self.removed_any = False
        self.held = ""                    # why a line stopped the stream
        self.cut = False                  # the answer ran past what may be shown
        self.why = ""                     # Slack's refusal, in its own word; "" when none
        self.broken = False               # Slack refused: nothing more is sent
        self._q = queue.Queue() if threaded else None
        self._worker = None

    def _stopped(self):
        return bool(self.held or self.cut or self.broken)

    def feed(self, piece):
        """Text as the model writes it. Never raises, and with `threaded` never waits on Slack."""
        if self._q is None:
            self._feed_now(piece)
            return
        if self._worker is None:
            self._worker = threading.Thread(target=self._drain, daemon=True)
            self._worker.start()
        self._q.put(piece)

    def _drain(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            if isinstance(item, threading.Event):
                item.set()
                continue
            self._feed_now(item)

    def flush(self):
        """Wait until everything fed so far has been handled (the selftest's door)."""
        if self._q is not None and self._worker is not None and self._worker.is_alive():
            done = threading.Event()
            self._q.put(done)
            done.wait(self.JOIN_SECONDS)

    def _close_input(self):
        """No more text will come: let the worker finish what it has. A worker that does not finish
        in time is told to stop sending, and the ending goes on without it."""
        if self._q is None or self._worker is None:
            return
        self._q.put(None)
        self._worker.join(self.JOIN_SECONDS)
        if self._worker.is_alive():
            self.broken = True
            self.why = self.why or "Slack did not answer in time"

    def _feed_now(self, piece):
        try:
            if self._stopped() or not isinstance(piece, str):
                return
            self.pending += piece
            while "\n" in self.pending and not self._stopped():
                line, self.pending = self.pending.split("\n", 1)
                self._line(line)
        except Exception as exc:                                       # noqa: BLE001
            self.broken = True
            self.why = self.why or "the stream failed (%s)" % type(exc).__name__

    def _refused(self, exc):
        self.why = self.why or str(exc)[:120]

    def _line(self, raw):
        self.raw += len(raw) + 1
        if self.raw > self.read_max:
            self.cut = True
            return
        held, text = self.judge(raw)
        if held:
            self.held = held
            return
        if text is None:
            self.removed_any = True
            return
        if not text.strip():
            if self.lines and not (self.removed_any and self.last_blank):
                self.blanks += 1
                self.last_blank = True
            return
        added = (self.blanks + 1 + len(text)) if self.lines else len(text)
        if self.size + added > self.max_chars:
            self.cut = True
            return
        chunk = ("\n" * (self.blanks + 1) + text) if self.lines else text
        try:
            if self.ts is None:
                self.ts = self.slack.start_stream(self.channel, self.thread_ts, chunk,
                                                  self.user_id, self.team_id)
                self.started = True
            else:
                self.slack.append_stream(self.channel, self.ts, chunk)
        except BridgeError as exc:
            self.broken = True
            self._refused(exc)
            return
        self.lines.extend([""] * self.blanks + [text])
        self.size += added
        self.blanks, self.last_blank = 0, False

    def _finish_line(self):
        """The turn is over: a last line with no newline after it is whole."""
        if self.pending and not self._stopped():
            line, self.pending = self.pending, ""
            try:
                self._line(line)
            except Exception as exc:                                   # noqa: BLE001
                self.broken = True
                self.why = self.why or "the stream failed (%s)" % type(exc).__name__

    def _stop(self, ts):
        try:
            self.slack.stop_stream(self.channel, ts)
        except BridgeError as exc:
            self._refused(exc)

    def _end(self, target):
        self._close_input()
        ts = self.ts
        if ts is None:
            return False
        self.ts, self.final_ts = None, ts
        self._stop(ts)
        try:
            self.slack.update_message(self.channel, ts, target)
            return True
        except BridgeError as exc:
            self._refused(exc)
        try:
            self.slack.delete_message(self.channel, ts)
        except BridgeError as exc:
            self._refused(exc)
        self.final_ts = None
        return False

    def settle(self, words):
        self._close_input()
        self._finish_line()
        return self._end(words)

    def replace(self, text):
        return self._end(text)

    def discard(self):
        self._close_input()
        ts = self.ts
        if ts is None:
            return
        self.ts = None
        self._stop(ts)
        try:
            self.slack.delete_message(self.channel, ts)
        except BridgeError as exc:
            self._refused(exc)


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline: the HTTP call is stubbed, or a server on 127.0.0.1 stands in
# ══════════════════════════════════════════════════════════════════════════════════════
CHAN, ME, TEAM, ALICE = "C0BRIDGE01", "UBRIDGEBOT", "T0TEAM0001", "U0ALICE001"


def selftest():
    import re
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    body = src[:src.index("# Selftest — offline")]

    # 1. THE REAL CLIENT, with only the HTTP call stubbed: a refusal from Slack is never
    #    read as "nothing there", and each call sends what it should, the token only in
    #    its header.
    def through(answer, call):
        sent = []

        def stub(url, headers, payload=None, timeout=20, form=False):
            sent.append((url, dict(headers), payload))
            return answer(url, payload)
        real_http = globals()["_http_json"]
        globals()["_http_json"] = stub
        try:
            got = call()
        except BridgeError as exc:
            got = "BridgeError: %s" % exc
        finally:
            globals()["_http_json"] = real_http
        return got, sent

    def raises(exc):
        def answer(_url, _payload):
            raise exc
        return answer

    slack = SlackClient({"chat_api_base": "https://slack.example.invalid/api"}, "xoxb-test")
    got, _s = through(lambda _u, _p: {"ok": False, "error": "not_in_channel"},
                      lambda: slack.history(CHAN, 0, 10))
    check("real-slack:ok-false-is-an-error-not-an-empty-channel",
          (str(got).startswith("BridgeError"), "not_in_channel" in str(got)), (True, True))
    got, sent = through(lambda _u, _p: {"ok": True, "messages": [{"ts": "1.0"}],
                                        "response_metadata": {"next_cursor": "more"}},
                        lambda: slack.history(CHAN, 0, 2))
    check("real-slack:history-past-the-page-cap-is-an-error",
          (str(got).startswith("BridgeError"), "more than 2 pages" in str(got), len(sent)),
          (True, True, 2))
    got, sent = through(lambda _u, _p: {"ok": True, "messages": [{"ts": "1.0"}, "junk"],
                                        "response_metadata": {"next_cursor": "more"}},
                        lambda: slack.replies(CHAN, "1.0"))
    check("real-slack:a-thread-past-its-page-cap-is-an-error",
          (str(got).startswith("BridgeError"), "more than %d pages" % MAX_THREAD_PAGES
           in str(got), len(sent)), (True, True, MAX_THREAD_PAGES))
    got, sent = through(lambda _u, _p: {"ok": True, "messages": [{"ts": "2.0"}, 7]},
                        lambda: slack.recent(CHAN, "3.0", 20))
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(sent[0][0]).query)
    check("real-slack:recent-is-one-page-before-a-point-with-no-oldest-bound",
          (got, query.get("latest"), query.get("inclusive"), query.get("limit"),
           "oldest" in query, len(sent)), ([{"ts": "2.0"}], ["3.0"], ["false"], ["20"],
                                           False, 1))
    got, sent = through(lambda _u, _p: {"ok": True, "user_id": ME, "team_id": TEAM,
                                        "bot_id": "B1", "url": "x"},
                        lambda: slack.auth_test())
    check("real-slack:auth-test-reads-who-the-token-is",
          (got, sent[0][1], "xoxb-test" in sent[0][0]),
          ({"user_id": ME, "team_id": TEAM, "bot_id": "B1"},
           {"Authorization": "Bearer xoxb-test"}, False))
    for name, exc in (("network", urllib.error.URLError("down")),
                      ("not-json", ValueError("not JSON")), ("socket", OSError("reset"))):
        got, _s = through(raises(exc), lambda: slack.user(ALICE))
        check("real-slack:a-read-that-fails-is-an-error:%s" % name,
              (str(got).startswith("BridgeError"), "could not be read" in str(got)),
              (True, True))
    got, _s = through(lambda _u, _p: ["not", "a", "dict"], lambda: slack.user(ALICE))
    check("real-slack:an-answer-that-is-no-object-is-an-error",
          (str(got).startswith("BridgeError"), "no JSON" in str(got)), (True, True))

    # 2. THE TWO SLACK WRITES. A DM opened, with its scope named when it is missing; a post,
    #    with no link previews, in a thread only when one is given.
    got, sent = through(lambda _u, _p: {"ok": True, "channel": {"id": "D0DM000001"}},
                        lambda: slack.open_dm(ALICE))
    check("real-slack:open-dm-answers-the-dm",
          (got, sent[0][0].endswith("/conversations.open"), sent[0][2],
           sent[0][1].get("Authorization")),
          ("D0DM000001", True, {"users": ALICE}, "Bearer xoxb-test"))
    got, _s = through(lambda _u, _p: {"ok": False, "error": "missing_scope"},
                      lambda: slack.open_dm(ALICE))
    check("real-slack:open-dm-names-a-missing-scope",
          ("im:write" in str(got), "CK-B1" in str(got), str(got).startswith("BridgeError")),
          (True, True, True))
    got, _s = through(lambda _u, _p: {"ok": False, "error": "user_not_found"},
                      lambda: slack.open_dm(ALICE))
    check("real-slack:open-dm-other-refusal-is-named",
          str(got), "BridgeError: Slack conversations.open refused: user_not_found")
    got, _s = through(lambda _u, _p: {"ok": True, "channel": "D0"}, lambda: slack.open_dm(ALICE))
    check("real-slack:open-dm-with-no-channel-object-names-none", got, None)
    got, _s = through(raises(urllib.error.URLError("down")), lambda: slack.open_dm(ALICE))
    check("real-slack:open-dm-that-fails-is-an-error",
          str(got), "BridgeError: Slack conversations.open failed (URLError)")
    got, sent = through(lambda _u, _p: {"ok": True, "ts": "9.0"},
                        lambda: slack.post(CHAN, "1.0", "hello"))
    check("real-slack:post-in-a-thread",
          (got, sent[0][0].endswith("/chat.postMessage"), sent[0][2]),
          ("9.0", True, {"channel": CHAN, "text": "hello", "unfurl_links": False,
                         "unfurl_media": False, "thread_ts": "1.0"}))
    got, sent = through(lambda _u, _p: {"ok": True, "ts": "9.1"},
                        lambda: slack.post(CHAN, None, "a message of its own"))
    # Every post in a DM, and every message of its own, relies on this (KIT-285).
    check("real-slack:post-without-a-thread-is-a-message-of-its-own",
          (got, sent[0][2]), ("9.1", {"channel": CHAN, "text": "a message of its own",
                                      "unfurl_links": False, "unfurl_media": False}))
    for label, answer, needle in (
            ("refused", {"ok": False, "error": "channel_not_found"}, "channel_not_found"),
            ("no-ts", {"ok": True}, "refused: None"),
            ("no-object", "nope", "refused: ?")):
        got, _s = through(lambda _u, _p, a=answer: a, lambda: slack.post(CHAN, None, "x"))
        check("real-slack:post-%s-is-an-error" % label,
              (str(got).startswith("BridgeError: Slack chat.postMessage refused"),
               needle in str(got)), (True, True))
    got, _s = through(raises(OSError("reset")), lambda: slack.post(CHAN, None, "x"))
    check("real-slack:post-that-fails-is-an-error",
          str(got), "BridgeError: Slack chat.postMessage failed (OSError)")

    # 3. THE REAL TRANSPORT REFUSES A REDIRECT: the token never reaches where it points.
    import http.server
    import threading

    class _Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.hits.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/api/"):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/stolen"
                                 % self.server.server_address[1])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            reply = json.dumps({"ok": True, "user_id": ME, "team_id": TEAM}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
    server.hits = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    saved_env = dict((k, os.environ.get(k)) for k in ("no_proxy", "NO_PROXY"))
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    try:
        local = SlackClient({"chat_api_base": "http://127.0.0.1:%d/api"
                             % server.server_address[1]}, "redirect-test-token")
        try:
            local.auth_test()
            got = "followed"
        except BridgeError as exc:
            got = str(exc)
        check("transport:a-redirect-is-refused-and-the-token-never-reaches-its-target",
              ("could not be read" in got, [h for h in server.hits if h[0] == "/stolen"],
               [h[1] for h in server.hits]),
              (True, [], ["Bearer redirect-test-token"]))
    finally:
        server.shutdown()
        server.server_close()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # 3a. A FORM CALL SENDS A FORM (KIT-616), through the real transport, not the stub: the body
    #     and content type a local server receives. apps.connections.open is form-encoded; a JSON
    #     body labelled as a form is what Slack refused with invalid_form_data on the first live
    #     run. Every other call still sends JSON.
    class _Recorder(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.server.hits.append((self.path.rsplit("/", 1)[-1],
                                     self.headers.get("Content-Type"), body))
            reply = json.dumps({"ok": True,
                                "url": "wss://wss-primary.slack.com/l/?ticket=T"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *_args):
            pass

    recorder = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    recorder.hits = []
    threading.Thread(target=recorder.serve_forever, daemon=True).start()
    saved_env = dict((k, os.environ.get(k)) for k in ("no_proxy", "NO_PROXY"))
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    try:
        local = SlackClient({"chat_api_base": "http://127.0.0.1:%d/api"
                             % recorder.server_address[1]}, "form-test-token")
        try:
            opened = local.open_socket_url("xapp-test")
            local.delete_message("D0DM000001", "1.0")
        except BridgeError as exc:
            opened = "BridgeError: %s" % exc
        hits = dict((h[0], (h[1], h[2])) for h in recorder.hits)
        deleted = hits.get("chat.delete") or (None, b"{}")
        check("real-slack:the-socket-call-sends-a-real-form-body-and-other-calls-send-json",
              (opened, hits.get("apps.connections.open"), deleted[0],
               json.loads(deleted[1].decode("utf-8") or "{}")),
              ("wss://wss-primary.slack.com/l/?ticket=T",
               ("application/x-www-form-urlencoded", b""),
               "application/json; charset=utf-8", {"channel": "D0DM000001", "ts": "1.0"}))
    finally:
        recorder.shutdown()
        recorder.server_close()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # 3b. THE THREE CALLS OF THE SOCKET MODE JOB (KIT-560): what each sends, the app-level token
    #     only on the one that needs it, and a refusal is an error without the address in it.
    got, sent = through(lambda _u, _p: {"ok": True, "url": "wss://wss-primary.slack.com/l/?ticket=T"},
                        lambda: slack.open_socket_url("xapp-test"))
    check("real-slack:the-socket-address-is-asked-for-with-the-app-token-only",
          (got, sent[0][0].endswith("/apps.connections.open"), sent[0][1].get("Authorization"),
           sent[0][1].get("Content-Type")),
          ("wss://wss-primary.slack.com/l/?ticket=T", True, "Bearer xapp-test",
           "application/x-www-form-urlencoded"))
    for name, answer in (("not-wss", {"ok": True, "url": "https://slack.com/x"}),
                         ("none", {"ok": True}), ("refused", {"ok": False, "error": "invalid_auth"})):
        got, _s = through(lambda _u, _p, a=answer: a, lambda: slack.open_socket_url("xapp-test"))
        check("real-slack:a-socket-address-that-is-no-wss-address-is-an-error:%s" % name,
              (str(got).startswith("BridgeError"), "ticket" in str(got), "xapp-test" in str(got)),
              (True, False, False))
    got, sent = through(lambda _u, _p: {"ok": True}, lambda: slack.set_status(
        "D0DM000001", "1.0", "checking the plans"))
    check("real-slack:set-status-names-the-thread-and-the-phrase",
          (got, sent[0][0].endswith("/assistant.threads.setStatus"), sent[0][2],
           sent[0][1].get("Authorization")),
          (None, True, {"channel_id": "D0DM000001", "thread_ts": "1.0",
                        "status": "checking the plans"}, "Bearer xoxb-test"))
    got, _s = through(lambda _u, _p: {"ok": False, "error": "method_deprecated"},
                      lambda: slack.set_status("D0DM000001", "1.0", "x"))
    check("real-slack:a-status-slack-refuses-is-an-error-naming-slacks-word",
          str(got), "BridgeError: Slack assistant.threads.setStatus refused: method_deprecated")
    got, sent = through(lambda _u, _p: {"ok": True}, lambda: slack.delete_message("D0DM000001", "2.0"))
    check("real-slack:delete-names-the-channel-and-the-message",
          (got, sent[0][0].endswith("/chat.delete"), sent[0][2]),
          (None, True, {"channel": "D0DM000001", "ts": "2.0"}))
    got, _s = through(raises(urllib.error.URLError("down")), lambda: slack.delete_message("D1", "3"))
    check("real-slack:a-write-that-fails-is-an-error", str(got),
          "BridgeError: Slack chat.delete failed (URLError)")

    # 3c. THE STREAM'S FOUR CALLS (KIT-565): what each sends, and Slack's word when it refuses.
    got, sent = through(lambda _u, _p: {"ok": True, "ts": "7.0"},
                        lambda: slack.start_stream("D0DM000001", "1.0", "first line", ALICE, TEAM))
    check("real-slack:start-stream-opens-in-the-thread-for-the-member",
          (got, sent[0][0].endswith("/chat.startStream"), sent[0][2], sent[0][1].get("Authorization")),
          ("7.0", True, {"channel": "D0DM000001", "thread_ts": "1.0", "markdown_text": "first line",
                         "recipient_user_id": ALICE, "recipient_team_id": TEAM}, "Bearer xoxb-test"))
    got, _s = through(lambda _u, _p: {"ok": True}, lambda: slack.start_stream("D1", "1.0", "x", ALICE, TEAM))
    check("real-slack:a-stream-that-opens-with-no-message-is-an-error",
          str(got), "BridgeError: Slack chat.startStream gave no message")
    got, sent = through(lambda _u, _p: {"ok": True}, lambda: slack.append_stream("D1", "7.0", "\nmore"))
    check("real-slack:append-stream-names-the-message-and-the-text",
          (got, sent[0][0].endswith("/chat.appendStream"), sent[0][2]),
          (None, True, {"channel": "D1", "ts": "7.0", "markdown_text": "\nmore"}))
    got, sent = through(lambda _u, _p: {"ok": True}, lambda: slack.stop_stream("D1", "7.0"))
    check("real-slack:stop-stream-sends-no-text",
          (got, sent[0][0].endswith("/chat.stopStream"), sent[0][2]),
          (None, True, {"channel": "D1", "ts": "7.0"}))
    got, sent = through(lambda _u, _p: {"ok": True}, lambda: slack.update_message("D1", "7.0", "the words"))
    check("real-slack:update-names-the-message-and-the-words",
          (got, sent[0][0].endswith("/chat.update"), sent[0][2]),
          (None, True, {"channel": "D1", "ts": "7.0", "text": "the words"}))
    got, _s = through(lambda _u, _p: {"ok": False, "error": "message_not_in_streaming_state"},
                      lambda: slack.append_stream("D1", "7.0", "x"))
    check("real-slack:a-refused-append-is-an-error-naming-slacks-word", str(got),
          "BridgeError: Slack chat.appendStream refused: message_not_in_streaming_state")

    # 3d. THE ANSWER STREAM (KIT-565), against a Slack that records every call. The judge here
    #     stands in for the bridge's: it holds a line with SECRET in it, leaves out a line with
    #     TYPE THIS, and shows the rest in capitals so what was sent is told from what was fed.
    class Recorder(object):
        def __init__(self, fail=()):
            self.calls, self.fail = [], set(fail)

        def _do(self, name, *args):
            self.calls.append((name,) + args)
            if name in self.fail:
                raise BridgeError("Slack %s refused: simulated" % name)

        def start_stream(self, channel, thread, text, user, team):
            self._do("start", channel, thread, text, user, team)
            return "7.0"

        def append_stream(self, channel, ts, text):
            self._do("append", ts, text)

        def stop_stream(self, channel, ts):
            self._do("stop", ts)

        def update_message(self, channel, ts, text):
            self._do("update", ts, text)

        def delete_message(self, channel, ts):
            self._do("delete", ts)

    def judge(raw):
        if "SECRET" in raw:
            return "a credential shape", None
        if "TYPE THIS" in raw:
            return "", None
        return "", raw.upper()

    def stream(slack_, **kw):
        return AnswerStream(slack_, "D1", "1.0", kw.get("judge", judge), ALICE, TEAM,
                            kw.get("max_chars", 100), kw.get("read_max", 400))

    def sent_text(rec):
        return "".join(c[3] if c[0] == "start" else c[2]
                       for c in rec.calls if c[0] in ("start", "append"))

    rec = Recorder()
    st = stream(rec)
    st.feed("one\ntw")
    check("stream:nothing-is-sent-until-a-line-is-whole",
          [c[0] for c in rec.calls], ["start"])
    check("stream:the-first-line-opens-the-stream-in-the-thread-for-the-member",
          rec.calls[0], ("start", "D1", "1.0", "ONE", ALICE, TEAM))
    st.feed("o\nthree")
    check("stream:the-next-whole-line-is-appended-with-its-newline-first",
          [c[:3] for c in rec.calls[1:]], [("append", "7.0", "\nTWO")])
    check("stream:a-line-with-no-newline-yet-is-not-sent", sent_text(rec), "ONE\nTWO")
    check("stream:the-last-line-is-sent-when-the-turn-ends-and-the-message-is-always-updated-to-the-"
          "words-even-when-the-lines-sent-already-read-as-them",
          (st.settle("ONE\nTWO\nTHREE"), [c[0] for c in rec.calls], rec.calls[-1], st.final_ts),
          (True, ["start", "append", "append", "stop", "update"],
           ("update", "7.0", "ONE\nTWO\nTHREE"), "7.0"))

    rec = Recorder()
    st = stream(rec)
    st.feed("keep\nTYPE THIS now\nkeep two\n")
    check("stream:a-line-the-judge-leaves-out-is-never-sent-and-leaves-nothing-behind",
          (sent_text(rec), st.lines), ("KEEP\nKEEP TWO", ["KEEP", "KEEP TWO"]))

    rec = Recorder()
    st = stream(rec)
    st.feed("\n\nfirst\n\n\nsecond\nTYPE THIS\n\n\nthird\n\n\n")
    check("stream:blank-lines-are-trimmed-and-folded-as-the-whole-answer-path-does",
          st.lines, ["FIRST", "", "", "SECOND", "", "THIRD"])
    check("stream:trailing-blank-lines-are-never-sent", sent_text(rec).endswith("THIRD"), True)

    rec = Recorder()
    st = stream(rec)
    st.feed("fine\nthe SECRET is here\nnever sent\n")
    check("stream:a-line-that-looks-like-a-secret-stops-the-stream-and-nothing-after-it-is-sent",
          (st.held, sent_text(rec), [c[0] for c in rec.calls]),
          ("a credential shape", "FINE", ["start"]))
    st.feed("more\n")
    check("stream:a-stopped-stream-takes-no-more", [c[0] for c in rec.calls], ["start"])
    check("stream:a-held-stream-is-replaced-by-the-held-back-line-in-the-same-message",
          (st.replace("I did not pass that on."), rec.calls[1:], st.final_ts),
          (True, [("stop", "7.0"), ("update", "7.0", "I did not pass that on.")], "7.0"))

    rec = Recorder()
    st = stream(rec)
    st.feed("a secret SECRET first line\n")
    check("stream:a-secret-in-the-first-line-sends-nothing-at-all",
          (rec.calls, st.started, st.replace("held")), ([], False, False))

    rec = Recorder()
    st = stream(rec)
    st.feed("one\ntwo\n")
    check("stream:words-that-differ-from-what-was-streamed-are-made-exact-by-an-update",
          (st.settle("ONE\nTWO (the whole path's words)"), rec.calls[-2:]),
          (True, [("stop", "7.0"), ("update", "7.0", "ONE\nTWO (the whole path's words)")]))

    rec = Recorder(fail=("update",))
    st = stream(rec)
    st.feed("one\n")
    check("stream:when-slack-will-not-update-the-message-it-is-removed-and-the-caller-posts",
          (st.settle("different"), [c[0] for c in rec.calls], st.final_ts, st.why),
          (False, ["start", "stop", "update", "delete"], None,
           "Slack update refused: simulated"))

    rec = Recorder()
    st = stream(rec)
    st.feed("one\n")
    check("stream:a-turn-that-gave-no-answer-removes-what-was-streamed",
          ([st.discard(), [c[0] for c in rec.calls]][1]), ["start", "stop", "delete"])

    for name, fails, want_calls, want_why in (
            ("start", ("start",), ["start"], "Slack start refused: simulated"),
            ("append", ("append",), ["start", "append"], "Slack append refused: simulated")):
        rec = Recorder(fail=fails)
        st = stream(rec)
        st.feed("one\ntwo\nthree\n")
        check("stream:a-refused-%s-stops-the-stream-and-says-slacks-word" % name,
              ([c[0] for c in rec.calls], st.why, st.broken), (want_calls, want_why, True))
    rec = Recorder(fail=("start",))
    st = stream(rec)
    st.feed("one\n")
    check("stream:a-stream-slack-refused-to-open-leaves-nothing-to-settle-and-the-caller-posts",
          (st.settle("ONE"), [c[0] for c in rec.calls]), (False, ["start"]))
    rec = Recorder(fail=("stop",))
    st = stream(rec)
    st.feed("one\n")
    check("stream:a-stop-slack-refuses-is-said-and-the-message-is-still-made-exact",
          (st.settle("ONE"), st.why, st.final_ts), (True, "Slack stop refused: simulated", "7.0"))

    rec = Recorder()
    st = stream(rec, max_chars=12)
    st.feed("aaaa\nbbbb\ncccc\n")
    check("stream:a-line-past-the-length-the-answer-may-show-stops-the-stream",
          (st.lines, st.cut), (["AAAA", "BBBB"], True))
    rec = Recorder()
    st = stream(rec, read_max=9)
    st.feed("aaaa\nbbbb\ncccc\n")
    check("stream:text-past-the-read-cap-is-never-judged", (st.lines, st.cut),
          (["AAAA"], True))

    # a slow Slack never holds up the reader of the CLI's output (KIT-565 review)
    import time as _time

    class SlowRecorder(Recorder):
        def append_stream(self, channel, ts, text):
            _time.sleep(0.2)
            Recorder.append_stream(self, channel, ts, text)
    rec = SlowRecorder()
    st = AnswerStream(rec, "D1", "1.0", judge, ALICE, TEAM, 100, 400, threaded=True)
    t0 = _time.monotonic()
    for piece in ("one\n", "two\n", "three\n", "four\n"):
        st.feed(piece)
    fed_in = _time.monotonic() - t0
    check("stream:threaded-feeding-never-waits-on-slack",
          (fed_in < 0.15, len(rec.calls) < 5), (True, True))
    check("stream:threaded-the-ending-waits-for-the-worker-and-the-message-is-exact",
          (st.settle("ONE\nTWO\nTHREE\nFOUR"), st.lines, [c[0] for c in rec.calls][-2:]),
          (True, ["ONE", "TWO", "THREE", "FOUR"], ["stop", "update"]))
    rec = Recorder()
    st = AnswerStream(rec, "D1", "1.0", judge, ALICE, TEAM, 100, 400, threaded=True)
    st.feed("a\nb\n")
    st.flush()
    check("stream:threaded-flush-waits-until-what-was-fed-is-handled",
          [c[0] for c in rec.calls], ["start", "append"])
    st.discard()
    check("stream:threaded-discard-removes-what-was-streamed",
          [c[0] for c in rec.calls][-2:], ["stop", "delete"])
    rec = Recorder()
    st = AnswerStream(rec, "D1", "1.0", judge, ALICE, TEAM, 100, 400, threaded=True)
    st.feed("fine\nthe SECRET is here\nnever sent\n")
    check("stream:threaded-a-secret-stops-it-as-before",
          (st.replace("held") and st.held != "", [c[0] for c in rec.calls], rec.calls[-1]),
          (True, ["start", "stop", "update"], ("update", "7.0", "held")))

    calls_judged = []

    def boom(raw):
        calls_judged.append(raw)
        raise ValueError("a fault in the judge")
    rec = Recorder()
    st = stream(rec, judge=boom)
    st.feed("one\n")
    check("stream:a-fault-in-the-judge-never-raises-and-sends-nothing",
          (rec.calls, st.broken, st.why), ([], True, "the stream failed (ValueError)"))

    # 4. THE ERROR carries its exit code: the bridge's error code unless one is given.
    check("bridge-error-codes", (BridgeError("x").code, BridgeError("x", 2).code,
                                 str(BridgeError("said"))), (EXIT_ERROR, 2, "said"))

    # 5. WHAT THIS MODULE MAY CALL: no tracker at all, and Slack's reads and two writes.
    check("no-tracker-call-here",
          [w for w in ("api.linear.app", "graphql", "mutation", "issueUpdate", "issueCreate",
                       "commentCreate") if w in body], [])
    check("only-these-slack-methods",
          sorted(set(re.findall(r'["/]([a-z]+(?:\.[a-zA-Z]+)+)"', body))),
          ["apps.connections.open", "assistant.threads.setStatus", "auth.test",
           "chat.appendStream", "chat.delete", "chat.postMessage", "chat.startStream",
           "chat.stopStream", "chat.update", "conversations.history", "conversations.open",
           "conversations.replies", "users.info"])
    check("one-http-call", (body.count("_OPENER.open("), body.count("urlopen(")), (1, 0))

    if failures:
        print("FAIL: pipeline_bridge_transport selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_bridge_transport selftest (%d cases)" % cases[0])
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    print("usage: pipeline_bridge_transport.py --selftest (the bridge imports this module)")
    sys.exit(notify.EXIT_USAGE)
