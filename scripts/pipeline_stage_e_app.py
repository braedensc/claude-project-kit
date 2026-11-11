#!/usr/bin/env python3
"""Stage E's own GitHub app: made once by its owner, then proven (KIT-578).

    python3 scripts/pipeline_stage_e_app.py run --conf stage-e.conf --test-pr OWNER/NAME#N
                                                [--name NAME] [--org ORG]
    python3 scripts/pipeline_stage_e_app.py verify --conf stage-e.conf
    python3 scripts/pipeline_stage_e_app.py --selftest

WHY. Without its own app, Stage E posts every review as the GitHub token's own login: yours.
Every session on this Mac can post as that login too, so a review comment proves nothing about
who wrote it, and the validator trusts none of them. An app has a bot login of its own,
`<slug>[bot]`, and its key is a file only the role account can read.

Run it in a terminal, as yourself. It checks what is done, shows what it will do, asks for one
typed `yes`, does it, and checks again. docs/STAGE-E-OPERATOR.md, Step 7, says the same.

WHAT `run` DOES, IN ORDER, skipping what is done
  1. Opens GitHub's page to create the app. You click GitHub's own Create button: GitHub lets
     nothing else make an app. The app may write pull-request comments, and nothing else.
  2. Records the app in the ledger, then writes its private key straight to the role account's
     ~/.stage-e/github-app.pem: as that account, mode 600, on stdin. GitHub hands the key over
     once. It is never printed, never an argument, and never kept anywhere else.
  3. Opens GitHub's page to install the app, and waits until GitHub shows it on every
     REVIEW_REPOS repository.
  4. Posts one test comment as the app on the pull request you name, reads it back, and checks
     that its author is the app's bot.
  5. Adds GITHUB_APP_ID, GITHUB_APP_LOGIN and GITHUB_APP_KEY_FILE to the conf: the ones it does
     not set yet.
Then run the installer once more. It writes the app into the review poller's config, and its
`github-app` row checks all of this on every pass. `verify` makes the same checks, read-only.

THE LEDGER is ~/.stage-e-app/state.json under YOUR home, mode 600: the app's id, slug, login,
account and key file, where it is installed, and the test comment. Fields only, never a key or a
token. The installer's `github-app` step reads the test comment from it.

WHAT IT NEVER DOES. Run under a model: `run` is refused in an agent's environment before it reads
anything, and `verify` there signs nothing. Print, log or pass as an argument a key or a token.
Call GitHub anywhere but the helper's fixed paths (pipeline_github_app.ENDPOINTS), none of which
can merge, approve, label or change a setting. Ask for your password twice: `sudo -v` once,
before the first `sudo`, and every `sudo` after it is `sudo -n`, which never asks.

Exit: 0 done, or every piece holds · 1 a piece does not hold, or a step failed after your yes
(the lines say which, and what was done) · 2 the conf or the command line needs fixing; nothing
changed · 3 refused (an agent's environment, or root); nothing changed · 4 something could not
be checked · 5 no administrator access; nothing changed · 10 you did not type yes; nothing
changed.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# The agent-env markers are IMPORTED, never copied, so every script in the kit means the same
# thing by "an agent's environment".
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
import pipeline_github_app as gha  # noqa: E402
# The installer's conf reader, the key file's probe and the three keys' names: one definition of
# each, so this script and the installer's `github-app` row cannot disagree.
import pipeline_stage_e_setup as setup  # noqa: E402

SCHEMA = "stage-e-app/1"
LEDGER = "~/.stage-e-app/state.json"
KEY_FILE = ".stage-e/github-app.pem"     # under the role account's home
PERMISSIONS = {"pull_requests": "write"}
DESCRIPTION = "Stage E's reviews. It writes pull-request comments, and nothing else."
NAME_MAX = 34
WAIT_SECONDS = 600         # for GitHub's answer after Create, and for the installations
POLL_SECONDS = 5
SIGN_AGAIN_SECONDS = 300   # a signed token lives nine minutes; the wait above is ten
TEST_COMMENT = ("Stage E identity check: this comment is posted by Stage E's own GitHub app. "
                "It changes nothing.")
TEST_PR_RE = re.compile(r"^([A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100})#([1-9][0-9]{0,9})$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
# What a key or a token looks like. The ledger refuses any value with one of these shapes.
SECRET_RE = re.compile(r"-----BEGIN|gh[pousr]_[A-Za-z0-9]{10,}|github_pat_|eyJ[A-Za-z0-9_-]{8,}\.")
EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_NOPRIV, EX_DECLINED = \
    0, 1, 2, 3, 4, 5, 10
DONE, TODO, FAILED, UNKNOWN = "done", "to do", "FAILED", "UNKNOWN"
PIECES = ("app", "key file", "app on GitHub", "installed", "test comment", "conf")
SCRIPT = "scripts/pipeline_stage_e_app.py"
INSTALLER = "scripts/pipeline_stage_e_setup.py"


class IO(object):
    """Everything this script touches outside itself, so the selftest can stand in for each."""

    def env(self):
        return os.environ

    def uid(self):
        return os.geteuid()

    def say(self, text=""):
        print(text)
        sys.stdout.flush()

    def ask(self, question):
        try:
            return input(question)
        except EOFError:
            return ""

    def sudo_validate(self):
        """`sudo -v`: its password prompt on your terminal, nothing captured."""
        try:
            return subprocess.run(["sudo", "-v"]).returncode
        except OSError:
            return 127

    def run(self, argv, input=None, capture_output=True, timeout=60, cwd="/"):
        """One command, bytes in and out. It starts at `/`: the role account cannot stand in
        your folder. Also openssl's runner, for the helper's signer."""
        return subprocess.run(argv, input=input, capture_output=capture_output, timeout=timeout,
                              cwd=cwd)

    def call(self, method, path, auth=None, body=None):
        return gha._call(method, path, auth=auth, body=body)

    def flow(self, doc, org):
        return gha.ManifestFlow(doc, org=org)

    def open_url(self, url):
        """The page in your browser: `open` on macOS. The address is printed either way."""
        self.say("   %s" % url)
        if sys.platform == "darwin":
            try:
                subprocess.run(["open", url], capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass

    def now(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)

    def role_home(self, account):
        import pwd
        return pwd.getpwnam(account).pw_dir

    def ledger_path(self):
        return os.path.expanduser(LEDGER)


class NoPrivilege(Exception):
    """Exit 5: `sudo -v` was refused, so nothing after it was attempted."""


class StepFailed(Exception):
    """Exit 1: a step after your yes did not finish. Its words say what was done."""


class Window(object):
    """Where `run` stands between GitHub making the app and the ledger recording it. An app made
    there and never recorded has lost its key, and a rerun would make a second one, so an
    interrupt there names the app to delete. `page_open`: GitHub's create page is open, so an
    app may be made at any moment. `made`: GitHub has handed the app over, and the ledger does
    not record it yet. Both are cleared once the ledger records it."""

    def __init__(self, org=None):
        self.org, self.page_open, self.made = org, False, None


def write_ledger_or_fail(ledger_path, ledger, then):
    """write_ledger, or StepFailed saying why it could not, then `then`: what was already done,
    and what to do about it. Never a traceback after your yes."""
    try:
        write_ledger(ledger_path, ledger)
    except (OSError, ValueError) as exc:
        raise StepFailed("the ledger %s could not be written (%s). %s"
                         % (ledger_path, getattr(exc, "strerror", None) or exc, then))


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class Sudo(object):
    """`sudo -v` once, before the first `sudo`, with the reason on the line before it."""

    def __init__(self, io):
        self.io, self.held = io, False

    def ensure(self):
        if self.held:
            return
        self.io.say("")
        self.io.say("Your Mac password, once. The key file is the role account's, so whatever "
                    "touches it runs as that account, through sudo. Every sudo after this one is "
                    "`sudo -n`, which never asks.")
        if self.io.sudo_validate() != 0:
            raise NoPrivilege("NO ADMINISTRATOR ACCESS: `sudo -v` was refused, so nothing after it "
                              "was attempted. Run this again and give your Mac password.")
        self.held = True


# --------------------------------------------------------------------------- the ledger
def read_ledger(path):
    """(doc, None, None) for the ledger at `path` ({} when there is none); (None, what is wrong,
    None) when it is not this script's ledger; (None, None, why not) when it cannot be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}, None, None
    except ValueError:
        return None, "%s is not JSON" % path, None
    except OSError as exc:
        return None, None, "%s could not be read (%s)" % (path, exc.strerror or "error")
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None, "%s is not this script's ledger (%s)" % (path, SCHEMA), None
    return doc, None, None


