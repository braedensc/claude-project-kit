#!/usr/bin/env python3
"""The validator's hard limits: layer 1, plain code with no model (KIT-538).
Design: docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md, "Layer 1".

    from pipeline_validator_limits import limits
    limits(item, settings, facts)   THE ONE FUNCTION: pass, or every reason not, for one item
    python3 scripts/pipeline_validator_limits.py --selftest

WHAT IT ANSWERS.  {"verdict": "pass"|"fail", "kind", "ref", "head", "reasons"} for one item: a plan
(`TEAM-12`), a piece starting (`TEAM-13`), or a pull request merging at one head (`owner/name#N`).
The reasons are closed words from REASONS, in that order. No text from a ticket or a pull request
is ever in the answer.

WHAT IT IS GIVEN.  `settings` is what the validator's settings `load()` returned (contract §23):
anything but a file that was read has every switch off. `facts` holds what was read for the item,
None where it could not be read. Every rule says `fail` when it cannot tell. The readers below
fill each fact by calling the part of the kit that owns it; nothing is counted or judged twice.
  origin      `owner`, `agent-first` or `agent-later`: who filed the idea behind the item.
              `owner` only when the owner filed it himself: idea_origin() reads that from the
              ticket (a plan's idea, a piece or its plan's idea) by the queue pool's own rule for a
              ticket he filed. A move into Todo, or a plan's approval, is not a filing.
              origin_from_pool() and origin_from_start() certify the generation of agent-filed
              work, and a ticket the pool admitted from his own backlog, from the pool's own verdict
              or the queue's record of a start.
  paths       a plan's or piece's: the paths its tickets name (named_paths()); a merge's: every
              file its diff changes.
  risk_paths  the project's own `autonomy.riskPaths`, from delivery.json on the default branch.
  pr          a merge's: {"head", "additions", "deletions"}, as GitHub reports it now.
  window      whether the working window is open: window_open().
  queue       {"stops": [...], "held": a reason or None}: queue_fact() over the queue's own result.
  bounces     a merge's: {"spent", "budget", "exhausted"}: bounce_fact(), from Stage E's ledger.
  watcher     `fresh` or `stale`: watcher_state() of the watcher's status file (contract §19).
  hold        the hold file's state (contract §20): hold_state().

THE RULES, each one tested.
  * The switch. That kind of approval is on in the settings.
  * Provenance. A plan only from an idea the owner filed himself: an idea an agent filed, never,
    even one he moved. A piece, and the piece a pull request belongs to, only from an idea he
    filed, or agent-filed work in its first generation.
  * Size and files. A merge: lines added plus removed within `max_changed_lines`
    (check_auto_merge.gate_size), and at most `max_files` files. Every item: no path on the
    safety-system floor (check_grader_paths.FLOOR, with the auto-approve gate's own) or in the
    project's `riskPaths` (check_auto_merge.gate_risk_paths), whatever is switched on; and none
    in a human-only class the owner has not allowed (HUMAN_ONLY).
  * Budget and window. The working window is open (scripts/pipeline_window.py), the queue names
    no runaway stop, and a piece is not held over the day's planned load (`over-load`,
    scripts/pipeline_queue_order.py).
  * The send-back budget. Stage E's own count, from its ledger (scripts/pipeline_bounce_local.py),
    never kept a second time. A merge whose bounces Stage E exhausted, or spent past
    `budgets.maxBounces`, is the owner's.
  * The watcher. Alive: its status file written within `max_age` seconds. And no hold: a hold, a
    hold that cannot be read, and no hold file named are each a reason.
  * One verdict per head. A merge is judged at the head asked about. A pull request whose head is
    now another commit fails (`head-moved`), so a push revokes a pass.

HUMAN-ONLY CLASSES BY PATH.  Each class the owner may allow is matched by path, lower-cased, with
the auto-approve gate's own glob rules, and wide on purpose: a path that only looks like a class
holds, and the owner decides. Deleting data is not provable by path: migrations and SQL are what a
path can show, and the agent reads the diff. A project's own paths for a class go in its
`riskPaths`, where they stay the owner's whatever is allowed.

WHAT IT NEVER DOES (asserted in --selftest): write anything, start a process, open a socket, or
put text from a ticket or a pull request in its answer.
"""
import argparse
import calendar
import json
import os
import re
import stat
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import check_auto_approve as auto_approve
    import check_auto_merge as auto_merge
    import check_grader_paths as graders
    import pipeline_bounce_local as bounce
    import pipeline_hold as hold
    import pipeline_queue_order as queue_order
    import pipeline_queue_pool as pool
    import pipeline_validator_settings as vsettings
    import pipeline_window as window
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The limits call those files' own rules and keep no copy." % exc)

KINDS = ("plan", "piece", "merge")
SWITCH = {"plan": "plans", "piece": "pieces", "merge": "merges"}
ORIGINS = ("owner", "agent-first", "agent-later")
FILED_SOURCE = "backlog"     # the pool's source for a ticket the owner filed himself, and only that
AGENT_SOURCE = "agent_filed"
REASONS = (
    "kind-unknown", "ref-unknown", "switch-off",
    "provenance-unknown", "provenance-not-owner", "provenance-later-generation",
    "head-moved", "size-unknown", "too-many-lines", "too-many-files",
    "paths-unknown", "risk-paths-unknown", "risk-path",
    "credentials", "billing-and-accounts", "production-deploy", "deleting-data",
    "window-unknown", "window-closed", "queue-unknown", "runaway-stop", "over-load",
    "bounces-unknown", "bounce-budget-spent",
    "watcher-unknown", "watcher-stale", "hold-unknown", "held")
