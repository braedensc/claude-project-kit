#!/usr/bin/env python3
"""The label gate runs the default branch's code, never the pull request's (KIT-668).

    python3 scripts/check_label_gate.py            # check .github/workflows/hooks-change-guard.yml
    python3 scripts/check_label_gate.py --selftest # the battery CI runs

WHY. The label gate ("Hooks change guard") is the check that makes a change to the guard machinery
visible: a pull request that touches it stays red until a person adds the `hooks-change` label. It
ran on `pull_request`, which takes the workflow file from the pull request itself, so a pull request
that rewrote the gate's workflow turned its own check green. It now runs on `pull_request_target`,
which takes the workflow from the default branch. This file pins that shape, so an edit cannot
quietly undo it. The workflow's own comment says what the shape buys and what it needs.

WHAT IS CHECKED. Each rule has its own closed reason.
  trigger-not-target       it runs on `pull_request_target`, for the five pull request types
  trigger-head-unfiltered  `pull_request` appears only filtered to this one file, for those types:
                           a pull request that edits the gate runs its own copy too, and the default
                           branch's copy still reports on the same commit
  trigger-other            no other event starts it
  job-shape                one job, named "Hooks change guard", with no `if:` (a skipped job
                           reports success)
  permissions              the token can only read: `contents` for the workflow; `contents`,
                           `issues` and `pull-requests` for the job
  checkout-not-default     the only action is actions/checkout, of the default branch, from this
                           repository
  head-on-disk             no step puts a commit on disk (git checkout, switch, worktree, reset,
                           restore, read-tree, merge, rebase, cherry-pick, apply, am, stash)
  gate-not-base            the gate script is the default branch's revision, from
                           `git show "$BASE_SHA:scripts/check_grader_paths.py"`, and no working-tree
                           copy of it is run or copied
  head-not-event           the head commit is the event's own `pull_request.head.sha`
  secret-other             no secret but the job's own GITHUB_TOKEN

Exit 0 = the shape holds. Exit 1 = it does not (each reason named). Exit 2 = nothing could be
checked (no PyYAML, no file, a file that does not parse): never a pass. Needs PyYAML (CI installs it).
"""
import copy
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKFLOW = ".github/workflows/hooks-change-guard.yml"
JOB_NAME = "Hooks change guard"
PR_TYPES = ("opened", "synchronize", "reopened", "labeled", "unlabeled")
TOP_PERMISSIONS = {"contents": "read"}
JOB_PERMISSIONS = {"contents": "read", "issues": "read", "pull-requests": "read"}
DEFAULT_REF = "${{ github.event.repository.default_branch }}"
HEAD_SHA_EXPR = "${{ github.event.pull_request.head.sha }}"
GATE_FROM_BASE = 'git show "$BASE_SHA:scripts/check_grader_paths.py"'
WORKING_COPY = "scripts/check_grader_paths.py"
ON_DISK_RE = re.compile(r"\bgit\b[^\n;&|]*?\s(checkout|switch|worktree|reset|restore|read-tree|merge|"
                        r"rebase|cherry-pick|apply|am|stash)\b")
SECRET_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
REASONS = ("trigger-not-target", "trigger-head-unfiltered", "trigger-other", "job-shape",
           "permissions", "checkout-not-default", "head-on-disk", "gate-not-base",
           "head-not-event", "secret-other")


def _triggers(doc):
    on = doc.get("on", doc.get(True))      # YAML 1.1 reads a bare `on` as True
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return dict((e, None) for e in on if isinstance(e, str))
    return on if isinstance(on, dict) else {}


def _types_ok(spec):
    types = spec.get("types") if isinstance(spec, dict) else None
    return isinstance(types, list) and all(t in types for t in PR_TYPES)


