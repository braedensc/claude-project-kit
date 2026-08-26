# templates/ — inert copies, activated at bootstrap

Files here are **deliberately not live** in the kit repo: the app-project
workflows would fail (or worse, run) against the bare kit, and the kit's own CI
must stay green. They become real when BOOTSTRAP-PROMPT.md moves them into
place in your new project.

**One exception:** the kit runs its own adapted, *active* copy of
`pr-conflict-monitor.yml` at `.github/workflows/` — parallel PRs off one `main`
make the conflict hazard real in this repo too. The template below stays inert
and unchanged; app projects still activate it the normal way.

| Template | Activates to | What it is |
|---|---|---|
| `workflows/ci.yml` | `.github/workflows/ci.yml` (replacing the kit's own) | App CI: secret-scan + forbidden paths, lint, typecheck, test, non-required e2e smoke |
| `workflows/deploy-on-green.yml` | `.github/workflows/deploy-on-green.yml` | Deploy-on-green: `workflow_run` gate → migrate → deploy, tree-derived targets + all-function smoke, with every production lesson inline |
| `workflows/pipeline-failure-alert.yml` | `.github/workflows/pipeline-failure-alert.yml` | `workflow_run` failure on main → one deduped issue, owner @mention+assign (email + phone push); post-merge failures are otherwise silent |
| `workflows/backup-cron.yml` | `.github/workflows/backup-cron.yml` | Daily encrypted `pg_dump` → artifact, with the IPv6/pooler/role gotchas inline |
| `workflows/keepalive.yml` | `.github/workflows/keepalive.yml` | Free-tier anti-pause ping (401-is-healthy pattern) |
| `workflows/pr-conflict-monitor.yml` | `.github/workflows/pr-conflict-monitor.yml` | Flags PRs whose merge state goes DIRTY — conflicted PRs skip required CI and can look green |
| `workflows/frontend-uptime.yml` | `.github/workflows/frontend-uptime.yml` | Synthetic probe of the user-facing app (status + app-shell marker, blip-tolerant) for surfaces deployed outside the pipeline |
| `workflows/migration-drift.yml` | `.github/workflows/migration-drift.yml` | Daily read-only declared-vs-applied compare against prod → issue; catches drift however it arises |
| `workflows/cron-health.yml` | `.github/workflows/cron-health.yml` | Monitors the downstream EFFECT of in-platform scheduled jobs — schedulers self-report success even when the work fails |
| `workflows/claude.yml` | `.github/workflows/claude.yml` | Official `@claude` GitHub Action (v1) — mention `@claude` on an issue/PR; cost-capped (`--max-turns` + `--max-budget-usd`, timeout). Needs the `ANTHROPIC_API_KEY` secret |
| `workflows/pipeline-dispatch.yml` | `.github/workflows/pipeline-dispatch.yml` | **Pipeline**: polls the queue → claims a ticket atomically → pins it outside the worktree → proves the rails loaded with a canary → runs the session. Capacity/budget/WIP/attempt caps |
| `workflows/pipeline-safe-outputs.yml` | `.github/workflows/pipeline-safe-outputs.yml` | **Pipeline**: reusable validator that holds the tracker credential *so the agent job never does*; checks the session's write-requests against the pin, all-or-nothing (`docs/PIPELINE-CONTRACT.md` §8) |
| `workflows/pipeline-review.yml` | `.github/workflows/pipeline-review.yml` | **Pipeline**: `opened`-only AI review against the dispatch-time snapshot. Comment-only, structurally unable to approve, never a required check |
| `workflows/pipeline-bounce.yml` | `.github/workflows/pipeline-bounce.yml` | **Pipeline**: wraps `/fix-ci` when findings meet the severity threshold. One bounce = one workflow run, counted by Actions run id, capped by `budgets.maxBounces`; `budgets.fixIterations` bounds the cycles *inside* the fix session |
| `scripts/check-migrations.mjs` | `scripts/check-migrations.mjs` | PR-time ordered-file guard: duplicate / out-of-order versions vs the base tip (pairs with the deploy's `--include-all`) |
| `scripts/dev-worktree-login.sh` | `scripts/dev-worktree-login.sh` | Per-worktree env regeneration + dedicated test login (Supabase-flavored — port the pattern; delete if no local backend) |
| `hooks/session-start-provision-env.sh` | `.claude/hooks/session-start-provision-env.sh` | SessionStart auto-provisioning of a fresh worktree's env (idempotent; secret stdout discarded, never enters model context) |

The four `pipeline-*.yml` templates are the **optional agentic delivery
pipeline** and are inert twice over: they no-op entirely unless a project has a
`delivery.json` at its root (`docs/PIPELINE-CONTRACT.md` §2). Move them only if
you are adopting that pipeline; a project without one should delete them along
with `delivery.example.json`. `pipeline-safe-outputs.yml` is referenced by path
from the other two, so it must keep its filename after activation.

Rows for files a sibling PR of the 2026-08 parity-port initiative adds
(`pr-conflict-monitor`, `frontend-uptime`, `migration-drift`, `cron-health`,
`hooks/session-start-provision-env.sh`) land with those PRs; the table is the
single index for all of them.

Activation (done by the bootstrap session, not by hand):

```bash
git rm .github/workflows/ci.yml           # the kit's own CI yields to the app CI first
git mv templates/workflows/*.yml .github/workflows/
git rm -r templates/                      # one-way: this README goes too
# then: fill the {{…}} tokens (see PLACEHOLDERS.md) and adapt the fenced
# STACK-SPECIFIC sections.
```

**Activation check — one minute, once.** After the first PR that exercises them, open
the Actions tab and confirm each activated workflow has a run **that produced jobs**. A
run can end as `startup_failure` before its first job — no jobs, no annotations, no
check run — and a PR's checks list then looks exactly like a workflow that simply wasn't
triggered. `npm run lint:workflow-calls` catches the caller/callee half of that class on
every PR; this eyeball catches the rest (a trigger that never matches, an org-level
secret that isn't there). See `docs/TESTING.md` §
*Workflows are code, and one of their failures is silent*.

Every template carries a header comment stating its provenance and
what was verified in production. Scheduled + secret-dependent jobs follow the
**preflight-skip-green** pattern: they merge before any secret exists and run
green-but-skipped until configured — a fork is never red out of the box. For
backup-cron / keepalive and the monitors that is permanent (a skipped run is
low-stakes and visible elsewhere); for **deploy-on-green it is lifecycle-bound**:
after your first successful deploy, flip its preflights to the fail-loud
variants commented inline — post-seed, a skipped deploy is a silent half-deploy
on a green run, and a green run is invisible to `pipeline-failure-alert.yml`.
