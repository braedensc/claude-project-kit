#!/usr/bin/env python3
"""The idea-gate executor — materialise a planning session's proposed epic tree.

The direct analog of the review publisher (docs/PIPELINE-CONTRACT.md §14,
scripts/pipeline_review_local.py), pointed at planning instead of review. A
planning session — an idea ticket delegated into a Planning team, run sandboxed
by the dispatcher — holds NO tracker tool at all (the fence is structural: no
Linear MCP is attached to that session; see
docs/adr/2026-09-06-stage-a-triggered-from-linear.md). Its whole deliverable is
one `pipeline-safe-outputs/1` request file carrying a tree-shaped `ticket-create`
(§8 "Filing a plan"). This module, run on the dispatcher host as an owner-scoped
role account that DOES hold the credential, then

    validates   the batch WHOLE against schemas/safe-outputs.schema.json     find_requests / validate_plan
    gates        every proposed child through check_ticket_dor.py --strict   run_dor
    materialises epic → children → blockedBy relations, forcing every field   materialise
    reports      a comment back on the idea ticket, for the owner, ALWAYS    render_*_comment

The session holds no tracker tool, so EVERY way it reaches the owner travels
through this executor. Besides a plan, a session may also emit QUESTION(s) as
ticket-comments in the same file (§8) — the executor posts them on the idea
ticket. If a run produces a plan, its questions ride along as notes; if it
produces only questions, the executor surfaces them as a `needs-input`
escalation; if it produces nothing at all, the executor still leaves a visible
`no-output` note, because absence must not be silent (§13). Every comment carries
a `<!-- pipeline-escalation: <label> -->` mark the human-action notifier
(docs/adr/2026-09-06-human-action-notifier-and-reply-relay.md) greps to page a
person — this executor is the producer of those marks for the planning lane.

THE THINGS THIS FILE IS SHAPED AROUND

  1. The session sets NO field that carries authority. The executor forces the
     epic's state (backlog only, linear.findingTicket.landing), its
     `provenance:agent` label, every child's `provenance:epic` label, and every
     child's PARENT — to the epic it created in THIS batch, never a
     session-supplied id. A session that could name an already-approved parent
     could have its children auto-approve; it cannot, because it names none.

  2. Nothing this executor writes is ever `ready`, and it never MOVES a ticket.
     Every issue is CREATED in the backlog state and left there; the executor
     issues no state-move mutation at all. The epic carries `provenance:agent`, which by
     §5 never auto-approves — so the epic cannot approve itself, and a person
     moving it out of intake is the one gate that releases the tree (§4).
     --selftest asserts no state-move / approve / merge path exists in this file.

  3. All-or-nothing, including the DoR gate (contract §8, §13). One malformed
     child, one bad dependency edge, or one child that fails
     check_ticket_dor.py --strict rejects the WHOLE tree — nothing is created.
     A partial epic reads as decomposed and is not. The failing children and
     their errors are reported back on the idea ticket for a re-plan.

  4. "Could not do it" is never "nothing to do" (contract §13). A tree read and
     REFUSED (schema-invalid, a DoR failure, a retarget attempt) exits `rejected`;
     a tracker/config failure THIS executor hit — including a question it could
     not deliver — exits `errored`; a run that produced a plan or delivered a
     question exits 0. A genuinely absent file is `skipped` (exit 0), but even
     then, when the ticket is known, the executor leaves a visible note rather
     than vanishing. Distinct facts, distinct exit codes, never collapsed.

  5. The gate is staged where the session cannot reach it. This executor runs on
     the trusted dispatcher host over its own checkout — never inside the
     planning session's sandbox, never from that session's worktree — so it
     validates against its own committed schema and DoR config, exactly as
     pipeline_review_local.py trusts its own working tree. The session's worktree
     is a different checkout it never reads.

WHAT IT IS AND IS NOT

  It is: batch validation, plan extraction, dependency-edge validation, the DoR
  gate over every child, and materialise-or-reject. It reuses linear.findingTicket
  for the landing state, the owner to notify and the notification mode, so the
  plan kind is off on the same discriminator the finding kind is.

  It is not: the dispatcher that delegates the idea ticket and starts the
  sandboxed planning session, the planning brief the session runs (KIT-98), or
  the approve tier that later releases DoR-passing children when the human moves
  the epic (scripts/check_auto_approve.py). It creates every ticket in the
  backlog and stops; releasing them is the human's move and the approve tier's.

Usage:
    pipeline_plan_executor.py --requests F [--config delivery.json]
                              [--repo-root DIR] [--dry-run]
    pipeline_plan_executor.py --selftest

Exit: 0 = materialised, a question delivered, or nothing to do; 3 = tree REJECTED
      (read and refused — reported back, nothing created); 2 = usage/config/tracker
      error, including a question that could not be delivered.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_schemas  # noqa: E402  document_problems: the one definition of "conforms"

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
# Exit codes. A refusal is NEVER 0 — that is the §13 point, encoded here. And a
# refusal (the tree's own fault) is NEVER the same code as an executor failure.
EXIT_OK = 0
EXIT_REJECTED = 3
EXIT_ERRORED = 2

# Caps. Mirrors the flat ticket-create kind, whose cap of 3 is likewise a code
# constant (in the safe-outputs job), not a delivery.json budget — keeping the
# flood guard where the flat kind's already is and off the config surface.
MAX_PLAN_CHILDREN = 20
MAX_TITLE = 200
MAX_BODY = 16000
MAX_LABEL_OPS = 10

SUPPORTED_VERSION = 1  # §1: an unrecognized version refuses, it does not guess
LINEAR_API = "https://api.linear.app/graphql"
SAFE_OUTPUTS_SCHEMA = "safe-outputs"

# A well-formed but impossible ticket id (real Linear numbers start at 1), used
# ONLY to gate children before the real epic exists. The DoR gate checks a
# child's internal consistency — parent link ↔ provenance ↔ project ↔ state — not
# whether the epic exists in Linear (it has no API), so gating against this
# sentinel is equivalent to gating against the real id, and creates no orphan
# epic on failure.  See docs/adr/2026-09-06-stage-a-triggered-from-linear.md,
# "What could not be settled" item 4.
PENDING_PROJECT = "pending-project-gated-before-create"

# A planning session's only channel is this file, so a QUESTION it needs answered
# rides as a ticket-comment the executor posts on the idea ticket (the session
# holds no tracker tool). A few, not a wall.
MAX_PLAN_COMMENTS = 3

# Escalation markers — invisible HTML comments the future human-action notifier
# greps for (docs/adr/2026-09-06-human-action-notifier-and-reply-relay.md: it
# reads "the marks the pipeline already writes"). Same
# `<!-- pipeline-escalation: <label> -->` shape a stopped session's comment uses,
# so one grep on the tracker catches every human-moment this executor produces.
ESC_AWAITING_APPROVAL = "epic-awaiting-approval"   # a plan is filed; approve it
ESC_NEEDS_INPUT = "planning-needs-input"           # the planner asked a question
ESC_REJECTED = "planning-rejected"                 # a plan was refused; re-plan
ESC_NO_OUTPUT = "planning-no-output"               # the run produced nothing


def _marker(label):
    return "<!-- pipeline-escalation: %s -->" % label


def _sanitize(text):
    """Neutralize any HTML-comment sequence in SESSION-supplied text before it is
    embedded in a comment the executor marks.

    The executor writes `<!-- pipeline-escalation: <label> -->` marks that a
    human-action notifier greps to page a person and apply `agent:blocked`. A
    session's only channel is this file, so without this a session could smuggle a
    FORGED mark inside a question body, a note, or a ticket title and make the
    notifier page the owner (or block a ticket) on text the executor never
    authored. Breaking the `<!--` / `-->` sequence renders the text visibly and
    defeats the grep — the same angle-bracket discipline the notifier ADR applies
    to relayed replies. Executor-authored marks are added AFTER sanitizing, so
    only they survive as real marks."""
    if not isinstance(text, str):
        return text
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


class ExecutorError(Exception):
    """This executor / its config / the tracker failed — verdict `errored`."""


# --------------------------------------------------------------------------- #
# Pure logic — no I/O
# --------------------------------------------------------------------------- #
def load_requests(path):
    """Read the agent-authored batch. Returns (doc, verdict, message).

    verdict is None when the doc loaded and should be validated; otherwise one of
    'skipped' (genuinely absent — nothing to do) or 'errored' (present but
    unreadable — a fact about THIS read, never about the session's output).
    """
    if not os.path.exists(path):
        return None, "skipped", (
            "no request file at %s — the planning session produced no tree. "
            "Nothing to materialise. (The dispatcher that expected a tree and got "
            "none must read that as its OWN signal; to this executor it is absence, "
            "not failure — §13.)" % path)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), None, None
    except (OSError, ValueError) as exc:
        return None, "errored", "request file %s could not be read: %s" % (path, exc)


def find_requests(doc):
    """Validate the batch WHOLE, then partition it into (plan, comments, errors).

    A planning session emits at most one plan tree and any number of QUESTIONS
    (ticket-comments) — nothing else. A non-empty errors list refuses the whole
    batch (verdict `rejected`); the document conforms or none of it is trusted
    (contract §8, §12).
    """
    if not isinstance(doc, dict):
        return None, [], ["the request file is not a JSON object"]
    problems = check_schemas.document_problems(doc, SAFE_OUTPUTS_SCHEMA)
    if problems:
        return None, [], ["malformed safe-outputs batch: " + p for p in problems[:8]]

    plans, comments, errors = [], [], []
    for i, r in enumerate(doc["requests"]):
        rtype = r.get("type")
        if rtype == "ticket-create" and "epic" in r:
            plans.append(r)
        elif rtype == "ticket-comment":
            comments.append(r)
        else:
            errors.append("requests[%d] type %r is not valid in a planning batch — a "
                          "planning session emits a plan tree (ticket-create with "
                          "`epic`/`children`) and/or questions (ticket-comment), "
                          "nothing else" % (i, rtype))
    if len(plans) > 1:
        errors.append("%d plan trees in one batch — a run proposes exactly one "
                      "decomposition (§8 caps)" % len(plans))
    return (plans[0] if plans else None), comments, errors


def comment_errors(comments, pinned):
    """A session's questions must name its OWN pinned ticket (the same central
    check the plan gets), and there is a small cap — a planner asks the few things
    that block it, not a wall (§8 caps)."""
    errors = []
    if len(comments) > MAX_PLAN_COMMENTS:
        errors.append("%d questions in one run exceeds the cap of %d — ask the few "
                      "that block you (§8 caps)" % (len(comments), MAX_PLAN_COMMENTS))
    for i, c in enumerate(comments):
        if c.get("ticket_id") != pinned:
            errors.append("question %d names ticket %r but this session is pinned to "
                          "%r — a retarget, not a typo" % (i, c.get("ticket_id"), pinned))
    return errors


def dependency_errors(children):
    """Every way `depends_on` is malformed: out of range, self, or a cycle."""
    errors = []
    n = len(children)
    edges = {}
    for i, child in enumerate(children):
        deps = child.get("depends_on") or []
        clean = []
        for d in deps:
            if not isinstance(d, int) or isinstance(d, bool):
                errors.append("child %d depends_on %r is not an integer ordinal" % (i, d))
                continue
            if d < 0 or d >= n:
                errors.append("child %d depends_on %d is out of range (0..%d)"
                              % (i, d, n - 1))
                continue
            if d == i:
                errors.append("child %d depends_on itself" % i)
                continue
            clean.append(d)
        edges[i] = clean
    if errors:
        return errors  # do not hunt cycles in an already-broken graph

    # Cycle detection (DFS three-colour). A dependency cycle is a tree that can
    # never be ordered, so it is a malformed plan, not a set of relations.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {i: WHITE for i in range(n)}

    def visit(node, trail):
        colour[node] = GREY
        for nxt in edges[node]:
            if colour[nxt] == GREY:
                cycle = trail[trail.index(nxt):] + [nxt]
                errors.append("dependency cycle among children: "
                              + " -> ".join(str(x) for x in cycle))
                return True
            if colour[nxt] == WHITE and visit(nxt, trail + [nxt]):
                return True
        colour[node] = BLACK
        return False

    for i in range(n):
        if colour[i] == WHITE and visit(i, [i]):
            break
    return errors


def validate_plan(plan, pinned_id, finding_configured):
    """Semantic checks §8 can express only in prose (the schema shape passed).

    Returns a list of errors; empty means the tree is well-formed and may be
    DoR-gated. Every rule here is a MUST that runs before any create.
    """
    errors = []
    if not finding_configured:
        errors.append("linear.findingTicket is not configured — the plan kind is "
                      "off on this project exactly as the finding kind is (§8)")
    source = plan.get("source_ticket_id")
    if source != pinned_id:
        # The central check: the agent NAMES a ticket, the executor COMPARES it
        # to the pin and never uses it. A mismatch is an attempted retarget.
        errors.append("source_ticket_id is %r but this session is pinned to %r — an "
                      "attempted retarget, not a typo" % (source, pinned_id))
    children = plan.get("children") or []
    if len(children) > MAX_PLAN_CHILDREN:
        errors.append("%d children exceeds the per-run cap of %d — a plan is a tree, "
                      "not a backlog (§8 caps)" % (len(children), MAX_PLAN_CHILDREN))
    errors.extend(dependency_errors(children))
    return errors


def build_child_tickets(plan, team_key, landing_state_id):
    """The ticket objects the DoR gate judges — one per proposed child.

    Each carries a PENDING epic reference (parent + provenance + a placeholder
    project) so the whole tree is gated before anything is created. The DoR gate
    checks a child's internal consistency, which does not depend on the epic's
    real id, so gating against the sentinel is sound and leaves no orphan.
    """
    pending_epic = "%s-0" % team_key
    tickets = []
    for child in plan["children"]:
        labels = list(child.get("labels") or []) + ["provenance:epic"]
        tickets.append({
            "id": None,
            "title": child["title"],
            "description": child["body"],
            "labels": labels,
            "projectId": PENDING_PROJECT,
            "parentId": pending_epic,
            "stateId": landing_state_id,
            "provenance": "epic/%s" % pending_epic,
        })
    return tickets


def render_success_comment(idea_id, epic, children):
    """One markdown comment posted back on the idea ticket, for the owner."""
    lines = [
        _marker(ESC_AWAITING_APPROVAL),
        "### Epic plan ready — awaiting your approval",
        "",
        "A planning session decomposed **%s** into an epic and %d "
        "child ticket(s), all in the backlog. **Nothing is dispatchable until you "
        "move the epic out of intake** — that is the one human gate (§4)."
        % (idea_id, len(children)),
        "",
        "- **Epic** [%s](%s) — `%s` (`provenance:agent`)"
        % (epic["identifier"], epic.get("url") or "", _sanitize(epic["title"])),
    ]
    for created, child in children:
        dep = ""
        if child.get("depends_on"):
            dep = " — depends on %s" % ", ".join(
                "#%d" % d for d in child["depends_on"])
        lines.append("  - [%s](%s) — `%s`%s"
                     % (created["identifier"], created.get("url") or "",
                        _sanitize(child["title"]), dep))
    lines += [
        "",
        "Approve the epic to release the tree; each child re-checks the "
        "Definition-of-Ready gate as it is released (§5, §11).",
    ]
    return "\n".join(lines)


def render_rejection_comment(idea_id, reason, detail_lines):
    """One markdown comment reporting a refused tree back for a re-plan."""
    lines = [
        _marker(ESC_REJECTED),
        "### Proposed epic plan REJECTED — nothing was created",
        "",
        "The plan a session proposed for **%s** was refused whole, so no epic and "
        "no children were filed (all-or-nothing, §8). Re-plan and resubmit." % idea_id,
        "",
        "**%s**" % reason,
        "",
    ]
    # detail_lines carry DoR messages and validation errors that quote SESSION
    # values (a child title, a bad id) — sanitize before the marked comment.
    lines.extend(_sanitize(line) for line in detail_lines)
    return "\n".join(lines)


def render_question_comment(idea_id, body):
    """A question the session filed no plan for — surfaced on the idea ticket and
    marked so the notifier can page the owner. It is the owner's to answer before
    the idea can be decomposed."""
    return ("%s\n> A planning session raised this while working **%s**, and filed no "
            "plan — it needs your input before it can decompose the idea.\n\n%s"
            % (_marker(ESC_NEEDS_INPUT), idea_id, _sanitize(body)))


def render_note_comment(idea_id, body):
    """A note the session left ALONGSIDE a plan it did file — surfaced as context,
    not an escalation (the plan itself is the awaiting-approval event)."""
    return ("<!-- planning-note -->\n> A note from the planning session that proposed "
            "the plan for **%s**:\n\n%s" % (idea_id, _sanitize(body)))


def render_no_output_comment(idea_id):
    """The run produced nothing. Absence must not be SILENT on the board (§13):
    the owner is told the miss, marked so the notifier can surface it."""
    return ("%s\n### No plan was produced\n\nThe planning session for **%s** finished "
            "without proposing a plan and left no message. It may have failed, hit its "
            "budget, or judged the idea impossible to decompose as written. Nothing was "
            "filed — re-run, or add detail to the idea and hand it off again."
            % (_marker(ESC_NO_OUTPUT), idea_id))


# --------------------------------------------------------------------------- #
# The Definition-of-Ready gate — a subprocess, not a judgment (§5)
# --------------------------------------------------------------------------- #
def run_dor(child_tickets, config_path, repo_root):
    """Run check_ticket_dor.py --strict over every child. Returns (ok, reports).

    The gate is the SAME script every other consumer uses, staged from this
    executor's own (trusted, dispatcher-host) checkout. A child that fails is not
    created; the caller rejects the whole tree.
    """
    gate = os.path.join(HERE, "check_ticket_dor.py")
    argv = [sys.executable, gate, "--strict", "--json", "--repo-root", repo_root]
    if config_path:
        argv += ["--config", config_path]
    payload = json.dumps({"tickets": child_tickets})
    try:
        proc = subprocess.run(argv, input=payload, capture_output=True, text=True)
    except OSError as exc:
        raise ExecutorError("could not run the DoR gate (%s): %s" % (gate, exc))
    # Exit 2 is the gate's own usage/IO/config error — an executor problem, not a
    # verdict on the children. Distinguish it from exit 1 (children not ready).
    if proc.returncode == 2:
        raise ExecutorError("the DoR gate could not evaluate the children: %s"
                            % (proc.stderr.strip() or proc.stdout.strip()))
    try:
        result = json.loads(proc.stdout)
    except ValueError as exc:
        raise ExecutorError("the DoR gate produced no JSON verdict (%s): %s"
                            % (exc, proc.stderr.strip()))
    return bool(result.get("ok")), result.get("tickets", [])


def dor_failure_lines(reports, children):
    """Human-readable lines naming every child the gate rejected."""
    lines = []
    for i, report in enumerate(reports):
        if report.get("errors"):
            title = children[i]["title"] if i < len(children) else report.get("ref", "?")
            lines.append("- child %d — `%s`:" % (i, title))
            for err in report["errors"]:
                lines.append("  - [%s] %s" % (err.get("rule", "?"), err.get("message", "")))
    return lines


# --------------------------------------------------------------------------- #
# I/O — the tracker (Linear). The ONLY party in the pipeline that holds the key.
# --------------------------------------------------------------------------- #
class LinearClient:
    """A thin Linear GraphQL client. Injected in --selftest so nothing hits the
    network. Every mutation here CREATES or COMMENTS — none MOVES a ticket."""

    def __init__(self, api_key):
        self._key = api_key

    def _gql(self, query, variables):
        body = json.dumps({"query": query, "variables": variables}).encode()
        req = urllib.request.Request(LINEAR_API, data=body, headers={
            "Content-Type": "application/json",
            # Linear personal API keys go in Authorization RAW (no "Bearer ").
            "Authorization": self._key,
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise ExecutorError("Linear API HTTP %s: %s" % (exc.code, exc.read()[:500]))
        except (urllib.error.URLError, ValueError, OSError) as exc:
            raise ExecutorError("Linear API call failed: %s" % exc)
        if payload.get("errors"):
            raise ExecutorError("Linear API error: %s" % json.dumps(payload["errors"])[:500])
        return payload["data"]

    def resolve_source(self, team_key, number):
        data = self._gql(
            "query($teamKey: String!, $number: Float!) {"
            "  issues(filter: { team: { key: { eq: $teamKey } }, "
            "number: { eq: $number } }, first: 1) {"
            "    nodes { id identifier team { id } } } }",
            {"teamKey": team_key, "number": float(number)})
        nodes = data["issues"]["nodes"]
        if not nodes:
            raise ExecutorError("source ticket %s-%s not found" % (team_key, number))
        return {"id": nodes[0]["id"], "team_id": (nodes[0].get("team") or {}).get("id")}

    def create_project(self, team_id, name, description):
        data = self._gql(
            "mutation($teamId: String!, $name: String!, $description: String!) {"
            "  projectCreate(input: { teamIds: [$teamId], name: $name, "
            "description: $description }) { success project { id } } }",
            {"teamId": team_id, "name": name[:200], "description": description[:240]})
        return data["projectCreate"]["project"]["id"]

    def create_issue(self, team_id, title, description, state_id, label_ids,
                     parent_id=None, project_id=None, subscriber_ids=None,
                     assignee_id=None):
        data = self._gql(
            "mutation($teamId: String!, $title: String!, $description: String!, "
            "$stateId: String!, $labelIds: [String!], $parentId: String, "
            "$projectId: String, $subscriberIds: [String!], $assigneeId: String) {"
            "  issueCreate(input: { teamId: $teamId, title: $title, "
            "description: $description, stateId: $stateId, labelIds: $labelIds, "
            "parentId: $parentId, projectId: $projectId, "
            "subscriberIds: $subscriberIds, assigneeId: $assigneeId }) {"
            "    success issue { id identifier url } } }",
            {"teamId": team_id, "title": title, "description": description,
             "stateId": state_id, "labelIds": label_ids, "parentId": parent_id,
             "projectId": project_id, "subscriberIds": subscriber_ids or [],
             "assigneeId": assignee_id})
        return data["issueCreate"]["issue"]

    def create_relation(self, blocker_id, blocked_id):
        # type "blocks": issueId BLOCKS relatedIssueId. child i depends_on j means
        # j blocks i, so blocker = children[j], blocked = children[i].
        self._gql(
            "mutation($issueId: String!, $relatedIssueId: String!) {"
            "  issueRelationCreate(input: { issueId: $issueId, "
            "relatedIssueId: $relatedIssueId, type: blocks }) { success } }",
            {"issueId": blocker_id, "relatedIssueId": blocked_id})

    def post_comment(self, issue_id, body):
        self._gql(
            "mutation($issueId: String!, $body: String!) {"
            "  commentCreate(input: { issueId: $issueId, body: $body }) "
            "{ success } }",
            {"issueId": issue_id, "body": body})


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def _forced_ids(cfg, finding_cfg):
    """Resolve every executor-forced id up front. Missing ones are `errored` —
    the tree validated; the CONFIG is what cannot supply an authority field."""
    state_ids = cfg["linear"]["stateIds"]
    label_ids = cfg["linear"]["labels"]["ids"]
    landing = finding_cfg.get("landing")
    landing_state = state_ids.get(landing)
    if not landing_state:
        raise ExecutorError("linear.findingTicket.landing %r does not resolve in "
                            "linear.stateIds" % landing)
    prov_agent = label_ids.get("provenance:agent")
    prov_epic = label_ids.get("provenance:epic")
    if not prov_agent:
        raise ExecutorError("linear.labels.ids has no `provenance:agent` — the "
                            "executor cannot mark the epic as agent-drafted (§5, §6)")
    if not prov_epic:
        raise ExecutorError("linear.labels.ids has no `provenance:epic` — the "
                            "executor cannot mark the children (§5, §6)")
    owner = finding_cfg.get("ownerUserId")
    if not owner:
        raise ExecutorError("linear.findingTicket.ownerUserId is unset — a plan "
                            "nobody is notified about dies in the backlog")
    notify = finding_cfg.get("notify") or "subscribe"
    return {
        "landing_state": landing_state,
        "prov_agent": prov_agent,
        "prov_epic": prov_epic,
        "label_ids": label_ids,
        "owner": owner,
        "subscribe": notify in ("subscribe", "both"),
    }


def materialise(args, client=None):
    """Validate → DoR-gate → create, or reject. Returns an exit code."""
    # ── Config (§2: absent delivery.json is OFF, not broken) ────────────────
    config_path = args.config or "delivery.json"
    if not os.path.exists(config_path):
        print("::notice:: no %s — the pipeline is not configured, so there is no "
              "tree to materialise (§2)." % config_path)
        return EXIT_OK
    try:
        cfg = json.load(open(config_path, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("::error:: %s is unreadable: %s" % (config_path, exc), file=sys.stderr)
        return EXIT_ERRORED
    if cfg.get("version") != SUPPORTED_VERSION:
        print("::error:: %s version %r unrecognized — refusing to guess."
              % (config_path, cfg.get("version")), file=sys.stderr)
        return EXIT_ERRORED

    team_key = cfg["linear"]["teamKey"]
    finding_cfg = cfg["linear"].get("findingTicket")

    # ── Load the batch. Absence is not silence (§13). ──────────────────────
    doc, verdict, message = load_requests(args.requests)
    if verdict == "errored":
        print("::error:: %s" % message, file=sys.stderr)
        return EXIT_ERRORED
    if verdict == "skipped":
        return _no_output(args, client, cfg, team_key, finding_cfg, message)

    # ── Validate the batch WHOLE, partition into plan + questions ──────────
    plan, comments, errors = find_requests(doc)
    pinned = args.pinned or (plan.get("source_ticket_id") if plan else None)
    if not errors and plan:
        errors = validate_plan(plan, pinned, bool(finding_cfg))
    if not errors:
        errors = comment_errors(comments, pinned)
    if not errors and not plan and not comments:
        errors = ["the batch carried neither a plan nor a question"]
    if not errors and (plan or comments) and pinned is None:
        errors = ["no dispatcher-pinned ticket id — cannot verify the target; the "
                  "dispatcher supplies it via --pinned"]
    if errors:
        # All-or-nothing: a refused batch posts only its rejection, never the
        # session's questions (§8). The report goes to the pinned/own ticket.
        return _reject(args, client, cfg, team_key, finding_cfg,
                       pinned or (plan or {}).get("source_ticket_id"),
                       "the batch was refused before any create",
                       ["- %s" % e for e in errors])

    # ── No plan, only question(s): the planner needs the owner's input ─────
    if not plan:
        return _escalate(args, client, cfg, team_key, finding_cfg, pinned, comments)

    # ── The DoR gate over every child (§5) ─────────────────────────────────
    children = plan["children"]
    child_tickets = build_child_tickets(plan, team_key, _landing_state_or_placeholder(cfg, finding_cfg))
    try:
        ok, reports = run_dor(child_tickets, config_path, args.repo_root)
    except ExecutorError as exc:
        print("::error:: %s" % exc, file=sys.stderr)
        return EXIT_ERRORED
    if not ok:
        return _reject(args, client, cfg, team_key, finding_cfg, pinned,
                       "%d child(ren) failed the Definition-of-Ready gate" %
                       sum(1 for r in reports if r.get("errors")),
                       dor_failure_lines(reports, children))

    # ── Everything passed. Create. (dry-run stops here.) ───────────────────
    if args.dry_run:
        print("::notice:: [dry-run] tree for %s valid and DoR-clean: 1 epic, %d "
              "children, %d note(s) — nothing created."
              % (pinned, len(children), len(comments)))
        return EXIT_OK

    try:
        forced = _forced_ids(cfg, finding_cfg)
    except ExecutorError as exc:
        print("::error:: %s" % exc, file=sys.stderr)
        return EXIT_ERRORED

    client = client or _live_client()
    if client is None:
        print("::error:: LINEAR_API_KEY is empty — this executor holds the only "
              "tracker credential, so the validated tree cannot be filed. Nothing "
              "was created, and this is not a verdict on the tree.", file=sys.stderr)
        return EXIT_ERRORED

    return _create(client, cfg, team_key, finding_cfg, forced, pinned, plan, comments)


def _landing_state_or_placeholder(cfg, finding_cfg):
    """The raw/landing state id for gating. Falls back to a placeholder only when
    the config cannot supply it — the real create path re-resolves and errors."""
    if finding_cfg:
        state = cfg["linear"]["stateIds"].get(finding_cfg.get("landing"))
        if state:
            return state
    return cfg["linear"]["stateIds"].get("raw") or "state-raw-unresolved"


def _no_output(args, client, cfg, team_key, finding_cfg, message):
    """A planning session that produced nothing must not be SILENT on the board
    (§13). When the ticket is known (a real dispatch: --pinned), leave a visible
    'no plan produced' comment so the owner sees the miss. Without a pinned ticket
    (a local or dry run) it stays a quiet notice — there is nobody's ticket to
    notify. `skipped` (exit 0) either way: this is an answer, not a failure."""
    print("::notice:: %s" % message)
    target = args.pinned
    if args.dry_run or not target or not finding_cfg:
        return EXIT_OK
    client = client or _live_client()
    if client is None:
        print("::warning:: no credential — could not surface the empty run on %s"
              % target, file=sys.stderr)
        return EXIT_OK
    try:
        src = client.resolve_source(team_key, target.rsplit("-", 1)[1])
        client.post_comment(src["id"], render_no_output_comment(target))
        print("::notice:: verdict=skipped (surfaced): the empty run is now visible on %s"
              % target)
    except (ExecutorError, IndexError) as exc:
        print("::warning:: could not surface the empty run on %s: %s" % (target, exc),
              file=sys.stderr)
    return EXIT_OK


def _escalate(args, client, cfg, team_key, finding_cfg, pinned, comments):
    """The session asked question(s) and filed no plan. Surface them on the idea
    ticket, marked so the notifier can page the owner — a legitimate terminal
    ('the planner needs you'), never a failure and never silent. A delivery that
    FAILS, though, is a genuine could-not-do-it and is loud (`errored`)."""
    print("::notice:: verdict=escalated — the planning session raised %d question(s) "
          "on %s and filed no plan." % (len(comments), pinned))
    if args.dry_run:
        return EXIT_OK
    client = client or _live_client()
    if client is None:
        print("::error:: no credential — the planner's question(s) on %s could not be "
              "delivered. A question nobody sees is worse than none." % pinned,
              file=sys.stderr)
        return EXIT_ERRORED
    try:
        src = client.resolve_source(team_key, pinned.rsplit("-", 1)[1])
        for c in comments:
            client.post_comment(src["id"], render_question_comment(pinned, c["body"]))
    except (ExecutorError, IndexError) as exc:
        print("::error:: could not deliver the planner's question(s) on %s: %s"
              % (pinned, exc), file=sys.stderr)
        return EXIT_ERRORED
    print("::notice:: delivered %d question(s) on %s — awaiting the owner." %
          (len(comments), pinned))
    return EXIT_OK


def _reject(args, client, cfg, team_key, finding_cfg, source_id, reason, detail_lines):
    """Post the rejection back on the idea ticket (best-effort) and exit rejected.
    A tree that was READ and REFUSED is the session's own output, never this
    executor's failure (§13) — so the exit code is `rejected`, even if the
    report-back comment cannot be delivered. The report ALWAYS goes to the pinned
    (own) ticket, never to a ticket a retargeting session named."""
    for line in [reason] + detail_lines:
        print("::error:: plan rejected: %s" % line, file=sys.stderr)
    target = args.pinned or source_id
    if not args.dry_run and target and finding_cfg:
        client = client or _live_client()
        if client is not None:
            body = render_rejection_comment(target, reason, detail_lines)
            try:
                src = client.resolve_source(team_key, target.rsplit("-", 1)[1])
                client.post_comment(src["id"], body)
                print("::notice:: reported the rejection back on %s" % target)
            except (ExecutorError, IndexError) as exc:
                print("::warning:: could not post the rejection comment on %s: %s"
                      % (target, exc), file=sys.stderr)
    return EXIT_REJECTED


def _create(client, cfg, team_key, finding_cfg, forced, pinned, plan, comments=None):
    """The only path that mutates. Any failure here is `errored`, never
    `rejected` — the tree was accepted; the tracker is what failed, and it may be
    partly written (§8, §13)."""
    comments = comments or []
    created_count = 0

    def errored(msg):
        print("::error:: %s" % msg, file=sys.stderr)
        print("::error:: created %d ticket(s) before this failure. All-or-nothing "
              "covers VALIDATION, not the network — the tracker may be partly "
              "written." % created_count, file=sys.stderr)
        return EXIT_ERRORED

    try:
        src = client.resolve_source(team_key, pinned.rsplit("-", 1)[1])
        team_id = src["team_id"]
        subscribers = [forced["owner"]] if forced["subscribe"] else []

        # The project holds the PRD and the tree (mirrors /plan-epic step 1).
        project_id = client.create_project(
            team_id, plan["epic"]["title"],
            "Idea-gate epic proposed from %s. Awaiting approval." % pinned)

        # The epic — backlog, provenance:agent, owner subscribed. It approves
        # nothing: provenance:agent never auto-approves (§5).
        epic_desc = ("> Epic drafted by a planning session from **%s**. "
                     "`provenance:agent` — awaiting a person's approval to release "
                     "its children (§4, §5).\n\n%s" % (pinned, plan["epic"]["body"]))
        epic = client.create_issue(
            team_id, plan["epic"]["title"], epic_desc, forced["landing_state"],
            [forced["prov_agent"]], parent_id=None, project_id=project_id,
            subscriber_ids=subscribers)
        created_count += 1

        # The children — backlog, provenance:epic, parent FORCED to the epic
        # created just now. The session supplied no parent id.
        created_children = []
        for child in plan["children"]:
            label_ids = [forced["prov_epic"]] + [
                forced["label_ids"][k] for k in (child.get("labels") or [])]
            issue = client.create_issue(
                team_id, child["title"], child["body"], forced["landing_state"],
                label_ids, parent_id=epic["id"], project_id=project_id)
            created_count += 1
            created_children.append((issue, child))

        # Dependency edges → blockedBy relations.
        for i, (_, child) in enumerate(created_children):
            for j in (child.get("depends_on") or []):
                client.create_relation(created_children[j][0]["id"],
                                       created_children[i][0]["id"])

        # Report the plan back on the idea ticket for the owner, then any notes the
        # session left alongside it (its open questions ride in the epic PRD; these
        # are top-level asides). The plan is filed either way — a note never blocks.
        client.post_comment(src["id"], render_success_comment(pinned, epic, created_children))
        for c in comments:
            client.post_comment(src["id"], render_note_comment(pinned, c["body"]))
    except ExecutorError as exc:
        return errored(str(exc))
    except (KeyError, IndexError) as exc:
        return errored("unexpected tracker response shape: %s" % exc)

    print("::notice:: filed epic %s and %d child(ren) for %s (all backlog, "
          "provenance:agent/epic)%s — awaiting the owner's approval."
          % (epic["identifier"], len(created_children), pinned,
             " + %d note(s)" % len(comments) if comments else ""))
    return EXIT_OK


def _live_client():
    key = os.environ.get("LINEAR_API_KEY")
    return LinearClient(key) if key else None


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
class FakeLinear:
    """Records every mutation; performs no I/O. Injected in --selftest."""

    def __init__(self, source_team_id="team-uuid"):
        self.source_team_id = source_team_id
        self.projects = []
        self.issues = []          # dicts as passed to create_issue
        self.relations = []       # (blocker_id, blocked_id)
        self.comments = []        # (issue_id, body)
        self._n = 0

    def resolve_source(self, team_key, number):
        return {"id": "src-%s-%s" % (team_key, number), "team_id": self.source_team_id}

    def create_project(self, team_id, name, description):
        pid = "proj-%d" % len(self.projects)
        self.projects.append({"id": pid, "name": name})
        return pid

    def create_issue(self, team_id, title, description, state_id, label_ids,
                     parent_id=None, project_id=None, subscriber_ids=None,
                     assignee_id=None):
        self._n += 1
        issue = {"id": "iss-%d" % self._n, "identifier": "KIT-%d" % (100 + self._n),
                 "url": "https://linear.app/x/issue/KIT-%d" % (100 + self._n),
                 "title": title, "description": description, "state_id": state_id,
                 "label_ids": list(label_ids), "parent_id": parent_id,
                 "project_id": project_id, "subscriber_ids": list(subscriber_ids or []),
                 "assignee_id": assignee_id}
        self.issues.append(issue)
        return issue

    def create_relation(self, blocker_id, blocked_id):
        self.relations.append((blocker_id, blocked_id))

    def post_comment(self, issue_id, body):
        if getattr(self, "fail_post", False):
            raise ExecutorError("simulated tracker failure on comment")
        self.comments.append((issue_id, body))


_GOOD_CHILD_BODY = """## Context

