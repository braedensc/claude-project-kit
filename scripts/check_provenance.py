#!/usr/bin/env python3
"""Provenance leak check.

Scans TRACKED files (git ls-files) for three kinds of leak and exits non-zero
with a file:line list of every hit. Public template, private history: what this
kit was distilled from must not be inferable from what it ships.

(a) DENYLIST TERMS — case-insensitive substring match on names that must never
    appear (the private project a template was distilled from, an internal
    codename, a client). Both file CONTENTS and the file PATH itself are
    checked: a repo can leak a name purely through a filename or a directory,
    and this one did (an ADR whose slug carried the source app's name). The
    terms are NEVER stored in the repo: writing them into a checked-in denylist
    would BE the leak this rule prevents. They are read from OUTSIDE the tree,
    in precedence order:
      1. $PROVENANCE_DENYLIST — comma- and/or newline-separated (in CI: a repo
         variable, `vars.PROVENANCE_DENYLIST`);
      2. .provenance-denylist — a git-ignored file in the repo root, one term
         per line, `#` comments allowed.
    With neither present the rule is a NO-OP and says so: the shipped template
    forbids no words by default — every adopter names their own. Because an
    UNDEFINED repo variable renders as the empty string, that no-op is
    indistinguishable in a CI log from "configured and clean"; a repo that has
    set the variable should also set $PROVENANCE_REQUIRE_DENYLIST=1, which
    turns the no-op into a hard failure. Hits print the position with the term
    MASKED — in the path as well as the label, since the path is exactly where
    a leaked name likes to hide — so a public CI log never republishes the very
    words being suppressed (PROVENANCE_SHOW_TERMS=1 unmasks them locally).

(b) ABSOLUTE HOME PATHS — needs no config: an absolute macOS/Linux home path
    pins a real account name to a public repo. Anonymized and placeholder
    account segments pass — e.g. "/Users/<you>/repo" or the elided
    "/Users/.../repo" form .claude/hooks/README.md uses — as do the shell forms
    that name no account (${HOME}, ~) and the service accounts in
    GENERIC_ACCOUNTS (root, vscode, runner, …).

(c) EMAIL ADDRESSES — reserved-TLD and documentation addresses pass (e.g.
    dev@example.com, a fixture's user@host.invalid), as does the one real
    address this kit documents, the Co-Authored-By commit trailer.

Runs from anywhere: main() chdirs to the repo root first, so a subdirectory
invocation can't quietly scan a subtree (and miss a root denylist) while still
printing a confident OK.

Self-safe: this file is scanned like every other tracked file. It necessarily
spells out the shapes it hunts for, so each literal above is written in a form
its own allowlists clear — the examples in this docstring and in the constants
below double as the check's regression test.

Exit 0 = clean, 1 = leaks found (or an unreadable/required-but-missing denylist).
"""
import os
import re
import subprocess
import sys

DENYLIST_ENV = "PROVENANCE_DENYLIST"
DENYLIST_FILE = ".provenance-denylist"
REQUIRE_ENV = "PROVENANCE_REQUIRE_DENYLIST"
SHOW_ENV = "PROVENANCE_SHOW_TERMS"

# Absolute home paths. Case-sensitive on purpose: the macOS/Linux spelling is
# what matters, and a case-insensitive match would flag every REST route like
# ".../users/42". The lookbehind keeps a URL path (".../home/dashboard" inside a
# host) from matching, while a real path — quoted, spaced, or after "file://" —
# still does.
HOME_PATH = re.compile(r"(?<![A-Za-z0-9._~-])/(?:Users|home)/([^/\s\"'`)\]},;:]+)")

# Account segments that name no person. Extend this if a literal route or
# container path in your project trips the check.
GENERIC_ACCOUNTS = {
    "admin", "administrator", "app", "appuser", "ci", "claude", "codespace",
    "debian", "dev", "developer", "docker", "ec2-user", "git", "home",
    "linuxbrew", "me", "name", "node", "public", "root", "runner", "service",
    "shared", "someone", "ubuntu", "user", "username", "users", "vscode", "you",
    "yourname",
}
# A segment carrying any of these is a placeholder, not an account: <you>,
# ${USER}, a doubled-brace token, a printf slot.
PLACEHOLDER_CHARS = set("<>{}$*%|")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")
# The one real address the kit legitimately documents (the commit trailer).
ALLOWED_ADDRESSES = {"noreply@anthropic.com"}
# RFC 2606 / 6761 reserved and special-use — deliverable to nobody.
RESERVED_TLDS = {"example", "internal", "invalid", "local", "localhost", "test"}
DOC_DOMAINS = {"example.com", "example.net", "example.org"}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def load_denylist():
    """Return (terms, source). An unset or blank env var falls through to the file."""
    raw = os.environ.get(DENYLIST_ENV, "")
    source = "$" + DENYLIST_ENV
    if not raw.strip():
        if not os.path.exists(DENYLIST_FILE):
            return [], None
        try:
            raw = open(DENYLIST_FILE, encoding="utf-8").read()
        except OSError as e:
            print(f"FAIL: cannot read {DENYLIST_FILE}: {e}")
            sys.exit(1)
        source = DENYLIST_FILE
    # Comments are stripped per LINE before splitting on commas — a comment that
    # contains a comma must not smuggle its tail in as a term.
    terms, seen = [], set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for chunk in line.split(","):
            term = chunk.strip()
            if term and term.lower() not in seen:
                seen.add(term.lower())
                terms.append(term)
    return terms, (source if terms else None)


def showing_terms():
    return os.environ.get(SHOW_ENV) == "1"


