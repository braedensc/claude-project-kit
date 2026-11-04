#!/usr/bin/env python3
"""The queue's reader: the tracker and three record files, read into one inputs file for the
morning job (KIT-445, MVP 2 part 6a; docs/adr/2026-11-03-autonomous-workers-within-a-budget-and-a-window.md).

    python3 scripts/pipeline_queue_read.py --config <queue.json> [--out PATH] [--dry-run]
    python3 scripts/pipeline_queue_read.py --selftest

A READER AND NOTHING ELSE. It decides nothing: scripts/pipeline_queue_pool.py says which tickets
may start, scripts/pipeline_queue_order.py in what order and lanes. This reads what they are given,
so its file feeds `pipeline_queue_pool.decide(...)` as that module documents. The bridge's hand-over
will call `gather()` in-process. It sends GraphQL queries only, the key in one request header. It
imports the pool, the order, the status job (transport, key check, git-tree check), the finding
poller (record reader) and the readiness gate: an installer lists those, and theirs, with it.

WHAT IT IS GIVEN.  The queue's config file: one JSON object of sections, from SECTIONS and no
other. `pool` and `order` are checked by those modules' `config()`, `reader` here (DEFAULTS); the
morning job's `planner`, `window` and `morning` are passed over unread. The key is read from the
environment variable `reader.linear_key_env` names. `gather(raw_config, gql, now, clock)` takes
the transport instead: `gql(query, variables) -> data`, raising SourceError.

WHAT IT READS, each question counted against `max_requests` and the clock: the candidates (the
pool's `team_keys`, ready states, and backlog states when the pool reads them; past `max_tickets`
the rest are counted, not read); the running work, second, so a cap cuts per-ticket reads and never
this (a hand-off state holds a lane by its pull request, a started ticket handed to a dispatcher
user by its session); the plans, and only plans (a candidate's parent the idea gate labelled
`provenance:agent`, a candidate that is such a plan, the epics in the bridge's record: the pool
reads every epic as a plan, so a sub-issue of an ordinary parent stays an ordinary ticket; a plan
past `max_pages` is left out, and its pieces among the candidates held); each issue's whole
history and whether a dispatcher's agent session commented on it (whole or null, never a part);
the three records.

WHAT IT RETURNS.  One document, schema SCHEMA: `read_at`, `date` (the machine-local day, as the
usage job names one), `result`, `sources` (`tracker`, `findings`, `plans`, `ledger`: `state` and
`reason`), `tickets`, `epics`, `histories`, `busy`, `findings`, `plans`, `ledger`, `extras`,
`running`. What was not read is null or empty and said, never zero. `result` is `ok` (every source
on was read whole), `partial` (one was not) or `failed` (candidates or running work not read at
all: lists empty, no record read). Reasons are fixed words and numbers, never a title, a text or an
error's words. The file is written for every result, atomically, mode 0600, so a stale one is never
taken for today's. Exit: 0 ok, 3 partial, 1 failed, 2 bad arguments or config.

WHERE IT CANNOT HELP.  The queue's starter writes a ledger row before every start, so no ledger
file means no start was made: today at zero (a lost ledger reads the same). A ticket whose session
ended but is still started reads as running, the safe side. Past `max_tickets` the tracker's own
order (newest first) decides which are read. The file holds raw titles and Pointers lines for the
planner: private to the account, never printed.

LIVE-TEST ITEMS (coded defensively; verify on the first real run and amend here).
  - The history's edit fields (HISTORY_FIELDS) are the pool's; without them every ticket is held.
  - Linear refuses a query over 10,000 complexity points, and nested lists multiply it. The
    selftest estimates each query by the published rule, counting generously, and holds them
    under COMPLEXITY_CAP. If a live read is still refused as too complex (the cause is on
    stderr), lower PAGE first.
  - The filters' shapes, and that a hand-off ticket's pull request is among its first 20 links.
"""
import argparse
import json
import os
import pwd
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import check_ticket_dor as dor
    from pipeline_finding_poller import PollerError, load_seen
    from pipeline_queue_order import config as order_config
    from pipeline_queue_pool import (GATE_EPIC_LABEL, HISTORY_KEYS, TEAM_RE, config as pool_config,
                                     dig, has_children, iso, label_names, local_date, pointer_lines)
    from pipeline_status_local import (ENV_NAME_RE, PR_URL_RE, REPO_RE, SourceError, credential,
                                       inside_git_worktree, linear_graphql)
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The reader uses those files' own definitions and keeps no copy." % exc)

SCHEMA = "pipeline-queue-inputs/1"
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3
OK, PARTIAL, FAILED = "ok", "partial", "failed"
READ, PARTLY, COULD_NOT, OFF = "read", "partly-read", "could-not-read", "off"
SOURCES = ("tracker", "findings", "plans", "ledger")
# The sections of the queue's one config file. The morning job writes the same list: the two
# modules do not import each other.
SECTIONS = ("pool", "order", "planner", "window", "reader", "morning")
NO_RECORD, UNREADABLE = "no record yet", "the record could not be read"
NOT_READ = "not read: the tracker was not read"
HALTED = {"time": "the pass ran out of time (run_timeout_seconds)",
          "requests": "the pass reached its cap of requests (max_requests)",
          "down": "the tracker did not answer"}
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
LABEL_RE = re.compile(r"^\S.{0,79}\Z")

DEFAULTS = {
    "linear_key_env": "STAGE_E_LINEAR_API_KEY",     # the NAME of the variable holding the key
    "repo_by_team": {},                             # {"ENG": "acme/app"}: a work team's repository
    "repo_by_label": {},                            # {"repo:web": "acme/web"}: wins over the team
    "findings_file": "",                            # the finding poller's seen.json; "" is off
    "bridge_state_file": "",                        # the bridge's bridge-state.json; "" is off
    "ledger_file": "~/.stage-e/queue/ledger.json",  # the queue's ledger of starts; "" is off
    "inputs_file": "~/.stage-e/queue/inputs.json",  # where the inputs file is written
    "max_requests": 1000,                           # tracker requests in one pass
    "max_tickets": 300,                             # candidates read in one pass
    "max_pages": 40,                                # pages of any one list (400 tickets)
    "run_timeout_seconds": 600,                     # how long one pass may read
}
LIMITS = {"max_requests": (1, 10000), "max_tickets": (1, 2000), "max_pages": (1, 200),
          "run_timeout_seconds": (30, 3600)}
PATH_KEYS = ("findings_file", "bridge_state_file", "ledger_file", "inputs_file")

# -- The queries: all of them `query`, none a mutation. Linear refuses a query whose complexity is
# over 10,000 points, and a nested list multiplies it, so the sizes are the bridge's own (20 labels,
# 20 relations: the plan's fingerprint must read what the bridge read) and COMPLEXITY_CAP pins them.
PAGE = 10                  # tickets, or a plan's pieces, in one page: each carries ISSUE_FIELDS
LIST_PAGE = 50             # a page of the running work, or of a history
COMPLEXITY_CAP = 8000      # the selftest's ceiling for each query: Linear's 10,000, with room
# Everything the pool reads of a ticket, a piece of a plan included, and what the order adds. The
# parent's labels say whether it is a plan, so only plans are read as epics.
ISSUE_FIELDS = """id identifier title description createdAt priority
    state { id name type }
    team { key }
    labels(first: 20) { nodes { name } }
    children(first: 1) { nodes { id } }
    delegate { id }
    parent { id identifier labels(first: 20) { nodes { name } } }
    project { id }
    creator { id }
    inverseRelations(first: 20) {
      nodes { type issue { id identifier state { name type } labels(first: 10) { nodes { name } } } }
      pageInfo { hasNextPage }
    }"""
# The history entry's fields: the pool's HISTORY_KEYS. The edit fields are a live-test item.
HISTORY_FIELDS = ("createdAt actorId fromStateId toStateId fromTitle toTitle updatedDescription "
                  "addedLabelIds removedLabelIds fromParentId toParentId "
                  "relationChanges { identifier type }")
Q_CANDIDATES = """query QueueCandidates($filter: IssueFilter!, $after: String) {
  issues(filter: $filter, first: %d, after: $after) {
    nodes { %s }
    pageInfo { hasNextPage endCursor }
  }
}""" % (PAGE, ISSUE_FIELDS)
Q_EPIC = """query QueueEpic($id: String!, $after: String) {
  issue(id: $id) {
    id identifier title description priority createdAt
    state { id name type }
    team { key }
    labels(first: 20) { nodes { name } }
    delegate { id }
    parent { id }
    creator { id }
    project { id }
    children(first: %d, after: $after) {
      nodes { %s }
      pageInfo { hasNextPage endCursor }
    }
  }
}""" % (PAGE, ISSUE_FIELDS)
Q_HISTORY = """query QueueHistory($id: String!, $after: String) {
  issue(id: $id) {
    history(first: %d, after: $after) {
      nodes { %s }
      pageInfo { hasNextPage endCursor }
    }
  }
}""" % (LIST_PAGE, HISTORY_FIELDS)
# The bounce driver's own live-proven read of who ran a session in a comment.
Q_BUSY = """query QueueBusy($id: String!, $after: String) {
  issue(id: $id) {
    comments(first: 100, orderBy: createdAt, after: $after) {
      nodes { agentSession { id appUser { id } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}"""
