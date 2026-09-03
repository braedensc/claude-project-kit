#!/usr/bin/env python3
"""GitHub operations that keep working when `gh` cannot verify TLS.

Three operations, because these are the three a session needs to ship work: open a
pull request, read its CI status, comment on it. Nothing else, and deliberately no
way to merge.

WHY THIS EXISTS
    Inside the Agent SDK's sandbox on macOS, `gh` fails every request with

        tls: failed to verify certificate: x509: OSStatus -26276

    and `gh auth status` mislabels it as "The token in GH_TOKEN is invalid." The token
    is fine. Go's darwin verifier calls SecTrustEvaluateWithError, which consults all
    three trust-settings domains; inside the sandbox the user and admin domains answer
    "No keychain is available" rather than the benign "No Trust Settings were found"
    they give outside, so the call returns an internal error. Measured on both sides —
    the two states share an exit code and mean opposite things.

    That is not fixable from configuration: the sandbox exposes filesystem and network
    controls only, with no way to permit a Mach service.

WHY PYTHON, AND NOT A SHELL SCRIPT AROUND curl
    Go on darwin ignores SSL_CERT_FILE and CURL_CA_BUNDLE (build-tag exclusion in
    root_unix.go) and asks the platform verifier instead. Python's ssl module honours
    SSL_CERT_FILE, which the dispatcher already sets in every session's environment. So
    this works inside the sandbox for the same reason curl does, by construction rather
    than by luck. If that variable is ever unset, this fails loudly rather than falling
    back to an unverified connection.

IT PREFERS gh, AND SAYS WHICH PATH IT TOOK
    Every invocation tries `gh` first and prints `path: gh` or `path: rest`. A silent
    substitution would hide two things worth knowing: the day `gh` starts working again,
    and the day this fallback quietly breaks.

IT CANNOT MERGE
    There is no merge operation and no code path that reaches that endpoint. ENDPOINTS
    below is the complete set of API paths this script will construct, and --selftest
    asserts it stays that way. Merging is the human's action; a tool the kit ships must
    not be able to do it, for the same reason the PreToolUse guard blocks it.

Usage:
    gh_fallback.py pr-create  --title T --body-file F --base B [--head H] [--repo O/R] [--draft]
    gh_fallback.py pr-checks  NUMBER [--repo O/R]
    gh_fallback.py pr-comment NUMBER --body-file F [--repo O/R]
    gh_fallback.py doctor     [--repo O/R]
    gh_fallback.py --selftest

Exit: 0 = ok, 1 = the operation failed, 2 = usage/IO/config error.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"

# The complete set of API paths this script will ever construct. --selftest asserts
# this mapping is exactly these three, so a merge endpoint cannot be added without a
# test failing and a human noticing.
ENDPOINTS = {
    "pr-create": ("POST", "/repos/{owner}/{repo}/pulls"),
    "pr-read": ("GET", "/repos/{owner}/{repo}/pulls/{number}"),
    "pr-checks": ("GET", "/repos/{owner}/{repo}/commits/{ref}/check-runs"),
    "pr-comment": ("POST", "/repos/{owner}/{repo}/issues/{number}/comments"),
}


class Failure(Exception):
    """An operation failed for a reason worth naming. Carries its own exit code."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def note(msg):
    """Progress and path reporting goes to stderr, so stdout stays parseable."""
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- repo


def parse_remote(url):
    """Derive (owner, repo) from a git remote URL.

    Handles the three forms a real checkout produces. An unparseable remote raises
    rather than guessing: a wrong owner/repo would open a PR somewhere unexpected,
    which is a worse outcome than stopping.
    """
    if not url:
        raise Failure("git remote 'origin' is empty or unset", 2)
    u = url.strip()
    u = re.sub(r"\.git$", "", u)
    m = re.match(r"^git@[^:]+:([^/]+)/(.+)$", u)          # git@github.invalid:owner/repo
    if not m:
        m = re.match(r"^ssh://git@[^/]+/([^/]+)/(.+)$", u)  # ssh://git@github.invalid/owner/repo
    if not m:
        m = re.match(r"^https?://[^/]+/([^/]+)/(.+)$", u)   # https://github.invalid/owner/repo
    if not m:
        raise Failure(f"could not parse owner/repo from git remote: {url!r}", 2)
    owner, repo = m.group(1), m.group(2).rstrip("/")
    if not owner or not repo or "/" in repo:
        raise Failure(f"could not parse owner/repo from git remote: {url!r}", 2)
    return owner, repo


def resolve_repo(explicit):
    if explicit:
        if explicit.count("/") != 1:
            raise Failure(f"--repo must be OWNER/REPO, got {explicit!r}", 2)
        owner, repo = explicit.split("/")
        if not owner or not repo:
            raise Failure(f"--repo must be OWNER/REPO, got {explicit!r}", 2)
        return owner, repo
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise Failure(f"could not run git to read the remote: {e}", 2)
    if out.returncode != 0:
        raise Failure(f"git remote get-url origin failed: {out.stderr.strip()}", 2)
    return parse_remote(out.stdout)


