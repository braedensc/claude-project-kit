#!/usr/bin/env python3
"""The validator's facts for one item: what the hard limits, the evidence and the agent's brief read,
gathered once from the parts of the kit that own them (KIT-791). Read-only.

    from pipeline_validator_facts import config, for_merge, for_piece, for_plan, origin_of
    for_merge(cfg, owner_repo, number, now)   one pull request
    for_piece(cfg, ticket, now)               one piece, before it starts
    for_plan(cfg, ticket, now)                one plan, before it is approved (KIT-544)
    origin_of(cfg, ticket)                    who filed the idea behind a ticket, a plan's too

    python3 scripts/pipeline_validator_facts.py show --config PATH --repo OWNER/NAME --pr N
    python3 scripts/pipeline_validator_facts.py --selftest

THE SHAPE.  One document for every item. Each consumer reads its own part, and looks nothing up again:
  item      {"kind": "merge"|"piece"|"plan", "ref": "owner/name#N"|"TEAM-12", "head": <40 hex>|None}:
            what pipeline_validator_limits.limits() is asked about
  limits    {"origin", "paths", "risk_paths", "pr", "window", "queue", "bounces", "watcher", "hold"}:
            exactly the facts limits() reads, each None when it could not be read
  evidence  {"last_piece", "acceptance"}: what pipeline_validator_evidence.evidence_for() takes
            besides the platform, which it reads itself
  brief     {"criteria", "diff", "ticket"}: what the brief builder takes. A merge's: the criteria
            as delegated, and the diff, for build_brief() with the evidence record. A piece's: its
            plan's criteria, and the piece's own text, for build_ticket_brief() (KIT-543). A plan's:
            its idea's text, and the plan with every piece as written, for the same (KIT-544)
  ticket, plan, idea   the tracker ids it followed, or None
  state     a plan's own state type, as the tracker gives it (`backlog` until it is approved);
            None for the other kinds, or when it was not read
  last_change   a plan's newest change, as the tracker dates it: the newest `updatedAt` of the plan
            and of every piece, in seconds; None for the other kinds, or when one was not read
  unread    {fact: why}: every fact it could not read, by name, in fixed words

UNREAD IS NEVER CLEAR (contract section 13).  A fact it could not read is None in its place and named in
`unread`, never an empty list, a zero or a clear answer. The limits read None as a reason to hold
(`origin` None is `provenance-unknown`, `paths` None is `paths-unknown`, and so on), the evidence reads an
unknown last piece as `no`, and the brief refuses to be built without criteria and a diff. So a source
that is down holds the item; it never lets it through.

WHERE EACH FACT COMES FROM, each through the reader of the part that owns it:
  ticket       the tracker's attachment for the pull request's address, by Stage E's own reader
               (pipeline_bounce_local.linear_ticket_for_pr_url): Linear's record, never the branch name
  origin       piece, then its plan, then the planning run, then the idea, then the queue pool's own
               rule for a ticket the owner filed: the bridge config's plan_run() and run_idea(), and
               the limits' idea_origin(). A ticket with no plan is judged by the same rule itself. A
               plan starts at its own receipt.
               Agent-filed work's generation is the pool's to certify, and is not read here.
  pr, paths    the pull request and every file it changes, a rename's old name too, from GitHub. A
               plan's paths: the ones its own text and every piece's name (the limits' named_paths)
  risk_paths   `autonomy.riskPaths` in delivery.json on the default branch; none named is []
  bounces      Stage E's ledger by the limits' bounce_fact(), against `budgets.maxBounces`, read
               by Stage E's own parse_delivery()
  window       the queue's working window, by the limits' window_open()
  queue        the runaway stops, from the queue's own order() over its usage file and its ledger
               of starts, by the limits' queue_fact()
  watcher      the watcher's status file, by the limits' watcher_state()
  hold         the watcher's hold, by the limits' hold_state()
  last_piece   the plan's other pieces, all done, from the tracker
  acceptance   the acceptance poller's ledger (load_ledger) for the plan at this head
  criteria     Stage E's criteria snapshot (read_snapshot), as they were at delegation
  diff         the pull request's diff, from GitHub

WHAT IT NEVER DOES (asserted in --selftest): write a file, change anything on GitHub or in the
tracker, or start a process but `gh` for its reads.
"""
import argparse
import base64
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_acceptance_poller as acceptance_poller
    import pipeline_bounce_local as stage_e
    import pipeline_bridge_config as bridge_config
    import pipeline_criteria_snapshot as snapshots
    import pipeline_morning as morning
    import pipeline_queue_order as queue_order
    import pipeline_queue_pool as pool
    import pipeline_queue_read as queue_read
    import pipeline_queue_start as queue_start
    import pipeline_validator_limits as limits
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The facts are read through those files' own readers, and kept in no copy." % exc)

FACTS = ("origin", "paths", "risk_paths", "pr", "window", "queue", "bounces", "watcher", "hold",
         "last_piece", "acceptance", "criteria", "diff", "ticket")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
