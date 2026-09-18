#!/usr/bin/env python3
"""Compare, and on request refresh, this machine's user-scope skills against the kit.

Claude Code loads skills from two places: a project's own `.claude/skills/`, and
the user's `~/.claude/skills/`. A project instantiated from the kit without a
skills directory runs whatever copy sits at user scope — and that copy is
machine-local. Nothing updates it when the kit changes, so it drifts quietly.
A planning run in such a project would load a stale procedure and nothing
would say so.

This script says so.

  check (default)  For every skill the kit ships, report whether the user-scope
                   copy is IDENTICAL, DIFFERS (with a count of differing lines),
                   or MISSING. Also report user-scope skills the kit does not
                   ship, which it never touches. Read-only.
  --apply          Replace each DIFFERS or MISSING copy with the kit's. A skill
                   is replaced whole, never merged, so a hand edit at user scope
                   is lost — the check lists those first. Refuses in an agent
                   environment: a session rewriting the instructions future
                   sessions load is a session editing its own supervision.

Machine-local by construction. It writes only under the user's own home, and
only the skill directories the kit ships.

Usage:
    sync_user_skills.py [--kit DIR] [--user-skills DIR] [--apply]
    sync_user_skills.py --selftest

Exit: 0 every kit skill is identical (or --apply made it so) · 1 drift found
      (check) or a copy failed (--apply) · 2 usage · 3 refused (agent env)
"""
import argparse
import difflib
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Imported, never copied, so this script and the dispatcher cannot disagree about
# what an agent environment looks like.
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

EX_OK, EX_DRIFT, EX_USAGE, EX_REFUSED = 0, 1, 2, 3
IDENTICAL, DIFFERS, MISSING = "IDENTICAL", "DIFFERS", "MISSING"


