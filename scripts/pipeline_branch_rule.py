#!/usr/bin/env python3
"""Main's branch rule also requires a pull request: the owner's step (KIT-911).

    python3 scripts/pipeline_branch_rule.py check --repo OWNER/NAME   # read only: ON, OFF or UNKNOWN
    python3 scripts/pipeline_branch_rule.py run --repo OWNER/NAME     # check, show, one typed yes, change, read back
    python3 scripts/pipeline_branch_rule.py --selftest

`--branch NAME` reads another branch. Without it, the repository's default branch is read.

WHY. Main's rule decides WHAT may merge: its required checks must be green. It did not decide HOW
a commit reaches main. Without "Require a pull request before merging", any token that can push,
a session's included, could push a commit with green checks straight to main, and no pull request
would ever show it. This turns that setting on, with no approving review needed, so a pull
request you open yourself still merges. docs/SECURITY.md says which layer stops what.

WHAT IT CHANGES. One setting of one rule. The required checks (each with the app it must come
from), admin enforcement and every other setting go back exactly as GitHub shows them. It reads
the rule back after the change: NOT DONE if the pull request setting is not on, or anything else
moved. It never creates a rule, never edits a ruleset, and never changes who may merge (that is
KIT-37: a session stops holding your token). To undo it: the repository's Settings, Branches, the
branch's rule, and untick "Require a pull request before merging".

BEFORE IT ASKS, `run` lists the branch's commits from the last 30 days that no pull request
brought there. Each was a push straight to the branch, and each would now need a pull request, so
you see which of your own steps would stop working. A list it cannot read whole changes nothing.

THE ANSWERS (`check`, and the first read of `run`)
  ON       the rule requires a pull request, admins are included, and nobody may skip it
  OFF      the rule was read, and it does not: the reason is said (no pull request setting, admins
           left out, someone allowed to skip it, or no rule on the branch at all)
  UNKNOWN  something could not be read, or came back in a shape GitHub never gives. Never ON.
It reads the rule itself, never its pull request sub-page: on this repository the two once
disagreed, and the rule is what GitHub enforces (docs/SECURITY.md). A ruleset that also asks for
a pull request is said in a note, and never changes the answer.

WHAT IT NEVER DOES. Run under a model or as root: every mode but the selftest is refused before it
reads anything. Read, print or keep a token: each call is `gh api` with your own `gh` login, and an
error is shown with anything shaped like a token hidden. Change anything without your typed yes.

Exit: 0 ON, done, or nothing to do · 1 OFF, a setting this script will not change, or not done
after the change · 2 usage · 3 refused (an agent's environment, or root) · 4 something could not
be read; nothing changed · 10 you did not type yes; nothing changed.
"""
import argparse
import copy
import json
import os
import re
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from pipeline_agent_env import AGENT_ENV_MARKERS  # noqa: E402  (the one list, imported, never copied)
import pipeline_root_guard as rg  # noqa: E402  (KIT-693: the one root refusal every owner script shares)

ON, OFF, UNKNOWN = "ON", "OFF", "UNKNOWN"
EXIT_OK, EXIT_NOT, EXIT_USAGE, EXIT_REFUSED, EXIT_UNCHECKED, EXIT_NOT_YES = 0, 1, 2, 3, 4, 10
CHECK_EXIT = {ON: EXIT_OK, OFF: EXIT_NOT, UNKNOWN: EXIT_UNCHECKED}
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
DAYS = 30
MAX_PAGES = 50          # 100 commits a page: a branch with more than 5,000 in 30 days is not listed whole
SHOWN = 100             # commits printed one per line; the count above them covers every one
NOT_PROTECTED = "Branch not protected"   # GitHub's 404 for a branch with no rule; any other failure is unread
SETTINGS_PAGE = "the repository's Settings, Branches"
# What this turns on: a pull request before merging, and no approving review, so your own merges.
PULL_REQUEST_SETTING = {"required_approving_review_count": 0, "dismiss_stale_reviews": False,
                        "require_code_owner_reviews": False, "require_last_push_approval": False}
# The rule's on/off settings. GitHub shows each as {"enabled": bool} and takes each back as a bool.
SWITCHES = ("required_linear_history", "allow_force_pushes", "allow_deletions", "block_creations",
            "required_conversation_resolution", "lock_branch", "allow_fork_syncing")
OWN_PAGE = ("required_signatures",)     # shown with the rule, set on a page of its own: a PUT leaves it be
KNOWN = frozenset(("url", "required_status_checks", "enforce_admins", "required_pull_request_reviews",
                   "restrictions") + SWITCHES + OWN_PAGE)
ANY_APP = -1            # how a PUT says "a check from any app"; GitHub shows it as null
TOKEN_RE = re.compile(r"(gh[opsur]_|github_pat_)[A-Za-z0-9_]+")
NO_RULE, NO_PR, ADMINS, SKIP = "no-rule", "no-pr", "admins", "skip"
FIXES = {
    NO_RULE: ("Set up the branch's rule first, with its required checks and admins included: "
              "docs/examples/protection.json shows one. This script adds to a rule; it never makes one."),
    ADMINS: ("Include admins first: tick \"Do not allow bypassing the above settings\" on the rule, in %s. "
             "This script keeps that setting as it finds it." % SETTINGS_PAGE),
    SKIP: ("Take them off the rule's list of who may skip a pull request, in %s. "
           "This script never edits that list." % SETTINGS_PAGE),
}
# One read of the branch's history: each commit, and the pull requests GitHub ties to it.
HISTORY = (
    "query($owner: String!, $name: String!, $ref: String!, $since: GitTimestamp!, $after: String) {"
    " repository(owner: $owner, name: $name) { ref(qualifiedName: $ref) { target { ... on Commit {"
    " history(first: 100, since: $since, after: $after) { pageInfo { hasNextPage endCursor }"
    " nodes { oid committedDate messageHeadline author { name user { login } }"
    " associatedPullRequests(first: 10) { nodes { number merged baseRefName } } } } } } } } }")


class IO:
    """Everything this script asks of the world: the environment, the clock, `gh`, and you."""
    exe = "gh"

    def env(self):
        return dict(os.environ)

    def uid(self):
        return os.geteuid()

    def now(self):
        return time.time()

    def gh(self, args, stdin=None):
        """(ok, stdout, stderr) for one `gh` call, with your own login. Nothing else is asked of it."""
        feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        try:
            p = subprocess.run([self.exe] + list(args), capture_output=True, text=True, timeout=120, **feed)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, "", "gh could not be run (%s)" % type(exc).__name__
        return p.returncode == 0, p.stdout, p.stderr

    def ask(self, prompt):
        try:
            return input(prompt)
        except EOFError:
            return ""

    def say(self, text):
        print(text)
        sys.stdout.flush()


