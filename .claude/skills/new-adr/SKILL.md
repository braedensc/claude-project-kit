---
name: new-adr
description: Scaffold a dated ADR (docs/adr/YYYY-MM-DD-slug.md, no numbers) and add its index row. Use when a decision changes the kit's shape, a guard, or the security model.
argument-hint: <short-kebab-slug> [one-line decision summary]
allowed-tools: Bash(git rev-parse *)
---

Today: !`date +%F`

## Instructions

Create an ADR following the kit's date-slug convention (docs/adr/README.md). `$0` is
the kebab-case slug; the rest of `$ARGUMENTS` is an optional one-line summary.

1. **File:** `docs/adr/<today>-$0.md` (the injected date above, e.g.
   `2026-07-04-<slug>.md`). No sequence number — that's the whole point of the
   convention (parallel sessions never collide on a counter).
2. **Body** — use this skeleton, filled from the summary + what you know:
   ```markdown
   # <Title>

   **Date:** <today> · **Context:** <branch / PR #>

   ## Decision
   <what, in 2–4 sentences>

   ## Why
   <forces, alternatives rejected by name, accepted tradeoffs>

   ## Verified
   <the proof it works — adversarial where possible; an ADR without evidence is an opinion>
   ```
3. **Index:** add one row to the table in `docs/adr/README.md`
   (`| [<Title>](<file>) | <today> | <one-line decision> |`).
4. You must be on a feature branch (the hooks block ADR writes on `main`). Do NOT
   commit — leave it staged for review, or hand off to `/ship`.