Q_RUNNING = """query QueueRunning($filter: IssueFilter!, $after: String) {
  issues(filter: $filter, first: %d, after: $after) {
    nodes {
      id identifier state { name type } team { key } delegate { id }
      labels(first: 20) { nodes { name } }
      attachments(first: 20) { nodes { url } }
    }
    pageInfo { hasNextPage endCursor }
  }
}""" % LIST_PAGE


def complexity(query):
    """A query's cost by Linear's published rule, estimated: an object is 1 point, a scalar 0.1, and
    a list's `first` multiplies everything inside it. Read by the selftest, never sent."""
    tokens = re.findall(r"\w+|[{}]|\([^)]*\)", query)

    def selection(i):
        total = 0.0
        while tokens[i] != "}":
            i, many = i + 1, 1
            if tokens[i].startswith("("):
                m = re.search(r"first:\s*(\d+)", tokens[i])
                i, many = i + 1, int(m.group(1)) if m else 1
            if tokens[i] == "{":
                inner, i = selection(i + 1)
                total += many * (1 + inner)
            else:
                total += 0.1
        return total, i + 1
    return selection(tokens.index("{") + 1)[0]


def candidate_filter(pcfg):
    """Issues of the work teams in a ready state, and in a backlog state when the pool reads one."""
    states = list(pcfg["ready_states"])
    if pcfg["backlog"] or pcfg["agent_filed"] != "off":
        states += [s for s in pcfg["backlog_states"] if s not in states]
    return {"team": {"key": {"in": list(pcfg["team_keys"])}}, "state": {"name": {"in": states}}}


def running_filter(pcfg, ocfg):
    """Issues of the work teams that are started, or in a state where a pull request waits."""
    return {"team": {"key": {"in": list(pcfg["team_keys"])}},
            "or": [{"state": {"type": {"eq": "started"}}},
                   {"state": {"name": {"in": list(ocfg["handoff_states"])}}}]}


# -- The config
def account_home():
    """This account's home from the account database, never $HOME, which a shell can move."""
    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        home = ""
    if not home or not os.path.isabs(home):
        raise ValueError("this account has no home, so a path with ~/ cannot be resolved")
    return home


def resolve_path(raw, key, off_when_empty=False):
    """An absolute path to a file: `~/` is the account's home. A relative path, a folder and a file in
    a git working tree, where a session can write, are refused."""
    if not isinstance(raw, str):
        raise ValueError("%s: must be a path" % key)
    if raw == "" and off_when_empty:
        return ""
    path = os.path.join(account_home(), raw[2:]) if raw.startswith("~/") else raw
    if not os.path.isabs(path) or os.path.basename(path) == "":
        raise ValueError("%s: must be an absolute path to a file, or start with ~/%s"
                         % (key, ", or be empty to turn it off" if off_when_empty else ""))
    path = os.path.realpath(path)
    if os.path.isdir(path):
        raise ValueError("%s: names a folder, not a file" % key)
    if inside_git_worktree(os.path.dirname(path)):
        raise ValueError("%s: its folder is inside a git working tree, where a session can write"
                         % key)
    return path


def reader_config(raw):
    """(cfg, problems) for `reader`, each named by key, never by value (the key's own variable could
    quote it). Every number is a runaway cap."""
    cfg = dict((k, raw.get(k, d)) for k, d in DEFAULTS.items())
    bad = ["%s: not a setting" % repr(k)[:40] for k in sorted(raw, key=repr) if k not in DEFAULTS]
    if not (isinstance(cfg["linear_key_env"], str) and ENV_NAME_RE.match(cfg["linear_key_env"])):
        bad.append("linear_key_env: must be the NAME of an environment variable, never the key")
    for key, rx in (("repo_by_team", TEAM_RE), ("repo_by_label", LABEL_RE)):
        table = cfg[key]
        if not isinstance(table, dict):
            bad.append("%s: must be an object mapping names to repositories, owner/name" % key)
            continue
        for name, repo in sorted(table.items(), key=repr):
            if not (isinstance(name, str) and rx.match(name) and isinstance(repo, str)
                    and REPO_RE.match(repo)):
                bad.append("%s: %s does not map to a repository, owner/name" % (key, repr(name)[:40]))
    for key, (low, high) in sorted(LIMITS.items()):
        v = cfg[key]
        if not (isinstance(v, int) and not isinstance(v, bool) and low <= v <= high):
            bad.append("%s: must be a whole number from %d to %d" % (key, low, high))
    for key in PATH_KEYS:
        try:
            cfg[key] = resolve_path(cfg[key], key, off_when_empty=key != "inputs_file")
        except ValueError as exc:
            bad.append(str(exc))
    return cfg, bad


def read_config(raw):
    """(cfg, problems): every problem of every section at once, under its section's name."""
    if not isinstance(raw, dict):
        return None, ["config: not an object"]
    bad = ["config: %s is not a section (the sections are %s)" % (repr(k)[:40], ", ".join(SECTIONS))
           for k in sorted(raw, key=repr) if k not in SECTIONS]
    cfg = {}
    for name in SECTIONS:
        section = cfg[name] = raw.get(name, {})
        judge = {"pool": pool_config, "order": order_config, "reader": reader_config}.get(name)
        if judge is None:                       # the morning job's own: passed over unread
            continue
        if not isinstance(section, dict):
            bad.append("%s: not an object" % name)
            continue
        cfg[name], why = judge(section)
        bad += ["%s: %s" % (name, w) for w in why]
    return cfg, bad


# -- What the order adds to a ticket: small pure rules
def priority_of(value):
    """The tracker's 0 (none) to 4 (low); anything else is 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 4 else 0


def repo_of(reader, node):
    """The repository of a ticket's work. Routing labels win over the team: exactly one distinct
    repository is it, two are none (a guess sends work to the wrong place), no label gives the team's."""
    named = set(reader["repo_by_label"][n] for n in label_names(node) if n in reader["repo_by_label"])
    if named:
        return named.pop() if len(named) == 1 else None
    return reader["repo_by_team"].get(dig(node, "team", "key"))


def effort_of(node):
    """"S", "M" or "L" when exactly one effort label is on the ticket, else None."""
    found = [n for n in label_names(node) if n in dor.EFFORTS]
    return found[0][len("effort:"):] if len(found) == 1 else None


def blockers_of(node):
    """The tickets that block this one, or None when they cannot all be named: a page that ran over
    or says nothing of it, or a blocker with no identifier."""
    rel = node.get("inverseRelations") if isinstance(node, dict) else None
    nodes = dig(rel, "nodes")
    if not isinstance(nodes, list) or dig(rel, "pageInfo", "hasNextPage") is not False:
        return None
    out = []
    for r in nodes:
        if dig(r, "type") != "blocks":
            continue
        issue = dig(r, "issue")
        if not isinstance(dig(issue, "identifier"), str):
            return None
        out.append({"ticket": issue["identifier"], "state": dig(issue, "state", "name"),
                    "type": dig(issue, "state", "type"), "labels": sorted(label_names(issue))})
    return out


def pr_ref(node):
    """owner/name#N from the first link on the ticket that is a GitHub pull request's, else None."""
    for att in dig(node, "attachments", "nodes") or []:
        m = PR_URL_RE.match(att["url"]) if isinstance(dig(att, "url"), str) else None
        if m:
            return "%s/%s#%s" % m.groups()
    return None


def running_entry(cfg, node):
    """What holds a lane, or None. A ticket in a hand-off state holds one by its pull request, a
    started ticket handed to a dispatcher user by its session; any other holds none."""
    ident, repo = dig(node, "identifier"), repo_of(cfg["reader"], node)

    def entry(kind, ref, effort):
        return {"kind": kind, "repo": repo, "ref": ref, "effort": effort, "lane": None,
                "footprint": None}
    if dig(node, "state", "name") in cfg["order"]["handoff_states"]:
        return entry("pr", pr_ref(node) or ident, None)
    if (dig(node, "state", "type") == "started"
            and dig(node, "delegate", "id") in cfg["pool"]["dispatcher_user_ids"]):
        return entry("session", ident, effort_of(node))
    return None


