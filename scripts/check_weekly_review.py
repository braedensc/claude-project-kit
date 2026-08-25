#!/usr/bin/env python3
"""Weekly-review validator — the three limits, mechanized.

`/weekly-review` produces one document: what shipped, pipeline health, what it
learned, backlog state, next period's plan, and quarantined feature proposals.
Three limits on that document are load-bearing, and a limit that exists only as
a sentence in a prompt is a limit the next model revision may not honour. This
script is what makes them checkable:

  1. PROPOSED RUBRIC AND PROMPT CHANGES SHIP AS NORMAL REVIEWED PRs.
     A retrospective that could edit its own graders is a system that grades its
     own homework. Enforced in three places, of which this is the third:
       • `scripts/check_auto_approve.py` holds any ticket naming a grader path
       • `scripts/check_auto_merge.py` refuses any diff touching one
       • here: every proposed rubric change must declare `"delivery": "pr"`,
         and naming a grader path with anything else is an error
     The first two are the teeth; this one catches the intent before a session
     spends anything acting on it.

  2. INVENTED FEATURES CARRY `retro-proposal` PROVENANCE AND STAY IN `raw`.
     §5 already makes that class unable to auto-approve, so the enforcement is
     structural — but only if the tickets actually get filed that way. Here every
     proposed ticket must declare `provenance: retro-proposal` and `state: raw`,
     so a proposal cannot be dressed up as epic-decomposed work on the way out.

  3. THE REVIEW CANNOT RAISE ITS OWN BUDGETS, WIP OR CAPS.
     This is the one that matters most and is easiest to lose. A retrospective
     reads telemetry saying "we ran out of turns four times" and the locally
     sensible conclusion is "raise maxTurns" — which is a system deciding how
     much it may spend on itself. Every proposed config change is diffed against
     the committed `delivery.json` and any LOOSENING is an error, in every shape
     it can take: a cap raised, the review severity threshold raised (which
     blocks *fewer* findings), a riskPath removed, an approval class added.

     Tightening is always allowed. A review that concludes the pipeline should
     have LESS rope is a review working correctly.

WHAT IT READS. One fenced `pipeline-weekly-review/1` JSON block inside the
document — the same shape the telemetry block uses, for the same reason: the
prose is for the person, and the machine-checkable claims are in one object a
script can be strict about. The prose sections are checked for presence only;
nothing here grades writing.

Usage:
    check_weekly_review.py [--config PATH] [--json] FILE
    check_weekly_review.py --selftest

Exit: 0 = the document may be published, 1 = errors, 2 = usage/IO error.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_auto_approve import RISK_PATH_FLOOR, glob_to_re, implicates  # noqa: E402

SCHEMA = "pipeline-weekly-review/1"
SUPPORTED_VERSION = 1

# The six sections the deliverable is defined as. Presence only — a section that
# exists and says "nothing this week" is a valid answer, and the alternative
# (grading prose) is exactly the kind of check that fails open.
REQUIRED_SECTIONS = [
    "What shipped",
    "Pipeline health",
    "What it learned",
    "Backlog state",
    "Next period's plan",
    "Feature proposals",
]

FENCE_RE = re.compile(r"^[ \t]*```[ \t]*json[ \t]*$(.*?)^[ \t]*```[ \t]*$",
                      re.MULTILINE | re.DOTALL)
HEADING_RE = re.compile(r"^##\s+(\S.*?)\s*$", re.MULTILINE)

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# Every numeric knob that bounds what the pipeline may spend or attempt. `*`
# matches one path segment. Direction is what the review may NOT do.
CAP_RULES = [
    ("budgets.maxTurns", "no-increase"),
    ("budgets.wipLimit", "no-increase"),
    ("budgets.maxBounces", "no-increase"),
    ("budgets.fixIterations", "no-increase"),
    ("budgets.totalAttempts", "no-increase"),
    ("budgets.dailyUsd", "no-increase"),
    ("budgets.perEffort.*.maxTurns", "no-increase"),
    ("budgets.perEffort.*.maxUsd", "no-increase"),
    ("budgets.perEffort.*.maxMinutes", "no-increase"),
    ("autonomy.autoMergeMaxLines", "no-increase"),
    ("budgets.reviewSeverityThreshold", "no-loosen-severity"),
    ("autonomy.autoApproveProvenance", "no-widen-list"),
    ("autonomy.riskPaths", "no-shrink-list"),
]

# Board wiring is not a retrospective's business at all. Changing a state or
# label ID silently redirects every guard that compares them (§1).
FROZEN_PREFIXES = ("linear.stateIds", "linear.labels", "github.", "dispatch.",
                   "version", "auth.")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, rule, message):
        self.errors.append({"rule": rule, "message": message})

    def warn(self, rule, message):
        self.warnings.append({"rule": rule, "message": message})

    def as_dict(self):
        return {"schema": "pipeline-weekly-review-check/1",
                "ok": not self.errors,
                "errors": self.errors, "warnings": self.warnings}


def path_matches(pattern, path):
    pp, ap = pattern.split("."), path.split(".")
    if len(pp) != len(ap):
        return False
    return all(p == "*" or p == a for p, a in zip(pp, ap))


def flatten(obj, prefix=""):
    """Leaf paths of the proposal. Lists are leaves — a list rule compares whole."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = "%s.%s" % (prefix, k) if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
    elif prefix:
        out[prefix] = obj
    return out


