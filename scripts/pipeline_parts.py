#!/usr/bin/env python3
"""Every part of the pipeline, listed once, as data, and checked against the kit (KIT-624).

    python3 scripts/pipeline_parts.py check       # the list against its schema and this repository
    python3 scripts/pipeline_parts.py render      # write docs/PARTS.md from the list
    python3 scripts/pipeline_parts.py render --stdout
    python3 scripts/pipeline_parts.py --selftest

WHY.  The pipeline's parts lived only in installer code and in guides scattered across
docs/. A new installer, a new job or a missing guide went unnoticed until a person tripped
on it. `pipeline-parts.json` is the one list: each part's name in the user's words, what it
does, its check and apply commands, what it needs first, its settings and secrets by name,
its jobs, the steps only a person can do, and its guide, lesson and runbook. A later one
command, its end summary and the documents are driven from it.

WHAT `check` FAILS ON
  schema      the list does not match schemas/pipeline-parts.schema.json
  ids         two parts share an id
  needs       a part needs an unknown part, itself, or a cycle
  order       the install order breaks a fixed rule: the lane marker before reviews, reviews
              before planning, the tech lead before paging, the health check after paging
  owners      an installer (scripts/pipeline_*_setup.py and the named five) or a script that
              writes a job file belongs to no part, or a script belongs to two
  paths       a path a part names does not exist
  commands    a check or apply names anything but a kit script, or an argument outside
              ALLOWED_ARGS, or a word the script does not contain
  reasons     an installer, check or apply is null with no reason, or a reason sits beside
              all three
  cards       a person step names a card its installer's CARDS table does not hold
  settings    a key, secret key or job key is not in the settings example
  jobs        a job row lacks a field (its command, schedule and interval key, account, log
              shape, switch or reason), or names a <KEY> its part's settings do not hold
  credentials a stored key that ends has no `clock` or no `date_from`, or one key is described two
              ways (two ends, clocks or places for a date), or two keys share one clock (KIT-667)
  docs        a guide, lesson or runbook is null with no reason, or has a reason and a path
  words       a plain field names a deployment, a company or a third-party product, or a
              part's plain name is an internal one
  page        docs/PARTS.md differs from what `render` writes

It also prints, by name, every part that has no guide yet. That is a report, not a failure:
the reason in the list is what keeps CI green.

It changes nothing outside this repository, needs no terminal, and asks nothing. Standard
library only.

Exit: 0 the list and the page agree with the kit · 1 a problem is named · 2 the list or the
      schema could not be read
"""
import argparse
import ast
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import jsonschema_mini as jsm  # noqa: E402

EX_OK, EX_PROBLEM, EX_USAGE = 0, 1, 2

PARTS_FILE = "pipeline-parts.json"
SCHEMA_FILE = os.path.join("schemas", "pipeline-parts.schema.json")
PAGE_FILE = os.path.join("docs", "PARTS.md")
MARKER = "pipeline-parts/1"

# Installers that are not named pipeline_*_setup.py. Each must belong to one part.
NAMED_INSTALLERS = (
    "scripts/pipeline_install.py",
    "scripts/pipeline_watch.py",
    "scripts/pipeline_update.py",
    "scripts/sync_user_skills.py",
    "scripts/pipeline_stage_e_app.py",
)

# The only arguments a check or apply may carry. The one command will run these with the
# owner's password session, so the list is closed: a new word is a reviewed change here.
ALLOWED_ARGS = frozenset({
    "run", "verify", "status", "check", "install", "review", "update", "dispatcher", "merge",
    "push", "--dry-run", "--apply", "--conf", "--json",
    "stage-e.conf", "stage-a.conf", "notifier.conf", "bridge.conf", "chat-lane.conf",
    "conflict-waker.conf", "queue.conf", "sweep.conf", "watcher.conf", "update.conf",
    "acceptance.conf",
})

# (earlier, later): the later part must need the earlier one, directly or through others.
ORDER_RULES = (
    ("lane-marker", "review"),
    ("review", "idea-gate"),
    ("tech-lead", "paging"),
    ("paging", "health-check"),
)

# Words no plain field may hold: a deployment, a company or a third-party product.
BANNED_WORDS = ("linear", "slack", "github", "cloudflare", "cyrus", "caddy", "braedenclaw",
                "clawdispatch", "anthropic", "atlassian", "jira", "bitbucket", "zulip")
# Words a part's plain name may not hold: what the code calls things, not what they do.
INTERNAL_WORDS = ("bridge", "stage e", "stage a", "stage-e", "stage-a", "poller", "daemon",
                  "launchd", "plist", "notifier", "dispatcher")

DOC_KINDS = ("guide", "lesson", "runbook")


# --------------------------------------------------------------------------- reading

def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load(root):
    """The parts list and its schema, or raise OSError / ValueError."""
    doc = json.loads(read_text(os.path.join(root, PARTS_FILE)))
    schema = json.loads(read_text(os.path.join(root, SCHEMA_FILE)))
    return doc, schema


def parts_of(doc):
    return [p for p in doc.get("parts") or [] if isinstance(p, dict)]


# --------------------------------------------------------------------------- the rules

