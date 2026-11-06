#!/usr/bin/env python3
"""Pin core.hooksPath to an absolute path, so every worktree runs the pre-commit hook (KIT-420).

    python3 scripts/pin_hooks_path.py            # what `npm install` runs after husky
    python3 scripts/pin_hooks_path.py --selftest # the battery CI runs

WHY. husky 9's `prepare` runs `git config core.hooksPath .husky/_`, a RELATIVE path, which git
reads from the top of the worktree a commit runs in. `.husky/_` is gitignored, so it exists only
where `npm install` ran. A worktree made after that has no `.husky/_/pre-commit`, git finds no
hook, and the branch check, the forbidden-path check and the secret scan are all skipped, with
no message saying so. Sessions commit in worktrees, so the local catch was missing for exactly
the commits it exists for.

WHAT IT DOES. After husky has written `.husky/_`, it sets core.hooksPath to the ABSOLUTE path of
the MAIN checkout's `.husky/_` (found through git's common dir), so every worktree runs that one
copy. When the main checkout has none, an ordinary checkout pins its own; a LINKED worktree does
not, because the setting is shared by every checkout and a worktree's folder goes when it does:
it says to run `npm install` in the main checkout. The hook itself resolves its tools through the common dir
(docs/LESSONS.md), so running it with a worktree as its cwd is what it is built for.

Exit 0 = pinned, already pinned, or nothing to pin (husky off, not a git checkout: said out loud).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def target_for(cwd):
    """(path, why-not): the absolute .husky/_ every worktree should use, or (None, reason)."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)
    if common.returncode != 0:
        return None, "not inside a git checkout"
    common = common.stdout.strip()
    here = git("rev-parse", "--show-toplevel", cwd=cwd).stdout.strip()
    main_top = os.path.dirname(common) if os.path.basename(common) == ".git" else None
    if main_top and os.path.isdir(os.path.join(main_top, ".husky", "_")):
        return os.path.realpath(os.path.join(main_top, ".husky", "_")), None
    own = os.path.join(here, ".husky", "_") if here else None
    # A linked worktree's folder goes when it does, and the setting is shared by every checkout.
    linked = bool(main_top and here) and os.path.realpath(here) != os.path.realpath(main_top)
    if own and os.path.isdir(own):
        if linked:
            return None, ("the main checkout has no .husky/_ and this is a linked worktree, whose folder "
                          "would disappear with it; run `npm install` in the main checkout instead")
        return os.path.realpath(own), None
    return None, "no .husky/_ here (husky did not run, e.g. HUSKY=0)"


def pin(cwd):
    """(changed, message)."""
    target, why = target_for(cwd)
    if target is None:
        return False, "core.hooksPath not pinned: " + why
    current = git("config", "--get", "core.hooksPath", cwd=cwd).stdout.strip()
    if current == target:
        return False, "core.hooksPath is already %s" % target
    done = git("config", "core.hooksPath", target, cwd=cwd)
    if done.returncode != 0:
        raise RuntimeError("could not set core.hooksPath: %s" % done.stderr.strip())
    return True, "core.hooksPath pinned to %s (every worktree runs that hook)" % target


