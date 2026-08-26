#!/usr/bin/env python3
"""Provenance-gated auto-approval — `raw` → `ready` without a human, or not.

Deterministic, model-free, stdlib only. This is the *decision* half of the
approval boundary; `.claude/hooks/pre-tool-use.py`'s `self-approval` guard is the
*denial* half. The guard refuses every attempt a session makes to move its own
ticket into `ready`; this script decides which tickets may legitimately be moved
there by an out-of-session automation that holds the tracker credential.

    guard  → "no session may approve anything"            (always, unconditional)
    this   → "these tickets qualify without a human"      (narrow, recomputed)

THE DISCRIMINATOR IS THE WHOLE POINT (contract §5). A ticket auto-approves only
when its provenance is `epic/<ID>` — decomposed from an epic a human already
approved. `monitor`, `review`, `retro-proposal` and `human` never auto-approve,
and the reason is adversarial, not stylistic: those are exactly the paths an
attacker-influenced payload takes into the backlog. A monitor files what a
failing probe told it; a review pass files what it read in a diff; a retro
proposes what it inferred from its own telemetry. If any of those could approve
itself, the pipeline could widen its own mandate by writing a ticket — and
"write a ticket that asks for X" is a capability every one of those paths has.
`epic/*` is the only class whose approval is anchored in something a human did.

Every gate below must pass. They are checked in a fixed order and ALL are
evaluated (no short-circuit), so one run reports every reason a ticket is held
rather than making an operator fix them one at a time.

  config        delivery.json present, version recognized
  enabled       autonomy.autoApproveProvenance non-empty and ⊆ ["epic"] (§5 r3)
  state         the ticket is actually sitting in linear.stateIds.raw
  supervision   no dispatcher-owned agent:*/blocked:* label, no hooks-change (§6)
  provenance    resolves to epic/<ID> per §5 rule 4 (label class + parent link)
  epic          that epic exists and a human moved it out of intake (§5 rule 2)
  dor           scripts/check_ticket_dor.py passes, in --strict mode
  risk-paths    nothing the ticket names matches autonomy.riskPaths (§1)

FAIL DIRECTION. Contract §3's read protocol: "checks that would GRANT extra
autonomy fail closed". Approval is the largest grant in the pipeline, so every
uncertainty here — absent config, unparseable config, an epic the caller could
not fetch, a state ID that matches nothing — HOLDS. That is the opposite of
`scripts/check_delivery_config.py`, which must emit nothing at all when
`delivery.json` is absent (§2: absent is *off*, not broken). The difference is
that a validator absent-cases into "there is nothing to validate", while an
approval gate absent-cases into "approve everything", and only one of those is
safe to be silent about.

WHY THE EPIC IS PASSED IN, NOT FETCHED. This script holds no credential and
makes no network call, so it stays runnable in CI, in a selftest, and on a
laptop against a pasted ticket. The caller that *does* hold the tracker key
fetches the epic and hands it over with `--epic`. A missing epic is a HOLD, so
the offline posture cannot be used to skip §5 rule 2: not fetching it is
indistinguishable, to this script, from it not existing.

Usage:
    check_auto_approve.py [--config PATH] [--repo-root DIR] [--epic FILE]
                          [--json] [--no-dor-strict] [FILE ...]
    check_auto_approve.py --selftest

Input: one ticket object, a bare list, or {"tickets": [...]} — file(s) or stdin,
the same shapes `check_ticket_dor.py` accepts, because it is the same ticket.
`--epic` takes an epic ticket object, a list, or {"epics": {...}} keyed by ID.

Exit: 0 = every ticket approved, 1 = at least one held, 2 = usage/IO/config error.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import check_ticket_dor as dor
except ImportError as exc:  # pragma: no cover - packaging accident
    sys.exit(
        "FAIL: cannot import scripts/check_ticket_dor.py (%s). The approval gate "
        "shares the Definition-of-Ready gate's ticket parser and will not fall "
        "back to a private one (contract §3, 'one description, one parser')." % exc
    )

SUPPORTED_VERSION = 1  # §1: an unrecognized version refuses to run, it does not guess

# §5: the single provenance class that may ever auto-approve. Named here as a
# constant so the "subset of [epic]" rule is one literal, not a spelled-out
# assumption in three places.
APPROVABLE_CLASS = "epic"
NEVER_APPROVE = ("monitor", "review", "retro-proposal", "human")

# §6: dispatcher-owned lifecycle labels. A ticket already under supervision is
# not a candidate — whatever put `agent:needs-human` on it is a decision this
# gate must not paper over by re-approving the ticket underneath it.
SUPERVISED_LABEL_RE = re.compile(r"^(agent:|blocked:)")
# §6: human-applied. A ticket pre-marked as touching guard machinery is a ticket
# a human has already said needs eyes.
HUMAN_ONLY_LABELS = ("hooks-change",)

# The epic must have been moved OUT of intake by a person. Every state except
# `raw` means someone acted; `raw` is where things land unreviewed.
INTAKE_STATE = "raw"

# Always-risky regardless of what a project's `autonomy.riskPaths` says. §7
# already requires the first three to be present, and this floor makes the gate
# correct even against a config that predates that rule or was hand-edited.
# `.claude/skills/**` is here because skills ARE the prompts and rubrics — a
# ticket that rewrites a grader must never ride an epic into `ready`.
# `templates/**`'s two mirrors are here for the same reason the destinations
# are: the kit ships the inert copies that BECOME `.github/workflows/**` and
# `.claude/hooks/**` at bootstrap, so a ticket scoped to a staged workflow is a
# ticket to choose the bytes CI will later run. Approving that without a person
# because the file is not visible yet is the same defect as gating it only once
# it is.
RISK_PATH_FLOOR = (
    ".claude/hooks/**",
    ".claude/settings*.json",
    ".claude/skills/**",
    ".github/workflows/**",
    "templates/workflows/**",
    "templates/hooks/**",
    "delivery.json",
)


# --------------------------------------------------------------------------- #
# Glob matching — mirrors `_glob_to_re` in .claude/hooks/pre-tool-use.py and
# `glob_to_re` in scripts/check_grader_paths.py. Keep the three in step.
# --------------------------------------------------------------------------- #
def glob_to_re(pat):
    """git-style glob → regex over a '/'-separated repo-relative path."""
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append(r"(?:[^/]+/)*"); i += 3
        elif pat.startswith("**", i):
            out.append(r".*"); i += 2
        elif pat[i] == "*":
            out.append(r"[^/]*"); i += 1
        elif pat[i] == "?":
            out.append(r"[^/]"); i += 1
        else:
            out.append(re.escape(pat[i])); i += 1
    return "^" + "".join(out) + "$"


def implicates(candidate, rx):
    """Does `candidate` touch the tree this compiled riskPath glob covers?

    Two tests, because a ticket names paths the way a human writes them. The
    literal match catches `.claude/hooks/pre-tool-use.py` against
    `.claude/hooks/**`. The probe suffix catches the DIRECTORY form — a ticket
    that says it will rework `.claude/hooks` names the same tree, and
    `^\\.claude/hooks/.*$` does not match a path with no trailing segment.
    """
    candidate = candidate.rstrip("/")
    if not candidate:
        return False
    return bool(rx.match(candidate) or rx.match(candidate + "/_"))


def candidate_paths(description):
    """Repo paths the ticket names, from inline code spans anywhere in the body.

    Deliberately whole-description, not just `## Pointers`: a ticket whose
    acceptance criteria say "the hook must also reject X" has implicated the
    hooks tree just as surely as one that lists the file under Pointers, and an
    auto-approval is not the place to be generous about which section counts.

    Reuses the DoR gate's `looks_like_path`, so "is this span a path?" is
    answered the same way in both gates. Line/anchor suffixes are trimmed the
    way `path_exists` trims them — `pre-tool-use.py:42` names a file.
    """
    spans = dor.code_spans((description or "").splitlines())
    out = []
    for span in spans:
        if not dor.looks_like_path(span):
            continue
        cleaned = re.sub(r":\d+(-\d+)?$", "", span.split("#", 1)[0]).strip()
        if cleaned:
            out.append(cleaned)
    return out


# --------------------------------------------------------------------------- #
# Config + epic loading
# --------------------------------------------------------------------------- #
def load_config(explicit, repo_root):
    """Return (config|None, source|None, error|None).

    Unlike the §7 validator, an absent config is NOT silence here: it is a
    reason to hold, reported as one. Returning the error instead of exiting lets
    a batch report it once per run alongside every other gate.
    """
    path = explicit or os.path.join(repo_root, "delivery.json")
    if not os.path.exists(path):
        if explicit:
            return None, path, "--config %s: no such file" % path
        return None, path, (
            "no delivery.json at %s — the pipeline is not configured, so nothing "
            "auto-approves (contract §2)" % path
        )
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as e:
        return None, path, "%s is present but unreadable: %s" % (path, e)
    if not isinstance(config, dict) or config.get("version") != SUPPORTED_VERSION:
        got = config.get("version") if isinstance(config, dict) else "?"
        return None, path, (
            "%s declares version %r; this gate implements contract version %d and "
            "will not guess" % (path, got, SUPPORTED_VERSION)
        )
    return config, path, None


def load_epics(path):
    """Epic ticket objects keyed by ticket ID. Exits 2 on an unreadable file."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        print("FAIL: cannot read --epic %s: %s" % (path, e), file=sys.stderr)
        sys.exit(2)
    if isinstance(data, dict) and "epics" in data:
        data = data["epics"]
    if isinstance(data, dict) and not all(isinstance(v, dict) for v in data.values()):
        data = [data]  # a bare single epic object
    if isinstance(data, list):
        data = {str(e.get("id") or e.get("identifier") or ""): e for e in data if isinstance(e, dict)}
    if not isinstance(data, dict):
        print(
            'FAIL: --epic %s: expected an epic object, a list, or {"epics": {...}}' % path,
            file=sys.stderr,
        )
        sys.exit(2)
    return {k: v for k, v in data.items() if k}


