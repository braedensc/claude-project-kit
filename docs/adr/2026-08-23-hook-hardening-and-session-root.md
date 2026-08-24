# Hook hardening + subagent session-root fix

**Status:** Accepted

**Date:** 2026-08-23 · **Context:** an audit of lessons from continued production
use of the build this kit was distilled from, plus a cross-worktree-guard bug
confirmed by three independent subagent sessions during this initiative

## Decision

Port that production hook hardening into `pre-tool-use.py` and `settings.json`
(both self-protected, so they land via the human-terminal apply flow), and fix how
the hook resolves the acting session's root:

1. **Block reasons print to stderr** — for a blocking exit 2, Claude Code relays
   *only* stderr; printed to stdout, every deny surfaced as "hook error … No stderr
   output" and the reason was lost. The battery now asserts the stream.
2. **Secret-read guard is a path-target match**, not a reader-verb denylist —
   `xxd`/`od`/`strings`/`grep`/`base64`/`source`/`node -e` on `.env.local` all
   slipped the verb list. Word-boundary lookarounds exempt `process.env`, `obj.key`,
   and `.env.example`; `id_rsa` + `credentials` are covered in the Bash arm, the
   Read/Edit/Write arms, and two new native `permissions.deny` rows.
3. **Egress guard** — a network tool (`curl`/`wget`/`scp`/`sftp`/`nc`) targeting a
   non-allowlisted host *combined with* an exfil shape (`-d`/`--data*`/`-F`/`-T`/
   `-X POST|PUT|PATCH`, `@file` payload, `$VAR` spliced in the URL, or an
   `scp`/`nc` push) blocks; plain GETs stay allowed. The allowlist is a
   domain-boundary suffix match (universal core: localhost, `github.com`,
   `githubusercontent.com`, `anthropic.com`, `npmjs.org`) plus a fenced
   stack-specific slot (a managed-backend host pair as the worked example).
4. **`rm` short-flag runs are anchored** to the start of an argument token, so
   interior dashes in filenames (`rm build-for-prod.txt`) no longer false-block.
5. **Self-protection widened** — `settings.local.json` joins the protected set
   (local settings override project scalars; writing it could neutralize every
   guard), the Bash mutation net adds `git reset/clean/stash/apply/rm/mv`,
   `chmod`/`chown`/`awk`, and *any* interpreter invocation naming a protected path,
   and an unresolvable target path fails **closed**.
6. **Fail-closed dispatch** — guards run inside a boundary that converts an
   internal exception to exit 2 ("failing closed" on stderr). A crash previously
   exited 1, which Claude Code treats as *non-blocking* — the tool ran. Malformed
   harness stdin still fails open by design.
7. **Session root = `CLAUDE_PROJECT_DIR`, widened to the hook process's cwd only
   for a genuine subagent** — payload carries `agent_id` *and* the cwd's git
   toplevel shares a `--git-common-dir` with the anchor. Used by the
   cross-worktree guard *and* the branch checks.

## Why

Items 1–6 are straight parity: each failure was hit in production after the kit
forked, and every one is portable (no stack coupling). The kit keeps its own
guards intact — this is additive hardening, not a rewrite.

Item 7 fixes a real bug with a real hole. Hooks are invoked as
`python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/pre-tool-use.py`, and a subagent
session inherits `CLAUDE_PROJECT_DIR` from its **parent** — so a subagent in its
own SDK-created worktree ran the parent's hook file, whose `__file__`-derived root
was the parent's checkout. Consequences (all reproduced in the battery): every
Edit/Write into the subagent's *own* worktree false-blocked as "cross-worktree";
branch checks evaluated the *parent's* branch; and — the hole — a subagent writing
into the **parent's** checkout was judged same-worktree and sailed through.
The first draft of this fix derived the root from the hook process's cwd alone,
justified by `lsof` sampling that showed cwd following the acting session. Adversarial
review killed that: `lsof` shows the value at an instant, not that it is immutable, and
the CLI spawns the hook with the *current* session cwd while the Bash tool writes the
shell's resulting directory back into that state. A persisted `cd` therefore moves it —
so the draft let `cd ~/any-other-checkout` switch **off** the branch, branch-naming and
cross-worktree guards (all four regressions reproduced against real repos). Hence the
two-condition widening: `agent_id` proves a subagent, and the shared `--git-common-dir`
proves the cwd is a worktree of the *same repo*, so widening can never reach a foreign
checkout. The battery pins all five directions, and each new case fails against the
cwd-derived draft — the lesson being that a guard's *anchor* must not be something the
model can move.

Accepted tradeoffs:

- The egress guard is a conservative denylist of obvious shapes, not an egress
  firewall — the devcontainer + OS sandbox remain the real fence
  (docs/SECURITY.md). A `$VAR` URL or upload flag to an allowlisted host passes.
- The bare word `id_rsa` in a Bash command blocks even in innocent mentions
  (outside stripped `-m`/`--body` prose) — accepted; rephrase, or use the
  Read-tool-visible name. `credentials` and `*.key`/`*.pem` now require a
  path shape, so `git add src/credentials.test.ts` and `jq '.key'` stay allowed.
- A subagent's own worktree is trusted from its cwd, so a subagent that moved its
  cwd to a *sibling worktree of the same repo* would be judged there. Bounded on
  purpose: the `--git-common-dir` check keeps widening inside one repo, and every
  other guard (self-protection, `rm -rf`, secrets, egress, push-to-main,
  `gh pr merge`) is root-independent and still fires.
- Interpreter invocations *naming* a protected basename are blocked even against
  scratch copies (e.g. `python3 -m py_compile <scratch>/pre-tool-use.py`) —
  accepted; validate drafts under a different basename, or let the battery (which
  runs the hook as a subprocess) do the validating. Documented in CLAUDE.md and
  BOOTSTRAP-PROMPT.md.

Alternatives rejected: trusting `CLAUDE_PROJECT_DIR` over cwd (that *is* the bug —
subagents inherit it); passing the session root via settings-level env wiring
(settings.json is shared, same inheritance problem); dropping the cross-worktree
guard for subagents only (leaves the parent-checkout hole open).

## Verified

- Scratch battery (working-tree `test_hooks.py` pointed at the new hook):
  **143/143 checks pass** — 129 PreToolUse block/allow cases + 9 Stop-hook cases +
  5 stderr-reason assertions. (The summary line previously under-reported its own
  total by omitting the reason assertions; it now counts every check.)
- The same battery against the *old* hook fails exactly the new-behavior cases,
  proving non-vacuity — including "subagent Write into its OWN worktree" (old:
  false BLOCK) and "subagent Write into the PARENT checkout" (old: false ALLOW).
- The five cwd-attack cases were also run against the **cwd-derived-root draft**
  of this change: all five fail there and pass here, so CI now catches the
  regression that review caught by hand.
- Item-7 cases run the hook with controlled cwd + `CLAUDE_PROJECT_DIR` against a
  real parent-worktree/sibling-worktree/codename-worktree sandbox, both
  directions, plus the non-git-cwd fallback.
- `python3 -m py_compile` on the new hook; `json.load` on the new settings;
  `scripts/check_placeholders.py` unchanged-green.
