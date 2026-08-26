#!/usr/bin/env python3
"""Keeps `schemas/` and `docs/PIPELINE-CONTRACT.md` from becoming two truths.

THE FAILURE THIS EXISTS TO PREVENT

    The contract was prose that agents READ and TRIED to follow, and every
    consumer re-implemented its rules by hand. Two independent implementations
    of one truth are free to drift — and they did: `/setup-board` shipped a
    `delivery.json` emitter that shared ZERO field names with §1, which would
    have bricked the repo it was setting up (the PreToolUse hook classifies a
    version-less config as BROKEN and fails closed, blocking every tool call).

    Fixing that instance fixed one bug. This script fixes the class: the schema
    is the machine-readable rendering of the contract, and CI fails if the two
    disagree about which fields exist.

WHAT IT CHECKS

  1. Every shipped schema is expressible by scripts/jsonschema_mini.py — no
     keyword that silently does nothing (`check_schema`).
  2. `delivery.example.json` validates against `schemas/delivery.schema.json`,
     so the example can never drift from the spec again.
  3. The malformed shape `/setup-board` used to emit is REJECTED — a pinned
     regression fixture for the bug that started this.
  4. Contract ⇄ schema parity, at the granularity each section's own rendering
     supports:
       §1 — EXACT, BIDIRECTIONAL, BY PATH. §1 documents `delivery.json` as one
            `### <section>` heading per top-level key with a `| Field |` table
            under it, so a full dotted path can be reconstructed for every row
            and compared against the schema's property paths. A field in one
            and not the other fails.
       §3, §4, §8 — BIDIRECTIONAL BY NAME. These sections mix tables with JSON
            examples and prose, so a path is not always reconstructable. Every
            field NAME in the section's `Field`/`Fields` tables must appear
            somewhere in the schema, and every property name in the schema must
            be named somewhere in the section — as inline code, or as a key in
            its canonical shape example. Weaker than §1's check, and honest
            about it: it still catches "the contract grew a field and the
            schema did not", which is the drift that hurts.
  5. Each schema accepts a valid fixture and rejects targeted malformations, so
     "the schema exists" never gets confused with "the schema has teeth".

Usage:
    check_schemas.py                          # validate this repo's own instances
    check_schemas.py --instance PATH --schema NAME
    check_schemas.py --list
    check_schemas.py --selftest

Exit: 0 = everything checked conforms
      1 = a schema, an instance, or contract⇄schema parity failed
      2 = usage/IO error
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonschema_mini as jsm  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_DIR = os.path.join(REPO_ROOT, "schemas")
CONTRACT = os.path.join(REPO_ROOT, "docs", "PIPELINE-CONTRACT.md")

# name -> (schema file, contract section, parity mode)
SCHEMAS = {
    "delivery": ("delivery.schema.json", "1.", "path"),
    "pin": ("pin.schema.json", "3.", "name"),
    "telemetry-block": ("telemetry-block.schema.json", "4.", "name"),
    "safe-outputs": ("safe-outputs.schema.json", "8.", "name"),
    "review-findings": ("review-findings.schema.json", "14.", "name"),
}

# Schema paths §1 documents in a row's PROSE rather than as rows of their own.
# One entry, and it earns its keep: §1 gives `budgets.perEffort` a single row
# whose Type cell reads "object keyed S/M/L, each { maxTurns, maxUsd,
# maxMinutes }". Reconstructing paths out of a prose cell would make the parser
# the fragile thing; naming the exception is cheaper and reviewable.
PATH_PARITY_EXEMPT_PREFIXES = ("budgets.perEffort.",)


# --------------------------------------------------------------------------- #
# Schema loading and property-path extraction
# --------------------------------------------------------------------------- #
def load_schema(name):
    filename = SCHEMAS[name][0]
    with open(os.path.join(SCHEMA_DIR, filename), encoding="utf-8") as handle:
        return json.load(handle)


def schema_paths(schema, root=None, prefix="", seen=None):
    """Every dotted property path a schema defines.

    Follows `$ref` and descends `oneOf` branches (a tagged union's fields are
    the union of its branches'). `additionalProperties` maps contribute no
    paths — their keys are per-project data, not contract fields.
    """
    if root is None:
        root = schema
    if seen is None:
        seen = set()
    paths = set()
    if not isinstance(schema, dict):
        return paths
    if "$ref" in schema:
        ref = schema["$ref"]
        marker = (ref, prefix)
        if marker in seen:  # a self-referential $ref would otherwise not terminate
            return paths
        seen = seen | {marker}
        schema = jsm._effective(schema, root)
    for key, sub in (schema.get("properties") or {}).items():
        child = f"{prefix}.{key}" if prefix else key
        paths.add(child)
        paths |= schema_paths(sub, root, child, seen)
    for branch in schema.get("oneOf") or []:
        paths |= schema_paths(branch, root, prefix, seen)
    if "items" in schema:
        paths |= schema_paths(schema["items"], root, prefix, seen)
    return paths


def schema_names(schema):
    """Every property NAME a schema defines, at any depth."""
    return {path.rsplit(".", 1)[-1] for path in schema_paths(schema)}


# --------------------------------------------------------------------------- #
# Contract parsing
# --------------------------------------------------------------------------- #
# Inline code only: a single-backtick span with no backtick on either side and
# no newline inside. A naive `` `([^`]+)` `` desynchronizes on the contract's
# ```` fences and then mis-pairs every span after them.
INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
# A JSON object key inside a fenced shape example. Matched with a regex rather
# than json.loads because §4's example carries a literal "...": "one row" row.
JSON_KEY_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
# A field path as the contract writes one, with any trailing `[]` dropped.
FIELD_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_*]*)*")


def read_contract():
    with open(CONTRACT, encoding="utf-8") as handle:
        return handle.read()


def section_text(text, number):
    """The `## <number> …` block, up to the next `## ` heading."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(f"## {number}"):
            start = index
            break
    if start is None:
        return None
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


def strip_fences(text):
    """Blank out fenced blocks so a `|` inside sample JSON is not read as a table.

    Lines are replaced rather than removed so nothing downstream needs to care
    about line numbers shifting.
    """
    out, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence is None and marker:
            fence = marker.group(1)[0]
            out.append("")
            continue
        if fence is not None:
            out.append("")
            if marker and marker.group(1)[0] == fence:
                fence = None
            continue
        out.append(line)
    return "\n".join(out)


def _rows(body):
    """Group consecutive `|`-leading lines into tables of split cells."""
    tables, current = [], []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            current.append([cell.strip() for cell in stripped.strip("|").split("|")])
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def table_fields(body):
    """Field names declared by the tables in `body`, in order.

    ONLY tables with a `Field` or `Fields` column count, and only that column is
    read. The contract is full of prose tables — §8's validation rules, §3's
    description-parsing rules, §4's session_mode→stage matrix — whose first cell
    is a sentence that happens to contain backticks. Reading those as field
    declarations is how a parity checker invents fields nobody wrote.

    One cell may declare several: §3 writes "| `branch`, `base_branch` |".
    """
    fields = []
    for table in _rows(body):
        if len(table) < 2:
            continue
        header = [cell.lower() for cell in table[0]]
        try:
            column = next(i for i, cell in enumerate(header) if cell in ("field", "fields"))
        except StopIteration:
            continue
        for row in table[1:]:
            if column >= len(row):
                continue
            cell = row[column]
            if not cell or set(cell) <= set("-: "):
                continue
            for token in INLINE_CODE_RE.findall(cell):
                token = token.strip().rstrip("[]")
                if FIELD_TOKEN_RE.fullmatch(token):
                    fields.append(token)
    return fields


def documented_paths_section1(text):
    """§1's documented field paths: one `### <section>` heading per top-level key.

    Returns (paths, sections). `### `version`` documents a field also called
    `version`, so section and field collapse to one path there.
    """
    body = section_text(text, "1.")
    if body is None:
        raise ValueError("contract §1 not found")
    body = strip_fences(body)

    blocks, current, buffer = [], None, []
    for line in body.splitlines():
        heading = re.match(r"^###\s+`([^`]+)`", line)
        if heading:
            if current:
                blocks.append((current, "\n".join(buffer)))
            current, buffer = heading.group(1), []
            continue
        if current:
            buffer.append(line)
    if current:
        blocks.append((current, "\n".join(buffer)))

    paths, sections = set(), []
    for section, block in blocks:
        sections.append(section)
        for field in table_fields(block):
            paths.add(section if field == section else f"{section}.{field}")
    return paths, sections


def documented_names(text, number):
    """Field NAMES a section's tables declare, for the by-name parity mode."""
    body = section_text(text, number)
    if body is None:
        raise ValueError(f"contract §{number} not found")
    names = set()
    for field in table_fields(strip_fences(body)):
        names.add(field.rsplit(".", 1)[-1])
    return names


def mentioned_names(text, number):
    """Every field name a section NAMES — the by-name reverse direction.

    Two sources, because the contract documents a field in one of two ways:
    as inline code in a table or a paragraph, or as a key in the canonical
    shape example. §3 only ever writes `budget.maxUsd` inside its JSON block;
    §4 only ever writes the envelope keys there. Reading just one source would
    report a documented field as undocumented.

    Deliberately generous — the schema's names have to be *mentioned*, not
    formally declared. A section that never types a field name at all has not
    documented it, and that is the whole assertion.
    """
    body = section_text(text, number)
    if body is None:
        raise ValueError(f"contract §{number} not found")
    tokens = set()
    for token in INLINE_CODE_RE.findall(strip_fences(body)):
        for part in re.split(r"[^A-Za-z0-9_.]+", token):
            for piece in part.split("."):
                if piece:
                    tokens.add(piece)
    tokens |= set(JSON_KEY_RE.findall(body))
    return tokens


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_all_schemas_expressible():
    problems = []
    for name in SCHEMAS:
        try:
            schema = load_schema(name)
        except (OSError, ValueError) as e:
            problems.append(f"{name}: cannot load — {e}")
            continue
        for problem in jsm.check_schema(schema):
            problems.append(f"{name}: {problem}")
    return problems


def check_parity():
    """Contract ⇄ schema, per section, at the granularity that section supports."""
    problems = []
    text = read_contract()

    # -- §1: exact, bidirectional, by path ---------------------------------- #
    schema = load_schema("delivery")
    documented, sections = documented_paths_section1(text)
    actual = schema_paths(schema)

    top_level = set(schema.get("properties", {}))
    for section in sections:
        if section not in top_level:
            problems.append(
                f"§1 documents a `### {section}` section that "
                f"schemas/delivery.schema.json has no top-level property for."
            )
    for key in sorted(top_level - set(sections)):
        problems.append(
            f"schemas/delivery.schema.json defines top-level `{key}` but §1 has "
            f"no `### {key}` section documenting it."
        )

    for path in sorted(documented - actual):
        problems.append(
            f"§1 documents `{path}` but schemas/delivery.schema.json does not "
            f"define it. Add the property, or drop the contract row — one "
            f"definition, two renderings."
        )
    for path in sorted(actual - documented):
        if path.startswith(PATH_PARITY_EXEMPT_PREFIXES):
            continue
        if any(other.startswith(path + ".") for other in documented):
            continue  # a container whose leaves §1 documents individually
        problems.append(
            f"schemas/delivery.schema.json defines `{path}` but §1 has no row "
            f"for it. Document it in the contract, or remove it — an "
            f"undocumented field is a second shape waiting to happen."
        )

    # -- §3, §4, §8: bidirectional, by name --------------------------------- #
    for name, (_, number, mode) in SCHEMAS.items():
        if mode != "name":
            continue
        schema = load_schema(name)
        actual = schema_names(schema)
        documented = documented_names(text, number)
        mentioned = mentioned_names(text, number)
        for field in sorted(documented - actual):
            problems.append(
                f"§{number.rstrip('.')} documents field `{field}` but "
                f"schemas/{SCHEMAS[name][0]} defines no such property."
            )
        for field in sorted(actual - mentioned):
            problems.append(
                f"schemas/{SCHEMAS[name][0]} defines `{field}` but "
                f"§{number.rstrip('.')} never names it."
            )
    return problems


def document_problems(document, name):
    """Every way an IN-MEMORY document fails schemas/<name>.schema.json.

    The one definition of "does this conform", so that every producer that has
    to answer it before writing — the two dispatchers, the safe-outputs
    appender, the review emitter — asks it the same way. Two hand-rolled copies
    of one check are two things free to drift, which is the entire reason these
    schemas exist rather than the prose alone.
    """
    schema = load_schema(name)
    return [f"{e['path'] or '<root>'} {e['message']} [{e['keyword']}]"
            for e in jsm.validate(document, schema)]


def validate_instance(path, name):
    """Validate one document ON DISK. Returns a list of human-readable problems."""
    with open(path, encoding="utf-8") as handle:
        instance = json.load(handle)
    return [f"{path}: {problem}" for problem in document_problems(instance, name)]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
# The shape `/setup-board` used to emit: no `version`, workflow states keyed by
# their DISPLAY NAME, label values as objects, and a handful of invented
# top-level fields. Pinned here because "we fixed the emitter" is not the same
# guarantee as "the schema refuses the shape".
SETUP_BOARD_REGRESSION = {
    "linear": {
        "teamKey": "ENG",
        "workspace": "acme",
        "states": {
            "Ideas": "8f1c0000-0000-0000-0000-000000000001",
            "Ready": "8f1c0000-0000-0000-0000-000000000002",
            "In Progress": "8f1c0000-0000-0000-0000-000000000003",
            "In Review": "8f1c0000-0000-0000-0000-000000000004",
            "Done": "8f1c0000-0000-0000-0000-000000000005",
        },
        "labels": {
            "agent:queued": {"id": "7de50000-0000-0000-0000-000000000001",
                             "name": "Agent Queued"},
        },
    },
    "organizationId": "0000-0000",
    "gitBranchFormat": "feat/{issueIdentifier}-{issueTitle}",
    "generatedBy": "setup-board",
    "generatedAt": "2026-08-24T00:00:00Z",
}

VALID_PIN = {
    "pin_version": 1,
    "dispatch_id": "d_01JAV8Q2S6R7X0M4KDNP3YHTZ9",
    "session_mode": "ticket",
    "worktree": "/abs/path/to/worktree",
    "branch": "feat/eng-123-token-refresh",
    "base_branch": "main",
    "auth_mode": "api-key",
    "budget": {"maxTurns": 60, "maxUsd": 6.0, "maxMinutes": 45, "attempt": 1, "of": 3},
    "ticket": {
        "id": "ENG-123",
        "team_key": "ENG",
        "url": "https://linear.app/acme/issue/ENG-123",
        "state_id": "2b400000-0000-0000-0000-000000000001",
        "effort": "M",
        "track": "track:platform",
        "provenance": "epic/ENG-100",
        "title": "Refresh tokens before expiry",
        "acceptance_criteria": ["A token within 5 minutes of expiry is refreshed"],
        "out_of_scope": ["Refresh-token rotation"],
        "snapshot_at": "2026-08-24T15:04:05Z",
    },
    "subject": None,
    "pinned_at": "2026-08-24T15:04:05Z",
    "pinned_by": "dispatcher:runner-01",
    "expires_at": "2026-08-24T17:04:05Z",
}

VALID_TELEMETRY = {
    "schema": "pipeline-telemetry/1",
    "runs": [{
        "run_id": "r_01JAV8Q2S6R7X0M4KDNP3YHTZ9",
        "dispatch_id": "d_01JAV8Q2S6R7X0M4KDNP3YHTZ9",
        "session_mode": "ticket",
        "ticket_id": "ENG-123",
        "team_key": "ENG",
        "stage": "dev",
        "model": "claude-opus-5",
        "auth_mode": "api-key",
        "started_at": "2026-08-24T15:04:05Z",
        "ended_at": "2026-08-24T15:34:05Z",
        "tokens_in": 120000,
        "tokens_out": 8000,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "cost_usd": 2.4213,
        "turns": 31,
        "outcome": "completed",
        "error_class": None,
        "files_changed": 4,
        "lines_added": 120,
        "lines_removed": 18,
        "pr_number": 42,
    }],
    "ticket_events": [
        {"ticket_id": "ENG-123", "event": "pr_opened",
         "at": "2026-08-24T15:30:00Z", "actor": "agent"},
    ],
}

VALID_SAFE_OUTPUTS = {
    "schema": "pipeline-safe-outputs/1",
    "requests": [
        {"type": "ticket-comment", "ticket_id": "ENG-123", "body": "Plan: …"},
        {"type": "ticket-state", "ticket_id": "ENG-123", "to": "review"},
        {"type": "ticket-label", "ticket_id": "ENG-123",
         "add": ["needs-design"], "remove": []},
    ],
}

VALID_REVIEW_FINDINGS = {
    "schema": "pipeline-review/1",
    "summary": "One real security finding; the rest of the diff matches the criteria.",
    "findings": [
        {"severity": "high", "category": "security", "file": "src/auth.ts", "line": 42,
         "summary": "The refresh path trusts an unverified `sub` claim.",
         "detail": "verify() is called with {ignoreExpiration: true}; a replayed token "
                   "reaches the session store. Verify before reading claims."},
        {"severity": "low", "category": "scope", "file": None, "line": None,
         "summary": "A drive-by formatting change the ticket did not ask for.",
         "detail": "Harmless, but it is how a small ticket becomes an unreviewable diff."},
    ],
}


def _without(document, *keys):
    copy = json.loads(json.dumps(document))
    node = copy
    for key in keys[:-1]:
        node = node[key]
    node.pop(keys[-1], None)
    return copy


def _with(document, path, value):
    copy = json.loads(json.dumps(document))
    node = copy
    keys = path.split(".")
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        node[int(keys[-1])] = value
    else:
        node[keys[-1]] = value
    return copy


# (schema name, fixture label, instance, must_be_valid, keyword expected when invalid)
FIXTURES = [
    ("pin", "valid", VALID_PIN, True, None),
    ("pin", "no-pin_version", _without(VALID_PIN, "pin_version"), False, "required"),
    ("pin", "future-pin_version", _with(VALID_PIN, "pin_version", 2), False, "const"),
    ("pin", "relative-worktree", _with(VALID_PIN, "worktree", "worktree"), False, "pattern"),
    ("pin", "uppercase-branch", _with(VALID_PIN, "branch", "feat/ENG-123-x"), False, "pattern"),
    ("pin", "local-time", _with(VALID_PIN, "expires_at", "2026-08-24T17:04:05+02:00"),
     False, "pattern"),
    ("pin", "unknown-session_mode", _with(VALID_PIN, "session_mode", "freestyle"), False, "enum"),
    ("pin", "null-ticket-allowed-by-shape", _with(VALID_PIN, "ticket", None), True, None),

    ("telemetry-block", "valid", VALID_TELEMETRY, True, None),
    ("telemetry-block", "wrong-marker",
     _with(VALID_TELEMETRY, "schema", "pipeline-telemetry/2"), False, "const"),
    ("telemetry-block", "no-runs", _without(VALID_TELEMETRY, "runs"), False, "required"),
    ("telemetry-block", "null-cache-counter",
     _with(VALID_TELEMETRY, "runs.0.tokens_cache_read", None), False, "type"),
    ("telemetry-block", "negative-counter",
     _with(VALID_TELEMETRY, "runs.0.turns", -1), False, "minimum"),
    ("telemetry-block", "stage-outside-enum",
     _with(VALID_TELEMETRY, "runs.0.stage", "deploy"), False, "enum"),
    ("telemetry-block", "agent-merged-event-is-shape-valid",
     _with(VALID_TELEMETRY, "ticket_events.0.actor", "agent"), True, None),

    ("safe-outputs", "valid", VALID_SAFE_OUTPUTS, True, None),
    ("safe-outputs", "unknown-request-type",
     _with(VALID_SAFE_OUTPUTS, "requests.0.type", "ticket-delete"), False, "const"),
    ("safe-outputs", "state-target-ready",
     _with(VALID_SAFE_OUTPUTS, "requests.1.to", "ready"), False, "enum"),
    ("safe-outputs", "state-target-done",
     _with(VALID_SAFE_OUTPUTS, "requests.1.to", "done"), False, "enum"),
    ("safe-outputs", "lifecycle-label-in-add",
     _with(VALID_SAFE_OUTPUTS, "requests.2.add", ["agent:needs-human"]), False, "pattern"),
    ("safe-outputs", "lifecycle-label-in-remove",
     _with(VALID_SAFE_OUTPUTS, "requests.2.remove", ["agent:blocked"]), False, "pattern"),
    ("safe-outputs", "hooks-change-label",
     _with(VALID_SAFE_OUTPUTS, "requests.2.add", ["hooks-change"]), False, "pattern"),
    ("safe-outputs", "empty-comment-body",
     _with(VALID_SAFE_OUTPUTS, "requests.0.body", "   "), False, "pattern"),
    ("safe-outputs", "oversized-batch",
     _with(VALID_SAFE_OUTPUTS, "requests",
           [VALID_SAFE_OUTPUTS["requests"][0]] * 21), False, "maxItems"),

    ("review-findings", "valid", VALID_REVIEW_FINDINGS, True, None),
    ("review-findings", "clean-review-is-an-empty-list",
     _with(_with(VALID_REVIEW_FINDINGS, "findings", []), "summary", ""), True, None),
    ("review-findings", "wrong-marker",
     _with(VALID_REVIEW_FINDINGS, "schema", "pipeline-review/2"), False, "const"),
    # THE finding this schema exists for. A severity outside the enum used to be
    # dropped from the list while the run still reported itself clean — a review
    # that had found something reporting that it had not.
    ("review-findings", "capitalized-severity",
     _with(VALID_REVIEW_FINDINGS, "findings.0.severity", "Critical"), False, "enum"),
    ("review-findings", "invented-severity",
     _with(VALID_REVIEW_FINDINGS, "findings.0.severity", "blocker"), False, "enum"),
    ("review-findings", "no-detail",
     _without(VALID_REVIEW_FINDINGS, "findings", 0, "detail"), False, "required"),
    ("review-findings", "empty-detail",
     _with(VALID_REVIEW_FINDINGS, "findings.0.detail", "  "), False, "pattern"),
    ("review-findings", "empty-finding-summary",
     _with(VALID_REVIEW_FINDINGS, "findings.0.summary", ""), False, "pattern"),
    ("review-findings", "line-zero",
     _with(VALID_REVIEW_FINDINGS, "findings.0.line", 0), False, "minimum"),
    # The workflow computes `usable`/`meets_threshold` itself. A model writing
    # them into its own file must not read as having decided anything.
    ("review-findings", "model-claims-usable",
     _with(VALID_REVIEW_FINDINGS, "usable", True), False, "additionalProperties"),
    ("review-findings", "no-findings-key",
     _without(VALID_REVIEW_FINDINGS, "findings"), False, "required"),
]


# --------------------------------------------------------------------------- #
def selftest():
    failures = []
    counted = [0]

    def note():
        counted[0] += 1

    note()
    failures += check_all_schemas_expressible()

    note()
    failures += check_parity()

    # The shipped example is the spec's own shape. This is the check that stops
    # delivery.example.json drifting away from §1 again.
    note()
    example = os.path.join(REPO_ROOT, "delivery.example.json")
    try:
        problems = validate_instance(example, "delivery")
    except (OSError, ValueError) as e:
        failures.append(f"delivery.example.json: {e}")
    else:
        failures += problems

    # The regression that started this stream.
    note()
    errors = jsm.validate(SETUP_BOARD_REGRESSION, load_schema("delivery"))
    if not errors:
        failures.append(
            "the malformed /setup-board shape (no version, states keyed by "
            "display name, label values as objects) VALIDATES against "
            "delivery.schema.json — the schema has no teeth"
        )
    else:
        reasons = {(e["path"], e["keyword"]) for e in errors}
        for want in (("version", "required"), ("linear.stateIds", "required")):
            if want not in reasons:
                failures.append(
                    f"malformed /setup-board shape: expected a {want[1]} failure "
                    f"at `{want[0]}`, got {sorted(reasons)}"
                )

    for name, label, instance, must_be_valid, keyword in FIXTURES:
        note()
        errors = jsm.validate(instance, load_schema(name))
        if must_be_valid and errors:
            failures.append(
                f"{name}/{label}: expected valid, got "
                f"{[(e['path'], e['keyword']) for e in errors]}"
            )
        elif not must_be_valid:
            if not errors:
                failures.append(f"{name}/{label}: expected REJECTION, schema accepted it")
            elif keyword and keyword not in {e["keyword"] for e in errors}:
                failures.append(
                    f"{name}/{label}: expected a {keyword} failure, got "
                    f"{[(e['path'], e['keyword']) for e in errors]}"
                )

    # The parity parser must be able to fail. A parser that silently finds
    # nothing reports parity between two empty sets forever.
    note()
    documented, sections = documented_paths_section1(read_contract())
    if len(sections) < 10 or len(documented) < 30:
        failures.append(
            f"§1 parser degraded: found {len(sections)} sections and "
            f"{len(documented)} field paths; the contract has far more"
        )
    note()
    for number, floor in (("3.", 10), ("4.", 15), ("8.", 5), ("14.", 6)):
        if len(documented_names(read_contract(), number)) < floor:
            failures.append(f"§{number.rstrip('.')} table parser found fewer than {floor} fields")

    # Two ways the parser could invent fields, both pinned:
    #   a fenced block that happens to contain table-shaped lines, and
    #   a prose table whose first cell merely quotes a field name.
    note()
    fenced = (
        "para\n\n```json\n{\n  \"a\": 1\n}\n```\n\n"
        "| Field | Type |\n|---|---|\n| `real` | string |\n| `also.real` | string |\n\n"
        "| Rule | Rationale |\n|---|---|\n| `raw`, `ready` are never targets | because |\n"
    )
    if table_fields(strip_fences(fenced)) != ["real", "also.real"]:
        failures.append(
            f"table_fields read something it should not: "
            f"{table_fields(strip_fences(fenced))}"
        )

    if failures:
        print("FAIL: check_schemas selftest")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"OK: check_schemas selftest ({counted[0]} checks, {len(FIXTURES)} fixtures)")
    return 0