def refused(io):
    """The one refusal line, or None. Read before anything else, so a refusal reads nothing."""
    if any(m in io.env() for m in AGENT_ENV_MARKERS):
        return ("REFUSED: an agent's environment. This script is yours to run, in your own terminal. "
                "Nothing was changed.")
    return rg.refusal(io.uid())


def good_name(name):
    return bool(NAME_RE.fullmatch(name)) and name not in (".", "..")


def good_repo(repo):
    parts = repo.split("/")
    return len(parts) == 2 and all(good_name(p) for p in parts)


def clean(text, limit):
    """`text` with anything unprintable dropped and anything shaped like a token hidden."""
    text = "".join(c for c in str(text) if c.isprintable())
    return TOKEN_RE.sub("[hidden]", text)[:limit]


def _why(err):
    lines = [ln.strip() for ln in (err or "").splitlines() if ln.strip()]
    return clean(lines[-1] if lines else "no reason given", 200)


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- reading
def default_branch(io, repo):
    """(branch, None), or (None, why) when GitHub did not name one."""
    ok, out, err = io.gh(["api", "repos/%s" % repo])
    doc = _json(out) if ok else None
    name = doc.get("default_branch") if isinstance(doc, dict) else None
    if isinstance(name, str) and good_name(name):
        return name, None
    return None, "its default branch could not be read (%s)" % (_why(err) if not ok else "no usable name came back")


def read_rule(io, repo, branch):
    """("rule", the rule as GitHub shows it), ("none", None) when GitHub says the branch has no rule,
    or ("unread", why). Only that one 404 means none: every other failure is unread."""
    ok, out, err = io.gh(["api", "repos/%s/branches/%s/protection" % (repo, branch)])
    doc = _json(out)
    if ok:
        if isinstance(doc, dict):
            return "rule", doc
        return "unread", "the rule came back in a shape GitHub never gives"
    if isinstance(doc, dict) and doc.get("message") == NOT_PROTECTED:
        return "none", None
    return "unread", "the rule could not be read (%s)" % _why(err)


def skippers(reviews):
    """Who the rule lets skip a pull request, by name; None when that could not be read."""
    allow = reviews.get("bypass_pull_request_allowances")
    if allow is None:
        return []
    if not isinstance(allow, dict):
        return None
    names = []
    for kind, key in (("users", "login"), ("teams", "slug"), ("apps", "slug")):
        items = allow.get(kind, [])
        if not isinstance(items, list):
            return None
        for item in items:
            name = item.get(key) if isinstance(item, dict) else None
            names.append(clean(name, 60) if isinstance(name, str) and name else "one of its %s" % kind)
    return names


def judge(kind, found):
    """(answer, [(code, words)]). ON only when the rule was read whole and every part of it holds."""
    if kind == "none":
        return OFF, [(NO_RULE, "the branch has no rule at all")]
    if kind != "rule":
        return UNKNOWN, [(None, found)]
    rule, reasons = found, []
    reviews = rule.get("required_pull_request_reviews")
    if reviews is None:
        reasons.append((NO_PR, "it does not require a pull request"))
    elif not isinstance(reviews, dict):
        return UNKNOWN, [(None, "its pull request setting came back in a shape GitHub never gives")]
    admins = rule.get("enforce_admins")
    if not isinstance(admins, dict) or not isinstance(admins.get("enabled"), bool):
        return UNKNOWN, [(None, "whether it includes admins could not be read")]
    if not admins["enabled"]:
        reasons.append((ADMINS, "admins are left out, so an admin's token is let past it"))
    if isinstance(reviews, dict):
        names = skippers(reviews)
        if names is None:
            return UNKNOWN, [(None, "who may skip its pull request setting could not be read")]
        if names:
            reasons.append((SKIP, "%s may skip the pull request setting" % ", ".join(names)))
    if reasons:
        return OFF, reasons
    return ON, [(None, "it requires a pull request, admins are included, and nobody may skip it")]


def read_answer(io, repo, branch):
    """(branch, the rule or None, answer, reasons). The default branch is read first when none was named."""
    if branch is None:
        branch, why = default_branch(io, repo)
        if branch is None:
            return None, None, UNKNOWN, [(None, why)]
    kind, found = read_rule(io, repo, branch)
    answer, reasons = judge(kind, found)
    return branch, (found if kind == "rule" else None), answer, reasons


def ruleset_note(io, repo, branch):
    """One line for you on rulesets, or None. A ruleset never changes the answer, which is about the rule."""
    ok, out, _err = io.gh(["api", "repos/%s/rules/branches/%s?per_page=100" % (repo, branch)])
    rules = _json(out) if ok else None
    if not isinstance(rules, list):
        return "Rulesets on %s could not be read, so whether one asks for a pull request is not known." % branch
    if any(isinstance(r, dict) and r.get("type") == "pull_request" for r in rules):
        return ("Note: a ruleset also asks for a pull request on %s. This script reads and changes the "
                "branch rule only." % branch)
    return None


def _direct(node, branch):
    """A commit's row when no merged pull request into `branch` brought it there, None when one did,
    or False when the node is not one GitHub gives."""
    if not isinstance(node, dict) or not isinstance(node.get("oid"), str):
        return False
    tied = node.get("associatedPullRequests")
    prs = tied.get("nodes") if isinstance(tied, dict) else None
    if not isinstance(prs, list):
        return False
    # Ten pull requests a commit are read: past ten, a commit is listed, never left out.
    if any(isinstance(p, dict) and p.get("merged") is True and p.get("baseRefName") == branch for p in prs):
        return None
    author = node.get("author") if isinstance(node.get("author"), dict) else {}
    user = author.get("user") if isinstance(author.get("user"), dict) else {}
    who = user.get("login") or author.get("name") or "unknown"
    return (clean(node["oid"], 7), clean(node.get("committedDate") or "", 10), clean(who, 39),
            clean(node.get("messageHeadline") or "", 72))


