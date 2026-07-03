# Testing Model

The pyramid as actually built and run in todoclaw (2026-06-23 → 2026-07-03; 135+ unit
tests, 11-spec golden E2E suite, smoke in CI on every PR). Sources: ADR-0008/0011/0018/
0020, both Playwright configs, and the e2e harness.

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
  sends (todoclaw: `x-client-info`) or the preflight rejects before the POST fires.
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

## What CI never does

- Never runs the DB-backed suite.
- Never receives real keys for tests — dummy env only.
- Never points any test at production. Prod verification is a **deliberate, scripted
  smoke after deploy** (reachability + auth + CORS), not a test suite.