def committed_at(config, path):
    node = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def check_config_changes(proposed, config, r):
    """Limit 3. Tightening passes; every loosening is an error."""
    if proposed in (None, {}):
        return
    if not isinstance(proposed, dict):
        r.err("config", "proposed_config_changes is %s — expected an object (use {} for none)"
              % type(proposed).__name__)
        return
    if config is None:
        r.err("config",
              "proposed_config_changes is non-empty but there is no committed "
              "delivery.json to compare against — a proposal nobody can diff is a "
              "proposal nobody can check")
        return

    for path, value in sorted(flatten(proposed).items()):
        if any(path == p.rstrip(".") or path.startswith(p) for p in FROZEN_PREFIXES):
            r.err("config",
                  "%s is board and dispatch wiring — a retrospective does not remap "
                  "the tracker, the repo, or the dispatcher (§1)" % path)
            continue

        rule = next((d for p, d in CAP_RULES if path_matches(p, path)), None)
        current, present = committed_at(config, path)

        if rule is None:
            # Not a cap and not frozen — allowed, but say so out loud. A silent
            # "everything else is fine" is how the next knob gets added without
            # anyone deciding it should be raisable.
            r.warn("config", "%s is not a recognized cap — proposing it is allowed, "
                             "but confirm it is not a budget in disguise" % path)
            continue

        if rule == "no-increase":
            got, cur = num(value), num(current)
            if got is None:
                r.err("config", "%s is proposed as %r — expected a number" % (path, value))
            elif cur is None:
                r.err("config", "%s has no committed value to compare against; a cap "
                                "cannot be introduced by a retrospective" % path)
            elif got > cur:
                r.err("config",
                      "%s would rise from %s to %s — the review cannot raise its own "
                      "budgets, WIP or caps. Propose the increase to a human instead."
                      % (path, cur, got))
        elif rule == "no-loosen-severity":
            got, cur = SEVERITY_RANK.get(value), SEVERITY_RANK.get(current)
            if got is None:
                r.err("config", "%s is proposed as %r, outside low|medium|high|critical"
                      % (path, value))
            elif cur is not None and got > cur:
                # §1: the threshold is the LOWEST severity that blocks progress, so
                # raising it means fewer findings block — looser, not stricter.
                r.err("config",
                      "%s would loosen from `%s` to `%s` — a higher threshold means "
                      "fewer findings block progress, which is a cap increase wearing "
                      "a different name" % (path, current, value))
        elif rule == "no-widen-list":
            got = value if isinstance(value, list) else None
            if got is None:
                r.err("config", "%s is proposed as %r — expected an array" % (path, value))
            else:
                added = sorted(set(map(str, got)) - set(map(str, current or [])))
                if added:
                    r.err("config",
                          "%s would gain %s — a retrospective cannot widen what "
                          "auto-approves (§5 rule 3 caps it at [\"epic\"] anyway)"
                          % (path, ", ".join(repr(a) for a in added)))
        elif rule == "no-shrink-list":
            got = value if isinstance(value, list) else None
            if got is None:
                r.err("config", "%s is proposed as %r — expected an array" % (path, value))
            else:
                removed = sorted(set(map(str, current or [])) - set(map(str, got)))
                if removed:
                    r.err("config",
                          "%s would drop %s — removing a risk path is removing "
                          "supervision, not tidying config"
                          % (path, ", ".join(repr(x) for x in removed)))
        if not present and rule is not None and rule.endswith("list"):
            r.warn("config", "%s is not present in the committed config" % path)


