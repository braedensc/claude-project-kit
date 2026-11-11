#!/usr/bin/env python3
"""The validator's own GitHub app, and its check made a required one: the owner's script (KIT-541).
Design: docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md, "The ruleset
changes". Guide: docs/VALIDATOR-OPERATOR.md, "The check on the pull request".

    python3 scripts/pipeline_validator_app.py run --repo OWNER/NAME --key-file ~/.validator/app.pem
                                                   [--name NAME] [--org ORG] [--ruleset ID]
    python3 scripts/pipeline_validator_app.py verify --repo OWNER/NAME
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

THE LEDGER is ~/.validator-app/state.json under your home, mode 600: the app's id and slug, the key
file's path and where the app is installed. Fields only, never a key or a token. The validator's
job reads the app's id from it.

WHAT IT NEVER DOES. Run under a model: `run` and `verify` are refused in an agent's environment
before they read anything. Print, log or store a key or a token anywhere but the key file. Change a
ruleset without your yes, or one it was not asked about.

Exit: 0 done, or every piece holds · 1 a piece does not hold, or a step failed after your yes · 2
the command line needs fixing; nothing changed · 3 refused (an agent's environment, or root);
nothing changed · 4 something could not be checked; nothing changed · 10 you did not type yes;
nothing changed.
"""
import argparse
import copy
import json
import os
import re
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# One definition each: the agent-env markers, the helper's calls, Stage E's owner-script pieces
# (its IO seam, its signer, its read-only `gh` call, its app pages), and the check's name.
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
import pipeline_github_app as gha  # noqa: E402
import pipeline_stage_e_app as stage_e_app  # noqa: E402
from pipeline_validator_check import CHECK_NAME  # noqa: E402

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
    """One PUT of one ruleset, with your own `gh` login: the only write this script makes to GitHub
    that is not the app's own. (status, doc). The body is on stdin; the token stays in `gh`."""
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


def changed(ruleset, app_id):
    """The ruleset's PUT body with the Validator check required from this app, and 0 approving
    reviews. Every other rule, check and setting is kept as it is."""
    rules = []
    for rule in copy.deepcopy(ruleset.get("rules") or []):
        params = rule.setdefault("parameters", {})
        if rule.get("type") == "required_status_checks":
            keep = [c for c in params.get("required_status_checks") or []
                    if c.get("context") != CHECK_NAME]
            params["required_status_checks"] = keep + [{"context": CHECK_NAME,
                                                        "integration_id": app_id}]
        elif rule.get("type") == "pull_request":
            params["required_approving_review_count"] = 0
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
    try:
        gh_put_ruleset(io, path, body_of(ruleset))
        undone = "ruleset %d was put back as it was" % ruleset["id"]
    except gha.GitHubAppError:
        undone = "putting ruleset %d back failed too: check it on GitHub" % ruleset["id"]
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

    def __init__(self, approvals=1, keeps_source=True, takes_put=True, extra_ruleset=False):
        self.app = None              # {"id", "slug"} once GitHub made it
        self.installed = False
        self.keeps_source, self.takes_put = keeps_source, takes_put
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


class _Flow(object):
    def __init__(self, world, code="abc_123"):
        self.world, self.code_ = world, code
        self.url = "http://127.0.0.1:1/start"

    def wait(self, seconds):
        return self.code_

    def close(self):
        pass


class _FakeIO(IO):
    def __init__(self, world, ledger_path, answer="yes", env=None, uid=501):
        self.world, self._ledger, self.answer = world, ledger_path, answer
        self._env, self._uid = env if env is not None else {}, uid
        self.said, self.asked, self.opened, self.t = [], [], [], 1_800_000_000

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
        if argv[:3] == ["openssl", "dgst", "-sha256"]:
            return _Out(0, b"s" * 256)
        if argv[:2] == ["gh", "api"] and argv[2:4] == ["user", "--jq"]:
            return _Out(0, b"acme\n")
        if argv[:4] == ["gh", "api", "-X", "PUT"]:
            body = json.loads(input.decode("utf-8"))
            w.puts.append((argv[4], body))
            if not w.takes_put:
                return _Out(1, b'{"message": "Validation Failed"}')
            rid = int(argv[4].rsplit("/", 1)[1])
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
                return _Out(0, json.dumps({"default_branch": "main"}).encode("utf-8"))
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

    if failures:
        for line in failures:
            print(line)
        print("validator app selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator app selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
