---
name: plan-mvp
description: The greenfield half of planning — a working conversation that turns an empty repo into a real skeleton plus ADRs: pick the stack, argue the data model, scaffold, wire auth, first migration, one deployed page, CI green. Runs a foundation rubric over the decisions that are expensive to reverse, calibrated against over-engineering as hard as under-engineering. The human drives; this files no tickets and never merges.
argument-hint: <what you are building, one or two sentences>
allowed-tools: Bash(git rev-parse *) Bash(git ls-files *) Bash(grep *) Bash(wc *) Bash(tr *)
---

## Repo state (injected before you start)

- Branch: !`git rev-parse --abbrev-ref HEAD 2>/dev/null`
- Tracked files: !`git ls-files 2>/dev/null | wc -l | tr -d ' '`
- ADRs so far: !`git ls-files 'docs/adr/*.md' | grep -v README | wc -l | tr -d ' '`

## Instructions

`$ARGUMENTS` is what the human is building. This is **a conversation, not a factory** —
`/plan-epic` is the factory, and it only works once there is real code to read. Your job
here is to get from nothing to something real, making the small number of decisions that
are expensive to unmake *on purpose* instead of by accident.

This skill writes to the repo (scaffolding, ADRs), so **be on a feature branch** —
`<type>/<short-kebab-desc>`. The hooks block writes on `main`.

`allowed-tools` lists only the preflight commands injected above. This skill also
scaffolds, edits, runs the project's own commands, and calls `/new-adr`.

---

### 0. Am I the right skill?

If the repo already has a real codebase — meaningful source beyond scaffolding, a
schema, a test command that runs — say so and offer `/plan-epic` instead. Then **ask**;
do not refuse. The asymmetry with `/plan-epic` is deliberate: that skill *hard-refuses*
on a greenfield repo because it would silently produce tickets that look ready and are
not. Running this one on a mature repo is merely redundant, and sometimes the human
genuinely wants to re-decide a foundation. Redundant is a question; misleading is a
refusal.

### 1. How to run the conversation

- **One decision at a time.** Present at most three options with a recommendation named
  first and the reason in one sentence. Not a survey, not a matrix. The human picks.
- **Argue for the decision, not for the maximum.** If the honest answer is "the boring
  one, because nothing here justifies more", say that.
- **Record, then move.** Every rubric item ends in an ADR — `/new-adr <slug>` — including
  the ones you defer. Then go to the next item; do not reopen.
- **Build the smallest thing that proves the decision.** A decision nothing runs against
  is a preference.
- **Name the blast-radius action and get a yes before you take it.** This skill does not
  only advise — it scaffolds, installs, migrates, and deploys. Say what you are about to
  do, then wait, for each of: installing dependencies or a toolchain; generating more than
  a couple of files at once; creating anything on a hosting/database/auth provider (a
  project, a database, an account, a key); running a migration against anything that is
  not a local throwaway database; the first deploy; and changing CI. "I'll scaffold now,
  which adds ~40 files under `src/` and installs N packages — go ahead?" is the whole
  ritual. Reading, planning, and writing ADRs need no permission.
- **Never file tickets here** and never merge. When the skeleton is real, hand off.

### 2. The foundation rubric

Ten items. Each one is **decide and record** — and **"deferred, with this trigger to
revisit" is a first-class answer** that still gets its own ADR (`docs/adr/README.md`
already carries this convention). What must never happen is an expensive-to-reverse
choice made by accident.

