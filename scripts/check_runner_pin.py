#!/usr/bin/env python3
"""Runner pin check — no workflow runs on a moving `-latest` runner image (KIT-429).

Needs PyYAML (CI installs it); the workflows are parsed, not pattern-matched.

    python3 scripts/check_runner_pin.py            # check this repo's workflows and templates
    python3 scripts/check_runner_pin.py --selftest # the battery CI runs

WHY. On 3-4 Nov 2026 GitHub moved `ubuntu-latest` from 24.04 to 26.04. The new image has a
newer Python, and two selftests broke on it. Every open PR whose job landed on the new image
went red at once, though none had changed anything those tests cover, and each session began
debugging a failure that was not its own. Pinning the image makes an upgrade a choice made in
a PR (docs/TESTING.md, "Moving the runner pin").

THE ONE EXCEPTION is `.github/workflows/ci-newest-image.yml`: its whole job is to run on the
newest image, as a check nobody requires, so a coming break shows there first.

Exit 0 = every `runs-on` is pinned. Exit 1 = one is not (the file and line are named).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FOLDERS = (".github/workflows", "templates/workflows")
# Files whose purpose is to run on the newest image.
NEWEST_IMAGE = (".github/workflows/ci-newest-image.yml",)
LATEST = re.compile(r"-latest\b")


def _labels(value):
    """The label strings a `runs-on` value names: a string, a list, or {group:, labels:}."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [x for v in value for x in _labels(v)]
    if isinstance(value, dict):
        return _labels(value.get("labels")) + _labels(value.get("group"))
    return []


def unpinned(text):
    """[(job, why)] for each job whose `runs-on` is not a literal pinned image. The workflow is
    PARSED, so a list, a `labels:` form, a comment and a trailing comment are read as YAML reads
    them. A `${{ }}` expression cannot be checked from here and is refused: write the image."""
    import yaml
    doc = yaml.safe_load(text) or {}
    found = []
    for name, job in sorted(((doc.get("jobs") if isinstance(doc, dict) else None) or {}).items()):
        if not isinstance(job, dict) or "runs-on" not in job:
            continue                          # a reusable-workflow call has no runs-on
        value = job["runs-on"]
        labels = _labels(value)
        if not labels:
            found.append((name, "runs-on names no image"))
        for label in labels:
            if "${{" in label:
                found.append((name, "runs-on %r is an expression, so the image cannot be checked: "
                                    "write it literally" % label))
            elif LATEST.search(label):
                found.append((name, "runs-on: %s moves under every open PR" % label))
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
            rel = f"{folder}/{name}"
            seen += 1
            if rel in NEWEST_IMAGE:
                continue
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                for job, why in unpinned(fh.read()):
                    problems.append(f"{rel} (job {job}): {why}; pin an image such as ubuntu-24.04")
    return problems, seen


def verdict(root):
    """(exit code, lines). No workflow files at all is its own answer, never a pass."""
    problems, seen = check(root)
    if not seen:
        return 2, ["COULD NOT CHECK: no workflow files found; this is not a pass"]
    if problems:
        return 1, ["FAIL: " + p for p in problems]
    return 0, [f"OK: {seen} workflow file(s), every runner pinned"]


def selftest():
    import tempfile
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    def wf(runs_on, extra=""):
        return "jobs:\n  a:\n    runs-on: %s\n%s    steps: []\n" % (runs_on, extra)
    for text, want in (
        (wf("ubuntu-latest"), 1),
        (wf("ubuntu-24.04"), 0),
        (wf("[self-hosted, macos-latest]"), 1),
        (wf('"windows-latest"'), 1),
        (wf("ubuntu-24.04 # not ubuntu-latest"), 0),
        ("# runs-on: ubuntu-latest\njobs: {}\n", 0),
        ("jobs:\n  a:\n    # runs-on: ubuntu-latest\n    runs-on: ubuntu-24.04\n    steps: []\n", 0),
        ("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n", 2),
        (wf("ubuntu-22.04", "    name: not-latest-at-all\n"), 0),
        # the forms a line-by-line reading missed:
        ("jobs:\n  a:\n    runs-on:\n      - ubuntu-latest\n    steps: []\n", 1),
        ("jobs:\n  a:\n    runs-on:\n      labels: ubuntu-latest\n    steps: []\n", 1),
        ("jobs:\n  a:\n    runs-on:\n      labels: ubuntu-24.04\n    steps: []\n", 0),
        ("jobs:\n  a:\n    runs-on:\n      - ubuntu-24.04\n    steps: []\n", 0),
        ("jobs:\n  a:\n    runs-on:\n      group: big\n      labels: [ubuntu-latest]\n    steps: []\n", 1),
        ("jobs:\n  a:\n    strategy: {matrix: {os: [ubuntu-latest]}}\n    runs-on: ${{ matrix.os }}\n    steps: []\n", 1),
        ("jobs:\n  a:\n    runs-on:\n    steps: []\n", 1),
        ("jobs:\n  a:\n    uses: ./.github/workflows/x.yml\n", 0),
    ):
        got = len(unpinned(text))
        expect(got == want, f"{text!r}: found {got}, want {want}")
    with tempfile.TemporaryDirectory(prefix="runner-pin-") as tmp:
        for folder in FOLDERS:
            os.makedirs(os.path.join(tmp, folder))
        for rel, body in ((".github/workflows/a.yml", wf("ubuntu-24.04")),
                          ("templates/workflows/b.yml", wf("ubuntu-latest")),
                          (NEWEST_IMAGE[0], wf("ubuntu-latest"))):
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                fh.write(body)
        problems, seen = check(tmp)
        expect(seen == 3 and len(problems) == 1 and "templates/workflows/b.yml" in problems[0],
               f"a template on -latest is named, the newest-image workflow is allowed: {problems} ({seen})")
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        expect(verdict(empty)[0] == 2, "a folder with no workflow files must be 'could not check', not a pass")
        expect(verdict(tmp)[0] == 1 and verdict(ROOT)[0] == 0, "a failing tree exits 1, this repo exits 0")
        # The check reads this repo's workflows, so it must find some, and none may move.
        real_problems, real_seen = check(ROOT)
        expect(real_seen >= 10, f"found only {real_seen} workflow files in {ROOT}")
        expect(not real_problems, "this repo has an unpinned runner: " + "; ".join(real_problems))
    if failures:
        print("check_runner_pin selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("check_runner_pin selftest: OK")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        sys.path.insert(0, HERE)
        from scratch_run import contained   # its temp folders go when it ends
        return contained(selftest, "runner-pin-selftest-run-")
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("COULD NOT CHECK: PyYAML is not installed (pip install pyyaml); this is not a pass", file=sys.stderr)
        return 2
    code, lines = verdict(ROOT)
    for line in lines:
        print(line, file=sys.stderr if code == 2 else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
