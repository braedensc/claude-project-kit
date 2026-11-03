#!/usr/bin/env python3
"""Move the tech lead to another model, and keep every ticket the pipeline makes on its own.

    python3 scripts/pipeline_model_switch.py --to sonnet [--backup haiku]
        [--conf <your stage-a.conf>] [--stage-e-conf <your stage-e.conf>]
        [--chat-lane-conf <your chat-lane.conf>]
    python3 scripts/pipeline_model_switch.py --selftest

Run it in a terminal, as yourself, from the kit checkout. It checks, shows, asks one yes,
applies, and verifies. Card CK-C8 (`pipeline_chat_lane_setup.py card CK-C8`) is the same
steps on paper.

WHY IT IS ONE SCRIPT (KIT-385). A chat turn runs on the dispatcher's one global pair,
`claudeDefaultModel` and `claudeDefaultFallbackModel`; no setting picks a model for chat
alone. A ticket session with no `[model=…]` tag and no model label runs on that same
model, since a repository entry's own `model` is never read (cyrus 0.2.73
RunnerSelectionService.js:297-310; KIT-377). The dispatcher works that model out again each
time a session resumes (EdgeWorker.js:5191-5247), so a ticket already in progress moves
too, at its next prompt. So moving the tech lead moves every untagged ticket with it, and
the order matters: the tickets first, then the pair.

WHAT IT DOES, IN ORDER
  1. Reads the three confs through their installers. TICKET_MODEL must be set in
     stage-a.conf and stage-e.conf: it is the `[model=…]` line the planner, the executor
     and the finding poller open each ticket with (KIT-379, KIT-380).
  2. Asks for your Mac password once. Then it reads, as the chat lane's role account, what
     the chat lane's `merge` would write, and goes on only when that is the model pair and
     nothing else. A model name in the dispatcher's env file, another chat-lane change
     waiting, or a default runner other than Claude's stops it, and it names what to do
     first. It says which tickets a tag moves, when a TICKET_MODEL is not the model they
     run on now.
  3. Reads the planner's and the finding poller's own configs, as their role accounts, and
     checks each carries its TICKET_MODEL. A conf the update never applied tags nothing.
  4. Lists the open tickets the pipeline filed that still run on the dispatcher's default:
     planning tickets (by their routing label or their opening routing tag), planned pieces
     (`provenance:epic`) and findings (`provenance:agent`, never an epic), in progress or
     not. It judges each by the dispatcher's own rule: no `[model=…]` tag, no model label,
     and no label that picks another runner. A planning run under way stops it: told its
     description changed, a planning session answers in one more turn, and the planner
     would file that answer as the plan.
  5. Shows those tickets, the line it writes in chat-lane.conf, and the pair. Asks ONE yes.
  6. Opens each ticket with `[model=<its TICKET_MODEL>]` (a planning ticket on its second
     line, under the routing tag that must open it) and reads it back; writes CHAT_MODEL
     (and CHAT_BACKUP_MODEL) into chat-lane.conf; runs the chat lane's `merge --apply`;
     reads the pair back; and runs its `verify`.

GOING BACK. `--to` set to the very model both TICKET_MODELs name (`--to opus`, where
tickets keep opus) needs no tag on any ticket: one on the default ends on its own model. It
is a match by name: an alias and a pinned id are two models even in one family, since
`opus` follows the CLI's newest Opus and a pinned id never moves. So steps 3 and 4 are skipped: it needs no Linear
key, and works while Linear is down. Taking CHAT_MODEL out is NOT going back: unset, `merge` leaves the
pair as it is, and a config key removed by hand keeps its old value in the running
dispatcher until a restart (ConfigManager.js:237-244).

WHAT IT NEVER DOES
  It never runs under a model: a session that could move the dispatcher's model could
  move its own, so it refuses in an agent environment, before it reads anything. It never
  labels a ticket or moves one, and it never prints a key: the Linear key is yours, read
  the way the idea gate's installer reads it (your environment, the review jobs' stored
  copy, or a hidden prompt), and never written anywhere. A session working on a ticket as
  it is tagged is told by the dispatcher that the description changed, and goes on; it
  runs on the tag from its next prompt. A ticket a person filed by hand is not the
  pipeline's, and is not tagged: it moves unless it carries a model label.

Exit: 0 switched: the pair reads back, and verify has run (until a chat turn has run on the
new pair, the last lines say how to see one) · 1 a step failed after your yes, or the pair
reads back and verify's chat-model row fails; the lines say which, and what was done ·
2 a setting, or how this was run, needs fixing first, or a planning run must finish
first; nothing changed · 3 refused (an agent
environment, or root); nothing changed · 4 something could not be checked; before your
yes nothing changed, and after it the lines say what was done · 5 no administrator
password; nothing changed · 10 you said no; nothing changed.
"""
import argparse
import atexit
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# The agent-env markers are IMPORTED from the dispatcher module, never copied, so every
# script in the kit means the same thing by "an agent environment".
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
# What a planning ticket is: one definition, shared with every job that skips one.
import pipeline_machine_tickets as machine  # noqa: E402

EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_NOPRIV, EX_DECLINED = \
    0, 1, 2, 3, 4, 5, 10
CHAT_LANE_SHOWN = "python3 scripts/pipeline_chat_lane_setup.py"

# The three kinds of ticket the pipeline files, and the conf whose TICKET_MODEL each keeps:
# the planner files planning tickets and, through its executor, the planned pieces; the
# finding poller files findings. The executor's epic carries provenance:agent too, and is
# never tagged: KIT-379 tags the pieces, and no session works an epic.
PLANNING, PIECE, FINDING = "planning ticket", "planned piece", "finding"
KIND_CONF = {PLANNING: "stage-a.conf", PIECE: "stage-a.conf", FINDING: "stage-e.conf"}
PIECE_LABEL, FINDING_LABEL = "provenance:epic", "provenance:agent"
EPIC_OPENING_RE = re.compile(r"^\s*\\?>\s*Epic drafted by a planning session")
# Every open state type. A started ticket is listed too: its session's next prompt runs on
# whatever model the dispatcher works out then.
OPEN_STATES = ("triage", "backlog", "unstarted", "started")
# The two live configs that must carry the tag's model, under each conf's role account.
LIVE_CONFIGS = (("stage-a.conf", "the planner job", ".stage-a/poller.json"),
                ("stage-e.conf", "the finding poller", ".stage-e/finding/poller.json"))
PAGE, MAX_PAGES = 50, 100                # 5000 open pipeline tickets: far past any backlog

# A planning ticket is found by its routing label or by the routing tag that opens it, as
# Linear may store it: either mark is enough (pipeline_machine_tickets.is_planning_ticket).
PLANNING_TAG_OPENINGS = ("[repo=" + machine.PLANNING_PREFIX, "\\[repo=" + machine.PLANNING_PREFIX)
Q_OPEN_TICKETS = """
query OpenPipelineTickets($after: String) {
  issues(first: %d, after: $after, filter: {
    or: [
      { labels: { some: { or: [ { name: { in: %s } }, { name: { startsWith: %s } } ] } } },
      { description: { startsWithIgnoreCase: %s } },
      { description: { startsWithIgnoreCase: %s } }
    ],
    state: { type: { in: %s } }
  }) {
    nodes { id identifier title description state { type } labels(first: 50) { nodes { name } } }
    pageInfo { hasNextPage endCursor }
  }
}""" % (PAGE, json.dumps([PIECE_LABEL, FINDING_LABEL]), json.dumps(machine.PLANNING_PREFIX),
        json.dumps(PLANNING_TAG_OPENINGS[0]), json.dumps(PLANNING_TAG_OPENINGS[1]),
        json.dumps(list(OPEN_STATES)))
Q_TICKET = """
query Ticket($id: String!) {
  issue(id: $id) { id identifier description state { type } labels(first: 50) { nodes { name } } }
}"""
M_DESCRIPTION = """
mutation Tag($id: String!, $description: String!) {
  issueUpdate(id: $id, input: { description: $description }) {
    success issue { id description }
  }
}"""


# --------------------------------------------------------------------------- #
# The dispatcher's own rule for a ticket's runner and model (cyrus 0.2.73,
# RunnerSelectionService.js:84-95 and 114-310), ported line for line, so a ticket is tagged
# exactly when the dispatcher would run it on Claude's default. Two settings this cannot
# read are taken at their defaults: `defaultRunner` (claude) and
# `inferOpenCodeRunnerFromProviderModel` (off). re.ASCII: JavaScript's case-insensitive
# match without the `u` flag folds ASCII letters only.
# --------------------------------------------------------------------------- #
DEFAULT_RUNNER = "claude"
_RUNNER_NAMES = {"opencode": "opencode", "cursor": "cursor", "codex": "codex",
                 "openai": "codex", "gemini": "gemini", "claude": "claude"}
_AGENT_LABELS = (("opencode", "opencode"), ("cursor", "cursor"), ("codex", "codex"),
                 ("openai", "codex"), ("gemini", "gemini"), ("claude", "claude"))
_I = re.IGNORECASE | re.ASCII
_TAG_RES = {name: re.compile(r"\\?\[%s=([a-zA-Z0-9_.:/-]+)\\?\]" % name, _I)
            for name in ("agent", "model")}
_OPENCODE_LABEL_RE = re.compile(r"^opencode/([a-z0-9_.-]+/[a-z0-9_.:/-]+)\Z", _I)
_PROVIDER_LABEL_RE = re.compile(r"^([a-z0-9_.-]+)/([a-z0-9_.:/-]+)\Z", _I)
_CODEX_RES = (re.compile(r"gpt-[a-z0-9.-]*codex\Z", _I), re.compile(r"^gpt-[a-z0-9.-]+\Z", _I))


def _is_codex_model(name):
    return bool(_CODEX_RES[0].search(name) or _CODEX_RES[1].match(name))


def _runner_from_model(model):
    """inferRunnerFromModel."""
    if not model:
        return None
    low = model.lower()
    if low.startswith("gemini"):
        return "gemini"
    if low in ("fable", "opus", "sonnet", "haiku") or low.startswith("claude"):
        return "claude"
    return "codex" if _is_codex_model(low) else None


def _provider_model_from_labels(labels):
    """resolveProviderModelFromLabel: a `<runner>/<model>` label, or `opencode/<p>/<m>`."""
    for label in labels:
        found = _OPENCODE_LABEL_RE.match(label)
        if found:
            return "opencode", found.group(1)
        found = _PROVIDER_LABEL_RE.match(label)
        if not found:
            continue
        runner = _RUNNER_NAMES.get(found.group(1))
        if runner == "opencode":
            return runner, label
        if runner:
            return runner, found.group(2)
    return None


def _model_from_labels(labels):
    """resolveModelFromLabel."""
    codex = next((label for label in labels if _is_codex_model(label)), None)
    if codex:
        return codex
    for names, model in ((("gemini-2.5-pro", "gemini-2.5"), "gemini-2.5-pro"),
                         (("gemini-2.5-flash",), "gemini-2.5-flash"),
                         (("gemini-2.5-flash-lite",), "gemini-2.5-flash-lite"),
                         (("gemini-3", "gemini-3-pro", "gemini-3-pro-preview"),
                          "gemini-3-pro-preview"),
                         (("fable",), "fable"), (("opus",), "opus"), (("sonnet",), "sonnet"),
                         (("haiku",), "haiku")):
        if any(n in labels for n in names):
            return model
    return None


def dispatcher_pick(label_names, description):
    """(runner, model) as the dispatcher picks them for a ticket: model is None when the
    ticket runs on that runner's default (determineRunnerSelection)."""
    labels = [str(n or "").lower() for n in label_names or ()]
    desc = description or ""
    found = _TAG_RES["agent"].search(desc)
    agent = _RUNNER_NAMES.get(found.group(1).lower()) if found else None
    found = _TAG_RES["model"].search(desc)
    provider = _provider_model_from_labels(labels)
    explicit = ((found.group(1) if found else None)
                or (provider[1] if provider else _model_from_labels(labels)))
    runner = (agent or (provider[0] if provider else None)
              or next((r for name, r in _AGENT_LABELS if name in labels), None)
              or _runner_from_model(explicit) or DEFAULT_RUNNER)
    implied = _runner_from_model(explicit)
    if explicit and implied and implied != runner:
        explicit = None          # an agent that disagrees with the model keeps the agent
    return runner, explicit or None


