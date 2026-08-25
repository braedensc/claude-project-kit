#!/usr/bin/env python3
"""Lifecycle events the PLATFORM observed — GitHub → `ticket_events` (§4, §10).

WHY A SECOND PRODUCER AT ALL

Contract §4 lets a run report `ticket_events` in its own block, and for the
milestones a session actually performs — its first commit, the PR it opened —
that is the right source. But three of §4's milestones are things that happen
to a ticket AFTER the session that could report them has ended, and one of them
is the one §4 singles out:

    "`merged` is always `human` or `system` — never `agent`."

A merge is not a session's to claim, and nor is a green CI run: both are facts
about the platform, observed from outside the loop. So they are read back out of
GitHub here, by the same job that runs the collector, and written to the same
table under the same natural key.

    pr_opened   agent    the branch matched the pipeline's own
                         `<type>/<teamkey>-<n>-…` convention, so a session opened
                         it by construction. Emitted here as well as by the
                         session, so a run that died before reporting still
                         leaves the PR on the record.
    ci_green    system   every check run on the head commit finished, none of
                         them badly, at least one of them green.
    merged      human    the platform recorded a merge, by a real account
                system   …or by a bot / app — which is what the merge tier's
                         GitHub auto-merge looks like from here (§11).

The tracker is a platform too, and the same reasoning applies to two more:

    approved    system   the ticket entered `ready`. §5 says only `epic/*` may
                human    ever auto-approve and a session may never move itself
                         there, so this is read out of the tracker's own state
                         history rather than reported by anything in the loop.
    dispatched  system   the ticket entered `working`. A second entry is a
                human    genuinely second dispatch and carries its own `at`.

`created` is NOT read here: `/plan-epic` already emits it in its own §4 block,
with the run that filed the tickets, and two producers for one row would agree
only by accident.

WHAT THIS IS NOT

Not authority. §10's last word applies unchanged: nothing read out of these
tables may gate a budget, an approval or a merge. These rows exist so a human
can see whether the pipeline delivered anything, and for no other purpose.

The ticket a PR belongs to is derived from the BRANCH NAME, which a session
writes. That is acceptable only because of the sentence above — a mislabelled
branch moves a row onto the wrong ticket's dashboard and can do nothing else. A
binding strong enough to gate on would have to come from the dispatcher's own
PR↔ticket record, not from a string in the repo.

IDEMPOTENT, LIKE THE SWEEP BESIDE IT. Every row carries §10's natural key
`(ticket_id, event, at)` and inserts `ON CONFLICT DO NOTHING`, so re-observing
an overlapping window costs a little read and changes nothing. There is no
cursor here either, for the same reason there is none there.

FAULT-ISOLATED, LIKE THE SWEEP BESIDE IT. A PR that will not parse, a checks
call that 404s, a branch that names no ticket: counted, named, stepped over.
Only a store it could not reach is an error.

Usage:
    telemetry_lifecycle.py --config PATH [--since ISO] [--init] [--dry-run] [--json]
    telemetry_lifecycle.py --selftest

Exit: 0 = observed (skips are normal), 1 = the store was unreachable, 2 = usage.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import telemetry_scrape as scrape  # noqa: E402

API_ROOT = "https://api.github.com"
PER_PAGE = 100
# A backstop, not a policy: ten pages of PRs in one lookback window means the
# window is wrong. Hitting it is REPORTED, never silently truncated.
MAX_PAGES = 10

# §4 actors. `merged` may never be `agent`, and `parse_event` refuses it outright.
ACTOR_BOT, ACTOR_HUMAN = "system", "human"

# A check run that ended this way did not end badly.
BENIGN_CONCLUSIONS = ("success", "neutral", "skipped")

DEFAULT_BRANCH_TYPES = ("feat", "fix", "chore", "refactor", "docs")


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
class GitHubError(Exception):
    """One call failed. Named and counted by the caller, never fatal."""


class GitHub:
    """The narrowest GitHub client this needs: read PRs, read check runs.

    `transport` is injectable so the selftest exercises the real derivation
    logic against canned payloads — the part worth testing is what an event
    looks like, not whether urllib works.
    """

    def __init__(self, owner, repo, token=None, transport=None):
        self.owner, self.repo = owner, repo
        self.token = token
        self._transport = transport or self._http

    def _http(self, path, params):
        import urllib.error
        import urllib.parse
        import urllib.request

        url = "%s%s" % (API_ROOT, path)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "claude-project-kit-telemetry"}
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise GitHubError("HTTP %s on %s: %s" % (exc.code, path, exc.read()[:200]))
        except OSError as exc:
            raise GitHubError("%s on %s" % (exc, path))

    def get(self, path, **params):
        return self._transport(path, params)

    def pulls(self, since):
        """Pull requests touched since `since`, newest first. Bounded, and loud."""
        base = "/repos/%s/%s/pulls" % (self.owner, self.repo)
        out, truncated = [], False
        for page in range(1, MAX_PAGES + 1):
            batch = self.get(base, state="all", sort="updated", direction="desc",
                             per_page=PER_PAGE, page=page)
            if not isinstance(batch, list) or not batch:
                break
            stale = 0
            for pr in batch:
                if not isinstance(pr, dict):
                    continue
                if (pr.get("updated_at") or "") < since:
                    stale += 1
                    continue
                out.append(pr)
            if stale:
                break
            if len(batch) < PER_PAGE:
                break
            if page == MAX_PAGES:
                truncated = True
        return out, truncated

    def check_runs(self, sha):
        payload = self.get("/repos/%s/%s/commits/%s/check-runs" % (self.owner, self.repo, sha),
                           per_page=PER_PAGE)
        runs = (payload or {}).get("check_runs")
        return runs if isinstance(runs, list) else []


# --------------------------------------------------------------------------- #
# Linear
# --------------------------------------------------------------------------- #
LINEAR_API = "https://api.linear.app/graphql"

# The two state transitions §4 has an event for. Keyed by CANONICAL state key,
# resolved to a UUID through `delivery.json` — §1: states are compared by ID,
# never by display name, so a rename in the tracker UI cannot silently stop this.
STATE_EVENTS = {"ready": "approved", "working": "dispatched"}

HISTORY_QUERY = """
query($filter: IssueFilter, $after: String) {
  issues(filter: $filter, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      identifier
      history(first: 50) {
        nodes { createdAt toState { id } actor { id } botActor { id } }
      }
    }
  }
}"""


class Linear:
    """Issue state history. Read-only, and it holds nothing this could write with."""

    def __init__(self, key, team_key, transport=None):
        self.key, self.team_key = key, team_key
        self._transport = transport or self._http

    def _http(self, query, variables):
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            LINEAR_API,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Content-Type": "application/json", "Authorization": self.key},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise GitHubError("Linear HTTP %s: %s" % (exc.code, exc.read()[:200]))
        except OSError as exc:
            raise GitHubError("Linear: %s" % exc)
        if payload.get("errors"):
            raise GitHubError("Linear API error: %s" % json.dumps(payload["errors"])[:300])
        return payload["data"]

    def issue_history(self, since):
        filt = {"updatedAt": {"gte": since}}
        if self.team_key:
            filt["team"] = {"key": {"eq": self.team_key}}
        out, after = [], None
        for _ in range(MAX_PAGES):
            issues = self._transport(HISTORY_QUERY, {"filter": filt, "after": after})["issues"]
            out.extend(issues.get("nodes") or [])
            if not (issues.get("pageInfo") or {}).get("hasNextPage"):
                break
            after = issues["pageInfo"]["endCursor"]
        return out


def history_actor(node):
    """§4's actor for a state transition: a bot moved it, or a person did."""
    return ACTOR_BOT if (node.get("botActor") or {}).get("id") else (
        ACTOR_HUMAN if (node.get("actor") or {}).get("id") else ACTOR_BOT)


