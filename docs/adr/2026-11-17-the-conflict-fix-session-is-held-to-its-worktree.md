# The conflict fix session is held to its worktree

**Date:** 2026-11-17 · **Status:** Accepted on merge · **Context:** KIT-676

Amends [2026-09-15 · conflict fix runs where the worktree lives](2026-09-15-conflict-fix-runs-where-the-worktree-lives.md),
which said the fix session runs under the repo's own hooks.

## Decision

The conflict waker's fix session no longer loads the worktree's settings or the owner's. The
waker builds its whole command line (`scripts/pr_conflict.py`, `fix_argv`):

- `--setting-sources ""` and `--strict-mcp-config`: no settings, hooks or MCP servers from anywhere.
- `--settings`, the waker's own: Claude Code's built-in sandbox for every command, with no
  unsandboxed retry, no start without it, and `github.com` its only host.
- `--permission-mode dontAsk` and `--permission-prompts none`: anything not allowed is refused.
- `--allowedTools`, a named list: fixed git steps, `gh pr checks <number>`, and the repository's
  `CHECK_COMMANDS`, each check by its exact words. Read, Edit and Write are allowed by the
  worktree's path only.

`CLAUDE_ARGS` goes. A conf that still sets it is refused by name. The installer prints the
whole list in the dry run, and CK-W1 signs it with the count.

## Why

Before, the session started with the worktree's own settings. This kit ships
`bypassPermissions`, so a fix session could write anywhere the owner can. A branch the owner's
own session wrote can still carry text from an untrusted source, such as a pasted log.

Rejected:

- **Keep the worktree's settings and add a sandbox.** Settings arrays merge, so the worktree
  could add allow rules or unsandboxed commands of its own. The branch being fixed is not
  reviewed yet.
- **Our own macOS sandbox around the whole program.** KIT-630 row r5b: inside it, the built-in
  sandbox cannot start, so Bash would not run.
- **Keep `CLAUDE_ARGS` beside the walls.** One flag in it undoes them all.

## The walls, measured

Measured on 17 Nov on this Mac, with Claude Code 2.1.287, as the owner's account. A scripted
stand-in for the API sent each tool call, so no model ran and nothing was spent. Every command
below was allowed by an exact rule. A refusal is therefore the sandbox's, and its words say so.

| What was tried | Result |
| --- | --- |
| A script writes a file in the owner's home, outside the worktree | Refused by the sandbox: `Operation not permitted` |
| The same, as `python3 -c "open(...)"` | Refused by the sandbox |
| The same, with `dangerouslyDisableSandbox: true` | Refused by the sandbox: no unsandboxed retry |
| The Write tool, outside the worktree | Refused: no rule allows that path |
| A script writes the main checkout's `.git/hooks` or `.git/config` | Refused by the sandbox |
| A script writes `.mcp.json` or `.claude/settings.local.json` in the worktree, or the Claude config folder | Refused by the sandbox |
| The Write tool on `.claude/settings.local.json`, `.vscode/` or `.envrc` in the worktree | Refused by Claude Code, despite the path rule |
| `/private/tmp` and the system `$TMPDIR` | Refused by the sandbox |
| `/tmp/claude-<uid>`, Claude Code's own temp folder | Allowed: its shell keeps its working folder there. Denying it broke every command |
| The per-project folders inside it, other sessions' scratchpads | Refused once denied by a pattern, and commands still worked |
| `git fetch`, `git merge`, `git commit` in a worktree | Worked: objects land in the shared git folder |
| `git ls-remote` and an authenticated `git push --dry-run` over HTTPS | Worked through the sandbox's proxy, with git's `osxkeychain` helper |
| The same push with a gh-backed credential helper (`gh auth git-credential`) | Failed: `could not read Username`. Preflight refuses such a helper |
| A commit through the kit's real pre-commit hook | Worked |
| `gh` inside the sandbox | Failed: it could not verify TLS or read its token |
| `gh pr checks <n>` as an exact excluded command | Worked, outside the sandbox |
| That command with a redirect added | Refused: the rule is exact |
| That command chained to another allowed command | Stayed in the sandbox |
| A command no rule names | Refused, never prompted |
| The Read tool on `.env`, and `git diff --no-index` on it | Refused by the carried deny rules |
| Glob and Grep outside the worktree | Refused |

## What it does not hold

- **Reads.** Commands could still read most of the machine. KIT-786 closed that:
  [the fix session reads only what it needs](2026-11-18-the-conflict-fix-session-reads-only-what-it-needs.md).
- **The owner's GitHub login.** The push needs it, and gh runs outside the sandbox.
- **Claude Code's temp folder.** Files at its top level stay writable.
- **This kit's own gate inside the sandbox.** `npm run test:hooks` passes every case, then its
  process check fails because the sandbox refuses `ps`. KIT-785 makes it say NOT CHECKED.
- **SSH.** git cannot use SSH inside the sandbox on macOS, per Claude Code's sandbox docs, so
  preflight refuses an SSH origin. This was not measured here.

## Verified

- The table above, measured once by hand. Nothing in the kit re-runs it yet, so a Claude Code
  update could change a row unseen.
- `npm run test:conflict` case 31 pins the command line, the settings key by key, and the named
  list. It also checks that every command the prompt names is allowed, and that the dry run
  prints the same list.
- `npm run test:conflict-waker-setup` covers the conf (`CLAUDE_ARGS` refused, checks by exact
  words), each check's program on the job's PATH, the SSH refusal, and the dry run's list.
