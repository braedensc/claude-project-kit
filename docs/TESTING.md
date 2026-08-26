# Testing Model

The pyramid as actually built and run in a production build (2026-06-23 → 2026-07-03;
135+ unit tests, 11-spec golden E2E suite, smoke in CI on every PR). Sources: that
build's testing ADRs, both Playwright configs, and the e2e harness.

---

## The pyramid

| Tier | Runner | Runs where | Needs |
|---|---|---|---|
| Unit + component | Vitest + React Testing Library (jsdom) | CI (required) + local | Nothing — hooks/data mocked |
| E2E smoke | Playwright, chromium-only | CI (**non-required** at first) | Nothing — dummy env, no DB |
| Golden-path E2E | Playwright, 2 projects (desktop + mobile) | **LOCAL ONLY** | Running local backend stack |
| Server pure logic | Runtime-native tests (e.g. `deno test`) colocated in functions | CI or local | Nothing — pure functions |

**The load-bearing split: smoke-in-CI vs DB-backed-golden-local-only.**

- CI smoke boots the app with **dummy, non-JWT env** injected by the Playwright
  `webServer.env` — no `.env.local`, no database. Logged-out app renders the sign-in
  form; that proves build + server + Playwright wiring. Cheap, fast, dependency-free.
- The golden suite drives the **real local stack** (auth → RLS → render → real
  interaction). It stays out of CI *by design*: booting the containerized backend in CI
  is slow, flaky, and burns minutes — and **a DB-backed suite must never point at prod
  or any shared remote**. The golden config enforces this: it resolves credentials from
  the *running local stack* at config-load time and **fails fast** ("run `supabase
  start`") rather than ever falling back to a remote or dummy DB.
- Two **separate config files** (not one config with projects) so CI can never
  accidentally pick up the DB suite; the smoke config additionally `testIgnore`s the
  golden directory.
- New browser jobs enter CI **non-required until proven stable** — a flaky browser run
  must not wedge `main`. Promote deliberately (merge-then-require, docs/LESSONS.md).

---

## Unit/component conventions

- Colocated `*.test.ts(x)` next to source; runner `include` scoped to `src/**` so it
  never collides with e2e (Playwright) or server-runtime code (Deno).
- `globals: false` — import test APIs explicitly; strict TS then needs no ambient types.
- jest-dom matchers + RTL `cleanup()` wired once in a setup file.
- Component tests mock the data hooks; **an env-validating module that throws at import
  is a test trap** — mock above it. If a test ever needs env values, use **non-JWT
  dummies** (the PreToolUse hook blocks writing `eyJ…`-shaped strings — by design).
- Magic constants (scoring weights, thresholds, style tiers) live in pure lib functions
  **pinned by boundary tests**, so a value change is a reviewed diff, not drift.

---

## The golden-path harness (the blueprint)

Every mechanism below transfers to any stack; the Supabase specifics are examples.

**Seeded auth via a setup project (not globalSetup).**
A fixed test user is created **out-of-band, idempotently** via the backend admin API
(match ONLY the specific "already registered" signal as success — swallowing every 4xx
hides real failures). A Playwright *setup project* (guarantees the dev server is up)
then drives the **real sign-in form** and saves `storageState` to a gitignored path —
driving the real form captures whatever storage shape the auth SDK uses; no hand-rolled
token JSON.

**Deterministic reset at superuser level.**
Per-test wipe deletes the test user's rows via a direct superuser connection — it must
bypass RLS *and* any append-only grants (app-level clients structurally can't clean an
append-only table). Table list is a hardcoded constant. The auth user row stays intact
so the persisted session remains valid. Forgetting one user-scoped table surfaces as a
second-run failure — add every new table to the wipe list.

**Serialized by contract, not hope.**
One shared test user + DB state ⇒ `workers: 1`, `fullyParallel: false`, retries 0 —
and a **runtime guard in the fixture** that throws if a CLI `--workers` override sneaks
in. Locale + timezone pinned (`en-US`/UTC). A UTC-midnight straddle guard sleeps past
midnight (extending the test timeout first) so day-keyed state can't flake once in a
blue moon.

**Semantic selectors survive restyles.**
Roles + accessible names + placeholders for anything semantic; `data-testid` only for
canvas-like surfaces with no semantics; assertions on `aria-current`/`aria-pressed`
state. Keep markup semantic (one `<nav>` across layouts) so the same helpers drive
desktop and mobile. Coordinates, when unavoidable, are viewport-independent fractions.

**Mock every paid/external API — deterministically.**
AI endpoints are route-mocked per spec with canned JSON and a canned **SSE stream**
(same wire format the server emits: `data: {json}\n\n`). Three details that bit:
- Answer OPTIONS preflights in mocks, and include every header the client actually
  sends (e.g. `x-client-info`) or the preflight rejects before the POST fires.
- Register a **catch-all escape detector first** (later-registered routes win), abort +
  record anything unmocked, and end specs with `expect(escapes()).toEqual([])` — the
  zero-real-spend proof.
- Index mock payloads by call order and **clamp to the last entry** — panels refetch
  more times than specs care to enumerate.

**Exclusive resources are named, not discovered.**
The golden suite is EXCLUSIVE across parallel sessions: one fixed test user, one dev
port. Smoke and golden servers get **different fixed ports** so they never collide;
extra manual dev servers take a third port. Before running the suite, confirm no other
session is mid-run (docs/COLLABORATION.md — serialized-resources list).

**Growth rule:** every feature PR grows the golden suite alongside the feature. One
"harness-proving" spec may keep its mechanics inline and annotated; everything else
uses the shared helpers.

---

## Workflows are code, and one of their failures is silent

> The design rule this section is one instance of lives in
> `docs/PIPELINE-CONTRACT.md` §13: *absence is not failure*. This section is about a
> failure GitHub reports silently; §13 is about the components that report silently on
> their own. Same symptom — a green run that did nothing — from opposite directions.

Every other failure in this document is loud: a red job, a failing assertion, an
annotation on the diff. **`startup_failure` is not.** When GitHub cannot build a run's
job graph, the run produces *no jobs, no API annotations and no check run*. The message
exists only on the run's HTML page. A PR's checks list shows nothing wrong — the
workflow simply appears not to have run, which is indistinguishable from a workflow
whose trigger didn't match. Nobody goes looking for a check that was never supposed to
be there.

The commonest cause in this kit's shape is the caller ⇄ callee permission cap. GitHub's
rule is that *"the `GITHUB_TOKEN` permissions passed from the caller workflow can be
only downgraded (not elevated) by the called workflow"* — so a job doing
`uses: ./.github/workflows/x.yml` while holding less than `x.yml` declares does not get
a quietly-narrowed token. The run refuses to start. That is exactly how
`pipeline-review.yml` shipped: a workflow-level `contents: read` meant `actions: none`,
the callee declared `actions: read`, and every review run died before its first job.

**The rule: the caller/callee contract is checked statically, on every PR.**
`scripts/check_workflow_calls.py` reads both files and verifies the permission cap, the
required inputs, the input types and the required secrets, for every local `uses: ./…`
in `.github/workflows/` and `templates/workflows/`. It runs in the kit's **Kit checks**
job and in the app template's **Provenance scan** job, needs no credentials, and works
in a repo where the pipeline is inert. Add a call site, and the check covers it the same
day.

### Why *not* a required activation smoke test

The obvious alternative — "after bootstrap, dispatch each workflow once and assert the
run reached its first job" — was considered and **deliberately not made a requirement**:

- **It is one-shot, and the defect is not.** It proves a workflow started on the day
  someone ran it. The next permissions edit reintroduces the bug, in silence, and no
  ritual fires. The static check runs on the PR that would cause it.
- **It detects after shipping; the gate detects at review.** A smoke test's earliest
  possible signal is on a live repo with credentials, i.e. after the change has landed
  somewhere. Everything it would catch here is decidable from two YAML files.
- **A manual step that only a human on a live repo can perform will decay** — and this
  defect is itself the proof that undetected things decay. Adding a ritual whose lapse
  is as invisible as the bug is not a control.
- **It is also not free**: dispatching pipeline workflows costs real sessions and real
  tracker writes.

What *is* worth keeping is the one-time **activation check** in the bootstrap runbook —
after `git mv`-ing the workflow templates into `.github/workflows/`, open the Actions
tab once and confirm each activated workflow has a run that produced jobs. It costs a
minute, catches the residue the static check cannot see (a trigger that never matches, a
missing secret at the org level), and it is a runbook line, not a gate. **No contract
amendment**: `docs/PIPELINE-CONTRACT.md` freezes shared *formats*, and this is a check,
not a format.

**Known limit, stated rather than implied.** The check verifies each hop independently;
a chain A → B → C is not verified transitively, because B's cap is A's grant and no
single hop declares that. The kit has no such chain today.

**Asked again, and the answer held.** A later sweep found four more components reporting
success while doing nothing, and re-opened the question: would an activation smoke test
have caught the `startup_failure`? It would — and it still is not worth requiring, for
the four reasons above, none of which the new evidence touched. Worse, a smoke test is
itself a component that can pass by doing nothing: a dispatch that never fires, a
credential that never resolves, an assertion that never runs all report green. Buying
one silent failure with another is not a trade. The general rule those five instances
share — *a component that can legitimately do nothing must distinguish "nothing to do"
from "could not do it", and must say which* — is `docs/PIPELINE-CONTRACT.md` §13, and it
constrains the next workflow at the point it is written rather than after it ships.

---

## What CI never does

- Never runs the DB-backed suite.
- Never receives real keys for tests — dummy env only.
- Never points any test at production. Prod verification is a **deliberate, scripted
  smoke after deploy** (reachability + auth + CORS), not a test suite.

---

## Evaluating AI/prompt surfaces

If the app has prompts, they're code — test them like code. Process rules distilled
from a production eval harness and its de-rot arc:

- **Prompt changes get measured, not eyeballed:** run the eval suite against a
  git-native baseline and compare — "the output looks better" is not a review.
- **Judge rubrics enumerate FAIL conditions only, with a default-pass judge.**
  Rubrics that grade quality (score warmth, count flourishes) are flaky; rubrics
  that name concrete failure conditions are stable.
- **Pin fixture clocks.** Date-relative fixtures rot silently as real time passes.
- **Dedicated non-prod eval API key** (e.g. `EVAL_ANTHROPIC_API_KEY`), and the
  harness hard-refuses any non-local backend — an eval run must never touch prod
  data or spend against the prod key.
- **Evals rot alongside the prompts they pin:** when prompts churn, schedule a
  de-rot pass over the suite in the same arc, not "someday".
