#!/usr/bin/env python3
"""Move the tech lead to another model, and keep every ticket the pipeline makes on its own.

    python3 scripts/pipeline_model_switch.py --to sonnet [--backup haiku]
        [--conf stage-a.conf] [--stage-e-conf stage-e.conf] [--chat-lane-conf chat-lane.conf]
    python3 scripts/pipeline_model_switch.py --selftest

Run it in a terminal, as yourself, from the kit checkout. It checks, shows, asks one yes,
applies, and verifies. Card CK-C8 (`pipeline_chat_lane_setup.py card CK-C8`) is the same
steps on paper.

WHY IT IS ONE SCRIPT (KIT-385). A chat turn runs on the dispatcher's one global pair,
`claudeDefaultModel` and `claudeDefaultFallbackModel`; no setting picks a model for chat
alone. A ticket session with no `[model=…]` tag and no model label runs on that same
model, since a repository entry's own `model` is never read (cyrus 0.2.73
RunnerSelectionService.js:297-310; KIT-377). So moving the tech lead moves every untagged
ticket with it, and the order matters: the tickets first, then the pair.

WHAT IT DOES, IN ORDER
  1. Reads the three confs through their installers. TICKET_MODEL must be set in
     stage-a.conf and stage-e.conf: it is the `[model=…]` line the planner, the executor
     and the finding poller open each ticket with (KIT-379, KIT-380).
  2. Reads the planner's and the finding poller's own configs, as their role accounts, and
     checks each carries that model. A conf the update never applied tags nothing.
  3. Lists the open tickets the pipeline filed before the tags existed: planned pieces
     (`provenance:epic`) and findings (`provenance:agent`, never an epic), not started,
     with no `[model=…]` tag and no model label. Each would move with the default.
  4. Shows those tickets, the line it writes in chat-lane.conf, and the pair it asks the
     chat lane's `merge` to write. Asks ONE yes.
  5. Opens each ticket's description with `[model=<its TICKET_MODEL>]` and reads it back;
     writes CHAT_MODEL (and CHAT_BACKUP_MODEL) into chat-lane.conf; runs the chat lane's
     `merge --apply`, then its `verify`.

WHAT IT NEVER DOES
  It never runs under a model: a session that could move the dispatcher's model could
  move its own, so it refuses in an agent environment, before it reads anything. It never
  labels a ticket, moves one, or touches a started one, and it never prints a key: the
  Linear key is yours, read the way the idea gate's installer reads it (your environment,
  the review jobs' stored copy, or a hidden prompt), and never written anywhere.

UNDO: run it again with `--to opus`. Taking CHAT_MODEL out is NOT undo: unset, `merge`
leaves the pair as it is, and a config key removed by hand keeps its old value in the
running dispatcher until a restart (ConfigManager.js:237-244).

Exit: 0 switched, and verify measured every row · 10 switched, and verify waits on one
chat turn on the new pair (ask the bot one question in a new thread, then run the verify
command it prints); or you said no, and nothing changed · 2 a conf needs fixing first ·
3 refused (an agent environment, or root) · 4 could not check something (the lines say
what) · 5 a step failed, and nothing after it ran.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# The agent-env markers are IMPORTED from the dispatcher module, never copied, so every
# script in the kit means the same thing by "an agent environment".
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

EX_OK, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_FAILED, EX_WAITING = 0, 2, 3, 4, 5, 10
CHAT_LANE = os.path.join(HERE, "pipeline_chat_lane_setup.py")

# Which ticket gets which conf's TICKET_MODEL: a planned piece the executor filed carries
# provenance:epic and the planner's model; a finding carries provenance:agent and the
# finding poller's. The executor's epic carries provenance:agent too, and is never tagged
# (KIT-379 tags pieces, not epics), so a provenance:agent ticket with children is skipped.
PIECE_LABEL, FINDING_LABEL = "provenance:epic", "provenance:agent"
# The dispatcher lowercases a ticket's labels and takes these as its model, or a
# `provider/model` label (RunnerSelectionService.js, resolveModelFromLabel and
# resolveProviderModelFromLabel, at 0.2.73). A ticket that has one already keeps its model.
MODEL_LABELS = ("fable", "opus", "sonnet", "haiku")
_PROVIDER_MODEL_LABEL_RE = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.:/-]+$", re.IGNORECASE)
# The two live configs that must carry the tag's model, under each conf's role account.
LIVE_CONFIGS = (("stage-a.conf", "the planner job", ".stage-a/poller.json"),
                ("stage-e.conf", "the finding poller", ".stage-e/finding/poller.json"))

Q_OPEN_TICKETS = """
query OpenPipelineTickets($after: String) {
  issues(first: 100, after: $after, filter: {
    labels: { some: { name: { in: ["provenance:epic", "provenance:agent"] } } },
    state: { type: { in: ["triage", "backlog", "unstarted"] } }
  }) {
    nodes { id identifier title description state { type }
            labels { nodes { name } } children(first: 1) { nodes { id } } }
    pageInfo { hasNextPage endCursor }
  }
}"""
Q_TICKET = """
query Ticket($id: String!) {
  issue(id: $id) { id identifier description state { type } labels { nodes { name } } }
}"""
M_DESCRIPTION = """
mutation Tag($id: String!, $description: String!) {
  issueUpdate(id: $id, input: { description: $description }) {
    success issue { id description }
  }
}"""


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

    def run(self, argv):
        """A child's exit code; its output goes straight to the terminal."""
        try:
            return subprocess.call(argv, cwd=KIT)
        except OSError as exc:
            self.say("  could not start %s: %s" % (argv[1] if len(argv) > 1 else argv, exc))
            return None

    def installers(self):
        """The three installer modules, imported only once the agent check has passed."""
        import pipeline_chat_lane_setup as chat
        import pipeline_stage_a_setup as stage_a
        import pipeline_stage_e_setup as stage_e
        return stage_a, stage_e, chat

    def sudo(self, stage_a):
        return stage_a._acquire_sudo("run the same command again")

    def host(self, stage_a):
        return stage_a.Host()

    def tracker(self, stage_a, conf):
        key, source = stage_a.operator_key(conf, stage_a.Host(), True, True, self.say)
        return (stage_a.LinearTransport(key), source) if len(key) >= 20 else (None, source)

    def read_text(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def write_text(self, path, text):
        """In place of the old file, keeping its mode: a temporary file beside it, then a
        rename, so a reader never sees half of it."""
        mode = os.stat(path).st_mode & 0o777
        tmp = "%s.model-switch.%d" % (path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)


def has_model_label(labels):
    """True when the dispatcher would take a model from one of these labels."""
    for name in labels:
        low = (name or "").lower()
        if low in MODEL_LABELS or _PROVIDER_MODEL_LABEL_RE.match(low):
            return True
    return False


def ticket_kind(issue):
    """PIECE_LABEL or FINDING_LABEL for a ticket this switch tags, else None."""
    labels = [l.get("name") for l in ((issue.get("labels") or {}).get("nodes") or [])]
    if PIECE_LABEL in labels:
        return PIECE_LABEL
    if FINDING_LABEL in labels and not ((issue.get("children") or {}).get("nodes") or []):
        return FINDING_LABEL
    return None


def tagged_body(description, model):
    """The description with the tag as its first line, as the planner and the pollers
    write it (KIT-379, KIT-380)."""
    return "[model=%s]\n\n%s" % (model, description or "")


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


def open_untagged(tracker, models, dispatcher_model):
    """[(issue, kind, model)] — every open, unstarted ticket this switch would tag."""
    out, after = [], None
    for _ in range(50):                      # 5000 tickets: far past any real backlog
        data = tracker.post(Q_OPEN_TICKETS, {"after": after})
        page = (data or {}).get("issues") or {}
        for issue in page.get("nodes") or []:
            kind = ticket_kind(issue)
            labels = [l.get("name") for l in ((issue.get("labels") or {}).get("nodes") or [])]
            if (kind and not has_model_label(labels)
                    and dispatcher_model(issue.get("description") or "", None) is None):
                out.append((issue, kind, models[kind]))
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return out
        after = info.get("endCursor")
    raise RuntimeError("more than 5000 open pipeline tickets: stopped listing them")


def tag_one(tracker, issue, model, dispatcher_model):
    """(True, None) once the ticket's description opens with the tag and reads back so, or
    (False, why). Read again just before the write, so an edit made since the list is kept,
    and a ticket started since the list is left alone."""
    fresh = ((tracker.post(Q_TICKET, {"id": issue["id"]}) or {}).get("issue")) or {}
    if not fresh:
        return False, "it could not be read again"
    if ((fresh.get("state") or {}).get("type")) not in ("triage", "backlog", "unstarted"):
        return False, "it has started since the list was made, so it was left alone"
    labels = [l.get("name") for l in ((fresh.get("labels") or {}).get("nodes") or [])]
    if has_model_label(labels) or dispatcher_model(fresh.get("description") or "", None):
        return True, None                     # someone gave it a model meanwhile
    data = tracker.post(M_DESCRIPTION, {"id": issue["id"],
                                        "description": tagged_body(fresh.get("description"),
                                                                   model)})
    result = (data or {}).get("issueUpdate") or {}
    stored = ((result.get("issue") or {}).get("description")) or ""
    if not result.get("success") or dispatcher_model(stored, None) != model:
        return False, "Linear did not store the tag as written"
    return True, None


def main(argv=None, io=None):
    io = io or IO()
    parser = argparse.ArgumentParser(
        prog="pipeline_model_switch.py",
        description="Move the tech lead to another model, keeping every ticket the "
                    "pipeline makes on its own (KIT-385).")
    parser.add_argument("--to", help="the model chat turns move to: fable, opus, sonnet, "
                                     "haiku or a claude- model id")
    parser.add_argument("--backup", default="",
                        help="its backup; the dispatcher's own pick for --to when left out")
    parser.add_argument("--conf", default=os.path.join(KIT, "stage-a.conf"),
                        help="the idea gate's settings (default: stage-a.conf in the kit)")
    parser.add_argument("--stage-e-conf", default=os.path.join(KIT, "stage-e.conf"),
                        help="the review jobs' settings (default: stage-e.conf in the kit)")
    parser.add_argument("--chat-lane-conf", default=os.path.join(KIT, "chat-lane.conf"),
                        help="the chat lane's settings (default: chat-lane.conf in the kit)")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    say = io.say

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
            "terminal.")
        return EX_USAGE
    if not args.to:
        say("Name the model chat turns move to: --to sonnet (or opus to go back).")
        return EX_USAGE
    stage_a, stage_e, chat = io.installers()
    from pipeline_plan_executor import dispatcher_model

    # 1. The three confs, each through its own installer.
    a_conf, a_errors = stage_a.load_conf(os.path.abspath(args.conf))
    try:
        e_conf, e_errors = stage_e.load_conf(os.path.abspath(args.stage_e_conf))
    except stage_e.SetupError as exc:          # it raises on a file it cannot read
        e_conf, e_errors = None, [str(exc).split("\n")[0]]
    c_path = os.path.abspath(args.chat_lane_conf)
    c_conf, c_errors = chat.load_conf(c_path)
    problems = (["stage-a.conf: " + e for e in a_errors] + ["stage-e.conf: " + e for e in e_errors]
                + ["chat-lane.conf: " + e for e in c_errors])
    # Left out, --backup means the dispatcher's own pick, so a backup already in the conf
    # is cleared: the file then holds exactly the pair checked here.
    want = dict(c_conf or {}, CHAT_MODEL=args.to, CHAT_BACKUP_MODEL=args.backup)
    problems += ["--to/--backup: " + e for e in chat.chat_model_problems(want)]
    models = {PIECE_LABEL: (a_conf or {}).get("TICKET_MODEL") or "",
              FINDING_LABEL: (e_conf or {}).get("TICKET_MODEL") or ""}
    for name, kind in (("stage-a.conf", PIECE_LABEL), ("stage-e.conf", FINDING_LABEL)):
        if not models[kind]:
            problems.append("%s: TICKET_MODEL is not set. Set it to the model tickets keep "
                            "(opus today), run the update (pipeline_install.py), then this "
                            "again. Without it, the tickets move with the tech lead" % name)
    if problems:
        say("Nothing was changed. Fix these first:")
        for p in problems:
            say("  - " + p)
        return EX_USAGE
    pair = chat.chat_pair_wanted(want)
    say("Moving the tech lead to %s, with %s as the backup." % pair)
    say("Planned pieces keep %s, and findings keep %s." % (models[PIECE_LABEL],
                                                         models[FINDING_LABEL]))

    # 2. The live configs carry the model: what the update applied, not only what the conf says.
    io.sudo(stage_a)
    host = io.host(stage_a)
    for (name, what, relpath), conf, kind in zip(LIVE_CONFIGS, (a_conf, e_conf),
                                                 (PIECE_LABEL, FINDING_LABEL)):
        text = host.read_role_file(conf["ROLE_ACCOUNT"], relpath)
        if text is None:
            say("NOT CHECKED: %s's config (~%s/%s) could not be read as %s. Nothing was "
                "changed. Run `sudo -v`, then this again." % (what, conf["ROLE_ACCOUNT"],
                                                               relpath, conf["ROLE_ACCOUNT"]))
            return EX_UNKNOWN
        try:
            live = (json.loads(text) or {}).get("ticket_model") if text else None
        except ValueError:
            live = "unreadable"
        if live != models[kind]:
            say("Nothing was changed: %s still runs without TICKET_MODEL=%s (its config "
                "says %s). Run the update (pipeline_install.py), then this again."
                % (what, models[kind], "no ticket_model" if not live else repr(live)))
            return EX_USAGE

    # 3. The open tickets filed before the tags existed.
    tracker, source = io.tracker(stage_a, a_conf)
    if tracker is None:
        say("NOT CHECKED: no Linear key (%s). Nothing was changed. Export yours under the "
            "name stage-a.conf gives OPERATOR_KEY_ENV, then run this again." % source)
        return EX_UNKNOWN
    try:
        todo = open_untagged(tracker, models, dispatcher_model)
    except Exception as exc:  # noqa: BLE001 - a tracker failure is reported, never a traceback
        say("NOT CHECKED: the open tickets could not be listed (%s). Nothing was changed."
            % type(exc).__name__)
        return EX_UNKNOWN

    # 4. Everything it will do, then one yes.
    say("")
    say("It will:")
    if todo:
        say("  - open %d ticket(s) with their model tag:" % len(todo))
        for issue, kind, model in todo:
            say("      %s  [model=%s]  %s" % (issue.get("identifier"), model,
                                             (issue.get("title") or "")[:60]))
    else:
        say("  - tag no ticket: every open pipeline ticket already keeps its model")
    say("  - set CHAT_MODEL=%s%s in %s" % (
        args.to, ", CHAT_BACKUP_MODEL=%s" % (args.backup or "(cleared: the dispatcher's own "
                                                             "pick)")
        if args.backup or (c_conf or {}).get("CHAT_BACKUP_MODEL") else "", c_path))
    say("  - run the chat lane's  merge --apply , which writes claudeDefaultModel = %s and "
        "claudeDefaultFallbackModel = %s in one write, then its  verify" % pair)
    say("")
    if not io.ask("Move the tech lead to %s now?" % args.to):
        say("Nothing was changed.")
        return EX_WAITING

    # 5. Apply, in that order: the tickets, the conf, the pair.
    for issue, kind, model in todo:
        try:
            ok, why = tag_one(tracker, issue, model, dispatcher_model)
        except Exception as exc:  # noqa: BLE001
            ok, why = False, "the tracker call failed (%s)" % type(exc).__name__
        if not ok:
            say("STOPPED at %s: %s. The tickets before it are tagged; chat-lane.conf and "
                "the dispatcher are unchanged. Run this again." % (issue.get("identifier"), why))
            return EX_FAILED
        say("  tagged %s with [model=%s]" % (issue.get("identifier"), model))
    values = {"CHAT_MODEL": args.to}
    if args.backup or (c_conf or {}).get("CHAT_BACKUP_MODEL"):
        values["CHAT_BACKUP_MODEL"] = args.backup
    old = io.read_text(c_path)
    try:
        new = conf_with(old, values)
    except ValueError as exc:
        say("STOPPED: %s. The tickets are tagged; the dispatcher is unchanged." % exc)
        return EX_FAILED
    io.write_text(c_path, new)
    if chat.load_conf(c_path)[1]:
        io.write_text(c_path, old)
        say("STOPPED: chat-lane.conf did not validate after the edit, so it was put back. "
            "The tickets are tagged; the dispatcher is unchanged.")
        return EX_FAILED
    say("  set %s in %s" % (", ".join("%s=%s" % kv for kv in values.items()), c_path))
    rc = io.run([sys.executable, CHAT_LANE, "merge", "--apply", "--conf", c_path])
    if rc != chat.EX_OK:
        say("STOPPED: the chat lane's merge exited %s, so the pair may not be written. Its "
            "lines above say why; fix that, then run this again." % rc)
        return EX_FAILED
    verify = [sys.executable, CHAT_LANE, "verify", "--conf", c_path]
    rc = io.run(verify)
    say("")
    if rc == chat.EX_OK:
        say("Done: the tech lead runs on %s, with %s as the backup." % pair)
        return EX_OK
    if rc == chat.EX_UNKNOWN:
        say("Switched. Ask the bot one question in a NEW thread, then run verify again; its "
            "chat-model row says NOT CHECKED until a turn has run on the new pair:")
        say("    %s" % " ".join(verify[1:]))
        return EX_WAITING
    say("Switched, but verify exited %s: its rows above say what is wrong." % rc)
    return EX_FAILED


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


