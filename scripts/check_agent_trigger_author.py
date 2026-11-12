#!/usr/bin/env python3
"""A comment-triggered agent workflow checks who wrote the comment (KIT-672).

    python3 scripts/check_agent_trigger_author.py            # check this repo's workflows and templates
    python3 scripts/check_agent_trigger_author.py --selftest # the battery CI runs

WHY. `templates/workflows/claude.yml` runs an agent with write permissions and the
repository's API key when a comment or issue body contains `@claude`. On a public
repository anyone can write that comment. Whether the action itself refuses a
non-writer is not something the template should rely on. So every job that runs
`anthropics/claude-code-action` from an `issue_comment`, `pull_request_review_comment`
or `issues` trigger must carry an `if:` that names the author_association of the
comment (issue_comment, review comment) and of the issue (issues), and the three
trusted values OWNER, MEMBER and COLLABORATOR.

Exit 0 = every such job checks its author. Exit 1 = one does not (file and job named).
Exit 2 = nothing could be checked, which is never a pass. Needs PyYAML (CI installs it).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FOLDERS = (".github/workflows", "templates/workflows")
ACTION = "anthropics/claude-code-action"
TRIGGERS = ("issue_comment", "pull_request_review_comment", "issues")
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
# The association field each trigger's event payload carries.
FIELD = {"issue_comment": "github.event.comment.author_association",
         "pull_request_review_comment": "github.event.comment.author_association",
         "issues": "github.event.issue.author_association"}


def _triggers(doc):
    on = doc.get("on", doc.get(True))      # YAML 1.1 reads a bare `on` as True
    if isinstance(on, str):
        return [on]
    if isinstance(on, list):
        return [str(x) for x in on]
    return [str(k) for k in on] if isinstance(on, dict) else []


def unchecked(text):
    """[(job, why)] for each agent job that does not check the author of what triggers it."""
    import yaml
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return []
    fired = [t for t in _triggers(doc) if t in TRIGGERS]
    found = []
    for name, job in sorted((doc.get("jobs") or {}).items()):
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") or []
        if not any(isinstance(s, dict) and str(s.get("uses", "")).startswith(ACTION) for s in steps):
            continue
        cond = str(job.get("if", ""))
        for field in sorted({FIELD[t] for t in fired}):
            if field not in cond:
                found.append((name, "`if:` never reads %s" % field))
        for role in TRUSTED:
            if fired and role not in cond:
                found.append((name, "`if:` does not name %s" % role))
    return found


def check(root):
    problems, seen = [], 0
    for folder in FOLDERS:
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not name.endswith((".yml", ".yaml")):
                continue
            seen += 1
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                for job, why in unchecked(fh.read()):
                    problems.append(f"{folder}/{name} (job {job}): {why}")
    return problems, seen


def verdict(root):
    problems, seen = check(root)
    if not seen:
        return 2, ["COULD NOT CHECK: no workflow files found; this is not a pass"]
    if problems:
        return 1, ["FAIL: " + p for p in problems]
    return 0, [f"OK: {seen} workflow file(s), every agent trigger checks its author"]


def selftest():
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    roles = "contains(fromJSON('[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]'), %s)"
    good = ("(github.event_name != 'issues' && " + roles % "github.event.comment.author_association"
            + ") || (" + roles % "github.event.issue.author_association" + ")")

    def wf(cond, on="[issue_comment, issues]", uses=ACTION + "@v1"):
        iff = "    if: '%s'\n" % cond.replace("'", "''") if cond is not None else ""
        return ("on: %s\njobs:\n  c:\n%s    runs-on: ubuntu-24.04\n    steps:\n      - uses: %s\n"
                % (on, iff, uses))
    cases = (
        (wf(good), 0, "a job that checks both fields and all three roles passes"),
        (wf(None), 5, "no if: at all fails"),
        (wf("contains(github.event.comment.body, '@claude')"), 5, "a mention-only if: fails"),
        (wf(good.replace("github.event.issue.author_association", "x")), 1, "the issue unchecked fails"),
        (wf(good.replace("github.event.comment.author_association", "x")), 1, "the comment unchecked fails"),
        (wf(good.replace("COLLABORATOR", "x")), 1, "a missing trusted role fails"),
        (wf(None, on="[push]"), 0, "a non-comment trigger is not this check's business"),
        (wf(None, uses="actions/checkout@v4"), 0, "a job without the agent action is ignored"),
    )
    for text, want, msg in cases:
        got = len(unchecked(text))
        expect(got == want, f"{msg}: found {got}, want {want}")
    real_problems, real_seen = check(ROOT)
    expect(real_seen >= 10, f"found only {real_seen} workflow files in {ROOT}")
    expect(not real_problems, "this repo has an unchecked agent trigger: " + "; ".join(real_problems))
    with open(os.path.join(ROOT, "templates/workflows/claude.yml"), encoding="utf-8") as fh:
        expect(ACTION in fh.read(), "the template this check exists for must still be there")
    if failures:
        print("check_agent_trigger_author selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print(f"check_agent_trigger_author selftest: OK ({len(cases)} cases)")
    return 0


def main(argv):
    try:
        if "--selftest" in argv:
            return selftest()
        code, lines = verdict(ROOT)
    except ImportError:
        print("FAIL: PyYAML is required (python3 -m pip install pyyaml)")
        return 2
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