def rule_schema(doc, schema):
    return ["schema: %s %s" % (e["path"] or "<root>", e["message"]) for e in jsm.validate(doc, schema)]


def rule_ids(parts):
    seen, found = set(), []
    for part in parts:
        if part["id"] in seen:
            found.append("ids: %r is the id of more than one part" % part["id"])
        seen.add(part["id"])
    return found


def cycle_in(parts):
    """One cycle among the needs, as a list of ids, or None."""
    needs = {p["id"]: [n for n in p["needs"] if n != p["id"]] for p in parts}
    state, stack = {}, []

    def visit(pid):
        state[pid] = "open"
        stack.append(pid)
        for nxt in needs.get(pid, []):
            if nxt not in needs:
                continue
            if state.get(nxt) == "open":
                return stack[stack.index(nxt):] + [nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[pid] = "done"
        return None

    for part in parts:
        if part["id"] not in state:
            found = visit(part["id"])
            if found:
                return found
    return None


def rule_needs(parts):
    ids = {p["id"] for p in parts}
    found = []
    for part in parts:
        for need in part["needs"]:
            if need == part["id"]:
                found.append("needs: %s needs itself" % part["id"])
            elif need not in ids:
                found.append("needs: %s needs %r, which is no part" % (part["id"], need))
    loop = cycle_in(parts)
    if loop:
        found.append("needs: a cycle: %s" % " -> ".join(loop))
    return found


def order(parts):
    """The parts in install order: each after everything it needs, otherwise as listed.
    Raises ValueError on a cycle, so no caller ever gets an order a cycle made up."""
    loop = cycle_in(parts)
    if loop:
        raise ValueError("a cycle: %s" % " -> ".join(loop))
    by_id = {p["id"]: p for p in parts}
    done, out = set(), []

    def place(part):
        if part["id"] in done:
            return
        done.add(part["id"])
        for need in part["needs"]:
            if need in by_id:
                place(by_id[need])
        out.append(part)

    for part in parts:
        place(part)
    return out


def _needs_closure(parts, pid):
    by_id = {p["id"]: p for p in parts}
    seen, todo = set(), list(by_id.get(pid, {}).get("needs", []))
    while todo:
        nxt = todo.pop()
        if nxt in seen or nxt not in by_id:
            continue
        seen.add(nxt)
        todo.extend(by_id[nxt]["needs"])
    return seen


def rule_order(parts, rules=ORDER_RULES):
    ids = {p["id"] for p in parts}
    found = []
    for earlier, later in rules:
        gone = [pid for pid in (earlier, later) if pid not in ids]
        if gone:
            found.append("order: the install order needs a part %s, and the list has none"
                         % " and ".join(repr(g) for g in gone))
        elif earlier not in _needs_closure(parts, later):
            found.append("order: %s must come after %s, so it must need it (directly or "
                         "through another part)" % (later, earlier))
    return found


# What marks a script that writes a launchd job: a job folder or the plist library, and a job key. Spelt in pieces,
# so this file, which only reads for them, is not judged a writer itself.
JOB_DIRS = ("Launch" + "Daemons", "Launch" + "Agents", "plist" + "lib")
JOB_KEYS = re.compile("|".join(("Program" + "Arguments", "Start" + "Interval", "Keep" + "Alive")))


def job_writers(root):
    """Every kit script that writes a launchd job file, judged by its text."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, "scripts", "*.py"))):
        text = read_text(path)
        if any(d in text for d in JOB_DIRS) and JOB_KEYS.search(text):
            out.append("scripts/" + os.path.basename(path))
    return out


def rule_owners(parts, root, named=NAMED_INSTALLERS):
    owner, found = {}, []
    for part in parts:
        for script in part["scripts"]:
            if script in owner:
                found.append("owners: %s belongs to both %s and %s" % (script, owner[script], part["id"]))
            owner.setdefault(script, part["id"])
    installers = ["scripts/" + os.path.basename(p)
                  for p in sorted(glob.glob(os.path.join(root, "scripts", "pipeline_*_setup.py")))]
    for script in installers + list(named):
        if script not in owner:
            found.append("owners: the installer %s belongs to no part" % script)
    for script in job_writers(root):
        if script not in owner:
            found.append("owners: %s writes a job file and belongs to no part" % script)
    return found


def _named_paths(part):
    paths = list(part["scripts"])
    if part["installer"]:
        paths.append(part["installer"])
    for key in ("check", "apply"):
        if part[key]:
            paths.append(part[key]["script"])
    if part["settings"]["example"]:
        paths.append(part["settings"]["example"])
    for step in part["person_steps"]:
        if step["card_in"]:
            paths.append(step["card_in"])
    for kind in DOC_KINDS:
        if part["docs"][kind]:
            paths.append(part["docs"][kind])
    return paths


def _inside(root, rel):
    real_root = os.path.realpath(root)
    real = os.path.realpath(os.path.join(root, rel))
    return real == real_root or real.startswith(real_root + os.sep)


def rule_paths(parts, root):
    found = []
    for part in parts:
        for rel in _named_paths(part):
            if ".." in rel.split("/") or not _inside(root, rel):
                found.append("paths: %s names %s, outside the kit" % (part["id"], rel))
            elif not os.path.isfile(os.path.join(root, rel)):
                found.append("paths: %s names %s, which does not exist" % (part["id"], rel))
    return found


def rule_commands(parts, root):
    found = []
    for part in parts:
        for key in ("check", "apply"):
            cmd = part[key]
            if not cmd:
                continue
            script = cmd["script"]
            if not re.match(r"^scripts/[a-z0-9_]+\.py$", script):
                found.append("commands: %s's %s names %r, not a kit script" % (part["id"], key, script))
                continue
            path = os.path.join(root, script)
            text = read_text(path) if os.path.isfile(path) else ""
            for arg in cmd["args"]:
                if arg not in ALLOWED_ARGS:
                    found.append("commands: %s's %s passes %r, which is not on the fixed list"
                                 % (part["id"], key, arg))
                elif not arg.endswith(".conf") and ('"%s"' % arg) not in text and ("'%s'" % arg) not in text:
                    found.append("commands: %s's %s passes %r, which %s does not hold"
                                 % (part["id"], key, arg, script))
    return found


def rule_reasons(parts):
    found = []
    for part in parts:
        none = [k for k in ("installer", "check", "apply") if not part[k]]
        if none and not part["no_command_reason"]:
            found.append("reasons: %s has no %s and no reason" % (part["id"], " or ".join(none)))
        if not none and part["no_command_reason"]:
            found.append("reasons: %s gives a reason, but has an installer, a check and an apply"
                         % part["id"])
    return found


def cards_in(path):
    """The keys of a script's top-level CARDS table, read with ast; never imported."""
    tree = ast.parse(read_text(path))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == "CARDS" for t in node.targets)):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return set()