TICKET_RE = re.compile(r"[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}")
PR_REF_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}#[0-9]{1,9}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
WATCHER_SCHEMA = "pipeline-watcher/1"
DEFAULT_WATCHER_MAX_AGE = 1800                   # twice the watcher's default pass of 900 seconds
WATCHER_MAX_BYTES = 8 * 1024 * 1024
# The four human-only classes the owner may allow (contract §23's `allow`), by path: (the
# settings' key, the reason, globs). Each glob in a class appears in both a file and a folder form.
HUMAN_ONLY = (
    ("credentials", "credentials", (
        "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/*.p12", "**/*.pfx", "**/*.jks",
        "**/id_rsa*", "**/id_ed25519*", "**/id_ecdsa*", "**/*credential*", "**/*credential*/**",
        "**/*secret*", "**/*secret*/**", "**/.npmrc", "**/.pypirc", "**/.netrc", "**/.aws/**",
        "**/.ssh/**")),
    ("billing_and_accounts", "billing-and-accounts", (
        "**/*billing*", "**/*billing*/**", "**/*payment*", "**/*payment*/**", "**/*stripe*",
        "**/*stripe*/**", "**/*subscription*", "**/*subscription*/**", "**/*invoice*",
        "**/*invoice*/**", "**/*account*", "**/*account*/**")),
    ("production_deploy", "production-deploy", (
        "**/*deploy*", "**/*deploy*/**", "**/dockerfile*", "**/docker-compose*", "**/fly.toml",
        "**/render.yaml", "**/vercel.json", "**/netlify.toml", "**/procfile", "**/app.yaml",
        "**/k8s/**", "**/helm/**", "**/terraform/**", "**/*.tf", "**/*.tfvars")),
    ("deleting_data", "deleting-data", (
        "**/migrations/**", "**/migrate/**", "**/*migration*", "**/*migration*/**",
        "**/alembic/**", "**/*.sql")),
)
_CLASSES = tuple((key, reason, tuple(re.compile(auto_approve.glob_to_re(g)) for g in globs))
                 for key, reason, globs in HUMAN_ONLY)


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _norm(path):
    """A repo-relative path as the globs read one: no leading `./` or `/`."""
    path = path.strip()
    while path.startswith("./") or path.startswith("/"):
        path = path[2:] if path.startswith("./") else path[1:]
    return path


def _epoch(text):
    if not isinstance(text, str) or not TIME_RE.fullmatch(text):
        return None
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _off():
    return vsettings.off(vsettings.ABSENT, "the settings were not read")


def _settings(settings):
    """The settings as `load()` gives them when they were read and are whole; every switch off for
    anything else, a read result with a section missing or of another shape included."""
    if not isinstance(settings, dict) or settings.get("state") != vsettings.READ:
        return _off()
    try:
        whole = (all(isinstance(settings["approve"][k], bool) for k in vsettings.APPROVE)
                 and all(_whole(settings["limits"][k]) for k, _top in vsettings.LIMITS)
                 and all(isinstance(settings["allow"][k], bool) for k in vsettings.ALLOW))
    except (KeyError, TypeError):
        whole = False
    return settings if whole else _off()


# --------------------------------------------------------------------------- #
# The one function
# --------------------------------------------------------------------------- #
def limits(item, settings, facts):
    """Layer 1's answer for one item. See the module's docstring for what goes in and comes out."""
    item = item if isinstance(item, dict) else {}
    kind = item.get("kind")
    if kind not in KINDS:
        return {"verdict": "fail", "kind": None, "ref": None, "head": None,
                "reasons": ["kind-unknown"]}
    facts = facts if isinstance(facts, dict) else {}
    s = _settings(settings)
    reasons = []
    ref, head = item.get("ref"), item.get("head") if kind == "merge" else None
    ref_re = PR_REF_RE if kind == "merge" else TICKET_RE
    if not isinstance(ref, str) or not ref_re.fullmatch(ref):
        ref = None
        reasons.append("ref-unknown")
    if s["approve"].get(SWITCH[kind]) is not True:
        reasons.append("switch-off")

    origin = facts.get("origin")
    if origin not in ORIGINS:
        reasons.append("provenance-unknown")
    elif kind == "plan" and origin != "owner":
        reasons.append("provenance-not-owner")
    elif origin == "agent-later":
        reasons.append("provenance-later-generation")

    paths = facts.get("paths")
    if (not isinstance(paths, list) or not all(isinstance(p, str) and p.strip() for p in paths)
            or (kind == "merge" and not paths)):
        reasons.append("paths-unknown")
        paths = None
    else:
        paths = sorted(set(_norm(p) for p in paths))

    if kind == "merge":
        if not isinstance(head, str) or not SHA_RE.fullmatch(head):
            head = None
        pr = facts.get("pr") if isinstance(facts.get("pr"), dict) else {}
        if head is None or pr.get("head") != head:
            reasons.append("head-moved")
        added, removed = pr.get("additions"), pr.get("deletions")
        if not _whole(added) or not _whole(removed):
            reasons.append("size-unknown")
        elif not auto_merge.gate_size(added, removed, s["limits"]["max_changed_lines"],
                                      auto_merge.Verdict()):
            reasons.append("too-many-lines")
        if paths is not None and len(paths) > s["limits"]["max_files"]:
            reasons.append("too-many-files")

    risk = facts.get("risk_paths")
    if not isinstance(risk, list) or not all(isinstance(g, str) for g in risk):
        reasons.append("risk-paths-unknown")
    elif paths is not None and not auto_merge.gate_risk_paths(
            paths, None, {"autonomy": {"riskPaths": list(graders.FLOOR) + list(risk)}},
            auto_merge.Verdict()):
        reasons.append("risk-path")
    if paths is not None:
        lowered = [p.lower() for p in paths]
        for key, reason, rxs in _CLASSES:
            if (s["allow"].get(key) is not True
                    and any(auto_approve.implicates(p, rx) for p in lowered for rx in rxs)):
                reasons.append(reason)

    open_now = facts.get("window")
    if not isinstance(open_now, bool):
        reasons.append("window-unknown")
    elif not open_now:
        reasons.append("window-closed")
    queue = facts.get("queue")
    if not isinstance(queue, dict) or not isinstance(queue.get("stops"), list):
        reasons.append("queue-unknown")
    else:
        if queue["stops"]:
            reasons.append("runaway-stop")
        if kind == "piece" and queue.get("held") == "over-load":
            reasons.append("over-load")

    if kind == "merge":
        b = facts.get("bounces")
        if (not isinstance(b, dict) or not _whole(b.get("spent")) or not _whole(b.get("budget"))
                or not isinstance(b.get("exhausted"), bool)):
            reasons.append("bounces-unknown")
        elif b["exhausted"] or b["spent"] > b["budget"]:
            reasons.append("bounce-budget-spent")

    watcher = facts.get("watcher")
    if watcher not in ("fresh", "stale"):
        reasons.append("watcher-unknown")
    elif watcher == "stale":
        reasons.append("watcher-stale")
    held = facts.get("hold")
    if held == hold.HELD:
        reasons.append("held")
    elif held != hold.NONE:
        reasons.append("hold-unknown")

    reasons = [r for r in REASONS if r in set(reasons)]
    return {"verdict": "fail" if reasons else "pass", "kind": kind, "ref": ref, "head": head,
            "reasons": reasons}


