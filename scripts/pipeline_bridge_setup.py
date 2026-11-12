#!/usr/bin/env python3
"""Bridge installer — the Slack bridge (scripts/pipeline_bridge.py), composed for a person.

    python3 scripts/pipeline_bridge_setup.py manifest [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py compose  [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py token    [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py writer-token [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py install  [--conf bridge.conf] [--apply]
    python3 scripts/pipeline_bridge_setup.py verify   [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py push-token|push-probe|push [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py card CK-B1
    python3 scripts/pipeline_bridge_setup.py --selftest

WHAT IT INSTALLS (KIT-220, KIT-221). One job, run by launchd every INTERVAL_SECONDS as the
dispatcher's own role account (owner decision, 2026-10-10): it reads the chat channels, and
with DM_USER_IDS each listed member's DM with its app (KIT-295), checks each sender with
Slack itself, and asks its own questions. It acts only with ACT=yes,
the default being no. It needs its OWN Slack app: if it posted as another app, that app could
post a question that looked like the bridge's. With TECH_LEAD_TURN=yes, the default since the
cutover (KIT-492), what a member writes in their DM that is no command, yes or no is answered
by the tech lead's own turn (KIT-489).

    manifest  prints the bridge app's manifest as JSON and nothing else, so it can be pasted
              whole into Slack's From-a-manifest box. It needs no BOT_USER_ID: the app does
              not exist yet, and its member ID is the one thing only the finished app can give
              (card CK-B1, KIT-271). Reads nothing live; runs anywhere, a model included.
    compose   prints the bridge app's manifest, the job's config, its LaunchDaemon plist and
              the order to do things in. Reads nothing live; runs anywhere, a model included.
              It needs the whole conf, BOT_USER_ID included.
    token     asks for the bridge app's bot token at a hidden prompt and writes it into the
              role account's env file, as that account, under SLACK_TOKEN_ENV. Refuses a
              token that is already in that file under another name (another app's, such as
              the notifier's), and refuses the dispatcher's env file as ENV_FILE. Prints names
              and lengths only.
    writer-token  the same, for the tech lead's token: the one `claude setup-token` prints,
              under TURN_TOKEN_ENV. Refuses anything else, an API key included. The name is
              the writer's, which first needed the token (KIT-297); the writer went at the
              cutover (KIT-492), and the turn uses the same token.
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
              plist says; empty, it says the watch is off (KIT-285). Its `old-paths` row says
              whether the old chain is off (KIT-492): no retired line left in bridge.conf, and
              the dispatcher's env file holding none of its chat app's names.
    standup   turns the daily stand-up on or off as STANDUP says (KIT-436): checks, shows what
              it will write to ~/.stage-e/standup.json, asks one yes, writes it as the role
              account, and reads it back. `install --apply` writes the same file.
    push-token  as `token`, for the app-level token Slack's Socket Mode connection opens with
              (KIT-561): xapp-, under PUSH_TOKEN_ENV.
    push-probe  opens the push connection once as the role account, with the job's own config and
              token, and says whether Slack said hello (KIT-561): the limits that account's
              network puts on the job, measured. It reads and answers nothing.
    push      the one script for switching instant replies on (KIT-561; card CK-B9): checks the conf,
              shows what it will do, asks one yes, sets PUSH_MODE and PUSH_JOB_LABEL in the conf,
              writes the config and the always-on job's plist, prints the manifest to paste into
              Slack, takes the app-level token at a hidden prompt, then runs the probe. It never
              loads the job: it ends by printing the two launchctl lines that do, and the verify
              to run after them.
    card      CK-B1 create the Slack app, CK-B2 load the job, CK-B3 turn the bridge off,
              CK-B4 prove it live, CK-B6 answer devs from the DM and hear when the notifier
              stops (KIT-332), CK-B7 let your own clear asks act after a stop window (ACT-4),
              CK-B9 turn instant replies on (KIT-561). CK-B5 (the writer) and CK-B8 (the
              wake) went at the cutover (KIT-492).

WHO RUNS SUDO. `token`, `writer-token`, `install`, `verify`, `standup`, `push-token`,
`push-probe` and `push` ask for the administrator password once, then run sudo
themselves: as the role
account (`sudo -u`), and as root to write the plist and to ask launchd whether the job is
loaded. None of them starts or stops the job. The `launchctl` lines that do (cards CK-B2 and CK-B3) are printed for the person
to run.

REFUSES IN AN AGENT ENVIRONMENT, before the conf is read: `token`, `writer-token`, `install`,
`verify`, `standup`, `push-token`, `push-probe` and `push`. `verify` runs the job's own dry run, which reads the owner's tracker key; a
model has no business running it. `compose` and `card` print and read nothing live, so they
are allowed. The markers are environment variables a session could unset: tamper-evident,
not tamper-proof, as everywhere else in this kit.

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
import pipeline_bridge_config as bridge_config  # noqa: E402
import pipeline_bridge_push_setup as push_setup  # noqa: E402  (KIT-561: Socket Mode's pieces)
import pipeline_heartbeat_monitor as hbm  # noqa: E402  (KIT-330: a beat aged in awake time)
import pipeline_tech_lead_turn as turn  # noqa: E402  (KIT-498: its keys and its probe)
# The writer's tool-less probe, kept to try the tech lead's token with one call (KIT-301,
# KIT-492): the bridge itself no longer imports the writer.
import pipeline_voice as voice  # noqa: E402

EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_NOPRIV, EX_BLOCKED = (
    se.EX_OK, se.EX_FAILED, se.EX_USAGE, se.EX_REFUSED, se.EX_UNKNOWN, se.EX_NOPRIV,
    se.EX_BLOCKED)
say = se.say

DEFAULT_CONF = "bridge.conf"
BRIDGE_SCRIPT = "pipeline_bridge.py"
VOICE_SCRIPT = "pipeline_voice.py"
TURN_SCRIPT = "pipeline_tech_lead_turn.py"
TOOLS_SCRIPT = "pipeline_tech_lead_tools.py"
LOG_NAME = "bridge.log"
TOKEN_RE = re.compile(r"^xoxb-[A-Za-z0-9-]{20,}$")
# A token `claude setup-token` prints (KIT-297). An API key (sk-ant-api...) never matches.
WRITER_TOKEN_RE = re.compile(r"^sk-ant-oat[0-9]{2}-[A-Za-z0-9_-]{20,}$")
# A whole token from `claude setup-token` was 108 characters on 2.1.287 (sk-ant-oat01- and
# 95 more). One much shorter was cut where it wrapped onto a second line in a narrow
# terminal: the hidden prompt keeps only what comes before a line break (KIT-301). A
# change in the token's format would show as this refusal, said, never as a silent pass.
WRITER_TOKEN_MIN_CHARS = 100
# A token cut there is never refused at its first line: the rest is asked for at further
# hidden prompts, until it has the whole length, the owner stops with an empty line, or
# this many lines have come. Refusing at once would send the owner's next paste, the
# token's tail, to the shell's visible prompt and its history (KIT-301 review). The
# asking goes on to the measured whole length, not to the floor above: a first line of
# 104 characters is a 108-character token cut where it wrapped (KIT-301 fix review). The
# floor is only for a shorter token the owner says is all of it, with an empty line.
WRITER_TOKEN_WHOLE_CHARS = 108
WRITER_TOKEN_MAX_LINES = 12
# A line that may begin a setup token: the loop asks for the rest of it even while it is
# too short for WRITER_TOKEN_RE, as a first line in a narrow terminal is.
WRITER_TOKEN_START_RE = re.compile(r"^sk-ant-oat[A-Za-z0-9_-]*$")
LABEL_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*){2,}$")
ACCOUNT_RE = re.compile(r"^_?[a-z][a-z0-9_-]{0,31}$")
PATH_RE = re.compile(r"^(~/|/)[A-Za-z0-9._/-]+$")
ABS_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
INTERVAL_MIN, INTERVAL_MAX = 15, 3600          # INTERVAL_SECONDS, in seconds
# KIT-340: QUESTION_REMINDER_HOURS, under the job's question_ttl_hours (24), which this
# installer leaves at its default: a reminder must come before the question expires.
REMINDER_HOURS_MAX = 23
# A whole number in ASCII digits. str.isdigit() also passes "²", which int() refuses.
WHOLE_RE = re.compile(r"^[0-9]+$")
# The bridge app's scopes: EXACTLY what the bridge calls, each cited. Its Slack calls are in
# pipeline_bridge_transport.py (KIT-388).
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
    "SLACK_TOKEN_ENV": ("BRIDGE_SLACK_BOT_TOKEN", "the NAME your tech lead's Slack token goes under"),
    "LINEAR_KEY_ENV": ("STAGE_E_LINEAR_API_KEY", "the NAME of the owner's tracker key"),
    "CHANNEL_IDS": ("", "the private channel id(s) it reads, comma-separated"),
    "OWNER_USER_ID": ("", "the tracker user id the key belongs to (the tool server is given it too)"),
    "BOT_USER_ID": ("", "your tech lead's own Slack bot user id (its member ID)"),
    "DISPATCHER_ENV_FILE": ("", "optional: the dispatcher's env file. `token` refuses a token it "
                                "holds, and `verify` checks it holds none of the old chat app's "
                                "names (KIT-492); unset, verify's old-paths row is UNKNOWN"),
    "TEAM_KEYS": ("", "the work teams it asks about, comma-separated"),
    "ALLOWED_SLACK_USERS": ("", "optional: the only Slack user ids who may answer"),
    "BRIDGE_CONFIG": ("~/.stage-e/bridge.json", "where the job's config is written"),
    "STATE_DIR": ("~/.stage-e/state", "where the job keeps its state and heartbeat"),
    "JOB_LABEL": ("", "the LaunchDaemon label, reverse-DNS, e.g. com.example.bridge"),
    "INTERVAL_SECONDS": ("60", "how often the job runs"),
    "APP_NAME": ("Tech lead", "what you call your tech lead: the Slack app's name and its "
                              "bot's, used when PROJECT_NAME is empty. Asked once when unset "
                              "(KIT-660)"),
    "PROJECT_NAME": ("", "optional (KIT-477): the project's name. The app is then called "
                         "\"Tech lead %s <name>\", which the manifest and verify use" % chr(0xb7)),
    "APP_AVATAR": ("", "optional (KIT-477): a square PNG file, 512 to 2000 pixels, for the "
                       "app's picture; the card says where to upload it"),
    "ACT": ("no", "yes: a verified yes moves an idea into Plan it, or approves an epic and "
                  "starts its children, as the owner. no: the tech lead asks and records only"),
    "PLAN_IT_STATE": ("Plan it", "the name of each team's Plan it state"),
    "AGENT_USER_NAME": ("", "the dispatcher's app user, by display name, that an approved "
                            "epic's children are handed to; empty refuses every approve (the "
                            "tool server is given it too, and refuses approve, retry and start "
                            "before it records)"),
    "EPIC_WIP": ("1", "how many children of one approved epic may run at once, 1 to 10"),
    "QUEUE_CONFIG": ("", "the queue's config file, as the role account sees it (KIT-454): with "
                         "it, the tech lead asks the queue what may start, inside the working "
                         "window, and starts it; empty: the tech lead's own hand-over of plans"),
    "QUEUE_EVERY_SECONDS": (str(bridge_config.DEFAULT_QUEUE_EVERY_SECONDS),
                            "how often the queue is asked, 60 to 86400 seconds"),
    "QUESTION_REMINDER_HOURS": (str(bridge.DEFAULT_QUESTION_REMINDER_HOURS),
                                "after how many hours a question still open in a DM gets its "
                                "one reminder, 0 to %d; 0: none (KIT-340)" % REMINDER_HOURS_MAX),
    "READY_STATE": ("Todo", "the name of each team's to-do state an approved epic moves to"),
    "DONE_STATE": (bridge.DEFAULT_DONE_STATE, "the name of each team's done state a plan moves "
                                              "to once every piece of it is merged (KIT-299)"),
    "HANDOFF_STATES": (", ".join(bridge.DEFAULT_HANDOFF_STATES),
                       "the states, by name, a child sits in while its pull request waits "
                       "for your merge, comma-separated"),
    "NOTIFIER_STATE_DIR": ("~/.stage-e/state", "the notifier's STATE_DIR, as notifier.conf "
                                               "sets it: the tech lead reads the pings it "
                                               "records there (KIT-224)"),
    "NOTIFIER_BOT_USER_ID": ("", "optional: the notifier app's own Slack bot user id (its "
                                 "member ID). A reply under one of its pings that it did "
                                 "not record is then refused aloud, and `verify` needs the "
                                 "pings file"),
    "DM_USER_IDS": ("", "optional: the Slack user ids whose DM with the tech lead's app it also "
                        "reads and answers in, comma-separated (KIT-295). Each message there "
                        "is one of its own, so it notifies. Empty: no DM"),
    "NOTIFIER_JOB_LABEL": ("", "optional: the notifier's JOB_LABEL, as notifier.conf sets it. "
                               "Set, each pass reads how often launchd runs the notifier from "
                               "its plist, and says in Slack when it goes quiet or keeps "
                               "failing (KIT-285). Empty: the watch is off"),
    "TECH_LEAD_TURN": ("yes", "yes, the default since the cutover (KIT-492): what a member "
                              "writes in their DM, other than a command, a yes or a no, is "
                              "answered by one turn of the claude CLI over the tech lead's tools "
                              "(KIT-498). Needs DM_USER_IDS, TURN_BIN and the token `writer-token` "
                              "stores. no: that text gets one line on what the tech lead can do"),
    "TURN_BIN": ("", "the claude CLI's full path, as `command -v claude` prints it; required "
                     "with TECH_LEAD_TURN=yes"),
    "TURN_MODEL": (turn.DEFAULTS["turn_model"], "the model that answers"),
    "TURN_TOKEN_ENV": (turn.DEFAULTS["turn_token_env"],
                       "the NAME the turn's token is under, where `writer-token` stores it: one "
                       "`claude setup-token` token. The notifier's writer may read it too"),
    "TURN_TOOLS_CONFIG": ("~/.stage-e/tech-lead/tools.json",
                          "the tool server's config, written by `install --apply` for the role "
                          "account (docs/TECH-LEAD-TOOLS.md)"),
    "TURN_KEY_FILE": ("~/.stage-e/tech-lead/tracker-key",
                      "the file the tool server reads the tracker key from, so the model's "
                      "environment holds none: `install --apply` writes it, as the role "
                      "account, from the env file's LINEAR_KEY_ENV, mode 0600"),
    "TURN_OUTBOX_DIR": ("~/.stage-e/tech-lead/outbox",
                        "the folder the tool server's plan and replan tools record what they "
                        "want asked in (KIT-520): `install --apply` makes it, as the role "
                        "account, mode 0700. The tech lead reads and empties it after each turn. "
                        "Without it those two tools are not offered"),
    "TECH_LEAD_LINK_HOSTS": (", ".join(bridge.DEFAULT_TECH_LEAD_LINK_HOSTS),
                             "the hosts, comma-separated, a link in the tech lead's answer may "
                             "go to and stay a link in your DM, each with its subdomains; any "
                             "other reaches it as plain text (KIT-343)"),
    # KIT-436: the daily stand-up (docs/STANDUP.md). These go into the stand-up's own
    # settings, ~/.stage-e/standup.json, never into the job's config.
    "STANDUP": ("off", "on: a morning rundown and an afternoon wrap go to the DM of the first "
                       "of DM_USER_IDS (KIT-349). Needs DM_USER_IDS. off: none, and each "
                       "piece's start keeps its own message"),
    "STANDUP_MORNING_AT": (bridge.standup.DEFAULTS["morning_at"],
                           "the morning rundown's local time, 24-hour HH:MM"),
    "STANDUP_AFTERNOON_AT": (bridge.standup.DEFAULTS["afternoon_at"],
                             "the afternoon wrap's local time, after the morning's"),
    "STANDUP_DAYS": (", ".join(bridge.standup.DEFAULTS["days"]),
                     "the days it posts, comma-separated, each from mon to sun"),
    "STANDUP_STATUS_FILE": (bridge.standup.DEFAULTS["status_file"],
                            "the status job's file: stage-e.conf's STATUS_FILE, if you set one"),
    "STANDUP_DAY_PLAN_FILE": ("", "the day plan file, to give the afternoon wrap its spend line: "
                                  "~/.stage-e/queue/day-plan.json. Empty: no spend line (KIT-471)"),
    "STANDUP_USAGE_FILE": ("", "the usage file, to give the wrap a short week on STANDUP_WEEKLY_DAY: "
                               "~/.stage-e/usage/usage.json. Empty: no weekly section (KIT-471)"),
    "STANDUP_WEEKLY_DAY": ("", "the day of the weekly section, one of STANDUP_DAYS. Empty: the "
                               "last of them"),
    "STANDUP_SPRINT": ("no", "yes: both messages add one line saying which day of the plan's "
                             "weekly sprint it is, when the week resets and how full the window "
                             "is. Needs STANDUP_USAGE_FILE (KIT-574)"),
    "STANDUP_SPRINT_PLAN_FILE": ("", "the sprint plan file (KIT-575): the file the queue's morning job writes the "
                                     "week's proposal into (SPRINT_PLAN_FILE in queue.conf, the same path). The "
                                     "stand-up sends a waiting proposal to your DM once, and your reply "
                                     "`plan sprint <date>` records the plan in it. Needs STANDUP_SPRINT=yes. Empty: off"),
    "STANDUP_HEALTH_FILE": ("", "the health watch's status file (its HEALTH_STATUS_FILE, KIT-609): when its memory or "
                                "burn-rate check is firing, both messages add one plain line; otherwise they say "
                                "nothing about either. Empty: off"),
    "STANDUP_SPRINT_REVIEW_CONFIG": ("", "the sprint review's config file (KIT-576, `python3 scripts/pipeline_sprint_review.py "
                                         "--example-config`): on the sprint's last working day, from the afternoon "
                                         "time, one message links a Linear document recapping the week. Needs "
                                         "STANDUP_SPRINT=yes. Empty: off"),
    "DIRECT_ASKS": ("no", "yes: your own DM ask that names plan or replan and up to "
                          "DIRECT_MAX_TICKETS tickets acts DIRECT_GRACE_SECONDS after the tech lead "
                          "says what it will do, with no question, unless you write again "
                          "first (ACT-4). Needs ACT=yes, DM_USER_IDS and AGENT_USER_NAME. Turn "
                          "it on with `direct-asks --apply`, which asks one yes"),
    "DIRECT_GRACE_SECONDS": ("90", "the stop window before a direct ask acts, 30 to 3600"),
    "DIRECT_MAX_TICKETS": ("3", "the most tickets one direct ask acts on, 1 to 10. More get one "
                                "combined question. It only sets where asking starts"),
    "DIRECT_MAX_AGE_MINUTES": ("10", "a direct ask first read later than this after it was "
                                     "sent, say after the Mac slept, gets a question instead, 1 "
                                     "to 1440"),
}
# KIT-561: instant replies. Off by default; with it off nothing here changes what is composed.
CONF_KEYS.update(push_setup.CONF_KEYS)
REQUIRED = ("ROLE_ACCOUNT", "ROLE_KIT_CLONE", "CHANNEL_IDS", "OWNER_USER_ID", "TEAM_KEYS",
            "JOB_LABEL", "BOT_USER_ID")
# KIT-492, the cutover: the keys of the old chain, and what took each one's place. A bridge.conf
# written before it still loads: each is read as nothing, never composed, and `verify`'s
# `old-paths` row names the lines to take out. Any other unknown key is still refused.
RETIRED_CONF_KEYS = {
    "ASK_TECH_LEAD": "the tech lead's own turn answers the DM (TECH_LEAD_TURN)",
    "CHAT_BOT_USER_ID": "no chat bot is asked any more",
    "BACK_ROOM_CHANNEL_ID": "there is no back room any more",
    "FORWARD_HISTORY_MESSAGES": "the turn keeps its own conversation",
    "FORWARD_HISTORY_CHARS": "the turn keeps its own conversation",
    "WRITER": "every message is the tech lead's own fixed text",
    "WRITER_BIN": "the turn uses TURN_BIN",
    "WRITER_MODEL": "the writer is gone",
    "WRITER_TOKEN_ENV": "the token is under TURN_TOKEN_ENV",
    "TECH_LEAD_WAKE": "the rundown carries the tech lead's take (KIT-493)",
    "WAKE_MAX_PER_DAY": "the wake is gone",
    "WAKE_QUIET_HOURS": "the wake is gone",
    "WAKE_PR_STUCK_HOURS": "the wake is gone",
}
# The names the chat lane's `env-names` writes into the dispatcher's env file. While it holds
# them, the dispatcher's chat lane can still answer in Slack (chat lane card CK-C6 takes them out).
CHAT_LANE_ENV_NAMES = ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")


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
        if key not in CONF_KEYS and key not in RETIRED_CONF_KEYS:
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
    # KIT-492: the old chain's lines this conf still has, by name. Read as nothing.
    conf["RETIRED"] = sorted(k for k in values if k in RETIRED_CONF_KEYS)
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
        errors.append("ENV_FILE and DISPATCHER_ENV_FILE both name %s. The tech lead's Slack token goes "
                      "in the role account's own env file, never the dispatcher's: every "
                      "session the dispatcher starts can read that one" % conf["ENV_FILE"])
    if conf["JOB_LABEL"] and not LABEL_RE.match(conf["JOB_LABEL"]):
        errors.append("JOB_LABEL must be reverse-DNS like com.example.bridge, got %r"
                      % conf["JOB_LABEL"])
    if conf["ACT"] not in ("yes", "no"):
        errors.append("ACT must be yes or no, got %r" % conf["ACT"])
    if (not WHOLE_RE.match(conf["INTERVAL_SECONDS"])
            or not INTERVAL_MIN <= int(conf["INTERVAL_SECONDS"]) <= INTERVAL_MAX):
        errors.append("INTERVAL_SECONDS must be a whole number from %d to %d"
                      % (INTERVAL_MIN, INTERVAL_MAX))
    queue = conf["QUEUE_CONFIG"]
    if queue and not (queue.startswith("/") or queue.startswith("~/")):
        errors.append("QUEUE_CONFIG must be a full path, or start with ~/, or be empty")
    every = conf["QUEUE_EVERY_SECONDS"]
    if not WHOLE_RE.match(every) or not 60 <= int(every) <= 86400:
        errors.append("QUEUE_EVERY_SECONDS must be a whole number from 60 to 86400")
    if not WHOLE_RE.match(conf["EPIC_WIP"]) or not 1 <= int(conf["EPIC_WIP"]) <= 10:
        errors.append("EPIC_WIP must be a whole number from 1 to 10")
    hours = conf["QUESTION_REMINDER_HOURS"]
    if not WHOLE_RE.match(hours) or not 0 <= int(hours) <= REMINDER_HOURS_MAX:
        errors.append("QUESTION_REMINDER_HOURS must be a whole number from 0 to %d, got %r: a "
                      "question expires after 24 hours, and its reminder comes before"
                      % (REMINDER_HOURS_MAX, hours))
    # The notifier's own installer's rule for its JOB_LABEL, as Stage E's conf checks it.
    label = conf["NOTIFIER_JOB_LABEL"]
    if label and not se._RDNS_RE.match(label):
        errors.append("NOTIFIER_JOB_LABEL must be the notifier's reverse-DNS JOB_LABEL, or "
                      "empty, got %r" % label)
    elif label and label == conf["JOB_LABEL"]:
        errors.append("NOTIFIER_JOB_LABEL is the tech lead's own JOB_LABEL. Set it to the "
                      "JOB_LABEL in notifier.conf, or leave it empty")
    # KIT-295: each a member's own user id, named once. The job checks the same.
    dms = split_list(conf["DM_USER_IDS"])
    bad = [u for u in dms if not bridge.SLACK_USER_RE.match(u)]
    if bad:
        errors.append("DM_USER_IDS must list Slack user ids like U0123456789, each a member's "
                      "own (their profile, then Copy member ID), got %s" % ", ".join(bad))
    elif len(set(dms)) != len(dms):
        errors.append("DM_USER_IDS names a member twice")
    errors.extend(turn_problems(conf))
    errors.extend(app_problems(conf))
    errors.extend(tech_lead_conf_errors(conf))
    errors.extend(standup_problems(conf))
    errors.extend(push_setup.problems(conf))
    # The job's own check runs whatever the checks above found, so every bad value is
    # named in one pass. A value both refuse is named once, by its conf key.
    named = [k for k in COMPOSED_AS if any(e.startswith(k + " ") for e in errors)]
    hidden = ["'%s'" % key for k in named for key in (
        (COMPOSED_AS[k],) if isinstance(COMPOSED_AS[k], str) else COMPOSED_AS[k])]
    try:
        bridge.validate_config(dict(compose_config(conf),
                                    state_dir="/nonexistent-%s" % os.getpid()))
    except bridge.BridgeError as exc:
        errors.extend(in_conf_terms(e) for e in str(exc).split("\n  - ")[1:] or [str(exc)]
                      if not any(h in e.partition(", got ")[0] for h in hidden))
    return conf, errors


# KIT-436: each stand-up setting, and the bridge.conf key it comes from.
STANDUP_KEY_FOR = {"morning_at": "STANDUP_MORNING_AT", "afternoon_at": "STANDUP_AFTERNOON_AT",
                   "days": "STANDUP_DAYS", "status_file": "STANDUP_STATUS_FILE",
                   "bridge_config": "BRIDGE_CONFIG", "day_plan_file": "STANDUP_DAY_PLAN_FILE",
                   "usage_file": "STANDUP_USAGE_FILE", "weekly_day": "STANDUP_WEEKLY_DAY",
                   "sprint": "STANDUP_SPRINT", "sprint_plan_file": "STANDUP_SPRINT_PLAN_FILE",
                   "sprint_review_config": "STANDUP_SPRINT_REVIEW_CONFIG",
                   "health_file": "STANDUP_HEALTH_FILE"}


# KIT-477: the app a project's owner talks to is that project's tech lead. Slack's limit on an
# app's name is 35 characters; the prefix takes 12, so the project's name has 23.
DOT_SEP = chr(0xb7)
NAME_PREFIX = "Tech lead " + DOT_SEP + " "
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,22}$")
AVATAR_MIN, AVATAR_MAX = 512, 2000


# KIT-660: the person names their tech lead. "bridge" is the plumbing's name, never theirs.
DEFAULT_LEAD_NAME = CONF_KEYS["APP_NAME"][0]
NAME_QUESTION = ("What do you want to call your tech lead? It is the name you will see in Slack. "
                 "Press Return for %s: ")
LEAD_NAME_RE = re.compile(r"^[^\W_](?:[\w .'-]{0,33}[^\W_])?$")
NAME_TRIES = 3


def names_its_lead(values):
    """True when the conf's own lines name the tech lead (APP_NAME or PROJECT_NAME set)."""
    return bool(values.get("APP_NAME") or values.get("PROJECT_NAME"))


def lead_name_problem(name):
    """None when `name` is one Slack takes as an app's name and the conf can hold, else why."""
    if not LEAD_NAME_RE.match(name) or name != name.strip():
        return ("use 1 to 35 letters, digits, spaces, dots, dashes, apostrophes and "
                "underscores, starting and ending with a letter or digit")
    return None


def _ask_line(prompt):
    """The question on stderr, the answer from stdin: stdout stays the manifest's alone."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\n")


def ask_name(conf_path, values, reader=None, tty=None, err=None):
    """KIT-660: asks once what to call the tech lead, when the conf names none, and writes the
    answer into the conf as APP_NAME. A conf that names it is kept as it is and nothing is
    asked. With no terminal, or no answer, nothing blocks and nothing is written: the default
    is used, and said. Returns (exit code, the values to use)."""
    err = err or sys.stderr
    if names_its_lead(values):
        return EX_OK, values
    if not (tty if tty is not None else (sys.stdin.isatty() and sys.stderr.isatty())):
        err.write("This is not a terminal, so the name was not asked: your tech lead is called "
                  "%r. To name it, set APP_NAME= in %s, or run this again at a terminal.\n"
                  % (DEFAULT_LEAD_NAME, conf_path))
        return EX_OK, values
    for _n in range(NAME_TRIES):
        try:
            answer = (reader or _ask_line)(NAME_QUESTION % DEFAULT_LEAD_NAME)
        except (EOFError, KeyboardInterrupt):
            err.write("\nNo answer, so nothing was written: your tech lead is called %r for "
                      "now. Run this again to name it.\n" % DEFAULT_LEAD_NAME)
            return EX_OK, values
        name = answer.strip() or DEFAULT_LEAD_NAME
        why = lead_name_problem(name)
        if why is None:
            break
        err.write("Slack would not take %r as a name: %s.\n" % (answer[:80], why))
    else:
        err.write("No name was written to %s. Run this again to name your tech lead.\n"
                  % conf_path)
        return EX_USAGE, values
    # A temp file beside the conf, swapped in whole: a failed write never leaves it partial.
    tmp = "%s.name.%d" % (conf_path, os.getpid())
    try:
        with open(conf_path, encoding="utf-8") as fh:
            text = fh.read()
        mode = os.stat(conf_path).st_mode & 0o777
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(set_conf_line(text, "APP_NAME", name))
        os.replace(tmp, conf_path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        err.write("Could not write the name into %s: %s. Nothing was written.\n"
                  % (conf_path, exc))
        return EX_FAILED, values
    err.write("Saved: your tech lead is called %r (APP_NAME in %s). To rename it later, change "
              "that line and paste the manifest again.\n" % (name, conf_path))
    return EX_OK, dict(values, APP_NAME=name)


def ask_name_from_file(conf_path, reader=None, tty=None, err=None):
    """`install` asks for the name too, for a person who comes to it first (KIT-660). A conf
    that cannot be read or parsed is left to the conf check that follows, which says why."""
    try:
        with open(conf_path, encoding="utf-8") as fh:
            values, errors = parse_conf(fh.read())
    except OSError:
        return EX_OK
    if errors:
        return EX_OK
    return ask_name(conf_path, values, reader=reader, tty=tty, err=err)[0]


def app_name(conf):
    """The Slack app's name (KIT-477): the project's tech lead when PROJECT_NAME is set, else
    APP_NAME as before."""
    return NAME_PREFIX + conf["PROJECT_NAME"] if conf.get("PROJECT_NAME") else conf["APP_NAME"]


def avatar_problem(path):
    """None when `path` is a square PNG Slack takes as an app icon, else why not (KIT-477). The
    file is only read here, never sent anywhere: the person uploads it by hand (card CK-B1)."""
    path = os.path.expanduser(path)
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return "is not a plain file"
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError as exc:
        return "could not be read (%s)" % type(exc).__name__
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return "is not a PNG file"
    width, height = (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
    if width != height or not AVATAR_MIN <= width <= AVATAR_MAX:
        return "is %dx%d pixels; it must be square, %d to %d" % (width, height, AVATAR_MIN,
                                                                 AVATAR_MAX)
    return None


def app_problems(conf):
    """KIT-477: the app's name and picture, checked whether or not they are set to anything."""
    errors = []
    name = conf["PROJECT_NAME"]
    if name and not PROJECT_NAME_RE.match(name):
        errors.append("PROJECT_NAME must be 1 to 23 characters: letters, digits, spaces, dots, "
                      "dashes and underscores, starting with a letter or digit, got %r" % name)
    if not name and len(conf["APP_NAME"]) > 35:
        errors.append("APP_NAME is %d characters; Slack allows 35" % len(conf["APP_NAME"]))
    if conf["APP_AVATAR"]:
        why = avatar_problem(conf["APP_AVATAR"])
        if why:
            errors.append("APP_AVATAR %s: %s" % (conf["APP_AVATAR"], why))
    return errors


def turn_problems(conf):
    """KIT-498: the tech lead's turn's keys. Each is checked whether TECH_LEAD_TURN is yes or no,
    so a typo is said when it is made. Since the cutover the turn is on by default (KIT-492), so
    a conf that leaves it on and names no TURN_BIN or no DM member is told both ways out."""
    errors = []
    if conf["TECH_LEAD_TURN"] not in ("yes", "no"):
        errors.append("TECH_LEAD_TURN must be yes or no, got %r" % conf["TECH_LEAD_TURN"])
    on = conf["TECH_LEAD_TURN"] == "yes"
    if on and not conf["TURN_BIN"]:
        errors.append("TURN_BIN is required with TECH_LEAD_TURN=yes, the default: the claude "
                      "CLI's full path, as `command -v claude` prints it. Or set "
                      "TECH_LEAD_TURN=no")
    if on and not split_list(conf["DM_USER_IDS"]):
        errors.append("TECH_LEAD_TURN is yes, the default, so it needs DM_USER_IDS: the tech "
                      "lead answers in a member's DM. Name yourself there, or set "
                      "TECH_LEAD_TURN=no")
    if conf["TURN_BIN"] and not ABS_PATH_RE.match(conf["TURN_BIN"]):
        errors.append("TURN_BIN must be the claude CLI's full path, starting with /, of plain "
                      "characters, got %r. The job runs under launchd, whose PATH is short"
                      % conf["TURN_BIN"])
    if turn.settings({"turn_model": conf["TURN_MODEL"]})[1]:
        errors.append("TURN_MODEL must be a model alias or name like sonnet or haiku, got %r"
                      % conf["TURN_MODEL"])
    name = conf["TURN_TOKEN_ENV"]
    if not bridge.ENV_NAME_RE.match(name):
        errors.append("TURN_TOKEN_ENV must be an env var NAME like %s, got %r"
                      % (turn.DEFAULTS["turn_token_env"], name))
    else:
        taken = [k for k in ("SLACK_TOKEN_ENV", "LINEAR_KEY_ENV") if conf[k] == name]
        if taken:
            errors.append("TURN_TOKEN_ENV is %s, the name %s already uses. The tech lead's "
                          "token needs a name of its own" % (name, " and ".join(taken)))
    for key in ("TURN_TOOLS_CONFIG", "TURN_KEY_FILE", "TURN_OUTBOX_DIR"):
        if not PATH_RE.match(conf[key]) or "$" in conf[key]:
            errors.append("%s must be an absolute or ~/ path of plain characters, got %r"
                          % (key, conf[key]))
    if conf["TURN_TOOLS_CONFIG"] == conf["TURN_KEY_FILE"]:
        errors.append("TURN_TOOLS_CONFIG and TURN_KEY_FILE must be two files")
    if conf["TURN_OUTBOX_DIR"] in (conf["TURN_TOOLS_CONFIG"], conf["TURN_KEY_FILE"]):
        errors.append("TURN_OUTBOX_DIR must be a folder of its own, not TURN_TOOLS_CONFIG or "
                      "TURN_KEY_FILE")
    return errors


def standup_settings(conf):
    """KIT-436: what ~/.stage-e/standup.json holds for this conf. The times and days still
    reach each rundown through the stand-up's one function, rundown_times."""
    doc = {"schema": bridge.standup.SETTINGS_SCHEMA, "on": conf["STANDUP"] == "on",
           "morning_at": conf["STANDUP_MORNING_AT"],
           "afternoon_at": conf["STANDUP_AFTERNOON_AT"],
           "days": split_list(conf["STANDUP_DAYS"]),
           "status_file": conf["STANDUP_STATUS_FILE"], "bridge_config": conf["BRIDGE_CONFIG"]}
    # KIT-471: the spend keys are written only when set, so a conf without them writes the
    # file it always wrote.
    for key in ("day_plan_file", "usage_file", "weekly_day", "health_file", "sprint_plan_file", "sprint_review_config"):
        if conf[STANDUP_KEY_FOR[key]]:
            doc[key] = conf[STANDUP_KEY_FOR[key]]
    if conf["STANDUP_SPRINT"] == "yes":       # KIT-574: written only when on
        doc["sprint"] = True
    return doc


def standup_bytes(conf):
    return json.dumps(standup_settings(conf), indent=2, sort_keys=True) + "\n"


