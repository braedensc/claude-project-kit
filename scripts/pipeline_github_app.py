#!/usr/bin/env python3
"""A GitHub App of the kit's own: made by GitHub's manifest flow, its key kept in one file, and the
short-lived token it acts with signed from that file (KIT-578). One helper, so Stage E's app and the
validator's app are made the same way.

    from pipeline_github_app import (manifest, ManifestFlow, convert, make_jwt, openssl_signer,
                                     app_info, installation_for, installation_token, bot_login,
                                     post_comment, read_comment, write_key_argv)

    python3 scripts/pipeline_github_app.py --selftest

HOW AN APP IS MADE (GitHub's manifest flow, the one way a script may make an app).
  1. ManifestFlow serves a page on 127.0.0.1 whose form posts the manifest to github.com. The owner
     opens it and clicks GitHub's own "Create" button: that click is GitHub's rule, not ours.
  2. GitHub sends the browser back to 127.0.0.1 with a one-time code and the state this run made.
  3. convert() swaps the code for the app's id, slug and private key. The key goes straight to its
     file, on stdin, as the account that will use it: write_key_argv().
  4. The owner installs the app on the repositories (GitHub's page again); installation_for() sees it.
  5. Later, as the key's owner: make_jwt() with openssl_signer(), then installation_token(), then post
     as the app's bot login, bot_login(slug).

THE RULES, each one tested.
  * One host each way. API calls go to https://api.github.com only, every path is in ENDPOINTS, and a
    redirect is refused, so a key or token is never sent anywhere else. Nothing in ENDPOINTS can merge,
    approve, label or change a setting.
  * A credential is never printed, logged, put in an argument list, or kept past the call that uses
    it. The key is written to its file from stdin; an error never carries a token.
  * The callback takes a code only with this run's state, on 127.0.0.1, and only once.
  * The signed token is RS256, signed by `openssl` reading the key file itself, as the account that
    owns it. It lives ten minutes at most.
"""
import argparse
import base64
import hmac
import html
import json
import re
import secrets
import sys
import threading
import time

API = "https://api.github.com"
WEB = "https://github.com"
# Every API path this file builds. --selftest asserts it is exactly this set.
ENDPOINTS = {
    "convert": ("POST", "/app-manifests/{code}/conversions"),
    "app": ("GET", "/app"),
    "repo-installation": ("GET", "/repos/{owner}/{repo}/installation"),
    "token": ("POST", "/app/installations/{installation_id}/access_tokens"),
    "comment-create": ("POST", "/repos/{owner}/{repo}/issues/{number}/comments"),
    "comment-read": ("GET", "/repos/{owner}/{repo}/issues/comments/{comment_id}"),
}
CODE_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
ORG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
JWT_SECONDS = 540
TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]{20,400}")


class GitHubAppError(Exception):
    """A call this helper made did not succeed. The message never holds a key or a token."""


def bot_login(slug):
    """The login an app's comments are posted under."""
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise GitHubAppError("the app's slug is not one GitHub makes")
    return slug + "[bot]"


def manifest(name, homepage, permissions, description=""):
    """The manifest GitHub makes the app from. No webhook, private, only the given permissions.
    `redirect_url` is set by ManifestFlow, which knows its own address."""
    if not isinstance(name, str) or not 1 <= len(name) <= 34:
        raise GitHubAppError("an app's name is 1 to 34 characters")
    if not isinstance(permissions, dict) or not all(
            isinstance(k, str) and v in ("read", "write") for k, v in permissions.items()):
        raise GitHubAppError("permissions are a name to read or write")
    return {"name": name, "url": homepage, "public": False, "description": description,
            "default_permissions": dict(permissions), "default_events": []}


