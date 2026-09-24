#!/usr/bin/env python3
"""Recognising the pipeline's own machine tickets, and reading the dispatcher's routing note.

WHY THIS FILE EXISTS

  Planning now lives on each project's own work team (KIT-184): the planner job files a
  planning ticket beside the team's real work, and the dispatcher routes it by a
  `[repo=…]` tag and a routing label instead of by a team of its own. A separate team used
  to keep every other job away from planning tickets by accident — they only watch the
  work teams. Built in, each of those jobs must skip a planning ticket on purpose: the
  review poller's discovery, the criteria snapshot, the finding poller, and the executor's
  duplicate check. Four jobs, one definition, here. The review lane can reuse it when it
  moves into the work teams too (KIT-185).

WHAT A PLANNING TICKET IS

  One of two things marks it, and either is enough:

    - its description OPENS with the planning tag `[repo=stage-a-planning-…]` (or the
      `\\[…\\]` escaped form the tracker may store), which is the first line the planner
      job writes and the tag the dispatcher routes on;
    - it carries a label whose name starts `stage-a-planning-`, the routing label only
      one repository's planning entry claims.

  Never the title. A title is whatever a person typed, and "Plan the release" is a coding
  ticket.

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

Usage:
    pipeline_machine_tickets.py --selftest
"""
import re
import sys

# The prefix every planning entry's id, name and routing label carries. The review
# installer's `_is_planning_entry` reads the same prefix (PR #155), so it must not change.
PLANNING_PREFIX = "stage-a-planning-"

# The dispatcher's display names for the two routes a planning ticket may take. A tag
# match is the intended route; a label match is the second net, for a tag that failed to
# fetch or match. Every other route — team, project, catch-all — lands a planning ticket
# somewhere it was never meant to go.
METHOD_TAG = "[repo=...] tag"
METHOD_LABEL = "Label routing"
PLANNING_METHODS = (METHOD_TAG, METHOD_LABEL)

_TAG_OPEN_RE = re.compile(r"^\\?\[repo=" + re.escape(PLANNING_PREFIX), re.IGNORECASE)
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


def routing_verdict(note_body, want_entry):
    """(ok, reason). ok only when the note names exactly one entry, that entry is
    `want_entry`, and the route was the tag or the label. None is a failure: the note is
    best-effort, so its absence proves nothing about where the session runs."""
    if note_body is None:
        return False, "no routing note from the dispatcher"
    parsed = parse_routing_note(note_body)
    if parsed is None:
        return False, "the routing note could not be read"
    if parsed.get("unparsed"):
        return False, "the routing note has a line that could not be read: %r" % parsed["unparsed"][:120]
    names = parsed["entries"]
    if len(names) != 1:
        return False, ("the routing note names %d setups (%s); a planning ticket must reach "
                       "exactly one" % (len(names), ", ".join(names) or "none"))
    if names[0] != want_entry:
        return False, "the routing note names %r, not %r" % (names[0], want_entry)
    method = (parsed.get("method") or "").strip()
    if method not in PLANNING_METHODS:
        return False, ("the routing note says the route was %r; a planning ticket must be "
                       "routed by its tag or its label" % (method or "not stated"))
    return True, "routed to %s by %s" % (names[0], method)


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
    check("review-tag-is-not", is_planning_ticket("[repo=stage-e-review-x]"), False)
    check("label", is_planning_ticket("plain", ["bug", "stage-a-planning-x"]), True)
    check("label-prefix-only", is_planning_ticket("plain", ["planning", "stage-a"]), False)
    check("nothing", is_planning_ticket(None, None), False)
    check("issue-node", issue_is_planning_ticket(
        {"description": "x", "labels": {"nodes": [{"name": "stage-a-planning-y"}]}}), True)
    check("issue-node-empty", issue_is_planning_ticket({}), False)

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

    if failures:
        print("FAIL: pipeline_machine_tickets selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_machine_tickets selftest (%d cases)" % cases[0])
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return selftest()
    print(__doc__.strip().splitlines()[-1].strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