def extras_of(cfg, nodes, plan_priority):
    """What the order adds, by identifier, for each candidate and each piece of a plan."""
    out = {}
    for n in nodes:
        ident, text = dig(n, "identifier"), dig(n, "description")
        if isinstance(ident, str):
            out[ident] = {
                "repo": repo_of(cfg["reader"], n), "priority": priority_of(dig(n, "priority")),
                "plan_priority": plan_priority.get(dig(n, "parent", "id"), 0),
                "effort": effort_of(n), "blockers": blockers_of(n),
                "title": dig(n, "title") if isinstance(dig(n, "title"), str) else None,
                "pointers": pointer_lines(text if isinstance(text, str) else "")}
    return out


# -- The tracker: every question through one budget
class TrackerDown(Exception):
    """The candidates or the running work could not be read at all: nothing can be decided."""


def out_of_requests(budget):
    return budget.asked >= budget.cap


def out_of_time(budget):
    return budget.clock() >= budget.deadline


class Budget(object):
    """Every question goes through here: counted against the cap and the clock, and not asked once
    the tracker is down for the rest of the pass (no network, a refused key, a rate limit)."""

    def __init__(self, gql, reader, clock, say):
        self.gql, self.clock, self.say = gql, clock, say
        self.cap, self.asked, self.halt = reader["max_requests"], 0, None
        self.deadline = clock() + reader["run_timeout_seconds"]

    def ask(self, query, variables):
        if self.halt is None:
            self.halt = "time" if out_of_time(self) else "requests" if out_of_requests(self) else None
            if self.halt:
                self.say("tracker: %s" % HALTED[self.halt])
        if self.halt:
            raise SourceError("stopped", whole=False)
        self.asked += 1
        try:
            return self.gql(query, variables)
        except SourceError as exc:
            self.say("tracker: %s" % exc)
            if exc.whole:
                self.halt = "down"
            raise


def paged(budget, query, variables, path, limit):
    """(nodes, status, first): every node of one list. `status` is "whole", "pages" when `limit` pages
    did not reach its end, or "error" (a question failed, or more pages and no cursor to them): the
    nodes before it are kept. `first` is the first answer, for what it carries besides the list."""
    nodes, after, first = [], None, None
    for _ in range(limit):
        try:
            data = budget.ask(query, dict(variables, after=after))
        except SourceError:
            return nodes, "error", first
        conn = dig(data, *path)
        if not (isinstance(conn, dict) and isinstance(conn.get("nodes"), list)
                and isinstance(conn.get("pageInfo"), dict)):
            budget.say("tracker: an answer held no whole page")
            return nodes, "error", first
        first = data if first is None else first
        nodes.extend(conn["nodes"])
        if not conn["pageInfo"].get("hasNextPage"):
            return nodes, "whole", first
        after = conn["pageInfo"].get("endCursor")
        if not after:
            return nodes, "error", first
    return nodes, "pages", first


def unique(nodes):
    """The nodes in the tracker's order, each issue once (a list can shift between two pages and
    show one twice). A node with no id is kept, for the pool to hold."""
    seen, out = set(), []
    for n in nodes:
        ident = dig(n, "id")
        if isinstance(ident, str):
            if ident in seen:
                continue
            seen.add(ident)
        out.append(n)
    return out


def read_tickets(budget, cfg, problems):
    """The candidates, the first `max_tickets` of them. TrackerDown when none could be read."""
    reader = cfg["reader"]
    nodes, status, _ = paged(budget, Q_CANDIDATES, {"filter": candidate_filter(cfg["pool"])},
                             ("issues",), reader["max_pages"])
    if status == "error" and not nodes:
        raise TrackerDown(HALTED["down"] if budget.halt == "down" else "the tickets could not be read")
    tickets = unique(nodes)
    extra = len(tickets) - reader["max_tickets"]
    if extra > 0:
        tickets = tickets[:reader["max_tickets"]]
        problems.append("tickets not read (max_tickets): %s%d" % ("" if status == "whole"
                                                                 else "at least ", extra))
    elif status != "whole":
        problems.append("the ticket list ran past max_pages" if status == "pages"
                        else "the ticket list was cut short")
    return tickets


def read_running(budget, cfg):
    """The tickets that may hold a lane. A list not read whole is unsafe to decide on: TrackerDown."""
    nodes, status, _ = paged(budget, Q_RUNNING, {"filter": running_filter(cfg["pool"], cfg["order"])},
                             ("issues",), cfg["reader"]["max_pages"])
    if status != "whole":
        raise TrackerDown("the running work could not be read whole")
    return unique(nodes)


def is_plan(node):
    """A plan the idea gate filed: the pool reads every epic it is given as one, so a parent that
    is no plan is never read as an epic (its sub-issues are ordinary tickets)."""
    return GATE_EPIC_LABEL in label_names(node)


def read_epics(budget, cfg, tickets, record, problems):
    """(epics, left): each plan with its pieces in full (the plan parent of each ticket, each ticket
    that is a plan with pieces, and each epic in the bridge's record), and the ids of plans not read
    whole: those are left out, and said."""
    ids = []
    for t in tickets:
        for node in (dig(t, "parent"), t if has_children(t) else None):
            if isinstance(dig(node, "id"), str) and is_plan(node) and node["id"] not in ids:
                ids.append(node["id"])
    ids += [i for i in sorted(record or ()) if i not in ids]
    epics, left = [], set()
    for ident in ids:
        pieces, status, first = paged(budget, Q_EPIC, {"id": ident}, ("issue", "children"),
                                      cfg["reader"]["max_pages"])
        epic = dig(first, "issue")
        if status == "whole" and isinstance(epic, dict):
            epics.append(dict(epic, children={"nodes": unique(pieces),
                                              "pageInfo": {"hasNextPage": False}}))
        else:
            left.add(ident)
    if left:
        problems.append("plans left out (pieces not read whole): %d" % len(left))
    return epics, left


def read_history(budget, ident, limit):
    """The issue's whole history, or None: past `limit` pages, a question that failed, or an entry
    short of a field the pool reads is not the whole."""
    nodes, status, _ = paged(budget, Q_HISTORY, {"id": ident}, ("issue", "history"), limit)
    whole = status == "whole" and all(isinstance(e, dict) and all(k in e for k in HISTORY_KEYS)
                                      for e in nodes)
    return nodes if whole else None


def read_busy(budget, ident, limit, dispatchers):
    """True when a comment carries a session of a dispatcher user, False when none does, None when
    the comments could not be read whole."""
    nodes, status, _ = paged(budget, Q_BUSY, {"id": ident}, ("issue", "comments"), limit)
    if status != "whole":
        return None
    return any(dig(n, "agentSession", "appUser", "id") in dispatchers for n in nodes)


def read_histories(budget, cfg, nodes, problems):
    """(histories, busy): an entry for each issue, its whole history, or None when that or its
    comments were not read whole (the pool then holds the ticket and says why)."""
    limit, dispatchers = cfg["reader"]["max_pages"], cfg["pool"]["dispatcher_user_ids"]
    histories, busy = {}, []
    for ident in [dig(n, "id") for n in unique(nodes) if isinstance(dig(n, "id"), str)]:
        history = read_history(budget, ident, limit)
        sessions = read_busy(budget, ident, limit, dispatchers) if history is not None else None
        histories[ident] = history if sessions is not None else None
        if sessions:
            busy.append(ident)
    unread = sum(1 for h in histories.values() if h is None)
    if unread:
        problems.append("histories not read: %d" % unread)
    return histories, sorted(busy)