def _files(root):
    out = {}
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            if name.endswith(".pyc"):
                continue
            path = os.path.join(base, name)
            out[os.path.relpath(path, root)] = path
    return out


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def differing_lines(kit_dir, user_dir):
    """Lines added or removed across every file in the two skill directories."""
    kit, user = _files(kit_dir), _files(user_dir)
    count = 0
    for rel in sorted(set(kit) | set(user)):
        a = _read(user[rel]).decode("utf-8", "replace").splitlines() if rel in user else []
        b = _read(kit[rel]).decode("utf-8", "replace").splitlines() if rel in kit else []
        if a == b:
            continue
        count += sum(1 for ln in difflib.unified_diff(a, b, lineterm="", n=0)
                     if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
    return count


def compare(kit_skills, user_skills):
    """[(name, status, differing_line_count)] for every skill the kit ships, and
    the names of user-scope skills the kit does not ship."""
    rows = []
    shipped = sorted(d for d in os.listdir(kit_skills)
                     if os.path.isdir(os.path.join(kit_skills, d)))
    for name in shipped:
        kit_dir = os.path.join(kit_skills, name)
        user_dir = os.path.join(user_skills, name)
        if not os.path.isdir(user_dir):
            rows.append((name, MISSING, 0))
            continue
        n = differing_lines(kit_dir, user_dir)
        rows.append((name, IDENTICAL if n == 0 else DIFFERS, n))
    extra = []
    if os.path.isdir(user_skills):
        extra = sorted(d for d in os.listdir(user_skills)
                       if os.path.isdir(os.path.join(user_skills, d)) and d not in shipped)
    return rows, extra


def agent_markers_present(env=None):
    env = os.environ if env is None else env
    return [m for m in AGENT_ENV_MARKERS if m in env]  # presence, not truthiness


def apply_sync(kit_skills, user_skills, rows):
    """Replace every DIFFERS/MISSING skill whole. Copy to a sibling temp dir
    first, then swap, so a failed copy never leaves a half-written skill."""
    failed = []
    os.makedirs(user_skills, exist_ok=True)
    for name, status, _n in rows:
        if status == IDENTICAL:
            continue
        dest = os.path.join(user_skills, name)
        stage = tempfile.mkdtemp(prefix=".%s-" % name, dir=user_skills)
        try:
            staged = os.path.join(stage, name)
            shutil.copytree(os.path.join(kit_skills, name), staged,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if os.path.isdir(dest):
                old = os.path.join(stage, name + ".old")
                os.rename(dest, old)
                os.rename(staged, dest)
                shutil.rmtree(old)
            else:
                os.rename(staged, dest)
        except OSError as exc:
            failed.append((name, str(exc)))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    return failed


def report(rows, extra, user_skills):
    print("User-scope skills at %s, compared with the kit:" % user_skills)
    for name, status, n in rows:
        print("  %-16s %-10s %s" % (name, status, "%d lines" % n if status == DIFFERS else ""))
    for name in extra:
        print("  %-16s %-10s not shipped by the kit; never touched" % (name, "EXTRA"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kit", default=os.path.join(HERE, os.pardir),
                    help="the kit checkout to copy from (default: this script's own)")
    ap.add_argument("--user-skills", default=os.path.expanduser("~/.claude/skills"),
                    help="the user-scope skills directory (default ~/.claude/skills)")
    ap.add_argument("--apply", action="store_true",
                    help="replace every differing or missing skill with the kit's copy")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

    kit_skills = os.path.join(os.path.abspath(args.kit), ".claude", "skills")
    if not os.path.isdir(kit_skills):
        print("no skills directory at %s — is --kit a kit checkout?" % kit_skills,
              file=sys.stderr)
        return EX_USAGE
    if args.apply:
        found = agent_markers_present()
        if found:
            print("REFUSED: --apply rewrites the instructions every future session on "
                  "this machine loads, and this is an agent environment (%s set). A "
                  "person runs it, in a terminal. There is no override."
                  % ", ".join(found), file=sys.stderr)
            return EX_REFUSED

    rows, extra = compare(kit_skills, args.user_skills)
    report(rows, extra, args.user_skills)
    drift = [r for r in rows if r[1] != IDENTICAL]
    if not drift:
        print("Every skill the kit ships is identical at user scope.")
        return EX_OK
    if not args.apply:
        print("%d of %d differ or are missing. A person refreshes them with:"
              % (len(drift), len(rows)))
        print("    python3 %s --apply" % os.path.join("scripts", os.path.basename(__file__)))
        print("That replaces each one whole: a hand edit at user scope is lost.")
        return EX_DRIFT
    failed = apply_sync(kit_skills, args.user_skills, rows)
    for name, why in failed:
        print("  could not replace %s: %s" % (name, why), file=sys.stderr)
    after, _ = compare(kit_skills, args.user_skills)
    left = [r for r in after if r[1] != IDENTICAL]
    if failed or left:
        print("%d skill(s) still differ after --apply." % len(left), file=sys.stderr)
        return EX_DRIFT
    print("Replaced %d skill(s). Every skill the kit ships is now identical at user scope."
          % len(drift))
    return EX_OK


def selftest():
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    with tempfile.TemporaryDirectory() as tmp:
        kit = os.path.join(tmp, "kit")
        ks = os.path.join(kit, ".claude", "skills")
        us = os.path.join(tmp, "home", "skills")
        write(os.path.join(ks, "same", "SKILL.md"), "a\nb\n")
        write(os.path.join(us, "same", "SKILL.md"), "a\nb\n")
        write(os.path.join(ks, "drift", "SKILL.md"), "a\nb\nc\n")
        write(os.path.join(ks, "drift", "helper.py"), "x = 1\n")
        write(os.path.join(us, "drift", "SKILL.md"), "a\nOLD\n")
        write(os.path.join(ks, "absent", "SKILL.md"), "new\n")
        write(os.path.join(us, "mine", "SKILL.md"), "the user's own\n")

        rows, extra = compare(ks, us)
        got = dict((n, s) for n, s, _ in rows)
        check("status-identical", got.get("same"), IDENTICAL)
        check("status-differs", got.get("drift"), DIFFERS)
        check("status-missing", got.get("absent"), MISSING)
        check("extra-listed-not-compared", extra, ["mine"])
        # a missing FILE inside a present skill counts: `helper.py` is one line,
        # `OLD` -> `b` is two, `c` is one.
        check("differing-line-count", dict((n, c) for n, _s, c in rows)["drift"], 4)

        # check mode is read-only and exits DRIFT
        saved = dict(os.environ)
        try:
            for m in AGENT_ENV_MARKERS:
                os.environ.pop(m, None)
            import io
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["--kit", kit, "--user-skills", us])
            check("check-exits-drift", code, EX_DRIFT)
            check("check-changed-nothing", _read(os.path.join(us, "drift", "SKILL.md")),
                  b"a\nOLD\n")

            # apply refuses in an agent environment, presence not truthiness
            os.environ[AGENT_ENV_MARKERS[0]] = ""
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["--kit", kit, "--user-skills", us, "--apply"])
            check("apply-refused-in-agent-env", code, EX_REFUSED)
            check("refusal-changed-nothing", os.path.isdir(os.path.join(us, "absent")), False)
            os.environ.pop(AGENT_ENV_MARKERS[0], None)

            # apply, as a person: drift and missing replaced whole, extra untouched
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["--kit", kit, "--user-skills", us, "--apply"])
            check("apply-exit-ok", code, EX_OK)
            after, extra2 = compare(ks, us)
            check("apply-all-identical", sorted(set(s for _n, s, _c in after)), [IDENTICAL])
            check("apply-copied-new-file",
                  os.path.isfile(os.path.join(us, "drift", "helper.py")), True)
            check("apply-left-user-skill-alone",
                  _read(os.path.join(us, "mine", "SKILL.md")), b"the user's own\n")
            check("apply-no-staging-left",
                  sorted(d for d in os.listdir(us) if d.startswith(".")), [])
            check("extra-still-extra", extra2, ["mine"])

            # a second apply is a no-op
            with contextlib.redirect_stdout(io.StringIO()):
                check("second-apply-ok", main(["--kit", kit, "--user-skills", us,
                                               "--apply"]), EX_OK)
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                check("bad-kit-usage", main(["--kit", os.path.join(tmp, "nope"),
                                             "--user-skills", us]), EX_USAGE)
        finally:
            os.environ.clear()
            os.environ.update(saved)

    check("markers-imported", len(AGENT_ENV_MARKERS) >= 3, True)
    if failures:
        print("FAIL: sync_user_skills selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: sync_user_skills selftest (%d cases)" % cases[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
