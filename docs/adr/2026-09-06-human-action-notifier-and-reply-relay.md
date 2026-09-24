# Human-action events leave the tracker through a dispatcher-side notifier; replies re-enter only as session-thread comments

**Date:** 2026-09-06 · **Status:** **Accepted** — all six decisions ratified by the owner
2026-09-11; decision 1 amended from Telegram to **Slack**, decision 3 refined
· **Context:** the notification-channel investigation (KIT-106), branch
`docs/kit-106-notification-channel-adr`. Extends build record §15 (*Where a person actually
lives in this*); builds on
[Stage E under a delegation-bound dispatcher](2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md)
and [Fencing tracker text as untrusted data](2026-08-24-untrusted-ticket-data-fence.md).

## Decision

**The events that need a person are pushed to a dedicated chat channel by a small
dispatcher-side job, not by a session.** The job is the Stage E poller's sibling: same
account, same env-file rule, same state directory, same one-tick-per-interval shape. It reads
the tracker and the code host for the marks the pipeline already writes, and it sends one
short message per event. It runs outside the session sandbox, so **the session egress
allowlist is not widened** and the channel credential never enters a session's environment.

**Replies are the second step, and a reply can only ever become one comment in one
session's thread.** The job polls the channel for replies, keeps only those from the one
pinned human account that are replies to a message it sent itself, resolves the target session
from its own state file, and posts the text as a comment in that session's agent thread —
through the same re-prompt function the bounce driver uses. No command grammar exists.
Approving, merging, labelling and state changes never transit the channel.

**The channel for the reference deployment is Slack, in a private one-person channel**
(ratified 2026-09-11; see decision 1, which records the error that first pointed elsewhere).
The property that decides it is that the channel can be **read by polling** — a plain
authenticated GET with a timestamp and a cursor — so no inbound URL, no open port and no
persistent connection are needed, and the same one-shot job can send and receive. The decision
is written against that property, not the product; any channel with the same property fits.
Slack adds two things on top of it: the channel is the durable store, so an offline stretch
costs nothing, and membership extends to a second person without a rebuild.

**Ships one-way first.** The one-way half is a day's work and carries no new authority; the
reply half is a designed step with the security model below, built and reviewed on its own,
and gated on the same live re-prompt test Stage E's bounce driver is gated on.

## Why

### The problem is delivery, not classification

Every human-action event is already distinguishable. A stopped session begins its comment with
the escalation marker `<!-- pipeline-escalation: agent:blocked -->` and reports `outcome:
blocked` (`/work` step 5, contract §4). A session that asks a question through the harness's
own ask-the-user tool becomes a tracker-side elicitation activity in the session thread, which
the tracker mirrors as a thread comment. A bounce budget spent is `agent:needs-human` (§6),
applied by the bounce driver. A review that could not run is a distinct "NOT reviewed"
comment with a non-zero exit (§13, §14). A PR ready to merge is a review verdict plus green
required checks. What is missing is a channel that carries only those. Build record §15
reasoned that ticket decisions belong in the tracker and that a chat tool is only justified for
ad-hoc questions; this ADR keeps both conclusions and adds one thing: the *ping* that a
decision is waiting must not compete with bookkeeping.

### Who sends — three candidates, one survivor

- **The session — rejected.** The dispatcher copies its whole environment into every session,
  so a channel credential in that environment is a credential every session holds. A session
  could then page the owner with any text, including text a stranger placed in a public PR.
  It would also need the channel host on the session allowlist, which hands every session an
  arbitrary-text sink (the KIT-83 finding: a grant cannot be scoped to a port or path). And a
  session announcing "PR ready, please merge" is the *agent producing a human's signal*
  defect class this kit is built against.
- **The safe-outputs executor — not available on this lane.** The executor exists on the
  GitHub-Actions backend. The live dispatcher runs none; there is nothing to attach a kind to.
- **A dispatcher-side job — chosen.** Outside the sandbox, so unproxied and unallowlisted;
  reads the tracker and the code host and nothing inside the dispatcher, so it survives the
  dispatcher's replacement; and its credential lives in the same file, mode and home the
  Stage E poller's key already lives in — no new credential class.

### Channel — assessed on the reply path, not the app

