# The tech lead's tools

The tech lead is becoming an agent of our own. It answers in your chat by calling tools,
and the tools decide what it can see and do. This guide covers the first set: the tools
that **read**. None of them writes anything.

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

Ticket text, and every file's contents (a queue file holds each ticket's title as the
tracker has it), comes back marked as data, not instructions. No text in it can break out of
that mark. An answer too long for one reply is made to fit by leaving out later lines, and
says how many; it is never cut in the middle of the mark. One too large to fit is refused.

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

One JSON file. Print an example with:

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