# -- The three records
def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def whole_count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def is_day(v):
    if not (isinstance(v, str) and DAY_RE.match(v)):
        return False
    try:
        time.strptime(v, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def read_findings(path, say):
    """(record, state, reason): the finding poller's seen map; not there yet is empty."""
    if not path:
        return None, OFF, None
    new = not os.path.exists(path)
    try:
        return load_seen(path), READ, NO_RECORD if new else None
    except (PollerError, OSError) as exc:
        say("findings: %s" % exc)
        return None, COULD_NOT, UNREADABLE


def read_plans(path, say):
    """(record, state, reason): the `epics` of the bridge's state (plans approved in Slack); not there
    yet is empty."""
    if not path:
        return None, OFF, None
    try:
        doc = read_json(path)
    except FileNotFoundError:
        return {}, READ, NO_RECORD
    except (OSError, ValueError) as exc:
        say("plans: %s could not be read (%s)" % (path, type(exc).__name__))
        return None, COULD_NOT, UNREADABLE
    epics = doc.get("epics") if isinstance(doc, dict) else None
    if not isinstance(epics, dict):
        say("plans: %s holds no epics record" % path)
        return None, COULD_NOT, UNREADABLE
    return epics, READ, None


def read_ledger(path, today, say):
    """(ledger, state, reason): today's counts of starts. The queue's starter writes a row before
    every start, so no file means no start: zero, not unknown. A day gone by starts again at
    zero; a day that has not come, or a file that is not a ledger, is not read."""
    if not path:
        return None, OFF, None
    zero = {"date": today, "starts": 0, "agent_filed_starts": 0}
    try:
        doc = read_json(path)
    except FileNotFoundError:
        return zero, READ, "no start recorded yet"
    except (OSError, ValueError) as exc:
        say("ledger: %s could not be read (%s)" % (path, type(exc).__name__))
        return None, COULD_NOT, UNREADABLE
    if not (isinstance(doc, dict) and is_day(doc.get("date")) and whole_count(doc.get("starts"))
            and whole_count(doc.get("agent_filed_starts"))):
        say("ledger: %s is not a ledger of starts" % path)
        return None, COULD_NOT, UNREADABLE
    if doc["date"] > today:
        say("ledger: %s is dated after today" % path)
        return None, COULD_NOT, "the record is dated ahead"
    if doc["date"] < today:
        return zero, READ, "a new day"
    return dict((k, doc[k]) for k in ("date", "starts", "agent_filed_starts")), READ, None


# -- The document
def result_of(sources):
    if sources["tracker"]["state"] == COULD_NOT:
        return FAILED
    return PARTIAL if any(s["state"] in (PARTLY, COULD_NOT) for s in sources.values()) else OK


def document(now, sources, tickets=(), epics=(), histories=None, busy=(), records=(None,) * 3,
             extras=None, running=()):
    return {"schema": SCHEMA, "read_at": iso(now), "date": local_date(now),
            "result": result_of(sources), "sources": sources, "tickets": list(tickets),
            "epics": list(epics), "histories": histories or {}, "busy": list(busy),
            "findings": records[0], "plans": records[1], "ledger": records[2],
            "extras": extras or {}, "running": list(running)}


def failed(cfg, now, why):
    """A pass that cannot be trusted for anything: every list empty, no record read."""
    sources = {"tracker": {"state": COULD_NOT, "reason": why}}
    for name, key in (("findings", "findings_file"), ("plans", "bridge_state_file"),
                      ("ledger", "ledger_file")):
        sources[name] = ({"state": COULD_NOT, "reason": NOT_READ} if cfg["reader"][key]
                         else {"state": OFF, "reason": None})
    return document(now, sources)


def collect(cfg, gql, now, clock, say):
    """One pass over a config that has been checked: the document, whatever the tracker said."""
    reader, today = cfg["reader"], local_date(now)
    budget, problems = Budget(gql, reader, clock, say), []
    try:
        tickets = read_tickets(budget, cfg, problems)
        running = read_running(budget, cfg)
    except TrackerDown as exc:
        return failed(cfg, now, str(exc))
    records = (read_findings(reader["findings_file"], say),
               read_plans(reader["bridge_state_file"], say),
               read_ledger(reader["ledger_file"], today, say))
    epics, left = read_epics(budget, cfg, tickets, records[1][0], problems)
    pieces = [k for e in epics for k in dig(e, "children", "nodes")]
    histories, busy = read_histories(budget, cfg, epics + tickets + pieces, problems)
    # A piece whose plan was left out would be judged as an ordinary ticket, past the plan's own
    # rules (its approval, a pause). Its history is set to null, so the pool holds it and says so.
    held = [t["id"] for t in tickets if dig(t, "parent", "id") in left and isinstance(t.get("id"), str)]
    for ident in held:
        histories[ident] = None
    if held:
        problems.append("tickets held (their plan was not read whole): %d" % len(held))
    if budget.halt:
        problems.insert(0, HALTED[budget.halt])
    sources = {"tracker": {"state": PARTLY if problems else READ,
                           "reason": "; ".join(problems) or None}}
    for name, (_value, state, reason) in zip(SOURCES[1:], records):
        sources[name] = {"state": state, "reason": reason}
    prio = dict((e["id"], priority_of(e.get("priority"))) for e in epics
                if isinstance(e.get("id"), str))
    entries = [e for e in (running_entry(cfg, n) for n in running) if e]
    return document(now, sources, tickets, epics, histories, busy, [r[0] for r in records],
                    extras_of(cfg, tickets + pieces, prio),
                    sorted(entries, key=lambda e: (e["kind"], str(e["ref"]))))


def gather(raw_config, gql, now, clock=time.time, say=None):
    """The inputs document: `read_at` and `date` from `now` (seconds since 1970), the tracker
    through `gql(query, variables) -> data` (raising SourceError), the timeout by `clock`. `say`,
    if given, is called with each problem, the tracker's own error words among them: scrub them."""
    cfg, bad = read_config(raw_config)
    if bad:
        raise ValueError("the queue's config is not usable: " + "; ".join(bad))
    return collect(cfg, gql, now, clock, say or (lambda line: None))


def write(doc, path):
    """The document, whole or not at all: a temp file beside it, flushed to disk, renamed over it.
    Mode 0600, its folder 0700 when made. A folder inside a git working tree is refused."""
    folder = os.path.dirname(path)
    if inside_git_worktree(folder):
        raise ValueError("%s is inside a git working tree, where a session can write" % folder)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w",
                       encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# -- The job
def scrub(text, secret):
    return text.replace(secret, "[key]") if secret else text


def summary_lines(doc, dry_run):
    """Counts and fixed words only: never a title, a text or the key."""
    out = ["queue reader: %s%s" % (doc["read_at"], " (dry run)" if dry_run else "")]
    for name in SOURCES:
        src = doc["sources"][name]
        out.append("  %-9s %-15s%s" % (name, src["state"], ": " + src["reason"] if src["reason"] else ""))
    out.append("  tickets %d, epics %d, histories unread %d, busy %d, running %d" % (
        len(doc["tickets"]), len(doc["epics"]), sum(1 for h in doc["histories"].values() if h is None),
        len(doc["busy"]), len(doc["running"])))
    out.append("  result: %s" % doc["result"])
    return out


def run(cfg, out, dry_run, environ, now=None):
    """One pass: read, print the counts, write the file unless it is a dry run. The exit code."""
    now = time.time() if now is None else now
    key, why = credential(environ, cfg["reader"]["linear_key_env"])
    say = lambda line: print(scrub(line, key), file=sys.stderr)    # noqa: E731
    if key is None:
        say("tracker: %s" % why)
        doc = failed(cfg, now, "the tracker key is not usable")
    else:
        try:
            doc = collect(cfg, lambda q, v: linear_graphql(q, v, key), now, time.monotonic, say)
        except Exception as exc:  # noqa: BLE001 — a bug must still leave a file that says so
            say("the pass stopped on an unexpected error (%s)" % type(exc).__name__)
            doc = failed(cfg, now, "the pass stopped on an unexpected error")
    for line in summary_lines(doc, dry_run):
        print(scrub(line, key))
    if dry_run:
        print("  dry run: would write %s" % out)
    else:
        try:
            write(doc, out)
        except (OSError, ValueError) as exc:
            say("COULD NOT WRITE %s: %s" % (out, exc if isinstance(exc, ValueError)
                                            else type(exc).__name__))
            return EXIT_ERROR
        print("  written to %s" % out)
    return {OK: EXIT_OK, PARTIAL: EXIT_PARTIAL}.get(doc["result"], EXIT_ERROR)


def main(argv=None, environ=None, now=None):
    p = argparse.ArgumentParser(prog="pipeline_queue_read.py", description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--config", help="the queue's config file")
    p.add_argument("--out", help="the inputs file (default: reader.inputs_file)")
    p.add_argument("--dry-run", action="store_true", help="write nothing")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.config:
        p.print_help()
        return EXIT_USAGE
    try:
        cfg, bad = read_config(read_json(args.config))
    except (OSError, ValueError) as exc:
        cfg, bad = None, ["could not read --config (%s)" % type(exc).__name__]
    out = None
    if args.out:
        try:
            out = resolve_path(args.out, "--out")
        except ValueError as exc:
            bad.append(str(exc))
    if bad:
        for line in bad:
            print("CONFIG: %s" % line, file=sys.stderr)
        return EXIT_USAGE
    return run(cfg, out or cfg["reader"]["inputs_file"], args.dry_run,
               os.environ if environ is None else environ, now)


# -- --selftest: no tracker, no network, files only in a temp folder
def selftest():
    import socket
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        good = got == want
        print("[%s] %s%s" % ("PASS" if good else "FAIL", name,
                             "" if good else "  (got %r, want %r)" % (got, want)))
        if not good:
            fails.append(name)

    class Blocked(object):          # stands in for socket.socket: an attempt is counted and refused
        attempts = []

        def __init__(self, *args, **kwargs):
            Blocked.attempts.append(args)
            raise OSError("the selftest opens no socket")
    real_socket, real_home, saved_zone = socket.socket, account_home, os.environ.get("TZ")
    socket.socket = Blocked
    os.environ["TZ"] = "TST-3"          # the local day is UTC+3, with no zone database behind it
    time.tzset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(os.path.realpath(tmp), "home")
            os.mkdir(home)
            globals()["account_home"] = lambda: home      # `~/` is a folder of the test's own
            _cases(check, tmp, Blocked)
    finally:
        globals()["account_home"] = real_home
        socket.socket = real_socket
        if saved_zone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved_zone
        time.tzset()
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, tmp, blocked):
    import contextlib
    import io
    import socket
    import stat
    import tempfile
    import types
    import pipeline_queue_order as order
    import pipeline_queue_pool as pool
    from pipeline_finding_poller import SEEN_SCHEMA

    OWNER, DISP, OTHER, KEY = "owner-1", "disp-1", "other-1", "SENTINEL-KEY-4f9a1c"
    T0, T1 = "2026-11-02T09:00:00Z", "2026-11-02T10:00:00Z"
    NOW, TODAY = pool.epoch("2026-11-05T22:30:00Z"), "2026-11-06"      # the local day (UTC+3) is the 6th
    RT, MISSING, HOME, dump = os.path.realpath(tmp), object(), account_home(), json.dumps
    FILES, GIT = os.path.join(RT, "files"), os.path.join(RT, "tree")
    os.makedirs(os.path.join(GIT, ".git"))
    os.mkdir(FILES)
    NEED = pool.TICKET_KEYS + ("id", "identifier", "priority", "inverseRelations")

    @contextlib.contextmanager
    def patch(**kw):
        saved = dict((k, globals()[k]) for k in kw)
        globals().update(kw)
        try:
            yield
        finally:
            globals().update(saved)

    def put(folder, name, text):
        path = os.path.join(folder, name)
        assert os.path.dirname(path) == folder and folder.startswith(RT + os.sep)   # written here only
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    # -- the tracker as the reader asks it: only the fields a query names, pages of `size`
    def selection(query):
        root, stack, last = {}, [], None
        for t in re.findall(r"\w+|[{}]|\([^)]*\)", query)[2:]:
            if t == "{":
                child = {} if stack else root
                if stack:
                    stack[-1][last] = child
                stack.append(child)
            elif t == "}":
                stack.pop()
            elif t[0] != "(":
                stack[-1][t], last = None, t
        return root

    def project(data, sel):
        if sel is None or data is None:
            return data
        if isinstance(data, list):
            return [project(x, sel) for x in data]
        return dict((k, project(data[k], s)) for k, s in sel.items() if data[k] is not MISSING)

    def matches(f, i):
        st = f.get("state", {})
        assert set(f) <= {"team", "state", "or"} and set(st) <= {"name", "type"}, f
        return (("team" not in f or i["team"]["key"] in f["team"]["key"]["in"])
                and ("name" not in st or i["state"]["name"] in st["name"]["in"])
                and ("type" not in st or i["state"]["type"] == st["type"]["eq"])
                and ("or" not in f or any(matches(g, i) for g in f["or"])))

    class Linear(object):
        def __init__(self, issues, hist=None, comments=None, size=2, fail=None, clock=None, cursor=True):
            self.issues, self.hist, self.comments, self.size = issues, hist or {}, comments or {}, size
            self.fail, self.clock, self.cursor, self.calls = fail, clock, cursor, []

        def kids(self, i):
            return [k for k in self.issues if dig(k, "parent", "id") == i["id"]]

        def shape(self, i):             # a parent as the tracker answers one: its own labels
            up = next((p for p in self.issues if p["id"] == dig(i, "parent", "id")), None)
            parent = dict(i["parent"], labels=up["labels"] if up else names(())) if i.get("parent") else None
            return dict(i, parent=parent, children={"nodes": [{"id": k["id"]} for k in self.kids(i)][:1]})

        def page(self, rows, after):
            at = int(after or 0)
            return {"nodes": rows[at:at + self.size], "pageInfo": {
                "hasNextPage": at + self.size < len(rows),
                "endCursor": str(at + self.size) if self.cursor else None}}

        def __call__(self, query, variables):
            assert query.startswith("query "), "a document that is not a query"
            op, sel = query.split()[1].split("(")[0], selection(query)
            self.calls.append(op)
            if self.clock:
                self.clock[0] += 10
            ident, after = variables.get("id"), variables.get("after")
            err = self.fail and self.fail(op, ident, len(self.calls))
            if err:
                raise err
            if op in ("QueueCandidates", "QueueRunning"):
                data = {"issues": self.page([self.shape(i) for i in self.issues
                                             if matches(variables["filter"], i)], after)}
            else:
                one = next((i for i in self.issues if i["id"] == ident), None)
                data = {"issue": {
                    "QueueHistory": lambda: {"history": self.page(self.hist.get(ident, []), after)},
                    "QueueBusy": lambda: {"comments": self.page(self.comments.get(ident, []), after)},
                    "QueueEpic": lambda: one and dict(self.shape(one), children=self.page(
                        [self.shape(k) for k in self.kids(one)], after))}[op]()}
            return project(data, sel)

    # -- a world: tickets as the API holds them, a plan of three pieces, work that is running
    KINDS = {"Todo": "unstarted", "Backlog": "backlog", "Doing": "started", "Needs Approval": "started",
             "Done": "completed"}
    names = lambda labels: {"nodes": [{"name": x} for x in labels]}
    rels = lambda *nodes, more=False: {"nodes": list(nodes), "pageInfo": {"hasNextPage": more}}

    def rel(n, typ="blocks", state="Done", kind="completed", labels=()):
        return {"type": typ, "issue": {"id": "id-%d" % n, "identifier": "ENG-%d" % n,
                                       "state": {"name": state, "type": kind}, "labels": names(labels)}}

    def issue(n, st="Todo", labels=(), **kw):
        t = {"id": "id-%d" % n, "identifier": "ENG-%d" % n, "title": "Refresh tokens",
             "description": dor.GOOD_DESCRIPTION, "createdAt": T0, "priority": 0,
             "state": {"id": "st-" + st.split()[0].lower(), "name": st, "type": KINDS[st]}, "team": {"key": "ENG"},
             "labels": names(("track:platform",) + tuple(labels)), "delegate": None, "parent": None,
             "project": {"id": "p1"}, "creator": {"id": OWNER}, "attachments": {"nodes": []},
             "inverseRelations": rels()}
        t.update(kw)
        return t

    def piece(n, st="Backlog", **kw):
        return issue(n, st, ("effort:S", "provenance:epic"), parent={"id": "id-10", "identifier": "ENG-10"}, **kw)
    MOVE = dict(dict.fromkeys(HISTORY_KEYS), createdAt=T1, actorId=OWNER, fromStateId="st-backlog",
                toStateId="st-todo")
    PIECES = [piece(11), piece(12, inverseRelations=rels(rel(11, "blocks", "Backlog", "backlog"))), piece(13)]
    ISSUES = [
        issue(1, labels=("effort:S", "provenance:human", "repo:web"), priority=2,
              inverseRelations=rels(rel(9, labels=("track:x",)), rel(8, "related", "Todo", "unstarted"))),
        issue(2, labels=("effort:M", "provenance:human")),
        issue(3, labels=("effort:S", "effort:L", "repo:web", "repo:api")),
        issue(4, "Backlog", ("effort:L", "provenance:human"), team={"key": "OPS"}),
        issue(5, labels=("repo:web", "web"), description="Just words"),
        issue(6, labels=("effort:S", "provenance:human"), inverseRelations=rels(rel(9), more=True)),
        issue(10, labels=("provenance:agent",), priority=1)] + PIECES + [
        issue(20, "Doing", ("effort:M",), delegate={"id": DISP}), issue(21, "Doing"),
        issue(23, "Needs Approval", delegate={"id": DISP}, attachments={"nodes": [
            {"url": "https://x.test/"}, {"url": "https://github.com/acme/app/pull/7"}]}),
        issue(24, "Needs Approval", state={"id": "st-a2", "name": "Needs Approval", "type": "unstarted"}),
        issue(25, "Doing", ("effort:S", "repo:web"), delegate={"id": DISP}),
        issue(40, "Doing", ("provenance:agent",)),
        issue(41, "Done", ("provenance:epic",), parent={"id": "id-40", "identifier": "ENG-40"})]
    HIST = dict((i["id"], [MOVE]) for i in ISSUES)
    SESSION = lambda who: {"agentSession": {"id": "s-" + who, "appUser": {"id": who}}}
    COMM = {"id-2": [SESSION(DISP)], "id-3": [SESSION(OTHER), {"agentSession": None}]}
    SEEN, LEDGER = {"c1": {"filed": "ENG-4", "source": "ENG-90"}}, {"date": TODAY, "starts": 3,
                                                                    "agent_filed_starts": 1}
    RECORD = {"id-10": {"ticket": "ENG-10", "approved_by": "U0OWNER001", "approved_at": T1,
                        "approved": dict((p["id"], pool.fingerprint(p)) for p in PIECES)},
              "id-40": {"ticket": "ENG-40"}}
    JUNK = [dict(LEDGER, starts=-1), dict(LEDGER, starts=True), {"date": TODAY},
            dict(LEDGER, date="2026-13-45"), "{not json"]
    F = dict((k, put(FILES, k + ".json", v if isinstance(v, str) else dump(v))) for k, v in dict(
        {"seen": {"schema": SEEN_SCHEMA, "seen": SEEN}, "bridge": {"epics": RECORD}, "bad": "{not json",
         "list": [], "ledger": dict(LEDGER, rows=[]), "old": dict(LEDGER, date="1999-01-01"),
         "ahead": dict(LEDGER, date="2999-01-01"), "epicslist": {"epics": []}},
        **dict(("junk%d" % i, v) for i, v in enumerate(JUNK))).items())
    F["none"] = os.path.join(FILES, "none.json")
    FILESET = {"findings_file": F["seen"], "bridge_state_file": F["bridge"], "ledger_file": F["ledger"]}
    PCFG = {"owner_user_ids": [OWNER], "dispatcher_user_ids": [DISP], "team_keys": ["ENG", "OPS"],
            "backlog": True, "agent_filed": "limits", "owner_slack_user_ids": ["U0OWNER001"],
            "reviews_team_key": "REV"}
    READER = {"repo_by_team": {"ENG": "acme/app"}, "findings_file": "", "bridge_state_file": "",
              "repo_by_label": {"repo:web": "acme/web", "web": "acme/web", "repo:api": "acme/api"},
              "ledger_file": "", "inputs_file": os.path.join(RT, "out", "in.json")}
    boom, down = SourceError("refused the query", whole=False), SourceError("could not reach it")
    failing = lambda err, op=None, ident=None, nth=None: (
        lambda op_, i, n: err if op in (None, op_) and ident in (None, i) and nth in (None, n) else None)

    def read(issues, over=None, hist=None, **kw):
        fake, clock = Linear(issues, HIST if hist is None else hist, COMM, **kw), kw.get("clock")
        doc = gather({"pool": PCFG, "reader": dict(READER, **(over or {}))}, fake, NOW,
                     (lambda: clock[0]) if clock else (lambda: 0.0))
        return types.SimpleNamespace(doc=doc, calls=fake.calls)

    def cli(args=(), conf=None, env=None, transport=None, over=None):
        """main() with the tracker's transport and the environment stood in for."""
        home = tempfile.mkdtemp(dir=RT)
        out, fake, keys = os.path.join(home, "q", "in.json"), Linear(ISSUES[:2], HIST, COMM), []
        reader = dict(READER, inputs_file=out, linear_key_env="QUEUE_KEY")
        reader.update(over or {})

        def seen(query, variables, key):
            keys.append(key)
            return fake(query, variables)
        cfg, bufs = dict({"pool": PCFG, "reader": reader}, **(conf or {})), (io.StringIO(), io.StringIO())
        with patch(linear_graphql=transport or seen), contextlib.redirect_stdout(bufs[0]), \
                contextlib.redirect_stderr(bufs[1]):
            code = main(["--config", put(home, "q.json", dump(cfg))] + list(args),
                        {"QUEUE_KEY": KEY} if env is None else env, NOW)
        said, err = [b.getvalue().replace(home, "<home>") for b in bufs]
        return types.SimpleNamespace(code=code, out=said, err=err, keys=keys, path=out,
                                     doc=json.load(open(out)) if os.path.exists(out) else None)

    def throws(make):
        def transport(query, variables, key):
            raise make("it said %s" % key)         # an error that quotes the key, as a careless one might
        return transport

    def atomic():
        folder = os.path.join(tempfile.mkdtemp(dir=RT), "in")
        path, real = os.path.join(folder, "in.json"), os.replace
        write({"v": 1}, path)

        def refuse(src, dst):
            assert dst == path and os.path.dirname(src) == folder          # the exact path it replaces
            raise OSError("the disk went away")
        os.replace = refuse
        try:
            write({"v": 2}, path)
        except OSError:
            pass
        finally:
            os.replace = real
        return types.SimpleNamespace(kept=json.load(open(path)), left=os.listdir(folder), doc=None)
    SCENES = {
        "W": lambda: read(ISSUES, FILESET, fail=failing(boom, "QueueBusy", "id-6")),
        "H": lambda: read(ISSUES[:3], {"max_pages": 2}, {"id-1": [MOVE] * 5, "id-2": [MOVE] * 4,
                                                          "id-3": [dict(MOVE, toParentId=MISSING)]}),
        "CAP": lambda: read(ISSUES[:2], {"max_requests": 4}),
        "TIME": lambda: read(ISSUES[:2], {"run_timeout_seconds": 30}, clock=[0.0]),
        "TK": lambda: read(ISSUES[:6], {"max_tickets": 2}),
        "NOCUR": lambda: read(ISSUES[:6], cursor=False),
        "EP": lambda: read([issue(10, labels=("provenance:agent",))] + [piece(n, "Done") for n in (11, 12, 13)],
                           {"max_pages": 1}),
        "NP": lambda: read([issue(30, labels=("effort:S", "provenance:human"),
                                  parent={"id": "id-31", "identifier": "ENG-31"}),
                            issue(31, "Backlog", ("provenance:human",))], hist={"id-30": [MOVE], "id-31": [MOVE]}),
        "EPH": lambda: read([issue(10, labels=("provenance:agent",)), piece(11), piece(12)]
                            + [piece(n, "Done") for n in (13, 14, 15)], {"max_pages": 1, "findings_file": F["seen"]},
                            size=3),
        "DOWN": lambda: read(ISSUES[:2], FILESET, fail=failing(down, nth=1)),
        "NORUN": lambda: read(ISSUES[:2], fail=failing(boom, "QueueRunning")),
        "RB": lambda: read(ISSUES[:2], {"findings_file": F["bad"]}),
        "ATOMIC": atomic, "OK": lambda: cli(), "DRY": lambda: cli(["--dry-run"]),
        "PART": lambda: cli(over={"max_requests": 4}), "NET": lambda: cli(transport=throws(SourceError)),
        "BUG": lambda: cli(transport=throws(RuntimeError)), "NOKEY": lambda: cli(env={}),
        "BADCFG": lambda: cli(["--out", os.path.join(GIT, "in.json")], {"oops": 1},
                              over={"max_pages": 0, "linear_key_env": KEY})}
    CLI = ("OK", "DRY", "PART", "NET", "BUG", "NOKEY", "BADCFG")
    CASES, MUTANTS = [], []

    def case(name, want, fn):
        CASES.append((name, fn, want))

    def mutant(name, **patched):
        MUTANTS.append((name, patched))
    W = lambda d: d["W"].doc
    X = lambda d, n, k: d["W"].doc["extras"]["ENG-%d" % n][k]
    tr = lambda doc: (doc["result"], doc["sources"]["tracker"])
    quiet = lambda line: None
    why = lambda reason: {"state": PARTLY, "reason": reason}
    QS = dict((k, v) for k, v in globals().items() if k.startswith("Q_"))

    def judged(doc):
        return dict((v["ticket"], v) for v in pool.decide(
            PCFG, NOW, doc["tickets"], doc["epics"], doc["histories"], doc["busy"], doc["findings"],
            doc["plans"], doc["ledger"]))
    # -- what it asks, and what it hands on
    case("only queries are asked, none a mutation",
         ([], 5, ["QueueBusy", "QueueCandidates", "QueueEpic", "QueueHistory", "QueueRunning"]),
         lambda d: ([k for k, v in QS.items() if not v.startswith("query ") or "mutation" in v.lower()],
                    len(QS), sorted(set(d["W"].calls))))
    case("document: keys, schema, time, local day; ok when all is read",
         (sorted("schema read_at date result sources tickets epics histories busy findings plans "
                 "ledger extras running".split()), SCHEMA, "2026-11-05T22:30:00Z", TODAY, "ok"),
         lambda d: (sorted(W(d)), W(d)["schema"], W(d)["read_at"], W(d)["date"], d["OK"].doc["result"]))
    case("paging; plans from tickets and the record; a history for each",
         ([5, 3, 3], ["ENG-%d" % n for n in (1, 2, 3, 4, 5, 6, 10, 11, 12, 13)],
          [["ENG-11", "ENG-12", "ENG-13"], ["ENG-41"]], [False] * 2, 12, [MOVE]),
         lambda d: ([d["W"].calls.count(q) for q in ("QueueCandidates", "QueueRunning", "QueueEpic")],
                    [t["identifier"] for t in W(d)["tickets"]],
                    [[k["identifier"] for k in e["children"]["nodes"]] for e in W(d)["epics"]],
                    [e["children"]["pageInfo"]["hasNextPage"] for e in W(d)["epics"]], len(W(d)["histories"]),
                    W(d)["histories"]["id-1"]))
    case("no ticket, piece, epic or history entry lacks a field the pool reads",
         ([], [], []), lambda d: (
             [t["identifier"] for t in W(d)["tickets"] + [k for e in W(d)["epics"] for k in e["children"]["nodes"]]
              if any(k not in t for k in NEED)],
             [e["identifier"] for e in W(d)["epics"] if any(k not in e for k in pool.EPIC_KEYS)],
             [i for i, h in W(d)["histories"].items() for e in h or [] if any(k not in e for k in HISTORY_KEYS)]))
    case("busy; unread comments, a cut history, a short entry",
         (["id-2"], None, ("partial", why("histories not read: 1")), why("histories not read: 2"),
          [None, 4, None]),
         lambda d: (W(d)["busy"], W(d)["histories"]["id-6"], tr(W(d)), d["H"].doc["sources"]["tracker"],
                    [d["H"].doc["histories"]["id-1"], len(d["H"].doc["histories"]["id-2"]),
                     d["H"].doc["histories"]["id-3"]]))
    # -- what the order adds
    case("repo, effort, priorities, blockers, title, Pointers",
         ([("acme/web", "S", 2, 0), ("acme/app", "M", 0, 0), (None, None, 0, 0), (None, "L", 0, 0),
           ("acme/web", None, 0, 0), ("acme/app", "S", 0, 1)],
          [{"ticket": "ENG-9", "state": "Done", "type": "completed", "labels": ["track:x"]}], [], None,
          "Refresh tokens", pool.pointer_lines(dor.GOOD_DESCRIPTION), None),
         lambda d: ([tuple(X(d, n, k) for k in ("repo", "effort", "priority", "plan_priority"))
                     for n in (1, 2, 3, 4, 5, 11)], X(d, 1, "blockers"), X(d, 2, "blockers"),
                    X(d, 6, "blockers"), X(d, 1, "title"), X(d, 1, "pointers"), X(d, 5, "pointers")))
    case("running: pr by link or ticket, session; not an undelegated one",
         ([("pr", "ENG-24", None, "acme/app"), ("pr", "acme/app#7", None, "acme/app"),
           ("session", "ENG-20", "M", "acme/app"), ("session", "ENG-25", "S", "acme/web")], [(None, None)] * 4),
         lambda d: ([(r["kind"], r["ref"], r["effort"], r["repo"]) for r in W(d)["running"]],
                    [(r["lane"], r["footprint"]) for r in W(d)["running"]]))
    case("the pool takes it: none malformed, a move, a plan piece, a held history",
         ([], ["admitted", "running", "plan-ticket", "admitted", "history-unreadable"]),
         lambda d: ([k for k, v in judged(W(d)).items() if v["reason"] == "malformed"],
                    [judged(W(d))[k]["reason"] for k in ("ENG-1", "ENG-2", "ENG-10", "ENG-11", "ENG-6")]))
    case("the order takes the extras", ("acme/web", 2, "S", ["ENG-9"]),
         lambda d: (lambda p: (p["repo"], p["priority"], p["effort"], [b["ticket"] for b in p["blockers"]]))(
             order.clean(dict(judged(W(d))["ENG-1"], **W(d)["extras"]["ENG-1"]))))
    # -- the records
    UN, ZERO = (None, COULD_NOT, UNREADABLE), {"date": TODAY, "starts": 0, "agent_filed_starts": 0}
    case("finding and plan records: read, missing, refused, off",
         ([(SEEN, READ, None), ({}, READ, NO_RECORD), UN, (None, OFF, None)],
          [(RECORD, READ, None), ({}, READ, NO_RECORD), UN, UN, UN, (None, OFF, None)]),
         lambda d: ([read_findings(F[k], quiet) for k in ("seen", "none", "bad")] + [read_findings("", quiet)],
                    [read_plans(F[k], quiet) for k in ("bridge", "none", "bad", "list", "epicslist")]
                    + [read_plans("", quiet)]))
    case("ledger: today, a new day, missing, ahead or malformed, off",
         ([(LEDGER, READ, None), (ZERO, READ, "a new day"), (ZERO, READ, "no start recorded yet"),
           (None, COULD_NOT, "the record is dated ahead"), (None, OFF, None)], [UN] * 5),
         lambda d: ([read_ledger(F[k], TODAY, quiet) for k in ("ledger", "old", "none", "ahead")]
                    + [read_ledger("", TODAY, quiet)],
                    [read_ledger(F["junk%d" % i], TODAY, quiet) for i in range(5)]))
    case("records ride along; an unreadable one makes it partial",
         ((SEEN, RECORD, LEDGER), ("partial", None, UNREADABLE)),
         lambda d: ((W(d)["findings"], W(d)["plans"], W(d)["ledger"]),
                    (d["RB"].doc["result"], d["RB"].doc["findings"], d["RB"].doc["sources"]["findings"]["reason"])))
    # -- the caps, and a tracker that fails
    case("request cap and clock: partial, said, no more asked",
         [("partial", why("the pass reached its cap of requests (max_requests); histories not read: 1"), 4),
          ("partial", why("the pass ran out of time (run_timeout_seconds); histories not read: 2"), 3)],
         lambda d: [tr(d[k].doc) + (len(d[k].calls),) for k in ("CAP", "TIME")])
    case("ticket cap, a list with no cursor, a plan past max_pages: said",
         [(["ENG-1", "ENG-2"], "partial", why("tickets not read (max_tickets): 4")),
          (["ENG-1", "ENG-2"], "partial", why("the ticket list was cut short")),
          (["ENG-10"], "partial", why("plans left out (pieces not read whole): 1"))], lambda d: [
              ([t["identifier"] for t in d[k].doc["tickets"]],) + tr(d[k].doc) for k in ("TK", "NOCUR", "EP")])
    case("a sub-issue of a parent that is no plan stays an ordinary ticket",
         ([], "admitted", "plan-ticket"), lambda d: (d["NP"].doc["epics"], judged(d["NP"].doc)["ENG-30"]["reason"],
                                                       judged(d["NP"].doc)["ENG-31"]["reason"]))
    case("a plan not read whole holds its pieces that are candidates",
         (why("plans left out (pieces not read whole): 1; tickets held (their plan was not read whole): 2"),
          [None, None], "history-unreadable"),
         lambda d: (d["EPH"].doc["sources"]["tracker"], [d["EPH"].doc["histories"][i] for i in ("id-11", "id-12")],
                    judged(d["EPH"].doc)["ENG-11"]["reason"]))
    case("every query stays under the complexity ceiling, by Linear's rule estimated",
         ([], True), lambda d: ([k for k in sorted(QS) if complexity(globals()[k]) > COMPLEXITY_CAP],
                                complexity(Q_CANDIDATES) > 1000))
    case("failed: no network, running work unread",
         (("failed", {"state": COULD_NOT, "reason": "the tracker did not answer"}),
          ("failed", {"state": COULD_NOT, "reason": "the running work could not be read whole"}),
          [[], [], [], [], {}, {}], [None] * 3, [NOT_READ] * 3),
         lambda d: (tr(d["DOWN"].doc), tr(d["NORUN"].doc), [d["DOWN"].doc[k] for k in (
             "tickets", "epics", "busy", "running", "histories", "extras")],
                    [d["DOWN"].doc[k] for k in ("findings", "plans", "ledger")],
                    [d["DOWN"].doc["sources"][k]["reason"] for k in ("findings", "plans", "ledger")]))
    # -- the job: exit codes, files, and what is printed
    case("a good run: exit 0, counts only, a private file",
         (0, ["queue reader: 2026-11-05T22:30:00Z", "  tickets 2, epics 0, histories unread 0, busy 1, running 0",
              "  result: ok", "  written to <home>/q/in.json"], "", [0o600, 0o700]),
         lambda d: (d["OK"].code, [l for i, l in enumerate(d["OK"].out.splitlines()) if i in (0, 5, 6, 7)],
                    d["OK"].err, [stat.S_IMODE(os.stat(p).st_mode) for p in (
                        d["OK"].path, os.path.dirname(d["OK"].path))]))
    case("exits: dry run, partial 3, no network, a bug, no key (no request)",
         ((0, None, True), (3, "partial"), (1, "failed"), (1, "failed"), (1, "failed", []), True, True),
         lambda d: ((d["DRY"].code, d["DRY"].doc, "dry run: would write" in d["DRY"].out),
                    (d["PART"].code, d["PART"].doc["result"]), (d["NET"].code, d["NET"].doc["result"]),
                    (d["BUG"].code, d["BUG"].doc["result"]),
                    (d["NOKEY"].code, d["NOKEY"].doc["result"], d["NOKEY"].keys),
                    "unexpected error (RuntimeError)" in d["BUG"].err, "it said [key]" in d["NET"].err))
    case("exit 2 on a bad config or an --out in a git tree, nothing read", (2, None, [], True),
         lambda d: (d["BADCFG"].code, d["BADCFG"].doc, d["BADCFG"].keys, "--out" in d["BADCFG"].err))
    case("the key reaches no output and no file, even quoted by an error", False,
         lambda d: any(KEY in d[k].out + d[k].err + dump(d[k].doc) for k in CLI))
    case("a write that fails leaves the old file whole and no temp file", ({"v": 1}, ["in.json"]),
         lambda d: (d["ATOMIC"].kept, d["ATOMIC"].left))
    # -- the config
    BAD = {"oops": 1, "pool": {}, "order": [], "planner": "left alone", "reader": {
        "max_pages": 0, "nope": 1, "repo_by_team": {"eng": "a/b"}, "linear_key_env": KEY, "inputs_file": "in.json"}}
    case("all ten faults named at once, none by value; the morning job's sections unread",
         (10, False), lambda d: (lambda bad: (len(bad), any(KEY in p for p in bad)))(read_config(BAD)[1]))
    rc = lambda **kw: reader_config(dict(READER, **kw))
    case("paths and values: ~/, off, and seven refusals",
         (HOME + "/q/l.json", "", [True] * 7),
         lambda d: (rc(ledger_file="~/q/l.json")[0]["ledger_file"], rc(ledger_file="")[0]["ledger_file"],
                    [bool(rc(**kw)[1]) for kw in (
                        {"ledger_file": "l.json"}, {"inputs_file": ""}, {"findings_file": HOME},
                        {"bridge_state_file": GIT + "/b.json"}, {"repo_by_label": {"x": "acme"}},
                        {"max_requests": True}, {"linear_key_env": "lower-case"})]))
    # -- run them all, then break each rule alone
    def seen(fn, d):
        try:
            return fn(d)
        except Exception as exc:  # noqa: BLE001 — a view that cannot be taken is a view that differs
            return "%s: %s" % (type(exc).__name__, exc)

    def battery():
        d = dict((k, v()) for k, v in SCENES.items())
        return [seen(fn, d) for _n, fn, _w in CASES] + [dump(r.doc, sort_keys=True) for r in d.values()
                                                        if r.doc is not None]
    got = battery()
    for (name, _fn, want), g in zip(CASES, got):
        check(name, g, want)
    before = list(blocked.attempts)
    try:
        socket.socket()
    except OSError:
        pass
    check("no socket was opened, and the guard is live", (before, len(blocked.attempts)), ([], 1))
    o = dict((k, globals()[k]) for k in (
        "repo_of blockers_of running_entry read_history read_busy paged read_ledger read_findings read_plans "
        "result_of read_tickets read_epics failed write read_config reader_config resolve_path").split())
    cap = lambda cfg, **kw: dict(cfg, reader=dict(cfg["reader"], **kw))
    rel_ = lambda n, **kw: dict(n, inverseRelations=dict(n["inverseRelations"], **kw))
    blank = lambda fn, empty: lambda path, *rest: (lambda r: (empty, READ, None) if r[1] == COULD_NOT else r)(
        fn(path, *rest))

    def keep_old(path, today, say):
        v = o["read_ledger"](path, today, say)
        return (read_json(path), READ, None) if v[2] in ("a new day", "the record is dated ahead") else v

    def no_cursor(budget, query, variables, path, limit):
        nodes, status, first = o["paged"](budget, query, variables, path, limit)
        return nodes, "whole" if status == "error" and nodes else status, first

    mutant("an error ends a list", paged=no_cursor)
    mutant("team repo beats label", repo_of=lambda r, n: r["repo_by_team"].get(
        dig(n, "team", "key")) or o["repo_of"](r, n))
    mutant("first of two repos", repo_of=lambda r, n: (sorted(set(
        r["repo_by_label"][x] for x in label_names(n) if x in r["repo_by_label"])) or [None])[0])
    mutant("any relation a blocker", blockers_of=lambda n: o["blockers_of"](rel_(
        n, nodes=[dict(r, type="blocks") for r in n["inverseRelations"]["nodes"]])))
    mutant("blockers overflow ignored", blockers_of=lambda n: o["blockers_of"](
        rel_(n, pageInfo={"hasNextPage": False})))
    mutant("undelegated started = session", running_entry=lambda cfg, n: o["running_entry"](
        cfg, dict(n, delegate={"id": DISP})))
    mutant("started beats hand-off", running_entry=lambda cfg, n: o["running_entry"](
        cfg if dig(n, "state", "type") != "started" else dict(cfg, order={"handoff_states": []}), n))
    mutant("history cap ignored", read_history=lambda b, i, limit: o["read_history"](b, i, 10 ** 6))
    mutant("short history entry accepted", HISTORY_KEYS=())
    mutant("any app user is busy", read_busy=lambda b, i, limit, disp: o["read_busy"](b, i, limit, [DISP, OTHER]))
    mutant("unread comments = idle", read_busy=lambda *a: bool(o["read_busy"](*a)))
    mutant("first page only", paged=lambda b, q, v, path, limit: o["paged"](
        b, q, v, path, 1 if q is Q_CANDIDATES else limit))
    mutant("old day's ledger kept", read_ledger=keep_old)
    mutant("unreadable ledger = zero", read_ledger=blank(o["read_ledger"], ZERO))
    mutant("unreadable findings = none", read_findings=blank(o["read_findings"], {}))
    mutant("unreadable plans = none", read_plans=blank(o["read_plans"], {}))
    mutant("partly-read tracker = ok", result_of=lambda s: o["result_of"](dict(s, tracker=dict(
        s["tracker"], state=READ if s["tracker"]["state"] == PARTLY else s["tracker"]["state"]))))
    mutant("unread record not partial", result_of=lambda s: o["result_of"](
        dict(s, findings={"state": READ}, plans={"state": READ}, ledger={"state": READ})))
    mutant("request cap ignored", out_of_requests=lambda budget: False)
    mutant("clock ignored", out_of_time=lambda budget: False)
    mutant("ticket cap ignored", read_tickets=lambda b, cfg, pr: o["read_tickets"](
        b, cap(cfg, max_tickets=10 ** 6), pr))
    mutant("plan past max_pages kept", read_epics=lambda b, cfg, t, rec, pr: o["read_epics"](
        b, cap(cfg, max_pages=10 ** 6), t, rec, pr))
    mutant("a ticket with pieces not a plan", has_children=lambda t: False)
    mutant("the bridge's plans not read", read_epics=lambda b, cfg, t, rec, pr: o["read_epics"](
        b, cfg, t, {}, pr))
    mutant("a parent that is no plan read as one", is_plan=lambda node: True)
    mutant("a plan's piece judged alone when its plan is left out", read_epics=lambda *a: (
        o["read_epics"](*a)[0], set()))
    mutant("a page too big for the tracker", PAGE=50, Q_CANDIDATES=Q_CANDIDATES.replace(
        "first: %d," % PAGE, "first: 50,"))
    mutant("failed pass keeps a record", failed=lambda cfg, now, why_: dict(o["failed"](
        cfg, now, why_), findings={}))
    mutant("the day taken from UTC", local_date=lambda now: iso(now)[:10])
    mutant("file readable by anyone", write=lambda doc, path: (o["write"](doc, path), os.chmod(path, 0o644)))
    mutant("file written in place", write=lambda doc, path: (os.makedirs(os.path.dirname(path), exist_ok=True),
                                                             open(path, "w").write(dump(doc))))
    mutant("git working tree accepted", inside_git_worktree=lambda path: False)
    mutant("relative path accepted", resolve_path=lambda raw, key, off_when_empty=False: o["resolve_path"](
        os.path.join(HOME, raw) if raw and not os.path.isabs(raw) and raw[:2] != "~/" else raw, key, off_when_empty))
    mutant("unknown setting ignored", reader_config=lambda raw: o["reader_config"](dict(
        (k, v) for k, v in raw.items() if k in DEFAULTS)))
    mutant("unknown section ignored", read_config=lambda raw: o["read_config"](dict(
        (k, v) for k, v in raw.items() if k in SECTIONS)))
    mutant("key printed", scrub=lambda text, secret: text)
    mutant("key variable echoed in a problem", reader_config=lambda raw: (lambda r: (r[0], [
        p + str(raw.get("linear_key_env")) for p in r[1]]))(o["reader_config"](raw)))
    mutant("history field not queried", Q_HISTORY=Q_HISTORY.replace("toParentId ", ""))
    mutant("no key still asks", credential=lambda environ, name: ("any-key", None))
    for name, patched in MUTANTS:
        with patch(**patched):
            try:
                hit = battery() != got
            except Exception:  # noqa: BLE001 — a mutant the scenes cannot even read is caught too
                hit = True
        check("mutant: %s is caught" % name, hit, True)
    check("…and with every mutant undone the reader is as it was", battery() == got, True)


if __name__ == "__main__":
    sys.exit(main())
