#!/usr/bin/env python3
"""The Slack bridge — the chat side's only way to act as the owner (KIT-220, KIT-221).

    python3 scripts/pipeline_bridge.py run --config <path>            # one pass
    python3 scripts/pipeline_bridge.py run --config <path> --dry-run  # posts and saves nothing
    python3 scripts/pipeline_bridge.py lanes --config <path>  # HANDOFF_STATES on the board
    python3 scripts/pipeline_bridge.py --example-config
    python3 scripts/pipeline_bridge.py --selftest

WHY IT EXISTS. The planner and the dispatcher accept only the owner's own moves, on
purpose, and the chat bot writes as the dispatcher's app user. So nothing the chat bot does
can start planning or work. This job is the one bridge across: it reads the Slack channel,
checks WITH SLACK ITSELF who sent each message, and acts in the tracker with the owner's
key — only after a verified member answers "yes" to a question the bridge asked itself.

TWO ACTIONS, AND ONLY WITH `act` ON. With `act` off (the default) it reads, verifies,
asks and records, and a verified "yes" changes nothing. With `act` on:

  PLAN (KIT-222). A verified "yes" to a `plan` question moves that idea into its team's
  Plan it state, AS THE OWNER, so the planner job starts planning it. Before it asks, and
  again before it moves, the ticket is re-read and refused if it is live work, by the
  planner's own rules (`pipeline_plan_poller.live_work_reasons`, imported), or already in
  Plan it. The question records the ticket as it showed it (its title, text, team, labels
  and state); a yes after any of them changed moves nothing and closes the question.

  REPLAN (KIT-244). A verified "yes" to a `replan` question moves an idea that sits in Plan
  it out to its team's first backlog state and straight back in, AS THE OWNER, so the
  planner sees a new move and starts a new planning run. It is asked only for a fresh idea
  in Plan it whose planning runs have all ended, none of which filed a plan that still
  stands (an epic carrying that run's receipt, not canceled), and whose newest move into
  Plan it either carries a planning run or is at least REPLAN_SETTLE_SECONDS old. A run and
  a plan count only when the owner's key filed them, as the planner and the executor do.
  Naming one of the planner's planning tickets asks about its idea, and the question names
  both. Every check is made again before the move, and so is what the question showed: the
  newest run and the newest move. The re-plan is recorded as under way before the first
  move, and the comment naming who said yes is written as soon as that move lands, so a
  pass cut midway is finished by the next one. Afterwards the idea's history is read the
  planner's own way. A move back the history does not show, a move back that failed, or
  one whose landing can't be read is said, and that pass exits 1.

  APPROVE (KIT-223). A verified "yes" to an `approve` question moves an epic the idea gate
  filed (`provenance:agent`, every child `provenance:epic`) to its team's `ready_state`,
  and records it as approved. From then on, every pass hands its children to the agent
  user, AS THE OWNER, in dependency order: a child starts only when every ticket that
  blocks it is Done, and at most `epic_wip` of one epic's children are started and
  unfinished at once (a child marked Done while its session still runs counts until the
  session ends). It never hands over the epic itself, a child already delegated or
  with a session, or a child it started once before; an epic someone else handed to the
  agent stops it, because its children would stack on the epic's branch, and so does an
  epic or child carrying the dispatcher's `orchestrator` or `graphite` label. It hands over
  only what the member saw: the question records each child's title, text, labels,
  project and blockers, a yes after any of them changed approves nothing, and a child
  added or changed after the yes (a session holds the tracker's write tools) is held and
  said once. Each start is said once in the thread, and so is a stall, a pause, a child
  outside the plan at work, and the end. So is a hand-over still not started two hours
  on, as its agent sessions show it (KIT-284): nothing while one runs; in one of
  `handoff_states`, its pull request waiting for your merge, or on you when its review
  ran out of rounds; its newest session ended early, or the agent never picked it up,
  two hours on from that session when you handed it over again; no session at all; or,
  when the session list was too long to read whole, that it could not tell. One epic the
  tracker will not read stops nothing else, and the pass still exits 1.

  FINISH (KIT-299). Once every piece of an approved plan is Done, none canceled, no
  session on one still open, and every pull request the GitHub integration linked to
  a piece merged (one still open, or closed with none merged in its place, holds it, as
  does a piece with more links than one read takes, said once), the bridge moves the epic
  to its team's `done_state`, AS THE OWNER, writes one comment on it naming who approved it
  and each piece's pull requests, and posts one wrap-up where the plan was approved: what
  shipped, a line for a piece whose merge the tracker does not confirm, that has no review,
  or whose review was partial, and the notes its newest review left, each review being
  the owner's own `**Pipeline review**` comment with a `pipeline-telemetry/1` block. With
  notes left it asks ONE question; a verified yes files them as one ticket in the epic's
  team, with a title and a description and nothing else, so it starts no work. Then it
  stops following the plan. A plan someone else closed is said and let go, never moved;
  one this bridge was closing when a pass stopped is finished without moving again.

  Every question also records the settings its text states; a yes after they changed acts
  on nothing and closes the question. A follow-up already being filed when its reply was
  lost is said as filed (or as not confirmed) instead, never as acting on nothing.

  ANSWER (KIT-224). The notifier records each `agent:blocked` ping it sends
  (`bridge_pings`). A verified member's reply under one becomes one comment in the session
  thread recorded with that ping, as the owner, which resumes the session. It replaces the
  notifier's reply relay and keeps its rules, imported from `pipeline_notify_local`: the
  recorded thread only and checked again, the reply decoded and cleaned of every angle
  bracket, a size and an hourly limit, no credential shape, and at most once. The ticket
  must be on one of `team_keys`, and the member's name goes through the same cleaner as
  the answer. The pings file is read as each reply is handled, so a ping recorded during
  a pass is found. A member's reply under a post of `notifier_bot_user_id` that the file
  does not hold (a ping sent before `bridge_pings` was on, or one the notifier failed to
  record) is refused, and said in its thread; without that id the bridge cannot tell the
  notifier's posts, and every pass says unrecorded pings are not checked.

Its own tracker writes are exactly four, pinned by the selftest: one `issueUpdate` setting
`stateId` only, one `issueUpdate` setting `delegateId` only, one `commentCreate` naming
who said yes, and one `issueCreate` carrying a team, a title and a description only (a
finished plan's follow-up, KIT-299). A move or a hand-over cannot be taken back, so a
comment that fails is OWED:
the thread says so, and every pass writes it again and exits 1 until it lands. The answer
is the bounce driver's thread reply, borrowed from one place. Merging stays the owner's.

WHO ASKED COMES FROM SLACK, NEVER FROM TEXT

  A message's sender is the `user` field Slack's own history and replies calls return. It
  is then looked up with `users.info`, and refused unless the account is a human, not
  deleted, not a guest (restricted or ultra-restricted), not an app user, and named by Slack
  as in the same workspace as the bridge's own token: an account Slack names no workspace
  for is refused, not passed. Bot posts, edits, file shares and every message with a
  subtype (joins, broadcasts) are refused too. An optional `allowed_slack_users` list
  narrows it further; empty means any full member, which is the owner's decision of
  2026-10-10 (KIT-117): workspace membership is the access rule.

THE BRIDGE ASKS, A MEMBER ANSWERS

  A request is one strict line, `plan <ticket id>`, `replan <ticket id>` or
  `approve <epic id>`, from anyone in the channel, the chat bot included: asking is
  harmless. The bridge reads the ticket from
  the tracker with the owner's key and posts its OWN question in that thread, carrying only
  facts it read itself. A question is answered by a reply `yes` or `no` in its thread,
  after it, from a verified member. A `yes` binds to the question recorded in this job's
  state, never to a ticket named in someone's text: with two questions open in one thread,
  a bare `yes` is refused and `yes <ticket id>` is asked for. So is a bare `yes` when
  anything other than the bridge or a verified member posted in the thread after the
  question (a bot, an app, Slackbot, a guest), because a look-alike question could have
  changed what the person thought they were answering. A question edited after it was
  posted, or gone from its thread, is refused and closed. A question expires
  `question_ttl_hours` after it was asked, judged by when the answer was SENT, so a yes
  sent while the Mac slept still counts. A question's status changes only after the
  bridge's reply saying so is posted. A tricked chat bot can make the bridge ask; it cannot
  make it act.

  IN A MEMBER'S DM (KIT-295). With `dm_user_ids` set, each pass also reads each listed
  member's DM with the bridge app, opened once with `conversations.open` and kept in the
  state. The first pass that reads a DM is a baseline for it: what was already there is
  passed over. Everything the bridge posts there is a message of its own, never a thread
  reply, so every message notifies. A request there is one line of its own, and so is its
  question. A `yes` or `no` of its own answers the one open question in that DM asked
  before it; with two open, a bare `yes` is refused and `yes <ticket id>` is asked for. A
  `yes` in the thread under a question answers that one. Every other check is the
  channel's: the sender, an edited or deleted question, anything posted between the
  question and a bare yes that the bridge cannot vouch for, the time limit, the settings
  stated and the action limit. The notifier posts its pages into that DM as this app, and
  records each one in its ledger (`notifier_posts_file`): a page it recorded for that DM is
  not a post the bridge cannot vouch for, unless it asks the member something (a ping, a
  planner's question, a plan waiting for approval), since a bare yes may be meant for it.
  A plan's page gets the approve question there on the next pass, asked by the bridge on
  its own, once, naming how many open questions the planner left with the plan, read from
  the ledger (KIT-274).
  A ledger that cannot be read stops the pass. A reply under a recorded page that is not a
  ping waiting on an answer is refused, and said, and so is one under a post of this app
  that neither job recorded.
  Anything else a member writes there gets one line on what the bridge can do, at most
  once an hour; a bot's post gets nothing. A member's DM that cannot be opened stops no
  channel and no other DM: each one is named, and the pass exits 1 (KIT-297 review).

  WRITTEN WHEN IT IS SENT (KIT-297). With `writer` on, every message goes through
  `pipeline_voice.write`, a model with no tools, given that message's plain text and the
  facts already in hand (ids, titles, counts), never a ticket's text or a comment. The
  sentence saying what to type, where to answer or what a yes does is the BIND: it is
  never given to the model to write, and follows its words verbatim. A question's bold
  head is not given to it either, since the model may ask nothing. What a yes binds to is
  unchanged: the recorded question, its fingerprint and its stated settings. Whenever the
  writer cannot write a message, the plain one goes out, and once a day that is said after
  it. The writer gets only the time the pass has left less the time to post plain, so a
  slow one can never cost a message to the pass's deadline (KIT-297 review). A dry run
  never calls it, and a tracker comment or an answer passed on to a session is never
  written by it. With `writer` off, the default, every message is as before.

  ASKING THE TECH LEAD FROM THE DM (KIT-298). With `ask_tech_lead` on, that anything else
  goes to the dispatcher's chat bot instead, from a person whose account passes the sender
  checks, and nothing else: cleaned as an answer to a session is (every angle bracket gone,
  a size limit, no credential shape), at most `forward_max_per_hour` an hour. The bridge
  posts it in `back_room_channel_id`, with a mention of `chat_bot_user_id` FIRST, the one
  mention of the chat bot it ever posts, and names the member in words. Within
  `forward_follow_minutes` of the last question or answer, it goes into that same thread,
  mentioned again, since the dispatcher drops a bot's plain reply; never into a thread that
  holds an open question. Each pass reads that thread and posts each of the chat bot's
  replies in the DM as a message of its own, with every mention, broadcast and channel
  link made plain text. A reply that is exactly `plan <id>` or `approve <id>` is a request
  made in the DM instead: the bridge asks its own question there, and the channel's request
  path never sees it. A relayed reply is a model's words, so it is never vouched for
  between a question and a bare yes. No reply after `forward_answer_minutes` is said once;
  so is a turn the chat bot ended without one. A question that opens a NEW thread carries
  the DM's recent conversation under it, marked as context and not instructions: the newest
  `forward_history_messages` messages before it, by count and never by time, read with a
  call of its own, oldest first, each line cleaned as the question is, the block cut to
  `forward_history_chars` from the oldest end. A follow-up carries none: the thread holds it.

  THE BRIDGE KNOWS ITS OWN BOT. `bot_user_id` in the config is the bridge app's bot user,
  and a token that answers to anyone else is refused before anything is read: a pasted
  chat-bot token would otherwise make the chat bot's posts look like the bridge's.

CATCHES UP AFTER SLEEP

  Slack drops an event once the receiver has been unreachable for about six minutes, and
  nothing redelivers it. So the bridge does not listen: every pass it reads the channel's
  history over `lookback_days`, the threads that changed, and the thread of every open
  question, and handles each message once (keyed on channel and timestamp). A "yes" sent
  while the Mac slept is handled when it wakes, if its question has not expired. A thread is
  marked read only after every message taken from it is handled, so a pass that fails or
  runs out of time reads it again next time.

  A thread is found through its first message. So a new reply in a thread that started
  before the window is not read, unless that thread holds an open question: ask in a new
  message, or a newer thread, instead.

  The FIRST pass (no state file) records everything already in the window as seen and
  answers none of it. A state file that cannot be read is refused, never treated as a first
  pass: the record of open questions is what binds a "yes" to a ticket.

SAYS WHICH NOTHING IT DID (contract §13)

  Every refusal it can explain is said once in the thread. State is written through after
  every message, so a later failure cannot lose an answer already said. A Slack or tracker
  call that fails stops the pass with exit 1 and leaves the message for the next pass; a
  message that fails three passes running is given up on, said once, and passed over, so
  one bad message cannot stall the bridge. One pass runs at a time (a lock in the state
  dir). Messages older than the window are never handled, and the window must be at least
  as long as a question lives. A real pass writes a heartbeat; a dry run writes none, so a
  check that runs one never hides the job's own record.

  Every request that reads the tracker counts against `max_questions_per_hour`, whether
  the ticket is found or not: the key is the owner's, shared with Stage E. Past the limit a
  request is refused without a read, and that is said at most once an hour per channel.
  The reads an answer makes before it acts are not counted there: they come only after
  `max_actions_per_hour` allows the act, for one answer per open question, so
  `max_open_questions` bounds them too. The session listing is read at most once a pass.

WATCHES THE NOTIFIER (KIT-285)

  The notifier is the only job that pages, so a notifier that stopped, or that runs and
  cannot post, would page nobody about itself. With `notifier_plist` set, each pass reads
  how often launchd runs the notifier, from that plist, and the notifier's heartbeat beside
  `pings_file`. It is QUIET once nothing is newer than three of its intervals plus two
  minutes: two runs missed, and a third interval for the longest one run may take. A run
  that did not exit 0 is FAILING, said only when a second one fails too, since the notifier
  sends again what it could not send. A plist or heartbeat that cannot be read, or one
  stamped in the future, is said as that, never as healthy (§13). A --dry-run beat says
  nothing about the scheduled runs, so it changes nothing: the watch keeps the newest
  scheduled run it has seen, and counts silence from that, through any rehearsal. A stamp
  of its own later than this clock means the Mac's clock moved back: it starts counting
  again from this pass, so the step never holds it silent.

  A problem is said once, as this app, in a message of its own in the first of
  `channel_ids`, and so is a different one after it: until a clean run, each of the three
  is said at most once, however often the notifier changes. Its end is said once more,
  as the end of the problem it said last, after a clean run newer than the heartbeat it had
  when it spoke. What was said is in a record of its own, bridge-notifier-watch.json beside
  the state, written before the post, so no later pass and no restart says it again; a
  post that fails is put back, and the next pass says it. The record is not the state, so
  a first pass that stops early is still a first pass, and answers nothing already in the
  window. A gap in this job's own passes longer than two of its runs and the longest one
  pass may take (`run_interval_seconds`, `run_timeout_seconds`) is the Mac asleep, or the
  job stopped: the watch counts afresh from that pass, and a heartbeat older than that
  waits for the notifier's next run. The watch runs before the tracker is read, so a pass
  that stops there does not silence it. The message carries times and one command, never
  ticket text. With `notifier_plist` unset there is no notifier to watch, and every pass
  says the watch is OFF.

WHAT IT NEVER DOES

  It writes to the tracker only the four writes above, and only with `act` on. It never
  posts outside its configured channels and DMs, never posts a message's own text back
  (with `ask_tech_lead` on, a DM question and the chat bot's answer, cleaned, are the one
  exception), and never prints a credential: both are read from env vars the config NAMES
  (the job's wrapper sources the role account's env file), and every line it writes (the
  log, the state file, a Slack post) is redacted against them, and against the writer's
  token while the writer is on. The state file is written mode 600.

WHERE ITS CODE IS (KIT-312)

  This file holds the pass's core (reading the channels and DMs, posting through the
  writer, the state it keeps), the job's entry points, the selftest's kit, and the cases
  of the pass itself. The rest is in modules beside it, by concern, each moved unchanged
  (KIT-388, KIT-407 and part 3), each with its own --selftest, and each with cases of
  its own:

    pipeline_bridge_transport.py   the bridge's error, the one HTTP call, Slack's client
    pipeline_bridge_config.py      the config, the job's files, the names every part shares
    pipeline_bridge_requests.py    what each message is, a request, the bridge's question
    pipeline_bridge_answers.py     a yes or a no to that question, and the plan action
    pipeline_bridge_replan.py      a re-plan, from the request to the end
    pipeline_bridge_plans.py       approve, the children's start, and a finished plan
    pipeline_bridge_pings.py       a dev's ping answered
    pipeline_bridge_tech_lead.py   a DM question passed to the tech lead, and its answer
    pipeline_bridge_watch.py       the notifier watch
    pipeline_bridge_direct.py      your own clear ask, acted on after a stop window
    pipeline_bridge_tracker.py     the tracker client: its reads and its four writes
"""
import argparse
import json
import os
import re
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The notifier's own pieces, imported, never copied: its chat-text cleaner, its title
# scrub for the chat provider, its secret scan and redaction, its atomic JSON write and its
# worktree test. One copy of each means one place to fix it.
import pipeline_notify_local as notify  # noqa: E402
# The planner job's own rules for what is live work, and its own team query: the bridge
# refuses exactly what the planner would, so it never moves a ticket the planner refuses.
import pipeline_plan_poller as poller  # noqa: E402
# The tech lead's voice (KIT-294): with `writer` on, each message is written by a model
# with no tools when it is sent, and the plain text goes out whenever it is not.
import pipeline_voice as voice  # noqa: E402
# The daily stand-up (KIT-349): it decides, words and records the rundown, and the pass
# posts it as its own.
import pipeline_standup as standup  # noqa: E402
# The bridge's error, its one HTTP call and its Slack client, in a module of their own
# (KIT-312, part 1: KIT-388). The tracker client calls `_http_json` through its own module's
# globals (pipeline_bridge_tracker), which is where its tests stand in for it.
from pipeline_bridge_transport import BridgeError, SlackClient, _http_json  # noqa: E402,F401
# The rest of the bridge's code, by concern (KIT-312, part 2: KIT-407). Every function,
# class and constant the bridge defined is still one of its names, the very same
# object: its selftest and its installer read them here. The modules only that code
# reads (hashlib, datetime, telemetry_scrape) are imported where it is now.
from pipeline_bridge_config import (CHANNEL_RE, CONFIG_KEYS,  # noqa: E402,F401
                                    CONFIG_SCHEMA, CONFIRMED, DECLINED, DEFAULT_DONE_STATE,
                                    DEFAULT_HANDOFF_STATES, DEFAULT_MAX_ACTIONS_PER_HOUR,
                                    DEFAULT_MAX_QUESTIONS_PER_HOUR,
                                    DEFAULT_RELAY_MAX_PER_HOUR, DEFAULT_RUN_INTERVAL,
                                    DEFAULT_RUN_TIMEOUT, DEFAULT_STATE_DIR,
                                    DEFAULT_TECH_LEAD_LINK_HOSTS, DM_RE, DONE,
                                    Deadline, ENV_NAME_RE, EXAMPLE_CONFIG, EXIT_DECLINED,
                                    EXIT_ERROR, EXIT_OK, EXIT_TIMEOUT, EXIT_USAGE, EXPIRED,
                                    FORWARD_HISTORY_CHARS_MAX, FORWARD_HISTORY_MESSAGES_MAX,
                                    HEARTBEAT_SCHEMA, LINK_HOST_RE, NOTIFIER_DM_POSTS_NAME,
                                    NOTIFIER_DM_POSTS_SCHEMA, OPEN, REFUSED, SLACK_USER_RE,
                                    STATE_SCHEMA, TEAM_KEY_RE, TICKET_RE,
                                    WRITER_MIN_SECONDS, WRITER_POST_SECONDS, _MONTHS,
                                    _TICKET_WORD_RE, _WEEKDAYS, clock, credential,
                                    day_clock, epoch, forward_config_errors, heartbeat_path,
                                    iso, key_of, label_names, live_secrets, load_config,
                                    load_dm_posts, load_pings, load_state, lock_path,
                                    new_state, run_idea, save_state, state_path,
                                    ticket_number, ts_float, validate_config,
                                    write_heartbeat)
from pipeline_bridge_requests import (DM_HELP, EDITED_WHY, NO_RE,  # noqa: E402,F401
                                      REQUEST_RE, RequestsMixin, YES_RE,
                                      _LEADING_MENTION_RE, account_refusal, command_text,
                                      message_refusal, newest_run, question_parts,
                                      question_text, ticket_fingerprint)
from pipeline_bridge_answers import AnswersMixin, DM_ASKING_MARKS  # noqa: E402,F401
from pipeline_bridge_replan import (REPLAN_COMMENT_OWED,  # noqa: E402,F401
                                    REPLAN_RUN_ENDED, REPLAN_SETTLE_SECONDS, ReplanMixin,
                                    history_epoch, replan_yes)
from pipeline_bridge_plans import (FINISHED, FINISH_RECHECK_SECONDS,  # noqa: E402,F401
                                   FOLLOWUP, FOLLOWUP_INTO, GATE_CHILD_LABEL,
                                   GATE_EPIC_LABEL, GITHUB_ATTACHMENT_SOURCES,
                                   JUST_MADE_SECONDS, MAX_CHILDREN, MODE_LABELS,
                                   NEEDS_HUMAN_LABEL, PARTIAL_RE, PIECE_LINKS, PR_URL_RE,
                                   PlansMixin, REVIEW_HEAD, SESSION_RAN, SESSION_RUNNING,
                                   STUCK_START_SECONDS, WRAP_LINES, _numbers, _partial,
                                   _plain, _relayed, _source_key, _where, approve_parts,
                                   approve_text, blocker_done, blockers_of, changes_since,
                                   children_of, closing_comment, epic_problems, fingerprint,
                                   fingerprints, followup_description, followup_parts,
                                   followup_text, followup_title, in_order,
                                   notes_fingerprint, notes_of, open_blockers, piece_hold,
                                   piece_lines, piece_merge, piece_prs, piece_review,
                                   wrap_up_parts, wrap_up_text)
from pipeline_bridge_pings import (PINGS_OFF_BIND, PINGS_OFF_SAYS,  # noqa: E402,F401
                                   PingsMixin, UNRECORDED_PING, UNRECORDED_PING_BIND,
                                   UNRECORDED_PING_SAYS)
from pipeline_bridge_tech_lead import (FORWARD_ASKING,  # noqa: E402,F401
                                       FORWARD_HISTORY_HEADER, FORWARD_HISTORY_LINE_MAX,
                                       FORWARD_LOST, FORWARD_LOST_BIND, FORWARD_LOST_SAYS,
                                       FORWARD_NOTHING, FORWARD_NO_ANSWER,
                                       FORWARD_NO_ANSWER_BIND, FORWARD_NO_ANSWER_SAYS,
                                       FORWARD_POST_MAX, TECH_LEAD_DONE_REACTION,
                                       TECH_LEAD_MAX_CHARS, TECH_LEAD_NOT_ANSWERS,
                                       TechLeadMixin, _SLACK_CONTROL_RE, _plain_control,
                                       tech_lead_words)
from pipeline_bridge_tracker import (FIND_MAX_PAGES, M_COMMENT,  # noqa: E402,F401
                                     M_DELEGATE, M_FOLLOWUP, M_MOVE, Q_EPIC,
                                     Q_FIND_BY_LINE, Q_ISSUE, Q_PIECE, Q_VIEWER,
                                     TRACKER_API, TrackerClient)
from pipeline_bridge_watch import (NOTIFIER_BACK, NOTIFIER_CHECK,  # noqa: E402,F401
                                   NOTIFIER_PROBLEMS, NOTIFIER_QUIET_INTERVALS,
                                   NOTIFIER_QUIET_SLACK, NOTIFIER_READABLE, NOTIFIER_SAYS,
                                   NOTIFIER_SCRIPT, WATCH_SCHEMA, WatchMixin,
                                   judge_notifier, load_watch, notifier_heartbeat_path,
                                   notifier_interval, quiet_after, read_notifier,
                                   save_watch, sleep_gap, slowest_run_interval, watch_path)
from pipeline_bridge_direct import (DIRECT_KEEP_SECONDS, DIRECT_VERBS,  # noqa: E402,F401
                                    DirectMixin, PENDING, STOPPED, just_after)


# KIT-297: said after everything else, at most once a day, when the writer is on and a
# message went out in its plain words.
WRITER_NOTE = "(I couldn't reach my writer, so this one's the plain version.)"
WRITER_NOTE_SECONDS = 24 * 3600
# The bind past the hourly action limit (KIT-342), pinned word for word with the others.
ACTION_LIMIT_BIND = "Say yes again later."
# A Slack mention. The writer may mention nobody, so it is given the member's name instead.
_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")







# ══════════════════════════════════════════════════════════════════════════════════════
# One pass
# ══════════════════════════════════════════════════════════════════════════════════════

