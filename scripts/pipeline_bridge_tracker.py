#!/usr/bin/env python3
"""The Slack bridge's tracker client: its reads, and its four writes (KIT-312).

    python3 scripts/pipeline_bridge_tracker.py --selftest

A MODULE OF ITS OWN (KIT-312, part 3). scripts/pipeline_bridge.py was one file of more than
12,000 lines, so Stage E withheld it from every review it was in, and every bridge feature
conflicted with every other. It is split by concern, with no change in behaviour: each
query and method here is main's own text, moved unchanged. pipeline_bridge imports each
one back, so `pipeline_bridge.TrackerClient` is still the very same object.

WHAT IS HERE
  TrackerClient  the tracker at one fixed endpoint, with the owner's key: the reads the
                 bridge makes, by the planner's own rules where the planner has them, and
                 the four writes the bridge's selftest pins: a move, a hand-over, a
                 comment, and a finished plan's follow-up.
  Its cases      the real client with only the HTTP call stood in for: an error status, a
                 refusal, the planner's own functions, the session list, and a re-plan's
                 searches. The bridge's selftest runs them too, each in its place.
"""
import json
import os
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The planner job's own rules for what is live work, and its own team query: the bridge
# refuses exactly what the planner would, so it never moves a ticket the planner refuses.
import pipeline_plan_poller as poller  # noqa: E402
# The bridge's error and its one HTTP call, in a module of their own (KIT-388). `_http_json`
# is imported by name, so the tracker client below calls it through THIS module's globals,
# which is where its tests stand in for it.
from pipeline_bridge_transport import BridgeError, _http_json  # noqa: E402
from pipeline_bridge_config import EXIT_USAGE, run_idea  # noqa: E402
from pipeline_bridge_plans import MAX_CHILDREN, PIECE_LINKS  # noqa: E402

# ══════════════════════════════════════════════════════════════════════════════════════
# Transports — the tracker: reads, and the four tracker writes the selftest pins, made only
# with `act` on. Slack's side, and the one HTTP call, are in pipeline_bridge_transport.py.
# ══════════════════════════════════════════════════════════════════════════════════════

TRACKER_API = "https://api.linear.app/graphql"
Q_VIEWER = "query BridgeViewer { viewer { id } }"
Q_ISSUE = """
query BridgeIssue($id: String!) {
  issue(id: $id) {
    id identifier title description createdAt
    state { id name type }
    team { key }
    labels(first: 50) { nodes { name } }
    creator { id name displayName }
  }
}"""
# KIT-341: who made each move and added each label, so only the owner's key is trusted.
# Read whole, page by page, as the planner reads an idea's history.
Q_TRUST_HISTORY = """
query BridgeTrustHistory($id: String!, $after: String) {
  issue(id: $id) {
    history(first: 50, after: $after) {
      nodes { createdAt actorId toStateId addedLabelIds }
      pageInfo { hasNextPage endCursor }
    }
  }
}"""
# An epic and its children, with what blocks each child. The page sizes are bounded, and a
# page that runs over is refused, never read as complete.
Q_EPIC = """
query BridgeEpic($id: String!) {
  issue(id: $id) {
    id identifier title
    state { id name type }
    team { key }
    delegate { id }
    parent { id }
    creator { id }
    labels(first: 20) { nodes { id name } }
    children(first: %d) {
      nodes {
        id identifier title description
        project { id }
        state { id name type }
        team { key }
        delegate { id }
        labels(first: 20) { nodes { id name } }
        children(first: 1) { nodes { id } }
        inverseRelations(first: 20) {
          nodes { type issue { id identifier state { name type } } }
          pageInfo { hasNextPage }
        }
      }
      pageInfo { hasNextPage }
    }
  }
}""" % MAX_CHILDREN
# One finished piece of a plan (KIT-299): the links on it, with what the integration says of
# each, and its newest comments, newest first, with who wrote each. Read only once a plan's
# every piece is finished, one piece at a time, so no page multiplies another.
Q_PIECE = """
query BridgePiece($id: String!) {
  issue(id: $id) {
    id identifier
    attachments(first: %d) {
      nodes { url sourceType metadata }
      pageInfo { hasNextPage }
    }
    comments(first: 100, orderBy: createdAt) {
      nodes { body createdAt user { id } }
      pageInfo { hasNextPage }
    }
  }
}""" % PIECE_LINKS
# A re-plan's searches (KIT-244): the planner's planning tickets for one idea, and the epics
# the executor filed from them, each found by a line the planner or the executor wrote.
# Archived tickets count: an archived run still ran, and an archived epic still stands
# unless it was canceled. A page that runs over is refused, never read as complete.
Q_FIND_BY_LINE = """
query BridgeFindByLine($filter: IssueFilter!, $after: String) {
  issues(filter: $filter, first: 50, after: $after, includeArchived: true) {
    nodes { id identifier description state { name type } creator { id } }
    pageInfo { hasNextPage endCursor }
  }
}"""
# A search for `Idea: PROD-6` also finds every run for PROD-60 to PROD-69 and on, filtered
# out afterwards. So the matches are read page by page, up to this many pages.
FIND_MAX_PAGES = 6
# THE BRIDGE'S ONLY FOUR TRACKER WRITES (KIT-222, KIT-223, KIT-299). The selftest pins
# them: no other write exists in this file, the move carries `stateId` and nothing else,
# the delegation carries `delegateId` and nothing else, and a finished plan's follow-up is
# filed with a team, a title and a description and nothing else: no label, no assignee, no
# delegate, and no state but the team's default, so it starts no work.
M_MOVE = """
mutation BridgeMove($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) { success }
}"""
M_DELEGATE = """
mutation BridgeDelegate($id: String!, $delegateId: String!) {
  issueUpdate(id: $id, input: { delegateId: $delegateId }) { success }
}"""
M_COMMENT = """
mutation BridgeComment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}"""
M_FOLLOWUP = """
mutation BridgeFollowUp($teamId: String!, $title: String!, $description: String!) {
  issueCreate(input: { teamId: $teamId, title: $title, description: $description }) {
    success issue { id identifier url }
  }
}"""


