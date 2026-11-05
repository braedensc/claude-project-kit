#!/usr/bin/env python3
"""The notifier's pages that come from files, not ticket comments (KIT-336).

WHAT THIS IS

  Every other page the notifier sends starts from a mark on a ticket comment. Some facts are
  not comments at all. The review poller writes one outcome record per pull request it
  reviews, under its own role account, in its own state directory, where no session can
  write. So a review's pages are read from those records and never from a comment, which a
  session can write: the notifier skips a review mark in a comment, by name.

  This file reads; it writes nothing, sends nothing and imports nothing of the kit's. The
  notifier imports it, so it cannot import the notifier: the marks below are spelled here,
  and the notifier's selftest pins them to its own table.

THE SOURCES

  Each one is `fn(cfg, sent_keys, now) -> (events, problems)`:

    events    every page it owes, none whose key is in `sent_keys`. Each is a dict: key, mark,
              ticket_id, ticket_title, url, detail, and `at`, the time it is ordered by. The
              notifier adds the rest, and a page's words are still only its mark's.
    problems  each thing it could not read, named. "Could not look" is a problem here, never
              an empty list (§13).

  `file_events` asks every source and keeps the oldest pages that fit the pass's room.

THE REVIEW POLLER'S RECORDS

  <review_state_dir>/outcomes/<owner>__<repo>__pr-<n>.json, schema pipeline-review-outcome/1
  (pipeline_review_poller.outcome_artifact). Three kinds page, each once per pull request and
  head (a record with no head, once per review), and only while the record is under two days
  old, so a first pass does not page every review on file:

    review-declined   `usable` is false: the review couldn't run. The page names why, from a
                      fixed list of the reasons the poller writes (DECLINED_REASONS), never in
                      the record's own words. A reason not on the list pages as `unnamed`.
    review-partial    `usable` is true and `coverage` is "partial": the reviewer never saw some
                      files, and the page says how many. Not at once: the poller reviews those
                      files on their own (KIT-425) and folds that review into the same record.
                      So a partial record pages only once it has stood PARTIAL_SETTLE_SECONDS
                      unchanged, which is past that review's own timeout.
    review-left-behind  a partial record the poller marked `left_behind` (KIT-455): the PR
                      moved on, so no review of those files is coming. It pages at once,
                      under review-partial's key, so that page never follows it.

THE STATUS FILE (KIT-350)

  The status job's one file (contract §16, `status_file`; empty is off). Three problems only
  GitHub sees, each paged once, while its time is under two days old:

    conflict-gave-up     an open PR's `conflict_gave_up`: the conflict loop gave up on that
                         episode, and the PR still conflicts. Once per PR and episode.
    union-red            an open PR's `union_red`: it passes alone and fails with the other
                         open PRs. Once per PR head: a verdict's key changes on each merge.
                         The status job keeps only a verdict on the PR's current head, so a
                         push that fixed it pages nothing.
    session-ended-empty  an open piece with no PR, no question flag, and a newest session that
                         ended. Once per session, and only when `asked(ticket, since)` says no
                         question was asked since it began: yes pages nothing, and "can't tell"
                         (None) pages nothing and is a problem. Its link is the ticket. One
                         paged already is not asked about again.

  A session the status file marks `unknown` is passed over without a word: the status job
  says its own listing was cut short, and its own heartbeat carries that.

  Every source now takes a fourth argument, `asked`; the review source does not use it.
"""
import calendar
import json
import os
import re
import time

# pipeline_review_poller.OUTCOME_SCHEMA; the notifier's selftest pins the two together.
OUTCOME_SCHEMA = "pipeline-review-outcome/1"
DECLINED_MARK = "review-declined"
PARTIAL_MARK = "review-partial"
LEFT_BEHIND_MARK = "review-left-behind"                  # KIT-455
# Records older than this are history, not news.
WINDOW_SECONDS = 2 * 24 * 3600
# The poller's own review of withheld files takes one pass to start, at most its reviewer
# timeout (30 minutes) to answer, and one pass to fold in. Two hours is well past all three.
PARTIAL_SETTLE_SECONDS = 2 * 3600
UNNAMED = "unnamed"
# Each reason the poller and its publisher decline with, as words found in it, and the key the
# page words it by. First match wins, so the poller's own unexpected error is read before the
# "gave up" its retries end with. The selftest checks every phrase is still in their source.
DECLINED_REASONS = (
    ("no review basis could be established", "no-criteria"),
    ("was not found in team", "no-ticket"),
    ("diff too large to deliver", "too-large"),
    ("the pull request diff is empty", "empty"),
    ("the head moved since the earlier part", "moved"),
    ("timed out waiting for the reviewer", "timed-out"),
    ("could not be read after the", "timed-out"),
    ("the reviewer session ended", "failed"),
    ("the reviewer session reported an error", "failed"),
    ("carried no pipeline-review/1 block", "failed"),
    ("did not conform to pipeline-review/1", "failed"),
    ("review ticket id missing from state", "failed"),
    ("the poller hit an unexpected error", "failed"),
    ("review basis tampered", "tampered"),
    ("could NOT review this change", "could-not"),
    ("possible secret in review output", "secret"),
    ("lives in a fork", "fork"),
    ("cross-repository flag could not be", "fork"),
    ("could not be fetched", "unreadable"),
    ("gave up after", "unreadable"),
)
# The shapes a record must have before anything in it reaches a page.
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
HEAD_RE = re.compile(r"^[0-9a-f]{0,64}\Z")
TICKET_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,7}\Z")
AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# KIT-350: pipeline_status_local.STATUS_SCHEMA, and the three marks the status file pages.
STATUS_SCHEMA = "pipeline-status/1"
CONFLICT_MARK = "conflict-gave-up"
UNION_MARK = "union-red"
SESSION_MARK = "session-ended-empty"
SESSION_ENDED = ("complete", "error", "stale")
UNION_KEY_RE = re.compile(r"^[0-9a-f]{16}\Z")
QUESTION_FLAGS = ("agent:blocked", "agent:needs-human")


