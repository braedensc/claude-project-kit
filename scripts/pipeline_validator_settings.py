#!/usr/bin/env python3
"""The validator's settings: what it may approve, as plain on/off switches (KIT-533, contract §22).
Design: docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md, decisions 3
and 4, as the owner decided them on 6 Nov 2026: no trust ladder, only switches a person turns.

    python3 scripts/pipeline_validator_settings.py show --settings-file PATH   every switch, and why
    python3 scripts/pipeline_validator_settings.py --selftest

WHAT IT HOLDS.  One small fields-only file, the owner's: one switch each for approving plans,
pieces starting and merges; the size limit, as the most changed lines and the most files; and one
switch each for the four human-only classes the owner may allow: credentials, billing and accounts,
production deploy settings, deleting data. Every part of the validator reads it through `load()`.

THE RULES, each one tested.
  * Absent is every switch off.
  * Anything else that is not a valid, trusted settings file is every switch off too, said in
    words: a file that does not parse, names another schema, gives a key twice, misses a key or
    adds one, is larger than a settings file, or cannot be read. So is a file a session could have
    written: one inside a git working tree, a link, writable by group or other, owned by another
    account, or in or under a folder others can write. It never means on.
  * No switch for the safety system: hooks, guards, CI and workflows, branch protection and
    rulesets, settings, the watcher, the validator's own limits. Those stay human, fixed in code.
    A file that names one, as a key anywhere, on or off, is refused: every switch off.
  * Nothing gains or loses trust by itself. Only the owner edits the file. This module reads it,
    and has no write path: it writes, removes and renames nothing, and starts no process.
"""
import argparse
import json
import os
import re
import stat
import sys

SETTINGS_SCHEMA = "pipeline-validator-settings/1"
SCHEMA_NAME = "pipeline-validator-settings"
APPROVE = ("plans", "pieces", "merges")
LIMITS = (("max_changed_lines", 100000), ("max_files", 10000))
ALLOW = ("credentials", "billing_and_accounts", "production_deploy", "deleting_data")
SECTIONS = (("approve", APPROVE), ("limits", tuple(k for k, _top in LIMITS)), ("allow", ALLOW))
MAX_BYTES = 64 * 1024
# The safety system, by the words a switch for it would carry. It is never a switch: a guard the
# system could approve for itself is theater. A key is matched word by word, in any case or style.
SAFETY_WORDS = frozenset(("safety", "hook", "hooks", "guard", "guards", "ci", "workflow",
                          "workflows", "ruleset", "rulesets", "setting", "settings", "watcher",
                          "validator", "grader", "graders", "limit", "limits"))
SAFETY_PAIRS = (("branch", "protection"),)
READ, ABSENT, MALFORMED, UNREADABLE, UNTRUSTED, REFUSED = (
    "read", "absent", "malformed", "unreadable", "untrusted", "refused")
EXIT_OK, EXIT_NOT_OBEYED, EXIT_USAGE = 0, 1, 2


class _Twice(ValueError):
    """A key given twice: which one a reader would keep is not a thing to guess."""


def off(state, why):
    """Every switch off and every limit 0, with the state and why. A fresh copy each time."""
    return {"state": state,
            "approve": dict((k, False) for k in APPROVE),
            "limits": dict((k, 0) for k, _top in LIMITS),
            "allow": dict((k, False) for k in ALLOW),
            "why": why}


def _words(key):
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return [w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w]


def names_safety(key):
    """True when a key names a part of the safety system."""
    words = _words(key)
    return (any(w in SAFETY_WORDS for w in words)
            or any(all(p in words for p in pair) for pair in SAFETY_PAIRS))


def _own_keys(where):
    if not where:
        return ("schema",) + tuple(name for name, _keys in SECTIONS)
    if len(where) == 1:
        return dict(SECTIONS).get(where[0], ())
    return ()


