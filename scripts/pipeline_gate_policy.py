#!/usr/bin/env python3
"""The Actions policy that lets the label gate run from the default branch (KIT-668). The owner's step.

    python3 scripts/pipeline_gate_policy.py check   # read only: is the allow in place?
    python3 scripts/pipeline_gate_policy.py run     # check, show, one typed yes, make it, check again
    python3 scripts/pipeline_gate_policy.py --selftest

Run it from your checkout of the repository; `--repo OWNER/NAME` names another.

WHY. The label gate (.github/workflows/hooks-change-guard.yml) runs on `pull_request_target`, so
GitHub takes it from the default branch and a pull request cannot change how it is judged. From
2 Dec 2026 GitHub blocks that event in public repositories unless an Actions policy allows it.
Without the allow the gate never runs, its required check never reports, and nothing can merge.

WHAT IT MAKES. One repository policy, active, for that one workflow file. It allows the two events
the gate runs on: `pull_request_target`, and `pull_request`, which runs only for a pull request that
edits the gate. Nothing else is touched, and no policy is ever edited or deleted. To undo it: the
repository's Settings, then Actions, then Policies, and delete it there.

THE ANSWERS.
  ALLOWED      an active policy allows both events for the gate's file, and none leaves one out
  NOT NEEDED   the repository is not public: GitHub's default block covers public ones only
  NOT ALLOWED  no policy allows them yet; `run` makes the one above
  BLOCKED      a policy that applies to the gate's file restricts events and leaves one of the two
               out. Change that policy in Settings, Actions, Policies: this script never edits one
  UNKNOWN      something could not be read. Never read as any other answer

Exit: 0 allowed, not needed, or done · 1 not allowed or blocked, or not done after the write ·
2 usage · 3 refused (an agent's environment, or root) · 4 something could not be checked; nothing
changed · 10 you did not type yes; nothing changed.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402  (one tuple, owned there)

GATE = ".github/workflows/hooks-change-guard.yml"
EVENTS = ("pull_request_target", "pull_request")
POLICY_NAME = "Label gate runs from the default branch"
POLICY = {
    "name": POLICY_NAME,
    "enforcement": "active",
    "conditions": {"workflow_path": {"include": [GATE], "exclude": []}},
    "rules": [{"type": "restrict_action_events", "parameters": {"allowed_events": list(EVENTS)}}],
}
REPO_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
PER_PAGE = 100
ALLOWED, NOT_NEEDED, NOT_ALLOWED, BLOCKED, UNKNOWN = (
    "ALLOWED", "NOT NEEDED", "NOT ALLOWED", "BLOCKED", "UNKNOWN")
EXIT_OK, EXIT_NOT, EXIT_USAGE, EXIT_REFUSED, EXIT_UNCHECKED, EXIT_NOT_YES = 0, 1, 2, 3, 4, 10
CHECK_EXIT = {ALLOWED: EXIT_OK, NOT_NEEDED: EXIT_OK, NOT_ALLOWED: EXIT_NOT, BLOCKED: EXIT_NOT,
              UNKNOWN: EXIT_UNCHECKED}


class IO:
    """Everything this script asks of the world: the environment, `gh`, and you."""

    def env(self):
        return dict(os.environ)

    def uid(self):
        return os.getuid()

    def gh(self, args, stdin=None):
        """(ok, stdout, stderr) for one `gh` call."""
        try:
            p = subprocess.run(["gh"] + list(args), input=stdin, capture_output=True, text=True,
                               timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, "", str(exc)
        return p.returncode == 0, p.stdout, p.stderr

    def ask(self, prompt):
        try:
            return input(prompt)
        except EOFError:
            return ""

    def say(self, text):
        print(text)


def refused(io):
    if any(m in io.env() for m in AGENT_ENV_MARKERS):
        return "an agent's environment: this script is yours to run, in your own terminal"
    if io.uid() == 0:
        return "root: run it as yourself"
    return None


def applies(policy, path=GATE):
    """Whether `policy`'s conditions take in the workflow at `path`. No condition is every workflow;
    an empty include, or `~ALL`, is every workflow not excluded."""
    cond = policy.get("conditions")
    wp = cond.get("workflow_path") if isinstance(cond, dict) else None
    if not isinstance(wp, dict):
        return True
    inc = [p for p in (wp.get("include") or []) if isinstance(p, str)]
    exc = [p for p in (wp.get("exclude") or []) if isinstance(p, str)]
    if any(fnmatch.fnmatchcase(path, p) for p in exc):
        return False
    return not inc or "~ALL" in inc or any(fnmatch.fnmatchcase(path, p) for p in inc)


def allowed_events(policy):
    """The events a policy's event rule allows, or None when it has no event rule."""
    for rule in policy.get("rules") or []:
        if isinstance(rule, dict) and rule.get("type") == "restrict_action_events":
            events = (rule.get("parameters") or {}).get("allowed_events")
            return set(e for e in events if isinstance(e, str)) if isinstance(events, list) else set()
    return None


