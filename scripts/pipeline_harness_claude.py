#!/usr/bin/env python3
"""The one adapter: a kind of session, as data, turned into what the official `claude` program
is given (KIT-704; docs/RUNNER.md, "Each kind of session").

    python3 scripts/pipeline_harness_claude.py --selftest

WHAT IT BUILDS, for one run of one kind (scripts/pipeline_session_kinds.py), and nothing else:

    command()      the one command line. Nothing loads from the workspace: --setting-sources "",
                   --strict-mcp-config and the run's own --settings file (KIT-630 rows r1, r1c)
    settings()     that settings file: the deny rules, the kit's guard run from the engine's own
                   folder, and for a kind with Bash the built-in sandbox with its host list and
                   deny-read list (rows r3, r3b)
    mcp()          the tool-server file: empty, as no tool server is offered yet
    profile()      for a kind without Bash, our own macOS sandbox around the whole program
                   (row r5a): no write but the program's own home and the run's temp folder, and no
                   read of the store, the pins or a key
    environment()  exactly the kind's environment names, each from one fixed source here
    workspace_problems()  a dotenv file in the workspace that names a variable the program reads

A second harness would be a second file like this one, reading the same kind data. This one knows
the program's own words: its flags, its tool names and its permission-rule forms.

THE RULES IT WRITES.  Each file tool is allowed by the workspace's path, as `Read(//<path>/**)`,
never by its bare name (row r4a: a bare name reached outside, sandbox or not; row r4d: by path,
the outside was refused with no deny rule). Grep and Glob need no rule of their own: the program
applies a Read rule to them. Bash is allowed by its name; the built-in sandbox holds it. Every
file tool is denied the runner's store, the pins and each key file. In the sessions' shared home,
no file tool may write anywhere, and none may read the program's own state (its `.claude` folder
and `.claude.json`, which hold every session's transcript). Bash's deny-read list names every
place denied to Read; why the rest of the home stays readable is in walls(). git in a session
reads no global config at all (GIT_CONFIG_GLOBAL is /dev/null).

WHAT IT NEVER WRITES.  A mode that skips the checks (`bypassPermissions`, `acceptEdits`,
`--dangerously-skip-permissions`), `--add-dir`, a permission-prompt tool, or a file tool by bare
name. The selftest asserts each, for every kind.

Standard library only. It writes no file and starts no process: the runner does both.
"""
import os
import re
import shlex
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_session_kinds as kinds_data
    import pipeline_voice as voice
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The adapter uses those files' own code and keeps no copy." % exc)

NAME = "claude"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PYTHON = "/usr/bin/python3"
GUARD_LAUNCHER = os.path.join("scripts", "pipeline_runner_guard.py")      # under the engine's folder
GUARD_TIMEOUT = 10
# Each environment name a kind may list, and its one source. Nothing else is ever passed: an API
# key, a second token, a proxy or another address has no source here, so no kind can name one.
ENV_SOURCES = {"PATH": "path", "HOME": "home", "TMPDIR": "tmp", "LANG": "lang",
               voice.CHILD_TOKEN_VAR: "token", "DISABLE_AUTOUPDATER": "1",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
               # git reads no global config at all: a file in the sessions' shared home is never
               # git's config, whoever wrote it there
               "GIT_CONFIG_GLOBAL": "devnull"}
# The program applies a Read rule to these, so they are allowed by Read's own rule.
COVERED_BY_READ = ("Grep", "Glob")
FILE_RULE_TOOLS = ("Read", "Edit", "Write")         # the tools every wall is denied to, by rule
OUTPUTS = {"json": ["--output-format", "json"],
           "stream": ["--output-format", "stream-json", "--verbose"]}
# Variables the program reads from its environment. If a dotenv file in its working folder were
# ever loaded into it, one of these would change how it signs in (an API key bills a card instead
# of the plan's usage window), where its traffic goes (a base address, a proxy, a certificate), or
# what it runs (NODE_OPTIONS). The runner refuses a workspace whose dotenv files name one.
PROGRAM_VARS = re.compile(r"^(ANTHROPIC_|CLAUDE_|BUN_|NODE_OPTIONS\Z|NODE_EXTRA_CA_CERTS\Z|"
                          r"NODE_TLS_REJECT_UNAUTHORIZED\Z|SSL_CERT_(FILE|DIR)\Z|HTTPS?_PROXY\Z|"
                          r"ALL_PROXY\Z|NO_PROXY\Z|AWS_BEARER_TOKEN_BEDROCK\Z)", re.I)