def rule_cards(parts, root):
    found = []
    for part in parts:
        for step in part["person_steps"]:
            card, where = step["card"], step["card_in"]
            if bool(card) != bool(where):
                found.append("cards: %s has a person step with %s" % (
                    part["id"], "a card and no installer to find it in" if card else
                    "an installer and no card"))
                continue
            if not card:
                continue
            path = os.path.join(root, where)
            try:
                known = cards_in(path)
            except (OSError, SyntaxError, ValueError):
                known = set()
            if card not in known:
                found.append("cards: %s names card %s, which %s's CARDS table does not hold"
                             % (part["id"], card, where))
    return found


def _has_key(text, key):
    return bool(re.search(r"(?m)^#?\s*%s=" % re.escape(key), text)) or ('"%s"' % key) in text


def rule_settings(parts, root):
    found = []
    for part in parts:
        example = part["settings"]["example"]
        keys = list(part["settings"]["keys"])
        keys += [s["key"] for s in part["secrets"]]
        keys += [j["from_key"] for j in part["jobs"] if j["from_key"]]
        if not example:
            if keys:
                found.append("settings: %s names keys but no example file to find them in" % part["id"])
            continue
        path = os.path.join(root, example)
        text = read_text(path) if os.path.isfile(path) else ""
        for key in keys:
            if not _has_key(text, key):
                found.append("settings: %s names key %s, which %s does not hold"
                             % (part["id"], key, example))
        for secret in part["secrets"]:
            name = secret["default_name"]
            if name and not re.search(r"\b%s\b" % re.escape(name), text):
                found.append("settings: %s names the secret %s, which %s does not hold"
                             % (part["id"], name, example))
    return found


def rule_credentials(parts):
    """What each stored key's end date is, and that it is said once (KIT-667). A key that ends needs
    the short name the health check files its date under and a line saying where to read the date;
    the same key in two parts is described the same way in both."""
    found, by_name, by_clock = [], {}, {}
    for part in parts:
        for secret in part["secrets"]:
            who = "%s's secret %s" % (part["id"], secret["key"])
            if secret["ends"] != "never":
                for field in ("clock", "date_from"):
                    if not secret.get(field):
                        found.append("credentials: %s ends (%s) but has no %s" % (who, secret["ends"], field))
            facts = (secret["ends"], secret.get("clock"), secret.get("date_from"))
            name = secret["default_name"]
            if name:
                by_name.setdefault(name, set()).add(facts)
            if secret.get("clock"):
                by_clock.setdefault(secret["clock"], set()).add((name,) + facts)
    for name, kinds in sorted(by_name.items()):
        if len(kinds) > 1:
            found.append("credentials: the key %s is described %d ways; say its end once, the same in every part"
                         % (name, len(kinds)))
    for clock, kinds in sorted(by_clock.items()):
        if len({k[0] for k in kinds}) > 1:
            found.append("credentials: the clock %s names more than one key" % clock)
    return found


def rule_docs(parts):
    found = []
    for part in parts:
        missing = part["docs"]["missing"]
        for kind in DOC_KINDS:
            if not part["docs"][kind] and not missing.get(kind):
                found.append("docs: %s has no %s and no reason" % (part["id"], kind))
            if part["docs"][kind] and missing.get(kind):
                found.append("docs: %s has a %s and a reason it is missing" % (part["id"], kind))
    return found


