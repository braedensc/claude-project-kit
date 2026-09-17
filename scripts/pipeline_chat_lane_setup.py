#!/usr/bin/env python3
"""Chat-lane composer — the dispatcher's built-in Slack lane, composed for a person to apply.

    python3 scripts/pipeline_chat_lane_setup.py compose [--conf chat-lane.conf]
    python3 scripts/pipeline_chat_lane_setup.py verify  [--conf chat-lane.conf]
    python3 scripts/pipeline_chat_lane_setup.py card CK-C1
    python3 scripts/pipeline_chat_lane_setup.py --selftest

WHAT IT IS.  The dispatcher ships a Slack transport: mention its bot in a channel and it
starts a session for that thread. That session gets no sandbox. This file COMPOSES every
piece a deployment needs to turn that lane on under the owner's conditions (KIT-117,
decision and correction of 2026-09-17), and VERIFIES the live result. A person applies
each piece by hand.

    compose   prints every piece. Reads no live file, needs no role-account access, runs
              anywhere. Exit 0, or 2 on a conf error.
    verify    read-only live measurement: three files as the role account, and pf's
              loaded rules as root. One outcome per check.
    card      prints a checkpoint card: CK-C1 (create the chat app), CK-C2 (the front
              door), CK-C3 (the live check in the channel), CK-C4 (the dispatcher's port,
              from a second device).

WHAT IT NEVER DOES.  It never writes the dispatcher's config, its env file, or any
settings file. `compose` prints; `verify` reads. There is no merge, approve, label or
ticket-state path anywhere in this file, and `--selftest` scans its own source for one.

EXIT CODES — the Stage E installer's, contract §13:
    0   compose printed / verify measured every check as applied
    1   FAILED — a check found something broken (an unparseable config, say)
    2   USAGE or CONFIG — nothing was attempted
    3   REFUSED — `verify` in an agent environment
    4   UNKNOWN — a check could not measure itself. Not a pass.
    5   NO ADMINISTRATOR ACCESS — `sudo` absent or declined; nothing was read
    10  BLOCKED-ON-HUMAN — drift: a piece is not applied, or not as composed

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
      (SharedApplicationServer.js:37-40). The code that enforces those lists lives in
      packages not read here, so whether a front door's forwarded address passes is
      unmeasured.
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
from pipeline_stage_e_setup import (  # noqa: E402
    ALREADY_DONE, BLOCKED, FAILED, UNKNOWN,
    EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_NOPRIV, EX_BLOCKED,
    REVIEW_BRIEF_FINGERPRINT, TRACKER_FENCE_SERVERS,
    _ACCOUNT_RE, _BLOB_RE, _CRED_PREFIXES, _ENV_NAME_RE,
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
OWNER_GRANT = ["Read", "Bash(git -C * pull)", "WebFetch", "WebSearch", "SendMessage",
                  "ToolSearch", "mcp__slack", "mcp__linear"]

# Tools that must never appear in the grant. A mutant grant holding any is red.
NEVER_IN_GRANT = ("Monitor", "Task", "Agent", "ScheduleWakeup", "Skill", "Write", "Edit",
                  "NotebookEdit", "CronCreate", "RemoteTrigger", "Workflow")

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


def grant_problems(grant):
    """Every way a grant breaks the owner's decision. Empty means acceptable."""
    problems = []
    if not isinstance(grant, list) or not all(isinstance(t, str) for t in grant):
        return ["the grant is not a list of tool names"]
    for tool in grant:
        if tool in NEVER_IN_GRANT:
            problems.append("%s is in the grant" % tool)
        if tool == "Bash" or re.match(r"^Bash\(\s*\*?\s*\)$", tool):
            problems.append("an unscoped Bash (%s) is in the grant" % tool)
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


def pf_install_commands(port):
    """The exact commands a person runs, in order, to install, check, load and confirm."""
    rules, plist = '"%s"' % PF_RULES_PATH, PF_DAEMON_PLIST
    return (["sudo mkdir -p \"%s\"" % PF_RULES_DIR,
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
               "sudo launchctl bootstrap system %s" % plist,
               "sudo /sbin/pfctl -a %s -s rules" % PF_ANCHOR,
               "sudo /sbin/pfctl -s info | grep Status",
               "curl -s -m 5 http://127.0.0.1:%s/status" % port])


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


def user_deny_patterns(conf):
    """The composed deny list: the repository's patterns anchored, then this deployment's
    two credential files by absolute path."""
    out = [anchored(p) for p in REPO_DENY_PATTERNS]
    for key in ("DISPATCHER_CONFIG", "DISPATCHER_ENV_FILE"):
        path = (conf or {}).get(key) or ""
        if path.startswith("/"):
            rule = "Read(/%s)" % path
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
            "description": "The dispatcher's chat lane. One-member workspace only.",
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
                 "DISPATCHER_PORT": DEFAULT_DISPATCHER_PORT}
