#!/usr/bin/env python3
"""Repository setup check: the labels and Actions variables the workflows rely on exist (KIT-692).

    python3 scripts/check_repo_setup.py                   # this repository (the one `gh` is in)
    python3 scripts/check_repo_setup.py --repo OWNER/NAME
    python3 scripts/check_repo_setup.py --selftest        # the battery CI runs

WHY. Some workflows assume repository state no step ever read. The `hooks-change` label has to
exist before a guarded PR can pass its check, and the alert workflows page the
`ALERT_PAGE_TO` variable (or fail their run when nobody can be paged). A repository without
them looks configured until the first guarded PR or the first alert. This lists what is
missing, by name, and prints the owner's command for each.

READS GITHUB ONLY. It makes GET calls through `gh api` and creates nothing. The commands it
prints are the owner's to run. Reading Actions variables needs a token with admin rights on
the repository; without one this says COULD NOT CHECK and never reports a pass.

THE DATA is the two tables below, one place: each label with the colour and words its create
command uses, each variable with who needs it. A page-recipient variable is needed only when
the repository owner is an Organization (an @mention of an organization notifies nobody);
when the owner is a person, an unset variable is reported as fine, not as missing. The selftest
checks that each name still appears in the file that relies on it, so the tables cannot rot.

Exit 0 = nothing missing. Exit 1 = something is missing (each named, with its command).
Exit 2 = could not check (gh missing, not signed in, or no admin rights to read variables).
"""
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# label -> (colour, description, the file that relies on it)
LABELS = {
    "hooks-change": ("D93F0B", "Acknowledges a PR that intentionally edits the Claude hook guard machinery",
                     ".github/workflows/hooks-change-guard.yml"),
    "conflict": ("d93f0b", "Merge conflicts with main (auto-managed by pr-conflict-monitor)",
                 "scripts/pr_conflict.py"),
}

# variable -> (what it is for, the file that reads it). Both are page recipients.
PAGE_VARIABLES = {
    "ALERT_PAGE_TO": ("who the alert workflows page", "templates/workflows/pipeline-failure-alert.yml"),
    "PR_CONFLICT_PAGE_TO": ("who the PR conflict monitor pages", ".github/workflows/pr-conflict-monitor.yml"),
}


class Reader:
    """Reads GitHub with `gh api` GETs. Each method returns None when it could not read."""

    def __init__(self, repo):
        self.repo = repo

    def _get(self, path, jq):
        out = subprocess.run(["gh", "api", "--paginate", path, "--jq", jq], capture_output=True, text=True)
        return out.stdout if out.returncode == 0 else None

    def labels(self):
        text = self._get("repos/%s/labels?per_page=100" % self.repo, ".[].name")
        return None if text is None else set(text.split("\n")) - {""}

    def variables(self):
        text = self._get("repos/%s/actions/variables?per_page=30" % self.repo, ".variables[].name")
        return None if text is None else set(text.split("\n")) - {""}

    def org_variables(self):
        """Variables the organization shares with this repository (the `vars` context reads them too)."""
        text = self._get("repos/%s/actions/organization-variables?per_page=30" % self.repo, ".variables[].name")
        return None if text is None else set(text.split("\n")) - {""}

    def owner_type(self):
        text = self._get("repos/%s" % self.repo, ".owner.type")
        return None if text is None else text.strip()


def judge(reader):
    """(exit code, lines) from whatever the reader returns. Variables are read only when the owner
    is an organization, the one case where one is required; there a name counts as present at the
    repository level or at the organization level, and an unreadable level is could-not-check."""
    labels, owner = reader.labels(), reader.owner_type()
    unread = [n for n, v in (("labels", labels), ("who owns the repository", owner)) if v is None]
    variables, org_vars = set(), set()
    if owner == "Organization":
        variables, org_vars = reader.variables(), reader.org_variables()
        unread += [n for n, v in (("Actions variables (needs admin rights)", variables),
                                  ("the organization's Actions variables", org_vars)) if v is None]
    if unread:
        return 2, ["COULD NOT CHECK: could not read %s; this is not a pass" % ", ".join(unread)]
    have = {l.lower() for l in labels}              # GitHub label names are case-insensitive
    lines, missing = [], False
    for name, (colour, why, _where) in sorted(LABELS.items()):
        if name.lower() not in have:
            missing = True
            lines.append("MISSING label %s\n  create: gh label create %s --color %s --description %s"
                         % (name, name, colour, shlex.quote(why)))
    for name, (what, _where) in sorted(PAGE_VARIABLES.items()):
        if owner == "Organization":
            if name not in variables and name not in org_vars:
                missing = True
                lines.append("MISSING variable %s (%s; the owner is an organization, which an @mention does not notify)\n"
                             "  create: gh variable set %s --body <login[,login]>   (or set it once for the organization)"
                             % (name, what, name))
        else:
            lines.append("not required: variable %s (%s): the owner is a person and is paged instead" % (name, what))
    if not missing:
        lines.append("OK: every label and required variable is present")
    return (1 if missing else 0), lines