# The dotenv files a loader reads from its working folder by itself. `.env.example` and the like
# are documentation, read by nothing.
DOTENV_FILES = (".env", ".env.local", ".env.development", ".env.production", ".env.test",
                ".env.development.local", ".env.production.local", ".env.test.local")
DOTENV_MAX = 256 << 10
# A path the rules or the profile name must say exactly one place: no pattern character, nothing a
# profile string could close, no control character, no `..`.
_BAD_PATH = re.compile(r"[*?\[\]{}!\\\"\x00-\x1f\x7f]")
RUN_PATHS = ("claude_bin", "workspace", "run_dir", "tmp", "store", "pins", "home", "engine")
NEVER = ("bypassPermissions", "acceptEdits", "--dangerously-skip-permissions", "--add-dir",
         "--permission-prompt-tool", "--allow-dangerously-skip-permissions")


def _exact(path):
    return (isinstance(path, str) and os.path.isabs(path) and not _BAD_PATH.search(path)
            and ".." not in path.split("/") and path == os.path.normpath(path))


def _path_problem(path, real=True):
    """Why `path` cannot be named by a rule or a profile, or ''. A walled place must be its own
    real path: the system matches real paths, so a rule naming a path through a link (macOS's /tmp
    and /var are links to /private) would match nothing, and its wall would stand open."""
    if not _exact(path):
        return "not an exact absolute path"
    if real and os.path.realpath(path) != path:
        return "goes through a link, so a rule naming it would match nothing"
    return ""


def check(spec):
    """What this adapter cannot build of a kind's data, in words. The data's own rules are the
    data module's; these are this program's."""
    bad = []
    name = spec.get("kind", "?")
    for env in spec.get("env") or []:
        if env not in ENV_SOURCES:
            bad.append("%s: env: %s has no source in the %s adapter, so it is never passed"
                       % (name, env, NAME))
    allow = spec.get("allow") or {}
    for tool in COVERED_BY_READ:
        if tool in (spec.get("tools") or []) and allow.get("Read") != "workspace":
            bad.append("%s: allow: %s is held by Read's rule, so Read must be allowed by the "
                       "workspace's path" % (name, tool))
    if spec.get("tool_servers"):
        bad.append("%s: tool_servers: the %s adapter offers none yet" % (name, NAME))
    return bad


def run_problems(run):
    """What is wrong with one run's facts: every path exact and absolute, and every place a rule
    or the profile names its own real path. The program's path and the engine's may go through a
    link: no wall names either."""
    bad = []
    for k in RUN_PATHS:
        why = _path_problem(run.get(k), real=k not in ("claude_bin", "engine"))
        if why:
            bad.append("%s: %s" % (k, why))
    if run.get("pin"):
        why = _path_problem(run["pin"])
        if why:
            bad.append("pin: %s" % why)
    keys = run.get("keys")
    if not (isinstance(keys, list) and keys and all(not _path_problem(k) for k in keys)):
        bad.append("keys: a list of exact real paths, at least the tracker key")
    if not all(isinstance(t, str) and t and not _BAD_PATH.search(t) and " " not in t
               for t in run.get("extra_allowed") or []):
        bad.append("extra_allowed: exact tool names only")
    return bad


def _tree(tool, path):
    return "%s(/%s/**)" % (tool, path)


def _file(tool, path):
    return "%s(/%s)" % (tool, path)


def walls(spec, run):
    """[(path, is_folder, tools)] for every place the kind denies, in its data's order: the tools
    each is denied to. Bash may not read a place whose tools include Read.

    The sessions' shared home is walled in two parts. Writes are denied on the whole of it, so
    no session leaves a file there for the next. Reads are denied on the program's own state:
    its `.claude` folder and `.claude.json`, which hold every session's transcript. Reads of the
    rest stay open, on purpose. The program makes each Read deny rule part of Bash's deny-read
    list too (measured 12 Oct 2026, KIT-256). git stops when a read of its global config is
    refused, rather than missing, so a Read deny on the whole home would stop Bash's git. The
    home holds nothing of the account's own (docs/RUNNER.md, "Settings"), and with writes
    denied nothing else lands there."""
    rwx = FILE_RULE_TOOLS
    out = []
    for place in spec["deny"]:
        if place == "store":
            out.append((run["store"], True, rwx))
        elif place == "pins":
            out.append((run["pins"], True, rwx))
        elif place == "keys":
            out += [(k, False, rwx) for k in run["keys"]]
        elif place == "home":
            out += [(os.path.join(run["home"], ".claude"), True, ("Read",)),
                    (os.path.join(run["home"], ".claude.json"), False, ("Read",)),
                    (run["home"], True, ("Edit", "Write"))]
    return out


