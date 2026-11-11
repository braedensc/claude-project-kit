#!/usr/bin/env python3
"""The push-connection job's installer pieces (KIT-561): what switching Socket Mode on needs
that is only a decision, with nothing live in it.

    python3 scripts/pipeline_bridge_push_setup.py --selftest

pipeline_bridge_setup.py is the installer. It imports this module, so the conf, the manifest,
the always-on job's plist and the heartbeat's judging are written, and tested, in one place,
and the installer only calls them. Nothing here runs a command, reads a file of the role
account's or asks for a credential: the installer does those, as the bridge's other jobs are
done.

WHAT SWITCHING ON MEANS.  The bridge has always been one job that runs once a minute. With
PUSH_MODE=yes there is a second, pipeline_bridge_push.py, that holds Slack's push connection
(Socket Mode: an outbound WebSocket, no public address) so a DM is picked up within seconds. The
minute job stays as the catch-up. This module says:
  * the keys that switch it on (PUSH_MODE and its three companions) and what they must be;
  * what the app's manifest gains: Socket Mode on, DM messages subscribed to, and Slack's agent
    view, with the scope it needs, `assistant:write` (Slack's manifest reference, read 10 Nov: new
    apps can only use `agent_view`; an app made before keeps its messages tab and gains the thread
    style, and moving to it cannot be undone);
  * the always-on job's LaunchDaemon: kept alive, installed disabled, as the bridge's own is;
  * how the job's heartbeat is judged: a pass only when it is fresh, connected, and Slack has been
    heard from lately; down or too old is not a pass; one that cannot be read is UNKNOWN.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_stage_e_setup as se  # noqa: E402

DEFAULT_TOKEN_ENV = "BRIDGE_PUSH_APP_TOKEN"
PUSH_SCRIPT = "pipeline_bridge_push.py"
LOG_NAME = "bridge-push.log"
HEARTBEAT_FILE = "bridge-push-heartbeat.json"
HEARTBEAT_SCHEMA = "pipeline-bridge-push-heartbeat/1"
# The app-level token Slack makes (Basic Information, App-Level Tokens), with the scope
# connections:write. It is not the bot's token (xoxb-) and opens only the connection.
APP_TOKEN_RE = re.compile(r"^xapp-[A-Za-z0-9-]{20,}$")
# The job beats about every 30 seconds while it runs, so a beat older than this, in time the Mac
# was awake, is a job that is not running. Slack pings a quiet connection about every half
# minute and the job counts that as hearing from Slack, so a connection silent longer than the
# second is one that is not carrying anything.
BEAT_MAX_SECONDS = 180
HEARD_MAX_SECONDS = 300

CONF_KEYS = {
    "PUSH_MODE": ("no", "yes: a second job holds Slack's push connection (Socket Mode) so a DM is "
                        "answered within seconds; the once-a-minute job stays as the catch-up "
                        "(KIT-561). Needs TECH_LEAD_TURN=yes, PUSH_JOB_LABEL, and the app-level "
                        "token (card CK-B9). no: the pass answers once a minute, as before"),
    "PUSH_TOKEN_ENV": (DEFAULT_TOKEN_ENV, "the NAME the app-level token (xapp-) goes under, at "
                                          "`push-token`. It is not the bot token's name, nor any "
                                          "other credential's"),
    "PUSH_JOB_LABEL": ("", "the always-on job's LaunchDaemon label, reverse-DNS, its own, e.g. "
                           "com.example.bridge-push; required with PUSH_MODE=yes"),
}

AGENT_DESCRIPTION = "The project's tech lead. Ask where things stand, or about a ticket or a plan."
PUSH_SCOPE = "assistant:write"
PUSH_SCOPE_CITE = ("assistant.threads.setStatus, the status line under a DM message while it is "
                   "answered (SlackClient.set_status; KIT-560). Slack adds it when an app is made "
                   "an agent app (features.agent_view)")

# The bridge's plist, but kept alive and not on a timer. Installed DISABLED, as the bridge's is:
# launchd loads every job in /Library/LaunchDaemons at boot, so a job installed and "not
# loaded" would start at the next restart, before the person's card CK-B9.
_INTERVAL = "  <key>StartInterval</key><integer>{interval}</integer>\n"
_KEEP = ("  <key>KeepAlive</key><true/>\n"
         "  <key>ThrottleInterval</key><integer>30</integer>\n")
_RUN_AT_LOAD = "  <key>RunAtLoad</key><true/>\n"
assert se.PLIST.count(_INTERVAL) == 1 and se.PLIST.count(_RUN_AT_LOAD) == 1
PLIST = se.PLIST.replace(_INTERVAL, _KEEP).replace(_RUN_AT_LOAD,
                                                   _RUN_AT_LOAD + "  <key>Disabled</key><true/>\n")


def problems(conf):
    """The push keys, checked whether PUSH_MODE is yes or no, so a typo is said when it is made,
    not on the day it is turned on. Each is named by its conf key."""
    errors = []
    if conf["PUSH_MODE"] not in ("yes", "no"):
        errors.append("PUSH_MODE must be yes or no, got %r" % conf["PUSH_MODE"])
    name = conf["PUSH_TOKEN_ENV"]
    if not re.match(r"^[A-Z][A-Z0-9_]*$", name):
        errors.append("PUSH_TOKEN_ENV must be an env var NAME like %s, got %r"
                      % (DEFAULT_TOKEN_ENV, name))
    else:
        taken = [k for k in ("SLACK_TOKEN_ENV", "LINEAR_KEY_ENV", "WRITER_TOKEN_ENV",
                             "TURN_TOKEN_ENV") if conf.get(k) == name]
        if taken:
            errors.append("PUSH_TOKEN_ENV is %s, the name %s already uses. The app-level token "
                          "opens a connection and is a credential of its own" % (
                              name, " and ".join(taken)))
    label = conf["PUSH_JOB_LABEL"]
    if label and not se._RDNS_RE.match(label):
        errors.append("PUSH_JOB_LABEL must be reverse-DNS like com.example.bridge-push, got %r"
                      % label)
    elif label and label in (conf.get("JOB_LABEL"), conf.get("NOTIFIER_JOB_LABEL")):
        errors.append("PUSH_JOB_LABEL is %s, a label another job already has. The always-on job "
                      "needs one of its own" % label)
    if conf["PUSH_MODE"] == "yes":
        if not label:
            errors.append("PUSH_JOB_LABEL is required with PUSH_MODE=yes: the always-on job's "
                          "LaunchDaemon label, such as com.example.bridge-push")
        if conf.get("TECH_LEAD_TURN") != "yes":
            errors.append("PUSH_MODE is yes, so it needs TECH_LEAD_TURN=yes: the job exists to "
                          "answer a DM with the tech lead's turn")
    return errors


def manifest_additions(conf, doc):
    """The app's manifest with what Socket Mode and Slack's agent view need, or the same manifest
    with PUSH_MODE=no. `doc` is the manifest as it was composed; it is changed and returned."""
    if conf["PUSH_MODE"] != "yes":
        return doc
    doc["settings"]["socket_mode_enabled"] = True
    doc["settings"]["event_subscriptions"] = {"bot_events": ["message.im"]}
    doc["features"]["agent_view"] = {"agent_description": AGENT_DESCRIPTION}
    doc["oauth_config"]["scopes"]["bot"] = sorted(set(doc["oauth_config"]["scopes"]["bot"])
                                                  | {PUSH_SCOPE})
    return doc


def sh_path(value):
    return "$HOME/" + value[2:] if value.startswith("~/") else value


def job_command(conf):
    """The always-on job's shell command: the env file sourced, then the job, by full path."""
    script = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + PUSH_SCRIPT
    return ('set -a; . "%s"; set +a; exec /usr/bin/python3 "%s" run --config "%s"'
            % (sh_path(conf["ENV_FILE"]), sh_path(script), sh_path(conf["BRIDGE_CONFIG"])))


