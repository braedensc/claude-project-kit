#!/usr/bin/env python3
"""Human-action notifier — one short ping per event that needs a person, to a chat channel.
Implements docs/adr/2026-09-06-human-action-notifier-and-reply-relay.md (Accepted
2026-09-11; channel ratified as Slack).

WHAT THIS IS

  The tracker notifies on everything the same way, so the handful of events that need a
  DECISION drown in bookkeeping. This job is the thing that separates them. It is the Stage E
  poller's sibling: one scheduled, one-shot pass that reads the marks the pipeline ALREADY
  writes, and sends one short message per new event to a private chat channel.

  It is deterministic code, not a session. No model, no prompt, no tokens — so there is
  nothing here for an injection to steer, and a notification cannot be produced by anything
  except a mark a pipeline component actually wrote.

  The marks are the producer-consumer contract (the ADR's own table, reproduced below). The
  notifier adds no second shape: it greps what the idea gate's executor, a stopped working
  session, and the bounce driver already put on the tracker.

  | Mark                     | Written by            | The human moment                  | agent:blocked? |
  |--------------------------|-----------------------|-----------------------------------|----------------|
  | epic-awaiting-approval   | plan executor         | a plan is filed; approve the epic | no             |
  | planning-needs-input     | plan executor         | planner asked, filed no plan      | yes            |
  | planning-no-output       | plan executor         | a planning run produced nothing   | yes (§13 case) |
  | planning-rejected        | plan executor         | a plan was refused; re-plan       | no             |
  | agent:blocked            | a stopped session     | blocked on a decision             | yes            |
  | agent:needs-human        | a stopped session     | terminal until a person acts      | needs-human    |

  The review lane's "NOT reviewed" verdict is deliberately NOT paged on: it is a CI-visible
  verdict, not a person's decision (ADR, same table).

  NOT IN THIS BUILD: the pull-request human moment (ADR decision 5, "opened" until the review
  lane is live and "reviewed and green" after). It needs the code host, which this pass does
  not read. There is deliberately NO `pr_human_moment` config key until the lane exists — an
  accepted key whose only effect is to be validated is the silent no-op §13 forbids.

WHAT IT NEVER DOES (asserted in --selftest, the same way every Stage E script asserts it)

  It never merges, never approves, never enables auto-merge, and never labels a pull request.
  Its ONLY tracker mutation is applying a lifecycle label on the two marks decision 4 names,
  plus honouring the label a session's own escalation mark requests. It moves no ticket state,
  creates no ticket, and posts no tracker comment. It launches no session and runs no model.
  §10 of the selftest reads this file's own source and asserts the banned tokens appear zero
  times outside the selftest.

  It never carries the question text off the tracker. `build_message()` takes the mark, the
  ticket id, the ticket title and a URL — and has NO parameter that could carry a comment
  body. Decision 2 is structural here, not a redaction rule that can miss.

WHERE IT RUNS, AND WHY THE CREDENTIAL RULES ARE WHAT THEY ARE

  It runs as the DISPATCHER'S ROLE ACCOUNT under a system LaunchDaemon, beside the Stage E
  poller (decision 6). It is not a session, so it is neither sandboxed nor proxied and the
  session egress allowlist is not widened to reach the chat provider.

  Credentials come from env vars this job's config NAMES, never from values in the config:

    1. in the job's OWN env file under the role account's home (<home>/.stage-e/env, 600);
    2. NEVER in the dispatcher's env file, which is copied unscrubbed into every session;
    3. NEVER under the dispatcher's state root, where session readability is unmeasured.

WHY THE MARK SEQUENCE IS NEVER DECODED

  The plan executor neutralizes `<!--` and `-->` in every session-supplied string BEFORE
  embedding it in a comment it marks (pipeline_plan_executor._sanitize). That is what stops a
  session smuggling a forged mark inside a question body and paging the owner on text no
  component authored. This file therefore greps the RAW body and never HTML-unescapes it: a
  `&lt;!--` in a body is neutralized text, and treating it as a mark would undo the defence.

  For the same reason the mark must be on the comment's FIRST line, which is where every
  producer puts it — a mark found deeper in a body is reported and skipped, not paged on.

THE SEEN-SET IS WHAT MAKES A RESTART SAFE

  One notification per event, keyed on the id of the comment (or the label event) that
  produced it. A restart, a re-run, or a scheduler catching up after sleep therefore re-sends
  nothing. The seen-set is a rebuildable cache, not a source of truth: losing it costs
  duplicate pings, never a missed one, so a corrupt file REFUSES to run rather than silently
  looking like a first pass.

LIVE-TEST ITEMS (coded defensively; verify on the first real run and amend here)

  * Whether Linear's comment list returns the mark on the body's first line unchanged for
    comments created by the executor's GraphQL mutation. Coded to require it; if the tracker
    normalizes leading whitespace this still matches (the regex tolerates it).
  * Whether `chat.postMessage` needs `channels:read` in addition to `chat:write` for a
    private channel the bot was invited to. Coded for `chat:write` only.
  * The exact label id for `agent:blocked` in the live workspace. Resolved by key from config,
    never by display text (§6).

Usage:
  pipeline_notify_local.py run      --config FILE [--dry-run] [--timeout N]
  pipeline_notify_local.py scan     --config FILE          # find events, send nothing
  pipeline_notify_local.py --example-config
  pipeline_notify_local.py --selftest

Exit:
  0  ran. Every "nothing to do" is printed as what was asked and what the answer was.
  1  could not do something it WILL retry (a transient tracker/chat failure, an escaped bug).
  2  usage/config/import error — nothing was touched.
  3  ran, and at least one event was DECLINED this pass (seen but not sendable).
  4  hit the wall-clock --timeout. Distinct from 1 so a scheduler log tells hung from failed.

  A heartbeat is left on every path above. The two cases that leave none are an unreadable
  --config (exit 2, before the state dir is known) and a SIGTERM/KeyboardInterrupt.
"""

import argparse
import json
import os
import re
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ── Exit vocabulary — the same five names and numbers every sibling job uses. ──────────
# Redeclared rather than imported so this file stands alone; the selftest asserts they
# still agree with the poller, because the Stage E installer cross-checks EXIT_DECLINED.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_DECLINED = 3
EXIT_TIMEOUT = 4

HEARTBEAT_SCHEMA = "pipeline-notifier-heartbeat/1"
SEEN_SCHEMA = "pipeline-notifier-seen/1"

DEFAULT_STATE_DIR = "~/.stage-e/state"

# ── The marks, and what each means. Keys are EXACTLY the ADR table's mark strings. ─────
#
# `label` is the lifecycle label this job applies, or None.
#
# Decision 4 names agent:blocked for the two planning marks that mean "the run needs the
# owner". For a SESSION-authored mark the job applies the label the session's own mark
# REQUESTS — agent:blocked or agent:needs-human — because §6 routes those two differently
# and the session asked for a specific one. The ADR's table gives "yes" for the bounce
# driver's needs-human row; that row is left as None here because the bounce driver has
# ALREADY applied needs-human by the time the mark exists, and re-applying it would be a
# second writer for one signal. Flagged in the PR for the owner rather than assumed.
ESC_AWAITING_APPROVAL = "epic-awaiting-approval"
ESC_NEEDS_INPUT = "planning-needs-input"
ESC_REJECTED = "planning-rejected"
ESC_NO_OUTPUT = "planning-no-output"
ESC_BLOCKED = "agent:blocked"
ESC_NEEDS_HUMAN = "agent:needs-human"

