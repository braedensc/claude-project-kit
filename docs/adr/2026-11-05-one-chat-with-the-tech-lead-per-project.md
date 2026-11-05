# One chat with the tech lead per project: the bridge, the back room and the notifier speak as it

- **Status:** Proposed (KIT-473). Waiting on the owner's yes to this whole design, and on his
  two choices below. No piece starts before his yes.
- **Date:** 2026-11-05
- **Builds on:** `2026-10-30-one-dm-tech-lead-voice.md` (one DM, the writer, the back room) and
  `2026-10-10-slack-bridge-acts-for-the-owner.md` (the bridge acts only on a member's yes).
  Neither changes its safety rules here.

## Context

On 5 Nov the owner said the tech lead's setup in Slack is too confusing to use. He sees three
things:

- a DM with the bridge's app;
- a channel that holds the tech lead and the bridge, the back room;
- the notifier's pages.

He wants one chat with the tech lead per project. In it he asks questions and gets tasks done
in plain conversation. In his words: "it should flow gracefully, just like my chat with you
here." A separate channel for pings and alerts is fine.

Under the hood the split stays, because it is the safety boundary:

- **The tech lead proposes.** It is an AI with read tools. It never acts.
- **The bridge acts.** It is plain code, and acts only on a member's `yes` to its own question.
- **The notifier pages.** It is a separate job, so a dead bridge can still be reported.

That split is how the safety works. It must not be what the owner sees.

## What he sees today, and where each part comes from

| He sees | It comes from | Why it shows |
|---|---|---|
| A DM with an app named for the bridge | the bridge's Slack app | the app's name and avatar are the bridge's |
| "the tech lead picked…", "Set up by the tech lead…" | the bridge's fixed sentences | they name the tech lead as a third party |
| A channel with the tech lead and the bridge | the back room (KIT-298) | he is a member of it |
| Pages in the same DM, or from a second app | the notifier (2026-10-30, decision 2) | a separate app where DM mode is off |
| Commands like `approve PROD-50` | the bridge's grammar | the help line teaches them first |

## The owner's two choices

### Choice 1: one Slack app per project, or one app that knows the project from context

**One app per project (recommended).** Each project gets its own Slack app, its own bridge
and its own DM. The app is named for the project's tech lead, such as *Tech lead · todoclaw*.

- A `yes` in one project's chat can only ever reach that project's bridge, its tracker teams
  and its repository. That holds by construction, with no routing code to get wrong.
- Each bridge is already one deployment, with its own config, state, token and team keys.
  Nothing in the bridge changes. The installer writes one manifest per project.
- It costs one more app and one more DM per project in his sidebar. Each install is one
  scripted run.

**One app that knows the project from context.** One DM for every project. The project
comes from a ticket's team key (`PROD-6` is one project, `TOD-6` another) or from asking.

- One name in one place, which is closest to "my chat with you here".
- But one bridge process then holds every project's tokens and acts on every project's
  tracker. A free question with no ticket id must be routed by asking, or by a model's guess.
  A guess must never decide where an action lands, so every action would need a ticket id.
- That is new routing code across the bridge, the notifier and the chat lane, in the path
  that decides what a `yes` moves.

**Recommendation: one app per project now.** It keeps "a yes reaches one project" true by
construction. The dream app (KIT-278) is where one agent across projects belongs, and it can
follow the same principle with its own routing.

### Choice 2: Slack's AI-assistant surface, or the plain DM as today

Slack offers apps an assistant surface. The research for this note, 5 Nov:

- An app turns on *Agents & AI Apps* and takes the `assistant:write` scope. It then gets an
  entry in Slack's top bar and opens in a split view beside any channel.
- Conversations are grouped into threads in the app's Messages tab. The app can set a title,
  suggested prompts, and a "thinking" status for each thread.
- **Slack says using these features needs a paid plan.** A free plan sees the settings, but
  real use needs a paid one. A free developer sandbox exists for testing only.
- The method names have changed across Slack's docs (`assistant.threads.*`, then
  `agents.sessions.*`). The current names must be checked when built.

**The plain DM as today (recommended).**

- It works on the free plan, which this workspace uses: its private channel is its access
  control (2026-09-11).
- Every message is top level, so every one notifies him. The 2026-10-30 note chose that on
  purpose: he missed thread replies, because a thread notifies only its followers. The
  assistant surface puts each conversation in a thread.
- The voice pieces below fit both surfaces, so moving later loses nothing.