def redactor(terms):
    """Build the masker used on EVERY hit line.

    A hit line carries a path, and a path is a place a denylisted name lives —
    printing it verbatim in a public CI log would republish the word the rule
    exists to suppress. One alternation (longest term first, so the longest
    match wins) replaces each occurrence with its `<term #N>` position in a
    single pass, so a replacement can never itself be re-matched.
    """
    if showing_terms() or not terms:
        return lambda text: text
    position = {t.lower(): i for i, t in enumerate(terms, 1)}
    pattern = re.compile(
        "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True) if t),
        re.IGNORECASE,
    )
    return lambda text: pattern.sub(lambda m: f"<term #{position[m.group(0).lower()]}>", text)


def is_placeholder_account(seg):
    seg = seg.rstrip(".,;:!?*")
    if not seg or seg.strip(".") == "" or seg == "~":
        return True
    if any(c in PLACEHOLDER_CHARS for c in seg):
        return True
    if re.fullmatch(r"[A-Z0-9_]+", seg):  # YOUR_NAME, USER
        return True
    return seg.lower() in GENERIC_ACCOUNTS


def is_allowed_address(addr):
    low = addr.lower()
    if low in ALLOWED_ADDRESSES:
        return True
    local, _, domain = low.partition("@")
    if any(c in local for c in PLACEHOLDER_CHARS):
        return True
    if domain in DOC_DOMAINS or any(domain.endswith("." + d) for d in DOC_DOMAINS):
        return True
    return domain.rsplit(".", 1)[-1] in RESERVED_TLDS


def scan(path, terms, hits, redact):
    show = showing_terms()
    lowered = [t.lower() for t in terms]
    safe_path = redact(path)

    # The PATH itself first — a file named after a private project leaks it
    # whether or not a single byte inside the file does.
    low_path = path.lower()
    for i, term in enumerate(lowered, 1):
        if term in low_path:
            label = terms[i - 1] if show else f"term #{i}"
            hits["terms"].append(f"{safe_path}  (in the PATH, {label})")

    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except (OSError, IsADirectoryError):
        return
    for n, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for i, term in enumerate(lowered, 1):
            if term in low:
                label = terms[i - 1] if show else f"term #{i}"
                hits["terms"].append(f"{safe_path}:{n}  ({label})")
        for seg in HOME_PATH.findall(line):
            if not is_placeholder_account(seg):
                account = redact(seg.rstrip(".,;:!?*"))
                hits["paths"].append(f"{safe_path}:{n}  (account '{account}')")
        for addr in EMAIL.findall(line):
            if not is_allowed_address(addr):
                hits["emails"].append(f"{safe_path}:{n}  ({redact(addr)})")


def report(title, lines, fix):
    print(f"FAIL: {title}:")
    for line in lines:
        print(f"  {line}")
    print(f"  -> {fix}")


def main():
    # Both the denylist-file lookup and `git ls-files` are relative to the cwd:
    # from a subdirectory they would scan only that subtree, miss a repo-root
    # denylist, and still print "OK". Anchor at the repo root instead.
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if top.returncode != 0:
        print("FAIL: not inside a git repository — this check scans tracked files.")
        return 1
    os.chdir(top.stdout.strip())

    terms, source = load_denylist()
    redact = redactor(terms)
    hits = {"terms": [], "paths": [], "emails": []}
    files = tracked_files()
    for f in files:
        scan(f, terms, hits, redact)

    # An undefined GitHub repo variable renders EMPTY, so an unconfigured term
    # rule is green and silent — forever, and indistinguishable from a clean
    # scan. A repo that has configured one opts into this strict mode so the
    # difference is loud.
    misconfigured = source is None and os.environ.get(REQUIRE_ENV) == "1"
    if source is None:
        if misconfigured:
            report(
                f"no denylist terms configured, but {REQUIRE_ENV}=1 requires them",
                [f"${DENYLIST_ENV} is empty or unset and {DENYLIST_FILE} is absent — "
                 "rule (a) would be a silent NO-OP"],
                f"set the repo variable {DENYLIST_ENV} (GitHub → Settings → Secrets and "
                f"variables → Actions → Variables) or create {DENYLIST_FILE} locally; "
                f"unset {REQUIRE_ENV} only if this repo truly forbids no names.",
            )
        else:
            print(
                f"note: no denylist terms configured — rule (a) is a NO-OP. Set ${DENYLIST_ENV} "
                f"(in CI: a repo variable) or create {DENYLIST_FILE} (git-ignored) to name the "
                f"words this repo must never contain, and set {REQUIRE_ENV}=1 so an empty list "
                "fails loudly instead of passing silently. The path and email rules always run."
            )

    if hits["terms"]:
        report(
            f"denylisted names in tracked files ({len(terms)} term(s) from {source})",
            hits["terms"],
            f"remove or reword each line, and rename each flagged path; {SHOW_ENV}=1 "
            "locally reveals which term matched.",
        )
    if hits["paths"]:
        report(
            "absolute home paths leak a real account name",
            hits["paths"],
            "use ~, ${HOME}, or an anonymized segment such as /Users/<you>/repo instead.",
        )
    if hits["emails"]:
        report(
            "real-looking email addresses",
            hits["emails"],
            "use an @example.com or .invalid address (or allowlist a deliberate one in ALLOWED_ADDRESSES).",
        )
    if any(hits.values()) or misconfigured:
        return 1

    if source is None:
        print(f"OK: no provenance leaks in {len(files)} tracked files (denylist empty)")
    else:
        print(f"OK: no provenance leaks in {len(files)} tracked files "
              f"({len(terms)} denylist term(s) from {source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