def current_branch():
    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise Failure("could not determine the current branch", 2)
    return out.stdout.strip()


# ---------------------------------------------------------------------------- gh


def try_gh(argv):
    """Run a gh command. Returns (ok, stdout, stderr).

    Never raises: a broken gh is the expected case here, not an error.
    """
    try:
        out = subprocess.run(
            ["gh"] + argv, capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        return False, "", "gh is not installed"
    except (OSError, subprocess.SubprocessError) as e:
        return False, "", f"gh could not be run: {e}"
    return out.returncode == 0, out.stdout, out.stderr.strip()


# --------------------------------------------------------------------------- rest


def token():
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(var)
        if val:
            return val
    raise Failure(
        "no GitHub token: set GH_TOKEN (or GITHUB_TOKEN). This is a distinct failure "
        "from a rejected token — nothing was sent.", 2
    )


def api(method, path, body=None):
    """One REST call. Raises Failure with the status and GitHub's own message.

    Proxy settings are taken from the environment by urllib, and the TLS context is
    Python's default, which honours SSL_CERT_FILE. Both are what make this work inside
    the sandbox; neither is overridden here.
    """
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "claude-project-kit-gh-fallback")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode()).get("message", "")
        except Exception:
            pass
        raise Failure(f"GitHub API {method} {path} -> HTTP {e.code}: {detail or e.reason}")
    except urllib.error.URLError as e:
        raise Failure(f"could not reach {url}: {e.reason}")


# ----------------------------------------------------------------------- commands


def read_body_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise Failure(f"could not read --body-file {path!r}: {e}", 2)


def cmd_pr_create(args):
    owner, repo = resolve_repo(args.repo)
    head = args.head or current_branch()
    body = read_body_file(args.body_file)

    gh_argv = ["pr", "create", "--title", args.title, "--body-file", args.body_file,
               "--base", args.base, "--head", head, "--repo", f"{owner}/{repo}"]
    if args.draft:
        gh_argv.append("--draft")
    ok, out, err = try_gh(gh_argv)
    if ok:
        note("path: gh")
        print(out.strip())
        return 0
    note(f"path: rest  (gh failed: {err.splitlines()[0] if err else 'no output'})")

    _, tmpl = ENDPOINTS["pr-create"]
    result = api("POST", tmpl.format(owner=owner, repo=repo), {
        "title": args.title, "body": body, "base": args.base,
        "head": head, "draft": bool(args.draft),
    })
    url = result.get("html_url")
    if not url:
        raise Failure("GitHub accepted the request but returned no PR url")
    print(url)
    return 0


def cmd_pr_checks(args):
    owner, repo = resolve_repo(args.repo)

    ok, out, err = try_gh(["pr", "checks", str(args.number), "--repo", f"{owner}/{repo}"])
    if ok:
        note("path: gh")
        print(out.rstrip())
        return 0
    note(f"path: rest  (gh failed: {err.splitlines()[0] if err else 'no output'})")

    _, pr_tmpl = ENDPOINTS["pr-read"]
    pr = api("GET", pr_tmpl.format(owner=owner, repo=repo, number=args.number))
    sha = (pr.get("head") or {}).get("sha")
    if not sha:
        raise Failure(f"PR #{args.number} has no head sha — cannot look up checks")

    _, chk_tmpl = ENDPOINTS["pr-checks"]
    data = api("GET", chk_tmpl.format(owner=owner, repo=repo, ref=sha))
    runs = data.get("check_runs") or []
    if not runs:
        # An empty list and a failed lookup are different states; say which this is.
        print("no check runs reported for this commit yet")
        return 0
    worst = 0
    for r in runs:
        status = r.get("status")
        concl = r.get("conclusion")
        state = concl or status or "unknown"
        print(f"{r.get('name','?')}\t{state}")
        if status != "completed":
            worst = max(worst, 1)
        elif concl not in ("success", "neutral", "skipped"):
            worst = max(worst, 1)
    return worst


def cmd_pr_comment(args):
    owner, repo = resolve_repo(args.repo)
    body = read_body_file(args.body_file)

    ok, out, err = try_gh(["pr", "comment", str(args.number),
                           "--body-file", args.body_file, "--repo", f"{owner}/{repo}"])
    if ok:
        note("path: gh")
        print(out.strip())
        return 0
    note(f"path: rest  (gh failed: {err.splitlines()[0] if err else 'no output'})")

    _, tmpl = ENDPOINTS["pr-comment"]
    result = api("POST", tmpl.format(owner=owner, repo=repo, number=args.number),
                 {"body": body})
    print(result.get("html_url", "comment posted"))
    return 0


