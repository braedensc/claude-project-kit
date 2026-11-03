#!/usr/bin/env python3
"""The Slack bridge's plans: `approve <epic id>`, its children's start, and its end.

    python3 scripts/pipeline_bridge_plans.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports every name it had, so `pipeline_bridge.X` is still the very same
object. Its cases run in the bridge's own selftest, end to end through one pass; this
module's selftest pins what it may reach.

WHAT IS HERE
  Approve (KIT-223)  a yes moves an epic the idea gate filed to its team's ready state;
                 then every pass hands its children to the agent user, as the owner, in
                 dependency order, at most `epic_wip` at once.
  A stalled start  a hand-over not started two hours on, as its sessions show it (KIT-284).
  Finish (KIT-299)  once every piece is merged: the epic to its done state, one comment,
                 one wrap-up, and its review notes filed as one ticket after one more yes.
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
import pipeline_plan_poller as poller  # noqa: E402
import pipeline_voice as voice  # noqa: E402
import telemetry_scrape as scrape  # noqa: E402
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_config import (DONE, EXIT_USAGE, OPEN, REFUSED,  # noqa: E402
                                    SLACK_USER_RE, _TICKET_WORD_RE, iso, key_of,
                                    label_names, ticket_number, ts_float)

FINISHED = ("completed", "canceled")
GATE_EPIC_LABEL, GATE_CHILD_LABEL = "provenance:agent", "provenance:epic"
MAX_CHILDREN = 30
# Labels that switch the dispatcher into a mode where it hands out, or stacks, children
# itself (0.2.73: the orchestrator prompts self-delegate sub-issues; a graphite child is cut
# from its blocker's branch). Matched without regard to case.
MODE_LABELS = ("orchestrator", "graphite")
# How long a child handed over may sit unstarted before the bridge says why, as its agent
# sessions show it (KIT-284). The dispatcher moves a ticket to a started state when its
# session begins.
STUCK_START_SECONDS = 2 * 3600
# A pass reads sessions after setting its clock, so one up to this far past it is just made.
JUST_MADE_SECONDS = 600
# What a session's status says of a child two hours on. A hand-over always makes a session,
# and what the tracker calls one nobody answered is not proven (docs/CATCH-UP.md): the
# catch-up poller models it as pending, and it may turn stale. So a session is running only
# once the agent has picked it up, and has ended only with a status the agent's own response
# or error sets.
SESSION_RUNNING = ("active", "awaitingInput")
SESSION_RAN = ("complete", "error")
# On a child in a hand-off lane, the label that says its review ran out of rounds with
# findings still open (contract §1). Matched without regard to case.
NEEDS_HUMAN_LABEL = "agent:needs-human"
# KIT-299: closing a finished plan. How often a plan whose close is held reads its pieces
# again (each piece is a read on the owner's key, shared with Stage E); how many links one
# piece's read holds (a piece with more holds the close: the one past them may be open);
# the kind of the one question asked after a plan closes, and where its yes files, which
# the question records as a stated term; and how many lines the wrap-up lists before it
# says how many more.
FINISH_RECHECK_SECONDS = 900
PIECE_LINKS = 50
FOLLOWUP = "followup"
FOLLOWUP_INTO = "the team's default state"
WRAP_LINES = 15
# A review is the owner's own comment that starts so, holding a `pipeline-telemetry/1`
# block: what the review poller posts on the original ticket (telemetry_block.py). One
# that saw only part of a change says so in its head, never in its block (KIT-138), with
# the number of files it never read.
REVIEW_HEAD = "**Pipeline review**"
PARTIAL_RE = re.compile(r"\*\*PARTIAL REVIEW(?: — (\d+) file)?")
# Only the GitHub INTEGRATION's own attachments name a piece's pull request: a session
# holds the tracker's write tools and can attach any link to its own ticket. A copy of the
# review poller's list and pull-request pattern, compared the same way (punctuation and case
# stripped). That file exits when its publisher is missing, so it is not imported here; the
# selftest pins the copies equal to it.
GITHUB_ATTACHMENT_SOURCES = ("github", "githubpullrequest", "githubpr", "githubissue",
                             "githubcommit", "githubbranch")
PR_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/(\d+)\b")


def blockers_of(child):
    """THE ORDER CHECK, shared with the order job (B9): (ticket, its state) for each ticket
    that BLOCKS this one. The idea gate links a child to what it waits on with a `blocks`
    relation from the blocker, so the blockers are the child's inverse `blocks` relations."""
    out = []
    for rel in ((child.get("inverseRelations") or {}).get("nodes")) or []:
        issue = (rel or {}).get("issue") or {}
        if rel.get("type") == "blocks" and issue.get("id"):
            out.append((issue, issue.get("state") or {}))
    return out


def blocker_done(state):
    return (state or {}).get("type") == "completed"


def open_blockers(child):
    """What this child still waits on, as `ID (State)`; [] when it may start. A child whose
    blockers could not all be read never may."""
    if (((child.get("inverseRelations") or {}).get("pageInfo")) or {}).get("hasNextPage"):
        return ["more blockers than the bridge reads"]
    return ["%s (%s)" % (b.get("identifier") or "?", st.get("name") or "?")
            for b, st in blockers_of(child) if not blocker_done(st)]


def in_order(children):
    """The children in dependency order: each after the siblings that block it, ties by
    ticket number. A loop in the plan leaves the rest in ticket order."""
    ids = set(k["id"] for k in children)
    waits = dict((k["id"], set(b["id"] for b, _st in blockers_of(k) if b["id"] in ids))
                 for k in children)
    left = sorted(children, key=lambda k: ticket_number(k.get("identifier")))
    placed, out = set(), []
    while left:
        ready = [k for k in left if waits[k["id"]] <= placed]
        if not ready:
            out.extend(left)
            break
        out.append(ready[0])
        placed.add(ready[0]["id"])
        left.remove(ready[0])
    return out


def children_of(epic):
    return ((epic.get("children") or {}).get("nodes")) or []


def fingerprint(child):
    """What the owner approved of a child: its title, text, labels, project and what it
    waits on. A session holds the tracker's write tools, so a child is handed over only
    while this still matches: text is the work, labels and project are its route, and the
    blockers are its place in the order."""
    text = json.dumps([child.get("title") or "", child.get("description") or "",
                       sorted(label_names(child) - {None}),
                       (child.get("project") or {}).get("id") or "",
                       sorted(b.get("id") or "" for b, _st in blockers_of(child))])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fingerprints(epic):
    return dict((k["id"], fingerprint(k)) for k in children_of(epic))


def changes_since(shown, epic):
    """How the epic's children differ from the ones shown, in words; "" when they don't."""
    now = fingerprints(epic)
    names = dict((k["id"], k.get("identifier") or "?") for k in children_of(epic))
    added = sorted(names[i] for i in now if i not in shown)
    changed = sorted(names[i] for i in now if i in shown and now[i] != shown[i])
    removed = len([i for i in shown if i not in now])
    parts = []
    if added:
        parts.append("added: %s" % ", ".join(added))
    if changed:
        parts.append("changed: %s" % ", ".join(changed))
    if removed:
        parts.append("%d removed" % removed)
    return "; ".join(parts)


