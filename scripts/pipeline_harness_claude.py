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
    mcp()          the tool-server file with no server: the runner writes the session's own
                   ticket tools' server (scripts/pipeline_session_tools.py, KIT-706) and the
                   acceptance kind's look tool (KIT-708) itself

THE ONE BUILDER (KIT-708).  line() spells every option of the program this file passes, in one
order, from the options its caller names. command() builds each session's line with it, and so do
the program's other callers: the tech lead's turn (scripts/pipeline_tech_lead_turn.py) and the
one-shot writer (scripts/pipeline_voice.py). Each names its own options and values; line() only
spells and orders them. It has no option for a mode that skips the checks.
    profile()      for a kind without Bash, our own macOS sandbox around the whole program
                   (row r5a): no write but the program's own home and the run's temp folder, and no
                   read of the store, the pins or a key
    environment()  exactly the kind's environment names, each from one fixed source here. The push
                   token (KIT-886) reaches a kind only under a name its data lists: code, fix and
                   conflict. git gets it through a helper given in the same environment, for
                   github.com alone, after every stored helper is cleared
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
reads no global config at all (GIT_CONFIG_GLOBAL is /dev/null). In a kind that holds the push token
(KIT-886), git's helpers from every other config are cleared, and one helper, by environment, hands
git the token for github.com alone. Each key file, the push token's among them, is walled whole.

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
    # The writer's own token name, path and language. The kinds' data is read by the selftest alone:
    # the writer builds its line here too (KIT-708), and a job that only writes loads nothing more.
    import pipeline_voice as voice
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The adapter uses those files' own code and keeps no copy." % exc)
try:
    # KIT-886: the push token's names, re-exported here, from the one place they are spelled: the
    # runner's store, which the session kinds re-export too. Not from the kinds' module, whose checks
    # load the runner's limits and the queue with them, which a job that only writes never needs.
    from pipeline_runner_store import PUSH_ENV, PUSH_TOKEN_ENV
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
               "GIT_CONFIG_GLOBAL": "devnull",
               # KIT-886: the push token, which the runner reads from its own file, and git's helper
               # for it as git's own config by environment: first every stored helper cleared (the
               # system's keychain helper among them), then one that hands git the token for
               # github.com alone. So no command line ever holds the token.
               PUSH_TOKEN_ENV: "push", "GIT_CONFIG_COUNT": "git-count",
               "GIT_CONFIG_KEY_0": "git-key-0", "GIT_CONFIG_VALUE_0": "git-value-0",
               "GIT_CONFIG_KEY_1": "git-key-1", "GIT_CONFIG_VALUE_1": "git-value-1"}
# git's helper: it reads what git writes to it, then answers a `get` with the token from its own
# environment. It answers nothing else, and git asks it about github.com alone.
GIT_PUSH_HOST = "https://github.com"
GIT_PUSH_HELPER = ('!f() { cat >/dev/null; if test "$1" = get; then echo username=x-access-token; '
                   'echo "password=$%s"; fi; }; f' % PUSH_TOKEN_ENV)
GIT_PUSH_CONFIG = (("credential.helper", ""), ("credential.%s.helper" % GIT_PUSH_HOST, GIT_PUSH_HELPER))
# The program applies a Read rule to these, so they are allowed by Read's own rule.
COVERED_BY_READ = ("Grep", "Glob")
FILE_RULE_TOOLS = ("Read", "Edit", "Write")         # the tools every wall is denied to, by rule
OUTPUTS = {"json": ["--output-format", "json"],
           "stream": ["--output-format", "stream-json", "--verbose"],
           # the tech lead's turn streams the model's text as it comes (KIT-565)
           "stream-partial": ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]}
# The outputs a session the runner starts, or the probe, is read in. The third is the turn's alone.
RUNNER_OUTPUTS = ("json", "stream")
# The only permission mode line() writes: anything no allow rule covers is refused, never asked.
PERMISSION_MODES = ("dontAsk",)
# The tool servers this adapter knows a session may get. The runner writes each one's entry itself:
# `ticket`, the session's own ticket tools (KIT-706), and `look`, the acceptance kind's look tool
# (KIT-708), started from the engine's own folder on the runner's `look_config_dir`.
TOOL_SERVERS = ("ticket", "look")
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
    others = [s for s in spec.get("tool_servers") or [] if s not in TOOL_SERVERS]
    if others:
        bad.append("%s: tool_servers: the %s adapter has no tool server but the session's own ticket "
                   "tools and the look tool, which the runner writes itself" % (name, NAME))
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
    """The run's --mcp-config file with no server. The session's own ticket tools are the runner's
    to add (scripts/pipeline_session_tools.py), with its session_tools setting."""
    return {"mcpServers": {}}


