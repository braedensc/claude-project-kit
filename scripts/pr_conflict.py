#!/usr/bin/env python3
"""Conflict self-resolution — one loop, two halves, one marker grammar.

    pr_conflict.py monitor [--dry-run]              # GitHub Actions (pr-conflict-monitor.yml)
    pr_conflict.py wake [--repo-dir DIR]... [opts]  # a PERSON's machine, as a LaunchAgent
    pr_conflict.py --selftest

  `wake` is installed and supervised by scripts/pipeline_conflict_waker_setup.py — one
  LaunchAgent per person, a heartbeat, a signed-off dry run. Nobody types a loop. That
  installer is macOS-only; `wake` is not. It is one pass of plain Python that shells `gh`
  and `claude` and exits, so a systemd timer or cron runs it anywhere
  (docs/COLLABORATION.md, parallel-session protocol item 8).

THE GAP THIS CLOSES

  A session opens a PR, drives it green and ends its turn. The Stop hook
  (.claude/hooks/stop-pr-check.py) samples merge state at that turn-end and never
  again: it holds no timer, so when a sibling merges and main moves, the PR goes
  CONFLICTING with nobody watching. The monitor workflow saw it — and could only
  page a person, because nothing in the repo can re-invoke an idle local session.
  With many parallel sessions that person becomes the only bridge between
  "detected" and "resolved" (docs/LESSONS.md, 2026-09-15).

WHY THE FIX RUNS LOCALLY, NOT IN ACTIONS

  The template's answer is an `@claude` comment for claude.yml. It needs a model
  credential in Actions, a PAT (GITHUB_TOKEN-authored comments and pushes fire no
  workflows, so neither the handoff nor the fix's CI would start), `workflows`
  scope for any conflict under .github/workflows/, and it runs the model OUTSIDE
  the PreToolUse hooks. The sessions that own these branches already exist on a
  machine that holds the owner's credentials and runs the hooks. So GitHub keeps
  the clock and the budget; the machine does the work:

    monitor (Actions)                     wake (a person's machine)
    ─────────────────                     ─────────────────────────
    CONFLICTING, new episode
      → label + `request` marker   ──►    sees an unclaimed request for a branch
                                          one of ITS worktrees holds
                                          → `ack` marker, then `claude -p` in that
                                            worktree: merge the PR's base, resolve,
                                            push, watch CI — never merge or approve
                                          → re-reads mergeable → `result` marker
    next tick:
      MERGEABLE        → drop label (episode over)
      no ack in 15 min → ESCALATE (page a person — WHO A PAGE REACHES)
      no result in 2 h → ESCALATE
      result, still CONFLICTING → ESCALATE
      budget spent (3 requests on this PR) or a fork → page, never request

  The cost, said plainly: the loop closes only while a waker runs on a machine
  that holds the branch's worktree and is awake. When it does not, the monitor's
  deadline turns that into a page — the same page as before, never silence.

WHOSE PULL REQUEST THE WAKER MAY TAKE — AND WHOSE IT MAY NEVER TAKE

  A fix session starts OUTSIDE any sandbox, as the person running the waker, and
  reads a branch it did not write. That is fine for a branch the same person's own
  local session wrote, and wrong for one a dispatcher's SANDBOXED session wrote:
  the fix would run with the owner's reach over text a sandboxed agent authored.
  A dispatcher's PR has its own wake path that keeps the sandbox — the Stage E
  bounce driver re-prompts the original session in its tracker thread
  (scripts/pipeline_bounce_local.py, the `conflict` action), and it answers the
  same `request` marker. So the waker takes a request only when ALL of these hold,
  and each is a fact a dispatcher's session cannot write:

    * the worktree holding the branch belongs to THIS uid, and
    * THIS user's own Claude Code has worked in it — a transcript under
      `<claude config dir>/projects/<the worktree path, non-alphanumerics as '-'>`,
      owned by this uid. A dispatcher's sessions run as its role account and keep
      their transcripts under THAT home, which this uid cannot write; and
    * the waker is not itself running as a dispatcher's role account (it refuses
      where `~/.stage-e/env` exists — that account's PRs are the bounce driver's)
      or as root.

  A request none of that matches is left alone, and the monitor's ack deadline
  turns it into a page. If Claude Code ever moves its transcripts, every request
  reads "no local session" and pages — loud, never a fix in the wrong place.

WHAT A PASS IS BOUNDED BY

  Per session: `--max-budget-usd` and `--timeout-min`. Per pass: `--max-sessions`,
  and the product of the two must fit inside the monitor's result deadline (so a
  queued session is never still working when the monitor pages about it). Every
  session this pass will run is ACKNOWLEDGED BEFORE THE FIRST STARTS, so a queue
  cannot miss the ack deadline. Per PR, lifetime: the monitor's three requests.
  A request over the pass cap is not acknowledged and is said by number: the
  monitor pages it. No loop anywhere — launchd starts the next pass.

THE BASE BRANCH IS GITHUB'S, NEVER ASSUMED

  Every recipe, headline and fix prompt names the PR's own `baseRefName` — the branch
  GitHub computed `mergeable` against — and falls back to the repository's default
  branch, read once per run and cached on `Gh`. A repository whose default is `trunk`
  gets a command that runs. The bounce driver picks its base the same way, so the two
  lanes hand a session the same command.

  A STACKED PR — based on a branch that is neither the default branch nor main/master
  — is not sent a fix request at all. Its recipe would be `git merge origin/<feature>`,
  which the PreToolUse stacked-branch guard refuses (feature-to-feature merges are not
  allowed; docs/COLLABORATION.md § Never stack a PR). It is PAGED instead, with
  reason=stacked and a retarget recipe, so no session is started on a step it cannot
  run.

WHO A PAGE REACHES

  An @mention of an ORGANIZATION notifies nobody, so the page never names the
  repository owner blindly. It names, in order: the logins in `--page-to` (the
  PR_CONFLICT_PAGE_TO repository variable in the workflow); else the PR's author, when
  that is a person; else the repository owner, when that is a person. The comment says
  which. When none applies — a bot's PR on an organization's repository, with no
  --page-to — the comment says nobody was paged and the run exits 1, because a page
  that reaches nobody must not look delivered.

MARKERS (the whole producer/consumer contract)

  The FIRST LINE of a comment, exactly:

    <!-- pr-conflict:request episode=N -->                      github-actions[bot]
    <!-- pr-conflict:page episode=N reason=budget|fork|stacked -->  github-actions[bot]
    <!-- pr-conflict:escalated episode=N -->                    github-actions[bot]
    <!-- pr-conflict:ack episode=N -->                          OWNER/MEMBER/COLLABORATOR
    <!-- pr-conflict:result episode=N outcome=O -->             OWNER/MEMBER/COLLABORATOR

  Bot markers count only from the Actions bot, so the budget cannot be reset by a
  comment. Waker markers count only from a writer, and only when posted after the
  episode opened, so an ack cannot be pre-posted for a future episode. A waker
  marker can never grant anything: an ack only moves the deadline (bounded), and a
  result only ends the wait — whether the conflict is gone is read from GitHub's
  own `mergeable`, never from the claim. Session output embedded in a result
  comment is neutralized, and only the first line is ever parsed, so a session
  cannot forge a marker (docs/LESSONS.md, agent-forged markers).

WHAT NEITHER HALF EVER DOES (asserted in --selftest)

  Merge, enable auto-merge, approve, push, or write any label except `conflict`.
  The fix session runs under the repo's own hooks, which block all of those too.

EXIT CODES (contract §13)

  0  the pass completed — it printed what it asked and what the answer was
  1  could not tell: mergeability never settled, a listing was truncated, a
     GitHub call failed, or a fix session ended with GitHub unable to say whether
     the conflict is gone. Never the same token as "no conflicts". Also: a page
     was posted that notifies nobody (WHO A PAGE REACHES).
  2  usage error
  3  REFUSED — `wake` (not --dry-run) in an agent environment, as root, or as a
     dispatcher's role account

THE WAKER'S HEARTBEAT (contract §13)

  Every real `wake` pass writes `<state dir>/heartbeat.json`
  (`pr-conflict-waker-heartbeat/1`): a `running` beat first, then `started_at`,
  `finished_at`, `result` (ok | idle | problems | error) and what it did. A stale
  file is NOT RUNNING; a fresh one saying `idle` is ran-and-had-nothing-to-do.
  A dry run and a refusal write none, so neither can impersonate a pass.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

LABEL = "conflict"
MAX_FIX_REQUESTS = 3          # automated attempts per PR, lifetime; the 4th conflict pages
# …unless a repository says otherwise. `monitor --max-fix-requests N` overrides it, and 0
# means NOTHING HERE ANSWERS: page a person at detection rather than ask for a fix nobody
# can do (KIT-175). The budget is the whole difference between the two first-conflict
# paths, so it is the only knob that needs to exist for that.
ACK_DEADLINE_MIN = 15
RESULT_DEADLINE_MIN = 120     # a pass may queue several sessions of up to --timeout-min each
SETTLE_ATTEMPTS = 5           # bounded re-query while GitHub computes `mergeable` lazily
SETTLE_DELAY_S = 15
MAX_PR_PAGES = 5              # 500 open PRs; past that, "could not tell", never truncate
MAX_COMMENT_PAGES = 20
BOT_LOGIN = "github-actions[bot]"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
BOT_KINDS = {"request", "page", "escalated"}
WAKER_KINDS = {"ack", "result"}
OUTCOMES = {"resolved", "unresolved", "unknown", "declined", "failed"}
MARKER_RE = re.compile(
    r"^<!-- pr-conflict:(request|page|escalated|ack|result) episode=(\d+)"
    r"((?: [a-z]+=[a-z-]+)*) -->$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")
# A user login, or an org/team slug (mentionable, not assignable). Nothing else is
# embedded after an `@`, so a value here cannot carry markup into a comment.
PAGE_TO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,99})?$")
PERSON_TYPES = {"User", "EnterpriseUserAccount"}  # GraphQL __typename of an actor a mention notifies
LABEL_DESCRIPTION_MAX = 100  # GitHub's limit; past it the create is a 422, read as "exists"
TAIL_CHARS = 1500

WAKER_HEARTBEAT_SCHEMA = "pr-conflict-waker-heartbeat/1"
DEFAULT_WAKER_HOME = "~/.pr-conflict-waker"
DEFAULT_STATE_DIR = DEFAULT_WAKER_HOME + "/state"
DEFAULT_MAX_SESSIONS = 2
DEFAULT_BUDGET_USD = 5.0
DEFAULT_TIMEOUT_MIN = 40
# A queued session must finish inside the monitor's result deadline, with room for
# the settle re-reads and the result comment after it.
QUEUE_MARGIN_MIN = 10
# The Stage E role account's env file (pipeline_bounce_local.DEFAULT_ENV_FILE — the
# selftest pins the two together). Where it exists, this is a dispatcher's account,
# its PRs are the bounce driver's to wake, and a waker here would run fixes outside
# the sandbox its sessions live in.
DISPATCHER_ENV_FILE = "~/.stage-e/env"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402  (one tuple, owned there)


class CouldNotTell(Exception):
    """Exit 1. The pass could not establish an answer."""


class Refusal(Exception):
    """Exit 3. Nothing was written."""


# ── markers ──────────────────────────────────────────────────────────────────

def marker(kind, episode, **attrs):
    extra = "".join(f" {k}={v}" for k, v in attrs.items())
    return f"<!-- pr-conflict:{kind} episode={episode}{extra} -->"


def _ts(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_marker(comment):
    """The marker on a comment's FIRST line, or None — including when the author
    is not allowed to write that kind."""
    first = (comment.get("body") or "").split("\n", 1)[0].strip()
    m = MARKER_RE.match(first)
    if not m:
        return None
    kind, episode = m.group(1), int(m.group(2))
    user = comment.get("user") or {}
    if kind in BOT_KINDS:
        if user.get("login") != BOT_LOGIN or user.get("type") != "Bot":
            return None
    elif comment.get("author_association") not in TRUSTED_ASSOCIATIONS:
        return None
    attrs = dict(p.split("=", 1) for p in m.group(3).split())
    return {"kind": kind, "episode": episode, "attrs": attrs, "at": _ts(comment["created_at"])}


def episode_state(comments):
    """Where this PR stands, read only from markers its authors were allowed to write."""
    marks = [m for m in (parse_marker(c) for c in comments) if m]
    opened = [m for m in marks if m["kind"] in ("request", "page")]
    latest = max(opened, key=lambda m: (m["episode"], m["at"])) if opened else None
    n = latest["episode"] if latest else 0

    def of(kind):
        return [m for m in marks if m["kind"] == kind and m["episode"] == n
                and latest and m["at"] >= latest["at"]]

    return {
        "requests": sum(1 for m in marks if m["kind"] == "request"),
        "opened": len(opened),
        "latest": latest,
        "episode": n,
        "escalated": bool(of("escalated")),
        "ack": (of("ack") or [None])[0],
        "result": (of("result") or [None])[-1],
    }


def decide_ongoing(state, now):
    """A PR still CONFLICTING and already labeled: stay quiet, or escalate once."""
    latest = state["latest"]
    if latest is None:
        return "quiet", "labeled before this monitor version wrote markers — already paged then"
    if latest["kind"] == "page" or state["escalated"]:
        return "quiet", f"episode {state['episode']} already with a person"
    if state["result"]:
        outcome = state["result"]["attrs"].get("outcome", "unknown")
        return "escalate", (f"the local fix attempt ended (outcome: {outcome}) and GitHub "
                            "still reports the PR as CONFLICTING")
    age = (now - latest["at"]).total_seconds() / 60
    if not state["ack"]:
        if age > ACK_DEADLINE_MIN:
            return "escalate", (
                f"no conflict waker acknowledged the request within {ACK_DEADLINE_MIN} min — "
                "none is running on a machine that holds this branch's worktree, that "
                "machine is asleep, or it could not reach GitHub")
        return "quiet", f"fix requested {age:.0f} min ago; waiting for a waker"
    ack_age = (now - state["ack"]["at"]).total_seconds() / 60
    if ack_age > RESULT_DEADLINE_MIN:
        return "escalate", (f"a waker acknowledged the request {ack_age:.0f} min ago and "
                            f"reported no result within {RESULT_DEADLINE_MIN} min")
    return "quiet", f"fix in progress (acknowledged {ack_age:.0f} min ago)"


def neutralize(text, limit=TAIL_CHARS):
    """Session output made safe to embed: no marker opener, no fence break-out,
    no @-pings, and bounded."""
    text = (text or "")[-limit:]
    text = text.replace("<!--", "&lt;!--")
    text = re.sub(r"`{3,}", "'''", text)
    return text.replace("@", "@\u200b")


# ── transport ────────────────────────────────────────────────────────────────

def run(cmd, cwd=None, input=None, timeout=None):
    return subprocess.run(cmd, cwd=cwd, input=input, timeout=timeout,
                          capture_output=True, text=True)


class Gh:
    """Every GitHub read and write either half makes. Nothing else talks to GitHub."""

    def __init__(self, repo, runner=run, cwd=None):
        self.owner, self.name = repo.split("/", 1)
        self.runner, self.cwd = runner, cwd
        self._meta = None

    def _repo_meta(self):
        """The repository's own record, read at most once per Gh: its default branch and
        whether its owner is a person. Neither is ever assumed."""
        if self._meta is None:
            data = self._api("GET", f"repos/{self.owner}/{self.name}") or {}
            branch = data.get("default_branch") or ""
            if not BRANCH_RE.match(branch):
                raise CouldNotTell(f"GitHub named no usable default branch for {self.owner}/{self.name}: {branch!r}")
            self._meta = {"default_branch": branch, "owner_type": (data.get("owner") or {}).get("type") or ""}
        return self._meta

    @property
    def default_branch(self):
        return self._repo_meta()["default_branch"]

    @property
    def owner_type(self):
        return self._repo_meta()["owner_type"]

    def _api(self, method, path, body=None, ok_statuses=()):
        cmd = ["gh", "api", "-X", method, path]
        if body is not None:
            cmd += ["--input", "-"]
        r = self.runner(cmd, cwd=self.cwd, input=None if body is None else json.dumps(body))
        if r.returncode != 0:
            if any(f"HTTP {s}" in (r.stderr or "") for s in ok_statuses):
                return None
            raise CouldNotTell(f"gh api {method} {path} failed: {(r.stderr or '').strip()[-300:]}")
        return json.loads(r.stdout) if (r.stdout or "").strip() else None

    def _graphql(self, query, variables):
        data = self._api("POST", "graphql", {"query": query, "variables": variables})
        if not data or data.get("errors"):
            raise CouldNotTell(f"GraphQL error: {json.dumps((data or {}).get('errors'))[:300]}")
        return data["data"]

    def _prs(self, states, labels=None):
        query = """query($owner:String!,$name:String!,$states:[PullRequestState!],$labels:[String!],$cursor:String){
          repository(owner:$owner,name:$name){pullRequests(states:$states,labels:$labels,first:100,after:$cursor){
            pageInfo{hasNextPage endCursor}
            nodes{number isDraft mergeable headRefName baseRefName isCrossRepository author{login __typename}
                  labels(first:100){nodes{name}}}}}}"""
        out, cursor = [], None
        for _ in range(MAX_PR_PAGES):
            page = self._graphql(query, {"owner": self.owner, "name": self.name, "states": states,
                                         "labels": labels, "cursor": cursor})
            prs = page["repository"]["pullRequests"]
            out += [{**p, "labels": [label["name"] for label in p["labels"]["nodes"]],
                     "author": (p.get("author") or {}).get("login"),
                     "authorType": (p.get("author") or {}).get("__typename")}
                    for p in prs["nodes"]]
            if not prs["pageInfo"]["hasNextPage"]:
                return out
            cursor = prs["pageInfo"]["endCursor"]
        raise CouldNotTell(f"more than {MAX_PR_PAGES * 100} {states} PRs — refusing to judge a truncated list")

    def open_prs(self):
        return self._prs(["OPEN"])

    def closed_labeled_prs(self):
        return self._prs(["CLOSED", "MERGED"], [LABEL])

    def comments(self, number):
        out = []
        for page in range(1, MAX_COMMENT_PAGES + 1):
            batch = self._api("GET", f"repos/{self.owner}/{self.name}/issues/{number}/comments"
                                     f"?per_page=100&page={page}") or []
            out += batch
            if len(batch) < 100:
                return out
        raise CouldNotTell(f"#{number} has more than {MAX_COMMENT_PAGES * 100} comments")

    def ensure_label(self):
        description = f"Merge conflicts with {self.default_branch} (auto-managed by pr-conflict-monitor)"
        self._api("POST", f"repos/{self.owner}/{self.name}/labels",
                  {"name": LABEL, "color": "d93f0b", "description": description[:LABEL_DESCRIPTION_MAX]},
                  ok_statuses=(422,))

    # No label parameter on purpose: `conflict` is the only label either half writes.
    def add_label(self, number):
        self._api("POST", f"repos/{self.owner}/{self.name}/issues/{number}/labels",
                  {"labels": [LABEL]})

    def remove_label(self, number):
        self._api("DELETE", f"repos/{self.owner}/{self.name}/issues/{number}/labels/{LABEL}",
                  ok_statuses=(404,))

    def comment(self, number, body):
        self._api("POST", f"repos/{self.owner}/{self.name}/issues/{number}/comments", {"body": body})

    def assign(self, number, logins):
        people = [login for login in logins if "/" not in login]  # a team is mentioned, never assigned
        if not people:
            return
        try:
            self._api("POST", f"repos/{self.owner}/{self.name}/issues/{number}/assignees",
                      {"assignees": people})
        except CouldNotTell as e:  # e.g. not a collaborator; the @mention is the page, this is extra
            print(f"  #{number}: could not assign {', '.join('@' + p for p in people)}: {e}")

    def mergeable(self, number):
        query = """query($owner:String!,$name:String!,$number:Int!){
          repository(owner:$owner,name:$name){pullRequest(number:$number){mergeable state}}}"""
        pr = self._graphql(query, {"owner": self.owner, "name": self.name, "number": number})
        return pr["repository"]["pullRequest"]["mergeable"]


# ── comment bodies ───────────────────────────────────────────────────────────

def base_of(pr, gh):
    """The branch this PR conflicts with: GitHub's `baseRefName`, else the default branch."""
    return pr.get("baseRefName") or gh.default_branch


