# The sweep — what a finished dispatcher run left goes

*Written 2026-11-04 (KIT-464). Script: `scripts/pipeline_sweep.py`.*

Every session the pipeline runs is a dispatcher session, run as its role account: coding
sessions, planning runs, re-prompts, the chat lane and the workers. Nobody watches them,
and each run leaves files behind.

The dispatcher removes a ticket's worktree when the ticket closes, but only if it sees the
change happen. It never removes the folder it keeps for each ticket in its home. On
4 Nov 2026 the reference machine had 216 such folders, one for every ticket since setup.

The sweep removes what a finished run left, once the run is clearly finished.

---

## What one pass removes

| Kind | Where | It goes when |
| --- | --- | --- |
| worktree | `<workspace_dir>/<ID>` | its ticket has been done for `done_days`, or is gone and the folder has not changed for `done_days` |
| ticket | `<dispatcher_home>/<ID>`, the dispatcher's folder for that ticket (it downloads attachments there) | the same, once the ticket's worktree is gone |
| session | a Claude Code project folder in `session_root`, or one session folder in it | nothing in it has changed for `idle_days` |
| temp | the kit's own temp folders in the account's `TMPDIR`: a battery's run folder (`<name>-selftest-…`, `<name>-run-…`), a scratch run, a dry run's (`stage-e-dry-…`, `pipeline-morning-…`) | nothing in it has changed for `idle_days` |

- **Done** means completed or canceled.
- **Gone** means deleted (in the tracker's trash), or no longer on its team.
- **A git worktree goes through its own clone,** with `git worktree remove --force`. It is
  never removed as a plain folder, which would leave the clone believing it still exists.
  A folder with no `.git` at all is plain, and goes as one.
- **Its branch stays.** Removing a worktree never deletes a branch.
- **Idle stands in for ended,** for a session folder. A session keeps no process in its
  temp folder, so a live one cannot be told apart. One idle for `idle_days` loses only that
  temp folder. The dispatcher's headless sessions leave theirs empty.
- **A temp name is the kit's** when it has the shape Python's `mkdtemp` gives a kit prefix:
  the prefix, then eight characters. The selftest reads every prefix the kit's scripts use,
  and fails if the rule misses one.

---

## What it never removes

- A folder whose ticket is open.
- A folder a process of this account is working in.
- A folder another account owns, or a symlink.
- A name that is not a ticket's, a session's or one of the kit's temp names.
- A ticket on a team the config does not name, or on one the tracker key cannot see.
- Anything outside the four folders the config names.

**A source it cannot read removes nothing that needs it.** The tracker, the list of folders
in use, or a folder it cannot read is said in the pass's output. It is never read as
empty.

Good: the tracker does not answer, so no worktree or ticket folder goes, and the pass
exits 4.
Not: the tracker does not answer, so every ticket reads as gone.

Anything odd is left and named: a worktree its clone does not list, a folder holding a
repository of its own, or a `.git` it cannot follow.

---

## What a pass says, and its exit code

Each pass prints one line per folder it removed, then what it kept and what it could not
judge, grouped by reason, then one summary line. `--dry-run` prints the same, with "would
remove", and removes nothing.

| Exit | Means |
| --- | --- |
| `0` | The pass ran, and everything due went. Nothing may have been due. |
| `1` | Something due could not be removed, or the pass could not complete. |
| `2` | Usage, config or credential, including a key the tracker refuses. |
| `3` | Refused: a real pass in an agent environment, or an account the config does not name. |
| `4` | The pass ran, but something could not be judged, and was left. |

**It refuses a real pass in an agent environment.** A session that ran it would sweep the
account it runs as. A dry run is allowed.

---

## Config

One JSON file. `python3 scripts/pipeline_sweep.py --example-config` prints every key.

| Key | Default | What it is |
| --- | --- | --- |
| `account` | (required) | The account the sweep runs as and sweeps: the dispatcher's role account. A pass as any other account is refused. |
| `workspace_dir` | (required) | The dispatcher's worktree folder, its `workspaceBaseDir`. |
| `dispatcher_home` | (required) | The dispatcher's home, its `--cyrus-home`. |
| `teams` | (required) | Team keys whose tickets' leftovers may go. |
| `linear_key_env` | `STAGE_E_LINEAR_API_KEY` | The name of the variable that holds the tracker key, never the key. |
| `done_days` | `3` | Days a ticket must be done, or a gone ticket's folder untouched. |
| `idle_days` | `3` | Days a session or temp folder must be untouched. |
| `session_root` | `/tmp/claude-<uid>` | Claude Code's temp folder for the account. |
| `temp_dir` | the account's own | The account's `TMPDIR`. On macOS it asks `getconf DARWIN_USER_TEMP_DIR`. |

Folders are absolute, real paths. A path through a symlink, or a top-level folder such as
`/opt`, is a config error.

---

## Run it by hand

As the role account, from `/`, a dry run first:

```bash
sudo -u <ROLE_ACCOUNT> -H /bin/sh -c 'cd / && set -a; . ~/.stage-e/env; set +a; /usr/bin/python3 ~/.stage-e/kit/scripts/pipeline_sweep.py run --config ~/.stage-e/sweep/sweep.json --dry-run'
```

Read what it would remove. Then run the same command without `--dry-run`.

Start it from `/`. Started in a folder the role account cannot enter, Python stops
before it does anything.

---

## Install it

`scripts/pipeline_sweep_setup.py` sets the sweep up beside a Stage E install, from `sweep.conf` (copy `sweep.conf.example`; every key is in it):

```bash
python3 scripts/pipeline_sweep_setup.py run --dry-run    # measure every step, change nothing
python3 scripts/pipeline_sweep_setup.py run              # the only command that changes anything
```

Its steps, each reported as a row:

| Step | What it does |
| --- | --- |
| preflight | The role account's home, its Stage E clone (level with this checkout) and env file (by name), and the sweep's selftest |
| config | Writes the sweep's config as the role account at mode 600, and reads it back. The sweep's own loader judges it first |
| job | Installs the LaunchDaemon plist. It never loads it |
| dry-run | Runs the sweep once as the role account with `--dry-run` and shows its lines |
| enable | Card `CK-S1`: **you** read those lines, then load the job with the two `sudo launchctl` commands the card prints |
| leftover | Measures what the role account's runs have left (the dispatcher's worktrees, its Claude Code scratch, its `TMPDIR`, `~/.stage-e`) against `LEFTOVER_CEILING_GB` |

- **Why it never loads the job.** The first pass removes what finished runs left, possibly hundreds of folders. You read the dry run first. Nothing is removed that the dry run did not list.
- **The leftover row fails past the line** and names the largest folders. What it could not read is `NOT MEASURED WHOLE` (exit 4), never counted as empty.
- **Under a model it is refused,** except `run --dry-run`, which says NOT MEASURED where `sudo` is needed.
- **`scripts/pipeline_install.py` runs it** when `sweep.conf` exists, after Stage E and before the idea gate. Its card does not hold the idea gate back.

---

## What is not proven

- No pass has run against the live tracker. It makes two reads: the teams the key sees,
  and one team's tickets by number. The team filter is one the bridge already uses; the
  number filter is new. A read the tracker refuses is said, and removes nothing. The
  first dry run as the role account shows which.
- No pass has run as the role account. The process list comes from `lsof`; if it cannot
  be read, the pass removes nothing and exits 4.
