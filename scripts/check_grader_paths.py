#!/usr/bin/env python3
"""Server-side backstop: a PR touching a GRADER path must carry the label, and the
label must have been applied by SOMEONE OTHER THAN THE AGENT.

Grader paths are the machinery that decides whether a change is acceptable —
the hook scripts and settings files (human-only locally, via hook
self-protection), the CI workflows that gate the PR, the `scripts/check_*.py`
graders those workflows run, and, when a project has opted into the agentic
delivery pipeline, `delivery.json` plus everything in its `autonomy.riskPaths`.
Local self-protection constrains only sessions that RUN the hook; a PR authored
anywhere else (another clone, the GitHub web UI, an Action) never meets it. This
job is the layer that does.

The configured set is read from the PR's BASE sha, never from the head: a head
that could shorten its own `riskPaths` would be grading its own homework. The
workflow applies the same rule to THIS FILE — it executes the base revision of
the script, so a PR cannot weaken its own gate by editing the gate.

TWO THINGS ARE CHECKED, and the second is the durable one:

  1. Is the required label present at all?
  2. WHO put it there? The label is an ACKNOWLEDGEMENT — the point is that a
     person read a guard-machinery change. A session that can label its own PR
     supplies its own acknowledgement, turns the check green, and removes the one
     signal that tells a reviewer to look closely. The PreToolUse hook blocks the
     `gh` spellings for this, but a denylist of shell spellings is never
     exhaustive; checking the APPLIER closes the whole class at once.

  LABELS           space-separated PR labels
  BASE_SHA         PR base commit
  HEAD_SHA         PR head commit
  REQUIRED_LABEL   default "hooks-change"
  GITHUB_REPOSITORY / PR_NUMBER      identify the PR for the events API
  PR_AUTHOR / PR_AUTHOR_TYPE         the PR's author login and "User"|"Bot"
  GITHUB_TOKEN                       read access for the events API
  STRICT_LABEL_IDENTITY   "1" ⇒ also fail when the applier IS the author even
                          though both are human accounts (see below)

WHAT THE IDENTITY CHECK CAN AND CANNOT SEE. It has teeth exactly when the agent
holds an identity distinct from the acknowledging human — the pipeline case,
where the PR is authored by an App/bot. It CANNOT help when a local session
drives `gh` with the human's own credential: GitHub then sees one identity for
both the PR and the label, and no API can separate "the human typed it" from
"the agent ran it". That topology is covered by the PreToolUse hook instead, and
this script says so out loud rather than implying a guarantee it does not have.

Exit 0 = nothing to gate, or the label is present and properly attributed.
Exit 1 = label required, or the acknowledgement was self-supplied.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

# Always gated, in every project, pipeline or not. Kept in step with the
# PreToolUse hook's self-protected set and its GRADER_PATH_FLOOR.
# `scripts/check_*.py` is here because those graders ARE the acceptance
# machinery: the workflows that gate a PR are only as trustworthy as the
# checkers they shell out to, and gating the workflow while leaving the checker
# open just moves the soft spot one file over.
FLOOR = (
    ".claude/hooks/**",
    ".claude/settings*.json",
    ".github/workflows/**",
    "scripts/check_*.py",
    "delivery.json",
)
DELIVERY_FILE = "delivery.json"
API = "https://api.github.com"


def glob_to_re(pat):
    """git-style glob → regex over a '/'-separated repo-relative path. Mirrors
    `_glob_to_re` in .claude/hooks/pre-tool-use.py — keep the two in step."""
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append(r"(?:[^/]+/)*"); i += 3
        elif pat.startswith("**", i):
            out.append(r".*"); i += 2
        elif pat[i] == "*":
            out.append(r"[^/]*"); i += 1
        elif pat[i] == "?":
            out.append(r"[^/]"); i += 1
        else:
            out.append(re.escape(pat[i])); i += 1
    return "^" + "".join(out) + "$"


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _is_bot(login, type_=""):
    """A machine identity. `type` is authoritative when the API gives it;
    the `[bot]` suffix is the fallback for places that only carry a login."""
    login = (login or "").strip()
    return (type_ or "").strip().lower() == "bot" or login.endswith("[bot]")


def _label_events(repo, number):
    """`labeled` events for this PR, oldest → newest. Returns None if they could
    not be fetched (caller decides what an unknowable applier means).

    `_EVENTS_JSON` short-circuits the network for --selftest."""
    injected = os.environ.get("_EVENTS_JSON")
    if injected is not None:
        try:
            return json.loads(injected)
        except Exception:
            return None
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not (repo and number and token):
        return None
    events, page = [], 1
    while page <= 10:  # 1000 events is far past any real PR
        req = urllib.request.Request(
            f"{API}/repos/{repo}/issues/{number}/events?per_page=100&page={page}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "check-grader-paths",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"::warning::could not read PR events ({exc})")
            return None
        if not batch:
            break
        events.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return events


def check_label_identity(label):
    """0 if the acknowledgement came from someone who could legitimately give it."""
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    number = os.environ.get("PR_NUMBER", "").strip()
    author = os.environ.get("PR_AUTHOR", "").strip()
    author_bot = _is_bot(author, os.environ.get("PR_AUTHOR_TYPE", ""))
    strict = os.environ.get("STRICT_LABEL_IDENTITY", "").strip() == "1"

    events = _label_events(repo, number)
    if events is None:
        # Fail CLOSED, like the unparseable-config path above: the label is
        # present but we cannot say who vouched for it, and "an acknowledgement
        # from nobody in particular" is exactly what this check exists to reject.
        print(
            f"::error::'{label}' is present but its applier could not be "
            "determined (PR events unreadable — needs `issues: read` and "
            "GITHUB_TOKEN). Re-run the job; if this persists, remove and re-add "
            "the label so a fresh event is recorded."
        )
        return 1

    applied = [
        e for e in events
        if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == label
    ]
    if not applied:
        print(
            f"::error::'{label}' is on the PR but no `labeled` event records who "
            "added it. The acknowledgement cannot be attributed, so it does not "
            "count. Remove the label and have a human re-apply it."
        )
        return 1

    last = applied[-1]  # the CURRENT acknowledgement; earlier ones were superseded
    actor = ((last.get("actor") or {}).get("login") or "").strip()
    actor_bot = _is_bot(actor, (last.get("actor") or {}).get("type", ""))

    if actor_bot:
        print(
            f"::error::'{label}' was applied by '{actor}', a machine identity. The "
            "label is a HUMAN acknowledgement that guard machinery changed — an "
            "agent that can apply it is acknowledging its own change, which is the "
            "one thing this gate exists to prevent. Have a person apply it."
        )
        return 1

    if author and actor == author:
        if author_bot:
            print(
                f"::error::'{label}' was applied by the PR's own author "
                f"('{actor}'). A change cannot acknowledge itself."
            )
            return 1
        # Same human account for both. In the local-session topology the agent
        # drives `gh` with the human's credential, so GitHub genuinely cannot
        # separate the two — reporting this as a pass would overstate what was
        # verified, and failing it outright would brick every PR in such a repo.
        # Say what is and is not known; the PreToolUse hook is the guard here.
        print(
            f"::warning::'{label}' was applied by '{actor}', who also authored the "
            "PR. If this account is shared with an agent (a local session using "
            "your `gh` credential), this check cannot tell who applied it — the "
            "PreToolUse protected-label guard is what covers that case. Set "
            "STRICT_LABEL_IDENTITY=1 to require a distinct applier."
        )
        if strict:
            print(
                f"::error::STRICT_LABEL_IDENTITY=1: '{label}' must be applied by "
                f"an account other than the PR author ('{author}')."
            )
            return 1
        return 0

    print(f"OK: '{label}' applied by '{actor}' (not the PR author) — acknowledged.")
    return 0


def main():
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "")
    label = os.environ.get("REQUIRED_LABEL", "hooks-change")
    if not base or not head:
        print("BASE_SHA/HEAD_SHA not set — nothing to compare.")
        return 0

    diff = git("diff", "--name-only", f"{base}...{head}")
    if diff is None:
        print("::error::could not diff the PR range")
        return 1
    changed = [p.strip() for p in diff.splitlines() if p.strip()]

    globs = list(FLOOR)
    raw = git("show", f"{base}:{DELIVERY_FILE}")
    if raw:
        try:
            for g in (json.loads(raw).get("autonomy") or {}).get("riskPaths") or []:
                if isinstance(g, str) and g.strip() and g.strip() not in globs:
                    globs.append(g.strip())
        except Exception as exc:
            # The pipeline is configured but its config does not parse on the BASE
            # branch. Fail closed: we cannot enumerate what needs a human.
            print(f"::error::{DELIVERY_FILE} on the base branch does not parse ({exc})")
            return 1

    pats = [re.compile(glob_to_re(g)) for g in globs]
    hits = sorted({p for p in changed if any(rx.match(p) for rx in pats)})
    if not hits:
        print("No grader-path files changed — nothing to gate.")
        return 0

    print("This PR modifies grader paths (guard machinery / CI / pipeline config):")
    for h in hits:
        print(f"  {h}")
    print("Gated set: " + ", ".join(globs))
    if label not in os.environ.get("LABELS", "").split():
        print(
            f"::error::This PR changes grader paths ({', '.join(hits[:8])}) — the "
            "machinery that decides whether a change is acceptable. Those edits must "
            f"be reviewed, not silent. Add the '{label}' label to acknowledge, then "
            "this check re-runs."
        )
        return 1
    print(f"'{label}' label present — checking who applied it.")
    return check_label_identity(label)


# ── selftest ───────────────────────────────────────────────────────────────────
def _selftest():
    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got}, want {want}")
            print(f"[FAIL] {name}  (got {got}, want {want})")
        else:
            print(f"[PASS] {name}")

    def ev(actor, type_="User", label="hooks-change", event="labeled"):
        return {"event": event, "label": {"name": label},
                "actor": {"login": actor, "type": type_}}

    def run_identity(events, author="claude[bot]", author_type="Bot", strict=False):
        env = dict(os.environ)
        for k in ("_EVENTS_JSON", "PR_AUTHOR", "PR_AUTHOR_TYPE", "STRICT_LABEL_IDENTITY"):
            env.pop(k, None)
        os.environ["_EVENTS_JSON"] = json.dumps(events) if events is not None else "!bad"
        os.environ["PR_AUTHOR"] = author
        os.environ["PR_AUTHOR_TYPE"] = author_type
        os.environ["STRICT_LABEL_IDENTITY"] = "1" if strict else ""
        try:
            return check_label_identity("hooks-change")
        finally:
            os.environ.clear()
            os.environ.update(env)

    # glob floor: the graders the workflows run are gated alongside the workflows
    def gated(path):
        return any(re.compile(glob_to_re(g)).match(path) for g in FLOOR)

    expect("floor gates the hook", gated(".claude/hooks/pre-tool-use.py"), True)
    expect("floor gates the battery", gated(".claude/hooks/test_hooks.py"), True)
    expect("floor gates settings", gated(".claude/settings.json"), True)
    expect("floor gates workflows", gated(".github/workflows/ci.yml"), True)
    expect("floor gates THIS grader", gated("scripts/check_grader_paths.py"), True)
    expect("floor gates sibling graders", gated("scripts/check_auto_merge.py"), True)
    expect("floor leaves ordinary scripts alone", gated("scripts/telemetry_scrape.py"), False)
    expect("floor leaves docs alone", gated("docs/SECURITY.md"), False)
    expect("floor leaves src alone", gated("src/app.ts"), False)

    # identity: a machine may never supply the acknowledgement
    expect("bot applier rejected (by type)",
           run_identity([ev("claude", "Bot")]), 1)
    expect("bot applier rejected (by [bot] suffix)",
           run_identity([ev("github-actions[bot]", "")]), 1)
    expect("bot author self-labelling rejected",
           run_identity([ev("claude[bot]", "Bot")], author="claude[bot]"), 1)
    expect("human applier on a bot-authored PR accepted",
           run_identity([ev("braedensc", "User")], author="claude[bot]"), 0)
    expect("human applier != human author accepted",
           run_identity([ev("reviewer", "User")], author="someone", author_type="User"), 0)
    # shared-credential topology: unknowable, so warn-and-pass by default…
    expect("same human account warns but passes",
           run_identity([ev("solo", "User")], author="solo", author_type="User"), 0)
    # …and is a hard failure only when a repo opts in
    expect("same human account fails under STRICT",
           run_identity([ev("solo", "User")], author="solo", author_type="User",
                        strict=True), 1)
    # the CURRENT acknowledgement is the last one: a human re-apply clears a bot's
    expect("latest labelled event wins (human re-applied after a bot)",
           run_identity([ev("claude", "Bot"), ev("braedensc", "User")],
                        author="claude[bot]"), 0)
    expect("latest labelled event wins (bot re-applied after a human)",
           run_identity([ev("braedensc", "User"), ev("claude", "Bot")],
                        author="claude[bot]"), 1)
    # unattributable acknowledgements do not count
    expect("no labelled event rejected", run_identity([]), 1)
    expect("only an UNrelated label event rejected",
           run_identity([ev("braedensc", "User", label="bug")]), 1)
    expect("unfetchable events rejected (fail closed)", run_identity(None), 1)

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'OK'} "
          f"— grader-path selftest")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