def allowed(spec, run):
    """The allow rules: each tool as its data says, then any exact tool names the caller adds."""
    rules = []
    for tool in spec["tools"]:
        how = spec["allow"][tool]
        if how == "name":
            rules.append(tool)
        elif tool not in COVERED_BY_READ:
            rules.append(_tree(tool, run["workspace"]))
    return rules + list(run.get("extra_allowed") or [])


def denied(spec, run):
    return [(_tree if folder else _file)(tool, path)
            for path, folder, tools in walls(spec, run) for tool in tools]


def deny_read(spec, run):
    """Bash's deny-read list: every place denied to Read."""
    return [path for path, _folder, tools in walls(spec, run) if "Read" in tools]


def hosts(spec, run):
    return list(run["hosts"]) if spec["hosts"] == "project" else list(spec["hosts"])


def guard_command(run, hook):
    """The hook's command line: the kit's own guard, through the launcher in the engine's own
    folder, told which workspace it guards. Every part is quoted for the shell the program runs
    hooks with."""
    parts = ["/usr/bin/env", "-u", "DEVELOPER_DIR", PYTHON, "-I",
             os.path.join(run["engine"], GUARD_LAUNCHER), "--workspace", run["workspace"],
             "--hook", hook]
    return " ".join(shlex.quote(p) for p in parts)


def settings(spec, run):
    """The run's --settings file."""
    doc = {"permissions": {"deny": denied(spec, run)},
           "hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [
               {"type": "command", "command": guard_command(run, g), "timeout": GUARD_TIMEOUT}
               for g in spec["guards"]]}]}}
    if spec["wall"] == "built-in-sandbox":
        doc["sandbox"] = {"enabled": True, "failIfUnavailable": True,
                          "allowUnsandboxedCommands": False,
                          "network": {"allowedDomains": hosts(spec, run)},
                          "filesystem": {"denyRead": deny_read(spec, run)}}
    return doc


def mcp(spec, run):
    """The run's --mcp-config file: no tool server is offered yet."""
    return {"mcpServers": {}}


def _sb(path):
    return '"%s"' % path                  # run_problems() keeps quotes and backslashes out


def profile(spec, run):
    """Our own sandbox for a kind without Bash, as profile text (KIT-630 row r5a). The last
    matching rule wins, so each exception comes after what it is an exception to."""
    walled = ["  (subpath %s)" % _sb(run["store"]), "  (subpath %s)" % _sb(run["pins"])]
    walled += ["  (literal %s)" % _sb(k) for k in run["keys"]]
    inputs = ["  (subpath %s)" % _sb(run["run_dir"])]
    if run.get("pin"):
        inputs.append("  (literal %s)" % _sb(run["pin"]))
    return "\n".join([
        "(version 1)",
        "; Our own sandbox around one %s session (KIT-704). The runner wrote it for this run, from" % spec["kind"],
        "; scripts/pipeline_harness_claude.py, outside every workspace. The last matching rule wins.",
        "(allow default)",
        "; Writes: only the program's own home, this run's temp folder and the null devices.",
        "(deny file-write*)",
        "(allow file-write*",
        "  (subpath %s)" % _sb(run["home"]),
        "  (subpath %s)" % _sb(run["tmp"]),
        '  (literal "/dev/null")',
        '  (literal "/dev/tty")',
        '  (literal "/dev/dtracehelper"))',
        "; Never the runner's store, the pins or a key...",
        "(deny file-read* file-write*",
    ] + walled[:-1] + [walled[-1] + ")",
        "; ...apart from reading this run's own inputs and its own pin, which the program and its guard read.",
        "(allow file-read*",
    ] + inputs[:-1] + [inputs[-1] + ")", ""])


