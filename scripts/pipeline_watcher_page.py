#!/usr/bin/env python3
"""The watcher's urgent page: its own way to tell the owner, not through the workers' bridge or the
notifier (KIT-519). Design: docs/adr/2026-11-05-the-watcher-an-independent-auditor.md, decision D2.

    python3 scripts/pipeline_watcher_page.py run     --config PATH [--dry-run]    one pass
    python3 scripts/pipeline_watcher_page.py check   --config PATH                the owner: what is set
    python3 scripts/pipeline_watcher_page.py test    --config PATH                the owner: one test page
    python3 scripts/pipeline_watcher_page.py --example-config
    python3 scripts/pipeline_watcher_page.py --selftest

WHY A SEPARATE FILE.  The watcher (scripts/pipeline_watcher_local.py) sends nothing but GETs, and
that is a rule its own selftest pins. A page is a POST. So the watcher writes its status file
(contract section 19) and this job reads it, and only it sends.

WHAT IT DOES.  Reads the status file, decides what the owner should hear, and sends it to each
route in the config: the watcher's own Slack app to the owner's DM, and a GitHub issue in a private
alert repository. Both are optional; at least one is needed. Neither goes through a worker-side job.

WHEN IT PAGES, once per change of answer, like the health watch:
  * a monitor flagged at `page` or `pause`: which monitor, how many findings, since when, which
    tickets or pull requests, and what the owner can do, in a closed table of plain words;
  * a monitor that stayed blind for three passes running;
  * the watcher itself: its file missing, unreadable, not a status file, stale, or written by a
    pass that crashed;
  * once more when a monitor it paged about is clear again.
It carries no ticket, comment or pull request text: the status file holds none, and the page is built
from its fields only.

A PAGE THAT COULD NOT BE SENT IS SENT AGAIN, and is not recorded as sent (contract section 13). Each
route keeps its own record, so one route failing does not repeat the page on the other. A pass that
could not send says so in the heartbeat file and in its exit code (4), never as a quiet success.

THE CREDENTIALS.  The config names ENV VARS, never a value. They live in the watcher account's own
env file, mode 600, and are read here by name. A token is never printed, logged or written.

EXIT CODES:  0 ok (nothing to send, or all sent; OFF with no config)   1 the pass crashed
             2 usage or config   3 refused (an agent's environment)   4 a page could not be sent

WHAT IT NEVER DOES (asserted in --selftest).  Start a process, open a socket by hand, send to any
host but the two it names, or print a credential.
"""
import argparse
import calendar
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CONFIG_SCHEMA = "pipeline-watcher-page-config/1"
STATE_SCHEMA = "pipeline-watcher-page-state/1"
HEARTBEAT_SCHEMA = "pipeline-watcher-page-heartbeat/1"
JUDGMENTS_SCHEMA = "pipeline-watcher-judgments/1"        # what the judge writes (KIT-516)
STATUS_SCHEMA_NAME = "pipeline-watcher"
SLACK_URL = "https://slack.com/api/chat.postMessage"
GITHUB_API = "https://api.github.com"
USER_AGENT = "pipeline-watcher-page/1"

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_REFUSED, EXIT_NOT_SENT = 0, 1, 2, 3, 4
DEFAULT_STATUS_FILE = "~/.watcher/watcher-status.json"
DEFAULT_STATE_DIR = "~/.watcher/page"
DEFAULT_MAX_AGE_MINUTES = 30
BLIND_PASSES = 3                   # a monitor blind this many passes running is paged
MAX_STATUS_BYTES = 16 * 1024 * 1024
LINE_CAP = 400
LEVEL_ORDER = {"record": 1, "page": 2, "pause": 3}

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*\Z")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z")
CHANNEL_RE = re.compile(r"^[CDGU][A-Z0-9]{6,20}\Z")
CREDENTIAL_RE = re.compile(r"^[\x21-\x7e]+\Z")
SLACK_ERROR_RE = re.compile(r"^[a-z_]{1,40}\Z")

CONFIG_KEYS = {
    "schema": "the marker, %s" % CONFIG_SCHEMA,
    "status_file": "the watcher's status file (contract section 19); absolute or ~/ (default %s)"
                   % DEFAULT_STATUS_FILE,
    "state_dir": "where this job keeps what it has already sent, and its heartbeat; absolute or ~/, "
                 "outside every git working tree (default %s)" % DEFAULT_STATE_DIR,
    "max_age_minutes": "a status file older than this is a page: the watcher stopped writing "
                       "(default %d)" % DEFAULT_MAX_AGE_MINUTES,
    "slack": "optional: {\"token_env\": ENV VAR NAME of the app's bot token, \"channel\": the owner's "
             "DM channel id}. The app is the watcher's own, with the chat:write scope",
    "github": "optional: {\"token_env\": ENV VAR NAME of a token that may open issues in the alert "
              "repository, \"repo\": OWNER/REPO of a PRIVATE alert repository}",
    "judge_file": "optional: the judge's judgments.json (scripts/pipeline_watcher_judge.py writes it "
                  "into the same state_dir). A judgment for the findings a page was sent about is "
                  "sent once, as a note after the page. It changes no page, level or hold",
}
EXAMPLE_CONFIG = {
    "schema": CONFIG_SCHEMA,
    "status_file": DEFAULT_STATUS_FILE,
    "state_dir": DEFAULT_STATE_DIR,
    "max_age_minutes": DEFAULT_MAX_AGE_MINUTES,
    "slack": {"token_env": "WATCHER_SLACK_TOKEN", "channel": "D0123456789"},
    "github": {"token_env": "WATCHER_ALERT_TOKEN", "repo": "acme/alerts"},
}