class Pass(RequestsMixin, AnswersMixin, ReplanMixin, PlansMixin, PingsMixin, TechLeadMixin,
           WatchMixin, DirectMixin):
    """One pass. Its core is here; each concern's methods are its module's, unchanged, and
    no method is defined twice (KIT-312, part 2: KIT-407)."""

    def __init__(self, cfg, slack, tracker, state, first, dry_run, now, out, writer=None,
                 deadline=None):
        self.cfg, self.slack, self.tracker, self.state = cfg, slack, tracker, state
        self.first, self.dry, self.now, self.out = first, dry_run, now, out
        self.writer = writer or voice.write    # KIT-297; the selftest hands in a fake
        # When the pass's wall clock runs out, on `clock` (KIT-297 review); None: no alarm.
        self.deadline, self.clock = deadline, time.monotonic
        self.me = None
        self.users = {}
        self.threads = {}                  # (channel, root ts) -> messages read this pass
        self.tops = {}                     # channel -> its own top-level messages, this pass
        self.unrecorded = {}               # thread key -> latest_reply, once all handled
        self.dm_channels = set()           # the members' DMs this pass reads (KIT-295)
        self.dm_posts = {}                 # the notifier's DM pages, "<channel>:<ts>"
        self.fresh_dms = {}                # DM channel -> member, opened for the first time
        self.dm_errors = []                # each member whose DM could not be opened
        self.secrets = live_secrets(cfg)
        self.release_errors = []
        self.replan_errors = []            # re-plans left half done or unconfirmed (KIT-244)
        self.expire_errors = []            # reminders and expiries not said (KIT-340)
        self.notifier = None               # the watch's line for the summary (KIT-285)
        self.watch_error = None
        self.standup_on = False            # each piece's start goes in the wrap (KIT-349)
        self.standup_error = None          # what the stand-up could not do this pass
        # written, fell_back: messages the writer wrote, and ones that went out plain while
        # it was on (KIT-297).
        self.counts = {"examined": 0, "asked": 0, "confirmed": 0, "declined": 0,
                       "expired": 0, "refused": 0, "baseline": 0, "gave_up": 0, "acted": 0,
                       "relayed": 0, "written": 0, "fell_back": 0}
        self.forward_errors = []           # back-room threads this pass could not read
        self.forwarded_now = set()         # forwards posted this pass: nothing answered yet
        self.forward_post = None           # the one text post() lets mention the chat bot
        self.history_errors = []           # DM histories a new forward could not read

    def clean(self, text):
        return notify.redact(text, self.secrets)

    def say(self, line):
        self.out.write(self.clean(line) + "\n")

    def save(self):
        """Write-through: after every message, so a later failure loses nothing said."""
        if not self.dry:
            save_state(self.cfg, self.state)

    def post(self, channel, thread_ts, text, kind="", bind="", facts=None, join="\n\n",
             plain=None):
        """One message (KIT-297). `kind` names the moment. `bind` is what tells the member
        what to type, where to answer or what a yes does: it is never the model's, and
        `join` is what stands between it and `text` in the plain message, as each one has
        always read. With the writer on, a kind given and no dry run, the writer writes the
        text from `plain` (when the text itself asks something) or `text`, and from
        `facts`, and the bind follows it verbatim. Otherwise, and whenever the writer cannot,
        the plain message goes out, byte for byte as before the writer; with the writer on,
        once a day, the line saying so follows it. The redaction runs on what is posted."""
        if channel in self.dm_channels:
            # In a DM every post is a message of its own (KIT-295): a thread reply notifies
            # only those who follow that thread, and the member reads the DM itself.
            thread_ts = None
        message = (text + join + bind if text else bind) if bind else text
        # The never-mention rule, on the plain message and again on what goes out. A written
        # message that breaks it is not used: the plain one, which kept it, goes instead.
        self.never_mention_chat_bot(message)
        note = False
        if kind and text and self.cfg.get("writer") is True and not self.dry:
            written = self.written(kind, plain or text, bind, facts)
            if written is not None and self.mentions_chat_bot(written):
                self.counts["written"] -= 1
                self.counts["fell_back"] += 1
                self.say("  the writer's %s mentioned the chat bot, so it went out plain" % kind)
                written = None
            if written is not None:
                message = written
            elif self.now - self.state.get("writer_note_at", 0) >= WRITER_NOTE_SECONDS:
                message, note = message + "\n\n" + WRITER_NOTE, True
        self.never_mention_chat_bot(message)
        text = self.clean(message)
        if self.dry:
            self.say("  [dry-run] would post in %s thread %s (%d chars)"
                     % (channel, thread_ts, len(text)))
            return "%.6f" % self.now
        ts = self.slack.post(channel, thread_ts, text)
        self.state["posted"][key_of(channel, ts)] = self.now
        if note:
            # Kept only once it is posted: a post that failed said nothing.
            self.state["writer_note_at"] = self.now
        return ts

    def written(self, kind, text, bind, facts):
        """The writer's message, its words then the bind, or None for the plain one. It is
        given the text redacted, with each mention turned into the member's name (it may
        mention nobody), and must keep every ticket id and link in the text and the bind.
        A writer that raises, or answers with no text, counts as one that fell back: a
        message is never lost to it. Each fallback is counted and logged. The writer gets
        only what writer_seconds allows, so the pass's deadline never cuts it off."""
        plain = self.clean(_MENTION_RE.sub(lambda m: self.name_of(m.group(1)), text))
        bind = self.clean(bind)
        both = plain + "\n" + bind
        keep = (sorted(set(_TICKET_WORD_RE.findall(both)))
                + sorted(voice.fact_links([both])))
        facts = dict(facts or {}, plain=plain)
        # A link only the bind holds is one the model must keep, so it is a fact too.
        extra = sorted(voice.fact_links([bind]) - voice.fact_links([plain]))
        if extra:
            facts["links"] = extra
        cfg, seconds = self.cfg, self.writer_seconds()
        if seconds < cfg["writer_timeout_seconds"]:
            cfg = dict(cfg, writer_timeout_seconds=seconds)
        if seconds < WRITER_MIN_SECONDS:
            by, why = "fallback", "too little of this pass was left to wait for the writer"
        else:
            try:
                res = self.writer(cfg, kind, facts, fallback=plain, keep=keep, bind=bind,
                                  now=self.now)
                out, by, why = res["text"], res["by"], res.get("why") or ""
                if not isinstance(out, str) or not out.strip():
                    raise ValueError("no text")
            except Exception as exc:                               # noqa: BLE001
                by, why = "fallback", "the writer failed (%s)" % type(exc).__name__
        if by == "model":
            self.counts["written"] += 1
            return out
        self.counts["fell_back"] += 1
        self.say("  the writer fell back on %s: %s" % (kind, why))
        return None

    def writer_seconds(self):
        """How long the writer may take on one message: its own timeout, cut to what is
        left of this pass less WRITER_POST_SECONDS, the time to post the message plain if it
        gives up (KIT-297 review). Whole seconds, so a cut one is never longer."""
        limit = self.cfg["writer_timeout_seconds"]
        if self.deadline is None:
            return limit
        return min(limit, int(self.deadline - self.clock() - WRITER_POST_SECONDS))

    def mark(self, channel, msg, what):
        # Redacted BEFORE it is cut short: a cut credential would no longer match.
        self.state["handled"][key_of(channel, msg.get("ts"))] = {
            "at": iso(self.now), "what": self.clean(what)[:160]}
        self.state["attempts"].pop(key_of(channel, msg.get("ts")), None)

    def account(self, uid):
        """Slack's own `users.info` for a sender, read once a pass."""
        if uid not in self.users:
            self.users[uid] = self.slack.user(uid)
        return self.users[uid]

    # -- reading -------------------------------------------------------------------
    def read_thread(self, channel, root):
        msgs = self.slack.replies(channel, root)
        self.threads[(channel, root)] = msgs
        return [r for r in msgs if r.get("ts") != root]

    def gather(self, channel):
        """Every message in the window this pass must look at, oldest first. A message
        older than the window is never handled, wherever it was found."""
        oldest = self.now - self.cfg["lookback_days"] * 86400
        tops = self.slack.history(channel, oldest, self.cfg["max_history_pages"])
        # Kept for a question asked as a message of its own, in a DM: the DM's history is
        # where it lives, and where anything posted after it shows (KIT-295).
        self.tops[channel] = tops
        msgs = list(tops)
        for m in tops:
            root = m.get("ts")
            if not m.get("reply_count"):
                continue
            seen = self.state["threads"].get(key_of(channel, root))
            if m.get("latest_reply") and m.get("latest_reply") == seen:
                continue
            msgs.extend(self.read_thread(channel, root))
            # Recorded as read only once every message gathered here is handled (run): a
            # failure or the deadline leaves it unrecorded, so the next pass reads it again.
            self.unrecorded[key_of(channel, root)] = m.get("latest_reply")
        # The thread of every OPEN question is read whatever the window says: its root
        # may be older than the window, and its answer must still be heard. A question in a
        # DM has no thread: it is a message of its own, read with the DM's history.
        for q in self.state["questions"].values():
            if (q.get("status") == OPEN and q.get("channel") == channel and q.get("thread")
                    and (channel, q.get("thread")) not in self.threads):
                msgs.extend(self.read_thread(channel, q["thread"]))
        unique = {}
        for m in msgs:
            if m.get("ts") and ts_float(m.get("ts")) >= oldest:
                unique[m["ts"]] = m
        return [unique[t] for t in sorted(unique, key=ts_float)]

    def run(self):
        # Read first: a yes in the DM can start a piece before the stand-up's own turn. With
        # no DM member there is no wrap to list the starts, so it counts as off here.
        self.standup_on = standup.is_on(cfg=self.cfg)
        self.me = self.slack.auth_test()
        if not (self.me.get("user_id") and self.me.get("team_id")):
            raise BridgeError("Slack auth.test named no bot user or workspace")
        if self.me["user_id"] != self.cfg["bot_user_id"]:
            raise BridgeError("the Slack token answers to %s, not the bridge app's bot "
                              "(bot_user_id). Nothing was read. Put the bridge app's own "
                              "token in place" % self.me["user_id"], EXIT_USAGE)
        try:
            # Before the pings file and the tracker: a pass either one stops must not
            # silence the one thing that says the notifier is down (KIT-285).
            self.watch_notifier()
        except OSError as exc:
            # A record it could not write, and so posted nothing: the rest of the pass
            # still runs, and the pass ends loud. Slack was not the trouble.
            self.watch_error = ("could not save the notifier watch's record %s (%s); until "
                                "it can, it says nothing new in Slack"
                                % (watch_path(self.cfg), exc.strerror or exc))
        except BridgeError as exc:
            # A post that failed: the same, and the next pass says it.
            self.watch_error = ("could not post in Slack what the notifier's heartbeat shows "
                                "(%s); the next pass tries again" % exc)
        load_pings(self.cfg)               # one that cannot be read stops the pass first
        if self.cfg["dm_user_ids"]:
            # Read once a pass, and stops it the same way (KIT-295).
            self.dm_posts = load_dm_posts(self.cfg)
        viewer = self.tracker.viewer_id()
        if viewer != self.cfg["owner_user_id"]:
            raise BridgeError("the tracker key belongs to %s, not owner_user_id. The bridge "
                              "acts as the owner, so it refuses to run with anyone else's "
                              "key" % (viewer or "nobody"), EXIT_USAGE)
        if self.first:
            self.say_lanes()
        # A member's DM that cannot be opened stops no channel and no other DM: it is said
        # at the end, and the pass ends loud (KIT-297 review).
        for channel in self.cfg["channel_ids"] + self.open_dms():
            # A DM read for the first time is a baseline too, as the first pass is: turned
            # on for a bridge that has run before, it must not answer days of old messages.
            fresh = channel in self.fresh_dms
            for msg in self.gather(channel):
                k = key_of(channel, msg.get("ts"))
                if k in self.state["handled"]:
                    continue
                self.counts["examined"] += 1
                if self.first or fresh:
                    self.mark(channel, msg, "baseline")
                    self.counts["baseline"] += 1
                    continue
                self.handle_once(channel, msg)
                self.save()
            self.state["threads"].update(self.unrecorded)
            self.unrecorded = {}
            if fresh:
                # Kept only now: a pass that stops before this point opens it, and
                # baselines it, again.
                self.state["dms"][self.fresh_dms[channel]] = channel
                self.say("  first read of the DM with %s: what was already there is passed "
                         "over, answered none" % self.fresh_dms[channel])
            self.save()
        if self.cfg["dm_user_ids"]:
            # After every message is read, so a plan already asked about in a message this
            # pass is not asked about twice (KIT-274).
            self.ask_ready_plans()
        self.rundown()
        self.relay_forwards()
        self.release()
        self.pay_owed()
        self.act_directs()                    # after every message: a stop is seen first
        # KIT-340: after every message is read, so a question answered this pass is not
        # reminded; each question is reminded once, while the clock still holds it open.
        self.remind()
        set_up_before = self.set_up_statuses()
        try:
            self.expire()
            self.prune()
            self.save()
            self.stranded_replans()
        finally:
            # What the clock or a stranded re-plan ended is told in its thread on the next
            # pass (ACT-2).
            self.note_outcomes(set_up_before)
            self.save()
        loud = []
        if self.release_errors:
            loud.append("could not follow %d approved epic(s): %s"
                        % (len(self.release_errors), "; ".join(self.release_errors)))
        if self.state["owed"]:
            loud.append("%d attribution comment(s) still owed, on %s. The actions they record "
                        "stand; each pass writes them again until they land"
                        % (len(self.state["owed"]), ", ".join(
                            o.get("ticket") or "?" for o in self.state["owed"])))
        if self.watch_error:
            loud.append(self.watch_error)
        if self.standup_error:
            loud.append(self.standup_error)
        if self.replan_errors:
            loud.append("%d re-plan(s) need a look, as said where they were asked: %s"
                        % (len(self.replan_errors), "; ".join(self.replan_errors)))
        if self.expire_errors:
            loud.append("; ".join(self.expire_errors))
        if self.dm_errors:
            loud.append("%d member DM(s) could not be opened and were not read; every channel "
                        "was. %s" % (len(self.dm_errors), "; ".join(self.dm_errors)))
        if self.forward_errors:
            loud.append("; ".join(self.forward_errors))
        if loud:
            raise BridgeError("; ".join(loud))

    def rundown(self):
        """The daily stand-up (KIT-349). pipeline_standup decides whether a rundown is due,
        words it and records it; it is posted here through post(), into the owner's DM, so
        it is this app's own post and a bare yes still works. What it could not do is said
        at the end of the pass, and never costs the pass its other work."""
        try:
            _outcome, problem = standup.bridge_tick(self)
        except Exception as exc:                                   # noqa: BLE001
            problem = "the stand-up could not run: %s" % exc
        if problem:
            self.standup_error = self.clean(problem)[:400]

    def open_dms(self):
        """Each listed member's DM with this app (KIT-295), opened once and kept in the
        state: Slack answers the same DM every time. An answer that is not a DM's id is
        refused, never read. A DM the state does not hold yet is read this pass as a
        baseline, and kept once that is done (run). One member's DM that cannot be opened
        (a refusal, a missing im:write, an answer that is no DM) is kept in dm_errors, and
        every other DM and every channel is still read (KIT-297 review)."""
        out = []
        for uid in self.cfg["dm_user_ids"]:
            channel = self.state["dms"].get(uid)
            if not (isinstance(channel, str) and DM_RE.match(channel)):
                try:
                    channel = self.slack.open_dm(uid)
                    if not (isinstance(channel, str) and DM_RE.match(channel)):
                        raise BridgeError("Slack conversations.open named no DM with %s (it "
                                          "answered %r), so no DM was read" % (uid, channel))
                except BridgeError as exc:
                    self.dm_errors.append("%s: %s" % (uid, exc))
                    self.say("  could not open the DM of %s: %s" % (uid, exc))
                    continue
                self.fresh_dms[channel] = uid
            if channel not in out:
                out.append(channel)
        self.dm_channels = set(out)
        return out

    def say_lanes(self):
        """KIT-291: on the first pass, each HANDOFF_STATES name a team lacks, and each board
        that could not be read to tell, said once in the log. It never stops the pass:
        `verify` is where a missing name blocks. Whatever the check hits is said as not
        checked: a pass it stopped would save nothing, so every pass after would be a first
        pass stopped the same way."""
        try:
            missing, unknown = handoff_problems(self.cfg, self.tracker)
        except Exception as exc:                                   # noqa: BLE001
            missing, unknown = [], ["the check could not run (%s: %s)"
                                    % (type(exc).__name__, str(exc)[:160])]
        for line in missing:
            self.say("  HANDOFF_STATES: %s, so a child waiting there for your merge would be "
                     "called stopped, with the advice to hand it over again. Fix "
                     "HANDOFF_STATES in bridge.conf (Step 9)" % line)
        for line in unknown:
            self.say("  HANDOFF_STATES not checked: %s" % line)

    def handle_once(self, channel, msg):
        """One message, with a failure counted against it. Three failed passes and it is
        given up on, said once, and passed over: one bad message must not stall every
        pass after it. Short of that the failure stops the pass, and the next one retries."""
        k = key_of(channel, msg.get("ts"))
        before = self.set_up_statuses()
        try:
            self.handle(channel, msg)
        except BridgeError as exc:
            tries = self.state["attempts"].get(k, 0) + 1
            self.state["attempts"][k] = tries
            if tries < 3:
                self.save()
                raise
            # The log line and the record are redacted where they are written (say, mark).
            self.mark(channel, msg, "gave up: %s" % exc)
            self.counts["gave_up"] += 1
            self.say("  gave up on message %s after %d failed passes: %s"
                     % (msg.get("ts"), tries, exc))
            try:
                self.post(channel, msg.get("thread_ts") or msg.get("ts"),
                          "I could not handle this message after %d tries, so I have "
                          "stopped trying. Nothing was changed." % tries, kind="gave-up",
                          facts={"tries": tries})
            except BridgeError:
                pass
        finally:
            # What came of a question the tech lead set up, told in its thread (ACT-2).
            self.note_outcomes(before, (self.state["handled"].get(k) or {}).get("what") or "")

    def actions_left(self):
        hour_ago = self.now - 3600
        return self.cfg["max_actions_per_hour"] - len(
            [t for t in self.state.setdefault("acted_at", []) if t > hour_ago])

    def refused_over_limit(self, channel, thread, msg):
        if self.actions_left() > 0:
            return False
        self.post(channel, thread, "I have acted %d times in the last hour, which is my "
                  "limit." % self.cfg["max_actions_per_hour"], kind="action-limit",
                  bind=ACTION_LIMIT_BIND, join=" ",
                  facts={"actions": self.cfg["max_actions_per_hour"]})
        self.mark(channel, msg, "refused: action limit")
        self.counts["refused"] += 1
        return True

    def name_of(self, uid):
        profile = self.users.get(uid) or {}
        return notify.safe_title(profile.get("real_name") or
                                 (profile.get("profile") or {}).get("display_name") or uid)

    def owe(self, issue_id, ticket, body):
        """An attribution comment that failed: kept, and written again every pass."""
        self.state["owed"].append({"issue_id": issue_id, "ticket": ticket, "body": body,
                                   "since": self.now})
        self.save()

    def pay_owed(self):
        """Every pass: write each owed attribution comment. The action it records stands,
        and the pass stays loud (exit 1) while one is still owed."""
        if self.dry:
            return
        for o in list(self.state["owed"]):
            try:
                self.tracker.comment(o["issue_id"], o["body"])
            except BridgeError as exc:
                self.say("  still owed: the comment on %s: %s" % (o.get("ticket"), exc))
                continue
            self.state["owed"].remove(o)
            self.save()
            self.say("  wrote the owed comment on %s" % o.get("ticket"))

    @staticmethod
    def newest(sessions):
        """The newest of a child's agent sessions, by each one's own `createdAt`, as the
        planner orders them. On the same time, one that has not ended counts as the newer.
        None when there is none, or when any carries no time: then they cannot be put in
        order."""
        if sessions and all(isinstance(n.get("createdAt"), str) and n["createdAt"]
                            for n in sessions):
            return max(sessions, key=lambda n: (n["createdAt"],
                                                n.get("status") not in SESSION_RAN))
        return None

    @staticmethod
    def newest_ended(sessions):
        """True when the newest of a child's agent sessions ran and ended. A child handed
        over again has a newer session, so the one that ended before it no longer says how
        the child stands. When the sessions cannot be put in order, the child has ended only
        when every session on it has."""
        if not sessions:
            return False
        newest = Pass.newest(sessions)
        if newest is not None:
            return newest.get("status") in SESSION_RAN
        return all(n.get("status") in SESSION_RAN for n in sessions)

    def watched(self, channel):
        """True for a channel this bridge reads: a listed channel, or a listed member's DM."""
        return (channel in self.cfg["channel_ids"] or channel in self.dm_channels
                or channel in self.state["dms"].values())

    def prune(self):
        horizon = self.now - (self.cfg["lookback_days"] + 1) * 86400

        def fresh(k):
            return ts_float(k.rsplit(":", 1)[-1]) >= horizon
        for name in ("handled", "threads", "posted", "attempts"):
            self.state[name] = dict((k, v) for k, v in self.state[name].items() if fresh(k))
        self.state["questions"] = dict(
            (k, q) for k, q in self.state["questions"].items()
            if q.get("status") == OPEN or q.get("asked_at", 0) >= horizon)
        self.state["asked_at"] = [t for t in self.state["asked_at"] if t > self.now - 3600]
        self.state["limit_said"] = dict((c, t) for c, t in self.state["limit_said"].items()
                                        if t > self.now - 3600)
        self.state["acted_at"] = [t for t in self.state.get("acted_at", [])
                                  if t > self.now - 3600]
        self.state["relays"] = [t for t in self.state.get("relays", [])
                                if isinstance(t, (int, float)) and t > self.now - 3600]
        self.state["relay_pending"] = dict(
            (k, v) for k, v in self.state.get("relay_pending", {}).items() if fresh(k))
        # KIT-295: the hourly line in each DM, and the DMs of members no longer listed.
        self.state["stray_said"] = dict((c, t) for c, t in self.state["stray_said"].items()
                                        if t > self.now - 3600)
        self.state["dms"] = dict((u, c) for u, c in self.state["dms"].items()
                                 if u in self.cfg["dm_user_ids"])
        # KIT-298: back-room threads and the chat bot's words posted in a DM, by the window,
        # as the other per-message records; the hourly count, by the hour. ACT-1: so are the
        # posts made for a line the tech lead set up.
        for name in ("forwards", "relayed_posts", "set_up_posts"):
            self.state[name] = dict((k, v) for k, v in self.state[name].items() if fresh(k))
        self.state["forwarded_at"] = [t for t in self.state["forwarded_at"]
                                      if isinstance(t, (int, float)) and t > self.now - 3600]


def run_once(cfg, slack, tracker, dry_run, now=None, out=sys.stdout, writer=None,
             deadline=None):
    state, first = load_state(cfg)
    p = Pass(cfg, slack, tracker, state, first, dry_run, now or time.time(), out,
             writer=writer, deadline=deadline)
    p.run()
    c = p.counts
    pending = sorted((v or {}).get("ticket") or "?" for v in p.state["relay_pending"].values())
    summary = ("bridge: %d message(s) examined, %d asked, %d confirmed, %d acted, "
               "%d answer(s) passed on, %d declined, %d expired, %d refused, %d given up%s%s"
               % (c["examined"], c["asked"], c["confirmed"], c["acted"], c["relayed"],
                  c["declined"], c["expired"], c["refused"], c["gave_up"], "; first pass: %d already in the window, "
                   "answered none" % c["baseline"] if first else "",
                   " (dry run)" if dry_run else ""))
    if pending:
        summary += (" | %d answer(s) unconfirmed, never sent twice: %s — check those tickets"
                    % (len(pending), ", ".join(pending)))
    if not cfg["notifier_bot_user_id"]:
        summary += (" | replies under a ping the notifier did not record are not checked: "
                    "notifier_bot_user_id is unset")
    if p.notifier:
        summary += " | notifier watch: %s" % p.notifier
    if cfg.get("writer") is True:
        summary += " | writer: %d written, %d went out plain" % (c["written"], c["fell_back"])
    if cfg["ask_tech_lead"]:
        summary += (" | tech lead: %d DM question(s) passed on, %d answer(s) passed back"
                    % (c.get("forwarded", 0), c.get("told", 0)))
        if p.history_errors:
            # Could not, never nothing: the question went, without its context (KIT-298).
            summary += ("; %d new thread(s) went without the DM's recent conversation, which "
                        "could not be read: %s" % (len(p.history_errors),
                                                  "; ".join(p.history_errors)))
    out.write(summary + "\n")
    return dict(c, exit=EXIT_DECLINED if pending else EXIT_OK, dry=bool(dry_run),
                summary=summary)


def run_command(cfg, dry_run, timeout, slack=None, tracker=None, out=sys.stdout, now=None,
                writer=None):
    import fcntl

    def _alarm(_signum, _frame):
        raise Deadline()

    armed = False
    secrets = live_secrets(cfg)
    lock = None
    deadline = None
    if timeout and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(int(timeout))
        armed = True
        # The same moment on the clock the writer's time is measured on (KIT-297 review).
        deadline = time.monotonic() + int(timeout)
    try:
        os.makedirs(cfg["state_dir"], exist_ok=True)
        lock = open(lock_path(cfg), "a")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BridgeError("another pass holds the bridge's lock, so this one did "
                              "nothing", EXIT_DECLINED)
        if slack is None or tracker is None:
            token = credential(cfg, "slack_token_env")
            key = credential(cfg, "linear_key_env")
            slack = slack or SlackClient(cfg, token)
            tracker = tracker or TrackerClient(key, cfg["linear_key_env"])
        result = run_once(cfg, slack, tracker, dry_run, now=now, out=out, writer=writer,
                          deadline=deadline)
    except Deadline:
        result = {"exit": EXIT_TIMEOUT, "dry": bool(dry_run),
                  "summary": "FAIL: the %ds deadline passed; this pass is PARTIAL. Every "
                             "message it finished was saved as it went" % timeout}
        out.write(result["summary"] + "\n")
    except BridgeError as exc:
        result = {"exit": exc.code, "dry": bool(dry_run),
                  "summary": "FAIL: %s" % notify.redact(str(exc), secrets)}
        out.write(result["summary"] + "\n")
    except Exception as exc:                                       # noqa: BLE001
        result = {"exit": EXIT_ERROR, "dry": bool(dry_run),
                  "summary": "FAIL: unexpected error: %s"
                             % notify.redact("%s: %s" % (type(exc).__name__, exc), secrets)}
        out.write(result["summary"] + "\n")
    finally:
        if armed:
            signal.alarm(0)
        if lock is not None:
            lock.close()
    if not dry_run:
        write_heartbeat(cfg, result, iso(time.time()))
    return result["exit"]


# KIT-291: what `lanes` prints first on its last line, the installer's `verify` reads it. And
# how many states one team read holds (the planner's team query): a name not among that many
# may be past them, so it is not known to be missing.
LANES_OK = "lanes: ok:"
LANES_MISSING = "lanes: missing:"
LANES_UNKNOWN = "lanes: not checked:"
TEAM_STATES_READ = 100


