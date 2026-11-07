#!/usr/bin/env python3
"""The watcher's one POST: a fixed, read-only query to the tracker (KIT-556).

    python3 scripts/pipeline_watcher_tracker.py --selftest

WHY THIS FILE EXISTS.  The watcher (scripts/pipeline_watcher_local.py) sends nothing but GETs, and its
own selftest pins that. The tracker answers only GraphQL, which is a POST, so the watcher could not read
ticket text first-hand. Reading it first-hand is the point of an independent watcher, so the owner chose
one narrow exception (6 Nov 2026, option (a) on the ticket that asked): this file. It is the only place
the watcher may send a POST, and what it sends cannot be changed by anything it reads.

THE RULES, each pinned by --selftest.
  * ONE host: the tracker's GraphQL address below. A request to anything else is refused before it is built.
  * ONE query, a constant. It has no variables, so no text from a ticket, a file or the environment is
    in it. It is a query: this file holds no mutation and no subscription, and the selftest fails if it
    ever does, or holds a second query.
  * The request body is that constant and nothing else.
  * A redirect is never followed: the key would go wherever the answer said.
  * A failure says so, in our own words (never the answer's), and is raised. The caller must treat it as
    "could not read", never as "nothing found".
  * The key is the caller's: read from the environment by the watcher, by the name its config gives, and
    handed in. It is never printed, logged or kept here.

WHAT IT READS.  The newest 100 tickets by when they were last updated, and the newest 100 comments by the
same order: their text, their author's id, and the ticket's identifier. One page of each. A caller that finds
the whole page inside its lookback knows older items were not read, and must say so.
"""
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request

TRACKER_URL = "https://api.linear.app/graphql"
USER_AGENT = "pipeline-watcher/1"
MAX_ANSWER_BYTES = 16 * 1024 * 1024
QUERY = """query WatcherTrackerText {
  issues(first: 100, orderBy: updatedAt) {
    nodes { identifier title description updatedAt creator { id } }
  }
  comments(first: 100, orderBy: updatedAt) {
    nodes { id body updatedAt user { id } issue { identifier } }
  }
}"""


class TrackerError(Exception):
    """The tracker could not be read. The words are ours, never the answer's."""


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirect)


def _post(url, headers, data, timeout):
    """(status, parsed JSON or None). The one place this file sends anything."""
    if url != TRACKER_URL:
        raise TrackerError("a host this file does not send to")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 (one fixed host)
            body = resp.read(MAX_ANSWER_BYTES + 1)
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError):
        raise TrackerError("could not reach the tracker")
    except (http.client.HTTPException, ValueError):
        # A half-read answer, a bad status line, a value the request refused: the tracker's glitch
        # alone, in our own words, so it cannot stop the watcher's other monitors.
        raise TrackerError("the tracker's answer could not be read")
    if len(body) > MAX_ANSWER_BYTES:
        raise TrackerError("the tracker's answer was larger than %d bytes, so it was not read"
                           % MAX_ANSWER_BYTES)
    try:
        return status, json.loads(body.decode("utf-8"))
    except ValueError:
        return status, None


def read(token, post=None, timeout=30):
    """(issues, comments), each a list of dicts, from the one fixed query. Raises TrackerError."""
    post = post or _post
    body = json.dumps({"query": QUERY}).encode("utf-8")
    status, answer = post(TRACKER_URL, {"Authorization": token, "Content-Type": "application/json",
                                        "User-Agent": USER_AGENT}, body, timeout)
    if status != 200:
        raise TrackerError("the tracker answered HTTP %s" % status)
    if not isinstance(answer, dict):
        raise TrackerError("the tracker's answer was not JSON")
    if answer.get("errors"):
        raise TrackerError("the tracker answered with an error")
    data = answer.get("data")
    if not isinstance(data, dict):
        raise TrackerError("the tracker's answer held no data")
    out = []
    for name in ("issues", "comments"):
        conn = data.get(name)
        nodes = conn.get("nodes") if isinstance(conn, dict) else None
        if not isinstance(nodes, list):
            raise TrackerError("the tracker's answer held no list of %s" % name)
        out.append([n for n in nodes if isinstance(n, dict)])
    return out[0], out[1]


