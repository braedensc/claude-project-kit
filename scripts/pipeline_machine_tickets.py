#!/usr/bin/env python3
"""Recognising the pipeline's own machine tickets, and reading the dispatcher's routing note.

WHY THIS FILE EXISTS

  Planning now lives on each project's own work team (KIT-184): the planner job files a
  planning ticket beside the team's real work, and the dispatcher routes it by a
  `[repo=…]` tag and a routing label instead of by a team of its own. A separate team used
  to keep every other job away from planning tickets by accident — they only watch the
  work teams. Built in, each of those jobs must skip a planning ticket on purpose: the
  review poller's discovery, the criteria snapshot, the finding poller, and the executor's
  duplicate check. Four jobs, one definition, here.

  Review tickets follow (KIT-185): they leave the shared Reviews team for the work teams
  too, so the same jobs must tell a review ticket apart on purpose as well. Its definition
  is here beside the planning one, with the three files the review lane shares or keeps:
  the review poller's own record, the routing sign-off, and the stop.

WHAT A PLANNING TICKET IS

  One of two things marks it, and either is enough:

    - its description OPENS with the planning tag `[repo=stage-a-planning-…]` (or the
      `\\[…\\]` escaped form the tracker may store), which is the first line the planner
      job writes and the tag the dispatcher routes on;
    - it carries a label whose name starts `stage-a-planning-`, the routing label only
      one repository's planning entry claims.

  Never the title. A title is whatever a person typed, and "Plan the release" is a coding
  ticket.

  BOTH MARKS ARE WRITABLE BY A CODING SESSION. A session holds the tracker's tools, so it can
  put the label on its own ticket or edit its own first line. That is harmless where a skip
  only spares a job some work (the finding poller, the executor's duplicate list). It is not
  harmless where the skip would take a ticket out of REVIEW: a session could switch off its
  own review. So the review poller's discovery and the criteria snapshot skip a ticket only
  when `routed_to_planning` also confirms, from the dispatcher's own routing note, that the
  session really ran in a planning entry. A session cannot forge that note: the dispatcher
  posts it before the session's model runs.

THE ROUTING NOTE

  When the dispatcher (Cyrus 0.2.69) starts a session it posts a thought:

      **Routing** (<method>)
      - **<entry name>** → `<branch>` (<source>)

  (`ActivityPoster.postRoutingActivity`, `EdgeWorker.createCyrusAgentSession`.) It is the
  only outside signal of WHICH dispatcher entry answered a ticket. Three properties decide
  how it may be read:

    - It is posted AFTER the worktree exists and BEFORE the runner starts, so no model has
      run when it lands. The model's own text is later posted as thoughts too, and a model
      could imitate the note, so only the EARLIEST routing-shaped thought counts.
    - It is best-effort: a failed post is only logged. So an absent note is a failure to
      confirm, never "fine".
    - It names the entry by NAME, which the dispatcher does not force to be unique. The
      installers keep planning entry names unique and refuse a routing tag that would
      match a second entry.

  `routing_verdict` is the one reading of it, used by the planner job and by the
  installer's probe sign-off.

WHAT A REVIEW TICKET IS (KIT-185)

  The same two marks, with the review prefix: a description that OPENS with
  `[repo=reviews-…]`, or a label whose name starts `reviews-`. Never the title.

  A mark is enough only where a skip spares work: the finding poller, the executor's
  duplicate list, the catch-up poller, the queue. Where a skip would take a ticket out of
  review — the review poller's discovery and the criteria snapshot — the mark is not
  enough, and nor is the routing note: the owner chose the review poller's own record
  (11 Nov). The poller writes it, no session can, and it costs no query to read.

THE REVIEW POLLER'S OWN RECORD

  `<the poller's state_dir>/review-tickets.json`: every review ticket the poller filed, by
  the tracker's issue id, with its identifier, repository and pull request. It only grows.
  An ABSENT file is an empty record — the poller writes it with its first ticket. A file
  that is there and cannot be read raises `RecordError`: unread is never empty.

THE ROUTING SIGN-OFF AND THE STOP, SHARED WITH THE IDEA GATE

  One probe proves both lanes (the owner's decision of 11 Nov). The idea gate's installer
  signs it, and writes what it proved to `~/.stage-a/routing-signoff.json` under the role
  account: when, on which dispatcher version, and which entries were reached by which
  route. The review poller reads that file, and files nothing for a repository whose review
  entry it does not show reached by BOTH its tag and its label alone.

  The stop is the planner job's own `~/.stage-a/state/stop.json`. The planner stops all
  planning while one stands. A review misroute writes it when none stands, so planning
  stops too, and the review poller pauses the repository a stop names. Only a probe
  signed after the stop clears it, so a stop dated the same second still holds. A stop that
  cannot be read counts as stopped, and names no repository, so it pauses every one.

Usage:
    pipeline_machine_tickets.py --selftest
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

# The prefix every planning entry's id, name and routing label carries. The review
# installer's `_is_planning_entry` reads the same prefix (PR #155), so it must not change.
PLANNING_PREFIX = "stage-a-planning-"

# The prefix every review entry's id, name and routing label carries (KIT-185). The review
# poller and the Stage E installer each spell it `REVIEW_ENTRY_PREFIX`; the selftest reads
# both files and pins them to this one.
REVIEW_PREFIX = "reviews-"

# The dispatcher's display names for the two routes a planning ticket may take. A tag
# match is the intended route; a label match is the second net, for a tag that failed to
# fetch or match. Every other route — team, project, catch-all — lands a planning ticket
# somewhere it was never meant to go.
METHOD_TAG = "[repo=...] tag"
METHOD_LABEL = "Label routing"
PLANNING_METHODS = (METHOD_TAG, METHOD_LABEL)
# The same two routes are the only right ones for a review ticket.
ROUTE_METHODS = PLANNING_METHODS

_TAG_OPEN_RE = re.compile(r"^\\?\[repo=" + re.escape(PLANNING_PREFIX), re.IGNORECASE)
_REVIEW_TAG_OPEN_RE = re.compile(r"^\\?\[repo=" + re.escape(REVIEW_PREFIX), re.IGNORECASE)
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_BACKSLASH_ESCAPE_RE = re.compile(r"\\(.)")
_NOTE_HEAD_RE = re.compile(r"^\*\*Routing\*\*(?:\s*\((?P<method>[^)\n]*)\))?\s*$")
_NOTE_LINE_RE = re.compile(r"^-\s+\*\*(?P<name>[^*\n]+)\*\*\s+→\s")


def planning_entry_name(repo):
    """The planning entry's id, name and routing label for `owner/name`: the prefix plus
    the repository's NAME, lower-cased. One definition, read by the job and the installer,
    so the tag the job writes and the entry the installer composes cannot drift apart."""
    name = str(repo or "").rsplit("/", 1)[-1].strip().lower()
    if not name or not _REPO_NAME_RE.match(name):
        raise ValueError("%r is not owner/name" % (repo,))
    return PLANNING_PREFIX + name


def planning_tag(entry_name):
    return "[repo=%s]" % entry_name


def is_planning_ticket(description=None, label_names=()):
    """True when a ticket is a planning ticket, by its opening tag or its routing label."""
    desc = (description or "").lstrip()
    if _TAG_OPEN_RE.match(desc):
        return True
    return any(str(name or "").startswith(PLANNING_PREFIX) for name in (label_names or ()))


def label_names_of(issue):
    """The label names on an issue node shaped `labels { nodes { name } }`."""
    nodes = (((issue or {}).get("labels") or {}).get("nodes")) or []
    return [n.get("name") or "" for n in nodes if isinstance(n, dict)]


def issue_is_planning_ticket(issue):
    """`is_planning_ticket` over an issue node carrying `description` and/or `labels`."""
    issue = issue or {}
    return is_planning_ticket(issue.get("description"), label_names_of(issue))


def is_review_ticket(description=None, label_names=()):
    """True when a ticket carries a review ticket's marks: its description OPENS with the
    review tag, or it carries a label whose name starts `reviews-`. A mark is something a
    coding session can write on its own ticket, so a job whose skip would take a ticket out
    of review reads the review poller's record instead (`in_review_record`)."""
    desc = (description or "").lstrip()
    if _REVIEW_TAG_OPEN_RE.match(desc):
        return True
    return any(str(name or "").startswith(REVIEW_PREFIX) for name in (label_names or ()))


