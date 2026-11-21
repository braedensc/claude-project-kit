#!/usr/bin/env python3
"""The direct checks: scripts Kit checks runs over the real tree, not as a selftest (KIT-659).

    python3 scripts/check_direct_checks.py --run        # run every direct check, report each, exit 1 on any failure
    python3 scripts/check_direct_checks.py --json-yaml  # parse every tracked JSON and YAML file
    python3 scripts/check_direct_checks.py --verify     # the list below and ci.yml's Kit checks agree
    python3 scripts/check_direct_checks.py --selftest   # the battery CI runs

WHY. The CI job on Python 3.9 (the Mac's own) used to run only the `test:*` batteries. Kit checks
also runs several scripts directly over the real tree, and those paths never ran on 3.9.

ONE PLACE. DIRECT below is the list. The 3.9 job runs `--run`. Kit checks keeps one named step
per check, and `--verify` fails when the two differ: a listed script with no direct step in
Kit checks, or a direct `python3 <script>.py` step there that is neither listed nor named in
EXCLUDED with its reason. So a new direct check cannot reach one job and miss the other.

Exit 0 = pass. Exit 1 = a check failed or the lists differ. Exit 2 = could not run (a file missing).
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CI = ".github/workflows/ci.yml"
JOB = "kit-checks"

# The direct checks: a script path from the repository root, then its arguments (if any).
DIRECT = (
    "scripts/check_runner_pin.py",
    "scripts/check_agent_trigger_author.py",
    "scripts/check_stdlib_only.py",
    "scripts/check_workflow_calls.py",
    "scripts/check_placeholders.py",
    "scripts/check_session_brief.py",
    "scripts/check_schemas.py",
    "scripts/check_delivery_config.py",
    "scripts/check_alert_pages.py",
    "scripts/pipeline_parts.py check",
    "scripts/check_label_gate.py",
    "scripts/check_template_label_gate.py",
    "scripts/check_ci_covers_tests.py",
    "scripts/ci_shards.py --verify",
    "scripts/pipeline_session_kinds.py --check",
)

# Direct steps in Kit checks that are deliberately not in DIRECT: command -> reason.
EXCLUDED = {
    ".claude/hooks/test_hooks.py": "a battery: the 3.9 job runs it as test:hooks",
    ".claude/hooks/test_guard_bypass.py": "a battery: the 3.9 job runs it as test:bypass",
}

# One command of a step: `python3 [flags] path.py [arguments]`. A step that runs the script with
# --selftest is a battery, not a direct check. This file's own steps (--verify, --json-yaml) are
# the machinery, not a check.
STEP = re.compile(r"python3\s+(?:-\w+\s+)*([\w./-]+\.py)((?:\s+[^\s&;|]+)*)\s*$")
SELF = "scripts/check_direct_checks.py"
HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def kit_checks_direct_steps(ci_text):
    """The direct commands Kit checks runs (path and arguments), in order; comments skipped, a
    `--selftest` run is not one, and each `&&` part of a line is read on its own."""
    found, inside, in_jobs = [], False, False
    for line in ci_text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
        m = HEADER.match(line) if in_jobs else None
        if m:
            # Kit checks is split into parallel shard jobs `kit-checks-1..N` (KIT-945), and the job
            # named plain `kit-checks` is the fan-in: a direct step is in the shards.
            inside = m.group(1).startswith(JOB + "-")
        if not inside or line.lstrip().startswith("#"):
            continue
        for part in re.split(r"\s*&&\s*", line.split("run:", 1)[-1].strip()):
            m = STEP.match(part.strip())
            if m and m.group(1) != SELF and "--selftest" not in m.group(2).split():
                found.append((m.group(1) + m.group(2)).strip())
    return found


def differences(ci_text, direct=DIRECT, excluded=EXCLUDED):
    """[message] for each way the list and Kit checks disagree."""
    steps = set(kit_checks_direct_steps(ci_text))
    problems = []
    for path in direct:
        if path not in steps:
            problems.append("%s is in DIRECT but Kit checks has no direct step for it" % path)
    for path in sorted(steps):
        if path not in direct and path not in excluded:
            problems.append("%s is a direct step in Kit checks but not in DIRECT: add it, or name it in "
                            "EXCLUDED with a reason" % path)
    for path in sorted(excluded):
        if path in direct:
            problems.append("%s is in both DIRECT and EXCLUDED" % path)
        elif path not in steps:
            problems.append("%s is in EXCLUDED but is no direct step in Kit checks: remove the entry" % path)
    return problems


def json_yaml(root=ROOT):
    """(bad, count): the tracked JSON and YAML files that do not parse."""
    try:
        import yaml
    except ImportError:                  # the CI jobs install PyYAML; without it this is could-not-check
        return None, 0
    out = subprocess.run(["git", "-C", root, "ls-files", "*.json", "*.yml", "*.yaml"],
                         capture_output=True, text=True)
    if out.returncode:
        return None, 0
    bad, files = [], [p for p in out.stdout.splitlines() if p]
    for f in files:
        try:
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                if f.endswith(".json"):
                    json.load(fh)
                else:
                    list(yaml.safe_load_all(fh))
        except Exception as e:                    # any parse error is the answer
            bad.append("%s: %s" % (f, e))
    return bad, len(files)


def cmd_json_yaml():
    bad, n = json_yaml()
    if bad is None:
        print("COULD NOT CHECK: git ls-files failed, or PyYAML is not installed", file=sys.stderr)
        return 2
    print("\n".join(bad) if bad else "all %d tracked JSON/YAML files parse OK" % n)
    return 1 if bad else 0


def cmd_run(root=ROOT, direct=DIRECT, json_yaml_too=True):
    print("python: %s" % sys.version.split()[0])
    failed = []
    for command in direct:
        argv = command.split()
        if not os.path.isfile(os.path.join(root, argv[0])):
            print("COULD NOT RUN %s: file missing" % argv[0], file=sys.stderr)
            return 2
        print("::group::%s" % command)
        code = subprocess.run([sys.executable] + argv, cwd=root).returncode
        print("::endgroup::")
        print("%s %s" % ("ok  " if code == 0 else "FAIL", command))
        if code:
            failed.append(command)
    if json_yaml_too and cmd_json_yaml():
        failed.append("tracked JSON/YAML parse")
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("every direct check passed")
    return 0


def cmd_verify():
    try:
        with open(os.path.join(ROOT, CI), encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print("COULD NOT CHECK: %s" % e, file=sys.stderr)
        return 2
    problems = differences(text)
    for p in problems:
        print("FAIL " + p)
    if not problems:
        print("the direct-check list and Kit checks agree (%d listed)" % len(DIRECT))
    return 1 if problems else 0


def selftest():
    failures = []

    def expect(name, got, want):
        ok = got == want
        print("[%s] %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            failures.append(name)

    ci = ("jobs:\n  kit-checks-1:\n    steps:\n      - name: a\n        run: python3 scripts/a.py\n"
          "      - run: |\n          python3 scripts/b.py --selftest\n          python3 scripts/c.py\n"
          "      # python3 scripts/ghost.py\n      - run: python3 scripts/d.py --selftest\n"
          "  kit-checks-2:\n    steps:\n      - run: python3 .claude/hooks/t.py\n"
          "  kit-checks:\n    steps:\n      - run: python3 scripts/fan.py\n"
          "  other:\n    steps:\n      - run: python3 scripts/z.py\n")
    expect("direct steps: no-argument runs in Kit checks only, comments skipped",
           kit_checks_direct_steps(ci), ["scripts/a.py", "scripts/c.py", ".claude/hooks/t.py"])
    ex = {".claude/hooks/t.py": "a battery"}
    expect("lists agree", differences(ci, ("scripts/a.py", "scripts/c.py"), ex), [])
    expect("a listed script with no Kit checks step fails",
           len(differences(ci, ("scripts/a.py", "scripts/c.py", "scripts/new.py"), ex)), 1)
    expect("a Kit checks direct step missing from the list fails",
           len(differences(ci, ("scripts/a.py",), ex)), 1)
    expect("a step in another job is not judged", differences(ci, ("scripts/a.py", "scripts/c.py"), ex), [])
    expect("a stale exclusion fails", len(differences(ci, ("scripts/a.py", "scripts/c.py"),
                                                      dict(ex, **{"scripts/gone.py": "x"}))), 1)
    expect("an exclusion also listed fails",
           len(differences(ci, ("scripts/a.py", "scripts/c.py", ".claude/hooks/t.py"), ex)), 1)
    expect("DIRECT holds no duplicate", len(set(DIRECT)), len(DIRECT))
    expect("every EXCLUDED has a reason", all(v.strip() for v in EXCLUDED.values()), True)
    expect("every listed script exists", all(os.path.isfile(os.path.join(ROOT, c.split()[0])) for c in DIRECT), True)
    # a step that takes an argument is read as a direct check, with its argument (review of KIT-659)
    ci2 = ci.replace("python3 scripts/c.py\n", "python3 scripts/c.py check && python3 -I scripts/e.py --strict\n")
    expect("an argument is part of the command; && parts and interpreter flags are read",
           kit_checks_direct_steps(ci2), ["scripts/a.py", "scripts/c.py check", "scripts/e.py --strict", ".claude/hooks/t.py"])
    expect("this file's own steps are not direct checks",
           kit_checks_direct_steps(ci + "      - run: python3 scripts/check_direct_checks.py --verify\n"),
           ["scripts/a.py", "scripts/c.py", ".claude/hooks/t.py"])
    expect("a listed bare command does not cover its argument form",
           len(differences(ci2, ("scripts/a.py", "scripts/c.py", "scripts/e.py --strict"), ex)), 2)
    # --json-yaml and --run, against a throwaway tree
    import tempfile
    with tempfile.TemporaryDirectory(prefix="direct-checks-") as tmp:
        def vc(*a):
            return subprocess.run(["git", "-C", tmp, *a], capture_output=True, text=True)
        vc("init", "-q")
        with open(os.path.join(tmp, "good.json"), "w") as fh:
            fh.write('{"a": 1}')
        with open(os.path.join(tmp, "ok.py"), "w") as fh:
            fh.write("raise SystemExit(0)\n")
        with open(os.path.join(tmp, "bad.py"), "w") as fh:
            fh.write("raise SystemExit(1)\n")
        vc("add", ".")
        bad, n = json_yaml(tmp)
        expect("json-yaml: a tree of good files reports none", (bad, n >= 1), ([], True))
        with open(os.path.join(tmp, "broken.json"), "w") as fh:
            fh.write("{nope")
        vc("add", ".")
        bad, _n = json_yaml(tmp)
        expect("json-yaml: a malformed file is named", [b.split(":")[0] for b in (bad or [])], ["broken.json"])
        quiet = open(os.devnull, "w")
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = quiet
        try:
            rc_ok = cmd_run(tmp, ("ok.py",), json_yaml_too=False)
            rc_bad = cmd_run(tmp, ("ok.py", "bad.py"), json_yaml_too=False)
            rc_gone = cmd_run(tmp, ("ok.py", "gone.py"), json_yaml_too=False)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            quiet.close()
        expect("run: all pass is 0, one failure is 1, a missing file is 2", (rc_ok, rc_bad, rc_gone), (0, 1, 2))
    if failures:
        print("%d FAILED" % len(failures))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selftest" in args:
        sys.path.insert(0, HERE)
        from scratch_run import contained       # its temp folders go when it ends
        sys.exit(contained(selftest, "direct-checks-selftest-run-"))
    if "--run" in args:
        sys.exit(cmd_run())
    if "--json-yaml" in args:
        sys.exit(cmd_json_yaml())
    if "--verify" in args:
        sys.exit(cmd_verify())
    print(__doc__)
    sys.exit(2)