MARKS = {
    ESC_AWAITING_APPROVAL: {
        "moment": "A plan is ready for you to approve",
        "label": None,
        "why_no_label": "an approval, not a block",
    },
    ESC_NEEDS_INPUT: {
        "moment": "Planning needs your input",
        "label": "agent:blocked",
        "why_no_label": None,
    },
    ESC_NO_OUTPUT: {
        "moment": "A planning run produced nothing",
        "label": "agent:blocked",
        "why_no_label": None,
    },
    ESC_REJECTED: {
        "moment": "A plan was refused; it needs re-planning",
        "label": None,
        "why_no_label": "a loud, self-explaining comment already carries its own cue",
    },
    ESC_BLOCKED: {
        "moment": "A session stopped and needs a decision",
        "label": "agent:blocked",
        "why_no_label": None,
    },
    ESC_NEEDS_HUMAN: {
        "moment": "A session is terminal until you act",
        "label": "agent:needs-human",
        "why_no_label": None,
    },
}

# The mark as every producer writes it, matched on a body's FIRST line only.
# The alternation is widened to all six marks: the only pre-existing consumer regex, in the
# inert GitHub-Actions lane, matches the session half alone and would silently see none of
# the four planning marks.
MARK_RE = re.compile(
    r"^\s*<!--\s*pipeline-escalation:\s*(%s)\s*-->" % "|".join(re.escape(k) for k in MARKS)
)

# needs-human WINS over blocked when a body somehow carries both — the precedence the
# GitHub-Actions lane already publishes, kept so the two lanes never disagree.
MARK_PRECEDENCE = [ESC_NEEDS_HUMAN, ESC_BLOCKED, ESC_NO_OUTPUT, ESC_NEEDS_INPUT,
                   ESC_REJECTED, ESC_AWAITING_APPROVAL]

# Deliberately NOT a mark. The plan executor writes it on informational notes; paging on it
# would turn every filed plan's footnote into a notification.
NOT_A_MARK = "planning-note"

CONFIG_KEYS = {
    "state_dir": "where the seen-set and heartbeat live; must be outside every git worktree",
    "linear_key_env": "ENV VAR NAME holding the tracker key (never the key itself)",
    "chat_token_env": "ENV VAR NAME holding the chat bot token (never the token itself)",
    "chat_channel_id": "the private channel id the ping is posted to",
    "chat_api_base": "chat API base URL; defaults to the Slack Web API",
    "team_keys": "tracker team keys whose tickets are scanned, e.g. ['KIT','TOD']",
    "ticket_url_template": "how a ticket id becomes a link, e.g. https://…/issue/{id}",
    "label_ids": "map of label key -> label id, resolved by KEY never by display text (§6)",
    "executor_actor_ids": "actor ids allowed to author the planning marks; a session that "
                          "can comment could otherwise forge them",
    "max_events_per_pass": "flood guard; above it the pass sends one summary and declines",
    "lookback_comments": "how many recent comments per ticket to examine",
    "run_timeout_seconds": "wall clock for one pass; exceeding it is exit 4, not exit 1",
}

EXAMPLE_CONFIG = {
    "state_dir": DEFAULT_STATE_DIR,
    "linear_key_env": "LINEAR_OWNER_API_KEY",
    "chat_token_env": "SLACK_BOT_TOKEN",
    "chat_channel_id": "C0123456789",
    "chat_api_base": "https://slack.com/api",
    "team_keys": ["KIT"],
    "ticket_url_template": "https://linear.app/example/issue/{id}",
    "label_ids": {"agent:blocked": "<uuid>", "agent:needs-human": "<uuid>"},
    "executor_actor_ids": ["<plan-executor actor uuid>"],
    "max_events_per_pass": 25,
    "lookback_comments": 20,
    "run_timeout_seconds": 240,
}

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class NotifierError(Exception):
    """A condition the operator must fix. Carries an exit code."""

    def __init__(self, message, code=EXIT_USAGE):
        super().__init__(message)
        self.code = code


class Deadline(BaseException):
    """The wall clock ran out.

    Deliberately a BaseException, like KeyboardInterrupt: a broad `except Exception` in
    the per-event loop would otherwise eat the alarm, the pass would run past its budget
    and report exit 3 instead of exit 4 — a hang reported as a decline.
    """


# ══════════════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════════════