def direct_commits(io, repo, branch):
    """([row, ...], None) for the branch's commits from the last DAYS days that no merged pull
    request into it brought there; or (None, why) when the history could not be read whole."""
    owner, name = repo.split("/")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(io.now() - DAYS * 86400))
    rows, after = [], None
    for _page in range(MAX_PAGES):
        args = ["api", "graphql", "-f", "query=" + HISTORY, "-f", "owner=" + owner, "-f", "name=" + name,
                "-f", "ref=refs/heads/" + branch, "-f", "since=" + since]
        if after:
            args += ["-f", "after=" + after]
        ok, out, err = io.gh(args)
        if not ok:
            return None, "its history could not be read (%s)" % _why(err)
        try:
            history = _json(out)["data"]["repository"]["ref"]["target"]["history"]
            nodes, more, after = history["nodes"], history["pageInfo"]["hasNextPage"], history["pageInfo"]["endCursor"]
        except (TypeError, KeyError):
            return None, "its history came back in a shape GitHub never gives"
        if not isinstance(nodes, list) or not isinstance(more, bool):
            return None, "its history came back in a shape GitHub never gives"
        for node in nodes:
            row = _direct(node, branch)
            if row is False:
                return None, "a commit in its history came back in a shape GitHub never gives"
            if row:
                rows.append(row)
        if not more:
            return rows, None
        if not isinstance(after, str) or not after:
            return None, "its history did not say where its next page starts"
    return None, "it has more than %d commits in %d days, too many to list whole" % (MAX_PAGES * 100, DAYS)


# --------------------------------------------------------------------------- the change
def put_body(rule):
    """(the rule as GitHub takes it back, None): the pull request setting on, everything else as it was.
    (None, why) when the rule holds something this script cannot send back exactly."""
    unknown = sorted(k for k in rule if k not in KNOWN)
    if unknown:
        return None, "holds a setting this script does not know how to keep: %s" % ", ".join(clean(k, 60) for k in unknown)
    body = {"required_pull_request_reviews": dict(PULL_REQUEST_SETTING), "enforce_admins": rule["enforce_admins"]["enabled"]}
    rsc = rule.get("required_status_checks")
    if rsc is None:
        body["required_status_checks"] = None
    else:
        checks = rsc.get("checks") if isinstance(rsc, dict) else None
        strict = rsc.get("strict") if isinstance(rsc, dict) else None
        if not isinstance(checks, list) or not isinstance(strict, bool):
            return None, "has required checks that could not be read whole"
        sent = []
        for check in checks:
            name = check.get("context") if isinstance(check, dict) else None
            app = check.get("app_id") if isinstance(check, dict) else None
            if not isinstance(name, str) or not name or isinstance(app, bool) or not (app is None or isinstance(app, int)):
                return None, "has a required check that could not be read whole"
            # GitHub shows "any app" as null and takes it back as -1. Left out, it would pick an app itself.
            sent.append({"context": name, "app_id": ANY_APP if app is None else app})
        body["required_status_checks"] = {"strict": strict, "checks": sent}
    restrictions = rule.get("restrictions")
    if restrictions is None:
        body["restrictions"] = None
    else:
        body["restrictions"] = {}
        for kind, key in (("users", "login"), ("teams", "slug"), ("apps", "slug")):
            items = restrictions.get(kind) if isinstance(restrictions, dict) else None
            names = [i.get(key) for i in items if isinstance(i, dict)] if isinstance(items, list) else None
            if names is None or len(names) != len(items) or not all(isinstance(n, str) and n for n in names):
                return None, "has a list of who may push that could not be read whole"
            body["restrictions"][kind] = names
    for key in SWITCHES:
        if key in rule:
            value = rule[key]
            if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
                return None, "has a %s setting that could not be read" % key
            body[key] = value["enabled"]
    return body, None