def issue_is_review_ticket(issue):
    """`is_review_ticket` over an issue node carrying `description` and/or `labels`."""
    issue = issue or {}
    return is_review_ticket(issue.get("description"), label_names_of(issue))


def is_machine_ticket(description=None, label_names=()):
    """A planning ticket or a review ticket, by its marks: for a job whose skip only spares
    it work."""
    return (is_planning_ticket(description, label_names)
            or is_review_ticket(description, label_names))


def issue_is_machine_ticket(issue):
    """`is_machine_ticket` over an issue node carrying `description` and/or `labels`."""
    issue = issue or {}
    return is_machine_ticket(issue.get("description"), label_names_of(issue))


def parse_routing_note(body):
    """{"method": str or None, "entries": [names]} for a routing-note body, or None when
    the body is not one. Backslash escapes the tracker may add are removed first."""
    text = _BACKSLASH_ESCAPE_RE.sub(r"\1", str(body or "")).strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    head = _NOTE_HEAD_RE.match(lines[0])
    if not head:
        return None
    entries = []
    for line in lines[1:]:
        m = _NOTE_LINE_RE.match(line)
        if not m:
            return {"method": head.group("method"), "entries": entries, "unparsed": line}
        entries.append(m.group("name").strip())
    return {"method": head.group("method"), "entries": entries}


def is_routing_shaped(body):
    """Whether a thought's body opens like a routing note, parseable or not. The earliest
    such thought is the one that counts — a later one may be a model's imitation."""
    text = _BACKSLASH_ESCAPE_RE.sub(r"\1", str(body or "")).lstrip()
    return text.startswith("**Routing**")


