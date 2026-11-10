# A worktree's own .env is loaded into the next session, so the hook names its interpreter

**Date:** 2026-11-10 · **Status:** Proposed (KIT-240). Part 1 is applied by the owner; parts 2 and 3 need his word.

## Context

The dispatcher reads `<worktree>/.env` with dotenv and merges it over the environment of every session it starts in that worktree, after its own base environment. No setting stops it (Cyrus 0.2.69 and 0.2.73, `ClaudeRunner.loadRepositoryEnv`). A dispatched session can write that file (the Bash guard that names `.env` is advisory, and the sandbox allows writes in the worktree). The next session in the same worktree then runs with the session's `PATH`, `HOME` and API address.

The kit's PreToolUse command found its interpreter on `PATH`. A planted `bin/python3` that exits 0 turned the hook off.

## Decision

1. **The hook commands name `/usr/bin/python3` by absolute path, isolated and with `DEVELOPER_DIR` cleared.** The form is `/usr/bin/env -u DEVELOPER_DIR /usr/bin/python3 -I ...`: `-I` ignores `PYTHON*` and the user site, `env -u` closes the macOS stub's `DEVELOPER_DIR` lookup. PreToolUse is `[ -x /usr/bin/python3 ] && ... || exit 2`, so a missing interpreter blocks rather than passes. `settings.json` is self-protected, so the owner applies it with one tested script. The battery pins the form and replays the attack.
2. **Proposed: a dispatched worktree that holds a `.env` is refused outright.** The cheap, honest place is the Stop hook and the readiness script flagging it; the enforcing place is a fail-closed check in the PreToolUse hook at the first call. Both are in self-protected files, so each is its own owner-applied change. Recommendation: the PreToolUse check, because by the time a Stop hook runs the session has already used the poisoned environment.
3. **Proposed: the hook pins `git` and `gh` by absolute path too.** The interpreter is only the first thing on `PATH` that the hook trusts.

## Consequences

- A machine without `/usr/bin/python3` has no working hooks until one is installed. That fails closed, on purpose.
- A moved `HOME` still drops user-level deny rules. Only parts 2 and 3 narrow that.
- Each project repository copies the settings change (KIT-232).