# --------------------------------------------------------------------------- the manifest flow
class ManifestFlow(object):
    """A page on 127.0.0.1 that posts `doc` to github.com, and the callback that takes the code.
    `org` makes the app under an organization; without it, under the signed-in user."""

    def __init__(self, doc, org=None):
        import http.server
        if org is not None and not ORG_RE.fullmatch(org or ""):
            raise GitHubAppError("the organization name is not one GitHub allows")
        self.state = secrets.token_urlsafe(24)
        self.code = None
        self._got = threading.Event()
        self._org = org
        self._doc = dict(doc)
        flow = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                status, page = flow._answer(self.path)
                data = page.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = "http://127.0.0.1:%d/" % self.port
        self._doc["redirect_url"] = "http://127.0.0.1:%d/callback" % self.port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _answer(self, path):
        from urllib.parse import parse_qs, urlsplit
        parts = urlsplit(path)
        if parts.path == "/":
            where = ("/organizations/%s/settings/apps/new" % self._org if self._org
                     else "/settings/apps/new")
            action = "%s%s?state=%s" % (WEB, where, self.state)
            return 200, (
                "<!doctype html><meta charset=utf-8><title>Create the app</title>"
                "<p>Sending you to GitHub to create the app. GitHub asks you to confirm.</p>"
                "<form id=f method=post action=\"%s\">"
                "<input type=hidden name=manifest value=\"%s\">"
                "<button type=submit>Continue to GitHub</button></form>"
                "<script>document.getElementById('f').submit()</script>"
                % (html.escape(action, quote=True), html.escape(json.dumps(self._doc), quote=True)))
        if parts.path == "/callback" and self.code is None:
            q = parse_qs(parts.query)
            code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
            if CODE_RE.fullmatch(code) and hmac.compare_digest(state, self.state):
                self.code = code
                self._got.set()
                return 200, ("<!doctype html><meta charset=utf-8><title>Done</title>"
                             "<p>GitHub made the app. Go back to your terminal.</p>")
        return 400, "<!doctype html><meta charset=utf-8><title>No</title><p>Not this.</p>"

    def wait(self, seconds):
        """The code, or None when none came in time."""
        self._got.wait(seconds)
        return self.code

    def close(self):
        self._server.shutdown()
        self._server.server_close()


# --------------------------------------------------------------------------- the API, one host
class _NoRedirect(object):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GitHubAppError("GitHub answered with a redirect (HTTP %d); not followed" % code)


def _call(method, path, auth=None, body=None):
    """(status, document) for one call to api.github.com. `auth` is the full Authorization value
    and is never in an error. A transport failure raises."""
    import urllib.error
    import urllib.request
    if not path.startswith("/") or "//" in path or ".." in path:
        raise GitHubAppError("not an API path")
    handler = type("_NR", (urllib.request.HTTPRedirectHandler,),
                   {"redirect_request": _NoRedirect.redirect_request})()
    opener = urllib.request.build_opener(handler)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "claude-project-kit-github-app")
    if auth:
        req.add_header("Authorization", auth)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=30) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read() or b"", exc.code
    except (urllib.error.URLError, OSError) as exc:
        raise GitHubAppError("GitHub could not be reached (%s)" % type(exc).__name__)
    try:
        doc = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        doc = {}
    return status, doc


def _path(name, **parts):
    method, template = ENDPOINTS[name]
    for key, value in parts.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", str(value)) or ".." in str(value):
            raise GitHubAppError("%s is not a plain name or number" % key)
    return method, template.format(**parts)