| # | Decision | Boring default that is usually right | The expensive-to-reverse trap | Deferring it |
|---|---|---|---|---|
| 1 | **Auth model & sessions** | The platform's or framework's built-in auth; server-side sessions or its standard token flow | Rolling your own credential handling, or an identity model where "user" and "account" are the same row and later cannot be split | Fine to defer providers (SSO, social) with a trigger: *first user who asks for it*. Not fine to defer *where identity lives*. |
| 2 | **Tenancy & data isolation** | Row-level policies on every table **from row one**, even at one tenant | Application-layer-only filtering. Retrofitting isolation means auditing every query ever written, and the bug class it leaks is cross-tenant data | **Never deferred.** This is the one item with no deferral column. |
| 3 | **Migration discipline & rollback** | Ordered, checked-in migration files from migration one; forward-only, with a written rollback for anything destructive | Editing the schema by hand in a console. There is then no true schema history and no reproducible environment | The *tooling* can be minimal. The *discipline* starts with the first table. |
| 4 | **Secrets & environment separation** | Distinct dev/prod credentials from day one; secrets only in the platform's store; an example file in git listing names and never values | One credential used everywhere. A dev mistake becomes a production incident, and rotation means downtime | Fine to defer staging as a third environment (trigger: *the first change you are afraid to deploy*). Not fine to defer separating dev from prod. |
| 5 | **Observability seams** | Structured logs with a request/correlation ID, and one place errors are reported | `console.log` scattered through handlers, and no way to answer "what happened for that user at 14:02" | Fine to defer dashboards, tracing, and metrics stacks (trigger: *the second incident you cannot reconstruct*). Cheap now: put the seam in — one logger, one error sink. |
| 6 | **Deploy, healthcheck, rollback** | Push-to-deploy on the host you already have, one healthcheck endpoint, and a rollback you have actually run once | Never testing the rollback. A rollback path first exercised during an incident is a hypothesis | Fine to defer blue/green, canaries, and staged rollouts indefinitely at this size. Not fine to defer *having run* the rollback once. |
| 7 | **Testing strategy & the golden suite** | Name one command that must stay green, and what it covers: the few paths whose breakage means the product is broken | Coverage targets instead of a golden suite. Chasing a percentage produces tests that assert the implementation and rot on contact | Fine to defer E2E and load testing (trigger: *the first regression that shipped*). The golden command exists from the first feature — it is also the exit test in §4. |
| 8 | **Cost shape at 10× and 100×** | Write down what the bill is made of and which line grows superlinearly. That is the whole exercise at this stage | A per-request cost that only shows up at scale — an unbounded model call, an unindexed query on a growing table, per-user always-on infrastructure | Fine to defer every optimization. Not fine to defer *knowing which line explodes*: one paragraph, one ADR. |
| 9 | **Abuse surface** | Enumerate what is reachable without authentication; put a coarse rate limit in front of anything that costs money or sends mail | An unauthenticated endpoint that calls a paid API. Someone finds it, and the bill is the notification | Fine to defer quotas, per-user tiers, and bot detection (trigger: *the first traffic you did not expect*). Not fine to defer the enumeration. |
| 10 | **Accessibility & responsive baseline** | Semantic markup, real labels, visible focus, keyboard reachability, and one breakpoint that works on a phone | Building the whole UI on divs and click handlers, then retrofitting. Accessibility retrofits are rewrites | Fine to defer audits, contrast tooling, and a formal conformance target (trigger: *the first external user*). The baseline is a habit, not a project. |

### 3. Calibrate against over-engineering, not just under-engineering

This targets a solo developer. A rubric that pushes toward "maximal" hands a platform
team's infrastructure to an app with four users, and then the developer maintains the
infrastructure instead of the product. **Recommending the smaller thing is a correct
answer here**, and saying so plainly is part of the job.

Do not propose, unless the human's own constraints demand it and you can name the
constraint: Kubernetes or a service mesh · microservices · multi-region anything ·
event sourcing or CQRS · a self-hosted metrics/logging stack · a message queue for work
that fits in a request · a feature-flag platform · a custom auth implementation · a
monorepo toolchain for one app · caching layers before a measured slow query.

The counterweight, so this does not collapse into "do nothing": **items 2 and 3 are
never deferred.** Isolation and migration discipline are the two whose retrofit cost is
measured in audits of everything you have already written. Everything else on the list
can honestly be "not yet, and here is what will tell us it is time."

A deferral is only real when it names its trigger. "We'll add rate limiting later" is a
wish; "we add rate limiting when an endpoint that costs money becomes reachable without
auth, or at the first unexpected traffic spike" is a decision with a tripwire.

### 4. The milestone spine

Work through these in order. Each has a done-test you can actually run — a milestone
nobody ran is not done.

| Milestone | Done when |
|---|---|
| Pick the stack | An ADR names it and the two alternatives rejected, in one paragraph each |
| Argue the data model | The core entities and their relationships exist as a written schema, and the human has pushed back on it at least once |
| Scaffold | The app builds and starts locally with one documented command |
| Wire auth | A real user can sign in and out, and an unauthenticated request to a protected route is refused |
| First migration | The schema is created by a checked-in migration, applied to a clean database, and the isolation policies from item 2 are in it |
| One deployed page | A URL a stranger can open, served from the deployed app rather than a local process |
| CI green | The golden command from item 7 runs in CI and is required on PRs |

### 5. Handoff — and the exit test

`/plan-mvp` is done when `/plan-epic` would no longer refuse. That is not a metaphor:
its greenfield guard checks source volume, a schema/migrations presence, and a test
command that really runs and really executes tests. Run those same three checks and show
the human the numbers.

Then hand off: the ADRs written, the golden command, the deployed URL, and the sentence
that `/plan-epic <the next feature>` is now the right tool. If the project is adopting
the agentic delivery pipeline, that is also the moment to create `delivery.json` from
`delivery.example.json` (`docs/PIPELINE-CONTRACT.md` §1) — `/plan-epic` needs it.

Report what got decided, what got deferred **with each trigger**, and anything the human
declined to decide, named rather than quietly dropped.
