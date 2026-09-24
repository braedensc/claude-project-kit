#!/usr/bin/env python3
"""PR base check — every PR branches off the base branch and merges back into it.

    python3 scripts/check_pr_base.py            # read the PR base from the event
    python3 scripts/check_pr_base.py --selftest # the battery CI runs

Run by .github/workflows/pr-base.yml (and its template twin), which triggers on
`edited` as well as pushes, so a retarget re-runs it by itself.

WHY THIS EXISTS. On 2026-09-20 six PRs shipped as a six-deep stack
(#153←#154←#155←#156←#157←#158), each branched off its predecessor. Every
squash-merge of a base REWRITES the default branch, so all remaining descendants
turn CONFLICTING at once — and GitHub runs NO checks on a conflicted PR, so five
PRs read as "no checks reported", which looks like broken CI rather than a
conflict. Six stacked PRs meant five forced re-cascades. It ended by merging the
tip alone and closing the other four. Independent PRs off the base never do this.

THE BASE SET is the one every layer uses: the repo's default branch, plus `main`
and `master` (never feature branches, so a PR into them is not stacked — a
git-flow release PR into `main` on a `develop`-default repo passes). The two hooks
add a configured `github.defaultBranch` and `origin/HEAD`, which for a real repo
name the same default branch this reads off the event.

WHAT THIS LAYER CARRIES, AND WHAT IT DOES NOT. The PreToolUse stacked-branch guard
reads command text and is advisory (docs/SECURITY.md § *What the pattern guards
actually carry*). This check reads the base branch off the `pull_request` event,
which only GitHub writes, so no command spelling reaches it. What it cannot do is
*prevent* a stacked PR from merging: branch protection guards the default branch
only, so a PR whose base is a feature branch has no required context gating it.
This check makes such a PR visibly RED; it does not make it unmergeable. Nor does
it see a branch CUT from a feature branch whose PR targets the base — its extra
commits are just commits. Say both plainly rather than implying a gate.

  GITHUB_EVENT_NAME  which event this run is for
  PR_BASE_REF        the PR's base branch            (github.base_ref)
  DEFAULT_BRANCH     the repo's default branch       (github.event.repository.default_branch)

Exit 0 = the base is a base branch, or this is a known non-PR event (said out
         loud — docs/PIPELINE-CONTRACT.md §13: "nothing to do" and "could not do
         it" must never share a rendering).
Exit 1 = the base is another branch, the event is unknown, or a value needed to
         decide is missing.
"""
import os
import subprocess
import sys

PR_EVENTS = {"pull_request", "pull_request_target"}
NON_PR_EVENTS = {"push", "merge_group", "workflow_dispatch", "schedule", "workflow_call",
                 "workflow_run", "release", "repository_dispatch"}
ALWAYS_BASE = ("main", "master")

FAIL_HINT = """
Every PR branches off the base branch and merges back into it. Fix it:

  # 1. retarget this PR (this check re-runs by itself on the retarget)
  gh pr edit <n> --base {default}

  # 2. then read the diff. If the branch was also CUT from a feature branch,
  #    retargeting leaves that branch's commits in this PR — move your own
  #    commits onto the base instead:
  git rebase --onto origin/{default} {base}

See docs/COLLABORATION.md § "Never stack a PR"."""


def check(event_name, base_ref, default_branch):
    """(exit_code, message). Pure, so the selftest exercises the real decision."""
    if event_name in NON_PR_EVENTS:
        return 0, (f"not a pull_request event ({event_name}) — no PR base to "
                   "check, and nothing was checked")
    if event_name not in PR_EVENTS:
        return 1, (f"FAIL: event {event_name!r} is not one this check knows — it cannot "
                   "tell whether there is a PR base to check, so it refuses to pass. "
                   "Wire GITHUB_EVENT_NAME to ${{ github.event_name }}.")
    if not base_ref:
        # A PR event with no base is not "nothing to do": it is a value this check
        # needs and could not read. Fail rather than exit green on it.
        return 1, ("FAIL: this is a pull_request event but PR_BASE_REF is empty — "
                   "the base branch could not be read, so the check could not run. "
                   "Wire PR_BASE_REF to ${{ github.base_ref }}.")
    if not default_branch:
        return 1, ("FAIL: DEFAULT_BRANCH is empty — the repo's default branch "
                   "could not be read, so there is nothing to compare the base "
                   "against. Wire DEFAULT_BRANCH to "
                   "${{ github.event.repository.default_branch }}.")
    if base_ref == default_branch or base_ref in ALWAYS_BASE:
        return 0, f"OK: PR base is `{base_ref}`, a base branch"
    return 1, (f"FAIL: this PR is based on `{base_ref}`, not the base branch "
               f"`{default_branch}` — it is STACKED on another branch."
               + FAIL_HINT.format(default=default_branch, base=base_ref))