| Channel | Reply path | Verdict |
|---|---|---|
| Slack | **The Web API can simply be polled**: `conversations.history` / `conversations.replies` are plain authenticated GETs taking an `oldest` timestamp and a cursor, so a one-shot job reads the channel with no socket, no inbound URL and no long-running process. Its push transports (Socket Mode, Events API) do need a supervised socket or a public URL — but nothing obliges an app to use them. The channel itself is the durable store, so an offline stretch is recovered by asking again from the last timestamp | **Chosen** (2026-09-11) |
| Telegram bot | Outbound long-poll for updates; no inbound URL, no persistent connection; mutually exclusive with a webhook, so a poll-only bot has no inbound surface at all. **Its cost is retention**: undelivered updates are discarded after 24 hours and a bot has no history API, so a laptop offline for a long weekend loses them permanently | Viable; rejected on retention and on the multi-person path |
| Discord | The Gateway is a persistent WebSocket; interactions need a public URL; a bot can DM a user only with a shared server | No advantage |
| iMessage | No API. A daemon has no access to the window server, so the dispatcher account cannot drive the Messages app. A helper in the owner's own login session runs only while logged in and does not return after a reboot with FileVault on; it would run as the owner, outside the sandbox | Rejected: the shape Stage E's first design was rejected for |
| Push-only services (ntfy, Pushover) | None | One-way only; cannot grow into replies |

A public URL is a new inbound surface on a machine whose front door is deliberately a
three-path allowlist; a persistent connection is a new long-running process to supervise.
The poll-based reply API needs neither, which is why it fits the job that already exists.

### The reply path is not a new instruction source

Contract §3 and the untrusted-data ADR settle that everything a session can write is reporting
and that tracker text is data, not instruction. A reply relay runs straight into that doctrine
if it lets a channel message become an instruction. It does not, because of six facts, each
resting on something outside the chat:

1. **Sender pinning.** The job keeps only messages whose sender id is the one configured human
   account. The channel's servers produce every update the bot receives; the API exposes no
   method by which anyone, token holder included, injects an update with a chosen sender.
   Forging a reply therefore requires the owner's channel account — phone plus password and
   second factor — which is the same standing as the owner's tracker login on the same phone.
2. **Reply-to addressing.** A message is acted on only if it is a reply to a message the job
   sent, and the target session is looked up from the job's own state file, never parsed from
   the text. There is no command grammar to abuse.
3. **The only effect is a comment.** The relayed text lands as one comment in one session
   thread, prefixed so the record shows it came through the channel. That is what the owner
   could already type in the tracker. The session it reaches is still sandboxed and still
   guard-bound; it cannot merge, approve, label or edit its guards regardless of what the
   comment says. The channel adds no authority a tracker comment lacks, and tracker comments
   carry none.
4. **The bot token's blast radius is confidentiality and phishing, not command.** A token
   holder can read the owner's replies, send the owner messages, and redirect delivery so the
   job stops hearing replies. Every genuine notification links only to the tracker and the
   code host, where the platform authenticates the owner; the highest-authority signals
   (approve, merge, label, state) never transit the channel. Revoking the token ends the
   exposure.
5. **The job's tracker key is the poller's key.** Commenting and delegating as the owner is
   already what the Stage E poller can do, with the same file and the same accepted risk. No
   new key class is introduced.
6. **Bounded and idempotent.** One notification per event, keyed on the comment or activity
   that caused it so a restart resends nothing; a size and rate cap on relayed replies, above
   which the job logs and drops; angle brackets stripped from relayed text, because the
   dispatcher pastes a comment into its prompt inside an unescaped XML wrapper.

**Accepted, and named:** the dispatcher's re-prompt access check tests the *delegator*, not
the *commenter* — it never reads the commenter's id, and four of its re-prompt paths (the
stop signal, a re-prompt on a parked session, a repository-selection answer, and the answer
to a pending ask-the-user question) run with no check at all. Already accepted for the bounce
driver; the relay is one more writer in the same thread under the same acceptance, and in a
one-person workspace the only other writers are the owner's own tools. And the channel
provider stores the message text: a blocked-session question, which may quote code, sits on a
third party's servers. That is the owner's call and is listed below.

### One gap this closes as a side effect

