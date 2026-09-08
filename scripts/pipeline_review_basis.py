#!/usr/bin/env python3
"""Stage E basis resolver — "what the ticket asked, as of when work started," or an
honestly empty answer the reviewer will decline on. See
docs/adr/2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md, decision 2 (the
snapshot problem — "the hardest question and it has no clean answer under Cyrus").

THE PROBLEM THIS FILE DOES NOT PRETEND TO SOLVE

  Under the old design a dispatcher-written PIN froze the ticket's acceptance criteria at
  dispatch time, outside the worktree, where the coding session could not touch it. Cyrus
  writes no pin. Worse: a Cyrus session holds a workspace-scoped Linear write token
  (docs/AUTONOMY.md) and can edit its OWN acceptance criteria mid-flight — so "read the
  live ticket at review time" does not just weaken the review, it lets the reviewed party
  rewrite the standard it is judged against. This file tries three sources, in order of
  how hard they are to forge, and is honest on the label about which one it used.

  | Tier | Source | Forgeable by the coding session? |
  |---|---|---|
  | 1 `history`     | Linear's server-side description history, as of delegation time | No, if it existed — see the spike note below |
  | 2 `reconciler`  | An independent daemon's delegation-time snapshot, outside every worktree | No, once such a daemon exists (none ships yet) |
  | 3 `live`        | The live ticket, with post-delegation edits FLAGGED | Yes — the weakest tier, used honestly, never silently |

THE KIT-92 SPIKE — RECORDED HERE, NOT JUST IN THE ADR

  Tier 1 needs a Linear query that reliably returns "this issue's description as it stood
  at timestamp T". Attempting to verify that from this codebase:

    - No tool in the Linear MCP surface available to this kit's sessions (get_issue,
      list_comments, list_issues, …) exposes issue HISTORY or an as-of-timestamp read —
      get_issue returns the CURRENT description only, and there is no companion
      "history" tool.
    - Linear's public GraphQL schema does expose `Issue.history` (an `IssueHistory`
      connection), but on best available understanding of that connection it records
      FIELD-LEVEL metadata transitions (state, assignee, title, an `updatedDescription`
      boolean flag) — not the description's own prior text. A boolean "it changed" is not
      "what it used to say".
    - This session had no live Linear credential or confirmed egress to `api.linear.app`
      to test a raw query directly, so this is a documented best-effort finding, not a
      measurement — the honest thing, per this kit's own "verify, don't assume" rule, is
      to say exactly that rather than ship a guessed query against a schema nobody here
      re-read live.

  **Conclusion: tier 1 is not implementable from what is verifiable here. Tier 3 (live +
  flag) is the shipped default.** `resolve_tier1()` below is a real, tested interface —
  not a placeholder comment — so that the day someone confirms a working history query
  (see the candidate query in the phase doc handed to Braeden), wiring it in is a change
  to ONE function's body, not to any caller. Tier 2 is deliberately unbuilt: this file is
  only the CONSUMER half (`resolve_tier2` reads a snapshot if one exists); the reconciler
  daemon that would WRITE one is filed as future work, per the ADR.

WHY resolve_tier1's SIGNATURE IS THE FORGERY-RESISTANCE ARGUMENT

  `resolve_tier1(ticket_id, delegated_at, history_fetcher)` does not accept the live
  issue at all. There is nothing in this function for a later description edit to
  influence — it only ever asks `history_fetcher` for the snapshot as of `delegated_at`,
  a moment already in the past by the time this runs. --selftest proves this with a fake
  fetcher standing in for a real history backend: the fetcher's answer for a fixed
  `delegated_at` is provably immune to what an out-of-band "current" description reads.

WHAT "DELEGATION TIME" MEANS HERE, WITH NO PIN TO CARRY IT

  Linear's own `Issue.startedAt` — the time the issue entered a `started`-type workflow
  state — is a reasonable, session-independent proxy: Cyrus moves a delegated issue into
  its lowest-ordered started state on session start (field guide §04), a transition the
  coding session did not initiate. It is not as strong as a dispatcher-written pin (a
  session COULD in principle move a ticket back and then forward again to reset it — a
  more visible act than editing text, and not defended against here), but it is the best
  session-independent timestamp this file can read without new infrastructure. A ticket
  that has never started falls back to `createdAt`, a weaker proxy noted as such.

WHAT IT IS AND IS NOT

  It is: the three-tier resolver, the live-ticket read, the edit-after-delegation flag,
  and a basis file in the exact shape `pipeline_review_local.py --basis-file` already
  consumes. It is READ-ONLY against Linear — it never writes a comment, a label, or a
  state change, asserted the same way `pipeline_review_local.py` asserts its own
  comment-only guarantee.

  It is not: the reviewer that consumes the basis (KIT-90, already merged), the poller
  that would wire this in (KIT-91 — one added `--basis-file` flag once this lands), or
  the reconciler daemon tier 2 is a consumer interface for (unbuilt, future work).

Usage:
    pipeline_review_basis.py TICKET-ID --team-key KIT [--out FILE]
                             [--api-key-env LINEAR_API_KEY] [--ticket-file F]
                             [--snapshot-dir DIR]
    pipeline_review_basis.py --selftest

Exit: 0 = a basis was resolved and written/printed (an empty acceptance_criteria list is
          an HONEST empty answer, not a failure of this script — the reviewer declines on
          it, which is the correct downstream outcome)
      2 = usage/IO/API error — Linear could not be reached at all; nothing is written
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_ticket_dor import pin_fields  # noqa: E402  (ONE description parser, contract §3)

EXIT_OK = 0
EXIT_USAGE = 2

LINEAR_API = "https://api.linear.app/graphql"
TICKET_ID_RE = re.compile(r"^([A-Z][A-Z0-9]*)-([0-9]+)$")
DEFAULT_SNAPSHOT_DIR = "~/.claude/pipeline/stage-e/basis-snapshots"

ISSUE_QUERY = """
query($team: String!, $number: Float!) {
  issues(filter: { team: { key: { eq: $team } }, number: { eq: $number } }, first: 1) {
    nodes { identifier description updatedAt createdAt startedAt url }
  }
}"""


class LinearError(Exception):
    """A Linear API call failed for a reason worth naming — never a silent None."""


# --------------------------------------------------------------------------- #
# Linear I/O — read-only. No mutation is ever constructed here.
# --------------------------------------------------------------------------- #
def _graphql(query, variables, api_key):
    req = urllib.request.Request(
        LINEAR_API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        # Linear personal API keys go in Authorization RAW (no "Bearer "), matching
        # scripts/pipeline_dispatch_local.py's own convention for the same header.
        headers={"Content-Type": "application/json", "Authorization": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise LinearError("Linear API HTTP %d: %r" % (exc.code, exc.read()[:400]))
    except urllib.error.URLError as exc:
        raise LinearError("could not reach the Linear API: %s" % exc.reason)
    except (OSError, ValueError) as exc:
        raise LinearError("Linear API call failed: %s" % exc)
    if payload.get("errors"):
        raise LinearError("Linear API error: %s" % json.dumps(payload["errors"])[:400])
    return payload.get("data") or {}


def fetch_issue(ticket_id, team_key, api_key):
    """The issue node, or None if it does not exist. Raises LinearError on any
    transport/API failure — a hard failure is never mistaken for "ticket not found"."""
    match = TICKET_ID_RE.match(ticket_id or "")
    if not match:
        raise LinearError("%r is not a TEAM-123 shaped ticket id" % ticket_id)
    data = _graphql(ISSUE_QUERY, {"team": team_key, "number": float(match.group(2))}, api_key)
    nodes = ((data.get("issues") or {}).get("nodes")) or []
    return nodes[0] if nodes else None


# --------------------------------------------------------------------------- #
# Pure logic — no I/O below this line, so --selftest can exercise all of it
# --------------------------------------------------------------------------- #
def _parse_iso(ts):
    """A UTC timestamp string -> datetime, or None. Never raises."""
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _history_unavailable(ticket_id, delegated_at):
    """The honest default backend for tier 1: unavailable, structurally, today.

    See the module docstring's spike note. This function's ENTIRE body is the
    up-to-date statement of that finding; nothing upstream needs to change when it
    does — resolve_tier1's contract stays "ask the fetcher for the as-of snapshot".
    """
    return None


def resolve_tier1(ticket_id, delegated_at, history_fetcher):
    """Ask ONLY for the as-of-delegation snapshot — never the live description.

    No `issue` parameter exists on this function. That omission IS the
    forgery-resistance argument: there is nothing here for a later description edit
    to influence, because this call cannot see the live description at all.
    """
    if not delegated_at:
        return None
    return history_fetcher(ticket_id, delegated_at)


def resolve_tier2(ticket_id, snapshot_dir):
    """A reconciler's delegation-time snapshot, if one has ever been written.

    No reconciler ships in this round (ADR: filed as future work once the tier-1 spike
    answer was known). This is the CONSUMER half only — a future daemon starts writing
    `<snapshot_dir>/<ticket_id>.json` and tier 2 begins working with no change here.
    """
    if not snapshot_dir:
        return None
    path = os.path.join(os.path.realpath(os.path.expanduser(snapshot_dir)), "%s.json" % ticket_id)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    ac = [s for s in (doc.get("acceptance_criteria") or []) if str(s).strip()]
    if not ac:
        return None
    return {"acceptance_criteria": ac,
            "out_of_scope": [s for s in (doc.get("out_of_scope") or []) if str(s).strip()]}


def resolve_tier3(issue):
    """The live ticket, parsed by the ONE description parser (contract §3) — the same
    `check_ticket_dor.pin_fields` the tier-0 dispatcher pins from. A missing or empty
    section yields an empty list, never an inferred one — never this file's business
    to invent a criterion the ticket does not carry."""
    fields = pin_fields((issue or {}).get("description"))
    return {"acceptance_criteria": fields["acceptance_criteria"],
            "out_of_scope": fields["out_of_scope"]}


def _criteria_changed(tier, data, issue):
    """True, False, or None — AND NONE IS NOT FALSE.

    Answerable only by a tier that saw the criteria as they stood at delegation:
    compare that list against the live one. Tier 3 reads the ticket as it is NOW
    and holds no record of what it said before, so it cannot answer either way
    and must say so.

    THE DEFECT THIS REPLACES. This was `issue.updatedAt > issue.startedAt`. That
    is the RECORD's modification time — bumped by a state move, a label, an
    assignee, or the pull-request attachment the poller discovers the work by —
    so it was true on very nearly every reviewed pull request. The reviewer is
    told in the same breath that a true flag "is itself a `scope` finding worth
    raising", so a signal that fires always was manufacturing scope findings on
    clean pull requests, and a finding at or above the threshold spends a real
    bounce. A flag that is always on is not a flag; worse, it made the one case
    it exists for — a session that really did rewrite its own acceptance
    criteria — indistinguishable from every other pull request.
    """
    if tier not in ("history", "reconciler"):
        return None
    at_delegation = [str(s) for s in (data.get("acceptance_criteria") or [])]
    live = [str(s) for s in (resolve_tier3(issue).get("acceptance_criteria") or [])]
    return at_delegation != live


def resolve_basis(ticket_id, issue, snapshot_dir=None, history_fetcher=None):
    """The basis dict, in the exact shape `pipeline_review_local.py --basis-file`
    already reads: acceptance_criteria[], out_of_scope[], basis_tier,
    criteria_changed_after_delegation.

    Tiers are tried in trust order (1 -> 2 -> 3) and the first with a NON-EMPTY
    acceptance-criteria list wins — an empty tier-1/2 answer is not "found nothing
    useful", it is "this mechanism could not answer", so the search continues. If
    every tier comes back empty, the returned basis is honestly empty and
    `basis_tier` names the last tier tried (`live`) — the reviewer declines on an
    empty list regardless of which tier produced it, so naming the tier here is for
    the human reading the eventual PR comment, not for any downstream branching.

    `criteria_changed_after_delegation` is TRUE, FALSE or NONE, and None is not
    False: only a tier that can see what the criteria said AT DELEGATION can
    answer it at all. See `_criteria_changed`.
    """
    issue = issue or {}
    delegated_at_str = issue.get("startedAt") or issue.get("createdAt")

    fetcher = history_fetcher or _history_unavailable
    tier, data = None, None

    t1 = resolve_tier1(ticket_id, delegated_at_str, fetcher)
    if t1 and t1.get("acceptance_criteria"):
        tier, data = "history", t1
    if data is None:
        t2 = resolve_tier2(ticket_id, snapshot_dir)
        if t2 and t2.get("acceptance_criteria"):
            tier, data = "reconciler", t2
    if data is None:
        tier, data = "live", resolve_tier3(issue)

    changed = _criteria_changed(tier, data, issue)

    return {
        "acceptance_criteria": data.get("acceptance_criteria") or [],
        "out_of_scope": data.get("out_of_scope") or [],
        "basis_tier": tier,
        "criteria_changed_after_delegation": changed,
        "ticket_id": ticket_id,
        "delegated_at": delegated_at_str,
    }


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def run(args):
    if args.ticket_file:
        try:
            with open(args.ticket_file, encoding="utf-8") as fh:
                issue = json.load(fh)
        except (OSError, ValueError) as exc:
            sys.stderr.write("FAIL: --ticket-file %s: %s\n" % (args.ticket_file, exc))
            return EXIT_USAGE
        if not isinstance(issue, dict):
            sys.stderr.write("FAIL: --ticket-file must hold one issue object.\n")
            return EXIT_USAGE
    else:
        api_key = os.environ.get(args.api_key_env, "").strip()
        if not api_key:
            sys.stderr.write("FAIL: $%s is unset, and no --ticket-file was given. Export "
                              "the key (never pass it as a flag) or resolve offline with "
                              "--ticket-file.\n" % args.api_key_env)
            return EXIT_USAGE
        if not args.team_key:
            sys.stderr.write("FAIL: --team-key is required against the live API.\n")
            return EXIT_USAGE
        try:
            issue = fetch_issue(args.ticket_id, args.team_key, api_key)
        except LinearError as exc:
            # A hard reach failure. No file is written: a poller calling this script
            # must treat a non-zero exit and no --out file as "no basis", the same
            # decline path an empty acceptance_criteria list produces downstream.
            sys.stderr.write("FAIL: could not reach Linear for %s: %s\n" % (args.ticket_id, exc))
            return EXIT_USAGE
        if issue is None:
            sys.stderr.write("FAIL: %s not found in team %s.\n" % (args.ticket_id, args.team_key))
            return EXIT_USAGE

    basis = resolve_basis(args.ticket_id, issue, args.snapshot_dir)
    if not basis["acceptance_criteria"]:
        sys.stderr.write(
            "NOTE: %s carries no acceptance criteria reachable by any tier (tier tried: "
            "%s). The basis is honestly empty; the reviewer will decline on it, which is "
            "the correct outcome here, not a bug in this resolver.\n"
            % (args.ticket_id, basis["basis_tier"]))

    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(basis, fh, indent=2)
        print("wrote %s: tier=%s, %d criterion/criteria, changed_after_delegation=%s"
              % (args.out, basis["basis_tier"], len(basis["acceptance_criteria"]),
                 basis["criteria_changed_after_delegation"]))
    else:
        print(json.dumps(basis, indent=2))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    # 1. _parse_iso: with and without fractional seconds, and the failure shapes.
    check("parse-iso-basic", _parse_iso("2026-09-05T22:26:57Z") is not None, True)
    check("parse-iso-fractional", _parse_iso("2026-09-05T22:26:57.658Z") is not None, True)
    check("parse-iso-none", _parse_iso(None), None)
    check("parse-iso-empty", _parse_iso(""), None)
    check("parse-iso-garbage", _parse_iso("not-a-timestamp"), None)
    check("parse-iso-orders", _parse_iso("2026-09-05T22:26:58Z") > _parse_iso("2026-09-05T22:26:57Z"), True)

    # 2. Tier 1: a fake history backend answers, and the answer is stable regardless
    #    of what a "live" description reads NOW — the forgery-resistance argument,
    #    proven by the function signature accepting no live issue at all.
    snapshot_calls = []

    def fake_history(ticket_id, delegated_at):
        snapshot_calls.append((ticket_id, delegated_at))
        return {"acceptance_criteria": ["as it was at delegation"], "out_of_scope": ["x"]}

    live_desc_now = ["not consulted"]  # a value tier 1 must never read
    t1 = resolve_tier1("KIT-1", "2026-01-01T00:00:00Z", fake_history)
    live_desc_now[0] = "## Acceptance criteria\n\n- [ ] something a session edited in later"
    t1_again = resolve_tier1("KIT-1", "2026-01-01T00:00:00Z", fake_history)
    check("tier1 answers from the fetcher", t1["acceptance_criteria"], ["as it was at delegation"])
    check("tier1 is immune to a later 'live' edit (same delegated_at -> same answer)",
          t1_again, t1)
    check("tier1 asked for the SAME delegated_at both times",
          snapshot_calls, [("KIT-1", "2026-01-01T00:00:00Z"), ("KIT-1", "2026-01-01T00:00:00Z")])
    check("the real default backend reports unavailable, not a guess",
          resolve_tier1("KIT-1", "2026-01-01T00:00:00Z", _history_unavailable), None)
    check("tier1 with no delegated_at asks nothing", resolve_tier1("KIT-1", None, fake_history), None)

    # 3. Tier 2: consumes a snapshot file if present, ignores an absent/malformed one.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        good = os.path.join(tmp, "KIT-2.json")
        with open(good, "w", encoding="utf-8") as fh:
            json.dump({"acceptance_criteria": ["reconciled criterion"], "out_of_scope": []}, fh)
        t2 = resolve_tier2("KIT-2", tmp)
        check("tier2 reads a real snapshot", t2["acceptance_criteria"], ["reconciled criterion"])
        check("tier2 is None for a ticket with no snapshot", resolve_tier2("KIT-3", tmp), None)
        empty = os.path.join(tmp, "KIT-4.json")
        with open(empty, "w", encoding="utf-8") as fh:
            json.dump({"acceptance_criteria": []}, fh)
        check("tier2 with an empty ac list is None (search continues)", resolve_tier2("KIT-4", tmp), None)
        with open(os.path.join(tmp, "KIT-5.json"), "w", encoding="utf-8") as fh:
            fh.write("not json")
        check("tier2 malformed JSON is None, not a crash", resolve_tier2("KIT-5", tmp), None)
    check("tier2 with no snapshot_dir at all is None", resolve_tier2("KIT-6", None), None)
    check("tier2 for a directory that does not exist is None",
          resolve_tier2("KIT-7", "/nonexistent/snapshot/dir"), None)

    # 4. Tier 3: the live-ticket floor, via the ONE description parser.
    issue_with_ac = {
        "identifier": "KIT-8",
        "description": "## Acceptance criteria\n\n- [ ] do the thing\n\n## Out of scope\n\n- not this\n",
        "startedAt": "2026-09-05T10:00:00Z",
        "createdAt": "2026-09-05T09:00:00Z",
        "updatedAt": "2026-09-05T09:30:00Z",  # BEFORE startedAt -> not changed after delegation
    }
    t3 = resolve_tier3(issue_with_ac)
    check("tier3 parses acceptance criteria", t3["acceptance_criteria"], ["do the thing"])
    check("tier3 parses out-of-scope", t3["out_of_scope"], ["not this"])
    check("tier3 on a description with no headings is empty, not invented",
          resolve_tier3({"description": "just some prose"}), {"acceptance_criteria": [], "out_of_scope": []})

    # 5. resolve_basis: tier ordering, the changed-after-delegation flag (both ways),
    #    and the honest empty answer when every tier comes up empty.
    basis_t3 = resolve_basis("KIT-8", issue_with_ac, snapshot_dir=None, history_fetcher=_history_unavailable)
    check("basis falls through to tier 'live' when 1 and 2 are unavailable",
          basis_t3["basis_tier"], "live")
    check("basis carries tier3's criteria", basis_t3["acceptance_criteria"], ["do the thing"])
    check("tier 'live' cannot see the criteria at delegation -> UNKNOWN, never False",
          basis_t3["criteria_changed_after_delegation"], None)

    # THE REGRESSION THIS FILE EXISTS TO HOLD. `updatedAt` is the RECORD's mtime:
    # a state move, a label, an assignee, or the pull-request attachment the poller
    # discovers the work by all bump it. Deriving the flag from it read True on
    # very nearly every reviewed pull request. Bumping it must now change nothing.
    issue_edited_after = dict(issue_with_ac, updatedAt="2026-09-05T11:00:00Z")  # AFTER startedAt
    basis_edited = resolve_basis("KIT-8", issue_edited_after, history_fetcher=_history_unavailable)
    check("a bumped updatedAt no longer manufactures a criteria-edit flag",
          basis_edited["criteria_changed_after_delegation"], None)

    basis_t1 = resolve_basis("KIT-8", issue_with_ac, history_fetcher=fake_history)
    check("basis prefers tier 1 when it answers", basis_t1["basis_tier"], "history")
    # fake_history says ["as it was at delegation"]; the live ticket says ["do the
    # thing"]. They differ, so the criteria really were edited — evidence, not mtime.
    check("tier 1 whose criteria differ from live -> changed",
          basis_t1["criteria_changed_after_delegation"], True)

    def _history_matching_live(ticket_id, delegated_at):
        return {"acceptance_criteria": ["do the thing"], "out_of_scope": ["not this"]}

    basis_same = resolve_basis("KIT-8", issue_with_ac, history_fetcher=_history_matching_live)
    check("tier 1 whose criteria match live -> NOT changed",
          basis_same["criteria_changed_after_delegation"], False)

    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "KIT-9.json"), "w", encoding="utf-8") as fh:
            json.dump({"acceptance_criteria": ["from the reconciler"]}, fh)
        basis_t2 = resolve_basis("KIT-9", {"description": ""}, snapshot_dir=tmp,
                                 history_fetcher=_history_unavailable)
        check("basis uses tier 2 when 1 is unavailable and 2 has a snapshot",
              basis_t2["basis_tier"], "reconciler")

    basis_empty = resolve_basis("KIT-10", {"description": "no headings here"},
                               history_fetcher=_history_unavailable)
    check("an honestly empty basis is still a well-formed dict, not a crash",
          basis_empty["acceptance_criteria"], [])
    check("an empty basis still names the tier it tried", basis_empty["basis_tier"], "live")

    # 6. The driver: --ticket-file works fully offline, a hard fetch failure is loud
    #    and writes nothing, and an empty result still exits 0 (an honest answer, not
    #    a failure of this script).
    with tempfile.TemporaryDirectory() as tmp:
        ticket_file = os.path.join(tmp, "ticket.json")
        with open(ticket_file, "w", encoding="utf-8") as fh:
            json.dump(issue_with_ac, fh)
        out_file = os.path.join(tmp, "basis.json")
        ns = argparse.Namespace(ticket_id="KIT-8", team_key=None, out=out_file,
                                api_key_env="LINEAR_API_KEY", ticket_file=ticket_file,
                                snapshot_dir=None)
        check("driver via --ticket-file exits OK", run(ns), EXIT_OK)
        with open(out_file, encoding="utf-8") as fh:
            written = json.load(fh)
        check("driver writes the resolved basis", written["acceptance_criteria"], ["do the thing"])

        # A malformed --ticket-file is a usage error, not a crash.
        bad_file = os.path.join(tmp, "bad.json")
        with open(bad_file, "w", encoding="utf-8") as fh:
            fh.write("not json")
        ns_bad = argparse.Namespace(ticket_id="KIT-8", team_key=None, out=out_file,
                                    api_key_env="LINEAR_API_KEY", ticket_file=bad_file,
                                    snapshot_dir=None)
        check("a malformed --ticket-file is a usage error", run(ns_bad), EXIT_USAGE)

        # No --ticket-file and no API key set: a usage error, never a silent empty basis.
        saved_key = os.environ.pop("LINEAR_API_KEY", None)
        try:
            ns_no_key = argparse.Namespace(ticket_id="KIT-8", team_key="KIT", out=out_file,
                                           api_key_env="LINEAR_API_KEY", ticket_file=None,
                                           snapshot_dir=None)
            check("no API key and no --ticket-file is a usage error", run(ns_no_key), EXIT_USAGE)
        finally:
            if saved_key is not None:
                os.environ["LINEAR_API_KEY"] = saved_key

    # A hard Linear failure, stubbed offline: loud, and the --out file is left absent.
    saved_fetch = fetch_issue
    try:
        def fail_fetch(ticket_id, team_key, api_key):
            raise LinearError("simulated outage")
        globals()["fetch_issue"] = fail_fetch
        with tempfile.TemporaryDirectory() as tmp:
            out_file = os.path.join(tmp, "basis.json")
            os.environ["LINEAR_API_KEY_TEST_92"] = "x"
            ns = argparse.Namespace(ticket_id="KIT-8", team_key="KIT", out=out_file,
                                    api_key_env="LINEAR_API_KEY_TEST_92", ticket_file=None,
                                    snapshot_dir=None)
            check("a hard Linear failure is a loud usage error", run(ns), EXIT_USAGE)
            check("a hard Linear failure writes nothing", os.path.exists(out_file), False)
            del os.environ["LINEAR_API_KEY_TEST_92"]
    finally:
        globals()["fetch_issue"] = saved_fetch

    # 7. The read-only guard: this file never constructs a Linear WRITE mutation. Each
    #    token below appears exactly once in this file — here, in this list. A count
    #    above one means a real write path slipped into the code above.
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    for banned in ("commentCreate", "issueUpdate", "issueCreate", "issueAddLabel",
                   "issueArchive", "issueDelete"):
        if src.count(banned) > 1:
            failures.append("source names a Linear write mutation: %r" % banned)

    if failures:
        print("FAIL pipeline_review_basis selftest:")
        for f in failures:
            print("  -", f)
        return 1
    print("ok — pipeline_review_basis: tier ordering (history > reconciler > live), "
          "tier 1 immune to post-delegation edits by construction, edit-flag computed "
          "from the live ticket regardless of tier, empty basis is honest not a crash, "
          "read-only against Linear")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Stage E review basis resolver (KIT-92).")
    p.add_argument("ticket_id", nargs="?", help="e.g. KIT-90 (omit with --selftest)")
    p.add_argument("--team-key", help="Linear team key, e.g. KIT (unused with --ticket-file)")
    p.add_argument("--out", help="where to write the basis JSON (default: stdout)")
    p.add_argument("--api-key-env", default="LINEAR_API_KEY",
                   help="NAME of the env var holding the key — never the key itself")
    p.add_argument("--ticket-file", help="an offline issue JSON (identifier, description, "
                                        "startedAt, createdAt, updatedAt) instead of Linear")
    p.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR,
                   help="tier-2 reconciler snapshot store (default %s)" % DEFAULT_SNAPSHOT_DIR)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.ticket_id:
        p.error("a ticket id is required (or use --selftest)")
    if args.snapshot_dir:
        args.snapshot_dir = os.path.realpath(os.path.expanduser(args.snapshot_dir))
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
