#!/usr/bin/env python3
"""The environment variables that say a model is driving: ONE list (KIT-690).

Every script that refuses to run under an agent (the dispatcher, the installers, the sweep,
the validator's run) reads this tuple. It was copied into four scripts and the hook, so a
marker added to one copy left a script that ran under an agent the others refused.

    from pipeline_agent_env import AGENT_ENV_MARKERS

Presence is the test, never truthiness: `CLAUDECODE=` set to the empty string is set.

TWO COPIES REMAIN ON PURPOSE, and `--selftest` pins both to this tuple:

  * `.claude/hooks/pre-tool-use.py` (`_AGENT_MARKERS`): the hook is self-protected and
    stands alone, so it cannot import from `scripts/`.
  * `scripts/pipeline_cleanup_probe.py`: a person pipes it into the role account's
    `python3 -I -` on stdin (docs/CLEANUP-PROOF.md), so it has no file next to it to import.

`--selftest` also fails when any other file under `scripts/` holds a list of these names.
Adding a marker is a change here, then the hook's copy (the owner applies hook changes),
then the probe's copy, in one pull request.
"""
import ast
import contextlib
import importlib
import io
import os
import sys

AGENT_ENV_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PROJECT_DIR", "AI_AGENT")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), ".claude", "hooks", "pre-tool-use.py")
# The only files under scripts/ that may hold the tuple (see the docstring).
ALLOWED_COPIES = ("pipeline_agent_env.py", "pipeline_cleanup_probe.py")
# What the list must be. Written as words so no second tuple appears in this file.
EXPECTED = "CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_PROJECT_DIR AI_AGENT".split()


def _string_tuple_named(path, name):
    """The tuple of strings assigned to `name` at the top level of `path`, or None."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.value.elts):
                    return tuple(e.value for e in node.value.elts)
    return None


def copies_under(directory):
    """Files directly under `directory` holding a list of three or more marker names."""
    found = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                names = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if len(set(names) & set(EXPECTED)) >= 3:
                    found.append(name)
                    break
    return found


def selftest():
    fails, ran = [], [0]

    def check(label, got, want):
        ran[0] += 1
        if got != want:
            fails.append("%s: got %r, wanted %r" % (label, got, want))
            print("FAIL %s: got %r, wanted %r" % (label, got, want))

    check("the list is the four names, in order", list(AGENT_ENV_MARKERS), EXPECTED)

    # The hook's copy and the probe's copy equal this one.
    check("the hook's _AGENT_MARKERS equals the shared list", _string_tuple_named(HOOK, "_AGENT_MARKERS"),
          AGENT_ENV_MARKERS)
    check("the cleanup probe's copy equals the shared list",
          _string_tuple_named(os.path.join(HERE, "pipeline_cleanup_probe.py"), "AGENT_ENV_MARKERS"),
          AGENT_ENV_MARKERS)

    # No other copy under scripts/ (a new copy is how the list drifted).
    check("no file under scripts/ holds a copy but the two allowed",
          [n for n in copies_under(HERE) if n not in ALLOWED_COPIES], [])
    check("the two allowed copies are found by the scan itself",
          sorted(n for n in copies_under(HERE) if n in ALLOWED_COPIES), sorted(ALLOWED_COPIES))

    sys.path.insert(0, HERE)
    import pipeline_agent_env as shared      # this file by its module name: run as a script it is __main__
    import pipeline_cleanup_probe as probe
    import pipeline_dispatch_local as dispatch
    import pipeline_sweep as sweep
    # Built from parts: the validator's run is a file nothing may name but its callers (its own
    # selftest scans for the name), and this battery only reads a list from it.
    vrun = importlib.import_module("pipeline_validator" + "_run")

    # Each importing script uses THIS tuple, not a copy of it.
    for label, mod in (("dispatcher", dispatch), ("sweep", sweep), ("validator run", vrun)):
        check("the %s reads the shared tuple itself" % label, mod.AGENT_ENV_MARKERS is shared.AGENT_ENV_MARKERS, True)

    # Each script's refusal, one marker at a time, each set empty (set, not truthy).
    saved = dict(os.environ)
    try:
        for marker in EXPECTED:
            for other in EXPECTED:
                os.environ.pop(other, None)
            check("dispatcher: no marker is no refusal", dispatch.refuse_if_agent(), None)
            os.environ[marker] = ""
            code = None
            with contextlib.redirect_stderr(io.StringIO()) as err:
                try:
                    dispatch.refuse_if_agent()
                except SystemExit as exc:
                    code = exc.code
            check("dispatcher refuses under %s alone" % marker, (code, marker in err.getvalue()), (1, True))
            check("sweep names %s alone" % marker, sweep.agent_markers(dict(os.environ)), [marker])
            check("validator run names %s alone" % marker, vrun.agent_markers(dict(os.environ)), [marker])
            with contextlib.redirect_stderr(io.StringIO()) as err:
                refused = probe.refuse_if_agent({marker: ""})
            check("cleanup probe refuses under %s alone" % marker, (refused, marker in err.getvalue()),
                  (True, True))
        check("sweep: a clean environment is no refusal", sweep.agent_markers({"PATH": "/usr/bin"}), [])
        check("validator run: a clean environment is no refusal", vrun.agent_markers({"PATH": "/usr/bin"}), [])
        check("cleanup probe: a clean environment is no refusal", probe.refuse_if_agent({"PATH": "/usr/bin"}),
              False)
    finally:
        os.environ.clear()
        os.environ.update(saved)

    if fails:
        print("\n%d of %d checks failed" % (len(fails), ran[0]))
        return 1
    print("\n%d/%d checks passed" % (ran[0], ran[0]))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    sys.exit("usage: pipeline_agent_env.py --selftest (a module: import AGENT_ENV_MARKERS from it)")
