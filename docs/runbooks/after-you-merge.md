# Bring this Mac up to date after you merge

*Written 2026-11-11, from the one command at that date. It covers every part the command installs.*

Merging a pull request changes GitHub, not this Mac. One command, typed in a terminal, brings this Mac level with what you merged. It works in this order: the kit checkout, the skills, the review jobs, the sweep, then the idea gate.

---

## Do this

Type it yourself, from the kit checkout. The macOS Terminal app works. So does the Terminal tab in Claude's desktop app.

```bash
python3 scripts/pipeline_install.py
```

- Good: the last line reads `The idea gate is installed, running and proven.`, or it stops and says what it needs.
- Not that: `Stopped: this shell belongs to a Claude session`. You typed it after `!` inside Claude. Type it in a Terminal tab instead.

It checks first and changes only what is behind. Running it again when nothing changed is safe, and the check is quick. If the pull has moved the code, it starts itself again on the new code.

---

## What it covers

| Part | What the one command does |
| --- | --- |
| The kit checkout | Fast-forwards it. It must be on its main branch with no uncommitted changes to tracked files. |
| The skills in your home folder | Compares them with the kit's. Asks before replacing. |
| The review jobs, with the finding, catch-up, heartbeat and status jobs, and the notifier and tech lead code that shares their clone | Runs the read-only check. Asks only when something is behind. |
| The sweep | Runs only if `sweep.conf` exists. It installs the job and never loads it. |
| The idea gate | Measures every step. Changes only what drifted. |

A part with its own settings file and installer, such as the work queue or the watcher, is not run by this command. Its check and apply lines are in `docs/PARTS.md`.

---

## What it asks

| It asks | Answer |
| --- | --- |
| Your Mac password | Type it. Asked once for the whole run. |
| Replace the skills in your home folder? | `y`. |
| Update the review jobs now | `y`, when nothing the session runner works on is In Progress. It may restart the session runner and stop the notifier and tech lead jobs for a few seconds. |
| Open the repository's `delivery.json` pull request now | `y`. Only asked when a planned repository does not switch plans on yet. |
| Run the probe now | `y` after a session runner upgrade or a planning stop. Otherwise it is not asked. |
| Run the routing drill now | `n`, unless you have not run it yet. |

**The session runner restarts only when a setting it reads changes.** A kit merge that changes neither the review setup nor the planning setup restarts nothing.

---

## If it stops

It stops with one plain sentence. Do what it says, then run the same command again. It picks up where it left off.

| It says | Do this |
| --- | --- |
| "the review installer printed a card" | Do exactly what the card above it says, then run it again. |
| "a review step could not measure itself" | Do the "What clears it" lines above it, then run it again. |
| "needs your Mac password" | Run it again and type your password at the prompt. |
| "The idea gate is waiting on you" | Do what the card above it says, then run it again. |
| "An idea-gate step could not measure itself" | Do the "What clears it" lines above it, then run it again. |
| "the kit checkout has uncommitted changes" | Look with the command it prints. Commit them or put them aside. |
| "git could not fast-forward main" | Your checkout has local commits. Look with `git -C <kit checkout> log origin/main..`. |
| "the kit checkout is on …" | Switch it back with the command it prints. |
| "interrupted" | You pressed Ctrl-C. The step you stopped finished its clean-up first. Run it again. |

To bring the idea gate level and skip the review jobs, add `--skip-stage-e`. A review step that failed part way is then left as it was.

---

## What is not proven

- **The one command has no look-only mode.** Each installer it runs has its own check, and the command itself changes things (no ticket yet).
- **It has not been run start to finish on a fresh Mac** with every part present (no ticket yet).
- **Parts with an installer of their own are not moved by it.** `docs/PARTS.md` lists them, and no page yet says when each needs a re-run (no ticket yet).
- **The review jobs' move onto work teams, and the sweep's first load,** are separate steps that this page does not walk (no ticket yet).
