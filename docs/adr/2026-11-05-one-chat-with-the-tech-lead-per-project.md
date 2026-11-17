# One chat with the tech lead per project: a tech lead of our own, over a gated tool layer

- **Status:** Accepted, 5 Nov 2026 (KIT-473). The owner chose this direction that morning,
  in place of the first draft of this note, which only re-worded today's chain, and said yes
  to the design that afternoon. They chose the plain DM (choice 2).
- **Date:** 2026-11-05
- **Piece 5, the cutover:** built 11 Nov 2026 (KIT-492). The tech lead's turn is on by default.
  The back room, the relay, request lines, the chat bot's rules and the writer are out of the
  kit, and the bridge's `verify` says, in its `old-paths` row, whether a deployment still runs
  them.
- **Builds on:** `2026-10-10-slack-bridge-acts-for-the-owner.md` (code acts only on a
  member's yes) and `2026-10-30-one-dm-tech-lead-voice.md` (one DM). The safety rules of both stand.
  The back room and relay of the second go.

## Context

### Before: the first live morning, 5 Nov

In the order the owner saw it:

1. The rundown, in their DM.
2. In the back room, which they can see: *"The morning rundown went out in the owner's DM. — the
   pipeline woke you, not a person: wake 1, ask 1"*.
3. The tech lead's long answer in that thread, then *"Pipeline: plan TOD-143: asked them in
   their DM…"*.
4. In their DM, the same answer again, under *"The tech lead is reaching out:"*.
5. In their DM: *"Plan this? TOD-143 … Set up by the tech lead after I woke it at 9:01am. The
   tech lead picked TOD-143. (I couldn't reach my writer, so this one's the plain version.)"*

In their words, they had no idea who was supposed to be who. They met four speakers:
- **the bridge**, the code that asks them questions and acts on their yes;
- **the tech lead**, an AI in the dispatcher's chat lane, under the name the owner gave it;
- **the back room**, the channel where the two of them talk;
- **the writer**, a model that words the bridge's messages.

The tech lead also offered to plan work the pipeline can never land: TOD-143 changes CI
workflows and branch protection.

### Why it grew this way

The tech lead lives in the dispatcher's chat lane. That lane can't read a DM, start a
conversation or act, so everything was added from outside: the bridge reads the DM, the back
room carries their question to the chat bot, a relay carries the answer back, request lines in
the answer are parsed into the bridge's questions, and a writer words the bridge's messages.
Each piece is safe. Together they show the owner the wiring.

They asked: why isn't the tech lead simply an agent with tool calls that let it see and act
into the system in a controlled way, as Claude Code already does? This note is that design.

## Decision

### 1. A tech-lead runner of our own, one Slack app per project

A small job of our own reads the owner's DM with one Slack app per project. It is today's
bridge job, which already reads the DM and holds the yes logic, with the back room and the
relay taken out. The dispatcher keeps dispatching dev sessions only. The tech lead leaves its
chat lane.

Each DM message takes one of two paths, chosen by plain code:

- **An answer to an open question** (`yes`, `no`, `yes <id>`) goes to code, never to the
  model. Code checks it as today, then acts.
- **Anything else** starts one tech-lead turn: the official, unmodified `claude` CLI, headless,
  under the owner's own sign-in. Each Slack thread, or the DM's running conversation, is one
  session, resumed each turn.

The turn's command line, from the CLI's own reference (checked 5 Nov):

| Flag | What it does here |
|---|---|
| `-p` | one headless turn; its final text is the reply |
| `--resume <session>` | the thread's conversation, carried from turn to turn |
| `--mcp-config <ours> --strict-mcp-config` | our tool server and no other MCP server |
| `--tools "Read"` | no Bash, no Edit, no web: the built-in Read tool only |
| `--allowedTools "mcp__techlead__*" "Read(<path>)"…` | our tools, and Read of exact paths only (the guides, the status file) |
| `--permission-mode dontAsk` | any call no allow rule covers is denied, never asked |
| `--permission-prompts none` | nobody answers a prompt in a headless run, so it is denied |
| `--append-system-prompt-file` | the tech lead's rules, with no command grammar to teach |
| `--max-turns` | a runaway's limit |

`--permission-prompt-tool` is not used. It answers a prompt during the turn, and the owner's
yes can come hours later.

### 2. A tool layer, served over MCP

One tool server of our own, plain Python, over MCP, so any harness can call it later: Claude
Code today, others such as Codex tomorrow.

**Read tools run freely:**
- where things stand, from the status file;
- tickets, ideas, plans and their pieces, and a search across them, from the tracker;
- the kit's guides, by name;
- the queue, the day plan and the health watch's findings, from their files.

**Acting tools never act.** Each one checks its arguments, then records a pending action and
returns at once: *"I asked them. Nothing happens until they say yes."* The tools are plan,
replan (with a note), approve, pause, resume, cancel, retry, start, answer a dev, and answer a
plan's open question. After the turn, the runner posts the model's reply. Each pending action's
fixed question follows it, in the same message, with its terms in the first person. When they
say yes, code acts, through the ACT core that runs today's questions.

### 3. The rules that don't move

- **Every acting tool treats its arguments as hostile.** It checks the ticket's shape, its
  team, who filed it, its state, and the action limit, as the bridge's request path does today.
- **Caps and role limits live in the tool layer,** never in the model's rules.
- **The model never holds a token.** The tool server holds the tracker key. The runner holds
  the Slack token. The model has neither, and no Bash to read either.
- **The model gets our tools and exact Read allows, nothing else.**
- **Nothing the model writes can stand for their yes.** A yes binds only to a question code
  recorded, from a sender Slack itself vouches for, with every check that holds today: the
  window, the fingerprint, the promised settings, the action limit, and an edited message.
- **Work the pipeline can't land is never offered** (the proposing side of KIT-108). A dev
  session's credential cannot change CI workflows, by design. So one check in the tool layer
  says whether a piece of work is within the pipeline's reach: it is not when it names
  `.github/workflows`, branch protection or an installer. The plan and replan tools refuse
  such an idea and say why, so the tech lead cannot offer it, whatever its rules say. A read
  tool answers the same check, so it can tell the owner the work is theirs by hand. The
  planner uses the same check when it moves onto the layer (KIT-485). KIT-108's bounce side,
  in the review and bounce loop, is separate.

### 4. After: the same morning

1. One message in their chat with the tech lead: the rundown, built by code from the files, as
   today (workers ADR, decision 6). It is for their visibility, like a manager who needn't attend.
2. The tech lead's take rides inside it, only for a blocker or a real decision: *"TOD-143
   changes the first project's CI and branch protection. I can't land that, so it's yours by hand."* (KIT-485)
3. On a day it does propose, the same message ends with the fixed question: *"Want me to plan
   PROD-6? Just say yes."* They say yes, and code plans it.

Nothing shows in a channel they can see. Nothing is shown twice. No note about a writer.

### 5. What goes

- the back room, and its forward and relay;
- request-line parsing, ask numbers, and the chat bot's rules file;
- the bridge's separate voice: the reply is the tech lead's own words;
- the writer, for the tech lead's own words. Fixed questions stay fixed text. The stand-up
  keeps its code-built facts.

### 6. What it must allow later

- The planner, Stage E's findings and the devs' follow-up requests move onto the same tool
  layer, one at a time.
- The dream app's interface (KIT-278) calls the same gated tools.
- The plumbing stays plain code, and becomes what the tools are made of: the status job, the
  notifier, the watchdogs, the sweep, the queue and the merge gates.

### 7. Where this goes

This note is the first step only: the tool layer, and the tech lead as its first agent. The
longer direction is recorded in the pipeline's roadmap, under *How it's built: agents over one
tool layer* (decided 5 Nov). In short: each judgment role becomes an agent over the same gated
layer; plumbing stays plain code; MCP is the seam to any harness. This note does not restate
that direction, and nothing here assumes more of it than section 6.

## The owner's choices

**Choice 1, one app per project or one app for all, is settled:** one app per project falls
out of this design. Each project's runner holds one Slack app, one tracker scope, and one
tool server, so a yes can only reach that project.

**Choice 2, settled 5 Nov: the plain DM**, as recommended, over Slack's AI-assistant surface. The
assistant surface gives the app a top-bar entry and a split view. Slack says it needs a paid
plan, and it puts each conversation in a thread, where a reply notifies only that thread's
followers. The plain DM works on the free plan, and every message notifies them. The runner
fits either, so the owner can move later and lose nothing (KIT-480 waits for that).

## The terms

Claude Code's legal page (5 Nov) says OAuth sign-in is for subscription purchasers and
"designed to support ordinary use of Claude Code". It says nothing prevents "an end user from
signing in to the unmodified Claude Code binary with their own Claude subscription". The
runner runs the unmodified binary, signed in as the owner, for their own use. It offers no one
else a login, and resells nothing. A kit user runs it under their own sign-in. Advertised
limits "assume ordinary, individual usage", and each turn counts toward their plan's limits.

## Build pieces

Each is one PR. The first two fit before any acting changes, and run beside today's chain.

1. **The tool server's read side** (KIT-488). MCP over stdio: status, tracker reads and
   search, guides, the queue's files. The tracker key in the server only.
2. **The runner's turn** (KIT-489). A DM message that is not an answer runs one headless turn
   with the flags above, the thread's session resumed, our tools only. Behind a switch: off,
   today's forward runs.
3. **Acting tools, plan and replan** (KIT-490). A pending action and its fixed question after
   the reply, acted on their yes by the ACT core.
4. **Acting tools, the rest** (KIT-491): approve, pause, resume, cancel, retry, start, answer a
   dev, and answer a plan's open question.
5. **The cutover** (KIT-492). The tech lead leaves the dispatcher's chat lane. The back room,
   relay, request lines, ask numbers, chat rules and the writer for its words go.
6. **Reaching out** (KIT-493). The rundown and the other set moments become runner turns, with
   the tech lead's take inside the rundown only for a blocker or a decision.

Kept from the first draft: one identity per project (KIT-477), an optional alerts channel
(KIT-479), the assistant surface if they choose it (KIT-480), and KIT-485, which moves into
the plan tool. Canceled as no longer fitting: KIT-475 (one voice) and KIT-478 (hide the back
room), which piece 5 replaces; KIT-476 (pages in its voice), split between pieces 4 and 6; and
KIT-486 (a rundown pings only for a blocker), which piece 6 carries.

**What could land by Friday:** pieces 1 and 2. That gives a read-only tech lead in their DM,
with our tools, beside today's chain. **After Friday:** pieces 3 to 6, then the planner, Stage
E and dev follow-ups onto the tool layer, one at a time. So the one-chat cutover, piece 5,
comes after Friday, as the owner was told it might.

**Migration.** Today's chain runs until piece 5. Each piece before it is behind a switch.
Piece 5 turns the old paths off for a day before removing them.

## Rejected

- **Re-wording today's chain** (this note's first draft). It hides the wiring without removing
  it, and every new action would still need a request line, a relay and a parser.
- **Keeping the tech lead in the dispatcher's chat lane.** That lane can't read a DM, start a
  conversation or act, which is why the wiring exists.
- **`--permission-prompt-tool` as the approval.** It answers during the turn, and a yes can
  come hours later.
- **A model that holds a token, or Bash.** Then a steered turn could act without their yes.

## Not proven

- That a resumed headless session keeps its tool allows and rules turn after turn, under
  launchd, with the owner's token from `claude setup-token`. The writer proved the token
  under launchd; the resume is to prove.
- That an MCP server started from `--mcp-config` gets the tracker key from its own config
  without the model ever seeing it.
- The cost of a turn on their plan's limits at a normal day's volume.