def standup_problems(conf):
    """KIT-436: the stand-up's keys, checked whether it is on or off, by the stand-up's own
    rules, so a typo is said when it is made and each is named by its conf key."""
    errors = []
    if conf["STANDUP"] not in ("on", "off"):
        errors.append("STANDUP must be on or off, got %r" % conf["STANDUP"])
    elif conf["STANDUP"] == "on" and not split_list(conf["DM_USER_IDS"]):
        errors.append("STANDUP=on needs DM_USER_IDS: the stand-up goes to the first member's DM")
    if conf["STANDUP_SPRINT"] not in ("yes", "no"):
        errors.append("STANDUP_SPRINT must be yes or no, got %r" % conf["STANDUP_SPRINT"])
    try:
        bridge.standup.validate_settings(standup_settings(dict(conf, STANDUP="off")),
                                         "bridge.conf")
    except bridge.standup.StandupError as exc:
        for problem in str(exc).split(": ", 1)[-1].split("; "):
            if "'late_by_minutes'" in problem:
                # No conf key holds it: say it in the two times' terms.
                problem = ("STANDUP_AFTERNOON_AT must be more than %d minutes after "
                           "STANDUP_MORNING_AT: a late morning rundown may still go out that "
                           "long after its time" % bridge.standup.DEFAULTS["late_by_minutes"])
            for key, conf_key in STANDUP_KEY_FOR.items():
                problem = problem.replace("'%s'" % key, conf_key)
            errors.append(problem)
    return errors


# The conf keys with a check of their own above, and the config key (or keys) each is
# composed into. NOTIFIER_STATE_DIR makes two: the pings file, and with DM_USER_IDS the
# notifier's record of its DM pages (KIT-297 review).
COMPOSED_AS = {"CHANNEL_IDS": "channel_ids", "OWNER_USER_ID": "owner_user_id",
               "BOT_USER_ID": "bot_user_id", "TEAM_KEYS": "team_keys", "EPIC_WIP": "epic_wip",
               "ACT": "act", "NOTIFIER_STATE_DIR": ("pings_file", "notifier_posts_file"),
               "INTERVAL_SECONDS": "run_interval_seconds", "DM_USER_IDS": "dm_user_ids",
               "QUESTION_REMINDER_HOURS": "question_reminder_hours",
               "QUEUE_CONFIG": "queue_config", "QUEUE_EVERY_SECONDS": "queue_every_seconds"}
# KIT-298: the tech lead's keys, each checked above by its conf name.
COMPOSED_AS.update({"TECH_LEAD_TURN": "tech_lead_turn", "TURN_BIN": "turn_bin",
                    "TURN_MODEL": "turn_model", "TURN_TOKEN_ENV": "turn_token_env",
                    "TURN_TOOLS_CONFIG": "turn_tools_config"})
COMPOSED_AS.update({"TECH_LEAD_LINK_HOSTS": "tech_lead_link_hosts"})
# ACT-4: the direct asks' keys, each checked below by its conf name.
COMPOSED_AS.update({"DIRECT_ASKS": "direct_asks", "DIRECT_GRACE_SECONDS": "direct_grace_seconds",
                    "DIRECT_MAX_TICKETS": "direct_max_tickets",
                    "DIRECT_MAX_AGE_MINUTES": "direct_max_age_minutes"})
DIRECT_NUMBERS = (("DIRECT_GRACE_SECONDS", "direct_grace_seconds", 30, 3600),
                  ("DIRECT_MAX_TICKETS", "direct_max_tickets", 1, 10),
                  ("DIRECT_MAX_AGE_MINUTES", "direct_max_age_minutes", 1, 1440))
# KIT-561: the push job's keys, each checked by push_setup.problems under its conf name.
COMPOSED_AS.update({"PUSH_MODE": "push_mode", "PUSH_TOKEN_ENV": "push_token_env"})
# KIT-292: the bridge.conf key each key of the job's config is composed from, so a refusal
# the job's own check makes names the line you edit. None: written by this installer from
# no conf key. A composed key missing here fails the selftest. (The one pair, the back room's
# channel and its fallback, went at the cutover, KIT-492.)
JOB_KEY_FROM = {
    "schema": None, "state_dir": "STATE_DIR", "slack_token_env": "SLACK_TOKEN_ENV",
    "linear_key_env": "LINEAR_KEY_ENV", "channel_ids": "CHANNEL_IDS",
    "owner_user_id": "OWNER_USER_ID", "bot_user_id": "BOT_USER_ID", "team_keys": "TEAM_KEYS",
    "allowed_slack_users": "ALLOWED_SLACK_USERS", "act": "ACT",
    "plan_it_state": "PLAN_IT_STATE", "ready_state": "READY_STATE",
    "agent_user_name": "AGENT_USER_NAME", "epic_wip": "EPIC_WIP",
    "pings_file": "NOTIFIER_STATE_DIR", "handoff_states": "HANDOFF_STATES",
    "done_state": "DONE_STATE", "notifier_bot_user_id": "NOTIFIER_BOT_USER_ID",
    "dm_user_ids": "DM_USER_IDS", "notifier_posts_file": "NOTIFIER_STATE_DIR",
    "notifier_plist": "NOTIFIER_JOB_LABEL", "run_interval_seconds": "INTERVAL_SECONDS",
    "tech_lead_turn": "TECH_LEAD_TURN", "turn_bin": "TURN_BIN", "turn_model": "TURN_MODEL",
    "turn_token_env": "TURN_TOKEN_ENV", "turn_tools_config": "TURN_TOOLS_CONFIG",
    "tech_lead_link_hosts": "TECH_LEAD_LINK_HOSTS",
    "direct_asks": "DIRECT_ASKS", "direct_grace_seconds": "DIRECT_GRACE_SECONDS",
    "direct_max_tickets": "DIRECT_MAX_TICKETS",
    "direct_max_age_minutes": "DIRECT_MAX_AGE_MINUTES",
    "question_reminder_hours": "QUESTION_REMINDER_HOURS",
    "queue_config": "QUEUE_CONFIG", "queue_every_seconds": "QUEUE_EVERY_SECONDS",
    "push_mode": "PUSH_MODE", "push_token_env": "PUSH_TOKEN_ENV",
}
_QUOTED_KEY_RE = re.compile(r"'([a-z][a-z0-9_]*)'")
_VALUE_TAIL_RE = re.compile(r", got (?=['\"\[{(])")


def in_conf_terms(message):
    """A refusal from the job's own config check, with each config key it quotes named as
    the bridge.conf key it came from (KIT-292). A key this installer never composes is the
    job's own, and is said to be. What follows `got` is the value as given, and stays as
    it was: a value that is spelled like a key is still that value (Stage E's review)."""
    text = message[len("config "):] if message.startswith("config ") else message
    # A template that echoes the value at its end writes `got %r` or `got %d`. Only a value
    # that opens with a quote or a bracket can hold a quoted key name, so only that starts
    # the tail: a ", got " inside an echoed state name ("Todo, got it") is passed over, and
    # a number or a constant reads the same renamed or not. A state name that itself opens
    # `got '` or `got [` still starts it, so keys after it keep the job's names. A value
    # echoed mid-template (a path, the joined state names) is renamed only where it holds
    # a quoted key name (final reviews of KIT-292).
    found = _VALUE_TAIL_RE.search(text)
    text, got, given = (text[:found.start()], "", text[found.start():]) if found else (
        text, "", "")

    def named(m):
        key = m.group(1)
        if key not in bridge.CONFIG_KEYS:
            return m.group(0)
        source = JOB_KEY_FROM.get(key)
        if source:
            return source
        return "'%s' (in the job's own config: bridge.conf does not set it)" % key
    return _QUOTED_KEY_RE.sub(named, text) + got + given


def doc_writes(source):
    """(keys written, writes by a name it can't read) for a function's source: every way it
    can put a key into `doc`. The selftest pins compose_config's to one write by a name, the
    HISTORY_KEYS loop's, so a key composed some other way can't go without its JOB_KEY_FROM
    entry (final delta review of KIT-292; the loop went with the back room, KIT-492, so the
    pin is now none). Only mappings that flow into doc are read: one
    assigned to it, merged into it, spread from it or returned. A lookup table counts for
    nothing, and a copy of doc kept under another name counts as a write it can't read
    (reviews of KIT-292)."""
    import ast
    written, dynamic = set(), []

    def key_of(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if hasattr(ast, "Index") and isinstance(node, ast.Index):    # Python 3.8
            return key_of(node.value)
        return None

    def is_doc(node):
        return isinstance(node, ast.Name) and node.id == "doc"

    def is_dict_call(node):
        return isinstance(node, ast.Call) and getattr(node.func, "id", None) == "dict"

    def copies_doc(node):
        """A new mapping built from doc: dict(doc, ...), doc.copy(), {**doc, ...}."""
        if is_dict_call(node) and any(is_doc(a) for a in node.args):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr in ("copy", "deepcopy") and \
                (is_doc(node.func.value) or any(is_doc(a) for a in node.args)):
            return True
        return isinstance(node, ast.Dict) and any(
            k is None and is_doc(v) for k, v in zip(node.keys, node.values))

    def from_mapping(node):
        """The keys a mapping that flows into doc writes."""
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if k is None:                      # `**x` inside the literal
                    if not is_doc(v):
                        from_mapping(v)
                    continue
                key = key_of(k)
                if key is None:
                    dynamic.append(ast.dump(node))
                else:
                    written.add(key)
        elif is_dict_call(node):
            written.update(k.arg for k in node.keywords if k.arg)
            for value in [a for a in node.args if not is_doc(a)] + \
                    [k.value for k in node.keywords if not k.arg]:
                from_mapping(value)
        elif not (isinstance(node, ast.Call) and copies_doc(node)):
            dynamic.append(ast.dump(node))

    for node in ast.walk(ast.parse(source)):
        value = getattr(node, "value", None)
        targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and is_doc(value):
            dynamic.append("an alias of doc")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and \
                copies_doc(value) and not any(is_doc(t) for t in targets):
            from_mapping(value)
            dynamic.append("a copy of doc, kept as another name")
        elif isinstance(node, ast.Assign) and any(is_doc(t) for t in node.targets):
            from_mapping(node.value)
        elif isinstance(node, ast.AugAssign) and is_doc(node.target):
            from_mapping(node.value)
        elif isinstance(node, ast.Return) and value is not None and (
                isinstance(value, ast.Dict) or copies_doc(value)):
            from_mapping(value)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and \
                is_doc(node.value):
            key = key_of(node.slice)
            if key is None:
                dynamic.append(ast.dump(node.slice))
            else:
                written.add(key)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                is_doc(node.func.value):
            if node.func.attr == "update":
                written.update(k.arg for k in node.keywords if k.arg)
                for arg in list(node.args) + [k.value for k in node.keywords if not k.arg]:
                    from_mapping(arg)
            elif node.func.attr in ("setdefault", "__setitem__"):
                key = key_of(node.args[0]) if node.args else None
                if key is None:
                    dynamic.append(ast.dump(node))
                else:
                    written.add(key)
    return written, dynamic


def tech_lead_conf_errors(conf):
    """The links in the tech lead's answer (KIT-343) and the direct asks (ACT-4). The job checks
    the same; these name each problem by its conf key. The back room's keys went at the cutover
    (KIT-492)."""
    errors = []
    bad = [h for h in link_hosts(conf) if not bridge.LINK_HOST_RE.match(h)]
    if bad:
        errors.append("TECH_LEAD_LINK_HOSTS must list host names like github.com, each of two "
                      "labels or more, comma-separated, got %r" % ", ".join(bad))
    if conf["DIRECT_ASKS"] not in ("yes", "no"):
        errors.append("DIRECT_ASKS must be yes or no, got %r" % conf["DIRECT_ASKS"])
    for key, _name, low, high in DIRECT_NUMBERS:
        if not WHOLE_RE.match(conf[key]) or not low <= int(conf[key]) <= high:
            errors.append("%s must be a whole number from %d to %d, got %r"
                          % (key, low, high, conf[key]))
    if conf["DIRECT_ASKS"] == "yes":
        errors.extend(direct_problems(conf))
    return errors


def link_hosts(conf):
    """The hosts a link in the tech lead's answer may go to and stay a link, as the job reads
    them: lower case, in the order given (KIT-343)."""
    return [h.lower() for h in split_list(conf["TECH_LEAD_LINK_HOSTS"])]


def direct_problems(conf):
    """What DIRECT_ASKS=yes needs and the conf lacks (ACT-4): acting, a DM, and the agent's
    account named, or a ticket it filed could never be told apart."""
    problems = []
    if conf["ACT"] != "yes":
        problems.append("DIRECT_ASKS needs ACT=yes: with acting off, a direct ask would say "
                        "it acts and change nothing")
    if not split_list(conf["DM_USER_IDS"]):
        problems.append("DIRECT_ASKS needs DM_USER_IDS: a direct ask is read only in a "
                        "member's DM")
    if not conf["AGENT_USER_NAME"]:
        problems.append("DIRECT_ASKS needs AGENT_USER_NAME: a ticket the agent's own account "
                        "filed is always asked about")
    return problems


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
        "epic_wip": (int(conf["EPIC_WIP"]) if WHOLE_RE.match(conf["EPIC_WIP"])
                     else conf["EPIC_WIP"]),
        "pings_file": pings_file(conf),
    }
    # Composed only when it is not the job's own default, so an install made before the
    # key existed is still the composed one (KIT-284).
    if split_list(conf["HANDOFF_STATES"]) != bridge.DEFAULT_HANDOFF_STATES:
        doc["handoff_states"] = split_list(conf["HANDOFF_STATES"])
    # KIT-454: the queue, composed only when set, so a config written before it is unchanged.
    if conf["QUEUE_CONFIG"]:
        doc["queue_config"] = conf["QUEUE_CONFIG"]
    if conf["QUEUE_EVERY_SECONDS"] != str(bridge_config.DEFAULT_QUEUE_EVERY_SECONDS):
        every = conf["QUEUE_EVERY_SECONDS"]
        doc["queue_every_seconds"] = int(every) if WHOLE_RE.match(every) else every
    # KIT-299: the same, so a config written before the key existed stays the same.
    if conf["DONE_STATE"] != bridge.DEFAULT_DONE_STATE:
        doc["done_state"] = conf["DONE_STATE"]
    # KIT-340: the same.
    hours = conf["QUESTION_REMINDER_HOURS"]
    hours = int(hours) if WHOLE_RE.match(hours) else hours
    if hours != bridge.DEFAULT_QUESTION_REMINDER_HOURS:
        doc["question_reminder_hours"] = hours
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
                                       if WHOLE_RE.match(conf["INTERVAL_SECONDS"])
                                       else conf["INTERVAL_SECONDS"])
    # KIT-343: the hosts a link in the tech lead's answer may stay a link to, composed only when
    # not the job's own default, so a default install is the same bytes as before.
    if link_hosts(conf) != bridge.DEFAULT_TECH_LEAD_LINK_HOSTS:
        doc["tech_lead_link_hosts"] = link_hosts(conf)
    # KIT-498: the tech lead's turn, composed only with TECH_LEAD_TURN=yes, on by default since
    # the cutover (KIT-492). The tool server's own config is the next file, tools_config_bytes.
    if conf["TECH_LEAD_TURN"] == "yes":
        doc.update(tech_lead_turn=True, turn_bin=conf["TURN_BIN"], turn_model=conf["TURN_MODEL"],
                   turn_token_env=conf["TURN_TOKEN_ENV"],
                   turn_tools_config=conf["TURN_TOOLS_CONFIG"])
    # ACT-4: composed only with DIRECT_ASKS=yes, so a config written before the keys existed
    # stays the same, byte for byte.
    if conf["DIRECT_ASKS"] == "yes":
        def whole(key):
            return int(conf[key]) if WHOLE_RE.match(conf[key]) else conf[key]
        doc["direct_asks"] = True
        doc["direct_grace_seconds"] = whole("DIRECT_GRACE_SECONDS")
        doc["direct_max_tickets"] = whole("DIRECT_MAX_TICKETS")
        doc["direct_max_age_minutes"] = whole("DIRECT_MAX_AGE_MINUTES")
    # KIT-561: the push connection, composed only with PUSH_MODE=yes, so a config written before
    # it existed stays the same, byte for byte.
    if conf["PUSH_MODE"] == "yes":
        doc["push_mode"] = True
        if conf["PUSH_TOKEN_ENV"] != push_setup.DEFAULT_TOKEN_ENV:
            doc["push_token_env"] = conf["PUSH_TOKEN_ENV"]
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


def tools_config(conf):
    """The tool server's config (KIT-498). The tracker key is a FILE's name here, never its
    value; `~` stays literal, and the server expands it as the role account. The kit's guides
    are read from the role account's own clone, and the status file is the stand-up's. The
    outbox is where the plan and replan tools record what they want asked (KIT-520)."""
    doc = {"team_keys": split_list(conf["TEAM_KEYS"]),
           "linear_key_file": conf["TURN_KEY_FILE"],
           "status_file": conf["STANDUP_STATUS_FILE"],
           "docs_dir": conf["ROLE_KIT_CLONE"].rstrip("/") + "/docs",
           "outbox_dir": conf["TURN_OUTBOX_DIR"],
           # KIT-562: what the tracker alone shows, so the server refuses what the bridge would
           # refuse before it records. owner_user_id is always written (it is required here);
           # agent_user_name only when named. A tool server from before these fields refuses the
           # whole config as an unknown field, so the role clone must carry the new server first.
           "owner_user_id": conf["OWNER_USER_ID"]}
    if conf["AGENT_USER_NAME"]:
        doc["agent_user_name"] = conf["AGENT_USER_NAME"]
    return doc


def tools_config_bytes(conf):
    return json.dumps(tools_config(conf), indent=2, sort_keys=True) + "\n"


def manifest(conf):
    return push_setup.manifest_additions(conf, _manifest(conf))


def _manifest(conf):
    return {
        "display_information": {"name": app_name(conf)},
        "features": {"bot_user": {"display_name": app_name(conf), "always_online": False},
                     # KIT-295: the Messages tab, so a member can write to the app in its DM.
                     "app_home": {"home_tab_enabled": False, "messages_tab_enabled": True,
                                  "messages_tab_read_only_enabled": False}},
        "oauth_config": {"scopes": {"bot": sorted(SCOPE_CITES)}},
        "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False,
                     "token_rotation_enabled": False},
    }


def sh_path(value):
    return "$HOME/" + value[2:] if value.startswith("~/") else value


def job_command(conf, command="run"):
    """The job's shell command: the env file sourced, then the bridge, by full path. With
    `command` "lanes", the bridge's HANDOFF_STATES check instead of a pass (KIT-291)."""
    script = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + BRIDGE_SCRIPT
    return ('set -a; . "%s"; set +a; exec /usr/bin/python3 "%s" %s --config "%s"'
            % (sh_path(conf["ENV_FILE"]), sh_path(script), command,
               sh_path(conf["BRIDGE_CONFIG"])))


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
    "CK-B1": ("Create your tech lead's own Slack app", [
        "Print the manifest:  python3 {SELF} manifest --conf {CONF}  (JSON and nothing else; it "
        "needs no BOT_USER_ID yet). If the conf names no tech lead, it first asks once what to "
        "call your tech lead (Return keeps the default) and saves the answer as APP_NAME.",
        "Slack: Create New App -> From a manifest -> paste the manifest `manifest` prints.",
        "Install it to the workspace. Copy its Bot User OAuth Token (it starts xoxb-).",
        "In EACH channel in CHANNEL_IDS, type: /invite @{APP_NAME}",
        "Click the app's name in the channel, open its profile, and Copy member ID. Fill in "
        "the BOT_USER_ID= line the conf already has (do not add a second one: a key set twice "
        "is refused). The job refuses any token that is not this bot's.",
        "Then put the token in place, at a hidden prompt:  python3 {SELF} token --conf {CONF}",
        "Made the app before? Update it instead of lines 2 and 3 (still print the manifest, line 1): open the app's Slack settings "
        "-> App Manifest -> paste the manifest `manifest` prints -> Save Changes -> Reinstall "
        "to Workspace. If its Bot User OAuth Token changed, run line 6 again.",
        "With DM_USER_IDS set: find the app in Slack and open its Messages tab once. That is "
        "your DM with your tech lead. Each member it names does the same.",
        "Its picture, if APP_AVATAR is set (a manifest cannot carry one): open the app's Basic "
        "Information -> Display Information -> App icon, and upload {AVATAR}. Renaming your "
        "tech lead later: change APP_NAME in the conf, then do line 7; Save Changes keeps the token, and "
        "if Slack asks you to reinstall, run line 6 again, because the token may change.",
    ], "token prints the env var name and the token's length, and nothing else.",
       "another app's token, such as the notifier's. Your tech lead needs its own bot identity, "
       "or that app could post a question that looks like your tech lead's."),
    "CK-B2": ("Load the tech lead's job", [
        "sudo launchctl enable system/{JOB_LABEL}",
        "sudo launchctl bootstrap system {PLIST}",
        "Wait one interval, then:  python3 {SELF} verify --conf {CONF}",
    ], "verify says the job is loaded and its heartbeat is fresh.",
       "`Bootstrap failed: 5`. The job is installed turned off, and CK-B3 turns it off "
       "again; until line 1 turns it on, the load fails. If line 1 ran, the job is already "
       "loaded, or was unloaded a moment ago. Run verify: if `loaded` passes, you are done. "
       "If it says launchd still runs the tech lead's job at another interval, run card CK-B3, then "
       "this card again. If it is not loaded, wait ten seconds and run lines 1 and 2 again."),
    "CK-B4": ("Prove the tech lead live (KIT-227)", [
        "Plan from your DM: ask the tech lead to plan an idea that waits in the backlog. Good: "
        "its answer, then the tech lead's own question, Plan this?; after your `yes` the idea is "
        "in Plan it, its history shows your move at once, and the planner's ticket appears "
        "within ten minutes.",
        "Who may answer: on a fresh question, have a second full member, a guest and a bot "
        "each reply `yes`. Good: only the full member's yes acts, and each refusal is said in "
        "the thread.",
        "While the Mac is asleep: ask a question, put the Mac to sleep, reply `yes` from your "
        "phone, then wake it within 24 hours. Good: the tech lead acts on that yes on its first "
        "pass after waking.",
        "Approve a plan: once one is filed, `approve <epic id>`, then `yes`. Good: the epic "
        "moves to Todo, its children start one at a time in order, and each child's session "
        "starts exactly as when you hand a ticket over yourself.",
        "Finish a plan: wait until every piece of an approved plan is merged. Good: within a "
        "few minutes the epic moves to {DONE_STATE} with one comment naming who approved it, "
        "and one wrap-up names each pull request. If its reviews left notes, one question "
        "asks first; after your `yes`, one new ticket lands in the team with no labels, and "
        "nothing starts on it. Record whether the wrap-up called each pull request merged, "
        "and whether the ticket landed in Backlog or Triage: neither is proven (KIT-299).",
        "Answer a stuck session: when a ping says a dev stopped to ask you something, reply "
        "under it. Good: one comment naming you lands in that session's thread, and it "
        "carries on.",
        "Ready to merge: when Stage E concludes a pull request clean or below the threshold, "
        "the bounce driver leaves its ready-to-merge mark, then moves the ticket to Needs "
        "Approval in the same pass, and the notifier pages on the mark. Good: a ping says the "
        "pull request is yours to merge and links it, and the ticket is in Needs Approval. "
        "A ticket moved to Needs Approval by hand pages nothing: that is expected.",
        "What the dispatcher's chat lane can read, while it still runs (once card CK-C6 of "
        "the chat lane has turned it off, skip this step and the next two), with a decoy: run  "
        "sudo -u {ROLE_ACCOUNT} -H sh -c "
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
        "The tech lead's own files, with a decoy: its config {BRIDGE_CONFIG}, its log {LOG}, its "
        "state dir {STATE_DIR} (its questions, the messages it has seen, its heartbeat) and "
        "the notifier's pings file {PINGS}. None holds a credential, and the chat lane "
        "composes no rule for them: its  compose --piece 5 --conf chat-lane.conf  names none "
        "of these paths. Run  sudo -u {ROLE_ACCOUNT} -H sh -c 'echo decoy > {STATE_DECOY}'  "
        "then ask the tech lead to read {STATE_DECOY} . Expected: it is read. Record the "
        "criterion \"the chat lane cannot read the tech lead's files\" as not held for these "
        "files, never as passed: that residual is accepted (KIT-162). Today no session can "
        "write them, which is what keeps them from being forged. Then remove the decoy:  "
        "sudo -u {ROLE_ACCOUNT} -H sh -c 'rm {STATE_DECOY}'",
        "Record each result as a comment on the live-proofs ticket, and update your private "
        "runbook.",
    ], "every step above held, except the tech lead's own files, which are recorded as not held "
       "(KIT-162), and each result is a comment on the live-proofs ticket.",
       "a step taken on trust: mark it not proven instead, with its ticket."),
    "CK-B3": ("Turn the tech lead's job off", [
        "sudo launchctl bootout system/{JOB_LABEL}",
        "sudo launchctl disable system/{JOB_LABEL}",
    ], "`launchctl print system/{JOB_LABEL}` says it could not find the service.",
       "removing the plist alone: launchd keeps a loaded job until it is booted out."),
    "CK-B6": ("Answer devs from your DM, and hear when the notifier stops (KIT-332)", [
        "In notifier.conf, set BRIDGE_PINGS=yes, then:  python3 {NOTIFIER_SETUP} run . The "
        "notifier then records which of its pages wait on an answer.",
        "In this conf, check that NOTIFIER_STATE_DIR is notifier.conf's STATE_DIR, and set "
        "NOTIFIER_JOB_LABEL to notifier.conf's JOB_LABEL. The tech lead reads the notifier's "
        "heartbeat and its plist from those.",
        "Write the config:  python3 {SELF} install --conf {CONF} --apply .",
        "After the notifier's next pass:  python3 {SELF} verify --conf {CONF} .",
        "On the next real question: reply under the message saying a dev stopped to ask "
        "you something, in your DM.",
    ], "verify's pings and notifier rows read ALREADY-DONE, and your reply is sent to the "
       "dev's session.",
       "setting NOTIFIER_JOB_LABEL while the role account's clone has an older copy of the kit. It "
       "refuses the key, and every pass stops, until `python3 scripts/pipeline_install.py` "
       "moves the clone."),
    "CK-B7": ("Let your own clear asks act after a stop window (ACT-4)", [
        "See what it changes, and run its drill:  python3 {SELF} direct-asks --conf {CONF}",
        "Turn it on:  python3 {SELF} direct-asks --conf {CONF} --apply  and type yes.",
        "In your DM, write `plan <ticket id>` for a ticket you want planned. Good: one line "
        "says what it will do in 90 seconds, and ends `Say stop to cancel.`. Write `stop`: "
        "nothing moves. Ask again, and wait: it moves.",
    ], "the drill passes, `direct-asks --apply` says Checked, and your plain ask moves once "
       "the window ends.",
       "REFUSED or BLOCKED: DIRECT_ASKS needs ACT=yes, DM_USER_IDS and AGENT_USER_NAME. Set "
       "them first. To turn it off, set DIRECT_ASKS=no, then install --apply."),
    "CK-B9": ("Turn instant replies on (KIT-561)", [
        "What it does: a second job holds Slack's push connection, so a DM is picked up within "
        "seconds and answered in a thread. The once-a-minute job stays as the catch-up. The conf "
        "must already have TECH_LEAD_TURN=yes.",
        "Run:  python3 {SELF} push --conf {CONF} . It sets PUSH_MODE=yes and PUSH_JOB_LABEL in the "
        "conf itself (the label is JOB_LABEL with -push after it), lists everything it will do, "
        "and asks one yes. Nothing is hand-edited.",
        "After the yes it prints the app's manifest. Only you can do the Slack part: open the "
        "app's settings, App Manifest, paste it, Save Changes, then Reinstall to Workspace. Then, "
        "under Basic Information, App-Level Tokens, make one with the scope connections:write "
        "(it starts xapp-). Slack's agent view, which the manifest turns on, can't be undone once "
        "saved; it is the threaded AI-app style you chose. If the Bot User OAuth Token changed, "
        "run  python3 {SELF} token --conf {CONF}  again.",
        "Paste the app-level token at the script's hidden prompt. It is never printed. It then "
        "opens the connection once as {ROLE_ACCOUNT} and says whether Slack said hello. It ends "
        "by printing the lines that load the always-on job; this installer never loads a job.",
        "Load it:  sudo launchctl enable system/{PUSH_JOB_LABEL}  then  sudo launchctl "
        "bootstrap system {PUSH_PLIST}",
        "Wait a minute, then:  python3 {SELF} verify --conf {CONF} . Its push row passes when the "
        "job is connected and has heard from Slack lately. Run  push  again after a Slack change; "
        "it writes nothing that is already right.",
        "To turn it off: sudo launchctl bootout system/{PUSH_JOB_LABEL}  then  sudo launchctl "
        "disable system/{PUSH_JOB_LABEL} , set PUSH_MODE=no and run  install --apply . The "
        "once-a-minute job answers as before.",
    ], "push says Slack said hello, and verify's push row says connected, with Slack heard from a "
       "moment ago.",
       "the app-level token pasted at the shell's own prompt, into a ticket or into the conf. It "
       "goes only in `push`'s hidden prompt. The bot token (xoxb-) is a different token and does "
       "not open this connection."),
}


def card_values(conf, conf_path):
    # The chat lane's own rule spelling (KIT-227 review): the card names the rules that
    # composer writes for this env file, never a guess at them.
    import pipeline_chat_lane_setup as chat_lane
    conf = conf or dict((k, d or "<%s>" % k) for k, (d, _doc) in CONF_KEYS.items())
    # Where `token` keeps its whole copies of the env file: the writer's own rule, below.
    backups = os.path.dirname(conf["ENV_FILE"]) + "/backups"
    return {"APP_NAME": app_name(conf), "JOB_LABEL": conf["JOB_LABEL"],
            "AVATAR": conf["APP_AVATAR"] or "(none set: skip this line)",
            "ROLE_ACCOUNT": conf["ROLE_ACCOUNT"],
            "PLIST": plist_path(conf), "SELF": os.path.abspath(__file__),
            "NOTIFIER_SETUP": os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "pipeline_notifier_setup.py"),
            "CHAT_LANE_SETUP": os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "pipeline_chat_lane_setup.py"),
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
            "PINGS": pings_file(conf), "DONE_STATE": conf["DONE_STATE"],
            "PUSH_JOB_LABEL": conf["PUSH_JOB_LABEL"] or "<PUSH_JOB_LABEL>",
            "PUSH_PLIST": push_setup.plist_path(conf) if conf["PUSH_JOB_LABEL"]
            else "/Library/LaunchDaemons/<PUSH_JOB_LABEL>.plist"}


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
env_path, cfg_path, beat_path, pings_path, nbeat_path, standup_path = [
    os.path.expanduser(a) for a in sys.argv[1:7]]