def on_the_default(label_names, description):
    """True when the dispatcher runs this ticket on Claude's default model: the one the
    switch moves."""
    return dispatcher_pick(label_names, description) == ("claude", None)


# --------------------------------------------------------------------------- #
# The tickets.
# --------------------------------------------------------------------------- #
def ticket_kind(issue):
    """PLANNING, PIECE or FINDING for a ticket the pipeline filed, else None. The epic is
    told by its own opening line, never by whether it has children yet: the executor files
    it before its pieces, and a finding may be split into sub-issues."""
    if machine.issue_is_planning_ticket(issue):
        return PLANNING
    labels = machine.label_names_of(issue)
    if PIECE_LABEL in labels:
        return PIECE
    if FINDING_LABEL in labels and not EPIC_OPENING_RE.match(issue.get("description") or ""):
        return FINDING
    return None


def tagged_body(description, model, kind):
    """The description with the tag on its first line, as the executor and the finding
    poller write it; a planning ticket's on its second, under the routing tag that must
    open it, as the planner writes it (KIT-379, KIT-380)."""
    desc, tag = description or "", "[model=%s]" % model
    if kind == PLANNING and machine.is_planning_ticket(desc):
        body = desc.lstrip()
        first, newline, rest = body.partition("\n")
        return desc[:len(desc) - len(body)] + first + "\n" + tag + (newline + rest
                                                                    if newline else "")
    return "%s\n\n%s" % (tag, desc)


def pipeline_tickets(tracker, models):
    """([(issue, kind, model)] for each open pipeline ticket on the default; how many other
    open pipeline tickets already name their own model or runner; and the identifiers of
    those in progress that name another runner. A Claude session already working on one of
    those resumes on the default whatever its labels say (RunnerConfigBuilder.js:137-142),
    and a tag cannot change that, so they are named."""
    todo, others, elsewhere, after = [], 0, [], None
    for _ in range(MAX_PAGES):
        page = (tracker.post(Q_OPEN_TICKETS, {"after": after}) or {}).get("issues")
        if not isinstance(page, dict):
            raise ValueError("the tracker's answer held no issues")
        for issue in page.get("nodes") or []:
            kind = ticket_kind(issue)
            if not kind:
                continue
            pick = dispatcher_pick(machine.label_names_of(issue), issue.get("description"))
            if pick == ("claude", None):
                todo.append((issue, kind, models[kind]))
            else:
                others += 1
                if pick[0] != "claude" and (issue.get("state") or {}).get("type") == "started":
                    elsewhere.append(issue.get("identifier"))
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return todo, others, elsewhere
        after = info.get("endCursor")
        if not after:
            raise ValueError("the tracker said there was another page, but gave no cursor")
    raise ValueError("more than %d open pipeline tickets: stopped listing them"
                     % (PAGE * MAX_PAGES))


def tag_one(tracker, issue, kind, model):
    """("tagged" | "kept" | "closed" | "running", None), or ("failed", why). Read again just
    before the write, so an edit made since the list is kept: a ticket given a model
    meanwhile keeps its own, one closed meanwhile is left as it is, and a planning ticket
    whose run started meanwhile stops the switch."""
    fresh = ((tracker.post(Q_TICKET, {"id": issue["id"]}) or {}).get("issue")) or {}
    if not fresh:
        return "failed", "it could not be read again"
    if ((fresh.get("state") or {}).get("type")) not in OPEN_STATES:
        return "closed", None
    # Read again, not from the list: a planning run that started during the yes is under
    # way, and a tag streamed into it could be filed as its plan (review of KIT-385).
    if kind == PLANNING and (fresh.get("state") or {}).get("type") == "started":
        return "running", None
    labels = machine.label_names_of(fresh)
    if not on_the_default(labels, fresh.get("description")):
        return "kept", None
    data = tracker.post(M_DESCRIPTION, {"id": issue["id"], "description": tagged_body(
        fresh.get("description"), model, kind)})
    result = (data or {}).get("issueUpdate") or {}
    stored = ((result.get("issue") or {}).get("description")) or ""
    if not result.get("success") or dispatcher_pick(labels, stored) != ("claude", model):
        return "failed", "Linear did not store the tag as written"
    if machine.is_planning_ticket(fresh.get("description")) \
            and not machine.is_planning_ticket(stored):
        return "failed", "its routing tag no longer opens it"
    return "tagged", None


def conf_with(text, values):
    """chat-lane.conf's text with each KEY=value set: the live line replaced, else a
    commented example line made live, else a line added at the end. Two live lines for one
    key are refused: which one counts is not this script's to guess."""
    lines = text.split("\n")
    for key, value in values.items():
        live = [i for i, l in enumerate(lines) if re.match(r"^\s*%s\s*=" % re.escape(key), l)]
        dead = [i for i, l in enumerate(lines)
                if re.match(r"^\s*#\s*%s\s*=" % re.escape(key), l)]
        if len(live) > 1:
            raise ValueError("chat-lane.conf sets %s on %d lines. Leave one, then run this "
                             "again" % (key, len(live)))
        if live:
            lines[live[0]] = "%s=%s" % (key, value)
        elif dead:
            lines[dead[-1]] = "%s=%s" % (key, value)
        else:
            if lines and lines[-1] == "":
                lines.insert(len(lines) - 1, "%s=%s" % (key, value))
            else:
                lines.append("%s=%s" % (key, value))
    return "\n".join(lines)


