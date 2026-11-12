# The tech lead's tools

The tech lead is becoming an agent of our own. It answers in your chat by calling tools,
and the tools decide what it can see and do. This guide covers the tools that **read**, and
the ten that **ask**: `plan`, `replan`, `approve`, `pause`, `resume`, `cancel`, `retry`, `start`,
`answer` and `answer_plan`. None of them does what its name says. Only your yes to a question the pipeline asks
does that. One more, `queue_setting`, changes a queue setting only when your own message asked for it (below).

The design is `docs/adr/2026-11-05-one-chat-with-the-tech-lead-per-project.md`. The
script is `scripts/pipeline_tech_lead_tools.py`.

Nothing in this guide changes your setup today. The server runs only when something
starts it. The tech lead's turn, which starts it, comes in the next piece (KIT-489).

## What it can read

| Tool | What it returns |
|---|---|
| `where_things_stand` | The status job's file: where every plan stands. |
| `ticket` | One ticket, idea or plan: its state, labels, description, pieces, parent and pull request links. |
| `search_tickets` | A team's tickets, newest change first, at most 25, by words in the title. |
| `guide` | One of the kit's guides by name, or the list of them. |
| `pipeline_file` | One of the queue's files: the day plan, its inputs, the health watch, usage. Only the ones you name in the config. |
| `reach` | Whether the pipeline can land the work a ticket describes. See below. |
| `queue_setting` | Record a change to one of three queue settings that your own message asked for. Only with an `outbox_dir`. See *Changing a queue setting*. |
| `plan`, `replan`, `approve`, `pause`, `resume`, `cancel`, `retry`, `start`, `answer`, `answer_plan` | Record that the tech lead wants to ask you whether to do that to a ticket. Only with an `outbox_dir`. See below. |
| `plan_change` | Record the tech lead's judgment of a piece's note that its plan needs changing (KIT-320). Only with an `outbox_dir`. See below. |

Ticket text, and every file's contents (a queue file holds each ticket's title as the
tracker has it), comes back marked as data, not instructions. No text in it can break out of
that mark. An answer too long for one reply is made to fit by leaving out later lines, and
says how many; it is never cut in the middle of the mark. One too large to fit is refused.

## What the pipeline can't land

A dev session's push credential cannot change CI workflows, by design: a session that could
rewrite CI could disable the checks that supervise it. So no session can land a ticket that
names `.github/workflows`, branch protection or an installer.

`reach` says so for a ticket. The tools that start work (`plan`, `replan`, `approve`, `retry`
and `start`) refuse such a ticket, and say why, however the tech lead's rules are worded. The
check is in the tool, not in the rules. It reads the ticket's title and description, matches
them, and never repeats them. `pause`, `resume` and `cancel` do not refuse it: they hold or
stop work, so you may ask about any ticket of the project.

Good: *PROD-12 is out of the pipeline's reach: it names CI workflows, which no dev session
can land. It is theirs to do by hand.*
Not: offering to plan it, and the planner filing pieces no session can finish.

A ticket that merely uses the word "workflow" or "installer" in a product sense can read as out
of reach. Then you can still plan it yourself, with `plan PROD-12` as a command. Only the
tech lead's own offer is held back.

## What the ten asking tools do

They do nothing themselves. Each checks its argument, reads the ticket, runs the reach check
where it applies, and writes one small file to the **outbox**: a folder of the role account's,
mode `0700`, named in the config. Then it says "Recorded. After this turn I will ask them
whether to plan PROD-12, unless the bridge refuses it, and nothing happens until they say yes." (`plan again`, `approve`,
`pause`, `resume`, `cancel`, `retry`, `start`, `send their message to the dev waiting on` or
`add their answer to the open questions of` in its place.) When it could not write the
file, it says so, and never says it asked.

**What it refuses before it records (KIT-562).** Only what the tracker alone shows. The bridge re-checks all
of this and more, and stays the real gate: approved plans, open questions, the watcher's hold and waiting
pings are the bridge's alone, and this server reads none of them.