def is_stacked(pr, gh):
    """True when the PR is based on a branch that is not a base branch — not the
    default branch, and not main/master (the base set every layer shares). Asks for
    the default branch only when the base is something else, so a main-based PR
    costs nothing."""
    base = pr.get("baseRefName")
    return bool(base) and base not in ("main", "master") and base != gh.default_branch


def page_recipients(pr, gh, page_to=()):
    """(logins, whose) — who a page @mentions, and the words that say why them. An empty
    list means nobody a mention would notify: the caller says so and fails the run."""
    if page_to:
        return list(page_to), "PR_CONFLICT_PAGE_TO"
    if pr.get("author") and pr.get("authorType") in PERSON_TYPES:
        return [pr["author"]], "the PR's author"
    author = "a bot" if pr.get("author") else "unknown"
    if gh.owner_type == "User":
        return [gh.owner], f"the repository owner — the PR's author is {author}"
    return [], (f"the PR's author is {author} and the repository owner is an organization, which an "
                "@mention does not notify. Set the PR_CONFLICT_PAGE_TO repository variable to the "
                "logins that should be paged")


def parse_page_to(value):
    """(logins, rejected) from a comma- or space-separated value; a leading `@` is dropped."""
    names = [v.lstrip("@") for v in re.split(r"[\s,]+", value or "") if v.strip()]
    return list(dict.fromkeys(names)), [n for n in names if not PAGE_TO_RE.match(n)]