def judge(visibility, policies):
    """(answer, words) for a repository's visibility and its policies as GitHub lists them."""
    if visibility in ("PRIVATE", "INTERNAL"):
        return NOT_NEEDED, ("the repository is %s: GitHub's default block on pull_request_target "
                            "covers public repositories only" % visibility.lower())
    if visibility != "PUBLIC" or not isinstance(policies, list):
        return UNKNOWN, "the repository's visibility or its policies could not be read"
    allows, blockers = [], []
    for p in policies:
        if not isinstance(p, dict) or p.get("enforcement") != "active" or not applies(p):
            continue
        events = allowed_events(p)
        if events is None:
            continue
        name = str(p.get("name") or p.get("id") or "a policy")
        missing = [e for e in EVENTS if e not in events]
        (blockers if missing else allows).append((name, missing))
    if blockers:
        return BLOCKED, "; ".join("the policy %r applies to %s and does not allow %s"
                                  % (n, GATE, " or ".join(m)) for n, m in blockers)
    if allows:
        return ALLOWED, "the policy %r allows %s for %s" % (allows[0][0], " and ".join(EVENTS), GATE)
    return NOT_ALLOWED, "no Actions policy allows pull_request_target for %s" % GATE


def read(io, repo):
    """(repo, answer, words): the repository named, or the checkout's own when `repo` is None."""
    ok, out, _err = io.gh(["repo", "view"] + ([repo] if repo else []) + ["--json", "nameWithOwner,visibility"])
    try:
        meta = json.loads(out) if ok else None
    except ValueError:
        meta = None
    name = meta.get("nameWithOwner") if isinstance(meta, dict) else None
    if not isinstance(name, str) or not REPO_RE.fullmatch(name):
        return repo, UNKNOWN, "the repository could not be read (run this from its checkout, or pass --repo)"
    visibility = meta.get("visibility")
    if visibility in ("PRIVATE", "INTERNAL"):
        return (name,) + judge(visibility, [])
    ok, out, _err = io.gh(["api", "repos/%s/actions/policies?per_page=%d" % (name, PER_PAGE)])
    try:
        doc = json.loads(out) if ok else None
    except ValueError:
        doc = None
    policies = doc.get("policies") if isinstance(doc, dict) else None
    total = doc.get("total_count") if isinstance(doc, dict) else None
    if not isinstance(policies, list) or not isinstance(total, int) or total > len(policies):
        return name, UNKNOWN, ("the repository's Actions policies could not be read whole, so whether "
                               "the gate may run is not known")
    return (name,) + judge(visibility, policies)


def say_answer(io, repo, answer, words):
    io.say("%s: %s for %s: %s." % (answer, GATE, repo or "this repository", words))


def cmd_check(io, repo):
    repo, answer, words = read(io, repo)
    say_answer(io, repo, answer, words)
    if answer == NOT_ALLOWED:
        io.say("From 2 Dec 2026 the label gate will not run there, and nothing can merge. "
               "Make the allow: python3 scripts/pipeline_gate_policy.py run")
    if answer == BLOCKED:
        io.say("Change that policy in the repository's Settings, Actions, Policies. "
               "This script never edits one.")
    return CHECK_EXIT[answer]


