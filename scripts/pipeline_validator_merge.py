#!/usr/bin/env python3
"""The validator's merge path: GitHub's own auto-merge, turned on for one pull request at a time, each
on the latest main (KIT-539). Design: docs/adr/2026-11-05-the-validator-approves-inside-limits-that-
code-enforces.md, "Merging one at a time, on the latest main".

    python3 scripts/pipeline_validator_merge.py run --config ~/.validator/config.json [--dry-run]
    python3 scripts/pipeline_validator_merge.py --example-config
    python3 scripts/pipeline_validator_merge.py --selftest

WHO RUNS IT.  The validator's own job, under the owner's own login, as a per-user scheduled job (the
install is its own piece). It is refused in an agent's environment, before it reads anything. It is
the one file that starts the validator's agent, through pipeline_validator_run.py, whose CALLERS
name it. It reads through `gh`, read-only, as the facts and the evidence do, and it writes only as
the validator's own GitHub app: a merge GitHub makes for that app is recorded as the app's, so the
watcher checks it against the ledger. One turned on under the owner's login would be his.

ONE PASS, in order. Each step says what it did, or which nothing.
  1. The switches (pipeline_validator_settings.load): settings not read, the merges switch off, or no
     repository listed in `merge_repos`: nothing more, said (OFF).
  2. The watcher's hold: a hold, or a hold file not named or not readable: nothing more (HELD).
  3. The ledger, by its own reader. One that cannot be read or trusted: nothing more.
  4. Every open pull request of each listed repository. A list not read whole: nothing more, since
     a pull request with auto-merge on could be in the part not read.
  5. ONE IN FLIGHT. A pull request with auto-merge on is in flight:
       - at a head with an `approved` row, it waits for GitHub to merge it, and one behind its base
         gets GitHub's own update-branch at that head;
       - at a head with no `approved` row (a push after it was turned on), it is turned off when the
         validator's app turned it on, and left as it is, said, when someone else did.
     While one is in flight, nothing else is turned on.
  6. The candidates, oldest first: open, not a draft, into the default branch.
       - A head that has its verdict is not judged again (contract §24). An `approved` one whose
         auto-merge is not on gets its check and its auto-merge now: a pass that stopped between.
       - Its facts (pipeline_validator_facts), the hard limits, the evidence, and the route's
         decision (pipeline_validator_route.decide), which says what is recorded.
       - A hold no later pass can settle (the safety-system floor, a human-only class the owner has
         not allowed, a review declined, ...): a `held` row, its `Validator` check, and the next.
       - Behind its base, and nothing else: GitHub's own update-branch at that head, and the pass
         ends. A later pass judges the new head from scratch, its checks and its review included.
       - Any other wait (checks running or red, a review not in yet or stale, a fact not read): no
         row, said, and the next. A head has one verdict, never spent on a wait.
       - Both layers pass: the brief, ONE agent run, the route's decision, its row, its check, and
         for an approval GitHub's own auto-merge at exactly that head. The pass ends there: at most
         one agent run, and at most one pull request turned on, per pass.

THE STALE-REVIEW LIMIT (KIT-792).  Updating a pull request moves its head, so its Stage E review is
stale until Stage E reviews the new head, and Stage E re-reviews a moved head once per pull request.
A pull request updated a second time, because main moved again before it merged, has no current
review: it waits and is never approved on a stale review, and each pass says so.

A MERGE QUEUE is optional. This job relies on no paid feature: it keeps one pull request in flight
and updates the next itself. Where GitHub's merge queue is free, a repository may use it instead,
once its required workflows also run on the queue's `merge_group` event: a workflow change only a
person can land (docs/VALIDATOR-OPERATOR.md, "The merge path").

WHAT IT NEVER DOES (asserted in --selftest): approve a review, merge, label, or change a setting.
Its writes are the ledger's one writer, the `Validator` check (pipeline_validator_check), GitHub's
own update-branch, and GitHub's own auto-merge on or off, through the shared helper's pinned calls
(scripts/pipeline_github_app.py).

EXIT CODES (contract §13):
  0  the pass ran: it turned something on or off, judged, waited, or had nothing to do, and said
     which. Off and held are said too.
  1  a write failed: a ledger row, a check, or a call GitHub refused. Said.
  2  the config is not valid, or this is an agent's environment.
  3  something could not be read: the hold, the ledger, or a list of open pull requests.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_github_app as gha
    import pipeline_validator_brief as brief
    import pipeline_validator_check as vcheck
    import pipeline_validator_evidence as evidence
    import pipeline_validator_facts as facts
    import pipeline_validator_ledger as ledger
    import pipeline_validator_limits as limits
    import pipeline_validator_route as route
    import pipeline_validator_run as vrun
    import pipeline_validator_settings as vsettings
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The merge path uses those files' own code and keeps no copy." % exc)

CONFIG_SCHEMA = "pipeline-validator-config/1"
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3
TOP_KEYS = ("schema", "settings_file", "ledger_file", "merge", "facts", "agent", "check")
MERGE_DEFAULTS = {"review_bar": "medium", "merge_method": "SQUASH", "max_pages": 3}
ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
BEHIND = "merge-behind"
STALE = "review-stale"
STALE_WORDS = ("its review judged an earlier head. Stage E re-reviews a moved head once per pull "
               "request, so one updated a second time waits and is never approved on a stale "
               "review (KIT-792)")
EXAMPLE = {
    "schema": CONFIG_SCHEMA,
    "settings_file": "~/.validator/settings.json",
    "ledger_file": "~/.validator/ledger.json",
    "merge": dict(MERGE_DEFAULTS),
    "facts": dict(facts.DEFAULTS),
    "agent": {"agent_bin": "/usr/local/bin/claude"},
    "check": {"app_id": 1, "key_file_env": "VALIDATOR_APP_KEY_FILE"},
}


# --------------------------------------------------------------------------- #
# The config: every problem at once, by key, never by value
# --------------------------------------------------------------------------- #
def config(raw):
    """(cfg, problems) for the validator's config, as the merge path reads it: its own `merge`
    section, and the `facts`, `agent` and `check` sections by their owners' own rules."""
    if not isinstance(raw, dict):
        return None, ["the config is not a JSON object"]
    bad = ["%s: not a setting" % repr(k)[:40] for k in sorted(raw, key=repr) if k not in TOP_KEYS]
    if raw.get("schema") != CONFIG_SCHEMA:
        bad.append("schema: must be %s" % CONFIG_SCHEMA)
    cfg = {}
    for key in ("settings_file", "ledger_file"):
        v = raw.get(key)
        if not isinstance(v, str) or not v or "\0" in v or not os.path.isabs(os.path.expanduser(v)):
            bad.append("%s: must be an absolute path (~/ is allowed)" % key)
        else:
            cfg[key] = os.path.expanduser(v)
    merge = raw.get("merge", {})
    if not isinstance(merge, dict):
        bad.append("merge: must be an object")
        merge = {}
    bad += ["merge.%s: not a setting" % repr(k)[:40] for k in sorted(merge, key=repr)
            if k not in MERGE_DEFAULTS]
    m = dict(MERGE_DEFAULTS, **dict((k, v) for k, v in merge.items() if k in MERGE_DEFAULTS))
    if m["review_bar"] not in evidence.SEVERITY_RANK:
        bad.append("merge.review_bar: must be one of %s" % ", ".join(sorted(evidence.SEVERITY_RANK)))
    if m["merge_method"] not in gha.MERGE_METHODS:
        bad.append("merge.merge_method: must be one of %s" % ", ".join(gha.MERGE_METHODS))
    if isinstance(m["max_pages"], bool) or not isinstance(m["max_pages"], int) \
            or not 1 <= m["max_pages"] <= 10:
        bad.append("merge.max_pages: must be a whole number from 1 to 10")
    cfg["merge"] = m
    fcfg, fbad = facts.config(raw.get("facts", {}))
    bad += ["facts.%s" % b for b in fbad]
    cfg["facts"] = fcfg
    _s, abad = vrun.settings(raw.get("agent"))
    if abad:
        bad.append("agent.%s: not valid" % abad if abad != "the config" else
                   "agent: must be an object naming agent_bin")
    cfg["agent"] = raw.get("agent")
    chk = raw.get("check")
    if not isinstance(chk, dict):
        bad.append("check: must be an object naming app_id and key_file_env")
    else:
        if isinstance(chk.get("app_id"), bool) or not isinstance(chk.get("app_id"), int) \
                or chk["app_id"] < 1:
            bad.append("check.app_id: must be the validator app's id, a positive whole number")
        if not isinstance(chk.get("key_file_env"), str) or not ENV_NAME_RE.fullmatch(
                chk["key_file_env"]):
            bad.append("check.key_file_env: must be the NAME of the variable that holds the key "
                       "file's path, never the path or the key")
    cfg["check"] = chk
    return (None if bad else cfg), bad


