# One tech lead in one DM: the bridge's app carries every message, and a model with no tools writes it

- **Status:** Accepted (owner, 2026-10-30, in chat; KIT-275). Built in parts: KIT-294 (the
  writer and this record), KIT-295 (the bridge in the DM), KIT-296 (the notifier into the DM),
  KIT-297 (the writer switched on, and the owner's switch-on script), KIT-298 (a DM question
  answered by the dispatcher's tech lead).
- **Date:** 2026-10-30
- **Amends:** `2026-10-10-slack-bridge-acts-for-the-owner.md` (where the bridge listens, and the
  never-mention rule for one path) and `2026-09-06-human-action-notifier-and-reply-relay.md`
  (decision 1's dedicated channel; the notifier's own app). Decision 2 of the notifier record,
  no question text in a page, stands.

## Context

After the first full plan ran live (2026-10-29/30), the owner asked for three things:

1. **No more pages from two bots.** The notifier and the bridge each have their own Slack app and
   post as two different names, in two private channels. He reads them as "automated robotic
   updates".
2. **Every message written by an AI when it is sent, "not a script at all"**, so it reads like a
   colleague. He chose his own Claude subscription to pay for it, not an API key.
3. **One chat.** A reply in a thread notifies only people who follow that thread. The bridge
   answers in threads under its own posts, which he never joined, so he missed them. Slack
   notifies every message in a direct message by default.

Three facts limit the answer:

- The dispatcher's chat bot cannot take a direct message and cannot start a conversation by
  itself. Its transport drops top-level messages, its app has no `im:*` scope, and it has no
  scheduler (KIT-275 design §6). So "the tech lead" in the DM has to be the bridge's app.
- The bridge is the only path from Slack to an action as the owner, and a yes binds only to the
  bridge's own recorded question. That must not weaken.
- A model that reads untrusted text and holds tools can be steered. The owner has rejected
  owner-account sessions with tools outside the sandbox.

## Decision

**1. One DM, with the bridge's app.** The bridge reads and posts in each listed member's DM
(`DM_USER_IDS`), at the top level, so every message notifies. Commands and yes/no work there
without threads. A bare yes binds only when exactly one question is open in that DM, and every
check the channel had still applies: the sender checked with Slack, TTL, fingerprint, promised
settings, the foreign-post check and the action limit. The chat channel keeps working.

**2. The notifier posts into the same DM through the bridge's app.** In DM mode its token env
names the bridge's token. Both jobs already run as the same role account from the same env file,
so no account gains a secret it could not already read. A notifier post binds nothing: only the
bridge's recorded question takes a yes. Because its posts appear as the bridge's own user, the
notifier records each one in a ledger (`notifier-dm-posts.json`). The bridge reads the ledger, so
a page between its question and a yes is not taken for a forged bridge post. A corrupt ledger
stops both jobs rather than being read as empty.

**3. A writer with no tools turns each message's facts into plain words** (`pipeline_voice.py`).
- **Its input is the facts the job already holds:** ids, cleaned titles, counts, states, clock
  times and links. It never gets a ticket body or comment, so the notifier's decision 2 holds by
  construction.
- **It runs the unmodified `claude` CLI headless**, on the owner's subscription token from
  `claude setup-token`:
  - `--tools ""`, no MCP servers, no settings and no session file;
  - one turn and a cost cap;
  - in a fresh empty folder that is also its `HOME`;
  - an environment holding only that token, never the Slack or tracker secrets.
- **Its output is checked** for:
  - length;
  - every required id kept, as a whole word;
  - no ticket id that the facts do not hold;
  - no link it was not given (a bare domain with no scheme is not caught yet);
  - no Slack mention or broadcast, and no code fence;
  - no question and no "reply yes" of its own, since only the fixed sentence asks;
  - no credential shape.
- **Any failure sends the fixed text instead**, never an error message. Failures include a
  missing binary or token, a timeout, a non-zero exit, `is_error` and a failed check. The bridge
  says once a day when that happened.
- **The sentence a yes agrees to is never written by the model.** It is appended verbatim after
  the model's words: what changes, how long the question stays open, and that merging stays his.
  Tracker comments and an answer relayed to a session stay verbatim too.

**4. A question the bridge cannot answer goes to the dispatcher's tech lead behind the scenes.**
- The bridge posts the member's own words, mention first, as a new top-level message in the chat
  channel, which becomes a back room he never opens. It relays the chat bot's reply to the DM.
- This lifts the never-mention rule for that one path only. A forward is never posted into a
  thread that holds an open question, and the bridge never forwards a bot's message.
- "Text cannot start paid work" still holds: a forward is the verified member's own question,
  which is what an @mention in the channel was before.
- Built only after one live test shows Slack delivers `app_mention` for another app's post.

## Consequences

- He reads one name in one place, and every message notifies.
- The two channels can be left or muted. The notifier's own app can be retired.
- Each message costs one short model call on his subscription. That is cents a day at today's
  volume (estimate), and it counts toward his plan's limits.
- Messages leave the Mac for the model provider as well as Slack. Their content is the same facts
  the fixed text already carried.
- **Accepted residual: a steered title can make the model's words odd or wrong.** The checks keep
  ids, links and mentions honest, and the fixed sentence keeps the terms of a yes honest, but a
  persuasive wrong summary is possible. The owner reads the fixed terms before he says yes.
- **Accepted cost of the checks:** a message whose facts quote a title that is itself a question
  goes out as the fixed text, since the writer may ask nothing.
- **Accepted residual: the subscription token sits in the role account's env file**, beside the
  tracker key and the Slack tokens, and is readable by anything that can read that account's
  files (KIT-162). It can run inference only.
- **A forwarded question costs a full tech-lead turn**, as an @mention does today. A dispatcher
  restart forgets the thread, and the next forward starts a fresh one.

## Rejected

- **A notifier DM from its own app.** That is two names again, which is the thing he asked to
  stop.
- **An outbox the bridge posts for the notifier.** It would keep the tokens apart, but a dead
  bridge would then silence the page that says the bridge is dead.
- **An API key for the writer.** It is a new paid secret, and the owner chose his subscription.
- **Letting the model write the whole question, terms included.** A yes would bind to words a
  steered model chose.
- **Waiting for DM support in a dispatcher fork (KIT-278).** That remains the end state, one agent
  with tools in the DM. Until then the bridge carries the DM.

## Not proven

- That `claude -p` authenticates for the role account from `CLAUDE_CODE_OAUTH_TOKEN` under
  launchd, or that it reads its prompt from stdin when no prompt argument is given. The
  writer's `probe`, run by the installer's `verify`, settles both.
- That Slack delivers `app_mention` when the bridge's bot mentions the chat bot (KIT-298 tests it
  first).
- That a reinstall after adding `im:history` and `im:write` keeps the bridge's bot token. If it
  changes, the token step is run again.