def cmd_doctor(args):
    """Say which path this environment will take, and why — without changing anything."""
    owner, repo = resolve_repo(args.repo)
    print(f"repo: {owner}/{repo}")
    print(f"SSL_CERT_FILE: {os.environ.get('SSL_CERT_FILE') or '(unset)'}")
    print(f"HTTPS_PROXY:   {os.environ.get('HTTPS_PROXY') or '(unset)'}")
    print(f"token:         {'present' if os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN') else 'MISSING'}")
    ok, _, err = try_gh(["api", "rate_limit", "--jq", ".rate.limit"])
    if ok:
        print("gh:            works -> operations will take the gh path")
        return 0
    first = err.splitlines()[0] if err else "no output"
    print(f"gh:            FAILS ({first})")
    print("               -> operations will take the rest path")
    if "OSStatus" in err or "certificate" in err:
        print("               this is the known sandbox TLS failure (KIT-71); the token is fine")
    return 0


# ---------------------------------------------------------------------- selftest


def selftest():
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    # The endpoint set is the merge guard. If a merge path is ever added, this fails.
    check("endpoint names", sorted(ENDPOINTS),
          ["pr-checks", "pr-comment", "pr-create", "pr-read"])
    for name, (method, path) in ENDPOINTS.items():
        if path.rstrip("/").endswith("/merge"):
            failures.append(f"{name} targets a merge endpoint; this script must not merge")
        if method not in ("GET", "POST"):
            failures.append(f"{name} uses {method}; only GET and POST are expected")

    # Remote parsing, including the forms that must fail rather than guess.
    for url, want in [
        ("git@github.invalid:owner/repo.git", ("owner", "repo")),
        ("git@github.invalid:owner/repo", ("owner", "repo")),
        ("https://github.invalid/owner/repo.git", ("owner", "repo")),
        ("https://github.invalid/owner/repo", ("owner", "repo")),
        ("ssh://git@github.invalid/owner/repo.git", ("owner", "repo")),
        ("https://github.invalid/owner/repo/", ("owner", "repo")),
    ]:
        try:
            check(f"parse_remote({url})", parse_remote(url), want)
        except Failure as e:
            failures.append(f"parse_remote({url}) raised: {e}")
    for bad in ["", "not-a-url", "https://github.invalid/onlyowner", "git@github.invalid:"]:
        try:
            parse_remote(bad)
            failures.append(f"parse_remote({bad!r}) should have raised, did not")
        except Failure:
            pass

    # --repo validation
    for bad in ["owner", "a/b/c", "/repo", "owner/"]:
        try:
            resolve_repo(bad)
            failures.append(f"resolve_repo({bad!r}) should have raised, did not")
        except Failure:
            pass
    check("resolve_repo explicit", resolve_repo("o/r"), ("o", "r"))

    # URL construction
    _, t = ENDPOINTS["pr-create"]
    check("pr-create path", t.format(owner="o", repo="r"), "/repos/o/r/pulls")
    _, t = ENDPOINTS["pr-comment"]
    check("pr-comment path", t.format(owner="o", repo="r", number=7),
          "/repos/o/r/issues/7/comments")
    _, t = ENDPOINTS["pr-checks"]
    check("pr-checks path", t.format(owner="o", repo="r", ref="abc"),
          "/repos/o/r/commits/abc/check-runs")

    # A missing token must be its own named failure, distinct from a rejected one.
    saved = {k: os.environ.pop(k, None) for k in ("GH_TOKEN", "GITHUB_TOKEN")}
    try:
        token()
        failures.append("token() should have raised with no token set, did not")
    except Failure as e:
        if "no GitHub token" not in str(e):
            failures.append(f"token() raised the wrong message: {e}")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    # A missing body file must be a usage error, not a crash.
    try:
        read_body_file("/nonexistent/body/file")
        failures.append("read_body_file should have raised on a missing file")
    except Failure as e:
        if e.code != 2:
            failures.append(f"missing body file gave exit code {e.code}, want 2")

    if failures:
        print("gh_fallback selftest: FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"gh_fallback selftest: ok ({len(ENDPOINTS)} endpoints, none of them merge)")
    return 0


# -------------------------------------------------------------------------- main


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--selftest", action="store_true", help="run offline self-checks and exit")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("pr-create", help="open a pull request")
    c.add_argument("--title", required=True)
    c.add_argument("--body-file", required=True)
    c.add_argument("--base", required=True)
    c.add_argument("--head")
    c.add_argument("--repo")
    c.add_argument("--draft", action="store_true")
    c.set_defaults(func=cmd_pr_create)

    c = sub.add_parser("pr-checks", help="report CI status for a pull request")
    c.add_argument("number", type=int)
    c.add_argument("--repo")
    c.set_defaults(func=cmd_pr_checks)

    c = sub.add_parser("pr-comment", help="comment on a pull request")
    c.add_argument("number", type=int)
    c.add_argument("--body-file", required=True)
    c.add_argument("--repo")
    c.set_defaults(func=cmd_pr_comment)

    c = sub.add_parser("doctor", help="report which path this environment will take")
    c.add_argument("--repo")
    c.set_defaults(func=cmd_doctor)

    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if not getattr(args, "func", None):
        p.print_help()
        return 2
    try:
        return args.func(args)
    except Failure as e:
        print(f"gh_fallback: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