**What the assistant surface would cost:** a paid plan; the new scope and events in the
manifest; one more reader in the bridge for the thread events; and a test that a reply in an
assistant thread notifies him. Worth it only if he wants the split view itself.

## The design, under either choice

1. **One identity.** The app the owner talks to is named and pictured as the tech lead, one
   per project. The manifest sets its display name and avatar. The dispatcher's own chat app
   stays in the back room, where he never looks.
2. **One voice.** Every question, confirmation and result is worded as the tech lead
   speaking: "I'll hand PROD-6 to the planner", "I picked PROD-6, so the yes must name it".
   No message names "the bridge", calls the tech lead a third party, or starts `Pipeline:`.
   `Pipeline:` notes stay in the back room, where they tell the tech lead what its last block
   led to; he never sees them.
3. **Pages in the same chat, alerts optionally apart.** Notifier pages already arrive in the
   DM through the bridge's app (2026-10-30, decision 2). They gain the same voice. A new,
   optional alerts channel takes the health pages only, for an owner who wants them apart.
4. **The back room hidden.** He leaves the back-room channel. The bridge's bot and the chat
   bot stay in it. The installer's `verify` says whether he is still a member, and never
   needs him there.
5. **Natural phrasing for every action.** Commands stay as shortcuts. His own words already
   reach every action: plain asks by the fixed reader (ACT-3), and anything else through the
   tech lead, which sets up any verb in its block. One gap remains: adding an answer to a
   plan's open questions (KIT-334) has only its command. The tech lead's block gains it.

## The safety model is unchanged

- **The AI never acts.** The tech lead reads, files one idea, and proposes lines. The writer
  has no tools. Neither holds a write that moves work.
- **Only a yes acts,** to a question the bridge itself recorded, from a sender Slack itself
  vouches for (`users.info`), with every check that holds today: the window, the
  fingerprint, the promised settings, the action limit, and an edited or deleted message.
- **The terms a yes agrees to stay fixed text.** Rewording them in the first person changes
  who seems to speak, never what a yes does. The selftest pins every one word for word, as
  today.
- **What changes is names and wording,** and one optional channel. No new token, scope beyond
  the manifest's display fields, write or command.
- **One residual grows a little.** With one voice, he can no longer tell a relayed AI answer
  from the bridge's own fixed sentence by the name on it. The fixed sentences stay
  recognisable by shape: the terms line, and a question that asks for `yes`. A relayed
  answer is still never a question the bridge recorded, so a yes cannot bind to it.

## Build pieces, in order

Each is one small PR, filed under KIT-473 as KIT-475 to KIT-480. None starts before the owner
says yes to this design. Once he has, the first two can start while his two choices are still
open, since they fit either answer.

1. **One voice for the bridge's own words.** Every sentence the bridge posts to a member is
   worded as the tech lead: "I", never "the bridge" or "the tech lead". The pinned terms
   change their subject only. A selftest refuses "bridge", "the tech lead" in the third
   person, and `Pipeline:` in anything posted outside the back room.
2. **One voice for the notifier's pages, and natural phrasing's last gap.** The pages read as
   the tech lead speaking. The tech lead's block can set up an answer to a plan's open
   questions.
3. **One identity per project** (after choice 1). The bridge installer's manifest takes the
   project's name and an avatar. `verify` reads the app's name back.
4. **The back room hidden.** `verify` says whether the owner is still in the back room. The
   guide tells him to leave it, and why nothing breaks.
5. **An optional alerts channel.** Health pages go there when it is set. Every other page
   stays in the chat.
6. **The assistant surface** (only if choice 2 is the assistant). The manifest's scope and
   events, the thread reader, and the notification test.

**Migration from today.** Pieces 1 and 2 change words only: the next pass speaks the new
way. Piece 3 renames the existing app in place, with no new token, for the first project. A
second project gets its own app from its own install. Piece 4 is the owner leaving one
channel. Nothing is removed until its replacement has run for a day.

## Rejected

- **Merging the bridge into the tech lead,** so the AI acts. That is the boundary this whole
  design keeps.
- **Hiding which messages are fixed text.** He reads the terms before he says yes. Their
  shape stays the same.
- **One app for every project now.** It moves "which project does this yes reach?" from
  construction into routing code.

## Not proven

- That renaming an installed app's display name keeps its bot token. If it changes, the
  token step runs again.
- That the assistant surface works on this workspace's plan. Slack says it needs a paid plan.
- That an assistant-thread reply notifies the owner the way a top-level DM message does.
