#!/usr/bin/env python3
"""The pipeline's settings, in one folder on this Mac (KIT-853, KIT-854, KIT-855).

    python3 scripts/pipeline_settings.py where  [--settings-dir DIR]   # where everything is
    python3 scripts/pipeline_settings.py init   [--settings-dir DIR]   # make the folder and machine.conf
    python3 scripts/pipeline_settings.py gather [--settings-dir DIR] [--parts-dir DIR] [--apply]
    python3 scripts/pipeline_settings.py check  [--settings-dir DIR]   # the folder's rules, read-only
    python3 scripts/pipeline_settings.py write  [--settings-dir DIR] [--apply]
    python3 scripts/pipeline_settings.py export --to FILE [--settings-dir DIR]
    python3 scripts/pipeline_settings.py import --from FILE [--settings-dir DIR] [--apply]
    python3 scripts/pipeline_settings.py map                           # where each value goes
    python3 scripts/pipeline_settings.py --selftest

WHY. Eleven settings files repeat the same values: the pipeline's account, the dispatcher, the
names the keys are stored under, the clone the jobs run from. A value changed in one file and
missed in another breaks a part quietly. Settings live on the machine, never in a repository.
So one private folder holds them, each value is typed once, and each part's own settings file is
written from it.

THE FOLDER. `~/Library/Application Support/claude-pipeline/settings`, yours, mode 700. Its own
place is a setting too: `--settings-dir`, else the PIPELINE_SETTINGS_DIR variable, else that
default. (It cannot sit in its own file.) It holds `machine.conf`, mode 600, in three kinds of
section:

  [places]   where things are. PARTS_DIR: the folder the part files are in (empty: this kit
             folder). <NAME>_CONF: one part file kept somewhere else, by its full path, such as
             BRIDGE_CONF=/full/path/bridge.conf. CONSENT_RECORD, STATE_DIR and SECRETS_DIR: the
             places of the consent record, the pipeline account's state folder and its secrets
             folder. `where` prints each one, and says what reads it.
  [shared]   a value typed once. MAP below names every part file and key it is written to.
  [stage-e.conf], [queue.conf] and the rest: one part file's own values. A key there wins over a
             shared value, and over the projects, for that file, and `check` says so.

  KEY=value, one per line, parsed and never sourced, as the part files are. `#` starts a comment
  only at the start of a line. A part's section takes only the keys its example names.

ONE FILE PER PROJECT. `projects/<name>.conf` holds a project's REPO (OWNER/NAME), its TEAM_KEY,
and PARTS: the parts that cover it, by their ids in the parts list. COMPOSED below names each list
key the projects fill, such as the repositories the review jobs watch: a part's list is the
projects it covers. A list is compared as a set, so a new order is no change. A list key no
project covers is left as it is.

A FIRST RUN: `gather`. On a Mac whose part files are already filled, `gather` reads them and shows
the folder it would make: a value is shared only when every part file that takes it sets the
same; the list keys become projects, one per repository and one per team, joined only where
queue.conf's REPO_BY_TEAM pairs them, never by a guess; everything else goes in each part's own
section. A value shaped like a secret, a key another tool writes and a key the example does not
name are never copied: each is named, with why. Before it shows anything it proves the folder
would change no part file. It writes nothing. `gather --apply`, run by you in your own terminal,
shows it again and asks for a typed yes, reads the part files once more, and writes the folder
only where no settings are yet: a folder that is not there, is empty, or holds the file `init` makes,
untouched. It writes the whole folder or nothing: a failure part way removes what that run made.

HAND EDITS. `write --apply` and `gather --apply` keep RECORD beside machine.conf: what the folder last
gave each part file. On the next `write`, a key whose value in the file is neither that nor the
folder's was changed by hand: it is shown with both values, and nothing is written. `write --apply`
then asks, for each one, which wins. `folder` writes the folder's value back into the file. `file`
puts the file's value in that part's own section of machine.conf, so it stays in that file alone
and a shared value still reaches the others. A value shaped like a secret is never taken, nor a key
the file no longer sets. With no record yet, a difference is the folder's own change, shown as such.

A NEW MAC. `export --to FILE` writes one plain file: machine.conf and every project's file, each
under a `=== <path>` line, and which part files were on this Mac. Never the record, never a secret:
`check` must pass first. `import --from FILE` shows the folder the file makes, and anything it names
that is not on this Mac, and writes nothing. `import --apply` asks for a typed yes and writes the
folder, whole or nothing, only where no settings are yet. It never writes a part file: copy the
examples of the parts you want on, then `write`.

WHAT `write` DOES. For each part file on this Mac it works out what the folder changes, and shows
each change: the file, the key, the value now and the value after. It writes nothing. With
`--apply`, run by you in your own terminal, it writes them. Every other byte of the file stays, and
so does its mode. The file is swapped in whole from a file made new beside it, never written
through a link, and read back. A part file that is not there is off on this Mac: nothing is written
for it. A problem anywhere, and nothing is written at all: every part file is read again just before
the first one is written. Only a failure part way through the writing itself, such as a full disk,
can leave some files written, and then the output names each file, written or not.

WHAT `check` FAILS ON (`write` runs it first, so a folder that fails it is never written from)
  secret      a value or a comment shaped like a credential (the notifier's scrub, the one the tech
              lead's installer uses on its own settings, plus a Slack app token and a signing
              secret), a long unbroken run of mixed letters and digits, or a key named like a
              secret. The folder holds the NAME a key is stored under (a KEY_ENV), never the key.
              Each problem names the line and the key, never the value.
  private     the folder or anything in it belongs to another account, or another account can
              read it.
  link        the folder or anything in it is a link.
  repository  the folder is inside a git working tree.
  lines       a line that is not KEY=value or a section; an unknown section or key; a key twice; a
              place that is not a full path; or a key another tool writes (TOOL_KEYS), which the
              folder would undo.
  projects    a project file with another key, a REPO or TEAM_KEY of the wrong shape, a part that
              takes no list, neither a REPO nor a TEAM_KEY, a REPO or TEAM_KEY two projects share,
              or a file in projects/ that is not <name>.conf. A project the queue covers needs a
              TEAM_KEY, and a REPO too unless [queue.conf] sets REPO_BY_LABEL: the queue's own
              installer refuses a team with no repository.

WHAT IT NEVER DOES (asserted in --selftest). It runs no installer and no other program, loads no
job, and opens no connection. It reads the folder, the kit's parts list and examples, the part
files, and an export file it is given. Only `init`, `gather --apply`, `import --apply`, `export`
and `write --apply` write: the folder, the export file, and the part files with the record. It
removes nothing but what a failed run of its own just made.

Exit: 0 done, or nothing to do (the words say which) · 1 a write failed (the file is named)
      · 2 the folder or a part file needs fixing first, or a usage error · 3 refused (inside a
      Claude session, or as root) · 4 something could not be read · 10 `write`, `gather` or `import`
      found something to write and wrote none, a hand edit waits for its choice, or the yes was not
      given.
"""
import argparse
import json
import os
import re
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

EX_OK, EX_FAILED, EX_FIX, EX_REFUSED, EX_UNREAD, EX_WAITING = 0, 1, 2, 3, 4, 10

DEFAULT_DIR = os.path.join("~", "Library", "Application Support", "claude-pipeline", "settings")
DIR_ENV = "PIPELINE_SETTINGS_DIR"
MACHINE = "machine.conf"
PARTS_LIST = "pipeline-parts.json"
FILE_MAX = 1 << 20
WALK_MAX = 500
FOLDER_MODE, FILE_MODE = 0o700, 0o600       # what `init` makes
OTHER_ACCESS = 0o077                         # any of these bits, and another account can reach it

# Each shared value: its name under [shared], what it is, and the part files it is written to,
# as file stems. "stem" means the same key in that file; "stem:KEY" names another key. Only the
# values the part files' own examples say must be the same are here; the selftest checks every
# target against its example. A value only some parts share is left to each part's section.
MAP = (
    ("ROLE_ACCOUNT", "the pipeline's own account, the one the review jobs and the dispatcher run as",
     "stage-e stage-a stage-a:DISPATCHER_ACCOUNT acceptance bridge chat-lane notifier queue sweep "
     "update watcher"),
    ("ROLE_KIT_CLONE", "that account's clone of this kit, the one the review jobs run from",
     "acceptance bridge notifier queue sweep"),
    ("ENV_FILE", "that account's own env file, the one the review installer keeps its keys in",
     "acceptance bridge notifier queue sweep chat-lane:ROLE_ENV_FILE"),
    ("LINEAR_KEY_ENV", "the NAME the pipeline's tracker key is stored under",
     "stage-e stage-a acceptance bridge notifier queue sweep"),
    ("GITHUB_TOKEN_ENV", "the NAME the review jobs' code host token is stored under",
     "stage-e acceptance"),
    ("DISPATCHER_SERVICE", "the dispatcher's launchd label", "stage-e stage-a acceptance chat-lane update"),
    ("DISPATCHER_CONFIG", "the dispatcher's own settings file", "stage-e stage-a acceptance chat-lane"),
    ("DISPATCHER_PORT", "the port the dispatcher listens on", "stage-e stage-a chat-lane update"),
    ("DISPATCHER_ENV_FILE", "the env file the dispatcher hands every session it starts",
     "chat-lane update notifier bridge"),
    ("KIT_REPO_URL", "the https address of this kit's repository", "stage-e stage-a"),
    ("OWNER_USER_ID", "your own user id in the tracker", "stage-a bridge acceptance"),
    ("TICKET_MODEL", "the model every ticket the pipeline files is tagged with", "stage-a stage-e"),
    ("HOLD_FILE", "the watcher's hold file", "stage-e stage-a"),
    ("NOTIFIER_JOB_LABEL", "the notifier job's launchd label", "notifier:JOB_LABEL bridge stage-e"),
    ("NOTIFIER_STATE_DIR", "the notifier's state folder", "notifier:STATE_DIR bridge"),
    ("NOTIFIER_TOKEN_ENV", "the NAME the notifier's chat token is stored under",
     "notifier:CHAT_TOKEN_ENV chat-lane"),
    ("HEALTH_STATUS_FILE", "the health check's status file", "update stage-e bridge:STANDUP_HEALTH_FILE"),
    ("HEALTH_WATCH_INTERVAL_SECONDS", "how often the health check runs, in seconds",
     "update:WATCH_INTERVAL_SECONDS stage-e"),
    ("SPRINT_PLAN_FILE", "the sprint plan's file", "queue bridge:STANDUP_SPRINT_PLAN_FILE"),
)
SHARED_NAMES = tuple(name for name, _what, _to in MAP)

# Keys a part file takes all together or not at all, as its example says.
TOGETHER = {"update.conf": ("DISPATCHER_SERVICE", "ROLE_ACCOUNT", "DISPATCHER_ENV_FILE")}

# Keys another tool writes into a part file. The folder never holds them, so `write` never undoes
# that tool's change.
TOOL_KEYS = {
    ("bridge.conf", "APP_NAME"): "the one command asks for the tech lead's name and writes it",
    ("bridge.conf", "DIRECT_ASKS"): "its own card switches it on, after its checks",
    ("bridge.conf", "PUSH_MODE"): "the instant-replies card switches it on, after its checks",
    ("bridge.conf", "PUSH_JOB_LABEL"): "the instant-replies card writes it",
    ("update.conf", "CREDENTIAL_EXPIRIES"): "the one command and the health check write the end dates",
    ("queue.conf", "PLANNED_LOAD"): "the tech lead changes it when you ask in your DM",
    ("queue.conf", "DAY_STOP"): "the tech lead changes it when you ask in your DM",
    ("queue.conf", "LANES_PER_REPO"): "the tech lead changes it when you ask in your DM",
}

# The list keys the projects fill: (part id in the parts list, its file, the key, what each item is).
# "repo" is OWNER/NAME, "team" a team key, "team=repo" TEAM=OWNER/NAME; a list is comma-separated.
# A list whose meaning is narrower stays each part's own: the stand-up's STANDUP_REPOS, the bridge's
# ACCEPTANCE_REPOS, the watcher's TRACKER_TEAMS, RUNNER_LANE_REPOS, REPO_BY_LABEL, REVIEWS_TEAM_KEY.
COMPOSED = (
    ("review", "stage-e.conf", "REVIEW_REPOS", "repo"),
    ("review", "stage-e.conf", "MANAGED_TEAM_KEYS", "team"),
    ("idea-gate", "stage-a.conf", "PLANNED_REPOS", "repo"),
    ("work-queue", "queue.conf", "TEAM_KEYS", "team"),
    ("work-queue", "queue.conf", "REPO_BY_TEAM", "team=repo"),
    ("tech-lead", "bridge.conf", "TEAM_KEYS", "team"),
    ("paging", "notifier.conf", "TEAM_KEYS", "team"),
    ("cleanup", "sweep.conf", "TEAM_KEYS", "team"),
    ("watcher", "watcher.conf", "WATCH_REPOS", "repo"),
    ("acceptance-check", "acceptance.conf", "ACCEPTANCE_REPOS", "repo"),
)
COMPOSED_PARTS = tuple(sorted(set(entry[0] for entry in COMPOSED)))
LIST_KEYS = frozenset((entry[1], entry[2]) for entry in COMPOSED)
KIND_WORDS = {"repo": "OWNER/NAME repositories", "team": "team keys", "team=repo": "TEAM=OWNER/NAME pairs"}
PROJECTS = "projects"
PROJECT_KEYS = ("REPO", "TEAM_KEY", "PARTS")
# What the folder last gave each part file, kept beside machine.conf: this Mac's state, never a setting, so
# an export never carries it. The next write tells a hand edit from a change of the folder's by it.
RECORD = "written.json"
RECORD_SCHEMA = "pipeline-settings-written/1"
EXPORT_MARK = "=== "
MISSING = object()
PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
TEAM_RE = re.compile(r"^[A-Z0-9]{1,10}\Z")

# The places, besides the part files. (key, what it is, its default, what reads it today.)
STATE_DEFAULT = "~/.stage-e"
PLACES = (
    ("CONSENT_RECORD", "The consent record", None,
     "Not read from here yet: the one command still takes --consent-record."),
    ("STATE_DIR", "The pipeline account's state folder", STATE_DEFAULT,
     "Not read from here yet: each part still names its own state files."),
    ("SECRETS_DIR", "The pipeline account's secrets folder", STATE_DEFAULT,
     "Not read from here yet: each part still names its own env file."),
)
PLACE_KEYS = ("PARTS_DIR",) + tuple(key for key, _w, _d, _r in PLACES)

