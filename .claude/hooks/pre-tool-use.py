#!/usr/bin/env python3
"""
PreToolUse security hook — layer 1 of 3 (see docs/SECURITY.md).
Runs before every Claude Code tool call. Exit 0 = allow. Exit 2 = block
(stdout is shown to Claude + the user as the reason). Unlike git hooks,
the model cannot bypass these — there is no --no-verify equivalent.

Distilled from todoclaw's .claude/hooks/pre-tool-use.py v2 — in production
2026-06-23 → 2026-07-03 across a full build (Stages 0–6). The v2 hardening
(prose-stripping, branch-scoped push guard) shipped post-retro on 2026-07-03.
Every guard here is verified by the block/allow battery in test_hooks.py,
which runs in CI.

BOOTSTRAP ORDER WARNING: settings.json hook wiring hot-loads the moment the
file is written, and a missing hook script BLOCKS EVERY TOOL CALL (python
exits 2 = the block signal — the system fails closed). Create the scripts in
.claude/hooks/ FIRST, write settings.json LAST. (Learned the hard way
building this kit — see docs/LESSONS.md.)

Layout:
  1. UNIVERSAL GUARDS — keep these in every project.
  2. STACK-SPECIFIC GUARDS — Supabase/Postgres examples at the bottom;
     replace them for your datastore, keep the *shape* (protect remote,
     allow local).
"""
import json
import os
import re
import shutil
import subprocess
import sys


def block(reason: str) -> None:
    print(f"[Security Hook] BLOCKED: {reason}")
    sys.exit(2)


try:
    data = json.load(sys.stdin)
except Exception:
    # Fail-open on malformed input: a broken harness payload must not brick
    # every tool call. The battery asserts this behavior (case "garbage stdin").
    sys.exit(0)

tool = data.get("tool_name", "")
inp = data.get("tool_input", {})


# ════════════════════════════════════════════════════════════════════════════
# 1. UNIVERSAL GUARDS — keep in every project
# ════════════════════════════════════════════════════════════════════════════

# ── Branch guard: no edits or commits while on main ─────────────────────────
# Enforces the feature-branch workflow automatically (see docs/COLLABORATION.md).
# Edit/Write and `git commit` are blocked whenever this repo is on a protected
# branch, so starting new work *forces* a branch first. This is what keeps main
# clean and conflict-free when several people (or agents) share the repo.
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
PROTECTED_BRANCHES = {"main", "master"}
BRANCH_HELP = (
    "You're on `{branch}` in this repo, where direct edits/commits are "
    "blocked (docs/COLLABORATION.md). Create a feature branch first, then retry:\n"
    "  git checkout -b <type>/<short-kebab-desc>\n"
    "  (type = feat | fix | chore | refactor | docs; e.g. feat/grid-drag)\n"
    "Pull latest main before branching if collaborators are active: "
    "git checkout main && git pull && git checkout -b <type>/<desc>"
)


# ── Branch-naming guard: work only happens on a properly-named branch ──────────
# docs/COLLABORATION.md's convention: <type>/<short-kebab-desc>, type in
# feat|fix|chore|refactor|docs. A fresh Claude Code worktree session defaults to
# an auto-generated `claude/<random-codename>` branch (e.g. claude/cool-jones-ca5); in
# todoclaw one landed UNRENAMED in a real PR. Blocking Edit/Write/commit the same
# way the main/master guard does forces a rename before any work, not just a
# reminder. (Fails open on an empty branch string, e.g. outside a repo.)
BRANCH_NAME_RE = re.compile(r"^(feat|fix|chore|refactor|docs)/[a-z0-9][a-z0-9-]*$")
BRANCH_NAME_HELP = (
    "Branch `{branch}` doesn't match this repo's naming convention "
    "(`<type>/<short-kebab-desc>`, type = feat|fix|chore|refactor|docs — see "
    "docs/COLLABORATION.md). Rename it before continuing, so an auto-generated "
    "worktree codename never lands in a real PR:\n"
    "  git branch -m <type>/<short-kebab-desc>"
)