# What the owner can do, by the watcher's closed word. Plain, and it names no text from anywhere.
CAN_DO = {
    "protected-label": "Look at who applied the label on that ticket or pull request. If it was not "
                       "you, remove it.",
    "spend-day": "Look at today's spend in the usage file. Nothing new starts until you clear the hold.",
    "spend-piece": "Look at what that piece has cost. Nothing new starts until you clear the hold.",
    "key-run": "A run signed in with a billed key. Find which job holds the key and take it out.",
    "runs-day": "Look at today's runs. Nothing new starts until you clear the hold.",
    "runs-piece": "Look at how many runs that piece has had. Nothing new starts until you clear the hold.",
    "forged-marker": "Open that comment. The pipeline ignored it; nothing to undo if you know why it is there.",
    "shape-fence-tag": "Read that text. The pipeline ignored it.",
    "shape-ignore-instructions": "Read that text. The pipeline ignored it.",
    "shape-system-tag": "Read that text. The pipeline ignored it.",
    "shape-secret-ask": "Read that text. The pipeline ignored it.",
    "shape-hidden-char": "Read that text. It holds characters a reader cannot see.",
}
CLEAR_HOLD = "To let new starts begin again, clear the hold in your terminal: scripts/pipeline_hold.py clear."


class ConfigError(Exception):
    pass


class SendError(Exception):
    """A page that did not go. The words are ours: a status code or a closed word, never a body."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _epoch(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = re.sub(r"\.\d+", "", value.strip())
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _line(text, cap=LINE_CAP):
    """Our own words on one line: nothing a reader cannot see, and no angle bracket, ampersand, bar or
    backtick (a chat or issue body would treat them as markup)."""
    s = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs")
                else "?" if c in "<>&|`" else c for c in str(text))
    s = " ".join(s.split())
    return s if len(s) <= cap else s[:cap - 1].rstrip() + "..."


def _home():
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


def _path(raw, key, default, errors):
    value = raw.get(key, default)
    if not isinstance(value, str) or not value:
        errors.append("config %r must be a path" % key)
        return default
    path = os.path.join(_home(), value[2:]) if value.startswith("~/") else value
    if not os.path.isabs(path):
        errors.append("config %r must be an absolute path, or start with ~/" % key)
        return default
    return os.path.realpath(path)


def inside_git_worktree(path):
    cur = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def validate_config(raw):
    """The config as a pass uses it, or ConfigError naming EVERY problem at once."""
    errors = []
    if not isinstance(raw, dict):
        raise ConfigError("the config must hold one JSON object")
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        errors.append("unknown config key(s): %s (see --example-config)" % ", ".join(unknown))
    if raw.get("schema") != CONFIG_SCHEMA:
        errors.append("config 'schema' must be %r" % CONFIG_SCHEMA)
    status_file = _path(raw, "status_file", DEFAULT_STATUS_FILE, errors)
    state_dir = _path(raw, "state_dir", DEFAULT_STATE_DIR, errors)
    if inside_git_worktree(state_dir):
        errors.append("config 'state_dir' must be outside every git working tree: a session can "
                      "write a worktree, and could then make this job think a page was sent")
    judge_file = _path(raw, "judge_file", "", errors) if "judge_file" in raw else ""
    if judge_file and inside_git_worktree(os.path.dirname(judge_file)):
        errors.append("config 'judge_file' must be outside every git working tree: a session could write it, "
                      "and a forged note would reach the owner")
    age = raw.get("max_age_minutes", DEFAULT_MAX_AGE_MINUTES)
    if not isinstance(age, int) or isinstance(age, bool) or not 5 <= age <= 10080:
        errors.append("config 'max_age_minutes' must be a whole number from 5 to 10080")
        age = DEFAULT_MAX_AGE_MINUTES
    routes = {}
    for name, keys, check in (
            ("slack", ("token_env", "channel"), lambda v: bool(CHANNEL_RE.match(v))),
            ("github", ("token_env", "repo"), lambda v: bool(REPO_RE.match(v)))):
        if name not in raw:
            continue
        block = raw[name]
        if not isinstance(block, dict) or set(block) != set(keys):
            errors.append("config %r must be exactly {%s}" % (name, ", ".join(keys)))
            continue
        env, target = block[keys[0]], block[keys[1]]
        if not isinstance(env, str) or not ENV_NAME_RE.match(env):
            errors.append("config %s.%s must be an ENV VAR NAME, never a credential" % (name, keys[0]))
            continue
        if not isinstance(target, str) or not check(target):
            errors.append("config %s.%s is not a valid %s" % (
                name, keys[1], "Slack channel id" if name == "slack" else "OWNER/REPO"))
            continue
        routes[name] = {"token_env": env, keys[1]: target}
    if not routes and not any(e.startswith("config 'slack'") or e.startswith("config 'github'")
                              or " slack." in e or " github." in e for e in errors):
        errors.append("name at least one route to the owner: slack or github")
    if errors:
        raise ConfigError("; ".join(errors))
    return {"status_file": status_file, "state_dir": state_dir, "max_age_minutes": age, "routes": routes,
            "judge_file": judge_file}


def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError("could not read --config %s: %s" % (path, type(exc).__name__))
    return validate_config(raw)


def credential(environ, name):
    """(value, None), or (None, why). Never the value in `why`."""
    value = (environ.get(name) or "").strip()
    if not value:
        return None, "$%s is unset: it lives in the watcher account's own env file, mode 600" % name
    if not CREDENTIAL_RE.match(value):
        return None, "$%s holds a space, a line break or another character a header cannot carry" % name
    return value, None


# --------------------------------------------------------------------------- #
# What the owner should hear
# --------------------------------------------------------------------------- #
def read_status(cfg, now):
    """(doc, problem): the status file, or the one word-for-word reason it can't be trusted."""
    import check_schemas
    path = cfg["status_file"]
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read(MAX_STATUS_BYTES + 1)
    except FileNotFoundError:
        return None, ("missing", "the watcher has written no status file")
    except OSError as exc:
        return None, ("unreadable", "the watcher's status file could not be read (%s)"
                      % (exc.strerror or "error"))
    if len(raw) > MAX_STATUS_BYTES:
        return None, ("unreadable", "the watcher's status file is larger than a status file")
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError):
        return None, ("unreadable", "the watcher's status file is not readable JSON")
    if check_schemas.document_problems(doc, STATUS_SCHEMA_NAME):
        return None, ("unreadable", "the watcher's status file is not a status file")
    written = _epoch(doc.get("written_at"))
    if written is None or now - written > cfg["max_age_minutes"] * 60:
        return None, ("stale", "the watcher has not written its status file for more than %d minutes"
                      % cfg["max_age_minutes"])
    if doc.get("error"):
        return doc, ("crashed", "the watcher's last pass failed (%s)"
                     % _line(str(doc["error"]).replace("the pass stopped on ", ""), 60))
    return doc, None


