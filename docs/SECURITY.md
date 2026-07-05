# Security Model

> **Found a vulnerability in the kit itself** (a hook bypass, a guard gap)? Please
> open a [private security advisory](https://github.com/braedensc/claude-project-kit/security/advisories/new)
> rather than a public issue.

Distilled from todoclaw's production security posture (built 2026-06-23 → 2026-07-03,
live app + real API spend, zero leaks). Everything here was enforced, not aspirational.
(Citations like `ADR-00xx` / `SERVICES.md` / PR numbers refer to todoclaw-internal
artifacts — provenance breadcrumbs, not files in this repo.)

---

## The three layers

Three **independent** enforcement layers, ordered by when they fire and whether they can
be bypassed:

| Layer | Fires when | Bypassable? | Guards against |
|---|---|---|---|
| 1. Claude Code hooks (`.claude/hooks/`) | Claude attempts a tool call | **No** — enforced by the harness, not the model | Claude reading a secret, `rm -rf`, staging `.env`, embedding a key, editing on `main` |
| 2. Git pre-commit (`.husky/` + secretlint) | `git commit` | Yes (`--no-verify`) | A secret value reaching a commit; forbidden paths staged; commits on `main` |
| 3. CI + branch protection | PR targets `main` | **No** — server-side on GitHub | Everything above, re-checked; the real gate |

Two principles worth keeping verbatim:

> **Layer 1 is unique to Claude Code and the reason this kit exists: it can block a tool
> call — even a *read* — so a secret never enters the model's context in the first place.**

> **CI is the real guarantee — the local layers just catch mistakes early and cheaply.**

**"Bypass is earned by hooks."** `.claude/settings.json` ships
`permissions.defaultMode: bypassPermissions` ONLY because layer 1 hard-blocks the
dangerous operations in every permission mode. Remove or weaken the hooks and you must
remove the bypass too — they are one decision, not two.

**The hooks protect themselves.** A guard the agent can edit is theater — Claude could
delete the block or unwire it in `settings.json` the moment it hit one. So the hook
scripts and `settings.json` are **human-only**: the PreToolUse guard blocks Edit/Write
(and Bash mutations — `>`, `sed -i`, `cp`/`mv`/`rm`, `git checkout/restore`, inline
interpreters) targeting itself, `audit.py`, `stop-pr-check.py`, and `settings.json`.
Changing one is a human step — Claude prints a terminal command for you to run. This is
a first line, not a perfect sandbox (a shell can't be fully fenced by regex); the real
guarantee stays git: any change must survive a reviewed PR + CI, which re-runs the
battery against the committed hook. (Reads are always allowed.)

**Native `permissions.deny` — a layer independent of the Python hook.**
`.claude/settings.json` also carries platform deny rules
(`Read(.env)`, `Read(.env.local)`, `Read(.env.production)`, `Read(secrets/**)`,
`Read(*.pem)`, `Read(*.key)`, …) that Claude Code enforces itself. This matters: per
the docs, **deny wins even under `defaultMode: bypassPermissions`**, deny beats allow
across every settings scope, and deny rules aren't gated by the workspace-trust
dialog — so secret-file reads are blocked immediately, even if the PreToolUse hook
were removed. It's belt-and-suspenders with the hook, not a replacement (deny covers
built-in Read/Edit/Grep and recognized `cat`/`head` Bash reads, but not an arbitrary
subprocess opening the file — which the hook and OS sandboxing cover). The list
deliberately **enumerates** real env-file names rather than using a `Read(.env.*)`
wildcard: the wildcard would also catch `.env.example` — the one env file that's
*meant* to be read and edited — and deny rules can't express exceptions; exotic
`.env.foo` variants are still caught by the hook layer.

**The system fails closed — including when python3 itself is missing.** A
missing/broken hook *script* blocks every tool call (python exits 2 = the block
signal). A missing *interpreter* would have failed open (shell exit 127 is treated as
a non-blocking error), so the PreToolUse wiring guards it explicitly:
`command -v python3 … || exit 2` — no python3, no tools, rather than no guards.
Operationally: **create hook scripts before wiring `settings.json`** (see
docs/LESSONS.md — this kit hit both variants of that deadlock).