def epic_problems(epic, kids=None):
    """Why this is not an epic the idea gate filed, or one whose children may not start;
    [] when it may be approved. `kids` narrows the child checks to the approved ones."""
    problems = []
    if GATE_EPIC_LABEL not in label_names(epic):
        problems.append("it does not carry `%s`, so the idea gate did not file it"
                        % GATE_EPIC_LABEL)
    if (epic.get("parent") or {}).get("id"):
        problems.append("it has a parent ticket")
    kids = children_of(epic) if kids is None else kids
    if not kids:
        problems.append("it has no child tickets")
    if ((epic.get("children") or {}).get("pageInfo") or {}).get("hasNextPage"):
        problems.append("it has more than %d children" % MAX_CHILDREN)
    strays = [k.get("identifier") or "?" for k in kids if GATE_CHILD_LABEL not in label_names(k)]
    if strays:
        problems.append("%s lack%s `%s`, so the idea gate did not file %s"
                        % (", ".join(strays), "s" if len(strays) == 1 else "",
                           GATE_CHILD_LABEL, "it" if len(strays) == 1 else "them"))
    nested = [k.get("identifier") or "?" for k in kids
              if ((k.get("children") or {}).get("nodes"))]
    if nested:
        problems.append("%s ha%s child tickets of its own" % (", ".join(nested),
                                                               "s" if len(nested) == 1 else "ve"))
    team = (epic.get("team") or {}).get("key")
    elsewhere = [k.get("identifier") or "?" for k in kids
                 if ((k.get("team") or {}).get("key")) != team]
    if elsewhere:
        problems.append("%s is on another team" % ", ".join(elsewhere))
    for node in [epic] + list(kids):
        modes = sorted(n for n in label_names(node) if (n or "").lower() in MODE_LABELS)
        if modes:
            problems.append("%s carries `%s`, which makes the dispatcher hand out or stack "
                            "children itself" % (node.get("identifier") or "?",
                                                 "`, `".join(modes)))
    if (epic.get("state") or {}).get("type") in FINISHED:
        problems.append("it is %s" % ((epic.get("state") or {}).get("name") or "closed"))
    return problems


def approve_text(epic, requester, cfg, by_bot=False, dm=False):
    head, body, bind, _facts = approve_parts(epic, requester, cfg, by_bot, dm)
    return head + body + "\n" + bind


def approve_parts(epic, requester, cfg, by_bot=False, dm=False):
    """(head, body, bind, facts): an approve question in the writer's pieces, as
    question_parts (KIT-297). The bind states what a yes does, and that merging stays his."""
    kids = in_order(children_of(epic))
    listing = ", ".join("%s “%s”" % (k.get("identifier"), notify.safe_title(k.get("title")
                                                                          or ""))
                        for k in kids[:12])
    if len(kids) > 12:
        listing += ", and %d more" % (len(kids) - 12)
    if by_bot:
        who = "the chat bot"
    else:
        who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    then = ("the bridge moves it to %s and hands its children to the agent for you, at most "
            "%d at a time, each once the tickets it waits on are Done. Merging stays yours"
            % (cfg["ready_state"], cfg["epic_wip"]) if cfg["act"] else
            "the bridge records it; acting is switched off here, so nothing moves or starts")
    title = notify.safe_title(epic.get("title") or "")
    team = (epic.get("team") or {}).get("key") or "?"
    body = ("%s “%s” on %s: %d children, in this order: %s."
            % (epic.get("identifier"), title, team, len(kids), listing))
    bind = ("Reply `yes` %s within %d hours, and %s. Reply `no` to leave it. "
            "(Asked for by %s.)"
            % ("here" if dm else "in this thread", cfg["question_ttl_hours"], then, who))
    facts = {"ticket": epic.get("identifier"), "title": title, "team": team,
             "children": len(kids), "order": [k.get("identifier") for k in kids[:12]],
             "needs": "his answer to the fixed line after this message"}
    return "*Approve this plan?* ", body, bind, facts


# -- A finished plan (KIT-299) ----------------------------------------------------------

def _source_key(value):
    """An attachment's source type, compared as the review poller compares it."""
    return re.sub(r"[^a-z0-9]", "", (value if isinstance(value, str) else "").lower())


def piece_prs(piece):
    """The pull requests the GitHub INTEGRATION linked to a piece, each as its repo,
    number, link and status: lower case, or None when the link carries no status. A link
    of any other source is a value a session could have written, so it is passed over, as
    the review poller passes it over."""
    out = []
    for att in (((piece or {}).get("attachments") or {}).get("nodes")) or []:
        att = att if isinstance(att, dict) else {}
        m = PR_URL_RE.search(att.get("url") if isinstance(att.get("url"), str) else "")
        if not m or _source_key(att.get("sourceType")) not in GITHUB_ATTACHMENT_SOURCES:
            continue
        repo, number = "%s/%s" % (m.group(1), m.group(2)), int(m.group(3))
        if any(p["repo"] == repo and p["number"] == number for p in out):
            continue
        meta = att.get("metadata") if isinstance(att.get("metadata"), dict) else {}
        status = meta.get("status")
        out.append({"repo": repo, "number": number, "url": m.group(0),
                    "status": (status.strip().lower() if isinstance(status, str)
                               and status.strip() else None)})
    return out


def piece_merge(prs):
    """How a finished piece's pull requests stand: (why it holds its plan open, or None;
    the merged ones; the ones the tracker names no status for). One still open holds it,
    and so does one closed unmerged, unless one in the same repository was merged: that
    one replaced it. A link with no status beside it replaces nothing."""
    merged = [p for p in prs if p["status"] == "merged"]
    unknown = [p for p in prs if p["status"] is None]
    still_open = [p for p in prs if p["status"] not in (None, "merged", "closed")]
    closed = [p for p in prs if p["status"] == "closed"
              and not any(m["repo"] == p["repo"] for m in merged)]
    if still_open:
        return ("#%d is %s, not merged" % (still_open[0]["number"],
                                           notify.safe_title(still_open[0]["status"])),
                merged, unknown)
    if closed:
        return "#%d was closed without being merged" % closed[0]["number"], merged, unknown
    return None, merged, unknown


def piece_hold(piece):
    """Why a finished piece holds its plan open, or None: a pull request not merged, or
    more links than one read holds, since one past them may be open."""
    if piece.get("links_over"):
        return ("%s has more than %d links, and I read only those, so I can't tell its pull "
                "requests are all merged" % (piece["ident"], PIECE_LINKS))
    why = piece_merge(piece["prs"])[0]
    return "%s's pull request %s" % (piece["ident"], why) if why else None