Under a dispatcher that writes no pin, **nothing applies `agent:blocked`.** The session
requests it (§6 says it never applies it); on the GitHub-Actions backend the dispatch
workflow greps the marker and applies the label; on the live dispatcher no component reads the
marker, so a blocked ticket looks like any other in-progress ticket. The notifier must grep the
same marker to notify, and it is a dispatcher-side component, which is exactly the writer §6
names. This ADR proposes it applies the label as it sends, and that §6 gains one line saying
so for pin-less dispatchers. It is the owner's decision (below) because it makes a new job a
label writer. It is also what would make the tracker's own view-based routing usable at all.

### What stays in the tracker, on purpose

Ticket creation and state moves, plan and summary comments, finding tickets, clean review
verdicts, the tracker's own "your agent finished" notification to the delegator, post-merge
failure and conflict issues (already GitHub issues assigned to the owner), auto-merges (the
weekly review) and system-level events (the pinned status issue, per §15). The channel
carries the decisions that are the owner's and nothing else, or it becomes the noise it
exists to escape.

## Verified

Read this week from primary sources — the channel vendors' current documentation, the
tracker's developer documentation and SDK schema, the installed dispatcher's source
(v0.2.69), and the kit — by six readers, with two skeptics per load-bearing claim. Nothing
was wired: no bot, no token, no allowlist edit, no test message.

- **The dispatcher checks the session creator on a re-prompt, not the commenter.** The
  prompted-webhook handler calls an access check that reads `webhook.agentSession.creator`;
  the activity's author id is present in the payload and never read. The check runs only on
  the ordinary-continuation branch; the stop, parked-re-prompt, repository-selection and
  ask-the-user-answer branches return before it. Confirmed by both skeptics at the cited lines.
