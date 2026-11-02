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
     nothing else. A model name in the dispatcher's env file, or another chat-lane change
     waiting, stops it, and it names what to do first.
  3. Reads the planner's and the finding poller's own configs, as their role accounts, and
     checks each carries its TICKET_MODEL. A conf the update never applied tags nothing.
  4. Lists the open tickets the pipeline filed that still run on the dispatcher's default:
     planning tickets, planned pieces (`provenance:epic`) and findings (`provenance:agent`,
     never an epic), in progress or not. It judges each by the dispatcher's own rule: no
     `[model=…]` tag, no model label, and no label that picks another runner.
  5. Shows those tickets, the line it writes in chat-lane.conf, and the pair. Asks ONE yes.
  6. Opens each ticket with `[model=<its TICKET_MODEL>]` (a planning ticket on its second
     line, under the routing tag that must open it) and reads it back; writes CHAT_MODEL
     (and CHAT_BACKUP_MODEL) into chat-lane.conf; runs the chat lane's `merge --apply`;
     reads the pair back; and runs its `verify`.

GOING BACK. `--to` set to the model both TICKET_MODELs keep (`--to opus`, where tickets
keep opus) moves no ticket, so steps 3 and 4 are skipped: it needs no Linear key, and works
while Linear is down. Taking CHAT_MODEL out is NOT going back: unset, `merge` leaves the
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
2 a setting, or how this was run, needs fixing first; nothing changed · 3 refused (an agent
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

Q_OPEN_TICKETS = """
query OpenPipelineTickets($after: String) {
  issues(first: %d, after: $after, filter: {
    labels: { some: { or: [ { name: { in: %s } }, { name: { startsWith: %s } } ] } },
    state: { type: { in: %s } }
  }) {
    nodes { id identifier title description state { type } labels(first: 50) { nodes { name } } }
    pageInfo { hasNextPage endCursor }
  }
}""" % (PAGE, json.dumps([PIECE_LABEL, FINDING_LABEL]), json.dumps(machine.PLANNING_PREFIX),
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
    """([(issue, kind, model)] for each open pipeline ticket on the default, and how many
    other open pipeline tickets already name their own model or runner)."""
    todo, others, after = [], 0, None
    for _ in range(MAX_PAGES):
        page = (tracker.post(Q_OPEN_TICKETS, {"after": after}) or {}).get("issues")
        if not isinstance(page, dict):
            raise ValueError("the tracker's answer held no issues")
        for issue in page.get("nodes") or []:
            kind = ticket_kind(issue)
            if not kind:
                continue
            if on_the_default(machine.label_names_of(issue), issue.get("description")):
                todo.append((issue, kind, models[kind]))
            else:
                others += 1
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return todo, others
        after = info.get("endCursor")
        if not after:
            raise ValueError("the tracker said there was another page, but gave no cursor")
    raise ValueError("more than %d open pipeline tickets: stopped listing them"
                     % (PAGE * MAX_PAGES))


def tag_one(tracker, issue, kind, model):
    """("tagged" | "kept" | "closed", None), or ("failed", why). Read again just before the
    write, so an edit made since the list is kept: a ticket given a model meanwhile keeps
    its own, and one closed meanwhile is left as it is."""
    fresh = ((tracker.post(Q_TICKET, {"id": issue["id"]}) or {}).get("issue")) or {}
    if not fresh:
        return "failed", "it could not be read again"
    if ((fresh.get("state") or {}).get("type")) not in OPEN_STATES:
        return "closed", None
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
    going_back = args.to == kept["stage-a.conf"] == kept["stage-e.conf"]
    pair = chat.chat_pair_wanted(want)
    say("Moving the tech lead to %s, with %s as the backup." % pair)
    if chat.same_model(*pair):
        say("  Note: %s and %s are in one family, so they may be one model, and the backup "
            "may back up nothing." % pair)
    if going_back:
        say("Every ticket keeps %s, the model chat turns go back to, so no ticket moves and "
            "the ticket checks are skipped." % args.to)
    else:
        say("Planning tickets and planned pieces keep %s, and findings keep %s."
            % (kept["stage-a.conf"], kept["stage-e.conf"]))

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
    _line, pair_op, why = chat.model_pair_plan(want, facts)
    if why:
        say("Nothing was changed: %s." % why)
        return EX_USAGE
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

    todo, others, tracker = [], 0, None
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
            todo, others = pipeline_tickets(tracker, models)
        except Exception as exc:  # noqa: BLE001 - a tracker failure is reported, never a traceback
            say("NOT CHECKED: the open tickets could not be listed (%s). Nothing was changed."
                % _said(stage_a, exc))
            return EX_UNKNOWN

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
    io.write_text(c_path, new)
    new_conf, errors = chat.load_conf(c_path)
    if errors or chat.chat_pair_wanted(new_conf) != pair:
        io.write_text(c_path, old)
        say("STOPPED: chat-lane.conf did not read back as that pair after the edit, so it was "
            "put back. The tickets are tagged; the dispatcher is unchanged.")
        return EX_FAILED
    say("  set %s in %s" % (", ".join("%s=%s" % kv for kv in values.items()), c_path))
    verify_shown = "%s verify --conf %s" % (CHAT_LANE_SHOWN, shlex.quote(c_path))
    merged = None
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
    if row["outcome"] != chat.ALREADY_DONE:
        say("STOPPED: the pair did not take%s: %s. The tickets are tagged and chat-lane.conf "
            "names the pair; merge's lines above say why. Fix that, then run this again."
            % (" (merge exited %s)" % merged if merged is not None else "", row["detail"]))
        return EX_FAILED
    try:
        checked = io.chat_command(chat, ["verify", "--conf", c_path], sudo)
    except Exception as exc:  # noqa: BLE001
        checked = None
        say("  the chat lane's verify stopped (%s)" % type(exc).__name__)
    acfg = after.get("config") or {}
    turn = chat.check_chat_model(acfg, after.get("env"), after.get("chatTurns"), new_conf)
    say("")
    if turn["outcome"] == chat.FAILED:
        say("Switched: chat turns start on %s, with %s as the backup. But verify's chat-model "
            "row FAILS: %s. Do what its lines above say." % (pair + (turn["detail"],)))
        return EX_FAILED
    if turn["outcome"] == chat.ALREADY_DONE:
        say("Done: chat turns start on %s, with %s as the backup, and the newest chat turn "
            "ran on it." % pair)
    else:
        say("Switched: chat turns now start on %s, with %s as the backup. No chat turn has run "
            "on the new pair yet, so verify's chat-model row says UNKNOWN until one does. Ask "
            "the bot one question in a NEW thread, then run:" % pair)
        say("    %s" % verify_shown)
    if checked is not None and checked not in (chat.EX_OK, chat.EX_UNKNOWN):
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

    def __init__(self, issues, store_as=None, page=2, fail=None):
        self.issues = dict((i["id"], i) for i in issues)
        self.order = [i["id"] for i in issues]
        self.updates, self.page, self.store_as, self.fail = [], page, store_as, fail
        self.queries = []

    @staticmethod
    def _project(issue, selection):
        out = {}
        for key, pattern in (("id", r"\bid\b"), ("identifier", r"\bidentifier\b"),
                             ("title", r"\btitle\b"), ("description", r"\bdescription\b"),
                             ("state", r"\bstate\s*\{\s*type\s*\}"),
                             ("labels", r"\blabels(\([^)]*\))?\s*\{\s*nodes\s*\{\s*name\s*\}")):
            if re.search(pattern, selection):
                out[key] = json.loads(json.dumps(issue[key]))
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
            start = int(variables.get("after") or 0)
            ids = self.order[start:start + self.page]
            selection = query.split("nodes {", 1)[1]
            nodes = [self._project(i, selection) for i in (self.issues[x] for x in ids)
                     if i["state"]["type"] in states
                     and any(l["name"] in names or l["name"].startswith(prefix)
                             for l in i["labels"]["nodes"])]
            more = start + self.page < len(self.order)
            return {"issues": {"nodes": nodes, "pageInfo": {
                "hasNextPage": more, "endCursor": str(start + self.page)}}}
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
                 merge_rc=0, verify_rc=4, turns=None, probe_fails=0, silent=0):
        self.chat, self.conf = chat, conf
        self.models = dict(models or {"claudeDefaultModel": "opus",
                                      "claudeDefaultFallbackModel": "sonnet"})
        self.env_names, self.merge_writes = list(env_names), merge_writes
        self.grant = chat.owner_grant([]) if grant is None else grant
        self.merge_rc, self.verify_rc, self.turns = merge_rc, verify_rc, turns or []
        self.probe_fails, self.silent, self.ran = probe_fails, silent, []

    def facts(self, conf):
        if self.probe_fails:
            self.probe_fails -= 1
            return None, "exit 1: synthetic"
        return {"config": {"entries": [], "models": dict(self.models),
                           "slackAllowedTools": list(self.grant)},
                "env": {"names": list(self.env_names), "empty": []},
                "userSettings": {"path": "~/.claude/settings.json",
                                 "deny": self.chat.user_deny_patterns(conf), "allowRead": [],
                                 "trust": {}},
                "chatTurns": {"turns": list(self.turns), "silent": self.silent,
                              "silentAt": "2026-11-02T10:15:00Z"}}, None

    def command(self, argv, conf_path):
        self.ran.append(argv[0])
        if argv[0] == "merge":
            conf = self.chat.load_conf(conf_path)[0]
            pair = self.chat.chat_pair_wanted(conf)
            if self.merge_writes and pair and not self.env_names:
                self.models.update(claudeDefaultModel=pair[0],
                                   claudeDefaultFallbackModel=pair[1])
            return self.merge_rc
        return self.verify_rc


class _FakeIO(IO):
    def __init__(self, tracker, host, lane, env=None, uid=501, tty=True, answer=True,
                 key_found=True, sudo_fails=False):
        self._tracker, self._host, self.lane, self._env = tracker, host, lane, env or {}
        self._uid, self._tty, self.answer, self.sudo_fails = uid, tty, answer, sudo_fails
        self.out, self.asked, self.key_found, self.why = [], [], key_found, None

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
            _issue("KIT-10", ["stage-a-planning-widgets"], plan_desc, state="started"),
            _issue("KIT-11", ["provenance:epic", "area/api"], "a slash label, not a runner"),
            _issue("KIT-12", ["provenance:epic", "gpt-5"], "a codex model label"),
            _issue("KIT-13", ["provenance:agent", "codex"], "a codex agent label"),
            _issue("KIT-14", ["provenance:agent"], "> Filed by the Stage E finding poller, "
                                                   "then split into sub-issues"),
            _issue("KIT-15", ["provenance:epic"], "done", state="completed"),
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
        check("refused-under:%s" % marker, (code, io.lane.ran, tracker.queries),
              (EX_REFUSED, [], []))
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
                                            "KIT-14"])
    check("switch-tag-is-each-kinds-own-model",
          dict((u[0], dispatcher_pick([], u[1])) for u in tracker.updates),
          {"KIT-1": ("claude", "opus"), "KIT-2": ("claude", "claude-opus-5-5"),
           "KIT-8": ("claude", "opus"), "KIT-10": ("claude", "opus"),
           "KIT-11": ("claude", "opus"), "KIT-14": ("claude", "claude-opus-5-5")})
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
    check("switch-lists-each-ticket-with-its-kind-and-state",
          ("KIT-1  [model=opus]  planned piece, not started" in io.text(),
           "KIT-8  [model=opus]  planned piece, in progress" in io.text(),
           "KIT-10  [model=opus]  planning ticket, in progress" in io.text(),
           "KIT-2  [model=claude-opus-5-5]  finding, not started" in io.text(),
           "told the description changed" in io.text(),
           "6 other open pipeline ticket(s) already name their model or another runner"
           in io.text(), "KIT-3" in io.text(), "KIT-9" in io.text()),
          (True, True, True, True, True, True, False, False))
    check("switch-says-how-to-see-a-turn",
          ("Switched: chat turns now start on sonnet" in io.text(),
           "python3 scripts/pipeline_chat_lane_setup.py verify --conf %s" % c_path in io.text()),
          (True, True))
    check("switch-listing-asks-the-right-filter",
          [q for q in tracker.queries if "OpenPipelineTickets" in q][0], Q_OPEN_TICKETS)

    # 3. How it ends, judged by the pair itself and by verify's chat-model row, never by
    #    merge's exit code.
    turn = {"at": "2026-11-02T10:15:00Z", "model": "claude-sonnet-5-5", "outcome": "success",
            "cost": 0.01, "costNote": "exact"}
    for name, lane, want, needle in (
            ("a-turn-on-it-is-done", _FakeLane(chat, c_conf, turns=[turn], verify_rc=0), EX_OK,
             "Done: chat turns start on sonnet"),
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
    code, io, _t = go(lane=_FakeLane(chat, c_conf, silent=2, verify_rc=1))
    check("ends:verify-chat-model-row-failing",
          (code, "But verify's chat-model row FAILS" in io.text(),
           "heard nothing from the model" in io.text()), (EX_FAILED, True, True))

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
          (EX_USAGE, [], [], True))
    code, io, tracker = go(lane=_FakeLane(chat, c_conf, grant=[]))
    check("preflight-other-changes-waiting",
          (code, io.asked, tracker.updates, io.lane.ran,
           "other chat-lane changes are waiting" in io.text(), "GRANT" in io.text(),
           "merge --apply --conf" in io.text()), (EX_USAGE, [], [], [], True, True, True))
    code, io, tracker = go(lane=_FakeLane(chat, c_conf, probe_fails=1))
    check("preflight-probe-failed", (code, io.asked, tracker.updates), (EX_UNKNOWN, [], []))
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
    code, io, tracker = go(["--to", "opus"] + base[2:])
    check("not-going-back-when-one-kind-keeps-another-model",
          (code, [u[0] for u in tracker.updates]),
          (EX_OK, ["KIT-1", "KIT-2", "KIT-8", "KIT-10", "KIT-11", "KIT-14"]))

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
    code, io, tracker = go(tracker=_FakeTracker(tickets(), store_as=lambda d: d.replace(
        "[model=", "[mode=")))
    check("tag-not-kept-stops-before-the-conf",
          (code, len(tracker.updates), io.lane.ran,
           open(c_path, encoding="utf-8").read() == c_before), (EX_FAILED, 1, [], True))
    code, io, tracker = go(tracker=_FakeTracker(tickets(), store_as=lambda d: d.replace(
        "[model=opus]", "\\[model=opus\\]").replace("[repo=", "\\[repo=")))
    check("tag-read-back-takes-linears-escape", (code, len(tracker.updates)), (EX_OK, 6))
    t = _FakeTracker(tickets(), store_as=lambda d: "\n" + d.replace("[repo=", "(repo="))
    check("tag-refuses-a-planning-ticket-that-lost-its-routing",
          tag_one(t, t.issues["id-KIT-10"], PLANNING, "opus"),
          ("failed", "its routing tag no longer opens it"))
    check("listing-pages-through", [i[0]["identifier"] for i in pipeline_tickets(
        _FakeTracker(tickets(), page=1), {PLANNING: "opus", PIECE: "opus",
                                         FINDING: "opus"})[0]],
          ["KIT-1", "KIT-2", "KIT-8", "KIT-10", "KIT-11", "KIT-14"])

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