def _sb(path):
    return '"%s"' % path                  # run_problems() keeps quotes and backslashes out


def _on_the_way(item, folder):
    """The folders from `folder` down to `item`'s parent, when `item` sits inside `folder`."""
    if not item.startswith(folder.rstrip("/") + "/"):
        return []
    out, cur = [folder], os.path.dirname(item)
    while cur != folder and cur.startswith(folder.rstrip("/") + "/"):
        out.append(cur)
        cur = os.path.dirname(cur)
    return sorted(out)


def profile(spec, run):
    """Our own sandbox for a kind without Bash, as profile text (KIT-630 row r5a). The last
    matching rule wins, so each exception comes after what it is an exception to."""
    walled = ["  (subpath %s)" % _sb(run["store"]), "  (subpath %s)" % _sb(run["pins"])]
    walled += ["  (literal %s)" % _sb(k) for k in run["keys"]]
    inputs = ["  (subpath %s)" % _sb(run["run_dir"])]
    if run.get("pin"):
        inputs.append("  (literal %s)" % _sb(run["pin"]))
    # The program checks each folder on a path before it reads a file there, and refuses a file
    # whose path it cannot check (the probe, 18 Nov 2026). So the folders between a wall and the
    # run's own inputs may be looked at, by their metadata only: their entries cannot be listed.
    way = []
    for item in [run["run_dir"]] + ([run["pin"]] if run.get("pin") else []):
        for folder in (run["store"], run["pins"]):
            way += [f for f in _on_the_way(item, folder) if f not in way]
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
    ] + inputs[:-1] + [inputs[-1] + ")"]
        + ([
            "; ...and the folders on the way to them, by their metadata alone: the program checks each one.",
            "(allow file-read-metadata",
        ] + ["  (literal %s)" % _sb(f) for f in way[:-1]] + ["  (literal %s))" % _sb(way[-1])] if way else [])
        + [""])


def _option_value(name, value):
    """One option's value as the program is given it: text, never read as an option of its own."""
    if not isinstance(value, str) or "\0" in value or (value.startswith("-") and value != "-"):
        raise ValueError("%s: its value must be text that is not an option" % name)
    return value


def line(program, model, output="json", session_id=None, resume=False, persist=True,
         setting_sources=None, strict_mcp=False, mcp_config=None, settings=None, tools=None,
         allowed=None, permission_mode=None, permission_prompts=None, system_prompt=None,
         append_system_prompt_file=None, max_turns=None, max_budget_usd=None):
    """One headless command line of the program (KIT-708), every option in one fixed order, from
    the options its caller names. A caller's values go in as given: it formats its own numbers.
    The prompt is never in it: it goes on stdin. ValueError for an option it cannot spell safely:
    a permission mode that skips the checks, or a value the program would read as an option."""
    if output not in OUTPUTS:
        raise ValueError("output: one of %s" % ", ".join(sorted(OUTPUTS)))
    argv = [_option_value("program", program), "-p", "--model", _option_value("model", model)]
    argv += OUTPUTS[output]
    if session_id is not None:
        argv += ["--resume" if resume else "--session-id", _option_value("session", session_id)]
    if not persist:
        argv.append("--no-session-persistence")
    if setting_sources is not None:
        argv += ["--setting-sources", _option_value("setting_sources", setting_sources)]
    if strict_mcp:
        argv.append("--strict-mcp-config")
    if mcp_config is not None:
        argv += ["--mcp-config", _option_value("mcp_config", mcp_config)]
    if settings is not None:
        argv += ["--settings", _option_value("settings", settings)]
    if tools is not None:
        argv += ["--tools", ",".join(_option_value("tools", t) for t in tools)]
    if allowed:
        argv += ["--allowedTools"] + [_option_value("allowed", rule) for rule in allowed]
    if permission_mode is not None:
        if permission_mode not in PERMISSION_MODES:
            raise ValueError("permission_mode: only %s: no mode that skips the checks"
                             % ", ".join(PERMISSION_MODES))
        argv += ["--permission-mode", permission_mode]
    if permission_prompts is not None:
        argv += ["--permission-prompts", _option_value("permission_prompts", permission_prompts)]
    if system_prompt is not None:
        argv += ["--system-prompt", _option_value("system_prompt", system_prompt)]
    if append_system_prompt_file is not None:
        argv += ["--append-system-prompt-file",
                 _option_value("append_system_prompt_file", append_system_prompt_file)]
    if max_turns is not None:
        argv += ["--max-turns", _option_value("max_turns", str(max_turns))]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", _option_value("max_budget_usd", str(max_budget_usd))]
    return argv