SECTION_RE = re.compile(r"^\[([^\]\s]+)\]\Z")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*\Z")
EXAMPLE_KEY_RE = re.compile(r"^#?[ \t]?([A-Z][A-Z0-9_]*)=")
PATH_RE = re.compile(r"^[^\x00-\x1f\x7f]+\Z")
SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|API_KEY|PRIVATE_KEY|CREDENTIALS?)\Z")
# Two shapes the notifier's scrub does not know, and a run of letters and digits with no separator
# in it: a folder name split by dashes or dots is not one, a random key is.
LOCAL_SHAPES = (
    (re.compile(r"xapp-[A-Za-z0-9-]{10,}"), "a Slack app-level token"),
    (re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{32}(?![0-9A-Fa-f])"), "a signing secret (32 hex digits)"),
)
RANDOM_RUN_RE = re.compile(r"[A-Za-z0-9+=]{32,}")


def targets(entry):
    """[(part file, key)] a shared value is written to."""
    name, _what, to = entry
    out = []
    for word in to.split():
        stem, _sep, key = word.partition(":")
        out.append((stem + ".conf", key or name))
    return out


def place_key(name):
    """The [places] key that names one part file's own place: stage-e.conf is STAGE_E_CONF."""
    return re.sub(r"[^A-Z0-9]", "_", name[:-len(".conf")].upper()) + "_CONF"


def consent_default():
    """The consent record's default place: the one command's own constant."""
    import pipeline_install
    return pipeline_install.DEFAULT_RECORD


# --------------------------------------------------------------------------- #
# Reading: the kit's facts, the folder, and a part file
# --------------------------------------------------------------------------- #
class Kit(object):
    """Each part's KEY=value settings file, from the parts list, and the keys its example names.
    Raises OSError or ValueError when the list or an example cannot be read."""

    def __init__(self, root=KIT):
        self.root = root
        with open(os.path.join(root, PARTS_LIST), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.examples, self.keys, self.part_file = {}, {}, {}
        for part in doc.get("parts") or []:
            s = part.get("settings") or {}
            name, example = s.get("file") or "", s.get("example") or ""
            self.part_file[part.get("id")] = name
            if name.endswith(".conf") and example and name not in self.examples:
                self.examples[name] = os.path.join(root, example)
        if not self.examples:
            raise ValueError("the parts list names no settings file")
        for name, path in self.examples.items():
            with open(path, encoding="utf-8") as fh:
                self.keys[name] = set(m.group(1) for m in map(EXAMPLE_KEY_RE.match, fh.read().splitlines()) if m)
        self.files = sorted(self.examples)
        self.conf_keys = dict((place_key(name), name) for name in self.files)


class Folder(object):
    """machine.conf, read: each section as a map, and the line each key is on; and each project."""

    def __init__(self):
        self.places, self.shared, self.parts, self.line, self.projects = {}, {}, {}, {}, {}


def read_text(path):
    """A small file's text, never through a link; CRLF endings kept. Raises OSError or ValueError."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(FILE_MAX + 1)
    if len(data) > FILE_MAX:
        raise ValueError("it is larger than 1 MB")
    return data.decode("utf-8")


def conf_values(text):
    """A part file's values, as the parts' own parsers read them."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _eq, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def key_problem(kit, section, key):
    """Why `key` may not sit in `section`, or None."""
    if section == "places":
        if key in PLACE_KEYS or key in kit.conf_keys:
            return None
        return "is not a place: %s, or a part file's own, such as BRIDGE_CONF" % ", ".join(PLACE_KEYS)
    if section == "shared":
        if key in SHARED_NAMES:
            return None
        return "is not a shared value (`map` lists them). A part's own value goes in its own section"
    if (section, key) in TOOL_KEYS:
        return "is not kept here: %s, and the folder would undo it" % TOOL_KEYS[(section, key)]
    if key not in kit.keys[section]:
        return "is not a key %s names" % os.path.basename(kit.examples[section])
    return None


def parse_machine(text, kit):
    """(Folder, problems). Each problem names the line and the key, never a value."""
    folder, problems, section, seen = Folder(), [], None, set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1)
            if section not in ("places", "shared") and section not in kit.examples:
                problems.append("line %d: [%s] is not a section: [places], [shared], or a part's "
                                "settings file such as [stage-e.conf]" % (n, section))
                section = "?"
            elif section in seen:
                problems.append("line %d: [%s] appears twice. Keep one" % (n, section))
                section = "?"
            else:
                seen.add(section)
            continue
        key, eq, value = line.partition("=")
        key = key.strip()
        if not eq or not KEY_RE.match(key):
            problems.append("line %d is not KEY=value or a [section]" % n)
            continue
        if section is None:
            problems.append("line %d: %s comes before any [section]" % (n, key))
            continue
        if section == "?":
            continue
        where = folder.places if section == "places" else folder.shared if section == "shared" \
            else folder.parts.setdefault(section, {})
        if key in where:
            problems.append("line %d: %s is set twice in [%s]" % (n, key, section))
            continue
        why = key_problem(kit, section, key)
        if why:
            problems.append("line %d: %s %s" % (n, key, why))
            continue
        where[key] = value.strip().strip('"').strip("'")
        folder.line[(section, key)] = n
    return folder, problems + place_problems(folder, kit)


def place_problems(folder, kit):
    """A place must be a full path (STATE_DIR and SECRETS_DIR may start ~/, the pipeline account's
    home), and a part file's own place must end in that file's name. Empty is the default."""
    out = []
    for key, value in folder.places.items():
        if not value:
            continue
        if key in ("STATE_DIR", "SECRETS_DIR"):
            ok, rule = value.startswith(("/", "~/")), "a full path, or ~/ for the pipeline account's home"
        elif key in kit.conf_keys:
            ok = value.startswith("/") and os.path.basename(value) == kit.conf_keys[key]
            rule = "the full path of a file named %s" % kit.conf_keys[key]
        else:
            ok, rule = value.startswith("/"), "a full path"
        if not ok or not PATH_RE.match(value) or ".." in value.split("/"):
            out.append("line %d: %s must be %s" % (folder.line[("places", key)], key, rule))
    return out


def parse_project(name, text):
    """(project, problems): its REPO, TEAM_KEY and PARTS. Each problem names the file, the line and
    the key, never a value."""
    project, problems, seen, where = {"REPO": "", "TEAM_KEY": "", "PARTS": []}, [], set(), \
        "projects/%s.conf" % name
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, eq, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not eq or key not in PROJECT_KEYS:
            problems.append("%s line %d: a project's file holds only REPO, TEAM_KEY and PARTS" % (where, n))
        elif key in seen:
            problems.append("%s line %d: %s is set twice" % (where, n, key))
        elif key == "PARTS":
            parts = [p for p in re.split(r"[\s,]+", value) if p]
            if [p for p in parts if p not in COMPOSED_PARTS]:
                problems.append("%s line %d: PARTS names a part that takes no list from a project. It may "
                                "name: %s" % (where, n, ", ".join(COMPOSED_PARTS)))
            project["PARTS"] = sorted(set(p for p in parts if p in COMPOSED_PARTS))
        elif value and not (REPO_RE if key == "REPO" else TEAM_RE).match(value):
            problems.append("%s line %d: %s must be %s" % (where, n, key, "OWNER/NAME" if key == "REPO" else
                                                            "a team key: 1 to 10 capital letters or digits"))
        else:
            project[key] = value
        seen.add(key)
    if not project["REPO"] and not project["TEAM_KEY"]:
        problems.append("%s names neither a REPO nor a TEAM_KEY" % where)
    return project, problems


def entry_problem(entry, path):
    """Why an entry in projects/ is not a project's file, or None."""
    stem = entry[:-len(".conf")] if entry.endswith(".conf") else ""
    if not PROJECT_NAME_RE.match(stem) or os.path.islink(path) or not os.path.isfile(path):
        return ("projects/%s is not a project's file: a plain <name>.conf, the name in lower-case letters, "
                "digits and dashes" % entry)
    return None


def duplicate_problems(projects):
    """Two projects that name the same REPO or the same TEAM_KEY."""
    out = []
    for key in ("REPO", "TEAM_KEY"):
        owners = {}
        for name in sorted(projects):
            if projects[name][key]:
                owners.setdefault(projects[name][key], []).append(name)
        out += ["the projects %s name the same %s" % (" and ".join(names), key)
                for _value, names in sorted(owners.items()) if len(names) > 1]
    return out


def queue_problems(folder):
    """The queue lists teams, and maps each one to a repository unless REPO_BY_LABEL is set (its installer's
    own rule). So a project the queue covers needs a TEAM_KEY, and a REPO too unless [queue.conf] sets
    REPO_BY_LABEL: otherwise the queue's settings would be written in a shape its installer refuses, or the
    project would silently not reach it."""
    out, by_label = [], (folder.parts.get("queue.conf") or {}).get("REPO_BY_LABEL")
    for name in sorted(folder.projects):
        p = folder.projects[name]
        if "work-queue" not in p["PARTS"]:
            continue
        if not p["TEAM_KEY"]:
            out.append("projects/%s.conf names work-queue but has no TEAM_KEY, and the queue lists teams" % name)
        elif not p["REPO"] and not by_label:
            out.append("projects/%s.conf names work-queue with a TEAM_KEY and no REPO, and [queue.conf] sets no "
                       "REPO_BY_LABEL: the queue maps every team to a repository" % name)
    return out


def read_projects(pdir):
    """({name: project}, problems) from projects/. Raises OSError or ValueError when a file cannot be read."""
    projects, problems = {}, []
    if not os.path.isdir(pdir) or os.path.islink(pdir):
        return projects, ["projects is not a folder"]
    for entry in sorted(os.listdir(pdir)):
        path = os.path.join(pdir, entry)
        why = entry_problem(entry, path)
        if why:
            problems.append(why)
            continue
        text = read_text(path)
        problems += ["projects/%s %s" % (entry, p) for p in secret_problems(text)]
        project, found = parse_project(entry[:-len(".conf")], text)
        projects[entry[:-len(".conf")]] = project
        problems += found
    return projects, problems + duplicate_problems(projects)


def composed(projects, entry):
    """(a part's list, the projects in it): every project the part covers that has what the list takes."""
    part, _file, _key, kind = entry
    items, names = [], []
    for name in sorted(projects):
        p = projects[name]
        if part not in p["PARTS"]:
            continue
        item = p["REPO"] if kind == "repo" else p["TEAM_KEY"] if kind == "team" else (
            "%s=%s" % (p["TEAM_KEY"], p["REPO"]) if p["TEAM_KEY"] and p["REPO"] else "")
        if item and item not in items:
            items.append(item)
            names.append(name)
    return items, names


def as_list(value):
    return sorted(x.strip() for x in (value or "").split(",") if x.strip())


def same_value(name, key, have, want):
    """A list key is the same when it holds the same items, in any order; any other key, the same text."""
    if (name, key) in LIST_KEYS:
        return as_list(have) == as_list(want)
    return have == want


# --------------------------------------------------------------------------- #
# The checker: secrets, privacy, links and repositories
# --------------------------------------------------------------------------- #
class ScrubError(Exception):
    """The shared scrub could not run, so nothing it would have judged is called clean."""


def scrub(text):
    """The notifier's scrub, the one the tech lead's installer runs on its own settings. Raises
    ScrubError when it cannot run."""
    try:
        import pipeline_notify_local as notify
        return list(notify.secret_hits(text))
    except Exception as exc:
        raise ScrubError(type(exc).__name__)


def secret_labels(text):
    """What `text` is shaped like, as labels; [] when clean. Raises ScrubError when the shared scrub
    cannot run, so a folder that could not be checked is never called clean."""
    labels = scrub(text)
    labels += [label for pattern, label in LOCAL_SHAPES if pattern.search(text) and label not in labels]
    for run in RANDOM_RUN_RE.findall(text):
        if re.search(r"[a-z]", run) and re.search(r"[A-Z]", run) and re.search(r"[0-9]", run):
            labels.append("a long random-looking string")
            break
    return labels


def secret_problems(text):
    """Every line of machine.conf shaped like a secret, by line and key. Never the value."""
    out, section = [], None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        if line.startswith("#"):
            labels, what = secret_labels(line), "a comment"
        else:
            key, _eq, value = line.partition("=")
            key = key.strip()
            what = "[%s] %s" % (section, key) if section else key
            labels = secret_labels(value)
            if value.strip() and SECRET_NAME_RE.search(key):
                labels.append("a key named like a secret: this folder holds the NAME a secret is "
                              "stored under (a key ending in _ENV), never the secret")
        if labels:
            out.append("line %d (%s) holds something shaped like %s" % (n, what, "; ".join(labels)))
    return out


def git_tree(path, stop=None):
    """The nearest folder at or above `path` that holds a .git, or None. `stop` ends the walk."""
    p = os.path.realpath(path)
    while p != stop:
        if os.path.lexists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    return None


def linked(st):
    return stat.S_ISLNK(st.st_mode)


def foreign(st, uid):
    return st.st_uid != uid


def tree_problems(top, uid):
    """The folder and everything in it: yours, no other account may read it, and no links."""
    out, count = [], [0]

    def judge(path, label, folder):
        st = os.lstat(path)
        if linked(st):
            out.append("%s is a link. Nothing in the settings folder may be one" % label)
            return False
        if folder and not stat.S_ISDIR(st.st_mode):
            out.append("%s is not a folder" % label)
            return False
        if foreign(st, uid):
            out.append("%s belongs to another account" % label)
        if st.st_mode & OTHER_ACCESS:
            out.append("%s can be read by other accounts (mode %03o). Make it %s"
                       % (label, st.st_mode & 0o777, "700" if stat.S_ISDIR(st.st_mode) else "600"))
        return stat.S_ISDIR(st.st_mode)

    def unread(exc):                 # a folder inside it that could not be listed is said, never passed
        out.append("%s could not be read (%s), so it was not checked"
                   % (os.path.relpath(exc.filename or top, top), type(exc).__name__))

    if judge(top, "the settings folder", True):
        for root, dirs, files in os.walk(top, onerror=unread):
            for name in sorted(dirs) + sorted(files):
                count[0] += 1
                if count[0] > WALK_MAX:
                    out.append("the settings folder holds more than %d entries; not all were checked" % WALK_MAX)
                    return out
                path = os.path.join(root, name)
                judge(path, os.path.relpath(path, top), False)
    return out


def overrides(folder):
    """Notes: a part's own value that stops a shared one, or the projects, reaching that file."""
    out = []
    for entry in MAP:
        if entry[0] in folder.shared:
            for name, key in targets(entry):
                if own_wins(folder.parts.get(name, {}), key):
                    out.append("%s sets %s itself, so the shared %s does not reach it there"
                               % (name, key, entry[0]))
    for entry in COMPOSED:
        if own_wins(folder.parts.get(entry[1], {}), entry[2]) and composed(folder.projects, entry)[0]:
            out.append("%s sets %s itself, so the projects do not reach it there" % (entry[1], entry[2]))
    return out


def load(settings_dir, kit, uid, stop=None):
    """(state, folder, problems, notes). state is "absent" (no folder or no machine.conf), or
    "read". Raises OSError or ValueError when machine.conf cannot be read."""
    if not os.path.lexists(settings_dir):
        return "absent", None, [], []
    problems = tree_problems(settings_dir, uid)
    repo = git_tree(settings_dir, stop)
    if repo:
        problems.append("the settings folder is inside a git working tree (%s). Settings never go in "
                        "a repository" % repo)
    machine = os.path.join(settings_dir, MACHINE)
    if not os.path.lexists(machine):
        return "absent", None, problems, []
    if os.path.islink(machine):
        return "read", Folder(), problems, []
    text = read_text(machine)
    problems += secret_problems(text)
    folder, parse = parse_machine(text, kit)
    problems += parse
    pdir = os.path.join(settings_dir, PROJECTS)
    if os.path.lexists(pdir):
        folder.projects, found = read_projects(pdir)
        problems += found + queue_problems(folder)
    return "read", folder, problems, overrides(folder)


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #
def set_line(text, key, value):
    """KEY=value set in a part file's text with every other byte kept: the tech lead installer's
    one writer of a line, used here unchanged."""
    import pipeline_bridge_setup
    return pipeline_bridge_setup.set_conf_line(text, key, value)


def part_path(folder, kit, name):
    """(where the part file is, which setting said so)."""
    own = folder.places.get(place_key(name))
    if own:
        return own, "[places] " + place_key(name)
    base = folder.places.get("PARTS_DIR")
    return (os.path.join(base, name), "[places] PARTS_DIR") if base else (
        os.path.join(kit.root, name), "this kit folder")


def own_wins(own, key):
    """A key a part's own section sets is that part's, whatever [shared] or the projects say."""
    return key in own


def feeds(items):
    """A list key is written only when some project gives it an item: one no project covers stays as it is."""
    return bool(items)


def part_values(folder, name):
    """[(key, value, from)] the folder gives one part file: [shared], the projects' lists, its own section."""
    own, out = folder.parts.get(name, {}), []
    for entry in MAP:
        if entry[0] in folder.shared:
            for file_name, key in targets(entry):
                if file_name == name and not own_wins(own, key):
                    out.append((key, folder.shared[entry[0]], "[shared] " + entry[0]))
    for entry in COMPOSED:
        if entry[1] == name and not own_wins(own, entry[2]):
            items, names = composed(folder.projects, entry)
            if feeds(items):
                out.append((entry[2], ",".join(items), "projects: " + ", ".join(names)))
    out += [(key, value, "[%s]" % name) for key, value in own.items()]
    return out


class Plan(object):
    """One part file: its state (off, empty, same, change, held, unread), the changes, the hand edits found
    in it, and why."""

    def __init__(self, name, path, source):
        self.name, self.path, self.source = name, path, source
        self.state, self.why, self.changes, self.old, self.new = "same", "", [], None, None
        self.edits = []                          # (key, the file's value, the folder's, from)


def hand_edited(name, key, have, recorded):
    """The file no longer holds what the folder last wrote there, so a person changed it since. With no
    record of the key (a first write), a difference is the folder's own change."""
    return recorded is not MISSING and not same_value(name, key, have, recorded)


def unsafe_part(st, uid):
    """Why a part file is never written, or "": a link, not a plain file, or another account's."""
    if stat.S_ISLNK(st.st_mode):
        return "is a link, and nothing is written through a link"
    if not stat.S_ISREG(st.st_mode):
        return "is not a plain file"
    return "belongs to another account" if foreign(st, uid) else ""


def plan_file(folder, kit, name, uid, record=None, force=()):
    """What the folder changes in one part file. `record` is what it last wrote there; a key the file
    changed since is a hand edit, held for the person's choice, unless `force` names it (the folder won)."""
    path, source = part_path(folder, kit, name)
    plan, wanted = Plan(name, path, source), part_values(folder, name)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        plan.state = "off"
        return plan
    except OSError as exc:
        plan.state, plan.why = "unread", "could not be looked at (%s)" % type(exc).__name__
        return plan
    plan.why = unsafe_part(st, uid)
    if plan.why:
        plan.state = "held"
        return plan
    if not wanted:
        plan.state = "empty"
        return plan
    try:
        plan.old = read_text(path)
    except (OSError, ValueError) as exc:
        plan.state, plan.why = "unread", "could not be read (%s)" % type(exc).__name__
        return plan
    have, plan.new = conf_values(plan.old), plan.old
    for key, value, src in wanted:
        if same_value(name, key, have.get(key), value):
            continue
        if key not in force and hand_edited(name, key, have.get(key), (record or {}).get(key, MISSING)):
            plan.edits.append((key, have.get(key), value, src))
            continue
        plan.new = set_line(plan.new, key, value)
        plan.changes.append((key, have.get(key), value, src))
    together = TOGETHER.get(name, ())
    after = conf_values(plan.new)
    if (set(k for k, _o, _n, _s in plan.changes) & set(together)
            and 0 < len([k for k in together if after.get(k)]) < len(together)):
        plan.state = "held"
        plan.why = ("takes %s all together or none, and the folder would leave some of them empty. "
                    "Set all three under [shared] or [%s]" % (", ".join(together), name))
        return plan
    plan.state = "change" if plan.changes else "same"
    return plan


def still_as_planned(plan):
    """"" when the part file is still the plain file it was when planned; else what changed."""
    try:
        st = os.lstat(plan.path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return "is no longer a plain file"
        if read_text(plan.path) != plan.old:
            return "changed while this ran"
    except (OSError, ValueError) as exc:
        return "could not be read again (%s)" % type(exc).__name__
    return ""


def preflight(todo):
    """[(plan, what changed)] for every part file about to be written. All are read again before the
    first one is written, so one changed file stops the whole write."""
    return [(p, why) for p, why in ((p, still_as_planned(p)) for p in todo) if why]


def blocked(plans):
    """(exit code, words) when one part file stops the whole write, else None: all or nothing."""
    if any(p.state == "unread" for p in plans):
        return EX_UNREAD, "Nothing was written: a part file could not be read."
    if any(p.state == "held" for p in plans):
        return EX_FIX, "Nothing was written: fix the part file named above first."
    return None


def reads_back(plan, back):
    """True when the text read back is the text written, and every changed key holds its new value."""
    values = conf_values(back)
    return back == plan.new and all(values.get(k) == new for k, _o, new, _s in plan.changes)


def apply_plan(plan):
    """None when the file is written and read back; else why not, in words."""
    why = still_as_planned(plan)
    if why:
        return "%s, so nothing was written to it. Run write again" % why
    try:
        st = os.lstat(plan.path)
    except OSError as exc:
        return "could not be read again (%s), so nothing was written to it" % type(exc).__name__
    mode, tmp, swapped = stat.S_IMODE(st.st_mode), "%s.settings.%d" % (plan.path, os.getpid()), False
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(plan.new.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, plan.path)
        swapped = True
    except OSError as exc:
        return "could not be written (%s); it is as it was" % type(exc).__name__
    finally:
        if not swapped:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    try:
        back = read_text(plan.path)
    except (OSError, ValueError) as exc:
        return "was written but could not be read back (%s). Check it by hand" % type(exc).__name__
    if not reads_back(plan, back):
        return "was written but reads back differently. Check it by hand"
    return None


def shown(value):
    if value is None:
        return "(none)"
    if not value:
        return "(empty)"
    try:
        hidden = bool(secret_labels(value))
    except Exception:            # a value that could not be judged is not shown either
        hidden = True
    return "(shaped like a secret, not shown)" if hidden else value


def put_file(path, text, mode, new=False):
    """One whole small file of the folder's: made new beside it, then put in place, never through a link.
    With new=True it never replaces a file that is there, even one made a moment ago: the file appears
    whole, or the call raises."""
    if os.path.islink(path):
        raise OSError("%s is a link" % path)
    tmp = "%s.new.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if new:
            os.link(tmp, path)             # refuses a path that is there
        else:
            os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def undo(made):
    """Remove what this run made, newest first: never anything else. Returns what could not be removed."""
    left = []
    for path in reversed(made):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                os.rmdir(path)
            else:
                os.unlink(path)
        except OSError:
            left.append(path)
    return left


# --------------------------------------------------------------------------- #
# Hand edits: the record of what was last written, and the person's choice
# --------------------------------------------------------------------------- #
def read_record(sdir):
    """{part file: {key: value}} the folder last gave each part file; {} when there is no record yet.
    Raises ValueError for a file that is not one: a hand edit must never slip by as "no record"."""
    path = os.path.join(sdir, RECORD)
    if not os.path.lexists(path):
        return {}
    doc = json.loads(read_text(path))
    files = doc.get("files") if isinstance(doc, dict) and doc.get("schema") == RECORD_SCHEMA else None
    if not isinstance(files, dict) or not all(
            isinstance(values, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in values.items())
            for values in files.values()):
        raise ValueError("it is not a record of what was written")
    return files


def record_text(files):
    return json.dumps({"schema": RECORD_SCHEMA, "files": files}, indent=1, sort_keys=True) + "\n"


def record_of(folder, plans, written):
    """{part file: {key: value}}: what the folder now gives each file it reached, and was written or found
    level. A file it gives nothing is dropped."""
    out = {}
    for p in plans:
        if p.state == "same" or (p.state == "change" and p.name in written):
            out[p.name] = dict((k, v) for k, v, _src in part_values(folder, p.name))
    return dict((name, values) for name, values in out.items() if values)


def save_record(sdir, old, folder, plans, written):
    """Keep the record, so the next write can tell a hand edit. None, or why it could not be kept."""
    reached = set(p.name for p in plans if p.state in ("same", "empty") or (p.state == "change" and p.name in written))
    new = dict((name, values) for name, values in old.items() if name not in reached)   # a failed file keeps its old one
    new.update(record_of(folder, plans, written))
    if new == old and os.path.lexists(os.path.join(sdir, RECORD)):
        return None
    try:
        put_file(os.path.join(sdir, RECORD), record_text(new), FILE_MODE)
    except OSError as exc:
        return type(exc).__name__
    return None


def own_section(name):
    """The section a part file's own value goes in: its own, so the shared value still reaches the others."""
    return name


def set_section_line(text, section, key, value):
    """machine.conf's text with KEY=value in [section]: its line there replaced, or added at the end of the
    section, or the section added at the end of the file. Every other byte stays."""
    lines, start, end = text.splitlines(True), None, None
    for i, raw in enumerate(lines):
        m = SECTION_RE.match(raw.strip())
        if m and start is not None and end is None:
            end = i
        if m and m.group(1) == section and start is None:
            start = i
    if start is None:
        return text + ("" if not text or text.endswith("\n") else "\n") + "\n[%s]\n%s=%s\n" % (section, key, value)
    end = len(lines) if end is None else end
    for i in range(start + 1, end):
        s = lines[i].strip()
        if s and not s.startswith("#") and s.partition("=")[0].strip() == key:
            lines[i] = "%s=%s\n" % (key, value)
            return "".join(lines)
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    if not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines.insert(at, "%s=%s\n" % (key, value))
    return "".join(lines)


def takeable(name, key, have):
    """Why a file's own value cannot go in the folder, or None."""
    if have is None:
        return "%s no longer sets %s, so only the folder's value can win" % (name, key)
    if (have and secret_labels(have)) or (have and SECRET_NAME_RE.search(key)):
        return "the value in %s %s is shaped like a secret, so it can never go in the folder" % (name, key)
    return None


def ask_choices(edits, ask, out):
    """{(part file, key): "file" or "folder"} from the person, one answer per hand edit; None when any answer
    is not one of the two (and then nothing is written)."""
    choices = {}
    for p, (key, have, want, _src) in edits:
        try:
            answer = (ask("Which wins for %s %s: the file's (%s) or the folder's (%s)? Type file or folder: "
                          % (p.name, key, shown(have), shown(want))) or "").strip()
        except EOFError:
            answer = ""
        why = takeable(p.name, key, have) if answer == "file" else None
        if answer not in ("file", "folder") or why:
            out("Nothing was written: %s." % (why or "each hand edit needs `file` or `folder`"))
            return None
        choices[(p.name, key)] = answer
    return choices


def take_from_files(sdir, kit, takes):
    """Put each file's own value in its part's own section of machine.conf, so the folder takes it. None, or
    why not (then machine.conf is as it was)."""
    path = os.path.join(sdir, MACHINE)
    try:
        text = read_text(path)
    except (OSError, ValueError) as exc:
        return "machine.conf could not be read (%s)" % type(exc).__name__
    for name, key, value in takes:
        text = set_section_line(text, own_section(name), key, value)
    problems = parse_machine(text, kit)[1] + secret_problems(text)
    if problems:
        return "machine.conf would not pass check with them: %s" % "; ".join(problems)
    try:
        put_file(path, text, FILE_MODE)
    except OSError as exc:
        return "machine.conf could not be written (%s)" % type(exc).__name__
    return None


# --------------------------------------------------------------------------- #
# A first run: gather what the part files already say
# --------------------------------------------------------------------------- #
class Gathered(object):
    """What `gather` found: the folder it would write (machine.conf's text and each project's), what it
    says about it, what it left in the part files, and what stops it."""

    def __init__(self):
        self.machine, self.projects, self.lines, self.left, self.stops = "", {}, [], [], []
        self.found, self.unread = [], False


def copy_problem(kit, name, key, value):
    """Why a part file's line is never copied into the folder, or None."""
    labels = secret_labels(value) if value else []
    if value and SECRET_NAME_RE.search(key):
        labels.append("a key named like a secret")
    if labels:
        return "it is shaped like %s, so it was not copied" % "; ".join(labels)
    if (name, key) in TOOL_KEYS:
        return "another tool writes it: %s" % TOOL_KEYS[(name, key)]
    if key not in kit.keys[name]:
        return "%s does not name it" % os.path.basename(kit.examples[name])
    return None


def agree(present, have):
    """A value is shared only when every part file that takes it sets it, and all to the same."""
    return len(have) == len(present) and len(set(v for _f, _k, v in have)) == 1


def item_ok(kind, item):
    if kind == "team=repo":
        team, eq, repo = item.partition("=")
        return bool(eq) and bool(TEAM_RE.match(team.strip())) and bool(REPO_RE.match(repo.strip()))
    return bool((REPO_RE if kind == "repo" else TEAM_RE).match(item))


def lists_match(entry, projects, value):
    """The projects give a part's list exactly the items it holds now."""
    return as_list(",".join(composed(projects, entry)[0])) == as_list(value)


def slug(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", text.lower())).strip("-")[:36] or "project"


def unique(name, taken):
    out, n = name, 1
    while out in taken:
        n += 1
        out = "%s-%d" % (name, n)
    return out


def projects_from(values, g):
    """(projects, the list keys they came from). One project per repository and one per team, joined
    only where a file pairs them (REPO_BY_TEAM)."""
    repos, teams, pairs, lists = {}, {}, {}, []
    for entry in COMPOSED:
        part, f, k, kind = entry
        if k not in values.get(f, {}):
            continue
        items = [x.strip() for x in values[f][k].split(",") if x.strip()]
        if not all(item_ok(kind, x) for x in items):
            g.lines.append("%s %s is not a list of %s, so it stays in [%s] as it is." % (f, k, KIND_WORDS[kind], f))
            continue
        lists.append(entry)
        for x in items:
            if kind == "repo":
                repos.setdefault(x, set()).add(part)
            elif kind == "team":
                teams.setdefault(x, set()).add(part)
            else:
                team, repo = (p.strip() for p in x.split("=", 1))
                repos.setdefault(repo, set()).add(part)
                teams.setdefault(team, set()).add(part)
                if pairs.get(team, repo) != repo or repo in [r for t, r in pairs.items() if t != team]:
                    g.lines.append("%s pairs a team or a repository twice, so %s is not joined to %s."
                                   % (f, team, repo))
                else:
                    pairs[team] = repo
    projects, team_of = {}, dict((r, t) for t, r in pairs.items())
    for repo in sorted(repos):
        base = slug(repo.split("/", 1)[1])
        name = unique(slug(repo) if base in projects else base, projects)
        team = team_of.get(repo, "")
        projects[name] = {"REPO": repo, "TEAM_KEY": team, "PARTS": sorted(repos[repo] | teams.get(team, set()))}
    for team in sorted(t for t in teams if t not in pairs):
        name = unique("team-" + slug(team) if slug(team) in projects else slug(team), projects)
        projects[name] = {"REPO": "", "TEAM_KEY": team, "PARTS": sorted(teams[team])}
    return projects, lists


def round_trip(kit, g, parts_dir, uid):
    """What the gathered folder would change in the part files: [] when nothing, or each change named.
    It is read back the way `check` and `write` read the folder."""
    folder, out = parse_machine(g.machine, kit)
    out = list(out) + secret_problems(g.machine)
    for name, text in sorted(g.projects.items()):
        folder.projects[name], found = parse_project(name, text)
        out += found + ["projects/%s.conf %s" % (name, p) for p in secret_problems(text)]
    out += duplicate_problems(folder.projects) + queue_problems(folder)
    folder.places.setdefault("PARTS_DIR", parts_dir)
    for name in kit.files:
        plan = plan_file(folder, kit, name, uid)
        if plan.state == "change":
            out.append("it would change %s %s" % (name, ", ".join(k for k, _o, _n, _s in plan.changes)))
        elif plan.state in ("held", "unread"):
            out.append("%s %s" % (name, plan.why))
    return out


def gather(kit, parts_dir, uid):
    """Read the part files in `parts_dir`, and work out the folder they make. Writes nothing."""
    g, values = Gathered(), {}
    for name in kit.files:
        path = os.path.join(parts_dir, name)
        try:
            why = unsafe_part(os.lstat(path), uid)
            text = None if why else read_text(path)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            why, g.unread = "could not be read (%s)" % type(exc).__name__, True
        if why:
            g.stops.append("%s %s" % (path, why))
            continue
        g.found.append(name)
        values[name] = {}
        for key, value in conf_values(text).items():
            why = copy_problem(kit, name, key, value)
            if why:
                g.left.append("%s %s: %s" % (name, key, why))
            else:
                values[name][key] = value
    shared, used = {}, set()
    for entry in MAP:
        present = [(f, k) for f, k in targets(entry) if f in values]
        have = [(f, k, values[f][k]) for f, k in present if k in values[f]]
        if not have:
            continue
        if agree(present, have):
            shared[entry[0]] = have[0][2]
            used |= set((f, k) for f, k, _v in have)
        else:
            said = ["%s %s says %s" % (f, k, shown(v)) for f, k, v in have]
            unset = ["%s %s" % (f, k) for f, k in present if k not in values[f]]
            said += ["%s does not set it" % u for u in unset]
            hint = "" if len(set(v for _f, _k, v in have)) > 1 else (
                " They agree where they set it: to share it, put it once under [shared] and delete their "
                "lines, and `write` then sets %s too." % " and ".join(unset))
            g.lines.append("%s is not shared, so each file keeps its own: %s.%s" % (entry[0], "; ".join(said), hint))
    projects, lists = projects_from(values, g)
    for entry in lists:
        if lists_match(entry, projects, values[entry[1]][entry[2]]):
            used.add((entry[1], entry[2]))
        else:
            g.lines.append("%s %s stays in [%s] as it is: the projects would give it %s." % (
                entry[1], entry[2], entry[1], ",".join(composed(projects, entry)[0]) or "nothing"))
    alone = sorted(n for n, p in projects.items() if not (p["REPO"] and p["TEAM_KEY"]))
    if [n for n in alone if projects[n]["REPO"]] and [n for n in alone if projects[n]["TEAM_KEY"]]:
        g.lines.append("No file pairs these teams and repositories (queue.conf's REPO_BY_TEAM is the one "
                       "that does), so each is its own project: %s. To join a team to its repository, put "
                       "its TEAM_KEY in the repository's file and delete the team's file." % ", ".join(alone))
    lines = ["# This Mac's pipeline settings, gathered from its part files by `gather`.",
             "# Names, paths, numbers and switches only: never a key, token or password.", "", "[places]"]
    if os.path.realpath(parts_dir) != os.path.realpath(kit.root):
        lines.append("PARTS_DIR=%s" % parts_dir)
    lines += ["", "[shared]"] + ["%s=%s" % (n, shared[n]) for n in SHARED_NAMES if n in shared]
    for f in kit.files:
        rest = [(k, v) for k, v in values.get(f, {}).items() if (f, k) not in used]
        if rest:
            lines += ["", "[%s]" % f] + ["%s=%s" % kv for kv in rest]
    g.machine = "\n".join(lines) + "\n"
    for name in sorted(projects):
        p = projects[name]
        g.projects[name] = "\n".join(
            ["# The project %s, gathered from the part files by `gather`." % name]
            + (["REPO=%s" % p["REPO"]] if p["REPO"] else []) + (["TEAM_KEY=%s" % p["TEAM_KEY"]] if p["TEAM_KEY"] else [])
            + ["PARTS=%s" % " ".join(p["PARTS"])]) + "\n"
    if g.found and not g.stops:
        g.stops += ["the folder it gathered fails its own proof: %s" % p for p in round_trip(kit, g, parts_dir, uid)]
    return g


def folder_state(sdir, kit):
    """("empty", "") when gather may fill the folder: it is not there, holds nothing, or holds only the
    machine.conf `init` makes, untouched. ("filled", why) when it holds anything else, a comment of the
    person's included. ("unread", why) when that cannot be told."""
    try:
        if not os.path.lexists(sdir):
            return "empty", ""
        if os.path.islink(sdir) or not os.path.isdir(sdir):
            return "filled", "it is a link or not a folder"
        names = sorted(os.listdir(sdir))
        if names and names != [MACHINE]:
            return "filled", "it holds %s" % ", ".join(names)
        if names and read_text(os.path.join(sdir, MACHINE)) != template(kit):
            return "filled", "its machine.conf is not the untouched one `init` makes"
    except (OSError, ValueError) as exc:
        return "unread", "it could not be read (%s)" % type(exc).__name__
    return "empty", ""


def write_folder(sdir, g, kit, extra=None):
    """Write the gathered folder whole, or leave the folder as it was. None, or why not in words. `extra`
    names more files to make in it: {name: text}, such as the record.
    A folder that is not there yet is built beside it and renamed into place. Into init's untouched folder,
    the projects go first and machine.conf last. A failure part way removes what this run made."""
    made, fresh, target = [], not os.path.lexists(sdir), sdir
    try:
        if fresh:
            parent = os.path.dirname(sdir)
            if not os.path.isdir(parent):
                os.makedirs(parent)
            target = os.path.join(parent, ".%s.gather.%d" % (os.path.basename(sdir), os.getpid()))
            os.mkdir(target, FOLDER_MODE)
            made.append(target)
            os.chmod(target, FOLDER_MODE)
        pdir = os.path.join(target, PROJECTS)
        if g.projects and not os.path.lexists(pdir):
            os.mkdir(pdir, FOLDER_MODE)
            made.append(pdir)
            os.chmod(pdir, FOLDER_MODE)
        for name in sorted(g.projects):
            put_file(os.path.join(pdir, name + ".conf"), g.projects[name], FILE_MODE, new=True)
            made.append(os.path.join(pdir, name + ".conf"))
        for name, text in sorted((extra or {}).items()):
            put_file(os.path.join(target, name), text, FILE_MODE, new=True)
            made.append(os.path.join(target, name))
        machine = os.path.join(target, MACHINE)
        if os.path.lexists(machine):
            if read_text(machine) != template(kit):
                raise ValueError("machine.conf changed")
            put_file(machine, g.machine, FILE_MODE)          # init's untouched file, replaced last
        else:
            put_file(machine, g.machine, FILE_MODE, new=True)
            made.append(machine)
        if fresh:
            os.rename(target, sdir)
        return None
    except (OSError, ValueError) as exc:
        left = undo(made)
        return "could not be written (%s). %s" % (type(exc).__name__, (
            "These could not be removed again; take them away by hand: %s" % ", ".join(left)) if left else
            "Nothing was left behind: %s is as it was" % sdir)


def said_yes(answer):
    return (answer or "").strip() == "yes"


def unchanged(g, again):
    """The part files still make the folder the person read."""
    return (g.machine, g.projects, g.stops) == (again.machine, again.projects, again.stops)


# --------------------------------------------------------------------------- #
# Moving to a new Mac: one plain export file, and its import
# --------------------------------------------------------------------------- #
def bundle_files(sdir):
    """[(path in the folder, text)]: machine.conf, then each project's file. Never the record, which is this
    Mac's own state, and never anything else in the folder."""
    out = [(MACHINE, read_text(os.path.join(sdir, MACHINE)))]
    pdir = os.path.join(sdir, PROJECTS)
    if os.path.isdir(pdir):
        out += [("%s/%s" % (PROJECTS, e), read_text(os.path.join(pdir, e))) for e in sorted(os.listdir(pdir))
                if e.endswith(".conf")]
    return out


def export_text(files, on):
    lines = ["# The pipeline's settings, exported by scripts/pipeline_settings.py. Names, paths, numbers and",
             "# switches only: no key, token or password. On another Mac: pipeline_settings.py import --from <this file>",
             "# On the Mac it came from, these part files were on: %s." % (", ".join(on) or "none")]
    for path, text in files:
        lines += [EXPORT_MARK + path] + text.splitlines()
    return "\n".join(lines) + "\n"


def bundle_path_ok(path):
    """Only machine.conf and projects/<name>.conf: no other file, no folder above, no trick in the name."""
    stem = path[len(PROJECTS) + 1:-len(".conf")] if path.startswith(PROJECTS + "/") and path.endswith(".conf") else ""
    return path == MACHINE or (bool(PROJECT_NAME_RE.match(stem)) and path == "%s/%s.conf" % (PROJECTS, stem))


def parse_export(text):
    """({path in the folder: text}, the part files that were on where it came from, problems)."""
    files, on, problems, cur = {}, [], [], None
    for raw in text.splitlines():
        if raw.startswith(EXPORT_MARK):
            path, cur = raw[len(EXPORT_MARK):].strip(), None
            if not bundle_path_ok(path):
                problems.append("it names a file that is not one of the settings' (%s)" % (
                    path if re.match(r"^[A-Za-z0-9_./-]{1,80}\Z", path) else "a name of another shape"))
            elif path in files:
                problems.append("it names %s twice" % path)
            else:
                files[path], cur = [], path
        elif cur is not None:
            files[cur].append(raw)
        elif raw.startswith("# On the Mac it came from"):
            on = [n.strip() for n in raw.partition(":")[2].strip().rstrip(".").split(",") if n.strip()]
        elif raw.strip() and not raw.startswith("#"):
            problems.append("a line before the first file is not a comment")
    if MACHINE not in files:
        problems.append("it holds no machine.conf")
    return dict((p, "\n".join(lines) + "\n") for p, lines in files.items()), on, problems


def ended(text):
    """A file's text as an export carries it: ending in one newline."""
    return text if text.endswith("\n") else text + "\n"


def same_export(again, text):
    """The export file still says what the person read."""
    return again == text


def import_problems(files, kit):
    """(the folder the export makes, read as check reads one; everything check would refuse in it)."""
    problems = []
    for path in sorted(files):
        problems += ["%s %s" % (path, p) for p in secret_problems(files[path])]
    folder, found = parse_machine(files.get(MACHINE, ""), kit)
    problems += found
    for path in sorted(p for p in files if p != MACHINE):
        name = path[len(PROJECTS) + 1:-len(".conf")]
        folder.projects[name], found = parse_project(name, files[path])
        problems += found
    return folder, problems + duplicate_problems(folder.projects) + queue_problems(folder)


# --------------------------------------------------------------------------- #
# The commands
# --------------------------------------------------------------------------- #
def refused(what, environ, uid, out):
    found = [m for m in AGENT_ENV_MARKERS if m in environ]
    if found:
        out("REFUSED: `%s` writes the settings the installers apply, and a Claude session must never "
            "do that. Run it in your own terminal. (A session's environment was found: %s.)"
            % (what, ", ".join(found)))
        return True
    if uid == 0:
        out("REFUSED: `%s` writes your own settings. Run it as yourself, not as root." % what)
        return True
    return False


def settings_dir_of(flag, environ):
    """(the folder, where its place came from), or (None, why) for a place that is not a full path."""
    value, source = (flag, "--settings-dir") if flag else (environ.get(DIR_ENV), DIR_ENV) \
        if environ.get(DIR_ENV) else (DEFAULT_DIR, "the default")
    path = os.path.expanduser(value)
    if not os.path.isabs(path):
        return None, "%s must be a full path" % source
    return os.path.normpath(path), source


def say_problems(problems, out):
    for p in problems:
        out("PROBLEM: %s." % p)


def cmd_check(sdir, kit, uid, out, stop=None):
    state, folder, problems, notes = load(sdir, kit, uid, stop)
    if state == "absent" and not problems:
        out("No settings at %s yet, so there was nothing to check. `init` makes the folder." % sdir)
        return EX_OK
    say_problems(problems, out)
    for note in notes:
        out("NOTE: %s." % note)
    if problems:
        out("The settings folder needs fixing: %d problem%s above." % (len(problems), "" if len(problems) == 1 else "s"))
        return EX_FIX
    def many(n, word):
        return "%d %s%s" % (n, word, "" if n == 1 else "s")
    out("OK: %s is private, holds nothing shaped like a secret, and reads cleanly: %s, %s, %s, %s." % (
        sdir, many(len(folder.shared), "shared value"), many(len(folder.parts), "part section"),
        many(len(folder.places), "place"), many(len(folder.projects), "project")))
    return EX_OK


def show_plans(plans, out):
    for p in plans:
        if p.state in ("held", "unread"):
            out("%s (%s): NOT WRITTEN: it %s." % (p.name, p.path, p.why))
        elif p.state == "change":
            out("%s (%s, from %s): %d change%s" % (p.name, p.path, p.source, len(p.changes),
                                                    "" if len(p.changes) == 1 else "s"))
            for key, old, new, src in p.changes:
                out("  %s: %s -> %s   (%s)" % (key, shown(old), shown(new), src))
        for key, have, want, src in p.edits:
            out("HAND EDIT %s %s: the file says %s, the folder says %s (%s). It was changed in the file after "
                "the last write." % (p.name, key, shown(have), shown(want), src))
    for state, words in (("same", "Already what the folder says"),
                         ("empty", "Nothing in the folder for them"),
                         ("off", "Off on this Mac (no settings file, so nothing is written for them)")):
        names = [p.name for p in plans if p.state == state and not p.edits]
        if names:
            out("%s: %s." % (words, ", ".join(names)))


def cmd_write(sdir, kit, uid, apply, out, stop=None, ask=input):
    state, folder, problems, notes = load(sdir, kit, uid, stop)
    if state == "absent" and not problems:
        out("No settings at %s yet, so there is nothing to write from. `init` makes the folder." % sdir)
        return EX_OK
    if problems:
        say_problems(problems, out)
        out("Nothing was written: `check` must pass first.")
        return EX_FIX
    try:
        record = read_record(sdir)
    except (OSError, ValueError) as exc:
        out("Nothing was written: %s, the record of what was last written, could not be read (%s). Without it a "
            "hand edit cannot be told from the folder's own change. Move it aside to start a new one." % (
                RECORD, type(exc).__name__))
        return EX_FIX
    for note in notes:
        out("NOTE: %s." % note)
    plans = [plan_file(folder, kit, name, uid, record.get(name)) for name in kit.files]
    show_plans(plans, out)
    stop_all = blocked(plans)
    if stop_all:
        out(stop_all[1])
        return stop_all[0]
    edits = [(p, e) for p in plans for e in p.edits]
    if edits:
        if not apply:
            out("Nothing was written. %d hand edit%s above: `write --apply` asks, for each one, whether the "
                "file's value or the folder's wins." % (len(edits), "" if len(edits) == 1 else "s"))
            return EX_WAITING
        choices = ask_choices(edits, ask, out)
        if choices is None:
            return EX_WAITING
        takes = [(p.name, e[0], e[1]) for p, e in edits if choices[(p.name, e[0])] == "file"]
        if takes:
            why = take_from_files(sdir, kit, takes)
            if why:
                out("Nothing was written: %s." % why)
                return EX_FIX
            for name, key, _value in takes:
                out("The folder now takes %s %s from the file, in [%s]." % (name, key, own_section(name)))
            state, folder, problems, notes = load(sdir, kit, uid, stop)
            if problems:
                say_problems(problems, out)
                return EX_FIX
        force = dict((name, set(k for (n, k), c in choices.items() if n == name and c == "folder"))
                     for name in kit.files)
        plans = [plan_file(folder, kit, name, uid, record.get(name), force[name]) for name in kit.files]
        if [p for p in plans if p.edits] or blocked(plans):
            out("A part file changed again while you chose. Nothing more was written. Run write again.")
            return EX_FIX
    todo = [p for p in plans if p.state == "change"]
    count = sum(len(p.changes) for p in todo)
    if not todo:
        out("Nothing to write: every part file on this Mac already says what the folder says.")
        if apply:
            why = save_record(sdir, record, folder, plans, set())
            if why:
                out("FAILED: %s could not be kept (%s), so a later hand edit will read as the folder's change."
                    % (RECORD, why))
                return EX_FAILED
        return EX_OK
    if not apply:
        out("Nothing was written. `write --apply` writes these %d change%s to %d file%s."
            % (count, "" if count == 1 else "s", len(todo), "" if len(todo) == 1 else "s"))
        return EX_WAITING
    stale = preflight(todo)
    if stale:
        for p, why in stale:
            out("NOT WRITTEN: %s %s." % (p.path, why))
        out("Nothing was written: a part file changed after it was read. Run write again.")
        return EX_FIX
    failed, written = 0, set()
    for p in todo:
        why = apply_plan(p)
        if why:
            failed += 1
            out("FAILED: %s %s." % (p.path, why))
        else:
            written.add(p.name)
            out("Wrote %s and read it back: it matches." % p.path)
    lost = save_record(sdir, record, folder, plans, written)
    if lost:
        out("FAILED: %s could not be kept (%s), so a later hand edit will read as the folder's change."
            % (RECORD, lost))
    if failed:
        out("%d of %d files were not written, part way through the write. The lines above name each file: "
            "Wrote, or FAILED and as it was, or check it by hand." % (failed, len(todo)))
    return EX_FAILED if failed or lost else EX_OK


def template(kit):
    """The first machine.conf: every place and shared value, commented out, with what it is."""
    lines = ["# This Mac's pipeline settings (scripts/pipeline_settings.py). Names, paths, numbers and",
             "# switches only: never a key, token or password. `check` checks this file; `write` shows",
             "# what reaches each part's own settings file, and `write --apply` writes it.", "",
             "[places]",
             "# The folder the part files are in. Empty: this kit folder.", "#PARTS_DIR=",
             "# A part file kept somewhere else, by its full path, such as BRIDGE_CONF=/full/path/bridge.conf"]
    for key, what, default, _reads in PLACES:
        lines += ["# %s." % what, "#%s=%s" % (key, default if default is not None else consent_default())]
    lines += ["", "[shared]", "# Each value here is typed once, and written to every part file named under it."]
    for entry in MAP:
        lines += ["# %s. Reaches %s." % (entry[1][0].upper() + entry[1][1:], ", ".join(
            "%s %s" % (f, k) for f, k in targets(entry))), "#%s=" % entry[0]]
    lines += ["", "# One section per part file, for its own values, such as [stage-e.conf] or [queue.conf].",
              "# A key there wins over a shared value, and over the projects, for that file.", "",
              "# Each project has its own file in projects/, such as projects/app.conf, with three lines:",
              "# REPO=owner/name, TEAM_KEY=ABC, and PARTS= the parts that cover it (`map` lists them)."]
    return "\n".join(lines) + "\n"


def cmd_init(sdir, kit, uid, out, stop=None):
    machine = os.path.join(sdir, MACHINE)
    if os.path.lexists(machine):
        out("%s is already there. Nothing was changed." % machine)
        return EX_OK
    repo = git_tree(sdir, stop)
    if repo:
        out("REFUSED: %s is inside a git working tree (%s). Settings never go in a repository. "
            "Nothing was made." % (sdir, repo))
        return EX_FIX
    try:
        if not os.path.lexists(sdir):
            os.makedirs(sdir, FOLDER_MODE)
            os.chmod(sdir, FOLDER_MODE)
        put_file(machine, template(kit), FILE_MODE, new=True)      # never over one made meanwhile
    except OSError as exc:
        out("FAILED: %s could not be made (%s)." % (machine, type(exc).__name__))
        return EX_FAILED
    out("Made %s. Type each value once there, then run `check` and `write`." % machine)
    return cmd_check(sdir, kit, uid, out, stop)


def show_gathered(g, sdir, parts_dir, out):
    out("Gathered from %s: %s." % (parts_dir, ", ".join(g.found) or "no part settings file"))
    for line in g.stops:
        out("STOP: %s." % line)
    for line in g.lines:
        out("NOTE: %s" % line)
    if g.left:
        out("Left in the part files, never copied into the folder:")
        for line in g.left:
            out("  %s." % line)
    if g.found and not g.stops:
        for path, text in [(os.path.join(sdir, MACHINE), g.machine)] + [
                (os.path.join(sdir, PROJECTS, name + ".conf"), g.projects[name]) for name in sorted(g.projects)]:
            out("--- %s would hold:" % path)
            for line in text.splitlines():
                out("    " + line)


def cmd_gather(sdir, kit, uid, parts_dir, apply, out, ask, stop=None):
    state, why = folder_state(sdir, kit)
    if state == "unread":
        out("COULD NOT RUN: %s: %s. Nothing was changed." % (sdir, why))
        return EX_UNREAD
    if state == "filled":
        out("%s already holds settings (%s). `gather` only fills a folder that is empty, or holds the file "
            "`init` makes, untouched. Nothing was changed." % (sdir, why))
        return EX_OK
    repo = git_tree(sdir, stop)
    if repo:
        out("REFUSED: %s is inside a git working tree (%s). Settings never go in a repository. Nothing was "
            "written." % (sdir, repo))
        return EX_FIX
    g = gather(kit, parts_dir, uid)
    show_gathered(g, sdir, parts_dir, out)
    if g.stops:
        out("Nothing was written: fix what is named above first.")
        return EX_UNREAD if g.unread else EX_FIX
    if not g.found:
        out("No part settings files in %s, so there is nothing to gather. `init` makes an empty folder."
            % parts_dir)
        return EX_OK
    if not apply:
        out("Nothing was written. `gather --apply` writes it, after your yes.")
        return EX_WAITING
    if not said_yes(ask("Write this to %s? Type yes: " % sdir)):
        out("Nothing was written.")
        return EX_WAITING
    if not unchanged(g, gather(kit, parts_dir, uid)) or folder_state(sdir, kit) != ("empty", ""):
        out("The part files or the folder changed while you read this. Nothing was written. Run gather again.")
        return EX_FIX
    folder = parse_machine(g.machine, kit)[0]
    for name, text in g.projects.items():
        folder.projects[name] = parse_project(name, text)[0]
    folder.places.setdefault("PARTS_DIR", parts_dir)
    record = record_of(folder, [plan_file(folder, kit, name, uid) for name in kit.files], set())
    why = write_folder(sdir, g, kit, extra={RECORD: record_text(record)})
    if why:
        out("FAILED: the folder %s." % why)
        return EX_FAILED
    out("Wrote %s and %d project file%s." % (os.path.join(sdir, MACHINE), len(g.projects),
                                             "" if len(g.projects) == 1 else "s"))
    return cmd_check(sdir, kit, uid, out, stop)


def export_blocked(problems):
    """A folder that fails check is never exported: no secret and nothing broken leaves it."""
    return bool(problems)


def cmd_export(sdir, kit, uid, to, out, stop=None):
    state, folder, problems, _notes = load(sdir, kit, uid, stop)
    if state == "absent" and not problems:
        out("No settings at %s yet, so there is nothing to export." % sdir)
        return EX_OK
    if export_blocked(problems):
        say_problems(problems, out)
        out("Nothing was exported: `check` must pass first, so no secret and nothing broken leaves the folder.")
        return EX_FIX
    repo = git_tree(os.path.dirname(to), stop)
    if repo:
        out("REFUSED: %s is inside a git working tree (%s). Your settings never go in a repository. Nothing was "
            "written." % (to, repo))
        return EX_FIX
    files = bundle_files(sdir)
    on = [name for name in kit.files if os.path.lexists(part_path(folder, kit, name)[0])]
    text = export_text(files, on)
    if secret_problems(text):
        out("Nothing was exported: something in it is shaped like a secret.")
        return EX_FIX
    try:
        put_file(to, text, FILE_MODE, new=True)
        back = parse_export(read_text(to))
    except (OSError, ValueError) as exc:
        out("FAILED: %s could not be written (%s)%s. Nothing was written." % (
            to, type(exc).__name__, ": a file is already there" if isinstance(exc, FileExistsError) else ""))
        return EX_FAILED
    if back[2] or back[0] != dict((p, ended(t)) for p, t in files):
        out("FAILED: %s was written but reads back differently. Check it by hand." % to)
        return EX_FAILED
    out("Wrote %s: machine.conf and %d project file%s, and no secret. Copy it to the other Mac and run "
        "`import --from` there." % (to, len(files) - 1, "" if len(files) == 2 else "s"))
    return EX_OK


def cmd_import(sdir, kit, uid, src, apply, out, ask, stop=None):
    try:
        text = read_text(src)
    except (OSError, ValueError) as exc:
        out("COULD NOT RUN: %s could not be read (%s). Nothing was written." % (src, type(exc).__name__))
        return EX_UNREAD
    state, why = folder_state(sdir, kit)
    if state == "unread":
        out("COULD NOT RUN: %s: %s. Nothing was changed." % (sdir, why))
        return EX_UNREAD
    if state == "filled":
        out("%s already holds settings (%s). `import` only fills a folder that is empty, or holds the file "
            "`init` makes, untouched. Nothing was changed." % (sdir, why))
        return EX_OK
    repo = git_tree(sdir, stop)
    if repo:
        out("REFUSED: %s is inside a git working tree (%s). Settings never go in a repository. Nothing was "
            "written." % (sdir, repo))
        return EX_FIX
    files, on, problems = parse_export(text)
    folder, found = import_problems(files, kit) if not problems else (Folder(), [])
    out("Read %s: %s." % (src, ", ".join(sorted(files)) or "no settings file"))
    for p in problems + found:
        out("STOP: %s." % p)
    if problems or found:
        out("Nothing was written: the export file needs fixing first.")
        return EX_FIX
    on = [n for n in on if n in kit.files]
    missing = [n for n in on if not os.path.lexists(part_path(folder, kit, n)[0])]
    if on:
        out("NOTE: on the Mac it came from, these part files were on: %s." % ", ".join(on))
    if missing:
        out("NOTE: here, %s %s not there yet. Each of those parts stays off until its file is: copy its example "
            "(such as cp %s.example %s), then run `write`." % (", ".join(missing), "is" if len(missing) == 1 else
                                                                "are", missing[0], missing[0]))
    for key, value in sorted(folder.places.items()):
        if value and (key == "PARTS_DIR" or key in kit.conf_keys) and not os.path.exists(value):
            out("NOTE: [places] %s names %s, which is not on this Mac. Change it in machine.conf after the "
                "import." % (key, value))
    for path in sorted(files, key=lambda p: (p != MACHINE, p)):
        out("--- %s would hold:" % os.path.join(sdir, path))
        for line in files[path].splitlines():
            out("    " + line)
    if not apply:
        out("Nothing was written. `import --apply` writes it, after your yes. It never writes a part file.")
        return EX_WAITING
    if not said_yes(ask("Write this to %s? Type yes: " % sdir)):
        out("Nothing was written.")
        return EX_WAITING
    try:
        again = read_text(src)
    except (OSError, ValueError):
        again = None
    if not same_export(again, text) or folder_state(sdir, kit) != ("empty", ""):
        out("The export file or the folder changed while you read this. Nothing was written. Run import again.")
        return EX_FIX
    g = Gathered()
    g.machine = files[MACHINE]
    g.projects = dict((p[len(PROJECTS) + 1:-len(".conf")], t) for p, t in files.items() if p != MACHINE)
    why = write_folder(sdir, g, kit)
    if why:
        out("FAILED: the folder %s." % why)
        return EX_FAILED
    out("Wrote %s and %d project file%s. No part file was touched: `write` shows what reaches each one."
        % (os.path.join(sdir, MACHINE), len(g.projects), "" if len(g.projects) == 1 else "s"))
    return cmd_check(sdir, kit, uid, out, stop)


def places_of(folder, kit):
    """[(what, where, which setting said so, what reads it)]: every place, the folder's or the default."""
    base = folder.places.get("PARTS_DIR")
    rows = [("The part files", base or kit.root, "[places] PARTS_DIR" if base else "the default: this kit folder",
             "Read by `write`.")]
    rows += [("  " + name, folder.places[place_key(name)], "[places] " + place_key(name), "")
             for name in kit.files if folder.places.get(place_key(name))]
    for key, what, default, reads in PLACES:
        value = folder.places.get(key)
        rows.append((what, value or (default if default is not None else consent_default()),
                     "[places] " + key if value else "the default", reads))
    return rows


def cmd_where(sdir, source, kit, uid, out, stop=None):
    state, folder, problems, _notes = load(sdir, kit, uid, stop)
    out("The settings folder: %s (%s; --settings-dir or %s moves it)." % (sdir, source, DIR_ENV))
    if state == "absent":
        out("  machine.conf is not there yet. `init` makes it. The places below are the defaults.")
        folder = Folder()
    elif problems:
        out("  machine.conf has problems (`check` names them). The places below are the defaults.")
        folder = Folder()
    for what, where, said, reads in places_of(folder, kit):
        out(("%s: %s (%s). %s" % (what, where, said, reads)).rstrip())
    return EX_FIX if problems else EX_OK


def cmd_map(kit, out):
    for entry in MAP:
        out("%s: %s." % (entry[0], entry[1]))
        out("  written to: %s." % ", ".join("%s %s" % (f, k) for f, k in targets(entry)))
    out("From the projects' files (PARTS names the part; each project gives its REPO, its TEAM_KEY, or both):")
    for part, f, k, kind in COMPOSED:
        out("  %s %s: the %s of every project %s covers." % (f, k, KIND_WORDS[kind], part))
    out("A key in a part file's own section ([stage-e.conf] and the rest) wins over these for that file.")
    return EX_OK


def main(argv=None, environ=None, out=print, uid=None, kit_root=KIT, stop=None, ask=input):
    ap = argparse.ArgumentParser(description="The pipeline's settings, in one folder on this Mac.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("where", "init", "gather", "check", "write", "export", "import"):
        p = sub.add_parser(name)
        p.add_argument("--settings-dir")
        if name in ("write", "gather", "import"):
            p.add_argument("--apply", action="store_true")
        if name == "gather":
            p.add_argument("--parts-dir", help="where the part files are (default: this kit folder)")
        if name == "export":
            p.add_argument("--to", required=True, help="the export file to make (never one that is there)")
        if name == "import":
            p.add_argument("--from", dest="source", required=True, help="the export file to read")
    sub.add_parser("map")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "settings-selftest-run-")
    if not args.cmd:
        ap.print_help()
        return EX_FIX
    environ = os.environ if environ is None else environ
    uid = os.getuid() if uid is None else uid
    writes = args.cmd in ("init", "export") or (args.cmd in ("write", "gather", "import") and args.apply)
    if writes and refused("%s%s" % (args.cmd, " --apply" if args.cmd not in ("init", "export") else ""),
                          environ, uid, out):
        return EX_REFUSED
    try:
        kit = Kit(kit_root)
    except (OSError, ValueError) as exc:
        out("COULD NOT RUN: the kit's parts list or a settings example could not be read (%s)."
            % type(exc).__name__)
        return EX_UNREAD
    if args.cmd == "map":
        return cmd_map(kit, out)
    sdir, source = settings_dir_of(args.settings_dir, environ)
    if sdir is None:
        out("COULD NOT RUN: %s." % source)
        return EX_FIX
    try:
        if args.cmd == "where":
            return cmd_where(sdir, source, kit, uid, out, stop)
        if args.cmd == "init":
            return cmd_init(sdir, kit, uid, out, stop)
        if args.cmd == "gather":
            parts_dir = os.path.abspath(os.path.expanduser(args.parts_dir)) if args.parts_dir else kit.root
            return cmd_gather(sdir, kit, uid, parts_dir, args.apply, out, ask, stop)
        if args.cmd == "check":
            return cmd_check(sdir, kit, uid, out, stop)
        if args.cmd == "export":
            return cmd_export(sdir, kit, uid, os.path.abspath(os.path.expanduser(args.to)), out, stop)
        if args.cmd == "import":
            return cmd_import(sdir, kit, uid, os.path.abspath(os.path.expanduser(args.source)), args.apply, out,
                              ask, stop)
        return cmd_write(sdir, kit, uid, args.apply, out, stop, ask)
    except (OSError, ValueError) as exc:
        out("COULD NOT RUN: the settings could not be read (%s). Nothing was written." % type(exc).__name__)
        return EX_UNREAD
    except ScrubError as exc:      # the shared scrub could not run: never call the folder clean
        out("COULD NOT RUN: the secret check could not run (%s). Nothing was written." % exc)
        return EX_UNREAD


# ======================================================================================
# SELFTEST: nothing below this line runs outside --selftest, and the source scan stops here.
# ======================================================================================
def battery():
    """Every rule, once, in a folder of its own. Returns (the failed checks, how many checks ran)."""
    import shutil
    import tempfile
    import secret_shapes
    from pipeline_dispatch_local import AGENT_ENV_MARKERS as MARKERS   # the list itself, never a mutant's
    from scratch_run import remove_tree
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    tmp = os.path.realpath(tempfile.mkdtemp(prefix="settings-"))
    stop = os.path.dirname(tmp)       # the git walk ends here, wherever the temp folder sits
    me, other = os.getuid(), os.getuid() + 1
    kit = Kit(KIT)

    def run(*argv, **kw):
        said = []
        code = main(list(argv), environ=kw.get("environ", {}), out=said.append, uid=kw.get("uid", me),
                    stop=stop, ask=kw.get("ask", lambda prompt: ""))
        return code, said

    def put(path, text, mode=0o600):
        if os.path.lexists(path):
            os.remove(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(path, mode)

    def get(path):
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8")

    def snapshot(folder):
        out = {}
        for root, _dirs, files in os.walk(folder):
            for f in files:
                p = os.path.join(root, f)
                st = os.lstat(p)
                out[p] = (get(p) if not os.path.islink(p) else os.readlink(p), st.st_mtime_ns,
                          stat.S_IMODE(st.st_mode))
        return out

    parts = os.path.join(tmp, "parts")
    os.makedirs(parts)
    for name in kit.files:
        shutil.copyfile(kit.examples[name], os.path.join(parts, name))
    sdir = os.path.join(tmp, "settings")
    machine = os.path.join(sdir, MACHINE)
    sd = ("--settings-dir", sdir)

    def settings(body):
        put(machine, "[places]\nPARTS_DIR=%s\n%s" % (parts, body))

    def forget():                       # a case that rewrites part files itself starts the record afresh
        if os.path.lexists(os.path.join(sdir, RECORD)):
            os.remove(os.path.join(sdir, RECORD))

    # 1. The folder's own place is a setting: the default under the user's Library, the variable, the flag.
    home = os.path.expanduser("~")
    check("the default place is under the user's Library",
          settings_dir_of(None, {}), (os.path.join(home, "Library", "Application Support", "claude-pipeline",
                                                   "settings"), "the default"))
    check("the variable moves it", settings_dir_of(None, {DIR_ENV: "/x/s"}), ("/x/s", DIR_ENV))
    check("the flag wins over the variable", settings_dir_of("/y/s", {DIR_ENV: "/x/s"}), ("/y/s", "--settings-dir"))
    check("a place that is not a full path is refused", settings_dir_of("rel/s", {})[0], None)

    # 2. init: refused in a session and as root, never inside a repository, private, never overwrites.
    for marker in MARKERS:
        code, said = run("init", *sd, environ={marker: ""})
        check("init is refused in a session (%s), before anything is made" % marker,
              (code, os.path.lexists(sdir)), (EX_REFUSED, False))
    check("init is refused as root", run("init", *sd, uid=0)[0], EX_REFUSED)
    repo = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(repo, ".git"))
    code, said = run("init", "--settings-dir", os.path.join(repo, "s"))
    check("init refuses a folder inside a git working tree and makes nothing",
          (code, os.path.lexists(os.path.join(repo, "s"))), (EX_FIX, False))
    code, said = run("init", *sd)
    check("init makes the folder 700 and machine.conf 600, and the new file passes check",
          (code, oct(stat.S_IMODE(os.stat(sdir).st_mode)), oct(stat.S_IMODE(os.stat(machine).st_mode))),
          (EX_OK, "0o700", "0o600"))
    first = get(machine)
    check("its template names every place and every shared value",
          [k for k in PLACE_KEYS + SHARED_NAMES if "#%s=" % k not in first], [])
    check("init never overwrites", (run("init", *sd)[0], get(machine)), (EX_OK, first))

    # 3. A value typed once reaches every part file that needs it; write shows and changes nothing.
    settings("[shared]\nROLE_ACCOUNT=_pipeline\nDISPATCHER_SERVICE=com.test.dispatcher\n"
             "DISPATCHER_ENV_FILE=/opt/test/.env\nNOTIFIER_JOB_LABEL=com.test.notifier\n")
    os.chmod(os.path.join(parts, "queue.conf"), 0o640)
    before = snapshot(parts)
    code, said = run("write", *sd)
    want = set()
    for entry in MAP:
        if entry[0] in ("ROLE_ACCOUNT", "DISPATCHER_SERVICE", "DISPATCHER_ENV_FILE", "NOTIFIER_JOB_LABEL"):
            want |= set(targets(entry))
    shown_pairs = set()
    current = None
    for line in said:
        m = re.match(r"^(\S+\.conf) \(", line)
        if m:
            current = m.group(1)
        m = re.match(r"^  ([A-Z0-9_]+): ", line)
        if m and current:
            shown_pairs.add((current, m.group(1)))
    check("write lists every target of every shared value set, file by file", shown_pairs, want)
    check("…shows the value now and after", "  ROLE_ACCOUNT: _exdispatch -> _pipeline   ([shared] ROLE_ACCOUNT)"
          in said, True)
    check("…exits 10 and writes nothing at all", (code, snapshot(parts) == before), (EX_WAITING, True))
    code, said = run("write", "--apply", *sd)
    check("write --apply writes them all and reads each back", (code, len([s for s in said if s.startswith("Wrote ")])),
          (EX_OK, len(set(f for f, _k in want))))
    for name, key in sorted(want):
        values = conf_values(get(os.path.join(parts, name)))
        value = {"ROLE_ACCOUNT": "_pipeline", "DISPATCHER_ACCOUNT": "_pipeline",
                 "DISPATCHER_SERVICE": "com.test.dispatcher", "DISPATCHER_ENV_FILE": "/opt/test/.env"}.get(
            key, "com.test.notifier")
        check("%s %s holds the shared value" % (name, key), values.get(key), value)
    literal = [(f, "ROLE_ACCOUNT") for f in ("stage-e", "stage-a", "acceptance", "bridge", "chat-lane", "notifier",
                                             "queue", "sweep", "update", "watcher")] + [("stage-a", "DISPATCHER_ACCOUNT")]
    check("ROLE_ACCOUNT, typed once, is in all eleven places that need it",
          [p for p in literal if conf_values(get(os.path.join(parts, p[0] + ".conf"))).get(p[1]) != "_pipeline"], [])
    check("…and the notifier's label reaches its three, under their own names",
          [conf_values(get(os.path.join(parts, f))).get(k) for f, k in (
              ("notifier.conf", "JOB_LABEL"), ("bridge.conf", "NOTIFIER_JOB_LABEL"), ("stage-e.conf", "NOTIFIER_JOB_LABEL"))],
          ["com.test.notifier"] * 3)
    old_q, new_q = before[os.path.join(parts, "queue.conf")][0], get(os.path.join(parts, "queue.conf"))
    check("every other byte stays", [a for a, b in zip(old_q.splitlines(), new_q.splitlines()) if a != b],
          ["ROLE_ACCOUNT=_exqueue"])
    check("…and the mode stays", oct(stat.S_IMODE(os.stat(os.path.join(parts, "queue.conf")).st_mode)), "0o640")
    check("a second write has nothing to do", run("write", *sd)[0], EX_OK)

    # 4. A part's own value wins, and check says so.
    put(os.path.join(parts, "stage-a.conf"), get(kit.examples["stage-a.conf"]))
    forget()
    settings("[shared]\nROLE_ACCOUNT=_pipeline\n[stage-a.conf]\nROLE_ACCOUNT=_planner\n")
    code, said = run("check", *sd)
    check("check notes a part's own value that stops a shared one",
          (code, "NOTE: stage-a.conf sets ROLE_ACCOUNT itself, so the shared ROLE_ACCOUNT does not reach "
                 "it there." in said), (EX_OK, True))
    said = run("write", *sd)[1]
    check("the dry run shows stage-a.conf's ROLE_ACCOUNT once, from its own section",
          [s for s in said if s.startswith("  ROLE_ACCOUNT: ")],
          ["  ROLE_ACCOUNT: _exdispatch -> _planner   ([stage-a.conf])"])
    run("write", "--apply", *sd)
    check("the part's own value is written there, the shared one everywhere else",
          (conf_values(get(os.path.join(parts, "stage-a.conf"))).get("ROLE_ACCOUNT"),
           conf_values(get(os.path.join(parts, "stage-a.conf"))).get("DISPATCHER_ACCOUNT"),
           conf_values(get(os.path.join(parts, "stage-e.conf"))).get("ROLE_ACCOUNT")),
          ("_planner", "_pipeline", "_pipeline"))

    # 5. --apply is refused in a session and as root, before anything is read.
    settings("[shared]\nROLE_ACCOUNT=_other\n")
    before = snapshot(parts)
    for marker in MARKERS:
        code, said = run("write", "--apply", *sd, environ={marker: "1"})
        check("write --apply is refused in a session (%s)" % marker, (code, snapshot(parts) == before),
              (EX_REFUSED, True))
    check("…and as root", run("write", "--apply", *sd, uid=0)[0], EX_REFUSED)
    check("…before it reads anything: no folder at all is still refused",
          run("write", "--apply", "--settings-dir", os.path.join(tmp, "none"), environ={"CLAUDECODE": "1"})[0],
          EX_REFUSED)
    check("a session may still look: write without --apply runs", run("write", *sd, environ={"CLAUDECODE": "1"})[0],
          EX_WAITING)

    # 6. A part file that is a link, or changed while it ran, or reads back wrong, is never written.
    real = os.path.join(tmp, "elsewhere.conf")
    shutil.move(os.path.join(parts, "sweep.conf"), real)
    os.symlink(real, os.path.join(parts, "sweep.conf"))
    before, target = snapshot(parts), get(real)
    code, said = run("write", "--apply", *sd)
    check("a part file that is a link stops the write, and nothing is written anywhere",
          (code, snapshot(parts) == before, get(real) == target,
           any("sweep.conf" in s and "link" in s for s in said)), (EX_FIX, True, True, True))
    os.remove(os.path.join(parts, "sweep.conf"))
    shutil.move(real, os.path.join(parts, "sweep.conf"))
    folder, _p = parse_machine(get(machine), kit)
    plan = plan_file(folder, kit, "stage-e.conf", me)
    with open(os.path.join(parts, "stage-e.conf"), "a", encoding="utf-8") as fh:
        fh.write("# a person's edit\n")
    edited = get(os.path.join(parts, "stage-e.conf"))
    check("a file changed while it ran is not written", ("changed while this ran" in (apply_plan(plan) or ""),
                                                          get(os.path.join(parts, "stage-e.conf"))), (True, edited))
    plan = plan_file(folder, kit, "stage-e.conf", me)
    real_read, calls = globals()["read_text"], [0]

    def wrong_back(path):
        calls[0] += 1
        return real_read(path) + ("X=1\n" if calls[0] == 2 else "")     # 1: the re-read, 2: the read back
    globals()["read_text"] = wrong_back
    try:
        why = apply_plan(plan)
    finally:
        globals()["read_text"] = real_read
    check("a file that reads back differently is said, never called written", "reads back differently" in (why or ""),
          True)
    real_plan = globals()["plan_file"]

    def plan_then_edit(folder, kit, name, uid, record=None, force=()):    # the last file changes after it is read
        plan = real_plan(folder, kit, name, uid, record, force)
        if name == "watcher.conf":
            with open(os.path.join(parts, name), "a", encoding="utf-8") as fh:
                fh.write("# edited while write ran\n")
        return plan

    def others():
        return dict((k, v) for k, v in snapshot(parts).items() if not k.endswith("watcher.conf"))
    forget()                            # the cases above wrote stage-e.conf past the record
    before = others()
    globals()["plan_file"] = plan_then_edit
    try:
        code, said = run("write", "--apply", *sd)
    finally:
        globals()["plan_file"] = real_plan
    check("a part file that changes after it is read stops the whole write before any file is written",
          (code, others() == before, any("watcher.conf" in s and "changed while this ran" in s for s in said)),
          (EX_FIX, True, True))
    crlf = os.path.join(parts, "bridge.conf")
    put(crlf, "ROLE_ACCOUNT=_old\r\nJOB_LABEL=com.test.bridge\r\n# no final newline\r\nTEAM_KEYS=X", 0o600)
    forget()
    settings("[shared]\nROLE_ACCOUNT=_crlf\n")
    run("write", "--apply", *sd)
    check("CRLF endings and a missing final newline are kept",
          get(crlf), "ROLE_ACCOUNT=_crlf\r\nJOB_LABEL=com.test.bridge\r\n# no final newline\r\nTEAM_KEYS=X")

    # 7. Off on this Mac, and one file's own place.
    os.remove(os.path.join(parts, "watcher.conf"))
    code, said = run("write", "--apply", *sd)
    check("a part file that is not there is off, and is not made",
          (os.path.lexists(os.path.join(parts, "watcher.conf")), any(s.startswith("Off on this Mac") and
                                                                     "watcher.conf" in s for s in said)), (False, True))
    ops = os.path.join(tmp, "ops", "notifier.conf")
    os.makedirs(os.path.dirname(ops))
    shutil.move(os.path.join(parts, "notifier.conf"), ops)
    settings("NOTIFIER_CONF=%s\n[shared]\nROLE_ACCOUNT=_moved\n" % ops)
    run("write", "--apply", *sd)
    check("a part file's own place moves where it is written",
          (conf_values(get(ops)).get("ROLE_ACCOUNT"), os.path.lexists(os.path.join(parts, "notifier.conf"))),
          ("_moved", False))

    # 8. Places are settings: each one shown, the defaults the kit's own, a bad place refused.
    settings("CONSENT_RECORD=/srv/consent.json\nSTATE_DIR=/srv/state\nSECRETS_DIR=~/.vault\n")
    code, said = run("where", *sd)
    text = "\n".join(said)
    check("where shows each place from the folder",
          (code, "/srv/consent.json ([places] CONSENT_RECORD)" in text, "/srv/state ([places] STATE_DIR)" in text,
           "~/.vault ([places] SECRETS_DIR)" in text, "%s ([places] PARTS_DIR)" % parts in text), (EX_OK,) + (True,) * 4)
    os.remove(machine)
    text = "\n".join(run("where", *sd)[1])
    check("…and the defaults when the folder names none",
          ("%s (the default)" % consent_default() in text, "%s (the default: this kit folder)" % KIT in text,
           "~/.stage-e (the default)" in text), (True, True, True))
    check("the consent record's default is the one command's own",
          consent_default(), "/Library/Application Support/claude-pipeline/consent-record.json")
    for bad in ("CONSENT_RECORD=relative.json", "STATE_DIR=state", "BRIDGE_CONF=/x/other.conf", "PARTS_DIR=/a/../b"):
        put(machine, "[places]\n%s\n" % bad)
        code, said = run("check", *sd)
        check("a bad place is a problem: %s" % bad.split("=")[0],
              (code, any("%s must be" % bad.split("=")[0] in s for s in said)), (EX_FIX, True))

    # 9. Secrets: every credential shape, a key named like one, a long random string; the value never shown.
    samples = [s[3] for s in secret_shapes.SHAPES] + ["Ab1Cd2" * 6, "0123456789abcdef" * 2]
    for value in samples:
        for body in ("[shared]\nLINEAR_KEY_ENV=%s\n" % value, "[shared]\n# saved it: %s\n" % value):
            settings(body)
            before = snapshot(parts)
            code, said = run("check", *sd)
            check("check fails on %s…" % value[:6], (code, any("shaped like" in s for s in said),
                                                      any(value in s for s in said)), (EX_FIX, True, False))
            code, said = run("write", "--apply", *sd)
            check("…and nothing is written from it (%s…)" % value[:6],
                  (code, snapshot(parts) == before, any(value in s for s in said)), (EX_FIX, True, False))
    settings("[stage-e.conf]\nGITHUB_APP_PASSWORD=hunter\n")
    said = run("check", *sd)[1]
    check("a key named like a secret is a problem of its own", any("named like a secret" in s for s in said), True)
    clean = ["/Users/someone/Library/Application Support/x", "~/.stage-e/env", "com.example.dispatcher",
             "00000000-0000-4000-8000-000000000001", "STAGE_E_LINEAR_API_KEY", "https://github.com/o/r",
             "claude-opus-4-1-20250805", "0123456789abcdef0123456789abcdef01234567", "U0123456789", "3456",
             "/private/tmp/x/-Users-someone-claude-project-kit--claude-worktrees-quiet-river-966e0c/parts",
             "/Users/Someone/Projects2026/MyApp/settings", "mon-sat 09:00 6, sun off", "Hooks change guard"]
    check("ordinary values are not shaped like a secret", [v for v in clean if secret_labels(v)], [])

    # 10. Private, no links, never in a repository.
    settings("[shared]\nROLE_ACCOUNT=_x\n")
    check("a private folder passes", run("check", *sd)[0], EX_OK)
    for path, mode, label in ((sdir, 0o755, "the settings folder"), (machine, 0o644, MACHINE)):
        os.chmod(path, mode)
        said = run("check", *sd)[1]
        check("%s readable by other accounts is a problem" % label,
              any(s.startswith("PROBLEM: %s can be read by other accounts" % label) for s in said), True)
        os.chmod(path, 0o700 if path == sdir else 0o600)
    check("a folder of another account's is a problem",
          any("belongs to another account" in s for s in run("check", *sd, uid=other)[1]), True)
    os.symlink(machine, os.path.join(sdir, "extra.conf"))
    check("a link inside the folder is a problem",
          any("extra.conf is a link" in s for s in run("check", *sd)[1]), True)
    os.remove(os.path.join(sdir, "extra.conf"))
    linked = os.path.join(tmp, "linked")
    os.symlink(sdir, linked)
    check("a folder that is a link is a problem",
          any("the settings folder is a link" in s for s in run("check", "--settings-dir", linked)[1]), True)
    inside = os.path.join(repo, "settings")
    shutil.copytree(sdir, inside)
    check("a folder inside a git working tree is a problem",
          any("inside a git working tree" in s for s in run("check", "--settings-dir", inside)[1]), True)

    # 11. The lines: what is refused, and the value never in the words.
    for body, words in (("[nope]\n", "[nope] is not a section"), ("ROLE_ACCOUNT=_x\n", "comes before any"),
                        ("[shared]\nROLE_ACCOUNT=_a\nROLE_ACCOUNT=_b\n", "set twice"),
                        ("[shared]\nNOT_SHARED=_x\n", "is not a shared value"),
                        ("[queue.conf]\nNO_SUCH_KEY=_x\n", "is not a key queue.conf.example names"),
                        ("[shared]\njust words\n", "is not KEY=value"),
                        ("[shared]\n[shared]\n", "appears twice"),
                        ("[bridge.conf]\nAPP_NAME=Rex\n", "the one command asks for the tech lead's name"),
                        ("[queue.conf]\nPLANNED_LOAD=30\n", "the tech lead changes it"),
                        ("[update.conf]\nCREDENTIAL_EXPIRIES=x\n", "write the end dates")):
        put(machine, body)
        code, said = run("check", *sd)
        check("check refuses: %s" % words, (code, any(words in s for s in said)), (EX_FIX, True))
    put(machine, "[shared]\nROLE_ACCOUNT=_only\n")
    for name in kit.files:
        put(os.path.join(parts, name), get(kit.examples[name]))
    forget()
    upd = os.path.join(parts, "update.conf")
    put(upd, get(upd).replace("DISPATCHER_SERVICE=com.example.dispatcher", "DISPATCHER_SERVICE=").replace(
        "ROLE_ACCOUNT=_exdispatch", "ROLE_ACCOUNT=").replace("DISPATCHER_ENV_FILE=/opt/example-dispatcher/.env",
                                                             "DISPATCHER_ENV_FILE="))
    put(machine, "[places]\nPARTS_DIR=%s\n[shared]\nROLE_ACCOUNT=_only\n" % parts)
    before = snapshot(parts)
    code, said = run("write", "--apply", *sd)
    check("update.conf's three dispatcher values go together: one alone writes nothing anywhere",
          (code, snapshot(parts) == before, any("update.conf" in s and "all together" in s for s in said)),
          (EX_FIX, True, True))

    # 12. The map: every target is a part file's key its example names, and none is written twice.
    seen = []
    for entry in MAP:
        for name, key in targets(entry):
            check("%s's target %s %s is a key that file's example names" % (entry[0], name, key),
                  name in kit.keys and key in kit.keys[name], True)
            seen.append((name, key))
    check("no part file's key is the target of two shared values", len(seen), len(set(seen)))
    check("the keys that go together are all shared targets",
          [k for k in TOGETHER.get("update.conf", ()) if ("update.conf", k) not in seen], [])
    check("no tool's key is a shared target", [k for k in TOOL_KEYS if k in seen], [])
    check("every tool's key is a key its file's example names", [k for k in TOOL_KEYS if k[1] not in kit.keys[k[0]]], [])
    check("map runs anywhere and lists every shared value",
          len([s for s in run("map")[1] if re.match(r"^[A-Z0-9_]+: ", s)]), len(MAP))

    # 14. One file per project, composed into each part's list keys.
    for name in kit.files:
        put(os.path.join(parts, name), get(kit.examples[name]))
    forget()
    pdir = os.path.join(sdir, PROJECTS)
    os.makedirs(pdir, 0o700, exist_ok=True)
    os.chmod(pdir, 0o700)

    def project(name, body):
        put(os.path.join(pdir, name + ".conf"), body)

    def now(name):
        return conf_values(get(os.path.join(parts, name)))
    settings("")
    project("app", "REPO=acme/app\nTEAM_KEY=ENG\nPARTS=review work-queue tech-lead\n")
    project("web", "REPO=acme/web\nTEAM_KEY=WEB\nPARTS=review\n")
    code, said = run("check", *sd)
    check("project files pass check, and are counted", (code, any("2 projects" in s for s in said)), (EX_OK, True))
    run("write", "--apply", *sd)
    check("each part's list is the projects it covers",
          (as_list(now("stage-e.conf")["REVIEW_REPOS"]), as_list(now("stage-e.conf")["MANAGED_TEAM_KEYS"]),
           as_list(now("queue.conf")["TEAM_KEYS"]), as_list(now("queue.conf")["REPO_BY_TEAM"]),
           as_list(now("bridge.conf")["TEAM_KEYS"])),
          (["acme/app", "acme/web"], ["ENG", "WEB"], ["ENG"], ["ENG=acme/app"], ["ENG"]))
    check("a list key no project covers is left as it is", (now("stage-a.conf")["PLANNED_REPOS"],
          now("notifier.conf")["TEAM_KEYS"]), (conf_values(get(kit.examples["stage-a.conf"]))["PLANNED_REPOS"],
                                               conf_values(get(kit.examples["notifier.conf"]))["TEAM_KEYS"]))
    stage_e = os.path.join(parts, "stage-e.conf")
    put(stage_e, get(stage_e).replace("REVIEW_REPOS=" + now("stage-e.conf")["REVIEW_REPOS"],
                                      "REVIEW_REPOS=acme/web , acme/app"))
    check("a list in another order is no change", run("write", *sd)[0], EX_OK)
    settings("[stage-e.conf]\nREVIEW_REPOS=acme/only\n")
    check("check says where a part's own list stops the projects", any(
        "stage-e.conf sets REVIEW_REPOS itself, so the projects do not reach it there" in s
        for s in run("check", *sd)[1]), True)
    run("write", "--apply", *sd)
    check("…and that own list is what is written", now("stage-e.conf")["REVIEW_REPOS"], "acme/only")
    settings("")
    for body, words in (("REPO=acme/x\nOTHER=x\nPARTS=review\n", "holds only REPO, TEAM_KEY and PARTS"),
                        ("REPO=not a repo\nPARTS=review\n", "REPO must be OWNER/NAME"),
                        ("TEAM_KEY=eng\nPARTS=review\n", "TEAM_KEY must be a team key"),
                        ("REPO=acme/x\nPARTS=review status\n", "takes no list from a project"),
                        ("PARTS=review\n", "names neither a REPO nor a TEAM_KEY"),
                        ("REPO=acme/x\nREPO=acme/y\nPARTS=review\n", "REPO is set twice")):
        project("bad", body)
        code, said = run("check", *sd)
        check("check refuses a project file: %s" % words, (code, any(words in s for s in said)), (EX_FIX, True))
    os.remove(os.path.join(pdir, "bad.conf"))
    project("app2", "REPO=acme/app\nPARTS=review\n")
    check("two projects that name one REPO are refused",
          any("name the same REPO" in s for s in run("check", *sd)[1]), True)
    os.remove(os.path.join(pdir, "app2.conf"))
    put(os.path.join(pdir, "Notes.txt"), "x\n")
    check("a file in projects/ that is not a project's is refused",
          any("projects/Notes.txt is not a project's file" in s for s in run("check", *sd)[1]), True)
    os.remove(os.path.join(pdir, "Notes.txt"))
    project("leak", "REPO=acme/leak\nPARTS=review\n# %s\n" % samples[1])
    said = run("check", *sd)[1]
    check("a secret in a project's file is refused, and never shown",
          (any("projects/leak.conf" in s and "shaped like" in s for s in said), any(samples[1] in s for s in said)),
          (True, False))
    os.remove(os.path.join(pdir, "leak.conf"))
    check("map lists the keys the projects fill",
          len([s for s in run("map")[1] if "of every project" in s]), len(COMPOSED))
    project("ops", "TEAM_KEY=OPS\nPARTS=work-queue\n")
    check("a team the queue covers with no repository is refused, as the queue's installer would",
          any("projects/ops.conf names work-queue with a TEAM_KEY and no REPO" in s for s in run("check", *sd)[1]), True)
    settings("[queue.conf]\nREPO_BY_LABEL=repo:ops=acme/ops\n")
    check("…unless [queue.conf] sets REPO_BY_LABEL, as the queue allows", run("check", *sd)[0], EX_OK)
    os.remove(os.path.join(pdir, "ops.conf"))
    project("solo", "REPO=acme/solo\nPARTS=work-queue\n")
    check("a repository the queue covers with no team is refused: it would never reach the queue",
          any("projects/solo.conf names work-queue but has no TEAM_KEY" in s for s in run("check", *sd)[1]), True)
    os.remove(os.path.join(pdir, "solo.conf"))
    settings("")
    check("each list key belongs to its part in the parts list, and is a key its example names",
          [e for e in COMPOSED if kit.part_file.get(e[0]) != e[1] or e[2] not in kit.keys.get(e[1], ())], [])
    there = os.path.join(tmp, "there.conf")
    put(there, "mine\n")
    try:
        put_file(there, "theirs\n", 0o600, new=True)
        replaced = True
    except OSError:
        replaced = False
    check("a new file is never put over one that is there", (replaced, get(there)), (False, "mine\n"))

    # 15. A first run: gather what the part files say, show it, and write nothing until a yes.
    gparts, gdir = os.path.join(tmp, "gparts"), os.path.join(tmp, "gathered")
    os.makedirs(gparts)

    def fill(name, folder=gparts, **values):
        text = get(kit.examples[name])
        for key, value in values.items():
            text = set_line(text, key, value)
        put(os.path.join(folder, name), text)
    pipe = {"ROLE_ACCOUNT": "_pipe"}
    for name in kit.files:
        if name not in ("chat-lane.conf", "conflict-waker.conf"):
            fill(name, **pipe)
    fill("stage-a.conf", ROLE_ACCOUNT="_pipe", DISPATCHER_ACCOUNT="_pipe", PLANNED_REPOS="acme/app")
    fill("stage-e.conf", ROLE_ACCOUNT="_pipe", REVIEW_REPOS="acme/app,acme/kit", MANAGED_TEAM_KEYS="ENG,KIT",
         GITHUB_APP_LOGIN=samples[0], OLD_KEY="x", OLD_API_TOKEN="abc")
    fill("queue.conf", ROLE_ACCOUNT="_pipe", TEAM_KEYS="ENG,WEB", REPO_BY_TEAM="ENG=acme/app",
         REPO_BY_LABEL="repo:web=acme/web")
    fill("bridge.conf", ROLE_ACCOUNT="_pipe", TEAM_KEYS="ENG", APP_NAME="Rex")
    fill("notifier.conf", ROLE_ACCOUNT="_pipe", TEAM_KEYS="KIT")
    fill("sweep.conf", ROLE_ACCOUNT="_pipe", TEAM_KEYS="ENG,KIT")
    fill("watcher.conf", ROLE_ACCOUNT="_pipe", WATCH_REPOS="acme/app")
    fill("acceptance.conf", ROLE_ACCOUNT="_pipe", ACCEPTANCE_REPOS="acme/app")
    fill("update.conf", ROLE_ACCOUNT="_pipe", DISPATCHER_PORT="3999")
    gsd = ("--settings-dir", gdir, "--parts-dir", gparts)

    def block(said, path):              # the lines gather shows for one file it would write
        out, on = [], False
        for s in said:
            if s.startswith("--- "):
                on = s == "--- %s would hold:" % path
            elif on and s.startswith("    "):
                out.append(s[4:])
        return out
    before = snapshot(gparts)
    code, said = run("gather", *gsd)
    text = "\n".join(said)
    check("gather shows what it would make, and writes nothing", (code, os.path.lexists(gdir), snapshot(gparts) == before),
          (EX_WAITING, False, True))
    machine_shown = block(said, os.path.join(gdir, MACHINE))
    check("…a value every part file sets the same is shared once", "ROLE_ACCOUNT=_pipe" in machine_shown, True)
    check("…one the files disagree on stays each file's, and the disagreement is said",
          ("DISPATCHER_PORT is not shared" in text, "DISPATCHER_PORT=3999" in machine_shown), (True, True))
    check("…one a file does not set is not shared either, and it says how to share it", (
        "bridge.conf ENV_FILE does not set it" in text, "put it once under [shared]" in text), (True, True))
    check("…the queue's pair is one project, with its repository and its team",
          block(said, os.path.join(gdir, PROJECTS, "app.conf"))[1:],
          ["REPO=acme/app", "TEAM_KEY=ENG", "PARTS=acceptance-check cleanup idea-gate review tech-lead watcher work-queue"])
    check("…a team and a repository no file pairs are each their own project, and it says how to join them",
          (block(said, os.path.join(gdir, PROJECTS, "kit.conf"))[1:],
           block(said, os.path.join(gdir, PROJECTS, "team-kit.conf"))[1:], "No file pairs these teams" in text),
          (["REPO=acme/kit", "PARTS=review"], ["TEAM_KEY=KIT", "PARTS=cleanup paging review"], True))
    check("…the list keys the projects give are not also kept in a section",
          [s for s in machine_shown if s.split("=")[0] in ("REVIEW_REPOS", "MANAGED_TEAM_KEYS", "REPO_BY_TEAM")], [])
    check("…a value shaped like a secret is never copied, and is named", (
        "stage-e.conf GITHUB_APP_LOGIN: it is shaped like" in text, samples[0] in text,
        "stage-e.conf OLD_API_TOKEN: it is shaped like a key named like a secret" in text), (True, False, True))
    check("…a key another tool writes stays in its file, and is named",
          ("bridge.conf APP_NAME: another tool writes it" in text, [s for s in machine_shown if "Rex" in s]),
          (True, []))
    check("…a key the example does not name stays in its file, and is named",
          "stage-e.conf OLD_KEY: stage-e.conf.example does not name it" in text, True)
    check("gather --apply without a yes writes nothing",
          (run("gather", "--apply", *gsd, ask=lambda p: "no")[0], os.path.lexists(gdir)), (EX_WAITING, False))
    for env, uid in (({"CLAUDECODE": ""}, me), ({}, 0)):
        check("gather --apply is refused in a session, and as root (%s)" % (uid or "session"),
              (run("gather", "--apply", *gsd, environ=env, uid=uid, ask=lambda p: "yes")[0], os.path.lexists(gdir)),
              (EX_REFUSED, False))
    def mode_of(path):                  # None for a path that is not there, so a case fails, never crashes
        return oct(stat.S_IMODE(os.stat(path).st_mode)) if os.path.exists(path) else None
    code, said = run("gather", "--apply", *gsd, ask=lambda p: "yes")
    check("with the yes it writes the folder, private, and the folder passes check", (
        code, mode_of(gdir), mode_of(os.path.join(gdir, MACHINE)), mode_of(os.path.join(gdir, PROJECTS)),
        sorted(os.listdir(os.path.join(gdir, PROJECTS))) if os.path.isdir(os.path.join(gdir, PROJECTS)) else None),
        (EX_OK, "0o700", "0o600", "0o700", ["app.conf", "kit.conf", "team-kit.conf", "web.conf"]))
    check("…and write then finds nothing to change: the folder says what the part files say",
          (run("write", "--settings-dir", gdir)[0], snapshot(gparts) == before), (EX_OK, True))
    if os.path.isdir(gdir):
        with open(os.path.join(gdir, MACHINE), "a", encoding="utf-8") as fh:
            fh.write("# a line of the person's own, which a second gather must not lose\n")
    kept = get(os.path.join(gdir, MACHINE)) if os.path.exists(os.path.join(gdir, MACHINE)) else None
    code = run("gather", "--apply", *gsd, ask=lambda p: "yes")[0]
    check("gather never fills a folder that already holds settings", (code, get(os.path.join(gdir, MACHINE))
          if os.path.exists(os.path.join(gdir, MACHINE)) else None), (EX_OK, kept if kept else "a filled folder"))
    tdir = os.path.join(tmp, "fresh")
    run("init", "--settings-dir", tdir)
    check("…but it fills one that init made and nobody filled",
          run("gather", "--apply", "--settings-dir", tdir, "--parts-dir", gparts, ask=lambda p: "yes")[0], EX_OK)
    cdir = os.path.join(tmp, "commented")
    run("init", "--settings-dir", cdir)
    with open(os.path.join(cdir, MACHINE), "a", encoding="utf-8") as fh:
        fh.write("# my own note\n")
    noted = get(os.path.join(cdir, MACHINE))
    check("…and never one whose file the person has written in, even a comment",
          (run("gather", "--apply", "--settings-dir", cdir, "--parts-dir", gparts, ask=lambda p: "yes")[0],
           get(os.path.join(cdir, MACHINE))), (EX_OK, noted))
    os.chmod(os.path.join(cdir, MACHINE), 0o000)
    code, said = run("gather", "--settings-dir", cdir, "--parts-dir", gparts)
    os.chmod(os.path.join(cdir, MACHINE), 0o600)
    check("a machine.conf that cannot be read is said as that, never as nothing to do",
          (code, any("could not be read" in s for s in said)), (EX_UNREAD, True))
    real_put, calls = globals()["put_file"], [0]

    def fails_second(path, text, mode, new=False):    # a full disk, part way through
        calls[0] += 1
        if calls[0] == 2:
            raise OSError("no space left")
        return real_put(path, text, mode, new)
    for label, target in (("a folder not there yet", os.path.join(tmp, "half")),
                          ("init's untouched folder", os.path.join(tmp, "half-init"))):
        if "init" in label:
            run("init", "--settings-dir", target)
        before_fail = sorted(os.listdir(tmp))
        calls[0], globals()["put_file"] = 0, fails_second
        try:
            code, said = run("gather", "--apply", "--settings-dir", target, "--parts-dir", gparts, ask=lambda p: "yes")
        finally:
            globals()["put_file"] = real_put
        left = sorted(os.listdir(target)) if os.path.isdir(target) else None
        check("a gather that fails part way into %s leaves it as it was" % label, (
            code, left, sorted(os.listdir(tmp)) == before_fail, any("Nothing was left behind" in s for s in said)),
            (EX_FAILED, [MACHINE] if "init" in label else None, True, True))
        check("…so gather can simply run again (%s)" % label,
              run("gather", "--apply", "--settings-dir", target, "--parts-dir", gparts, ask=lambda p: "yes")[0], EX_OK)
    fill("stage-e.conf", ROLE_ACCOUNT="_pipe", REVIEW_REPOS="acme/kit", MANAGED_TEAM_KEYS="ENG,KIT")
    code, said = run("gather", "--settings-dir", os.path.join(tmp, "g3"), "--parts-dir", gparts)
    check("a list the projects would not give exactly stays in its section as it is", (
        code, any("stage-e.conf REVIEW_REPOS stays in [stage-e.conf] as it is" in s for s in said),
        "REVIEW_REPOS=acme/kit" in block(said, os.path.join(tmp, "g3", MACHINE))), (EX_WAITING, True, True))

    def edit_then_yes(prompt):          # a part file changes while the person reads
        fill("sweep.conf", ROLE_ACCOUNT="_pipe", TEAM_KEYS="ENG,KIT", SWEEP_IDLE_DAYS="9")
        return "yes"
    check("a part file that changes before the yes stops the write",
          (run("gather", "--apply", "--settings-dir", os.path.join(tmp, "g4"), "--parts-dir", gparts,
               ask=edit_then_yes)[0], os.path.lexists(os.path.join(tmp, "g4", MACHINE))), (EX_FIX, False))
    check("gather keeps the record, so a hand edit made after it is found",
          any("HAND EDIT sweep.conf SWEEP_IDLE_DAYS: the file says 9, the folder says 3" in s
              for s in run("write", "--settings-dir", gdir)[1]), True)
    lparts = os.path.join(tmp, "lparts")
    os.makedirs(lparts)
    fill("queue.conf", folder=lparts, **pipe)
    os.symlink(os.path.join(gparts, "sweep.conf"), os.path.join(lparts, "sweep.conf"))
    code, said = run("gather", "--settings-dir", os.path.join(tmp, "g5"), "--parts-dir", lparts)
    check("a part file that is a link stops the gather", (code, any(s.startswith("STOP:") and "link" in s for s in said)),
          (EX_FIX, True))
    real_agree = globals()["agree"]
    globals()["agree"] = lambda present, have: bool(have)         # a gather that shares what the files disagree on
    try:
        code, said = run("gather", "--settings-dir", os.path.join(tmp, "g6"), "--parts-dir", gparts)
    finally:
        globals()["agree"] = real_agree
    check("a gathered folder that would change a part file is stopped before it is shown",
          (code, any("fails its own proof: it would change" in s for s in said)), (EX_FIX, True))
    empty = os.path.join(tmp, "noparts")
    os.makedirs(empty)
    check("no part file at all: nothing to gather, said",
          run("gather", "--settings-dir", os.path.join(tmp, "g7"), "--parts-dir", empty)[0], EX_OK)

    # 16. A hand edit: found before the next write, shown with both values, and the person chooses.
    for name in kit.files:
        put(os.path.join(parts, name), get(kit.examples[name]))
    for entry in os.listdir(pdir):
        os.remove(os.path.join(pdir, entry))
    forget()
    settings("[shared]\nROLE_ACCOUNT=_one\n[stage-e.conf]\nPOLL_INTERVAL_SECONDS=150\nGITHUB_APP_LOGIN=bot-login\n")
    rec = os.path.join(sdir, RECORD)
    code = run("write", "--apply", *sd)[0]
    check("a first write asks nothing, and keeps a private record of what it wrote",
          (code, mode_of(rec), (read_record(sdir) if os.path.exists(rec) else {}).get("stage-e.conf", {}).get(
              "POLL_INTERVAL_SECONDS")), (EX_OK, "0o600", "150"))
    stage_e, bridge = os.path.join(parts, "stage-e.conf"), os.path.join(parts, "bridge.conf")
    put(stage_e, set_line(get(stage_e), "POLL_INTERVAL_SECONDS", "300"))
    put(bridge, set_line(get(bridge), "ROLE_ACCOUNT", "_hand"))
    before = snapshot(parts)
    code, said = run("write", *sd)
    text = "\n".join(said)
    check("a hand edit is found before the next write, and shown with both values", (
        code, "HAND EDIT stage-e.conf POLL_INTERVAL_SECONDS: the file says 300, the folder says 150" in text,
        "HAND EDIT bridge.conf ROLE_ACCOUNT: the file says _hand, the folder says _one" in text,
        snapshot(parts) == before), (EX_WAITING, True, True, True))
    check("…and write --apply writes nothing until the person chooses",
          (run("write", "--apply", *sd, ask=lambda p: "")[0], snapshot(parts) == before), (EX_WAITING, True))

    def choose(prompt):                 # the file wins for the interval, the folder for the account
        return "file" if " POLL_INTERVAL_SECONDS:" in prompt else "folder"
    code = run("write", "--apply", *sd, ask=choose)[0]
    mine = parse_machine(get(machine), kit)[0]
    check("the file's value, chosen, stays in the file and goes in that part's own section", (
        code, conf_values(get(stage_e)).get("POLL_INTERVAL_SECONDS"),
        mine.parts.get("stage-e.conf", {}).get("POLL_INTERVAL_SECONDS")), (EX_OK, "300", "300"))
    check("the folder's value, chosen, is written back, and the shared value is untouched", (
        conf_values(get(bridge)).get("ROLE_ACCOUNT"), mine.shared.get("ROLE_ACCOUNT"),
        conf_values(get(os.path.join(parts, "queue.conf"))).get("ROLE_ACCOUNT")), ("_one", "_one", "_one"))
    check("…and the next write finds nothing: the record holds what was chosen", run("write", *sd)[0], EX_OK)
    put(stage_e, set_line(get(stage_e), "GITHUB_APP_LOGIN", samples[0]))
    kept_machine = get(machine)
    code, said = run("write", "--apply", *sd, ask=lambda p: "file")
    check("a file's value shaped like a secret is never taken into the folder, nor shown", (
        code, get(machine) == kept_machine, any("can never go in the folder" in s for s in said),
        any(samples[0] in s for s in said)), (EX_WAITING, True, True, False))
    run("write", "--apply", *sd, ask=lambda p: "folder")
    check("…while the folder's value can still win", conf_values(get(stage_e)).get("GITHUB_APP_LOGIN"), "bot-login")
    put(stage_e, "".join(l for l in get(stage_e).splitlines(True) if not l.startswith("POLL_INTERVAL_SECONDS=")))
    code, said = run("write", "--apply", *sd, ask=lambda p: "file")
    check("a key the file no longer sets cannot be taken from it",
          (code, any("no longer sets POLL_INTERVAL_SECONDS" in s for s in said)), (EX_WAITING, True))
    run("write", "--apply", *sd, ask=lambda p: "folder")
    check("…and the folder's value goes back in", conf_values(get(stage_e)).get("POLL_INTERVAL_SECONDS"), "300")
    put(rec, "{not a record")
    check("a record that cannot be read stops the write, and never reads as no record",
          run("write", *sd)[0], EX_FIX)
    forget()

    # 17. A new Mac: one plain export file, and its import, which writes nothing before a yes.
    project("app", "REPO=acme/app\nTEAM_KEY=ENG\nPARTS=review\n")
    run("write", "--apply", *sd)                 # the record exists, and must not travel
    carry = os.path.join(tmp, "carry")
    os.makedirs(carry)
    export = os.path.join(carry, "settings.txt")
    code = run("export", "--to", export, *sd)[0]
    bundle = get(export) if os.path.exists(export) else ""
    check("export writes one private file: machine.conf and each project, under their names", (
        code, mode_of(export), "=== machine.conf" in bundle, "=== projects/app.conf" in bundle),
        (EX_OK, "0o600", True, True))
    check("…never the record, and nothing shaped like a secret", (RECORD in bundle, secret_problems(bundle)),
          (False, []))
    check("…and it reads back as the folder's own files",
          parse_export(bundle)[0], dict((p, ended(t)) for p, t in bundle_files(sdir)))
    check("export never writes over a file that is there",
          (run("export", "--to", export, *sd)[0], get(export) == bundle), (EX_FAILED, True))
    check("export is refused in a session",
          run("export", "--to", os.path.join(carry, "s2.txt"), *sd, environ={"CLAUDECODE": "1"})[0], EX_REFUSED)
    check("export never writes inside a git working tree", (
        run("export", "--to", os.path.join(repo, "s.txt"), *sd)[0], os.path.lexists(os.path.join(repo, "s.txt"))),
        (EX_FIX, False))
    mine = get(machine)
    for label, extra in (("a secret", "# saved: %s\n" % samples[1]), ("a broken line", "[nope]\n")):
        put(machine, mine + extra)
        target = os.path.join(carry, "s3.txt")
        check("export refuses a folder that fails check (%s): nothing leaves it" % label,
              (run("export", "--to", target, *sd)[0], os.path.lexists(target)), (EX_FIX, False))
    put(machine, mine)
    newmac = os.path.join(tmp, "newmac")
    nsd = ("--settings-dir", newmac, "--from", export)
    before = snapshot(parts)
    code, said = run("import", *nsd)
    text = "\n".join(said)
    check("import shows the folder the file makes, and writes nothing", (
        code, os.path.lexists(newmac), "--- %s would hold:" % os.path.join(newmac, MACHINE) in text,
        "these part files were on" in text), (EX_WAITING, False, True, True))
    check("import --apply without a yes writes nothing",
          (run("import", "--apply", *nsd, ask=lambda p: "no")[0], os.path.lexists(newmac)), (EX_WAITING, False))
    check("import --apply is refused in a session", (
        run("import", "--apply", *nsd, environ={"CLAUDECODE": "1"}, ask=lambda p: "yes")[0],
        os.path.lexists(newmac)), (EX_REFUSED, False))

    def edit_export_then_yes(prompt):   # the export file changes while the person reads
        put(export, bundle + "# a line added late\n")
        return "yes"
    check("an export file that changes before the yes stops the import", (
        run("import", "--apply", *nsd, ask=edit_export_then_yes)[0], os.path.lexists(newmac)), (EX_FIX, False))
    put(export, bundle)
    code = run("import", "--apply", *nsd, ask=lambda p: "yes")[0]
    check("with the yes it writes the folder, private, as the export holds it, with no record and no part file", (
        code, mode_of(newmac), get(os.path.join(newmac, MACHINE)) if os.path.isdir(newmac) else None,
        get(os.path.join(newmac, PROJECTS, "app.conf")) if os.path.isdir(newmac) else None,
        os.path.lexists(os.path.join(newmac, RECORD)), snapshot(parts) == before),
        (EX_OK, "0o700", ended(get(machine)), ended(get(os.path.join(pdir, "app.conf"))), False, True))
    held = get(os.path.join(newmac, MACHINE)) if os.path.isdir(newmac) else None
    check("import never fills a folder that already holds settings",
          (run("import", "--apply", *nsd, ask=lambda p: "yes")[0],
           get(os.path.join(newmac, MACHINE)) if os.path.isdir(newmac) else None), (EX_OK, held))
    for n, (label, body, words) in enumerate((
            ("a secret", bundle + "# token: %s\n" % samples[2], "shaped like"),
            ("a path outside the folder", bundle.replace("=== projects/app.conf", "=== ../app.conf"),
             "not one of the settings'"),
            ("a file that is not a setting", bundle + "=== notes.txt\nx\n", "not one of the settings'"),
            ("a line check refuses", bundle.replace("=== machine.conf\n", "=== machine.conf\n[nope]\n"),
             "is not a section"),
            ("no machine.conf", "=== projects/app.conf\nREPO=acme/app\nPARTS=review\n", "holds no machine.conf"))):
        source, target = os.path.join(carry, "bad-%d.txt" % n), os.path.join(tmp, "newmac-%d" % n)
        put(source, body)
        code, said = run("import", "--apply", "--settings-dir", target, "--from", source, ask=lambda p: "yes")
        check("import refuses an export with %s, writes nothing, and never shows a secret" % label, (
            code, os.path.lexists(target), any(words in s for s in said), any(samples[2] in s for s in said)),
            (EX_FIX, False, True, False))
    elsewhere = os.path.join(carry, "elsewhere.txt")
    put(elsewhere, bundle.replace("PARTS_DIR=%s" % parts, "PARTS_DIR=/no/such/folder"))
    check("import says when a place it names is not on this Mac", any(
        "PARTS_DIR names /no/such/folder, which is not on this Mac" in s
        for s in run("import", "--settings-dir", os.path.join(tmp, "newmac-place"), "--from", elsewhere)[1]), True)

    # 13. What it never does.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("subprocess", "os.system", "Popen", "socket", "urlopen", "sudo", "launchctl", "exec(", "eval(")
    check("it starts no process, opens no connection and calls no installer",
          [b for b in banned if b in above], [])
    check("a part file is only ever written through apply_plan, and the folder's files through put_file",
          (above.count("os.replace("), above.count("O_CREAT")), (2, 2))
    remove_tree(tmp)
    return failures, total[0]


def selftest():
    """The battery once, then once with each rule broken in turn: every mutant must turn it red. Then
    once more, to show it is whole again with every rule restored."""
    failures, count = battery()
    if failures:
        for line in failures:
            print(line)
        print("settings selftest: %d of %d checks failed" % (len(failures), count))
        return 1
    o = dict(globals())
    never = re.compile(r"(?!)")

    def caught(**patched):
        saved = dict((k, globals()[k]) for k in patched)
        globals().update(patched)
        try:
            return bool(battery()[0])
        except Exception:                # a mutant that breaks the battery outright is caught too
            return True
        finally:
            globals().update(saved)

    def crlf_lost(text, key, value):
        return o["set_line"](text, key, value).replace("\r\n", "\n")

    def every_part(projects, entry):    # composed() as though each project named every part
        return o["composed"](dict((n, dict(p, PARTS=list(COMPOSED_PARTS))) for n, p in projects.items()), entry)

    def unpaired(values, g):            # projects_from() that never joins a team to its repository
        projects, lists = o["projects_from"](values, g)
        out = {}
        for n, p in projects.items():
            if p["REPO"] and p["TEAM_KEY"]:
                out[n] = dict(p, TEAM_KEY="")
                out["team-" + n] = dict(p, REPO="")
            else:
                out[n] = p
        return out, lists

    mutants = (
        ("the folder's place ignores %s" % DIR_ENV,
         dict(settings_dir_of=lambda flag, environ: o["settings_dir_of"](flag, {}))),
        ("a shared value misses the last part file it reaches",
         dict(targets=lambda entry: o["targets"](entry)[:-1])),
        ("a part's own value no longer stops the shared one or the projects", dict(own_wins=lambda own, key: False)),
        ("check no longer says where a part's own value wins", dict(overrides=lambda folder: [])),
        ("write without --apply writes",
         dict(cmd_write=lambda sdir, kit, uid, apply, out, stop=None, ask=input: o["cmd_write"](
             sdir, kit, uid, True, out, stop, ask))),
        ("a Claude session is not refused", dict(AGENT_ENV_MARKERS=())),
        ("root is not refused",
         dict(refused=lambda what, environ, uid, out: o["refused"](what, environ, uid or 501, out))),
        ("a file that reads back wrong is called written", dict(reads_back=lambda plan, back: True)),
        ("a file that changed while it ran is written", dict(still_as_planned=lambda plan: "")),
        ("the files are not read again before the first is written", dict(preflight=lambda todo: [])),
        ("a link or another account's part file is planned", dict(unsafe_part=lambda st, uid: "")),
        ("one part file that is held or unread stops no write", dict(blocked=lambda plans: None)),
        ("a written line rewrites the file's line endings", dict(set_line=crlf_lost)),
        ("the notifier's scrub is skipped", dict(scrub=lambda text: [])),
        ("nothing is shaped like a secret", dict(secret_labels=lambda text: [])),
        ("the Slack app token and signing secret shapes are gone", dict(LOCAL_SHAPES=())),
        ("a long random run passes", dict(RANDOM_RUN_RE=never)),
        ("a key named like a secret passes", dict(SECRET_NAME_RE=never)),
        ("a file other accounts can read passes", dict(OTHER_ACCESS=0)),
        ("another account's file passes", dict(foreign=lambda st, uid: False)),
        ("a link in the folder passes", dict(linked=lambda st: False)),
        ("a folder inside a git working tree passes", dict(git_tree=lambda path, stop=None: None)),
        ("init makes the folder readable by others", dict(FOLDER_MODE=0o755)),
        ("where shows the defaults, not the folder's places",
         dict(places_of=lambda folder, kit: o["places_of"](Folder(), kit))),
        ("a part file's own place is ignored",
         dict(part_path=lambda folder, kit, name: (os.path.join(folder.places.get("PARTS_DIR") or kit.root, name), "x"))),
        ("a place that is not a full path passes", dict(place_problems=lambda folder, kit: [])),
        ("an unknown key passes", dict(key_problem=lambda kit, section, key: None)),
        ("another tool's key is kept in the folder", dict(TOOL_KEYS={})),
        ("update.conf's three dispatcher values may split", dict(TOGETHER={})),
        ("a part's list takes projects it does not cover", dict(composed=every_part)),
        ("a list in another order reads as a change", dict(same_value=lambda name, key, have, want: have == want)),
        ("a list key no project covers is written empty", dict(feeds=lambda items: True)),
        ("a project file's own rules pass anything",
         dict(parse_project=lambda name, text: (o["parse_project"](name, text)[0], []))),
        ("two projects may name one repository", dict(duplicate_problems=lambda projects: [])),
        ("any file in projects/ is read as a project", dict(entry_problem=lambda entry, path: None)),
        ("gather copies whatever a part file says", dict(copy_problem=lambda kit, name, key, value: None)),
        ("gather shares a value the files disagree on", dict(agree=lambda present, have: bool(have))),
        ("gather never joins a team to its repository", dict(projects_from=unpaired)),
        ("gather drops a list the projects would not give exactly", dict(lists_match=lambda *a: True)),
        ("gather skips its own proof", dict(round_trip=lambda *a: [])),
        ("gather writes over a folder that holds settings", dict(folder_state=lambda sdir, kit: ("empty", ""))),
        ("a project the queue cannot take passes", dict(queue_problems=lambda folder: [])),
        ("a new file is put over one that is there",
         dict(put_file=lambda path, text, mode, new=False: o["put_file"](path, text, mode))),
        ("a gather that fails part way leaves what it made", dict(undo=lambda made: [])),
        ("a list key goes to another part's file",
         dict(COMPOSED=(("paging",) + COMPOSED[0][1:],) + COMPOSED[1:])),
        ("gather writes without --apply",
         dict(cmd_gather=lambda sdir, kit, uid, parts_dir, apply, out, ask, stop=None: o["cmd_gather"](
             sdir, kit, uid, parts_dir, True, out, lambda prompt: "yes", stop))),
        ("gather and import take any answer for a yes", dict(said_yes=lambda answer: True)),
        ("a hand edit reads as the folder's own change", dict(hand_edited=lambda name, key, have, recorded: False)),
        ("a hand edit is decided without the person",
         dict(ask_choices=lambda edits, ask, out: dict(((p.name, e[0]), "folder") for p, e in edits))),
        ("the file's value goes in [shared], not its own section", dict(own_section=lambda name: "shared")),
        ("a file's value is taken whatever it holds", dict(takeable=lambda name, key, have: None)),
        ("the record is not kept", dict(save_record=lambda *a: None)),
        ("export carries the record",
         dict(bundle_files=lambda sdir: o["bundle_files"](sdir) + [(RECORD, read_text(os.path.join(sdir, RECORD)))])),
        ("export takes a folder that fails check", dict(export_blocked=lambda problems: False)),
        ("import takes a file of any name", dict(bundle_path_ok=lambda path: True)),
        ("import takes settings check would refuse", dict(import_problems=lambda files, kit: (Folder(), []))),
        ("import writes what the export said before it changed", dict(same_export=lambda again, text: True)),
        ("gather writes what the part files said before they changed", dict(unchanged=lambda g, again: True)),
    )
    missed = [label for label, patched in mutants if not caught(**patched)]
    whole, again = battery()
    for label in missed:
        print("FAIL a mutant the battery did not catch: %s" % label)
    for line in whole:
        print("FAIL with every rule restored: %s" % line)
    if missed or whole:
        print("settings selftest: %d of %d mutants not caught, %d checks failed after them"
              % (len(missed), len(mutants), len(whole)))
        return 1
    print("OK: settings selftest (%d checks; each of %d mutants turns it red; whole again after)"
          % (again, len(mutants)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