def write_ledger(path, doc):
    """The ledger, whole, mode 600 in a mode-700 folder, by a rename, so a reader never sees half
    of it. Fields only: a value shaped like a key or a token is refused, and nothing is written."""
    text = json.dumps(dict(doc, schema=SCHEMA), indent=2, sort_keys=True) + "\n"
    if SECRET_RE.search(text):
        raise ValueError("the ledger holds fields only, and a value looked like a key or a token; "
                         "nothing was written")
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def ledger_app(ledger):
    """(app, None) for the app the ledger records; (None, None) when it records none; (None, what
    is wrong) when its record is not whole."""
    keys = ("app_id", "slug", "login", "account", "key_file")
    if not any(k in ledger for k in keys):
        return None, None
    app = dict((k, ledger.get(k)) for k in keys)
    try:
        login = gha.bot_login(app["slug"])
    except gha.GitHubAppError:
        login = None
    if (isinstance(app["app_id"], bool) or not isinstance(app["app_id"], int)
            or app["app_id"] < 1 or login is None or app["login"] != login
            or not isinstance(app["account"], str) or not app["account"]
            or not isinstance(app["key_file"], str) or not os.path.isabs(app["key_file"])):
        return None, "its record of the app is not whole"
    return app, None


def ledger_comment(ledger):
    """The test comment the ledger records, as {repo, number, id}, or None."""
    rec = ledger.get("test_comment")
    if not isinstance(rec, dict) or not TEST_PR_RE.fullmatch("%s#%s" % (rec.get("repo"),
                                                                       rec.get("number"))):
        return None
    if isinstance(rec.get("id"), bool) or not isinstance(rec.get("id"), int) or rec["id"] < 1:
        return None
    return {"repo": rec["repo"], "number": rec["number"], "id": rec["id"]}


# --------------------------------------------------------------------------- the conf
def conf_values(app):
    return {"GITHUB_APP_ID": str(app["app_id"]), "GITHUB_APP_LOGIN": app["login"],
            "GITHUB_APP_KEY_FILE": app["key_file"]}


def conf_with_app(text, want):
    """(new text, conflicts): `text` with each KEY=value in `want` it does not set yet. A key set to
    the same value is left alone; an empty `KEY=` line, as the example has, is filled where it
    stands; a key the conf does not name is added at the end. A key set to another value is a
    conflict, and the text comes back unchanged."""
    lines = text.split("\n")
    conflicts, where = [], {}
    for key, value in want.items():
        live = [i for i, l in enumerate(lines) if re.match(r"^\s*%s\s*=" % re.escape(key), l)]
        if len(live) > 1:
            conflicts.append("%s is set on %d lines" % (key, len(live)))
            continue
        if live:
            have = lines[live[0]].split("=", 1)[1].strip()
            if have == value:
                continue
            if have:
                conflicts.append("%s is already %s, not %s" % (key, have, value))
                continue
        where[key] = live[0] if live else None
    if conflicts:
        return text, conflicts
    added = []
    for key, value in want.items():
        if key not in where:
            continue
        if where[key] is None:
            added.append("%s=%s" % (key, value))
        else:
            lines[where[key]] = "%s=%s" % (key, value)
    if added:
        if lines and lines[-1] == "":
            lines.pop()
        lines += ["", "# Stage E's own GitHub app, added by %s." % SCRIPT] + added + [""]
    return "\n".join(lines), []