def tracker_events(linear, state_ids, notes):
    """`approved` / `dispatched`, read out of the tracker's own state history.

    Neither is a session's to report: §5 bars a session from moving itself to
    `ready`, and the dispatcher owns the move to `working`. Reading the
    transitions back is stronger than either side reporting them, because the
    tracker recorded them as a side effect of the move actually happening.
    """
    wanted = {}
    for key, event in STATE_EVENTS.items():
        state_id = (state_ids or {}).get(key)
        if isinstance(state_id, str) and state_id.strip() and "{{" not in state_id:
            wanted[state_id] = event
    if not wanted:
        notes.append("delivery.json resolves no stateIds for %s — no tracker events"
                     % ", ".join(sorted(STATE_EVENTS)))
        return []

    rows = []
    for issue in linear:
        ticket = issue.get("identifier")
        if not ticket:
            continue
        for node in ((issue.get("history") or {}).get("nodes") or []):
            if not isinstance(node, dict):
                continue
            event = wanted.get(((node.get("toState") or {}).get("id")))
            at = node.get("createdAt")
            if not event or not scrape.ISO_RE.match(str(at or "")):
                continue
            rows.append({"ticket_id": ticket, "event": event, "at": at,
                         "actor": history_actor(node)})
    return rows


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def branch_pattern(team_key, branch_types=DEFAULT_BRANCH_TYPES):
    """§1's derived branch pattern, with the team key lower-cased as it demands."""
    types = "|".join(re.escape(t) for t in branch_types)
    return re.compile(r"^(?:%s)/%s-(\d+)(?:-|$)" % (types, re.escape(team_key.lower())))