def command(spec, run, output="json"):
    """The one command line. The prompt is not in it: it goes on stdin."""
    argv = ([run["claude_bin"], "-p", "--model", run["model"]] + OUTPUTS[output]
            + ["--resume" if run["resume"] else "--session-id", run["session_id"],
               "--setting-sources", "", "--strict-mcp-config",
               "--mcp-config", os.path.join(run["run_dir"], "mcp.json"),
               "--settings", os.path.join(run["run_dir"], "settings.json"),
               "--tools", ",".join(spec["tools"]), "--allowedTools"] + allowed(spec, run)
            + ["--permission-mode", "dontAsk", "--permission-prompts", "none",
               "--append-system-prompt-file", os.path.join(run["run_dir"], "rules.md"),
               "--max-turns", str(run["budget"]["maxTurns"]),
               "--max-budget-usd", "%g" % run["budget"]["maxUsd"]])
    if spec["wall"] == "own-sandbox":
        argv = [SANDBOX_EXEC, "-f", os.path.join(run["run_dir"], "sandbox.sb")] + argv
    return argv


def search_path(claude_bin):
    """The program's own folder first (a node-run program needs its node there), then the fixed
    path, each folder once."""
    out = []
    for d in [os.path.dirname(claude_bin)] + voice.DEFAULT_PATH.split(":"):
        if d and d not in out:
            out.append(d)
    return ":".join(out)


def environment(spec, run, token):
    """Exactly the kind's names, each from its one source here."""
    src = {"path": search_path(run["claude_bin"]), "home": run["home"], "tmp": run["tmp"],
           "lang": voice.DEFAULT_LANG, "token": token, "1": "1", "devnull": os.devnull}
    return dict((name, src[ENV_SOURCES[name]]) for name in spec["env"])


def workspace_problems(workspace):
    """Each dotenv file in the workspace that names a variable the program reads, with a value, as
    words: the file and the name, never the value."""
    bad = []
    for name in DOTENV_FILES:
        path = os.path.join(workspace, name)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            bad.append("the workspace's %s could not be checked (%s)" % (name, type(exc).__name__))
            continue
        if not stat.S_ISREG(st.st_mode):
            bad.append("the workspace's %s is not a plain file, so it cannot be checked" % name)
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as fh:
                raw = fh.read(DOTENV_MAX + 1)
        except OSError as exc:
            bad.append("the workspace's %s could not be read (%s)" % (name, type(exc).__name__))
            continue
        if len(raw) > DOTENV_MAX:
            bad.append("the workspace's %s is too large to check" % name)
            continue
        named = []
        for line in raw.decode("utf-8", "replace").splitlines():
            text = line.strip()
            if text.startswith("export "):
                text = text[len("export "):].lstrip()
            key, eq, value = text.partition("=")
            key, value = key.strip(), value.strip().strip("'\"").strip()
            if eq and value and PROGRAM_VARS.match(key) and key not in named:
                named.append(key)
        if named:
            bad.append("the workspace's %s names %s, which the program reads: it could sign in "
                       "another way or send its traffic elsewhere" % (name, ", ".join(named)))
    return bad


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    from scratch_run import contained
    return contained(_selftest, "harness-claude-selftest-")


