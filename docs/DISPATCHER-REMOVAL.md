# Removing the old dispatcher from a Mac

*Written 2026-11-24 for KIT-646. Built and tested against a stand-in Mac. It has not run on a real one.*

`scripts/pipeline_dispatcher_removal.py` takes the old dispatcher off a Mac, together with the host
pieces that exist only for it. It is yours to run, once, in your own terminal. It refuses to run under
a model or as root.

**Run it only once every lane of every project runs on our own runner, and you have proved each lane
live** (docs/RUNNER.md, "Proving a lane live"). It checks the lanes itself. The proofs are yours to judge.

---

## What it removes

| Piece | Where its name comes from | How it goes |
| --- | --- | --- |
| The dispatcher's job | `DISPATCHER_SERVICE`, stage-e.conf | Disabled, booted out, its job file removed |
| Its package | `DISPATCHER_PACKAGE` and `DISPATCHER_NODE_SETUP`, update.conf | `npm uninstall -g`, as the pipeline's account |
| Its state backup job | `STATE_BACKUP_SERVICE`, dispatcher-removal.conf | Booted out, its job file removed |
| Any other job only it needs | `OTHER_SERVICES`, dispatcher-removal.conf | The same |
| The front door | `FRONT_DOOR_SERVICE` and `FRONT_DOOR_CONFIG`, chat-lane.conf | Its job, then its settings file |
| The tunnel | `TUNNEL_SERVICE` and `TUNNEL_TOKEN_FILE`, dispatcher-removal.conf | Its job, its token file, its job file |
| The firewall rule | The chat-lane composer's own anchor, rules file and boot job | Boot job out, anchor emptied, files removed |
| The lane marker | The review installer's folder, named for the account | Removed as root, after the last session |
| The tracker application's secrets and stored tokens | The env file, the settings file and the token store beside them | Removed as the pipeline's account |
| Their backup copies | The review installer's backups folder, and `STATE_BACKUP_COPIES` | The same |

Each piece is read back as gone before the next one starts. A piece already gone is said, never
removed twice.

The tunnel's token file is removed as root, so it gets a stricter rule. The tunnel's own job file
must name it, and it must be a file of root's alone. Anything else stays, and `check` says why.

## Before you run it

- Every lane is on for every project on the runner. Every repository the old dispatcher serves is a
  runner project. `check` reads both from the runner's own records.
- No session of the old dispatcher is working. `check` looks for its processes in its work folders.
- The app's client details are in the runner's own file. Run this first, at a terminal:

      python3 scripts/pipeline_runner_setup.py writer-client

- runner.conf says `WRITER_KEY_SOURCE=other-program`, with `WRITER_KEY_FROM` naming the old
  dispatcher's settings file. It also sets `WRITER_RENEWAL_FROM_FIELD`, the JSON key that holds the
  dispatcher's renewal token. The runner's guide says how (docs/RUNNER.md, "The writer's key").
- The settings folder does not set `WRITER_KEY_SOURCE` for runner.conf. If it did, its next `write`
  would undo the switch.
- `dispatcher-removal.conf` is filled in, beside the other part files:

      cp dispatcher-removal.conf.example dispatcher-removal.conf

## Running it

    python3 scripts/pipeline_dispatcher_removal.py check
    python3 scripts/pipeline_dispatcher_removal.py run

Both ask for your Mac password once. `check` changes nothing. It prints each gate and each piece, then
one answer: READY, NOTHING LEFT, NOT READY or UNKNOWN. UNKNOWN means something could not be read. It is
never READY. `run` checks, shows exactly what it will do, and asks you to type `yes`. Anything else
changes nothing.

Good: `ANSWER: READY`, then the plan, then `Type yes to go on:`.
Not: `ANSWER: NOT READY` with a lane still off. Switch that lane first.

## The order, and why it matters

The tracker hands back a new renewal token each time a key is renewed. So two programs must never
renew the same key: the second one is refused. Today the old dispatcher renews the session writer's
key, and the runner reads that key from the dispatcher's settings file. So `run` goes in this order:

1. It stops the old dispatcher: disabled, then booted out. Then its port must be closed.
2. It changes one line of runner.conf, `WRITER_KEY_SOURCE=other-program` to `renew`. Every other byte
   stays. Then the runner's own installer applies it, with its answer through a pipe.
3. It waits up to 12 minutes. The runner's next pass takes the renewal token from the dispatcher's
   settings file, once, and renews the key. The wait ends when the runner says the key answered as the
   session writer. Then the runner's own `check` must say so too.
4. Only then does it remove anything, in the order of the table above. The secrets go last.

No key passes through this script. The client details go in through `writer-client`. The renewal
token moves inside the runner's own process.

## If it stops

**Before anything is removed (steps 1 to 3).** Every file stays, and the end says which way to go:

- The runner has not renewed the key. The old dispatcher's renewal token still works. Fix what it
  says and run `run` again, or start the old dispatcher again with the commands it prints.
- The runner has renewed the key. Do not start the old dispatcher: its next renewal would be refused.
  Fix what the runner says, then run `run` again.

**During the removals (step 4).** It stops at the first piece that fails. Everything before it is gone
and read back as gone. Nothing after it was touched. Fix what it says and run `run` again: it starts
from what is left.

## What stays

- The old dispatcher's own folder, with its logs and work folders. Some jobs still read its logs.
  Remove the folder by hand once nothing does.
- The tracker application, and the tracker key the review jobs use for tickets. The session writer
  is the application's user, and the runner renews its key with the application's client details.
- The front door's and the tunnel's programs. Remove them where you installed them, if nothing else
  of yours uses them.

The review installer's first step still asks for a running old dispatcher. After the removal, its
`verify` and `run` stop there until that step changes.

## Outside the Mac, by hand

The end of a finished `run` prints these, each with its page:

1. Delete the tunnel, on the page `TUNNEL_PAGE` names. Then take the public name out of its domain's
   records. Let the domain lapse if nothing else uses it.
2. Turn the tracker application's webhook off, on its page under
   https://linear.app/settings/api/applications. Keep the application.
3. Rotate or revoke the keys nothing uses now. It lists the names the old dispatcher's env file held.
   A code host token goes at https://github.com/settings/tokens. A key you made for the tunnel's
   provider goes on that provider's own page. Keep the application's client id and secret.

**Never revoke the tracker application's authorization or its tokens.** That is the session writer's
key, which the runner now renews. The copies removed here ended when the runner renewed it, and the
old key ends within a day.

## Tickets that close

The kit's own tickets that exist only because of the old dispatcher. Close each one still open, once
the removal has run:

- KIT-23, KIT-25 to KIT-34, KIT-44, KIT-56, KIT-58, KIT-62, KIT-182, KIT-235, KIT-258 and KIT-377.
- KIT-114: there is no budget on the delegation lane.
- KIT-600 and KIT-601: the old review lane is gone.
- KIT-614: ticket text can no longer rewrite the dispatcher's prompt.

## What is not proven

- **It has never run on a real Mac.** The selftest runs it against a stand-in Mac in a folder. There,
  launchd, pfctl, lsof, npm and the runner's installer are data, and the account's programs run for
  real. No ticket yet.
- **The runner's first renewal against the tracker's real token endpoint** is not proven (KIT-933).
- **That a host's tunnel job file names its token file** is not measured. If it does not, the token
  stays and `check` says so.
- **Whether every old-dispatcher session ends when its job is booted out** is not measured. The lane
  marker waits for none to be working in its folders.