# --------------------------------------------------------------------------- #
# The readers: each fact from the part of the kit that owns it
# --------------------------------------------------------------------------- #
def idea_origin(pool_config, ticket, findings):
    """`owner` when the owner filed `ticket` himself, by the queue pool's own rule for a ticket he
    filed (its backlog rule): its creator is the owner (pipeline_queue_pool.is_owner, never the
    dispatcher's user), it carries his `provenance:human` mark, it has no parent, and the finding
    record does not list it as a session's finding. For a plan, `ticket` is its idea; for a piece,
    the piece's own ticket or its plan's idea. None for anything else: a ticket he only moved or
    approved, an agent's, or a config or finding record that could not be read certifies nothing.
    `pool_config` is the pool's own config; `ticket` is the tracker's issue (`id`, `identifier`,
    `creator{id}`, `labels{nodes{name}}`, `parent{id}`); `findings` is the finding poller's record."""
    cfg, bad = pool.config(pool_config)
    if bad or not isinstance(ticket, dict) or not isinstance(findings, dict):
        return None
    ident, uuid = pool.dig(ticket, "identifier"), pool.dig(ticket, "id")
    if not isinstance(ident, str) or not TICKET_RE.fullmatch(ident):
        return None
    for rec in findings.values():
        if isinstance(rec, dict) and (rec.get("filed") == ident or (
                isinstance(uuid, str) and rec.get("filed_uuid") == uuid)):
            return None
    if (pool.is_owner(cfg, pool.dig(ticket, "creator", "id"))
            and pool.HUMAN_LABEL in pool.label_names(ticket)
            and not pool.dig(ticket, "parent", "id")):
        return "owner"
    return None


def origin_from_pool(verdict):
    """What the queue pool's own verdict on a ticket proves about who filed it: `owner` when it
    admitted the ticket from the owner's backlog, which it does only for a ticket he filed;
    `agent-first` when it admitted agent-filed work, which it does only in its first generation;
    `agent-later` when it held agent-filed work for its generation. A move into Todo, or a plan's
    approval, says who moved or approved, not who filed: those, and every held verdict, are None."""
    if not isinstance(verdict, dict):
        return None
    source = verdict.get("source")
    if source == AGENT_SOURCE and verdict.get("reason") == "generation":
        return "agent-later"
    if verdict.get("admitted") is not True or verdict.get("reason") != "admitted":
        return None
    return "owner" if source == FILED_SOURCE else "agent-first" if source == AGENT_SOURCE else None


def origin_from_start(row):
    """The same, from the queue's record of a start ({ticket, source, at}): `owner` for a start
    from the owner's backlog, `agent-first` for an agent-filed one. A start from Todo or from a plan
    says nothing about who filed the idea: None, and its caller reads it with idea_origin()."""
    source = row.get("source") if isinstance(row, dict) else None
    return "owner" if source == FILED_SOURCE else "agent-first" if source == AGENT_SOURCE else None


def named_paths(descriptions):
    """The repo paths a plan's or a piece's tickets name, from their inline code spans, read by the
    auto-approve gate's own reader (check_auto_approve.candidate_paths). None when one is not
    text. The paths are matched against globs only; they never reach an answer."""
    if not isinstance(descriptions, (list, tuple)):
        return None
    out = []
    for d in descriptions:
        if not isinstance(d, str):
            return None
        out += auto_approve.candidate_paths(d)
    return sorted(set(out))


def window_open(window_config, now):
    """Whether the working window is open at `now` (scripts/pipeline_window.py): True or False, or
    None when its config is refused. No config is the kit's default window."""
    cfg, errors = window.config(window_config if window_config is not None else {})
    if errors or cfg is None:
        return None
    try:
        return window.window_at(cfg, now)["open"] is True
    except (ValueError, TypeError, KeyError):
        return None


def queue_fact(result, ticket=None):
    """{"stops": [...], "held": a reason or None} for one ticket, from the queue's own `order()`
    result for this pass (scripts/pipeline_queue_order.py), or None when it is not one. Every stop
    counts, and one the queue does not name is still a stop."""
    if (not isinstance(result, dict) or not isinstance(result.get("stops"), list)
            or not isinstance(result.get("held"), list)):
        return None
    stops = [s.get("stop") if isinstance(s, dict) and s.get("stop") in queue_order.STOPS
             else "unknown" for s in result["stops"]]
    held = None
    for h in result["held"]:
        if isinstance(h, dict) and ticket is not None and h.get("ticket") == ticket:
            held = h.get("reason") if h.get("reason") in queue_order.HELD else "malformed"
    return {"stops": stops, "held": held}