# ── selftest ──────────────────────────────────────────────────────────────────
# Each row asserts the DECISION plus a substring a caller would act on — never a
# copy of this file's own message (four selftest checks in this repo once matched
# their own source literals and so could never fail; found 2026-09-20).
SELFTEST = [
    # (name, event, base_ref, default_branch, want_code, want_in_message)
    ("base is the default branch", "pull_request", "main", "main", 0, "OK"),
    ("base is a feature branch", "pull_request", "feat/other", "main", 1, "STACKED"),
    ("base is a differently-named default", "pull_request", "develop", "develop", 0, "OK"),
    ("a develop-default project, stacked", "pull_request", "feat/other", "develop", 1, "`develop`"),
    ("main is a base even when the default is develop (a release PR)",
     "pull_request", "main", "develop", 0, "OK"),
    ("pull_request_target is checked too", "pull_request_target", "feat/x", "main", 1, "STACKED"),
    # Exact equality — a base that merely CONTAINS or STARTS WITH the default name is not it.
    ("a prefix of the default is not the default", "pull_request", "main-v2", "main", 1, "STACKED"),
    ("a feature branch ending in /main is not main", "pull_request", "feat/main-fix", "main", 1, "STACKED"),
    ("case matters", "pull_request", "Main", "main", 1, "STACKED"),
    # §13: absence and failure must not share a rendering.
    ("a push is not a PR and says so", "push", "", "main", 0, "nothing was checked"),
    ("an unknown event FAILS rather than passing quietly", "pull_request_review", "", "main", 1,
     "not one this check knows"),
    ("an empty event name FAILS", "", "", "main", 1, "not one this check knows"),
    ("a PR event with no base FAILS rather than passing quietly",
     "pull_request", "", "main", 1, "could not run"),
    ("an unreadable default branch FAILS rather than passing quietly",
     "pull_request", "feat/x", "", 1, "nothing to compare"),
    ("the remedy names the recovery for a branch CUT from the base it names",
     "pull_request", "feat/other", "main", 1, "git rebase --onto origin/main feat/other"),
]

# main() is what CI actually runs, so it is exercised too — as a subprocess, with a
# CLEAN environment (only PATH), so the runner's own GITHUB_* variables cannot leak
# in. A main() that ignored the verdict, or read the wrong variable, fails here.
MAIN_ROWS = [
    ({}, 1),
    ({"GITHUB_EVENT_NAME": "pull_request", "PR_BASE_REF": "feat/x", "DEFAULT_BRANCH": "main"}, 1),
    ({"GITHUB_EVENT_NAME": "pull_request", "PR_BASE_REF": "main", "DEFAULT_BRANCH": "main"}, 0),
    ({"GITHUB_EVENT_NAME": "push", "PR_BASE_REF": "", "DEFAULT_BRANCH": "main"}, 0),
    ({"GITHUB_EVENT_NAME": "pull_request", "PR_BASE_REF": " main ", "DEFAULT_BRANCH": "main"}, 0),
]


def selftest():
    failures = 0
    for name, event, base, default, want_code, want_in in SELFTEST:
        code, msg = check(event, base, default)
        ok = code == want_code and want_in in msg
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  "
              f"(want exit {want_code} + {want_in!r}, got exit {code})")
        if not ok:
            print(f"         message: {msg}")
            failures += 1
    for env_row, want_code in MAIN_ROWS:
        env = dict(env_row, PATH=os.environ.get("PATH", ""))
        r = subprocess.run([sys.executable, os.path.abspath(__file__)], env=env,
                           capture_output=True, text=True)
        ok = r.returncode == want_code
        print(f"[{'PASS' if ok else 'FAIL'}] main() with {env_row or 'no environment'}  "
              f"(want exit {want_code}, got {r.returncode})")
        failures += 0 if ok else 1
    total = len(SELFTEST) + len(MAIN_ROWS)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    code, msg = check(
        os.environ.get("GITHUB_EVENT_NAME", "").strip(),
        os.environ.get("PR_BASE_REF", "").strip(),
        os.environ.get("DEFAULT_BRANCH", "").strip(),
    )
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