def selftest():
    import tempfile
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    os.environ.update(env)
    with tempfile.TemporaryDirectory(prefix="pin-hooks-") as tmp:
        tmp = os.path.realpath(tmp)
        main = os.path.join(tmp, "main")
        os.makedirs(os.path.join(main, ".husky", "_"))
        mark = os.path.join(tmp, "hook-ran")
        hook = os.path.join(main, ".husky", "_", "pre-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho ran >> '%s'\n" % mark)
        os.chmod(hook, 0o755)
        git("init", "-q", "-b", "main", main, cwd=tmp)
        with open(os.path.join(main, ".gitignore"), "w") as fh:
            fh.write(".husky/_/\n")
        with open(os.path.join(main, "a.txt"), "w") as fh:
            fh.write("a\n")
        git("add", "-A", cwd=main)
        git("commit", "-q", "--no-verify", "-m", "seed", cwd=main)

        def commit_in(wt, name):
            with open(os.path.join(wt, name), "w") as fh:
                fh.write(name)
            git("add", name, cwd=wt)
            return git("commit", "-q", "-m", name, cwd=wt)

        def ran():
            return os.path.exists(mark) and open(mark).read().count("ran")

        # The defect, as husky 9 leaves it: a RELATIVE hooks path, and a worktree made afterwards.
        git("config", "core.hooksPath", ".husky/_", cwd=main)
        wt1 = os.path.join(tmp, "wt1")
        git("worktree", "add", "-q", "-b", "feat/one", wt1, cwd=main)
        commit_in(wt1, "one.txt")
        expect(not ran(), "control: a relative hooks path must NOT run the hook in a worktree "
                          "(this is the defect; if it ran, the case below proves nothing)")

        # The fix: pinned absolute, the SAME worktree and a new one both run the main copy.
        changed, msg = pin(main)
        expect(changed and git("config", "--get", "core.hooksPath", cwd=main).stdout.strip()
               == os.path.join(main, ".husky", "_"), f"pinned to the main checkout's .husky/_: {msg}")
        commit_in(wt1, "two.txt")
        expect(ran() == 1, "a worktree made BEFORE the pin runs the hook after it")
        wt2 = os.path.join(tmp, "wt2")
        git("worktree", "add", "-q", "-b", "feat/two", wt2, cwd=main)
        commit_in(wt2, "three.txt")
        expect(ran() == 2, "a worktree made AFTER the pin runs the hook")
        expect(pin(main)[0] is False and "already" in pin(main)[1], "pinning twice changes nothing")
        # Run from inside a worktree: still the main checkout's copy.
        git("config", "core.hooksPath", ".husky/_", cwd=main)
        expect(pin(wt2)[0] and git("config", "--get", "core.hooksPath", cwd=main).stdout.strip()
               == os.path.join(main, ".husky", "_"), "run from a worktree, it pins the MAIN copy")

        # An install in a worktree that has its OWN .husky/_ while the main checkout has one too:
        # the main checkout's copy wins, because the worktree is the one that gets deleted.
        os.makedirs(os.path.join(wt1, ".husky", "_"))
        git("config", "core.hooksPath", ".husky/_", cwd=main)
        pin(wt1)
        expect(git("config", "--get", "core.hooksPath", cwd=main).stdout.strip()
               == os.path.join(main, ".husky", "_"), "with a .husky/_ in both, the main checkout's wins")

        # Nothing to pin is said, not silent: no .husky/_ anywhere, and not a repo at all.
        bare = os.path.join(tmp, "nohusky")
        git("init", "-q", "-b", "main", bare, cwd=tmp)
        changed, msg = pin(bare)
        expect(not changed and "not pinned" in msg and "HUSKY=0" in msg, f"no .husky/_: {msg}")
        plain = os.path.join(tmp, "plain")
        os.makedirs(plain)
        changed, msg = pin(plain)
        expect(not changed and "not inside a git checkout" in msg, f"not a repo: {msg}")
        # An install run only inside a worktree, when the main checkout has no .husky/_.
        solo = os.path.join(tmp, "solo")
        git("init", "-q", "-b", "main", solo, cwd=tmp)
        with open(os.path.join(solo, "x"), "w") as fh:
            fh.write("x")
        git("add", "x", cwd=solo)
        git("commit", "-q", "-m", "x", cwd=solo)
        swt = os.path.join(tmp, "solo-wt")
        git("worktree", "add", "-q", "-b", "feat/s", swt, cwd=solo)
        os.makedirs(os.path.join(swt, ".husky", "_"))
        changed, msg = pin(swt)
        expect(not changed and "linked worktree" in msg and not git("config", "--get", "core.hooksPath", cwd=solo).stdout.strip(),
               f"a linked worktree's folder is never pinned for everyone: {msg}")
        # An ordinary checkout with its own .husky/_ (and the main checkout lacking none) pins itself.
        os.makedirs(os.path.join(solo, ".husky", "_"))
        changed, msg = pin(solo)
        expect(changed and os.path.join(solo, ".husky", "_") in msg, f"a lone checkout pins its own .husky/_: {msg}")
    if failures:
        print("pin_hooks_path selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("pin_hooks_path selftest: OK")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        sys.path.insert(0, HERE)
        from scratch_run import contained   # its temp folders go when it ends
        return contained(selftest, "pin-hooks-selftest-run-")
    try:
        _changed, message = pin(os.getcwd())
    except RuntimeError as exc:
        print("pin_hooks_path:", exc, file=sys.stderr)
        return 2
    print("pin_hooks_path:", message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