def cmd_run(io, repo):
    why = refused(io)
    if why:
        io.say("REFUSED: %s. Nothing changed." % why)
        return EXIT_REFUSED
    repo, answer, words = read(io, repo)
    say_answer(io, repo, answer, words)
    if answer in (ALLOWED, NOT_NEEDED):
        io.say("Nothing to do. Nothing changed.")
        return EXIT_OK
    if answer == UNKNOWN:
        io.say("Nothing changed. Run it again; if it repeats, paste this terminal to Claude.")
        return EXIT_UNCHECKED
    if answer == BLOCKED:
        io.say("Nothing changed. Change that policy in the repository's Settings, Actions, Policies.")
        return EXIT_NOT
    io.say("\nThis makes one Actions policy on %s:\n"
           "  name:         %s\n"
           "  enforcement:  active\n"
           "  workflow:     %s (that file only)\n"
           "  events:       %s\n"
           "It changes nothing else. To undo it: Settings, Actions, Policies, and delete it there.\n"
           % (repo, POLICY_NAME, GATE, ", ".join(EVENTS)))
    if io.ask("Type yes to go on: ").strip() != "yes":
        io.say("You did not type yes. Nothing changed.")
        return EXIT_NOT_YES
    ok, _out, err = io.gh(["api", "-X", "POST", "repos/%s/actions/policies" % repo, "--input", "-"],
                          stdin=json.dumps(POLICY))
    if not ok:
        io.say("NOT DONE: GitHub refused the policy (%s). Paste this terminal to Claude."
               % ((err or "no reason given").strip().splitlines() or ["no reason given"])[-1][:300])
        return EXIT_NOT
    _repo, answer, words = read(io, repo)
    say_answer(io, repo, answer, words)
    if answer != ALLOWED:
        io.say("NOT DONE: the policy was sent, but reading it back did not show the allow. "
               "Paste this terminal to Claude.")
        return EXIT_NOT
    io.say("DONE: the label gate may run from the default branch on %s." % repo)
    return EXIT_OK


# ======================================================================================
# SELFTEST: nothing below this line runs outside --selftest.
# ======================================================================================
class _FakeIO(IO):
    def __init__(self, world, env=None, uid=501, answer="yes"):
        self.world, self._env, self._uid, self.answer = world, env or {}, uid, answer
        self.calls, self.lines, self.asked = [], [], []

    def env(self):
        return dict(self._env)

    def uid(self):
        return self._uid

    def ask(self, prompt):
        self.asked.append(prompt)
        return self.answer

    def say(self, text):
        self.lines.append(text)

    def gh(self, args, stdin=None):
        args = list(args)
        self.calls.append((args, stdin))
        w = self.world
        if args[:2] == ["repo", "view"]:
            meta = w.get("meta")
            return (meta is not None, json.dumps(meta) if meta is not None else "", "")
        if args[:2] == ["api", "-X"]:
            if w.get("post_fails"):
                return False, "", "HTTP 422: Validation Failed\n"
            if not w.get("post_ignored"):
                w["policies"].append(json.loads(stdin))
            return True, "{}", ""
        if args and args[0] == "api":
            if w.get("policies_fail"):
                return False, "", "HTTP 500"
            if "raw" in w:
                return True, w["raw"], ""
            ps = w.get("policies", [])
            return True, json.dumps({"total_count": w.get("total", len(ps)), "policies": ps}), ""
        return False, "", "unexpected call %r" % args


