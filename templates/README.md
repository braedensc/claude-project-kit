# templates/ — inert copies, activated at bootstrap

Files here are **deliberately not live** in the kit repo: the app-project
workflows would fail (or worse, run) against the bare kit, and the kit's own CI
must stay green. They become real when BOOTSTRAP-PROMPT.md moves them into
place in your new project.

| Template | Activates to | What it is |
|---|---|---|
| `workflows/ci.yml` | `.github/workflows/ci.yml` (replacing the kit's own) | App CI: secret-scan + forbidden paths, lint, typecheck, test, non-required e2e smoke |
| `workflows/deploy-on-green.yml` | `.github/workflows/deploy-on-green.yml` | Deploy-on-green: `workflow_run` gate → migrate → deploy, with every production lesson inline |
| `workflows/backup-cron.yml` | `.github/workflows/backup-cron.yml` | Daily encrypted `pg_dump` → artifact, with the IPv6/pooler/role gotchas inline |
| `workflows/keepalive.yml` | `.github/workflows/keepalive.yml` | Free-tier anti-pause ping (401-is-healthy pattern) |
| `workflows/claude.yml` | `.github/workflows/claude.yml` | Official `@claude` GitHub Action (v1) — mention `@claude` on an issue/PR; cost-capped (`--max-turns`, timeout). Needs the `ANTHROPIC_API_KEY` secret |
| `scripts/dev-worktree-login.sh` | `scripts/dev-worktree-login.sh` | Per-worktree env regeneration + dedicated test login (Supabase-flavored — port the pattern; delete if no local backend) |

Activation (done by the bootstrap session, not by hand):

```bash
git rm .github/workflows/ci.yml           # the kit's own CI yields to the app CI first
git mv templates/workflows/*.yml .github/workflows/
git rm -r templates/                      # one-way: this README goes too
# then: fill the {{…}} tokens (see PLACEHOLDERS.md) and adapt the fenced
# STACK-SPECIFIC sections.
```

Every template carries a header comment stating its todoclaw provenance and
what was verified in production. Scheduled + secret-dependent jobs all follow
the **preflight-skip-green** pattern: they merge before any secret exists and
run green-but-skipped until configured — a fork is never red out of the box.