def _digest(parts):
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def pages_for(doc, problem, blind_counts):
    """[{key, kind, signature, level, title, lines}]: what to say now, from fields only."""
    pages = []
    if problem:
        word, why = problem
        pages.append({"key": "watcher", "kind": "flagged", "level": "pause",
                      "signature": "flagged:watcher:" + word,
                      "title": "The watcher itself is in trouble",
                      "lines": [why + ".",
                                "While it is not watching, nothing is checking the workers. Look at "
                                "its job and its log in the watcher account."]})
    if doc is None:
        return pages
    for m in doc["monitors"]:
        findings = [f for f in m["findings"] if LEVEL_ORDER.get(f["level"], 0) >= LEVEL_ORDER["page"]]
        if m["state"] == "flagged" and findings:
            level = max((f["level"] for f in findings), key=LEVEL_ORDER.get)
            total = sum(f["count"] for f in findings)
            since = min([f["at"] for f in findings if f["at"]] or [doc["started_at"]])
            refs = sorted(set(f["ref"] for f in findings))
            ids = ", ".join(refs[:3]) + (" and %d more" % (len(refs) - 3) if len(refs) > 3 else "")
            todo = []
            for f in findings:
                if CAN_DO.get(f["what"]) and CAN_DO[f["what"]] not in todo:
                    todo.append(CAN_DO[f["what"]])
            if level == "pause":
                todo.append(CLEAR_HOLD)
            sig = "flagged:%s:%s:%s" % (m["name"], level, _digest(sorted(
                "%s %s %d" % (f["what"], f["ref"], f["count"]) for f in findings)))
            pages.append({
                "key": m["name"], "kind": "flagged", "level": level, "signature": sig,
                "title": "%s: %s flagged at %s" % ("Watcher", m["name"], level),
                "lines": ["Monitor %s flagged at level %s: %d finding(s), %d match(es), since %s."
                          % (m["name"], level, len(findings), total, since),
                          "About: %s." % ids] + todo[:3]})
        elif m["state"] == "blind" and blind_counts.get(m["name"], 0) >= BLIND_PASSES:
            pages.append({
                "key": m["name"], "kind": "blind", "level": "page",
                "signature": "blind:%s" % m["name"],
                "title": "Watcher: %s has been blind" % m["name"],
                "lines": ["Monitor %s could not establish its answer for %d passes running: %s."
                          % (m["name"], blind_counts[m["name"]], _line(m["reason"] or "no reason given", 200)),
                          "Until it can, nothing says whether that signal is clean. Check its key or its file."]})
    return pages


def judge_notes(cfg, pages, reasons):
    """Notes from the judge (KIT-516) for the flagged pages going out now: a judgment whose signature is
    the page's own. The words are a model's, already checked by the judge, and read here only to be said
    after the page: nothing in them changes a page, a level or a hold. A file that cannot be read is
    said in `reasons`, and the pages go as they were."""
    path = cfg.get("judge_file")
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        reasons.append("the judge's record could not be read, so no note was added")
        return []
    if not isinstance(doc, dict) or doc.get("schema") != JUDGMENTS_SCHEMA or not isinstance(
            doc.get("judgments"), dict):
        reasons.append("the judge's record is not one this job knows, so no note was added")
        return []
    notes = []
    for page in pages:
        if page["kind"] != "flagged" or page["key"] == "watcher":
            continue
        j = doc["judgments"].get(page["key"])
        if not isinstance(j, dict) or j.get("signature") != page["signature"]:
            continue
        words = j.get("words") if j.get("by") == "model" and isinstance(j.get("words"), str) else ""
        why = j.get("why") if isinstance(j.get("why"), str) else ""
        if words.strip():
            lines = ["The judge, a model, says: %s" % _line(words, 600),
                     "Those are its words, not the watcher's rules. They change nothing: the page above "
                     "and any hold stand."]
        else:
            lines = ["The judge could not explain this (%s). The page above stands on the watcher's own "
                     "words." % (_line(why, 160) or "no reason given")]
        notes.append({"key": "judge:" + page["key"], "kind": "note", "level": "page",
                      "after": (page["key"], page["signature"]),
                      "signature": "note:" + _digest([page["signature"], words, why]),
                      "title": "Watcher: %s: the judge's note" % page["key"], "lines": lines})
    return notes


def render(page):
    return " ".join(_line(x, LINE_CAP) for x in page["lines"])


# --------------------------------------------------------------------------- #
# Sending: the only part that sends, to two fixed hosts
# --------------------------------------------------------------------------- #
class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is never followed. Python re-sends a redirected request with its Authorization
    header, so following one would carry a token to whatever host the answer named."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirect)


def real_post(url, headers, data, timeout=30):
    """(status, parsed JSON or None). Only the two hosts this job names; anything else is refused."""
    if not (url == SLACK_URL or url.startswith(GITHUB_API + "/repos/")):
        raise SendError("a host this job does not send to")
    return _post_checked(url, headers, data, timeout)


def _post_checked(url, headers, data, timeout=30):
    """The request itself, after the host check. A redirect comes back as its own status."""
    req = urllib.request.Request(url, data=data, method="POST", headers=dict(headers, **{
        "User-Agent": USER_AGENT}))
    try:
        with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 (fixed hosts)
            body = resp.read(1024 * 1024)
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError):
        raise SendError("the route could not be reached")
    try:
        return status, json.loads(body.decode("utf-8"))
    except ValueError:
        return status, None