class TrackerClient(object):
    """The tracker, at one fixed endpoint: reads, and the writes the selftest pins."""

    def __init__(self, key, key_env=""):
        self.key = key
        self.key_env = key_env
        self._sessions = None
        self._sessions_error = ""

    def _query(self, query, variables):
        try:
            doc = _http_json(TRACKER_API, {"Authorization": self.key,
                                           "Content-Type": "application/json"},
                             {"query": query, "variables": variables})
        except urllib.error.HTTPError as exc:
            # The tracker answers an unknown id with an error STATUS and a GraphQL body.
            # Read the body before giving up, or one mistyped request stops every pass. A
            # body with no GraphQL errors in it (an outage, a proxy's 503, nothing at all)
            # is "could not check", never "not found".
            try:
                doc = json.loads((exc.read() or b"").decode("utf-8") or "{}")
            except (AttributeError, OSError, ValueError):
                doc = None
            if not (isinstance(doc, dict) and doc.get("errors")):
                raise BridgeError("the tracker answered HTTP %s" % exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BridgeError("the tracker could not be read (%s)" % type(exc).__name__)
        if not isinstance(doc, dict) or doc.get("errors"):
            msgs = [str(e.get("message"))[:120] for e in (doc or {}).get("errors") or []
                    if isinstance(e, dict)] if isinstance(doc, dict) else []
            if any("not found" in m.lower() or "entity not found" in m.lower() for m in msgs):
                return None
            raise BridgeError("the tracker refused a read: %s" % ("; ".join(msgs) or "?"))
        if not isinstance(doc.get("data"), dict):
            raise BridgeError("the tracker's answer carried no data")
        return doc["data"]

    def viewer_id(self):
        return ((self._query(Q_VIEWER, {}) or {}).get("viewer") or {}).get("id")

    def issue(self, identifier):
        data = self._query(Q_ISSUE, {"id": identifier})
        return (data or {}).get("issue")

    def linear(self, query, variables):
        """The planner's own call shape, so its functions run unchanged."""
        return self._query(query, variables) or {}

    def live_work(self, issue_id):
        """Why the ticket is live work, by the planner's own rules, or []. The session
        listing is this pass's own, read once: a yes is always handled a pass after its
        question, and each pass makes a new client."""
        return poller.live_work_reasons(self.linear, issue_id, self.session_index())

    def epic(self, identifier):
        data = self._query(Q_EPIC, {"id": identifier})
        return (data or {}).get("issue")

    def trust_history(self, issue_id):
        """Every entry of the issue's history: who moved it where, and who added which
        labels (KIT-341). A page that holds no list, or a history past the bound, raises:
        a part read is never taken for the whole."""
        nodes, after = [], None
        for _ in range(poller.HISTORY_MAX_PAGES):
            data = self._query(Q_TRUST_HISTORY, {"id": issue_id, "after": after}) or {}
            conn = (data.get("issue") or {}).get("history")
            if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list):
                raise BridgeError("the tracker's answer held no history for %s" % issue_id)
            nodes.extend(conn["nodes"])
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return nodes
            after = info.get("endCursor")
        raise BridgeError("the history of %s runs past %d pages" % (issue_id,
                                                                    poller.HISTORY_MAX_PAGES))

    def session_index(self):
        """The workspace's recent agent sessions, read once a pass. A read that failed is
        never kept as the list, not even the part that came back: each later ask this pass
        fails the same way without reading again. So no ticket is ever said to have no
        session because the list could not be read (KIT-284, contract §13)."""
        if self._sessions_error:
            raise BridgeError(self._sessions_error)
        if self._sessions is None:
            index = poller.SessionIndex(self._session_page)
            try:
                index.nodes()
            except BridgeError as exc:
                self._sessions_error = "the agent sessions could not be read: %s" % exc
                raise BridgeError(self._sessions_error)
            self._sessions = index
        return self._sessions

    def _session_page(self, query, variables):
        """One page of the session list. An answer that holds no whole page, its list and
        where the list goes on, is a read that failed, never an empty list."""
        data = self._query(query, variables)
        conn = (data or {}).get("agentSessions")
        if not (isinstance(conn, dict) and isinstance(conn.get("nodes"), list)
                and isinstance(conn.get("pageInfo"), dict)):
            raise BridgeError("the tracker's answer held no list of agent sessions")
        return data

    def sessions_on(self, issue_id):
        """Agent sessions on the ticket: the workspace's recent sessions, read once a pass."""
        return self.session_index().on_issue(issue_id)

    def sessions_read_whole(self):
        """True when this pass read the session list to its end. Only then does a ticket
        with none in it have none: past the pages one pass reads, an older one may sit."""
        return self.session_index().complete

    def agent_user(self, name):
        """(user id, None), or (None, why) unless exactly one active app user has that
        display name — the planner's own query."""
        data = self.linear(poller.Q_AGENT, {"filter": {"displayName": {"eq": name},
                                                       "app": {"eq": True}}})
        users = ((data.get("users") or {}).get("nodes")) or []
        if len(users) != 1:
            return None, "%d app users are called %r" % (len(users), name)
        if not users[0].get("active"):
            return None, "the app user %r is not active" % name
        return users[0]["id"], None

    def _bounce(self):
        """The bounce driver, for its paged comment reader and its thread reply: the route
        proven to resume a session (2026-09-08). Given the key's NAME, never the key."""
        import pipeline_bounce_local as pbl
        return pbl, {"linear_api_key_env": self.key_env}

    def recorded_thread(self, ticket_id, thread_id, agent_id):
        """(issue uuid, thread id) when the RECORDED thread is still a root comment carrying
        a session of the agent user; (uuid, None) otherwise. Never picks a thread itself."""
        pbl, bcfg = self._bounce()
        try:
            issue = pbl.linear_issue(ticket_id, bcfg)
        except Exception as exc:                                   # noqa: BLE001
            raise BridgeError("the tracker could not read %s (%s)"
                              % (ticket_id, type(exc).__name__))
        for comment in (((issue or {}).get("comments") or {}).get("nodes") or []):
            if comment.get("id") != thread_id:
                continue
            session = comment.get("agentSession") or {}
            if (not comment.get("parent")
                    and (session.get("appUser") or {}).get("id") == agent_id):
                return (issue or {}).get("id"), thread_id
            break
        return (issue or {}).get("id"), None

    def reply_in_thread(self, issue_uuid, thread_id, body):
        pbl, bcfg = self._bounce()
        return pbl.linear_reply_in_thread(issue_uuid, thread_id, body, bcfg)

    def delegate(self, issue_id, agent_id):
        data = self._query(M_DELEGATE, {"id": issue_id, "delegateId": agent_id}) or {}
        if not (data.get("issueUpdate") or {}).get("success"):
            raise BridgeError("the tracker would not hand the ticket to the agent")

    def team_state(self, team_key, name, kind="unstarted"):
        """(state id, None), or (None, why) unless the team has exactly one state of that
        name and it is of that kind: a to-do (unstarted) state, as the planner requires, or
        a done (completed) one for a finished plan (KIT-299)."""
        nodes = ((self.linear(poller.Q_TEAM, {"key": team_key}).get("teams") or {})
                 .get("nodes")) or []
        if len(nodes) != 1:
            return None, "%d teams have the key %s" % (len(nodes), team_key)
        states = [s for s in ((nodes[0].get("states") or {}).get("nodes") or [])
                  if (s.get("name") or "") == name]
        if len(states) != 1:
            return None, "%s has %d states named %r" % (team_key, len(states), name)
        if states[0].get("type") != kind:
            return None, "%s's %r state is not a %s state" % (
                team_key, name, "done" if kind == "completed" else "to-do")
        return states[0]["id"], None

    def team_id(self, team_key):
        """(team id, None), or (None, why) unless exactly one team has the key: the planner's
        own team query, read for a follow-up's team (KIT-299)."""
        nodes = ((self.linear(poller.Q_TEAM, {"key": team_key}).get("teams") or {})
                 .get("nodes")) or []
        if len(nodes) != 1 or not nodes[0].get("id"):
            return None, "%d teams have the key %s" % (len(nodes), team_key)
        return nodes[0]["id"], None

    def backlog_state(self, team_key):
        """(id, name, None) for the team's first backlog state by position, or (None, None,
        why): where a re-plan parks an idea for a moment (KIT-244). A backlog state starts
        nothing: no job acts on an idea that enters one."""
        nodes = ((self.linear(poller.Q_TEAM, {"key": team_key}).get("teams") or {})
                 .get("nodes")) or []
        if len(nodes) != 1:
            return None, None, "%d teams have the key %s" % (len(nodes), team_key)
        backlog = sorted((s for s in ((nodes[0].get("states") or {}).get("nodes") or [])
                          if s.get("type") == "backlog" and s.get("id")),
                         key=lambda s: s.get("position") or 0)
        if not backlog:
            return None, None, "%s has no backlog state" % team_key
        return backlog[0]["id"], backlog[0].get("name") or "Backlog", None

    def find_by_line(self, team_key, lines):
        """Tickets on one team whose description holds any of these lines, archived ones
        included (KIT-244), read page by page. More than FIND_MAX_PAGES pages is refused:
        "could not read them all" is never "there are none"."""
        if not lines:
            return []
        where = [{"description": {"contains": line}} for line in lines]
        flt = {"team": {"key": {"eq": team_key}}}
        flt.update(where[0] if len(where) == 1 else {"or": where})
        nodes, after = [], None
        for _ in range(FIND_MAX_PAGES):
            conn = ((self._query(Q_FIND_BY_LINE, {"filter": flt, "after": after}) or {})
                    .get("issues") or {})
            nodes.extend(conn.get("nodes") or [])
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return nodes
            after = info.get("endCursor")
            if not after:
                break
        raise BridgeError("more tickets on %s match than the bridge reads (%d pages)"
                          % (team_key, FIND_MAX_PAGES))

    def planning_runs(self, team_key, idea, owner):
        """The planner's planning tickets for one idea, open or closed: the planner's own
        tickets, filed with the owner's key (run_idea), never a look-alike (KIT-244)."""
        return [n for n in self.find_by_line(team_key, [poller.IDEA_PREFIX + idea])
                if run_idea(n, owner) == idea]

    def filed_plans(self, team_key, runs, owner):
        """The epics the executor filed from any of these planning tickets: each carries
        that run's receipt, `Plan id: <run>/<digest>`, and was filed with the owner's key, as
        the executor files (KIT-244)."""
        lines = [poller.executor.PLAN_RECEIPT_PREFIX + r + "/" for r in runs]
        return [n for n in self.find_by_line(team_key, lines)
                if any(line in (n.get("description") or "") for line in lines)
                and (n.get("creator") or {}).get("id") == owner]

    def plan_trigger(self, issue_id, plan_it_id):
        """The move into Plan it the planner would act on now, read the planner's own way
        (pipeline_plan_poller.latest_trigger), or None (KIT-244)."""
        try:
            return poller.latest_trigger(self.linear, issue_id, plan_it_id)
        except poller.PollerError as exc:
            raise BridgeError("the history of the idea could not be read whole: %s" % exc)

    def piece(self, issue_id):
        """One finished piece of a plan: its links and its newest comments (KIT-299)."""
        data = self._query(Q_PIECE, {"id": issue_id})
        return (data or {}).get("issue")

    def file_followup(self, team_id, title, description):
        """The follow-up a finished plan's review notes become, on a member's yes (KIT-299):
        a team, a title and a description, and nothing else. The new ticket's id, key and
        link; an answer that names none raises, and the caller never files again."""
        data = self._query(M_FOLLOWUP, {"teamId": team_id, "title": title,
                                        "description": description}) or {}
        made = data.get("issueCreate") or {}
        if not made.get("success") or not (made.get("issue") or {}).get("identifier"):
            raise BridgeError("the tracker would not file the follow-up ticket")
        return made["issue"]

    def move(self, issue_id, state_id):
        data = self._query(M_MOVE, {"id": issue_id, "stateId": state_id}) or {}
        if not (data.get("issueUpdate") or {}).get("success"):
            raise BridgeError("the tracker would not move the ticket")

    def comment(self, issue_id, body):
        data = self._query(M_COMMENT, {"issueId": issue_id, "body": body}) or {}
        if not (data.get("commentCreate") or {}).get("success"):
            raise BridgeError("the tracker would not take the comment")



# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline: this module's cases, moved here from the bridge's selftest (KIT-312),
# and its pins. The bridge's selftest runs these cases too, each in its place among the
# others, with one kit for all of them.
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_errors(kit):
    """The tracker's error status read as not found: its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    import re
    from pipeline_bridge import blockers_of
    with kit as tmp:
        check = kit.check
        # The tracker's error STATUS with a not-found body is "not found", not a stall.
        import io as _io

        def http_404_like(*_a, **_k):
            raise urllib.error.HTTPError(TRACKER_API, 400, "Bad Request", {}, _io.BytesIO(
                b'{"errors": [{"message": "Entity not found: Issue"}]}'))
        real_http = globals()["_http_json"]
        globals()["_http_json"] = http_404_like
        try:
            check("tracker-not-found-on-an-error-status", TrackerClient("k").issue("PROD-1"),
                  None)
        finally:
            globals()["_http_json"] = real_http
        # The real tracker client runs the PLANNER'S OWN functions unchanged (KIT-222).
        sent = []
        idea_labels = [[{"name": "track:x"}]]
        agents = [[{"id": "agent-1", "active": True}]]

        def kid(n, blockers):
            return {"id": "uuid-%d" % n, "identifier": "PROD-%d" % n, "title": "c",
                    "state": {"id": "s", "name": "Backlog", "type": "backlog"},
                    "team": {"key": "PROD"}, "delegate": None,
                    "labels": {"nodes": [{"name": "provenance:epic"}]},
                    "children": {"nodes": []},
                    "inverseRelations": {"nodes": [
                        {"type": "related", "issue": {"id": "x", "identifier": "PROD-9",
                                                      "state": {"name": "B",
                                                                "type": "backlog"}}}] + [
                        {"type": "blocks", "issue": {"id": "uuid-%d" % b,
                                                     "identifier": "PROD-%d" % b,
                                                     "state": {"name": "Backlog",
                                                               "type": "backlog"}}}
                        for b in blockers]}}

        def by_operation(url, headers, payload=None, timeout=20):
            op = re.search(r"(?:query|mutation)\s+(\w+)", payload["query"]).group(1)
            sent.append((op, payload["variables"]))
            answers = {
                "PlanIdea": {"issue": {"id": "u", "delegate": None, "parent": None,
                                       "children": {"nodes": []},
                                       "labels": {"nodes": idea_labels[0]},
                                       "attachments": {"nodes": []}}},
                "PlanSessions": {"agentSessions": {"nodes": [],
                                                   "pageInfo": {"hasNextPage": False}}},
                "PlanTeam": {"teams": {"nodes": [{"id": "team", "key": "PROD", "states": {
                    "nodes": [{"id": "st-plan-it", "name": "Plan it", "type": "unstarted"},
                              {"id": "st-x", "name": "Doing", "type": "started"}]},
                    "labels": {"nodes": []}}]}},
                "BridgeMove": {"issueUpdate": {"success": True}},
                "BridgeComment": {"commentCreate": {"success": True}},
                "BridgeDelegate": {"issueUpdate": {"success": True}},
                "PlanAgent": {"users": {"nodes": agents[0]}},
                "BridgeEpic": {"issue": {
                    "id": "uuid-50", "identifier": "PROD-50", "title": "e",
                    "state": {"id": "s", "name": "Backlog", "type": "backlog"},
                    "team": {"key": "PROD"}, "parent": None, "delegate": None,
                    "labels": {"nodes": [{"name": "provenance:agent"}]},
                    "children": {"nodes": [kid(51, []), kid(52, [51])],
                                 "pageInfo": {"hasNextPage": False}}}},
            }
            return {"data": answers[op]}
        real_http = globals()["_http_json"]
        globals()["_http_json"] = by_operation
        try:
            tc = TrackerClient("k")
            check("wiring-live-work-clean", tc.live_work("u"), [])
            idea_labels[0] = [{"name": "provenance:epic"}]
            check("wiring-live-work-by-the-planners-rules",
                  any("provenance:epic" in r for r in tc.live_work("u")), True)
            check("wiring-plan-it-state", tc.team_state("PROD", "Plan it"),
                  ("st-plan-it", None))
            check("wiring-a-started-state-is-refused",
                  tc.team_state("PROD", "Doing")[0], None)
            check("wiring-agent-user", tc.agent_user("Agent"), ("agent-1", None))
            agents[0] = [{"id": "agent-1", "active": True}, {"id": "agent-2", "active": True}]
            check("wiring-two-agent-users-refused", tc.agent_user("Agent")[0], None)
            agents[0] = [{"id": "agent-1", "active": False}]
            check("wiring-an-inactive-agent-user-refused", tc.agent_user("Agent")[0], None)
            ep = tc.epic("PROD-50")
            check("wiring-epic-children-and-blockers",
                  ([k["identifier"] for k in ep["children"]["nodes"]],
                   [b["identifier"] for b, _s in blockers_of(ep["children"]["nodes"][1])]),
                  (["PROD-51", "PROD-52"], ["PROD-51"]))
            tc.delegate("u", "agent-1")
            check("wiring-the-delegation-carries-delegateId-only",
                  [v for op, v in sent if op == "BridgeDelegate"],
                  [{"id": "u", "delegateId": "agent-1"}])
            # One client a pass: the session listing is paged once, however many tickets
            # are checked, by live_work or sessions_on (KIT-262).
            once = TrackerClient("k")
            before = [op for op, _v in sent].count("PlanSessions")
            check("wiring-sessions-read-once-per-pass",
                  (once.sessions_on("u"), once.sessions_on("v"),
                   [op for op, _v in sent].count("PlanSessions") - before), ([], [], 1))
            once = TrackerClient("k")
            before = [op for op, _v in sent].count("PlanSessions")
            once.live_work("u")
            once.live_work("v")
            once.sessions_on("w")
            check("wiring-live-work-reads-the-sessions-once-per-pass",
                  [op for op, _v in sent].count("PlanSessions") - before, 1)
            # KIT-284: a session list the tracker did not give is never "no session". A
            # read that failed is not kept, even in part: each later ask this pass fails
            # too, and reads nothing more. An answer with no whole page in it (its list and
            # where it ends) is a failed read, and a list past the pages one pass reads is
            # said not to be whole.
            def asks(client, *ids):
                out = []
                for i in ids:
                    try:
                        out.append(client.sessions_on(i))
                    except BridgeError:
                        out.append("could not read")
                return out

            def sessions_answer(answer):
                def transport(url, headers, payload=None, timeout=20):
                    sent.append(("PlanSessions", payload["variables"]))
                    if answer is None:
                        raise urllib.error.URLError("simulated")
                    return {"data": answer}
                return transport

            def read_whole(client):
                client.sessions_on("u")
                return getattr(client, "sessions_read_whole", lambda: "no such ask")()
            globals()["_http_json"] = sessions_answer(None)
            before = len(sent)
            check("wiring-a-session-list-that-failed-fails-every-ask-this-pass-and-is-read-once",
                  (asks(TrackerClient("k"), "u", "v"), len(sent) - before),
                  (["could not read", "could not read"], 1))
            def no_list(answer):
                globals()["_http_json"] = sessions_answer(answer)
                return asks(TrackerClient("k"), "u")
            check("wiring-an-answer-holding-no-session-list-is-a-failed-read",
                  [no_list(answer) for answer in (
                      {}, {"agentSessions": {}},
                      {"agentSessions": {"nodes": None, "pageInfo": {"hasNextPage": False}}},
                      {"agentSessions": {"nodes": [], "pageInfo": None}})],
                  [["could not read"]] * 4)
            globals()["_http_json"] = sessions_answer({"agentSessions": {
                "nodes": [], "pageInfo": {"hasNextPage": False}}})
            whole = read_whole(TrackerClient("k"))
            globals()["_http_json"] = sessions_answer({"agentSessions": {
                "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "c"}}})
            check("wiring-a-session-list-past-the-pages-one-pass-reads-is-not-whole",
                  (whole, read_whole(TrackerClient("k"))), (True, False))
            globals()["_http_json"] = by_operation
            tc.move("u", "st-plan-it")
            tc.comment("u", "who said yes")
            check("wiring-the-move-carries-stateId-only",
                  [v for op, v in sent if op == "BridgeMove"], [{"id": "u",
                                                                 "stateId": "st-plan-it"}])
        finally:
            globals()["_http_json"] = real_http


def cases_real(kit):
    """The real tracker client: a refusal is never not found: its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    import io
    from pipeline_bridge import ALICE, EXIT_ERROR, OWNER
    with kit as tmp:
        check, posted, run, world = kit.take("check", "posted", "run", "world")
        # The REAL tracker client, with only the transport stubbed: a refusal from the tracker
        # is never read as "not found". Slack's client is tested beside it, in
        # pipeline_bridge_transport.py (KIT-388).
        def through(answer, call):
            def stub(_url, _headers, payload=None, timeout=20):
                return answer(payload)
            real_http = globals()["_http_json"]
            globals()["_http_json"] = stub
            try:
                return call()
            except BridgeError as exc:
                return "BridgeError: %s" % exc
            finally:
                globals()["_http_json"] = real_http

        def http_error(code, body):
            def answer(_payload):
                raise urllib.error.HTTPError(TRACKER_API, code, "x", {}, io.BytesIO(body))
            return answer

        got = through(lambda _p: {"errors": [{"message": "Authentication required"}]},
                      lambda: TrackerClient("k").issue("PROD-5"))
        check("real-tracker:an-auth-error-is-an-error-not-not-found",
              str(got).startswith("BridgeError"), True)
        got = through(lambda _p: {}, lambda: TrackerClient("k").issue("PROD-5"))
        check("real-tracker:an-answer-with-no-data-is-an-error-not-not-found",
              str(got).startswith("BridgeError"), True)
        for code, raw in ((503, b""), (502, b"{}"), (429, b'{"message": "rate limited"}'),
                          (500, b'{"data": {"issue": null, "viewer": null}}')):
            for what, call in (("issue", lambda: TrackerClient("k").issue("PROD-5")),
                               ("viewer", lambda: TrackerClient("k").viewer_id())):
                got = through(http_error(code, raw), call)
                check("real-tracker:http-%d-%s-is-could-not-check" % (code, what),
                      str(got).startswith("BridgeError"), True)

        # KIT-341: an issue's history is read whole, page by page. A broken page, or one past
        # the bound, is an error: a part read is never taken for the whole.
        def history_page(nodes, more, cursor=None):
            return {"data": {"issue": {"history": {
                "nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": cursor}}}}}
        pages = {None: history_page([{"actorId": "a"}], True, "c1"),
                 "c1": history_page([{"actorId": "b"}], False)}
        got = through(lambda p: pages[p["variables"]["after"]],
                      lambda: TrackerClient("k").trust_history("uuid-5"))
        check("real-tracker:a-history-is-read-whole-page-by-page",
              [n["actorId"] for n in got] if isinstance(got, list) else got, ["a", "b"])
        got = through(lambda _p: {"data": {"issue": {"history": None}}},
                      lambda: TrackerClient("k").trust_history("uuid-5"))
        check("real-tracker:a-broken-history-page-is-an-error",
              str(got).startswith("BridgeError"), True)
        got = through(lambda _p: history_page([], True, "c"),
                      lambda: TrackerClient("k").trust_history("uuid-5"))
        check("real-tracker:a-history-past-its-bound-is-an-error",
              str(got).startswith("BridgeError: the history of uuid-5 runs past"), True)

        def viewer_ok_issue_503(payload):
            if "BridgeViewer" in payload["query"]:
                return {"data": {"viewer": {"id": OWNER}}}
            return http_error(503, b"")(payload)
        c, s, _t = world("tracker-outage")
        through(viewer_ok_issue_503, lambda: run(c, s, TrackerClient("k")))
        s.add(ALICE, "plan PROD-5")
        code, _o = through(viewer_ok_issue_503, lambda: run(c, s, TrackerClient("k")))
        check("a-tracker-outage-exits-1-and-is-not-said-as-not-found",
              (code, any("can't find" in x for x in posted(s))), (EXIT_ERROR, False))


