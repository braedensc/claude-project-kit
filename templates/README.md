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

Activation (done by the bootstrap session, not by hand):

```bash
git mv templates/workflows/*.yml .github/workflows/
# then: fill the {{…}} tokens (see PLACEHOLDERS.md), adapt the fenced
# STACK-SPECIFIC sections, and delete templates/ once empty.
```

Every template carries a header comment stating its todoclaw provenance and
what was verified in production. Scheduled + secret-dependent jobs all follow
the **preflight-skip-green** pattern: they merge before any secret exists and
run green-but-skipped until configured — a fork is never red out of the box.