def convert(code, call=_call):
    """{"id", "slug", "owner", "pem", ...} for the code GitHub sent back. The pem must go straight
    to its file; nothing here prints or keeps it."""
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        raise GitHubAppError("the code is not one GitHub sends")
    method, path = _path("convert", code=code)
    status, doc = call(method, path)
    if status != 201 or not isinstance(doc, dict):
        raise GitHubAppError("GitHub would not make the app from the code (HTTP %s)" % status)
    if (not isinstance(doc.get("id"), int) or not SLUG_RE.fullmatch(str(doc.get("slug") or ""))
            or not str(doc.get("pem") or "").startswith("-----BEGIN ")):
        raise GitHubAppError("GitHub's answer did not carry an app id, slug and key")
    return doc


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_jwt(app_id, sign, now=None):
    """The app's own token, RS256, for at most JWT_SECONDS. `sign(bytes) -> bytes` holds the key."""
    if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id < 1:
        raise GitHubAppError("the app id is not a positive whole number")
    now = int(time.time()) if now is None else int(now)
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({"iat": now - 60, "exp": now + JWT_SECONDS, "iss": str(app_id)},
                           separators=(",", ":")).encode())
    signing_input = "%s.%s" % (head, body)
    signature = sign(signing_input.encode("ascii"))
    if not isinstance(signature, bytes) or len(signature) < 128:
        raise GitHubAppError("the key did not sign the token")
    return "%s.%s" % (signing_input, _b64(signature))


def openssl_signer(key_file, account=None, run=None):
    """`sign(bytes) -> bytes` by `openssl dgst -sha256 -sign key_file`. With `account`, it runs as
    that account through `sudo -n`, so the key is read only by the account that owns it."""
    import subprocess
    run = run or subprocess.run
    argv = ["openssl", "dgst", "-sha256", "-sign", key_file]
    if account:
        argv = ["sudo", "-n", "-u", account, "--"] + argv

    def sign(data):
        try:
            out = run(argv, input=data, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise GitHubAppError("openssl could not be run to sign the app's token")
        if out.returncode != 0 or not out.stdout:
            raise GitHubAppError("openssl could not sign with the app's key file (exit %d)"
                                 % out.returncode)
        return out.stdout
    return sign


def app_info(jwt, call=_call):
    """{"id", "slug"} of the app the token is for: what GitHub itself says it is."""
    method, path = _path("app")
    status, doc = call(method, path, auth="Bearer " + jwt)
    if status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("id"), int):
        raise GitHubAppError("GitHub would not say which app this is (HTTP %s)" % status)
    return {"id": doc["id"], "slug": doc.get("slug")}


def installation_for(owner_repo, jwt, call=_call):
    """The app's installation id on `owner_repo`, or None when it is not installed there."""
    if not isinstance(owner_repo, str) or not REPO_RE.fullmatch(owner_repo):
        raise GitHubAppError("not OWNER/NAME")
    owner, repo = owner_repo.split("/", 1)
    method, path = _path("repo-installation", owner=owner, repo=repo)
    status, doc = call(method, path, auth="Bearer " + jwt)
    if status == 404:
        return None
    if status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("id"), int):
        raise GitHubAppError("GitHub would not say where the app is installed (HTTP %s)" % status)
    return doc["id"]


def installation_token(installation_id, jwt, call=_call):
    """A token that acts as the app on its installation, for an hour. Never printed."""
    method, path = _path("token", installation_id=installation_id)
    status, doc = call(method, path, auth="Bearer " + jwt, body={})
    token = doc.get("token") if isinstance(doc, dict) else None
    if status != 201 or not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise GitHubAppError("GitHub would not give the app a token (HTTP %s)" % status)
    return token


def post_comment(owner_repo, number, body, token, call=_call):
    """{"id", "author", "type"} of a comment posted as the app on issue or pull request `number`."""
    owner, repo = owner_repo.split("/", 1)
    method, path = _path("comment-create", owner=owner, repo=repo, number=int(number))
    status, doc = call(method, path, auth="token " + token, body={"body": body})
    if status != 201 or not isinstance(doc, dict) or not isinstance(doc.get("id"), int):
        raise GitHubAppError("GitHub would not take the app's comment (HTTP %s)" % status)
    user = doc.get("user") or {}
    return {"id": doc["id"], "author": user.get("login"), "type": user.get("type")}