def handoff_problems(cfg, tracker):
    """KIT-291: (missing, unknown) for each HANDOFF_STATES name on each team the bridge acts
    on, one team read each. Missing: a team without a state of that name, or no team with
    the key. Unknown: a board that could not be read, or one read only in part that holds
    none of that name, which is never said as there (contract §13). With a name the board
    lacks, every child waiting there for a merge is said to have stopped, with the advice to
    hand it over again."""
    missing, unknown = [], []
    for team in cfg["team_keys"]:
        try:
            board = tracker.team_board(team)
        except BridgeError as exc:
            unknown.append("%s's board could not be read (%s)" % (team, str(exc)[:160]))
            continue
        if not board:
            missing.append("no team has the key %s" % team)
            continue
        states = [s.get("name") for s in ((board.get("states") or {}).get("nodes") or [])]
        for name in cfg["handoff_states"]:
            if name in states:
                continue
            if len(states) >= TEAM_STATES_READ:
                unknown.append("%s has more states than one read holds, and none named %r "
                               "among them" % (team, name))
            else:
                missing.append("%s has no state named %r" % (team, name))
    return missing, unknown


def lanes_command(cfg, tracker=None, out=sys.stdout):
    """`lanes` (KIT-291): the HANDOFF_STATES check, with the job's own key, posting and
    saving nothing. Each problem on a line, then a verdict: LANES_OK (exit 0), LANES_MISSING
    (exit 3, whatever else was not read) or LANES_UNKNOWN (exit 1)."""
    secrets = live_secrets(cfg)
    try:
        if tracker is None:
            tracker = TrackerClient(credential(cfg, "linear_key_env"), cfg["linear_key_env"])
        missing, unknown = handoff_problems(cfg, tracker)
    except BridgeError as exc:
        missing, unknown = [], [str(exc)]
    except Exception as exc:                                       # noqa: BLE001
        missing, unknown = [], ["the check could not run (%s: %s)"
                                % (type(exc).__name__, str(exc)[:160])]
    missing, unknown = ([notify.redact(x, secrets) for x in each] for each in (missing, unknown))
    for line in missing + unknown:
        out.write("  %s\n" % line)
    if missing:
        out.write("%s %s%s\n" % (LANES_MISSING, "; ".join(missing),
                                 "; not read: %s" % "; ".join(unknown) if unknown else ""))
        return EXIT_DECLINED
    if unknown:
        out.write("%s %s\n" % (LANES_UNKNOWN, "; ".join(unknown)))
        return EXIT_ERROR
    out.write("%s every HANDOFF_STATES name (%s) is a state on every team (%s)\n"
              % (LANES_OK, ", ".join(cfg["handoff_states"]), ", ".join(cfg["team_keys"])))
    return EXIT_OK


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline, every transport faked
# ══════════════════════════════════════════════════════════════════════════════════════

TEAM = "T0000000AA"
ME = "U0BRIDGE01"
BOT = "B0BRIDGE01"
OWNER = "00000000-0000-4000-8000-000000000001"
CHAN = "C0000000AA"
ALICE, BOB, GUEST, GONE, OTHERWS, CHATBOT = ("U0ALICE001", "U0BOB00001", "U0GUEST001",
                                             "U0GONE0001", "U0OTHER001", "U0CHATBOT1")
APPUSER, UGUEST, NOTEAM = "U0APPUSER1", "U0UGUEST01", "U0NOTEAM01"
NOTIFIER = "U0NOTIFY01"                   # the notifier app's own bot user
DM = "D0ALICE001"                         # ALICE's DM with the bridge app (KIT-295)


def _chan(m):
    """The channel a fake message is in: CHAN unless it was put elsewhere (KIT-295)."""
    return m.get("_channel", CHAN)


class FakeSlack(object):
    made = []                             # every one a case made: the data it held (KIT-342)

    def __init__(self, now):
        FakeSlack.made.append(self)
        self.now = now
        self.messages = []                # top-level and replies, dicts with ts/thread_ts
        self.posts = []
        self.fail = set()
        self.reads = []
        self.dm_ids = {}                  # user -> what conversations.open answers
        self.profiles = {
            ALICE: {"id": ALICE, "team_id": TEAM},
            BOB: {"id": BOB, "team_id": TEAM},
            GUEST: {"id": GUEST, "team_id": TEAM, "is_restricted": True},
            GONE: {"id": GONE, "team_id": TEAM, "deleted": True},
            OTHERWS: {"id": OTHERWS, "team_id": "T0OTHER000"},
            CHATBOT: {"id": CHATBOT, "team_id": TEAM, "is_bot": True},
            APPUSER: {"id": APPUSER, "team_id": TEAM, "is_app_user": True},
            UGUEST: {"id": UGUEST, "team_id": TEAM, "is_ultra_restricted": True},
            NOTEAM: {"id": NOTEAM},
            # Slack documents Slackbot as is_bot false, so only its id gives it away.
            "USLACKBOT": {"id": "USLACKBOT", "team_id": TEAM},
        }
        self._clock = 0

    def add(self, user, text, thread=None, at=None, channel=CHAN, **extra):
        self._clock += 1
        ts = "%.6f" % (at if at is not None else self.now - 10000 + self._clock)
        m = dict({"ts": ts, "text": text}, **extra)
        if user:
            m["user"] = user
        if thread:
            m["thread_ts"] = thread
        if channel != CHAN:
            m["_channel"] = channel
        self.messages.append(m)
        return ts

    def _check(self, method):
        self.reads.append(method)
        if method in self.fail:
            raise BridgeError("Slack %s refused: simulated" % method)

    def auth_test(self):
        self._check("auth.test")
        return {"user_id": ME, "team_id": TEAM, "bot_id": BOT}

    def _tops(self, channel):
        out = []
        for m in self.messages:
            if _chan(m) != channel or (m.get("thread_ts") and m["thread_ts"] != m["ts"]):
                continue
            top = dict(m)
            kids = [r for r in self.messages if r.get("thread_ts") == m["ts"]
                    and r["ts"] != m["ts"] and _chan(r) == channel]
            if kids:
                top["reply_count"] = len(kids)
                top["latest_reply"] = max(r["ts"] for r in kids)
            out.append(top)
        return out

    def history(self, channel, oldest, max_pages):
        self._check("conversations.history")
        return [m for m in self._tops(channel) if float(m["ts"]) >= oldest]

    def recent(self, channel, before, limit):
        """SlackClient.recent: the newest `limit` before `before`, newest first, no oldest
        bound (KIT-298)."""
        self._check("conversations.history")
        self.recent_reads = getattr(self, "recent_reads", []) + [(channel, before, limit)]
        tops = [m for m in self._tops(channel) if float(m["ts"]) < float(before)]
        return sorted(tops, key=lambda m: -float(m["ts"]))[:limit]

    def replies(self, channel, ts):
        self._check("conversations.replies")
        return [m for m in self.messages if _chan(m) == channel
                and (m["ts"] == ts or m.get("thread_ts") == ts)]

    def open_dm(self, uid):
        self._check("conversations.open")
        return self.dm_ids.get(uid, "D" + uid[1:])

    def user(self, uid):
        self._check("users.info")
        return self.profiles.get(uid, {})

    def post(self, channel, thread_ts, text):
        self._check("chat.postMessage")
        self._clock += 1
        ts = "%.6f" % (self.now - 10000 + self._clock)
        self.posts.append((channel, thread_ts, text))
        m = {"ts": ts, "thread_ts": thread_ts, "text": text, "user": ME, "bot_id": BOT}
        if channel != CHAN:
            m["_channel"] = channel
        self.messages.append(m)
        return ts


class FakeTracker(object):
    made = []                             # every one a case made: the data it held (KIT-342)

    def __init__(self, viewer=OWNER):
        FakeTracker.made.append(self)
        self.viewer = viewer
        self.issues = {
            "PROD-5": {"id": "uuid-5", "identifier": "PROD-5", "title": "Dark mode <!channel>",
                       "createdAt": "2026-10-10T12:00:00Z",
                       "state": {"name": "Backlog", "type": "backlog"},
                       "team": {"key": "PROD"}, "creator": {"displayName": "Chat bot"}},
            "PROD-6": {"id": "uuid-6", "identifier": "PROD-6", "title": "Light mode",
                       "state": {"name": "Backlog", "type": "backlog"},
                       "team": {"key": "PROD"}, "creator": {"displayName": "Chat bot"}},
            "OPS-1": {"id": "uuid-o1", "identifier": "OPS-1", "title": "Other team",
                      "state": {"name": "Backlog", "type": "backlog"},
                      "team": {"key": "OPS"}, "creator": {"displayName": "Someone"}},
            # The ticket a stuck session asked on (KIT-224).
            "TOD-5": {"id": "uuid-t5", "identifier": "TOD-5", "title": "Stuck",
                      "state": {"name": "In Progress", "type": "started"},
                      "team": {"key": "TOD"}, "creator": {"displayName": "Someone"}},
        }
        self.reads = 0
        self.fail = False
        self.live = {}                 # issue uuid -> reasons
        self.team_states = {("PROD", "Plan it"): "st-plan-it", ("PROD", "Todo"): "st-todo",
                            ("PROD", "Done"): "st-done",
                            ("PROD", "Needs Approval"): "st-needs-approval",
                            ("PROD", "Canceled"): "st-canceled"}
        # any other: a to-do state (KIT-299); a cancelled one is where a cancel goes (KIT-351)
        self.state_kinds = {"st-done": "completed", "st-canceled": "canceled"}
        # KIT-299: what a finished piece carries. Links by ticket, as the tracker lists them;
        # comments by ticket, each {body, createdAt, user: {id}}; the follow-ups filed.
        self.links, self.notes_on, self.more_comments = {}, {}, set()
        self.more_links = set()        # pieces whose links run past one read
        self.team_ids = {"PROD": "team-prod"}
        self.board_reads, self.board_raises = 0, None   # the HANDOFF_STATES check (KIT-291)
        self.filed, self.file_raises, self.piece_reads = [], None, 0
        self.agents = {"Agent": "agent-1"}
        self.sessions = {}             # issue uuid -> sessions
        self.sessions_whole = True     # False: more sessions than one pass reads (KIT-284)
        self.overflow = set()          # epics whose children run past one page
        self.blocker_overflow = set()  # children whose blockers run past one page
        self.epics = {}                # epic identifier -> child identifiers, in filing order
        self.moves, self.comments, self.delegations = [], [], []
        self.live_threads = {"TOD-5": ["root-5"]}   # ticket -> the agent's session threads
        self.thread_reads, self.thread_replies = [], []
        self.reply_raises = None
        # KIT-244: an idea's planning tickets, the epics filed from each one, and each
        # issue's moves into Plan it as the planner reads them, newest last. A move into
        # Plan it is recorded unless the issue is in `history_drops`; `move_raises` names
        # the move (counted from 1) that fails.
        self.runs, self.plans, self.triggers = {}, {}, {}
        self.history_drops, self.move_raises, self.trigger_raises = set(), None, None
        self.no_backlog, self.searches, self.move_actor = False, [], OWNER
        # (move number, whether the tracker applied it first, the exception, a step to run
        # before raising): a move whose reply is lost, or a pass cut at that point.
        self.move_fail = None
        # KIT-341: each issue's history as trust_history reads it, oldest first: who moved
        # it where, and who added which label. A move and set_state record one each.
        self.histories = {}
        self.trigger_fail, self.trigger_reads = None, 0     # (read number, exception)
        self.state_kinds["st-backlog"] = "backlog"
        for i in self.issues.values():
            i["state"]["id"] = "st-backlog"

    def viewer_id(self):
        return self.viewer

    def live_work(self, issue_id):
        return list(self.live.get(issue_id, []))

    def team_state(self, team_key, name, kind="unstarted"):
        sid = self.team_states.get((team_key, name))
        if sid and self.state_kinds.get(sid, "unstarted") != kind:
            return None, "%s's %r state is not a %s state" % (
                team_key, name, "done" if kind == "completed" else "to-do")
        return (sid, None) if sid else (None, "%s has no single to-do state named %r"
                                        % (team_key, name))

    def team_board(self, team_key):
        self.board_reads += 1
        if self.board_raises is not None:
            raise self.board_raises
        states = [{"id": sid, "name": name, "type": self.state_kinds.get(sid, "unstarted")}
                  for (team, name), sid in sorted(self.team_states.items()) if team == team_key]
        return {"key": team_key, "states": {"nodes": states}} if states else None

    def team_id(self, team_key):
        tid = self.team_ids.get(team_key)
        return (tid, None) if tid else (None, "0 teams have the key %s" % team_key)

    def piece(self, issue_id):
        self.piece_reads += 1
        if self.fail:
            raise BridgeError("the tracker could not be read (simulated)")
        i = next((x for x in self.issues.values() if x["id"] == issue_id), None)
        if not i:
            return None
        ident = i["identifier"]
        return {"id": issue_id, "identifier": ident,
                "attachments": {"nodes": list(self.links.get(ident, [])),
                                "pageInfo": {"hasNextPage": ident in self.more_links}},
                "comments": {"nodes": list(self.notes_on.get(ident, [])),
                             "pageInfo": {"hasNextPage": ident in self.more_comments}}}

    def file_followup(self, team_id, title, description):
        self.filed.append({"teamId": team_id, "title": title, "description": description})
        if self.file_raises is not None:
            raise self.file_raises
        return {"id": "uuid-900", "identifier": "PROD-900",
                "url": "https://linear.app/example/issue/PROD-900"}

    def agent_user(self, name):
        aid = self.agents.get(name)
        return (aid, None) if aid else (None, "no app user is called %r" % name)

    def sessions_on(self, issue_id):
        return list(self.sessions.get(issue_id, []))

    def sessions_read_whole(self):
        return self.sessions_whole

    def recorded_thread(self, ticket_id, thread_id, agent_id):
        self.thread_reads.append((ticket_id, thread_id, agent_id))
        live = self.live_threads.get(ticket_id) or []
        return "u-" + ticket_id, (thread_id if thread_id in live else None)

    def reply_in_thread(self, issue_uuid, thread_id, body):
        self.thread_replies.append((issue_uuid, thread_id, body))
        if self.reply_raises is not None:
            raise self.reply_raises
        return "reply-%d" % len(self.thread_replies)

    def move(self, issue_id, state_id):
        if self.move_raises is not None and len(self.moves) + 1 == self.move_raises:
            raise BridgeError("the tracker would not move the ticket (simulated)")
        self.move_calls = getattr(self, "move_calls", 0) + 1
        fail = self.move_fail if (self.move_fail or (0,))[0] == self.move_calls else None
        if fail and not fail[1]:
            if fail[3]:
                fail[3]()
            raise fail[2]
        self.moves.append((issue_id, state_id))
        self.record(issue_id, actorId=self.move_actor, toStateId=state_id)
        names = {"st-plan-it": "Plan it", "st-todo": "Todo", "st-done": "Done",
                 "st-backlog": "Backlog", "st-canceled": "Canceled"}
        for i in self.issues.values():
            if i["id"] == issue_id:
                i["state"] = {"id": state_id, "name": names.get(state_id, state_id),
                              "type": self.state_kinds.get(state_id, "unstarted")}
        if state_id == "st-plan-it" and issue_id not in self.history_drops:
            trail = self.triggers.setdefault(issue_id, [])
            trail.append({"id": "hist-%s-%d" % (issue_id, len(trail) + 1),
                          "createdAt": "2025-10-09T08:53:20.000Z", "actorId": self.move_actor})
        if fail:
            if fail[3]:
                fail[3]()
            raise fail[2]

    def record(self, issue_id, **entry):
        """One history entry, later than every one before it (KIT-341)."""
        n = sum(len(v) for v in self.histories.values())
        self.histories.setdefault(issue_id, []).append(dict(
            {"createdAt": "2026-11-01T%02d:%02d:%02d.000Z" % (n // 3600 % 24, n // 60 % 60,
                                                            n % 60),
             "actorId": None, "toStateId": None, "addedLabelIds": []}, **entry))

    def trust_history(self, issue_id):
        if self.fail:
            raise BridgeError("the tracker could not be read (simulated)")
        if getattr(self, "history_raises", None) is not None:
            raise self.history_raises
        return [dict(e) for e in self.histories.get(issue_id, [])]

    def add_label(self, ident, name, actor):
        """A label added to a ticket later, by `actor` (KIT-341)."""
        self.issues[ident].setdefault("labels", []).append(name)
        self.record(self.issues[ident]["id"], actorId=actor, addedLabelIds=["lbl-" + name])

    def backlog_state(self, team_key):
        if self.no_backlog:
            return None, None, "%s has no backlog state" % team_key
        return "st-backlog", "Backlog", None

    def planning_runs(self, team_key, idea, owner):
        # The real client's filter, so the selftest exercises it: the planner's own tickets.
        self.searches.append(("runs", team_key, idea))
        return [dict(r) for r in self.runs.get(idea, []) if run_idea(r, owner) == idea]

    def filed_plans(self, team_key, runs, owner):
        self.searches.append(("plans", team_key, tuple(runs)))
        return [dict(p) for r in runs for p in self.plans.get(r, [])
                if (p.get("creator") or {}).get("id") == owner]

    def plan_trigger(self, issue_id, plan_it_id):
        self.trigger_reads += 1
        if self.trigger_raises is not None:
            raise self.trigger_raises
        if self.trigger_fail and self.trigger_fail[0] == self.trigger_reads:
            raise self.trigger_fail[1]
        trail = self.triggers.get(issue_id) or []
        return dict(trail[-1]) if trail else None

    def add_run(self, idea, run, kind="completed", trigger=None, creator=OWNER):
        """A planning ticket of the planner's for `idea`, in a state of `kind`, as the
        planner writes it (run_body): its routing tag first, then its trigger and idea."""
        self.runs.setdefault(idea, []).append({
            "id": "uuid-" + run, "identifier": run, "creator": {"id": creator},
            "state": {"name": kind.title(), "type": kind},
            "description": run_text(idea, trigger or ("hist-" + run))})

    def in_plan_it(self, ident, moved_at="2025-10-09T07:00:00.000Z", trigger=None):
        """The idea sits in Plan it, moved there by the owner at `moved_at`."""
        issue = self.issues[ident]
        issue["state"] = {"id": "st-plan-it", "name": "Plan it", "type": "unstarted"}
        self.triggers[issue["id"]] = [{"id": trigger or "hist-%s-0" % ident,
                                       "createdAt": moved_at, "actorId": OWNER}]

    def delegate(self, issue_id, agent_id):
        self.delegations.append((issue_id, agent_id))
        for i in self.issues.values():
            if i["id"] == issue_id:
                i["delegate"] = {"id": agent_id}

    def add_epic(self, ident, kids, epic_labels=("provenance:agent",),
                 kid_label="provenance:epic", creator=OWNER):
        """An epic as the idea gate files it: children under it, linked by 'blocks'."""
        def make(ident_, title, labels):
            n = ident_.split("-")[1]
            self.issues[ident_] = {
                "id": "uuid-%s" % n, "identifier": ident_, "title": title,
                "state": {"id": "st-backlog", "name": "Backlog", "type": "backlog"},
                "team": {"key": "PROD"}, "creator": {"id": creator, "displayName": "Planner"},
                "labels": list(labels), "parent": None, "delegate": None, "blocked_by": [],
                "description": "What %s does." % ident_}
            return self.issues[ident_]
        make(ident, "Epic %s" % ident, epic_labels)
        self.epics[ident] = []
        for kid, after in kids:
            child = make(kid, "Child %s" % kid, [kid_label] if kid_label else [])
            child["parent"] = {"id": self.issues[ident]["id"]}
            child["blocked_by"] = list(after)
            self.epics[ident].append(kid)

    def set_state(self, ident, kind, actor=OWNER):
        names = {"backlog": "Backlog", "unstarted": "Todo", "started": "In Progress",
                 "completed": "Done", "canceled": "Canceled"}
        self.issues[ident]["state"] = {"id": "st-" + kind, "name": names[kind], "type": kind}
        self.record(self.issues[ident]["id"], actorId=actor, toStateId="st-" + kind)

    def epic(self, ident):
        self.reads += 1
        if self.fail:
            raise BridgeError("the tracker could not be read (simulated)")
        e = self.issues.get(ident) or next(
            (i for i in self.issues.values() if i["id"] == ident), None)
        if not e:
            return None
        ident = e["identifier"]

        def node(i):
            return {"id": i["id"], "identifier": i["identifier"], "title": i["title"],
                    "description": i.get("description"), "project": i.get("project"),
                    "state": dict(i["state"]), "team": dict(i["team"]),
                    "delegate": i.get("delegate"), "creator": i.get("creator"),
                    "labels": {"nodes": [{"id": "lbl-" + n, "name": n}
                                         for n in i.get("labels", [])]},
                    "children": {"nodes": [{"id": self.issues[k]["id"]}
                                           for k in self.epics.get(i["identifier"], [])]},
                    "inverseRelations": {"nodes": [
                        {"type": "blocks", "issue": {
                            "id": self.issues[b]["id"], "identifier": b,
                            "state": dict(self.issues[b]["state"])}}
                        for b in i.get("blocked_by", [])],
                        "pageInfo": {"hasNextPage": i["identifier"] in self.blocker_overflow}}}
        out = node(e)
        out["parent"] = e.get("parent")
        out["creator"] = e.get("creator")
        out["children"] = {"nodes": [node(self.issues[k]) for k in self.epics.get(ident, [])],
                           "pageInfo": {"hasNextPage": ident in self.overflow}}
        return out

    def comment(self, issue_id, body):
        self.comments.append((issue_id, body))

    def issue(self, ident):
        self.reads += 1
        if self.fail:
            raise BridgeError("the tracker could not be read (simulated)")
        return self.issues.get(ident)


def run_text(idea, trigger):
    """A planning ticket's description as the planner writes it (pipeline_plan_poller.run_body),
    with Linear's escaping of the routing tag's bracket (seen live on TOD-134)."""
    return ("\\%s\n\n**Planning run** for **%s**.\n\n%s%s\n%s%s\n\n%s\nTitle: x\n%s"
            % (poller.machine.planning_tag(poller.machine.PLANNING_PREFIX + "prod"), idea,
               poller.TRIGGER_PREFIX, trigger, poller.IDEA_PREFIX, idea, poller.FENCE_OPEN,
               poller.FENCE_CLOSE))


def _config_error(raw):
    try:
        validate_config(raw)
        return ""
    except BridgeError as exc:
        return str(exc)


def module_pins(text, tracker=False):
    """The pins every module split out of the bridge holds (KIT-312, part 2: KIT-407), as
    (case, got, want), on its own text above its tests. It holds no HTTP call of its own:
    only the transport's HTTP call refuses a redirect, so only it may make one. It holds no
    GraphQL write of its own: the bridge's four are in its tracker client, pinned there.
    The writes it makes go through that client, and the four-write pins count their call
    sites.

    The tracker client is the one module that calls the transport's HTTP call and holds
    the GraphQL writes, so its pin is narrower: nothing else of its own reaches a server,
    and it holds no copy of the transport's names. On main the bridge's own scan read the
    tracker client; it moved, so its pin moved with it (review of #258)."""
    body = text[:text.index("# Selftest — offline")]
    if tracker:
        raw = ("def _http_json(", "class _RefuseRedirect(", "class BridgeError(",
               "class SlackClient(", "build_opener(", "urlopen(", "urllib.request",
               "http.client", "socket", "_post_json")
        return [("calls-no-http-but-the-transports", [w for w in raw if w in body], [])]
    http = ("_http_json", "_post_json", "urllib", "urlopen", "http.client", "socket")
    writes = ("mutation", "issueUpdate", "issueCreate", "commentCreate", "graphql",
              "api.linear.app")
    return [("holds-no-http-call-of-its-own", [w for w in http if w in body], []),
            ("holds-no-graphql-write-of-its-own", [w for w in writes if w in body], [])]


# THE BIND SENTENCES (KIT-342): the helpers returning (text, bind, why); the two binds not
# named *_BIND; words the bridge never says (the voice guide's swaps: pieces, the dev,
# Linear); and the lanes a bind never names, by their default names.
REFUSAL_HELPERS = ("replan_refusal", "replan_work_refusal", "replan_changed",
                   "block_refusal")
NAMED_BINDS = ("DM_HELP", "NOTIFIER_CHECK")
BANNED_WORDS_RE = re.compile(r"\b(child|children|epics?|delegat\w*|bounce\w*|sessions?|"
                             r"dispatcher|tracker|fingerprints?|ledger|provenance|"
                             r"heartbeats?|pings?|daemons?|bridge(?:'s)?)\b", re.IGNORECASE)
# A question's what-a-yes-does slot, by its place in the bind, holds one of these (review of
# #265; the slot is the fourth since ACT-1 put the yes first); and where each posting helper
# takes its text, so a post no case reaches is scanned too.
THEN_SLOTS = {"DIRECT_BIND": (2, ("DIRECT_PLAN_THEN_BIND", "DIRECT_PLAN_EACH_THEN_BIND",
                                 "DIRECT_REPLAN_THEN_BIND", "DIRECT_REPLAN_EACH_THEN_BIND")),
              "COMBINED_BIND": (3, ("COMBINED_PLAN_THEN_BIND", "COMBINED_REPLAN_THEN_BIND",
                                   "ACT_OFF_THEN_BIND")),
              "QUESTION_BIND": (4, ("PLAN_THEN_BIND", "REPLAN_THEN_BIND", "ACT_OFF_THEN_BIND")),
              "APPROVE_BIND": (4, ("APPROVE_THEN_BIND", "APPROVE_OFF_THEN_BIND")),
              "PLAN_MOVE_BIND": (4, ("PAUSE_THEN_BIND", "RESUME_THEN_BIND", "CANCEL_THEN_BIND",
                                     "APPROVE_OFF_THEN_BIND"))}
POST_TEXT_ARGS = {"post": 2, "post_refusal": 2, "refuse_request": 3, "note": 2, "drop": 2,
                  "finish": 1, "refuse": 0}
STATE_NAMES = ("Plan it", "Todo", "Done", "Backlog", "Needs Approval")


