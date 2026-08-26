# A staging copy is floored with the path it mirrors

**Date:** 2026-08-25 · **Status:** Accepted · **Context:** `fix/kit-14-kit-1-guard-boundaries`, KIT-14. Amends [The grader floor gates what a change can do, not what the file is called](2026-08-25-grader-floor-gates-supervision-not-names.md), whose "Deliberately not changed" section left the PreToolUse floor narrow.

## Decision

For every path on a risk/grader floor, its **staging copy under `templates/` is on
the same floor.** Two mirrors exist today and both are now listed everywhere the
destination is:

| Staging copy | Becomes at bootstrap |
|---|---|
| `templates/workflows/**` | `.github/workflows/**` |
| `templates/hooks/**` | `.claude/hooks/**` |

Four floors change: the hook's `GRADER_PATH_FLOOR`, `check_delivery_config.py`'s
`REQUIRED_RISK_PATHS` (which also gains `.github/workflows/**` — see below),
`check_auto_approve.py`'s `RISK_PATH_FLOOR`, and `check_grader_paths.py`'s
`COVERED_DIRS` + `FLOOR` for the hooks mirror only.

## Why

The protection attached when a file became **visible**, not when its contents
were **decided**. A pinned session could edit `templates/workflows/pipeline-*.yml`
— the exact bytes that later run in CI holding repository secrets — without
tripping the scope fence, and the activation is then a pure `git mv` that reads
in review as "just a move". `templates/hooks/` was worse: it mirrors
`.claude/hooks/**`, the most heavily guarded directory in the kit, and it was
guarded in **zero** places, including the exhaustiveness ledger that exists to
stop exactly that.

Stating the rule as a *mirror relation* rather than listing globs is deliberate,
and is the amended ADR's own lesson applied one level out: it encoded its
reasoning as a naming convention and a convention is not a boundary. "Every
floored path's staging copy is floored" covers the next mirror without a patch.

### Amending the prior ADR

That ADR wrote: *"The PreToolUse hook's own `GRADER_PATH_FLOOR` … stays so. The
two are related, not mirrored: the hook blocks a pinned session's local edits,
this gates any PR from any author."* That distinction is real and is kept — the
two floors still differ. What it did not do is argue that staged bytes are safe
in a pinned session; it argued only about *populations*. On the question KIT-14
actually asks, it was silent, so this is an extension of it rather than a
reversal.

### What is deliberately NOT widened

`check_grader_paths.py`'s `FLOOR` keeps `templates/workflows/pipeline-*.yml`
rather than the whole directory. Its `UNGATED` ledger excuses the nine stack
templates as *"no supervision role"*, and applying the amended ADR's own
capability test — *can editing this alter what the pipeline is permitted to do
without a person seeing it?* — still answers **no** for `backup-cron.yml` and
`keepalive.yml`. That tier's cost is a human's label on every PR from every
author; the hook's cost is one escalation from one autonomous session. Different
prices justify different lines, and the ADR's measured 13% friction figure stands.

`templates/hooks/**` **is** added to that FLOOR, by the same test: its one file
becomes a `.claude/hooks/` script, and the directory sat outside `COVERED_DIRS`
entirely, so a new file dropped there got neither a glob nor the exhaustiveness
backstop.

### The active/staged pair is inseparable

`REQUIRED_RISK_PATHS` did not require `.github/workflows/**` at all. Requiring a
project to declare the *staged* workflows while leaving the *active* ones
undeclared would be an incoherent floor, so both halves land together.

### The accepted tradeoff

A project that deleted `templates/` at bootstrap (the documented path) carries
two globs matching nothing, which costs nothing. The project that **keeps** the
directory — adopting the pipeline in stages, holding `pipeline-*.yml` staged for
later — is precisely the at-risk case, and it is the one the floor now reaches.
Existing configs that already list the destinations get one new required line
each; `delivery.example.json` is updated in the same PR.

## Verified

- **Each new case fails without the change**, run against the unmodified hook via
  `HOOK_UNDER_TEST`: 5 battery cases red (`312/317`), naming staged workflow
  Edit, staged stack-template Edit, staged hook Write, `sed -i` on a staged
  workflow, redirect into a staged hook. With the candidate: `317/317`.
- **The auto-approve floor is the loudest failure**: without it, a ticket scoped
  to `templates/workflows/pipeline-review.yml` returns **APPROVE** — auto-promoted
  to `ready` with no person in the loop. Two cases, both red pre-fix.
- **The validator explains itself, and that is asserted.** Reverting
  `REQUIRED_RISK_PATHS` turns 3 cases plus 8 message assertions red; the message
  must name the staged glob, what it becomes, "same bytes" and "git mv", so it
  cannot decay into a generic "you omitted a glob".
- **The exhaustiveness ledger now covers the hooks mirror**: reverting
  `COVERED_DIRS` makes the staged-hook gating case red.
- **Over-reach is gated in both directions**: `templates/README.md` and
  `templates/scripts/**` stay editable by a pinned session; the unpinned
  fail-open and the pipeline-off allow are unchanged.
- Full gate green: hooks 317/317, delivery 92, approve 39, merge, dor,
  local-dispatch, graders, telemetry, dashboard, review, schemas, jsonschema,
  secretlint, placeholders, provenance.