# Placeholders a job row may hold that are no settings key: where the code itself decides.
JOB_PLACEHOLDERS = frozenset({"role home", "owner home", "kit", "python", "claude", "conf file"})
_PLACEHOLDER = re.compile(r"<([^<>]+)>")


def rule_jobs(parts, root):
    """Each job is data the supervisor can run: every field present (the schema), a schedule
    with its key, a switch or a reason, and every <KEY> in its command or log a key of the
    part's settings, so no path is written down where the code reads it from a setting."""
    found = []
    for part in parts:
        example = part["settings"]["example"]
        path = os.path.join(root, example) if example else ""
        text = read_text(path) if path and os.path.isfile(path) else ""
        for job in part["jobs"]:
            where = "jobs: %s's job %s" % (part["id"], job["label"])
            if (job["schedule"] == "interval") != bool(job["interval_key"]):
                found.append("%s: an interval schedule needs its interval_key, and only it has one" % where)
            if job["interval_key"] and not _has_key(text, job["interval_key"]):
                found.append("%s: its interval key %s is not in %s" % (where, job["interval_key"], example))
            if not job["switch"] and not job["switch_reason"]:
                found.append("%s: no switch and no reason" % where)
            if job["switch"] and job["switch_reason"]:
                found.append("%s: a switch and a reason it has none" % where)
            if job["switch"]:
                spath = os.path.join(root, job["switch"]["example"])
                stext = read_text(spath) if os.path.isfile(spath) else ""
                if not _has_key(stext, job["switch"]["key"]):
                    found.append("%s: its switch %s is not in %s"
                                 % (where, job["switch"]["key"], job["switch"]["example"]))
            for value in [job["command"]["program"], job["log"]] + job["command"]["args"]:
                for name in _PLACEHOLDER.findall(value):
                    key = name[len("dir of "):] if name.startswith("dir of ") else name
                    if key in JOB_PLACEHOLDERS:
                        continue
                    if not re.match(r"^[A-Z][A-Z0-9_]*$", key) or not _has_key(text, key):
                        found.append("%s: <%s> is neither a settings key of %s nor a known placeholder"
                                     % (where, name, example))
    return found


def _plain_fields(part):
    yield "name", part["name"]
    yield "internal_name", part["internal_name"]
    yield "does", part["does"]
    if part["no_command_reason"]:
        yield "no_command_reason", part["no_command_reason"]
    for line in part["restarts"]:
        yield "restarts", line
    for line in part["consent"]:
        yield "consent", line
    for step in part["person_steps"]:
        yield "person_steps", step["words"]
    for secret in part["secrets"]:
        yield "secrets", secret["what"]
        if secret.get("date_from"):
            yield "secrets.date_from", secret["date_from"]
    for kind, why in sorted(part["docs"]["missing"].items()):
        yield "docs.missing." + kind, why
    if part["settings"]["file"]:
        yield "settings.file", part["settings"]["file"]
    for job in part["jobs"]:
        yield "jobs.label", job["label"]
        for value in [job["command"]["program"], job["log"], job["switch_reason"] or ""] + job["command"]["args"]:
            yield "jobs", _PLACEHOLDER.sub("", value)   # a <KEY> is the code's own identifier


def rule_words(parts):
    found = []
    banned = re.compile(r"(?<![a-z0-9])(%s)(?![a-z0-9])" % "|".join(BANNED_WORDS), re.I)
    internal = re.compile(r"(?<![a-z0-9])(%s)(?![a-z0-9])" % "|".join(
        re.escape(w) for w in INTERNAL_WORDS), re.I)
    for part in parts:
        for field, text in _plain_fields(part):
            hit = banned.search(text)
            if hit:
                found.append("words: %s's %s names %r; use a plain word for it"
                             % (part["id"], field, hit.group(1)))
        hit = internal.search(part["name"])
        if hit:
            found.append("words: %s's plain name holds %r, an internal name; say what it does"
                         % (part["id"], hit.group(1)))
    return found


# --------------------------------------------------------------------------- the page

STATE_WORDS = {"live": "live", "built-off": "built, off by default", "designed": "designed only"}


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _command(cmd):
    if not cmd:
        return "none"
    return "`python3 %s`" % " ".join([cmd["script"]] + cmd["args"])