Server-side extras (free, one-time toggles in GitHub → Settings → Security): secret
scanning, **push protection**, Dependabot security updates. They backstop layer 3.

---

## The secrets model — three ISOLATED stores

A value must be set separately in every store that needs it. **Setting one does nothing
for the others** — todoclaw's deploys proved this repeatedly:

| Store | Examples | Set by | Feeds |
|---|---|---|---|
| Local env | `.env.local` (gitignored) | **Human only** — the hook blocks Claude from writing `.env*` and key-shaped values | Local dev server |
| CI secrets | GitHub → Settings → Secrets and variables → Actions | Human (`gh secret set` or dashboard) | Workflows (backup/deploy/keepalive) |
| Host env | `supabase secrets set …`, Vercel env vars | Human (CLI/dashboard) | Deployed functions / frontend build |

Rules that fall out of this:

- **Placeholders only in code.** `.env.example` (placeholder values) is the only env
  file ever committed; all three enforcement layers verify that.
- **Classify every var at birth** (ADR-0003): *public-by-design client value* (e.g. a
  Supabase anon key — shipped in the bundle, gated by RLS) vs *server secret* (service
  role, API keys — never in any frontend file or `VITE_*`-style var). Public values may
  still live in CI Secrets purely for log-masking hygiene.
- **Human-only steps are labeled.** Docs mark dashboard/CLI secret steps "(you, in
  dashboards)" — Claude cannot and should not do them; that boundary is the design.
- **Secrets in workflows stay in `env:`** referenced as `"$VAR"` — never inline, never
  with `set -x`/`--debug`, or the value leaks into logs.
- **MCP servers that carry tokens are user-scoped, never committed.** Register them
  with `claude mcp add --scope user …` (lives in `~/.claude.json`, OAuth on first use)
  so no token or server config lands in project files; collaborators run the same
  command on their own machines (todoclaw's Sentry MCP pattern).
- **Project-scoped MCP (`.mcp.json`, committed) references secrets only via `${VAR}`** —
  never hardcode a token in it (see `.mcp.json.example`). Env-var expansion (`${VAR}`,
  `${VAR:-default}`) is the supported form; a project `.mcp.json` server is also
  approval-gated on first use, so a committed config can't silently connect.
- **Autonomous/unattended runs** (`--dangerously-skip-permissions`) belong in a
  sandboxed devcontainer with an egress firewall — never on the host, never with
  `~/.ssh` mounted (`.devcontainer/README.md`).

---

## Database posture (Postgres/Supabase flavor — keep the shape for any datastore)

- **RLS deny-by-default on every table**: owner-scoped policies (`user_id = auth.uid()`
  in both `USING` and `WITH CHECK`), scoped `to authenticated`, `user_id` defaulting
  server-side. Once proven adversarially, **clone the pattern verbatim per table**.
- **No client hard-delete**: grant `select, insert, update` — never `delete` — and
  define no DELETE policy. Destruction is structurally impossible twice over (no grant
  AND no policy); the app soft-deletes (`deleted_at` UPDATE). Recovery for the common
  case never depends on backups.
- **Append-only logs**: audit/history tables get SELECT + INSERT only, denormalized
  columns, and **no FK to mutable rows** — record lifecycle and log lifecycle can never
  conflict.
- **Identity is never a parameter**: server-side functions run as the caller
  (`SECURITY INVOKER`) with the user id derived from auth context, so no caller can
  address another user's rows. Global system state (budget ledgers, caches) lives in
  tables with **no grants and no policies**, reachable only through narrow privileged
  RPCs — which is how the admin/service key stays out of application code entirely.
- **Prove it adversarially**: every policy shipped with a two-user proof (isolation,
  escalation blocked, hard-delete denied, soft-delete recoverable).

The PreToolUse hook completes the picture locally: destructive ops against a **remote**
database are blocked outright; the **local** (Docker) stack stays frictionless. Local is
disposable, production is irreplaceable.

---

## AI cost guardrails (the owner-key pattern)

For any app calling a paid AI API on the owner's key (from todoclaw ADR-0015/0017,
live with real spend):

