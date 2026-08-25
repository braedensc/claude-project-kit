#!/usr/bin/env python3
"""Definition-of-Ready gate for pipeline tickets.

Deterministic, model-free. `/plan-epic` runs this on every ticket it drafts
BEFORE filing, and again on what actually landed after filing — so a field that
silently failed to set is caught by the same rules that judged the draft.

The canonical section list is parsed out of `docs/TICKET-TEMPLATE.md` (the block
between the BEGIN/END TICKET TEMPLATE markers), so renaming a heading there
changes this gate. A baked-in fallback covers callers running outside a kit
checkout; `--selftest` asserts the two agree, so they cannot drift silently.

Two tiers. ERRORS are the gate proper: sections present, acceptance criteria
non-empty, labels set, project linked, state not self-approved. WARNINGS are the
quality tier (empty Out of scope / Test plan, nothing mechanically checkable, a
Pointers path that does not exist) — `--strict` promotes them, and that is what
`/plan-epic` runs. The looser default keeps the gate usable on hand-written
tickets and on ticket types where a pointer genuinely does not apply.

Config: `delivery.json` from the working tree (or --config). Unlike the guards in
docs/PIPELINE-CONTRACT.md §1, this validator reads the working-tree copy on
purpose: it gates a session's own output quality, not the session's autonomy. A
session that loosens its DoR still cannot move a ticket to ready, raise a budget,
or merge anything — those read the committed copy. When there is no config, the
config-dependent rules are SKIPPED AND NAMED, never quietly passed.

Usage:
    check_ticket_dor.py [--strict] [--json] [--config PATH] [--repo-root DIR] [FILE ...]
    check_ticket_dor.py --selftest

Input: one ticket object, a bare list, or {"tickets": [...]} — file(s) or stdin.
Exit: 0 = ready (warnings alone do not fail), 1 = errors, 2 = usage/IO/config error.
"""
import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile

# Fallback only. The live list comes from docs/TICKET-TEMPLATE.md; --selftest
# fails if this and that file disagree.
FALLBACK_SECTIONS = [
    "Context",
    "Acceptance criteria",
    "Out of scope",
    "Test plan",
    "Pointers",
]
SUPPORTED_VERSION = 1  # contract §1: an unrecognized version refuses, it does not guess
TEMPLATE_DOC = os.path.join("docs", "TICKET-TEMPLATE.md")
BEGIN_MARKER = "<!-- BEGIN TICKET TEMPLATE -->"
END_MARKER = "<!-- END TICKET TEMPLATE -->"

EFFORTS = ("effort:S", "effort:M", "effort:L")
PROVENANCE_CLASSES = ("epic", "monitor", "review", "retro-proposal", "human")
# Dispatcher-owned lifecycle labels (contract §6). A session setting one is a
# session editing its own supervision.
FORBIDDEN_LABEL_RE = re.compile(r"^(agent:|blocked:capacity$)")