def main():
    parser = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    parser.add_argument("--instance", help="a JSON document to validate")
    parser.add_argument("--schema", choices=sorted(SCHEMAS), help="which schema to validate against")
    parser.add_argument("--list", action="store_true", dest="list_schemas")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if args.list_schemas:
        for name, (filename, number, mode) in sorted(SCHEMAS.items()):
            print(f"{name:16} schemas/{filename:32} contract §{number.rstrip('.'):2} "
                  f"parity: by {mode}")
        return 0

    if args.instance or args.schema:
        if not (args.instance and args.schema):
            print("FAIL: --instance and --schema are used together", file=sys.stderr)
            return 2
        try:
            problems = validate_instance(args.instance, args.schema)
        except OSError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"FAIL: {args.instance} is not valid JSON: {e}", file=sys.stderr)
            return 1
        for problem in problems:
            print(f"  error  {problem}")
        print(f"{'FAIL' if problems else 'OK  '}  {args.instance} against "
              f"schemas/{SCHEMAS[args.schema][0]} ({len(problems)} error(s))")
        return 1 if problems else 0

    # Bare run: this repo's own tracked instances.
    problems = check_all_schemas_expressible() + check_parity()
    problems += validate_instance(os.path.join(REPO_ROOT, "delivery.example.json"), "delivery")
    live = os.path.join(REPO_ROOT, "delivery.json")
    if os.path.exists(live):
        problems += validate_instance(live, "delivery")
    for problem in problems:
        print(f"  error  {problem}")
    print(f"{'FAIL' if problems else 'OK  '}  schemas/ ⇄ docs/PIPELINE-CONTRACT.md "
          f"({len(problems)} problem(s))")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