def probe_command(conf):
    """The same, for the probe: one connection, hello or why not (the job's own `probe`)."""
    script = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + PUSH_SCRIPT
    return ('set -a; . "%s"; set +a; exec /usr/bin/python3 "%s" probe --config "%s"'
            % (sh_path(conf["ENV_FILE"]), sh_path(script), sh_path(conf["BRIDGE_CONFIG"])))


def plist_path(conf):
    return "/Library/LaunchDaemons/%s.plist" % conf["PUSH_JOB_LABEL"]


def heartbeat_path(conf):
    return conf["STATE_DIR"].rstrip("/") + "/" + HEARTBEAT_FILE


def render_plist(conf, home, escape, resolve):
    """The plist, filled in. `escape` is the XML escape and `resolve` the home expansion, both
    the installer's own, so the two jobs' plists are written the same way."""
    log = os.path.dirname(resolve(conf["BRIDGE_CONFIG"], home)) + "/" + LOG_NAME
    return PLIST.format(
        label=escape(conf["PUSH_JOB_LABEL"]), account=escape(conf["ROLE_ACCOUNT"]),
        home=escape(home), path=escape(se.DAEMON_PATH), command=escape(job_command(conf)),
        interval=0, log=escape(log))


# Read by the role account: the job's heartbeat, with only the fields the judging uses.
BEAT_PY = r'''
import json, os, sys
path = os.path.expanduser(sys.argv[1])
keys = ("schema", "at", "awake_clock", "connected", "last_heard_at", "error", "handled",
        "reconnects")
try:
    doc = json.load(open(path))
except FileNotFoundError:
    print(json.dumps({"missing": True}))
    sys.exit(0)
except Exception as exc:
    print(json.dumps({"unreadable": type(exc).__name__}))
    sys.exit(0)
if not isinstance(doc, dict):
    print(json.dumps({"unreadable": "not a JSON object"}))
else:
    print(json.dumps(dict((k, doc.get(k)) for k in keys)))
'''


