# Ecosystem parity: skills, native deny, MCP/devcontainer/@claude, SessionStart

**Date:** 2026-07-04 · **Context:** gap audit vs. the 2026 Claude Code template
ecosystem (Anthropic docs + awesome-claude-code + claude-code-templates + hooks-mastery)

## Decision

Close the ecosystem gaps the kit lacked, each verified against live docs before
shipping (the syntax is newer than the model's cutoff in several cases):

1. **Native `permissions.deny` in `settings.json`**, alongside the Python hooks —
   `Read(.env)`, `Read(.env.*)`, `Read(secrets/**)`, `Read(*.pem)`, `Read(*.key)`.
2. **Skills** (`.claude/skills/<name>/SKILL.md`, the current form after commands were
   folded into skills): `/ship` (user-only via `disable-model-invocation: true`) and
   `/new-adr`.
3. **Inert templates** for the pieces a project opts into: `templates/workflows/claude.yml`
   (the `@claude` Action, v1 `claude_args` syntax, cost-capped) and `.mcp.json.example`
   (project-scoped MCP, `${VAR}` secrets only).
4. **`.devcontainer/`** as a minimal official-feature container + a `README.md`
   pointing at Anthropic's hardened firewall reference — rather than vendoring their
   `init-firewall.sh`.
5. **An advisory `SessionStart` hook** (`session-start.py`) that injects repo
   orientation — **not** added to the self-protected set.

## Why

- **Deny is not redundant with the hook, and not a no-op under bypass.** The docs
  confirm `deny` hard-blocks even when `defaultMode` is `bypassPermissions`, wins over
  allow across all scopes, and isn't gated by the workspace-trust dialog. So it's a
  genuine second layer that survives the hook being removed — exactly the
  defense-in-depth the kit already preaches. (It doesn't cover arbitrary subprocesses;
  the hook and OS sandboxing do.)
- **Skills over commands, side-effecting ones locked.** `/ship` must never be
  auto-run, so `disable-model-invocation: true`; it also encodes the never-merge stop.
  This closes a currency gap — the kit shipped *no* skills and the ecosystem moved to
  `.claude/skills/` in 2026.
- **Inert-template pattern reused.** The `@claude` Action needs a secret and would run
  against the bare kit; `.mcp.json` would try to load. Shipping them under
  `templates/` / `.example` keeps the kit's own CI green and secret-free while still
  handing projects a correct, cost-capped starting point — same rationale as the
  existing workflow templates.
- **Devcontainer as pointer, not vendor.** Anthropic's firewall script is versioned and
  theirs to maintain; copying it in is drift debt. The minimal feature + a caveats
  README (no `~/.ssh`, non-root, the `--dangerously-skip-permissions` exfiltration
  warning) is the honest, low-maintenance shape.
- **Protect enforcers, not informers.** Self-protection exists to stop the agent
  deleting a *block* it's hitting. A SessionStart hook only injects context — the agent
  has no block to remove — so protecting it would add a terminal-handoff and a battery
  case for no threat-model gain. Documented as advisory.

## Verified

- Syntax for all six areas fetched from live docs by a parallel verification workflow
  (deny-under-bypass, `disable-model-invocation`, `!`-shell injection,
  `hookSpecificOutput.additionalContext`, the `@claude` v1 `claude_args` migration,
  `.mcp.json` `${VAR}` expansion) — quotes in the PR.
- Hook battery green at 92 (the PR also fixed the Stop hook's stale-local-main
  comparison — see the commit — with a regression case); all tracked JSON/YAML parse
  (devcontainer.json kept strict-JSON so CI validates it); placeholder integrity clean;
  kit CI green on the PR.
- The one self-protected change (settings.json: `permissions.deny` + SessionStart
  wiring) applied via the human-only terminal flow the kit mandates.

**Update (2026-07-04): `/ship` is model-invocable, not user-only.** The initial
decision locked `/ship` with `disable-model-invocation: true` (and so Claude couldn't
even *see* it). That was over-cautious: `/ship` does nothing Claude can't already do —
it commits, pushes, and opens a PR, which is the kit's documented default behavior, and
merging (the only irreversible step) stays hook-blocked. Packaging the routine as a
skill Claude can run makes shipping *more* reliable (it won't skip the watch-CI step).
`disable-model-invocation` is now reserved for genuinely irreversible/expensive skills
(a real deploy). `/new-adr` was already model-invocable.