CONF_KEYS = set(CONF_REQUIRED) | set(CONF_DEFAULTS)
_HOST_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_EXTRA_CRED_PREFIXES = ("xoxp-", "xoxa-", "xoxe", "xapp-")


def parse_conf(text, source="chat-lane.conf"):
    """(values, errors). Every bad line, never only the first."""
    values, seen, errors = {}, {}, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append("%s:%d: not KEY=value: %r" % (source, n, raw[:60]))
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
def cmd_compose(conf, conf_path="chat-lane.conf"):
    problems = grant_problems(OWNER_GRANT)
    if problems:
        # The approved list is a constant; a broken one is a defect in this file.
        say("BUG: the composed grant breaks the owner's decision: " + "; ".join(problems))
        return EX_FAILED
    rule = "=" * 76
    say(rule)
    say(" Chat lane — every piece, composed for you to apply by hand")
    say(rule)
    say(" conf: %s    dispatcher source read: %s" % (conf_path, SOURCE_VERSION))
    para("This command read no live file and changed nothing. Apply each piece yourself, "
         "in THE ORDER at the end, then run:  python3 %s verify" % _self_path(), " ")
    say("")
    say("READ THIS FIRST — THE SLACK WORKSPACE MUST HAVE ONE MEMBER")
    para("The chat lane has no user list and no channel list. Any member of the Slack "
         "workspace who can mention the bot in a channel it is in starts a session, as "
         "the dispatcher's account, with no sandbox. The access check runs only on tracker "
         "webhooks (EdgeWorker.js:3083-3087, 3620-3624). So the gate is workspace "
         "membership. Keep the workspace to one member: you. Turn this lane off before "
         "anyone else joins. The private channel keeps the notifier's pings confidential; "
         "it does not gate this lane.")
    say("")

    # -- Piece 1 -------------------------------------------------------------
    say("PIECE 1 — THE TRIMMED CHAT GRANT")
    para("Where: the top level of %s. MERGE this one key; never replace the file, because "
         "the dispatcher rewrites it itself to store refreshed tracker tokens "
         "(EdgeWorker.js:5154-5211)." % conf["DISPATCHER_CONFIG"])
    say("")
    say('    "slackAllowedTools": ' + json.dumps(OWNER_GRANT))
    say("")
    say("  THE DISPATCHER STILL ADDS mcp__cyrus-tools AND mcp__cyrus-docs, WHATEVER THIS")
    say("  LIST SAYS. It appends every tool server it built for the session after the list")
    say("  (ToolPermissionResolver.js:72-77), and the chat lane's disallowedTools is")
    say("  hard-coded empty (RunnerConfigBuilder.js:78). Nothing here can take them out.")
    for name, what in APPENDED_SERVERS:
        say("    %s" % name)
        para(what, "      ")
    para("One more path adds tools: any tool starting mcp__ in the FIRST repository entry's "
         "allowedTools joins this grant (RunnerConfigBuilder.js:63-66; "
         "ChatRepositoryProvider.js:18-20). Keep that entry's allowedTools free of mcp__ "
         "names; `verify` checks it.")
    say("")

    # -- Piece 2 -------------------------------------------------------------
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
         "list for each entry from the live file, ready to paste.")
    say("")

    # -- Piece 3 -------------------------------------------------------------
    say("PIECE 3 — THE DISPATCHER'S ENV FILE: THREE NAMES")
    para("Where: %s. The dispatcher loads it at start and re-applies it when it changes "
         "(Application.js:52-78). Names only: type the two secret values in yourself."
         % conf["DISPATCHER_ENV_FILE"])
    say("")
    say("    SLACK_BOT_TOKEN        the CHAT app's bot token (Slack: OAuth & Permissions)")
    say("    SLACK_SIGNING_SECRET   the CHAT app's signing secret (Slack: Basic Information)")
    say("    CYRUS_HOST_EXTERNAL=true")
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
        "webhook. Read at start only. CLOSED BY PIECE 4 once it is loaded and card CK-C4 "
        "passes; open until then.",
        "3. Webhook source-address checks turn on (EdgeWorker.js:241-252) unless "
        "WEBHOOK_IP_VALIDATION=false, and the dispatcher fetches GitHub's address list from "
        "api.github.com at start (EdgeWorker.js:411-416). The tracker webhook is checked "
        "only with LINEAR_DIRECT_WEBHOOKS=true (EdgeWorker.js:475-489); the Slack webhook gets no "
        "address list at all (EdgeWorker.js:743-750). Read at start only.",
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

    # -- Piece 4 -------------------------------------------------------------
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
         "line, and a plist may not start with a space.")
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
         "CYRUS_HOST_EXTERNAL in piece 3 until both are right.")
    para("After piece 3's restart, card CK-C4 proves the rule from a second device on the "
         "same network. A test from this machine to its own network address proves "
         "nothing.")
    say("")

    # -- Piece 5 -------------------------------------------------------------
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
    para("Why every rule starts with //**/: the first twelve are this repository's own "
         "denies, which are relative, and a relative rule matches under the session's "
         "current directory only. A chat session's is a fresh, empty folder. Anchored, each "
         "matches that file name anywhere. The last two are this deployment's dispatcher "
         "config and env file.")
    para("WHAT THESE DO NOT STOP. They block the Read tool. They do not stop the upload "
         "tool in mcp__cyrus-tools, which reads the file inside the dispatcher's own "
         "process, outside the session (cyrus-tools/index.js:107). And they apply to every "
         "session this account runs, coding and review too, because every Claude session "
         "loads user settings (ClaudeRunner.js:499).")
    say("")

    # -- Piece 6 -------------------------------------------------------------
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

    # -- Piece 7 -------------------------------------------------------------
    say("PIECE 7 — THE FRONT DOOR")
    para("The reverse proxy's path allowlist gains /slack-webhook, and nothing else. "
         "Slack's request URL is then https://%s/slack-webhook. Card CK-C2 walks it."
         % conf["FRONT_DOOR_HOST"])
    say("")

    # -- Order ---------------------------------------------------------------
    say("THE ORDER — the fence before the token, the port block before the listen")
    steps = (
        "1. Confirm the Slack workspace has one member.",
        "2. Piece 2, then piece 1, in the dispatcher config.",
        "3. Piece 5, in the role account's user settings.",
        "4. Piece 4: install and load the port block. Go on only when pfctl shows both "
        "rules and \"Status: Enabled\" — the next restart makes the dispatcher listen on "
        "every interface.",
        "5. Card CK-C1: create the chat app from piece 6 and install it.",
        "6. Piece 3, then restart the dispatcher when no session is in flight. "
        "slackAllowedTools reloads live (ConfigManager.js:51-62, 181), but the listening "
        "address and address checks are read at start only, and a removed env name stays "
        "set until a restart (Application.js:54). Restart on purpose, now, not at the next "
        "reboot.",
        "7. Card CK-C4, from a second device: the port refuses the network.",
        "8. Piece 7 and card CK-C2: the front door.",
        "9. In the Slack app, retry the request URL under Event Subscriptions. Make a "
        "private channel and invite the bot.",
        "10. python3 %s verify" % _self_path(),
        "11. Card CK-C3: the live check, in the channel.",
    )
    for step in steps:
        para(step, "  ")
    return EX_OK