def _issue(identifier, labels, description="", state="backlog", children=0):
    return {"id": "id-" + identifier, "identifier": identifier, "title": "t " + identifier,
            "description": description, "state": {"type": state},
            "labels": {"nodes": [{"name": n} for n in labels]},
            "children": {"nodes": [{"id": "c"}] * children}}


class _FakeTracker(object):
    def __init__(self, issues, store_as=None, page=2):
        self.issues = dict((i["id"], i) for i in issues)
        self.order = [i["id"] for i in issues]
        self.updates, self.page, self.store_as = [], page, store_as

    def post(self, query, variables=None):
        variables = variables or {}
        if "OpenPipelineTickets" in query:
            start = int(variables.get("after") or 0)
            ids = self.order[start:start + self.page]
            nodes = [i for i in (self.issues[x] for x in ids)
                     if any(l["name"] in ("provenance:epic", "provenance:agent")
                            for l in i["labels"]["nodes"])
                     and i["state"]["type"] in ("triage", "backlog", "unstarted")]
            more = start + self.page < len(self.order)
            return {"issues": {"nodes": nodes, "pageInfo": {
                "hasNextPage": more, "endCursor": str(start + self.page)}}}
        if "query Ticket" in query:
            return {"issue": self.issues.get(variables["id"])}
        if "mutation Tag" in query:
            issue = self.issues[variables["id"]]
            stored = (self.store_as or (lambda d: d))(variables["description"])
            issue["description"] = stored
            self.updates.append((issue["identifier"], variables["description"]))
            return {"issueUpdate": {"success": True, "issue": {"id": issue["id"],
                                                               "description": stored}}}
        raise AssertionError("unexpected query")


