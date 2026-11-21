#!/usr/bin/env python3
"""CI-covers-every-battery check: each package.json `test:*` script has a CI step (KIT-620).

    python3 scripts/check_ci_covers_tests.py            # check this repo
    python3 scripts/check_ci_covers_tests.py --selftest # the battery CI runs

WHY. On 11 Nov 2026 a review found ten batteries that ran in CI only inside the temp-leak
sweeps. When the sweeps became their own job, nothing else ran those ten, and they would have
stopped blocking merges with no red check to say so. A battery nobody runs is the silent
nothing contract 13 warns about, so this fails the PR that adds a `test:*` script without a step.

THE RULE. A `test:*` script is covered when each `python3 ... .py ...` command it runs, with its
arguments, is run by a step of a job in `.github/workflows/ci.yml` other than the leak sweeps (the
same words, then the end of the line, `&&`, `;` or `||`), or when the workflow writes the script as
`npm run <script>`. A path in `git show` or `cp`, or the same script with other arguments, is not a
run of it. A `for m in a b; do python3 x_$m.py; done` loop is read once per word. The leak sweeps
do not count (the sweeps job and each of its shard jobs): they run every battery, which is what hid the gap.

THE ALLOW LIST is data: a script left out on purpose, with its reason. An entry for a script
that is covered anyway, or that is not in package.json, fails too, so the list cannot go stale.

Exit 0 = every script is covered. Exit 1 = one is not (the script is named). Exit 2 = could not
check (a file missing or unreadable, or no `test:*` script at all): never a pass.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CI = ".github/workflows/ci.yml"
SWEEP_JOB = "temp-leak-sweeps"

# script name -> one-line reason it has no CI step of its own.
ALLOWED = {}

PY = re.compile(r"[\w./$-]+\.py")
LOOP = re.compile(r"for\s+(\w+)\s+in\s+([^;]+);\s*do\s+(.*?);\s*done")
JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def ci_job_texts(text):
    """{job id: its text, comment lines dropped} for each job except the leak sweeps (the sweeps job and
    every shard of it: an id that starts with SWEEP_JOB). A comment naming a script is not a step running it.
    Lines before `jobs:` belong to no job."""
    out, current, in_jobs = {}, None, False
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        m = JOB.match(line) if in_jobs else None
        if m:
            current = None if m.group(1).startswith(SWEEP_JOB) else m.group(1)
            if current is not None:
                out[current] = []
        elif in_jobs and current is not None:
            out[current].append(line)
    return {job: "\n".join(lines) for job, lines in out.items()}


def ci_text_without_sweeps(text):
    """The workflow's text minus the leak-sweep jobs: every other job's lines, joined."""
    return "\n".join(ci_job_texts(text).values())


def needed_commands(command):
    """The `python3 ... .py ...` commands a script's command runs, each as one whitespace-normalised
    string: a loop is expanded word by word, `&&` and `;` split it, a trailing `|| exit 1` goes."""
    out = []

    def expand(m):
        var, words, body = m.group(1), m.group(2).split(), m.group(3)
        for w in words:
            out.extend(split_commands(body.replace("$" + var, w)))
        return " ; "
    rest = LOOP.sub(expand, command)
    out.extend(split_commands(rest))
    return out


def split_commands(text):
    found = []
    for part in re.split(r"\s*(?:&&|;)\s*", text):
        part = re.sub(r"\s*\|\|.*$", "", part).strip()
        if part.startswith("python3") and PY.search(part):
            found.append(" ".join(part.split()))
    return found


def runs(command, text):
    """True when `text` has a line that runs exactly `command`: the same arguments, and nothing
    after them but the end of the line, `&&`, `;` or `||`. A path in `git show` or `cp`, or the
    same script with other arguments, is not a run of it."""
    pattern = r"(?<![\w/.$-])" + r"\s+".join(re.escape(w) for w in command.split()) + r"\s*(?:$|&&|;|\|\|)"
    return re.search(pattern, text, re.M) is not None


def uncovered(scripts, ci_text, allowed=None):
    """[(script, why)] for each test:* script with no step, one run by more than one job, plus stale
    allow-list entries. Kit checks is split into parallel shard jobs (KIT-945): the shards' union must
    be exactly the full test:* list, so a battery in no shard is silently untested and one in two is
    run twice (a CI minute spent on nothing, and a shard that can no longer be balanced)."""
    allowed = ALLOWED if allowed is None else allowed
    jobs = ci_job_texts(ci_text)
    problems = []
    names = sorted(k for k in scripts if k.startswith("test:"))
    for name in names:
        command = scripts[name]
        npm = re.compile(r"npm run (?:--silent )?%s(?![\w:-])" % re.escape(name))
        needed = needed_commands(command)
        npm_jobs = [j for j, text in jobs.items() if npm.search(text)]
        if npm_jobs:
            run_by = [npm_jobs]
        else:
            run_by = [[j for j, text in jobs.items() if runs(c, text)] for c in needed]
        covered = bool(npm_jobs) or (bool(needed) and all(run_by))
        doubled = sorted({j for sets in run_by for j in sets}) if any(len(s) > 1 for s in run_by) else []
        if name in allowed:
            if covered:
                problems.append((name, "is in the allow list but has a CI step: remove the entry"))
        elif not covered:
            problems.append((name, "has no CI step: add one to %s, or list it in ALLOWED with a reason" % CI))
        elif doubled:
            problems.append((name, "runs in more than one job (%s): each battery runs in exactly one"
                             % ", ".join(doubled)))
    for name in sorted(allowed):
        if name not in scripts:
            problems.append((name, "is in the allow list but not in package.json: remove the entry"))
    return problems


def check(root=ROOT):
    try:
        with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
            scripts = json.load(f).get("scripts", {})
        with open(os.path.join(root, CI), encoding="utf-8") as f:
            text = f.read()
    except (OSError, ValueError) as e:
        print("COULD NOT CHECK: %s" % e)
        return 2
    if not any(k.startswith("test:") for k in scripts):
        print("COULD NOT CHECK: package.json has no test:* script")
        return 2
    problems = uncovered(scripts, text)
    for name, why in problems:
        print("FAIL %s %s" % (name, why))
    if problems:
        return 1
    print("every test:* script has a CI step (%d checked)" % sum(1 for k in scripts if k.startswith("test:")))
    return 0


def selftest():
    failures = []

    def expect(name, got, want):
        ok = got == want
        print("[%s] %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            failures.append(name)

    ci = ("jobs:\n  kit-checks:\n    steps:\n      - run: python3 scripts/a.py --selftest\n"
          "      - run: |\n          python3 scripts/b.py --selftest\n          npm run --silent test:c\n"
          "  temp-leak-sweeps:\n    steps:\n      - run: python3 scripts/sweepy.py --selftest\n"
          "  other:\n    steps:\n      - run: python3 scripts/d.py\n")
    s = {"test:a": "python3 scripts/a.py --selftest", "test:b": "python3 scripts/b.py --selftest",
         "test:c": "python3 scripts/c.py --selftest", "lint:x": "python3 scripts/zzz.py"}
    expect("a script with a step passes; npm run covers; lint is ignored", uncovered(s, ci, {}), [])
    s["test:new"] = "python3 scripts/new.py --selftest"
    expect("a missing step fails and names the script", [n for n, _ in uncovered(s, ci, {})], ["test:new"])
    expect("an allowed script passes", uncovered(s, ci, {"test:new": "runs only on a Mac"}), [])
    expect("a stale allow entry (covered) fails", [n for n, _ in uncovered(s, ci, {"test:new": "r", "test:a": "r"})],
           ["test:a"])
    expect("an allow entry for a missing script fails",
           [n for n, _ in uncovered(s, ci, {"test:new": "r", "test:gone": "r"})], ["test:gone"])
    s2 = {"test:sw": "python3 scripts/sweepy.py --selftest"}
    expect("the leak sweeps do not count", [n for n, _ in uncovered(s2, ci, {})], ["test:sw"])
    expect("the job after the sweeps counts", uncovered({"test:d": "python3 scripts/d.py"}, ci, {}), [])
    loop = {"test:m": "for m in x y; do python3 scripts/p_$m.py --selftest || exit 1; done"}
    expect("a loop needs every word named",
           [n for n, _ in uncovered(loop, ci + "      - run: python3 scripts/p_x.py --selftest\n", {})], ["test:m"])
    expect("a loop is covered when each is named",
           uncovered(loop, ci + "      - run: python3 scripts/p_x.py --selftest\n      - run: python3 scripts/p_y.py --selftest\n", {}), [])
    expect("a command naming no Python file is not covered",
           [n for n, _ in uncovered({"test:n": "node x.mjs"}, ci, {})], ["test:n"])
    expect("a comment naming the script is not a step",
           [n for n, _ in uncovered({"test:q": "python3 scripts/q.py"}, ci + "      # python3 scripts/q.py\n", {})],
           ["test:q"])
    # the review of KIT-620: a path alone is not a run of the battery
    sec = {"test:sec": "python3 scripts/sec.py --selftest"}
    only_other_args = ci + "      - run: python3 scripts/sec.py\n"
    expect("the same script with other arguments does not cover it",
           [n for n, _ in uncovered(sec, only_other_args, {})], ["test:sec"])
    mention = ci + "      - run: git show \"$t:scripts/sec.py\" > x && cp scripts/sec.py y\n"
    expect("a path in git show or cp does not cover it", [n for n, _ in uncovered(sec, mention, {})], ["test:sec"])
    expect("the exact command covers it", uncovered(sec, ci + "      - run: python3 scripts/sec.py --selftest\n", {}), [])
    both = {"test:two": "python3 scripts/t.py --selftest && python3 scripts/t.py"}
    expect("each part of an && command needs its own run",
           [n for n, _ in uncovered(both, ci + "      - run: python3 scripts/t.py --selftest\n", {})], ["test:two"])
    expect("both parts run on their own lines: covered",
           uncovered(both, ci + "      - run: python3 scripts/t.py --selftest\n      - run: python3 scripts/t.py\n", {}), [])
    expect("a command followed by && or || still counts",
           uncovered(sec, ci + "      - run: python3 scripts/sec.py --selftest && echo ok\n", {}), [])
    # KIT-945: Kit checks is parallel shard jobs, and each battery belongs to exactly one of them
    sharded = ("jobs:\n  kit-checks-1:\n    steps:\n      - run: python3 scripts/a.py --selftest\n"
               "  kit-checks-2:\n    steps:\n      - run: python3 scripts/b.py --selftest\n"
               "  temp-leak-sweeps-shards:\n    steps:\n      - run: python3 scripts/a.py --selftest\n"
               "  temp-leak-sweeps:\n    steps:\n      - run: python3 scripts/c.py --selftest\n")
    ab = {"test:a": "python3 scripts/a.py --selftest", "test:b": "python3 scripts/b.py --selftest"}
    expect("each battery in exactly one shard job passes (the sweeps' shard jobs are not shards)",
           uncovered(ab, sharded, {}), [])
    twice = sharded.replace("scripts/b.py --selftest", "scripts/a.py --selftest")
    expect("a battery run by two shard jobs fails and is named",
           [(n, "kit-checks-1, kit-checks-2" in w) for n, w in uncovered({"test:a": ab["test:a"]}, twice, {})],
           [("test:a", True)])
    expect("a battery in no shard job fails even when a sweeps shard runs it",
           [n for n, _ in uncovered({"test:c": "python3 scripts/c.py --selftest", "test:d": "python3 scripts/a.py --selftest"},
                                    sharded.replace("kit-checks-1:\n    steps:\n      - run: python3 scripts/a.py",
                                                    "kit-checks-1:\n    steps:\n      - run: python3 scripts/z.py"), {})],
           ["test:c", "test:d"])
    npm_twice = ("jobs:\n  kit-checks-1:\n    steps:\n      - run: npm run test:n\n"
                 "  kit-checks-2:\n    steps:\n      - run: npm run --silent test:n\n")
    expect("an npm run in two shard jobs fails", [n for n, _ in uncovered({"test:n": "node x.mjs"}, npm_twice, {})], ["test:n"])
    expect("the allow list holds only reasons", all(isinstance(v, str) and v for v in ALLOWED.values()), True)
    if failures:
        print("%d FAILED" % len(failures))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else check())