# --------------------------------------------------------------------------- #
# Cards — the Stage E installer's card format.
# --------------------------------------------------------------------------- #
CARDS = {
    "CK-C1": {
        "title": "Create the chat app from the manifest",
        "why": ("A Slack app is created by a person signed in to the workspace, and its two "
                "secrets are shown only to that person. This command has no Slack access and "
                "wants none. It is a SECOND app: the notifier keeps its own app and token, so "
                "the token every dispatcher session can read is never the notifier's."),
        "do": ["Check the workspace has exactly one member: you.",
               "Print the manifest (piece 6):",
               "    python3 scripts/pipeline_chat_lane_setup.py compose",
               "In Slack's app settings: Create New App -> From a manifest -> choose the",
               "workspace -> paste the JSON. The request URL is",
               "    https://${FRONT_DOOR_HOST}/slack-webhook",
               "If Slack says the URL did not verify, carry on: the door is not open yet.",
               "Install the app to the workspace. Then put two values into",
               "${DISPATCHER_ENV_FILE}, by typing them, under the names piece 3 gives:",
               "    SLACK_BOT_TOKEN       <- OAuth & Permissions: Bot User OAuth Token",
               "    SLACK_SIGNING_SECRET  <- Basic Information: Signing Secret",
               "Never paste either into a chat, a ticket, a pull request or a repository."],
        "good": ("an app with exactly the four bot scopes of piece 6, no user scopes, and "
                 "the two names in the dispatcher's env file"),
        "not": "the notifier's token under SLACK_BOT_TOKEN — every session could read it",
    },
    "CK-C2": {
        "title": "Open the front door for one path",
        "why": ("The reverse proxy lives outside this repository, so nothing here can measure "
                "its path allowlist. The port behind it is card CK-C4's: do that one first."),
        "do": ["Add /slack-webhook to the proxy's path allowlist. Nothing else.",
               "From anywhere, ask the door for the new path with no signature:",
               "    curl -s -o /dev/null -w '%{http_code}\\n' -X POST \\",
               "      https://${FRONT_DOOR_HOST}/slack-webhook",
               "Good: 401. Not that: 404 or a timeout — the path is not reaching it.",
               "Ask the door for a path it must still refuse:",
               "    curl -s -m 5 https://${FRONT_DOOR_HOST}/status",
               "Good: the proxy's refusal. Not that: a JSON status — the door is too wide."],
        "good": "401 on /slack-webhook, and refused on /status",
        "not": "a JSON status through the door — it lets more than one path through",
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
               "    curl -s -m 5 http://127.0.0.1:${DISPATCHER_PORT}/status",
               "Good: a JSON status. Not that: nothing — the dispatcher is not running.",
               "On a SECOND device on the same network (another computer, or a phone",
               "with a terminal app), ask for the same page at that address:",
               "    curl -s -m 5 http://<this machine's network address>:${DISPATCHER_PORT}/status",
               "Good: \"Connection refused\", or a timeout after 5 seconds.",
               "Not that: a JSON status — the port is open to the network. Remove",
               "CYRUS_HOST_EXTERNAL from the dispatcher's env file, restart the dispatcher,",
               "and fix piece 4 before anything else.",
               "After the next reboot, run both checks again: the rule is loaded at boot."],
        "good": ("a JSON status from 127.0.0.1 on this machine, and refused or timed out "
                 "from the second device"),
        "not": "a JSON status on the second device — every route is open past the door",
    },
    "CK-C3": {
        "title": "Ask the bot, in the private channel, what it holds",
        "why": ("Only a live session shows the tool list the dispatcher really built. The "
                "source says what should be there; this is the measurement. No computer "
                "here can mention a bot as you."),
        "do": ["In the private channel, mention the bot:",
               "    @pipeline-chat list the name of every tool you can call, one per line",
               "Confirm these are ABSENT: Monitor, Task, ScheduleWakeup (and Agent, Write,",
               "Edit, Skill).",
               "Confirm tools beginning mcp__cyrus-tools ARE present. That is expected and",
               "accepted: the trim cannot remove that server.",
               "Ask a read question:",
               "    @pipeline-chat what is waiting on me in the tracker?",
               "Good: an answer drawn from the tracker.",
               "Record the tool list and the date in your private runbook."],
        "good": ("no Monitor, Task or ScheduleWakeup; mcp__cyrus-tools present, as expected; "
                 "a read question answered"),
        "not": "Monitor or Task in the list — the grant did not load; restart and ask again",
    },
}