def safety_names(doc, where=()):
    """Every key in `doc`, at any depth, that names the safety system, as a dotted path. The
    file's own keys in their own places are not names: `limits` is the size section."""
    found = []
    if isinstance(doc, dict):
        own = _own_keys(where)
        for key, value in doc.items():
            if key not in own and names_safety(key):
                found.append(".".join(where + (key,)))
            found.extend(safety_names(value, where + (key,)))
    elif isinstance(doc, list):
        for item in doc:
            found.extend(safety_names(item, where + ("[]",)))
    return found


def problems(doc):
    """Everything wrong with a settings document, in words; [] when it is one. Never quotes a
    value from the file."""
    if not isinstance(doc, dict):
        return ["it is not a settings document"]
    if doc.get("schema") != SETTINGS_SCHEMA:
        return ["it names another schema"]
    out = []
    if set(doc) != set(_own_keys(())):
        out.append("it does not have exactly the keys: %s" % ", ".join(_own_keys(())))
    for section, keys in SECTIONS:
        part = doc.get(section)
        if not isinstance(part, dict) or set(part) != set(keys):
            out.append("its %s section does not have exactly: %s" % (section, ", ".join(keys)))
            continue
        for key in keys:
            value = part[key]
            if section == "limits":
                top = dict(LIMITS)[key]
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
                    out.append("limits.%s is not a whole number from 0 to %d" % (key, top))
            elif not isinstance(value, bool):
                out.append("%s.%s is not true or false" % (section, key))
    return out


def inside_git_worktree(path):
    """True when `path`, or a folder above it, holds `.git`: a session can write a worktree."""
    cur = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def open_folder(folder):
    """The first folder from `folder` up to the root that group or other can write, without the
    sticky bit, or None. Another account that can write any of them could put a folder of its
    own in the file's place. Raises OSError when one cannot be inspected."""
    cur = os.path.realpath(folder)
    while True:
        mode = os.stat(cur).st_mode
        if mode & (stat.S_IWGRP | stat.S_IWOTH) and not mode & stat.S_ISVTX:
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def trust_problem(path):
    """Why the file at `path` must not be obeyed, or None. A file a session could have written
    grants nothing. Raises OSError when the file cannot be inspected."""
    if os.path.islink(path):
        return "is a symbolic link"
    folder = os.path.dirname(path)
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        return "is not a plain file"
    if inside_git_worktree(folder):
        return "is inside a git working tree, where a session can write"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "is writable by group or other"
    if st.st_uid not in (0, os.geteuid()):
        return "is owned by another account"
    if open_folder(folder):
        return "sits in or under a folder that group or other can write"
    return None


