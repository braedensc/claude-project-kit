#!/usr/bin/env python3
"""The secret scan CI runs over every tracked file, and the proof it reads what it must.

    python3 scripts/check_secret_scan.py --selftest   # the check CI runs after `npm ci`

Layer 3's secret scan (docs/SECURITY.md) is one command, written in three places that
must agree: the kit's CI step, the CI template a project starts from, and
`npm run lint:secrets`. It reads what git tracks (`git ls-files -z`), so an untracked
ignored file is never scanned, and it passes:

  --no-gitignore  secretlint skips a path a .gitignore rule covers even when the path is
                  named on its command line, so a tracked file under an ignore rule went
                  unscanned on every PR (KIT-400). Its parser takes the flag on Node 22.4
                  or newer; CI pins Node 22.
  --              ends secretlint's options, so a tracked file named `--help` or
                  `--output=x` is read as a path, not taken as an option that ends the
                  scan with exit 0 (KIT-400).

The rules the scan judges by are not the PR's to change: `.secretlintrc*` and every
`.secretlintignore*` are grader paths, so a PR touching one needs the `hooks-change`
label (scripts/check_grader_paths.py, and the template's own guard; KIT-401).

--selftest checks the three copies agree, then runs the command, with the real
secretlint, in throwaway repos holding a key-shaped value made at run time:
  - a tracked file a .gitignore rule covers, holding it, fails the scan;
  - a tracked file named `--help`, or `--output=report.txt`, beside one holding it fails
    the scan, and no report.txt is written;
  - a clean repo passes.

Exit 0 = every check held. Exit 1 = one failed. Exit 2 = it could not run (no secretlint,
or a Node too old for --no-gitignore): docs/PIPELINE-CONTRACT.md §13, could-not-run is
never reported as a pass.
"""
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The one command. CI and the template run it through `npx`; package.json's script runs it
# with node_modules/.bin on PATH.
COMMAND = "git ls-files -z | xargs -0 secretlint --secretlintrc .secretlintrc.json --no-gitignore --"
COPIES = ((".github/workflows/ci.yml", "yaml"), ("templates/workflows/ci.yml", "yaml"),
          ("package.json", "npm"))


def copies_found():
    """{path: the scan command it holds, or None}, normalized to the npm form."""
    found = {}
    for rel, kind in COPIES:
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            found[rel] = None
            continue
        if kind == "npm":
            line = (json.loads(text).get("scripts") or {}).get("lint:secrets")
        else:
            hits = re.findall(r"^\s*run:\s*(git ls-files -z \| xargs -0 .*secretlint.*)$", text, re.M)
            line = hits[0].replace("npx secretlint", "secretlint") if len(hits) == 1 else None
        found[rel] = line.strip() if line else None
    return found


def template_guard_pattern():
    """The `grep -E` pattern the CI template's hooks-change guard flags paths with."""
    try:
        with open(os.path.join(ROOT, "templates/workflows/ci.yml"), encoding="utf-8") as fh:
            m = re.search(r"SENSITIVE=.*grep -E '([^']+)'", fh.read())
    except OSError:
        return None
    return m.group(1) if m else None