NEVER = [
    "Never let this lane stay on with a second member in the Slack workspace.",
    "Never put the notifier's token in the dispatcher's env file.",
    "Never paste a token into a chat, a ticket, a pull request or a repository.",
    "Never merge a pull request, and never give one your own sign-off.",
    "Never apply or remove a protected label, and never move a ticket to ready.",
    "Never edit a guard, a hook, or this repository's settings files.",
]


def print_card(cid, conf=None):
    card = CARDS.get(cid)
    if not card:
        raise ConfError("no such checkpoint card: %s (have %s)"
                        % (cid, ", ".join(sorted(CARDS))))
    say("")
    say("=" * 74)
    say(" %s — %s" % (cid, card["title"]))
    say("=" * 74)
    if conf is None:
        say(" (no chat-lane.conf loaded: ${NAMES} below are yours to fill in)")
    say("")
    say("WHY THIS IS YOURS")
    for line in _wrap(card["why"]):
        say("  " + line)
    say("")
    say("WHAT TO DO")
    for line in card["do"]:
        say("  " + _fill(line, conf))
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
import json, os, re, sys
QUOTES = "\"'`"
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
        with open(path) as fh:
            c = json.load(fh)
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
            "instructionHead": str(r.get("appendInstruction") or "")[:HEAD],
        })
    return {"slackAllowedTools": tools(c.get("slackAllowedTools")),
            "slackMcpConfigs": mcp_rows(c.get("slackMcpConfigs")),
            "defaultDisallowedTools": tools(c.get("defaultDisallowedTools")),
            "promptDefaults": prompt_lists(c.get("promptDefaults")),
            "entries": entries}

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
    seen, external, port = {}, None, None
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        value = present(m.group(2))
        seen[m.group(1)] = bool(value)
        if m.group(1) == "CYRUS_HOST_EXTERNAL":
            external = value.strip().lower() == "true"
        if m.group(1) == "CYRUS_SERVER_PORT":
            digits = re.match(r"^\s*(\d+)", value)
            port = int(digits.group(1)) if digits else 0
        value = None
    # The dispatcher's parsePort: unset, unparseable or out of range means 3456.
    live_port = port if port and 1 <= port <= 65535 else 3456
    return {"names": sorted(k for k, v in seen.items() if v),
            "empty": sorted(k for k, v in seen.items() if not v),
            "hostExternalTrue": external,
            "serverPortMatches": live_port == int(sys.argv[3])}