def _no_twice(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise _Twice(key)
        seen[key] = value
    return seen


def load(path):
    """The settings at `path`: {"state", "approve", "limits", "allow", "why"}. `state` is `read`
    only for a valid, trusted file; every other state means every switch off and every limit 0,
    with `why` in words. It never means on."""
    if not isinstance(path, str) or not os.path.isabs(path):
        return off(UNTRUSTED, "the settings path is not an absolute path, so every switch is off")
    try:
        os.lstat(path)
    except FileNotFoundError:
        return off(ABSENT, "no settings file, so every switch is off")
    except OSError as exc:
        return off(UNREADABLE, "the settings file could not be read (%s), so every switch is off"
                   % (exc.strerror or type(exc).__name__))
    try:
        distrust = trust_problem(path)
        if not distrust:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read(MAX_BYTES + 1)
    except OSError as exc:
        return off(UNREADABLE, "the settings file could not be read (%s), so every switch is off"
                   % (exc.strerror or type(exc).__name__))
    except UnicodeDecodeError:
        return off(MALFORMED, "the settings file is not readable text, so every switch is off")
    if distrust:
        return off(UNTRUSTED, "the settings file %s, so it is not obeyed: every switch is off"
                   % distrust)
    if len(raw) > MAX_BYTES:
        return off(MALFORMED, "the settings file is larger than a settings file, so every switch "
                              "is off")
    try:
        doc = json.loads(raw, object_pairs_hook=_no_twice)
        named = (safety_names(doc) if isinstance(doc, dict)
                 and doc.get("schema") == SETTINGS_SCHEMA else [])
        bad = problems(doc)
    except _Twice:
        return off(MALFORMED, "the settings file gives a key twice, so every switch is off")
    except (ValueError, RecursionError):
        return off(MALFORMED, "the settings file is not readable JSON, so every switch is off")
    if named:
        return off(REFUSED, "the settings file names the safety system (%s). Changes to it are "
                            "never a switch: they stay the owner's, fixed in code. So every "
                            "switch is off" % ", ".join(named[:3]))
    if bad:
        return off(MALFORMED, "the settings file is not a settings file: %s. So every switch is "
                              "off" % bad[0])
    return {"state": READ,
            "approve": dict((k, doc["approve"][k]) for k in APPROVE),
            "limits": dict((k, doc["limits"][k]) for k, _top in LIMITS),
            "allow": dict((k, doc["allow"][k]) for k in ALLOW),
            "why": "read"}


def show_lines(settings):
    """What `show` prints: every switch, on or off, and why."""
    s = settings
    lines = (["read: the validator may approve only what is on below"] if s["state"] == READ
             else ["EVERY SWITCH IS OFF (%s): %s" % (s["state"], s["why"])])
    onoff = lambda v: "on" if v else "off"  # noqa: E731
    lines += ["  approve plans:              %s" % onoff(s["approve"]["plans"]),
              "  approve pieces starting:    %s" % onoff(s["approve"]["pieces"]),
              "  approve merges:             %s" % onoff(s["approve"]["merges"])]
    for key, label in (("max_changed_lines", "most changed lines"), ("max_files", "most files")):
        n = s["limits"][key]
        lines.append("  %-27s %d%s" % (label + ":", n, " (nothing passes)" if n == 0 else ""))
    for key in ALLOW:
        lines.append("  allow %-21s %s" % (key.replace("_", " ") + ":",
                                           "on" if s["allow"][key] else "off (stays the owner's)"))
    lines.append("  the safety system:          never a switch; it stays the owner's")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="The validator's settings: what it may approve.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("show", help="every switch, on or off, and why")
    p.add_argument("--settings-file", required=True)
    args = ap.parse_args(argv)
    if args.selftest:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from scratch_run import contained
        return contained(selftest, "validator-settings-selftest-")
    if args.cmd == "show":
        path = os.path.expanduser(args.settings_file)
        if not os.path.isabs(path):
            print("COULD NOT RUN: --settings-file must be an absolute path, or start with ~/.")
            return EXIT_USAGE
        got = load(path)
        print("\n".join(show_lines(got)))
        return EXIT_OK if got["state"] in (READ, ABSENT) else EXIT_NOT_OBEYED
    ap.print_help()
    return EXIT_USAGE


# ======================================================================================
# SELFTEST: nothing below the line above is run by a reader, and the source scan stops there.
# ======================================================================================
def selftest():
    import contextlib
    import copy
    import io
    import tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import check_schemas
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:260], str(want)[:260]))

    def all_off(s):
        return (not any(s["approve"].values()) and not any(s["allow"].values())
                and not any(s["limits"].values()))

    here = os.path.dirname(os.path.abspath(__file__))
    example_path = os.path.join(os.path.dirname(here), "validator-settings.example.json")
    with open(example_path, encoding="utf-8") as fh:
        example_text = fh.read()
    example = json.loads(example_text)
    all_on = {"schema": SETTINGS_SCHEMA,
              "approve": {"plans": True, "pieces": True, "merges": True},
              "limits": {"max_changed_lines": 400, "max_files": 20},
              "allow": {"credentials": True, "billing_and_accounts": True,
                        "production_deploy": True, "deleting_data": True}}
    seen = []                          # every result a case got, for the sweep at the end

    def got(p):
        result = load(p)
        seen.append(result)
        return result

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        folder = os.path.join(tmp, "validator")
        os.makedirs(folder, mode=0o700)
        path = os.path.join(folder, "settings.json")

        def put(content, mode=0o600, at=None):
            target = at or path
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            with open(target, "wb") as fh:
                fh.write(data)
            os.chmod(target, mode)
            return target

        # 1. Absent is every switch off, said.
        s = got(path)
        check("absent: every switch off, and said", (s["state"], all_off(s), s["why"]),
              (ABSENT, True, "no settings file, so every switch is off"))

        # 2. A valid file is obeyed: the loader has teeth, so "off" is a reading, not a constant.
        put(json.dumps(all_on))
        s = got(path)
        check("a valid file with every switch on reads every switch on, and its limits",
              (s["state"], s["approve"], s["limits"], s["allow"]),
              (READ, all_on["approve"], all_on["limits"], all_on["allow"]))
        check("...and the schema agrees it is a settings file",
              check_schemas.document_problems(all_on, SCHEMA_NAME), [])
        mixed = copy.deepcopy(all_on)
        mixed["approve"].update(pieces=False, merges=False)
        mixed["allow"].update(credentials=False, deleting_data=False)
        put(json.dumps(mixed))
        s = got(path)
        check("each switch is read on its own",
              (s["approve"], s["allow"]), (mixed["approve"], mixed["allow"]))

        # 3. The shipped example: every key, every key off, a valid settings file.
        put(example_text)
        s = got(path)
        check("the example reads, with every switch off and every limit 0",
              (s["state"], all_off(s)), (READ, True))
        check("the example names every key the file has, no more",
              (sorted(example), sorted(example["approve"]), sorted(example["limits"]),
               sorted(example["allow"])),
              (sorted(_own_keys(())), sorted(APPROVE), sorted(k for k, _t in LIMITS),
               sorted(ALLOW)))
        check("the example conforms to the schema",
              check_schemas.document_problems(example, SCHEMA_NAME), [])

        # 4. Anything that is not a valid settings file is every switch off, said in words.
        def doc_with(section, key, value):
            d = copy.deepcopy(all_on)
            if key is None:
                d[section] = value
            else:
                d[section][key] = value
            return json.dumps(d)

        twice = json.dumps(all_on).replace('"plans": true', '"plans": false, "plans": true')
        for label, text in (
                ("a file that is not JSON", "{no"),
                ("an empty file", ""),
                ("a JSON list", "[]"),
                ("another schema", json.dumps(dict(all_on, schema="pipeline-validator-settings/2"))),
                ("no schema at all", json.dumps(dict((k, v) for k, v in all_on.items()
                                                     if k != "schema"))),
                ("a key given twice", twice),
                ("a missing section", json.dumps(dict((k, v) for k, v in all_on.items()
                                                      if k != "allow"))),
                ("a missing switch", json.dumps(dict(all_on, approve={"plans": True,
                                                                      "pieces": True}))),
                ("an extra key", json.dumps(dict(all_on, notes="trust me"))),
                ("an extra switch", doc_with("approve", "everything", True)),
                ("a switch that is a string", doc_with("approve", "merges", "true")),
                ("a switch that is a number", doc_with("allow", "credentials", 1)),
                ("a switch that is null", doc_with("approve", "plans", None)),
                ("a section that is a list", doc_with("allow", None, [])),
                ("a limit that is a float", doc_with("limits", "max_files", 2.5)),
                ("a negative limit", doc_with("limits", "max_changed_lines", -1)),
                ("a limit past its top", doc_with("limits", "max_changed_lines", 100001)),
                ("a limit that is true", doc_with("limits", "max_files", True)),
                ("a limit that is NaN", json.dumps(all_on).replace('"max_files": 20',
                                                                   '"max_files": NaN')),
                ("a valid file padded past a settings file's size",
                 json.dumps(all_on) + " " * MAX_BYTES),
                ("text that is not UTF-8", b"\xff\xfe{}"),
                ("JSON nested past any depth", "[" * 5000 + "]" * 5000)):
            put(text)
            s = got(path)
            check("%s: every switch off, said" % label,
                  (s["state"], all_off(s), s["why"].endswith("every switch is off")),
                  (MALFORMED, True, True))
        put(twice)
        check("a key given twice says so", "twice" in got(path)["why"], True)
        put(json.dumps(all_on) + " " * MAX_BYTES)
        check("a file past the size says so", "larger" in got(path)["why"], True)
        put(json.dumps(dict(all_on, schema="x/1")))
        check("another schema says so", "another schema" in got(path)["why"], True)

        # 5. No switch for the safety system: a file that names one is refused, on or off.
        for label, section, key, value in (
                ("the safety system", "allow", "safety_system", True),
                ("hooks, switched off", "allow", "hooks", False),
                ("guards", "approve", "guards", True),
                ("CI", "allow", "CI", True),
                ("workflows", "allow", "workflows", True),
                ("branch protection", "allow", "branch_protection", True),
                ("branch protection, spelt another way", "allow", "BranchProtection", True),
                ("rulesets", "allow", "rulesets", True),
                ("settings", "allow", "settings", True),
                ("the watcher", "allow", "watcher", True),
                ("the validator's own limits", "allow", "validator_limits", True),
                ("its limits, under the limits", "limits", "limits_override", True),
                ("its own limits, at the top", None, "own-limits", True),
                ("hooks, nested under a switch", "allow", "credentials", {"hooks": True})):
            d = copy.deepcopy(all_on)
            if section is None:
                d[key] = value
            else:
                d[section][key] = value
            put(json.dumps(d))
            s = got(path)
            check("a file naming %s is refused: every switch off, said" % label,
                  (s["state"], all_off(s), "safety system" in s["why"]), (REFUSED, True, True))
            check("...and the schema refuses it too (%s)" % label,
                  bool(check_schemas.document_problems(d, SCHEMA_NAME)), True)
        check("the file's own sections are not names: `limits` is the size section",
              (safety_names(all_on), safety_names(example)), ([], []))

        # 6. A file a session could have written grants nothing.
        put(json.dumps(all_on))
        tree = os.path.join(tmp, "tree")
        os.makedirs(os.path.join(tree, ".git"))
        in_tree = put(json.dumps(all_on), at=os.path.join(tree, "settings.json"))
        link = os.path.join(folder, "linked.json")
        os.symlink(path, link)
        for label, p, word in (("inside a git working tree", in_tree, "git working tree"),
                               ("a symbolic link", link, "symbolic link"),
                               ("a folder in its place", folder, "plain file")):
            s = got(p)
            check("a settings file %s is not obeyed: every switch off, said" % label,
                  (s["state"], all_off(s), word in s["why"]), (UNTRUSTED, True, True))
        for label, mode in (("writable by group", 0o620), ("writable by other", 0o602)):
            put(json.dumps(all_on), mode=mode)
            s = got(path)
            check("a settings file %s is not obeyed" % label,
                  (s["state"], all_off(s), "group or other" in s["why"]), (UNTRUSTED, True, True))
        put(json.dumps(all_on))
        os.chmod(folder, 0o770)
        s = got(path)
        check("a settings file in a folder group can write is not obeyed",
              (s["state"], all_off(s), "folder" in s["why"]), (UNTRUSTED, True, True))
        os.chmod(folder, 0o700)
        above = os.path.join(tmp, "open-above")
        deep = os.path.join(above, "middle", "validator")
        os.makedirs(deep, mode=0o700)
        deep_file = put(json.dumps(all_on), at=os.path.join(deep, "settings.json"))
        check("a settings file whose folders only its owner can write is obeyed",
              got(deep_file)["state"], READ)
        os.chmod(above, 0o770)
        s = got(deep_file)
        check("a settings file two folders below one group can write is not obeyed",
              (s["state"], all_off(s), "under a folder" in s["why"]), (UNTRUSTED, True, True))
        os.chmod(above, 0o1777)
        check("...but a shared folder with the sticky bit, as /tmp has, is no reason",
              got(deep_file)["state"], READ)
        os.chmod(above, 0o700)
        for label, p in (("relative", "settings.json"), ("not a path", None)):
            s = got(p)
            check("a %s settings path is not obeyed" % label,
                  (s["state"], all_off(s)), (UNTRUSTED, True))
        if os.geteuid() != 0:
            os.chmod(path, 0o000)
            s = got(path)
            check("a settings file that cannot be read: every switch off, said",
                  (s["state"], all_off(s), "could not be read" in s["why"]),
                  (UNREADABLE, True, True))
            os.chmod(path, 0o600)
            os.chmod(folder, 0o000)
            try:
                s = got(path)
            finally:
                os.chmod(folder, 0o700)
            check("a settings folder that cannot be read: every switch off",
                  (s["state"], all_off(s)), (UNREADABLE, True))

        # 7. It never means on: every result that was not a valid file read, all off.
        not_read = [r for r in seen if r["state"] != READ]
        check("every state but read is every switch off and every limit 0",
              (len(not_read) > 40, all(all_off(r) for r in not_read)), (True, True))
        a = off(ABSENT, "x")
        a["approve"]["merges"] = True
        check("an all-off answer is a fresh copy: a reader that changes one changes no other",
              off(ABSENT, "x")["approve"]["merges"], False)

        # 8. show: what a person reads, and the exit code a script reads.
        def run_main(argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(argv)
            return code, out.getvalue()
        put(json.dumps(mixed))
        code, text = run_main(["show", "--settings-file", path])
        check("show: a valid file exits 0 and says what is on",
              (code, "approve plans:              on" in text,
               "approve merges:             off" in text, "never a switch" in text),
              (EXIT_OK, True, True, True))
        code, text = run_main(["show", "--settings-file", os.path.join(folder, "none.json")])
        check("show: no file exits 0 and says every switch is off",
              (code, text.startswith("EVERY SWITCH IS OFF (absent)")), (EXIT_OK, True))
        put("{no")
        code, text = run_main(["show", "--settings-file", path])
        check("show: a file that is not obeyed exits 1, and says why",
              (code, text.startswith("EVERY SWITCH IS OFF (malformed)")), (EXIT_NOT_OBEYED, True))
        code, _text = run_main(["show", "--settings-file", "settings.json"])
        check("show: a relative path is a usage error", code, EXIT_USAGE)

    # 9. The loader and the schema agree on the shape, case by case.
    for label, mutate in (
            ("another marker", lambda d: d.update(schema="pipeline-validator-settings/2")),
            ("no approve section", lambda d: d.pop("approve")),
            ("a string switch", lambda d: d["approve"].update(plans="yes")),
            ("a negative limit", lambda d: d["limits"].update(max_files=-1)),
            ("a limit past its top", lambda d: d["limits"].update(max_files=10001)),
            ("an extra switch", lambda d: d["allow"].update(everything=True))):
        d = copy.deepcopy(all_on)
        mutate(d)
        check("loader and schema both refuse %s" % label,
              (bool(problems(d)), bool(check_schemas.document_problems(d, SCHEMA_NAME))),
              (True, True))

    # 10. No write path: above the selftest line it writes, removes and renames nothing, and
    #     starts no process. Nothing in it can turn a switch on.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("os.replace", "os.rename", "os.unlink", "os.remove", "os.rmdir", "shutil",
              "os.makedirs", "os.mkdir", "os.chmod", "os.chown", "os.open(", "json.dump(",
              ".write(", "subprocess", "os.system", "socket", "urlopen", "eval(", "exec(")
    check("it has no write path, and starts no process", [b for b in banned if b in above], [])
    check("its one open() reads", (above.count("open("),
                                   "open(path, encoding=\"utf-8\")" in above), (1, True))

    if failures:
        for line in failures:
            print(line)
        print("validator settings selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator settings selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
