#!/usr/bin/env python3
"""Stage E criteria snapshot — what a ticket asked when a person delegated it, written
where the delegated session cannot reach, and a divergence said once on the ticket.
Tier 2 of docs/adr/2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md, decision 2
(KIT-131).

THE GAP

  A dispatcher session holds a workspace-scoped tracker write token, so it can edit its
  own acceptance criteria mid-flight. The review reads the criteria through
  scripts/pipeline_review_basis.py, whose three tiers are: 1 Linear's description history
  as of delegation (not implementable — history records THAT a description changed, not
  what it said), 2 a trusted snapshot taken at delegation, 3 the live ticket. Only tier 3
  ever shipped, so `criteria_changed_after_delegation` was always unknown and a review
  judged a session against criteria that session could have rewritten. The resolver has
  read `<snapshot dir>/<TICKET>.json` since 2026-09-05; nothing wrote one. This file does.

WHAT A SNAPSHOT IS, AND WHY A SESSION CANNOT FORGE ONE

  - It is taken for an agent session whose `creator` is a PERSON. Linear sets that field
    only when a human delegated; a session delegating through its own token leaves it
    null (AgentSession.creator, @linear/sdk 64). So a session cannot open a second session
    on its ticket to re-take the snapshot after editing the criteria.
  - It is written under the role account's state directory, outside every worktree, which
    the session sandbox denies reads of — let alone writes.
  - It is immutable for its session. A later session a person delegates replaces it (the
    reconciliation path for a legitimate edit); the old one is kept beside it.
  - It records how late it was: `delegated_at` is the session's own `createdAt`
    (Linear's clock), `taken_at` is this pass. Between the two a session could already
    have edited. Linear's issue history says whether the description changed in that
    window (`updatedDescription`, `descriptionUpdatedBy`), and the snapshot records the
    answer — true, false, or None when the history could not be read or did not reach
    back far enough. None is not false.

  The one parser is check_ticket_dor.pin_fields — the same one the resolver's live tier
  and the tier-0 pin use — so a snapshot and the live ticket are always read alike.

WHAT A DIVERGENCE DOES

  On a later pass, while the ticket is not done, the live criteria are read again. When
  they differ from the snapshot, ONE top-level comment on the coding ticket says so: what
  was added and removed, that the review judges against the criteria as delegated, and
  how to make the new ones the basis (a person delegates again). Once per distinct live
  version, recorded in the snapshot, so a pass every few minutes says it once. Never a
  thread reply — a reply under the session's root comment is a prompt, and this is a
  record. An edit that is later reverted stops diverging; nothing more is said.

WHAT THIS FILE NEVER DOES

  Its one Linear mutation is `commentCreate`, on a managed team's ticket, for a divergence.
  It never edits a description, never labels, never moves a ticket, never starts a
  session. --selftest asserts it over the source.

HOSTED BY THE BOUNCE DRIVER

  `snapshot_pass` runs at the start of every scripts/pipeline_bounce_local.py `run`: the
  driver already runs as the role account, holds the owner-scoped tracker key, and is
  the component that leaves notices on coding tickets. No fourth daemon. A pass that
  could not look is said in the driver's heartbeat, never read as "nothing changed".

Usage:
    pipeline_criteria_snapshot.py pass --config ~/.stage-e/config.json [--dry-run]
    pipeline_criteria_snapshot.py --selftest

Exit: 0 = the pass ran (it may have done nothing, and says so)
      2 = could not: no credential, the tracker unreachable, the store unwritable
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_ticket_dor import pin_fields  # noqa: E402  (ONE description parser, contract §3)

EXIT_OK = 0
EXIT_USAGE = 2

SCHEMA = "pipeline-criteria-snapshot/1"
NOTICE_MARKER = "stage-e-criteria-changed/1"
LINEAR_API = "https://api.linear.app/graphql"
SNAPSHOT_SUBDIR = "basis-snapshots"
IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
TERMINAL_STATE_TYPES = ("completed", "canceled")
SESSION_PAGE_SIZE = 50
SESSION_MAX_PAGES = 3
DEFAULT_MAX_READS = 25
HISTORY_PAGE = 50
# Copied ticket text is untrusted; the notice neutralizes what a dispatcher parses or a
# driver scans, the same way every other Stage E body does.
_ROUTING_TAG_RE = re.compile(r"\b(repos?|model|agent)\s*=", re.IGNORECASE)
_FENCE_RE = re.compile(r"<(/?)untrusted-", re.IGNORECASE)

SESSIONS_QUERY = """
query CriteriaSnapshotSessions($first: Int!, $after: String) {
  agentSessions(first: $first, after: $after, orderBy: updatedAt) {
    nodes {
      id createdAt updatedAt
      creator { id }
      issue { id identifier team { key } state { type } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""

ISSUE_QUERY = """
query CriteriaSnapshotIssue($id: String!, $history: Int!) {
  issue(id: $id) {
    id identifier description
    history(first: $history) {
      nodes { createdAt updatedDescription descriptionUpdatedBy { id } }
      pageInfo { hasNextPage }
    }
  }
}"""

# The COMPLETE set of Linear mutations this file constructs: exactly one.
COMMENT_MUTATION = """
mutation CriteriaChangedNotice($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}"""


class SnapshotError(Exception):
    """A read or write failed for a reason worth naming — never a silent None."""


# --------------------------------------------------------------------------- #
# Linear I/O
# --------------------------------------------------------------------------- #
def linear_transport(api_key):
    """(query, variables) -> data, over the tracker's GraphQL endpoint. Injected into the
    pass so --selftest can stand a recording fake in its place."""
    def call(query, variables):
        req = urllib.request.Request(
            LINEAR_API, data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Content-Type": "application/json", "Authorization": api_key})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise SnapshotError("tracker HTTP %d" % exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SnapshotError("tracker unreachable (%s)" % exc.__class__.__name__)
        if payload.get("errors"):
            raise SnapshotError("tracker error: %s" % json.dumps(payload["errors"])[:300])
        return payload.get("data") or {}
    return call


# --------------------------------------------------------------------------- #
# Pure logic
# --------------------------------------------------------------------------- #
def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts):
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def criteria_of(description):
    """(acceptance_criteria, out_of_scope) by the ONE parser the resolver uses."""
    fields = pin_fields(description or "")
    return list(fields["acceptance_criteria"]), list(fields["out_of_scope"])


def criteria_sha(acceptance, out_of_scope):
    doc = json.dumps({"acceptance_criteria": list(acceptance), "out_of_scope": list(out_of_scope)},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(doc.encode("utf-8")).hexdigest()


def choose_sessions(nodes, team_keys):
    """{identifier: the newest PERSON-delegated session on it}, over managed teams only.

    A session whose `creator` is null was started by automation or by an agent — the shape
    a coding session produces if it tries to open a second session on its own ticket — so
    it can never take or replace a snapshot."""
    managed = {str(k).upper() for k in (team_keys or [])}
    chosen = {}
    for node in nodes or []:
        issue = (node or {}).get("issue") or {}
        ident = str(issue.get("identifier") or "")
        if not IDENTIFIER_RE.match(ident):
            continue
        if managed and str(((issue.get("team") or {}).get("key")) or "").upper() not in managed:
            continue
        if not ((node.get("creator") or {}).get("id")):
            continue
        prior = chosen.get(ident)
        if prior is None or str(node.get("createdAt") or "") > str(prior.get("createdAt") or ""):
            chosen[ident] = node
    return chosen


def edits_between(history, start, end, has_more):
    """(True | False | None, [editor ids]) — was the description edited in [start, end]?

    True the moment an edit inside the window is seen. Otherwise None whenever the window
    cannot be read whole: no timestamps, no history, or MORE pages than were read — the
    order Linear pages history in is not verified here, so an unread page could hold the
    edit. None is not False."""
    start_dt, end_dt = _parse_iso(start), _parse_iso(end)
    if start_dt is None or end_dt is None or not isinstance(history, list):
        return None, []
    editors, edited = [], False
    for node in history:
        at = _parse_iso((node or {}).get("createdAt"))
        if at is None:
            continue
        if start_dt <= at <= end_dt and (node or {}).get("updatedDescription"):
            edited = True
            editors += [str(u.get("id")) for u in (node.get("descriptionUpdatedBy") or [])
                        if isinstance(u, dict) and u.get("id")]
    if edited:
        return True, sorted(set(editors))
    if has_more:
        return None, []
    return False, []


def needs_snapshot(existing, session):
    """Whether this pass writes a snapshot for the ticket.

    No snapshot ⇒ yes. The same session's ⇒ never: a snapshot is immutable for its session.
    A NEWER person-delegated session ⇒ yes — that is how a legitimate edit becomes the
    basis. An OLDER one (paging order, or a stale read) ⇒ no."""
    if not existing:
        return True
    if existing.get("session_id") == session.get("id"):
        return False
    return str(session.get("createdAt") or "") > str(existing.get("delegated_at") or "")


def build_snapshot(identifier, session, issue, taken_at, history_nodes=None, history_more=False):
    acceptance, out_of_scope = criteria_of((issue or {}).get("description"))
    delegated_at = str(session.get("createdAt") or "")
    edited, editors = edits_between(history_nodes, delegated_at, taken_at, history_more) \
        if history_nodes is not None else (None, [])
    d, t = _parse_iso(delegated_at), _parse_iso(taken_at)
    return {
        "schema": SCHEMA,
        "ticket_id": identifier,
        "session_id": session.get("id"),
        "creator_id": (session.get("creator") or {}).get("id"),
        "delegated_at": delegated_at,
        "taken_at": taken_at,
        "lag_seconds": int((t - d).total_seconds()) if d and t else None,
        "edited_before_snapshot": edited,
        "edited_before_snapshot_by": editors,
        "acceptance_criteria": acceptance,
        "out_of_scope": out_of_scope,
        "criteria_sha256": criteria_sha(acceptance, out_of_scope),
        "notices": [],
    }


def divergence(snapshot, description):
    """None when the live criteria match the snapshot; else what changed."""
    acceptance, out_of_scope = criteria_of(description)
    live_sha = criteria_sha(acceptance, out_of_scope)
    if live_sha == snapshot.get("criteria_sha256"):
        return None
    was_ac, was_oos = snapshot.get("acceptance_criteria") or [], snapshot.get("out_of_scope") or []
    return {"live_sha256": live_sha,
            "added": [c for c in acceptance if c not in was_ac],
            "removed": [c for c in was_ac if c not in acceptance],
            "out_of_scope_changed": list(out_of_scope) != list(was_oos)}


def notice_due(snapshot, div):
    said = {n.get("live_sha256") for n in (snapshot.get("notices") or []) if isinstance(n, dict)}
    return bool(div) and div["live_sha256"] not in said


def _clean(text):
    text = _ROUTING_TAG_RE.sub(lambda m: m.group(1) + " =", str(text))
    text = _FENCE_RE.sub(lambda m: "<" + m.group(1) + "untrusted_", text)
    return " ".join(text.replace(NOTICE_MARKER, "stage-e-criteria-changed-quoted").split())[:300]


def render_notice(snapshot, div):
    lines = [
        "**Stage E — the acceptance criteria changed after this ticket was delegated.**",
        "",
        "They were captured at %s, %s after a person delegated the ticket. The review of "
        "this ticket's pull request judges against the criteria **as delegated**, and tells "
        "the reviewer they changed." % (snapshot.get("taken_at") or "?",
                                        _duration(snapshot.get("lag_seconds"))),
        "",
    ]
    if div["added"]:
        lines.append("Added since delegation:")
        lines += ["- %s" % _clean(c) for c in div["added"][:20]]
        lines.append("")
    if div["removed"]:
        lines.append("Removed since delegation:")
        lines += ["- %s" % _clean(c) for c in div["removed"][:20]]
        lines.append("")
    if div["out_of_scope_changed"]:
        lines += ["The out-of-scope list changed too.", ""]
    lines += [
        "If the new criteria are the intended ones, delegate the ticket again: a session a "
        "person delegates takes a fresh snapshot, and its review judges against that. This "
        "comment is a record, not a prompt — no session was told, and nothing was re-run.",
        "",
        "_%s %s %s — said once per version of the criteria by the Stage E bounce driver._"
        % (NOTICE_MARKER, snapshot.get("ticket_id") or "?", div["live_sha256"][:12]),
    ]
    return "\n".join(lines)


def _duration(seconds):
    if not isinstance(seconds, int) or seconds < 0:
        return "an unknown time"
    if seconds < 120:
        return "%d s" % seconds
    if seconds < 7200:
        return "%d min" % (seconds // 60)
    return "%.1f h" % (seconds / 3600.0)


# --------------------------------------------------------------------------- #
# The store — under the role account's state directory, outside every worktree
# --------------------------------------------------------------------------- #
def snapshot_dir_for(cfg, state_dir):
    return os.path.realpath(os.path.expanduser(
        (cfg or {}).get("basis_snapshot_dir") or os.path.join(state_dir, SNAPSHOT_SUBDIR)))


def read_snapshot(directory, identifier):
    """The snapshot dict, None when there is none. An unreadable or foreign file is a
    SnapshotError — never read as "no snapshot", which would re-take it with whatever the
    ticket says NOW and launder an edit into the basis."""
    path = os.path.join(directory, "%s.json" % identifier)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise SnapshotError("the snapshot for %s could not be read (%s)"
                            % (identifier, exc.__class__.__name__))
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise SnapshotError("the snapshot for %s is not a %s document" % (identifier, SCHEMA))
    return doc


def write_snapshot(directory, doc, archive=None):
    """Atomic, mode 600 in a mode-700 directory. `archive` (the snapshot being replaced)
    is kept as `<TICKET>.<its session>.json` first, so a replacement never loses what the
    earlier session was delegated with."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    ident = doc["ticket_id"]
    if archive:
        _atomic(os.path.join(directory, "%s.%s.json" % (ident, archive.get("session_id") or "prior")),
                archive)
    _atomic(os.path.join(directory, "%s.json" % ident), doc)


def _atomic(path, doc):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".snapshot-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# The pass
# --------------------------------------------------------------------------- #
def snapshot_pass(cfg, state_dir, call, dry_run=False, deadline=None, now=None,
                  max_reads=DEFAULT_MAX_READS):
    """One pass: take the snapshots that are owed, say the divergences that are new.

    Returns {"taken", "replaced", "notices", "unchanged", "reads", "capped", "problems",
    "detail"}. A problem with one ticket is that ticket's; the pass carries on. A failure
    to list sessions at all raises SnapshotError — the caller says "could not look",
    never "nothing changed"."""
    directory = snapshot_dir_for(cfg, state_dir)
    team_keys = (cfg or {}).get("team_keys") or []
    now = now or _now_iso()
    result = {"taken": [], "replaced": [], "notices": [], "unchanged": 0, "reads": 0,
              "capped": [], "problems": [], "detail": []}

    nodes, cursor = [], None
    for _ in range(SESSION_MAX_PAGES):
        data = call(SESSIONS_QUERY, {"first": SESSION_PAGE_SIZE, "after": cursor})
        page = (data or {}).get("agentSessions") or {}
        nodes += page.get("nodes") or []
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")

    for ident, session in sorted(choose_sessions(nodes, team_keys).items()):
        if deadline is not None and time.monotonic() >= deadline:
            result["capped"].append(ident)
            continue
        state_type = str((((session.get("issue") or {}).get("state")) or {}).get("type") or "")
        if state_type in TERMINAL_STATE_TYPES:
            # Done or canceled: no review will read it, and a first snapshot taken now
            # would record long-after criteria under an "as delegated" name.
            continue
        try:
            existing = read_snapshot(directory, ident)
        except SnapshotError as exc:
            result["problems"].append(str(exc))
            continue
        owed = needs_snapshot(existing, session)
        if result["reads"] >= max_reads:
            result["capped"].append(ident)
            continue
        result["reads"] += 1
        try:
            data = call(ISSUE_QUERY, {"id": (session.get("issue") or {}).get("id"),
                                      "history": HISTORY_PAGE})
        except SnapshotError as exc:
            result["problems"].append("%s: %s" % (ident, exc))
            continue
        issue = (data or {}).get("issue") or {}
        if not issue:
            result["problems"].append("%s: the tracker returned no issue" % ident)
            continue

        if owed:
            hist = issue.get("history") or {}
            doc = build_snapshot(ident, session, issue, now,
                                 history_nodes=hist.get("nodes") if "nodes" in hist else None,
                                 history_more=bool((hist.get("pageInfo") or {}).get("hasNextPage")))
            if not dry_run:
                try:
                    write_snapshot(directory, doc, archive=existing)
                except OSError as exc:
                    result["problems"].append("%s: the snapshot could not be written (%s)"
                                              % (ident, exc.__class__.__name__))
                    continue
            (result["replaced"] if existing else result["taken"]).append(ident)
            result["detail"].append("%s: %s snapshot of %d criterion/criteria, %s after "
                                    "delegation, edited before it: %s"
                                    % (ident, "replaced" if existing else "took",
                                       len(doc["acceptance_criteria"]),
                                       _duration(doc["lag_seconds"]), doc["edited_before_snapshot"]))
            continue

        div = divergence(existing, issue.get("description"))
        if not div or not notice_due(existing, div):
            result["unchanged"] += 1
            continue
        body = render_notice(existing, div)
        if dry_run:
            result["notices"].append(ident)
            result["detail"].append("%s: [dry-run] would say the criteria changed" % ident)
            continue
        try:
            sent = call(COMMENT_MUTATION, {"issueId": issue.get("id"), "body": body})
        except SnapshotError as exc:
            result["problems"].append("%s: the change notice could not be posted (%s)" % (ident, exc))
            continue
        if not ((sent or {}).get("commentCreate") or {}).get("success"):
            result["problems"].append("%s: the tracker did not accept the change notice" % ident)
            continue
        existing["notices"] = (existing.get("notices") or []) + [
            {"live_sha256": div["live_sha256"], "at": now, "added": div["added"],
             "removed": div["removed"]}]
        try:
            write_snapshot(directory, existing)
        except OSError as exc:
            # Said, and the next pass says the change again: a repeat is safe, a lost
            # record of having said it is not worth hiding.
            result["problems"].append("%s: the notice posted but was not recorded (%s)"
                                      % (ident, exc.__class__.__name__))
        result["notices"].append(ident)
        result["detail"].append("%s: said the criteria changed since delegation" % ident)
    return result


def summarize(result):
    return ("criteria snapshots: took %s · replaced %s · change notices %s · unchanged %d · "
            "reads %d%s%s" % (result["taken"] or "none", result["replaced"] or "none",
                              result["notices"] or "none", result["unchanged"], result["reads"],
                              " · capped %s" % result["capped"] if result["capped"] else "",
                              " · PROBLEMS %d" % len(result["problems"]) if result["problems"] else ""))


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    desc = ("## Acceptance criteria\n\n- [ ] do the thing\n- [ ] test it\n\n"
            "## Out of scope\n\n- the other thing\n")
    edited = desc.replace("- [ ] test it\n", "- [ ] test it\n- [ ] also rewrite the auth layer\n")

    def session(sid, ident="KIT-7", created="2026-09-16T10:00:00Z", creator="u-owner",
                team="KIT", state="started", issue_id="iss-7"):
        return {"id": sid, "createdAt": created, "updatedAt": created,
                "creator": {"id": creator} if creator else None,
                "issue": {"id": issue_id, "identifier": ident, "team": {"key": team},
                          "state": {"type": state}}}

    # ── Selection: only a PERSON's session, only a managed team ────────────
    nodes = [session("s-agent", creator=None, created="2026-09-16T11:00:00Z"),
             session("s-human"), session("s-other", ident="ENG-1", team="ENG"),
             session("s-bad", ident="not-an-id")]
    chosen = choose_sessions(nodes, ["KIT"])
    check("a null-creator session never takes a snapshot, even when newer",
          (sorted(chosen), chosen["KIT-7"]["id"]), (["KIT-7"], "s-human"))
    check("with no team list every well-formed identifier counts",
          sorted(choose_sessions(nodes, [])), ["ENG-1", "KIT-7"])

    # ── The window between delegation and the capture ──────────────────────
    hist = [{"createdAt": "2026-09-16T10:01:00Z", "updatedDescription": True,
             "descriptionUpdatedBy": [{"id": "u-agent"}]},
            {"createdAt": "2026-09-16T09:00:00Z", "updatedDescription": True}]
    check("an edit inside the window is caught, with its editor",
          edits_between(hist, "2026-09-16T10:00:00Z", "2026-09-16T10:04:00Z", False),
          (True, ["u-agent"]))
    check("an edit before delegation is not an edit after it",
          edits_between(hist[1:], "2026-09-16T10:00:00Z", "2026-09-16T10:04:00Z", False), (False, []))
    check("a history page that stops short of delegation, with more pages, is UNKNOWN",
          edits_between([{"createdAt": "2026-09-16T10:03:00Z"}], "2026-09-16T10:00:00Z",
                        "2026-09-16T10:04:00Z", True), (None, []))
    check("more pages is UNKNOWN even when the page read reaches back past delegation",
          edits_between([{"createdAt": "2026-09-16T08:00:00Z"}], "2026-09-16T10:00:00Z",
                        "2026-09-16T10:04:00Z", True), (None, []))
    check("no history at all is UNKNOWN, never False",
          edits_between(None, "2026-09-16T10:00:00Z", "2026-09-16T10:04:00Z", False), (None, []))

    # ── The snapshot ────────────────────────────────────────────────────────
    snap = build_snapshot("KIT-7", session("s-human"), {"description": desc},
                          "2026-09-16T10:04:00Z", history_nodes=hist[1:])
    check("the snapshot holds the delegated criteria, by the one parser",
          (snap["acceptance_criteria"], snap["out_of_scope"]),
          (["do the thing", "test it"], ["the other thing"]))
    check("the snapshot records how late it was, and that nothing was edited in between",
          (snap["delegated_at"], snap["lag_seconds"], snap["edited_before_snapshot"]),
          ("2026-09-16T10:00:00Z", 240, False))
    check("the snapshot is keyed by the session and the person who delegated",
          (snap["session_id"], snap["creator_id"], snap["schema"]), ("s-human", "u-owner", SCHEMA))

    check("no snapshot yet ⇒ one is owed", needs_snapshot(None, session("s-human")), True)
    check("the same session's snapshot is never re-taken", needs_snapshot(snap, session("s-human")), False)
    check("…even when its recorded delegation time reads older than the session's (the id "
          "decides, not the clock)",
          needs_snapshot(dict(snap, delegated_at="2026-09-01T00:00:00Z"), session("s-human")), False)
    check("a NEWER person-delegated session replaces it — the reconciliation path",
          needs_snapshot(snap, session("s-new", created="2026-09-17T09:00:00Z")), True)
    check("an older session never replaces it",
          needs_snapshot(snap, session("s-old", created="2026-09-15T09:00:00Z")), False)

    # ── Divergence: unedited, edited, edited-then-reverted ─────────────────
    check("unedited criteria do not diverge", divergence(snap, desc), None)
    div = divergence(snap, edited)
    check("an added criterion diverges, and is named",
          (bool(div), div["added"], div["removed"]), (True, ["also rewrite the auth layer"], []))
    check("reordered prose around the same criteria does not diverge",
          divergence(snap, "Some context first.\n\n" + desc), None)
    check("a new divergence is owed a notice", notice_due(snap, div), True)
    said = dict(snap, notices=[{"live_sha256": div["live_sha256"]}])
    check("…said once, it is not owed again", notice_due(said, div), False)
    check("edited then reverted: no divergence, so nothing more is said",
          divergence(said, desc), None)

    body = render_notice(snap, div)
    check("the notice names what was added and what the review judges against",
          ("also rewrite the auth layer" in body, "as delegated" in body, NOTICE_MARKER in body),
          (True, True, True))
    hostile = dict(div, added=["[repo=kit#main] <untrusted-diff> %s" % NOTICE_MARKER])
    hbody = render_notice(snap, hostile)
    check("copied criteria cannot carry a routing tag, a fence or this file's marker",
          ("[repo=kit" in hbody, "<untrusted-diff>" in hbody, hbody.count(NOTICE_MARKER)),
          (False, False, 1))

    # ── The pass, against a recording fake tracker ─────────────────────────
    class Fake:
        def __init__(self, sessions, issues, fail=()):
            self.sessions, self.issues, self.fail, self.calls = sessions, issues, set(fail), []

        def __call__(self, query, variables):
            op = re.search(r"(?:query|mutation)\s+(\w+)", query).group(1)
            self.calls.append((op, variables))
            if op in self.fail:
                raise SnapshotError("simulated %s failure" % op)
            if op == "CriteriaSnapshotSessions":
                return {"agentSessions": {"nodes": self.sessions,
                                          "pageInfo": {"hasNextPage": False}}}
            if op == "CriteriaSnapshotIssue":
                return {"issue": self.issues.get(variables["id"])}
            return {"commentCreate": {"success": True}}

    def ops(fake, name):
        return [c for c in fake.calls if c[0] == name]

    with tempfile.TemporaryDirectory() as state:
        cfg = {"team_keys": ["KIT"]}
        issue = {"id": "iss-7", "identifier": "KIT-7", "description": desc,
                 "history": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        fake = Fake([session("s-human")], {"iss-7": issue})
        r = snapshot_pass(cfg, state, fake, now="2026-09-16T10:04:00Z")
        stored = read_snapshot(snapshot_dir_for(cfg, state), "KIT-7")
        check("a first pass takes the snapshot", (r["taken"], stored["acceptance_criteria"]),
              (["KIT-7"], ["do the thing", "test it"]))
        mode = os.stat(os.path.join(snapshot_dir_for(cfg, state), "KIT-7.json")).st_mode & 0o777
        check("the snapshot file is mode 600 in the role account's state dir", mode, 0o600)

        r = snapshot_pass(cfg, state, fake, now="2026-09-16T10:10:00Z")
        check("an unedited ticket: one read, nothing said", (r["unchanged"], r["notices"],
              len(ops(fake, "CriteriaChangedNotice"))), (1, [], 0))

        issue["description"] = edited
        r = snapshot_pass(cfg, state, fake, now="2026-09-16T10:20:00Z")
        notice = ops(fake, "CriteriaChangedNotice")
        check("an edit after delegation is said ONCE, top level on the coding ticket",
              (r["notices"], len(notice), notice[0][1]["issueId"] if notice else None),
              (["KIT-7"], 1, "iss-7"))
        check("…and the snapshot itself is NOT rewritten with the edit",
              read_snapshot(snapshot_dir_for(cfg, state), "KIT-7")["acceptance_criteria"],
              ["do the thing", "test it"])
        r = snapshot_pass(cfg, state, fake, now="2026-09-16T10:30:00Z")
        check("the next pass does not repeat it", len(ops(fake, "CriteriaChangedNotice")), 1)

        issue["description"] = desc
        r = snapshot_pass(cfg, state, fake, now="2026-09-16T10:40:00Z")
        check("reverted: nothing more is said", (r["unchanged"], len(ops(fake, "CriteriaChangedNotice"))),
              (1, 1))

        issue["description"] = edited
        fake.sessions = [session("s-human"), session("s-redelegated", created="2026-09-16T11:00:00Z")]
        r = snapshot_pass(cfg, state, fake, now="2026-09-16T11:02:00Z")
        stored = read_snapshot(snapshot_dir_for(cfg, state), "KIT-7")
        check("a person delegating again replaces the snapshot with the new criteria",
              (r["replaced"], stored["session_id"], "also rewrite the auth layer" in stored["acceptance_criteria"]),
              (["KIT-7"], "s-redelegated", True))
        check("…and keeps the earlier one beside it",
              os.path.exists(os.path.join(snapshot_dir_for(cfg, state), "KIT-7.s-human.json")), True)

        fake.sessions = [session("s-redelegated", created="2026-09-16T11:00:00Z", state="completed"),
                         session("s-never", ident="KIT-8", issue_id="iss-8", state="canceled")]
        before = len(fake.calls)
        r = snapshot_pass(cfg, state, fake, now="2026-09-16T12:00:00Z")
        check("a done ticket is not read again, and a done ticket never gets a first snapshot",
              ([c[0] for c in fake.calls[before:]], r["taken"]), (["CriteriaSnapshotSessions"], []))

        with open(os.path.join(snapshot_dir_for(cfg, state), "KIT-9.json"), "w") as fh:
            fh.write("not json")
        fake.sessions = [session("s-9", ident="KIT-9", issue_id="iss-9")]
        r = snapshot_pass(cfg, state, fake, now="2026-09-16T12:00:00Z")
        check("an unreadable snapshot is a PROBLEM, never re-taken over",
              (r["taken"], len(r["problems"])), ([], 1))

    with tempfile.TemporaryDirectory() as state:
        fake = Fake([session("s-human")], {"iss-7": {"id": "iss-7", "description": desc}},
                    fail=("CriteriaSnapshotSessions",))
        try:
            snapshot_pass({"team_keys": ["KIT"]}, state, fake)
            failures.append("a pass that could not list sessions must raise, not return empty")
        except SnapshotError:
            pass
        fake = Fake([session("s-human")], {"iss-7": {"id": "iss-7", "description": desc}})
        r = snapshot_pass({"team_keys": ["KIT"]}, state, fake, dry_run=True)
        check("a dry run writes nothing", (r["taken"], os.path.exists(os.path.join(state, SNAPSHOT_SUBDIR))),
              (["KIT-7"], False))
        fake = Fake([session("s-%d" % i, ident="KIT-%d" % i, issue_id="iss-%d" % i) for i in range(4)],
                    {"iss-%d" % i: {"id": "iss-%d" % i, "description": desc} for i in range(4)})
        r = snapshot_pass({"team_keys": ["KIT"]}, state, fake, max_reads=2)
        check("the read cap leaves the rest for the next pass, and names them",
              (len(r["taken"]), len(r["capped"])), (2, 2))

    # ── The write guard ────────────────────────────────────────────────────
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    code = src.split("def selftest", 1)[0]
    check("exactly one mutation document exists: the change notice",
          sorted(set(re.findall(r"^\s*mutation\s+(\w+)", code, re.MULTILINE))),
          ["CriteriaChangedNotice"])
    for banned in ("issueUpdate", "issueCreate", "issueAddLabel", "issueArchive", "agentSessionCreate"):
        check("no %s anywhere in the pass" % banned, banned in code, False)

    if failures:
        print("FAIL pipeline_criteria_snapshot selftest (%d of %d):" % (len(failures), cases[0]))
        for line in failures:
            print("  -", line)
        return 1
    print("ok — pipeline_criteria_snapshot: %d cases — only a person's delegation takes a "
          "snapshot, it is immutable for its session and replaced only by a newer person-"
          "delegated one, the window before the capture is read as edited/not/unknown, a "
          "divergence is said once top-level (edited-then-reverted says nothing more), an "
          "unreadable snapshot is a problem never re-taken, one mutation only" % cases[0])
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Stage E criteria snapshot (KIT-131).")
    sub = p.add_subparsers(dest="cmd")
    run_p = sub.add_parser("pass", help="one snapshot pass")
    run_p.add_argument("--config", default="~/.stage-e/config.json")
    run_p.add_argument("--dry-run", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.cmd != "pass":
        p.error("a command is required: pass (or --selftest)")
    try:
        with open(os.path.expanduser(args.config), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("FAIL: could not read --config: %s\n" % exc)
        return EXIT_USAGE
    key_env = cfg.get("linear_api_key_env") or cfg.get("linear_key_env") or "STAGE_E_LINEAR_API_KEY"
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        sys.stderr.write("FAIL: $%s is unset — the snapshot pass reads the tracker with the "
                         "owner-scoped key\n" % key_env)
        return EXIT_USAGE
    state_dir = os.path.expanduser(cfg.get("state_dir") or "~/.stage-e/state")
    try:
        result = snapshot_pass(cfg, state_dir, linear_transport(api_key), dry_run=args.dry_run)
    except SnapshotError as exc:
        sys.stderr.write("FAIL: could not look: %s — this is not 'nothing changed'\n" % exc)
        return EXIT_USAGE
    print(summarize(result))
    for line in result["detail"]:
        print("  " + line)
    for line in result["problems"]:
        sys.stderr.write("PROBLEM: %s\n" % line)
    return EXIT_USAGE if result["problems"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
