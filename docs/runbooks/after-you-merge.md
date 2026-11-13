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

| I want | Type |
| --- | --- |
| To only look | `python3 scripts/pipeline_install.py --check`. It changes nothing, asks nothing and needs no terminal. It prints one line per part, then what waits on you. Exit 0: every part is up to date or off. Exit 10: something waits on you. Exit 4: something could not be measured. |

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
| How should this run go? | `1` for Automatic ("things just work"), `2` for Manual ("show me each change and ask"). Enter keeps the default: the way you chose last time, or Automatic. |
| The consent page: "Type yes to agree" | Read it, then type `yes`. Anything else changes nothing and the command stops with exit 10. It is asked the first time. After that it is asked again only when the mode, a setting or the page's words differ from what you agreed to. |
| Your Mac password | Type it. Asked once for the whole run, right after you agree, to save the record of your consent where only the administrator can write. |
| Replace the skills in your home folder? | `y`. |
| Update the review jobs now | `y`, when nothing the session runner works on is In Progress. It may restart the session runner and stop the notifier and tech lead jobs for a few seconds. |
| Open the repository's `delivery.json` pull request now | `y`. Only asked when a planned repository does not switch plans on yet. |
| Run the probe now | `y` after a session runner upgrade or a planning stop. Otherwise it is not asked. |
| Run the routing drill now | `n`, unless you have not run it yet. |

**In Automatic, the one command answers its own two questions yes for you.** Those are the skills and the review jobs. It says so out loud, because you agreed on the consent page. In Manual it asks each one. The installers still ask their own, such as the probe, the drill and the pull request. The consent page names them.

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

- **`--check` has only been run against made-up machines.** A later workflow is meant to run it on a clean hosted Mac.
- **It has not been run start to finish on a fresh Mac** with every part present (no ticket yet).
- **Parts with an installer of their own are not moved by it.** `docs/PARTS.md` lists them, and no page yet says when each needs a re-run (no ticket yet).
- **The review jobs' move onto work teams, and the sweep's first load,** are separate steps that this page does not walk (no ticket yet).