dispatcher_path = os.path.expanduser(sys.argv[7]) if len(sys.argv) > 7 and sys.argv[7] else ""
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
if dispatcher_path:
    # KIT-492: the dispatcher's env file, by name only, so verify can say its chat lane is off.
    try:
        names = []
        for line in open(dispatcher_path):
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
            if m and m.group(2).strip().strip("'\"").strip():
                names.append(m.group(1))
        out["dispatcher_env_names"] = sorted(set(names))
    except Exception as exc:
        out["dispatcher_env_error"] = type(exc).__name__
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
                                                           "summary", "awake_clock"))
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
try:
    raw = open(standup_path).read()
    out["standup_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed, out["standup_bad"] = None, "not JSON"
    if isinstance(parsed, dict):
        out["standup"], out["standup_on"] = parsed, parsed.get("on") is True
    elif "standup_bad" not in out:
        out["standup_bad"] = "not a JSON object"
except FileNotFoundError:
    out["standup_missing"] = True
except Exception as exc:
    out["standup_error"] = type(exc).__name__
print(json.dumps(out))
'''

# Writes the token: read from stdin, never argv. Refuses one that another name already holds.
TOKEN_WRITER_PY = r'''
import os, re, sys, time
env_path, name = os.path.expanduser(sys.argv[1]), sys.argv[2]
others = [os.path.expanduser(a) for a in sys.argv[3:] if a]
for other in others:
    if os.path.realpath(other) == os.path.realpath(env_path):
        print("REFUSED: %s is the same file as DISPATCHER_ENV_FILE (%s). The Slack bot token "
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
            print("REFUSED: that token is already in %s under %s. Your tech lead needs its own "
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

# KIT-297: the tech lead's token (the writer's, until the cutover, KIT-492) goes in by the same
# program, in its own words: the same refusals (the dispatcher's env file, a value another name
# already holds there or in DISPATCHER_ENV_FILE), the same backups, mode 600, the value on stdin
# only.
WRITER_TOKEN_WORDS = (
    ("The Slack bot token ", "The tech lead's token "),
    ("Your tech lead needs its own ", "The tech lead needs its own "),
    ("Slack app's token.", "token, from `claude setup-token`."),
    ("the chat bot's. Nothing", "the dispatcher's. Nothing"))
WRITER_TOKEN_WRITER_PY = TOKEN_WRITER_PY
for _bridge_words, _writer_words in WRITER_TOKEN_WORDS:
    WRITER_TOKEN_WRITER_PY = WRITER_TOKEN_WRITER_PY.replace(_bridge_words, _writer_words)
# One more refusal, the token's own (Stage E review of #220): the env file is shared, so
# TURN_TOKEN_ENV may name a credential the bridge's conf does not know, such as the
# notifier's Slack token. A name that already holds anything but a setup-token is never
# overwritten.
_OWN_NAME_PY = r'''
for line in lines:
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
    held = m.group(2).strip().strip("'\"") if m and m.group(1) == name else ""
    if held and not held.startswith("sk-ant-oat"):
        print("REFUSED: %s already holds another credential in %s, not a token from `claude "
              "setup-token`, so it was left as it is. Give TURN_TOKEN_ENV a name of its "
              "own. Nothing was written." % (name, env_path))
        sys.exit(3)
'''
_ANCHOR = "\nholds(lines, env_path, True)\n"
assert WRITER_TOKEN_WRITER_PY.count(_ANCHOR) == 1
WRITER_TOKEN_WRITER_PY = WRITER_TOKEN_WRITER_PY.replace(_ANCHOR, _ANCHOR + _OWN_NAME_PY)

# KIT-561: the app-level token goes in by the same program once more, in its own words: the same
# refusals (the dispatcher's env file, a value another name already holds there or in
# DISPATCHER_ENV_FILE), the same backups, mode 600, the value on stdin only; and a name that
# already holds anything but an app-level token is never overwritten.
PUSH_TOKEN_WORDS = (
    ("The Slack bot token ", "The app-level token "),
    ("Your tech lead needs its own ", "The app-level token is its own "),
    ("Slack app's token.", "credential."),
    ("the chat bot's. Nothing", "the dispatcher's. Nothing"))
PUSH_TOKEN_WRITER_PY = TOKEN_WRITER_PY
for _bridge_words, _push_words in PUSH_TOKEN_WORDS:
    assert PUSH_TOKEN_WRITER_PY.count(_bridge_words) == 1, _bridge_words
    PUSH_TOKEN_WRITER_PY = PUSH_TOKEN_WRITER_PY.replace(_bridge_words, _push_words)
_PUSH_OWN_PY = r'''
for line in lines:
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
    held = m.group(2).strip().strip("'\"") if m and m.group(1) == name else ""
    if held and not held.startswith("xapp-"):
        print("REFUSED: %s already holds another credential in %s, not an app-level token, so it "
              "was left as it is. Give PUSH_TOKEN_ENV a name of its own. Nothing was written."
              % (name, env_path))
        sys.exit(3)
'''
assert PUSH_TOKEN_WRITER_PY.count(_ANCHOR) == 1
PUSH_TOKEN_WRITER_PY = PUSH_TOKEN_WRITER_PY.replace(_ANCHOR, _ANCHOR + _PUSH_OWN_PY)

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


# KIT-498: the tool server's tracker key, as the role account, from the env file's own value
# (sourced, as the job sources it). `check` prints one word: ok, missing, stale, bad-mode (a
# link, another account's, or open to others) or no-env (the env file holds no key). `write`
# makes it ok: written whole, mode 0600, renamed into place, so a link is replaced and never
# written through. The key is never printed, and no word of it is in any output.
KEY_PY = r'''
import os, re, stat, sys
mode, name, path = sys.argv[1], sys.argv[2], os.path.expanduser(sys.argv[3])
value = (os.environ.get(name) or "").strip()
def state():
    if not re.match(r"^[A-Za-z0-9_-]{10,200}$", value):
        return "no-env"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if (stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & 0o077):
        return "bad-mode"
    return "ok" if open(path).read().strip() == value else "stale"
now = state()
if mode == "check":
    print(now)
elif now == "no-env":
    print("no-env")
    sys.exit(3)
elif now == "ok":
    print("unchanged")
else:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = "%s.bridge-setup.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    os.replace(tmp, path)
    print("wrote")
'''


# KIT-520: the tool server's outbox, as the role account. `check` prints `<state> <waiting>`:
# ok (a plain folder of this account that nobody else can use), open (this account's, but others
# can use it), missing, or refused (a link, a file, or another account's: never used, never
# changed). `write` makes a missing one, mode 0700, and puts an open one right; it refuses the
# rest. waiting is how many entries a turn left in it.
OUTBOX_PY = r'''
import os, stat, sys
mode, path = sys.argv[1], os.path.expanduser(sys.argv[2])
def state():
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing", 0
    # lstat: a link is not a directory here, so a link is refused by the first test.
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return "refused", 0
    try:
        waiting = len([n for n in os.listdir(path) if n.endswith(".json")])
    except OSError:
        return "refused", 0
    return ("ok" if not st.st_mode & 0o077 else "open"), waiting
now, waiting = state()
if mode == "check":
    print("%s %d" % (now, waiting))
elif now == "refused":
    print("refused")
    sys.exit(3)
elif now == "ok":
    print("unchanged")
elif now == "missing":
    os.makedirs(path, mode=0o700)
    os.chmod(path, 0o700)
    print("made")
else:
    os.chmod(path, 0o700)
    print("put right")
'''


def outbox_state(conf, runner):
    """(state, waiting): ok, open, missing, refused, or unread, and how many entries are in it."""
    res = runner.as_role(conf["ROLE_ACCOUNT"], py(OUTBOX_PY, "check", conf["TURN_OUTBOX_DIR"]))
    parts = (res.out or "").strip().split()
    if res.ok and len(parts) == 2 and parts[0] in ("ok", "open", "missing", "refused") \
            and parts[1].isdigit():
        return parts[0], int(parts[1])
    return "unread", 0


def key_command(conf, mode):
    """KEY_PY as the job would run it: the env file sourced, then the program."""
    return ('set -a; . "%s"; set +a; %s' % (sh_path(conf["ENV_FILE"]), py(
        KEY_PY, mode, conf["LINEAR_KEY_ENV"], conf["TURN_KEY_FILE"])))


def key_state(conf, runner):
    """ok, missing, stale, bad-mode, no-env, or unread."""
    res = runner.as_role(conf["ROLE_ACCOUNT"], key_command(conf, "check"))
    word = (res.out or "").strip()
    return word if res.ok and word in ("ok", "missing", "stale", "bad-mode", "no-env") else "unread"


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
        notifier_heartbeat(conf), bridge.standup.DEFAULT_SETTINGS_FILE,
        conf["DISPATCHER_ENV_FILE"]))
    if not res.ok:
        return None
    try:
        return json.loads(res.out)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════════════════
# The subcommands
# ══════════════════════════════════════════════════════════════════════════════════════

MANIFEST_STAND_IN_ID = "U000000000"     # only to pass validation: the manifest holds no member ID


def cmd_manifest(conf_path, reader=None, tty=None, err=None):
    """The manifest as JSON and nothing else (KIT-271). BOT_USER_ID is the id of the app this
    manifest makes, so it cannot exist before the manifest is used; a missing one is not an
    error here. Every other conf problem still is, said in full, and no JSON is printed. A conf
    that names no tech lead is asked for its name first, on stderr (KIT-660)."""
    try:
        with open(conf_path, encoding="utf-8") as fh:
            values, errors = parse_conf(fh.read())
    except OSError as exc:
        say("CONF PROBLEMS in %s:" % conf_path)
        say("  - could not read %s: %s" % (conf_path, exc))
        return EX_USAGE
    if not errors:
        rc, values = ask_name(conf_path, values, reader=reader, tty=tty, err=err)
        if rc != EX_OK:
            return rc
    if not values.get("BOT_USER_ID"):
        values["BOT_USER_ID"] = MANIFEST_STAND_IN_ID
    conf, more = validate_conf(values)
    if errors + more:
        say("CONF PROBLEMS in %s:" % conf_path)
        for e in errors + more:
            say("  - %s" % e)
        return EX_USAGE
    say(json.dumps(manifest(conf), indent=2))
    return EX_OK


def cmd_compose(conf, conf_path):
    say("YOUR TECH LEAD — composed from %s. Nothing live was read; nothing was written."
        % conf_path)
    say("")
    say("PIECE 1 — your tech lead's Slack app manifest (its OWN app; card CK-B1):")
    for line in json.dumps(manifest(conf), indent=2).splitlines():
        say("    " + line)
    say("")
    say("  Why each scope (a note for you, NOT part of the manifest: paste only the JSON above, "
        "or run `manifest`):")
    for scope, why in sorted(SCOPE_CITES.items()):
        say("    %-16s %s" % (scope, why))
    if conf["PUSH_MODE"] == "yes":
        say("    %-16s %s" % (push_setup.PUSH_SCOPE, push_setup.PUSH_SCOPE_CITE))
        say("    Socket Mode is on, DM messages are subscribed to, and Slack's agent view is on. "
            "Slack does not let an app go back from the agent view (card CK-B9).")
    say("")
    say("PIECE 2 — the job's config, written by `install --apply` to %s:"
        % conf["BRIDGE_CONFIG"])
    for line in config_bytes(conf).splitlines():
        say("    " + line)
    say("")
    say("PIECE 3 — the job: %s, as %s, every %s seconds. Its command:"
        % (plist_path(conf), conf["ROLE_ACCOUNT"], conf["INTERVAL_SECONDS"]))
    say("    " + job_command(conf))
    if conf["PUSH_MODE"] == "yes":
        say("")
        say("PIECE 3b — the always-on job (PUSH_MODE=yes): %s, as %s, kept alive. Its command:"
            % (push_setup.plist_path(conf), conf["ROLE_ACCOUNT"]))
        say("    " + push_setup.job_command(conf))
    say("")
    say("THE ORDER")
    say("  1. Card CK-B1: print the manifest (`manifest`), create the app, invite it, fill in "
        "BOT_USER_ID, then `token`.")
    say("  2. `install`, then `install --apply`.")
    say("  3. Card CK-B2: load it. Then `verify`.")
    if conf["PUSH_MODE"] == "yes":
        say("  4. Card CK-B9: instant replies. Its Slack steps, then `push`, then load the "
            "always-on job.")
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
                "wrap-up, and asks before it files the review notes left as one new ticket "
                "in the team (KIT-299)." % conf["DONE_STATE"])
        else:
            say("  AGENT_USER_NAME is empty, so every `approve` is refused.")
    else:
        say("ACT=no: a verified yes is recorded and said, and nothing in the tracker changes. "
            "Set ACT=yes and run `install --apply` to let it act.")
    if split_list(conf["DM_USER_IDS"]):
        say("DM_USER_IDS: it also reads and answers in the DM of %s, always as messages of "
            "their own. An app made before needs the manifest again: card CK-B1."
            % ", ".join(split_list(conf["DM_USER_IDS"])))
    if conf["TECH_LEAD_TURN"] == "yes":
        say("TECH_LEAD_TURN=yes: anything else written in those DMs is answered there by one "
            "turn of %s (%s), over the tech lead's tools, on your subscription, with the token "
            "under %s (`writer-token`). What it asks you to do comes as the tech lead's own "
            "question, and only your yes acts."
            % (conf["TURN_BIN"], conf["TURN_MODEL"], conf["TURN_TOKEN_ENV"]))
    else:
        say("TECH_LEAD_TURN=no: anything else written in those DMs gets one line on what the "
            "tech lead can do there.")
    if conf["RETIRED"]:
        # KIT-492: read as nothing. Said here and by verify's old-paths row until taken out.
        say("RETIRED (KIT-492): %s still has %s. Each does nothing since the cutover; take "
            "those lines out." % (conf_path, ", ".join(conf["RETIRED"])))
    return EX_OK


def cmd_token(conf, runner, sudo, reader=None, tty=None):
    found = agent_markers_present()
    if found:
        return refused(found)
    if not (tty if tty is not None else sys.stdin.isatty()):
        say("BLOCKED: `token` asks at a hidden prompt, and this is not a terminal.")
        return EX_BLOCKED
    try:
        sudo.acquire("`token` writes your tech lead's Slack token into %s as %s."
                     % (conf["ENV_FILE"], conf["ROLE_ACCOUNT"]), "token")
    except se.NoPrivilege as exc:
        return no_privilege(exc, "token")
    value = (reader or getpass.getpass)("Your tech lead's Bot User OAuth Token (hidden): ")
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


def cmd_writer_token(conf, runner, sudo, reader=None, tty=None):
    """The tech lead's token, as `token` puts the bridge's (KIT-297, KIT-498): a person at a
    terminal, a hidden prompt, the role account writing it under TURN_TOKEN_ENV, nothing printed
    but its name and length. The name is the writer's, which first needed it (KIT-492)."""
    found = agent_markers_present()
    if found:
        return refused(found)
    if not (tty if tty is not None else sys.stdin.isatty()):
        say("BLOCKED: `writer-token` asks at a hidden prompt, and this is not a terminal.")
        return EX_BLOCKED
    try:
        sudo.acquire("`writer-token` writes the tech lead's token into %s as %s."
                     % (conf["ENV_FILE"], conf["ROLE_ACCOUNT"]), "writer-token")
    except se.NoPrivilege as exc:
        return no_privilege(exc, "writer-token")
    read = reader or getpass.getpass
    value = (read("First run `claude setup-token` in your own terminal, then paste the token "
                  "it prints (hidden). If it wraps onto a second line, paste the first line "
                  "and press Return, and I'll ask for the rest: ") or "").strip()
    lines = 1
    while (WRITER_TOKEN_START_RE.match(value) and len(value) < WRITER_TOKEN_WHOLE_CHARS
           and lines < WRITER_TOKEN_MAX_LINES):
        more = (read("That is %d characters, and a whole token has about %d, so it may have "
                     "wrapped. Paste its next line (hidden), or press Return on an empty line "
                     "if that was all of it: " % (len(value), WRITER_TOKEN_WHOLE_CHARS))
                or "").strip()
        if not more:
            break
        # A fresh first line starts the token again; any other line goes on the end.
        value = more if more.startswith("sk-ant-") else value + more
        lines += 1
    if not WRITER_TOKEN_RE.match(value):
        say("REFUSED: %s a token from `claude setup-token` (it starts sk-ant-oat). An API key "
            "is refused too: it would bill each turn. Nothing was written."
            % ("that is not" if lines == 1 else "the lines you pasted do not make"))
        return EX_USAGE
    if len(value) < WRITER_TOKEN_MIN_CHARS:
        say("REFUSED: that token has %d characters, and a whole one from `claude setup-token` "
            "has about 108, so part of it is missing. Run `writer-token` again, and paste each "
            "line at the hidden prompt that asks for it. Nothing was written." % len(value))
        return EX_USAGE
    runner.dry_run = False
    res = runner.as_role(conf["ROLE_ACCOUNT"],
                         py(WRITER_TOKEN_WRITER_PY, conf["ENV_FILE"], conf["TURN_TOKEN_ENV"],
                            conf["DISPATCHER_ENV_FILE"]),
                         stdin=value + "\n", why="write %s into %s"
                         % (conf["TURN_TOKEN_ENV"], conf["ENV_FILE"]), secret_stdin=True)
    for line in (res.out or "").splitlines():
        say("  " + line)
    if res.rc == 3:
        return EX_REFUSED
    if not res.ok:
        # As `token`: the error is on stderr, and a line shaped like a credential is never
        # shown, since stdin carried the token.
        last = ((res.err or "").strip().splitlines() or ["it printed no error"])[-1]
        if bridge.notify.secret_hits(last):
            last = "its last line is shaped like a credential, so it is not shown"
        say("FAILED: the tech lead's token could not be written into %s, as %s (%s)."
            % (conf["ENV_FILE"], conf["ROLE_ACCOUNT"], last[:200]))
        return EX_FAILED
    # Tried once, straight away (KIT-301): a token Anthropic refuses is said now, with the
    # CLI's reason, not at the first turn that fails. It is tried with the conf's TURN_BIN, which
    # `install --apply` is about to write, not with the config installed before it.
    code = check_new_writer_token(conf, runner)
    if code == EX_FAILED:
        return code                       # its own line said what to fix first
    if conf["TECH_LEAD_TURN"] != "yes":
        say("The tech lead's turn is off. Set TECH_LEAD_TURN=yes in the conf, then "
            "`install --apply` and `verify`.")
    else:
        say("Next: `install --apply`, if the conf changed, then `verify`: its turn row tries "
            "two real turns.")
    return code


def check_new_writer_token(conf, runner):
    """One tool-less call, as the role account, with the token just stored, the conf's TURN_BIN
    and the writer's own probe, which the writer used and the token still has (KIT-301 review;
    KIT-492). FAILED only when the token, or TURN_BIN, is at fault. Anthropic not answering says
    nothing about the token: NOT CHECKED. A sample that only failed a check on its words still
    shows the token works."""
    if not conf["TURN_BIN"]:
        say("NOT CHECKED: TURN_BIN is not set in the conf, so the token can't be tried. Set it "
            "to the claude CLI's full path, then run `writer-token` again, or `verify` after "
            "`install --apply`.")
        return EX_UNKNOWN
    rc, last, said = probe_writer(conf, runner, token_probe_doc(conf))
    why = last[len("fallback: "):] if last.startswith("fallback: ") else ""
    verdict = voice.token_verdict(why) if why else "failed"
    if clone_behind(rc, last):
        # The role account's clone is behind (KIT-301 fix review, KIT-360), which says
        # nothing about the token.
        say("NOT CHECKED: the token is stored, but the role account's copy of the kit is "
            "older than this installer, so its writer can't be tried with these settings. "
            "Bring it up to date with `python3 scripts/pipeline_install.py`, then `verify`.")
        return EX_UNKNOWN
    if rc == 0 and last == "written by the model":
        say("Checked: one sample call went through with the new token.")
        return EX_OK
    if verdict == "works":
        say("Checked: Anthropic took the new token, though a check refused the model's sample "
            "words (%s). The token works." % why[:200])
        return EX_OK
    if verdict == "unasked":
        say("NOT CHECKED: the token is stored, but Anthropic could not be asked (%s). That "
            "says nothing about the token. Run `writer-token` again later, or `verify` after "
            "`install --apply`." % unasked_reason(why)[:200])
        return EX_UNKNOWN
    say("FAILED: the token is stored, but its check says: %s"
        % (probe_detail(last, said)[:300] or "nothing"))
    say(failed_fix(why))
    return EX_FAILED


# A probe older than `--config -` reads the dash as a file name, finds none (errno 2) and
# says this first. A probe that reads stdin never says errno 2 there.
OLD_PROBE_SAYS = "FAIL: could not read --config -:"


def token_probe_doc(conf):
    """What the writer's probe reads on stdin to try the tech lead's token: the conf's TURN_BIN
    and TURN_TOKEN_ENV, in the writer's own key names, which are all it reads (KIT-492)."""
    return {"writer_bin": conf["TURN_BIN"], "writer_token_env": conf["TURN_TOKEN_ENV"]}


def clone_behind(rc, last):
    """True when the role account's copy of the kit is older than this installer: its probe
    predates `--config -` and reads the dash as a file (KIT-301 fix review), or the copy
    predates the writer and has no pipeline_voice.py at all (KIT-360). ROLE_KIT_CLONE is
    always the update's own copy, so either means the update has not moved it yet."""
    if "[Errno 2]" not in last:
        return False
    return ((rc == voice.EXIT_USAGE and last.startswith(OLD_PROBE_SAYS))
            or (rc == 2 and "can't open file" in last and "pipeline_voice.py" in last))


def failed_fix(why):
    """What to do after writer-token's check failed, by its reason (KIT-301 fix review):
    never `install --apply`, which would install a turn that cannot answer."""
    if voice.ORG_OFF in why:
        return ("A new token won't help: an admin of your Claude organization has turned "
                "off Claude subscription access for Claude Code. Ask them to turn it back "
                "on, then run `writer-token` again.")
    if "Anthropic refused the token" in why:
        return ("The token is wrong, expired or revoked. Make a new one with `claude "
                "setup-token`, then run `writer-token` again.")
    if why.startswith("could not start"):
        return ("TURN_BIN is not a claude the role account can run. Fix it in the conf "
                "(`command -v claude` prints the path), then run `writer-token` again.")
    return "Fix what it says, then run `writer-token` again."


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
    if standup_needs_writing(conf, facts):
        todo.append("standup")
    turn_todo, blocked = turn_install_needs(conf, runner)
    todo += turn_todo
    push_text = (push_setup.render_plist(conf, home, _xml_escape, resolve)
                 if conf["PUSH_MODE"] == "yes" else None)
    if push_text is not None:
        push_now = runner.read(["/bin/cat", push_setup.plist_path(conf)])
        if not (push_now.ok and push_now.out == push_text):
            todo.append("push-plist")
    if blocked:
        say(blocked[1])
        return blocked[0]
    if not todo:
        say("Nothing to change: the config, the plist and the stand-up's settings are the "
            "composed ones.")
        print_card("CK-B2", conf)
        return EX_OK
    say("To write: %s." % ", ".join(todo))
    if not apply_it:
        say("DRY RUN: nothing was written. Run it again with --apply.")
        return EX_BLOCKED
    runner.dry_run = False
    if "config" in todo:
        res = runner.as_role(conf["ROLE_ACCOUNT"], py(CONFIG_WRITER_PY, conf["BRIDGE_CONFIG"]),
                             stdin=body, why="write the tech lead's config")
        if not res.ok:
            say("FAILED: the config could not be written (%s)." % (res.err or "").strip()[:200])
            return EX_FAILED
        say("  " + res.out.strip())
    if "standup" in todo:
        failed = write_standup(conf, runner)
        if failed:
            say("FAILED: %s" % failed)
            return EX_FAILED
    if "tools-config" in todo:
        res = runner.as_role(conf["ROLE_ACCOUNT"], py(CONFIG_WRITER_PY, conf["TURN_TOOLS_CONFIG"]),
                             stdin=tools_config_bytes(conf), why="write the tool server's config")
        if not res.ok:
            say("FAILED: the tool server's config could not be written (%s)."
                % shown(res.err)[:200])
            return EX_FAILED
        say("  " + res.out.strip())
    if "turn-key" in todo:
        res = runner.as_role(conf["ROLE_ACCOUNT"], key_command(conf, "write"),
                             why="write the tool server's key file")
        if not res.ok or (res.out or "").strip() not in ("wrote", "unchanged"):
            say("FAILED: the tool server's key file could not be written (%s)."
                % shown(res.err)[:200])
            return EX_FAILED
        say("  the tool server's key file: %s (%s, mode 0600)"
            % ((res.out or "").strip(), conf["TURN_KEY_FILE"]))
    if "outbox" in todo:
        res = runner.as_role(conf["ROLE_ACCOUNT"],
                             py(OUTBOX_PY, "write", conf["TURN_OUTBOX_DIR"]),
                             why="make the tool server's outbox")
        if not res.ok or (res.out or "").strip() not in ("made", "put right", "unchanged"):
            say("FAILED: the tool server's outbox could not be made (%s)."
                % shown(res.err)[:200])
            return EX_FAILED
        say("  the tool server's outbox: %s (%s, mode 0700)"
            % ((res.out or "").strip(), conf["TURN_OUTBOX_DIR"]))
    if "plist" in todo:
        for argv, stdin in ((["/usr/bin/tee", plist_path(conf)], plist),
                            (["/usr/sbin/chown", "root:wheel", plist_path(conf)], None),
                            (["/bin/chmod", "644", plist_path(conf)], None)):
            res = runner.as_root(argv, why="install the tech lead's job", stdin=stdin)
            if not res.ok:
                say("FAILED: %s" % (res.err or "").strip()[:200])
                return EX_FAILED
        say("  wrote %s (installed turned off, not loaded)" % plist_path(conf))
    if "push-plist" in todo:
        failed = write_plist(runner, push_setup.plist_path(conf), push_text,
                             "install the always-on job's plist")
        if failed:
            say("FAILED: %s" % failed)
            return EX_FAILED
        say("  wrote %s (installed turned off, not loaded)" % push_setup.plist_path(conf))
    say("")
    print_card("CK-B2", conf)
    return EX_OK


def shown(text):
    """What a role-side program said on stderr, with a line shaped like a credential never shown."""
    line = ((text or "").strip().splitlines() or [""])[-1]
    if bridge.notify.secret_hits(line):
        return "its last line is shaped like a credential, so it is not shown"
    return line


def turn_install_needs(conf, runner):
    """([what to write], None) or ([], (exit code, words)) for the tech lead's turn (KIT-498):
    the tool server's config, and its key file. Both are composed only with TECH_LEAD_TURN=yes.
    The key file is judged by the role account against the env file's own key, and a key the env
    file does not hold is a stop, said plainly: nothing can be written from it."""
    if conf["TECH_LEAD_TURN"] != "yes":
        return [], None
    todo = []
    now = runner.as_role(conf["ROLE_ACCOUNT"], 'cat "%s"' % sh_path(conf["TURN_TOOLS_CONFIG"]))
    if not (now.ok and now.out == tools_config_bytes(conf)):
        todo.append("tools-config")
    state = key_state(conf, runner)
    if state == "no-env":
        return [], (EX_BLOCKED, "BLOCKED: the env file %s holds no tracker key under %s, so the "
                    "tool server's key file cannot be written from it. Run `token` first."
                    % (conf["ENV_FILE"], conf["LINEAR_KEY_ENV"]))
    if state == "unread":
        return [], (EX_UNKNOWN, "UNKNOWN: the tool server's key file could not be checked as %s."
                    % conf["ROLE_ACCOUNT"])
    if state != "ok":
        todo.append("turn-key")
    ostate, _waiting = outbox_state(conf, runner)
    if ostate == "refused":
        return [], (EX_BLOCKED, "BLOCKED: %s on the role account is a link, a file, a folder "
                    "of another account, or one that can't be read, so I won't use or change "
                    "it. Move it aside or set TURN_OUTBOX_DIR to another folder."
                    % conf["TURN_OUTBOX_DIR"])
    if ostate == "unread":
        return [], (EX_UNKNOWN, "UNKNOWN: the tool server's outbox could not be checked as %s."
                    % conf["ROLE_ACCOUNT"])
    if ostate != "ok":
        todo.append("outbox")
    return todo, None


def drill_command(conf):
    """The direct asks' drill (ACT-4): their own selftest, then the fixed reader's, its 100
    phrasings with them, as the role account runs the job, from the role's own copy of the
    kit. Their selftest checks a config with the DIRECT_* settings through that copy's own
    validator. A copy older than ACT-4 has the reader but no pipeline_bridge_direct.py, and
    its job would refuse those settings and stop every pass, so the drill fails there before
    anything is written (Stage E on #304). Theirs runs first, so python3's words saying the
    file is missing are the drill's last line."""
    scripts = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/"
    return ('/usr/bin/python3 "%s" --selftest && exec /usr/bin/python3 "%s" --selftest'
            % (sh_path(scripts + "pipeline_bridge_direct.py"),
               sh_path(scripts + "pipeline_bridge_requests.py")))


def set_conf_line(text, key, value):
    """The conf's text with KEY=value set: its own line changed in place, or one added."""
    lines, done = text.splitlines(), False
    for n, line in enumerate(lines):
        if line.strip().split("=", 1)[0].strip() == key and not line.strip().startswith("#"):
            lines[n], done = "%s=%s" % (key, value), True
    if not done:
        lines.append("%s=%s" % (key, value))
    return "\n".join(lines) + "\n"


def cmd_direct_asks(conf, conf_path, runner, sudo, apply_it, reader=None):
    """ACT-4's one-yes card: it checks, shows what changes, runs the drill as the role
    account, asks one typed yes, sets DIRECT_ASKS=yes in the conf, writes the job's config
    as `install --apply` does, and reads it back. Refused while ACT=no. Without --apply and
    the yes it changes nothing."""
    found = agent_markers_present()
    if found:
        return refused(found)
    if conf["DIRECT_ASKS"] == "yes":
        say("DIRECT_ASKS is already yes. If verify's config row asks for it, run install "
            "--apply.")
        return EX_OK
    problems = direct_problems(conf)
    if problems:
        say("BLOCKED: nothing was changed.")
        for p in problems:
            say("  - %s." % p)
        return EX_BLOCKED
    resume = "direct-asks --apply" if apply_it else "direct-asks"
    try:
        sudo.acquire("`direct-asks` runs the direct asks' and the fixed reader's selftests as "
                     "the role account%s."
                     % (", and writes the job's config" if apply_it else ""), resume)
    except se.NoPrivilege as exc:
        return no_privilege(exc, resume)
    say("With DIRECT_ASKS=yes, your own DM message that names plan or replan and up to %s "
        "tickets acts %s seconds after I say what I'll do, with no question. Any other "
        "message from you in that DM first stops it. Approve, a note, a `?`, a hedge word, "
        "more tickets than that, and a ticket the tech lead or the agent's account picked "
        "still get a question." % (conf["DIRECT_MAX_TICKETS"], conf["DIRECT_GRACE_SECONDS"]))
    drill = runner.as_role(conf["ROLE_ACCOUNT"], drill_command(conf))
    last = (((drill.out or "").strip() or (drill.err or "").strip()).splitlines() or [""])[-1]
    if bridge.notify.secret_hits(last):
        last = "its last line is shaped like a credential, so it is not shown"
    if drill.rc != 0:
        say("FAILED: the drill, the direct asks' and the fixed reader's selftests in the role "
            "account's copy of the kit, did not pass (%s). Nothing was changed. If that copy "
            "is older than this installer, bring it up to date with `python3 "
            "scripts/pipeline_install.py` first." % (last[:200] or "it printed nothing"))
        return EX_FAILED
    say("Drill: %s" % last[:200])
    if not apply_it:
        say("DRY RUN: nothing was changed. Run it again with --apply, and type yes.")
        return EX_BLOCKED
    answer = (reader or input)("Type yes to turn direct asks on: ")
    if (answer or "").strip().lower() != "yes":
        say("Nothing was changed.")
        return EX_BLOCKED
    try:
        with open(conf_path, encoding="utf-8") as fh:
            text = fh.read()
        with open(conf_path, "w", encoding="utf-8") as fh:
            fh.write(set_conf_line(text, "DIRECT_ASKS", "yes"))
    except OSError as exc:
        say("FAILED: %s could not be written (%s). Nothing was changed." % (conf_path, exc))
        return EX_FAILED
    on = dict(conf, DIRECT_ASKS="yes")
    runner.dry_run = False
    res = runner.as_role(conf["ROLE_ACCOUNT"], py(CONFIG_WRITER_PY, conf["BRIDGE_CONFIG"]),
                         stdin=config_bytes(on), why="write the tech lead's config")
    if not res.ok:
        say("FAILED: DIRECT_ASKS=yes is in %s, but the job's config could not be written "
            "(%s). Run install --apply." % (conf_path, (res.err or "").strip()[:200]))
        return EX_FAILED
    facts = read_facts(runner, on) or {}
    if facts.get("config_sha256") != sha(config_bytes(on)):
        say("UNKNOWN: the config was written, but reading it back did not show it. Run "
            "verify.")
        return EX_UNKNOWN
    say("Checked: direct asks are on in %s and in the job's config. The job's next pass "
        "uses them." % conf_path)
    return EX_OK


def standup_file_problem(facts):
    """Why the stand-up could not use the settings file as it is, or None. A file that is not
    JSON, not an object, or not settings it accepts is never "off" (Stage E on #295)."""
    if facts.get("standup_bad"):
        return facts["standup_bad"]
    if isinstance(facts.get("standup"), dict):
        try:
            bridge.standup.validate_settings(facts["standup"], "it")
        except bridge.standup.StandupError as exc:
            return str(exc)
    return None


def standup_needs_writing(conf, facts):
    """KIT-436: on, the stand-up's settings must be the composed file. Off, a missing file is
    off already, so only one that still says on, or that the stand-up cannot use, is written
    over."""
    if conf["STANDUP"] == "on":
        return facts.get("standup_sha256") != sha(standup_bytes(conf))
    return facts.get("standup_on") is True or bool(standup_file_problem(facts))


def write_standup(conf, runner):
    """Write the stand-up's settings as the role account. None, or what failed."""
    res = runner.as_role(conf["ROLE_ACCOUNT"], py(CONFIG_WRITER_PY,
                                                  bridge.standup.DEFAULT_SETTINGS_FILE),
                         stdin=standup_bytes(conf), why="write the stand-up's settings")
    if not res.ok:
        return "the stand-up's settings could not be written (%s)" % (
            (res.err or "").strip()[:200])
    say("  " + res.out.strip())
    return None


def standup_words(conf):
    days = split_list(conf["STANDUP_DAYS"])
    when = ("Monday to Saturday" if days == bridge.standup.DEFAULTS["days"]
            else ", ".join(days))
    return "on: the morning rundown at %s and the afternoon wrap at %s, %s" % (
        conf["STANDUP_MORNING_AT"], conf["STANDUP_AFTERNOON_AT"], when)


def standup_row(conf, facts):
    """KIT-436: verify's row for the stand-up's settings. Off as the conf has it is SKIPPED,
    and a missing file is off; on, the file must be the composed one."""
    path = bridge.standup.DEFAULT_SETTINGS_FILE
    if facts.get("standup_error"):
        return ("standup", se.UNKNOWN, "%s could not be read (%s)"
                % (path, facts["standup_error"]))
    if standup_needs_writing(conf, facts):
        problem = standup_file_problem(facts)
        return ("standup", se.BLOCKED, "STANDUP=%s, and %s %s: run `standup`" % (
            conf["STANDUP"], path,
            "is not a file the stand-up can use (%s)" % problem[:160] if problem else
            "still says on" if conf["STANDUP"] != "on" else
            "is not written" if facts.get("standup_missing") else "is not the composed one"))
    if conf["STANDUP"] == "on":
        return ("standup", se.ALREADY_DONE, standup_words(conf))
    return ("standup", se.SKIPPED, "off (STANDUP=off)")


def ask_yes(question):
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_standup(conf, runner, sudo, ask=ask_yes):
    """KIT-436: turn the daily stand-up on or off as bridge.conf says, in one go. It checks,
    shows what it will write, asks one yes, writes the settings as the role account, and
    reads them back. Refused inside a Claude session."""
    found = agent_markers_present()
    if found:
        return refused(found)
    try:
        sudo.acquire("`standup` reads the stand-up's settings in the role account's home, and "
                     "writes them after one yes.", "standup")
    except se.NoPrivilege as exc:
        return no_privilege(exc, "standup")
    facts = read_facts(runner, conf)
    if facts is None:
        say("UNKNOWN: the role account's files could not be read.")
        return EX_UNKNOWN
    _name, outcome, detail = standup_row(conf, facts)
    if outcome == se.UNKNOWN:
        say("UNKNOWN: %s" % detail)
        return EX_UNKNOWN
    if outcome != se.BLOCKED:
        say("Nothing to change: the stand-up is %s." % detail)
        return EX_OK
    on = conf["STANDUP"] == "on"
    say("It will write %s as %s, turning the stand-up %s." % (
        bridge.standup.DEFAULT_SETTINGS_FILE, conf["ROLE_ACCOUNT"],
        standup_words(conf) if on else "off"))
    if on:
        say("  It goes to the DM of %s. While it is on, a piece that starts gets no message "
            "of its own: the afternoon wrap lists it." % split_list(conf["DM_USER_IDS"])[0])
    say("  The tech lead reads it on its next pass. Nothing else changes.")
    if not ask("Write it now?"):
        say("Nothing was changed.")
        return EX_BLOCKED
    runner.dry_run = False
    failed = write_standup(conf, runner)
    if failed:
        say("FAILED: %s" % failed)
        return EX_FAILED
    # The proof is the file itself: what is read back must be the very bytes written, on or
    # off. A read-back that read nothing proves nothing (Stage E on #295).
    after = read_facts(runner, conf)
    if after is None or after.get("standup_error"):
        say("UNKNOWN: the stand-up's settings were written, and could not be read back%s. Run "
            "`verify`." % ((" (%s)" % after["standup_error"]) if after else ""))
        return EX_UNKNOWN
    if after.get("standup_sha256") != sha(standup_bytes(conf)):
        say("FAILED: the settings read back are not the ones written. Run `verify`.")
        return EX_FAILED
    say("Good: the stand-up is %s." % (standup_words(conf) if on else "off"))
    return EX_OK


