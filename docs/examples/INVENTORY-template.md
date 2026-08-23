# INVENTORY.md — stack, accounts & secrets tracker (template)

Copy to `docs/INVENTORY.md` at bootstrap and fill the tables. One at-a-glance,
**value-free** map of the whole stack — every service, every account, every secret and
config variable **by name**, and the automated processes that tie them together — built
for audits, key rotation, and onboarding a fresh environment. Table-first on purpose: a
roster of names and locations resists drift far better than narrative docs.

**The one rule: names and locations only — no secret _values_ ever live here** (or
anywhere in git). Real values stay in the secret stores listed below.

---

## 1. Production coordinates

| Thing | Value | Notes |
|---|---|---|
| Frontend (prod) | | hosting tier, domain |
| Backend (prod) | | project ref / region |
| Repo | | public/private |
| Custom domain | | or "none — provider subdomains" |
| Local dev backend | | ports, how it starts |

## 2. Services & accounts

| Service | What it does for this app | Account / login | Dashboard | Tier |
|---|---|---|---|---|
| | | | | |

## 3. Secrets & config variables — grouped by store

One subsection per store (hosting env vars, backend secrets, CI secrets/vars,
platform-injected, local-only), each a table:

| Variable | Kind (secret / public / config) | Service | Purpose / unset behavior |
|---|---|---|---|
| | | | |

Close the section with two rollups:

- **One value, two names** — the same value under different names across stores, so
  nobody double-counts (or rotates only one alias).
- **The true secrets** — the short list to rotate immediately if exposed.

## 4. Automated processes

### 4a. Scheduled jobs

| When (UTC) | Workflow | Does | Config it needs |
|---|---|---|---|
| | | | |

### 4b. Event-driven pipelines

| Workflow | Trigger | Does |
|---|---|---|
| | | |

## 5. Cost & billing posture

| Service | Tier | Can it bill? | Bound / kill-switch |
|---|---|---|---|
| | | | |

## 6. "Where do I change X?" quick reference

| X | Where |
|---|---|
| | |

---

Keep it current: fix any row a change makes stale in the same PR. For apps with real
quotas or caps, a sibling `LIMITS.md` (every quantitative limit by layer, citing file +
constant *name* rather than line numbers so it ages gracefully) is the same pattern
applied to boundaries.