def ticket_from_branch(ref, pattern, team_key):
    match = pattern.match(ref or "")
    return "%s-%s" % (team_key.upper(), match.group(1)) if match else None


def merge_actor(merged_by):
    """§4: `merged` is `human` or `system`. An app or bot merging is `system`.

    That is exactly what the merge tier looks like from here — the platform
    merges under its own ruleset once auto-merge is enabled (§11), so the
    account on the record is the app's, not a person's.
    """
    if not isinstance(merged_by, dict):
        return ACTOR_BOT
    login = str(merged_by.get("login") or "")
    if merged_by.get("type") == "Bot" or login.endswith("[bot]"):
        return ACTOR_BOT
    return ACTOR_HUMAN if login else ACTOR_BOT


def ci_green_at(runs):
    """When every check run on this commit had finished, none of them badly.

    Returns None for "not green", which covers three different situations that
    all mean the same thing here: still running, ended badly, or no checks at
    all. A commit with no checks is not evidence of a green pipeline.
    """
    if not runs:
        return None
    latest = None
    saw_success = False
    for run in runs:
        if not isinstance(run, dict):
            return None
        if run.get("status") != "completed":
            return None
        conclusion = run.get("conclusion")
        if conclusion not in BENIGN_CONCLUSIONS:
            return None
        saw_success = saw_success or conclusion == "success"
        completed = run.get("completed_at")
        if not scrape.ISO_RE.match(str(completed or "")):
            return None
        latest = completed if latest is None or completed > latest else latest
    return latest if saw_success else None


def events_for(pr, api, pattern, team_key, notes):
    """Every lifecycle row one pull request supports. Never raises."""
    ref = ((pr.get("head") or {}).get("ref")) or ""
    number = pr.get("number")
    ticket = ticket_from_branch(ref, pattern, team_key)
    if not ticket:
        return []

    rows = []
    opened = pr.get("created_at")
    if scrape.ISO_RE.match(str(opened or "")):
        rows.append({"ticket_id": ticket, "event": "pr_opened", "at": opened,
                     "actor": "agent"})

    merged_at = pr.get("merged_at")
    if scrape.ISO_RE.match(str(merged_at or "")):
        rows.append({"ticket_id": ticket, "event": "merged", "at": merged_at,
                     "actor": merge_actor(pr.get("merged_by"))})

    sha = (pr.get("head") or {}).get("sha")
    if sha:
        try:
            green = ci_green_at(api.check_runs(sha))
        except GitHubError as exc:
            notes.append("PR #%s: could not read check runs (%s)" % (number, exc))
            green = None
        if green:
            rows.append({"ticket_id": ticket, "event": "ci_green", "at": green,
                         "actor": "system"})
    return rows