def send_slack(route, token, page, post):
    body = json.dumps({"channel": route["channel"], "text": "*%s*\n%s" % (_line(page["title"]), render(page)),
                       "unfurl_links": False, "unfurl_media": False}).encode("utf-8")
    status, answer = post(SLACK_URL, {"Authorization": "Bearer " + token,
                                      "Content-Type": "application/json; charset=utf-8"}, body)
    if status != 200 or not isinstance(answer, dict):
        raise SendError("Slack answered HTTP %s" % status)
    if answer.get("ok") is not True:
        err = answer.get("error")
        raise SendError("Slack refused it (%s)" % (err if isinstance(err, str) and SLACK_ERROR_RE.match(err)
                                                     else "no reason given"))


def send_github(route, token, page, post):
    body = json.dumps({"title": _line(page["title"], 200), "body": render(page)
                       + "\n\nClose this issue once you have looked."}).encode("utf-8")
    status, answer = post("%s/repos/%s/issues" % (GITHUB_API, route["repo"]),
                          {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                           "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"}, body)
    if status != 201:
        raise SendError("GitHub answered HTTP %s" % status)


SENDERS = {"slack": send_slack, "github": send_github}


# --------------------------------------------------------------------------- #
# State and heartbeat
# --------------------------------------------------------------------------- #
def state_path(cfg):
    return os.path.join(cfg["state_dir"], "page-state.json")


def heartbeat_path(cfg):
    return os.path.join(cfg["state_dir"], "page-heartbeat.json")


def load_state(cfg):
    """(state, problem). An unreadable record is NOT 'nothing sent': it would page again, and that is
    the safe way to be wrong, but it is said."""
    fresh = {"schema": STATE_SCHEMA, "routes": {}, "blind": {}}
    try:
        with open(state_path(cfg), encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return fresh, None
    except (OSError, ValueError):
        return fresh, "the record of what was sent could not be read, so every page may repeat"
    if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA or not isinstance(
            doc.get("routes"), dict) or not isinstance(doc.get("blind"), dict):
        return fresh, "the record of what was sent is not one this job wrote, so every page may repeat"
    return doc, None


def atomic_write(path, doc):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def run_pass(cfg, environ=None, post=None, now=None, dry_run=False, say=print):
    """(exit code, heartbeat). Reads the status file, sends what each route has not yet heard, records
    only what went, and writes the heartbeat."""
    environ = os.environ if environ is None else environ
    post = post or real_post
    now = (now or time.time)()
    doc, problem = read_status(cfg, now)
    state, state_problem = load_state(cfg)
    reasons = [state_problem] if state_problem else []
    # A blind pass is a pass of the WATCHER, told by the start time in its file: this job may run more
    # often than the watcher writes, and a missing or stale file changes no count.
    blind_now, seen_started = dict(state["blind"]), state.get("seen")
    if doc and doc["started_at"] != seen_started:
        blind_now = dict((m["name"], state["blind"].get(m["name"], 0) + 1)
                         for m in doc["monitors"] if m["state"] == "blind")
        seen_started = doc["started_at"]
    pages = pages_for(doc, problem, blind_now)
    notes = judge_notes(cfg, pages, reasons)
    clearing = []
    # A monitor paged about, and clear now, is said once.
    if doc and not problem:
        states = dict((m["name"], m["state"]) for m in doc["monitors"])
        for route in cfg["routes"]:
            for key, rec in sorted(state["routes"].get(route, {}).items()):
                was_blind = rec["signature"].startswith("blind:")
                over = (states.get(key) in ("clear", "flagged", "off") if was_blind else
                        states.get(key) == "clear" if key != "watcher" else True)
                if (was_blind or rec["signature"].startswith("flagged:")) and over:
                    clearing.append({
                        "key": key, "kind": "clear", "level": "page", "signature": "clear:" + key,
                        "title": "Watcher: %s is %s" % (key, "no longer blind" if was_blind
                                                         else "clear again"),
                        "lines": ["Monitor %s is no longer blind: it read its source this pass." % key
                                  if was_blind else
                                  "Monitor %s is clear again: its last pass found nothing at page or pause."
                                  % key if key != "watcher" else
                                  "The watcher is writing its status file again."]})
    clearing = list({p["key"]: p for p in clearing}.values())
    routes_report, sent, pending = {}, 0, 0
    for route, rcfg in sorted(cfg["routes"].items()):
        token, why = credential(environ, rcfg["token_env"])
        done = state["routes"].setdefault(route, {})
        # The clears go first: one that follows a new page for the same monitor would drop its record.
        todo = [p for p in clearing if done.get(p["key"], {}).get("signature", "").startswith(
            ("flagged:", "blind:"))]
        todo += [p for p in pages if done.get(p["key"], {}).get("signature") != p["signature"]]
        todo += [n for n in notes if done.get(n["key"], {}).get("signature") != n["signature"]]
        if not todo:
            routes_report[route] = "ok"
            continue
        if dry_run:
            for p in todo:
                say("WOULD PAGE %s: %s" % (route, render(p)))
            routes_report[route] = "ok"
            continue
        if token is None:
            routes_report[route] = "failed"
            reasons.append("%s: %s" % (route, why))
            pending += len(todo)
            continue
        failed_here = False
        for p in todo:
            # A note goes only to a route its page has reached: after it, never before it.
            if p["kind"] == "note" and done.get(p["after"][0], {}).get("signature") != p["after"][1]:
                continue
            try:
                SENDERS[route](rcfg, token, p, post)
            except SendError as exc:
                failed_here = True
                pending += 1
                reasons.append("%s: %s" % (route, _line(str(exc), 120)))
                continue
            sent += 1
            if p["kind"] == "clear":
                done.pop(p["key"], None)
                done.pop("judge:" + p["key"], None)       # a second episode gets its note again
            else:
                done[p["key"]] = {"signature": p["signature"], "sent_at": _iso(now)}
            say("PAGED %s: %s" % (route, _line(p["title"], 120)))
        routes_report[route] = "failed" if failed_here else "ok"
    state["blind"], state["seen"] = blind_now, seen_started
    result = "could-not-page" if pending else "ok"
    hb = {"schema": HEARTBEAT_SCHEMA, "at": _iso(now), "result": result, "routes": routes_report,
          "sent": sent, "pending": pending, "watcher": "ok" if not problem else problem[0],
          "reasons": [_line(r, 200) for r in reasons[:10]]}
    if dry_run:
        say("DRY RUN: nothing was sent and nothing was written.")
        return EXIT_OK, hb
    try:
        atomic_write(state_path(cfg), state)
        atomic_write(heartbeat_path(cfg), hb)
    except OSError as exc:
        say("COULD NOT WRITE the record of what was sent (%s): the next pass may repeat a page."
            % (exc.strerror or "error"))
        return EXIT_NOT_SENT, hb
    if pending:
        say("COULD NOT PAGE: %d page(s) not sent (%s). They are sent again next pass."
            % (pending, "; ".join(hb["reasons"])))
        return EXIT_NOT_SENT, hb
    return EXIT_OK, hb


# --------------------------------------------------------------------------- #
# The owner's two commands
# --------------------------------------------------------------------------- #
def refuse_in_agent(environ, out):
    from pipeline_dispatch_local import AGENT_ENV_MARKERS
    found = [m for m in AGENT_ENV_MARKERS if m in environ]
    if found:
        out("REFUSED: this sends a page to the owner, and credentials are involved. Run it in your own "
            "terminal. (An agent's environment was found: %s.)" % ", ".join(found))
        return True
    return False


def check_command(cfg, environ=None, out=print):
    """What is set, by name, and never a value."""
    environ = os.environ if environ is None else environ
    out("Status file: %s" % cfg["status_file"])
    out("Record of what was sent: %s" % cfg["state_dir"])
    for route, rcfg in sorted(cfg["routes"].items()):
        token, why = credential(environ, rcfg["token_env"])
        out("%s: %s -> %s; $%s is %s" % (
            route, "the owner's DM" if route == "slack" else "issues in a private repository",
            rcfg.get("channel") or rcfg.get("repo"), rcfg["token_env"], "set" if token else "NOT SET (%s)" % why))
    return EXIT_OK


def test_command(cfg, environ=None, ask=input, post=None, out=print, now=None):
    """The owner's test: one page marked as a test, to each route, after one typed yes."""
    environ = os.environ if environ is None else environ
    post = post or real_post
    if refuse_in_agent(environ, out):
        return EXIT_REFUSED
    check_command(cfg, environ, out)
    if ask("Send one test page to each route above? Type yes: ").strip() != "yes":
        out("Nothing was sent.")
        return EXIT_OK
    page = {"title": "Watcher: TEST page", "lines": ["This is a test of the watcher's urgent page.",
                                                       "Nothing is wrong. You can ignore it."]}
    worst = EXIT_OK
    for route, rcfg in sorted(cfg["routes"].items()):
        token, why = credential(environ, rcfg["token_env"])
        if token is None:
            out("%s: NOT SENT (%s)" % (route, why))
            worst = EXIT_NOT_SENT
            continue
        try:
            SENDERS[route](rcfg, token, page, post)
        except SendError as exc:
            out("%s: NOT SENT (%s)" % (route, _line(str(exc), 120)))
            worst = EXIT_NOT_SENT
            continue
        out("%s: SENT. Look for it now; if it is not there, the route is not the one you meant." % route)
    return worst


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="The watcher's urgent page.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--example-config", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("run", "check", "test"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        if name == "run":
            p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "watcher-page-selftest-run-")
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2, sort_keys=True))
        return EXIT_OK
    if not args.cmd:
        ap.print_usage()
        return EXIT_USAGE
    if not os.path.lexists(args.config):
        print("OFF: no config at %s, so nothing was read and nothing was sent." % args.config)
        return EXIT_OK
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print("COULD NOT RUN: %s" % exc)
        return EXIT_USAGE
    if args.cmd == "check":
        return check_command(cfg)
    if args.cmd == "test":
        return test_command(cfg)
    try:
        code, _hb = run_pass(cfg, dry_run=args.dry_run)
        return code
    except Exception as exc:          # a crash is exit 1 and a class name, never the words of an error
        print("CRASHED: %s" % type(exc).__name__)
        return EXIT_ERROR