def default_repo():
    out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def main(argv):
    if "--selftest" in argv:
        return selftest()
    repo = argv[argv.index("--repo") + 1] if "--repo" in argv and argv.index("--repo") + 1 < len(argv) else None
    try:
        repo = repo or default_repo()
    except OSError as e:
        print("COULD NOT CHECK: gh did not run (%s); this is not a pass" % e)
        return 2
    if not repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        print("COULD NOT CHECK: no repository named and none found from gh; this is not a pass")
        return 2
    try:
        code, lines = judge(Reader(repo))
    except OSError as e:
        print("COULD NOT CHECK: gh did not run (%s); this is not a pass" % e)
        return 2
    print("\n".join(lines))
    return code


class Stub:
    """A stand-in GitHub: what each read returns (None = could not read)."""

    def __init__(self, labels, variables, owner, org=()):
        self._l, self._v, self._o, self._org = labels, variables, owner, (None if org is None else set(org))

    def labels(self):
        return self._l

    def variables(self):
        return self._v

    def org_variables(self):
        return self._org

    def owner_type(self):
        return self._o


def selftest():
    failures = []

    def expect(name, got, want):
        ok = got == want
        print("[%s] %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            failures.append(name)

    all_labels = set(LABELS)
    all_vars = set(PAGE_VARIABLES)
    code, lines = judge(Stub(all_labels, all_vars, "Organization"))
    expect("all present passes", (code, len(lines)), (0, 1))
    code, lines = judge(Stub(all_labels - {"hooks-change"}, all_vars, "User"))
    text = "\n".join(lines)
    expect("a missing label fails, named, with the owner's command",
           (code, "MISSING label hooks-change" in text, "gh label create hooks-change --color D93F0B" in text), (1, True, True))
    code, lines = judge(Stub(all_labels, all_vars - {"ALERT_PAGE_TO"}, "Organization"))
    text = "\n".join(lines)
    expect("a missing page variable on an org-owned repo fails, named",
           (code, "MISSING variable ALERT_PAGE_TO" in text, "gh variable set ALERT_PAGE_TO" in text), (1, True, True))
    code, lines = judge(Stub(all_labels, set(), "User"))
    expect("an unset page variable on a person-owned repo is fine, and said so",
           (code, any("not required: variable ALERT_PAGE_TO" in l for l in lines)), (0, True))
    code, lines = judge(Stub(set(), set(), "Organization"))
    expect("everything missing lists every name", (code, sum(l.startswith("MISSING") for l in lines)),
           (1, len(LABELS) + len(PAGE_VARIABLES)))
    for label, stub in (("labels", Stub(None, all_vars, "User")), ("variables", Stub(all_labels, None, "Organization")),
                        ("organization variables", Stub(all_labels, all_vars, "Organization", org=None)),
                        ("owner", Stub(all_labels, all_vars, None))):
        code, lines = judge(stub)
        expect("an unreadable %s is could-not-check, never a pass" % label,
               (code, lines[0].startswith("COULD NOT CHECK")), (2, True))
    # the review of KIT-692
    code, lines = judge(Stub(all_labels, set(), "Organization", org=all_vars))
    expect("a page variable set only at organization level counts as present", (code, len(lines)), (0, 1))
    code, lines = judge(Stub(all_labels, {"ALERT_PAGE_TO"}, "Organization", org={"PR_CONFLICT_PAGE_TO"}))
    expect("one at each level, both present", code, 0)
    code, lines = judge(Stub(all_labels, None, "User"))
    expect("a person-owned repo is judged without reading variables (no admin rights needed)", code, 0)
    code, lines = judge(Stub({"Hooks-Change", "CONFLICT"}, all_vars, "User"))
    expect("label names compare case-insensitively", (code, len(lines)), (0, len(PAGE_VARIABLES) + 1))
    # the data cannot rot: each name is still in the file that relies on it
    for name, (_c, _d, where) in LABELS.items():
        with open(os.path.join(ROOT, where), encoding="utf-8") as f:
            expect("label %s is still named in %s" % (name, where), name in f.read(), True)
    for name, (_w, where) in PAGE_VARIABLES.items():
        with open(os.path.join(ROOT, where), encoding="utf-8") as f:
            expect("variable %s is still read in %s" % (name, where), name in f.read(), True)
    # it reads and never writes: every gh call it builds is a GET (no -X, no -f/-F, no `label create`)
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        src = f.read()
    calls = re.findall(r'\["gh", "[a-z]+"[^\]]*\]', src.split("def selftest")[0])
    expect("every gh call it makes is a read", all(("-X" not in c and "create" not in c and "-f" not in c) for c in calls), True)
    if failures:
        print("%d FAILED" % len(failures))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