class IO(object):
    """Everything this touches outside itself, so the selftest can stand in for each."""

    def env(self):
        return os.environ

    def uid(self):
        return os.getuid()

    def is_tty(self):
        return sys.stdin.isatty()

    def say(self, text=""):
        print(text)
        sys.stdout.flush()

    def ask(self, question):
        try:
            return input(question + " [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def installers(self):
        """The three installer modules, imported only once the agent check has passed."""
        import pipeline_chat_lane_setup as chat
        import pipeline_stage_a_setup as stage_a
        import pipeline_stage_e_setup as stage_e
        return stage_a, stage_e, chat

    def sudo_cached(self):
        try:
            return subprocess.call(["sudo", "-n", "-v"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL) == 0
        except OSError:
            return False

    def sudo_session(self, chat):
        return chat.SudoSession()

    def sudo(self, chat, why, resume):
        """Administrator access ONCE, for the whole run, with this script's own reason, or
        NoPrivilege. Already held in this terminal, it is kept fresh without asking again."""
        session = self.sudo_session(chat)
        if self.sudo_cached():
            session.held = True
            session._start_keepalive()
        else:
            session.acquire(why, resume)
        atexit.register(session.release)
        return session

    def host(self, stage_a):
        return stage_a.Host()

    def tracker(self, stage_a, conf):
        key, source = stage_a.operator_key(conf, stage_a.Host(), True, True, self.say)
        return (stage_a.LinearTransport(key), source) if len(key) >= 20 else (None, source)

    def chat_runner(self, chat):
        return chat.Runner(dry_run=True)

    def now(self):
        import time
        return time.time()

    def chat_facts(self, chat, conf, runner):
        """(facts, why not): the chat lane's own read-only probe, as its role account."""
        return chat.probe_facts(runner, conf)

    def chat_command(self, chat, argv, sudo):
        """One chat-lane subcommand, run here, under this run's administrator session, so
        it asks for no password and prints no second password banner."""
        return chat.main(argv, sudo=sudo)

    def read_text(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def writable(self, path):
        """Whether write_text can replace `path`: its folder takes the temporary file, and
        the file itself, where it exists, is yours to write."""
        real = os.path.realpath(path)
        return os.access(os.path.dirname(real) or ".", os.W_OK) and (
            not os.path.exists(real) or os.access(real, os.W_OK))

    def write_text(self, path, text):
        """In place of the old file, keeping its mode: a temporary file beside it, then a
        rename, so a reader never sees half of it. Through a symlink to the file it names,
        so the link stays a link."""
        path = os.path.realpath(path)
        mode = os.stat(path).st_mode & 0o777
        tmp = "%s.model-switch.%d" % (path, os.getpid())
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


# The dispatcher's own pick when nothing names a runner: the one runner whose credential its
# environment holds, else Claude (cyrus-edge-worker 0.2.73 RunnerSelectionService.js:20-42).
# Read from the env file's names, never its values; a launchd plist's variables are not seen.
_RUNNER_CREDENTIALS = (("claude", ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")),
                       ("gemini", ("GEMINI_API_KEY",)), ("codex", ("OPENAI_API_KEY",)),
                       ("cursor", ("CURSOR_API_KEY",)))


def detected_runner(names):
    found = [r for r, keys in _RUNNER_CREDENTIALS if any(k in names for k in keys)]
    return found[0] if len(found) == 1 else "claude"


_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})[-:](\d{2})[-:](\d{2})")


def _epoch(stamp):
    """Seconds since the epoch for either stamp a chat turn's log carries, else None."""
    import calendar
    found = _STAMP_RE.match(str(stamp or ""))
    return calendar.timegm(tuple(int(x) for x in found.groups())) if found else None


def turns_since(ct, since):
    """The chat turns' facts, keeping only what was logged at or after `since`: a turn or a
    silent run from before the pair was written proves nothing about it (review of
    KIT-385). A stamp that cannot be read counts as before."""
    if not isinstance(ct, dict) or ct.get("error"):
        return ct
    fresh = [t for t in ct.get("turns") or [] if isinstance(t, dict)
             and (_epoch(t.get("at") or t.get("run")) or 0) >= since]
    silent_at = _epoch(ct.get("silentAt"))
    return dict(ct, turns=fresh,
                silent=ct.get("silent") if silent_at is not None and silent_at >= since else 0)


def _said(stage_a, exc):
    """What a tracker failure may say out loud. The installer's own errors are written to
    carry no key, so their words are printed; anything else, its kind only."""
    if isinstance(exc, (stage_a.SetupError, stage_a.Unknown)):
        return str(exc)
    return type(exc).__name__


def main(argv=None, io=None):
    io = io or IO()
    parser = argparse.ArgumentParser(
        prog="pipeline_model_switch.py",
        description="Move the tech lead to another model, keeping every ticket the "
                    "pipeline makes on its own (KIT-385).")
    parser.add_argument("--to", help="the model chat turns move to: fable, opus, sonnet, "
                                     "haiku or a claude- model id")
    parser.add_argument("--backup", default="",
                        help="its backup; left out, the backup the dispatcher picks for "
                             "that model's alias, by family")
    parser.add_argument("--conf", default=os.path.join(KIT, "stage-a.conf"),
                        help="your stage-a.conf (default: the one in the kit checkout)")
    parser.add_argument("--stage-e-conf", default=os.path.join(KIT, "stage-e.conf"),
                        help="your stage-e.conf (default: the one in the kit checkout)")
    parser.add_argument("--chat-lane-conf", default=os.path.join(KIT, "chat-lane.conf"),
                        help="your chat-lane.conf (default: the one in the kit checkout)")
    parser.add_argument("--selftest", action="store_true")
    shown = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    say = io.say
    again = "python3 %s %s" % (os.path.relpath(os.path.abspath(__file__), KIT),
                               " ".join(shlex.quote(a) for a in shown))

    # 0. Never under a model, never as root, only with a person at the terminal.
    found = [m for m in AGENT_ENV_MARKERS if m in io.env()]
    if found:
        say("REFUSED: this shell belongs to a Claude session (%s is set). Moving the "
            "dispatcher's model from one would let a session choose its own model. Open the "
            "Terminal app, or this app's Terminal tab, and run it there." % ", ".join(found))
        return EX_REFUSED
    if io.uid() == 0:
        say("REFUSED: do not run this with sudo. Run it as yourself; it asks for your Mac "
            "password when it needs it.")
        return EX_REFUSED
    if not io.is_tty():
        say("This asks you one question before it changes anything, so it runs only in a "
            "terminal. Nothing was changed.")
        return EX_USAGE
    if not args.to:
        say("Name the model chat turns move to: --to sonnet, or the model tickets keep to go "
            "back. Nothing was changed.")
        return EX_USAGE
    stage_a, stage_e, chat = io.installers()

    # 1. The three confs, each through its own installer.
    paths = {"stage-a.conf": (os.path.abspath(args.conf), "--conf"),
             "stage-e.conf": (os.path.abspath(args.stage_e_conf), "--stage-e-conf"),
             "chat-lane.conf": (os.path.abspath(args.chat_lane_conf), "--chat-lane-conf")}
    problems, confs = [], {}
    for name, loader in (("stage-a.conf", stage_a.load_conf), ("stage-e.conf",
                                                                stage_e.load_conf),
                         ("chat-lane.conf", chat.load_conf)):
        path, flag = paths[name]
        if not os.path.isfile(path):
            problems.append("%s: there is none at %s. Pass the path to yours: %s <your %s>"
                            % (name, path, flag, name))
            continue
        try:
            confs[name], errors = loader(path)
        except stage_e.SetupError as exc:          # Stage E's raises on a file it cannot read
            confs[name], errors = None, [str(exc).split("\n")[0]]
        except (OSError, UnicodeDecodeError) as exc:
            confs[name], errors = None, ["it could not be read (%s)" % (
                getattr(exc, "strerror", None) or type(exc).__name__)]
        problems += ["%s: %s" % (name, e) for e in errors]
    a_conf, e_conf, c_conf = (confs.get(n) or {} for n in ("stage-a.conf", "stage-e.conf",
                                                            "chat-lane.conf"))
    c_path = paths["chat-lane.conf"][0]
    # Left out, --backup means the dispatcher's pick, so a backup already in the conf is
    # cleared: the file then holds exactly the pair checked here.
    want = dict(c_conf, CHAT_MODEL=args.to, CHAT_BACKUP_MODEL=args.backup)
    problems += ["--to/--backup: " + e for e in chat.chat_model_problems(want)]
    kept = {"stage-a.conf": a_conf.get("TICKET_MODEL") or "",
            "stage-e.conf": e_conf.get("TICKET_MODEL") or ""}
    for name in ("stage-a.conf", "stage-e.conf"):
        if name in confs and confs[name] and not kept[name]:
            problems.append("%s: TICKET_MODEL is not set. Set it to the model tickets keep "
                            "(the one they run on now), run the update (pipeline_install.py), "
                            "then this again. Without it, the tickets move with the tech "
                            "lead" % name)
    if problems:
        say("Nothing was changed. Fix these first:")
        for p in problems:
            say("  - " + p)
        return EX_USAGE
    models = dict((kind, kept[KIND_CONF[kind]]) for kind in KIND_CONF)
    # Going back: --to is, by name, the model both TICKET_MODELs give their tickets, so a
    # ticket on the default ends on its own model with no tag, and the ticket checks are
    # skipped. An alias and a pinned id are two names even in one family: `opus` follows the
    # CLI's newest Opus, and a pinned id never moves (review of KIT-385).
    going_back = all(chat.bare_model(kept[n]) == chat.bare_model(args.to)
                     for n in ("stage-a.conf", "stage-e.conf"))
    pair = chat.chat_pair_wanted(want)
    say("Moving the tech lead to %s, with %s as the backup." % pair)
    if chat.same_model(*pair):
        say("  Note: %s and %s are in one family, so they may be one model, and the backup "
            "may back up nothing." % pair)

    # 2. Administrator access once; then what the chat lane's merge would write.
    try:
        sudo = io.sudo(chat, "the switch reads the chat lane's, the planner's and the finding "
                             "poller's settings as their role accounts, and the chat lane's "
                             "merge writes the dispatcher's model pair as its account",
                       " ".join(shlex.quote(a) for a in shown))
    except chat.NoPrivilege as exc:
        say(str(exc).replace("python3 %s %s" % (chat._stage_e_self_path(),
                                                " ".join(shlex.quote(a) for a in shown)),
                             again))
        say("")
        say("The command to run again is this one:  %s" % again)
        return EX_NOPRIV
    runner = io.chat_runner(chat)
    facts, why_not = io.chat_facts(chat, want, runner)
    if facts is None:
        say("NOT CHECKED: the chat lane's read-only probe as %s did not run (%s). Nothing was "
            "changed." % (c_conf.get("ROLE_ACCOUNT"), why_not))
        return EX_UNKNOWN
    cfg = facts.get("config") or {"error": "missing"}
    if cfg.get("error"):
        say("NOT CHECKED: the dispatcher config %s is %s. Nothing was changed."
            % (c_conf.get("DISPATCHER_CONFIG"), cfg["error"]))
        return EX_UNKNOWN
    env_error = (facts.get("env") or {}).get("error")
    if env_error and env_error != "missing":
        say("NOT CHECKED: the dispatcher's env file could not be read (%s), so whether it sets "
            "the model or the runner is not known. Nothing was changed." % env_error)
        return EX_UNKNOWN
    _line, pair_op, why = chat.model_pair_plan(want, facts)
    if why:
        say("Nothing was changed: %s." % why)
        return EX_USAGE
    runner_set = cfg.get("defaultRunner") or None
    env_names = set((facts.get("env") or {}).get("names") or [])
    picked = detected_runner(env_names)
    if "CYRUS_DEFAULT_RUNNER" in env_names:
        runner_why = ("set by CYRUS_DEFAULT_RUNNER in its env file",
                      "Take CYRUS_DEFAULT_RUNNER out of the env file")
    elif runner_set not in (None, "claude"):
        runner_why = ("%r in its config" % runner_set,
                      "Set defaultRunner to claude in its config, or take it out")
    elif runner_set is None and picked != "claude":
        runner_why = ("%s, the dispatcher's own pick: its env file holds that runner's "
                      "credential and no Claude one" % picked,
                      "Set defaultRunner to claude in its config")
    else:
        runner_why = None
    if not going_back and runner_why:
        # A tag names a Claude model, so on a ticket that runs on another runner today it
        # would move that ticket to Claude (RunnerSelectionService.js:20-42, 297-310). The
        # runner is fixed when the dispatcher starts, and a config reload keeps it, so the
        # change takes a restart (review of KIT-385).
        say("Nothing was changed: the dispatcher's default runner is %s, so a [model=...] tag "
            "could move a ticket to Claude. %s, restart the dispatcher when it is idle (card "
            "CK-C5), then run this again." % runner_why)
        return EX_USAGE
    current = chat.chat_model_pair(cfg.get("models"))[0]
    if going_back:
        say("TICKET_MODEL is %s in both stage-a.conf and stage-e.conf, the model chat turns go "
            "to, so a ticket on the default ends on it with no tag, and the ticket checks are "
            "skipped." % kept["stage-a.conf"])
    else:
        for name, what in (("stage-a.conf", "Planning tickets and planned pieces"),
                           ("stage-e.conf", "Findings")):
            if chat.bare_model(kept[name]) == chat.bare_model(current):
                say("%s keep %s." % (what, kept[name]))
            else:
                # TICKET_MODEL is meant to be the model they run on now; when it is not, a
                # tag moves them, and that is said before the yes (review of KIT-385).
                say("%s are tagged %s, but those with no tag run on %s today, so the tag "
                    "moves them to %s." % (what, kept[name], current, kept[name]))
    plan, ops, deny_add, allow_add = chat.merge_plan(want, facts)
    if [o for o in ops if o.get("op") != "model-pair"] or deny_add or allow_add:
        say("Nothing was changed: other chat-lane changes are waiting, besides the model "
            "pair, and a yes about the model must not write them unseen:")
        for line in plan:
            if line[:12].strip() in ("GRANT", "DROP", "FENCE", "DENY", "ALLOW READ"):
                say("    " + line)
        say("Run the chat lane's merge on its own, read what it writes, and apply it:")
        say("    %s merge --conf %s" % (CHAT_LANE_SHOWN, shlex.quote(c_path)))
        say("    %s merge --apply --conf %s" % (CHAT_LANE_SHOWN, shlex.quote(c_path)))
        say("Then run this again.")
        return EX_USAGE

    todo, others, elsewhere, tracker = [], 0, [], None
    if not going_back:
        # 3. The live configs carry the model: what the update applied, not only the conf.
        host = io.host(stage_a)
        for (name, what, relpath) in LIVE_CONFIGS:
            role = confs[name]["ROLE_ACCOUNT"]
            text = host.read_role_file(role, relpath)
            update = ("python3 scripts/pipeline_install.py --conf %s --stage-e-conf %s"
                      % (shlex.quote(paths["stage-a.conf"][0]),
                         shlex.quote(paths["stage-e.conf"][0])))
            if text is None:
                say("NOT CHECKED: %s's config (~%s/%s) could not be read as %s. Nothing was "
                    "changed. Run `sudo -v`, then this again." % (what, role, relpath, role))
                return EX_UNKNOWN
            if text == "":
                say("Nothing was changed: %s is not installed for %s (there is no ~%s/%s). "
                    "Run the update, then this again:  %s" % (what, role, role, relpath,
                                                             update))
                return EX_USAGE
            try:
                live = json.loads(text)
            except ValueError:
                live = None
            if not isinstance(live, dict):
                say("NOT CHECKED: %s's config (~%s/%s) is not a JSON object. Nothing was "
                    "changed." % (what, role, relpath))
                return EX_UNKNOWN
            if live.get("ticket_model") != kept[name]:
                say("Nothing was changed: %s still runs without TICKET_MODEL=%s (its config "
                    "says %s). Run the update, then this again:  %s"
                    % (what, kept[name], repr(live["ticket_model"]) if live.get("ticket_model")
                       else "no ticket_model", update))
                return EX_USAGE

        # 4. The open pipeline tickets still on the default.
        tracker, source = io.tracker(stage_a, a_conf)
        if tracker is None:
            say("NOT CHECKED: no Linear key (%s). Nothing was changed. Export yours under the "
                "name stage-a.conf gives OPERATOR_KEY_ENV, then run this again." % source)
            return EX_UNKNOWN
        try:
            todo, others, elsewhere = pipeline_tickets(tracker, models)
        except Exception as exc:  # noqa: BLE001 - a tracker failure is reported, never a traceback
            say("NOT CHECKED: the open tickets could not be listed (%s). Nothing was changed."
                % _said(stage_a, exc))
            return EX_UNKNOWN

    # A planning session told its description changed answers in one more turn, and the
    # planner files the newest answer as the plan, so a run under way is never tagged.
    running = [i.get("identifier") for i, kind, _m in todo
               if kind == PLANNING and (i.get("state") or {}).get("type") == "started"]
    if running:
        say("Nothing was changed: a planning run is under way on %s. Tagging its ticket now "
            "could hand its session a note it answers in place of its plan. Run this again "
            "once the planner has closed it, or close it by hand if the planner says it could "
            "not." % ", ".join(running))
        return EX_USAGE

    # 5. Everything it will do, then one yes.
    say("")
    say("It will:")
    if going_back:
        say("  - tag no ticket: none moves when chat turns go back to %s" % args.to)
    elif todo:
        say("  - open %d pipeline ticket(s) with their model tag, so each keeps its model:"
            % len(todo))
        for issue, kind, model in todo:
            started = (issue.get("state") or {}).get("type") == "started"
            say("      %s  [model=%s]  %s, %s: %s" % (
                issue.get("identifier"), model, kind,
                "in progress" if started else "not started", (issue.get("title") or "")[:50]))
        if any((i.get("state") or {}).get("type") == "started" for i, _k, _m in todo):
            say("    A session working on one of those right now is told the description "
                "changed, and goes on; it runs on the tag from its next prompt.")
    else:
        say("  - tag no ticket: no open pipeline ticket runs on the default")
    if others:
        say("    %d other open pipeline ticket(s) already name their model or another runner, "
            "and are left as they are." % others)
    if elsewhere:
        say("    In progress and naming another runner: %s. A Claude session already working "
            "on one resumes on the default, which no tag changes: look at %s by hand."
            % (", ".join(elsewhere), "it" if len(elsewhere) == 1 else "them"))
    clear = not args.backup and c_conf.get("CHAT_BACKUP_MODEL")
    say("  - set CHAT_MODEL=%s%s in %s" % (
        args.to, ", CHAT_BACKUP_MODEL=%s" % args.backup if args.backup else
        ", and clear CHAT_BACKUP_MODEL (the dispatcher's pick)" if clear else "", c_path))
    if pair_op:
        say("  - run the chat lane's  merge --apply , which writes claudeDefaultModel = %s and "
            "claudeDefaultFallbackModel = %s in one write, read the pair back, then run its  "
            "verify" % pair)
    else:
        say("  - leave the dispatcher's pair alone, since it already holds %s with %s as the "
            "backup, and run the chat lane's  verify" % pair)
    if not io.writable(c_path):
        say("Nothing was changed: you cannot write %s, where the switch sets CHAT_MODEL after "
            "your yes (it writes a temporary file beside it, then renames it). Fix that, then "
            "run this again." % c_path)
        return EX_USAGE
    say("")
    if not io.ask("Move the tech lead to %s now?" % args.to):
        say("Nothing was changed.")
        return EX_DECLINED

    # 6. Apply, in that order: the tickets, the conf, the pair.
    for issue, kind, model in todo:
        try:
            status, why = tag_one(tracker, issue, kind, model)
        except Exception as exc:  # noqa: BLE001
            status, why = "failed", "the tracker call failed (%s)" % _said(stage_a, exc)
        if status == "failed":
            say("STOPPED at %s: %s. The tickets before it are tagged; chat-lane.conf and the "
                "dispatcher are unchanged. Run this again." % (issue.get("identifier"), why))
            return EX_FAILED
        if status == "running":
            say("STOPPED at %s: its planning run started since the list was made, and tagging "
                "it now could hand its session a note it answers in place of its plan. The "
                "tickets before it are tagged; chat-lane.conf and the dispatcher are unchanged. "
                "Run this again once the planner has closed it, or close it by hand if the "
                "planner says it could not." % issue.get("identifier"))
            return EX_USAGE
        say("  %s" % {"tagged": "tagged %s with [model=%s]" % (issue.get("identifier"), model),
                      "kept": "left %s: it was given its own model since the list was made"
                              % issue.get("identifier"),
                      "closed": "left %s: it was closed since the list was made"
                                % issue.get("identifier")}[status])
    values = {"CHAT_MODEL": args.to}
    if args.backup or clear:
        values["CHAT_BACKUP_MODEL"] = args.backup
    old = io.read_text(c_path)
    try:
        new = conf_with(old, values)
    except ValueError as exc:
        say("STOPPED: %s. The tickets are tagged; the dispatcher is unchanged." % exc)
        return EX_FAILED
    try:
        io.write_text(c_path, new)
    except OSError as exc:
        say("STOPPED: chat-lane.conf could not be written (%s). The tickets are tagged; "
            "chat-lane.conf and the dispatcher are unchanged." % (exc.strerror or exc))
        return EX_FAILED
    new_conf, errors = chat.load_conf(c_path)
    if errors or chat.chat_pair_wanted(new_conf) != pair:
        try:
            io.write_text(c_path, old)
            put = "so it was put back"
        except OSError as exc:
            put = "and it could not be put back (%s): fix it by hand" % (exc.strerror or exc)
        say("STOPPED: chat-lane.conf did not read back as that pair after the edit, %s. The "
            "tickets are tagged; the dispatcher is unchanged." % put)
        return EX_FAILED
    say("  set %s in %s" % (", ".join("%s=%s" % kv for kv in values.items()), c_path))
    verify_shown = "%s verify --conf %s" % (CHAT_LANE_SHOWN, shlex.quote(c_path))
    merged, wrote_at = None, io.now()
    if pair_op:
        try:
            merged = io.chat_command(chat, ["merge", "--apply", "--conf", c_path], sudo)
        except Exception as exc:  # noqa: BLE001
            say("  the chat lane's merge stopped (%s)" % type(exc).__name__)
    after, why_not = io.chat_facts(chat, new_conf, runner)
    if after is None:
        say("NOT CHECKED: the pair could not be read back (%s). The tickets are tagged and "
            "chat-lane.conf names the pair. To see whether it took, run:  %s"
            % (why_not, verify_shown))
        return EX_UNKNOWN
    row = chat.check_model_pair(new_conf, after)
    if row["outcome"] == chat.UNKNOWN:
        say("NOT CHECKED: the pair could not be read back (%s). The tickets are tagged and "
            "chat-lane.conf names the pair. To see whether it took, run:  %s"
            % (row["detail"], verify_shown))
        return EX_UNKNOWN
    if row["outcome"] != chat.ALREADY_DONE:
        say("STOPPED: the pair %s%s: %s. The tickets are tagged and chat-lane.conf names the "
            "pair; merge's lines above say why. Fix that, then run this again."
            % ("can't be written" if row["outcome"] == chat.BLOCKED else "did not take",
               " (merge exited %s)" % merged if merged is not None else "", row["detail"]))
        return EX_FAILED
    try:
        checked = io.chat_command(chat, ["verify", "--conf", c_path], sudo)
    except Exception as exc:  # noqa: BLE001
        checked = None
        say("  the chat lane's verify stopped (%s)" % type(exc).__name__)
    acfg = after.get("config") or {}
    # Only what was logged since this run wrote the pair counts as proof of it. verify's own
    # chat-model row reads every logged run, those before the write too, so the two are
    # named apart (review of KIT-385).
    ct = turns_since(after.get("chatTurns"), wrote_at) if pair_op else after.get("chatTurns")
    turn = chat.check_chat_model(acfg, after.get("env"), ct, new_conf)
    whole = (chat.check_chat_model(acfg, after.get("env"), after.get("chatTurns"), new_conf)
             if pair_op else turn)
    seen = [t.get("model") for t in ((ct or {}).get("turns") or [] if isinstance(ct, dict)
                                     else []) if isinstance(t, dict)
            and t.get("model") not in (None, "", "unreadable")]
    said = "the chat-model check of the chat runs since this switch wrote the pair"
    say("")
    if turn["outcome"] == chat.FAILED:
        say("Switched: chat turns start on %s, with %s as the backup. But %s FAILS: %s. Do "
            "what verify's lines above say." % (pair + (said, turn["detail"])))
        return EX_FAILED
    if (turn["outcome"] == chat.ALREADY_DONE and seen
            and chat.same_model(seen[0], pair[0]) is True):
        say("Done: chat turns start on %s, with %s as the backup, and a chat turn since then "
            "ran on it." % pair)
    else:
        say("Switched: chat turns now start on %s, with %s as the backup. %s: %s. Ask the bot "
            "one question in a NEW thread, then run:" % (pair + (said[0].upper() + said[1:],
                                                                 turn["detail"])))
        say("    %s" % verify_shown)
    if checked is not None and checked not in (chat.EX_OK, chat.EX_UNKNOWN):
        if whole["outcome"] == chat.FAILED:
            say("verify exited %s: its chat-model row fails on chat runs from before this "
                "switch wrote the pair (%s). Those ran on the old pair. Once a question in a "
                "new thread is answered, run verify again." % (checked, whole["detail"]))
        else:
            say("verify exited %s for rows that are not about the model: its rows above say "
                "what." % checked)
    return EX_OK


# --------------------------------------------------------------------------- #
# Selftest. Every outside call is faked: no terminal, no sudo, no tracker, no child runs.
# --------------------------------------------------------------------------- #
def selftest():
    import tempfile
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    tmp = tempfile.mkdtemp(prefix="model-switch-selftest-")
    try:
        return _selftest_cases(check, tmp, failures, cases)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _issue(identifier, labels, description="", state="backlog"):
    return {"id": "id-" + identifier, "identifier": identifier, "title": "t " + identifier,
            "description": description, "state": {"type": state},
            "labels": {"nodes": [{"name": n} for n in labels]}}


class _FakeTracker(object):
    """Linear, as far as the switch asks it: each answer is built from the filter and the
    fields the query's own TEXT names, so a filter or a field dropped from a query fails
    here as it would against the tracker."""

    def __init__(self, issues, store_as=None, page=2, fail=None, paging="ok"):
        self.issues = dict((i["id"], i) for i in issues)
        self.order = [i["id"] for i in issues]
        self.updates, self.page, self.store_as, self.fail = [], page, store_as, fail
        self.queries, self.paging = [], paging

    @staticmethod
    def _project(issue, selection):
        out = {}
        for key, pattern in (("id", r"\bid\b"), ("identifier", r"\bidentifier\b"),
                             ("title", r"\btitle\b"), ("description", r"\bdescription\b"),
                             ("state", r"\bstate\s*\{\s*type\s*\}"),
                             ("labels", r"\blabels(\([^)]*\))?\s*\{\s*nodes\s*\{\s*name\s*\}")):
            found = re.search(pattern, selection)
            if found:
                out[key] = json.loads(json.dumps(issue[key]))
                if key == "labels":
                    # A page holds only as many labels as the query asks for.
                    first = re.search(r"first:\s*(\d+)", found.group(1) or "")
                    out[key]["nodes"] = out[key]["nodes"][:int(first.group(1)) if first else 50]
        return out

    def post(self, query, variables=None):
        variables = variables or {}
        self.queries.append(query)
        if self.fail:
            raise self.fail
        if "query OpenPipelineTickets" in query:
            states = json.loads(re.search(r"state:\s*\{\s*type:\s*\{\s*in:\s*(\[[^\]]*\])",
                                          query).group(1))
            names = json.loads(re.search(r"name:\s*\{\s*in:\s*(\[[^\]]*\])", query).group(1))
            prefix = json.loads(re.search(r"name:\s*\{\s*startsWith:\s*(\"[^\"]*\")",
                                          query).group(1))
            if not re.search(r"labels:\s*\{\s*some:\s*\{\s*or:", query):
                raise AssertionError("the label filter is not an `or` of the two")
            if not re.search(r"filter:\s*\{\s*or:\s*\[", query):
                raise AssertionError("the listing is not an `or` of labels and opening tags")
            # Linear's startsWithIgnoreCase, as the real query asks: any case of the opening.
            openings = [json.loads(o).lower() for o in re.findall(
                r"description:\s*\{\s*startsWithIgnoreCase:\s*(\"(?:[^\"\\\\]|\\\\.)*\")",
                query)]
            if self.paging == "endless":
                return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": True,
                                                             "endCursor": "more"}}}
            start = int(variables.get("after") or 0)
            ids = self.order[start:start + self.page]
            selection = query.split("nodes {", 1)[1]
            nodes = [self._project(i, selection) for i in (self.issues[x] for x in ids)
                     if i["state"]["type"] in states
                     and (any(l["name"] in names or l["name"].startswith(prefix)
                              for l in i["labels"]["nodes"])
                          or any(i["description"].lower().startswith(o) for o in openings))]
            more = start + self.page < len(self.order)
            return {"issues": {"nodes": nodes, "pageInfo": {
                "hasNextPage": more, "endCursor": None if self.paging == "no-cursor"
                else str(start + self.page)}}}
        if "query Ticket(" in query:
            issue = self.issues.get(variables["id"])
            selection = query.split("issue(id: $id)", 1)[1]
            return {"issue": self._project(issue, selection) if issue else None}
        if "mutation Tag" in query:
            if not re.search(r"issueUpdate\(id: \$id, input: \{ description: \$description \}\)"
                             r"\s*\{\s*success issue \{ id description \}", query):
                raise AssertionError("the tag mutation does not ask for its read-back")
            issue = self.issues[variables["id"]]
            stored = (self.store_as or (lambda d: d))(variables["description"])
            issue["description"] = stored
            self.updates.append((issue["identifier"], variables["description"]))
            return {"issueUpdate": {"success": True, "issue": {"id": issue["id"],
                                                               "description": stored}}}
        raise AssertionError("unexpected query")