def cases_searches(kit):
    """The real client's searches for a re-plan: its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    from pipeline_bridge import OWNER, run_text
    with kit as tmp:
        check, outcome = kit.take("check", "outcome")
        # The real client's searches: one team, the planner's or the executor's own line,
        # archived tickets read, a page that runs over refused; and the backlog state is the
        # team's first by position.
        def case_rp_wiring():
            seen = []
            run6, run60 = run_text("PROD-6", "T1"), run_text("PROD-60", "T7")

            def transport(url, headers, payload=None, timeout=20):
                q, v = payload["query"], payload["variables"]
                seen.append((q.split("(")[0].split()[-1], v))
                if "BridgeFindByLine" in q:
                    lines = [w["description"]["contains"] for w in
                             v["filter"].get("or") or [v["filter"]]]
                    if any("PROD-99" in line for line in lines):
                        # Always another page: past FIND_MAX_PAGES the read is refused.
                        return {"data": {"issues": {"nodes": [], "pageInfo": {
                            "hasNextPage": True, "endCursor": "c%s" % (v.get("after") or "")}}}}
                    pages = [
                        [{"id": "r2", "identifier": "PROD-60", "state": {"type": "completed"},
                          "creator": {"id": OWNER}, "description": run60},
                         {"id": "r3", "identifier": "PROD-61", "state": {"type": "unstarted"},
                          "creator": {"id": "someone-else"}, "description": run6}],
                        [{"id": "r1", "identifier": "PROD-50", "state": {"type": "completed"},
                          "creator": {"id": OWNER}, "description": run6},
                         {"id": "e1", "identifier": "PROD-70", "state": {"type": "unstarted"},
                          "creator": {"id": OWNER}, "description": "x\n\nPlan id: PROD-50/abc"},
                         {"id": "e2", "identifier": "PROD-71", "state": {"type": "unstarted"},
                          "creator": {"id": "someone-else"},
                          "description": "x\n\nPlan id: PROD-50/def"}]]
                    page = 1 if v.get("after") == "p2" else 0
                    return {"data": {"issues": {"nodes": [n for n in pages[page] if any(
                        line in n["description"] for line in lines)], "pageInfo": {
                        "hasNextPage": page == 0, "endCursor": "p2" if page == 0 else None}}}}
                if "PlanTeam" in q:
                    return {"data": {"teams": {"nodes": [{"id": "team-prod", "key": "PROD",
                                                          "states": {"nodes": [
                        {"id": "st-icebox", "name": "Icebox", "type": "backlog", "position": 2},
                        {"id": "st-backlog", "name": "Backlog", "type": "backlog",
                         "position": 1},
                        {"id": "st-plan-it", "name": "Plan it", "type": "unstarted",
                         "position": 3}]}, "labels": {"nodes": []}}]}}}
                if "PlanHistory" in q:
                    return {"data": {"issue": {"createdAt": "2025-10-01T00:00:00.000Z",
                                               "creator": {"id": OWNER}, "history": {
                        "nodes": [{"id": "h1", "createdAt": "2025-10-02T00:00:00.000Z",
                                   "actorId": OWNER, "fromStateId": "st-backlog",
                                   "toStateId": "st-plan-it"},
                                  {"id": "h2", "createdAt": "2025-10-03T00:00:00.000Z",
                                   "actorId": OWNER, "fromStateId": "st-backlog",
                                   "toStateId": "st-plan-it"}],
                        "pageInfo": {"hasNextPage": False}}}}}
                raise AssertionError("unexpected query: %s" % q[:60])
            real_http = globals()["_http_json"]
            globals()["_http_json"] = transport
            try:
                tc = TrackerClient("k")
                runs = [r["identifier"] for r in tc.planning_runs("PROD", "PROD-6", OWNER)]
                plans = [p["identifier"] for p in tc.filed_plans("PROD", ["PROD-50"], OWNER)]
                none = tc.filed_plans("PROD", [], OWNER)
                before_over = len(seen)
                try:
                    tc.filed_plans("PROD", ["PROD-50", "PROD-99"], OWNER)
                    over = "read as complete"
                except BridgeError as exc:
                    over = "more tickets" in str(exc)
                over_reads = len(seen) - before_over
                backlog = tc.backlog_state("PROD")
                trig = (tc.plan_trigger("uuid-6", "st-plan-it") or {}).get("id")
            finally:
                globals()["_http_json"] = real_http
            finds = [v for op, v in seen if op == "BridgeFindByLine"]
            return (runs, plans, none, over, over_reads, backlog, trig, finds[0]["filter"],
                    [f.get("after") for f in finds[:2]], sorted(finds[2]["filter"]))
        check("replan-wiring: the searches page, keep only the owner's own runs and plans, and "
              "read the backlog state and the history the planner's way",
              outcome(case_rp_wiring),
              (["PROD-50"], ["PROD-70"], [], True, FIND_MAX_PAGES,
               ("st-backlog", "Backlog", None), "h2",
               {"team": {"key": {"eq": "PROD"}}, "description": {"contains": "Idea: PROD-6"}},
               [None, "p2"], ["description", "team"]))


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__),
                                                 [cases_errors, cases_real, cases_searches])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_tracker
        sys.exit(pipeline_bridge_tracker.selftest())
    print("usage: pipeline_bridge_tracker.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