class _FakeHost(object):
    def __init__(self, files):
        self.files = files

    def read_role_file(self, account, relpath):
        return self.files.get((account, relpath))


class _FakeIO(IO):
    def __init__(self, tracker, host, env=None, uid=501, tty=True, answer=True, runs=None,
                 key_found=True):
        self._tracker, self._host, self._env = tracker, host, env or {}
        self._uid, self._tty, self.answer = uid, tty, answer
        self._runs = list(runs or [])
        self.ran, self.out, self.asked, self.key_found = [], [], [], key_found

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

    def run(self, argv):
        self.ran.append(argv[2:])
        return self._runs.pop(0)

    def sudo(self, stage_a):
        return None

    def host(self, stage_a):
        return self._host

    def tracker(self, stage_a, conf):
        return (self._tracker, "$TEST") if self.key_found else (None, "no key in $TEST")

    def text(self):
        return "\n".join(self.out)


def _selftest_cases(check, tmp, failures, cases):
    import pipeline_chat_lane_setup as chat
    import pipeline_stage_a_setup as stage_a
    import pipeline_stage_e_setup as stage_e
    from pipeline_plan_executor import dispatcher_model

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
    live = {(a_role, ".stage-a/poller.json"): json.dumps({"ticket_model": "opus"}),
            (e_role, ".stage-e/finding/poller.json"): json.dumps(
                {"ticket_model": "claude-opus-5-5"})}
    base = ["--to", "sonnet", "--conf", a_path, "--stage-e-conf", e_path,
            "--chat-lane-conf", c_path]
    c_before = open(c_path, encoding="utf-8").read()

    def tickets():
        return [
            _issue("KIT-1", ["provenance:epic"], "a planned piece"),
            _issue("KIT-2", ["provenance:agent"], "> Filed by the Stage E finding poller"),
            _issue("KIT-3", ["provenance:agent"], "an epic", children=2),
            _issue("KIT-4", ["provenance:epic"], "[model=opus]\n\nalready tagged"),
            _issue("KIT-5", ["provenance:epic", "Sonnet"], "a model label"),
            _issue("KIT-6", ["provenance:epic", "claude/opus"], "a provider/model label"),
            _issue("KIT-7", ["provenance:epic"], "x \\[model=haiku\\] y"),
            _issue("KIT-8", ["provenance:epic"], "started", state="started"),
            _issue("KIT-9", ["Bug"], "hand-filed"),
        ]

    def go(argv=None, **kw):
        with open(c_path, "w", encoding="utf-8") as fh:
            fh.write(c_before)
        tracker = kw.pop("tracker", None) or _FakeTracker(tickets())
        kw.setdefault("runs", [chat.EX_OK, chat.EX_UNKNOWN])   # merge wrote; no turn yet
        io = _FakeIO(tracker, _FakeHost(kw.pop("files", live)), **kw)
        code = main(base if argv is None else argv, io)
        return code, io, tracker

    # 1. Refused before anything is read: every agent marker, root, no terminal.
    for marker in AGENT_ENV_MARKERS:
        code, io, tracker = go(env={marker: ""})
        check("refused-under:%s" % marker, (code, io.ran, tracker.updates), (EX_REFUSED, [], []))
    code, io, _t = go(uid=0)
    check("refused-as-root", (code, io.ran), (EX_REFUSED, []))
    code, io, _t = go(tty=False)
    check("refused-without-a-person", (code, io.ran), (EX_USAGE, []))

    # 2. The whole switch: two tickets tagged with their own model, the conf line written,
    #    merge --apply then verify, and verify's wait on a chat turn passed on as 10.
    code, io, tracker = go()
    check("switch-exit-waits-on-a-turn", code, EX_WAITING)
    check("switch-tags-only-untagged-open-pieces-and-findings",
          [u[0] for u in tracker.updates], ["KIT-1", "KIT-2"])
    check("switch-tag-is-the-first-line-and-each-labels-own-model",
          [u[1].split("\n", 1)[0] for u in tracker.updates],
          ["[model=opus]", "[model=claude-opus-5-5]"])
    check("switch-tag-keeps-the-description", tracker.updates[0][1],
          "[model=opus]\n\na planned piece")
    check("switch-asks-once", len(io.asked), 1)
    check("switch-runs-merge-then-verify",
          io.ran, [["merge", "--apply", "--conf", c_path], ["verify", "--conf", c_path]])
    written = open(c_path, encoding="utf-8").read()
    check("switch-writes-chat-model", (chat.load_conf(c_path)[1],
                                       chat.chat_pair_wanted(chat.load_conf(c_path)[0])),
          ([], ("sonnet", "haiku")))
    check("switch-keeps-the-conf-mode", oct(os.stat(c_path).st_mode & 0o777), oct(0o640))
    check("switch-changes-only-that-line",
          [l for l in written.split("\n") if l not in c_before.split("\n")], ["CHAT_MODEL=sonnet"])
    check("switch-lists-the-tickets-it-tags",
          ("KIT-1  [model=opus]" in io.text(), "KIT-2  [model=claude-opus-5-5]" in io.text(),
           "KIT-3" in io.text(), "KIT-9" in io.text()), (True, True, False, False))
    code, io, _t = go(runs=[chat.EX_OK, chat.EX_OK])
    check("switch-verify-clean-is-done", (code, "Done: the tech lead runs on sonnet" in io.text()),
          (EX_OK, True))
    code, io, _t = go(runs=[chat.EX_OK, chat.EX_FAILED])
    check("switch-verify-failed-is-a-failure", code, EX_FAILED)
    code, io, _t = go(runs=[chat.EX_FAILED])
    check("switch-merge-failed-stops-before-verify", (code, len(io.ran)), (EX_FAILED, 1))

    # 3. A backup of your own, and the way back.
    code, io, _t = go(base + ["--backup", "opus"])
    check("switch-with-a-backup", chat.chat_pair_wanted(chat.load_conf(c_path)[0]),
          ("sonnet", "opus"))
    code, io, _t = go(["--to", "opus"] + base[2:])
    check("switch-back-to-opus", (code, chat.chat_pair_wanted(chat.load_conf(c_path)[0])),
          (EX_WAITING, ("opus", "sonnet")))

    # 3b. A backup already in the conf does not outlive a switch that names none: it is
    #     cleared, so the file holds the pair that was checked.
    with open(c_path, "w", encoding="utf-8") as fh:
        fh.write(conf_with(c_before, {"CHAT_MODEL": "sonnet", "CHAT_BACKUP_MODEL": "opus"}))
    with_backup = open(c_path, encoding="utf-8").read()
    tracker = _FakeTracker(tickets())
    io = _FakeIO(tracker, _FakeHost(live), runs=[chat.EX_OK, chat.EX_UNKNOWN])
    code = main(["--to", "opus"] + base[2:], io)
    check("switch-clears-a-backup-it-was-not-given",
          (code, chat.chat_pair_wanted(chat.load_conf(c_path)[0]),
           "CHAT_BACKUP_MODEL=" in open(c_path, encoding="utf-8").read().split("\n")),
          (EX_WAITING, ("opus", "sonnet"), True))
    del with_backup

    # 3c. A conf that does not validate after the edit is put back as it was read, and the
    #     dispatcher is not touched: here the file changed under the switch.
    class _Moved(_FakeIO):
        def read_text(self, path):
            return IO.read_text(self, path) + "DISPATCHER_PORT=0\n"

    io = _Moved(_FakeTracker(tickets()), _FakeHost(live), runs=[chat.EX_OK, chat.EX_UNKNOWN])
    with open(c_path, "w", encoding="utf-8") as fh:
        fh.write(c_before)
    code = main(base, io)
    check("switch-puts-back-a-conf-that-fails-after-the-edit",
          (code, io.ran, "DISPATCHER_PORT=0" in open(c_path, encoding="utf-8").read(),
           "CHAT_MODEL=sonnet" in open(c_path, encoding="utf-8").read().split("\n")),
          (EX_FAILED, [], True, False))

    # 4. No: nothing changes, not a ticket, not the conf, nothing runs.
    code, io, tracker = go(answer=False)
    check("no-changes-nothing", (code, tracker.updates, io.ran,
                                 open(c_path, encoding="utf-8").read() == c_before),
          (EX_WAITING, [], [], True))

    # 5. Each check that must hold first, each stopping before the question.
    def with_file(key, text):
        files = dict(live)
        files[key] = text
        return files

    for name, kw, want in (
            ("bad-model", {"argv": ["--to", "gpt-5"] + base[2:]}, EX_USAGE),
            ("same-backup", {"argv": base + ["--backup", "sonnet"]}, EX_USAGE),
            ("no-key", {"key_found": False}, EX_UNKNOWN),
            ("stage-e-conf-missing",
             {"argv": base[:5] + [os.path.join(tmp, "absent.conf")] + base[6:]}, EX_USAGE),
            ("planner-config-unreadable",
             {"files": with_file((a_role, ".stage-a/poller.json"), None)}, EX_UNKNOWN),
            ("planner-config-without-the-model",
             {"files": with_file((a_role, ".stage-a/poller.json"), "{}")}, EX_USAGE),
            ("finding-config-another-model",
             {"files": with_file((e_role, ".stage-e/finding/poller.json"),
                                 json.dumps({"ticket_model": "haiku"}))}, EX_USAGE)):
        code, io, tracker = go(**kw)
        check("stops:%s" % name, (code, io.asked, tracker.updates, io.ran), (want, [], [], []))
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

    # 6. Tagging, each guard: a ticket that started since the list is left, one given a
    #    model meanwhile is counted done, a tag Linear did not keep stops the run.
    t = _FakeTracker(tickets())
    t.issues["id-KIT-1"]["state"]["type"] = "started"
    check("tag-leaves-a-started-ticket", tag_one(t, t.issues["id-KIT-1"], "opus",
                                                 dispatcher_model)[0], False)
    t = _FakeTracker(tickets())
    t.issues["id-KIT-1"]["description"] = "[model=haiku] now"
    check("tag-counts-a-ticket-given-a-model-meanwhile",
          (tag_one(t, t.issues["id-KIT-1"], "opus", dispatcher_model), t.updates),
          ((True, None), []))
    code, io, tracker = go(tracker=_FakeTracker(tickets(), store_as=lambda d: d.replace(
        "[model=", "[mode=")))
    check("tag-not-kept-stops-before-the-conf",
          (code, len(tracker.updates), io.ran, open(c_path, encoding="utf-8").read() == c_before),
          (EX_FAILED, 1, [], True))
    code, io, tracker = go(tracker=_FakeTracker(tickets(), store_as=lambda d: d.replace(
        "[model=opus]", "\\[model=opus\\]")))
    check("tag-read-back-takes-linears-escape", (code, [u[0] for u in tracker.updates]),
          (EX_WAITING, ["KIT-1", "KIT-2"]))
    check("listing-pages-through", [i[0]["identifier"] for i in open_untagged(
        _FakeTracker(tickets(), page=1), {"provenance:epic": "opus", "provenance:agent": "opus"},
        dispatcher_model)], ["KIT-1", "KIT-2"])

    # 7. The conf edit: live line replaced, commented example made live, else added; two
    #    live lines refused.
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

    # 8. The source never prints a key, never labels or moves a ticket, and imports the
    #    agent markers rather than keeping a copy.
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
