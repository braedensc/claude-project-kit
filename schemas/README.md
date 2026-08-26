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
| [`review-findings.schema.json`](review-findings.schema.json) | §14 review findings file | the review session | the review workflow's normalizer, then the bounce and merge tiers |

**Every one of them is enforced at write time, not only in CI.** A producer validates
what it is about to write and refuses to write a document that does not conform — see
*Generating a document* below. That is what stops a malformed document being something
somebody has to find later, remotely, after the session that could have fixed it ended.

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

`--schema` takes `delivery`, `pin`, `telemetry-block`, `safe-outputs` or
`review-findings`; `--list` prints the table above. A bare run validates this repo's own instances and the contract⇄schema
parity, and `--selftest` is what CI runs.

## Generating a document

**Validate before the write, never after.** A check that runs afterwards is a diagnosis:
the malformed `delivery.json` is already on disk and already failing the hook closed.
`scripts/emit_document.py` is the gate — it validates a candidate and writes the
destination only if the candidate conforms, so a refused document is never a file.

```bash
# a whole document — installed only if it conforms, otherwise nothing is written
python3 scripts/emit_document.py --schema delivery \
  --candidate cand.json --install delivery.json

# one §8 request — the RESULTING batch is validated before it replaces the old one,
# so a malformed request costs that request rather than the batch around it
python3 scripts/emit_document.py --append "$REQ"
```

A step that builds a document in memory calls `check_schemas.document_problems(doc,
name)` before writing it — one definition of "conforms", shared by both dispatchers and
the review emitter rather than hand-rolled per caller.

Where a **model** produces the document, the mechanism depends on how it hands it over,
and the two are not interchangeable:

- **The final assistant message is the document** — constrain it at generation. Claude
  Code headless takes `--json-schema <file>`; the Agent SDK takes
  `outputFormat: { type: 'json_schema', schema }`.
- **The model writes a file with a tool** — neither of those binds a tool call, so the
  document is refused at the boundary that reads it instead. §14's findings file is this
  case, and its gate is the review workflow's normalize step.

When the boundary lives under a ref an agent could have written, **stage the schema and
the validator from the default branch**: a PR that can loosen the schema its own review
is held to has not been reviewed.

None of this replaces the consumer-side rules above. It moves a whole class of malformed
document from "discovered at dispatch time, remotely, expensively" to "cannot be
produced".

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
