#!/usr/bin/env python3
"""Conflict self-resolution — one loop, two halves, one marker grammar.

    pr_conflict.py monitor [--dry-run]              # GitHub Actions (pr-conflict-monitor.yml)
    pr_conflict.py wake [--repo-dir DIR] [options]  # a PERSON's machine, on a schedule
    pr_conflict.py --selftest

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
                                            worktree: merge main, resolve, push,
                                            watch CI — never merge, never approve
                                          → re-reads mergeable → `result` marker
    next tick:
      MERGEABLE        → drop label (episode over)
      no ack in 15 min → ESCALATE (page the owner)
      no result in 2 h → ESCALATE
      result, still CONFLICTING → ESCALATE
      budget spent (3 requests on this PR) or a fork → page, never request

  The cost, said plainly: the loop closes only while a waker runs on a machine
  that holds the branch's worktree and is awake. When it does not, the monitor's
  deadline turns that into a page — the same page as before, never silence.

MARKERS (the whole producer/consumer contract)

  The FIRST LINE of a comment, exactly:

    <!-- pr-conflict:request episode=N -->                      github-actions[bot]
    <!-- pr-conflict:page episode=N reason=budget|fork -->      github-actions[bot]
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
  1  could not tell: mergeability never settled, a listing was truncated, or a
     GitHub call failed. Never the same token as "no conflicts".
  2  usage error
  3  REFUSED — `wake` (not --dry-run) in an agent environment
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
TAIL_CHARS = 1500

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
            nodes{number isDraft mergeable headRefName isCrossRepository labels(first:100){nodes{name}}}}}}"""
        out, cursor = [], None
        for _ in range(MAX_PR_PAGES):
            page = self._graphql(query, {"owner": self.owner, "name": self.name, "states": states,
                                         "labels": labels, "cursor": cursor})
            prs = page["repository"]["pullRequests"]
            out += [{**p, "labels": [label["name"] for label in p["labels"]["nodes"]]}
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
        self._api("POST", f"repos/{self.owner}/{self.name}/labels",
                  {"name": LABEL, "color": "d93f0b",
                   "description": "Merge conflicts with main (auto-managed by pr-conflict-monitor)"},
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

    def assign_owner(self, number):
        try:
            self._api("POST", f"repos/{self.owner}/{self.name}/issues/{number}/assignees",
                      {"assignees": [self.owner]})
        except CouldNotTell as e:  # an org owner cannot be assigned; the @mention still lands
            print(f"  #{number}: could not assign @{self.owner}: {e}")

    def mergeable(self, number):
        query = """query($owner:String!,$name:String!,$number:Int!){
          repository(owner:$owner,name:$name){pullRequest(number:$number){mergeable state}}}"""
        pr = self._graphql(query, {"owner": self.owner, "name": self.name, "number": number})
        return pr["repository"]["pullRequest"]["mergeable"]


# ── comment bodies ───────────────────────────────────────────────────────────

def _recipe(pr):
    return "\n".join([
        "```", "git fetch origin main && git merge origin/main",
        "# resolve, run the local checks, git commit, then:",
        "git push", f"gh pr checks {pr['number']} --watch", "```",
        "",
        "(A merge, not a rebase: nothing is force-pushed under a worktree that may still hold this branch.)",
    ])


def _headline(pr):
    return (f"`{pr['headRefName']}` has **merge conflicts** with `main` (mergeable = CONFLICTING). "
            "While conflicted, GitHub cannot build the merge ref, so the **required checks never run** "
            "— a side check can still report green. Do not read that as a passing PR.")


def request_body(pr, episode, attempt):
    return "\n".join([
        marker("request", episode), _headline(pr), "",
        f"**A fix has been requested** (automated attempt {attempt} of {MAX_FIX_REQUESTS} on this PR). "
        "A conflict waker (`scripts/pr_conflict.py wake`) running on a machine that holds this "
        "branch's worktree acknowledges it here, then wakes a Claude Code session in that worktree "
        "to merge `main`, resolve, push and watch CI. It never merges or approves.",
        "",
        f"If nothing acknowledges this within {ACK_DEADLINE_MIN} min, or the attempt ends with the "
        "PR still conflicted, this monitor pages the owner here. To fix it by hand meanwhile:", "",
        _recipe(pr), "",
        "The label clears itself once the PR is mergeable again, so a later conflict starts a new episode.",
    ])


def page_body(pr, episode, reason, owner):
    why = {
        "budget": (f"this PR has already had {MAX_FIX_REQUESTS} automated fix attempts — a PR that "
                   "keeps conflicting needs a person, not another guess"),
        "fork": "it comes from a fork, and the waker only acts on branches in this repository",
    }[reason]
    return "\n".join([
        marker("page", episode, reason=reason), _headline(pr), "",
        f"**No automated fix requested:** {why}.", "", _recipe(pr), "",
        f"cc @{owner} — this PR cannot merge and its required checks are not running.",
    ])


def escalation_body(pr, episode, reason, owner):
    return "\n".join([
        marker("escalated", episode),
        f"**The automated conflict fix did not land — this needs a person.** {reason[0].upper()}{reason[1:]}.",
        "", _recipe(pr), "",
        f"cc @{owner} — this PR cannot merge and its required checks are not running.",
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


def monitor(gh, now, sleep=time.sleep, dry_run=False, summary=print):
    act = (lambda *a, **k: None) if dry_run else None
    add_label = act or gh.add_label
    remove_label = act or gh.remove_label
    comment = act or gh.comment
    assign = act or gh.assign_owner
    if dry_run:
        print("DRY RUN — every line below says what WOULD be written; nothing is.")
    else:
        gh.ensure_label()

    tally = {"open": 0, "conflicted": 0, "requested": [], "paged": [], "escalated": [],
             "cleared": [], "unsettled": []}
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
            if pr["isCrossRepository"] or state["requests"] >= MAX_FIX_REQUESTS:
                reason = "fork" if pr["isCrossRepository"] else "budget"
                comment(n, page_body(pr, episode, reason, gh.owner))
                assign(n)
                tally["paged"].append(n)
                print(f"#{n}: CONFLICTING — paged ({reason}), episode {episode}")
            else:
                comment(n, request_body(pr, episode, state["requests"] + 1))
                tally["requested"].append(n)
                print(f"#{n}: CONFLICTING — fix requested, episode {episode}")
            continue
        verdict, why = decide_ongoing(state, now)
        if verdict == "escalate":
            comment(n, escalation_body(pr, state["episode"], why, gh.owner))
            assign(n)
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
    summary("\n".join(lines))
    return 1 if tally["unsettled"] else 0


# ── wake (a person's machine) ────────────────────────────────────────────────

FIX_PROMPT = """\
PR #{number} (branch `{branch}`) now has merge conflicts with main: main moved after this PR \
went green, so its required checks are not running. You are in this branch's worktree. Resolve it:

1. Confirm `git status` is clean and you are on `{branch}`.
2. `git fetch origin main && git merge origin/main` — merge, do not rebase. A rebase ends in a \
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


def wake(gh, repo_dir, now, *, runner=run, env=None, sleep=time.sleep, dry_run=False,
         claude_bin="claude", resume_mode="from-pr", claude_args=(), budget_usd=5.0,
         timeout_min=40, lock_dir=None):
    env = os.environ if env is None else env
    found = agent_env_markers_present(env)
    if found and not dry_run:
        raise Refusal(
            f"REFUSED: `wake` starts model sessions, and this is an agent environment ({', '.join(found)} set).\n"
            "  A session that launches its own sessions spends money nobody approved. A PERSON runs\n"
            "  this, from a terminal or a scheduler. Read-only meanwhile: wake --dry-run")
    lock_dir = lock_dir or os.path.join(tempfile.gettempdir(), "pr-conflict-waker")
    trees = worktrees_by_branch(repo_dir, runner)
    tally = {"pending": 0, "woken": [], "declined": [], "elsewhere": [], "busy": []}
    candidates = [p for p in gh.open_prs()
                  if LABEL in p["labels"] and not p["isDraft"] and not p["isCrossRepository"]]
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
        lock = _lock(lock_dir, f"{gh.owner}/{gh.name}", n)
        if lock is None:
            tally["busy"].append(n)
            print(f"#{n}: another waker pass is working it")
            continue
        try:
            state = episode_state(gh.comments(n))  # re-read under the lock: a finished pass may have claimed it
            if state["ack"] or state["result"]:
                print(f"#{n}: claimed by another pass while waiting for the lock")
                continue
            episode = state["episode"]
            dirty = runner(["git", "-C", wt, "status", "--porcelain"])
            reason = None
            if not BRANCH_RE.match(branch):
                reason = "the branch name has characters the waker will not put in a prompt"
            elif dirty.returncode != 0:
                reason = "`git status` failed in the worktree"
            elif dirty.stdout.strip():
                reason = "the worktree has uncommitted changes — a session may be mid-work there"
            if reason:
                tally["declined"].append(n)
                print(f"#{n}: declined — {reason}")
                if not dry_run:
                    gh.comment(n, "\n".join([marker("result", episode, outcome="declined"),
                                             f"The conflict waker declined to start a fix: {reason}. "
                                             "A person needs to look."]))
                continue
            if dry_run:
                tally["woken"].append(n)
                print(f"#{n}: would wake a session in the worktree for `{branch}`")
                continue
            gh.comment(n, "\n".join([
                marker("ack", episode),
                f"Conflict waker: starting a fix session in this branch's worktree "
                f"(spend cap ${budget_usd:g}, timeout {timeout_min} min). It never merges or approves."]))
            cmd = [claude_bin, "-p", FIX_PROMPT.format(number=n, branch=branch),
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
            merge_state = settle(lambda: [{"number": n, "isDraft": False, "mergeable": gh.mergeable(n)}],
                                 sleep, attempts=6, delay=10)[0]["mergeable"]
            outcome = {"MERGEABLE": "resolved", "CONFLICTING": "unresolved"}.get(merge_state, "unknown")
            if outcome != "resolved" and (timed_out or rc):
                outcome = "failed"
            ran = "timed out" if timed_out else f"exited {rc}"
            gh.comment(n, "\n".join([
                marker("result", episode, outcome=outcome),
                f"Conflict waker: the fix session {ran}; GitHub now reports mergeable = {merge_state}.",
                "", "<details><summary>Last lines of the session's output</summary>", "",
                "```", neutralize(output) or "(no output)", "```", "</details>"]))
            tally["woken"].append(n)
            print(f"#{n}: fix session {ran} — outcome {outcome}")
        finally:
            lock.close()
    print(f"asked: open `{LABEL}` PRs with an unclaimed fix request; pending {tally['pending']} · "
          f"{'would wake' if dry_run else 'woke'} {tally['woken'] or 'none'} · declined "
          f"{tally['declined'] or 'none'} · not on this machine {tally['elsewhere'] or 'none'} · "
          f"busy {tally['busy'] or 'none'}")
    return 0


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

    def __init__(self, prs=(), comments=None, closed=(), mergeable_after="MERGEABLE"):
        self.prs = [dict(p) for p in prs]
        self._comments = comments or {}
        self.closed = list(closed)
        self.writes = []
        self.mergeable_after = mergeable_after

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

    def assign_owner(self, n):
        self.writes.append(("assign", n))

    def mergeable(self, n):
        self.writes.append(("read_mergeable", n))
        return self.mergeable_after


def _pr(n, mergeable="CONFLICTING", labels=(), draft=False, fork=False, branch=None):
    return {"number": n, "mergeable": mergeable, "labels": list(labels), "isDraft": draft,
            "isCrossRepository": fork, "headRefName": branch or f"feat/pr-{n}"}


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
    expect(body.startswith(marker("request", 1)) and "@acme" not in body,
           "a request must carry the marker on line 1 and must not page the owner")

    # 2. budget spent → page (assign + @mention), never a fourth request.
    prior = [_bot(marker("request", i), 500 - i) for i in (1, 2, 3)]
    gh = FakeGh([_pr(2)], {2: prior})
    run_monitor(gh)
    body = [w for w in gh.writes if w[0] == "comment"][0][2]
    expect(body.startswith(marker("page", 4, reason="budget")) and "@acme" in body
           and ("assign", 2) in gh.writes, f"budget spent must page: {gh.writes}")

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

        common = dict(runner=runner, sleep=quiet, lock_dir=os.path.join(tmp, "locks"))

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
        rc = wake(gh, repo, NOW, env={}, **common)
        seq = [(w[0], w[1], (w[2].split("\n", 1)[0] if w[0] == "comment" else "")) for w in gh.writes]
        expect(rc == 0, f"wake rc={rc}")
        expect(seq[:3] == [("comment", 7, marker("ack", 1)), ("read_mergeable", 7, ""),
                           ("comment", 7, marker("result", 1, outcome="resolved"))],
               f"ack must precede the session and the result must follow a re-read: {seq}")
        expect(("comment", 8, marker("result", 1, outcome="declined")) in seq, f"dirty worktree must decline: {seq}")
        expect(not any(len(w) > 1 and w[1] in (6, 9) for w in gh.writes), f"#6 claimed and #9 elsewhere must be untouched: {seq}")
        expect(len(calls) == 1 and os.path.realpath(calls[0][1]) == os.path.realpath(wt)
               and "--from-pr" in calls[0][0] and "--max-budget-usd" in calls[0][0],
               f"one session, in #7's worktree, resumed by PR, with a spend cap: {calls}")
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
        firsts = [w[2].split("\n", 1)[0] for w in gh.writes if w[0] == "comment" and w[1] == 7]
        expect(firsts[-1] == marker("result", 1, outcome="failed"), f"a failed session must say failed: {firsts}")

        # 20. the prompt forbids merge/approve/labels and never asks for a force-push.
        expect("Never merge" in FIX_PROMPT and "never approve" in FIX_PROMPT
               and "--force-with-lease" not in FIX_PROMPT, "the fix prompt must stay merge-free")

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
    print("pr_conflict selftest: OK (21 cases)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    m = sub.add_parser("monitor")
    m.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    m.add_argument("--dry-run", action="store_true")
    w = sub.add_parser("wake")
    w.add_argument("--repo-dir", default=".")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--max-budget-usd", type=float, default=5.0)
    w.add_argument("--timeout-min", type=int, default=40)
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
            summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

            def summary(text):
                print(text)
                if summary_path:
                    with open(summary_path, "a") as fh:
                        fh.write(text + "\n")

            return monitor(Gh(args.repo), dt.datetime.now(dt.timezone.utc),
                           dry_run=args.dry_run, summary=summary)
        if args.cmd == "wake":
            repo_dir = os.path.abspath(args.repo_dir)
            r = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=repo_dir)
            if r.returncode != 0 or "/" not in r.stdout:
                raise CouldNotTell(f"could not resolve the GitHub repo for {repo_dir}: {r.stderr.strip()[-300:]}")
            return wake(Gh(r.stdout.strip(), cwd=repo_dir), repo_dir, dt.datetime.now(dt.timezone.utc),
                        dry_run=args.dry_run, claude_bin=args.claude_bin, resume_mode=args.resume_mode,
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