def read_comment(owner_repo, comment_id, token=None, call=_call):
    """{"id", "author", "type"} of one comment, read back from GitHub."""
    owner, repo = owner_repo.split("/", 1)
    method, path = _path("comment-read", owner=owner, repo=repo, comment_id=int(comment_id))
    status, doc = call(method, path, auth=("token " + token) if token else None)
    if status != 200 or not isinstance(doc, dict):
        raise GitHubAppError("GitHub would not show the comment (HTTP %s)" % status)
    user = doc.get("user") or {}
    return {"id": doc.get("id"), "author": user.get("login"), "type": user.get("type")}


def write_key_argv(account, path):
    """The command that writes a key, given on stdin, to `path` as `account`: mode 600 from the
    first byte (umask 077), through a temporary file and one rename. The key is never an argument."""
    if not isinstance(path, str) or not path.startswith("/") or "'" in path or "\n" in path:
        raise GitHubAppError("the key file must be an absolute path")
    return ["sudo", "-n", "-u", account, "--", "/bin/sh", "-c",
            'umask 077 && cat > "$1.tmp" && chmod 600 "$1.tmp" && mv "$1.tmp" "$1"', "sh", path]


def main(argv=None):
    ap = argparse.ArgumentParser(description="The kit's GitHub App helper.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from scratch_run import contained
        return contained(selftest, "github-app-selftest-")
    ap.print_help()
    return 2


# ======================================================================================
# SELFTEST: nothing below the line above is run by a reader, and the source scan stops there.
# ======================================================================================
def selftest():
    import os
    import subprocess
    import tempfile
    import urllib.request
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    def raises(fn):
        try:
            fn()
            return False
        except GitHubAppError:
            return True

    # 1. Only these paths, and none can merge, approve, label or change a setting.
    check("the paths it can build", sorted(ENDPOINTS),
          ["app", "comment-create", "comment-read", "convert", "repo-installation", "token"])
    words = " ".join(p for _m, p in ENDPOINTS.values())
    check("no path merges, approves, labels or changes a setting",
          [w for w in ("merge", "review", "label", "protection", "rules", "settings", "hooks")
           if w in words], [])
    check("a path piece that is not a plain name is refused",
          (raises(lambda: _path("repo-installation", owner="a..b", repo="x")),
           raises(lambda: _path("repo-installation", owner="a/b", repo="x")),
           raises(lambda: _call("GET", "//evil.example/x"))), (True, True, True))

    # 2. The manifest, and the flow that posts it and takes the code back.
    doc = manifest("Stage E reviews", "https://github.com/acme/kit", {"pull_requests": "write"})
    check("a manifest asks only for what it names, with no webhook",
          (doc["default_permissions"], doc["public"], "hook_attributes" in doc,
           doc["default_events"]), ({"pull_requests": "write"}, False, False, []))
    check("a bad manifest is refused",
          (raises(lambda: manifest("x" * 35, "u", {})),
           raises(lambda: manifest("ok", "u", {"contents": "admin"}))), (True, True))
    flow = ManifestFlow(doc)
    try:
        page = urllib.request.urlopen(flow.url, timeout=10).read().decode()
        check("the page posts the manifest to github.com with this run's state",
              ('action="https://github.com/settings/apps/new?state=%s"' % flow.state in page,
               html.escape(json.dumps(dict(doc, redirect_url="http://127.0.0.1:%d/callback"
                                           % flow.port)), quote=True) in page), (True, True))

        def get(path):
            try:
                return urllib.request.urlopen(flow.url.rstrip("/") + path, timeout=10).status
            except Exception as exc:   # noqa: BLE001 - an HTTPError carries the status
                return getattr(exc, "code", None)
        check("a callback with another state is refused, and takes nothing",
              (get("/callback?code=abc&state=wrong"), flow.code), (400, None))
        check("a code that is not one GitHub sends is refused",
              get("/callback?code=a%20b&state=" + flow.state), 400)
        check("the callback with this run's state takes the code",
              (get("/callback?code=abc_123&state=" + flow.state), flow.wait(1)), (200, "abc_123"))
        check("...once: a second callback is refused, and the first code stands",
              (get("/callback?code=other&state=" + flow.state), flow.code), (400, "abc_123"))
    finally:
        flow.close()
    org_flow = ManifestFlow(doc, org="acme-org")
    try:
        page = urllib.request.urlopen(org_flow.url, timeout=10).read().decode()
        check("for an organization, the form posts to its settings",
              "https://github.com/organizations/acme-org/settings/apps/new?state=" in page, True)
    finally:
        org_flow.close()
    check("an organization name GitHub would not allow is refused",
          raises(lambda: ManifestFlow(doc, org="bad org")), True)

    # 3. The API, stubbed: what each call sends, and what it takes back.
    sent = []

    def fake(answers):
        def call(method, path, auth=None, body=None):
            sent.append((method, path, auth, body))
            return answers.get((method, path), (500, {}))
        return call
    # Shaped like GitHub's answer only as far as convert() looks: no key material in this file.
    KEYISH = "-----BEGIN TEST FIXTURE-----\nnot a key\n-----END TEST FIXTURE-----\n"
    conv = fake({("POST", "/app-manifests/abc_123/conversions"):
                 (201, {"id": 42, "slug": "acme-stage-e", "pem": KEYISH, "owner": {"login": "acme"}})})
    got = convert("abc_123", conv)
    check("convert: the app's id, slug and key, asked with no credential",
          (got["id"], got["slug"], got["pem"] == KEYISH, sent[-1][2]),
          (42, "acme-stage-e", True, None))
    check("convert: a code that is not one GitHub sends is refused before any call",
          (raises(lambda: convert("a b", conv)), len(sent)), (True, 1))
    check("convert: an answer without a key is refused",
          raises(lambda: convert("abc_123", fake({("POST", "/app-manifests/abc_123/conversions"):
                                                  (201, {"id": 42, "slug": "x"})}))), True)
    check("the bot login is the slug's",
          (bot_login("acme-stage-e"), raises(lambda: bot_login("Bad Slug"))),
          ("acme-stage-e[bot]", True))
    api = fake({("GET", "/app"): (200, {"id": 42, "slug": "acme-stage-e"}),
                ("GET", "/repos/acme/app/installation"): (200, {"id": 7}),
                ("GET", "/repos/acme/other/installation"): (404, {}),
                ("POST", "/app/installations/7/access_tokens"): (201, {"token": "ghs_" + "t" * 36}),
                ("POST", "/repos/acme/app/issues/12/comments"):
                    (201, {"id": 99, "user": {"login": "acme-stage-e[bot]", "type": "Bot"}}),
                ("GET", "/repos/acme/app/issues/comments/99"):
                    (200, {"id": 99, "user": {"login": "acme-stage-e[bot]", "type": "Bot"}})})
    del sent[:]
    check("app_info: what GitHub says the app is", app_info("J", api),
          {"id": 42, "slug": "acme-stage-e"})
    check("installation_for: installed, and not", (installation_for("acme/app", "J", api),
                                                    installation_for("acme/other", "J", api)),
          (7, None))
    tok = installation_token(7, "J", api)
    check("installation_token: a token, asked with the app's own token",
          (tok.startswith("ghs_"), sent[-1][2]), (True, "Bearer J"))
    posted = post_comment("acme/app", 12, "Stage E identity check.", tok, api)
    check("post_comment: the comment's author is the app's bot",
          (posted, sent[-1][2].startswith("token ")),
          ({"id": 99, "author": "acme-stage-e[bot]", "type": "Bot"}, True))
    check("read_comment: read back", read_comment("acme/app", 99, None, api)["author"],
          "acme-stage-e[bot]")
    for label, fn in (("an app that will not say what it is", lambda: app_info("J", fake({}))),
                      ("a token GitHub will not give", lambda: installation_token(7, "J", fake({}))),
                      ("an installation GitHub will not show",
                       lambda: installation_for("acme/app", "J", fake({})))):
        check("a failure is said, never a quiet None: %s" % label, raises(fn), True)

    # 3b. The real call follows no redirect: a key or token never goes to another host.
    import http.server
    hops = []

    class Redirect(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            hops.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/elsewhere" % self.server.server_address[1])
            self.end_headers()
    server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real_api = globals()["API"]
    globals()["API"] = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        check("a redirect is refused, not followed, and the token goes nowhere else",
              (raises(lambda: _call("GET", "/app", auth="Bearer J")), hops), (True, ["/app"]))
    finally:
        globals()["API"] = real_api
        server.shutdown()
        server.server_close()

    # 4. The signed token, by a real openssl over a throwaway key made here and never kept.
    with tempfile.TemporaryDirectory() as tmp:
        key, pub = os.path.join(tmp, "app.pem"), os.path.join(tmp, "app.pub")
        made = subprocess.run(["openssl", "genrsa", "-out", key, "2048"], capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", key, "-pubout", "-out", pub], capture_output=True)
        if made.returncode != 0:
            failures.append("FAIL openssl could not make a throwaway key, so signing was not tested")
        else:
            token = make_jwt(42, openssl_signer(key), now=1793890800)
            head, body, sig = token.split(".")
            pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
            claims = json.loads(base64.urlsafe_b64decode(pad(body)))
            check("the token's claims: the app, and ten minutes at most",
                  (json.loads(base64.urlsafe_b64decode(pad(head))), claims),
                  ({"alg": "RS256", "typ": "JWT"},
                   {"iat": 1793890740, "exp": 1793891340, "iss": "42"}))
            sig_file, data_file = os.path.join(tmp, "sig"), os.path.join(tmp, "data")
            with open(sig_file, "wb") as fh:
                fh.write(base64.urlsafe_b64decode(pad(sig)))
            with open(data_file, "wb") as fh:
                fh.write(("%s.%s" % (head, body)).encode())
            ok = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature",
                                 sig_file, data_file], capture_output=True)
            check("openssl verifies the token's signature with the app's public key",
                  ok.returncode, 0)
            check("a missing key file is said, not a token",
                  raises(lambda: make_jwt(42, openssl_signer(os.path.join(tmp, "none.pem")))), True)
    seen = []
    openssl_signer("/k.pem", account="_stagee", run=lambda argv, **kw: (
        seen.append(argv), type("R", (), {"returncode": 0, "stdout": b"s" * 256})())[1])(b"x")
    check("as the key's own account, through sudo -n, with the key file named and never read here",
          seen[0], ["sudo", "-n", "-u", "_stagee", "--", "openssl", "dgst", "-sha256", "-sign",
                    "/k.pem"])
    check("an app id that is not a positive whole number is refused",
          (raises(lambda: make_jwt(0, None)), raises(lambda: make_jwt(True, None))), (True, True))

    # 5. The key file: written from stdin, as its account, mode 600 from the first byte.
    argv = write_key_argv("_stagee", "/Users/<role-account>/.stage-e/app.pem")
    check("the key is written as its account, from stdin, never as an argument",
          (argv[:5], "umask 077" in argv[7], argv[-1], any("BEGIN" in a for a in argv)),
          (["sudo", "-n", "-u", "_stagee", "--"], True, "/Users/<role-account>/.stage-e/app.pem", False))
    check("a relative key path is refused", raises(lambda: write_key_argv("_s", "app.pem")), True)

    # 6. Nothing above prints, logs or writes a file of its own.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    body_above = above.replace("self.wfile.write(data)", "")
    check("it prints nothing, and keeps no key or token in a file of its own",
          ([w for w in ("print(", "logging", ".write(", "os.system", "shell=True")
            if w in body_above], re.findall(r"(?<![.\w])open\(", body_above)), ([], []))

    if failures:
        for line in failures:
            print(line)
        print("github app selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: github app selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