- **A re-prompt resumes the same session in the same worktree**, verbatim, with the comment
  body pasted unescaped into an XML wrapper (`resumeSessionId` → the harness's `resume`; cwd
  = the session's worktree). If the runner is still streaming, the text is injected live.
- **The dispatcher turns a harness ask-the-user call into a tracker elicitation** with a
  `select` signal, holds the pending question in memory only, and resolves it from the next
  prompted webhook. A daemon restart mid-question loses it; the eventual answer is then
  handled as an ordinary re-prompt. The dispatcher writes no session status on the tracker
  lane; the tracker sets state itself from the last activity.
- **The egress proxy is scoped to the session, not the daemon.** No proxy environment
  variable exists anywhere in the dispatcher's packages; the proxy ports reach the harness
  subprocess only as a per-session sandbox option, and the code's own comments say the proxy
  covers shell-spawned traffic only. A launchd job in the same account outside the dispatcher
  is neither sandboxed nor proxied. Confirmed by both skeptics.
- **No component under the live dispatcher reads the escalation marker or applies
  `agent:blocked`.** The only reader is the inert GitHub-Actions lifecycle job. Under an
  unpinned session the lifecycle-label and own-ticket guards stand down and the session
  holds a workspace-scoped tracker token, so today every `agent:*` label is session-writable
  in practice. Confirmed by both skeptics.
- **Slack** (added 2026-09-07, the correction behind decision 1). `conversations.history` and
  `conversations.replies` are plain authenticated GETs taking `oldest` + `cursor`, so a
  process that is offline between calls still retrieves everything posted in the interim — the
  channel is the durable store, bounded only by plan retention (90 days on free). Nothing in
  Slack's docs or terms obliges an app to use Socket Mode or the Events API; those are
  alternatives for *event delivery* only. Internal (non-distributed) workspace apps keep Tier 3
  limits — 50+ requests/minute, `limit` up to 999 — the May 2025 tightening applying only to
  commercially distributed apps. An **incoming webhook returns no message id**, so threading
  and tracking need a bot token rather than a webhook URL. On the free plan **guests are
  paid-only** and per-channel posting restrictions exist **only for `#general`**, so channel
  membership is the only access control — hence the private channel. Confirmed by two
  independent skeptics against the live pages.
- **Telegram.** One host, `api.telegram.org`; free, with per-chat rate limits and a
  4,096-character message cap; `getUpdates` is a bot-initiated pull needing no reachable
  server, mutually exclusive with a webhook; the token gives full control (read, send,
  redirect) and is revocable; no API method injects an update; `reply_to_message` carries the
  original message id; bot chats are cloud chats stored on the vendor's servers, not
  end-to-end encrypted; anyone can message a bot first, so the relay must drop every other
  sender. Confirmed by both skeptics on the live pages (Bot API 10.3).
- **Tracker-native routing.** A Slack channel or a person can subscribe to a custom view,
  and the only per-view triggers are "issue added to the view" and "completed/canceled";
  personal notifications are per-channel × per-category with no label filter; webhooks
  filter by team and resource type only. Thread sync is bidirectional only for explicitly
  synced threads; a reply under a channel notification post is not documented as syncing.
  An OAuth scope `comments:create` exists and personal API keys can be scoped to comments
  and teams. Confirmed by both skeptics.
- **Slack, Discord, push services.** Socket Mode needs a persistent WebSocket to a host
  returned at runtime and loses events while disconnected; the Events API needs a public
  URL; the Discord Gateway is a persistent WebSocket and interactions need a public URL;
  ntfy and Pushover have no reply feature. Read from the vendors' current pages.
- **iMessage.** A daemon has no access to the window server (Apple, *Designing Daemons* and
  TN2083; a 2024 Apple DTS answer says the rule stands); a LaunchAgent defaults to the Aqua
  session type and runs only while that user is logged in; automatic login is unavailable
  with FileVault on, which it is on the reference machine; the owner's Messages database is
  unreadable by the role account before TCC is consulted. One skeptic held that "cannot launch
  a GUI application" over-reads a 2016 page for a root daemon; the window-server conclusion
  stood.

**Not verified, and named as the gate on the reply half:**

- **That a comment created through the API in the session thread produces a prompt-type
  activity and so a re-prompt.** The tracker's docs say a re-prompt fires when a user creates
  a prompt-type activity, that agents cannot create one, and that the mutation for creating
  one directly is internal; they are silent on whether an API comment counts, or whether the
  actor type matters. The bounce driver rests on the same claim from the dispatcher's source
  and is gated on a live test (KIT-99). The relay is gated on the same test. **The one-way
  half does not depend on it.**
- Whether the tracker marks a session "awaiting input" on an elicitation. It publishes no
  activity-to-state table; the notifier keys on the elicitation activity being last, not on
  the state name.
- Whether the tracker restricts who may post into a session thread beyond workspace
  membership. Moot for one person; a question for the day a second joins.
- That the deployed dispatcher's sandbox is on: the startup banner read "deny-all with 19
  allowed domains" on 2026-09-03 per the field guide; the config file was not re-read here.

> **Update, 2026-09-17 — the first gate was met in production on 2026-09-08.** A comment
> created through the tracker API in a session thread did resume that session. The bounce
> driver posted "Stage E bounce 1 of 3" into a ticket's agent-session thread with the owner's
> key at 17:44:38 UTC, and the dispatcher's agent replied in the same thread at 17:45:58,
> answering it (TOD-117). That is the mechanism the relay uses, for the identity the relay
> writes with. **It does not show** that a comment from a *different* identity resumes a
> session: that is the cross-session question KIT-99 test 7 measures, still unrun. The owner
> accepted this as the relay's gate on 2026-09-17 (KIT-118).

## Owner decisions — all six ratified 2026-09-11

*Kept as the record of what was asked. Every one is now answered in the section below; nothing
here is outstanding.*

1. The channel itself, or the zero-code one-way fallback — which still needs the label writer.
2. Whether the question text may transit the channel provider, or only a title and a link.
3. One-way first as a separate step, or both halves in one build.
4. Whether the notifier applies `agent:blocked` (the §6 amendment).
5. Which PR event is the human moment until Stage E is on ("opened") and after ("reviewed
   and green").
6. Where the job lives: beside the Stage E poller, in whichever account that lands in.

## The decisions, as ratified (proposed 2026-09-09, ratified 2026-09-11)

These were recommendations when written; **the owner ratified all six on 2026-09-11**, and the
ADR moved to *Accepted*. Decision 1 changed in the ratification — from Telegram to Slack — and
decision 3 was refined; both carry a dated amendment note in place. They are written against this ADR's own *Verified* facts and
against the idea gate, whose executor (`scripts/pipeline_plan_executor.py`, shipped) is now a
concrete **producer** of the marks this notifier consumes. One synergy runs through all of
them: **the escalation content already lives in the tracker** — the executor posts the plan,
the rejection, the question, or the no-output note as a tracker comment carrying an invisible
mark — so the channel only ever has to carry the *ping*, never the content.

1. **Channel — Slack.** *Ratified by the owner 2026-09-11.*

   > **Amended 2026-09-11.** This decision originally read *Telegram*, and it rested on a
   > factual error in this ADR's own channel table: the Slack row assessed only Slack's
   > **push** transports (Socket Mode, the Events API) and concluded it needed a supervised
   > socket or a public URL. **Slack can simply be polled.** `conversations.history` and
   > `conversations.replies` are plain authenticated GETs with `oldest` + cursor, so the same
   > one-shot job that sends can also read — the exact property that made Telegram look
   > unique. The table row is corrected above. Verified 2026-09-07 against live vendor
   > documentation by two independent skeptics, along with: an internal (non-distributed)
   > workspace app keeps Tier 3 limits — 50+ requests/minute, up to 999 messages a page —
   > because the May 2025 tightening targets commercially distributed apps only.

   Three reasons, in order of weight. **Retention:** the Slack channel is the durable store
   (90 days on the free plan), where Telegram discards undelivered bot updates after 24 hours
   with no history API to recover them — so a laptop offline for a weekend loses nothing on
   one and everything on the other. **The multi-person path:** Slack is the only option whose
   model extends to a second person without rebuilding, which the owner wants to keep open.
   **The reply path costs the same either way** now that polling is on the table.

   Discord remains rejected (a persistent gateway or a public URL, and a bot can only DM
   inside a shared server), iMessage remains the sandbox-escape shape Stage E was rejected
   for, and push-only services still cannot reply.

   **One Slack-specific requirement this adds: the bot lives in a *private* channel.** On the
   free plan guest roles are paid-only and per-channel posting restrictions exist only for
   `#general`, so **channel membership is the only access control available** — and it is
   what supplies the identity gate the dispatcher's chat lane structurally lacks, since its
   user allowlist is never consulted on the Slack path. A public channel would let any second
   workspace member join uninvited and drive the agent with the owner's authority.

2. **Content — the ping carries a title and a link, never the question text.** A blocked-session
   question can quote code, and no mainstream chat provider stores it end-to-end encrypted —
   Slack holds message content on its servers under its own keys, as Telegram does for bot
   chats (Verified, above). It does not need to: the executor already writes the question as a
   tracker comment, so the notification is "**Planning needs your input on KIT-777** → <link>",
   and the owner clicks through to read and answer *in the tracker*, where the platform
   authenticates them. This keeps code off a third party by construction, not by a redaction
   rule that can miss. (On the reply half the owner's own short answer does transit the
   provider — capped and angle-bracket-stripped per the security model's fact 6; a one-line
   "yes, rotate on reuse" is the low-sensitivity case, and anything longer belongs in the
   tracker comment the link points at.)

3. **Sequencing — three pieces, not two.** *Refined 2026-09-11, because "two-way" was doing
   double duty and the ambiguity read as a conflict with the owner's stated requirement.*

   | Piece | When | Why there |
   |---|---|---|
   | **Notifier** — the ping plus a link | **First** | A day's work, adds no new authority, depends on nothing unproven |
   | **Conversational channel** — asking it questions, seeding an idea | **In the MVP** | This is what the owner means by "two-way". It needs no relay and does **not** depend on the re-prompt claim |
   | **Reply relay** — a reply waking a blocked session | **Deferred** | Gated on the same live re-prompt test as the bounce driver (KIT-99). The owner separately prefers tapping the link through to the tracker |

   So one-way-first and two-way-in-the-MVP were never actually in tension: only the *relay* is
   deferred, and it is the piece the owner wants least.

4. **`agent:blocked` — yes, the notifier applies it, and §6 gains one line.** Under a pin-less
   dispatcher nothing else is positioned to: the notifier is the dispatcher-side writer §6
   already names, and without a writer a blocked ticket is indistinguishable from an
   in-progress one (and the tracker's own view routing never fires). The session still only
   *requests* the label — it never applies it — so the §6 invariant holds. **Mapping for the
   planning lane** (the marks the executor now writes, and the principle behind each — see the
   table below): apply `agent:blocked` on `planning-needs-input` and `planning-no-output` (the
   run needs the owner, and no-output is the §13 silent case a label makes visible); do **not**
   apply it on `epic-awaiting-approval` (a normal approval, not a block) or `planning-rejected`
   (a loud, self-explaining comment already carries its own cue).
   **Named honestly:** this ADR's *Verified* section found that under the live unpinned
   dispatcher every `agent:*` label is session-writable in practice, so applying the label
   gives it a real writer but not yet integrity — tightening that is separate hardening, not
   this build.

5. **The PR human-moment — "opened" until Stage E is live, "reviewed and green" after.** A
   config toggle the notifier reads. Before the review lane looks first, "opened" is the only
   human moment; once the review lane handles opened→reviewed, ping only when it is actually the
   owner's turn, so the channel does not fire twice for one PR.

6. **Where it lives — beside the Stage E poller, in the same role account.** The ADR designs it
   as the poller's sibling: same env-file rule, same state directory, same one-tick shape, same
   credential class. No new account, no new credential, and it moves with the poller when the
   dispatcher is replaced (Phase 2).

### The marks the notifier greps (the producer ⇄ consumer contract)

The notifier keys on marks the pipeline *already writes*, so the two never share a second
shape. The idea gate's executor writes these on the tracker today (invisible HTML comments,
`<!-- pipeline-escalation: <label> -->`), and the stopped-session escalation and the bounce
lane write the rest:

| Mark | Written by | The human moment | Apply `agent:blocked`? |
|---|---|---|---|
| `epic-awaiting-approval` | plan executor (idea gate) | a plan is filed; approve the epic | no — it is an approval, not a block |
| `planning-needs-input` | plan executor (idea gate) | the planner asked a question, filed no plan | yes |
| `planning-no-output` | plan executor (idea gate) | a planning run produced nothing | **yes** — this is the §13 silent-failure case; without the label a run that vanished is invisible on the board, so it needs the forced human look a block gives |
| `planning-rejected` | plan executor (idea gate) | a plan was refused; re-plan | no — unlike no-output, a rejection is a loud, self-explaining comment naming what failed; the owner already has their cue, so no `agent:blocked` is needed to make it visible |
| `agent:blocked` (marker) | a stopped working session (`/work` step 5) | a session is blocked on a decision | yes (this is the case §6 already names) |
| `agent:needs-human` | bounce driver | the bounce budget is spent | yes |
| `agent:needs-human` | planner job (idea gate), on the cancelled planning ticket | a planning ticket's routing could not be confirmed; all planning is stopped until the probe is signed again (KIT-184) | yes — the label lands on that dead planning ticket, never on the idea |
| review "NOT reviewed" | review publisher (§14) | a review could not run | no — it is a CI-visible verdict, not a person's decision |

New producer marks are added to this table in the same PR that ships them, so a mark the
notifier cannot page on never exists silently.

> **Implementation note, 2026-09-13.** The shipped notifier applies **`agent:needs-human`**
> on the `agent:needs-human` mark, not the `agent:blocked` this table's last data column
> says. §6 routes the two labels differently — blocked means a question is waiting, needs-human
> means the work is terminal until a person acts — so answering a needs-human mark with a
> blocked label would file it where someone looks for a question to answer. The four planning
> rows match the table exactly, and §6's invariant is untouched either way: the session
> requests the label, this job applies it. Recorded here rather than changed in the decision,
> which stays the owner's to confirm. The reasoning lives beside `MARKS` in
> `scripts/pipeline_notify_local.py`.

> **Confirmed by the owner, 2026-09-17.** The shipped mapping is the decision. Read the table's
> `agent:needs-human` row as applying **`agent:needs-human`**, for the reason in the note above
> (KIT-115).

## Update 2026-09-17 — the conversational lane, the front door, paging and the relay

Five owner decisions, taken after the dispatcher's source was read at the installed version,
0.2.69. Each amends one piece of this record. The six decisions above otherwise stand.

### 1. The conversational lane runs on the dispatcher's own Slack transport

Decision 3 put a conversational channel in the MVP without saying where its agent runs. The
design note that followed recommended a sandboxed dispatcher entry fed by this notifier, in the
review lane's shape. **The owner chose the dispatcher's built-in Slack transport instead, with
its tool grant trimmed** (KIT-117). His reason: he prompts it directly, and a faster, more
capable agent is worth the residual risk.

What was measured, and what each fact decided:

| Fact about the built-in chat lane | Consequence |
| --- | --- |
| It gets no sandbox. Only the ticket path passes sandbox settings | No egress allowlist and no write confinement |
| Its tool allowlist is enforced. The permission callback that allows every tool is attached only on the ticket path | Trimming the grant is a real fence |
| Its `disallowedTools` is hard-coded empty | Only the allowlist can fence it |
| Its default grant includes `Monitor`, whose parameter is a shell command, and `Task` | These are the removals |
| After the trim, the dispatcher re-appends every tool server it built for the session: the tracker, its own tools, its docs and Slack | **The trim cannot remove the dispatcher's tool server** |
| That server runs in the dispatcher's unsandboxed process. One tool delivers a message into any running session by id without checking the caller; another reads any file path and uploads it, optionally public | The lane can steer any session and read any file the role account can (KIT-162) |
| Its working directory is a fresh folder, not a repository | The kit's PreToolUse hooks do not apply. Only the role account's user-level settings would |
| The dispatcher rewrites its own config file when the tracker token refreshes | That file cannot be made read-only to the role account, so the lane can rewrite every other session's fence |
| It is inbound only, on a webhook path, verified by Slack's signing-secret HMAC when the secret is set | The front door gains a path (2, below) |
| The access check runs only on tracker webhooks. The Slack path has no user or channel allowlist | Any member of the Slack workspace can start a session |
| Its bot token is read from the dispatcher's environment, which every session inherits, and a Slack tool server is injected into every lane when the token is set | Every coding session holds the chat bot's token (KIT-157) |

**Conditions.** The grant drops `Monitor`, `Task` and `ScheduleWakeup`. The role account's
user-level settings deny reads of secret files through the `Read` tool; they do not stop the
upload tool. The Slack workspace has one member, because workspace membership is the only gate.
There are **two Slack apps**, so the notifier's token never enters the dispatcher's environment
and a leaked chat token cannot pass as a notifier ping. The Slack tool server is fenced off every
coding entry (KIT-157).

**Accepted residuals.** No sandbox on that lane. It can rewrite the dispatcher's config. There is
no identity check beyond Slack workspace membership. It can steer any running session. It can
read any file the role account can read and upload it, optionally public, so a fetched web page
can drive a credential to a public link without a shell. Any coding session can read the chat
bot's token. Steering and file upload were accepted after a recommendation to hold the lane
until a deny rule was measured, which the owner declined. Coding sessions keep the same tool
server, also by decision (KIT-162).

This reverses *Who sends* for this one lane: it is the shape rejected there, a channel
credential in the environment every session inherits. It is accepted with that named. The
notifier is unchanged and stays the only sender of pings, outside every session.

### 2. The front door gains the Slack webhook path

The one inbound path this record avoided is added, for the conversational lane only, and
verified per request against Slack's signing secret. Without the secret the dispatcher falls
back to a bearer check that no client can currently satisfy. That accident is not relied on.
The notifier still needs no inbound path.

Signature verification is switched on by the same setting that makes the dispatcher's web
server listen on **every network interface** instead of this machine only; the two cannot be
separated. Every route then answers the local network directly, past the front door. So the
owner applies a packet-filter rule, persisted across reboots, that refuses the dispatcher's port
on every interface except loopback, and verifies it from a second device. The front door still
connects locally. The application firewall is not used: it does not close a port a signed app
already holds.

**The dispatcher's API key stays unset.** Setting one would close the tool server's open auth
check, but any value also marks the dispatcher as paired with its vendor's hosted service, which
registers a failure-report tool that posts session recaps and quotes to that service from outside
the sandbox, with no opt-out. Accepted instead: a process already on this machine can call the
tool server, given a live session's id.

### 3. Heartbeat-monitor incidents page through the notifier

The monitor posts under the owner's own key, and the tracker does not notify a user of their own
comments, so its page reached nobody. Its incident and recovery comments gain marks the notifier
pages on. They carry no label and are accepted only from the monitor's author. The monitor also
starts watching the notifier's own heartbeat (KIT-156). Still uncovered: the notifier's own death,
which needs an off-box probe (KIT-45).

### 4. The relay is built now, off by default

Its gate is met (the update under *Not verified*). It ships behind a switch that defaults off, so
the notifier stays send-only until the owner turns it on (KIT-118). The reviewer's basis does not
yet include comments that arrived after delegation, so a relayed instruction still lands outside
what review checks. That half is open (KIT-118).

### 5. The needs-human label stands

Recorded under the marks table.

## Update 2026-09-24 — the chat lane is on, and the composer writes (KIT-197, KIT-196)

**The lane was switched on, and kept on.** A deployment turned the conversational lane on on
2026-09-24, in the order the composer prints. Three live-file edits the composer only
described were made with three small scripts instead, tested on fixtures first: the grant,
fences and read denies merged into the dispatcher's config and the role account's user
settings; the four names written into the dispatcher's env file from hidden prompts; and one
path appended to the front door's allowlist line. `verify` then measured every row as
applied, and the door answered `401` on the Slack path, `401` on the tracker path and `404`
on the config-update route.

The live check found one thing the source reading had not (KIT-196). A chat session is
shown every built-in tool, and the grant is enforced at call time, so a tool listing proves
nothing. Calls were tested instead. Read-only commands **ran**, through Bash and through
`Monitor`: the SDK's default permission handling approves them whatever the grant says. A
write, a `touch`, a chained `echo …; touch …` and `Monitor` running `touch` were refused.
A Bash read of a path under a user-level `Read` deny was refused. `log_failure_mode` was
absent, as it is while `CYRUS_API_KEY` is unset. **The owner kept the lane on with a
read-only shell as a named residual.** It adds to the accepted residuals of decision 1: the
lane can read, through a shell as well as the `Read` tool, any file the role account can
read that no deny rule covers.

**The composer now writes (owner decision, 2026-09-24).** It had been print-and-verify only.
The three scripts' behaviour moved into it as subcommands a person runs:

| Subcommand | Piece | What it writes |
| --- | --- | --- |
| `merge [--apply]` | 1, 2, 5 | the grant and fences into the dispatcher's config, in place; the read denies into the role account's user settings |
| `env-names [--remove]` | 3 | the four names into the dispatcher's env file, from two hidden prompts |
| `front-door [--apply] [--remove]` | 7 | one path on the front door's one allowlist line, then the door's own validation |

Each refuses under a model, before its conf is read. Each writes as the role account,
through one program handed to that account's shell, with every value on standard input and
none in an argument. `merge` and `front-door` re-read the file, as they start and again
just before the write, and refuse when it is not the file they planned from: the
dispatcher rewrites its config when it refreshes a tracker token. `env-names` has no separate plan; it reads and replaces the env file in one pass, and
nothing else rewrites that file. Each writes nothing when nothing would change, and
otherwise backs the file up first, under the role account's home, at mode 600; the
user-level read denies now cover that folder and the role account's own env file. A dry run
is the default where there is one. `env-names` refuses without a terminal, and until
`verify`'s fence and port-block rows both measure as applied: the fence before the token,
and the block before the listen, on the port the dispatcher really listens on. It compares
the chat token with every value in the role account's env file, where the notifier keeps
its own, and writes nothing when it cannot read that file. **Restarts and the port block
stay printed commands the owner runs**, as installers never load jobs: a new card restarts
the dispatcher only when its own `/status` answers `idle`, and another turns the lane off in
order. `verify` gains a row that reads the front door's allowlist line. The live-check card
now tests calls, not the listing, and names KIT-196.

The writers are guarded the way `verify` is: they need administrator access, asked for once,
and they refuse in an agent environment. The markers are environment variables a session
could unset, so that refusal is tamper-evident, not tamper-proof, as for every other command
here.

**"One member" becomes "fully trusted members" (owner decision, 2026-09-24).** Decision 1
kept the Slack workspace to one member, because workspace membership is the lane's only
gate. The owner replaced that with a rule that admits more people on stated terms. Every
member of the workspace must be someone the owner would trust with the role account itself.
Nothing narrows what a member can do: the tracker's `allowedUsers` list does not apply to
Slack, so every member can read every file and token the role account can read, steer any
running session, write to the tracker, and run read-only shell commands. Members are full
members only, never guests or shared-channel users, and every one signs in with two-factor,
because each member's Slack account becomes part of the boundary. The lane is turned off
before anyone who does not meet that joins. The composer's opening warning, its order, the
app-creation card and its "never" list now say this rule, and a selftest keeps the old one
from returning.