TICKET_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
PROVENANCE_RE = re.compile(
    r"^(epic/[A-Z][A-Z0-9]*-\d+|monitor|review|retro-proposal|human)$"
)
HEADING_RE = re.compile(r"^##\s+(\S.*?)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
CHECKLIST_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s*(.*)$")
BULLET_RE = re.compile(r"^\s*[-*]\s+(?!\[[ xX]\])(.*)$")
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
# Residue of an unfinished draft. The doubled-brace form is spelled with escapes
# so this file never itself contains a placeholder token.
DRAFT_RE = re.compile(r"\b(TBD|TKTK)\b|\{\{[A-Z0-9_]+\}\}")
# An unfilled template prompt: <Why this ticket exists, in 2-4 sentences…>. It
# needs a leading letter and an internal space, so `<div>`, `List<T>` and a bare
# `<you>` do not match, and neither does prose like "if x < 5 and y > 3".
ANGLE_PROMPT_RE = re.compile(r"<[A-Za-z][^<>]*?\s[^<>]*?>", re.DOTALL)
# These are also real words in ticket prose ("remove the TODO in foo.ts"), so they
# only count as residue OUTSIDE an inline code span.
MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")
MAX_TITLE = 90
MIN_AC_CHARS = 12


# --------------------------------------------------------------------------- #
# Template + config loading
# --------------------------------------------------------------------------- #
def template_sections(repo_root):
    """Canonical section names from docs/TICKET-TEMPLATE.md, or None if absent."""
    path = os.path.join(repo_root, TEMPLATE_DOC)
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start < 0 or end < start:
        return None
    names = []
    for line in text[start + len(BEGIN_MARKER):end].splitlines():
        m = HEADING_RE.match(line)
        if m:
            names.append(m.group(1))
    return names or None


def load_config(explicit, repo_root):
    """Return (config|None, source|None). Exits 2 on an unreadable/invalid file."""
    path = explicit or os.path.join(repo_root, "delivery.json")
    if not os.path.exists(path):
        if explicit:
            print(f"FAIL: --config {path}: no such file", file=sys.stderr)
            sys.exit(2)
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as e:
        # Present but unparseable is BROKEN, not off (contract §2).
        print(f"FAIL: {path} is present but unreadable: {e}", file=sys.stderr)
        sys.exit(2)
    # Contract §1: "A reader that does not recognize the value must refuse to run,
    # not guess." Guessing here is not merely wrong, it is fail-open: a v2 that
    # relocates linear.labels.ids or linear.stateIds would leave those rules
    # SKIPPED and the run green.
    if not isinstance(config, dict) or config.get("version") != SUPPORTED_VERSION:
        print(
            f"FAIL: {path} declares version {config.get('version') if isinstance(config, dict) else '?'!r}; "
            f"this gate implements contract version {SUPPORTED_VERSION} and will not guess.",
            file=sys.stderr,
        )
        sys.exit(2)
    return config, path


# --------------------------------------------------------------------------- #
# Markdown parsing
# --------------------------------------------------------------------------- #
def split_sections(description):
    """[(name, [body lines])] for every `## ` heading outside a fenced block."""
    sections, current, fence = [], None, None
    for line in (description or "").splitlines():
        f = FENCE_RE.match(line)
        if f:
            token = f.group(1)
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
        if fence is None:
            m = HEADING_RE.match(line)
            if m:
                current = (m.group(1), [])
                sections.append(current)
                continue
        if current is not None:
            current[1].append(line)
    return sections


def body_of(sections, name):
    for got, lines in sections:
        if got.strip().lower() == name.lower():
            return lines
    return None


def checklist_items(lines):
    return [m.group(1).strip() for m in map(CHECKLIST_RE.match, lines or []) if m]


def bullets(lines):
    return [m.group(1).strip() for m in map(BULLET_RE.match, lines or []) if m]


def prose_only(text):
    """The description with fenced blocks and inline code spans removed.

    Markers like TODO and angle-bracket prompts are residue in prose and
    perfectly legitimate inside code — judging them needs the code stripped.
    """
    out, fence = [], None
    for line in (text or "").splitlines():
        f = FENCE_RE.match(line)
        if f:
            token = f.group(1)
            if fence is None:
                fence = token
                continue
            if fence == token:
                fence = None
                continue
        if fence is None:
            out.append(CODE_SPAN_RE.sub("`code`", line))
    return "\n".join(out)


def code_spans(lines):
    out = []
    for line in lines or []:
        out.extend(s.strip() for s in CODE_SPAN_RE.findall(line))
    return out


# Extensionless files that are ordinary, useful pointers.
EXTENSIONLESS = {
    "brewfile", "codeowners", "changelog", "dockerfile", "gemfile", "justfile",
    "license", "makefile", "notice", "procfile", "rakefile", "readme",
    "vagrantfile",
}


def looks_like_path(span):
    """A code span that is plausibly a repo path rather than a command."""
    if not span or span.startswith(("$", "#", "-")) or " " in span:
        return False
    if span.lower() in EXTENSIONLESS:
        return True
    return "/" in span or re.search(r"\.[A-Za-z0-9]{1,6}$", span) is not None


def within(repo_root, path):
    """True if path is inside repo_root. Pointers name files in THIS tree."""
    root = os.path.realpath(repo_root)
    target = os.path.realpath(path)
    return target == root or target.startswith(root + os.sep)


def path_exists(repo_root, span):
    candidate = span.split("#", 1)[0]
    candidate = re.sub(r":\d+(-\d+)?$", "", candidate).rstrip("/")
    # An absolute path or a `..` escape is not a pointer into this repo; without
    # this, os.path.join discards repo_root entirely and `/etc/passwd` "exists".
    if not candidate or os.path.isabs(candidate):
        return False
    literal = os.path.join(repo_root, candidate)
    # Literal first: `[` is far more often a real path character (a Next.js
    # dynamic segment, src/app/[slug]/page.tsx) than an intended character class.
    if os.path.exists(literal) and within(repo_root, literal):
        return True
    if any(ch in candidate for ch in "*?"):
        import glob

        return any(
            within(repo_root, m)
            for m in glob.glob(literal, recursive=True)
        )
    return False


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self, ref, title):
        self.ref = ref
        self.title = title
        self.errors = []
        self.warnings = []

    def err(self, rule, message):
        self.errors.append({"rule": rule, "message": message})

    def warn(self, rule, message):
        self.warnings.append({"rule": rule, "message": message})

    def as_dict(self):
        return {
            "ref": self.ref,
            "title": self.title,
            "ok": not self.errors,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def check_title(t, r):
    title = (t.get("title") or "").strip()
    if not title:
        r.err("title", "title is empty")
        return
    if "\n" in title:
        r.err("title", "title spans multiple lines")
    if len(title) > MAX_TITLE:
        r.err("title", f"title is {len(title)} chars (max {MAX_TITLE})")
    if title.endswith("."):
        r.err("title", "title ends with a period")
    if re.match(r"^[A-Z][A-Z0-9]*-\d+\b", title):
        r.err("title", "title repeats the ticket ID — Linear already shows it")


def check_sections(t, r, canonical):
    sections = split_sections(t.get("description"))
    names = [n.strip() for n, _ in sections]
    lowered = [n.lower() for n in names]
    for want in canonical:
        n = lowered.count(want.lower())
        if n == 0:
            r.err("sections", f"missing section '## {want}'")
        elif n > 1:
            r.err("sections", f"section '## {want}' appears {n} times")
    present = [n for n in lowered if n in [c.lower() for c in canonical]]
    want_order = [c.lower() for c in canonical if c.lower() in present]
    if present != want_order:
        r.err(
            "sections",
            "sections out of order: expected "
            + " → ".join(canonical)
            + f", got {' → '.join(names) or '(none)'}",
        )
    return sections


def check_acceptance(sections, r, strict):
    lines = body_of(sections, "Acceptance criteria")
    if lines is None:
        return  # already reported by check_sections
    items = checklist_items(lines)
    if not items:
        r.err(
            "acceptance-criteria",
            "no checklist items under '## Acceptance criteria' (use `- [ ] …`)",
        )
        return
    thin = [i for i in items if len(i) < MIN_AC_CHARS]
    if thin:
        r.err(
            "acceptance-criteria",
            f"{len(thin)} acceptance criterion/criteria under {MIN_AC_CHARS} chars: "
            + "; ".join(repr(i) for i in thin[:3]),
        )
    if not any(CODE_SPAN_RE.search(i) for i in items):
        (r.err if strict else r.warn)(
            "ac-mechanical",
            "no acceptance criterion names a command, path, or endpoint in backticks — "
            "nothing here is mechanically checkable",
        )


def check_scope_and_tests(sections, r, strict):
    oos = body_of(sections, "Out of scope")
    if oos is not None and not bullets(oos):
        (r.err if strict else r.warn)(
            "out-of-scope", "'## Out of scope' has no bullets — the ticket has no scope fence"
        )
    plan = body_of(sections, "Test plan")
    if plan is not None:
        if not [ln for ln in plan if ln.strip()]:
            (r.err if strict else r.warn)("test-plan", "'## Test plan' is empty")
        elif not code_spans(plan):
            (r.err if strict else r.warn)(
                "test-plan", "'## Test plan' names no command in backticks"
            )


def check_pointers(sections, r, strict, repo_root):
    lines = body_of(sections, "Pointers")
    if lines is None:
        return
    # Existence is the authoritative signal; the heuristic only has to catch the
    # paths that do NOT exist, which is the case worth reporting.
    spans = [
        s for s in code_spans(lines)
        if looks_like_path(s) or path_exists(repo_root, s)
    ]
    if not spans:
        (r.err if strict else r.warn)(
            "pointers",
            "'## Pointers' names no file path in backticks — nobody read the codebase "
            "to write this ticket",
        )
        return
    missing = [s for s in spans if not path_exists(repo_root, s)]
    if missing:
        (r.err if strict else r.warn)(
            "pointers",
            f"{len(missing)} pointer path(s) do not exist: " + ", ".join(missing[:5]),
        )


def squash(text, limit=60):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def check_placeholders(t, r):
    text = t.get("description") or ""
    prose = prose_only(text)
    hits = {m.group(0) for m in DRAFT_RE.finditer(text)}
    hits |= {m.group(0) for m in MARKER_RE.finditer(prose)}
    if hits:
        r.err(
            "no-placeholders",
            "unfinished draft markers remain: "
            + ", ".join(sorted(hits))
            + " (backtick it if you mean the literal marker in the code)",
        )
    stubs = [squash(m.group(0)) for m in ANGLE_PROMPT_RE.finditer(prose)]
    if stubs:
        r.err(
            "no-placeholders",
            f"{len(stubs)} template prompt(s) left unfilled: "
            + "; ".join(stubs[:2]),
        )


def check_labels(t, r, config, skipped):
    labels = t.get("labels") or []
    if not isinstance(labels, list):
        r.err("labels", "labels must be a list of canonical keys")
        return []
    efforts = [x for x in labels if x.startswith("effort:")]
    tracks = [x for x in labels if x.startswith("track:")]
    provs = [x for x in labels if x.startswith("provenance:")]
    forbidden = [x for x in labels if FORBIDDEN_LABEL_RE.match(x)]

    if len(efforts) != 1 or efforts[0] not in EFFORTS:
        r.err("labels", f"need exactly one of {', '.join(EFFORTS)}; got {efforts or 'none'}")
    if not tracks:
        r.err("labels", "no `track:*` label")
    if len(provs) != 1:
        r.err("labels", f"need exactly one `provenance:*` label; got {provs or 'none'}")
    elif provs[0].split(":", 1)[1] not in PROVENANCE_CLASSES:
        r.err("labels", f"unknown provenance class in label {provs[0]!r}")
    if forbidden:
        r.err(
            "labels",
            "dispatcher-owned lifecycle labels must not be set by a session: "
            + ", ".join(forbidden),
        )

    ids = (((config or {}).get("linear") or {}).get("labels") or {}).get("ids")
    if not isinstance(ids, dict):
        skipped.add("labels-resolve")
    else:
        unknown = [x for x in labels if x not in ids]
        unresolved = [x for x in labels if x in ids and not ids[x]]
        if unknown:
            r.err("labels-resolve", "labels absent from linear.labels.ids: " + ", ".join(unknown))
        if unresolved:
            r.err(
                "labels-resolve",
                "labels present but unresolved (empty ID) in linear.labels.ids: "
                + ", ".join(unresolved)
                + " — run the board-setup step that resolves label IDs",
            )
    return provs


def check_links(t, r, provs):
    if not (t.get("projectId") or t.get("project")):
        r.err("project", "no project linked — the project holds the PRD and the tree")

    value = (t.get("provenance") or "").strip()
    label_class = provs[0].split(":", 1)[1] if len(provs) == 1 else None
    parent = (t.get("parentId") or t.get("parent") or "").strip()
    if not value:
        # Contract §5 rule 4: Linear stores the CLASS in the label and the ID in
        # the parent link — the full value is not a Linear field. So a ticket read
        # back from Linear has no `provenance` key, and the value is reconstructed
        # from what Linear does return.
        if label_class == "epic":
            if TICKET_ID_RE.match(parent):
                value = f"epic/{parent}"
            else:
                r.err(
                    "provenance",
                    "provenance:epic needs either a `provenance` value of `epic/<EPIC-ID>` "
                    "or a parent linked by ticket ID to reconstruct one from",
                )
        elif label_class:
            value = label_class  # the non-epic classes carry no ID
        else:
            r.err("provenance", "no provenance value and no provenance label to derive one")
    if value:
        if not PROVENANCE_RE.match(value):
            r.err(
                "provenance",
                f"{value!r} is not a contract §5 provenance value "
                "(epic/<ID>, monitor, review, retro-proposal, human)",
            )
        else:
            cls = "epic" if value.startswith("epic/") else value
            if label_class and cls != label_class:
                r.err(
                    "provenance",
                    f"provenance value {value!r} and label `provenance:{label_class}` disagree",
                )
            if cls == "epic":
                if not parent:
                    r.err("provenance", "provenance is epic/<ID> but no parent is linked")
                elif TICKET_ID_RE.match(parent) and parent != value.split("/", 1)[1]:
                    r.err(
                        "provenance",
                        f"parent {parent} is not the epic named in provenance {value!r}",
                    )


def check_state(t, r, config, skipped):
    if "stateId" not in t or t.get("stateId") in (None, ""):
        return
    states = ((config or {}).get("linear") or {}).get("stateIds") or {}
    raw = states.get("raw")
    if not raw:
        skipped.add("state")
        return
    got = t["stateId"]
    if got == raw:
        return
    if got == states.get("ready"):
        r.err(
            "state",
            "ticket is in the ready state — only a human moves a ticket to ready; "
            "file into the raw/backlog state",
        )
    else:
        r.err("state", f"stateId {got!r} is not linear.stateIds.raw ({raw!r})")


def check_ticket(t, index, canonical, config, strict, repo_root, skipped):
    if not isinstance(t, dict):
        r = Report(f"draft#{index + 1}", "")
        r.err("input", "ticket is not an object")
        return r
    ref = t.get("id") or f"draft#{index + 1}"
    r = Report(ref, (t.get("title") or "").strip())
    check_title(t, r)
    sections = check_sections(t, r, canonical)
    check_acceptance(sections, r, strict)
    check_scope_and_tests(sections, r, strict)
    check_pointers(sections, r, strict, repo_root)
    check_placeholders(t, r)
    provs = check_labels(t, r, config, skipped)
    check_links(t, r, provs)
    check_state(t, r, config, skipped)
    return r


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def load_tickets(paths):
    blobs = []
    if paths:
        for p in paths:
            try:
                blobs.append((p, open(p, encoding="utf-8").read()))
            except OSError as e:
                print(f"FAIL: cannot read {p}: {e}", file=sys.stderr)
                sys.exit(2)
    else:
        blobs.append(("<stdin>", sys.stdin.read()))
    tickets = []
    for name, raw in blobs:
        if not raw.strip():
            print(f"FAIL: {name} is empty — expected ticket JSON", file=sys.stderr)
            sys.exit(2)
        try:
            data = json.loads(raw)
        except ValueError as e:
            print(f"FAIL: {name} is not valid JSON: {e}", file=sys.stderr)
            sys.exit(2)
        if isinstance(data, dict) and "tickets" in data:
            data = data["tickets"]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
            print(
                f"FAIL: {name}: expected a ticket object, a list, or "
                '{"tickets": [...]}',
                file=sys.stderr,
            )
            sys.exit(2)
        tickets.extend(data)
    if not tickets:
        print("FAIL: no tickets to check", file=sys.stderr)
        sys.exit(2)
    return tickets


def print_text(result):
    for t in result["tickets"]:
        mark = "OK  " if t["ok"] and not t["warnings"] else ("FAIL" if not t["ok"] else "WARN")
        print(f"{mark}  {t['ref']}  {t['title']}")
        for f in t["errors"]:
            print(f"      error   [{f['rule']}] {f['message']}")
        for f in t["warnings"]:
            print(f"      warning [{f['rule']}] {f['message']}")
    s = result["summary"]
    if result["skipped_rules"]:
        where = (
            f"keys absent from {result['config']}"
            if result.get("config")
            else "no delivery.json config"
        )
        print(f"note: skipped ({where}): " + ", ".join(result["skipped_rules"]))
    print(
        f"{s['tickets']} ticket(s), {s['errors']} error(s), {s['warnings']} warning(s)"
        + ("" if result["ok"] else " — NOT ready")
    )


def run(tickets, canonical, config, strict, repo_root):
    skipped = set()
    reports = [
        check_ticket(t, i, canonical, config, strict, repo_root, skipped)
        for i, t in enumerate(tickets)
    ]
    return {
        "ok": all(not r.errors for r in reports),
        "strict": strict,
        "sections": canonical,
        "skipped_rules": sorted(skipped),
        "tickets": [r.as_dict() for r in reports],
        "summary": {
            "tickets": len(reports),
            "errors": sum(len(r.errors) for r in reports),
            "warnings": sum(len(r.warnings) for r in reports),
        },
    }


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
GOOD_DESCRIPTION = """## Context

Tokens refresh only after a 401, so every session shows one failed request.

## Acceptance criteria

- [ ] A token within 5 minutes of expiry refreshes before the request is sent
- [ ] `npm test` covers the near-expiry path and passes

## Out of scope

- Refresh-token rotation

## Test plan

- `npm test` — the new near-expiry cases

## Pointers

- `session.ts` — the reactive refresh this replaces
"""

GOOD_CONFIG = {
    "version": 1,
    "linear": {
        "stateIds": {"raw": "state-raw", "ready": "state-ready"},
        "labels": {
            "ids": {
                "track:platform": "lbl-track",
                "effort:S": "lbl-s",
                "effort:M": "lbl-m",
                "effort:L": "lbl-l",
                "provenance:epic": "lbl-prov",
                "agent:queued": "lbl-queued",
            }
        },
    }
}


def good_ticket():
    return {
        "id": None,
        "title": "Refresh tokens before expiry",
        "description": GOOD_DESCRIPTION,
        "labels": ["track:platform", "effort:M", "provenance:epic"],
        "projectId": "proj-1",
        "parentId": "ENG-100",
        "stateId": "state-raw",
        "provenance": "epic/ENG-100",
    }


def selftest():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    failures = []
    cases = [0]  # every assertion below increments it; the printed count is real

    def note():
        cases[0] += 1

    # 1. Drift: the doc's section list is the fallback list.
    from_doc = template_sections(root)
    note()
    if from_doc is None:
        failures.append(f"cannot parse sections out of {TEMPLATE_DOC}")
    elif from_doc != FALLBACK_SECTIONS:
        failures.append(
            f"section drift: {TEMPLATE_DOC} says {from_doc}, fallback says {FALLBACK_SECTIONS}"
        )

    canonical = from_doc or FALLBACK_SECTIONS

    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "session.ts"), "w").close()

        def check(ticket, strict=False, config=GOOD_CONFIG):
            return run([ticket], canonical, config, strict, tmp)

        def rules(res):
            t = res["tickets"][0]
            return sorted({f["rule"] for f in t["errors"]}), sorted(
                {f["rule"] for f in t["warnings"]}
            )

        def expect(name, ticket, want_errors, strict=False, config=GOOD_CONFIG):
            note()
            errs, _ = rules(check(ticket, strict, config))
            if errs != sorted(want_errors):
                failures.append(f"{name}: expected errors {sorted(want_errors)}, got {errs}")

        # 2. The good ticket is clean, with no warnings either.
        note()
        res = check(good_ticket())
        errs, warns = rules(res)
        if errs or warns:
            failures.append(f"good ticket not clean: errors={errs} warnings={warns}")
        note()
        if not res["ok"]:
            failures.append("good ticket reported not ok")

        # 3. One failure mode per rule.
        t = good_ticket(); t["title"] = "ENG-123 refresh tokens."
        expect("title", t, ["title"])

        t = good_ticket(); t["description"] = t["description"].replace("## Out of scope", "## Non-goals")
        expect("sections", t, ["sections"])

        t = good_ticket()
        t["description"] = t["description"].replace(
            "- [ ] A token within 5 minutes of expiry refreshes before the request is sent\n", ""
        ).replace("- [ ] `npm test` covers the near-expiry path and passes", "- [ ] works")
        expect("acceptance-criteria", t, ["acceptance-criteria"])

        t = good_ticket(); t["description"] += "\n- [ ] TODO decide this later\n"
        expect("no-placeholders", t, ["no-placeholders"])

        t = good_ticket(); t["description"] += "\n- Remove the `TODO` left in `session.ts`\n"
        expect("no-placeholders-backticked", t, [])

        t = good_ticket(); t["labels"] = ["track:platform", "effort:M", "provenance:epic", "agent:queued"]
        expect("labels-forbidden", t, ["labels"])

        t = good_ticket(); t["labels"] = ["effort:M", "provenance:epic"]
        expect("labels-track", t, ["labels"])

        t = good_ticket(); t["labels"] = ["track:platform", "effort:M", "effort:L", "provenance:epic"]
        expect("labels-effort", t, ["labels"])

        t = good_ticket(); t["labels"] = t["labels"] + ["track:unknown-track"]
        expect("labels-resolve", t, ["labels-resolve"])

        t = good_ticket(); t["projectId"] = None
        expect("project", t, ["project"])

        t = good_ticket(); t["parentId"] = None
        expect("provenance-parent", t, ["provenance"])

        t = good_ticket(); t["parentId"] = "ENG-999"
        expect("provenance-parent-mismatch", t, ["provenance"])

        t = good_ticket(); t["provenance"] = "monitor"
        expect("provenance-class", t, ["provenance"])

        t = good_ticket(); t["stateId"] = "state-ready"
        expect("state-ready", t, ["state"])

        t = good_ticket(); t["stateId"] = "state-elsewhere"
        expect("state-other", t, ["state"])

        # 4. Warning tier: warns by default, errors under --strict.
        t = good_ticket()
        t["description"] = t["description"].replace("- `session.ts` — the reactive refresh this replaces", "- nothing yet")
        note()
        errs, warns = rules(check(t))
        if errs or warns != ["pointers"]:
            failures.append(f"empty pointers: expected warning only, got errors={errs} warnings={warns}")
        expect("pointers-strict", t, ["pointers"], strict=True)

        t = good_ticket()
        t["description"] = t["description"].replace("`session.ts`", "`does/not/exist.ts`")
        note()
        errs, warns = rules(check(t))
        if errs or warns != ["pointers"]:
            failures.append(f"missing pointer path: got errors={errs} warnings={warns}")

        t = good_ticket()
        t["description"] = t["description"].replace("`npm test` covers", "the tests cover")
        note()
        errs, warns = rules(check(t))
        if errs or warns != ["ac-mechanical"]:
            failures.append(f"ac-mechanical: got errors={errs} warnings={warns}")

        t = good_ticket()
        t["description"] = t["description"].replace("- Refresh-token rotation", "")
        note()
        errs, warns = rules(check(t))
        if errs or warns != ["out-of-scope"]:
            failures.append(f"out-of-scope: got errors={errs} warnings={warns}")

        # 5. No config: config-dependent rules are skipped AND NAMED, not passed.
        t = good_ticket(); t["stateId"] = "state-ready"; t["labels"] = t["labels"] + ["track:whatever"]
        note()
        res = run([t], canonical, None, False, tmp)
        if not res["ok"]:
            failures.append("no-config run should not error on config-dependent rules")
        note()
        if res["skipped_rules"] != ["labels-resolve", "state"]:
            failures.append(f"no-config skips not named: {res['skipped_rules']}")

        # 6. A fenced `## ` inside the description is not a section heading.
        t = good_ticket()
        t["description"] += "\n```markdown\n## Pointers\n```\n"
        note()
        errs, _ = rules(check(t))
        if errs:
            failures.append(f"fenced heading miscounted as a section: {errs}")

        # 7. Pointer paths: containment, literal-before-glob, extensionless.
        os.makedirs(os.path.join(tmp, "src", "app", "[slug]"), exist_ok=True)
        open(os.path.join(tmp, "src", "app", "[slug]", "page.tsx"), "w").close()
        open(os.path.join(tmp, "Dockerfile"), "w").close()

        note()
        escapes = ["/etc/passwd", "../../../../etc/hosts"]
        if any(path_exists(tmp, e) for e in escapes):
            failures.append("path_exists escapes the repo root (absolute or ..)")

        note()
        if not path_exists(tmp, "src/app/[slug]/page.tsx"):
            failures.append("a literal path containing [] is treated as a glob and reported missing")

        t = good_ticket()
        t["description"] = t["description"].replace(
            "- `session.ts` \u2014 the reactive refresh this replaces",
            "- `Dockerfile` \u2014 the image this ships in",
        )
        expect("pointers-extensionless", t, [])

        t = good_ticket()
        t["description"] = t["description"].replace(
            "- `session.ts` \u2014 the reactive refresh this replaces",
            "- `/etc/passwd` \u2014 outside the tree",
        )
        expect("pointers-escape", t, ["pointers"], strict=True)

        # 8. A section left as the template's verbatim prompt is not "filled".
        t = good_ticket()
        t["description"] = t["description"].replace(
            "- Refresh-token rotation",
            "- <A thing a reasonable reader would assume is included, and is not.>",
        )
        expect("unfilled-template-prompt", t, ["no-placeholders"])

        note()
        prose = "Use `List<T>` and a <div> here; check if x < 5 and y > 3 holds."
        if ANGLE_PROMPT_RE.search(prose_only(prose)):
            failures.append(f"angle-prompt rule false-positives on: {prose}")

        # 9. Round trip: Linear returns the parent link and the label, never a
        #    `provenance` field — the value must be reconstructable from those.
        t = good_ticket(); t["id"] = "ENG-123"; t.pop("provenance")
        expect("round-trip-provenance", t, [])

        t = good_ticket(); t.pop("provenance"); t["parentId"] = None
        expect("round-trip-provenance-no-parent", t, ["provenance"])

    # 10. An unrecognized delivery.json version refuses; it does not guess.
    with tempfile.TemporaryDirectory() as tmp2:
        for version, should_exit in ((SUPPORTED_VERSION, False), (SUPPORTED_VERSION + 1, True), (None, True)):
            note()
            body = {"linear": {}} if version is None else {"version": version, "linear": {}}
            cfg = os.path.join(tmp2, "delivery.json")
            with open(cfg, "w") as fh:
                json.dump(body, fh)
            try:
                # the refusal writes to stderr; the selftest owns its own output
                with contextlib.redirect_stderr(io.StringIO()):
                    load_config(None, tmp2)
                exited = False
            except SystemExit:
                exited = True
            if exited != should_exit:
                failures.append(
                    f"delivery.json version={version!r}: expected exit={should_exit}, got exit={exited}"
                )

    if failures:
        print("FAIL: check_ticket_dor selftest")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK: check_ticket_dor selftest ({cases[0]} cases)")
    return 0


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", metavar="FILE", help="ticket JSON (default: stdin)")
    ap.add_argument("--strict", action="store_true", help="promote warnings to errors")
    ap.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    ap.add_argument("--config", help="path to delivery.json (default: <repo-root>/delivery.json)")
    ap.add_argument("--repo-root", default=".", help="root for pointer-path checks (default: .)")
    ap.add_argument("--selftest", action="store_true", help="run built-in fixtures and exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    repo_root = os.path.abspath(args.repo_root)
    canonical = template_sections(repo_root) or template_sections(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ) or FALLBACK_SECTIONS
    config, source = load_config(args.config, repo_root)
    result = run(load_tickets(args.files), canonical, config, args.strict, repo_root)
    result["config"] = source

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print_text(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