# SELFTEST: nothing below this line is read by the checks that scan the code above it.
def selftest():
    import http.server
    import threading
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    TOKEN = "lin_api_" + "k" * 32

    def fake(status=200, answer=None, boom=None):
        sent = []

        def post(url, headers, data, timeout):
            sent.append((url, dict(headers), data))
            if boom:
                raise boom
            return status, answer
        post.sent = sent
        return post

    good = {"data": {"issues": {"nodes": [{"identifier": "KIT-1", "title": "t", "description": "d",
                                           "updatedAt": "2026-11-05T15:00:00Z", "creator": {"id": "u1"}}]},
                     "comments": {"nodes": [{"id": "c1", "body": "b", "updatedAt": "2026-11-05T15:00:00Z",
                                             "user": {"id": "u1"}, "issue": {"identifier": "KIT-1"}}, 7]}}}
    p = fake(200, good)
    issues, comments = read(TOKEN, p)
    check("a good answer is read: issues and comments, non-dicts dropped",
          (len(issues), len(comments), issues[0]["identifier"]), (1, 1, "KIT-1"))
    url, headers, data = p.sent[0]
    check("it sends to the one host, with the key in the header only",
          (url, headers["Authorization"], TOKEN.encode() in data), (TRACKER_URL, TOKEN, False))
    check("the body is the constant query and nothing else", json.loads(data.decode("utf-8")), {"query": QUERY})

    def why(post):
        try:
            read(TOKEN, post)
            return ""
        except TrackerError as exc:
            return str(exc)

    for label, post, needle in (
            ("an HTTP error", fake(500, None), "HTTP 500"),
            ("a rate limit", fake(429, None), "HTTP 429"),
            ("an answer that is not JSON", fake(200, None), "not JSON"),
            ("a GraphQL error", fake(200, {"errors": [{"message": "SECRET-ISH TEXT " + TOKEN}]}), "with an error"),
            ("no data", fake(200, {"data": None}), "no data"),
            ("no list", fake(200, {"data": {"issues": {"nodes": 1}, "comments": {"nodes": []}}}), "no list of issues"),
            ("an unreachable tracker", fake(boom=TrackerError("could not reach the tracker")), "could not reach")):
        got = why(post)
        check("%s is raised, in our words" % label, needle in got, True)
        check("...and the answer's words and the key are in none of them", TOKEN in got or "SECRET-ISH" in got, False)
    # a half-read or malformed answer is the tracker's glitch alone, raised as ours (Stage E)
    class Broken(object):
        def __init__(self, exc):
            self.exc = exc

        def open(self, *_a, **_k):
            raise self.exc

    global _OPENER
    real_opener = _OPENER
    try:
        for label, exc in (("a half-read answer", http.client.IncompleteRead(b"x")),
                           ("a bad status line", http.client.BadStatusLine("x")),
                           ("a value it refused", ValueError("bad header value " + TOKEN))):
            _OPENER = Broken(exc)
            got = why_post(TRACKER_URL)
            check("%s is a TrackerError in our words, with no key in it" % label,
                  (bool(got), TOKEN in got, "bad header" in got), (True, False, False))
    finally:
        _OPENER = real_opener
    check("_post refuses any other host before it builds a request",
          why_post("https://example.com/graphql"), "a host this file does not send to")

    # a redirect is never followed
    landed = []

    class Target(http.server.BaseHTTPRequestHandler):
        def _hit(self):
            landed.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        do_GET = do_POST = _hit

        def log_message(self, *_a):
            pass

    target = http.server.HTTPServer(("127.0.0.1", 0), Target)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(int(self.path.strip("/")))
            self.send_header("Location", "http://127.0.0.1:%d/landed" % target.server_port)
            self.end_headers()

        def log_message(self, *_a):
            pass

    redirector = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=x.serve_forever, daemon=True) for x in (target, redirector)]
    for t in threads:
        t.start()
    try:
        for code in (301, 302, 303, 307, 308):
            req = urllib.request.Request("http://127.0.0.1:%d/%d" % (redirector.server_port, code),
                                         data=b"{}", method="POST", headers={"Authorization": TOKEN})
            try:
                _OPENER.open(req, timeout=5)
                got = None
            except urllib.error.HTTPError as exc:
                got = exc.code
            check("a %d is not followed" % code, got, code)
    finally:
        for x in (target, redirector):
            x.shutdown()
            x.server_close()
    check("...and the key reached nobody else", landed, [])

    # what the file may hold
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    check("the code above the marker was found", len(above) > 500, True)
    check("it holds no mutation and no subscription, anywhere above the selftest",
          re.findall(r"\b(?:mutation|subscription)\b", above, re.I), [])
    check("it holds exactly one query definition", len(re.findall(r"\bquery\s+\w+\s*[{(]", above)), 1)
    check("the query is a constant with no variables and no interpolation",
          ("$" in QUERY, "%" in QUERY, "{}" in QUERY, "{{" in QUERY), (False, False, False, False))
    check("it sends exactly one POST, built in one place", above.count('method="POST"'), 1)
    check("it names exactly one host", sorted(set(re.findall(r"https?://[A-Za-z0-9.\-]+", above))),
          ["https://api.linear.app"])
    check("the body is built in one place, from the constant only",
          (above.count("json.dumps("), "json.dumps({\"query\": QUERY})" in above), (1, True))
    check("it starts no process and opens no socket by hand",
          [b for b in ("subprocess", "os.system", "socket", "shutil", "eval(", "exec(", "environ", "getenv")
           if b in above], [])
    check("a fixed query reads only fields, no field named like a write",
          re.findall(r"\b(?:create|delete|archive)\w*|\bupdate(?!dAt\b)\w*", QUERY, re.I), [])
    if failures:
        for line in failures:
            print(line)
        print("watcher tracker selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: watcher tracker selftest (%d checks)" % total[0])
    return 0


def why_post(url):
    try:
        _post(url, {}, b"", 1)
        return ""
    except TrackerError as exc:
        return str(exc)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from scratch_run import contained
        return contained(selftest, "watcher-tracker-selftest-run-")
    print("usage: pipeline_watcher_tracker.py --selftest  (the watcher imports read())")
    return 2


if __name__ == "__main__":
    sys.exit(main())