def _selftest():  # noqa: C901  (one battery, read top to bottom)
    import json
    import subprocess
    import tempfile
    failures, counted = [], [0]

    def expect(name, got, want):
        counted[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    kinds, bad = kinds_data.load()
    expect("the kinds load", bad, [])
    for name, spec in sorted(kinds.items()):
        expect("%s: this adapter can build every part of it" % name, check(spec), [])

    tmp = os.path.realpath(tempfile.mkdtemp(prefix="harness-"))
    P = dict((k, os.path.join(tmp, k)) for k in ("store", "pins", "home", "work", "engine", "outside"))
    for d in P.values():
        os.makedirs(d)
    ws = os.path.join(P["work"], "code", "ENG-1")
    rdir = os.path.join(P["store"], "runs", "run-1")
    key = os.path.join(tmp, "tracker-key")
    pin = os.path.join(P["pins"], "0123456789abcdef.json")
    run = {"claude_bin": "/opt/x/bin/claude", "model": "sonnet", "workspace": ws, "run_dir": rdir,
           "tmp": os.path.join(P["work"], ".tmp", "run-1"), "store": P["store"], "pins": P["pins"],
           "pin": pin, "home": P["home"], "keys": [key], "engine": P["engine"],
           "hosts": ["github.com", "api.github.com"], "session_id": "sid-1", "resume": False,
           "budget": {"maxTurns": 25, "maxUsd": 2.0}, "extra_allowed": []}
    expect("a run's facts with exact paths are fine", run_problems(run), [])
    for what, change, want in (
            ("a pattern character", {"store": P["store"] + "*"}, ["store: not an exact absolute path"]),
            ("a quote a profile could close on", {"home": P["home"] + '"x'}, ["home: not an exact absolute path"]),
            ("a relative path", {"pins": "pins"}, ["pins: not an exact absolute path"]),
            ("a parent step", {"run_dir": P["store"] + "/../x"}, ["run_dir: not an exact absolute path"]),
            ("no key file", {"keys": []}, ["keys: a list of exact real paths, at least the tracker key"]),
            ("an added tool that is a pattern", {"extra_allowed": ["mcp__x__*"]}, ["extra_allowed: exact tool names only"])):
        expect("refused: %s" % what, run_problems(dict(run, **change)), want)
    os.symlink(P["store"], os.path.join(tmp, "store-link"))
    os.symlink(tmp, os.path.join(P["outside"], "via"))
    expect("refused: a walled place named through a link, which no rule would match",
           run_problems(dict(run, store=os.path.join(tmp, "store-link"))),
           ["store: goes through a link, so a rule naming it would match nothing"])
    expect("refused: a key file named through a link to its folder",
           run_problems(dict(run, keys=[os.path.join(P["outside"], "via", "tracker-key")])),
           ["keys: a list of exact real paths, at least the tracker key"])
    expect("…while the program and the engine may sit behind a link: no wall names them",
           run_problems(dict(run, claude_bin=os.path.join(P["outside"], "via", "claude"),
                             engine=os.path.join(P["outside"], "via", "engine"))), [])

    # ---- the full command line, pinned for every kind ----------------------- #
    common = ["--setting-sources", "", "--strict-mcp-config",
              "--mcp-config", rdir + "/mcp.json", "--settings", rdir + "/settings.json"]
    tail = ["--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--append-system-prompt-file", rdir + "/rules.md", "--max-turns", "25", "--max-budget-usd", "2"]
    head = ["/opt/x/bin/claude", "-p", "--model", "sonnet", "--output-format", "json", "--session-id", "sid-1"]
    write_kind = ["--tools", "Bash,Read,Edit,Write", "--allowedTools", "Bash", "Read(/%s/**)" % ws,
                  "Edit(/%s/**)" % ws, "Write(/%s/**)" % ws]
    read_kind = ["--tools", "Read,Grep,Glob", "--allowedTools", "Read(/%s/**)" % ws]
    sbx = ["/usr/bin/sandbox-exec", "-f", rdir + "/sandbox.sb"]
    want_argv = {
        "code": head + common + write_kind + tail,
        "fix": head + common + write_kind + tail,
        "conflict": head + common + write_kind + tail,
        "review": sbx + head + common + read_kind + tail,
        "plan": sbx + head + common + read_kind + tail,
        "acceptance": head + common + ["--tools", "Bash,Read", "--allowedTools", "Bash",
                                       "Read(/%s/**)" % ws] + tail,
    }
    for name in sorted(kinds):
        argv = command(kinds[name], run)
        expect("%s: the one command line, exactly" % name, argv, want_argv[name])
        expect("%s: no mode or flag that skips a check" % name, [a for a in argv if a in NEVER], [])
        expect("%s: no file tool allowed by its bare name" % name,
               [a for a in argv[argv.index("--allowedTools") + 1:argv.index("--permission-mode")]
                if a in kinds_data.TOOL_CLASS and kinds_data.TOOL_CLASS[a] == "file"], [])
    resumed = command(kinds["fix"], dict(run, resume=True))
    expect("a resume names the session to resume, and nothing else changes",
           (resumed[6:8], resumed[:6] + resumed[8:] == want_argv["fix"][:6] + want_argv["fix"][8:]),
           (["--resume", "sid-1"], True))
    streamed = command(kinds["review"], run, output="stream")
    expect("the probe's stream output changes the output flags and nothing else",
           (streamed[7:10], streamed[:7] + streamed[10:] == want_argv["review"][:7] + want_argv["review"][9:]),
           (["--output-format", "stream-json", "--verbose"], True))
    extra = command(kinds["code"], dict(run, extra_allowed=["mcp__session__read_my_ticket"]))
    expect("added exact tool names go last in the allow list, and nothing else moves",
           extra, want_argv["code"][:-len(tail)] + ["mcp__session__read_my_ticket"] + tail)

    # ---- the settings file ------------------------------------------------ #
    deny_paths = [P["store"], P["pins"], key, P["home"] + "/.claude", P["home"] + "/.claude.json"]
    deny_rules = (["%s(/%s/**)" % (t, P["store"]) for t in ("Read", "Edit", "Write")]
                  + ["%s(/%s/**)" % (t, P["pins"]) for t in ("Read", "Edit", "Write")]
                  + ["%s(/%s)" % (t, key) for t in ("Read", "Edit", "Write")]
                  + ["Read(/%s/.claude/**)" % P["home"], "Read(/%s/.claude.json)" % P["home"],
                     "Edit(/%s/**)" % P["home"], "Write(/%s/**)" % P["home"]])
    guard = ("/usr/bin/env -u DEVELOPER_DIR /usr/bin/python3 -I %s/scripts/pipeline_runner_guard.py "
             "--workspace %s --hook pre-tool-use" % (P["engine"], ws))
    hooks = {"PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": guard, "timeout": 10}]}]}
    for name in sorted(kinds):
        got = settings(kinds[name], run)
        want = {"permissions": {"deny": deny_rules}, "hooks": hooks}
        if "Bash" in kinds[name]["tools"]:
            want["sandbox"] = {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                               "network": {"allowedDomains": ["github.com", "api.github.com"]},
                               "filesystem": {"denyRead": deny_paths}}
        expect("%s: its settings file, exactly" % name, got, want)
        expect("%s: no file tool may write anywhere in the sessions' shared home, nor read its transcripts" % name,
               [r for r in got["permissions"]["deny"] if P["home"] in r],
               ["Read(/%s/.claude/**)" % P["home"], "Read(/%s/.claude.json)" % P["home"],
                "Edit(/%s/**)" % P["home"], "Write(/%s/**)" % P["home"]])
    expect("a path with a space is quoted for the hook's shell",
           shlex.split(guard_command(dict(run, workspace="/w s/ENG-1"), "pre-tool-use"))[7], "/w s/ENG-1")
    expect("no tool server is offered yet", mcp(kinds["code"], run), {"mcpServers": {}})

    # ---- the environment -------------------------------------------------- #
    for name in sorted(kinds):
        env = environment(kinds[name], run, "a-token")
        expect("%s: its environment is exactly its kind's names" % name, sorted(env), sorted(kinds[name]["env"]))
        expect("%s: no API key, proxy or base address can be among them" % name,
               [k for k in env if PROGRAM_VARS.match(k) and k not in ENV_SOURCES], [])
    env = environment(kinds["code"], run, "a-token")
    expect("the environment's values", (env["HOME"], env["TMPDIR"], env[voice.CHILD_TOKEN_VAR],
                                         env["PATH"].split(":")[0]), (P["home"], run["tmp"], "a-token", "/opt/x/bin"))
    expect("every kind with Bash runs git with no global config, so nothing in the shared home is git's config",
           sorted(k for k in kinds if environment(kinds[k], run, "t").get("GIT_CONFIG_GLOBAL") == os.devnull),
           sorted(k for k in kinds if "Bash" in kinds[k]["tools"]))
    expect("a name with no source is said, never passed",
           check(dict(kinds["code"], env=kinds["code"]["env"] + ["ANTHROPIC_API_KEY"])),
           ["code: env: ANTHROPIC_API_KEY has no source in the claude adapter, so it is never passed"])
    expect("Grep with no Read rule is said",
           check(dict(kinds["review"], tools=["Grep"], allow={"Grep": "workspace"})),
           ["review: allow: Grep is held by Read's rule, so Read must be allowed by the workspace's path"])

    # ---- a workspace's dotenv files ---------------------------------------- #
    os.makedirs(ws)
    expect("a workspace with no dotenv file is fine", workspace_problems(ws), [])
    with open(os.path.join(ws, ".env"), "w") as fh:
        fh.write("# a comment\nAPP_PORT=8080\nexport ANTHROPIC_API_KEY='sk-ant-not-a-real-key'\n"
                 "https_proxy=http://example.invalid:9\nCLAUDE_CONFIG_DIR=\n")
    with open(os.path.join(ws, ".env.example"), "w") as fh:
        fh.write("ANTHROPIC_API_KEY=put-yours-here\n")
    got = workspace_problems(ws)
    expect("a dotenv file naming the program's variables is said: the file and the names",
           got, ["the workspace's .env names ANTHROPIC_API_KEY, https_proxy, which the program reads: it "
                 "could sign in another way or send its traffic elsewhere"])
    expect("…never the value", "sk-ant" in " ".join(got), False)
    os.unlink(os.path.join(ws, ".env"))
    os.symlink(os.path.join(P["outside"], "x"), os.path.join(ws, ".env.local"))
    expect("a dotenv file that is a link cannot be checked, so it is said",
           workspace_problems(ws), ["the workspace's .env.local is not a plain file, so it cannot be checked"])
    os.unlink(os.path.join(ws, ".env.local"))

    # ---- our own sandbox: the profile's text, then the real thing -------------- #
    text = profile(kinds["review"], run)
    expect("the profile allows by default, then walls off writes and the four places",
           [ln for ln in text.splitlines() if ln.startswith("(")],
           ["(version 1)", "(allow default)", "(deny file-write*)", "(allow file-write*",
            "(deny file-read* file-write*", "(allow file-read*"])
    expect("the profile names each place exactly once",
           [text.count('"%s"' % p) for p in (P["store"], P["pins"], key, rdir, pin, P["home"], run["tmp"])],
           [1, 1, 1, 1, 1, 1, 1])
    if os.path.exists(SANDBOX_EXEC) and sys.platform == "darwin":
        for d in (rdir, run["tmp"], os.path.join(P["store"], "runs", "run-2")):
            os.makedirs(d)
        files = {"own-input": os.path.join(rdir, "settings.json"),
                 "other-run": os.path.join(P["store"], "runs", "run-2", "prompt.txt"),
                 "own-pin": pin, "other-pin": os.path.join(P["pins"], "fedcba9876543210.json"),
                 "key": key, "inside": os.path.join(ws, "README.md")}
        for path in files.values():
            with open(path, "w") as fh:
                fh.write("x\n")
        with open(os.path.join(rdir, "sandbox.sb"), "w") as fh:
            fh.write(text)
        script = ("import json, sys\nout = {}\n"
                  "for name, path in json.loads(sys.argv[1]).items():\n"
                  "    try:\n        open(path).read(); out[name] = True\n"
                  "    except OSError:\n        out[name] = False\n"
                  "for name, path in json.loads(sys.argv[2]).items():\n"
                  "    try:\n        open(path, 'w').write('x'); out['write ' + name] = True\n"
                  "    except OSError:\n        out['write ' + name] = False\n"
                  "print(json.dumps(out, sort_keys=True))\n")
        writes = {"home": os.path.join(P["home"], "state.json"), "tmp": os.path.join(run["tmp"], "t"),
                  "workspace": os.path.join(ws, "made.txt"), "outside": os.path.join(P["outside"], "made.txt"),
                  "store": os.path.join(rdir, "made.txt"), "engine": os.path.join(P["engine"], "made.txt")}
        r = subprocess.run([SANDBOX_EXEC, "-f", os.path.join(rdir, "sandbox.sb"), PYTHON, "-I", "-c", script,
                            json.dumps(files), json.dumps(writes)], capture_output=True, text=True, timeout=60)
        got = json.loads(r.stdout or "{}") if r.returncode == 0 else {"exit": r.returncode, "err": r.stderr[-300:]}
        expect("inside our own sandbox, for real: its inputs, its pin and the workspace are read; the store's other "
               "runs, other pins and the key are not; only its home and temp folder are written", got,
               {"own-input": True, "own-pin": True, "inside": True, "other-run": False, "other-pin": False,
                "key": False, "write home": True, "write tmp": True, "write workspace": False,
                "write outside": False, "write store": False, "write engine": False})
    else:
        print("  (skipped: the live profile check needs %s on a Mac)" % SANDBOX_EXEC)

    if failures:
        print("FAIL: pipeline_harness_claude selftest")
        for f in failures:
            print("  - " + f)
        return 1
    print("OK: pipeline_harness_claude selftest (%d checks)" % counted[0])
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return selftest()
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main())