def command(spec, run, output="json"):
    """The one command line of a session the runner starts, from the kind's data. The prompt is not
    in it: it goes on stdin."""
    argv = line(run["claude_bin"], run["model"], output=output, session_id=run["session_id"],
                resume=run["resume"], setting_sources="", strict_mcp=True,
                mcp_config=os.path.join(run["run_dir"], "mcp.json"),
                settings=os.path.join(run["run_dir"], "settings.json"), tools=spec["tools"],
                allowed=allowed(spec, run), permission_mode="dontAsk", permission_prompts="none",
                append_system_prompt_file=os.path.join(run["run_dir"], "rules.md"),
                max_turns=run["budget"]["maxTurns"], max_budget_usd="%g" % run["budget"]["maxUsd"])
    if spec["wall"] == "own-sandbox":
        argv = [SANDBOX_EXEC, "-f", os.path.join(run["run_dir"], "sandbox.sb")] + argv
    return argv


# The oldest program that knows every option command() uses: `--permission-prompts` arrived in
# 2.1.259 (the program's own changelog). An older one stops at its first unknown option, with exit
# 1 and no output, before any session starts.
OLDEST = "2.1.259"
# Options the program takes but its --help does not list (2.1.287's help, read 18 Nov 2026). KIT-630's
# live runs passed both to 2.1.247 and to 2.1.287, so a check of the help must not count them missing.
UNLISTED = ("--append-system-prompt-file", "--max-turns")


def flags_used(specs):
    """Every option the command lines for these kinds can hold, started or resumed, in either
    output: what a program's own --help must list before a session of it can start."""
    p = "/x"
    run = {"claude_bin": p, "model": "m", "workspace": p, "run_dir": p, "tmp": p, "store": p, "pins": p,
           "pin": "", "home": p, "keys": [p], "engine": p, "hosts": [], "session_id": "s",
           "budget": {"maxTurns": 1, "maxUsd": 1.0}, "extra_allowed": []}
    found = set()
    for spec in specs:
        for resume in (False, True):
            for output in RUNNER_OUTPUTS:
                found.update(a for a in command(spec, dict(run, resume=resume), output) if a.startswith("--"))
    return sorted(found)


def search_path(claude_bin):
    """The program's own folder first (a node-run program needs its node there), then the fixed
    path, each folder once."""
    out = []
    for d in [os.path.dirname(claude_bin)] + voice.DEFAULT_PATH.split(":"):
        if d and d not in out:
            out.append(d)
    return ":".join(out)