def _epoch(stamp):
    import calendar
    import time
    try:
        return calendar.timegm(time.strptime(stamp or "", "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def judge_beat(beat, now, awake_age, secret_hits):
    """(outcome, detail) for the job's heartbeat as BEAT_PY read it. A pass only when the beat is
    fresh in time the Mac was awake, the job says it is connected, and Slack has been heard from
    lately. Down or too old is not a pass. A beat that cannot be read, or has no readable time, is
    UNKNOWN, never a pass. `awake_age(beat_at)` is the beat's age in awake time, or None;
    `secret_hits` is the credential scan, so a line shaped like one is never shown."""
    if beat is None:
        return se.UNKNOWN, "the heartbeat could not be read: the role account's files did not answer"
    if beat.get("missing"):
        return se.BLOCKED, ("no heartbeat yet: the always-on job has not run. Card CK-B9 loads it")
    if beat.get("unreadable"):
        return se.UNKNOWN, "the heartbeat could not be read (%s)" % str(beat["unreadable"])[:60]
    if beat.get("schema") != HEARTBEAT_SCHEMA:
        return se.UNKNOWN, "the heartbeat is in a shape this installer does not know"
    at = _epoch(beat.get("at"))
    if at is None:
        return se.UNKNOWN, "the heartbeat has no readable time on it"
    age = now - at
    awake = awake_age(at, beat.get("awake_clock"))
    judged = age if awake is None else awake
    slept = ""
    if awake is not None and age - awake >= 60:
        slept = " (%d s by the clock: the Mac slept for %d min of that)" % (
            int(age), int((age - awake) // 60))
    if judged > BEAT_MAX_SECONDS:
        return se.FAILED, ("the job's last heartbeat was %ds ago%s: it is not running. Card "
                           "CK-B9 loads it" % (int(judged), slept))
    if beat.get("connected") is not True:
        why = str(beat.get("error") or "no reason given")[:120]
        if secret_hits(why):
            why = "its reason is shaped like a credential, so it is not shown"
        return se.FAILED, "the job is running but is not connected to Slack: %s" % why
    heard = _epoch(beat.get("last_heard_at"))
    if heard is None:
        return se.FAILED, "the job is connected but has heard nothing from Slack yet"
    heard_age = max(0, (now - heard) - (age - awake if awake is not None and age > awake else 0))
    if heard_age > HEARD_MAX_SECONDS:
        return se.FAILED, ("the job says it is connected, but Slack was last heard from %d "
                           "seconds ago" % int(heard_age))
    return se.ALREADY_DONE, ("connected, Slack heard from %ds ago, beat %ds ago%s, %s reconnect(s)"
                             % (int(heard_age), int(judged), slept,
                                beat.get("reconnects") if isinstance(beat.get("reconnects"), int)
                                else "?"))


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest
# ══════════════════════════════════════════════════════════════════════════════════════
def selftest():
    import json
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    base = {"PUSH_MODE": "no", "PUSH_TOKEN_ENV": DEFAULT_TOKEN_ENV, "PUSH_JOB_LABEL": "",
            "SLACK_TOKEN_ENV": "BRIDGE_SLACK_BOT_TOKEN", "LINEAR_KEY_ENV": "STAGE_E_LINEAR_API_KEY",
            "WRITER_TOKEN_ENV": "CLAUDE_WRITER_OAUTH_TOKEN",
            "TURN_TOKEN_ENV": "CLAUDE_WRITER_OAUTH_TOKEN", "JOB_LABEL": "com.example.bridge",
            "NOTIFIER_JOB_LABEL": "", "TECH_LEAD_TURN": "yes",
            "ROLE_KIT_CLONE": "~/.stage-e/kit", "ENV_FILE": "~/.stage-e/env",
            "BRIDGE_CONFIG": "~/.stage-e/bridge.json", "STATE_DIR": "~/.stage-e/state",
            "ROLE_ACCOUNT": "_exdispatch"}
    on = dict(base, PUSH_MODE="yes", PUSH_JOB_LABEL="com.example.bridge-push")

    # the keys
    check("problems: the defaults are valid", problems(base), [])
    check("problems: a valid switch-on is valid", problems(on), [])
    for name, conf, needle in (
            ("a mode that is neither", dict(base, PUSH_MODE="maybe"), "PUSH_MODE must be yes or no"),
            ("a token name in lower case", dict(base, PUSH_TOKEN_ENV="lower"),
             "PUSH_TOKEN_ENV must be an env var NAME"),
            ("the bot token's name", dict(base, PUSH_TOKEN_ENV="BRIDGE_SLACK_BOT_TOKEN"),
             "SLACK_TOKEN_ENV"),
            ("the writer's name", dict(base, PUSH_TOKEN_ENV="CLAUDE_WRITER_OAUTH_TOKEN"),
             "WRITER_TOKEN_ENV"),
            ("a label that is not reverse-DNS", dict(base, PUSH_JOB_LABEL="push"),
             "PUSH_JOB_LABEL must be reverse-DNS"),
            ("the bridge's own label", dict(base, PUSH_JOB_LABEL="com.example.bridge"),
             "a label another job already has"),
            ("the notifier's label", dict(base, PUSH_JOB_LABEL="com.example.n",
                                         NOTIFIER_JOB_LABEL="com.example.n"),
             "a label another job already has"),
            ("on with no label", dict(on, PUSH_JOB_LABEL=""), "PUSH_JOB_LABEL is required"),
            ("on without the turn", dict(on, TECH_LEAD_TURN="no"), "needs TECH_LEAD_TURN=yes")):
        found = problems(conf)
        check("problems: %s is refused, named by its conf key" % name,
              (len(found) == 1, needle in "".join(found)), (True, True))

    # the manifest
    def plain():
        return {"display_information": {"name": "x"},
                "features": {"bot_user": {"display_name": "x"}},
                "oauth_config": {"scopes": {"bot": ["chat:write", "users:read"]}},
                "settings": {"socket_mode_enabled": False}}
    check("manifest: off is the manifest it was", manifest_additions(base, plain()), plain())
    m = manifest_additions(on, plain())
    check("manifest: on turns Socket Mode on, subscribes to DM messages, makes it an agent app and "
          "adds the one scope that needs",
          (m["settings"]["socket_mode_enabled"], m["settings"]["event_subscriptions"],
           m["features"]["agent_view"], m["oauth_config"]["scopes"]["bot"]),
          (True, {"bot_events": ["message.im"]}, {"agent_description": AGENT_DESCRIPTION},
           ["assistant:write", "chat:write", "users:read"]))
    check("manifest: the agent description fits Slack's 300 characters", len(AGENT_DESCRIPTION) <= 300,
          True)
    json.dumps(m)

    # the job and its plist
    check("job: the env file sourced, the push job by full path, its config",
          job_command(on),
          'set -a; . "$HOME/.stage-e/env"; set +a; exec /usr/bin/python3 '
          '"$HOME/.stage-e/kit/scripts/pipeline_bridge_push.py" run --config '
          '"$HOME/.stage-e/bridge.json"')
    check("job: the probe is the same command with the probe verb", probe_command(on).replace(
          " probe ", " run "), job_command(on))
    rendered = render_plist(on, "/Users/_exdispatch", lambda t: t,
                            lambda v, h: h + "/" + v[2:] if v.startswith("~/") else v)
    check("plist: kept alive, installed disabled, on no timer, as the role account, with its own "
          "label and log",
          ("<key>KeepAlive</key><true/>" in rendered, "<key>Disabled</key><true/>" in rendered,
           "StartInterval" in rendered, "<string>_exdispatch</string>" in rendered,
           "<string>com.example.bridge-push</string>" in rendered,
           "/Users/_exdispatch/.stage-e/bridge-push.log" in rendered),
          (True, True, False, True, True, True))
    check("plist: where it lives and where its heartbeat is",
          (plist_path(on), heartbeat_path(on)),
          ("/Library/LaunchDaemons/com.example.bridge-push.plist",
           "~/.stage-e/state/bridge-push-heartbeat.json"))

    # the heartbeat, judged
    def stamp(t):
        import time
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    NOW = 1760000000

    def beat(**over):
        doc = {"schema": HEARTBEAT_SCHEMA, "at": stamp(NOW - 20), "awake_clock": 5.0,
               "connected": True, "last_heard_at": stamp(NOW - 40), "error": None,
               "handled": 3, "reconnects": 1}
        doc.update(over)
        return doc

    def judged(doc, awake=None):
        return judge_beat(doc, NOW, lambda at, clock: awake, lambda t: [])
    ok = judged(beat())
    check("beat: fresh, connected and recently heard is a pass",
          (ok[0], "connected, Slack heard from 40s ago, beat 20s ago, 1 reconnect(s)" == ok[1]),
          (se.ALREADY_DONE, True))
    for name, doc, want in (
            ("down", beat(connected=False, error="could not connect: down"), se.FAILED),
            ("never connected, no reason", beat(connected=False, error=None), se.FAILED),
            ("a beat too old", beat(at=stamp(NOW - 600)), se.FAILED),
            ("connected but silent too long", beat(last_heard_at=stamp(NOW - 900)), se.FAILED),
            ("connected, nothing ever heard", beat(last_heard_at=None), se.FAILED),
            ("no heartbeat yet", {"missing": True}, se.BLOCKED),
            ("one that cannot be read", {"unreadable": "JSONDecodeError"}, se.UNKNOWN),
            ("another shape", beat(schema="other/1"), se.UNKNOWN),
            ("no time on it", beat(at="never"), se.UNKNOWN),
            ("nothing answered", None, se.UNKNOWN)):
        check("beat: %s is not a pass" % name, judged(doc)[0], want)
    check("beat: only a pass is ALREADY-DONE: every other outcome is a different word",
          [judged(d)[0] for d in (beat(connected=False), {"missing": True}, None)].count(
              se.ALREADY_DONE), 0)
    slept = judged(beat(at=stamp(NOW - 3600), last_heard_at=stamp(NOW - 3600)), awake=30.0)
    check("beat: after the Mac slept an hour, a beat that is 30 awake seconds old is judged by "
          "those, and says so", (slept[0], "the Mac slept for 59 min" in slept[1]),
          (se.ALREADY_DONE, True))
    check("beat: a reason shaped like a credential is never shown",
          judge_beat(beat(connected=False, error="xoxb-secret"), NOW, lambda a, c: None,
                     lambda t: ["slack token"])[1].endswith("so it is not shown"), True)
    check("beat: the reason is cut",
          len(judged(beat(connected=False, error="x" * 500))[1]) < 220, True)

    # the source runs and loads nothing
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    body = src[:src.index("# Selftest\n")]
    check("this module starts no process, reads no file and asks for no credential",
          [w for w in ("subprocess", "getpass", "os.system", "Popen", "launchctl", "urlopen")
           if w in body], [])
    if failures:
        print("FAIL: pipeline_bridge_push_setup selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_bridge_push_setup selftest (%d cases)" % cases[0])
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    print("usage: pipeline_bridge_push_setup.py --selftest (the installer imports this module)")
    sys.exit(2)