def render(doc):
    """docs/PARTS.md, from the list alone. Same list, same page, byte for byte."""
    parts = order(parts_of(doc))
    names = {p["id"]: p["name"] for p in parts}
    out = [
        "# Every part of the pipeline",
        "",
        "*Written by `python3 scripts/pipeline_parts.py render` from `pipeline-parts.json`. "
        "Do not edit this page by hand: CI fails when it differs from the list.*",
        "",
        "Each part once, in install order: what it does for you, its state in the kit, what it "
        "needs first, and where its guide is. A part comes after every part it needs.",
        "",
        "## The parts",
        "",
        "| Part | What it does | State | Needs first | Check | Apply | Guide |",
        "|---|---|---|---|---|---|---|",
    ]
    for part in parts:
        guide = part["docs"]["guide"]
        out.append("| **%s** (`%s`) | %s | %s | %s | %s | %s | %s |" % (
            _cell(part["name"]), part["id"], _cell(part["does"]), STATE_WORDS[part["state"]],
            _cell(", ".join(names.get(n, n) for n in part["needs"]) or "nothing"),
            _command(part["check"]), _command(part["apply"]),
            "[%s](%s)" % (guide, os.path.relpath(guide, "docs")) if guide else "none yet"))
    out += ["", "## Documents still to write", ""]
    for kind in DOC_KINDS:
        lacking = [p for p in parts if not p["docs"][kind]]
        out.append("### No %s yet (%d)" % (kind, len(lacking)))
        out.append("")
        if not lacking:
            out.append("Every part has one.")
        for part in lacking:
            out.append("- **%s** (`%s`): %s" % (part["name"], part["id"],
                                               part["docs"]["missing"].get(kind, "")))
        out.append("")
    jobs = [(p, j) for p in parts for j in p["jobs"]]
    out += [
        "## The jobs (%d)" % len(jobs),
        "",
        "Each background job, as the installer writes it today. `<KEY>` is that setting's value; "
        "`<dir of KEY>` is its folder.",
        "",
        "| Job | Part | Runs | As | Command | Log | Switched by |",
        "|---|---|---|---|---|---|---|",
    ]
    runs = {"always-on": "always on", "at-boot": "at boot"}
    for part, job in jobs:
        switch = ("`%s` in `%s`" % (job["switch"]["key"], job["switch"]["example"]) if job["switch"]
                  else _cell(job["switch_reason"]))
        out.append("| `%s` | %s | %s | %s | `%s` | `%s` | %s |" % (
            _cell(job["label"]), part["id"],
            "every `%s` seconds" % job["interval_key"] if job["schedule"] == "interval" else runs[job["schedule"]],
            job["account"], _cell(" ".join([job["command"]["program"]] + job["command"]["args"])).replace("`", "'"),
            _cell(job["log"]), switch))
    out += [
        "",
        "## What only a person does",
        "",
        "Each step is marked: *only a person* can do it, or it is *a sign-off a machine could fake*.",
        "",
    ]
    for part in parts:
        for step in part["person_steps"]:
            card = " (card %s)" % step["card"] if step["card"] else ""
            kind = "only a person" if step["kind"] == "only-a-person" else "a sign-off a machine could fake"
            out.append("- **%s**%s: %s *%s.*" % (part["name"], card, step["words"], kind))
    out += [
        "",
        "## What is not proven",
        "",
        "- The list was checked against the kit's code, not against a running machine. No ticket yet.",
        "- No part has a first-pass ceiling yet: every `ceiling` is empty. No ticket yet.",
        "",
    ]
    return "\n".join(out)


def rule_page(doc, root):
    path = os.path.join(root, PAGE_FILE)
    try:
        committed = read_text(path)
    except OSError:
        return ["page: %s is missing; run `python3 scripts/pipeline_parts.py render`" % PAGE_FILE]
    if committed != render(doc):
        return ["page: %s differs from what render writes; run "
                "`python3 scripts/pipeline_parts.py render` and commit it" % PAGE_FILE]
    return []


def problems(root, rules=ORDER_RULES, named=NAMED_INSTALLERS):
    """Every problem with the list and the page, in rule order. Raises on an unreadable file."""
    doc, schema = load(root)
    found = rule_schema(doc, schema)
    if found:
        return found
    parts = parts_of(doc)
    for rule in (rule_ids(parts), rule_needs(parts), rule_order(parts, rules),
                 rule_owners(parts, root, named), rule_paths(parts, root),
                 rule_commands(parts, root), rule_reasons(parts), rule_cards(parts, root),
                 rule_settings(parts, root), rule_jobs(parts, root), rule_credentials(parts),
                 rule_docs(parts), rule_words(parts)):
        found += rule
    if not found:
        found += rule_page(doc, root)
    return found


def no_guide(doc):
    return [p for p in parts_of(doc) if not (p.get("docs") or {}).get("guide")]


# --------------------------------------------------------------------------- commands

def cmd_check(root):
    try:
        found = problems(root)
        doc, _schema = load(root)
    except (OSError, ValueError) as exc:
        print("pipeline_parts: could not read the list or its schema: %s" % exc)
        return EX_USAGE
    for line in found:
        print("  problem  %s" % line)
    for part in no_guide(doc):
        print("  no guide  %s (%s): %s" % (part["name"], part["id"],
                                          (part["docs"].get("missing") or {}).get("guide", "")))
    print("%s  %s: %d part(s), %d problem(s), %d without a guide"
          % ("FAIL" if found else "OK  ", PARTS_FILE, len(parts_of(doc)), len(found),
             len(no_guide(doc))))
    return EX_PROBLEM if found else EX_OK