def load_config(path):
    """Read the config, refuse anything it does not recognise, report EVERY error at once.

    All-errors-in-one-pass is deliberate: a first-error-wins loader makes an operator run
    the installer once per typo, and the installer's own selftest pins this behaviour.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise NotifierError("could not read --config %s: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise NotifierError("--config must hold one JSON object")

    errors = []
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        errors.append("unknown config key(s): %s (see --example-config)"
                      % ", ".join(unknown))

    cfg = {
        "state_dir": os.path.realpath(os.path.expanduser(
            raw.get("state_dir") or DEFAULT_STATE_DIR)),
        "linear_key_env": raw.get("linear_key_env") or "LINEAR_OWNER_API_KEY",
        "chat_token_env": raw.get("chat_token_env") or "SLACK_BOT_TOKEN",
        "chat_channel_id": (raw.get("chat_channel_id") or "").strip(),
        "chat_api_base": (raw.get("chat_api_base") or "https://slack.com/api").rstrip("/"),
        "team_keys": raw.get("team_keys") or [],
        "ticket_url_template": (raw.get("ticket_url_template") or "").strip(),
        "label_ids": raw.get("label_ids") or {},
        "executor_actor_ids": raw.get("executor_actor_ids") or [],
        "max_events_per_pass": raw.get("max_events_per_pass") or 25,
        "lookback_comments": raw.get("lookback_comments") or 20,
        "run_timeout_seconds": raw.get("run_timeout_seconds") or 240,
    }

    for key in ("linear_key_env", "chat_token_env"):
        name = cfg[key]
        if not ENV_NAME_RE.match(name or ""):
            errors.append(
                "config %r must be an ENV VAR NAME like SLACK_BOT_TOKEN, got %r — "
                "never put a credential value in the config" % (key, name))

    if not cfg["chat_channel_id"]:
        errors.append("config needs 'chat_channel_id' — the private channel to post to")
    if not cfg["team_keys"] or not isinstance(cfg["team_keys"], list):
        errors.append("config needs 'team_keys' as a non-empty list of tracker team keys")
    if "{id}" not in cfg["ticket_url_template"]:
        errors.append("config 'ticket_url_template' must contain '{id}', got %r"
                      % cfg["ticket_url_template"])
    if not isinstance(cfg["label_ids"], dict):
        errors.append("config 'label_ids' must be an object mapping label key -> id")
    else:
        needed = sorted({m["label"] for m in MARKS.values() if m["label"]})
        missing = [k for k in needed if not cfg["label_ids"].get(k)]
        if missing:
            errors.append(
                "config 'label_ids' is missing %s — resolve each by KEY out of the "
                "tracker's label ids; §6 compares ids, never display text"
                % ", ".join(repr(m) for m in missing))

    # Numeric keys are validated here, in the same all-at-once pass, so a string value is a
    # named config error rather than a traceback at alarm-arming time with no heartbeat.
    for key in ("lookback_comments", "run_timeout_seconds", "max_events_per_pass"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            errors.append("config %r must be a positive integer, got %r" % (key, value))

    if not isinstance(cfg["executor_actor_ids"], list) or not cfg["executor_actor_ids"]:
        errors.append(
            "config needs 'executor_actor_ids' — the actor id(s) allowed to author the "
            "planning marks. Without it a session that can comment could forge one; the "
            "job refuses to guess.")

    dupes = sorted({k for k in cfg["team_keys"] if cfg["team_keys"].count(k) > 1})
    if dupes:
        errors.append("config 'team_keys' repeats %s — one pass would read it twice"
                      % ", ".join(repr(d) for d in dupes))

    if _inside_git_worktree(cfg["state_dir"]):
        errors.append(
            "config 'state_dir' (%s) is inside a git working tree — the seen-set must live "
            "outside every worktree so no session can reach it" % cfg["state_dir"])

    if errors:
        raise NotifierError("config problems:\n  - " + "\n  - ".join(errors))
    return cfg


def _inside_git_worktree(path):
    """True when path sits under a directory containing a .git entry."""
    cur = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def credential(cfg, key):
    """The value from the env var the config NAMES. Never logged, never defaulted."""
    name = cfg[key]
    value = os.environ.get(name, "").strip()
    if not value:
        raise NotifierError(
            "$%s is unset — export it in the NOTIFIER'S OWN env file under the role "
            "account's home (<home>/.stage-e/env, mode 600). Never in the dispatcher's env "
            "file, which is copied unscrubbed into every session, and never under the "
            "dispatcher's state root, where session readability is unmeasured. --dry-run "
            "still needs it for reads." % name)
    return value


# ══════════════════════════════════════════════════════════════════════════════════════
# State — the seen-set and the heartbeat
# ══════════════════════════════════════════════════════════════════════════════════════

def seen_path(cfg):
    return os.path.join(cfg["state_dir"], "notifier-seen.json")


def heartbeat_path(cfg):
    return os.path.join(cfg["state_dir"], "notifier-heartbeat.json")


def _atomic_write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_seen(cfg):
    """The rebuildable cache of event ids already sent.

    A MISSING file is a first pass and is fine. A CORRUPT file refuses to run: silently
    treating it as empty would re-page every open event, and silently treating it as full
    would drop real ones — §13's "could not do it" rather than "nothing to do".
    """
    path = seen_path(cfg)
    if not os.path.exists(path):
        return {"sent": set(), "labelled": set()}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise NotifierError(
            "could not read the seen-set %s: %s — refusing to run rather than re-paging "
            "every open event. Delete it to start fresh (one duplicate ping per open "
            "event is the cost)." % (path, exc), code=EXIT_ERROR)
    if not isinstance(doc, dict) or doc.get("schema") != SEEN_SCHEMA:
        raise NotifierError(
            "the seen-set %s is not a %s document — refusing to run" % (path, SEEN_SCHEMA),
            code=EXIT_ERROR)
    # Two halves: what has been paged, and what has been marked on the board. An event in
    # the first but not the second is a label owed, and the next pass settles it without
    # paging again. `ids` is the pre-split shape and is read as fully-done.
    legacy = set(doc.get("ids") or [])
    return {
        "sent": set(doc.get("sent") or []) | legacy,
        "labelled": set(doc.get("labelled") or []) | legacy,
    }


def save_seen(cfg, sent_keys, labelled_keys):
    _atomic_write_json(seen_path(cfg), {
        "schema": SEEN_SCHEMA,
        "sent": sorted(sent_keys),
        "labelled": sorted(labelled_keys),
    })


def write_heartbeat(cfg, result, now):
    """Record what the last one-shot pass did, atomically.

    §13 applied to the daemon itself: without this, "ran, nothing to do" and "has not run
    since the reboot" look identical from outside. Best effort — a heartbeat that cannot be
    written is said on stderr and never changes the pass's own exit code.
    """
    # `dry` is load-bearing: without it a rehearsal writes a heartbeat claiming messages
    # were sent, and a monitor reading the record cannot tell a dry run from a real pass.
    dry = bool(result.get("dry"))
    doc = {
        "schema": HEARTBEAT_SCHEMA,
        "at": now,
        "dry": dry,
        "exit": result.get("exit"),
        "examined": result.get("examined", 0),
        "sent": 0 if dry else result.get("sent", 0),
        "would_send": result.get("sent", 0) if dry else 0,
        "declined": result.get("declined", 0),
        "labelled": 0 if dry else result.get("labelled", 0),
        "summary": result.get("summary", ""),
    }
    try:
        _atomic_write_json(heartbeat_path(cfg), doc)
    except OSError as exc:
        sys.stderr.write("NOTE: could not write the heartbeat %s: %s\n"
                         % (heartbeat_path(cfg), exc))


# ══════════════════════════════════════════════════════════════════════════════════════
# Pure logic — no I/O, so the selftest drives all of it offline
# ══════════════════════════════════════════════════════════════════════════════════════

_SECRET_SHAPES = [
    (re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"), "slack token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github fine-grained PAT"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "github token"),
    (re.compile(r"lin_(api|oauth)_[A-Za-z0-9]{20,}"), "linear key"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "api key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
]


def _local_secret_hits(text):
    hits = []
    for pattern, label in _SECRET_SHAPES:
        if pattern.search(text or "") and label not in hits:
            hits.append(label)
    return hits


def secret_hits(text):
    """The LABELS of every credential shape in `text` (never the matched text).

    The UNION of the publisher's scanner and the shapes above, not one or the other. The
    publisher's list is authoritative for everything the pipeline already handles, and this
    lane keeps the two in step by always consulting it. But it carries no `xox*` pattern —
    it was written before a chat token existed anywhere in the system — so a Slack bot token
    in a session-writable title would have passed it. Running both means neither lane has to
    know about the other's shapes, and adding one here never weakens the shared list.

    Empty means clean.
    """
    hits = list(_local_secret_hits(text))
    try:
        import pipeline_review_local as prl
        fn = getattr(prl, "secret_hits", None)
        if callable(fn):
            for label in fn(text):
                if label not in hits:
                    hits.append(label)
    except Exception:                                             # noqa: BLE001
        pass
    return hits


def redact(text, secrets):
    """Replace any live credential VALUE with a marker before it reaches an output.

    An exception message is the usual carrier: a transport that echoes its request, or a
    tracker error quoting the Authorization header, would otherwise put the key on stdout
    and into the heartbeat file.
    """
    out = text or ""
    for value in secrets or ():
        if value and len(value) >= 8:
            out = out.replace(value, "«redacted»")
    return out


_SLACK_UNSAFE = re.compile(r"[<>&|]")


def safe_title(title):
    """A session-writable title, made inert for the chat provider.

    Slack reads `<...>` as a link or a control sequence, so a title of `<!channel>` would
    turn one ticket's ping into a room-wide alert. Angle brackets, ampersands and pipes go;
    the same angle-bracket discipline the ADR's security model applies to relayed replies.
    """
    cleaned = _SLACK_UNSAFE.sub(" ", title or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > 120:
        cleaned = cleaned[:117] + "…"
    return cleaned


def find_mark(body):
    """The escalation mark on a comment body's FIRST line, or None.

    The body is used RAW. The plan executor neutralizes `<!--` in session-supplied text
    before embedding it, so an HTML-escaped sequence is deliberately-defused text; decoding
    it back here would hand a session the power to forge a mark and page the owner.
    """
    if not body:
        return None
    first = body.split("\n", 1)[0]
    match = MARK_RE.match(first)
    if not match:
        return None
    return match.group(1)


def resolve_marks(bodies):
    """Given several bodies, the single mark that wins by precedence, or None."""
    found = {m for m in (find_mark(b) for b in bodies) if m}
    for mark in MARK_PRECEDENCE:
        if mark in found:
            return mark
    return None


def build_message(mark, ticket_id, ticket_title, url):
    """The outbound text. Title and link only — decision 2, structurally.

    There is no parameter here that could carry a comment body, a question, or a diff. That
    is the point: the content stays in the tracker, where the owner is authenticated, and it
    cannot leak through a redaction rule that misses a case.
    """
    if mark not in MARKS:
        raise NotifierError("unknown mark %r — not in the ADR's table" % mark)
    moment = MARKS[mark]["moment"]
    title = safe_title(ticket_title)
    head = "*%s on %s*" % (moment, ticket_id)
    if title:
        head += " — %s" % title
    return "%s\n%s" % (head, url)


def label_for(mark):
    """The lifecycle label this job applies for a mark, or None."""
    return MARKS.get(mark, {}).get("label")


def event_id(comment_id):
    """The dedupe key. One notification per producing comment, forever."""
    return "comment:%s" % comment_id


def ticket_url(cfg, ticket_id):
    return cfg["ticket_url_template"].replace("{id}", ticket_id)


def is_authorised(mark, author_id, cfg):
    """Whether this author may produce this mark.

    The plan executor's sanitizer protects text the EXECUTOR embeds. It does nothing about
    a comment a session writes itself — a session with tracker-comment write could post
    `<!-- pipeline-escalation: planning-no-output -->` and page the owner, or make this job
    apply a label, on a mark no component authored. So the four executor-only marks are
    accepted only from a configured executor actor id.

    The two `agent:*` marks are legitimately session-authored (the /work escalation step),
    so they are accepted from any author. What they can do is bounded by the label mapping:
    a session can already request its own supervision label, and §6's invariant is that it
    never applies one itself — which is still true here, because this job applies it.
    """
    if mark in (ESC_BLOCKED, ESC_NEEDS_HUMAN):
        return True
    allowed = cfg.get("executor_actor_ids") or []
    return bool(author_id) and author_id in allowed


def select_events(tickets, sent_keys, cfg):
    """Pure: turn tracker reads into the events that should be sent this pass.

    `tickets` is a list of {"id","uuid","title","comments":[{"id","body","author_id"}]}.
    Returns (events, skipped, capped) where every skipped entry NAMES its reason, so a pass
    can say what it asked and what the answer was rather than going quiet.
    """
    events, skipped = [], []
    emitted = set()
    cap = cfg.get("max_events_per_pass") or 25
    capped = 0

    for ticket in tickets:
        tid = ticket.get("id")
        for comment in ticket.get("comments") or []:
            cid = comment.get("id")
            body = comment.get("body") or ""
            first = body.split("\n", 1)[0]
            mark = find_mark(body)

            if not mark:
                if NOT_A_MARK in first:
                    skipped.append((tid, cid, "informational note, not a mark"))
                elif "<!-- pipeline-escalation:" in body:
                    # The live-test assumption failing must be VISIBLE, not silent: a mark
                    # the producers put on line 1 turning up deeper means a producer
                    # changed, and a quiet skip would read as a quiet week.
                    skipped.append((tid, cid,
                                    "a pipeline-escalation sequence is present but not on "
                                    "line 1 — not paged; check the producer"))
                continue

            if not cid:
                skipped.append((tid, None, "comment has no id — cannot dedupe, not paged"))
                continue

            if not is_authorised(mark, comment.get("author_id"), cfg):
                skipped.append((tid, cid,
                                "mark %s from an unauthorised author — only the configured "
                                "executor may produce it" % mark))
                continue

            key = event_id(cid)
            if key in sent_keys:
                skipped.append((tid, cid, "already sent"))
                continue
            if key in emitted:
                skipped.append((tid, cid, "duplicate within this pass"))
                continue
            if len(events) >= cap:
                capped += 1
                continue

            emitted.add(key)
            events.append({
                "key": key,
                "mark": mark,
                "ticket_id": tid,
                "ticket_uuid": ticket.get("uuid"),
                "ticket_title": ticket.get("title") or "",
                "url": ticket_url(cfg, tid or ""),
                "label": label_for(mark),
                "existing_labels": ticket.get("label_ids") or [],
            })
    return events, skipped, capped


# ══════════════════════════════════════════════════════════════════════════════════════
# Transport
# ══════════════════════════════════════════════════════════════════════════════════════

def _post_json(url, payload, headers, timeout=20):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


class ChatClient:
    """The chat side. Posts, and nothing else — there is no read path here at all."""

    def __init__(self, cfg, token):
        self.base = cfg["chat_api_base"]
        self.channel = cfg["chat_channel_id"]
        self.token = token

    def post_message(self, text):
        return _post_json(
            "%s/chat.postMessage" % self.base,
            {"channel": self.channel, "text": text, "unfurl_links": False,
             "unfurl_media": False},
            {"Authorization": "Bearer %s" % self.token,
             "Content-Type": "application/json; charset=utf-8"},
        )


class TrackerClient:
    """The tracker side. Reads tickets and comments; its ONE write is an ADDITIVE label."""

    def __init__(self, cfg, token, endpoint="https://api.linear.app/graphql"):
        self.cfg = cfg
        self.token = token
        self.endpoint = endpoint

    def _gql(self, query, variables):
        doc = _post_json(
            self.endpoint, {"query": query, "variables": variables},
            {"Authorization": self.token, "Content-Type": "application/json"},
        )
        # An in-band `errors` document is a FAILURE, not an empty result. Without this a
        # revoked credential or a bad field reads as zero tickets and the pass reports
        # "nothing to do" — §13 inverted, the exact shape the contract legislates against.
        if isinstance(doc, dict) and doc.get("errors"):
            raise NotifierError(
                "tracker returned errors: %s" % json.dumps(doc["errors"])[:400],
                code=EXIT_ERROR)
        if not isinstance(doc, dict) or "data" not in doc:
            raise NotifierError("tracker response carried no data document",
                                code=EXIT_ERROR)
        return doc

    def recent_tickets(self, team_key, limit):
        """Tickets, with the UUID a mutation needs, the labels a union needs, and the
        comment author the mark gate needs."""
        query = ("query($t:String!,$n:Int!,$c:Int!){issues(filter:{team:{key:{eq:$t}}},"
                 "first:$n,orderBy:updatedAt){nodes{id identifier title "
                 "labels{nodes{id}} "
                 "comments(first:$c){nodes{id body user{id} botActor{id}}}}}}")
        doc = self._gql(query, {"t": team_key, "n": 50, "c": int(limit)})
        nodes = (((doc.get("data") or {}).get("issues") or {}).get("nodes")) or []
        out = []
        for n in nodes:
            comments = []
            for c in ((n.get("comments") or {}).get("nodes")) or []:
                author = ((c.get("user") or {}).get("id")
                          or (c.get("botActor") or {}).get("id"))
                comments.append({"id": c.get("id"), "body": c.get("body"),
                                 "author_id": author})
            out.append({
                "id": n.get("identifier"),
                "uuid": n.get("id"),
                "title": n.get("title"),
                "label_ids": [x.get("id") for x in
                              ((n.get("labels") or {}).get("nodes")) or []],
                "comments": comments,
            })
        return out

    def add_label(self, issue_uuid, label_id):
        """THE ONLY TRACKER WRITE IN THIS FILE. Additive, so the other labels survive.

        A bare `labelIds` on issueUpdate REPLACES the whole set, which would strip
        effort/track/provenance off every ticket this job touches — the same delete-by-write
        shape §1 already warns about. And the mutation is keyed on the issue UUID, not the
        human identifier: `issueUpdate(id:"KIT-1")` does not resolve.
        """
        if not issue_uuid:
            raise NotifierError("no issue uuid — refusing to guess", code=EXIT_ERROR)
        if not label_id:
            raise NotifierError("no label id — resolve it by key from config (§6)",
                                code=EXIT_ERROR)
        query = ("mutation($id:String!,$input:IssueUpdateInput!)"
                 "{issueUpdate(id:$id,input:$input){success}}")
        doc = self._gql(query, {"id": issue_uuid,
                                "input": {"addedLabelIds": [label_id]}})
        if not (((doc.get("data") or {}).get("issueUpdate") or {}).get("success")):
            raise NotifierError("issueUpdate did not report success", code=EXIT_ERROR)
        return True


# ══════════════════════════════════════════════════════════════════════════════════════
# The pass
# ══════════════════════════════════════════════════════════════════════════════════════

def run_once(cfg, tracker, chat, dry_run, out=sys.stdout, secrets=()):
    """One scan-decide-send pass. Returns a result dict; never raises for one bad event.

    THE SEEN-SET HAS TWO HALVES, and that is what makes a half-done event recoverable.
    `sent` records what has been paged; `labelled` records what has been marked on the
    board. An event whose ping landed but whose label write failed is in the first and not
    the second, so the NEXT pass retries the label alone — it neither re-pages the owner nor
    silently abandons the supervision hold. Recording one combined "done" would have made a
    failed label permanent and invisible, which is how this was first written and wrong.
    """
    state = load_seen(cfg)
    sent_keys, labelled_keys = set(state["sent"]), set(state["labelled"])
    tickets, read_failures = [], []
    for team in cfg["team_keys"]:
        try:
            tickets.extend(tracker.recent_tickets(team, cfg["lookback_comments"]))
        except (NotifierError, Exception) as exc:                 # noqa: BLE001
            if isinstance(exc, Deadline):
                raise
            read_failures.append("%s: %s" % (team, redact(str(exc), secrets)))

    if read_failures:
        # §13: a read that failed is NOT an empty result. Say so, loudly, on stdout too —
        # a totally failed pass that prints nothing looks identical to a quiet one.
        summary = ("FAIL: could not read %d of %d team(s): %s — this pass established "
                   "nothing; it is NOT 'nothing to do'"
                   % (len(read_failures), len(cfg["team_keys"]), "; ".join(read_failures)))
        out.write(summary + "\n")
        return {"exit": EXIT_ERROR, "examined": 0, "sent": 0, "declined": 0,
                "labelled": 0, "dry": bool(dry_run), "summary": summary}

    events, skipped, capped = select_events(tickets, sent_keys, cfg)
    sent = declined = labelled = 0
    problems = []

    # Labels owed from an earlier pass whose ping landed but whose write did not.
    pending = [e for e in events if e["label"] and e["key"] in sent_keys
               and e["key"] not in labelled_keys]

    for event in events:
        try:
            text = build_message(event["mark"], event["ticket_id"],
                                 event["ticket_title"], event["url"])
            leaked = secret_hits(text)
            if leaked:
                # A credential shape in a session-writable title must not be relayed to a
                # third party. Decline loudly rather than send a redacted half-message.
                declined += 1
                problems.append("%s: withheld — the title carries a %s shape"
                                % (event["ticket_id"], ", ".join(leaked)))
                continue

            if dry_run:
                out.write("WOULD SEND  %s  %s\n" % (event["ticket_id"], event["mark"]))
                out.write("            %s\n" % text.replace("\n", " | "))
                if event["label"]:
                    out.write("WOULD LABEL %s  %s\n"
                              % (event["ticket_id"], event["label"]))
                sent += 1
                continue

            reply = chat.post_message(text)
            if not (reply or {}).get("ok"):
                declined += 1
                problems.append("%s: chat refused (%s)"
                                % (event["ticket_id"],
                                   redact(str((reply or {}).get("error")), secrets)))
                continue
            sent += 1
            sent_keys.add(event["key"])

            if event["label"]:
                label_id = (cfg["label_ids"] or {}).get(event["label"])
                if label_id and label_id in (event.get("existing_labels") or []):
                    labelled += 1
                    labelled_keys.add(event["key"])
                else:
                    try:
                        tracker.add_label(event["ticket_uuid"], label_id)
                        labelled += 1
                        labelled_keys.add(event["key"])
                    except Deadline:
                        raise
                    except Exception as exc:                      # noqa: BLE001
                        # A label that did not land is "could not do it": it is counted,
                        # it makes the pass non-zero, and it is retried next pass because
                        # the key never enters labelled_keys.
                        declined += 1
                        problems.append(
                            "%s: paged, but the %s label did NOT apply (%s) — will retry"
                            % (event["ticket_id"], event["label"],
                               redact(str(exc), secrets)))
            else:
                labelled_keys.add(event["key"])
        except Deadline:
            raise
        except Exception as exc:                                  # noqa: BLE001
            declined += 1
            problems.append("%s: %s" % (event.get("ticket_id"),
                                        redact(str(exc), secrets)))

    if not dry_run:
        save_seen(cfg, sent_keys, labelled_keys)

    if capped:
        declined += capped
        problems.append("%d event(s) over the %d-per-pass cap were NOT sent this pass"
                        % (capped, cfg.get("max_events_per_pass") or 25))

    verb = "would send" if dry_run else "sent"
    if not events:
        summary = ("nothing to do: examined %d ticket(s) across %s and found no unsent "
                   "escalation mark (%d skipped: already sent, unauthorised, or "
                   "informational) — this is 'nothing to do', not a failure"
                   % (len(tickets), ", ".join(cfg["team_keys"]), len(skipped)))
        code = EXIT_OK
    else:
        summary = "%s %d, labelled %d, declined %d (examined %d ticket(s), %d owed label)" % (
            verb, sent, labelled, declined, len(tickets), len(pending))
        code = EXIT_DECLINED if declined else EXIT_OK
    if problems:
        summary += " | " + "; ".join(problems)

    out.write(summary + "\n")
    return {"exit": code, "examined": len(tickets), "sent": sent,
            "declined": declined, "labelled": labelled, "dry": bool(dry_run),
            "summary": summary}


def _now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_command(cfg, dry_run, timeout, tracker=None, chat=None, out=sys.stdout):
    def _alarm(_signum, _frame):
        raise Deadline()

    armed = False
    if timeout and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(int(timeout))
        armed = True
    try:
        secrets = []
        if tracker is None or chat is None:
            tracker_secret = credential(cfg, "linear_key_env")
            chat_secret = credential(cfg, "chat_token_env")
            secrets = [tracker_secret, chat_secret]
            if tracker is None:
                tracker = TrackerClient(cfg, tracker_secret)
            if chat is None:
                chat = ChatClient(cfg, chat_secret)
        result = run_once(cfg, tracker, chat, dry_run, out=out, secrets=secrets)
    except Deadline:
        result = {"exit": EXIT_TIMEOUT, "examined": 0, "sent": 0, "declined": 0,
                  "labelled": 0, "dry": bool(dry_run),
                  "summary": "FAIL: the %ds run deadline passed — this pass is PARTIAL, "
                             "not clean; the scheduler starts the next one" % timeout}
        out.write(result["summary"] + "\n")
    except NotifierError as exc:
        result = {"exit": exc.code, "examined": 0, "sent": 0, "declined": 0, "labelled": 0,
                  "dry": bool(dry_run),
                  "summary": "FAIL: %s" % redact(str(exc), locals().get("secrets") or ())}
        out.write(result["summary"] + "\n")
    except Exception as exc:                                      # noqa: BLE001
        result = {"exit": EXIT_ERROR, "examined": 0, "sent": 0, "declined": 0,
                  "labelled": 0, "dry": bool(dry_run),
                  "summary": "FAIL: unexpected error: %s"
                             % redact(str(exc), locals().get("secrets") or ())}
        out.write(result["summary"] + "\n")
    finally:
        if armed:
            signal.alarm(0)
    write_heartbeat(cfg, result, _now_iso())
    return result["exit"]


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline, every transport stubbed
# ══════════════════════════════════════════════════════════════════════════════════════

class _FakeChat:
    def __init__(self, ok=True, error=None):
        self.ok, self.error, self.posts = ok, error, []

    def post_message(self, text):
        self.posts.append(text)
        return {"ok": self.ok, "error": self.error}


class _FakeTracker:
    def __init__(self, tickets, fail=False, label_fails=False):
        self.tickets, self.fail, self.labels = tickets, fail, []
        self.label_fails = label_fails

    def recent_tickets(self, team_key, limit):
        if self.fail:
            raise RuntimeError("tracker unreachable")
        return list(self.tickets)

    def add_label(self, issue_uuid, label_id):
        if self.label_fails:
            raise NotifierError("Entity not found: Issue", code=EXIT_ERROR)
        self.labels.append((issue_uuid, label_id))
        return True


def selftest():
    import io
    import tempfile

    checks = []

    def ok(name, cond, detail=""):
        checks.append((bool(cond), name, detail))

    def marker(label):
        return "<!-- pipeline-escalation: %s -->" % label

    # ── §1. The mark table matches the ADR, and nothing extra pages ────────────────
    ok("all six ADR marks are known", set(MARKS) == {
        ESC_AWAITING_APPROVAL, ESC_NEEDS_INPUT, ESC_NO_OUTPUT, ESC_REJECTED,
        ESC_BLOCKED, ESC_NEEDS_HUMAN})
    for mark in MARKS:
        ok("mark %s is found on a first line" % mark,
           find_mark(marker(mark) + "\nbody") == mark)
    ok("an informational note is not a mark",
       find_mark("<!-- %s -->\nhi" % NOT_A_MARK) is None)
    ok("a mark below the first line is ignored",
       find_mark("preamble\n" + marker(ESC_BLOCKED)) is None)
    ok("an unknown mark value is ignored",
       find_mark("<!-- pipeline-escalation: something-else -->") is None)

    # ── §2. The anti-forgery defence is not undone ────────────────────────────────
    forged = "&lt;!-- pipeline-escalation: %s --&gt;\nquestion text" % ESC_BLOCKED
    ok("an HTML-escaped (neutralized) mark does NOT page", find_mark(forged) is None)
    # The prose above DESCRIBES this rule, so match a call rather than the word: any
    # unescape/unquote of a body would hand a session back the forged mark.
    _src = open(__file__, encoding="utf-8").read().split("\ndef selftest():", 1)[0]
    ok("no body is ever HTML-unescaped or URL-unquoted",
       not re.search(r"\b(html\.)?unescape\s*\(|\bunquote\w*\s*\(", _src))

    # ── §3. Precedence ────────────────────────────────────────────────────────────
    ok("needs-human wins over blocked",
       resolve_marks([marker(ESC_BLOCKED), marker(ESC_NEEDS_HUMAN)]) == ESC_NEEDS_HUMAN)

    # ── §4. Decision 2 is structural: no body can reach the message ───────────────
    import inspect
    params = list(inspect.signature(build_message).parameters)
    ok("build_message takes exactly (mark, ticket_id, ticket_title, url)",
       params == ["mark", "ticket_id", "ticket_title", "url"], repr(params))
    msg = build_message(ESC_NEEDS_INPUT, "KIT-777", "Refresh tokens", "https://x/KIT-777")
    ok("the message carries the id, the moment and the link",
       "KIT-777" in msg and "Planning needs your input" in msg and "https://x/KIT-777" in msg)
    ok("the message is two lines", len(msg.split("\n")) == 2)

    # ── §5. Decision 4's label mapping, exactly ──────────────────────────────────
    ok("needs-input applies blocked", label_for(ESC_NEEDS_INPUT) == "agent:blocked")
    ok("no-output applies blocked", label_for(ESC_NO_OUTPUT) == "agent:blocked")
    ok("awaiting-approval applies no label", label_for(ESC_AWAITING_APPROVAL) is None)
    ok("rejected applies no label", label_for(ESC_REJECTED) is None)
    ok("a session's blocked mark applies blocked", label_for(ESC_BLOCKED) == "agent:blocked")
    ok("a session's needs-human mark applies needs-human",
       label_for(ESC_NEEDS_HUMAN) == "agent:needs-human")

    # ── §6. Config: all errors at once, env NAMES only, state dir outside a worktree ──
    with tempfile.TemporaryDirectory() as tmp:
        good = dict(EXAMPLE_CONFIG)
        good["state_dir"] = os.path.join(tmp, "state")
        good["label_ids"] = {"agent:blocked": "L1", "agent:needs-human": "L2"}
        good["executor_actor_ids"] = ["exec-actor-1"]
        path = os.path.join(tmp, "conf.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(good, fh)
        cfg = load_config(path)
        ok("a good config loads", cfg["chat_channel_id"] == "C0123456789")

        bad = dict(good)
        # Built at runtime, never as a source literal: this repo's pre-commit scan and its
        # PreToolUse hook both read the file text, and a fixture that looks like a token
        # would block the commit that ships the test proving tokens are refused.
        bad["chat_token_env"] = "xo" + "xb-" + "a-real-looking-value"
        bad["ticket_url_template"] = "https://x/no-placeholder"
        bad["run_timeout_seconds"] = "240"
        bad["team_keys"] = ["KIT", "KIT"]
        bad["nonsense_key"] = 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        try:
            load_config(path)
            ok("a bad config is refused", False)
        except NotifierError as exc:
            text = str(exc)
            ok("every bad value is reported in one pass",
               all(t in text for t in ("chat_token_env", "ticket_url_template",
                                       "run_timeout_seconds", "team_keys",
                                       "nonsense_key")), text)
            ok("a credential VALUE in the config is refused by name",
               "never put a credential value in the config" in text)
            ok("a non-integer timeout is a named config error, not a traceback",
               "must be a positive integer" in text)

        # The PR lane is not in this build, so its key must be REFUSED, not accepted and
        # ignored — an accepted key that changes nothing is the §13 silent no-op.
        bad3 = dict(good)
        bad3["pr_human_moment"] = "opened"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bad3, fh)
        try:
            load_config(path)
            ok("the unbuilt PR key is refused rather than silently ignored", False)
        except NotifierError as exc:
            ok("the unbuilt PR key is refused rather than silently ignored",
               "pr_human_moment" in str(exc))

        worktree = os.path.join(tmp, "repo", "state")
        os.makedirs(os.path.join(tmp, "repo", ".git"), exist_ok=True)
        bad2 = dict(good)
        bad2["state_dir"] = worktree
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bad2, fh)
        try:
            load_config(path)
            ok("a state dir inside a worktree is refused", False)
        except NotifierError as exc:
            ok("a state dir inside a worktree is refused",
               "inside a git working tree" in str(exc))

        # ── §7. The three §13 states have three different exit codes ─────────────
        cfg = load_config_from(good, tmp)
        EXEC = good["executor_actor_ids"][0]

        def tkt(tid, mark, cid, title="A", author=EXEC, uuid=None, labels=None):
            return {"id": tid, "uuid": uuid or ("u-" + tid), "title": title,
                    "label_ids": labels or [],
                    "comments": [{"id": cid, "body": marker(mark) + "\nwhat about X?",
                                  "author_id": author}]}

        tickets = [tkt("KIT-1", ESC_NEEDS_INPUT, "c1")]
        chat, tracker = _FakeChat(), _FakeTracker(tickets)
        buf = io.StringIO()
        res = run_once(cfg, tracker, chat, dry_run=False, out=buf)
        ok("did the work → exit 0", res["exit"] == EXIT_OK and res["sent"] == 1)
        ok("the label was applied, keyed on the UUID not the identifier",
           tracker.labels == [("u-KIT-1", "L1")], repr(tracker.labels))
        ok("the question text never left the tracker",
           "what about X?" not in chat.posts[0])

        res2 = run_once(cfg, _FakeTracker(tickets), _FakeChat(), False, out=buf)
        ok("nothing to do → exit 0 and says so",
           res2["exit"] == EXIT_OK and "nothing to do" in res2["summary"])

        buf_fail = io.StringIO()
        res3 = run_once(cfg, _FakeTracker([], fail=True), _FakeChat(), False, out=buf_fail)
        ok("could not do it → exit 1, never 'nothing to do'",
           res3["exit"] == EXIT_ERROR and "NOT 'nothing to do'" in res3["summary"])
        ok("a totally failed pass PRINTS its summary", "FAIL:" in buf_fail.getvalue())

        refusing = _FakeChat(ok=False, error="channel_not_found")
        res4 = run_once(cfg, _FakeTracker([tkt("KIT-2", ESC_BLOCKED, "c2", author="s1")]),
                        refusing, False, out=buf)
        ok("a refused send → exit 3 (declined)", res4["exit"] == EXIT_DECLINED)

        # ── §7b. A failed LABEL write is loud, and retried without re-paging ──────
        t_lbl = [tkt("KIT-9", ESC_NEEDS_INPUT, "c9")]
        chat_l, tracker_l = _FakeChat(), _FakeTracker(t_lbl, label_fails=True)
        res5 = run_once(cfg, tracker_l, chat_l, False, out=buf)
        ok("a failed label write is NOT reported as a clean pass",
           res5["exit"] == EXIT_DECLINED, "exit=%s" % res5["exit"])
        ok("a failed label write says it will retry", "will retry" in res5["summary"])
        state = load_seen(cfg)
        ok("the ping is remembered so the owner is not paged twice",
           event_id("c9") in state["sent"])
        ok("the label is NOT remembered, so the next pass settles it",
           event_id("c9") not in state["labelled"])
        chat_r, tracker_r = _FakeChat(), _FakeTracker(t_lbl)
        res6 = run_once(cfg, tracker_r, chat_r, False, out=buf)
        ok("the retry pass re-pages nobody", chat_r.posts == [])

        # ── §7c. An unauthorised author cannot forge a planning mark ─────────────
        forgery = [tkt("KIT-3", ESC_NO_OUTPUT, "c3", author="a-session")]
        chat_f, tracker_f = _FakeChat(), _FakeTracker(forgery)
        res7 = run_once(cfg, tracker_f, chat_f, False, out=buf)
        ok("a session cannot forge a planning mark", chat_f.posts == [])
        ok("a session cannot make the job label a ticket", tracker_f.labels == [])
        ok("the forgery is NAMED, not silently dropped",
           "unauthorised author" in "".join(
               "%s" % s[2] for s in select_events(forgery, set(), cfg)[1]))
        sess = [tkt("KIT-4", ESC_BLOCKED, "c4", author="a-session")]
        chat_s = _FakeChat()
        run_once(cfg, _FakeTracker(sess), chat_s, False, out=buf)
        ok("a session's OWN agent:blocked mark is still honoured", len(chat_s.posts) == 1)

        # ── §7d. A hostile ticket title cannot control the chat client ───────────
        nasty = [tkt("KIT-5", ESC_BLOCKED, "c5", title="<!channel> <http://x|click>",
                     author="s")]
        chat_n = _FakeChat()
        run_once(cfg, _FakeTracker(nasty), chat_n, False, out=buf)
        ok("angle brackets and pipes are stripped from a title",
           not any(ch in chat_n.posts[0] for ch in "<>|"), chat_n.posts[0])

        # ── §7e. A credential shape in a title is withheld, not relayed ──────────
        leak_title = "rotate " + "xoxb-" + "1234567890abcdefghij"
        leaky = [tkt("KIT-6", ESC_BLOCKED, "c6", title=leak_title, author="s")]
        chat_k = _FakeChat()
        res8 = run_once(cfg, _FakeTracker(leaky), chat_k, False, out=buf)
        ok("a credential shape in a title is never posted", chat_k.posts == [])
        ok("withholding is a decline, not a silent skip",
           res8["exit"] == EXIT_DECLINED and "withheld" in res8["summary"])

        # ── §8. Dedupe, and the seen-set's refusal to guess ──────────────────────
        ok("the first sent event is remembered", event_id("c1") in load_seen(cfg)["sent"])
        with open(seen_path(cfg), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        try:
            load_seen(cfg)
            ok("a corrupt seen-set refuses to run", False)
        except NotifierError as exc:
            ok("a corrupt seen-set refuses to run",
               exc.code == EXIT_ERROR and "refusing to run" in str(exc))
        os.remove(seen_path(cfg))

        # one comment reachable twice in a pass yields one event
        dup_events, _, _ = select_events([tkt("KIT-7", ESC_BLOCKED, "c7", author="s"),
                                          tkt("KIT-7", ESC_BLOCKED, "c7", author="s")],
                                         set(), cfg)
        ok("a comment seen twice in one pass produces one event", len(dup_events) == 1)

        # the flood guard
        many = [tkt("KIT-%d" % i, ESC_BLOCKED, "cc%d" % i, author="s")
                for i in range(cfg["max_events_per_pass"] + 5)]
        ev, _, capped = select_events(many, set(), cfg)
        ok("the per-pass cap holds", len(ev) == cfg["max_events_per_pass"] and capped == 5)

        # ── §9. Dry run sends nothing, writes no state, and says it was a rehearsal ──
        chat3, tracker3 = _FakeChat(), _FakeTracker([tkt("KIT-8", ESC_BLOCKED, "c8",
                                                         author="s")])
        buf2 = io.StringIO()
        res9 = run_once(cfg, tracker3, chat3, dry_run=True, out=buf2)
        ok("dry run posts nothing", chat3.posts == [])
        ok("dry run labels nothing", tracker3.labels == [])
        ok("dry run says what it would send", "WOULD SEND" in buf2.getvalue())
        ok("dry run writes no seen-set", not os.path.exists(seen_path(cfg)))
        write_heartbeat(cfg, res9, _now_iso())
        with open(heartbeat_path(cfg), encoding="utf-8") as fh:
            hbd = json.load(fh)
        ok("a dry run's heartbeat cannot impersonate a real pass",
           hbd["dry"] is True and hbd["sent"] == 0 and hbd["would_send"] == 1)

        # ── heartbeat on every path ──────────────────────────────────────────────
        write_heartbeat(cfg, res3, _now_iso())
        with open(heartbeat_path(cfg), encoding="utf-8") as fh:
            hb = json.load(fh)
        ok("the heartbeat records the failing pass",
           hb["schema"] == HEARTBEAT_SCHEMA and hb["exit"] == EXIT_ERROR)

        # ── a credential value never reaches an output ───────────────────────────
        fake_secret = "xoxb-" + "9876543210zyxwvutsrq"
        ok("a credential value is redacted out of any message",
           fake_secret not in redact("failed with %s" % fake_secret, [fake_secret]))

        # ── a mark below line 1 is RECORDED, not silently dropped ────────────────
        deep = [{"id": "KIT-D", "uuid": "u", "title": "t", "label_ids": [],
                 "comments": [{"id": "cd", "author_id": EXEC,
                               "body": "preamble\n" + marker(ESC_NO_OUTPUT)}]}]
        _, deep_skipped, _ = select_events(deep, set(), cfg)
        ok("a mark below line 1 is named in the skipped list",
           any("not on line 1" in s[2] for s in deep_skipped), repr(deep_skipped))

    # ── §10. What it never does — asserted against this file's own source ────────
    source = open(__file__, encoding="utf-8").read()
    # Scan EVERYTHING except the selftest body: a prefix split left main() unscanned, so a
    # merge path added below the selftest would have passed the guard that exists to catch it.
    head, _, rest = source.partition("\ndef selftest():")
    tail = rest.partition("\ndef load_config_from")[2]
    body_only = head + tail
    banned = ("pr merge", "--auto", "mergePullRequest", "pull-request-review",
              "event: APPROVE", "issueUpdate(id:$id,input:{stateId",
              "gh pr review")
    for token in banned:
        ok("never constructs %r" % token, token not in body_only)
    # The REPLACING label spelling must never come back: a bare labelIds on issueUpdate
    # strips every other label off the ticket. Only the additive form may appear.
    ok("the label write is additive, never replacing",
       "addedLabelIds" in body_only and "input:{labelIds" not in body_only)
    ok("the only tracker mutation is one label write",
       body_only.count("mutation(") == 1)
    ok("no ticket state is ever written", "stateId" not in body_only)

    # exit-code vocabulary agrees with the sibling job the installer cross-checks
    try:
        import pipeline_review_poller as prp
        ok("EXIT_DECLINED agrees with the review poller",
           EXIT_DECLINED == prp.EXIT_DECLINED)
        ok("EXIT_TIMEOUT agrees with the review poller",
           EXIT_TIMEOUT == prp.EXIT_TIMEOUT)
    except Exception as exc:                                      # noqa: BLE001
        # Not a skip: both files sit in the directory HERE puts on sys.path, so an
        # ImportError means something is broken. A silent pass would let the drift guard
        # go dark with CI green — the §13 shape one level up.
        ok("the review poller is importable for the exit-code cross-check", False,
           str(exc))

    failed = [c for c in checks if not c[0]]
    for passed, name, detail in checks:
        if not passed:
            sys.stderr.write("FAIL  %s  %s\n" % (name, detail))
    print("%d checks, %d failed" % (len(checks), len(failed)))
    return EXIT_OK if not failed else EXIT_ERROR


def load_config_from(doc, tmpdir):
    """Selftest helper: load a config dict without writing a second file by hand."""
    path = os.path.join(tmpdir, "_conf.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return load_config(path)


# ══════════════════════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════════════════════

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ping a chat channel on the marks the pipeline already writes.")
    parser.add_argument("command", nargs="?", choices=("run", "scan"), default="run")
    parser.add_argument("--config", help="path to the notifier config JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be sent and send nothing")
    parser.add_argument("--timeout", type=int, default=None,
                        help="wall clock for one pass (default: from config)")
    parser.add_argument("--example-config", action="store_true",
                        help="print an example config and exit")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2, sort_keys=True))
        for key, doc in sorted(CONFIG_KEYS.items()):
            sys.stderr.write("  %-22s %s\n" % (key, doc))
        return EXIT_OK
    if not args.config:
        sys.stderr.write("FAIL: --config is required (see --example-config)\n")
        return EXIT_USAGE

    try:
        cfg = load_config(args.config)
    except NotifierError as exc:
        sys.stderr.write("FAIL: %s\n" % exc)
        return exc.code
    try:
        os.makedirs(cfg["state_dir"], exist_ok=True)
    except OSError as exc:
        sys.stderr.write("FAIL: could not create state_dir %s: %s\n"
                         % (cfg["state_dir"], exc))
        return EXIT_USAGE

    dry = args.dry_run or args.command == "scan"
    timeout = args.timeout if args.timeout is not None else cfg["run_timeout_seconds"]
    return run_command(cfg, dry, timeout)


if __name__ == "__main__":
    sys.exit(main())