def observe(api, team_key, since, sink, branch_types=DEFAULT_BRANCH_TYPES,
            linear=None, state_ids=None):
    """Sweep the platforms' own records into `ticket_events`."""
    stats = {"pulls": 0, "matched": 0, "issues": 0, "ticket_events": 0, "skipped": 0}
    notes, skips = [], []
    pattern = branch_pattern(team_key, branch_types)

    def record(row, source):
        try:
            # Routed through the collector's own parser so §4's refusals —
            # `merged` by an agent above all — are enforced by ONE
            # implementation, not by a second one that agrees today.
            values = scrape.parse_event(row, source)
        except scrape.Skipped as exc:
            stats["skipped"] += 1
            skips.append("%s %s: %s" % (source, row.get("event"), exc))
            return
        sink.write("ticket_events", scrape.EVENT_COLUMNS, values,
                   ("ticket_id", "event", "at"), update=False)
        stats["ticket_events"] += 1

    # ── The tracker's own state history ────────────────────────────────────
    if linear is not None:
        try:
            issues = linear.issue_history(since)
        except GitHubError as exc:
            issues = []
            skips.append("could not read tracker history: %s" % exc)
        stats["issues"] = len(issues)
        for row in tracker_events(issues, state_ids, notes):
            record(row, "linear:%s" % row["ticket_id"])

    try:
        pulls, truncated = api.pulls(since)
    except GitHubError as exc:
        # One platform being unreachable is not the store being unreachable, and
        # it is not the OTHER platform being unreachable either: say so, keep
        # whatever the tracker already gave us, and let the sweep beside this
        # one land its own rows.
        pulls, truncated = [], False
        skips.append("could not list pull requests: %s" % exc)

    if truncated:
        notes.append("stopped at %d page(s) of pull requests — the lookback window is "
                     "wider than this observer will page through" % MAX_PAGES)

    stats["pulls"] = len(pulls)
    for pr in pulls:
        rows = events_for(pr, api, pattern, team_key, notes)
        if not rows:
            continue
        stats["matched"] += 1
        for row in rows:
            record(row, "gh:pr:%s" % pr.get("number"))

    return {"schema": "pipeline-telemetry-lifecycle/1", "stats": stats,
            "skipped": skips, "notes": notes}


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
SINCE = "2026-08-18T00:00:00Z"

PULLS = [
    {   # merged by a person
        "number": 41, "created_at": "2026-08-19T09:00:00Z",
        "updated_at": "2026-08-20T18:00:00Z", "merged_at": "2026-08-20T17:30:00Z",
        "merged_by": {"login": "a-person", "type": "User"},
        "head": {"ref": "feat/eng-123-token-refresh", "sha": "aaa"},
    },
    {   # not a pipeline branch at all
        "number": 42, "created_at": "2026-08-19T10:00:00Z",
        "updated_at": "2026-08-19T10:00:00Z", "merged_at": None, "merged_by": None,
        "head": {"ref": "chore/bump-deps", "sha": "bbb"},
    },
    {   # open, CI still running
        "number": 43, "created_at": "2026-08-20T08:00:00Z",
        "updated_at": "2026-08-20T08:30:00Z", "merged_at": None, "merged_by": None,
        "head": {"ref": "fix/eng-124-null-guard", "sha": "ccc"},
    },
    {   # merged by the platform's own app — the merge tier, seen from here
        "number": 44, "created_at": "2026-08-20T11:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z", "merged_at": "2026-08-20T11:45:00Z",
        "merged_by": {"login": "github-actions[bot]", "type": "Bot"},
        "head": {"ref": "feat/eng-125-copy-tweak", "sha": "ddd"},
    },
    {   # CI red
        "number": 45, "created_at": "2026-08-20T13:00:00Z",
        "updated_at": "2026-08-20T13:30:00Z", "merged_at": None, "merged_by": None,
        "head": {"ref": "fix/eng-126-flake", "sha": "eee"},
    },
    {   # outside the window; `pulls()` must stop here, newest-first
        "number": 40, "created_at": "2026-08-01T09:00:00Z",
        "updated_at": "2026-08-01T09:00:00Z", "merged_at": "2026-08-02T09:00:00Z",
        "merged_by": {"login": "a-person", "type": "User"},
        "head": {"ref": "feat/eng-100-old", "sha": "fff"},
    },
]