def _current_branch() -> str:
    try:
        r = subprocess.run(
            ["git", "-C", PROJECT_ROOT, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _in_project(path: str) -> bool:
    if not path:
        return False
    try:
        return (
            os.path.commonpath([os.path.abspath(path), PROJECT_ROOT]) == PROJECT_ROOT
        )
    except Exception:
        return False


# ── Cross-worktree write guard: never write into a DIFFERENT checkout ───────────
# The branch guards above only fire for paths INSIDE this worktree (_in_project).
# A write whose path belongs to a SIBLING/PARENT worktree — classically the main
# checkout (on `main`), reached via a persisted `cd` into it — skips every guard and
# lands there SILENTLY: tests/typecheck here still pass against the unmodified files,
# so a whole session's edits can go to the wrong checkout unnoticed (todoclaw PR #77,
# 2026-07-03 retro). Resolve the target's OWNING worktree via `git worktree list` (the
# most-specific/longest root that contains it); if that isn't THIS session's worktree,
# block. Fails open (no git / not a worktree → owner None → allow), and same-worktree
# writes are untouched (owner == PROJECT_ROOT), so paths outside the repo (scratchpad,
# ~/.claude memory, /tmp) and normal edits are unaffected — the guard cannot lock the
# session out of its own worktree.
CROSS_WORKTREE_HELP = (
    "Cross-worktree write blocked — this path is in a DIFFERENT checkout than your session:\n"
    "  target worktree: {owner}\n"
    "  your session:    {here}\n"
    "Writing into another worktree (especially the MAIN checkout, usually on `main`) lands "
    "there SILENTLY: the branch guard only protects your own worktree, and your tests/typecheck "
    "would still pass against the unmodified files here. Use your OWN worktree's path instead:\n"
    "  {suggested}\n"
    "(Usual cause: a persisted `cd` into another checkout — prefer absolute worktree paths and "
    "`git -C <dir>` over `cd`. If you genuinely must edit the other worktree, do it from a "
    "session rooted there.)"
)


def _worktree_roots():
    """Absolute roots of every git worktree for this repo, or [] on any failure."""
    try:
        r = subprocess.run(
            ["git", "-C", PROJECT_ROOT, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode != 0:
            return []
        return [
            os.path.abspath(line[len("worktree ") :].strip())
            for line in r.stdout.splitlines()
            if line.startswith("worktree ")
        ]
    except Exception:
        return []


def _owning_worktree(path: str, roots):
    """The most-specific (longest) worktree root that contains `path`, or None."""
    try:
        ap = os.path.abspath(path)
    except Exception:
        return None
    best = None
    for root in roots:
        try:
            if os.path.commonpath([ap, root]) == root and (
                best is None or len(root) > len(best)
            ):
                best = root
        except Exception:
            continue
    return best


# ── Merged-PR guard: no commits/pushes on a branch whose PR already merged ──────
# A branch pushed with more work after its PR merges is silently stranded: GitHub
# stops syncing that PR's head and stops running CI on further pushes to the
# branch (burned real debugging time in todoclaw before "PR merged" was recognized
# as the cause — ported from todoclaw PR #61, 2026-07-03). Only fires once the
# branch has an upstream (skips fresh local-only branches, avoiding a network
# call), and fails open on any gh/network error — never block on something this
# can't verify.
MERGED_PR_HELP = (
    "`{branch}`'s PR (#{number}) is already MERGED. Commits/pushes here would be "
    "silently stranded — GitHub stops syncing a merged PR's head and stops "
    "running CI on further pushes to that branch. Branch fresh off updated main "
    "instead:\n"
    "  git checkout main && git pull --ff-only && git checkout -b <type>/<desc>"
)


def _has_upstream() -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", PROJECT_ROOT, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def _merged_pr_info(branch: str):
    """Returns {"number": ...} if `branch` has a MERGED PR, else None. Fails open."""
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "state,number"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return None
        info = json.loads(r.stdout)
        return info if info.get("state") == "MERGED" else None
    except Exception:
        return None


# ── Self-protection guard: Claude must never edit the hooks that guard it ───────
# Every block above is trivially defeated if Claude can rewrite the hook to delete the
# guard, or edit settings.json to unwire it. So these files are HUMAN-ONLY: the
# Edit/Write tools are blocked outright, and Bash that would mutate them (redirect,
# tee, sed -i, cp/mv/rm, git checkout/restore, an inline `-c`/`-e` interpreter, …) is
# blocked too. To change one, Claude must hand the HUMAN a terminal command to run —
# it cannot apply the change itself. The running hook protects itself (pre-tool-use.py
# is in its own set). This is a first line, not a sandbox: a shell can't be perfectly
# fenced by regex, so the real backstop stays git — any change must survive a reviewed
# PR + CI, which re-runs the battery against the committed hook. (Reads are allowed:
# Claude may `Read`/`cat` these files freely; only writes/mutations are blocked.)
SELF_PROTECTED = {
    os.path.join(PROJECT_ROOT, ".claude", "hooks", "pre-tool-use.py"),
    os.path.join(PROJECT_ROOT, ".claude", "hooks", "audit.py"),
    os.path.join(PROJECT_ROOT, ".claude", "hooks", "stop-pr-check.py"),
    os.path.join(PROJECT_ROOT, ".claude", "settings.json"),
}
SELF_PROTECT_HELP = (
    "🔒 `{path}` is a protected hook file — Claude may not edit it. Editing the "
    "guardrails would let any block be removed, so these files are HUMAN-ONLY. Do not "
    "reach for another tool or a shell workaround — instead, print a terminal command "
    "for the human to run themselves, e.g.:\n"
    "  cat > {path} <<'EOF'\n"
    "  <full new file contents>\n"
    "  EOF\n"
    "and let them run it. (The change still lands via a reviewed PR — CI re-runs the "
    "hook battery against it.)"
)
SELF_PROTECT_BASH_HELP = (
    "🔒 That command would modify a protected hook file (one of: pre-tool-use.py, "
    "audit.py, stop-pr-check.py, .claude/settings.json). These are HUMAN-ONLY — Claude "
    "cannot edit or overwrite the guards that constrain it. Print the change as a "
    "terminal command for the human to run themselves instead; it lands via a reviewed "
    "PR. (Reading them — cat/less/grep — is fine.)"
)


def _is_self_protected(path: str) -> bool:
    if not path:
        return False
    try:
        return os.path.abspath(path) in {os.path.abspath(p) for p in SELF_PROTECTED}
    except Exception:
        return False


# Bash detection: a write/mutation operator TARGETING a protected file — the path
# must be the operator's target, so an unrelated `2>&1` / `> /dev/null` / `rm other`
# in a command that merely mentions a hook path is NOT a false positive. Read-only
# commands (cat/grep, `python3 <dir>/test_hooks.py`, `python3 -m py_compile <path>`,
# `cat <hook> > /tmp/x`) do not match, so verifying the hooks stays frictionless.
_SELF_PROT = r"(?:pre-tool-use\.py|stop-pr-check\.py|audit\.py|\.claude[/\\]settings\.json)"
_SELF_MUTATE_RE = re.compile(
    r">>?\s*['\"]?[^\s'\"|&;<>]*?" + _SELF_PROT +                     # redirect INTO a protected path
    r"|\btee\b[^|;&]*?" + _SELF_PROT +                                # tee protected
    r"|\b(?:sed|perl)\b[^|;&]*\s-[a-zA-Z]*i\b[^|;&]*?" + _SELF_PROT + # sed -i / perl -i protected
    r"|\b(?:cp|mv|rm|ln|install|truncate|dd|shred|unlink)\b[^|;&]*?" + _SELF_PROT +  # cmd -> protected
    r"|\bgit\b[^|;&]*\b(?:checkout|restore)\b[^|;&]*?" + _SELF_PROT + # git revert protected
    r"|\b(?:python3?|node|deno|ruby)\b[^|;&]*\s-[a-zA-Z]*[ce]\b[^|;&]*?" + _SELF_PROT  # inline interpreter writing protected
)


# Self-protection FIRST — highest-priority block, so its message wins. Claude may not
# edit the hook files or settings.json themselves; changing them is a human-only
# terminal step (see SELF_PROTECTED). Covers NotebookEdit too, for completeness.
if tool in ("Edit", "Write", "NotebookEdit"):
    _spp = inp.get("file_path", "") or inp.get("notebook_path", "")
    if _is_self_protected(_spp):
        block(SELF_PROTECT_HELP.format(path=os.path.abspath(_spp)))

# Cross-worktree guard runs for ALL Edit/Write (not just in-project), and BEFORE
# the branch guard — a write into another checkout must be caught even though it is
# outside PROJECT_ROOT.
if tool in ("Edit", "Write"):
    _fp = inp.get("file_path", "")
    _owner = _owning_worktree(_fp, _worktree_roots()) if _fp else None
    if _owner and os.path.abspath(_owner) != os.path.abspath(PROJECT_ROOT):
        try:
            _suggested = os.path.join(
                PROJECT_ROOT, os.path.relpath(os.path.abspath(_fp), _owner)
            )
        except Exception:
            _suggested = os.path.join(PROJECT_ROOT, "<same-relative-path>")
        block(CROSS_WORKTREE_HELP.format(owner=_owner, here=PROJECT_ROOT, suggested=_suggested))

if tool in ("Edit", "Write") and _in_project(inp.get("file_path", "")):
    branch = _current_branch()
    if branch in PROTECTED_BRANCHES:
        block(BRANCH_HELP.format(branch=branch))
    elif branch and not BRANCH_NAME_RE.match(branch):
        block(BRANCH_NAME_HELP.format(branch=branch))

if tool == "Bash" and re.search(r"\bgit\s+commit\b", inp.get("command", "")):
    branch = _current_branch()
    if branch in PROTECTED_BRANCHES:
        block(BRANCH_HELP.format(branch=branch))
    elif branch and not BRANCH_NAME_RE.match(branch):
        block(BRANCH_NAME_HELP.format(branch=branch))
    elif _has_upstream():
        merged = _merged_pr_info(branch)
        if merged:
            block(MERGED_PR_HELP.format(branch=branch, number=merged["number"]))


# ── Bash ──────────────────────────────────────────────────────────────────────
def _strip_prose(c: str) -> str:
    """v2 (todoclaw retro 2026-07-03): guards must match OPERATIONS, not PROSE.

    Commit messages and PR titles/bodies passed inline (-m "drop stale rows")
    were false-positiving the destructive-verb patterns below. Strip quoted
    message payloads before scanning; long text via `git commit -F` /
    `--body-file` remains the norm. Message text is inert prose — it is never
    executed — so stripping it loses no protection.
    -[a-z]*m catches combined short flags too (git commit -am / -sm "msg").
    """
    return re.sub(
        r"(-[a-zA-Z]*m|--message|--title|--body|-t|-b)(\s+|=)(\"(?:[^\"\\]|\\.)*\"|'[^']*')",
        r"\1\2''",
        c,
    )


if tool == "Bash":
    cmd = inp.get("command", "")
    scan = _strip_prose(cmd)

    # Self-protection: block any shell mutation of a protected hook file. First guard
    # in the Bash block so its message wins.
    if _SELF_MUTATE_RE.search(scan):
        block(SELF_PROTECT_BASH_HELP)

    # Block rm -rf / rm -fr / rm --recursive
    if re.search(r"\brm\b[^#\n;&|]*-[a-zA-Z]*r[a-zA-Z]*f", scan) or \
       re.search(r"\brm\b[^#\n;&|]*-[a-zA-Z]*f[a-zA-Z]*r", scan) or \
       re.search(r"\brm\b[^#\n;&|]*--recursive", scan):
        block(
            "rm -rf / rm --recursive detected — use specific paths or ask the user to confirm."
        )

    # Block curl/wget piped directly to a shell
    if re.search(
        r"(curl|wget)\s[^|\n]*\|\s*(bash|sh|zsh|fish|python3?|ruby|perl)", scan
    ):
        block(
            "Piping curl/wget into a shell is a supply-chain risk. "
            "Download first, inspect, then run."
        )

    # Block staging reference dirs or real .env files.
    # `planning/` is this kit's default name for a gitignored reference-material
    # dir (licensed specs, exports, scratch notes). If your project names it
    # differently, update this pattern AND .gitignore AND .husky/pre-commit AND
    # the app CI's forbidden-paths grep together — every layer must agree.
    if re.search(r"\bgit\s+add\b[^#\n;&|]*(planning/|\.env(?!\.example))", scan):
        block(
            "Staging planning/ or .env files is forbidden — "
            "these paths are gitignored to prevent leaks."
        )

    # Push guard v2 (todoclaw retro 2026-07-03): protect main/master from ANY
    # push; elsewhere allow the safe `--force-with-lease` (refuses to clobber
    # unseen remote commits) but block bare `--force`/`-f`. GitHub branch
    # protection is the server-side backstop for anything this heuristic misses.
    _push = re.search(r"\bgit\s+push\b([^#\n;&|]*)", scan)
    if _push:
        _seg = _push.group(1)
        if re.search(r"[\s:](main|master)(?![\w./-])", _seg):
            block("Pushing to main/master is not allowed. Use a feature branch + PR.")
        if re.search(r"(^|\s)--force(?!-with-lease\b)\b", _seg) or re.search(
            r"(^|\s)-f\b", _seg
        ):
            block(
                "Bare --force/-f push is blocked — use `git push --force-with-lease`, "
                "which refuses to overwrite remote commits you haven't seen."
            )
        branch = _current_branch()
        if branch not in PROTECTED_BRANCHES and _has_upstream():
            merged = _merged_pr_info(branch)
            if merged:
                block(MERGED_PR_HELP.format(branch=branch, number=merged["number"]))

    # Merging a PR (with or without --auto) is the HUMAN's action only — Claude
    # opens PRs and stops there (todoclaw near-miss 2026-07-03: `gh pr merge
    # --auto` was briefly used on Claude-opened PRs before being corrected;
    # auto-merge still means the agent caused the merge). `--disable-auto` is
    # exempted since it only *undoes* an auto-merge, never causes one.
    _gh_merge = re.search(r"\bgh\s+pr\s+merge\b([^#\n;&|]*)", scan)
    if _gh_merge and "--disable-auto" not in _gh_merge.group(1):
        block(
            "`gh pr merge` (including --auto) is not allowed — merging PRs is "
            "the human's action only. Open the PR (`gh pr create`) and stop "
            "there. (`gh pr merge --disable-auto` is still allowed, to undo an "
            "auto-merge that shouldn't have been enabled.)"
        )

    # Block shell-reading secret files (cat, less, head, etc.).
    # The [^#\n;&|]* gap is scoped per shell command, so a .env named in a
    # LATER command on the same line (e.g. `cat foo; grep x .env`) is not a
    # false positive — while a real `cat .env` still blocks.
    if re.search(
        r"\b(cat|less|head|tail|bat|open|more)\b[^#\n;&|]*(\.env(?!\.example)|\.pem\b|\.key\b)",
        scan,
    ):
        block(
            "Reading secret files (.env, .pem, .key) via shell is not allowed. "
            "Reference by variable name only."
        )


# ── Read ──────────────────────────────────────────────────────────────────────
if tool == "Read":
    path = inp.get("file_path", "")
    basename = os.path.basename(path)

    if re.match(r"^\.env", basename) and not basename.endswith(".example"):
        block(
            f"Reading {basename} is blocked — it may contain real secrets. "
            "Reference env vars by name only."
        )
    if re.search(r"\.(pem|key)$", basename):
        block(f"Reading {basename} is blocked — private key files are off-limits.")


# ── Edit / Write ──────────────────────────────────────────────────────────────
if tool in ("Edit", "Write"):
    path = inp.get("file_path", "")
    basename = os.path.basename(path)

    # Block writing to real .env files
    if re.match(r"^\.env", basename) and not basename.endswith(".example"):
        block(
            f"Writing to {basename} is blocked. "
            "Only .env.example (with placeholder values) is committed."
        )

    # Block embedding secret values in any file content
    content = inp.get("new_string", "") or inp.get("content", "")
    SECRET_PATTERNS = [
        (r"sk-ant-[a-zA-Z0-9\-_]{20,}", "Anthropic API key (sk-ant-…)"),
        (r"(?:supabase|postgres)://[^:@\s]+:[^@\s]{8,}@", "DB connection string with password"),
        (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "Private key block"),
        (r"(?:AKID|AKIA)[A-Z0-9]{16}", "AWS access key"),
        (r"gh[pousr]_[A-Za-z0-9_]{36,}", "GitHub personal access token"),
        (r"eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}", "JWT token value"),
        # Extension point: add patterns for providers YOUR project uses, e.g.
        # OpenAI-style keys: (r"sk-[a-zA-Z0-9]{32,}", "OpenAI-style API key").
        # Add a battery case in test_hooks.py for every pattern you add.
    ]
    for pattern, label in SECRET_PATTERNS:
        if re.search(pattern, content):
            block(
                f"Secret value pattern detected in file content ({label}). "
                "Reference secrets by env var name only — never embed values."
            )


# ════════════════════════════════════════════════════════════════════════════
# 2. STACK-SPECIFIC GUARDS — Supabase / Postgres
#    Replace this section for your datastore. Keep the shape: the LOCAL
#    (disposable) instance stays unguarded so routine resets are frictionless;
#    the REMOTE (irreplaceable) instance gets hard blocks on destructive ops.
# ════════════════════════════════════════════════════════════════════════════
if tool == "Bash":
    scan = _strip_prose(inp.get("command", ""))

    # `supabase db reset` wipes the database. Local (Docker) is fine; --linked /
    # --db-url target a REMOTE db and would destroy it.
    if re.search(r"\bsupabase\b[^#\n]*\bdb\s+reset\b", scan) and \
       re.search(r"--linked\b|--db-url\b", scan):
        block(
            "`supabase db reset` against a linked/remote database wipes it. "
            "Only the local (Docker) reset is allowed; change prod via reviewed, "
            "reversible migrations."
        )

    # Deleting a hosted Supabase project is irreversible.
    if re.search(r"\bsupabase\b[^#\n]*\bprojects?\s+delete\b", scan):
        block("`supabase projects delete` is irreversible and is not allowed.")

    # Raw destructive SQL (DROP / TRUNCATE / DELETE) aimed at a NON-localhost
    # Postgres host — e.g. psql against a remote connection string. A postgres
    # URL whose host is not localhost/127.0.0.1 alongside a destructive verb
    # is blocked.
    if re.search(r"\b(drop|truncate|delete)\b", scan, re.IGNORECASE) and re.search(
        r"postgres(?:ql)?://[^\s'\"]*@(?!(?:localhost|127\.0\.0\.1|0\.0\.0\.0))",
        scan,
        re.IGNORECASE,
    ):
        block(
            "Destructive SQL (DROP/TRUNCATE/DELETE) against a remote database is "
            "blocked. Run destructive changes only on the local DB, via migrations."
        )


sys.exit(0)