def earliest_routing_thought(activities):
    """The earliest activity whose content is a thought shaped like a routing note, or
    None. `activities` are nodes `{createdAt, content: {__typename, body}}`, in any order."""
    thoughts = [a for a in activities or []
                if ((a or {}).get("content") or {}).get("__typename")
                == "AgentActivityThoughtContent"
                and is_routing_shaped(((a.get("content") or {}).get("body")))]
    if not thoughts:
        return None
    return sorted(thoughts, key=lambda a: str(a.get("createdAt") or ""))[0]


# The session's thoughts, oldest first as asked. `routed_to_planning` refuses to confirm
# anything from a list it could not read whole, so the unknown page order can only make it
# say "not confirmed" — the safe answer, which leaves the ticket in review.
Q_SESSION_THOUGHTS = """
query SessionRoutingThoughts($id: String!) {
  agentSession(id: $id) {
    activities(first: 50, orderBy: createdAt, filter: { type: { in: ["thought"] } }) {
      nodes { createdAt content { __typename ... on AgentActivityThoughtContent { body } } }
      pageInfo { hasNextPage }
    }
  }
}"""


def routed_to_planning(data):
    """True only when the dispatcher's own routing note, read from a `SessionRoutingThoughts`
    answer, says the session ran in a planning entry — and only that one.

    The note is the earliest routing-shaped thought, and it must name exactly one entry,
    starting with the planning prefix. Its route may be the tag, the label, or none (a
    later session on a ticket already routed reuses the cached route and names no method).
    A list read partly, no note, or anything else is NOT confirmed."""
    conn = (((data or {}).get("agentSession") or {}).get("activities")) or {}
    if (conn.get("pageInfo") or {}).get("hasNextPage"):
        return False
    note = earliest_routing_thought(conn.get("nodes") or [])
    if note is None:
        return False
    parsed = parse_routing_note((note.get("content") or {}).get("body"))
    if not parsed or parsed.get("unparsed"):
        return False
    names = parsed["entries"]
    return len(names) == 1 and names[0].startswith(PLANNING_PREFIX)


def routing_verdict(note_body, want_entry, what="a planning ticket"):
    """(ok, reason). ok only when the note names exactly one entry, that entry is
    `want_entry`, and the route was the tag or the label. None is a failure: the note is
    best-effort, so its absence proves nothing about where the session runs. `what` names
    the ticket in the reasons: the review lane passes "a review ticket"."""
    if note_body is None:
        return False, "no routing note from the dispatcher"
    parsed = parse_routing_note(note_body)
    if parsed is None:
        return False, "the routing note could not be read"
    if parsed.get("unparsed"):
        return False, "the routing note has a line that could not be read: %r" % parsed["unparsed"][:120]
    names = parsed["entries"]
    if len(names) != 1:
        return False, ("the routing note names %d setups (%s); %s must reach "
                       "exactly one" % (len(names), ", ".join(names) or "none", what))
    if names[0] != want_entry:
        return False, "the routing note names %r, not %r" % (names[0], want_entry)
    method = (parsed.get("method") or "").strip()
    if method not in ROUTE_METHODS:
        return False, ("the routing note says the route was %r; %s must be "
                       "routed by its tag or its label" % (method or "not stated", what))
    return True, "routed to %s by %s" % (names[0], method)


# --------------------------------------------------------------------------- #
# Files the review lane keeps or shares (KIT-185). Written atomically, mode 600, in a
# folder made mode 700: the same shape the planner job and the review poller use.
# --------------------------------------------------------------------------- #
class RecordError(Exception):
    """A record or a sign-off could not be read or written. Never read as "nothing"."""


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path, doc):
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# The review poller's own record of every review ticket it filed. Only that job writes it:
# no session can, so it is what confirms a skip that would take a ticket out of review.
REVIEW_RECORD_NAME = "review-tickets.json"
REVIEW_RECORD_SCHEMA = "pipeline-review-tickets/1"


def review_record_path(state_dir):
    return os.path.join(os.path.expanduser(str(state_dir or "")), REVIEW_RECORD_NAME)