class _FakeHost(object):
    def __init__(self, files):
        self.files, self.reads = files, []

    def read_role_file(self, account, relpath):
        self.reads.append((account, relpath))
        return self.files.get((account, relpath))


class _FakeLane(object):
    """The dispatcher as the chat lane's probe sees it, and the chat lane's merge and verify
    against it: merge writes the pair chat-lane.conf names unless the env file overrides it."""

    def __init__(self, chat, conf, models=None, env_names=(), grant=None, merge_writes=True,
                 merge_rc=0, verify_rc=4, turns=None, probe_fails=0, silent=0,
                 silent_at="2026-11-02T12-05-00-000Z", runner=None, config_error=None,
                 error_after_merge=None, env_after_merge=(), merge_raises=False,
                 chat_turns=None):
        self.chat, self.conf = chat, conf
        self.models = dict(models or {"claudeDefaultModel": "opus",
                                      "claudeDefaultFallbackModel": "sonnet"})
        self.env_names, self.merge_writes = list(env_names), merge_writes
        self.grant = chat.owner_grant([]) if grant is None else grant
        self.merge_rc, self.verify_rc, self.turns = merge_rc, verify_rc, turns or []
        self.probe_fails, self.silent, self.ran = probe_fails, silent, []
        self.silent_at, self.runner, self.config_error = silent_at, runner, config_error
        self.error_after_merge, self.env_after_merge = error_after_merge, list(env_after_merge)
        self.merge_raises, self.chat_turns = merge_raises, chat_turns

    def facts(self, conf):
        if self.probe_fails:
            self.probe_fails -= 1
            return None, "exit 1: synthetic"
        merged = "merge" in self.ran
        error = self.error_after_merge if merged else self.config_error
        config = ({"error": error} if error else
                  {"entries": [], "models": dict(self.models), "defaultRunner": self.runner,
                   "slackAllowedTools": list(self.grant)})
        return {"config": config,
                "env": {"names": list(self.env_names) + (self.env_after_merge if merged
                                                         else []), "empty": []},
                "userSettings": {"path": "~/.claude/settings.json",
                                 "deny": self.chat.user_deny_patterns(conf), "allowRead": [],
                                 "trust": {}},
                "chatTurns": self.chat_turns or {"turns": list(self.turns),
                                                 "silent": self.silent,
                                                 "silentAt": self.silent_at}}, None

    def command(self, argv, conf_path):
        self.ran.append(argv[0])
        if argv[0] == "merge" and self.merge_raises:
            raise RuntimeError("synthetic")
        if argv[0] == "merge":
            conf = self.chat.load_conf(conf_path)[0]
            pair = self.chat.chat_pair_wanted(conf)
            if self.merge_writes and pair and not [
                    n for n in self.env_names if n in self.chat.MODEL_ENV_NAMES]:
                self.models.update(claudeDefaultModel=pair[0],
                                   claudeDefaultFallbackModel=pair[1])
            return self.merge_rc
        return self.verify_rc