def selftest():
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, got, want))

    PUBLIC = {"nameWithOwner": "acme/kit", "visibility": "PUBLIC"}

    def policy(include=(GATE,), events=EVENTS, enforcement="active", exclude=(), name="p"):
        doc = {"id": 7, "name": name, "enforcement": enforcement, "source_type": "Repository",
               "conditions": {"workflow_path": {"include": list(include), "exclude": list(exclude)}},
               "rules": []}
        if events is not None:
            doc["rules"].append({"type": "restrict_action_events",
                                 "parameters": {"allowed_events": list(events)}})
        return doc

    def checked(world):
        io = _FakeIO(world)
        rc = cmd_check(io, None)
        return rc, io.lines[0].split(":", 1)[0]

    # 1. The answers, read only.
    for label, world, want in (
            ("a public repository with no policy", {"meta": PUBLIC, "policies": []},
             (EXIT_NOT, NOT_ALLOWED)),
            ("a private repository needs none", {"meta": dict(PUBLIC, visibility="PRIVATE")},
             (EXIT_OK, NOT_NEEDED)),
            ("an internal repository needs none", {"meta": dict(PUBLIC, visibility="INTERNAL")},
             (EXIT_OK, NOT_NEEDED)),
            ("the allow this script makes", {"meta": PUBLIC, "policies": [POLICY]}, (EXIT_OK, ALLOWED)),
            ("an allow in evaluate mode allows nothing yet",
             {"meta": PUBLIC, "policies": [policy(enforcement="evaluate")]}, (EXIT_NOT, NOT_ALLOWED)),
            ("an allow for another workflow file",
             {"meta": PUBLIC, "policies": [policy(include=(".github/workflows/ci.yml",))]},
             (EXIT_NOT, NOT_ALLOWED)),
            ("an allow whose exclude takes the gate's file out",
             {"meta": PUBLIC, "policies": [policy(include=("~ALL",), exclude=(GATE,))]},
             (EXIT_NOT, NOT_ALLOWED)),
            ("an allow for every workflow", {"meta": PUBLIC, "policies": [policy(include=("~ALL",))]},
             (EXIT_OK, ALLOWED)),
            ("an allow by a glob that matches the file",
             {"meta": PUBLIC, "policies": [policy(include=(".github/workflows/*.yml",))]},
             (EXIT_OK, ALLOWED)),
            ("a policy with no event rule restricts no event",
             {"meta": PUBLIC, "policies": [policy(events=None)]}, (EXIT_NOT, NOT_ALLOWED)),
            ("a policy over every workflow that allows push alone blocks the gate",
             {"meta": PUBLIC, "policies": [POLICY, policy(include=(), events=("push",), name="push only")]},
             (EXIT_NOT, BLOCKED)),
            ("a policy that leaves out pull_request blocks the gate's own-copy run",
             {"meta": PUBLIC, "policies": [policy(events=("pull_request_target",))]},
             (EXIT_NOT, BLOCKED)),
            ("the repository cannot be read", {"meta": None}, (EXIT_UNCHECKED, UNKNOWN)),
            ("a visibility GitHub never gives", {"meta": dict(PUBLIC, visibility="SECRET"), "policies": []},
             (EXIT_UNCHECKED, UNKNOWN)),
            ("the policies cannot be read", {"meta": PUBLIC, "policies_fail": True},
             (EXIT_UNCHECKED, UNKNOWN)),
            ("more policies than one read holds",
             {"meta": PUBLIC, "policies": [POLICY], "total": 101}, (EXIT_UNCHECKED, UNKNOWN)),
            ("an answer that is not JSON", {"meta": PUBLIC, "raw": "<html>"}, (EXIT_UNCHECKED, UNKNOWN)),
            ("an answer with no policy list", {"meta": PUBLIC, "raw": json.dumps({"total_count": 0})},
             (EXIT_UNCHECKED, UNKNOWN))):
        check("check: %s" % label, checked(world), want)
    io = _FakeIO({"meta": PUBLIC, "policies": [policy(include=(), events=("push",), name="push only")]})
    cmd_check(io, None)
    check("check: a blocking policy is named, and the fix is said to be the owner's, in settings",
          ("'push only'" in io.lines[0], any("never edits one" in ln for ln in io.lines)), (True, True))
    io = _FakeIO({"meta": PUBLIC, "policies": []})
    cmd_check(io, "acme/kit")
    check("check: --repo names the repository to read, and nothing is written",
          (io.calls[0][0][:3], [c for c in io.calls if "-X" in c[0]]), (["repo", "view", "acme/kit"], []))

    # 2. run: refused under a model or as root, before anything is read.
    for label, kw in (("an agent's environment", {"env": {sorted(AGENT_ENV_MARKERS)[0]: "1"}}),
                      ("root", {"uid": 0})):
        io = _FakeIO({"meta": PUBLIC, "policies": []}, **kw)
        check("run: refused in %s, and nothing is read or written" % label,
              (cmd_run(io, None), io.calls, io.asked), (EXIT_REFUSED, [], []))

    # 3. run: nothing to do, or nothing it may do, writes nothing and asks nothing.
    for label, world, want in (
            ("already allowed", {"meta": PUBLIC, "policies": [POLICY]}, EXIT_OK),
            ("not needed", {"meta": dict(PUBLIC, visibility="PRIVATE")}, EXIT_OK),
            ("blocked by another policy",
             {"meta": PUBLIC, "policies": [policy(include=(), events=("push",))]}, EXIT_NOT),
            ("unknown", {"meta": PUBLIC, "policies_fail": True}, EXIT_UNCHECKED)):
        io = _FakeIO(world)
        rc = cmd_run(io, None)
        check("run: %s writes nothing and asks nothing" % label,
              (rc, [c for c in io.calls if "-X" in c[0]], io.asked), (want, [], []))

    # 4. run: the one typed yes.
    world = {"meta": PUBLIC, "policies": []}
    io = _FakeIO(world, answer="y")
    check("run: anything but yes changes nothing, after showing what it would make",
          (cmd_run(io, None), [c for c in io.calls if "-X" in c[0]],
           any(GATE + " (that file only)" in ln for ln in io.lines)), (EXIT_NOT_YES, [], True))
    io = _FakeIO(world)
    rc = cmd_run(io, None)
    posts = [c for c in io.calls if "-X" in c[0]]
    check("run: yes sends one POST of exactly this policy, then reads it back: DONE",
          (rc, len(posts), posts[0][0] if posts else None, json.loads(posts[0][1]) if posts else None,
           io.lines[-1].startswith("DONE")),
          (EXIT_OK, 1, ["api", "-X", "POST", "repos/acme/kit/actions/policies", "--input", "-"],
           POLICY, True))
    io = _FakeIO({"meta": PUBLIC, "policies": [], "post_fails": True})
    check("run: a refused POST is NOT DONE, with GitHub's reason, never DONE",
          (cmd_run(io, None), io.lines[-1].startswith("NOT DONE"), "422" in io.lines[-1]),
          (EXIT_NOT, True, True))
    io = _FakeIO({"meta": PUBLIC, "policies": [], "post_ignored": True})
    check("run: a POST that does not read back as the allow is NOT DONE",
          (cmd_run(io, None), io.lines[-1].startswith("NOT DONE")), (EXIT_NOT, True))

    # 5. What it can write, and what the policy says.
    check("the policy names the gate's one file, and only the two events it runs on",
          (POLICY["conditions"], POLICY["rules"], POLICY["enforcement"]),
          ({"workflow_path": {"include": [GATE], "exclude": []}},
           [{"type": "restrict_action_events",
             "parameters": {"allowed_events": ["pull_request_target", "pull_request"]}}], "active"))
    import check_label_gate
    check("the gate's file is the one the label-gate battery pins", GATE, check_label_gate.WORKFLOW)
    src = open(os.path.abspath(__file__), encoding="utf-8").read().split("\n# " + "=" * 86, 1)[0]
    check("the one write is a POST to actions/policies: no PUT, PATCH or DELETE anywhere",
          (src.count('"-X"'), '"POST"' in src, any(m in src for m in ('"PUT"', '"PATCH"', '"DELETE"'))),
          (1, True, False))

    if failures:
        print("\n".join(failures))
        print("pipeline_gate_policy selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: pipeline_gate_policy selftest (%d checks)" % total[0])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The Actions policy that lets the label gate run.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("cmd", nargs="?", choices=("check", "run"))
    ap.add_argument("--repo", default=None, help="OWNER/NAME; default: this checkout's repository")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.repo is not None and not REPO_RE.fullmatch(args.repo):
        print("--repo must be OWNER/NAME")
        return EXIT_USAGE
    if args.cmd == "check":
        return cmd_check(IO(), args.repo)
    if args.cmd == "run":
        return cmd_run(IO(), args.repo)
    ap.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