def load_config(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None, ["the config at %s could not be read as JSON" % path]
    return config(raw)


# --------------------------------------------------------------------------- #
# The code host: reads through gh, writes as the validator's own app
# --------------------------------------------------------------------------- #
class Host(object):
    """The code host as the merge path meets it. Reads go through `gh`, read-only, as the facts and
    the evidence read. Writes go only as the validator's own app, through the shared helper's pinned
    calls: the check, GitHub's own update-branch, and GitHub's own auto-merge on or off."""

    def __init__(self, cfg, environ=None, gh=None, call=None, signer=None, runner=None):
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ
        self.gh = gh or facts._gh
        self.call = call or gha._call
        self.signer = signer or gha.openssl_signer
        self.runner = runner
        self._tokens, self._login = {}, None

    def open_pulls(self, repo):
        """(the open pull requests, oldest first, or None, complete): read page by page."""
        out = []
        for page in range(1, self.cfg["merge"]["max_pages"] + 1):
            try:
                rows = facts._api(self.gh, "repos/%s/pulls?state=open&sort=created&direction=asc"
                                           "&per_page=100&page=%d" % (repo, page), "the open list")
            except facts.Unread:
                return None, False
            if not isinstance(rows, list):
                return None, False
            out += rows
            if len(rows) < 100:
                return out, True
        return out, False

    def pull(self, repo, number):
        try:
            got = facts._api(self.gh, "repos/%s/pulls/%d" % (repo, number), "the pull request")
        except facts.Unread:
            return None
        return got if isinstance(got, dict) else None

    def facts(self, repo, number, now):
        return facts.for_merge(self.cfg["facts"], repo, number, now)

    def evidence(self, repo, number, head, settings, last_piece, acceptance):
        return evidence.evidence_for(repo, number, head, settings, self.cfg["merge"]["review_bar"],
                                     last_piece, acceptance, own_check=vcheck.CHECK_NAME,
                                     own_app=self.cfg["check"]["app_id"])

    def run_agent(self, built):
        return vrun.run_agent(self.cfg["agent"], built, environ=self.environ, runner=self.runner)

    def post_check(self, repo, number, head):
        return vcheck.post_verdict(repo, number, head, self.cfg["ledger_file"], self.cfg["check"],
                                   environ=self.environ, call=self.call, signer=self.signer)

    def _jwt(self):
        app_id, key_file, why = vcheck.credential(self.cfg["check"], self.environ)
        if why:
            raise gha.GitHubAppError(why)
        return gha.make_jwt(app_id, self.signer(key_file))

    def _token(self, repo):
        if repo not in self._tokens:
            jwt = self._jwt()
            installation = gha.installation_for(repo, jwt, self.call)
            if installation is None:
                raise gha.GitHubAppError("the validator's app is not installed on %s" % repo)
            self._tokens[repo] = gha.installation_token(installation, jwt, self.call)
        return self._tokens[repo]

    def app_login(self):
        if self._login is None:
            self._login = gha.bot_login(gha.app_info(self._jwt(), self.call)["slug"])
        return self._login

    def enable(self, repo, node_id, head):
        return gha.auto_merge(node_id, True, self._token(repo), head,
                              self.cfg["merge"]["merge_method"], self.call)

    def disable(self, repo, node_id):
        return gha.auto_merge(node_id, False, self._token(repo), call=self.call)

    def update(self, repo, number, head):
        return gha.update_branch(repo, number, head, self._token(repo), self.call)


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def _open(raw, repo):
    """One open pull request from GitHub's list, as fields, or None for one of another shape."""
    if not isinstance(raw, dict):
        return None
    head, base = raw.get("head") or {}, raw.get("base") or {}
    sha = head.get("sha") if isinstance(head, dict) else None
    number = raw.get("number")
    if (isinstance(number, bool) or not isinstance(number, int) or number < 1
            or not isinstance(sha, str) or not SHA_RE.fullmatch(sha)):
        return None
    auto = raw.get("auto_merge")
    by = ((auto.get("enabled_by") or {}).get("login") if isinstance(auto, dict)
          and isinstance(auto.get("enabled_by"), dict) else None)
    default = ((base.get("repo") or {}).get("default_branch")
               if isinstance(base, dict) and isinstance(base.get("repo"), dict) else None)
    return {"repo": repo, "number": number, "ref": "%s#%d" % (repo, number), "head": sha,
            "node_id": raw.get("node_id"), "draft": raw.get("draft") is not False,
            "created_at": str(raw.get("created_at") or ""),
            "into_default": isinstance(base, dict) and default is not None
            and base.get("ref") == default,
            "auto": auto is not None, "auto_by": by}


def _row_at(rows, ref, head):
    """The ledger's row for `ref` at `head`, or None: a head has one verdict."""
    for row in rows:
        if row["subject"] == "merge" and row["ref"].lower() == ref.lower() and row["head"] == head:
            return row
    return None


def _words(reasons):
    words = []
    for w in reasons:
        words.append(STALE_WORDS if w == STALE else w)
    return "; ".join(words)


class Pass(object):
    """One pass of the merge path, for the parts that write: the ledger's one writer, the check, and
    GitHub's own calls through `host`. `dry` writes nothing and says what it would do."""

    def __init__(self, cfg, host, settings, now, dry, say):
        self.cfg, self.host, self.settings, self.now, self.dry = cfg, host, settings, now, dry
        self.say, self.failed = say, False

    def record(self, pr, decision):
        """Write the decision's row and post its check. True when both happened."""
        row = route.row_for("merge", pr["ref"], pr["head"], decision, self.settings)
        if row is None:
            return False
        if self.dry:
            self.say("  [dry-run] would record %s for %s at %s" % (row["kind"], pr["ref"],
                                                                   pr["head"][:12]))
            return False
        try:
            ledger.append(self.cfg["ledger_file"], row, time.time)
        except (ledger.LedgerError, OSError) as exc:
            self.failed = True
            self.say("  %s: its %s could not be recorded (%s), so nothing follows from it"
                     % (pr["ref"], row["kind"], exc))
            return False
        return self.post(pr)

    def post(self, pr):
        got = self.host.post_check(pr["repo"], pr["number"], pr["head"])
        if not isinstance(got, dict) or got.get("result") != vcheck.POSTED:
            self.failed = True
            self.say("  %s: its Validator check was not posted (%s)"
                     % (pr["ref"], (got or {}).get("why") if isinstance(got, dict) else "no answer"))
            return False
        return True

    def github(self, what, pr, fn):
        """One write to GitHub, as the validator's app. True when GitHub did it."""
        if self.dry:
            self.say("  [dry-run] would %s on %s" % (what, pr["ref"]))
            return False
        try:
            fn()
        except gha.GitHubAppError as exc:
            self.failed = True
            self.say("  %s: could not %s: %s" % (pr["ref"], what, exc))
            return False
        self.say("  %s: %s" % (pr["ref"], what))
        return True


def run_pass(cfg, host, now=None, dry=False, say=print):
    """One pass. Returns its exit code (contract §13), having said what it did or which nothing."""
    now = time.time() if now is None else now
    settings = vsettings.load(cfg["settings_file"])
    if settings["state"] != vsettings.READ:
        say("OFF: %s." % settings["why"])
        return EXIT_OK
    if settings["approve"]["merges"] is not True:
        say("OFF: the merges switch is off in the validator's settings.")
        return EXIT_OK
    repos = [r for r in settings[vsettings.REPOS_KEY] if vsettings.merge_allowed(settings, r)]
    if not repos:
        say("OFF: no repository is listed for merges (merge_repos is empty).")
        return EXIT_OK
    hold = limits.hold_state(cfg["facts"]["hold_file"])
    if hold != "none":
        say("HELD: %s, so nothing is judged or turned on." % {
            "held": "the watcher's hold stands",
            None: "no hold file is named (facts.hold_file), and nothing can say there is no hold"
        }.get(hold, "the watcher's hold file could not be read (%s)" % hold))
        return EXIT_OK if hold == "held" else EXIT_PARTIAL
    got = ledger.read(cfg["ledger_file"])
    if got["rows"] is None:
        say("COULD NOT JUDGE: %s, so nothing is approved or turned on." % got["why"])
        return EXIT_PARTIAL
    rows = got["rows"]
    pulls = []
    for repo in repos:
        listed, complete = host.open_pulls(repo)
        if listed is None or not complete:
            say("COULD NOT JUDGE: the open pull requests of %s could not be read whole, so one with "
                "auto-merge on could be missed; nothing is turned on." % repo)
            return EXIT_PARTIAL
        pulls += [p for p in (_open(raw, repo) for raw in listed) if p is not None]
    p = Pass(cfg, host, settings, now, dry, say)
    say("Pass: %d open pull request(s) in %s." % (len(pulls), ", ".join(repos)))

    # 5. One in flight.
    in_flight = False
    for pr in [x for x in pulls if x["auto"]]:
        row = _row_at(rows, pr["ref"], pr["head"])
        if row is not None and row["kind"] == "approved":
            in_flight = True
            full = host.pull(pr["repo"], pr["number"])
            if isinstance(full, dict) and full.get("mergeable_state") == "behind":
                say("In flight: %s is behind its base." % pr["ref"])
                p.github("ask GitHub to update its branch at %s" % pr["head"][:12], pr,
                         lambda pr=pr: host.update(pr["repo"], pr["number"], pr["head"]))
            else:
                say("In flight: %s, approved at %s, waits for GitHub to merge it."
                    % (pr["ref"], pr["head"][:12]))
            continue
        try:
            ours = (pr["auto_by"] is not None
                    and pr["auto_by"].lower() == host.app_login().lower())
        except gha.GitHubAppError as exc:
            say("In flight: auto-merge is on for %s, and the validator's app could not say who it "
                "is (%s), so it is left on." % (pr["ref"], exc))
            p.failed, in_flight = True, True
            continue
        if ours:
            say("Auto-merge on %s has no approval at its head %s (a push after it was turned on)."
                % (pr["ref"], pr["head"][:12]))
            if not p.github("turn its auto-merge off", pr,
                            lambda pr=pr: host.disable(pr["repo"], pr["node_id"])):
                in_flight = True
        else:
            in_flight = True
            say("In flight: auto-merge on %s was turned on by %s, not the validator. It is left "
                "as it is, and nothing else is turned on while it is on."
                % (pr["ref"], pr["auto_by"] or "someone GitHub did not name"))
    if in_flight:
        say("One pull request is in flight, so no other is turned on.")
        return EXIT_FAILED if p.failed else EXIT_OK

    # 6. The candidates, oldest first.
    candidates = sorted((x for x in pulls if not x["auto"]),
                        key=lambda x: (x["created_at"], x["repo"], x["number"]))
    for pr in candidates:
        if pr["draft"] or not pr["into_default"]:
            say("  %s: skipped, %s." % (pr["ref"], "a draft" if pr["draft"] else
                                        "not into the default branch"))
            continue
        row = _row_at(rows, pr["ref"], pr["head"])
        if row is not None:
            if row["kind"] != "approved":
                say("  %s: its head %s has its verdict (%s)." % (pr["ref"], pr["head"][:12],
                                                                  row["kind"]))
                continue
            say("  %s: approved at %s, and its auto-merge is not on yet." % (pr["ref"],
                                                                          pr["head"][:12]))
            if p.dry or p.post(pr):
                p.github("turn GitHub's own auto-merge on at %s" % pr["head"][:12], pr,
                         lambda pr=pr: host.enable(pr["repo"], pr["node_id"], pr["head"]))
            return EXIT_FAILED if p.failed else EXIT_OK
        doc = host.facts(pr["repo"], pr["number"], now)
        if (doc.get("item") or {}).get("head") != pr["head"]:
            say("  %s: waits: its head moved while it was read, or could not be read." % pr["ref"])
            continue
        l1 = limits.limits(doc["item"], settings, doc["limits"])
        ev = doc.get("evidence") or {}
        l2 = host.evidence(pr["repo"], pr["number"], pr["head"], settings, ev.get("last_piece"),
                           ev.get("acceptance"))
        bounces = doc["limits"].get("bounces")
        decision = route.decide("merge", l1, l2, None, bounces)
        if decision["record"]:
            say("  %s: held for you: %s." % (pr["ref"], "; ".join(
                route.reason_words(decision["reasons"]))))
            p.record(pr, decision)
            continue
        if decision["wait"] == [BEHIND]:
            say("  %s: passes but for being behind its base." % pr["ref"])
            p.github("ask GitHub to update its branch at %s" % pr["head"][:12], pr,
                     lambda pr=pr: host.update(pr["repo"], pr["number"], pr["head"]))
            say("The pass ends: the new head is judged from scratch on a later pass.")
            return EXIT_FAILED if p.failed else EXIT_OK
        if decision["wait"] != ["agent-not-run"]:
            say("  %s: waits: %s." % (pr["ref"], _words(decision["wait"])))
            continue
        try:
            built = brief.build_brief(doc["brief"].get("criteria"), l2, doc["brief"].get("diff"))
        except brief.BriefError as exc:
            say("  %s: waits: the agent's brief could not be built (%s)." % (pr["ref"], exc))
            continue
        if p.dry:
            say("  [dry-run] %s passes both layers: would run the agent on it." % pr["ref"])
            return EXIT_OK
        answer = route.answer_of(host.run_agent(built), built)
        decision = route.decide("merge", l1, l2, answer, bounces)
        if not decision["record"]:
            say("  %s: waits: %s. The pass ends: one agent run per pass."
                % (pr["ref"], _words(decision["wait"])))
            return EXIT_OK
        say("  %s: the validator %s it." % (pr["ref"], {
            "approved": "approved", "sent-back": "sent back", "escalated": "escalated",
            "escalated-urgent": "urgently escalated", "held": "held"}[decision["kind"]]))
        if p.record(pr, decision) and decision["kind"] == "approved":
            p.github("turn GitHub's own auto-merge on at %s" % pr["head"][:12], pr,
                     lambda pr=pr: host.enable(pr["repo"], pr["node_id"], pr["head"]))
        say("The pass ends: one agent run per pass.")
        return EXIT_FAILED if p.failed else EXIT_OK
    say("Nothing to turn on: no pull request passes yet." if candidates else
        "Nothing to judge: no open pull request.")
    return EXIT_FAILED if p.failed else EXIT_OK


def main(argv=None, environ=None):
    ap = argparse.ArgumentParser(description="The validator's merge path: GitHub's own auto-merge, "
                                             "one pull request at a time.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--example-config", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="one pass")
    r.add_argument("--config", default="~/.validator/config.json")
    r.add_argument("--dry-run", action="store_true",
                   help="read and judge, run no agent, and write nothing")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "validator-merge-selftest-")
    if args.example_config:
        print(json.dumps(EXAMPLE, indent=2, sort_keys=True))
        return EXIT_OK
    if args.cmd != "run":
        ap.print_help()
        return EXIT_USAGE
    environ = os.environ if environ is None else environ
    markers = vrun.agent_markers(environ)
    if markers:
        print("REFUSED: this is an agent's environment (%s set). Only the validator's own job "
              "runs the merge path." % ", ".join(markers))
        return EXIT_USAGE
    cfg, bad = load_config(args.config)
    if bad:
        print("COULD NOT RUN: %s" % "; ".join(bad))
        return EXIT_USAGE
    return run_pass(cfg, Host(cfg, environ), dry=args.dry_run)