def check_rubric_changes(items, config, r):
    """Limit 1. Every grader edit is a PR a person reviews, never an applied diff."""
    if items in (None, []):
        return
    if not isinstance(items, list):
        r.err("rubric", "proposed_rubric_changes is %s — expected an array"
              % type(items).__name__)
        return
    globs = list(RISK_PATH_FLOOR)
    for g in ((config or {}).get("autonomy") or {}).get("riskPaths") or []:
        if isinstance(g, str) and g.strip() and g.strip() not in globs:
            globs.append(g.strip())
    compiled = [(g, re.compile(glob_to_re(g))) for g in globs]

    for i, item in enumerate(items):
        where = "proposed_rubric_changes[%d]" % i
        if not isinstance(item, dict):
            r.err("rubric", "%s is %s — expected an object" % (where, type(item).__name__))
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            r.err("rubric", "%s has no `path`" % where)
            continue
        delivery = str(item.get("delivery") or "").strip().lower()
        if delivery != "pr":
            r.err("rubric",
                  "%s (%s) declares delivery %r — a rubric or prompt change ships as a "
                  "normal reviewed PR, so the pipeline cannot silently rewrite its own "
                  "graders. Use \"pr\"." % (where, path, delivery or "none"))
        if str(item.get("status") or "").strip().lower() in ("applied", "merged", "done"):
            r.err("rubric",
                  "%s (%s) is marked as already applied — a retrospective proposes "
                  "grader changes; it does not land them" % (where, path))
        if not any(implicates(path, rx) for _, rx in compiled):
            # Not fatal: a rubric can live outside the guarded set in a project
            # that arranges its prompts differently. But it is worth saying,
            # because a grader outside riskPaths is a grader nothing protects.
            r.warn("rubric",
                   "%s (%s) is not covered by any riskPath glob — if this really is a "
                   "grader, add its path to autonomy.riskPaths so the auto-merge gate "
                   "refuses it too" % (where, path))
        if not str(item.get("rationale") or "").strip():
            r.warn("rubric", "%s has no rationale — a grader change without a stated "
                             "reason is hard for a reviewer to judge" % where)


def check_proposed_tickets(items, r):
    """Limit 2. Invented work is quarantined by provenance, at the point of filing."""
    if items in (None, []):
        return
    if not isinstance(items, list):
        r.err("proposals", "proposed_tickets is %s — expected an array" % type(items).__name__)
        return
    for i, item in enumerate(items):
        where = "proposed_tickets[%d]" % i
        if not isinstance(item, dict):
            r.err("proposals", "%s is %s — expected an object" % (where, type(item).__name__))
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            r.err("proposals", "%s has no title" % where)
        prov = str(item.get("provenance") or "").strip()
        if prov != "retro-proposal":
            r.err("proposals",
                  "%s (%s) declares provenance %r — everything a retrospective invents "
                  "carries `retro-proposal`, which §5 makes unable to auto-approve. "
                  "Anything else would let the pipeline widen its own mandate."
                  % (where, title or "untitled", prov or "none"))
        state = str(item.get("state") or "").strip()
        if state and state != "raw":
            r.err("proposals",
                  "%s (%s) would be filed into `%s` — proposals land in `raw` and wait "
                  "for a person" % (where, title or "untitled", state))
        labels = [str(x) for x in (item.get("labels") or [])]
        bad = sorted(l for l in labels
                     if l.startswith(("agent:", "blocked:")) or l.startswith("provenance:")
                     and l != "provenance:retro-proposal")
        if bad:
            r.err("proposals",
                  "%s carries %s — `agent:*`, `blocked:*` and `provenance:*` are "
                  "dispatcher- and human-owned (§6)" % (where, ", ".join(bad)))


