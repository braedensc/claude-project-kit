#!/usr/bin/env python3
"""PR base check — every PR branches off the default branch and merges back into it.

    python3 scripts/check_pr_base.py            # read the PR base from the event
    python3 scripts/check_pr_base.py --selftest # the battery CI runs

WHY THIS EXISTS. On 2026-09-20 six PRs shipped as a six-deep stack
(#153←#154←#155←#156←#157←#158), each branched off its predecessor. Every
squash-merge of a base REWRITES the default branch, so all remaining descendants
turn CONFLICTING at once — and GitHub runs NO checks on a conflicted PR, so five
PRs read as "no checks reported", which looks like broken CI rather than a
conflict. Six stacked PRs meant five forced re-cascades. It ended by merging the
tip alone and closing the other four. Independent PRs off the default branch
never do this.

WHAT THIS LAYER CARRIES, AND WHAT IT DOES NOT. The PreToolUse hook's
stacked-branch guard matches command TEXT and is therefore advisory — a
respelling gets past it (docs/SECURITY.md § *What the pattern guards actually
carry*). This check does not read command text at all: it reads the base branch
off the `pull_request` event, which only GitHub writes, so no spelling reaches
it. What it still cannot do is *prevent* a stacked PR from being merged: branch
protection guards the default branch only, so a PR whose base is a feature branch
is not gated by any required context. This check makes such a PR visibly RED; it
does not make it unmergeable. Say that plainly rather than implying a gate that
is not there.

  PR_BASE_REF      the PR's base branch  (github.base_ref)
  DEFAULT_BRANCH   the repo's default    (github.event.repository.default_branch)
  GITHUB_EVENT_NAME  used only to tell "not a PR" from "could not read the base"

Exit 0 = the base is the default branch, or this is not a pull_request event
         (said out loud — docs/PIPELINE-CONTRACT.md §13: "nothing to do" and
         "could not do it" must never share a rendering).
Exit 1 = the base is some other branch, or a value needed to decide is missing.
"""
import os
import sys

FAIL_HINT = """
Every PR branches off the default branch and merges back into it. Fix it:

  # 1. retarget this PR
  gh pr edit <n> --base {default}

  # 2. then read the diff. If the branch was also CUT from a feature branch,
  #    retargeting leaves that branch's commits in this PR — re-cut instead:
  git fetch origin && git checkout -b <type>/<desc> origin/{default}

See docs/COLLABORATION.md § "Never stack a PR"."""


def check(event_name, base_ref, default_branch):
    """(exit_code, message). Pure, so the selftest exercises the real decision."""
    if event_name and event_name != "pull_request":
        return 0, (f"not a pull_request event ({event_name}) — no PR base to "
                   "check, and nothing was checked")
    if not base_ref:
        # A pull_request event with no base is not "nothing to do": it is a value
        # this check needs and could not read. Fail rather than exit green on it.
        return 1, ("FAIL: this is a pull_request event but PR_BASE_REF is empty — "
                   "the base branch could not be read, so the check could not run. "
                   "Wire PR_BASE_REF to ${{ github.base_ref }}.")
    if not default_branch:
        return 1, ("FAIL: DEFAULT_BRANCH is empty — the repo's default branch "
                   "could not be read, so there is nothing to compare the base "
                   "against. Wire DEFAULT_BRANCH to "
                   "${{ github.event.repository.default_branch }}.")
    if base_ref != default_branch:
        return 1, (f"FAIL: this PR is based on `{base_ref}`, not the default "
                   f"branch `{default_branch}` — it is STACKED on another branch."
                   + FAIL_HINT.format(default=default_branch))
    return 0, f"OK: PR base is `{base_ref}`, the default branch"


# ── selftest ──────────────────────────────────────────────────────────────────
# Each row asserts the DECISION, never a string this file also produces — four
# selftest checks in this repo once matched their own source literals and so
# could never fail (found 2026-09-20). The oracle here is the exit code plus a
# substring the caller would act on, not a copy of the message.
SELFTEST = [
    # (name, event, base_ref, default_branch, want_code, want_in_message)
    ("base is the default branch", "pull_request", "main", "main", 0, "OK"),
    ("base is a feature branch", "pull_request", "feat/other", "main", 1, "STACKED"),
    ("base is a differently-named default", "pull_request", "develop", "develop", 0, "OK"),
    ("a project whose default is develop, stacked",
     "pull_request", "feat/other", "develop", 1, "`develop`"),
    # main is NOT special-cased: what counts is the repo's own default branch, so a
    # PR into `main` on a develop-default repo is still the wrong base.
    ("main is not privileged over the configured default",
     "pull_request", "main", "develop", 1, "STACKED"),
    # §13: absence and failure must not share a rendering.
    ("a push is not a PR and says so", "push", "", "main", 0, "nothing was checked"),
    ("a PR event with no base FAILS rather than passing quietly",
     "pull_request", "", "main", 1, "could not run"),
    ("an unreadable default branch FAILS rather than passing quietly",
     "pull_request", "feat/x", "", 1, "nothing to compare"),
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
    print(f"\n{len(SELFTEST) - failures}/{len(SELFTEST)} checks passed")
    return 1 if failures else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    code, msg = check(
        os.environ.get("GITHUB_EVENT_NAME", "pull_request").strip(),
        os.environ.get("PR_BASE_REF", "").strip(),
        os.environ.get("DEFAULT_BRANCH", "").strip(),
    )
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