def _page_line(page):
    logins, whose = page
    if not logins:
        return f"**Nobody was paged:** {whose}. This PR cannot merge and its required checks are not running."
    return (f"cc {', '.join('@' + login for login in logins)} ({whose}) — this PR cannot merge and its "
            "required checks are not running.")


def _recipe(pr, base):
    return "\n".join([
        "```", f"git fetch origin {base} && git merge origin/{base}",
        "# resolve, run the local checks, git commit, then:",
        "git push", f"gh pr checks {pr['number']} --watch", "```",
        "",
        "(A merge, not a rebase: nothing is force-pushed under a worktree that may still hold this branch.)",
    ])


def _headline(pr, base):
    return (f"`{pr['headRefName']}` has **merge conflicts** with `{base}` (mergeable = CONFLICTING). "
            "While conflicted, GitHub cannot build the merge ref, so the **required checks never run** "
            "— a side check can still report green. Do not read that as a passing PR.")


def request_body(pr, episode, attempt, base, budget=None):
    budget = MAX_FIX_REQUESTS if budget is None else budget
    return "\n".join([
        marker("request", episode), _headline(pr, base), "",
        f"**A fix has been requested** (automated attempt {attempt} of {budget} on this PR). "
        "It is acknowledged here by whichever owns this branch's session: the conflict waker on the "
        "machine where a local session worked in its worktree, or — for a dispatcher's PR — the "
        "bounce driver, which sends it back to that session's own thread. Either way the session "
        f"merges `{base}`, resolves, pushes and watches CI. Nothing merges or approves.",
        "",
        f"If nothing acknowledges this within {ACK_DEADLINE_MIN} min, or the attempt ends with the "
        "PR still conflicted, this monitor pages a person here. To fix it by hand meanwhile:", "",
        _recipe(pr, base), "",
        "The label clears itself once the PR is mergeable again, so a later conflict starts a new episode.",
    ])


def _retarget_recipe(pr, base, default):
    return "\n".join([
        "```", f"gh pr edit {pr['number']} --base {default}",
        "# then read the diff. If this branch was also CUT from that feature branch,",
        f"# its commits are still in this PR — move your own commits onto {default}:",
        f"git rebase --onto origin/{default} origin/{base}",
        "git push --force-with-lease", f"gh pr checks {pr['number']} --watch", "```",
        "",
        f"(Retarget, not merge: merging `{base}` in is the feature-to-feature merge the kit "
        "forbids, and the stacked-branch guard refuses it.)",
    ])


def page_body(pr, episode, reason, base, page, budget=None, default=None):
    budget = MAX_FIX_REQUESTS if budget is None else budget
    why = {
        "budget": (f"this PR has already had {budget} automated fix attempts — a PR that "
                   "keeps conflicting needs a person, not another guess")
        if budget else
        # The 0 case reads as its own sentence: "0 attempts" would say nothing true.
        ("nothing in this repository answers an automated fix request — it is configured "
         "to page a person at the first conflict instead"),
        "fork": "it comes from a fork, and the waker only acts on branches in this repository",
        "stacked": (f"its base `{base}` is not the default branch — it is STACKED on another "
                    "branch, and a stacked PR is retargeted, not merged into "
                    "(docs/COLLABORATION.md § Never stack a PR)"),
    }[reason]
    recipe = _retarget_recipe(pr, base, default) if reason == "stacked" else _recipe(pr, base)
    return "\n".join([
        marker("page", episode, reason=reason), _headline(pr, base), "",
        f"**No automated fix requested:** {why}.", "", recipe, "",
        _page_line(page),
    ])


def escalation_body(pr, episode, reason, base, page):
    return "\n".join([
        marker("escalated", episode),
        f"**The automated conflict fix did not land — this needs a person.** {reason[0].upper()}{reason[1:]}.",
        "", _recipe(pr, base), "",
        _page_line(page),
    ])


# ── monitor (Actions) ────────────────────────────────────────────────────────

def settle(fetch, sleep, attempts=SETTLE_ATTEMPTS, delay=SETTLE_DELAY_S):
    """Re-query a FIXED number of times while mergeability is UNKNOWN. Never loops on it."""
    prs = fetch()
    for attempt in range(2, attempts + 1):
        pending = [p["number"] for p in prs if p["mergeable"] == "UNKNOWN" and not p["isDraft"]]
        if not pending:
            break
        print(f"mergeability still computing for {pending} — re-querying ({attempt}/{attempts})")
        sleep(delay)
        prs = fetch()
    return prs


def monitor(gh, now, sleep=time.sleep, dry_run=False, summary=print, page_to=(),
            max_fix_requests=None):
    budget = MAX_FIX_REQUESTS if max_fix_requests is None else int(max_fix_requests)
    act = (lambda *a, **k: None) if dry_run else None
    add_label = act or gh.add_label
    remove_label = act or gh.remove_label
    comment = act or gh.comment
    assign = act or gh.assign
    if dry_run:
        print("DRY RUN — every line below says what WOULD be written; nothing is.")
    else:
        gh.ensure_label()

    tally = {"open": 0, "conflicted": 0, "requested": [], "paged": [], "escalated": [],
             "cleared": [], "unsettled": [], "unpaged": []}

    def page(n, recipients):
        """Assign whoever the page names; a page that names nobody fails the run, said."""
        if recipients[0]:
            assign(n, recipients[0])
        else:
            tally["unpaged"].append(n)
            print(f"::warning::#{n}: the page reaches NOBODY — {recipients[1]}")

    prs = settle(gh.open_prs, sleep)
    tally["open"] = len(prs)
    for pr in prs:
        n, labeled = pr["number"], LABEL in pr["labels"]
        if pr["isDraft"]:
            if labeled:  # drafts are not monitored, so a label on one would be a lie
                remove_label(n)
                tally["cleared"].append(n)
                print(f"#{n}: draft — stale label removed")
            continue
        if pr["mergeable"] == "UNKNOWN":
            tally["unsettled"].append(n)
            print(f"#{n}: mergeability never settled — NOT judged this run")
            continue
        if pr["mergeable"] == "MERGEABLE":
            if labeled:
                remove_label(n)
                tally["cleared"].append(n)
                print(f"#{n}: mergeable again — label removed")
            continue
        tally["conflicted"] += 1
        state = episode_state(gh.comments(n))
        if not labeled:
            episode = state["opened"] + 1
            add_label(n)  # the dedupe key first: a mid-step failure re-alerts, never double-posts
            stacked = not pr["isCrossRepository"] and is_stacked(pr, gh)
            if pr["isCrossRepository"] or stacked or state["requests"] >= budget:
                reason = "fork" if pr["isCrossRepository"] else ("stacked" if stacked else "budget")
                recipients = page_recipients(pr, gh, page_to)
                comment(n, page_body(pr, episode, reason, base_of(pr, gh), recipients, budget,
                                     default=gh.default_branch if stacked else None))
                page(n, recipients)
                tally["paged"].append(n)
                print(f"#{n}: CONFLICTING — paged ({reason}), episode {episode}")
            else:
                comment(n, request_body(pr, episode, state["requests"] + 1, base_of(pr, gh), budget))
                tally["requested"].append(n)
                print(f"#{n}: CONFLICTING — fix requested, episode {episode}")
            continue
        verdict, why = decide_ongoing(state, now)
        if verdict == "escalate":
            recipients = page_recipients(pr, gh, page_to)
            comment(n, escalation_body(pr, state["episode"], why, base_of(pr, gh), recipients))
            page(n, recipients)
            tally["escalated"].append(n)
        print(f"#{n}: still CONFLICTING — {verdict}: {why}")

    for pr in gh.closed_labeled_prs():
        remove_label(pr["number"])
        tally["cleared"].append(pr["number"])
        print(f"#{pr['number']}: closed with the label still on — removed")

    lines = [
        "### PR conflict monitor" + (" (dry run — nothing written)" if dry_run else ""),
        f"- Asked: mergeability of {tally['open']} open PR(s), and which closed PRs still carry `{LABEL}`.",
        f"- Conflicted: {tally['conflicted']} · fix requested: {tally['requested'] or 'none'} · "
        f"paged: {tally['paged'] or 'none'} · escalated: {tally['escalated'] or 'none'} · "
        f"labels cleared: {tally['cleared'] or 'none'}",
    ]
    if tally["unsettled"]:
        lines.append(f"- **COULD NOT TELL** for {tally['unsettled']}: GitHub never finished computing "
                     "mergeability. This run is not a clean result for those PRs.")
    if tally["unpaged"]:
        lines.append(f"- **PAGED NOBODY** for {tally['unpaged']}: the page was posted, but no person it "
                     "@mentions would be notified. Set the PR_CONFLICT_PAGE_TO repository variable.")
    summary("\n".join(lines))
    return 1 if (tally["unsettled"] or tally["unpaged"]) else 0