def piece_review(piece, author_id):
    """What a piece's newest review left: (its notes, or None when there are none to read;
    how many files it never read when it says it was partial, else None; True when it
    could not be read). A review is a comment by `author_id`, the owner's key the review
    poller posts with, whose body starts with REVIEW_HEAD. A comment by anyone else is
    never one: a session posts as the dispatcher's app, and could write a look-alike. The
    NEWEST review is the one read: when its `pipeline-telemetry/1` block does not parse, or
    its findings are not a list, the piece has no notes, never an older review's, which
    after a bounce are about code since fixed. A review with no findings left no notes; a
    partial one left none only on the files it read."""
    best = None
    for c in (((piece or {}).get("comments") or {}).get("nodes")) or []:
        c = c if isinstance(c, dict) else {}
        user = c.get("user") if isinstance(c.get("user"), dict) else {}
        body = c.get("body") if isinstance(c.get("body"), str) else ""
        if not author_id or user.get("id") != author_id \
                or not body.lstrip().startswith(REVIEW_HEAD):
            continue
        at = c.get("createdAt") if isinstance(c.get("createdAt"), str) else ""
        if best is None or at > best[0]:
            best = (at, body)
    if best is None:
        return None, None, False
    part = PARTIAL_RE.search(best[1].split("```", 1)[0])
    partial = (int(part.group(1)) if part.group(1) else 0) if part else None
    blocks, _malformed = scrape.telemetry_blocks(best[1])
    found = blocks[0].get("review_findings", []) if blocks else None
    if not isinstance(found, list):
        return None, partial, True
    notes = []
    for f in found:
        if not isinstance(f, dict) or not str(f.get("summary") or "").strip():
            continue
        line, pr = f.get("line"), f.get("pr_number")
        notes.append({"severity": str(f.get("severity") or "unrated").strip(),
                      "summary": str(f.get("summary")).strip(),
                      "file": str(f.get("file") or "").strip(),
                      "line": line if isinstance(line, int) and not isinstance(line, bool)
                      else None,
                      "pr": pr if isinstance(pr, int) and not isinstance(pr, bool) else None})
    return notes, partial, False


def notes_of(pieces):
    """Every note left on a plan's pieces, in plan order, each naming its piece, and its
    pull request: the one the review names, or else the piece's first."""
    out = []
    for p in pieces:
        first = (p["prs"][0]["number"] if p["prs"] else None)
        for n in p["review"] or []:
            out.append(dict(n, piece=p["ident"], pr=n["pr"] or first))
    return out


def notes_fingerprint(notes):
    """What a follow-up question showed: the exact notes. A yes files them only while this
    still matches."""
    text = json.dumps([[n["piece"], n["severity"], n["file"], n["line"], n["pr"],
                        n["summary"]] for n in notes])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _plain(text, cap):
    """Relayed text for the tracker: no angle bracket, one line, cut to `cap`."""
    out = re.sub(r"\s+", " ", re.sub(r"[<>]", " ", str(text or ""))).strip()
    return out if len(out) <= cap else out[:cap - 1] + "…"


def _relayed(text, cap):
    """Relayed text for a ticket the dispatcher may read once someone hands it over: the
    relay's own cleaner first, so a routing tag (`repo=`, `model=`, `agent=`) a session or a
    review wrote reaches it inert, as every other copied body does; then plain."""
    return _plain(notify.clean_relay_text(str(text or "")), cap)


def _partial(files):
    """What a partial review is said as: how many files it never read, when it names it."""
    return "its review was partial" + (": %d file%s never read" % (
        files, " was" if files == 1 else "s were") if files else "")


def _where(note):
    if not note["file"]:
        return "no file named"
    return note["file"] + (":%d" % note["line"] if note["line"] is not None else "")


def _numbers(prs):
    return ", ".join("#%d" % p["number"] for p in prs)


def piece_lines(pieces):
    """One line for each piece with something worth saying: no pull request linked, a
    merge the tracker does not confirm, no review found, a newest review that could not be
    read, or a partial one."""
    lines = []
    for p in pieces:
        _why, merged, unknown = piece_merge(p["prs"])
        said = []
        if not p["prs"]:
            said.append("no pull request linked")
        elif not merged and unknown:
            said.append("merge not confirmed by the tracker (%s)" % _numbers(unknown))
        if p["review"] is None and p.get("unreadable"):
            said.append("its newest review could not be read")
        elif p["review"] is None:
            said.append("no review found" + (" in the 100 comments I read" if p["more"]
                                             else ""))
        if p.get("partial") is not None:
            said.append(_partial(p["partial"]))
        if said:
            lines.append("• %s: %s." % (p["ident"], "; ".join(said)))
    return lines


def wrap_up_text(epic, pieces, notes, done_name):
    """The one message a finished plan gets: what shipped, a line for each piece worth one,
    and the review notes left, at most WRAP_LINES lines and how many more."""
    text, notes_part, _about, _facts = wrap_up_parts(epic, pieces, notes, done_name)
    return text + ("\n" + notes_part if notes_part else "")


def wrap_up_parts(epic, pieces, notes, done_name):
    """(text, notes, about, facts): the wrap-up in the writer's pieces (KIT-297). The writer
    writes what shipped and the line for each piece, from `about`: the text, and the ids and
    links only the notes hold, since its words must keep each one. The notes, from the note
    list's first line on, follow verbatim: a yes to the follow-up files exactly the notes the
    member read."""
    title = notify.safe_title(epic.get("title") or "")
    merged = [m for p in pieces for m in piece_merge(p["prs"])[1]]
    every = bool(pieces) and all(piece_merge(p["prs"])[1] for p in pieces)
    shipped = (" — %s" % _numbers(merged)) if merged else ""
    head = "All %d children of %s “%s” are finished%s%s. I've marked the plan %s." % (
        len(pieces), epic.get("identifier"), title, " and merged" if every else "", shipped,
        notify.safe_title(done_name))
    lines = piece_lines(pieces)
    first_note = len(lines) if notes else None
    if notes:
        lines.append("%d review note%s left:" % (len(notes), " is" if len(notes) == 1
                                                 else "s are"))
        lines += ["• %s, %s, %s: %s" % (n["piece"], notify.safe_title(n["severity"])[:20],
                                        notify.safe_title(_where(n))[:80],
                                        notify.safe_title(n["summary"])) for n in notes]
    elif any(p["review"] is not None for p in pieces):
        # A partial review's "no notes" covers only the files it read (KIT-138).
        lines.append("Its reviews left no notes%s." % (
            " on the files they read" if any(p.get("partial") is not None for p in pieces)
            else ""))
    if len(lines) > WRAP_LINES:
        lines = lines[:WRAP_LINES] + ["…and %d more." % (len(lines) - WRAP_LINES)]
    cut = len(lines) if first_note is None else min(first_note, len(lines))
    facts = {"plan": epic.get("identifier"), "title": title, "done_state": done_name,
             "pieces": len(pieces), "all_merged": every, "notes": len(notes)}
    text, notes_part = "\n".join([head] + lines[:cut]), "\n".join(lines[cut:])
    # As whole words, as the writer keeps them: PROD-512 in the text does not name PROD-51.
    named = ([w for w in sorted(set(_TICKET_WORD_RE.findall(notes_part)))
              if not voice._whole(w, text)]
             + [u for u in sorted(voice.fact_links([notes_part])) if not voice._whole(u, text)])
    about = text + (" The notes below name %s." % ", ".join(named) if named else "")
    return text, notes_part, about, facts


