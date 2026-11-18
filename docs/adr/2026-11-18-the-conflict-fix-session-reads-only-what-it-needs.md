# The conflict fix session reads only what it needs

**Date:** 2026-11-18 · **Status:** Accepted on merge · **Context:** KIT-786

Amends [2026-11-17 · the conflict fix session is held to its worktree](2026-11-17-the-conflict-fix-session-is-held-to-its-worktree.md).
Its sandbox fenced writes, but reads only where a rule denied them.

## Decision

Every command a fix session runs is refused reads where people and programs keep files. Reads
are opened again only where the session's own tools need them. `scripts/pr_conflict.py` holds
three tables. Each row is a path and its reason, and `fix_settings` turns them into the
sandbox's `denyRead` and `allowRead`.

Denied (`FIX_READ_DENY`):

| Path | Why |
| --- | --- |
| Your home, from the user database, never `$HOME` | Your keys, logins, mail, and every other project |
| `/Users` | Every account's files, and `/Users/Shared` |
| `/Volumes` | Other disks: backups and copies of the same files |
| `/private/var/folders` | Every account's own temp and cache folders |
| `/tmp` | The shared temp folder |
| Scratchpads in `/tmp/claude-<uid>` | Other Claude Code sessions' scratchpads |

Opened again (`FIX_READ_FOLDERS`, `FIX_READ_FILES`):

| Path | Why |
| --- | --- |
| The worktree | The session's own files |
| Its checkout, the `REPO_DIRS` entry | git's shared objects, refs and hooks, and the scanner the pre-commit hook runs |
| `/tmp/claude-<uid>` | The `TMPDIR` the sandbox gives every command, and where each command's output is kept |
| `~/.config/git` and `~/.gitconfig` | git's settings: the name and email on the merge commit |
| `~/.nvm` and `~/.pyenv` | Node, npm and Python where those managers install them |
| `~/Library/Keychains/login.keychain-db` | git's `osxkeychain` helper reads it to push |

Each folder opened again keeps the kit's secret-file denials, so a `.env` inside one is still
refused. Around the tables:

- The waker refuses to start when the user database has no home for its uid.
- It declines a PR whose worktree or checkout is your home, or holds it. Opening one would
  undo the deny on your home.
- The installer's preflight refuses a check whose program a session could not read, and names
  the folders it may use.
- The dry run prints the read walls, and CK-W1 signs them with the rest.

## Why

The path out went in three steps. A check command's child reads a secret outside the worktree.
The session writes it into the worktree. Then it pushes. Fencing writes alone does not stop that.

Rejected:

- **Claude Code's own block on reads outside a session's folders.** It is a managed-settings
  key. In `-p`, a settings file with a key Claude Code does not accept is dropped whole and
  silently, and the sandbox goes with it. The explicit lists were measured working. They deny
  the same Mac folders that block does, `/Users` and `/Volumes`.
- **Opening `~/Library/Keychains` whole.** The login keychain file alone lets the push work.
- **Denying all of `/tmp/claude-<uid>`'s project folders.** Claude Code keeps each command's
  output there. With it unreadable, `cat` and `node` failed to write their output. Only other
  sessions' scratchpads are denied.
- **Opening npm's cache, `~/.npm`.** `npm run` and `npx` of an installed tool work without it.
- **Opening the top folder of every `PATH` entry under your home.** `~/.cargo` holds a crates.io
  token and `~/.docker` holds registry logins. A fixed list is narrower, and preflight names a
  program outside it.

## The walls, measured

Measured on 18 Nov on this Mac, with Claude Code 2.1.287, as the owner's account. A scripted
stand-in for the API sent each tool call, so no model ran. The session was built by the
branch's own `fix_settings`, `fix_rules` and `fix_argv`. Its checkout was a kit clone under the
home, with the real pre-commit hook. Node, npm and Python came from `~/.nvm` and `~/.pyenv`.
Each read below ran in a named check's child, so a refusal is the sandbox's.

| What was tried | Result |
| --- | --- |
| A fake secret in your home, outside the checkout | Refused: `Operation not permitted` |
| The checkout's `.env`, and a `credentials` file inside it | Refused: the narrower deny wins inside an opened folder |
| A file in `/Users/Shared`, in your `$TMPDIR`, in `/tmp` | Refused |
| Another session's scratchpad | Refused |
| `ls` of your home, and of `/Users` | Refused |
| Every read above under KIT-676's settings, the control | Each one read the fake |
| `git commit` through the kit's real pre-commit hook and its secret scan | Worked |
| `git ls-remote`, and an authenticated `git push --dry-run` | Worked |
| The same push without the keychain file | Failed: `could not read Username` |
| `npm run`, `npx` of an installed tool, `node`, `python3` through pyenv | Worked |
| The kit's whole gate, `npm run test:hooks` | 853 of 854 passed and one was NOT CHECKED, as KIT-785 says. Exit 0 |
| `python3 scripts/check_placeholders.py` | Worked |
| Grep and Glob in the worktree | Worked |
| An opened path that does not exist | Harmless |
| A read deny on all of `/tmp/claude-<uid>/-*` | `cat` and `node` could not write their output |
| `git credential fill` in a check's child | Returned the GitHub login. Only a count was printed |

## What it does not hold

- **Your GitHub login.** The push needs it, so git's own helper hands it to any command in the
  session that asks. A check's child can read it, write it into the worktree, and push it.
- **git's settings files.** A token written into `~/.gitconfig` stays readable. A
  `~/.gitconfig` that includes another file under your home makes git fail in the session.
- **Other sessions' command output in `/tmp/claude-<uid>`.** It is every sandboxed session's
  temp folder, so it stays open. Only their scratchpads are denied.
- **Programs under your home outside `~/.nvm` and `~/.pyenv`.** A check that runs one fails.
  Preflight refuses a check whose own program is there, but not what that check starts.
- **Linux.** Not measured. The list names Mac folders, and the home deny is what holds there.
- **A Claude Code update.** Nothing re-runs this table yet, so an update could change a row
  unseen.

## Verified

- The table above, measured once by hand. The probe and its notes are kept outside the repo.
- `npm run test:conflict` cases 31 and 31b pin both lists path by path, with each row's reason.
  They check that your home comes from the user database when `$HOME` is moved. A missing home
  is refused before anything is read, and a checkout holding your home is declined. The dry
  run names every read wall.
- `npm run test:conflict-waker-setup` covers the preflight refusal of a check program that a
  session could not read.