def find_secretlint():
    """This checkout's secretlint, else the main checkout's (a worktree has no node_modules
    of its own), as the pre-commit hook finds it."""
    here = os.path.join(ROOT, "node_modules", ".bin", "secretlint")
    if os.access(here, os.X_OK):
        return here
    r = subprocess.run(["git", "-C", ROOT, "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True)
    if r.returncode == 0:
        main = os.path.join(os.path.dirname(r.stdout.strip()), "node_modules", ".bin",
                            "secretlint")
        if os.access(main, os.X_OK):
            return main
    return None


def key_value():
    # Built at run time: a key-shaped literal in this file would fail the very scan it tests.
    body = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    return "TOKEN=" + "lin" + "_api_" + body + "\n"


def scan(files, ignored=(), bindir=""):
    """(exit code, output, files left behind) of COMMAND in a throwaway repo holding
    `files` ({path: text}); paths in `ignored` are under a .gitignore rule, force-added."""
    root = tempfile.mkdtemp(prefix="secret-scan-")
    try:
        def git(*args):
            subprocess.run(["git", "-C", root, *args], check=True, capture_output=True,
                           env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                                "GIT_CONFIG_SYSTEM": os.devnull})
        git("init", "-q")
        shutil.copy(os.path.join(ROOT, ".secretlintrc.json"), root)
        with open(os.path.join(root, ".gitignore"), "w") as fh:
            fh.write("ignored/\n")
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)) or root, exist_ok=True)
            with open(os.path.join(root, rel), "w") as fh:
                fh.write(text)
        git("add", "--", ".secretlintrc.json", ".gitignore",
            *[rel for rel in files if rel not in ignored])
        if ignored:
            git("add", "-f", "--", *ignored)
        r = subprocess.run(["sh", "-c", COMMAND], cwd=root, capture_output=True, text=True,
                           timeout=300,
                           env={**os.environ, "PATH": bindir + os.pathsep + os.environ.get("PATH", "")})
        left = sorted(set(os.listdir(root)) - {".git", ".gitignore", ".secretlintrc.json"}
                      - {rel.split("/")[0] for rel in files})
        return r.returncode, r.stdout + r.stderr, left
    finally:
        shutil.rmtree(root, ignore_errors=True)


def selftest():
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else " — %s" % detail))

    found = copies_found()
    check("the kit's CI, the template's CI and lint:secrets run the same scan",
          all(line == COMMAND for line in found.values()), found)
    # The template's own guard is a grep in YAML (the kit's is check_grader_paths.py): it
    # must flag the scan's rules too, and nothing that only looks like them (KIT-401).
    pattern = template_guard_pattern()
    flagged = {p: bool(pattern and re.search(pattern, p)) for p in (
        ".secretlintrc.json", ".secretlintignore", "docs/deep/.secretlintignore",
        ".claude/hooks/pre-tool-use.py", "docs/secretlintignore.md", "src/app.ts")}
    check("the template's hooks-change guard flags the scan's rules, and only them",
          flagged == {".secretlintrc.json": True, ".secretlintignore": True,
                      "docs/deep/.secretlintignore": True, ".claude/hooks/pre-tool-use.py": True,
                      "docs/secretlintignore.md": False, "src/app.ts": False}, flagged)

    secretlint = find_secretlint()
    if not secretlint:
        print("COULD NOT RUN: no secretlint here or in the main checkout. Run `npm install` "
              "(CI runs this after `npm ci`).")
        return 2
    bindir = os.path.dirname(secretlint)
    probe = subprocess.run([secretlint, "--no-gitignore", "--help"], capture_output=True,
                           text=True)
    if probe.returncode != 0:
        print("COULD NOT RUN: this secretlint cannot take --no-gitignore (Node 22.4 or newer "
              "is needed): " + (probe.stdout + probe.stderr).strip()[-300:])
        return 2

    key = key_value()
    rc, said, _ = scan({"plain.md": "plain\n", "ignored/held.md": key},
                       ignored=("ignored/held.md",), bindir=bindir)
    check("a tracked file a .gitignore rule covers, holding a key, fails the scan", rc != 0,
          "exit %d: %s" % (rc, said.strip()[-300:]))
    for name in ("--help", "--output=report.txt"):
        rc, said, left = scan({name: "plain\n", "plain.md": key}, bindir=bindir)
        check("a tracked file named %s, beside one holding a key, fails the scan" % name,
              rc != 0 and "report.txt" not in left,
              "exit %d, left %s: %s" % (rc, left, said.strip()[-300:]))
    rc, said, _ = scan({"plain.md": "plain\n", "ignored/held.md": "plain too\n"},
                       ignored=("ignored/held.md",), bindir=bindir)
    check("a clean repo passes the scan", rc == 0, "exit %d: %s" % (rc, said.strip()[-300:]))

    passed = sum(results)
    print("%d/%d secret-scan checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    print(__doc__.strip().splitlines()[0])
    print("usage: python3 scripts/check_secret_scan.py --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