1. **Server-side-only keys.** The API key is a platform function secret, read from
   server env. Never in the bundle, never in a client-visible var, never logged. The
   hook additionally blocks writing `sk-ant-…`-shaped values into any file.
2. **CORS origin-lock — never `*`.** Functions echo the request Origin only if it's in
   the `ALLOWED_ORIGIN` allow-list; otherwise no ACAO header and the browser blocks.
   Side effect worth keeping: preview-deploy URLs don't match the allow-list, so
   previews can't spend the owner's budget.
3. **Per-user rate limits** as append-only event rows counted over trailing windows
   (e.g. chat 30/hr, 100/day) — no mutable counter to race, no cron to reset.
4. **Global monthly budget kill-switch**: a ledger (one row per month, micro-dollar
   integers) capped at a constant (todoclaw: $20/month). When tripped, every AI endpoint
   refuses until the next month. Check order: **budget first** (cheap, no write — don't
   charge a rate-limit unit against a paused month), then rate limit (which records).
5. **Per-call output cap** (`max_tokens` small) bounds a single call; the kill-switch
   bounds the month.
6. **Agentic tool safety**: destructive tools are a **server-side** classification the
   model can't influence; the whole turn halts until human confirmation the model can
   neither see nor forge; every DB write goes through the caller's JWT client so RLS
   bounds prompt-injection blast radius; system prompts frame user data as *data, not
   instructions*; tool loops carry a hard iteration cap.
7. **Verify model IDs and pricing against live docs** before hard-coding cost math —
   post-cutoff models/prices change. Bias the math in the *conservative* direction (a
   kill-switch that can only trip early, never late).

---

## Runbooks

**Security incident** (alert from Dependabot / secret scanning / Sentry):
1. Assess severity.
2. Let Dependabot open the fix PR (or Claude bumps it); CI runs the full gate.
3. Review → merge → deploy.
4. **If a key leak is suspected, rotate immediately** (below).

**Key rotation:** rotate at the provider dashboard first, then update every store that
holds it (Actions secret / platform secret / `.env.local` — all three are isolated, so
walk the list). For a DB password: reset at the dashboard, update the backup/deploy
connection-string secret. App-facing anon keys are public by design and need no rotation
panic — RLS is the guard.

**Backup restore:** download the encrypted artifact →
`gpg --batch --passphrase "$BACKUP_GPG_PASSPHRASE" -d backup.sql.gpg > backup.sql` →
restore into a fresh/throwaway DB → verify row counts. Prove the full round-trip once
**before** you need it.

**Schema rollback is not `git revert`:** reverting a migration file only stops it
re-applying. Run the migration's hand-written `-- down:` block against prod, remove its
row from the migrations ledger; for data-lossy changes restore the backup (take an
on-demand backup *before* risky migrations).

---

## macOS keychain norms

Working rules for CLI tools that store tokens in the macOS keychain (Supabase CLI, gh,
etc.):

- **Know what the prompt means.** "<tool> wants to access key … in your keychain" =
  a CLI reading a token it stored earlier. Expected when *you* just ran (or asked
  Claude to run) a command that authenticates.
- **Announce before keychain-reading commands.** Claude should say a keychain dialog is
  expected *before* running such a command, so the prompt is never a surprise.
- **"Always Allow" is per-binary-signature.** Fine for tools you trust; note that a
  `brew upgrade` produces a new binary, so the keychain will re-prompt — expected, not
  suspicious (docs/LESSONS.md).
- **Never type your login password into a dialog you didn't expect.** If a keychain
  prompt appears with no command you can attribute it to, deny it and investigate.
