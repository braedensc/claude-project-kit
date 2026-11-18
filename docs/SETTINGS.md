# The settings folder: type a value once

*Written 2026-11-18 (KIT-853, KIT-854, KIT-855). Script: `scripts/pipeline_settings.py`.*

Each part of the pipeline reads its own settings file: `stage-e.conf`, `bridge.conf`, `queue.conf`
and eight more. Many of them repeat the same values: the pipeline's account, the dispatcher, the
names the keys are stored under. A value changed in one file and missed in another breaks a part
quietly.

The settings folder holds each value once. You type it there, and `write` puts it into every part
file that needs it.

---

## Where it is

`~/Library/Application Support/claude-pipeline/settings`. It is yours, private, and never in a
repository. `--settings-dir`, or the `PIPELINE_SETTINGS_DIR` variable, moves it.

It holds `machine.conf`, one file per project in `projects/` (below), and `written.json`, the record
of what was last written (see "A hand edit"). `machine.conf` has three kinds of section:

| Section | What goes there |
| --- | --- |
| `[places]` | Where things are. `PARTS_DIR` is the folder the part files are in (empty: this kit folder). `BRIDGE_CONF=/full/path/bridge.conf` names one part file kept somewhere else. `CONSENT_RECORD`, `STATE_DIR` and `SECRETS_DIR` are the places of the consent record, the pipeline account's state folder and its secrets folder. |
| `[shared]` | A value typed once, such as `ROLE_ACCOUNT=_pipeline`. `python3 scripts/pipeline_settings.py map` lists every part file and key each one reaches. |
| `[stage-e.conf]`, `[queue.conf]`, … | One part file's own values. A key here wins over a shared value, and over the projects, for that file, and `check` says so. |

The format is the part files' own: `KEY=value`, one per line, and `#` starts a comment.

- **Good:** `LINEAR_KEY_ENV=STAGE_E_LINEAR_API_KEY`, the NAME a key is stored under.
- **Not:** the key itself. `check` fails on anything shaped like a key, token or password.

---

## One file per project

Each project has its own file, `projects/<name>.conf`, with up to three lines:

```
REPO=acme/app
TEAM_KEY=ENG
PARTS=review idea-gate tech-lead
```

`PARTS` names the parts that cover the project, by their ids in `pipeline-parts.json`. Each part's
list is then made from its projects: the repositories the review jobs watch, the teams the tech
lead asks about, the queue's team-to-repository pairs. `map` lists every list key and what fills it.

- **A list's order does not matter.** The same items in another order is no change.
- **A list no project covers is left as it is.** Nothing empties it.
- **A part's own section still wins.** `[stage-e.conf]` `REVIEW_REPOS=…` keeps that list, and `check`
  says so.
- **Some lists stay each part's own,** because they mean something narrower: the stand-up's
  `STANDUP_REPOS`, the bridge's `ACCEPTANCE_REPOS`, the watcher's `TRACKER_TEAMS`,
  `RUNNER_LANE_REPOS`, `REPO_BY_LABEL` and `REVIEWS_TEAM_KEY`.

---

## A first run: `gather`

On a Mac whose part files are already filled, `gather` reads them and shows the folder they make.
It writes nothing.

```
python3 scripts/pipeline_settings.py gather            # show what it found
python3 scripts/pipeline_settings.py gather --apply    # show it again, ask for your yes, then write it
```

- **A value is shared only when every part file that takes it sets the same.** Otherwise each file
  keeps its own value, and the output says which file says what.
- **The lists become projects,** one per repository and one per team. A team joins its repository
  only where queue.conf's `REPO_BY_TEAM` pairs them. It never guesses. The output says how to join
  the others yourself.
- **It never copies these, and names each one:** a value shaped like a secret, a key named like
  one, a key another tool writes, and a key the part's example does not name.
- **It proves the folder first.** If the folder it made would change any part file, it stops and
  says what.
- **`--apply` asks for a typed `yes`,** reads the part files again, and writes only into a folder
  with no settings yet: one that is not there, is empty, or holds the file `init` made, untouched.
  A note of your own in that file counts as settings. Like `write --apply`, it refuses inside a
  Claude session.
- **It writes the whole folder or nothing.** A failure part way, such as a full disk, removes what
  that run made, so you can simply run it again.
- `--parts-dir` reads the part files from another folder, and records it as `PARTS_DIR`.

---

## The commands

