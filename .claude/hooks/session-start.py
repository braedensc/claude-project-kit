#!/usr/bin/env python3
"""
SessionStart hook — orientation, not enforcement.

Injects a short repo-state summary (branch, dirty tree, open PR for the branch, and a
one-line reminder of the guards) into the session's context at startup, so a fresh
session opens already knowing where it is instead of discovering it by running git.

Output contract (Claude Code hooks): print JSON with
  {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}
to stdout; the text becomes session context. Always exits 0 — this hook cannot and
should not block (SessionStart has no block semantics), and it fails open silently on
any error so a missing `gh`/network/git never delays a session start.

NOTE: unlike pre-tool-use.py / stop-pr-check.py, this hook is DELIBERATELY NOT in the
self-protected set. Those enforce blocks the agent might want gone; this only informs,
so there's nothing to "edit away." Keep it read-only and side-effect-free.

Distilled from the todoclaw session-start pattern; generalized for the kit 2026-07-04.
"""
import json
import os
import subprocess
import sys


def _run(args, timeout=4):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def main():
    # Read the payload but don't require anything from it.
    try:
        json.load(sys.stdin)
    except Exception:
        pass

    root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    branch = _run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
    if not branch:
        sys.exit(0)  # not a git repo — say nothing

    lines = [f"Repo orientation (SessionStart hook): on branch `{branch}`."]

    dirty = _run(["git", "-C", root, "status", "--porcelain"])
    lines.append("Working tree: " + ("dirty (uncommitted changes)." if dirty else "clean."))

    if branch not in ("main", "master"):
        # Best-effort open-PR lookup; silent if gh is missing/unauthed/offline.
        import shutil
        if shutil.which("gh"):
            pr = _run(["gh", "pr", "view", branch, "--json", "number,state",
                       "-q", '"#\\(.number) \\(.state)"'], timeout=6)
            if pr:
                lines.append(f"This branch's PR: {pr}.")
        lines.append(
            "Reminder: commits go on this feature branch via PR; you never merge "
            "(`gh pr merge` is hook-blocked). Open the PR, watch CI to green, then stop."
        )
    else:
        lines.append(
            "You're on a protected branch — Edit/Write/commit are hook-blocked here. "
            "Branch first: `git checkout -b <type>/<short-kebab-desc>`."
        )

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": " ".join(lines),
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
