# The watcher

An auditor that watches the pipeline and writes down what it sees. It is plain code: no model, no
prompt. It can stop nothing and change nothing. This is its first piece (KIT-514): the monitors.
The design, and the decisions that still wait for the owner, are in
`docs/adr/2026-11-05-the-watcher-an-independent-auditor.md` (status Proposed). The file it writes is
contract §19. The code is `scripts/pipeline_watcher_local.py`.

It is off until you turn it on. Nothing runs it on a schedule yet: its own account, its install and
its job are KIT-517. The way it pauses the system is KIT-515, and its urgent page is KIT-519 (below).

## What it watches

| Monitor | What it looks for | Source |
|---|---|---|
| `spend` | Today's dollars or one piece's over a limit of the watcher's own. Any run that signed in with a billed key. | The usage file (§17) |
| `runs` | Too many runs in a day, or on one piece. | The usage file |
| `labels` | `hooks-change`, or an `agent:`, `blocked:` or `provenance:` label, applied or removed by a login not on its list. | GitHub's issue events |
| `markers` | A pipeline marker on a comment's first line from an author its readers would refuse. The workflow bot's own comments are always real. | GitHub's comments |
| `injection` | Shapes that try to steer an AI reading the text: closing the kit's fence, "ignore the previous instructions", a system-role tag, a request to print a secret, and characters a reader can't see. | GitHub's comments and pull requests |

| `tracker-text` | The same shapes in ticket and comment text, and a pipeline marker on a comment's first line from an author who is not trusted. The tracker's newest 100 tickets and 100 comments. | The tracker, by one fixed read-only query |

Four more are listed as `off`, each with its reason, until something writes their signal: guard blocks,
egress refusals, work outside a ticket's files, and the validator's approval rate (KIT-518). The file
always says how many monitors are on.

`injection` is a tripwire, not a filter. It catches the lazy attempts. The gates that matter are
the code in the validator's hard limits and the owner's own list of human-only changes.

## What each state means

| State | Means | What to do |
|---|---|---|
| `clear` | It asked, read the answer, and found nothing at `page` or `pause`. It says what it asked. | Nothing. |
| `flagged` | A finding at `page` or `pause`. | Read the finding's link. It names the pull request or ticket, who, and when. |
| `blind` | It couldn't establish the answer. The reason says why. | Fix the reason. A blind monitor is never a clear one. |
| `off` | Not configured, or nothing writes its signal yet. | Set its limits or its repositories, if it should be on. |

A finding has a level. `pause`: something got through, or money is moving. `page`: an attempt the
readers ignored. `record`: noted, never paged. Nothing acts on the level yet.

## Turn it on, by hand

1. Print an example, and save it as your rules file. The numbers are illustrative: the limits are
   yours to set, from a week of measured spend.
   ```bash
   python3 scripts/pipeline_watcher_local.py --example-config
   ```
2. Keep the rules file where no session can write it: outside every git working tree, owned by you,
   mode 600, in a folder only you can write. The pass judges the file before it obeys anything in it.
   A file that is inside a working tree, is a link, is writable by group or other, is owned by another
   account, or sits in a writable folder is not trusted, and nothing in it is obeyed, not even
   `"on": false`. Every monitor is blind, by name, and the pass exits 4.
   With no file at all the watcher is off, and says so.
3. Give it a read-only GitHub token in the environment variable your rules name. It never reads a
   token from the rules file.
4. Try it. This reads everything and writes nothing:
   ```bash
   python3 scripts/pipeline_watcher_local.py run --config /path/to/watcher.json --dry-run
   ```

Good: each monitor that is on says what it asked and what the answer was, and the last line says
how many are on.

Not that: `blind` with a reason you can't act on, or `COULD NOT RUN`, which names every config
problem at once.

## The rules file

