#!/usr/bin/env python3
"""Each kind of session the runner starts, as data (KIT-704; docs/RUNNER.md, "Each kind of session").

    python3 scripts/pipeline_session_kinds.py --check       the kit's own kinds: load them, or say what is wrong
    python3 scripts/pipeline_session_kinds.py --selftest

WHAT IT IS.  One file per kind in `session-kinds/`: code, fix, conflict, review, plan and acceptance.
Each file says what a session of that kind is given and what walls it runs behind:

    mode          the pin's session_mode
    branch        {type, word}: the workspace's branch (word "title" is the ticket title's slug);
                  null for a kind that is only ever a resume, which keeps its run's branch
    approval      {newest_move, states}: the owner's newest move must be the approval, or any
                  of his moves; and which of the project's approval_states lists counts. Or
                  {from_run: code}, for a kind that follows the coding it judges: it is let start
                  by the approval the runner recorded when it started that ticket's code run
                  (KIT-644, the review kind), and by nothing else
    brief         the fixed words the session is told first
    permissions   "deny-unlisted": anything no allow rule covers is refused, never asked.
                  It is the only value there is: no kind can say "skip every check"
    tools         the program's own tools the session has, and no others
    allow         how each tool is allowed: "workspace" (by the workspace's path) or "name"
                  (by its name: only Bash, which the built-in sandbox holds). No file tool is
                  ever allowed by name: in KIT-630's live run a file tool allowed by name read
                  and wrote outside the workspace (row r4a)
    deny          the places walled off from every file tool and from Bash's reads: the runner's
                  store, the pins and the keys, whole; and the sessions' shared home, where no
                  file tool may write and none may read the program's own state (the adapter
                  says how). Every kind lists all four
    wall          "built-in-sandbox" for a kind with Bash: the program's own sandbox holds
                  Bash (rows r3, r3b) and rules hold the file tools (rows r4b to r4d).
                  "own-sandbox" for a kind without Bash: our own macOS sandbox holds the whole
                  program (row r5a). The two cannot be combined (row r5b: Bash does not start)
    hosts         "project" (the project's allowed_hosts) or a list; [] for a kind without Bash,
                  since nothing of it reaches a host
    env           the names of the only environment variables the program is given. The push
                  token's names (PUSH_ENV, KIT-886) are for a kind that does a ticket's work, with
                  Bash inside the built-in sandbox: code, fix and conflict. Such a kind names all
                  of them or none
    guards        the kit's guards the session's tool calls go through, run from the engine's
                  own folder (scripts/pipeline_runner_guard.py)
    tool_servers  tool servers the session may get: `ticket`, its own five ticket tools, for
                  code, fix and conflict, only while the runner's `session_tools` is set
    limits        the kind's own time, memory and disk ceilings; the runner holds each run to
                  the smaller of these and its settings' limits

One small adapter turns a kind into the program's command line (scripts/pipeline_harness_claude.py).
This file knows nothing of any one program: a second harness would read the same data.

ONE HASH OF THE FOLDER (KIT-880).  `digest()` hashes every entry of the folder by its name, and
each kind's file by its bytes, read as `load()` reads them. `load_with_digest()` gives the same
hash of the very bytes it loaded. The runner keeps that hash when it starts, and compares it with
the folder's before each session: a kit that moved under it never starts a session from walls it
no longer has. A folder or a file that cannot be read gives no hash, never an old one.

Standard library only. It reads files and nothing else.
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_runner_store as store
    import pipeline_runner_limits as limits
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The session kinds use those files' own names and keep no copy." % exc)

SCHEMA = "pipeline-session-kind/1"
FOLDER = os.path.join(os.path.dirname(HERE), "session-kinds")
FIELDS = ("schema", "kind", "about", "brief", "mode", "branch", "approval", "permissions", "tools",
          "allow", "deny", "wall", "hosts", "env", "guards", "tool_servers", "limits")
MODES = ("ticket", "diagnosis", "planning")
BRANCH_TYPES = ("feat", "fix", "chore", "refactor", "docs")
PERMISSIONS = ("deny-unlisted",)
# Each tool a kind may name, and what it is: a shell, or a file tool, which reaches paths itself.
TOOL_CLASS = {"Bash": "shell", "Read": "file", "Grep": "file", "Glob": "file", "Edit": "file",
              "Write": "file"}
ALLOW_HOW = ("workspace", "name")
DENY_PLACES = ("store", "pins", "keys", "home")
WALLS = ("built-in-sandbox", "own-sandbox")
GUARDS = ("pre-tool-use",)
# The tool servers a kind may name. `ticket` is the session's own ticket tools (KIT-706,
# scripts/pipeline_session_tools.py), given only while the runner's `session_tools` is set.
TOOL_SERVERS = ("ticket",)
LIMIT_KEYS = ("wall_minutes", "memory_mb", "disk_mb")
# KIT-886. The sessions' push token: the code host's token, under the one name git's helper and the
# kit's GitHub fallback read, then git's own helper for it, given by the environment. A session
# that holds it can push its branch and open its pull request. Only a kind that does a ticket's
# work, with Bash held to its host list by the built-in sandbox, may name these, and it names all.
PUSH_TOKEN_ENV = "GH_TOKEN"
PUSH_ENV = (PUSH_TOKEN_ENV, "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
            "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1")
WORD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}\Z")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}\Z")
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+\Z")
FILE_MAX = 64 << 10


def _plain(text, limit):
    return (isinstance(text, str) and 0 < len(text) <= limit
            and not any(ord(c) < 32 or ord(c) == 127 for c in text))


def _int_in(v, low, high):
    return isinstance(v, int) and not isinstance(v, bool) and low <= v <= high


def problems(doc, name):
    """Everything wrong with one kind's data, in words. Empty when it may be used."""
    if not isinstance(doc, dict):
        return ["%s: not an object" % name]
    bad = []
    extra = sorted(repr(k)[:40] for k in doc if k not in FIELDS)
    if extra:
        bad.append("%s: %s is not a field" % (name, extra[0]))
    missing = [k for k in FIELDS if k not in doc]
    if missing:
        return bad + ["%s: lacks %s" % (name, missing[0])]
    say = lambda words: bad.append("%s: %s" % (name, words))  # noqa: E731
    if doc["schema"] != SCHEMA:
        say("schema is not %s" % SCHEMA)
    kind = doc["kind"]
    if kind != name or kind not in store.KINDS:
        say("kind must be the file's own name, one of %s" % ", ".join(store.KINDS))
    if not _plain(doc["about"], 200):
        say("about: one line of words, at most 200 characters")
    if not _plain(doc["brief"], 300):
        say("brief: one line of words, at most 300 characters")
    if doc["mode"] not in MODES:
        say("mode: one of %s" % ", ".join(MODES))
    br = doc["branch"]
    if br is None:
        if kind in store.STARTS:
            say("branch: a kind that starts needs one")
    elif not (isinstance(br, dict) and sorted(br) == ["type", "word"] and br["type"] in BRANCH_TYPES
              and isinstance(br["word"], str) and WORD_RE.match(br["word"])):
        say("branch: {type, word}, the type one of %s and the word lower case" % ", ".join(BRANCH_TYPES))
    elif kind not in store.STARTS:
        say("branch: a kind that is only ever a resume keeps its run's branch, so it has none")
    ap = doc["approval"]
    by_states = (isinstance(ap, dict) and sorted(ap) == ["newest_move", "states"]
                 and isinstance(ap["newest_move"], bool) and ap["states"] in store.STARTS)
    by_run = (isinstance(ap, dict) and sorted(ap) == ["from_run"] and ap["from_run"] == "code"
              and kind in store.STARTS and kind != "code")
    if not (by_states or by_run):
        say("approval: {newest_move: true or false, states: one of %s}, or {from_run: code} for a "
            "starting kind that follows a code run" % ", ".join(store.STARTS))
    if doc["permissions"] not in PERMISSIONS:
        say("permissions: must be deny-unlisted; no kind may skip the checks")
    tools = doc["tools"]
    if not (isinstance(tools, list) and tools and all(t in TOOL_CLASS for t in tools)
            and len(set(tools)) == len(tools)):
        say("tools: a list of distinct tools, each one of %s" % ", ".join(sorted(TOOL_CLASS)))
        tools = []
    allow = doc["allow"]
    if not (isinstance(allow, dict) and sorted(allow) == sorted(tools)):
        say("allow: says how each of its tools is allowed, and names no other")
        allow = {}
    for tool in tools:
        how = allow.get(tool)
        if how not in ALLOW_HOW:
            continue                                      # said above
        if TOOL_CLASS[tool] == "file" and how != "workspace":
            say("allow: %s is a file tool, so it is allowed by the workspace's path, never by "
                "its name (KIT-630 row r4a)" % tool)
        if TOOL_CLASS[tool] == "shell" and how != "name":
            say("allow: %s is allowed by name, and the built-in sandbox holds it" % tool)
    deny = doc["deny"]
    if not (isinstance(deny, list) and sorted(deny) == sorted(DENY_PLACES)):
        say("deny: must list every one of %s, once" % ", ".join(DENY_PLACES))
    shell = any(TOOL_CLASS[t] == "shell" for t in tools)
    want = "built-in-sandbox" if shell else "own-sandbox"
    if doc["wall"] not in WALLS:
        say("wall: one of %s" % ", ".join(WALLS))
    elif doc["wall"] != want:
        say("wall: must be %s for a kind %s Bash (KIT-630 rows r3 and r5a; the two cannot be "
            "combined, row r5b)" % (want, "with" if shell else "without"))
    hosts = doc["hosts"]
    if hosts != "project" and not (isinstance(hosts, list) and all(isinstance(h, str) and HOST_RE.match(h)
                                                                   for h in hosts)):
        say("hosts: \"project\" or a list of host names")
    elif not shell and hosts != []:
        say("hosts: a kind without Bash reaches no host, so it lists none")
    env = doc["env"]
    if not (isinstance(env, list) and env and all(isinstance(e, str) and ENV_RE.match(e) for e in env)
            and len(set(env)) == len(env)):
        say("env: a list of distinct variable names")
    else:
        # KIT-886: the push token reaches only a kind that does a ticket's work, and only through
        # Bash the built-in sandbox holds to its host list. A kind without Bash runs inside our own
        # sandbox, which walls files and not the network.
        named = [e for e in PUSH_ENV if e in env]
        if named and not (doc["mode"] == "ticket" and shell and doc["wall"] == "built-in-sandbox"):
            say("env: %s is the push token's, so only a kind that does a ticket's work, with Bash "
                "inside the built-in sandbox, may name it (KIT-886)" % named[0])
        elif named and len(named) != len(PUSH_ENV):
            say("env: the push token goes with git's helper for it: %s, all or none"
                % ", ".join(PUSH_ENV))
    guards = doc["guards"]
    if not (isinstance(guards, list) and all(g in GUARDS for g in guards) and "pre-tool-use" in guards
            and len(set(guards)) == len(guards)):
        say("guards: must hold pre-tool-use, and only the kit's own guards (%s)" % ", ".join(GUARDS))
    servers = doc["tool_servers"]
    if not (isinstance(servers, list) and all(s in TOOL_SERVERS for s in servers)
            and len(set(servers)) == len(servers)):
        say("tool_servers: only the session's own ticket tools (%s) are offered" % ", ".join(TOOL_SERVERS))
    lim = doc["limits"]
    if not (isinstance(lim, dict) and sorted(lim) == sorted(LIMIT_KEYS)
            and all(_int_in(lim[k], limits.FLOORS[k], limits.CEILINGS[k]) for k in LIMIT_KEYS)):
        say("limits: %s, each a whole number inside the runner's own range (%s)" % (
            ", ".join(LIMIT_KEYS), "; ".join("%s %d to %d" % (k, limits.FLOORS[k], limits.CEILINGS[k])
                                             for k in LIMIT_KEYS)))
    return bad