def _strings(value):
    """Every string inside a parsed YAML value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _commands(run):
    """A run script without its comment lines: a comment may name a command it never runs."""
    return "\n".join(ln for ln in str(run).split("\n") if not ln.strip().startswith("#"))


def _default_checkout(step):
    w = step.get("with") if isinstance(step.get("with"), dict) else {}
    return (str(step.get("uses", "")).startswith("actions/checkout@") and w.get("ref") == DEFAULT_REF
            and "repository" not in w)


def judge(doc):
    """The reasons the parsed workflow `doc` breaks the gate's shape, in REASONS order; [] when it
    holds. Something that is not a workflow at all is ["unreadable"]."""
    if not isinstance(doc, dict):
        return ["unreadable"]
    found = set()
    on = _triggers(doc)
    if not _types_ok(on.get("pull_request_target")):
        found.add("trigger-not-target")
    if "pull_request" in on:
        pr = on.get("pull_request")
        if not (isinstance(pr, dict) and _types_ok(pr) and pr.get("paths") == [WORKFLOW]
                and set(pr) <= {"types", "paths"}):
            found.add("trigger-head-unfiltered")
    if set(on) - {"pull_request_target", "pull_request"}:
        found.add("trigger-other")
    jobs = doc.get("jobs") if isinstance(doc.get("jobs"), dict) else {}
    named = [j for j in jobs.values() if isinstance(j, dict) and j.get("name") == JOB_NAME]
    only = [j for j in jobs.values() if isinstance(j, dict)] if len(jobs) == 1 else []
    job = named[0] if len(named) == 1 else (only[0] if only else {})
    if len(jobs) != 1 or len(named) != 1 or "if" in job:
        found.add("job-shape")
    if doc.get("permissions") != TOP_PERMISSIONS or job.get("permissions") != JOB_PERMISSIONS:
        found.add("permissions")
    steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
    actions = [s for s in steps if "uses" in s]
    if not actions or not all(_default_checkout(s) for s in actions):
        found.add("checkout-not-default")
    runs = [s for s in steps if "run" in s]
    commands = "\n".join(_commands(s.get("run")) for s in runs)
    if ON_DISK_RE.search(commands):
        found.add("head-on-disk")
    if GATE_FROM_BASE not in commands or WORKING_COPY in commands.replace(GATE_FROM_BASE, ""):
        found.add("gate-not-base")
    heads = [s["env"]["HEAD_SHA"] for s in runs if isinstance(s.get("env"), dict) and "HEAD_SHA" in s["env"]]
    if not heads or any(h != HEAD_SHA_EXPR for h in heads):
        found.add("head-not-event")
    if set(SECRET_RE.findall("\n".join(_strings(doc)))) - {"GITHUB_TOKEN"}:
        found.add("secret-other")
    return [r for r in REASONS if r in found]


def check_repo(root=ROOT):
    """(exit code, lines) for the repository at `root`."""
    try:
        import yaml
    except ImportError:
        return 2, ["COULD NOT CHECK: PyYAML is not installed (pip install pyyaml); this is not a pass"]
    try:
        with open(os.path.join(root, WORKFLOW), encoding="utf-8") as fh:
            doc = yaml.safe_load(fh.read())
    except OSError as exc:
        return 2, ["COULD NOT CHECK: %s cannot be read (%s); this is not a pass" % (WORKFLOW, exc.strerror)]
    except yaml.YAMLError:
        return 2, ["COULD NOT CHECK: %s does not parse as YAML; this is not a pass" % WORKFLOW]
    reasons = judge(doc)
    if reasons == ["unreadable"]:
        return 2, ["COULD NOT CHECK: %s is not a workflow; this is not a pass" % WORKFLOW]
    if reasons:
        return 1, ["%s: %s" % (WORKFLOW, r) for r in reasons] + [
            "The label gate must run the default branch's workflow and code, never the PR's "
            "(scripts/check_label_gate.py says what each reason means)."]
    return 0, ["%s: the label gate runs the default branch's workflow and code" % WORKFLOW]


def selftest():
    import yaml
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, got, want))

    with open(os.path.join(ROOT, WORKFLOW), encoding="utf-8") as fh:
        real = yaml.safe_load(fh.read())
    check("the kit's own workflow holds the shape", judge(real), [])

    def job(doc):
        return doc["jobs"]["hooks-change-guard"]

    def run_step(doc):
        return [s for s in job(doc)["steps"] if "run" in s][0]

    def mutate(fn):
        doc = copy.deepcopy(real)
        fn(doc)
        return judge(doc)

    def on(doc):
        return doc["on"] if "on" in doc else doc[True]

    def append_run(text):
        return lambda d: run_step(d).__setitem__("run", run_step(d)["run"] + text)

    cases = (
        # The trigger: the default branch's copy judges every pull request.
        ("the old trigger: pull_request alone, unfiltered",
         lambda d: d.__setitem__(True if True in d else "on",
                                 {"pull_request": {"types": list(PR_TYPES)}}),
         ["trigger-not-target", "trigger-head-unfiltered"]),
        ("pull_request_target without the labeled type, so a label re-judges nothing",
         lambda d: on(d)["pull_request_target"].__setitem__("types", ["opened", "synchronize"]),
         ["trigger-not-target"]),
        ("pull_request with no paths filter, so every PR runs its own copy",
         lambda d: on(d)["pull_request"].pop("paths"), ["trigger-head-unfiltered"]),
        ("pull_request filtered to another file",
         lambda d: on(d)["pull_request"].__setitem__("paths", [".github/workflows/ci.yml"]),
         ["trigger-head-unfiltered"]),
        ("pull_request filtered to this file plus a branch filter",
         lambda d: on(d)["pull_request"].__setitem__("branches", ["main"]),
         ["trigger-head-unfiltered"]),
        ("a manual start, which runs the copy on whatever ref it names",
         lambda d: on(d).__setitem__("workflow_dispatch", None), ["trigger-other"]),
        ("a push trigger", lambda d: on(d).__setitem__("push", {"branches": ["main"]}),
         ["trigger-other"]),
        # The job.
        ("a job-level if:, which reports a skipped job as success",
         lambda d: job(d).__setitem__("if", "github.event_name == 'pull_request'"), ["job-shape"]),
        ("the job renamed, so the required check never reports",
         lambda d: job(d).__setitem__("name", "Label gate"), ["job-shape"]),
        ("a second job beside it",
         lambda d: d["jobs"].__setitem__("other", {"runs-on": "ubuntu-24.04", "steps": []}),
         ["job-shape"]),
        # The token reads only.
        ("a job token that can write",
         lambda d: job(d)["permissions"].__setitem__("pull-requests", "write"), ["permissions"]),
        ("a job token that gains a scope",
         lambda d: job(d)["permissions"].__setitem__("actions", "read"), ["permissions"]),
        ("a workflow token that can write",
         lambda d: d["permissions"].__setitem__("contents", "write"), ["permissions"]),
        # Nothing the pull request wrote is ever on disk.
        ("checkout with no ref, which on pull_request is the PR's merge commit",
         lambda d: job(d)["steps"][0]["with"].pop("ref"), ["checkout-not-default"]),
        ("checkout of the PR's head",
         lambda d: job(d)["steps"][0]["with"].__setitem__("ref", HEAD_SHA_EXPR),
         ["checkout-not-default"]),
        ("checkout of another repository",
         lambda d: job(d)["steps"][0]["with"].__setitem__("repository", "someone/fork"),
         ["checkout-not-default"]),
        ("a second action in the job",
         lambda d: job(d)["steps"].insert(1, {"uses": "someone/setup@v1"}), ["checkout-not-default"]),
        ("git checkout of the head", append_run('\ngit checkout "$HEAD_SHA"\n'), ["head-on-disk"]),
        ("a worktree of the head", append_run('\ngit worktree add "$RUNNER_TEMP/h" "$HEAD_SHA"\n'),
         ["head-on-disk"]),
        ("git -c ... switch to the head", append_run('\ngit -c advice.x=false switch --detach "$HEAD_SHA"\n'),
         ["head-on-disk"]),
        ("a reset onto the head", append_run('\ngit reset --hard "$HEAD_SHA"\n'), ["head-on-disk"]),
        ("a comment that names git checkout runs nothing",
         append_run("\n# never git checkout the head here\n"), []),
        # The gate script is the default branch's revision.
        ("the old fallback that copied the working tree's gate",
         append_run('\ncp scripts/check_grader_paths.py "$gate"\n'), ["gate-not-base"]),
        ("the working tree's gate run directly",
         append_run("\npython3 scripts/check_grader_paths.py\n"), ["gate-not-base"]),
        ("the gate taken from the head instead of the base",
         lambda d: run_step(d).__setitem__("run", run_step(d)["run"].replace(
             GATE_FROM_BASE, 'git show "$HEAD_SHA:scripts/check_grader_paths.py"')), ["gate-not-base"]),
        # The head commit is the event's own.
        ("a head from the workflow's own sha",
         lambda d: run_step(d)["env"].__setitem__("HEAD_SHA", "${{ github.sha }}"), ["head-not-event"]),
        ("no head at all", lambda d: run_step(d)["env"].pop("HEAD_SHA"), ["head-not-event"]),
        # Secrets.
        ("a secret beyond the job's token",
         lambda d: run_step(d)["env"].__setitem__("KEY", "${{ secrets.ANTHROPIC_API_KEY }}"),
         ["secret-other"]),
    )
    for label, fn, want in cases:
        try:
            got = mutate(fn)
        except Exception as exc:      # noqa: BLE001  (a case that cannot apply is a failure, named)
            got = "case could not apply: %r" % exc
        check(label, got, want)
    check("something that is not a workflow is unreadable", (judge(None), judge([]), judge("on")),
          (["unreadable"], ["unreadable"], ["unreadable"]))
    check("a list of triggers is read as triggers",
          judge(dict(copy.deepcopy(real), on=["pull_request_target"])) != [], True)
    check("every reason this battery names is one judge() can give",
          sorted(set(r for _l, _f, w in cases for r in w)), sorted(REASONS))

    rc, lines = check_repo()
    check("the repository's own check passes and says so", (rc, lines[0].endswith("code")), (0, True))
    rc, lines = check_repo(os.path.join(ROOT, "no-such-folder"))
    check("a workflow that cannot be read is COULD NOT CHECK, never a pass",
          (rc, lines[0].startswith("COULD NOT CHECK")), (2, True))

    if failures:
        print("\n".join(failures))
        print("check_label_gate selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: check_label_gate selftest (%d checks)" % total[0])
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        try:
            import yaml  # noqa: F401
        except ImportError:
            print("COULD NOT CHECK: PyYAML is not installed (pip install pyyaml); this is not a pass",
                  file=sys.stderr)
            return 2
        return selftest()
    if argv:
        print(__doc__.split("\n\n", 2)[1], file=sys.stderr)
        return 2
    rc, lines = check_repo()
    print("\n".join(lines), file=sys.stderr if rc else sys.stdout)
    return rc


if __name__ == "__main__":
    sys.exit(main())