def closing_comment(rec, pieces, done_name, now):
    """The comment on a plan the bridge closes: who approved it, when it finished, and each
    piece with its pull requests. Names and titles are relayed text."""
    lines = ["Marked %s by the Slack bridge at %s: every piece of this plan is finished. "
             "**%s** (Slack user %s) approved it in Slack at %s."
             % (done_name, iso(now), _relayed(rec.get("approved_name"), 120) or "?",
                rec.get("approved_by") or "?", rec.get("approved_at") or "?"), ""]
    for p in pieces:
        _why, merged, unknown = piece_merge(p["prs"])
        if merged:
            how = "%s, merged" % ", ".join(m["url"] for m in merged)
        elif unknown:
            how = "%s, merge not confirmed by the tracker" % ", ".join(m["url"] for m in unknown)
        else:
            how = "no pull request linked"
        lines.append("- %s “%s”: %s" % (p["ident"], _relayed(p["title"], 120), how))
    return "\n".join(lines)


def followup_text(ticket, count, team, cfg, dm=False):
    """The one question a closed plan with notes left asks. Its terms are fixed: what a yes
    files, where, and that it starts nothing."""
    head, _about, bind, _facts = followup_parts(ticket, count, team, cfg, dm)
    return head + " " + bind


def followup_parts(ticket, count, team, cfg, dm=False):
    """(head, about, bind, facts): the follow-up question in the writer's pieces, as
    question_parts (KIT-297). The head asks, and the writer is given `about` only; the bind
    states what a yes files, where, and that it starts nothing."""
    head = "*File the review notes from %s?*" % ticket
    # Never "listed in my wrap-up": past WRAP_LINES the wrap-up may show none of them.
    about = ("The reviews of %s's pieces left %s."
             % (ticket, "one note" if count == 1 else "%d notes" % count))
    bind = ("Say `yes` %s within %d hours and I'll file %s as one follow-up ticket in %s. "
            "It starts no work. Say `no` to leave %s."
            % ("here" if dm else "in this thread", cfg["question_ttl_hours"],
               "this note" if count == 1 else "these %d notes" % count, team,
               "it" if count == 1 else "them"))
    facts = {"ticket": ticket, "notes": count, "team": team,
             "needs": "his answer to the fixed line after this message"}
    return head, about, bind, facts


def followup_title(epic):
    """The follow-up's title. The plan's title is relayed text: a session can edit it."""
    return _relayed("Follow-ups from %s" % (epic.get("title") or epic.get("identifier")
                                            or "a plan"), 120)


def followup_description(epic, notes, name, uid, when, pieces=()):
    """The follow-up's text: where the notes came from, who said yes, each note with its
    piece and pull request, and each piece whose review was partial. Every relayed field
    (the plan's title, the member's name, each note's parts) goes through the relay's
    cleaner: this ticket is filed as the owner, and the dispatcher reads routing tags out
    of a description once someone hands it over."""
    lines = ["Review notes left on the pieces of %s “%s” when every piece was finished. Filed "
             "by the Slack bridge for **%s** (Slack user %s), who said yes in Slack at %s. "
             "Nothing starts on this ticket until someone hands it over."
             % (epic.get("identifier") or "?", _relayed(epic.get("title"), 120),
                _relayed(name, 120) or uid, uid, when), ""]
    for n in notes[:100]:
        lines.append("- %s%s, %s, %s: %s" % (
            n["piece"], " (#%d)" % n["pr"] if n["pr"] else "", _relayed(n["severity"], 20),
            _relayed(_where(n), 200), _relayed(n["summary"], 500)))
    if len(notes) > 100:
        lines.append("- …and %d more, in each piece's review comment." % (len(notes) - 100))
    partial = [p for p in pieces if p.get("partial") is not None]
    if partial:
        lines += [""] + ["- %s: %s, so those files have no notes here."
                         % (p["ident"], _partial(p["partial"])) for p in partial]
    return "\n".join(lines)