def bounce_fact(ledger_path, owner_repo, pr_number, budget):
    """{"spent", "budget", "exhausted"} for one pull request, from Stage E's own ledger through its
    own reader (pipeline_bounce_local.ledger_view): never counted here. `budget` is
    `budgets.maxBounces` from delivery.json on the default branch. None when the ledger cannot be
    read whole, a row in it is not one, or the budget is not a whole number. A ledger not there
    yet is nothing spent, as Stage E reads it."""
    if (not _whole(budget) or not isinstance(owner_repo, str) or not PR_REF_RE.fullmatch(
            "%s#1" % owner_repo) or not _whole(pr_number) or not isinstance(ledger_path, str)):
        return None
    try:
        view = bounce.ledger_view(ledger_path, owner_repo, pr_number)
    except (bounce.BounceError, OSError, TypeError, ValueError):
        return None
    return {"spent": view["prior"], "budget": budget, "exhausted": view["exhausted"] is not None}


def hold_state(path):
    """The hold file's state, as pipeline_hold.read() gives it: `none`, `held`, `malformed` or
    `unreadable`. None when no hold file is named, or it is named inside a git working tree: then
    nothing can say there is no hold."""
    if not isinstance(path, str) or not os.path.isabs(path):
        return None
    if hold.inside_git_worktree(os.path.dirname(path)):
        return None
    return hold.read(path)["state"]


def watcher_state(path, now, max_age=DEFAULT_WATCHER_MAX_AGE):
    """`fresh` when the watcher's status file (contract §19) was written within `max_age` seconds
    of `now`, `stale` when longer ago or dated ahead of `now`. None when it cannot be read and
    trusted: no path named, a link, not a plain file, inside a git working tree, writable by group
    or other, not a pipeline-watcher/1 document, or a time it cannot read."""
    if (not isinstance(path, str) or not os.path.isabs(path) or not _whole(max_age)
            or max_age == 0 or isinstance(now, bool) or not isinstance(now, (int, float))):
        return None
    try:
        if os.path.islink(path) or hold.inside_git_worktree(os.path.dirname(path)):
            return None
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return None
        with open(path, encoding="utf-8") as fh:
            raw = fh.read(WATCHER_MAX_BYTES + 1)
    except (OSError, UnicodeDecodeError):
        return None
    if len(raw) > WATCHER_MAX_BYTES:
        return None
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != WATCHER_SCHEMA:
        return None
    at = _epoch(doc.get("written_at"))
    if at is None:
        return None
    return "fresh" if 0 <= now - at <= max_age else "stale"


def main(argv=None):
    ap = argparse.ArgumentParser(description="The validator's hard limits (layer 1).")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "validator-limits-selftest-")
    ap.print_help()
    return 2