CHECKS = {
    "aaa": [{"status": "completed", "conclusion": "success", "completed_at": "2026-08-20T16:00:00Z"},
            {"status": "completed", "conclusion": "skipped", "completed_at": "2026-08-20T15:50:00Z"}],
    "ccc": [{"status": "in_progress", "conclusion": None, "completed_at": None}],
    "ddd": [{"status": "completed", "conclusion": "success", "completed_at": "2026-08-20T11:30:00Z"}],
    "eee": [{"status": "completed", "conclusion": "failure", "completed_at": "2026-08-20T13:20:00Z"}],
}


def fake_transport(pulls=None, checks=None, fail=()):
    pulls = PULLS if pulls is None else pulls
    checks = CHECKS if checks is None else checks

    def transport(path, params):
        if path.endswith("/pulls"):
            if "pulls" in fail:
                raise GitHubError("HTTP 403 on %s: rate limited" % path)
            return pulls if int(params.get("page", 1)) == 1 else []
        match = re.search(r"/commits/([^/]+)/check-runs$", path)
        if match:
            if "checks" in fail:
                raise GitHubError("HTTP 404 on %s" % path)
            return {"check_runs": checks.get(match.group(1), [])}
        raise GitHubError("unexpected path %s" % path)

    return transport


STATE_IDS = {"raw": "s-raw", "ready": "s-ready", "working": "s-working",
             "review": "s-review", "done": "s-done"}

ISSUES = [{
    "identifier": "ENG-123",
    "history": {"nodes": [
        {"createdAt": "2026-08-18T14:00:00Z", "toState": {"id": "s-ready"},
         "actor": None, "botActor": {"id": "bot-1"}},
        {"createdAt": "2026-08-18T15:00:00Z", "toState": {"id": "s-working"},
         "actor": None, "botActor": {"id": "bot-1"}},
        {"createdAt": "2026-08-19T09:05:00Z", "toState": {"id": "s-review"},
         "actor": None, "botActor": {"id": "bot-1"}},
        {"createdAt": "2026-08-20T18:00:00Z", "toState": {"id": "s-done"},
         "actor": {"id": "u-1"}, "botActor": None},
    ]},
}, {
    "identifier": "ENG-124",
    "history": {"nodes": [
        {"createdAt": "2026-08-19T08:00:00Z", "toState": {"id": "s-ready"},
         "actor": {"id": "u-1"}, "botActor": None},
        {"createdAt": "2026-08-19T08:30:00Z", "toState": None,
         "actor": {"id": "u-1"}, "botActor": None},
        {"createdAt": "not a timestamp", "toState": {"id": "s-working"},
         "actor": {"id": "u-1"}, "botActor": None},
    ]},
}]


class FakeLinear:
    def __init__(self, issues=None, fail=False):
        self.issues, self.fail = ISSUES if issues is None else issues, fail

    def issue_history(self, since):
        if self.fail:
            raise GitHubError("Linear HTTP 401: bad key")
        return self.issues