# ── wake (a person's machine) ────────────────────────────────────────────────

FIX_PROMPT = """\
PR #{number} (branch `{branch}`) now has merge conflicts with `{base}`: `{base}` moved after this PR \
went green, so its required checks are not running. You are in this branch's worktree. Resolve it:

1. Confirm `git status` is clean and you are on `{branch}`.
2. `git fetch origin {base} && git merge origin/{base}` — merge, do not rebase. A rebase ends in a \
force-push under a branch a session may still hold, and stops mid-way on a detached HEAD.
3. Resolve each conflict by reading BOTH sides and keeping both intents. Where they genuinely \
contradict, do not pick one: stop and say what contradicts.
4. Run the project's local checks (CLAUDE.md names them), commit the merge with `git commit -F`, \
and `git push` (never --force).
5. Watch CI to green: `gh pr checks {number} --watch`.

Never merge this PR, never approve it, never add or remove labels. If you cannot resolve it \
safely, push nothing and end with a short account of what conflicts and why."""


def agent_env_markers_present(env):
    return [m for m in AGENT_ENV_MARKERS if m in env]  # presence, not truthiness


def refuse_waker(env, dry_run, uid, dispatcher_env):
    """Raise Refusal when this process must not start sessions. Checked before anything
    is read or written. A dry run starts nothing, so it is never refused."""
    if dry_run:
        return
    found = agent_env_markers_present(env)
    if found:
        raise Refusal(
            f"REFUSED: `wake` starts model sessions, and this is an agent environment ({', '.join(found)} set).\n"
            "  A session that launches its own sessions spends money nobody approved. A PERSON installs\n"
            "  this (scripts/pipeline_conflict_waker_setup.py run). Read-only meanwhile: wake --dry-run")
    if uid == 0:
        raise Refusal(
            "REFUSED: `wake` as root. A fix session runs as whoever runs the waker, and root owns no\n"
            "  local session's worktree. Install it for the person whose sessions it wakes.")
    if dispatcher_env and os.path.exists(dispatcher_env):
        raise Refusal(
            f"REFUSED: `wake` as a dispatcher's role account ({dispatcher_env} exists).\n"
            "  That account's sessions are sandboxed and a waker is not: a fix started here would run\n"
            "  outside the sandbox, over a branch a sandboxed session wrote. A dispatcher's conflicts\n"
            "  go back through its tracker thread — the Stage E bounce driver's `conflict` action.")


def claude_project_dir(claude_dir, path):
    """Where Claude Code keeps the transcripts of sessions whose working directory was
    `path`: every non-alphanumeric character of the path becomes '-'."""
    return os.path.join(claude_dir, "projects", re.sub(r"[^A-Za-z0-9]", "-", path))


def local_session_evidence(worktree, claude_dir, uid):
    """True when THIS uid owns the worktree AND its own Claude Code has worked there —
    the positive proof that a branch belongs to a local session rather than a
    dispatcher's. A sandboxed session runs as another account and cannot write here."""
    try:
        if os.stat(worktree).st_uid != uid:
            return False
    except OSError:
        return False
    for path in dict.fromkeys((worktree, os.path.realpath(worktree))):
        d = claude_project_dir(claude_dir, path)
        try:
            if os.stat(d).st_uid == uid and any(n.endswith(".jsonl") for n in os.listdir(d)):
                return True
        except OSError:
            continue
    return False


def worktrees_by_branch(repo_dir, runner):
    r = runner(["git", "-C", repo_dir, "worktree", "list", "--porcelain"])
    if r.returncode != 0:
        raise CouldNotTell(f"git worktree list failed: {r.stderr.strip()[-300:]}")
    out, path = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/") and path:
            out[line[len("branch refs/heads/"):]] = path
    return out