def user_settings():
    path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(path) as fh:
            s = json.load(fh)
    except Exception as exc:
        return {"path": path, "error": why(exc)}
    perms = s.get("permissions") if isinstance(s, dict) else None
    deny = perms.get("deny") if isinstance(perms, dict) else None
    return {"path": path,
            "deny": [str(d)[:300] for d in deny] if isinstance(deny, list) else [],
            "hasHooks": bool(isinstance(s, dict) and s.get("hooks"))}

print(json.dumps({"config": config(sys.argv[1]), "env": env(sys.argv[2]),
                  "userSettings": user_settings()}))
'''.replace("[:HEAD]", "[:%d]" % INSTRUCTION_HEAD_CHARS)


def facts_command(conf):
    """The /bin/sh script `as_role` runs. Paths are arguments, never spliced into code."""
    return "/usr/bin/python3 -c %s %s %s %s" % (shlex.quote(FACTS_PY),
                                                shlex.quote(conf["DISPATCHER_CONFIG"]),
                                                shlex.quote(conf["DISPATCHER_ENV_FILE"]),
                                                shlex.quote(conf["DISPATCHER_PORT"]))


# The root reads `verify` makes for the port block — the Stage E installer's `as_root`
# helper — and the two world-readable files it cats. Every one only reads: `-s` shows,
# and pfctl(8)'s load, enable, flush and release flags (-f, -E, -e, -F, -X) never appear.
PF_READ_RULES = ["/sbin/pfctl", "-a", PF_ANCHOR, "-s", "rules"]
PF_READ_INFO = ["/sbin/pfctl", "-s", "info"]


def pf_probe(runner):
    """Four read-only answers. Nothing here parses them; `check_port_block` does."""
    def answer(res):
        return {"rc": res.rc, "out": res.out, "err": (res.err or "").strip()[:160]}
    return {"rules": answer(runner.as_root(PF_READ_RULES)),
            "info": answer(runner.as_root(PF_READ_INFO)),
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


def check_grant(cfg):
    live = cfg.get("slackAllowedTools")
    entries = cfg.get("entries") or []
    lines, problems = [], []
    if live == "not-a-list":
        return _row("grant", FAILED, "slackAllowedTools is not a list")
    if not live:
        problems.append("slackAllowedTools is unset or empty, so the built-in chat grant "
                        "applies, Monitor and Task included (ToolPermissionResolver.js:69-71)")
    else:
        extra = [t for t in live if t not in OWNER_GRANT]
        missing = [t for t in OWNER_GRANT if t not in live]
        if extra:
            problems.append("slackAllowedTools holds what the owner did not approve: %s"
                            % ", ".join(extra))
        if missing:
            problems.append("slackAllowedTools lacks: %s" % ", ".join(missing))
    if entries and entries[0].get("mcpAllowedTools"):
        first = entries[0]
        problems.append("the first repository entry %s lists %s in allowedTools, and the "
                        "dispatcher adds those to the chat grant (RunnerConfigBuilder.js:63-66)"
                        % (first.get("id") or first.get("name") or "#0",
                           ", ".join(first["mcpAllowedTools"])))
    for e in entries[1:]:
        if e.get("mcpAllowedTools"):
            lines.append("note: entry %s lists %s in allowedTools. Harmless while it is not "
                         "the first entry; the chat grant takes the first "
                         "(ChatRepositoryProvider.js:18-20)."
                         % (e.get("id") or e.get("name"), ", ".join(e["mcpAllowedTools"])))
    if problems:
        return _problem_row("grant", BLOCKED, problems, lines + [
            '  paste at the top level:  "slackAllowedTools": ' + json.dumps(OWNER_GRANT)])
    return _row("grant", ALREADY_DONE, "slackAllowedTools is the approved list", lines)


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
            _l, missing, _s = fence_entry(own, default)
            if missing:
                lines.append("note: review entry %s lacks %s. Its own installer owns it: "
                             "run the Stage E installer's verify." % (label, ", ".join(missing)))
            continue
        fenced_count += 1
        composed, missing, source = fence_entry(own, default)
        if missing:
            problems.append("%s (%s entry) lacks %s; its effective list comes from %s"
                            % (label, kind, ", ".join(missing), source))
            lines.append("  paste into %s:  \"disallowedTools\": %s" % (label,
                                                                        json.dumps(composed)))
        if own is None and "DISALLOWED_TOOLS" in env_names:
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
                            "Slack rules" % ptype)
    if problems:
        return _problem_row("coding-fence", FAILED if any("not a list" in p for p in problems)
                            else BLOCKED, problems, lines)
    if unknown:
        return _problem_row("coding-fence", UNKNOWN, unknown, lines)
    return _row("coding-fence", ALREADY_DONE,
                "%d non-review entr%s carry both Slack rules"
                % (fenced_count, "y" if fenced_count == 1 else "ies"), lines)


def check_chat_mcp(cfg):
    rows = cfg.get("slackMcpConfigs")
    if not rows:
        return _row("chat-mcp-configs", ALREADY_DONE, "slackMcpConfigs is empty or unset")
    lines = []
    for r in rows:
        if r.get("servers") is not None:
            lines.append("WARN: %s adds server(s): %s" % (r.get("path"),
                                                          ", ".join(r["servers"]) or "none"))
        else:
            lines.append("WARN: %s: %s" % (r.get("path"), r.get("error")))
    return _row("chat-mcp-configs", BLOCKED,
                "slackMcpConfigs loads extra servers into every chat session "
                "(RunnerConfigBuilder.js:52-57); the approved lane has none", lines)


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
    plist = pf.get("plist") or {}
    if plist.get("rc") != 0:
        problems.append("the boot LaunchDaemon is not installed at %s, so a reboot drops the "
                        "rule" % PF_DAEMON_PLIST)
    else:
        text = plist.get("out") or ""
        lacks = [part for part in (PF_ANCHOR, PF_RULES_PATH, "<string>-E</string>",
                                   "<key>RunAtLoad</key>") if part not in text]
        if lacks:
            problems.append("%s does not carry %s, so it would not load the rule at boot"
                            % (PF_DAEMON_PLIST, ", ".join(lacks)))
    rules_file = pf.get("rulesFile") or {}
    if rules_file.get("rc") != 0:
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
                "%s refuses port %s for inet and inet6 off loopback, pf is enabled, and the "
                "boot files are installed. Card CK-C4 is the proof from the network"
                % (PF_ANCHOR, port))


def check_user_settings(conf, us, env_facts):
    wanted = user_deny_patterns(conf)
    names = set((env_facts or {}).get("names") or [])
    path = us.get("path") or "~/.claude/settings.json"
    if "CLAUDE_CONFIG_DIR" in names:
        return _row("user-settings", UNKNOWN,
                    "the dispatcher env file sets CLAUDE_CONFIG_DIR, so sessions read user "
                    "settings from that directory, not %s" % path)
    err = us.get("error")
    if err == "missing":
        return _row("user-settings", BLOCKED, "no user settings file at %s" % path,
                    ["compose piece 5 has the block to merge"])
    if err and err.startswith("unparseable"):
        return _row("user-settings", FAILED, "%s is %s" % (path, err))
    if err:
        return _row("user-settings", UNKNOWN, "%s is %s" % (path, err))
    missing = [p for p in wanted if p not in (us.get("deny") or [])]
    if missing:
        return _row("user-settings", BLOCKED,
                    "%d composed deny rule(s) missing from %s" % (len(missing), path),
                    ["missing: " + m for m in missing])
    return _row("user-settings", ALREADY_DONE,
                "%s carries every composed deny rule" % path)


CHECKS = ("grant", "coding-fence", "chat-mcp-configs", "dispatcher-env",
          "notifier-token-absent", "hosted-keys-absent", "port-block", "user-settings")


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
        rows.append(check_notifier_absent(conf, env_facts))
        rows.append(check_hosted_keys_absent(env_facts))
        rows.append(check_user_settings(conf, us, env_facts))
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


def refusal_text(found):
    return ("REFUSED: `verify` reads the dispatcher's config and env file as the role account,\n"
            "  and this is an agent environment (%s set). The config holds the tracker's\n"
            "  tokens and the env file holds the chat bot's. The probe prints names only,\n"
            "  but a session has no business running it. There is no override flag.\n"
            "  A PERSON runs this, in a terminal:  python3 %s verify\n"
            "  Read-only meanwhile:  compose | card <CK-id>" % (", ".join(found), _self_path()))


def cmd_verify(conf, runner, sudo):
    found = agent_env_markers_present()
    if found:
        say(refusal_text(found))
        return EX_REFUSED
    runner.dry_run = True
    account = conf["ROLE_ACCOUNT"]
    say("Chat lane verify — read-only. It reads the dispatcher config, its env file and the")
    say("user settings as %s, through one program that prints names and tool lists and" % account)
    say("never a value, and asks pf, as root, what it has loaded. Your login password may be")
    say("asked for, once.")
    sudo.acquire("`verify` reads three files as the %s role account and asks pf, as root, "
                 "which rules it holds. It changes nothing." % account, "verify")
    res = runner.as_role(account, facts_command(conf))
    facts = None
    if res.ok:
        try:
            facts = json.loads(res.out)
        except ValueError:
            facts = None
    rows = evaluate(facts, conf, pf_probe(runner))
    if facts is None:
        why = ("exit %d: %s" % (res.rc, (res.err or "").strip()[:160]) if not res.ok
               else "its output was not JSON")
        for r in rows:
            if r["outcome"] == UNKNOWN and r["check"] != "port-block":
                r["detail"] = "the read-only probe as %s did not run (%s)" % (account, why)
    say("")
    say("-- checks --")
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
    if runner.writes:
        say("")
        say("BUG: verify recorded %d write(s); that is a defect in this file."
            % len(runner.writes))
        return EX_FAILED
    code = worst_exit(rows)
    say("")
    say("No drift: every check measures as applied." if code == EX_OK else
        "Not clean (exit %d). Apply what the rows name, then run verify again." % code)
    say("Not measured from here, so they are cards: the Slack app (CK-C1), the front door "
        "(CK-C2), the live session (CK-C3) and the port from a second device (CK-C4).")
    return code


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="pipeline_chat_lane_setup.py",
        description="Compose the dispatcher's built-in Slack lane for a person to apply, "
                    "and verify it read-only.")
    p.add_argument("command", nargs="?", choices=["compose", "verify", "card"])
    p.add_argument("target", nargs="?", help="a CK-id for `card`")
    p.add_argument("--conf", default="chat-lane.conf")
    p.add_argument("--selftest", action="store_true")
    return p


def main(argv=None, runner=None, sudo=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.command:
        build_parser().print_usage()
        return EX_USAGE
    # BEFORE the conf is read: a refusal that happens after the first read already ran.
    if args.command == "verify":
        found = agent_env_markers_present()
        if found:
            say(refusal_text(found))
            return EX_REFUSED
    if args.command == "card":
        conf, errors = load_conf(args.conf)
        try:
            return print_card(args.target or "", conf if (conf and not errors) else None)
        except ConfError as exc:
            say(str(exc))
            return EX_USAGE
    conf, errors = load_conf(args.conf)
    if errors:
        say("Your conf has %d problem(s). Every one of them, in one pass:" % len(errors))
        for e in errors:
            say("  - " + e)
        return EX_USAGE
    if args.command == "compose":
        return cmd_compose(conf, args.conf)
    try:
        return cmd_verify(conf, runner or Runner(dry_run=True), sudo or SudoSession())
    except NoPrivilege as exc:
        say(str(exc))
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

    def _exec(self, argv, stdin, timeout):
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


def _pf_answers(rules=None, info=None, plist=None, rules_file=None):
    """The four read-only pf answers, healthy unless one is overridden."""
    return [("pfctl -a %s -s rules" % PF_ANCHOR,) + (rules or (0, GOOD_PF_RULES_OUT, "")),
            ("pfctl -s info",) + (info or (0, GOOD_PF_INFO_OUT, "")),
            ("/bin/cat %s" % PF_DAEMON_PLIST,) + (plist or (0, pf_plist(), "")),
            ("/bin/cat %s" % PF_RULES_PATH,) + (rules_file or (0, pf_rules("3456"), ""))]


def _argv_allowed(argv, account):
    """True only for the five reads `verify` may run."""
    if argv[:6] == ["sudo", "-u", account, "-H", "/bin/sh", "-c"] and len(argv) == 7:
        return argv[6].startswith("cd / && /usr/bin/python3 -c ")
    return argv in (["sudo"] + PF_READ_RULES, ["sudo"] + PF_READ_INFO,
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


GOOD_CONF_TEXT = """
ROLE_ACCOUNT=_exdispatch
DISPATCHER_CONFIG=/opt/example-dispatcher/config.json
DISPATCHER_ENV_FILE=/opt/example-dispatcher/.env
FRONT_DOOR_HOST=chat.example.com
"""

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
    approved_literal = ["Read", "Bash(git -C * pull)", "WebFetch", "WebSearch", "SendMessage",
                        "ToolSearch", "mcp__slack", "mcp__linear"]
    expect("grant-is-approved", OWNER_GRANT == approved_literal, OWNER_GRANT)
    expect("grant-clean", grant_problems(OWNER_GRANT) == [], grant_problems(OWNER_GRANT))
    for tool in ("Monitor", "Task", "Agent", "ScheduleWakeup", "Skill", "Write", "Edit",
                 "NotebookEdit", "CronCreate", "RemoteTrigger", "Workflow"):
        expect("grant-lacks-" + tool, tool not in OWNER_GRANT)
        expect("grant-mutant-" + tool, grant_problems(OWNER_GRANT + [tool]) != [],
               "a grant holding %s passed" % tool)
    for bash in ("Bash", "Bash(*)", "Bash( * )"):
        expect("grant-no-unscoped-bash", not any(t == bash for t in OWNER_GRANT))
        expect("grant-mutant-unscoped-bash", grant_problems(OWNER_GRANT + [bash]) != [],
               "a grant holding %s passed" % bash)
    rc, out = _capture(cmd_compose, conf)
    shown = None
    for line in out.splitlines():
        if line.strip().startswith('"slackAllowedTools":'):
            shown = json.loads(line.split(":", 1)[1])
    expect("compose-prints-approved-grant", rc == EX_OK and shown == approved_literal,
           (rc, shown))

    # -- 2. a mutant grant containing Monitor turns compose red ----------------
    real_grant = globals()["OWNER_GRANT"]
    try:
        globals()["OWNER_GRANT"] = real_grant + ["Monitor"]
        rc_mutant, _o = _capture(cmd_compose, conf)
        expect("compose-mutant-monitor-red", rc_mutant == EX_FAILED, rc_mutant)
    finally:
        globals()["OWNER_GRANT"] = real_grant

    # compose shape: one-member warning first, the appended servers right under the grant
    flat = " ".join(out.split())
    first_piece = out.find("PIECE 1")
    expect("compose-one-member-at-top",
           0 <= out.find("ONE MEMBER") < first_piece, "the workspace warning is not first")
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
    restart_step = out.find("Piece 3, then restart", order_at)
    c4_step = out.find("Card CK-C4", order_at)
    expect("order-port-block-before-restart", order_at < load_step < restart_step < c4_step,
           (load_step, restart_step, c4_step))
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
    expect("deny-anchored", all(p.startswith("Read(//") for p in anchored_all), anchored_all)
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
        vconf = dict(conf, DISPATCHER_CONFIG=cfg_path, DISPATCHER_ENV_FILE=env_path)
        review_brief = REVIEW_BRIEF_FINGERPRINT + ". More."
        planning_brief = PLANNING_BRIEF_FINGERPRINT + ". More."

        def good_config():
            return {
                "linearWorkspaces": {"ws": {"linearToken": SENTINELS[3]}},
                "slackAllowedTools": list(OWNER_GRANT),
                "defaultDisallowedTools": ["Bash(rm -rf *)", "mcp__slack", "mcp__slack__*"],
                "repositories": [
                    {"id": "coding-a", "name": "a", "allowedTools": ["Read"],
                     "disallowedTools": ["Edit", "mcp__slack", "mcp__slack__*"]},
                    {"id": "coding-b", "name": "b",
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
                    "CYRUS_HOST_EXTERNAL=true\n" % (SENTINELS[0], SENTINELS[1]))

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
                                  vconf["DISPATCHER_PORT"]],
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
        cfg_m["slackAllowedTools"] = list(OWNER_GRANT) + ["Monitor"]
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
        cfg_m = good_config()
        cfg_m["repositories"][2]["disallowedTools"] = ["Bash"]
        rows = evaluate(facts_of(probe(cfg_m, good_env(), good_settings)), vconf)
        fence_row = [r for r in rows if r["check"] == "coding-fence"][0]
        expect("verify-fence-review-skipped", fence_row["outcome"] == ALREADY_DONE
               and any("reviews-a" in l for l in fence_row["lines"]), fence_row)

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

    # cards
    for cid in ("CK-C1", "CK-C2", "CK-C3", "CK-C4"):
        rc_card, card_out = _capture(print_card, cid, conf)
        expect("card-" + cid, rc_card == EX_OK and "WHY THIS IS YOURS" in card_out
               and "NEVER" in card_out and "${" not in card_out, card_out[:200])
    _rc, c4 = _capture(print_card, "CK-C4", conf)
    flat_c4 = " ".join(c4.split())
    expect("card-c4-content", all(t in flat_c4 for t in (
        "curl -s -m 5 http://127.0.0.1:3456/status", "SECOND device",
        "<this machine's network address>:3456/status", "Connection refused", "timeout",
        "proves nothing")), c4)
    _rc, c3 = _capture(print_card, "CK-C3", conf)
    expect("card-c3-content", all(t in c3 for t in ("Monitor", "Task", "ScheduleWakeup",
                                                    "mcp__cyrus-tools", "expected")))
    _rc, c2 = _capture(print_card, "CK-C2", conf)
    expect("card-c2-filled", "https://chat.example.com/slack-webhook" in c2)
    expect("card-unknown", _capture(main, ["card", "CK-9", "--conf", "/nonexistent"])[0]
           == EX_USAGE)

    # -- 11. the source: no write to a dispatcher path, no merge/label/state path
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split(SELFTEST_SENTINEL, 1)[0]
    above_scan = "\n".join(l for l in above.splitlines() if _BANNED_MARK not in l)
    whole_scan = "\n".join(l for l in src.splitlines() if _BANNED_MARK not in l)
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


if __name__ == "__main__":
    sys.exit(main())