def selftest():
    failures = []
    cases = [0]

    def check(label, cond, detail=""):
        cases[0] += 1
        if not cond:
            failures.append("%s%s" % (label, (": " + detail) if detail else ""))

    def run(linear=None, state_ids=None, **kwargs):
        api = GitHub("acme", "app", transport=fake_transport(**kwargs))
        sink = scrape.DrySink("pipeline")
        return observe(api, "ENG", SINCE, sink, linear=linear,
                       state_ids=state_ids), sink

    result, sink = run()
    rows = [dict(zip(scrape.EVENT_COLUMNS, v)) for v in sink.rows["ticket_events"]]
    seen = {(r["ticket_id"], r["event"]): r for r in rows}

    check("the stale pull request is outside the window", result["stats"]["pulls"] == 5,
          json.dumps(result["stats"]))
    check("a non-pipeline branch yields no ticket", ("ENG-100", "merged") not in seen)
    check("four pull requests map to a ticket", result["stats"]["matched"] == 4,
          json.dumps(result["stats"]))
    check("nothing is skipped on the happy path", result["stats"]["skipped"] == 0,
          "; ".join(result["skipped"]))

    check("pr_opened is derived for every matched PR",
          sum(1 for k in seen if k[1] == "pr_opened") == 4)
    check("pr_opened is attributed to the session that opened it",
          seen[("ENG-123", "pr_opened")]["actor"] == "agent")
    check("pr_opened carries the platform's own timestamp",
          seen[("ENG-123", "pr_opened")]["at"] == "2026-08-19T09:00:00Z")

    check("a human merge is `human`", seen[("ENG-123", "merged")]["actor"] == "human")
    check("an app merge is `system`", seen[("ENG-125", "merged")]["actor"] == "system")
    check("no merge event is ever `agent`",
          all(r["actor"] != "agent" for r in rows if r["event"] == "merged"))
    check("an unmerged PR emits no merge", ("ENG-124", "merged") not in seen)

    check("a fully green head commit yields ci_green",
          seen[("ENG-123", "ci_green")]["at"] == "2026-08-20T16:00:00Z")
    check("ci_green is the LATEST check to finish, not the first",
          seen[("ENG-123", "ci_green")]["at"] > "2026-08-20T15:50:00Z")
    check("ci_green is `system`", seen[("ENG-123", "ci_green")]["actor"] == "system")
    check("checks still running are not green", ("ENG-124", "ci_green") not in seen)
    check("a red check run is not green", ("ENG-126", "ci_green") not in seen)
    check("a commit with no checks at all is not green", ci_green_at([]) is None)
    check("checks that all skipped are not green",
          ci_green_at([{"status": "completed", "conclusion": "skipped",
                        "completed_at": "2026-08-20T10:00:00Z"}]) is None)

    # ── The natural key, and therefore idempotency ──────────────────────────
    sql = sink.statements[0][0]
    check("rows dedupe on §10's natural key",
          "ON CONFLICT (ticket_id, event, at) DO NOTHING" in sql, sql)
    again, sink2 = run()
    check("a second pass produces an identical row set",
          sink2.rows["ticket_events"] == sink.rows["ticket_events"])
    check("a second pass is not a no-op it hides",
          again["stats"]["ticket_events"] == result["stats"]["ticket_events"])

    # ── §4's one outright refusal is enforced by the collector's parser ─────
    try:
        scrape.parse_event({"ticket_id": "ENG-1", "event": "merged",
                            "at": "2026-08-20T17:30:00Z", "actor": "agent"}, "gh:pr:1")
        refused = False
    except scrape.Skipped:
        refused = True
    check("an agent-authored merge is refused by the shared parser", refused)

    # ── Failure is isolated, never fatal ───────────────────────────────────
    result, sink3 = run(fail=("checks",))
    check("an unreadable checks call loses only ci_green",
          not any(r[1] == "ci_green" for r in sink3.rows["ticket_events"]))
    check("...and the merge rows still land",
          any(r[1] == "merged" for r in sink3.rows["ticket_events"]))
    check("...and it is named, not swallowed", result["notes"], "no note emitted")

    result, sink4 = run(fail=("pulls",))
    check("an unreachable GitHub yields no rows and no crash",
          result["stats"]["ticket_events"] == 0 and result["skipped"])

    result, _ = run(pulls=[{"number": 1, "head": {"ref": "feat/eng-7-x"}}])
    check("a pull request with no timestamps yields nothing",
          result["stats"]["ticket_events"] == 0, json.dumps(result["stats"]))

    result, _ = run(pulls=[{"number": 1, "created_at": "2026-08-19 09:00:00",
                            "updated_at": "2026-08-20T18:00:00Z",
                            "head": {"ref": "feat/eng-7-x", "sha": "zzz"}}])
    check("a timestamp without Z is not an event",
          result["stats"]["ticket_events"] == 0)

    # ── The branch → ticket derivation, per §1 ─────────────────────────────
    pattern = branch_pattern("ENG")
    for ref, want in (("feat/eng-123-token-refresh", "ENG-123"),
                      ("fix/eng-9-x", "ENG-9"),
                      ("chore/eng-4", "ENG-4"),
                      ("feat/ENG-123-token-refresh", None),
                      ("feat/other-123-x", None),
                      ("eng-123-no-type", None),
                      ("wip/eng-123-x", None)):
        check("branch %r maps to %r" % (ref, want),
              ticket_from_branch(ref, pattern, "ENG") == want,
              str(ticket_from_branch(ref, pattern, "ENG")))

    # ── The tracker's own state history ────────────────────────────────────
    result, sink5 = run(linear=FakeLinear(), state_ids=STATE_IDS)
    rows5 = [dict(zip(scrape.EVENT_COLUMNS, v)) for v in sink5.rows["ticket_events"]]
    tracker = {(r["ticket_id"], r["event"]): r for r in rows5}

    check("entering `ready` is an approval",
          tracker[("ENG-123", "approved")]["at"] == "2026-08-18T14:00:00Z")
    check("entering `working` is a dispatch",
          tracker[("ENG-123", "dispatched")]["at"] == "2026-08-18T15:00:00Z")
    check("a bot transition is `system`",
          tracker[("ENG-123", "approved")]["actor"] == "system")
    check("a person's transition is `human`",
          tracker[("ENG-124", "approved")]["actor"] == "human")
    check("states §4 has no event for produce no rows",
          ("ENG-123", "review") not in tracker and ("ENG-123", "done") not in tracker)
    check("a history entry with no target state is skipped",
          ("ENG-124", "dispatched") not in tracker)
    check("a history timestamp §4 cannot parse is skipped",
          sum(1 for r in rows5 if r["event"] == "dispatched") == 1,
          json.dumps([r for r in rows5 if r["event"] == "dispatched"]))
    check("`created` is left to the run that filed the ticket",
          not any(r["event"] == "created" for r in rows5))
    check("the GitHub rows still land beside the tracker rows",
          ("ENG-123", "merged") in tracker and ("ENG-123", "ci_green") in tracker)
    check("both sources are counted",
          result["stats"]["issues"] == 2 and result["stats"]["pulls"] == 5,
          json.dumps(result["stats"]))

    # Unresolved stateIds — the shipped template's own state — is a note, not a row.
    notes = []
    check("placeholder stateIds yield nothing",
          tracker_events(ISSUES, {"ready": "{{LINEAR_STATE_ID_READY}}"}, notes) == [])
    check("...and say so", notes, "no note emitted")

    result, sink6 = run(linear=FakeLinear(fail=True), state_ids=STATE_IDS)
    check("an unreachable tracker loses only the tracker rows",
          not any(r[1] in ("approved", "dispatched") for r in sink6.rows["ticket_events"]))
    check("...and the GitHub rows survive it",
          any(r[1] == "merged" for r in sink6.rows["ticket_events"]))
    check("...and the failure is named", result["skipped"], "nothing reported")

    result, sink7 = run(linear=FakeLinear(), state_ids=STATE_IDS, fail=("pulls",))
    check("an unreachable GitHub loses only the GitHub rows",
          any(r[1] == "approved" for r in sink7.rows["ticket_events"])
          and not any(r[1] == "merged" for r in sink7.rows["ticket_events"]))

    # ── The DDL this writes into is the collector's, not a second one ──────
    ddl = scrape.DDL.format(s="pipeline")
    for column in scrape.EVENT_COLUMNS:
        check("the shared DDL declares ticket_events.%s" % column,
              re.search(r"\b%s\b" % re.escape(column), ddl) is not None)

    if failures:
        print("FAIL: %d of %d lifecycle case(s) failed:" % (len(failures), cases[0]))
        for line in failures:
            print("  - %s" % line)
        return 1
    print("OK: %d lifecycle case(s) passed" % cases[0])
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="path to delivery.json")
    ap.add_argument("--since", help="ISO-8601 UTC lower bound (default: lookbackDays ago)")
    ap.add_argument("--init", action="store_true", help="create the tables if absent")
    ap.add_argument("--dry-run", action="store_true", help="print the SQL, touch no database")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    config_path = args.config or "delivery.json"
    if not os.path.exists(config_path):
        return 0  # §2: absent is OFF, not broken. Silence, exit 0.
    try:
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as exc:
        print("FAIL: %s is present but unreadable: %s" % (config_path, exc), file=sys.stderr)
        return 2
    if config.get("version") != scrape.SUPPORTED_VERSION:
        print("FAIL: %s declares version %r; this observer implements contract version %d"
              % (config_path, config.get("version"), scrape.SUPPORTED_VERSION), file=sys.stderr)
        return 2

    settings, reason = scrape.telemetry_config(config)
    if settings is None:
        print("Telemetry collection is not configured: %s" % reason)
        return 0

    gh = config.get("github") or {}
    owner, repo = str(gh.get("owner") or ""), str(gh.get("repo") or "")
    team_key = str((config.get("linear") or {}).get("teamKey") or "")
    if not owner or not repo or not team_key or "{{" in owner + repo + team_key:
        print("github.owner/repo or linear.teamKey is unset — nothing to observe.")
        return 0
    branch_types = (config.get("branch") or {}).get("types") or list(DEFAULT_BRANCH_TYPES)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set — skipping the platform observer.")
        return 0

    since = args.since or (
        datetime.now(timezone.utc) - timedelta(days=settings["lookback"])
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # The tracker half is optional in exactly the way every other credential in
    # this kit is: without the key it is off, and it says so once.
    linear_key = os.environ.get("LINEAR_API_KEY")
    state_ids = ((config.get("linear") or {}).get("stateIds")) or {}
    linear = Linear(linear_key, team_key) if linear_key else None
    if linear is None:
        print("LINEAR_API_KEY is not set — observing GitHub only, no `approved` or "
              "`dispatched` rows.")

    if args.dry_run:
        sink = scrape.DrySink(settings["schema"])
    else:
        dsn = os.environ.get(settings["dsn_env"])
        if not dsn:
            print("FAIL: $%s is not set — telemetry.dsnEnv names it, and the DSN is "
                  "never stored in delivery.json" % settings["dsn_env"], file=sys.stderr)
            return 1
        try:
            sink = scrape.PostgresSink(dsn, settings["schema"])
        except Exception as exc:
            print("FAIL: could not connect to the telemetry store: %s" % exc, file=sys.stderr)
            return 1

    try:
        if args.init:
            sink.init()
        result = observe(GitHub(owner, repo, token), team_key, since, sink, branch_types,
                         linear=linear, state_ids=state_ids)
        sink.commit()
    except Exception as exc:
        print("FAIL: the store rejected the observation: %s" % exc, file=sys.stderr)
        return 1
    finally:
        sink.close()

    result["since"] = since
    result["dry_run"] = bool(args.dry_run)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        stats = result["stats"]
        print("Observed %d pull request(s) (%d on a ticket branch) and %d issue(s) since "
              "%s → %d event(s); %d row(s) skipped."
              % (stats["pulls"], stats["matched"], stats["issues"], since,
                 stats["ticket_events"], stats["skipped"]))
        for line in result["skipped"][:20]:
            print("  skip: %s" % line)
        for line in result["notes"][:20]:
            print("  note: %s" % line)
        if args.dry_run:
            print("  (dry run — no database was contacted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
