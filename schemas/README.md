# `schemas/` — the contract, machine-readable

`docs/PIPELINE-CONTRACT.md` is the contract. **These files are the same contract in a
form a machine can enforce**, and `scripts/check_schemas.py` fails CI if the two ever
disagree about which fields exist. They are not a second source of truth; they are a
second *rendering* of the one that already existed.

| Schema | Defines | Written by | Read by |
|---|---|---|---|
| [`delivery.schema.json`](delivery.schema.json) | §1 `delivery.json` | human (bootstrap), `/setup-board` | dispatcher, hooks, skills, CI, collectors |
| [`pin.schema.json`](pin.schema.json) | §3 pin file | the dispatcher | hooks, validators |
| [`telemetry-block.schema.json`](telemetry-block.schema.json) | §4 telemetry block | the session agent, the review workflow | **the safe-outputs validator**, the collector, dashboards |
| [`safe-outputs.schema.json`](safe-outputs.schema.json) | §8 safe-outputs request file | the session agent | the safe-outputs validator |

`telemetry-block.schema.json` is the one that is enforced *at write time* as well as in
CI: `scripts/telemetry_block.py` validates a block against it inside the safe-outputs
validator, so a session's malformed telemetry is rejected while the session that wrote it
is still there to fix it, rather than being dropped a row at a time by the collector.

## Why they exist

A cross-stream review found `/setup-board` emitting a `delivery.json` that shared **zero
field names** with §1 — which would have bricked the repo it was setting up, since the
PreToolUse hook classifies a version-less config as BROKEN and fails closed on every tool
call. That instance is fixed. The *class* was that the contract was prose an agent read
and tried to follow, while every consumer re-implemented the same rules by hand: two
independent implementations of one truth, free to drift.

## Shape is not semantics — both layers stay

**A schema constrains shape, not meaning.** A schema-valid `delivery.json` can still name
a UUID that resolves to nothing, a `pinsRoot` inside a worktree, or a `perEffort` band
above the global cap. So every consumer-side check stays exactly where it was:

- the hook's **BROKEN** classification (§2) still fails closed on a config it cannot use;
- `scripts/check_delivery_config.py` still owns the semantic rules — resolution, on-disk
  containment, cross-field comparisons, the `riskPaths` floor, and `branch.types` against
  the **live** guard's own regex;
- the safe-outputs validator still compares every `ticket_id` against the
  **dispatcher-supplied** pinned ID, and still refuses `raw`/`ready`/`done` however the
  caller is configured.

Defense in depth. Conforming to a schema earns a document nothing.

## Validating a document

```bash
python3 scripts/check_schemas.py --instance delivery.json --schema delivery
```

`--schema` takes `delivery`, `pin`, `telemetry-block` or `safe-outputs`; `--list` prints
the table above. A bare run validates this repo's own instances and the contract⇄schema
parity, and `--selftest` is what CI runs.

## Generating a document

Where a model produces one of these, constrain it **at generation** rather than asking
for the shape in a prompt:

- **Claude Code headless** — `claude -p "…" --json-schema schemas/<name>.schema.json`
- **Agent SDK** — `outputFormat: { type: 'json_schema', schema }`

Where a shell script or a workflow step produces one, validate before the file is used:

```bash
python3 scripts/check_schemas.py --instance "$OUT" --schema safe-outputs || exit 1
```

Structured outputs and a validation step are not alternatives to the consumer-side rules
above. They move a whole class of malformed document from "discovered at dispatch time,
remotely, expensively" to "cannot be produced".

## The vendored validator

`scripts/jsonschema_mini.py` implements the draft 2020-12 subset these schemas use, in
stdlib only — the kit installs no Python packages for its guards, and a validator that
decides whether the pipeline may run is the last place to add a dependency.

Its one non-negotiable rule: **an unrecognized keyword is an error, never a silent
no-op.** `check_schema()` walks every shipped schema in CI and rejects any keyword it
cannot enforce, so "the schema says `minimum` and nothing checks it" is impossible by
construction. Adding a keyword to a schema means implementing it there first.

The schemas also carry `x-rule`, `x-tier`, `x-tier-<keyword>` and `x-fix` annotations.
Those are how `check_delivery_config.py` renders a violation in §7's own vocabulary —
which rule it belongs to, whether it is a MUST-fail or advisory, and the remediation
prose, which almost always lives in the contract's paragraphs rather than in a field
name. Adding a field to §1 and the schema therefore adds its rule name, tier and fix in
one place instead of three.