| Key | What it sets |
|---|---|
| `on` | `true` to run. Off by default. |
| `repos` | The repositories whose events, comments and pull requests are read. With none, the three GitHub monitors are off. |
| `usage_file` | The usage file. With none, `spend` and `runs` are off. |
| `hold_file` | Where the hold is written when a monitor flags something at level `pause`. Empty: the watcher only reports. |
| `limits` | `day_usd`, `piece_usd`, `day_runs`, `piece_runs`. A monitor with none of its limits is off: the limits are the owner's decision. |
| `trusted_actors` | GitHub logins that may apply or remove a protected label. |
| `trusted_associations` | Author associations a marker or text is trusted from. Default: owner, member, collaborator. |
| `github_token_env` | The name of the environment variable that holds the token, never the token. |
| `linear_key_env`, `tracker_teams`, `tracker_trusted_users` | `tracker-text` needs all three: the name of the variable that holds a READ-ONLY tracker key, the team keys to scan, and the tracker user ids whose text and markers are trusted (you, and the dispatcher's agent). Any one empty and the monitor is off, with its reason. |
| `status_file` | Where the file is written, outside every git working tree. |
| `lookback_hours`, `max_pages`, `max_requests`, `run_timeout_seconds` | How far back it reads, and caps against a runaway. A feed that runs past `max_pages` makes its monitor blind. A usage file past 20 MB, or a GitHub answer past 16 MB, is not read. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every monitor that is on is clear, or the watcher is off. |
| 4 | None is flagged, and at least one that is on is blind. |
| 10 | At least one is flagged at `page` or `pause`. |
| 1 | The pass crashed, or the file could not be written. |
| 2 | The rules are wrong, or the watcher is on with nothing to watch. |

## The hold

The watcher's one power is a hold on new starts. When a monitor flags something at level `pause`, and the rules name a
`hold_file`, the watcher writes it (contract §20). While it exists the queue starts nothing (`docs/QUEUE-OPERATOR.md`).
A later clean pass never clears it, and neither does the watcher: only you do, in your terminal:

```bash
python3 scripts/pipeline_hold.py clear --hold-file /path/to/hold.json
```

Good: it shows what is held, asks for a typed `yes`, and says `Cleared`. Not that: `REFUSED`, which means it was run
inside an agent's environment.

With no `hold_file` the watcher can only report, and says `NOT HELD` each time a finding calls for a hold. The queue's starter,
the planner, the bounce driver and the catch-up job each read the hold before they start anything (name it in each job's
`hold` setting; see below). The bridge's direct asks do not read it yet: that is the bridge session's ticket. The hold file must sit in a folder no worker can write, or a worker could lift its own pause: its own
account and install (KIT-517) set that up, and the rules refuse a path inside a git working tree.

## The one POST: reading the tracker

The watcher sends nothing but GETs, with one exception, and it is yours (KIT-556, option (a) on the
ticket that asked). The tracker answers only GraphQL, which is a POST. Reading ticket text first-hand is
the point of an independent watcher, so `scripts/pipeline_watcher_tracker.py` may send **one fixed
read-only query** to the tracker's one host, and nothing else.

- **One host, one query, a constant.** It has no variables, so no text from a ticket, a file or the
  environment is in it. It reads the newest 100 tickets and the newest 100 comments, by when they were
  last updated.
- **Pinned.** That file's selftest fails if it holds a mutation, a second query, a POST to any other host,
  or a redirect that is followed. The watcher's own selftest still pins that it sends no POST of its own.
- **Never "nothing found".** A query that fails, or a page that is all inside the lookback so older items
  were not read, makes `tracker-text` blind, with the reason.
- **Read-only by key, too.** Give it a read-only tracker key. The query could not write with any key, but
  a key that cannot write is the second lock.

Good: `tracker-text: clear`, with "read the newest tickets and comments of 1 team(s)... 12 text(s)".
Not: `blind: the newest 100 comments were all updated inside the lookback`, which means a busy day hid
older ones: shorten `lookback_hours`, or read it as a warning.

Two limits, said plainly:

- **A description is trusted by who made the ticket, not by who last edited it.** The query does not read
  a ticket's history. A shape added to a ticket the owner made, by someone else, is recorded and not
  paged. A comment is judged by its own author, so a comment is the stronger signal.
- **It reads the newest page only.** It checks the order of a full page, and a page in the wrong order is
  blind. A short page is the whole set.

Not proven: the query has been run only against a fake. The first live pass is the test.

## The urgent page

The watcher writes its status file and sends nothing but GETs. A page is a POST, so a second job does it:
`scripts/pipeline_watcher_page.py` (KIT-519). It reads the status file and tells you. It does not go
through the bridge, the notifier or any job a worker can reach, so it works when they are down or lying.

It speaks when the answer changes, once, like the health watch:

- a monitor flagged at `page` or `pause`: which monitor, how many findings, since when, which tickets or
  pull requests, and what you can do. No ticket, comment or pull request text is in it.
- a monitor blind for three passes running;
- the watcher itself: no status file, an unreadable one, one that is not a status file, one older than
  `max_age_minutes`, or one written by a pass that failed;
- once more when a monitor it paged about is clear again.

**Two routes, both yours** (ADR decision D2). Name one or both in the page job's config; a route with no
credential is said, never skipped quietly.

| Route | What it is | What it needs |
|---|---|---|
| `slack` | The watcher's own Slack app, to your DM | The app's bot token, with the `chat:write` scope, and your DM channel id |
| `github` | An issue in a private alert repository | A token that may open issues there, and the repository |

The config holds the NAME of each environment variable, never a value. The values live in the watcher
account's own env file, mode 600. Print an example with `--example-config`.

**Your steps, once (nothing here is run by a session):**

1. In Slack, create an app of your own, give it the `chat:write` scope, install it to the workspace,
   and copy its bot token. Open a DM with the app, and copy the DM's channel id (it starts with `D`).
2. Put the token in the watcher account's env file, under the variable name the config names.
3. Check what is set, in your terminal: `python3 scripts/pipeline_watcher_page.py check --config PATH`.
4. Send one test page: `python3 scripts/pipeline_watcher_page.py test --config PATH`. It shows what it
   will do, asks for a typed `yes`, and sends one page marked TEST to each route.

Good: `slack: SENT. Look for it now`, and the message in your DM. Not that: `NOT SENT (...)`, which says
which route and why, with no body from the service.

**A page that could not be sent is sent again, and is never recorded as sent.** Each route keeps its own
record, so one route failing does not repeat the page on the other. A pass that could not send exits 4
and writes `page-heartbeat.json` with `"result": "could-not-page"` and the route that failed. The health watch reads that file, so a page job that keeps failing reaches you another way: name it in `update.conf` as `WATCHER_PAGE_HEARTBEAT_FILE` (`docs/UPDATE-OPERATOR.md`). It says Slack only, and names the route that failed.

Good: `watcher-page OK checking in, and every page went`. Not: `could not send a page by slack`, which means a page did not reach you by that route; run `check` and then `test`.

**What it cannot do.**

- It never follows a redirect: a token goes to the host it was meant for or nowhere.
- It cannot page when this machine is asleep or off. An off-box dead-man's switch is ADR decision D7.
- The Slack and GitHub requests have been tested only against a fake sender. The first real page is the
  test in step 4.
- It runs from nothing yet: its schedule and account are KIT-517. Run it by hand with
  `python3 scripts/pipeline_watcher_page.py run --config PATH` (`--dry-run` shows what it would send).

## The judge

A model can say in plain words what a flag means. It is the last thing the watcher has, and the least
trusted: it reads text an attacker may have written. So it is built to do one thing, **add a note**.

`scripts/pipeline_watcher_judge.py` (KIT-516) reads the status file. For each monitor flagged at `page` or
`pause` that it has not explained, it asks the tech lead's writer for a few plain sentences, and stores them in
`judgments.json`. The pager sends them as a note **after** the page, only if you name that file in its
`judge_file` setting.

- **It is never in the path of a stop.** The page goes first, from rules. The hold is written by the watcher's
  code. Nothing reads the judge's file to set a level, clear a flag or clear a hold. A judge that says "this is
  fine" lowers nothing.
- **It has no tools at all.** It is one `claude -p` run with no tools, no MCP, no settings and no saved session,
  through the same safe call the tech lead's messages use. Plain code fetches the flagged text first, with a
  read-only GET (a GitHub comment or pull request) or the watcher's one fixed tracker query, cuts it, and hands it
  over as data inside a fence.
- **Its words are checked by code.** No link or ticket id the facts do not hold, no credential shape, no mention,
  no code block, no question, no instruction on how to reply. Anything else falls back.
- **A failed judge is said, and tried again.** The note says "The judge could not explain this" and why, and the
  judge exits 4, so it turns red, and stays red while the monitor stands unexplained. It tries again an hour later,
  up to three times, then gives up and says so; the words, if they come, are sent as a second note. A day cap waits for
  the next day and costs no try.
- **It has a cap of its own:** `day_cap_usd` a day, kept in its own file, and `writer_max_budget_usd` per call. A
  pass past the cap judges nothing more and says so. Each call's time and dollars are also kept alone, with no
  words, in `judge-calls.json` (mode 644). The usage job counts the judge's cost into its day from that file, when
  its `judge_file` names it (contract section 17).
- **A finding is explained once.** The same findings are not explained again; new findings are.

Set it up with `--example-config`: the `claude` CLI's full path, a `claude setup-token` token under the name
`writer_token_env`, and optional read-only keys for the flagged text. Without the keys a finding is explained from
its fields alone.

Good: a note after the page, "The judge, a model, says: Someone not on your list added the label. Look at who."
Not that: a page that waited for the judge, or a note that says the finding is fine and changes a level.

## What it cannot do

- **It can't tell a session from the owner.** Sessions post to GitHub as the owner, so a label or a
  marker by the owner's own login passes. The protected-label guard in the hooks and the CI check on
  who applied `hooks-change` hold that line, not this.
- **A deleted comment is gone.** It reads on each pass. A comment a worker deletes between two
  passes is not seen.
- **A sleeping Mac runs nothing.** The silence looks like health. Only a check off this machine
  closes that (ADR decision D7).
- **Its own account and its install aren't built yet.** Until KIT-517, a pass run from a worktree
  copy is only as independent as that copy.
- **The usage file's day is the local day.** The watcher account's time zone must match the usage
  job's, or `spend` and `runs` go blind near midnight, which is loud.
