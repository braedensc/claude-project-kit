# templates/ — inert copies, activated at bootstrap

Files here are **deliberately not live** in the kit repo: the app-project
workflows would fail (or worse, run) against the bare kit, and the kit's own CI
must stay green. They become real when BOOTSTRAP-PROMPT.md moves them into
place in your new project.

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
| `workflows/claude.yml` | `.github/workflows/claude.yml` | Official `@claude` GitHub Action (v1) — mention `@claude` on an issue/PR; cost-capped (`--max-turns`, timeout). Needs the `ANTHROPIC_API_KEY` secret |
| `scripts/check-migrations.mjs` | `scripts/check-migrations.mjs` | PR-time ordered-file guard: duplicate / out-of-order versions vs the base tip (pairs with the deploy's `--include-all`) |
| `scripts/dev-worktree-login.sh` | `scripts/dev-worktree-login.sh` | Per-worktree env regeneration + dedicated test login (Supabase-flavored — port the pattern; delete if no local backend) |
| `hooks/session-start-provision-env.sh` | `.claude/hooks/session-start-provision-env.sh` | SessionStart auto-provisioning of a fresh worktree's env (idempotent; secret stdout discarded, never enters model context) |

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

Every template carries a header comment stating its provenance and
what was verified in production. Scheduled + secret-dependent jobs follow the
**preflight-skip-green** pattern: they merge before any secret exists and run
green-but-skipped until configured — a fork is never red out of the box. For
backup-cron / keepalive and the monitors that is permanent (a skipped run is
low-stakes and visible elsewhere); for **deploy-on-green it is lifecycle-bound**:
after your first successful deploy, flip its preflights to the fail-loud
variants commented inline — post-seed, a skipped deploy is a silent half-deploy
on a green run, and a green run is invisible to `pipeline-failure-alert.yml`.