def write_text_keeping_mode(path, text):
    """In place of the old file, keeping its mode: a temporary file beside it, then a rename."""
    mode = os.stat(path).st_mode & 0o777
    tmp = "%s.stage-e-app.%d" % (path, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_conf(io, path):
    """(conf, EX_OK), or (None, EX_USAGE) with every problem said."""
    try:
        conf, errors = setup.load_conf(path)
    except setup.SetupError as exc:
        io.say("COULD NOT RUN: %s" % str(exc).splitlines()[0])
        return None, EX_USAGE
    if errors:
        io.say("%s has %d problem(s). Every one of them:" % (path, len(errors)))
        for e in errors:
            io.say("  - " + e)
        return None, EX_USAGE
    return conf, EX_OK


# --------------------------------------------------------------------------- GitHub, read
def gh_read(io):
    """A `call` for the helper's readers that asks with YOUR `gh` login, as the installer does, so
    a comment on a private repository can be read too. GET only, and with no credential."""
    def call(method, path, auth=None, body=None):
        if method != "GET" or body is not None or auth is not None:
            raise gha.GitHubAppError("this check only reads, with your own `gh` login")
        try:
            out = io.run(["gh", "api", path.lstrip("/")], timeout=60)
        except (OSError, subprocess.SubprocessError):
            raise gha.GitHubAppError("`gh` could not be run")
        if out.returncode != 0:
            raise gha.GitHubAppError("`gh api` could not read it (exit %d)" % out.returncode)
        try:
            return 200, json.loads(out.stdout.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise gha.GitHubAppError("`gh api` did not answer with JSON")
    return call


class Signer(object):
    """The app's own signed token: signed by openssl as the role account, through `sudo -n`, and
    signed again before it ages out."""

    def __init__(self, io, app_id, key_file, account):
        self.io, self.app_id, self.key_file, self.account = io, app_id, key_file, account
        self._jwt, self._at = None, None

    def jwt(self):
        now = self.io.now()
        if self._jwt is None or now - self._at >= SIGN_AGAIN_SECONDS:
            self._jwt = gha.make_jwt(self.app_id, gha.openssl_signer(
                self.key_file, account=self.account, run=self.io.run), now=now)
            self._at = now
        return self._jwt


def default_name(io):
    """`stage-e-<your GitHub login>`, cut to 34 characters, or None when `gh` cannot say."""
    try:
        out = io.run(["gh", "api", "user", "--jq", ".login"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    login = out.stdout.decode("utf-8", "replace").strip() if out.returncode == 0 else ""
    if not LOGIN_RE.fullmatch(login):
        return None
    return ("stage-e-" + login)[:NAME_MAX].rstrip("-")


def apps_page(org):
    return ("https://github.com/organizations/%s/settings/apps" % org if org
            else "https://github.com/settings/apps")


def app_page(org, slug):
    """One app's own settings page, where it is deleted."""
    return "%s/%s" % (apps_page(org), slug)


# --------------------------------------------------------------------------- the checks
def measure(io, conf, ledger, ledger_path, sudo, agent):
    """[(piece, state, words)], one row per piece in PIECES. Read-only. With `agent` set, nothing
    runs as the role account and the key signs nothing: those rows are UNKNOWN."""
    rows = []
    account = conf["ROLE_ACCOUNT"]
    repos = setup.split_list(conf["REVIEW_REPOS"])
    in_conf = dict((k, (conf.get(k) or "").strip()) for k in setup.GITHUB_APP_KEYS)
    app, wrong = ledger_app(ledger)
    if app is None:
        rows.append(("app", FAILED if wrong else TODO, (
            "%s: %s. Move it aside, delete the app on GitHub if there is one (%s), and run this "
            "again" % (ledger_path, wrong, apps_page(None))) if wrong else "none is recorded yet"))
        rows += [(p, TODO, "comes with the app") for p in PIECES[1:5]]
        if any(in_conf.values()):
            rows.append(("conf", FAILED, "it names app %s, which %s does not record. Empty the three "
                         "GITHUB_APP_* lines, or bring back the ledger that recorded it"
                         % (in_conf["GITHUB_APP_ID"] or "?", ledger_path)))
        else:
            rows.append(("conf", TODO, "the three GITHUB_APP_* keys are added last"))
        return rows
    if app["account"] != account:
        rows.append(("app", FAILED, "its key is %s's, but the conf's ROLE_ACCOUNT is %s"
                     % (app["account"], account)))
    else:
        rows.append(("app", DONE, "app %s, %s, recorded in %s" % (app["app_id"], app["slug"],
                                                                  ledger_path)))
    key_file = app["key_file"]
    if agent:
        rows.append(("key file", UNKNOWN, "not looked at in an agent's environment: it is read as "
                     "%s, through sudo" % account))
    else:
        sudo.ensure()
        argv = ["sudo", "-n", "-u", account, "--", "/bin/sh", "-c",
                "cd / && " + setup.APP_KEY_PROBE_SH % shlex.quote(key_file)]
        try:
            out = io.run(argv, timeout=60)
            text, rc = out.stdout.decode("utf-8", "replace"), out.returncode
        except (OSError, subprocess.SubprocessError):
            text, rc = "", 127
        problems, could_not = setup.app_key_problems(text if rc == 0 else "", key_file, account)
        if problems:
            lost = any("finds no file" in p for p in problems)
            rows.append(("key file", FAILED, "; ".join(problems) + (
                ". GitHub hands an app's key over once, so this app cannot sign any more. Delete "
                "it on GitHub (%s), delete %s, then run this again" % (apps_page(None), ledger_path)
                if lost else "")))
        elif could_not:
            rows.append(("key file", UNKNOWN, "it could not be looked at as %s (exit %d)"
                         % (account, rc)))
        else:
            rows.append(("key file", DONE, "%s, mode 600, %s's own" % (key_file, account)))
    # The installer's read_only_call: GET alone, and each answer's status kept, so a 401 is
    # GitHub's refusal of the key and never "could not ask".
    statuses = []
    kept = setup.read_only_call(io.call, statuses)
    jwt, confirmed = None, False
    if agent:
        rows.append(("app on GitHub", UNKNOWN, "not asked in an agent's environment: the key "
                     "signs nothing there, since a signed token acts as the app"))
    else:
        try:
            jwt = Signer(io, app["app_id"], key_file, account).jwt()
            info = gha.app_info(jwt, call=kept)
        except gha.GitHubAppError as exc:
            if statuses[-1:] == [401]:
                rows.append(("app on GitHub", FAILED, "GitHub refused the token the key file "
                             "signed (HTTP 401): it is not app %s's key, the app was deleted, or "
                             "this Mac's clock is wrong" % app["app_id"]))
            else:
                rows.append(("app on GitHub", UNKNOWN, "GitHub could not be asked: %s" % exc))
        else:
            try:
                named = gha.bot_login(info.get("slug"))
            except gha.GitHubAppError:
                named = None
            if info["id"] != app["app_id"] or named != app["login"]:
                rows.append(("app on GitHub", FAILED, "GitHub says the key is app %s, %s; the "
                             "ledger records app %s, %s" % (info["id"], named, app["app_id"],
                                                            app["login"])))
            else:
                confirmed = True
                rows.append(("app on GitHub", DONE, "app %s posts as %s" % (app["app_id"],
                                                                         app["login"])))
    if not confirmed:
        rows.append(("installed", UNKNOWN, "not asked: GitHub did not confirm the app"))
    else:
        missing, trouble = [], None
        for repo in repos:
            try:
                if gha.installation_for(repo, jwt, call=kept) is None:
                    missing.append(repo)
            except gha.GitHubAppError as exc:
                trouble = "%s (%s)" % (repo, exc)
        if trouble:
            rows.append(("installed", UNKNOWN, "GitHub could not be asked about %s" % trouble))
        elif missing:
            rows.append(("installed", TODO, "not yet on %s" % ", ".join(missing)))
        else:
            rows.append(("installed", DONE, "on %s" % ", ".join(repos)))
    rec = ledger_comment(ledger)
    if rec is None:
        rows.append(("test comment", TODO, "none is recorded yet"))
    else:
        where = "comment %d on %s#%d" % (rec["id"], rec["repo"], rec["number"])
        try:
            got = gha.read_comment(rec["repo"], rec["id"], call=gh_read(io))
        except gha.GitHubAppError as exc:
            rows.append(("test comment", UNKNOWN, "%s could not be read back: %s" % (where, exc)))
        else:
            if got.get("author") == app["login"] and got.get("type") == "Bot":
                rows.append(("test comment", DONE, "%s, posted by %s" % (where, app["login"])))
            else:
                rows.append(("test comment", TODO, "%s was posted by %s (%s), not by %s (Bot)"
                             % (where, got.get("author"), got.get("type"), app["login"])))
    want = conf_values(app)
    differ = [k for k in setup.GITHUB_APP_KEYS if in_conf[k] and in_conf[k] != want[k]]
    if differ:
        rows.append(("conf", FAILED, "it sets %s, not this app's %s. Empty those lines, then run "
                     "this again" % (", ".join("%s=%s" % (k, in_conf[k]) for k in differ),
                                     ", ".join(want[k] for k in differ))))
    elif all(in_conf[k] == want[k] for k in setup.GITHUB_APP_KEYS):
        rows.append(("conf", DONE, "GITHUB_APP_ID, GITHUB_APP_LOGIN and GITHUB_APP_KEY_FILE "
                     "name this app"))
    else:
        rows.append(("conf", TODO, "the three GITHUB_APP_* keys are added"))
    return rows


def worst(rows):
    states = [s for _p, s, _w in rows]
    for state in (FAILED, UNKNOWN, TODO):
        if state in states:
            return state
    return DONE


def show(io, rows):
    for piece, state, words in rows:
        io.say("  %-14s %-8s %s" % (piece, state, words))


# --------------------------------------------------------------------------- the steps
def step_create(io, conf, org, name, ledger, ledger_path, key_file, window):
    """Steps 1 and 2: GitHub makes the app from the manifest, hands its key over once, and the key
    goes straight to its file. The ledger records the app FIRST: GitHub has made it, so whatever
    fails next must not leave an app nobody knows of. Until it does, `window` says so, and a
    failure or an interrupt there names the app to delete."""
    account = conf["ROLE_ACCOUNT"]
    doc = gha.manifest(name, conf["KIT_REPO_URL"], PERMISSIONS, description=DESCRIPTION)
    io.say("")
    io.say("1. GitHub's page to create the app %s. Click GitHub's own Create button there. This "
           "waits up to %d s." % (name, WAIT_SECONDS))
    flow = io.flow(doc, org)
    try:
        window.page_open = True
        io.open_url(flow.url)
        code = flow.wait(WAIT_SECONDS)
    finally:
        flow.close()
    if not code:
        raise StepFailed("no answer came back from GitHub within %d s, so nothing was written. If "
                         "you did click Create, GitHub made an app this script never received: "
                         "delete it at %s, then run this again." % (WAIT_SECONDS, apps_page(org)))
    try:
        made = gha.convert(code, call=io.call)
    except gha.GitHubAppError as exc:
        raise StepFailed("GitHub did not hand the app over (%s), so nothing was written. If it "
                         "made one, delete it at %s, then run this again." % (exc, apps_page(org)))
    pem = made.pop("pem")
    window.made = {"app_id": made["id"], "slug": made["slug"]}
    app = {"app_id": made["id"], "slug": made["slug"], "login": gha.bot_login(made["slug"]),
           "account": account, "key_file": key_file}
    for stale in ("installation_ids", "test_comment"):
        ledger.pop(stale, None)
    ledger.update(app)
    ledger["installation_ids"] = {}
    try:
        write_ledger_or_fail(ledger_path, ledger, (
            "GitHub made app %s (%s), and its key was not written, so this app cannot be used: "
            "GitHub hands an app's key over once. Delete it at %s, fix what stopped the ledger, "
            "then run this again." % (app["app_id"], app["slug"], app_page(org, app["slug"]))))
    except BaseException:
        pem = made = None     # dropped: no key is written for an app the ledger does not record
        raise
    window.page_open, window.made = False, None
    io.say("   GitHub made app %s; its comments post as %s. Recorded in %s."
           % (app["app_id"], app["login"], ledger_path))
    try:
        out = io.run(gha.write_key_argv(account, key_file), input=pem.encode("utf-8"), timeout=60)
        rc, err = out.returncode, (out.stderr or b"").decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        rc, err = 127, type(exc).__name__
    finally:
        pem = made = None     # dropped: nothing here keeps the key
    if rc != 0:
        raise StepFailed("app %s exists, but its key could not be written to %s as %s (exit %d: "
                         "%s). GitHub hands an app's key over once, so this app cannot sign. "
                         "Delete it at %s, delete %s, then run this again."
                         % (app["app_id"], key_file, account, rc, err[:160] or "no reason given",
                            app_page(org, app["slug"]), ledger_path))
    io.say("2. Its key is in %s, as %s, mode 600. It was never printed." % (key_file, account))
    return app


def step_install(io, conf, app, ledger, ledger_path, signer):
    """Step 3: GitHub's page to install the app, then a look every 5 s, up to 600 s, until GitHub
    shows it on every reviewed repository."""
    repos = setup.split_list(conf["REVIEW_REPOS"])
    url = "https://github.com/apps/%s/installations/new" % app["slug"]
    io.say("")
    io.say("3. GitHub's page to install the app. Pick %s. This waits until GitHub shows the app on "
           "each, up to %d s." % (", ".join(repos), WAIT_SECONDS))
    io.open_url(url)
    deadline = io.now() + WAIT_SECONDS
    while True:
        try:
            jwt = signer.jwt()
        except gha.GitHubAppError as exc:
            raise StepFailed("the key file could not sign as %s: %s" % (app["account"], exc))
        ids, missing, trouble = {}, [], ""
        for repo in repos:
            try:
                got = gha.installation_for(repo, jwt, call=io.call)
            except gha.GitHubAppError as exc:
                got, trouble = None, "; GitHub could not be asked about %s (%s)" % (repo, exc)
            if got is None:
                missing.append(repo)
            else:
                ids[repo] = got
        if ids != ledger.get("installation_ids"):
            ledger["installation_ids"] = ids
            write_ledger_or_fail(ledger_path, ledger, (
                "App %s is made and recorded, and its key is in place. Fix what stopped the "
                "ledger, then run this again: what is done stays done." % app["app_id"]))
        if not missing:
            io.say("   It is on %s." % ", ".join(repos))
            return
        if io.now() >= deadline:
            raise StepFailed("after %d s the app is not on %s%s. Install it there from %s, then "
                             "run this again: what is done stays done."
                             % (WAIT_SECONDS, ", ".join(missing), trouble, url))
        io.sleep(POLL_SECONDS)


def step_comment(io, app, ledger, ledger_path, signer, repo, number):
    """Step 4: one comment as the app, read back. Its author must be the app's bot."""
    io.say("")
    io.say("4. One test comment as the app on %s#%d." % (repo, number))
    try:
        jwt = signer.jwt()
        installation = gha.installation_for(repo, jwt, call=io.call)
        if installation is None:
            raise StepFailed("the app is not installed on %s, so it cannot post there. Install it "
                             "there, then run this again." % repo)
        token = gha.installation_token(installation, jwt, call=io.call)
        try:
            posted = gha.post_comment(repo, number, TEST_COMMENT, token, call=io.call)
            got = gha.read_comment(repo, posted["id"], token=token, call=io.call)
        finally:
            token = None      # dropped: it lives an hour, and nothing here keeps it
    except gha.GitHubAppError as exc:
        raise StepFailed("the test comment could not be posted and read back: %s" % exc)
    ledger["test_comment"] = {"repo": repo, "number": number, "id": posted["id"],
                              "author": got.get("author"), "at": _iso(io.now())}
    write_ledger_or_fail(ledger_path, ledger, (
        "Test comment %d is posted on %s#%d, and is not recorded. Fix what stopped the ledger, "
        "then run this again: it posts a new one." % (posted["id"], repo, number)))
    if got.get("author") != app["login"] or got.get("type") != "Bot":
        raise StepFailed("test comment %d on %s#%d was posted by %s (%s), not by %s (Bot), so it "
                         "proves nothing about the app. Nothing was added to the conf."
                         % (posted["id"], repo, number, got.get("author"), got.get("type"),
                            app["login"]))
    io.say("   Comment %d is posted by %s, a Bot." % (posted["id"], app["login"]))


def step_conf(io, conf_path, app):
    """Step 5: the three keys, added to the conf where it does not set them yet. Every step
    before it is done by now, so a conf that cannot be read or written fails here with the lines
    to add by hand, and never with the advice to delete a working app."""
    want = conf_values(app)
    real = os.path.realpath(conf_path)
    lines = ", ".join("%s=%s" % kv for kv in want.items())

    def could_not(what, exc):
        return StepFailed("app %s is made and recorded; only the conf is left, and %s could not be "
                          "%s (%s). Fix that, then run this again, or add these lines to it "
                          "yourself: %s." % (app["app_id"], conf_path, what,
                                             getattr(exc, "strerror", None) or exc, lines))
    try:
        with open(real, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, ValueError) as exc:
        raise could_not("read", exc)
    new, conflicts = conf_with_app(text, want)
    if conflicts:
        raise StepFailed("%s: %s. Nothing was written to it." % (conf_path, "; ".join(conflicts)))
    if new != text:
        try:
            write_text_keeping_mode(real, new)
        except OSError as exc:
            raise could_not("written", exc)
    try:
        conf, errors = setup.load_conf(real)
    except setup.SetupError as exc:
        raise could_not("read back", exc)
    if errors or any(conf.get(k) != v for k, v in want.items()):
        raise StepFailed("%s does not read back with the app's keys: %s"
                         % (conf_path, "; ".join(errors) or "a value differs"))
    io.say("")
    io.say("5. %s names the app: %s." % (conf_path, ", ".join("%s=%s" % kv for kv in want.items())))


def plan_of(rows):
    state = dict((p, s) for p, s, _w in rows)
    steps = []
    if state["app"] == TODO:
        steps.append("create")
    for piece, step in (("installed", "install"), ("test comment", "comment"), ("conf", "conf")):
        if state[piece] == TODO:
            steps.append(step)
    return steps


def plan_lines(steps, name, ledger_path, key_file, account, repos, test, conf_path):
    lines = []
    if "create" in steps:
        lines.append("Open GitHub's page to create the app %s. You click GitHub's own Create "
                     "button: GitHub lets nothing else make an app. It may write pull-request "
                     "comments, and nothing else." % name)
        lines.append("Record the app in %s, then write its private key to %s as %s, mode 600. "
                     "The key is never printed, and kept nowhere else." % (ledger_path, key_file,
                                                                           account))
    if "install" in steps:
        lines.append("Open GitHub's page to install the app, and wait until it is on %s."
                     % ", ".join(repos))
    if "comment" in steps:
        lines.append("Post one test comment as the app on %s#%d, read it back, and check that "
                     "its author is the app's bot." % test)
    if "conf" in steps:
        lines.append("Add GITHUB_APP_ID, GITHUB_APP_LOGIN and GITHUB_APP_KEY_FILE to %s."
                     % conf_path)
    return ["  %d. %s" % (i + 1, line) for i, line in enumerate(lines)]


# --------------------------------------------------------------------------- the commands
def cmd_run(io, args):
    found = [m for m in AGENT_ENV_MARKERS if m in io.env()]
    if found:
        io.say("REFUSED: this makes Stage E's GitHub app and writes its key, and an agent must "
               "never do that. Run it in your own terminal. (An agent's environment was found: "
               "%s.)" % ", ".join(found))
        return EX_REFUSED
    if io.uid() == 0:
        io.say("REFUSED: run this as yourself, not as root. Its ledger lives under YOUR home, "
               "where the installer reads it.")
        return EX_REFUSED
    conf, code = load_conf(io, args.conf)
    if conf is None:
        return code
    repos = setup.split_list(conf["REVIEW_REPOS"])
    m = TEST_PR_RE.match(args.test_pr or "")
    known = dict((r.lower(), r) for r in repos)
    if not m or m.group(1).lower() not in known:
        io.say("COULD NOT RUN: --test-pr must be OWNER/NAME#N, a pull request on a REVIEW_REPOS "
               "repository (%s); got %r." % (", ".join(repos), args.test_pr))
        return EX_USAGE
    test = (known[m.group(1).lower()], int(m.group(2)))
    if args.name is not None and not 1 <= len(args.name) <= NAME_MAX:
        io.say("COULD NOT RUN: --name must be 1 to %d characters." % NAME_MAX)
        return EX_USAGE
    if args.org is not None and not gha.ORG_RE.fullmatch(args.org):
        io.say("COULD NOT RUN: --org %r is not an organization name GitHub allows." % args.org)
        return EX_USAGE
    ledger_path = io.ledger_path()
    ledger, wrong, could_not = read_ledger(ledger_path)
    if ledger is None:
        io.say("COULD NOT RUN: %s. Move it aside, then run this again." % (wrong or could_not))
        return EX_USAGE
    sudo, window = Sudo(io), Window(args.org)
    try:
        return _run(io, args, conf, repos, test, ledger, ledger_path, sudo, window)
    except NoPrivilege as exc:
        io.say("")
        io.say(str(exc))
        return EX_NOPRIV
    except KeyboardInterrupt:
        io.say("")
        if window.made:
            # GitHub made the app, and no ledger records it: its key is gone with this process.
            io.say("Interrupted after GitHub made app %s (%s), before the ledger recorded it. "
                   "Its key is lost, and a rerun would make a second app. Delete this one at %s, "
                   "then run this again." % (window.made["app_id"], window.made["slug"],
                                             app_page(window.org, window.made["slug"])))
        elif window.page_open:
            io.say("Interrupted while GitHub's create page was open. If you clicked Create, GitHub "
                   "made an app this script never recorded, and a rerun would make a second one: "
                   "delete it at %s, then run this again." % apps_page(window.org))
        else:
            io.say("Interrupted. What is done stays done; run the same command again.")
        return EX_FAILED


def _run(io, args, conf, repos, test, ledger, ledger_path, sudo, window):
    account = conf["ROLE_ACCOUNT"]
    rows = measure(io, conf, ledger, ledger_path, sudo, agent=False)
    io.say("Stage E's own GitHub app, as it stands:")
    show(io, rows)
    state = worst(rows)
    if state == FAILED:
        io.say("")
        io.say("A FAILED line is not this script's to fix. Fix it, then run this again. Nothing "
               "was changed.")
        return EX_FAILED
    if state == UNKNOWN:
        io.say("")
        io.say("Something could not be checked, so nothing was changed. Run this again when it "
               "can be.")
        return EX_UNKNOWN
    app, _wrong = ledger_app(ledger)
    if state == DONE:
        io.say("")
        io.say("Everything is done: Stage E posts as %s. Nothing to do, and nothing was changed."
               % app["login"])
        return EX_OK
    steps = plan_of(rows)
    name = key_file = None
    if "create" in steps:
        name = args.name or default_name(io)
        if not name:
            io.say("COULD NOT RUN: `gh api user` did not say your GitHub login, so the app has no "
                   "name. Pass --name NAME. Nothing was changed.")
            return EX_USAGE
        try:
            key_file = os.path.join(io.role_home(account), KEY_FILE)
            gha.write_key_argv(account, key_file)
            gha.manifest(name, conf["KIT_REPO_URL"], PERMISSIONS, description=DESCRIPTION)
        except (KeyError, OSError, gha.GitHubAppError) as exc:
            io.say("COULD NOT RUN: %s. Nothing was changed." % (
                exc if isinstance(exc, gha.GitHubAppError)
                else "no home for the role account %s" % account))
            return EX_USAGE
    io.say("")
    io.say("What `run` will do, in order:")
    for line in plan_lines(steps, name, ledger_path, key_file or app["key_file"], account,
                           repos, test, args.conf):
        io.say(line)
    io.say("")
    if io.ask("Type yes to do this: ").strip() != "yes":
        io.say("Nothing was changed.")
        return EX_DECLINED
    try:
        if steps != ["conf"]:
            sudo.ensure()
        if "create" in steps:
            app = step_create(io, conf, args.org, name, ledger, ledger_path, key_file, window)
        signer = Signer(io, app["app_id"], app["key_file"], account)
        if "install" in steps:
            step_install(io, conf, app, ledger, ledger_path, signer)
        if "comment" in steps:
            step_comment(io, app, ledger, ledger_path, signer, test[0], test[1])
        if "conf" in steps:
            step_conf(io, args.conf, app)
    except StepFailed as exc:
        io.say("")
        io.say("FAILED: %s" % exc)
        return EX_FAILED
    io.say("")
    io.say("Checked again:")
    conf, code = load_conf(io, args.conf)
    if conf is None:
        return code
    rows = measure(io, conf, ledger, ledger_path, sudo, agent=False)
    show(io, rows)
    state = worst(rows)
    if state != DONE:
        io.say("")
        io.say("Not everything holds yet; the lines above say what. Run the same command again.")
        return EX_UNKNOWN if state == UNKNOWN else EX_FAILED
    io.say("")
    io.say("Done. Stage E posts as %s." % app["login"])
    io.say("Next, run the installer once more:  python3 %s run --conf %s" % (INSTALLER, args.conf))
    io.say("And tell the watcher: add %s to its trusted_bots." % app["login"])
    return EX_OK


def cmd_verify(io, args):
    agent = [m for m in AGENT_ENV_MARKERS if m in io.env()]
    conf, code = load_conf(io, args.conf)
    if conf is None:
        return code
    ledger_path = io.ledger_path()
    ledger, wrong, could_not = read_ledger(ledger_path)
    if ledger is None:
        io.say("%s: %s." % ("FAILED" if wrong else "UNKNOWN", wrong or could_not))
        return EX_FAILED if wrong else EX_UNKNOWN
    try:
        rows = measure(io, conf, ledger, ledger_path, Sudo(io), agent=bool(agent))
    except NoPrivilege as exc:
        io.say(str(exc))
        return EX_NOPRIV
    io.say("Stage E's own GitHub app (read-only):")
    show(io, rows)
    if agent:
        io.say("An agent's environment (%s): the key file and the app were not checked." %
               ", ".join(agent))
    state = worst(rows)
    io.say("")
    io.say({DONE: "Every piece holds.", TODO: "Not every piece is done: `run` does the rest.",
            FAILED: "A piece does not hold; its line says what to do.",
            UNKNOWN: "Something could not be checked, which is not a pass."}[state])
    return {DONE: EX_OK, TODO: EX_FAILED, FAILED: EX_FAILED, UNKNOWN: EX_UNKNOWN}[state]


def main(argv=None, io=None):
    ap = argparse.ArgumentParser(prog="pipeline_stage_e_app.py",
                                 description="Stage E's own GitHub app: make it, then prove it.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="make what is missing: check, show, one yes, do, check")
    run.add_argument("--conf", required=True)
    run.add_argument("--test-pr", required=True, help="OWNER/NAME#N on a REVIEW_REPOS repository")
    run.add_argument("--name", help="the app's name; default stage-e-<your GitHub login>")
    run.add_argument("--org", help="make the app under this organization, not your account")
    verify = sub.add_parser("verify", help="the same checks, read-only")
    verify.add_argument("--conf", required=True)
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # its temp folders go when it ends (KIT-450)
        return contained(selftest, "stage-e-app-selftest-")
    io = io or IO()
    if args.cmd == "run":
        return cmd_run(io, args)
    if args.cmd == "verify":
        return cmd_verify(io, args)
    ap.print_help()
    return EX_USAGE


# ======================================================================================
# SELFTEST: nothing below the line above is run by a reader. No network, no sudo, no browser:
# every effect goes through a stand-in IO, and GitHub and the role account are made up.
# ======================================================================================
ROLE_T = "_exdispatch"                     # the installer's GOOD_CONF account
HOME_T = "/Users/<role-account>"
KEY_T = HOME_T + "/" + KEY_FILE
APP_ID_T, SLUG_T = 123456, "stage-e-owner"
LOGIN_T = SLUG_T + "[bot]"
T0 = 1793890800.0
KEY_MARK = "KEYMATERIAL-NEVER-SHOWN-0123"
# Shaped like GitHub's answer only as far as convert() looks: no key material in this file.
KEYISH = "-----BEGIN TEST FIXTURE-----\n%s\n-----END TEST FIXTURE-----\n" % KEY_MARK
# Assembled, never spelled whole: a token-shaped literal is what the secret scan stops.
TOKEN_T = "ghs_" + "SELFTESTTOKENNOTREAL0123456789"


class _World(object):
    """GitHub and the role account's key file, made up, and shared by every run in one case, so
    a second run meets what the first one left."""

    def __init__(self, code="abc_123", installed_after=1, author=None, reachable=True,
                 key_mode="600", key_write_rc=0, convert_status=201, interrupt=None):
        self.code, self.installed_after, self.reachable = code, installed_after, reachable
        self.author = author or {"login": LOGIN_T, "type": "Bot"}
        self.key_mode = key_mode
        self.key_write_rc = key_write_rc        # the key write's exit; non-zero writes nothing
        self.convert_status = convert_status    # GitHub's answer to the code; not 201 makes nothing
        self.interrupt = interrupt              # where a Ctrl-C lands: "wait" or "key"
        self.app_made = False
        self.install_page_opened = False
        self.install_polls = 0
        self.key = None              # the bytes written to the key file
        self.ledger_at_key = None    # the ledger as it stood when the key was written
        self.comments = {}


class _FakeFlow(object):
    url = "http://127.0.0.1:49152/"

    def __init__(self, io, doc, org):
        self.io, self.doc, self.org = io, doc, org
        self.waited, self.closed = None, False

    def wait(self, seconds):
        self.waited = seconds
        if self.io.world.interrupt == "wait":
            raise KeyboardInterrupt
        if self.io.world.code is None:
            self.io.t += seconds
        return self.io.world.code

    def close(self):
        self.closed = True


class _FakeIO(IO):
    """A Mac with no network, no sudo and no browser. Every effect is recorded."""

    def __init__(self, world, tmp, env=None, typed="yes", uid=501, sudo_rc=0, login="owner"):
        self.world, self.tmp, self._env, self.typed = world, tmp, env or {}, typed
        self._uid, self.sudo_rc, self.login = uid, sudo_rc, login
        self.said, self.asked, self.ran, self.calls, self.opened, self.flows = [], [], [], [], [], []
        self.sudo_asked = 0
        self.t = T0

    def env(self):
        return self._env

    def uid(self):
        return self._uid

    def say(self, text=""):
        self.said.append(str(text))

    def ask(self, question):
        self.asked.append(question)
        return self.typed

    def sudo_validate(self):
        self.sudo_asked += 1
        return self.sudo_rc

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def role_home(self, account):
        if account != ROLE_T:
            raise KeyError(account)
        return HOME_T

    def ledger_path(self):
        return os.path.join(self.tmp, "owner-home", ".stage-e-app", "state.json")

    def open_url(self, url):
        self.opened.append(url)
        if url.endswith("/installations/new"):
            self.world.install_page_opened = True

    def flow(self, doc, org):
        made = _FakeFlow(self, doc, org)
        self.flows.append(made)
        return made

    def run(self, argv, input=None, capture_output=True, timeout=60, cwd="/"):
        argv, w = list(argv), self.world
        self.ran.append((argv, input))

        def done(rc, out=b"", err=b""):
            return subprocess.CompletedProcess(argv, rc, out, err)
        if argv[:3] == ["gh", "api", "user"]:
            return done(0, (self.login + "\n").encode())
        if argv[:2] == ["gh", "api"]:
            cid = int(argv[2].rsplit("/", 1)[1])
            if cid not in w.comments or not w.reachable:
                return done(1, b"", b"gh: Not Found (HTTP 404)")
            return done(0, json.dumps({"id": cid, "user": w.comments[cid]}).encode())
        if argv[:5] == ["sudo", "-n", "-u", ROLE_T, "--"]:
            rest = argv[5:]
            if rest[:2] == ["/bin/sh", "-c"] and "kind=symlink" in rest[2]:
                if not w.key:
                    return done(0, b"kind=missing\n")
                return done(0, ("kind=file mode=%s owner=%s\n" % (w.key_mode, ROLE_T)).encode())
            if rest[:1] == ["openssl"]:
                return done(0, b"s" * 256) if w.key else done(1, b"", b"Could not open file")
            if argv == gha.write_key_argv(ROLE_T, KEY_T):
                if w.interrupt == "key":
                    raise KeyboardInterrupt
                if w.key_write_rc:
                    return done(w.key_write_rc, b"", b"sh: cannot create the file: Permission denied")
                w.key = input
                try:
                    with open(self.ledger_path(), encoding="utf-8") as fh:
                        w.ledger_at_key = json.load(fh)
                except (OSError, ValueError):
                    w.ledger_at_key = None
                return done(0)
        return done(127, b"", b"not scripted")

    def call(self, method, path, auth=None, body=None):
        self.calls.append((method, path, auth, body))
        w = self.world
        if not w.reachable:
            raise gha.GitHubAppError("GitHub could not be reached (URLError)")
        if (method, path) == gha._path("convert", code="abc_123"):
            if w.app_made:
                return 404, {}
            if w.convert_status != 201:
                return w.convert_status, {"message": "Unprocessable Entity"}
            w.app_made = True
            return 201, {"id": APP_ID_T, "slug": SLUG_T, "pem": KEYISH,
                         "owner": {"login": self.login}}
        if (method, path) == gha._path("app"):
            ok = w.app_made and (auth or "").startswith("Bearer ")
            return (200, {"id": APP_ID_T, "slug": SLUG_T}) if ok else (401, {})
        if (method, path) == gha._path("repo-installation", owner="example-org", repo="kit"):
            if w.install_page_opened and w.installed_after is not None:
                w.install_polls += 1
                if w.install_polls >= w.installed_after:
                    return 200, {"id": 7}
            return 404, {}
        if (method, path) == gha._path("token", installation_id=7):
            return 201, {"token": TOKEN_T}
        if (method, path) == gha._path("comment-create", owner="example-org", repo="kit",
                                       number=12):
            if auth != "token " + TOKEN_T:
                return 401, {}
            cid = 4242 + len(w.comments)
            w.comments[cid] = dict(w.author)
            return 201, {"id": cid, "user": dict(w.author)}
        for cid, user in w.comments.items():
            if (method, path) == gha._path("comment-read", owner="example-org", repo="kit",
                                           comment_id=cid):
                return 200, {"id": cid, "user": dict(user)}
        return 500, {}


def selftest():
    import tempfile
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:400], str(want)[:400]))

    def read(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    with tempfile.TemporaryDirectory() as tmp:
        n = [0]

        def case(conf_text=None, **world):
            """A folder, a conf in it, and a world. Each case gets its own."""
            n[0] += 1
            d = os.path.join(tmp, "case%d" % n[0])
            os.makedirs(d)
            path = os.path.join(d, "stage-e.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(setup.GOOD_CONF if conf_text is None else conf_text)
            os.chmod(path, 0o600)
            return d, path, _World(**world)

        def run_argv(conf_path, *more):
            return ["run", "--conf", conf_path, "--test-pr", "example-org/kit#12"] + list(more)

        def posts(io):
            return [(m, p) for m, p, _a, _b in io.calls if m != "GET"]

        # 1. Refused under a model, before anything is read: a missing conf is not even looked for.
        d, conf, w = case()
        io = _FakeIO(w, d, env={AGENT_ENV_MARKERS[0]: "1"})
        code = main(run_argv(os.path.join(d, "absent.conf")), io)
        check("an agent's environment is refused before anything is read",
              (code, io.said[0].startswith("REFUSED"), io.ran, io.calls, io.flows, io.sudo_asked,
               os.path.exists(io.ledger_path())), (EX_REFUSED, True, [], [], [], 0, False))
        io = _FakeIO(w, d, uid=0)
        check("root is refused too: the ledger is YOUR home's",
              (main(run_argv(conf), io), io.ran, io.calls), (EX_REFUSED, [], []))

        # 2. Without a typed yes, nothing changes: no app, no key, no ledger, no conf line.
        for typed in ("no", "y", "YES please", ""):
            d, conf, w = case()
            before = read(conf)
            io = _FakeIO(w, d, typed=typed)
            code = main(run_argv(conf), io)
            check("without a typed yes nothing changes (%r)" % typed,
                  (code, len(io.asked), posts(io), io.flows, io.opened, w.key, io.sudo_asked,
                   os.path.exists(io.ledger_path()), read(conf), io.said[-1]),
                  (EX_DECLINED, 1, [], [], [], None, 0, False, before, "Nothing was changed."))
        check("…and the plan it showed said all five steps in plain words",
              [s.split(".", 1)[0].strip() for s in io.said if re.match(r"^  [1-5]\. ", s)],
              ["1", "2", "3", "4", "5"])

        # 3. A full run, every piece.
        d_full, conf_full, w_full = case()
        io = _FakeIO(w_full, d_full)
        code = main(run_argv(conf_full), io)
        said_full = "\n".join(io.said)
        check("a full run is done", code, EX_OK)
        check("the waits are the documented ones: 600 s, a look every 5 s, a 34-character name",
              (WAIT_SECONDS, POLL_SECONDS, NAME_MAX, PERMISSIONS), (600, 5, 34,
                                                                   {"pull_requests": "write"}))
        flow = io.flows[0] if io.flows else _FakeFlow(io, {}, None)
        check("the app is made from the manifest: pull-request comments and nothing else",
              (flow.doc.get("name"), flow.doc.get("default_permissions"), flow.doc.get("public"),
               flow.doc.get("url"), flow.doc.get("default_events"), flow.org, flow.waited,
               flow.closed),
              ("stage-e-owner", {"pull_requests": "write"}, False,
               "https://github.com/example-org/kit", [], None, WAIT_SECONDS, True))
        check("it opens GitHub's two pages, the create page and the install page",
              io.opened, [_FakeFlow.url, "https://github.com/apps/stage-e-owner/installations/new"])
        key_writes = [(a, i) for a, i in io.ran if a == gha.write_key_argv(ROLE_T, KEY_T)]
        check("the key goes to the role account's file once, on stdin, by the helper's command",
              (len(key_writes), w_full.key), (1, KEYISH.encode()))
        check("the ledger records the app before the key is written",
              (w_full.ledger_at_key or {}).get("app_id"), APP_ID_T)
        ledger_path = io.ledger_path()
        ledger = json.loads(read(ledger_path) or "{}")
        at = (ledger.get("test_comment") or {}).pop("at", None)
        check("the ledger: these fields and no others",
              ledger, {"schema": SCHEMA, "app_id": APP_ID_T, "slug": SLUG_T, "login": LOGIN_T,
                       "account": ROLE_T, "key_file": KEY_T,
                       "installation_ids": {"example-org/kit": 7},
                       "test_comment": {"repo": "example-org/kit", "number": 12, "id": 4242,
                                        "author": LOGIN_T}})
        check("…stamped, mode 600, in a mode-700 folder",
              (bool(at and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", at)),
               oct(os.stat(ledger_path).st_mode & 0o777),
               oct(os.stat(os.path.dirname(ledger_path)).st_mode & 0o777)),
              (True, "0o600", "0o700"))
        conf_after, errs = setup.load_conf(conf_full)
        check("the conf names the app and still loads clean",
              (errs, conf_after["GITHUB_APP_ID"], conf_after["GITHUB_APP_LOGIN"],
               conf_after["GITHUB_APP_KEY_FILE"], oct(os.stat(conf_full).st_mode & 0o777)),
              ([], str(APP_ID_T), LOGIN_T, KEY_T, "0o600"))
        check("the test comment is the one line, posted with the app's own token",
              [(m, b, a == "token " + TOKEN_T) for m, p, a, b in io.calls
               if m == "POST" and p.endswith("/comments")],
              [("POST", {"body": TEST_COMMENT}, True)])
        sudos = [a[:5] for a, _i in io.ran if a[:1] == ["sudo"]]
        check("sudo -v once, and every sudo after it is `sudo -n` as the role account",
              (io.sudo_asked, bool(sudos), all(s == ["sudo", "-n", "-u", ROLE_T, "--"]
                                               for s in sudos)), (1, True, True))
        check("GitHub's calls are the helper's fixed paths, and nothing else",
              (bool(io.calls), all(any(m == em and re.fullmatch(re.sub(r"\{\w+\}", "[^/]+", tp), p)
                                       for em, tp in gha.ENDPOINTS.values())
                                   for m, p, _a, _b in io.calls)), (True, True))
        check("the last lines say it holds, and what comes next",
              ("Done. Stage E posts as %s." % LOGIN_T in said_full,
               "%s run --conf %s" % (INSTALLER, conf_full) in said_full,
               "trusted_bots" in said_full), (True, True, True))

        # 4. No key, token or signed token in any output, argv, ledger or conf.
        bearers = set(a[len("Bearer "):] for _m, _p, a, _b in io.calls
                      if (a or "").startswith("Bearer "))
        texts = {"output": said_full, "argv": json.dumps([a for a, _i in io.ran]),
                 "ledger": read(ledger_path), "conf": read(conf_full)}
        secrets = [("the key", KEY_MARK), ("the token", TOKEN_T)] + [("a signed token", b)
                                                                     for b in bearers]
        check("no key, token or signed token in any output, argv, ledger or conf",
              [(where, what) for where, text in sorted(texts.items())
               for what, secret in secrets if secret in (text or "")], [])
        check("…the key went to one place, the key file's stdin, and a token was signed",
              ([i for _a, i in io.ran if i and KEY_MARK.encode() in i], bool(bearers)),
              ([KEYISH.encode()], True))
        try:
            write_ledger(os.path.join(tmp, "refused", "state.json"), {"note": KEYISH})
            refused = False
        except ValueError:
            refused = True
        check("the ledger refuses a value shaped like a key, and writes nothing",
              (refused, os.path.exists(os.path.join(tmp, "refused", "state.json"))), (True, False))
        io = _FakeIO(_World(), tmp)
        reader, refused = gh_read(io), []
        for method, extra in (("POST", {"body": {}}), ("GET", {"auth": "token " + TOKEN_T}),
                              ("GET", {"body": {}})):
            try:
                reader(method, "/repos/example-org/kit/x", **extra)
            except gha.GitHubAppError:
                refused.append(method)
        check("the `gh` reader only reads, and never with a credential: refused before it runs",
              (refused, io.ran), (["POST", "GET", "GET"], []))

        # 5. The conf's keys go in once: a second write adds nothing, an example's empty lines are
        # filled where they stand, another app's values are a conflict, a missing newline is fine.
        check("each key is in the conf once",
              [sum(1 for l in (read(conf_full) or "").splitlines() if l.startswith(k + "="))
               for k in setup.GITHUB_APP_KEYS], [1, 1, 1])
        want = {"GITHUB_APP_ID": str(APP_ID_T), "GITHUB_APP_LOGIN": LOGIN_T,
                "GITHUB_APP_KEY_FILE": KEY_T}
        example = "A=1\nGITHUB_APP_ID=\nGITHUB_APP_LOGIN=\nGITHUB_APP_KEY_FILE=\nB=2\n"
        filled, conflicts = conf_with_app(example, want)
        check("an example's empty lines are filled where they stand",
              (filled, conflicts), ("A=1\nGITHUB_APP_ID=%s\nGITHUB_APP_LOGIN=%s\n"
                                    "GITHUB_APP_KEY_FILE=%s\nB=2\n" % (APP_ID_T, LOGIN_T, KEY_T), []))
        check("a conf that already names the app is left as it is",
              conf_with_app(filled, want), (filled, []))
        other = filled.replace("ID=%s" % APP_ID_T, "ID=999")
        check("a conf naming another app is a conflict, and nothing is added",
              conf_with_app(other, want), (other, ["GITHUB_APP_ID is already 999, not %s"
                                                   % APP_ID_T]))
        added, _c = conf_with_app("A=1", want)
        check("a conf with no last newline gets the keys on lines of their own",
              added.split("\n")[0], "A=1")

        # 6. A second run with everything done changes nothing, asks nothing, and says so.
        ledger_before, conf_before = read(ledger_path), read(conf_full)
        io2 = _FakeIO(w_full, d_full, typed="")
        code = main(run_argv(conf_full), io2)
        check("a second run with everything done is a no-op, and says so",
              (code, io2.asked, posts(io2), io2.flows, io2.opened, read(ledger_path),
               read(conf_full), any("Nothing to do" in s for s in io2.said)),
              (EX_OK, [], [], [], [], ledger_before, conf_before, True))

        # 7. No answer from GitHub in time: nothing is written, and it says what to delete.
        d, conf, w = case(code=None)
        before = read(conf)
        io = _FakeIO(w, d)
        code = main(run_argv(conf), io)
        check("no code from GitHub in time means nothing is written",
              (code, os.path.exists(io.ledger_path()), w.key, read(conf), posts(io),
               io.t - T0 >= WAIT_SECONDS,
               any("delete it at https://github.com/settings/apps" in s for s in io.said)),
              (EX_FAILED, False, None, before, [], True, True))

        # 8. Not installed in time is said, by repository, and nothing after it runs.
        d, conf, w = case(installed_after=None)
        before = read(conf)
        io = _FakeIO(w, d)
        code = main(run_argv(conf), io)
        ledger = json.loads(read(io.ledger_path()) or "{}")
        signs = [a for a, _i in io.ran if a[5:6] == ["openssl"]]
        check("not installed in time is said, and no comment is posted and no conf line added",
              (code, ledger.get("app_id"), ledger.get("installation_ids"), "test_comment" in ledger,
               read(conf), any("is not on example-org/kit" in s for s in io.said),
               io.t - T0 >= WAIT_SECONDS, [p for _m, p in posts(io) if p.endswith("/comments")]),
              (EX_FAILED, APP_ID_T, {}, False, before, True, True, []))
        check("…its token is signed again before it ages out, not once per look",
              1 < len(signs) < WAIT_SECONDS // POLL_SECONDS, True)

        # 9. A test comment by another author, or by a User, fails, and the conf is not written.
        for label, author in (("another author", {"login": "other-app[bot]", "type": "Bot"}),
                              ("a User", {"login": LOGIN_T, "type": "User"})):
            d, conf, w = case(author=author)
            before = read(conf)
            io = _FakeIO(w, d)
            code = main(run_argv(conf), io)
            ledger = json.loads(read(io.ledger_path()) or "{}")
            check("a test comment by %s fails, and the conf is not written" % label,
                  (code, (ledger.get("test_comment") or {}).get("author"), read(conf),
                   any("not by %s (Bot)" % LOGIN_T in s for s in io.said)),
                  (EX_FAILED, author["login"], before, True))

        # 10. verify: read-only, 0 only when every piece holds.
        io = _FakeIO(w_full, d_full)
        code = main(["verify", "--conf", conf_full], io)
        check("verify after a full run: 0, and read-only",
              (code, posts(io), io.flows, io.opened, read(ledger_path), read(conf_full)),
              (EX_OK, [], [], [], ledger_before, conf_before))
        d, conf, w = case()
        io = _FakeIO(w, d)
        check("verify with nothing made: 1", (main(["verify", "--conf", conf], io), io.sudo_asked),
              (EX_FAILED, 0))
        w_full.reachable = False
        io = _FakeIO(w_full, d_full)
        check("verify when GitHub cannot be reached: 4",
              main(["verify", "--conf", conf_full], io), EX_UNKNOWN)
        w_full.reachable = True
        io = _FakeIO(w_full, d_full)
        bad = os.path.join(d_full, "bad.conf")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write(setup.GOOD_CONF + "NOT_A_KEY=1\n")
        check("verify with a conf that has a problem: 2, nothing asked",
              (main(["verify", "--conf", bad], io), io.ran, io.calls), (EX_USAGE, [], []))
        io = _FakeIO(w_full, d_full, env={AGENT_ENV_MARKERS[0]: "1"})
        code = main(["verify", "--conf", conf_full], io)
        check("verify in an agent's environment: 4, with no sudo and nothing signed",
              (code, io.sudo_asked, [a for a, _i in io.ran if a[:1] == ["sudo"]],
               [c for c in io.calls if (c[2] or "").startswith("Bearer ")]),
              (EX_UNKNOWN, 0, [], []))
        w_full.key_mode = "644"
        io = _FakeIO(w_full, d_full)
        check("verify with the key file at mode 644: 1",
              (main(["verify", "--conf", conf_full], io),
               any("is mode 644" in s for s in io.said)), (EX_FAILED, True))
        w_full.key_mode = "600"
        w_full.comments[4242] = {"login": LOGIN_T, "type": "User"}
        io = _FakeIO(w_full, d_full)
        check("verify with a test comment GitHub now shows by a User: 1",
              main(["verify", "--conf", conf_full], io), EX_FAILED)
        w_full.comments[4242] = {"login": LOGIN_T, "type": "Bot"}
        io = _FakeIO(w_full, d_full, sudo_rc=1)
        check("verify without administrator access: 5",
              main(["verify", "--conf", conf_full], io), EX_NOPRIV)
        io = _FakeIO(w_full, d_full)
        check("verify after all that: 0 again", main(["verify", "--conf", conf_full], io), EX_OK)

        # 11. More refusals before the yes, each with nothing changed.
        d, conf, w = case()
        io = _FakeIO(w, d, sudo_rc=1)
        code = main(run_argv(conf), io)
        check("no administrator access after the yes: 5, and no app is made",
              (code, io.flows, w.app_made, os.path.exists(io.ledger_path())),
              (EX_NOPRIV, [], False, False))
        for label, pr in (("not a REVIEW_REPOS repository", "other-org/x#3"),
                          ("no number", "example-org/kit"), ("number zero", "example-org/kit#0")):
            io = _FakeIO(w, d)
            code = main(["run", "--conf", conf, "--test-pr", pr], io)
            check("--test-pr %s: 2, nothing asked" % label,
                  (code, io.ran, io.calls, io.asked), (EX_USAGE, [], [], []))
        other_app = setup.GOOD_CONF + ("GITHUB_APP_ID=999\nGITHUB_APP_LOGIN=other-app[bot]\n"
                                       "GITHUB_APP_KEY_FILE=%s/.stage-e/other\n" % HOME_T)
        d, conf, w = case(conf_text=other_app)
        io = _FakeIO(w, d)
        code = main(run_argv(conf), io)
        check("a conf naming an app the ledger does not record stops before the yes",
              (code, io.asked, io.flows, any("names app 999" in s for s in io.said)),
              (EX_FAILED, [], [], True))
        check("the default name is stage-e-<your login>, cut to 34 characters",
              (default_name(_FakeIO(_World(), tmp, login="a" * 39)),
               default_name(_FakeIO(_World(), tmp, login=""))),
              (("stage-e-" + "a" * 39)[:NAME_MAX], None))
        d, conf, w = case()
        io = _FakeIO(w, d)
        code = main(run_argv(conf, "--name", "kit-reviews", "--org", "example-org"), io)
        check("--name and --org reach the flow, and the run is done",
              (code, io.flows[0].doc["name"] if io.flows else None,
               io.flows[0].org if io.flows else None), (EX_OK, "kit-reviews", "example-org"))

        # 12. After GitHub makes the app (review of KIT-578): every failure there is a StepFailed
        # that names the app to delete, never a traceback, and so is an interrupt there.
        def said_has(io, *needles):
            text = "\n".join(io.said)
            return all(needle in text for needle in needles)
        page = "https://github.com/settings/apps/" + SLUG_T
        real_write = globals()["write_ledger"]

        d, conf, w = case(key_write_rc=1)
        before = read(conf)
        io = _FakeIO(w, d)
        code = main(run_argv(conf), io)
        ledger = json.loads(read(io.ledger_path()) or "{}")
        check("a key write that fails: 1, the ledger keeps the app, and both are named to delete",
              (code, ledger.get("app_id"), ledger.get("slug"), w.key, read(conf), io.opened[1:],
               said_has(io, "Delete it at %s, delete %s" % (page, io.ledger_path()))),
              (EX_FAILED, APP_ID_T, SLUG_T, None, before, [], True))
        d, conf, w = case(convert_status=422)
        io = _FakeIO(w, d)
        code = main(run_argv(conf), io)
        check("GitHub answering the code with an error: 1, no ledger and no key",
              (code, os.path.exists(io.ledger_path()), w.key,
               said_has(io, "If it made one, delete it at https://github.com/settings/apps")),
              (EX_FAILED, False, None, True))
        for label, exc in (("an OSError", PermissionError(13, "Permission denied")),
                           ("a refusal", ValueError("the ledger holds fields only"))):
            d, conf, w = case()
            io = _FakeIO(w, d)

            def refuse(path, doc, exc=exc):
                raise exc
            globals()["write_ledger"] = refuse
            try:
                code = main(run_argv(conf), io)
            except Exception as raised:   # noqa: BLE001 - a traceback is the defect under test
                code = "raised %s" % type(raised).__name__
            finally:
                globals()["write_ledger"] = real_write
            check("a ledger that cannot be written after GitHub made the app (%s): 1, the app named "
                  "to delete, and no key written" % label,
                  (code, w.app_made, w.key, os.path.exists(io.ledger_path()),
                   said_has(io, "app %d (%s)" % (APP_ID_T, SLUG_T), "Delete it at %s" % page)),
                  (EX_FAILED, True, None, False, True))
        # …and the ledger writes after it, step 3's and step 4's: a StepFailed saying what is done.
        for nth, said in ((2, "App %d is made and recorded, and its key is in place" % APP_ID_T),
                          (3, "Test comment 4242 is posted on example-org/kit#12, and is not "
                              "recorded")):
            d, conf, w = case()
            io = _FakeIO(w, d)
            calls = [0]

            def fail_nth(path, doc, nth=nth):
                calls[0] += 1
                if calls[0] == nth:
                    raise PermissionError(13, "Permission denied")
                real_write(path, doc)
            globals()["write_ledger"] = fail_nth
            try:
                code = main(run_argv(conf), io)
            except Exception as raised:   # noqa: BLE001 - a traceback is the defect under test
                code = "raised %s" % type(raised).__name__
            finally:
                globals()["write_ledger"] = real_write
            check("ledger write %d failing: 1, saying what is done, and no word of deleting the app"
                  % nth, (code, said_has(io, said), said_has(io, "Delete it")),
                  (EX_FAILED, True, False))
        try:
            step_conf(_FakeIO(_World(), tmp), os.path.join(tmp, "gone", "stage-e.conf"),
                      {"app_id": APP_ID_T, "slug": SLUG_T, "login": LOGIN_T, "account": ROLE_T,
                       "key_file": KEY_T})
            got = "no failure"
        except StepFailed as exc:
            got = str(exc)
        except Exception as exc:          # noqa: BLE001 - a traceback is the defect under test
            got = "raised %s" % type(exc).__name__
        check("a conf step 5 cannot read: a StepFailed with the lines to add, and no word of "
              "deleting a working app",
              ("app %d is made and recorded" % APP_ID_T in got, "GITHUB_APP_ID=%d" % APP_ID_T in got,
               "delete it" in got.lower()), (True, True, False))

        d, conf, w = case()
        io = _FakeIO(w, d)

        def interrupted(path, doc):
            raise KeyboardInterrupt
        globals()["write_ledger"] = interrupted
        try:
            code = main(run_argv(conf), io)
        finally:
            globals()["write_ledger"] = real_write
        check("Ctrl-C after GitHub made the app and before the ledger: 1, and the app to delete",
              (code, w.key, os.path.exists(io.ledger_path()),
               said_has(io, "after GitHub made app %d (%s)" % (APP_ID_T, SLUG_T),
                        "a rerun would make a second app", "Delete this one at %s" % page),
               said_has(io, "What is done stays done")), (EX_FAILED, None, False, True, False))
        d, conf, w = case(interrupt="wait")
        io = _FakeIO(w, d)
        code = main(run_argv(conf), io)
        check("Ctrl-C while GitHub's create page is open: 1, and delete any app it made",
              (code, w.app_made, os.path.exists(io.ledger_path()),
               io.flows[0].closed if io.flows else None,
               said_has(io, "If you clicked Create", "delete it at https://github.com/settings/apps,"),
               said_has(io, "What is done stays done")), (EX_FAILED, False, False, True, True, False))
        d, conf, w = case(interrupt="key")
        io = _FakeIO(w, d)
        code = main(run_argv(conf), io)
        ledger = json.loads(read(io.ledger_path()) or "{}")
        check("Ctrl-C after the ledger recorded the app: what is done stays done",
              (code, ledger.get("app_id"), said_has(io, "What is done stays done"),
               said_has(io, "Delete this one"), said_has(io, "If you clicked Create")),
              (EX_FAILED, APP_ID_T, True, False, False))

    if failures:
        for line in failures:
            print(line)
        print("stage-e app selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("ok — stage-e app selftest: %d checks" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
