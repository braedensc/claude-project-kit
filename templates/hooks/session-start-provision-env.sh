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
#       "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-start-provision-env.sh \"$CLAUDE_PROJECT_DIR\"/scripts/dev-worktree-login.sh",
#       "timeout": 60 } ] }
#
# NAME THE PROVISIONING SCRIPT IN THE WIRING, AS ABOVE (KIT-603). This hook and the
# script it runs both run outside the Bash sandbox, at every start and resume, from
# $CLAUDE_PROJECT_DIR: on the dispatcher's lane that is the session's own worktree. The
# PreToolUse hook self-protects every file a hook command in the settings names through
# $CLAUDE_PROJECT_DIR, so a session cannot edit either one. This hook runs only the
# script its wiring names. Wired without it, or with a path outside the project, it
# provisions nothing and says so.
#
# No-ops silently (no error, no output) when:
#   - .env.local already exists (already provisioned, or not a fresh worktree)
#   - this isn't one of this project's worktrees (backend config absent, or the
#     named provisioning script is missing)
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

# ── STACK-SPECIFIC: project-detection + stack-up guards — swap at bootstrap ──
# Gate on YOUR backend's config file and a "local stack is running" probe
# (Supabase shown; Firebase emulator, `docker compose ps`, `pg_isready`, …).
[ -f supabase/config.toml ] || exit 0
command -v supabase >/dev/null 2>&1 || exit 0
supabase status >/dev/null 2>&1 || exit 0
# ── end STACK-SPECIFIC ───────────────────────────────────────────────────────

# The provisioning script is the one the wiring names, never one this file picks, so it
# is a file the settings name and therefore self-protected (KIT-603; see the header).
provision="${1:-}"
case "$provision" in
  *..*) provision="" ;;                          # a `..` path is never one the hook protects
  "${CLAUDE_PROJECT_DIR:-.}"/*) ;;
  *) provision="" ;;
esac
if [ -z "$provision" ]; then
  printf '%s\n' '{"systemMessage": "Worktree provisioning skipped: its SessionStart wiring must name the provisioning script under $CLAUDE_PROJECT_DIR, after the hook, so both stay self-protected. See the header of .claude/hooks/session-start-provision-env.sh."}'
  exit 0
fi
[ -x "$provision" ] || exit 0

slug="$(basename "$(pwd)")"

if "$provision" "$slug" >/dev/null 2>&1; then
  printf '{"systemMessage": "Auto-provisioned .env.local + local login (%s@dev.local) for this worktree."}\n' "$slug"
fi
exit 0
