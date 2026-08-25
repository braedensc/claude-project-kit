#!/usr/bin/env bash
# SessionStart hook TEMPLATE: auto-provisions THIS worktree's .env.local + a
# dedicated local login the first time a session starts here, by invoking the
# committed provisioning script (the kit ships it as
# templates/scripts/dev-worktree-login.sh -> scripts/dev-worktree-login.sh at
# bootstrap). Removes the need for a human to run that script manually every
# time a new worktree spins up (learned in production, 2026-07-04 — a fresh
# worktree hit the "no .env.local yet" dialog and had to be unblocked by hand).
#
# INERT IN THE KIT — activate at bootstrap by copying to
# .claude/hooks/session-start-provision-env.sh (keep it executable) and wiring
# it under SessionStart in .claude/settings.json, as a second entry beside any
# orientation hook. Safe with no matcher since it no-ops once provisioned; give
# it a generous timeout — the provisioning script talks to the local stack:
#
#   { "hooks": [ { "type": "command",
#       "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-start-provision-env.sh",
#       "timeout": 60 } ] }
#
# No-ops silently (no error, no output) when:
#   - .env.local already exists (already provisioned, or not a fresh worktree)
#   - this isn't one of this project's worktrees (backend config absent, or no
#     provisioning script)
#   - the local backend stack isn't running yet — legitimate; don't block
#     session startup on it, and don't nag every session until it is.
#
# SECURITY RATIONALE (preserved from the original production script): this is a plain,
# reviewed shell script the harness runs on a lifecycle event — not a Claude
# tool call — so it isn't subject to the separate PreToolUse guard that blocks
# Claude from writing .env files or key-shaped values (that guard exists to
# keep the *model* from ever handling raw secrets in its own reasoning; this
# script's secret values never pass through Claude's context either way, same
# as when a human runs dev-worktree-login.sh directly).
#
# The provisioning script's stdout (which echoes keys + a dev password) is
# discarded to /dev/null — never written to a log file or surfaced to the
# model. The one line emitted back is a plain systemMessage naming the login
# email only.
set -u

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

[ -f .env.local ] && exit 0
[ -x ./scripts/dev-worktree-login.sh ] || exit 0

# ── STACK-SPECIFIC: project-detection + stack-up guards — swap at bootstrap ──
# Gate on YOUR backend's config file and a "local stack is running" probe
# (Supabase shown; Firebase emulator, `docker compose ps`, `pg_isready`, …).
[ -f supabase/config.toml ] || exit 0
command -v supabase >/dev/null 2>&1 || exit 0
supabase status >/dev/null 2>&1 || exit 0
# ── end STACK-SPECIFIC ───────────────────────────────────────────────────────

slug="$(basename "$(pwd)")"

if ./scripts/dev-worktree-login.sh "$slug" >/dev/null 2>&1; then
  printf '{"systemMessage": "Auto-provisioned .env.local + local login (%s@dev.local) for this worktree."}\n' "$slug"
fi
exit 0
