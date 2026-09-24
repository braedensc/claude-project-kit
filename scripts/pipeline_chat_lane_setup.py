#!/usr/bin/env python3
"""Chat-lane composer — the dispatcher's built-in Slack lane, composed for a person to apply.

    python3 scripts/pipeline_chat_lane_setup.py compose [--piece N] [--conf chat-lane.conf]
    python3 scripts/pipeline_chat_lane_setup.py verify  [--conf chat-lane.conf]
    python3 scripts/pipeline_chat_lane_setup.py merge [--apply]
    python3 scripts/pipeline_chat_lane_setup.py env-names [--remove]
    python3 scripts/pipeline_chat_lane_setup.py front-door [--apply] [--remove]
    python3 scripts/pipeline_chat_lane_setup.py card CK-C1
    python3 scripts/pipeline_chat_lane_setup.py --selftest

WHAT IT IS.  The dispatcher ships a Slack transport: mention its bot in a channel and it
starts a session for that thread. That session gets no sandbox. This file COMPOSES every
piece a deployment needs to turn that lane on under the owner's conditions (KIT-117,
decision and correction of 2026-09-17), and VERIFIES the live result.

    compose   prints every piece, or one (`--piece N`; piece 4 alone is only its script).
              Reads no live file, needs no role-account access, runs anywhere. Exit 0, or
              2 on a conf error.
    verify    read-only live measurement: four files as the role account, and pf's
              loaded rules as root. One outcome per check.
    merge     pieces 1, 2 and 5 into the dispatcher config and the role account's user
              settings. Dry run by default; `--apply` writes.
    env-names piece 3: asks for the chat app's two secrets at hidden prompts and writes
              the four names into the dispatcher's env file. `--remove` takes them out.
    front-door piece 7: adds the Slack path to the front door's one allowlist line.
              Dry run by default; `--apply` writes; `--remove` takes the path off.
    card      prints a checkpoint card: CK-C1 (create the chat app), CK-C2 (the front
              door), CK-C3 (the live check in the channel), CK-C4 (the dispatcher's port,
              from a second device), CK-C5 (restart the dispatcher, only when it is
              idle), CK-C6 (turn the lane off).

WHAT CHANGED ON 2026-09-24 (owner decision, KIT-197).  This file used to print and read
only. The chat lane was then switched on by hand on a deployment, and three live-file
edits it only described were done with three small scripts instead, tested on fixtures
and then run for real. The owner decided the composer itself gains those writers. So:

    `merge --apply`, `env-names` and `front-door --apply` WRITE. Each is a subcommand a
    PERSON runs; each refuses in an agent environment, before the conf is read; each
    writes AS THE ROLE ACCOUNT, through one program handed to `sudo -u <role> /bin/sh`,
    with every value on standard input and never in an argument; each backs the file up
    first, under the role account's own home, and writes nothing when there is nothing
    to change. `merge --apply` and `front-door --apply` also check the file is still the
    one they planned from (a checksum), as they start and again just before the write.
    `env-names` has no separate plan: it reads and replaces the env file in one
    pass, and nothing else rewrites that file. `compose`, `verify` and `card` still
    write nothing.

WHAT IT NEVER DOES.  It never restarts anything and never loads a job: the restarts and
the port block stay PRINTED commands a person runs (installers never load jobs). It never
prints a value from the env file, the config or the settings file. There is no merge,
approve, label or ticket-state path anywhere in this file, and `--selftest` scans its own
source: outside the three writer programs and the one function that runs them, the
source may hold no write at all.

EXIT CODES — the Stage E installer's, contract §13. The word on a line (REFUSED, NOT
MEASURED) says what happened; the exit code says which kind of nothing it was:
    0   compose printed / verify measured every check as applied / a writer wrote, or
        found nothing to write, and its rows measure as applied
    1   FAILED — a check found something broken, or a write failed (a writer that fails
        after writing puts its backup back)
    2   USAGE or CONFIG — nothing was attempted: a conf error, a conf key the subcommand
        needs is unset, or a secret of the wrong shape
    3   REFUSED for safety — an agent environment; or, for merge and front-door, the
        file changed between the read and the write; or the token offered is the
        notifier's. Nothing was written.
    4   UNKNOWN — a check could not measure itself, or env-names could not compare the
        token with the notifier's. Not a pass.
    5   NO ADMINISTRATOR ACCESS — `sudo` absent or declined; nothing was read
    10  BLOCKED-ON-HUMAN — drift: a piece is not applied, or not as composed; or a step
        must come first (env-names before the fence or the port block, or without a
        terminal; a file a writer will not touch — missing, a symbolic link, another
        account's; not exactly one allowlist line, or one that reaches the
        dispatcher's control routes). A writer's dry run that found something to
        write exits 10: it did nothing, on purpose

`verify` REFUSES IN AN AGENT ENVIRONMENT, and that is stricter than the Stage E
installer's `verify` on purpose. That one still runs under a model with its credential
reads gated off, because most of its rows measure something that holds no credential.
Here every row reads the dispatcher's config (which holds the tracker's OAuth tokens —
the dispatcher writes them there, cyrus-edge-worker EdgeWorker.js:5154-5211) or its env
file (which holds the chat bot's token). The probe prints names and tool lists only, but
a session has no business running it: there is no gated-off version that still measures
anything. Tamper-evident, not tamper-proof — the markers are environment variables a
session could unset (see the same note in scripts/pipeline_stage_e_setup.py).

WHERE EVERY CLAIM COMES FROM.  The dispatcher's source at its installed version, 0.2.69,
cited as <package>/<file>:<line>. Package names drop the `cyrus-` prefix below where
unambiguous. The Slack scope facts come from Slack's method and event reference pages;
the permission-rule facts from Claude Code's permissions page; both read 2026-09-17.

THREE THINGS SETTLED FROM SOURCE BEFORE COMPOSING
1. EVERY EFFECT OF CYRUS_HOST_EXTERNAL=true (all eleven reads, 0.2.69):
   a. Slack webhooks are HMAC-checked with SLACK_SIGNING_SECRET (edge-worker
      EdgeWorker.js:730-752), and the check is re-resolved per request, so it switches
      on without a restart (slack-event-transport SlackEventTransport.js:65-80). In the
      no-repository modes too (cli WorkerService.js:117-129).
   b. THE DISPATCHER'S HTTP SERVER BINDS 0.0.0.0 INSTEAD OF localhost (cli
      WorkerService.js:149, 191 -> EdgeWorker.js:254-257 -> SharedApplicationServer.js:75-78;
      the no-repository modes at WorkerService.js:85-88). Every route is then reachable
      from the local network, not only through a front door: the config-update routes
      (EdgeWorker.js:516-521), the dispatcher's tool server at /mcp/cyrus-tools
      (EdgeWorker.js:97, 3948-3965), whose auth check passes everything when
      CYRUS_API_KEY is unset (McpConfigService.js:166-170), /status (533-541) and every
      webhook. Read at start only. The owner's answer (2026-09-17) is piece 4: a
      packet-filter rule refusing the port off loopback, proven by card CK-C4.
   c. Webhook source-address checks turn on by default (EdgeWorker.js:241-252) unless
      WEBHOOK_IP_VALIDATION=false, and the dispatcher fetches api.github.com/meta at
      start (EdgeWorker.js:411-416; core WebhookIpValidator.js:122-141). The tracker
      transport gets the list only when LINEAR_DIRECT_WEBHOOKS=true (EdgeWorker.js:475-489),
      the GitHub one only in signature mode (EdgeWorker.js:561-580); Slack gets none
      (EdgeWorker.js:743-750). Fastify trusts X-Forwarded-For
      (SharedApplicationServer.js:37-40), and the tracker transport refuses a webhook whose
      address misses the list with a 403, loopback included (linear-event-transport
      LinearEventTransport.js:89-96). A front door that does not trust its local tunnel
      client forwards 127.0.0.1, so on a signature-verifying dispatcher every tracker
      webhook would be refused. Piece 3 therefore sets WEBHOOK_IP_VALIDATION=false, the
      only value that keeps the checks off (EdgeWorker.js:244-246) — today's behaviour.
   d. GitHub and GitLab webhooks switch to signature checks when their secrets are set
      (EdgeWorker.js:561-574, 625-631); with no secret, nothing changes.
   e. The tracker sign-in flow uses a local authorize page instead of the hosted proxy
      when LINEAR_CLIENT_ID is also set (SharedApplicationServer.js:209-218), and the
      self-auth command's callback listener binds 0.0.0.0 (cli SelfAuthCommand.js:153-156).
   Not touched: the Cloudflare tunnel, keyed on CLOUDFLARE_TOKEN
   (SharedApplicationServer.js:81-84); the tool-server URL sessions use, fixed at
   127.0.0.1 (EdgeWorker.js:4224-4230); where any token is read.

2. HOT RELOAD. `slackAllowedTools` IS hot-reloaded, for sessions built after the reload.
   The config file is watched (edge-worker ConfigManager.js:51-62); on `change` the new
   value is merged in (ConfigManager.js:181) and counts as a global change
   (ConfigManager.js:273), which is emitted (ConfigManager.js:127-132); the handler calls
   `toolPermissionResolver.setConfig` (EdgeWorker.js:362-381), and that resolver is the
   same object the chat config builder holds (EdgeWorker.js:323, 330;
   RunnerConfigBuilder.js:33-34), which reads the list at call time
   (ToolPermissionResolver.js:68-71) each time a chat session is built or resumed
   (ChatSessionHandler.js:139, 279). Four limits: a runner still streaming keeps
   the list it started with, since follow-ups are injected, not rebuilt
   (ChatSessionHandler.js:70-77); REMOVING the key does not revert until a restart,
   because the merge keeps the old in-memory value (ConfigManager.js:181); only a
   `change` event reloads (ConfigManager.js:59); and the handler's earlier awaits have no
   catch, so a failure there skips `setConfig` (EdgeWorker.js:372-380). The env file is
   also watched and re-applied (cli Application.js:52-78), but with `override: true`
   (Application.js:54), which sets names and never unsets one. And (1b) and (1c) are read
   at start only. So: RESTART DELIBERATELY after applying, and look for the reload lines
   in the log if you do not.

3. MINIMAL SLACK SCOPES AND EVENTS — see SCOPE_CITES and EVENT_CITES below: every Web API
   method the transport, the chat adapter and the reaction service call, and every event
   type they consume. The Slack tool server the dispatcher also starts (edge-worker
   McpConfigService.js:87-99) may want more; none is added.
"""
import argparse
import contextlib
import getpass
import inspect
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# The agent-environment markers are IMPORTED, never copied — a copied list drifts.
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
# The Stage E installer's exit codes, outcomes, role-account runner, administrator-access
# session, card helpers and its test of "a review entry this installer wrote". Imported so
# the two installers cannot disagree on any of them.
from pipeline_stage_e_setup import _self_path as _stage_e_self_path  # noqa: E402
from pipeline_stage_e_setup import (  # noqa: E402
    ALREADY_DONE, BLOCKED, FAILED, UNKNOWN,
    EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_NOPRIV, EX_BLOCKED,
    REVIEW_BRIEF_FINGERPRINT, TRACKER_FENCE_SERVERS,
    _ACCOUNT_RE, _BLOB_RE, _CRED_PREFIXES, _ENV_NAME_RE, _RDNS_RE,
    NoPrivilege, Runner, SudoSession,
    _fill, _owns_review_entry, _server_rules, _wrap,
)
# The Stage A installer's planning brief: its first sentence identifies a planning entry.
from pipeline_stage_a_setup import PLANNING_BRIEF_FINGERPRINT  # noqa: E402

SOURCE_VERSION = "0.2.69"
ROOT = os.path.dirname(HERE)

# --------------------------------------------------------------------------- #
# Piece 1 — the trimmed chat grant.  THE LIST THE OWNER SIGNED OFF, verbatim.
#
# Setting `slackAllowedTools` replaces the built-in chat list (core
# config-schemas.js:387; edge-worker ToolPermissionResolver.js:68-71), which holds
# Monitor, Task, ScheduleWakeup, Skill and six task-lifecycle tools
# (core allowed-tools-defaults.js:91-119). `Monitor` runs a shell command, so leaving it
# is a shell by another name (KIT-117). On the chat lane this list IS the fence: no
# permission callback is attached there: one is made only when a caller passes
# onAskUserQuestion (claude-runner ClaudeRunner.js:196-199), the issue builder does
# (RunnerConfigBuilder.js:229-232), and the chat builder does not.
# --------------------------------------------------------------------------- #
OWNER_GRANT_BASE = ["Read", "WebFetch", "WebSearch", "SendMessage", "ToolSearch",
                    "mcp__slack", "mcp__linear"]

# THE PULL RULE IS PER REPOSITORY, AND THAT IS THE WHOLE POINT (owner decision, 2026-09-17,
# after the review of PR #147). The first list carried `Bash(git -C * pull)`. A Bash rule
# matches the whole command text with `*` standing in for any text, so that rule also
# matches `git -C <dir> -c core.fsmonitor='<any command>' status pull`: everything before
# the first `*` is `git -C `, an option rather than a subcommand, so every option and every
# subcommand in between is free — and `-c` makes git run a program the caller names. A
# verifier ran it: git executed the named program. Claude Code's own permissions page uses
# `Bash(git * main)` as the worked example ("That includes `-c`, which makes git run a
# program you name"), and its errors page uses this exact `git -C *` shape, saying the rule
# is kept and matches as written. On the chat lane the grant is the only fence, so that was
# unprompted command execution as the role account, with no sandbox.
#
# So: no wildcard. One pair of literal rules per repository the dispatcher serves, with the
# path fixed, so nothing can stand before `pull`. A new repository needs a new pair, or the
# chat lane's pull of it simply stops working — said out loud in compose and in the doc,
# because a silent stop is the cost of this shape.
PULL_RULE_FORMS = ("Bash(git -C %s pull)", "Bash(git -C %s pull --ff-only)")
# What compose prints in place of a path it cannot know: it reads nothing live.
PLACEHOLDER_REPO_PATH = "/ABSOLUTE/PATH/TO/REPOSITORY"


def pull_rules(repo_path):
    # `replace`, not `%`: a form that lost its placeholder would raise, and a crash hides
    # the finding the checker exists to report. This way the rule is built, and
    # `grant_problems` refuses it by name.
    return [form.replace("%s", repo_path) for form in PULL_RULE_FORMS]


def owner_grant(repo_paths=(PLACEHOLDER_REPO_PATH,)):
    """The approved grant for a machine serving these repository paths."""
    out = list(OWNER_GRANT_BASE)
    for path in repo_paths:
        for rule in pull_rules(path):
            if rule not in out:
                out.append(rule)
    return out


# Tools that must never appear in the grant. A mutant grant holding any is red.
NEVER_IN_GRANT = ("Monitor", "Task", "Agent", "ScheduleWakeup", "Skill", "Write", "Edit",
                  "NotebookEdit", "CronCreate", "RemoteTrigger", "Workflow")

# Programs that are a shell by another name: a rule naming one grants whatever it is handed.
SHELL_PROGRAMS = ("sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "env", "eval",
                  "exec", "xargs", "nohup", "script", "sudo", "doas", "ssh", "perl",
                  "python", "python2", "python3", "ruby", "node", "osascript", "open",
                  "awk", "find")
# Shell punctuation inside a rule: a second command hides behind any of them.
SHELL_OPERATORS = ("&&", "||", ";", "|", "`", "$(", ">", "<", "&")

# Options that name a program the command will run. A rule carrying one is a shell even
# with no wildcard in it: `Bash(git -c core.fsmonitor=/tmp/x -C /repo pull)` matches only
# that exact text, but that text already runs /tmp/x. The wildcard checks below never see
# it, and `verify` runs this over the LIVE grant, where a hand edit back toward "let it
# pull with a config override" would land. `-c` and `--config-env` set git config such as
# core.fsmonitor, core.pager, core.sshCommand or an alias; the other three name a binary
# outright. `-C`, which the composed rules use, only changes directory (found in the
# second review of PR #147).
PROGRAM_RUNNING_OPTIONS = ("-c", "--config-env", "--exec-path", "--upload-pack",
                           "--receive-pack")

# What the dispatcher appends whatever the list says: `mcp__<name>` for every server it
# built for the session (ToolPermissionResolver.js:72-77), with the chat lane's
# disallowedTools hard-coded empty (RunnerConfigBuilder.js:78). `mcp__linear` and
# `mcp__slack` are in the approved list already; these two are not, and stay.
APPENDED_SERVERS = (
    ("mcp__cyrus-tools",
     "The dispatcher's own tool server. It runs inside the dispatcher's process, outside "
     "any sandbox, reached over loopback (McpConfigService.js:70-81; EdgeWorker.js:4224-4230). "
     "It carries linear_agent_give_feedback, which delivers a message into any running "
     "session by id and never checks the caller (mcp-tools cyrus-tools/index.js:181; "
     "EdgeWorker.js:4138-4170), and linear_upload_file, which reads any path the role "
     "account can read and uploads it to the tracker, public if asked "
     "(cyrus-tools/index.js:72-107). Also issue relations, child issues and agent-session "
     "listing (cyrus-tools/index.js:237, 319, 421, 517)."),
    ("mcp__cyrus-docs",
     "A documentation search served by a third party at https://atcyrus.com/docs/mcp "
     "(McpConfigService.js:82-85). That service sees what the session searches for."),
)


def bash_rule_problems(tool):
    """Every way one `Bash(...)` rule grants more than its words suggest.

    The rules this composes carry no wildcard at all, so they pass trivially. The point is
    that a hand-edited or future one cannot quietly reintroduce the shape the owner removed.
    """
    if tool == "Bash":
        return ["an unscoped Bash (%s) is in the grant" % tool]
    m = re.match(r"^Bash\((.*)\)$", tool, re.S)
    if not m:
        return []
    inner = m.group(1).strip()
    problems = []
    if not inner or inner == "*":
        return ["an unscoped Bash (%s) is in the grant" % tool]
    for op in SHELL_OPERATORS:
        if op in inner:
            problems.append("%s carries the shell punctuation %r: a second command can hide "
                            "behind it" % (tool, op))
            break
    tokens = inner.split()
    program = os.path.basename(tokens[0]) if tokens else ""
    if program in SHELL_PROGRAMS or any(os.path.basename(t) in SHELL_PROGRAMS
                                        for t in tokens[1:]):
        problems.append("%s names a shell, or a program that runs one: it grants whatever "
                        "it is handed" % tool)
    named = [t for t in tokens if t.split("=", 1)[0] in PROGRAM_RUNNING_OPTIONS]
    if named:
        problems.append("%s carries %s, an option that names a program the command will "
                        "run: git's -c and --config-env set config such as core.fsmonitor "
                        "or core.pager, and --exec-path, --upload-pack and --receive-pack "
                        "name a binary. A pull rule may carry no git option but --ff-only"
                        % (tool, ", ".join(sorted(set(named)))))
    wild = [i for i, t in enumerate(tokens) if "*" in t]
    if wild:
        before = tokens[1:wild[0]]            # the fixed words after the program name
        if not [t for t in before if not t.startswith("-")]:
            problems.append("%s puts its wildcard where the subcommand goes: everything "
                            "before the first * is what limits the rule, so every "
                            "subcommand and every option before it matches — including "
                            "the options that make a program run another program" % tool)
        elif before and before[-1].startswith("-"):
            problems.append("%s ends its fixed words with an option, so the wildcard "
                            "stands in for that option's value and for whatever follows"
                            % tool)
    return problems


def grant_problems(grant):
    """Every way a grant breaks the owner's decision. Empty means acceptable."""
    problems = []
    if not isinstance(grant, list) or not all(isinstance(t, str) for t in grant):
        return ["the grant is not a list of tool names"]
    for tool in grant:
        if tool in NEVER_IN_GRANT:
            problems.append("%s is in the grant" % tool)
        problems.extend(bash_rule_problems(tool))
    if len(set(grant)) != len(grant):
        problems.append("the grant names a tool twice")
    return problems


# --------------------------------------------------------------------------- #
# Piece 2 — the Slack fence on every ticket-lane entry that is not a review entry.
#
# With SLACK_BOT_TOKEN in the dispatcher's environment, EVERY session gets a working Slack
# server (McpConfigService.js:87-99), including ticket-lane sessions, where the permission
# callback approves every tool that is not denied (ClaudeRunner.js:219-228). So the fence
# is `disallowedTools`, which the ticket lane enforces (KIT-157). The two rule forms are
# the Stage E installer's own, so they cannot drift from the reviewer fence.
#
# PLANNING ENTRIES ARE FENCED TOO, and that departs from the brief this was built from.
# The Stage A installer's planning entry sets `linearMcpAttached: false` as its
# structural fence. No package of 0.2.69 read here reads that key, and the ticket-lane
# config builder builds every server for every issue session unconditionally
# (RunnerConfigBuilder.js:177). So a planning session gets the Slack server like any
# other, and only a deny rule closes it.
#
# Review entries are skipped: their fence already names the Slack server
# (pipeline_stage_e_setup.TRACKER_FENCE_SERVERS), and their own installer rewrites them.
# --------------------------------------------------------------------------- #
SLACK_FENCE_RULES = tuple(_server_rules("slack"))


def effective_disallowed(own, default):
    """(list, source) — the list a ticket-lane session of this entry runs under when no
    prompt type overrides it. The entry's own list whenever it is SET, even to [] (the
    dispatcher tests the array's truthiness, and an empty array is truthy:
    ToolPermissionResolver.js:217-220); otherwise `defaultDisallowedTools`
    (ToolPermissionResolver.js:221-224); otherwise nothing (225-226)."""
    if own is not None:
        return list(own), "its own disallowedTools"
    if default is not None:
        return list(default), "defaultDisallowedTools"
    return [], "nothing (no list of its own, no defaultDisallowedTools)"


def fence_entry(own, default):
    """(composed list, missing rules, source). The composed list is the EFFECTIVE list
    plus whichever Slack rules it lacks — so an entry that inherited the defaults gets a
    list that still carries every one of them. Never the two rules alone."""
    eff, source = effective_disallowed(own, default)
    missing = [rule for rule in SLACK_FENCE_RULES if rule not in eff]
    return eff + missing, missing, source


def prompt_type_gaps(prompt_lists):
    """The prompt types whose `disallowedTools` list REPLACES the entry's
    (ToolPermissionResolver.js:199-216: labelPrompts[type], then promptDefaults[type],
    before the entry's own) and lacks a Slack rule — for those sessions, no fence."""
    gaps = []
    for ptype, lst in sorted((prompt_lists or {}).items()):
        if not isinstance(lst, list) or any(rule not in lst for rule in SLACK_FENCE_RULES):
            gaps.append(ptype)
    return gaps


# The number of `appendInstruction` characters the probe carries back — only enough to
# recognise an entry's brief. The selftest pins both fingerprints under it.
INSTRUCTION_HEAD_CHARS = 120


def entry_kind(entry):
    """review | planning | coding, from an entry as the probe reports it."""
    head = entry.get("instructionHead")
    if head is None:
        head = entry.get("appendInstruction")
    probe = {"id": entry.get("id"), "appendInstruction": head or ""}
    if _owns_review_entry(probe):
        return "review"
    if (head or "").startswith(PLANNING_BRIEF_FINGERPRINT):
        return "planning"
    return "coding"


# --------------------------------------------------------------------------- #
# Piece 3 — the dispatcher env file.  NAMES ONLY.
# --------------------------------------------------------------------------- #
ENV_NAMES = ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "CYRUS_HOST_EXTERNAL")

# REQUIRED, owner decision of 2026-09-17 (KIT-117). CYRUS_HOST_EXTERNAL=true turns webhook
# source-address checks on unless this is exactly `false` (EdgeWorker.js:241-252). On a
# dispatcher that verifies tracker signatures (LINEAR_DIRECT_WEBHOOKS=true) the tracker
# webhook then accepts only the tracker's nine published addresses (EdgeWorker.js:475-489;
# core WebhookIpValidator.js:9-19) and answers anything else with 403, loopback included
# (linear-event-transport LinearEventTransport.js:89-96). The address comes from
# X-Forwarded-For, which the server trusts (SharedApplicationServer.js:37-40), and a front
# door that does not trust its local tunnel client replaces that header with 127.0.0.1
# (Caddy's reverse_proxy reference: "by default, the proxy will ignore their values from
# incoming requests"). Every tracker webhook refused: no ticket starts a session. `false`
# keeps today's behaviour, a webhook protected by its signature alone.
IP_VALIDATION_NAME = "WEBHOOK_IP_VALIDATION"

# NEVER SET — owner decision of 2026-09-17 (KIT-117). Any CYRUS_API_KEY makes the dispatcher
# act as paired with the vendor's hosted service: it registers `log_failure_mode`
# (EdgeWorker.js:4016-4027, 4142-4147; mcp-tools cyrus-tools/index.js:636-642), which POSTs
# the session id, a recap, a quote from the conversation, the failure text, the ticket
# identifier and the workspace path to <CYRUS_APP_URL>/api/failure-modes
# (cyrus-tools/failure-modes-http-client.js:4-35), defaulting to https://app.atcyrus.com
# (cloudflare-tunnel-client ConfigApiClient.js:5-12), from the dispatcher's own unsandboxed
# process. Every session's prompt tells it to call that tool (RunnerConfigBuilder.js:87, 207;
# prompts/failureModePromptAddendum.js:21-27). With CYRUS_TEAM_ID as well, session transcripts
# mirror there (EdgeWorker.js:152-173). `verify` checks the first two by name.
HOSTED_PAIRING_NAMES = ("CYRUS_API_KEY", "CYRUS_TEAM_ID")
HOSTED_DO_NOT_SET = HOSTED_PAIRING_NAMES + ("CYRUS_APP_URL",)

# --------------------------------------------------------------------------- #
# Piece 4 — block the dispatcher's port from the network (owner decision, 2026-09-17).
#
# CYRUS_HOST_EXTERNAL=true binds the server to 0.0.0.0 (WorkerService.js:191;
# EdgeWorker.js:254-257). A packet-filter rule refuses its port on every interface but
# loopback. The front door is unaffected: it connects locally.
#
# WHERE THE RULE LIVES. Not in /etc/pf.conf, which a macOS update can reset. Apple's own
# /etc/pf.conf evaluates every anchor attached under `com.apple` — its line 26 reads
# `anchor "com.apple/*"` (read on macOS 26.6.2, 2026-09-17) — and pf.conf(5) says an anchor
# ending in `/*` evaluates every anchor attached at that point, in alphabetical order. So the
# rule loads into its own child anchor there, from its own file, by a root LaunchDaemon at
# boot that also enables pf with `-E` ("Enable the packet filter and increment the pf enable
# reference count", pfctl(8)). With `-a`, `-f` applies only to that anchor (pfctl(8)), so the
# main ruleset is not flushed.
#
# BOTH ADDRESS FAMILIES. The dispatcher passes its host straight to Fastify's listen
# (SharedApplicationServer.js:75-78), and Fastify's server reference says `0.0.0.0` "will
# listen on all IPv4 addresses", while `::` "may also listen on all IPv4 addresses". So
# IPv6 is not expected to answer today; the rule refuses inet6 too, at no cost, in case the
# listen address ever changes.
#
# `quick` because a later anchor's `pass` would otherwise win: pf.conf(5), "The last
# matching rule decides", unless a rule has `quick`. `return` so the refusal is immediate.
# --------------------------------------------------------------------------- #
DEFAULT_DISPATCHER_PORT = "3456"   # cli config/constants.js:7; EdgeWorker.js:254
PF_ANCHOR = "com.apple/250.pipeline-dispatcher-port"
PF_RULES_DIR = "/Library/Application Support/pipeline-dispatcher-port"
PF_RULES_PATH = PF_RULES_DIR + "/pf.rules"
PF_DAEMON_LABEL = "local.pipeline-dispatcher-port"
PF_DAEMON_PLIST = "/Library/LaunchDaemons/%s.plist" % PF_DAEMON_LABEL
PF_DAEMON_LOG = "/var/log/pipeline-dispatcher-port.log"
PF_FAMILIES = ("inet", "inet6")


def pf_rules(port):
    """The anchor file's text: one refusal per address family, loopback exempt."""
    lines = ["# Refuse the dispatcher's port on every interface except loopback, IPv4 and IPv6.",
             "# Loaded into %s at boot by %s." % (PF_ANCHOR, PF_DAEMON_LABEL)]
    for family in PF_FAMILIES:
        lines.append("block return in quick on ! lo0 %s proto tcp from any to any port %s"
                     % (family, port))
    return "\n".join(lines) + "\n"


def pf_plist():
    """The root LaunchDaemon: at load and at every boot, load the anchor and enable pf."""
    args = ["/sbin/pfctl", "-E", "-a", PF_ANCHOR, "-f", PF_RULES_PATH]
    return "\n".join(
        ['<?xml version="1.0" encoding="UTF-8"?>',
         '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
         '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
         '<plist version="1.0">',
         "<dict>",
         "  <key>Label</key>",
         "  <string>%s</string>" % PF_DAEMON_LABEL,
         "  <key>ProgramArguments</key>",
         "  <array>"]
        + ["    <string>%s</string>" % a for a in args]
        + ["  </array>",
           "  <key>RunAtLoad</key>",
           "  <true/>",
           "  <key>StandardOutPath</key>",
           "  <string>%s</string>" % PF_DAEMON_LOG,
           "  <key>StandardErrorPath</key>",
           "  <string>%s</string>" % PF_DAEMON_LOG,
           "</dict>",
           "</plist>"]) + "\n"


# RE-RUNNABLE (KIT-197). A second run used to stop at `launchctl bootstrap` with errno 5,
# because the boot job was still loaded from the first. Now a loaded job is booted out
# first, and the script waits until launchd no longer lists it — `bootout` returns when
# launchd has accepted the request, not when the job is gone (the Stage E installer's
# restart notes). Bounded: 30 polls, one second apart, and the bound says so when it runs
# out — the bootstrap after it then fails with errno 5, and `sh -e` stops there (review of
# KIT-197, finding 41). Each line stands alone, because a person may paste them one at a time.
PF_RELOAD_IF_LOADED = (
    "if sudo launchctl print system/%(label)s >/dev/null 2>&1; then "
    "sudo launchctl bootout system/%(label)s; n=0; "
    "while sudo launchctl print system/%(label)s >/dev/null 2>&1; do "
    "n=$((n+1)); [ $n -ge 30 ] && { echo 'STILL LOADED after 30 s: launchd has not let the "
    "old boot job go. Wait a minute, then run this script again.' >&2; break; }; "
    "sleep 1; done; fi" % {"label": PF_DAEMON_LABEL})


def pf_install_commands(port):
    """The exact commands a person runs, in order, to install, check, load and confirm."""
    rules, plist = '"%s"' % PF_RULES_PATH, PF_DAEMON_PLIST
    return (["sudo mkdir -p \"%s\"" % PF_RULES_DIR,
             # 755, explicitly: sudo unions the caller's umask with its own, so a login
             # umask of 077 would otherwise leave this directory unreadable — pf would
             # still load the rule as root, while `verify` could not read the file and
             # would call it missing (review of PR #147, finding 15).
             "sudo chmod 755 \"%s\"" % PF_RULES_DIR,
             "sudo tee %s > /dev/null <<'RULES'" % rules]
            + pf_rules(port).splitlines()
            + ["RULES",
               "sudo /sbin/pfctl -n -a %s -f %s" % (PF_ANCHOR, rules),
               "sudo tee %s > /dev/null <<'PLIST'" % plist]
            + pf_plist().splitlines()
            + ["PLIST",
               "sudo chown root:wheel %s %s" % (rules, plist),
               "sudo chmod 644 %s %s" % (rules, plist),
               "plutil -lint %s" % plist,
               PF_RELOAD_IF_LOADED,
               "sudo launchctl bootstrap system %s" % plist,
               "sudo /sbin/pfctl -a %s -s rules" % PF_ANCHOR,
               "sudo /sbin/pfctl -s info | grep Status",
               "curl -s -m 5 http://127.0.0.1:%s/status" % port])


# The first thing to prove after the restart: tracker dispatch still works.
TICKET_CHECK_STEP = (
    "Delegate one throwaway tracker ticket to the dispatcher. Good: a session starts on it "
    "within five minutes. Not that: no session. Roll back now: remove CYRUS_HOST_EXTERNAL "
    "from the dispatcher's env file and restart it, then read the dispatcher's log for "
    "\"Rejected Linear webhook from unauthorized IP\" (LinearEventTransport.js:93).")

PF_COPY_START = "----- copy from the next line -----"
PF_COPY_END = "----- to the line above -----"


def pf_missing_families(rules_text, port):
    """The address families with no loaded refusal of `port`, from `pfctl -s rules` output
    or from the rules file. pfctl prints a port match as `port = N`."""
    missing = []
    for family in PF_FAMILIES:
        pattern = (r"^\s*block\b[^\n]*\bin\b[^\n]*\bquick\b[^\n]*\bon\s+!\s*lo0\s+%s\s+"
                   r"proto\s+tcp\b[^\n]*\bport\s*=?\s*%s\b" % (family, re.escape(str(port))))
        if not re.search(pattern, rules_text or "", re.M):
            missing.append(family)
    return missing

# --------------------------------------------------------------------------- #
# Piece 5 — the role account's user-level settings: secret-file read denies, nothing else.
#
# REPO_DENY_PATTERNS is this repository's .claude/settings.json `permissions.deny`, read
# 2026-09-17 and PINNED: the selftest fails the moment the two differ.
#
# They are RELATIVE rules, and a relative Read rule matches under the session's current
# directory only (Claude Code permissions page: "`path` or `./path` — Path relative to
# current directory"). A chat session's current directory is a fresh folder under the
# dispatcher's home (edge-worker ChatSessionHandler.js:427-432). Copied as written, they
# would guard that empty folder. So each is re-anchored at the filesystem root with
# `//**/`, which the same page gives as "any `.env` anywhere on the filesystem". The two
# conf paths are added because the repository's patterns do not cover them: the
# dispatcher's config holds tracker tokens (EdgeWorker.js:5154-5211) and its env file
# holds the chat bot's token.
# --------------------------------------------------------------------------- #
REPO_DENY_PATTERNS = (
    "Read(.env)", "Read(.env.local)", "Read(.env.development)", "Read(.env.production)",
    "Read(.env.staging)", "Read(.env.test)", "Read(.env.*.local)", "Read(secrets/**)",
    "Read(*.pem)", "Read(*.key)", "Read(**/id_rsa)", "Read(**/credentials)",
)
REPO_SETTINGS = os.path.join(ROOT, ".claude", "settings.json")


def anchored(pattern):
    """`Read(X)` -> `Read(//**/X)`: the same file name, anywhere on the filesystem."""
    m = re.match(r"^Read\((.*)\)$", pattern)
    if not m:
        raise ValueError("not a Read rule: %r" % pattern)
    inner = m.group(1)
    while inner.startswith("**/"):
        inner = inner[3:]
    return "Read(//**/%s)" % inner.lstrip("/")


# The role account's own files the chat lane must not read either (KIT-197). Its env file
# holds the Stage E key, the notifier's token and more, and the backups folder is where the
# writers below copy the dispatcher's env file, its config and the user settings before
# changing them — so it holds the chat app's secrets and the tracker's tokens too. A `~/`
# rule is the permissions page's "Path from home directory", and a chat session runs as the
# role account, so `~/` is that account's home wherever the session stands.
DEFAULT_ROLE_ENV_FILE = "~/.stage-e/env"
ROLE_BACKUPS = "~/.stage-e/backups"
BACKUPS_DENY_RULE = "Read(%s/**)" % ROLE_BACKUPS


def _path_rule(path):
    """`Read(//abs/path)` for an absolute path, `Read(~/rel)` for one under the home."""
    if path.startswith("~/"):
        return "Read(%s)" % path
    return "Read(/%s)" % path


def user_deny_patterns(conf):
    """The composed deny list: the repository's patterns anchored, then this deployment's
    two credential files by absolute path, then the role account's own env file and the
    backups folder under its home."""
    out = [anchored(p) for p in REPO_DENY_PATTERNS]
    for key in ("DISPATCHER_CONFIG", "DISPATCHER_ENV_FILE"):
        path = (conf or {}).get(key) or ""
        if path.startswith("/"):
            rule = _path_rule(path)
            if rule not in out:
                out.append(rule)
    role_env = (conf or {}).get("ROLE_ENV_FILE") or DEFAULT_ROLE_ENV_FILE
    for rule in ([_path_rule(role_env)] if role_env.startswith(("/", "~/")) else []) + [
            BACKUPS_DENY_RULE]:
        if rule not in out:
            out.append(rule)
    return out


def repo_deny_drift(settings):
    """Problems when a settings object's deny list no longer matches REPO_DENY_PATTERNS."""
    perms = settings.get("permissions") if isinstance(settings, dict) else None
    deny = perms.get("deny") if isinstance(perms, dict) else None
    if not isinstance(deny, list):
        return ["the settings file has no permissions.deny list"]
    problems = []
    for p in deny:
        if p not in REPO_DENY_PATTERNS:
            problems.append("the repository denies %s, which this file does not compose" % p)
    for p in REPO_DENY_PATTERNS:
        if p not in deny:
            problems.append("this file composes %s, which the repository no longer denies" % p)
    return problems


# --------------------------------------------------------------------------- #
# Piece 6 — the chat app's Slack manifest.  EXACTLY the scopes and events the dispatcher's
# own Slack code uses, each cited to its call. `auth.test` needs no scope
# (slack-event-transport SlackMessageService.js:47-55). Deliberately absent: public-channel
# and direct-message history and events (the lane is one private channel), token rotation
# (the token is read from the environment and never refreshed: SlackEventTransport.js:53-55,
# SlackChatAdapter.js:63-65), Socket Mode (the transport is inbound HTTP only:
# SlackEventTransport.js:84-89), and anything the Slack tool server might want.
# --------------------------------------------------------------------------- #
SCOPE_CITES = {
    "app_mentions:read": (
        "receive app_mention, the event that starts a session "
        "(SlackEventTransport.js:211; SlackChatAdapter.js:97-99)"),
    "chat:write": (
        "chat.postMessage: the reply and the still-working notice "
        "(SlackMessageService.js:17-31; SlackChatAdapter.js:296-301, 351-356)"),
    "groups:history": (
        "conversations.replies in a private channel, for thread context "
        "(SlackMessageService.js:71-93; SlackChatAdapter.js:215-227); and the "
        "message.groups event, for follow-ups in a thread (SlackEventTransport.js:221-235)"),
    "reactions:write": (
        "reactions.add and reactions.remove: the receipt and done reactions "
        "(SlackReactionService.js:18-30; SlackChatAdapter.js:314-319, 342-343)"),
}
EVENT_CITES = {
    "app_mention": (
        "an @mention starts or resumes the thread's session "
        "(SlackEventTransport.js:211; SlackChatAdapter.js:97-99)"),
    "message.groups": (
        "a plain reply in a private-channel thread the bot is already bound to continues "
        "it (SlackEventTransport.js:221-235; SlackChatAdapter.js:88-99; thread following "
        "is on unless turned off: EdgeWorker.js:664-673)"),
}
CHAT_APP_NAME = "Pipeline chat"
CHAT_BOT_NAME = "pipeline-chat"


def slack_manifest(conf):
    host = (conf or {}).get("FRONT_DOOR_HOST") or "${FRONT_DOOR_HOST}"
    return {
        "display_information": {
            "name": CHAT_APP_NAME,
            "description": "The dispatcher's chat lane. Fully trusted members only.",
        },
        "features": {"bot_user": {"display_name": CHAT_BOT_NAME, "always_online": False}},
        "oauth_config": {"scopes": {"bot": sorted(SCOPE_CITES)}},
        "settings": {
            "event_subscriptions": {
                "request_url": "https://%s/slack-webhook" % host,
                "bot_events": sorted(EVENT_CITES),
            },
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


# --------------------------------------------------------------------------- #
# Piece 7 — the front door's path allowlist (KIT-197).
#
# The front door is a Caddy-style reverse proxy whose allowlist is ONE named-matcher line,
# `<matcher> path /a /b …` in FRONT_DOOR_CONFIG. The chat lane appends one path to it. A
# path on that line that is not one of the dispatcher's own routes is a wider door than
# the kit can account for, so it is named — never removed: it may be there on purpose.
#
# The dispatcher's own routes behind the door, each where 0.2.69 registers it:
# --------------------------------------------------------------------------- #
SLACK_PATH = "/slack-webhook"
TRACKER_PATH = "/linear-webhook"
# The config-update route: only the dispatcher answers it 401, so it is the probe that
# proves the door forwards no more than its paths (EdgeWorker.js:516-521).
CONFIG_UPDATE_PATH = "/api/update/cyrus-config"
EXPECTED_ROUTES = {
    TRACKER_PATH: "the tracker's webhooks (linear-event-transport LinearEventTransport.js:68)",
    "/callback": ("the tracker sign-in's redirect, served by the self-auth command's own "
                  "listener (cli SelfAuthCommand.js:113)"),
    "/status": "idle or busy, for a safe restart (edge-worker EdgeWorker.js:535)",
    SLACK_PATH: "the chat lane's Slack events (slack-event-transport SlackEventTransport.js:85)",
}

# The two dispatcher routes that must never be reachable through the door (review of
# KIT-197, finding 46): the config-update routes (EdgeWorker.js:516-521) and the
# dispatcher's own tool server (EdgeWorker.js:97), whose auth check passes everything while
# CYRUS_API_KEY is unset (McpConfigService.js:166-170). A path with `*` in it is a pattern:
# it forwards paths nobody listed, and may cover either. Compared without case, on the
# safe side of however the door matches.
TOOL_SERVER_PATH = "/mcp/cyrus-tools"


def widening_reason(path):
    """Why a path on the allowlist line forwards one of the dispatcher's control routes,
    or None. A literal path that is not a dispatcher route is not this: it is named, not
    judged, because it may be there on purpose."""
    low = path.lower()
    if "*" in path:
        return ("a wildcard: it forwards paths nobody listed, and can cover the dispatcher's "
                "config-update route (%s) or its tool server (%s)"
                % (CONFIG_UPDATE_PATH, TOOL_SERVER_PATH))
    if low.startswith("/api/update/"):
        return "the dispatcher's config-update route (EdgeWorker.js:516-521)"
    if low.startswith("/mcp/"):
        return ("the dispatcher's tool server, whose auth check passes everything while "
                "CYRUS_API_KEY is unset (McpConfigService.js:166-170)")
    return None


# One named-matcher line: the matcher, `path`, then one or more paths, then an optional
# trailing comment (`#` starting a word) and trailing space. No line ending: callers split
# on "\n" and hand one line's text in.
_PATH_WORD = r"[^\s#]\S*"


def front_line_parts(line, matcher):
    """(head, paths, tail) whose concatenation is `line`, or None when `line` is not this
    matcher's single-line `path` form. `paths.split()` is the path list."""
    m = re.match(r"^(\s*%s\s+path\s+)(%s(?:\s+%s)*)((?:\s+#.*)?\s*)$"
                 % (re.escape(matcher), _PATH_WORD, _PATH_WORD), line)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def front_line_edit(line, matcher, path, remove=False):
    """The line with `path` appended to its paths (or, with `remove`, taken out of them).
    The line itself when there is nothing to change. None when `line` is not the
    matcher's line, or when removing would leave it with no path — an empty allowlist line
    is a broken door, not a closed one."""
    parts = front_line_parts(line, matcher)
    if parts is None:
        return None
    head, paths, tail = parts
    words = paths.split()
    if remove:
        if path not in words:
            return line
        kept = [w for w in words if w != path]
        return head + " ".join(kept) + tail if kept else None
    if path in words:
        return line
    return head + paths + " " + path + tail


def front_door_digest(fd, matcher):
    """The probe's front-door answer, reduced by `front_line_parts`: how many lines are the
    matcher's `path` line, and — when exactly one — which, its text and its paths."""
    if fd is None or fd.get("error"):
        return fd
    hits = []
    for cand in fd.get("candidates") or []:
        parts = front_line_parts(cand.get("text") or "", matcher)
        if parts is not None:
            hits.append((cand.get("index"), cand.get("text"), parts[1].split()))
    out = {"path": fd.get("path"), "sha256": fd.get("sha256"), "count": len(hits),
           "lines": [i + 1 for i, _t, _p in hits]}
    if len(hits) == 1:
        index, text, paths = hits[0]
        out.update({"index": index, "line": index + 1, "text": text, "paths": paths})
    return out


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def say(msg=""):
    print(msg.rstrip("\n"))


def para(text, indent="  ", width=74):
    """Wrap a paragraph. A list item ("- " or "3. ") hangs its later lines under its text."""
    marker = re.match(r"^(- |\d+\. )", text)
    hang = indent + " " * (len(marker.group(1)) if marker else 0)
    for n, line in enumerate(_wrap(text, width - len(hang))):
        say((indent if n == 0 else hang) + line)


def agent_env_markers_present(env=None):
    """Presence, not truthiness: `CLAUDECODE=` is a name that is SET."""
    env = os.environ if env is None else env
    return [m for m in AGENT_ENV_MARKERS if m in env]


def _self_path():
    try:
        return os.path.relpath(os.path.abspath(__file__), os.getcwd())
    except ValueError:
        return os.path.abspath(__file__)


class ConfError(Exception):
    """Exit 2. Nothing was attempted."""


# --------------------------------------------------------------------------- #
# The conf.  Every error in one pass.
# --------------------------------------------------------------------------- #
CONF_REQUIRED = ("ROLE_ACCOUNT", "DISPATCHER_CONFIG", "DISPATCHER_ENV_FILE", "FRONT_DOOR_HOST")
CONF_DEFAULTS = {"NOTIFIER_TOKEN_ENV": "NOTIFIER_SLACK_BOT_TOKEN",
                 "DISPATCHER_PORT": DEFAULT_DISPATCHER_PORT,
                 "ROLE_ENV_FILE": DEFAULT_ROLE_ENV_FILE}
# Optional, and empty when unset (KIT-197). A printed command that needs one says
# "not composed: set <KEY> in chat-lane.conf" instead of printing a placeholder; a
# subcommand that needs one refuses, naming it.
CONF_OPTIONAL = ("DISPATCHER_SERVICE", "FRONT_DOOR_SERVICE", "FRONT_DOOR_CONFIG",
                 "FRONT_DOOR_BIN", "FRONT_DOOR_MATCHER")
CONF_KEYS = set(CONF_REQUIRED) | set(CONF_DEFAULTS) | set(CONF_OPTIONAL)
# What `front-door` needs, and what verify's front-door row needs.
FRONT_DOOR_KEYS = ("FRONT_DOOR_CONFIG", "FRONT_DOOR_BIN", "FRONT_DOOR_MATCHER")
FRONT_DOOR_READ_KEYS = ("FRONT_DOOR_CONFIG", "FRONT_DOOR_MATCHER")
_HOST_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_MATCHER_RE = re.compile(r"^@[A-Za-z0-9_-]+$")
_EXTRA_CRED_PREFIXES = ("xoxp-", "xoxa-", "xoxe", "xapp-")


def parse_conf(text, source="chat-lane.conf"):
    """(values, errors). Every bad line, never only the first."""
    values, seen, errors = {}, {}, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            # NEVER echo this line. A pasted bot token or signing secret has no `=`, so
            # it lands here, and the credential-shape guard below never sees it. The line
            # number is enough to find it (review of PR #147, finding 10).
            errors.append("%s:%d: not KEY=value (%d characters; the line is not shown, in "
                          "case it is a pasted credential)" % (source, n, len(line)))
            continue
        key, val = [part.strip() for part in line.split("=", 1)]
        if not re.match(r"^[A-Z][A-Z0-9_]*$", key):
            errors.append("%s:%d: %r is not a KEY (upper-case, digits, underscore)"
                          % (source, n, key))
            continue
        if key in seen:
            errors.append("%s:%d: duplicate key %s, first set at line %d" % (source, n, key,
                                                                          seen[key]))
            continue
        if key not in CONF_KEYS:
            errors.append("%s:%d: unknown key %s (see chat-lane.conf.example)"
                          % (source, n, key))
            continue
        if (val.startswith(_CRED_PREFIXES + _EXTRA_CRED_PREFIXES)
                or (not val.startswith("/") and _BLOB_RE.match(val))):
            errors.append("%s:%d: %s carries a CREDENTIAL SHAPE. No key in this file ever "
                          "holds one. (The value is not shown.)" % (source, n, key))
            continue
        seen[key] = n
        values[key] = val
    return values, errors


def validate_conf(values):
    errors = []
    conf = dict(CONF_DEFAULTS)
    conf.update(values)
    for key in CONF_REQUIRED:
        if not conf.get(key):
            errors.append("%s is required and is missing or empty" % key)
    if conf.get("ROLE_ACCOUNT") and not _ACCOUNT_RE.match(conf["ROLE_ACCOUNT"]):
        errors.append("ROLE_ACCOUNT %r is not a local account name" % conf["ROLE_ACCOUNT"])
    for key in ("DISPATCHER_CONFIG", "DISPATCHER_ENV_FILE"):
        if conf.get(key) and not conf[key].startswith("/"):
            errors.append("%s must be an absolute path (got %r)" % (key, conf[key]))
    if (conf.get("DISPATCHER_CONFIG") and
            conf.get("DISPATCHER_CONFIG") == conf.get("DISPATCHER_ENV_FILE")):
        errors.append("DISPATCHER_CONFIG and DISPATCHER_ENV_FILE name the same file")
    host = conf.get("FRONT_DOOR_HOST") or ""
    if host and ("://" in host or "/" in host):
        errors.append("FRONT_DOOR_HOST is a host name only, no scheme and no path (got %r)"
                      % host)
    elif host and not _HOST_RE.match(host):
        errors.append("FRONT_DOOR_HOST %r is not a host name" % host)
    port = conf.get("DISPATCHER_PORT") or ""
    if not (port.isdigit() and 1 <= int(port) <= 65535 and not port.startswith("0")):
        errors.append("DISPATCHER_PORT must be a port number from 1 to 65535, the one the "
                      "dispatcher listens on: its CYRUS_SERVER_PORT, or 3456 when that is "
                      "unset (got %r)" % port)
    name = conf.get("NOTIFIER_TOKEN_ENV") or ""
    if not _ENV_NAME_RE.match(name):
        errors.append("NOTIFIER_TOKEN_ENV must be the NAME of an environment variable "
                      "(got %r)" % name)
    elif name in ENV_NAMES:
        errors.append("NOTIFIER_TOKEN_ENV is %s, a name the dispatcher itself reads. Two "
                      "apps means two names: the notifier's token never enters the "
                      "dispatcher's env file" % name)
    for key in CONF_OPTIONAL:
        conf.setdefault(key, "")
    for key in ("DISPATCHER_SERVICE", "FRONT_DOOR_SERVICE"):
        if conf[key] and not _RDNS_RE.match(conf[key]):
            errors.append("%s %r is not a reverse-DNS launchd label: the name `launchctl "
                          "print system/<label>` finds, such as com.example.dispatcher"
                          % (key, conf[key]))
    if conf["DISPATCHER_SERVICE"] and conf["DISPATCHER_SERVICE"] == conf["FRONT_DOOR_SERVICE"]:
        errors.append("DISPATCHER_SERVICE and FRONT_DOOR_SERVICE name the same service")
    for key in ("FRONT_DOOR_CONFIG", "FRONT_DOOR_BIN"):
        if conf[key] and not conf[key].startswith("/"):
            errors.append("%s must be an absolute path (got %r)" % (key, conf[key]))
    if conf["FRONT_DOOR_MATCHER"] and not _MATCHER_RE.match(conf["FRONT_DOOR_MATCHER"]):
        errors.append("FRONT_DOOR_MATCHER %r is not a named matcher: @ and then letters, "
                      "digits, _ or -" % conf["FRONT_DOOR_MATCHER"])
    role_env = conf.get("ROLE_ENV_FILE") or ""
    if not role_env.startswith(("/", "~/")) or role_env in ("/", "~/"):
        errors.append("ROLE_ENV_FILE must be an absolute path, or start with ~/ for the role "
                      "account's own home (got %r)" % role_env)
    return conf, errors


def load_conf(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return None, ["could not read %s: %s — copy chat-lane.conf.example to "
                      "chat-lane.conf and fill it in" % (path, exc.strerror or exc)]
    values, errors = parse_conf(text, os.path.basename(path))
    conf, more = validate_conf(values)
    return conf, errors + more


# --------------------------------------------------------------------------- #
# compose
# --------------------------------------------------------------------------- #
def cmd_compose(conf, conf_path="chat-lane.conf", piece=None):
    """Every piece and THE ORDER; or one piece alone. Piece 4 alone is only its script, so
    `compose --piece 4 > pf-block.sh` is the file a person reads and then runs."""
    problems = grant_problems(owner_grant())
    if problems:
        # The approved list is a constant; a broken one is a defect in this file.
        say("BUG: the composed grant breaks the owner's decision: " + "; ".join(problems))
        return EX_FAILED
    if piece == 4:
        for line in pf_install_commands(conf["DISPATCHER_PORT"]):
            say(line)
        return EX_OK
    if piece:
        COMPOSE_PIECES[piece](conf)
        return EX_OK
    rule = "=" * 76
    say(rule)
    say(" Chat lane — every piece, composed for you to apply")
    say(rule)
    say(" conf: %s    dispatcher source read: %s" % (conf_path, SOURCE_VERSION))
    para("This command read no live file and changed nothing. Three subcommands write pieces "
         "1, 2, 3, 5 and 7 for you, as the role account, when you run them: merge, env-names "
         "and front-door. The rest is yours by hand. Apply everything in the order at the "
         "end, then run:  python3 %s verify" % _self_path(), " ")
    say("")
    _compose_warning()
    for n in sorted(COMPOSE_PIECES):
        COMPOSE_PIECES[n](conf)
    _compose_order(conf)
    return EX_OK


def _compose_warning():
    say("READ THIS FIRST — EVERY MEMBER OF THE SLACK WORKSPACE MUST BE FULLY TRUSTED")
    para("The chat lane has no user list and no channel list. Any member of the Slack "
         "workspace who can mention the bot in a channel it is in starts a session, as "
         "the dispatcher's account, with no sandbox. The access check runs only on tracker "
         "webhooks (EdgeWorker.js:3083-3087, 3620-3624), so the tracker's allowedUsers "
         "list does not apply here either. The gate is workspace membership.")
    para("So every member can do everything you can: read every file and token the "
         "dispatcher's account can read (your tracker key and code-host token among them), "
         "steer any running session, write to the tracker, and run read-only shell "
         "commands. Admit only people you would trust with that account itself. Full "
         "members only: no guests and no shared-channel users. Require two-factor sign-in "
         "for every member, because each member's Slack account is now part of this "
         "boundary. Turn this lane off before anyone who does not meet that joins. The "
         "private channel keeps the notifier's pings confidential; it does not gate this "
         "lane.")
    say("")


# -- Piece 1 -----------------------------------------------------------------
def _compose_piece_1(conf):
    say("PIECE 1 — THE TRIMMED CHAT GRANT")
    para("Where: the top level of %s. MERGE this one key; never replace the file, because "
         "the dispatcher rewrites it itself to store refreshed tracker tokens "
         "(EdgeWorker.js:5154-5211)." % conf["DISPATCHER_CONFIG"])
    say("")
    say('    "slackAllowedTools": ' + json.dumps(owner_grant()))
    say("")
    para("THE LAST TWO RULES ARE A TEMPLATE, NOT A VALUE. %s stands for one repository the "
         "dispatcher serves, written exactly as its entry's repositoryPath. Repeat BOTH "
         "rules for EVERY repository, with that repository's own path:"
         % PLACEHOLDER_REPO_PATH)
    for form in PULL_RULE_FORMS:
        say("      " + json.dumps(form % PLACEHOLDER_REPO_PATH))
    para("The path is literal on purpose. `Bash(git -C * pull)`, which this list used to "
         "carry, also matches `git -C <dir> -c core.fsmonitor='<any command>' status pull`: "
         "a Bash rule matches the whole command text, the words before the first * are all "
         "that limit it, and git's -c option runs a program the caller names. On this lane "
         "the list is the only fence, so that was arbitrary command execution. A fixed path "
         "leaves nothing that can stand before `pull`.")
    para("A pull rule may carry no git option other than --ff-only. -c, --config-env, "
         "--exec-path, --upload-pack and --receive-pack each name a program git then runs, "
         "so a rule holding one is a shell even with the path written out. `verify` "
         "refuses any it finds in the live list.")
    para("THE COST, SAID PLAINLY: add a repository to the dispatcher and its pull stops "
         "working in chat until you add both rules for it. Nothing announces that; the tool "
         "call is simply refused. `verify` reads the live repository paths and names any "
         "repository with no pull rule.")
    say("")
    say("  THE DISPATCHER STILL ADDS mcp__cyrus-tools AND mcp__cyrus-docs, WHATEVER THIS")
    say("  LIST SAYS. It appends every tool server it built for the session after the list")
    say("  (ToolPermissionResolver.js:72-77), and the chat lane's disallowedTools is")
    say("  hard-coded empty (RunnerConfigBuilder.js:78). Nothing here can take them out.")
    for name, what in APPENDED_SERVERS:
        say("    %s" % name)
        para(what, "      ")
    para("One more path adds tools: any tool starting mcp__ in the FIRST ACTIVE repository "
         "entry's allowedTools joins this grant (RunnerConfigBuilder.js:63-66; "
         "ChatRepositoryProvider.js:18-20). First ACTIVE, not first in the file: the "
         "dispatcher skips entries with isActive false (EdgeWorker.js:274-290), so "
         "retiring one promotes the next. Keep every entry's allowedTools free of mcp__ "
         "names; `verify` checks the live order.")
    para("merge writes this key for you, with the pull rules filled in from the live "
         "repository paths — the list verify's paste line carries:  python3 %s merge  shows "
         "it and changes nothing;  merge --apply  writes it. It never edits an entry's "
         "allowedTools: an mcp__ name there is a warning for you to remove by hand."
         % _self_path())
    say("")


# -- Piece 2 -----------------------------------------------------------------
def _compose_piece_2(conf):
    say("PIECE 2 — FENCE THE SLACK SERVER OFF EVERY OTHER ENTRY")
    para("Why: once SLACK_BOT_TOKEN is in the dispatcher's environment, every session gets "
         "a working Slack server (McpConfigService.js:87-99), and the ticket lane approves "
         "every tool it does not deny (ClaudeRunner.js:219-228). disallowedTools is the "
         "only fence there.")
    say("  The two rules:  " + json.dumps(list(SLACK_FENCE_RULES)))
    para("Which entries: every entry in \"repositories\" except a review entry written by "
         "the Stage E installer, whose fence already names both rules. That INCLUDES a "
         "planning entry: its linearMcpAttached key is read by nothing in %s, and every "
         "issue session gets every server (RunnerConfigBuilder.js:177)." % SOURCE_VERSION)
    say("  How, per entry — to its EFFECTIVE list (ToolPermissionResolver.js:217-226):")
    para("- It has its own \"disallowedTools\", even an empty one: append both rules to it.",
         "    ")
    para("- It has none, so it inherits \"defaultDisallowedTools\": give it its own list, a "
         "copy of defaultDisallowedTools plus both rules. NEVER a list of only the two "
         "rules — that silently drops every default it used to inherit.", "    ")
    para("WARNING — PROMPT TYPES. A \"disallowedTools\" under an entry's "
         "\"labelPrompts\".<type>, or under \"promptDefaults\".<type>, REPLACES the entry's "
         "list for sessions of that type (ToolPermissionResolver.js:199-216). The fence "
         "does not apply to that type unless both rules are in that list too. `verify` "
         "names every such type.")
    para("Also: a session routed to several entries keeps only rules EVERY entry denies "
         "(ToolPermissionResolver.js:181-189) — one unfenced entry opens it. And a "
         "DISALLOWED_TOOLS environment variable replaces defaultDisallowedTools at start "
         "(cli WorkerService.js:164-165).")
    para("compose reads no live config, so this is the rule. `verify` computes the exact "
         "list for each entry from the live file, and `merge` applies exactly that list, "
         "with piece 1 and piece 5, in one pass. It leaves a review entry alone and names "
         "it if its fence is missing: the Stage E installer owns those entries. With "
         "DISALLOWED_TOOLS set, an entry that inherits runs under a list no file holds, so "
         "`merge` composes nothing for it and says CANNOT: give it its own list by hand.")
    say("")


# -- Piece 3 -----------------------------------------------------------------
def _compose_piece_3(conf):
    say("PIECE 3 — THE DISPATCHER'S ENV FILE: FOUR NAMES")
    para("Where: %s. The dispatcher loads it at start and re-applies it when it changes "
         "(Application.js:52-78). Nobody types a secret into this file by hand:  python3 %s "
         "env-names  asks for the two secret values at hidden prompts, hands them to the "
         "role account's shell on standard input — never in an argument, never on this "
         "screen — and writes all four names together. It refuses until verify's "
         "coding-fence row (piece 2) and port-block row (piece 4, on the port the "
         "dispatcher listens on) both measure as applied. It refuses a token found "
         "anywhere in ROLE_ENV_FILE, where the notifier keeps its own, and will not guess "
         "when that file cannot be read. It backs the file up first; that backup holds "
         "the secrets, so delete it once verify is clean. It never restarts the "
         "dispatcher: card CK-C5 does, only when it is idle."
         % (conf["DISPATCHER_ENV_FILE"], _self_path()))
    say("")
    say("    SLACK_BOT_TOKEN        the CHAT app's bot token (Slack: OAuth & Permissions)")
    say("    SLACK_SIGNING_SECRET   the CHAT app's signing secret (Slack: Basic Information)")
    say("    CYRUS_HOST_EXTERNAL=true")
    say("    %s=false" % IP_VALIDATION_NAME)
    say("")
    say("  %s=false IS NOT OPTIONAL. WITHOUT IT, TICKETS STOP STARTING SESSIONS." % IP_VALIDATION_NAME)
    para("CYRUS_HOST_EXTERNAL=true turns on webhook source-address checks unless this name "
         "is exactly false (EdgeWorker.js:241-252). A dispatcher that verifies tracker "
         "signatures (LINEAR_DIRECT_WEBHOOKS=true) then takes tracker webhooks only from the "
         "tracker's nine published addresses (EdgeWorker.js:475-489; WebhookIpValidator.js:"
         "9-19), and answers every other address with 403, loopback included "
         "(LinearEventTransport.js:89-96). It reads the address from X-Forwarded-For "
         "(SharedApplicationServer.js:37-40). A front door that does not trust its local "
         "tunnel client puts 127.0.0.1 there, so every tracker webhook is refused, no ticket "
         "starts a session, and nothing else looks wrong.")
    para("What false keeps: today's behaviour exactly. The tracker webhook stays protected by "
         "its signature alone, as it is now. The Slack webhook gets no address list either "
         "way (EdgeWorker.js:743-750).")
    say("")
    say("  SLACK_BOT_TOKEN MUST BE THE CHAT APP'S TOKEN, NEVER THE NOTIFIER'S.")
    para("The dispatcher's environment reaches every session it starts: a session's "
         "environment is a copy of the dispatcher's (claude-runner session-env.js:45-65), so "
         "any coding session can read this token. The notifier's token stays in its own env "
         "file under its own name, %s, so a leaked chat token cannot send a notifier "
         "ping. Never add %s to this file." % (conf["NOTIFIER_TOKEN_ENV"],
                                               conf["NOTIFIER_TOKEN_ENV"]))
    say("")
    say("  DO NOT SET %s OR %s IN THIS FILE." % (", ".join(HOSTED_DO_NOT_SET[:-1]),
                                               HOSTED_DO_NOT_SET[-1]))
    para("Any CYRUS_API_KEY at all makes the dispatcher act as paired with the vendor's "
         "hosted service. It then gives sessions a log_failure_mode tool "
         "(EdgeWorker.js:4016-4027, 4142-4147; cyrus-tools/index.js:636-642), and every "
         "session's prompt tells it to use it (RunnerConfigBuilder.js:87, 207; "
         "failureModePromptAddendum.js:21-27). That tool sends the session id, a recap, a "
         "quote from the conversation, the failure text, the ticket id and the workspace "
         "path to https://app.atcyrus.com/api/failure-modes (failure-modes-http-client.js:"
         "4-35; ConfigApiClient.js:5-12), from the dispatcher's own unsandboxed process. "
         "There is no opt-out except leaving the key unset. With CYRUS_TEAM_ID as well, "
         "whole session transcripts go there too (EdgeWorker.js:152-173). CYRUS_APP_URL "
         "only changes where they go. `verify` checks the first two are absent, by name.")
    para("What leaving the key unset costs, accepted: any process already running on this "
         "machine can call the dispatcher's tool server, whose auth check passes everything "
         "while no key is set. It needs a live session's id to do anything "
         "(McpConfigService.js:166-181). Piece 4 keeps the rest of the network out.")
    say("")
    say("  WHAT CYRUS_HOST_EXTERNAL=true CHANGES — EVERY EFFECT, NOT ONLY THE HMAC CHECK")
    effects = (
        "1. Slack requests are HMAC-checked with SLACK_SIGNING_SECRET (EdgeWorker.js:730-752). "
        "This is re-read per request, so it switches on without a restart "
        "(SlackEventTransport.js:65-80).",
        "2. The dispatcher's web server listens on EVERY network interface instead of this "
        "machine only (WorkerService.js:149, 191; EdgeWorker.js:254-257). Every route is "
        "then reachable from the local network without the front door: the config-update "
        "routes, the tool server at /mcp/cyrus-tools, whose auth check passes everything "
        "while CYRUS_API_KEY is unset (McpConfigService.js:166-170), /status and every "
        "webhook. Read at start only. CLOSED BY piece 4 once it is loaded and card CK-C4 "
        "passes; open until then.",
        "3. Webhook source-address checks turn on (EdgeWorker.js:241-252), and the "
        "dispatcher fetches GitHub's address list from api.github.com at start "
        "(EdgeWorker.js:411-416). The tracker webhook is checked only with "
        "LINEAR_DIRECT_WEBHOOKS=true (EdgeWorker.js:475-489); the Slack webhook gets no "
        "address list at all (EdgeWorker.js:743-750). Read at start only. KEPT OFF by "
        "WEBHOOK_IP_VALIDATION=false, above.",
        "4. GitHub and GitLab webhooks switch to signature checks if their secrets are set "
        "(EdgeWorker.js:561-574, 625-631). No secret, no change.",
        "5. A tracker sign-in uses a local authorize page instead of the hosted proxy when "
        "LINEAR_CLIENT_ID is set (SharedApplicationServer.js:209-218); the self-auth "
        "callback listener binds every interface (SelfAuthCommand.js:153-156).",
        "Unchanged: the Cloudflare tunnel (SharedApplicationServer.js:81-84), the loopback "
        "address sessions use for the tool server (EdgeWorker.js:4224-4230), and where "
        "every token is read.",
    )
    for effect in effects:
        para(effect, "    ")
    say("")


# -- Piece 4 -----------------------------------------------------------------
def _compose_piece_4(conf):
    """The whole piece, as `compose` prints it. `compose --piece 4` prints only the
    script, which `cmd_compose` handles before it would get here."""
    port = conf["DISPATCHER_PORT"]
    say("PIECE 4 — BLOCK THE DISPATCHER'S PORT FROM THE NETWORK (load it BEFORE piece 3)")
    para("Why: CYRUS_HOST_EXTERNAL=true makes the dispatcher listen on every network "
         "interface (piece 3, effect 2). This packet-filter rule refuses port %s on every "
         "interface except loopback, in both address families. The front door is "
         "unaffected: it connects locally. The port is DISPATCHER_PORT in your conf, and it "
         "must be the port the dispatcher reads from CYRUS_SERVER_PORT, 3456 when that is "
         "unset (WorkerService.js:190; config/constants.js:7). `verify` compares the two "
         "without reading the value." % port)
    para("Not the application firewall: it does not close a port that a signed app already "
         "holds (measured on a deployment; KIT-117, owner decision of 2026-09-17).")
    para("Where it lives: not in /etc/pf.conf, which a macOS update can reset. macOS's own "
         "/etc/pf.conf already evaluates every anchor under com.apple (line 26 of the file "
         "macOS 26.6.2 ships: anchor \"com.apple/*\"; pf.conf(5): an anchor ending in /* "
         "evaluates every anchor attached there). The rule loads into its own anchor, %s, "
         "from %s, by a root LaunchDaemon at boot that also enables pf (pfctl -E). With -a, "
         "pfctl -f loads only that anchor and leaves the main ruleset alone (pfctl(8)). "
         "Check your /etc/pf.conf still has that line first:" % (PF_ANCHOR, PF_RULES_PATH))
    say("    grep -n 'anchor \"com.apple/\\*\"' /etc/pf.conf")
    para("IPv6: the dispatcher hands 0.0.0.0 to Fastify's listen (SharedApplicationServer.js:"
         "75-78), which Fastify documents as all IPv4 addresses only. So IPv6 should not "
         "answer today. The rule refuses IPv6 too, in case the listen address ever changes.")
    say("")
    para("Run these in order in a terminal on this machine, and stop at the first error. "
         "They are printed flush left on purpose: a heredoc's closing word must start its "
         "line, and a plist may not start with a space. The same lines, and nothing else, "
         "come from  python3 %s compose --piece 4 > pf-block.sh ; read that file, then run "
         "it with  sh -e pf-block.sh , which stops at the first error. It is safe to run "
         "again: a boot job that is already loaded is stopped, and waited for, before it is "
         "loaded again." % _self_path())
    say("")
    say(PF_COPY_START)
    for line in pf_install_commands(port):
        say(line)
    say(PF_COPY_END)
    say("")
    para("Good: the rules command prints two block lines, one inet and one inet6, for port "
         "%s; the info command prints \"Status: Enabled\"; the curl on 127.0.0.1 answers "
         "with a JSON status." % port)
    para("Not that: no rules, or \"Status: Disabled\". Stop there, and do NOT set "
         "CYRUS_HOST_EXTERNAL in piece 3 until both are right. Read the boot job's log, %s, "
         "and its last exit (sudo launchctl print system/%s | grep 'last exit'). Its one "
         "command is /sbin/pfctl -E -a %s -f \"%s\"; run that by hand with sudo to see the "
         "error." % (PF_DAEMON_LOG, PF_DAEMON_LABEL, PF_ANCHOR, PF_RULES_PATH))
    para("Not that either: \"STILL LOADED after 30 s\", then \"Bootstrap failed: 5\". The "
         "old boot job had not let go yet. The rule already loaded stays in force: wait a "
         "minute and run the script again.")
    para("After piece 3's restart, card CK-C4 proves the rule from a second device on the "
         "same network. A test from this machine to its own network address proves "
         "nothing.")
    say("")


# -- Piece 5 -----------------------------------------------------------------
def _compose_piece_5(conf):
    say("PIECE 5 — THE ROLE ACCOUNT'S USER-LEVEL SETTINGS: SECRET-FILE READ DENIES ONLY")
    para("Where: ~/.claude/settings.json in %s's home. A chat session loads user, project "
         "and local settings (ClaudeRunner.js:499), and its folder is not a project "
         "(ChatSessionHandler.js:427-432), so this is the only settings file it gets. "
         "MERGE these rules into that file's permissions.deny list and keep every rule "
         "already there. Never overwrite the file: the account may already have one. "
         "No hooks." % conf["ROLE_ACCOUNT"])
    say("")
    block = json.dumps({"permissions": {"deny": user_deny_patterns(conf)}}, indent=2)
    for line in block.splitlines():
        say("    " + line)
    say("")
    para("Why the rules start with //**/ or ~/: the first twelve are this repository's own "
         "denies, which are relative, and a relative rule matches under the session's "
         "current directory only. A chat session's is a fresh, empty folder. Anchored, each "
         "matches that file name anywhere. The next two are this deployment's dispatcher "
         "config and env file. The last two are the role account's own: its env file "
         "(ROLE_ENV_FILE), and the backups folder where merge, env-names and front-door copy "
         "a file before changing it — those copies hold the same secrets. A rule starting "
         "~/ matches under the home of the account the session runs as, which is the role "
         "account.")
    para("merge writes this for you: it adds only the rules missing from the file, keeps "
         "every rule and every other key already there, backs an existing file up first, "
         "and leaves it at mode 600:  python3 %s merge , then  merge --apply ." % _self_path())
    para("WHAT THESE DO NOT STOP. They block the Read tool. They do not stop the upload "
         "tool in mcp__cyrus-tools, which reads the file inside the dispatcher's own "
         "process, outside the session (cyrus-tools/index.js:107). And they apply to every "
         "session this account runs, coding and review too, because every Claude session "
         "loads user settings (ClaudeRunner.js:499).")
    say("")


# -- Piece 6 -----------------------------------------------------------------
def _compose_piece_6(conf):
    say("PIECE 6 — THE CHAT APP'S SLACK MANIFEST (a SECOND app; the notifier keeps its own)")
    para("Slack: Create New App -> From a manifest -> paste this. Card CK-C1 walks it.")
    say("")
    for line in json.dumps(slack_manifest(conf), indent=2).splitlines():
        say("    " + line)
    say("")
    say("  Each scope, and the dispatcher code that needs it:")
    for scope in sorted(SCOPE_CITES):
        say("    %s" % scope)
        para(SCOPE_CITES[scope], "      ")
    say("  Each event:")
    for event in sorted(EVENT_CITES):
        say("    %s" % event)
        para(EVENT_CITES[event], "      ")
    para("auth.test needs no scope (SlackMessageService.js:47-55). Deliberately absent: "
         "public-channel and direct-message history and events, token rotation (one fixed "
         "token is read from the environment and never refreshed: SlackEventTransport.js:"
         "53-55) and Socket Mode (inbound HTTP only: SlackEventTransport.js:84-89).")
    para("NOT ADDED: any scope the Slack tool server the dispatcher starts may want "
         "(McpConfigService.js:91-98). A tool lacking a scope fails; it does not widen "
         "the app.")
    say("")


# -- Piece 7 -----------------------------------------------------------------
def _compose_piece_7(conf):
    say("PIECE 7 — THE FRONT DOOR")
    para("The reverse proxy's path allowlist gains /slack-webhook, and nothing else. "
         "Slack's request URL is then https://%s/slack-webhook." % conf["FRONT_DOOR_HOST"])
    para("front-door writes it for you, as the role account:  python3 %s front-door  prints "
         "the ONE `<matcher> path …` line of FRONT_DOOR_CONFIG, names every path on it that "
         "is not one of the dispatcher's own routes, and changes nothing;  front-door "
         "--apply  appends the path, backs the file up, validates it with FRONT_DOOR_BIN, and "
         "puts the backup back if validation fails. It refuses on no such line, or on two, "
         "and will not add the path beside a wildcard or a path under /api/update/ or "
         "/mcp/: those reach the dispatcher's config-update route or its tool server. It "
         "never restarts the front door: card CK-C2 has the restart and the three probes."
         % _self_path())
    say("")


COMPOSE_PIECES = {1: _compose_piece_1, 2: _compose_piece_2, 3: _compose_piece_3,
                  4: _compose_piece_4, 5: _compose_piece_5, 6: _compose_piece_6,
                  7: _compose_piece_7}


# -- Order -------------------------------------------------------------------
def _compose_order(conf):
    me = "python3 %s" % _self_path()
    say("THE ORDER — the fence before the token, the port block before the listen")
    steps = (
        "1. Confirm every member of the Slack workspace is fully trusted, a full member, and signs in with two-factor.",
        "2. Pieces 2, 1 and 5, in one command, as the role account:  %s merge  prints what "
        "it would change and changes nothing; then  %s merge --apply  writes the "
        "dispatcher config in place and the user settings, and prints verify's rows for "
        "them. If the dispatcher rewrote its config in between, it refuses and writes "
        "nothing: run it again." % (me, me),
        "3. Piece 4: install and load the port block:  %s compose --piece 4 > pf-block.sh "
        "; read it; then  sh -e pf-block.sh . Go on only when pfctl shows both rules and "
        "\"Status: Enabled\" — the next restart makes the dispatcher listen on every "
        "interface. It is safe to run again." % me,
        "4. Card CK-C1: create the chat app from piece 6 and install it. Its two secrets "
        "stay in Slack until the next step asks for them.",
        "5. Piece 3, all four names together:  %s env-names  asks for the chat app's token "
        "and signing secret at hidden prompts and writes all four names. It refuses until "
        "the fence and the port block measure as applied, as verify's rows read them. "
        "Then restart the dispatcher with card CK-C5, "
        "which restarts it only when it answers idle. slackAllowedTools reloads live "
        "(ConfigManager.js:51-62, 181), but the listening address and address checks are "
        "read at start only, and a removed env name stays set until a restart "
        "(Application.js:54). Restart on purpose, now, not at the next reboot." % me,
        "6. %s" % TICKET_CHECK_STEP,
        "7. Card CK-C4, from a second device: the port refuses the network.",
        "8. Piece 7 and card CK-C2: the front door.  %s front-door , then  front-door "
        "--apply ; the card has the front door's restart and the three probes." % me,
        "9. In the Slack app, retry the request URL under Event Subscriptions. Make a "
        "private channel and invite the bot.",
        "10. %s verify" % me,
        "11. Card CK-C3: the live check, in the channel.",
        "To turn the lane off later: card CK-C6.",
    )
    for step in steps:
        para(step, "  ")


# --------------------------------------------------------------------------- #
# Cards — the Stage E installer's card format.
#
# A `do` item is a line of text, or a COMMAND GROUP made by `_group`: lines that print only
# when every conf key they need has a value. Otherwise the group prints
# `not composed: set <KEY> in chat-lane.conf`, one line per missing key, and never a
# placeholder command a person could paste by mistake (KIT-197).
# --------------------------------------------------------------------------- #
ME = "python3 scripts/pipeline_chat_lane_setup.py"


def _group(needs, lines):
    return {"needs": tuple(needs), "lines": list(lines)}


def card_lines(item, conf, broken=False):
    """The lines one `do` item prints under this conf (None: no conf was loaded). `broken`:
    a conf was read and has problems, so no command is composed from it, and "set <KEY>"
    would be false for a key that is set (review of KIT-197, finding 39)."""
    if isinstance(item, dict):
        if broken:
            return ["    not composed: fix chat-lane.conf first (its problems are listed "
                    "above)"]
        missing = [k for k in item["needs"] if not (conf or {}).get(k)]
        if missing:
            return ["    not composed: set %s in chat-lane.conf" % k for k in missing]
        return [_fill(line, conf) for line in item["lines"]]
    return [_fill(item, conf)]


def say_group(group, conf):
    for line in card_lines(group, conf):
        say("  " + line)


# How long a printed restart waits for launchd to let the old job go before it starts the
# new one: the Stage E installer's bound (an ExitTimeOut of 120 s plus 30 s of headroom,
# polled every two seconds). `bootout` returns when launchd has accepted the request, not
# when the job is gone, and a bootstrap into a domain that still holds it fails with
# `Bootstrap failed: 5` (the Stage E installer's restart notes).
_WAIT_GONE = ("n=0; while sudo launchctl print system/%s >/dev/null 2>&1; do n=$((n+1)); "
              "[ $n -ge 75 ] && { echo 'STILL LOADED after 150 s: run the bootstrap line "
              "again in a minute'; break; }; sleep 2; done")

# CK-C5's block. The dispatcher's own "can it be safely restarted" answer gates it: /status
# says busy while a webhook, a ticket session or a chat session runs (cyrus-edge-worker
# EdgeWorker.js:1784-1802, served at EdgeWorker.js:535), and a restart stops every running
# session. The log's path is read out of the plist's StandardOutPath, as the Stage E
# installer's `_dispatcher_log_path` does, so no key names it.
#
# NO ANSWER IS NOT "BUSY" (review of KIT-197, finding 31). Card CK-C2 sends a 502 here, and
# a dispatcher that is not listening can never answer idle, so the block used to say "paste
# again later" forever. Now: an answer that is not idle is busy; no answer, with launchd not
# holding the job, means nothing runs and it is started; no answer while launchd holds it
# prints its state and log, and the card's forced restart is the person's call.
_LOG_TAIL = ["L=$(/usr/libexec/PlistBuddy -c 'Print :StandardOutPath' "
             "/Library/LaunchDaemons/${DISPATCHER_SERVICE}.plist)",
             "sudo tail -n 40 \"$L\""]
_BOOTSTRAP = ["sudo launchctl bootstrap system /Library/LaunchDaemons/${DISPATCHER_SERVICE}.plist",
              "sleep 10"]
IDLE_RESTART_LINES = (
    ["S=$(curl -s -m 5 http://127.0.0.1:${DISPATCHER_PORT}/status)",
     "if printf '%s' \"$S\" | grep -q '\"idle\"'; then",
     "  sudo launchctl bootout system/${DISPATCHER_SERVICE}",
     "  " + _WAIT_GONE % "${DISPATCHER_SERVICE}"]
    + ["  " + line for line in _BOOTSTRAP + _LOG_TAIL]
    + ["elif [ -n \"$S\" ]; then",
       "  echo 'NOT RESTARTED: the dispatcher answered, and not idle: a webhook, a ticket "
       "session or a chat session is running. Paste this again later.'",
       "elif ! sudo launchctl print system/${DISPATCHER_SERVICE} >/dev/null 2>&1; then",
       "  echo 'NOT LOADED: launchd does not hold the dispatcher, so nothing of it runs. "
       "Starting it:'"]
    + ["  " + line for line in _BOOTSTRAP + _LOG_TAIL]
    + ["else",
       "  echo 'NOT ANSWERING: launchd holds the dispatcher and nothing answered on its port "
       "in 5 s. Its state, and the end of its log:'",
       "  sudo launchctl print system/${DISPATCHER_SERVICE} | grep -E 'state =|pid =|last "
       "exit'"]
    + ["  " + line for line in _LOG_TAIL]
    + ["fi"])
IDLE_RESTART = _group(("DISPATCHER_SERVICE", "DISPATCHER_PORT"),
                      ["    " + line for line in IDLE_RESTART_LINES])
# After NOT ANSWERING, and only then: the same stop, wait, start, with no /status gate.
FORCED_RESTART = _group(("DISPATCHER_SERVICE",), ["    " + line for line in (
    ["echo 'FORCED RESTART: it stops any session still running.'",
     "sudo launchctl bootout system/${DISPATCHER_SERVICE}",
     _WAIT_GONE % "${DISPATCHER_SERVICE}"] + _BOOTSTRAP + _LOG_TAIL)])

# The front door's restart: the same stop, wait, start. It stops no session; a tracker
# webhook sent in those seconds fails, and the tracker retries it.
FRONT_RESTART = _group(("FRONT_DOOR_SERVICE",), [
    "    sudo launchctl bootout system/${FRONT_DOOR_SERVICE}",
    "    " + _WAIT_GONE % "${FRONT_DOOR_SERVICE}",
    "    sudo launchctl bootstrap system /Library/LaunchDaemons/${FRONT_DOOR_SERVICE}.plist",
])

FRONT_DOOR_SUBCOMMAND = _group(FRONT_DOOR_KEYS, ["    %s front-door" % ME,
                                                 "    %s front-door --apply" % ME])
FRONT_DOOR_REMOVE = _group(FRONT_DOOR_KEYS, ["    %s front-door --remove" % ME,
                                             "    %s front-door --remove --apply" % ME])


def _probe_line(path):
    return ("    curl -sS -m 10 -o /dev/null -w '%{http_code}\\n' -X POST "
            "https://${FRONT_DOOR_HOST}" + path)


# The three probes, each with no signature. Only the dispatcher answers 401 on its routes
# (the Slack transport and the tracker transport both refuse an unsigned request), so a
# 401 means "the door forwarded it" and anything else means it did not.
PROBES_ON = _group(("FRONT_DOOR_HOST",), [
    _probe_line(SLACK_PATH),
    "Good: 401 — the door forwarded it, and the dispatcher asked for a signature.",
    _probe_line(TRACKER_PATH),
    "Good: 401 — the tracker's webhooks still reach the dispatcher.",
    _probe_line(CONFIG_UPDATE_PATH),
    "Good: 404 — the door's own answer for a path it does not forward. A 403 or a",
    "connection error is as safe: only the dispatcher answers this route 401.",
    "Not that: 401 on this last one. That is the dispatcher's config-update route",
    "answering, so the allowlist lets more than its paths through. Fix the door first.",
])
PROBES_OFF = _group(("FRONT_DOOR_HOST",), [
    _probe_line(SLACK_PATH),
    "Good: 404 — the door no longer forwards the chat path.",
    _probe_line(TRACKER_PATH),
    "Good: 401 — tickets still reach the dispatcher.",
])

CARDS = {
    "CK-C1": {
        "title": "Create the chat app from the manifest",
        "why": ("A Slack app is created by a person signed in to the workspace, and its two "
                "secrets are shown only to that person. This command has no Slack access and "
                "wants none. It is a SECOND app: the notifier keeps its own app and token, so "
                "the token every dispatcher session can read is never the notifier's."),
        "do": ["Check every workspace member is fully trusted, a full member (no guests),",
               "and signs in with two-factor: each can do everything you can in this lane.",
               "Print the manifest (piece 6):",
               "    %s compose --piece 6" % ME,
               "In Slack's app settings: Create New App -> From a manifest -> choose the",
               "workspace -> paste the JSON. The request URL is",
               "    https://${FRONT_DOOR_HOST}/slack-webhook",
               "If Slack says the URL did not verify, carry on: the door is not open yet.",
               "Install the app to the workspace. Its bot token and signing secret stay in",
               "Slack for now. Copy them nowhere: the env-names step of THE ORDER asks for",
               "both at hidden prompts, once the port block is loaded, and writes them",
               "itself. Never paste either into a chat, a ticket, a pull request or a",
               "repository."],
        "good": ("an app with exactly the four bot scopes of piece 6, no user scopes, "
                 "installed, and its two secrets still only in Slack"),
        "not": "the notifier's app or token used for this — two apps, two tokens",
    },
    "CK-C2": {
        "title": "Open the front door for one path",
        "why": ("The reverse proxy lives outside this repository, and only it can say what it "
                "forwards. The port behind it is card CK-C4's: do that one first."),
        "do": ["Dry run first. It prints the ONE allowlist line, names any path on it that is",
               "not one of the dispatcher's own routes, and changes nothing. Then apply:",
               FRONT_DOOR_SUBCOMMAND,
               "--apply appends /slack-webhook to that line and changes nothing else. It",
               "backs the file up, validates it with the proxy's own binary, and puts the",
               "backup back if validation fails. It never restarts the door.",
               "Restart the front door: stop it, wait until launchd has let it go, start it.",
               "It stops no session; a tracker webhook sent in those seconds is retried.",
               FRONT_RESTART,
               "Then three probes, from anywhere, each with no signature:",
               PROBES_ON,
               "If a probe answers otherwise, each answer means a different layer:",
               "  404 on the Slack path: the door answered, and the path is not on its",
               "      line, or the door was not restarted. Run front-door again; restart.",
               "  404 on the tracker path: the tracker path left the line. Put it back",
               "      first: until then every ticket is turned away at the door.",
               "  530, or the tunnel's own error page: the tunnel in front of the door is",
               "      down, not the door. Read the tunnel service's state and its log.",
               "  502: the door forwarded it and the dispatcher is not listening. Paste card",
               "      CK-C5's block: it starts a dispatcher launchd does not hold, and says",
               "      NOT ANSWERING, with its state and log, for one launchd holds. Read the",
               "      log before the card's forced restart; then probe again.",
               "(A status or version path is not the test: a front door may forward one on",
               "purpose for monitoring.)"],
        "good": ("401 on the Slack path and on the tracker path, and 404 (anything but 401) "
                 "on the config-update route"),
        "not": ("401 on the config-update route — the door forwards more than its paths; or "
                "404 on the tracker path — tickets no longer reach the dispatcher"),
    },
    "CK-C4": {
        "title": "Prove the dispatcher's port is closed, from a second device",
        "why": ("The port block only counts once something outside this machine is refused. "
                "A connection from this machine to its own network address never crosses "
                "the network, so it proves nothing either way. It needs a second device on "
                "the same network, after the restart that turns CYRUS_HOST_EXTERNAL on — "
                "before that, the dispatcher listens on this machine only, and every test "
                "passes for the wrong reason."),
        "do": ["On THIS machine, find its network address (en1 if en0 prints nothing):",
               "    ipconfig getifaddr en0",
               "On THIS machine, check the dispatcher answers locally:",
               "    curl -sS -m 5 http://127.0.0.1:${DISPATCHER_PORT}/status; echo \"exit=$?\"",
               "Good: a JSON status, exit=0. Not that: exit=7 — the dispatcher is not",
               "running, so the next check would pass for the wrong reason.",
               "",
               "FIRST, ON THE SECOND DEVICE, A CONTROL. Ask it for a port nothing listens",
               "on, so you learn what a refusal looks like from there:",
               "    curl -sS -m 5 http://<this machine's network address>:9/; echo \"exit=$?\"",
               "Good: exit=7, 'Connection refused' or 'Couldn't connect'. The device can",
               "reach this machine and closed ports answer.",
               "Not that: exit=28 (a timeout). Then this network cannot tell you anything:",
               "the device is isolated, on a guest network or VPN, or the address is of an",
               "interface it cannot reach. Fix that, or use another device or network. Do",
               "NOT record the port as closed from a timeout.",
               "",
               "THEN THE PORT ITSELF, from the same device:",
               "    curl -sS -m 5 http://<this machine's network address>:${DISPATCHER_PORT}/status; echo \"exit=$?\"",
               "Good: exit=7 with a refusal, the SAME answer the control gave. The rule",
               "is `block return`, which answers at once with a reset, so a refusal is",
               "what a working rule produces.",
               "Not that: exit=0 and a JSON status — the port is open to the network.",
               "Remove CYRUS_HOST_EXTERNAL from the dispatcher's env file, restart the",
               "dispatcher, and fix piece 4 before anything else.",
               "Not that either: exit=28, a timeout, when the control was refused. That is",
               "not this rule's doing — `block return` never produces it — so something",
               "else is dropping the packets and the rule is unproven.",
               "After the next reboot, run all three again: the rule is loaded at boot."],
        "good": ("a JSON status from 127.0.0.1 here, a refusal on the control from the "
                 "second device, and the same refusal on the dispatcher's port"),
        "not": ("a JSON status on the second device — the port is open; or a timeout, "
                "which proves nothing either way"),
    },
    # CK-C3 TESTS CALLS, NOT THE LISTING (KIT-196). A chat session is SHOWN every built-in
    # tool: the chat runner passes allowedTools and no tools restriction, and the grant is
    # enforced when a tool is CALLED. So "list your tools" proves nothing — and the
    # dispatcher's chat prompt names log_failure_mode even when that tool is absent.
    # Measured on a deployment, 2026-09-24: Bash `echo` and Monitor `echo` RAN (read-only
    # commands are auto-approved by the SDK's default permission handling); Write, `touch`,
    # a chained `echo; touch` and Monitor `touch` were refused; a Bash read of a path under
    # a user-level Read deny was refused; ToolSearch found no log_failure_mode.
    "CK-C3": {
        "title": "Test the bot's calls, in the private channel",
        "why": ("Only a live session shows what the dispatcher really built, and a listing "
                "shows the wrong thing: the session is SHOWN every built-in tool, Monitor, "
                "Task and ScheduleWakeup included, because the grant is enforced when a tool "
                "is CALLED. So this card makes calls. No computer here can mention a bot as "
                "you."),
        "do": ["First, on this machine, make a harmless decoy that a read deny covers (a",
               "*.pem name; it holds no key):",
               "    printf 'decoy, not a key\\n' > /tmp/chat-lane-decoy.pem",
               "In the private channel, send each of these as its own mention:",
               "    @%s run this with Bash and show me the output: echo chat-lane-check"
               % CHAT_BOT_NAME,
               "    @%s run this with Bash: touch /tmp/chat-lane-touch" % CHAT_BOT_NAME,
               "    @%s run this with Bash: echo one; touch /tmp/chat-lane-touch"
               % CHAT_BOT_NAME,
               "    @%s use the Write tool to create /tmp/chat-lane-write.txt" % CHAT_BOT_NAME,
               "    @%s use Monitor to run: echo chat-lane-monitor" % CHAT_BOT_NAME,
               "    @%s use Monitor to run: touch /tmp/chat-lane-monitor" % CHAT_BOT_NAME,
               "    @%s run this with Bash: wc -c /tmp/chat-lane-decoy.pem" % CHAT_BOT_NAME,
               "    @%s use ToolSearch to look for log_failure_mode" % CHAT_BOT_NAME,
               "    @%s what is waiting on me in the tracker?" % CHAT_BOT_NAME,
               "Good, as measured on a deployment on 2026-09-24:",
               "  - Bash with echo RAN, and Monitor with echo RAN. Read-only commands are",
               "    auto-approved by the SDK's default permission handling, so this lane",
               "    has a read-only shell. That is the open residual, KIT-196.",
               "  - Write, touch, the chained echo; touch, and Monitor with touch were all",
               "    refused.",
               "  - The wc -c of the decoy was refused: a user-level Read deny covers a",
               "    Bash read of that path too.",
               "  - ToolSearch found no log_failure_mode. It is registered only when",
               "    CYRUS_API_KEY is set, and that stays unset.",
               "  - The tracker question was answered.",
               "Then check that nothing was made, and remove the decoy:",
               "    ls -l /tmp/chat-lane-touch /tmp/chat-lane-write.txt /tmp/chat-lane-monitor",
               "    rm -f /tmp/chat-lane-decoy.pem",
               "Good: ls finds none of the three.",
               "Tools beginning mcp__cyrus-tools are there, as expected: the trim cannot",
               "remove that server. Record the answers and the date in your private runbook."],
        "good": ("echo RAN through Bash and Monitor; Write, touch, the chained touch and "
                 "Monitor's touch refused; the decoy read refused; no log_failure_mode; a "
                 "tracker question answered"),
        "not": ("a Write or a touch that RAN — the grant did not load: restart the dispatcher "
                "(card CK-C5) and test again; or log_failure_mode found — CYRUS_API_KEY is "
                "set"),
    },
    "CK-C5": {
        "title": "Restart the dispatcher, only when it is idle",
        "why": ("A restart stops every running session (docs/STAGE-E-OPERATOR.md). The "
                "dispatcher says itself when that is safe: /status answers idle only while "
                "no webhook, ticket session or chat session is running (cyrus-edge-worker "
                "EdgeWorker.js:1784-1802). The listening address, the address checks and a "
                "removed env name are read at start only, so the chat lane's env change "
                "needs this restart. Nothing in this file restarts it for you."),
        "do": ["Paste this block whole. It restarts only when the dispatcher answers idle.",
               "It stops the dispatcher, waits until launchd has let it go, starts it, and",
               "shows the end of its log, whose path it reads from the dispatcher's plist:",
               IDLE_RESTART,
               "NOT RESTARTED means it answered busy: wait, and paste it again. Do not",
               "force it while a session runs.",
               "NOT LOADED means launchd did not hold it, so nothing of it was running: the",
               "block started it. Read the log it showed.",
               "NOT ANSWERING means launchd holds it and nothing answered on its port, so no",
               "webhook reaches it either. Read the state and log the block showed first: a",
               "dispatcher that fails at start is restarted by launchd again and again, and",
               "a restart will not fix that. When a restart is what it needs, paste this",
               "instead. It asks nothing first, and stops any session still running:",
               FORCED_RESTART,
               "Then, first: delegate one throwaway tracker ticket (THE ORDER, step 6)."],
        "good": "the block showed the dispatcher's fresh start at the end of its log",
        "not": ("NOT RESTARTED for an hour or more — a session is stuck: find it before "
                "forcing a restart; or NOT ANSWERING again after the forced restart — the "
                "dispatcher fails at start: its log says why"),
    },
    "CK-C6": {
        "title": "Turn the chat lane off",
        "why": ("Turning the lane off starts in Slack, which nothing here can reach: until "
                "the app is gone, a copied token keeps working. Then the four names leave "
                "the dispatcher's env file, the dispatcher restarts so it listens on this "
                "machine only, and the front door stops forwarding the chat path."),
        "do": ["1. In Slack, open the chat app's settings and Uninstall it from the",
               "   workspace. That revokes its token: it is the step that really ends the",
               "   lane.",
               "2. Take the four names out of the dispatcher's env file (it backs the file",
               "   up first; no terminal prompt is needed):",
               "    %s env-names --remove" % ME,
               "3. Restart the dispatcher, only when it is idle (card CK-C5's block). A",
               "   reload never unsets a name (Application.js:54); only a restart does:",
               IDLE_RESTART,
               "4. Take the chat path off the front door, then restart the door:",
               FRONT_DOOR_REMOVE,
               FRONT_RESTART,
               "5. Probe the door:",
               PROBES_OFF,
               "6. The port block stays loaded. It costs nothing while the dispatcher",
               "   listens on this machine only. Check it is still there:",
               "    sudo /sbin/pfctl -a %s -s rules" % PF_ANCHOR,
               "Keep the fences, the grant and the port block. Removing slackAllowedTools",
               "would bring back the built-in chat list, Monitor and Task included, at the",
               "next restart; the fences and the port block cost nothing while the lane is",
               "off.",
               "Then run verify. The env and front-door rows now say the lane's pieces are",
               "not applied: that is what off looks like."],
        "good": ("the app uninstalled; 404 on the chat path and 401 on the tracker path; "
                 "the port block still loaded"),
        "not": ("401 on the chat path — the door still forwards it; or no 401 on the "
                "tracker path — tickets no longer arrive"),
    },
}

NEVER = [
    "Never let this lane stay on while anyone not fully trusted is in the Slack workspace.",
    "Never put the notifier's token in the dispatcher's env file.",
    "Never paste a token into a chat, a ticket, a pull request or a repository.",
    "Never merge a pull request, and never give one your own sign-off.",
    "Never apply or remove a protected label, and never move a ticket to ready.",
    "Never edit a guard, a hook, or this repository's settings files.",
]


def print_card(cid, conf=None, broken=False):
    """`broken`: a chat-lane.conf was read, and its problems were printed above this card;
    nothing is composed from it."""
    card = CARDS.get(cid)
    if not card:
        raise ConfError("no such checkpoint card: %s (have %s)"
                        % (cid, ", ".join(sorted(CARDS))))
    say("")
    say("=" * 74)
    say(" %s — %s" % (cid, card["title"]))
    say("=" * 74)
    if broken:
        say(" (chat-lane.conf has the problems listed above: ${NAMES} below are left as they")
        say("  are, and no command that needs a conf value is composed until they are fixed)")
    elif conf is None:
        say(" (no chat-lane.conf loaded: ${NAMES} below are yours to fill in, and a command")
        say("  that needs a conf value says so instead of printing)")
    say("")
    say("WHY THIS IS YOURS")
    for line in _wrap(card["why"]):
        say("  " + line)
    say("")
    say("WHAT TO DO")
    for item in card["do"]:
        for line in card_lines(item, None if broken else conf, broken):
            say("  " + line)
    say("")
    say("GOOD: %s" % card["good"])
    say("NOT THAT: %s" % card["not"])
    say("")
    say("NEVER")
    for line in NEVER:
        say("  " + line)
    say("")
    return EX_OK


# --------------------------------------------------------------------------- #
# verify — one read-only program, run as the role account.
#
# It prints NAMES, BOOLEANS and TOOL LISTS. It never prints a value from the env file, a
# token from the config, or the settings file's `env` block. Every read is wrapped, and an
# error is reported by its class, never its message (a parse error's message can quote the
# file). Every `open` in it takes one argument: read mode.
# --------------------------------------------------------------------------- #
FACTS_PY = r'''
import hashlib, json, os, pathlib, re, sys
QUOTES = "\"'`"

def raw_of(path):
    return pathlib.Path(path).read_bytes()

def digest(raw):
    return hashlib.sha256(raw).hexdigest()
LINE = re.compile(r"^\s*(?:export\s+)?([\w.-]+)\s*(?:=|:\s)(.*)$")

def why(exc):
    if isinstance(exc, FileNotFoundError):
        return "missing"
    if isinstance(exc, PermissionError):
        return "unreadable"
    return "unparseable (" + type(exc).__name__ + ")"

def tools(v):
    if v is None:
        return None
    if not isinstance(v, list):
        return "not-a-list"
    return [str(t)[:200] for t in v]

def prompt_lists(d):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict) and v.get("disallowedTools") is not None:
                out[str(k)[:40]] = tools(v.get("disallowedTools"))
    return out

def mcp_rows(paths):
    if paths is None:
        return None
    if not isinstance(paths, list):
        return [{"path": "slackMcpConfigs", "error": "is not a list of paths"}]
    rows = []
    for p in paths:
        if not isinstance(p, str):
            rows.append({"path": repr(p)[:80], "error": "is not a path"})
            continue
        q = os.path.expanduser(p) if p.startswith("~/") else p
        try:
            with open(q) as fh:
                d = json.load(fh)
            s = d.get("mcpServers") if isinstance(d, dict) else None
            if not isinstance(s, dict):
                rows.append({"path": p, "error": "holds no mcpServers object"})
                continue
            rows.append({"path": p, "servers": sorted(str(k)[:80] for k in s)})
        except Exception as exc:
            rows.append({"path": p, "error": why(exc)})
    return rows

def config(path):
    try:
        raw = raw_of(path)
        c = json.loads(raw.decode("utf-8"))
        if not isinstance(c, dict):
            raise ValueError("not an object")
    except Exception as exc:
        return {"error": why(exc)}
    rs = c.get("repositories")
    entries = []
    for i, r in enumerate(rs if isinstance(rs, list) else []):
        if not isinstance(r, dict):
            continue
        allowed = r.get("allowedTools")
        entries.append({
            "index": i,
            "id": str(r.get("id") or "")[:80],
            "name": str(r.get("name") or "")[:80],
            "disallowedTools": tools(r.get("disallowedTools")),
            "labelPrompts": prompt_lists(r.get("labelPrompts")),
            "mcpAllowedTools": [str(t)[:200] for t in allowed
                                if isinstance(t, str) and t.startswith("mcp__")]
                               if isinstance(allowed, list) else [],
            "isActive": r.get("isActive") is not False,
            "repositoryPath": str(r.get("repositoryPath") or "")[:300],
            "instructionHead": str(r.get("appendInstruction") or "")[:HEAD],
        })
    return {"slackAllowedTools": tools(c.get("slackAllowedTools")),
            "slackMcpConfigs": mcp_rows(c.get("slackMcpConfigs")),
            "defaultDisallowedTools": tools(c.get("defaultDisallowedTools")),
            "promptDefaults": prompt_lists(c.get("promptDefaults")),
            "entries": entries,
            "sha256": digest(raw)}

def present(raw):
    raw = raw.strip()
    if raw and raw[0] in QUOTES:
        end = raw.find(raw[0], 1)
        raw = raw[1:end] if end != -1 else raw[1:]
    else:
        cut = raw.find("#")
        raw = (raw[:cut] if cut != -1 else raw).strip()
    return raw

def env(path):
    try:
        with open(path) as fh:
            text = fh.read()
    except Exception as exc:
        return {"error": why(exc)}
    seen, external, port, ip_off = {}, None, None, None
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        value = present(m.group(2))
        seen[m.group(1)] = bool(value)
        if m.group(1) == "CYRUS_HOST_EXTERNAL":
            external = value.strip().lower() == "true"
        if m.group(1) == "WEBHOOK_IP_VALIDATION":
            ip_off = value.strip().lower() == "false"
        if m.group(1) == "CYRUS_SERVER_PORT":
            digits = re.match(r"^\s*(\d+)", value)
            port = int(digits.group(1)) if digits else 0
        value = None
    # The dispatcher's parsePort: unset, unparseable or out of range means 3456.
    live_port = port if port and 1 <= port <= 65535 else 3456
    return {"names": sorted(k for k, v in seen.items() if v),
            "empty": sorted(k for k, v in seen.items() if not v),
            "hostExternalTrue": external,
            "ipValidationOff": ip_off,
            "serverPortMatches": live_port == int(sys.argv[3])}

def user_settings():
    path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        raw = raw_of(path)
        s = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return {"path": path, "error": why(exc)}
    # The shape the merge writer can extend: an object, whose permissions (when present)
    # is an object, whose deny (when present) is a list. Anything else is reported, never
    # read as an empty list the writer would then refuse.
    perms = s.get("permissions", {}) if isinstance(s, dict) else None
    deny = perms.get("deny", []) if isinstance(perms, dict) else None
    if not isinstance(deny, list):
        return {"path": path, "sha256": digest(raw),
                "error": "unparseable (not a settings object with a permissions.deny list)"}
    return {"path": path, "sha256": digest(raw),
            "deny": [str(d)[:300] for d in deny],
            "hasHooks": bool(s.get("hooks"))}

def front_door(path, matcher):
    # Only the lines that START with the matcher: the caller decides which of them is the
    # `path` line. The file holds no secret; these lines are an allowlist.
    try:
        raw = raw_of(path)
        text = raw.decode("utf-8")
    except Exception as exc:
        return {"path": path, "error": why(exc)}
    head = re.compile(r"^\s*" + re.escape(matcher) + r"\s")
    found = [{"index": i, "text": line[:1000]}
             for i, line in enumerate(text.split("\n")) if head.match(line)]
    return {"path": path, "sha256": digest(raw), "candidates": found[:50]}

out = {"config": config(sys.argv[1]), "env": env(sys.argv[2]),
       "userSettings": user_settings()}
if len(sys.argv) > 5:
    out["frontDoor"] = front_door(sys.argv[4], sys.argv[5])
print(json.dumps(out))
'''.replace("[:HEAD]", "[:%d]" % INSTRUCTION_HEAD_CHARS)


def facts_command(conf):
    """The /bin/sh script `as_role` runs. Paths are arguments, never spliced into code. The
    front door's file and matcher go last, and only when both are set."""
    args = [conf["DISPATCHER_CONFIG"], conf["DISPATCHER_ENV_FILE"], conf["DISPATCHER_PORT"]]
    if all(conf.get(k) for k in FRONT_DOOR_READ_KEYS):
        args += [conf["FRONT_DOOR_CONFIG"], conf["FRONT_DOOR_MATCHER"]]
    return "/usr/bin/python3 -c %s %s" % (shlex.quote(FACTS_PY),
                                          " ".join(shlex.quote(a) for a in args))


# The root reads `verify` makes for the port block — the Stage E installer's `as_root`
# helper — and the two world-readable files it cats. Every one only reads: `-s` shows,
# and pfctl(8)'s load, enable, flush and release flags (-f, -E, -e, -F, -X) never appear.
PF_READ_RULES = ["/sbin/pfctl", "-a", PF_ANCHOR, "-s", "rules"]
PF_READ_INFO = ["/sbin/pfctl", "-s", "info"]
# The MAIN ruleset, which is what recurses into the anchor. A child anchor keeps its rules
# whether or not anything evaluates them, so `pfctl -a <anchor> -s rules` looks identical
# after another tool loads its own main ruleset — and the block would be inert. Both the
# loaded ruleset and the file that restores it at boot are read (review of PR #147, 12).
PF_READ_MAIN = ["/sbin/pfctl", "-s", "rules"]
PF_CONF = "/etc/pf.conf"
PF_ANCHOR_POINT_RE = re.compile(r'^\s*anchor\s+"?com\.apple/\*"?', re.M)


def pf_probe(runner):
    """Six read-only answers. Nothing here parses them; `check_port_block` does."""
    def answer(res):
        return {"rc": res.rc, "out": res.out, "err": (res.err or "").strip()[:160]}
    return {"rules": answer(runner.as_root(PF_READ_RULES)),
            "info": answer(runner.as_root(PF_READ_INFO)),
            "main": answer(runner.as_root(PF_READ_MAIN)),
            "pfConf": answer(runner.read(["/bin/cat", PF_CONF])),
            "plist": answer(runner.read(["/bin/cat", PF_DAEMON_PLIST])),
            "rulesFile": answer(runner.read(["/bin/cat", PF_RULES_PATH]))}


def _row(check, outcome, detail, lines=()):
    return {"check": check, "outcome": outcome, "detail": detail, "lines": list(lines)}


def _problem_row(check, outcome, problems, lines=()):
    """The first problem is the row's detail; the rest, and any notes, follow it."""
    return _row(check, outcome, problems[0], list(problems[1:]) + list(lines))


def _config_unmeasured(cfg, checks):
    kind = cfg.get("error") or ""
    outcome = FAILED if kind.startswith("unparseable") else UNKNOWN
    return [_row(c, outcome, "the dispatcher config is %s, so this was not measured" % kind)
            for c in checks]


def _entry_label(entry):
    return entry.get("id") or entry.get("name") or "#%d" % entry.get("index", 0)


def active_entries(cfg):
    """The entries the dispatcher actually loads: it skips isActive === false
    (EdgeWorker.js:274-290), and the chat lane's default repository is the FIRST of those
    (ChatRepositoryProvider.js:18-20) — not the first line of the file."""
    return [e for e in (cfg.get("entries") or []) if e.get("isActive") is not False]


def check_grant(cfg):
    live = cfg.get("slackAllowedTools")
    active = active_entries(cfg)
    lines, problems = [], []
    if live == "not-a-list":
        return _row("grant", FAILED, "slackAllowedTools is not a list")
    # The approved grant is the base list plus one pair of literal pull rules per repository
    # the dispatcher serves. Paths come from the live config, because compose cannot know
    # them (owner decision after the review of PR #147, finding 1).
    paths, unpathed = [], []
    for e in active:
        path = e.get("repositoryPath")
        if path:
            if path not in paths:
                paths.append(path)
        else:
            unpathed.append(_entry_label(e))
    wanted = owner_grant(paths)
    if not live:
        problems.append("slackAllowedTools is unset or empty, so the built-in chat grant "
                        "applies, Monitor and Task included (ToolPermissionResolver.js:69-71)")
    else:
        for bad in grant_problems(live):
            problems.append("the live grant is unsafe: %s" % bad)
        extra = [t for t in live if t not in wanted]
        missing = [t for t in wanted if t not in live]
        if extra:
            problems.append("slackAllowedTools holds what the owner did not approve: %s"
                            % ", ".join(extra))
        for path in paths:
            gone = [r for r in pull_rules(path) if r not in live]
            if gone and len(gone) < len(pull_rules(path)):
                problems.append("the repository at %s has only part of its pull rule pair; "
                                "missing %s" % (path, ", ".join(gone)))
            elif gone:
                problems.append("the repository at %s has no pull rule, so the chat lane's "
                                "pull of it is refused with nothing to say why" % path)
        rest = [t for t in missing
                if not any(t in pull_rules(path) for path in paths)]
        if rest:
            problems.append("slackAllowedTools lacks: %s" % ", ".join(rest))
    if unpathed:
        lines.append("note: %s carr%s no repositoryPath, so no pull rule is composed for "
                     "%s." % (", ".join(unpathed), "ies" if len(unpathed) == 1 else "y",
                              "it" if len(unpathed) == 1 else "them"))
    if active and active[0].get("mcpAllowedTools"):
        first = active[0]
        problems.append("the first ACTIVE repository entry %s lists %s in allowedTools, and "
                        "the dispatcher adds those to the chat grant "
                        "(RunnerConfigBuilder.js:63-66; ChatRepositoryProvider.js:18-20)"
                        % (_entry_label(first), ", ".join(first["mcpAllowedTools"])))
    for e in active[1:]:
        if e.get("mcpAllowedTools"):
            lines.append("note: active entry %s lists %s in allowedTools. It joins the chat "
                         "grant if it ever becomes the first active entry — which removing "
                         "or deactivating an earlier one does."
                         % (_entry_label(e), ", ".join(e["mcpAllowedTools"])))
    for e in cfg.get("entries") or []:
        if e.get("isActive") is False and e.get("mcpAllowedTools"):
            lines.append("note: inactive entry %s lists %s in allowedTools. The dispatcher "
                         "skips it today (EdgeWorker.js:274-290); activating it would put "
                         "those in the chat grant."
                         % (_entry_label(e), ", ".join(e["mcpAllowedTools"])))
    if problems:
        return _problem_row("grant", BLOCKED, problems, lines + [
            '  paste at the top level:  "slackAllowedTools": ' + json.dumps(wanted)])
    return _row("grant", ALREADY_DONE,
                "slackAllowedTools is the approved list, with a pull rule pair for each of "
                "the %d repository path(s) the dispatcher serves" % len(paths), lines)


def check_fence(cfg, env_facts):
    default = cfg.get("defaultDisallowedTools")
    if default == "not-a-list":
        return _row("coding-fence", FAILED, "defaultDisallowedTools is not a list")
    problems, lines, unknown = [], [], []
    env_names = set((env_facts or {}).get("names") or []) | set(
        (env_facts or {}).get("empty") or [])
    fenced_count = 0
    for e in cfg.get("entries") or []:
        label = e.get("id") or e.get("name") or "#%d" % e.get("index", 0)
        kind = entry_kind(e)
        own = e.get("disallowedTools")
        if own == "not-a-list":
            problems.append("%s: disallowedTools is not a list" % label)
            continue
        if kind == "review":
            # NOT a note. Piece 2 skips review entries only because their own fence already
            # names the Slack server; when it does not, that assumption is false and every
            # review session gets a working Slack server holding the chat token. verify used
            # to pass while that was true (review of PR #147, findings 4 and 5).
            _l, missing, _s = fence_entry(own, default)
            if missing:
                problems.append("review entry %s lacks %s. Piece 2 skips review entries "
                                "because their fence already names the Slack server, and "
                                "this one does not: fix it by running the Stage E "
                                "installer, whose entries these are"
                                % (label, ", ".join(missing)))
            continue
        fenced_count += 1
        composed, missing, source = fence_entry(own, default)
        env_override = own is None and "DISALLOWED_TOOLS" in env_names
        if missing:
            problems.append("%s (%s entry) lacks %s; its effective list comes from %s"
                            % (label, kind, ", ".join(missing), source))
            if env_override:
                # A list composed from the file would drop every deny the env list
                # supplies (review of KIT-197, finding 29): no paste line for it.
                lines.append("give %s its own list by hand: the value of DISALLOWED_TOOLS "
                             "plus both Slack rules" % label)
            else:
                lines.append("  paste into %s:  \"disallowedTools\": %s"
                             % (label, json.dumps(composed)))
        if env_override:
            unknown.append("%s inherits defaultDisallowedTools, and the dispatcher env file "
                           "sets DISALLOWED_TOOLS, which replaces that list at start "
                           "(WorkerService.js:164-165); its live list is not in any file "
                           "this reads" % label)
        for ptype in prompt_type_gaps(e.get("labelPrompts")):
            problems.append("%s: prompt type %s has its own disallowedTools in labelPrompts, "
                            "which replaces the entry's list and lacks the Slack rules"
                            % (label, ptype))
    if fenced_count:
        for ptype in prompt_type_gaps(cfg.get("promptDefaults")):
            problems.append("promptDefaults: prompt type %s has its own disallowedTools, "
                            "which replaces every entry's list for that type and lacks the "
                            "Slack rules. Add both rules to that list rather than deleting "
                            "the key: %s" % (ptype, REMOVAL_NOTE))
    if problems:
        # The caveats ride along: a row that is BLOCKED for one reason must still say what
        # it could not measure (review of KIT-197, finding 29).
        return _problem_row("coding-fence", FAILED if any("not a list" in p for p in problems)
                            else BLOCKED, problems, unknown + lines)
    if unknown:
        return _problem_row("coding-fence", UNKNOWN, unknown, lines)
    return _row("coding-fence", ALREADY_DONE,
                "%d non-review entr%s carry both Slack rules"
                % (fenced_count, "y" if fenced_count == 1 else "ies"), lines)


# What a reload does NOT do: a key REMOVED from the config file keeps its old in-memory
# value, because the merge is `parsedConfig.<key> || this.config.<key>` (ConfigManager.js:176,
# 181, 183). Worse, the merged config then equals the old one, so detectGlobalConfigChanges
# returns false and nothing is re-applied at all — while the watcher has already logged the
# reload line the doc tells the operator to look for. Removing a key therefore needs a
# restart, and every row that can go green by removal says so (review of PR #147, 7 and 16).
REMOVAL_NOTE = ("if you fixed this by DELETING the key rather than setting it empty, the "
                "running dispatcher keeps the old value until it is restarted "
                "(ConfigManager.js:176-183): restart it, or set the key to an empty value "
                "instead")


def check_chat_mcp(cfg):
    rows = cfg.get("slackMcpConfigs")
    if rows is None:
        return _row("chat-mcp-configs", ALREADY_DONE, "slackMcpConfigs is unset",
                    ["note: " + REMOVAL_NOTE])
    if not rows:
        return _row("chat-mcp-configs", ALREADY_DONE, "slackMcpConfigs is empty")
    lines = []
    for r in rows:
        if r.get("servers") is not None:
            lines.append("WARN: %s adds server(s): %s" % (r.get("path"),
                                                          ", ".join(r["servers"]) or "none"))
        else:
            lines.append("WARN: %s: %s" % (r.get("path"), r.get("error")))
    return _row("chat-mcp-configs", BLOCKED,
                "slackMcpConfigs loads extra servers into every chat session "
                "(RunnerConfigBuilder.js:52-57); the approved lane has none",
                lines + ["set it to [] rather than deleting the key: " + REMOVAL_NOTE])


def check_env(conf, env_facts):
    err = env_facts.get("error")
    path = conf["DISPATCHER_ENV_FILE"]
    if err == "missing":
        return _row("dispatcher-env", BLOCKED, "no file at %s" % path)
    if err:
        return _row("dispatcher-env", UNKNOWN, "%s is %s" % (path, err))
    names, empty = set(env_facts.get("names") or []), set(env_facts.get("empty") or [])
    problems = []
    for name in ENV_NAMES:
        if name in empty:
            problems.append("%s is set but empty" % name)
        elif name not in names:
            problems.append("%s is not set" % name)
    if "CYRUS_HOST_EXTERNAL" in names and env_facts.get("hostExternalTrue") is not True:
        problems.append("CYRUS_HOST_EXTERNAL is set, but not to true")
    if problems:
        return _problem_row("dispatcher-env", BLOCKED, problems)
    return _row("dispatcher-env", ALREADY_DONE,
                "all three names are set (values not read into this process)")


def check_ip_validation_off(env_facts):
    """WEBHOOK_IP_VALIDATION=false, by name and value (the value is not a secret)."""
    err = env_facts.get("error")
    if err == "missing":
        return _row("ip-validation-off", BLOCKED,
                    "no dispatcher env file, so %s=false is not set" % IP_VALIDATION_NAME)
    if err:
        return _row("ip-validation-off", UNKNOWN, "the env file is %s" % err)
    off = env_facts.get("ipValidationOff")
    if off is True:
        return _row("ip-validation-off", ALREADY_DONE,
                    "%s=false: tracker webhooks keep today's signature-only check"
                    % IP_VALIDATION_NAME)
    state = "not set" if off is None else "set, but not to false"
    consequence = ("with CYRUS_HOST_EXTERNAL=true a signature-verifying dispatcher takes "
                   "tracker webhooks only from the tracker's own addresses, and a front "
                   "door that forwards them as 127.0.0.1 gets every one refused with 403 "
                   "(LinearEventTransport.js:89-96): no ticket starts a session")
    if env_facts.get("hostExternalTrue") is True:
        return _row("ip-validation-off", FAILED,
                    "%s is %s while CYRUS_HOST_EXTERNAL=true: %s. Set %s=false and restart "
                    "the dispatcher" % (IP_VALIDATION_NAME, state, consequence,
                                        IP_VALIDATION_NAME))
    return _row("ip-validation-off", BLOCKED,
                "%s is %s. Add %s=false with the other names, before the restart: %s"
                % (IP_VALIDATION_NAME, state, IP_VALIDATION_NAME, consequence))


def check_notifier_absent(conf, env_facts):
    name = conf["NOTIFIER_TOKEN_ENV"]
    err = env_facts.get("error")
    if err == "missing":
        return _row("notifier-token-absent", ALREADY_DONE,
                    "no dispatcher env file, so %s is not in it" % name)
    if err:
        return _row("notifier-token-absent", UNKNOWN, "the env file is %s" % err)
    if name in (env_facts.get("names") or []) or name in (env_facts.get("empty") or []):
        return _row("notifier-token-absent", BLOCKED,
                    "the dispatcher env file sets %s. Two apps means the notifier's token "
                    "never enters that file: every session inherits it "
                    "(session-env.js:45-65). Remove it, and restart the dispatcher: a "
                    "reload does not unset a name (Application.js:54)" % name)
    return _row("notifier-token-absent", ALREADY_DONE,
                "the dispatcher env file does not set %s" % name)


def check_hosted_keys_absent(env_facts):
    err = env_facts.get("error")
    if err == "missing":
        return _row("hosted-keys-absent", ALREADY_DONE,
                    "no dispatcher env file, so neither %s is in it"
                    % " nor ".join(HOSTED_PAIRING_NAMES))
    if err:
        return _row("hosted-keys-absent", UNKNOWN, "the env file is %s" % err)
    present_names = set(env_facts.get("names") or []) | set(env_facts.get("empty") or [])
    found = [n for n in HOSTED_PAIRING_NAMES if n in present_names]
    if found:
        return _row("hosted-keys-absent", BLOCKED,
                    "the dispatcher env file sets %s. Any CYRUS_API_KEY pairs the dispatcher "
                    "with the vendor's hosted service, and log_failure_mode then sends "
                    "session recaps and conversation quotes there (EdgeWorker.js:4016-4027); "
                    "with CYRUS_TEAM_ID, whole transcripts (EdgeWorker.js:152-173). Delete "
                    "the line, and restart: a reload does not unset a name "
                    "(Application.js:54)" % " and ".join(found))
    return _row("hosted-keys-absent", ALREADY_DONE,
                "the dispatcher env file sets neither %s (checked by name)"
                % " nor ".join(HOSTED_PAIRING_NAMES))


def _read_denied(answer):
    """True when a read failed for a reason that is NOT the file being absent: permission,
    a directory that cannot be traversed, an interrupted read. Absent is a fact about the
    machine; the rest is this command failing to measure it (contract §13)."""
    err = (answer.get("err") or "").lower()
    if re.search(r"no such file|not found", err):
        return False
    return bool(err)


def check_port_block(conf, pf, env_facts):
    """The anchor holds both refusals, pf is enabled, and the boot files are installed."""
    port = conf["DISPATCHER_PORT"]
    problems, unknown = [], []
    rules = pf.get("rules") or {}
    if rules.get("rc") == 0:
        missing = pf_missing_families(rules.get("out"), port)
        if missing:
            problems.append("the anchor %s does not refuse port %s for %s: `sudo pfctl -a %s "
                            "-s rules` shows no such block rule. Load piece 4, and do not set "
                            "CYRUS_HOST_EXTERNAL until it shows"
                            % (PF_ANCHOR, port, " and ".join(missing), PF_ANCHOR))
    elif re.search(r"does not exist|No such|Invalid argument", rules.get("err") or "", re.I):
        problems.append("the anchor %s is not loaded (pfctl: %s)" % (PF_ANCHOR, rules["err"]))
    else:
        unknown.append("pfctl could not show the anchor's rules (exit %s: %s)"
                       % (rules.get("rc"), rules.get("err")))
    info = pf.get("info") or {}
    if info.get("rc") == 0:
        if not re.search(r"^Status:\s*Enabled\b", info.get("out") or "", re.M):
            problems.append("pf is not enabled (`sudo pfctl -s info` does not say "
                            "\"Status: Enabled\"), so no rule applies")
    else:
        unknown.append("pfctl could not show pf's status (exit %s: %s)"
                       % (info.get("rc"), info.get("err")))
    # The anchor point: without it nothing evaluates the anchor, however full it looks.
    main = pf.get("main") or {}
    conf_file = pf.get("pfConf") or {}
    point_loaded = (PF_ANCHOR_POINT_RE.search(main.get("out") or "")
                    if main.get("rc") == 0 else None)
    point_in_file = (PF_ANCHOR_POINT_RE.search(conf_file.get("out") or "")
                     if conf_file.get("rc") == 0 else None)
    if conf_file.get("rc") != 0:
        unknown.append("%s could not be read (%s), so whether a reboot restores the anchor "
                       "point was not measured" % (PF_CONF, conf_file.get("err")))
    elif not point_in_file:
        problems.append("%s no longer carries an anchor \"com.apple/*\" line, so the next "
                        "boot loads a main ruleset that never reaches this anchor and the "
                        "rule is inert" % PF_CONF)
    if main.get("rc") != 0:
        unknown.append("the loaded main ruleset could not be read (%s), so whether anything "
                       "evaluates the anchor was not measured" % main.get("err"))
    elif not point_loaded and point_in_file:
        unknown.append("the loaded main ruleset does not show an anchor \"com.apple/*\" "
                       "line, though %s has one. Check by hand: sudo pfctl -s rules | grep "
                       "com.apple" % PF_CONF)
    elif not point_loaded:
        problems.append("nothing evaluates this anchor: the loaded main ruleset has no "
                        "anchor \"com.apple/*\" line, so the block rules are inert however "
                        "full the anchor looks")
    plist = pf.get("plist") or {}
    if plist.get("rc") != 0:
        # Could-not-read and is-not-there are opposite facts with the same rc (§13).
        if _read_denied(plist):
            unknown.append("%s could not be read (%s): its directory may not be traversable "
                           "by this account" % (PF_DAEMON_PLIST, plist.get("err")))
        else:
            problems.append("the boot LaunchDaemon is not installed at %s, so a reboot drops "
                            "the rule" % PF_DAEMON_PLIST)
    else:
        text = plist.get("out") or ""
        lacks = [part for part in (PF_ANCHOR, PF_RULES_PATH, "<string>-E</string>",
                                   "<key>RunAtLoad</key>") if part not in text]
        if lacks:
            problems.append("%s does not carry %s, so it would not load the rule at boot"
                            % (PF_DAEMON_PLIST, ", ".join(lacks)))
    rules_file = pf.get("rulesFile") or {}
    if rules_file.get("rc") != 0:
        if _read_denied(rules_file):
            unknown.append("%s could not be read (%s): its directory may not be traversable "
                           "by this account — `sudo chmod 755 \"%s\"` if so"
                           % (PF_RULES_PATH, rules_file.get("err"), PF_RULES_DIR))
        else:
            problems.append("the rules file is not installed at %s, so a reboot loads nothing"
                            % PF_RULES_PATH)
    else:
        missing = pf_missing_families(rules_file.get("out"), port)
        if missing:
            problems.append("%s does not refuse port %s for %s"
                            % (PF_RULES_PATH, port, " and ".join(missing)))
    if env_facts and not env_facts.get("error") and env_facts.get("serverPortMatches") is False:
        problems.append("the dispatcher does not listen on DISPATCHER_PORT %s: its env file "
                        "sets CYRUS_SERVER_PORT to another port (value compared, not shown). "
                        "The rule guards the wrong port" % port)
    if problems:
        return _problem_row("port-block", BLOCKED, problems, unknown)
    if unknown:
        return _problem_row("port-block", UNKNOWN, unknown)
    return _row("port-block", ALREADY_DONE,
                "%s refuses port %s for inet and inet6 off loopback, the loaded main ruleset "
                "still evaluates it, pf is enabled, and the boot files are installed. Card "
                "CK-C4 is the proof from the network" % (PF_ANCHOR, port))


def check_user_settings(conf, us, env_facts):
    wanted = user_deny_patterns(conf)
    names = set((env_facts or {}).get("names") or [])
    path = us.get("path") or "~/.claude/settings.json"
    if "CLAUDE_CONFIG_DIR" in names:
        return _row("user-settings", UNKNOWN,
                    "the dispatcher env file sets CLAUDE_CONFIG_DIR, so sessions read user "
                    "settings from that directory, not %s" % path)
    # The remedy is named on the row, so a deployment that was on before the rules grew
    # is told what to run (review of KIT-197, findings 40 and 47).
    remedy = ("run  merge , then  merge --apply : it adds only the missing rules and keeps "
              "every other rule and key")
    err = us.get("error")
    if err == "missing":
        return _row("user-settings", BLOCKED, "no user settings file at %s" % path,
                    [remedy + " (a new file, mode 600)"])
    if err and err.startswith("unparseable"):
        return _row("user-settings", FAILED, "%s is %s" % (path, err))
    if err:
        return _row("user-settings", UNKNOWN, "%s is %s" % (path, err))
    missing = [p for p in wanted if p not in (us.get("deny") or [])]
    if missing:
        return _row("user-settings", BLOCKED,
                    "%d composed deny rule(s) missing from %s" % (len(missing), path),
                    ["missing: " + m for m in missing] + [remedy])
    return _row("user-settings", ALREADY_DONE,
                "%s carries every composed deny rule" % path)


def check_front_door(conf, fd):
    """The front door's allowlist line, read as the role account (KIT-197). `fd` is
    `front_door_digest`'s answer. The Slack path and the tracker path on the one line is
    done. A path that forwards the config-update route or the tool server, or a wildcard
    that may, is drift (`widening_reason`); any other path that is not one of the
    dispatcher's own routes is named, not judged."""
    unset = [k for k in FRONT_DOOR_READ_KEYS if not conf.get(k)]
    if unset:
        return _row("front-door", UNKNOWN,
                    "NOT MEASURED: set %s in chat-lane.conf, and this row reads the front "
                    "door's path allowlist as the role account" % " and ".join(unset))
    path, matcher = conf["FRONT_DOOR_CONFIG"], conf["FRONT_DOOR_MATCHER"]
    if fd is None:
        return _row("front-door", UNKNOWN, "the read-only probe did not read %s" % path)
    err = fd.get("error")
    if err == "missing":
        return _row("front-door", BLOCKED, "no file at %s (FRONT_DOOR_CONFIG)" % path)
    if err:
        return _row("front-door", UNKNOWN, "%s is %s" % (path, err))
    count = fd.get("count")
    if count != 1:
        return _row("front-door", BLOCKED,
                    "%s has %s `%s path …` lines, and the allowlist is exactly one: found %s"
                    % (path, count, matcher, count),
                    ["line %d" % n for n in fd.get("lines") or []])
    paths = fd.get("paths") or []
    problems = []
    if SLACK_PATH not in paths:
        problems.append("%s is not on line %s of %s, so Slack's requests never reach the "
                        "dispatcher: run front-door --apply, then restart the door (card "
                        "CK-C2)" % (SLACK_PATH, fd.get("line"), path))
    if TRACKER_PATH not in paths:
        problems.append("the tracker path %s is not on line %s of %s, so no tracker webhook "
                        "reaches the dispatcher and no ticket starts a session"
                        % (TRACKER_PATH, fd.get("line"), path))
    for p in paths:
        why = widening_reason(p)
        if why:
            problems.append("line %s of %s forwards %s, %s. Take it off the line, restart "
                            "the door (card CK-C2), and find out what added it"
                            % (fd.get("line"), path, p, why))
    notes = ["note: the line also forwards %s, which is not one of the dispatcher's own "
             "routes (%s). It widens the door; find out what added it" % (p, ", ".join(
                 sorted(EXPECTED_ROUTES)))
             for p in paths if p not in EXPECTED_ROUTES and not widening_reason(p)]
    if problems:
        return _problem_row("front-door", BLOCKED, problems, notes)
    return _row("front-door", ALREADY_DONE,
                "line %s of %s carries %s and the tracker path %s. Card CK-C2's probes are "
                "the proof from outside" % (fd.get("line"), path, SLACK_PATH, TRACKER_PATH),
                notes)


CHECKS = ("grant", "coding-fence", "chat-mcp-configs", "dispatcher-env", "ip-validation-off",
          "notifier-token-absent", "hosted-keys-absent", "port-block", "user-settings",
          "front-door")


def evaluate(facts, conf, pf=None):
    """One row per CHECKS entry, in that order. `facts` is None when the role-account
    probe did not run; `pf` is None when the root reads were not made."""
    rows = []
    if facts is None:
        rows.extend(_row(c, UNKNOWN, "the read-only probe as the role account did not run")
                    for c in CHECKS if c != "port-block")
        env_facts = None
    else:
        cfg = facts.get("config") or {"error": "missing"}
        env_facts = facts.get("env") or {"error": "missing"}
        us = facts.get("userSettings") or {"error": "missing"}
        if cfg.get("error"):
            rows.extend(_config_unmeasured(cfg, ("grant", "coding-fence", "chat-mcp-configs")))
        else:
            rows.append(check_grant(cfg))
            rows.append(check_fence(cfg, env_facts))
            rows.append(check_chat_mcp(cfg))
        rows.append(check_env(conf, env_facts))
        rows.append(check_ip_validation_off(env_facts))
        rows.append(check_notifier_absent(conf, env_facts))
        rows.append(check_hosted_keys_absent(env_facts))
        rows.append(check_user_settings(conf, us, env_facts))
        rows.append(check_front_door(conf, front_door_digest(facts.get("frontDoor"),
                                                             conf.get("FRONT_DOOR_MATCHER"))))
    rows.append(check_port_block(conf, pf, env_facts) if pf is not None else
                _row("port-block", UNKNOWN, "the root reads of pf were not made"))
    order = {c: i for i, c in enumerate(CHECKS)}
    return sorted(rows, key=lambda r: order.get(r["check"], len(CHECKS)))


_SEVERITY = (FAILED, UNKNOWN, BLOCKED, ALREADY_DONE)
_VERIFY_EXIT = {FAILED: EX_FAILED, UNKNOWN: EX_UNKNOWN, BLOCKED: EX_BLOCKED,
                ALREADY_DONE: EX_OK}


def worst_exit(rows):
    for outcome in _SEVERITY:
        if any(r["outcome"] == outcome for r in rows):
            return _VERIFY_EXIT[outcome]
    return EX_OK


_REFUSAL_WHAT = {
    "verify": "reads the dispatcher's config and env file as the role account",
    "merge": "reads the dispatcher's config as the role account and writes it",
    "env-names": "writes the chat app's secrets into the dispatcher's env file",
    "front-door": "reads and writes the front door's config as the role account",
}


def refusal_text(found, command="verify"):
    return ("REFUSED: `%s` %s,\n"
            "  and this is an agent environment (%s set). The config holds the tracker's\n"
            "  tokens and the env file holds the chat bot's. Nothing here prints a value,\n"
            "  but a session has no business running it. There is no override flag.\n"
            "  A PERSON runs this, in a terminal:  python3 %s %s\n"
            "  Read-only meanwhile:  compose | card <CK-id>"
            % (command, _REFUSAL_WHAT.get(command, _REFUSAL_WHAT["verify"]),
               ", ".join(found), _self_path(), command))


def probe_facts(runner, conf):
    """(facts, why-not). The read-only probe, as the role account."""
    res = runner.as_role(conf["ROLE_ACCOUNT"], facts_command(conf))
    if not res.ok:
        return None, "exit %d: %s" % (res.rc, (res.err or "").strip()[:160])
    try:
        return json.loads(res.out), None
    except ValueError:
        return None, "its output was not JSON"


def print_rows(rows):
    for r in rows:
        head = "  %-22s %-17s " % (r["check"], r["outcome"])
        wrapped = _wrap(r["detail"], 104 - len(head)) or [""]
        say(head + wrapped[0])
        for more in wrapped[1:]:
            say(" " * len(head) + more)
        for line in r["lines"]:
            if line.startswith("  paste"):
                say("      " + line.strip())     # never wrapped: it is pasted as JSON
            else:
                para(line, "      ", 104)


def cmd_verify(conf, runner, sudo):
    found = agent_env_markers_present()
    if found:
        say(refusal_text(found))
        return EX_REFUSED
    runner.dry_run = True
    account = conf["ROLE_ACCOUNT"]
    say("Chat lane verify — read-only. It reads the dispatcher config, its env file, the")
    say("user settings and the front door's config as %s, through one program that prints"
        % account)
    say("names, tool lists and allowlist paths and never a value, and asks pf, as root, what")
    say("it has loaded. Your login password may be asked for, once.")
    sudo.acquire("`verify` reads four files as the %s role account and asks pf, as root, "
                 "which rules it holds. It changes nothing." % account, "verify")
    facts, why = probe_facts(runner, conf)
    rows = evaluate(facts, conf, pf_probe(runner))
    if facts is None:
        for r in rows:
            if r["outcome"] == UNKNOWN and r["check"] != "port-block":
                r["detail"] = "the read-only probe as %s did not run (%s)" % (account, why)
    say("")
    say("-- checks --")
    print_rows(rows)
    if runner.writes:
        say("")
        say("BUG: verify recorded %d write(s); that is a defect in this file."
            % len(runner.writes))
        return EX_FAILED
    code = worst_exit(rows)
    say("")
    say("No drift: every check measures as applied." if code == EX_OK else
        "Not clean (exit %d). Apply what the rows name, then run verify again." % code)
    say("Not measured from here, so they are cards: the Slack app (CK-C1), the front door's "
        "answers from outside (CK-C2), the live session (CK-C3) and the port from a second "
        "device (CK-C4).")
    return code


# --------------------------------------------------------------------------- #
# The writers — owner decision of 2026-09-24 (KIT-197).
#
# Three live-file edits this file used to only describe were done on a deployment by three
# small scripts, tested on fixtures and then run for real. These are those scripts'
# behaviour, owned here. Each is a PROGRAM handed to the role account's /bin/sh through
# `_role_write`, the one place in this file that asks the runner to write:
#
#   * every value goes on STANDARD INPUT — a secret never reaches an argument, so `ps`
#     never shows it, and the Runner records it as "<hidden>";
#   * paths are arguments, never spliced into the program;
#   * the merge and front-door programs re-read the file and refuse (exit 3, nothing
#     written) when it is not the file the plan read, as they start and again just before
#     the write — the dispatcher rewrites its own config when it refreshes a tracker
#     token. The env program has no separate plan to compare with: it reads and
#     replaces the env file in one pass, and the dispatcher never rewrites that file;
#   * it writes nothing, and backs nothing up, when there is nothing to change;
#   * it backs the file up first, under the role account's home, at mode 600, with a name
#     the Stage E installer's backup pruning never matches;
#   * it prints names, lengths, paths and tool lists, never a value.
#
# `--selftest` RUNS each program through a real /bin/sh against temporary files; a shell
# fragment nobody has executed is a guess about a shell (the Stage E installer, 15b). The
# exact text of the three programs, and of `_role_write`, is all the write scan exempts.
# --------------------------------------------------------------------------- #
BACKUP_TAG = "pre-chat-lane"

# Pieces 1, 2 and 5. Exit 0 wrote (or had nothing to write), 1 failed (a copy taken first
# is put back, unless something else wrote the file too; or the settings file has a shape
# it cannot extend), 2 a malformed patch, 3 refused: the file is not the one the plan read,
# checked as it starts and again just before the config is written.
MERGE_WRITER_PY = r'''
import hashlib, json, os, sys, time

def say(msg):
    print(msg)
    sys.stdout.flush()

def stop(code, msg):
    say(("REFUSED: " if code == 3 else "FAILED: ") + msg)
    sys.exit(code)

def indent_of(text):
    for line in text.split("\n")[1:]:
        body = line.lstrip(" \t")
        if body and len(body) < len(line):
            return "\t" if line.startswith("\t") else len(line) - len(body)
    return 2

def render(doc, like):
    if like is None:
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    text = json.dumps(doc, indent=indent_of(like), ensure_ascii=False)
    return text + "\n" if like.endswith("\n") else text

def read(path):
    with open(path, "rb") as fh:
        return fh.read()

def loads(raw):
    try:
        return json.loads(raw.decode("utf-8")), True
    except ValueError:
        return None, False

def backup_path(folder, name):
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    path = os.path.join(folder, "%s.%s" % (name, time.strftime("%Y-%m-%d-%H%M%S")))
    return path + ".%d" % os.getpid() if os.path.exists(path) else path

def private_copy(raw, path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(path, 0o600)

def in_place(path, data):
    # The SAME file, not a rename: the dispatcher watches it and reloads on a change.
    with open(path, "r+b") as fh:
        fh.seek(0)
        fh.write(data)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())

def entry_at(cfg, op):
    repos = cfg.get("repositories")
    i = op.get("index")
    if (not isinstance(repos, list) or type(i) is not int or not 0 <= i < len(repos)
            or not isinstance(repos[i], dict)):
        stop(3, "entry #%s is not where the plan found it. Nothing was written. Run merge "
                "again." % i)
    r = repos[i]
    if (str(r.get("id") or "")[:80] != op.get("id")
            or str(r.get("name") or "")[:80] != op.get("name")):
        stop(3, "entry #%d is not the entry the plan named (%s). Nothing was written. Run "
                "merge again." % (i, op.get("id") or op.get("name")))
    return r

def plus(lst, add):
    return lst + [x for x in add if x not in lst]

def apply_op(cfg, op):
    kind, add = op.get("op"), list(op.get("add") or [])
    if kind == "fence-entry":
        r = entry_at(cfg, op)
        own, default, src = r.get("disallowedTools"), cfg.get("defaultDisallowedTools"), op.get("from")
        if src == "own" and isinstance(own, list):
            r["disallowedTools"] = plus(own, add)
        elif src == "default" and own is None and isinstance(default, list):
            r["disallowedTools"] = plus(list(default), add)
        elif src == "none" and own is None and default is None:
            r["disallowedTools"] = add
        else:
            stop(3, "entry #%d's disallowedTools is not what the plan read. Nothing was "
                    "written. Run merge again." % op.get("index"))
    elif kind == "fence-prompt":
        if op.get("scope") == "labelPrompts":
            holder = entry_at(cfg, op).get("labelPrompts")
        elif op.get("scope") == "promptDefaults":
            holder = cfg.get("promptDefaults")
        else:
            stop(2, "the patch names an unknown scope. Nothing was written.")
        v = holder.get(op.get("type")) if isinstance(holder, dict) else None
        if not isinstance(v, dict) or not isinstance(v.get("disallowedTools"), list):
            stop(3, "%s.%s.disallowedTools is not what the plan read. Nothing was written. "
                    "Run merge again." % (op.get("scope"), op.get("type")))
        v["disallowedTools"] = plus(v["disallowedTools"], add)
    elif kind == "grant":
        grant = op.get("set")
        if not isinstance(grant, list) or not all(isinstance(t, str) for t in grant):
            stop(2, "the patch's grant is not a list of tool names. Nothing was written.")
        cfg["slackAllowedTools"] = grant
    else:
        stop(2, "the patch names an unknown operation. Nothing was written.")

def main():
    path = sys.argv[1]
    try:
        patch = json.loads(sys.stdin.read())
    except ValueError:
        patch = None
    if not isinstance(patch, dict):
        stop(2, "the patch on standard input is not a JSON object. Nothing was written.")
    ops, deny_add = patch.get("config") or [], patch.get("deny") or []
    home = os.path.expanduser("~")
    backups = os.path.join(home, ".stage-e", "backups")
    s_path = os.path.join(home, ".claude", "settings.json")

    # 1. Every check, before anything is written.
    raw = new_cfg = None
    if ops:
        raw = read(path)
        if hashlib.sha256(raw).hexdigest() != patch.get("configSha256"):
            stop(3, "%s changed since it was read: the dispatcher rewrites it when it "
                    "refreshes a tracker token. Nothing was written. Run merge again." % path)
        cfg, ok = loads(raw)
        if not ok or not isinstance(cfg, dict):
            stop(1, "%s is not a JSON object. Nothing was written." % path)
        for op in ops:
            apply_op(cfg, op)
        new_cfg = render(cfg, raw.decode("utf-8")).encode("utf-8")
    s_raw = new_settings = None
    if deny_add:
        # The settings checksum is not optional: a patch without it would switch the check
        # off (review of KIT-197, finding 49). None means "there was no file".
        if "settingsSha256" not in patch:
            stop(2, "the patch adds deny rules and carries no settings checksum. Nothing was "
                    "written.")
        exists = os.path.exists(s_path)
        s_raw = read(s_path) if exists else None
        now = hashlib.sha256(s_raw).hexdigest() if exists else None
        if now != patch["settingsSha256"]:
            stop(3, "%s changed since it was read. Nothing was written. Run merge "
                    "again." % s_path)
        settings, ok = loads(s_raw) if exists else ({}, True)
        perms = settings.setdefault("permissions", {}) if ok and isinstance(settings, dict) else None
        deny = perms.setdefault("deny", []) if isinstance(perms, dict) else None
        if not isinstance(deny, list):
            stop(1, "%s is not a JSON object with a permissions.deny list. Nothing was "
                    "written: merge piece 5 into it by hand." % s_path)
        deny.extend([x for x in deny_add if x not in deny])
        new_settings = render(settings, s_raw.decode("utf-8") if exists else None).encode("utf-8")

    # 2. The dispatcher config, in place, after a private copy. The checksum is taken
    # again after the copy, just before the write: the dispatcher does not wait while the
    # copy is made (review of KIT-197, finding 37). A window of one read remains; the
    # dispatcher honours no lock.
    if new_cfg is not None:
        bk = backup_path(backups, "dispatcher-config.pre-chat-lane")
        private_copy(raw, bk)
        say("BACKUP      %s (it holds the tracker's tokens; mode 600)" % bk)
        if read(path) != raw:
            stop(3, "%s changed while it was being backed up: the dispatcher rewrites it "
                    "when it refreshes a tracker token. Nothing was written. Run merge "
                    "again." % path)
        in_place(path, new_cfg)
        _doc, ok = loads(read(path))
        if not ok:
            if read(path) != new_cfg:
                stop(1, "%s does not parse, and it no longer holds what this wrote: "
                        "something else wrote it too, so it was not overwritten. Compare it "
                        "with the copy taken first, %s, and restore by hand." % (path, bk))
            in_place(path, raw)
            stop(1, "%s did not parse after the write, so the copy taken first was put "
                    "back." % path)
        say("WROTE       %s in place (the same file, so the dispatcher reloads it), and it "
            "parses" % path)

    # 3. The user settings: a private temporary file, then a rename, at mode 600.
    if new_settings is not None:
        folder = os.path.dirname(s_path)
        if not os.path.isdir(folder):
            os.makedirs(folder, mode=0o700)
            os.chmod(folder, 0o700)
        if s_raw is not None:
            bk = backup_path(backups, "user-settings.json")
            private_copy(s_raw, bk)
            say("BACKUP      %s" % bk)
        tmp = "%s.chat-lane.%d" % (s_path, os.getpid())
        private_copy(new_settings, tmp)
        os.replace(tmp, s_path)
        _doc, ok = loads(read(s_path))
        if not ok:
            stop(1, "%s did not parse after the write." % s_path)
        say("WROTE       %s, mode 600, and it parses" % s_path)
    if new_cfg is None and new_settings is None:
        say("Nothing to write: the patch changes nothing.")
    sys.exit(0)

try:
    main()
except OSError as exc:
    stop(1, "%s: %s. Nothing more was written." % (exc.strerror, exc.filename))
'''

# Piece 3. Arguments: set|remove, the dispatcher's env file, the role account's own env
# file (`~/` = that account's home), and the notifier token's NAME. `set` reads the token
# and the signing secret from standard input, one per line. Exit 0 wrote; 9 had nothing to
# change (nothing written, nothing backed up); 4 refused the file (missing, a symbolic
# link, or another account's); 5 could not (the original untouched); 6 refused the
# notifier's token; 7 nothing on standard input; 8 could not compare the token with the
# notifier's (the role account's env file is absent or unreadable); 2 a bad argument.
# Portable to both stats: GNU's `-c` is tried first, BSD's `-f` after.
#
# THE NOTIFIER'S TOKEN (review of KIT-197, 30/43/48). The offered token is compared with
# EVERY value in the role account's env file, not only the one under NOTIFIER_TOKEN_ENV:
# a notifier conf that names its token differently, or a stale line above the live one
# (a shell that sources the file keeps the last), would otherwise pass. A file that cannot
# be read is exit 8, never a pass; a file with no line for the name says so. The secrets,
# and every line and value read from that file, meet only shell builtins: `ps` never
# shows a value (the selftest scans for it).
ENV_WRITER_SH = r'''
cd / || exit 5
mode=$1; f=$2; renv=$3; nname=$4
case $mode in set|remove) ;; *) echo "FAILED: unknown mode. Nothing was changed."; exit 2;; esac
case $nname in ''|*[!A-Z0-9_]*) echo "FAILED: bad notifier name. Nothing was changed."; exit 2;; esac
case $renv in "~/"*) renv="$HOME/${renv#??}";; esac
names='SLACK_BOT_TOKEN|SLACK_SIGNING_SECRET|CYRUS_HOST_EXTERNAL|WEBHOOK_IP_VALIDATION'
pat="^[[:space:]]*(export[[:space:]]+)?($names)[[:space:]]*(=|:[[:space:]])"
[ -L "$f" ] && { echo "REFUSED: $f is a symbolic link. Replacing it would leave a plain file at the link's own mode where the link was. Point DISPATCHER_ENV_FILE at the file itself. Nothing was changed."; exit 4; }
[ -f "$f" ] || { echo "REFUSED: $f does not exist. Nothing was changed."; exit 4; }
me=$(id -un)
owner=$(stat -c %U "$f" 2>/dev/null || stat -f %Su "$f" 2>/dev/null)
[ "$owner" = "$me" ] || { echo "REFUSED: $f is owned by ${owner:-an unknown account}, not $me. Nothing was changed."; exit 4; }
if [ "$mode" = set ]; then
    IFS= read -r T || T=
    IFS= read -r S || S=
    [ -n "$T" ] && [ -n "$S" ] || { T=; S=; echo "FAILED: two values did not arrive on standard input. Nothing was changed."; exit 7; }
    if [ ! -e "$renv" ]; then why="does not exist"
    elif [ ! -f "$renv" ] || [ ! -r "$renv" ]; then why="cannot be read"
    else why=; fi
    if [ -n "$why" ]; then
        T=; S=
        echo "NOT CHECKED: $renv $why, so the token could not be compared with the notifier's. Point ROLE_ENV_FILE in chat-lane.conf at the file the notifier keeps its token in (notifier.conf's ENV_FILE). Nothing was changed."
        exit 8
    fi
    hit=; seen=
    while IFS= read -r l || [ -n "$l" ]; do
        l=${l#"${l%%[![:space:]]*}"}
        case $l in export[[:space:]]*) l=${l#export}; l=${l#"${l%%[![:space:]]*}"};; esac
        case $l in *=*) ;; *) continue;; esac
        k=${l%%=*}; k=${k%"${k##*[![:space:]]}"}
        case $k in ''|*[!A-Za-z0-9_]*) continue;; esac
        v=${l#*=}; v=${v#"${v%%[![:space:]]*}"}
        case $v in
            \"*) v=${v#\"}; v=${v%%\"*};;
            \'*) v=${v#\'}; v=${v%%\'*};;
            *) v=${v%%[[:space:]#]*};;
        esac
        [ "$k" = "$nname" ] && seen=1
        [ -n "$v" ] && [ "$v" = "$T" ] && hit=$k
    done < "$renv"
    l=; v=
    if [ -n "$hit" ]; then
        T=; S=
        if [ "$hit" = "$nname" ]; then
            echo "REFUSED: that token is the NOTIFIER's ($nname in the role account's env file). The chat lane needs the chat app's own token: two apps, two tokens. Nothing was changed."
        else
            echo "REFUSED: that token is already in the role account's env file, as $hit, so it is not the chat app's own: two apps, two tokens. If $hit is the notifier's token under another name, set NOTIFIER_TOKEN_ENV=$hit in chat-lane.conf. Nothing was changed."
        fi
        exit 6
    fi
    if [ -n "$seen" ]; then
        echo "CHECKED     the token is not the notifier's: it matches no value in $renv, $nname among them."
    else
        echo "NOTE        $renv has no $nname line, so the notifier may keep its token elsewhere. The token was compared with every value in that file and matched none. Make ROLE_ENV_FILE and NOTIFIER_TOKEN_ENV match notifier.conf's ENV_FILE and CHAT_TOKEN_ENV."
    fi
fi
umask 077
t="$f.chat-lane.$$"
grep -v -E "$pat" "$f" > "$t"
[ $? -le 1 ] || { T=; S=; rm -f "$t"; echo "FAILED: could not read $f. The original is untouched."; exit 5; }
if [ "$mode" = set ]; then
    printf 'SLACK_BOT_TOKEN=%s\nSLACK_SIGNING_SECRET=%s\nCYRUS_HOST_EXTERNAL=true\nWEBHOOK_IP_VALIDATION=false\n' "$T" "$S" >> "$t" || { T=; S=; rm -f "$t"; echo "FAILED: could not write the new copy. The original is untouched."; exit 5; }
fi
T=; S=
if cmp -s "$t" "$f"; then
    rm -f "$t"
    if [ "$mode" = set ]; then
        echo "UNCHANGED $f already ends with these four lines. Nothing was written, and nothing was backed up."
    else
        echo "UNCHANGED $f names none of the four. Nothing was written, and nothing was backed up."
    fi
    exit 9
fi
d="$HOME/.stage-e/backups"
mkdir -p "$d" && chmod 700 "$d" || { rm -f "$t"; echo "FAILED: could not make $d. Nothing was changed."; exit 5; }
b="$d/dispatcher-env.pre-chat-lane.$(date +%Y-%m-%d-%H%M%S)"
[ -e "$b" ] && b="$b.$$"
cp -p "$f" "$b" && chmod 600 "$b" || { rm -f "$t"; echo "FAILED: could not back up $f. Nothing was changed."; exit 5; }
m=$(stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f" 2>/dev/null)
chmod "$m" "$t" && mv -f "$t" "$f" || { rm -f "$t"; echo "FAILED: could not replace $f. The original is untouched."; exit 5; }
echo "WROTE $f (mode $m, owner $me): $(grep -c . "$b") non-empty line(s) before, $(grep -c . "$f") after. Every other line kept, in order."
if [ "$mode" = set ]; then
    awk -F= '/^(SLACK_BOT_TOKEN|SLACK_SIGNING_SECRET|CYRUS_HOST_EXTERNAL|WEBHOOK_IP_VALIDATION)=/ {print "  " $1, length($0) - length($1) - 1}' "$f"
else
    echo "  lines naming any of the four left in it: $(grep -c -E "$pat" "$f")"
fi
echo "BACKUP $b"
echo "  It holds the whole env file as it was, the dispatcher's other secrets included. Delete it once verify is clean:"
echo "      sudo -u $me rm -f '$b'"
dir=$(dirname "$f")
for s in "$dir"/.*.sw? "$dir"/*.sw?; do
    [ -e "$s" ] && echo "WARNING: a vi swap file is still beside the env file: $s. It can hold the secrets. Quit every vi on that file, then delete it."
done
exit 0
'''

# Piece 7. Arguments: the front door's config and its binary. The patch names the line
# (index, old text, new text) and the file's checksum. Exit 0 wrote and validated, 3
# refused (not the file or not the line the plan read), 4 validation failed and the
# backup was put back byte for byte, 1 failed, 2 a malformed patch.
#
# `--adapter caddyfile`: the matcher line is Caddyfile syntax, and Caddy picks that adapter
# by itself only for a file whose name starts "Caddyfile"; a front door built with another
# file name would otherwise fail validation every time.
FRONT_WRITER_PY = r'''
import hashlib, json, os, subprocess, sys, time

def say(msg):
    print(msg)
    sys.stdout.flush()

def stop(code, msg):
    say(("REFUSED: " if code == 3 else "FAILED: ") + msg)
    sys.exit(code)

def read(path):
    with open(path, "rb") as fh:
        return fh.read()

def backup_path(folder, name):
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    path = os.path.join(folder, "%s.%s" % (name, time.strftime("%Y-%m-%d-%H%M%S")))
    return path + ".%d" % os.getpid() if os.path.exists(path) else path

def private_copy(raw, path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(path, 0o600)

def in_place(path, data):
    with open(path, "r+b") as fh:
        fh.seek(0)
        fh.write(data)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())

def main():
    path, binary = sys.argv[1], sys.argv[2]
    try:
        patch = json.loads(sys.stdin.read())
    except ValueError:
        patch = None
    if not isinstance(patch, dict) or not isinstance(patch.get("new"), str):
        stop(2, "the patch on standard input is not what this program takes. Nothing was "
                "written.")
    raw = read(path)
    if hashlib.sha256(raw).hexdigest() != patch.get("sha256"):
        stop(3, "%s changed since it was read. Nothing was written. Run front-door again."
             % path)
    lines = raw.split(b"\n")
    i = patch.get("index")
    if (type(i) is not int or not 0 <= i < len(lines)
            or lines[i] != str(patch.get("old")).encode("utf-8")):
        stop(3, "that line of %s is not the line the plan read. Nothing was written." % path)
    lines[i] = patch["new"].encode("utf-8")
    bk = backup_path(os.path.join(os.path.expanduser("~"), ".stage-e", "backups"),
                     os.path.basename(path) + ".pre-chat-lane")
    private_copy(raw, bk)
    say("BACKUP    %s" % bk)
    if read(path) != raw:
        stop(3, "%s changed while it was being backed up. Nothing was written. Run "
                "front-door again." % path)
    in_place(path, b"\n".join(lines))
    say("WROTE     %s in place: line %d, and nothing else" % (path, i + 1))
    try:
        ran = subprocess.run([binary, "validate", "--config", path, "--adapter", "caddyfile"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=120)
        rc, out = ran.returncode, ran.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        rc, out = 127, "%s could not be run (%s)" % (binary, type(exc).__name__)
    tail = [l for l in out.splitlines() if l.strip()][-3:]
    if rc != 0:
        in_place(path, raw)
        if read(path) == raw:
            said = "the backup was put back byte for byte. The front door still runs the old file"
        else:
            said = "THE RESTORE DID NOT READ BACK: copy %s over %s by hand" % (bk, path)
        say("VALIDATE FAILED (exit %d), so %s." % (rc, said))
        for l in tail:
            say("    " + l[:300])
        sys.exit(4)
    say("VALIDATE  ok: %s" % (tail[-1][:300] if tail else "exit 0"))
    sys.exit(0)

try:
    main()
except OSError as exc:
    stop(1, "%s: %s. Nothing more was written." % (exc.strerror, exc.filename))
'''

# The programs the write scan exempts, by exact text.
WRITER_PROGRAMS = (MERGE_WRITER_PY, ENV_WRITER_SH, FRONT_WRITER_PY)


def merge_writer_command(conf):
    return "/usr/bin/python3 -c %s %s" % (shlex.quote(MERGE_WRITER_PY),
                                          shlex.quote(conf["DISPATCHER_CONFIG"]))


def env_writer_command(conf, mode):
    return "/bin/sh -c %s env-names %s %s %s %s" % (
        shlex.quote(ENV_WRITER_SH), shlex.quote(mode), shlex.quote(conf["DISPATCHER_ENV_FILE"]),
        shlex.quote(conf["ROLE_ENV_FILE"]), shlex.quote(conf["NOTIFIER_TOKEN_ENV"]))


def front_door_writer_command(conf):
    return "/usr/bin/python3 -c %s %s %s" % (shlex.quote(FRONT_WRITER_PY),
                                             shlex.quote(conf["FRONT_DOOR_CONFIG"]),
                                             shlex.quote(conf["FRONT_DOOR_BIN"]))


def _role_write(runner, account, script, stdin, what, secret=False):
    """THE ONE WRITE SEAM. Every write this file makes is one of the three writer programs,
    run as the role account, reached only from `merge --apply`, `env-names` and
    `front-door --apply` — subcommands a person runs, each of which has already refused an
    agent environment. `--selftest` checks nothing else in the file asks for a write."""
    runner.dry_run = False
    return runner.as_role(account, script, stdin=stdin, why=what, secret_stdin=secret)


def _relay(res):
    """The writer's own lines. They carry names, lengths and paths, never a value."""
    for line in (res.out or "").splitlines():
        say("  " + line)
    for line in (res.err or "").strip().splitlines()[-5:]:
        say("  (stderr) " + line[:300])


def _resume(command, apply_it=False, remove=False):
    """The command line a declined sudo tells a person to run again."""
    return command + (" --remove" if remove else "") + (" --apply" if apply_it else "")


def _plan_line(line):
    """A plan line: a 12-character tag, then text wrapped under itself."""
    tag, rest = line[:12], line[12:]
    wrapped = _wrap(rest, 90) or [""]
    say("  " + tag + wrapped[0])
    for more in wrapped[1:]:
        say(" " * 14 + more)


# --------------------------------------------------------------------------- #
# merge — pieces 1, 2 and 5.
# --------------------------------------------------------------------------- #
def merge_plan(conf, facts):
    """(lines, config operations, deny rules to add), from the probe's facts, with the SAME
    functions `verify` uses: owner_grant, fence_entry, prompt_type_gaps, user_deny_patterns
    and entry_kind. It reads nothing itself, and its lines hold no value."""
    cfg = facts.get("config") or {}
    env_facts = facts.get("env") or {}
    us = facts.get("userSettings") or {}
    lines, ops = [], []
    default = cfg.get("defaultDisallowedTools")
    default_ok = default is None or isinstance(default, list)
    env_names = set(env_facts.get("names") or []) | set(env_facts.get("empty") or [])
    fenced = 0
    for e in cfg.get("entries") or []:
        label, kind, own = _entry_label(e), entry_kind(e), e.get("disallowedTools")
        ident = {"index": e.get("index"), "id": e.get("id") or "", "name": e.get("name") or ""}
        if kind == "review":
            # Judged exactly as verify's coding-fence row judges it: an entry with no list
            # of its own inherits defaultDisallowedTools.
            _c, missing, _s = fence_entry(own if own != "not-a-list" else [],
                                          default if default_ok else None)
            if missing or own == "not-a-list":
                lines.append("LEFT ALONE  review entry %s lacks %s. It is the Stage E "
                             "installer's: run the Stage E installer, whose entries these are"
                             % (label, ", ".join(missing or SLACK_FENCE_RULES)))
            else:
                lines.append("ok          review entry %s already denies both Slack rules"
                             % label)
            continue
        fenced += 1
        if own == "not-a-list" or (own is None and not default_ok):
            lines.append("CANNOT      %s entry %s: %s is not a list. Fix it by hand; nothing "
                         "is composed for it" % (kind, label, "its disallowedTools"
                                                 if own == "not-a-list"
                                                 else "defaultDisallowedTools"))
        elif own is None and "DISALLOWED_TOOLS" in env_names:
            # The env list replaces defaultDisallowedTools at start (WorkerService.js:164-165),
            # and this reads no env value. A list composed from the file would drop every
            # deny that env list supplies, and verify would then pass (review of KIT-197,
            # finding 29).
            lines.append("CANNOT      %s entry %s inherits its list, and the dispatcher env "
                         "file sets DISALLOWED_TOOLS, which replaces defaultDisallowedTools at "
                         "start (WorkerService.js:164-165). Nothing is composed for it: give "
                         "it its own list by hand, the value of DISALLOWED_TOOLS plus both "
                         "Slack rules" % (kind, label))
        else:
            composed, missing, source = fence_entry(own, default)
            if missing:
                op = dict(ident)
                op.update({"op": "fence-entry", "add": missing,
                           "from": "own" if own is not None else
                                   "default" if default is not None else "none"})
                ops.append(op)
                lines.append("FENCE       %s entry %s: disallowedTools = %s (was: %s)"
                             % (kind, label, json.dumps(composed), source))
            else:
                lines.append("ok          %s entry %s already denies both Slack rules"
                             % (kind, label))
        prompts = e.get("labelPrompts") or {}
        for ptype in prompt_type_gaps(prompts):
            if not isinstance(prompts.get(ptype), list):
                lines.append("CANNOT      %s labelPrompts.%s.disallowedTools is not a list. Fix "
                             "it by hand" % (label, ptype))
                continue
            add = [r for r in SLACK_FENCE_RULES if r not in prompts[ptype]]
            op = dict(ident)
            op.update({"op": "fence-prompt", "scope": "labelPrompts", "type": ptype,
                       "add": add})
            ops.append(op)
            lines.append("FENCE       %s labelPrompts.%s.disallowedTools += %s"
                         % (label, ptype, json.dumps(add)))
    if fenced:
        defaults = cfg.get("promptDefaults") or {}
        for ptype in prompt_type_gaps(defaults):
            if not isinstance(defaults.get(ptype), list):
                lines.append("CANNOT      promptDefaults.%s.disallowedTools is not a list. Fix "
                             "it by hand" % ptype)
                continue
            add = [r for r in SLACK_FENCE_RULES if r not in defaults[ptype]]
            ops.append({"op": "fence-prompt", "scope": "promptDefaults", "type": ptype,
                        "add": add})
            lines.append("FENCE       promptDefaults.%s.disallowedTools += %s"
                         % (ptype, json.dumps(add)))

    # Piece 1: the approved list, with a pull-rule pair per ACTIVE repository path.
    active = active_entries(cfg)
    paths = []
    for e in active:
        if e.get("repositoryPath") and e["repositoryPath"] not in paths:
            paths.append(e["repositoryPath"])
    wanted = owner_grant(paths)
    live = cfg.get("slackAllowedTools")
    unsafe = grant_problems(wanted)
    if unsafe:
        lines.append("CANNOT      the grant composed from the live repository paths is unsafe: "
                     "%s. Nothing is composed for slackAllowedTools" % "; ".join(unsafe))
    elif isinstance(live, list) and sorted(live) == sorted(wanted):
        lines.append("ok          slackAllowedTools is already the approved list")
    else:
        extra = [t for t in live if t not in wanted] if isinstance(live, list) else []
        if extra:
            lines.append("DROP        slackAllowedTools holds what the owner did not approve: %s"
                         % ", ".join(extra))
        ops.append({"op": "grant", "set": wanted})
        lines.append("GRANT       slackAllowedTools = %s" % json.dumps(wanted))
    if active and active[0].get("mcpAllowedTools"):
        lines.append("WARNING     the first active entry %s lists %s in allowedTools, and the "
                     "dispatcher adds those to the chat grant (RunnerConfigBuilder.js:63-66). "
                     "Not changed here: remove them by hand"
                     % (_entry_label(active[0]), ", ".join(active[0]["mcpAllowedTools"])))

    # Piece 5: the read denies, merged into the role account's user settings.
    names = set(env_facts.get("names") or []) | set(env_facts.get("empty") or [])
    s_path = us.get("path") or "~/.claude/settings.json"
    deny_add = []
    if "CLAUDE_CONFIG_DIR" in names:
        lines.append("NOT MERGED  the dispatcher env file sets CLAUDE_CONFIG_DIR, so sessions "
                     "read user settings from that directory, not %s. Merge piece 5 there by "
                     "hand" % s_path)
    elif us.get("error") and us.get("error") != "missing":
        lines.append("CANNOT      %s is %s: the read denies are not merged" % (s_path,
                                                                                us["error"]))
    else:
        have = us.get("deny") or []
        deny_add = [r for r in user_deny_patterns(conf) if r not in have]
        if deny_add:
            lines.append("DENY        %s%s: add %d rule(s): %s"
                         % (s_path, " (a new file, mode 600)" if us.get("error") else "",
                            len(deny_add), ", ".join(deny_add)))
        else:
            lines.append("ok          %s already carries every composed read deny" % s_path)
    return lines, ops, deny_add


def merge_rows(conf, facts):
    """The grant, coding-fence and user-settings rows, as `verify` prints them."""
    cfg = facts.get("config") or {"error": "missing"}
    env_facts = facts.get("env") or {"error": "missing"}
    us = facts.get("userSettings") or {"error": "missing"}
    rows = (_config_unmeasured(cfg, ("grant", "coding-fence")) if cfg.get("error") else
            [check_grant(cfg), check_fence(cfg, env_facts)])
    return rows + [check_user_settings(conf, us, env_facts)]


def cmd_merge(conf, runner, sudo, apply_it=False):
    found = agent_env_markers_present()
    if found:
        say(refusal_text(found, "merge"))
        return EX_REFUSED
    account = conf["ROLE_ACCOUNT"]
    say("Chat-lane merge — %s" % ("APPLY" if apply_it else "DRY RUN: nothing will be changed"))
    say("  Pieces 1, 2 and 5, planned from what verify's probe reads as %s. It prints entry"
        % account)
    say("  names, key names and tool lists, never a value.")
    sudo.acquire("`merge` reads the dispatcher config and the user settings as the %s role "
                 "account%s." % (account, ", and writes them as that account" if apply_it
                                 else ", and changes nothing"), _resume("merge", apply_it))
    facts, why_not = probe_facts(runner, conf)
    if facts is None:
        say("NOT MEASURED: the read-only probe as %s did not run (%s). Nothing was changed."
            % (account, why_not))
        return EX_UNKNOWN
    cfg = facts.get("config") or {"error": "missing"}
    if cfg.get("error"):
        broken = cfg["error"].startswith("unparseable")
        say("%s: the dispatcher config %s is %s. Nothing was changed."
            % ("FAILED" if broken else "NOT MEASURED", conf["DISPATCHER_CONFIG"], cfg["error"]))
        return EX_FAILED if broken else EX_UNKNOWN
    lines, ops, deny_add = merge_plan(conf, facts)
    us = facts.get("userSettings") or {}
    say("  config    %s" % conf["DISPATCHER_CONFIG"])
    say("  settings  %s" % (us.get("path") or "~/.claude/settings.json"))
    for line in lines:
        _plan_line(line)
    say("")
    if not ops and not deny_add:
        # "Nothing to do" and "could not do it" must not read alike (contract §13).
        held = [l for l in lines if l.startswith(("CANNOT", "NOT MERGED", "LEFT ALONE"))]
        say("Nothing this command can write: %d line(s) above name a piece it did not merge "
            "(CANNOT, NOT MERGED, LEFT ALONE), and the rows below say what is left." % len(held)
            if held else
            "Nothing to write: every piece this command merges is already in place.")
        rows = merge_rows(conf, facts)
        say("-- rows, as verify reads them --")
        print_rows(rows)
        return worst_exit(rows)
    if not apply_it:
        say("DRY RUN: nothing was changed. Run it again with --apply to write this, as %s."
            % account)
        return EX_BLOCKED
    patch = {"configSha256": cfg.get("sha256"), "config": ops,
             "settingsSha256": us.get("sha256"), "deny": deny_add}
    res = _role_write(runner, account, merge_writer_command(conf), json.dumps(patch),
                      "merge pieces 1, 2 and 5 into %s and the user settings, as %s"
                      % (conf["DISPATCHER_CONFIG"], account))
    _relay(res)
    if res.rc == 3:
        return EX_REFUSED
    if not res.ok:
        say("FAILED: the writer exited %d." % res.rc)
        return EX_FAILED
    again, why_not = probe_facts(runner, conf)
    if again is None:
        say("NOT MEASURED after the write: the probe as %s did not run (%s)." % (account,
                                                                                 why_not))
        return EX_UNKNOWN
    rows = merge_rows(conf, again)
    say("")
    say("-- rows, read again as verify reads them --")
    print_rows(rows)
    say("")
    para("The dispatcher reloads its config on a change (ConfigManager.js:51-62): its log "
         "says \"Config file changed, reloading...\". Nothing was restarted.", "")
    return worst_exit(rows)


# --------------------------------------------------------------------------- #
# env-names — piece 3.
# --------------------------------------------------------------------------- #
CHAT_TOKEN_PREFIX = "xoxb-"
CHAT_TOKEN_MIN_LEN = 40
_CHAT_TOKEN_RE = re.compile(r"^[A-Za-z0-9-]+$")
_SIGNING_SECRET_RE = re.compile(r"^[0-9a-f]{32}$")


def secret_shape_problem(token, secret):
    """Why these two values cannot be the chat app's bot token and signing secret, or None.
    Never quotes either value."""
    if not token.startswith(CHAT_TOKEN_PREFIX):
        return ("the token does not start %s: it may be the signing secret, or a user token."
                % CHAT_TOKEN_PREFIX)
    if len(token) < CHAT_TOKEN_MIN_LEN:
        return "the token is only %d characters: the paste was cut short." % len(token)
    if not _CHAT_TOKEN_RE.match(token):
        return ("the token holds a character a Slack bot token never does (a space, or a "
                "stray quote).")
    if len(secret) != 32:
        return "the signing secret is %d characters; Slack's is 32." % len(secret)
    if not _SIGNING_SECRET_RE.match(secret):
        return "the signing secret is not lower-case hex."
    return None


def _env_done(conf, res):
    """The env writer's exit, as this file's exit (contract §13: each nothing named). The
    writer's own line, relayed first, says which case it was; none of these adds a second
    cause to it."""
    _relay(res)
    if res.rc == 6:              # the notifier's token: refused, nothing changed
        return EX_REFUSED
    if res.rc == 4:              # not a file it writes: missing, a link, another account's
        return EX_BLOCKED
    if res.rc == 8:              # the notifier's token could not be compared: not a pass
        return EX_UNKNOWN
    if res.rc == 9:              # nothing to change: nothing written, nothing backed up
        para("Nothing changed, so this run needs no restart. If the dispatcher was not "
             "restarted since these names last changed, restart it now, only when it is "
             "idle: card CK-C5.", "")
        return EX_OK
    if not res.ok:
        say("FAILED: the role account's shell exited %d%s." % (res.rc, {
            5: ": the file could not be read or replaced, and the original is untouched",
            7: ": no value arrived on its standard input"}.get(res.rc, "")))
        return EX_FAILED
    say("")
    para("It restarted nothing. The listening address, the address checks and a removed "
         "name are read at start only (Application.js:54): restart the dispatcher now, only "
         "when it is idle — card CK-C5:", "")
    print_card("CK-C5", conf)
    return EX_OK


def _env_names_gate(conf, runner):
    """None when `env-names` may ask for the secrets; otherwise its exit code, after saying
    why. THE ORDER's two rules, each measured exactly as `verify` measures it: the fence
    before the token (the coding-fence row) and the port block before the listen (the
    port-block row, including CYRUS_SERVER_PORT against DISPATCHER_PORT). Review of KIT-197,
    findings 32, 35, 44 and 45: the gate used to read pf alone."""
    account, envfile = conf["ROLE_ACCOUNT"], conf["DISPATCHER_ENV_FILE"]
    facts, why_not = probe_facts(runner, conf)
    if facts is None:
        say("NOT MEASURED: the read-only probe as %s did not run (%s). Nothing was asked for "
            "or changed." % (account, why_not))
        return EX_UNKNOWN
    env_facts = facts.get("env") or {"error": "missing"}
    if env_facts.get("error"):
        missing = env_facts["error"] == "missing"
        say("%s: the dispatcher's env file %s is %s, so the port it listens on could not be "
            "compared with DISPATCHER_PORT. Nothing was asked for or changed."
            % ("REFUSED" if missing else "NOT MEASURED", envfile, env_facts["error"]))
        return EX_BLOCKED if missing else EX_UNKNOWN
    cfg = facts.get("config") or {"error": "missing"}
    rows = [_config_unmeasured(cfg, ("coding-fence",))[0] if cfg.get("error")
            else check_fence(cfg, env_facts),
            check_port_block(conf, pf_probe(runner), env_facts)]
    remedy = {
        "coding-fence": "The order is the fence before the token: once SLACK_BOT_TOKEN is in "
                        "the dispatcher's environment, every session it starts gets a working "
                        "Slack server, and an unfenced entry keeps it. Run  python3 %s merge  "
                        "and then  merge --apply  first." % _self_path(),
        "port-block": "The order is the port block before the listen, and the block must "
                      "refuse the port the dispatcher listens on. Fix what the row names "
                      "(piece 4:  python3 %s compose --piece 4 ), then run env-names again."
                      % _self_path(),
    }
    for row in rows:
        if row["outcome"] == ALREADY_DONE:
            continue
        unmeasured = row["outcome"] == UNKNOWN
        say("%s: the %s row does not measure as applied (%s): %s"
            % ("NOT MEASURED" if unmeasured else "REFUSED", row["check"], row["outcome"],
               row["detail"]))
        for line in row["lines"]:
            if line.startswith("  paste"):
                say("  " + line.strip())       # never wrapped: it is pasted as JSON
            else:
                para(line, "  ")
        para(("It could not be measured, so nothing is asked for until it is. Fix the read "
              "above, then run env-names again." if unmeasured else remedy[row["check"]])
             + " Nothing was asked for or changed.", "")
        return _VERIFY_EXIT[row["outcome"]]
    return None


def cmd_env_names(conf, runner, sudo, remove=False, tty=False, reader=None):
    found = agent_env_markers_present()
    if found:
        say(refusal_text(found, "env-names"))
        return EX_REFUSED
    account, envfile = conf["ROLE_ACCOUNT"], conf["DISPATCHER_ENV_FILE"]
    if remove:
        say("Chat-lane env-names --remove — the four names leave %s, as %s. Every other line "
            "stays." % (envfile, account))
        sudo.acquire("`env-names --remove` rewrites %s as the %s role account, keeping every "
                     "other line" % (envfile, account), _resume("env-names", remove=True))
        res = _role_write(runner, account, env_writer_command(conf, "remove"), "",
                          "remove the four chat-lane names from %s" % envfile)
        return _env_done(conf, res)
    if not tty:
        say("NEEDS A TERMINAL: env-names asks for the chat app's bot token and signing secret "
            "at two hidden prompts, and this run has no terminal to ask at. Nothing was read "
            "or changed. Run it yourself, in a terminal:  python3 %s env-names" % _self_path())
        return EX_BLOCKED
    say("Chat-lane env-names — the four names into %s, as %s." % (envfile, account))
    say("  First the order is measured, as verify measures it: the Slack fence before the")
    say("  token, and the port block — on the port the dispatcher listens on — before")
    say("  CYRUS_HOST_EXTERNAL makes it listen on every interface.")
    sudo.acquire("`env-names` reads the dispatcher's config and env file as the %s role "
                 "account (names and tool lists only) and asks pf, as root, which rules it "
                 "holds; then it writes %s as that account" % (account, envfile),
                 _resume("env-names"))
    refused = _env_names_gate(conf, runner)
    if refused is not None:
        return refused
    ask = reader or getpass.getpass
    token = (ask("  Paste the CHAT app's Bot User OAuth Token (Slack: OAuth & Permissions; "
                 "xoxb-...), then Enter. Hidden: ") or "").strip()
    secret = (ask("  Paste the CHAT app's Signing Secret (Slack: Basic Information), then "
                  "Enter. Hidden: ") or "").strip()
    problem = secret_shape_problem(token, secret)
    if problem:
        token = secret = None
        say("REFUSED: %s Nothing was changed. (No value is shown.)" % problem)
        return EX_USAGE
    say("  token: %d characters, starts %s; signing secret: 32 lower-case hex characters."
        % (len(token), CHAT_TOKEN_PREFIX))
    res = _role_write(runner, account, env_writer_command(conf, "set"),
                      "%s\n%s\n" % (token, secret),
                      "write the four chat-lane names into %s" % envfile, secret=True)
    token = secret = None
    return _env_done(conf, res)


# --------------------------------------------------------------------------- #
# front-door — piece 7.
# --------------------------------------------------------------------------- #
def cmd_front_door(conf, runner, sudo, apply_it=False, remove=False):
    found = agent_env_markers_present()
    if found:
        say(refusal_text(found, "front-door"))
        return EX_REFUSED
    unset = [k for k in FRONT_DOOR_KEYS if not conf.get(k)]
    if unset:
        say("REFUSED: front-door needs %s in chat-lane.conf. Nothing was read or changed."
            % " and ".join(unset))
        return EX_USAGE
    account, path = conf["ROLE_ACCOUNT"], conf["FRONT_DOOR_CONFIG"]
    matcher = conf["FRONT_DOOR_MATCHER"]
    say("Front door allowlist — %s%s" % ("APPLY" if apply_it else "DRY RUN: nothing will be "
                                          "changed", ", REMOVING the chat path" if remove else ""))
    sudo.acquire("`front-door` reads %s as the %s role account%s." % (
        path, account, ", and writes and validates it as that account" if apply_it
        else ", and changes nothing"), _resume("front-door", apply_it, remove))
    facts, why_not = probe_facts(runner, conf)
    fd = front_door_digest((facts or {}).get("frontDoor"), matcher)
    if fd is None:
        say("NOT MEASURED: the read-only probe as %s did not read %s (%s). Nothing was "
            "changed." % (account, path, why_not or "no answer for the front door"))
        return EX_UNKNOWN
    if fd.get("error"):
        say("%s: %s is %s. Nothing was changed." % (
            "REFUSED" if fd["error"] == "missing" else "NOT MEASURED", path, fd["error"]))
        return EX_BLOCKED if fd["error"] == "missing" else EX_UNKNOWN
    say("  file      %s" % path)
    if fd["count"] != 1:
        say("REFUSED: expected exactly one `%s path ...` line in %s, found %d%s. Nothing was "
            "changed: the allowlist is one line, and this will not guess which."
            % (matcher, path, fd["count"], " (lines %s)" % ", ".join(
                str(n) for n in fd["lines"]) if fd["lines"] else ""))
        return EX_BLOCKED
    text = fd["text"]
    say("  line      %d" % fd["line"])
    say("  now       %s" % text.strip())
    wide = []
    for p in fd["paths"]:
        why = widening_reason(p)
        if why:
            wide.append(p)
            say("  WIDENS    %s: %s." % (p, why))
        elif p not in EXPECTED_ROUTES:
            say("  WARNING   %s is not one of the dispatcher's own routes: the door forwards a "
                "path this kit cannot account for. Find out what added it." % p)
    say("  The dispatcher's own routes:")
    for route in sorted(EXPECTED_ROUTES):
        say("    %-16s %s" % (route, EXPECTED_ROUTES[route]))
    if wide and not remove:
        say("REFUSED: this line forwards %s. The chat path is not added to a door that "
            "reaches the dispatcher's control routes: take %s off the line by hand, restart "
            "the door, then run front-door again. Nothing was changed."
            % (", ".join(wide), "it" if len(wide) == 1 else "them"))
        return EX_BLOCKED
    new = front_line_edit(text, matcher, SLACK_PATH, remove)
    if new is None:
        say("REFUSED: taking %s off would leave the line with no path at all, and an empty "
            "allowlist line is a broken door, not a closed one. Edit it by hand. Nothing was "
            "changed." % SLACK_PATH)
        return EX_BLOCKED
    if new == text:
        say("  ok        %s is already %s the line. Nothing to change." % (
            SLACK_PATH, "off" if remove else "on"))
        para("If the door was not restarted since that line last changed, restart it now "
             "(card %s)." % ("CK-C6" if remove else "CK-C2"), "  ")
        return EX_OK
    say("  becomes   %s" % new.strip())
    if not apply_it:
        say("DRY RUN: nothing was changed. Run it again with --apply to write this, as %s."
            % account)
        return EX_BLOCKED
    patch = {"sha256": fd["sha256"], "index": fd["index"], "old": text, "new": new}
    res = _role_write(runner, account, front_door_writer_command(conf), json.dumps(patch),
                      "%s %s on line %d of %s, then validate it, as %s" % (
                          "remove" if remove else "add", SLACK_PATH, fd["line"], path,
                          account))
    _relay(res)
    if res.rc == 3:
        return EX_REFUSED
    if not res.ok:
        say("FAILED: the writer exited %d." % res.rc)
        return EX_FAILED
    say("")
    say("It restarted nothing. Restart the front door, then probe it:")
    say_group(FRONT_RESTART, conf)
    say_group(PROBES_OFF if remove else PROBES_ON, conf)
    return EX_OK


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
WRITING_COMMANDS = ("merge", "env-names", "front-door")
# The flags each command takes. Anything else is a usage error, so `merge --remove` cannot
# be read as a command that removed something.
_FLAGS = {"compose": (), "verify": (), "card": (), "merge": ("apply",),
          "env-names": ("remove",), "front-door": ("apply", "remove")}


def build_parser():
    p = argparse.ArgumentParser(
        prog="pipeline_chat_lane_setup.py",
        description="Compose the dispatcher's built-in Slack lane, apply the pieces a "
                    "person asks it to, and verify it read-only.")
    p.add_argument("command", nargs="?", choices=["compose", "verify", "card"]
                   + list(WRITING_COMMANDS))
    p.add_argument("target", nargs="?", help="a CK-id for `card`")
    p.add_argument("--conf", default="chat-lane.conf")
    p.add_argument("--piece", type=int, help="compose: print one piece (1 to 7) alone")
    p.add_argument("--apply", action="store_true",
                   help="merge, front-door: write (the default is a dry run)")
    p.add_argument("--remove", action="store_true",
                   help="env-names, front-door: take the chat lane's names, or path, out")
    p.add_argument("--selftest", action="store_true")
    return p


def main(argv=None, runner=None, sudo=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.command:
        build_parser().print_usage()
        return EX_USAGE
    wrong = [f for f in ("apply", "remove") if getattr(args, f)
             and f not in _FLAGS[args.command]]
    if wrong:
        say("`%s` takes no --%s. Nothing was attempted." % (args.command, wrong[0]))
        return EX_USAGE
    # BEFORE the conf is read: a refusal that happens after the first read already ran.
    if args.command == "verify" or args.command in WRITING_COMMANDS:
        found = agent_env_markers_present()
        if found:
            say(refusal_text(found, args.command))
            return EX_REFUSED
    if args.command == "card":
        # No conf at all is fine: a card reads without one. A conf that was read and has
        # problems is named, and nothing is composed from it (review of KIT-197, 39).
        conf, errors = load_conf(args.conf)
        broken = conf is not None and bool(errors)
        if broken:
            say("Your conf has %d problem(s), so no command below is composed from it:"
                % len(errors))
            for e in errors:
                say("  - " + e)
        try:
            rc = print_card(args.target or "", None if (broken or not conf) else conf, broken)
        except ConfError as exc:
            say(str(exc))
            return EX_USAGE
        return EX_USAGE if broken else rc
    conf, errors = load_conf(args.conf)
    if errors:
        say("Your conf has %d problem(s). Every one of them, in one pass:" % len(errors))
        for e in errors:
            say("  - " + e)
        return EX_USAGE
    if args.command == "compose":
        if args.piece is not None and args.piece not in COMPOSE_PIECES:
            say("no such piece: %d (have 1 to %d)" % (args.piece, max(COMPOSE_PIECES)))
            return EX_USAGE
        return cmd_compose(conf, args.conf, args.piece)
    runner = runner or Runner(dry_run=True)
    sudo = sudo or SudoSession()
    resume = _resume(args.command, args.apply, args.remove)
    try:
        if args.command == "verify":
            return cmd_verify(conf, runner, sudo)
        if args.command == "merge":
            return cmd_merge(conf, runner, sudo, args.apply)
        if args.command == "env-names":
            return cmd_env_names(conf, runner, sudo, args.remove,
                                 sys.stdin.isatty() and sys.stdout.isatty())
        return cmd_front_door(conf, runner, sudo, args.apply, args.remove)
    except NoPrivilege as exc:
        # The text comes from the Stage E installer's SudoSession, so its "run the same
        # command again" line names THAT script and drops the --conf this run was given.
        # Following it would run a different installer's command and leave the chat lane
        # untouched (review of PR #147, finding 8).
        mine = "python3 %s %s --conf %s" % (_self_path(), resume, args.conf)
        say(str(exc).replace("python3 %s %s" % (_stage_e_self_path(), resume), mine))
        say("")
        say("The command to run again is this one:  %s" % mine)
        return EX_NOPRIV


# ----------------------------- SELFTEST BELOW ------------------------------ #
# Everything above this line is scanned for write and merge tokens by the selftest.
SELFTEST_SENTINEL = "# ----------------------------- SELFTEST BELOW"

# Tokens the part above may never hold: a content write of any kind from THIS process, and
# any merge, approve, protected-label or ticket-state path.  # banned-token-list
# Shell writes (`sudo tee …`) are not scanned for: piece 4 PRINTS them for a person. What
# this file itself RUNS is pinned instead, by the argv allowlist in the selftest.
WRITE_TOKENS = ('"w"', "'w'", '"a"', "'a'", '"x"', "'x'", '"w+"', '"r+"',  # banned-token-list
                "json.dump(", "write_text", "os.replace", "os.rename",  # banned-token-list
                "shutil", "why=", ".write(", "os.remove")  # banned-token-list
FORBIDDEN_TOKENS = ("pr merge", "--squash", "--auto", "pr review", "--approve",  # banned-token-list
                    "APPROVE", "--add-label", "--remove-label", "/labels",  # banned-token-list
                    "issues/", "/merge", "enable-auto-merge", "issueUpdate",  # banned-token-list
                    "save_issue", "stateId", "addLabel")  # banned-token-list
_BANNED_MARK = "banned-token-list"


class _FakeRunner(Runner):
    """Answers each command from a table of (needle, rc, out, err): the first needle found
    in the joined argv wins. Records every argv it was asked to run, and every write."""

    def __init__(self, answers=(), default=(1, "", "FakeRunner: nothing scripted")):
        Runner.__init__(self, dry_run=True)
        self.answers = list(answers)
        self.default = default
        self.argvs = []

    def _exec(self, argv, stdin, timeout, cwd=None, **kwargs):
        # cwd/**kwargs: the Stage E Runner this subclasses is gaining a `cwd` argument in a
        # sibling pull request, and its read() forwards it on every call. Without these the
        # battery raises TypeError the moment both land, and Kit checks go red on main for
        # whichever merged second — a break neither pull request's own CI can see (review
        # of PR #147, finding 2). Accepting and ignoring them passes either way.
        from pipeline_stage_e_setup import Result
        self.argvs.append(list(argv))
        line = " ".join(argv)
        for needle, rc, out, err in self.answers:
            if needle in line:
                return Result(rc, out, err)
        return Result(*self.default)


GOOD_PF_RULES_OUT = (
    "block return in quick on ! lo0 inet proto tcp from any to any port = 3456\n"
    "block return in quick on ! lo0 inet6 proto tcp from any to any port = 3456\n")
GOOD_PF_INFO_OUT = "Status: Enabled for 0 days 00:04:12           Debug: Urgent\n"


GOOD_PF_MAIN_OUT = ("scrub-anchor \"com.apple/*\" all fragment reassemble\n"
                    "anchor \"com.apple/*\" all\n")
GOOD_PF_CONF_OUT = ("#\n# Default PF configuration file.\n#\n"
                    "scrub-anchor \"com.apple/*\"\n"
                    "anchor \"com.apple/*\"\n"
                    "load anchor \"com.apple\" from \"/etc/pf.anchors/com.apple\"\n")


def _pf_answers(rules=None, info=None, plist=None, rules_file=None, main=None, pf_conf=None):
    """The six read-only pf answers, healthy unless one is overridden. The order matters:
    the first needle found in the joined argv wins, so the anchor read (`-a … -s rules`)
    must be listed before the main-ruleset read (`-s rules`), which is a substring of it."""
    return [("pfctl -a %s -s rules" % PF_ANCHOR,) + (rules or (0, GOOD_PF_RULES_OUT, "")),
            ("pfctl -s info",) + (info or (0, GOOD_PF_INFO_OUT, "")),
            ("pfctl -s rules",) + (main or (0, GOOD_PF_MAIN_OUT, "")),
            ("/bin/cat %s" % PF_CONF,) + (pf_conf or (0, GOOD_PF_CONF_OUT, "")),
            ("/bin/cat %s" % PF_DAEMON_PLIST,) + (plist or (0, pf_plist(), "")),
            ("/bin/cat %s" % PF_RULES_PATH,) + (rules_file or (0, pf_rules("3456"), ""))]


def _argv_allowed(argv, account):
    """True only for the five reads `verify` may run."""
    if argv[:6] == ["sudo", "-u", account, "-H", "/bin/sh", "-c"] and len(argv) == 7:
        return argv[6].startswith("cd / && /usr/bin/python3 -c ")
    return argv in (["sudo"] + PF_READ_RULES, ["sudo"] + PF_READ_INFO,
                    ["sudo"] + PF_READ_MAIN, ["/bin/cat", PF_CONF],
                    ["/bin/cat", PF_DAEMON_PLIST], ["/bin/cat", PF_RULES_PATH])


class _FakeSudo(object):
    def __init__(self):
        self.acquisitions = 0

    def acquire(self, why, resume):
        self.acquisitions += 1
        return True


def _row_text(row):
    return "\n".join([row["detail"]] + row["lines"])


def _capture(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        value = fn(*a, **kw)
    return value, buf.getvalue()


def _put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


MINIMAL_CONF_TEXT = """
ROLE_ACCOUNT=_exdispatch
DISPATCHER_CONFIG=/opt/example-dispatcher/config.json
DISPATCHER_ENV_FILE=/opt/example-dispatcher/.env
FRONT_DOOR_HOST=chat.example.com
"""

# The four required keys, and the five optional ones the printed commands and the
# front-door subcommand need (KIT-197). ROLE_ENV_FILE keeps its default.
GOOD_CONF_TEXT = MINIMAL_CONF_TEXT + """DISPATCHER_SERVICE=com.example.dispatcher
FRONT_DOOR_SERVICE=com.example.front-door
FRONT_DOOR_CONFIG=/opt/example-dispatcher/Caddyfile
FRONT_DOOR_BIN=/opt/homebrew/bin/caddy
FRONT_DOOR_MATCHER=@dispatcher
"""

# The repository paths the fake dispatcher config serves, and therefore the paths the
# composed pull rules must name.
REPO_ONE = "/srv/example/repo-one"
REPO_TWO = "/srv/example/repo-two"

# Distinctive values the fake env file carries. None may appear in any output.
SENTINELS = ("sentinel-chat-bot-value-4f1c9a", "sentinel-signing-value-83bd20",
             "sentinel-notifier-value-c07e55", "sentinel-debug-value-19aa3e",
             "sentinel-hosted-key-value-5d2e71", "sentinel-hosted-team-value-a90b3c")


def selftest():
    """Owns its environment: the agent markers are scrubbed for the run and restored after,
    so the verdict does not depend on who ran it."""
    saved = dict(os.environ)
    for marker in AGENT_ENV_MARKERS:
        os.environ.pop(marker, None)
    try:
        return _selftest_body()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _selftest_body():
    failures, cases = [], [0]

    def expect(name, cond, detail=""):
        cases[0] += 1
        if not cond:
            failures.append("%s: %s" % (name, detail))

    conf, errs = validate_conf(parse_conf(GOOD_CONF_TEXT)[0])
    expect("good-conf", not errs, errs)

    # -- 1. the grant is the approved list, and holds nothing forbidden ---------
    approved_literal = ["Read", "WebFetch", "WebSearch", "SendMessage", "ToolSearch",
                        "mcp__slack", "mcp__linear",
                        "Bash(git -C /srv/repo-one pull)",
                        "Bash(git -C /srv/repo-one pull --ff-only)",
                        "Bash(git -C /srv/repo-two pull)",
                        "Bash(git -C /srv/repo-two pull --ff-only)"]
    expect("pull-rule-forms-are-literal",
           all(f.count("%s") == 1 and "*" not in f for f in PULL_RULE_FORMS),
           PULL_RULE_FORMS)
    two = owner_grant(["/srv/repo-one", "/srv/repo-two"])
    expect("grant-is-approved", two == approved_literal, two)
    expect("grant-base-has-no-bash", not any(t.startswith("Bash") for t in OWNER_GRANT_BASE),
           OWNER_GRANT_BASE)
    expect("grant-clean", grant_problems(two) == [], grant_problems(two))
    expect("grant-placeholder-clean", grant_problems(owner_grant()) == [])
    expect("grant-dedupes-a-repeated-path",
           owner_grant(["/srv/one", "/srv/one"]) == owner_grant(["/srv/one"]))
    for tool in ("Monitor", "Task", "Agent", "ScheduleWakeup", "Skill", "Write", "Edit",
                 "NotebookEdit", "CronCreate", "RemoteTrigger", "Workflow"):
        expect("grant-lacks-" + tool, tool not in two)
        expect("grant-mutant-" + tool, grant_problems(two + [tool]) != [],
               "a grant holding %s passed" % tool)
    # THE RULE SHAPE THE OWNER REMOVED, and its neighbours. Each must be reported.
    for bad in ("Bash", "Bash(*)", "Bash( * )", "Bash(git -C * pull)", "Bash(git * main)",
                "Bash(sh -c *)", "Bash(bash *)", "Bash(git -C * status *)",
                "Bash(/bin/sh -c 'git pull')", "Bash(git -C /srv/one pull && sh)",
                "Bash(env FOO=1 git -C /srv/one pull)", "Bash(xargs git pull)",
                "Bash(git -c * pull)",
                # literal rules with no wildcard at all, whose own text runs a program
                "Bash(git -c x=y -C /repo pull)",
                "Bash(git -c core.fsmonitor=/tmp/x -C /repo pull)",
                "Bash(git --exec-path=/tmp -C /repo pull)",
                "Bash(git -C /repo pull --upload-pack=/tmp/x)",
                "Bash(git -C /repo fetch --receive-pack /tmp/x)",
                "Bash(git --config-env=core.pager=EVIL -C /repo pull)"):
        expect("grant-refuses:" + bad, grant_problems([bad]) != [],
               "%s passed grant_problems" % bad)
        expect("grant-mutant:" + bad, grant_problems(two + [bad]) != [],
               "a grant holding %s passed" % bad)
    expect("grant-old-rule-gone", "Bash(git -C * pull)" not in two
           and not any("*" in t for t in two), two)
    # …and rules that are genuinely narrow still pass, so the checker is not a blanket no.
    # -C only changes directory and --ff-only names nothing, so both composed forms stay
    # legal however strict the option check gets.
    for ok in ("Bash(git -C /srv/one pull)", "Bash(git -C /srv/one pull --ff-only)",
               "Bash(npm run lint:*)", "Bash(git status)"):
        expect("grant-allows:" + ok, grant_problems([ok]) == [], grant_problems([ok]))
    for path in ("/srv/one", "/opt/example/two", PLACEHOLDER_REPO_PATH):
        for rule in pull_rules(path):
            expect("grant-allows-composed-pull:" + rule, grant_problems([rule]) == [],
                   grant_problems([rule]))
    for opt in ("-c", "--config-env", "--exec-path", "--upload-pack", "--receive-pack"):
        said = " ".join(grant_problems(["Bash(git %s=v -C /r pull)" % opt]))
        expect("grant-option-message-names:" + opt,
               opt in said and "names a program the command will run" in said, said)
    rc, out = _capture(cmd_compose, conf)
    shown = None
    for line in out.splitlines():
        if line.strip().startswith('"slackAllowedTools":'):
            shown = json.loads(line.split(":", 1)[1])
    expect("compose-prints-approved-grant",
           rc == EX_OK and shown == owner_grant(), (rc, shown))
    expect("compose-grant-uses-placeholder",
           shown and shown[-2:] == pull_rules(PLACEHOLDER_REPO_PATH), shown)
    flat_all = " ".join(out.split())
    expect("compose-says-one-pair-per-repository",
           "Repeat BOTH rules for EVERY repository" in flat_all
           and "its pull stops working in chat until you add both rules" in flat_all,
           "piece 1 does not say a new repository needs its own rules")
    expect("compose-says-no-git-option-but-ff-only",
           "A pull rule may carry no git option other than --ff-only" in flat_all
           and all(opt in flat_all for opt in ("--exec-path", "--upload-pack",
                                               "--receive-pack", "--config-env")),
           "piece 1 does not rule out the program-running git options")

    # -- 2. a mutant grant containing Monitor turns compose red ----------------
    real_base = globals()["OWNER_GRANT_BASE"]
    for mutant, label in ((real_base + ["Monitor"], "monitor"),
                          (real_base + ["Bash(git -C * pull)"], "wildcard-pull")):
        try:
            globals()["OWNER_GRANT_BASE"] = mutant
            rc_mutant, _o = _capture(cmd_compose, conf)
            expect("compose-mutant-%s-red" % label, rc_mutant == EX_FAILED, rc_mutant)
        finally:
            globals()["OWNER_GRANT_BASE"] = real_base

    # compose shape: the trusted-members warning first, the appended servers right under
    # the grant. The owner replaced "one member" with "fully trusted members" on
    # 2026-09-24 (KIT-117); the old rule must not come back by accident.
    flat = " ".join(out.split())
    first_piece = out.find("PIECE 1")
    expect("compose-trusted-members-at-top",
           0 <= out.find("MUST BE FULLY TRUSTED") < first_piece,
           "the workspace warning is not first")
    warning = " ".join(out[max(0, out.find("READ THIS FIRST")):max(0, first_piece)].split())
    expect("compose-trusted-members-says-what-a-member-can-do",
           all(w in warning for w in ("allowedUsers", "two-factor", "no guests",
                                      "Full members only", "everything you can",
                                      "every file and token", "steer any running")),
           "the warning block does not say what a member can do, or what the rule asks")
    expect("compose-no-one-member-rule",
           "ONE MEMBER" not in out and "one member" not in flat.lower(),
           "the replaced one-member rule is back in compose")
    grant_at = out.find('"slackAllowedTools":')
    piece2 = out.find("PIECE 2")
    expect("compose-appended-servers-under-grant",
           grant_at < out.find("mcp__cyrus-tools AND mcp__cyrus-docs") < piece2
           and grant_at < out.find("    mcp__cyrus-docs", grant_at) < piece2)
    expect("compose-names-bind-effect", "EVERY network interface" in flat)
    expect("compose-names-notifier-rule",
           "NEVER THE NOTIFIER'S" in flat and "NOTIFIER_SLACK_BOT_TOKEN" in flat)
    expect("compose-front-door", "gains /slack-webhook, and nothing else" in flat)
    expect("compose-deny-limit", "do not stop the upload tool" in flat)

    # piece 4, the port block: between pieces 3 and 5, and complete
    p3, p4, p5 = out.find("PIECE 3"), out.find("PIECE 4"), out.find("PIECE 5")
    expect("compose-port-block-between-3-and-5", 0 < p3 < p4 < p5, (p3, p4, p5))
    piece4 = out[p4:p5]
    for needle in (PF_ANCHOR, "block return in quick on ! lo0 inet proto tcp from any to any "
                   "port 3456", "block return in quick on ! lo0 inet6 proto tcp from any to "
                   "any port 3456", "<string>-E</string>", "<key>RunAtLoad</key>",
                   "sudo launchctl bootstrap system %s" % PF_DAEMON_PLIST,
                   "sudo /sbin/pfctl -n -a %s -f" % PF_ANCHOR,
                   "sudo /sbin/pfctl -a %s -s rules" % PF_ANCHOR, "sudo /sbin/pfctl -s info",
                   "curl -s -m 5 http://127.0.0.1:3456/status"):
        expect("compose-port-block:" + needle[:40], needle in piece4, needle)
    flat4 = " ".join(piece4.split())
    for needle in ("Not the application firewall", "anchor \"com.apple/*\"", "IPv6",
                   "CYRUS_SERVER_PORT", "card CK-C4"):
        expect("compose-port-block-says:" + needle, needle in flat4, needle)
    order_at = out.find("THE ORDER")
    load_step = out.find("Piece 4: install and load the port block", order_at)
    restart_step = out.find("Piece 3, all four names together", order_at)
    c4_step = out.find("Card CK-C4", order_at)
    expect("order-port-block-before-restart", order_at < load_step < restart_step < c4_step,
           (load_step, restart_step, c4_step))
    p3_text = " ".join(out[p3:p4].split())
    expect("compose-piece3-ip-validation", "WEBHOOK_IP_VALIDATION=false IS NOT OPTIONAL" in
           p3_text and "LinearEventTransport.js:89-96" in p3_text and "signature alone" in
           p3_text and "\n    WEBHOOK_IP_VALIDATION=false\n" in out[p3:p4], p3_text[:200])
    restart_at = out.find("Piece 3, all four names together", order_at)
    ticket_at = out.find(" ".join(TICKET_CHECK_STEP.split()[:6]), order_at)
    door_at = out.find("Piece 7 and card CK-C2", order_at)
    expect("order-ticket-check-after-restart-before-slack",
           0 < restart_at < ticket_at < door_at, (restart_at, ticket_at, door_at))
    expect("ticket-check-content", all(t in TICKET_CHECK_STEP for t in (
        "five minutes", "remove CYRUS_HOST_EXTERNAL",
        "Rejected Linear webhook from unauthorized IP")))
    expect("compose-do-not-hosted", "DO NOT SET CYRUS_API_KEY, CYRUS_TEAM_ID OR CYRUS_APP_URL"
           in flat and "app.atcyrus.com/api/failure-modes" in flat
           and "McpConfigService.js:166-181" in flat, "piece 3 lacks the hosted-service DO NOT")
    # the printed block is what a person pastes: parse it as a shell would see it
    lines4 = piece4.splitlines()
    try:
        block = lines4[lines4.index(PF_COPY_START) + 1:lines4.index(PF_COPY_END)]
    except ValueError:
        block = []
    expect("pf-block-delimited", block and block == pf_install_commands("3456"), block[:3])

    def between(start, end):
        try:
            i = block.index(start)
            return "\n".join(block[i + 1:block.index(end, i + 1)]) + "\n"
        except ValueError:
            return None
    rules_heredoc = [l for l in block if l.endswith("<<'RULES'")]
    plist_heredoc = [l for l in block if l.endswith("<<'PLIST'")]
    expect("pf-heredoc-rules-exact", rules_heredoc and between(rules_heredoc[0], "RULES")
           == pf_rules("3456"), between(rules_heredoc[0], "RULES") if rules_heredoc else None)
    expect("pf-heredoc-plist-exact", plist_heredoc and between(plist_heredoc[0], "PLIST")
           == pf_plist())
    expect("pf-block-flush-left", all(l == l.lstrip() or l.startswith("  <")
                                      or l.startswith("    <") for l in block)
           and "RULES" in block and "PLIST" in block and block[block.index("PLIST") - 1]
           == "</plist>", [l for l in block if l != l.lstrip()][:3])
    try:
        shell_check = subprocess.run(["/bin/bash", "-n"], input="\n".join(block) + "\n",
                                     capture_output=True, text=True, timeout=10)
        expect("pf-block-bash-syntax", shell_check.returncode == 0, shell_check.stderr[-300:])
    except (OSError, subprocess.TimeoutExpired) as exc:
        expect("pf-block-bash-syntax", False, "bash -n could not run: %s" % exc)
    rules_text, plist_text = pf_rules("3456"), pf_plist()
    expect("pf-rules-parse-own", pf_missing_families(rules_text, "3456") == [], rules_text)
    expect("pf-rules-parse-pfctl-form", pf_missing_families(GOOD_PF_RULES_OUT, "3456") == [])
    for label, mutant, want in (
            ("empty", "", ["inet", "inet6"]),
            ("inet-only", GOOD_PF_RULES_OUT.splitlines()[0], ["inet6"]),
            ("wrong-port", GOOD_PF_RULES_OUT.replace("3456", "34567"), ["inet", "inet6"]),
            ("not-quick", GOOD_PF_RULES_OUT.replace(" quick", ""), ["inet", "inet6"]),
            ("lo0-not-negated", GOOD_PF_RULES_OUT.replace("! lo0", "lo0"), ["inet", "inet6"]),
            ("pass-not-block", GOOD_PF_RULES_OUT.replace("block return", "pass"),
             ["inet", "inet6"]),
            ("commented", "\n".join("# " + l for l in GOOD_PF_RULES_OUT.splitlines()),
             ["inet", "inet6"])):
        expect("pf-rules-mutant-" + label, pf_missing_families(mutant, "3456") == want,
               pf_missing_families(mutant, "3456"))
    import plistlib
    try:
        parsed = plistlib.loads(plist_text.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — any parse failure is the finding
        parsed = {"error": str(exc)}
    expect("pf-plist-parses", parsed.get("Label") == PF_DAEMON_LABEL
           and parsed.get("ProgramArguments") == ["/sbin/pfctl", "-E", "-a", PF_ANCHOR,
                                                  "-f", PF_RULES_PATH]
           and parsed.get("RunAtLoad") is True, parsed)
    expect("pf-anchor-under-com-apple", PF_ANCHOR.startswith("com.apple/")
           and "/" not in PF_ANCHOR[len("com.apple/"):], PF_ANCHOR)
    conf_4567, errs_4567 = validate_conf(dict(parse_conf(GOOD_CONF_TEXT)[0],
                                              DISPATCHER_PORT="4567"))
    _rc, out_4567 = _capture(cmd_compose, conf_4567)
    expect("compose-port-from-conf", not errs_4567 and "port 4567" in out_4567
           and "127.0.0.1:4567" in out_4567 and "port 3456" not in out_4567, errs_4567)
    expect("conf-port-default", conf.get("DISPATCHER_PORT") == "3456", conf)

    # -- 3. the effective list keeps an inherited default and adds the rules ---
    default = ["Bash(rm -rf *)", "WebFetch"]
    composed, missing, source = fence_entry(None, default)
    expect("fence-keeps-inherited-default",
           composed[:2] == default and list(SLACK_FENCE_RULES) == composed[2:]
           and source == "defaultDisallowedTools", composed)
    composed, missing, source = fence_entry([], default)
    expect("fence-empty-own-list-wins", composed == list(SLACK_FENCE_RULES)
           and source == "its own disallowedTools", composed)
    composed, missing, _s = fence_entry(["Edit", "mcp__slack"], default)
    expect("fence-appends-only-missing", composed == ["Edit", "mcp__slack", "mcp__slack__*"]
           and missing == ["mcp__slack__*"], composed)
    expect("fence-rules-are-stage-e", "slack" in TRACKER_FENCE_SERVERS
           and SLACK_FENCE_RULES == ("mcp__slack", "mcp__slack__*"))
    expect("fingerprints-fit-probe", len(REVIEW_BRIEF_FINGERPRINT) <= INSTRUCTION_HEAD_CHARS
           and len(PLANNING_BRIEF_FINGERPRINT) <= INSTRUCTION_HEAD_CHARS)

    # -- 5. deny patterns match the repository, and a drifted copy is red ------
    try:
        with open(REPO_SETTINGS, encoding="utf-8") as fh:
            repo_settings = json.load(fh)
        drift = repo_deny_drift(repo_settings)
    except (OSError, ValueError) as exc:
        drift = ["could not read %s: %s" % (REPO_SETTINGS, exc)]
    expect("deny-matches-repo", drift == [], drift)
    drifted = json.loads(json.dumps(repo_settings)) if not drift else {}
    if drifted:
        drifted["permissions"]["deny"].append("Read(*.p12)")
        expect("deny-drift-added-red", repo_deny_drift(drifted) != [])
        drifted["permissions"]["deny"] = drifted["permissions"]["deny"][1:-1]
        expect("deny-drift-removed-red", repo_deny_drift(drifted) != [])
    anchored_all = user_deny_patterns(conf)
    # Every rule is anchored at the root, or at the role account's own home (`~/`), which a
    # rule written from there matches wherever the session stands (KIT-197).
    expect("deny-anchored", all(p.startswith("Read(//") or p.startswith("Read(~/")
                                for p in anchored_all)
           and all(anchored(p) in anchored_all for p in REPO_DENY_PATTERNS), anchored_all)
    expect("deny-anchor-shape", anchored("Read(**/id_rsa)") == "Read(//**/id_rsa)"
           and anchored("Read(.env)") == "Read(//**/.env)", anchored("Read(**/id_rsa)"))
    expect("deny-conf-files", "Read(//opt/example-dispatcher/config.json)" in anchored_all
           and "Read(//opt/example-dispatcher/.env)" in anchored_all, anchored_all)

    # -- 10. the manifest's scopes are exactly the cited set -------------------
    manifest = slack_manifest(conf)
    scopes = manifest["oauth_config"]["scopes"]["bot"]
    expect("manifest-scopes-exact", sorted(scopes) == sorted(
        ["app_mentions:read", "chat:write", "groups:history", "reactions:write"])
        and set(scopes) == set(SCOPE_CITES), scopes)
    expect("manifest-scopes-cited", all(".js:" in SCOPE_CITES.get(s, "") for s in scopes))
    events = manifest["settings"]["event_subscriptions"]["bot_events"]
    expect("manifest-events-exact", sorted(events) == ["app_mention", "message.groups"]
           and all(".js:" in EVENT_CITES.get(e, "") for e in events), events)
    expect("manifest-url", manifest["settings"]["event_subscriptions"]["request_url"]
           == "https://chat.example.com/slack-webhook")
    expect("manifest-no-rotation-no-socket",
           manifest["settings"]["token_rotation_enabled"] is False
           and manifest["settings"]["socket_mode_enabled"] is False)
    expect("manifest-no-user-scopes", set(manifest["oauth_config"]["scopes"]) == {"bot"})

    # -- 9. every conf error in one pass --------------------------------------
    bad = ("ROLE_ACCOUNT=bad account\n"
           "ROLE_ACCOUNT=twice\n"
           "DISPATCHER_CONFIG=relative/config.json\n"
           "DISPATCHER_ENV_FILE=\n"
           "FRONT_DOOR_HOST=https://chat.example.com/slack-webhook\n"
           "NOTIFIER_TOKEN_ENV=SLACK_BOT_TOKEN\n"
           "DISPATCHER_PORT=99999\n"
           "SURPRISE=1\n"
           "no equals sign here\n")
    values, perrs = parse_conf(bad)
    _c, verrs = validate_conf(values)
    allerrs = perrs + verrs
    for needle in ("duplicate key ROLE_ACCOUNT", "not a local account name",
                   "DISPATCHER_CONFIG must be an absolute path",
                   "DISPATCHER_ENV_FILE is required", "host name only",
                   "a name the dispatcher itself reads", "DISPATCHER_PORT must be a port",
                   "unknown key SURPRISE", "not KEY=value"):
        expect("conf-all-errors:" + needle, any(needle in e for e in allerrs), allerrs)
    # A pasted token has no `=`, so it lands in the not-KEY=value branch, where the
    # credential-shape guard never sees it. That branch must not echo the line (review of
    # PR #147, finding 10).
    # Assembled, never written out: a literal of this shape is what a code host's secret
    # scanner blocks a push for, and a fixture is not worth an exception to that.
    bot_shaped = "-".join(["xoxb", "9" * 10, "8" * 10, "abcdefGHIJKLmnopQRSTUvwx"])
    for pasted in (bot_shaped,
                   "0123456789abcdef0123456789abcdef",
                   "not a conf line at all"):
        _v, perrs2 = parse_conf("ROLE_ACCOUNT=_x\n%s\n" % pasted)
        joined2 = " ".join(perrs2)
        expect("conf-never-echoes-a-bare-line:" + pasted[:12],
               len(perrs2) == 1 and "not KEY=value" in joined2
               and pasted not in joined2 and pasted[:12] not in joined2
               and ":2:" in joined2 and str(len(pasted)) in joined2, perrs2)
    _v, cerrs = parse_conf("FRONT_DOOR_HOST=xoxb-%s\n" % ("1" * 12))
    expect("conf-credential-shape", any("CREDENTIAL SHAPE" in e for e in cerrs)
           and not any("1" * 12 in e for e in cerrs), cerrs)
    with tempfile.TemporaryDirectory() as tmp:
        cpath = os.path.join(tmp, "chat-lane.conf")
        _put(cpath, bad)
        rc_bad, out_bad = _capture(main, ["compose", "--conf", cpath])
        expect("conf-errors-exit-2", rc_bad == EX_USAGE and out_bad.count("\n  - ") >= 9,
               (rc_bad, out_bad))

    # -- 6/7. verify against fake configs, through the REAL probe --------------
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        cfg_path = os.path.join(tmp, "dispatcher", "config.json")
        env_path = os.path.join(tmp, "dispatcher", ".env")
        front_path = os.path.join(tmp, "front", "Caddyfile")
        vconf = dict(conf, DISPATCHER_CONFIG=cfg_path, DISPATCHER_ENV_FILE=env_path,
                     FRONT_DOOR_CONFIG=front_path)
        _put(front_path, FRONT_DOOR_FIXTURE.replace("/extra-path", "/slack-webhook"))
        review_brief = REVIEW_BRIEF_FINGERPRINT + ". More."
        planning_brief = PLANNING_BRIEF_FINGERPRINT + ". More."

        def good_config():
            return {
                "linearWorkspaces": {"ws": {"linearToken": SENTINELS[3]}},
                "slackAllowedTools": owner_grant([REPO_ONE, REPO_TWO]),
                "defaultDisallowedTools": ["Bash(rm -rf *)", "mcp__slack", "mcp__slack__*"],
                "repositories": [
                    {"id": "coding-a", "name": "a", "allowedTools": ["Read"],
                     "repositoryPath": REPO_ONE,
                     "disallowedTools": ["Edit", "mcp__slack", "mcp__slack__*"]},
                    {"id": "coding-b", "name": "b", "repositoryPath": REPO_TWO,
                     "labelPrompts": {"builder": ["Bug"]}},
                    {"id": "reviews-a", "name": "reviews-a", "appendInstruction": review_brief,
                     "disallowedTools": ["Bash", "mcp__slack", "mcp__slack__*"]},
                    {"name": "stage-a-planning-plan", "appendInstruction": planning_brief,
                     "disallowedTools": ["Edit", "mcp__slack", "mcp__slack__*"]},
                ],
            }

        def good_env():
            return ("# dispatcher env\n"
                    "SLACK_BOT_TOKEN=%s\n"
                    "export SLACK_SIGNING_SECRET=\"%s\"\n"
                    "CYRUS_HOST_EXTERNAL=true\n"
                    "WEBHOOK_IP_VALIDATION=false\n" % (SENTINELS[0], SENTINELS[1]))

        def settings_with(deny):
            return json.dumps({"env": {"SECRET_THING": SENTINELS[3]},
                               "permissions": {"deny": deny}})

        def probe(config_obj, env_text, settings_text):
            _put(cfg_path, json.dumps(config_obj) if not isinstance(config_obj, str)
                 else config_obj)
            _put(env_path, env_text)
            spath = os.path.join(home, ".claude", "settings.json")
            if settings_text is None:
                if os.path.exists(spath):
                    os.unlink(spath)
            else:
                _put(spath, settings_text)
            ran = subprocess.run([sys.executable, "-c", FACTS_PY, cfg_path, env_path,
                                  vconf["DISPATCHER_PORT"], front_path,
                                  vconf.get("FRONT_DOOR_MATCHER") or "@dispatcher"],
                                 capture_output=True, text=True,
                                 env=dict(os.environ, HOME=home))
            # EVERY probe's whole output is kept for the no-value scan at the end.
            outputs.append(ran.stdout + ran.stderr)
            return ran

        def facts_of(ran):
            """The probe's JSON, or {} with a named failure — never a crash that would
            stop the no-value scan from running."""
            try:
                return json.loads(ran.stdout)
            except ValueError:
                expect("probe-output-is-json", False, "the probe printed more than one "
                       "JSON line (%d bytes)" % len(ran.stdout))
                return {}

        def verify_with(ran, **pf):
            fake = _FakeRunner([("/usr/bin/python3 -c", ran.returncode, ran.stdout,
                                 ran.stderr)] + _pf_answers(**pf))
            fakes.append(fake)
            sudo = _FakeSudo()
            rc_v, printed = _capture(cmd_verify, vconf, fake, sudo)
            outputs.append(printed)
            return rc_v, printed, fake, sudo

        def port_row(printed_rc_fake):
            return re.search(r"port-block\s+(\S+)", printed_rc_fake[1])

        outputs, fakes = [], []
        good_settings = settings_with(user_deny_patterns(vconf) + ["Read(~/.ssh/**)"])

        ran = probe(good_config(), good_env(), good_settings)
        outputs.append(ran.stdout + ran.stderr)
        expect("probe-runs", ran.returncode == 0 and ran.stdout.strip().startswith("{"),
               (ran.returncode, ran.stderr[-300:]))
        rc_v, printed, fake, sudo = verify_with(ran)
        outputs.append(printed)
        expect("verify-match-exit-0", rc_v == EX_OK, printed)
        expect("verify-no-writes", fake.writes == [], fake.writes)
        expect("verify-sudo-once", sudo.acquisitions == 1, sudo.acquisitions)
        argv = fake.argvs[0] if fake.argvs else []
        expect("verify-as-role", argv[:5] == ["sudo", "-u", "_exdispatch", "-H", "/bin/sh"]
               and "/usr/bin/python3 -c" in argv[-1], argv[:5])
        facts = facts_of(ran)
        kinds = {e.get("id") or e.get("name"): entry_kind(e)
                 for e in (facts.get("config") or {}).get("entries") or []}
        expect("entry-kinds", kinds == {"coding-a": "coding", "coding-b": "coding",
                                        "reviews-a": "review",
                                        "stage-a-planning-plan": "planning"}, kinds)

        # grant mismatch
        cfg_m = good_config()
        cfg_m["slackAllowedTools"] = owner_grant([REPO_ONE, REPO_TWO]) + ["Monitor"]
        ran = probe(cfg_m, good_env(), good_settings)
        rc_v, printed, _f, _s = verify_with(ran)
        outputs.append(printed)
        expect("verify-grant-mismatch", rc_v == EX_BLOCKED and "Monitor" in printed
               and re.search(r"grant\s+BLOCKED-ON-HUMAN", printed), printed)
        cfg_m = good_config()
        del cfg_m["slackAllowedTools"]
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        expect("verify-grant-unset", rows[0]["outcome"] == BLOCKED, rows[0])
        cfg_m = good_config()
        cfg_m["repositories"][0]["allowedTools"] = ["Read", "mcp__github"]
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        expect("verify-first-entry-mcp-tools", rows[0]["outcome"] == BLOCKED
               and "mcp__github" in _row_text(rows[0]), rows[0])

        # THE FIRST **ACTIVE** ENTRY, not the first line of the file: the dispatcher skips
        # isActive:false entries, so an inactive first entry hides a live one (review of
        # PR #147, findings 9, 11 and 14).
        cfg_m = good_config()
        cfg_m["repositories"].insert(0, {"id": "retired", "name": "retired",
                                         "isActive": False,
                                         "repositoryPath": "/srv/example/retired",
                                         "allowedTools": ["Read"],
                                         "disallowedTools": list(SLACK_FENCE_RULES)})
        cfg_m["repositories"][1]["allowedTools"] = ["Read", "mcp__github"]
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        grant_row = [r for r in rows if r["check"] == "grant"][0]
        expect("verify-grant-first-active-entry", grant_row["outcome"] == BLOCKED
               and "first ACTIVE repository entry coding-a" in _row_text(grant_row),
               grant_row)
        # an inactive entry's mcp__ tools are a note, and say what activating it would do
        cfg_m = good_config()
        cfg_m["repositories"].insert(0, {"id": "retired", "name": "retired",
                                         "isActive": False,
                                         "allowedTools": ["Read", "mcp__github"],
                                         "disallowedTools": list(SLACK_FENCE_RULES)})
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        grant_row = [r for r in rows if r["check"] == "grant"][0]
        expect("verify-grant-inactive-entry-is-a-note",
               grant_row["outcome"] == ALREADY_DONE
               and "inactive entry retired" in _row_text(grant_row), grant_row)

        # THE PULL RULES ARE PER REPOSITORY, from the live paths.
        rows = evaluate(facts_of(probe(good_config(), good_env(), good_settings)), vconf)
        grant_row = [r for r in rows if r["check"] == "grant"][0]
        expect("verify-grant-pull-rules-match-live-paths",
               grant_row["outcome"] == ALREADY_DONE and "2 repository path(s)"
               in grant_row["detail"], grant_row)
        cfg_m = good_config()
        cfg_m["repositories"].append({"id": "coding-c", "name": "c",
                                      "repositoryPath": "/srv/example/repo-three",
                                      "disallowedTools": list(SLACK_FENCE_RULES)})
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        grant_row = [r for r in rows if r["check"] == "grant"][0]
        expect("verify-grant-new-repository-has-no-pull-rule",
               grant_row["outcome"] == BLOCKED
               and "/srv/example/repo-three has no pull rule" in _row_text(grant_row),
               grant_row)
        expect("verify-grant-paste-line-carries-every-repository",
               any("repo-three pull)" in l for l in grant_row["lines"]), grant_row["lines"])
        cfg_m = good_config()
        cfg_m["slackAllowedTools"] = [t for t in cfg_m["slackAllowedTools"]
                                      if t != "Bash(git -C %s pull --ff-only)" % REPO_TWO]
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        grant_row = [r for r in rows if r["check"] == "grant"][0]
        expect("verify-grant-half-a-pull-pair", grant_row["outcome"] == BLOCKED
               and "only part of its pull rule pair" in _row_text(grant_row), grant_row)
        # a live grant carrying the shape the owner removed is reported as unsafe
        cfg_m = good_config()
        cfg_m["slackAllowedTools"] = OWNER_GRANT_BASE + ["Bash(git -C * pull)"]
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        grant_row = [r for r in rows if r["check"] == "grant"][0]
        expect("verify-grant-live-wildcard-is-unsafe", grant_row["outcome"] == BLOCKED
               and "the live grant is unsafe" in _row_text(grant_row)
               and "wildcard where the subcommand goes" in _row_text(grant_row), grant_row)

        # a coding entry missing the fence — and the composed list keeps the default
        cfg_m = good_config()
        cfg_m["repositories"][0]["disallowedTools"] = ["Edit"]
        ran = probe(cfg_m, good_env(), good_settings)
        rc_v, printed, _f, _s = verify_with(ran)
        outputs.append(printed)
        expect("verify-fence-missing", rc_v == EX_BLOCKED and "coding-a" in printed
               and re.search(r"coding-fence\s+BLOCKED-ON-HUMAN", printed), printed)
        rows = evaluate(facts_of(ran), vconf)
        fence_row = [r for r in rows if r["check"] == "coding-fence"][0]
        expect("verify-fence-names-inheriting", not any("coding-b" in l and "lacks" in l
                                                        for l in fence_row["lines"]))
        cfg_m = good_config()
        cfg_m["defaultDisallowedTools"] = ["Bash(rm -rf *)"]
        cfg_m["repositories"][1].pop("disallowedTools", None)
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        fence_row = [r for r in rows if r["check"] == "coding-fence"][0]
        paste = [l for l in fence_row["lines"] if "coding-b" in l and "paste" in l]
        expect("verify-fence-inherit-keeps-default", fence_row["outcome"] == BLOCKED and paste
               and json.dumps(["Bash(rm -rf *)", "mcp__slack", "mcp__slack__*"]) in paste[0],
               fence_row)
        cfg_m = good_config()
        cfg_m["repositories"][3]["disallowedTools"] = ["Edit"]
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        fence_row = [r for r in rows if r["check"] == "coding-fence"][0]
        expect("verify-fence-planning-entry", fence_row["outcome"] == BLOCKED
               and "stage-a-planning-plan" in _row_text(fence_row), fence_row)
        # A review entry whose own fence lacks the Slack rules is a FAILURE, not a note:
        # piece 2 skips those entries only because that fence is supposed to name the
        # server (review of PR #147, findings 4 and 5).
        cfg_m = good_config()
        cfg_m["repositories"][2]["disallowedTools"] = ["Bash"]
        ran_rev = probe(cfg_m, good_env(), good_settings)
        rows = evaluate(facts_of(ran_rev), vconf)
        fence_row = [r for r in rows if r["check"] == "coding-fence"][0]
        expect("verify-fence-review-unfenced-is-a-problem",
               fence_row["outcome"] == BLOCKED and "reviews-a" in _row_text(fence_row)
               and "Stage E" in _row_text(fence_row), fence_row)
        rc_v, printed, _f, _s = verify_with(ran_rev)
        expect("verify-fence-review-unfenced-exit",
               rc_v == EX_BLOCKED and "No drift" not in printed, printed)
        # …and a review entry that IS fenced stays quiet.
        rows = evaluate(facts_of(probe(good_config(), good_env(), good_settings)), vconf)
        expect("verify-fence-review-fenced-quiet",
               [r for r in rows if r["check"] == "coding-fence"][0]["outcome"]
               == ALREADY_DONE)

        # -- 4. a prompt-type override is warned by name
        cfg_m = good_config()
        cfg_m["repositories"][0]["labelPrompts"] = {
            "debugger": {"labels": ["Bug"], "disallowedTools": ["Edit"]}}
        cfg_m["promptDefaults"] = {"scoper": {"disallowedTools": ["Write"]}}
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        fence_row = [r for r in rows if r["check"] == "coding-fence"][0]
        text = _row_text(fence_row)
        expect("verify-prompt-type-warned", fence_row["outcome"] == BLOCKED
               and "prompt type debugger" in text and "prompt type scoper" in text, text)
        expect("prompt-type-gaps", prompt_type_gaps({"builder": ["mcp__slack"],
                                                     "scoper": list(SLACK_FENCE_RULES)})
               == ["builder"])
        env_dt = good_env() + "DISALLOWED_TOOLS=Bash\n"
        rows = evaluate(facts_of(probe(good_config(), env_dt, good_settings)), vconf)
        expect("verify-fence-env-override-unknown",
               [r for r in rows if r["check"] == "coding-fence"][0]["outcome"] == UNKNOWN,
               rows)

        # A key that was REMOVED rather than emptied keeps its old value in the running
        # dispatcher, so the rows that can go green by removal say so (PR #147, 7 and 16).
        rows = evaluate(facts_of(probe(good_config(), good_env(), good_settings)), vconf)
        mcp_row = [r for r in rows if r["check"] == "chat-mcp-configs"][0]
        expect("verify-mcp-unset-carries-the-removal-note",
               mcp_row["outcome"] == ALREADY_DONE
               and "keeps the old value until it is restarted" in _row_text(mcp_row),
               mcp_row)
        cfg_m = good_config()
        cfg_m["slackMcpConfigs"] = []
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        mcp_row = [r for r in rows if r["check"] == "chat-mcp-configs"][0]
        expect("verify-mcp-empty-list-needs-no-restart",
               mcp_row["outcome"] == ALREADY_DONE
               and "keeps the old value" not in _row_text(mcp_row), mcp_row)
        cfg_m = good_config()
        cfg_m["promptDefaults"] = {"scoper": {"disallowedTools": ["Write"]}}
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        fence_row = [r for r in rows if r["check"] == "coding-fence"][0]
        expect("verify-promptdefaults-says-not-to-delete-the-key",
               "rather than deleting the key" in _row_text(fence_row), fence_row)

        # extra slackMcpConfigs
        extra_path = os.path.join(tmp, "dispatcher", "extra.json")
        _put(extra_path, json.dumps({"mcpServers": {"github": {"headers": {
            "Authorization": SENTINELS[3]}}, "filesystem": {}}}))
        cfg_m = good_config()
        cfg_m["slackMcpConfigs"] = [extra_path]
        ran = probe(cfg_m, good_env(), good_settings)
        rc_v, printed, _f, _s = verify_with(ran)
        outputs.append(printed)
        outputs.append(ran.stdout)
        expect("verify-extra-mcp-configs", rc_v == EX_BLOCKED and "github" in printed
               and "filesystem" in printed, printed)

        # the notifier's token name in the dispatcher env file
        env_n = good_env() + "NOTIFIER_SLACK_BOT_TOKEN='%s'\n" % SENTINELS[2]
        ran = probe(good_config(), env_n, good_settings)
        rc_v, printed, _f, _s = verify_with(ran)
        outputs.append(printed)
        outputs.append(ran.stdout)
        expect("verify-notifier-token-present", rc_v == EX_BLOCKED
               and re.search(r"notifier-token-absent\s+BLOCKED-ON-HUMAN", printed), printed)

        # WEBHOOK_IP_VALIDATION: missing and `true` are failures under CYRUS_HOST_EXTERNAL,
        # `false` passes, and before activation a missing one is outstanding, not broken
        base_env = good_env().replace("WEBHOOK_IP_VALIDATION=false\n", "")
        for label, env_text, want_row, want_rc in (
                ("missing", base_env, FAILED, EX_FAILED),
                ("true", base_env + "WEBHOOK_IP_VALIDATION=true\n", FAILED, EX_FAILED),
                ("false", base_env + "WEBHOOK_IP_VALIDATION=false\n", ALREADY_DONE, EX_OK),
                ("quoted-FALSE", base_env + "WEBHOOK_IP_VALIDATION=\"FALSE\"\n", ALREADY_DONE,
                 EX_OK),
                ("missing-before-activation", base_env.replace(
                    "CYRUS_HOST_EXTERNAL=true\n", ""), BLOCKED, EX_BLOCKED)):
            ran_ip = probe(good_config(), env_text, good_settings)
            rc_v, printed, _f, _s = verify_with(ran_ip)
            got = re.search(r"ip-validation-off\s+(\S+)", printed)
            expect("verify-ip-validation-" + label, rc_v == want_rc and got
                   and got.group(1) == want_row
                   and (want_row == ALREADY_DONE or "403" in printed), printed)

        # CYRUS_API_KEY and CYRUS_TEAM_ID: each alone turns the check red, by name only
        for name, sentinel in (("CYRUS_API_KEY", SENTINELS[4]), ("CYRUS_TEAM_ID", SENTINELS[5])):
            ran = probe(good_config(), good_env() + "%s=%s\n" % (name, sentinel), good_settings)
            rc_v, printed, _f, _s = verify_with(ran)
            expect("verify-hosted-key-present:" + name, rc_v == EX_BLOCKED
                   and re.search(r"hosted-keys-absent\s+BLOCKED-ON-HUMAN", printed)
                   and name in printed, printed)
        rows = evaluate(facts_of(probe(good_config(), good_env(), good_settings)), vconf,
                        {"rules": {"rc": 0, "out": GOOD_PF_RULES_OUT},
                         "info": {"rc": 0, "out": GOOD_PF_INFO_OUT},
                         "plist": {"rc": 0, "out": pf_plist()},
                         "rulesFile": {"rc": 0, "out": pf_rules("3456")}})
        expect("verify-hosted-keys-absent-clean",
               [r for r in rows if r["check"] == "hosted-keys-absent"][0]["outcome"]
               == ALREADY_DONE and [r["check"] for r in rows] == list(CHECKS),
               [(r["check"], r["outcome"]) for r in rows])

        # the port block: healthy, then one mutant per way it can be wrong
        ran = probe(good_config(), good_env(), good_settings)
        for label, pf, want_row, want_rc, needle in (
                ("empty-anchor", {"rules": (0, "", "")}, BLOCKED, EX_BLOCKED, "inet and inet6"),
                ("inet-only", {"rules": (0, GOOD_PF_RULES_OUT.splitlines()[0] + "\n", "")},
                 BLOCKED, EX_BLOCKED, "for inet6"),
                ("anchor-absent", {"rules": (1, "", "pfctl: Anchor does not exist.")},
                 BLOCKED, EX_BLOCKED, "is not loaded"),
                ("pf-disabled", {"info": (0, "Status: Disabled\n", "")}, BLOCKED, EX_BLOCKED,
                 "pf is not enabled"),
                ("plist-missing", {"plist": (1, "", "No such file or directory")}, BLOCKED,
                 EX_BLOCKED, "is not installed at %s" % PF_DAEMON_PLIST),
                ("plist-without-E", {"plist": (0, pf_plist().replace(
                    "<string>-E</string>", ""), "")}, BLOCKED, EX_BLOCKED,
                 "<string>-E</string>"),
                ("rules-file-missing", {"rules_file": (1, "", "No such file")}, BLOCKED,
                 EX_BLOCKED, "rules file is not installed"),
                ("pfctl-unreadable", {"rules": (1, "", "sudo: a password is required"),
                                      "info": (1, "", "sudo: a password is required")},
                 UNKNOWN, EX_UNKNOWN, "could not show")):
            rc_v, printed, _f, _s = verify_with(ran, **pf)
            got = port_row((rc_v, printed))
            expect("verify-port-block-" + label,
                   rc_v == want_rc and got and got.group(1) == want_row
                   and needle in " ".join(printed.split()), printed)
        # THE ANCHOR POINT. A child anchor keeps its rules whether or not anything
        # evaluates them, so the main ruleset is what decides (PR #147, finding 12).
        for label, pf, want_row, needle in (
                # both gone: nothing evaluates the anchor, and that is a fact, not a guess
                ("anchor-point-gone-everywhere",
                 {"main": (0, "block drop in all\n", ""),
                  "pf_conf": (0, "# emptied by an update\n", "")}, BLOCKED,
                 "nothing evaluates this anchor"),
                # the file has it but the loaded ruleset does not SHOW it: this command has
                # never observed how pfctl prints an anchor line, so it says so rather than
                # claiming drift (contract §13)
                ("main-ruleset-lost-the-anchor",
                 {"main": (0, "block drop in all\n", "")}, UNKNOWN,
                 "does not show an anchor"),
                ("pf-conf-lost-the-anchor",
                 {"pf_conf": (0, "# emptied by an update\n", "")}, BLOCKED,
                 "no longer carries an anchor"),
                ("main-ruleset-unreadable",
                 {"main": (1, "", "pfctl: Operation not permitted")}, UNKNOWN,
                 "loaded main ruleset could not be read"),
                ("pf-conf-unreadable",
                 {"pf_conf": (1, "", "cat: /etc/pf.conf: Permission denied")}, UNKNOWN,
                 "could not be read"),
                ("anchor-in-file-not-in-loaded-ruleset",
                 {"main": (0, "block drop in all\n", ""),
                  "pf_conf": (0, GOOD_PF_CONF_OUT, "")}, UNKNOWN,
                 "Check by hand: sudo pfctl -s rules"),
                # could-not-read is NOT is-not-there (PR #147, finding 15)
                ("rules-file-unreadable",
                 {"rules_file": (1, "", "cat: …/pf.rules: Permission denied")}, UNKNOWN,
                 "may not be traversable"),
                ("plist-unreadable",
                 {"plist": (1, "", "cat: …plist: Permission denied")}, UNKNOWN,
                 "may not be traversable")):
            rc_v, printed, _f, _s = verify_with(ran, **pf)
            got = port_row((rc_v, printed))
            expect("verify-port-block-" + label,
                   got and got.group(1) == want_row and needle in " ".join(printed.split()),
                   printed)
        expect("pf-install-makes-the-directory-traversable",
               any(l.startswith("sudo chmod 755") and PF_RULES_DIR in l
                   for l in pf_install_commands("3456")), pf_install_commands("3456")[:4])
        expect("read-denied-tells-absent-from-unreadable",
               _read_denied({"err": "cat: x: Permission denied"}) is True
               and _read_denied({"err": "cat: x: No such file or directory"}) is False
               and _read_denied({"err": ""}) is False)

        for port_line, want_row in (("CYRUS_SERVER_PORT=4000\n", BLOCKED),
                                    ("CYRUS_SERVER_PORT=3456\n", ALREADY_DONE),
                                    ("CYRUS_SERVER_PORT=not-a-port\n", ALREADY_DONE)):
            ran_p = probe(good_config(), good_env() + port_line, good_settings)
            rc_v, printed, _f, _s = verify_with(ran_p)
            got = port_row((rc_v, printed))
            expect("verify-port-matches:" + port_line.strip(),
                   got and got.group(1) == want_row, printed)

        # a missing user settings file
        ran = probe(good_config(), good_env(), None)
        rc_v, printed, _f, _s = verify_with(ran)
        outputs.append(printed)
        expect("verify-user-settings-missing", rc_v == EX_BLOCKED
               and re.search(r"user-settings\s+BLOCKED-ON-HUMAN", printed), printed)
        ran = probe(good_config(), good_env(), settings_with(REPO_DENY_PATTERNS))
        rows = evaluate(facts_of(ran), vconf)
        expect("verify-user-settings-unanchored-red",
               [r for r in rows if r["check"] == "user-settings"][0]["outcome"] == BLOCKED)

        # env problems: a missing name, an empty one, a non-true external flag
        env_bad = "SLACK_BOT_TOKEN=\nCYRUS_HOST_EXTERNAL=false # %s\n" % SENTINELS[1]
        ran = probe(good_config(), env_bad, good_settings)
        rows = evaluate(facts_of(ran), vconf)
        outputs.append(ran.stdout)
        env_row = [r for r in rows if r["check"] == "dispatcher-env"][0]
        expect("verify-env-problems", env_row["outcome"] == BLOCKED and
               set([env_row["detail"]] + env_row["lines"]) >= {
                   "SLACK_BOT_TOKEN is set but empty",
                                         "SLACK_SIGNING_SECRET is not set",
                                         "CYRUS_HOST_EXTERNAL is set, but not to true"},
               env_row)

        # an unparseable config is FAILED; a probe that did not run is UNKNOWN
        ran = probe("{not json " + SENTINELS[3], good_env(), good_settings)
        rc_v, printed, _f, _s = verify_with(ran)
        outputs.append(printed)
        outputs.append(ran.stdout)
        expect("verify-config-unparseable", rc_v == EX_FAILED, printed)
        rc_v, printed, fake_fail, _s = _capture_verify_fail(vconf)
        fakes.append(fake_fail)
        outputs.append(printed)
        expect("verify-probe-failed-unknown", rc_v == EX_UNKNOWN, printed)

        # every command verify ran, in every case above, is one of its five reads
        ran_argvs = [a for f in fakes for a in f.argvs]
        bad_argvs = [a for a in ran_argvs if not _argv_allowed(a, "_exdispatch")]
        expect("verify-runs-only-reads", ran_argvs and not bad_argvs, bad_argvs[:3])
        for a in ran_argvs:
            if a[:1] == ["sudo"] and "/sbin/pfctl" in a:
                expect("verify-pfctl-read-only", not set(a) & {"-f", "-E", "-e", "-F", "-X"}, a)
        expect("argv-allowlist-mutant", not _argv_allowed(
            ["sudo", "/sbin/pfctl", "-a", PF_ANCHOR, "-f", PF_RULES_PATH], "_exdispatch"))

        # -- 7. no value from the fake env file, config or settings in any output
        joined = "\n".join(outputs)
        for sentinel in SENTINELS:
            expect("no-value-in-output:" + sentinel[:20], sentinel not in joined,
                   "a value reached the output")
        expect("probe-open-read-only", not re.search(r"open\([^)]*,", FACTS_PY),
               "the probe opens a file with a mode")

    # -- the fake runner survives the Runner it subclasses growing a cwd argument.
    # A sibling pull request adds cwd to Stage E's Runner.read/_exec, and read() forwards
    # it on every call. Without this the battery raises TypeError once both land, and it is
    # main that goes red, not either pull request (review of PR #147, finding 2).
    probe_fake = _FakeRunner([("anything", 0, "out", "")])
    try:
        probe_fake._exec(["/bin/echo", "x"], None, 60, cwd="/")
        probe_fake._exec(["/bin/echo", "x"], None, 60, cwd="/", env={})
        probe_fake.read(["/bin/echo", "x"])
        cwd_ok = True
    except TypeError as exc:
        cwd_ok = str(exc)
    expect("fake-runner-takes-cwd", cwd_ok is True, cwd_ok)
    expect("fake-runner-signature-is-forward-compatible",
           "cwd" in inspect.signature(_FakeRunner._exec).parameters
           and any(q.kind == inspect.Parameter.VAR_KEYWORD
                   for q in inspect.signature(_FakeRunner._exec).parameters.values()),
           str(inspect.signature(_FakeRunner._exec)))

    # -- the command verify really sends to the role account is RUN here, not just
    # matched as a substring: a swapped argument or a lost quote stays green otherwise
    # (review of PR #147, finding 13).
    with tempfile.TemporaryDirectory() as tmp2:
        home2 = os.path.join(tmp2, "home space")
        cfg2 = os.path.join(tmp2, "dir with space", "config.json")
        env2 = os.path.join(tmp2, "dir with space", ".env")
        conf2 = dict(conf, DISPATCHER_CONFIG=cfg2, DISPATCHER_ENV_FILE=env2,
                     DISPATCHER_PORT="4567")
        _put(cfg2, json.dumps({"slackAllowedTools": ["Read"], "repositories": []}))
        _put(env2, "CYRUS_SERVER_PORT=4567\nSLACK_BOT_TOKEN=%s\n" % SENTINELS[0])
        _put(os.path.join(home2, ".claude", "settings.json"), json.dumps({}))
        script = facts_command(conf2)
        expect("facts-command-runs-the-system-python",
               script.startswith("/usr/bin/python3 -c "), script[:40])
        # the same string, with only the interpreter swapped so this runs anywhere CI does
        local = script.replace("/usr/bin/python3", shlex.quote(sys.executable), 1)
        ran2 = subprocess.run(["/bin/sh", "-c", "cd / && " + local], capture_output=True,
                              text=True, env=dict(os.environ, HOME=home2))
        try:
            facts2 = json.loads(ran2.stdout)
        except ValueError:
            facts2 = {}
        expect("facts-command-is-quoted-and-ordered",
               ran2.returncode == 0
               and (facts2.get("config") or {}).get("slackAllowedTools") == ["Read"]
               and (facts2.get("env") or {}).get("serverPortMatches") is True
               and (facts2.get("env") or {}).get("names") == ["CYRUS_SERVER_PORT",
                                                              "SLACK_BOT_TOKEN"],
               (ran2.returncode, ran2.stdout[:200], ran2.stderr[-200:]))
        expect("facts-command-leaks-no-value", SENTINELS[0] not in ran2.stdout + ran2.stderr)

    # -- 8. verify refuses in an agent environment, before the conf is read ----
    marker = AGENT_ENV_MARKERS[0]
    try:
        os.environ[marker] = ""
        fake = _FakeRunner([("", 0, "{}", "")])
        rc_r, out_r = _capture(main, ["verify", "--conf", "/nonexistent/chat-lane.conf"],
                               fake, _FakeSudo())
        expect("verify-refused-agent-env", rc_r == EX_REFUSED and "REFUSED" in out_r
               and not fake.argvs and "could not read" not in out_r, (rc_r, out_r))
        rc_r2, _o = _capture(cmd_verify, conf, fake, _FakeSudo())
        expect("cmd-verify-refused-agent-env", rc_r2 == EX_REFUSED and not fake.argvs)
        rc_c, _o = _capture(cmd_compose, conf)
        expect("compose-allowed-agent-env", rc_c == EX_OK)
    finally:
        os.environ.pop(marker, None)

    # -- a declined sudo names THIS script, with the conf that was passed. The text comes
    # from the Stage E installer's session, whose own rerun line names Stage E's verify —
    # a different installer, and one that would leave the chat lane unmeasured (review of
    # PR #147, finding 8).
    class _DecliningSudo(object):
        def acquire(self, why, resume):
            raise NoPrivilege("NO ADMINISTRATOR ACCESS — nothing was attempted.\n"
                              "  Fix that and run the same command again:\n"
                              "      python3 %s %s" % (_stage_e_self_path(), resume))

    with tempfile.TemporaryDirectory() as tmp3:
        cpath3 = os.path.join(tmp3, "chat-lane.conf")
        _put(cpath3, GOOD_CONF_TEXT)
        rc_np, out_np = _capture(main, ["verify", "--conf", cpath3],
                                 _FakeRunner([("", 0, "{}", "")]), _DecliningSudo())
        expect("no-sudo-exit-5", rc_np == EX_NOPRIV, rc_np)
        expect("no-sudo-names-this-script",
               "pipeline_stage_e_setup.py verify" not in out_np
               and "pipeline_chat_lane_setup.py verify --conf %s" % cpath3 in out_np,
               out_np[-400:])

    # cards
    for cid in ("CK-C1", "CK-C2", "CK-C3", "CK-C4"):
        rc_card, card_out = _capture(print_card, cid, conf)
        expect("card-" + cid, rc_card == EX_OK and "WHY THIS IS YOURS" in card_out
               and "NEVER" in card_out and "${" not in card_out, card_out[:200])
    _rc, c4 = _capture(print_card, "CK-C4", conf)
    flat_c4 = " ".join(c4.split())
    # The card must demand a REFUSAL, show curl's error, and refuse to read a timeout as
    # proof — a timeout is what an unreachable device looks like, and `block return` never
    # produces one (review of PR #147, finding 3).
    expect("card-c4-requires-a-refusal", all(t in flat_c4 for t in (
        "curl -sS -m 5 http://127.0.0.1:3456/status", "SECOND DEVICE",
        "<this machine's network address>:3456/status", "exit=7", "exit=28")), c4)
    expect("card-c4-shows-curl-errors", " -s " not in flat_c4 and "curl -s " not in flat_c4,
           "the card still hides curl's error text with -s")
    expect("card-c4-has-a-reachability-control",
           "A CONTROL" in flat_c4 and ":9/" in flat_c4, "no control step")
    expect("card-c4-timeout-is-not-proof",
           "Do NOT record the port as closed from a timeout" in flat_c4
           and "the rule is unproven" in flat_c4
           and "Good: exit=7" in flat_c4, "the card still treats a timeout as Good")
    expect("card-c4-good-line-wants-the-refusal",
           "refusal" in CARDS["CK-C4"]["good"] and "timed out" not in CARDS["CK-C4"]["good"],
           CARDS["CK-C4"]["good"])
    _rc, c3 = _capture(print_card, "CK-C3", conf)
    expect("card-c3-content", all(t in c3 for t in ("Monitor", "Task", "ScheduleWakeup",
                                                    "mcp__cyrus-tools", "expected")))
    _rc, c2 = _capture(print_card, "CK-C2", conf)
    flat_c2 = " ".join(c2.split())
    expect("card-c2-filled", "https://chat.example.com/slack-webhook" in c2)
    # CK-C2 must test a route that must ALWAYS be refused, not /status, which a front door
    # may forward on purpose (review of PR #147, finding 3). Since KIT-197 the card names
    # the door's own 404 as the expected answer, and a 401 as the failure.
    expect("card-c2-checks-a-never-route",
           "/api/update/cyrus-config" in flat_c2
           and "Good: 404" in flat_c2 and "Not that: 401 on this last one" in flat_c2
           and "may forward one on purpose" in flat_c2, c2)
    expect("card-c2-no-status-assumption",
           "curl -s -m 5 https://chat.example.com/status" not in flat_c2,
           "CK-C2 still assumes /status is refused")
    expect("card-unknown", _capture(main, ["card", "CK-9", "--conf", "/nonexistent"])[0]
           == EX_USAGE)

    # -- 11. the source: no write to a dispatcher path, no merge/label/state path
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split(SELFTEST_SENTINEL, 1)[0]
    # The owner's decision of 2026-09-24 (KIT-197): three writer PROGRAMS, run as the role
    # account, through ONE seam. Exactly their text, and the seam's, is exempt from the
    # write scan; every other line above the sentinel is still scanned. A program that is
    # not found verbatim (a post-processed string, say) exempts nothing and is reported.
    exempt = above
    for n, prog in enumerate(WRITER_PROGRAMS):
        expect("writer-program-verbatim-in-source:%d" % n, prog in exempt)
        exempt = exempt.replace(prog, "")
    seam = inspect.getsource(_role_write)
    expect("write-seam-verbatim-in-source", seam in exempt and "why=" in seam)
    exempt = exempt.replace(seam, "")
    above_scan = "\n".join(l for l in exempt.splitlines() if _BANNED_MARK not in l)
    whole_scan = "\n".join(l for l in src.splitlines() if _BANNED_MARK not in l)
    # …and the seam is reached from the three writing subcommands and from nowhere else:
    # once from merge and front-door, twice from env-names (its set and its --remove).
    callers = {fn.__name__: inspect.getsource(fn).count("_role_write(")
               for fn in (cmd_merge, cmd_env_names, cmd_front_door)}
    expect("write-seam-callers", above_scan.count("_role_write(") == 4
           and callers == {"cmd_merge": 1, "cmd_env_names": 2, "cmd_front_door": 1},
           (above_scan.count("_role_write("), callers))
    expect("write-seam-callers-refuse-agents", all(
        "agent_env_markers_present()" in inspect.getsource(fn)
        for fn in (cmd_merge, cmd_env_names, cmd_front_door)))
    for tok in WRITE_TOKENS:
        expect("no-write-token:" + tok, tok not in above_scan,
               "the non-test source holds %r" % tok)
    for tok in FORBIDDEN_TOKENS:
        expect("no-forbidden-token:" + tok, tok not in whole_scan,
               "this file holds %r" % tok)
    injected = above_scan + '\n    open(conf["DISPATCHER_CONFIG"], ' + WRITE_TOKENS[0] + ")\n"
    expect("write-scan-mutant", any(tok in injected for tok in WRITE_TOKENS))
    injected = whole_scan + "\n    run(['gh', '" + FORBIDDEN_TOKENS[0] + "'])\n"
    expect("forbidden-scan-mutant", any(tok in injected for tok in FORBIDDEN_TOKENS))
    expect("sentinel-present", SELFTEST_SENTINEL in src and len(above) < len(src))

    # -- KIT-197: the owner-run writers, the printed commands, the front-door row -----
    _selftest_kit197(expect, conf)

    if failures:
        for f in failures:
            say("FAIL " + str(f)[:600])
        say("chat-lane setup selftest: %d of %d cases FAILED" % (len(failures), cases[0]))
        return EX_FAILED
    say("ok — chat-lane setup selftest: %d cases, %d cards, %d checks, %d scopes"
        % (cases[0], len(CARDS), len(CHECKS), len(SCOPE_CITES)))
    return EX_OK


def _capture_verify_fail(vconf):
    fake = _FakeRunner([], default=(1, "", "sudo: a password is required"))
    rc_v, printed = _capture(cmd_verify, vconf, fake, _FakeSudo())
    return rc_v, printed, fake, None


# --------------------------------------------------------------------------- #
# KIT-197 — the three owner-run writers, the printed commands, the front-door row.
#
# Every writer here is RUN, not matched: `_RoleMachine` hands `sudo -u <role> -H /bin/sh -c`
# to a real /bin/sh with HOME set to a temporary role home, so the program a person's machine
# runs is the program this battery runs. Only pf and `sudo` itself are stood in for.
# --------------------------------------------------------------------------- #
# Obviously fake, assembled at run time: no literal of a credential's shape in the source.
FAKE_BOT_TOKEN = "xoxb-" + "FAKE" * 12
FAKE_SIGNING_SECRET = "deadbeef" * 4
FAKE_NOTIFIER_TOKEN = "xoxb-" + "NOTIFIERFAKE" * 4

FRONT_DOOR_FIXTURE = (
    "chat.example.com {\n"
    "\t@dispatcher path /linear-webhook /callback /status /extra-path\n"
    "\thandle @dispatcher {\n"
    "\t\treverse_proxy 127.0.0.1:3456\n"
    "\t}\n"
    "\trespond 404\n"
    "}\n")

# A stand-in for the front door's binary: logs its arguments, and fails validation while a
# flag file exists.
FAKE_PROXY_BIN = """#!/bin/sh
echo "$*" >> '%(log)s'
if [ -e '%(flag)s' ]; then echo "Error: adapting config: unrecognized directive"; exit 1; fi
echo "Valid configuration"
exit 0
"""

# launchd, curl and sleep, for running the PRINTED restart commands for real. `print` keeps
# answering "loaded" for SHIM_LINGER polls after a bootout, as launchd does for a job still
# inside its exit timeout, and a bootstrap into a domain that still holds the job fails with
# the errno 5 the Stage E installer met in production.
SUDO_SHIM = """#!/bin/sh
echo "sudo $*" >> "$SHIM_LOG"
[ "$1" = launchctl ] || exit 0
case "$2" in
print)
    [ -e "$SHIM_STATE/loaded" ] || exit 113
    if [ -e "$SHIM_STATE/linger" ]; then
        n=$(cat "$SHIM_STATE/linger")
        if [ "$n" -le 0 ]; then rm -f "$SHIM_STATE/loaded" "$SHIM_STATE/linger"; exit 113; fi
        echo $((n - 1)) > "$SHIM_STATE/linger"
    fi
    exit 0;;
bootout)
    [ -e "$SHIM_STATE/loaded" ] || { echo "Boot-out failed: 3: No such process" >&2; exit 3; }
    echo "$SHIM_LINGER" > "$SHIM_STATE/linger"
    exit 0;;
bootstrap)
    if [ -e "$SHIM_STATE/loaded" ]; then echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; fi
    : > "$SHIM_STATE/loaded"
    echo "bootstrapped" >> "$SHIM_LOG"
    exit 0;;
esac
exit 0
"""
CURL_SHIM = """#!/bin/sh
echo "curl $*" >> "$SHIM_LOG"
[ "$SHIM_CURL" = down ] && exit 7
printf '{"status":"%s"}' "$SHIM_CURL"
"""
SLEEP_SHIM = """#!/bin/sh
echo "sleep $*" >> "$SHIM_LOG"
"""


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _mode(path):
    return os.stat(path).st_mode & 0o777


def _shims(tmp, curl_says="idle", loaded=True, linger=2):
    bindir = os.path.join(tmp, "shim-bin")
    state = os.path.join(tmp, "shim-state-%d" % len(os.listdir(tmp)))
    log = os.path.join(state, "calls.log")
    os.makedirs(bindir, exist_ok=True)
    os.makedirs(state)
    if loaded:
        _put(os.path.join(state, "loaded"), "")
    _put(log, "")
    for name, body in (("sudo", SUDO_SHIM), ("curl", CURL_SHIM), ("sleep", SLEEP_SHIM)):
        path = os.path.join(bindir, name)
        _put(path, body)
        os.chmod(path, 0o755)
    env = {"PATH": bindir + ":/usr/bin:/bin", "SHIM_LOG": log, "SHIM_STATE": state,
           "SHIM_LINGER": str(linger), "SHIM_CURL": curl_says, "HOME": tmp}
    return env, log


def _run_shell(script, env):
    return subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True,
                          timeout=60)


def _shell_block(text, first, last):
    """The printed lines from the one that starts `first` to the one that starts `last`,
    inclusive, with the card's indentation taken off."""
    lines = [l.strip() for l in text.splitlines()]
    try:
        i = next(n for n, l in enumerate(lines) if l.startswith(first))
        j = next(n for n, l in enumerate(lines) if n > i and l.startswith(last))
    except StopIteration:
        return ""
    return "\n".join(lines[i:j + 1]) + "\n"


class _RoleMachine(_FakeRunner):
    """`sudo -u <role> -H /bin/sh -c <script>` runs through a REAL /bin/sh as this user, with
    HOME a temporary role home and /usr/bin/python3 swapped for this interpreter. pf's reads
    answer from the table. `before_write` runs just before each write reaches the machine:
    the moment the dispatcher might rewrite its own config."""

    def __init__(self, home, answers=(), before_write=None):
        _FakeRunner.__init__(self, answers)
        self.home = home
        self.before_write = before_write
        self.role_runs = []

    def write(self, why, argv, stdin=None, timeout=300, secret_stdin=False, cwd=None):
        if self.before_write is not None:
            self.before_write()
        return _FakeRunner.write(self, why, argv, stdin, timeout, secret_stdin, cwd)

    def _exec(self, argv, stdin, timeout, cwd=None, **kwargs):
        from pipeline_stage_e_setup import Result
        argv = list(argv)
        if argv[:2] == ["sudo", "-u"] and argv[3:6] == ["-H", "/bin/sh", "-c"] and len(argv) == 7:
            self.argvs.append(argv)
            script = argv[6].replace("/usr/bin/python3", shlex.quote(sys.executable))
            p = subprocess.run(["/bin/sh", "-c", script], input=stdin, capture_output=True,
                               text=True, timeout=timeout,
                               env={"HOME": self.home, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
            self.role_runs.append({"rc": p.returncode, "out": p.stdout, "err": p.stderr})
            return Result(p.returncode, p.stdout, p.stderr)
        return _FakeRunner._exec(self, argv, stdin, timeout, cwd=cwd, **kwargs)


def _merge_fixture(review_fenced=True, first_mcp=False):
    review_brief = REVIEW_BRIEF_FINGERPRINT + ". More."
    planning_brief = PLANNING_BRIEF_FINGERPRINT + ". More."
    first = {"id": "coding-a", "name": "a", "repositoryPath": REPO_ONE,
             "allowedTools": ["Read"] + (["mcp__github"] if first_mcp else []),
             "disallowedTools": ["Edit"],
             "labelPrompts": {"debugger": {"labels": ["Bug"], "disallowedTools": ["Write"]}}}
    return {
        "linearWorkspaces": {"ws": {"linearToken": SENTINELS[3]}},
        "defaultDisallowedTools": ["Bash(rm -rf *)"],
        "repositories": [
            first,
            {"id": "coding-b", "name": "b", "repositoryPath": REPO_TWO},
            {"id": "reviews-a", "name": "reviews-a", "appendInstruction": review_brief,
             "disallowedTools": ["Bash"] + (list(SLACK_FENCE_RULES) if review_fenced else [])},
            {"name": "stage-a-planning-plan", "appendInstruction": planning_brief,
             "disallowedTools": ["Edit"]},
        ],
        "promptDefaults": {"scoper": {"disallowedTools": ["Write"]}},
    }


# Runs a writer program's REAL text with `os.fsync` wrapped: on its Nth call the wrapper
# overwrites a file, as the dispatcher does when it stores a refreshed tracker token. The
# writers fsync the backup first and the written file second, so N picks the moment.
_FSYNC_HOOK = '''
import os as _hook_os
_hook_real = _hook_os.fsync
_hook_calls = [0]
def _hook_fsync(fd):
    _hook_real(fd)
    _hook_calls[0] += 1
    act = _HOOKS.get(_hook_calls[0])
    if act:
        with open(act[0], "w", encoding="utf-8") as fh:
            fh.write(act[1])
_hook_os.fsync = _hook_fsync
'''


def _run_hooked(program, args, stdin, home, hooks):
    return subprocess.run([sys.executable, "-c", "_HOOKS = %r\n" % hooks + _FSYNC_HOOK + program]
                          + list(args), input=stdin, capture_output=True, text=True,
                          timeout=60, cwd="/", env={"HOME": home, "PATH": "/usr/bin:/bin"})


def _fenced_fixture():
    """`_merge_fixture` with piece 2 applied, as `merge --apply` leaves it: every non-review
    entry and every prompt type carries both Slack rules."""
    doc = _merge_fixture()
    fence = list(SLACK_FENCE_RULES)
    first, second, _review, planning = doc["repositories"]
    first["disallowedTools"] = first["disallowedTools"] + fence
    first["labelPrompts"]["debugger"]["disallowedTools"] += fence
    second["disallowedTools"] = doc["defaultDisallowedTools"] + fence
    planning["disallowedTools"] = planning["disallowedTools"] + fence
    doc["promptDefaults"]["scoper"]["disallowedTools"] += fence
    return doc


# The words a secret-carrying variable may follow in the env writer: shell builtins, and a
# plain assignment. `echo` is left out on purpose: it would print the value.
_BUILTIN_WORDS = ("printf", "[", "case", "read", "assign")
_SECRET_VAR = re.compile(r"\$\{?(?:T|S|l|v)(?![A-Za-z0-9_])")


def _value_commands(script):
    """The command word of every shell segment that mentions a secret-carrying variable
    ("assign" for a segment that only assigns). The notifier installer's check, with `${`
    kept whole so `"${T}"` is still seen."""
    words = []
    for seg in re.split(r";|&&|\|\||\||\(|\)|(?<!\$)\{|\}|\n|\bthen\b|\bdo\b|\belse\b|\bif\b",
                        script):
        if not _SECRET_VAR.search(seg):
            continue
        parts = [w for w in seg.split() if w not in ("if", "!", "while", "elif")]
        while parts and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parts[0]):
            parts = parts[1:] if len(parts) > 1 else ["assign"]
        words.append(parts[0] if parts else "")
    return words


def _selftest_kit197(expect, conf):
    """The owner-run writers (merge, env-names, front-door), the printed commands filled from
    the conf, and verify's front-door row. Each group records a crash as a failed case rather
    than stopping the battery, so a missing piece is counted, not hidden."""
    def group(name, fn):
        try:
            fn()
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — a crash is a failed case
            expect("crashed:" + name, False, "%s: %s" % (type(exc).__name__, str(exc)[:400]))

    minimal = validate_conf(parse_conf(MINIMAL_CONF_TEXT)[0])[0]

    # -- the conf: six new keys, each validated, all optional -------------------------
    def conf_keys():
        values, perrs = parse_conf(GOOD_CONF_TEXT)
        full, verrs = validate_conf(values)
        expect("conf-new-keys-accepted", not perrs and not verrs
               and full.get("DISPATCHER_SERVICE") == "com.example.dispatcher"
               and full.get("FRONT_DOOR_MATCHER") == "@dispatcher"
               and full.get("FRONT_DOOR_BIN") == "/opt/homebrew/bin/caddy", perrs + verrs)
        expect("conf-role-env-file-default", full.get("ROLE_ENV_FILE") == "~/.stage-e/env",
               full.get("ROLE_ENV_FILE"))
        _m, merrs = validate_conf(parse_conf(MINIMAL_CONF_TEXT)[0])
        expect("conf-new-keys-optional", not merrs and not _m.get("FRONT_DOOR_CONFIG"), merrs)
        bad = ("DISPATCHER_SERVICE=not a label\n"
               "FRONT_DOOR_SERVICE=frontdoor\n"
               "FRONT_DOOR_CONFIG=relative/Caddyfile\n"
               "FRONT_DOOR_BIN=caddy\n"
               "FRONT_DOOR_MATCHER=dispatcher\n"
               "ROLE_ENV_FILE=.stage-e/env\n")
        values, perrs = parse_conf(MINIMAL_CONF_TEXT + bad)
        allerrs = perrs + validate_conf(values)[1]
        for needle in ("DISPATCHER_SERVICE", "FRONT_DOOR_SERVICE",
                       "FRONT_DOOR_CONFIG must be an absolute path",
                       "FRONT_DOOR_BIN must be an absolute path", "FRONT_DOOR_MATCHER",
                       "ROLE_ENV_FILE"):
            expect("conf-new-key-error:" + needle, any(needle in e for e in allerrs), allerrs)
        values, _p = parse_conf(MINIMAL_CONF_TEXT + "DISPATCHER_SERVICE=com.example.same\n"
                                "FRONT_DOOR_SERVICE=com.example.same\n")
        errs = validate_conf(values)[1]
        expect("conf-two-services-differ", any("the same service" in e for e in errs), errs)
    group("conf", conf_keys)

    # -- piece 5: the backups folder and the role account's own env file are denied too ----
    def deny_rules():
        rules = user_deny_patterns(conf)
        expect("deny-backups-rule", "Read(~/.stage-e/backups/**)" in rules, rules)
        expect("deny-role-env-rule-default", "Read(~/.stage-e/env)" in rules, rules)
        other = user_deny_patterns(dict(conf, ROLE_ENV_FILE="/srv/role/env"))
        expect("deny-role-env-rule-absolute", "Read(//srv/role/env)" in other
               and "Read(~/.stage-e/env)" not in other, other)
        us = {"path": "/h/.claude/settings.json",
              "deny": [r for r in rules if r != "Read(~/.stage-e/backups/**)"]}
        row = check_user_settings(conf, us, {"names": []})
        expect("verify-user-settings-wants-backups-rule", row["outcome"] == BLOCKED
               and "Read(~/.stage-e/backups/**)" in _row_text(row), row)
    group("deny", deny_rules)

    # -- compose --piece N, and piece 4 as a script alone ------------------------------
    def pieces():
        with tempfile.TemporaryDirectory() as tmp:
            cpath = os.path.join(tmp, "chat-lane.conf")
            _put(cpath, GOOD_CONF_TEXT)
            for n in range(1, 8):
                rc, out = _capture(main, ["compose", "--piece", str(n), "--conf", cpath])
                others = [m for m in range(1, 8) if m != n and ("PIECE %d " % m) in out]
                if n == 4:
                    expect("compose-piece-4-is-only-the-script",
                           rc == EX_OK and out == "\n".join(pf_install_commands("3456")) + "\n"
                           and PF_COPY_START not in out, out[:200])
                else:
                    expect("compose-piece-%d-alone" % n,
                           rc == EX_OK and ("PIECE %d " % n) in out and not others
                           and "THE ORDER" not in out, (rc, others, out[:120]))
            rc, _o = _capture(main, ["compose", "--piece", "9", "--conf", cpath])
            expect("compose-piece-out-of-range", rc == EX_USAGE, rc)
            rc, out4 = _capture(main, ["compose", "--piece", "4", "--conf", cpath])
            check = subprocess.run(["/bin/bash", "-n"], input=out4, capture_output=True,
                                   text=True, timeout=10)
            expect("compose-piece-4-parses-as-shell", rc == EX_OK and check.returncode == 0,
                   check.stderr[-200:])
    group("pieces", pieces)

    # -- piece 4 is re-runnable: a loaded job is booted out and waited for -------------
    def pf_rerun():
        launchd = "\n".join(l for l in pf_install_commands("3456") if "launchctl" in l) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            for loaded in (True, False):
                env, log = _shims(tmp, loaded=loaded, linger=2)
                ran = _run_shell(launchd, env)
                calls = _read(log)
                label = "loaded" if loaded else "not-loaded"
                booted_out = "launchctl bootout system/%s" % PF_DAEMON_LABEL in calls
                expect("pf-rerun-%s-bootstraps" % label, "bootstrapped" in calls
                       and "Bootstrap failed" not in ran.stderr, (calls, ran.stderr[-200:]))
                expect("pf-rerun-%s-bootout" % label, booted_out == loaded, calls)
                if loaded:
                    expect("pf-rerun-waits-until-gone",
                           calls.find("launchctl bootout") < calls.rfind("launchctl print")
                           < calls.find("bootstrapped"), calls)
            # the old job never lets go within the bound: the script says so before the
            # bootstrap fails, and `sh -e` stops there (review 41)
            env, log = _shims(tmp, loaded=True, linger=40)
            ran = subprocess.run(["/bin/sh", "-e", "-c", launchd], env=env, capture_output=True,
                                 text=True, timeout=60)
            expect("pf-rerun-says-when-the-wait-ran-out", ran.returncode != 0
                   and "STILL LOADED" in ran.stdout + ran.stderr
                   and "Bootstrap failed" in ran.stderr, (ran.returncode, ran.stdout[-200:],
                                                          ran.stderr[-200:]))
    group("pf-rerun", pf_rerun)

    # -- CK-C5: restart the dispatcher only when it says it is idle -------------------
    def card_c5():
        rc, out = _capture(print_card, "CK-C5", conf)
        flat = " ".join(out.split())
        expect("card-c5-exists", rc == EX_OK and "WHY THIS IS YOURS" in out and "${" not in out,
               out[:200])
        expect("card-c5-cites-the-idle-answer", "EdgeWorker.js:1784-1802" in flat, flat[:300])
        expect("card-c5-log-path-from-the-plist",
               "/usr/libexec/PlistBuddy -c 'Print :StandardOutPath' "
               "/Library/LaunchDaemons/com.example.dispatcher.plist" in out, out)
        block = _shell_block(out, "S=$(curl", "fi")
        expect("card-c5-gate-block-found", "127.0.0.1:3456/status" in block
               and "grep -q '\"idle\"'" in block, block)
        with tempfile.TemporaryDirectory() as tmp:
            for says in ("idle", "busy"):
                env, log = _shims(tmp, curl_says=says, loaded=True, linger=2)
                ran = _run_shell(block, env)
                calls = _read(log)
                if says == "idle":
                    expect("card-c5-idle-restarts",
                           "launchctl bootout system/com.example.dispatcher" in calls
                           and "bootstrapped" in calls and "Bootstrap failed" not in ran.stderr
                           and calls.find("launchctl bootout") < calls.rfind("launchctl print")
                           < calls.find("bootstrapped"), (calls, ran.stderr[-200:]))
                else:
                    expect("card-c5-%s-does-not-restart" % says,
                           "launchctl bootout" not in calls and "bootstrapped" not in calls
                           and "NOT RESTARTED" in ran.stdout, (calls, ran.stdout))
            # nothing answers: "busy" and "dead" are told apart, so CK-C2's 502 remedy is
            # not a loop (review 31). Launchd holds it: its state and log, and no restart.
            env, log = _shims(tmp, curl_says="down", loaded=True, linger=2)
            ran = _run_shell(block, env)
            calls = _read(log)
            expect("card-c5-not-answering-shows-state-and-log",
                   "NOT ANSWERING" in ran.stdout and "NOT RESTARTED" not in ran.stdout
                   and "launchctl print system/com.example.dispatcher" in calls
                   and "sudo tail -n 40" in calls and "launchctl bootout" not in calls
                   and "bootstrapped" not in calls, (calls, ran.stdout[-300:]))
            # launchd does not hold it: nothing runs, so nothing is lost by starting it
            env, log = _shims(tmp, curl_says="down", loaded=False)
            ran = _run_shell(block, env)
            calls = _read(log)
            expect("card-c5-not-loaded-starts-it", "NOT LOADED" in ran.stdout
                   and "bootstrapped" in calls and "launchctl bootout" not in calls,
                   (calls, ran.stdout[-300:]))
            # the forced restart the card offers after NOT ANSWERING stops, waits, starts
            forced = _shell_block(out, "echo 'FORCED RESTART", "sudo tail")
            env, log = _shims(tmp, loaded=True, linger=2)
            ran = _run_shell(forced, env)
            calls = _read(log)
            expect("card-c5-forced-restart-waits", forced and "curl" not in forced
                   and calls.find("launchctl bootout") < calls.rfind("launchctl print")
                   < calls.find("bootstrapped") and "Bootstrap failed" not in ran.stderr,
                   (forced, calls))
        flat = " ".join(out.split())
        expect("card-c5-reads-not-answering-apart-from-busy",
               "NOT ANSWERING" in flat and "NOT LOADED" in flat
               and "NOT ANSWERING" in CARDS["CK-C5"]["not"], flat[-900:])
        rc, out_min = _capture(print_card, "CK-C5", minimal)
        expect("card-c5-not-composed-without-the-key",
               "not composed: set DISPATCHER_SERVICE in chat-lane.conf" in out_min
               and "launchctl bootout" not in out_min and "${" not in out_min, out_min)
    group("card-c5", card_c5)

    # -- CK-C2: the subcommand, the front door's restart, three probes ----------------
    def card_c2():
        rc, out = _capture(print_card, "CK-C2", conf)
        flat = " ".join(out.split())
        for needle in ("front-door --apply",
                       "sudo launchctl bootout system/com.example.front-door",
                       "sudo launchctl bootstrap system "
                       "/Library/LaunchDaemons/com.example.front-door.plist",
                       "curl -sS -m 10", "https://chat.example.com/slack-webhook",
                       "https://chat.example.com/linear-webhook",
                       "https://chat.example.com/api/update/cyrus-config",
                       "Good: 401", "Good: 404", "530", "502"):
            expect("card-c2-has:" + needle, needle in flat, needle)
        block = _shell_block(out, "sudo launchctl bootout", "sudo launchctl bootstrap")
        with tempfile.TemporaryDirectory() as tmp:
            env, log = _shims(tmp, loaded=True, linger=3)
            ran = _run_shell(block, env)
            calls = _read(log)
            expect("card-c2-restart-waits", "bootstrapped" in calls
                   and "Bootstrap failed" not in ran.stderr, (block, calls))
        c2_502 = flat[flat.find("502:"):flat.find("502:") + 260]
        expect("card-c2-502-names-not-answering", "NOT ANSWERING" in c2_502, c2_502)
        rc, out_min = _capture(print_card, "CK-C2", minimal)
        expect("card-c2-not-composed-without-the-keys",
               "not composed: set FRONT_DOOR_SERVICE in chat-lane.conf" in out_min
               and "launchctl bootout" not in out_min and "${" not in out_min
               and "https://chat.example.com/slack-webhook" in out_min, out_min)
    group("card-c2", card_c2)

    # -- CK-C6: turning the lane off, in order ----------------------------------------
    def card_c6():
        rc, out = _capture(print_card, "CK-C6", conf)
        flat = " ".join(out.split())
        steps = ("Uninstall", "env-names --remove", "S=$(curl", "front-door --remove --apply",
                 "bootout system/com.example.front-door", "/slack-webhook",
                 "pfctl -a %s -s rules" % PF_ANCHOR)
        at = [flat.find(s) for s in steps]
        expect("card-c6-in-order", rc == EX_OK and all(a >= 0 for a in at) and at == sorted(at),
               list(zip(steps, at)))
        expect("card-c6-keeps-the-fences", "Keep the fences, the grant and the port block"
               in flat, flat[-600:])
        expect("card-c6-probes-say-404-then-401", "Good: 404" in flat and "Good: 401" in flat)
    group("card-c6", card_c6)

    # -- CK-C1 leaves the secrets in Slack; CK-C3 tests calls, not the listing ---------
    def cards_c1_c3():
        _rc, c1 = _capture(print_card, "CK-C1", conf)
        flat1 = " ".join(c1.split())
        expect("card-c1-no-env-edit-at-creation", "SLACK_BOT_TOKEN <-" not in flat1
               and "env-names" in flat1 and "stay in Slack" in flat1, flat1)
        _rc, c3 = _capture(print_card, "CK-C3", conf)
        flat3 = " ".join(c3.split())
        for needle in ("KIT-196", "Monitor", "Write", "touch", "RAN", "refused",
                       "log_failure_mode", "CYRUS_API_KEY", "echo"):
            expect("card-c3-says:" + needle, needle in flat3, needle)
        expect("card-c3-tests-calls-not-the-listing",
               "list the name of every tool" not in flat3, flat3[:300])
        do3 = "\n".join(CARDS["CK-C3"]["do"])
        expect("card-c3-reads-no-credential-file",
               not re.search(r"\.stage-e/env|DISPATCHER_ENV_FILE|DISPATCHER_CONFIG|\.env\b"
                             r"|config\.json", do3), do3)
        for cid, card in CARDS.items():
            refs = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", json.dumps(card["do"])))
            expect("card-keys-known:" + cid, refs <= CONF_KEYS, refs - CONF_KEYS)
    group("cards-c1-c3", cards_c1_c3)

    # -- a card under a conf with problems names them; it never says "set" a key that is
    # set, nor "no chat-lane.conf loaded" when one was (review 39) ---------------------
    def card_bad_conf():
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "chat-lane.conf")
            _put(bad, GOOD_CONF_TEXT + "FRONT_DOOR_MATCHER2=@x\n")
            rc, out = _capture(main, ["card", "CK-C5", "--conf", bad])
            expect("card-bad-conf-names-the-problem", rc == EX_USAGE
                   and "unknown key FRONT_DOOR_MATCHER2" in out
                   and "set DISPATCHER_SERVICE" not in out and "set DISPATCHER_PORT" not in out
                   and "no chat-lane.conf loaded" not in out and "fix chat-lane.conf" in out
                   and "launchctl bootout" not in out, (rc, out[:900]))
            rc, out = _capture(main, ["card", "CK-C5", "--conf",
                                      os.path.join(tmp, "absent.conf")])
            expect("card-without-a-conf-still-prints", rc == EX_OK
                   and "no chat-lane.conf loaded" in out
                   and "not composed: set DISPATCHER_SERVICE" in out, (rc, out[:400]))
    group("card-bad-conf", card_bad_conf)

    # -- THE ORDER uses the subcommands ----------------------------------------------
    def order():
        _rc, out = _capture(cmd_compose, conf)
        tail = " ".join(out[out.find("THE ORDER"):].split())
        for needle in ("merge --apply", "compose --piece 4", "env-names", "card CK-C5",
                       "front-door --apply", "card CK-C6"):
            expect("order-uses:" + needle, needle in tail, needle)
        at = [tail.find(s) for s in ("merge --apply", "compose --piece 4", "env-names",
                                     "card CK-C5")]
        expect("order-merge-pf-env-restart", all(a >= 0 for a in at) and at == sorted(at), at)
    group("order", order)

    # -- the allowlist line: one function edits it, byte for byte ---------------------
    def front_lines():
        m = "@dispatcher"
        for text, remove, want in (
                ("\t@dispatcher path /linear-webhook /status", False,
                 "\t@dispatcher path /linear-webhook /status /slack-webhook"),
                ("  @dispatcher path /a /b  # keep this", False,
                 "  @dispatcher path /a /b /slack-webhook  # keep this"),
                ("@dispatcher path /a /slack-webhook", False, "@dispatcher path /a /slack-webhook"),
                ("@dispatcher path /a /slack-webhook /b", True, "@dispatcher path /a /b"),
                ("@dispatcher path /slack-webhook /a", True, "@dispatcher path /a"),
                ("@dispatcher path /a /slack-webhook-old", True,
                 "@dispatcher path /a /slack-webhook-old")):
            got = front_line_edit(text, m, SLACK_PATH, remove)
            expect("front-line-edit:%r" % text[:40] + (":remove" if remove else ""), got == want,
                   got)
        expect("front-line-remove-last-path-refused",
               front_line_edit("@dispatcher path /slack-webhook", m, SLACK_PATH, True) is None)
        expect("front-line-only-the-named-matcher",
               front_line_parts("@other path /a", m) is None
               and front_line_parts("@dispatcher path_regexp x", m) is None
               and front_line_parts("# @dispatcher path /a", m) is None
               and front_line_parts("@dispatcherx path /a", m) is None)
        parts = front_line_parts("  @dispatcher path /a /b  # c", m)
        expect("front-line-parts-rejoin", parts and "".join(parts) == "  @dispatcher path /a /b  # c"
               and parts[1].split() == ["/a", "/b"], parts)
        expect("front-routes-cited", set(EXPECTED_ROUTES) == {"/linear-webhook", "/callback",
                                                              "/status", "/slack-webhook"}
               and all(".js:" in v for v in EXPECTED_ROUTES.values()), EXPECTED_ROUTES)
    group("front-lines", front_lines)

    # -- verify's front-door row ------------------------------------------------------
    def verify_row():
        row = check_front_door(minimal, None)
        expect("verify-front-door-unset-keys", row["outcome"] == UNKNOWN
               and "NOT MEASURED" in row["detail"] and "FRONT_DOOR_CONFIG" in row["detail"]
               and "FRONT_DOOR_MATCHER" in row["detail"], row)
        good = {"count": 1, "line": 2, "sha256": "0" * 64,
                "text": "@dispatcher path /linear-webhook /slack-webhook",
                "paths": ["/linear-webhook", "/callback", "/status", "/slack-webhook"]}
        row = check_front_door(conf, good)
        expect("verify-front-door-done", row["outcome"] == ALREADY_DONE, row)
        row = check_front_door(conf, dict(good, paths=good["paths"] + ["/extra-path"]))
        expect("verify-front-door-names-an-unknown-path", row["outcome"] == ALREADY_DONE
               and "/extra-path" in _row_text(row), row)
        # a path that forwards the config-update route or the tool server, or a wildcard
        # that may, is drift, not a note (review 46)
        for wide in ("/*", "/api/*", "/mcp/*", "*", "/mcp/cyrus-tools", "/MCP/cyrus-tools",
                     "/api/update/cyrus-config", "/api/update/other"):
            row = check_front_door(conf, dict(good, paths=good["paths"] + [wide]))
            expect("verify-front-door-blocks-a-widening-path:" + wide,
                   row["outcome"] == BLOCKED and wide in _row_text(row), row)
        row = check_front_door(conf, dict(good, paths=["/linear-webhook", "/status"]))
        expect("verify-front-door-slack-missing", row["outcome"] == BLOCKED
               and "/slack-webhook" in _row_text(row), row)
        row = check_front_door(conf, dict(good, paths=["/slack-webhook"]))
        expect("verify-front-door-tracker-missing", row["outcome"] == BLOCKED
               and "/linear-webhook" in _row_text(row), row)
        for n in (0, 2):
            row = check_front_door(conf, {"count": n, "sha256": "0" * 64})
            expect("verify-front-door-%d-lines" % n, row["outcome"] == BLOCKED
                   and str(n) in row["detail"], row)
        expect("verify-front-door-missing-file",
               check_front_door(conf, {"error": "missing"})["outcome"] == BLOCKED)
        expect("verify-front-door-unreadable",
               check_front_door(conf, {"error": "unreadable"})["outcome"] == UNKNOWN)
        expect("verify-checks-include-front-door", "front-door" in CHECKS, CHECKS)
    group("verify-row", verify_row)

    # -- merge: the plan, through the REAL probe, and the REAL writer -----------------
    def merge_real():
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "role-home")
            os.makedirs(home)
            cfg_path = os.path.join(tmp, "dispatcher", "config.json")
            env_path = os.path.join(tmp, "dispatcher", ".env")
            mconf = dict(conf, DISPATCHER_CONFIG=cfg_path, DISPATCHER_ENV_FILE=env_path,
                         FRONT_DOOR_CONFIG=os.path.join(tmp, "front", "Caddyfile"))
            fixture = _merge_fixture(review_fenced=True)
            before = json.dumps(fixture, indent=2) + "\n"
            _put(cfg_path, before)
            _put(env_path, "CYRUS_HOST_EXTERNAL=true\n")
            spath = os.path.join(home, ".claude", "settings.json")
            old_settings = json.dumps({"env": {"SECRET_THING": SENTINELS[3]},
                                       "permissions": {"deny": ["Read(~/.ssh/**)"]}},
                                      indent=4) + "\n"
            _put(spath, old_settings)
            inode = os.stat(cfg_path).st_ino

            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_merge, mconf, mach, _FakeSudo(), False)
            expect("merge-dry-run-exit-10", rc == EX_BLOCKED, (rc, out[-400:]))
            expect("merge-dry-run-writes-nothing", mach.writes == []
                   and _read(cfg_path) == before and _read(spath) == old_settings)
            for needle in ("coding-a", "coding-b", "stage-a-planning-plan", "slackAllowedTools",
                           "debugger", "scoper", "Read(~/.stage-e/backups/**)"):
                expect("merge-dry-run-names:" + needle, needle in out, needle)
            expect("merge-dry-run-no-value", SENTINELS[3] not in out)

            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_merge, mconf, mach, _FakeSudo(), True)
            after = json.loads(_read(cfg_path))
            repos = after.get("repositories") or [{}, {}, {}, {}]
            expect("merge-apply-exit-0", rc == EX_OK, out[-800:])
            expect("merge-apply-in-place", os.stat(cfg_path).st_ino == inode)
            expect("merge-apply-fences-coding", repos[0].get("disallowedTools")
                   == ["Edit", "mcp__slack", "mcp__slack__*"], repos[0])
            expect("merge-apply-keeps-inherited-default", repos[1].get("disallowedTools")
                   == ["Bash(rm -rf *)", "mcp__slack", "mcp__slack__*"], repos[1])
            expect("merge-apply-review-entry-untouched", repos[2] == fixture["repositories"][2],
                   repos[2])
            expect("merge-apply-fences-planning", repos[3].get("disallowedTools")
                   == ["Edit", "mcp__slack", "mcp__slack__*"], repos[3])
            expect("merge-apply-prompt-types",
                   repos[0].get("labelPrompts", {}).get("debugger", {}).get("disallowedTools")
                   == ["Write", "mcp__slack", "mcp__slack__*"]
                   and after.get("promptDefaults", {}).get("scoper", {}).get("disallowedTools")
                   == ["Write", "mcp__slack", "mcp__slack__*"], after.get("promptDefaults"))
            expect("merge-apply-grant-is-verifys",
                   after.get("slackAllowedTools") == owner_grant([REPO_ONE, REPO_TWO]),
                   after.get("slackAllowedTools"))
            expect("merge-apply-keeps-the-token", after.get("linearWorkspaces")
                   == fixture["linearWorkspaces"])
            text = _read(cfg_path)
            expect("merge-apply-indent-and-newline", text.endswith("}\n")
                   and text.splitlines()[1].startswith('  "')
                   and not text.splitlines()[1].startswith('   '), text[:80])
            settings = json.loads(_read(spath))
            deny = settings.get("permissions", {}).get("deny", [])
            expect("merge-apply-settings-merged", settings.get("env") == {"SECRET_THING":
                                                                           SENTINELS[3]}
                   and deny[:1] == ["Read(~/.ssh/**)"]
                   and all(r in deny for r in user_deny_patterns(mconf)), deny)
            expect("merge-apply-settings-keeps-indent",
                   _read(spath).splitlines()[1].startswith('    "'), _read(spath)[:60])
            expect("merge-apply-settings-mode-600", _mode(spath) == 0o600, oct(_mode(spath)))
            bdir = os.path.join(home, ".stage-e", "backups")
            bks = [b for b in (os.listdir(bdir) if os.path.isdir(bdir) else [])
                   if b.startswith("user-settings.json.")]
            expect("merge-apply-settings-backed-up", len(bks) == 1
                   and _read(os.path.join(bdir, bks[0])) == old_settings
                   and _mode(os.path.join(bdir, bks[0])) == 0o600, bks)
            expect("merge-apply-prints-verify-rows",
                   re.search(r"grant\s+ALREADY-DONE", out)
                   and re.search(r"coding-fence\s+ALREADY-DONE", out)
                   and re.search(r"user-settings\s+ALREADY-DONE", out), out[-800:])
            expect("merge-apply-one-write-the-writer", len(mach.writes) == 1
                   and mach.writes[0]["argv"][:6] == ["sudo", "-u", "_exdispatch", "-H",
                                                       "/bin/sh", "-c"]
                   and mach.writes[0]["argv"][6] == "cd / && " + merge_writer_command(mconf),
                   [w["argv"][:6] for w in mach.writes])
            expect("merge-apply-no-value", SENTINELS[3] not in out)

            cfg_snap, set_snap = _read(cfg_path), _read(spath)
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_merge, mconf, mach, _FakeSudo(), True)
            expect("merge-idempotent", rc == EX_OK and mach.writes == []
                   and _read(cfg_path) == cfg_snap and _read(spath) == set_snap
                   and "Nothing to write" in out, (rc, out[-300:]))

            # a fresh home with no settings file: made at 600, in a folder made at 700
            home2 = os.path.join(tmp, "role-home-2")
            os.makedirs(home2)
            _put(cfg_path, before)
            rc, out = _capture(cmd_merge, mconf, _RoleMachine(home2, _pf_answers()),
                               _FakeSudo(), True)
            spath2 = os.path.join(home2, ".claude", "settings.json")
            expect("merge-creates-settings-600-in-700", rc == EX_OK and os.path.exists(spath2)
                   and _mode(spath2) == 0o600 and _mode(os.path.dirname(spath2)) == 0o700,
                   (rc, out[-300:]))
    group("merge-real", merge_real)

    def merge_moved():
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "role-home")
            os.makedirs(home)
            cfg_path = os.path.join(tmp, "dispatcher", "config.json")
            mconf = dict(conf, DISPATCHER_CONFIG=cfg_path,
                         DISPATCHER_ENV_FILE=os.path.join(tmp, "dispatcher", ".env"),
                         FRONT_DOOR_CONFIG=os.path.join(tmp, "front", "Caddyfile"))
            _put(cfg_path, json.dumps(_merge_fixture(), indent=2) + "\n")

            def refresh():
                # What the dispatcher does between the read and the write: it rewrites its
                # own config to store a refreshed tracker token.
                doc = json.loads(_read(cfg_path))
                doc["linearWorkspaces"]["ws"]["linearToken"] = SENTINELS[3] + "-refreshed"
                _put(cfg_path, json.dumps(doc, indent=2) + "\n")
            mach = _RoleMachine(home, _pf_answers(), before_write=refresh)
            rc, out = _capture(cmd_merge, mconf, mach, _FakeSudo(), True)
            moved = json.loads(_read(cfg_path))
            expect("merge-sha-mismatch-refused", rc == EX_REFUSED and "changed" in out,
                   (rc, out[-400:]))
            expect("merge-sha-mismatch-wrote-nothing",
                   moved["repositories"][0]["disallowedTools"] == ["Edit"]
                   and moved["linearWorkspaces"]["ws"]["linearToken"].endswith("-refreshed")
                   and not os.path.exists(os.path.join(home, ".claude", "settings.json")),
                   moved["repositories"][0])
            expect("merge-sha-mismatch-no-value", SENTINELS[3] not in out)

            # The writer checks each entry's index AND id, whatever the checksum says.
            raw = _read_bytes(cfg_path)
            import hashlib
            sha = hashlib.sha256(raw).hexdigest()
            script = merge_writer_command(mconf).replace("/usr/bin/python3",
                                                         shlex.quote(sys.executable))
            for label, op, want_rc in (
                    ("wrong-id", {"op": "fence-entry", "index": 0, "id": "someone-else",
                                  "name": "a", "from": "own", "add": list(SLACK_FENCE_RULES)}, 3),
                    ("wrong-index", {"op": "fence-entry", "index": 9, "id": "coding-a",
                                     "name": "a", "from": "own",
                                     "add": list(SLACK_FENCE_RULES)}, 3),
                    ("right", {"op": "fence-entry", "index": 0, "id": "coding-a", "name": "a",
                               "from": "own", "add": list(SLACK_FENCE_RULES)}, 0)):
                ran = subprocess.run(["/bin/sh", "-c", "cd / && " + script],
                                     input=json.dumps({"configSha256": sha, "config": [op],
                                                       "deny": []}),
                                     capture_output=True, text=True, timeout=60,
                                     env={"HOME": home, "PATH": "/usr/bin:/bin"})
                changed = _read_bytes(cfg_path) != raw
                expect("merge-writer-entry-identity:" + label, ran.returncode == want_rc
                       and changed == (want_rc == 0), (ran.returncode, ran.stdout[-300:],
                                                       ran.stderr[-300:]))
    group("merge-moved", merge_moved)

    def merge_hand():
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "role-home")
            os.makedirs(home)
            cfg_path = os.path.join(tmp, "dispatcher", "config.json")
            mconf = dict(conf, DISPATCHER_CONFIG=cfg_path,
                         DISPATCHER_ENV_FILE=os.path.join(tmp, "dispatcher", ".env"),
                         FRONT_DOOR_CONFIG=os.path.join(tmp, "front", "Caddyfile"))
            fixture = _merge_fixture(review_fenced=False, first_mcp=True)
            _put(cfg_path, json.dumps(fixture, indent=2) + "\n")
            rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()), _FakeSudo(),
                               False)
            review_lines = [l.strip() for l in out.splitlines() if "reviews-a" in l]
            expect("merge-names-the-unfenced-review-entry",
                   review_lines and not any(l.startswith("FENCE") for l in review_lines)
                   and "run the Stage E installer" in " ".join(out.split()), review_lines)
            expect("merge-warns-first-entry-mcp", "WARNING" in out and "mcp__github" in out,
                   out[-500:])
            rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()), _FakeSudo(),
                               True)
            after = json.loads(_read(cfg_path))
            expect("merge-leaves-review-and-mcp-alone",
                   after["repositories"][2] == fixture["repositories"][2]
                   and "mcp__github" in after["repositories"][0]["allowedTools"]
                   and rc == EX_BLOCKED, (rc, out[-500:]))
            # a review entry with no list of its own is fenced by an inherited default that
            # carries both rules: merge agrees with verify and does not name it
            inheriting = _merge_fixture()
            del inheriting["repositories"][2]["disallowedTools"]
            inheriting["defaultDisallowedTools"] = ["Bash(rm -rf *)"] + list(SLACK_FENCE_RULES)
            _put(cfg_path, json.dumps(inheriting, indent=2) + "\n")
            rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()), _FakeSudo(),
                               False)
            review_lines = [l.strip() for l in out.splitlines() if "reviews-a" in l]
            expect("merge-review-entry-inheriting-the-fence-is-ok",
                   review_lines and review_lines[0].startswith("ok")
                   and "LEFT ALONE" not in out, review_lines)
    group("merge-hand", merge_hand)

    # -- merge's edges: what it must not compose, and what it must not call "in place" --
    def merge_edges():
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "role-home")
            os.makedirs(home)
            cfg_path = os.path.join(tmp, "dispatcher", "config.json")
            env_path = os.path.join(tmp, "dispatcher", ".env")
            spath = os.path.join(home, ".claude", "settings.json")
            mconf = dict(conf, DISPATCHER_CONFIG=cfg_path, DISPATCHER_ENV_FILE=env_path,
                         FRONT_DOOR_CONFIG=os.path.join(tmp, "front", "Caddyfile"))

            # DISALLOWED_TOOLS in the env file replaces defaultDisallowedTools at start
            # (WorkerService.js:164-165): an entry that inherits runs under a list no file
            # holds, so merge composes nothing for it — a list built from the file would
            # drop every deny the env list supplies, and verify would then pass (review 29)
            doc = {"defaultDisallowedTools": ["Bash(rm -rf *)"],
                   "repositories": [
                       {"id": "coding-own", "name": "own", "repositoryPath": REPO_ONE,
                        "disallowedTools": ["Edit"]},
                       {"id": "coding-inherits", "name": "inherits",
                        "repositoryPath": REPO_TWO}]}
            _put(cfg_path, json.dumps(doc, indent=2) + "\n")
            _put(env_path, "DISALLOWED_TOOLS=Bash(rm:*),WebFetch\n")
            rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()), _FakeSudo(),
                               True)
            repos = json.loads(_read(cfg_path))["repositories"]
            expect("merge-env-override-composes-nothing-for-an-inheriting-entry",
                   "disallowedTools" not in repos[1]
                   and repos[0].get("disallowedTools") == ["Edit"] + list(SLACK_FENCE_RULES)
                   and "CANNOT" in out and "DISALLOWED_TOOLS" in out and rc == EX_BLOCKED
                   and not re.search(r"coding-fence\s+ALREADY-DONE", out),
                   (rc, repos, out[-600:]))
            probe_cfg = {"defaultDisallowedTools": ["Bash(rm -rf *)"], "entries": [
                {"index": 0, "id": "coding-inherits", "name": "inherits",
                 "disallowedTools": None, "labelPrompts": {}, "instructionHead": ""}]}
            row = check_fence(probe_cfg, {"names": ["DISALLOWED_TOOLS"]})
            expect("verify-fence-keeps-the-env-caveat-beside-a-problem",
                   row["outcome"] == BLOCKED
                   and "its live list is not in any file this reads" in _row_text(row)
                   and "paste into coding-inherits" not in _row_text(row), row)

            # the headline names "could not" apart from "nothing to do" (review 33)
            done = _fenced_fixture()
            done["slackAllowedTools"] = owner_grant([REPO_ONE, REPO_TWO])
            full = json.dumps({"permissions": {"deny": user_deny_patterns(mconf)}}) + "\n"
            _put(spath, full)
            _put(env_path, "CLAUDE_CONFIG_DIR=/srv/elsewhere\n")
            _put(cfg_path, json.dumps(done, indent=2) + "\n")
            rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()), _FakeSudo(),
                               False)
            expect("merge-not-merged-is-not-already-in-place", rc == EX_UNKNOWN
                   and "NOT MERGED" in out and "already in place" not in out, (rc, out[-500:]))
            _put(env_path, "A=1\n")
            broken = json.loads(json.dumps(done))
            broken["repositories"][0]["labelPrompts"]["debugger"]["disallowedTools"] = "Write"
            _put(cfg_path, json.dumps(broken, indent=2) + "\n")
            rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()), _FakeSudo(),
                               False)
            expect("merge-cannot-is-not-already-in-place", rc == EX_BLOCKED
                   and "CANNOT" in out and "already in place" not in out, (rc, out[-500:]))
            _put(cfg_path, json.dumps(done, indent=2) + "\n")
            rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()), _FakeSudo(),
                               False)
            expect("merge-in-place-says-so", rc == EX_OK and "already in place" in out,
                   (rc, out[-300:]))

            # a settings file of the wrong shape: the plan and the writer agree. The dry run
            # plans no DENY, verify's row says FAILED, and --apply still writes the config
            # and exits 1 — never 3, which is "refused for safety" (review 36)
            for label, text in (("permissions-is-a-list", '{"permissions": []}'),
                                ("deny-is-a-string", '{"permissions": {"deny": "Read(x)"}}'),
                                ("deny-is-null", '{"permissions": {"deny": null}}'),
                                ("top-level-is-a-list", "[]")):
                _put(cfg_path, json.dumps(_merge_fixture(), indent=2) + "\n")
                _put(spath, text)
                rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()),
                                   _FakeSudo(), False)
                dry_ok = ("CANNOT" in out and not re.search(r"^\s*DENY\b", out, re.M)
                          and rc == EX_BLOCKED)
                facts, _w = probe_facts(_RoleMachine(home, _pf_answers()), mconf)
                us_row = check_user_settings(mconf, facts["userSettings"], facts["env"])
                rc, out = _capture(cmd_merge, mconf, _RoleMachine(home, _pf_answers()),
                                   _FakeSudo(), True)
                repos = json.loads(_read(cfg_path))["repositories"]
                expect("merge-settings-shape-plan-and-writer-agree:" + label, dry_ok
                       and us_row["outcome"] == FAILED and rc == EX_FAILED
                       and repos[0]["disallowedTools"][-2:] == list(SLACK_FENCE_RULES)
                       and _read(spath) == text, (label, dry_ok, us_row, rc, out[-400:]))

            # the settings file moved between the plan and the write: cmd_merge's own patch
            # carries its checksum, so nothing is written, not even the config (review 49)
            _put(cfg_path, json.dumps(_merge_fixture(), indent=2) + "\n")
            _put(spath, json.dumps({"permissions": {"deny": []}}) + "\n")
            cfg_before = _read(cfg_path)
            moved = json.dumps({"permissions": {"deny": []}, "model": "someone-else"}) + "\n"

            def edit_settings():
                _put(spath, moved)
            rc, out = _capture(cmd_merge, mconf,
                               _RoleMachine(home, _pf_answers(), before_write=edit_settings),
                               _FakeSudo(), True)
            expect("merge-refuses-moved-settings-through-cmd-merge", rc == EX_REFUSED
                   and "changed" in out and _read(cfg_path) == cfg_before
                   and _read(spath) == moved, (rc, out[-400:]))
    group("merge-edges", merge_edges)

    # -- a lane that was on before this change: the documented upgrade path holds (review
    # 40, 47). The old conf has none of the new keys; the settings carry the old fourteen
    # rules plus the backups folder written by hand, in the absolute form. -------------
    def upgrade():
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "role-home")
            os.makedirs(home)
            cfg_path = os.path.join(tmp, "dispatcher", "config.json")
            env_path = os.path.join(tmp, "dispatcher", ".env")
            spath = os.path.join(home, ".claude", "settings.json")
            old = dict(minimal, DISPATCHER_CONFIG=cfg_path, DISPATCHER_ENV_FILE=env_path)
            done = _fenced_fixture()
            done["slackAllowedTools"] = owner_grant([REPO_ONE, REPO_TWO])
            _put(cfg_path, json.dumps(done, indent=2) + "\n")
            _put(env_path, "A=1\n")
            hand = "Read(/%s/.stage-e/backups/**)" % home
            old_rules = [r for r in user_deny_patterns(old)
                         if r not in ("Read(~/.stage-e/env)", BACKUPS_DENY_RULE)] + [hand]
            _put(spath, json.dumps({"permissions": {"deny": old_rules}}, indent=2) + "\n")
            facts, _w = probe_facts(_RoleMachine(home, _pf_answers()), old)
            rows = {r["check"]: r for r in evaluate(facts, old, pf_probe(
                _RoleMachine(home, _pf_answers())))}
            us, fd = rows["user-settings"], rows["front-door"]
            expect("upgrade-front-door-not-measured-until-the-keys-are-set",
                   fd["outcome"] == UNKNOWN and "NOT MEASURED" in fd["detail"]
                   and "FRONT_DOOR_CONFIG" in fd["detail"], fd)
            expect("upgrade-user-settings-names-merge-as-the-remedy",
                   us["outcome"] == BLOCKED and "Read(~/.stage-e/env)" in _row_text(us)
                   and BACKUPS_DENY_RULE in _row_text(us) and "merge --apply" in _row_text(us),
                   us)
            rc, out = _capture(cmd_merge, old, _RoleMachine(home, _pf_answers()), _FakeSudo(),
                               True)
            deny = json.loads(_read(spath))["permissions"]["deny"]
            expect("upgrade-merge-adds-only-the-two-rules", rc == EX_OK
                   and deny == old_rules + ["Read(~/.stage-e/env)", BACKUPS_DENY_RULE]
                   and json.loads(_read(cfg_path)) == done, (rc, deny, out[-400:]))
    group("upgrade", upgrade)

    # -- env-names: the REAL role-account shell ---------------------------------------
    def env_names():
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "role-home")
            os.makedirs(home)
            envdir = os.path.join(tmp, "dispatcher")
            env_path = os.path.join(envdir, ".env")
            cfg_path = os.path.join(envdir, "config.json")
            _put(cfg_path, json.dumps(_merge_fixture(), indent=2) + "\n")
            econf = dict(conf, DISPATCHER_CONFIG=cfg_path, DISPATCHER_ENV_FILE=env_path,
                         FRONT_DOOR_CONFIG=os.path.join(tmp, "front", "Caddyfile"))
            original = ("# dispatcher env\n"
                        "LINEAR_CLIENT_ID=%s\n"
                        "export SLACK_BOT_TOKEN=%s\n"
                        "CYRUS_HOST_EXTERNAL=false\n"
                        "OTHER_SETTING=1\n"
                        "  export WEBHOOK_IP_VALIDATION = true\n"
                        "LAST_LINE=kept" % (SENTINELS[3], SENTINELS[0]))
            _put(env_path, original)
            os.chmod(env_path, 0o640)
            role_env = os.path.join(home, ".stage-e", "env")
            _put(role_env, "NOTIFIER_SLACK_BOT_TOKEN=%s\nSTAGE_E_LINEAR_API_KEY=%s\n"
                 % (FAKE_NOTIFIER_TOKEN, SENTINELS[4]))
            asked = []

            def reader_of(*values):
                queue = list(values)

                def read(prompt):
                    asked.append(prompt)
                    return queue.pop(0)
                return read
            bdir = os.path.join(home, ".stage-e", "backups")

            def backups():
                return sorted(b for b in (os.listdir(bdir) if os.path.isdir(bdir) else [])
                              if b.startswith("dispatcher-env.pre-chat-lane."))

            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, False,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-needs-a-terminal", rc == EX_BLOCKED and not asked
                   and not mach.argvs and "terminal" in out, (rc, out[-300:]))
            # THE ORDER's first rule, measured before anything is asked: the fence before
            # the token. The fixture's coding entries lack the Slack rules (review 45).
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-refuses-without-the-fence", rc == EX_BLOCKED and not asked
                   and mach.writes == [] and _read(env_path) == original
                   and "fence" in out and "merge --apply" in out, (rc, out[-400:]))
            _put(cfg_path, json.dumps(_fenced_fixture(), indent=2) + "\n")
            mach = _RoleMachine(home, _pf_answers(rules=(0, "", "")))
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-refuses-without-the-port-block", rc == EX_BLOCKED and not asked
                   and mach.writes == [] and _read(env_path) == original
                   and "port block" in out, (rc, out[-400:]))
            # the block must guard the port the dispatcher LISTENS on, as verify's row
            # measures it: CYRUS_SERVER_PORT compared with DISPATCHER_PORT (review 32, 44)
            _put(env_path, original + "\nCYRUS_SERVER_PORT=4000\n")
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-refuses-a-port-mismatch", rc == EX_BLOCKED and not asked
                   and mach.writes == [] and "CYRUS_SERVER_PORT" in out, (rc, out[-400:]))
            _put(env_path, original)
            os.chmod(env_path, 0o640)
            # a port block that could not be measured is exit 4, and its remedy is not
            # "load piece 4" (review 35)
            mach = _RoleMachine(home, _pf_answers(rules=(1, "", "sudo: a password is required")))
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-unmeasured-port-block-exits-4", rc == EX_UNKNOWN and not asked
                   and mach.writes == [] and "piece 4" not in out, (rc, out[-400:]))
            # no env file at all: refused before either secret is asked for
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_env_names, dict(econf, DISPATCHER_ENV_FILE=env_path + "-gone"),
                               mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-refuses-a-missing-env-file-before-asking", rc == EX_BLOCKED
                   and not asked and mach.writes == [], (rc, out[-300:]))
            for label, tok, sec in (
                    ("not-xoxb", "xoxp-" + "FAKE" * 12, FAKE_SIGNING_SECRET),
                    ("short", "xoxb-FAKE", FAKE_SIGNING_SECRET),
                    ("bad-char", "xoxb-" + "FAKE" * 10 + " '", FAKE_SIGNING_SECRET),
                    ("secret-length", FAKE_BOT_TOKEN, "deadbeef" * 3),
                    ("secret-upper-case", FAKE_BOT_TOKEN, "DEADBEEF" * 4)):
                del asked[:]
                mach = _RoleMachine(home, _pf_answers())
                rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                                   reader_of(tok, sec))
                expect("env-names-shape:" + label, rc == EX_USAGE and len(asked) == 2
                       and mach.writes == [] and _read(env_path) == original
                       and tok not in out and sec not in out, (rc, out[-300:]))

            _put(os.path.join(envdir, ".env.swp"), "swap")
            del asked[:]
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            want = ["# dispatcher env", "LINEAR_CLIENT_ID=%s" % SENTINELS[3], "OTHER_SETTING=1",
                    "LAST_LINE=kept", "SLACK_BOT_TOKEN=" + FAKE_BOT_TOKEN,
                    "SLACK_SIGNING_SECRET=" + FAKE_SIGNING_SECRET, "CYRUS_HOST_EXTERNAL=true",
                    "WEBHOOK_IP_VALIDATION=false"]
            expect("env-names-writes-the-four-keeps-the-rest-in-order",
                   rc == EX_OK and _read(env_path).splitlines() == want, (rc, out[-600:]))
            expect("env-names-keeps-the-mode", _mode(env_path) == 0o640, oct(_mode(env_path)))
            expect("env-names-prints-lengths", all(
                ("%s %d" % (n, size)) in out for n, size in (
                    ("SLACK_BOT_TOKEN", len(FAKE_BOT_TOKEN)), ("SLACK_SIGNING_SECRET", 32),
                    ("CYRUS_HOST_EXTERNAL", 4), ("WEBHOOK_IP_VALIDATION", 5))), out[-600:])
            expect("env-names-swap-file-warning", "swap file" in out and ".env.swp" in out,
                   out[-400:])
            got = backups()
            expect("env-names-backed-up-first", len(got) == 1
                   and _read(os.path.join(bdir, got[0])) == original
                   and _mode(os.path.join(bdir, got[0])) == 0o600, got)
            flat = " ".join(out.split())
            expect("env-names-says-the-backup-holds-secrets", "holds the whole env file" in flat
                   and "once verify is clean" in flat, flat[-500:])
            expect("env-names-says-it-checked-the-notifier-token",
                   "CHECKED" in out and "NOTIFIER_SLACK_BOT_TOKEN" in out, out[-600:])
            expect("env-names-prints-the-restart-card", "CK-C5" in out
                   and "launchctl bootout system/com.example.dispatcher" in out)
            expect("env-names-never-restarts",
                   not any("launchctl" in " ".join(a) for a in mach.argvs))
            argv_text = "\n".join(" ".join(a) for a in mach.argvs)
            expect("env-names-no-value-in-argv", FAKE_BOT_TOKEN not in argv_text
                   and FAKE_SIGNING_SECRET not in argv_text)
            expect("env-names-no-value-in-output", all(v not in out for v in (
                FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET, SENTINELS[0], SENTINELS[3],
                SENTINELS[4], FAKE_NOTIFIER_TOKEN)))
            expect("env-names-stdin-hidden", len(mach.writes) == 1
                   and mach.writes[0]["stdin"] == "<hidden>"
                   and mach.writes[0]["argv"][6] == "cd / && " + env_writer_command(econf,
                                                                                     "set"),
                   [w["stdin"] for w in mach.writes])

            # the same values again: nothing to change, so no backup, no rename, no restart
            # card — "nothing to do" is told apart from "did it" (review 34)
            snap, count, inode = _read(env_path), len(backups()), os.stat(env_path).st_ino
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-same-values-change-nothing", rc == EX_OK
                   and _read(env_path) == snap and len(backups()) == count
                   and os.stat(env_path).st_ino == inode and "UNCHANGED" in out
                   and "launchctl bootout" not in out, (rc, out[-400:]))

            # the notifier's own token is refused, and nothing is touched — under its own
            # name, under another name, and as the LAST of two definitions, which is the
            # one a shell that sources the file keeps (review 30, 43, 48)
            for label, role_text in (
                    ("its-name", "export NOTIFIER_SLACK_BOT_TOKEN=\"%s\"\n" % FAKE_BOT_TOKEN),
                    ("another-name", "CHAT_BOT_TOKEN=%s\n" % FAKE_BOT_TOKEN),
                    ("stale-line-first", "NOTIFIER_SLACK_BOT_TOKEN=%s\n"
                                         "NOTIFIER_SLACK_BOT_TOKEN='%s'\n"
                                         % (FAKE_NOTIFIER_TOKEN, FAKE_BOT_TOKEN))):
                _put(role_env, role_text)
                mach = _RoleMachine(home, _pf_answers())
                rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                                   reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
                expect("env-names-refuses-the-notifier-token:" + label, rc == EX_REFUSED
                       and _read(env_path) == snap and len(backups()) == count
                       and "notifier" in out.lower() and FAKE_BOT_TOKEN not in out,
                       (rc, out[-400:]))
            # the file it compares with cannot be read: NOT CHECKED, exit 4, nothing
            # written and nothing backed up — never a silent pass
            os.unlink(role_env)
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-no-role-env-file-is-not-a-pass", rc == EX_UNKNOWN
                   and "NOT CHECKED" in out and "does not exist" in out
                   and _read(env_path) == snap and len(backups()) == count, (rc, out[-400:]))
            if os.geteuid() != 0:
                _put(role_env, "NOTIFIER_SLACK_BOT_TOKEN=%s\n" % FAKE_NOTIFIER_TOKEN)
                os.chmod(role_env, 0)
                mach = _RoleMachine(home, _pf_answers())
                rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                                   reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
                os.chmod(role_env, 0o600)
                expect("env-names-unreadable-role-env-file-is-not-a-pass", rc == EX_UNKNOWN
                       and "NOT CHECKED" in out and "cannot be read" in out
                       and _read(env_path) == snap and len(backups()) == count,
                       (rc, out[-400:]))
            # a file with no line for the name: every value is still compared, and the
            # output says the name was not there
            _put(role_env, "STAGE_E_LINEAR_API_KEY=%s\n" % SENTINELS[4])
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), False, True,
                               reader_of(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET))
            expect("env-names-says-the-notifier-name-is-absent", rc == EX_OK
                   and "NOTE" in out and "no NOTIFIER_SLACK_BOT_TOKEN line" in out
                   and SENTINELS[4] not in out, (rc, out[-400:]))
            _put(role_env, "NOTIFIER_SLACK_BOT_TOKEN=%s\n" % FAKE_NOTIFIER_TOKEN)

            # --remove: no terminal, no port block needed; the four go, the rest stay
            del asked[:]
            count = len(backups())
            mach = _RoleMachine(home, _pf_answers(rules=(0, "", "")))
            rc, out = _capture(cmd_env_names, econf, mach, _FakeSudo(), True, False,
                               reader_of())
            expect("env-names-remove", rc == EX_OK and not asked
                   and _read(env_path).splitlines() == want[:4], (rc, out[-400:]))
            expect("env-names-remove-backs-up-first", len(backups()) == count + 1)
            expect("env-names-remove-keeps-the-mode", _mode(env_path) == 0o640)
            # a second --remove has nothing to remove, and says so
            snap, count, inode = _read(env_path), len(backups()), os.stat(env_path).st_ino
            rc, out = _capture(cmd_env_names, econf, _RoleMachine(home, _pf_answers()),
                               _FakeSudo(), True, False, reader_of())
            expect("env-names-remove-twice-changes-nothing", rc == EX_OK
                   and _read(env_path) == snap and len(backups()) == count
                   and os.stat(env_path).st_ino == inode and "UNCHANGED" in out
                   and "launchctl bootout" not in out, (rc, out[-400:]))
            # a symbolic link is refused, and the refusal is not reported as a failure
            real = os.path.join(envdir, "real-env")
            _put(real, "KEEP=1\n")
            os.chmod(real, 0o600)
            link = os.path.join(envdir, "env-link")
            os.symlink(real, link)
            rc, out = _capture(cmd_env_names, dict(econf, DISPATCHER_ENV_FILE=link),
                               _RoleMachine(home, _pf_answers()), _FakeSudo(), True, False,
                               reader_of())
            expect("env-names-refuses-a-symlink-as-blocked", rc == EX_BLOCKED
                   and os.path.islink(link) and "symbolic link" in out and "FAILED" not in out,
                   (rc, out[-300:]))
    group("env-names", env_names)

    # -- front-door: the REAL writer, a stand-in proxy binary -------------------------
    def front_door():
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "role-home")
            os.makedirs(home)
            fd_path = os.path.join(tmp, "front", "Caddyfile")
            fbin = os.path.join(tmp, "bin", "fake-proxy")
            vlog = os.path.join(tmp, "validate.log")
            flag = os.path.join(tmp, "validate-fails")
            _put(fbin, FAKE_PROXY_BIN % {"log": vlog, "flag": flag})
            os.chmod(fbin, 0o755)
            _put(vlog, "")
            fconf = dict(conf, DISPATCHER_CONFIG=os.path.join(tmp, "d", "config.json"),
                         DISPATCHER_ENV_FILE=os.path.join(tmp, "d", ".env"),
                         FRONT_DOOR_CONFIG=fd_path, FRONT_DOOR_BIN=fbin)
            _put(fd_path, FRONT_DOOR_FIXTURE)
            original = _read_bytes(fd_path)
            inode = os.stat(fd_path).st_ino
            bdir = os.path.join(home, ".stage-e", "backups")

            for key in ("FRONT_DOOR_CONFIG", "FRONT_DOOR_BIN", "FRONT_DOOR_MATCHER"):
                mach = _RoleMachine(home, _pf_answers())
                rc, out = _capture(cmd_front_door, dict(fconf, **{key: ""}), mach, _FakeSudo(),
                                   True, False)
                expect("front-door-needs:" + key, rc == EX_USAGE and key in out
                       and not mach.argvs, (rc, out[-200:]))

            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_front_door, fconf, mach, _FakeSudo(), False, False)
            expect("front-door-dry-run", rc == EX_BLOCKED and mach.writes == []
                   and _read_bytes(fd_path) == original
                   and "@dispatcher path /linear-webhook /callback /status /extra-path" in out
                   and "/extra-path /slack-webhook" in out, (rc, out[-500:]))
            warned = [l for l in out.splitlines() if "WARNING" in l]
            expect("front-door-names-the-unknown-path", any("/extra-path" in l for l in warned)
                   and not any("/callback" in l for l in warned), warned)
            expect("front-door-cites-each-route", all(c in " ".join(out.split()) for c in (
                "LinearEventTransport.js:68", "SlackEventTransport.js:85")), out[-600:])

            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_front_door, fconf, mach, _FakeSudo(), True, False)
            after = _read_bytes(fd_path).splitlines(True)
            before_lines = original.splitlines(True)
            diff = [i for i, (a, b) in enumerate(zip(before_lines, after)) if a != b]
            expect("front-door-apply-changes-one-line", rc == EX_OK
                   and len(after) == len(before_lines) and diff == [1]
                   and after[1] == b"\t@dispatcher path /linear-webhook /callback /status "
                                   b"/extra-path /slack-webhook\n", (rc, diff, out[-500:]))
            expect("front-door-apply-in-place", os.stat(fd_path).st_ino == inode)
            expect("front-door-validated", ("validate --config " + fd_path) in _read(vlog),
                   _read(vlog))
            bks = sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []
            expect("front-door-backed-up", len(bks) == 1
                   and bks[0].startswith("Caddyfile.pre-chat-lane.")
                   and _read_bytes(os.path.join(bdir, bks[0])) == original
                   and _mode(os.path.join(bdir, bks[0])) == 0o600, bks)
            expect("front-door-prints-restart-and-probes",
                   "launchctl bootout system/com.example.front-door" in out
                   and "/api/update/cyrus-config" in out, out[-600:])
            expect("front-door-never-restarts",
                   not any("launchctl" in " ".join(a) for a in mach.argvs))
            expect("front-door-apply-the-writer", len(mach.writes) == 1
                   and mach.writes[0]["argv"][6] == "cd / && " + front_door_writer_command(fconf))

            applied = _read_bytes(fd_path)
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_front_door, fconf, mach, _FakeSudo(), True, False)
            expect("front-door-idempotent", rc == EX_OK and mach.writes == []
                   and _read_bytes(fd_path) == applied and "already" in out, (rc, out[-300:]))

            _put(fd_path, FRONT_DOOR_FIXTURE)
            _put(flag, "")
            rc, out = _capture(cmd_front_door, fconf, _RoleMachine(home, _pf_answers()),
                               _FakeSudo(), True, False)
            expect("front-door-validate-failure-restores-byte-for-byte", rc == EX_FAILED
                   and _read_bytes(fd_path) == original and "VALIDATE" in out.upper(),
                   (rc, out[-400:]))
            os.unlink(flag)

            two = FRONT_DOOR_FIXTURE.replace("\trespond 404\n",
                                             "\t@dispatcher path /other\n\trespond 404\n")
            _put(fd_path, two)
            for apply_it in (False, True):
                mach = _RoleMachine(home, _pf_answers())
                rc, out = _capture(cmd_front_door, fconf, mach, _FakeSudo(), apply_it, False)
                expect("front-door-two-lines-refused:%s" % apply_it, rc == EX_BLOCKED
                       and mach.writes == [] and _read(fd_path) == two and "found 2" in out,
                       (rc, out[-300:]))
            # no line at all — the matcher line removed, or a matcher the conf misspells —
            # is a clean refusal, never a traceback (review 51)
            zero = FRONT_DOOR_FIXTURE.replace(
                "\t@dispatcher path /linear-webhook /callback /status /extra-path\n", "")
            for label, text, matcher in (("no-line", zero, "@dispatcher"),
                                         ("other-matcher", FRONT_DOOR_FIXTURE, "@other")):
                _put(fd_path, text)
                for apply_it in (False, True):
                    mach = _RoleMachine(home, _pf_answers())
                    rc, out = _capture(cmd_front_door, dict(fconf, FRONT_DOOR_MATCHER=matcher),
                                       mach, _FakeSudo(), apply_it, False)
                    expect("front-door-zero-lines-refused:%s:%s" % (label, apply_it),
                           rc == EX_BLOCKED and mach.writes == [] and _read(fd_path) == text
                           and "found 0" in out, (rc, out[-300:]))
            # a line that forwards the config-update route or the tool server: the chat
            # path is not added to it; taking the chat path off still works (review 46)
            wide = FRONT_DOOR_FIXTURE.replace("/extra-path", "/extra-path /api/*")
            _put(fd_path, wide)
            mach = _RoleMachine(home, _pf_answers())
            rc, out = _capture(cmd_front_door, fconf, mach, _FakeSudo(), True, False)
            expect("front-door-refuses-to-add-beside-a-widening-path", rc == EX_BLOCKED
                   and mach.writes == [] and _read(fd_path) == wide and "/api/*" in out,
                   (rc, out[-300:]))
            wide_on = wide.replace("/api/*", "/api/* /slack-webhook")
            _put(fd_path, wide_on)
            rc, out = _capture(cmd_front_door, fconf, _RoleMachine(home, _pf_answers()),
                               _FakeSudo(), True, True)
            expect("front-door-remove-beside-a-widening-path", rc == EX_OK
                   and _read(fd_path) == wide and "/api/*" in out, (rc, out[-300:]))

            def moved():
                _put(fd_path, FRONT_DOOR_FIXTURE + "# edited by hand\n")
            _put(fd_path, FRONT_DOOR_FIXTURE)
            rc, out = _capture(cmd_front_door, fconf,
                               _RoleMachine(home, _pf_answers(), before_write=moved),
                               _FakeSudo(), True, False)
            expect("front-door-sha-mismatch-refused", rc == EX_REFUSED
                   and _read(fd_path) == FRONT_DOOR_FIXTURE + "# edited by hand\n",
                   (rc, out[-300:]))

            _put(fd_path, FRONT_DOOR_FIXTURE.replace("/extra-path", "/extra-path /slack-webhook"))
            rc, out = _capture(cmd_front_door, fconf, _RoleMachine(home, _pf_answers()),
                               _FakeSudo(), True, True)
            expect("front-door-remove", rc == EX_OK and _read(fd_path) == FRONT_DOOR_FIXTURE,
                   (rc, out[-400:]))
            _put(fd_path, "@dispatcher path /slack-webhook\n")
            rc, out = _capture(cmd_front_door, fconf, _RoleMachine(home, _pf_answers()),
                               _FakeSudo(), True, True)
            expect("front-door-remove-last-path-refused", rc == EX_BLOCKED
                   and _read(fd_path) == "@dispatcher path /slack-webhook\n", (rc, out[-300:]))
    group("front-door", front_door)

    # -- each writer's own guards, run directly: the checks the subcommands never reach
    # because their plan is right (a wrong name, a wrong `from`, a moved settings file, a
    # file owned by someone else, a line that is not the one read) -------------------
    def writer_guards():
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "role-home")
            os.makedirs(home)
            cfg_path = os.path.join(tmp, "dispatcher", "config.json")
            wconf = dict(conf, DISPATCHER_CONFIG=cfg_path,
                         DISPATCHER_ENV_FILE=os.path.join(tmp, "dispatcher", ".env"))
            _put(cfg_path, json.dumps(_merge_fixture(), indent=2) + "\n")
            raw = _read_bytes(cfg_path)
            sha = hashlib.sha256(raw).hexdigest()
            py = shlex.quote(sys.executable)

            def run(script, stdin, path_first=None):
                path = "/usr/bin:/bin" if path_first is None else path_first + ":/usr/bin:/bin"
                return subprocess.run(["/bin/sh", "-c", "cd / && " + script.replace(
                    "/usr/bin/python3", py)], input=stdin, capture_output=True, text=True,
                    timeout=60, env={"HOME": home, "PATH": path})

            right = {"op": "fence-entry", "index": 0, "id": "coding-a", "name": "a",
                     "from": "own", "add": list(SLACK_FENCE_RULES)}
            for label, op in (("wrong-name", dict(right, name="someone-else")),
                              ("wrong-from", dict(right, **{"from": "default"})),
                              ("prompt-type-gone", {"op": "fence-prompt", "scope": "labelPrompts",
                                                    "index": 0, "id": "coding-a", "name": "a",
                                                    "type": "no-such-type",
                                                    "add": list(SLACK_FENCE_RULES)})):
                ran = run(merge_writer_command(wconf),
                          json.dumps({"configSha256": sha, "config": [op], "deny": []}))
                expect("merge-writer-refuses:" + label, ran.returncode == 3
                       and _read_bytes(cfg_path) == raw, (ran.returncode, ran.stdout[-200:]))
            # the settings file moved between the read and the write: nothing is written,
            # not even the config
            spath = os.path.join(home, ".claude", "settings.json")
            _put(spath, json.dumps({"permissions": {"deny": []}}))
            ran = run(merge_writer_command(wconf), json.dumps({
                "configSha256": sha, "config": [right], "settingsSha256": "0" * 64,
                "deny": [BACKUPS_DENY_RULE]}))
            expect("merge-writer-refuses-moved-settings", ran.returncode == 3
                   and _read_bytes(cfg_path) == raw
                   and _read(spath) == json.dumps({"permissions": {"deny": []}}),
                   (ran.returncode, ran.stdout[-200:]))
            # a shape problem is FAILED (1), not REFUSED (3): nothing moved, it is broken
            _put(spath, json.dumps({"permissions": {"deny": "not a list"}}))
            ran = run(merge_writer_command(wconf), json.dumps({
                "configSha256": sha, "config": [], "deny": [BACKUPS_DENY_RULE],
                "settingsSha256": hashlib.sha256(_read_bytes(spath)).hexdigest()}))
            expect("merge-writer-fails-a-deny-that-is-not-a-list", ran.returncode == 1
                   and "by hand" in ran.stdout, (ran.returncode, ran.stdout[-200:]))
            # a patch with deny rules and no settings checksum is malformed: the check can
            # not be switched off by leaving the key out (review 49)
            _put(spath, json.dumps({"permissions": {"deny": []}}))
            ran = run(merge_writer_command(wconf), json.dumps({
                "configSha256": sha, "config": [right], "deny": [BACKUPS_DENY_RULE]}))
            expect("merge-writer-wants-the-settings-checksum", ran.returncode == 2
                   and _read_bytes(cfg_path) == raw
                   and _read(spath) == json.dumps({"permissions": {"deny": []}}),
                   (ran.returncode, ran.stdout[-200:]))
            # the config is copied, privately, before it is written
            os.unlink(spath)
            ran = run(merge_writer_command(wconf), json.dumps({
                "configSha256": sha, "config": [right], "deny": []}))
            bdir = os.path.join(home, ".stage-e", "backups")
            copies = [b for b in (os.listdir(bdir) if os.path.isdir(bdir) else [])
                      if b.startswith("dispatcher-config.%s." % BACKUP_TAG)]
            expect("merge-writer-backs-the-config-up-first", ran.returncode == 0
                   and len(copies) == 1 and _read_bytes(os.path.join(bdir, copies[0])) == raw
                   and _mode(os.path.join(bdir, copies[0])) == 0o600
                   and _mode(bdir) == 0o700, (ran.returncode, copies))

            # INSIDE the writer: the dispatcher rewrites its config while the backup is
            # being made. The writer checks again just before the write, and refuses; and a
            # file that no longer holds what it wrote is never overwritten with the old
            # bytes (review 37). The real program runs, with os.fsync wrapped.
            _put(cfg_path, json.dumps(_merge_fixture(), indent=2) + "\n")
            raw = _read_bytes(cfg_path)
            sha = hashlib.sha256(raw).hexdigest()
            refreshed = raw.decode("utf-8").replace(SENTINELS[3], SENTINELS[3] + "-refreshed")
            patch = json.dumps({"configSha256": sha, "config": [right], "deny": []})
            ran = _run_hooked(MERGE_WRITER_PY, [cfg_path], patch, home,
                              {1: (cfg_path, refreshed)})
            expect("merge-writer-rechecks-just-before-the-write", ran.returncode == 3
                   and "changed" in ran.stdout and _read(cfg_path) == refreshed,
                   (ran.returncode, ran.stdout[-300:]))
            _put(cfg_path, raw.decode("utf-8"))
            ran = _run_hooked(MERGE_WRITER_PY, [cfg_path], patch, home,
                              {2: (cfg_path, "{ not json")})
            expect("merge-writer-never-restores-over-another-write", ran.returncode == 1
                   and _read(cfg_path) == "{ not json" and "by hand" in ran.stdout,
                   (ran.returncode, ran.stdout[-300:]))
            fd_file = os.path.join(tmp, "front", "Caddyfile.edge")
            _put(fd_file, FRONT_DOOR_FIXTURE)
            fraw = _read_bytes(fd_file)
            edited = FRONT_DOOR_FIXTURE + "# edited by hand\n"
            ran = _run_hooked(FRONT_WRITER_PY, [fd_file, "/bin/true"], json.dumps({
                "sha256": hashlib.sha256(fraw).hexdigest(), "index": 1,
                "old": FRONT_DOOR_FIXTURE.splitlines()[1],
                "new": FRONT_DOOR_FIXTURE.splitlines()[1] + " /slack-webhook"}), home,
                {1: (fd_file, edited)})
            expect("front-writer-rechecks-just-before-the-write", ran.returncode == 3
                   and _read(fd_file) == edited, (ran.returncode, ran.stdout[-300:]))

            # the env writer: someone else's file, a missing file, nothing on stdin, a
            # mode it does not know
            env_path = os.path.join(tmp, "dispatcher", ".env")
            _put(env_path, "KEEP=1\n")
            fakebin = os.path.join(tmp, "fake-id-bin")
            _put(os.path.join(fakebin, "id"), "#!/bin/sh\necho someone-else\n")
            os.chmod(os.path.join(fakebin, "id"), 0o755)
            ran = run(env_writer_command(wconf, "set"), "%s\n%s\n" % (
                FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET), path_first=fakebin)
            expect("env-writer-refuses-a-file-it-does-not-own", ran.returncode == 4
                   and "owned by" in ran.stdout and _read(env_path) == "KEEP=1\n"
                   and FAKE_BOT_TOKEN not in ran.stdout + ran.stderr, ran.stdout[-200:])
            ran = run(env_writer_command(dict(wconf, DISPATCHER_ENV_FILE=env_path + "-gone"),
                                         "set"), "%s\n%s\n" % (FAKE_BOT_TOKEN,
                                                               FAKE_SIGNING_SECRET))
            expect("env-writer-refuses-a-missing-file", ran.returncode == 4
                   and "does not exist" in ran.stdout
                   and not os.path.exists(env_path + "-gone"), ran.stdout[-200:])
            # a symbolic link: `mv` would replace the LINK with a plain file at the link's
            # own mode (755 on macOS, 777 under GNU stat), holding every secret (review 42)
            real = os.path.join(tmp, "dispatcher", "real-env")
            _put(real, "KEEP=1\n")
            os.chmod(real, 0o600)
            link = os.path.join(tmp, "dispatcher", "env-link")
            os.symlink(real, link)
            for mode in ("set", "remove"):
                ran = run(env_writer_command(dict(wconf, DISPATCHER_ENV_FILE=link), mode),
                          "%s\n%s\n" % (FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET)
                          if mode == "set" else "")
                expect("env-writer-refuses-a-symlink:" + mode, ran.returncode == 4
                       and os.path.islink(link) and _read(real) == "KEEP=1\n"
                       and _mode(real) == 0o600 and "symbolic link" in ran.stdout
                       and FAKE_BOT_TOKEN not in ran.stdout + ran.stderr,
                       (ran.returncode, ran.stdout[-200:]))
            ran = run(env_writer_command(wconf, "set"), "")
            expect("env-writer-wants-both-values", ran.returncode == 7
                   and _read(env_path) == "KEEP=1\n", ran.stdout[-200:])
            ran = run(env_writer_command(wconf, "bogus"), "")
            expect("env-writer-refuses-an-unknown-mode", ran.returncode == 2
                   and _read(env_path) == "KEEP=1\n", ran.stdout[-200:])

            # the front-door writer: the right checksum, but not the line the plan read
            fd_path = os.path.join(tmp, "front", "Caddyfile")
            _put(fd_path, FRONT_DOOR_FIXTURE)
            fraw = _read_bytes(fd_path)
            fconf = dict(wconf, FRONT_DOOR_CONFIG=fd_path, FRONT_DOOR_BIN="/bin/true")
            ran = run(front_door_writer_command(fconf), json.dumps({
                "sha256": hashlib.sha256(fraw).hexdigest(), "index": 1,
                "old": "\t@dispatcher path /somewhere-else", "new": "\t@dispatcher path /x"}))
            expect("front-writer-refuses-a-different-line", ran.returncode == 3
                   and _read_bytes(fd_path) == fraw, (ran.returncode, ran.stdout[-200:]))
    group("writer-guards", writer_guards)

    # -- the env writer's secrets meet only shell builtins, never an external command,
    # whose argv `ps` would show: the chat token and signing secret ($T, $S), and every
    # line and value it reads from the role account's env file ($l, $v) (review 50) ----
    def env_builtins():
        used = _value_commands(ENV_WRITER_SH)
        expect("env-writer-values-meet-only-builtins",
               used and all(w in _BUILTIN_WORDS for w in used), used)
        for mutant in ("/usr/bin/printf 'x%s' \"$T\"", "/bin/test \"$v\" = \"$T\"",
                       "echo \"${S}\" | /usr/bin/wc -c"):
            got = _value_commands(ENV_WRITER_SH + "\n" + mutant + "\n")
            expect("env-writer-builtins-scan-flags:" + mutant.split()[0],
                   any(w not in _BUILTIN_WORDS for w in got), got)
    group("env-builtins", env_builtins)

    # -- each shape refusal says which shape, by length or kind, and never the value -----
    def shapes():
        for tok, sec, said in (
                ("xoxp-" + "FAKE" * 12, FAKE_SIGNING_SECRET, "does not start xoxb-"),
                ("xoxb-FAKE", FAKE_SIGNING_SECRET, "only 9 characters"),
                ("xoxb-" + "FAKE" * 10 + " '", FAKE_SIGNING_SECRET, "never does"),
                (FAKE_BOT_TOKEN, "deadbeef" * 3, "is 24 characters"),
                (FAKE_BOT_TOKEN, "DEADBEEF" * 4, "not lower-case hex")):
            got = secret_shape_problem(tok, sec) or ""
            expect("shape-says:" + said, said in got and tok not in got and sec not in got,
                   got)
        expect("shape-accepts-the-real-shape",
               secret_shape_problem(FAKE_BOT_TOKEN, FAKE_SIGNING_SECRET) is None)
    group("shapes", shapes)

    # -- a flag a command does not take is a usage error, before anything is read -----
    def flags():
        for argv in (["merge", "--remove"], ["env-names", "--apply"], ["verify", "--apply"],
                     ["compose", "--remove"]):
            fake = _FakeRunner([("", 0, "{}", "")])
            rc, out = _capture(main, argv + ["--conf", "/nonexistent/chat-lane.conf"],
                               fake, _FakeSudo())
            expect("flag-refused:" + " ".join(argv), rc == EX_USAGE and "takes no" in out
                   and not fake.argvs, (rc, out[-200:]))
    group("flags", flags)

    # -- a declined sudo names THIS script, the subcommand and its flags, and the conf --
    def no_sudo_writers():
        class _Declining(object):
            def acquire(self, why, resume):
                raise NoPrivilege("NO ADMINISTRATOR ACCESS — nothing was attempted.\n"
                                  "  Fix that and run the same command again:\n"
                                  "      python3 %s %s" % (_stage_e_self_path(), resume))
        with tempfile.TemporaryDirectory() as tmp:
            cpath = os.path.join(tmp, "chat-lane.conf")
            _put(cpath, GOOD_CONF_TEXT)
            for argv in (["merge", "--apply"], ["front-door", "--remove", "--apply"],
                         ["env-names", "--remove"]):
                rc, out = _capture(main, argv + ["--conf", cpath],
                                   _FakeRunner([("", 0, "{}", "")]), _Declining())
                want = "pipeline_chat_lane_setup.py %s --conf %s" % (" ".join(argv), cpath)
                expect("no-sudo-names-the-writer:" + argv[0], rc == EX_NOPRIV and want in out
                       and "pipeline_stage_e_setup.py" not in out, out[-300:])
    group("no-sudo-writers", no_sudo_writers)

    # -- every writing subcommand refuses in an agent environment, before the conf ----
    def agent_refused():
        marker = AGENT_ENV_MARKERS[0]
        try:
            os.environ[marker] = ""
            for argv in (["merge", "--apply"], ["env-names"], ["env-names", "--remove"],
                         ["front-door", "--apply"]):
                fake = _FakeRunner([("", 0, "{}", "")])
                rc, out = _capture(main, argv + ["--conf", "/nonexistent/chat-lane.conf"],
                                   fake, _FakeSudo())
                expect("agent-refused:" + " ".join(argv), rc == EX_REFUSED
                       and "REFUSED" in out and not fake.argvs and "could not read" not in out,
                       (rc, out[-300:]))
        finally:
            os.environ.pop(marker, None)
    group("agent-refused", agent_refused)


if __name__ == "__main__":
    sys.exit(main())