class _FakeIO(IO):
    NOW = 1793620800                 # 2026-11-02T12:00:00Z: the moment the pair is written

    def __init__(self, tracker, host, lane, env=None, uid=501, tty=True, answer=True,
                 key_found=True, sudo_fails=False, write_fails=False, not_writable=False):
        self._tracker, self._host, self.lane, self._env = tracker, host, lane, env or {}
        self.not_writable = not_writable
        self._uid, self._tty, self.answer, self.sudo_fails = uid, tty, answer, sudo_fails
        self.out, self.asked, self.key_found, self.why = [], [], key_found, None
        self.loads, self.write_fails = 0, write_fails

    def installers(self):
        self.loads += 1
        return IO.installers(self)

    def now(self):
        return self.NOW

    def write_text(self, path, text):
        if self.write_fails:
            raise OSError(13, "Permission denied")
        return IO.write_text(self, path, text)

    def writable(self, path):
        return not self.not_writable and IO.writable(self, path)

    def env(self):
        return self._env

    def uid(self):
        return self._uid

    def is_tty(self):
        return self._tty

    def say(self, text=""):
        self.out.append(text)

    def ask(self, question):
        self.asked.append(question)
        return self.answer

    def sudo(self, chat, why, resume):
        self.why = why
        if self.sudo_fails:
            raise chat.NoPrivilege("NO ADMINISTRATOR ACCESS — synthetic.\n      python3 %s %s"
                                   % (chat._stage_e_self_path(), resume))
        return "held"

    def host(self, stage_a):
        return self._host

    def tracker(self, stage_a, conf):
        return (self._tracker, "$TEST") if self.key_found else (None, "no key in $TEST")

    def chat_runner(self, chat):
        return None

    def chat_facts(self, chat, conf, runner):
        return self.lane.facts(conf)

    def chat_command(self, chat, argv, sudo):
        if sudo != "held":
            raise AssertionError("a chat-lane command ran without this run's sudo session")
        return self.lane.command(argv, argv[argv.index("--conf") + 1])

    def text(self):
        return "\n".join(self.out)


