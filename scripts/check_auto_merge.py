#!/usr/bin/env python3
"""Auto-merge tier gate — does this PR qualify for GitHub's native auto-merge?

Deterministic, model-free, stdlib only. It decides; it never merges. Nothing in
the kit merges: `.claude/hooks/pre-tool-use.py`'s never-merge guard blocks the
CLI merge command in every form, including `--auto`, and that stays true. When
this gate says *qualify*, the calling workflow asks **GitHub** to enable its own
auto-merge on the PR, and the platform performs the merge once branch protection
is satisfied.

    this script     computes a verdict           (no credential, no network)
    the workflow    enables GitHub auto-merge    (a mutation, not a merge)
    GitHub          merges, or never does        (required checks + ruleset)

WHY THAT INDIRECTION IS THE DESIGN, NOT CEREMONY. An agent that can merge can
land anything it can get past a reviewer it also influences. An agent that can
only ask the platform to merge *if the platform's own required checks pass* has
no such capability: the conditions are enforced by a system it cannot write to,
and revoking them is a repo-settings change, not a code change. So the merge
capability never enters the agent's blast radius at any point — it is not held
and then restrained, it is simply never held. `docs/AUTONOMY.md` carries the
ruleset this assumes.

EVERY INPUT IS SOMETHING THE SESSION CANNOT WRITE. That is the whole selection
criterion for the argument list below, and it is why some obvious inputs are
absent:

  used                             refused, and why
  ───────────────────────────────  ────────────────────────────────────────────
  delivery.json from the BASE sha  the head copy is inside the agent's worktree
  the review findings artifact     a PR comment is editable by the PR author
  git diff base...head             the PR body's own description of its size
  Actions run history (bounces)    `pipeline:bounce-N` PR labels — the fix
                                   session's token can edit PR labels, so a
                                   label can never hold a number that decides
                                   whether something merges
  check runs from the API          "CI passed" asserted in a commit message
  a RECOMPUTED approval verdict    a stored "was auto-approved" flag

The last row is the subtle one. "The ticket was auto-approved tier" could have
been a bit written down at approval time, but a stored bit is only as good as
everything that could have touched the store since. Instead the caller re-runs
`scripts/check_auto_approve.py` against the live ticket and passes the verdict
here, so the tier is *recomputed at merge time* from the same gates that granted
it. A ticket that has since been relabelled, reparented, or blocked stops
qualifying on its own.

THE GATES — all eight must pass. Zero is not "few".

  enabled      autonomy.autoMergeMaxLines > 0 AND the operator kill switch is on
  approved     the ticket still passes the auto-approval gate (epic provenance)
  bounces      exactly zero — anything that needed fixing goes to a human
  review       findings usable, and none at or above reviewSeverityThreshold
  ci           every check run terminal and green; none pending, none failing
  merge-state  not DIRTY (a conflicted PR is not a green PR), not UNKNOWN
  risk-paths   the diff touches nothing in autonomy.riskPaths
  size         added + removed <= autonomy.autoMergeMaxLines

WHY ZERO BOUNCES, SPECIFICALLY. A bounce means the first attempt was wrong in a
way a reviewer or CI caught. The fix may well be right — but the evidence that
the pipeline understood the ticket is now mixed, and mixed evidence is exactly
the case worth a human's thirty seconds. Bouncing is also the state in which the
most machine-authored churn has accumulated on the branch, so it is the state
where a human read is worth the most. `budgets.maxBounces > 0` and this gate are
not in tension: bounces are for getting a PR *ready for a person*, not for
getting it ready to merge itself.

FAIL DIRECTION: every uncertainty holds. An unreadable findings file is
UNREVIEWED, not clean; an empty check list is "CI did not report", not "CI
passed"; an absent config is off. A gate that cannot see something has not
seen it pass.

Usage:
    check_auto_merge.py --config PATH [--findings PATH] [--checks PATH]
                        [--approval PATH] [--changed PATH] [--lines-added N]
                        [--lines-removed N] [--bounces N] [--merge-state S]
                        [--kill-switch VALUE] [--json]
    check_auto_merge.py --selftest

Exit: 0 = qualifies, 1 = held (the normal, expected outcome), 2 = usage error.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_auto_approve import RISK_PATH_FLOOR, glob_to_re, implicates  # noqa: E402

SUPPORTED_VERSION = 1
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# GitHub's mergeStateStatus values that disqualify outright. BEHIND and BLOCKED
# are deliberately NOT here: BEHIND is what auto-merge exists to resolve, and
# BLOCKED usually means "a required review has not arrived yet" — which is the
# platform holding the PR exactly as intended, not a defect in the PR.
BAD_MERGE_STATES = {
    "DIRTY": "the PR has merge conflicts — a conflicted PR is not a green PR, and "
             "GitHub skips required CI on it, so side checks alone can look passing",
    "UNSTABLE": "a non-required check is failing — 'fully green' means every check",
    "UNKNOWN": "GitHub has not finished computing mergeability; an unknown state is "
               "not a passing one",
}

# Check-run conclusions that count as "did not block". NEUTRAL and SKIPPED are
# how a correctly-configured conditional job reports "not applicable here".
GOOD_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


class Verdict:
    def __init__(self):
        self.checks = []
        self.held = []

    def gate(self, name, ok, detail):
        self.checks.append({"gate": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.held.append({"gate": name, "reason": detail})
        return bool(ok)

    @property
    def qualifies(self):
        return not self.held


def load_json(path, label):
    """(data, error). A missing or unparseable input is an error, never {}."""
    if not path:
        return None, "%s was not supplied" % label
    if not os.path.exists(path):
        return None, "%s (%s) does not exist" % (label, path)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as e:
        return None, "%s (%s) is unreadable: %s" % (label, path, e)


def load_lines(path, label):
    if not path:
        return None, "%s was not supplied" % label
    if not os.path.exists(path):
        return None, "%s (%s) does not exist" % (label, path)
    try:
        with open(path, encoding="utf-8") as fh:
            return [l.strip() for l in fh if l.strip()], None
    except OSError as e:
        return None, "%s (%s) is unreadable: %s" % (label, path, e)


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def gate_enabled(config, kill_switch, v):
    autonomy = (config or {}).get("autonomy") or {}
    cap = autonomy.get("autoMergeMaxLines")
    if not isinstance(cap, int) or isinstance(cap, bool):
        return v.gate(
            "enabled", False,
            "autonomy.autoMergeMaxLines is %r — §1 defines an integer, and an "
            "unreadable cap is not an enabled one" % cap,
        ), 0
    if cap <= 0:
        return v.gate(
            "enabled", False,
            "autonomy.autoMergeMaxLines is %d — auto-merge is switched off for this "
            "project (the shipped default). Every PR waits for a person." % cap,
        ), cap
    # The second switch lives in repo VARIABLES, not in the repo tree. A config
    # change can be proposed in a PR; a repo variable cannot, so turning this on
    # is unavoidably a human at the GitHub UI. Two switches, two owners.
    if str(kill_switch).lower() != "true":
        return v.gate(
            "enabled", False,
            "the PIPELINE_AUTO_MERGE_ENABLED repository variable is %r, not \"true\" — "
            "auto-merge stays off until an operator turns it on outside the repo tree"
            % (kill_switch or ""),
        ), cap
    return v.gate("enabled", True, "auto-merge enabled, cap %d changed line(s)" % cap), cap


def gate_approved(approval, approval_error, v):
    if approval_error:
        return v.gate(
            "approved", False,
            "no recomputed approval verdict: %s. The auto-merge tier is defined as "
            "'the ticket still qualifies for auto-approval', and an unverified tier "
            "is not a tier" % approval_error,
        )
    if approval.get("schema") != "pipeline-auto-approve/1":
        return v.gate(
            "approved", False,
            "approval verdict declares schema %r, not pipeline-auto-approve/1 — "
            "refusing to interpret an unrecognized shape" % approval.get("schema"),
        )
    if not approval.get("ok"):
        held = approval.get("tickets") or [{}]
        reasons = "; ".join(
            "[%s] %s" % (h.get("gate"), h.get("reason"))
            for t in held for h in (t.get("held") or [])
        )[:400]
        return v.gate(
            "approved", False,
            "the ticket no longer passes the auto-approval gate: %s" % (reasons or "held"),
        )
    return v.gate(
        "approved", True,
        "ticket still passes the auto-approval gate (%s)"
        % (", ".join(approval.get("approve") or []) or "recomputed"),
    )


def gate_bounces(bounces, v):
    if bounces is None:
        return v.gate(
            "bounces", False,
            "the bounce count was not supplied — it is read from Actions run history, "
            "and a count nobody produced is not a zero",
        )
    if bounces != 0:
        return v.gate(
            "bounces", False,
            "%d bounce(s) on this branch — anything that needed fixing goes to a "
            "human. The fix may be right; the evidence is now mixed." % bounces,
        )
    return v.gate("bounces", True, "zero bounces — first attempt, unamended")


def gate_review(findings, findings_error, threshold, v):
    if findings_error:
        return v.gate(
            "review", False,
            "no usable review findings: %s. Treat the PR as UNREVIEWED, not as clean."
            % findings_error,
        )
    if findings.get("schema") != "pipeline-review/1":
        return v.gate(
            "review", False,
            "findings declare schema %r, not pipeline-review/1" % findings.get("schema"),
        )
    if not findings.get("usable"):
        return v.gate(
            "review", False,
            "the reviewer did not produce usable findings (%s) — UNREVIEWED is not clean"
            % (str(findings.get("summary") or "")[:200] or "no summary"),
        )
    rank = SEVERITY_RANK.get(threshold)
    if rank is None:
        return v.gate(
            "review", False,
            "budgets.reviewSeverityThreshold is %r, outside low|medium|high|critical — "
            "there is no bar to clear" % threshold,
        )
    blocking = [
        f for f in (findings.get("findings") or [])
        if isinstance(f, dict) and SEVERITY_RANK.get(f.get("severity"), 0) >= rank
    ]
    if blocking:
        top = ", ".join(
            "%s/%s" % (f.get("severity"), f.get("category") or "general") for f in blocking[:5]
        )
        return v.gate(
            "review", False,
            "%d finding(s) at or above the `%s` threshold (%s)"
            % (len(blocking), threshold, top),
        )
    below = len(findings.get("findings") or [])
    return v.gate(
        "review", True,
        "zero findings at or above `%s`%s"
        % (threshold, " (%d below it, comment-only)" % below if below else ""),
    )


def gate_ci(checks, checks_error, v):
    if checks_error:
        return v.gate("ci", False, "no check-run data: %s" % checks_error)
    if isinstance(checks, dict):
        checks = checks.get("checks") or checks.get("statusCheckRollup") or []
    if not isinstance(checks, list) or not checks:
        return v.gate(
            "ci", False,
            "no check runs reported — 'CI did not report' is not 'CI passed'",
        )
    pending, failing = [], []
    for c in checks:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or c.get("context") or "?")
        state = str(c.get("state") or c.get("conclusion") or "").upper()
        status = str(c.get("status") or "").upper()
        if status and status not in ("COMPLETED",) and not state:
            pending.append(name)
        elif state in ("", "PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "EXPECTED"):
            pending.append(name)
        elif state not in GOOD_CONCLUSIONS:
            failing.append("%s=%s" % (name, state or "?"))
    if failing:
        return v.gate("ci", False, "check(s) not green: %s" % ", ".join(sorted(failing)[:8]))
    if pending:
        return v.gate("ci", False, "check(s) still running: %s" % ", ".join(sorted(pending)[:8]))
    return v.gate("ci", True, "all %d check run(s) green" % len(checks))


def gate_merge_state(state, v):
    got = str(state or "").upper()
    if not got:
        return v.gate("merge-state", False, "no mergeStateStatus supplied")
    if got in BAD_MERGE_STATES:
        return v.gate("merge-state", False, "%s: %s" % (got, BAD_MERGE_STATES[got]))
    return v.gate("merge-state", True, "mergeStateStatus %s" % got)


def gate_risk_paths(changed, changed_error, config, v):
    if changed_error:
        return v.gate("risk-paths", False, "no changed-file list: %s" % changed_error)
    configured = ((config or {}).get("autonomy") or {}).get("riskPaths") or []
    globs = list(RISK_PATH_FLOOR)
    for g in configured:
        if isinstance(g, str) and g.strip() and g.strip() not in globs:
            globs.append(g.strip())
    compiled = [(g, re.compile(glob_to_re(g))) for g in globs]
    hits = []
    for path in changed:
        for g, rx in compiled:
            if implicates(path, rx):
                hits.append("%s (matches %s)" % (path, g))
                break
    if hits:
        return v.gate(
            "risk-paths", False,
            "the diff touches risk-allowlisted path(s): %s" % "; ".join(sorted(hits)[:6]),
        )
    return v.gate("risk-paths", True, "%d changed file(s), none risk-allowlisted" % len(changed))


def gate_size(added, removed, cap, v):
    if added is None or removed is None:
        return v.gate("size", False, "diff line counts were not supplied")
    total = added + removed
    if cap <= 0:
        return v.gate("size", False, "no positive line cap to compare against")
    if total > cap:
        return v.gate(
            "size", False,
            "%d changed line(s) (+%d/-%d) exceeds autonomy.autoMergeMaxLines (%d)"
            % (total, added, removed, cap),
        )
    return v.gate("size", True, "%d changed line(s) (+%d/-%d), cap %d" % (total, added, removed, cap))


def decide(config, kill_switch, approval, approval_error, bounces, findings,
           findings_error, checks, checks_error, merge_state, changed,
           changed_error, added, removed):
    v = Verdict()
    _, cap = gate_enabled(config, kill_switch, v)
    gate_approved(approval, approval_error, v)
    gate_bounces(bounces, v)
    threshold = ((config or {}).get("budgets") or {}).get("reviewSeverityThreshold")
    gate_review(findings, findings_error, threshold, v)
    gate_ci(checks, checks_error, v)
    gate_merge_state(merge_state, v)
    gate_risk_paths(changed, changed_error, config, v)
    gate_size(added, removed, cap, v)
    return {
        "schema": "pipeline-auto-merge/1",
        "qualifies": v.qualifies,
        "checks": v.checks,
        "held": v.held,
    }


def print_text(result):
    for c in result["checks"]:
        print("  %s  %-12s %s" % ("PASS" if c["ok"] else "HOLD", c["gate"], c["detail"]))
    if result["qualifies"]:
        print("QUALIFIES — the workflow may ask GitHub to enable auto-merge on this PR.")
    else:
        print("HELD (%d gate(s)) — this PR merges only when a human merges it."
              % len(result["held"]))


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
GOOD_CONFIG = {
    "version": 1,
    "budgets": {"reviewSeverityThreshold": "medium"},
    "autonomy": {
        "autoApproveProvenance": ["epic"],
        "autoMergeMaxLines": 150,
        "riskPaths": [".claude/hooks/**", ".claude/settings*.json", "delivery.json"],
    },
}
GOOD_FINDINGS = {
    "schema": "pipeline-review/1", "usable": True, "threshold": "medium",
    "reviewer_outcome": "success", "summary": "Looks fine.",
    "findings": [], "max_severity": None, "meets_threshold": False,
}
GOOD_CHECKS = [
    {"name": "Kit checks", "status": "COMPLETED", "state": "SUCCESS"},
    {"name": "Provenance scan", "status": "COMPLETED", "state": "SUCCESS"},
]
GOOD_APPROVAL = {
    "schema": "pipeline-auto-approve/1", "ok": True,
    "approve": ["ENG-123"], "hold": [],
    "tickets": [{"ref": "ENG-123", "approved": True, "held": []}],
}


def selftest():
    import copy

    failures = []
    cases = [0]

    def run(**over):
        kw = dict(
            config=GOOD_CONFIG, kill_switch="true", approval=GOOD_APPROVAL,
            approval_error=None, bounces=0, findings=GOOD_FINDINGS,
            findings_error=None, checks=GOOD_CHECKS, checks_error=None,
            merge_state="CLEAN", changed=["src/auth/token.ts", "src/auth/token.test.ts"],
            changed_error=None, added=40, removed=12,
        )
        kw.update(over)
        return decide(**kw)

    def expect(label, result, qualifies, gate=None):
        cases[0] += 1
        if result["qualifies"] != qualifies:
            failures.append(
                "%s: expected %s, got %s (%s)"
                % (label, "QUALIFY" if qualifies else "HOLD",
                   "QUALIFY" if result["qualifies"] else "HOLD",
                   "; ".join(h["reason"] for h in result["held"]) or "no holds"))
            return
        if gate and not any(h["gate"] == gate for h in result["held"]):
            failures.append(
                "%s: held, but not on gate %r (held on %s)"
                % (label, gate, ", ".join(h["gate"] for h in result["held"]) or "nothing"))

    expect("baseline qualifies", run(), True)

    # ── enabled ─────────────────────────────────────────────────────────────
    off = copy.deepcopy(GOOD_CONFIG)
    off["autonomy"]["autoMergeMaxLines"] = 0
    expect("cap 0 disables auto-merge", run(config=off), False, "enabled")
    expect("kill switch off holds", run(kill_switch="false"), False, "enabled")
    expect("kill switch unset holds", run(kill_switch=""), False, "enabled")
    bad = copy.deepcopy(GOOD_CONFIG)
    bad["autonomy"]["autoMergeMaxLines"] = "150"
    expect("non-integer cap holds", run(config=bad), False, "enabled")

    # ── approved tier ───────────────────────────────────────────────────────
    expect("missing approval verdict holds",
           run(approval=None, approval_error="not supplied"), False, "approved")
    expect("held approval verdict holds",
           run(approval={"schema": "pipeline-auto-approve/1", "ok": False,
                         "tickets": [{"held": [{"gate": "provenance",
                                                "reason": "provenance is `monitor`"}]}]}),
           False, "approved")
    expect("unrecognized approval schema holds",
           run(approval={"schema": "something-else/9", "ok": True}), False, "approved")

    # ── bounces ─────────────────────────────────────────────────────────────
    expect("one bounce holds", run(bounces=1), False, "bounces")
    expect("unknown bounce count holds", run(bounces=None), False, "bounces")

    # ── review ──────────────────────────────────────────────────────────────
    at_threshold = copy.deepcopy(GOOD_FINDINGS)
    at_threshold["findings"] = [{"severity": "medium", "category": "correctness",
                                 "summary": "off-by-one"}]
    expect("finding at threshold holds", run(findings=at_threshold), False, "review")
    above = copy.deepcopy(GOOD_FINDINGS)
    above["findings"] = [{"severity": "critical", "category": "security", "summary": "leak"}]
    expect("finding above threshold holds", run(findings=above), False, "review")
    below = copy.deepcopy(GOOD_FINDINGS)
    below["findings"] = [{"severity": "low", "category": "tests", "summary": "nit"}]
    expect("finding below threshold still qualifies", run(findings=below), True)
    unusable = copy.deepcopy(GOOD_FINDINGS)
    unusable["usable"] = False
    expect("unusable findings hold (UNREVIEWED != clean)",
           run(findings=unusable), False, "review")
    expect("missing findings file holds",
           run(findings=None, findings_error="does not exist"), False, "review")
    noth = copy.deepcopy(GOOD_CONFIG)
    noth["budgets"]["reviewSeverityThreshold"] = "urgent"
    expect("invalid threshold holds", run(config=noth), False, "review")

    # ── ci ──────────────────────────────────────────────────────────────────
    expect("failing check holds",
           run(checks=[{"name": "Kit checks", "status": "COMPLETED", "state": "FAILURE"}]),
           False, "ci")
    expect("pending check holds",
           run(checks=GOOD_CHECKS + [{"name": "Deploy", "status": "IN_PROGRESS", "state": ""}]),
           False, "ci")
    expect("empty check list holds", run(checks=[]), False, "ci")
    expect("skipped/neutral checks are green",
           run(checks=[{"name": "E2E", "status": "COMPLETED", "state": "SKIPPED"},
                       {"name": "Kit checks", "status": "COMPLETED", "state": "SUCCESS"}]),
           True)

    # ── merge state ─────────────────────────────────────────────────────────
    expect("dirty PR holds", run(merge_state="DIRTY"), False, "merge-state")
    expect("unstable PR holds", run(merge_state="UNSTABLE"), False, "merge-state")
    expect("unknown mergeability holds", run(merge_state="UNKNOWN"), False, "merge-state")
    expect("behind PR still qualifies", run(merge_state="BEHIND"), True)
    expect("blocked PR still qualifies (a missing review is the platform holding it)",
           run(merge_state="BLOCKED"), True)

    # ── risk paths ──────────────────────────────────────────────────────────
    expect("hooks change holds",
           run(changed=["src/a.ts", ".claude/hooks/pre-tool-use.py"]), False, "risk-paths")
    expect("workflow change holds",
           run(changed=[".github/workflows/ci.yml"]), False, "risk-paths")
    expect("skill change holds (rubrics are graders)",
           run(changed=[".claude/skills/ship/SKILL.md"]), False, "risk-paths")
    expect("delivery.json change holds", run(changed=["delivery.json"]), False, "risk-paths")
    short = copy.deepcopy(GOOD_CONFIG)
    short["autonomy"]["riskPaths"] = []
    expect("risk floor survives an emptied config list",
           run(config=short, changed=[".claude/hooks/audit.py"]), False, "risk-paths")
    expect("missing changed-file list holds",
           run(changed=None, changed_error="not supplied"), False, "risk-paths")

    # ── size ────────────────────────────────────────────────────────────────
    expect("oversized diff holds", run(added=200, removed=10), False, "size")
    expect("diff exactly at the cap qualifies", run(added=150, removed=0), True)
    expect("one line over the cap holds", run(added=150, removed=1), False, "size")
    expect("missing line counts hold", run(added=None, removed=None), False, "size")

    # ── all gates are reported, not short-circuited ─────────────────────────
    cases[0] += 1
    many = run(bounces=3, merge_state="DIRTY", added=900, removed=900)
    if len({h["gate"] for h in many["held"]}) < 3:
        failures.append("a PR failing several gates must report all of them, not the first")

    if failures:
        print("FAIL: %d of %d auto-merge case(s) failed:" % (len(failures), cases[0]))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: %d auto-merge case(s) passed" % cases[0])
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="delivery.json, read from the PR's BASE sha")
    ap.add_argument("--findings", help="findings.json from the pipeline-review artifact")
    ap.add_argument("--checks", help="JSON array of the PR's check runs")
    ap.add_argument("--approval", help="recomputed pipeline-auto-approve/1 verdict")
    ap.add_argument("--changed", help="file with one changed path per line (base...head)")
    ap.add_argument("--lines-added", type=int, default=None)
    ap.add_argument("--lines-removed", type=int, default=None)
    ap.add_argument("--bounces", type=int, default=None, help="prior bounce runs on this branch")
    ap.add_argument("--merge-state", default="", help="GitHub mergeStateStatus")
    ap.add_argument("--kill-switch", default="", help="PIPELINE_AUTO_MERGE_ENABLED value")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.config:
        print("FAIL: --config is required (delivery.json from the PR's base sha)", file=sys.stderr)
        return 2

    config, config_error = load_json(args.config, "--config")
    if config_error:
        # Absent config is *off* (§2). Off means nothing auto-merges — which is a
        # hold, reported as one, not a crash and not a pass.
        result = {"schema": "pipeline-auto-merge/1", "qualifies": False,
                  "checks": [{"gate": "config", "ok": False, "detail": config_error}],
                  "held": [{"gate": "config", "reason": config_error}]}
    elif config.get("version") != SUPPORTED_VERSION:
        detail = ("delivery.json declares version %r; this gate implements contract "
                  "version %d and will not guess" % (config.get("version"), SUPPORTED_VERSION))
        result = {"schema": "pipeline-auto-merge/1", "qualifies": False,
                  "checks": [{"gate": "config", "ok": False, "detail": detail}],
                  "held": [{"gate": "config", "reason": detail}]}
    else:
        findings, findings_error = load_json(args.findings, "--findings")
        checks, checks_error = load_json(args.checks, "--checks")
        approval, approval_error = load_json(args.approval, "--approval")
        changed, changed_error = load_lines(args.changed, "--changed")
        result = decide(
            config, args.kill_switch, approval, approval_error, args.bounces,
            findings, findings_error, checks, checks_error, args.merge_state,
            changed, changed_error, args.lines_added, args.lines_removed,
        )

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print_text(result)
    return 0 if result["qualifies"] else 1


if __name__ == "__main__":
    sys.exit(main())