def bind_offences(text, pinned, constants):
    """Each place one file's code builds a bind from anything but a pinned sentence (KIT-342),
    as "line: what". A bind is a `bind=` argument, a nested helper's `then`, `how` or `bind`,
    a local `bind` or `then`, a refusal helper's bind, and what `again` returns: a pinned
    name, maybe `%`-formatted, a choice of two, "", a local, `self.again()` or `self.clean()`
    of one. Any other text, or an unpinned capitals name, is an offence; so is a helper
    documented as returning (text, bind, why) that REFUSAL_HELPERS lacks."""
    import ast
    body = text[:text.index("# Selftest — offline")]
    tree, bad = ast.parse(body), set()

    def judge(node):
        if isinstance(node, ast.Constant) and node.value == "":
            return
        if isinstance(node, ast.Name):
            # A constant is named in capitals, imported or not: only a pinned one is a bind.
            if (node.id in constants or node.id.isupper()) and node.id not in pinned:
                bad.add("%d: %s is not a pinned bind" % (node.lineno, node.id))
            return
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
                and isinstance(node.left, ast.Name) and node.left.id in pinned):
            return
        if isinstance(node, ast.IfExp):
            judge(node.body)
            judge(node.orelse)
            return
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                judge(value)
            return
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "again"):
            return
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "clean" and len(node.args) == 1):
            judge(node.args[0])                    # the redaction of a bind is that bind
            return
        bad.add("%d: %s" % (node.lineno, (ast.get_source_segment(body, node) or "?")[:70]))
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        helpers = dict((inner.name, [(i, a.arg) for i, a in enumerate(inner.args.args)
                                     if a.arg in ("then", "how", "bind")])
                       for inner in ast.walk(fn)
                       if isinstance(inner, ast.FunctionDef) and inner is not fn)
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "bind":
                        judge(kw.value)
                callee = node.func.id if isinstance(node.func, ast.Name) else None
                for index, param in helpers.get(callee, ()):
                    if index < len(node.args):
                        judge(node.args[index])
                    for kw in node.keywords:
                        if kw.arg == param and param != "bind":
                            judge(kw.value)
            elif isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in ("bind", "then") for t in node.targets):
                judge(node.value)              # `then` is a bind's slot (review of #265)
        doc = ast.get_docstring(fn) or ""
        if "(text, bind, why)" in doc and fn.name not in REFUSAL_HELPERS:
            bad.add("%d: %s returns a bind, and is not one of REFUSAL_HELPERS"
                    % (fn.lineno, fn.name))
        if fn.name not in REFUSAL_HELPERS + ("again",):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            value = node.value
            if fn.name == "again":
                judge(value)
            elif (isinstance(value, ast.Tuple) and value.elts
                  and isinstance(value.elts[0], ast.Tuple) and len(value.elts[0].elts) == 3):
                judge(value.elts[0].elts[1])
            elif isinstance(value, ast.Tuple) and len(value.elts) == 3:
                judge(value.elts[1])
    return sorted(bad)