def _lock(lock_dir, repo, number):
    os.makedirs(lock_dir, exist_ok=True)
    key = hashlib.sha256(f"{repo}#{number}".encode()).hexdigest()[:16]
    fh = open(os.path.join(lock_dir, f"{key}.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def queue_problem(max_sessions, timeout_min, budget_usd):
    """A usage error in the bounds themselves, or None."""
    if max_sessions < 1 or timeout_min < 1 or budget_usd <= 0:
        return "--max-sessions and --timeout-min must be at least 1, and --max-budget-usd above 0"
    if max_sessions * timeout_min > RESULT_DEADLINE_MIN - QUEUE_MARGIN_MIN:
        return (f"--max-sessions {max_sessions} x --timeout-min {timeout_min} = "
                f"{max_sessions * timeout_min} min, past the monitor's {RESULT_DEADLINE_MIN}-min result "
                f"deadline less {QUEUE_MARGIN_MIN} min of margin: the last queued session would still be "
                "working when the monitor pages about it")
    return None


def new_tally():
    return {"pending": 0, "woken": [], "declined": [], "elsewhere": [], "not_local": [],
            "busy": [], "deferred": [], "outcomes": {}, "problems": []}


def wake(gh, repo_dir, now, *, runner=run, env=None, sleep=time.sleep, dry_run=False,
         claude_bin="claude", resume_mode="from-pr", claude_args=(), budget_usd=DEFAULT_BUDGET_USD,
         timeout_min=DEFAULT_TIMEOUT_MIN, lock_dir=None, sessions_left=DEFAULT_MAX_SESSIONS,
         claude_dir=None, uid=None, dispatcher_env=DISPATCHER_ENV_FILE):
    """One repository's share of a pass. Returns its tally; raises CouldNotTell when the
    repository cannot be read at all, and Refusal before anything is read."""
    env = os.environ if env is None else env
    uid = os.getuid() if uid is None else uid
    refuse_waker(env, dry_run, uid, os.path.expanduser(dispatcher_env) if dispatcher_env else None)
    lock_dir = lock_dir or os.path.join(tempfile.gettempdir(), "pr-conflict-waker")
    claude_dir = claude_dir or env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    trees = worktrees_by_branch(repo_dir, runner)
    tally = new_tally()
    candidates = [p for p in gh.open_prs()
                  if LABEL in p["labels"] and not p["isDraft"] and not p["isCrossRepository"]]
    claimed = []  # (number, branch, worktree, episode, lock, base)
    try:
        for pr in candidates:
            n, branch = pr["number"], pr["headRefName"]
            state = episode_state(gh.comments(n))
            latest = state["latest"]
            if not latest or latest["kind"] != "request" or state["escalated"]:
                print(f"#{n}: no open fix request")
                continue
            if state["ack"] or state["result"]:
                print(f"#{n}: request already claimed")
                continue
            if pr["mergeable"] == "MERGEABLE":
                print(f"#{n}: already mergeable — the monitor clears the label on its next run")
                continue
            tally["pending"] += 1
            wt = trees.get(branch)
            if not wt:
                tally["elsewhere"].append(n)
                print(f"#{n}: no worktree on this machine holds `{branch}` — left for a machine that does")
                continue
            if not local_session_evidence(wt, claude_dir, uid):
                tally["not_local"].append(n)
                print(f"#{n}: `{branch}` is checked out here, but no Claude Code session of this user has "
                      "worked in that worktree — not a local session's PR. A dispatcher's PR is re-prompted "
                      "in its tracker thread by the bounce driver; anything else is paged by the monitor")
                continue
            lock = _lock(lock_dir, f"{gh.owner}/{gh.name}", n)
            if lock is None:
                tally["busy"].append(n)
                print(f"#{n}: another waker pass is working it")
                continue
            state = episode_state(gh.comments(n))  # re-read under the lock: a finished pass may have claimed it
            if state["ack"] or state["result"]:
                lock.close()
                print(f"#{n}: claimed by another pass while waiting for the lock")
                continue
            episode = state["episode"]
            base = base_of(pr, gh)
            dirty = runner(["git", "-C", wt, "status", "--porcelain"])
            reason = None
            if not (BRANCH_RE.match(branch) and BRANCH_RE.match(base)):
                reason = "the branch or base name has characters the waker will not put in a prompt"
            elif dirty.returncode != 0:
                reason = "`git status` failed in the worktree"
            elif dirty.stdout.strip():
                reason = "the worktree has uncommitted changes — a session may be mid-work there"
            if reason:
                lock.close()
                tally["declined"].append(n)
                print(f"#{n}: declined — {reason}")
                if not dry_run:
                    gh.comment(n, "\n".join([marker("result", episode, outcome="declined"),
                                             f"The conflict waker declined to start a fix: {reason}. "
                                             "A person needs to look."]))
                continue
            if len(claimed) >= sessions_left:
                lock.close()
                tally["deferred"].append(n)
                print(f"#{n}: over this pass's session cap — NOT acknowledged, so the monitor pages a person "
                      f"owner unless a later pass claims it within {ACK_DEADLINE_MIN} min of the request")
                continue
            claimed.append((n, branch, wt, episode, lock, base))

        if dry_run:
            for n, branch, *_rest in claimed:
                tally["woken"].append(n)
                print(f"#{n}: would wake a session in the worktree for `{branch}`")
            return tally

        # Acknowledge EVERY session this pass will run before the first one starts: a queued
        # request must not miss the ack deadline while an earlier session is still working.
        runnable = []
        for item in claimed:
            n, episode = item[0], item[3]
            try:
                gh.comment(n, "\n".join([
                    marker("ack", episode),
                    f"Conflict waker: a fix session starts in this branch's worktree on this pass "
                    f"(spend cap ${budget_usd:g}, timeout {timeout_min} min). It never merges or approves."]))
                runnable.append(item)
            except CouldNotTell as e:
                tally["problems"].append(f"#{n}: the ack could not be posted, so no session was started: {e}")
                print(f"#{n}: COULD NOT acknowledge — no session started: {e}")

        for n, branch, wt, episode, _held, base in runnable:
            cmd = [claude_bin, "-p", FIX_PROMPT.format(number=n, branch=branch, base=base),
                   "--max-budget-usd", f"{budget_usd:g}"]
            cmd += {"from-pr": ["--from-pr", str(n)], "continue": ["--continue"], "fresh": []}[resume_mode]
            cmd += list(claude_args)
            timed_out, rc, output = False, None, ""
            try:
                r = runner(cmd, cwd=wt, timeout=timeout_min * 60)
                rc, output = r.returncode, (r.stdout or "") + (r.stderr or "")
            except subprocess.TimeoutExpired as e:
                timed_out = True
                output = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            except OSError as e:
                rc, output = 127, f"could not start {claude_bin}: {e}"
            tally["woken"].append(n)
            ran = "timed out" if timed_out else f"exited {rc}"
            try:
                merge_state = settle(lambda: [{"number": n, "isDraft": False, "mergeable": gh.mergeable(n)}],
                                     sleep, attempts=6, delay=10)[0]["mergeable"]
            except CouldNotTell as e:
                merge_state = "UNREADABLE"
                tally["problems"].append(f"#{n}: mergeability could not be read after the session: {e}")
            outcome = {"MERGEABLE": "resolved", "CONFLICTING": "unresolved"}.get(merge_state, "unknown")
            if outcome != "resolved" and (timed_out or rc):
                outcome = "failed"
            tally["outcomes"][n] = outcome
            try:
                gh.comment(n, "\n".join([
                    marker("result", episode, outcome=outcome),
                    f"Conflict waker: the fix session {ran}; GitHub now reports mergeable = {merge_state}.",
                    "", "<details><summary>Last lines of the session's output</summary>", "",
                    "```", neutralize(output) or "(no output)", "```", "</details>"]))
            except CouldNotTell as e:
                tally["problems"].append(f"#{n}: the result could not be posted: {e}")
            print(f"#{n}: fix session {ran} — outcome {outcome}")
    finally:
        for item in claimed:
            item[4].close()
    return tally


def summarize(tally, dry_run):
    return (f"asked: open `{LABEL}` PRs with an unclaimed fix request; pending {tally['pending']} · "
            f"{'would wake' if dry_run else 'woke'} {tally['woken'] or 'none'} · declined "
            f"{tally['declined'] or 'none'} · not a local session's {tally['not_local'] or 'none'} · "
            f"not on this machine {tally['elsewhere'] or 'none'} · busy {tally['busy'] or 'none'} · "
            f"over the session cap {tally['deferred'] or 'none'}")


def write_heartbeat(state_dir, **fields):
    """Atomic and best effort: a heartbeat that cannot be written is said on stderr and
    never changes the pass's own exit code."""
    doc = {"schema": WAKER_HEARTBEAT_SCHEMA, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    doc.update(fields)
    path = os.path.join(state_dir, "heartbeat.json")
    try:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        with open(path + ".tmp", "w") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print(f"NOTE: could not write the heartbeat {path}: {e}", file=sys.stderr)
    return doc


def wake_pass(repo_dirs, gh_for, now, *, state_dir, dry_run=False, max_sessions=DEFAULT_MAX_SESSIONS,
              env=None, uid=None, dispatcher_env=DISPATCHER_ENV_FILE, **wake_kw):
    """The whole pass the LaunchAgent runs: every repository, one shared session cap, one
    heartbeat. Exit 0, or 1 when it could not tell; a Refusal propagates before anything
    is read or written, heartbeat included."""
    env = os.environ if env is None else env
    uid = os.getuid() if uid is None else uid
    refuse_waker(env, dry_run, uid, os.path.expanduser(dispatcher_env) if dispatcher_env else None)
    state_dir = os.path.expanduser(state_dir)
    wake_kw.setdefault("lock_dir", os.path.join(state_dir, "locks"))
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    beat = (lambda **f: None) if dry_run else (lambda **f: write_heartbeat(state_dir, **f))
    beat(started_at=started, result="running", repos=list(repo_dirs))
    total = new_tally()
    for repo_dir in repo_dirs:
        try:
            tally = wake(gh_for(repo_dir), repo_dir, now, dry_run=dry_run, env=env, uid=uid,
                         dispatcher_env=dispatcher_env,
                         sessions_left=max_sessions - len(total["woken"]), **wake_kw)
        except CouldNotTell as e:
            total["problems"].append(f"{repo_dir}: {e}")
            print(f"COULD NOT TELL for {repo_dir}: {e}", file=sys.stderr)
            continue
        total["pending"] += tally["pending"]
        total["outcomes"].update({f"{repo_dir}#{n}": o for n, o in tally["outcomes"].items()})
        for key in ("woken", "declined", "elsewhere", "not_local", "busy", "deferred", "problems"):
            total[key] += tally[key]
    print(summarize(total, dry_run))
    for problem in total["problems"]:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    unknown = sorted(k for k, o in total["outcomes"].items() if o == "unknown")
    if unknown:
        print(f"COULD NOT TELL whether the fix landed for {unknown}: GitHub never settled mergeability",
              file=sys.stderr)
    bad = [o for o in total["outcomes"].values() if o in ("failed", "unknown")]
    result = ("problems" if (total["problems"] or bad) else
              "ok" if (total["woken"] or total["declined"]) else "idle")
    beat(started_at=started, finished_at=dt.datetime.now(dt.timezone.utc).isoformat(), result=result,
         repos=list(repo_dirs), pending=total["pending"], woken=total["woken"],
         declined=total["declined"], not_local=total["not_local"], elsewhere=total["elsewhere"],
         busy=total["busy"], deferred=total["deferred"], outcomes=total["outcomes"],
         problems=total["problems"][:20])
    return 1 if (total["problems"] or unknown) else 0


# ── selftest ─────────────────────────────────────────────────────────────────

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)


def _iso(minutes_ago):
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def _bot(body, minutes_ago):
    return {"body": body, "user": {"login": BOT_LOGIN, "type": "Bot"},
            "author_association": "NONE", "created_at": _iso(minutes_ago)}


def _human(body, minutes_ago, assoc="OWNER"):
    return {"body": body, "user": {"login": "someone", "type": "User"},
            "author_association": assoc, "created_at": _iso(minutes_ago)}


class FakeGh:
    owner, name = "acme", "widgets"

    def __init__(self, prs=(), comments=None, closed=(), mergeable_after="MERGEABLE",
                 default_branch="main", owner_type="Organization"):
        self.prs = [dict(p) for p in prs]
        self._comments = comments or {}
        self.closed = list(closed)
        self.writes = []
        self.mergeable_after = mergeable_after
        self.default_branch, self.owner_type = default_branch, owner_type

    def open_prs(self):
        return [dict(p) for p in self.prs]

    def closed_labeled_prs(self):
        return self.closed

    def comments(self, n):
        return list(self._comments.get(n, []))

    def ensure_label(self):
        self.writes.append(("ensure_label",))

    def add_label(self, n):
        self.writes.append(("add_label", n))

    def remove_label(self, n):
        self.writes.append(("remove_label", n))

    def comment(self, n, body):
        # The monitor posts as the Actions bot, the waker as a writer — as in production.
        self.writes.append(("comment", n, body))
        kind = MARKER_RE.match(body.split("\n", 1)[0])
        author = _bot if kind and kind.group(1) in BOT_KINDS else _human
        self._comments.setdefault(n, []).append(author(body, 0))

    def assign(self, n, logins):
        self.writes.append(("assign", n, tuple(logins)))

    def mergeable(self, n):
        self.writes.append(("read_mergeable", n))
        return self.mergeable_after


def _pr(n, mergeable="CONFLICTING", labels=(), draft=False, fork=False, branch=None, base="main",
        author="dev", author_type="User"):
    return {"number": n, "mergeable": mergeable, "labels": list(labels), "isDraft": draft,
            "isCrossRepository": fork, "headRefName": branch or f"feat/pr-{n}", "baseRefName": base,
            "author": author, "authorType": author_type}


def selftest():
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    quiet = lambda *_: None  # noqa: E731

    def run_monitor(gh, **kw):
        out = []
        rc = monitor(gh, NOW, sleep=quiet, summary=out.append, **kw)
        return rc, "\n".join(out)

    def kinds(gh, n):
        return [w[0] for w in gh.writes if len(w) > 1 and w[1] == n]

    # 1. a new conflict: label, then ONE request comment, and no page.
    gh = FakeGh([_pr(1)])
    rc, _ = run_monitor(gh)
    expect(rc == 0 and kinds(gh, 1) == ["add_label", "comment"], f"new conflict: {gh.writes}")
    body = [w for w in gh.writes if w[0] == "comment"][0][2]
    expect(body.startswith(marker("request", 1)) and "cc @" not in body,
           "a request must carry the marker on line 1 and must not page anyone")

    # 2. budget spent → page (assign + @mention the PR's author, said by role), never a fourth request.
    prior = [_bot(marker("request", i), 500 - i) for i in (1, 2, 3)]
    gh = FakeGh([_pr(2)], {2: prior})
    run_monitor(gh)
    body = [w for w in gh.writes if w[0] == "comment"][0][2]
    expect(body.startswith(marker("page", 4, reason="budget")) and "cc @dev (the PR's author)" in body
           and ("assign", 2, ("dev",)) in gh.writes, f"budget spent must page the author: {gh.writes}")

    # 2b. A REPOSITORY WHERE NOTHING ANSWERS pages at the first conflict (KIT-175). Asking
    #     for a fix where no waker and no dispatcher exists posts a comment addressed to
    #     nobody and delays the person who can actually fix it by a whole tick.
    gh = FakeGh([_pr(21)])
    rc0, _ = run_monitor(gh, max_fix_requests=0)
    body0 = [w for w in gh.writes if w[0] == "comment"][0][2]
    expect(rc0 == 0 and body0.startswith(marker("page", 1, reason="budget")),
           f"--max-fix-requests 0 must page at detection: {gh.writes}")
    expect("A fix has been requested" not in body0 and "cc @dev (the PR's author)" in body0
           and ("assign", 21, ("dev",)) in gh.writes,
           "…and it must page a person, not ask an answerer that does not exist")
    expect("nothing in this repository answers" in body0 and "0 automated fix attempts" not in body0,
           f"…and it must say WHY in its own words, not as a count of zero: {body0[:200]}")
    # …while the default is untouched, and a raised budget asks more times before paging.
    gh = FakeGh([_pr(22)], {22: [_bot(marker("request", i), 500 - i) for i in (1, 2, 3)]})
    run_monitor(gh, max_fix_requests=5)
    body5 = [w for w in gh.writes if w[0] == "comment"][0][2]
    expect(body5.startswith(marker("request", 4)) and "attempt 4 of 5" in body5,
           f"a raised budget must keep asking, and say the budget it is counting: {body5[:200]}")
    expect(main(["monitor", "--repo", "acme/widgets", "--max-fix-requests", "-1",
                 "--dry-run"]) == 2,
           "a negative budget must be a usage error, never a silent default")

    # 3. a fork is paged, never requested.
    gh = FakeGh([_pr(3, fork=True)])
    run_monitor(gh)
    expect([w[2] for w in gh.writes if w[0] == "comment"][0].startswith(marker("page", 1, reason="fork")),
           "a fork PR must be paged, not requested")

    # 4. the budget cannot be reset by a writer's comment impersonating the bot.
    forged = [_human(marker("request", 9), 10)]
    expect(episode_state(prior + forged)["requests"] == 3, "a non-bot request marker must not count")

    # 5. ongoing: waiting inside the ack deadline is quiet; past it escalates exactly once.
    req = _bot(marker("request", 1), 5)
    gh = FakeGh([_pr(5, labels=[LABEL])], {5: [req]})
    run_monitor(gh)
    expect(kinds(gh, 5) == [], f"inside the ack deadline must stay quiet: {gh.writes}")
    req = _bot(marker("request", 1), ACK_DEADLINE_MIN + 5)
    gh = FakeGh([_pr(5, labels=[LABEL])], {5: [req]})
    run_monitor(gh)
    expect(kinds(gh, 5) == ["comment", "assign"], f"a missed ack must escalate: {gh.writes}")
    gh.writes.clear()
    run_monitor(gh)
    expect(kinds(gh, 5) == [], f"an escalation must be posted once, not every tick: {gh.writes}")

    # 6. ack from a non-writer does not count; a pre-posted ack for a future episode does not count.
    req = _bot(marker("request", 1), ACK_DEADLINE_MIN + 5)
    drive_by = _human(marker("ack", 1), 1, assoc="NONE")
    early = _human(marker("ack", 1), ACK_DEADLINE_MIN + 30)
    expect(decide_ongoing(episode_state([req, drive_by]), NOW)[0] == "escalate",
           "an ack from a non-writer must not hold off escalation")
    expect(decide_ongoing(episode_state([early, req]), NOW)[0] == "escalate",
           "an ack posted before the request must not count")

    # 7. acked: quiet inside the result deadline, escalate past it.
    req = _bot(marker("request", 1), 200)
    expect(decide_ongoing(episode_state([req, _human(marker("ack", 1), 30)]), NOW)[0] == "quiet",
           "an acknowledged fix inside the result deadline must stay quiet")
    expect(decide_ongoing(episode_state([req, _human(marker("ack", 1), RESULT_DEADLINE_MIN + 1)]), NOW)[0]
           == "escalate", "an ack with no result past the deadline must escalate")

    # 8. a result CLAIMING resolved while GitHub still says CONFLICTING escalates: truth is `mergeable`.
    res = _human(marker("result", 1, outcome="resolved"), 1)
    gh = FakeGh([_pr(8, labels=[LABEL])], {8: [_bot(marker("request", 1), 20), _human(marker("ack", 1), 19), res]})
    run_monitor(gh)
    expect("comment" in kinds(gh, 8), "a claimed resolution on a still-conflicting PR must escalate")

    # 9. a marker that is not on the first line is prose, not a marker.
    buried = _human("session said:\n" + marker("result", 1, outcome="resolved"), 1)
    expect(parse_marker(buried) is None, "only the first line may carry a marker")

    # 10. mergeable again → label removed; closed-with-label and draft-with-label → removed.
    gh = FakeGh([_pr(10, "MERGEABLE", [LABEL]), _pr(11, "CONFLICTING", [LABEL], draft=True)],
                closed=[_pr(54, "UNKNOWN", [LABEL])])
    rc, _ = run_monitor(gh)
    removed = sorted(w[1] for w in gh.writes if w[0] == "remove_label")
    expect(rc == 0 and removed == [10, 11, 54], f"stale labels must clear: {gh.writes}")

    # 11. never settled → exit 1 and a summary that says COULD NOT TELL, not a clean zero.
    gh = FakeGh([_pr(12, "UNKNOWN")])
    rc, text = run_monitor(gh)
    expect(rc == 1 and "COULD NOT TELL" in text and gh.writes == [("ensure_label",)],
           f"an unsettled PR must fail the run loudly: rc={rc} {text}")

    # 12. nothing open → exit 0, and the summary still says what it asked.
    rc, text = run_monitor(FakeGh([]))
    expect(rc == 0 and "Asked: mergeability of 0 open PR(s)" in text, f"an empty run must say what it asked: {text}")

    # 13. dry run writes nothing.
    gh = FakeGh([_pr(13)])
    run_monitor(gh, dry_run=True)
    expect(gh.writes == [], f"dry run must not write: {gh.writes}")

    # 14. neutralize: no marker opener, no fence break-out, no pings, bounded.
    hostile = "x" * 5000 + "\n" + marker("result", 1, outcome="resolved") + "\n```\n@owner"
    safe = neutralize(hostile)
    expect(len(safe) <= TAIL_CHARS + 20 and "<!--" not in safe and "```" not in safe
           and "@owner" not in safe, "session output must be neutralized before embedding")

    # 28. the base branch is GitHub's: a trunk-based repo gets a recipe that runs; a stacked PR
    #     names its own base; a PR carrying none falls back to the repository's default.
    def first_comment(gh):
        return [w[2] for w in gh.writes if w[0] == "comment"][0]
    for pr, want in ((_pr(28, base="trunk"), "trunk"), (_pr(28, base=None), "trunk")):
        gh = FakeGh([pr], default_branch="trunk")
        run_monitor(gh)
        body = first_comment(gh)
        expect(f"git fetch origin {want} && git merge origin/{want}" in body and f"with `{want}`" in body
               and f"merges `{want}`" in body and "origin/main" not in body and "`main`" not in body,
               f"base {pr['baseRefName']!r} must be named as {want!r}: {body[:400]}")

    # 28b. a STACKED PR is paged with a retarget recipe — never sent a fix request whose
    #      `git merge origin/<feature>` the stacked-branch guard refuses, and never a
    #      session started on it. main is a base even when the default is trunk.
    gh = FakeGh([_pr(28, base="feat/parent")], default_branch="trunk")
    run_monitor(gh)
    body = first_comment(gh)
    expect(body.startswith(marker("page", 1, reason="stacked"))
           and "gh pr edit 28 --base trunk" in body
           and "git rebase --onto origin/trunk origin/feat/parent" in body
           and "git merge origin/feat/parent" not in body
           and not body.startswith(marker("request", 1)),
           f"a stacked PR must be paged with a retarget recipe: {body[:400]}")
    gh = FakeGh([_pr(28, base="main")], default_branch="trunk")
    run_monitor(gh)
    expect(first_comment(gh).startswith(marker("request", 1)),
           "a PR into main is not stacked, even on a trunk-default repository")

    # 29. WHO A PAGE REACHES. An organization's @mention notifies nobody, so: the PR's author;
    #     else a person who owns the repo; else say NOBODY was paged and fail the run. A
    #     configured --page-to wins, and every page says which rule chose its recipient.
    budget = [_bot(marker("request", i), 500 - i) for i in (1, 2, 3)]
    gh = FakeGh([_pr(29, author="deploy-app", author_type="Bot")], {29: list(budget)}, owner_type="Organization")
    rc, text = run_monitor(gh)
    body = first_comment(gh)
    expect(rc == 1 and "**Nobody was paged:**" in body and "PR_CONFLICT_PAGE_TO" in body and "cc @" not in body
           and "PAGED NOBODY" in text and not any(w[0] == "assign" for w in gh.writes),
           f"a bot's PR on an org repo must say nobody was paged and fail the run: rc={rc} {body} {text}")
    gh = FakeGh([_pr(29, author="deploy-app", author_type="Bot")], {29: list(budget)}, owner_type="User")
    rc, _ = run_monitor(gh)
    expect(rc == 0 and "cc @acme (the repository owner — the PR's author is a bot)" in first_comment(gh)
           and ("assign", 29, ("acme",)) in gh.writes, f"a user-owned repo pages its owner: {gh.writes}")
    gh = FakeGh([_pr(29, author="deploy-app", author_type="Bot")], {29: list(budget)})
    rc, _ = run_monitor(gh, page_to=["alice", "acme/on-call"])
    expect(rc == 0 and "cc @alice, @acme/on-call (PR_CONFLICT_PAGE_TO)" in first_comment(gh)
           and ("assign", 29, ("alice", "acme/on-call")) in gh.writes, f"--page-to must win: {gh.writes}")
    stalled = FakeGh([_pr(30, labels=[LABEL], author=None, author_type=None)],
                     {30: [_bot(marker("request", 1), ACK_DEADLINE_MIN + 5)]})
    rc, text = run_monitor(stalled)
    expect(rc == 1 and "**Nobody was paged:**" in first_comment(stalled) and "PAGED NOBODY" in text,
           f"an escalation that reaches nobody must fail the run too: rc={rc} {text}")
    expect(parse_page_to("@alice, bob acme/on-call") == (["alice", "bob", "acme/on-call"], [])
           and parse_page_to("alice;rm")[1] == ["alice;rm"] and parse_page_to("") == ([], []),
           "PR_CONFLICT_PAGE_TO parses logins and team slugs and rejects anything else")
    expect(main(["monitor", "--repo", "acme/widgets", "--page-to", "x<y", "--dry-run"]) == 2,
           "a --page-to that is not a login is a usage error before anything is read")

    # 30. the real transport reads the repository ONCE, names its default branch in the label,
    #     and assigns people, never a team.
    seen30 = []

    def rec30(cmd, cwd=None, input=None, timeout=None):
        seen30.append((cmd, input))
        out = ('{"default_branch": "trunk", "owner": {"type": "Organization"}}'
               if cmd[3:5] == ["GET", "repos/acme/widgets"] else "{}")
        return subprocess.CompletedProcess(cmd, 0, out, "")
    real = Gh("acme/widgets", runner=rec30)
    real.ensure_label()
    real.ensure_label()
    reads = [c for c, _i in seen30 if c[3:5] == ["GET", "repos/acme/widgets"]]
    labels = [json.loads(i) for c, i in seen30 if c[4] == "repos/acme/widgets/labels"]
    expect(len(reads) == 1 and real.owner_type == "Organization"
           and all("trunk" in d["description"] and "main" not in d["description"]
                   and len(d["description"]) <= LABEL_DESCRIPTION_MAX for d in labels),
           f"one repository read, and the label names the real default branch: {seen30}")
    seen30.clear()
    real.assign(4, ["alice", "acme/on-call"])
    real.assign(5, ["acme/on-call"])
    expect([json.loads(i) for _c, i in seen30] == [{"assignees": ["alice"]}],
           f"assign must carry people only, and skip a call with none: {seen30}")
    empty = Gh("acme/widgets", runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "{}", ""))
    try:
        empty.default_branch
        expect(False, "a repository record with no default branch must not be guessed")
    except CouldNotTell:
        pass

    # ── wake ──
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        wt = os.path.join(tmp, "wt-7")
        g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)  # noqa: E731
        os.makedirs(repo)
        g("init", "-q", "-b", "main")
        g("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "base")
        g("worktree", "add", "-q", "-b", "feat/pr-7", wt)
        g("worktree", "add", "-q", "-b", "feat/pr-8", os.path.join(tmp, "wt-8"))
        with open(os.path.join(tmp, "wt-8", "dirty.txt"), "w") as fh:
            fh.write("uncommitted\n")
        g("worktree", "add", "-q", "-b", "feat/pr-5", os.path.join(tmp, "wt-5"))
        claude_dir = os.path.join(tmp, "claude-config")

        def local_session(path):
            """What this user's own Claude Code leaves behind in a worktree it worked in."""
            d = claude_project_dir(claude_dir, os.path.realpath(path))
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "session.jsonl"), "w").close()

        # wt-7 and wt-8 are local sessions' worktrees; wt-5 holds a branch no local session
        # ever worked in — the shape of a dispatcher's PR someone checked out by hand.
        local_session(wt)
        local_session(os.path.join(tmp, "wt-8"))
        no_dispatcher = os.path.join(tmp, "not-a-role-account", "env")

        calls = []

        def runner(cmd, cwd=None, input=None, timeout=None):
            if cmd[0] == "claude":
                calls.append((cmd, cwd, timeout))
                return subprocess.CompletedProcess(cmd, 0, "resolved it\n" + marker("result", 1, outcome="resolved"), "")
            return run(cmd, cwd=cwd, input=input, timeout=timeout)

        def fresh_gh(**kw):
            prs = [_pr(7, labels=[LABEL]), _pr(8, labels=[LABEL]), _pr(9, labels=[LABEL]),
                   _pr(6, labels=[LABEL])]
            comments = {7: [_bot(marker("request", 1), 2)], 8: [_bot(marker("request", 1), 2)],
                        9: [_bot(marker("request", 1), 2)],
                        6: [_bot(marker("request", 1), 9), _human(marker("ack", 1), 8)]}
            return FakeGh(prs, comments, **kw)

        common = dict(runner=runner, sleep=quiet, lock_dir=os.path.join(tmp, "locks"),
                      claude_dir=claude_dir, dispatcher_env=no_dispatcher)

        def firsts_for(gh, n):
            return [w[2].split("\n", 1)[0] for w in gh.writes if w[0] == "comment" and w[1] == n]

        # 15. an agent environment is refused before anything is read or written.
        gh = fresh_gh()
        try:
            wake(gh, repo, NOW, env={"CLAUDECODE": ""}, **common)
            expect(False, "wake in an agent environment must refuse")
        except Refusal:
            expect(gh.writes == [] and calls == [], "a refusal must write nothing and start nothing")

        # 16. dry run is allowed there, and writes and starts nothing.
        gh = fresh_gh()
        wake(gh, repo, NOW, env={"CLAUDECODE": "1"}, dry_run=True, **common)
        expect(gh.writes == [] and calls == [], f"dry run must not write or wake: {gh.writes} {calls}")

        # 17. the real pass: #7 acked BEFORE the session, session in its worktree with the spend
        #     cap and --from-pr, result from GitHub's re-read; #8 declined (dirty); #9 elsewhere;
        #     #6 already claimed.
        gh = fresh_gh()
        tally = wake(gh, repo, NOW, env={}, **common)
        seq = [(w[0], w[1], (w[2].split("\n", 1)[0] if w[0] == "comment" else "")) for w in gh.writes]
        on7 = [s for s in seq if s[1] == 7]
        expect(tally["woken"] == [7] and not tally["problems"], f"wake tally={tally}")
        expect(on7 == [("comment", 7, marker("ack", 1)), ("read_mergeable", 7, ""),
                       ("comment", 7, marker("result", 1, outcome="resolved"))],
               f"ack must precede the session and the result must follow a re-read: {seq}")
        expect(("comment", 8, marker("result", 1, outcome="declined")) in seq, f"dirty worktree must decline: {seq}")
        expect(not any(len(w) > 1 and w[1] in (6, 9) for w in gh.writes), f"#6 claimed and #9 elsewhere must be untouched: {seq}")
        expect(len(calls) == 1 and os.path.realpath(calls[0][1]) == os.path.realpath(wt)
               and "--from-pr" in calls[0][0] and "--max-budget-usd" in calls[0][0],
               f"one session, in #7's worktree, resumed by PR, with a spend cap: {calls}")
        expect("git fetch origin main && git merge origin/main" in calls[0][0][2],
               "the fix prompt must name the PR's own base")
        posted = [w[2] for w in gh.writes if w[0] == "comment" and w[1] == 7][-1]
        expect(posted.count("<!--") == 1 and parse_marker(_human(posted, 0))["attrs"]["outcome"] == "resolved",
               "a marker inside session output must not survive into the result comment")

        # 18. a second pass finds #7 claimed and starts nothing.
        calls.clear()
        wake(gh, repo, NOW, env={}, **common)
        expect(calls == [], "a claimed request must not be woken twice")

        # 19. session exits non-zero and the PR is still conflicted → outcome=failed.
        calls.clear()
        gh = fresh_gh(mergeable_after="CONFLICTING")
        bad = lambda cmd, cwd=None, input=None, timeout=None: (  # noqa: E731
            subprocess.CompletedProcess(cmd, 1, "", "boom") if cmd[0] == "claude" else run(cmd, cwd=cwd))
        wake(gh, repo, NOW, env={}, **{**common, "runner": bad})
        expect(firsts_for(gh, 7)[-1] == marker("result", 1, outcome="failed"),
               f"a failed session must say failed: {firsts_for(gh, 7)}")

        # 20. the prompt forbids merge/approve/labels and never asks for a force-push.
        expect("Never merge" in FIX_PROMPT and "never approve" in FIX_PROMPT
               and "--force-with-lease" not in FIX_PROMPT, "the fix prompt must stay merge-free")

        # 22. WHOSE PR: a branch checked out here that no local Claude Code session of this user
        #     ever worked in is not the waker's — no ack, no session, no declined result. That is
        #     the shape of a dispatcher's PR someone checked out by hand, and its fix belongs in
        #     the session's own sandbox, through the bounce driver.
        calls.clear()
        gh = FakeGh([_pr(5, labels=[LABEL])], {5: [_bot(marker("request", 1), 2)]})
        tally = wake(gh, repo, NOW, env={}, **common)
        expect(tally["not_local"] == [5] and gh.writes == [] and calls == [],
               f"a worktree with no local session must be left untouched: {tally} {gh.writes} {calls}")
        expect(not local_session_evidence(wt, claude_dir, os.getuid() + 1),
               "a worktree owned by another uid is never local evidence, whatever the transcripts say")

        # 23. the waker refuses to run as root or as a dispatcher's role account — before any read.
        role_home = os.path.join(tmp, "role")
        os.makedirs(os.path.join(role_home, ".stage-e"))
        role_env = os.path.join(role_home, ".stage-e", "env")
        open(role_env, "w").close()
        for label, kw in (("root", {"uid": 0}), ("a role account", {"dispatcher_env": role_env})):
            gh = fresh_gh()
            try:
                wake(gh, repo, NOW, env={}, **{**common, **kw})
                expect(False, f"wake as {label} must refuse")
            except Refusal:
                expect(gh.writes == [] and calls == [], f"a refusal as {label} must write and start nothing")
        gh = fresh_gh()
        wake(gh, repo, NOW, env={}, dry_run=True, **{**common, "dispatcher_env": role_env})
        expect(gh.writes == [], "a dry run as a role account reads and writes nothing")
        import pipeline_bounce_local as _bounce
        expect(DISPATCHER_ENV_FILE == _bounce.DEFAULT_ENV_FILE,
               f"the role-account marker must be the bounce driver's env file: {DISPATCHER_ENV_FILE}")

        # 24. the per-pass cap: two claimable requests and room for one — ONE acked and woken, the
        #     other NOT acked (so the monitor pages it) and named. With room for both, BOTH acks
        #     precede the first session, so a queue never misses the ack deadline.
        g("worktree", "add", "-q", "-b", "feat/pr-4", os.path.join(tmp, "wt-4"))
        local_session(os.path.join(tmp, "wt-4"))

        def two():
            return FakeGh([_pr(7, labels=[LABEL]), _pr(4, labels=[LABEL])],
                          {7: [_bot(marker("request", 1), 2)], 4: [_bot(marker("request", 1), 2)]})
        calls.clear()
        gh = two()
        tally = wake(gh, repo, NOW, env={}, sessions_left=1, **common)
        expect(tally["woken"] == [7] and tally["deferred"] == [4] and firsts_for(gh, 4) == []
               and len(calls) == 1, f"over the cap: not acked, not woken, named: {tally} {gh.writes}")
        calls.clear()
        gh = two()
        wake(gh, repo, NOW, env={}, sessions_left=2, **common)
        order = [(w[0], w[1]) for w in gh.writes]
        expect(order[:2] == [("comment", 7), ("comment", 4)] and len(calls) == 2,
               f"every ack must precede the first session: {order}")
        expect(queue_problem(2, 40, 5) is None and queue_problem(3, 40, 5) is not None
               and queue_problem(0, 40, 5) is not None,
               "the cap times the timeout must fit inside the monitor's result deadline")
        expect(main(["wake", "--max-sessions", "4", "--timeout-min", "40", "--dry-run"]) == 2,
               "an unboundable queue is a usage error before anything runs")

        # 25. the heartbeat: a real pass writes `running`, then its result; a dry run writes none; a
        #     repository that cannot be read makes the pass exit 1 and the heartbeat say problems.
        state_dir = os.path.join(tmp, "waker-state")
        beat_path = os.path.join(state_dir, "heartbeat.json")
        pass_kw = {k: v for k, v in common.items() if k != "lock_dir"}
        calls.clear()
        rc = wake_pass([repo], lambda d: FakeGh([]), NOW, state_dir=state_dir, dry_run=True, env={}, **pass_kw)
        expect(rc == 0 and not os.path.exists(beat_path), "a dry run must leave no heartbeat")
        rc = wake_pass([repo], lambda d: FakeGh([]), NOW, state_dir=state_dir, env={}, **pass_kw)
        with open(beat_path) as fh:
            beat = json.load(fh)
        expect(rc == 0 and beat["schema"] == WAKER_HEARTBEAT_SCHEMA and beat["result"] == "idle"
               and beat.get("finished_at"), f"nothing to do must say idle, with a finish time: {beat}")

        def unreadable(d):
            raise CouldNotTell("simulated: gh could not resolve the repository")
        rc = wake_pass([repo], unreadable, NOW, state_dir=state_dir, env={}, **pass_kw)
        with open(beat_path) as fh:
            beat = json.load(fh)
        expect(rc == 1 and beat["result"] == "problems" and beat["problems"],
               f"could-not-tell must exit 1 and say problems: rc={rc} {beat}")
        rc = wake_pass([repo], lambda d: two(), NOW, state_dir=state_dir, env={}, max_sessions=1, **pass_kw)
        with open(beat_path) as fh:
            beat = json.load(fh)
        expect(rc == 0 and beat["result"] == "ok" and beat["woken"] == [7] and beat["deferred"] == [4],
               f"a pass that woke one and capped one must say both: {beat}")
        try:
            os.unlink(beat_path)
            wake_pass([repo], lambda d: FakeGh([]), NOW, state_dir=state_dir, env={"CLAUDECODE": "1"}, **pass_kw)
            expect(False, "wake_pass in an agent environment must refuse")
        except Refusal:
            expect(not os.path.exists(beat_path), "a refused pass must leave no heartbeat")

        # 26. a session whose PR GitHub never settles is `unknown` — and the pass exits 1.
        gh_u = two()
        gh_u.prs = gh_u.prs[:1]
        gh_u.mergeable_after = "UNKNOWN"
        rc = wake_pass([repo], lambda d: gh_u, NOW, state_dir=state_dir, env={}, **pass_kw)
        expect(rc == 1 and firsts_for(gh_u, 7)[-1] == marker("result", 1, outcome="unknown"),
               f"a fix nobody can confirm must say unknown and exit 1: rc={rc} {firsts_for(gh_u, 7)}")

        # 27. the fix session is handed the PR's OWN base, never `main` by assumption: a `develop`
        #     base gets a command that runs; a base the prompt must not carry is declined, said.
        calls.clear()
        gh = FakeGh([_pr(7, labels=[LABEL], base="develop")], {7: [_bot(marker("request", 1), 2)]},
                    default_branch="trunk")
        wake(gh, repo, NOW, env={}, **common)
        prompt = calls[0][0][2] if calls else ""
        expect("git fetch origin develop && git merge origin/develop" in prompt and "origin/main" not in prompt
               and "origin/trunk" not in prompt, f"the prompt must merge the PR's base: {prompt[:300]}")
        calls.clear()
        gh = FakeGh([_pr(7, labels=[LABEL], base="rel ease`x")], {7: [_bot(marker("request", 1), 2)]})
        wake(gh, repo, NOW, env={}, **common)
        expect(calls == [] and firsts_for(gh, 7) == [marker("result", 1, outcome="declined")],
               f"a base the prompt must not carry is declined, never embedded: {gh.writes} {calls}")

    # 21. the real transport: label writes carry only `conflict`; nothing merges or approves.
    seen = []

    def rec(cmd, cwd=None, input=None, timeout=None):
        seen.append((cmd, input))
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    real = Gh("acme/widgets", runner=rec)
    real.add_label(4)
    expect(seen[-1][0][:5] == ["gh", "api", "-X", "POST", "repos/acme/widgets/issues/4/labels"]
           and json.loads(seen[-1][1]) == {"labels": [LABEL]}, f"add_label shape: {seen[-1]}")
    src = open(os.path.abspath(__file__)).read()
    for token in ("pr " + "merge", "merge" + "PullRequest", "enablePullRequest" + "AutoMerge",
                  "/mer" + "ge\"", "APPR" + "OVE", "/rev" + "iews", '"pu' + 'sh"'):
        expect(token not in src, f"the source must build no merge/approve/push path: found {token!r}")

    if failures:
        print("pr_conflict selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("pr_conflict selftest: OK (30 cases)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    m = sub.add_parser("monitor")
    m.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--max-fix-requests", type=int,
                   default=os.environ.get("PR_CONFLICT_MAX_FIX_REQUESTS") or MAX_FIX_REQUESTS,
                   help="automated fix attempts per PR before a person is paged (default %d). "
                        "0 means nothing in this repository answers one: page at the first "
                        "conflict instead of asking" % MAX_FIX_REQUESTS)
    m.add_argument("--page-to", default=os.environ.get("PR_CONFLICT_PAGE_TO", ""),
                   help="logins (or org/team) a page @mentions, comma- or space-separated; default "
                        "PR_CONFLICT_PAGE_TO, else the PR's author, else a person who owns the repo")
    w = sub.add_parser("wake")
    w.add_argument("--repo-dir", action="append", default=[],
                   help="a checkout whose worktrees this waker serves; repeat for several (default .)")
    w.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="where the heartbeat and locks live")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--max-budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    w.add_argument("--timeout-min", type=int, default=DEFAULT_TIMEOUT_MIN)
    w.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS,
                   help="fix sessions per pass, across every --repo-dir")
    w.add_argument("--resume-mode", choices=["from-pr", "continue", "fresh"], default="from-pr")
    w.add_argument("--claude-bin", default="claude")
    w.add_argument("--claude-arg", action="append", default=[],
                   help="passed through to claude, e.g. --claude-arg=--permission-mode --claude-arg=acceptEdits")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        if args.cmd == "monitor":
            if not args.repo or "/" not in args.repo:
                print("monitor needs --repo owner/name (or GITHUB_REPOSITORY)", file=sys.stderr)
                return 2
            if args.max_fix_requests < 0:
                print("monitor: --max-fix-requests cannot be negative (0 means page at the "
                      "first conflict)", file=sys.stderr)
                return 2
            page_to, bad = parse_page_to(args.page_to)
            if bad:
                print(f"monitor: --page-to / PR_CONFLICT_PAGE_TO names {bad}, which is not a GitHub "
                      "login or org/team", file=sys.stderr)
                return 2
            summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

            def summary(text):
                print(text)
                if summary_path:
                    with open(summary_path, "a") as fh:
                        fh.write(text + "\n")

            return monitor(Gh(args.repo), dt.datetime.now(dt.timezone.utc),
                           dry_run=args.dry_run, summary=summary, page_to=page_to,
                           max_fix_requests=args.max_fix_requests)
        if args.cmd == "wake":
            problem = queue_problem(args.max_sessions, args.timeout_min, args.max_budget_usd)
            if problem:
                print(f"wake: {problem}", file=sys.stderr)
                return 2

            def gh_for(repo_dir):
                r = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=repo_dir)
                if r.returncode != 0 or "/" not in r.stdout:
                    raise CouldNotTell(f"could not resolve the GitHub repo for {repo_dir}: "
                                       f"{(r.stderr or '').strip()[-300:]}")
                return Gh(r.stdout.strip(), cwd=repo_dir)

            return wake_pass([os.path.abspath(d) for d in (args.repo_dir or ["."])], gh_for,
                             dt.datetime.now(dt.timezone.utc), state_dir=args.state_dir,
                             dry_run=args.dry_run, max_sessions=args.max_sessions,
                             claude_bin=args.claude_bin, resume_mode=args.resume_mode,
                             claude_args=args.claude_arg, budget_usd=args.max_budget_usd,
                             timeout_min=args.timeout_min)
    except CouldNotTell as e:
        print(f"COULD NOT TELL: {e}", file=sys.stderr)
        return 1
    except Refusal as e:
        print(str(e), file=sys.stderr)
        return 3
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
