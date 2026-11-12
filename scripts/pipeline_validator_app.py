#!/usr/bin/env python3
"""The validator's own GitHub app, and its check made a required one: the owner's script (KIT-541).
Design: docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md, "The ruleset
changes". Guide: docs/VALIDATOR-OPERATOR.md, "The check on the pull request".

    python3 scripts/pipeline_validator_app.py run --repo OWNER/NAME --key-file ~/.validator/app.pem
                                                   [--name NAME] [--org ORG] [--ruleset ID]
    python3 scripts/pipeline_validator_app.py verify --repo OWNER/NAME
    python3 scripts/pipeline_validator_app.py merges on|off --repo OWNER/NAME
                                   [--settings-file ~/.validator/settings.json] [--ruleset ID]
    python3 scripts/pipeline_validator_app.py --selftest

WHY. With no agent allowed to approve a pull request, the ruleset's "one approving review" can't be
met once you step out. The validator's verdict takes its place: the `Validator` check
(scripts/pipeline_validator_check.py), required by the ruleset, with the validator's own app named
as its only source, so nothing else can post a check by that name.

Run it in a terminal, as yourself. It checks what is done, shows what it will change in plain
words, asks for one typed `yes`, does it, and checks again. A second run changes nothing.

WHAT `run` DOES, IN ORDER, skipping what is done
  1. Opens GitHub's page to create the app. You click GitHub's own Create button. The app may write
     check runs, and nothing else.
  2. Writes its private key to the file you name with --key-file: mode 600, a file that must not
     exist yet, outside every repository. GitHub hands the key over once. It is never printed,
     never an argument, and never kept anywhere else.
  3. Opens GitHub's page to install the app, and waits until GitHub shows it on the repository.
  4. Changes the repository's ruleset for its default branch, with your own `gh` login: the
     `Validator` check is required, with this app as its source, and the pull request rule needs 0
     approving reviews instead of 1. It reads the ruleset back. If GitHub does not keep the app as
     the check's source, it puts the ruleset back as it was and says so.

WHAT `merges on --repo OWNER/NAME` DOES (KIT-605): auto-merge for one project, after `run`. It
checks, read-only: the app installed there; the default branch's ruleset; the repository's
auto-merge and squash settings; the validator's settings, through their own `load()`. It warns
when nothing would merge yet (no recent decision in the validator's ledger, or no Stage E login),
and still asks. After your yes, in this order, each read back:
  1. The ruleset: `Validator` required from the validator's app only, branches up to date, no
     approving review, and the repository admin role the only bypass, for pull requests only.
  2. The repository: auto-merge and squash merging allowed.
  3. Last, the switch: `approve.merges` on and the repository in `merge_repos`, written by this
     script as the hold's `clear` writes the hold. A failure part way leaves the switch off.
`merges off` undoes it in the safe order: the repository off the list first; then auto-merge off
on each open pull request there; then the ruleset parts `on` sets back as `on` found them, from the
ledger. The ruleset waits while a pull request could still merge on its own. A default branch with
classic branch protection only is said, and nothing changes.

THE LEDGER is ~/.validator-app/state.json under your home, mode 600: the app's id and slug, the key
file's path, where the app is installed, and for each repository `merges on` changed, the ruleset
as it found it. Fields only, never a key or a token. The validator's job reads the app's id from
it.

WHAT IT NEVER DOES. Run under a model: `run`, `verify` and `merges` are refused in an agent's
environment before they read anything. Print, log or store a key or a token anywhere but the key
file. Change a ruleset, a repository's merge settings or your settings file without your yes, or
one it was not asked about. Merge, approve or label anything.

Exit: 0 done, or every piece holds · 1 a piece does not hold, or a step failed after your yes · 2
the command line needs fixing; nothing changed · 3 refused (an agent's environment, or root);
nothing changed · 4 something could not be checked; nothing changed · 10 you did not type yes;
nothing changed.
"""
import argparse
import calendar
import copy
import json
import os
import re
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# One definition each: the agent-env markers, the helper's calls, Stage E's owner-script pieces
# (its IO seam, its signer, its read-only `gh` call, its app pages), and the check's name.
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
import pipeline_github_app as gha  # noqa: E402
import pipeline_stage_e_app as stage_e_app  # noqa: E402
from pipeline_validator_check import CHECK_NAME  # noqa: E402
import pipeline_validator_ledger as vledger  # noqa: E402
import pipeline_validator_settings as vsettings  # noqa: E402

SCHEMA = "validator-app/1"
LEDGER = "~/.validator-app/state.json"
PERMISSIONS = {"checks": "write"}
DESCRIPTION = "The validator's verdict, as a check run. It writes check runs, and nothing else."
NAME_MAX = 34
WAIT_SECONDS = 600
POLL_SECONDS = 5
DONE, TODO, BLOCKED, UNKNOWN = "DONE", "TODO", "BLOCKED", "UNKNOWN"
PIECES = ("app", "key file", "installed", "required check", "approving reviews")
EXIT_OK, EXIT_HOLD, EXIT_USAGE, EXIT_REFUSED, EXIT_UNCHECKED, EXIT_NOT_YES = 0, 1, 2, 3, 4, 10
RULESET_PATH_RE = re.compile(r"repos/[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}/rulesets/[0-9]{1,12}")
StepFailed = stage_e_app.StepFailed


class IO(stage_e_app.IO):
    """Stage E's owner-script seam, with this script's own ledger."""

    def ledger_path(self):
        return os.path.expanduser(LEDGER)


def refused(io):
    env = io.env()
    if any(m in env for m in AGENT_ENV_MARKERS):
        return "an agent's environment: this script is yours to run, in your own terminal"
    if io.uid() == 0:
        return "root: run it as yourself"
    return None


# --------------------------------------------------------------------------- #
# The ledger and the key file
# --------------------------------------------------------------------------- #
def read_ledger(path):
    """(doc, None) for the ledger ({} when there is none), or (None, why) when it is not one."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError):
        return None, "%s could not be read as this script's ledger" % path
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None, "%s is not this script's ledger (%s)" % (path, SCHEMA)
    return doc, None


def write_ledger(path, doc):
    """Fields only, mode 600 in a mode-700 folder, by one rename. A value shaped like a key or a
    token is refused, and nothing is written."""
    text = json.dumps(dict(doc, schema=SCHEMA), indent=2, sort_keys=True) + "\n"
    if stage_e_app.SECRET_RE.search(text):
        raise ValueError("the ledger holds fields only; nothing was written")
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def key_problem(path):
    """Why the key file at `path` cannot be the app's key, or None: a plain file only you can read,
    outside every git working tree."""
    try:
        st = os.lstat(path)
    except OSError:
        return "there is no key file at %s" % path
    if (not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077
            or inside_worktree(os.path.dirname(path))):
        return ("the key file at %s is not a plain file only you can read, outside every git "
                "working tree" % path)
    return None


def inside_worktree(folder):
    import pipeline_validator_ledger as ledger
    return ledger.inside_git_worktree(folder)


def write_key(path, pem):
    """The key, to a file that must not exist yet, mode 600 from its first byte. Never printed."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(pem)