def _norm(value):
    """A setting with GitHub's links dropped and its lists in one order, to compare by."""
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items() if not k.endswith("url")}
    if isinstance(value, list):
        return sorted((_norm(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def moved(before, after):
    """The settings, other than the pull request one, that read differently after the change."""
    keys = (set(before) | set(after)) - {"required_pull_request_reviews"}
    return sorted(k for k in keys if not k.endswith("url") and _norm(before.get(k)) != _norm(after.get(k)))


def describe_checks(rule):
    rsc = rule.get("required_status_checks")
    if not isinstance(rsc, dict):
        return "none"
    names = [clean(c.get("context"), 80) for c in rsc.get("checks") or [] if isinstance(c, dict)]
    return "%s (each from the app it comes from now; branch up to date first: %s)" % (
        ", ".join(names) or "none", "yes" if rsc.get("strict") else "no")


# --------------------------------------------------------------------------- what you see
def say_answer(io, repo, branch, answer, reasons):
    where = "%s's rule on %s" % (branch, repo) if branch else repo
    io.say("%s: %s: %s." % (answer, where, "; ".join(w for _c, w in reasons)))
    if answer == UNKNOWN:
        io.say("That is not ON. Run it again; if it repeats, paste this terminal to Claude.")


def say_off(io, repo, branch, branch_arg, reasons):
    note = ruleset_note(io, repo, branch)
    if note:
        io.say(note)
    codes = [c for c, _w in reasons]
    if codes == [NO_PR]:
        io.say("A token that can push could push a commit straight to %s. Turn the setting on: "
               "python3 scripts/pipeline_branch_rule.py run --repo %s%s"
               % (branch, repo, " --branch %s" % branch_arg if branch_arg else ""))
    for code in codes:
        if code in FIXES:
            io.say(FIXES[code])


def show_commits(io, branch, rows):
    io.say("")
    io.say("Commits on %s from the last %d days that no pull request brought there: %s"
           % (branch, DAYS, len(rows) if rows else "none"))
    if not rows:
        io.say("Every commit in that time came through a pull request, so no step of yours pushes "
               "straight to %s." % branch)
        return
    for sha, day, who, title in rows[:SHOWN]:
        io.say("  %s  %s  %-20s %s" % (sha, day, who, title))
    if len(rows) > SHOWN:
        io.say("  ... and %d more." % (len(rows) - SHOWN))
    io.say("Each was a push straight to %s. After this change each would need a pull request, so a "
           "step of yours that pushes there stops working." % branch)


def show_change(io, repo, branch, rule):
    io.say("")
    io.say("This changes one setting of %s's rule on %s:" % (branch, repo))
    io.say("  require a pull request before merging:  off, then on, with no approving review needed")
    io.say("It sends the rest back as GitHub shows it now, and checks it after:")
    io.say("  required checks:  %s" % describe_checks(rule))
    io.say("  admins included:  yes")
    io.say("  every other setting of the rule")
    io.say("To undo it later: %s, the rule for %s; untick \"Require a pull request before merging\"."
           % (SETTINGS_PAGE, branch))
    io.say("")


# --------------------------------------------------------------------------- the two modes
def cmd_check(io, repo, branch_arg):
    why = refused(io)
    if why:
        io.say(why)
        return EXIT_REFUSED
    branch, _rule, answer, reasons = read_answer(io, repo, branch_arg)
    say_answer(io, repo, branch, answer, reasons)
    if answer == OFF:
        say_off(io, repo, branch, branch_arg, reasons)
    return CHECK_EXIT[answer]


def cmd_run(io, repo, branch_arg):
    why = refused(io)
    if why:
        io.say(why)
        return EXIT_REFUSED
    branch, rule, answer, reasons = read_answer(io, repo, branch_arg)
    say_answer(io, repo, branch, answer, reasons)
    if answer == ON:
        io.say("Nothing to do. Nothing changed.")
        return EXIT_OK
    if answer == UNKNOWN:
        io.say("Nothing changed.")
        return EXIT_UNCHECKED
    if [c for c, _w in reasons] != [NO_PR]:
        say_off(io, repo, branch, branch_arg, reasons)
        io.say("Nothing changed.")
        return EXIT_NOT
    body, why = put_body(rule)
    if body is None:
        io.say("Nothing changed: the rule %s. Change it in %s, or paste this terminal to Claude."
               % (why, SETTINGS_PAGE))
        return EXIT_NOT
    rows, why = direct_commits(io, repo, branch)
    if rows is None:
        io.say("Nothing changed: %s, so the commits that skipped a pull request could not be listed. "
               "Run it again; if it repeats, paste this terminal to Claude." % why)
        return EXIT_UNCHECKED
    note = ruleset_note(io, repo, branch)
    if note:
        io.say(note)
    show_commits(io, branch, rows)
    show_change(io, repo, branch, rule)
    if io.ask("Type yes to go on: ").strip() != "yes":
        io.say("You did not type yes. Nothing changed.")
        return EXIT_NOT_YES
    ok, _out, err = io.gh(["api", "-X", "PUT", "repos/%s/branches/%s/protection" % (repo, branch),
                           "--input", "-"], stdin=json.dumps(body))
    if not ok:
        io.say("NOT DONE: GitHub refused the change (%s). Read it with `check`, and paste this terminal "
               "to Claude." % _why(err))
        return EXIT_NOT
    kind, found = read_rule(io, repo, branch)
    after, reasons = judge(kind, found)
    if after != ON:
        io.say("NOT DONE: the change was sent, but reading the rule back says %s: %s. Look at it in %s, "
               "and paste this terminal to Claude." % (after, "; ".join(w for _c, w in reasons), SETTINGS_PAGE))
        return EXIT_NOT
    gone = moved(rule, found)
    if gone:
        was = clean(json.dumps({k: _norm(rule.get(k)) for k in gone}, sort_keys=True), 1500)
        io.say("NOT DONE: the pull request setting is on, but these settings read differently now: %s. "
               "Set them back in %s; they were %s. Paste this terminal to Claude."
               % (", ".join(gone), SETTINGS_PAGE, was))
        return EXIT_NOT
    io.say("DONE: %s's rule on %s requires a pull request now. Its required checks, admin enforcement "
           "and every other setting read back as they were." % (branch, repo))
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description="Main's branch rule also requires a pull request (KIT-911).")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("cmd", nargs="?", choices=("check", "run"))
    ap.add_argument("--repo", help="OWNER/NAME: the repository whose rule to read")
    ap.add_argument("--branch", help="a branch other than the repository's default one")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "branch-rule-selftest-")
    if not args.cmd:
        ap.print_help()
        return EXIT_USAGE
    if not args.repo or not good_repo(args.repo):
        print("--repo must be OWNER/NAME, the repository whose rule to read. Nothing was read.")
        return EXIT_USAGE
    if args.branch is not None and not good_name(args.branch):
        print("--branch must be a plain branch name. Nothing was read.")
        return EXIT_USAGE
    return (cmd_check if args.cmd == "check" else cmd_run)(IO(), args.repo, args.branch)


# ======================================================================================
# SELFTEST: nothing below this line runs outside --selftest.
# ======================================================================================
APP = 15368                     # the app GitHub Actions posts its checks as
KIT_CHECKS = ("Kit checks", "Provenance scan", "Hooks change guard", "Temp-leak sweeps")
BASE = "https://api.github.com/repos/acme/app/branches/main/protection"


def _rule(pr=None, admins=True, checks=tuple((c, APP) for c in KIT_CHECKS), strict=False, **extra):
    """A branch rule as GitHub shows it: the shape of a real one read on 19 Nov 2026, links included."""
    rule = {"url": BASE,
            "required_status_checks": {"url": BASE + "/required_status_checks", "strict": strict,
                                       "contexts": [c for c, _a in checks],
                                       "contexts_url": BASE + "/required_status_checks/contexts",
                                       "checks": [{"context": c, "app_id": a} for c, a in checks]},
            "required_signatures": {"url": BASE + "/required_signatures", "enabled": False},
            "enforce_admins": {"url": BASE + "/enforce_admins", "enabled": admins}}
    for key in SWITCHES:
        rule[key] = {"enabled": False}
    if pr is not None:
        rule["required_pull_request_reviews"] = dict(pr, url=BASE + "/required_pull_request_reviews")
    rule.update(extra)
    return rule


def _commit(n, pr=None, base="main", merged=True, who="owner", title="a change"):
    prs = [] if pr is None else [{"number": pr, "merged": merged, "baseRefName": base}]
    return {"oid": ("%x" % n) * 40, "committedDate": "2026-11-%02dT10:00:00Z" % (n % 28 + 1),
            "messageHeadline": title, "author": {"name": who, "user": {"login": who}},
            "associatedPullRequests": {"nodes": prs}}


def _shown(body, before):
    """The rule GitHub shows after a PUT of `body`: "any app" (-1) comes back null, and a check sent
    with no app, or a null one, gets the app that posted it last, as GitHub's own docs say."""
    rule = {"url": BASE, "enforce_admins": {"url": BASE + "/enforce_admins", "enabled": body["enforce_admins"]}}
    rsc = body["required_status_checks"]
    if rsc is not None:
        checks = [{"context": c["context"],
                   "app_id": None if c.get("app_id") == ANY_APP else (c.get("app_id") or APP)} for c in rsc["checks"]]
        rule["required_status_checks"] = {"url": BASE + "/required_status_checks", "strict": rsc["strict"],
                                          "contexts": [c["context"] for c in checks], "checks": checks,
                                          "contexts_url": BASE + "/required_status_checks/contexts"}
    if body["required_pull_request_reviews"] is not None:
        rule["required_pull_request_reviews"] = dict(body["required_pull_request_reviews"],
                                                     url=BASE + "/required_pull_request_reviews")
    if body["restrictions"] is not None:
        rule["restrictions"] = {kind: [{("login" if kind == "users" else "slug"): n} for n in names]
                                for kind, names in body["restrictions"].items()}
    for key in SWITCHES:
        rule[key] = {"enabled": bool(body.get(key, False))}
    for key in OWN_PAGE:
        if key in before:
            rule[key] = before[key]
    return rule


class _FakeIO(IO):
    """A stand-in for `gh` (and for you): it answers from `world`, a dict a test builds, and keeps
    every call. A PUT changes world["rule"] the way GitHub would, unless the world says otherwise."""

    def __init__(self, world, env=None, uid=501, answer="yes", now=1795000000.0):
        self.world, self._env, self._uid, self.answer, self._now = world, env or {}, uid, answer, now
        self.calls, self.lines, self.asked = [], [], []

    def env(self):
        return dict(self._env)

    def uid(self):
        return self._uid

    def now(self):
        return self._now

    def ask(self, prompt):
        self.asked.append(prompt)
        return self.answer

    def say(self, text):
        self.lines.append(text)

    def gh(self, args, stdin=None):
        args = list(args)
        self.calls.append((args, stdin))
        w = self.world
        if args[:1] != ["api"] or len(args) < 2:
            return False, "", "unknown command %r" % args
        if args[1] == "graphql":
            return self._history(args)
        if args[1:3] == ["-X", "PUT"]:
            return self._put(args, stdin)
        path = args[1]
        if path == "repos/acme/app":
            name = w.get("default", "main")
            return (True, json.dumps({"default_branch": name}), "") if name else (False, "", "HTTP 502")
        if path == "repos/acme/app/branches/%s/protection" % w.get("branch", "main"):
            if w.get("reads_left") is not None:
                w["reads_left"] -= 1
                if w["reads_left"] < 0:
                    return False, "", "gh: Bad Gateway (HTTP 502)"
            rule = w.get("rule")
            if rule == "none":
                return False, json.dumps({"message": NOT_PROTECTED, "status": "404"}), "gh: Branch not protected (HTTP 404)"
            if rule == "missing":
                return False, json.dumps({"message": "Branch not found", "status": "404"}), "gh: Branch not found (HTTP 404)"
            if rule == "forbidden":
                return False, json.dumps({"message": "Not Found", "status": "404"}), "gh: Not Found (HTTP 404)"
            if isinstance(rule, str):
                return True, rule, ""         # a raw answer, as given
            return True, json.dumps(rule), ""
        if path.endswith("/protection/required_pull_request_reviews"):
            # The sub-page that once disagreed with the rule: it says a review is required even when
            # the rule has no pull request setting at all. A reader of it would answer wrongly.
            return True, json.dumps({"required_approving_review_count": 1}), ""
        if "/rules/branches/" in path:
            rules = w.get("rules", [])
            return (True, json.dumps(rules), "") if rules is not None else (False, "", "gh: Not Found (HTTP 404)")
        return False, "", "unexpected call %r" % args

    def _history(self, args):
        w = self.world
        fields, i = {}, 2
        while i < len(args):
            if args[i] != "-f" or i + 1 >= len(args) or "=" not in args[i + 1]:
                return False, "", "unexpected graphql argument %r" % args[i]
            key, value = args[i + 1].split("=", 1)
            fields[key] = value
            i += 2
        w.setdefault("history_reads", []).append({k: v for k, v in fields.items() if k != "query"})
        pages = w.get("history", [[]])
        if pages == "fail":
            return False, "", "gh: Something went wrong (HTTP 502)"
        if pages == "shape":
            return True, json.dumps({"data": {"repository": {"ref": None}}}), ""
        index = int(fields["after"][1:]) if "after" in fields else 0
        more = index + 1 < len(pages)
        cursor = None if w.get("no_cursor") else "c%d" % (index + 1)
        doc = {"data": {"repository": {"ref": {"target": {"history": {
            "pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": pages[index]}}}}}}
        return True, json.dumps(doc), ""

    def _put(self, args, stdin):
        w = self.world
        if w.get("put") == "fail":
            return False, json.dumps({"message": "Validation Failed"}), "gh: Validation Failed (HTTP 422)"
        before = w["rule"]
        if w.get("put") != "ignore":
            w["rule"] = _shown(json.loads(stdin), before)
        if w.get("put") == "drop-check":
            w["rule"]["required_status_checks"]["checks"].pop()
        if w.get("put") == "admins-off":
            w["rule"]["enforce_admins"]["enabled"] = False
        return True, json.dumps(w["rule"]), ""


# A stand-in `gh` executable for the one case that runs the real `IO.gh`. It reads and writes one
# file, world.json in its own folder, never a path from its arguments, and answers through _FakeIO.
STAND_IN = r'''#!%(python)s
import json, os, sys
sys.path.insert(0, %(scripts)r)
import pipeline_branch_rule as m
here = os.path.dirname(os.path.abspath(__file__))
world_path = os.path.join(here, "world.json")
with open(world_path, encoding="utf-8") as fh:
    world = json.load(fh)
args = sys.argv[1:]
stdin = sys.stdin.read() if args[-2:] == ["--input", "-"] else None
ok, out, err = m._FakeIO(world).gh(args, stdin=stdin)
world.setdefault("argv", []).append([args, stdin])
with open(world_path, "w", encoding="utf-8") as fh:
    json.dump(world, fh)
sys.stdout.write(out)
sys.stderr.write(err)
sys.exit(0 if ok else 1)
'''


def selftest():
    import tempfile
    from io import StringIO
    import secret_shapes
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, got, want))

    def writes(io):
        return [c for c in io.calls if "-X" in c[0]]

    def run(world, mode="check", branch=None, **kw):
        io = _FakeIO(world, **kw)
        rc = (cmd_check if mode == "check" else cmd_run)(io, "acme/app", branch)
        return io, rc

    def first(io):
        return io.lines[0].split(":", 1)[0] if io.lines else None

    REVIEWS = {"required_approving_review_count": 0, "dismiss_stale_reviews": False,
               "require_code_owner_reviews": False, "require_last_push_approval": False}

    # 1. Refused under a model and as root, in both modes, before anything is read.
    for marker in AGENT_ENV_MARKERS:
        for mode in ("check", "run"):
            io, rc = run({"rule": _rule()}, mode, env={marker: ""})
            check("%s: refused when %s is set, even empty; nothing read or asked" % (mode, marker),
                  (rc, io.calls, io.asked, io.lines[0].startswith("REFUSED")), (EXIT_REFUSED, [], [], True))
    for mode in ("check", "run"):
        io, rc = run({"rule": _rule()}, mode, uid=0)
        check("%s: refused as root with the shared message; nothing read" % mode,
              (rc, io.calls, io.lines), (EXIT_REFUSED, [], [rg.MESSAGE]))

    # 2. check: the answers. Read only, every one.
    for label, world, want in (
            ("the rule with no pull request setting", {"rule": _rule()}, (EXIT_NOT, OFF)),
            ("the rule with the setting this turns on", {"rule": _rule(pr=REVIEWS)}, (EXIT_OK, ON)),
            ("the setting with one approving review is ON too",
             {"rule": _rule(pr=dict(REVIEWS, required_approving_review_count=1))}, (EXIT_OK, ON)),
            ("the setting, with admins left out", {"rule": _rule(pr=REVIEWS, admins=False)}, (EXIT_NOT, OFF)),
            ("the setting, with a user allowed to skip it",
             {"rule": _rule(pr=dict(REVIEWS, bypass_pull_request_allowances={
                 "users": [{"login": "someone"}], "teams": [], "apps": []}))}, (EXIT_NOT, OFF)),
            ("the setting, with an empty skip list", {"rule": _rule(pr=dict(REVIEWS, bypass_pull_request_allowances={
                "users": [], "teams": [], "apps": []}))}, (EXIT_OK, ON)),
            ("no rule on the branch at all", {"rule": "none"}, (EXIT_NOT, OFF)),
            ("a branch GitHub does not know", {"rule": "missing"}, (EXIT_UNCHECKED, UNKNOWN)),
            ("a rule it may not read (no admin rights)", {"rule": "forbidden"}, (EXIT_UNCHECKED, UNKNOWN)),
            ("an answer that is not JSON", {"rule": "<html>"}, (EXIT_UNCHECKED, UNKNOWN)),
            ("an answer that is a list", {"rule": "[]"}, (EXIT_UNCHECKED, UNKNOWN)),
            ("a rule with no admin setting", {"rule": _rule(pr=REVIEWS, enforce_admins=None)},
             (EXIT_UNCHECKED, UNKNOWN)),
            ("an admin setting that is not true or false",
             {"rule": _rule(pr=REVIEWS, enforce_admins={"enabled": "true"})}, (EXIT_UNCHECKED, UNKNOWN)),
            ("a pull request setting that is not an object",
             {"rule": _rule(required_pull_request_reviews=True)}, (EXIT_UNCHECKED, UNKNOWN)),
            ("a skip list in a shape GitHub never gives",
             {"rule": _rule(pr=dict(REVIEWS, bypass_pull_request_allowances={"users": "someone"}))},
             (EXIT_UNCHECKED, UNKNOWN)),
            ("a default branch that cannot be read", {"rule": _rule(pr=REVIEWS), "default": None},
             (EXIT_UNCHECKED, UNKNOWN)),
            ("a default branch with a name it will not put in a path",
             {"rule": _rule(pr=REVIEWS), "default": "../x"}, (EXIT_UNCHECKED, UNKNOWN))):
        io, rc = run(world)
        check("check: %s" % label, (rc, first(io)), want)
        check("check: %s writes nothing and asks nothing" % label, (writes(io), io.asked), ([], []))
    io, _rc = run({"rule": _rule()})
    check("check: it reads the rule itself, never its pull request sub-page (which says a review is required)",
          ([c[0][1] for c in io.calls if c[0][1].startswith("repos/acme/app/branches/")],
           first(io)), (["repos/acme/app/branches/main/protection"], OFF))
    check("check: OFF for no setting gives the owner's command", any(
        "pipeline_branch_rule.py run --repo acme/app" in ln for ln in io.lines), True)
    io, _rc = run({"rule": _rule(pr=dict(REVIEWS, bypass_pull_request_allowances={
        "users": [{"login": "someone"}], "teams": [{"slug": "crew"}], "apps": [{"slug": "bot"}]}))})
    check("check: who may skip it is named, and the fix is said to be the owner's",
          ("someone, crew, bot may skip" in io.lines[0], any(FIXES[SKIP] == ln for ln in io.lines)), (True, True))
    io, _rc = run({"rule": "none"})
    check("check: no rule at all points to the example, and this script makes none",
          any(FIXES[NO_RULE] == ln for ln in io.lines), True)
    io, rc = run({"rule": _rule(), "branch": "trunk"}, branch="trunk")
    check("check: --branch reads that branch, without reading the default one",
          (rc, [c[0][1] for c in io.calls][0], any("--branch trunk" in ln for ln in io.lines)),
          (EXIT_NOT, "repos/acme/app/branches/trunk/protection", True))
    io, rc = run({"rule": _rule(), "rules": [{"type": "pull_request", "ruleset_id": 7}]})
    check("check: a ruleset that asks for a pull request is a note; the answer stays the rule's",
          (rc, first(io), any(ln.startswith("Note: a ruleset also asks") for ln in io.lines)), (EXIT_NOT, OFF, True))
    io, rc = run({"rule": _rule(), "rules": None})
    check("check: rulesets it cannot read are said, and change nothing",
          (rc, any("could not be read, so whether one asks" in ln for ln in io.lines)), (EXIT_NOT, True))
    io, rc = run({"rule": "forbidden", "rules": [{"type": "pull_request"}]})
    check("check: a rule it cannot read is never ON, whatever a ruleset says", (rc, first(io)),
          (EXIT_UNCHECKED, UNKNOWN))
    token = [s[3] for s in secret_shapes.SHAPES if s[0].startswith("GitHub")][0]
    leaky = _FakeIO({"rule": "forbidden"})
    leaky.gh = lambda args, stdin=None: (False, "", "gh: Bad credentials %s (HTTP 401)\n" % token)
    rc = cmd_check(leaky, "acme/app", "main")
    check("check: an error holding a token's shape is shown with the token hidden",
          (rc, any(token in ln for ln in leaky.lines), any("[hidden]" in ln for ln in leaky.lines)),
          (EXIT_UNCHECKED, False, True))

    # 3. run: nothing to do, or nothing it may do, writes nothing and asks nothing.
    for label, world, want in (
            ("already ON", {"rule": _rule(pr=REVIEWS)}, EXIT_OK),
            ("UNKNOWN", {"rule": "forbidden"}, EXIT_UNCHECKED),
            ("no rule at all (it never makes one)", {"rule": "none"}, EXIT_NOT),
            ("admins left out (it keeps that as it is)", {"rule": _rule(admins=False)}, EXIT_NOT),
            ("someone may skip the setting", {"rule": _rule(pr=dict(REVIEWS, bypass_pull_request_allowances={
                "users": [{"login": "someone"}]}))}, EXIT_NOT),
            ("a setting it does not know how to keep", {"rule": _rule(required_deployments={"enabled": True})},
             EXIT_NOT),
            ("required checks with no app list", {"rule": _rule(required_status_checks={
                "strict": False, "contexts": list(KIT_CHECKS)})}, EXIT_NOT),
            ("a required check with an app id that is not a number", {"rule": _rule(
                checks=(("Kit checks", "15368"),))}, EXIT_NOT),
            ("a push list it cannot read", {"rule": _rule(restrictions={"users": [{"id": 1}], "teams": [],
                                                                        "apps": []})}, EXIT_NOT),
            ("a history it cannot read", {"rule": _rule(), "history": "fail"}, EXIT_UNCHECKED),
            ("a history in a shape GitHub never gives", {"rule": _rule(), "history": "shape"}, EXIT_UNCHECKED),
            ("a history with no next-page cursor", {"rule": _rule(), "history": [[_commit(1)], [_commit(2)]],
                                                    "no_cursor": True}, EXIT_UNCHECKED),
            ("a commit in a shape GitHub never gives", {"rule": _rule(), "history": [[{"oid": "a" * 40}]]},
             EXIT_UNCHECKED),
            ("a history longer than it reads", {"rule": _rule(), "history": [[_commit(1, pr=1)]] * (MAX_PAGES + 1)},
             EXIT_UNCHECKED)):
        io, rc = run(world, "run")
        check("run: %s writes nothing and asks nothing" % label, (rc, writes(io), io.asked), (want, [], []))

    # 4. run: the list of commits that skipped a pull request, before anything is asked.
    pages = [[_commit(1, pr=11), _commit(2, who="deploy-bot", title="chore: push the dashboard"),
              _commit(3, pr=13, base="develop")],
             [_commit(4, pr=14, merged=False), _commit(5, pr=15)]]
    world = {"rule": _rule(), "history": pages}
    io, rc = run(world, "run", answer="no")
    listed = [ln for ln in io.lines if ln.startswith("  ") and ln[2:9] in ("2" * 7, "3" * 7, "4" * 7)]
    check("run: lists the commits no merged pull request into the branch brought: a push, one merged into "
          "another branch, one whose pull request never merged; a merged one is left out",
          ([ln[2:9] for ln in listed], any(ln.startswith("  1111111") or ln.startswith("  5555555")
                                           for ln in io.lines)), (["2222222", "3333333", "4444444"], False))
    count = [i for i, ln in enumerate(io.lines) if ln.startswith("Commits on main from the last 30 days")]
    check("run: the list and its count come before the question, and the change is shown",
          (len(count), "no pull request brought there: 3" in io.lines[count[0]] if count else None,
           len(io.asked), any("off, then on, with no approving review" in ln for ln in io.lines)),
          (1, True, 1, True))
    reads = world["history_reads"]
    check("run: the history read pages on, for the branch, from 30 days before now",
          ([r.get("after") for r in reads], reads[0]["ref"], reads[0]["since"], reads[0]["owner"], reads[0]["name"]),
          ([None, "c1"], "refs/heads/main", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1795000000.0 - 30 * 86400)),
           "acme", "app"))
    check("run: anything but yes changes nothing", (rc, writes(io)), (EXIT_NOT_YES, []))
    for typed in ("y", "YES", "yes please", ""):
        io, rc = run({"rule": _rule(), "history": [[]]}, "run", answer=typed)
        check("run: %r is not yes" % typed, (rc, writes(io)), (EXIT_NOT_YES, []))
    io, _rc = run({"rule": _rule(), "history": [[]]}, "run", answer="no")
    check("run: an empty list says no step of yours pushes straight to the branch",
          any("no pull request brought there: none" in ln for ln in io.lines), True)
    many = [[_commit(n % 15 + 1, who="bot", title="n%d" % n) for n in range(SHOWN + 7)]]
    io, _rc = run({"rule": _rule(), "history": many}, "run", answer="no")
    check("run: a long list shows %d lines and counts the rest" % SHOWN,
          (sum(1 for ln in io.lines if ln.startswith("  ") and "  bot " in ln), "  ... and 7 more." in io.lines),
          (SHOWN, True))
    rogue = _commit(6, who="x\x1b[31m", title="evil\x1b]0;title\x07 %s" % token)
    io, _rc = run({"rule": _rule(), "history": [[rogue]]}, "run", answer="no")
    check("run: a commit's words are shown with control characters dropped and token shapes hidden",
          (any("\x1b" in ln or "\x07" in ln or token in ln for ln in io.lines),
           any("[hidden]" in ln for ln in io.lines)), (False, True))

    # 5. run: yes sends one PUT that changes the one setting and carries the rest; then reads it back.
    world = {"rule": _rule(), "history": [[_commit(1, pr=1)]]}
    before = copy.deepcopy(world["rule"])
    io, rc = run(world, "run")
    puts = writes(io)
    want = {"required_status_checks": {"strict": False, "checks": [{"context": c, "app_id": APP} for c in KIT_CHECKS]},
            "enforce_admins": True, "required_pull_request_reviews": REVIEWS, "restrictions": None}
    want.update((k, False) for k in SWITCHES)
    check("run: yes sends one PUT, to the rule, of exactly the rule with the setting on",
          (rc, len(puts), puts[0][0] if puts else None, json.loads(puts[0][1]) if puts else None),
          (EXIT_OK, 1, ["api", "-X", "PUT", "repos/acme/app/branches/main/protection", "--input", "-"], want))
    check("run: it reads the rule back, ON, and says DONE",
          (io.calls[-1][0][1], io.lines[-1].startswith("DONE"), moved(before, world["rule"])),
          ("repos/acme/app/branches/main/protection", True, []))
    io, rc = run(world, "run")
    check("run: a second run finds it ON and changes nothing", (rc, writes(io), io.asked), (EXIT_OK, [], []))
    world = {"rule": _rule(checks=(("Kit checks", APP), ("Lint", None)), strict=True,
                           allow_force_pushes={"enabled": True}, required_signatures={"enabled": True},
                           restrictions={"users": [{"login": "owner", "url": "u"}], "teams": [{"slug": "core"}],
                                         "apps": []}),
             "history": [[]]}
    before = copy.deepcopy(world["rule"])
    io, rc = run(world, "run")
    sent = json.loads(writes(io)[0][1])
    check("run: a check from any app goes back as any app (-1), strict and every switch as they were, and who "
          "may push by name",
          (sent["required_status_checks"], sent["allow_force_pushes"], sent["restrictions"], "required_signatures" in sent),
          ({"strict": True, "checks": [{"context": "Kit checks", "app_id": APP}, {"context": "Lint", "app_id": ANY_APP}]},
           True, {"users": ["owner"], "teams": ["core"], "apps": []}, False))
    check("run: ...and every one reads back the same: DONE", (rc, moved(before, world["rule"]),
                                                              io.lines[-1].startswith("DONE")), (EXIT_OK, [], True))
    world = {"rule": _rule(required_status_checks=None), "history": [[]]}
    io, rc = run(world, "run")
    check("run: a rule with no required checks keeps none", (rc, json.loads(writes(io)[0][1])["required_status_checks"]),
          (EXIT_OK, None))

    # 6. run: NOT DONE, never DONE, when the change did not hold or something else moved.
    for label, extra, word in (
            ("GitHub refuses the PUT", {"put": "fail"}, "Validation Failed"),
            ("the PUT changes nothing", {"put": "ignore"}, "says OFF"),
            ("a required check is gone after it", {"put": "drop-check"}, "required_status_checks"),
            ("admins are left out after it", {"put": "admins-off"}, "says OFF"),
            ("the rule cannot be read back", {"reads_left": 1}, "says UNKNOWN")):
        world = dict({"rule": _rule(), "history": [[]]}, **extra)
        io, rc = run(world, "run")
        check("run: %s is NOT DONE, named" % label,
              (rc, io.lines[-1].startswith("NOT DONE"), word in io.lines[-1]), (EXIT_NOT, True, True))

    # 7. The real `IO.gh`, run once against a stand-in `gh` executable (argv, the body on stdin, exit codes).
    folder = tempfile.mkdtemp(prefix="gh-")
    exe = os.path.join(folder, "gh")
    with open(exe, "w", encoding="utf-8") as fh:
        fh.write(STAND_IN % {"python": sys.executable, "scripts": HERE})
    os.chmod(exe, stat.S_IRWXU)
    with open(os.path.join(folder, "world.json"), "w", encoding="utf-8") as fh:
        json.dump({"rule": _rule(), "history": [[_commit(2, who="deploy-bot")]]}, fh)

    class _Real(IO):
        def env(self):
            return {}

        def uid(self):
            return 501

        def ask(self, prompt):
            return "yes"

        def say(self, text):
            self.lines.append(text)

    real = _Real()
    real.exe, real.lines = exe, []
    rc = cmd_run(real, "acme/app", None)
    with open(os.path.join(folder, "world.json"), encoding="utf-8") as fh:
        seen = json.load(fh)
    sent = [s for a, s in seen["argv"] if a[:3] == ["api", "-X", "PUT"]]
    check("through the real IO and a stand-in gh: one PUT, its body on stdin, read back, DONE",
          (rc, len(sent), json.loads(sent[0])["required_pull_request_reviews"] if sent else None,
           real.lines[-1].startswith("DONE"), any(ln.startswith("  2222222") for ln in real.lines)),
          (EXIT_OK, 1, REVIEWS, True, True))
    gone = _Real()
    gone.exe, gone.lines = os.path.join(folder, "no-such-gh"), []
    check("through the real IO: a gh that cannot be run is UNKNOWN, never ON",
          (cmd_check(gone, "acme/app", "main"), first(gone)), (EXIT_UNCHECKED, UNKNOWN))

    # 8. What it can write, and what it never touches.
    src = open(os.path.abspath(__file__), encoding="utf-8").read().split("\n# " + "=" * 86, 1)[0]
    check("the one write is a PUT of the rule: no POST, PATCH or DELETE, and no `gh auth` anywhere",
          (src.count('"-X"'), src.count('"PUT"'), any(m in src for m in ('"POST"', '"PATCH"', '"DELETE"', '"auth"'))),
          (1, 1, False))
    check("the setting it turns on asks for a pull request and no approving review",
          PULL_REQUEST_SETTING["required_approving_review_count"], 0)
    root = os.path.dirname(HERE)
    with open(os.path.join(root, "docs", "examples", "protection.json"), encoding="utf-8") as fh:
        example = json.load(fh)
    check("the kit's example rule asks for the same: a pull request, no review, admins included",
          ((example.get("required_pull_request_reviews") or {}).get("required_approving_review_count"),
           example.get("enforce_admins")), (0, True))
    with open(os.path.join(root, "docs", "SECURITY.md"), encoding="utf-8") as fh:
        check("docs/SECURITY.md names this script", "scripts/pipeline_branch_rule.py" in fh.read(), True)
    for argv, want in ((["check"], EXIT_USAGE), (["check", "--repo", "acme"], EXIT_USAGE),
                       (["run", "--repo", "../app"], EXIT_USAGE),
                       (["check", "--repo", "acme/app", "--branch", "a/b"], EXIT_USAGE), ([], EXIT_USAGE)):
        real_stdout, sys.stdout = sys.stdout, StringIO()
        try:
            rc = main(argv)
        finally:
            sys.stdout = real_stdout
        check("usage: %r is refused before anything is read" % (argv,), rc, want)

    if failures:
        print("\n".join(failures))
        print("pipeline_branch_rule selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: pipeline_branch_rule selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
