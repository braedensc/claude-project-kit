#!/usr/bin/env python3
"""Bridge installer — the Slack bridge (scripts/pipeline_bridge.py), composed for a person.

    python3 scripts/pipeline_bridge_setup.py compose  [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py token    [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py install  [--conf bridge.conf] [--apply]
    python3 scripts/pipeline_bridge_setup.py verify   [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py card CK-B1
    python3 scripts/pipeline_bridge_setup.py --selftest

WHAT IT INSTALLS (KIT-220, KIT-221). One job, run by launchd every INTERVAL_SECONDS as the
dispatcher's own role account (owner decision, 2026-10-10): it reads the chat channels, and
with DM_USER_IDS each listed member's DM with its app (KIT-295), checks each sender with
Slack itself, and asks its own questions. It acts only with ACT=yes,
the default being no. It needs its OWN Slack app: if it posted as the chat bot, the chat bot
could post a question that looked like the bridge's.

    compose   prints the bridge app's manifest, the job's config, its LaunchDaemon plist and
              the order to do things in. Reads nothing live; runs anywhere, a model included.
    token     asks for the bridge app's bot token at a hidden prompt and writes it into the
              role account's env file, as that account, under SLACK_TOKEN_ENV. Refuses a
              token that is already in that file under another name (the chat bot's or the
              notifier's), and refuses the dispatcher's env file as ENV_FILE. Prints names
              and lengths only.
    install   writes the config (as the role account) and the plist (as root). Dry run by
              default; `--apply` writes. The plist is installed disabled, so a restart does
              not load it either. It never loads the job: that is card CK-B2.
    verify    read-only: the config and plist are the composed ones, the env file names both
              credentials, the job's own dry run passes as the role account, launchd holds
              the job and runs it every INTERVAL_SECONDS, its heartbeat is fresh, and the
              notifier's pings file, in NOTIFIER_STATE_DIR, is there and is the notifier's
              (KIT-224). With NOTIFIER_JOB_LABEL set, a launchd interval that is not
              INTERVAL_SECONDS blocks, and its `notifier` row judges the notifier's
              heartbeat by the job's own rule, checks the job runs often enough to tell the
              Mac's sleep from a quiet notifier, and checks launchd runs the notifier as its
              plist says; empty, it says the watch is off (KIT-285).
    card      CK-B1 create the Slack app, CK-B2 load the job, CK-B3 turn the bridge off,
              CK-B4 prove it live.

WHO RUNS SUDO. `token`, `install` and `verify` ask for the administrator password once,
then run sudo themselves: as the role account (`sudo -u`), and as root to write the plist
and to ask launchd whether the job is loaded. None of them starts or stops the job. The
`launchctl` lines that do (cards CK-B2 and CK-B3) are printed for the person to run.

REFUSES IN AN AGENT ENVIRONMENT, before the conf is read: `token`, `install` and `verify`.
`verify` runs the job's own dry run, which reads the owner's tracker key; a model has no
business running it. `compose` and `card` print and read nothing live, so they are allowed.
The markers are environment variables a session could unset: tamper-evident, not
tamper-proof, as everywhere else in this kit.

EXIT CODES — the Stage E installer's (contract §13): 0 done or nothing to do, 1 failed,
2 usage or conf, 3 refused for safety, 4 could not measure, 5 no administrator access,
10 blocked on a person (a dry run that found something to write, or a row not yet applied).
"""
import argparse
import getpass
import hashlib
import json
import os
import re
import shlex
import sys
from xml.sax.saxutils import escape as _xml_escape

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
import pipeline_stage_e_setup as se  # noqa: E402
import pipeline_bridge as bridge  # noqa: E402

EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_NOPRIV, EX_BLOCKED = (
    se.EX_OK, se.EX_FAILED, se.EX_USAGE, se.EX_REFUSED, se.EX_UNKNOWN, se.EX_NOPRIV,
    se.EX_BLOCKED)
say = se.say

DEFAULT_CONF = "bridge.conf"
BRIDGE_SCRIPT = "pipeline_bridge.py"
LOG_NAME = "bridge.log"
TOKEN_RE = re.compile(r"^xoxb-[A-Za-z0-9-]{20,}$")
LABEL_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*){2,}$")
ACCOUNT_RE = re.compile(r"^_?[a-z][a-z0-9_-]{0,31}$")
PATH_RE = re.compile(r"^(~/|/)[A-Za-z0-9._/-]+$")
INTERVAL_MIN, INTERVAL_MAX = 15, 3600          # INTERVAL_SECONDS, in seconds
# The bridge app's scopes: EXACTLY what pipeline_bridge.py calls, each cited.
SCOPE_CITES = {
    "groups:history": "conversations.history and conversations.replies in a private "
                      "channel (SlackClient.history, SlackClient.replies)",
    "users:read": "users.info, the sender check (SlackClient.user)",
    "chat:write": "chat.postMessage, its questions and answers (SlackClient.post)",
    "im:history": "conversations.history and conversations.replies in a member's DM with "
                  "the app, with DM_USER_IDS set (SlackClient.history, SlackClient.replies, "
                  "read for each DM Pass.open_dms opens; KIT-295)",
    "im:write": "conversations.open, which names a member's DM with the app "
                "(SlackClient.open_dm, called by Pass.open_dms; KIT-295)",
}

CONF_KEYS = {
    "ROLE_ACCOUNT": ("", "the dispatcher's role account the job runs as"),
    "ROLE_KIT_CLONE": ("", "that account's clone of this kit, which the job runs from"),
    "ENV_FILE": ("~/.stage-e/env", "the role account's env file, which the job sources"),
    "SLACK_TOKEN_ENV": ("BRIDGE_SLACK_BOT_TOKEN", "the NAME the bridge app's token goes under"),
    "LINEAR_KEY_ENV": ("STAGE_E_LINEAR_API_KEY", "the NAME of the owner's tracker key"),
    "CHANNEL_IDS": ("", "the private channel id(s) it reads, comma-separated"),
    "OWNER_USER_ID": ("", "the tracker user id the key belongs to"),
    "BOT_USER_ID": ("", "the bridge app's own Slack bot user id (its member ID)"),
    "DISPATCHER_ENV_FILE": ("", "optional: the dispatcher's env file, where the chat bot's "
                                "token lives; `token` refuses that token too"),
    "TEAM_KEYS": ("", "the work teams it asks about, comma-separated"),
    "ALLOWED_SLACK_USERS": ("", "optional: the only Slack user ids who may answer"),
    "BRIDGE_CONFIG": ("~/.stage-e/bridge.json", "where the job's config is written"),
    "STATE_DIR": ("~/.stage-e/state", "where the job keeps its state and heartbeat"),
    "JOB_LABEL": ("", "the LaunchDaemon label, reverse-DNS, e.g. com.example.bridge"),
    "INTERVAL_SECONDS": ("60", "how often the job runs"),
    "APP_NAME": ("Pipeline bridge", "the Slack app's display name"),
    "ACT": ("no", "yes: a verified yes moves an idea into Plan it, or approves an epic and "
                  "starts its children, as the owner. no: the bridge asks and records only"),
    "PLAN_IT_STATE": ("Plan it", "the name of each team's Plan it state"),
    "AGENT_USER_NAME": ("", "the dispatcher's app user, by display name, that an approved "
                            "epic's children are handed to; empty refuses every approve"),
    "EPIC_WIP": ("1", "how many children of one approved epic may run at once, 1 to 10"),
    "READY_STATE": ("Todo", "the name of each team's to-do state an approved epic moves to"),
    "DONE_STATE": (bridge.DEFAULT_DONE_STATE, "the name of each team's done state a plan moves "
                                              "to once every piece of it is merged (KIT-299)"),
    "HANDOFF_STATES": (", ".join(bridge.DEFAULT_HANDOFF_STATES),
                       "the states, by name, a child sits in while its pull request waits "
                       "for your merge, comma-separated"),
    "NOTIFIER_STATE_DIR": ("~/.stage-e/state", "the notifier's STATE_DIR, as notifier.conf "
                                               "sets it: the bridge reads the pings it "
                                               "records there (KIT-224)"),
    "NOTIFIER_BOT_USER_ID": ("", "optional: the notifier app's own Slack bot user id (its "
                                 "member ID). A reply under one of its pings that it did "
                                 "not record is then refused aloud, and `verify` needs the "
                                 "pings file"),
    "DM_USER_IDS": ("", "optional: the Slack user ids whose DM with the bridge app it also "
                        "reads and answers in, comma-separated (KIT-295). Each message there "
                        "is one of its own, so it notifies. Empty: no DM"),
    "NOTIFIER_JOB_LABEL": ("", "optional: the notifier's JOB_LABEL, as notifier.conf sets it. "
                               "Set, each pass reads how often launchd runs the notifier from "
                               "its plist, and says in Slack when it goes quiet or keeps "
                               "failing (KIT-285). Empty: the watch is off"),
}
REQUIRED = ("ROLE_ACCOUNT", "ROLE_KIT_CLONE", "CHANNEL_IDS", "OWNER_USER_ID", "TEAM_KEYS",
            "JOB_LABEL", "BOT_USER_ID")


# ══════════════════════════════════════════════════════════════════════════════════════
# The conf
# ══════════════════════════════════════════════════════════════════════════════════════

