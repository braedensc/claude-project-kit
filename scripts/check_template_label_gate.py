#!/usr/bin/env python3
"""The stack template's label gate runs from the default branch, never the PR's checkout (KIT-788).

    python3 scripts/check_template_label_gate.py            # check templates/workflows/hooks-change-guard.yml
    python3 scripts/check_template_label_gate.py --selftest # the battery CI runs

WHY. Every new project copies templates/workflows/. Its label gate ("Hooks change guard") used to be a
job in the app CI template, which runs on `pull_request` and so takes its workflow from the pull request
itself: a PR that rewrote the gate turned its own check green. The gate now has a workflow of its own
that runs on `pull_request_target`, which takes the workflow from the default branch. This file pins
that shape, so an edit cannot quietly undo it. It is the template's twin of the kit's own pin for
.github/workflows/hooks-change-guard.yml, and it holds the template to the same rules: the PR's code is
never on disk and never runs, the token can only read, and no secret is used.

WHAT IS CHECKED. Each rule has its own closed reason.
  gate-in-ci               templates/workflows/ci.yml holds no copy of the gate (a second copy would run
                           from the PR and report the same required context)
  trigger-not-target       it runs on `pull_request_target`, for the five pull request types
  trigger-head-unfiltered  `pull_request` appears only filtered to this one file: a PR that edits the gate
                           runs its own copy too, and the default branch's copy still reports
  trigger-other            no other event starts it
  job-shape                one job, named "Hooks change guard", with no `if:` (a skipped job reports success)
  permissions              the token can only read (`contents: read`), for the workflow and the job
  checkout-not-default     the only action is actions/checkout, of the default branch, from this repository
  head-on-disk             nothing the PR wrote enters the job. Git may run only rev-parse, fetch,
                           `cat-file -e` and diff, an allow list; and no path is read out of the head
  head-not-event           the head commit is the event's own `pull_request.head.sha`
  secret-used              no secret at all

Exit 0 = the shape holds. Exit 1 = it does not (each reason named). Exit 2 = nothing could be checked
(no PyYAML, no file, a file that does not parse): never a pass. Needs PyYAML (CI installs it).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKFLOW = "templates/workflows/hooks-change-guard.yml"
APP_CI = "templates/workflows/ci.yml"
OWN_PATH = ".github/workflows/hooks-change-guard.yml"
JOB_NAME = "Hooks change guard"
PR_TYPES = ("opened", "synchronize", "reopened", "labeled", "unlabeled")
DEFAULT_REF = "${{ github.event.repository.default_branch }}"
HEAD_SHA_EXPR = "${{ github.event.pull_request.head.sha }}"
GIT_RE = re.compile(r"\bgit((?:\s+-[cC]\s+\S+|\s+--?[A-Za-z][\w-]*(?:=\S+)?)*)\s+([a-z][a-z-]*)(\s+-e\b)?")
GIT_ALLOWED = ("rev-parse", "fetch", "diff")      # plus `cat-file -e`
HEAD_PATH_RE = re.compile(r'\$\{?HEAD_SHA\}?"?:')   # a path read out of the head: "$HEAD_SHA:x"
SECRET_RE = re.compile(r"secrets\.|GITHUB_TOKEN")
REASONS = ("gate-in-ci", "trigger-not-target", "trigger-head-unfiltered", "trigger-other", "job-shape",
           "permissions", "checkout-not-default", "head-on-disk", "head-not-event", "secret-used")


def _triggers(doc):
    on = doc.get("on", doc.get(True))      # YAML 1.1 reads a bare `on` as True
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return dict((e, None) for e in on if isinstance(e, str))
    return on if isinstance(on, dict) else {}


def _commands(run):
    """A run script without its comment lines: a comment may name a command it never runs."""
    return "\n".join(ln for ln in str(run).split("\n") if not ln.strip().startswith("#"))


def _head_enters(commands):
    if HEAD_PATH_RE.search(commands):
        return True
    for m in GIT_RE.finditer(commands):
        sub, existence_test = m.group(2), m.group(3)
        if sub in GIT_ALLOWED or (sub == "cat-file" and existence_test):
            continue
        return True
    return False


def reasons(text, ci_text):
    """The closed reasons the gate workflow `text` breaks. `ci_text` is the app CI template."""
    import yaml
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("the workflow is not a mapping")
    out = []
    if "hooks-change-guard:" in ci_text or "Hooks change guard\n    runs-on" in ci_text:
        out.append("gate-in-ci")
    trig = _triggers(doc)
    spec = trig.get("pull_request_target")
    types = spec.get("types") if isinstance(spec, dict) else None
    if not (isinstance(types, list) and all(t in types for t in PR_TYPES)):
        out.append("trigger-not-target")
    head = trig.get("pull_request", False)
    if head is not False:
        ok = (isinstance(head, dict) and head.get("paths") == [OWN_PATH]
              and all(t in (head.get("types") or []) for t in PR_TYPES)
              and set(head) <= {"types", "paths"})
        if not ok:
            out.append("trigger-head-unfiltered")
    if set(trig) - {"pull_request_target", "pull_request"}:
        out.append("trigger-other")
    jobs = doc.get("jobs") if isinstance(doc.get("jobs"), dict) else {}
    job = next(iter(jobs.values())) if len(jobs) == 1 else None
    if not isinstance(job, dict) or job.get("name") != JOB_NAME or "if" in job or "concurrency" in job:
        out.append("job-shape")
        job = job if isinstance(job, dict) else {}
    if doc.get("permissions") != {"contents": "read"} or job.get("permissions", {"contents": "read"}) != {"contents": "read"}:
        out.append("permissions")
    steps = job.get("steps") if isinstance(job.get("steps"), list) else []
    uses = [s for s in steps if isinstance(s, dict) and "uses" in s]
    if not (len(uses) == 1 and str(uses[0]["uses"]).startswith("actions/checkout@")
            and (uses[0].get("with") or {}).get("ref") == DEFAULT_REF
            and not ({"repository", "token", "path", "submodules"} & set(uses[0].get("with") or {}))):
        out.append("checkout-not-default")
    runs = [_commands(s.get("run")) for s in steps if isinstance(s, dict) and "run" in s]
    if any(_head_enters(r) for r in runs):
        out.append("head-on-disk")
    envs = [s.get("env") or {} for s in steps if isinstance(s, dict)]
    if not envs or any(e.get("HEAD_SHA") != HEAD_SHA_EXPR for e in envs if "HEAD_SHA" in e) \
            or not any("HEAD_SHA" in e for e in envs):
        out.append("head-not-event")
    if any(SECRET_RE.search(ln) for ln in text.split("\n") if not ln.strip().startswith("#")):
        out.append("secret-used")
    return out


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def selftest():
    failures = []
    real = _read(WORKFLOW)
    ci = _read(APP_CI)
    got = reasons(real, ci)
    if got:
        failures.append("the real template breaks: %s" % got)

    def mutant(old, new, want, msg, ci_text=ci):
        if old not in real:
            failures.append("%s: the template no longer holds %r, so this case proves nothing" % (msg, old))
            return
        found = reasons(real.replace(old, new, 1), ci_text)
        if want not in found:
            failures.append("%s: wanted %s, got %s" % (msg, want, found))

    mutant("  pull_request_target:\n    types: [opened, synchronize, reopened, labeled, unlabeled]\n  pull_request:",
           "  pull_request:", "trigger-not-target", "a gate on pull_request only runs from the PR")
    mutant("    paths:\n      - .github/workflows/hooks-change-guard.yml\n", "",
           "trigger-head-unfiltered", "an unfiltered pull_request copy runs on every PR")
    mutant("    paths:\n      - .github/workflows/hooks-change-guard.yml\n", "    paths:\n      - '**'\n",
           "trigger-head-unfiltered", "a pull_request copy filtered to everything")
    mutant("permissions:\n  contents: read\n\njobs:", "permissions:\n  contents: read\n  pull-requests: write\n\njobs:",
           "permissions", "a write permission on the token")
    mutant("          ref: ${{ github.event.repository.default_branch }}\n", "",
           "checkout-not-default", "a checkout with no ref takes the PR's merge commit")
    mutant("          ref: ${{ github.event.repository.default_branch }}\n",
           "          ref: ${{ github.event.pull_request.head.sha }}\n",
           "checkout-not-default", "a checkout of the PR's head")
    mutant("    runs-on: ubuntu-24.04\n", "    runs-on: ubuntu-24.04\n    if: github.event_name == 'pull_request'\n",
           "job-shape", "a job-level if: can skip the job, and a skipped job reports success")
    mutant("    name: Hooks change guard\n", "    name: Label gate\n", "job-shape", "a renamed job is a different required context")
    mutant("          set -euo pipefail\n", "          set -euo pipefail\n          git checkout \"$HEAD_SHA\"\n",
           "head-on-disk", "a checkout of the head puts the PR's files on disk")
    mutant("          set -euo pipefail\n", "          set -euo pipefail\n          git show \"$HEAD_SHA:scripts/x.sh\" | sh\n",
           "head-on-disk", "a path read out of the head")
    mutant("HEAD_SHA: ${{ github.event.pull_request.head.sha }}", "HEAD_SHA: ${{ github.event.pull_request.title }}",
           "head-not-event", "a head commit the PR's author names")
    mutant("          LABELS:", "          TOKEN: ${{ secrets.DEPLOY_KEY }}\n          LABELS:", "secret-used",
           "a secret in a job that sees the PR")
    mutant("  pull_request:\n    types", "  push:\n    branches: [main]\n  pull_request:\n    types",
           "trigger-other", "an extra trigger")
    found = reasons(real, ci + "\n  hooks-change-guard:\n    name: Hooks change guard\n    runs-on: ubuntu-24.04\n")
    if "gate-in-ci" not in found:
        failures.append("a second copy of the gate in the app CI template is not refused: %s" % found)
    # A comment that names a forbidden command is not a command.
    commented = real.replace("          set -euo pipefail\n", "          set -euo pipefail\n          # git checkout \"$HEAD_SHA\"\n", 1)
    if reasons(commented, ci):
        failures.append("a comment naming git checkout was read as a command")
    if failures:
        print("check_template_label_gate selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("check_template_label_gate selftest: OK")
    return 0


def main(argv):
    try:
        if "--selftest" in argv:
            return selftest()
        found = reasons(_read(WORKFLOW), _read(APP_CI))
    except ImportError:
        print("FAIL: PyYAML is required (python3 -m pip install pyyaml)")
        return 2
    except Exception as e:  # a file it cannot read or parse is never a pass
        print("COULD NOT CHECK: %s" % e)
        return 2
    if found:
        print("FAIL: %s runs from the pull request, or can run its code: %s" % (WORKFLOW, ", ".join(found)))
        return 1
    print("OK: %s runs from the default branch (pull_request_target), reads only, uses no secret" % WORKFLOW)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
