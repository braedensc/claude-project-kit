#!/usr/bin/env python3
"""Which job runs which battery: the CI shard lists, and the fan-in that never reads a skip as a pass (KIT-945).

    python3 scripts/check_ci_shards.py --verify                     # lists, workflows and fan-ins agree
    python3 scripts/check_ci_shards.py --list FAMILY N              # the batteries of shard N (1-based), one a line
    python3 scripts/check_ci_shards.py fan-in --expect ID,ID,...    # the fan-in step: every needed job succeeded
    python3 scripts/check_ci_shards.py --selftest                   # the battery CI runs

WHY. On 20 Nov 2026 "Kit checks" took 14m25s of its 15 minutes, the sweeps 15m32s of 20, and the
Python 3.9 job passed its 30 minutes and was cancelled at the limit on two PRs, so the batteries
after the cut got no run on the Python the Mac runs. About 140 `test:*` batteries ran one after
another in each. A job that nears its limit turns every PR red for a reason no PR caused.

THE SPLIT. Each of the three jobs is now several parallel shard jobs and one fan-in job that keeps
the old name, because branch protection requires "Kit checks" and "Temp-leak sweeps" by name.
  * Kit checks: explicit jobs `kit-checks-1..N` in ci.yml. Their steps ARE the list, and
    scripts/check_ci_covers_tests.py fails when a `test:*` battery is run by no shard or by two.
  * Temp-leak sweeps and the Python 3.9 job: the lists below, run by
    `check_temp_leaks.py --shard FAMILY/N`.

NO BATTERY DROPPED OR DOUBLED. --verify fails when a family's lists do not hold every `test:*` script of
package.json exactly once, so a new battery is never silently untested: add it to a list below.

THE SKIPPED-JOB TRAP. A job whose `needs` failed is SKIPPED, and a skipped required check can read as
a pass. The fan-in jobs run with `if: always()` and `fan-in` below fails unless EVERY needed job's
result is "success" and exactly the expected jobs are present. --verify checks the workflow keeps that
shape: the name, `if: always()`, the needs list, the --expect list and the shard jobs all agree.

Standard library only. Exit 0 = pass. Exit 1 = a check failed. Exit 2 = could not check (a file missing).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The batteries of each shard, balanced on measured times (20 Nov 2026, Ubuntu): the sweeps by
# 482 s and 481 s, the Mac job on the first macOS runs (test:runner-setup alone is 9.5 minutes there).
# Add a new battery to the shortest list. Order inside a list is the order they run.
SWEEPS = [
    [
        "test:delivery", "test:schemas", "test:emit", "test:approve", "test:merge",
        "test:graders", "test:telemetry", "test:block", "test:plan-poller", "test:dashboard",
        "test:review-basis", "test:root-guard", "test:pass-lock", "test:stage-e-setup",
        "test:github-app", "test:install", "test:settings", "test:parts", "test:chat-lane-setup",
        "test:bridge", "test:ws-client", "test:bridge-push-setup", "test:bridge-requests",
        "test:bridge-setup", "test:voice", "test:review-poller", "test:finding-poller",
        "test:catchup", "test:sweep", "test:watcher", "test:watcher-copy", "test:watcher-tracker",
        "test:hold", "test:watcher-page", "test:validator-check", "test:validator-app",
        "test:morning", "test:validator-brief", "test:validator-run", "test:validator-pieces",
        "test:validator-limits", "test:sprint", "test:sprint-plan", "test:sprint-changes",
        "test:window", "test:queue-order", "test:usage-runs", "test:usage", "test:queue-read",
        "test:runner-guard", "test:runner-store", "test:runner-setup", "test:session-tools",
        "test:writer-key", "test:bounce", "test:notifier", "test:heartbeat-monitor",
        "test:conflict-waker-setup", "test:union", "test:acceptance-run",
        "test:template-label-gate", "test:label-gate", "test:gate-policy", "test:branch-rule",
        "test:dispatcher-removal", "test:repo-setup", "test:ci-covers", "test:alert-pages", "test:routing-tags",
        "test:sync-kit", "test:scratch-run", "test:agent-env",
    ],
    [
        "test:hooks", "test:bypass", "test:workflow-calls", "test:dor", "test:jsonschema",
        "test:local-dispatch", "test:safe-outputs", "test:plan-executor", "test:sync-user-skills",
        "test:lifecycle", "test:review", "test:gh-fallback", "test:review-local",
        "test:criteria-snapshot", "test:stage-e-app", "test:stage-a-setup", "test:ask",
        "test:model-switch", "test:bridge-transport", "test:chat-cap", "test:bridge-push",
        "test:bridge-modules", "test:telemetry-local", "test:look", "test:sweep-setup",
        "test:status", "test:watcher-setup", "test:watcher-judge", "test:tech-lead-tools",
        "test:validator-ledger", "test:day-planner", "test:validator-settings",
        "test:validator-evidence", "test:validator-facts", "test:validator-merge",
        "test:validator-plans", "test:validator-route", "test:validator-setup",
        "test:tech-lead-turn", "test:standup", "test:sprint-review", "test:queue-settings",
        "test:spend-ledger", "test:queue-pool", "test:queue-start", "test:session-kinds",
        "test:harness-claude", "test:runner-probe", "test:runner", "test:runner-limits",
        "test:runner-live", "test:queue-setup", "test:day-plan", "test:notifier-setup",
        "test:conflict", "test:session-brief", "test:update", "test:watch",
        "test:acceptance-poller", "test:acceptance-setup", "test:pr-base", "test:hooks-path",
        "test:agent-trigger", "test:runner-pin", "test:stdlib-only", "test:direct-checks",
        "test:secret-scan", "test:machine-tickets", "test:temp-leaks", "test:cleanup-probe",
        "test:ci-shards",
    ],
]

MAC = [
    [
        "test:dor", "test:approve", "test:graders", "test:plan-executor", "test:plan-poller",
        "test:review-local", "test:pass-lock", "test:install", "test:ask", "test:chat-lane-setup",
        "test:bridge-push-setup", "test:bridge-requests", "test:review-poller", "test:look",
        "test:sweep-setup", "test:watcher", "test:watcher-copy", "test:watcher-tracker",
        "test:hold", "test:morning", "test:validator-brief", "test:validator-pieces",
        "test:validator-plans", "test:validator-limits", "test:sprint-plan", "test:queue-pool",
        "test:runner-probe", "test:runner", "test:runner-limits", "test:session-tools",
        "test:writer-key", "test:notifier", "test:conflict-waker-setup", "test:watch",
        "test:branch-rule", "test:stdlib-only", "test:secret-scan", "test:sync-kit",
    ],
    [
        "test:runner-setup",
    ],
    [
        "test:emit", "test:merge", "test:telemetry", "test:block", "test:github-app",
        "test:model-switch", "test:status", "test:validator-facts", "test:validator-check",
        "test:validator-route", "test:sprint", "test:usage", "test:spend-ledger",
        "test:queue-read", "test:runner-live", "test:day-plan", "test:heartbeat-monitor",
        "test:update", "test:union", "test:acceptance-poller", "test:pr-base", "test:hooks-path",
        "test:label-gate", "test:gate-policy", "test:dispatcher-removal", "test:repo-setup", "test:ci-covers",
        "test:ci-shards",
    ],
    [
        "test:hooks", "test:jsonschema", "test:local-dispatch", "test:safe-outputs",
        "test:review", "test:gh-fallback", "test:criteria-snapshot", "test:stage-a-setup",
        "test:settings", "test:chat-cap", "test:bridge-push", "test:bridge-setup", "test:voice",
        "test:telemetry-local", "test:sweep", "test:watcher-page", "test:day-planner",
        "test:validator-settings", "test:validator-evidence", "test:validator-run",
        "test:standup", "test:sprint-changes", "test:window", "test:queue-order",
        "test:session-kinds", "test:harness-claude", "test:runner-guard", "test:runner-store",
        "test:queue-setup", "test:bounce", "test:conflict", "test:template-label-gate",
        "test:runner-pin", "test:alert-pages", "test:routing-tags", "test:temp-leaks",
        "test:agent-env", "test:cleanup-probe",
    ],
    [
        "test:bypass", "test:workflow-calls", "test:delivery", "test:schemas",
        "test:sync-user-skills", "test:lifecycle", "test:dashboard", "test:review-basis",
        "test:root-guard", "test:stage-e-setup", "test:stage-e-app", "test:parts", "test:bridge",
        "test:bridge-transport", "test:ws-client", "test:bridge-modules", "test:finding-poller",
        "test:catchup", "test:watcher-setup", "test:watcher-judge", "test:tech-lead-tools",
        "test:validator-ledger", "test:validator-app", "test:validator-merge",
        "test:validator-setup", "test:tech-lead-turn", "test:sprint-review",
        "test:queue-settings", "test:usage-runs", "test:queue-start", "test:notifier-setup",
        "test:session-brief", "test:acceptance-run", "test:acceptance-setup",
        "test:agent-trigger", "test:direct-checks", "test:machine-tickets", "test:scratch-run",
    ],
]

FAMILIES = {"sweeps": SWEEPS, "mac": MAC}

# One row per fan-in job. `shards`: how its shard jobs are found. "prefix:X" = every job id that
# starts with X. "matrix:ID" = the one job ID whose `matrix.shard` list is 1..N for the family.
FAN_INS = (
    {"workflow": ".github/workflows/ci.yml", "id": "kit-checks", "name": "Kit checks",
     "shards": "prefix:kit-checks-", "family": None},
    {"workflow": ".github/workflows/ci.yml", "id": "temp-leak-sweeps", "name": "Temp-leak sweeps",
     "shards": "matrix:temp-leak-sweeps-shards", "family": "sweeps"},
    {"workflow": ".github/workflows/ci-mac-python.yml", "id": "mac-python",
     "name": "Kit checks (Python 3.9, the Mac's)", "shards": "matrix:mac-python-shards", "family": "mac"},
)


def shard_names(family, n):
    """The `test:*` names of shard `n` (1-based) of `family`. ValueError for an unknown family or shard."""
    if family not in FAMILIES:
        raise ValueError("no such shard family %r (have: %s)" % (family, ", ".join(sorted(FAMILIES))))
    shards = FAMILIES[family]
    if not isinstance(n, int) or not 1 <= n <= len(shards):
        raise ValueError("%s has shards 1..%d, not %r" % (family, len(shards), n))
    return list(shards[n - 1])


def parse_spec(spec):
    """'sweeps/2' -> ('sweeps', 2). ValueError on anything else."""
    m = re.fullmatch(r"([a-z]+)/(\d+)", spec or "")
    if not m:
        raise ValueError("a shard is FAMILY/N, like sweeps/1, not %r" % spec)
    return m.group(1), int(m.group(2))


def package_batteries(root=ROOT):
    """The `test:*` script names in package.json, in order. OSError/ValueError if unreadable."""
    with open(os.path.join(root, "package.json"), encoding="utf-8") as fh:
        scripts = json.load(fh).get("scripts", {})
    return [k for k in scripts if k.startswith("test:")]


def list_problems(batteries, families=None):
    """[message] for each way a family's lists differ from the full battery list: a battery missing,
    in two shards (or twice in one), unknown to package.json, or a shard with nothing in it."""
    families = FAMILIES if families is None else families
    problems = []
    full = set(batteries)
    for family in sorted(families):
        seen = {}
        for i, shard in enumerate(families[family], 1):
            if not shard:
                problems.append("%s shard %d is empty" % (family, i))
            for name in shard:
                if name in seen:
                    problems.append("%s: %s is in shard %d and shard %d" % (family, name, seen[name], i))
                seen.setdefault(name, i)
        for name in sorted(set(seen) - full):
            problems.append("%s: %s is in shard %d but is not a test:* script in package.json: remove it"
                            % (family, name, seen[name]))
        for name in sorted(full - set(seen)):
            problems.append("%s: %s is in no shard, so no job would run it: add it to a list in %s"
                            % (family, name, "scripts/check_ci_shards.py"))
    return problems


# ---------------------------------------------------------------------------------- the workflows

JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def split_jobs(text):
    """{job id: its lines} for each job under `jobs:`, in file order (a dict keeps it)."""
    jobs, current, in_jobs = {}, None, False
    for line in text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if line.startswith("#"):          # a column-0 comment between jobs ends nothing
            continue
        if in_jobs and line and not line.startswith(" "):
            in_jobs = False
        m = JOB_HEADER.match(line) if in_jobs else None
        if m:
            current = m.group(1)
            jobs[current] = []
        elif in_jobs and current is not None:
            jobs[current].append(line)
    return jobs


def job_field(lines, key):
    """The value of a job-level `key: value` line (four spaces in), or None."""
    for line in lines:
        m = re.match(r"^    %s:\s*(.*?)\s*$" % re.escape(key), line)
        if m:
            return m.group(1)
    return None


def flow_list(value):
    """['a', 'b'] from '[a, b]'; None when it is not a one-line list."""
    m = re.fullmatch(r"\[(.*)\]", value or "")
    if not m:
        return None
    return [w.strip() for w in m.group(1).split(",") if w.strip()]


def workflow_problems(workflows, fan_ins=None, families=None):
    """[message] for each way a fan-in job, its shard jobs and the family lists disagree.
    `workflows` is {path: text}."""
    fan_ins = FAN_INS if fan_ins is None else fan_ins
    families = FAMILIES if families is None else families
    problems = []
    for spec in fan_ins:
        where = "%s job %s" % (spec["workflow"], spec["id"])
        text = workflows.get(spec["workflow"])
        if text is None:
            problems.append("%s: the workflow file could not be read" % where)
            continue
        jobs = split_jobs(text)
        if spec["id"] not in jobs:
            problems.append("%s: there is no such job" % where)
            continue
        fan = jobs[spec["id"]]
        if job_field(fan, "name") != spec["name"]:
            problems.append("%s: its name is %r, and branch protection needs %r"
                            % (where, job_field(fan, "name"), spec["name"]))
        if job_field(fan, "if") != "always()":
            problems.append("%s: it needs `if: always()`, or a failed shard makes it SKIPPED, which can read as a pass"
                            % where)
        if job_field(fan, "continue-on-error") is not None:
            problems.append("%s: a fan-in cannot have continue-on-error" % where)
        for jid, lines in jobs.items():
            if jid != spec["id"] and job_field(lines, "name") == spec["name"]:
                problems.append("%s: job %s has the same name, so one context would report twice" % (where, jid))
        kind, _, arg = spec["shards"].partition(":")
        if kind == "prefix":
            shard_ids = [j for j in jobs if j.startswith(arg)]
        else:
            shard_ids = [arg] if arg in jobs else []
        if not shard_ids:
            problems.append("%s: no shard job (%s) found" % (where, spec["shards"]))
            continue
        needs = flow_list(job_field(fan, "needs"))
        if needs is None or sorted(needs) != sorted(shard_ids):
            problems.append("%s: needs is %r, but the shard jobs are %r (write needs as a one-line list)"
                            % (where, needs, shard_ids))
        body = "\n".join(fan)
        m = re.search(r"check_ci_shards\.py fan-in --expect (\S+)", body)
        if not m or sorted(m.group(1).split(",")) != sorted(shard_ids):
            problems.append("%s: its fan-in step must run `check_ci_shards.py fan-in --expect %s`"
                            % (where, ",".join(shard_ids)))
        if "toJSON(needs)" not in body:
            problems.append("%s: its fan-in step must pass NEEDS: ${{ toJSON(needs) }}" % where)
        for jid in shard_ids:
            if job_field(jobs[jid], "continue-on-error") is not None:
                problems.append("%s: shard job %s has continue-on-error, so its failure would read as a pass"
                                % (where, jid))
        if kind == "matrix":
            family = spec["family"]
            lines = jobs[shard_ids[0]]
            m = re.search(r"^        shard:\s*(\[.*\])\s*$", "\n".join(lines), re.M)
            want = list(range(1, len(families[family]) + 1))
            got = flow_list(m.group(1)) if m else None
            if got is None or [x for x in got] != [str(x) for x in want]:
                problems.append("%s: matrix shard is %r but the %s lists have shards %r"
                                % (where, got, family, want))
            if "fail-fast: false" not in "\n".join(lines):
                problems.append("%s: its matrix needs `fail-fast: false`, so one red shard does not hide the rest"
                                % where)
            if "--shard %s/${{ matrix.shard }}" % family not in "\n".join(lines):
                problems.append("%s: no step runs `--shard %s/${{ matrix.shard }}`" % (where, family))
    return problems


def fan_in(needs, expect):
    """[message] unless every job in `expect` is present in `needs` with the result "success", and nothing
    else is needed. A skipped, cancelled, failed or missing job is a failure, never a pass."""
    problems = []
    if not isinstance(needs, dict):
        return ["the needs context is not an object: it cannot be read, so this is not a pass"]
    for jid in expect:
        got = needs.get(jid)
        result = got.get("result") if isinstance(got, dict) else None
        if result != "success":
            problems.append("%s: result is %r, not 'success'" % (jid, result if result is not None else "missing"))
    for jid in sorted(set(needs) - set(expect)):
        problems.append("%s: needed by the job but not in --expect (a shard nobody listed)" % jid)
    if not expect:
        problems.append("--expect names no job: nothing was checked, which is not a pass")
    return problems


def cmd_fan_in(argv):
    if "--expect" not in argv or argv.index("--expect") + 1 >= len(argv):
        print("usage: check_ci_shards.py fan-in --expect ID,ID,...", file=sys.stderr)
        return 2
    expect = [w for w in argv[argv.index("--expect") + 1].split(",") if w]
    raw = os.environ.get("NEEDS", "")
    try:
        needs = json.loads(raw)
    except ValueError:
        print("FAIL the NEEDS environment variable is not JSON: %r" % raw[:80])
        return 1
    problems = fan_in(needs, expect)
    for p in problems:
        print("FAIL " + p)
    if problems:
        return 1
    print("every shard succeeded: %s" % ", ".join(expect))
    return 0


def cmd_verify(root=ROOT):
    try:
        batteries = package_batteries(root)
        workflows = {}
        for spec in FAN_INS:
            if spec["workflow"] not in workflows:
                with open(os.path.join(root, spec["workflow"]), encoding="utf-8") as fh:
                    workflows[spec["workflow"]] = fh.read()
    except (OSError, ValueError) as e:
        print("COULD NOT CHECK: %s" % e)
        return 2
    if not batteries:
        print("COULD NOT CHECK: package.json has no test:* script")
        return 2
    problems = list_problems(batteries) + workflow_problems(workflows)
    for p in problems:
        print("FAIL " + p)
    if problems:
        return 1
    print("every test:* battery is in exactly one shard of each family (%d batteries; %s), "
          "and each fan-in needs every shard" % (len(batteries),
                                                 ", ".join("%s %d" % (f, len(FAMILIES[f])) for f in sorted(FAMILIES))))
    return 0


def selftest():
    failures = []

    def expect(name, got, want):
        ok = got == want
        print("[%s] %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            failures.append(name)

    fams = {"f": [["test:a", "test:b"], ["test:c"]]}
    expect("lists that hold each battery once pass", list_problems(["test:a", "test:b", "test:c"], fams), [])
    expect("a battery in no shard fails and is named",
           [("test:new" in p and "no shard" in p) for p in list_problems(["test:a", "test:b", "test:c", "test:new"], fams)],
           [True])
    expect("a battery in two shards fails",
           len(list_problems(["test:a", "test:b", "test:c"], {"f": [["test:a", "test:b"], ["test:b", "test:c"]]})), 1)
    expect("a battery twice in one shard fails",
           len(list_problems(["test:a"], {"f": [["test:a", "test:a"]]})), 1)
    expect("a name that is not in package.json fails",
           len(list_problems(["test:a", "test:b"], {"f": [["test:a", "test:b", "test:gone"]]})), 1)
    expect("an empty shard fails", len(list_problems(["test:a"], {"f": [["test:a"], []]})), 1)
    expect("each family is judged on its own",
           len(list_problems(["test:a", "test:b"], {"f": [["test:a", "test:b"]], "g": [["test:a"]]})), 1)
    expect("the real lists hold no battery twice and none empty",
           [p for p in list_problems(sorted({n for s in SWEEPS for n in s} | {n for s in MAC for n in s}))
            if "in shard" in p or "is empty" in p], [])
    expect("the real families cover the same batteries",
           sorted(n for s in SWEEPS for n in s) == sorted(n for s in MAC for n in s), True)
    expect("shard_names is 1-based", shard_names("sweeps", 1) == list(SWEEPS[0]), True)
    for bad in (("nope", 1), ("sweeps", 0), ("sweeps", len(SWEEPS) + 1)):
        try:
            shard_names(*bad)
            raised = False
        except ValueError:
            raised = True
        expect("shard_names%r is refused" % (bad,), raised, True)
    expect("a shard spec parses", parse_spec("mac/3"), ("mac", 3))
    for bad in ("mac", "mac/x", "/1", "mac/1/2", None):
        try:
            parse_spec(bad)
            raised = False
        except ValueError:
            raised = True
        expect("a bad shard spec %r is refused" % (bad,), raised, True)

    ok = {"a": {"result": "success"}, "b": {"result": "success"}}
    expect("fan-in: all succeeded passes", fan_in(ok, ["a", "b"]), [])
    for result in ("failure", "skipped", "cancelled", None):
        needs = {"a": {"result": "success"}, "b": {"result": result} if result else {}}
        expect("fan-in: a %s shard fails" % (result or "result-less"), len(fan_in(needs, ["a", "b"])), 1)
    expect("fan-in: a shard that is not in needs fails", len(fan_in({"a": {"result": "success"}}, ["a", "b"])), 1)
    expect("fan-in: an unexpected needed job fails", len(fan_in(dict(ok, c={"result": "success"}), ["a", "b"])), 1)
    expect("fan-in: an empty --expect is not a pass", len(fan_in({}, [])), 1)
    expect("fan-in: unreadable needs is not a pass", len(fan_in("oops", ["a"])), 1)
    expect("fan-in: a non-object job entry is not a pass", len(fan_in({"a": "success"}, ["a"])), 1)
    saved = os.environ.get("NEEDS")
    try:
        quiet = open(os.devnull, "w")
        old, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = quiet
        try:
            os.environ["NEEDS"] = json.dumps({"a": {"result": "success"}, "b": {"result": "skipped"}})
            code_bad = cmd_fan_in(["--expect", "a,b"])
            os.environ["NEEDS"] = json.dumps(ok)
            code_ok = cmd_fan_in(["--expect", "a,b"])
            os.environ["NEEDS"] = "not json"
            code_junk = cmd_fan_in(["--expect", "a,b"])
            code_noarg = cmd_fan_in([])
        finally:
            sys.stdout, sys.stderr = old, old_err
            quiet.close()
    finally:
        if saved is None:
            os.environ.pop("NEEDS", None)
        else:
            os.environ["NEEDS"] = saved
    expect("fan-in command: a skipped shard exits 1, all success 0, junk 1, no --expect 2",
           (code_bad, code_ok, code_junk, code_noarg), (1, 0, 1, 2))

    wf = ("jobs:\n"
          "  k-1:\n    name: Shard 1\n    steps:\n      - run: x\n"
          "  k-2:\n    name: Shard 2\n    steps:\n      - run: y\n"
          "  k:\n    name: K\n    needs: [k-1, k-2]\n    if: always()\n    steps:\n"
          "      - run: python3 scripts/check_ci_shards.py fan-in --expect k-1,k-2\n"
          "        env:\n          NEEDS: ${{ toJSON(needs) }}\n"
          "  s-shards:\n    name: S shard ${{ matrix.shard }}\n    strategy:\n      fail-fast: false\n"
          "      matrix:\n        shard: [1, 2]\n    steps:\n      - run: c --shard f/${{ matrix.shard }}\n"
          "  s:\n    name: S\n    needs: [s-shards]\n    if: always()\n    steps:\n"
          "      - run: python3 scripts/check_ci_shards.py fan-in --expect s-shards\n"
          "        env:\n          NEEDS: ${{ toJSON(needs) }}\n")
    spec = ({"workflow": "w.yml", "id": "k", "name": "K", "shards": "prefix:k-", "family": None},
            {"workflow": "w.yml", "id": "s", "name": "S", "shards": "matrix:s-shards", "family": "f"})
    f2 = {"f": [["test:a"], ["test:b"]]}

    def wp(text, specs=spec, fam=f2):
        return workflow_problems({"w.yml": text}, specs, fam)

    expect("workflow: a well-formed fan-in and matrix pass", wp(wf), [])
    expect("workflow: a column-0 comment between jobs hides no job",
           wp(wf.replace("  k:\n", "# a note\n  k:\n", 1)), [])
    expect("workflow: a fan-in without `if: always()` fails",
           len(wp(wf.replace("    if: always()\n", "", 1))), 1)
    expect("workflow: a fan-in whose needs omits a shard fails (and its --expect differs from the jobs)",
           len(wp(wf.replace("needs: [k-1, k-2]", "needs: [k-1]"))), 1)
    expect("workflow: an --expect list that omits a shard fails",
           len(wp(wf.replace("--expect k-1,k-2", "--expect k-1"))), 1)
    expect("workflow: a new shard job nobody needs fails",
           len(wp(wf.replace("  k:\n", "  k-3:\n    name: Shard 3\n    steps:\n      - run: z\n  k:\n"))) >= 1, True)
    expect("workflow: a renamed fan-in fails", len(wp(wf.replace("name: K\n", "name: Kit\n"))), 1)
    expect("workflow: a matrix with the wrong shard count fails", len(wp(wf.replace("[1, 2]", "[1]"))), 1)
    expect("workflow: a matrix without fail-fast false fails",
           len(wp(wf.replace("      fail-fast: false\n", ""))), 1)
    expect("workflow: a matrix step that does not use matrix.shard fails",
           len(wp(wf.replace("c --shard f/${{ matrix.shard }}", "c --shard f/1"))), 1)
    expect("workflow: continue-on-error on a shard fails",
           len(wp(wf.replace("  k-1:\n    name: Shard 1\n", "  k-1:\n    name: Shard 1\n    continue-on-error: true\n"))), 1)
    expect("workflow: a fan-in that does not pass NEEDS fails",
           len(wp(wf.replace("toJSON(needs)", "x", 1))), 1)
    expect("workflow: a missing fan-in job fails", len(wp(wf.replace("  k:\n", "  kk:\n"))) >= 1, True)
    expect("workflow: an unreadable workflow file fails", len(workflow_problems({}, spec, f2)), 2)
    if failures:
        print("%d FAILED" % len(failures))
        return 1
    print("all passed")
    return 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    if "--verify" in argv:
        return cmd_verify()
    if "--list" in argv:
        i = argv.index("--list")
        try:
            names = shard_names(argv[i + 1], int(argv[i + 2]))
        except (IndexError, ValueError) as e:
            print("COULD NOT LIST: %s" % e, file=sys.stderr)
            return 2
        print("\n".join(names))
        return 0
    if argv and argv[0] == "fan-in":
        return cmd_fan_in(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