def _selftest_cases(check, tmp, failures, cases):
    import contextlib
    import io as io_module
    import pipeline_chat_lane_setup as chat
    import pipeline_stage_a_setup as stage_a
    import pipeline_stage_e_setup as stage_e

    # Three real confs, written from each installer's own example, so the switch reads them
    # through the same validation a person's would get.
    def conf_from(example, out, overrides):
        with open(os.path.join(KIT, example), encoding="utf-8") as fh:
            text = conf_with(fh.read(), overrides)
        path = os.path.join(tmp, out)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, 0o640)
        return path

    a_path = conf_from("stage-a.conf.example", "stage-a.conf", {"TICKET_MODEL": "opus"})
    e_path = conf_from("stage-e.conf.example", "stage-e.conf",
                       {"TICKET_MODEL": "claude-opus-5-5",
                        "OWNER_LINEAR_EMAIL": "owner@example.invalid",
                        "REVIEW_REPOS": "acme/widgets"})
    c_path = conf_from("chat-lane.conf.example", "chat-lane.conf", {})
    for loader, path in ((stage_a.load_conf, a_path), (stage_e.load_conf, e_path),
                         (chat.load_conf, c_path)):
        check("example-conf-valid:%s" % os.path.basename(path), loader(path)[1], [])
    a_role = stage_a.load_conf(a_path)[0]["ROLE_ACCOUNT"]
    e_role = stage_e.load_conf(e_path)[0]["ROLE_ACCOUNT"]
    c_conf = chat.load_conf(c_path)[0]
    live = {(a_role, ".stage-a/poller.json"): json.dumps({"ticket_model": "opus"}),
            (e_role, ".stage-e/finding/poller.json"): json.dumps(
                {"ticket_model": "claude-opus-5-5"})}
    base = ["--to", "sonnet", "--conf", a_path, "--stage-e-conf", e_path,
            "--chat-lane-conf", c_path]
    c_before = open(c_path, encoding="utf-8").read()
    plan_desc = "[repo=stage-a-planning-widgets]\n\n**Planning run** for **PROD-1**."

    def tickets():
        return [
            _issue("KIT-1", ["provenance:epic"], "a planned piece"),
            _issue("KIT-2", ["provenance:agent"], "> Filed by the Stage E finding poller",
                   state="unstarted"),
            _issue("KIT-3", ["provenance:agent"], "> Epic drafted by a planning session from "
                                                  "**PROD-1**."),
            _issue("KIT-4", ["provenance:epic"], "[model=opus]\n\nalready tagged"),
            _issue("KIT-5", ["provenance:epic", "Sonnet"], "a model label"),
            _issue("KIT-6", ["provenance:epic", "claude/opus"], "a provider/model label"),
            _issue("KIT-7", ["provenance:epic"], "x \\[model=haiku\\] y"),
            _issue("KIT-8", ["provenance:epic"], "started", state="started"),
            _issue("KIT-9", ["Bug"], "hand-filed"),
            _issue("KIT-10", ["stage-a-planning-widgets"], plan_desc, state="unstarted"),
            _issue("KIT-11", ["provenance:epic", "area/api"], "a slash label, not a runner"),
            _issue("KIT-12", ["provenance:epic", "gpt-5"], "a codex model label"),
            _issue("KIT-13", ["provenance:agent", "codex"], "a codex agent label"),
            _issue("KIT-14", ["provenance:agent"], "> Filed by the Stage E finding poller, "
                                                   "then split into sub-issues"),
            _issue("KIT-15", ["provenance:epic"], "done", state="completed"),
            _issue("KIT-16", ["provenance:epic"], "in triage", state="triage"),
            # A planning ticket that lost its routing label: found by its opening tag, in
            # the escaped form Linear stores.
            _issue("KIT-17", [], "\\" + plan_desc, state="unstarted"),
            _issue("KIT-18", ["provenance:epic", "codex"], "in progress on codex",
                   state="started"),
        ]

    def go(argv=None, **kw):
        with open(c_path, "w", encoding="utf-8") as fh:
            fh.write(c_before)
        tracker = kw.pop("tracker", None) or _FakeTracker(tickets())
        lane = kw.pop("lane", None) or _FakeLane(chat, c_conf)
        io = _FakeIO(tracker, _FakeHost(kw.pop("files", live)), lane, **kw)
        code = main(base if argv is None else argv, io)
        return code, io, tracker

    def written_pair():
        return chat.chat_pair_wanted(chat.load_conf(c_path)[0])

    # 1. Refused, or stopped, before anything is read: every agent marker, root, no
    #    terminal, no model named.
    for marker in AGENT_ENV_MARKERS:
        code, io, tracker = go(env={marker: ""})
        check("refused-under:%s" % marker, (code, io.lane.ran, tracker.queries, io.loads),
              (EX_REFUSED, [], [], 0))
    code, io, _t = go(uid=0)
    check("refused-as-root", (code, io.lane.ran), (EX_REFUSED, []))
    code, io, _t = go(tty=False)
    check("stopped-without-a-person", (code, io.lane.ran), (EX_USAGE, []))
    code, io, _t = go(["--conf", a_path])
    check("stopped-without-a-model", (code, io.lane.ran), (EX_USAGE, []))

    # 2. The whole switch: every open pipeline ticket on the default tagged with its own
    #    kind's model, in progress or not; the conf line written; merge --apply, the pair
    #    read back, then verify; no chat turn yet, so it says how to see one.
    code, io, tracker = go()
    check("switch-exit", code, EX_OK)
    check("switch-tags-every-open-pipeline-ticket-on-the-default",
          [u[0] for u in tracker.updates], ["KIT-1", "KIT-2", "KIT-8", "KIT-10", "KIT-11",
                                            "KIT-14", "KIT-16", "KIT-17"])
    check("switch-tag-is-each-kinds-own-model",
          dict((u[0], dispatcher_pick([], u[1])) for u in tracker.updates),
          {"KIT-1": ("claude", "opus"), "KIT-2": ("claude", "claude-opus-5-5"),
           "KIT-8": ("claude", "opus"), "KIT-10": ("claude", "opus"),
           "KIT-11": ("claude", "opus"), "KIT-14": ("claude", "claude-opus-5-5"),
           "KIT-16": ("claude", "opus"), "KIT-17": ("claude", "opus")})
    check("switch-planning-tag-under-an-escaped-routing-tag", dict(tracker.updates)["KIT-17"],
          "\\[repo=stage-a-planning-widgets]\n[model=opus]\n\n**Planning run** for "
          "**PROD-1**.")
    check("switch-tag-keeps-the-description", tracker.updates[0][1],
          "[model=opus]\n\na planned piece")
    check("switch-planning-tag-under-the-routing-tag", dict(tracker.updates)["KIT-10"],
          "[repo=stage-a-planning-widgets]\n[model=opus]\n\n**Planning run** for **PROD-1**.")
    check("switch-asks-once", len(io.asked), 1)
    check("switch-runs-merge-then-verify", io.lane.ran, ["merge", "verify"])
    check("switch-asks-sudo-for-its-own-reason",
          ("the switch reads" in io.why, "planner job" in io.why), (True, False))
    written = open(c_path, encoding="utf-8").read()
    check("switch-writes-chat-model", (chat.load_conf(c_path)[1], written_pair()),
          ([], ("sonnet", "haiku")))
    check("switch-keeps-the-conf-mode", oct(os.stat(c_path).st_mode & 0o777), oct(0o640))
    check("switch-changes-only-that-line",
          [l for l in written.split("\n") if l not in c_before.split("\n")], ["CHAT_MODEL=sonnet"])
    check("switch-lists-each-ticket-with-its-kind-state-and-title",
          ("KIT-1  [model=opus]  planned piece, not started: t KIT-1" in io.text(),
           "KIT-8  [model=opus]  planned piece, in progress" in io.text(),
           "KIT-10  [model=opus]  planning ticket, not started" in io.text(),
           "KIT-2  [model=claude-opus-5-5]  finding, not started" in io.text(),
           "told the description changed" in io.text(),
           "7 other open pipeline ticket(s) already name their model or another runner"
           in io.text(), "In progress and naming another runner: KIT-18" in io.text(),
           "KIT-3" in io.text(), "KIT-9" in io.text()),
          (True, True, True, True, True, True, True, False, False))
    # `keep` only for the very name: findings keep claude-opus-5-5, a pinned id, while those
    # with no tag run on the alias opus today (review of KIT-385).
    check("switch-says-which-kinds-keep-their-model-and-which-a-tag-moves",
          ("Planning tickets and planned pieces keep opus." in io.text(),
           "Findings are tagged claude-opus-5-5, but those with no tag run on opus today, so "
           "the tag moves them to claude-opus-5-5." in io.text()), (True, True))
    check("switch-says-how-to-see-a-turn",
          ("Switched: chat turns now start on sonnet" in io.text(),
           "python3 scripts/pipeline_chat_lane_setup.py verify --conf %s" % c_path in io.text()),
          (True, True))
    check("switch-listing-asks-the-right-filter",
          [q for q in tracker.queries if "OpenPipelineTickets" in q][0], Q_OPEN_TICKETS)

    # 3. How it ends, judged by the pair itself and by verify's chat-model row, never by
    #    merge's exit code.
    turn = {"at": "2026-11-02T12:05:00Z", "model": "claude-sonnet-5-5", "outcome": "success",
            "cost": 0.01, "costNote": "exact"}
    before = dict(turn, at="2026-11-02T10:15:00Z")
    for name, lane, want, needle in (
            ("a-turn-on-it-is-done", _FakeLane(chat, c_conf, turns=[turn], verify_rc=0), EX_OK,
             "Done: chat turns start on sonnet"),
            # Only what was logged since the pair was written proves it.
            ("a-turn-from-before-the-write-is-no-proof",
             _FakeLane(chat, c_conf, turns=[before], verify_rc=0), EX_OK,
             "Switched: chat turns now start"),
            ("a-silent-run-from-before-the-write-is-no-failure",
             _FakeLane(chat, c_conf, silent=2, silent_at="2026-11-02T10:15:00Z"), EX_OK,
             "Switched: chat turns now start"),
            ("logs-that-cannot-be-read-are-said",
             _FakeLane(chat, c_conf, chat_turns={"error": "unreadable"}), EX_OK,
             "could not be read"),
            ("a-read-back-that-cannot-read-the-config-is-not-checked",
             _FakeLane(chat, c_conf, error_after_merge="unreadable"), EX_UNKNOWN,
             "NOT CHECKED: the pair could not be read back (the dispatcher config is "
             "unreadable"),
            ("a-read-back-past-a-new-env-name-cannot-be-written",
             _FakeLane(chat, c_conf, env_after_merge=["CYRUS_DEFAULT_MODEL"]), EX_FAILED,
             "STOPPED: the pair can't be written"),
            ("a-merge-that-stops-is-said",
             _FakeLane(chat, c_conf, merge_raises=True), EX_FAILED,
             "the chat lane's merge stopped (RuntimeError)"),
            ("merge-exit-ignored-when-the-pair-took",
             _FakeLane(chat, c_conf, merge_rc=10), EX_OK, "Switched: chat turns now start"),
            ("pair-not-taken-is-a-failure",
             _FakeLane(chat, c_conf, merge_writes=False, merge_rc=0), EX_FAILED,
             "STOPPED: the pair did not take (merge exited 0)"),
            ("other-rows-are-named", _FakeLane(chat, c_conf, verify_rc=1), EX_OK,
             "verify exited 1 for rows that are not about the model")):
        code, io, _t = go(lane=lane)
        check("ends:%s" % name, (code, needle in io.text()), (want, True))

    class _ReadBackFails(_FakeLane):
        def facts(self, conf):
            if self.ran:
                return None, "exit 1: synthetic"
            return _FakeLane.facts(self, conf)

    code, io, _t = go(lane=_ReadBackFails(chat, c_conf))
    check("ends:read-back-failed-is-unknown",
          (code, "NOT CHECKED: the pair could not be read back" in io.text()),
          (EX_UNKNOWN, True))
    # A model the comparison cannot place is not proven by a turn on another one.
    code, io, _t = go(["--to", "claude-nova-1", "--backup", "sonnet"] + base[2:],
                      lane=_FakeLane(chat, c_conf, turns=[dict(turn, model="claude-opus-5-5")]))
    check("ends:an-uncomparable-model-is-not-done",
          (code, "Done:" in io.text(), "Switched: chat turns now start on claude-nova-1"
           in io.text(), "the ticket checks are skipped" in io.text(), len(_t.updates)),
          (EX_OK, False, True, False, 8))
    code, io, _t = go(lane=_FakeLane(chat, c_conf, silent=2, verify_rc=1))
    check("ends:verify-chat-model-row-failing",
          (code, "But the chat-model check of the chat runs since this switch wrote the pair "
                 "FAILS" in io.text(),
           "heard nothing from the model" in io.text()), (EX_FAILED, True, True))
    # verify's row reads the runs from before the write too: its failure there is named as
    # that, never blamed on rows about something else (review of KIT-385).
    code, io, _t = go(lane=_FakeLane(chat, c_conf, silent=2, verify_rc=1,
                                     silent_at="2026-11-02T10-15-00-000Z"))
    check("ends:verify-failing-on-runs-before-the-write-is-said-as-that",
          (code, "its chat-model row fails on chat runs from before this switch wrote the pair"
           in io.text(), "rows that are not about the model" in io.text()),
          (EX_OK, True, False))
    # The probe's own stamps carry dashes, and a turn may carry only its run's stamp: one
    # after the write proves it, one before does not.
    for name, stamp, want in (("after", "2026-11-02T12-05-30-123Z", "Done: chat turns"),
                              ("before", "2026-11-02T10-15-30-123Z", "Switched: chat turns")):
        run_only = dict((k, v) for k, v in turn.items() if k != "at")
        run_only["run"] = stamp
        code, io, _t = go(lane=_FakeLane(chat, c_conf, turns=[run_only], verify_rc=0))
        check("ends:a-turn-stamped-by-its-run-only:%s" % name, (code, want in io.text()),
              (EX_OK, True))

    # 4. Before the yes: an env file that sets the model, or another chat-lane change
    #    waiting, stops it, and nothing is tagged or written.
    for name in chat.MODEL_ENV_NAMES:
        code, io, tracker = go(lane=_FakeLane(chat, c_conf, env_names=[name]))
        check("preflight-env-override:%s" % name,
              (code, io.asked, tracker.updates, io.lane.ran, name in io.text()),
              (EX_USAGE, [], [], [], True))

    class _EnvUnread(_FakeLane):
        def facts(self, conf):
            facts, why = _FakeLane.facts(self, conf)
            facts["env"] = {"error": "unreadable"}
            return facts, why

    code, io, tracker = go(lane=_EnvUnread(chat, c_conf))
    check("preflight-env-unreadable", (code, io.asked, tracker.updates,
                                       "could not be read (unreadable)" in io.text()),
          (EX_UNKNOWN, [], [], True))
    code, io, tracker = go(lane=_FakeLane(chat, c_conf, grant=[]))
    check("preflight-other-changes-waiting",
          (code, io.asked, tracker.updates, io.lane.ran,
           "other chat-lane changes are waiting" in io.text(), "GRANT" in io.text(),
           "merge --apply --conf" in io.text()), (EX_USAGE, [], [], [], True, True, True))
    code, io, tracker = go(lane=_FakeLane(chat, c_conf, probe_fails=1))
    check("preflight-probe-failed", (code, io.asked, tracker.updates), (EX_UNKNOWN, [], []))
    code, io, tracker = go(lane=_FakeLane(chat, c_conf, config_error="unparseable"))
    check("preflight-config-error", (code, io.asked, tracker.updates, io.lane.ran,
                                     "is unparseable" in io.text()),
          (EX_UNKNOWN, [], [], [], True))
    for name, lane, fix in (
            ("env", _FakeLane(chat, c_conf, env_names=["CYRUS_DEFAULT_RUNNER"]),
             "Take CYRUS_DEFAULT_RUNNER out of the env file, restart the dispatcher"),
            ("config", _FakeLane(chat, c_conf, runner="codex"),
             "Set defaultRunner to claude in its config, or take it out, restart"),
            ("picked", _FakeLane(chat, c_conf, env_names=["OPENAI_API_KEY"]),
             "codex, the dispatcher's own pick")):
        code, io, tracker = go(lane=lane)
        check("preflight-default-runner-not-claudes:%s" % name,
              (code, io.asked, tracker.updates, "default runner" in io.text(),
               fix in io.text(), "card CK-C5" in io.text(), "or set it to claude" in io.text()),
              (EX_USAGE, [], [], True, True, True, False))
    # Two runners' credentials, or Claude's beside another: the dispatcher picks Claude.
    for names in (["OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"], ["GEMINI_API_KEY", "CURSOR_API_KEY"],
                  ["ANTHROPIC_API_KEY"]):
        code, io, tracker = go(lane=_FakeLane(chat, c_conf, env_names=names))
        check("preflight-default-runner-picked-claude-goes-on:%s" % "+".join(names),
              (code, "default runner" in io.text()), (EX_OK, False))
    # chat-lane.conf that cannot be written stops it before the yes, not after the tags.
    code, io, tracker = go(not_writable=True)
    check("preflight-conf-not-writable", (code, io.asked, tracker.updates,
                                          "you cannot write" in io.text()),
          (EX_USAGE, [], [], True))
    code, io, tracker = go(lane=_FakeLane(chat, c_conf, runner="claude"))
    check("preflight-default-runner-claude-goes-on", (code, len(tracker.updates)), (EX_OK, 8))

    def with_running_plan():
        return tickets() + [_issue("KIT-19", ["stage-a-planning-widgets"], plan_desc,
                                   state="started")]
    code, io, tracker = go(tracker=_FakeTracker(with_running_plan()))
    check("preflight-a-planning-run-under-way-stops-it",
          (code, io.asked, tracker.updates, io.lane.ran,
           "a planning run is under way on KIT-19" in io.text()),
          (EX_USAGE, [], [], [], True))
    code, io, _t = go(lane=_FakeLane(chat, c_conf, models={
        "claudeDefaultModel": "haiku", "claudeDefaultFallbackModel": "sonnet"}))
    check("preflight-a-ticket-model-that-is-not-todays-is-said-as-a-move",
          ("those with no tag run on haiku today, so the tag moves them to opus" in io.text(),
           "keep opus" in io.text()), (True, False))
    code, io, tracker = go(lane=_FakeLane(chat, c_conf, models={
        "claudeDefaultModel": "sonnet", "claudeDefaultFallbackModel": "haiku"}))
    check("preflight-pair-in-place-runs-verify-only",
          (code, io.lane.ran, "leave the dispatcher's pair alone" in io.text()),
          (EX_OK, ["verify"], True))

    # 5. Administrator access: refused, it says this script's own command and changes nothing;
    #    and the REAL session's banner carries this script's reason, not an installer's.
    code, io, tracker = go(sudo_fails=True)
    check("sudo-refused", (code, io.asked, tracker.updates, io.lane.ran,
                           "pipeline_model_switch.py --to sonnet" in io.text(),
                           "pipeline_stage_e_setup" in io.text()),
          (EX_NOPRIV, [], [], [], True, False))

    class _RealSudo(IO):
        def sudo_cached(self):
            return False

        def sudo_session(self, chat_module):
            return chat_module.SudoSession(validate=lambda: 1)

    banner = io_module.StringIO()
    with contextlib.redirect_stdout(banner):
        try:
            _RealSudo().sudo(chat, "the switch's own reason", "--to sonnet")
            raised = False
        except chat.NoPrivilege:
            raised = True
    check("real-sudo-refusal-raises-with-its-own-reason",
          (raised, "WHY IT IS NEEDED: the switch's own reason" in banner.getvalue(),
           "planner job" in banner.getvalue()), (True, True, False))

    class _Session(object):
        def __init__(self):
            self.held, self.asked, self.kept = False, 0, 0

        def acquire(self, why, resume):
            self.asked += 1

        def _start_keepalive(self):
            self.kept += 1

        def release(self):
            pass

    class _Cached(IO):
        def sudo_cached(self):
            return True

        def sudo_session(self, chat_module):
            self.session = _Session()
            return self.session

    cached = _Cached()
    banner = io_module.StringIO()
    with contextlib.redirect_stdout(banner):
        held = cached.sudo(chat, "why", "--to sonnet")
    check("real-sudo-already-held-is-kept-fresh-without-asking",
          (held.held, held.asked, held.kept, banner.getvalue()), (True, 0, 1, ""))

    # 6. A backup of its own, a backup cleared, and going back.
    code, io, _t = go(base + ["--backup", "opus"])
    check("switch-with-a-backup", (code, written_pair()), (EX_OK, ("sonnet", "opus")))
    with open(c_path, "w", encoding="utf-8") as fh:
        fh.write(conf_with(c_before, {"CHAT_MODEL": "sonnet", "CHAT_BACKUP_MODEL": "opus"}))
    io = _FakeIO(_FakeTracker(tickets()), _FakeHost(live),
                 _FakeLane(chat, c_conf, models={"claudeDefaultModel": "sonnet",
                                                 "claudeDefaultFallbackModel": "opus"}))
    code = main(["--to", "haiku"] + base[2:], io)
    check("switch-clears-a-backup-it-was-not-given",
          (code, written_pair(), "CHAT_BACKUP_MODEL=" in open(c_path, encoding="utf-8")
           .read().split("\n"), "and clear CHAT_BACKUP_MODEL" in io.text()),
          (EX_OK, ("haiku", "sonnet"), True, True))
    original_e = open(e_path, encoding="utf-8").read()
    with open(e_path, "w", encoding="utf-8") as fh:
        fh.write(conf_with(original_e, {"TICKET_MODEL": "opus"}))
    code, io, tracker = go(["--to", "opus"] + base[2:], key_found=False, files={},
                           tracker=_FakeTracker([], fail=AssertionError("no tracker call")),
                           lane=_FakeLane(chat, c_conf, models={
                               "claudeDefaultModel": "sonnet",
                               "claudeDefaultFallbackModel": "haiku"}))
    check("going-back-needs-no-tracker-and-no-live-config",
          (code, tracker.queries, io._host.reads, written_pair(),
           "the ticket checks are skipped" in io.text()),
          (EX_OK, [], [], ("opus", "sonnet"), True))
    with open(e_path, "w", encoding="utf-8") as fh:
        fh.write(original_e)
    # An alias and a pinned id of one family are two names, never going back (review of
    # KIT-385): findings keep claude-opus-5-5, so `--to opus` tags them...
    code, io, tracker = go(["--to", "opus"] + base[2:])
    check("an-alias-is-not-going-back-from-a-pinned-id",
          (code, "the ticket checks are skipped" in io.text(), len(tracker.updates),
           ("KIT-2", "[model=claude-opus-5-5]") in [(u[0], u[1].split("\n")[0])
                                                    for u in tracker.updates]),
          (EX_OK, False, 8, True))
    # ...and where both keep the alias opus, `--to` a pinned Opus tags them too.
    with open(e_path, "w", encoding="utf-8") as fh:
        fh.write(conf_with(original_e, {"TICKET_MODEL": "opus"}))
    opus_files = dict(live)
    opus_files[(e_role, ".stage-e/finding/poller.json")] = json.dumps({"ticket_model": "opus"})
    code, io, tracker = go(["--to", "claude-opus-4-1", "--backup", "sonnet"] + base[2:],
                           files=opus_files)
    check("a-pinned-id-is-not-going-back-from-its-alias",
          (code, "the ticket checks are skipped" in io.text(), len(tracker.updates),
           sorted(set(m for u in tracker.updates
                      for m in re.findall(r"(?m)^\[model=[^\]\n]*\]", u[1]))), written_pair()),
          (EX_OK, False, 8, ["[model=opus]"], ("claude-opus-4-1", "sonnet")))
    with open(e_path, "w", encoding="utf-8") as fh:
        fh.write(original_e)
    # One kind keeping another model is not going back: its tickets are tagged.
    with open(e_path, "w", encoding="utf-8") as fh:
        fh.write(conf_with(original_e, {"TICKET_MODEL": "haiku"}))
    haiku_files = dict(live)
    haiku_files[(e_role, ".stage-e/finding/poller.json")] = json.dumps({"ticket_model": "haiku"})
    code, io, tracker = go(["--to", "opus"] + base[2:], files=haiku_files)
    check("not-going-back-when-one-kind-keeps-another-model",
          (code, [u[0] for u in tracker.updates],
           "Findings are tagged haiku, but those with no tag run on opus today" in io.text()),
          (EX_OK, ["KIT-1", "KIT-2", "KIT-8", "KIT-10", "KIT-11", "KIT-14", "KIT-16",
                   "KIT-17"], True))
    with open(e_path, "w", encoding="utf-8") as fh:
        fh.write(original_e)

    # 5c. A planning ticket that lost its label is found by its opening tag: the plain one
    #     the fence probe writes, in any case, as the dispatcher reads it (review of KIT-385).
    plain = tickets() + [_issue("KIT-20", [], plan_desc, state="unstarted"),
                         _issue("KIT-21", [], plan_desc.replace("[repo=", "[REPO=", 1),
                                state="unstarted")]
    code, io, tracker = go(tracker=_FakeTracker(plain))
    check("switch-finds-a-label-less-planning-ticket-by-its-plain-opening",
          (code, [u[0] for u in tracker.updates if u[0] in ("KIT-20", "KIT-21")]),
          (EX_OK, ["KIT-20", "KIT-21"]))

    # 6a. A planning ticket whose run starts between the list and the write stops the
    #     switch there: nothing after it is tagged, and the conf and the pair are untouched
    #     (review of KIT-385).
    class _StartsDuringTheYes(_FakeIO):
        def ask(self, question):
            self._tracker.issues["id-KIT-10"]["state"] = {"type": "started"}
            return _FakeIO.ask(self, question)

    race_lane, race_tracker = _FakeLane(chat, c_conf), _FakeTracker(tickets())
    io = _StartsDuringTheYes(race_tracker, _FakeHost(live), race_lane)
    with open(c_path, "w", encoding="utf-8") as fh:
        fh.write(c_before)
    code = main(base, io)
    with open(c_path, encoding="utf-8") as fh:
        conf_after = fh.read()
    check("a-planning-run-that-starts-during-the-yes-stops-the-switch",
          (code, "STOPPED at KIT-10: its planning run started since the list was made"
           in io.text(), [u[0] for u in race_tracker.updates], race_lane.ran,
           conf_after == c_before),
          (EX_USAGE, True, ["KIT-1", "KIT-2", "KIT-8"], [], True))

    # 6b. A conf that does not read back as the pair after the edit is put back as it was
    #     read, and the dispatcher is not touched: here the file changed under the switch.
    class _Moved(_FakeIO):
        def read_text(self, path):
            return IO.read_text(self, path) + "DISPATCHER_PORT=0\n"

    lane = _FakeLane(chat, c_conf)
    io = _Moved(_FakeTracker(tickets()), _FakeHost(live), lane)
    with open(c_path, "w", encoding="utf-8") as fh:
        fh.write(c_before)
    code = main(base, io)
    check("switch-puts-back-a-conf-that-fails-after-the-edit",
          (code, lane.ran, "DISPATCHER_PORT=0" in open(c_path, encoding="utf-8").read(),
           "CHAT_MODEL=sonnet" in open(c_path, encoding="utf-8").read().split("\n")),
          (EX_FAILED, [], True, False))

    # 7. No: nothing changes, not a ticket, not the conf, nothing runs.
    code, io, tracker = go(answer=False)
    check("no-changes-nothing", (code, tracker.updates, io.lane.ran,
                                 open(c_path, encoding="utf-8").read() == c_before),
          (EX_DECLINED, [], [], True))

    # 8. Each check that must hold first, each stopping before the question.
    def with_file(key, text):
        files = dict(live)
        files[key] = text
        return files

    for name, kw, want, needle in (
            ("bad-model", {"argv": ["--to", "gpt-5"] + base[2:]}, EX_USAGE, "must be fable"),
            ("same-backup", {"argv": base + ["--backup", "sonnet"]}, EX_USAGE,
             "CHAT_MODEL itself"),
            ("no-key", {"key_found": False}, EX_UNKNOWN, "no Linear key"),
            ("stage-e-conf-missing",
             {"argv": base[:5] + [os.path.join(tmp, "absent.conf")] + base[6:]}, EX_USAGE,
             "--stage-e-conf <your stage-e.conf>"),
            ("planner-config-unreadable",
             {"files": with_file((a_role, ".stage-a/poller.json"), None)}, EX_UNKNOWN,
             "could not be read as"),
            ("planner-not-installed",
             {"files": with_file((a_role, ".stage-a/poller.json"), "")}, EX_USAGE,
             "is not installed for"),
            ("planner-config-not-an-object",
             {"files": with_file((a_role, ".stage-a/poller.json"), "[]")}, EX_UNKNOWN,
             "is not a JSON object"),
            ("planner-config-without-the-model",
             {"files": with_file((a_role, ".stage-a/poller.json"), "{}")}, EX_USAGE,
             "says no ticket_model"),
            ("finding-config-another-model",
             {"files": with_file((e_role, ".stage-e/finding/poller.json"),
                                 json.dumps({"ticket_model": "haiku"}))}, EX_USAGE,
             "says 'haiku'"),
            ("listing-unreachable",
             {"tracker": _FakeTracker(tickets(), fail=stage_a.Unknown(
                 "the tracker was unreachable (synthetic)"))}, EX_UNKNOWN,
             "the tracker was unreachable (synthetic)"),
            ("listing-odd-failure", {"tracker": _FakeTracker(tickets(), fail=KeyError("x"))},
             EX_UNKNOWN, "listed (KeyError)")):
        code, io, tracker = go(**kw)
        check("stops:%s" % name, (code, io.asked, tracker.updates, io.lane.ran,
                                  needle in io.text()), (want, [], [], [], True))
    check("stops:missing-conf-names-the-flag-not-the-installers-first-run",
          "copy stage-a.conf.example" in go(
              ["--to", "sonnet", "--conf", os.path.join(tmp, "none.conf")] + base[4:])[1].text(),
          False)
    code, io, tracker = go(write_fails=True)
    check("stops:a-conf-that-cannot-be-written",
          (code, len(tracker.updates), io.lane.ran,
           "STOPPED: chat-lane.conf could not be written" in io.text()),
          (EX_FAILED, 8, [], True))
    bad_bytes = os.path.join(tmp, "bad-bytes.conf")
    with open(bad_bytes, "wb") as fh:
        fh.write(c_before.encode("utf-8") + b"\xe9\n")
    code, io, _t = go(base[:6] + ["--chat-lane-conf", bad_bytes])
    check("stops:a-conf-that-is-not-utf8", (code, "it could not be read" in io.text()),
          (EX_USAGE, True))
    if os.geteuid() != 0:                     # root reads a file whatever its mode
        locked = os.path.join(tmp, "locked.conf")
        with open(locked, "w", encoding="utf-8") as fh:
            fh.write(open(a_path, encoding="utf-8").read())
        os.chmod(locked, 0)
        code, io, _t = go(["--to", "sonnet", "--conf", locked] + base[4:])
        os.chmod(locked, 0o600)
        check("stops:a-conf-that-cannot-be-read", (code, "it could not be read" in io.text()),
              (EX_USAGE, True))
    for conf_name, path in (("stage-a", a_path), ("stage-e", e_path)):
        original = open(path, encoding="utf-8").read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(conf_with(original, {"TICKET_MODEL": ""}))
        code, io, tracker = go()
        check("stops:no-ticket-model-in-%s" % conf_name,
              (code, io.asked, tracker.updates, "TICKET_MODEL is not set" in io.text()),
              (EX_USAGE, [], [], True))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original)

    # 9. Tagging, each guard: closed since the list, given a model since the list, a tag
    #    Linear did not keep, a planning ticket's routing tag, a tracker failure's words.
    t = _FakeTracker(tickets())
    t.issues["id-KIT-1"]["state"]["type"] = "completed"
    check("tag-leaves-a-closed-ticket", tag_one(t, t.issues["id-KIT-1"], PIECE, "opus"),
          ("closed", None))
    t = _FakeTracker(tickets())
    t.issues["id-KIT-1"]["description"] = "[model=haiku] now"
    check("tag-keeps-a-model-given-meanwhile",
          (tag_one(t, t.issues["id-KIT-1"], PIECE, "opus"), t.updates), (("kept", None), []))
    late = _FakeTracker(tickets())
    original_post = late.post

    def post_then_tag(query, variables=None):
        answer = original_post(query, variables)
        if "OpenPipelineTickets" in query and not answer["issues"]["pageInfo"]["hasNextPage"]:
            late.issues["id-KIT-8"]["description"] = "[model=haiku]\n\nstarted"
        return answer

    late.post = post_then_tag
    code, io, _t = go(tracker=late)
    check("tag-says-kept-not-tagged", ("left KIT-8: it was given its own model" in io.text(),
                                       "tagged KIT-8" in io.text()), (True, False))
    # A model label added, and a person's edit made, between the list and the write: the
    # write reads the ticket again, so the label keeps its own model and the edit is kept.
    later = _FakeTracker(tickets())
    listing = later.post

    def post_then_change(query, variables=None):
        answer = listing(query, variables)
        if "OpenPipelineTickets" in query and not answer["issues"]["pageInfo"]["hasNextPage"]:
            later.issues["id-KIT-8"]["labels"]["nodes"].append({"name": "haiku"})
            later.issues["id-KIT-1"]["description"] = "edited after the list"
        return answer
    later.post = post_then_change
    code, io, _t = go(tracker=later)
    check("tag-reads-the-ticket-again-before-the-write",
          ("left KIT-8: it was given its own model" in io.text(),
           dict(later.updates).get("KIT-1")), (True, "[model=opus]\n\nedited after the list"))
    code, io, tracker = go(tracker=_FakeTracker(tickets(), store_as=lambda d: d.replace(
        "[model=", "[mode=")))
    check("tag-not-kept-stops-before-the-conf",
          (code, len(tracker.updates), io.lane.ran,
           open(c_path, encoding="utf-8").read() == c_before), (EX_FAILED, 1, [], True))
    code, io, tracker = go(tracker=_FakeTracker(tickets(), store_as=lambda d: re.sub(
        r"(?<!\\)\[repo=", r"\\[repo=", d.replace("[model=opus]", "\\[model=opus\\]"))))
    check("tag-read-back-takes-linears-escape", (code, len(tracker.updates)), (EX_OK, 8))
    t = _FakeTracker(tickets(), store_as=lambda d: "\n" + d.replace("[repo=", "(repo="))
    check("tag-refuses-a-planning-ticket-that-lost-its-routing",
          tag_one(t, t.issues["id-KIT-10"], PLANNING, "opus"),
          ("failed", "its routing tag no longer opens it"))
    for name, paging, needle in (("no-cursor", "no-cursor", "gave no cursor"),
                                 ("past-the-cap", "endless", "more than")):
        try:
            pipeline_tickets(_FakeTracker(tickets(), paging=paging), {
                PLANNING: "opus", PIECE: "opus", FINDING: "opus"})
            got = "read as complete"
        except ValueError as exc:
            got = str(exc)
        check("listing-%s-is-never-complete" % name, needle in got, True)
    check("listing-pages-through", [i[0]["identifier"] for i in pipeline_tickets(
        _FakeTracker(tickets(), page=1), {PLANNING: "opus", PIECE: "opus",
                                         FINDING: "opus"})[0]],
          ["KIT-1", "KIT-2", "KIT-8", "KIT-10", "KIT-11", "KIT-14", "KIT-16", "KIT-17"])

    # 10. The dispatcher's rule, ported: each line here is what cyrus 0.2.73's own
    #     determineRunnerSelection returns for it (checked against its source with node).
    for labels, desc, want in (
            ([], "", ("claude", None)),
            ([], "[model=opus]", ("claude", "opus")),
            ([], "\\[model=Opus\\]", ("claude", "Opus")),
            ([], "[MODEL=sonnet]", ("claude", "sonnet")),
            ([], "[agent=codex]", ("codex", None)),
            ([], "[agent=codex] [model=opus]", ("codex", None)),
            ([], "[model=gpt-5]", ("codex", "gpt-5")),
            ([], "[model=gemini-3]", ("gemini", "gemini-3")),
            ([], "[model=whatever]", ("claude", "whatever")),
            (["Opus"], "", ("claude", "opus")),
            (["area/api"], "", ("claude", None)),
            (["claude/sonnet"], "", ("claude", "sonnet")),
            (["openai/gpt-5"], "", ("codex", "gpt-5")),
            (["opencode/anthropic/x"], "", ("opencode", "anthropic/x")),
            (["opencode/x"], "", ("opencode", "opencode/x")),
            (["gpt-5"], "", ("codex", "gpt-5")),
            (["my-gpt-5-codex"], "", ("codex", "my-gpt-5-codex")),
            (["gemini-2.5"], "", ("gemini", "gemini-2.5-pro")),
            (["codex"], "", ("codex", None)),
            (["codex"], "[model=opus]", ("codex", None)),
            (["claude"], "[model=gpt-5]", ("claude", None)),
            (["gpt-5"], "[model=opus]", ("claude", "opus")),
            (["haiku", "opus"], "", ("claude", "opus")),
            (["sonnet", "claude/haiku"], "", ("claude", "haiku")),
            ([], "[agent=CLAUDE]", ("claude", None)),
            ([], "[agent=nobody]", ("claude", None)),
            ([], "[model=opu\u017f]", ("claude", None))):
        check("pick:%r:%r" % (labels, desc), dispatcher_pick(labels, desc), want)

    # 11. Which tickets are the pipeline's, and where a tag goes.
    check("kind:epic-by-its-opening-line", ticket_kind(_issue("E", ["provenance:agent"],
                                                             "> Epic drafted by a planning "
                                                             "session from **X**.")), None)
    check("kind:childless-epic-is-not-a-finding", ticket_kind(_issue(
        "E", ["provenance:agent"], "\n> Epic drafted by a planning session")), None)
    check("kind:escaped-epic-is-not-a-finding", ticket_kind(_issue(
        "E", ["provenance:agent"], "\\> Epic drafted by a planning session")), None)
    check("body:planning-tag-after-leading-space",
          tagged_body("\n[repo=stage-a-planning-x]\n\nbody", "opus", PLANNING),
          "\n[repo=stage-a-planning-x]\n[model=opus]\n\nbody")
    check("kind:split-finding-is-a-finding", ticket_kind(_issue("F", ["provenance:agent"],
                                                              "> Filed by the poller")), FINDING)
    check("kind:planning-by-label", ticket_kind(_issue("P", ["stage-a-planning-x"], "x")),
          PLANNING)
    check("kind:planning-by-tag", ticket_kind(_issue("P", [], "[repo=stage-a-planning-x]")),
          PLANNING)
    check("body:planning-tag-alone", tagged_body("[repo=stage-a-planning-x]", "opus", PLANNING),
          "[repo=stage-a-planning-x]\n[model=opus]")
    check("body:planning-by-label-only-tag-first",
          tagged_body("no routing tag", "opus", PLANNING), "[model=opus]\n\nno routing tag")

    # 12. The conf edit: live line replaced, commented example made live, else added; two
    #     live lines refused; a symlinked conf written through the link, no temp file left.
    check("conf-replaces-the-live-line", conf_with("A=1\nCHAT_MODEL=opus\n", {"CHAT_MODEL": "x"}),
          "A=1\nCHAT_MODEL=x\n")
    check("conf-uses-the-example-line", conf_with("# about it\n#CHAT_MODEL=sonnet\nB=2\n",
                                                  {"CHAT_MODEL": "x"}),
          "# about it\nCHAT_MODEL=x\nB=2\n")
    check("conf-adds-a-line", conf_with("A=1\n", {"CHAT_MODEL": "x"}), "A=1\nCHAT_MODEL=x\n")
    check("conf-adds-without-a-final-newline", conf_with("A=1", {"CHAT_MODEL": "x"}),
          "A=1\nCHAT_MODEL=x")
    try:
        conf_with("CHAT_MODEL=a\nCHAT_MODEL=b\n", {"CHAT_MODEL": "x"})
        two = "accepted"
    except ValueError:
        two = "refused"
    check("conf-refuses-two-live-lines", two, "refused")
    check("conf-makes-the-last-example-line-live",
          conf_with("#CHAT_MODEL=opus\nA=1\n#CHAT_MODEL=sonnet\n", {"CHAT_MODEL": "x"}),
          "#CHAT_MODEL=opus\nA=1\nCHAT_MODEL=x\n")
    target, link = os.path.join(tmp, "real.conf"), os.path.join(tmp, "link.conf")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("A=1\n")
    os.symlink(target, link)
    IO().write_text(link, "A=2\n")
    check("conf-write-goes-through-a-symlink",
          (os.path.islink(link), open(target, encoding="utf-8").read(),
           sorted(n for n in os.listdir(tmp) if ".model-switch." in n)), (True, "A=2\n", []))
    real_replace = os.replace

    def refused(_a, _b):
        raise OSError("synthetic")

    os.replace = refused
    try:
        IO().write_text(link, "A=3\n")
        refusal = None
    except OSError as exc:
        refusal = str(exc)
    finally:
        os.replace = real_replace
    check("conf-write-that-fails-leaves-no-temporary-file",
          (refusal, open(target, encoding="utf-8").read(),
           sorted(n for n in os.listdir(tmp) if ".model-switch." in n)), ("synthetic", "A=2\n", []))
    os.chmod(target, 0o664)
    mask = os.umask(0o022)
    try:
        IO().write_text(target, "A=4\n")
    finally:
        os.umask(mask)
    check("conf-write-keeps-its-mode-under-a-umask", oct(os.stat(target).st_mode & 0o777),
          oct(0o664))

    # 13. The source never prints a key, never labels or moves a ticket, and imports the
    #     agent markers rather than keeping a copy.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read().split("# Selftest.")[0]
    for banned in ("labelIds", "stateId", "addedLabelIds", "issueArchive", "print(key"):
        check("never:%s" % banned, banned in src, False)
    check("markers-imported-not-copied",
          "from pipeline_dispatch_local import AGENT_ENV_MARKERS" in src, True)

    if failures:
        print("FAIL: pipeline_model_switch selftest")
        for f in failures:
            print("  - " + f)
        return 1
    print("OK: pipeline_model_switch selftest (%d cases)" % cases[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