class PlansMixin(object):
    """Pass's approved plans, from the request to the end (KIT-312)."""

    def request_approve(self, channel, msg, thread, requester, ident, by_bot=False, dm=False):
        if not self.cfg["agent_user_name"].strip():
            self.refuse_request(channel, msg, thread, "This bridge names no agent user "
                                "(agent_user_name), so it cannot start an epic's children. "
                                "Nothing was asked.", "no agent user", ticket=ident)
            return
        epic = self.tracker.epic(ident)
        if not epic or not epic.get("identifier"):
            self.refuse_request(channel, msg, thread, "I can't find %s in the tracker."
                                % ident, "not found", ticket=ident)
            return
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.refuse_request(channel, msg, thread, "%s is on %s, and the bridge acts only "
                                "on %s." % (epic["identifier"], team or "no team",
                                            ", ".join(self.cfg["team_keys"])),
                                "team not planned", ticket=epic["identifier"])
            return
        if epic.get("id") in self.state["epics"]:
            self.refuse_request(channel, msg, thread, "%s is already approved, and I am "
                                "starting its children. Nothing was asked."
                                % epic["identifier"], "already approved",
                                ticket=epic["identifier"])
            return
        problems = epic_problems(epic)
        if self.handed_over(epic):
            problems.append("it is handed to the agent itself, so its children would stack "
                            "on its branch")
        if problems:
            self.refuse_request(channel, msg, thread, "I won't ask about approving %s: %s."
                                % (epic["identifier"], "; ".join(problems)),
                                "not an idea-gate epic", ticket=epic["identifier"])
            return
        self.ask(channel, msg, thread, "approve", epic, requester,
                 approve_parts(epic, requester, self.cfg, by_bot=by_bot, dm=dm),
                 children=fingerprints(epic))

    def handed_over(self, node):
        """True when the ticket is delegated to anyone, or has an agent session."""
        return bool((node.get("delegate") or {}).get("id")
                    or self.tracker.sessions_on(node["id"]))

    def live_session(self, node):
        """True while an agent session on the ticket has not ended, whatever its state says.
        The workspace's sessions are read once a pass, so this costs no extra read."""
        return any((n or {}).get("status") not in poller.SESSION_FINISHED
                   for n in self.tracker.sessions_on(node["id"]))

    def act_approve(self, channel, thread, q, msg):
        """Move the epic to its team's ready state, as the owner, and record it as approved,
        so every pass from now on starts its children in order. Re-read first."""
        def finish(status, text, what, kind="approve-refused", bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind=kind, bind=bind, join=" ",
                      facts={"ticket": q["ticket"]})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if self.refused_over_limit(channel, thread, msg):
            return
        epic = self.tracker.epic(q["ticket"])
        if not epic or not epic.get("id"):
            finish(REFUSED, "I can't find %s in the tracker any more, so nothing was "
                   "approved." % q["ticket"], "refused: gone")
            return
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            finish(REFUSED, "%s is now on %s, which the bridge does not act on, so nothing "
                   "was approved." % (q["ticket"], team or "no team"), "refused: team")
            return
        if epic["id"] in self.state["epics"]:
            finish(DONE, "%s is already approved." % q["ticket"], "done: already",
                   kind="approve-already")
            return
        changed = changes_since(q.get("children") or {}, epic)
        if changed:
            finish(REFUSED, "%s's children changed since I asked (%s), so nothing was "
                   "approved." % (q["ticket"], changed), "refused: changed since asked",
                   bind="Ask again with `approve %s`." % q["ticket"])
            return
        problems = epic_problems(epic)
        if self.handed_over(epic):
            problems.append("it is handed to the agent itself, so its children would stack "
                            "on its branch")
        ready_id, why = self.tracker.team_state(team, self.cfg["ready_state"])
        if not ready_id:
            problems.append(why)
        agent_id, why = self.tracker.agent_user(self.cfg["agent_user_name"])
        if not agent_id:
            problems.append(why)
        if problems:
            finish(REFUSED, "I can't approve %s now: %s." % (q["ticket"], "; ".join(problems)),
                   "refused: %s" % problems[0][:80])
            return
        if self.dry:
            self.say("  [dry-run] would approve %s" % q["ticket"])
            return
        name = self.name_of(msg["user"])
        when = iso(ts_float(msg.get("ts")))
        state = epic.get("state") or {}
        if state.get("id") != ready_id and state.get("type") in ("backlog", "triage",
                                                                 "unstarted"):
            self.tracker.move(epic["id"], ready_id)
            self.state["acted_at"].append(self.now)
        self.state["epics"][epic["id"]] = {
            "ticket": epic["identifier"], "team": team, "channel": channel,
            "thread": thread, "approved_by": msg["user"], "approved_name": name,
            "approved_at": when, "started": [], "noted": [],
            "approved": dict(q.get("children") or {}), "started_at": {}, "failures": 0}
        self.save()
        body = ("Approved in Slack by **%s** (Slack user %s) at %s, through the Slack bridge."
                "\n\nIts children are handed to the agent in dependency order, at most %d at "
                "once, each once the tickets it waits on are Done. Merging stays the owner's."
                % (name, msg["user"], when, self.cfg["epic_wip"]))
        owed = ""
        try:
            self.tracker.comment(epic["id"], body)
        except BridgeError as exc:
            # The approval stands. The record of who approved it is owed.
            self.say("  NOTE: %s was approved, but its attribution comment failed: %s"
                     % (q["ticket"], exc))
            self.owe(epic["id"], q["ticket"], body)
            owed = (" The approval was done, but the comment naming who approved it could not "
                    "be written; I will try again.")
        finish(DONE, "Approved %s for <@%s>. I'll hand its children to the agent in order, "
               "at most %d at a time, each once what it waits on is Done, and say here as "
               "each one starts.%s" % (q["ticket"], msg["user"], self.cfg["epic_wip"], owed),
               "done: approved %s" % q["ticket"], kind="approve-done")
        self.counts["acted"] += 1
        self.say("  acted: approved %s for %s" % (q["ticket"], msg["user"]))

    def note(self, rec, key, text):
        """Say it in the epic's thread once, however many passes find it. The kind is the
        note's key up to its first colon (KIT-297)."""
        if key in rec["noted"]:
            return
        self.post(rec["channel"], rec["thread"], text, kind="epic-" + key.split(":", 1)[0],
                  facts={"epic": rec["ticket"]})
        rec["noted"] = (rec["noted"] + [key])[-200:]

    def drop(self, epic_id, rec, text):
        self.post(rec["channel"], rec["thread"], text, kind="epic-stopped",
                  facts={"epic": rec["ticket"]})
        self.state["epics"].pop(epic_id, None)
        self.say("  stopped following %s" % rec["ticket"])

    def release(self):
        """Every pass: start the next children of each approved epic (KIT-223)."""
        if not self.state["epics"]:
            return
        if not self.cfg["act"]:
            self.say("  acting is off: %d approved epic(s) wait" % len(self.state["epics"]))
            return
        agent_id, why = self.tracker.agent_user(self.cfg["agent_user_name"])
        for epic_id in sorted(self.state["epics"]):
            rec = self.state["epics"][epic_id]
            if not agent_id:
                self.note(rec, "agent:" + why, "I can't start %s's children: %s."
                          % (rec["ticket"], why))
                continue
            # ONE EPIC'S FAILURE IS ITS OWN. A read the tracker refuses must not stop the
            # other epics, or the expiry and pruning after them; the pass still ends loud.
            try:
                self.release_epic(epic_id, rec, agent_id)
                rec["failures"] = 0
            except BridgeError as exc:
                rec["failures"] = rec.get("failures", 0) + 1
                # Redacted BEFORE it is cut short: a cut credential would no longer match.
                err = self.clean(str(exc))[:160]
                self.release_errors.append("%s: %s" % (rec["ticket"], err))
                if rec["failures"] == 3:
                    self.note(rec, "unreadable", "I can't read %s (%s). I'll keep trying "
                              "each pass, and start nothing in it until I can."
                              % (rec["ticket"], err))
            self.save()

    def release_epic(self, epic_id, rec, agent_id):
        ticket = rec["ticket"]
        epic = self.tracker.epic(epic_id)
        if not epic or epic.get("id") != epic_id:
            self.drop(epic_id, rec, "%s is no longer in the tracker, so I've stopped starting "
                      "its children." % ticket)
            return
        state = epic.get("state") or {}
        if state.get("type") == "completed" and self.closed_already(epic_id, rec, epic):
            return
        if state.get("type") in FINISHED:
            self.drop(epic_id, rec, "%s is %s, so I've stopped starting its children."
                      % (ticket, state.get("name") or "closed"))
            return
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.drop(epic_id, rec, "%s is now on %s, which the bridge does not act on, so "
                      "I've stopped starting its children." % (ticket, team or "no team"))
            return
        if self.handed_over(epic):
            self.drop(epic_id, rec, "%s itself was handed to the agent. Its children would "
                      "stack on its branch, so I've stopped starting them." % ticket)
            return
        if state.get("type") in ("backlog", "triage"):
            self.note(rec, "paused", "%s is back in %s, so I've paused starting its children. "
                      "Move it to %s and they go on." % (ticket, state.get("name") or "Backlog",
                                                         self.cfg["ready_state"]))
            return
        rec["noted"] = [n for n in rec["noted"] if n != "paused"]
        approved = rec.get("approved") or {}
        kids = in_order([k for k in children_of(epic) if k["id"] in approved])
        problems = epic_problems(epic, kids)
        if problems:
            self.note(rec, "problems:" + "; ".join(problems), "I've paused starting %s's "
                      "children: %s." % (ticket, "; ".join(problems)))
            return

        def live(k):
            return (k.get("state") or {}).get("type") not in FINISHED
        extras = [k for k in children_of(epic) if k["id"] not in approved and live(k)]
        running = [k for k in extras if self.handed_over(k)
                   or (k.get("state") or {}).get("type") == "started"]
        for k in running:
            self.note(rec, "atwork:" + k["id"], "%s is at work but was not in the plan you "
                      "approved. It counts toward the limit of %d."
                      % (k.get("identifier") or "?", self.cfg["epic_wip"]))
        fresh = [k for k in extras if k not in running and "added:" + k["id"] not in rec["noted"]]
        if fresh:
            self.note(rec, "added:" + fresh[0]["id"], "%s %s added after the approval, so I "
                      "won't hand %s over. Hand %s over yourself if %s yours."
                      % (", ".join(k.get("identifier") or "?" for k in fresh),
                         "was" if len(fresh) == 1 else "were",
                         "it" if len(fresh) == 1 else "them",
                         "it" if len(fresh) == 1 else "them",
                         "it is" if len(fresh) == 1 else "they are"))
            rec["noted"] += ["added:" + k["id"] for k in fresh[1:]]
        unfinished = [k for k in kids if live(k)]
        if not unfinished:
            self.finish_epic(epic_id, rec, epic, kids)
            return
        running += [k for k in unfinished if self.handed_over(k)
                    or (k.get("state") or {}).get("type") == "started"]
        # A session can move its own child to Done while it still runs: a finished child
        # holds its slot until its session ends, and the hold is said once.
        for k in children_of(epic):
            if not live(k) and self.live_session(k):
                running.append(k)
                self.note(rec, "running:" + k["id"], "%s is marked finished, but its session "
                          "is still running. It counts toward the limit of %d until the "
                          "session ends." % (k.get("identifier") or "?", self.cfg["epic_wip"]))
        room = self.cfg["epic_wip"] - len(running)

        def next_waits_on(k):
            """True when an approved child can't start yet but will once k is done: it
            waits on k alone, or on nothing while k holds the last slot. With more running
            than the limit, k's end frees no slot. Nor does it while a session on k has not
            ended, one the agent never picked up included: a child marked finished holds its
            slot until then, by the rule above."""
            mine = k["identifier"] + " ("
            return room >= 0 and not self.live_session(k) and any(
                q not in running and q["identifier"] not in rec["started"]
                and fingerprint(q) == approved.get(q["id"])
                and all(b.startswith(mine) for b in open_blockers(q))
                and (room == 0 or open_blockers(q)) for q in unfinished)
        for k in running:
            since = (rec.get("started_at") or {}).get(k.get("identifier"))
            if (since and self.now - since > STUCK_START_SECONDS
                    and (k.get("state") or {}).get("type") in ("backlog", "triage",
                                                               "unstarted")):
                self.not_started(rec, k, since, next_waits_on)
        waiting, started, held = [], 0, 0
        for k in unfinished:
            if k in running:
                continue
            if k["identifier"] in rec["started"]:
                waiting.append("%s was started once already; hand it over yourself if it "
                               "needs another go" % k["identifier"])
                continue
            if fingerprint(k) != approved.get(k["id"]):
                self.note(rec, "changed:%s:%s" % (k["id"], fingerprint(k)), "%s changed "
                          "after the approval, so I won't hand it over. Hand it over "
                          "yourself if the change is yours." % k["identifier"])
                held += 1
                continue
            blockers = open_blockers(k)
            if blockers:
                waiting.append("%s waits on %s" % (k["identifier"], ", ".join(blockers)))
                continue
            if room <= 0:
                continue
            if self.actions_left() <= 0:
                self.say("  action limit: %s waits for the next hour" % k["identifier"])
                room = 0
                continue
            self.start_child(rec, k, kids, agent_id)
            room -= 1
            started += 1
        if not running and not started and waiting and \
                len(waiting) + held == len(unfinished):
            self.note(rec, "stall:" + "; ".join(waiting), "Nothing in %s can start: %s."
                      % (ticket, "; ".join(waiting)))

    def not_started(self, rec, k, since, next_waits_on):
        """A child handed over two hours ago and still not in a started state, said once as
        its agent sessions show it (KIT-284). While one runs, nothing is said; one the agent
        never picked up is not running. In a hand-off lane its pull request waits for you,
        so it is never called not started, nor offered to be handed over again; while it
        carries `agent:needs-human` it is never called ready to merge either, and that is
        said even after the merge note. Anywhere else, a child whose newest session ran and
        ended stopped short. Any other never began: its newest session not picked up, or
        none at all, unless the list was too long to read whole and holds none: then it is
        said as could not tell, never as no session (contract §13). A newest session made
        after the bridge's hand-over (its own, a hand-over again, or a mention) starts the
        two hours, and the note names its time. A list the tracker would not give fails the
        epic's pass before anything is said."""
        ident, wip = k["identifier"], self.cfg["epic_wip"]
        lane = (k.get("state") or {}).get("name") or "?"
        sessions = [n or {} for n in self.tracker.sessions_on(k["id"])]
        statuses = [n.get("status") for n in sessions]
        if any(s in SESSION_RUNNING for s in statuses):
            return
        if lane in self.cfg["handoff_states"]:
            if NEEDS_HUMAN_LABEL in set((n or "").lower() for n in label_names(k)):
                self.note(rec, "needs-human:" + k["id"], "%s is marked as needing a person, "
                          "and its pull request is waiting on you. That usually means its "
                          "review ran out of rounds with findings still open. It's yours to "
                          "fix and merge, or cancel. It still counts toward the limit of %d."
                          % (ident, wip))
            else:
                self.note(rec, "handoff:" + k["id"], "%s's pull request is waiting for you "
                          "to merge.%s" % (ident, " The next piece starts once it's merged."
                                           if next_waits_on(k) else ""))
        elif self.newest_ended(sessions):
            self.note(rec, "ended:" + k["id"], "%s's session ended before it finished, and "
                      "it's in %s now. It still counts toward the limit of %d: hand it over "
                      "again yourself, or cancel it." % (ident, lane, wip))
        elif not statuses and not self.tracker.sessions_read_whole():
            self.note(rec, "unsure:" + k["id"], "%s was handed to the agent at %s and is "
                      "still in %s. I couldn't tell whether its session is running: the "
                      "tracker holds more agent sessions than I can read at once. It still "
                      "counts toward the limit of %d. Check it in the tracker before you "
                      "hand it over again." % (ident, iso(since), lane, wip))
        else:
            since = self.handed_at(sessions, since)
            if self.now - since <= STUCK_START_SECONDS:
                return
            self.note(rec, "stuck:" + k["id"], "%s was handed to the agent at %s and has "
                      "not started. Its session may have stopped. It still counts toward "
                      "the limit of %d: hand it over again yourself, or cancel it."
                      % (ident, iso(since), wip))

    def handed_at(self, sessions, since):
        """When the two hours start for a child the bridge handed over at `since`, once its
        newest session has not ended: at `since`, or at that session's own `createdAt` when
        it was made later (the bridge's own session, a hand-over again, or a mention). A time
        not in the tracker's own form, UTC ending in Z, never moves the clock (one with no
        zone would be read as this machine's local time), and nor does one more than
        JUST_MADE_SECONDS past this pass. One less far past it was made while this pass ran,
        or by a clock a little ahead of this one, so it counts as just made and the pass waits."""
        text = (self.newest(sessions) or {}).get("createdAt") or ""
        if not text.endswith("Z"):
            return since
        try:
            at = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return since
        return at if since < at <= self.now + JUST_MADE_SECONDS else since

    def start_child(self, rec, child, kids, agent_id):
        ident = child["identifier"]
        if self.dry:
            self.say("  [dry-run] would hand %s to the agent" % ident)
            return
        self.tracker.delegate(child["id"], agent_id)
        self.state["acted_at"].append(self.now)
        rec["started"].append(ident)
        rec.setdefault("started_at", {})[ident] = self.now
        self.save()
        after = [b.get("identifier") or "?" for b, _st in blockers_of(child)]
        body = ("Started by the Slack bridge for **%s** (Slack user %s), who approved %s in "
                "Slack at %s." % (rec["approved_name"], rec["approved_by"], rec["ticket"],
                                  rec["approved_at"]))
        if after:
            body += "\n\nIt waited on %s, now Done." % ", ".join(after)
        owed = ""
        try:
            self.tracker.comment(child["id"], body)
        except BridgeError as exc:
            # The hand-over stands: it cannot be taken back. The record of who approved it
            # is owed.
            self.say("  NOTE: %s was started, but its attribution comment failed: %s"
                     % (ident, exc))
            self.owe(child["id"], ident, body)
            owed = (" The hand-over was done, but the comment naming who approved it could "
                    "not be written; I will try again.")
        position = [k["id"] for k in kids].index(child["id"]) + 1
        title = notify.safe_title(child.get("title") or "")
        self.post(rec["channel"], rec["thread"], "Started %s “%s” (%d of %d)%s.%s" % (
            ident, title, position, len(kids),
            ", now that %s %s Done" % (", ".join(after), "is" if len(after) == 1 else "are")
            if after else "", owed), kind="started-child",
            facts={"ticket": ident, "title": title, "epic": rec["ticket"],
                   "position": position, "of": len(kids), "waited_on": after})
        self.counts["acted"] += 1
        self.say("  acted: handed %s to the agent (%s)" % (ident, rec["ticket"]))

    # -- a finished plan (KIT-299) -------------------------------------------------
    def finish_epic(self, epic_id, rec, epic, kids):
        """Every approved piece is finished: close the plan, once every child of it is Done
        (an extra one included: a plan is not done while any piece of it is open), none was
        canceled, no session on one still runs, and no pull request the GitHub integration
        linked is still open, or closed with none merged. Each hold is said once, and the
        plan is still followed; one held once its pieces were read (by a pull request, a
        done state the team lacks, the action limit) reads them again at most every
        FINISH_RECHECK_SECONDS. `kids` are the approved pieces, all finished here."""
        ticket, done = rec["ticket"], self.cfg["done_state"]
        pieces = in_order(children_of(epic)) or kids
        for k in pieces:
            ident, kind = k.get("identifier") or "?", (k.get("state") or {}).get("type")
            if kind == "canceled":
                self.note(rec, "canceled:" + k["id"], "%s was canceled, so not every piece of "
                          "%s shipped, and I won't mark it %s. Close it yourself when you're "
                          "happy with it." % (ident, ticket, done))
                return
            if kind != "completed":
                self.note(rec, "open:" + k["id"], "%s is still %s, so I'll mark %s %s once "
                          "it's finished too." % (ident, (k.get("state") or {}).get("name")
                                                  or "open", ticket, done))
                return
        for k in pieces:
            if self.live_session(k):
                self.note(rec, "ending:" + k["id"], "%s is marked finished, but its session "
                          "is still running. I'll mark %s %s once it ends."
                          % (k.get("identifier") or "?", ticket, done))
                return
        if self.now - rec.get("finish_read", 0) < FINISH_RECHECK_SECONDS:
            return
        # Recorded before the read, so whatever holds the close after it, the pieces are
        # read again only that much later.
        rec["finish_read"] = self.now
        read = self.read_pieces(pieces)
        held = [(p, piece_hold(p)) for p in read]
        held = [(p, why) for p, why in held if why]
        if held:
            for p, why in held:
                self.note(rec, "unmerged:%s:%s" % (p["id"], why), "%s, so I haven't marked "
                          "%s %s. I'll look again." % (why, ticket, done))
            return
        self.close_epic(epic_id, rec, epic, read, move=True)

    def read_pieces(self, kids):
        """Each piece as the tracker shows it now: its pull requests, from the GitHub
        integration's own links, whether it has more links than one read holds, and what
        its newest review left. One read a piece."""
        out = []
        for k in kids:
            node = self.tracker.piece(k["id"]) or {}
            review, partial, unreadable = piece_review(node, self.cfg["owner_user_id"])
            out.append({"id": k["id"], "ident": k.get("identifier") or "?",
                        "title": k.get("title") or "", "prs": piece_prs(node),
                        "links_over": bool((((node.get("attachments") or {}).get("pageInfo"))
                                            or {}).get("hasNextPage")),
                        "review": review, "partial": partial, "unreadable": unreadable,
                        "more": bool((((node.get("comments") or {}).get("pageInfo"))
                                      or {}).get("hasNextPage"))})
        return out

    def closed_already(self, epic_id, rec, epic):
        """The plan is in a done state. One this bridge was closing when a pass stopped
        after the move is finished now, never moved again. One someone else closed once
        every piece was finished is said and let go, with no move. False otherwise: a plan
        closed before its pieces were finished is said as before."""
        kids = children_of(epic)
        if rec.get("closing"):
            self.close_epic(epic_id, rec, epic, self.read_pieces(in_order(kids)), move=False)
            return True
        if kids and all((k.get("state") or {}).get("type") == "completed" for k in kids):
            self.drop(epic_id, rec, "%s is already %s, and every piece of it is finished, so "
                      "there's nothing left for me to close. I've stopped following it."
                      % (rec["ticket"], (epic.get("state") or {}).get("name") or "done"))
            return True
        return False

    def close_epic(self, epic_id, rec, epic, read, move):
        """Move the plan to its team's done state, as the owner, comment on it, post the
        wrap-up, ask about the notes left, and stop following it. Each step is recorded as
        it lands: `closing` before the move, so a pass that stops after it is finished by
        closed_already without moving again, then `closed_at`, `commented`, `wrapped` and
        `asked`, so no pass after one that stopped repeats a step. The question is recorded
        before the plan is let go, and needs nothing in its record."""
        ticket = rec["ticket"]
        team = (epic.get("team") or {}).get("key") or rec.get("team")
        done = (self.cfg["done_state"] if move
                else (epic.get("state") or {}).get("name") or self.cfg["done_state"])
        if move and not rec.get("closed_at"):
            done_id, why = self.tracker.team_state(team, done, "completed")
            if not done_id:
                self.note(rec, "nodone:" + why, "Every piece of %s is finished, but I can't "
                          "mark it %s: %s." % (ticket, done, why))
                return
            if self.actions_left() <= 0:
                self.say("  action limit: closing %s waits for the next hour" % ticket)
                return
            if self.dry:
                self.say("  [dry-run] would mark %s %s and post its wrap-up" % (ticket, done))
                return
            rec["closing"] = self.now
            self.save()
            if (epic.get("state") or {}).get("id") != done_id:
                self.tracker.move(epic["id"], done_id)
                self.state["acted_at"].append(self.now)
            rec["closed_at"] = self.now
            self.save()
        elif self.dry:
            self.say("  [dry-run] would finish closing %s" % ticket)
            return
        if not rec.get("commented"):
            body = closing_comment(rec, read, done, self.now)
            try:
                self.tracker.comment(epic["id"], body)
            except BridgeError as exc:
                # The move stands: it cannot be taken back. The record of it is owed.
                self.say("  NOTE: %s was closed, but its comment failed: %s" % (ticket, exc))
                self.owe(epic["id"], ticket, body)
            rec["commented"] = True
            self.save()
        notes = notes_of(read)
        if not rec.get("wrapped"):
            text, notes_part, about, facts = wrap_up_parts(epic, read, notes, done)
            self.post(rec["channel"], rec["thread"], text, kind="plan-finished",
                      bind=notes_part, join="\n", plain=about, facts=facts)
            rec["wrapped"] = True
            self.save()
        if notes and not rec.get("asked"):
            self.ask_followup(rec, epic, team, notes)
            rec["asked"] = True
            self.save()
        self.state["epics"].pop(epic_id, None)
        self.save()
        self.counts["acted"] += 1
        self.say("  acted: closed %s as %s, %d review note(s) left" % (ticket, done,
                                                                        len(notes)))

    def ask_followup(self, rec, epic, team, notes):
        """ONE question once a plan closes with review notes left, built like the others: a
        yes binds to these exact notes and to the terms the text states, never to the
        epic's record, which is let go once it is asked."""
        channel = rec["channel"]
        dm = channel in self.dm_channels
        thread = None if dm else rec["thread"]
        head, about, bind, facts = followup_parts(rec["ticket"], len(notes), team, self.cfg,
                                                  dm=dm)
        posted = self.post(channel, thread, head, kind="question-followup", bind=bind,
                           join=" ", plain=about, facts=facts)
        self.state["questions"][key_of(channel, posted)] = {
            "kind": FOLLOWUP, "ticket": rec["ticket"], "ticket_id": epic.get("id"),
            "channel": channel, "thread": thread, "ts": posted, "asked_at": self.now,
            "asked_for": rec.get("approved_by") or "", "status": OPEN,
            "fingerprint": notes_fingerprint(notes), "promised": self.promised(FOLLOWUP, team),
            "team": team, "notes": len(notes)}
        self.counts["asked"] += 1
        self.say("  asked: followup %s (%d notes)" % (rec["ticket"], len(notes)))

    def act_followup(self, channel, thread, q, msg):
        """File the notes a closed plan's reviews left as ONE ticket in its team, as the
        owner: a title and a description, and nothing else, so it starts no work. Re-read
        first: a yes after the notes changed files nothing. At most once: recorded before
        the write, so a pass that stops after it never files twice."""
        def finish(status, text, what):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind="followup-" + ("filed" if status == DONE
                                                                 else "refused"),
                      facts={"ticket": q["ticket"]})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if q.get("filing") and not q.get("filed"):
            finish(REFUSED, "I started filing the notes from %s, but I can't confirm the "
                   "ticket was made, so I won't file it again. Look for “Follow-ups from” in "
                   "%s's tickets." % (q["ticket"], q.get("team") or "the team"),
                   "refused: filing not confirmed")
            return
        if not q.get("filed"):
            if self.refused_over_limit(channel, thread, msg):
                return
            epic = self.tracker.epic(q.get("ticket_id") or q["ticket"])
            if not epic or not epic.get("id"):
                finish(REFUSED, "I can't find %s in the tracker any more, so nothing was "
                       "filed." % q["ticket"], "refused: gone")
                return
            team = (epic.get("team") or {}).get("key")
            if team != q.get("team") or team not in self.cfg["team_keys"]:
                finish(REFUSED, "%s is now on %s, so nothing was filed, and I have closed the "
                       "question." % (q["ticket"], team or "no team"), "refused: team")
                return
            read = self.read_pieces(in_order(children_of(epic)))
            notes = notes_of(read)
            if notes_fingerprint(notes) != q.get("fingerprint"):
                finish(REFUSED, "The review notes on %s's pieces changed since I asked, so "
                       "nothing was filed, and I have closed the question." % q["ticket"],
                       "refused: changed since asked")
                return
            team_id, why = self.tracker.team_id(team)
            if not team_id:
                finish(REFUSED, "I can't file the notes from %s: %s." % (q["ticket"], why),
                       "refused: no team")
                return
            if self.dry:
                self.say("  [dry-run] would file %d notes from %s" % (len(notes), q["ticket"]))
                return
            # The name is the member's own to set: relayed text, like the answer's (KIT-224).
            name = _relayed(self.name_of(msg["user"]), 120) or msg["user"]
            when = iso(ts_float(msg.get("ts")))
            q["filing"] = self.now
            self.save()
            made = self.tracker.file_followup(team_id, followup_title(epic),
                                              followup_description(epic, notes, name,
                                                                   msg["user"], when, read))
            self.state["acted_at"].append(self.now)
            q.update(filed=made.get("identifier"), filed_url=made.get("url") or "",
                     filed_title=followup_title(epic), filed_notes=len(notes),
                     epic_id=epic["id"], filed_body=(
                         "Follow-ups from this plan's reviews are in %s%s: %d note(s) from "
                         "its pieces, filed by the Slack bridge for **%s** (Slack user %s), "
                         "who said yes in Slack at %s."
                         % (made.get("identifier"), " (%s)" % made["url"] if made.get("url")
                            else "", len(notes), name, msg["user"], when)))
            self.save()
        owed = ""
        if not q.get("linked"):
            try:
                self.tracker.comment(q["epic_id"], q["filed_body"])
            except BridgeError as exc:
                # The ticket stands: it cannot be taken back. The link to it is owed.
                self.say("  NOTE: %s was filed, but the comment on %s failed: %s"
                         % (q["filed"], q["ticket"], exc))
                self.owe(q["epic_id"], q["ticket"], q["filed_body"])
                owed = (" The ticket was filed, but the comment on %s linking it could not be "
                        "written; I will try again." % q["ticket"])
            q["linked"] = True
            self.save()
        finish(DONE, "Filed %s “%s” in %s for <@%s>, with %d note%s. Nothing starts "
               "on it until someone hands it over.%s"
               % (q["filed"], notify.safe_title(q["filed_title"]), q.get("team") or "?",
                  msg["user"], q["filed_notes"], "" if q["filed_notes"] == 1 else "s", owed),
               "done: filed %s" % q["filed"])
        self.counts["acted"] += 1
        self.say("  acted: filed %s from %s for %s" % (q["filed"], q["ticket"], msg["user"]))


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline. This module's cases run in the bridge's own selftest, end to end
# through one pass; this one pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__))


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    print("usage: pipeline_bridge_plans.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