def check_sections(text, r):
    found = [h.strip().lower() for h in HEADING_RE.findall(text or "")]
    for name in REQUIRED_SECTIONS:
        if not any(f.startswith(name.lower()) for f in found):
            r.err("sections", "the document has no `## %s` section — the weekly review "
                              "is one document with all six" % name)


def review_block(text):
    """(block, error). Exactly one `pipeline-weekly-review/1` fence."""
    blocks = []
    for m in FENCE_RE.finditer(text or ""):
        try:
            doc = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(doc, dict) and doc.get("schema") == SCHEMA:
            blocks.append(doc)
    if not blocks:
        return None, ("the document carries no `%s` JSON block — the machine-checkable "
                      "claims (config changes, rubric changes, proposed tickets) have to "
                      "be in one object a script can be strict about" % SCHEMA)
    if len(blocks) > 1:
        return None, "the document carries %d `%s` blocks — there must be exactly one" % (
            len(blocks), SCHEMA)
    return blocks[0], None


def check(text, config):
    r = Report()
    check_sections(text, r)
    block, err = review_block(text)
    if err:
        r.err("block", err)
        return r.as_dict()
    check_config_changes(block.get("proposed_config_changes"), config, r)
    check_rubric_changes(block.get("proposed_rubric_changes"), config, r)
    check_proposed_tickets(block.get("proposed_tickets"), r)
    return r.as_dict()


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
CONFIG = {
    "version": 1,
    "budgets": {
        "perEffort": {"S": {"maxTurns": 25, "maxUsd": 2.0, "maxMinutes": 20},
                      "M": {"maxTurns": 60, "maxUsd": 6.0, "maxMinutes": 45}},
        "maxTurns": 150, "wipLimit": 3, "maxBounces": 2, "fixIterations": 3,
        "totalAttempts": 3, "dailyUsd": 50.0, "reviewSeverityThreshold": "medium",
    },
    "autonomy": {"autoApproveProvenance": ["epic"], "autoMergeMaxLines": 0,
                 "riskPaths": [".claude/hooks/**", ".claude/settings*.json", "delivery.json"]},
    "linear": {"stateIds": {"raw": "s-raw"}},
}


def document(block):
    sections = "\n\n".join("## %s\n\nSomething true about this week." % s
                           for s in REQUIRED_SECTIONS)
    return "# Weekly review\n\n%s\n\n```json\n%s\n```\n" % (sections, json.dumps(block, indent=2))


def base_block(**over):
    block = {
        "schema": SCHEMA,
        "period": {"since": "2026-08-17T00:00:00Z", "until": "2026-08-24T00:00:00Z"},
        "proposed_config_changes": {},
        "proposed_rubric_changes": [],
        "proposed_tickets": [],
    }
    block.update(over)
    return block