# ======================================================================================
# SELFTEST: nothing below the line above is run by a caller, and the source scan stops there.
# ======================================================================================
class _FakeHost(object):
    """The code host, stood in for: open pull requests with heads, checks, reviews and auto-merge, and
    a log of every write. The hard limits, the route, the ledger and the agent's run are the real
    ones; the agent's CLI is the run's own Stub."""
    APP = "acme-validator[bot]"

    def __init__(self, ledger_path, agent_cfg, environ):
        self.prs, self.log, self.sha = {}, [], 0
        self.ledger_path, self.agent_cfg, self.environ = ledger_path, agent_cfg, environ
        self.stub = vrun.Stub()
        self.complete, self.refuse = True, set()

    def new_head(self):
        self.sha += 1
        return "%040x" % self.sha

    def add(self, number, created, paths=("src/app.py",), by=None, draft=False):
        head = self.new_head()
        self.prs["acme/app#%d" % number] = {
            "number": number, "head": head, "created": created, "paths": list(paths),
            "checks": "green", "review": head, "behind": False, "draft": draft,
            "auto": by, "open": True}
        return head

    def merge(self, ref):
        """GitHub merged it: it closes, and every other open one is behind its base now."""
        self.prs[ref]["open"] = False
        for other in self.prs.values():
            if other["open"]:
                other["behind"] = True

    def open_pulls(self, repo):
        out = [{"number": p["number"], "node_id": "PR_%d" % p["number"], "draft": p["draft"],
                "created_at": p["created"], "head": {"sha": p["head"]},
                "base": {"ref": "main", "repo": {"default_branch": "main"}},
                "auto_merge": {"enabled_by": {"login": p["auto"]}} if p["auto"] else None}
               for p in self.prs.values() if p["open"]]
        return out, self.complete

    def pull(self, repo, number):
        return {"mergeable_state": "behind" if self.prs["%s#%d" % (repo, number)]["behind"]
                else "clean"}

    def facts(self, repo, number, now):
        p = self.prs["%s#%d" % (repo, number)]
        return {"item": {"kind": "merge", "ref": "%s#%d" % (repo, number), "head": p["head"]},
                "limits": {"origin": "owner", "paths": p["paths"], "risk_paths": [],
                           "pr": {"head": p["head"], "additions": 10, "deletions": 2},
                           "window": True, "queue": {"stops": [], "held": None},
                           "bounces": {"spent": 0, "budget": 3, "exhausted": False},
                           "watcher": "fresh", "hold": "none"},
                "evidence": {"last_piece": False, "acceptance": None},
                "brief": {"criteria": "## Acceptance criteria\n\n- [ ] It works.\n",
                          "diff": "+print('x')\n"}, "unread": {}}

    def evidence(self, repo, number, head, settings, last_piece, acceptance):
        p, reasons = self.prs["%s#%d" % (repo, number)], []
        if p["head"] != head:
            reasons.append("head-moved")
        reasons += {"pending": ["check-pending"], "red": ["check-failed"]}.get(p["checks"], [])
        if p["review"] is None:
            reasons.append("review-missing")
        elif p["review"] != head:
            reasons.append(STALE)
        if p["behind"]:
            reasons.append(BEHIND)
        return {"verdict": "no" if reasons else "yes", "head": head, "reasons": reasons}

    def run_agent(self, built):
        self.log.append(("agent",))
        return vrun.run_agent(self.agent_cfg, built, environ=self.environ, runner=self.stub)

    def post_check(self, repo, number, head):
        row = _row_at(ledger.read(self.ledger_path)["rows"] or [], "%s#%d" % (repo, number), head)
        self.log.append(("check", "%s#%d" % (repo, number), row and row["kind"]))
        return {"result": vcheck.POSTED if row else vcheck.NOTHING, "why": "x"}

    def app_login(self):
        return self.APP

    def _write(self, what, ref):
        if what in self.refuse:
            raise gha.GitHubAppError("GitHub would not %s (HTTP 403)" % what)
        self.log.append((what, ref))

    def enable(self, repo, node_id, head):
        ref = "%s#%s" % (repo, node_id.split("_")[1])
        self._write("enable", ref)
        self.prs[ref]["auto"], self.prs[ref]["auto_head"] = self.APP, head

    def disable(self, repo, node_id):
        ref = "%s#%s" % (repo, node_id.split("_")[1])
        self._write("disable", ref)
        self.prs[ref]["auto"] = None

    def update(self, repo, number, head):
        ref = "%s#%d" % (repo, number)
        assert self.prs[ref]["head"] == head, "update-branch at a head that moved"
        self._write("update", ref)
        p = self.prs[ref]
        p.update(head=self.new_head(), behind=False, checks="pending")


