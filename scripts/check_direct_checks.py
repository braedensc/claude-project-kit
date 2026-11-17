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

# The direct checks: script path, relative to the repository root.
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
)

# Direct steps in Kit checks that are deliberately not in DIRECT: path -> reason.
EXCLUDED = {
    ".claude/hooks/test_hooks.py": "a battery: the 3.9 job runs it as test:hooks",
    ".claude/hooks/test_guard_bypass.py": "a battery: the 3.9 job runs it as test:bypass",
}

STEP = re.compile(r"python3\s+([\w./-]+\.py)\s*$")
HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def kit_checks_direct_steps(ci_text):
    """The script paths Kit checks runs with no argument, in order (comments skipped)."""
    found, inside, in_jobs = [], False, False
    for line in ci_text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
        m = HEADER.match(line) if in_jobs else None
        if m:
            inside = m.group(1) == JOB
        if not inside or line.lstrip().startswith("#"):
            continue
        m = STEP.search(line.split("run:", 1)[-1].strip())
        if m:
            found.append(m.group(1))
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


def cmd_run():
    print("python: %s" % sys.version.split()[0])
    failed = []
    for path in DIRECT:
        if not os.path.isfile(os.path.join(ROOT, path)):
            print("COULD NOT RUN %s: file missing" % path, file=sys.stderr)
            return 2
        print("::group::%s" % path)
        code = subprocess.run([sys.executable, path], cwd=ROOT).returncode
        print("::endgroup::")
        print("%s %s" % ("ok  " if code == 0 else "FAIL", path))
        if code:
            failed.append(path)
    if cmd_json_yaml():
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

    ci = ("jobs:\n  kit-checks:\n    steps:\n      - name: a\n        run: python3 scripts/a.py\n"
          "      - run: |\n          python3 scripts/b.py --selftest\n          python3 scripts/c.py\n"
          "      # python3 scripts/ghost.py\n      - run: python3 scripts/d.py --selftest\n"
          "      - run: python3 .claude/hooks/t.py\n"
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
    expect("every listed script exists", all(os.path.isfile(os.path.join(ROOT, p)) for p in DIRECT), True)
    if failures:
        print("%d FAILED" % len(failures))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selftest" in args:
        sys.exit(selftest())
    if "--run" in args:
        sys.exit(cmd_run())
    if "--json-yaml" in args:
        sys.exit(cmd_json_yaml())
    if "--verify" in args:
        sys.exit(cmd_verify())
    print(__doc__)
    sys.exit(2)