# --------------------------------------------------------------------------- #
# The ruleset, through your own `gh` login
# --------------------------------------------------------------------------- #
def gh_put_ruleset(io, path, body):
    """One PUT of one ruleset, with your own `gh` login. With your login it writes only two other
    things: a repository's merge settings (gh_patch_repo), and auto-merge off on a pull request
    (`merges off`). (status, doc). The body is on stdin; the token stays in `gh`."""
    if not RULESET_PATH_RE.fullmatch(path):
        raise gha.GitHubAppError("not a ruleset of one repository")
    try:
        out = io.run(["gh", "api", "-X", "PUT", path, "--input", "-"],
                     input=json.dumps(body).encode("utf-8"), timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise gha.GitHubAppError("`gh` could not be run")
    try:
        doc = json.loads(out.stdout.decode("utf-8")) if out.stdout else {}
    except (ValueError, UnicodeDecodeError):
        doc = {}
    return (200 if out.returncode == 0 else 422), doc


def covers_default(ruleset, default_branch):
    include = ((ruleset.get("conditions") or {}).get("ref_name") or {}).get("include") or []
    return any(r in ("~DEFAULT_BRANCH", "~ALL", "refs/heads/%s" % default_branch) for r in include)


def find_ruleset(io, repo, chosen=None):
    """(ruleset, None) for the one active repository ruleset on the default branch that requires
    status checks, read whole; or (None, why) in words, with nothing changed."""
    read = stage_e_app.gh_read(io)
    try:
        default = read("GET", "/repos/%s" % repo)[1].get("default_branch")
        listed = read("GET", "/repos/%s/rulesets" % repo)[1]
    except gha.GitHubAppError as exc:
        return None, "the rulesets of %s could not be read (%s)" % (repo, exc)
    if not isinstance(listed, list) or not isinstance(default, str):
        return None, "GitHub did not list the rulesets of %s" % repo
    found = []
    for item in listed:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        if chosen is not None and item["id"] != chosen:
            continue
        try:
            full = read("GET", "/repos/%s/rulesets/%d" % (repo, item["id"]))[1]
        except gha.GitHubAppError:
            return None, "ruleset %d of %s could not be read" % (item["id"], repo)
        if (isinstance(full, dict) and full.get("target") == "branch"
                and full.get("enforcement") == "active" and covers_default(full, default)
                and any(r.get("type") == "required_status_checks" for r in full.get("rules") or [])):
            found.append(full)
    if not found:
        return None, ("no active ruleset on %s's default branch requires status checks. Set one up "
                      "first (docs/AUTONOMY.md, 'Set up the ruleset FIRST')" % repo)
    if len(found) > 1:
        return None, ("several rulesets cover %s's default branch (%s): name one with --ruleset"
                      % (repo, ", ".join(str(f["id"]) for f in found)))
    if found[0].get("source_type") not in (None, "Repository"):
        return None, ("the ruleset on %s's default branch belongs to the %s, which this script does "
                      "not change" % (repo, found[0].get("source_type")))
    return found[0], None


def checks_of(ruleset):
    for rule in ruleset.get("rules") or []:
        if rule.get("type") == "required_status_checks":
            return (rule.get("parameters") or {}).get("required_status_checks") or []
    return []


def check_held(ruleset, app_id):
    return any(c.get("context") == CHECK_NAME and c.get("integration_id") == app_id
               for c in checks_of(ruleset))


def approvals(ruleset):
    """The most approving reviews any pull request rule needs: 0 when none does."""
    return max([(r.get("parameters") or {}).get("required_approving_review_count") or 0
                for r in ruleset.get("rules") or [] if r.get("type") == "pull_request"] or [0])


def as_it_was(back, ruleset):
    """True when `back` holds what this script changes exactly as `ruleset` did: the same
    Validator check entries and the same approving reviews."""
    def validator(rs):
        return [c for c in checks_of(rs) if c.get("context") == CHECK_NAME]
    return (back is not None and validator(back) == validator(ruleset)
            and approvals(back) == approvals(ruleset))


def changed(ruleset, app_id):
    """The ruleset's PUT body with the Validator check required from this app, and 0 approving
    reviews. Every other rule, check and setting is kept as it is."""
    rules = []
    for rule in copy.deepcopy(ruleset.get("rules") or []):
        # Only the two rules it changes are touched: every other rule goes back as it was read.
        if rule.get("type") == "required_status_checks":
            params = rule.setdefault("parameters", {})
            keep = [c for c in params.get("required_status_checks") or []
                    if c.get("context") != CHECK_NAME]
            params["required_status_checks"] = keep + [{"context": CHECK_NAME,
                                                        "integration_id": app_id}]
        elif rule.get("type") == "pull_request":
            rule.setdefault("parameters", {})["required_approving_review_count"] = 0
        rules.append(rule)
    return body_of(dict(ruleset, rules=rules))


def body_of(ruleset):
    return dict((k, copy.deepcopy(ruleset[k])) for k in
                ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")
                if k in ruleset)


# --------------------------------------------------------------------------- #
# Measure: what holds now
# --------------------------------------------------------------------------- #
def measure(io, repo, key_file, ledger, chosen):
    """[(piece, state, words)] and the facts behind them. Reads only."""
    rows, facts = [], {"ruleset": None, "signer": None}
    app_id, slug = ledger.get("app_id"), ledger.get("slug")
    if not isinstance(app_id, int):
        rows.append(("app", TODO, "no app recorded yet"))
        if key_file and os.path.lexists(key_file):
            rows.append(("key file", BLOCKED, "a file is already at %s: move it, or name another "
                                              "with --key-file" % key_file))
        else:
            rows.append(("key file", TODO, "written when GitHub makes the app"))
        rows += [("installed", TODO, "after the app"),
                 ("required check", TODO, "after the app")]
    else:
        key_file = ledger.get("key_file")
        bad = key_problem(key_file) if isinstance(key_file, str) else "no key file recorded"
        rows.append(("key file", BLOCKED if bad else DONE, bad or "only you can read it"))
        signer = stage_e_app.Signer(io, app_id, key_file, None)
        try:
            info = gha.app_info(signer.jwt(), call=io.call) if not bad else None
        except gha.GitHubAppError as exc:
            info = None
            rows.insert(0, ("app", UNKNOWN, "GitHub could not say what the key signs for (%s)"
                                            % exc))
        if info is not None:
            same = info.get("id") == app_id
            rows.insert(0, ("app", DONE if same else BLOCKED,
                            "app %d (%s)" % (app_id, slug) if same else
                            "the key signs for another app than the ledger records"))
            facts["signer"] = signer if same else None
        elif bad:
            rows.insert(0, ("app", BLOCKED, "app %d is recorded, but its key file is not usable"
                                            % app_id))
        if facts["signer"] is not None:
            try:
                inst = gha.installation_for(repo, facts["signer"].jwt(), call=io.call)
            except gha.GitHubAppError as exc:
                inst = exc
            rows.append(("installed", UNKNOWN if isinstance(inst, Exception) else
                         TODO if inst is None else DONE,
                         "on %s" % repo if isinstance(inst, int) else "not on %s yet" % repo))
        else:
            rows.append(("installed", UNKNOWN, "the app cannot be asked"))
        rows.append(("required check", TODO, "not read yet"))
    ruleset, why = find_ruleset(io, repo, chosen)
    facts["ruleset"] = ruleset
    rows = [r for r in rows if r[0] != "required check"]
    if ruleset is None:
        rows.append(("required check", BLOCKED, why))
        rows.append(("approving reviews", BLOCKED, why))
        return rows, facts
    held = isinstance(app_id, int) and check_held(ruleset, app_id)
    rows.append(("required check", DONE if held else TODO,
                 "ruleset %d requires %s from app %s" % (ruleset["id"], CHECK_NAME, app_id) if held
                 else "ruleset %d does not require %s from this app yet" % (ruleset["id"],
                                                                            CHECK_NAME)))
    n = approvals(ruleset)
    rows.append(("approving reviews", DONE if n == 0 else TODO,
                 "ruleset %d needs %d approving reviews" % (ruleset["id"], n)))
    return rows, facts


def show(io, rows):
    for piece, state, words in sorted(rows, key=lambda r: PIECES.index(r[0])):
        io.say("  %-18s %-8s %s" % (piece, state, words))


# --------------------------------------------------------------------------- #
# The steps, after your yes
# --------------------------------------------------------------------------- #
def step_create(io, repo, org, name, key_file, ledger, ledger_path):
    doc = gha.manifest(name, "https://github.com/%s" % repo, PERMISSIONS, description=DESCRIPTION)
    io.say("")
    io.say("1. GitHub's page to create the app %s. Click GitHub's own Create button there. This "
           "waits up to %d s." % (name, WAIT_SECONDS))
    flow = io.flow(doc, org)
    try:
        io.open_url(flow.url)
        code = flow.wait(WAIT_SECONDS)
    finally:
        flow.close()
    if not code:
        raise StepFailed("no answer came back from GitHub within %d s, so nothing was written. If "
                         "you did click Create, delete that app at %s, then run this again."
                         % (WAIT_SECONDS, stage_e_app.apps_page(org)))
    try:
        made = gha.convert(code, call=io.call)
    except gha.GitHubAppError as exc:
        raise StepFailed("GitHub did not hand the app over (%s), so nothing was written." % exc)
    pem = made.pop("pem")
    ledger.update(app_id=made["id"], slug=made["slug"], key_file=key_file, installation_ids={})
    write_ledger(ledger_path, ledger)
    try:
        write_key(key_file, pem)
    except OSError as exc:
        raise StepFailed("app %s exists, but its key could not be written to %s (%s). GitHub hands "
                         "a key over once: delete the app at %s and %s, then run this again."
                         % (made["id"], key_file, exc.strerror or "error",
                            stage_e_app.app_page(org, made["slug"]), ledger_path))
    finally:
        pem = made = None
    io.say("2. GitHub made app %s. Its key is in %s, mode 600. It was never printed."
           % (ledger["app_id"], key_file))


def step_install(io, repo, ledger, ledger_path, signer):
    url = "https://github.com/apps/%s/installations/new" % ledger["slug"]
    io.say("")
    io.say("3. GitHub's page to install the app. Pick %s. This waits up to %d s." % (repo,
                                                                                     WAIT_SECONDS))
    io.open_url(url)
    deadline = io.now() + WAIT_SECONDS
    while True:
        try:
            got = gha.installation_for(repo, signer.jwt(), call=io.call)
        except gha.GitHubAppError:
            got = None
        if isinstance(got, int):
            ledger.setdefault("installation_ids", {})[repo] = got
            write_ledger(ledger_path, ledger)
            io.say("   It is on %s." % repo)
            return
        if io.now() >= deadline:
            raise StepFailed("after %d s the app is not on %s. Install it from %s, then run this "
                             "again: what is done stays done." % (WAIT_SECONDS, repo, url))
        io.sleep(POLL_SECONDS)


def step_ruleset(io, repo, ruleset, app_id):
    """One PUT, read back. If GitHub does not keep the app as the check's source, the old ruleset
    goes back, and the words say so."""
    path = "repos/%s/rulesets/%d" % (repo, ruleset["id"])
    io.say("")
    io.say("4. Ruleset %d: %s required from app %d, and 0 approving reviews." % (
        ruleset["id"], CHECK_NAME, app_id))
    try:
        status, _doc = gh_put_ruleset(io, path, changed(ruleset, app_id))
    except gha.GitHubAppError as exc:
        raise StepFailed("the ruleset was not changed: %s." % exc)
    if status != 200:
        raise StepFailed("GitHub would not take the change, so ruleset %d is as it was. If GitHub "
                         "does not offer a source app for a required check here, the check is "
                         "not required: anything could post one by that name." % ruleset["id"])
    back, why = find_ruleset(io, repo, ruleset["id"])
    if back is not None and check_held(back, app_id) and approvals(back) == 0:
        io.say("   Read back: it holds.")
        return
    # Put it back, and believe only what GitHub then reads back: an undo GitHub refused, or one it
    # took without restoring the check and the reviews, is said as a failure, never as put back.
    try:
        undo_status, _doc = gh_put_ruleset(io, path, body_of(ruleset))
    except gha.GitHubAppError:
        undo_status = None
    restored = find_ruleset(io, repo, ruleset["id"])[0] if undo_status == 200 else None
    if as_it_was(restored, ruleset):
        undone = "ruleset %d was put back as it was" % ruleset["id"]
    else:
        undone = ("putting ruleset %d back failed: check it on GitHub now, since it may require a "
                  "%s check anything could post, with %d approving reviews"
                  % (ruleset["id"], CHECK_NAME, approvals(back) if back is not None else 0))
    raise StepFailed("GitHub did not keep %s with this app as its source (%s), so %s. A check "
                     "anything could post is not one to require." % (
                         CHECK_NAME, why or "read back without it", undone))


def plan_lines(rows, repo, key_file, name, ruleset):
    todo = dict((p, w) for p, s, w in rows if s == TODO)
    out = []
    if "app" in todo:
        out.append("- make the GitHub app %s, which may write check runs and nothing else, and "
                   "write its key to %s" % (name, key_file))
    if "app" in todo or "installed" in todo:
        out.append("- install it on %s" % repo)
    if "required check" in todo:
        out.append("- make ruleset %d require the %s check, with this app as its only source, so "
                   "nothing else can post a check by that name" % (ruleset["id"], CHECK_NAME))
    if "approving reviews" in todo:
        out.append("- drop the rule that a pull request needs %d approving review%s before it "
                   "merges. That removes a person's review as a gate: after this, a merge waits "
                   "for the %s check, which only the validator's own app can post"
                   % (approvals(ruleset), "" if approvals(ruleset) == 1 else "s", CHECK_NAME))
    return out


# --------------------------------------------------------------------------- #
# merges on|off: auto-merge for one repository (KIT-605)
# --------------------------------------------------------------------------- #
SETTINGS_FILE = "~/.validator/settings.json"
VALIDATOR_LEDGER = "~/.validator/ledger.json"
# The repository admin role (GitHub numbers it 5): in a switched-on project the only bypass, and
# only for pull requests, so you can still merge a held or human-only pull request yourself.
# GitHub records each bypass, so the watcher can check it.
ADMIN_BYPASS = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
PARTS = ("validator", "strict", "approvals", "code_owners", "last_push", "bypass")
REPO_PATH_RE = re.compile(r"repos/[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
REPO_KEYS = ("allow_auto_merge", "allow_squash_merge")
LIVE_SECONDS = 7 * 86400        # no decision from the validator for a week: its job may be down
PR_PAGES = 20                   # of 100 open pull requests each
OPEN_PRS = ("query($owner: String!, $name: String!, $after: String) { repository(owner: $owner, "
            "name: $name) { pullRequests(states: OPEN, first: 100, after: $after) { pageInfo { "
            "hasNextPage endCursor } nodes { id number autoMergeRequest { enabledAt } } } } }")
CLASSIC = ("query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { "
           "defaultBranchRef { name branchProtectionRule { pattern } } } }")
AUTO_OFF = ("mutation($id: ID!) { disablePullRequestAutoMerge(input: {pullRequestId: $id}) { "
            "pullRequest { number } } }")


def managed(ruleset):
    """The parts `merges on` sets, as `ruleset` has them: the Validator check entries, whether
    branches must be up to date, the approving reviews a pull request needs, whether a code owner
    must review and whether the last push needs someone else's approval (each a person's review),
    and who may bypass it (None when GitHub did not show that)."""
    req = [r for r in ruleset.get("rules") or [] if r.get("type") == "required_status_checks"]
    params = (req[0].get("parameters") or {}) if req else {}
    reviews = [(r.get("parameters") or {}) for r in ruleset.get("rules") or []
               if r.get("type") == "pull_request"]
    bypass = ruleset.get("bypass_actors")
    return {"validator": [c for c in params.get("required_status_checks") or []
                          if c.get("context") == CHECK_NAME],
            "strict": params.get("strict_required_status_checks_policy") is True,
            "approvals": approvals(ruleset),
            "code_owners": any(p.get("require_code_owner_review") is True for p in reviews),
            "last_push": any(p.get("require_last_push_approval") is True for p in reviews),
            "bypass": bypass if isinstance(bypass, list) else None}


def merges_target(app_id):
    """The parts in a switched-on project: no person's review is a gate, the Validator check is."""
    return {"validator": [{"context": CHECK_NAME, "integration_id": app_id}], "strict": True,
            "approvals": 0, "code_owners": False, "last_push": False,
            "bypass": [dict(ADMIN_BYPASS)]}


def _part(key, value):
    if key == "validator":
        return sorted(json.dumps([c.get("context"), c.get("integration_id")]) for c in value)
    if key == "bypass" and value is not None:
        return sorted(json.dumps([b.get("actor_id"), b.get("actor_type"), b.get("bypass_mode")])
                      for b in value)
    return value


def differ(a, b):
    """The parts in which `a` and `b` differ, in order."""
    return [k for k in PARTS if _part(k, a[k]) != _part(k, b[k])]


def same_parts(a, b):
    return not differ(a, b)


def with_parts(ruleset, parts):
    """The ruleset's PUT body with the parts as `parts` has them. Every other rule, check and
    setting goes back as it is now, so a check added since `merges on` stays."""
    rules = []
    for rule in copy.deepcopy(ruleset.get("rules") or []):
        if rule.get("type") == "required_status_checks":
            params = rule.setdefault("parameters", {})
            keep = [c for c in params.get("required_status_checks") or []
                    if c.get("context") != CHECK_NAME]
            params["required_status_checks"] = keep + copy.deepcopy(parts["validator"])
            params["strict_required_status_checks_policy"] = parts["strict"]
        elif rule.get("type") == "pull_request":
            rule.setdefault("parameters", {}).update(
                required_approving_review_count=parts["approvals"],
                require_code_owner_review=parts["code_owners"],
                require_last_push_approval=parts["last_push"])
        rules.append(rule)
    return body_of(dict(ruleset, rules=rules, bypass_actors=copy.deepcopy(parts["bypass"] or [])))


def who(actor):
    """A bypass actor, in words."""
    kind, n, mode = actor.get("actor_type"), actor.get("actor_id"), actor.get("bypass_mode")
    name = {"OrganizationAdmin": "the organization's admins", "DeployKey": "deploy keys",
            "Team": "team %s" % n, "Integration": "app %s" % n}.get(
        kind, "repository admins" if (kind, n) == ("RepositoryRole", 5) else "%s %s" % (kind, n))
    return name + ("" if mode == "pull_request" else ", %s" % mode)


def put_parts(io, repo, ruleset, parts):
    """One PUT of the parts, read back. If GitHub does not keep them, the ruleset goes back as
    it was, and the words say whether that worked."""
    path = "repos/%s/rulesets/%d" % (repo, ruleset["id"])
    try:
        status, _doc = gh_put_ruleset(io, path, with_parts(ruleset, parts))
    except gha.GitHubAppError as exc:
        raise StepFailed("ruleset %d was not changed: %s" % (ruleset["id"], exc))
    if status != 200:
        raise StepFailed("GitHub would not take the change, so ruleset %d is as it was"
                         % ruleset["id"])
    back, _why = find_ruleset(io, repo, ruleset["id"])
    if back is not None and same_parts(managed(back), parts):
        io.say("   Read back: it holds.")
        return
    try:
        undo, _doc = gh_put_ruleset(io, path, body_of(ruleset))
    except gha.GitHubAppError:
        undo = None
    restored = find_ruleset(io, repo, ruleset["id"])[0] if undo == 200 else None
    if restored is not None and same_parts(managed(restored), managed(ruleset)):
        raise StepFailed("GitHub did not keep the change, so ruleset %d was put back as it was"
                         % ruleset["id"])
    raise StepFailed("GitHub did not keep the change, and putting ruleset %d back failed: check it "
                     "on GitHub now" % ruleset["id"])


def gh_graphql(io, query, variables):
    """One GraphQL call with your own `gh` login: (data, None) or (None, why). The query is on
    stdin, and the token stays in `gh`."""
    try:
        out = io.run(["gh", "api", "graphql", "--input", "-"], timeout=60,
                     input=json.dumps({"query": query, "variables": variables}).encode("utf-8"))
    except (OSError, subprocess.SubprocessError):
        return None, "`gh` could not be run"
    if out.returncode != 0:
        return None, "`gh api graphql` failed (exit %d)" % out.returncode
    try:
        doc = json.loads(out.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "`gh api graphql` did not answer with JSON"
    if not isinstance(doc, dict) or doc.get("errors") or not isinstance(doc.get("data"), dict):
        return None, "GitHub answered with an error"
    return doc["data"], None


def auto_merging(io, repo):
    """([(number, id)] of the open pull requests with auto-merge on, None), or (None, why)."""
    owner, name = repo.split("/", 1)
    found, after = [], None
    for _page in range(PR_PAGES):
        data, why = gh_graphql(io, OPEN_PRS, {"owner": owner, "name": name, "after": after})
        try:
            prs = data["repository"]["pullRequests"]
            for node in prs["nodes"]:
                if node.get("autoMergeRequest") is not None:
                    if not isinstance(node.get("number"), int) or not isinstance(node.get("id"),
                                                                                 str):
                        raise TypeError(node)
                    found.append((node["number"], node["id"]))
            more, after = prs["pageInfo"]["hasNextPage"], prs["pageInfo"]["endCursor"]
        except (TypeError, KeyError, AttributeError):
            return None, why or "GitHub did not list the open pull requests of %s" % repo
        if not more:
            return found, None
    return None, "%s has more open pull requests than this script reads" % repo


def classic_protection(io, repo):
    """True when a classic branch protection rule covers the default branch, False when none does,
    None when GitHub could not say."""
    owner, name = repo.split("/", 1)
    data, _why = gh_graphql(io, CLASSIC, {"owner": owner, "name": name})
    try:
        return data["repository"]["defaultBranchRef"]["branchProtectionRule"] is not None
    except (TypeError, KeyError):
        return None


def repo_merge_settings(io, repo):
    """{"allow_auto_merge", "allow_squash_merge"} as GitHub has them, or None."""
    try:
        doc = stage_e_app.gh_read(io)("GET", "/repos/%s" % repo)[1]
    except gha.GitHubAppError:
        return None
    if not isinstance(doc, dict) or not all(isinstance(doc.get(k), bool) for k in REPO_KEYS):
        return None
    return dict((k, doc[k]) for k in REPO_KEYS)


def gh_patch_repo(io, repo, body):
    """One PATCH of one repository's merge settings, with your own `gh` login: True when GitHub
    took it. Nothing else about the repository is sent."""
    path = "repos/%s" % repo
    if not REPO_PATH_RE.fullmatch(path) or not body or set(body) - set(REPO_KEYS):
        raise gha.GitHubAppError("not one repository's merge settings")
    try:
        out = io.run(["gh", "api", "-X", "PATCH", path, "--input", "-"],
                     input=json.dumps(body).encode("utf-8"), timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise gha.GitHubAppError("`gh` could not be run")
    return out.returncode == 0


def job_warning(got, now):
    """None when the validator's ledger (`vledger.read()`'s answer) holds a decision from the last
    week. Otherwise why nothing would merge yet, in words."""
    rows = got.get("rows") if isinstance(got, dict) else None
    if rows is None:
        return "the validator's ledger could not be read, so its job cannot be seen running"
    times = []
    for row in rows:
        try:
            times.append(calendar.timegm(time.strptime(row.get("at"), "%Y-%m-%dT%H:%M:%SZ")))
        except (TypeError, ValueError, AttributeError):
            continue
    if times and now - max(times) <= LIVE_SECONDS:
        return None
    return ("no decision from the validator in the last %d days, so its job may not be running. "
            "Until it runs, no %s check is posted" % (LIVE_SECONDS // 86400, CHECK_NAME))


def login_warning(settings):
    """None when the settings name Stage E's own app's login. Otherwise why nothing would merge."""
    if settings.get("state") == vsettings.READ and settings.get(vsettings.LOGIN_KEY):
        return None
    return ("Stage E's own app is not in the settings (%s), so no review is trusted"
            % vsettings.LOGIN_KEY)


def write_settings(path, doc):
    """Your switch, written by your own script after your typed yes, as the hold's `clear` writes
    the hold: the whole file by one rename, mode 600, in a folder only you can write. The
    validator's own code still has no write path."""
    bad = vsettings.problems(doc)
    if bad:
        raise StepFailed("the settings would not be a settings file (%s), so nothing was written"
                         % bad[0])
    folder = os.path.dirname(path)
    if inside_worktree(folder):
        raise StepFailed("the settings file is inside a git working tree, so nothing was written")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def step_settings(io, path, repo, on):
    """The switch: on lists `repo` and turns approve.merges on; off takes `repo` off the list and
    leaves approve.merges as it is, for the other repositories. Read back through load()."""
    got = vsettings.load(path)
    if got["state"] not in (vsettings.READ, vsettings.ABSENT):
        raise StepFailed("the settings file is not obeyed (%s), so nothing was written"
                         % got["why"])
    doc = {"schema": vsettings.SETTINGS_SCHEMA, "approve": dict(got["approve"]),
           "limits": dict(got["limits"]), "allow": dict(got["allow"]),
           vsettings.LOGIN_KEY: got[vsettings.LOGIN_KEY],
           vsettings.REPOS_KEY: [r for r in got[vsettings.REPOS_KEY] if r != repo]}
    if on:
        doc["approve"]["merges"] = True
        doc[vsettings.REPOS_KEY].append(repo)
    write_settings(path, doc)
    back = vsettings.load(path)
    if back["state"] != vsettings.READ or vsettings.merge_allowed(back, repo) != on:
        raise StepFailed("the settings file did not read back as written (%s)" % back["why"])
    io.say("   Read back: merges in %s are %s." % (repo, "on" if on else "off"))


def show_rows(io, rows):
    for piece, state, words in rows:
        io.say("  %-14s %-8s %s" % (piece, state, words))


def measure_on(io, repo, settings_path, vledger_path, ledger, chosen):
    """[(piece, state, words)], the facts, and the warnings for `merges on`. Reads only."""
    app_rows, facts = measure(io, repo, None, ledger, chosen)
    app_id, ruleset = ledger.get("app_id"), facts["ruleset"]
    rows = []
    bad = [(s, w) for p, s, w in app_rows if p in ("app", "key file", "installed") and s != DONE]
    if bad:
        rows.append(("validator app", UNKNOWN if bad[0][0] == UNKNOWN else BLOCKED,
                     "%s. Make it and install it first: python3 scripts/pipeline_validator_app.py "
                     "run --repo %s --key-file ~/.validator/app.pem" % (bad[0][1], repo)))
    else:
        rows.append(("validator app", DONE, "app %d, installed on %s" % (app_id, repo)))
    if ruleset is None:
        why = dict((p, w) for p, _s, w in app_rows).get("required check", "")
        if classic_protection(io, repo):
            why = ("the default branch is protected by classic branch protection only. This "
                   "script changes rulesets, so nothing changes: move the protection to a ruleset "
                   "first (docs/AUTONOMY.md)")
        rows.append(("ruleset", BLOCKED, why))
    elif managed(ruleset)["bypass"] is None:
        rows.append(("ruleset", UNKNOWN, "GitHub did not show who may bypass ruleset %d. It shows "
                                         "that to repository admins only" % ruleset["id"]))
    elif not isinstance(app_id, int):
        rows.append(("ruleset", BLOCKED, "after the validator app"))
    elif same_parts(managed(ruleset), merges_target(app_id)):
        rows.append(("ruleset", DONE, "ruleset %d requires %s from app %d and branches up to "
                                      "date, needs no person's review, and only repository "
                                      "admins may bypass it, for pull requests"
                     % (ruleset["id"], CHECK_NAME, app_id)))
    else:
        rows.append(("ruleset", TODO, "ruleset %d is not set up for merges yet" % ruleset["id"]))
    have = repo_merge_settings(io, repo)
    if have is None:
        rows.append(("repository", UNKNOWN, "GitHub did not say whether %s allows auto-merge and "
                                            "squash merging" % repo))
    else:
        missing = [k.replace("allow_", "").replace("_", " ") for k in REPO_KEYS if not have[k]]
        rows.append(("repository", TODO if missing else DONE,
                     "%s does not allow %s yet" % (repo, " or ".join(missing)) if missing else
                     "%s allows auto-merge and squash merging" % repo))
    got = vsettings.load(settings_path)
    listed = got[vsettings.REPOS_KEY]
    if got["state"] == vsettings.ABSENT:
        rows.append(("settings", TODO, "no settings file yet"))
    elif got["state"] != vsettings.READ:
        rows.append(("settings", BLOCKED, "the settings file is not obeyed: %s. Fix it by hand "
                                          "first" % got["why"]))
    elif vsettings.merge_allowed(got, repo):
        rows.append(("settings", DONE, "merges on, and %s listed" % repo))
    elif repo not in listed and len(listed) >= vsettings.MAX_REPOS:
        rows.append(("settings", BLOCKED, "merge_repos already names %d repositories, the most it "
                                          "may" % vsettings.MAX_REPOS))
    else:
        rows.append(("settings", TODO, "merges %s, and %s %s" % (
            "on" if got["approve"]["merges"] else "off", repo,
            "listed" if repo in listed else "not listed")))
    warnings = [w for w in (job_warning(vledger.read(vledger_path), io.now()),
                            login_warning(got)) if w]
    return rows, dict(facts, app_id=app_id, settings=got), warnings


def plan_on(rows, repo, facts, settings_path):
    todo = set(p for p, s, _w in rows if s == TODO)
    out, ruleset, app_id = [], facts["ruleset"], facts["app_id"]
    if "ruleset" in todo:
        n, have = ruleset["id"], managed(ruleset)
        for part in differ(have, merges_target(app_id)):
            if part == "validator":
                out.append("- ruleset %d: require the %s check, with the validator's app (%d) as "
                           "its only source, so nothing else can post a check by that name"
                           % (n, CHECK_NAME, app_id))
            elif part == "strict":
                out.append("- ruleset %d: require branches to be up to date before merging, so "
                           "each pull request is tested on the latest default branch" % n)
            elif part == "approvals":
                k = have["approvals"]
                out.append("- ruleset %d: drop the rule that a pull request needs %d approving "
                           "review%s. That removes a person's review as a gate: a merge waits for "
                           "the %s check instead" % (n, k, "" if k == 1 else "s", CHECK_NAME))
            elif part == "code_owners":
                out.append("- ruleset %d: drop the rule that a code owner must review a pull "
                           "request. That removes a code owner's review as a gate" % n)
            elif part == "last_push":
                out.append("- ruleset %d: drop the rule that the last push needs someone else's "
                           "approval. That removes that review as a gate" % n)
            else:
                gone = [who(b) for b in have["bypass"]
                        if _part("bypass", [b]) != _part("bypass", [ADMIN_BYPASS])]
                out.append("- ruleset %d: only repository admins may bypass it, and only for pull "
                           "requests%s" % (n, ". That removes the bypass for: %s" % "; ".join(gone)
                                           if gone else ""))
    if "repository" in todo:
        out.append("- allow auto-merge and squash merging on %s" % repo)
    if "settings" in todo:
        out.append("- turn approve.merges on and list %s in merge_repos, in %s%s" % (
            repo, settings_path, " (a new file, with every other switch off)"
            if facts["settings"]["state"] == vsettings.ABSENT else ""))
    out.append("The switch goes on last, so a failure part way leaves it off.")
    return out


def measure_off(io, repo, settings_path, ledger):
    """[(piece, state, words)] and the facts for `merges off`. Reads only."""
    rows, facts = [], {"ruleset": None, "found": None}
    got = vsettings.load(settings_path)
    if got["state"] == vsettings.READ and repo in got[vsettings.REPOS_KEY]:
        rows.append(("settings", TODO, "%s is listed in merge_repos" % repo))
    elif got["state"] in (vsettings.READ, vsettings.ABSENT):
        rows.append(("settings", DONE, "%s is not listed: no merge there is approved" % repo))
    else:
        rows.append(("settings", DONE, "the settings file is not obeyed (%s), so every switch "
                                       "already reads off. Fix it by hand before you turn "
                                       "anything on" % got["why"]))
    prs, why = auto_merging(io, repo)
    facts["prs"] = prs
    if prs is None:
        rows.append(("pull requests", UNKNOWN, why))
    else:
        rows.append(("pull requests", TODO if prs else DONE,
                     "auto-merge is on for %s" % ", ".join("#%d" % n for n, _i in prs) if prs
                     else "no open pull request has auto-merge on"))
    record = (ledger.get("merges") or {}).get(repo)
    if not isinstance(record, dict):
        rows.append(("ruleset", DONE, "no record of `merges on` changing a ruleset here, so "
                                      "nothing to put back"))
        return rows, facts
    found = managed(record.get("found") if isinstance(record.get("found"), dict) else {})
    ruleset, why = find_ruleset(io, repo, record.get("ruleset_id"))
    facts.update(ruleset=ruleset, found=found)
    if found["bypass"] is None:
        rows.append(("ruleset", BLOCKED, "the record of what `merges on` found is not whole. Put "
                                         "the ruleset back by hand"))
    elif ruleset is None or managed(ruleset)["bypass"] is None:
        rows.append(("ruleset", UNKNOWN, why or "GitHub did not show who may bypass ruleset %s"
                     % record.get("ruleset_id")))
    elif same_parts(managed(ruleset), found):
        rows.append(("ruleset", DONE, "ruleset %d is as `merges on` found it" % ruleset["id"]))
    else:
        rows.append(("ruleset", TODO, "ruleset %d is set up for merges" % ruleset["id"]))
    return rows, facts


def plan_off(rows, repo, facts, settings_path):
    state = dict((p, s) for p, s, _w in rows)
    out = []
    if state["settings"] == TODO:
        out.append("- take %s off merge_repos in %s, so no new merge there is approved. "
                   "approve.merges stays as it is, for the other repositories"
                   % (repo, settings_path))
    if state["pull requests"] == TODO:
        out.append("- turn auto-merge off on %s"
                   % ", ".join("#%d" % n for n, _i in facts["prs"]))
    if state["ruleset"] == TODO:
        found, n = facts["found"], facts["ruleset"]["id"]
        words = {"validator": "the %s check %s" % (CHECK_NAME, "required" if found["validator"]
                                                    else "not required"),
                 "strict": "branches %s to be up to date" % ("required" if found["strict"]
                                                            else "not required"),
                 "approvals": "%d approving review%s" % (found["approvals"],
                                                         "" if found["approvals"] == 1 else "s"),
                 "code_owners": "a code owner's review %s" % ("required" if found["code_owners"]
                                                             else "not required"),
                 "last_push": "approval of the last push %s" % ("required" if found["last_push"]
                                                               else "not required"),
                 "bypass": "bypass for: %s" % ("; ".join(who(b) for b in found["bypass"])
                                               or "nobody")}
        out.append("- ruleset %d: put back as `merges on` found it: %s" % (n, ", ".join(
            words[p] for p in differ(managed(facts["ruleset"]), found))))
        if state["pull requests"] != DONE and state["pull requests"] != TODO:
            out.append("  It waits until no pull request could still merge on its own: run "
                       "`merges off` again when GitHub can be read.")
    return out


def merges_on(io, repo, args, ledger, ledger_path):
    rows, facts, warnings = measure_on(io, repo, args.settings_file, args.validator_ledger,
                                       ledger, args.ruleset)
    io.say("What holds now, for merges in %s:" % repo)
    show_rows(io, rows)
    for w in warnings:
        io.say("WARNING: nothing would merge yet: %s." % w)
    states = [s for _p, s, _w in rows]
    if BLOCKED in states or UNKNOWN in states:
        io.say("A piece is blocked or could not be checked, so nothing was changed.")
        return EXIT_HOLD if BLOCKED in states else EXIT_UNCHECKED
    if TODO not in states:
        io.say("Nothing to do: merges are on for %s. Nothing was changed." % repo)
        return EXIT_OK
    io.say("")
    io.say("This will:")
    for line in plan_on(rows, repo, facts, args.settings_file):
        io.say(line)
    if io.ask("Type yes to go on: ").strip() != "yes":
        io.say("You did not type yes. Nothing was changed.")
        return EXIT_NOT_YES
    todo, changed_ruleset = set(p for p, s, _w in rows if s == TODO), False
    try:
        if "ruleset" in todo:
            records = ledger.setdefault("merges", {})
            if repo not in records:
                records[repo] = {"ruleset_id": facts["ruleset"]["id"],
                                 "found": body_of(facts["ruleset"])}
                write_ledger(ledger_path, ledger)
            io.say("")
            io.say("1. Ruleset %d, set up for merges." % facts["ruleset"]["id"])
            put_parts(io, repo, facts["ruleset"], merges_target(facts["app_id"]))
            changed_ruleset = True
        if "repository" in todo:
            io.say("2. Auto-merge and squash merging on %s." % repo)
            took = gh_patch_repo(io, repo, dict((k, True) for k in REPO_KEYS))
            back = repo_merge_settings(io, repo)
            if not took or back is None or not all(back.values()):
                raise StepFailed("GitHub did not allow auto-merge and squash merging on %s" % repo)
            io.say("   Read back: it holds.")
        if "settings" in todo:
            io.say("3. The switch: merges on, and %s listed." % repo)
            step_settings(io, args.settings_file, repo, True)
    except (StepFailed, gha.GitHubAppError, OSError, ValueError) as exc:
        io.say("STOPPED: %s." % exc)
        if "settings" in todo:
            io.say("Nothing turned merges on: the switch is still off.")
        else:
            io.say("The switch was already on, and is unchanged: merges in %s may still be "
                   "approved. Run `merges off` to stop them." % repo)
        if changed_ruleset:
            io.say("Run `merges on` again to finish, or `merges off` to put the ruleset back.")
        return EXIT_HOLD
    rows, _facts, _w = measure_on(io, repo, args.settings_file, args.validator_ledger, ledger,
                                  args.ruleset)
    io.say("")
    io.say("Checked again:")
    show_rows(io, rows)
    return EXIT_OK if all(s == DONE for _p, s, _w in rows) else EXIT_HOLD


def merges_off(io, repo, args, ledger, ledger_path):
    rows, facts = measure_off(io, repo, args.settings_file, ledger)
    io.say("What holds now, for merges in %s:" % repo)
    show_rows(io, rows)
    state = dict((p, s) for p, s, _w in rows)
    if TODO not in state.values():
        if BLOCKED in state.values() or UNKNOWN in state.values():
            io.say("A piece is blocked or could not be checked, and nothing else is to do.")
            return EXIT_HOLD if BLOCKED in state.values() else EXIT_UNCHECKED
        io.say("Nothing to do: merges are off for %s. Nothing was changed." % repo)
        return EXIT_OK
    io.say("")
    io.say("This will:")
    for line in plan_off(rows, repo, facts, args.settings_file):
        io.say(line)
    if io.ask("Type yes to go on: ").strip() != "yes":
        io.say("You did not type yes. Nothing was changed.")
        return EXIT_NOT_YES
    try:
        if state["settings"] == TODO:
            io.say("")
            io.say("1. The switch: %s off the list." % repo)
            step_settings(io, args.settings_file, repo, False)
        clear = state["pull requests"] == DONE
        if state["pull requests"] == TODO:
            io.say("2. Auto-merge off on each pull request in flight.")
            for number, node in facts["prs"]:
                data, why = gh_graphql(io, AUTO_OFF, {"id": node})
                if data is None:
                    raise StepFailed("auto-merge could not be turned off on #%d (%s), so the "
                                     "ruleset was not put back" % (number, why))
            left, why = auto_merging(io, repo)
            if left is None or left:
                raise StepFailed("auto-merge still reads on, or could not be read (%s), so the "
                                 "ruleset was not put back" % (why or ", ".join(
                                     "#%d" % n for n, _i in left)))
            io.say("   Read back: no open pull request has auto-merge on.")
            clear = True
        if state["ruleset"] == TODO and clear:
            io.say("3. Ruleset %d, back as `merges on` found it." % facts["ruleset"]["id"])
            put_parts(io, repo, facts["ruleset"], facts["found"])
            ledger.setdefault("merges", {}).pop(repo, None)
            write_ledger(ledger_path, ledger)
        elif state["ruleset"] == TODO:
            io.say("The ruleset was not put back: a pull request could still merge on its own.")
    except (StepFailed, gha.GitHubAppError, OSError, ValueError) as exc:
        io.say("STOPPED: %s." % exc)
        return EXIT_HOLD
    rows, _facts = measure_off(io, repo, args.settings_file, ledger)
    io.say("")
    io.say("Checked again:")
    show_rows(io, rows)
    states = [s for _p, s, _w in rows]
    return (EXIT_OK if all(s == DONE for s in states) else
            EXIT_UNCHECKED if UNKNOWN in states else EXIT_HOLD)


def cmd_merges(io, args):
    why = refused(io)
    if why:
        io.say("REFUSED: %s. Nothing was read or changed." % why)
        return EXIT_REFUSED
    args.settings_file = os.path.expanduser(args.settings_file)
    args.validator_ledger = os.path.expanduser(args.validator_ledger)
    if not (os.path.isabs(args.settings_file) and os.path.isabs(args.validator_ledger)):
        io.say("COULD NOT RUN: --settings-file and --validator-ledger must be absolute paths, or "
               "start with ~/. Nothing changed.")
        return EXIT_USAGE
    ledger_path = io.ledger_path()
    ledger, bad = read_ledger(ledger_path)
    if ledger is None:
        io.say("COULD NOT RUN: %s. Nothing changed." % bad)
        return EXIT_UNCHECKED
    repo = args.repo.lower()
    if args.state == "on":
        return merges_on(io, repo, args, ledger, ledger_path)
    return merges_off(io, repo, args, ledger, ledger_path)


# --------------------------------------------------------------------------- #
# The commands
# --------------------------------------------------------------------------- #
def default_name(io):
    try:
        out = io.run(["gh", "api", "user", "--jq", ".login"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    login = out.stdout.decode("utf-8", "replace").strip() if out.returncode == 0 else ""
    if not stage_e_app.LOGIN_RE.fullmatch(login):
        return None
    return ("validator-" + login)[:NAME_MAX].rstrip("-")


def cmd_run(io, args):
    why = refused(io)
    if why:
        io.say("REFUSED: %s. Nothing was read or changed." % why)
        return EXIT_REFUSED
    key_file = os.path.expanduser(args.key_file)
    if not os.path.isabs(key_file) or inside_worktree(os.path.dirname(key_file) or "/"):
        io.say("COULD NOT RUN: --key-file must be an absolute path, or start with ~/, outside every "
               "git working tree. Nothing changed.")
        return EXIT_USAGE
    ledger_path = io.ledger_path()
    ledger, bad = read_ledger(ledger_path)
    if ledger is None:
        io.say("COULD NOT RUN: %s. Nothing changed." % bad)
        return EXIT_UNCHECKED
    rows, facts = measure(io, args.repo, key_file, ledger, args.ruleset)
    io.say("What holds now, for %s:" % args.repo)
    show(io, rows)
    states = [s for _p, s, _w in rows]
    if BLOCKED in states or UNKNOWN in states:
        io.say("A piece is blocked or could not be checked, so nothing was changed.")
        return EXIT_HOLD if BLOCKED in states else EXIT_UNCHECKED
    if TODO not in states:
        io.say("Nothing to do: every piece holds. Nothing was changed.")
        return EXIT_OK
    name = args.name or ledger.get("slug") or default_name(io)
    if not name:
        io.say("COULD NOT RUN: name the app with --name (your `gh` login could not be read). "
               "Nothing changed.")
        return EXIT_USAGE
    io.say("")
    io.say("This will:")
    for line in plan_lines(rows, args.repo, key_file, name, facts["ruleset"]):
        io.say(line)
    if io.ask("Type yes to go on: ").strip() != "yes":
        io.say("You did not type yes. Nothing was changed.")
        return EXIT_NOT_YES
    try:
        if not isinstance(ledger.get("app_id"), int):
            step_create(io, args.repo, args.org, name, key_file, ledger, ledger_path)
        signer = stage_e_app.Signer(io, ledger["app_id"], ledger["key_file"], None)
        if args.repo not in (ledger.get("installation_ids") or {}) or dict(
                (p, s) for p, s, _w in rows).get("installed") == TODO:
            step_install(io, args.repo, ledger, ledger_path, signer)
        step_ruleset(io, args.repo, facts["ruleset"], ledger["app_id"])
    except StepFailed as exc:
        io.say("STOPPED: %s" % exc)
        return EXIT_HOLD
    rows, _facts = measure(io, args.repo, key_file, ledger, facts["ruleset"]["id"])
    io.say("")
    io.say("Checked again:")
    show(io, rows)
    return EXIT_OK if all(s == DONE for _p, s, _w in rows) else EXIT_HOLD


def cmd_verify(io, args):
    why = refused(io)
    if why:
        io.say("REFUSED: %s. Nothing was read." % why)
        return EXIT_REFUSED
    ledger, bad = read_ledger(io.ledger_path())
    if ledger is None:
        io.say("COULD NOT CHECK: %s." % bad)
        return EXIT_UNCHECKED
    rows, _facts = measure(io, args.repo, ledger.get("key_file"), ledger, args.ruleset)
    show(io, rows)
    return EXIT_OK if all(s == DONE for _p, s, _w in rows) else EXIT_HOLD


def main(argv=None, io=None):
    io = io or IO()
    ap = argparse.ArgumentParser(description="The validator's GitHub app and its required check.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="check, show, one typed yes, apply, check again")
    r.add_argument("--repo", required=True)
    r.add_argument("--key-file", required=True)
    r.add_argument("--name")
    r.add_argument("--org")
    r.add_argument("--ruleset", type=int)
    v = sub.add_parser("verify", help="the same checks, read-only")
    v.add_argument("--repo", required=True)
    v.add_argument("--ruleset", type=int)
    m = sub.add_parser("merges", help="auto-merge on or off for one repository: check, show, one "
                                      "typed yes, apply, check again")
    m.add_argument("state", choices=("on", "off"))
    m.add_argument("--repo", required=True)
    m.add_argument("--settings-file", default=SETTINGS_FILE)
    m.add_argument("--validator-ledger", default=VALIDATOR_LEDGER)
    m.add_argument("--ruleset", type=int)
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "validator-app-selftest-")
    if args.cmd and not gha.REPO_RE.fullmatch(args.repo or ""):
        io.say("COULD NOT RUN: --repo must be OWNER/NAME. Nothing changed.")
        return EXIT_USAGE
    if args.cmd == "run":
        if args.name is not None and not 1 <= len(args.name) <= NAME_MAX:
            io.say("COULD NOT RUN: --name must be 1 to %d characters. Nothing changed." % NAME_MAX)
            return EXIT_USAGE
        return cmd_run(io, args)
    if args.cmd == "verify":
        return cmd_verify(io, args)
    if args.cmd == "merges":
        if not vsettings.REPO_RE.fullmatch(args.repo.lower()):
            io.say("COULD NOT RUN: --repo must be OWNER/NAME as GitHub names it. Nothing changed.")
            return EXIT_USAGE
        return cmd_merges(io, args)
    ap.print_help()
    return EXIT_USAGE


# ======================================================================================
# SELFTEST: nothing below the line above is run by a reader, and the source scan stops there.
# ======================================================================================
KEY_MARK = "KEYMATERIAL-NEVER-SHOWN-0123"
KEYISH = "-----BEGIN TEST FIXTURE-----\n%s\n-----END TEST FIXTURE-----\n" % KEY_MARK
TOKEN_T = "ghs_" + "SELFTESTTOKENNOTREAL0123456789"


class _Out(object):
    def __init__(self, code, stdout=b"", stderr=b""):
        self.returncode, self.stdout, self.stderr = code, stdout, stderr


class _World(object):
    """GitHub as this script sees it: the app, where it is installed, and the rulesets."""

    def __init__(self, approvals=1, keeps_source=True, takes_put=True, extra_ruleset=False,
                 undo="takes"):
        self.app = None              # {"id", "slug"} once GitHub made it
        self.installed = False
        self.keeps_source, self.takes_put = keeps_source, takes_put
        self.undo = undo             # the second PUT: "takes" it, "refuses" it, or "ignores" it
        self.puts = []
        self.rulesets = {7: {"id": 7, "name": "main", "target": "branch", "enforcement": "active",
                             "source_type": "Repository",
                             "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"],
                                                         "exclude": []}},
                             "rules": [{"type": "required_status_checks", "parameters": {
                                 "strict_required_status_checks_policy": True,
                                 "required_status_checks": [{"context": "Kit checks",
                                                             "integration_id": 15368}]}},
                                       {"type": "pull_request", "parameters": {
                                           "required_approving_review_count": approvals,
                                           "dismiss_stale_reviews_on_push": True}},
                                       {"type": "non_fast_forward"}],
                             "bypass_actors": []}}
        if extra_ruleset:
            self.rulesets[9] = dict(copy.deepcopy(self.rulesets[7]), id=9, name="extra")
        # For `merges on|off`: the repository's merge settings, a classic protection rule, the open
        # pull requests ({"id", "number", "auto"}), and every write in order, each with whether
        # the settings file then allowed merges in acme/app.
        self.repo = {"default_branch": "main", "allow_auto_merge": False,
                     "allow_squash_merge": True}
        self.takes_patch, self.classic, self.prs = True, False, []
        self.prs_readable, self.disable_fails, self.disable_ignored = True, False, False
        self.settings_path, self.log, self.graphql = None, [], []


class _Flow(object):
    def __init__(self, world, code="abc_123"):
        self.world, self.code_ = world, code
        self.url = "http://127.0.0.1:1/start"

    def wait(self, seconds):
        return self.code_

    def close(self):
        pass


class _FakeIO(IO):
    made = []                           # every fake made, so a check can read every run's output

    def __init__(self, world, ledger_path, answer="yes", env=None, uid=501):
        _FakeIO.made.append(self)
        self.world, self._ledger, self.answer = world, ledger_path, answer
        self._env, self._uid = env if env is not None else {}, uid
        self.said, self.asked, self.opened, self.t = [], [], [], 1_800_000_000
        self.ran = []                   # every command and API call it was asked to make

    def _allowed(self):
        path = self.world.settings_path
        return vsettings.merge_allowed(vsettings.load(path), "acme/app") if path else None

    def env(self):
        return self._env

    def uid(self):
        return self._uid

    def say(self, text=""):
        self.said.append(text)

    def ask(self, question):
        self.asked.append(question)
        return self.answer

    def ledger_path(self):
        return self._ledger

    def open_url(self, url):
        self.opened.append(url)
        if "/installations/new" in url:
            self.world.installed = True

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def flow(self, doc, org):
        return _Flow(self.world)

    def call(self, method, path, auth=None, body=None):
        w = self.world
        self.ran.append([method, path])
        path = path.lower()             # GitHub's names ignore case
        if (method, path) == ("POST", "/app-manifests/abc_123/conversions"):
            w.app = {"id": 4242, "slug": "validator-acme"}
            return 201, {"id": 4242, "slug": "validator-acme", "pem": KEYISH,
                         "owner": {"login": "acme"}}
        if (method, path) == ("GET", "/app") and w.app:
            return 200, dict(w.app)
        if (method, path) == ("GET", "/repos/acme/app/installation"):
            return (200, {"id": 77}) if w.installed else (404, {})
        return 500, {}

    def run(self, argv, input=None, capture_output=True, timeout=60, cwd="/"):
        w = self.world
        self.ran.append(list(argv))
        argv = [a.lower() if a.startswith("repos/") else a for a in argv]
        if argv[:3] == ["openssl", "dgst", "-sha256"]:
            return _Out(0, b"s" * 256)
        if argv[:2] == ["gh", "api"] and argv[2:4] == ["user", "--jq"]:
            return _Out(0, b"acme\n")
        if argv[:4] == ["gh", "api", "-X", "PATCH"]:
            w.log.append(("patch " + argv[4], self._allowed()))
            if not w.takes_patch or argv[4] != "repos/acme/app":
                return _Out(1, b'{"message": "Validation Failed"}')
            w.repo.update(json.loads(input.decode("utf-8")))
            return _Out(0, json.dumps(w.repo).encode("utf-8"))
        if argv[:3] == ["gh", "api", "graphql"]:
            body = json.loads(input.decode("utf-8"))
            query, var = body["query"], body.get("variables") or {}
            w.graphql.append(query)
            if "disablePullRequestAutoMerge" in query:
                w.log.append(("disable " + str(var.get("id")), self._allowed()))
                if w.disable_fails:
                    return _Out(1, b"")
                for pr in w.prs:
                    if pr["id"] == var.get("id") and not w.disable_ignored:
                        pr["auto"] = False
                return _Out(0, json.dumps({"data": {"disablePullRequestAutoMerge": {
                    "pullRequest": {"number": 1}}}}).encode("utf-8"))
            if (str(var.get("owner")).lower(), str(var.get("name")).lower()) != ("acme", "app"):
                return _Out(1, b"")
            if "branchProtectionRule" in query:
                rule = {"pattern": "main"} if w.classic else None
                return _Out(0, json.dumps({"data": {"repository": {"defaultBranchRef": {
                    "name": "main", "branchProtectionRule": rule}}}}).encode("utf-8"))
            if "pullRequests" in query and w.prs_readable:
                nodes = [{"id": p["id"], "number": p["number"], "autoMergeRequest":
                          {"enabledAt": "2026-11-11T00:00:00Z"} if p["auto"] else None}
                         for p in w.prs]
                return _Out(0, json.dumps({"data": {"repository": {"pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": nodes}}}}).encode("utf-8"))
            return _Out(1, b"")
        if argv[:4] == ["gh", "api", "-X", "PUT"]:
            w.log.append(("put " + argv[4], self._allowed()))
            body = json.loads(input.decode("utf-8"))
            w.puts.append((argv[4], body))
            rid = int(argv[4].rsplit("/", 1)[1])
            if not w.takes_put or (len(w.puts) > 1 and w.undo == "refuses"):
                return _Out(1, b'{"message": "Validation Failed"}')
            if len(w.puts) > 1 and w.undo == "ignores":
                return _Out(0, json.dumps(w.rulesets[rid]).encode("utf-8"))
            if not w.keeps_source:      # a host that keeps no source app for a new check
                for rule in body.get("rules", []):
                    for c in (rule.get("parameters") or {}).get("required_status_checks") or []:
                        if c.get("context") == CHECK_NAME:
                            c.pop("integration_id", None)
            w.rulesets[rid] = dict(w.rulesets[rid], **body)
            return _Out(0, json.dumps(w.rulesets[rid]).encode("utf-8"))
        if argv[:2] == ["gh", "api"] and len(argv) == 3:
            path = argv[2]
            if path == "repos/acme/app":
                return _Out(0, json.dumps(w.repo).encode("utf-8"))
            if path == "repos/acme/app/rulesets":
                return _Out(0, json.dumps([{"id": i} for i in w.rulesets]).encode("utf-8"))
            m = re.fullmatch(r"repos/acme/app/rulesets/([0-9]+)", path)
            if m and int(m.group(1)) in w.rulesets:
                return _Out(0, json.dumps(w.rulesets[int(m.group(1))]).encode("utf-8"))
        return _Out(1, b"")


def _raises(fn):
    try:
        fn()
        return False
    except gha.GitHubAppError:
        return True


def selftest():
    import tempfile
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)

        def fresh(**kw):
            world = _World(**kw)
            ledger = os.path.join(tmp, "ledger-%d.json" % len(os.listdir(tmp)))
            key = os.path.join(tmp, "key-%d" % len(os.listdir(tmp)), "app.pem")
            return world, ledger, key

        def run(io, key, extra=()):
            return main(["run", "--repo", "acme/app", "--key-file", key] + list(extra), io=io)

        # 1. An agent's environment, and root, are refused before anything is read.
        world, ledger, key = fresh()
        for label, io in (("an agent's environment",
                           _FakeIO(world, ledger, env={sorted(AGENT_ENV_MARKERS)[0]: "1"})),
                          ("root", _FakeIO(world, ledger, uid=0))):
            code = run(io, key)
            check("%s: refused, nothing read or changed" % label,
                  (code, io.asked, world.puts, world.app, os.path.exists(ledger),
                   io.said[0].startswith("REFUSED")),
                  (EXIT_REFUSED, [], [], None, False, True))
        check("verify is refused in an agent's environment too",
              main(["verify", "--repo", "acme/app"],
                   io=_FakeIO(world, ledger, env={sorted(AGENT_ENV_MARKERS)[0]: "1"})),
              EXIT_REFUSED)

        # 2. Without a typed yes, nothing changes.
        for answer in ("no", "", "YES", "yes please"):
            world, ledger, key = fresh()
            io = _FakeIO(world, ledger, answer=answer)
            code = run(io, key)
            check("answer %r: nothing changed" % answer,
                  (code, len(io.asked), world.app, world.puts, os.path.exists(key),
                   os.path.exists(ledger)), (EXIT_NOT_YES, 1, None, [], False, False))
        check("...and what it would change was said first, in plain words",
              (any("drop the rule that a pull request needs 1 approving review" in s
                   for s in io.said),
               any("require the Validator check, with this app as its only source" in s
                   for s in io.said)), (True, True))

        # 3. The typed yes: the app, its key, the install, the ruleset, and the read-back.
        world, ledger, key = fresh()
        io = _FakeIO(world, ledger)
        code = run(io, key)
        rs = world.rulesets[7]
        check("yes: every piece holds after one run", (code, io.said[-5:][0].strip()[:6]),
              (EXIT_OK, "app   "))
        check("...the key is in its file, mode 600, and nowhere else",
              (open(key, encoding="utf-8").read() == KEYISH, stat.S_IMODE(os.stat(key).st_mode)),
              (True, 0o600))
        check("...the ledger records the app in fields only",
              (json.load(open(ledger, encoding="utf-8")), stat.S_IMODE(os.stat(ledger).st_mode)),
              ({"app_id": 4242, "slug": "validator-acme", "key_file": key,
                "installation_ids": {"acme/app": 77}, "schema": SCHEMA}, 0o600))
        check("...the ruleset requires Validator from this app, and keeps every other check",
              checks_of(rs), [{"context": "Kit checks", "integration_id": 15368},
                              {"context": "Validator", "integration_id": 4242}])
        check("...and needs 0 approving reviews, every other rule kept as it was",
              ([r.get("type") for r in rs["rules"]], approvals(rs),
               rs["rules"][1]["parameters"]["dismiss_stale_reviews_on_push"]),
              (["required_status_checks", "pull_request", "non_fast_forward"], 0, True))
        check("...in one change to the one ruleset", [p for p, _b in world.puts],
              ["repos/acme/app/rulesets/7"])
        # Stage E on the first version (low): every rule went back with an empty parameters
        # object, which GitHub's read never returned. Rules it does not change go back as read.
        check("...and a rule it does not change is sent exactly as it was read",
              world.puts[0][1]["rules"][2], {"type": "non_fast_forward"})
        check("...through GitHub's own pages, which you click",
              [u.split("?")[0] for u in io.opened],
              ["http://127.0.0.1:1/start", "https://github.com/apps/validator-acme/installations/new"])

        # 4. A second run changes nothing, and says so.
        io2 = _FakeIO(world, ledger)
        code = run(io2, key)
        check("a second run: nothing to do, no question, no change",
              (code, io2.asked, len(world.puts), any("Nothing to do" in s for s in io2.said)),
              (EXIT_OK, [], 1, True))
        check("verify: every piece holds, read-only",
              (main(["verify", "--repo", "acme/app"], io=_FakeIO(world, ledger)),
               len(world.puts)), (EXIT_OK, 1))

        # 5. GitHub does not keep the app as the check's source: put back, said, not required.
        world, ledger, key = fresh(keeps_source=False)
        before = copy.deepcopy(world.rulesets[7])
        io = _FakeIO(world, ledger)
        code = run(io, key)
        check("a host that drops the source: the ruleset is put back as it was, and it says so",
              (code, body_of(world.rulesets[7]) == body_of(before), len(world.puts),
               any("put back as it was" in s for s in io.said)), (EXIT_HOLD, True, 2, True))
        # Stage E on the first version (medium): an undo GitHub refused was still said as put back,
        # leaving a check anything could post, and 0 reviews, with nobody told.
        for undo in ("refuses", "ignores"):
            world, ledger, key = fresh(keeps_source=False, undo=undo)
            io = _FakeIO(world, ledger)
            code = run(io, key)
            check("a host that drops the source, then %s the undo: said as a failure, never as "
                  "put back" % undo,
                  (code, any("putting ruleset 7 back failed: check it on GitHub now" in s
                             for s in io.said),
                   any("was put back as it was" in s for s in io.said)), (EXIT_HOLD, True, False))
        world, ledger, key = fresh(takes_put=False)
        before = copy.deepcopy(world.rulesets[7])
        io = _FakeIO(world, ledger)
        check("a host that refuses the change: the ruleset is as it was, and it says why",
              (run(io, key), world.rulesets[7] == before,
               any("is as it was" in s for s in io.said)), (EXIT_HOLD, True, True))

        # 6. Nothing to change on: blocked, said, and nothing made.
        world, ledger, key = fresh()
        world.rulesets.clear()
        io = _FakeIO(world, ledger)
        check("no ruleset on the default branch: blocked, no question, no app",
              (run(io, key), io.asked, world.app, any("Set one up first" in s for s in io.said)),
              (EXIT_HOLD, [], None, True))
        world, ledger, key = fresh(extra_ruleset=True)
        io = _FakeIO(world, ledger)
        check("two rulesets: it asks you to name one, and makes nothing",
              (run(io, key), io.asked, world.app, any("--ruleset" in s for s in io.said)),
              (EXIT_HOLD, [], None, True))
        io = _FakeIO(world, ledger)
        check("...and with --ruleset it changes only that one",
              (run(io, key, ["--ruleset", "9"]), [p for p, _b in world.puts]),
              (EXIT_OK, ["repos/acme/app/rulesets/9"]))
        world, ledger, key = fresh()
        os.makedirs(os.path.dirname(key))
        with open(key, "w", encoding="utf-8") as fh:
            fh.write("someone else's file\n")
        io = _FakeIO(world, ledger)
        check("a file already at the key's path: blocked, untouched, no app",
              (run(io, key), world.app, open(key, encoding="utf-8").read()),
              (EXIT_HOLD, None, "someone else's file\n"))
        world, ledger, key = fresh(approvals=0)
        io = _FakeIO(world, ledger)
        run(io, key)
        check("reviews already at 0: the plan does not say it drops them",
              any("drop the rule" in s for s in io.said), False)
        for label, change in (("on another branch only",
                               {"conditions": {"ref_name": {"include": ["refs/heads/dev"],
                                                            "exclude": []}}}),
                              ("only evaluated, not enforced", {"enforcement": "evaluate"}),
                              ("an organization's", {"source_type": "Organization"})):
            world, ledger, key = fresh()
            world.rulesets[7].update(change)
            io = _FakeIO(world, ledger)
            check("a ruleset %s is not changed: blocked, no question, no app" % label,
                  (run(io, key), io.asked, world.app, world.puts), (EXIT_HOLD, [], None, []))
        check("a PUT to anything but one repository's ruleset is refused before `gh` runs",
              [_raises(lambda p=p: gh_put_ruleset(_FakeIO(_World(), ledger), p, {}))
               for p in ("repos/a/b/branches/main/protection", "repos/a/b/rulesets",
                         "orgs/a/rulesets/7", "repos/a/b/rulesets/7/../8")], [True] * 4)

        # 7. No credential reaches any output, the ledger, or an argument list.
        world, ledger, key = fresh()
        io = _FakeIO(world, ledger)
        run(io, key)
        everything = "\n".join(io.said) + open(ledger, encoding="utf-8").read() + json.dumps(
            world.puts)
        check("no key and no token in anything it said, wrote to the ledger, or sent",
              [m for m in (KEY_MARK, "BEGIN TEST FIXTURE", TOKEN_T) if m in everything], [])
        check("the command line is checked before anything is read",
              (main(["run", "--repo", "acme app", "--key-file", key], io=_FakeIO(world, ledger)),
               main(["run", "--repo", "acme/app", "--key-file", "app.pem"],
                    io=_FakeIO(world, ledger))), (EXIT_USAGE, EXIT_USAGE))

        # 9. merges on|off, for one repository (KIT-605). Each world has run's work done (the app,
        #    its key, the install), and a ruleset that is not yet set up for merges: no Validator
        #    check, branches not required up to date, 1 approving review, an org admin bypass.
        no_vledger = os.path.join(tmp, "no-validator-ledger.json")
        worlds, first_io = [], len(_FakeIO.made)
        org_bypass = [{"actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}]

        def merges_world():
            world, ledger, key = fresh()
            run(_FakeIO(world, ledger), key)
            world.settings_path = os.path.join(tmp, "v-%d" % len(os.listdir(tmp)), "settings.json")
            req = world.rulesets[7]["rules"][0]["parameters"]
            req["required_status_checks"] = [{"context": "Kit checks", "integration_id": 15368}]
            req["strict_required_status_checks_policy"] = False
            world.rulesets[7]["rules"][1]["parameters"]["required_approving_review_count"] = 1
            world.rulesets[7]["bypass_actors"] = copy.deepcopy(org_bypass)
            world.rulesets[7]["rules"][1]["parameters"].update(require_code_owner_review=True,
                                                               require_last_push_approval=True)
            world.puts, world.log = [], []
            worlds.append((world, ledger))
            return world, ledger

        def merges(io, state, repo="acme/app"):
            return main(["merges", state, "--repo", repo, "--settings-file",
                         io.world.settings_path, "--validator-ledger", no_vledger], io=io)

        def allowed(world):
            return vsettings.merge_allowed(vsettings.load(world.settings_path), "acme/app")

        found = {"validator": [], "strict": False, "approvals": 1, "bypass": org_bypass,
                 "code_owners": True, "last_push": True}
        world, ledger = merges_world()
        check("the world before: the parts as a ruleset not yet set up for merges",
              same_parts(managed(world.rulesets[7]), found), True)

        # 9a. Refused in an agent's environment, before it reads anything.
        for state in ("on", "off"):
            io = _FakeIO(world, ledger, env={sorted(AGENT_ENV_MARKERS)[0]: "1"})
            check("merges %s in an agent's environment: refused before anything is read" % state,
                  (merges(io, state), io.ran, io.asked, world.log,
                   os.path.exists(world.settings_path), io.said[0].startswith("REFUSED")),
                  (EXIT_REFUSED, [], [], [], False, True))

        # 9b. Without a typed yes, nothing changes; what it would change is said first.
        for answer in ("no", "", "YES"):
            world, ledger = merges_world()
            before = (copy.deepcopy(world.rulesets[7]), dict(world.repo))
            io = _FakeIO(world, ledger, answer=answer)
            check("merges on, answer %r: nothing changed" % answer,
                  (merges(io, "on"), len(io.asked), world.log, world.rulesets[7] == before[0],
                   world.repo == before[1], os.path.exists(world.settings_path),
                   "merges" in read_ledger(ledger)[0]),
                  (EXIT_NOT_YES, 1, [], True, True, False, False))
        said = "\n".join(io.said)
        check("...it said each change first, in plain words",
              [w for w in ("require the Validator check, with the validator's app (4242) as its "
                           "only source", "branches to be up to date",
                           "drop the rule that a pull request needs 1 approving review",
                           "That removes a person's review as a gate",
                           "only repository admins may bypass it, and only for pull requests",
                           "That removes the bypass for: the organization's admins",
                           "drop the rule that a code owner must review a pull request",
                           "drop the rule that the last push needs someone else's approval",
                           "allow auto-merge and squash merging on acme/app",
                           "turn approve.merges on and list acme/app") if w not in said], [])
        check("...and it warned that nothing would merge yet, and still asked",
              ("no decision from the validator" in said, "Stage E's own app" in said,
               len(io.asked)), (True, True, 1))

        # 9c. The yes: the ruleset, then the repository, and the switch last; each read back.
        world, ledger = merges_world()
        io = _FakeIO(world, ledger)
        code = merges(io, "on")
        rs, s = world.rulesets[7], vsettings.load(world.settings_path)
        check("merges on: done, and checked again", (code, "Checked again:" in io.said),
              (EXIT_OK, True))
        check("...in this order, with the switch still off at each change on GitHub",
              world.log, [("put repos/acme/app/rulesets/7", False), ("patch repos/acme/app", False)])
        check("...the ruleset: Validator from the validator's app only, branches up to date, no "
              "approving review, and admins the only bypass, for pull requests",
              same_parts(managed(rs), merges_target(4242)), True)
        check("...as GitHub holds it: strict, 0 approving reviews, no code owner or last-push "
              "review, the admin role (5) for pull requests only",
              (rs["rules"][0]["parameters"]["strict_required_status_checks_policy"],
               rs["rules"][1]["parameters"]["required_approving_review_count"],
               rs["rules"][1]["parameters"]["require_code_owner_review"],
               rs["rules"][1]["parameters"]["require_last_push_approval"],
               rs["bypass_actors"]),
              (True, 0, False, False, [{"actor_id": 5, "actor_type": "RepositoryRole",
                                        "bypass_mode": "pull_request"}]))
        check("...every other check and rule kept as it was",
              (checks_of(rs), [r.get("type") for r in rs["rules"]],
               rs["rules"][1]["parameters"]["dismiss_stale_reviews_on_push"]),
              ([{"context": "Kit checks", "integration_id": 15368},
                {"context": "Validator", "integration_id": 4242}],
               ["required_status_checks", "pull_request", "non_fast_forward"], True))
        check("...auto-merge and squash allowed on the repository",
              (world.repo["allow_auto_merge"], world.repo["allow_squash_merge"]), (True, True))
        check("...the switch on, the repository listed, every other switch off, mode 600",
              (s["state"], allowed(world), s["approve"]["plans"], s["approve"]["pieces"],
               s["merge_repos"], stat.S_IMODE(os.stat(world.settings_path).st_mode)),
              (vsettings.READ, True, False, False, ["acme/app"], 0o600))
        record = read_ledger(ledger)[0]["merges"]["acme/app"]
        check("...the ledger records the ruleset as it was found, for `off`",
              (record["ruleset_id"], same_parts(managed(record["found"]), found)), (7, True))

        # 9d. A second run changes nothing, and says so.
        io2 = _FakeIO(world, ledger)
        check("merges on, a second run: nothing to do, no question, no change",
              (merges(io2, "on"), io2.asked, len(world.log),
               any("Nothing to do" in x for x in io2.said)), (EXIT_OK, [], 2, True))
        world.rulesets[7]["rules"][0]["parameters"]["strict_required_status_checks_policy"] = False
        merges(_FakeIO(world, ledger), "on")
        check("a later `on` sets the ruleset again, and keeps the record of the first",
              (len(world.log), same_parts(managed(
                  read_ledger(ledger)[0]["merges"]["acme/app"]["found"]), found)), (3, True))

        # 9e. A failure part way leaves the switch as it was, and says which: off, or already on.
        world, ledger = merges_world()
        step_settings(_FakeIO(world, ledger), world.settings_path, "acme/app", True)
        world.takes_put = False
        io = _FakeIO(world, ledger)
        check("merges on with the switch already on by hand, and the ruleset refused: it says the "
              "switch was already on, never that it is off",
              (merges(io, "on"), any("already on" in x for x in io.said),
               any("still off" in x for x in io.said), allowed(world)),
              (EXIT_HOLD, True, False, True))
        for label, setup, want_log in (
                ("GitHub refuses the ruleset", lambda w: setattr(w, "takes_put", False),
                 ["put"]),
                ("GitHub keeps no source app for the check",
                 lambda w: setattr(w, "keeps_source", False), ["put", "put"]),
                ("GitHub refuses the repository's settings",
                 lambda w: setattr(w, "takes_patch", False), ["put", "patch"])):
            world, ledger = merges_world()
            setup(world)
            io = _FakeIO(world, ledger)
            check("merges on, %s: stopped, and the switch is still off" % label,
                  (merges(io, "on"), [w.split()[0] for w, _a in world.log], allowed(world),
                   any("the switch is still off" in x for x in io.said)),
                  (EXIT_HOLD, want_log, False, True))
        check("...and with the ruleset left changed, the words say how to finish or undo",
              any("merges off" in x for x in io.said), True)

        # 9f. The warnings: nothing would merge until the validator decides and Stage E's own
        #     app is set. Each is a warning only; it still asks.
        t = 1_800_000_000
        for label, got, warns in (
                ("no ledger yet", {"state": "absent", "rows": [], "why": "x"}, True),
                ("a decision an hour ago", {"state": "read", "rows": [
                    {"at": "2027-01-15T07:00:00Z"}], "why": "read"}, False),
                ("the last decision eight days ago", {"state": "read", "rows": [
                    {"at": "2027-01-07T08:00:00Z"}], "why": "read"}, True),
                ("a ledger it cannot read", {"state": "unreadable", "rows": None, "why": "x"},
                 True)):
            check("the validator job, %s: %s" % (label, "warns" if warns else "no warning"),
                  job_warning(got, t) is not None, warns)
        check("Stage E's own app: warns with no login, not with one",
              (login_warning(vsettings.off(vsettings.ABSENT, "x")) is not None,
               login_warning(dict(vsettings.off(vsettings.ABSENT, "x"), state=vsettings.READ,
                                  stage_e_login="acme-stage-e[bot]"))), (True, None))

        # 9g. What blocks: classic protection only, no ruleset, settings it cannot trust, no app.
        def blocked(label, setup, words, state="on"):
            world, ledger = merges_world()
            setup(world)
            io = _FakeIO(world, ledger)
            code = merges(io, state)
            check("merges %s, %s: said, and nothing changes" % (state, label),
                  (code, io.asked, world.log, any(words in x for x in io.said)),
                  (EXIT_HOLD, [], [], True))
            return world
        blocked("the default branch protected only by classic branch protection",
                lambda w: (w.rulesets.clear(), setattr(w, "classic", True)),
                "classic branch protection")
        blocked("no ruleset at all", lambda w: w.rulesets.clear(), "no active ruleset")

        def bad_settings(w):
            os.makedirs(os.path.dirname(w.settings_path), mode=0o700)
            with open(w.settings_path, "w", encoding="utf-8") as fh:
                fh.write("{no")
            os.chmod(w.settings_path, 0o600)
        w = blocked("a settings file it cannot trust", bad_settings, "not obeyed")
        check("...and that file is left as it was",
              open(w.settings_path, encoding="utf-8").read(), "{no")
        world, ledger, key = fresh()
        world.settings_path = os.path.join(tmp, "v-none", "settings.json")
        io = _FakeIO(world, ledger)
        check("merges on with no validator app yet: said, and nothing changes",
              (merges(io, "on"), io.asked, world.log,
               any("pipeline_validator_app.py run" in x for x in io.said)),
              (EXIT_HOLD, [], [], True))

        # 9h. GitHub's capitals: the repository is listed in lower case.
        world, ledger = merges_world()
        check("merges on --repo Acme/App lists acme/app",
              (merges(_FakeIO(world, ledger), "on", repo="Acme/App"),
               vsettings.load(world.settings_path)["merge_repos"]), (EXIT_OK, ["acme/app"]))

        # 9i. off: the list first, then auto-merge off on each pull request in flight, then the
        #     ruleset's parts back as `on` found them. A check added since stays.
        world, ledger = merges_world()
        merges(_FakeIO(world, ledger), "on")
        doc = json.load(open(world.settings_path, encoding="utf-8"))
        doc["merge_repos"].append("acme/web")
        with open(world.settings_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        world.prs = [{"id": "PR_kwA1", "number": 12, "auto": True},
                     {"id": "PR_kwA2", "number": 13, "auto": False}]
        world.rulesets[7]["rules"][0]["parameters"]["required_status_checks"].append(
            {"context": "Union check", "integration_id": 15368})
        world.log = []
        before = (copy.deepcopy(world.rulesets[7]), copy.deepcopy(world.prs))
        io = _FakeIO(world, ledger, answer="no")
        check("merges off, answer 'no': nothing changed",
              (merges(io, "off"), len(io.asked), world.log, allowed(world),
               world.rulesets[7] == before[0], world.prs == before[1],
               "acme/app" in read_ledger(ledger)[0]["merges"]),
              (EXIT_NOT_YES, 1, [], True, True, True, True))
        io = _FakeIO(world, ledger)
        code = merges(io, "off")
        rs, s = world.rulesets[7], vsettings.load(world.settings_path)
        check("merges off: done, and checked again", (code, "Checked again:" in io.said),
              (EXIT_OK, True))
        check("...off the list before any change on GitHub",
              world.log, [("disable PR_kwA1", False), ("put repos/acme/app/rulesets/7", False)])
        check("...auto-merge off on the pull request in flight",
              [p["auto"] for p in world.prs], [False, False])
        check("...the parts back as `on` found them", same_parts(managed(rs), found), True)
        check("...and a check added since stays", [c["context"] for c in checks_of(rs)],
              ["Kit checks", "Union check"])
        check("...the other repository stays listed, and the merges switch as it was",
              (s["merge_repos"], s["approve"]["merges"]), (["acme/web"], True))
        check("...and the record is gone", "acme/app" in read_ledger(ledger)[0]["merges"], False)
        io2 = _FakeIO(world, ledger)
        check("merges off, a second run: nothing to do, no question, no change",
              (merges(io2, "off"), io2.asked, len(world.log),
               any("Nothing to do" in x for x in io2.said)), (EXIT_OK, [], 2, True))

        # 9j. off stops before the ruleset while a pull request could still merge on its own.
        for label, setup, code_want in (
                ("auto-merge cannot be turned off", lambda w: setattr(w, "disable_fails", True),
                 EXIT_HOLD),
                ("auto-merge still reads on after it was turned off",
                 lambda w: setattr(w, "disable_ignored", True), EXIT_HOLD),
                ("the pull requests cannot be read", lambda w: setattr(w, "prs_readable", False),
                 EXIT_UNCHECKED)):
            world, ledger = merges_world()
            merges(_FakeIO(world, ledger), "on")
            world.prs = [{"id": "PR_kwA1", "number": 12, "auto": True}]
            setup(world)
            world.log = []
            io = _FakeIO(world, ledger)
            check("merges off, %s: off the list, and the ruleset not put back" % label,
                  (merges(io, "off"), allowed(world), [w.split()[0] for w, _a in world.log
                                                       if w.startswith("put")]),
                  (code_want, False, []))

        # 9k. No credential anywhere, and nothing it ran merges, approves or labels: every run in
        #     section 9, on and off, each world's ledger and settings file, every call it made.
        ios = _FakeIO.made[first_io:]
        files = [p for w, led in worlds for p in (led, w.settings_path) if os.path.isfile(p)]
        everything = "\n".join("\n".join(i.said) for i in ios) + "".join(
            open(p, encoding="utf-8").read() for p in files) + json.dumps(
            [[w.log for w, _l in worlds], [i.ran for i in ios]])
        check("merges: every run said something, and the check reads them all",
              (len(ios) > 20, len(files) > 10, "Checked again:" in everything), (True, True, True))
        check("merges: no key and no token in anything it said, wrote or sent, on any run",
              [m for m in (KEY_MARK, "BEGIN TEST FIXTURE", TOKEN_T) if m in everything], [])
        check("merges: nothing it asked GitHub for, on any run, merges, approves, reviews or labels",
              ([a for i in ios for a in i.ran if any(w in " ".join(map(str, a)).lower()
                                                     for w in ("merge", "approve", "review",
                                                               "label"))],
               [q for w, _l in worlds for q in w.graphql if re.search(
                   r"enablePullRequestAutoMerge|mergePullRequest|Review|Label", q)]), ([], []))
        check("merges: the command line is checked before anything is read",
              [main(argv, io=_FakeIO(world, ledger)) for argv in (
                  ["merges", "on", "--repo", "acme app"],
                  ["merges", "on", "--repo", "acme/..", "--settings-file", world.settings_path],
                  ["merges", "on", "--repo", "acme/app", "--settings-file", "settings.json"])],
              [EXIT_USAGE] * 3)

    # 8. The one write to a ruleset goes through one function, for one ruleset path.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    check("one PUT, in one place, of one ruleset", (above.count('"-X", "PUT"'),
                                                  bool(RULESET_PATH_RE.fullmatch(
                                                      "repos/a/b/rulesets/7")),
                                                  bool(RULESET_PATH_RE.fullmatch(
                                                      "repos/a/b/branches/main/protection"))),
          (1, True, False))
    check("no API path in it merges, approves, labels or touches branch protection",
          [w for w in ("/merge", "/reviews", "/labels", "/pulls", "/protection") if w in above],
          [])
    check("one PATCH, in one place, of one repository's merge settings",
          (above.count('"-X", "PATCH"'), bool(REPO_PATH_RE.fullmatch("repos/a/b")),
           bool(REPO_PATH_RE.fullmatch("repos/a/b/rulesets/7")),
           _raises(lambda: gh_patch_repo(_FakeIO(_World(), "x"), "a/b", {"name": "x"}))),
          (1, True, False, True))
    check("its one change through GraphQL turns auto-merge off: none merges, approves, labels or "
          "changes branch protection", sorted(set(re.findall(r"([A-Za-z]+)\(input:", above))),
          ["disablePullRequestAutoMerge"])

    if failures:
        for line in failures:
            print(line)
        print("validator app selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator app selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