def declined_why(reason):
    """The key a decline reason is worded by, or UNNAMED."""
    text = reason if isinstance(reason, str) else ""
    for words, key in DECLINED_REASONS:
        if words in text:
            return key
    return UNNAMED


def _at_seconds(text):
    try:
        return calendar.timegm(time.strptime(text, AT_FORMAT))
    except (TypeError, ValueError):
        return None


def _object(doc, key):
    """doc[key] when it is an object, or an empty one: a value out of shape reads as absent."""
    value = doc.get(key)
    return value if isinstance(value, dict) else {}


def _read_record(path):
    """(record, why): the record, or None and what is wrong with it."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "could not be read (%s)" % type(exc).__name__
    if not isinstance(doc, dict) or doc.get("outcome_schema") != OUTCOME_SCHEMA:
        return None, "is not a %s record" % OUTCOME_SCHEMA
    repo, pr, head = doc.get("repo"), doc.get("pr"), doc.get("head_sha")
    if not (isinstance(repo, str) and REPO_RE.match(repo)):
        return None, "names no repository in owner/name form"
    if type(pr) is not int or pr < 1:
        return None, "names no pull request number"
    if not (isinstance(head, str) and HEAD_RE.match(head)):
        return None, "names no head in its shape"
    if not (isinstance(doc.get("ticket_id"), str) and TICKET_ID_RE.match(doc["ticket_id"])):
        return None, "names no ticket in its shape"
    if type(doc.get("usable")) is not bool:
        return None, "does not say whether the review was usable"
    if _at_seconds(doc.get("at")) is None:
        return None, "carries no time in %s form" % AT_FORMAT
    withheld = doc.get("withheld_files")
    if doc.get("usable") and doc.get("coverage") == "partial" and not (
            isinstance(withheld, list) and withheld):
        return None, "says partial but names no withheld file"
    return doc, ""


def review_outcome_events(cfg, sent_keys, now, asked=None):
    """The review poller's records as pages: a review that couldn't run, and one that left
    files unread. Off, with nothing said, while `review_state_dir` is empty."""
    root = cfg.get("review_state_dir") or ""
    if not root:
        return [], []
    if not os.path.isdir(root):
        return [], ["the review poller's state directory %s is not there, so no review's "
                    "record was read" % root]
    folder = os.path.join(root, "outcomes")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except FileNotFoundError:
        return [], []                     # the poller has written no record yet
    except OSError as exc:
        return [], ["the review records in %s could not be listed (%s)"
                    % (folder, type(exc).__name__)]
    events, problems = [], []
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        doc, why = _read_record(path)
        if doc is None:
            try:
                fresh = os.path.getmtime(path) >= now - WINDOW_SECONDS
            except OSError:
                fresh = True
            if fresh:
                problems.append("the review record %s %s, so it was not paged" % (name, why))
            continue
        age = now - _at_seconds(doc["at"])
        if age > WINDOW_SECONDS:
            continue
        if not doc["usable"]:
            kind, mark, detail = "declined", DECLINED_MARK, {"why": declined_why(doc.get("reason"))}
        elif doc.get("coverage") == "partial":
            left = doc.get("left_behind")
            if isinstance(left, dict) and _at_seconds(left.get("at")) is not None:
                # KIT-455: the PR moved on, so no review of these files is coming: page at
                # once, under the partial page's own key, so that page never follows.
                mark = LEFT_BEHIND_MARK
            elif age < PARTIAL_SETTLE_SECONDS:
                continue                  # its withheld files may still get their own review
            else:
                mark = PARTIAL_MARK
            kind, detail = "partial", {"files": len(doc["withheld_files"])}
        else:
            continue
        # An older poller wrote head_sha "", so a record with no head is keyed by its time: a
        # new review of the same pull request still pages, and the same one read again doesn't.
        head = doc["head_sha"] or "at:" + doc["at"]
        key = "review:%s#%d@%s:%s" % (doc["repo"].lower(), doc["pr"], head, kind)
        if key in sent_keys:
            continue
        events.append({"key": key, "mark": mark, "ticket_id": doc["ticket_id"],
                       "ticket_title": "", "detail": detail, "at": doc["at"],
                       "url": "https://github.com/%s/pull/%d" % (doc["repo"], doc["pr"])})
    return events, problems


def status_file_events(cfg, sent_keys, now, asked):
    """The status file's three problems only GitHub sees, as pages (KIT-350). Off, with
    nothing said, while `status_file` is empty. Fields only: a page's words are its mark's."""
    path = cfg.get("status_file") or ""
    if not path:
        return [], []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return [], ["the status file %s is not there, so no GitHub problem was read" % path]
    except (OSError, ValueError, RecursionError) as exc:
        return [], ["the status file %s could not be read (%s)" % (path, type(exc).__name__)]
    if not isinstance(doc, dict) or doc.get("schema") != STATUS_SCHEMA:
        return [], ["the status file %s is not a %s document" % (path, STATUS_SCHEMA)]
    events, problems = [], []

    def fresh(at):
        seconds = _at_seconds(at) if isinstance(at, str) else None
        return seconds is not None and now - seconds <= WINDOW_SECONDS

    def page(key, mark, ticket_id, url, at):
        if key not in sent_keys:
            events.append({"key": key, "mark": mark, "ticket_id": ticket_id, "ticket_title": "",
                           "detail": {}, "at": at, "url": url})

    for plan in doc.get("plans") if isinstance(doc.get("plans"), list) else []:
        pieces = plan.get("pieces") if isinstance(plan, dict) else None
        for piece in pieces if isinstance(pieces, list) else []:
            tid = piece.get("id") if isinstance(piece, dict) else None
            if not (isinstance(tid, str) and TICKET_ID_RE.match(tid)):
                continue
            prs = piece.get("prs") if isinstance(piece.get("prs"), list) else []
            for pr in prs:
                if not isinstance(pr, dict) or pr.get("state") != "open":
                    continue
                repo, number = pr.get("repo"), pr.get("number")
                if not (isinstance(repo, str) and REPO_RE.match(repo) and type(number) is int):
                    continue
                gave = _object(pr, "conflict_gave_up")
                if type(gave.get("episode")) is int and fresh(gave.get("at")):
                    page("status:conflict:%s#%d@ep%d" % (repo.lower(), number, gave["episode"]),
                         CONFLICT_MARK, tid, pr.get("url"), gave["at"])
                red = _object(pr, "union_red")
                key, head = red.get("key"), pr.get("head_sha")
                # Once per head of the PR. The verdict's key hashes main and every open head,
                # so it changes on each merge, and a PR stuck red would page after every one.
                if (isinstance(key, str) and UNION_KEY_RE.match(key)
                        and isinstance(head, str) and HEAD_RE.match(head) and head
                        and fresh(red.get("at"))):
                    page("status:union:%s#%d@%s" % (repo.lower(), number, head),
                         UNION_MARK, tid, pr.get("url"), red["at"])
            session = _object(piece, "session")
            flags = piece.get("flags") if isinstance(piece.get("flags"), list) else []
            # Compared one by one, never hashed: an odd value in the list is not a flag.
            if (piece.get("state_type") in ("completed", "canceled") or prs
                    or any(flag in QUESTION_FLAGS for flag in flags)
                    or session.get("status") not in SESSION_ENDED
                    or not isinstance(session.get("id"), str)):
                continue
            at = session.get("ended_at") or session.get("created_at")
            if not fresh(at):
                continue
            key = "status:session:%s@%s" % (tid, session["id"])
            if key in sent_keys:
                continue                    # paged already: nothing is left to ask about it
            said = asked(tid, session.get("created_at")) if asked else None
            if said is None:
                problems.append("%s: whether its ended session asked a question could not be "
                                "told this pass, so it was not paged" % tid)
            elif not said:
                page(key, SESSION_MARK, tid, piece.get("url"), at)
    return events, problems


SOURCES = (review_outcome_events, status_file_events)


def file_events(cfg, sent_keys, now, room, asked=None):
    """(events, problems, held): every source's pages, oldest first, as many as `room` takes,
    and how many more it held back for a later pass."""
    events, problems = [], []
    for source in SOURCES:
        got, said = source(cfg, sent_keys, now, asked)
        events.extend(got)
        problems.extend(said)
    events.sort(key=lambda e: str(e.get("at") or ""))
    keep = events[:max(0, room)]
    return keep, problems, len(events) - len(keep)