def cmd_verify(conf, runner, sudo, now=None):
    import time
    found = agent_markers_present()
    if found:
        return refused(found)
    try:
        sudo.acquire("`verify` reads the role account's files and runs the tech lead's own dry "
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
        rows.append(standup_row(conf, facts))
        names = facts.get("env_names") or []
        missing = [n for n in (conf["SLACK_TOKEN_ENV"], conf["LINEAR_KEY_ENV"]) if n not in names]
        # KIT-498: with the tech lead's turn on, its token is named there too, put by
        # `writer-token`.
        wants_token = conf["TECH_LEAD_TURN"] == "yes"
        writer_missing = (wants_token and conf["TURN_TOKEN_ENV"] not in names)
        how = "; ".join((["missing by name: %s; run `token`" % ", ".join(missing)]
                         if missing else []) +
                        (["missing by name: %s; run `writer-token`" % conf["TURN_TOKEN_ENV"]]
                         if writer_missing else []))
        rows.append(("credentials", se.UNKNOWN if facts.get("env_error") else
                     (se.BLOCKED if how else se.ALREADY_DONE),
                     "the env file could not be read (%s)" % facts.get("env_error")
                     if facts.get("env_error") else (how or (
                         "all three credentials are named" if wants_token
                         else "both credentials are named"))))
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
        if dry.rc == bridge.EXIT_DECLINED and bridge.LOCK_BUSY_SAYS in last:
            # KIT-270: the job's own pass held the lock for the whole wait. Healthy, not failed.
            rows.append(("dry-run", se.UNKNOWN, "the job's own pass held the lock for the whole "
                         "%d seconds this waited, so the dry run did not run. Run verify again"
                         % bridge.LOCK_WAIT_SECONDS))
        else:
            rows.append(("dry-run", se.ALREADY_DONE if dry.rc == 0 else se.FAILED,
                         last or "the job printed nothing"))
        rows.append(lanes_row(conf, runner))
        rows.append(name_row(conf, runner))
        loaded = runner.as_root(["/bin/launchctl", "print", "system/%s" % conf["JOB_LABEL"]])
        rows.append(loaded_row(conf, loaded))
        beat = facts.get("heartbeat") or {}
        age = awake = None
        try:
            import calendar
            beat_at = calendar.timegm(time.strptime(beat.get("at") or "", "%Y-%m-%dT%H:%M:%SZ"))
            age = (now or time.time()) - beat_at
            # KIT-330: launchd's interval counts no time asleep, so right after the Mac wakes a
            # working job's beat is old by the clock and on time by its schedule. The beat
            # carries the awake clock the job read beside `at`; with it the beat is aged in
            # awake time. A beat with none (an older job's) is judged by the clock, as before.
            awake = hbm.beat_awake_age(beat_at, beat_at, beat.get("awake_clock"),
                                       now or time.time(), hbm._awake_clock(), hbm._boot_wall())
        except (TypeError, ValueError):
            pass
        judged = age if awake is None else awake
        how = ""
        if awake is not None and age - awake >= 60:
            how = (" (%d s by the clock: the Mac slept for %d min of that)"
                   % (int(age), int((age - awake) // 60)))
        fresh = (judged is not None and judged <= 3 * int(conf["INTERVAL_SECONDS"]) + 60
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
                         ("its last pass, %ds ago%s, exited 0"
                          % (int(judged), (" in awake time" + how) if awake is not None
                             else "")) if fresh else
                         ("no heartbeat yet" if age is None else
                          "its last pass was %ss ago%s with exit %s: %s"
                          % (int(judged), (" in awake time" + how) if awake is not None else "",
                             beat.get("exit"), (beat.get("summary") or "")[:160]))))
        rows.append(pings_row(conf, facts))
        rows.append(notifier_row(conf, facts, runner, now or time.time()))
        rows.extend(turn_rows(conf, runner))
        rows.extend(push_rows(conf, runner, facts, home, now or time.time()))
        rows.append(old_paths_row(conf, facts))
    for check, outcome, detail in rows:
        say("  %-13s %-17s %s" % (check, outcome, detail))
    worst = [o for _c, o, _d in rows]
    for outcome, code in ((se.FAILED, EX_FAILED), (se.UNKNOWN, EX_UNKNOWN),
                          (se.BLOCKED, EX_BLOCKED)):
        if outcome in worst:
            return code
    say("Good: your tech lead is installed, loaded and passing.")
    return EX_OK


def write_plist(runner, path, text, why):
    """A plist, written as root, owned by root, readable by all. None, or what failed."""
    for argv, stdin in ((["/usr/bin/tee", path], text),
                        (["/usr/sbin/chown", "root:wheel", path], None),
                        (["/bin/chmod", "644", path], None)):
        res = runner.as_root(argv, why=why, stdin=stdin)
        if not res.ok:
            return (res.err or "").strip()[:200] or "the plist could not be written"
    return None


def push_beat(conf, runner):
    """The always-on job's heartbeat as the role account reads it, or None when that account's files
    could not be asked."""
    res = runner.as_role(conf["ROLE_ACCOUNT"], py(push_setup.BEAT_PY,
                                                  push_setup.heartbeat_path(conf)))
    if not res.ok:
        return None
    try:
        doc = json.loads(res.out)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def push_rows(conf, runner, facts, home, now):
    """KIT-561: verify's rows for instant replies. Off as the conf has it is one row saying so.
    On, the files must be the composed ones and the token named (push-files), and the job's own
    heartbeat must say it is connected and Slack was heard from lately (push): down or too old is
    not a pass, and a heartbeat that cannot be read is UNKNOWN."""
    if conf["PUSH_MODE"] != "yes":
        return [("push", se.SKIPPED, "off (PUSH_MODE=no): the pass answers once a minute")]
    rows = []
    wants = []
    if conf["PUSH_TOKEN_ENV"] not in (facts.get("env_names") or []):
        wants.append("missing by name: %s; run `push`" % conf["PUSH_TOKEN_ENV"])
    now_plist = runner.read(["/bin/cat", push_setup.plist_path(conf)])
    if not (now_plist.ok and now_plist.out == push_setup.render_plist(conf, home, _xml_escape,
                                                                      resolve)):
        wants.append("the always-on job's plist is not the composed one; run `install --apply`")
    rows.append(("push-files", se.UNKNOWN if facts.get("env_error") else
                 (se.BLOCKED if wants else se.ALREADY_DONE),
                 ("the env file could not be read (%s)" % facts.get("env_error"))
                 if facts.get("env_error") else
                 ("; ".join(wants) if wants else "the token is named and the plist is the composed one")))

    def awake_age(at, clock):
        try:
            return hbm.beat_awake_age(at, at, clock, now, hbm._awake_clock(), hbm._boot_wall())
        except (TypeError, ValueError):
            return None
    outcome, detail = push_setup.judge_beat(push_beat(conf, runner), now, awake_age,
                                            bridge.notify.secret_hits)
    rows.append(("push", outcome, detail))
    return rows


def run_probe(conf, runner):
    """(True, line) when the push connection opened once as the role account and Slack said hello;
    (False, line) when it did not; (None, line) when it could not be measured, say because the role
    account's copy of the kit has no probe yet. A line shaped like a credential is never shown."""
    res = runner.as_role(conf["ROLE_ACCOUNT"], push_setup.probe_command(conf))
    said = ((res.out or "").strip() or (res.err or "").strip()).splitlines() or [""]
    last = said[-1]
    if bridge.notify.secret_hits(last):
        last = "its last line is shaped like a credential, so it is not shown: read the job's log"
    if res.rc == 0 and last.startswith("push probe: "):
        return True, last[len("push probe: "):][:200]
    if (not last.startswith("push probe: ")
            and ("can't open file" in (res.err or "") or "usage:" in (res.err or ""))):
        return None, ("the role account's copy of the kit has no push probe: it is older than this "
                      "installer. Move it with `python3 scripts/pipeline_install.py`, then run "
                      "this again")
    return False, last[len("push probe: "):][:200] if last.startswith("push probe: ") else last[:200]


def put_push_token(conf, runner, reader, tty):
    """Ask for the app-level token at a hidden prompt and have the role account write it under
    PUSH_TOKEN_ENV. The caller holds the sudo session. An exit code; nothing of the token is shown."""
    if not (tty if tty is not None else sys.stdin.isatty()):
        say("BLOCKED: the app-level token is asked for at a hidden prompt, and this is not a "
            "terminal.")
        return EX_BLOCKED
    value = ((reader or getpass.getpass)("The app-level token, which starts xapp- (hidden): ")
             or "").strip()
    if not push_setup.APP_TOKEN_RE.match(value):
        say("REFUSED: that is not an app-level token (it should start xapp-). Nothing was "
            "written. Slack makes it in the app's Basic Information, under App-Level Tokens, "
            "with the scope connections:write. The bot token, which starts xoxb-, is another.")
        return EX_USAGE
    runner.dry_run = False
    res = runner.as_role(conf["ROLE_ACCOUNT"],
                         py(PUSH_TOKEN_WRITER_PY, conf["ENV_FILE"], conf["PUSH_TOKEN_ENV"],
                            conf["DISPATCHER_ENV_FILE"]),
                         stdin=value + "\n", why="write %s into %s"
                         % (conf["PUSH_TOKEN_ENV"], conf["ENV_FILE"]), secret_stdin=True)
    for line in (res.out or "").splitlines():
        say("  " + line)
    if res.rc == 3:
        return EX_REFUSED
    if not res.ok:
        say("FAILED: the app-level token could not be written into %s, as %s (%s)."
            % (conf["ENV_FILE"], conf["ROLE_ACCOUNT"], shown(res.err)[:200]))
        return EX_FAILED
    return EX_OK


def cmd_push_token(conf, runner, sudo, reader=None, tty=None):
    """The app-level token, as `token` puts the bot's (KIT-561): a person at a terminal, a hidden
    prompt, the role account writing it, nothing printed but its name and length."""
    found = agent_markers_present()
    if found:
        return refused(found)
    if not (tty if tty is not None else sys.stdin.isatty()):
        say("BLOCKED: `push-token` asks at a hidden prompt, and this is not a terminal.")
        return EX_BLOCKED
    try:
        sudo.acquire("`push-token` writes the app-level token into %s as %s."
                     % (conf["ENV_FILE"], conf["ROLE_ACCOUNT"]), "push-token")
    except se.NoPrivilege as exc:
        return no_privilege(exc, "push-token")
    return put_push_token(conf, runner, reader, tty)


def probe_code(ok):
    return EX_OK if ok else (EX_UNKNOWN if ok is None else EX_FAILED)


def cmd_push_probe(conf, runner, sudo):
    """Open the push connection once, as the role account, and say whether Slack said hello
    (KIT-561). It measures the limits that account's network puts on the job. It changes nothing."""
    found = agent_markers_present()
    if found:
        return refused(found)
    if conf["PUSH_MODE"] != "yes":
        say("REFUSED: PUSH_MODE is no in the conf, so there is no push connection to probe.")
        return EX_USAGE
    try:
        sudo.acquire("`push-probe` opens the push connection once as %s, with the job's config "
                     "and the app-level token. It changes nothing." % conf["ROLE_ACCOUNT"],
                     "push-probe")
    except se.NoPrivilege as exc:
        return no_privilege(exc, "push-probe")
    ok, line = run_probe(conf, runner)
    say("%s: %s" % ("probe" if ok else ("UNKNOWN" if ok is None else "FAILED"), line))
    return probe_code(ok)


PUSH_TODO_WORDS = {
    "set-conf": "set %(sets)s in %(conf_path)s, so the conf says what is installed",
    "config": "write the job's config, with instant replies on, to %(config)s",
    "push-plist": "install the always-on job's plist at %(plist)s",
    "push-token": "print the manifest for you to paste into Slack, then ask for the app-level token at "
                  "a hidden prompt and put it in %(env)s under %(name)s",
}


def push_label(conf):
    """The always-on job's label, derived as the bridge's other jobs' are: the bridge's own label with
    `-push` after it (com.example.bridge gives com.example.bridge-push)."""
    return conf["JOB_LABEL"] + "-push"


def push_job_loaded(conf, runner):
    return runner.as_root(["/bin/launchctl", "print", "system/%s" % conf["PUSH_JOB_LABEL"]]).ok


def push_todo(conf, runner, facts, home, edits=()):
    todo = ["set-conf"] if edits else []
    if facts.get("config_sha256") != sha(config_bytes(conf)):
        todo.append("config")
    now_plist = runner.read(["/bin/cat", push_setup.plist_path(conf)])
    if not (now_plist.ok and now_plist.out == push_setup.render_plist(conf, home, _xml_escape,
                                                                      resolve)):
        todo.append("push-plist")
    if conf["PUSH_TOKEN_ENV"] not in (facts.get("env_names") or []):
        todo.append("push-token")
    return todo


def load_lines(conf, runner, plist_changed):
    """The lines that load the always-on job, which this installer never runs (card CK-B2's rule holds
    for every job it installs): the person does. A plist that changed under a loaded job is booted
    out first, or launchd would keep the old one."""
    label = conf["PUSH_JOB_LABEL"]
    lines = []
    loaded = push_job_loaded(conf, runner)
    if loaded and plist_changed:
        lines.append("sudo launchctl bootout system/%s" % label)
    if not loaded or plist_changed:
        lines += ["sudo launchctl enable system/%s" % label,
                  "sudo launchctl bootstrap system %s" % push_setup.plist_path(conf)]
    return lines


def cmd_push(conf, conf_path, runner, sudo, ask=ask_yes, reader=None, tty=None):
    """KIT-561: switch instant replies on, in one go (card CK-B9). Check, show, one yes, apply,
    verify. It sets PUSH_MODE and PUSH_JOB_LABEL in the conf itself, writes the job's config and the
    always-on job's plist, prints the manifest for the one thing only the owner can do (paste it into
    Slack and reinstall, and make the app-level token), takes the token at a hidden prompt, opens the
    connection once as the role account, and ends by printing the two launchctl lines that load the
    job (this installer never loads a job) and the verify to run after them. Refused inside a Claude
    session."""
    found = agent_markers_present()
    if found:
        return refused(found)
    # CHECK: what the conf needs. The two keys are the script's to set; anything else it cannot.
    try:
        with open(conf_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        say("FAILED: %s could not be read (%s)." % (conf_path, exc))
        return EX_FAILED
    values, _perrs = parse_conf(text)
    edits = []
    if conf["PUSH_MODE"] != "yes":
        edits.append(("PUSH_MODE", "yes"))
    if not conf["PUSH_JOB_LABEL"]:
        edits.append(("PUSH_JOB_LABEL", push_label(conf)))
    conf2, problems = validate_conf(dict(values, **dict(edits)))
    if problems:
        say("REFUSED: instant replies cannot be switched on with this conf. Nothing was changed:")
        for e in problems:
            say("  - %s" % e)
        return EX_USAGE
    try:
        sudo.acquire("`push` reads the role account's files and, after one yes, writes the conf, the "
                     "job's config and plist, puts the app-level token in the env file, opens the "
                     "push connection once as that account, and loads the always-on job.", "push")
    except se.NoPrivilege as exc:
        return no_privilege(exc, "push")
    home = home_of(runner, conf2["ROLE_ACCOUNT"])
    facts = read_facts(runner, conf2) if home else None
    if not home or facts is None or facts.get("env_error"):
        say("UNKNOWN: the role account's files could not be read.")
        return EX_UNKNOWN
    todo = push_todo(conf2, runner, facts, home, edits)
    # SHOW
    say("INSTANT REPLIES (%s)" % conf_path)
    if todo:
        names = {"config": conf2["BRIDGE_CONFIG"], "plist": push_setup.plist_path(conf2),
                 "env": conf2["ENV_FILE"], "name": conf2["PUSH_TOKEN_ENV"],
                 "conf_path": conf_path, "sets": " and ".join("%s=%s" % kv for kv in edits)}
        say("It will, as %s where it writes a file of that account's:" % conf2["ROLE_ACCOUNT"])
        for item in todo:
            say("  - " + PUSH_TODO_WORDS[item] % names)
        say("  - open the push connection once as %s and read what Slack says."
            % conf2["ROLE_ACCOUNT"])
        say("It does not load the job: this installer never loads one. It ends by printing the two "
            "lines that do, and the verify to run after them.")
        say("Slack's own side only you can do: paste the manifest it prints into the app's App "
            "Manifest and reinstall the app, and make the app-level token (Basic Information, "
            "App-Level Tokens, the scope connections:write). The connection it opens tells whether "
            "both were done. Slack's agent view, which the manifest turns on, cannot be undone "
            "once saved; it is the threaded AI-app style you chose.")
        if "push-token" in todo and not (tty if tty is not None else sys.stdin.isatty()):
            say("BLOCKED: the app-level token is asked for at a hidden prompt, and this is not a "
                "terminal. Nothing was changed.")
            return EX_BLOCKED
        # ONE YES
        if not ask("Do it now?"):
            say("Nothing was changed.")
            return EX_BLOCKED
        # APPLY
        runner.dry_run = False
        if "set-conf" in todo:
            try:
                new_text = text
                for key, value in edits:
                    new_text = set_conf_line(new_text, key, value)
                with open(conf_path, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
            except OSError as exc:
                say("FAILED: %s could not be written (%s). Nothing was changed." % (conf_path, exc))
                return EX_FAILED
            say("  set %s in %s" % (" and ".join("%s=%s" % kv for kv in edits), conf_path))
        if "config" in todo:
            res = runner.as_role(conf2["ROLE_ACCOUNT"],
                                 py(CONFIG_WRITER_PY, conf2["BRIDGE_CONFIG"]),
                                 stdin=config_bytes(conf2), why="write the tech lead's config")
            if not res.ok:
                say("FAILED: the config could not be written (%s)." % shown(res.err)[:200])
                return EX_FAILED
            say("  " + res.out.strip())
        if "push-plist" in todo:
            failed = write_plist(runner, push_setup.plist_path(conf2),
                                 push_setup.render_plist(conf2, home, _xml_escape, resolve),
                                 "install the always-on job's plist")
            if failed:
                say("FAILED: %s" % failed)
                return EX_FAILED
            say("  wrote %s" % push_setup.plist_path(conf2))
        if "push-token" in todo:
            say("")
            say("NOW, IN SLACK (only you can): open the app's settings, App Manifest, paste this, "
                "Save Changes, then Reinstall to Workspace. Then, under Basic Information, App-Level "
                "Tokens, make one with the scope connections:write.")
            for line in json.dumps(manifest(conf2), indent=2).splitlines():
                say("    " + line)
            say("")
            code = put_push_token(conf2, runner, reader, tty)
            if code != EX_OK:
                return code
    else:
        say("Nothing to write: the conf, the config, the plist and the app-level token's name are in "
            "place, and the job is loaded.")
    # VERIFY: the files, then the connection itself, then the job.
    facts = read_facts(runner, conf2) or {}
    after = push_todo(conf2, runner, facts, home)
    if after:
        say("FAILED: %s still to do after writing it. Run `verify`." % ", ".join(after))
        return EX_FAILED
    ok, line = run_probe(conf2, runner)
    say("  %-13s %-17s %s" % ("push-probe", se.ALREADY_DONE if ok else (
        se.UNKNOWN if ok is None else se.FAILED), line))
    if not ok:
        say("Not done: %s The job was not loaded." % (
            "the connection could not be measured." if ok is None else
            "the connection did not open. The Slack steps above are the usual cause; the line above "
            "says which."))
        return probe_code(ok)
    say("")
    lines = load_lines(conf2, runner, "push-plist" in todo)
    if lines:
        say("The last step is yours, because this installer never loads a job. Run these, exactly:")
        for line in lines:
            say("    " + line)
    else:
        say("The always-on job is loaded and its plist is the composed one.")
    say("Then run:  python3 %s verify --conf %s" % (os.path.abspath(__file__), conf_path))
    say("Its push row passes when the job is connected and has heard from Slack lately.")
    return EX_OK


def writer_command(conf, from_stdin=False):
    """The writer's probe, as the job would run it (KIT-297): the job's PATH, the env file
    sourced as job_command sources it, Apple's interpreter, and the job's own config, or
    with `from_stdin` the config given on stdin (KIT-301 review). Since the cutover it only
    tries the tech lead's token, from stdin (token_probe_doc, KIT-492)."""
    script = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + VOICE_SCRIPT
    config = "-" if from_stdin else '"%s"' % sh_path(conf["BRIDGE_CONFIG"])
    return ('PATH=%s; export PATH; set -a; . "%s"; set +a; exec /usr/bin/python3 "%s" probe '
            '--config %s' % (shlex.quote(se.DAEMON_PATH), sh_path(conf["ENV_FILE"]),
                             sh_path(script), config))


def probe_writer(conf, runner, doc=None):
    """(exit code, last line, the CLI's words): the writer's probe as the role account, the
    way the job ran it. The last line is the probe's verdict, with the CLI's reason in
    fixed words since KIT-301. With `doc` the probe reads that config, on stdin, instead of
    the installed one (KIT-301 review). The CLI's words arrive with the token cut out, and
    a line shaped like a credential is never shown."""
    if doc is None:
        res = runner.as_role(conf["ROLE_ACCOUNT"], writer_command(conf))
    else:
        res = runner.as_role(conf["ROLE_ACCOUNT"], writer_command(conf, from_stdin=True),
                             stdin=json.dumps(doc) + "\n")
    lines = [line.strip() for line in ((res.out or "").strip() or (res.err or "").strip())
             .splitlines()] or [""]
    last = lines[-1]
    said = next((line[len(voice.CLI_SAID):] for line in lines
                 if line.startswith(voice.CLI_SAID)), "")
    if bridge.notify.secret_hits(last):
        last = "its last line is shaped like a credential, so it is not shown"
    if said and bridge.notify.secret_hits(said):
        said = "(not shown: it is shaped like a credential)"
    return res.rc, last, said


# A fallback that names no reason (KIT-301 review): the CLI's own words, already cut and
# scanned, are the only clue, so they are shown beside it.
UNNAMED_FALLBACK_RE = re.compile(r"^fallback: the writer (?:exited -?\d+|reported an error)$")


def probe_detail(last, said):
    """The probe's verdict line, with what the CLI said when the line names no reason."""
    if said and UNNAMED_FALLBACK_RE.match(last):
        return "%s (the claude CLI said: %s)" % (last, said)
    return last


def unasked_reason(why):
    """A fallback's fixed reason alone: "a usage limit was reached", not the exit code."""
    head, _sep, reason = why.partition(": ")
    return reason if head.startswith(("the writer exited", "the writer reported")) else why


def turn_command(conf):
    """The turn's probe, as the job would run it (KIT-498): the job's PATH, the env file sourced,
    Apple's interpreter, and the job's own config. A role account's copy of the kit that lacks
    the turn, its tool server or the reach tool (the changes of #353, #354 and #355) is said
    first, in one line, and nothing is tried."""
    turn_py = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + TURN_SCRIPT
    tools_py = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + TOOLS_SCRIPT
    turn_py, tools_py = sh_path(turn_py), sh_path(tools_py)
    return ('PATH=%s; export PATH; set -a; . "%s"; set +a; if test -f "%s" && test -f "%s" '
            '&& grep -q "def do_reach" "%s"; then exec /usr/bin/python3 "%s" probe --config '
            '"%s"; else echo "behind: the role account\'s copy of the kit lacks the tech '
            'lead\'s turn or its tool server"; exit 4; fi'
            % (shlex.quote(se.DAEMON_PATH), sh_path(conf["ENV_FILE"]), turn_py, tools_py,
               tools_py, turn_py, sh_path(conf["BRIDGE_CONFIG"])))


TURN_BEHIND = ("the role account's copy of the kit is older than this installer: it lacks the tech "
               "lead's turn or its tool server, which came in with the changes merged in #353, #354 "
               "and #355. Nothing was tried. Bring it up to date with the after-merges script, "
               "`python3 scripts/pipeline_install.py`, then run `verify` again")


def turn_rows(conf, runner):
    """KIT-498: the tech lead's turn, as three rows. Off, one row says so. On: the tool server's
    config is the composed one, its key file holds the tracker key, and the probe ran two real
    turns as the role account: the tool server answered, the conversation resumed, and a Read
    outside the allows was refused. Anthropic not answering is UNKNOWN, never OK or FAILED
    (contract section 13)."""
    if conf["TECH_LEAD_TURN"] != "yes":
        return [("turn", se.SKIPPED, "off: TECH_LEAD_TURN is no, so a DM message gets the one "
                 "line on what the tech lead can do")]
    rows = []
    now = runner.as_role(conf["ROLE_ACCOUNT"], 'cat "%s"' % sh_path(conf["TURN_TOOLS_CONFIG"]))
    composed = now.ok and now.out == tools_config_bytes(conf)
    rows.append(("tools-config", se.ALREADY_DONE if composed else se.BLOCKED,
                 "the tool server's config is the composed one" if composed
                 else "run `install --apply`"))
    state = key_state(conf, runner)
    words = {"ok": "the tool server's key file holds the tracker key, mode 0600",
             "no-env": "the env file holds no tracker key under %s: run `token`"
                       % conf["LINEAR_KEY_ENV"],
             "unread": "the key file could not be checked as %s" % conf["ROLE_ACCOUNT"],
             "missing": "the tool server's key file is not there: run `install --apply`",
             "stale": "the tool server's key file is not the env file's key: run "
                      "`install --apply`",
             "bad-mode": "the tool server's key file is a link, another account's, or open to "
                         "others: run `install --apply`"}
    rows.append(("turn-key", {"ok": se.ALREADY_DONE, "unread": se.UNKNOWN}.get(state, se.BLOCKED),
                 words[state]))
    ostate, waiting = outbox_state(conf, runner)
    owords = {"ok": "the tool server's outbox is a plain folder of %s, mode 0700, and holds no "
                    "entry a turn left" % conf["ROLE_ACCOUNT"],
              "open": "the tool server's outbox can be used by others: run `install --apply`",
              "missing": "the tool server's outbox is not there: run `install --apply`",
              "refused": "the tool server's outbox is a link, a file, a folder of another "
                         "account, or one that can't be read: move it aside, or set "
                         "TURN_OUTBOX_DIR to another folder",
              "unread": "the outbox could not be checked as %s" % conf["ROLE_ACCOUNT"]}
    if ostate == "ok" and waiting:
        rows.append(("outbox", se.BLOCKED, "the tool server's outbox holds %d entr%s a turn "
                     "left: the tech lead reads and empties it at its next turn, so run `verify` "
                     "again after one" % (waiting, "y" if waiting == 1 else "ies")))
    else:
        rows.append(("outbox", {"ok": se.ALREADY_DONE, "unread": se.UNKNOWN}.get(
            ostate, se.BLOCKED), owords[ostate]))
    res = runner.as_role(conf["ROLE_ACCOUNT"], turn_command(conf))
    lines = [line.strip() for line in ((res.out or "").strip() or (res.err or "").strip())
             .splitlines()] or [""]
    last = lines[-1]
    if bridge.notify.secret_hits(last):
        last = "its last line is shaped like a credential, so it is not shown"
    if res.rc == 4 and last.startswith("behind:"):
        rows.append(("turn", se.UNKNOWN, TURN_BEHIND))
    elif res.rc == 0 and last in (turn.PROBE_OK, turn.PROBE_OK_REPORTED):
        rows.append(("turn", se.ALREADY_DONE, "%s. It used a `claude setup-token` token: an API "
                     "key is never sent" % last[len("tech lead turn: "):]))
    elif last == "fallback: the turn is off":
        rows.append(("turn", se.BLOCKED, "the job's config does not switch the turn on yet: run "
                     "`install --apply`"))
    elif last.startswith("fallback: "):
        why = last[len("fallback: "):]
        if why.startswith("the turn took longer"):
            # A turn that did not finish may be Anthropic, the network, or a tool server that
            # hung: it is not proven either way, so it is not called a failure or a pass.
            rows.append(("turn", se.UNKNOWN, "the turn did not finish in time (%s): Anthropic, "
                         "the network or the tool server may be slow, so it is not proven. Run "
                         "`verify` again" % why[:160]))
        elif why in voice.UNASKED:
            rows.append(("turn", se.UNKNOWN, "Anthropic could not be asked (%s), so the turn "
                         "was not tried" % why[:160]))
        else:
            rows.append(("turn", se.FAILED, why[:300]))
    else:
        rows.append(("turn", se.FAILED, last[:300] or "the probe printed nothing"))
    return rows


def old_paths_row(conf, facts):
    """KIT-492: whether the old chain is off. bridge.conf holds none of its retired lines, and,
    with DISPATCHER_ENV_FILE set, the dispatcher's env file holds none of its chat app's names,
    by name only: while it does, the dispatcher's chat lane can still answer in Slack. A file
    that could not be read, or DISPATCHER_ENV_FILE unset, is UNKNOWN, never off: a chat lane
    not checked is not a chat lane that is off (contract section 13; Stage E on #494). The
    job's config is the `config` row's: a composed one holds no retired key."""
    said = []
    if conf.get("RETIRED"):
        said.append("bridge.conf still has %s: each does nothing since the cutover; take those "
                    "lines out" % ", ".join(conf["RETIRED"]))
    if not conf["DISPATCHER_ENV_FILE"]:
        return ("old-paths", se.UNKNOWN, "the dispatcher's chat lane was not checked: "
                "DISPATCHER_ENV_FILE is not set. Set it to the dispatcher's env file, so verify "
                "can tell whether the chat lane is off%s"
                % (("; " + "; ".join(said)) if said else ""))
    if facts.get("dispatcher_env_error"):
        return ("old-paths", se.UNKNOWN, "the dispatcher's env file %s could not be read (%s)%s"
                % (conf["DISPATCHER_ENV_FILE"], facts["dispatcher_env_error"],
                   ("; " + "; ".join(said)) if said else ""))
    held = [n for n in CHAT_LANE_ENV_NAMES if n in (facts.get("dispatcher_env_names") or [])]
    if held:
        said.append("the dispatcher's env file still names %s, so its chat lane can still "
                    "answer in Slack: turn it off with the chat lane's card CK-C6"
                    % ", ".join(held))
    if said:
        return ("old-paths", se.BLOCKED, "; ".join(said))
    return ("old-paths", se.ALREADY_DONE, "bridge.conf has no retired line; the dispatcher's "
            "chat lane is off: its env file names no chat app")


# A copy of the bridge from before its `lanes` command: argparse refuses the word (KIT-291).
OLD_LANES_SAYS = "invalid choice: 'lanes'"
OLD_NAME_SAYS = "invalid choice: 'name'"


def name_row(conf, runner):
    """KIT-477: what Slack calls the app, read back by the bridge's own `name` command as the
    role account (it holds the token), against the name the conf asks for. A different name is
    BLOCKED on the person, who edits the manifest; a name that could not be read, or a copy of
    the kit from before the command, is UNKNOWN, never OK (contract section 13). A line shaped
    like a credential is never shown."""
    want = app_name(conf)
    res = runner.as_role(conf["ROLE_ACCOUNT"], job_command(conf, "name"))
    last = (((res.out or "").strip() or (res.err or "").strip()).splitlines() or [""])[-1].strip()
    if bridge.notify.secret_hits(last):
        return ("app-name", se.UNKNOWN, "its last line is shaped like a credential, so it is "
                "not shown: read it as the role account, with `pipeline_bridge.py name`")
    if res.rc == 0 and last.startswith(bridge.NAME_OK):
        try:
            got = json.loads(last[len(bridge.NAME_OK):])
        except ValueError:
            got = None
        if got is None or not isinstance(got, str):
            return ("app-name", se.UNKNOWN, "Slack's answer could not be read")
        if got == want:
            return ("app-name", se.ALREADY_DONE, "Slack calls the app %r, as the conf says" % got)
        return ("app-name", se.BLOCKED, "Slack calls the app %r; the conf says %r. Paste the "
                "manifest again to match (card CK-B1), or change the name in the conf: "
                "APP_NAME, or PROJECT_NAME when it is set" % (got[:80], want))
    if res.rc == 2 and OLD_NAME_SAYS in last:
        return ("app-name", se.UNKNOWN, "the role account's copy of the kit is older than this "
                "installer, so the name was not read. Bring it up to date with "
                "`python3 scripts/pipeline_install.py`")
    why = last[len(bridge.NAME_UNKNOWN):] if last.startswith(bridge.NAME_UNKNOWN) else last
    return ("app-name", se.UNKNOWN, "the name was not read: %s"
            % (why.strip()[:300] or "the check printed nothing"))


def lanes_row(conf, runner):
    """KIT-291: each HANDOFF_STATES name on each TEAM_KEYS team, as the bridge's own
    `lanes` check reads the board: as the role account, with the job's key and its
    installed config. A name a team lacks is BLOCKED, naming both. A board it could not
    read, or a copy of the kit from before the check, is UNKNOWN, never OK (contract §13). A
    line shaped like a credential is never shown."""
    res = runner.as_role(conf["ROLE_ACCOUNT"], job_command(conf, "lanes"))
    last = (((res.out or "").strip() or (res.err or "").strip()).splitlines() or [""])[-1]
    last = last.strip()
    if bridge.notify.secret_hits(last):
        return ("lanes", se.UNKNOWN, "its last line is shaped like a credential, so it is not "
                "shown: read it as the role account, with `pipeline_bridge.py lanes`")
    if res.rc == 0 and last.startswith(bridge.LANES_OK):
        return ("lanes", se.ALREADY_DONE, last[len(bridge.LANES_OK):].strip()[:300])
    if res.rc == bridge.EXIT_DECLINED and last.startswith(bridge.LANES_MISSING):
        return ("lanes", se.BLOCKED, "%s. Fix HANDOFF_STATES in bridge.conf (Step 9), then "
                "run `install --apply`" % last[len(bridge.LANES_MISSING):].strip()[:300])
    if res.rc == 2 and OLD_LANES_SAYS in last:
        return ("lanes", se.UNKNOWN, "the role account's copy of the kit is older than this "
                "installer, so HANDOFF_STATES was not checked. Bring it up to date with "
                "`python3 scripts/pipeline_install.py`")
    why = last[len(bridge.LANES_UNKNOWN):] if last.startswith(bridge.LANES_UNKNOWN) else last
    return ("lanes", se.UNKNOWN, "HANDOFF_STATES was not checked: %s"
            % (why.strip()[:300] or "the check printed nothing"))


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
        return ("loaded", se.BLOCKED, "launchd still runs the tech lead's job every %s s, not every %d "
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
        return ("notifier", se.SKIPPED, "off: NOTIFIER_JOB_LABEL is empty, so the tech lead "
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
                    "its plist says %d s. The tech lead judges it by the plist, so until the "
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
                    "often for the tech lead to tell the Mac's sleep from a quiet notifier: the "
                    "tech lead's job runs once every %d s at the fastest. Set INTERVAL_SECONDS in "
                    "notifier.conf to %d or more, then run `python3 "
                    "scripts/pipeline_notifier_setup.py run`" % (interval, INTERVAL_MIN, need))
        if int(conf["INTERVAL_SECONDS"]) > slowest:
            return ("notifier", se.BLOCKED, "watched, but the tech lead's job runs every %s s, too "
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
    NOTIFIER_BOT_USER_ID set the owner has turned answering on, so a missing file blocks.
    So it does with DM_USER_IDS set (KIT-332): the DM is then where the owner answers a
    dev, and without the file every such answer is refused."""
    path = pings_file(conf)
    if facts.get("pings_error"):
        return ("pings", se.UNKNOWN, "%s could not be read (%s)" % (path, facts["pings_error"]))
    if facts.get("pings_missing"):
        answering = conf["NOTIFIER_BOT_USER_ID"] or split_list(conf["DM_USER_IDS"])
        return ("pings", se.BLOCKED if answering else se.SKIPPED,
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

# A channel-only bridge, the tech lead's turn off: the cases that need the turn, which is on by
# default since the cutover (KIT-492), turn it on themselves.
GOOD_CONF = """
ROLE_ACCOUNT=_exdispatch
ROLE_KIT_CLONE=~/.stage-e/kit
CHANNEL_IDS=C0000000AA, C0000000BB
OWNER_USER_ID=00000000-0000-4000-8000-000000000001
TEAM_KEYS=PROD
JOB_LABEL=com.example.bridge
BOT_USER_ID=U0BRIDGE01
DISPATCHER_ENV_FILE=~/dispatcher.env
TECH_LEAD_TURN=no
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
                 job_out="bridge: 0 message(s) examined (dry run)\n", job_err="", prints=None,
                 probe_rc=0, probe_out="A sample.\nwritten by the model\n", turn_rc=0,
                 turn_out="", turn_real=False, lanes_rc=0,
                 lanes_out="lanes: ok: every HANDOFF_STATES name (Needs Approval) is a state "
                           "on every team (PROD)\n", lanes_err="", name_rc=0,
                 name_out='name: Slack calls the app: "Tech lead"\n', name_err="",
                 push_rc=0, push_out="push probe: Slack said hello after 0.4 seconds\n",
                 push_err=""):
        se.Runner.__init__(self)
        self.home, self.root, self.loaded, self.job_rc = home, root, loaded, job_rc
        self.job_out, self.job_err = job_out, job_err
        self.prints = prints or {}
        self.stdins = []
        # The writer's probe (KIT-297): answered as told, never run, so no CLI starts here.
        self.probe_rc, self.probe_out, self.probes = probe_rc, probe_out, []
        # The direct asks' drill (ACT-4): answered as told, and counted. A role copy without
        # a file the drill names answers as python3 does there (Stage E on #304).
        self.drill_rc, self.drills, self.drill_missing = 0, [], None
        self.probe_stdins = []
        # The tech lead's turn's probe (KIT-498): answered as told, never run, so no CLI starts
        # here. With turn_real, its shell runs for real, to show a role copy that lacks the turn.
        self.turn_rc, self.turn_out, self.turn_real, self.turns = turn_rc, turn_out, turn_real, []
        # The bridge's lanes check (KIT-291): answered as told, never run.
        self.lanes_rc, self.lanes_out, self.lanes_err = lanes_rc, lanes_out, lanes_err
        self.lanes_calls = []
        # The bridge's name check (KIT-477): answered as told, never run.
        self.name_rc, self.name_out, self.name_err = name_rc, name_out, name_err
        self.name_calls = []
        # The push job's probe (KIT-561): answered as told, never run, so no connection opens here.
        self.push_rc, self.push_out, self.push_err, self.push_probes = (push_rc, push_out,
                                                                        push_err, [])
        self.launchctl = []

    def _exec(self, argv, stdin, timeout, cwd=None):
        import subprocess
        self.stdins.append(stdin)
        if argv[:2] == ["sudo", "-u"]:
            script = argv[-1]
            if "pipeline_bridge.py" in script and "--dry-run" in script:
                return se.Result(self.job_rc, self.job_out, self.job_err)
            if "pipeline_bridge.py\" name --config" in script:
                self.name_calls.append(script)
                return se.Result(self.name_rc, self.name_out, self.name_err)
            if "pipeline_bridge.py\" lanes --config" in script:
                self.lanes_calls.append(script)
                return se.Result(self.lanes_rc, self.lanes_out, self.lanes_err)
            if "--selftest" in script and ("pipeline_bridge_requests.py" in script
                                           or "pipeline_bridge_direct.py" in script):
                self.drills.append(script)
                if self.drill_missing and self.drill_missing in script:
                    # A selftest named before the missing file ran, and said so first.
                    first = script.find("pipeline_bridge_requests.py")
                    ran = -1 < first < script.index(self.drill_missing)
                    return se.Result(2, "OK: pipeline_bridge_requests selftest (35 cases)\n"
                                     if ran else "", "/usr/bin/python3: can't open file "
                                     "'/h/.stage-e/kit/scripts/%s': [Errno 2] No such file or "
                                     "directory\n" % self.drill_missing)
                return se.Result(self.drill_rc, "OK: pipeline_bridge_requests selftest (35 "
                                 "cases)\n" if self.drill_rc == 0 else "", "")
            if ("pipeline_tech_lead_turn.py" in script and " probe " in script
                    and not self.turn_real):
                self.turns.append(script)
                return se.Result(self.turn_rc, self.turn_out, "")
            if "pipeline_bridge_push.py" in script and " probe " in script:
                self.push_probes.append(script)
                return se.Result(self.push_rc, self.push_out, self.push_err)
            if "pipeline_voice.py" in script and " probe " in script:
                self.probes.append(script)
                self.probe_stdins.append(stdin)
                return se.Result(self.probe_rc, self.probe_out, "")
            p = subprocess.run(["/bin/sh", "-c", script], input=stdin, capture_output=True,
                               text=True, env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
            return se.Result(p.returncode, p.stdout, p.stderr)
        if argv[:1] == ["sudo"]:
            argv = argv[1:]
        if argv[:1] == ["/bin/launchctl"] and argv[1] in ("enable", "bootstrap", "bootout"):
            self.launchctl.append((argv[1], argv[-1]))
            label = (argv[-1].split("/")[-1][:-len(".plist")] if argv[1] == "bootstrap"
                     else argv[-1].split("/", 1)[-1])
            if argv[1] == "bootstrap":
                self.prints[label] = "\tstate = running\n"
            elif argv[1] == "bootout":
                self.prints.pop(label, None)
            return se.Result(0, "", "")
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

    def parse_ok(text):
        try:
            json.loads(text)
            return True
        except ValueError:
            return False

    def says_bridge(text):
        """KIT-660: each word of `text` that says the component's name to a person. A word
        holding / . _ = - or a brace is a file, a key or a label, and may say it."""
        hits = []
        for word in text.split():
            bare_word = word.strip(".,;:()`\"'[]!?")
            if re.search(r"[/._=\-{}<>]", bare_word):
                continue
            if bare_word.lower().startswith("bridge"):
                hits.append(word)
        return hits

    def outcome(fn):
        """One case run alone, so one that raises fails as itself."""
        try:
            return fn()
        except Exception as exc:                                  # noqa: BLE001
            return "raised %s: %s" % (type(exc).__name__, exc)

    saved = dict(os.environ)
    for m in AGENT_ENV_MARKERS:
        os.environ.pop(m, None)
    saved_stdin = sys.stdin
    sys.stdin = io.StringIO("")     # KIT-660: no case ever reads a person's real terminal
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
        # KIT-454: the queue is composed only when set; a path or interval of the wrong
        # shape is refused, named.
        queued, _e = validate_conf(dict(values, QUEUE_CONFIG="~/.stage-e/queue/queue.json",
                                        QUEUE_EVERY_SECONDS="600"))
        check("config-carries-the-queue-only-when-set",
              ([compose_config(queued).get(k) for k in ("queue_config", "queue_every_seconds")],
               [k for k in ("queue_config", "queue_every_seconds")
                if k in compose_config(validate_conf(dict(values))[0])],
               [any(name in e for e in validate_conf(dict(values, **{name: bad}))[1])
                for name, bad in (("QUEUE_CONFIG", "queue.json"),
                                  ("QUEUE_EVERY_SECONDS", "30"))]),
              (["~/.stage-e/queue/queue.json", 600], [], [True, True]))
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
              [any("NOTIFIER_BOT_USER_ID" in e and "'notifier_bot_user_id'" not in e
                   for e in validate_conf(dict(values, NOTIFIER_BOT_USER_ID=v))[1])
               for v in ("notifier", "U0BRIDGE01")], [True, True])
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
        # The job's own check refuses it, and the line names the bridge.conf keys (KIT-292).
        _ho = [e for e in validate_conf(dict(values, HANDOFF_STATES="Todo"))[1]
               if "HANDOFF_STATES" in e]
        check("conf-HANDOFF_STATES-naming-READY_STATE-is-refused-in-one-line",
              (len(_ho), bool(_ho) and _ho[0].startswith("HANDOFF_STATES names Todo"),
               bool(_ho) and "READY_STATE" in _ho[0] and "PLAN_IT_STATE" in _ho[0],
               [e for e in _ho if "handoff_states" in e or "ready_state" in e]),
              (1, True, True, []))
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
        for needle in ("JOB_LABEL", "INTERVAL_SECONDS", "CHANNEL_IDS must be", "chat bot"):
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
                    if e.startswith("NOTIFIER_STATE_DIR (") and "inside a git working tree"
                    in e] != []), (["NOTIFIER_STATE_DIR"], True))
        # KIT-292: every key the job's config can hold, as composed with every option on,
        # comes from a bridge.conf key this file names, so the job's refusal of it names
        # the line to change. A new composed key with no entry fails here.
        everything, _ev = validate_conf(dict(
            values, DM_USER_IDS="U0ALICE001", NOTIFIER_JOB_LABEL="com.example.notifier",
            TECH_LEAD_TURN="yes", TURN_BIN="/usr/local/bin/claude",
            TECH_LEAD_LINK_HOSTS="github.com", HANDOFF_STATES="Ready to merge",
            DONE_STATE="Shipped", NOTIFIER_BOT_USER_ID="U0NOTIFY01", ACT="yes",
            AGENT_USER_NAME="Agent", DIRECT_ASKS="yes", QUEUE_CONFIG="~/q.json",
            QUESTION_REMINDER_HOURS="5"))
        composed_keys = sorted(compose_config(everything))
        sources = [JOB_KEY_FROM.get(k, "missing") for k in composed_keys]
        check("kit292-every-composed-key-names-its-bridge-conf-key",
              ([k for k, v in zip(composed_keys, sources) if v == "missing"],
               [v for v in sources for one in ((v,) if isinstance(v, str) else v or ())
                if one != "missing" and one not in CONF_KEYS],
               [k for k in JOB_KEY_FROM if k not in bridge.CONFIG_KEYS],
               len(composed_keys) > 25), ([], [], [], True))
        check("kit292-each-composed-key-is-said-as-its-conf-key",
              [k for k, v in JOB_KEY_FROM.items() if isinstance(v, str)
               and in_conf_terms("config '%s' must be x" % k) != "%s must be x" % v], [])
        # KIT-492: a retired key is no key of the job's any more, so a refusal naming one says so.
        check("kit292-a-retired-key-is-the-jobs-own-no-conf-key-names-it",
              in_conf_terms("config 'back_room_channel_id' must be one of 'channel_ids'"),
              "'back_room_channel_id' must be one of CHANNEL_IDS")
        # The value the job echoes after `got` stays the value, though it reads like a key.
        check("kit292-an-echoed-value-is-never-renamed",
              (in_conf_terms("config 'allowed_slack_users' must be a list of Slack user ids "
                             "like U0123456789, got ['act']"),
               in_conf_terms("config 'plan_it_state' must be a state name, got 'schema'")),
              ("ALLOWED_SLACK_USERS must be a list of Slack user ids like U0123456789, got "
               "['act']", "PLAN_IT_STATE must be a state name, got 'schema'"))
        # A backstop to the walk above: every key compose_config's own text writes, under
        # any condition, has its entry (Stage E's review).
        import inspect
        import textwrap
        written, dynamic = doc_writes(textwrap.dedent(inspect.getsource(compose_config)))
        # Each spelling that can write a key into `doc` is seen: a name it can't read counts
        # as dynamic, a constant as written (final delta review of KIT-292).
        spellings = [doc_writes("def f(conf, k):\n    doc = {}\n    %s\n" % line)
                     for line in ("doc.setdefault(k, True)", "doc.update({k: True})",
                                  "doc.update(dict(sneaky_key=True))",
                                  "doc.update([('sneaky_key', True)])",
                                  "alias = doc\n    alias['sneaky_key'] = True",
                                  "doc.__setitem__(k, True)", "doc |= {'sneaky_key': True}",
                                  "doc.update(**{'sneaky_key': True})",
                                  "extra = {'sneaky_key': True}\n    doc.update(**extra)",
                                  "return {**doc, 'sneaky_key': True}",
                                  "doc.update(**extra_of(conf))",
                                  "return dict(doc, sneaky_key=True)",
                                  "x = dict(doc)\n    x['sneaky_key'] = True\n    return x",
                                  "x = doc.copy()\n    x['sneaky_key'] = True")]
        check("kit292-the-compose-scan-sees-every-spelling-of-a-write",
              [bool(dyn) or "sneaky_key" in wrote for wrote, dyn in spellings], [True] * 14)
        # ...and a lookup table is no write: it never reaches doc (review of KIT-292).
        check("kit292-a-lookup-table-is-no-write",
              doc_writes("def f(conf, k):\n    doc = {}\n    v = {'sneaky_key': 1}[k]\n"),
              (set(), []))
        # No key is written by a name: the HISTORY_KEYS loop that once was went with the back
        # room (KIT-492).
        check("kit292-compose-config-writes-no-key-by-a-name", len(dynamic), 0)
        check("kit292-every-key-compose-config-writes-has-its-entry",
              (sorted(written - set(JOB_KEY_FROM)),
               {"schema", "handoff_states", "turn_model", "tech_lead_link_hosts"} <= written),
              ([], True))
        # Only the template's ", got " starts the value: a state named "We got it" is a name,
        # and the keys after it are still renamed (delta review of KIT-292).
        check("kit292-a-got-inside-a-name-is-no-value-tail",
              in_conf_terms("config 'handoff_states' names We got it, which is also "
                            "'ready_state' or 'plan_it_state'. A child sits there"),
              "HANDOFF_STATES names We got it, which is also READY_STATE or PLAN_IT_STATE. A "
              "child sits there")
        # ...and so is one a joined list carries, where ", got " sits inside the names
        # (final delta review of KIT-292).
        check("kit292-a-got-inside-joined-names-is-no-value-tail",
              in_conf_terms("config 'handoff_states' names Todo, got it, which is also "
                            "'ready_state' or 'plan_it_state'. A child sits there"),
              "HANDOFF_STATES names Todo, got it, which is also READY_STATE or PLAN_IT_STATE. "
              "A child sits there")
        check("kit292-a-got-before-a-number-inside-names-is-no-value-tail",
              in_conf_terms("config 'handoff_states' names Todo, got 5 left, which is also "
                            "'ready_state' or 'plan_it_state'."),
              "HANDOFF_STATES names Todo, got 5 left, which is also READY_STATE or "
              "PLAN_IT_STATE.")
        check("kit292-every-value-tail-the-job-writes-is-left-as-given",
              [in_conf_terms("config 'epic_wip' must be at most 10, got 12"),
               in_conf_terms("config 'act' must be true or false, got None"),
               in_conf_terms("config 'dm_user_ids' must be a list, got ['ready_state']"),
               in_conf_terms("config 'plan_it_state' must be a state name, got 'schema'")],
              ["EPIC_WIP must be at most 10, got 12", "ACT must be true or false, got None",
               "DM_USER_IDS must be a list, got ['ready_state']",
               "PLAN_IT_STATE must be a state name, got 'schema'"])
        # A refusal is hidden only when its own key was refused already, never because its
        # echoed value reads like that key.
        _hv, _he = validate_conf(dict(values, CHANNEL_IDS="", ALLOWED_SLACK_USERS="channel_ids"))
        check("kit292-an-echoed-value-never-hides-another-keys-refusal",
              ([e for e in _he if e.startswith("ALLOWED_SLACK_USERS")] != [],
               [e for e in _he if e.startswith("CHANNEL_IDS")] != []), (True, True))
        check("kit292-a-key-bridge-conf-does-not-set-is-the-jobs-own",
              (in_conf_terms("config 'schema' must be 'x', got 'y'"),
               in_conf_terms("config 'chat_api_base' must be an https Slack API address, "
                             "got 'ftp://x'")),
              ("'schema' (in the job's own config: bridge.conf does not set it) must be 'x', "
               "got 'y'",
               "'chat_api_base' (in the job's own config: bridge.conf does not set it) must "
               "be an https Slack API address, got 'ftp://x'"))
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
        # KIT-330: the job writes the awake clock beside its beat's time, from its own copy of the
        # monitor's clock, which this pins equal to the monitor's.
        with tempfile.TemporaryDirectory() as beat_dir:
            bridge_config.write_heartbeat({"state_dir": beat_dir}, {"exit": 0, "dry": False,
                                                                    "summary": "ok"},
                                          "2026-10-10T12:00:00Z")
            written = json.load(open(bridge_config.heartbeat_path({"state_dir": beat_dir})))
        check("the-bridge-job-writes-its-awake-clock-beside-its-beat",
              (isinstance(written.get("awake_clock"), float), written["at"], written["exit"]),
              (True, "2026-10-10T12:00:00Z", 0))
        check("the-jobs-awake-clock-is-the-monitors",
              abs(bridge_config._awake_clock() - hbm._awake_clock()) < 5, True)
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
        # KIT-477: the app a project's owner talks to is that project's tech lead. The manifest
        # carries the name twice (the app's and its bot's), the same; a conf with no
        # PROJECT_NAME keeps the old name; a second project's app shares nothing with the first.
        tech = "Tech lead " + chr(0xb7) + " "
        named_conf, named_errs = validate_conf(dict(values, PROJECT_NAME="todoclaw"))
        other_conf, other_errs = validate_conf(dict(values, PROJECT_NAME="chefclaw"))
        mn, mo, m0 = manifest(named_conf), manifest(other_conf), manifest(conf)
        check("manifest-names-the-app-for-the-project-and-the-old-name-stays-without-one",
              (named_errs, other_errs,
               (mn["display_information"]["name"], mn["features"]["bot_user"]["display_name"]),
               (mo["display_information"]["name"], mo["features"]["bot_user"]["display_name"]),
               (m0["display_information"]["name"], m0["features"]["bot_user"]["display_name"])),
              ([], [], (tech + "todoclaw",) * 2, (tech + "chefclaw",) * 2,
               ("Tech lead",) * 2))
        # KIT-660: the person names their tech lead. A conf that names none is asked once, at a
        # terminal, before the manifest is printed; the answer is written as APP_NAME, and the
        # manifest carries it twice. A conf that names it is kept byte for byte, and nothing is
        # asked. With no terminal nothing blocks and nothing is written: the default is used,
        # and said. The question and every word around it go to stderr, so `manifest`'s stdout
        # stays JSON and nothing else.
        check("name-default-is-plain-and-never-the-components-own",
              (CONF_KEYS["APP_NAME"][0], DEFAULT_LEAD_NAME), ("Tech lead", "Tech lead"))

        def ask_run(body, answers=(), tty=True):
            with tempfile.TemporaryDirectory() as ntmp:
                path = os.path.join(ntmp, "bridge.conf")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                asked, left, err = [], list(answers), io.StringIO()

                def reader(prompt):
                    asked.append(prompt)
                    if not left:
                        raise EOFError
                    return left.pop(0)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc_ = cmd_manifest(path, reader=reader, tty=tty, err=err)
                with open(path, encoding="utf-8") as fh:
                    after = fh.read()
                out_ = buf.getvalue()
                names = None
                if parse_ok(out_):
                    doc = json.loads(out_)
                    names = (doc["display_information"]["name"],
                             doc["features"]["bot_user"]["display_name"])
                return rc_, asked, after, names, err.getvalue()

        bare = GOOD_CONF
        q = NAME_QUESTION % DEFAULT_LEAD_NAME
        r_asked = ask_run(bare, ["babyclaw"])
        check("name-asked-once-written-and-in-the-manifest-twice",
              (r_asked[0], r_asked[1], r_asked[2] == bare.rstrip("\n") + "\nAPP_NAME=babyclaw\n",
               r_asked[3]),
              (EX_OK, [q], True, ("babyclaw",) * 2))
        with tempfile.TemporaryDirectory() as wtmp:
            wpath = os.path.join(wtmp, "bridge.conf")
            with open(wpath, "w", encoding="utf-8") as fh:
                fh.write(bare)
            os.chmod(wpath, 0o600)
            w_rc = ask_name(wpath, {}, reader=lambda _q: "babyclaw", tty=True,
                            err=io.StringIO())[0]
            w_left = (sorted(os.listdir(wtmp)), oct(os.stat(wpath).st_mode & 0o777))
            os.chmod(wtmp, 0o500)               # the folder refuses the temp file
            try:
                f_err = io.StringIO()
                f_rc = ask_name(wpath, {}, reader=lambda _q: "other", tty=True, err=f_err)[0]
                with open(wpath, encoding="utf-8") as fh:
                    f_kept = fh.read().endswith("\nAPP_NAME=babyclaw\n")
                f_left = sorted(os.listdir(wtmp))
            finally:
                os.chmod(wtmp, 0o700)
        check("name-written-whole-through-a-swap-keeping-the-mode-and-a-failed-write-keeps-the-conf",
              (w_rc, w_left, f_rc, f_kept, f_left, "Nothing was written" in f_err.getvalue()),
              (EX_OK, (["bridge.conf"], "0o600"), EX_FAILED, True, ["bridge.conf"], True))
        check("name-saved-line-says-the-name-and-how-to-rename",
              (r_asked[4].startswith("Saved: your tech lead is called 'babyclaw' (APP_NAME in "),
               "change that line and paste the manifest again" in r_asked[4]), (True, True))
        r_empty = ask_run(bare, [""])
        check("name-empty-answer-takes-the-default-and-writes-it",
              (r_empty[0], len(r_empty[1]), r_empty[2].endswith("\nAPP_NAME=Tech lead\n"),
               r_empty[3]), (EX_OK, 1, True, ("Tech lead",) * 2))
        r_bad = ask_run(bare, ["a=b", "  baby claw  "])
        check("name-a-name-slack-would-refuse-is-said-and-asked-again",
              (r_bad[0], len(r_bad[1]), r_bad[2].endswith("\nAPP_NAME=baby claw\n"),
               r_bad[3], "letters, digits" in r_bad[4]),
              (EX_OK, 2, True, ("baby claw",) * 2, True))
        r_never = ask_run(bare, ["x" * 36, "a/b", "!!"])
        check("name-three-refused-answers-write-nothing-and-print-no-manifest",
              (r_never[0], len(r_never[1]), r_never[2] == bare, r_never[3]),
              (EX_USAGE, 3, True, None))
        kept_body = bare + "APP_NAME=babyclaw\n"
        r_kept = ask_run(kept_body, ["other"])
        proj_body = bare + "PROJECT_NAME=todoclaw\n"
        r_proj = ask_run(proj_body, ["other"])
        check("name-a-conf-that-names-it-is-kept-and-nothing-is-asked",
              (r_kept[:4], r_kept[4], r_proj[:4], r_proj[4]),
              ((EX_OK, [], kept_body, ("babyclaw",) * 2), "",
               (EX_OK, [], proj_body, (tech + "todoclaw",) * 2), ""))
        r_notty = ask_run(bare, ["babyclaw"], tty=False)
        check("name-no-terminal-never-blocks-writes-nothing-and-says-the-default",
              (r_notty[0], r_notty[1], r_notty[2] == bare, r_notty[3],
               "is not a terminal" in r_notty[4], "'Tech lead'" in r_notty[4],
               "APP_NAME=" in r_notty[4]),
              (EX_OK, [], True, ("Tech lead",) * 2, True, True, True))
        with tempfile.TemporaryDirectory() as itmp:
            ipath = os.path.join(itmp, "bridge.conf")
            with open(ipath, "w", encoding="utf-8") as fh:
                fh.write(bare)
            i_rc = ask_name_from_file(ipath, reader=lambda _q: "babyclaw", tty=True,
                                      err=io.StringIO())
            with open(ipath, encoding="utf-8") as fh:
                i_after = fh.read()
            i_again = ask_name_from_file(ipath, reader=lambda _q: "other", tty=True,
                                         err=io.StringIO())
            with open(ipath, encoding="utf-8") as fh:
                i_kept = fh.read()
        check("name-install-asks-too-and-only-the-first-time",
              (i_rc, i_after.endswith("\nAPP_NAME=babyclaw\n"), i_again, i_kept == i_after),
              (EX_OK, True, EX_OK, True))
        r_eof = ask_run(bare, [])
        check("name-no-answer-at-all-writes-nothing-and-uses-the-default",
              (r_eof[0], len(r_eof[1]), r_eof[2] == bare, r_eof[3]),
              (EX_OK, 1, True, ("Tech lead",) * 2))
        check("name-question-is-plain-and-says-no-component-name",
              (q.startswith("What do you want to call your tech lead?"), says_bridge(q)),
              (True, []))

        # KIT-660: nothing this installer shows a person says the component's name. Every
        # string the module carries outside its selftest and its stand-ins is read as prose:
        # a word that is a file, a key or a label (it holds / . _ = - or a brace) is an internal
        # name and may stay; any other word starting "bridge" is the component's name said.
        def module_prose_hits():
            import ast
            with open(os.path.abspath(__file__), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            skip, hits = set(), []
            for node in ast.walk(tree):
                if (isinstance(node, ast.FunctionDef) and node.name == "selftest") or (
                        isinstance(node, ast.ClassDef) and node.name.startswith("_")):
                    skip.update(id(n) for n in ast.walk(node))
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and \
                        node.body and isinstance(node.body[0], ast.Expr) and \
                        isinstance(getattr(node.body[0], "value", None), ast.Constant):
                    skip.add(id(node.body[0].value))           # a docstring: read by no one
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                        id(node) not in skip:
                    hits.extend((node.lineno, w) for w in says_bridge(node.value))
            return hits
        check("no-shown-string-says-the-components-name", module_prose_hits(), [])
        card_text = []
        for cid in sorted(CARDS):
            for c_ in (None, conf):
                card_text.append(cap(print_card, cid, c_, "bridge.conf")[1])
        check("no-card-says-the-components-name",
              says_bridge("\n".join(card_text)), [])
        check("cards-name-the-tech-lead",
              [CARDS[c][0] for c in ("CK-B1", "CK-B2", "CK-B3", "CK-B4")],
              ["Create your tech lead's own Slack app", "Load the tech lead's job",
               "Turn the tech lead's job off", "Prove the tech lead live (KIT-227)"])
        check("card-B1-asks-the-name-before-the-manifest",
              ("asks once what to call your tech lead" in CARDS["CK-B1"][1][0],
               "your DM with your tech lead" in " ".join(CARDS["CK-B1"][1])), (True, True))
        check("manifest-says-the-name-and-never-the-components",
              (says_bridge(json.dumps(manifest(conf))),
               manifest(conf)["display_information"]["name"]), ([], "Tech lead"))
        check("manifest-two-projects-share-nothing-but-the-scopes",
              sorted(k for k in mn if mn[k] != mo[k]), ["display_information", "features"])
        long_app = "A" * 36
        errs_of = lambda **over: [e for e in validate_conf(dict(values, **over))[1]]
        check("conf-project-name-is-checked-and-said-once-by-its-key",
              [errs_of(PROJECT_NAME="x" * 24), errs_of(PROJECT_NAME="a/b"),
               errs_of(PROJECT_NAME=" lead"), errs_of(PROJECT_NAME="x" * 23),
               errs_of(PROJECT_NAME="my-app_2.0"), errs_of(APP_NAME=long_app),
               errs_of(APP_NAME=long_app, PROJECT_NAME="todoclaw")],
              [["PROJECT_NAME must be 1 to 23 characters: letters, digits, spaces, dots, dashes "
                "and underscores, starting with a letter or digit, got %r" % ("x" * 24)],
               ["PROJECT_NAME must be 1 to 23 characters: letters, digits, spaces, dots, dashes "
                "and underscores, starting with a letter or digit, got 'a/b'"],
               ["PROJECT_NAME must be 1 to 23 characters: letters, digits, spaces, dots, dashes "
                "and underscores, starting with a letter or digit, got ' lead'"],
               [], [], ["APP_NAME is 36 characters; Slack allows 35"], []])

        def png_of(width, height, name):
            path = os.path.join(gtmp_avatar, name)
            with open(path, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                         + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06")
            return path

        with tempfile.TemporaryDirectory() as gtmp_avatar:
            good = png_of(512, 512, "good.png")
            text = os.path.join(gtmp_avatar, "text.png")
            with open(text, "w") as fh:
                fh.write("not a picture, but long enough to hold a PNG header, twice over")
            link = os.path.join(gtmp_avatar, "link.png")
            os.symlink(good, link)
            nochunk = os.path.join(gtmp_avatar, "nochunk.png")
            with open(nochunk, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IDAT"
                         + (512).to_bytes(4, "big") * 2)
            sizes = [png_of(256, 256, "small.png"), png_of(512, 600, "tall.png"),
                     png_of(2001, 2001, "huge.png"), png_of(2000, 2000, "max.png")]
            bad_why = ["is 256x256 pixels; it must be square, 512 to 2000",
                       "is 512x600 pixels; it must be square, 512 to 2000",
                       "is 2001x2001 pixels; it must be square, 512 to 2000", None]
            check("conf-app-avatar-must-be-a-square-png-of-a-size-slack-takes",
                  ([avatar_problem(good), avatar_problem(text),
                    avatar_problem(os.path.join(gtmp_avatar, "none.png")),
                    avatar_problem(link), avatar_problem(gtmp_avatar),
                    avatar_problem(nochunk)]
                   + [avatar_problem(each) for each in sizes]),
                  [None, "is not a PNG file", "is not a plain file", "is not a plain file",
                   "is not a plain file", "is not a PNG file"] + bad_why)
            check("conf-app-avatar-is-said-once-by-its-key-and-nothing-is-written",
                  ([e.split(" ")[0] for e in errs_of(APP_AVATAR=text)],
                   errs_of(APP_AVATAR=good), os.listdir(gtmp_avatar).count("good.png")),
                  (["APP_AVATAR"], [], 1))
            card = card_values(validate_conf(dict(values, PROJECT_NAME="todoclaw",
                                                  APP_AVATAR=good))[0], "bridge.conf")
            plain = card_values(conf, "bridge.conf")
            check("card-names-the-app-and-the-picture-to-upload",
                  (card["APP_NAME"], card["AVATAR"] == good, plain["APP_NAME"], plain["AVATAR"]),
                  (tech + "todoclaw", True, "Tech lead", "(none set: skip this line)"))
            check("card-says-how-to-rename-and-where-the-picture-goes-by-hand",
                  ("upload {AVATAR}" in " ".join(CARDS["CK-B1"][1]),
                   "Save Changes keeps the token" in " ".join(CARDS["CK-B1"][1]),
                   "run line 6 again" in " ".join(CARDS["CK-B1"][1])), (True, True, True))
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
        # KIT-297 review: with DM_USER_IDS set, NOTIFIER_STATE_DIR is composed twice (the
        # pings file, and the notifier's record of its DM pages). A bad one is still named
        # once, by its conf key.
        with tempfile.TemporaryDirectory() as gtmp:
            os.makedirs(os.path.join(gtmp, ".git"))
            _c, errs = validate_conf(dict(values, DM_USER_IDS="U0ALICE001",
                                          NOTIFIER_STATE_DIR=gtmp + "/state dir"))
            check("conf-a-refused-notifier-state-dir-named-once-with-DM_USER_IDS-too",
                  [e.split(" ")[0] for e in errs], ["NOTIFIER_STATE_DIR"])
        # KIT-492, THE CUTOVER: a bridge.conf written before it still loads. Each of the old
        # chain's lines is read as nothing, named, composed into nothing, and said by
        # `compose`; any other unknown key is still refused. The tech lead's turn is on by
        # default, so a conf that leaves it on needs TURN_BIN and a DM member, each named.
        retired_text = GOOD_CONF + ("ASK_TECH_LEAD=yes\nCHAT_BOT_USER_ID=U0CHATBOT1\n"
                                    "WRITER=yes\nWRITER_BIN=/opt/claude/bin/claude\n"
                                    "TECH_LEAD_WAKE=yes\nWAKE_QUIET_HOURS=none\n")
        r_values, r_perrs = parse_conf(retired_text)
        r_conf, r_errs = validate_conf(r_values)
        check("conf-a-retired-line-loads-is-named-and-composes-nothing",
              outcome(lambda: (r_perrs, r_errs, r_conf["RETIRED"],
                               config_bytes(r_conf) == config_bytes(conf),
                               parse_conf(GOOD_CONF + "WAKE_BOGUS=1\n")[1])),
              ([], [], ["ASK_TECH_LEAD", "CHAT_BOT_USER_ID", "TECH_LEAD_WAKE",
                        "WAKE_QUIET_HOURS", "WRITER", "WRITER_BIN"], True,
               ["line 11: unknown key WAKE_BOGUS"]))
        check("compose-names-the-retired-lines-to-take-out",
              outcome(lambda: ("RETIRED (KIT-492): bridge.conf still has ASK_TECH_LEAD, "
                               "CHAT_BOT_USER_ID, TECH_LEAD_WAKE, WAKE_QUIET_HOURS, WRITER, "
                               "WRITER_BIN. Each does nothing since the cutover; take those "
                               "lines out." in cap(cmd_compose, r_conf, "bridge.conf")[1],
                               "RETIRED" in cap(cmd_compose, conf, "bridge.conf")[1])),
              (True, False))
        wbin = "/opt/claude/bin/claude"
        no_turn = dict((k, v) for k, v in values.items() if k != "TECH_LEAD_TURN")
        check("conf-TECH_LEAD_TURN-on-by-default-needs-TURN_BIN-and-a-DM-each-named",
              outcome(lambda: (CONF_KEYS["TECH_LEAD_TURN"][0],
                               sorted(e.split(" ")[0] for e in validate_conf(no_turn)[1]),
                               validate_conf(dict(no_turn, TURN_BIN=wbin,
                                                  DM_USER_IDS="U0ALICE001"))[1],
                               compose_config(validate_conf(dict(
                                   no_turn, TURN_BIN=wbin,
                                   DM_USER_IDS="U0ALICE001"))[0]).get("tech_lead_turn"))),
              ("yes", ["TECH_LEAD_TURN", "TURN_BIN"], [], True))
        # Every problem in one pass: a TURN_BIN that is no full path is named even while the
        # DM members are missing too (Stage E on #494).
        check("conf-a-bad-TURN_BIN-is-named-even-with-no-DM-member",
              outcome(lambda: sorted(e.split(" ")[0] for e in validate_conf(
                  dict(no_turn, TURN_BIN="claude"))[1])),
              ["TECH_LEAD_TURN", "TURN_BIN"])
        check("conf-TURN_TOKEN_ENV-needs-a-name-of-its-own",
              outcome(lambda: [[e.split(" ")[0] for e in validate_conf(dict(
                  values, TURN_TOKEN_ENV=v))[1]] for v in (
                  "claude token", "BRIDGE_SLACK_BOT_TOKEN", "STAGE_E_LINEAR_API_KEY")]),
              [["TURN_TOKEN_ENV"]] * 3)
        check("card-b6-answers-devs-and-watches-the-notifier (KIT-332)",
              outcome(lambda: [n for n in (
                  "BRIDGE_PINGS=yes", "pipeline_notifier_setup.py run", "NOTIFIER_STATE_DIR",
                  "NOTIFIER_JOB_LABEL", "install --conf bridge.conf --apply",
                  "verify --conf bridge.conf", "ALREADY-DONE", "pipeline_install.py")
                  if n not in cap(print_card, "CK-B6", conf)[1]]), [])
        check("the-writer-and-wake-cards-went-at-the-cutover",
              sorted(c for c in ("CK-B5", "CK-B8") if c in CARDS), [])
        # ACT-4: direct asks. Composed only with DIRECT_ASKS=yes, so a config written before
        # the keys existed is the same bytes; on, it needs acting, a DM and the agent's
        # account, each named; each number in its range, and the job takes what it composes.
        d_keys = ("direct_asks", "direct_grace_seconds", "direct_max_tickets",
                  "direct_max_age_minutes")
        try:
            d_base = dict(values, ACT="yes", DM_USER_IDS="U0ALICE001", AGENT_USER_NAME="Agent")
            d_conf, d_errs = validate_conf(dict(d_base, DIRECT_ASKS="yes", DIRECT_MAX_TICKETS="4"))
            d_doc = compose_config(d_conf)
            d_loaded = bridge.validate_config(dict(d_doc, state_dir="/nonexistent-%s"
                                                   % os.getpid()))
            d_got = (d_errs, [d_doc.get(k) for k in d_keys], [d_loaded.get(k) for k in d_keys],
                     [k for k in d_keys if k in compose_config(validate_conf(d_base)[0])],
                     [e.split(":")[0] for e in validate_conf(dict(
                         values, DIRECT_ASKS="yes", ACT="no", DM_USER_IDS="",
                         AGENT_USER_NAME=""))[1] if e.startswith("DIRECT_ASKS needs")],
                     len([e for e in validate_conf(dict(
                         d_base, DIRECT_GRACE_SECONDS="5", DIRECT_MAX_TICKETS="11",
                         DIRECT_MAX_AGE_MINUTES="x"))[1] if e.startswith("DIRECT_")]))
        except Exception as exc:                                   # noqa: BLE001
            d_got = "raised %s: %s" % (type(exc).__name__, exc)
        check("conf-DIRECT_ASKS-composed-only-when-yes-and-on-only-with-what-it-needs",
              d_got, ([], [True, 90, 4, 10], [True, 90, 4, 10], [],
                      ["DIRECT_ASKS needs ACT=yes", "DIRECT_ASKS needs DM_USER_IDS",
                       "DIRECT_ASKS needs AGENT_USER_NAME"], 3))
        # KIT-340: the reminder's hours. Composed only when not the job's own default, so a
        # config written before the key existed is the same bytes; 0 turns it off, and the
        # job takes what it composes; each bad value is named once, by its conf key.
        def reminder_doc(hours):
            return compose_config(validate_conf(dict(values, QUESTION_REMINDER_HOURS=hours))[0])
        try:
            r_off = reminder_doc("0")
            r_got = ("question_reminder_hours" in compose_config(conf),
                     config_bytes(conf) == config_bytes(validate_conf(dict(
                         values, QUESTION_REMINDER_HOURS="4"))[0]),
                     r_off.get("question_reminder_hours"),
                     bridge.validate_config(dict(r_off, state_dir="/nonexistent-%s"
                                                 % os.getpid()))["question_reminder_hours"],
                     reminder_doc("6").get("question_reminder_hours"),
                     [[e.split(",")[0] for e in validate_conf(dict(
                         values, QUESTION_REMINDER_HOURS=bad))[1] if "reminder" in e.lower()]
                      for bad in ("24", "-1", "x", chr(0xb2))])
        except Exception as exc:                                   # noqa: BLE001
            r_got = "raised %s: %s" % (type(exc).__name__, exc)
        check("conf-QUESTION_REMINDER_HOURS-composed-only-when-not-the-default-named-once",
              r_got, (False, True, 0, 0, 6, [["QUESTION_REMINDER_HOURS must be a whole number "
                                              "from 0 to %d" % REMINDER_HOURS_MAX]] * 4))

        check("compose-says-what-the-tech-leads-turn-does-on-and-off",
              outcome(lambda: ("TECH_LEAD_TURN=yes: anything else written in those DMs is "
                               "answered there by one turn of /opt/claude/bin/claude (sonnet)"
                               in cap(cmd_compose, validate_conf(dict(
                                   values, TECH_LEAD_TURN="yes", TURN_BIN=wbin,
                                   DM_USER_IDS="U0ALICE001"))[0], "bridge.conf")[1],
                               "TECH_LEAD_TURN=no: anything else written in those DMs gets one "
                               "line" in cap(cmd_compose, conf, "bridge.conf")[1])),
              (True, True))
        # KIT-343: the hosts a link in the tech lead's answer may go to and stay a link.
        # Composed only when not the job's default, in lower case, so a default install
        # composes the same bytes as before; the job's own loader takes it; a bad host is
        # named once, by its conf key. Since the cutover it needs no other switch (KIT-492).
        def hosts_doc(**over):
            return compose_config(validate_conf(dict(values, **over))[0])
        try:
            hosts_got = (
                "tech_lead_link_hosts" in hosts_doc(),
                config_bytes(conf) == config_bytes(validate_conf(dict(
                    values, TECH_LEAD_LINK_HOSTS="linear.app, github.com"))[0]),
                hosts_doc(TECH_LEAD_LINK_HOSTS="GitHub.com, notion.so")
                .get("tech_lead_link_hosts"),
                bridge.validate_config(dict(hosts_doc(TECH_LEAD_LINK_HOSTS="notion.so"),
                    state_dir="/nonexistent-%s" % os.getpid()))["tech_lead_link_hosts"],
                [[e for e in validate_conf(dict(values, TECH_LEAD_LINK_HOSTS=v))[1]
                  if "LINK_HOSTS" in e or "link_hosts" in e]
                 for v in ("com", "github.com, *.notion.so", "https://github.com")])
        except Exception as exc:                                   # noqa: BLE001
            hosts_got = "raised %s: %s" % (type(exc).__name__, exc)
        hosts_bad = ("TECH_LEAD_LINK_HOSTS must list host names like github.com, each of two "
                     "labels or more, comma-separated, got %r")
        check("conf-TECH_LEAD_LINK_HOSTS-composed-only-when-not-the-default-and-named-once",
              hosts_got,
              (False, True, ["github.com", "notion.so"], ["notion.so"],
               [[hosts_bad % "com"], [hosts_bad % "*.notion.so"],
                [hosts_bad % "https://github.com"]]))
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
        for needle in ("Plan from your DM", "history shows your move", "guest", "asleep",
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
        # KIT-271: the manifest needs no BOT_USER_ID, is JSON and nothing else, and the scope
        # notes in compose are a block of their own, so "the manifest above" can be pasted whole.
        no_id = GOOD_CONF.replace("BOT_USER_ID=U0BRIDGE01\n", "BOT_USER_ID=\n")
        no_key = GOOD_CONF.replace("BOT_USER_ID=U0BRIDGE01\n", "")
        bad_conf = GOOD_CONF.replace("TEAM_KEYS=PROD\n", "")
        import tempfile
        mtmp = tempfile.mkdtemp(prefix="manifest-")
        confs = {}
        for name, text in (("full", GOOD_CONF), ("empty-id", no_id), ("no-key", no_key),
                           ("bad", bad_conf)):
            confs[name] = os.path.join(mtmp, "manifest-%s.conf" % name)
            with open(confs[name], "w") as fh:
                fh.write(text)
        outs = dict((n, cap(cmd_manifest, path, tty=False, err=io.StringIO()))
                    for n, path in confs.items())
        parsed = {}
        for n in ("full", "empty-id", "no-key"):
            try:
                parsed[n] = json.loads(outs[n][1])
            except ValueError:
                parsed[n] = None
        check("manifest-prints-json-and-nothing-else-with-or-without-an-id",
              (outs["full"][0], outs["empty-id"][0], outs["no-key"][0],
               parsed["full"] is not None, parsed["full"] == parsed["empty-id"] == parsed["no-key"],
               parsed["full"] == manifest(conf) if conf["BOT_USER_ID"] else True),
              (EX_OK, EX_OK, EX_OK, True, True, True))
        check("manifest-holds-no-member-id-and-no-scope-notes",
              ("U0BRIDGE01" in outs["full"][1], MANIFEST_STAND_IN_ID in outs["no-key"][1],
               any(why in outs["full"][1] for why in SCOPE_CITES.values())),
              (False, False, False))
        check("manifest-another-conf-problem-is-said-in-full-and-no-json-is-printed",
              (outs["bad"][0], "CONF PROBLEMS in" in outs["bad"][1],
               "TEAM_KEYS is required" in outs["bad"][1], "{" in outs["bad"][1]),
              (EX_USAGE, True, True, False))
        rc_missing, out_missing = cap(cmd_manifest, os.path.join(mtmp, "no-such.conf"),
                                      tty=False, err=io.StringIO())
        check("manifest-an-unreadable-conf-is-said", (rc_missing, "could not read" in out_missing),
              (EX_USAGE, True))
        rc_c, out_c = cap(cmd_compose, conf, confs["full"])
        piece1 = out_c.split("PIECE 1")[1].split("PIECE 2")[0]
        json_lines = []
        for line in piece1.splitlines()[1:]:
            if line.startswith("    "):
                json_lines.append(line[4:])
            elif json_lines:
                break
        check("compose-piece-1-json-is-the-manifest-alone-and-the-notes-follow-apart",
              (json.loads("\n".join(json_lines)) == manifest(conf),
               "Why each scope" in piece1 and "NOT part of the manifest" in piece1
               and piece1.index("Why each scope") > piece1.rindex("}")), (True, True))
        main_err = io.StringIO()
        with contextlib.redirect_stderr(main_err):
            main_rc, main_out = cap(main, ["manifest", "--conf", confs["no-key"]])
        check("manifest-is-a-command-and-runs-where-an-agent-does",
              (main_rc, parse_ok(main_out), "is not a terminal" in main_err.getvalue()),
              (EX_OK, True, True))
        card = " ".join(CARDS["CK-B1"][1])
        check("card-ck-b1-prints-the-manifest-first-and-fills-the-existing-line",
              (card.index("manifest --conf") < card.index("From a manifest"),
               "BOT_USER_ID= line the conf already has" in card, "do not add a second one" in card,
               "paste the manifest `compose` prints" in card),
              (True, True, True, False))
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
            check("direct-asks-refused-under-a-model",
                  cap(cmd_direct_asks, conf, "/nonexistent.conf", r, _FakeSudo(), True,
                      lambda _p: "yes")[0], EX_REFUSED)
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

            # KIT-297: writer-token puts the writer's token in place as `token` puts the
            # bridge's: refused under a model, only at a terminal, only a token `claude
            # setup-token` prints (never an API key), with the same refusals and backups,
            # and never printed. Each case runs alone.
            prompts = []

            def typed(value):
                # A list is one answer per prompt, then empty lines (KIT-301 review).
                answers = list(value) if isinstance(value, list) else None

                def reader(prompt):
                    prompts.append(prompt)
                    if answers is None:
                        return value
                    return answers.pop(0) if answers else ""
                return reader

            # TURN_BIN is set before writer-token, so its check has a program to run.
            bconf = dict(conf, TURN_BIN="/opt/homebrew/bin/claude")

            def writer_token(c_=bconf, value="x", tty=True, sudo=None, runner=None):
                return cap(cmd_writer_token, c_, runner or r, sudo or _FakeSudo(),
                           typed(value), tty)
            os.environ[AGENT_ENV_MARKERS[0]] = ""
            r = _RoleRunner(home, root)
            check("writer-token-refused-under-a-model",
                  outcome(lambda: (writer_token()[0], r.writes)), (EX_REFUSED, []))
            os.environ.pop(AGENT_ENV_MARKERS[0])
            check("writer-token-needs-a-terminal", outcome(lambda: writer_token(tty=False)[0]),
                  EX_BLOCKED)
            before = open(envp).read()
            for name, value in (("an-api-key", "sk-ant-api03-" + "a" * 40),
                                ("a-slack-token", "xoxb-" + "a" * 30),
                                ("shell-after-it", "sk-ant-oat01-%s $(id)" % ("a" * 30))):
                got = outcome(lambda: writer_token(value=value))
                check("writer-token-refuses:" + name,
                      (got[0], open(envp).read() == before, "a" * 30 in got[1])
                      if isinstance(got, tuple) else got, (EX_USAGE, True, False))
            wtoken = "sk-ant-oat01-" + "t" * 95
            # Three older backups in place (KIT-297 review): the run must add its own and
            # prune the oldest, so a writer that skips either one fails the count or the name.
            bdir = os.path.join(home, ".stage-e", "backups")
            for n in os.listdir(bdir):
                if n.startswith("env.bridge-setup."):
                    os.remove(os.path.join(bdir, n))
            older = ["env.bridge-setup.20000101T00000%dZ.1" % i for i in range(3)]
            for n in older:
                with open(os.path.join(bdir, n), "w") as fh:
                    fh.write("an older backup\n")
            probes_before = len(r.probes)
            got = outcome(lambda: writer_token(value=wtoken))
            got_written, probes_written = got, r.probes[probes_before:]
            after = sorted(n for n in os.listdir(bdir) if n.startswith("env.bridge-setup."))
            env_text = open(envp).read()
            check("writer-token-written",
                  (got[0], "CLAUDE_WRITER_OAUTH_TOKEN=%s" % wtoken in env_text,
                   "BRIDGE_SLACK_BOT_TOKEN=" in env_text, oct(os.stat(envp).st_mode & 0o777))
                  if isinstance(got, tuple) else got, (EX_OK, True, True, "0o600"))
            check("writer-token-never-printed-nor-in-argv",
                  (wtoken in str(got), any(wtoken in " ".join(w["argv"]) for w in r.writes)),
                  (False, False))
            check("writer-token-keeps-three-backups",
                  (len(after), after[:2] == older[1:], after[-1] not in older,
                   after[-1] in str(got)), (3, True, True, True))
            check("writer-token-asks-for-the-setup-token-first-and-says-the-turn-is-off",
                  (bool(prompts) and "run `claude setup-token` in your own terminal"
                   in prompts[-1], "Set TECH_LEAD_TURN=yes" in str(got)), (True, True))
            # KIT-301: the prompt says how to paste a wrapped token, a token cut at a line
            # break is completed at further hidden prompts or refused by its length, and the
            # stored token is tried once, the way the job runs it.
            check("writer-token-prompt-says-how-to-paste-a-wrapped-token",
                  bool(prompts) and "paste the first line and press Return" in prompts[-1],
                  True)
            before = saved_env = open(envp).read()
            cut = "sk-ant-oat01-" + "c" * 60
            del prompts[:]
            got = outcome(lambda: writer_token(value=[cut, ""]))
            check("writer-token-asks-for-the-rest-of-a-cut-token-at-a-hidden-prompt",
                  (len(prompts), "Paste its next line (hidden)" in prompts[-1],
                   "73 characters" in prompts[-1]), (2, True, True))
            check("writer-token-refuses-a-token-cut-at-a-line-break",
                  (got[0], "has 73 characters" in got[1], "part of it is missing" in got[1],
                   open(envp).read() == before, "c" * 60 in got[1])
                  if isinstance(got, tuple) else got, (EX_USAGE, True, True, True, False))
            # The refusal comes only after the owner stops, or after the last line it asks
            # for, never while the token's tail is still to come (KIT-301 review).
            del prompts[:]
            got = outcome(lambda: writer_token(value=cut))
            check("writer-token-stops-asking-after-its-last-line",
                  (got[0], len(prompts)) if isinstance(got, tuple) else got,
                  (EX_USAGE, WRITER_TOKEN_MAX_LINES))
            # KIT-301 fix review: the asking goes on to the whole 108 characters, so a token
            # wrapped as 104 + 4, or 52 + 52 + 4, is asked for to its end, and a first line
            # too short for a whole token's shape (a terminal 30 columns wide) is not refused
            # while the rest is still to come.
            for name, cuts in (("104-then-4", (104,)), ("52-52-4", (52, 104)),
                               ("30-columns", (30, 60, 90))):
                # Each case its own token, so none passes on what the one before it stored.
                whole_ = "sk-ant-oat01-" + (("P4rT5q%d" % len(cuts)) * 20)[:95]
                pieces, at_ = [], 0
                for cut_at in cuts + (len(whole_),):
                    pieces.append(whole_[at_:cut_at])
                    at_ = cut_at
                del prompts[:]
                got = outcome(lambda: writer_token(value=list(pieces)))
                check("writer-token-asks-to-the-whole-token:" + name,
                      (got[0], "CLAUDE_WRITER_OAUTH_TOKEN=%s\n" % whole_ in open(envp).read(),
                       len(prompts)) if isinstance(got, tuple) else got,
                      (EX_OK, True, len(pieces)))
            # A shorter token the owner says is all of it, with an empty line, is still taken
            # when it reaches the floor: a change in the format is said by the check, not here.
            short_whole = "sk-ant-oat01-" + "v" * 90
            got = outcome(lambda: writer_token(value=[short_whole, ""]))
            check("writer-token-takes-a-shorter-token-the-owner-says-is-whole",
                  (got[0], "CLAUDE_WRITER_OAUTH_TOKEN=%s\n" % short_whole in open(envp).read())
                  if isinstance(got, tuple) else got, (EX_OK, True))
            del prompts[:]
            got = outcome(lambda: writer_token(value=[cut, "sk-ant-oat01-" + "j" * 50, ""]))
            check("writer-token-a-new-first-line-starts-the-token-again",
                  (got[0], "has 63 characters" in got[1], len(prompts))
                  if isinstance(got, tuple) else got, (EX_USAGE, True, 3))
            check("writer-token-refuses-lines-that-make-no-token",
                  outcome(lambda: (lambda g: (g[0], "the lines you pasted do not make" in g[1]))(
                      writer_token(value=[cut, "c" * 20 + " $(id)"]))), (EX_USAGE, True))
            check("writer-token-tries-the-token-it-stored",
                  ("Checked: one sample call went through with the new token" in str(got_written),
                   len(probes_written)), (True, 1))
            refused_run = _RoleRunner(home, root, probe_rc=1, probe_out=(
                "A sample.\nthe claude CLI said: Failed to authenticate.\nfallback: the writer "
                "exited 1: Anthropic refused the token\n"))
            got = outcome(lambda: writer_token(value="sk-ant-oat01-" + "f" * 95,
                                               runner=refused_run))
            check("writer-token-says-when-anthropic-refuses-the-stored-token",
                  (got[0], "the token is stored, but its check says: fallback: the "
                   "writer exited 1: Anthropic refused the token" in got[1])
                  if isinstance(got, tuple) else got, (EX_FAILED, True))
            # KIT-301 review: the check uses the conf's own settings, given on stdin, not the
            # config installed before them: since the cutover, TURN_BIN and TURN_TOKEN_ENV, in
            # the writer's key names its probe reads (KIT-492).
            # A name of the conf's own, not the default, so the installed config or a default
            # could not pass for it (Stage E on #494).
            tried = _RoleRunner(home, root)
            got = outcome(lambda: writer_token(dict(bconf, TURN_TOKEN_ENV="TECH_LEAD_OAUTH_TOKEN"),
                                               "sk-ant-oat01-" + "g" * 95, runner=tried))
            sent = (json.loads(tried.probe_stdins[0]) if tried.probe_stdins
                    and tried.probe_stdins[0] else {})
            check("writer-token-tries-the-confs-turn-bin",
                  (got[0] if isinstance(got, tuple) else got,
                   [p_.endswith(" probe --config -") for p_ in tried.probes], sent),
                  (EX_OK, [True], {"writer_bin": "/opt/homebrew/bin/claude",
                                   "writer_token_env": "TECH_LEAD_OAUTH_TOKEN"}))
            unset = _RoleRunner(home, root)
            got = outcome(lambda: writer_token(conf, "sk-ant-oat01-" + "h" * 95, runner=unset))
            check("writer-token-without-turn-bin-says-not-checked",
                  (got[0], "NOT CHECKED: TURN_BIN is not set" in got[1], unset.probes,
                   "Set TECH_LEAD_TURN=yes in the conf" in got[1])
                  if isinstance(got, tuple) else got, (EX_UNKNOWN, True, [], True))
            # What the check found decides the verdict: FAILED only for the token or the
            # writer's own setup, NOT CHECKED when Anthropic could not be asked, and
            # Checked when Anthropic took the token and only a check on the words failed.
            # After FAILED it says the fix, never `install --apply`; otherwise the next step,
            # which with TECH_LEAD_TURN=no, as here, is to turn the turn on.
            nxt = "The tech lead's turn is off. Set TECH_LEAD_TURN=yes in the conf"
            for name, rc_, said, code, words, then in (
                    # KIT-360: a clone from before the writer has no voice script: it is
                    # behind, which says nothing about the token.
                    ("no-voice-script", 2, "/usr/bin/python3: can't open file "
                     "'/h/.stage-e/kit/scripts/pipeline_voice.py': [Errno 2] No such file or "
                     "directory", EX_UNKNOWN, "NOT CHECKED: the token is stored, but the role "
                     "account's copy of the kit is older than this installer", nxt),
                    ("a-server-error", 1, "A sample.\nthe claude CLI said: API Error: 500 "
                     "Internal server error.\nfallback: the writer exited 1: Anthropic had a "
                     "server error", EX_UNKNOWN, "NOT CHECKED: the token is stored, but "
                     "Anthropic could not be asked (Anthropic had a server error)", nxt),
                    ("the-organization-turned-it-off", 1, "A sample.\nthe claude CLI said: "
                     "Your organization has disabled Claude subscription access\nfallback: the "
                     "writer exited 1: the organization has turned off Claude subscription "
                     "access for Claude Code", EX_FAILED, "check says: fallback: the writer "
                     "exited 1: the organization has turned off", "A new token won't help"),
                    ("cannot-start", 1, "A sample.\nfallback: could not start "
                     "/opt/homebrew/bin/claude: No such file or directory", EX_FAILED,
                     "check says: fallback: could not start /opt/homebrew/bin/claude",
                     "TURN_BIN is not a claude the role account can run"),
                    ("a-refused-token", 1, "A sample.\nthe claude CLI said: Failed to "
                     "authenticate.\nfallback: the writer exited 1: Anthropic refused the "
                     "token", EX_FAILED, "check says: fallback: the writer exited 1: "
                     "Anthropic refused the token", "Make a new one with `claude "
                     "setup-token`"),
                    # A role clone from before `--config -` reads the dash as a file: the
                    # clone is behind, which says nothing about the token (fix review).
                    ("a-clone-older-than-this-installer", 2, "FAIL: could not read --config "
                     "-: [Errno 2] No such file or directory: '-'", EX_UNKNOWN, "NOT CHECKED: "
                     "the token is stored, but the role account's copy of the kit is older "
                     "than this installer", nxt),
                    ("a-usage-limit", 1, "A sample.\nthe claude CLI said: You've hit your "
                     "session limit · resets 3pm\nfallback: the writer exited 1: a usage "
                     "limit was reached", EX_UNKNOWN, "NOT CHECKED: the token is stored, but "
                     "Anthropic could not be asked (a usage limit was reached)", nxt),
                    ("a-timeout", 1, "A sample.\nfallback: the writer took longer than 60 "
                     "seconds", EX_UNKNOWN, "could not be asked (the writer took longer than "
                     "60 seconds)", nxt),
                    ("a-check-on-the-words", 1, "A sample.\nfallback: the model's text asks "
                     "him a question", EX_OK, "Checked: Anthropic took the new token, though a "
                     "check refused the model's sample words (the model's text asks him a "
                     "question)", nxt),
                    ("an-unnamed-refusal", 1, "A sample.\nthe claude CLI said: Something odd "
                     "happened\nfallback: the writer exited 1", EX_FAILED, "check says: "
                     "fallback: the writer exited 1 (the claude CLI said: Something odd "
                     "happened)", "Fix what it says, then run `writer-token` again")):
                found = _RoleRunner(home, root, probe_rc=rc_, probe_out=said + "\n")
                got = outcome(lambda: writer_token(value="sk-ant-oat01-" + "k" * 95,
                                                   runner=found))
                check("writer-token-check-verdict:" + name,
                      (got[0], words in got[1], then in got[1],
                       code == EX_FAILED and ("Next: `install --apply`" in got[1]
                                              or "Set TECH_LEAD_TURN=yes" in got[1]))
                      if isinstance(got, tuple) else got, (code, True, True, False))
            on_conf = dict(bconf, TECH_LEAD_TURN="yes")
            got = outcome(lambda: writer_token(on_conf, "sk-ant-oat01-" + "k" * 95))
            check("writer-token-with-the-writer-on-says-the-next-step",
                  (got[0], "Next: `install --apply`, if the conf changed, then `verify`" in got[1])
                  if isinstance(got, tuple) else got, (EX_OK, True))
            # A token that wrapped, pasted a line at a time: stored whole (KIT-301 review).
            whole = "sk-ant-oat01-" + "m" * 60 + "n" * 35
            del prompts[:]
            got = outcome(lambda: writer_token(value=[whole[:73], whole[73:]]))
            env_text = open(envp).read()
            check("writer-token-joins-a-wrapped-token-pasted-a-line-at-a-time",
                  (got[0], "CLAUDE_WRITER_OAUTH_TOKEN=%s\n" % whole in env_text, len(prompts),
                   "n" * 35 in got[1], "m" * 40 in got[1])
                  if isinstance(got, tuple) else got, (EX_OK, True, 2, False, False))
            with open(envp, "w") as fh:           # the cases below start from the first write
                fh.write(saved_env)
            got = outcome(lambda: writer_token(dict(conf, TURN_TOKEN_ENV="OTHER_WRITER_TOKEN"),
                                               wtoken))
            check("writer-token-already-held-by-another-name",
                  (got[0], "CLAUDE_WRITER_OAUTH_TOKEN" in got[1], "needs its own token"
                   in got[1], wtoken in got[1]) if isinstance(got, tuple) else got,
                  (EX_REFUSED, True, True, False))
            # The env file is shared: a name that holds the notifier's Slack token is never
            # overwritten by the tech lead's (Stage E review of #220).
            with open(envp, "a") as fh:
                fh.write("NOTIFIER_SLACK_BOT_TOKEN=xoxb-notifier-%s\n" % ("n" * 30))
            got = outcome(lambda: writer_token(dict(conf,
                                                    TURN_TOKEN_ENV="NOTIFIER_SLACK_BOT_TOKEN"),
                                               "sk-ant-oat01-" + "w" * 95))
            env_text = open(envp).read()
            check("writer-token-never-overwrites-another-credential-under-its-name",
                  (got[0], "already holds another credential" in got[1], "n" * 30 in got[1],
                   "xoxb-notifier-%s" % ("n" * 30) in env_text, "w" * 40 in env_text,
                   "already holds another credential" in TOKEN_WRITER_PY)
                  if isinstance(got, tuple) else got,
                  (EX_REFUSED, True, False, True, False, False))
            got = outcome(lambda: writer_token(value="sk-ant-oat01-" + "r" * 95))
            check("writer-token-replaces-its-own-earlier-setup-token",
                  (got[0], "r" * 40 in open(envp).read(), wtoken in open(envp).read())
                  if isinstance(got, tuple) else got, (EX_OK, True, False))
            held = "sk-ant-oat01-" + "d" * 95
            with open(os.path.join(home, "dispatcher.env"), "a") as fh:
                fh.write("CLAUDE_CODE_OAUTH_TOKEN=%s\n" % held)
            got = outcome(lambda: writer_token(value=held))
            check("writer-token-the-dispatchers-own-refused",
                  (got[0], "CLAUDE_CODE_OAUTH_TOKEN" in got[1], "d" * 40 in got[1],
                   "d" * 40 in open(envp).read()) if isinstance(got, tuple) else got,
                  (EX_REFUSED, True, False, False))
            got = outcome(lambda: writer_token(dict(conf,
                                                    DISPATCHER_ENV_FILE="~/dispatcher-link.env"),
                                               "sk-ant-oat01-" + "s" * 95))
            check("writer-token-dispatcher-env-is-the-same-file-refused",
                  (got[0], "DISPATCHER_ENV_FILE" in got[1], "The tech lead's token goes" in got[1],
                   "s" * 40 in open(envp).read()) if isinstance(got, tuple) else got,
                  (EX_REFUSED, True, True, False))
            got = outcome(lambda: writer_token(value=wtoken,
                                               sudo=se.SudoSession(validate=lambda: 1)))
            check("no-sudo-says-why:writer-token",
                  (got[0], "NO ADMINISTRATOR ACCESS" in got[1],
                   "pipeline_bridge_setup.py writer-token" in got[1])
                  if isinstance(got, tuple) else got, (EX_NOPRIV, True, True))
            check("writer-token-program-is-tokens-in-its-own-words", outcome(lambda: (
                [w for w, _x in WRITER_TOKEN_WORDS if w not in TOKEN_WRITER_PY],
                [w for w, _x in WRITER_TOKEN_WORDS if w in WRITER_TOKEN_WRITER_PY],
                [x for _w, x in WRITER_TOKEN_WORDS if x not in WRITER_TOKEN_WRITER_PY])),
                  ([], [], []))

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
            check("install-with-the-stand-up-off-writes-no-settings",
                  os.path.exists(os.path.join(home, ".stage-e", "standup.json")), False)

            # KIT-436: the stand-up's settings, from bridge.conf. A home and root of their own,
            # so the verify cases below still see a machine with the stand-up off.
            vals = parse_conf(GOOD_CONF)[0]
            conf_on, errs_on = validate_conf(dict(vals, STANDUP="on", DM_USER_IDS="U0123456789",
                                                  STANDUP_MORNING_AT="08:30"))
            check("standup-conf-on-is-clean", errs_on, [])
            _c, errs_bad = validate_conf(dict(vals, STANDUP="maybe", STANDUP_MORNING_AT="9am",
                                              STANDUP_DAYS="mon, funday"))
            check("standup-conf-names-each-problem-by-its-key", [
                any(e.startswith("STANDUP must be on or off") for e in errs_bad),
                any(e.startswith("STANDUP_MORNING_AT must be a time of day") for e in errs_bad),
                any(e.startswith("STANDUP_DAYS must name each day") for e in errs_bad)],
                [True, True, True])
            # KIT-471: the spend keys reach the settings only when set, and are checked by key.
            conf_sp, errs_sp = validate_conf(dict(vals, STANDUP_DAY_PLAN_FILE="~/dp.json",
                                                  STANDUP_WEEKLY_DAY="fri"))
            _c, errs_sp_bad = validate_conf(dict(vals, STANDUP_USAGE_FILE="rel.json",
                                                 STANDUP_WEEKLY_DAY="sun"))
            check("standup-spend-keys-are-written-only-when-set", (
                errs_sp, "usage_file" in standup_settings(conf_sp),
                standup_settings(conf_sp).get("day_plan_file"),
                standup_settings(conf_sp).get("weekly_day"),
                sorted(set(standup_settings(conf_on)) & {"day_plan_file", "usage_file",
                                                         "weekly_day"})),
                ([], False, "~/dp.json", "fri", []))
            check("standup-spend-keys-are-named-by-conf-key", [
                any(e.startswith("STANDUP_USAGE_FILE must be an absolute path") for e in
                    errs_sp_bad),
                any(e.startswith("STANDUP_WEEKLY_DAY must be one of") for e in errs_sp_bad)],
                [True, True])
            # KIT-574: the sprint line is written only when yes, and needs the usage file.
            conf_spr, errs_spr = validate_conf(dict(vals, STANDUP_SPRINT="yes",
                                                    STANDUP_USAGE_FILE="~/usage.json"))
            _c, errs_spr_nofile = validate_conf(dict(vals, STANDUP_SPRINT="yes"))
            _c, errs_spr_bad = validate_conf(dict(vals, STANDUP_SPRINT="maybe"))
            check("standup-sprint-is-written-only-when-yes-and-named-by-its-key", (
                errs_spr, standup_settings(conf_spr).get("sprint"),
                "sprint" in standup_settings(conf_on),
                any(e.startswith("STANDUP_SPRINT needs STANDUP_USAGE_FILE") for e in
                    errs_spr_nofile),
                any(e.startswith("STANDUP_SPRINT must be yes or no") for e in errs_spr_bad)),
                ([], True, False, True, True))
            # KIT-575: the sprint plan file is written only when named, and needs the sprint line
            conf_spp, errs_spp = validate_conf(dict(vals, STANDUP_SPRINT="yes", STANDUP_USAGE_FILE="~/usage.json",
                                                    STANDUP_SPRINT_PLAN_FILE="~/.stage-e/queue/sprint-plan.json"))
            _c, errs_spp_off = validate_conf(dict(vals, STANDUP_USAGE_FILE="~/usage.json",
                                                  STANDUP_SPRINT_PLAN_FILE="~/.stage-e/queue/sprint-plan.json"))
            _c, errs_spp_rel = validate_conf(dict(vals, STANDUP_SPRINT="yes", STANDUP_USAGE_FILE="~/usage.json",
                                                  STANDUP_SPRINT_PLAN_FILE="plan.json"))
            check("standup-sprint-plan-file-is-written-only-when-named-and-needs-the-sprint-line", (
                errs_spp, standup_settings(conf_spp).get("sprint_plan_file"),
                "sprint_plan_file" in standup_settings(conf_on),
                any(e.startswith("STANDUP_SPRINT_PLAN_FILE needs STANDUP_SPRINT") for e in errs_spp_off),
                any(e.startswith("STANDUP_SPRINT_PLAN_FILE must be an absolute path") for e in errs_spp_rel)),
                ([], "~/.stage-e/queue/sprint-plan.json", False, True, True))
            # KIT-609: the health file is written only when named, and must be an absolute path
            conf_hf, errs_hf = validate_conf(dict(vals, STANDUP_HEALTH_FILE="/Users/Shared/pipeline-health/status.json"))
            _c, errs_hf_rel = validate_conf(dict(vals, STANDUP_HEALTH_FILE="status.json"))
            check("standup-health-file-is-written-only-when-named-and-must-be-absolute", (
                errs_hf, standup_settings(conf_hf).get("health_file"), "health_file" in standup_settings(conf_on),
                any(e.startswith("STANDUP_HEALTH_FILE must be an absolute path") for e in errs_hf_rel)),
                ([], "/Users/Shared/pipeline-health/status.json", False, True))
            # KIT-576: the sprint review's config is written only when named, and needs the sprint line
            conf_srv, errs_srv = validate_conf(dict(vals, STANDUP_SPRINT="yes", STANDUP_USAGE_FILE="~/usage.json",
                                                    STANDUP_SPRINT_REVIEW_CONFIG="~/.stage-e/sprint-review.json"))
            _c, errs_srv_off = validate_conf(dict(vals, STANDUP_USAGE_FILE="~/usage.json",
                                                  STANDUP_SPRINT_REVIEW_CONFIG="~/.stage-e/sprint-review.json"))
            check("standup-sprint-review-config-is-written-only-when-named-and-needs-the-sprint-line", (
                errs_srv, standup_settings(conf_srv).get("sprint_review_config"),
                "sprint_review_config" in standup_settings(conf_on),
                any(e.startswith("STANDUP_SPRINT_REVIEW_CONFIG needs STANDUP_SPRINT") for e in errs_srv_off)),
                ([], "~/.stage-e/sprint-review.json", False, True))
            _c, errs_nodm = validate_conf(dict(vals, STANDUP="on", DM_USER_IDS=""))
            check("standup-on-needs-a-dm-member",
                  any(e.startswith("STANDUP=on needs DM_USER_IDS") for e in errs_nodm), True)
            _c, errs_gap = validate_conf(dict(vals, STANDUP_MORNING_AT="09:00",
                                              STANDUP_AFTERNOON_AT="11:00"))
            check("standup-times-too-close-are-said-in-conf-terms",
                  any(e.startswith("STANDUP_AFTERNOON_AT must be more than 180 minutes after "
                                   "STANDUP_MORNING_AT") for e in errs_gap), True)
            composed_on = bridge.standup.validate_settings(standup_settings(conf_on), "x")
            check("standup-settings-are-the-stand-ups-own",
                  (composed_on["on"], composed_on["morning_at"], composed_on["afternoon_at"],
                   composed_on["days"], composed_on["bridge_config"]),
                  (True, "08:30", "15:00", bridge.standup.DEFAULTS["days"],
                   conf_on["BRIDGE_CONFIG"]))
            home_s, root_s = os.path.join(tmp, "home-standup"), os.path.join(tmp, "root-standup")
            os.makedirs(os.path.join(home_s, ".stage-e"))
            sfile = os.path.join(home_s, ".stage-e", "standup.json")
            rc, out = cap(cmd_install, conf_on, _RoleRunner(home_s, root_s), _FakeSudo(), True)
            check("install-writes-the-stand-ups-settings-as-composed",
                  (rc, os.path.exists(sfile) and open(sfile).read() == standup_bytes(conf_on),
                   bridge.standup.load_settings(sfile)["on"]), (EX_OK, True, True))
            check("verify-row-says-the-stand-up-is-on",
                  standup_row(conf_on, read_facts(_RoleRunner(home_s, root_s), conf_on)),
                  ("standup", se.ALREADY_DONE, "on: the morning rundown at 08:30 and the "
                                               "afternoon wrap at 15:00, Monday to Saturday"))
            # The one owner command: off in the conf and on in the file is shown, asked once,
            # and a no changes nothing.
            asked = []
            rc, out = cap(cmd_standup, conf, _RoleRunner(home_s, root_s), _FakeSudo(),
                          ask=lambda q: asked.append(q) or False)
            check("standup-command-shows-asks-and-a-no-changes-nothing",
                  (rc, asked, "turning the stand-up off" in out, "Nothing was changed" in out,
                   bridge.standup.load_settings(sfile)["on"]),
                  (EX_BLOCKED, ["Write it now?"], True, True, True))
            rc, out = cap(cmd_standup, conf, _RoleRunner(home_s, root_s), _FakeSudo(),
                          ask=lambda q: True)
            check("standup-command-writes-and-reads-back",
                  (rc, "Good: the stand-up is off." in out,
                   bridge.standup.load_settings(sfile)["on"]), (EX_OK, True, False))
            asked = []
            rc, out = cap(cmd_standup, conf, _RoleRunner(home_s, root_s), _FakeSudo(),
                          ask=lambda q: asked.append(q) or True)
            check("standup-command-with-nothing-to-change-asks-nothing",
                  (rc, asked, "Nothing to change: the stand-up is off (STANDUP=off)." in out),
                  (EX_OK, [], True))
            check("verify-row-says-off-and-is-skipped",
                  standup_row(conf, read_facts(_RoleRunner(home_s, root_s), conf))[:2],
                  ("standup", se.SKIPPED))
            rc, out = cap(cmd_standup, conf_on, _RoleRunner(home_s, root_s), _FakeSudo(),
                          ask=lambda q: True)
            check("standup-command-turns-it-on", (rc, "Good: the stand-up is on:" in out,
                                                  bridge.standup.load_settings(sfile)["on"]),
                  (EX_OK, True, True))
            rc, out = cap(cmd_verify, conf_on, _RoleRunner(home_s, root_s), _FakeSudo())
            check("verify-shows-the-stand-ups-row", bool(re.search(
                r"standup\s+ALREADY-DONE\s+on: the morning rundown at 08:30", out)), True)
            rc, out = cap(cmd_install, conf, _RoleRunner(home_s, root_s), _FakeSudo(), True)
            check("install-turns-a-stand-up-the-conf-has-off-off",
                  (rc, bridge.standup.load_settings(sfile)["on"]), (EX_OK, False))
            marker = AGENT_ENV_MARKERS[0]
            os.environ[marker] = "1"
            try:
                rc, out = cap(cmd_standup, conf_on, _RoleRunner(home_s, root_s), _FakeSudo(),
                              ask=lambda q: True)
            finally:
                os.environ.pop(marker, None)
            check("standup-command-refuses-in-a-claude-session",
                  (rc, bridge.standup.load_settings(sfile)["on"]), (EX_REFUSED, False))
            # A write that says it worked and did not is caught by the read-back.
            real_write = globals()["write_standup"]
            globals()["write_standup"] = lambda conf_, runner_: None
            try:
                rc, out = cap(cmd_standup, conf_on, _RoleRunner(home_s, root_s), _FakeSudo(),
                              ask=lambda q: True)
            finally:
                globals()["write_standup"] = real_write
            check("standup-command-reads-back-what-it-wrote",
                  (rc, "read back are not the ones written" in out), (EX_FAILED, True))
            # A read-back that read nothing is never "Good", on or off (Stage E on #295).
            real_facts, calls = globals()["read_facts"], {"n": 0}

            def facts_then_nothing(runner_, conf_):
                calls["n"] += 1
                return real_facts(runner_, conf_) if calls["n"] == 1 else None

            with open(sfile, "w") as fh:
                fh.write(standup_bytes(conf_on))
            globals()["read_facts"] = facts_then_nothing
            try:
                rc, out = cap(cmd_standup, conf, _RoleRunner(home_s, root_s), _FakeSudo(),
                              ask=lambda q: True)
            finally:
                globals()["read_facts"] = real_facts
            check("standup-command-never-calls-an-unread-read-back-good",
                  (rc, "Good:" in out, "could not be read back" in out),
                  (EX_UNKNOWN, False, True))
            # A settings file the stand-up cannot use is never "off": not JSON, or settings it
            # refuses, are BLOCKED, and `standup` writes them over (Stage E on #295).
            with open(sfile, "w") as fh:
                fh.write("{not json")
            row_bad = standup_row(conf, read_facts(_RoleRunner(home_s, root_s), conf))
            with open(sfile, "w") as fh:
                json.dump({"schema": bridge.standup.SETTINGS_SCHEMA, "morning_at": "9am"}, fh)
            row_refused = standup_row(conf, read_facts(_RoleRunner(home_s, root_s), conf))
            rc, out = cap(cmd_standup, conf, _RoleRunner(home_s, root_s), _FakeSudo(),
                          ask=lambda q: True)

            def on_in(path):
                try:
                    return bridge.standup.load_settings(path)["on"]
                except bridge.standup.StandupError:
                    return "unusable"
            check("standup-file-the-stand-up-cannot-use-is-blocked-and-written-over",
                  (row_bad[1], "not a file the stand-up can use (not JSON)" in row_bad[2],
                   row_refused[1], "morning_at" in row_refused[2], rc, on_in(sfile)),
                  (se.BLOCKED, True, se.BLOCKED, True, EX_OK, False))

            # verify: not loaded -> blocked; loaded with a fresh good heartbeat -> 0; a stale
            # or failing heartbeat -> failed; a dry run that fails -> failed. The machine here
            # has had its cutover (KIT-492): the dispatcher's env file names no chat app.
            dispatcher_env = os.path.join(home, "dispatcher.env")
            with open(dispatcher_env, "w") as fh:
                fh.write("CYRUS_API_KEY=k-%s\nCLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-%s\n"
                         % ("a" * 20, "d" * 95))
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
            # KIT-492: the old-paths row. Off: no retired line in bridge.conf, and the dispatcher's
            # env file names no chat app. A retired line, or the chat app's names, block, each
            # with its fix; an env file it cannot read is UNKNOWN, never off; and so, with
            # DISPATCHER_ENV_FILE unset, is a chat lane it did not check (Stage E on #494).
            def old_paths(c_, env_text=None):
                if env_text is not None:
                    with open(dispatcher_env, "w") as fh:
                        fh.write(env_text)
                return old_paths_row(c_, read_facts(_RoleRunner(home, root), c_))
            clean_env = open(dispatcher_env).read()
            retired_conf = validate_conf(r_values)[0]
            check("verify-old-paths-off-blocked-by-a-retired-line-or-the-chat-app-and-unread-is-"
                  "unknown",
                  outcome(lambda: [old_paths(conf), old_paths(retired_conf),
                                   old_paths(conf, clean_env + "SLACK_BOT_TOKEN=xoxb-%s\n"
                                             "SLACK_SIGNING_SECRET=%s\n" % ("c" * 30, "e" * 32)),
                                   old_paths(dict(conf, DISPATCHER_ENV_FILE="~/missing.env")),
                                   old_paths(dict(conf, DISPATCHER_ENV_FILE=""), clean_env),
                                   old_paths(dict(retired_conf, DISPATCHER_ENV_FILE=""))]),
                  [("old-paths", se.ALREADY_DONE, "bridge.conf has no retired line; the "
                    "dispatcher's chat lane is off: its env file names no chat app"),
                   ("old-paths", se.BLOCKED, "bridge.conf still has ASK_TECH_LEAD, "
                    "CHAT_BOT_USER_ID, TECH_LEAD_WAKE, WAKE_QUIET_HOURS, WRITER, WRITER_BIN: each "
                    "does nothing since the cutover; take those lines out"),
                   ("old-paths", se.BLOCKED, "the dispatcher's env file still names "
                    "SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, so its chat lane can still answer in "
                    "Slack: turn it off with the chat lane's card CK-C6"),
                   ("old-paths", se.UNKNOWN, "the dispatcher's env file ~/missing.env could not "
                    "be read (FileNotFoundError)"),
                   ("old-paths", se.UNKNOWN, "the dispatcher's chat lane was not checked: "
                    "DISPATCHER_ENV_FILE is not set. Set it to the dispatcher's env file, so "
                    "verify can tell whether the chat lane is off"),
                   ("old-paths", se.UNKNOWN, "the dispatcher's chat lane was not checked: "
                    "DISPATCHER_ENV_FILE is not set. Set it to the dispatcher's env file, so "
                    "verify can tell whether the chat lane is off; bridge.conf still has "
                    "ASK_TECH_LEAD, CHAT_BOT_USER_ID, TECH_LEAD_WAKE, WAKE_QUIET_HOURS, WRITER, "
                    "WRITER_BIN: each does nothing since the cutover; take those lines out")])
            # By name only: with the chat app's secret in the file as it is read, no value in
            # it reaches the facts, and the names do (Stage E on #494).
            def dumped_facts(env_text):
                with open(dispatcher_env, "w") as fh:
                    fh.write(env_text)
                try:
                    return json.dumps(read_facts(_RoleRunner(home, root), conf))
                finally:
                    with open(dispatcher_env, "w") as fh:
                        fh.write(clean_env)
            dumped = outcome(lambda: dumped_facts(clean_env + "SLACK_SIGNING_SECRET=%s\n"
                                                  % ("e" * 32)))
            check("verify-old-paths-reads-the-dispatchers-env-by-name-only",
                  ([v in dumped for v in ("e" * 32, "a" * 20, "d" * 95)],
                   "SLACK_SIGNING_SECRET" in dumped, "CYRUS_API_KEY" in dumped),
                  ([False, False, False], True, True))
            # KIT-291: the lanes row, the bridge's own check run as the job runs. Every name
            # on every team passes. A name a team lacks blocks, naming both. A board not
            # read, or a copy of the kit from before the check, is not known, and a line
            # shaped like a credential is never shown. The job's own command is unchanged,
            # so an install made before this check still verifies clean.
            def lanes_says(lanes_rc=0, lanes_out=None, lanes_err=""):
                over = {} if lanes_out is None else {"lanes_out": lanes_out}
                rr = _RoleRunner(home, root, loaded=True, lanes_rc=lanes_rc,
                                 lanes_err=lanes_err, **over)
                rc_, out_ = cap(cmd_verify, conf, rr, _FakeSudo(), now=at + 30)
                row = re.search(r"^\s*lanes\s+(\S+)\s+(.*)$", out_, re.M)
                return rc_, row and (row.group(1), row.group(2)), rr.lanes_calls
            missing = ("  PROD has no state named 'Needs Aproval'\nlanes: missing: PROD has "
                       "no state named 'Needs Aproval'\n")
            old = ("usage: pipeline_bridge.py [-h] ...\npipeline_bridge.py: error: argument "
                   "command: invalid choice: 'lanes' (choose from 'run')\n")
            got = outcome(lambda: [lanes_says()[:2], lanes_says(3, missing)[:2],
                                   lanes_says(1, "lanes: not checked: PROD's board could not "
                                                 "be read (the tracker timed out)\n")[:2],
                                   lanes_says(2, "", old)[:2],
                                   lanes_says(1, "lanes: not checked: lin_api_" + "q" * 40
                                              + "\n")[:2],
                                   lanes_says(0, "")[:2]])
            check("verify-lanes-row-passes-blocks-or-is-not-known-and-never-ok-unread",
                  got, [(EX_OK, ("ALREADY-DONE", "every HANDOFF_STATES name (Needs Approval) "
                                                 "is a state on every team (PROD)")),
                        (EX_BLOCKED, ("BLOCKED-ON-HUMAN", "PROD has no state named 'Needs "
                                      "Aproval'. Fix HANDOFF_STATES in bridge.conf (Step 9), "
                                      "then run `install --apply`")),
                        (EX_UNKNOWN, ("UNKNOWN", "HANDOFF_STATES was not checked: PROD's "
                                      "board could not be read (the tracker timed out)")),
                        (EX_UNKNOWN, ("UNKNOWN", "the role account's copy of the kit is older "
                                      "than this installer, so HANDOFF_STATES was not "
                                      "checked. Bring it up to date with `python3 "
                                      "scripts/pipeline_install.py`")),
                        (EX_UNKNOWN, ("UNKNOWN", "its last line is shaped like a credential, "
                                      "so it is not shown: read it as the role account, with "
                                      "`pipeline_bridge.py lanes`")),
                        (EX_UNKNOWN, ("UNKNOWN", "HANDOFF_STATES was not checked: the check "
                                      "printed nothing"))])
            as_job = ('set -a; . "$HOME/.stage-e/env"; set +a; exec /usr/bin/python3 '
                      '"$HOME/.stage-e/kit/scripts/pipeline_bridge.py" %s --config '
                      '"$HOME/.stage-e/bridge.json"')
            check("verify-lanes-runs-the-bridges-own-check-as-the-job-and-leaves-the-job-as-is",
                  outcome(lambda: (lanes_says()[2], job_command(conf))),
                  (["cd / && " + as_job % "lanes"], as_job % "run"))
            # KIT-270: the job's own pass held the lock for the whole wait: not a failure of
            # the job, so the row is not known, and says to run verify again. Any other exit 3
            # (a dry run with questions pending) and any other failure still read as before.
            def dry_says(rc_, text_):
                rr = _RoleRunner(home, root, loaded=True, job_rc=rc_, job_out=text_)
                code_, out_ = cap(cmd_verify, conf, rr, _FakeSudo(), now=at + 30)
                row = re.search(r"^\s*dry-run\s+(\S+)\s+(.*)$", out_, re.M)
                return code_, row and (row.group(1), row.group(2))
            busy = "FAIL: " + bridge.LOCK_BUSY_SAYS + "\n"
            check("verify-a-dry-run-that-met-the-jobs-own-pass-is-unknown-not-failed",
                  outcome(lambda: [dry_says(3, busy), dry_says(3, "bridge: 1 asked (dry run)\n"),
                                   dry_says(1, "FAIL: unexpected error: x\n"),
                                   dry_says(1, busy)]),
                  [(EX_UNKNOWN, ("UNKNOWN", "the job's own pass held the lock for the whole "
                                 "%d seconds this waited, so the dry run did not run. Run "
                                 "verify again" % bridge.LOCK_WAIT_SECONDS)),
                   (EX_FAILED, ("FAILED", "bridge: 1 asked (dry run)")),
                   (EX_FAILED, ("FAILED", "FAIL: unexpected error: x")),
                   (EX_FAILED, ("FAILED", "FAIL: " + bridge.LOCK_BUSY_SAYS))])
            # KIT-477: the app-name row, what Slack calls the app read back by the bridge's own
            # `name` command as the job runs it, against the name the conf asks for. The same
            # name passes. A different one blocks, naming both. A name not read, a copy of the
            # kit from before the command, or a line shaped like a credential is not known.
            def name_says(name_out=None, name_rc=0, name_err="", **conf_over):
                over = {} if name_out is None else {"name_out": name_out}
                rr = _RoleRunner(home, root, loaded=True, name_rc=name_rc, name_err=name_err,
                                 **over)
                c_ = validate_conf(dict(values, **conf_over))[0] if conf_over else conf
                rc_, out_ = cap(cmd_verify, c_, rr, _FakeSudo(), now=at + 30)
                row = re.search(r"^\s*app-name\s+(\S+)\s+(.*)$", out_, re.M)
                return rc_, row and (row.group(1), row.group(2)), rr.name_calls
            tech = "Tech lead " + chr(0xb7) + " todoclaw"
            old_name = ("usage: pipeline_bridge.py [-h] ...\npipeline_bridge.py: error: argument "
                        "command: invalid choice: 'name' (choose from 'run')\n")
            got = outcome(lambda: [
                name_says()[:2],
                name_says('name: Slack calls the app: "' + tech + '"\n',
                          PROJECT_NAME="todoclaw")[:2],
                name_says(PROJECT_NAME="todoclaw")[:2],
                name_says('name: Slack calls the app: "Pipeline chat"\n')[:2],
                name_says("name: not read: Slack users.info refused: missing_scope\n", 1)[:2],
                name_says("", 2, old_name)[:2],
                name_says("name: not read: lin_api_" + "q" * 40 + "\n", 1)[:2],
                name_says('name: Slack calls the app: not json\n')[:2],
                name_says("", 0)[:2]])
            check("verify-app-name-row-passes-blocks-or-is-not-known-and-never-ok-unread", got,
                  [(EX_OK, ("ALREADY-DONE", "Slack calls the app 'Tech lead', as the conf "
                                            "says")),
                   (EX_OK, ("ALREADY-DONE", "Slack calls the app %r, as the conf says" % tech)),
                   (EX_BLOCKED, ("BLOCKED-ON-HUMAN", "Slack calls the app 'Tech lead'; the "
                                 "conf says %r. Paste the manifest again to match (card CK-B1), "
                                 "or change the name in the conf: APP_NAME, or PROJECT_NAME when "
                                 "it is set" % tech)),
                   (EX_BLOCKED, ("BLOCKED-ON-HUMAN", "Slack calls the app 'Pipeline chat'; the "
                                 "conf says 'Tech lead'. Paste the manifest again to match (card "
                                 "CK-B1), or change the name in the conf: APP_NAME, or "
                                 "PROJECT_NAME when it is set")),
                   (EX_UNKNOWN, ("UNKNOWN", "the name was not read: Slack users.info refused: "
                                 "missing_scope")),
                   (EX_UNKNOWN, ("UNKNOWN", "the role account's copy of the kit is older than "
                                 "this installer, so the name was not read. Bring it up to date "
                                 "with `python3 scripts/pipeline_install.py`")),
                   (EX_UNKNOWN, ("UNKNOWN", "its last line is shaped like a credential, so it is "
                                 "not shown: read it as the role account, with "
                                 "`pipeline_bridge.py name`")),
                   (EX_UNKNOWN, ("UNKNOWN", "Slack's answer could not be read")),
                   (EX_UNKNOWN, ("UNKNOWN", "the name was not read: the check printed "
                                 "nothing"))])
            check("verify-app-name-runs-the-bridges-own-command-as-the-job",
                  outcome(lambda: name_says()[2]), ["cd / && " + as_job % "name"])
            # ACT-4: `direct-asks`, the one-yes card. Blocked while ACT=no; its dry run runs
            # the drill as the role account and changes nothing; only a typed yes after the
            # drill passes sets DIRECT_ASKS=yes and writes the job's config, read back.
            dconf = dict(conf, ACT="yes", DM_USER_IDS="U0ALICE001", AGENT_USER_NAME="Agent")
            dpath = os.path.join(tmp, "direct-asks.conf")

            def direct_card(c_, apply_it, typed="yes", drill_rc=0, missing=None):
                with open(dpath, "w") as fh:
                    fh.write("ACT=yes\n#DIRECT_ASKS=no\n")
                rr = _RoleRunner(home, root, loaded=True)
                rr.drill_rc, rr.drill_missing = drill_rc, missing
                before = open(os.path.join(home, ".stage-e", "bridge.json")).read()
                rc_, out_ = cap(cmd_direct_asks, c_, dpath, rr, _FakeSudo(), apply_it,
                                lambda _p: typed)
                after = open(os.path.join(home, ".stage-e", "bridge.json")).read()
                written = json.loads(after).get("direct_asks") if after != before else None
                if after != before:
                    with open(os.path.join(home, ".stage-e", "bridge.json"), "w") as fh:
                        fh.write(before)
                return (rc_, open(dpath).read().splitlines(), len(rr.drills), written,
                        [line for line in out_.splitlines() if line.startswith((
                            "BLOCKED", "DRY RUN", "Nothing", "FAILED", "Checked", "DIRECT_ASKS",
                            "  - "))])
            check("direct-asks-the-card-changes-nothing-without-a-typed-yes-after-the-drill",
                  [outcome(lambda: direct_card(dict(dconf, ACT="no"), True)),
                   outcome(lambda: direct_card(dconf, False)),
                   outcome(lambda: direct_card(dconf, True, typed="no")),
                   outcome(lambda: direct_card(dconf, True, drill_rc=1))],
                  [(EX_BLOCKED, ["ACT=yes", "#DIRECT_ASKS=no"], 0, None,
                    ["BLOCKED: nothing was changed.", "  - DIRECT_ASKS needs ACT=yes: with "
                     "acting off, a direct ask would say it acts and change nothing."]),
                   (EX_BLOCKED, ["ACT=yes", "#DIRECT_ASKS=no"], 1, None,
                    ["DRY RUN: nothing was changed. Run it again with --apply, and type yes."]),
                   (EX_BLOCKED, ["ACT=yes", "#DIRECT_ASKS=no"], 1, None, ["Nothing was changed."]),
                   (EX_FAILED, ["ACT=yes", "#DIRECT_ASKS=no"], 1, None,
                    ["FAILED: the drill, the direct asks' and the fixed reader's selftests in "
                     "the role account's copy of the kit, did not pass (it printed nothing). "
                     "Nothing was changed. If that copy is older than this installer, bring it "
                     "up to date with `python3 scripts/pipeline_install.py` first."])])
            # Stage E on #304: a role copy from before ACT-4 has the reader but no
            # pipeline_bridge_direct.py, and its job would refuse the DIRECT_* settings and
            # stop every pass. The drill fails there, in python3's words, and nothing is
            # written: not the conf, not the job's config.
            check("direct-asks-a-role-copy-without-the-direct-module-fails-the-drill-and-writes-"
                  "nothing",
                  outcome(lambda: direct_card(dconf, True, missing="pipeline_bridge_direct.py")),
                  (EX_FAILED, ["ACT=yes", "#DIRECT_ASKS=no"], 1, None,
                   ["FAILED: the drill, the direct asks' and the fixed reader's selftests in the "
                    "role account's copy of the kit, did not pass (/usr/bin/python3: can't open "
                    "file '/h/.stage-e/kit/scripts/pipeline_bridge_direct.py': [Errno 2] No such "
                    "file or directory). Nothing was changed. If that copy is older than this "
                    "installer, bring it up to date with `python3 scripts/pipeline_install.py` "
                    "first."]))
            check("direct-asks-a-typed-yes-turns-it-on-in-the-conf-and-the-jobs-config",
                  outcome(lambda: direct_card(dconf, True)),
                  (EX_OK, ["ACT=yes", "#DIRECT_ASKS=no", "DIRECT_ASKS=yes"], 1, True,
                   ["Checked: direct asks are on in %s and in the job's config. The job's next "
                    "pass uses them." % dpath]))
            check("direct-asks-already-on-says-so-and-changes-nothing",
                  outcome(lambda: direct_card(dict(dconf, DIRECT_ASKS="yes"), True)[:4]),
                  (EX_OK, ["ACT=yes", "#DIRECT_ASKS=no"], 0, None))
            # KIT-297: the writer row. Off, it says so and passes. On, its token's name must
            # be in the env file, and the writer writes one sample as the job would run it:
            # the job's PATH, the env file sourced as the job sources it, the job's config.
            # ── KIT-498: the tech lead's turn. Off, composed into nothing; on by default since the
            # cutover (KIT-492). On: the bridge's config takes its keys, `install --apply` writes
            # the tool server's config and the tracker key's own file as the role account, and
            # `verify` says what it found, a role copy of the kit that lacks #353 to #355
            # included. The key is never printed.
            import pipeline_tech_lead_tools as tl_tools
            tbase = dict(values, DM_USER_IDS="U0ALICE001")
            tc, terrs = validate_conf(dict(tbase, TECH_LEAD_TURN="yes", TURN_BIN=wbin))
            check("conf-TECH_LEAD_TURN-no-composes-no-turn-key",
                  outcome(lambda: [k for k in compose_config(conf)
                                   if k.startswith(("tech_lead_turn", "turn_"))]), [])

            def turn_composed():
                doc = compose_config(tc)
                job = bridge.validate_config(dict(doc, state_dir="/nonexistent-%s" % os.getpid()))
                return (terrs, sorted(k for k in doc if k.startswith(("tech_lead_turn", "turn_"))),
                        [job[k] for k in ("tech_lead_turn", "turn_bin", "turn_model",
                                          "turn_token_env", "turn_tools_config")])
            check("conf-TECH_LEAD_TURN-yes-composes-the-turn-and-the-job-takes-it",
                  outcome(turn_composed),
                  ([], ["tech_lead_turn", "turn_bin", "turn_model", "turn_token_env",
                        "turn_tools_config"],
                   [True, wbin, "sonnet", "CLAUDE_WRITER_OAUTH_TOKEN",
                    "~/.stage-e/tech-lead/tools.json"]))
            for name, over, key in (
                    ("not-yes-or-no", {"TECH_LEAD_TURN": "true"}, "TECH_LEAD_TURN"),
                    ("bin-required", {"TURN_BIN": ""}, "TURN_BIN"),
                    ("bin-a-bare-name", {"TURN_BIN": "claude"}, "TURN_BIN"),
                    ("needs-a-dm", {"DM_USER_IDS": ""}, "TECH_LEAD_TURN"),
                    ("model-a-flag", {"TURN_MODEL": "--bare"}, "TURN_MODEL"),
                    ("token-env-not-a-name", {"TURN_TOKEN_ENV": "lower"}, "TURN_TOKEN_ENV"),
                    ("token-env-the-bridge-tokens-name",
                     {"TURN_TOKEN_ENV": "BRIDGE_SLACK_BOT_TOKEN"}, "TURN_TOKEN_ENV"),
                    ("two-files-not-one", {"TURN_KEY_FILE": "~/.stage-e/tech-lead/tools.json"},
                     "TURN_TOOLS_CONFIG"),
                    ("key-file-relative", {"TURN_KEY_FILE": "tracker-key"}, "TURN_KEY_FILE"),
                    ("key-file-shell", {"TURN_KEY_FILE": "~/k$(id)"}, "TURN_KEY_FILE"),
                    ("outbox-dir-relative", {"TURN_OUTBOX_DIR": "outbox"}, "TURN_OUTBOX_DIR"),
                    ("outbox-dir-shell", {"TURN_OUTBOX_DIR": "~/o$(id)"}, "TURN_OUTBOX_DIR"),
                    ("outbox-dir-is-the-key-file",
                     {"TURN_OUTBOX_DIR": "~/.stage-e/tech-lead/tracker-key"}, "TURN_OUTBOX_DIR"),
                    ("outbox-dir-is-the-tools-config",
                     {"TURN_OUTBOX_DIR": "~/.stage-e/tech-lead/tools.json"}, "TURN_OUTBOX_DIR")):
                _c, errs = validate_conf(dict(dict(tbase, TECH_LEAD_TURN="yes", TURN_BIN=wbin),
                                              **over))
                check("conf-turn-%s-refused-once-by-its-key" % name,
                      sorted(set(e.split(" ")[0] for e in errs)), [key])

            def tools_doc():
                body = tools_config_bytes(tc)
                parsed = json.loads(body)
                cfg_path = os.path.join(tmp, "tools-config-check.json")
                with open(cfg_path, "w") as fh:
                    fh.write(body)
                loaded, errs_ = tl_tools.load_config(cfg_path)
                return (sorted(parsed), parsed["team_keys"] == split_list(tc["TEAM_KEYS"]),
                        parsed["linear_key_file"], parsed["docs_dir"], parsed["outbox_dir"],
                        errs_, "lin_api" in body,
                        [v for v in tl_tools.ACTING
                         if v not in [t["name"] for t in tl_tools.tool_specs(loaded)]])
            check("tools-config-names-the-key-file-never-the-key-and-the-outbox-and-the-server-"
                  "then-offers-every-acting-tool",
                  outcome(tools_doc),
                  (["docs_dir", "linear_key_file", "outbox_dir", "owner_user_id", "status_file",
                    "team_keys"],
                   True, "~/.stage-e/tech-lead/tracker-key", "~/.stage-e/kit/docs",
                   "~/.stage-e/tech-lead/outbox", [], False, []))

            # KIT-562: the tool server is told who the owner is, and the agent's name when there is one.
            named = dict(tc, AGENT_USER_NAME="Agent")
            named_path = os.path.join(tmp, "tools-config-named.json")
            with open(named_path, "w") as fh:
                fh.write(tools_config_bytes(named))
            named_loaded, named_errs = tl_tools.load_config(named_path)
            check("tools-config-carries-the-agent-name-only-when-set-and-the-server-reads-both",
                  ("agent_user_name" in tools_config(dict(tc, AGENT_USER_NAME="")),
                   tools_config(named)["agent_user_name"], tools_config(tc)["owner_user_id"],
                   named_errs, named_loaded["agent_user_name"], named_loaded["owner_user_id"]),
                  (False, "Agent", tc["OWNER_USER_ID"], [], "Agent", tc["OWNER_USER_ID"]))

            ROLE_ACCOUNT_FOR_TESTS = tc["ROLE_ACCOUNT"]
            thome, troot = os.path.join(tmp, "thome"), os.path.join(tmp, "troot")
            os.makedirs(os.path.join(thome, ".stage-e"))
            tenv = os.path.join(thome, ".stage-e", "env")
            tkey = "lin_api_" + "k" * 30
            ttoken = "sk-ant-oat01-" + "t" * 60

            def write_env(key=tkey, token=True):
                lines = ["NOTIFIER_TOKEN=xoxb-%s" % ("n" * 30),
                         "%s=xoxb-%s" % (tc["SLACK_TOKEN_ENV"], "s" * 30)]
                if key:
                    lines.append("%s=%s" % (tc["LINEAR_KEY_ENV"], key))
                if token:
                    lines.append("CLAUDE_WRITER_OAUTH_TOKEN=%s" % ttoken)
                with open(tenv, "w") as fh:
                    fh.write("\n".join(lines) + "\n")
                os.chmod(tenv, 0o600)
            write_env()
            tools_path = os.path.join(thome, ".stage-e", "tech-lead", "tools.json")
            keyp = os.path.join(thome, ".stage-e", "tech-lead", "tracker-key")

            def turn_install(c_, apply_it):
                return cap(cmd_install, c_, _RoleRunner(thome, troot), _FakeSudo(), apply_it)
            rc_, out_ = turn_install(tc, False)
            outp = os.path.join(thome, ".stage-e", "tech-lead", "outbox")
            check("turn-install-dry-run-says-what-it-would-write-and-writes-nothing",
                  (rc_, "tools-config" in out_ and "turn-key" in out_ and "outbox" in out_,
                   os.path.exists(tools_path), os.path.exists(keyp), os.path.exists(outp),
                   tkey in out_), (EX_BLOCKED, True, False, False, False, False))
            rc_, out_ = turn_install(tc, True)
            check("turn-install-apply-writes-the-tool-config-and-the-key-from-the-env-files-own",
                  (rc_, oct(os.stat(keyp).st_mode & 0o777), open(keyp).read() == tkey + "\n",
                   json.load(open(tools_path))["linear_key_file"],
                   oct(os.stat(tools_path).st_mode & 0o777), tkey in out_,
                   "the tool server's key file: wrote" in out_),
                  (EX_OK, "0o600", True, "~/.stage-e/tech-lead/tracker-key", "0o600", False, True))
            check("turn-install-apply-makes-the-outbox-mode-0700-as-the-role-account",
                  (os.path.isdir(outp), oct(os.stat(outp).st_mode & 0o777),
                   "the tool server's outbox: made" in out_), (True, "0o700", True))
            rc_, out_ = turn_install(tc, True)
            check("turn-install-again-has-nothing-to-change",
                  (rc_, "Nothing to change" in out_), (EX_OK, True))

            def outbox_after(mutate):
                mutate()
                rc2, out2 = turn_install(tc, True)
                return (rc2, os.path.isdir(outp) and not os.path.islink(outp),
                        oct(os.lstat(outp).st_mode & 0o777), out2)
            got = outcome(lambda: outbox_after(lambda: os.chmod(outp, 0o755)))
            check("turn-install-an-outbox-open-to-others-is-put-right",
                  (got[:3], "put right" in got[3]) if isinstance(got, tuple) else got,
                  ((EX_OK, True, "0o700"), True))
            got = outcome(lambda: outbox_after(lambda: os.rmdir(outp)))
            check("turn-install-a-missing-outbox-is-made-again",
                  (got[:3], "made" in got[3]) if isinstance(got, tuple) else got,
                  ((EX_OK, True, "0o700"), True))
            with open(os.path.join(outp, "1-x.json"), "w") as fh:
                fh.write("{}")
            rc_, out_ = turn_install(tc, True)
            check("turn-install-entries-a-turn-left-do-not-stop-it-or-get-removed",
                  (rc_, os.listdir(outp)), (EX_OK, ["1-x.json"]))
            os.remove(os.path.join(outp, "1-x.json"))
            elsewhere = os.path.join(thome, "elsewhere-outbox")
            os.makedirs(elsewhere)

            def make_link_outbox():
                os.rmdir(outp)
                os.symlink(elsewhere, outp)
            before_ = os.stat(elsewhere).st_mode & 0o777
            make_link_outbox()
            rc_, out_ = turn_install(tc, True)
            check("turn-install-an-outbox-that-is-a-link-is-refused-and-never-changed",
                  (rc_, os.path.islink(outp), os.stat(elsewhere).st_mode & 0o777 == before_,
                   "a link, a file, a folder of another account" in out_),
                  (EX_BLOCKED, True, True, True))
            os.remove(outp)
            with open(outp, "w") as fh:
                fh.write("a file")
            rc_, out_ = turn_install(tc, True)
            check("turn-install-a-file-where-the-outbox-belongs-is-refused",
                  (rc_, open(outp).read()), (EX_BLOCKED, "a file"))
            os.remove(outp)
            rc_, out_ = turn_install(tc, True)
            check("turn-install-the-outbox-is-made-once-it-is-out-of-the-way",
                  (rc_, os.path.isdir(outp)), (EX_OK, True))

            def key_after(mutate, env_key=tkey):
                mutate()
                write_env(env_key)
                rc2, out2 = turn_install(tc, True)
                return (rc2, oct(os.lstat(keyp).st_mode & 0o777), os.path.islink(keyp),
                        open(keyp).read() == (env_key or "") + "\n", tkey in out2 or "z" * 30 in out2)
            tkey2 = "lin_api_" + "z" * 30
            check("turn-install-a-changed-env-key-is-written-again",
                  outcome(lambda: key_after(lambda: None, tkey2)), (EX_OK, "0o600", False, True,
                                                                   False))
            write_env()
            turn_install(tc, True)            # in step with the env file again, so only the mode is off
            check("turn-install-a-key-file-open-to-others-is-made-0600",
                  outcome(lambda: key_after(lambda: os.chmod(keyp, 0o644))),
                  (EX_OK, "0o600", False, True, False))
            other = os.path.join(thome, "other-file")

            def make_link():
                with open(other, "w") as fh:
                    fh.write("untouched")
                os.remove(keyp)
                os.symlink(other, keyp)
            check("turn-install-a-link-is-replaced-and-never-written-through",
                  (outcome(lambda: key_after(make_link)), open(other).read()),
                  ((EX_OK, "0o600", False, True, False), "untouched"))
            before = open(keyp).read()
            write_env(key=None)
            rc_, out_ = turn_install(tc, True)
            check("turn-install-an-env-file-with-no-key-stops-plainly-and-writes-nothing",
                  (rc_, "holds no tracker key under STAGE_E_LINEAR_API_KEY" in out_,
                   "Run `token` first" in out_, open(keyp).read() == before),
                  (EX_BLOCKED, True, True, True))
            write_env(key="short")
            check("turn-install-a-key-that-is-not-one-is-no-key",
                  turn_install(tc, True)[0], EX_BLOCKED)
            write_env()

            def turn_rows_of(c_, **rkw):
                rr = _RoleRunner(thome, troot, loaded=True, **rkw)
                rc2, out2 = cap(cmd_verify, c_, rr, _FakeSudo(), now=at + 30)
                rows = dict((m.group(1), (m.group(2), m.group(3))) for m in re.finditer(
                    r"^\s*(credentials|tools-config|turn-key|outbox|turn)\s+(\S+)\s+(.*)$",
                    out2, re.M))
                return rc2, rows, rr.turns, tkey in out2 or ttoken in out2
            probe_ok = ("the turn's own folders hold no setup the CLI would load\n"
                        "the tool server started and its guide tool answered\n"
                        "the conversation resumed\n%s\n" % turn.PROBE_OK)
            got = outcome(lambda: turn_rows_of(tc, turn_out=probe_ok))
            check("verify-turn-on: the config, the key file and two real turns all pass, run as "
                  "the job", (got[1], got[2] == ["cd / && " + turn_command(tc)], got[3])
                  if isinstance(got, tuple) else got,
                  ({"credentials": ("ALREADY-DONE", "all three credentials are named"),
                    "tools-config": ("ALREADY-DONE", "the tool server's config is the composed "
                                                     "one"),
                    "turn-key": ("ALREADY-DONE", "the tool server's key file holds the tracker "
                                                 "key, mode 0600"),
                    "outbox": ("ALREADY-DONE", "the tool server's outbox is a plain folder of "
                                               "%s, mode 0700, and holds no entry a turn left"
                               % ROLE_ACCOUNT_FOR_TESTS),
                    "turn": ("ALREADY-DONE", "ok, the tool server answered, the conversation "
                                             "resumed, and a Read outside its allows was "
                                             "refused. It used a `claude setup-token` token: an "
                                             "API key is never sent")}, True, False))
            check("verify-turn-command-is-the-jobs-own-way-in", outcome(lambda: (
                turn_command(tc).startswith("PATH=%s; export PATH; " % se.DAEMON_PATH),
                'probe --config "$HOME/.stage-e/bridge.json"' in turn_command(tc),
                "def do_reach" in turn_command(tc))), (True, True, True))
            got = outcome(lambda: turn_rows_of(dict(tc, TECH_LEAD_TURN="no")))
            check("verify-turn-off-says-so-and-runs-nothing",
                  (got[1].get("turn"), "tools-config" in got[1], got[2])
                  if isinstance(got, tuple) else got,
                  (("SKIPPED", "off: TECH_LEAD_TURN is no, so a DM message gets the one line "
                               "on what the tech lead can do"), False, []))
            for name, rc_p, out_p, want in (
                    ("Anthropic not answering is UNKNOWN", 1, "fallback: a usage limit was "
                     "reached\n", ("UNKNOWN", "Anthropic could not be asked (a usage limit was "
                                   "reached), so the turn was not tried")),
                    ("a turn that ran too long is UNKNOWN, and not put on Anthropic", 1,
                     "fallback: the turn took longer than 90 seconds\n",
                     ("UNKNOWN", "the turn did not finish in time (the turn took longer than 90 "
                                 "seconds): Anthropic, the network or the tool server may be "
                                 "slow, so it is not proven. Run `verify` again")),
                    ("a missing token fails", 1, "fallback: CLAUDE_WRITER_OAUTH_TOKEN is not "
                     "set\n", ("FAILED", "CLAUDE_WRITER_OAUTH_TOKEN is not set")),
                    ("a config that does not switch it on yet is BLOCKED", 1, "fallback: the "
                     "turn is off\n", ("BLOCKED-ON-HUMAN", "the job's config does not switch the "
                                       "turn on yet: run `install --apply`")),
                    ("a resumed conversation that forgot fails", 1, "mismatch: the conversation "
                     "did not resume: the second turn did not know the word\n",
                     ("FAILED", "mismatch: the conversation did not resume: the second turn did "
                                "not know the word")),
                    ("a Read outside the allows not refused fails", 1, "mismatch: a Read outside "
                     "the allows was not refused\n", ("FAILED", "mismatch: a Read outside the "
                                                      "allows was not refused")),
                    ("a verdict that is not the probe's fails", 0, "ok\n", ("FAILED", "ok")),
                    ("a probe that printed nothing fails", 1, "", ("FAILED", "the probe printed "
                                                                   "nothing"))):
                got = outcome(lambda: turn_rows_of(tc, turn_rc=rc_p, turn_out=out_p))
                check("verify-turn-on: %s" % name,
                      got[1]["turn"] if isinstance(got, tuple) else got, want)
            got = outcome(lambda: turn_rows_of(tc, turn_out=probe_ok.replace(
                turn.PROBE_OK, turn.PROBE_OK_REPORTED)))
            check("verify-turn-on: a refusal the model only reported is ok, said as the model's "
                  "report", (got[1]["turn"][0], "the model said a Read outside its allows was "
                                                "refused, but the CLI recorded no refusal"
                             in got[1]["turn"][1]) if isinstance(got, tuple) else got,
                  ("ALREADY-DONE", True))
            got = outcome(lambda: turn_rows_of(tc, turn_rc=1, turn_out="fallback: %s\n"
                                               % ("sk-ant-oat01-" + "q" * 40)))
            check("verify-turn-on: a last line shaped like a credential is never shown",
                  (got[1]["turn"][0], "q" * 40 in got[1]["turn"][1])
                  if isinstance(got, tuple) else got, ("FAILED", False))
            # #353 to #355: the role account's copy of the kit has no turn, no tool server, or a
            # tool server from before the reach tool. Said in one line, as UNKNOWN, with the way
            # to bring it up to date: nothing is tried, and nothing fails obscurely.
            kit_scripts = os.path.join(thome, ".stage-e", "kit", "scripts")
            got = outcome(lambda: turn_rows_of(tc, turn_real=True))
            behind = (got[1]["turn"] if isinstance(got, tuple) else got)
            check("verify-turn-on: a role copy of the kit with no turn is UNKNOWN, said plainly",
                  (behind[0], behind[1].startswith("the role account's copy of the kit is older "
                                                   "than this installer"),
                   all(n in behind[1] for n in ("#353", "#354", "#355", "pipeline_install.py",
                                                "Nothing was tried"))), ("UNKNOWN", True, True))
            os.makedirs(kit_scripts)
            for name_, text_ in (("pipeline_tech_lead_turn.py", "# the turn\n"),
                                 ("pipeline_tech_lead_tools.py", "# a tool server of #353\n")):
                with open(os.path.join(kit_scripts, name_), "w") as fh:
                    fh.write(text_)
            got = outcome(lambda: turn_rows_of(tc, turn_real=True))
            check("verify-turn-on: a tool server from before the reach tool is behind too",
                  (got[1]["turn"][0], "#355" in got[1]["turn"][1]) if isinstance(got, tuple)
                  else got, ("UNKNOWN", True))
            # With the three scripts the probe needs, its real shell runs the turn's script by full
            # path, in the job's PATH and the env file's, on the job's own config. A stand-in
            # script records its arguments and the env file's variable, and says the probe's line.
            with open(os.path.join(kit_scripts, "pipeline_tech_lead_tools.py"), "w") as fh:
                fh.write("def do_reach():\n    pass\n")
            with open(os.path.join(kit_scripts, "pipeline_tech_lead_turn.py"), "w") as fh:
                fh.write("import os, sys\n"
                         "open(os.path.join(os.environ['HOME'], 'turn-seen.txt'), 'w').write(\n"
                         "    ' '.join(sys.argv[1:]) + ' ' + str('STAGE_E_LINEAR_API_KEY' in "
                         "os.environ) + '\\n')\n"
                         "print(%r)\n" % turn.PROBE_OK)
            got = outcome(lambda: turn_rows_of(tc, turn_real=True))
            seen = open(os.path.join(thome, "turn-seen.txt")).read()
            check("verify-turn-on: a role copy with the turn and the reach tool runs the probe "
                  "for real, on the job's config, in the env file's environment",
                  (got[1]["turn"][0], seen) if isinstance(got, tuple) else got,
                  ("ALREADY-DONE", "probe --config %s True\n"
                   % os.path.join(thome, ".stage-e", "bridge.json")))
            os.remove(keyp)
            os.remove(tools_path)
            got = outcome(lambda: turn_rows_of(tc, turn_out=probe_ok))
            check("verify-turn-on: a missing key file and tool config are BLOCKED, with the fix",
                  (got[1]["turn-key"], got[1]["tools-config"]) if isinstance(got, tuple) else got,
                  (("BLOCKED-ON-HUMAN", "the tool server's key file is not there: run `install "
                                        "--apply`"), ("BLOCKED-ON-HUMAN", "run `install "
                                                                          "--apply`")))
            for name, mutate, want in (
                    ("a missing outbox", lambda: os.rmdir(outp),
                     ("BLOCKED-ON-HUMAN", "the tool server's outbox is not there: run "
                                          "`install --apply`")),
                    ("an outbox open to others", lambda: (os.makedirs(outp),
                                                          os.chmod(outp, 0o755)),
                     ("BLOCKED-ON-HUMAN", "the tool server's outbox can be used by others: run "
                                          "`install --apply`")),
                    ("an outbox holding entries a turn left", lambda: (
                        os.makedirs(outp, mode=0o700),
                        [open(os.path.join(outp, "%d-x.json" % n), "w").close()
                         for n in range(2)]),
                     ("BLOCKED-ON-HUMAN", "the tool server's outbox holds 2 entries a turn "
                                          "left: the tech lead reads and empties it at its next "
                                          "turn, so run `verify` again after one")),
                    ("an outbox that is a link", lambda: os.symlink(elsewhere, outp),
                     ("BLOCKED-ON-HUMAN", "the tool server's outbox is a link, a file, a "
                                          "folder of another account, or one that can't be "
                                          "read: move it aside, or set TURN_OUTBOX_DIR to "
                                          "another folder"))):
                mutate()
                got = outcome(lambda: turn_rows_of(tc, turn_out=probe_ok))
                check("verify-turn-on: %s is BLOCKED, with the fix" % name,
                      got[1]["outbox"] if isinstance(got, tuple) else got, want)
                if os.path.islink(outp):
                    os.remove(outp)
                elif os.path.isdir(outp):
                    for n in os.listdir(outp):
                        os.remove(os.path.join(outp, n))
                    os.rmdir(outp)
            os.makedirs(outp, mode=0o700)
            write_env(token=False)
            got = outcome(lambda: turn_rows_of(tc, turn_out=probe_ok))
            check("verify-turn-on: the token's name must be in the env file, said by name",
                  got[1]["credentials"] if isinstance(got, tuple) else got,
                  ("BLOCKED-ON-HUMAN", "missing by name: CLAUDE_WRITER_OAUTH_TOKEN; run "
                                       "`writer-token`"))
            write_env()

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
            # KIT-332: with DM_USER_IDS set the DM is where the owner answers a dev, so a
            # missing pings file blocks there too, naming BRIDGE_PINGS=yes.
            dconf = dict(conf, DM_USER_IDS="U0ALICE001")
            rc, out = cap(cmd_verify, dconf, _RoleRunner(home, root, loaded=True),
                          _FakeSudo(), now=at + 30)
            check("verify-no-pings-file-blocks-in-dm-mode (KIT-332)",
                  (bool(re.search(r"pings\s+BLOCKED", out)), "BRIDGE_PINGS=yes" in out,
                   bool(re.search(r"pings\s+SKIPPED", out))), (True, True, False))
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
            # runs alone (outcome), so one that raises fails as itself.
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
                  ("SKIPPED", "off: NOTIFIER_JOB_LABEL is empty, so the tech lead watches no "
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
                     "the notifier runs every 20 s, too often for the tech lead to tell the Mac's "
                     "sleep from a quiet notifier: the tech lead's job runs once every 15 s at the "
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
                  "tech lead judges it by the plist, so until the notifier is reloaded it may say "
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
                  [("BLOCKED-ON-HUMAN", "launchd still runs the tech lead's job every 600 s, not every "
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
            # KIT-330: launchd's interval counts no time asleep, so a beat old by the clock may be on
            # time by the job's schedule. A beat carrying the awake clock is aged in awake time and
            # the row says the Mac slept; one that is stale in awake time still fails; one with no
            # clock (an older job's) is judged by the clock, as before.
            def beat_says(awake_clock, wall, awake_now, exit_=0):
                doc = {"schema": bridge.HEARTBEAT_SCHEMA, "at": "2026-10-10T12:00:00Z",
                       "exit": exit_, "dry": False, "summary": "ok"}
                if awake_clock is not None:
                    doc["awake_clock"] = awake_clock
                with open(beat, "w") as fh:
                    json.dump(doc, fh)
                saved = (hbm._awake_clock, hbm._boot_wall)
                hbm._awake_clock, hbm._boot_wall = (lambda: awake_now), (lambda: at - 10 ** 7)
                try:
                    rc_, out_ = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True),
                                    _FakeSudo(), now=at + wall)
                finally:
                    hbm._awake_clock, hbm._boot_wall = saved
                    with open(beat, "w") as fh:
                        json.dump({"schema": bridge.HEARTBEAT_SCHEMA, "at": "2026-10-10T12:00:00Z",
                                   "exit": 0, "dry": False, "summary": "ok"}, fh)
                row = re.search(r"^\s*heartbeat\s+(\S+)\s+(.*)$", out_, re.M)
                return rc_, row and (row.group(1), row.group(2))
            check("verify-heartbeat-is-aged-in-awake-time-and-a-sleep-is-named",
                  outcome(lambda: [beat_says(50000.0, 4000, 50100.0),
                                   beat_says(50000.0, 4000, 54000.0),
                                   beat_says(None, 4000, 50100.0),
                                   beat_says(50000.0, 100, 50100.0),
                                   beat_says(50000.0, 4000, 50100.0, exit_=1)]),
                  [(EX_OK, ("ALREADY-DONE", "its last pass, 100s ago in awake time (4000 s by "
                                            "the clock: the Mac slept for 65 min of that), "
                                            "exited 0")),
                   (EX_FAILED, ("FAILED", "its last pass was 4000s ago in awake time with exit "
                                          "0: ok")),
                   (EX_FAILED, ("FAILED", "its last pass was 4000s ago with exit 0: ok")),
                   (EX_OK, ("ALREADY-DONE", "its last pass, 100s ago in awake time, exited "
                                            "0")),
                   (EX_FAILED, ("FAILED", "its last pass was 100s ago in awake time (4000 s by "
                                          "the clock: the Mac slept for 65 min of that) with "
                                          "exit 1: ok"))])
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

            # ── KIT-561: instant replies. Off by default and composed into nothing. On, the manifest
            # gains Socket Mode, the DM event and the agent view; the always-on job has a plist of its
            # own; the app-level token goes in at a hidden prompt; one script (`push`) does the rest;
            # and verify judges the job's heartbeat.
            import calendar as _cal
            import time as _t
            pbase = dict(values, ASK_TECH_LEAD="yes", CHAT_BOT_USER_ID="U0CHATBOT1",
                         DM_USER_IDS="U0ALICE001", TECH_LEAD_TURN="yes", TURN_BIN=wbin)
            pvals = dict(pbase, PUSH_MODE="yes", PUSH_JOB_LABEL="com.example.bridge-push")
            pconf, perrs2 = validate_conf(pvals)
            offconf, offerrs = validate_conf(pbase)
            check("push-conf: valid on and valid off", (perrs2, offerrs), ([], []))
            check("push-conf: off composes no push key, and the same bytes as a conf from before",
                  ([k for k in compose_config(offconf) if k.startswith(("push", "stream"))],
                   config_bytes(offconf) == config_bytes(validate_conf(dict(pbase, PUSH_MODE="no"))[0])),
                  ([], True))
            check("push-conf: on composes the switch, the job's own loader accepts it, and the "
                  "token's name only when it is not the default",
                  outcome(lambda: (compose_config(pconf).get("push_mode"),
                                   "push_token_env" in compose_config(pconf),
                                   compose_config(validate_conf(dict(pvals, PUSH_TOKEN_ENV="MY_APP"))[0])
                                   .get("push_token_env"),
                                   bridge.validate_config(dict(compose_config(pconf),
                                                               state_dir="/nonexistent-%d" % os.getpid())
                                                          )["push_mode"])),
                  (True, False, "MY_APP", True))
            for name, over, needle in (
                    ("on with no label", dict(pvals, PUSH_JOB_LABEL=""), "PUSH_JOB_LABEL is required"),
                    ("the bridge's own label", dict(pvals, PUSH_JOB_LABEL="com.example.bridge"),
                     "a label another job already has"),
                    ("on without the turn", dict(pvals, TECH_LEAD_TURN="no", TURN_BIN=""),
                     "PUSH_MODE is yes, so it needs TECH_LEAD_TURN=yes"),
                    ("the bot token's name", dict(pvals, PUSH_TOKEN_ENV="BRIDGE_SLACK_BOT_TOKEN"),
                     "PUSH_TOKEN_ENV is BRIDGE_SLACK_BOT_TOKEN")):
                got = [e for e in validate_conf(over)[1] if "PUSH" in e or "push" in e]
                check("push-conf: %s is refused once, in conf terms" % name,
                      (len(got), needle in "".join(got), any("'push_" in e for e in got)),
                      (1, True, False))
            mp, mo = manifest(pconf), manifest(offconf)
            check("push-manifest: off is the manifest it always was",
                  (mo["settings"]["socket_mode_enabled"], "event_subscriptions" in mo["settings"],
                   "agent_view" in mo["features"], "assistant:write" in mo["oauth_config"]["scopes"]["bot"]),
                  (False, False, False, False))
            check("push-manifest: on turns Socket Mode on, subscribes to DM messages, makes it an agent "
                  "app, and adds the one scope that needs",
                  (mp["settings"]["socket_mode_enabled"], mp["settings"]["event_subscriptions"],
                   list(mp["features"]["agent_view"]), mp["oauth_config"]["scopes"]["bot"]
                   == sorted(set(SCOPE_CITES) | {"assistant:write"})),
                  (True, {"bot_events": ["message.im"]}, ["agent_description"], True))
            pconf_file = os.path.join(tmp, "push.conf")
            with open(pconf_file, "w") as fh:
                fh.write("\n".join("%s=%s" % kv for kv in pvals.items()) + "\n")
            rc, out = cap(cmd_manifest, pconf_file, tty=False, err=io.StringIO())
            check("push-manifest: the manifest command prints it as JSON and nothing else",
                  (rc, json.loads(out)["features"].get("agent_view") is not None), (EX_OK, True))
            rc, out = cap(cmd_compose, pconf, pconf_file)
            rc0, out0 = cap(cmd_compose, offconf, pconf_file)
            check("push-compose: on names the always-on job, the scope and the card; off says nothing of it",
                  ("PIECE 3b" in out, "assistant:write" in out, "CK-B9" in out,
                   "PIECE 3b" in out0, "assistant:write" in out0, "CK-B9" in out0),
                  (True, True, True, False, False, False))

            # the card
            nine = cap(print_card, "CK-B9", pconf, pconf_file)[1]
            check("push-card: Slack's steps, the one script, the load and how to turn it off, with "
                  "nothing left unfilled",
                  ([n for n in ("manifest", "agent view", "can't be undone", "connections:write",
                                "xapp-", " push --conf", "PUSH_MODE=yes and PUSH_JOB_LABEL",
                                "hidden prompt", "never loads a job", "launchctl enable system/com.example.bridge-push",
                                "launchctl bootstrap system /Library/LaunchDaemons/"
                                "com.example.bridge-push.plist", "verify",
                                "launchctl bootout system/com.example.bridge-push",
                                "PUSH_MODE=no") if n not in nine], "{" in nine), ([], False))
            check("push-card: it is a card a person can ask for by name",
                  "CK-B9" in CARDS, True)

            with tempfile.TemporaryDirectory() as ptmp:
                ph, pr = os.path.join(ptmp, "home"), os.path.join(ptmp, "root")
                os.makedirs(os.path.join(ph, ".stage-e", "state"))
                penv = os.path.join(ph, ".stage-e", "env")
                with open(penv, "w") as fh:
                    fh.write("STAGE_E_LINEAR_API_KEY=lin_api_%s\nBRIDGE_SLACK_BOT_TOKEN=xoxb-%s\n"
                             "CLAUDE_WRITER_OAUTH_TOKEN=sk-ant-oat01-%s\n" % ("k" * 30, "b" * 30,
                                                                             "w" * 40))
                os.chmod(penv, 0o600)
                # Built, so no token-shaped literal stands in the source.
                XAPP = "xapp" + "-1-A0APPLEVEL-" + "1234567890-" + "f" * 30
                XOTHER = "xapp" + "-1-A0OTHER000-" + "2" * 30

                # push-token: refused under a model, needs a terminal, takes only an app-level token
                os.environ[AGENT_ENV_MARKERS[0]] = ""
                r = _RoleRunner(ph, pr)
                check("push-token: refused under a model, and ran nothing",
                      (cap(cmd_push_token, pconf, r, _FakeSudo(), lambda _p: XAPP, True)[0],
                       r.reads + r.writes), (EX_REFUSED, []))
                os.environ.pop(AGENT_ENV_MARKERS[0])
                check("push-token: needs a terminal",
                      cap(cmd_push_token, pconf, r, _FakeSudo(), lambda _p: XAPP, False)[0], EX_BLOCKED)
                for name, value in (("the bot token", "xoxb-" + "z" * 30), ("nonsense", "hello"),
                                    ("shell after it", XAPP + " $(id)")):
                    rc, out = cap(cmd_push_token, pconf, r, _FakeSudo(), lambda _p, v=value: v, True)
                    check("push-token: %s is refused and nothing is written" % name,
                          (rc, "BRIDGE_PUSH_APP_TOKEN" in open(penv).read(), XAPP in out),
                          (EX_USAGE, False, False))
                rc, out = cap(cmd_push_token, dict(pconf, DISPATCHER_ENV_FILE=""), r, _FakeSudo(),
                              lambda _p: XAPP, True)
                written = open(penv).read()
                check("push-token: a good one is written under PUSH_TOKEN_ENV, mode 600, other names "
                      "kept, and its value is never printed",
                      (rc, "BRIDGE_PUSH_APP_TOKEN=%s" % XAPP in written,
                       "BRIDGE_SLACK_BOT_TOKEN=" in written, oct(os.stat(penv).st_mode & 0o777),
                       XAPP in out, "(%d characters)" % len(XAPP) in out),
                      (EX_OK, True, True, "0o600", False, True))
                with open(penv, "a") as fh:
                    fh.write("OTHER_TOKEN=%s\n" % XOTHER)
                rc, out = cap(cmd_push_token, dict(pconf, DISPATCHER_ENV_FILE=""), r, _FakeSudo(),
                              lambda _p: XOTHER, True)
                check("push-token: a token another name already holds is refused, in the app-level "
                      "token's words", (rc, "The app-level token is its own credential" in out),
                      (EX_REFUSED, True))
                with open(penv, "w") as fh:
                    fh.write("BRIDGE_PUSH_APP_TOKEN=xoxb-%s\n" % ("q" * 30))
                rc, out = cap(cmd_push_token, dict(pconf, DISPATCHER_ENV_FILE=""), r, _FakeSudo(),
                              lambda _p: XAPP, True)
                check("push-token: a name that holds another credential is left as it is",
                      (rc, "q" * 30 in open(penv).read(), "not an app-level token" in out),
                      (EX_REFUSED, True, True))
                with open(penv, "w") as fh:
                    fh.write("STAGE_E_LINEAR_API_KEY=lin_api_%s\nBRIDGE_SLACK_BOT_TOKEN=xoxb-%s\n"
                             "CLAUDE_WRITER_OAUTH_TOKEN=sk-ant-oat01-%s\n" % ("k" * 30, "b" * 30,
                                                                             "w" * 40))

                # push-probe: one connection as the role account, with the job's env and config
                r = _RoleRunner(ph, pr)
                rc, out = cap(cmd_push_probe, pconf, r, _FakeSudo())
                check("push-probe: a hello is a pass, run as the role account with the env file "
                      "sourced and the job's config", (rc, "probe: Slack said hello after 0.4 seconds" in out,
                                                       len(r.push_probes),
                                                       'pipeline_bridge_push.py" probe --config'
                                                       in r.push_probes[0], "$HOME/.stage-e/env"
                                                       in r.push_probes[0]),
                      (EX_OK, True, 1, True, True))
                for name, kw, want_rc, needle in (
                        ("no hello", dict(push_rc=1, push_out="push probe: Slack did not say hello "
                                                              "within 10 seconds\n"), EX_FAILED,
                         "FAILED: Slack did not say hello within 10 seconds"),
                        ("an older role copy", dict(push_rc=2, push_out="", push_err="usage: x\n"),
                         EX_UNKNOWN, "has no push probe"),
                        ("a line shaped like a credential", dict(push_rc=1, push_out="push probe: "
                                                                 "xoxb-%s\n" % ("s" * 30)), EX_FAILED,
                         "shaped like a credential")):
                    rc, out = cap(cmd_push_probe, pconf, _RoleRunner(ph, pr, **kw), _FakeSudo())
                    check("push-probe: %s" % name, (rc, needle in out, "s" * 30 in out),
                          (want_rc, True, False))
                rc, out = cap(cmd_push_probe, pconf, _RoleRunner(
                    ph, pr, push_rc=2, push_out="push probe: could not start: $BRIDGE_PUSH_APP_TOKEN "
                                                "is unset\n"), _FakeSudo())
                check("push-probe: exit 2 with the probe's own line (a token not set yet) is its answer, "
                      "said as it is, and not taken for a role copy that lacks the probe",
                      (rc, "could not start: $BRIDGE_PUSH_APP_TOKEN is unset" in out,
                       "no push probe" in out), (EX_FAILED, True, False))
                check("push-probe: with PUSH_MODE=no there is nothing to probe",
                      cap(cmd_push_probe, offconf, _RoleRunner(ph, pr), _FakeSudo())[0], EX_USAGE)

                # install writes the always-on job's plist, turned off; verify judges it
                rc, out = cap(cmd_install, pconf, _RoleRunner(ph, pr), _FakeSudo(), False)
                check("push-install: a dry run names the plist it would write and writes nothing",
                      (rc, "push-plist" in out, os.path.exists(pr + push_setup.plist_path(pconf))),
                      (EX_BLOCKED, True, False))
                rc, out = cap(cmd_install, pconf, _RoleRunner(ph, pr), _FakeSudo(), True)
                ppl = pr + push_setup.plist_path(pconf)
                check("push-install: --apply writes it as the composed one, kept alive, disabled, and "
                      "loads nothing",
                      (rc, os.path.exists(ppl) and open(ppl).read() == push_setup.render_plist(
                          pconf, ph, _xml_escape, resolve),
                       "<key>Disabled</key><true/>" in open(ppl).read(),
                       "<key>KeepAlive</key><true/>" in open(ppl).read(), "launchctl" in out
                       and "not loaded" in out),
                      (EX_OK, True, True, True, True))
                rc, out = cap(cmd_install, offconf, _RoleRunner(ph, pr), _FakeSudo(), False)
                check("push-install: with it off the always-on job's plist is not asked about",
                      "push-plist" in out, False)

                # verify: the push rows
                def push_rows_of(conf_, beat_text=None, beat_dir=False, at=None, **kw):
                    beat_path = os.path.join(ph, ".stage-e", "state", push_setup.HEARTBEAT_FILE)
                    for gone in (beat_path,):
                        if os.path.isdir(gone):
                            os.rmdir(gone)
                        elif os.path.exists(gone):
                            os.remove(gone)
                    if beat_dir:
                        os.makedirs(beat_path)
                    elif beat_text is not None:
                        with open(beat_path, "w") as fh:
                            fh.write(beat_text)
                    now_ = at or _cal.timegm(_t.strptime("2026-11-10T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
                    rc_, out_ = cap(cmd_verify, conf_, _RoleRunner(ph, pr, loaded=True, **kw),
                                    _FakeSudo(), now=now_)
                    rows_ = dict((ln.split()[0], ln.split(None, 2)[1:]) for ln in out_.splitlines()
                                 if ln.strip().startswith("push"))
                    return rc_, rows_, now_

                def stamp_(t):
                    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(t))

                def beat_json(now_, **over):
                    doc = {"schema": push_setup.HEARTBEAT_SCHEMA, "at": stamp_(now_ - 20),
                           "awake_clock": 5.0, "connected": True, "last_heard_at": stamp_(now_ - 40),
                           "error": None, "handled": 2, "reconnects": 0}
                    doc.update(over)
                    return json.dumps(doc)
                _rc, rows_off, now_p = push_rows_of(offconf)
                check("push-verify: off is one row that says so", rows_off.get("push"),
                      ["SKIPPED", "off (PUSH_MODE=no): the pass answers once a minute"])
                with open(penv, "a") as fh:
                    fh.write("BRIDGE_PUSH_APP_TOKEN=%s\n" % XAPP)
                _rc, rows_on, _n = push_rows_of(pconf, beat_json(now_p))
                check("push-verify: files in place and a fresh, connected, recently heard beat passes",
                      (rows_on["push-files"][0], rows_on["push"][0]), ("ALREADY-DONE", "ALREADY-DONE"))
                for name, beat_text, beat_dir, want in (
                        ("down", beat_json(now_p, connected=False, error="could not connect: down"),
                         False, "FAILED"),
                        ("too old", beat_json(now_p, at=stamp_(now_p - 900)), False, "FAILED"),
                        ("silent too long", beat_json(now_p, last_heard_at=stamp_(now_p - 3000)),
                         False, "FAILED"),
                        ("not written yet", None, False, "BLOCKED-ON-HUMAN"),
                        ("corrupt", "{not json", False, "UNKNOWN"),
                        ("a folder where the file belongs", None, True, "UNKNOWN")):
                    rc_, rows_, _n = push_rows_of(pconf, beat_text, beat_dir, at=now_p)
                    check("push-verify: a heartbeat that is %s is %s, never a pass" % (name, want),
                          (rows_["push"][0], rc_ != EX_OK), (want, True))
                os.remove(ppl)
                _rc, rows_, _n = push_rows_of(pconf, beat_json(now_p), at=now_p)
                check("push-verify: a plist that is not the composed one blocks, naming the fix",
                      (rows_["push-files"][0], "install --apply" in rows_["push-files"][1]),
                      ("BLOCKED-ON-HUMAN", True))
                rc, out = cap(cmd_install, pconf, _RoleRunner(ph, pr), _FakeSudo(), True)

                # push: check, show, one yes, apply, verify (KIT-561). It sets the conf's two keys itself, writes the
                # config and the plist, prints the manifest for Slack, takes the token, checks the connection, LOADS
                # the job, waits for its heartbeat and runs verify. It never needs a hand edit.
                off_file = os.path.join(tmp, "push-off.conf")
                with open(off_file, "w") as fh:
                    fh.write("\n".join("%s=%s" % kv for kv in dict(pbase, DISPATCHER_ENV_FILE="").items())
                             + "\n")
                pcfg = os.path.join(ph, ".stage-e", "bridge.json")
                LABEL = "com.example.bridge-push"
                old_conf = open(off_file).read()


                def fresh_world():
                    for gone in (pcfg, ppl):
                        if os.path.exists(gone):
                            os.remove(gone)
                    with open(penv, "w") as fh:
                        fh.write("STAGE_E_LINEAR_API_KEY=lin_api_%s\nBRIDGE_SLACK_BOT_TOKEN=xoxb-%s\n"
                                 "CLAUDE_WRITER_OAUTH_TOKEN=sk-ant-oat01-%s\n" % ("k" * 30, "b" * 30, "w" * 40))
                    with open(off_file, "w") as fh:
                        fh.write(old_conf)


                def run_push(path, runner, yes=True, token=XAPP, tty=True, **kw):
                    asked = []
                    rc_, out_ = cap(cmd_push, validate_conf(parse_conf(open(path).read())[0])[0], path, runner,
                                    _FakeSudo(), lambda q: (asked.append(q), yes)[1], lambda _p: token, tty, **kw)
                    return rc_, out_, asked


                fresh_world()
                os.environ[AGENT_ENV_MARKERS[0]] = ""
                r = _RoleRunner(ph, pr)
                check("push: refused under a model, and ran nothing",
                      (run_push(off_file, r)[0], r.reads + r.writes, open(off_file).read() == old_conf),
                      (EX_REFUSED, [], True))
                os.environ.pop(AGENT_ENV_MARKERS[0])
                bad_file = os.path.join(tmp, "push-bad.conf")
                with open(bad_file, "w") as fh:
                    fh.write("\n".join("%s=%s" % kv for kv in dict(pbase, TECH_LEAD_TURN="no", TURN_BIN="",
                                                                    DISPATCHER_ENV_FILE="").items())
                             + "\n")
                r = _RoleRunner(ph, pr)
                rc, out, asked = run_push(bad_file, r)
                check("push: a conf instant replies cannot work with is refused before anything is asked or "
                      "touched, and says why", (rc, asked, "needs TECH_LEAD_TURN=yes" in out, r.reads + r.writes,
                                                "PUSH_MODE=yes" in open(bad_file).read()),
                      (EX_USAGE, [], True, [], False))
                r = _RoleRunner(ph, pr)
                rc, out, asked = run_push(off_file, r, yes=False)
                check("push: it shows what it will do, says plainly that the agent view cannot be undone, asks "
                      "ONE yes, and a no changes nothing",
                      (rc, len(asked), "set PUSH_MODE=yes and PUSH_JOB_LABEL=%s" % LABEL in out,
                       "cannot be undone" in out, "hidden prompt" in out, "never loads one" in out,
                       "paste the manifest" in out, open(off_file).read() == old_conf, os.path.exists(pcfg),
                       os.path.exists(ppl), r.launchctl, r.push_probes, "BRIDGE_PUSH_APP_TOKEN=" in open(penv).read()),
                      (EX_BLOCKED, 1, True, True, True, True, True, True, False, False, [], [], False))
                r = _RoleRunner(ph, pr)
                rc, out, asked = run_push(off_file, r, tty=False)
                check("push: with no terminal and the token missing it stops before it asks anything",
                      (rc, asked, open(off_file).read() == old_conf), (EX_BLOCKED, [], True))
                rc, out, asked = run_push(off_file, r, token="xoxb-" + "9" * 30)
                check("push: a token that is not an app-level token is refused with nothing of it kept (the conf, "
                      "config and plist it had already written stand: it can be run again)",
                      (rc, "9" * 30 in open(penv).read(), "PUSH_MODE=yes" in open(off_file).read(), r.launchctl),
                      (EX_USAGE, False, True, []))

                fresh_world()
                r = _RoleRunner(ph, pr)
                rc, out, asked = run_push(off_file, r)
                written = open(off_file).read()
                ENABLE = "sudo launchctl enable system/%s" % LABEL
                BOOT = "sudo launchctl bootstrap system %s" % ppl[len(pr):]
                check("push: ONE yes sets the two conf keys itself, writes the config and the plist, prints the "
                      "manifest for Slack, takes the token, opens the connection as the role account; the token is "
                      "never printed",
                      (rc, len(asked), "\nPUSH_MODE=yes\n" in "\n" + written, "PUSH_JOB_LABEL=%s" % LABEL in written,
                       os.path.exists(pcfg), os.path.exists(ppl), "BRIDGE_PUSH_APP_TOKEN=%s" % XAPP in open(penv).read(),
                       '"socket_mode_enabled": true' in out and '"agent_view"' in out, len(r.push_probes),
                       XAPP in out),
                      (EX_OK, 1, True, True, True, True, True, True, 1, False))
                check("push: it NEVER loads the job; it ends by printing the two launchctl lines exactly, in order, "
                      "after the connection check, and the verify to run",
                      (r.launchctl, [ln.strip() for ln in out.splitlines() if ln.strip().startswith("sudo launchctl")],
                       out.index("push-probe") < out.index(ENABLE) < out.index(BOOT),
                       "verify --conf %s" % off_file in out),
                      ([], [ENABLE, BOOT], True, True))

                r = _RoleRunner(ph, pr, prints={LABEL: "\tstate = running\n"})
                rc, out, asked = run_push(off_file, r)
                check("push: run again with everything in place and the job loaded it asks nothing, writes nothing, "
                      "prints no load line, and still checks the connection",
                      (asked, "Nothing to write" in out, r.launchctl, len(r.push_probes), "sudo launchctl" in out,
                       "is loaded and its plist is the composed one" in out, rc), ([], True, [], 1, False, True, EX_OK))
                r = _RoleRunner(ph, pr)
                rc, out, asked = run_push(off_file, r)
                check("push: run again with everything in place but the job not loaded it asks nothing and prints the "
                      "two lines", (asked, [ln.strip() for ln in out.splitlines() if ln.strip().startswith("sudo")]),
                      ([], [ENABLE, BOOT]))
                with open(ppl, "a") as fh:
                    fh.write("<!-- changed -->\n")
                r = _RoleRunner(ph, pr, prints={LABEL: "\tstate = running\n"})
                rc, out, asked = run_push(off_file, r)
                check("push: a plist that changed under a loaded job is rewritten and the printed lines boot it out "
                      "first, so launchd does not keep the old one; it still loads nothing itself",
                      ([ln.strip() for ln in out.splitlines() if ln.strip().startswith("sudo launchctl")], r.launchctl,
                       len(asked)),
                      (["sudo launchctl bootout system/%s" % LABEL, ENABLE, BOOT], [], 1))

                for name, kw, want, needle in (
                        ("no hello", dict(push_rc=1, push_out="push probe: Slack did not say hello within 10 "
                                                              "seconds\n"), EX_FAILED, "The job was not loaded"),
                        ("a role copy with no probe", dict(push_rc=2, push_out="", push_err="usage: x\n"),
                         EX_UNKNOWN,
                         "The job was not loaded")):
                    fresh_world()
                    r = _RoleRunner(ph, pr, **kw)
                    rc, out, asked = run_push(off_file, r)
                    check("push: when the connection does not open (%s) it is not a pass, prints no load line, and "
                          "says where to look" % name,
                          (rc, needle in out, r.launchctl, "sudo launchctl" in out, "Good" in out),
                          (want, True, [], False, False))
    finally:
        os.environ.clear()
        os.environ.update(saved)
        sys.stdin = saved_stdin

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
    parser = argparse.ArgumentParser(description="Install your tech lead: its Slack app and its job.")
    parser.add_argument("command", nargs="?",
                        choices=("manifest", "compose", "token", "writer-token", "install", "verify",
                                 "standup", "card", "direct-asks", "push-token",
                                 "push-probe", "push"))
    parser.add_argument("card_id", nargs="?")
    parser.add_argument("--conf", default=DEFAULT_CONF)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "bridge-setup-selftest-run-")
    if args.command in ("token", "writer-token", "install", "verify", "standup",
                        "direct-asks", "push-token", "push-probe", "push"):
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
    if args.command == "manifest":
        return cmd_manifest(args.conf)
    if args.command == "install":
        rc = ask_name_from_file(args.conf)
        if rc != EX_OK:
            return rc
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
    if args.command == "writer-token":
        return cmd_writer_token(conf, runner, sudo)
    if args.command == "push-token":
        return cmd_push_token(conf, runner, sudo)
    if args.command == "push-probe":
        return cmd_push_probe(conf, runner, sudo)
    if args.command == "push":
        return cmd_push(conf, args.conf, runner, sudo)
    if args.command == "install":
        return cmd_install(conf, runner, sudo, args.apply)
    if args.command == "direct-asks":
        return cmd_direct_asks(conf, args.conf, runner, sudo, args.apply)
    if args.command == "standup":
        return cmd_standup(conf, runner, sudo)
    return cmd_verify(conf, runner, sudo)


if __name__ == "__main__":
    sys.exit(main())
