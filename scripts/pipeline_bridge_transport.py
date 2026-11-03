#!/usr/bin/env python3
"""The Slack bridge's transport: its error, its one HTTP call and its Slack client.

    python3 scripts/pipeline_bridge_transport.py --selftest

WHY A MODULE OF ITS OWN (KIT-312). scripts/pipeline_bridge.py is one file of more than
12,000 lines, so Stage E withholds it from every review it is in, and every bridge feature
conflicts with every other. It is split by concern, one module a pull request, with no
change in behaviour, and each module keeps its own tests. This is the first part (KIT-388):
the Slack side of the bridge's transports. The tracker client follows.

WHAT IS HERE
  BridgeError   the bridge's one error, carrying the exit code a pass ends on.
  _http_json    the one HTTP call: JSON in and out, and a redirect refused, so the
                Authorization header never goes where a redirect points.
  SlackClient   the bridge app's side of Slack. Every `ok: false` is raised, never read as
                empty: a token that cannot see the channel must not look like a channel
                where nobody asked.

pipeline_bridge imports all three, so `pipeline_bridge.BridgeError` and
`pipeline_bridge.SlackClient` are these very objects. Its tracker client calls the bridge's
own name for `_http_json`, which is this same function: a test standing in for the
tracker's HTTP replaces it there, and one standing in for Slack's replaces it here.

It makes no tracker call. Its only Slack writes are a message (`chat.postMessage`) and the
opening of a member's DM with the app (`conversations.open`); the selftest pins both.
"""
import json
import os
import sys
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


def _http_json(url, headers, payload=None, timeout=20):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
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

        def stub(url, headers, payload=None, timeout=20):
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

    # 4. THE ERROR carries its exit code: the bridge's error code unless one is given.
    check("bridge-error-codes", (BridgeError("x").code, BridgeError("x", 2).code,
                                 str(BridgeError("said"))), (EXIT_ERROR, 2, "said"))

    # 5. WHAT THIS MODULE MAY CALL: no tracker at all, and Slack's reads and two writes.
    check("no-tracker-call-here",
          [w for w in ("api.linear.app", "graphql", "mutation", "issueUpdate", "issueCreate",
                       "commentCreate") if w in body], [])
    check("only-these-slack-methods",
          sorted(set(re.findall(r'["/]([a-z]+\.[a-zA-Z]+)"', body))),
          ["auth.test", "chat.postMessage", "conversations.history", "conversations.open",
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