def cmd_render(root, to_stdout):
    try:
        doc, schema = load(root)
    except (OSError, ValueError) as exc:
        print("pipeline_parts: could not read the list or its schema: %s" % exc)
        return EX_USAGE
    bad = rule_schema(doc, schema)
    if bad:
        for line in bad:
            print("  problem  %s" % line)
        print("FAIL  the list does not match its schema; nothing was written")
        return EX_PROBLEM
    try:
        page = render(doc)
    except ValueError as exc:
        print("FAIL  the parts have no install order (%s); nothing was written" % exc)
        return EX_PROBLEM
    if to_stdout:
        sys.stdout.write(page)
        return EX_OK
    with open(os.path.join(root, PAGE_FILE), "w", encoding="utf-8") as fh:
        fh.write(page)
    print("wrote %s (%d parts)" % (PAGE_FILE, len(parts_of(doc))))
    return EX_OK


# --------------------------------------------------------------------------- selftest

FIXTURE_SETUP = '''CARDS = {
    "CK-A1": {"title": "Make the app"},
}
choices = ("run", "verify")
flags = ("--dry-run", "--conf", "--force")   # --force is here, so only the fixed list refuses it
'''
# Spelt in pieces, so this file is not itself judged a job writer.
FIXTURE_WRITER = 'P = "/Library/Launch' + 'Daemons/x.plist"; K = "<key>Start' + 'Interval</key>"\n'
FIXTURE_CONF = "# example\nJOB_LABEL=com.example.alpha\nTOKEN_ENV=ALPHA_TOKEN\n#INTERVAL=60\n"


def _fixture_part(pid, **over):
    part = {
        "id": pid, "name": "Something it does for you", "internal_name": "the %s job" % pid,
        "does": "It does one thing for you.", "scope": "machine", "state": "live",
        "needs": [], "scripts": [], "installer": None, "check": None, "apply": None,
        "no_command_reason": "Nothing to install.", "restarts": [],
        "settings": {"file": None, "example": None, "keys": []}, "secrets": [], "jobs": [],
        "person_steps": [], "ceiling": None, "consent": [],
        "docs": {"guide": "docs/A.md", "lesson": None, "runbook": None,
                 "missing": {"lesson": "None yet.", "runbook": "None yet."}},
    }
    part.update(over)
    return part


def _fixture_doc():
    alpha = _fixture_part(
        "alpha", scripts=["scripts/pipeline_alpha_setup.py", "scripts/pipeline_writer.py"],
        installer="scripts/pipeline_alpha_setup.py",
        check={"script": "scripts/pipeline_alpha_setup.py", "args": ["run", "--dry-run", "--conf", "stage-e.conf"]},
        apply={"script": "scripts/pipeline_alpha_setup.py", "args": ["run", "--conf", "stage-e.conf"]},
        no_command_reason=None,
        settings={"file": "alpha.conf", "example": "alpha.conf.example", "keys": ["JOB_LABEL", "INTERVAL"]},
        secrets=[{"key": "TOKEN_ENV", "default_name": "ALPHA_TOKEN", "what": "the alpha token",
                  "ends": "one-year", "clock": "alpha-token", "date_from": "The day you made it, plus one year."}],
        jobs=[{"label": "com.example.alpha", "from_key": "JOB_LABEL", "account": "role", "loaded_by": "person",
               "command": {"program": "/bin/sh", "args": ["-c", "exec /usr/bin/python3 <role home>/alpha.py run"]},
               "schedule": "interval", "interval_key": "INTERVAL", "log": "<dir of JOB_LABEL>/alpha.log",
               "switch": {"key": "TOKEN_ENV", "example": "alpha.conf.example"}, "switch_reason": None}],
        person_steps=[{"card": "CK-A1", "card_in": "scripts/pipeline_alpha_setup.py",
                       "kind": "only-a-person", "words": "Make the app in its own settings page."}])
    beta = _fixture_part("beta", needs=["alpha"], scripts=list(NAMED_INSTALLERS))
    return {"schema": MARKER, "parts": [alpha, beta]}


def _write_fixture(root, doc):
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(root, "schemas"), exist_ok=True)
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    files = {"scripts/pipeline_alpha_setup.py": FIXTURE_SETUP,
             "scripts/pipeline_writer.py": FIXTURE_WRITER,
             "alpha.conf.example": FIXTURE_CONF, "docs/A.md": "# A\n"}
    for name in NAMED_INSTALLERS:
        files[name] = "# installer\n"
    for rel, text in files.items():
        with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(root, SCHEMA_FILE), "w", encoding="utf-8") as fh:
        fh.write(read_text(os.path.join(KIT, SCHEMA_FILE)))
    _write_doc(root, doc)
    with open(os.path.join(root, PAGE_FILE), "w", encoding="utf-8") as fh:
        fh.write(render(doc))


def _write_doc(root, doc):
    with open(os.path.join(root, PARTS_FILE), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)