def _is_kind_file(entry):
    stem, ext = os.path.splitext(entry)
    return ext == ".json" and stem in store.KINDS


def _raw(path):
    """(bytes, '') or (None, why): one kind's file, read as every reader here reads it: never
    through a link, never past FILE_MAX."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(FILE_MAX + 1)
    except OSError as exc:
        return None, "could not be read (%s)" % type(exc).__name__
    if len(raw) > FILE_MAX:
        return None, "larger than %d bytes" % FILE_MAX
    return raw, ""


def _hash(read):
    """One hash over [(entry, bytes or None)], in the folder's sorted order: each entry's name, then
    its bytes' own hash, or a mark for an entry that is not a kind's file and so was not read."""
    h = hashlib.sha256(SCHEMA.encode("ascii") + b"\0")
    for entry, raw in read:
        h.update(os.fsencode(entry) + b"\0")
        h.update(b"F" + hashlib.sha256(raw).digest() if raw is not None else b"N")
    return h.hexdigest()


def digest(folder=None):
    """(hash, '') or (None, why): the kinds' folder as it is now, as one hash (KIT-880). Every entry
    counts by its name, and each kind's file by its bytes, read as load() reads them. So a file
    added, removed or changed changes it, and one rewritten with the same bytes does not. A folder
    or a file that cannot be read gives no hash: it is never taken as unchanged."""
    folder = folder or FOLDER
    try:
        names = sorted(os.listdir(folder))
    except OSError as exc:
        return None, "the session kinds' folder could not be read (%s)" % type(exc).__name__
    read = []
    for entry in names:
        if not _is_kind_file(entry):
            read.append((entry, None))
            continue
        raw, why = _raw(os.path.join(folder, entry))
        if raw is None:
            return None, "%s: %s" % (entry, why)
        read.append((entry, raw))
    return _hash(read), ""


def load(folder=None):
    """(kinds, problems): every kind's data, by name, and everything wrong with the folder. Every
    kind the store knows needs its file, and the folder holds nothing else. Kinds come back only
    when there are no problems at all: a runner never starts a kind from part of its data."""
    found, bad, _hash_of_them = load_with_digest(folder)
    return found, bad


def load_with_digest(folder=None):
    """(kinds, problems, hash): load()'s answer, and digest()'s hash of the very bytes it loaded,
    so no change between two reads can slip in. The hash is None whenever nothing loaded."""
    folder = folder or FOLDER
    try:
        names = sorted(os.listdir(folder))
    except OSError as exc:
        return {}, ["the session kinds' folder could not be read (%s)" % type(exc).__name__], None
    found, bad, read = {}, [], []
    for entry in names:
        stem = os.path.splitext(entry)[0]
        if not _is_kind_file(entry):
            bad.append("%s: not a kind's file (each is <kind>.json, for %s)" % (entry[:60], ", ".join(store.KINDS)))
            continue
        raw, why = _raw(os.path.join(folder, entry))
        if raw is None:
            bad.append("%s: %s" % (entry, why))
            continue
        read.append((entry, raw))
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            bad.append("%s: could not be read (%s)" % (entry, type(exc).__name__))
            continue
        more = problems(doc, stem)
        bad += more
        if not more:
            found[stem] = doc
    for kind in store.KINDS:
        if kind not in found and not any(b.startswith(kind + ".json:") or b.startswith(kind + ":")
                                         for b in bad):
            bad.append("%s: no file for this kind" % kind)
    if bad:
        return {}, bad, None
    return found, bad, _hash(read)


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    from scratch_run import contained
    return contained(_selftest, "session-kinds-selftest-")


def _read_tuple(path, name):
    """A module-level tuple or list of strings from another kit file, read without importing it."""
    import ast
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise KeyError(name)


def _selftest():  # noqa: C901  (one battery, read top to bottom)
    import copy
    import tempfile
    failures, counted = [], [0]

    def expect(name, got, want):
        counted[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    kinds, bad = load()
    expect("the kit's own kinds load with nothing wrong", bad, [])
    expect("every kind the store knows has its data", sorted(kinds), sorted(store.KINDS))
    if bad:                             # nothing below can be judged without the kit's own kinds
        print("FAIL: pipeline_session_kinds selftest")
        for f in failures:
            print("  - " + f)
        return 1
    for name, doc in sorted(kinds.items()):
        shell = "Bash" in doc["tools"]
        expect("%s: no file tool is allowed by name" % name,
               [t for t in doc["tools"] if TOOL_CLASS[t] == "file" and doc["allow"][t] != "workspace"], [])
        expect("%s: its wall fits its tools (r3, r5a, r5b)" % name, doc["wall"],
               "built-in-sandbox" if shell else "own-sandbox")
        expect("%s: anything no rule covers is refused" % name, doc["permissions"], "deny-unlisted")
        expect("%s: it denies the store, the pins, the keys and the sessions' state" % name,
               sorted(doc["deny"]), sorted(DENY_PLACES))
    expect("the kinds without Bash are review and plan",
           sorted(k for k, d in kinds.items() if d["wall"] == "own-sandbox"), ["plan", "review"])
    expect("a resume keeps its run's branch: fix and conflict have none",
           sorted(k for k, d in kinds.items() if d["branch"] is None), ["conflict", "fix"])
    # KIT-886: the push token, by name, for code, fix and conflict, and for no other kind.
    expect("the kinds that hold the push token are code, fix and conflict",
           sorted(k for k, d in kinds.items() if PUSH_TOKEN_ENV in d["env"]), ["code", "conflict", "fix"])
    expect("…each with git's helper for it, all of its names",
           sorted(k for k, d in kinds.items() if all(e in d["env"] for e in PUSH_ENV)),
           ["code", "conflict", "fix"])
    expect("…and review, plan and acceptance name none of them",
           [(k, e) for k, d in sorted(kinds.items()) if k in ("review", "plan", "acceptance")
            for e in PUSH_ENV if e in d["env"]], [])

    # The reviewer's and the planner's tools are an allow list, the same sets the old lane's
    # live probes check against (pipeline_stage_e_setup.py, pipeline_stage_a_setup.py).
    stage_e = os.path.join(HERE, "pipeline_stage_e_setup.py")
    stage_a = os.path.join(HERE, "pipeline_stage_a_setup.py")
    reviewer = _read_tuple(stage_e, "REVIEWER_READ_ONLY_TOOLS")
    planner = _read_tuple(stage_a, "PLANNER_KEEP_TOOLS")
    expect("the review kind's tools are inside the reviewer's read-only set",
           [t for t in kinds["review"]["tools"] if t not in reviewer], [])
    expect("…and none is in the reviewer's fence",
           [t for t in kinds["review"]["tools"] if t in _read_tuple(stage_e, "DISALLOWED_BUILTINS")], [])
    import ast
    with open(os.path.join(HERE, "pipeline_session_tools.py"), encoding="utf-8") as fh:
        stages = next(ast.literal_eval(n.value) for n in ast.parse(fh.read()).body
                      if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "STAGES" for t in n.targets))
    expect("the kinds that name the ticket tools are the ones the tools' own server serves",
           sorted(k for k, d in kinds.items() if "ticket" in d["tool_servers"]), sorted(stages))
    expect("the plan kind's tools are inside the planner's set",
           [t for t in kinds["plan"]["tools"] if t not in planner], [])
    expect("…and none is in the planner's fence",
           [t for t in kinds["plan"]["tools"] if t in _read_tuple(stage_a, "DISALLOWED_BUILTINS")], [])

    # KIT-644: a review is let start by the approval its ticket's code run recorded, and the
    # review kind's brief speaks of the facts it is given, not of a ticket it is not given.
    expect("the review kind is approved by its ticket's code run, by nothing of its own",
           kinds["review"]["approval"], {"from_run": "code"})
    expect("...and every other kind by the owner's moves",
           sorted(k for k, d in kinds.items() if "states" in d["approval"]),
           sorted(k for k in kinds if k != "review"))
    expect("the review kind's brief names its facts, not a ticket", ("facts" in kinds["review"]["brief"],
           "ticket describes" in kinds["review"]["brief"]), (True, False))
    approved = dict(kinds["review"])
    for what, value in (("the code kind itself", ("code", {"from_run": "code"})),
                        ("a run of another kind", ("review", {"from_run": "plan"})),
                        ("both shapes at once", ("review", {"from_run": "code", "states": "code"})),
                        ("a kind that is only a resume", ("fix", {"from_run": "code"}))):
        name, ap = value
        doc = dict(kinds[name], approval=ap)
        expect("refused: an approval from a run for %s" % what, problems(doc, name)[:1],
               ["%s: approval: {newest_move: true or false, states: one of %s}, or {from_run: code} "
                "for a starting kind that follows a code run" % (name, ", ".join(store.STARTS))])
    expect("the review kind as shipped loads", problems(approved, "review"), [])

    # Each refusal, one change at a time from a good kind.
    good = kinds["code"]
    for what, change, want in (
            ("a mode that skips every check", {"permissions": "bypassPermissions"},
             "code: permissions: must be deny-unlisted; no kind may skip the checks"),
            ("a file tool allowed by name", {"allow": dict(good["allow"], Read="name")},
             "code: allow: Read is a file tool, so it is allowed by the workspace's path, never by "
             "its name (KIT-630 row r4a)"),
            ("Bash inside our own sandbox", {"wall": "own-sandbox"},
             "code: wall: must be built-in-sandbox for a kind with Bash (KIT-630 rows r3 and r5a; "
             "the two cannot be combined, row r5b)"),
            ("a tool the data does not offer", {"tools": good["tools"] + ["WebFetch"]},
             "code: tools: a list of distinct tools, each one of Bash, Edit, Glob, Grep, Read, Write"),
            ("a tool with no allow", {"tools": good["tools"] + ["Grep"]},
             "code: allow: says how each of its tools is allowed, and names no other"),
            ("a wall left out", {"deny": ["store", "pins", "home"]},
             "code: deny: must list every one of store, pins, keys, home, once"),
            ("a tool server nobody offers", {"tool_servers": ["tracker"]},
             "code: tool_servers: only the session's own ticket tools (ticket) are offered"),
            ("no guard", {"guards": []},
             "code: guards: must hold pre-tool-use, and only the kit's own guards (pre-tool-use)"),
            ("a field it does not know", {"sandbox": False}, "code: 'sandbox' is not a field"),
            ("a time limit a real run would hit", {"limits": dict(good["limits"], wall_minutes=5)},
             "code: limits: wall_minutes, memory_mb, disk_mb, each a whole number inside the runner's "
             "own range (wall_minutes 10 to 10080; memory_mb 512 to 1048576; disk_mb 512 to 16777216)"),
            ("an environment name in lower case", {"env": good["env"] + ["path"]},
             "code: env: a list of distinct variable names"),
            ("an approval no list counts", {"approval": {"newest_move": True, "states": "fix"}},
             "code: approval: {newest_move: true or false, states: one of code, review, plan, "
             "acceptance}, or {from_run: code} for a starting kind that follows a code run"),
            ("a starting kind with no branch", {"branch": None}, "code: branch: a kind that starts needs one"),
            ("the push token without git's helper for it",
             {"env": [e for e in good["env"] if e not in PUSH_ENV[1:]]},
             "code: env: the push token goes with git's helper for it: %s, all or none" % ", ".join(PUSH_ENV)),
            ("git's helper without the push token", {"env": [e for e in good["env"] if e != PUSH_TOKEN_ENV]},
             "code: env: the push token goes with git's helper for it: %s, all or none" % ", ".join(PUSH_ENV))):
        doc = copy.deepcopy(good)
        doc.update(change)
        expect("refused: %s" % what, problems(doc, "code")[:1], [want])
    for name, extra in (("review", [PUSH_TOKEN_ENV]), ("plan", list(PUSH_ENV)), ("acceptance", list(PUSH_ENV))):
        doc = dict(kinds[name], env=kinds[name]["env"] + extra)
        expect("refused: the push token in a %s session, which does no ticket's work (KIT-886)" % name,
               problems(doc, name), ["%s: env: GH_TOKEN is the push token's, so only a kind that does a "
                                     "ticket's work, with Bash inside the built-in sandbox, may name it "
                                     "(KIT-886)" % name])
    expect("refused: the push token in a ticket kind without Bash, whose own sandbox walls no host",
           problems(dict(kinds["review"], mode="ticket", env=kinds["review"]["env"] + list(PUSH_ENV)), "review"),
           ["review: env: GH_TOKEN is the push token's, so only a kind that does a ticket's work, with Bash "
            "inside the built-in sandbox, may name it (KIT-886)"])
    review = copy.deepcopy(kinds["review"])
    review.update(tools=["Read", "Grep", "Glob", "Bash"], allow=dict(review["allow"], Bash="name"))
    expect("refused: Bash added to a kind inside our own sandbox", problems(review, "review"),
           ["review: wall: must be built-in-sandbox for a kind with Bash (KIT-630 rows r3 and r5a; "
            "the two cannot be combined, row r5b)"])
    review = dict(kinds["review"], hosts=["github.com"])
    expect("refused: a host for a kind without Bash", problems(review, "review"),
           ["review: hosts: a kind without Bash reaches no host, so it lists none"])
    fix = dict(kinds["fix"], branch={"type": "fix", "word": "again"})
    expect("refused: a branch for a kind that is only ever a resume", problems(fix, "fix"),
           ["fix: branch: a kind that is only ever a resume keeps its run's branch, so it has none"])
    expect("refused: a file named for another kind", problems(kinds["code"], "fix")[:1],
           ["fix: kind must be the file's own name, one of %s" % ", ".join(store.KINDS)])

    # The folder: every kind, nothing else, nothing half-read.
    tmp = tempfile.mkdtemp(prefix="kinds-")
    for name, doc in kinds.items():
        with open(os.path.join(tmp, name + ".json"), "w") as fh:
            json.dump(doc, fh)
    expect("a whole folder loads", (sorted(load(tmp)[0]), load(tmp)[1]), (sorted(store.KINDS), []))
    os.unlink(os.path.join(tmp, "plan.json"))
    expect("a kind with no file: nothing loads, and it says which",
           load(tmp), ({}, ["plan: no file for this kind"]))
    with open(os.path.join(tmp, "plan.json"), "w") as fh:
        fh.write("{not json")
    expect("a file that is not JSON: nothing loads", load(tmp), ({}, ["plan.json: could not be read (JSONDecodeError)"]))
    os.unlink(os.path.join(tmp, "plan.json"))
    os.symlink(os.path.join(FOLDER, "plan.json"), os.path.join(tmp, "plan.json"))
    expect("a kind's file that is a link is not read", load(tmp)[1], ["plan.json: could not be read (OSError)"])
    os.unlink(os.path.join(tmp, "plan.json"))
    with open(os.path.join(tmp, "plan.json"), "w") as fh:
        json.dump(kinds["plan"], fh)
    with open(os.path.join(tmp, "notes.txt"), "w") as fh:
        fh.write("x")
    expect("anything else in the folder is said", load(tmp),
           ({}, ["notes.txt: not a kind's file (each is <kind>.json, for %s)" % ", ".join(store.KINDS)]))
    expect("a folder that cannot be read", load(os.path.join(tmp, "nowhere")),
           ({}, ["the session kinds' folder could not be read (FileNotFoundError)"]))

    # One hash of the folder (KIT-880): what the runner keeps when it starts, and compares later.
    kit_hash, why = digest()
    expect("the kit's folder has a hash, and a load of it gives the same one, of the bytes it loaded",
           (bool(kit_hash), why, load_with_digest()[2]), (True, "", kit_hash))
    same = tempfile.mkdtemp(prefix="kinds-hash-")
    for name in sorted(kinds):
        with open(os.path.join(FOLDER, name + ".json"), "rb") as src, \
                open(os.path.join(same, name + ".json"), "wb") as dst:
            dst.write(src.read())
    expect("the same files in another folder give the same hash: it is of the data, not the place",
           digest(same), (kit_hash, ""))
    code_path = os.path.join(same, "code.json")
    with open(code_path, "rb") as fh:
        code_bytes = fh.read()
    with open(code_path, "wb") as fh:
        fh.write(code_bytes)
    expect("a file written again with the same bytes leaves the hash as it was", digest(same)[0], kit_hash)
    with open(code_path, "wb") as fh:
        fh.write(code_bytes.replace(b'"wall_minutes": 360', b'"wall_minutes": 361'))
    changed = digest(same)[0]
    expect("one changed value in one kind's file changes the hash, and the data still loads",
           (bool(changed) and changed != kit_hash, load(same)[1]), (True, []))
    expect("…and a load of it gives the new hash", load_with_digest(same)[2], changed)
    with open(code_path, "wb") as fh:
        fh.write(code_bytes)
    with open(os.path.join(same, "notes.txt"), "w") as fh:
        fh.write("x")
    expect("an entry added beside the kinds changes the hash, and a load of that folder gives none",
           (digest(same)[0] not in (None, kit_hash), load_with_digest(same)[2]), (True, None))
    os.unlink(os.path.join(same, "notes.txt"))
    os.unlink(os.path.join(same, "plan.json"))
    expect("a kind's file taken away changes the hash", digest(same)[0] not in (None, kit_hash), True)
    os.symlink(os.path.join(FOLDER, "plan.json"), os.path.join(same, "plan.json"))
    expect("a kind's file that is a link gives no hash, and says which", digest(same),
           (None, "plan.json: could not be read (OSError)"))
    expect("a folder that cannot be read gives no hash", digest(os.path.join(same, "nowhere")),
           (None, "the session kinds' folder could not be read (FileNotFoundError)"))

    if failures:
        print("FAIL: pipeline_session_kinds selftest")
        for f in failures:
            print("  - " + f)
        return 1
    print("OK: pipeline_session_kinds selftest (%d checks)" % counted[0])
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return selftest()
    if argv == ["--check"]:
        kinds, bad = load()
        for line in bad:
            print("KIND: %s" % line)
        if bad:
            return 1
        print("session kinds: %s" % ", ".join("%s (%s)" % (k, kinds[k]["wall"]) for k in store.KINDS))
        return 0
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main())