def selftest():
    failures = []
    cases = [0]

    def expect(label, result, ok, rule=None):
        cases[0] += 1
        if result["ok"] != ok:
            failures.append("%s: expected %s, got %s (%s)"
                            % (label, "OK" if ok else "ERROR", "OK" if result["ok"] else "ERROR",
                               "; ".join(e["message"] for e in result["errors"]) or "no errors"))
            return
        if rule and not any(e["rule"] == rule for e in result["errors"]):
            failures.append("%s: failed, but not on rule %r (got %s)"
                            % (label, rule, ", ".join(e["rule"] for e in result["errors"])))

    run = lambda block, cfg=CONFIG: check(document(block), cfg)  # noqa: E731

    expect("an empty, honest review passes", run(base_block()), True)

    # ── Structure ───────────────────────────────────────────────────────────
    expect("a document with no block fails", check("# Weekly review\n\nnothing\n", CONFIG),
           False, "block")
    cases[0] += 1
    partial = "## What shipped\n\nx\n\n```json\n%s\n```\n" % json.dumps(base_block())
    if check(partial, CONFIG)["ok"]:
        failures.append("a document missing five sections must fail")
    cases[0] += 1
    doubled = document(base_block())
    doubled += "\n```json\n%s\n```\n" % json.dumps(base_block())
    if check(doubled, CONFIG)["ok"]:
        failures.append("two review blocks must fail")

    # ── Limit 3: no raising your own caps ───────────────────────────────────
    for path, value, label in (
        ({"budgets": {"maxTurns": 300}}, 300, "maxTurns"),
        ({"budgets": {"wipLimit": 10}}, 10, "wipLimit"),
        ({"budgets": {"dailyUsd": 500.0}}, 500.0, "dailyUsd"),
        ({"budgets": {"maxBounces": 5}}, 5, "maxBounces"),
        ({"budgets": {"totalAttempts": 9}}, 9, "totalAttempts"),
        ({"budgets": {"fixIterations": 9}}, 9, "fixIterations"),
        ({"budgets": {"perEffort": {"M": {"maxUsd": 60.0}}}}, 60.0, "perEffort maxUsd"),
        ({"autonomy": {"autoMergeMaxLines": 5000}}, 5000, "autoMergeMaxLines"),
    ):
        expect("raising %s is refused" % label,
               run(base_block(proposed_config_changes=path)), False, "config")

    for path, label in (
        ({"budgets": {"maxTurns": 100}}, "maxTurns"),
        ({"budgets": {"dailyUsd": 20.0}}, "dailyUsd"),
        ({"budgets": {"perEffort": {"M": {"maxUsd": 3.0}}}}, "perEffort maxUsd"),
        ({"autonomy": {"autoMergeMaxLines": 0}}, "autoMergeMaxLines held at 0"),
    ):
        expect("LOWERING %s is allowed" % label,
               run(base_block(proposed_config_changes=path)), True)

    # The threshold is the LOWEST severity that blocks (§1), so `high` blocks
    # less than `medium` does — raising it is the loosening.
    expect("raising the severity threshold (fewer findings block) is refused",
           run(base_block(proposed_config_changes={"budgets": {"reviewSeverityThreshold": "high"}})),
           False, "config")
    expect("lowering the severity threshold (more findings block) is allowed",
           run(base_block(proposed_config_changes={"budgets": {"reviewSeverityThreshold": "low"}})),
           True)
    expect("widening autoApproveProvenance is refused",
           run(base_block(proposed_config_changes={
               "autonomy": {"autoApproveProvenance": ["epic", "retro-proposal"]}})),
           False, "config")
    expect("narrowing autoApproveProvenance is allowed",
           run(base_block(proposed_config_changes={"autonomy": {"autoApproveProvenance": []}})),
           True)
    expect("dropping a riskPath is refused",
           run(base_block(proposed_config_changes={
               "autonomy": {"riskPaths": [".claude/hooks/**", "delivery.json"]}})),
           False, "config")
    expect("adding a riskPath is allowed",
           run(base_block(proposed_config_changes={"autonomy": {"riskPaths": [
               ".claude/hooks/**", ".claude/settings*.json", "delivery.json",
               ".claude/skills/**"]}})),
           True)
    expect("remapping a Linear state is refused",
           run(base_block(proposed_config_changes={"linear": {"stateIds": {"raw": "s-other"}}})),
           False, "config")
    expect("changing the dispatch backend is refused",
           run(base_block(proposed_config_changes={"dispatch": {"backend": "cloud"}})),
           False, "config")
    expect("a config proposal with no committed config to diff is refused",
           check(document(base_block(proposed_config_changes={"budgets": {"maxTurns": 10}})), None),
           False, "config")
    cases[0] += 1
    unknown = run(base_block(proposed_config_changes={"stack": {"kind": "python"}}))
    if not unknown["ok"] or not unknown["warnings"]:
        failures.append("an unrecognized config key should pass with a warning")

    # ── Limit 1: rubric changes are PRs ─────────────────────────────────────
    expect("a rubric change delivered as a PR passes",
           run(base_block(proposed_rubric_changes=[
               {"path": ".claude/skills/plan-epic/SKILL.md", "delivery": "pr",
                "rationale": "three scope findings in a row"}])),
           True)
    expect("a rubric change with no delivery mode is refused",
           run(base_block(proposed_rubric_changes=[
               {"path": ".claude/hooks/pre-tool-use.py", "rationale": "x"}])),
           False, "rubric")
    expect("a directly-applied rubric change is refused",
           run(base_block(proposed_rubric_changes=[
               {"path": ".claude/hooks/pre-tool-use.py", "delivery": "direct",
                "rationale": "x"}])),
           False, "rubric")
    expect("a rubric change claimed as already applied is refused",
           run(base_block(proposed_rubric_changes=[
               {"path": ".claude/skills/ship/SKILL.md", "delivery": "pr",
                "status": "applied", "rationale": "x"}])),
           False, "rubric")
    cases[0] += 1
    unguarded = run(base_block(proposed_rubric_changes=[
        {"path": "docs/rubric.md", "delivery": "pr", "rationale": "x"}]))
    if not unguarded["ok"] or not unguarded["warnings"]:
        failures.append("a rubric outside riskPaths should pass with a warning")

    # ── Limit 2: invented work is quarantined ───────────────────────────────
    expect("a retro-proposal ticket passes",
           run(base_block(proposed_tickets=[
               {"title": "Cache the token introspection call",
                "provenance": "retro-proposal", "state": "raw",
                "rationale": "three runs blew their turn budget here"}])),
           True)
    for prov in ("epic/ENG-100", "human", "monitor", "review", ""):
        expect("a proposal claiming provenance %r is refused" % (prov or "none"),
               run(base_block(proposed_tickets=[
                   {"title": "x", "provenance": prov, "state": "raw"}])),
               False, "proposals")
    expect("a proposal filed straight into ready is refused",
           run(base_block(proposed_tickets=[
               {"title": "x", "provenance": "retro-proposal", "state": "ready"}])),
           False, "proposals")
    expect("a proposal carrying a dispatcher label is refused",
           run(base_block(proposed_tickets=[
               {"title": "x", "provenance": "retro-proposal", "state": "raw",
                "labels": ["track:platform", "agent:queued"]}])),
           False, "proposals")
    expect("a proposal carrying provenance:epic is refused",
           run(base_block(proposed_tickets=[
               {"title": "x", "provenance": "retro-proposal", "state": "raw",
                "labels": ["provenance:epic"]}])),
           False, "proposals")
    expect("a titleless proposal is refused",
           run(base_block(proposed_tickets=[
               {"provenance": "retro-proposal", "state": "raw"}])),
           False, "proposals")

    # ── Several violations are all reported ─────────────────────────────────
    cases[0] += 1
    messy = run(base_block(
        proposed_config_changes={"budgets": {"maxTurns": 999, "wipLimit": 99}},
        proposed_rubric_changes=[{"path": ".claude/hooks/audit.py", "delivery": "direct"}],
        proposed_tickets=[{"title": "x", "provenance": "epic/ENG-1", "state": "ready"}]))
    if len({e["rule"] for e in messy["errors"]}) < 3:
        failures.append("a document breaking all three limits must report all three")

    if failures:
        print("FAIL: %d of %d weekly-review case(s) failed:" % (len(failures), cases[0]))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: %d weekly-review case(s) passed" % cases[0])
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", help="the review document (default: stdin)")
    ap.add_argument("--config", help="path to delivery.json (default: ./delivery.json)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    try:
        text = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    except OSError as e:
        print("FAIL: cannot read %s: %s" % (args.file, e), file=sys.stderr)
        return 2

    config_path = args.config or "delivery.json"
    config = None
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as fh:
                config = json.load(fh)
        except (OSError, ValueError) as e:
            print("FAIL: %s is present but unreadable: %s" % (config_path, e), file=sys.stderr)
            return 2
        if config.get("version") != SUPPORTED_VERSION:
            print("FAIL: %s declares version %r; this checker implements contract version %d"
                  % (config_path, config.get("version"), SUPPORTED_VERSION), file=sys.stderr)
            return 2

    result = check(text, config)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        for e in result["errors"]:
            print("error   [%s] %s" % (e["rule"], e["message"]))
        for w in result["warnings"]:
            print("warning [%s] %s" % (w["rule"], w["message"]))
        print("%d error(s), %d warning(s)%s"
              % (len(result["errors"]), len(result["warnings"]),
                 "" if result["ok"] else " — do not publish"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
