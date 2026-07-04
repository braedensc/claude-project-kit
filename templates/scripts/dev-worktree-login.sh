#!/usr/bin/env bash
# Per-worktree dev-environment provisioning — ported from todoclaw (2026-07-03),
# where it provisioned parallel worktree sessions against the shared local stack
# (dedicated per-slug logins ended the shared-test-account collisions).
#
# THE PATTERN (portable to any backend): git worktrees don't share gitignored
# files (.env.local), so every new worktree starts broken until someone
# regenerates it — and if parallel worktrees/sessions share one local test
# account, they collide. One script, run ONCE per new worktree (by a human, not
# Claude — the PreToolUse hook blocks .env writes and key-shaped values):
#   1. regenerates THIS worktree's .env.local from the running local backend
#   2. provisions a dedicated <slug>@dev.local test login for this worktree
# (Run by a human OR invoked by Claude; the values never enter the model's
# context.) Gate on a backend config file's presence, fail loudly if the local stack
# isn't running, and make user creation idempotent. Claude may INVOKE this
# committed script — the hooks block constructing/reading env content, not
# running a script that does.
#
# THE IMPLEMENTATION BELOW IS SUPABASE-SPECIFIC — swap the `supabase status` /
# admin-API calls for your backend (Firebase emulator, local Postgres + custom
# auth, …) and adjust the VITE_ env names at bootstrap.
#
# Usage: scripts/dev-worktree-login.sh <slug>
#   <slug> — a short name for this worktree/session (e.g. its branch or folder name).

set -euo pipefail

slug="${1:?Usage: scripts/dev-worktree-login.sh <slug>}"
email="${slug}@dev.local"
password="devpassword123"

if [ ! -f supabase/config.toml ]; then
  echo "Run this from a worktree root (no supabase/config.toml here)." >&2
  exit 1
fi

if ! command -v supabase >/dev/null; then
  echo "supabase CLI not found — brew install supabase/tap/supabase" >&2
  exit 1
fi

if ! supabase status >/dev/null 2>&1; then
  echo "Local Supabase stack isn't running — run 'supabase start' first." >&2
  exit 1
fi

env_out="$(supabase status -o env \
  --override-name api.url=VITE_SUPABASE_URL \
  --override-name auth.anon_key=VITE_SUPABASE_ANON_KEY)"

grep '^VITE_' <<<"$env_out" > .env.local
echo "VITE_SENTRY_DSN=" >> .env.local

api_url="$(grep '^VITE_SUPABASE_URL=' <<<"$env_out" | cut -d= -f2- | tr -d '"')"
service_role_key="$(grep '^SERVICE_ROLE_KEY=' <<<"$env_out" | cut -d= -f2- | tr -d '"')"

tmp_response="$(mktemp)"
trap 'rm -f "$tmp_response"' EXIT

status_code="$(curl -s -o "$tmp_response" -w '%{http_code}' \
  -X POST "${api_url}/auth/v1/admin/users" \
  -H "apikey: ${service_role_key}" \
  -H "Authorization: Bearer ${service_role_key}" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${email}\",\"password\":\"${password}\",\"email_confirm\":true}")"

if [ "$status_code" = "200" ] || [ "$status_code" = "201" ]; then
  echo "Created local user: ${email}"
elif grep -qi "already been registered\|already exists" "$tmp_response"; then
  echo "${email} already exists — reusing it."
else
  echo "Unexpected response (HTTP ${status_code}):" >&2
  cat "$tmp_response" >&2
  exit 1
fi

echo
echo ".env.local written — pointing at the local backend stack."
echo "Sign in with:"
echo "  email:    ${email}"
echo "  password: ${password}"