def parse_conf(text):
    values, errors, where = {}, [], {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append("line %d is not KEY=value" % n)
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key not in CONF_KEYS:
            errors.append("line %d: unknown key %s" % (n, key))
            continue
        if key in where:
            errors.append("%s is set twice, on lines %d and %d" % (key, where[key], n))
            continue
        if bridge.notify.secret_hits(value):
            errors.append("line %d: %s holds a value shaped like a credential. No key here "
                          "ever holds one: the token goes in at `token`'s hidden prompt"
                          % (n, key))
            continue
        where[key] = n
        values[key] = value
    return values, errors


def split_list(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def validate_conf(values):
    conf = dict((k, values.get(k, d) or d) for k, (d, _doc) in CONF_KEYS.items())
    errors = ["%s is required" % k for k in REQUIRED if not conf.get(k)]
    if conf["ROLE_ACCOUNT"] and not ACCOUNT_RE.match(conf["ROLE_ACCOUNT"]):
        errors.append("ROLE_ACCOUNT %r is not an account name" % conf["ROLE_ACCOUNT"])
    for key in ("ROLE_KIT_CLONE", "ENV_FILE", "BRIDGE_CONFIG", "STATE_DIR",
                "DISPATCHER_ENV_FILE", "NOTIFIER_STATE_DIR"):
        if conf[key] and not PATH_RE.match(conf[key]):
            errors.append("%s must be an absolute or ~/ path of plain characters, got %r"
                          % (key, conf[key]))
    # THE CLONE STAGE E MOVES, AND NO OTHER (KIT-280). This installer never fetches or pulls.
    # The Stage E installer's `code` step moves only its own clone, stopping this job around
    # the move, so a job on any other clone would run the code it was installed with for good.
    clone = conf["ROLE_KIT_CLONE"]
    if clone and PATH_RE.match(clone) and clone.rstrip("/") != se.ROLE_KIT_CLONE_PATH:
        errors.append("ROLE_KIT_CLONE is %r, not Stage E's clone, %s. Nothing moves any other "
                      "clone, so a merged fix would never reach this job. Set it to %s; "
                      "docs/BRIDGE-OPERATOR.md, Step 1, says how to move a job installed on "
                      "another clone" % (clone, se.ROLE_KIT_CLONE_PATH, se.ROLE_KIT_CLONE_PATH))
    # The dispatcher hands its env file to every session it starts. The same file under
    # another spelling (`~/x` against an absolute path, or a link) is caught by the token
    # writer, which resolves both as the role account.
    if conf["DISPATCHER_ENV_FILE"] and (os.path.normpath(conf["DISPATCHER_ENV_FILE"])
                                        == os.path.normpath(conf["ENV_FILE"])):
        errors.append("ENV_FILE and DISPATCHER_ENV_FILE both name %s. The bridge's token goes "
                      "in the role account's own env file, never the dispatcher's: every "
                      "session the dispatcher starts can read that one" % conf["ENV_FILE"])
    if conf["JOB_LABEL"] and not LABEL_RE.match(conf["JOB_LABEL"]):
        errors.append("JOB_LABEL must be reverse-DNS like com.example.bridge, got %r"
                      % conf["JOB_LABEL"])
    if conf["ACT"] not in ("yes", "no"):
        errors.append("ACT must be yes or no, got %r" % conf["ACT"])
    if (not conf["INTERVAL_SECONDS"].isdigit()
            or not INTERVAL_MIN <= int(conf["INTERVAL_SECONDS"]) <= INTERVAL_MAX):
        errors.append("INTERVAL_SECONDS must be a whole number from %d to %d"
                      % (INTERVAL_MIN, INTERVAL_MAX))
    if not conf["EPIC_WIP"].isdigit() or not 1 <= int(conf["EPIC_WIP"]) <= 10:
        errors.append("EPIC_WIP must be a whole number from 1 to 10")
    # The notifier's own installer's rule for its JOB_LABEL, as Stage E's conf checks it.
    label = conf["NOTIFIER_JOB_LABEL"]
    if label and not se._RDNS_RE.match(label):
        errors.append("NOTIFIER_JOB_LABEL must be the notifier's reverse-DNS JOB_LABEL, or "
                      "empty, got %r" % label)
    elif label and label == conf["JOB_LABEL"]:
        errors.append("NOTIFIER_JOB_LABEL is the bridge's own JOB_LABEL. Set it to the "
                      "JOB_LABEL in notifier.conf, or leave it empty")
    # KIT-295: each a member's own user id, named once. The job checks the same.
    dms = split_list(conf["DM_USER_IDS"])
    bad = [u for u in dms if not bridge.SLACK_USER_RE.match(u)]
    if bad:
        errors.append("DM_USER_IDS must list Slack user ids like U0123456789, each a member's "
                      "own (their profile, then Copy member ID), got %s" % ", ".join(bad))
    elif len(set(dms)) != len(dms):
        errors.append("DM_USER_IDS names a member twice")
    # The job's own check runs whatever the checks above found, so every bad value is
    # named in one pass. A value both refuse is named once, by its conf key.
    named = [k for k in COMPOSED_AS if any(e.startswith(k + " ") for e in errors)]
    try:
        bridge.validate_config(dict(compose_config(conf),
                                    state_dir="/nonexistent-%s" % os.getpid()))
    except bridge.BridgeError as exc:
        errors.extend(e for e in str(exc).split("\n  - ")[1:] or [str(exc)]
                      if not any("'%s'" % COMPOSED_AS[k] in e for k in named))
    return conf, errors


# The conf keys with a check of their own above, and the config key each is composed into.
COMPOSED_AS = {"CHANNEL_IDS": "channel_ids", "OWNER_USER_ID": "owner_user_id",
               "BOT_USER_ID": "bot_user_id", "TEAM_KEYS": "team_keys", "EPIC_WIP": "epic_wip",
               "ACT": "act", "NOTIFIER_STATE_DIR": "pings_file",
               "INTERVAL_SECONDS": "run_interval_seconds", "DM_USER_IDS": "dm_user_ids"}


def load_conf(path):
    try:
        with open(path, encoding="utf-8") as fh:
            values, errors = parse_conf(fh.read())
    except OSError as exc:
        return None, ["could not read %s: %s" % (path, exc)]
    conf, more = validate_conf(values)
    return conf, errors + more


# ══════════════════════════════════════════════════════════════════════════════════════
# What it composes
# ══════════════════════════════════════════════════════════════════════════════════════

def compose_config(conf):
    """The job's config. `~` stays literal: the job expands it as the role account. The
    pings file is the notifier's, in ITS state dir: left to the bridge's own, two state
    dirs that differ would leave every answer under a ping unread (KIT-224 review)."""
    doc = {
        "schema": bridge.CONFIG_SCHEMA,
        "state_dir": conf["STATE_DIR"],
        "slack_token_env": conf["SLACK_TOKEN_ENV"],
        "linear_key_env": conf["LINEAR_KEY_ENV"],
        "channel_ids": split_list(conf["CHANNEL_IDS"]),
        "owner_user_id": conf["OWNER_USER_ID"],
        "bot_user_id": conf["BOT_USER_ID"],
        "team_keys": split_list(conf["TEAM_KEYS"]),
        "allowed_slack_users": split_list(conf["ALLOWED_SLACK_USERS"]),
        "act": conf["ACT"] == "yes",
        "plan_it_state": conf["PLAN_IT_STATE"],
        "ready_state": conf["READY_STATE"],
        "agent_user_name": conf["AGENT_USER_NAME"],
        "epic_wip": int(conf["EPIC_WIP"]) if conf["EPIC_WIP"].isdigit() else conf["EPIC_WIP"],
        "pings_file": pings_file(conf),
    }
    # Composed only when it is not the job's own default, so an install made before the
    # key existed is still the composed one (KIT-284).
    if split_list(conf["HANDOFF_STATES"]) != bridge.DEFAULT_HANDOFF_STATES:
        doc["handoff_states"] = split_list(conf["HANDOFF_STATES"])
    # KIT-299: the same, so a config written before the key existed stays the same.
    if conf["DONE_STATE"] != bridge.DEFAULT_DONE_STATE:
        doc["done_state"] = conf["DONE_STATE"]
    if conf["NOTIFIER_BOT_USER_ID"]:
        doc["notifier_bot_user_id"] = conf["NOTIFIER_BOT_USER_ID"]
    # KIT-295: composed only when set, so a config written before the key existed stays
    # the same. The notifier's record of its DM pages is beside its pings, in its STATE_DIR.
    if split_list(conf["DM_USER_IDS"]):
        doc["dm_user_ids"] = split_list(conf["DM_USER_IDS"])
        doc["notifier_posts_file"] = dm_posts_file(conf)
    # Composed only when set, so a config written before the key existed stays the same.
    if conf["NOTIFIER_JOB_LABEL"]:
        doc["notifier_plist"] = notifier_plist_path(conf)
        doc["run_interval_seconds"] = (int(conf["INTERVAL_SECONDS"])
                                       if conf["INTERVAL_SECONDS"].isdigit()
                                       else conf["INTERVAL_SECONDS"])
    return doc


def pings_file(conf):
    return bridge.notify.bridge_pings_path({"state_dir": conf["NOTIFIER_STATE_DIR"]})


def dm_posts_file(conf):
    """The notifier's record of the pages it posts into a DM as the bridge's app (KIT-295)."""
    return os.path.join(conf["NOTIFIER_STATE_DIR"], bridge.NOTIFIER_DM_POSTS_NAME)


def notifier_plist_path(conf):
    """The notifier's LaunchDaemon plist, where its installer writes it (KIT-285)."""
    return "/Library/LaunchDaemons/%s.plist" % conf["NOTIFIER_JOB_LABEL"]


def notifier_heartbeat(conf):
    """The notifier's heartbeat, in its STATE_DIR beside the pings file, `~` kept."""
    return bridge.notify.heartbeat_path({"state_dir": conf["NOTIFIER_STATE_DIR"]})


def config_bytes(conf):
    return json.dumps(compose_config(conf), indent=2, sort_keys=True) + "\n"


def manifest(conf):
    return {
        "display_information": {"name": conf["APP_NAME"]},
        "features": {"bot_user": {"display_name": conf["APP_NAME"], "always_online": False},
                     # KIT-295: the Messages tab, so a member can write to the app in its DM.
                     "app_home": {"home_tab_enabled": False, "messages_tab_enabled": True,
                                  "messages_tab_read_only_enabled": False}},
        "oauth_config": {"scopes": {"bot": sorted(SCOPE_CITES)}},
        "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False,
                     "token_rotation_enabled": False},
    }


def sh_path(value):
    return "$HOME/" + value[2:] if value.startswith("~/") else value


def job_command(conf):
    """The job's shell command: the env file sourced, then the bridge, by full path."""
    script = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + BRIDGE_SCRIPT
    return ('set -a; . "%s"; set +a; exec /usr/bin/python3 "%s" run --config "%s"'
            % (sh_path(conf["ENV_FILE"]), sh_path(script), sh_path(conf["BRIDGE_CONFIG"])))


def plist_path(conf):
    return "/Library/LaunchDaemons/%s.plist" % conf["JOB_LABEL"]


def resolve(value, home):
    return os.path.join(home, value[2:]) if value.startswith("~/") else value


# The Stage E installer's own plist template, INSTALLED DISABLED, as Stage A's job is.
# launchd loads every job in /Library/LaunchDaemons at boot, so a job installed and "not
# loaded" would start at the next restart, before the person's card CK-B2. Its enable line
# overrides `Disabled` in launchd's own record, which outlives a restart.
_RUN_AT_LOAD = "  <key>RunAtLoad</key><true/>\n"
PLIST = se.PLIST.replace(_RUN_AT_LOAD, _RUN_AT_LOAD + "  <key>Disabled</key><true/>\n")


def render_plist(conf, home):
    """The plist template above, filled in. launchd expands nothing."""
    log = os.path.dirname(resolve(conf["BRIDGE_CONFIG"], home)) + "/" + LOG_NAME
    return PLIST.format(
        label=_xml_escape(conf["JOB_LABEL"]), account=_xml_escape(conf["ROLE_ACCOUNT"]),
        home=_xml_escape(home), path=_xml_escape(se.DAEMON_PATH),
        command=_xml_escape(job_command(conf)), interval=int(conf["INTERVAL_SECONDS"]),
        log=_xml_escape(log))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════════════
# The cards
# ══════════════════════════════════════════════════════════════════════════════════════

CARDS = {
    "CK-B1": ("Create the bridge's own Slack app", [
        "Slack: Create New App -> From a manifest -> paste the manifest `compose` prints.",
        "Install it to the workspace. Copy its Bot User OAuth Token (it starts xoxb-).",
        "In EACH channel in CHANNEL_IDS, type: /invite @{APP_NAME}",
        "Click the app's name in the channel, open its profile, and Copy member ID. Put it "
        "in the conf as BOT_USER_ID: the job refuses any token that is not this bot's.",
        "Then put the token in place, at a hidden prompt:  python3 {SELF} token --conf {CONF}",
        "Made the app before? Update it instead of lines 1 and 2: open the app's Slack settings "
        "-> App Manifest -> paste the manifest `compose` prints -> Save Changes -> Reinstall "
        "to Workspace. If its Bot User OAuth Token changed, run line 5 again.",
        "With DM_USER_IDS set: find the app in Slack and open its Messages tab once. That is "
        "your DM with the bridge. Each member it names does the same.",
    ], "token prints the env var name and the token's length, and nothing else.",
       "the chat bot's own app, or the notifier's. The bridge needs its own bot identity, "
       "or the chat bot could post a question that looks like the bridge's."),
    "CK-B2": ("Load the bridge job", [
        "sudo launchctl enable system/{JOB_LABEL}",
        "sudo launchctl bootstrap system {PLIST}",
        "Wait one interval, then:  python3 {SELF} verify --conf {CONF}",
    ], "verify says the job is loaded and its heartbeat is fresh.",
       "`Bootstrap failed: 5`. The job is installed turned off, and CK-B3 turns it off "
       "again; until line 1 turns it on, the load fails. If line 1 ran, the job is already "
       "loaded, or was unloaded a moment ago. Run verify: if `loaded` passes, you are done. "
       "If it says launchd still runs the bridge at another interval, run card CK-B3, then "
       "this card again. If it is not loaded, wait ten seconds and run lines 1 and 2 again."),
    "CK-B4": ("Prove the bridge live (KIT-227)", [
        "Plan from Slack: in the chat channel, write \"I want\" and something small. Good: "
        "the tech lead files one idea and replies `plan <id>`; the bridge asks; after your "
        "`yes` the idea is in Plan it, its history shows your move at once, and the planner's "
        "ticket appears within ten minutes.",
        "Who may answer: on a fresh question, have a second full member, a guest and a bot "
        "each reply `yes`. Good: only the full member's yes acts, and each refusal is said in "
        "the thread.",
        "While the Mac is asleep: ask a question, put the Mac to sleep, reply `yes` from your "
        "phone, then wake it within 24 hours. Good: the bridge acts on that yes on its first "
        "pass after waking.",
        "Approve a plan: once one is filed, `approve <epic id>`, then `yes`. Good: the epic "
        "moves to Todo, its children start one at a time in order, and each child's session "
        "starts exactly as when you hand a ticket over yourself.",
        "Finish a plan: wait until every piece of an approved plan is merged. Good: within a "
        "few minutes the epic moves to {DONE_STATE} with one comment naming who approved it, "
        "and one wrap-up names each pull request. If its reviews left notes, one question "
        "asks first; after your `yes`, one ticket lands in the team's backlog with no labels, "
        "and nothing starts on it. Record whether the wrap-up called each pull request "
        "merged: that the tracker's link says so is not proven (KIT-299).",
        "Answer a stuck session: when a ping says a dev stopped to ask you something, reply "
        "under it. Good: one comment naming you lands in that session's thread, and it "
        "carries on.",
        "Ready to merge: when Stage E concludes a pull request clean or below the threshold, "
        "the bounce driver leaves its ready-to-merge mark, then moves the ticket to Needs "
        "Approval in the same pass, and the notifier pages on the mark. Good: a ping says the "
        "pull request is yours to merge and links it, and the ticket is in Needs Approval. "
        "A ticket moved to Needs Approval by hand pages nothing: that is expected.",
        "What the chat lane can read, with a decoy: run  sudo -u {ROLE_ACCOUNT} -H sh -c "
        "'echo decoy > {DECOY}'  then ask the tech lead to read {DECOY} . Good: it is "
        "refused. That proves {COPIES_RULE}, the rule for the env file's sibling copies (a "
        "writer's temp copy holds the token too). It does not test {ENV_RULE}, the rule for "
        "the env file itself: the next step checks that one. Then remove the decoy:  sudo -u "
        "{ROLE_ACCOUNT} -H sh -c 'rm {DECOY}'",
        "The env file's own rule, without reading the file: run  python3 "
        "scripts/pipeline_chat_lane_setup.py verify --conf chat-lane.conf . Good: its "
        "user-settings row is ALREADY-DONE, and the same script's  compose --piece 5 --conf "
        "chat-lane.conf  lists {ENV_RULE}. That "
        "row measures every deny rule the chat lane composes, in the role account's user "
        "settings. The rule is made from chat-lane.conf's ROLE_ENV_FILE, so that must be "
        "{ENV_FILE}. Do not have the tech lead read the env file itself: a read that worked "
        "would post the token into Slack.",
        "The env file's backups, with a decoy: `token` copies the whole env file into "
        "{BACKUPS} before it writes. Run  sudo -u {ROLE_ACCOUNT} -H sh -c 'echo decoy > "
        "{BACKUPS_DECOY}'  then ask the tech lead to read {BACKUPS_DECOY} . Good: it is "
        "refused. That proves {BACKUPS_RULE}, the chat lane's rule for its backups "
        "folder.{BACKUPS_NOTE} Then remove the decoy:  sudo -u {ROLE_ACCOUNT} -H sh -c 'rm "
        "{BACKUPS_DECOY}'",
        "The bridge's own files, with a decoy: its config {BRIDGE_CONFIG}, its log {LOG}, its "
        "state dir {STATE_DIR} (its questions, the messages it has seen, its heartbeat) and "
        "the notifier's pings file {PINGS}. None holds a credential, and the chat lane "
        "composes no rule for them: its  compose --piece 5 --conf chat-lane.conf  names none "
        "of these paths. Run  sudo -u {ROLE_ACCOUNT} -H sh -c 'echo decoy > {STATE_DECOY}'  "
        "then ask the tech lead to read {STATE_DECOY} . Expected: it is read. Record the "
        "criterion \"the chat lane cannot read the bridge's files\" as not held for these "
        "files, never as passed: that residual is accepted (KIT-162). Today no session can "
        "write them, which is what keeps them from being forged. Then remove the decoy:  "
        "sudo -u {ROLE_ACCOUNT} -H sh -c 'rm {STATE_DECOY}'",
        "Record each result as a comment on the live-proofs ticket, and update your private "
        "runbook.",
    ], "every step above held, except the bridge's own files, which are recorded as not held "
       "(KIT-162), and each result is a comment on the live-proofs ticket.",
       "a step taken on trust: mark it not proven instead, with its ticket."),
    "CK-B3": ("Turn the bridge off", [
        "sudo launchctl bootout system/{JOB_LABEL}",
        "sudo launchctl disable system/{JOB_LABEL}",
    ], "`launchctl print system/{JOB_LABEL}` says it could not find the service.",
       "removing the plist alone: launchd keeps a loaded job until it is booted out."),
}


def card_values(conf, conf_path):
    # The chat lane's own rule spelling (KIT-227 review): the card names the rules that
    # composer writes for this env file, never a guess at them.
    import pipeline_chat_lane_setup as chat_lane
    conf = conf or dict((k, d or "<%s>" % k) for k, (d, _doc) in CONF_KEYS.items())
    # Where `token` keeps its whole copies of the env file: the writer's own rule, below.
    backups = os.path.dirname(conf["ENV_FILE"]) + "/backups"
    return {"APP_NAME": conf["APP_NAME"], "JOB_LABEL": conf["JOB_LABEL"],
            "ROLE_ACCOUNT": conf["ROLE_ACCOUNT"],
            "PLIST": plist_path(conf), "SELF": os.path.abspath(__file__),
            "CONF": conf_path, "ENV_FILE": conf["ENV_FILE"],
            "DECOY": conf["ENV_FILE"] + ".decoy-proof",
            "ENV_RULE": chat_lane._path_rule(conf["ENV_FILE"]),
            "COPIES_RULE": chat_lane._path_rule(conf["ENV_FILE"] + ".*"),
            "BACKUPS": backups, "BACKUPS_DECOY": backups + "/decoy-proof",
            "BACKUPS_RULE": chat_lane.BACKUPS_DENY_RULE,
            "BACKUPS_NOTE": "" if backups == chat_lane.ROLE_BACKUPS else (
                " That rule names %s, not %s. Unless those are one folder, expect the decoy "
                "to be read: the copies of the token there are then readable, and this step "
                "is not held." % (chat_lane.ROLE_BACKUPS, backups)),
            "BRIDGE_CONFIG": conf["BRIDGE_CONFIG"], "STATE_DIR": conf["STATE_DIR"],
            "LOG": os.path.dirname(conf["BRIDGE_CONFIG"]) + "/" + LOG_NAME,
            "STATE_DECOY": conf["STATE_DIR"].rstrip("/") + "/decoy-proof",
            "PINGS": pings_file(conf), "DONE_STATE": conf["DONE_STATE"]}


def print_card(cid, conf=None, conf_path=DEFAULT_CONF):
    title, steps, good, bad = CARDS[cid]
    vals = card_values(conf, conf_path)

    def fill(text):
        for k, v in vals.items():
            text = text.replace("{%s}" % k, v)
        return text
    say("%s — %s" % (cid, title))
    for n, step in enumerate(steps, 1):
        say("  %d. %s" % (n, fill(step)))
    say("  Good: %s" % fill(good))
    say("  Not that: %s" % fill(bad))
    say("")


# ══════════════════════════════════════════════════════════════════════════════════════
# The role account: one read program, two writer programs
# ══════════════════════════════════════════════════════════════════════════════════════

def agent_markers_present():
    return [m for m in AGENT_ENV_MARKERS if m in os.environ]


def refused(found):
    say("REFUSED: an agent environment is set (%s). This command reads or writes the role "
        "account's files and the owner's credentials; a person runs it, never a session."
        % ", ".join(found))
    return EX_REFUSED


_RESUME = "  Fix that and run the same command again:"


def no_privilege(exc, resume):
    """Exit 5, saying why. The Stage E refusal's own words, with its last line naming
    THIS installer, not Stage E's."""
    say("")
    say(str(exc).split("\n" + _RESUME)[0])
    say(_RESUME)
    say("      python3 %s %s" % (os.path.abspath(__file__), resume))
    return EX_NOPRIV


# Reads the env file's NAMES (never a value), the config's bytes and the heartbeat. Every
# read is wrapped; an error is reported by its class.
FACTS_PY = r'''
import hashlib, json, os, re, sys
env_path, cfg_path, beat_path, pings_path, nbeat_path = [os.path.expanduser(a)
                                                         for a in sys.argv[1:6]]
out = {}
try:
    names = []
    for line in open(env_path):
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m and m.group(2).strip().strip("'\"").strip():
            names.append(m.group(1))
    out["env_names"] = sorted(set(names))
except Exception as exc:
    out["env_error"] = type(exc).__name__
try:
    raw = open(cfg_path).read()
    out["config_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
except FileNotFoundError:
    out["config_missing"] = True
except Exception as exc:
    out["config_error"] = type(exc).__name__
try:
    beat = json.load(open(beat_path))
    if isinstance(beat, dict):
        out["heartbeat"] = dict((k, beat.get(k)) for k in ("schema", "at", "exit", "dry",
                                                           "summary"))
    else:
        out["heartbeat_error"] = "not a JSON object"
except FileNotFoundError:
    out["heartbeat_missing"] = True
except Exception as exc:
    out["heartbeat_error"] = type(exc).__name__
try:
    pings = json.load(open(pings_path))
    out["pings_schema"] = (pings.get("schema") if isinstance(pings, dict)
                           and isinstance(pings.get("messages"), dict) else None)
except FileNotFoundError:
    out["pings_missing"] = True
except ValueError:
    out["pings_schema"] = None
except Exception as exc:
    out["pings_error"] = type(exc).__name__
try:
    nbeat = json.load(open(nbeat_path))
    if isinstance(nbeat, dict):
        out["notifier_beat"] = dict((k, nbeat.get(k)) for k in ("schema", "at", "exit", "dry"))
    else:
        out["notifier_beat_error"] = "not a JSON object"
except FileNotFoundError:
    out["notifier_beat_missing"] = True
except Exception as exc:
    out["notifier_beat_error"] = type(exc).__name__
print(json.dumps(out))
'''

# Writes the token: read from stdin, never argv. Refuses one that another name already holds.
TOKEN_WRITER_PY = r'''
import os, re, sys, time
env_path, name = os.path.expanduser(sys.argv[1]), sys.argv[2]
others = [os.path.expanduser(a) for a in sys.argv[3:] if a]
for other in others:
    if os.path.realpath(other) == os.path.realpath(env_path):
        print("REFUSED: %s is the same file as DISPATCHER_ENV_FILE (%s). The bridge's token "
              "goes in the role account's own env file, never the dispatcher's: every session "
              "the dispatcher starts can read that one. Nothing was written."
              % (env_path, other))
        sys.exit(3)
token = sys.stdin.read().strip()
lines = []
if os.path.exists(env_path):
    with open(env_path) as fh:
        lines = fh.read().splitlines()


def holds(path_lines, path, own):
    for line in path_lines:
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m and not (own and m.group(1) == name) and m.group(2).strip().strip("'\"") == token:
            print("REFUSED: that token is already in %s under %s. The bridge needs its own "
                  "Slack app's token." % (path, m.group(1)))
            sys.exit(3)


holds(lines, env_path, True)
for other in others:
    try:
        with open(other) as fh:
            holds(fh.read().splitlines(), other, False)
    except OSError as exc:
        print("REFUSED: %s could not be read (%s), so the token could not be compared with "
              "the chat bot's. Nothing was written." % (other, type(exc).__name__))
        sys.exit(3)
backups = os.path.join(os.path.dirname(env_path), "backups")
os.makedirs(backups, mode=0o700, exist_ok=True)
if os.path.exists(env_path):
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bk = os.path.join(backups, "env.bridge-setup.%s.%d" % (stamp, os.getpid()))
    fd = os.open(bk, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("backed up the env file to %s" % bk)
    # Each backup is a whole copy of the file, the owner's key included: keep three.
    mine = sorted(n for n in os.listdir(backups) if n.startswith("env.bridge-setup."))
    for old in mine[:-3]:
        os.remove(os.path.join(backups, old))
kept = [l for l in lines if not re.match(r"^\s*(?:export\s+)?%s=" % re.escape(name), l)]
kept.append("%s=%s" % (name, token))
tmp = "%s.bridge-setup.%d" % (env_path, os.getpid())
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write("\n".join(kept) + "\n")
os.replace(tmp, env_path)
os.chmod(env_path, 0o600)
print("wrote %s (%d characters) into %s, mode 600" % (name, len(token), env_path))
'''

# Writes the config from stdin, mode 600, creating its directory at 700.
CONFIG_WRITER_PY = r'''
import os, sys
path = os.path.expanduser(sys.argv[1])
body = sys.stdin.read()
os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
tmp = "%s.bridge-setup.%d" % (path, os.getpid())
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write(body)
os.replace(tmp, path)
print("wrote %s (%d bytes), mode 600" % (path, len(body)))
'''


def py(program, *args):
    """One program for the role account's shell. Paths go as typed, `~/` included, and the
    program expands them itself: a quoted `$HOME` would reach it literally."""
    return "/usr/bin/python3 -c %s %s" % (shlex.quote(program),
                                         " ".join(shlex.quote(a) for a in args))


def home_of(runner, account):
    res = runner.as_role(account, 'printf "%s" "$HOME"')
    return res.out.strip() if res.ok and res.out.strip().startswith("/") else None


def read_facts(runner, conf):
    res = runner.as_role(conf["ROLE_ACCOUNT"], py(
        FACTS_PY, conf["ENV_FILE"], conf["BRIDGE_CONFIG"],
        conf["STATE_DIR"].rstrip("/") + "/bridge-heartbeat.json", pings_file(conf),
        notifier_heartbeat(conf)))
    if not res.ok:
        return None
    try:
        return json.loads(res.out)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════════════════
# The subcommands
# ══════════════════════════════════════════════════════════════════════════════════════

def cmd_compose(conf, conf_path):
    say("THE SLACK BRIDGE — composed from %s. Nothing live was read; nothing was written."
        % conf_path)
    say("")
    say("PIECE 1 — the bridge app's manifest (its OWN app; card CK-B1):")
    for line in json.dumps(manifest(conf), indent=2).splitlines():
        say("    " + line)
    for scope, why in sorted(SCOPE_CITES.items()):
        say("    %-16s %s" % (scope, why))
    say("")
    say("PIECE 2 — the job's config, written by `install --apply` to %s:"
        % conf["BRIDGE_CONFIG"])
    for line in config_bytes(conf).splitlines():
        say("    " + line)
    say("")
    say("PIECE 3 — the job: %s, as %s, every %s seconds. Its command:"
        % (plist_path(conf), conf["ROLE_ACCOUNT"], conf["INTERVAL_SECONDS"]))
    say("    " + job_command(conf))
    say("")
    say("THE ORDER")
    say("  1. Card CK-B1: create the app, invite it, then `token`.")
    say("  2. `install`, then `install --apply`.")
    say("  3. Card CK-B2: load it. Then `verify`.")
    say("")
    if conf["ACT"] == "yes":
        say("ACT=yes: a verified yes to `plan <ticket id>` moves that idea into %s, as the "
            "owner." % conf["PLAN_IT_STATE"])
        if conf["AGENT_USER_NAME"]:
            say("  A verified yes to `approve <epic id>` moves the epic to %s and hands its "
                "children to %s, as the owner, in dependency order, at most %s at a time. "
                "Merging stays yours." % (conf["READY_STATE"], conf["AGENT_USER_NAME"],
                                          conf["EPIC_WIP"]))
            say("  Once every piece of it is merged, it moves the epic to %s, posts a "
                "wrap-up, and asks before it files the review notes left as one ticket in "
                "the backlog (KIT-299)." % conf["DONE_STATE"])
        else:
            say("  AGENT_USER_NAME is empty, so every `approve` is refused.")
    else:
        say("ACT=no: a verified yes is recorded and said, and nothing in the tracker changes. "
            "Set ACT=yes and run `install --apply` to let it act.")
    if split_list(conf["DM_USER_IDS"]):
        say("DM_USER_IDS: it also reads and answers in the DM of %s, always as messages of "
            "their own. An app made before needs the manifest again: card CK-B1."
            % ", ".join(split_list(conf["DM_USER_IDS"])))
    return EX_OK


def cmd_token(conf, runner, sudo, reader=None, tty=None):
    found = agent_markers_present()
    if found:
        return refused(found)
    if not (tty if tty is not None else sys.stdin.isatty()):
        say("BLOCKED: `token` asks at a hidden prompt, and this is not a terminal.")
        return EX_BLOCKED
    try:
        sudo.acquire("`token` writes the bridge app's token into %s as %s."
                     % (conf["ENV_FILE"], conf["ROLE_ACCOUNT"]), "token")
    except se.NoPrivilege as exc:
        return no_privilege(exc, "token")
    value = (reader or getpass.getpass)("The bridge app's Bot User OAuth Token (hidden): ")
    value = (value or "").strip()
    if not TOKEN_RE.match(value):
        say("REFUSED: that is not a Slack bot token (it should start xoxb-). Nothing was "
            "written.")
        return EX_USAGE
    runner.dry_run = False
    res = runner.as_role(conf["ROLE_ACCOUNT"],
                         py(TOKEN_WRITER_PY, conf["ENV_FILE"], conf["SLACK_TOKEN_ENV"],
                            conf["DISPATCHER_ENV_FILE"]),
                         stdin=value + "\n", why="write %s into %s"
                         % (conf["SLACK_TOKEN_ENV"], conf["ENV_FILE"]), secret_stdin=True)
    for line in (res.out or "").splitlines():
        say("  " + line)
    if res.rc == 3:
        return EX_REFUSED
    if not res.ok:
        # The writer's error is on stderr only. Its stdin carried the token, so a line
        # shaped like a credential, as the token is, is never shown (KIT-262).
        last = ((res.err or "").strip().splitlines() or ["it printed no error"])[-1]
        if bridge.notify.secret_hits(last):
            last = "its last line is shaped like a credential, so it is not shown"
        say("FAILED: the token could not be written into %s, as %s (%s)."
            % (conf["ENV_FILE"], conf["ROLE_ACCOUNT"], last[:200]))
        return EX_FAILED
    return EX_OK


def cmd_install(conf, runner, sudo, apply_it):
    found = agent_markers_present()
    if found:
        return refused(found)
    resume = "install --apply" if apply_it else "install"
    try:
        sudo.acquire("`install` reads the role account's home and files, and %s the job's "
                     "config and plist." % ("writes" if apply_it else "compares"), resume)
    except se.NoPrivilege as exc:
        return no_privilege(exc, resume)
    home = home_of(runner, conf["ROLE_ACCOUNT"])
    if not home:
        say("UNKNOWN: the home of %s could not be read." % conf["ROLE_ACCOUNT"])
        return EX_UNKNOWN
    facts = read_facts(runner, conf) or {}
    body, plist = config_bytes(conf), render_plist(conf, home)
    plist_now = runner.read(["/bin/cat", plist_path(conf)])
    todo = []
    if facts.get("config_sha256") != sha(body):
        todo.append("config")
    if not (plist_now.ok and plist_now.out == plist):
        todo.append("plist")
    if not todo:
        say("Nothing to change: the config and the plist are the composed ones.")
        print_card("CK-B2", conf)
        return EX_OK
    say("To write: %s." % ", ".join(todo))
    if not apply_it:
        say("DRY RUN: nothing was written. Run it again with --apply.")
        return EX_BLOCKED
    runner.dry_run = False
    if "config" in todo:
        res = runner.as_role(conf["ROLE_ACCOUNT"], py(CONFIG_WRITER_PY, conf["BRIDGE_CONFIG"]),
                             stdin=body, why="write the bridge config")
        if not res.ok:
            say("FAILED: the config could not be written (%s)." % (res.err or "").strip()[:200])
            return EX_FAILED
        say("  " + res.out.strip())
    if "plist" in todo:
        for argv, stdin in ((["/usr/bin/tee", plist_path(conf)], plist),
                            (["/usr/sbin/chown", "root:wheel", plist_path(conf)], None),
                            (["/bin/chmod", "644", plist_path(conf)], None)):
            res = runner.as_root(argv, why="install the bridge plist", stdin=stdin)
            if not res.ok:
                say("FAILED: %s" % (res.err or "").strip()[:200])
                return EX_FAILED
        say("  wrote %s (installed turned off, not loaded)" % plist_path(conf))
    say("")
    print_card("CK-B2", conf)
    return EX_OK


def cmd_verify(conf, runner, sudo, now=None):
    import time
    found = agent_markers_present()
    if found:
        return refused(found)
    try:
        sudo.acquire("`verify` reads the role account's files and runs the bridge's own dry "
                     "run as that account. It changes nothing.", "verify")
    except se.NoPrivilege as exc:
        return no_privilege(exc, "verify")
    runner.dry_run = True
    rows = []
    home = home_of(runner, conf["ROLE_ACCOUNT"])
    facts = read_facts(runner, conf)
    if not home or facts is None:
        rows.append(("role-account", se.UNKNOWN, "the role account's files could not be read"))
    else:
        # A file there that could not be read is UNKNOWN, with why: never a state verify
        # did not see (KIT-262).
        if facts.get("config_error"):
            rows.append(("config", se.UNKNOWN, "%s could not be read (%s)"
                         % (conf["BRIDGE_CONFIG"], facts["config_error"])))
        else:
            rows.append(("config", se.ALREADY_DONE if facts.get("config_sha256") ==
                         sha(config_bytes(conf)) else se.BLOCKED,
                         "the job's config is the composed one" if facts.get("config_sha256")
                         == sha(config_bytes(conf)) else "run `install --apply`"))
        names = facts.get("env_names") or []
        missing = [n for n in (conf["SLACK_TOKEN_ENV"], conf["LINEAR_KEY_ENV"]) if n not in names]
        rows.append(("credentials", se.UNKNOWN if facts.get("env_error") else
                     (se.BLOCKED if missing else se.ALREADY_DONE),
                     "the env file could not be read (%s)" % facts.get("env_error")
                     if facts.get("env_error") else ("missing by name: %s; run `token`"
                                                     % ", ".join(missing) if missing else
                                                     "both credentials are named")))
        plist_now = runner.read(["/bin/cat", plist_path(conf)])
        rows.append(("plist", se.ALREADY_DONE if plist_now.ok and plist_now.out ==
                     render_plist(conf, home) else se.BLOCKED,
                     "the plist is the composed one" if plist_now.ok and plist_now.out ==
                     render_plist(conf, home) else "run `install --apply`"))
        dry = runner.as_role(conf["ROLE_ACCOUNT"], job_command(conf) + " --dry-run")
        # A config problem is said on stderr only, and so is the shell's own error about
        # the env file. The shell can echo a line of that file, so a line shaped like a
        # credential is never shown.
        said = ((dry.out or "").strip() or (dry.err or "").strip()).splitlines() or [""]
        last = said[-1]
        if bridge.notify.secret_hits(last):
            last = "its last line is shaped like a credential, so it is not shown: read %s" % (
                LOG_NAME)
        last = last[:200]
        rows.append(("dry-run", se.ALREADY_DONE if dry.rc == 0 else se.FAILED,
                     last or "the job printed nothing"))
        loaded = runner.as_root(["/bin/launchctl", "print", "system/%s" % conf["JOB_LABEL"]])
        rows.append(loaded_row(conf, loaded))
        beat = facts.get("heartbeat") or {}
        age = None
        try:
            import calendar
            age = (now or time.time()) - calendar.timegm(time.strptime(beat.get("at") or "",
                                                                       "%Y-%m-%dT%H:%M:%SZ"))
        except (TypeError, ValueError):
            pass
        fresh = (age is not None and age <= 3 * int(conf["INTERVAL_SECONDS"]) + 60
                 and beat.get("exit") == 0 and not beat.get("dry"))
        beat_path = conf["STATE_DIR"].rstrip("/") + "/bridge-heartbeat.json"
        # A heartbeat there but unread, unparsed or with no time it was written.
        if age is None and not facts.get("heartbeat_missing"):
            rows.append(("heartbeat", se.UNKNOWN, "%s could not be read (%s): the job's log, "
                         "%s, says what its last pass did"
                         % (beat_path, facts.get("heartbeat_error")
                            or "it has no readable `at` time", LOG_NAME)))
        else:
            rows.append(("heartbeat", se.ALREADY_DONE if fresh else
                         (se.BLOCKED if age is None else se.FAILED),
                         ("its last pass, %ds ago, exited 0" % age) if fresh else
                         ("no heartbeat yet" if age is None else
                          "its last pass was %ss ago with exit %s: %s"
                          % (int(age), beat.get("exit"), (beat.get("summary") or "")[:160]))))
        rows.append(pings_row(conf, facts))
        rows.append(notifier_row(conf, facts, runner, now or time.time()))
    for check, outcome, detail in rows:
        say("  %-13s %-17s %s" % (check, outcome, detail))
    worst = [o for _c, o, _d in rows]
    for outcome, code in ((se.FAILED, EX_FAILED), (se.UNKNOWN, EX_UNKNOWN),
                          (se.BLOCKED, EX_BLOCKED)):
        if outcome in worst:
            return code
    say("Good: the bridge is installed, loaded and passing.")
    return EX_OK


def loaded_row(conf, loaded):
    """launchd holds the job, at INTERVAL_SECONDS (KIT-285 review). `install --apply`
    rewrites the plist and never reloads the job, and launchd keeps the interval it was
    loaded with. The notifier watch reads a gap in the job's passes against the interval in
    its config, so with the watch on, a job launchd runs less often reads every pass as a
    sleep and never says a dead notifier: BLOCKED. With the watch off nothing counts on it,
    so the row keeps the verdict it had before the watch, and still says it. With the watch
    on, a print that names no interval is UNKNOWN."""
    if not loaded.ok:
        return ("loaded", se.BLOCKED, "not loaded: card CK-B2")
    found = se._RUN_INTERVAL_RE.search(loaded.out or "")
    want = int(conf["INTERVAL_SECONDS"])
    if found and int(found.group(1)) != want and conf["NOTIFIER_JOB_LABEL"]:
        return ("loaded", se.BLOCKED, "launchd still runs the bridge every %s s, not every %d "
                "s (INTERVAL_SECONDS). Reload it: card CK-B3, then card CK-B2"
                % (found.group(1), want))
    if found and int(found.group(1)) != want:
        return ("loaded", se.ALREADY_DONE, "launchd holds the job, but runs it every %s s, "
                "not every %d s (INTERVAL_SECONDS). To change that, reload it: card CK-B3, "
                "then card CK-B2" % (found.group(1), want))
    if not found and conf["NOTIFIER_JOB_LABEL"]:
        return ("loaded", se.UNKNOWN, "launchd holds the job, and its print names no run "
                "interval, so whether it runs every %d s, as the notifier watch counts on, "
                "could not be measured. Read it yourself: sudo launchctl print system/%s"
                % (want, conf["JOB_LABEL"]))
    return ("loaded", se.ALREADY_DONE, "launchd holds the job" + (
        ", every %d s" % want if found else ""))


def notifier_row(conf, facts, runner, now):
    """KIT-285: the watch the job keeps on the notifier, judged by the job's own rule. Off is
    said as off. A file that could not be read is UNKNOWN, never a healthy notifier (§13).
    The plist is root:wheel 644, so it is read as any account reads it; the heartbeat, in
    the facts, as the role account."""
    if not conf["NOTIFIER_JOB_LABEL"]:
        return ("notifier", se.SKIPPED, "off: NOTIFIER_JOB_LABEL is empty, so the bridge "
                "watches no notifier")
    path = notifier_plist_path(conf)
    res = runner.read(["/bin/cat", path])
    if res.ok:
        interval, why = bridge.notifier_interval((res.out or "").encode("utf-8"))
    else:
        interval, why = None, "its plist %s could not be read (%s)" % (
            path, (res.err or "").strip()[:80] or "exit %d" % res.rc)
    if interval:
        # The job judges the notifier by the plist on disk, and launchd keeps the interval
        # it was loaded with. A notifier launchd runs less often than its plist says would
        # read as quiet, then back, over and over (KIT-285 review).
        held = runner.as_root(["/bin/launchctl", "print",
                               "system/%s" % conf["NOTIFIER_JOB_LABEL"]])
        found = se._RUN_INTERVAL_RE.search(held.out or "") if held.ok else None
        if found and int(found.group(1)) != interval:
            return ("notifier", se.BLOCKED, "launchd still runs the notifier every %s s, and "
                    "its plist says %d s. The bridge judges it by the plist, so until the "
                    "notifier is reloaded it may say quiet, then back, again and again. "
                    "Reload it: the notifier installer's card CK-N3"
                    % (found.group(1), interval))
    if facts.get("notifier_beat_error"):
        why = why or "its heartbeat %s could not be read (%s)" % (
            notifier_heartbeat(conf), facts["notifier_beat_error"])
    verdict, at, detail = bridge.judge_notifier(interval, why, facts.get("notifier_beat"), now)
    if verdict == "ok":
        # The job's run limit is not in bridge.conf, so the composed config has its default.
        slowest = bridge.slowest_run_interval(interval, bridge.DEFAULT_RUN_TIMEOUT)
        if slowest < INTERVAL_MIN:
            # No INTERVAL_SECONDS the conf check takes is fast enough: the notifier's own
            # interval is what must change (KIT-285 review).
            need = interval
            while bridge.slowest_run_interval(need, bridge.DEFAULT_RUN_TIMEOUT) < INTERVAL_MIN:
                need += 1
            return ("notifier", se.BLOCKED, "watched, but the notifier runs every %d s, too "
                    "often for the bridge to tell the Mac's sleep from a quiet notifier: the "
                    "bridge runs once every %d s at the fastest. Set INTERVAL_SECONDS in "
                    "notifier.conf to %d or more, then run `python3 "
                    "scripts/pipeline_notifier_setup.py run`" % (interval, INTERVAL_MIN, need))
        if int(conf["INTERVAL_SECONDS"]) > slowest:
            return ("notifier", se.BLOCKED, "watched, but the bridge runs every %s s, too "
                    "seldom to tell the Mac's sleep from a quiet notifier that runs every %d "
                    "s. Set INTERVAL_SECONDS to %d or less, run `install --apply`, then "
                    "reload the job: card CK-B3, then card CK-B2"
                    % (conf["INTERVAL_SECONDS"], interval, slowest))
        return ("notifier", se.ALREADY_DONE, "watched: %s; quiet after %d s (%d x its %d s "
                "interval + %d s)" % (detail, bridge.quiet_after(interval),
                                      bridge.NOTIFIER_QUIET_INTERVALS, interval,
                                      bridge.NOTIFIER_QUIET_SLACK))
    if verdict == "quiet" and at is None:
        return ("notifier", se.BLOCKED, "watched, and there is no heartbeat at %s. Is the "
                "notifier loaded (its installer's card CK-N3), and is NOTIFIER_STATE_DIR its "
                "STATE_DIR?" % notifier_heartbeat(conf))
    if verdict in ("quiet", "failing"):
        return ("notifier", se.FAILED, "%s: %s. Check it: python3 "
                "scripts/pipeline_notifier_setup.py verify" % (verdict, detail))
    return ("notifier", se.UNKNOWN, "%s: %s" % (verdict, detail))


def pings_row(conf, facts):
    """The notifier writes its pings file on its first pass with BRIDGE_PINGS=yes, so a
    missing file means that is off, or the two jobs name different state dirs. With
    NOTIFIER_BOT_USER_ID set the owner has turned answering on, so a missing file blocks."""
    path = pings_file(conf)
    if facts.get("pings_error"):
        return ("pings", se.UNKNOWN, "%s could not be read (%s)" % (path, facts["pings_error"]))
    if facts.get("pings_missing"):
        return ("pings", se.BLOCKED if conf["NOTIFIER_BOT_USER_ID"] else se.SKIPPED,
                "no pings file at %s: BRIDGE_PINGS=yes is not set in notifier.conf, or its "
                "STATE_DIR is not NOTIFIER_STATE_DIR. Until it is there, no reply under a "
                "ping is answered (docs/BRIDGE-OPERATOR.md, Step 10)" % path)
    if facts.get("pings_schema") != bridge.notify.BRIDGE_PINGS_SCHEMA:
        return ("pings", se.FAILED, "%s is not a %s document"
                % (path, bridge.notify.BRIDGE_PINGS_SCHEMA))
    return ("pings", se.ALREADY_DONE, "the notifier records its pings at %s" % path)


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest
# ══════════════════════════════════════════════════════════════════════════════════════

GOOD_CONF = """
ROLE_ACCOUNT=_exdispatch
ROLE_KIT_CLONE=~/.stage-e/kit
CHANNEL_IDS=C0000000AA, C0000000BB
OWNER_USER_ID=00000000-0000-4000-8000-000000000001
TEAM_KEYS=PROD
JOB_LABEL=com.example.bridge
BOT_USER_ID=U0BRIDGE01
DISPATCHER_ENV_FILE=~/dispatcher.env
"""


class _FakeSudo(object):
    def __init__(self, ok=True):
        self.ok, self.acquisitions = ok, 0

    def acquire(self, why, resume):
        self.acquisitions += 1
        if not self.ok:
            raise se.NoPrivilege("declined")
        return True


class _RoleRunner(se.Runner):
    """Runs the role-account programs for real, through /bin/sh, with HOME a temp home;
    root commands write into a temp tree; launchctl answers from `loaded`, or for a label in
    `prints` with that text, as launchd prints a job it holds."""

    def __init__(self, home, root, loaded=False, job_rc=0,
                 job_out="bridge: 0 message(s) examined (dry run)\n", job_err="", prints=None):
        se.Runner.__init__(self)
        self.home, self.root, self.loaded, self.job_rc = home, root, loaded, job_rc
        self.job_out, self.job_err = job_out, job_err
        self.prints = prints or {}
        self.stdins = []

    def _exec(self, argv, stdin, timeout, cwd=None):
        import subprocess
        self.stdins.append(stdin)
        if argv[:2] == ["sudo", "-u"]:
            script = argv[-1]
            if "pipeline_bridge.py" in script and "--dry-run" in script:
                return se.Result(self.job_rc, self.job_out, self.job_err)
            p = subprocess.run(["/bin/sh", "-c", script], input=stdin, capture_output=True,
                               text=True, env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
            return se.Result(p.returncode, p.stdout, p.stderr)
        if argv[:1] == ["sudo"]:
            argv = argv[1:]
        if argv[:2] == ["/bin/launchctl", "print"]:
            label = argv[2].split("/", 1)[-1]
            if label in self.prints:
                return se.Result(0, self.prints[label], "")
            return se.Result(0 if self.loaded else 113, "", "")
        if argv[:1] == ["/usr/bin/tee"]:
            path = self.root + argv[1]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(stdin)
            return se.Result(0, stdin, "")
        if argv[:1] in (["/usr/sbin/chown"], ["/bin/chmod"]):
            return se.Result(0, "", "")
        if argv[:1] == ["/bin/cat"]:
            path = self.root + argv[1]
            if not os.path.exists(path):
                return se.Result(1, "", "No such file")
            return se.Result(0, open(path).read(), "")
        return se.Result(127, "", "unexpected: %r" % (argv,))


def selftest():
    import io
    import contextlib
    import tempfile
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    def cap(fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn(*a, **kw)
        return rc, buf.getvalue()

    saved = dict(os.environ)
    for m in AGENT_ENV_MARKERS:
        os.environ.pop(m, None)
    try:
        values, perrs = parse_conf(GOOD_CONF)
        conf, errs = validate_conf(values)
        check("good-conf", (perrs, errs), ([], []))
        check("config-passes-the-jobs-own-loader",
              bridge.validate_config(dict(compose_config(conf), state_dir="/tmp/x-%d"
                                          % os.getpid()))["channel_ids"],
              ["C0000000AA", "C0000000BB"])
        check("config-acts-on-nothing-by-default", compose_config(conf)["act"], False)
        on, _e = validate_conf(dict(values, ACT="yes"))
        check("config-acts-with-ACT-yes", (compose_config(on)["act"],
                                           compose_config(on)["plan_it_state"]),
              (True, "Plan it"))
        approving, _e = validate_conf(dict(values, AGENT_USER_NAME="Agent", EPIC_WIP="2",
                                           READY_STATE="Ready"))
        check("config-carries-the-approve-settings",
              [compose_config(approving)[k] for k in ("agent_user_name", "epic_wip",
                                                      "ready_state")], ["Agent", 2, "Ready"])
        # KIT-224 review: the pings file is the NOTIFIER's, in its STATE_DIR, never left to
        # the bridge's own state dir; the notifier's bot user is composed only when set.
        pinged_conf, _e = validate_conf(dict(values, STATE_DIR="~/.bridge-state",
                                             NOTIFIER_STATE_DIR="/srv/notifier/state",
                                             NOTIFIER_BOT_USER_ID="U0NOTIFY01"))
        check("config-pings-file-from-NOTIFIER_STATE_DIR",
              (compose_config(conf).get("pings_file"),
               compose_config(pinged_conf).get("pings_file"),
               compose_config(pinged_conf).get("notifier_bot_user_id"),
               "notifier_bot_user_id" in compose_config(conf)),
              ("~/.stage-e/state/notifier-bridge-pings.json",
               "/srv/notifier/state/notifier-bridge-pings.json", "U0NOTIFY01", False))
        check("conf-NOTIFIER_BOT_USER_ID-must-be-a-user-id-not-the-bridges",
              [any("notifier_bot_user_id" in e for e in validate_conf(dict(
                  values, NOTIFIER_BOT_USER_ID=v))[1]) for v in ("notifier", "U0BRIDGE01")],
              [True, True])
        check("config-approve-defaults",
              [compose_config(conf)[k] for k in ("agent_user_name", "epic_wip",
                                                 "ready_state")], ["", 1, "Todo"])
        for bad in ("0", "11", "two"):
            check("conf-EPIC_WIP-refused:" + bad,
                  any("EPIC_WIP must be" in e
                      for e in validate_conf(dict(values, EPIC_WIP=bad))[1]), True)
        named, _e = validate_conf(dict(values, PLAN_IT_STATE="Ready to plan"))
        check("config-carries-the-plan-it-state-name",
              compose_config(named)["plan_it_state"], "Ready to plan")
        # KIT-284: the hand-off lanes. The default is the job's own and composes no key, so
        # an install made before the key existed is still the composed one. Names compose in
        # order, and the job's own check refuses a state a child sits in before work starts.
        check("conf-HANDOFF_STATES-defaults-to-the-jobs-own-and-composes-no-key",
              (CONF_KEYS.get("HANDOFF_STATES", ("not a key",))[0],
               getattr(bridge, "DEFAULT_HANDOFF_STATES", None),
               "handoff_states" in compose_config(conf)),
              ("Needs Approval", ["Needs Approval"], False))
        lanes, lane_errs = validate_conf(dict(values,
                                              HANDOFF_STATES="Ready to merge, Needs Approval"))
        try:
            loaded = bridge.validate_config(dict(compose_config(lanes),
                                                 state_dir="/nonexistent-%s"
                                                 % os.getpid()))["handoff_states"]
        except (bridge.BridgeError, KeyError) as exc:
            loaded = "refused: %s" % exc
        check("conf-HANDOFF_STATES-composes-each-name-in-order",
              (lane_errs, compose_config(lanes).get("handoff_states"), loaded),
              ([], ["Ready to merge", "Needs Approval"], ["Ready to merge", "Needs Approval"]))
        check("conf-HANDOFF_STATES-naming-READY_STATE-is-refused-in-one-line",
              len([e for e in validate_conf(dict(values, HANDOFF_STATES="Todo"))[1]
                   if "handoff_states" in e and "ready_state" in e]), 1)
        # KIT-299: the done state a finished plan moves to. The default is the job's own and
        # composes no key, so an install made before the key existed is still the composed
        # one; another name composes, and the job's own loader takes it.
        shipped, ship_errs = validate_conf(dict(values, DONE_STATE="Shipped"))
        try:
            ship_loaded = bridge.validate_config(dict(compose_config(shipped),
                                                      state_dir="/nonexistent-%s"
                                                      % os.getpid()))["done_state"]
        except (bridge.BridgeError, KeyError) as exc:
            ship_loaded = "refused: %s" % exc
        check("conf-DONE_STATE-defaults-to-the-jobs-own-and-composes-no-key",
              (CONF_KEYS.get("DONE_STATE", ("not a key",))[0],
               getattr(bridge, "DEFAULT_DONE_STATE", None), "done_state" in compose_config(conf),
               config_bytes(conf) == config_bytes(dict(conf, DONE_STATE="Done"))),
              ("Done", "Done", False, True))
        check("conf-DONE_STATE-composes-another-name",
              (ship_errs, compose_config(shipped).get("done_state"), ship_loaded),
              ([], "Shipped", "Shipped"))
        check("live-proofs-card-says-how-a-plan-finishes",
              [n for n in ("Finish a plan", "moves to Shipped", "no labels", "not proven")
               if n not in cap(print_card, "CK-B4", shipped)[1]], [])
        check("conf-ACT-must-be-yes-or-no",
              any("ACT must be yes or no" in e for e in validate_conf(dict(values,
                                                                          ACT="true"))[1]),
              True)
        example = os.path.join(os.path.dirname(HERE), "bridge.conf.example")
        ex_conf, ex_errs = load_conf(example)
        check("example-conf-is-valid", (ex_errs, sorted(k for k in CONF_KEYS
                                                       if k not in open(example).read())),
              ([], []))
        _c, errs = validate_conf(dict(values, SLACK_TOKEN_ENV="SLACK_BOT_TOKEN",
                                      JOB_LABEL="nope", INTERVAL_SECONDS="5",
                                      CHANNEL_IDS="general"))
        joined = " | ".join(errs)
        # Every bad value in ONE pass (bridge.conf.example's promise): the job's own config
        # check runs even when a conf-level check has already failed (KIT-262).
        for needle in ("JOB_LABEL", "INTERVAL_SECONDS", "config 'channel_ids'", "chat bot"):
            check("conf-names:" + needle, needle in joined, True)
        # ...and a value both checks refuse is named once, by its conf key.
        _c, errs = validate_conf(dict(values, CHANNEL_IDS="", EPIC_WIP="two"))
        check("conf-one-problem-one-line",
              [len([e for e in errs if word in e.lower()]) for word in ("channel", "epic_wip")],
              [1, 1])
        # A NOTIFIER_STATE_DIR refused here is not named again as the job's pings file,
        # whatever the job's own check makes of it (here: inside a git working tree).
        with tempfile.TemporaryDirectory() as gtmp:
            os.makedirs(os.path.join(gtmp, ".git"))
            _c, errs = validate_conf(dict(values, NOTIFIER_STATE_DIR=gtmp + "/state dir"))
            check("conf-a-refused-notifier-state-dir-named-once",
                  ([e.split(" ")[0] for e in errs],
                   [e for e in validate_conf(dict(values, NOTIFIER_STATE_DIR=gtmp + "/s"))[1]
                    if "'pings_file'" in e] != []), (["NOTIFIER_STATE_DIR"], True))
        _c, errs = validate_conf(dict(values, SLACK_TOKEN_ENV="SLACK_BOT_TOKEN"))
        check("conf-refuses-the-chat-bots-token-name", any("chat bot" in e for e in errs), True)
        # Each path lands inside the job's shell command, and the account in `sudo -u`: a
        # value with anything after its plain characters is refused, by key.
        inject = '~/kit"; /usr/bin/touch /tmp/pwned; "'
        check("conf-path-with-shell-after-it-refused",
              [k for k in ("ROLE_KIT_CLONE", "ENV_FILE", "BRIDGE_CONFIG", "STATE_DIR",
                           "DISPATCHER_ENV_FILE", "NOTIFIER_STATE_DIR")
               if not any(e.startswith(k + " must be") for e in
                          validate_conf(dict(values, **{k: inject}))[1])], [])
        _c, errs = validate_conf(dict(values, ROLE_ACCOUNT="_exdispatch;id"))
        check("conf-account-with-junk-after-it-refused",
              any(e.startswith("ROLE_ACCOUNT") for e in errs), True)
        # The bridge's token must never go in the dispatcher's env file.
        _c, errs = validate_conf(dict(values, DISPATCHER_ENV_FILE="~/.stage-e/./env"))
        check("conf-env-file-is-the-dispatchers-refused",
              any("ENV_FILE and DISPATCHER_ENV_FILE" in e for e in errs), True)
        # KIT-280: the job runs from Stage E's clone, the one clone anything moves.
        _c, errs = validate_conf(dict(values, ROLE_KIT_CLONE="~/kit"))
        _c, errs_slash = validate_conf(dict(values, ROLE_KIT_CLONE="~/.stage-e/kit/"))
        _c, errs_bad = validate_conf(dict(values, ROLE_KIT_CLONE="relative/kit"))
        check("conf-refuses-a-clone-nothing-moves",
              ([e for e in errs if "ROLE_KIT_CLONE" in e and "not Stage E's clone" in e
                and "never reach this job" in e and "Step 1" in e] != [], errs_slash,
               len([e for e in errs_bad if "ROLE_KIT_CLONE" in e])),
              (True, [], 1))
        _v, perrs = parse_conf("NOPE=1\nbroken line\nTEAM_KEYS=A\nTEAM_KEYS=B\n"
                               "OWNER_USER_ID=xoxb-%s\n" % ("z" * 30))
        check("conf-parse-errors", len(perrs), 4)
        check("conf-duplicate-names-both-lines", any("lines 3 and 4" in e for e in perrs), True)
        check("conf-credential-value-refused-by-key-only",
              any("OWNER_USER_ID holds a value shaped like a credential" in e for e in perrs)
              and not any("z" * 30 in e for e in perrs), True)
        m = manifest(conf)
        check("manifest-scopes-exact", m["oauth_config"]["scopes"]["bot"],
              ["chat:write", "groups:history", "im:history", "im:write", "users:read"])
        check("manifest-no-events-no-socket", ("event_subscriptions" in m["settings"],
                                               m["settings"]["socket_mode_enabled"]),
              (False, False))
        # KIT-295: a member's DM with the app. Reading it needs im:history, opening it
        # im:write, and a member can write to the app only with its Messages tab on. Each
        # scope is cited to the code that calls it.
        check("manifest-scopes-read-and-open-a-dm",
              [sc for sc in ("im:history", "im:write")
               if sc not in m["oauth_config"]["scopes"]["bot"]], [])
        check("manifest-scopes-cite-the-dm-code",
              ("SlackClient.open_dm" in SCOPE_CITES.get("im:write", ""),
               "Pass.open_dms" in SCOPE_CITES.get("im:history", ""),
               hasattr(bridge.SlackClient, "open_dm"), hasattr(bridge.Pass, "open_dms")),
              (True, True, True, True))
        check("manifest-messages-tab-on", m["features"].get("app_home"),
              {"home_tab_enabled": False, "messages_tab_enabled": True,
               "messages_tab_read_only_enabled": False})
        dm_conf, dm_errs = validate_conf(dict(values, DM_USER_IDS="U0ALICE001, U0BOB00001",
                                              NOTIFIER_STATE_DIR="/srv/notifier/state"))
        composed = compose_config(dm_conf)
        try:
            loaded = bridge.validate_config(dict(composed, state_dir="/nonexistent-%s"
                                                 % os.getpid()))["dm_user_ids"]
        except (bridge.BridgeError, KeyError) as exc:
            loaded = "refused: %s" % exc
        check("conf-DM_USER_IDS-composed-only-when-set",
              (dm_errs, composed.get("dm_user_ids"), composed.get("notifier_posts_file"),
               loaded, [k for k in ("dm_user_ids", "notifier_posts_file")
                        if k in compose_config(conf)]),
              ([], ["U0ALICE001", "U0BOB00001"],
               "/srv/notifier/state/notifier-dm-posts.json", ["U0ALICE001", "U0BOB00001"],
               []))
        for name, value in (("a-name", "alice"), ("a-channel", "C0000000AA"),
                            ("a-dm-channel", "D0ALICE001")):
            _c, errs = validate_conf(dict(values, DM_USER_IDS="U0ALICE001, " + value))
            check("conf-DM_USER_IDS-bad-id-refused:" + name,
                  [e.split(" ")[0] for e in errs if "DM_USER_IDS" in e or "dm_user_ids" in e],
                  ["DM_USER_IDS"])
        _c, errs = validate_conf(dict(values, DM_USER_IDS="U0ALICE001, U0ALICE001"))
        check("conf-DM_USER_IDS-duplicate-refused",
              [e for e in errs if "DM_USER_IDS" in e or "dm_user_ids" in e],
              ["DM_USER_IDS names a member twice"])
        b1 = cap(print_card, "CK-B1", conf)[1]
        check("card-b1-says-how-to-update-the-app-and-open-the-dm",
              [n for n in ("App Manifest", "Save Changes", "Reinstall to Workspace",
                           "Bot User OAuth Token changed", "Messages tab")
               if n not in b1], [])
        cmd = job_command(conf)
        check("job-sources-the-env-file-then-runs-the-bridge",
              cmd.startswith('set -a; . "$HOME/.stage-e/env"; set +a; exec /usr/bin/python3 '
                             '"$HOME/.stage-e/kit/scripts/pipeline_bridge.py" run --config '
                             '"$HOME/.stage-e/bridge.json"'), True)
        plist = render_plist(conf, "/srv/role-home")
        check("plist-runs-as-the-role-account",
              ("<string>_exdispatch</string>" in plist, "com.example.bridge" in plist,
               "<integer>60</integer>" in plist, "/srv/role-home/.stage-e/bridge.log"
               in plist), (True, True, True, True))
        # Installed disabled: launchd loads every plist in /Library/LaunchDaemons at boot.
        import plistlib
        parsed = plistlib.loads(plist.encode("utf-8"))
        check("plist-installed-disabled", (parsed.get("Disabled"), parsed.get("RunAtLoad")),
              (True, True))
        check("cards-all-print", all(cap(print_card, c, conf)[1].startswith(c)
                                     for c in CARDS), True)
        # Enable, then load: a job turned off with CK-B3 (or installed disabled) will not
        # load until it is enabled.
        b2 = cap(print_card, "CK-B2", conf)[1]
        check("card-b2-enables-before-it-loads",
              0 <= b2.find("launchctl enable system/") < b2.find("launchctl bootstrap system"),
              True)
        # KIT-227: the live proofs, one card the owner runs, filled from the conf.
        _ck4 = cap(print_card, "CK-B4", conf)[1]
        for needle in ("I want", "history shows your move", "guest", "asleep",
                       "approve", "one at a time", "stopped to ask you something",
                       "yours to merge",
                       "env.decoy-proof", "sudo -u _exdispatch -H sh -c", "Record"):
            check("live-proofs-card-says:" + needle, needle in _ck4, True)
        # KIT-262 (review of KIT-227): the ready-to-merge step names the trigger the code
        # has (Stage E's conclusion, clean or below the threshold, then the move to Needs
        # Approval), and that a move by hand pages nothing.
        _ready = [ln for ln in _ck4.splitlines() if "Ready to merge" in ln]
        check("live-proofs-card-ready-to-merge-says-its-trigger",
              [n for n in ("below the threshold", "Needs Approval", "by hand pages nothing")
               if not _ready or n not in _ready[0]], [])
        check("live-proofs-card-never-reads-the-real-env-file",
              ("cat ~/.stage-e/env " not in _ck4 and "read ~/.stage-e/env " not in _ck4
               and "sh -c 'rm ~/.stage-e/env.decoy-proof'" in _ck4), True)
        # KIT-227 review: the decoy is filled from ENV_FILE and is said to prove the rule
        # for the env file's sibling copies only. The env file's own rule is checked
        # without reading it, by the chat lane's verify row that measures it. The tech lead
        # is asked to read decoys only: this one first, then the backups' and the state
        # dir's, each from this env's own paths (KIT-262 review: the whole list, for every
        # spelling here, not only the first read).
        import pipeline_chat_lane_setup as chat_lane
        for env in ("~/.stage-e/env", "/srv/role/bridge.env"):
            vconf, verrs = validate_conf(dict(values, ENV_FILE=env, DISPATCHER_ENV_FILE=""))
            card = cap(print_card, "CK-B4", vconf)[1]
            decoy = env + ".decoy-proof"
            decoys = [decoy, os.path.dirname(env) + "/backups/decoy-proof",
                      vconf["STATE_DIR"] + "/decoy-proof"]
            own, copies = chat_lane._path_rule(env), chat_lane._path_rule(env + ".*")
            asked = [a.rstrip(".,") for a in re.findall(r"ask the tech lead to read (\S+)",
                                                         card)]
            check("live-proofs-card-decoy-from-ENV_FILE:" + env,
                  (verrs, asked, "echo decoy > %s'" % decoy in card,
                   "sh -c 'rm %s'" % decoy in card), ([], decoys, True, True))
            check("live-proofs-card-says-what-the-decoy-proves:" + env,
                  ("proves %s" % copies in card, "does not test %s" % own in card,
                   "same rule" in card), (True, True, False))
            check("live-proofs-card-checks-the-env-files-own-rule-by-verify:" + env,
                  ("pipeline_chat_lane_setup.py verify" in card, "user-settings" in card,
                   "lists %s" % own in card), (True, True, True))
            check("live-proofs-card-rules-are-the-chat-lanes:" + env,
                  [r in chat_lane.user_deny_patterns({"ROLE_ENV_FILE": env})
                   for r in (own, copies)], [True, True])
            check("live-proofs-card-names-no-other-env-file:" + env,
                  env == "~/.stage-e/env" or "~/.stage-e/env" not in card, True)
        # KIT-262 (review of KIT-227): the read step covers EVERY bridge file a session must
        # not read, each filled from the conf: the env file, its copies beside it, the
        # backups `token` keeps of it, the config, the state dir and the notifier's pings
        # file. The tech lead is asked to read decoys only, each made and removed as the
        # role account. The config and records have no chat-lane rule, and the card says
        # so (not held, accepted) instead of passing them on trust.
        for over in ({}, {"ENV_FILE": "/srv/role/bridge.env",
                          "BRIDGE_CONFIG": "/srv/role/bridge.json",
                          "STATE_DIR": "/srv/role/state",
                          "NOTIFIER_STATE_DIR": "/srv/notifier/state"}):
            vconf, verrs = validate_conf(dict(values, DISPATCHER_ENV_FILE="", **over))
            card = cap(print_card, "CK-B4", vconf)[1]
            tag = vconf["ENV_FILE"]
            backups = os.path.dirname(vconf["ENV_FILE"]) + "/backups"
            log = os.path.dirname(vconf["BRIDGE_CONFIG"]) + "/bridge.log"
            decoys = [vconf["ENV_FILE"] + ".decoy-proof", backups + "/decoy-proof",
                      vconf["STATE_DIR"] + "/decoy-proof"]
            asked = [a.rstrip(".,") for a in re.findall(r"ask the tech lead to read (\S+)",
                                                         card)]
            check("live-proofs-card-names-every-bridge-file:" + tag,
                  (verrs, [p for p in (vconf["ENV_FILE"], backups, vconf["BRIDGE_CONFIG"],
                                       log, vconf["STATE_DIR"], pings_file(vconf))
                           if p not in card]), ([], []))
            check("live-proofs-card-asks-for-decoys-only:" + tag, asked, decoys)
            check("live-proofs-card-makes-and-removes-each-decoy:" + tag,
                  [d for d in decoys if "sudo -u _exdispatch -H sh -c 'echo decoy > %s'" % d
                   not in card or "sudo -u _exdispatch -H sh -c 'rm %s'" % d not in card], [])
            rules = chat_lane.user_deny_patterns({"ROLE_ENV_FILE": vconf["ENV_FILE"]})
            check("live-proofs-card-backups-rule-is-the-chat-lanes:" + tag,
                  (chat_lane.BACKUPS_DENY_RULE in rules,
                   "proves %s" % chat_lane.BACKUPS_DENY_RULE in card,
                   (backups == chat_lane.ROLE_BACKUPS) == ("not %s" % backups not in card)),
                  (True, True, True))
            # What the card expects, against the chat lane's own rules (a glob match, as
            # wide as theirs or wider): the env file's copies are covered, the backups only
            # where the rule names them, and no rule covers the config or the records. If
            # one ever does, this goes red and the card's "not held" is due a change.
            import fnmatch

            def covered(path):
                return [r for r in rules if r.startswith(("Read(~/", "Read(//")) and
                        fnmatch.fnmatchcase(path, r[5:-1][1:] if r.startswith("Read(//")
                                            else r[5:-1])]
            check("live-proofs-card-expects-what-the-rules-cover:" + tag,
                  (bool(covered(decoys[0])), bool(covered(decoys[1])),
                   [p for p in (vconf["BRIDGE_CONFIG"], log, decoys[2], pings_file(vconf))
                    if covered(p)]),
                  (True, backups == chat_lane.ROLE_BACKUPS, []))
            check("live-proofs-card-says-the-records-are-not-held:" + tag,
                  ("Expected: it is read" in card, "not held" in card, "KIT-162" in card),
                  (True, True, True))
        rc, out = cap(cmd_compose, conf, "bridge.conf")
        check("compose-prints-every-piece", (rc, all(p in out for p in (
            "PIECE 1", "PIECE 2", "PIECE 3", "ACT=no"))), (EX_OK, True))

        with tempfile.TemporaryDirectory() as tmp:
            home, root = os.path.join(tmp, "home"), os.path.join(tmp, "root")
            os.makedirs(os.path.join(home, ".stage-e"))
            envp = os.path.join(home, ".stage-e", "env")
            with open(envp, "w") as fh:
                fh.write("STAGE_E_LINEAR_API_KEY=lin_api_%s\nNOTIFIER_TOKEN=xoxb-%s\n"
                         % ("k" * 30, "n" * 30))
            os.chmod(envp, 0o600)

            # token: refused under a model; a non-token refused; the notifier's refused;
            # a good one written, its value never printed.
            os.environ[AGENT_ENV_MARKERS[0]] = ""
            r = _RoleRunner(home, root)
            check("token-refused-under-a-model",
                  cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "x", True)[0], EX_REFUSED)
            check("install-refused-under-a-model",
                  cap(cmd_install, conf, r, _FakeSudo(), True)[0], EX_REFUSED)
            check("verify-refused-under-a-model",
                  cap(cmd_verify, conf, r, _FakeSudo())[0], EX_REFUSED)
            check("refusals-ran-nothing", r.reads + r.writes, [])
            os.environ.pop(AGENT_ENV_MARKERS[0])
            check("token-needs-a-terminal",
                  cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "x", False)[0], EX_BLOCKED)
            check("token-not-a-token",
                  cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "hello", True)[0], EX_USAGE)
            # The env file is sourced by a shell: anything after the token would run.
            rc, out = cap(cmd_token, dict(conf, DISPATCHER_ENV_FILE=""), r, _FakeSudo(),
                          lambda _p: "xoxb-%s $(id)" % ("j" * 30), True)
            check("token-with-shell-after-it-refused", (rc, "j" * 30 in open(envp).read()),
                  (EX_USAGE, False))
            rc, out = cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "xoxb-" + "n" * 30, True)
            check("token-already-held-by-another-name", (rc, "NOTIFIER_TOKEN" in out),
                  (EX_REFUSED, True))
            with open(os.path.join(home, "dispatcher.env"), "w") as fh:
                fh.write("SLACK_BOT_TOKEN=xoxb-%s\n" % ("c" * 30))
            rc, out = cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "xoxb-" + "c" * 30, True)
            check("token-the-chat-bots-own-refused", (rc, "SLACK_BOT_TOKEN" in out,
                                                      "c" * 30 in out), (EX_REFUSED, True, False))
            # A dispatcher env file it cannot read, or one that IS the role account's env
            # file under another name, is refused: nothing is written.
            fresh = "xoxb-" + "s" * 30
            rc, out = cap(cmd_token, dict(conf, DISPATCHER_ENV_FILE="~/missing.env"), r,
                          _FakeSudo(), lambda _p: fresh, True)
            check("token-unreadable-dispatcher-env-refused",
                  (rc, "could not be read" in out, "s" * 30 in open(envp).read()),
                  (EX_REFUSED, True, False))
            os.symlink(envp, os.path.join(home, "dispatcher-link.env"))
            rc, out = cap(cmd_token, dict(conf, DISPATCHER_ENV_FILE="~/dispatcher-link.env"), r,
                          _FakeSudo(), lambda _p: fresh, True)
            check("token-dispatcher-env-is-the-same-file-refused",
                  (rc, "DISPATCHER_ENV_FILE" in out, "s" * 30 in open(envp).read()),
                  (EX_REFUSED, True, False))
            # The writer's temp file is opened O_EXCL|O_NOFOLLOW: a link planted at its
            # name, symbolic or hard, is refused and the file it points at is untouched.
            # (Each run pins its own pid: the backup's name carries it too.)
            for pid, kind, make in ((4242, "symlink", os.symlink), (4243, "hardlink", os.link)):
                victim = os.path.join(tmp, "victim-" + kind)
                with open(victim, "w") as fh:
                    fh.write("untouched\n")
                planted = "%s.bridge-setup.%d" % (envp, pid)
                make(victim, planted)
                pin_pid = "import os\nos.getpid = lambda: %d\n" % pid
                res = r.as_role(conf["ROLE_ACCOUNT"],
                                py(pin_pid + TOKEN_WRITER_PY, conf["ENV_FILE"],
                                   conf["SLACK_TOKEN_ENV"]), stdin="xoxb-%s\n" % ("u" * 30))
                check("token-tmp-refuses-a-planted-" + kind,
                      (res.ok, open(victim).read() == "untouched\n",
                       "u" * 30 in open(envp).read()), (False, True, False))
                if os.path.lexists(planted):
                    os.remove(planted)
            # A declined administrator password says why, and names THIS installer to
            # run again.
            for name, fn, args in (("token", cmd_token, (lambda _p: fresh, True)),
                                   ("install", cmd_install, (True,)),
                                   ("verify", cmd_verify, ())):
                rc, out = cap(fn, conf, r, se.SudoSession(validate=lambda: 1), *args)
                check("no-sudo-says-why:" + name,
                      (rc, "NO ADMINISTRATOR ACCESS" in out,
                       "pipeline_bridge_setup.py " + name in out,
                       "pipeline_stage_e_setup.py" in out), (EX_NOPRIV, True, True, False))
            good = "xoxb-" + "b" * 30
            rc, out = cap(cmd_token, conf, r, _FakeSudo(), lambda _p: good, True)
            env_text = open(envp).read()
            check("token-written", (rc, "BRIDGE_SLACK_BOT_TOKEN=%s" % good in env_text,
                                    "STAGE_E_LINEAR_API_KEY=" in env_text,
                                    oct(os.stat(envp).st_mode & 0o777)), (EX_OK, True, True,
                                                                          "0o600"))
            check("token-never-printed", good in out, False)
            check("token-went-on-stdin-only",
                  any(good in " ".join(w["argv"]) for w in r.writes), False)
            check("token-backed-up-first",
                  os.listdir(os.path.join(home, ".stage-e", "backups"))[0].startswith(
                      "env.bridge-setup."), True)
            for _ in range(4):
                cap(cmd_token, conf, r, _FakeSudo(), lambda _p: good, True)
            check("token-keeps-three-backups",
                  len([n for n in os.listdir(os.path.join(home, ".stage-e", "backups"))
                       if n.startswith("env.bridge-setup.")]), 3)
            check("no-sudo-declined-token",
                  cap(cmd_token, conf, r, _FakeSudo(ok=False), lambda _p: good, True)[0],
                  EX_NOPRIV)
            # A writer that fails says why, from its error, never silently (KIT-262). A
            # directory where the env file should be fails the read even for root.
            os.makedirs(os.path.join(home, ".stage-e", "env-dir"))
            rc, out = cap(cmd_token, dict(conf, ENV_FILE="~/.stage-e/env-dir",
                                          DISPATCHER_ENV_FILE=""), r, _FakeSudo(),
                          lambda _p: good, True)
            check("token-writer-failure-says-why",
                  (rc, "FAILED: the token could not be written into ~/.stage-e/env-dir" in out,
                   "IsADirectoryError" in out, good in out), (EX_FAILED, True, True, False))

            class _FailingWriter(_RoleRunner):
                def _exec(self, argv, stdin, timeout, cwd=None):
                    return se.Result(1, "", "Traceback (most recent call last):\n"
                                     "ValueError: %s\n" % stdin.strip())
            rc, out = cap(cmd_token, conf, _FailingWriter(home, root), _FakeSudo(),
                          lambda _p: good, True)
            check("token-writer-failure-never-shows-the-token",
                  (rc, "FAILED: the token could not be written" in out, good in out,
                   "b" * 30 in out), (EX_FAILED, True, False, False))

            # Before install, a config that is not there yet is a step to take, not a
            # failed read.
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root), _FakeSudo())
            check("verify-no-config-yet-says-install",
                  bool(re.search(r"config\s+BLOCKED-ON-HUMAN\s+run `install --apply`", out)),
                  True)

            # install: dry run writes nothing; --apply writes the config and plist and loads
            # nothing; a second run has nothing to do.
            r = _RoleRunner(home, root)
            rc, out = cap(cmd_install, conf, r, _FakeSudo(), False)
            check("install-dry-run-writes-nothing",
                  (rc, os.path.exists(os.path.join(home, ".stage-e", "bridge.json")),
                   os.path.exists(root + plist_path(conf))), (EX_BLOCKED, False, False))
            rc, out = cap(cmd_install, conf, r, _FakeSudo(), True)
            cfg_written = open(os.path.join(home, ".stage-e", "bridge.json")).read()
            check("install-apply-writes-both", (rc, cfg_written == config_bytes(conf),
                                                open(root + plist_path(conf)).read() ==
                                                render_plist(conf, home)), (EX_OK, True, True))
            check("install-never-loads", any("launchctl" in " ".join(w["argv"]) and
                                             ("bootstrap" in " ".join(w["argv"]))
                                             for w in r.writes), False)
            rc, out = cap(cmd_install, conf, _RoleRunner(home, root), _FakeSudo(), True)
            check("install-again-nothing-to-do", (rc, "Nothing to change" in out),
                  (EX_OK, True))

            # verify: not loaded -> blocked; loaded with a fresh good heartbeat -> 0; a stale
            # or failing heartbeat -> failed; a dry run that fails -> failed.
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=False), _FakeSudo())
            check("verify-not-loaded-blocks", (rc, "card CK-B2" in out), (EX_BLOCKED, True))
            check("verify-no-heartbeat-file-yet-is-said",
                  bool(re.search(r"heartbeat\s+BLOCKED-ON-HUMAN\s+no heartbeat yet", out)),
                  True)
            os.makedirs(os.path.join(home, ".stage-e", "state"))
            beat = os.path.join(home, ".stage-e", "state", "bridge-heartbeat.json")
            with open(beat, "w") as fh:
                json.dump({"schema": bridge.HEARTBEAT_SCHEMA, "at": "2026-10-10T12:00:00Z",
                           "exit": 0, "dry": False, "summary": "ok"}, fh)
            import calendar
            import time as _t
            at = calendar.timegm(_t.strptime("2026-10-10T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True), _FakeSudo(),
                          now=at + 30)
            check("verify-clean", (rc, "Good:" in out), (EX_OK, True))
            # KIT-284: a hand-off lane named after the install is a config to write again.
            rc, out = cap(cmd_verify, dict(conf, HANDOFF_STATES="Ready to merge"),
                          _RoleRunner(home, root, loaded=True), _FakeSudo(), now=at + 30)
            check("verify-asks-for-install-once-HANDOFF_STATES-changes",
                  (rc, bool(re.search(r"config\s+BLOCKED-ON-HUMAN\s+run `install --apply`",
                                      out))), (EX_BLOCKED, True))
            # The notifier's pings file (KIT-224 review): with none, the row says so; with
            # NOTIFIER_BOT_USER_ID set, the owner has turned pings on, so none blocks; a
            # file that is not the notifier's document fails; the notifier's own passes.
            check("verify-no-pings-file-is-said", bool(re.search(
                r"pings\s+SKIPPED\s+no pings file at ~/.stage-e/state/notifier-bridge-pings"
                r"\.json", out)), True)
            pconf = dict(conf, NOTIFIER_BOT_USER_ID="U0NOTIFY01")
            rc, out = cap(cmd_verify, pconf, _RoleRunner(home, root, loaded=True),
                          _FakeSudo(), now=at + 30)
            check("verify-no-pings-file-blocks-once-pings-are-on",
                  (rc, bool(re.search(r"pings\s+BLOCKED", out)), "BRIDGE_PINGS=yes" in out),
                  (EX_BLOCKED, True, True))
            pings_path = os.path.join(home, ".stage-e", "state", "notifier-bridge-pings.json")
            for name, doc, want_row in (
                    ("not-the-notifiers", {"schema": "other/1", "messages": {}},
                     r"pings\s+FAILED\s+.*is not a pipeline-notifier-bridge-pings/1"),
                    ("the-notifiers", {"schema": bridge.notify.BRIDGE_PINGS_SCHEMA,
                                       "messages": {}}, r"pings\s+ALREADY-DONE")):
                with open(pings_path, "w") as fh:
                    json.dump(doc, fh)
                rc, out = cap(cmd_verify, pconf, _RoleRunner(home, root, loaded=True),
                              _FakeSudo(), now=at + 30)
                check("verify-pings-file:" + name, bool(re.search(want_row, out)), True)
            os.remove(pings_path)
            # KIT-285: the notifier row, judged by the job's own rule. Off is said as off,
            # and a file that cannot be read is UNKNOWN, never a healthy notifier. Each case
            # runs alone, so one that raises fails as itself.
            def outcome(fn):
                try:
                    return fn()
                except Exception as exc:                          # noqa: BLE001
                    return "raised %s: %s" % (type(exc).__name__, exc)

            nbeat = os.path.join(home, ".stage-e", "state", "notifier-heartbeat.json")
            nplist = root + "/Library/LaunchDaemons/com.example.notifier.plist"
            nconf = dict(conf, NOTIFIER_JOB_LABEL="com.example.notifier")
            runs_it = {"Label": "com.example.notifier", "StartInterval": 300,
                       "ProgramArguments": ["/bin/sh", "-c", "exec /usr/bin/python3 "
                                            "$HOME/kit/scripts/pipeline_notify_local.py run"]}

            def stamp(**over):
                return json.dumps(dict({"schema": bridge.notify.HEARTBEAT_SCHEMA,
                                        "at": "2026-10-10T12:00:00Z", "exit": 0,
                                        "dry": False}, **over))

            def notifier_row_says(row_conf, body, plist_doc=runs_it, prints=None):
                for path, there in ((nbeat, body is not None), (nplist, plist_doc is not None)):
                    if not there and os.path.exists(path):
                        os.remove(path)
                os.makedirs(os.path.dirname(nplist), exist_ok=True)
                if plist_doc is not None:
                    with open(nplist, "wb") as fh:
                        plistlib.dump(plist_doc, fh)
                if body is not None:
                    with open(nbeat, "w") as fh:
                        fh.write(body)
                rc, out = cap(cmd_verify, row_conf, _RoleRunner(home, root, loaded=True,
                                                                prints=prints),
                              _FakeSudo(), now=at + 30)
                found = re.search(r"^\s*notifier\s+(\S+)\s+(.*)$", out, re.M)
                return rc, found.groups() if found else None

            got = outcome(lambda: notifier_row_says(conf, "{not json"))
            check("verify-notifier-row: no NOTIFIER_JOB_LABEL reads off, and nothing about the "
                  "notifier's files", got[1] if isinstance(got, tuple) else got,
                  ("SKIPPED", "off: NOTIFIER_JOB_LABEL is empty, so the bridge watches no "
                              "notifier"))
            for case, row_conf, beat_body, plist_doc, want_row, needle in (
                    ("a fresh clean run is watched, with the bound it is judged by", nconf,
                     stamp(), runs_it, "ALREADY-DONE", "quiet after 1020 s (3 x its 300 s "
                                                       "interval + 120 s)"),
                    ("an old heartbeat is quiet", nconf, stamp(at="2026-10-10T11:00:00Z"),
                     runs_it, "FAILED", "quiet: nothing from it since 2026-10-10T11:00:00Z"),
                    ("a run that failed is failing", nconf, stamp(exit=3), runs_it, "FAILED",
                     "failing: its run at 2026-10-10T12:00:00Z exited 3"),
                    ("no heartbeat yet blocks on loading it", nconf, None, runs_it,
                     "BLOCKED-ON-HUMAN", "card CK-N3"),
                    ("a heartbeat it cannot read is unknown", nconf, "{not json", runs_it,
                     "UNKNOWN", "could not be read (JSONDecodeError)"),
                    ("a heartbeat that is JSON but no object is unknown", nconf, "[1, 2]",
                     runs_it, "UNKNOWN", "could not be read (not a JSON object)"),
                    ("a rehearsal is unknown", nconf, stamp(dry=True), runs_it, "UNKNOWN",
                     "--dry-run"),
                    ("a plist that is not there is unknown", nconf, stamp(), None, "UNKNOWN",
                     "its plist /Library/LaunchDaemons/com.example.notifier.plist could not "
                     "be read"),
                    ("a plist that runs another job is unknown", nconf, stamp(), dict(
                        runs_it, ProgramArguments=["/bin/sh", "-c", "exec true"]), "UNKNOWN",
                     "does not run pipeline_notify_local.py"),
                    ("a bridge at half the notifier's interval is watched",
                     dict(nconf, INTERVAL_SECONDS="150"), stamp(), runs_it, "ALREADY-DONE",
                     "watched: its run at 2026-10-10T12:00:00Z exited 0"),
                    ("a bridge too slow to tell a sleep from a quiet notifier blocks, and says "
                     "to reload the job", dict(nconf, INTERVAL_SECONDS="151"), stamp(), runs_it,
                     "BLOCKED-ON-HUMAN", "Set INTERVAL_SECONDS to 150 or less, run `install "
                                         "--apply`, then reload the job: card CK-B3, then card "
                                         "CK-B2"),
                    # Review round 2: a notifier too fast for any interval the bridge takes
                    # names the notifier's interval, never one the conf check refuses.
                    ("a notifier too fast for the bridge names the notifier's interval to "
                     "change", dict(nconf, INTERVAL_SECONDS="15"), stamp(),
                     dict(runs_it, StartInterval=20), "BLOCKED-ON-HUMAN",
                     "the notifier runs every 20 s, too often for the bridge to tell the Mac's "
                     "sleep from a quiet notifier: the bridge runs once every 15 s at the "
                     "fastest. Set INTERVAL_SECONDS in notifier.conf to 30 or more")):
                got = outcome(lambda: notifier_row_says(row_conf, beat_body, plist_doc))
                row = got[1] if isinstance(got, tuple) else None
                check("verify-notifier-row: " + case,
                      (row[0], needle in row[1]) if row else got, (want_row, True))
            got = outcome(lambda: notifier_row_says(nconf, stamp(at="2026-10-10T11:00:00Z")))
            check("verify-notifier-row: a quiet notifier fails verify",
                  got[0] if isinstance(got, tuple) else got, EX_FAILED)
            # Review round 2: launchd keeps the interval a job was loaded with, and the
            # bridge judges the notifier by the plist on disk.
            def row_of(got):
                return (got[1][0], got[1][1]) if isinstance(got, tuple) and got[1] else got
            check("verify-notifier-row: launchd running the notifier at another interval than "
                  "its plist blocks, and says to reload it",
                  outcome(lambda: [row_of(notifier_row_says(
                      dict(nconf, INTERVAL_SECONDS="30"), stamp(), dict(runs_it,
                                                                         StartInterval=60),
                      {"com.example.notifier": "\trun interval = %d seconds\n" % every}))[0]
                      for every in (300, 60)]),
                  ["BLOCKED-ON-HUMAN", "ALREADY-DONE"])
            got = outcome(lambda: notifier_row_says(
                dict(nconf, INTERVAL_SECONDS="30"), stamp(), dict(runs_it, StartInterval=60),
                {"com.example.notifier": "\trun interval = 300 seconds\n"}))
            check("verify-notifier-row: the reload it names is the notifier's card CK-N3",
                  row_of(got)[1] if isinstance(row_of(got), tuple) else got,
                  "launchd still runs the notifier every 300 s, and its plist says 60 s. The "
                  "bridge judges it by the plist, so until the notifier is reloaded it may say "
                  "quiet, then back, again and again. Reload it: the notifier installer's card "
                  "CK-N3")

            def loaded_says(row_conf, printed):
                rc, out = cap(cmd_verify, row_conf, _RoleRunner(
                    home, root, loaded=True, prints={row_conf["JOB_LABEL"]: printed}),
                    _FakeSudo(), now=at + 30)
                found = re.search(r"^\s*loaded\s+(\S+)\s+(.*)$", out, re.M)
                return found.groups() if found else None
            check("verify-loaded: launchd running the bridge at another interval than "
                  "INTERVAL_SECONDS blocks, and says how to reload it; with the watch on, a "
                  "print with no interval is unknown",
                  outcome(lambda: [loaded_says(c_, p_) for c_, p_ in (
                      (dict(nconf, INTERVAL_SECONDS="150"), "\trun interval = 600 seconds\n"),
                      (dict(nconf, INTERVAL_SECONDS="150"), "\trun interval = 150 seconds\n"),
                      (nconf, "\tstate = running\n"),
                      (conf, "\tstate = running\n"))]),
                  [("BLOCKED-ON-HUMAN", "launchd still runs the bridge every 600 s, not every "
                                        "150 s (INTERVAL_SECONDS). Reload it: card CK-B3, then "
                                        "card CK-B2"),
                   ("ALREADY-DONE", "launchd holds the job, every 150 s"),
                   ("UNKNOWN", "launchd holds the job, and its print names no run interval, "
                               "so whether it runs every 60 s, as the notifier watch counts "
                               "on, could not be measured. Read it yourself: sudo launchctl "
                               "print system/com.example.bridge"),
                   ("ALREADY-DONE", "launchd holds the job")])
            # Stage E review: with the watch off nothing counts on the interval, so verify
            # keeps the verdict it had before the watch, and still says the interval.
            off_150 = dict(conf, INTERVAL_SECONDS="150")
            check("verify-loaded: with the watch off, launchd running the bridge at another "
                  "interval keeps its verdict, and says how to reload it",
                  outcome(lambda: [loaded_says(off_150, "\trun interval = %d seconds\n" % v)
                                   for v in (600, 150)]),
                  [("ALREADY-DONE", "launchd holds the job, but runs it every 600 s, not "
                                    "every 150 s (INTERVAL_SECONDS). To change that, reload "
                                    "it: card CK-B3, then card CK-B2"),
                   ("ALREADY-DONE", "launchd holds the job, every 150 s")])
            for path in (nbeat, nplist):
                if os.path.exists(path):
                    os.remove(path)
            check("config: notifier_plist and run_interval_seconds are composed only with "
                  "NOTIFIER_JOB_LABEL, from it and INTERVAL_SECONDS",
                  outcome(lambda: [(doc.get("notifier_plist"), doc.get("run_interval_seconds"))
                                   for doc in (compose_config(conf), compose_config(validate_conf(
                                       dict(values, NOTIFIER_JOB_LABEL="com.example.notifier",
                                            INTERVAL_SECONDS="90"))[0]))]),
                  [(None, None), ("/Library/LaunchDaemons/com.example.notifier.plist", 90)])
            check("conf: NOTIFIER_JOB_LABEL refused, by name, when it is no label or the "
                  "bridge's own",
                  outcome(lambda: [[e.split(" ")[0] for e in validate_conf(dict(
                      values, NOTIFIER_JOB_LABEL=v))[1]] for v in ("notifier",
                                                                 "com.example.bridge")]),
                  [["NOTIFIER_JOB_LABEL"], ["NOTIFIER_JOB_LABEL"]])
            def label_taken(v):
                conf_v, errors = validate_conf(dict(values, NOTIFIER_JOB_LABEL=v))
                return errors, compose_config(conf_v).get("notifier_plist")
            check("conf: NOTIFIER_JOB_LABEL takes any label the notifier's installer takes",
                  outcome(lambda: [label_taken(v) for v in ("local.notifier",
                                                            "com.Example.Notifier")]),
                  [([], "/Library/LaunchDaemons/local.notifier.plist"),
                   ([], "/Library/LaunchDaemons/com.Example.Notifier.plist")])

            def interval_named(v):
                conf_v, errors = validate_conf(dict(
                    values, NOTIFIER_JOB_LABEL="com.example.notifier", INTERVAL_SECONDS=v))
                return ([e.split(" ")[0] for e in errors],
                        compose_config(conf_v).get("run_interval_seconds"))
            check("conf: with the watch on, INTERVAL_SECONDS goes into the job's config, and a "
                  "bad one is named once, by its key",
                  outcome(lambda: [interval_named(v) for v in ("abc", "45")]),
                  [(["INTERVAL_SECONDS"], "abc"), ([], 45)])
            check("verify-reads-no-credential-value", ("k" * 30 in out, "b" * 30 in out),
                  (False, False))
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=False), _FakeSudo(),
                          now=at + 30)
            check("verify-fresh-beat-but-not-loaded-blocks",
                  (rc, bool(re.search(r"loaded\s+BLOCKED", out))), (EX_BLOCKED, True))
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True), _FakeSudo(),
                          now=at + 3600)
            check("verify-stale-heartbeat-fails", rc, EX_FAILED)
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True, job_rc=1),
                          _FakeSudo(), now=at + 30)
            check("verify-failing-dry-run-fails", rc, EX_FAILED)
            # A config problem is said on stderr only: the row shows it, not "nothing".
            rc, out = cap(cmd_verify, conf, _RoleRunner(
                home, root, loaded=True, job_rc=2, job_out="",
                job_err="FAIL: config problems:\n  - config 'lookback_days' must be a "
                        "positive integer, got 0\n"), _FakeSudo(), now=at + 30)
            check("verify-dry-run-shows-stderr",
                  (rc, bool(re.search(r"dry-run\s+FAILED\s+- config 'lookback_days'", out))),
                  (EX_FAILED, True))
            # ...but never a line shaped like a credential (a shell can echo an env line).
            rc, out = cap(cmd_verify, conf, _RoleRunner(
                home, root, loaded=True, job_rc=2, job_out="",
                job_err="sh: xoxb-%s: command not found\n" % ("q" * 30)), _FakeSudo(),
                now=at + 30)
            check("verify-dry-run-stderr-credential-not-shown", (rc, "q" * 30 in out),
                  (EX_FAILED, False))
            # A heartbeat or config that is there but cannot be read is UNKNOWN, with why:
            # never "no heartbeat yet" or "run `install --apply`", a state verify did not
            # see (KIT-262). A directory in a file's place fails the read even for root.
            beat_rows = []
            os.rename(beat, beat + ".kept")
            for name, body, why in (("unreadable", None, "(IsADirectoryError)"),
                                    ("corrupt", "{not json", "(JSONDecodeError)"),
                                    ("not-an-object", "[1]", "(not a JSON object)"),
                                    ("no-time", '{"exit": 0}',
                                     "(it has no readable `at` time)")):
                if body is None:
                    os.makedirs(beat)
                else:
                    with open(beat, "w") as fh:
                        fh.write(body)
                rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True),
                              _FakeSudo(), now=at + 30)
                row = [ln for ln in out.splitlines() if ln.strip().startswith("heartbeat")]
                beat_rows.append((name, rc, row and row[0].split()[1],
                                  bool(row) and "no heartbeat yet" in row[0],
                                  bool(row) and why in row[0]))
                (os.rmdir if body is None else os.remove)(beat)
                if name == "unreadable":
                    check("verify-unreadable-heartbeat-says-why",
                          bool(row) and "~/.stage-e/state/bridge-heartbeat.json could not be "
                          "read (IsADirectoryError)" in row[0], True)
            os.rename(beat + ".kept", beat)
            check("verify-a-heartbeat-it-cannot-read-is-unknown", beat_rows,
                  [(n, EX_UNKNOWN, "UNKNOWN", False, True)
                   for n in ("unreadable", "corrupt", "not-an-object", "no-time")])
            cfgp = os.path.join(home, ".stage-e", "bridge.json")
            os.rename(cfgp, cfgp + ".kept")
            os.makedirs(cfgp)
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True), _FakeSudo(),
                          now=at + 30)
            os.rmdir(cfgp)
            os.rename(cfgp + ".kept", cfgp)
            check("verify-a-config-it-cannot-read-is-unknown",
                  (rc, bool(re.search(r"config\s+UNKNOWN\s+~/.stage-e/bridge.json could not be "
                                      r"read \(IsADirectoryError\)", out))), (EX_UNKNOWN, True))
            with open(envp, "w") as fh:
                fh.write("STAGE_E_LINEAR_API_KEY=x\n")
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True), _FakeSudo(),
                          now=at + 30)
            check("verify-missing-token-name-blocks",
                  (rc, "BRIDGE_SLACK_BOT_TOKEN" in out), (EX_BLOCKED, True))
    finally:
        os.environ.clear()
        os.environ.update(saved)

    # The source writes nothing to the tracker, and loads no job.
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    body = src[:src.index("# Selftest\n")]
    check("no-tracker-write", "mutation" in body.lower(), False)
    check("never-bootstraps-itself", '"bootstrap"' in body or "'bootstrap'" in body, False)

    if failures:
        print("FAIL: pipeline_bridge_setup selftest")
        for f in failures:
            print("  - %s" % f)
        return EX_FAILED
    print("OK: pipeline_bridge_setup selftest (%d cases)" % cases[0])
    return EX_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the Slack bridge.")
    parser.add_argument("command", nargs="?",
                        choices=("compose", "token", "install", "verify", "card"))
    parser.add_argument("card_id", nargs="?")
    parser.add_argument("--conf", default=DEFAULT_CONF)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.command in ("token", "install", "verify"):
        found = agent_markers_present()
        if found:
            return refused(found)
    if args.command == "card":
        if args.card_id not in CARDS:
            say("usage: card %s" % "|".join(sorted(CARDS)))
            return EX_USAGE
        conf, errs = load_conf(args.conf)
        print_card(args.card_id, None if errs else conf, args.conf)
        return EX_OK
    if not args.command:
        parser.print_help()
        return EX_USAGE
    conf, errs = load_conf(args.conf)
    if errs:
        say("CONF PROBLEMS in %s:" % args.conf)
        for e in errs:
            say("  - %s" % e)
        return EX_USAGE
    if args.command == "compose":
        return cmd_compose(conf, args.conf)
    runner, sudo = se.Runner(), se.SudoSession()
    if args.command == "token":
        return cmd_token(conf, runner, sudo)
    if args.command == "install":
        return cmd_install(conf, runner, sudo, args.apply)
    return cmd_verify(conf, runner, sudo)


if __name__ == "__main__":
    sys.exit(main())