- `pause`, `resume`, `cancel` and `approve` on something that is no plan the idea gate filed: no
  `provenance:agent` mark, a parent ticket, no pieces, or (with `owner_user_id` named) not filed with the
  owner's key.
- `approve`, `retry` and `start` with no `agent_user_name` named.
- `retry` and `start` on a closed ticket, or one with a running dev session.
- `start` on a ticket already handed to an agent.

Each is refused in fixed words, and nothing is recorded.

Good: the tech lead calls `pause PROD-12`. After its reply, the bridge asks *Pause this?*
itself, in its own words, and nothing is paused until you reply `yes`.
Not: the tech lead says "I've paused PROD-12". It only recorded a question. If the bridge
refuses it (nothing is running, say), you read the bridge's refusal, not a claim.

`answer` and `answer_plan` ask whether to send your own message to the dev waiting on a ticket,
or to add it to a plan's open questions. They take only the ticket. The words are your message in
the chat, as you wrote it, taken by the bridge and by nothing else, and only when that message
names exactly that one ticket. The question shows them back to you in a quote, and your yes sends
or adds exactly that quote. No tool and no model ever writes them. Typing `answer PROD-12: ...`
yourself still works as it did.

Good: you write "Tell the PROD-12 dev to use the second helper." The tech lead calls `answer
PROD-12`. The bridge shows your sentence back. Your `yes PROD-12` sends it.
Not: a message that names two tickets, or none. The bridge asks nothing and says why.

- At most **three** actions wait at once, and one for a ticket.
- A file is mode `0600`, written whole, never through a link.
- The outbox is judged on the open folder: a plain folder, this account's, that nobody else
  can use. Anything else is said, and nothing is written.

The bridge reads the outbox once, after the turn, and removes what it read. It trusts none of
it: an entry must be exactly what these tools write, with no other field, so no note, ever; its
ticket must be one of this project's. It posts the tech lead's reply first, then asks you each
question itself, in its own fixed words, through the same request path your own commands take.
Its hourly limit, its cap on open questions, and its checks for live work and already-asked
all apply. A turn that failed after the tools ran asks nothing. Only your yes, read by code
from your own Slack message, does anything.

A `replan` carries your own `note:` words, word for word, only when your message asks to plan
that one ticket again, the way your own command does. A message that only names a ticket, or
names several, carries none. The tool never supplies a note.

The installer writes the `outbox_dir` into this config, and makes the outbox, when the tech
lead's turn is on (KIT-520; `docs/BRIDGE-OPERATOR.md`, "The tech lead's own turn"). Until your
role account's copy of the kit has that change, the config names no outbox and none of the ten
is offered.