def post_text_words(text):
    """Each banned word in text one file's code hands a posting helper, as "line: word": the
    scan of what cases post never sees a post no case reaches (review of #265)."""
    import ast
    body = text[:text.index("# Selftest — offline")]
    found = set()
    for node in ast.walk(ast.parse(body)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        at = POST_TEXT_ARGS.get(name)
        parts = node.args[at:at + 1] if at is not None else []
        parts += [k.value for k in node.keywords if k.arg in ("plain", "bind")]
        for part in parts:
            for each in ast.walk(part):
                if isinstance(each, ast.Constant) and isinstance(each.value, str):
                    found |= set("%d: %s" % (each.lineno, w.lower())
                                 for w in BANNED_WORDS_RE.findall(each.value))
    return sorted(found)


class Kit(object):
    """The selftest's fixtures, and the helpers the cases of more than one module read
    (KIT-312): each moved here unchanged from the cases that first made it. The bridge's
    selftest makes one kit for all its cases, run in main's order; a module's own
    selftest makes one for its cases alone. `with kit as tmp` is the folder their state
    lives in."""

    def __init__(self, check, tmp):
        import io
        import plistlib

        # The stand-up's settings are the role account's own file. A test pass reads one
        # that is not there, so the stand-up is off unless a case turns it on (KIT-349).
        standup.DEFAULT_SETTINGS_FILE = os.path.join(tmp, "standup-off.json")

        def cfg_for(name, **over):
            raw = dict(EXAMPLE_CONFIG, channel_ids=[CHAN], owner_user_id=OWNER,
                       bot_user_id=ME, state_dir=os.path.join(tmp, name))
            raw.update(over)
            return validate_config(raw)

        now = 1760000000.0

        def world(name, **over):
            cfg = cfg_for(name, **over)
            slack, tracker = FakeSlack(now), FakeTracker()
            return cfg, slack, tracker

        def run(cfg, slack, tracker, dry=False, at=None):
            out = io.StringIO()
            try:
                res = run_once(cfg, slack, tracker, dry, now=at or now, out=out)
                return res["exit"], out.getvalue()
            except BridgeError as exc:
                return exc.code, str(exc)

        def posted(slack):
            return [t for _c, _th, t in slack.posts]

        def status_of(cfg):
            doc = json.load(open(state_path(cfg)))
            return [v["status"] for v in doc["questions"].values()]

        def refusing_comment(_issue_id, _body):
            raise BridgeError("the tracker would not take the comment (simulated)")

        def ping_text(ticket="TOD-5", title="Stuck", mentions=()):
            """What the notifier posts for one agent:blocked ping, made by its own builders,
            so these pings say what it says and cannot drift from it (KIT-281)."""
            ncfg = dict(notify.EXAMPLE_CONFIG, mention_chat_user_ids=list(mentions))
            return notify.mention_prefix(notify.ESC_BLOCKED, ncfg) + notify.build_message(
                notify.ESC_BLOCKED, ticket, title, notify.ticket_url(ncfg, ticket))

        def outcome(fn):
            try:
                return fn()
            except Exception as exc:                              # noqa: BLE001
                return "raised %s: %s" % (type(exc).__name__, exc)

        every = 300                                   # the notifier's own StartInterval

        notifier_plist = {"Label": "com.example.notifier", "StartInterval": every,
                          "ProgramArguments": ["/bin/sh", "-c", "exec /usr/bin/python3 "
                                               "$HOME/kit/scripts/pipeline_notify_local.py "
                                               "run --config $HOME/.stage-e/notifier.json"]}

        def nworld(name, plist_doc=None, **over):
            ndir = os.path.join(tmp, name + "-notifier")
            os.makedirs(ndir)
            plist = os.path.join(ndir, "com.example.notifier.plist")
            with open(plist, "wb") as fh:
                plistlib.dump(notifier_plist if plist_doc is None else plist_doc, fh)
            opts = {"notifier_plist": plist,
                    "pings_file": os.path.join(ndir, "notifier-bridge-pings.json")}
            opts.update(over)
            c, s, t = world(name, **opts)
            return c, s, t, os.path.join(ndir, "notifier-heartbeat.json")

        def beat(path, at, code=0, dry=False):
            with open(path, "w") as fh:
                json.dump({"schema": notify.HEARTBEAT_SCHEMA, "at": iso(at), "dry": dry,
                           "exit": code}, fh)

        dm_help = ("I can do `plan <ticket id>`, `replan <ticket id>`, `approve <ticket id>`, "
                   "`pause <ticket id>`, `resume <ticket id>`, `cancel <ticket id>` and yes or "
                   "no here. I read an ask in your own words too, such as `please replan "
                   "<ticket id>`, with up to 10 tickets to plan or replan at once. To answer a "
                   "dev, reply in the thread under its message. For anything else, ask the "
                   "tech lead in the chat channel for now.")

        def dmworld(name, **over):
            c, s, t = world(name, dm_user_ids=[ALICE], **over)
            run(c, s, t)                                    # the first pass: a baseline
            return c, s, t

        def in_dm(s, channel=DM):
            return [(th, x) for ch, th, x in s.posts if ch == channel]

        # Asking the tech lead from the DM (KIT-298), read by its cases and the requests
        # module's (ACT-1).
        tl_on = dict(ask_tech_lead=True, chat_bot_user_id=CHATBOT, back_room_channel_id=CHAN)

        def tlworld(name, **over):
            return dmworld(name, **dict(tl_on, **over))

        def asked_tl(name, *lines, **over):
            c, s, t = tlworld(name, **over)
            for line in lines:
                s.add(ALICE, line, channel=DM)
            run(c, s, t)
            return c, s, t

        def in_room(s):
            return [(th, x) for ch, th, x in s.posts if ch == CHAN]

        def dm_lines(s):
            return [x for _th, x in in_dm(s)]

        def tl_root(c):
            return [r["root"] for r in json.load(open(state_path(c)))["forwards"].values()][0]

        def tl_says(s, root, text, **extra):
            """The chat bot's reply in the thread under the forward, as its own app posts it."""
            return s.add(CHATBOT, text, thread=root, bot_id="B0CHATBOT1", app_id="A0CHATAPP1",
                         **extra)

        writer_bin = "/opt/claude/bin/claude"
        # The contract's line, word for word: said once a day after a message gone out plain.
        plain_note = "\n\n(I couldn't reach my writer, so this one's the plain version.)"

        def written_as(keep):
            return "I have news on %s." % ", ".join(keep) if keep else "I have news."

        class FakeWriter(object):
            """Stands in for voice.write: records each call, and answers with a rewrite that
            keeps every id it must, or with the plain text, or raises, as told."""

            def __init__(self, by="model", raises=None):
                self.by, self.raises, self.calls = by, raises, []

            def __call__(self, cfg, kind, facts, fallback, keep=(), bind="", runner=None,
                         now=None):
                self.calls.append({"kind": kind, "facts": dict(facts), "fallback": fallback,
                                   "keep": list(keep), "bind": bind, "now": now})
                if self.raises is not None:
                    raise self.raises
                text = written_as(keep) if self.by == "model" else fallback
                return {"text": text + ("\n\n" + bind if bind else ""), "by": self.by,
                        "why": "" if self.by == "model" else "the writer exited 1",
                        "cost_usd": None}

            def kinds(self):
                return [x["kind"] for x in self.calls]

            def last(self, kind):
                return [x for x in self.calls if x["kind"] == kind][-1]

        audited = []                                  # every fake writer, for the audit

        def wworld(name, **over):
            kw = dict(writer=True, writer_bin=writer_bin)
            kw.update(over)
            c, s, t = world(name, **kw)
            s.profiles[ALICE]["real_name"] = "Alice Example"
            return c, s, t

        def wrun(c, s, t, fw, dry=False, at=None):
            if fw not in audited:
                audited.append(fw)
            out = io.StringIO()
            try:
                res = run_once(c, s, t, dry, now=at or now, out=out, writer=fw)
                return res["exit"], out.getvalue()
            except BridgeError as exc:
                return exc.code, str(exc)

        vars(self).update((name, value) for name, value in list(locals().items())
                          if name != "self")

    def __enter__(self):
        return self.tmp

    def __exit__(self, *_exc):
        return False

    def take(self, *names):
        """Each named fixture or helper, in that order."""
        return tuple(getattr(self, name) for name in names)

    def keep(self, **names):
        """Names one function's cases made, for a later function's cases to take."""
        vars(self).update(names)


def split_module_selftest(path, cases=()):
    """A split module's own selftest (KIT-407, KIT-312): its pins, then its own cases with a
    kit of their own, under the guard the bridge's selftest keeps: no case reaches a real
    server, and a refused call fails the run."""
    import tempfile
    import pipeline_bridge_tracker as tracker
    import pipeline_bridge_transport as transport
    name = os.path.basename(path)[:-3]
    failures, count, reached = [], [0], []

    def check(case, got, want):
        count[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (case, got, want))
    with open(path, encoding="utf-8") as fh:
        for case, got, want in module_pins(fh.read(), name == "pipeline_bridge_tracker"):
            check(case, got, want)
    # Its cases run in the module the bridge imports, never in a copy of it named
    # __main__, which the guard below would not reach (review of #258).
    check("its-cases-run-in-the-module-the-bridge-imports",
          sorted(set(f.__module__ for f in cases) - {name}), [])
    # Read before the guard goes in: the tracker's HTTP call is the transport's, with its
    # redirect refusal (review of #258).
    check("the-tracker-runs-on-the-transports-own-objects",
          (tracker._http_json is transport._http_json,
           tracker.BridgeError is transport.BridgeError), (True, True))

    def no_network(url, *_a, **_k):
        reached.append(str(url).split("?")[0])
        raise AssertionError("a selftest case reached real HTTP: %s" % reached[-1])
    real_http = transport._http_json
    transport._http_json = tracker._http_json = globals()["_http_json"] = no_network
    try:
        # The guard is on all three names before any case runs: a case that forgets to
        # stand in for one would otherwise reach the real server unseen.
        check("no-case-reaches-real-http",
              (transport._http_json.__name__, tracker._http_json.__name__,
               globals()["_http_json"].__name__), ("no_network",) * 3)
        with tempfile.TemporaryDirectory() as tmp:
            kit = Kit(check, tmp)
            for run_cases in cases:
                run_cases(kit)
    finally:
        transport._http_json = tracker._http_json = globals()["_http_json"] = real_http
    check("no-case-reached-real-http", list(reached), [])
    if failures:
        print("FAIL: %s selftest" % name)
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: %s selftest (%d cases)" % (name, count[0]))
    return 0

def selftest():
    """No case reaches a real server: the HTTP call, under this module's name, the
    transport module's and the tracker module's, refuses until a case stands in for it. A
    case that stands in for one name no longer reaches the other modules' clients
    (KIT-388, KIT-312), so a missed one would otherwise have called a server. Each refused
    call is recorded and fails the run, since a case can read the refusal as an ordinary
    failure and still pass."""
    if __name__ == "__main__":
        # The cases run in the module the split modules import, never in a copy of it named
        # __main__, so their cases and the pass share one copy of this module (KIT-312).
        import pipeline_bridge
        return pipeline_bridge.selftest()
    import pipeline_bridge_tracker as tracker
    import pipeline_bridge_transport as transport
    # Read before the guard goes in: the names the bridge and its tracker client run on are
    # the transport's own objects, so the tracker's call keeps the transport's redirect
    # refusal (review of KIT-388; of #258, since the client moved).
    identity = (BridgeError is transport.BridgeError, SlackClient is transport.SlackClient,
                globals()["_http_json"] is transport._http_json,
                tracker.BridgeError is transport.BridgeError,
                tracker._http_json is transport._http_json)
    real_http, reached = transport._http_json, []

    def no_network(url, *_a, **_k):
        reached.append(str(url).split("?")[0])
        raise AssertionError("a selftest case reached real HTTP: %s" % reached[-1])
    transport._http_json = tracker._http_json = globals()["_http_json"] = no_network
    try:
        return _selftest(identity, reached)
    finally:
        transport._http_json = tracker._http_json = globals()["_http_json"] = real_http


def _selftest(identity=(True,) * 5, reached=()):
    import io
    import tempfile
    import pipeline_bridge_transport as transport
    import pipeline_bridge_tracker as tracker_module
    import pipeline_bridge_answers
    import pipeline_bridge_config
    import pipeline_bridge_direct
    import pipeline_bridge_pings
    import pipeline_bridge_plans
    import pipeline_bridge_replan
    import pipeline_bridge_requests
    import pipeline_bridge_tech_lead
    import pipeline_bridge_tracker
    import pipeline_bridge_watch
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    check("no-case-reaches-real-http",
          (globals()["_http_json"].__name__, transport._http_json.__name__,
           tracker_module._http_json.__name__), ("no_network", "no_network", "no_network"))
    check("the-bridge-runs-on-the-transports-own-objects", identity, (True,) * 5)

    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    body = src[:src.index("# Selftest — offline")]
    # Nothing the transport owns is declared here again: a copy would shadow it unseen.
    check("the-bridge-declares-none-of-the-transports-names",
          [w for w in ("class BridgeError(", "class SlackClient(", "class _RefuseRedirect(",
                       "def _http_json(", "build_opener(", "urlopen(") if w in body], [])
    # The modules split out of the bridge (KIT-312), found from its own imports: the text
    # the pins below and the four-write pins read. Read before any case, so a case that
    # scans the bridge's code reads every module of it (KIT-407).
    import ast
    def split_imports(text):
        found = ast.parse(text)
        return ({n.module for n in ast.walk(found) if isinstance(n, ast.ImportFrom)
                 and (n.module or "").startswith("pipeline_bridge_")}
                | {a.name for n in ast.walk(found) if isinstance(n, ast.Import)
                   for a in n.names if a.name.startswith("pipeline_bridge_")})
    def split_sources(text, read):
        """{name: source} for every split module `text` imports, followed until nothing new
        turns up: a module a split module imports is scanned too (review of KIT-388)."""
        found, todo = {}, split_imports(text)
        while todo:
            name = todo.pop()
            found[name] = read(name)
            todo |= split_imports(found[name]) - set(found)
        return found

    def read_split(name):
        with open(os.path.join(HERE, name + ".py"), encoding="utf-8") as fh:
            return fh.read()
    def pinned_text(whole, text, read):
        """`text` and every split module `whole` reaches, each up to its own tests: what
        the write pins below read. Modules are found from the WHOLE file, since main()
        sits below the tests and may import one too (reviews of KIT-388)."""
        found = split_sources(whole, read)
        return text + "".join(found[n][:found[n].index("# Selftest — offline")]
                              for n in sorted(found)), sorted(found)
    body, split_modules = pinned_text(src, body, read_split)
    # Each name is declared in one of the bridge's files only: a copy in a second one, left
    # by a merge, would shadow the first unseen there. No method of Pass is defined twice,
    # since one would hide the other. Each module holds no HTTP call and no GraphQL write
    # of its own (KIT-407).
    split_found = split_sources(src, read_split)

    def bound(text):
        """The names a file binds at its top level above its tests, its imports aside, and
        HERE, the folder each file finds its neighbours in."""
        names = set()
        for node in ast.parse(text[:text.index("# Selftest — offline")]).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for target in getattr(node, "targets", None) or [node.target]:
                    names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        return names - {"HERE"}
    declared = {}
    for name, text in [("pipeline_bridge", src)] + sorted(split_found.items()):
        for each in bound(text):
            declared.setdefault(each, []).append(name)
    # EXIT_ERROR is the one exception: the notifier's own exit code, which the transport
    # reads for its error's default and config with the other codes, so neither can drift.
    check("no-name-is-declared-in-two-of-the-bridges-files",
          sorted((each, files) for each, files in declared.items()
                 if len(files) > 1 and each != "EXIT_ERROR"), [])
    methods = [m for klass in Pass.__mro__[:-1] for m, v in vars(klass).items()
               if isinstance(v, (staticmethod, classmethod, type(check)))]
    check("no-method-of-pass-is-defined-twice",
          sorted(set(m for m in methods if methods.count(m) > 1)), [])
    for name in sorted(split_found):
        if name != "pipeline_bridge_transport":
            for case, got, want in module_pins(split_found[name],
                                               name == "pipeline_bridge_tracker"):
                check("%s:%s" % (name, case), got, want)
    # Every name the bridge's code or a module's reads as a global is there, in that file:
    # one its imports lost would fail only when its line runs, which may be live. The
    # bridge is read too, since it is the file the split took imports from (KIT-407).
    import builtins
    import dis
    import types

    def codes(code):
        yield code
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                yield from codes(const)
    missing = []
    for name in [__name__] + sorted(split_found):
        module = sys.modules[name]
        for value in list(vars(module).values()):
            if getattr(value, "__module__", None) != name:
                continue
            if isinstance(value, type):
                funcs = [getattr(v, "__func__", v) for v in vars(value).values()]
            else:
                funcs = [value]
            for fn in funcs:
                if not isinstance(fn, types.FunctionType):
                    continue
                for code in codes(fn.__code__):
                    missing += ["%s.%s: %s" % (name, fn.__qualname__, ins.argval)
                                for ins in dis.get_instructions(code)
                                if ins.opname == "LOAD_GLOBAL"
                                and ins.argval not in vars(module)
                                and not hasattr(builtins, ins.argval)]
    check("every-name-each-file-reads-is-there", sorted(set(missing)), [])
    # Every module's cases run in two places: here, each in its place among the others,
    # and in the module's own selftest. A cases function left out of either place loses
    # that run unseen, since the other still passes. Both are read from the code, so a
    # call left in a comment is no call (KIT-312, review of #258).
    own = ast.parse(src[src.index("def _selftest("):src.index("\ndef main(")])
    called = {(n.func.value.id, n.func.attr) for n in ast.walk(own)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name)
              and n.func.value.id.startswith("pipeline_bridge_")
              and n.func.attr.startswith("cases_")}
    defined, listed = set(), set()
    for name in sorted(split_found):
        module_tree = ast.parse(split_found[name])
        defined |= {(name, n.name) for n in module_tree.body
                    if isinstance(n, ast.FunctionDef) and n.name.startswith("cases_")}
        listed |= {(name, e.id) for n in ast.walk(module_tree) if isinstance(n, ast.Call)
                   and getattr(n.func, "attr", "") == "split_module_selftest"
                   and len(n.args) > 1 and isinstance(n.args[1], ast.List)
                   for e in n.args[1].elts if isinstance(e, ast.Name)}
    check("every-cases-function-runs-in-the-bridges-selftest", sorted(defined ^ called), [])
    check("every-cases-function-runs-in-its-modules-own-selftest", sorted(defined ^ listed),
          [])
    # 0. THE BIND SENTENCES (KIT-342), word for word. A change to one is a change to this
    #    list, made on purpose.
    PINNED_BINDS = {
        "ACTION_LIMIT_BIND": "Say yes again later.",
        "ACT_OFF_THEN_BIND": ("I'll only note your answer: acting is switched off here, so "
                              "nothing moves"),
        "ALREADY_ASKED_BIND": "Answer that one.",
        "ASKED_TWICE_BIND": ("The tech lead answered that question of yours twice, so I didn't "
                             "set up what the second answer asked for. Say it again if you "
                             "want it."),
        "AMBIGUOUS_BIND": "Reply `%s <ticket id>` to say which.",
        "ANSWER_AGAIN_BIND": "Ask again if you still want to send it.",
        "ANSWER_ASK_BIND": ("*Send your message to the %s dev as your answer?*\n%s\nReply %s "
                            "here within %d hours, and I'll send it to them as you wrote it. "
                            "Reply `no` to drop it. %s"),
        "ANSWER_IN_LINEAR_BIND": "Answer in Linear.",
        "APPROVE_BIND": ("Reply %s %s within %d hours, and %s. Reply `no` to leave it. "
                         "Merging stays yours. %s"),
        "APPROVE_OFF_THEN_BIND": ("I'll only note your answer: acting is switched off here, "
                                  "so nothing moves or starts"),
        "APPROVE_THEN_BIND": ("I'll mark the plan ready and hand its pieces to the agent, at "
                              "most %d at a time, each once the pieces it waits on are done"),
        "ASK_AGAIN_BIND": "Ask again with `%s %s`.",
        "CANCEL_IN_LINEAR_BIND": "To drop a plan I'm not running, cancel it in Linear.",
        "CANCEL_THEN_BIND": ("I'll cancel the plan and never hand the agent another of its "
                             "pieces. A piece already at work keeps going: I cancel only the "
                             "plan, never a piece. You can't undo this here"),
        "CREDIT_BIND": "(Asked for by %s.)",
        "DM_HELP": ("I can do `plan <ticket id>`, `replan <ticket id>`, `approve <ticket id>`, "
                    "`pause <ticket id>`, `resume <ticket id>`, `cancel <ticket id>` and yes "
                    "or no here. I read an ask in your own words too, such as `please replan "
                    "<ticket id>`, with up to 10 tickets to plan or replan at once. To answer "
                    "a dev, reply in the thread under its message. For anything else, ask the "
                    "tech lead in the chat channel for now."),
        "COMBINED_BIND": ("Reply %s here within %d hours, and %s. Reply `no` to drop them all, "
                          "or `yes <ticket id>` for just one. Merging stays yours. %s"),
        "COMBINED_PLAN_THEN_BIND": "I'll hand each one to the planner",
        "COMBINED_REPLAN_THEN_BIND": "I'll hand each one back to the planner for a new plan",
        "DIRECT_BIND": "In %d seconds %s: %s. Say stop to cancel.",
        "DIRECT_PLAN_THEN_BIND": "I'll hand it to the planner",
        "DIRECT_PLAN_EACH_THEN_BIND": "I'll hand each to the planner",
        "DIRECT_REPLAN_THEN_BIND": "I'll hand it back to the planner for a new plan",
        "DIRECT_REPLAN_EACH_THEN_BIND": "I'll hand each back to the planner for a new plan",
        "EDITED_BIND": "Send the answer again as a new reply.",
        "FOLLOWUP_BIND": ("Say `yes` %s within %d hours and I'll file %s as one follow-up "
                          "ticket in %s. It starts no work. Say `no` to leave %s."),
        "FOLLOWUP_LEFT_BIND": ("The notes stay in my wrap-up; file them yourself if you want "
                               "them."),
        "FOREIGN_POST_BIND": "Reply `%s %s` to answer the one about %s.",
        "FORWARD_EDITED_BIND": "Send it again as a new message.",
        "FORWARD_LIMIT_BIND": "Ask again later.",
        "FORWARD_LOST_BIND": "Ask again if you still need an answer.",
        "FORWARD_NO_ANSWER_BIND": "Ask again in a bit.",
        "FORWARD_TOO_LONG_BIND": "Ask in shorter pieces.",
        "LATE_ASK_BIND": ("The tech lead's reply came more than %d minutes after your "
                          "question, so I didn't set up what it asked for. Say it again if you "
                          "want it."),
        "LONG_ANSWER_BIND": "A long answer belongs in Linear.",
        "NAMED_YES_BIND": "The tech lead picked %s, so the yes must name it: reply `yes %s`.",
        "NAMED_YES_WORD_BIND": "`yes %s`",
        "LOOKUP_LIMIT_BIND": "Ask again later.",
        "NOTIFIER_CHECK": ("To check it, run `python3 scripts/pipeline_notifier_setup.py "
                           "verify` in your kit checkout."),
        "NO_HEADER_BIND": ("The tech lead's reply asked for an action without naming your "
                           "question, so I didn't set it up. Say it again if you want it."),
        "OLDER_ASK_BIND": ("The tech lead's reply asked for an action, but not for your newest "
                           "question, so I didn't set it up. Say it again if you want it."),
        "PAGE_NOT_WAITING_BIND": ("To answer a dev, reply under the message saying it "
                                  "stopped to ask you something, or answer in Linear."),
        "PINGS_OFF_BIND": "Until then, answer in Linear.",
        "PING_UNCONFIRMED_BIND": ("I couldn't confirm that your answer reached the %s dev. I "
                                  "won't send it again: check the ticket in Linear, and "
                                  "answer there if it's missing."),
        "PLAN_AGAIN_BIND": "To plan it again, type `plan %s`.",
        "PLAN_FILED_BIND": "Approve that plan, or cancel it before you plan the idea again.",
        "PLAN_IT_BIND": "To plan it, type `plan %s`.",
        "PLAN_MOVE_BIND": ("Reply %s %s within %d hours, and %s. Reply `no` to leave it as it "
                           "is. %s"),
        "PLAN_PIECE_BIND": "To %s the plan, type `%s %s`.",
        "PAUSE_THEN_BIND": ("I'll move the plan back to the backlog and start none of its "
                            "pieces until you resume it. A piece already at work keeps going"),
        "RESUME_HINT_BIND": "To go on with it, type `resume %s`.",
        "RESUME_THEN_BIND": ("I'll mark the plan ready again and go on handing its pieces to "
                             "the agent, at most %d at a time, each once the pieces it waits "
                             "on are done. Merging stays yours"),
        "PLAN_THEN_BIND": "I'll hand it to the planner",
        "QUESTION_BIND": ("Reply %s %s within %d hours, and %s. Reply `no` to drop it. "
                          "Merging stays yours. %s"),
        "RELAY_FAILED_BIND": "Ask again if you still need it.",
        "REPLAN_ACT_OFF_BIND": ("Switch acting on and reply `yes %s` to finish it, or reply "
                                "`no %s` to leave it there."),
        "REPLAN_AGAIN_BIND": "To plan it again, type `replan %s`.",
        "REPLAN_BY_HAND_BIND": ("If no planning run starts within ten minutes, move %s out of "
                                "planning in Linear and back in by hand."),
        "REPLAN_FINISH_BIND": "To finish, type `plan %s`.",
        "REPLAN_FIX_BIND": "Fix that, then reply `yes %s`, or reply `no %s` to leave it there.",
        "REPLAN_LEAVE_BIND": "Reply `no %s` to leave it there; then, to plan it, type `plan %s`.",
        "REPLAN_LOOK_BIND": ("Look at %s in Linear. If it isn't back with the planner, type "
                             "`plan %s`."),
        "REPLAN_THEN_BIND": "I'll hand it back to the planner for a new plan",
        "RUN_OPEN_BIND": "Ask again once that run has finished.",
        "SETTLE_BIND": "Give it %d minutes, then ask again.",
        "TL_CREDIT_BIND": "Set up by the tech lead after your message at %s.",
        "TL_LINKS_PLAIN_BIND": ("Some links in that answer go to sites you haven't allowed, so "
                                "I made them plain text: their dots and colons show in square "
                                "brackets."),
        "TL_PICKED_BIND": "The tech lead picked %s.",
        "TL_UNKNOWN_BIND": ("The tech lead asked for %s, which I can't set up. I can set up "
                            "only %s."),
        "TL_WITHHELD_BIND": ("I didn't pass on that answer from the tech lead: it had something "
                             "in it shaped like a password or a key. Ask again if you still "
                             "need it."),
        "TOO_MANY_OPEN_BIND": "Answer one, or let it expire, then ask again.",
        "UNRECORDED_PING_BIND": "Answer under the newer message, or in Linear.",
        "READY_PLAN_CREDIT_BIND": "(Asked because its plan was just filed.)",
        "YES_BIND": "`yes`",
        "YOU_NAMED_BIND": "You named %s.",
    }
    held = {}
    for name, text in [(__name__, src)] + sorted(split_found.items()):
        for each in sorted(bound(text)):
            if each.endswith("_BIND") or each in NAMED_BINDS:
                held[each] = vars(sys.modules[name])[each]
    check("every-bind-sentence-is-pinned-word-for-word", held, PINNED_BINDS)
    check("every-bind-is-built-only-from-a-pinned-sentence",
          [("%s:%s" % (name, o)) for name, text in [(__name__, src)] + sorted(split_found.items())
           for o in bind_offences(text, set(PINNED_BINDS), set(declared))], [])
    check("no-sentence-handed-to-a-posting-helper-holds-a-banned-word",
          [("%s:%s" % (name, o)) for name, text in [(__name__, src)] + sorted(split_found.items())
           for o in post_text_words(text)], [])
    check("no-pinned-bind-holds-a-banned-word-or-a-lane",
          sorted(name for name, value in PINNED_BINDS.items()
                 if BANNED_WORDS_RE.search(value) or any(
                     re.search(r"\b%s\b" % re.escape(lane), value) for lane in STATE_NAMES)),
          [])
    # Run as `python3 scripts/pipeline_bridge.py`, this file is __main__, and the modules'
    # cases import it again as pipeline_bridge: two copies, two FakeSlack classes. The
    # cases run in the second, the one the modules import (KIT-312).
    check("the-cases-run-in-the-module-the-split-modules-import",
          sys.modules.get("pipeline_bridge") is sys.modules[__name__], True)

    # Every message a case posts, as the pass hands it to post(), for the bind pins at the
    # end of the cases (KIT-342). Put back once they have run.
    bind_log, pass_post = [], Pass.post

    def recording_post(self, channel, thread_ts, text, kind="", bind="", facts=None,
                       join="\n\n", plain=None):
        # Kept once posted: a post the door refused said nothing (never_mention_chat_bot).
        posted_ts = pass_post(self, channel, thread_ts, text, kind=kind, bind=bind,
                              facts=facts, join=join, plain=plain)
        bind_log.append((kind, text, bind, plain))
        return posted_ts
    Pass.post = recording_post

    with tempfile.TemporaryDirectory() as tmp:
        kit = Kit(check, tmp)
        cfg_for, now = kit.take("cfg_for", "now")

        # The job builds Slack's client from its own validated config on every pass: the
        # real client, from cfg_for, with only the transport's HTTP call stood in for.
        sent = []

        def slack_answers(url, headers, payload=None, timeout=20):
            sent.append((url.split("?")[0], headers.get("Authorization")))
            return {"ok": True, "user_id": ME, "team_id": TEAM, "bot_id": "B0BRIDGE01"}
        guard = transport._http_json
        transport._http_json = slack_answers
        try:
            real_slack = SlackClient(cfg_for("real-client"), "xoxb-test")
            who = real_slack.auth_test()
        finally:
            transport._http_json = guard
        check("the-job-builds-slacks-client-from-its-own-config",
              (real_slack.base, sent, who.get("user_id")),
              ("https://slack.com/api", [("https://slack.com/api/auth.test",
                                          "Bearer xoxb-test")], ME))
        # The job's own construction, end to end: run_command builds both clients from the
        # two credentials its config names, each going only where it belongs. A swap would
        # send the tracker key to Slack (review of KIT-388).
        job_cfg = cfg_for("job-construction")
        heard = []

        def both_answer(url, headers, payload=None, timeout=20):
            heard.append((url.split("?")[0], headers.get("Authorization")))
            if url.startswith("https://slack.com/api/auth.test"):
                return {"ok": True, "user_id": ME, "team_id": TEAM, "bot_id": "B0BRIDGE01"}
            return {"data": {}}
        saved_env = dict((k, os.environ.get(k)) for k in (job_cfg["slack_token_env"],
                                                          job_cfg["linear_key_env"]))
        guard_transport, guard_bridge = transport._http_json, tracker_module._http_json
        os.environ[job_cfg["slack_token_env"]] = "slack-token-for-the-job"
        os.environ[job_cfg["linear_key_env"]] = "lin-key-for-the-job"
        transport._http_json = tracker_module._http_json = both_answer
        try:
            run_command(job_cfg, True, 30, out=io.StringIO())
        finally:
            transport._http_json, tracker_module._http_json = guard_transport, guard_bridge
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        check("the-job-sends-each-credential-only-where-it-belongs",
              heard[:2], [("https://slack.com/api/auth.test", "Bearer slack-token-for-the-job"),
                          (TRACKER_API, "lin-key-for-the-job")])

        posted, run, status_of, world = kit.take("posted", "run", "status_of", "world")

        # 1. FIRST PASS: everything already in the window is seen, and nothing is asked.
        cfg, slack, tracker = world("first")
        slack.add(ALICE, "plan PROD-5")
        code, out = run(cfg, slack, tracker)
        check("first-pass-asks-nothing", (code, slack.posts, "first pass" in out),
              (EXIT_OK, [], True))
        # KIT-291: the first pass checks each HANDOFF_STATES name on each team, in the log
        # alone. A name a team lacks is said once, and the pass still does its work.
        check("first-pass-finds-every-handoff-lane-and-says-nothing-of-it",
              ("HANDOFF_STATES" in out, tracker.board_reads), (False, 1))
        c2, s2, t2 = world("lanes-misspelled", handoff_states=["Needs Aproval"])
        s2.add(ALICE, "plan PROD-5")
        code2, out2 = run(c2, s2, t2)
        later = s2.add(ALICE, "plan PROD-6")
        code3, out3 = run(c2, s2, t2)
        check("first-pass-says-a-handoff-lane-a-team-lacks-once-and-goes-on",
              (code2, out2.count("HANDOFF_STATES: PROD has no state named 'Needs Aproval'"),
               "HANDOFF_STATES" in out3, t2.board_reads, code3, s2.posts[0][1]),
              (EXIT_OK, 1, False, 1, EXIT_OK, later))
        c2, s2, t2 = world("lanes-unread")
        t2.board_raises = BridgeError("the tracker could not be reached (simulated)")
        code2, out2 = run(c2, s2, t2)
        check("first-pass-says-a-board-it-could-not-read-as-not-checked-and-goes-on",
              (code2, "HANDOFF_STATES not checked: PROD's board could not be read" in out2),
              (EXIT_OK, True))
        # Whatever else the check hits is said too, and stops neither the first pass nor
        # `lanes`: a stopped first pass saves nothing, so the next would stop the same way.
        c2, s2, t2 = world("lanes-odd")
        t2.board_raises = ValueError("an answer of an odd shape")
        try:
            code2, out2 = run(c2, s2, t2)
        except Exception as exc:                                   # noqa: BLE001
            code2, out2 = "raised %s" % type(exc).__name__, ""
        odd = io.StringIO()
        try:
            odd_rc = lanes_command(c2, tracker=t2, out=odd)
        except Exception as exc:                                   # noqa: BLE001
            odd_rc = "raised %s" % type(exc).__name__
        check("the-check-goes-on-whatever-it-hits-and-says-so",
              (code2, "HANDOFF_STATES not checked: the check could not run (ValueError: an "
                      "answer of an odd shape)" in out2,
               odd_rc, (odd.getvalue().splitlines() or [""])[-1]),
              (EXIT_OK, True, EXIT_ERROR, "lanes: not checked: the check could not run "
                                          "(ValueError: an answer of an odd shape)"))

        def lanes(name, setup=None, **over):
            c_, _s, t_ = world(name, **over)
            if setup:
                setup(t_)
            buf = io.StringIO()
            return lanes_command(c_, tracker=t_, out=buf), buf.getvalue().splitlines()[-1]

        def crowded(t_):
            for n in range(TEAM_STATES_READ):
                t_.team_states[("PROD", "Lane %d" % n)] = "st-lane-%d" % n
            del t_.team_states[("PROD", "Needs Approval")]

        def unread(t_):
            t_.board_raises = BridgeError("the tracker could not be reached (simulated)")
        check("lanes-says-ok-missing-or-not-checked-and-never-ok-for-a-board-it-did-not-read",
              [lanes("lanes-ok"),
               lanes("lanes-two", handoff_states=["Needs Approval", "Ready to merge"]),
               lanes("lanes-gone-team", team_keys=["PROD", "GONE"]),
               lanes("lanes-unread-cmd", unread),
               lanes("lanes-crowded", crowded)],
              [(EXIT_OK, "lanes: ok: every HANDOFF_STATES name (Needs Approval) is a state "
                         "on every team (PROD)"),
               (EXIT_DECLINED, "lanes: missing: PROD has no state named 'Ready to merge'"),
               (EXIT_DECLINED, "lanes: missing: no team has the key GONE"),
               (EXIT_ERROR, "lanes: not checked: PROD's board could not be read (the "
                            "tracker could not be reached (simulated))"),
               (EXIT_ERROR, "lanes: not checked: PROD has more states than one read holds, "
                            "and none named 'Needs Approval' among them")])
        # A name missing on one team blocks, whatever another team's board left unread; the
        # bound is the planner's own team query; and the key is never printed.
        key_env = world("lanes-key")[0]["linear_key_env"]
        old_key = os.environ.get(key_env)
        os.environ[key_env] = "lin_api_" + "z" * 40
        try:
            leaked = lanes("lanes-leak", lambda t_: setattr(t_, "board_raises", BridgeError(
                "the tracker refused lin_api_" + "z" * 40)))
        finally:
            if old_key is None:
                os.environ.pop(key_env, None)
            else:
                os.environ[key_env] = old_key
        check("lanes-a-missing-name-blocks-beside-an-unread-board-and-the-key-is-never-said",
              (lanes("lanes-both", crowded, team_keys=["PROD", "GONE"]),
               "states(first: %d)" % TEAM_STATES_READ in poller.Q_TEAM,
               leaked[0], "z" * 40 in leaked[1]),
              ((EXIT_DECLINED, "lanes: missing: no team has the key GONE; not read: PROD has "
                               "more states than one read holds, and none named 'Needs "
                               "Approval' among them"), True, EXIT_ERROR, False))
        # ... and the second pass hears a NEW request, and asks in its thread.
        req = slack.add(ALICE, "plan prod-6")
        code, out = run(cfg, slack, tracker)
        check("request-asks-in-its-thread", (code, len(slack.posts), slack.posts[0][1]),
              (EXIT_OK, 1, req))
        q = posted(slack)[0]
        check("question-carries-tracker-facts",
              ("PROD-6" in q, "Light mode" in q, "Backlog" in q, "Chat bot" in q,
               "Reply `yes`" in q, "<@%s>" % ALICE in q), (True,) * 6)
        state = json.load(open(state_path(cfg)))
        check("question-recorded", [(v["ticket"], v["status"], v["thread"])
                                    for v in state["questions"].values()],
              [("PROD-6", OPEN, req)])
        # The same pass again: nothing new, nothing asked twice.
        run(cfg, slack, tracker)
        check("request-handled-once", len(slack.posts), 1)

        # 2. A VERIFIED MEMBER'S YES confirms — and with acting off changes nothing.
        slack.add(BOB, "Yes.", thread=req)
        code, out = run(cfg, slack, tracker)
        check("yes-confirms", (code, "Confirmed by <@%s>" % BOB in posted(slack)[-1],
                               "was not changed" in posted(slack)[-1]), (EXIT_OK, True, True))
        state = json.load(open(state_path(cfg)))
        check("yes-recorded", [(v["status"], v.get("answered_by"))
                               for v in state["questions"].values()], [(CONFIRMED, BOB)])
        slack.add(ALICE, "yes", thread=req)
        run(cfg, slack, tracker)
        check("second-yes-does-nothing", sum("Confirmed" in t for t in posted(slack)), 1)

        # 3. EVERY SENDER THAT IS NOT A VERIFIED FULL MEMBER IS REFUSED, and says why.
        def refused_by(name, msg_user, extra=None, allowed=None, text="yes"):
            c, s, t = world(name, **({"allowed_slack_users": allowed} if allowed else {}))
            run(c, s, t)                                   # the first pass, empty
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            s.add(msg_user, text, thread=root, **(extra or {}))
            code, _o = run(c, s, t)
            st = json.load(open(state_path(c)))
            return code, [v["status"] for v in st["questions"].values()], posted(s)[-1]

        for name, user, extra, needle in (
                ("guest", GUEST, None, "guest"),
                ("deactivated", GONE, None, "deactivated"),
                ("bot-account", CHATBOT, None, "bot or an app"),
                ("other-workspace-account", OTHERWS, None, "another Slack workspace"),
                ("bot-post", ALICE, {"bot_id": "B0OTHER001"}, "bot post"),
                ("subtype", ALICE, {"subtype": "thread_broadcast"}, "thread_broadcast"),
                ("edited", ALICE, {"edited": {"user": ALICE, "ts": "1"}}, "edited"),
                ("file", ALICE, {"files": [{"id": "F1"}]}, "file"),
                ("other-workspace-message", ALICE, {"user_team": "T0OTHER000"},
                 "another Slack workspace"),
                ("other-workspace-team-field", ALICE, {"team": "T0OTHER000"},
                 "another Slack workspace"),
                ("other-workspace-source-team", ALICE, {"source_team": "T0OTHER000"},
                 "another Slack workspace"),
                ("app-user", APPUSER, None, "bot or an app"),
                ("ultra-restricted-guest", UGUEST, None, "guest"),
                ("no-workspace-named", NOTEAM, None, "no workspace"),
                ("no-sender", None, None, "no sender")):
            code, statuses, last = refused_by("refuse-" + name, user, extra)
            check("refused:" + name, (code, statuses, needle in last), (EXIT_OK, [OPEN], True))
        code, statuses, last = refused_by("refuse-not-listed", BOB, allowed=[ALICE])
        check("refused:not-on-the-list", (statuses, "list of members" in last),
              ([OPEN], True))
        code, statuses, last = refused_by("allowed-listed", ALICE, allowed=[ALICE])
        check("allowed-member-confirms", statuses, [CONFIRMED])

        # 4. A yes binds to a question THIS job asked, in its thread, after it.
        c, s, t = world("binding")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        other = s.add(ALICE, "something else entirely")
        s.add(ALICE, "yes", thread=other)                   # another thread: nothing
        s.add(ALICE, "yes")                                 # top level: nothing
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("yes-elsewhere-is-nothing", ([v["status"] for v in st["questions"].values()],
                                           sum("Confirmed" in x for x in posted(s))),
              ([OPEN], 0))
        # After the question exists: a yes in ANOTHER thread still binds nothing, and a yes
        # that Slack shows with a timestamp older than the question (late, or forged order)
        # is not an answer to it.
        s.add(BOB, "yes", thread=other)
        q_ts = [v["ts"] for v in json.load(open(state_path(c)))["questions"].values()][0]
        s.messages.append({"ts": "%.6f" % (float(q_ts) - 0.5), "thread_ts": root,
                           "user": BOB, "text": "yes"})
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("yes-elsewhere-or-earlier-binds-nothing",
              ([v["status"] for v in st["questions"].values()],
               sum("Confirmed" in x for x in posted(s))), ([OPEN], 0))
        # A fake "question" posted by another bot in the thread is not one of ours.
        s.add(CHATBOT, "*Plan this?* PROD-6. Reply yes.", thread=root, bot_id="B0CHATBOT1")
        s.add(ALICE, "yes PROD-6", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("a-forged-question-binds-nothing",
              sorted((v["ticket"], v["status"]) for v in st["questions"].values()),
              [("PROD-5", OPEN)])
        # Two open questions in one thread: a bare yes is refused, a named one binds.
        s.add(ALICE, "plan PROD-6", thread=root)
        run(c, s, t)
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("two-open-bare-yes-asks-which", "2 questions are open" in posted(s)[-1], True)
        s.add(BOB, "yes prod-6", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("named-yes-binds-that-one",
              sorted((v["ticket"], v["status"]) for v in st["questions"].values()),
              [("PROD-5", OPEN), ("PROD-6", CONFIRMED)])
        s.add(BOB, "no", thread=root)
        run(c, s, t)
        check("a-bot-posted-after-the-question-so-a-bare-answer-is-refused",
              "Reply `no PROD-5`" in posted(s)[-1], True)
        s.add(BOB, "no prod-5", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("no-declines", sorted(v["status"] for v in st["questions"].values()),
              [CONFIRMED, DECLINED])

        # 5. REQUESTS that cannot become a question are refused, said once.
        c, s, t = world("requests")
        run(c, s, t)
        s.add(ALICE, "plan PROD-404")
        s.add(ALICE, "plan OPS-1")
        s.add(CHATBOT, "<@%s> plan PROD-5" % ME, bot_id="B0CHATBOT1")   # the chat bot asks
        s.add(ALICE, "plan PROD-5")                                       # a duplicate
        s.add(ALICE, "please plan PROD-6 now")                           # not the grammar
        run(c, s, t)
        texts = posted(s)
        check("request-unknown-ticket", any("can't find PROD-404" in x for x in texts), True)
        check("request-other-team", any("OPS-1 is on OPS" in x for x in texts), True)
        check("request-from-the-chat-bot-asks", any("*Plan this?* PROD-5" in x for x in texts),
              True)
        check("request-duplicate-refused", any("already has an open question" in x
                                               for x in texts), True)
        check("request-grammar-is-strict", any("PROD-6" in x for x in texts), False)
        check("question-title-made-inert", any("<!channel>" in x for x in texts), False)

        # 6. EXPIRY: a yes after the window is refused; an open question expires anyway.
        c, s, t = world("expiry", question_ttl_hours=1)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root, at=now + 2 * 3600)
        run(c, s, t, at=now + 2 * 3600 + 60)
        st = json.load(open(state_path(c)))
        check("late-yes-expired", ([v["status"] for v in st["questions"].values()],
                                   "expired" in posted(s)[-1]), ([EXPIRED], True))
        c, s, t = world("in-time-but-late", question_ttl_hours=1)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root, at=now + 1800)       # sent in time, Mac asleep
        run(c, s, t, at=now + 7200)                          # handled two hours on
        check("a-yes-sent-in-time-counts-when-handled-late", "Confirmed" in posted(s)[-1], True)
        c, s, t = world("expiry-quiet", question_ttl_hours=1)
        run(c, s, t)
        s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        run(c, s, t, at=now + 2 * 3600)
        st = json.load(open(state_path(c)))
        check("unanswered-question-expires", [v["status"] for v in st["questions"].values()],
              [EXPIRED])

        # 7. RATE LIMIT on questions.
        c, s, t = world("rate", max_questions_per_hour=1)
        run(c, s, t)
        s.add(ALICE, "plan PROD-5")
        s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        check("rate-limit", ("*Plan this?* PROD-5" in posted(s)[0], "my limit" in posted(s)[1]),
              (True, True))

        # 8. CATCH-UP: an open question's thread is read even when its root has left the
        #    window, and a yes sent "while the Mac slept" is heard on the next pass.
        c, s, t = world("catch-up", lookback_days=1)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root, at=now + 79000)    # sent while the Mac slept
        s.reads = []
        run(c, s, t, at=now + 80000)                  # the root is past the 1-day window
        check("catch-up-reads-the-open-thread", ("conversations.replies" in s.reads,
                                                 "Confirmed" in posted(s)[-1]), (True, True))

        # 9. FAILURES are loud, and leave the message for the next pass.
        c, s, t = world("fail-users")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root)
        s.fail = {"users.info"}
        code, out = run(c, s, t)
        check("users-info-failure-exits-1", (code, "users.info" in out), (EXIT_ERROR, True))
        s.fail = set()
        run(c, s, t)
        check("users-info-failure-retried-next-pass", "Confirmed" in posted(s)[-1], True)
        c, s, t = world("fail-history")
        s.fail = {"conversations.history"}
        check("history-failure-exits-1", run(c, s, t)[0], EXIT_ERROR)
        c, s, t = world("fail-post")
        run(c, s, t)
        s.add(ALICE, "plan PROD-5")
        s.fail = {"chat.postMessage"}
        run(c, s, t)
        s.fail = set()
        run(c, s, t)
        check("post-failure-asks-on-the-next-pass",
              sum("*Plan this?* PROD-5" in x for x in posted(s)), 1)
        c, s, t = world("fail-tracker")
        run(c, s, t)
        s.add(ALICE, "plan PROD-5")
        t.fail = True
        check("tracker-failure-exits-1", run(c, s, t)[0], EXIT_ERROR)
        c, s, t = world("not-owner")
        t.viewer = "someone-else"
        check("a-key-not-the-owners-refuses", run(c, s, t)[0], EXIT_USAGE)
        c, s, t = world("corrupt")
        os.makedirs(c["state_dir"], exist_ok=True)
        with open(state_path(c), "w") as fh:
            fh.write("{not json")
        code, out = run(c, s, t)
        check("corrupt-state-refused", (code, "cannot be read" in out, s.posts),
              (EXIT_ERROR, True, []))

        # 10. DRY RUN posts nothing and saves nothing.
        c, s, t = world("dry")
        run(c, s, t)
        before = open(state_path(c)).read()
        s.add(ALICE, "plan PROD-5")
        code, out = run(c, s, t, dry=True)
        check("dry-run-posts-and-saves-nothing",
              (code, s.posts, open(state_path(c)).read() == before, "would post" in out),
              (EXIT_OK, [], True, True))

        # 11. Own posts are never commands.
        c, s, t = world("own")
        run(c, s, t)
        s.add(ME, "plan PROD-5", bot_id=BOT)
        run(c, s, t)
        check("own-posts-ignored", s.posts, [])

        # 11b. REVIEW FIXES (KIT-221 review, 2026-10-10) ------------------------------
        # An edited question is refused and closed.
        c, s, t = world("edited")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        for m in s.messages:
            if m.get("user") == ME and "Plan this?" in m.get("text", ""):
                m["edited"] = {"user": ME, "ts": "1"}
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("edited-question-refused-and-closed",
              ([v["status"] for v in st["questions"].values()], "was changed" in posted(s)[-1]),
              ([EXPIRED], True))
        # The bridge's own account posting something it has no record of is a look-alike.
        c, s, t = world("own-unrecorded")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(ME, "*Plan this?* PROD-6 Light mode", thread=root, bot_id=BOT)
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("an-unrecorded-post-by-the-bridges-own-account-forces-a-named-yes",
              "Reply `yes PROD-5`" in posted(s)[-1], True)
        # A token answering to another bot (the chat bot's, pasted) is refused at once.
        c, s, t = world("wrong-token", bot_user_id="U0OTHERBOT")
        code, out = run(c, s, t)
        check("a-token-that-is-not-the-bridges-is-refused",
              (code, "not the bridge app's bot" in out, s.reads), (EXIT_USAGE, True,
                                                                   ["auth.test"]))
        # Slackbot and app posts are refused.
        for name, extra in (("slackbot", {"user": "USLACKBOT"}), ("app", {"app_id": "A1"})):
            c, s, t = world("refuse-" + name)
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            m = {"ts": "%.6f" % (now - 1), "thread_ts": root, "text": "yes", "user": ALICE}
            m.update(extra)
            s.messages.append(m)
            run(c, s, t)
            check("refused:" + name, "Slackbot or an app" in posted(s)[-1], True)
        # An old message found in a re-read thread is never handled (the window rule).
        c, s, t = world("old-in-thread", lookback_days=1, question_ttl_hours=24)
        run(c, s, t, at=now)
        root = s.add(ALICE, "plan PROD-5", at=now - 100)
        run(c, s, t, at=now)
        s.add(ALICE, "plan PROD-6", thread=root, at=now - 90)      # will age out
        s.reads = []
        run(c, s, t, at=now + 86400 - 50)
        check("a-message-older-than-the-window-is-never-handled",
              any("PROD-6" in x for x in posted(s)), False)
        check("config-refuses-a-question-outliving-the-window", "longer than the window" in
              str(_config_error(dict(EXAMPLE_CONFIG, bot_user_id=ME, owner_user_id=OWNER,
                                     channel_ids=[CHAN], lookback_days=1,
                                     question_ttl_hours=48,
                                     state_dir=os.path.join(tmp, "ttl"))))
              , True)
        # Write-through: a confirmation said before a later failure is never said twice.
        c, s, t = world("write-through")
        run(c, s, t)
        r1 = s.add(ALICE, "plan PROD-5")
        r2 = s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        s.add(BOB, "yes", thread=r1)
        s.add(GONE, "yes", thread=r2)
        real_user = s.user

        def flaky(uid):
            if uid == GONE:
                raise BridgeError("Slack users.info refused: simulated")
            return real_user(uid)
        s.user = flaky
        code, _o = run(c, s, t)
        s.user = real_user
        run(c, s, t)
        check("write-through-says-a-confirmation-once",
              (code, sum("Confirmed by <@%s>" % BOB in x for x in posted(s))), (EXIT_ERROR, 1))
        # Write-through also covers a failure that is not the bridge's own kind.
        c, s, t = world("write-through-crash")
        run(c, s, t)
        r1 = s.add(ALICE, "plan PROD-5")
        r2 = s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        s.add(BOB, "yes", thread=r1)
        s.add(GONE, "yes", thread=r2)

        def crash(uid):
            if uid == GONE:
                raise RuntimeError("simulated crash")
            return real_user(uid)
        real_user = s.user
        s.user = crash
        try:
            run(c, s, t)
        except RuntimeError:
            pass
        s.user = real_user
        run(c, s, t)
        check("write-through-survives-a-crash",
              sum("Confirmed by <@%s>" % BOB in x for x in posted(s)), 1)
        # A message that fails three passes is given up on, said once, and passed over.
        c, s, t = world("give-up")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root)
        s.fail = {"users.info"}
        codes = [run(c, s, t)[0] for _ in range(3)]
        s.fail = set()
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("a-failing-message-is-given-up-on-after-three-passes",
              (codes, any("stopped trying" in x for x in posted(s)),
               [v["status"] for v in st["questions"].values()]),
              ([EXIT_ERROR, EXIT_ERROR, EXIT_OK], True, [OPEN]))
        # Too many open questions; a yes in a thread whose question is closed is answered.
        c, s, t = world("too-many", max_open_questions=1)
        run(c, s, t)
        r1 = s.add(ALICE, "plan PROD-5")
        s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        check("too-many-open-questions", "already open" in posted(s)[-1], True)
        s.add(BOB, "no", thread=r1)
        run(c, s, t)
        s.add(BOB, "yes", thread=r1)
        run(c, s, t)
        check("a-yes-to-a-closed-question-is-answered", "no open question" in posted(s)[-1],
              True)
        # With acting off, the question says nothing moves, in the approve question's words
        # (KIT-262): this version can act, so "moves nothing yet" was wrong.
        _q = question_text(FakeTracker().issues["PROD-5"], ALICE, cfg_for("q"), now)
        check("question-says-acting-is-switched-off",
              ("acting is switched off here, so nothing moves" in _q, "this version" in _q),
              (True, False))

        pipeline_bridge_tracker.cases_errors(kit)

        # The lock: a second pass does nothing, and says so.
        import fcntl
        c, s, t = world("lock")
        os.makedirs(c["state_dir"], exist_ok=True)
        holder = open(lock_path(c), "a")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            out = io.StringIO()
            code = run_command(c, False, 30, slack=s, tracker=t, out=out, now=now)
            check("a-second-pass-holds-off", (code, "another pass holds" in out.getvalue(),
                                              s.reads), (EXIT_DECLINED, True, []))
        finally:
            holder.close()
        # A dry run writes no heartbeat.
        c, s, t = world("dry-beat")
        run_command(c, True, 30, slack=s, tracker=t, out=io.StringIO(), now=now)
        check("dry-run-writes-no-heartbeat", os.path.exists(heartbeat_path(c)), False)
        # The Slack address is pinned to Slack, over https.
        check("config-refuses-a-non-slack-api", "chat_api_base" in str(_config_error(
            dict(EXAMPLE_CONFIG, chat_api_base="http://evil.example/api",
                 state_dir=os.path.join(tmp, "api")))), True)

        # 11c. REVIEW FIXES, second round (KIT-221 review, 2026-10-10) ----------------
        # The host pin, apart from the scheme: https alone is not enough.
        for name, base in (("another-host", "https://evil.example/api"),
                           ("a-slack-look-alike", "https://slack.com.evil.example/api")):
            check("config-refuses-%s-over-https" % name, "chat_api_base" in _config_error(
                dict(EXAMPLE_CONFIG, chat_api_base=base,
                     state_dir=os.path.join(tmp, "api"))), True)
        check("config-takes-slacks-own-api", _config_error(dict(
            EXAMPLE_CONFIG, chat_api_base="https://slack.com/api",
            state_dir=os.path.join(tmp, "api"))), "")

        # The grammar is the whole line: a reply that only STARTS with yes or no, or a
        # request with words after the id, is none of them.
        c, s, t = world("grammar-anchors")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yesterday I said we should wait on this", thread=root)
        run(c, s, t)
        check("grammar:yesterday-is-not-a-yes", status_of(c), [OPEN])
        s.add(BOB, "nobody wants this", thread=root)
        run(c, s, t)
        check("grammar:nobody-is-not-a-no", status_of(c), [OPEN])
        s.add(ALICE, "plan PROD-6 now")
        run(c, s, t)
        check("grammar:plan-with-words-after-the-id-is-not-a-request",
              any("PROD-6" in x for x in posted(s)), False)

        # A bot's request is credited in words, never @mentioned (bot_id or app_id).
        # The chat bot the config names is the tech lead; any other bot is a bot (review of
        # #265: a bot's words are not the tech lead's because it is a bot).
        c, s, t = world("bot-credit", chat_bot_user_id=CHATBOT)
        run(c, s, t)
        s.add(CHATBOT, "plan PROD-5", bot_id="B0CHATBOT1")
        s.add(APPUSER, "plan PROD-6", app_id="A0OTHERAPP")
        run(c, s, t)
        check("a-bots-request-is-credited-in-words-never-mentioned",
              [("Asked for by the tech lead" in x, "Asked for by a bot" in x,
                "<@%s>" % CHATBOT in x or "<@%s>" % APPUSER in x) for x in posted(s)],
              [(True, False, False), (False, True, False)])

        # Every request that reads the tracker counts, found or not; past the limit a
        # request reads nothing, and the limit is said once an hour in the channel.
        c, s, t = world("rate-unknown", max_questions_per_hour=1)
        run(c, s, t)
        for n in range(50):
            s.add(GUEST, "plan PROD-%d" % (9000 + n))
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("unknown-ticket-requests-count-against-the-hourly-limit",
              (t.reads, len(s.posts), "my limit" in posted(s)[-1],
               sum(h["what"].startswith("refused") for h in st["handled"].values())),
              (1, 2, True, 50))
        for n in range(5):
            s.add(GUEST, "plan PROD-%d" % (9100 + n))
        run(c, s, t, at=now + 60)
        check("the-limit-is-said-once-an-hour-in-a-channel", (t.reads, len(s.posts)), (1, 2))

        # A look-alike is anything the bridge cannot vouch for, not only a flagged bot.
        def lookalike(name, poster, extra=None,
                      text="*Plan this?* PROD-6 “Light mode”: in Backlog on PROD. "
                           "Reply `yes` in this thread."):
            c, s, t = world("lookalike-" + name)
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            s.add(poster, text, thread=root, **(extra or {}))
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            return status_of(c), posted(s)[-1]

        for name, poster, extra in (("guest", GUEST, None),
                                    ("slackbot", "USLACKBOT", None),
                                    ("app-id-post", ALICE, {"app_id": "A0LOOKALIKE"}),
                                    ("bot-message-post", ALICE, {"subtype": "bot_message"})):
            sts, last = lookalike(name, poster, extra)
            check("look-alike-by-a-%s-forces-a-named-yes" % name,
                  (sts, "Reply `yes PROD-5`" in last), ([OPEN], True))
        sts, last = lookalike("member", ALICE, text="Sounds right to me.")
        check("a-members-post-after-the-question-is-no-look-alike", sts, [CONFIRMED])

        # A question gone from its thread is closed, never answered.
        c, s, t = world("question-deleted")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.messages = [m for m in s.messages if "Plan this?" not in m.get("text", "")]
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("a-deleted-question-is-closed-not-answered",
              (status_of(c), "no longer in this thread" in posted(s)[-1]), ([EXPIRED], True))

        # A status changes only after the reply saying so is posted: when that post fails,
        # nothing is saved, and the next pass closes the question and says so, once.
        for name, answer, said, final in (("confirm", "yes", "Confirmed by", CONFIRMED),
                                          ("decline", "no", "OK, PROD-5", DECLINED),
                                          ("expire", "yes", "has expired", EXPIRED),
                                          ("edited-close", "yes", "was changed", EXPIRED)):
            c, s, t = world("post-fails-" + name, question_ttl_hours=1)
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            if name == "edited-close":
                for m in s.messages:
                    if "Plan this?" in m.get("text", ""):
                        m["edited"] = {"user": ME, "ts": "1"}
            late = now + 2 * 3600 if name == "expire" else None
            s.add(BOB, answer, thread=root, at=late)
            real_post = s.post

            def failing(channel, thread_ts, text, said=said, real_post=real_post):
                if said in text:
                    raise BridgeError("Slack chat.postMessage failed (simulated)")
                return real_post(channel, thread_ts, text)
            s.post = failing
            code, _o = run(c, s, t, at=late and late + 60)
            saved = status_of(c)
            s.post = real_post
            run(c, s, t, at=late and late + 120)
            check("a-failed-%s-post-saves-nothing-and-the-next-pass-says-it" % name,
                  (code, saved, status_of(c), sum(said in x for x in posted(s))),
                  (EXIT_ERROR, [OPEN], [final], 1))

        # A request posted as a thread reply survives a failed pass, and the deadline.
        c, s, t = world("thread-reply-blip")
        run(c, s, t)
        root = s.add(ALICE, "I want light mode")
        s.add(CHATBOT, "plan PROD-6", thread=root, bot_id="B0CHATBOT1")
        t.fail = True
        code, _o = run(c, s, t)
        t.fail = False
        run(c, s, t)
        check("a-thread-reply-request-is-asked-after-a-tracker-blip",
              (code, sum("*Plan this?* PROD-6" in x for x in posted(s))), (EXIT_ERROR, 1))
        # The first reply is handled with no post and no question, so nothing else would
        # make the next pass read the thread again.
        c, s, t = world("thread-reply-deadline")
        run(c, s, t)
        root = s.add(ALICE, "I want light mode")
        s.add(CHATBOT, "I filed it as PROD-6.", thread=root, bot_id="B0CHATBOT1")
        s.add(CHATBOT, "plan PROD-6", thread=root, bot_id="B0CHATBOT1")
        real_issue = t.issue

        def deadline_on_prod6(ident):
            if ident == "PROD-6":
                raise Deadline()
            return real_issue(ident)
        t.issue = deadline_on_prod6
        code = run_command(c, False, 30, slack=s, tracker=t, out=io.StringIO(), now=now)
        t.issue = real_issue
        run(c, s, t)
        check("a-thread-reply-request-is-asked-after-a-deadline-mid-thread",
              (code, [x.split("“")[0] for x in posted(s)]),
              (EXIT_TIMEOUT, ["*Plan this?* PROD-6 "]))

        # A state file of another schema is refused, like one that cannot be read.
        c, s, t = world("wrong-schema")
        os.makedirs(c["state_dir"], exist_ok=True)
        with open(state_path(c), "w") as fh:
            json.dump(dict(new_state(), schema="pipeline-bridge-state/0"), fh)
        code, out = run(c, s, t)
        check("a-state-file-of-another-schema-is-refused",
              (code, "is not a %s document" % STATE_SCHEMA in out, s.posts),
              (EXIT_ERROR, True, []))

        pipeline_bridge_tracker.cases_real(kit)
        pipeline_bridge_answers.cases_plan(kit)
        pipeline_bridge_answers.cases_reminder(kit)
        pipeline_bridge_plans.cases_approve(kit)
        pipeline_bridge_plans.cases_ready_plans(kit)
        pipeline_bridge_plans.cases_plan_moves(kit)
        pipeline_bridge_pings.cases_pings(kit)
        ping_text = kit.ping_text
        pipeline_bridge_config.cases_config(kit)

        # 13. The heartbeat and the command wrapper: a credential never reaches an output.
        c, s, t = world("heartbeat")
        os.environ["BRIDGE_SLACK_BOT_TOKEN"] = "xoxb-" + "1" * 30
        os.environ["STAGE_E_LINEAR_API_KEY"] = "lin_api_" + "2" * 30
        try:
            s.add(ALICE, "plan PROD-5")
            run(c, s, t)                                         # first pass: baseline
            s.add(ALICE, "plan PROD-6")

            def leaky(_ident):
                # Padded so the key straddles where the state record is cut short.
                raise BridgeError("the tracker echoed %s Authorization: %s"
                                  % ("x" * 90, os.environ["STAGE_E_LINEAR_API_KEY"]))
            t.issue = leaky
            out = io.StringIO()
            code = run_command(c, False, 30, slack=s, tracker=t, out=out, now=now)
            beat_text = open(heartbeat_path(c)).read()
            beat = json.loads(beat_text)
            check("heartbeat-on-failure", (code, beat["exit"], beat["schema"]),
                  (EXIT_ERROR, EXIT_ERROR, HEARTBEAT_SCHEMA))
            check("credential-redacted-everywhere",
                  ("2" * 30 in out.getvalue() + beat_text, "redacted" in beat_text),
                  (False, True))
            # ... and on the give-up path, the third failed pass: the log line, the state
            # file and the heartbeat. The state file is mode 600.
            logs = []
            for _ in range(2):
                out = io.StringIO()
                logs.append((run_command(c, False, 30, slack=s, tracker=t, out=out,
                                         now=now), out.getvalue()))
            state_text = open(state_path(c)).read()
            written = "".join(l for _c, l in logs) + state_text + open(heartbeat_path(c)).read()
            check("credential-redacted-on-the-give-up-path",
                  ([code for code, _l in logs], "gave up" in logs[-1][1],
                   os.environ["STAGE_E_LINEAR_API_KEY"][:16] in written,
                   "redacted" in state_text, "redacted" in logs[-1][1]),
                  ([EXIT_ERROR, EXIT_OK], True, False, True, True))
            check("state-file-is-mode-600", oct(os.stat(state_path(c)).st_mode & 0o777),
                  oct(0o600))
            # A Slack post is redacted too: a key pasted into a ticket's title never posts.
            c2, s2, t2 = world("redact-post")
            run(c2, s2, t2)
            t2.issues["PROD-7"] = dict(t2.issues["PROD-6"], identifier="PROD-7", title=(
                "key %s pasted" % os.environ["STAGE_E_LINEAR_API_KEY"]))
            s2.add(ALICE, "plan PROD-7")
            run(c2, s2, t2)
            check("credential-redacted-in-a-slack-post",
                  ("2" * 30 in posted(s2)[-1], "redacted" in posted(s2)[-1]), (False, True))
        finally:
            os.environ.pop("BRIDGE_SLACK_BOT_TOKEN", None)
            os.environ.pop("STAGE_E_LINEAR_API_KEY", None)
        check("missing-credential-is-usage", run_command(c, False, 30, out=io.StringIO()),
              EXIT_USAGE)

        pipeline_bridge_watch.cases_watch(kit)
        standup.cases_bridge(kit)
        pipeline_bridge_direct.cases_direct(kit)
        beat, nworld, outcome = kit.take("beat", "nworld", "outcome")

        # 15. ONE DM (KIT-295). With dm_user_ids set, each listed member's DM with the bridge
        #     app is read as a channel is, and everything posted there is a message of its
        #     own, so it notifies. Commands and yes or no work there with no thread; a bare
        #     yes binds only when one question is open in that DM, after every check a
        #     channel makes. Each case runs alone, so one that raises fails as itself.
        dm_help = kit.dm_help
        unrecorded_ping = ("I can't pass that on to a dev: I have no record of this message, "
                           "so I can't tell which dev is waiting. Messages sent before "
                           "BRIDGE_PINGS=yes have no record, and neither does one the notifier "
                           "failed to record (it sends that one again). Answer under the newer "
                           "message, or in Linear.")

        dmworld, in_dm = kit.take("dmworld", "in_dm")

        def ledger(c, *pages, **kw):
            """What the notifier writes for the pages it posts into the DM (KIT-296)."""
            os.makedirs(os.path.dirname(c["notifier_posts_file"]), exist_ok=True)
            with open(c["notifier_posts_file"], "w") as fh:
                fh.write(kw.get("raw") or json.dumps({
                    "schema": "pipeline-notifier-dm-posts/1",
                    "posts": dict((key_of(DM, ts), {"channel": DM, "ts": ts,
                                                    "mark": kw.get("mark", "ready-to-merge")})
                                  for ts in pages)}))

        def pinged(c, page, channel=DM):
            """What the notifier writes for an agent:blocked ping it can relay (KIT-224)."""
            os.makedirs(os.path.dirname(c["pings_file"]), exist_ok=True)
            with open(c["pings_file"], "w") as fh:
                json.dump({"schema": notify.BRIDGE_PINGS_SCHEMA, "messages": {
                    notify.chat_message_key(channel, page): {
                        "channel": channel, "ts": page, "ticket_id": "TOD-5",
                        "thread_id": "root-5", "event_key": "comment:m5",
                        "comment_id": "m5", "mark": "agent:blocked"}}}, fh)

        def asked_in_dm(name, *requests, **over):
            c, s, t = dmworld(name, **over)
            for line in requests:
                s.add(ALICE, line, channel=DM)
            run(c, s, t)
            return c, s, t

        def case_opened():
            c, s, t = world("dm-open", dm_user_ids=[ALICE])
            run(c, s, t)
            run(c, s, t)
            return (s.reads.count("conversations.open"),
                    json.load(open(state_path(c))).get("dms"))
        check("dm-opened-and-cached", outcome(case_opened), (1, {ALICE: DM}))

        # DMs turned on for a bridge that has run before: the first pass that reads the DM
        # is a baseline for it, so days-old replies and answers there are passed over. A
        # pass that stops before the DM is read keeps no record of it, so the next pass
        # still baselines it.
        def case_turned_on(stops):
            over = dict(act=True, agent_user_name="Agent", team_keys=["PROD", "TOD"])
            c, s, t = world("dm-turned-on-%s" % stops, **over)
            run(c, s, t)                                    # channels only, for a while
            page = s.add(ME, ping_text(), channel=DM, bot_id=BOT, at=now - 3 * 86400)
            s.add(ALICE, "use the old helper", thread=page, channel=DM, at=now - 3 * 86400 + 60)
            s.add(ALICE, "no", channel=DM, at=now - 3 * 86400 + 120)
            s.add(ALICE, "plan PROD-6", channel=DM, at=now - 3 * 86400 + 180)
            c = cfg_for("dm-turned-on-%s" % stops, dm_user_ids=[ALICE], **over)
            pinged(c, page)
            ledger(c, page, mark="agent:blocked")
            if stops:
                history = s.history

                def dm_refused(channel, oldest, pages):
                    if channel == DM:
                        raise BridgeError("Slack conversations.history refused: simulated")
                    return history(channel, oldest, pages)
                s.history = dm_refused
                stopped = run(c, s, t)[0]
                s.history = history
            code, out = run(c, s, t)
            first = (code, list(s.posts), t.thread_replies, "first read of the DM" in out,
                     json.load(open(state_path(c))).get("dms"))
            s.add(ALICE, "plan PROD-5", channel=DM)
            run(c, s, t)
            return ((stopped,) if stops else ()) + first + (len(in_dm(s)),)
        check("dm-first-read-is-a-baseline", outcome(lambda: case_turned_on(False)),
              (EXIT_OK, [], [], True, {ALICE: DM}, 1))
        check("dm-first-read-is-a-baseline: a pass that stops first keeps no record of it",
              outcome(lambda: case_turned_on(True)),
              (EXIT_ERROR, EXIT_OK, [], [], True, {ALICE: DM}, 1))

        # KIT-297 review: the refused DM is not read, and the channels still are, once each.
        def case_not_a_dm():
            c, s, t = world("dm-not-a-dm", dm_user_ids=[ALICE])
            s.dm_ids[ALICE] = CHAN
            read, history = [], s.history

            def reading(channel, oldest, pages):
                read.append(channel)
                return history(channel, oldest, pages)
            s.history = reading
            code, out = run(c, s, t)
            return (code, "named no DM" in out, read,
                    json.load(open(state_path(c))).get("dms"))
        check("dm-an-open-that-names-no-dm-is-refused-and-reads-no-dm",
              outcome(case_not_a_dm), (EXIT_ERROR, True, [CHAN], {}))

        def case_plan():
            c, s, t = asked_in_dm("dm-plan", "plan PROD-6")
            qs = json.load(open(state_path(c)))["questions"].values()
            return ([(ch, th) for ch, th, _x in s.posts],
                    [(q["channel"], q["thread"], q["status"]) for q in qs],
                    "Plan this?" in posted(s)[-1])
        check("dm-plan-asks-top-level", outcome(case_plan),
              ([(DM, None)], [(DM, None, OPEN)], True))

        def case_here():
            _c, s, _t = asked_in_dm("dm-here", "plan PROD-6")
            return "Reply `yes` here within" in posted(s)[-1], "thread" in posted(s)[-1]
        check("dm-question-says-here", outcome(case_here), (True, False))

        def case_yes(act):
            c, s, t = asked_in_dm("dm-yes-%s" % act, "plan PROD-6", act=act)
            s.add(ALICE, "yes", channel=DM)
            code, _o = run(c, s, t)
            return code, status_of(c), t.moves, in_dm(s)[-1]
        check("dm-bare-yes-with-one-open-acts", outcome(lambda: case_yes(True)),
              (EXIT_OK, [DONE], [("uuid-6", "st-plan-it")],
               (None, "Moved PROD-6 to Plan it for <@%s>. The planner starts on it within a "
                      "few minutes." % ALICE)))
        check("dm-bare-yes-with-one-open-acts: with acting off it confirms",
              outcome(lambda: case_yes(False)),
              (EXIT_OK, [CONFIRMED], [],
               (None, "Confirmed by <@%s>. Acting is switched off here, so PROD-6 was not "
                      "changed." % ALICE)))

        def case_two():
            c, s, t = asked_in_dm("dm-two", "plan PROD-5", "plan PROD-6")
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            bare = (status_of(c), in_dm(s)[-1])
            s.add(ALICE, "yes prod-6", channel=DM)
            run(c, s, t)
            qs = json.load(open(state_path(c)))["questions"].values()
            return bare, sorted((q["ticket"], q["status"]) for q in qs)
        two = outcome(case_two)
        check("dm-two-open-asks-for-id", two[0] if isinstance(two, tuple) else two,
              ([OPEN, OPEN], (None, "2 questions are open here. Reply `yes <ticket id>` to "
                                    "say which.")))
        check("dm-yes-with-id-picks-one", two[1] if isinstance(two, tuple) else two,
              [("PROD-5", OPEN), ("PROD-6", CONFIRMED)])

        def case_own_post(recorded):
            c, s, t = asked_in_dm("dm-own-post-%s" % recorded, "plan PROD-6")
            page = s.add(ME, "*A pull request is ready for you to merge on PROD-9*",
                         channel=DM, bot_id=BOT)
            if recorded:
                ledger(c, page)
            s.add(ALICE, "yes", channel=DM)
            code, _o = run(c, s, t)
            return code, status_of(c), in_dm(s)[-1]
        check("dm-unrecorded-own-post-refuses-bare-yes", outcome(lambda: case_own_post(False)),
              (EXIT_OK, [OPEN], (None, "Something other than me posted here after my "
                                       "question, so I can't be sure which question you are "
                                       "answering. Reply `yes PROD-6` to answer the one "
                                       "about PROD-6.")))
        check("dm-ledger-recorded-notifier-post-allows-bare-yes",
              outcome(lambda: case_own_post(True)),
              (EXIT_OK, [CONFIRMED], (None, "Confirmed by <@%s>. Acting is switched off "
                                            "here, so PROD-6 was not changed." % ALICE)))

        # A recorded page that asks the member something is a real question in the same
        # place: a bare yes after it may be meant for it, so it acts on nothing. Asking by
        # its mark, or held in the pings file; a record with no mark is not vouched for.
        def case_asking_page(mark, in_pings):
            c, s, t = asked_in_dm("dm-asking-%s-%s" % (mark, in_pings), "plan PROD-6",
                                  act=True)
            page = s.add(ME, ping_text(), channel=DM, bot_id=BOT)
            ledger(c, page, mark=mark)
            if in_pings:
                pinged(c, page)
            s.add(ALICE, "yes", channel=DM)
            code, _o = run(c, s, t)
            return (code, status_of(c), t.moves,
                    in_dm(s)[-1][1].startswith("Something other than me posted here"))
        check("dm-a-page-asking-something-is-not-vouched-for",
              [outcome(lambda: case_asking_page(mark, in_pings)) for mark, in_pings in (
                  ("agent:blocked", True), ("agent:blocked", False),
                  ("planning-needs-input", False), ("epic-awaiting-approval", False),
                  ("ready-to-merge", True), (None, False))],
              [(EXIT_OK, [OPEN], [], True)] * 6)

        # The ledger vouches only for its own DM: a record keyed in a channel, or one whose
        # channel is not the DM it is keyed in, vouches for nothing.
        def case_ledger_elsewhere(home):
            c, s, t = dmworld("dm-ledger-" + home)
            req = s.add(ALICE, "plan PROD-6", channel=home)
            run(c, s, t)
            thread = req if home == CHAN else None
            look = s.add(ME, "*Plan this?* PROD-7", thread=thread, channel=home, bot_id=BOT)
            ledger(c, raw=json.dumps({"schema": "pipeline-notifier-dm-posts/1", "posts": {
                key_of(home, look): {"channel": CHAN, "ts": look, "mark": "ready-to-merge"}}}))
            s.add(ALICE, "yes", thread=thread, channel=home)
            code, _o = run(c, s, t)
            return code, status_of(c), posted(s)[-1].startswith("Something other than me")
        check("dm-the-ledger-vouches-for-nothing-outside-its-own-dm",
              [outcome(lambda: case_ledger_elsewhere(home)) for home in (CHAN, DM)],
              [(EXIT_OK, [OPEN], True)] * 2)

        def case_corrupt(raw):
            c, s, t = asked_in_dm("dm-corrupt-%d" % len(raw), "plan PROD-6")
            ledger(c, raw=raw)
            s.add(ALICE, "yes", channel=DM)
            before = len(s.posts)
            code, out = run(c, s, t)
            return code, len(s.posts) - before, status_of(c), "Nothing was done" in out
        check("dm-corrupt-ledger-refuses-pass",
              [outcome(lambda: case_corrupt(raw)) for raw in (
                  "{not json", json.dumps({"schema": "pipeline-notifier-dm-posts/0",
                                           "posts": {}}),
                  json.dumps({"schema": "pipeline-notifier-dm-posts/1", "posts": []}))],
              [(EXIT_ERROR, 0, [OPEN], True)] * 3)

        def case_closed(how):
            c, s, t = asked_in_dm("dm-question-" + how, "plan PROD-6")
            for m in list(s.messages):
                if _chan(m) == DM and m.get("user") == ME and "Plan this?" in m["text"]:
                    if how == "edited":
                        m["edited"] = {"user": ME, "ts": "1"}
                    else:
                        s.messages.remove(m)
            s.add(ALICE, "yes", channel=DM)
            code, _o = run(c, s, t)
            return code, status_of(c), in_dm(s)[-1]
        check("dm-edited-question-closes", outcome(lambda: case_closed("edited")),
              (EXIT_OK, [EXPIRED], (None, "My question about PROD-6 was changed after I "
                                          "asked it, so I have closed it. Ask again with "
                                          "`plan PROD-6`.")))
        check("dm-deleted-question-closes", outcome(lambda: case_closed("deleted")),
              (EXIT_OK, [EXPIRED], (None, "My question about PROD-6 is no longer here, so I "
                                          "have closed it. Ask again with `plan PROD-6`.")))

        def case_guest():
            guest_dm = "D" + GUEST[1:]
            c, s, t = world("dm-guest", dm_user_ids=[GUEST])
            run(c, s, t)
            s.add(GUEST, "plan PROD-6", channel=guest_dm)
            run(c, s, t)
            s.add(GUEST, "yes", channel=guest_dm)
            code, _o = run(c, s, t)
            th, last = in_dm(s, guest_dm)[-1]
            return code, status_of(c), th, "guest" in last
        check("dm-guest-answer-refused", outcome(case_guest), (EXIT_OK, [OPEN], None, True))

        def case_bot():
            c, s, t = asked_in_dm("dm-bot", "plan PROD-6")
            s.add(CHATBOT, "yes", channel=DM, bot_id="B0CHATBOT1")
            code, _o = run(c, s, t)
            th, last = in_dm(s)[-1]
            return code, status_of(c), th, "bot post" in last
        check("dm-bot-yes-refused", outcome(case_bot), (EXIT_OK, [OPEN], None, True))

        def case_thread_yes():
            c, s, t = asked_in_dm("dm-thread-yes", "plan PROD-6")
            q_ts = [q["ts"] for q in json.load(open(state_path(c)))["questions"].values()]
            s.add(ALICE, "yes", thread=q_ts[0], channel=DM)
            code, _o = run(c, s, t)
            return code, status_of(c), in_dm(s)[-1][0]
        check("dm-a-yes-in-the-thread-under-a-question-answers-it", outcome(case_thread_yes),
              (EXIT_OK, [CONFIRMED], None))

        def case_cross():
            c, s, t = dmworld("dm-cross")
            s.add(ALICE, "plan PROD-6")                     # asked in the channel
            run(c, s, t)
            s.add(ALICE, "yes", channel=DM)
            code, _o = run(c, s, t)
            return code, status_of(c), in_dm(s)
        check("dm-a-yes-there-never-answers-a-channels-question", outcome(case_cross),
              (EXIT_OK, [OPEN], [(None, "There is no open question here to answer.")]))

        def case_ping():
            c, s, t = dmworld("dm-ping", act=True, agent_user_name="Agent",
                              team_keys=["PROD", "TOD"])
            page = s.add(ME, ping_text(), channel=DM, bot_id=BOT)
            pinged(c, page)
            ledger(c, page, mark="agent:blocked")
            s.add(ALICE, "use the second helper", thread=page, channel=DM)
            code, _o = run(c, s, t)
            sent = t.thread_replies
            return (code, [(u, th) for u, th, _b in sent],
                    bool(sent) and "their DM" in sent[0][2], in_dm(s))
        check("dm-ping-answered-in-thread-relays", outcome(case_ping),
              (EXIT_OK, [("u-TOD-5", "root-5")], True,
               [(None, "Passed <@%s>'s answer to the TOD-5 dev. It should pick up from there."
                 % ALICE)]))

        def case_unwaited():
            c, s, t = dmworld("dm-unwaited", act=True, agent_user_name="Agent")
            page = s.add(ME, "*A pull request is ready for you to merge on PROD-9*",
                         channel=DM, bot_id=BOT)
            ledger(c, page)
            s.add(ALICE, "thanks, merging now", thread=page, channel=DM)
            code, _o = run(c, s, t)
            return code, t.thread_replies, [(th, "isn't waiting on an answer" in x)
                                            for th, x in in_dm(s)]
        check("dm-a-reply-under-a-page-not-waiting-on-one-is-refused-and-said",
              outcome(case_unwaited), (EXIT_OK, [], [(None, True)]))

        # KIT-332: a reply under a dev's question the pings file does not hold is never told
        # to reply under that same kind of message. With no pings file at all (BRIDGE_PINGS
        # off), it names the setting; with one that misses this page, it says the page was
        # not recorded, as in a channel.
        def case_blocked_unrelayable(name, with_pings):
            c, s, t = dmworld(name, act=True, agent_user_name="Agent", team_keys=["PROD", "TOD"])
            page = s.add(ME, ping_text(), channel=DM, bot_id=BOT)
            ledger(c, page, mark="agent:blocked")
            if with_pings:
                other = s.add(ME, ping_text(), channel=DM, bot_id=BOT)
                pinged(c, other)
            s.add(ALICE, "use the second helper", thread=page, channel=DM)
            code, _o = run(c, s, t)
            said = [x for th, x in in_dm(s) if th is None][-1:]
            return (code, t.thread_replies, said,
                    any("reply under the message saying it stopped" in x for x in said))
        check("dm-a-dev-question-with-answering-off-names-the-setting",
              outcome(lambda: case_blocked_unrelayable("dm-pings-off", False)),
              (EXIT_OK, [], [PINGS_OFF_SAYS + " " + PINGS_OFF_BIND], False))
        check("dm-a-dev-question-the-pings-file-misses-says-it-was-not-recorded",
              outcome(lambda: case_blocked_unrelayable("dm-pings-missed", True)),
              (EXIT_OK, [], [unrecorded_ping], False))

        # A page the notifier posted as this app but failed to record is in neither file:
        # a reply under it reaches no session, and is refused aloud as in a channel.
        def case_unrecorded_page():
            c, s, t = dmworld("dm-unrecorded-page", act=True, agent_user_name="Agent",
                              team_keys=["PROD", "TOD"])
            page = s.add(ME, ping_text(), channel=DM, bot_id=BOT)
            ledger(c)
            s.add(ALICE, "use the second helper", thread=page, channel=DM)
            code, _o = run(c, s, t)
            return code, t.thread_replies, [(th, x == unrecorded_ping) for th, x in in_dm(s)]
        check("dm-a-reply-under-an-unrecorded-post-is-refused-and-said",
              outcome(case_unrecorded_page), (EXIT_OK, [], [(None, True)]))

        # The ledger is read again for each reply, as the pings file is: a page recorded
        # while this pass runs is told apart from one never recorded.
        def case_recorded_mid_pass():
            c, s, t = dmworld("dm-recorded-mid-pass", act=True, agent_user_name="Agent")
            page = s.add(ME, "*A pull request is ready for you to merge on PROD-9*",
                         channel=DM, bot_id=BOT)
            s.add(ALICE, "thanks, merging now", thread=page, channel=DM)
            ledger(c)
            replies = s.replies

            def recorded_now(channel, ts):
                ledger(c, page)                    # the notifier records it, mid-pass
                return replies(channel, ts)
            s.replies = recorded_now
            code, _o = run(c, s, t)
            return code, [(th, "isn't waiting on an answer" in x) for th, x in in_dm(s)]
        check("dm-a-page-recorded-during-the-pass-is-found", outcome(case_recorded_mid_pass),
              (EXIT_OK, [(None, True)]))

        def case_stray():
            c, s, t = dmworld("dm-stray")
            s.add(CHATBOT, "beep", channel=DM, bot_id="B0CHATBOT1")
            s.add(ALICE, "hello there", channel=DM)
            s.add(ALICE, "anyone?", channel=DM)
            run(c, s, t)
            seen = [len(in_dm(s))]
            s.add(ALICE, "still there?", channel=DM)
            run(c, s, t, at=now + 600)
            seen.append(len(in_dm(s)))
            s.add(ALICE, "hi again", channel=DM)
            run(c, s, t, at=now + 3700)
            seen.append(len(in_dm(s)))
            return seen, sorted(set(in_dm(s)))
        check("dm-stray-text-gets-one-line-an-hour", outcome(case_stray),
              ([1, 1, 2], [(None, dm_help)]))

        def case_bot_alone():
            c, s, t = dmworld("dm-bot-alone")
            s.add(CHATBOT, "beep", channel=DM, bot_id="B0CHATBOT1")
            s.add("USLACKBOT", "a reminder", channel=DM)
            s.add(ALICE, "joined", channel=DM, subtype="channel_join")
            code, _o = run(c, s, t)
            return code, in_dm(s)
        check("dm-a-bot-post-alone-gets-no-help-line", outcome(case_bot_alone), (EXIT_OK, []))

        # A yes sent before the question was asked binds to nothing, in that pass or later.
        def case_yes_first():
            c, s, t = dmworld("dm-yes-first", act=True)
            s.add(ALICE, "plan PROD-6", channel=DM)
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            run(c, s, t)
            return status_of(c), t.moves, in_dm(s)[-1]
        check("dm-yes-before-the-question-binds-nothing", outcome(case_yes_first),
              ([OPEN], [], (None, "There is no open question here to answer.")))

        # A yes in a thread under anything but a question answers nothing: under the
        # member's own message, or under a post of this app that neither job recorded.
        def case_other_thread(root):
            c, s, t = asked_in_dm("dm-other-thread-" + root, "plan PROD-6", act=True)
            under = ([m["ts"] for m in s.messages if m.get("text") == "plan PROD-6"][0]
                     if root == "own" else s.add(ME, "*Plan this?* PROD-6", channel=DM,
                                                 bot_id=BOT))
            s.add(ALICE, "yes", thread=under, channel=DM)
            code, _o = run(c, s, t)
            return code, status_of(c), t.moves, in_dm(s)[-1][1]
        check("dm-yes-in-another-thread-binds-nothing",
              [outcome(lambda: case_other_thread(root)) for root in ("own", "unrecorded")],
              [(EXIT_OK, [OPEN], [], "There is no open question in that thread to answer."),
               (EXIT_OK, [OPEN], [], unrecorded_ping)])

        # Only a verified member's reply under a page is told where it went: a guest's or a
        # bot's gets no line, under a recorded page or an unrecorded one.
        def case_guest_under_page(recorded):
            guest_dm = "D" + GUEST[1:]
            c, s, t = world("dm-guest-page-%s" % recorded, dm_user_ids=[GUEST])
            run(c, s, t)
            page = s.add(ME, "*A pull request is ready for you to merge on PROD-9*",
                         channel=guest_dm, bot_id=BOT)
            os.makedirs(os.path.dirname(c["notifier_posts_file"]), exist_ok=True)
            with open(c["notifier_posts_file"], "w") as fh:
                json.dump({"schema": "pipeline-notifier-dm-posts/1", "posts": dict(
                    [(key_of(guest_dm, page), {"channel": guest_dm, "ts": page,
                                               "mark": "ready-to-merge"})]
                    if recorded else [])}, fh)
            s.add(GUEST, "thanks", thread=page, channel=guest_dm)
            s.add(CHATBOT, "noted", thread=page, channel=guest_dm, bot_id="B0CHATBOT1")
            code, _o = run(c, s, t)
            return code, in_dm(s, guest_dm)
        check("dm-guest-reply-under-a-page-gets-no-line",
              [outcome(lambda: case_guest_under_page(r)) for r in (True, False)],
              [(EXIT_OK, [])] * 2)

        def case_epic():
            c, s, t = dmworld("dm-epic", act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            s.add(ALICE, "approve PROD-50", channel=DM)
            run(c, s, t)
            asked = posted(s)[-1]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            rec = json.load(open(state_path(c)))["epics"].get("uuid-50") or {}
            t.set_state("PROD-50", "backlog")
            run(c, s, t)
            return ("Reply `yes` here within" in asked, rec.get("channel"), rec.get("thread"),
                    sorted(set((ch, th) for ch, th, _x in s.posts)),
                    [n for n in ("Approved PROD-50", "Started PROD-51", "paused starting")
                     if not any(n in x for _th, x in in_dm(s))])
        check("dm-epic-notes-top-level", outcome(case_epic),
              (True, DM, None, [(DM, None)], []))

        def case_bad_state():
            c, s, t = world("dm-bad-state", dm_user_ids=[ALICE])
            os.makedirs(c["state_dir"], exist_ok=True)
            with open(state_path(c), "w") as fh:
                json.dump(dict(new_state(), dms=[]), fh)
            code, out = run(c, s, t)
            return code, "record of its DMs" in out, s.posts
        check("dm-a-state-with-no-readable-record-of-its-dms-is-refused",
              outcome(case_bad_state), (EXIT_ERROR, True, []))

        check("config: dm_user_ids must be a list of Slack user ids, each named once",
              outcome(lambda: ["'dm_user_ids'" in _config_error(dict(
                  EXAMPLE_CONFIG, dm_user_ids=v, state_dir=os.path.join(tmp, "dmu%d" % i)))
                  for i, v in enumerate((ALICE, ["alice"], [7], [ALICE, ALICE], "",
                                         [ALICE, BOB], []))]),
              [True, True, True, True, True, False, False])
        check("config: notifier_posts_file is refused inside a worktree, and defaults beside "
              "the state", outcome(lambda: (
                  "'notifier_posts_file'" in _config_error(dict(
                      EXAMPLE_CONFIG, state_dir=os.path.join(tmp, "np-state"),
                      notifier_posts_file=os.path.join(tmp, "in-a-repo", "posts.json"))),
                  cfg_for("np-default")["notifier_posts_file"])),
              (True, os.path.join(os.path.realpath(os.path.join(tmp, "np-default")),
                                  "notifier-dm-posts.json")))

        def case_channel_only():
            c, s, t = world("dm-off")
            run(c, s, t)
            req = s.add(ALICE, "plan PROD-6")
            run(c, s, t)
            return ("conversations.open" in s.reads,
                    [(ch, th == req) for ch, th, _x in s.posts],
                    "Reply `yes` in this thread" in posted(s)[-1],
                    json.load(open(state_path(c))).get("dms"))
        check("dm-off: with dm_user_ids empty a channel works as before, and no DM is opened",
              outcome(case_channel_only), (False, [(CHAN, True)], True, {}))

        # 15b. ONE MEMBER'S DM THAT CANNOT BE OPENED (KIT-297 review) stops no channel and
        #      no other member's DM. Each failure is named, and the pass ends loud: so does a
        #      missing im:write, after the channels are answered.
        def case_one_dm_fails(error, failing):
            c, s, t = world("dm-open-fails-%d" % len(failing), dm_user_ids=[ALICE, BOB])
            opened = s.open_dm

            def open_dm(uid):
                if uid in failing:
                    raise BridgeError(error)
                return opened(uid)
            s.open_dm = open_dm
            run(c, s, t)                                    # the first pass: a baseline
            s.add(ALICE, "plan PROD-6")
            s.add(ALICE, "plan PROD-5", channel=DM)
            code, out = run(c, s, t)
            return (code, sorted(set(ch for ch, _th, x in s.posts if "Plan this?" in x)),
                    [u for u in failing if u in out], json.load(open(state_path(c))).get("dms"))
        check("dm-one-member-whose-dm-cannot-be-opened-stops-no-channel-and-no-other-dm",
              outcome(lambda: case_one_dm_fails("Slack conversations.open refused: "
                                                "user_not_found", [BOB])),
              (EXIT_ERROR, [CHAN, DM], [BOB], {ALICE: DM}))
        scope = ("Slack conversations.open refused: missing_scope. The bridge app needs "
                 "im:write to open a DM")
        got = outcome(lambda: case_one_dm_fails(scope, [ALICE, BOB]))
        check("dm-a-missing-im-write-still-answers-the-channels-then-stops-loud",
              got if not isinstance(got, tuple) else (got[0], got[1], got[2], got[3]),
              (EXIT_ERROR, [CHAN], [ALICE, BOB], {}))

        def case_dm_line():
            c, s, t = world("dm-open-fails-line", dm_user_ids=[ALICE, BOB])
            opened = s.open_dm

            def open_dm(uid):
                if uid == BOB:
                    raise BridgeError("Slack conversations.open refused: user_not_found")
                return opened(uid)
            s.open_dm = open_dm
            return run(c, s, t)[1]
        check("dm-the-failure-line-says-every-channel-was-read",
              "1 member DM(s) could not be opened and were not read; every channel was. "
              "U0BOB00001: Slack conversations.open refused: user_not_found"
              in outcome(case_dm_line), True)

        # 16. THE WRITER (KIT-297). With `writer` on, each message is written when it is
        #     sent, from its plain text and facts, and the sentence saying what to type,
        #     where to answer or what a yes does follows it verbatim: the model never writes
        #     that. A fake writer stands in for the model, so no CLI ever starts here. With
        #     the writer off, or in a dry run, nothing calls it.
        FakeWriter, audited, plain_note = kit.take("FakeWriter", "audited", "plain_note")
        writer_bin, written_as, wrun = kit.take("writer_bin", "written_as", "wrun")
        wworld = kit.wworld

        def wdm(name, fw, *requests, **over):
            c, s, t = wworld(name, dm_user_ids=[ALICE], **over)
            wrun(c, s, t, fw)                               # the first pass: a baseline
            for line in requests:
                s.add(ALICE, line, channel=DM)
            if requests:
                wrun(c, s, t, fw)
            return c, s, t

        plan_bind = ("Reply `yes` in this thread within 24 hours, and I'll only note your "
                     "answer: acting is switched off here, so nothing moves. Reply `no` to "
                     "drop it. Merging stays yours. (Asked for by <@%s>.)" % ALICE)
        plan_body = "PROD-6 “Light mode”: in Backlog on PROD, filed by Chat bot."
        # A message gone out plain while the writer is on is the writer-off one, exactly.
        plan_text = "*Plan this?* " + plan_body + "\n" + plan_bind

        # The question: the writer is given the body, never its head (it asks) nor its
        # terms (the bind). What a yes binds to is unchanged, and a yes still answers it.
        def case_question():
            fw = FakeWriter()
            c, s, t = wworld("w-question")
            wrun(c, s, t, fw)
            req = s.add(ALICE, "plan PROD-6")
            wrun(c, s, t, fw)
            asked = fw.last("question-plan")
            q = list(json.load(open(state_path(c)))["questions"].values())[0]
            s.add(ALICE, "yes", thread=req)
            code, _o = wrun(c, s, t, fw)
            said = fw.last("confirmed-acting-off")
            return (asked["fallback"], asked["keep"], asked["bind"], asked["facts"]["title"],
                    asked["facts"]["plain"] == plan_body, asked["now"], posted(s)[0],
                    q["promised"], code, status_of(c), said["fallback"], posted(s)[-1])
        check("writer-question: the body is written, and the terms follow verbatim",
              outcome(case_question),
              (plan_body, ["PROD-6"], plan_bind, "Light mode", True, now,
               "I have news on PROD-6.\n\n" + plan_bind,
               {"act": False, "question_ttl_hours": 24, "plan_it_state": "Plan it"},
               EXIT_OK, [CONFIRMED],
               "Confirmed by Alice Example. Acting is switched off here, so PROD-6 was not "
               "changed.", "I have news on PROD-6."))

        # An approve question, its yes, and the start that follows: each written; the
        # writer keeps every child id in the order, and sees no ticket's description.
        def case_epic():
            fw = FakeWriter()
            c, s, t = wdm("w-epic", fw, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            s.add(ALICE, "approve PROD-50", channel=DM)
            wrun(c, s, t, fw)
            s.add(ALICE, "yes", channel=DM)
            wrun(c, s, t, fw)
            t.set_state("PROD-50", "backlog")              # a note: paused
            wrun(c, s, t, fw)
            ask, started = fw.last("question-approve"), fw.last("started-child")
            # With acting on, the DM's terms say what a yes starts (review of #265).
            return (fw.kinds(), ask["keep"],
                    ask["bind"] == ("Reply `yes` here within 24 hours, and I'll mark the plan "
                                    "ready and hand its pieces to the agent, at most 1 at a "
                                    "time, each once the pieces it waits on are done. Reply "
                                    "`no` to leave it. Merging stays yours. (Asked for by "
                                    "<@%s>.)" % ALICE),
                    started["facts"]["title"], started["facts"]["epic"],
                    [x for _th, x in in_dm(s)][1:], t.delegations)
        check("writer-approve: the question, the approval, each start and each note are "
              "written", outcome(case_epic),
              (["question-approve", "approve-done", "started-child", "epic-paused"],
               ["PROD-50", "PROD-51", "PROD-52"], True, "Child PROD-51", "PROD-50",
               ["I have news on PROD-50.", "I have news on PROD-51.",
                "I have news on PROD-50."], [("uuid-51", "agent-1")]))

        # What to type next is the bind, after the written text, never in it.
        def case_binds():
            fw = FakeWriter()
            c, s, t = wdm("w-binds", fw, "plan PROD-5", "plan PROD-6")
            s.add(ALICE, "yes", channel=DM)                 # two open: which one
            s.add(ALICE, "plan PROD-6", channel=DM)         # asked already
            wrun(c, s, t, fw)
            return ([(x["kind"], x["fallback"], x["bind"]) for x in fw.calls[2:]],
                    [x for _th, x in in_dm(s)][2:])
        check("writer-binds: which question to answer, and what to do instead",
              outcome(case_binds),
              ([("ambiguous", "2 questions are open here.",
                 "Reply `yes <ticket id>` to say which."),
                ("request-refused", "PROD-6 already has an open question here.",
                 "Answer that one.")],
               ["I have news.\n\nReply `yes <ticket id>` to say which.",
                "I have news on PROD-6.\n\nAnswer that one."]))

        def case_foreign():
            fw = FakeWriter()
            c, s, t = wdm("w-foreign", fw, "plan PROD-6")
            s.add(ME, "*A pull request is ready for you to merge on PROD-9*", channel=DM,
                  bot_id=BOT)
            s.add(ALICE, "yes", channel=DM)
            wrun(c, s, t, fw)
            x = fw.last("foreign-post")
            return x["fallback"], x["keep"], in_dm(s)[-1][1], status_of(c)
        check("writer-foreign-post: the id only the bind names is still kept",
              outcome(case_foreign),
              ("Something other than me posted here after my question about PROD-6, so I "
               "can't be sure which question you are answering.", ["PROD-6"],
               "I have news on PROD-6.\n\nReply `yes PROD-6` to answer the one about PROD-6.",
               [OPEN]))

        def case_limit():
            fw = FakeWriter()
            c, s, t = wdm("w-limit", fw, "plan PROD-5", "plan PROD-6", act=True,
                          max_actions_per_hour=1)
            s.add(ALICE, "yes PROD-5", channel=DM)
            s.add(ALICE, "yes PROD-6", channel=DM)
            wrun(c, s, t, fw)
            moved = fw.last("plan-moved")
            return (moved["fallback"], fw.last("action-limit")["bind"], in_dm(s)[-1][1],
                    t.moves)
        check("writer-acting: a move is written with the member's name, never a mention, and "
              "the limit's 'say yes' is the bind", outcome(case_limit),
              ("Moved PROD-5 to Plan it for Alice Example. The planner starts on it within a "
               "few minutes.", "Say yes again later.", "I have news.\n\nSay yes again later.",
               [("uuid-5", "st-plan-it")]))

        def case_changed():
            fw = FakeWriter()
            c, s, t = wdm("w-changed", fw, "plan PROD-6", act=True)
            t.issues["PROD-6"]["title"] = "Light mode, changed"
            s.add(ALICE, "yes", channel=DM)
            wrun(c, s, t, fw)
            x = fw.last("plan-refused")
            return x["fallback"], x["bind"], in_dm(s)[-1][1], t.moves
        check("writer-refused: 'ask again with' is the bind", outcome(case_changed),
              ("PROD-6 changed since I asked (its title, text, team, labels or state), so "
               "nothing was moved, and I have closed the question.",
               "Ask again with `plan PROD-6`.",
               "I have news on PROD-6.\n\nAsk again with `plan PROD-6`.", []))

        def case_notifier():
            fw = FakeWriter()
            c, s, t = wworld("w-notifier", notifier_plist=os.path.join(tmp, "w-none.plist"))
            wrun(c, s, t, fw)
            x = fw.calls[0] if fw.calls else {}
            return x.get("kind"), x.get("fallback"), x.get("bind"), s.posts
        check("writer-notifier-watch: the command to check it is the bind",
              outcome(case_notifier),
              ("notifier-unreadable", "I can't read the notifier's check-in or its schedule, "
                                      "so I can't tell whether your pages are going out.",
               NOTIFIER_CHECK, [(CHAN, None, "I have news.\n\n" + NOTIFIER_CHECK)]))

        def case_ping(act):
            fw = FakeWriter()
            c, s, t = wdm("w-ping-%s" % act, fw, act=act, agent_user_name="Agent",
                          team_keys=["PROD", "TOD"])
            page = s.add(ME, ping_text(), channel=DM, bot_id=BOT)
            pinged(c, page)
            ledger(c, page, mark="agent:blocked")
            s.add(ALICE, "use the second helper", thread=page, channel=DM)
            wrun(c, s, t, fw)
            x = fw.calls[-1] if fw.calls else {}
            return (x.get("kind"), x.get("fallback"), x.get("bind"), in_dm(s)[-1][1],
                    [b.endswith("\n\nuse the second helper") for _u, _th, b in t.thread_replies])
        check("writer-ping: the answer passed on is the member's own words, and only the line "
              "saying so is written", outcome(lambda: case_ping(True)),
              ("ping-sent", "Passed Alice Example's answer to the TOD-5 dev. It should pick up "
                            "from there.", "", "I have news on TOD-5.", [True]))
        check("writer-ping: where to answer instead is the bind",
              outcome(lambda: case_ping(False)),
              ("ping-refused", "I can't pass that on to the TOD-5 dev: acting is switched "
                               "off here, so it was not sent.", "Answer in Linear.",
               "I have news on TOD-5.\n\nAnswer in Linear.", []))

        # The help line, all instruction, goes out as it is, with no model call. A refusal
        # that says how to answer again is written, and the instruction is its bind (KIT-342).
        def case_verbatim():
            fw = FakeWriter()
            c, s, t = wdm("w-verbatim", fw, "plan PROD-6")
            s.add(ALICE, "yes", channel=DM, edited={"user": ALICE, "ts": "1"})
            s.add(ALICE, "hello there", channel=DM)
            wrun(c, s, t, fw)
            return fw.kinds(), [x for _th, x in in_dm(s)][1:]
        check("writer-verbatim: the help line is never written, and a re-send instruction "
              "follows the words verbatim", outcome(case_verbatim),
              (["question-plan", "answer-refused"],
               ["I have news on PROD-6.\n\nSend the answer again as a new reply.", DM_HELP]))

        # Gone out plain while the writer is on: said once a day, after everything else,
        # recorded only once posted, and counted in the summary and the heartbeat.
        def case_note():
            fw = FakeWriter(by="fallback")
            c, s, t = wdm("w-note", fw, "plan PROD-6")
            first = [x for _th, x in in_dm(s)]
            noted = json.load(open(state_path(c))).get("writer_note_at")
            s.add(ALICE, "plan PROD-6", channel=DM)
            out = io.StringIO()
            code = run_command(c, False, 30, slack=s, tracker=t, out=out, now=now + 60,
                               writer=fw)
            beat = json.load(open(heartbeat_path(c)))
            again = in_dm(s)[-1][1]
            s.add(ALICE, "plan PROD-6", channel=DM, at=now + 86400)
            wrun(c, s, t, fw, at=now + 86400 + 60)
            return (first, noted, code, again, (beat.get("written"), beat.get("fell_back")),
                    "| writer: 0 written, 1 went out plain" in out.getvalue(),
                    in_dm(s)[-1][1].endswith(plain_note))
        check("writer-plain-is-said-once-a-day", outcome(case_note),
              ([plan_text.replace("in this thread", "here") + plain_note],
               now, EXIT_OK, "PROD-6 already has an open question here. Answer that one.",
               (0, 1), True, True))

        def case_never():
            fw = FakeWriter()
            c, s, t = wworld("w-dry")
            wrun(c, s, t, fw)
            s.add(ALICE, "plan PROD-6")
            wrun(c, s, t, fw, dry=True)
            off = FakeWriter()
            c2, s2, t2 = world("w-off")
            wrun(c2, s2, t2, off)
            s2.add(ALICE, "plan PROD-6")
            code, out = wrun(c2, s2, t2, off)
            return (fw.calls, s.posts, off.calls,
                    posted(s2) == [question_text(t2.issues["PROD-6"], ALICE, c2, now)],
                    "writer:" in out)
        check("writer-a-dry-run-or-the-writer-off-calls-nothing", outcome(case_never),
              ([], [], [], True, False))

        def case_raises():
            fw = FakeWriter(raises=RuntimeError("boom"))
            c, s, t = wworld("w-raises")
            wrun(c, s, t, fw)
            s.add(ALICE, "plan PROD-6")
            code, out = wrun(c, s, t, fw)
            return code, posted(s), status_of(c), "the writer failed (RuntimeError)" in out
        check("writer-that-raises-loses-no-message", outcome(case_raises),
              (EXIT_OK, [plan_text + plain_note], [OPEN], True))

        # The pass's own deadline bounds the writer (KIT-297 review). It gets what the pass
        # has left, less the time to post the message plain, and with too little left it is
        # not called: a slow writer can never cost a message, nor the pass that sends it.
        class TimedWriter(FakeWriter):
            """A FakeWriter that records how long it was given for each message."""

            def __init__(self, *a, **kw):
                FakeWriter.__init__(self, *a, **kw)
                self.given = []

            def __call__(self, cfg, *a, **kw):
                self.given.append(cfg["writer_timeout_seconds"])
                return FakeWriter.__call__(self, cfg, *a, **kw)

        def case_bounded(timeout):
            fw = TimedWriter()
            c, s, t = wworld("w-bounded-%d" % timeout)
            wrun(c, s, t, fw)                               # the first pass: a baseline
            s.add(ALICE, "plan PROD-6")
            out = io.StringIO()
            code = run_command(c, False, timeout, slack=s, tracker=t, out=out, now=now + 60,
                               writer=fw)
            return (code, fw.given, posted(s),
                    "too little of this pass was left to wait for the writer" in out.getvalue(),
                    "| writer: %d written, %d went out plain" % (len(fw.given),
                                                                  1 - len(fw.given))
                    in out.getvalue())
        got = outcome(lambda: case_bounded(60))
        check("writer-time-is-cut-to-what-the-pass-has-left",
              (got[0], [30 <= x <= 35 for x in got[1]]) + got[2:]
              if isinstance(got, tuple) else got,
              (EXIT_OK, [True], ["I have news on PROD-6.\n\n" + plan_bind], False, True))
        check("writer-time-is-its-own-when-the-pass-has-room",
              outcome(lambda: case_bounded(120)[:3]),
              (EXIT_OK, [60], ["I have news on PROD-6.\n\n" + plan_bind]))
        check("writer-not-called-with-too-little-of-the-pass-left",
              outcome(lambda: case_bounded(30)),
              (EXIT_OK, [], [plan_text + plain_note], True, True))

        # The review's case: an approval pass has used `used` seconds of its 120 when
        # "Started" is written, and the writer takes all the time it is given. Started and
        # saved before the post, the child must still be said, now: no later pass says it.
        def case_start_near_deadline(used):
            ticks = [float(used)]
            c, s, t = wworld("w-deadline-%d" % used, act=True, agent_user_name="Agent")
            state = new_state()
            state.setdefault("acted_at", [])
            p = Pass(c, s, t, state, False, False, now, io.StringIO(),
                     deadline=float(c["run_timeout_seconds"]))
            p.clock = lambda: ticks[0]
            given = []

            def slow(cfg, kind, facts, fallback, keep=(), bind="", runner=None, now=None):
                given.append(cfg["writer_timeout_seconds"])
                ticks[0] += cfg["writer_timeout_seconds"]
                if ticks[0] >= p.deadline:
                    raise Deadline()                        # the alarm cuts it off first
                return {"text": fallback + ("\n\n" + bind if bind else ""), "by": "fallback",
                        "why": "the writer took longer than %g seconds"
                               % cfg["writer_timeout_seconds"], "cost_usd": None}
            p.writer = slow
            rec = {"channel": CHAN, "thread": "1760000000.000100", "ticket": "PROD-50",
                   "started": [], "approved_name": "Alice Example", "approved_by": ALICE,
                   "approved_at": "t"}
            child = {"id": "uuid-51", "identifier": "PROD-51", "title": "Child PROD-51"}
            try:
                p.start_child(rec, child, [child], "agent-1")
            except Deadline:
                return "the deadline cut the writer off", given, posted(s), rec["started"]
            return given, posted(s), rec["started"], p.counts["fell_back"]
        started_plain = "Started PROD-51 “Child PROD-51” (1 of 1)." + plain_note
        check("writer-near-the-deadline-still-says-the-start",
              outcome(lambda: case_start_near_deadline(70)),
              ([25], [started_plain], ["PROD-51"], 1))
        check("writer-at-the-deadline-is-not-called-and-the-start-is-said",
              outcome(lambda: case_start_near_deadline(100)),
              ([], [started_plain], ["PROD-51"], 1))
        check("config: the writer's timeout must leave a pass time to post plain",
              outcome(lambda: (
                  "'writer_timeout_seconds' (300) must be at most 95 while "
                  "'run_timeout_seconds' is 120" in _config_error(dict(
                      EXAMPLE_CONFIG, writer=True, writer_bin=writer_bin,
                      writer_timeout_seconds=300, state_dir=os.path.join(tmp, "wd1"))),
                  "'run_timeout_seconds' (30) leaves the writer no time: it must be at least "
                  "35" in _config_error(dict(
                      EXAMPLE_CONFIG, writer=True, writer_bin=writer_bin,
                      run_timeout_seconds=30, state_dir=os.path.join(tmp, "wd2"))),
                  _config_error(dict(EXAMPLE_CONFIG, writer=False, writer_timeout_seconds=300,
                                     state_dir=os.path.join(tmp, "wd3"))),
                  _config_error(dict(EXAMPLE_CONFIG, writer=True, writer_bin=writer_bin,
                                     writer_timeout_seconds=95,
                                     state_dir=os.path.join(tmp, "wd4"))))),
              (True, True, "", ""))

        # After "can't read", a clean run is told to the writer as readable again, never as
        # the notifier being back: it may never have stopped (KIT-297 review).
        def case_readable_fact():
            fw = FakeWriter()
            c, s, t, hb = nworld("w-readable", writer=True, writer_bin=writer_bin)
            with open(hb, "w") as fh:
                fh.write("{not json")
            for at in range(int(now), int(now) + 361, 60):
                if at == int(now) + 300:
                    beat(hb, at, 0)
                wrun(c, s, t, fw, at=float(at))
            return [(x["kind"], x["facts"].get("notifier")) for x in fw.calls]
        check("writer-readable-again-is-never-told-as-back", outcome(case_readable_fact),
              [("notifier-unreadable", "unreadable"), ("notifier-readable", "readable again")])

        # The real writer, with its token unset: it starts nothing, and the plain text goes.
        def case_real_writer():
            name = "BRIDGE_SELFTEST_WRITER_TOKEN"
            os.environ.pop(name, None)
            c, s, t = wworld("w-real", writer_token_env=name)
            run(c, s, t)
            s.add(ALICE, "plan PROD-6")
            code, out = run(c, s, t)
            p = Pass(c, s, t, new_state(), True, True, now, io.StringIO())
            return (code, posted(s), "%s is not set" % name in out, p.writer is voice.write)
        check("writer-the-real-one-with-no-token-starts-nothing", outcome(case_real_writer),
              (EXIT_OK, [plan_text + plain_note], True, True))

        def case_secrets():
            name = "BRIDGE_SELFTEST_WRITER_TOKEN"
            token = "sk-ant-oat01-" + "w" * 30
            os.environ[name] = token
            try:
                on = cfg_for("w-secrets-on", writer=True, writer_bin=writer_bin,
                             writer_token_env=name)
                off = cfg_for("w-secrets-off", writer_token_env=name)

                class Leaky(FakeWriter):
                    def __call__(self, *a, **kw):
                        res = FakeWriter.__call__(self, *a, **kw)
                        return dict(res, text="%s %s" % (token, res["text"]))
                fw = Leaky()
                c, s, t = wworld("w-secrets", writer_token_env=name)
                wrun(c, s, t, fw)
                s.add(ALICE, "plan PROD-6")
                wrun(c, s, t, fw)
                return (token in live_secrets(on), token in live_secrets(off),
                        token in posted(s)[-1], "«redacted»" in posted(s)[-1])
            finally:
                os.environ.pop(name, None)
        check("writer-its-token-is-redacted-like-every-other-secret", outcome(case_secrets),
              (True, False, False, True))

        check("config: the writer's keys are the job's, by the writer's own rules, every bad "
              "one named at once",
              outcome(lambda: (
                  sorted(k for k in voice.CONFIG_KEYS if k not in CONFIG_KEYS),
                  "'writer' must be true or false" in _config_error(dict(
                      EXAMPLE_CONFIG, writer="yes", state_dir=os.path.join(tmp, "wc1"))),
                  [k for k in ("writer_model", "writer_max_chars", "writer_token_env")
                   if "%r is not a valid writer setting" % k in _config_error(dict(
                       EXAMPLE_CONFIG, writer_model="--bare", writer_max_chars=0,
                       state_dir=os.path.join(tmp, "wc2")))],
                  "'writer_bin' must be the claude CLI's full path" in _config_error(dict(
                      EXAMPLE_CONFIG, writer=True, state_dir=os.path.join(tmp, "wc3"))),
                  _config_error(dict(EXAMPLE_CONFIG, writer=False, writer_bin="claude",
                                     state_dir=os.path.join(tmp, "wc4"))),
                  [cfg_for("wc5", writer=True, writer_bin=writer_bin)[k]
                   for k in ("writer", "writer_bin", "writer_model")])),
              ([], True, ["writer_model", "writer_max_chars"], True, "",
               [True, writer_bin, "haiku"]))

        # THE AUDIT: every text handed to the writer above passes the checks the model's
        # own text must pass (no question, no reply instruction, no mention, no stray id or
        # link), so none of these messages falls back by its own words; and none carries a
        # ticket's description.
        def audit():
            bad, kinds = [], set()
            for fw in audited:
                for x in fw.calls:
                    kinds.add(x["kind"])
                    why = voice.check_text(x["fallback"], 100000, x["keep"],
                                           voice.fact_links(x["facts"]),
                                           voice.fact_ids(x["facts"], x["keep"]))
                    if why or "What PROD-" in json.dumps(x["facts"]):
                        bad.append((x["kind"], why or "a description"))
            return bad, sorted(kinds)
        got = outcome(audit)
        check("writer-audit: no text handed to the writer would fail its own checks",
              got[0] if isinstance(got, tuple) else got, [])
        check("writer-audit: covers each kind of message above",
              [k for k in ("question-plan", "question-approve", "approve-done",
                           "started-child", "epic-paused", "ambiguous", "request-refused",
                           "foreign-post", "plan-moved", "action-limit", "plan-refused",
                           "notifier-unreadable", "ping-sent", "ping-refused",
                           "confirmed-acting-off")
               if not isinstance(got, tuple) or k not in got[1]], [])

        pipeline_bridge_plans.cases_finish(kit)
        pipeline_bridge_tech_lead.cases_tech_lead(kit)
        pipeline_bridge_requests.cases_requests(kit)
        pipeline_bridge_requests.cases_reader(kit)
        pipeline_bridge_requests.cases_combined(kit)
        pipeline_bridge_replan.cases_replan(kit)
        pipeline_bridge_tracker.cases_searches(kit)

        # The one mention of the chat bot the bridge ever posts is the forward's, made in one
        # place and called from one place.
        mentions = [ln.strip() for ln in body.splitlines()
                    if "<@" in ln and "chat_bot_user_id" in ln]
        check("tl-the-chat-bot-is-mentioned-at-one-post-site-only",
              ([ln.startswith('return "<@%s> %s') for ln in mentions],
               body.count("self.forward_text("), body.count("def forward_text(")),
              ([True], 1, 1))

        # 17. THE BINDS AS THE CASES POSTED THEM (KIT-342): each pinned, naming no lane, and
        #     no banned word in what the bridge says. The fixtures' data is taken out first,
        #     the fake tracker's live-work reasons too: they stand for the planner's own
        #     words, which it owns. The wrap-up's bind is the review's notes, verbatim.
        Pass.post = pass_post
        shapes = dict((name, re.compile(re.escape(value).replace("%s", "(.+?)")
                                        .replace("%d", r"(\d+)"), re.S))
                      for name, value in PINNED_BINDS.items())
        def posted_shape(name):
            """A fragment counts as posted only where it ends: at a stop, a comma or the end,
            or at its own closing backtick."""
            return re.compile(shapes[name].pattern + (
                "" if PINNED_BINDS[name].endswith("`") else r"(?=[.,;:]|$)"), re.S)
        posts = dict((name, posted_shape(name)) for name in shapes)
        binds = [(kind, bind) for kind, _text, bind, _plain in bind_log if bind]
        check("every-bind-a-case-posted-is-a-pinned-sentence",
              sorted(set(bind for kind, bind in binds if kind != "plan-finished"
                         and not any(shape.fullmatch(bind) for shape in shapes.values()))),
              [])
        # ...and every pinned one is posted by some case, whole or inside a question's.
        check("every-pinned-bind-is-posted-by-some-case",
              sorted(name for name, shape in posts.items()
                     if not any(shape.search(bind) for _kind, bind in binds)), [])
        # A question's clause saying what a yes does is one of its own pinned ones, whole.
        def wrong_slots(binds):
            slots = []
            for name, (slot, clauses) in sorted(THEN_SLOTS.items()):
                for _kind, bind in binds:
                    said = shapes[name].fullmatch(bind)
                    if said and not any(shapes[c].fullmatch(said.group(slot))
                                        for c in clauses):
                        slots.append("%s: %s" % (name, said.group(slot)))
            return sorted(set(slots))
        check("every-question-says-what-a-yes-does-in-a-pinned-clause", wrong_slots(binds), [])
        # The two audits have teeth (Stage E review of #271): a clause posted only as the
        # start of a longer one is not posted, a backticked word is wherever it stands, and a
        # question saying a yes does what no pin allows is named.
        asking = PINNED_BINDS["QUESTION_BIND"] % ("`yes`", "here", 24, "%s", "(Asked for by x.)")
        check("the-bind-audits-name-a-planted-fault",
              (bool(posted_shape("PLAN_THEN_BIND").search("and I'll hand it to the planner's "
                                                          "twin.")),
               bool(posted_shape("PLAN_THEN_BIND").search("and I'll hand it to the planner.")),
               bool(posted_shape("YES_BIND").search("Reply `yes` here")),
               wrong_slots([("question-plan", asking % "the moon turns blue")]),
               wrong_slots([("question-plan", asking % "I'll hand it to the planner")])),
              (False, True, True, ["QUESTION_BIND: the moon turns blue"], []))
        check("no-bind-a-case-posted-names-a-lane",
              sorted(set(bind for kind, bind in binds if kind != "plan-finished"
                         for lane in STATE_NAMES
                         if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(lane), bind))), [])

        def strings(value, depth=0):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict) and depth < 8:
                for each in value.values():
                    yield from strings(each, depth + 1)
            elif isinstance(value, (list, tuple, set)) and depth < 8:
                for each in value:
                    yield from strings(each, depth + 1)
        data = set()
        for fake in FakeTracker.made:
            data.update(strings(vars(fake)))
        for fake in FakeSlack.made:
            data.update(strings([m for m in fake.messages if m.get("user") != ME]))
            data.update(strings(fake.profiles))
        data = sorted((d for d in data if len(d) >= 4), key=len, reverse=True)

        def banned_in(part):
            """The banned words in one text of the bridge's, the fixtures' data taken out."""
            if not part or not BANNED_WORDS_RE.search(part):
                return []
            for d in data:
                part = part.replace(d, " ")
            return [word.lower() for word in BANNED_WORDS_RE.findall(part)]
        banned = set()
        for kind, text, bind, plain in bind_log:
            for part in ([text, plain] if kind else []) + [bind]:
                banned |= set("%s: %s" % (kind or "(no kind)", w) for w in banned_in(part))
        check("nothing-the-bridge-says-holds-a-banned-word", sorted(banned), [])
        # The scan is not blind: none of its own words is data (the chat bot's, relayed, are
        # posted with no kind), and a planted word is found.
        own = [part for kind, text, bind, _p in bind_log if kind for part in (text, bind)
               if part]
        check("the-scan-takes-none-of-the-bridges-own-words-out-as-data",
              (bool(own), sorted(set(part for part in own if part in set(data)))), (True, []))
        sample = next((text for kind, text, _b, _p in bind_log if kind and text), "")
        check("the-scan-finds-a-banned-word-in-a-real-post",
              (bool(sample), banned_in(sample + " Its children wait.")), (True, ["children"]))

    # 14. EXACTLY FOUR TRACKER WRITES (KIT-222, KIT-223, KIT-299): a state move carrying
    #     `stateId` only (an idea, an approved epic, a finished plan), a delegation carrying
    #     `delegateId` only, a comment, and a finished plan's follow-up carrying a team, a
    #     title and a description only. No other mutation anywhere above the tests, in this
    #     file or in the modules split out of it (KIT-312), so none can hide in one.
    tree = ast.parse(src)
    # A module only another split module imports is found too, at any depth, a cycle
    # ends the scan, and the pins read each one (final delta reviews of KIT-388).
    deep = {"pipeline_bridge_a": "import pipeline_bridge_b\nx = 'mutation DeepA'\n"
                                 "# Selftest — offline\nnot read\n",
            "pipeline_bridge_b": "from pipeline_bridge_c import x\nx = 'mutation DeepB'\n"
                                 "# Selftest — offline\nnot read\n",
            "pipeline_bridge_c": "import pipeline_bridge_a\nx = 'mutation DeepC'\n"
                                 "# Selftest — offline\nnot read\n"}
    whole = "x = 1\n# Selftest — offline\ndef main():\n    import pipeline_bridge_a\n"
    deep_text, deep_found = pinned_text(whole, "x = 1\n", deep.__getitem__)
    check("the-split-scan-follows-imports-to-any-depth",
          (deep_found, re.findall(r"mutation (Deep\w)", deep_text), "not read" in deep_text),
          (["pipeline_bridge_a", "pipeline_bridge_b", "pipeline_bridge_c"],
           ["DeepA", "DeepB", "DeepC"], False))
    check("the-write-pins-read-the-split-modules-too",
          (split_modules != [], "class SlackClient(" in body, "def _http_json(" in body),
          (True, True, True))
    mutations = re.findall(r"\bmutation\s+(\w+)", body)
    check("only-four-tracker-writes", sorted(mutations),
          ["BridgeComment", "BridgeDelegate", "BridgeFollowUp", "BridgeMove"])
    check("the-move-sets-stateId-only",
          re.search(r"issueUpdate\(id: \$id, input: \{ stateId: \$stateId \}\)", body)
          is not None, True)
    check("the-delegation-sets-delegateId-only",
          re.search(r"issueUpdate\(id: \$id, input: \{ delegateId: \$delegateId \}\)",
                    body) is not None and body.count("issueUpdate(") == 2, True)
    # KIT-299: one site, and its input exactly a team, a title and a description: no label,
    # no assignee, no delegate, no state, no parent, so the ticket starts no work.
    check("the-follow-up-sets-teamId-title-description-only",
          (re.search(r"issueCreate\(input: \{ teamId: \$teamId, title: \$title, "
                     r"description: \$description \}\)", body) is not None,
           body.count("issueCreate(")), (True, 1))
    # The moves: a plan, a finished plan, the epic's approval, a re-plan's two, out to the
    # backlog and back into Plan it (KIT-244), and one a plan's pause, resume or cancel
    # shares (KIT-351).
    check("the-move-and-the-follow-up-are-the-only-callers",
          (len(re.findall(r"\.move\(", body)), len(re.findall(r"\.file_followup\(", body)),
           len(re.findall(r"_query\(M_FOLLOWUP", body))), (6, 1, 1))
    # The other tracker writes, made through the tracker client from the modules that make
    # them: nine comments, one hand-over and one thread reply. A new call site, in the
    # bridge or in any module, turns this red, so each one is chosen (review of KIT-407);
    # the ninth names who paused, resumed or cancelled a plan (KIT-351).
    check("the-comment-hand-over-and-thread-reply-callers-are-counted",
          [len(re.findall(r"\.%s\(" % verb, body))
           for verb in ("comment", "delegate", "reply_in_thread")], [9, 1, 1])
    check("no-other-write-verb", [v for v in ("issueDelete", "issueArchive",
                                              "issueAddLabel", "issueRelationCreate",
                                              "issueBatchCreate", "agentSessionCreate",
                                              "assigneeId", "labelIds", "parentId",
                                              "delegateId: $delegateId, ",
                                              "stateId: $stateId, ", "subscriberIds",
                                              "projectId", "templateId")
                                  if v in body], [])
    check("tracker-endpoint-fixed", body.count("https://api.linear.app/graphql"), 1)
    # KIT-224: the one borrowed write is the bounce driver's thread reply, from one place.
    check("the thread reply is borrowed from one place only",
          body.count("linear_reply_in_thread("), 1)
    # Last: every call the guard refused, whatever the case made of the refusal.
    check("no-case-reached-real-http", list(reached), [])

    if failures:
        print("FAIL: pipeline_bridge selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_bridge selftest (%d cases)" % cases[0])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="The Slack bridge: one pass.")
    parser.add_argument("command", nargs="?", choices=("run", "lanes"), default="run")
    parser.add_argument("--config")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--example-config", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2, sort_keys=True))
        for key, doc in sorted(CONFIG_KEYS.items()):
            sys.stderr.write("  %-24s %s\n" % (key, doc))
        return EXIT_OK
    if not args.config:
        sys.stderr.write("FAIL: --config is required (see --example-config)\n")
        return EXIT_USAGE
    try:
        cfg = load_config(args.config)
        os.makedirs(cfg["state_dir"], exist_ok=True)
    except BridgeError as exc:
        sys.stderr.write("FAIL: %s\n" % exc)
        return exc.code
    except OSError as exc:
        sys.stderr.write("FAIL: could not create state_dir: %s\n" % exc)
        return EXIT_USAGE
    if args.command == "lanes":
        return lanes_command(cfg)
    timeout = args.timeout if args.timeout is not None else cfg["run_timeout_seconds"]
    return run_command(cfg, args.dry_run, timeout)


if __name__ == "__main__":
    sys.exit(main())