# --------------------------------------------------------------------------- #
# The gates
# --------------------------------------------------------------------------- #
class Verdict:
    """One ticket's decision. `held` is the list of reasons, newest gate last."""

    def __init__(self, ref, title):
        self.ref = ref
        self.title = title
        self.checks = []
        self.held = []

    def gate(self, name, ok, detail):
        self.checks.append({"gate": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.held.append({"gate": name, "reason": detail})
        return bool(ok)

    @property
    def approved(self):
        return not self.held

    def as_dict(self):
        return {
            "ref": self.ref,
            "title": self.title,
            "approved": self.approved,
            "checks": self.checks,
            "held": self.held,
        }


def resolve_provenance(ticket):
    """(value, class, error) — §5 rule 4's two representations, one value.

    Linear cannot store `epic/ENG-100` in a label (labels are a fixed
    vocabulary), so the CLASS lives in `provenance:<class>` and the ID lives in
    the parent link. This reconstructs the full value the same way
    `check_ticket_dor.check_links` does, and refuses when the two disagree —
    a ticket labelled `provenance:epic` whose `provenance` field says `monitor`
    is not a ticket to guess about.
    """
    labels = [str(x) for x in (ticket.get("labels") or [])]
    classes = [l.split(":", 1)[1] for l in labels if l.startswith("provenance:")]
    if len(classes) > 1:
        return None, None, "carries %d provenance labels (%s) — §6 allows exactly one" % (
            len(classes), ", ".join(sorted(classes)),
        )
    label_class = classes[0] if classes else None
    value = str(ticket.get("provenance") or "").strip()
    parent = str(ticket.get("parentId") or ticket.get("parent") or "").strip()

    if not value:
        if label_class == APPROVABLE_CLASS:
            if not dor.TICKET_ID_RE.match(parent):
                return None, label_class, (
                    "labelled provenance:epic but no parent linked by ticket ID, so "
                    "there is no epic/<ID> to verify (§5 rule 4)"
                )
            value = "%s/%s" % (APPROVABLE_CLASS, parent)
        elif label_class:
            value = label_class
        else:
            return None, None, "no provenance value and no provenance:* label to derive one"

    if not dor.PROVENANCE_RE.match(value):
        return None, label_class, (
            "%r is not a contract §5 provenance value (epic/<ID>, monitor, review, "
            "retro-proposal, human)" % value
        )
    cls = APPROVABLE_CLASS if value.startswith(APPROVABLE_CLASS + "/") else value
    if label_class and cls != label_class:
        return None, label_class, (
            "provenance value %r and label provenance:%s disagree — refusing to pick "
            "one" % (value, label_class)
        )
    if cls == APPROVABLE_CLASS:
        named = value.split("/", 1)[1]
        if parent and dor.TICKET_ID_RE.match(parent) and parent != named:
            return None, cls, (
                "parent %s is not the epic named in provenance %r" % (parent, value)
            )
    return value, cls, None


def check_enabled(config, v):
    autonomy = (config or {}).get("autonomy") or {}
    allowed = autonomy.get("autoApproveProvenance")
    if not isinstance(allowed, list):
        return v.gate(
            "enabled", False,
            "autonomy.autoApproveProvenance is not an array — §1 requires one, and "
            "an unreadable setting is not a licence to approve",
        )
    if not allowed:
        return v.gate(
            "enabled", False,
            "autonomy.autoApproveProvenance is empty — auto-approval is switched off "
            "for this project; every ticket waits for a person",
        )
    extra = sorted({str(x) for x in allowed} - {APPROVABLE_CLASS})
    if extra:
        return v.gate(
            "enabled", False,
            "autonomy.autoApproveProvenance contains %s — §5 rule 3 makes it a subset "
            "of [\"epic\"]; refusing to honour a config the §7 validator rejects"
            % ", ".join(repr(x) for x in extra),
        )
    return v.gate("enabled", True, "auto-approval enabled for provenance class epic")


def check_state(ticket, config, v):
    states = ((config or {}).get("linear") or {}).get("stateIds") or {}
    raw = states.get(INTAKE_STATE)
    got = ticket.get("stateId")
    if not raw:
        return v.gate(
            "state", False,
            "linear.stateIds.raw is unset — cannot tell whether this ticket is in "
            "intake, and an unverifiable state is not an approvable one",
        )
    if got in (None, ""):
        return v.gate("state", False, "ticket has no stateId to compare against linear.stateIds.raw")
    if got != raw:
        if got == states.get("ready"):
            return v.gate("state", False, "ticket is already in `ready` — nothing to approve")
        return v.gate(
            "state", False,
            "stateId %r is not linear.stateIds.raw (%r) — this gate only moves tickets "
            "out of intake" % (got, raw),
        )
    return v.gate("state", True, "ticket is in intake (linear.stateIds.raw)")


def check_supervision(ticket, v):
    labels = [str(x) for x in (ticket.get("labels") or [])]
    supervised = sorted(l for l in labels if SUPERVISED_LABEL_RE.match(l))
    human_only = sorted(l for l in labels if l in HUMAN_ONLY_LABELS)
    if supervised:
        return v.gate(
            "supervision", False,
            "carries dispatcher-owned label(s) %s — a ticket already under supervision "
            "is not re-approved underneath it (§6)" % ", ".join(supervised),
        )
    if human_only:
        return v.gate(
            "supervision", False,
            "carries %s — a human has already marked this as touching guard machinery"
            % ", ".join(human_only),
        )
    return v.gate("supervision", True, "no dispatcher-owned or human-only labels")


def check_provenance(ticket, v):
    value, cls, err = resolve_provenance(ticket)
    if err:
        v.gate("provenance", False, err)
        return None
    if cls in NEVER_APPROVE:
        v.gate(
            "provenance", False,
            "provenance is `%s` — §5 permits auto-approval for epic/<ID> only. %s"
            % (cls, _why_not(cls)),
        )
        return None
    v.gate("provenance", True, "provenance %s — the one auto-approvable class" % value)
    return value


def _why_not(cls):
    """The adversarial reason, per class. Operators read these in run logs."""
    return {
        "monitor": "A monitor files what a failing probe reported; approving it would let "
                   "whatever can trip a probe queue work.",
        "review": "A review pass files what it read in a diff; approving it would let a "
                  "reviewed change author its own follow-up mandate.",
        "retro-proposal": "A retrospective proposes from its own telemetry; approving it "
                          "would let the pipeline widen its mandate by writing a ticket.",
        "human": "A person already decided — a human-filed ticket enters `ready` directly, "
                 "and does not need this gate.",
    }.get(cls, "")


def check_epic(value, epics, config, v):
    """§5 rule 2: the referenced epic exists and is itself human-approved.

    Without this, `epic/<anything>` is a self-serve approval: a fabricated ID
    would mint autonomy, because the string alone is what the provenance gate
    matched on.
    """
    epic_id = value.split("/", 1)[1]
    epic = epics.get(epic_id)
    if epic is None:
        return v.gate(
            "epic", False,
            "epic %s was not supplied to --epic — §5 rule 2 requires the referenced "
            "epic to be shown to exist and be approved; an unverified ID is a "
            "fabricated one as far as this gate is concerned" % epic_id,
        )
    states = ((config or {}).get("linear") or {}).get("stateIds") or {}
    by_id = {sid: key for key, sid in states.items() if sid}
    got = epic.get("stateId")
    if got in (None, ""):
        return v.gate("epic", False, "epic %s has no stateId to judge" % epic_id)
    key = by_id.get(got)
    if key is None:
        return v.gate(
            "epic", False,
            "epic %s is in state %r, which matches none of linear.stateIds — an "
            "unclassifiable state cannot be read as approval" % (epic_id, got),
        )
    if key == INTAKE_STATE:
        return v.gate(
            "epic", False,
            "epic %s is still in intake (`raw`) — nobody has approved it, so nothing "
            "decomposed from it inherits approval" % epic_id,
        )
    epic_labels = [str(x) for x in (epic.get("labels") or [])]
    blocked = sorted(l for l in epic_labels if SUPERVISED_LABEL_RE.match(l))
    if blocked:
        return v.gate(
            "epic", False,
            "epic %s carries %s — its own supervision is unresolved"
            % (epic_id, ", ".join(blocked)),
        )
    return v.gate("epic", True, "epic %s is in `%s` — a human moved it out of intake" % (epic_id, key))


def check_dor(ticket, config_path, repo_root, strict, v):
    """Run the Definition-of-Ready gate over this ticket, in-process.

    `--strict` by default. The looser tier exists so the DoR gate stays usable
    on hand-written tickets; auto-approval is the highest-autonomy path in the
    pipeline and gets the tightest reading of the same rules.
    """
    canonical = (
        dor.template_sections(repo_root)
        or dor.template_sections(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        or dor.FALLBACK_SECTIONS
    )
    config = None
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as fh:
                config = json.load(fh)
        except (OSError, ValueError):
            config = None
    result = dor.run([ticket], canonical, config, strict, repo_root)
    report = result["tickets"][0]
    problems = report["errors"] + (report["warnings"] if strict else [])
    if problems:
        listed = "; ".join("[%s] %s" % (p["rule"], p["message"]) for p in problems[:6])
        more = "" if len(problems) <= 6 else " (+%d more)" % (len(problems) - 6)
        return v.gate(
            "dor", False,
            "Definition-of-Ready%s failed: %s%s" % (" (strict)" if strict else "", listed, more),
        )
    return v.gate("dor", True, "Definition-of-Ready%s passes" % (" (strict)" if strict else ""))


def check_risk_paths(ticket, config, v):
    configured = ((config or {}).get("autonomy") or {}).get("riskPaths") or []
    globs = list(RISK_PATH_FLOOR)
    for g in configured:
        if isinstance(g, str) and g.strip() and g.strip() not in globs:
            globs.append(g.strip())
    compiled = [(g, re.compile(glob_to_re(g))) for g in globs]
    hits = []
    for candidate in candidate_paths(ticket.get("description")):
        for g, rx in compiled:
            if implicates(candidate, rx):
                hits.append("%s (matches %s)" % (candidate, g))
                break
    if hits:
        return v.gate(
            "risk-paths", False,
            "names risk-allowlisted path(s): %s — a change there forces human review "
            "regardless of provenance (§1 autonomy.riskPaths)" % "; ".join(sorted(set(hits))[:6]),
        )
    return v.gate("risk-paths", True, "names no risk-allowlisted path")


def check_ticket(ticket, index, config, config_path, config_error, epics, repo_root, strict):
    ref = ticket.get("id") or ticket.get("identifier") or "ticket[%d]" % index
    v = Verdict(str(ref), str(ticket.get("title") or "").strip())

    if config_error:
        # One hard gate, and every downstream gate is unevaluable without it.
        # Report it and stop: listing eight "cannot verify" lines would bury the
        # single thing an operator has to fix.
        v.gate("config", False, config_error)
        return v
    v.gate("config", True, "%s (version %d)" % (config_path, SUPPORTED_VERSION))

    check_enabled(config, v)
    check_state(ticket, config, v)
    check_supervision(ticket, v)
    value = check_provenance(ticket, v)
    if value:
        check_epic(value, epics, config, v)
    check_dor(ticket, config_path, repo_root, strict, v)
    check_risk_paths(ticket, config, v)
    return v


def run(tickets, config, config_path, config_error, epics, repo_root, strict):
    verdicts = [
        check_ticket(t, i, config, config_path, config_error, epics, repo_root, strict)
        for i, t in enumerate(tickets)
    ]
    return {
        "schema": "pipeline-auto-approve/1",
        "ok": all(v.approved for v in verdicts),
        "strict": strict,
        "config": config_path,
        "approve": [v.ref for v in verdicts if v.approved],
        "hold": [v.ref for v in verdicts if not v.approved],
        "tickets": [v.as_dict() for v in verdicts],
        "summary": {
            "tickets": len(verdicts),
            "approved": sum(1 for v in verdicts if v.approved),
            "held": sum(1 for v in verdicts if not v.approved),
        },
    }


def print_text(result):
    for t in result["tickets"]:
        print("%s  %s  %s" % ("APPROVE" if t["approved"] else "HOLD   ", t["ref"], t["title"]))
        for h in t["held"]:
            print("           hold [%s] %s" % (h["gate"], h["reason"]))
    s = result["summary"]
    print("%d ticket(s): %d approved, %d held" % (s["tickets"], s["approved"], s["held"]))


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
GOOD_DESCRIPTION = """## Context

Tokens refresh only after a 401, so every session shows one failed request.

## Acceptance criteria

- [ ] A token within 5 minutes of expiry is refreshed before the request is sent
- [ ] `npm test` covers the near-expiry path and passes

## Out of scope

- Refresh-token rotation and reuse detection (separate ticket)

## Test plan

- `npm test` with a clock stubbed to 4 minutes before expiry

## Pointers

- `README.md` holds the current auth notes
"""

RISKY_DESCRIPTION = GOOD_DESCRIPTION.replace(
    "- `README.md` holds the current auth notes",
    "- `.claude/hooks/pre-tool-use.py` will need a matching case",
)

GOOD_CONFIG = {
    "version": 1,
    "linear": {
        "teamKey": "ENG",
        "workspace": "acme",
        "stateIds": {
            "raw": "state-raw",
            "ready": "state-ready",
            "working": "state-working",
            "review": "state-review",
            "done": "state-done",
        },
        "labels": {
            "ids": {
                "track:platform": "lbl-track",
                "effort:S": "lbl-s",
                "effort:M": "lbl-m",
                "effort:L": "lbl-l",
                "agent:queued": "lbl-queued",
                "provenance:epic": "lbl-epic",
                "provenance:human": "lbl-human",
                "hooks-change": "lbl-hooks",
            },
            "required": ["effort:M"],
        },
    },
    "autonomy": {
        "autoApproveProvenance": ["epic"],
        "autoMergeMaxLines": 0,
        "riskPaths": [".claude/hooks/**", ".claude/settings*.json", "delivery.json"],
    },
}


def good_ticket():
    return {
        "id": "ENG-123",
        "title": "Refresh tokens before expiry",
        "description": GOOD_DESCRIPTION,
        "labels": ["track:platform", "effort:M", "provenance:epic"],
        "projectId": "proj-1",
        "parentId": "ENG-100",
        "stateId": "state-raw",
        "provenance": "epic/ENG-100",
    }


def good_epics():
    return {"ENG-100": {"id": "ENG-100", "stateId": "state-working", "labels": ["track:platform"]}}


def selftest():
    import copy
    import tempfile

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    failures = []
    cases = [0]

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "delivery.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(GOOD_CONFIG, fh)

        def decide(ticket, epics=None, config=None, strict=True):
            cfg = GOOD_CONFIG if config is None else config
            path = cfg_path
            if config is not None:
                path = os.path.join(tmp, "custom.json")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(cfg, fh)
            return run(
                [ticket], cfg, path, None,
                good_epics() if epics is None else epics,
                root, strict,
            )["tickets"][0]

        def expect(label, verdict, approved, gate=None):
            cases[0] += 1
            if verdict["approved"] != approved:
                failures.append(
                    "%s: expected %s, got %s (%s)"
                    % (label, "APPROVE" if approved else "HOLD",
                       "APPROVE" if verdict["approved"] else "HOLD",
                       "; ".join(h["reason"] for h in verdict["held"]) or "no holds"))
                return
            if gate and not any(h["gate"] == gate for h in verdict["held"]):
                failures.append(
                    "%s: held, but not on gate %r (held on %s)"
                    % (label, gate, ", ".join(h["gate"] for h in verdict["held"]) or "nothing"))

        # ── The happy path ──────────────────────────────────────────────────
        expect("baseline epic ticket approves", decide(good_ticket()), True)

        # ── §5: the four classes that must never auto-approve ───────────────
        for cls in NEVER_APPROVE:
            t = good_ticket()
            t["provenance"] = cls
            t["labels"] = ["track:platform", "effort:M", "provenance:%s" % cls]
            t["parentId"] = ""
            expect("provenance %s never approves" % cls, decide(t), False, "provenance")

        # A fabricated epic ID is not an approval (§5 rule 2).
        t = good_ticket()
        t["provenance"] = "epic/ENG-999"
        t["parentId"] = "ENG-999"
        expect("unverified epic holds", decide(t), False, "epic")

        # An epic still in intake approves nothing beneath it.
        expect(
            "epic in raw holds",
            decide(good_ticket(), {"ENG-100": {"id": "ENG-100", "stateId": "state-raw"}}),
            False, "epic",
        )
        # An epic in a state matching no configured ID is unclassifiable.
        expect(
            "epic in unknown state holds",
            decide(good_ticket(), {"ENG-100": {"id": "ENG-100", "stateId": "state-mystery"}}),
            False, "epic",
        )
        # A blocked epic drags its supervision down the tree.
        expect(
            "blocked epic holds",
            decide(good_ticket(), {"ENG-100": {"id": "ENG-100", "stateId": "state-working",
                                               "labels": ["agent:needs-human"]}}),
            False, "epic",
        )
        # The label/value disagreement is refused rather than resolved.
        t = good_ticket()
        t["labels"] = ["track:platform", "effort:M", "provenance:monitor"]
        expect("provenance label/value disagreement holds", decide(t), False, "provenance")
        # Two provenance labels is a §6 violation, not a majority vote.
        t = good_ticket()
        t["labels"] = ["track:platform", "effort:M", "provenance:epic", "provenance:human"]
        expect("two provenance labels hold", decide(t), False, "provenance")
        # §5 rule 4 reconstruction: label class + parent, no explicit value.
        t = good_ticket()
        del t["provenance"]
        expect("provenance reconstructed from label + parent", decide(t), True)
        t = good_ticket()
        del t["provenance"]
        t["parentId"] = ""
        expect("provenance:epic with no parent holds", decide(t), False, "provenance")
        # The parent must be the epic the provenance names.
        t = good_ticket()
        t["parentId"] = "ENG-200"
        expect("parent disagreeing with provenance holds", decide(t), False, "provenance")

        # ── The enable switch ───────────────────────────────────────────────
        cfg = copy.deepcopy(GOOD_CONFIG)
        cfg["autonomy"]["autoApproveProvenance"] = []
        expect("empty autoApproveProvenance holds", decide(good_ticket(), config=cfg), False, "enabled")
        cfg = copy.deepcopy(GOOD_CONFIG)
        cfg["autonomy"]["autoApproveProvenance"] = ["epic", "monitor"]
        expect("non-subset autoApproveProvenance holds", decide(good_ticket(), config=cfg), False, "enabled")

        # ── State ───────────────────────────────────────────────────────────
        t = good_ticket()
        t["stateId"] = "state-ready"
        expect("already-ready ticket holds", decide(t), False, "state")
        t = good_ticket()
        t["stateId"] = "state-working"
        expect("non-intake ticket holds", decide(t), False, "state")

        # ── Supervision (§6) ────────────────────────────────────────────────
        for label in ("agent:needs-human", "agent:blocked", "blocked:capacity"):
            t = good_ticket()
            t["labels"] = t["labels"] + [label]
            expect("supervised (%s) holds" % label, decide(t), False, "supervision")
        t = good_ticket()
        t["labels"] = t["labels"] + ["hooks-change"]
        expect("hooks-change holds", decide(t), False, "supervision")

        # ── riskPaths ───────────────────────────────────────────────────────
        t = good_ticket()
        t["description"] = RISKY_DESCRIPTION
        expect("riskPath in pointers holds", decide(t), False, "risk-paths")
        # The FLOOR holds even when the config's own list has been shortened.
        cfg = copy.deepcopy(GOOD_CONFIG)
        cfg["autonomy"]["riskPaths"] = []
        t = good_ticket()
        t["description"] = RISKY_DESCRIPTION
        expect("riskPath floor survives an empty config list",
               decide(t, config=cfg), False, "risk-paths")
        # A skill is a rubric: naming one is a grader change.
        t = good_ticket()
        t["description"] = GOOD_DESCRIPTION.replace(
            "`README.md`", "`.claude/skills/weekly-review/SKILL.md`")
        expect("skill path holds (rubrics are graders)", decide(t), False, "risk-paths")
        # The DIRECTORY form names the same tree as the `/**` glob.
        t = good_ticket()
        t["description"] = GOOD_DESCRIPTION.replace("`README.md`", "`.claude/hooks`")
        expect("riskPath directory form holds", decide(t), False, "risk-paths")
        # Staging mirrors: a ticket scoped to the INERT copy is a ticket to
        # choose the bytes CI later runs. Held with an emptied config list too —
        # these are floor entries, not config the project can opt out of.
        cfg = copy.deepcopy(GOOD_CONFIG)
        cfg["autonomy"]["riskPaths"] = []
        for staged in ("templates/workflows/pipeline-review.yml",
                       "templates/hooks/session-start-provision-env.sh"):
            t = good_ticket()
            t["description"] = GOOD_DESCRIPTION.replace("`README.md`", "`%s`" % staged)
            expect("staged mirror holds (%s)" % staged, decide(t, config=cfg),
                   False, "risk-paths")

        # ── DoR ─────────────────────────────────────────────────────────────
        t = good_ticket()
        t["description"] = t["description"].replace(
            "- [ ] A token within 5 minutes of expiry is refreshed before the request is sent\n", ""
        ).replace(
            "- [ ] `npm test` covers the near-expiry path and passes\n", ""
        )
        expect("empty acceptance criteria holds", decide(t), False, "dor")
        t = good_ticket()
        t["labels"] = ["track:platform", "provenance:epic"]  # no effort:*
        expect("missing effort label holds", decide(t), False, "dor")
        # Strictness is the point: a warning-tier ticket approves only when the
        # caller explicitly loosens the gate.
        t = good_ticket()
        t["description"] = t["description"].replace(
            "- Refresh-token rotation and reuse detection (separate ticket)\n", ""
        )
        expect("empty out-of-scope holds under strict", decide(t), False, "dor")
        expect("empty out-of-scope passes when strict is off", decide(t, strict=False), True)

        # ── Config (§2/§3 fail direction) ───────────────────────────────────
        cases[0] += 1
        missing = run([good_ticket()], None, os.path.join(tmp, "nope.json"),
                      "no delivery.json", good_epics(), root, True)
        if missing["ok"] or missing["tickets"][0]["held"][0]["gate"] != "config":
            failures.append("absent config must HOLD on the config gate, not approve")

        cases[0] += 1
        _, _, err = load_config(None, tmp + "-does-not-exist")
        if not err:
            failures.append("load_config must report an error for an absent delivery.json")

        cases[0] += 1
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        _, _, err = load_config(bad, tmp)
        if not err:
            failures.append("load_config must report an error for an unparseable delivery.json")

        cases[0] += 1
        wrong = os.path.join(tmp, "v2.json")
        with open(wrong, "w", encoding="utf-8") as fh:
            json.dump({"version": 2}, fh)
        _, _, err = load_config(wrong, tmp)
        if not err:
            failures.append("load_config must refuse an unrecognized contract version")

        # ── Batch behaviour ─────────────────────────────────────────────────
        cases[0] += 1
        held = good_ticket()
        held["provenance"] = "monitor"
        held["labels"] = ["track:platform", "effort:M", "provenance:monitor"]
        held["parentId"] = ""
        batch = run([good_ticket(), held], GOOD_CONFIG, cfg_path, None,
                    good_epics(), root, True)
        if batch["ok"] or batch["approve"] != ["ENG-123"] or len(batch["hold"]) != 1:
            failures.append("a mixed batch must approve the good ticket and hold the other")

        # ── Glob helpers ────────────────────────────────────────────────────
        rx = re.compile(glob_to_re(".claude/settings*.json"))
        cases[0] += 1
        if not implicates(".claude/settings.local.json", rx):
            failures.append("settings*.json must match settings.local.json")
        cases[0] += 1
        if implicates("src/settings.json", rx):
            failures.append("settings*.json must not match a nested settings.json")

    if failures:
        print("FAIL: %d of %d auto-approval case(s) failed:" % (len(failures), cases[0]))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: %d auto-approval case(s) passed" % cases[0])
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", metavar="FILE", help="ticket JSON (default: stdin)")
    ap.add_argument("--config", help="path to delivery.json (default: <repo-root>/delivery.json)")
    ap.add_argument("--repo-root", default=".", help="root for pointer-path checks (default: .)")
    ap.add_argument("--epic", help="JSON with the referenced epic ticket(s)")
    ap.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    ap.add_argument(
        "--no-dor-strict", action="store_false", dest="strict",
        help="run the DoR gate in its default tier instead of --strict (not recommended)",
    )
    ap.add_argument("--selftest", action="store_true", help="run built-in fixtures and exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    repo_root = os.path.abspath(args.repo_root)
    config, config_path, config_error = load_config(args.config, repo_root)
    if config_error and args.config:
        # An explicitly named config that does not exist is an operator mistake,
        # not a policy outcome. Fail loudly rather than reporting "held".
        if not os.path.exists(args.config):
            print("FAIL: %s" % config_error, file=sys.stderr)
            return 2
    epics = load_epics(args.epic)
    tickets = dor.load_tickets(args.files)
    result = run(tickets, config, config_path, config_error, epics, repo_root, args.strict)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print_text(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