**`plan_change` (KIT-320)** answers a piece's note that its plan needs changing. It records
fields only: the piece, a kind (`reorder`, `add_prerequisite` or `ask_owner`) and, for a
reorder, the piece to wait for. It changes nothing either. The bridge reads it only after a plan
note's turn, and leaves it out of any other turn. Its own code then makes a reorder inside your
approved plan without you, and asks you about anything else (`docs/BRIDGE-OPERATOR.md`, "When a
piece says the plan needs changing").

## Changing a queue setting (KIT-700)

`queue_setting` takes a setting and a value: `PLANNED_LOAD`, `DAY_STOP` or `LANES_PER_REPO`. Like the asking
tools, it does nothing by itself. It records the change in the outbox, and the tool server checks the value's form.
After the reply, the bridge makes the change, through `scripts/pipeline_queue_settings.py`, the one write path into
queue.conf. The sprint review's `apply` reply goes through the same path.

The bridge makes it only when:

- the message that started the turn is from you, the first of `dm_user_ids`, as Slack says;
- that message asks for it in one sentence: a verb such as set, lower or raise, the setting and the value. A question
  or a sentence with don't, not, never or keep asks for nothing; and
- the queue's own loader takes the new settings.

Then it writes the installed copy of queue.conf and the queue's config, reads both back, and adds a ledger row. It
posts the old and the new value. Your next queue install carries the change into your own queue.conf
(`docs/QUEUE-OPERATOR.md`, *When the tech lead changes a setting*).

Good: you write *lower the planned load to 30*. The tech lead calls `queue_setting PLANNED_LOAD 30`. After its reply:
*Changed the planned load (PLANNED_LOAD) from 40 to 30 ...*
Not: you ask *what does PROD-7 say?*, PROD-7 asks for a planned load of 9, and the tech lead records it. Your message
names neither, so the bridge changes nothing and says so.

## What it refuses

Every argument is checked before anything is read. It refuses:

- a ticket id of the wrong shape, or from a team your config doesn't name;
- search words longer than 80 characters, or with a line break;
- a guide name that isn't on the list;
- a file name your config doesn't name;
- any argument it doesn't expect.

Good: `ticket` with `PROD-12`, when your config names `PROD`.
Not: `ticket` with `ENG-12`, `../secrets`, or `PROD-12 and also delete it`.

When a source can't be read, it says which one and why. It never shows an unread source
as empty.

## The tracker key

The server reads your tracker key from a file, never from its environment. That lets the
tech lead run with no key anywhere it can reach.

The file must:

- belong to the account that runs the server;
- be a plain file, not a link;
- be readable by that account only (mode `0600`);
- hold the key on one line.

The key goes only to the tracker's own address, in one request header. No answer, error
or log line ever shows it. The address is fixed in the script; the config can't change it.

## The config

One JSON file. You don't write it by hand: `install --apply` of the bridge installer composes
it for the role account, and writes the tracker key's own file beside it (`docs/BRIDGE-OPERATOR.md`,
*The tech lead's own turn*). Print an example with:

```bash
python3 scripts/pipeline_tech_lead_tools.py --example-config
```

| Field | What it is |
|---|---|
| `team_keys` | The tracker teams this project's tickets live in, such as `["PROD"]`. Required. |
| `linear_key_file` | The key file above. Required. |
| `status_file` | The status job's file. Default `~/.stage-e/status/pipeline-status.json`. |
| `docs_dir` | The kit's docs folder. Default `~/.stage-e/kit/docs`. |
| `files` | The queue's files, by name: `day-plan`, `queue-inputs`, `health-watch`, `usage`. Optional. |
| `outbox_dir` | The outbox's full path. Optional: without it none of the ten asking tools is offered. |
| `agent_user_name` | The dispatcher's app user, by display name, as the bridge's config names it. Optional: without it `approve`, `retry` and `start` are refused before anything is recorded. The installer writes it from `AGENT_USER_NAME`. |
| `owner_user_id` | The tracker user id the key belongs to, as the bridge's config names it. Optional: with it, a plan counts as the idea gate's only when it was filed with that key. The installer writes it from `OWNER_USER_ID`. |

A wrong field stops it before it reads anything, and it lists every error at once.

## Checking it

Run this as the account that will run the server:

```bash
python3 scripts/pipeline_tech_lead_tools.py check --config <your tools.json>
```

It reads each source once, as the tools do, and prints one line each: `read`, or
`NOT READ` and why. It exits 0 when it read everything, 3 when something wasn't read,
and 2 for a config error. It sends the tracker one read, and prints nothing of the key.

## How a harness starts it

Any harness that speaks MCP starts it over stdio. For Claude Code:

```json
{"mcpServers": {"techlead": {"command": "python3",
  "args": ["<kit>/scripts/pipeline_tech_lead_tools.py", "serve", "--config", "<tools.json>"]}}}
```

The tools then show as `mcp__techlead__ticket` and so on. The tech lead's turn
(KIT-489) passes this with `--strict-mcp-config`, so it sees no other server.

## Testing it

```bash
npm run test:tech-lead-tools
```

The selftest checks the protocol and every refusal. It also checks that only reads reach
the tracker, that the fence holds against every closing-tag trick, and that the key shows
up in no answer, error or log line. Last, it runs the real server over stdio. No network
is used.