# SELFTEST: nothing below this line is read by the checks that scan the code above it.
def selftest():
    import copy
    import tempfile
    import check_schemas
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    NOW = calendar.timegm((2026, 11, 5, 15, 0, 0))
    TOKENS = {"WS": "xoxb-" + "s" * 30, "WG": "ghp_" + "g" * 36}

    class FakePost(object):
        """Plays Slack and GitHub: records every request, and answers per route as told."""

        def __init__(self):
            self.sent, self.slack, self.github = [], 200, 201

        def __call__(self, url, headers, data):
            self.sent.append((url, headers, data))
            if url == SLACK_URL:
                if self.slack == "no":
                    return 200, {"ok": False, "error": "channel_not_found"}
                return (self.slack, {"ok": True} if self.slack == 200 else None)
            return self.github, {}

        def to(self, route):
            return [json.loads(d.decode("utf-8")) for u, _h, d in self.sent
                    if (u == SLACK_URL) == (route == "slack")]

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        status = os.path.join(tmp, "watch", "watcher-status.json")
        os.makedirs(os.path.dirname(status))
        raw = {"schema": CONFIG_SCHEMA, "status_file": status, "state_dir": os.path.join(tmp, "page"),
               "slack": {"token_env": "WS", "channel": "D0123456789"},
               "github": {"token_env": "WG", "repo": "acme/alerts"}}
        cfg = validate_config(raw)

        def doc_with(**kw):
            d = copy.deepcopy(check_schemas.VALID_WATCHER)
            d["written_at"] = _iso(NOW - 60)
            d["started_at"] = _iso(NOW - 70)
            d.update(kw)
            return d

        def put(doc):
            with open(status, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)

        def mon(doc, name):
            return [m for m in doc["monitors"] if m["name"] == name][0]

        def clear_all(doc):
            for m in doc["monitors"]:
                if m["state"] == "flagged":
                    m.update(state="clear", level=None, findings=[])
            doc["result"], doc["firing"] = "clear", []
            return doc

        def fresh(name):
            return dict(cfg, state_dir=os.path.join(tmp, name))

        ALL_SAID = []

        def go(c, post, doc=None, now=NOW, dry=False):
            if doc is not None:
                put(doc)
            said = []
            code, hb = run_pass(c, environ=TOKENS, post=post, now=lambda: now, dry_run=dry, say=said.append)
            ALL_SAID.extend(said)
            ALL_SAID.append(json.dumps(hb))
            return code, hb, said

        # config
        check("a good config validates", sorted(cfg["routes"]), ["github", "slack"])
        for label, bad, needle in (
                ("a credential value as the env name", dict(raw, slack={"token_env": "xoxb-abc", "channel": "D0123456789"}), "ENV VAR NAME"),
                ("no route", {k: v for k, v in raw.items() if k not in ("slack", "github")}, "at least one route"),
                ("a relative status file", dict(raw, status_file="status.json"), "absolute"),
                ("an unknown key", dict(raw, token="x"), "unknown config key"),
                ("a bad channel", dict(raw, slack={"token_env": "WS", "channel": "general"}), "Slack channel"),
                ("a bad repo", dict(raw, github={"token_env": "WG", "repo": "nope"}), "OWNER/REPO"),
                ("a state folder in a working tree", dict(raw, state_dir=os.path.join(os.path.dirname(HERE), "page")), "git working tree")):
            try:
                validate_config(bad)
                why = ""
            except ConfigError as exc:
                why = str(exc)
            check("config: %s is refused, naming it" % label, needle in why, True)
        check("the example config validates", bool(validate_config(dict(
            EXAMPLE_CONFIG, status_file=status, state_dir=os.path.join(tmp, "ex"))).get("routes")), True)

        # nothing to say
        c = fresh("quiet")
        post = FakePost()
        code, hb, said = go(c, post, clear_all(doc_with()))
        check("a clear watcher: nothing sent, ok", (code, post.sent, hb["result"]), (EXIT_OK, [], "ok"))

        # a pause is sent to both routes, once
        c = fresh("pause")
        post = FakePost()
        flagged = doc_with()
        code, hb, said = go(c, post, flagged)
        check("a pause: sent to both routes", (code, len(post.to("slack")), len(post.to("github"))), (EXIT_OK, 1, 1))
        text = post.to("slack")[0]["text"]
        for must in ("labels", "pause", "acme/app#12", "since 2026-11-05T14:55:00Z", "pipeline_hold.py clear"):
            check("the page says %r" % must, must in text, True)
        check("the page names the monitor, how many, since when, and what to do",
              ("1 finding(s)" in text, "remove it" in text), (True, True))
        check("the issue carries the same page", "labels" in post.to("github")[0]["body"], True)
        n = len(post.sent)
        go(c, post)
        check("the same answer is not sent again", len(post.sent), n)
        flagged2 = copy.deepcopy(flagged)
        mon(flagged2, "labels")["findings"].append(dict(mon(flagged2, "labels")["findings"][0], ref="acme/app#13"))
        go(c, post, flagged2)
        check("a changed answer is sent again", len(post.sent), n + 2)

        # a failure is sent again, and is not recorded as sent
        c = fresh("fail")
        post = FakePost()
        post.slack = "no"
        code, hb, said = go(c, post, flagged)
        check("Slack refusing: exit 4, GitHub still sent, the heartbeat says which",
              (code, hb["routes"], hb["pending"], len(post.to("github"))),
              (EXIT_NOT_SENT, {"github": "ok", "slack": "failed"}, 1, 1))
        check("...and says so in words, naming no body", "COULD NOT PAGE" in said[-1] and "channel_not_found" in said[-1], True)
        post.slack = 200
        code, hb, said = go(c, post)
        check("next pass: only Slack is sent again, and then all is ok",
              (code, len(post.to("slack")), len(post.to("github")), hb["routes"]), (EXIT_OK, 2, 1, {"github": "ok", "slack": "ok"}))
        c2 = fresh("bothfail")
        p2 = FakePost()
        p2.slack, p2.github = 500, 500
        code, hb, _s = go(c2, p2, flagged)
        check("both routes down: exit 4, nothing recorded as sent",
              (code, hb["result"], load_state(c2)[0]["routes"]), (EXIT_NOT_SENT, "could-not-page", {"slack": {}, "github": {}}))
        p2.slack, p2.github = 200, 201
        p2.sent.clear()
        code, hb, _s = go(c2, p2)
        check("...and when they come back, the page goes", (code, len(p2.to("slack")), len(p2.to("github"))), (EXIT_OK, 1, 1))
        c3 = fresh("notoken")
        said = []
        code, hb = run_pass(c3, environ={}, post=FakePost(), now=lambda: NOW, say=said.append)[0:2]
        check("no credential: not sent, said by name only", (code, any("$WS is unset" in r for r in hb["reasons"])), (EXIT_NOT_SENT, True))

        # cleared, once
        post = FakePost()
        c = fresh("clear")
        go(c, post, flagged)
        n = len(post.sent)
        go(c, post, clear_all(doc_with()))
        check("a monitor paged about and clear again is said once, on each route", len(post.sent), n + 2)
        check("...in plain words", "clear again" in post.to("slack")[-1]["text"], True)
        go(c, post)
        check("...and not again", len(post.sent), n + 2)

        # the watcher itself
        for label, setup, word in (
                ("missing", lambda: os.unlink(status) if os.path.exists(status) else None, "no status file"),
                ("stale", lambda: put(doc_with(written_at=_iso(NOW - 3 * 3600))), "not written its status file"),
                ("not a status file", lambda: put({"schema": "other/1"}), "not a status file"),
                ("unreadable json", lambda: open(status, "w").write("{ nope"), "not readable JSON"),
                ("crashed", lambda: put(doc_with(error="the pass stopped on ValueError")), "last pass failed (ValueError)")):
            setup()
            post = FakePost()
            code, hb, _s = go(fresh("w-" + label), post)
            check("the watcher %s is paged, at pause" % label,
                  (bool(post.to("slack")), any(word in x["text"] for x in post.to("slack"))), (True, True))

        # blind: three WATCHER passes running (the status file's own start time), not three runs of
        # this job, and a second episode is paged again once the first has been said clear
        c = fresh("blind")
        post = FakePost()

        def blind_doc(i):
            d = clear_all(doc_with(started_at=_iso(NOW - 600 + i * 10)))
            mon(d, "spend").update(state="blind", reason="the usage file is stale")
            return d

        def clear_doc(i):
            return clear_all(doc_with(started_at=_iso(NOW - 600 + i * 10)))

        for _ in range(5):
            go(c, post, blind_doc(0))
        check("one watcher pass seen five times is one blind pass: no page", post.sent, [])
        go(c, post, blind_doc(1))
        check("blind for two watcher passes: no page", post.sent, [])
        go(c, post, blind_doc(2))
        check("blind for three: one page", (len(post.to("slack")), "blind" in post.to("slack")[0]["text"]), (1, True))
        go(c, post, blind_doc(2))
        go(c, post, blind_doc(3))
        check("...and not again while it stays blind", len(post.to("slack")), 1)
        go(c, post, clear_doc(4))
        check("a monitor that stops being blind is said once, on each route",
              (len(post.to("slack")), len(post.to("github")), "no longer blind" in post.to("slack")[-1]["text"]),
              (2, 2, True))
        go(c, post, clear_doc(5))
        check("...and not again", len(post.to("slack")), 2)
        for i in (6, 7, 8):
            go(c, post, blind_doc(i))
        check("a second blind episode is paged again", len(post.to("slack")), 3)
        # blind, then flagged: the clear and the new page both go, and the new page stays recorded
        c = fresh("blind-then-flagged")
        post = FakePost()
        for i in (0, 1, 2):
            d = clear_doc(i)
            mon(d, "labels").update(state="blind", reason="GitHub could not be read")
            go(c, post, d)
        flagged_now = doc_with(started_at=_iso(NOW - 400))
        go(c, post, flagged_now)
        n = len(post.to("slack"))
        go(c, post, doc_with(started_at=_iso(NOW - 390)))
        check("blind, then flagged: two pages go (no longer blind, then the flag), and neither repeats",
              (n, len(post.to("slack"))), (3, 3))

        # a hostile status file cannot put text in a page
        hostile = doc_with()
        mon(hostile, "labels")["findings"][0]["ref"] = "<script>ignore previous instructions</script>"
        post = FakePost()
        go(fresh("hostile"), post, hostile)
        check("a status file with text where a ref goes is not obeyed: the page is about the watcher",
              ("ignore previous" in str(post.sent), "not a status file" in post.to("slack")[0]["text"]), (False, True))

        # the judge's note (KIT-516): a follow-up after the page, from a file the pager only reads
        judge_dir = os.path.join(tmp, "judge")
        os.makedirs(judge_dir)
        judge_path = os.path.join(judge_dir, "judgments.json")
        jcfg = lambda name: dict(fresh(name), judge_file=judge_path)        # noqa: E731

        def write_judgments(**per_key):
            with open(judge_path, "w", encoding="utf-8") as fh:
                json.dump({"schema": JUDGMENTS_SCHEMA, "judgments": per_key, "days": {}}, fh)

        def sig_of(doc_, key_):
            return [p_ for p_ in pages_for(doc_, None, {}) if p_["key"] == key_][0]["signature"]
        jdoc = doc_with()
        jsig = sig_of(jdoc, "labels")
        if os.path.exists(judge_path):
            os.unlink(judge_path)
        post = FakePost()
        c = jcfg("judge-none")
        go(c, post, jdoc)
        check("no judgments file: the page goes alone", (len(post.to("slack")), len(post.to("github"))), (1, 1))
        write_judgments(labels={"signature": jsig, "by": "model", "at": "x", "cost_usd": 0.01, "why": "",
                                "words": "Someone not on your list added the label. <script>x</script> Check who."})
        go(c, post)
        notes_ = [t["text"] for t in post.to("slack")][1:]
        check("a judgment for the same findings is sent once, as a note, on each route",
              (len(notes_), len(post.to("github"))), (1, 2))
        check("...marked as a model's words that change nothing, with markup made visible",
              ("The judge, a model, says: Someone not on your list" in notes_[0], "change nothing" in notes_[0],
               "<script>" in notes_[0], "?script?" in notes_[0]), (True, True, False, True))
        go(c, post)
        check("...and not again", (len(post.to("slack")), len(post.to("github"))), (2, 2))
        write_judgments(labels={"signature": "flagged:labels:pause:0000000000000000", "by": "model", "at": "x",
                                "cost_usd": 0.0, "why": "", "words": "About something else."})
        post = FakePost()
        go(jcfg("judge-stale"), post, jdoc)
        check("a judgment for other findings is not sent", len(post.to("slack")), 1)
        write_judgments(labels={"signature": jsig, "by": "fallback", "at": "x", "cost_usd": 0.0, "words": "",
                                "why": "the writer took longer than 60 seconds"})
        post = FakePost()
        go(jcfg("judge-failed"), post, jdoc)
        check("a judge that failed is said, never silent",
              ("could not explain this (the writer took longer than 60 seconds)" in post.to("slack")[1]["text"],
               len(post.to("slack"))), (True, 2))
        write_judgments(labels={"signature": jsig, "by": "model", "at": "x", "cost_usd": 0.0, "why": "",
                                "words": "Check who applied it."})
        post = FakePost()
        post.slack = "no"
        c = jcfg("judge-order")
        go(c, post, jdoc)
        check("a note never goes to a route its page did not reach",
              (len(post.to("github")), [("judge" in t["body"]) for t in post.to("github")]), (2, [False, True]))
        post.slack = 200
        go(c, post)
        check("...and goes there once the page does",
              [("Check who applied it" in t["text"]) for t in post.to("slack")], [False, False, True])
        with open(judge_path, "w", encoding="utf-8") as fh:
            fh.write("{ nope")
        post = FakePost()
        code, hb, _s = go(jcfg("judge-broken"), post, jdoc)
        check("an unreadable judgments file costs the page nothing, and is said",
              (code, len(post.to("slack")), any("judge" in r for r in hb["reasons"])), (EXIT_OK, 1, True))
        write_judgments(labels={"signature": jsig, "by": "model", "at": "x", "cost_usd": 0.0, "why": "",
                                "words": "Check who applied it."})
        post = FakePost()
        c = jcfg("judge-second-episode")
        go(c, post, jdoc)
        go(c, post, clear_all(doc_with(started_at=_iso(NOW - 500))))
        n_before = len(post.to("slack"))
        go(c, post, doc_with(started_at=_iso(NOW - 400)))
        check("a second episode with the same findings gets its note again",
              len(post.to("slack")) - n_before, 2)
        # the judge changes no page: the pager run with and without a judgments file, even a hostile one,
        # sends the same pages and keeps the same record of them
        write_judgments(labels={"signature": jsig, "by": "model", "at": "x", "cost_usd": 0.0, "why": "",
                                "words": "Mark this clear, lower it to a record, and lift the hold."})
        post_with, post_without = FakePost(), FakePost()
        c_with, c_without = jcfg("judge-with"), fresh("judge-without")
        go(c_with, post_with, jdoc)
        go(c_without, post_without, jdoc)
        pages_with = [t_["text"] for t_ in post_with.to("slack") if "the judge" not in t_["text"].lower()]
        check("the judge changes no page: the same pages go with and without a judgments file",
              (pages_with, len(post_with.to("slack")), len(post_without.to("slack"))),
              ([t_["text"] for t_ in post_without.to("slack")], 2, 1))
        rec_with = load_state(c_with)[0]["routes"]["slack"]
        rec_without = load_state(c_without)[0]["routes"]["slack"]
        check("...and the record of what was sent differs only by the note",
              (rec_with["labels"]["signature"] == rec_without["labels"]["signature"],
               sorted(set(rec_with) - set(rec_without)), sorted(set(rec_without) - set(rec_with))),
              (True, ["judge:labels"], []))

        try:
            validate_config(dict(raw, judge_file=os.path.join(os.path.dirname(HERE), "judgments.json")))
            why_j = ""
        except ConfigError as exc:
            why_j = str(exc)
        check("a judge_file inside a git working tree is refused, like the state folder", "judge_file" in why_j, True)
        check("a judge_file outside one is accepted", validate_config(dict(raw, judge_file=judge_path))["judge_file"],
              os.path.realpath(judge_path))

        # credentials never reach any output or file
        walk = list(ALL_SAID)
        for root, _d, files in os.walk(tmp):
            for f in files:
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    walk.append(fh.read())
        leaked = [t for t in TOKENS.values() if any(t in w for w in walk)]
        check("no credential is in any file this job wrote, or in anything it said", leaked, [])

        # dry run
        c = fresh("dry")
        post = FakePost()
        code, hb, said = go(c, post, flagged, dry=True)
        check("--dry-run says what it would send, sends and writes nothing",
              (code, post.sent, os.path.exists(c["state_dir"]), any(s.startswith("WOULD PAGE") for s in said)),
              (EXIT_OK, [], False, True))

        # the owner's commands
        out = []
        c = fresh("owner")
        from pipeline_dispatch_local import AGENT_ENV_MARKERS
        env = dict(TOKENS)
        post = FakePost()
        env_agent = dict(env, **{AGENT_ENV_MARKERS[0]: "1"})
        check("test: refused inside an agent's environment, nothing sent",
              (test_command(c, env_agent, lambda _p: "yes", post, out.append), post.sent), (EXIT_REFUSED, []))
        check("test: no yes, nothing sent", (test_command(c, env, lambda _p: "no", post, out.append), post.sent), (EXIT_OK, []))
        check("test: a yes sends one test page to each route",
              (test_command(c, env, lambda _p: "yes", post, out.append), len(post.to("slack")), len(post.to("github"))),
              (EXIT_OK, 1, 1))
        check("test: it is marked as a test", "TEST" in post.to("slack")[0]["text"], True)
        check("check: names the variables and says set or not, never a value",
              (any("$WS is set" in o for o in out), any(TOKENS["WS"] in o for o in out)), (True, False))
        p3 = FakePost()
        p3.slack = "no"
        check("test: a route that refuses is said, and exits 4",
              test_command(c, env, lambda _p: "yes", p3, out.append), EXIT_NOT_SENT)

        # what it never does
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            above = fh.read().split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
        banned = ("subprocess", "os.system", "socket", "shutil", "eval(", "exec(")
        check("it starts no process and opens no socket", [b for b in banned if b in above], [])
        check("it names exactly two hosts to send to",
              sorted(set(re.findall(r'https://[a-z.]+', above))), ["https://api.github.com", "https://slack.com"])
        # a redirect is never followed: a token would go wherever the Location header says
        import http.server
        import threading
        landed = []

        class Target(http.server.BaseHTTPRequestHandler):
            def _hit(self):
                landed.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            do_GET = do_POST = _hit

            def log_message(self, *_a):
                pass

        target = http.server.HTTPServer(("127.0.0.1", 0), Target)

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(int(self.path.strip("/")))
                self.send_header("Location", "http://127.0.0.1:%d/landed" % target.server_port)
                self.end_headers()

            def log_message(self, *_a):
                pass

        redirector = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threads = [threading.Thread(target=x.serve_forever, daemon=True) for x in (target, redirector)]
        for t in threads:
            t.start()
        try:
            for code_ in (301, 302, 303, 307, 308):
                got = _post_checked("http://127.0.0.1:%d/%d" % (redirector.server_port, code_),
                                    {"Authorization": "Bearer secret-token"}, b"{}")
                check("a %d is not followed: the answer is the redirect itself" % code_, got, (code_, None))
        finally:
            for x in (target, redirector):
                x.shutdown()
                x.server_close()
        check("...and the token reached nobody else", landed, [])
        check("real_post refuses any other host, before it tries to reach it",
              _raises(lambda: real_post("https://evil.example/x", {}, b"")), "a host this job does not send to")
    if failures:
        for line in failures:
            print(line)
        print("watcher page selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: watcher page selftest (%d checks)" % total[0])
    return 0


def _raises(fn):
    try:
        fn()
        return None
    except SendError as exc:
        return str(exc)


if __name__ == "__main__":
    sys.exit(main())