def selftest():
    import contextlib
    import io
    import tempfile
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:400], str(want)[:400]))

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        settings_path = os.path.join(tmp, "validator", "settings.json")
        ledger_path = os.path.join(tmp, "validator", "ledger.json")
        hold_path = os.path.join(tmp, "watcher", "hold.json")
        os.makedirs(os.path.dirname(settings_path), mode=0o755)
        os.makedirs(os.path.dirname(hold_path), mode=0o755)
        on = {"schema": vsettings.SETTINGS_SCHEMA,
              "approve": {"plans": False, "pieces": False, "merges": True},
              "limits": {"max_changed_lines": 400, "max_files": 20},
              "allow": {"credentials": False, "billing_and_accounts": False,
                        "production_deploy": False, "deleting_data": False},
              "stage_e_login": "acme-stage-e[bot]", "merge_repos": ["acme/app"]}

        def put_settings(doc):
            with open(settings_path, "w", encoding="utf-8") as fh:
                fh.write(doc if isinstance(doc, str) else json.dumps(doc))
            os.chmod(settings_path, 0o600)

        raw = dict(EXAMPLE, settings_file=settings_path, ledger_file=ledger_path,
                   facts=dict(facts.DEFAULTS, hold_file=hold_path))
        cfg, bad = config(raw)
        check("a config with real paths is valid", bad, [])
        token = vrun.voice.SETUP_TOKEN_PREFIX + "01-" + "t" * 40
        environ = {vrun.DEFAULTS["agent_token_env"]: token}

        def fresh():
            for path in (ledger_path, ledger_path + ".lock"):
                if os.path.exists(path):
                    os.unlink(path)
            return _FakeHost(ledger_path, cfg["agent"], environ)

        def go(host, dry=False, c=None):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run_pass(c or cfg, host, now=1_800_000_000, dry=dry,
                                say=lambda line: print(line))
            return code, out.getvalue()

        def writes(host):
            return [w for w in host.log if w[0] in ("enable", "disable", "update")]

        def kinds():
            got = ledger.read(ledger_path)["rows"] or []
            return [(r["ref"], r["kind"]) for r in got]

        # 1. Off, or settings it cannot read: nothing at all, said in words.
        for label, doc, needle in (
                ("no settings file", None, "OFF: no settings file"),
                ("settings that are not JSON", "{oops", "OFF: the settings file is not readable JSON"),
                ("the merges switch off", dict(on, approve=dict(on["approve"], merges=False)),
                 "OFF: the merges switch is off"),
                ("no repository listed", dict(on, merge_repos=[]), "OFF: no repository is listed")):
            if doc is None:
                if os.path.exists(settings_path):
                    os.unlink(settings_path)
            else:
                put_settings(doc)
            host = fresh()
            host.add(1, "2026-11-01T00:00:00Z")
            code, said = go(host)
            check("off: %s does nothing and says so" % label,
                  (code, needle in said, host.log, kinds()), (EXIT_OK, True, [], []))
        put_settings(on)

        # 2. The watcher's hold: held is said; a hold that cannot be read, or none named, holds too.
        host = fresh()
        host.add(1, "2026-11-01T00:00:00Z")
        limits.hold.write(hold_path, [("spend", "spend-day", "acme/app")], lambda: 1_800_000_000)
        code, said = go(host)
        check("a hold stands: nothing is judged or turned on",
              (code, "HELD: the watcher's hold stands" in said, host.log), (EXIT_OK, True, []))
        os.unlink(hold_path)
        code, said = go(host, c=dict(cfg, facts=dict(cfg["facts"], hold_file="")))
        check("no hold file named: nothing can say there is no hold",
              (code, "HELD: no hold file is named" in said, host.log), (EXIT_PARTIAL, True, []))

        # 3. Two qualified pull requests: one agent run, one approval, auto-merge on one only.
        host = fresh()
        a = host.add(1, "2026-11-01T00:00:00Z")
        host.add(2, "2026-11-02T00:00:00Z")
        code, said = go(host)
        check("two qualified: the oldest is judged, approved and turned on, at its head",
              (code, host.log, kinds(), host.prs["acme/app#1"].get("auto_head")),
              (EXIT_OK, [("agent",), ("check", "acme/app#1", "approved"), ("enable", "acme/app#1")],
               [("acme/app#1", "approved")], a))
        check("...and the agent's brief went in on stdin, once", len(host.stub.calls), 1)
        code, said = go(host)
        check("while one is in flight, no other is turned on and no agent runs",
              (code, writes(host), [w for w in host.log if w[0] == "agent"],
               "In flight: acme/app#1, approved at" in said),
              (EXIT_OK, [("enable", "acme/app#1")], [("agent",)], True))

        # 4. The first merges: the next is behind, gets GitHub's own update-branch, and qualifies
        #    again from scratch: checks on the new head, and a review of the new head.
        host.merge("acme/app#1")
        old = host.prs["acme/app#2"]["head"]
        code, said = go(host)
        check("the next one, behind its base and nothing else, is updated at its head; the pass ends",
              (code, writes(host)[-1], host.prs["acme/app#2"]["head"] != old,
               [w for w in host.log if w[0] == "agent"]),
              (EXIT_OK, ("update", "acme/app#2"), True, [("agent",)]))
        code, said = go(host)
        check("its new head waits for its checks and a fresh review: no row, nothing turned on",
              (code, "acme/app#2: waits: check-pending; its review judged an earlier head" in said,
               kinds(), writes(host)[-1]),
              (EXIT_OK, True, [("acme/app#1", "approved")], ("update", "acme/app#2")))
        host.prs["acme/app#2"]["checks"] = "red"
        code, said = go(host)
        check("a red check on the updated head holds it: never approved, said",
              ("acme/app#2: waits: check-failed" in said, kinds(), writes(host)[-1]),
              (True, [("acme/app#1", "approved")], ("update", "acme/app#2")))
        host.prs["acme/app#2"]["checks"] = "green"
        code, said = go(host)
        check("KIT-792: green checks on a stale review still wait, said in words, never approved",
              ("acme/app#2: waits: %s." % STALE_WORDS in said, kinds(),
               [w for w in host.log if w[0] == "agent"]),
              (True, [("acme/app#1", "approved")], [("agent",)]))
        host.prs["acme/app#2"]["review"] = host.prs["acme/app#2"]["head"]
        code, said = go(host)
        check("a fresh review of the new head: judged from scratch, approved, turned on at it",
              (code, kinds()[-1], writes(host)[-1], host.prs["acme/app#2"]["auto_head"]),
              (EXIT_OK, ("acme/app#2", "approved"), ("enable", "acme/app#2"),
               host.prs["acme/app#2"]["head"]))

        # 4b. Behind with anything else to wait on is not updated: only behind and nothing else.
        host = fresh()
        host.add(15, "2026-11-01T00:00:00Z")
        host.prs["acme/app#15"].update(behind=True, checks="pending")
        code, said = go(host)
        check("behind but its checks still running: no update, it waits, said",
              (writes(host), "acme/app#15: waits: check-pending; merge-behind" in said), ([], True))

        # 5. The floor and a human-only class are never picked: a held row, its check, said.
        host = fresh()
        host.add(3, "2026-11-01T00:00:00Z", paths=(".claude/hooks/pre-tool-use.py",))
        host.add(4, "2026-11-02T00:00:00Z", paths=("billing/stripe.py",))
        code, said = go(host)
        check("a change to the safety system or a human-only class is held for you, not turned on",
              (kinds(), [w for w in host.log if w[0] != "agent"], [w for w in host.log
                                                                  if w[0] == "agent"],
               "acme/app#3: held for you: it changes the safety system or a risk path" in said,
               "acme/app#4: held for you: it touches billing or accounts" in said),
              ([("acme/app#3", "held"), ("acme/app#4", "held")],
               [("check", "acme/app#3", "held"), ("check", "acme/app#4", "held")], [], True, True))
        code, said = go(host)
        check("...and a head that has its verdict is not judged again",
              (kinds(), "its head" in said and "has its verdict (held)" in said),
              ([("acme/app#3", "held"), ("acme/app#4", "held")], True))

        # 6. Auto-merge on a head with no approval: the validator's own is turned off; someone
        #    else's is left on, said, and nothing else is turned on while it is.
        host = fresh()
        host.add(5, "2026-11-01T00:00:00Z", by=_FakeHost.APP)
        code, said = go(host)
        check("the validator's own auto-merge on an unapproved head is turned off",
              (writes(host)[0], host.prs["acme/app#5"]["auto"]), (("disable", "acme/app#5"), None))
        host = fresh()
        host.add(6, "2026-11-01T00:00:00Z", by="the-owner")
        host.add(7, "2026-11-02T00:00:00Z")
        code, said = go(host)
        check("someone else's auto-merge is left on, and nothing else is turned on",
              (code, host.log, "turned on by the-owner, not the validator" in said),
              (EXIT_OK, [], True))

        # 7. An approved one that is behind while in flight gets GitHub's own update-branch.
        host = fresh()
        host.add(8, "2026-11-01T00:00:00Z")
        go(host)
        host.prs["acme/app#8"]["behind"] = True
        code, said = go(host)
        check("an approved pull request in flight that falls behind is updated at its head",
              writes(host), [("enable", "acme/app#8"), ("update", "acme/app#8")])

        # 8. An approval whose auto-merge did not get turned on is finished on the next pass.
        host = fresh()
        host.add(9, "2026-11-01T00:00:00Z")
        host.refuse.add("enable")
        code, said = go(host)
        check("GitHub refusing to turn auto-merge on is a failed write, said",
              (code, kinds(), "could not turn GitHub's own auto-merge on" in said),
              (EXIT_FAILED, [("acme/app#9", "approved")], True))
        host.refuse.discard("enable")
        code, said = go(host)
        check("...and the next pass turns it on, with no second agent run or row",
              (code, writes(host), kinds(), [w for w in host.log if w[0] == "agent"]),
              (EXIT_OK, [("enable", "acme/app#9")], [("acme/app#9", "approved")], [("agent",)]))

        # 9. A send-back: its row and check, nothing turned on, and one agent run per pass.
        host = fresh()
        host.add(10, "2026-11-01T00:00:00Z")
        host.add(11, "2026-11-02T00:00:00Z")
        host.stub.text = "SEND_BACK\nAdd a test for the new branch."
        code, said = go(host)
        check("a send-back is recorded, nothing is turned on, and the next waits for a later pass",
              (kinds(), writes(host), len(host.stub.calls)),
              ([("acme/app#10", "sent-back")], [], 1))

        # 10. A dry run judges and says, and runs no agent and writes nothing.
        host = fresh()
        host.add(12, "2026-11-01T00:00:00Z")
        code, said = go(host, dry=True)
        check("a dry run runs no agent and writes nothing",
              (code, host.log, kinds(), "[dry-run] acme/app#12 passes both layers" in said),
              (EXIT_OK, [], [], True))

        # 11. A list it could not read whole, or a ledger it cannot trust: nothing, said.
        host = fresh()
        host.add(13, "2026-11-01T00:00:00Z")
        host.complete = False
        code, said = go(host)
        check("an open list not read whole: nothing is turned on",
              (code, host.log, "could not be read whole" in said), (EXIT_PARTIAL, [], True))
        host = fresh()
        host.add(14, "2026-11-01T00:00:00Z")
        with open(ledger_path, "w", encoding="utf-8") as fh:
            fh.write("{not a ledger")
        code, said = go(host)
        check("a ledger it cannot read: nothing is approved or turned on",
              (code, host.log, "COULD NOT JUDGE" in said), (EXIT_PARTIAL, [], True))

    # 12. The config: every problem at once, by key; the example is valid; refused under an agent.
    check("the example config is valid", config(EXAMPLE)[1], [])
    _c, problems = config({"schema": "x", "settings_file": "rel.json", "merge": {"review_bar": "none",
                                                                                  "speed": 9},
                           "agent": {}, "check": {"app_id": "1", "key_file_env": "a path"}, "x": 1})
    check("a wrong config names every problem by key",
          [any(p.startswith(k) for p in problems) for k in (
              "'x'", "schema", "settings_file", "ledger_file", "merge.'speed'", "merge.review_bar",
              "agent.agent_bin", "check.app_id", "check.key_file_env")], [True] * 9)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["run", "--config", "/nonexistent/config.json"], environ={"CLAUDECODE": "1"})
    check("refused in an agent's environment, before it reads anything",
          (code, out.getvalue().startswith("REFUSED")), (EXIT_USAGE, True))

    # 13. It decides through the parts that own each answer, and never merges, approves or labels.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    body = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("mergePullRequest", "/merge", "gh pr", "addLabel", "labels", "APPROVE", "approve_",
              "createPullRequestReview", "subprocess", "os.system", "PATCH", "DELETE", "\"-X\"",
              "urlopen", "socket", "rulesets", "protection")
    check("it never merges, approves, labels or changes a setting, and starts no process itself",
          [w for w in banned if w in body], [])
    used = sorted(set(re.findall(r"\bgha\.([a-z_]+)\(", body)))
    check("its only GitHub writes are the helper's pinned calls, as the validator's app",
          used, ["app_info", "auto_merge", "bot_login", "installation_for", "installation_token",
                 "make_jwt", "update_branch"])
    check("it is the agent's run's one caller",
          os.path.basename(__file__) in vrun.CALLERS, True)

    if failures:
        for line in failures:
            print(line)
        print("validator merge selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator merge selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
