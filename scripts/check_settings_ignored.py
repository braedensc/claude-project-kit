#!/usr/bin/env python3
"""Every filled-in settings file is git-ignored (KIT-981).

    python3 scripts/check_settings_ignored.py            # check this repository
    python3 scripts/check_settings_ignored.py --selftest # the battery CI runs

WHY. Each installer reads a `<name>.conf` an owner fills in from the committed
`<name>.conf.example`. The filled-in file holds one deployment's facts, so it must never reach the
public template by a careless `git add`. In Nov 2026 four of them (runner, queue, watcher,
acceptance) sat untracked but not ignored, and nothing noticed.

HOW. It lists the tracked `*.conf.example` files at the repository root, copies the tracked
`.gitignore` into a private temporary repository, and asks `git check-ignore` there about each
filled-in name. The real checkout's state (its untracked files, its index) is never consulted.
The JSON examples (`runner.example.json`, `validator-settings.example.json`, `delivery.example.json`)
are not covered: the installers keep the filled-in copies outside the repository, and
`delivery.json` is committed on purpose.

Exit 0 = every filled-in name is ignored. Exit 1 = one is not (named). Exit 2 = could not check
(git missing, no examples found): never a pass.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from scratch_run import contained  # noqa: E402

SUFFIX = ".example"


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def examples(root):
    """The tracked root-level `*.conf.example` names, or None when git cannot say."""
    r = _git(["ls-files", "-z"], root)
    if r.returncode != 0:
        return None
    names = [n for n in r.stdout.split("\0") if n and "/" not in n and n.endswith(".conf" + SUFFIX)]
    return sorted(names)


def unignored(gitignore_text, filled):
    """The names in `filled` that `gitignore_text` does not ignore. None when git cannot check."""
    with tempfile.TemporaryDirectory(prefix="settings-ignored-") as tmp:
        if _git(["init", "-q"], tmp).returncode != 0:
            return None
        # Judge only the copied .gitignore: a person's global ignore file must not hide a missing line.
        no_global = ["-c", "core.excludesFile=" + os.devnull]
        with open(os.path.join(tmp, ".gitignore"), "w") as fh:
            fh.write(gitignore_text)
        out = []
        for name in filled:
            r = _git(no_global + ["check-ignore", "-q", "--no-index", name], tmp)
            if r.returncode == 1:
                out.append(name)
            elif r.returncode != 0:
                return None
        return out


def check(root):
    ex = examples(root)
    if not ex:
        print("check_settings_ignored: found no tracked *.conf.example (git unavailable?): NOT CHECKED")
        return 2
    try:
        with open(os.path.join(root, ".gitignore")) as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""  # no .gitignore ignores nothing: every example's file is then named as not ignored
    except OSError as e:
        print("check_settings_ignored: cannot read .gitignore (%s): NOT CHECKED" % e)
        return 2
    filled = [n[: -len(SUFFIX)] for n in ex]
    bad = unignored(text, filled)
    if bad is None:
        print("check_settings_ignored: git check-ignore failed: NOT CHECKED")
        return 2
    for n in bad:
        print("NOT IGNORED: %s (its example %s is committed): add `%s` to .gitignore" % (n, n + SUFFIX, n))
    if bad:
        return 1
    print("check_settings_ignored: %d filled-in settings files, all ignored" % len(filled))
    return 0


def selftest():
    failures = []

    def expect(name, got, want):
        ok = got == want
        print("[%s] %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            failures.append(name)

    with open(os.path.join(ROOT, ".gitignore")) as fh:
        real = fh.read()
    ex = examples(ROOT)
    expect("the repository has settings examples", bool(ex), True)
    filled = [n[: -len(SUFFIX)] for n in (ex or [])]
    expect("this repository's .gitignore ignores every one", unignored(real, filled), [])
    for must in ("runner.conf", "queue.conf", "watcher.conf", "acceptance.conf"):
        expect("%s is among the checked names" % must, must in filled, True)

    # Mutants: dropping any one filled-in line must be caught, and only that name.
    lines = real.split("\n")
    for name in filled:
        mutated = "\n".join(l for l in lines if l.strip() != name)
        expect("dropping the %s line is caught" % name, unignored(mutated, filled), [name])
    expect("an empty .gitignore catches them all", unignored("", filled), filled)
    expect("a pattern that does not match is caught", unignored("*.conff\n", ["a.conf"]), ["a.conf"])
    expect("a glob that does match passes", unignored("*.conf\n", ["a.conf"]), [])

    # Only root-level tracked examples count; a missing git repo is not a pass.
    with tempfile.TemporaryDirectory(prefix="settings-ignored-root-") as tmp:
        expect("a folder that is no repository is not a pass", check(tmp), 2)
        _git(["init", "-q"], tmp)
        expect("a repository with no examples is not a pass", check(tmp), 2)
        for rel in ("a.conf.example", "sub/b.conf.example", "c.example.json"):
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)) or tmp, exist_ok=True)
            open(os.path.join(tmp, rel), "w").close()
        _git(["add", "-A"], tmp)
        expect("only root *.conf.example are listed", examples(tmp), ["a.conf.example"])
        expect("an example with no ignore line fails", check(tmp), 1)
        with open(os.path.join(tmp, ".gitignore"), "w") as fh:
            fh.write("a.conf\n")
        expect("and passes once ignored", check(tmp), 0)

    if failures:
        print("check_settings_ignored selftest: FAIL (%d)" % len(failures))
        return 1
    print("check_settings_ignored selftest: OK")
    return 0


def main(argv):
    if "--selftest" in argv:
        return contained(selftest, "settings-ignored-selftest-run-")
    return check(ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