def selftest():
    import copy
    import tempfile

    failures = []
    rules = (("alpha", "beta"),)

    def expect(name, got, want):
        ok = got == want
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            failures.append(name)

    root = tempfile.mkdtemp(prefix="pipeline-parts-")
    good = _fixture_doc()
    _write_fixture(root, good)

    def run(mutate, page_too=True):
        doc = copy.deepcopy(good)
        mutate(doc)
        _write_doc(root, doc)
        if page_too:
            try:
                page = render(doc)
            except (ValueError, KeyError, TypeError):   # a cycle or a bad shape has no page
                page = ""
            with open(os.path.join(root, PAGE_FILE), "w", encoding="utf-8") as fh:
                fh.write(page)
        try:
            return problems(root, rules=rules)
        finally:
            _write_doc(root, good)
            with open(os.path.join(root, PAGE_FILE), "w", encoding="utf-8") as fh:
                fh.write(render(good))

    def fails(name, mutate, needle, page_too=True):
        found = run(mutate, page_too)
        expect(name, any(needle in line for line in found), True)

    a = lambda d: d["parts"][0]  # noqa: E731
    b = lambda d: d["parts"][1]  # noqa: E731

    # The good fixture passes every rule: each failing case below differs from it in one thing.
    expect("a good list has no problem", problems(root, rules=rules), [])

    # schema
    fails("schema: a part missing a field fails", lambda d: a(d).pop("ceiling"), "schema:")
    fails("schema: a state outside the three fails", lambda d: a(d).update(state="maybe"), "schema:")
    fails("schema: a secret with a value-shaped name fails",
          lambda d: a(d)["secrets"][0].update(default_name="ghp_abc123"), "schema:")
    fails("schema: a person step of an unknown kind fails",
          lambda d: a(d)["person_steps"][0].update(kind="whoever"), "schema:")
    # ids
    fails("ids: two parts with one id fail", lambda d: b(d).update(id="alpha", needs=[]), "ids:")
    # needs
    fails("needs: an unknown part fails", lambda d: b(d).update(needs=["gamma"]), "needs:")
    fails("needs: a part that needs itself fails", lambda d: a(d).update(needs=["alpha"]), "needs:")
    fails("needs: a cycle fails", lambda d: a(d).update(needs=["beta"]), "cycle")
    # order
    fails("order: a later part that does not need the earlier fails",
          lambda d: b(d).update(needs=[]), "order:")
    fails("order: a part the rules name is gone", lambda d: d["parts"].pop(1), "order:")
    # owners
    fails("owners: removing an installer's part fails",
          lambda d: a(d).update(scripts=["scripts/pipeline_writer.py"]), "owners:")
    fails("owners: removing a named installer fails",
          lambda d: b(d).update(scripts=list(NAMED_INSTALLERS[1:])), "owners:")
    fails("owners: a job writer no part owns fails",
          lambda d: a(d).update(scripts=["scripts/pipeline_alpha_setup.py"]), "owners:")
    fails("owners: one script in two parts fails",
          lambda d: b(d)["scripts"].append("scripts/pipeline_writer.py"), "owners:")
    # paths
    fails("paths: a script that does not exist fails",
          lambda d: b(d)["scripts"].append("scripts/pipeline_gone.py"), "paths:")
    fails("paths: a guide that does not exist fails",
          lambda d: b(d)["docs"].update(guide="docs/GONE.md"), "paths:")
    # commands
    fails("commands: an argument outside the fixed list fails",
          lambda d: a(d)["apply"]["args"].append("--force"), "commands:")
    fails("commands: a word the script does not hold fails",
          lambda d: a(d)["check"].update(args=["status"]), "commands:")
    fails("commands: a script outside the kit's folder is refused",
          lambda d: a(d)["check"].update(script="scripts/../evil.py"), "schema:")
    # reasons
    fails("reasons: a null apply with no reason fails",
          lambda d: a(d).update(apply=None), "reasons:")
    fails("reasons: a stale reason beside all three commands fails",
          lambda d: a(d).update(no_command_reason="None."), "reasons:")
    # cards
    fails("cards: a made-up card fails",
          lambda d: a(d)["person_steps"][0].update(card="CK-A9"), "cards:")
    fails("cards: a card with no installer to look in fails",
          lambda d: a(d)["person_steps"][0].update(card_in=None), "cards:")
    # settings
    fails("settings: a key the example lacks fails",
          lambda d: a(d)["settings"]["keys"].append("NOT_THERE"), "settings:")
    fails("settings: a secret key the example lacks fails",
          lambda d: a(d)["secrets"][0].update(key="OTHER_ENV"), "settings:")
    fails("settings: a secret's default name the example lacks fails",
          lambda d: a(d)["secrets"][0].update(default_name="OTHER_TOKEN"), "settings:")
    fails("settings: a job key the example lacks fails",
          lambda d: a(d)["jobs"][0].update(from_key="NO_LABEL"), "settings:")
    # credentials
    fails("schema: a secret with no ends fails", lambda d: a(d)["secrets"][0].pop("ends"), "schema:")
    fails("schema: a secret ending some other way fails",
          lambda d: a(d)["secrets"][0].update(ends="soon"), "schema:")
    fails("schema: a clock that is not an entry name fails",
          lambda d: a(d)["secrets"][0].update(clock="Alpha_Token"), "schema:")
    fails("credentials: a key that ends with no clock fails",
          lambda d: a(d)["secrets"][0].pop("clock"), "credentials:")
    fails("credentials: a key that ends with no place to read the date fails",
          lambda d: a(d)["secrets"][0].pop("date_from"), "credentials:")
    fails("credentials: an unknown end still needs a clock",
          lambda d: (a(d)["secrets"][0].update(ends="unknown"), a(d)["secrets"][0].pop("clock")), "credentials:")

    def never(d):
        a(d)["secrets"][0].update(ends="never")
        a(d)["secrets"][0].pop("clock")
        a(d)["secrets"][0].pop("date_from")

    def twice(d, **over):
        twin = dict(a(d)["secrets"][0], **over)
        b(d).update(settings=dict(a(d)["settings"], keys=[]), secrets=[twin])

    fails("credentials: the same key described two ways fails",
          lambda d: twice(d, ends="set-when-made"), "credentials:")
    fails("credentials: the same key with two places for its date fails",
          lambda d: twice(d, date_from="Somewhere else."), "credentials:")
    fails("credentials: two keys on one clock fail",
          lambda d: twice(d, default_name="OTHER_TOKEN"), "credentials:")
    expect("credentials: a key that never ends needs no clock", run(never), [])
    expect("credentials: the same key described the same way twice passes",
           run(lambda d: twice(d)), [])
    # jobs: each job is a full row of data
    fails("jobs: a job row missing its command fails", lambda d: a(d)["jobs"][0].pop("command"), "schema:")
    fails("jobs: a job row missing its log fails", lambda d: a(d)["jobs"][0].pop("log"), "schema:")
    fails("jobs: an interval with no key fails",
          lambda d: a(d)["jobs"][0].update(interval_key=None), "jobs:")
    fails("jobs: no switch and no reason fails", lambda d: a(d)["jobs"][0].update(switch=None), "jobs:")
    fails("jobs: a path written down where the code reads a key fails",
          lambda d: a(d)["jobs"][0].update(log="<dir of NOT_A_KEY>/alpha.log"), "jobs:")
    found = run(lambda d: a(d)["jobs"][0].update(schedule="always-on", interval_key=None, switch=None,
                                                  switch_reason="A person unloads it."))
    expect("jobs: an always-on job with a reason for no switch passes", found, [])
    # docs
    fails("docs: a missing guide with no reason fails",
          lambda d: b(d)["docs"].update(guide=None), "docs:")
    fails("docs: a reason beside a guide fails",
          lambda d: b(d)["docs"]["missing"].update(guide="None yet."), "docs:")
    found = run(lambda d: b(d)["docs"].update(guide=None, missing={"guide": "Not written yet.",
                                                                   "lesson": "x", "runbook": "x"}))
    expect("docs: a missing guide with a reason passes", found, [])
    # words
    fails("words: a company name in a plain field fails",
          lambda d: a(d).update(does="It posts to Slack."), "words:")
    fails("words: a deployment name fails",
          lambda d: a(d)["consent"].append("Installs com.braedenclaw jobs."), "words:")
    fails("words: a deployment name in a job label fails",
          lambda d: a(d)["jobs"][0].update(label="com." + "braeden" + "claw.alpha"), "words:")
    fails("words: a product name in a settings file name fails",
          lambda d: a(d)["settings"].update(file="cy" + "rus.conf"), "words:")
    fails("words: a product name in a secret's date_from fails",
          lambda d: a(d)["secrets"][0].update(date_from="Read it on the " + "git" + "hub page."), "words:")
    fails("words: an internal name as the plain name fails",
          lambda d: a(d).update(name="The bridge"), "words:")
    # page
    fails("page: a page that differs from render fails",
          lambda d: a(d).update(does="It does another thing."), "page:", page_too=False)
    page = render(good)
    expect("page: render names every part", all(p["name"] in page for p in good["parts"]), True)
    expect("page: render lists a part with no lesson", "No lesson yet" in page and "(`beta`)" in page, True)
    expect("page: render is the same twice", render(good), page)

    # The kit's own list passes, and owns every installer that exists today.
    try:
        found = problems(KIT)
    except (OSError, ValueError) as exc:
        found = ["could not read: %s" % exc]
    for line in found:
        print("    kit: %s" % line)
    expect("the kit's own list and page pass every rule", found, [])
    # Removing the row of any installer the kit ships today fails: each one, in turn.
    try:
        kit_parts = parts_of(load(KIT)[0])
    except (OSError, ValueError):
        kit_parts = []
    installers = sorted({s for p in kit_parts for s in p["scripts"]
                         if s in NAMED_INSTALLERS or re.match(r"^scripts/pipeline_\w+_setup\.py$", s)})
    caught = [s for s in installers
              if any(s in line for line in rule_owners([p for p in kit_parts if s not in p["scripts"]], KIT))]
    expect("removing any installer's row from the kit's list fails (%d installers)" % len(installers),
           (len(installers) >= 15, caught), (True, installers))

    print("%s: %d failure(s)" % ("FAIL" if failures else "OK", len(failures)))
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Every part of the pipeline, listed once.")
    parser.add_argument("command", nargs="?", choices=("check", "render"))
    parser.add_argument("--stdout", action="store_true", help="render: print the page instead")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "pipeline-parts-selftest-run-")
    if args.command == "check":
        return cmd_check(KIT)
    if args.command == "render":
        return cmd_render(KIT, args.stdout)
    parser.print_usage()
    return EX_USAGE


if __name__ == "__main__":
    sys.exit(main())
