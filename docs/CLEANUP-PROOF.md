# Proving a run left nothing behind

*Written 2026-11-05 (KIT-468). Script: `scripts/pipeline_cleanup_probe.py`.*

The cleanup rules each have a selftest. Only a live run shows the pipeline as a whole leaves nothing. The probe is how
you prove it: a snapshot of the role account's areas before a run and after it, and a comparison.

It changes nothing. A snapshot lists and stats; it opens no file and follows no symlink. It prints names, sizes and
file counts only.

---

## What it looks at

| Area | What lives there |
| --- | --- |
| `worktrees` | the dispatcher's worktree per ticket |
| `dispatcher-home` | the dispatcher's folder per ticket |
| `claude-scratch` | Claude Code's `/tmp/claude-<uid>`: scratch and task output per session |
| `tmpdir` | the role account's own temp folder |
| `stage-e`, `stage-a` | the jobs' state, per-PR files and logs |
| `claude-transcripts` | `~/.claude/projects`: **kept by Claude Code's own retention, never judged** |

---

## Run it, once per kind

Take a snapshot, run **one** of: a dispatched coding session on a throwaway ticket, a Stage E review of a throwaway PR,
a planning run. Wait for it to finish and for the sweep's next pass. Take a second snapshot and compare.

The role account cannot read your home, so each snapshot is run from `/` with the script fed on stdin. Check the hash
first, so an edit made after you read it cannot run:

```bash
shasum -a 256 -c <<< "<sha256 of the script>  scripts/pipeline_cleanup_probe.py" && (cd / && sudo -u <ROLE_ACCOUNT> /usr/bin/python3 -I - snapshot --label before-coding --workspace-dir <dispatcher worktrees> --dispatcher-home <dispatcher home> < /path/to/kit/scripts/pipeline_cleanup_probe.py > ~/before-coding.json)
```

Run the same command with `--label after-coding` into `~/after-coding.json`, then:

```bash
python3 scripts/pipeline_cleanup_probe.py compare ~/before-coding.json ~/after-coding.json
```

---

## What the answer means

| Exit | Means |
| --- | --- |
| `0` | Nothing left behind between the two snapshots |
| `1` | Something was left. Each is named with its area, size and file count |
| `4` | Part of an area could not be read, so "nothing left" is not proven |
| `2` | A file was not a snapshot, or is missing |
| `3` | `snapshot` was run in an agent environment: refused |

- **Left behind** means an entry that is only in the second snapshot, other than a log (the sweep trims those, so one
  grows) and the transcripts.
- **A note, not a verdict:** a folder that only gained files (for example the review poller's per-PR files under
  `~/.stage-e/state`) is reported as growth. Nothing bounds those yet (KIT-481), so the probe does not judge them.
- **It never reads an unreadable area as clean.** That is exit 4.

Attach the before and after files, and the compare's output, to KIT-468.