# ======================================================================================
# SELFTEST: nothing below the line above is run by a reader, and the source scan stops there.
# ======================================================================================
def selftest():
    import copy
    import tempfile
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    H, OLD = "a" * 40, "b" * 40
    ALL_ON = {"state": vsettings.READ,
              "approve": {"plans": True, "pieces": True, "merges": True},
              "limits": {"max_changed_lines": 400, "max_files": 20},
              "allow": {"credentials": False, "billing_and_accounts": False,
                        "production_deploy": False, "deleting_data": False},
              "why": "read"}
    BASE = {"origin": "owner", "paths": ["src/app/views.py"], "risk_paths": ["db/**"],
            "window": True, "queue": {"stops": [], "held": None}, "watcher": "fresh",
            "hold": hold.NONE}
    MERGE = dict(BASE, pr={"head": H, "additions": 120, "deletions": 30},
                 bounces={"spent": 1, "budget": 2, "exhausted": False})
    ITEMS = {"plan": {"kind": "plan", "ref": "ENG-10"}, "piece": {"kind": "piece", "ref": "ENG-11"},
             "merge": {"kind": "merge", "ref": "acme/app#12", "head": H}}
    GOOD = {"plan": BASE, "piece": BASE, "merge": MERGE}
    seen = set()

    def run(kind, settings=None, item=None, **over):
        facts = copy.deepcopy(GOOD[kind])
        facts.update(over)
        got = limits(dict(ITEMS[kind], **(item or {})),
                     ALL_ON if settings is None else settings, facts)
        seen.update(got["reasons"])
        return got

    def why(kind, **kw):
        return run(kind, **kw)["reasons"]

    def with_settings(section, key, value):
        s = copy.deepcopy(ALL_ON)
        s[section][key] = value
        return s

    # 0. Good facts pass, for each kind, and the answer is closed words and ids only.
    for kind in KINDS:
        check("%s: good facts pass" % kind, run(kind),
              {"verdict": "pass", "kind": kind, "ref": ITEMS[kind]["ref"],
               "head": H if kind == "merge" else None, "reasons": []})
    check("an item that is not one of the three kinds fails, alone",
          limits({"kind": "deploy", "ref": "ENG-1"}, ALL_ON, BASE)["reasons"], ["kind-unknown"])
    check("no item at all fails", limits(None, ALL_ON, BASE)["reasons"], ["kind-unknown"])
    seen.add("kind-unknown")
    for kind, bad in (("plan", "acme/app#12"), ("piece", "eng-11"), ("merge", "ENG-12"),
                      ("merge", "acme/app#12 and more")):
        check("%s: a ref of another shape is not one" % kind,
              (why(kind, item={"ref": bad}), run(kind, item={"ref": bad})["ref"]),
              (["ref-unknown"], None))

    # 1. The switch: the kind's own, and settings that were not read are every switch off.
    for kind in KINDS:
        check("%s: its switch off" % kind,
              why(kind, settings=with_settings("approve", SWITCH[kind], False)), ["switch-off"])
        others = copy.deepcopy(ALL_ON)
        others["approve"] = dict((k, k != SWITCH[kind]) for k in others["approve"])
        check("%s: the other switches on do not stand in for its own" % kind,
              why(kind, settings=others), ["switch-off"])
    for label, settings in (("absent", vsettings.off(vsettings.ABSENT, "x")),
                            ("refused", dict(ALL_ON, state=vsettings.UNTRUSTED)),
                            ("not a dict", "all on"), ("none", None)):
        check("settings %s: every switch off" % label,
              limits(ITEMS["piece"], settings, BASE)["reasons"], ["switch-off"])
    check("a switch that is truthy but not true is off",
          why("piece", settings=with_settings("approve", "pieces", 1)), ["switch-off"])
    # Stage E on the first version (low): a read result with a section missing crashed.
    for label, settings in (
            ("read, with no sections", {"state": vsettings.READ}),
            ("read, with the allow section missing",
             dict((k, v) for k, v in ALL_ON.items() if k != "allow")),
            ("read, with a limit that is not a number", with_settings("limits", "max_files", "20")),
            ("read, with a section that is a list", dict(ALL_ON, approve=[True]))):
        check("settings %s: every switch off, never a crash" % label,
              limits(ITEMS["merge"], settings, MERGE)["reasons"],
              ["switch-off", "too-many-lines", "too-many-files"])

    # 2. Provenance: a plan only from the owner's idea; a piece or merge from the owner's own
    #    work or first-generation agent-filed work.
    for kind, origin, want in (
            ("plan", "agent-first", ["provenance-not-owner"]),
            ("plan", "agent-later", ["provenance-not-owner"]),
            ("piece", "agent-first", []), ("merge", "agent-first", []),
            ("piece", "agent-later", ["provenance-later-generation"]),
            ("merge", "agent-later", ["provenance-later-generation"]),
            ("piece", None, ["provenance-unknown"]), ("plan", "the owner", ["provenance-unknown"]),
            ("merge", "", ["provenance-unknown"])):
        check("%s from %r" % (kind, origin), why(kind, origin=origin), want)

    # 3. One verdict per head: a merge is judged at its head, and a push revokes a pass.
    check("merge: the pull request's head is another commit",
          why("merge", pr={"head": OLD, "additions": 1, "deletions": 1}), ["head-moved"])
    check("merge: asked about a head that is not a full commit id",
          (why("merge", item={"head": "abc123"}), run("merge", item={"head": "abc123"})["head"]),
          (["head-moved"], None))
    check("merge: no pull request facts at all",
          why("merge", pr=None), ["head-moved", "size-unknown"])
    check("a plan or a piece carries no head", (run("plan")["head"], run("piece")["head"]),
          (None, None))

    # 4. Size and files, for a merge: the line limit is check_auto_merge's own gate.
    for label, pr, want in (
            ("at the line limit", {"head": H, "additions": 400, "deletions": 0}, []),
            ("one line over", {"head": H, "additions": 300, "deletions": 101}, ["too-many-lines"]),
            ("counts not read", {"head": H, "additions": None, "deletions": 3}, ["size-unknown"]),
            ("a count that is true", {"head": H, "additions": True, "deletions": 3},
             ["size-unknown"]),
            ("a negative count", {"head": H, "additions": -1, "deletions": 3}, ["size-unknown"])):
        check("merge: %s" % label, why("merge", pr=pr), want)
    check("merge: a line limit of 0 passes nothing",
          why("merge", settings=with_settings("limits", "max_changed_lines", 0)),
          ["too-many-lines"])
    many = ["src/f%d.py" % n for n in range(21)]
    check("merge: one file over the limit", why("merge", paths=many), ["too-many-files"])
    check("merge: at the file limit", why("merge", paths=many[:20]), [])
    check("merge: a file limit of 0 passes nothing",
          why("merge", settings=with_settings("limits", "max_files", 0)), ["too-many-files"])
    check("merge: the same file listed twice counts once",
          why("merge", paths=many[:20] + many[:5]), [])
    check("a plan or a piece is not held to a merge's size",
          (why("plan", settings=with_settings("limits", "max_files", 0)),
           why("piece", settings=with_settings("limits", "max_changed_lines", 0))), ([], []))

    # 5. Files on the safety-system floor, or in the project's riskPaths: never a switch.
    for label, path in (("a hook", ".claude/hooks/pre-tool-use.py"),
                        ("a workflow", ".github/workflows/ci.yml"),
                        ("a pipeline script", "scripts/pipeline_queue_pool.py"),
                        ("a gate", "scripts/check_auto_merge.py"),
                        ("only on the grader floor", "scripts/gh_fallback.py"),
                        ("only on the auto-approve floor", ".claude/skills/ship/SKILL.md"),
                        ("delivery.json", "delivery.json"),
                        ("written with ./ in front", "./.claude/settings.json"),
                        ("the project's own riskPath", "db/schema.rb")):
        for kind in KINDS:
            got = why(kind, paths=["src/ok.py", path])
            check("%s: %s is the owner's" % (kind, label), got, ["risk-path"])
    every_on = copy.deepcopy(ALL_ON)
    every_on["allow"] = dict((k, True) for k in every_on["allow"])
    check("the floor holds with every human-only class allowed",
          why("merge", settings=every_on, paths=[".claude/hooks/x.py"]), ["risk-path"])
    check("a riskPaths list that could not be read is a reason",
          why("piece", risk_paths=None), ["risk-paths-unknown"])
    check("...and so is one that is not a list of globs",
          why("piece", risk_paths=["db/**", 7]), ["risk-paths-unknown"])
    for kind in KINDS:
        check("%s: paths not read" % kind, why(kind, paths=None), ["paths-unknown"])
        check("%s: a path that is not text" % kind, why(kind, paths=["src/a.py", 3]),
              ["paths-unknown"])
    check("merge: a pull request that names no file is not read",
          why("merge", paths=[]), ["paths-unknown"])
    check("a plan or a piece that names no path is fine",
          (why("plan", paths=[]), why("piece", paths=[])), ([], []))

    # 6. The human-only classes: each is the owner's until he allows it.
    for key, reason, path in (("credentials", "credentials", "config/.env.production"),
                              ("credentials", "credentials", "deploy-keys/server.PEM"),
                              ("billing_and_accounts", "billing-and-accounts",
                               "src/Billing/Invoices.ts"),
                              ("billing_and_accounts", "billing-and-accounts",
                               "app/stripe_webhook.py"),
                              ("production_deploy", "production-deploy", "Dockerfile"),
                              ("production_deploy", "production-deploy", "infra/main.tf"),
                              ("deleting_data", "deleting-data",
                               "app/db/migrations/0042_drop_users.py"),
                              ("deleting_data", "deleting-data", "sql/cleanup.SQL")):
        got = why("merge", paths=[path])
        want = ([reason, "production-deploy"] if "deploy" in path and reason != "production-deploy"
                else [reason])
        check("%s: %s is the owner's while it is off" % (reason, path), got, want)
        allowed = with_settings("allow", key, True)
        check("%s: %s passes once he allows it" % (reason, path),
              [r for r in why("merge", settings=allowed, paths=[path]) if r != "production-deploy"],
              [])
    check("the classes are the settings' own allow switches, in order",
          tuple(key for key, _reason, _globs in HUMAN_ONLY), vsettings.ALLOW)
    check("a piece whose ticket names a class path is the owner's too",
          why("piece", paths=["src/payments/refund.py"]), ["billing-and-accounts"])
    check("a path in two classes gives both reasons, in order",
          why("merge", paths=["deploy/secrets.yaml"]), ["credentials", "production-deploy"])

    # 7. Budget and window.
    for kind in KINDS:
        check("%s: the window closed" % kind, why(kind, window=False), ["window-closed"])
        check("%s: the window not known" % kind, why(kind, window=None), ["window-unknown"])
        check("%s: a runaway stop" % kind,
              why(kind, queue={"stops": ["day-hard-stop"], "held": None}), ["runaway-stop"])
        check("%s: the queue not read" % kind, why(kind, queue=None), ["queue-unknown"])
    check("piece: held over the day's planned load",
          why("piece", queue={"stops": [], "held": "over-load"}), ["over-load"])
    check("a plan or a merge is not held for one piece's load",
          (why("plan", queue={"stops": [], "held": "over-load"}),
           why("merge", queue={"stops": [], "held": "over-load"})), ([], []))
    check("piece: held for a reason that is not the load is the queue's business",
          why("piece", queue={"stops": [], "held": "no-lane"}), [])
    check("a window that is truthy but not true is not known",
          why("piece", window=1), ["window-unknown"])

    # 8. The send-back budget: Stage E's own count, for a merge.
    for label, b, want in (
            ("within the budget", {"spent": 2, "budget": 2, "exhausted": False}, []),
            ("exhausted", {"spent": 2, "budget": 2, "exhausted": True}, ["bounce-budget-spent"]),
            ("spent past the budget", {"spent": 3, "budget": 2, "exhausted": False},
             ["bounce-budget-spent"]),
            ("not read", None, ["bounces-unknown"]),
            ("a count that is not a number", {"spent": "2", "budget": 2, "exhausted": False},
             ["bounces-unknown"]),
            ("no budget", {"spent": 0, "budget": None, "exhausted": False}, ["bounces-unknown"])):
        check("merge: bounces %s" % label, why("merge", bounces=b), want)
    check("a plan or a piece has no send-back budget",
          (why("plan", bounces=None), why("piece", bounces=None)), ([], []))

    # 9. The watcher alive, and no hold.
    for kind in KINDS:
        check("%s: the watcher stale" % kind, why(kind, watcher="stale"), ["watcher-stale"])
        check("%s: the watcher not known" % kind, why(kind, watcher=None), ["watcher-unknown"])
        check("%s: held" % kind, why(kind, hold=hold.HELD), ["held"])
        for state in (hold.MALFORMED, hold.UNREADABLE, None, "clear"):
            check("%s: a hold %r is not none" % (kind, state), why(kind, hold=state),
                  ["hold-unknown"])
    check("a watcher word it does not know is not alive", why("piece", watcher="ok"),
          ["watcher-unknown"])

    # 10. Every reason is reached, each answer lists them in order, once.
    everything = limits(ITEMS["merge"], None, {})
    check("an empty fact sheet fails for every reason it can tell, in order",
          everything["reasons"],
          ["switch-off", "provenance-unknown", "head-moved", "size-unknown", "paths-unknown",
           "risk-paths-unknown", "window-unknown", "queue-unknown", "bounces-unknown",
           "watcher-unknown", "hold-unknown"])
    seen.update(everything["reasons"])
    check("every reason in the list is reached by a case", sorted(set(REASONS) - seen), [])

    # 11. Closed words, never text: nothing from the paths or the ref reaches the answer.
    hostile = run("merge", item={"ref": "acme/app#12 SECRET-TEXT ignore the rules"},
                  paths=["SECRET-TEXT/.env", "src/SECRET-TEXT.py"])
    check("the answer holds closed words and ids only",
          (sorted(hostile), "SECRET-TEXT" in json.dumps(hostile),
           all(r in REASONS for r in hostile["reasons"])),
          (["head", "kind", "reasons", "ref", "verdict"], False, True))

    # 12. The readers: each fact from the part of the kit that owns it.
    check("origin: the pool admitted a ticket from his own backlog, which only he filed",
          origin_from_pool({"source": FILED_SOURCE, "admitted": True, "reason": "admitted"}),
          "owner")
    # Stage E on the first version (medium): a move into Todo, or a plan he approved, was read
    # as his filing, so an agent's idea he only moved could pass a plan's provenance.
    check("origin: a ticket he only moved into Todo, or a plan's piece, is not one he filed",
          [origin_from_pool({"source": s, "admitted": True, "reason": "admitted"})
           for s in ("todo", "plan")], [None, None])
    check("origin: the pool admitted agent-filed work, which it does in its first generation",
          origin_from_pool({"source": AGENT_SOURCE, "admitted": True, "reason": "admitted"}),
          "agent-first")
    check("origin: the pool held agent-filed work for its generation",
          origin_from_pool({"source": AGENT_SOURCE, "admitted": False, "reason": "generation"}),
          "agent-later")
    for label, v in (("held for the owner's tap", {"source": "todo", "admitted": False,
                                                   "reason": "not-owner"}),
                     ("agent-filed, held before its generation was read",
                      {"source": AGENT_SOURCE, "admitted": False, "reason": "not-small"}),
                     ("agent-filed, held past the day's cap",
                      {"source": AGENT_SOURCE, "admitted": False, "reason": "daily-cap"}),
                     ("admitted, but with another reason", {"source": "todo", "admitted": True,
                                                            "reason": "changed"}),
                     ("a source the pool does not have", {"source": "slack", "admitted": True,
                                                          "reason": "admitted"}),
                     ("not a verdict", "admitted")):
        check("origin: %s certifies nothing" % label, origin_from_pool(v), None)
    check("origin: a start from his backlog is his; from Todo or a plan, it says nothing",
          [origin_from_start({"ticket": "ENG-1", "source": s, "at": "x"})
           for s in (FILED_SOURCE, "todo", "plan", AGENT_SOURCE, "none", None)],
          ["owner", None, None, "agent-first", None, None])
    check("origin: the pool's own words are the ones read here",
          ({FILED_SOURCE, AGENT_SOURCE} <= set(pool.SOURCES),
           {"admitted", "generation"} <= set(pool.REASONS)), (True, True))
    pool_cfg = {"owner_user_ids": ["owner-1"], "dispatcher_user_ids": ["disp-1"],
                "team_keys": ["ENG"]}
    record = {"k1": {"filed": "ENG-30", "filed_uuid": "u-30", "source": "ENG-5"}}

    def ticket(creator="owner-1", labels=("provenance:human",), parent=None, ident="ENG-20",
               uuid="u-20"):
        return {"id": uuid, "identifier": ident, "creator": {"id": creator},
                "labels": {"nodes": [{"name": n} for n in labels]},
                "parent": {"id": parent} if parent else None}
    check("idea: one the owner filed himself is his, by the pool's own rule",
          idea_origin(pool_cfg, ticket(), record), "owner")
    for label, t, rec, cfg in (
            ("an agent's idea, filed with his key, that he then moved",
             ticket(labels=("provenance:agent",)), record, pool_cfg),
            ("one with no provenance mark", ticket(labels=()), record, pool_cfg),
            ("one the dispatcher's user filed", ticket(creator="disp-1"), record, pool_cfg),
            ("one someone else filed", ticket(creator="someone"), record, pool_cfg),
            ("a piece under a plan", ticket(parent="epic-1"), record, pool_cfg),
            ("one the finding record lists by id", ticket(ident="ENG-30"), record, pool_cfg),
            ("one the finding record lists by uuid", ticket(uuid="u-30"), record, pool_cfg),
            ("with no finding record read", ticket(), None, pool_cfg),
            ("with a pool config it refuses", ticket(), record, {"owner_user_ids": ["owner-1"]}),
            ("an id of another shape", ticket(ident="eng-20"), record, pool_cfg),
            ("not a ticket", "ENG-20", record, pool_cfg)):
        check("idea: %s is not his" % label, idea_origin(cfg, t, rec), None)
    plan_from = lambda t: limits(ITEMS["plan"], ALL_ON, dict(  # noqa: E731
        BASE, origin=idea_origin(pool_cfg, t, record)))
    check("a plan from his own idea passes; from an agent's idea he only moved, it fails",
          (plan_from(ticket())["verdict"], plan_from(ticket(labels=("provenance:agent",)))[
              "reasons"]), ("pass", ["provenance-unknown"]))
    check("paths: what the tickets name, by the auto-approve gate's own reader",
          named_paths(["## Pointers\n\n`scripts/pipeline_hold.py`, `src/app/x.py`.",
                       "Touches `src/app/x.py` again."]),
          ["scripts/pipeline_hold.py", "src/app/x.py"])
    check("paths: a description that is not text reads nothing",
          (named_paths(["ok", None]), named_paths("one string")), (None, None))
    result = {"start": [], "stops": [{"stop": "plan-at-limit"}, {"stop": "made-up"}, "x"],
              "held": [{"ticket": "ENG-11", "reason": "over-load", "waits_on": []},
                       {"ticket": "ENG-12", "reason": "invented", "waits_on": []}]}
    check("queue: its stops, and this piece held over the load",
          queue_fact(result, "ENG-11"),
          {"stops": ["plan-at-limit", "unknown", "unknown"], "held": "over-load"})
    check("queue: a held reason it does not know is malformed, never none",
          queue_fact(result, "ENG-12")["held"], "malformed")
    check("queue: a ticket it does not hold, and no stops",
          queue_fact({"stops": [], "held": []}, "ENG-11"), {"stops": [], "held": None})
    check("queue: a result that is not one is not read",
          (queue_fact(None), queue_fact({"stops": None, "held": []}),
           queue_fact({"stops": [], "held": None})), (None, None, None))
    check("queue: the queue's own words are the ones read here",
          ("over-load" in queue_order.HELD, "plan-at-limit" in queue_order.STOPS), (True, True))
    utc = {"time_zone": "UTC", "days": dict((d, {"start": "09:00", "hours": 6}) for d in (
        "mon", "tue", "wed", "thu", "fri", "sat", "sun")), "grace_minutes": 0}
    noon = calendar.timegm((2026, 11, 11, 12, 0, 0))
    night = calendar.timegm((2026, 11, 11, 22, 0, 0))
    check("window: open at noon, closed at night, by the window's own rules",
          (window_open(utc, noon), window_open(utc, night)), (True, False))
    check("window: a config it refuses is not known",
          window_open(dict(utc, hours=3), noon), None)

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        status = os.path.join(tmp, "watcher.json")

        def put(path, content, mode=0o600):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content if isinstance(content, str) else json.dumps(content))
            os.chmod(path, mode)
            return path

        now = calendar.timegm((2026, 11, 11, 15, 0, 0))
        doc = {"schema": WATCHER_SCHEMA, "written_at": "2026-11-11T14:45:00Z"}
        put(status, doc)
        check("watcher: written 15 minutes ago is fresh", watcher_state(status, now), "fresh")
        check("watcher: past its max age is stale", watcher_state(status, now, max_age=600),
              "stale")
        put(status, dict(doc, written_at="2026-11-11T15:10:00Z"))
        check("watcher: dated ahead of now is stale", watcher_state(status, now), "stale")
        for label, content in (("another schema", dict(doc, schema="pipeline-watcher/2")),
                               ("no time", dict(doc, written_at=None)),
                               ("a local time", dict(doc, written_at="2026-11-11 14:45:00")),
                               ("not JSON", "{no"), ("a list", [doc])):
            put(status, content)
            check("watcher: %s is not read" % label, watcher_state(status, now), None)
        put(status, doc, mode=0o620)
        check("watcher: a file group can write is not trusted", watcher_state(status, now), None)
        put(status, doc)
        link = os.path.join(tmp, "linked.json")
        os.symlink(status, link)
        tree = os.path.join(tmp, "tree")
        os.makedirs(os.path.join(tree, ".git"))
        in_tree = put(os.path.join(tree, "watcher.json"), doc)
        for label, path in (("a link", link), ("inside a git working tree", in_tree),
                            ("a folder", tmp), ("absent", os.path.join(tmp, "none.json")),
                            ("relative", "watcher.json"), ("not named", None)):
            check("watcher: %s is not read" % label, watcher_state(path, now), None)
        check("watcher: a max age of 0 reads nothing", watcher_state(status, now, max_age=0), None)

        hold_path = os.path.join(tmp, "hold.json")
        check("hold: no hold file is none", hold_state(hold_path), hold.NONE)
        put(hold_path, {"schema": "pipeline-hold/1", "set_at": "2026-11-11T14:00:00Z",
                        "holds": [{"monitor": "spend", "what": "spend-day", "ref": "2026-11-11",
                                   "since": "2026-11-11T14:00:00Z"}]})
        check("hold: a hold the watcher set is held", hold_state(hold_path), hold.HELD)
        put(hold_path, "{no")
        check("hold: one that cannot be read holds", hold_state(hold_path), hold.MALFORMED)
        check("hold: none named, or one inside a git working tree, is not known",
              (hold_state(None), hold_state(os.path.join(tree, "hold.json"))), (None, None))

        ledger = os.path.join(tmp, "ledger.jsonl")
        check("bounces: a ledger not there yet is nothing spent",
              bounce_fact(ledger, "acme/app", 12, 2), {"spent": 0, "budget": 2, "exhausted": False})

        def row(outcome, pr=12, repo="acme/app"):
            return json.dumps({"schema": bounce.LEDGER_SCHEMA, "repo": repo, "pr": pr,
                               "outcome": outcome})
        put(ledger, "\n".join([row("spent"), row("spent"), row("spent", pr=13),
                               row("spent", repo="acme/other")]) + "\n")
        check("bounces: counted by Stage E's own reader, for this pull request only",
              bounce_fact(ledger, "acme/app", 12, 2), {"spent": 2, "budget": 2, "exhausted": False})
        put(ledger, "\n".join([row("spent"), row("exhausted")]) + "\n")
        check("bounces: an exhaustion Stage E recorded",
              bounce_fact(ledger, "acme/app", 12, 2)["exhausted"], True)
        put(ledger, row("spent") + "\nnot json\n")
        check("bounces: a corrupt ledger is not read, never zero",
              bounce_fact(ledger, "acme/app", 12, 2), None)
        put(ledger, row("spent") + "\n")
        for label, args in (("a budget that is not a number", ("acme/app", 12, "2")),
                            ("a negative budget", ("acme/app", 12, -1)),
                            ("a repository of another shape", ("acme app", 12, 2)),
                            ("a number that is not one", ("acme/app", True, 2))):
            check("bounces: %s is not read" % label, bounce_fact(ledger, *args), None)
        check("bounces: no ledger named is not read", bounce_fact(None, "acme/app", 12, 2), None)

        # 13. End to end: the readers' facts into the one function.
        put(status, doc)
        put(hold_path, "{no")
        facts = dict(GOOD["merge"], watcher=watcher_state(status, now),
                     hold=hold_state(os.path.join(tmp, "absent-hold.json")),
                     bounces=bounce_fact(ledger, "acme/app", 12, 2),
                     window=window_open(utc, noon), queue=queue_fact({"stops": [], "held": []}),
                     origin=origin_from_start({"ticket": "ENG-11", "source": FILED_SOURCE,
                                               "at": "x"}))
        check("end to end: facts from the readers pass a good merge",
              limits(ITEMS["merge"], ALL_ON, facts)["verdict"], "pass")
        check("end to end: an unreadable hold file fails it",
              limits(ITEMS["merge"], ALL_ON, dict(facts, hold=hold_state(hold_path)))["reasons"],
              ["hold-unknown"])

    # 14. It reuses the gates' rules by calling them, and keeps no copy of their lists; it
    #     writes nothing and starts nothing.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    check("it calls each gate it reuses",
          [c for c in ("auto_merge.gate_size(", "auto_merge.gate_risk_paths(", "graders.FLOOR",
                       "hold.read(", "bounce.ledger_view(", "window.window_at(",
                       "auto_approve.candidate_paths(", "auto_approve.glob_to_re(",
                       "auto_approve.implicates(") if c not in above], [])
    check("it keeps no copy of a floor it calls",
          [g for g in graders.FLOOR + auto_approve.RISK_PATH_FLOOR if '"%s"' % g in above], [])
    banned = ("os.replace", "os.rename", "os.unlink", "os.remove", "shutil", "os.makedirs",
              "os.chmod", "json.dump(", ".write(", "subprocess", "os.system", "socket",
              "urlopen", "eval(", "exec(", "Popen")
    check("it writes nothing, and starts no process",
          ([b for b in banned if b in above], len(re.findall(r"(?<![\w.])open\(", above)),
           'open(path, encoding="utf-8")' in above), ([], 1, True))

    if failures:
        for line in failures:
            print(line)
        print("validator limits selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator limits selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