```
python3 scripts/pipeline_settings.py init            # make the folder and a machine.conf to fill in
python3 scripts/pipeline_settings.py gather          # or, on a filled Mac, show the folder its files make
python3 scripts/pipeline_settings.py check           # read-only: the rules below
python3 scripts/pipeline_settings.py write           # show every change, write nothing
python3 scripts/pipeline_settings.py write --apply   # write them, and read each file back
python3 scripts/pipeline_settings.py where           # where the folder, the part files and the places are
python3 scripts/pipeline_settings.py export --to ~/Desktop/settings.txt      # one plain file, for a new Mac
python3 scripts/pipeline_settings.py import --from ~/Desktop/settings.txt    # on the new Mac: show it first
```

- **`write` shows before it writes.** Each change is listed: the file, the key, the value now and the
  value after. It writes nothing until you run it again with `--apply`.
- **`write --apply` keeps the rest of each file.** Only the lines it names change. The mode stays, the
  file is swapped in whole, and it is read back.
- **A part file that is not there is off on this Mac.** Nothing is made for it. Copy its example first.
- **A problem anywhere stops the whole write.** Every part file is read again just before the first
  one is written, so one changed file stops them all. Only a failure part way through the writing
  itself, such as a full disk, can leave some files written. The output then names each file,
  written or not.
- **`init`, `gather --apply`, `import --apply`, `export` and `write --apply` refuse inside a Claude
  session,** and as root. Run them in your own terminal. `check`, `where`, `map`, and `gather`,
  `import` and `write` without `--apply`, only read.

---

## A hand edit

`write --apply` keeps a record beside `machine.conf`, `written.json`: what the folder last gave each
part file. When you then change one of those keys in a part file by hand, the next `write` finds it.

- **It is shown with both values,** the file's and the folder's, and nothing is written.
- **`write --apply` asks which wins,** one hand edit at a time. Type `file` or `folder`. Anything else
  writes nothing.
- **`folder`** puts the folder's value back in the file.
- **`file`** keeps the file's value, and puts it in that part's own section of `machine.conf`. A
  shared value still reaches the other files.
- **A file's value shaped like a secret is never taken,** and neither is a key the file no longer sets.
  Only `folder` can win then.
- **With no record yet,** such as the first write, a difference is the folder's own change, shown
  with both values as usual.

---

## A new Mac

```
python3 scripts/pipeline_settings.py export --to ~/Desktop/settings.txt   # on the old Mac
python3 scripts/pipeline_settings.py import --from settings.txt           # on the new Mac: shows it
python3 scripts/pipeline_settings.py import --from settings.txt --apply   # then writes it, after your yes
```

- **The export is one plain file:** `machine.conf` and each project's file, each under a
  `=== <path>` line, and which part files were on the old Mac. Read it before you copy it.
- **It never holds a secret, or the record.** `check` must pass first, so nothing shaped like a key
  leaves the folder. Keys are stored again on the new Mac, by each part's own installer.
- **It never writes over a file that is there, or inside a git repository.**
- **`import` shows the folder the file makes, and writes nothing.** It says which part files were on
  the old Mac and are not here yet, and any place it names that is not on this Mac, such as an
  old `PARTS_DIR`.
- **`import --apply` asks for a typed `yes`,** and writes the folder whole or not at all. It writes
  only into a folder with no settings yet. It refuses a file that holds a secret, names a file that
  is not a setting, or holds anything `check` would refuse.
- **It never writes a part file.** Copy the example of each part you want on this Mac, such as
  `cp stage-e.conf.example stage-e.conf`, then run `write`.

---

## What `check` fails on

- Anything shaped like a secret: a known key or token shape, a long unbroken run of mixed letters and
  digits, or a key named like a secret. It names the line and the key, never the value.
- A folder or file another account can read, or one that is not yours.
- A link, anywhere in the folder.
- A folder inside a git working tree.
- A line it cannot read, an unknown section or key, a key set twice, or a place that is not a full path.
- A key another tool writes, such as the tech lead's name or the queue's daily limits. Those stay in
  the part file, so `write` never undoes that tool's change.
- A project file with another key, a `REPO` or `TEAM_KEY` of the wrong shape, a part that takes no
  list, neither a `REPO` nor a `TEAM_KEY`, a `REPO` or `TEAM_KEY` two projects share, or a file in
  `projects/` that is not `<name>.conf`.
- A project the queue covers (`work-queue`) without a `TEAM_KEY`, or without a `REPO` when
  `[queue.conf]` sets no `REPO_BY_LABEL`. The queue's own installer refuses a team with no repository.

---

## What is not here yet

- **The places are not read from here yet.** The one command still takes `--consent-record`, and each
  part still names its own state and env files. `where` says so for each.