def read_review_record(state_dir):
    """{issue id: {identifier, repo, pr, at}} for every review ticket the review poller has
    filed. An ABSENT file is an empty record: the poller writes it with its first ticket,
    so before that there is nothing to list. A file that is there and cannot be read, or is
    not this record, raises RecordError — unread is never empty (contract §13)."""
    path = review_record_path(state_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RecordError("the review poller's record %s could not be read (%s)" % (path, exc))
    tickets = doc.get("tickets") if isinstance(doc, dict) else None
    if (not isinstance(doc, dict) or doc.get("schema") != REVIEW_RECORD_SCHEMA
            or not isinstance(tickets, dict)
            or not all(isinstance(v, dict) for v in tickets.values())):
        raise RecordError("the review poller's record %s is not %s" % (path, REVIEW_RECORD_SCHEMA))
    return dict(tickets)


def in_review_record(record, issue_id=None, identifier=None):
    """Whether a ticket is in a record `read_review_record` returned, by the tracker's issue
    id or by its identifier (a run's folder is named by the identifier alone)."""
    if issue_id and issue_id in (record or {}):
        return True
    return bool(identifier) and any((entry or {}).get("identifier") == identifier
                                    for entry in (record or {}).values())


def add_to_review_record(state_dir, issue_id, identifier, repo, pr, at=None):
    """Add one review ticket, keeping every ticket already there. A record that cannot be
    read is NEVER written over: RecordError, so the poller stops before it files a ticket
    it could not record. Returns the record as written."""
    if not (isinstance(issue_id, str) and issue_id and isinstance(identifier, str) and identifier):
        raise ValueError("a review ticket is recorded by its issue id and its identifier")
    path = review_record_path(state_dir)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        lock = open(path + ".lock", "a")
    except OSError as exc:
        raise RecordError("the review poller's record %s could not be locked (%s)" % (path, exc))
    try:
        # Read, add and rewrite under an exclusive lock: two writers at once would each read
        # N tickets and write N+1, and one ticket would be lost. The record never shrinks.
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        tickets = read_review_record(state_dir)
        tickets[issue_id] = {"identifier": identifier, "repo": repo, "pr": pr, "at": at or _now_iso()}
        try:
            _atomic_write_json(path, {"schema": REVIEW_RECORD_SCHEMA, "tickets": tickets})
        except OSError as exc:
            raise RecordError("the review poller's record %s could not be written (%s)" % (path, exc))
        return tickets
    finally:
        lock.close()


# The routing sign-off: what the one probe proved, for both lanes. The idea gate's installer
# writes it under the role account when its probe is signed and current, and removes it when
# the probe stops holding; the review poller reads it. The path is relative to that home.
ROUTING_SIGNOFF_RELPATH = ".stage-a/routing-signoff.json"
ROUTING_SIGNOFF_SCHEMA = "pipeline-routing-signoff/1"
_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def signoff_problems(doc):
    """Every way `doc` is not a routing sign-off; empty when it is one."""
    if not isinstance(doc, dict):
        return ["the sign-off is not one JSON object"]
    problems = []
    if doc.get("schema") != ROUTING_SIGNOFF_SCHEMA:
        problems.append("its schema is not %s" % ROUTING_SIGNOFF_SCHEMA)
    if not (isinstance(doc.get("signed_at"), str) and _ISO_Z_RE.match(doc["signed_at"])):
        problems.append("signed_at is not a UTC time like 2026-11-11T09:30:00Z")
    version = doc.get("dispatcher_version")
    if not (isinstance(version, str) and version.strip()):
        problems.append("dispatcher_version is missing")
    entries = doc.get("entries")
    if not (isinstance(entries, dict) and entries):
        problems.append("entries names no entry the probe reached")
    else:
        for name, methods in sorted(entries.items()):
            if not (isinstance(methods, list) and methods
                    and all(m in ROUTE_METHODS for m in methods)):
                problems.append("entries[%r] is not a list of routes from %s"
                                % (name, ", ".join(ROUTE_METHODS)))
    unknown = sorted(set(doc) - {"schema", "signed_at", "dispatcher_version", "entries"})
    if unknown:
        problems.append("it has keys no sign-off has: %s" % ", ".join(unknown))
    return problems


def signoff_document(signed_at, dispatcher_version, entries):
    """The sign-off the idea gate's installer writes: `entries` maps an entry name to the
    routes the probe reached it by. Refuses to build one that would not read back."""
    doc = {"schema": ROUTING_SIGNOFF_SCHEMA, "signed_at": signed_at,
           "dispatcher_version": dispatcher_version,
           "entries": dict((name, sorted(set(methods))) for name, methods in entries.items())}
    problems = signoff_problems(doc)
    if problems:
        raise ValueError("not a routing sign-off: %s" % "; ".join(problems))
    return doc


def read_routing_signoff(path):
    """(sign-off, None), or (None, why) when there is none to go by: absent, unreadable or
    malformed. The review poller files nothing on any of those."""
    path = os.path.expanduser(str(path or ""))
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, ("there is no routing sign-off at %s: the probe has not been signed, or "
                      "it no longer holds. The one command offers to run it" % path)
    except (OSError, ValueError) as exc:
        return None, "the routing sign-off at %s could not be read (%s)" % (path, exc)
    problems = signoff_problems(doc)
    if problems:
        return None, "the routing sign-off at %s is not one: %s" % (path, "; ".join(problems))
    return doc, None


def entry_proven(signoff, entry):
    """Whether the sign-off shows `entry` reached by BOTH its tag and its label alone."""
    methods = (((signoff or {}).get("entries") or {}).get(entry)) or []
    return all(m in methods for m in ROUTE_METHODS)


# The stop: the planner job's own file, read the way that job reads it. The path is relative
# to the role account's home, and the selftest pins both it and the schema to the idea gate.
SHARED_STOP_RELPATH = ".stage-a/state/stop.json"
STOP_SCHEMA = "pipeline-plan-poller-stop/1"
FAR_FUTURE = "9999-12-31T23:59:59Z"


def read_stop(path):
    """The stop at `path`, or None when none stands. UNREADABLE IS STOPPED, as the planner
    job reads it: a damaged file is a stop as of its last write, it names no repository,
    and so it holds for every one."""
    path = os.path.expanduser(str(path or ""))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and doc.get("schema") == STOP_SCHEMA and doc.get("at"):
            return doc
    except (OSError, ValueError):
        pass
    try:
        at = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        at = FAR_FUTURE
    return {"schema": STOP_SCHEMA, "at": at, "unreadable": True,
            "reason": "the stop at %s could not be read, so it counts as stopped" % path}


def stop_holds(stop, repo, signed_at):
    """Whether `stop` pauses `repo` (owner/name), given the sign-off's time. Only a probe
    signed AFTER the stop clears it, as the planner job's `clear_stop_if_resigned` reads it:
    a stop dated the same second as the sign-off still holds. A stop that names no
    repository, or could not be read, holds for every one."""
    if not stop:
        return False
    if signed_at and str(stop.get("at") or FAR_FUTURE) < str(signed_at):
        return False
    named = stop.get("repo")
    return stop.get("unreadable") or not named or named == repo


def stop_document(reason, repo, lane, evidence, signed_at=None, at=None):
    """A stop in the planner job's own shape, naming its lane ("review" or "planning")."""
    return {"schema": STOP_SCHEMA, "at": at or _now_iso(), "reason": reason, "repo": repo,
            "lane": lane, "evidence": evidence, "probe_signed_at": signed_at}


def write_stop_if_absent(path, doc):
    """Write the shared stop only when none stands. True when this call wrote it, False when
    one already stood: that one keeps its evidence, and stops planning just the same. The
    file is published by a hard link, which refuses an existing name, so a stop the planner
    wrote a moment earlier is never written over."""
    path = os.path.expanduser(str(path or ""))
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-stop-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(tmp)


# --------------------------------------------------------------------------- #
# Selftest. The consumers (the planner job, the installer, the Stage E jobs) each test
# their own use of these; this pins the definitions themselves.
# --------------------------------------------------------------------------- #
def selftest():
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    check("entry-name", planning_entry_name("Example-Org/Product"), "stage-a-planning-product")
    check("entry-name-keeps-dots", planning_entry_name("o/my.app_2"), "stage-a-planning-my.app_2")
    for bad in ("", "o/", "o/has space", "o/a]b"):
        try:
            planning_entry_name(bad)
            check("entry-name-refuses:%r" % bad, "accepted", "refused")
        except ValueError:
            check("entry-name-refuses:%r" % bad, "refused", "refused")
    check("prefix-is-stage-e-prefix", PLANNING_PREFIX, "stage-a-planning-")

    # Recognition: the opening tag (both spellings), or the label. Never the title, never
    # a tag further down, never another entry's tag.
    check("tag-opens", is_planning_ticket("[repo=stage-a-planning-x]\n\nbody"), True)
    check("tag-escaped", is_planning_ticket("\\[repo=stage-a-planning-x\\]\n"), True)
    check("tag-leading-space", is_planning_ticket("\n  [repo=stage-a-planning-x]"), True)
    check("tag-mid-body-is-not", is_planning_ticket("see\n[repo=stage-a-planning-x]"), False)
    check("coding-tag-is-not", is_planning_ticket("[repo=product]\nbody"), False)
    check("review-tag-is-not", is_planning_ticket("[repo=reviews-x]\nbody"), False)
    check("review-label-is-not", is_planning_ticket("plain", ["reviews-x"]), False)
    check("label", is_planning_ticket("plain", ["bug", "stage-a-planning-x"]), True)
    check("label-prefix-only", is_planning_ticket("plain", ["planning", "stage-a"]), False)
    check("nothing", is_planning_ticket(None, None), False)
    check("issue-node", issue_is_planning_ticket(
        {"description": "x", "labels": {"nodes": [{"name": "stage-a-planning-y"}]}}), True)
    check("issue-node-empty", issue_is_planning_ticket({}), False)

    # A review ticket (KIT-185): the same two marks with the review prefix, and the prefix
    # is the one the review poller and the Stage E installer write.
    check("review-tag-opens", is_review_ticket("[repo=reviews-x]\n\nbody"), True)
    check("review-tag-escaped", is_review_ticket("\\[repo=reviews-x\\]\n"), True)
    check("review-tag-leading-space", is_review_ticket("\n  [repo=reviews-x]"), True)
    check("review-tag-mid-body-is-not", is_review_ticket("see\n[repo=reviews-x]"), False)
    check("review-coding-tag-is-not", is_review_ticket("[repo=product]\nbody"), False)
    check("review-old-single-entry-tag-is-not", is_review_ticket("[repo=reviews]\nbody"), False)
    check("review-planning-tag-is-not", is_review_ticket("[repo=stage-a-planning-x]"), False)
    check("review-label", is_review_ticket("plain", ["bug", "reviews-x"]), True)
    check("review-label-prefix-only", is_review_ticket("plain", ["reviews", "review-x"]), False)
    check("review-title-is-not", is_review_ticket("Review PR #5 — ENG-5", ["bug"]), False)
    check("review-nothing", is_review_ticket(None, None), False)
    check("review-issue-node", issue_is_review_ticket(
        {"description": "x", "labels": {"nodes": [{"name": "reviews-y"}]}}), True)
    check("review-issue-node-tag", issue_is_review_ticket({"description": "[repo=reviews-y]"}), True)
    check("review-issue-node-empty", issue_is_review_ticket({}), False)
    check("machine-planning", issue_is_machine_ticket(
        {"labels": {"nodes": [{"name": "stage-a-planning-y"}]}}), True)
    check("machine-review", issue_is_machine_ticket({"description": "[repo=reviews-y]"}), True)
    check("machine-coding", issue_is_machine_ticket(
        {"description": "[repo=product]", "labels": {"nodes": [{"name": "bug"}]}}), False)
    check("machine-plain", is_machine_ticket("plain", ["reviews-x"]), True)

    # The routing note, as ActivityPoster writes it.
    tag_note = "**Routing** ([repo=...] tag)\n- **stage-a-planning-x** → `main` (default)"
    check("parse-tag", parse_routing_note(tag_note),
          {"method": "[repo=...] tag", "entries": ["stage-a-planning-x"]})
    check("verdict-tag", routing_verdict(tag_note, "stage-a-planning-x")[0], True)
    label_note = "**Routing** (Label routing)\n- **stage-a-planning-x** → `main` (default)"
    check("verdict-label", routing_verdict(label_note, "stage-a-planning-x")[0], True)
    escaped = "**Routing** (\\[repo=...\\] tag)\n- **stage-a-planning-x** → `main` (default)"
    check("verdict-escaped", routing_verdict(escaped, "stage-a-planning-x")[0], True)
    team_note = "**Routing** (Team routing)\n- **product** → `main` (default)"
    check("verdict-team-fails", routing_verdict(team_note, "stage-a-planning-x")[0], False)
    wrong_method = "**Routing** (Team routing)\n- **stage-a-planning-x** → `main` (default)"
    check("verdict-right-name-wrong-route-fails",
          routing_verdict(wrong_method, "stage-a-planning-x")[0], False)
    merged = tag_note + "\n- **product** → `main` (default)"
    check("verdict-two-entries-fails", routing_verdict(merged, "stage-a-planning-x")[0], False)
    check("verdict-other-planner-fails",
          routing_verdict(tag_note, "stage-a-planning-y")[0], False)
    check("verdict-none-fails", routing_verdict(None, "stage-a-planning-x")[0], False)
    check("verdict-garbage-fails", routing_verdict("hello", "stage-a-planning-x")[0], False)
    check("verdict-no-method-fails",
          routing_verdict("**Routing**\n- **stage-a-planning-x** → `main`",
                          "stage-a-planning-x")[0], False)
    check("verdict-bad-line-fails",
          routing_verdict(tag_note + "\nnot an entry line", "stage-a-planning-x")[0], False)
    # The review lane reads the note the same way, and its reasons name a review ticket.
    review_note = "**Routing** (Label routing)\n- **reviews-x** → `main` (default)"
    check("verdict-review-ok", routing_verdict(review_note, "reviews-x", "a review ticket")[0], True)
    check("verdict-review-wrong-entry", routing_verdict(team_note, "reviews-x",
                                                        "a review ticket")[0], False)
    check("verdict-review-wording", "a review ticket must be routed" in routing_verdict(
        "**Routing** (Team routing)\n- **reviews-x** → `main`", "reviews-x", "a review ticket")[1],
          True)
    check("verdict-review-wording-two", "a review ticket must reach exactly one" in routing_verdict(
        review_note + "\n- **product** → `main` (default)", "reviews-x", "a review ticket")[1], True)
    check("verdict-planning-wording-kept", "a planning ticket must be routed" in routing_verdict(
        wrong_method, "stage-a-planning-x")[1], True)

    # The EARLIEST routing-shaped thought counts; a later imitation cannot displace it.
    acts = [
        {"createdAt": "2026-01-01T00:00:09Z",
         "content": {"__typename": "AgentActivityThoughtContent", "body": tag_note}},
        {"createdAt": "2026-01-01T00:00:03Z",
         "content": {"__typename": "AgentActivityThoughtContent", "body": team_note}},
        {"createdAt": "2026-01-01T00:00:01Z",
         "content": {"__typename": "AgentActivityThoughtContent", "body": "On it."}},
        {"createdAt": "2026-01-01T00:00:00Z",
         "content": {"__typename": "AgentActivityResponseContent", "body": tag_note}},
    ]
    first = earliest_routing_thought(acts)
    check("earliest-wins", (first or {}).get("createdAt"), "2026-01-01T00:00:03Z")
    check("earliest-is-the-wrong-one", routing_verdict(
        first["content"]["body"], "stage-a-planning-x")[0], False)
    check("no-thought-no-note", earliest_routing_thought(acts[3:]), None)

    # Confirmation for a skip that would take a ticket out of review: the dispatcher's own
    # earliest note must name a planning entry, from a list read whole.
    def thoughts(*bodies, more=False):
        return {"agentSession": {"activities": {"nodes": [
            {"createdAt": "2026-01-01T00:00:%02dZ" % i,
             "content": {"__typename": "AgentActivityThoughtContent", "body": b}}
            for i, b in enumerate(bodies)], "pageInfo": {"hasNextPage": more}}}}
    check("confirmed-by-tag", routed_to_planning(thoughts("On it.", tag_note)), True)
    check("confirmed-by-cached-route", routed_to_planning(
        thoughts("**Routing**\n- **stage-a-planning-x** → `main` (default)")), True)
    check("coding-note-not-confirmed", routed_to_planning(thoughts(team_note, tag_note)), False)
    check("no-note-not-confirmed", routed_to_planning(thoughts("On it.")), False)
    check("partial-list-not-confirmed", routed_to_planning(thoughts(tag_note, more=True)), False)
    check("merged-not-confirmed", routed_to_planning(thoughts(merged)), False)
    check("nothing-not-confirmed", routed_to_planning(None), False)

    _selftest_files(check)
    _selftest_pins(check)

    if failures:
        print("FAIL: pipeline_machine_tickets selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_machine_tickets selftest (%d cases)" % cases[0])
    return 0


def _selftest_files(check):
    """The record, the sign-off and the stop, on real files in a temp folder."""
    def raises(fn, exc_type):
        try:
            fn()
        except exc_type:
            return True
        return False

    with tempfile.TemporaryDirectory(prefix="machine-tickets-") as tmp:
        state = os.path.join(tmp, "state")
        # The review poller's record: absent is empty; it grows; unread is never empty.
        check("record-absent-is-empty", read_review_record(state), {})
        add_to_review_record(state, "id-1", "ENG-7", "o/r", 5, at="2026-11-11T00:00:00Z")
        add_to_review_record(state, "id-2", "ENG-9", "o/r", 6, at="2026-11-11T00:01:00Z")
        record = read_review_record(state)
        check("record-keeps-every-ticket", sorted(record), ["id-1", "id-2"])
        check("record-entry", record["id-1"], {"identifier": "ENG-7", "repo": "o/r", "pr": 5,
                                               "at": "2026-11-11T00:00:00Z"})
        check("record-mode-600", oct(os.stat(review_record_path(state)).st_mode & 0o777), "0o600")
        check("record-by-id", in_review_record(record, issue_id="id-2"), True)
        check("record-by-identifier", in_review_record(record, identifier="ENG-7"), True)
        check("record-not-listed", in_review_record(record, "id-3", "ENG-8"), False)
        check("record-nothing-asked", in_review_record(record), False)
        check("record-needs-ids", raises(lambda: add_to_review_record(state, "", "ENG-1", "o/r", 1),
                                         ValueError), True)
        check("record-no-temp-left", [n for n in os.listdir(state) if n.startswith(".tmp-")], [])
        # Two writers at once each keep their own ticket: the add holds an exclusive lock.
        import threading
        errors = []

        def add_many(prefix):
            try:
                for i in range(25):
                    add_to_review_record(state, "%s-%d" % (prefix, i), "ENG-%d" % i, "o/r", i)
            except Exception as exc:     # noqa: BLE001 — a crash is a failed case, not a pass
                errors.append(repr(exc))
        threads = [threading.Thread(target=add_many, args=(p,)) for p in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("record-concurrent-adds-keep-every-ticket",
              (errors, len(read_review_record(state))), ([], 52))
        with open(review_record_path(state), "w") as fh:
            fh.write("{not json")
        check("record-unreadable-raises", raises(lambda: read_review_record(state), RecordError), True)
        check("record-unreadable-never-overwritten", (
            raises(lambda: add_to_review_record(state, "id-4", "ENG-4", "o/r", 4), RecordError),
            open(review_record_path(state)).read()), (True, "{not json"))
        with open(review_record_path(state), "w") as fh:
            json.dump({"schema": "something-else/1", "tickets": {}}, fh)
        check("record-wrong-schema-raises", raises(lambda: read_review_record(state), RecordError),
              True)
        with open(review_record_path(state), "w") as fh:
            json.dump({"schema": REVIEW_RECORD_SCHEMA, "tickets": {"id-1": "ENG-7"}}, fh)
        check("record-bad-entry-raises", raises(lambda: read_review_record(state), RecordError), True)

        # The routing sign-off: written whole or not at all, read back, and proven per entry
        # only by BOTH routes.
        signoff = os.path.join(tmp, ".stage-a", "routing-signoff.json")
        got, why = read_routing_signoff(signoff)
        check("signoff-absent", (got, "no routing sign-off" in why), (None, True))
        doc = signoff_document("2026-11-11T09:30:00Z", "0.2.73", {
            "reviews-r": [METHOD_LABEL, METHOD_TAG, METHOD_TAG],
            "reviews-s": [METHOD_TAG], "stage-a-planning-r": list(ROUTE_METHODS)})
        check("signoff-routes-sorted-once", doc["entries"]["reviews-r"], sorted(ROUTE_METHODS))
        _atomic_write_json(signoff, doc)
        got, why = read_routing_signoff(signoff)
        check("signoff-reads-back", (got == doc, why), (True, None))
        check("signoff-both-routes-proven", entry_proven(got, "reviews-r"), True)
        check("signoff-tag-only-not-proven", entry_proven(got, "reviews-s"), False)
        check("signoff-absent-entry-not-proven", entry_proven(got, "reviews-t"), False)
        check("signoff-none-not-proven", entry_proven(None, "reviews-r"), False)
        for name, bad in (("bad-time", dict(doc, signed_at="11 Nov")),
                          ("no-version", dict(doc, dispatcher_version=" ")),
                          ("no-entries", dict(doc, entries={})),
                          ("bad-route", dict(doc, entries={"reviews-r": ["Team routing"]})),
                          ("extra-key", dict(doc, extra=1)),
                          ("wrong-schema", dict(doc, schema="x/1"))):
            _atomic_write_json(signoff, bad)
            got, why = read_routing_signoff(signoff)
            check("signoff-refuses-" + name, (got, bool(why)), (None, True))
        with open(signoff, "w") as fh:
            fh.write("[")
        check("signoff-unreadable", read_routing_signoff(signoff)[0], None)
        check("signoff-document-refuses", raises(
            lambda: signoff_document("now", "0.2.73", {"reviews-r": [METHOD_TAG]}), ValueError),
              True)

        # The stop, read the planner's way; it holds per repository until a later sign-off.
        stop = os.path.join(tmp, ".stage-a", "state", "stop.json")
        check("stop-absent", read_stop(stop), None)
        doc = stop_document("no routing note", "o/r", "review", {"ticket": "ENG-7"},
                            signed_at="2026-11-11T09:30:00Z", at="2026-11-11T10:00:00Z")
        check("stop-written-when-none", write_stop_if_absent(stop, doc), True)
        check("stop-reads-back", read_stop(stop), doc)
        check("stop-mode-600", oct(os.stat(stop).st_mode & 0o777), "0o600")
        later = dict(doc, repo="o/other", at="2026-11-11T11:00:00Z")
        check("stop-never-written-over", (write_stop_if_absent(stop, later), read_stop(stop)),
              (False, doc))
        check("stop-no-temp-left", [n for n in os.listdir(os.path.dirname(stop))
                                    if n.startswith(".tmp-")], [])
        check("stop-holds-for-its-repo", stop_holds(doc, "o/r", "2026-11-11T09:30:00Z"), True)
        check("stop-not-for-another-repo", stop_holds(doc, "o/s", "2026-11-11T09:30:00Z"), False)
        check("stop-cleared-by-later-signoff", stop_holds(doc, "o/r", "2026-11-11T10:00:01Z"),
              False)
        check("stop-same-second-still-holds", stop_holds(doc, "o/r", "2026-11-11T10:00:00Z"), True)
        check("stop-holds-without-signoff", stop_holds(doc, "o/r", None), True)
        check("stop-naming-none-holds-for-all", stop_holds(dict(doc, repo=None), "o/s",
                                                           "2026-11-11T09:30:00Z"), True)
        check("stop-none-holds-nothing", stop_holds(None, "o/r", None), False)
        check("planner-stop-shape-holds", stop_holds(
            {"schema": STOP_SCHEMA, "at": "2026-11-11T10:00:00Z", "repo": "o/r", "reason": "x"},
            "o/r", "2026-11-11T09:00:00Z"), True)
        with open(stop, "w") as fh:
            fh.write("{")
        unread = read_stop(stop)
        check("stop-unreadable-is-stopped", (bool(unread), unread.get("unreadable")), (True, True))
        check("stop-unreadable-holds-for-all", stop_holds(unread, "o/s", "2000-01-01T00:00:00Z"),
              True)
        with open(stop, "w") as fh:
            json.dump({"schema": "x/1", "at": "2026-11-11T10:00:00Z"}, fh)
        check("stop-wrong-schema-is-stopped", read_stop(stop).get("unreadable"), True)


def _module_constant(filename, name):
    """A module-level string constant from a sibling script, read with `ast` so the pin needs
    no import of a large job."""
    import ast
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
                and isinstance(node.value, ast.Constant)):
            return node.value.value
    return None


def _selftest_pins(check):
    """The names other files spell must be these: a drift would make one job write a mark,
    a file or a stop another job never reads."""
    check("pin-review-prefix-poller",
          _module_constant("pipeline_review_poller.py", "REVIEW_ENTRY_PREFIX"), REVIEW_PREFIX)
    check("pin-review-prefix-installer",
          _module_constant("pipeline_stage_e_setup.py", "REVIEW_ENTRY_PREFIX"), REVIEW_PREFIX)
    check("pin-stop-schema-planner",
          _module_constant("pipeline_plan_poller.py", "STOP_SCHEMA"), STOP_SCHEMA)
    check("pin-stop-path-idea-gate",
          (_module_constant("pipeline_stage_a_setup.py", "ROLE_STATE_DIR") or "") + "/stop.json",
          SHARED_STOP_RELPATH)
    check("pin-signoff-beside-idea-gate",
          os.path.dirname(ROUTING_SIGNOFF_RELPATH),
          os.path.dirname(_module_constant("pipeline_stage_a_setup.py", "ROLE_POLLER_CONFIG")
                          or ""))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "machine-tickets-selftest-run-")
    print(__doc__.strip().splitlines()[-1].strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
