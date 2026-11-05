#!/usr/bin/env python3
"""Runner pin check — no workflow runs on a moving `-latest` runner image (KIT-429).

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
# `runs-on: ubuntu-latest`, `runs-on: [self-hosted, macos-latest]`, `runs-on: "windows-latest"`.
# A trailing comment is not part of the value. A commented-out line is not a runner.
RUNS_ON = re.compile(r"^\s*(?:-\s*)?runs-on\s*:\s*(?P<value>[^#\n]*)")
LATEST = re.compile(r"-latest\b")


def unpinned(text):
    """[(line number, value)] for each active `runs-on` that names a `-latest` image."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        m = RUNS_ON.match(line)
        if m and LATEST.search(m.group("value")):
            found.append((n, m.group("value").strip()))
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
                for n, value in unpinned(fh.read()):
                    problems.append(f"{rel}:{n}: runs-on: {value} moves under every open PR; "
                                    "pin an image such as ubuntu-24.04")
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

    for text, want in (
        ("jobs:\n  a:\n    runs-on: ubuntu-latest\n", 1),
        ("jobs:\n  a:\n    runs-on: ubuntu-24.04\n", 0),
        ("jobs:\n  a:\n    runs-on: [self-hosted, macos-latest]\n", 1),
        ("jobs:\n  a:\n    runs-on: \"windows-latest\"\n", 1),
        ("jobs:\n  a:\n    runs-on: ubuntu-24.04 # not ubuntu-latest\n", 0),
        ("# runs-on: ubuntu-latest\njobs: {}\n", 0),
        ("  #   runs-on: ubuntu-latest\n", 0),
        ("jobs:\n  a:\n    runs-on: ubuntu-latest\n  b:\n    runs-on: ubuntu-latest\n", 2),
        ("jobs:\n  a:\n    runs-on: ubuntu-22.04\n    name: not-latest-at-all\n", 0),
    ):
        got = len(unpinned(text))
        expect(got == want, f"{text!r}: found {got}, want {want}")
    with tempfile.TemporaryDirectory(prefix="runner-pin-") as tmp:
        for folder in FOLDERS:
            os.makedirs(os.path.join(tmp, folder))
        for rel, body in ((".github/workflows/a.yml", "runs-on: ubuntu-24.04\n"),
                          ("templates/workflows/b.yml", "runs-on: ubuntu-latest\n"),
                          (NEWEST_IMAGE[0], "runs-on: ubuntu-latest\n")):
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                fh.write(body)
        problems, seen = check(tmp)
        expect(seen == 3 and len(problems) == 1 and "templates/workflows/b.yml:1" in problems[0],
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
    code, lines = verdict(ROOT)
    for line in lines:
        print(line, file=sys.stderr if code == 2 else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