The gate needs a real body with all five sections to pass --strict.

## Acceptance criteria

- [ ] `check_ticket_dor.py` accepts this child
- [ ] the tree materialises

## Out of scope

- The live dispatcher wiring

## Test plan

- `npm run test:plan-executor`

## Pointers

- `x.py` — the file this child touches
"""

_GOOD_CONFIG = {
    "version": 1,
    "linear": {
        "teamKey": "KIT",
        "stateIds": {"raw": "state-raw", "ready": "state-ready", "working": "w",
                     "review": "r", "needsApproval": "n", "done": "d"},
        "labels": {"ids": {
            "track:meta": "lbl-track", "effort:S": "lbl-s", "effort:M": "lbl-m",
            "effort:L": "lbl-l", "provenance:epic": "lbl-prov-epic",
            "provenance:agent": "lbl-prov-agent", "agent:queued": "lbl-q",
        }},
        "findingTicket": {"landing": "raw", "notify": "subscribe",
                          "ownerUserId": "owner-1"},
    },
}


def _tree(children=None):
    return {
        "schema": "pipeline-safe-outputs/1",
        "requests": [{
            "type": "ticket-create", "source_ticket_id": "KIT-777",
            "epic": {"title": "Safe follow-up ticket filing",
                     "body": "## Context\nThe PRD."},
            "children": children if children is not None else [
                {"title": "Add the ticket-create tree kind", "body": _GOOD_CHILD_BODY,
                 "labels": ["track:meta", "effort:M"]},
                {"title": "Executor forces every authority field", "body": _GOOD_CHILD_BODY,
                 "labels": ["track:meta", "effort:S"], "depends_on": [0]},
            ],
        }],
    }


def selftest():
    import tempfile
    failures = []
    cases = [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    # 1. The source names no state-move / approve / merge path. The executor
    #    CREATES in the backlog and never advances a ticket.
    src = open(os.path.abspath(__file__)).read()
    marker = "_BANNED"  # lines carrying this comment are the assertion, not code
    body_lines = [ln for ln in src.splitlines() if marker not in ln]
    scanned = "\n".join(body_lines)
    for banned in ("issueUpdate", "gh pr merge", "issueAddLabel",  # _BANNED
                   "createReview", "pulls/{number}/merge", "--approve"):  # _BANNED
        check("no-move-approve-merge:%s" % banned, banned in scanned, False)
    # `ready`/`working`/`review`/`done`/`needsApproval` must never be a create
    # target: the only state this executor ever writes is the landing (raw) one.
    check("landing-is-raw-only", "forced[\"landing_state\"]" in src or
          "landing_state" in src, True)

    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "x.py"), "w").close()
        cfg_path = os.path.join(tmp, "delivery.json")
        json.dump(_GOOD_CONFIG, open(cfg_path, "w"))

        def run(doc, dry_run=False, client=None, config=cfg_path):
            req = os.path.join(tmp, "requests.json")
            json.dump(doc, open(req, "w"))
            args = argparse.Namespace(requests=req, config=config, repo_root=tmp,
                                      dry_run=dry_run, pinned="KIT-777")
            return materialise(args, client=client)

        # 2. A valid, DoR-clean tree materialises: exactly the forced fields.
        fake = FakeLinear()
        check("valid-tree-exit", run(_tree(), client=fake), EXIT_OK)
        check("valid-tree-one-project", len(fake.projects), 1)
        check("valid-tree-issue-count", len(fake.issues), 3)  # 1 epic + 2 children
        epic = fake.issues[0]
        kids = fake.issues[1:]
        check("epic-provenance-agent", epic["label_ids"], ["lbl-prov-agent"])
        check("epic-landing-raw", epic["state_id"], "state-raw")
        check("epic-no-parent", epic["parent_id"], None)
        check("epic-owner-subscribed", epic["subscriber_ids"], ["owner-1"])
        check("epic-never-assigned-to-session", epic["assignee_id"], None)
        check("child-parent-forced-to-epic", all(k["parent_id"] == epic["id"] for k in kids), True)
        check("child-provenance-epic-forced",
              all("lbl-prov-epic" in k["label_ids"] for k in kids), True)
        check("child-landing-raw", all(k["state_id"] == "state-raw" for k in kids), True)
        check("child-carries-proposed-labels", "lbl-m" in kids[0]["label_ids"], True)
        check("child-not-assigned", all(k["assignee_id"] is None for k in kids), True)
        # depends_on [0] on child index 1 → blockedBy: children[0] blocks children[1]
        check("one-relation", len(fake.relations), 1)
        check("relation-direction", fake.relations[0], (kids[0]["id"], kids[1]["id"]))
        check("summary-comment-on-idea", len(fake.comments), 1)
        check("summary-comment-target", fake.comments[0][0], "src-KIT-777")
        check("summary-names-approval", "move the epic out of intake" in fake.comments[0][1], True)
        check("summary-carries-approval-marker",
              _marker(ESC_AWAITING_APPROVAL) in fake.comments[0][1], True)

        # 3. dry-run creates nothing.
        fake2 = FakeLinear()
        check("dry-run-exit", run(_tree(), dry_run=True, client=fake2), EXIT_OK)
        check("dry-run-no-issues", len(fake2.issues), 0)
        check("dry-run-no-comments", len(fake2.comments), 0)

        # 4. source_ticket_id ≠ pin → rejected, nothing created.
        fake3 = FakeLinear()
        retarget = _tree()
        retarget["requests"][0]["source_ticket_id"] = "KIT-999"
        check("retarget-rejected", run(retarget, client=fake3), EXIT_REJECTED)
        check("retarget-no-create", len(fake3.issues), 0)

        # 5. A malformed child (schema: protected label) rejects the WHOLE tree.
        fake4 = FakeLinear()
        bad = _tree()
        bad["requests"][0]["children"][0]["labels"] = ["provenance:human"]
        check("schema-bad-child-rejected", run(bad, client=fake4), EXIT_REJECTED)
        check("schema-bad-child-no-create", len(fake4.issues), 0)

        # 6. A DoR-failing child (empty acceptance criteria) rejects the whole
        #    tree, creates nothing, and names the failing child on the idea ticket.
        fake5 = FakeLinear()
        dor_bad = _tree(children=[
            {"title": "Missing acceptance criteria", "body": _GOOD_CHILD_BODY,
             "labels": ["track:meta", "effort:M"]},
            {"title": "Empty criteria child",
             "body": _GOOD_CHILD_BODY.replace(
                 "- [ ] `check_ticket_dor.py` accepts this child\n"
                 "- [ ] the tree materialises\n", "\n"),
             "labels": ["track:meta", "effort:S"]},
        ])
        check("dor-fail-rejected", run(dor_bad, client=fake5), EXIT_REJECTED)
        check("dor-fail-no-create", len(fake5.issues), 0)
        check("dor-fail-reported-back", len(fake5.comments), 1)
        check("dor-fail-comment-names-child",
              "Empty criteria child" in fake5.comments[0][1], True)

        # 7. Over the child cap → rejected. (Build a tree past MAX_PLAN_CHILDREN.)
        fake6 = FakeLinear()
        many = _tree(children=[
            {"title": "c%d" % i, "body": _GOOD_CHILD_BODY, "labels": ["track:meta", "effort:S"]}
            for i in range(MAX_PLAN_CHILDREN + 1)])
        # schema hard-ceiling is 20; a tree past it is refused at the schema layer.
        check("over-cap-rejected", run(many, client=fake6), EXIT_REJECTED)
        check("over-cap-no-create", len(fake6.issues), 0)

        # 8. A dependency cycle → rejected before any create.
        fake7 = FakeLinear()
        cyc = _tree(children=[
            {"title": "A", "body": _GOOD_CHILD_BODY, "labels": ["track:meta", "effort:S"],
             "depends_on": [1]},
            {"title": "B", "body": _GOOD_CHILD_BODY, "labels": ["track:meta", "effort:S"],
             "depends_on": [0]},
        ])
        check("cycle-rejected", run(cyc, client=fake7), EXIT_REJECTED)
        check("cycle-no-create", len(fake7.issues), 0)

        # 9. depends_on out of range → rejected.
        fake8 = FakeLinear()
        oob = _tree(children=[
            {"title": "A", "body": _GOOD_CHILD_BODY, "labels": ["track:meta", "effort:S"],
             "depends_on": [5]},
        ])
        check("oob-dep-rejected", run(oob, client=fake8), EXIT_REJECTED)

        # 10. Not a plan (a flat finding batch) → rejected (no tree present).
        fake9 = FakeLinear()
        flat = {"schema": "pipeline-safe-outputs/1", "requests": [
            {"type": "ticket-create", "source_ticket_id": "KIT-777",
             "title": "a finding", "body": "found it"}]}
        check("no-tree-rejected", run(flat, client=fake9), EXIT_REJECTED)
        check("no-tree-no-create", len(fake9.issues), 0)

        # 11. Absent request file → skipped (exit 0), never a create — but NOT
        #     silent: with the ticket known, a visible no-output note is posted (§13).
        fakeA = FakeLinear()
        args = argparse.Namespace(requests=os.path.join(tmp, "nope.json"),
                                  config=cfg_path, repo_root=tmp, dry_run=False,
                                  pinned="KIT-777")
        check("absent-skipped", materialise(args, client=fakeA), EXIT_OK)
        check("absent-no-create", len(fakeA.issues), 0)
        check("absent-surfaced", len(fakeA.comments), 1)
        check("absent-carries-no-output-marker",
              _marker(ESC_NO_OUTPUT) in fakeA.comments[0][1], True)
        # …but a dry run stays quiet (nobody's ticket to notify on a measurement).
        fakeA2 = FakeLinear()
        args2 = argparse.Namespace(requests=os.path.join(tmp, "nope.json"),
                                   config=cfg_path, repo_root=tmp, dry_run=True,
                                   pinned="KIT-777")
        check("absent-dry-run-quiet", materialise(args2, client=fakeA2), EXIT_OK)
        check("absent-dry-run-no-comment", len(fakeA2.comments), 0)

        # 12. findingTicket unconfigured → the plan kind is off → rejected.
        fakeB = FakeLinear()
        cfg_off = json.loads(json.dumps(_GOOD_CONFIG))
        del cfg_off["linear"]["findingTicket"]
        off_path = os.path.join(tmp, "delivery-off.json")
        json.dump(cfg_off, open(off_path, "w"))
        check("finding-off-rejected", run(_tree(), client=fakeB, config=off_path),
              EXIT_REJECTED)
        check("finding-off-no-create", len(fakeB.issues), 0)

        # 13. Config missing provenance:agent id → errored (config, not the tree).
        fakeC = FakeLinear()
        cfg_noprov = json.loads(json.dumps(_GOOD_CONFIG))
        del cfg_noprov["linear"]["labels"]["ids"]["provenance:agent"]
        np_path = os.path.join(tmp, "delivery-noprov.json")
        json.dump(cfg_noprov, open(np_path, "w"))
        check("config-gap-errored", run(_tree(), client=fakeC, config=np_path),
              EXIT_ERRORED)
        check("config-gap-no-create", len(fakeC.issues), 0)

        # 14. Absent delivery.json → OFF, exit ok, nothing created (§2).
        fakeD = FakeLinear()
        args = argparse.Namespace(requests=os.path.join(tmp, "requests.json"),
                                  config=os.path.join(tmp, "no-delivery.json"),
                                  repo_root=tmp, dry_run=False, pinned="KIT-777")
        json.dump(_tree(), open(os.path.join(tmp, "requests.json"), "w"))
        check("no-config-off", materialise(args, client=fakeD), EXIT_OK)
        check("no-config-no-create", len(fakeD.issues), 0)

        # ── The escalation / question channel ──────────────────────────────
        def _comment(body, tid="KIT-777"):
            return {"type": "ticket-comment", "ticket_id": tid, "body": body}

        def _batch(*reqs):
            return {"schema": "pipeline-safe-outputs/1", "requests": list(reqs)}

        # 15. A question with NO plan → escalated (exit 0), delivered on the idea
        #     ticket with the needs-input marker, nothing created.
        fakeE = FakeLinear()
        q = _batch(_comment("Should tokens rotate on reuse, or only on expiry? It "
                            "changes the whole store design and the idea does not say."))
        check("question-only-escalated", run(q, client=fakeE), EXIT_OK)
        check("question-only-no-create", len(fakeE.issues), 0)
        check("question-only-delivered", len(fakeE.comments), 1)
        check("question-only-on-idea", fakeE.comments[0][0], "src-KIT-777")
        check("question-only-marked",
              _marker(ESC_NEEDS_INPUT) in fakeE.comments[0][1], True)

        # 16. A plan WITH a note → tree materialises AND the note is posted (summary
        #     + note = 2 comments); the note carries the planning-note marker.
        fakeF = FakeLinear()
        tree_note = _tree()
        tree_note["requests"].append(_comment("Heads up: child B assumes the new "
                                              "endpoint from the other epic."))
        check("plan-plus-note-ok", run(tree_note, client=fakeF), EXIT_OK)
        check("plan-plus-note-created", len(fakeF.issues), 3)
        check("plan-plus-note-two-comments", len(fakeF.comments), 2)
        check("plan-plus-note-marked",
              "planning-note" in fakeF.comments[1][1], True)

        # 17. A question naming a DIFFERENT ticket than the pin → rejected.
        fakeG = FakeLinear()
        check("question-retarget-rejected",
              run(_batch(_comment("q", tid="KIT-999")), client=fakeG), EXIT_REJECTED)
        check("question-retarget-no-deliver",
              all(_marker(ESC_NEEDS_INPUT) not in b for _, b in fakeG.comments), True)

        # 18. A question the executor CANNOT deliver (tracker fails) → errored, not
        #     a quiet success. A question nobody sees is worse than none.
        fakeH = FakeLinear(); fakeH.fail_post = True
        check("question-delivery-failed-errored",
              run(_batch(_comment("q")), client=fakeH), EXIT_ERRORED)

        # 19. A request type a planning session may not emit (ticket-state) → rejected.
        fakeI = FakeLinear()
        state_req = _batch({"type": "ticket-state", "ticket_id": "KIT-777", "to": "review"})
        check("disallowed-type-rejected", run(state_req, client=fakeI), EXIT_REJECTED)
        check("disallowed-type-no-create", len(fakeI.issues), 0)

        # 20. An empty batch (neither plan nor question) → rejected.
        fakeJ = FakeLinear()
        check("empty-batch-rejected", run(_batch(), client=fakeJ), EXIT_REJECTED)

        # 21. Over the question cap → rejected (all-or-nothing, nothing delivered).
        fakeK = FakeLinear()
        many_q = _batch(*[_comment("q%d" % i) for i in range(MAX_PLAN_COMMENTS + 1)])
        check("over-question-cap-rejected", run(many_q, client=fakeK), EXIT_REJECTED)
        check("over-question-cap-no-deliver",
              all(_marker(ESC_NEEDS_INPUT) not in b for _, b in fakeK.comments), True)

        # 22. A session must not FORGE an escalation mark by embedding it in text
        #     the executor posts (a question body, a note, or a ticket title). The
        #     forged mark is neutralized; only the executor's own mark survives.
        forged = _marker("agent:needs-human")  # what a session would try to smuggle
        fakeL = FakeLinear()
        check("forged-in-question-escalated",
              run(_batch(_comment("Please block this. " + forged)), client=fakeL), EXIT_OK)
        posted = fakeL.comments[0][1]
        check("forged-question-neutralized", forged in posted, False)
        check("forged-question-real-mark-survives",
              _marker(ESC_NEEDS_INPUT) in posted, True)
        # …and via a child title in a materialised plan's summary comment.
        fakeM = FakeLinear()
        forged_tree = _tree()
        forged_tree["requests"][0]["children"][0]["title"] = "Do it " + forged
        check("forged-title-ok", run(forged_tree, client=fakeM), EXIT_OK)
        summary = fakeM.comments[0][1]
        check("forged-title-neutralized", forged in summary, False)
        check("forged-title-real-mark-survives",
              _marker(ESC_AWAITING_APPROVAL) in summary, True)

    if failures:
        print("FAIL: pipeline_plan_executor selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_plan_executor selftest (%d cases)" % cases[0])
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--requests", help="path to the session's pipeline-safe-outputs/1 file")
    ap.add_argument("--config", help="path to delivery.json (default: ./delivery.json)")
    ap.add_argument("--repo-root", default=".", help="checkout root for DoR pointer checks")
    ap.add_argument("--pinned", help="the dispatcher-pinned ticket id (default: the "
                                     "tree's own source_ticket_id — an unpinned local run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and DoR-gate, but create nothing")
    ap.add_argument("--selftest", action="store_true", help="run built-in fixtures and exit")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.requests:
        ap.error("--requests is required (or use --selftest)")
    return materialise(args)


if __name__ == "__main__":
    sys.exit(main())