def environment(spec, run, token, push=None):
    """Exactly the kind's names, each from its one source here. `push` is the push token (KIT-886).
    It reaches the program only under a name the kind's data lists, so a kind that lists none never
    gets it, whatever is offered. A kind that lists it and is offered none is a ValueError: it would
    start a session that cannot push."""
    if PUSH_TOKEN_ENV in spec["env"] and not push:
        raise ValueError("a %s session needs the push token, and none was given" % spec.get("kind", "?"))
    src = {"path": search_path(run["claude_bin"]), "home": run["home"], "tmp": run["tmp"],
           "lang": voice.DEFAULT_LANG, "token": token, "1": "1", "devnull": os.devnull,
           "push": push or "", "git-count": str(len(GIT_PUSH_CONFIG))}
    for i, (key, value) in enumerate(GIT_PUSH_CONFIG):
        src["git-key-%d" % i], src["git-value-%d" % i] = key, value
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
    import shutil
    import subprocess
    import tempfile
    import pipeline_session_kinds as kinds_data
    failures, counted = [], [0]

    def expect(name, got, want):
        counted[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    def refused(fn):
        try:
            fn()
        except ValueError as exc:
            return str(exc)
        return None

    kinds, bad = kinds_data.load()
    expect("the kinds load", bad, [])
    for name, spec in sorted(kinds.items()):
        expect("%s: this adapter can build every part of it" % name, check(spec), [])
    expect("the look tool is a server this adapter knows; another is said",
           (check(dict(kinds["acceptance"], tool_servers=["look"])),
            check(dict(kinds["acceptance"], tool_servers=["tracker"]))),
           ([], ["acceptance: tool_servers: the claude adapter has no tool server but the session's own "
                 "ticket tools and the look tool, which the runner writes itself"]))

    # ---- the one builder (KIT-708): every option, in its one order ----------- #
    expect("line(): every option it spells, in its one order",
           line("/bin/claude", "sonnet", output="stream", session_id="s-1", resume=True, persist=False,
                setting_sources="", strict_mcp=True, mcp_config="/r/mcp.json", settings="/r/settings.json",
                tools=["Bash", "Read"], allowed=["Bash", "Read(//w/**)"], permission_mode="dontAsk",
                permission_prompts="none", system_prompt="be brief", append_system_prompt_file="/r/rules.md",
                max_turns=3, max_budget_usd="0.5"),
           ["/bin/claude", "-p", "--model", "sonnet", "--output-format", "stream-json", "--verbose",
            "--resume", "s-1", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
            "--mcp-config", "/r/mcp.json", "--settings", "/r/settings.json", "--tools", "Bash,Read",
            "--allowedTools", "Bash", "Read(//w/**)", "--permission-mode", "dontAsk",
            "--permission-prompts", "none", "--system-prompt", "be brief",
            "--append-system-prompt-file", "/r/rules.md", "--max-turns", "3", "--max-budget-usd", "0.5"])
    expect("line(): only what a caller names; no tools at all is an empty --tools",
           line("claude", "haiku", tools=[], persist=False),
           ["claude", "-p", "--model", "haiku", "--output-format", "json", "--no-session-persistence",
            "--tools", ""])
    expect("line(): the turn's streamed output adds partial messages and changes nothing else",
           line("c", "m", output="stream-partial")[4:], ["--output-format", "stream-json", "--verbose",
                                                         "--include-partial-messages"])
    for what, kw, want in (
            ("a mode that skips the checks", {"permission_mode": "bypassPermissions"},
             "permission_mode: only dontAsk: no mode that skips the checks"),
            ("an allow rule the program would read as an option",
             {"allowed": ["--dangerously-skip-permissions"]}, "allowed: its value must be text that is not an option"),
            ("a tool named like an option", {"tools": ["--add-dir"]}, "tools: its value must be text that is not an option"),
            ("an output it does not know", {"output": "text"}, "output: one of json, stream, stream-partial")):
        expect("line() refuses %s" % what, refused(lambda: line("c", "m", **kw)), want)

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
    expect("the options a program's help must list: every one the kinds' lines can hold",
           flags_used(kinds.values()),
           ["--allowedTools", "--append-system-prompt-file", "--max-budget-usd", "--max-turns", "--mcp-config",
            "--model", "--output-format", "--permission-mode", "--permission-prompts", "--resume",
            "--session-id", "--setting-sources", "--settings", "--strict-mcp-config", "--tools", "--verbose"])
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
        # The program's own list of settings that weaken its sandbox. A Unix socket allowed would let
        # Bash reach the program's cross-session inbox, which takes messages into a session.
        weakening = ("allowUnixSockets", "allowAllUnixSockets", "allowLocalBinding", "allowMachLookup",
                     "enableWeakerNestedSandbox", "enableWeakerNetworkIsolation", "allowAppleEvents")
        expect("%s: no setting that weakens the sandbox" % name,
               [k for k in weakening if k in json.dumps(got)], [])
        expect("%s: no file tool may write anywhere in the sessions' shared home, nor read its transcripts" % name,
               [r for r in got["permissions"]["deny"] if P["home"] in r],
               ["Read(/%s/.claude/**)" % P["home"], "Read(/%s/.claude.json)" % P["home"],
                "Edit(/%s/**)" % P["home"], "Write(/%s/**)" % P["home"]])
    expect("a path with a space is quoted for the hook's shell",
           shlex.split(guard_command(dict(run, workspace="/w s/ENG-1"), "pre-tool-use"))[7], "/w s/ENG-1")
    expect("the adapter adds no tool server of its own", mcp(kinds["code"], run), {"mcpServers": {}})

    # ---- the environment -------------------------------------------------- #
    push = "fake-push-token-for-the-selftest"           # never a real token: nothing here reads one
    for name in sorted(kinds):
        env = environment(kinds[name], run, "a-token", push=push)
        expect("%s: its environment is exactly its kind's names" % name, sorted(env), sorted(kinds[name]["env"]))
        expect("%s: no API key, proxy or base address can be among them" % name,
               [k for k in env if PROGRAM_VARS.match(k) and k not in ENV_SOURCES], [])
    env = environment(kinds["code"], run, "a-token", push=push)
    expect("the environment's values", (env["HOME"], env["TMPDIR"], env[voice.CHILD_TOKEN_VAR],
                                         env["PATH"].split(":")[0]), (P["home"], run["tmp"], "a-token", "/opt/x/bin"))
    expect("every kind with Bash runs git with no global config, so nothing in the shared home is git's config",
           sorted(k for k in kinds if environment(kinds[k], run, "t", push=push).get("GIT_CONFIG_GLOBAL")
                  == os.devnull),
           sorted(k for k in kinds if "Bash" in kinds[k]["tools"]))
    # KIT-886: the push token, offered to every kind, reaches code, fix and conflict alone.
    import pipeline_session_kinds as push_names
    expect("the adapter re-exports the push token's names from the kinds' own data, by name",
           [n for n in ("PUSH_TOKEN_ENV", "PUSH_ENV") if globals().get(n) != getattr(push_names, n)], [])
    held = dict((k, environment(kinds[k], run, "t", push=push)) for k in sorted(kinds))
    expect("offered to every kind, the push token reaches code, fix and conflict, under its one name",
           sorted((k, e[PUSH_TOKEN_ENV]) for k, e in held.items() if PUSH_TOKEN_ENV in e),
           [("code", push), ("conflict", push), ("fix", push)])
    expect("…and review, plan and acceptance get none of its names, nor git's helper",
           [(k, n) for k, e in sorted(held.items()) if k in ("review", "plan", "acceptance")
            for n in PUSH_ENV if n in e], [])
    expect("…nor any value that holds it",
           [k for k, e in sorted(held.items()) if k in ("review", "plan", "acceptance")
            and any(push in v for v in e.values())], [])
    try:
        environment(kinds["fix"], run, "t")
        said = "no error"
    except ValueError as exc:
        said = str(exc)
    expect("a kind that holds the push token, offered none, is said: it never starts unable to push",
           said, "a fix session needs the push token, and none was given")
    sandboxes = dict((k, settings(kinds[k], run).get("sandbox") or {}) for k in held)
    expect("a kind that holds it runs Bash inside the built-in sandbox, held to its host list (KIT-886)",
           sorted((k, (sandboxes[k].get("network") or {}).get("allowedDomains"),
                   sandboxes[k].get("allowUnsandboxedCommands")) for k in held
                  if PUSH_TOKEN_ENV in held[k]),
           [(k, run["hosts"], False) for k in ("code", "conflict", "fix")])
    expect("the token is in nothing the adapter writes to a file: settings, tool servers, profile, command line",
           [k for k in sorted(kinds) if push in json.dumps([settings(kinds[k], run), mcp(kinds[k], run),
                                                            command(kinds[k], run), profile(kinds[k], run)])], [])

    # git's helper for it, run by git itself (KIT-886). A stored helper is planted in a repository of
    # the test's own, as the runner's clone could hold one. git's system config, where a keychain
    # helper often sits, is never read, and a password that is not one of the two made up here is
    # never shown: no real keychain or token can reach this test's words.
    gdir = os.path.join(tmp, "git-helper")
    if shutil.which("git", path=held["code"]["PATH"]):
        subprocess.run(["git", "init", "--quiet", gdir], env={"PATH": held["code"]["PATH"], "HOME": tmp,
                                                            "GIT_CONFIG_NOSYSTEM": "1",
                                                            "GIT_CONFIG_GLOBAL": os.devnull},
                       check=True, capture_output=True, timeout=60)
        with open(os.path.join(gdir, ".git", "config"), "a") as fh:
            fh.write('[credential]\n\thelper = "!f() { cat >/dev/null; echo username=stored; '
                     'echo password=from-a-stored-helper; }; f"\n')

        def fill(env, host):
            """What git's helpers hand git for `host`, as its username and password lines."""
            r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=%s\n\n" % host,
                               env=dict(env, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0"), cwd=gdir,
                               capture_output=True, text=True, timeout=60)
            known = ("password=" + push, "password=from-a-stored-helper")
            return [ln if not ln.startswith("password=") or ln in known else "password=<not one made up here>"
                    for ln in r.stdout.splitlines() if ln.startswith(("username=", "password="))]

        no_reset = dict(held["code"], GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=GIT_PUSH_CONFIG[1][0],
                        GIT_CONFIG_VALUE_0=GIT_PUSH_CONFIG[1][1])
        expect("git's helper hands a push kind's git the push token, for github.com, and the repository's "
               "stored helper is never asked", fill(held["code"], "github.com"),
               ["username=x-access-token", "password=" + push])
        expect("…and nothing for a host that is not github.com", fill(held["code"], "example.org"), [])
        expect("…while without the clearing, the stored helper is asked first and its answer wins: why a push "
               "kind clears every helper", fill(no_reset, "github.com"),
               ["username=stored", "password=from-a-stored-helper"])
        expect("…and a kind without the push token gets the stored helper's answer: why the probe asks the "
               "acceptance kind's git", fill(held["acceptance"], "github.com"),
               ["username=stored", "password=from-a-stored-helper"])
    else:
        print("  (skipped: git's helper needs git)")
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
            "(deny file-read* file-write*", "(allow file-read*", "(allow file-read-metadata"])
    expect("the profile names each place once, and the store and the pins again for their metadata alone",
           [text.count('"%s"' % p) for p in (P["store"], P["pins"], key, rdir, pin, P["home"], run["tmp"])],
           [2, 2, 1, 1, 1, 1, 1])
    expect("…exactly the folders between a wall and the run's own inputs, by metadata",
           re.findall(r'\(literal "([^"]+)"\)', text.split("(allow file-read-metadata")[1]
                      if "(allow file-read-metadata" in text else ""),
           [P["store"], os.path.join(P["store"], "runs"), P["pins"]])
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
                  # The program checks every folder on a path before it reads a file there (it
                  # refused its own settings file on 18 Nov when one could not be checked).
                  "import os\n"
                  "for name, path in json.loads(sys.argv[3]).items():\n"
                  "    parts, ok = path.split('/')[1:], True\n"
                  "    for i in range(1, len(parts) + 1):\n"
                  "        try:\n            os.lstat('/' + '/'.join(parts[:i]))\n"
                  "        except OSError:\n            ok = False\n"
                  "    out['every folder on the path to ' + name] = ok\n"
                  "try:\n    os.listdir(sys.argv[4]); out['list the store'] = True\n"
                  "except OSError:\n    out['list the store'] = False\n"
                  "print(json.dumps(out, sort_keys=True))\n")
        writes = {"home": os.path.join(P["home"], "state.json"), "tmp": os.path.join(run["tmp"], "t"),
                  "workspace": os.path.join(ws, "made.txt"), "outside": os.path.join(P["outside"], "made.txt"),
                  "store": os.path.join(rdir, "made.txt"), "engine": os.path.join(P["engine"], "made.txt")}
        r = subprocess.run([SANDBOX_EXEC, "-f", os.path.join(rdir, "sandbox.sb"), PYTHON, "-I", "-c", script,
                            json.dumps(files), json.dumps(writes),
                            json.dumps({"own-input": files["own-input"], "own-pin": pin}), P["store"]],
                           capture_output=True, text=True, timeout=60)
        got = json.loads(r.stdout or "{}") if r.returncode == 0 else {"exit": r.returncode, "err": r.stderr[-300:]}
        expect("inside our own sandbox, for real: its inputs, its pin and the workspace are read; the store's other "
               "runs, other pins and the key are not; only its home and temp folder are written", got,
               {"own-input": True, "own-pin": True, "inside": True, "other-run": False, "other-pin": False,
                "key": False, "write home": True, "write tmp": True, "write workspace": False,
                "write outside": False, "write store": False, "write engine": False,
                "every folder on the path to own-input": True, "every folder on the path to own-pin": True,
                "list the store": False})
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