TICKET_RE = re.compile(r"[A-Z][A-Z0-9]{0,9}-[0-9]{1,7}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
ENV_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
MAX_FILE_PAGES = 30                  # GitHub lists at most 3000 files of a pull request
MAX_DIFF_CHARS = 4 * 1024 * 1024
MAX_CHILDREN = 50
DEFAULTS = {
    "linear_key_env": "",            # the NAME of the variable that holds a READ-ONLY tracker key
    "team_keys": [],                 # the work teams a pull request's ticket may belong to
    "queue_config": "",              # the queue's config: its pool, order, window, morning, reader
    "stage_e_ledger": "",            # Stage E's bounce-ledger.jsonl
    "snapshot_dir": "",              # Stage E's basis-snapshots folder
    "acceptance_ledger": "",         # the acceptance poller's ledger
    "watcher_status": "",            # the watcher's status file
    "hold_file": "",                 # the watcher's hold
    "watcher_max_age": limits.DEFAULT_WATCHER_MAX_AGE,
}
PATH_KEYS = ("queue_config", "stage_e_ledger", "snapshot_dir", "acceptance_ledger", "watcher_status",
             "hold_file")
ISSUE_QUERY = ("query($id: String!) { issue(id: $id) { id identifier title description creator { id } "
               "team { key } labels(first: 20) { nodes { name } } parent { id identifier description "
               "creator { id } labels(first: 20) { nodes { name } } "
               "children(first: %d) { nodes { identifier state { type } } "
               "pageInfo { hasNextPage } } } } }" % MAX_CHILDREN)
# A plan, with every piece's own text and when each last changed (KIT-544): what the agent judges, and
# what says whether a decision is about the plan as it stands.
PLAN_QUERY = ("query($id: String!) { issue(id: $id) { id identifier title description updatedAt "
              "creator { id } team { key } labels(first: 20) { nodes { name } } parent { id } "
              "state { type } children(first: %d) { nodes { id identifier title description "
              "updatedAt } pageInfo { hasNextPage } } } }" % MAX_CHILDREN)
# The plan states that wait for an approval: a plan someone approved, started or closed has left them.
PLAN_WAITING = ("backlog", "triage")


# Everything Stage E's tracker reader can raise for a read that did not come back whole: its own
# error, a socket timeout during the read (an OSError, not a URLError), and an answer of the wrong
# shape. Each is an unread fact, never a crash of the whole read.
TRACKER_FAULTS = (stage_e.BounceError, OSError, AttributeError, TypeError, ValueError, KeyError)


class Unread(Exception):
    """A fact that could not be read. Its words are fixed: they never quote what a source said."""


def config(raw):
    """(cfg, problems): the facts section with its defaults, every problem named by key. A path left
    empty is a source not configured: its facts are said as unread, never guessed."""
    if not isinstance(raw, dict):
        return None, ["the facts config is not an object"]
    cfg = dict((k, raw.get(k, d)) for k, d in DEFAULTS.items())
    bad = ["%s: not a setting" % repr(k)[:40] for k in sorted(raw, key=repr) if k not in DEFAULTS]
    if not (isinstance(cfg["linear_key_env"], str)
            and (cfg["linear_key_env"] == "" or ENV_RE.fullmatch(cfg["linear_key_env"]))):
        bad.append("linear_key_env: must be the NAME of an environment variable, never the key")
    keys = cfg["team_keys"]
    if not isinstance(keys, list) or not all(isinstance(k, str) and re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", k)
                                             for k in keys):
        bad.append("team_keys: must list team keys, such as TOD")
    for key in PATH_KEYS:
        v = cfg[key]
        if not isinstance(v, str) or "\0" in v or (v and not os.path.isabs(os.path.expanduser(v))):
            bad.append("%s: must be an absolute path, or empty for none" % key)
        else:
            cfg[key] = os.path.expanduser(v) if v else ""
    age = cfg["watcher_max_age"]
    if isinstance(age, bool) or not isinstance(age, int) or not 60 <= age <= 86400:
        bad.append("watcher_max_age: must be a whole number of seconds from 60 to 86400")
    return (None if bad else cfg), bad


# --------------------------------------------------------------------------- #
# The two doors: the code host (gh, read-only) and the tracker (Stage E's own reader)
# --------------------------------------------------------------------------- #
def _gh(args):
    """(ok, stdout, stderr) for one read-only `gh api` call."""
    import subprocess
    try:
        p = subprocess.run(["gh"] + list(args), capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False, "", "gh could not be started"
    return p.returncode == 0, p.stdout, p.stderr


def _api(run, path, why):
    ok, out, err = run(["api", path])
    if not ok:
        raise Unread("%s: GitHub %s" % (why, "has no such thing" if "404" in (err or "")
                                        else "did not answer"))
    try:
        return json.loads(out)
    except ValueError:
        raise Unread("%s: GitHub's answer was not JSON" % why)


def _tracker_cfg(cfg):
    return {"linear_api_key_env": cfg["linear_key_env"], "team_keys": list(cfg["team_keys"])}


def _issue(cfg, ident, query=ISSUE_QUERY):
    """One ticket, with its parent plan and that plan's pieces, through Stage E's tracker reader. With
    PLAN_QUERY, one plan with every piece's text."""
    if not cfg["linear_key_env"]:
        raise Unread("no linear_key_env is configured, so the tracker is not read")
    try:
        data = stage_e.linear_graphql(query, {"id": ident}, _tracker_cfg(cfg))
    except TRACKER_FAULTS:
        raise Unread("the tracker did not answer for %s" % ident)
    node = data.get("issue") if isinstance(data, dict) else None
    if not isinstance(node, dict) or node.get("identifier") != ident:
        raise Unread("the tracker has no ticket %s" % ident)
    return node


def _empty(kind, ref):
    return {"item": {"kind": kind, "ref": ref, "head": None},
            "limits": dict((k, None) for k in ("origin", "paths", "risk_paths", "pr", "window",
                                               "queue", "bounces", "watcher", "hold")),
            "evidence": {"last_piece": None, "acceptance": None},
            "brief": {"criteria": None, "diff": None, "ticket": None},
            "ticket": None, "plan": None, "idea": None, "state": None, "last_change": None,
            "unread": {}}


def _read(doc, name, fn):
    """Run one reader. Its answer, or None with the reason said under `name` in `unread`."""
    try:
        return fn()
    except Unread as exc:
        doc["unread"][name] = str(exc)
        return None


# --------------------------------------------------------------------------- #
# Provenance: piece, plan, planning run, idea, the pool's owner rule
# --------------------------------------------------------------------------- #
def _queue_raw(cfg):
    path = cfg["queue_config"]
    if not path:
        raise Unread("no queue_config is configured")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raise Unread("the queue's config could not be read")
    if not isinstance(raw, dict):
        raise Unread("the queue's config is not an object")
    return raw


def _findings(raw):
    rcfg, bad = queue_read.reader_config(raw.get("reader") or {})
    if bad or not rcfg["findings_file"]:
        raise Unread("the finding record is not configured in the queue's reader")
    try:
        with open(rcfg["findings_file"], encoding="utf-8") as fh:
            found = json.load(fh)
    except (OSError, ValueError):
        raise Unread("the finding record could not be read")
    if not isinstance(found, dict):
        raise Unread("the finding record is not one")
    return found


def _is_plan(node):
    return isinstance(node, dict) and pool.GATE_EPIC_LABEL in pool.label_names(node)


def _idea_of_plan(cfg, pcfg, plan_node):
    """The idea a plan was filed from, as the tracker's ticket: the plan's receipt names its planning
    run, and the run names its idea, each on a ticket the owner's key filed. Raises Unread otherwise."""
    plan = plan_node.get("identifier")
    runs = [r for r in (bridge_config.plan_run(plan_node, owner) for owner in pcfg["owner_user_ids"])
            if r]
    if len(runs) != 1:
        raise Unread("plan %s names no planning run filed with the owner's key" % plan)
    run_node = _issue(cfg, runs[0])
    ideas = [i for i in (bridge_config.run_idea(run_node, owner) for owner in pcfg["owner_user_ids"])
             if i]
    if len(ideas) != 1:
        raise Unread("the planning run of %s names no idea" % plan)
    return _issue(cfg, ideas[0])


def _origin(cfg, ident, node, is_plan=False):
    """(origin, plan id, the idea's ticket) for ticket `ident`, read as `node`. A plan starts at its
    own receipt; any other ticket at its parent plan's, or is its own idea when it has none."""
    raw = _queue_raw(cfg)
    pcfg, bad = pool.config(raw.get("pool") or {})
    if bad or not pcfg:
        raise Unread("the queue's pool config is not usable")
    findings = _findings(raw)
    parent = node.get("parent") if isinstance(node.get("parent"), dict) else None
    plan, idea_node = None, node
    if is_plan:
        if not _is_plan(node) or parent is not None:
            raise Unread("%s is not a plan the idea gate filed" % ident)
        plan, idea_node = ident, _idea_of_plan(cfg, pcfg, node)
    elif parent is not None:
        if not _is_plan(parent):
            raise Unread("%s sits under a ticket that is not a plan the idea gate filed" % ident)
        plan, idea_node = parent.get("identifier"), _idea_of_plan(cfg, pcfg, parent)
    got = limits.idea_origin(raw.get("pool") or {}, idea_node, findings)
    if got not in limits.ORIGINS:
        raise Unread("the idea behind %s is not certified as one the owner filed" % ident)
    return got, plan, idea_node


def origin_of(cfg, ident, node=None):
    """(origin, plan id, idea id): `owner` when the owner filed the idea behind ticket `ident`, by the
    queue pool's own rule. Raises Unread for anything it cannot certify, in fixed words: a source that
    could not be read, or an idea that is not certified as the owner's."""
    got, plan, idea_node = _origin(cfg, ident, node or _issue(cfg, ident))
    return got, plan, idea_node.get("identifier")


def _last_piece(node, ident):
    """True when every other piece of this ticket's plan is done; False for a ticket in no plan."""
    parent = node.get("parent") if isinstance(node.get("parent"), dict) else None
    if parent is None:
        return False
    kids = parent.get("children") if isinstance(parent.get("children"), dict) else {}
    nodes = kids.get("nodes")
    if (not isinstance(nodes, list) or (kids.get("pageInfo") or {}).get("hasNextPage") is not False
            or not all(isinstance(n, dict) for n in nodes)
            or ident not in [n.get("identifier") for n in nodes]):
        raise Unread("the pieces of plan %s could not all be read" % parent.get("identifier"))
    return all(((n.get("state") or {}).get("type") in ("completed", "canceled")) for n in nodes
               if n.get("identifier") != ident)


# --------------------------------------------------------------------------- #
# The facts every item shares, and each kind's own
# --------------------------------------------------------------------------- #
def _shared(cfg, doc, now, piece_queue=None):
    """The facts every item shares. `piece_queue`, for a piece, replaces the merge's queue fact."""
    lim = doc["limits"]

    def queue_fact():
        raw = _queue_raw(cfg)
        mcfg, bad = morning.check_morning(raw.get("morning") or {})
        rcfg, rbad = queue_read.reader_config(raw.get("reader") or {})
        if bad or rbad:
            raise Unread("the queue's morning or reader section is not usable")
        try:
            with open(os.path.expanduser(mcfg["usage_file"]), encoding="utf-8") as fh:
                usage = json.load(fh)
        except (OSError, ValueError):
            usage = None        # the queue's own stop says a usage file it cannot use
        today = queue_order.local_date(now)
        try:
            ledger = queue_start.read_ledger(rcfg["ledger_file"], today) if rcfg["ledger_file"] else None
        except (OSError, ValueError):
            ledger = None       # the queue's own stop says a ledger it cannot count on
        try:
            result = queue_order.order(raw.get("order") or {}, now, [], [], usage, ledger)
        except (ValueError, TypeError, KeyError):
            raise Unread("the queue's order config is not usable")
        got = limits.queue_fact(result)
        if got is None:
            raise Unread("the queue's order gave no stops to read")
        return got

    def window_fact():
        got = limits.window_open((_queue_raw(cfg)).get("window") or {}, now)
        if got is None:
            raise Unread("the queue's working window config is not usable")
        return got

    def watcher_fact():
        if not cfg["watcher_status"]:
            raise Unread("no watcher_status is configured")
        got = limits.watcher_state(cfg["watcher_status"], now, cfg["watcher_max_age"])
        if got is None:
            raise Unread("the watcher's status file could not be read or trusted")
        return got

    def hold_fact():
        if not cfg["hold_file"]:
            raise Unread("no hold_file is configured")
        got = limits.hold_state(cfg["hold_file"])
        if got not in (limits.hold.NONE, limits.hold.HELD):
            raise Unread("the watcher's hold could not be read")
        return got

    lim["window"] = _read(doc, "window", window_fact)
    lim["queue"] = _read(doc, "queue", piece_queue or queue_fact)
    lim["watcher"] = _read(doc, "watcher", watcher_fact)
    lim["hold"] = _read(doc, "hold", hold_fact)


def _delivery(run, owner_repo, branch):
    """(riskPaths, maxBounces) from delivery.json on the default branch. No file is no risk paths and
    no budget; a file Stage E's own parser refuses is unread."""
    ok, out, err = run(["api", "repos/%s/contents/delivery.json?ref=%s" % (owner_repo, branch)])
    if not ok and "404" in (err or ""):
        return [], None
    if not ok:
        raise Unread("delivery.json on %s could not be read" % branch)
    try:
        raw = base64.b64decode(json.loads(out).get("content") or "").decode("utf-8")
        doc = json.loads(raw)
    except (ValueError, AttributeError, TypeError, UnicodeDecodeError):
        raise Unread("delivery.json on %s is not readable JSON" % branch)
    autonomy = doc.get("autonomy", {}) if isinstance(doc, dict) else None
    if not isinstance(autonomy, dict):
        raise Unread("delivery.json on %s gives an autonomy section that is not an object" % branch)
    risk = autonomy.get("riskPaths", [])
    if not isinstance(risk, list) or not all(isinstance(g, str) for g in risk):
        raise Unread("delivery.json on %s gives riskPaths that are not a list of paths" % branch)
    budget, _threshold, _label, _state, state = stage_e.parse_delivery(raw)
    return risk, (budget if state == "ok" else None)


def for_merge(cfg, owner_repo, number, now, run=None):
    """The facts for pull request `owner_repo#number`, as it stands now. See THE SHAPE above."""
    run = run or _gh
    ref = "%s#%s" % (owner_repo, number)
    doc = _empty("merge", ref)
    if (not isinstance(owner_repo, str) or not REPO_RE.fullmatch(owner_repo)
            or isinstance(number, bool) or not isinstance(number, int) or number < 1):
        doc["unread"] = dict((f, "the pull request is not OWNER/NAME and a number") for f in FACTS)
        return doc
    lim = doc["limits"]
    pr = _read(doc, "pr", lambda: _api(run, "repos/%s/pulls/%d" % (owner_repo, number),
                                       "the pull request"))
    head = (pr.get("head") or {}).get("sha") if isinstance(pr, dict) and isinstance(
        pr.get("head"), dict) else None
    if isinstance(pr, dict) and isinstance(head, str) and SHA_RE.fullmatch(head) and all(
            isinstance(pr.get(k), int) and not isinstance(pr.get(k), bool) and pr[k] >= 0
            for k in ("additions", "deletions", "changed_files")):
        doc["item"]["head"] = head
        lim["pr"] = {"head": head, "additions": pr["additions"], "deletions": pr["deletions"]}
    else:
        lim["pr"] = None
        doc["unread"].setdefault("pr", "the pull request's head and size could not be read")
    base = ((pr.get("base") or {}).get("repo") or {}).get("default_branch") if isinstance(pr, dict) \
        and isinstance(pr.get("base"), dict) and isinstance(pr["base"].get("repo"), dict) else None
    url = pr.get("html_url") if isinstance(pr, dict) else None

    def paths_fact():
        if lim["pr"] is None:
            raise Unread("the pull request was not read, so its files were not")
        names, files = [], 0
        for page in range(1, MAX_FILE_PAGES + 1):
            batch = _api(run, "repos/%s/pulls/%d/files?per_page=100&page=%d" % (owner_repo, number, page),
                         "the pull request's files")
            if not isinstance(batch, list) or not all(
                    isinstance(f, dict) and isinstance(f.get("filename"), str) and f["filename"]
                    for f in batch):
                raise Unread("the pull request's files were not a list of files")
            files += len(batch)
            for f in batch:
                names.append(f["filename"])
                if isinstance(f.get("previous_filename"), str) and f["previous_filename"]:
                    names.append(f["previous_filename"])      # a rename: its old name counts too
            if len(batch) < 100:
                break
        if files != pr["changed_files"]:
            raise Unread("the pull request's file list was cut short")
        if not names:
            raise Unread("the pull request changes no file")
        return sorted(set(names))
    lim["paths"] = _read(doc, "paths", paths_fact)

    budget = [None]

    def risk_fact():
        if not isinstance(base, str) or not base:
            raise Unread("the repository's default branch was not read")
        risk, budget[0] = _delivery(run, owner_repo, base)
        return risk
    lim["risk_paths"] = _read(doc, "risk_paths", risk_fact)

    def bounces_fact():
        if not cfg["stage_e_ledger"]:
            raise Unread("no stage_e_ledger is configured")
        if budget[0] is None:
            raise Unread("delivery.json on the default branch names no send-back budget")
        got = limits.bounce_fact(cfg["stage_e_ledger"], owner_repo, number, budget[0])
        if got is None:
            raise Unread("Stage E's ledger could not be read")
        return got
    lim["bounces"] = _read(doc, "bounces", bounces_fact)

    def ticket_fact():
        if not isinstance(url, str) or not url:
            raise Unread("the pull request's address was not read")
        if not cfg["linear_key_env"]:
            raise Unread("no linear_key_env is configured, so the tracker is not read")
        try:
            got = stage_e.linear_ticket_for_pr_url(url, _tracker_cfg(cfg))
        except TRACKER_FAULTS:
            raise Unread("the tracker did not answer for the pull request")
        if not got or not TICKET_RE.fullmatch(got):
            raise Unread("the tracker records no one ticket for the pull request")
        return got
    doc["ticket"] = _read(doc, "ticket", ticket_fact)
    node = None
    if doc["ticket"]:
        node = _read(doc, "origin", lambda: _issue(cfg, doc["ticket"]))
    else:
        doc["unread"].setdefault("origin", "its ticket was not read")
    if node is not None:
        got = _read(doc, "origin", lambda: origin_of(cfg, doc["ticket"], node))
        if got:
            lim["origin"], doc["plan"], doc["idea"] = got
        doc["evidence"]["last_piece"] = _read(doc, "last_piece", lambda: _last_piece(node, doc["ticket"]))
    else:
        doc["unread"].setdefault("last_piece", "its ticket was not read")

    def acceptance_fact():
        if doc["evidence"]["last_piece"] is not True:
            return None if doc["evidence"]["last_piece"] is False else _raise(
                "it is not known whether this is the plan's last piece")
        if not cfg["acceptance_ledger"]:
            raise Unread("no acceptance_ledger is configured")
        plan = (node.get("parent") or {}).get("identifier")
        try:
            rows = acceptance_poller.load_ledger(cfg["acceptance_ledger"])
        except acceptance_poller.LedgerError:
            raise Unread("the acceptance ledger could not be read")
        verdict = (rows.get((plan, doc["item"]["head"])) or {}).get("verdict") or {}
        return verdict.get("word") if verdict.get("status") == "written" else "none"
    doc["evidence"]["acceptance"] = _read(doc, "acceptance", acceptance_fact)

    def criteria_fact():
        if not doc["ticket"]:
            raise Unread("its ticket was not read")
        if not cfg["snapshot_dir"]:
            raise Unread("no snapshot_dir is configured")
        try:
            snap = snapshots.read_snapshot(cfg["snapshot_dir"], doc["ticket"])
        except snapshots.SnapshotError:
            raise Unread("the criteria snapshot for %s could not be read" % doc["ticket"])
        if not isinstance(snap, dict) or not snap.get("acceptance_criteria"):
            raise Unread("no criteria were snapshotted for %s when it was delegated" % doc["ticket"])
        return criteria_text(snap["acceptance_criteria"], snap.get("out_of_scope") or [])
    doc["brief"]["criteria"] = _read(doc, "criteria", criteria_fact)

    def diff_fact():
        ok, out, _err = run(["api", "-H", "Accept: application/vnd.github.v3.diff",
                             "repos/%s/pulls/%d" % (owner_repo, number)])
        if not ok or not isinstance(out, str) or not out.strip():
            raise Unread("the pull request's diff could not be read")
        if len(out) > MAX_DIFF_CHARS:
            raise Unread("the pull request's diff is larger than %d characters" % MAX_DIFF_CHARS)
        return out
    doc["brief"]["diff"] = _read(doc, "diff", diff_fact)
    _shared(cfg, doc, now)
    return doc


def _raise(why):
    raise Unread(why)


def _risk_of(cfg, run, node, what):
    """The riskPaths of the repository the queue's reader names for this ticket, from delivery.json on
    its default branch. `what` names the ticket in the words an unread fact says."""
    rcfg, bad = queue_read.reader_config(_queue_raw(cfg).get("reader") or {})
    repo = None if bad else queue_read.repo_of(rcfg, node)
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise Unread("the %s's repository is not known from the queue's reader" % what)
    info = _api(run, "repos/%s" % repo, "the %s's repository" % what)
    branch = info.get("default_branch") if isinstance(info, dict) else None
    if not isinstance(branch, str) or not branch:
        raise Unread("the %s's repository named no default branch" % what)
    return _delivery(run, repo, branch)[0]


def criteria_text(acceptance, out_of_scope):
    """The snapshot's criteria as the brief's text: the acceptance criteria, then what is out of scope."""
    lines = ["## Acceptance criteria", ""] + ["- [ ] %s" % c for c in acceptance]
    if out_of_scope:
        lines += ["", "## Out of scope", ""] + ["- %s" % c for c in out_of_scope]
    return "\n".join(lines) + "\n"


def for_piece(cfg, ident, now, run=None, queue_result=None):
    """The facts for piece `ident` before it starts: who filed its idea, the paths its ticket names,
    its repository's riskPaths, its plan, and the facts every item shares. It has no pull request, no
    diff and no send-backs yet, and says so. Its queue fact is the queue's own: `queue_result` is the
    order() result of the pass that is about to start it, which judged this piece against the day's
    load. Without one, or one that does not name the piece, whether it fits the day is unread."""
    run = run or _gh
    doc = _empty("piece", ident)
    for fact in ("pr", "bounces", "diff", "acceptance", "last_piece"):
        doc["unread"][fact] = "a piece that has not started has no %s" % fact.replace("_", " ")
    if not isinstance(ident, str) or not TICKET_RE.fullmatch(ident):
        doc["unread"] = dict((f, "the piece is not a ticket id") for f in FACTS)
        return doc
    doc["ticket"] = ident
    node = _read(doc, "origin", lambda: _issue(cfg, ident))
    if node is not None:
        got = _read(doc, "origin", lambda: origin_of(cfg, ident, node))
        if got:
            doc["limits"]["origin"], doc["plan"], doc["idea"] = got
        doc["limits"]["paths"] = _read(doc, "paths", lambda: limits.named_paths(
            [node.get("description") if isinstance(node.get("description"), str) else None])
            if isinstance(node.get("description"), str) else _raise("the piece's ticket has no text"))
        doc["limits"]["risk_paths"] = _read(doc, "risk_paths",
                                            lambda: _risk_of(cfg, run, node, "piece"))
        # The agent judges the piece as written against its plan's criteria (KIT-543): no code yet.
        parent = node.get("parent") if isinstance(node.get("parent"), dict) else {}
        plan_text = parent.get("description")
        acceptance, out = (snapshots.criteria_of(plan_text) if isinstance(plan_text, str)
                           else (None, None))
        if acceptance:
            doc["brief"]["criteria"] = criteria_text(acceptance, out)
        else:
            doc["unread"]["criteria"] = "the piece's plan has no acceptance criteria"
        text = node.get("description")
        if isinstance(text, str) and text.strip():
            doc["brief"]["ticket"] = text
    else:
        for fact in ("paths", "risk_paths", "criteria"):
            doc["unread"].setdefault(fact, "its ticket was not read")

    def piece_queue():
        if not isinstance(queue_result, dict):
            raise Unread("the queue's own order for this pass was not given, so whether the piece "
                         "fits the day's load is not known")
        named = [e.get("ticket") for e in list(queue_result.get("start") or [])
                 + list(queue_result.get("held") or []) if isinstance(e, dict)]
        if ident not in named:
            raise Unread("the queue's own order does not name this piece")
        got = limits.queue_fact(queue_result, ident)
        if got is None:
            raise Unread("the queue's own order gave no stops to read")
        return got
    _shared(cfg, doc, now, piece_queue)
    return doc


def _text(value):
    """A ticket's text as the tracker gives it: none is empty, and anything else is not text."""
    return "" if value is None else value if isinstance(value, str) else None


def plan_text(node, kids):
    """The plan as written, for the agent's ticket brief: its own title and text, then each piece's,
    in the tracker's order. Raises Unread when a text is not text."""
    parts = []
    for n, (label, ticket) in enumerate([("Plan", node)] + [("Piece", k) for k in kids]):
        title, text = _text(ticket.get("title")), _text(ticket.get("description"))
        if title is None or text is None:
            raise Unread("the text of %s could not be read" % (ticket.get("identifier") or "a piece"))
        head = "%s %s: %s" % (label, ticket.get("identifier"), " ".join(title.split()))
        if label == "Piece":
            head = "%s (%d of %d)" % (head, n, len(kids))
        parts.append("%s\n\n%s" % (head, text.strip()) if text.strip() else head)
    return "\n\n".join(parts) + "\n"


def for_plan(cfg, ident, now, run=None):
    """The facts for plan `ident` before it is approved (KIT-544): who filed its idea, the paths it and
    its pieces name, its repository's riskPaths, the facts every item shares, its state and its newest
    change, and for the agent's brief its idea's text and the plan as written. It has no pull request,
    no diff, no send-backs and no acceptance check yet, and says so."""
    run = run or _gh
    doc = _empty("plan", ident)
    for fact in ("pr", "bounces", "diff", "acceptance", "last_piece"):
        doc["unread"][fact] = "a plan that is not approved has no %s" % fact.replace("_", " ")
    if not isinstance(ident, str) or not TICKET_RE.fullmatch(ident):
        doc["unread"] = dict((f, "the plan is not a ticket id") for f in FACTS)
        return doc
    doc["ticket"] = doc["plan"] = ident
    node = _read(doc, "origin", lambda: _issue(cfg, ident, PLAN_QUERY))
    kids = None
    if node is not None:
        found = node.get("children") if isinstance(node.get("children"), dict) else {}
        nodes = found.get("nodes")
        if (isinstance(nodes, list) and all(isinstance(k, dict) for k in nodes)
                and (found.get("pageInfo") or {}).get("hasNextPage") is False):
            kids = nodes
        state = (node.get("state") or {}).get("type") if isinstance(node.get("state"), dict) else None
        doc["state"] = state if isinstance(state, str) else None
        got = _read(doc, "origin", lambda: _origin(cfg, ident, node, is_plan=True))
        if got:
            doc["limits"]["origin"], _plan, idea = got
            doc["idea"] = idea.get("identifier")
            title, text = _text(idea.get("title")), _text(idea.get("description"))
            if title is None or text is None or not (title.strip() or text.strip()):
                doc["unread"]["criteria"] = "the plan's idea has no text to judge it against"
            else:
                head = "Idea %s: %s" % (doc["idea"], " ".join(title.split()))
                doc["brief"]["criteria"] = ("%s\n\n%s\n" % (head, text.strip()) if text.strip()
                                            else head + "\n")
        else:
            doc["unread"].setdefault("criteria", "the plan's idea was not read")
        if kids is None:
            for fact in ("paths", "ticket"):
                doc["unread"][fact] = "the pieces of plan %s could not all be read" % ident
        else:
            texts = [_text(t.get("description")) for t in [node] + kids]
            doc["limits"]["paths"] = _read(doc, "paths", lambda: limits.named_paths(texts)
                                           if None not in texts else
                                           _raise("the text of plan %s or a piece could not be read"
                                                  % ident))
            doc["brief"]["ticket"] = _read(doc, "ticket", lambda: plan_text(node, kids))
            times = [pool.epoch(t.get("updatedAt")) for t in [node] + kids]
            doc["last_change"] = None if None in times else max(times)
        doc["limits"]["risk_paths"] = _read(doc, "risk_paths", lambda: _risk_of(cfg, run, node, "plan"))
    else:
        for fact in ("paths", "risk_paths", "criteria", "ticket"):
            doc["unread"].setdefault(fact, "the plan was not read")
    _shared(cfg, doc, now)
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="The validator's facts for one item (read-only).")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("show", help="read one pull request's facts and print them as fields")
    p.add_argument("--config", required=True, help="the validator's config; its `facts` section")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", type=int, required=True)
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "validator-facts-selftest-")
    if args.cmd == "show":
        import time
        try:
            with open(os.path.expanduser(args.config), encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            print("COULD NOT RUN: the config could not be read.")
            return 2
        cfg, bad = config((raw or {}).get("facts") if isinstance(raw, dict) else None)
        if bad:
            print("COULD NOT RUN: %s" % "; ".join(bad))
            return 2
        doc = for_merge(cfg, args.repo, args.pr, time.time())
        shown = dict(doc, brief={k: (None if v is None else "%d characters" % len(v))
                                 for k, v in doc["brief"].items()})
        print(json.dumps(shown, indent=1, sort_keys=True))
        return 0 if not doc["unread"] else 1
    ap.print_help()
    return 2


# ======================================================================================
# SELFTEST: nothing below the line above is run by a caller, and the source scan stops there.
# ======================================================================================
def selftest():
    import calendar
    import time
    import pipeline_machine_tickets as machine
    from scratch_run import scratch_run
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    NOW = calendar.timegm((2026, 11, 18, 15, 0, 0))     # a Wednesday, 15:00 UTC
    HEAD = "a" * 40
    OWNER, DISPATCHER = "owner-user", "dispatcher-user"
    REPO, PR = "acme/app", 12
    PR_URL = "https://github.com/acme/app/pull/12"
    SETTINGS = {"state": "read", "approve": {"plans": True, "pieces": True, "merges": True},
                "limits": {"max_changed_lines": 400, "max_files": 20},
                "allow": {"credentials": False, "billing_and_accounts": False,
                          "production_deploy": False, "deleting_data": False},
                "stage_e_login": "s[bot]", "merge_repos": ["acme/app"], "why": "read"}
    with scratch_run("validator-facts-") as tmp:
        def put(rel, doc, mode=0o644):
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(doc if isinstance(doc, str) else json.dumps(doc))
            os.chmod(path, mode)
            return path

        usage = put("usage/usage.json", {})
        starts = os.path.join(tmp, "queue", "ledger.json")
        findings = put("findings/seen.json", {})
        queue_cfg = put("queue/queue.json", {
            "pool": {"owner_user_ids": [OWNER], "dispatcher_user_ids": [DISPATCHER],
                     "team_keys": ["TOD"]},
            "order": {}, "window": {},
            "morning": {"planner": False, "usage_file": usage},
            "reader": {"findings_file": findings, "ledger_file": starts,
                       "repo_by_team": {"TOD": "acme/app"}}})
        stage_e_ledger = os.path.join(tmp, "stage-e", "bounce-ledger.jsonl")
        snaps = os.path.join(tmp, "stage-e", "basis-snapshots")
        os.makedirs(snaps, mode=0o700)
        put("stage-e/basis-snapshots/TOD-13.json", {
            "schema": snapshots.SCHEMA, "ticket_id": "TOD-13",
            "acceptance_criteria": ["Empty input returns an empty list."],
            "out_of_scope": ["The CLI."]})
        acc_ledger = os.path.join(tmp, "acceptance", "ledger.json")
        watcher_status = put("watcher/watcher-status.json", {
            "schema": limits.WATCHER_SCHEMA,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 60))})
        hold_file = os.path.join(tmp, "watcher", "hold.json")
        CFG, bad = config({"linear_key_env": "VALIDATOR_LINEAR_KEY", "team_keys": ["TOD"],
                           "queue_config": queue_cfg, "stage_e_ledger": stage_e_ledger,
                           "snapshot_dir": snaps, "acceptance_ledger": acc_ledger,
                           "watcher_status": watcher_status, "hold_file": hold_file})
        check("a config naming every source is taken", bad, [])

        # The code host: GitHub's answers by what gh is asked. Nothing else is answered.
        def pr_doc(**over):
            d = {"number": PR, "html_url": PR_URL, "head": {"sha": HEAD}, "additions": 30,
                 "deletions": 4, "changed_files": 2,
                 "base": {"ref": "main", "repo": {"default_branch": "main"}}}
            d.update(over)
            return d
        delivery = {"version": 1, "budgets": {"maxBounces": 3},
                    "autonomy": {"riskPaths": ["infra/**"]}}

        def encoded(doc):
            return json.dumps({"content": base64.b64encode(
                (doc if isinstance(doc, str) else json.dumps(doc)).encode()).decode()})

        def host(answers=None):
            table = {
                "repos/acme/app": json.dumps({"default_branch": "main"}),
                "repos/acme/app/pulls/12": json.dumps(pr_doc()),
                "repos/acme/app/pulls/12/files?per_page=100&page=1": json.dumps(
                    [{"filename": "src/a.py"},
                     {"filename": "src/b.py", "previous_filename": "src/old.py"}]),
                "repos/acme/app/contents/delivery.json?ref=main": encoded(delivery),
                "DIFF repos/acme/app/pulls/12": "diff --git a/src/a.py b/src/a.py\n+x\n",
            }
            table.update(answers or {})
            calls = []

            def run(args):
                calls.append(list(args))
                answer = table.get(("DIFF " + args[-1]) if "-H" in args else args[-1])
                if isinstance(answer, Exception):
                    return False, "", str(answer)
                if answer is None:
                    return False, "", "gh: Not Found (HTTP 404)"
                return True, answer, ""
            run.calls = calls
            return run

        # The tracker, through Stage E's own reader, stood in for.
        def issue(ident, creator=OWNER, labels=("provenance:human",), parent=None, description=""):
            return {"id": "id-" + ident, "identifier": ident, "description": description,
                    "creator": {"id": creator}, "team": {"key": "TOD"},
                    "labels": {"nodes": [{"name": n} for n in labels]}, "parent": parent}
        run_ticket = issue("TOD-5", labels=(), description="\n".join([
            machine.planning_tag(machine.PLANNING_PREFIX + "tod"), "Planning-run trigger: move-1",
            "Idea: TOD-4", "<untrusted-idea-data>", "the idea", "</untrusted-idea-data>"]))

        def plan_node(*kids, more=False):
            return {"id": "id-TOD-10", "identifier": "TOD-10", "creator": {"id": OWNER},
                    "labels": {"nodes": [{"name": pool.GATE_EPIC_LABEL}]},
                    "description": "Plan id: TOD-5/abcdef\n\n## Acceptance criteria\n\n"
                                   "- [ ] The pieces work together.\n",
                    "children": {"nodes": [{"identifier": i, "state": {"type": t}} for i, t in kids],
                                 "pageInfo": {"hasNextPage": more}}}
        PLAN = plan_node(("TOD-13", "started"), ("TOD-14", "started"))
        base_tickets = {"TOD-13": issue("TOD-13", labels=(), parent=PLAN,
                                        description="Change `src/a.py`."),
                        "TOD-5": run_ticket, "TOD-4": issue("TOD-4")}
        tickets, attached = dict(base_tickets), {"pr": ["TOD-13"]}

        def tracker(query, variables, cfg_):
            if "attachmentsForURL" in query:
                if isinstance(attached["pr"], Exception):
                    raise attached["pr"]
                return {"attachmentsForURL": {"nodes": [
                    {"issue": {"identifier": t, "description": "", "labels": {"nodes": []}}}
                    for t in attached["pr"]]}}
            node = tickets.get(variables.get("id"))
            if isinstance(node, Exception):
                raise node
            return {"issue": node}

        saved_graphql = stage_e.linear_graphql
        try:
            stage_e.linear_graphql = tracker
            gh = host()
            doc = for_merge(CFG, REPO, PR, NOW, run=gh)
            check("every fact of a pull request is read, and nothing is unread", doc["unread"], {})
            check("the limits' facts, as limits() reads them",
                  (doc["item"], doc["limits"]["origin"], doc["limits"]["paths"],
                   doc["limits"]["risk_paths"], doc["limits"]["pr"], doc["limits"]["bounces"],
                   doc["limits"]["watcher"], doc["limits"]["hold"], (doc["limits"]["queue"] or {}).get("held", "unread")),
                  ({"kind": "merge", "ref": "acme/app#12", "head": HEAD}, "owner",
                   ["src/a.py", "src/b.py", "src/old.py"], ["infra/**"],
                   {"head": HEAD, "additions": 30, "deletions": 4},
                   {"spent": 0, "budget": 3, "exhausted": False}, "fresh", "none", None))
            check("the ticket, its plan and its idea: piece, plan, planning run, idea",
                  (doc["ticket"], doc["plan"], doc["idea"]), ("TOD-13", "TOD-10", "TOD-4"))
            check("the evidence's own inputs: not the last piece, so no acceptance is asked",
                  doc["evidence"], {"last_piece": False, "acceptance": None})
            check("the brief's inputs: the criteria as delegated, and the diff",
                  (doc["brief"]["criteria"].splitlines()[2],
                   doc["brief"]["diff"].startswith("diff --git")),
                  ("- [ ] Empty input returns an empty list.", True))
            reasons = limits.limits(doc["item"], SETTINGS, doc["limits"])["reasons"]
            check("limits() reads every fact as known: no reason of its ends in -unknown",
                  [r for r in reasons if r.endswith("-unknown")], [])
            check("it asks GitHub through gh api and changes nothing there",
                  [c for c in gh.calls if c[0] != "api" or any(
                      a in c for a in ("-X", "--method", "-f", "-F", "--input", "--field"))], [])

            # Each fact, unread: None in its place, said by name, never clear, empty or zero.
            def facts_with(answers=None, cfg_over=None, tickets_over=None, attach=None, piece=False):
                tickets.clear()
                tickets.update(base_tickets)
                tickets.update(tickets_over or {})
                attached["pr"] = attach if attach is not None else ["TOD-13"]
                cfg_ = dict(CFG, **(cfg_over or {}))
                return (for_piece(cfg_, "TOD-13", NOW, run=host(answers)) if piece
                        else for_merge(cfg_, REPO, PR, NOW, run=host(answers)))

            def value(d, fact):
                for part in ("limits", "evidence", "brief"):
                    if fact in d[part]:
                        return d[part][fact]
                return d.get(fact)
            bad_hold = put("watcher/bad-hold.json", "{ not a hold")
            # Each limits fact's own reason when it is unread: the limits name what they could not tell.
            UNKNOWN = {"origin": "provenance-unknown", "paths": "paths-unknown",
                       "risk_paths": "risk-paths-unknown", "pr": "size-unknown",
                       "bounces": "bounces-unknown", "window": "window-unknown",
                       "queue": "queue-unknown", "watcher": "watcher-unknown",
                       "hold": "hold-unknown"}
            for label, kw, fact in (
                    ("the pull request", {"answers": {"repos/acme/app/pulls/12": Exception("HTTP 502")}},
                     "pr"),
                    ("its files, cut short", {"answers": {"repos/acme/app/pulls/12": json.dumps(
                        pr_doc(changed_files=5))}}, "paths"),
                    ("its files, not files", {"answers": {
                        "repos/acme/app/pulls/12/files?per_page=100&page=1": json.dumps([{"x": 1}])}},
                     "paths"),
                    ("delivery.json, refused by Stage E's parser", {"answers": {
                        "repos/acme/app/contents/delivery.json?ref=main": encoded("{not json")}},
                     "risk_paths"),
                    ("delivery.json, unanswered", {"answers": {
                        "repos/acme/app/contents/delivery.json?ref=main": Exception("HTTP 502")}},
                     "risk_paths"),
                    ("delivery.json, an autonomy that is not an object", {"answers": {
                        "repos/acme/app/contents/delivery.json?ref=main": encoded(
                            {"version": 1, "budgets": {"maxBounces": 3}, "autonomy": ["x"]})}},
                     "risk_paths"),
                    ("Stage E's ledger, not configured", {"cfg_over": {"stage_e_ledger": ""}}, "bounces"),
                    ("Stage E's ledger, corrupt", {"cfg_over": {"stage_e_ledger": put(
                        "stage-e/corrupt.jsonl", "not json\n")}}, "bounces"),
                    ("the tracker's attachment", {"attach": stage_e.BounceError("down")}, "ticket"),
                    ("the tracker timing out mid-read", {"attach": TimeoutError("timed out")}, "ticket"),
                    ("a pull request two tickets claim", {"attach": ["TOD-13", "TOD-14"]}, "ticket"),
                    ("an idea the owner did not file", {"tickets_over": {"TOD-4": issue(
                        "TOD-4", creator=DISPATCHER)}}, "origin"),
                    ("a planning run the tracker would not give", {"tickets_over": {
                        "TOD-5": stage_e.BounceError("down")}}, "origin"),
                    ("a planning run read with a timeout", {"tickets_over": {
                        "TOD-5": TimeoutError("timed out")}}, "origin"),
                    ("a ticket under a plan the idea gate did not file", {"tickets_over": {
                        "TOD-13": issue("TOD-13", labels=(), parent=dict(PLAN, labels={"nodes": []}))}},
                     "origin"),
                    ("the finding record", {"cfg_over": {"queue_config": put("queue/no-findings.json", {
                        "pool": {"owner_user_ids": [OWNER], "dispatcher_user_ids": [DISPATCHER],
                                 "team_keys": ["TOD"]}, "reader": {"findings_file": ""}})}}, "origin"),
                    ("the watcher, not configured", {"cfg_over": {"watcher_status": ""}}, "watcher"),
                    ("the watcher's file, unreadable", {"cfg_over": {"watcher_status": put(
                        "watcher/bad.json", "{")}}, "watcher"),
                    ("the hold, not a hold", {"cfg_over": {"hold_file": bad_hold}}, "hold"),
                    ("the hold, not configured", {"cfg_over": {"hold_file": ""}}, "hold"),
                    ("the queue's config", {"cfg_over": {"queue_config": os.path.join(tmp, "none.json")}},
                     "queue"),
                    ("the queue's window", {"cfg_over": {"queue_config": put("queue/bad-window.json", {
                        "window": {"days": "every day"}})}}, "window"),
                    ("the criteria snapshot, none taken", {"cfg_over": {"snapshot_dir": os.path.join(
                        tmp, "empty")}}, "criteria"),
                    ("the criteria snapshot, unreadable", {"cfg_over": {"snapshot_dir": os.path.dirname(
                        put("bad-snaps/TOD-13.json", "{"))}}, "criteria"),
                    ("the diff", {"answers": {"DIFF repos/acme/app/pulls/12": Exception("HTTP 502")}},
                     "diff")):
                got = facts_with(**kw)
                check("unread: %s is None, never clear, empty or zero" % label, value(got, fact), None)
                check("unread: %s is said, by name" % label, bool(got["unread"].get(fact)), True)
                if fact in got["limits"]:
                    check("unread: %s holds the limits with its own reason" % label,
                          UNKNOWN[fact] in limits.limits(got["item"], SETTINGS, got["limits"])["reasons"],
                          True)
            got = facts_with(answers={"repos/acme/app/contents/delivery.json?ref=main": Exception("HTTP 404")})
            check("no delivery.json: no risk paths named, and no send-back budget, so the bounces are unread",
                  (got["limits"]["risk_paths"], got["limits"]["bounces"], bool(got["unread"].get("bounces"))),
                  ([], None, True))
            put("watcher/hold.json", {"schema": "pipeline-hold/1", "set_at": "2026-11-18T14:00:00Z",
                                      "holds": [{"monitor": "labels", "what": "protected-label",
                                                 "ref": "acme/app#3", "since": "2026-11-18T14:00:00Z"}]})
            check("a hold that stands is read as held", facts_with()["limits"]["hold"], "held")
            os.remove(hold_file)

            # The last piece, and its acceptance at this head.
            last = {"TOD-13": issue("TOD-13", labels=(), parent=plan_node(
                ("TOD-13", "started"), ("TOD-14", "completed"), ("TOD-15", "canceled")))}
            check("the plan's last piece with no acceptance recorded is `none`, never passed",
                  facts_with(tickets_over=last)["evidence"], {"last_piece": True, "acceptance": "none"})
            put("acceptance/ledger.json", {"schema": acceptance_poller.LEDGER_SCHEMA, "rows": [
                {"plan": "TOD-10", "head": HEAD, "status": "started", "at": "2026-11-18T12:00:00Z",
                 "verdict": {"word": "passed", "status": "written", "at": "2026-11-18T13:00:00Z"}}]})
            check("...a check that passed at this head is `passed`",
                  facts_with(tickets_over=last)["evidence"]["acceptance"], "passed")
            put("acceptance/ledger.json", "{ not a ledger")
            got = facts_with(tickets_over=last)
            check("...and an acceptance ledger it cannot read is unread, never `none`",
                  (got["evidence"]["acceptance"], bool(got["unread"].get("acceptance"))), (None, True))
            got = facts_with(tickets_over={"TOD-13": issue("TOD-13", labels=(), parent=plan_node(
                ("TOD-13", "started"), more=True))})
            check("a plan whose pieces could not all be read leaves the last piece unread",
                  (got["evidence"]["last_piece"], bool(got["unread"].get("last_piece"))), (None, True))
            got = facts_with(tickets_over={"TOD-13": issue("TOD-13")})
            check("a ticket in no plan, filed by the owner, is his own idea and no plan's last piece",
                  (got["limits"]["origin"], got["plan"], got["idea"], got["evidence"]["last_piece"]),
                  ("owner", None, "TOD-13", False))

            # A piece, before it starts.
            piece_ticket = {"TOD-13": issue("TOD-13", labels=(), parent=PLAN, description=(
                "## Acceptance criteria\n\n- [ ] It works.\n\nTouch `src/a.py`."))}
            piece = facts_with(tickets_over=piece_ticket, piece=True)
            check("a piece with no order() result given has its queue fact unread, and the limits hold",
                  (piece["limits"]["queue"], bool(piece["unread"].get("queue")),
                   "queue-unknown" in limits.limits(piece["item"], SETTINGS, piece["limits"])["reasons"]),
                  (None, True, True))
            tickets.clear()
            tickets.update(base_tickets)
            tickets.update(piece_ticket)
            over = {"start": [], "held": [{"ticket": "TOD-13", "reason": "over-load", "waits_on": []}],
                    "stops": [], "needs_ok": []}
            got = for_piece(CFG, "TOD-13", NOW, run=host(), queue_result=over)
            check("a piece the queue's own order held over the day's load is read as over-load, "
                  "and the limits say so",
                  (got["limits"]["queue"], "over-load" in limits.limits(
                      got["item"], SETTINGS, got["limits"])["reasons"]),
                  ({"stops": [], "held": "over-load"}, True))
            fits = {"start": [{"ticket": "TOD-13"}], "held": [], "stops": [], "needs_ok": []}
            check("a piece the queue's order would start is read as fitting the day",
                  for_piece(CFG, "TOD-13", NOW, run=host(), queue_result=fits)["limits"]["queue"],
                  {"stops": [], "held": None})
            got = for_piece(CFG, "TOD-13", NOW, run=host(), queue_result=dict(fits, start=[]))
            check("an order() result that does not name the piece leaves its queue fact unread",
                  (got["limits"]["queue"], bool(got["unread"].get("queue"))), (None, True))
            piece = facts_with(tickets_over=piece_ticket, piece=True)
            check("a piece: its origin, the paths its ticket names, its repository's riskPaths, its criteria",
                  (piece["limits"]["origin"], piece["limits"]["paths"], piece["limits"]["risk_paths"],
                   bool(piece["brief"]["criteria"]), piece["item"]),
                  ("owner", ["src/a.py"], ["infra/**"], True, {"kind": "piece", "ref": "TOD-13", "head": None}))
            check("...its brief is its plan's criteria and its own text, for the ticket brief (KIT-543)",
                  ("The pieces work together." in piece["brief"]["criteria"],
                   "It works." in piece["brief"]["criteria"], piece["brief"]["ticket"],
                   piece["brief"]["diff"]),
                  (True, False, "## Acceptance criteria\n\n- [ ] It works.\n\nTouch `src/a.py`.", None))
            check("...and what a piece has not got yet is said, never zero",
                  sorted(piece["unread"]), ["acceptance", "bounces", "diff", "last_piece", "pr", "queue"])
            got = facts_with(tickets_over=piece_ticket, piece=True, cfg_over={
                "queue_config": put("queue/no-repo.json", {
                    "pool": {"owner_user_ids": [OWNER], "dispatcher_user_ids": [DISPATCHER],
                             "team_keys": ["TOD"]},
                    "reader": {"findings_file": findings}})})
            check("...a piece whose repository the queue cannot name has its riskPaths unread",
                  (got["limits"]["risk_paths"], bool(got["unread"].get("risk_paths"))), (None, True))

            # A plan, before it is approved (KIT-544): its idea through its own receipt, every piece's
            # text and the paths each names, its state, and its newest change.
            def plan_doc(kids, state="backlog", labels=(pool.GATE_EPIC_LABEL,), more=False, **over):
                node = {"id": "id-TOD-10", "identifier": "TOD-10", "title": "The  parser",
                        "description": "Plan id: TOD-5/abcdef\n\nBuild the parser in `src/parse.py`.",
                        "updatedAt": "2026-11-18T10:00:00.000Z", "creator": {"id": OWNER},
                        "team": {"key": "TOD"}, "labels": {"nodes": [{"name": n} for n in labels]},
                        "parent": None, "state": {"type": state},
                        "children": {"nodes": [{"id": "id-" + i, "identifier": i, "title": "Do " + i,
                                                "description": d, "updatedAt": at}
                                               for i, d, at in kids],
                                     "pageInfo": {"hasNextPage": more}}}
                node.update(over)
                return node
            KIDS = (("TOD-11", "Write `src/parse.py`.", "2026-11-18T11:00:00.000Z"),
                    ("TOD-12", None, "2026-11-18T09:00:00.000Z"))
            IDEA = dict(issue("TOD-4", description="Parse the logs, and nothing more."),
                        title="Log  parser")

            def plan_with(node=None, idea=None, cfg_over=None):
                tickets.clear()
                tickets.update(base_tickets)
                tickets.update({"TOD-10": plan_doc(KIDS) if node is None else node,
                                "TOD-4": IDEA if idea is None else idea})
                return for_plan(dict(CFG, **(cfg_over or {})), "TOD-10", NOW, run=host())
            plan = plan_with()
            check("a plan: every fact it can have is read, and what it has not got yet is said",
                  sorted(plan["unread"]), ["acceptance", "bounces", "diff", "last_piece", "pr"])
            check("a plan: its idea through its own receipt, the paths it and its pieces name, its "
                  "repository's riskPaths, its state and its newest change",
                  (plan["item"], plan["limits"]["origin"], (plan["ticket"], plan["plan"], plan["idea"]),
                   plan["limits"]["paths"], plan["limits"]["risk_paths"], plan["state"],
                   plan["last_change"]),
                  ({"kind": "plan", "ref": "TOD-10", "head": None}, "owner", ("TOD-10", "TOD-10", "TOD-4"),
                   ["src/parse.py"], ["infra/**"], "backlog", pool.epoch("2026-11-18T11:00:00Z")))
            check("...its brief: its idea's own text, and the plan with each piece as written",
                  (plan["brief"]["criteria"], plan["brief"]["ticket"], plan["brief"]["diff"]),
                  ("Idea TOD-4: Log parser\n\nParse the logs, and nothing more.\n",
                   "Plan TOD-10: The parser\n\nPlan id: TOD-5/abcdef\n\nBuild the parser in "
                   "`src/parse.py`.\n\nPiece TOD-11: Do TOD-11 (1 of 2)\n\nWrite `src/parse.py`.\n\n"
                   "Piece TOD-12: Do TOD-12 (2 of 2)\n", None))
            check("...and limits() reads every fact of it as known",
                  [r for r in limits.limits(plan["item"], SETTINGS, plan["limits"])["reasons"]
                   if r.endswith("-unknown")], [])
            for label, kw, facts_ in (
                    ("an idea the owner did not file", {"idea": dict(IDEA, creator={"id": DISPATCHER})},
                     ("origin", "criteria")),
                    ("a plan the idea gate did not file", {"node": plan_doc(KIDS, labels=())},
                     ("origin", "criteria")),
                    ("a plan whose receipt names no planning run", {"node": plan_doc(
                        KIDS, description="No receipt.")}, ("origin", "criteria")),
                    ("pieces that run past one page", {"node": plan_doc(KIDS, more=True)},
                     ("paths", "ticket")),
                    ("a piece's text that is not text", {"node": plan_doc(
                        (("TOD-11", ["x"], "2026-11-18T11:00:00.000Z"),))}, ("paths", "ticket")),
                    ("an idea with no text", {"idea": dict(IDEA, title="", description=None)},
                     ("criteria",)),
                    ("the plan, not given by the tracker", {"node": stage_e.BounceError("down")},
                     ("origin", "paths", "risk_paths", "criteria", "ticket"))):
                got = plan_with(**kw)
                reasons = limits.limits(got["item"], SETTINGS, got["limits"])["reasons"]
                check("a plan, unread: %s is said by name, None in its place, and a limit's own "
                      "reason holds it" % label,
                      [f for f in facts_ if not got["unread"].get(f) or value(got, f) is not None
                       or (f in UNKNOWN and UNKNOWN[f] not in reasons)], [])
            got = plan_with(node=plan_doc((("TOD-11", "x", None),)))
            check("a plan with a piece whose last change is not dated has no newest change",
                  (got["last_change"], got["brief"]["ticket"] is not None), (None, True))
            check("a plan someone approved, started or closed says its own state",
                  [plan_with(node=plan_doc(KIDS, state=s))["state"] for s in ("unstarted", "canceled")],
                  ["unstarted", "canceled"])
            check("the other kinds carry no plan state and no newest change",
                  [(d["state"], d["last_change"]) for d in (facts_with(), facts_with(
                      tickets_over=piece_ticket, piece=True))], [(None, None), (None, None)])
            check("a plan id that is not one reads nothing",
                  sorted(for_plan(CFG, "not a ticket", NOW)["unread"]), sorted(FACTS))
            check("an id that is not one reads nothing", sorted(for_piece(CFG, "not a ticket", NOW)["unread"]),
                  sorted(FACTS))
            check("a pull request that is not one reads nothing",
                  sorted(for_merge(CFG, "not a repo", PR, NOW, run=host())["unread"]), sorted(FACTS))
        finally:
            stage_e.linear_graphql = saved_graphql

        check("a config naming no tracker key reads no ticket, and says so",
              bool(for_merge(dict(CFG, linear_key_env=""), REPO, PR, NOW, run=host())["unread"].get("ticket")),
              True)
        for label, raw in (("a key value", {"linear_key_env": "lin_api_" + "x" * 20}),
                           ("a relative path", {"hold_file": "hold.json"}),
                           ("a team key in lower case", {"team_keys": ["tod"]}),
                           ("an unknown setting", {"merge": True}),
                           ("a watcher age of 0", {"watcher_max_age": 0}),
                           ("a config that is not an object", None)):
            check("config: %s is refused" % label, bool(config(raw)[1]), True)

    # It reads, and does nothing else.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    body = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("os.replace", "os.remove", "os.unlink", "os.rename", ".write(", "\"w\"", "'w'",
              "Popen", "os.system", "urlopen", "mutation", "\"-X\"", "--method", "--input")
    check("it writes nothing, starts nothing but gh, and sends no change anywhere",
          [w for w in banned if w in body], [])

    if failures:
        for line in failures:
            print(line)
        print("validator facts selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator facts selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
