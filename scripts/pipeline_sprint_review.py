#!/usr/bin/env python3
"""The sprint review: a recap of the week, built by code, written as one Linear document and linked from one DM
message on the sprint's last working day (KIT-576).

    python3 scripts/pipeline_sprint_review.py period [--settings FILE]
    python3 scripts/pipeline_sprint_review.py --example-config
    python3 scripts/pipeline_sprint_review.py --selftest

A sprint is the plan's week (scripts/pipeline_sprint.py). On its last working day, once the afternoon stand-up's time
has come, the stand-up calls this module. It reads four things and no model:
  - the usage file (contract section 17): what the week spent, by kind of work, and the dearest pieces;
  - GitHub: the pull requests merged in the sprint's period, which merged on their own (auto-merge was on), and what
    Stage E's review comment on each said (how many findings, of what kind);
  - the weekly budget the owner set, for the cost against the plan;
  - the health watch's state file (optional, KIT-602): the week's lowest free memory and most swap, for the machine and
    the budget section, which also draws the burn against the reset from the usage file's days.
It then writes the recap as a Linear document and posts ONE message to the owner's DM that links it.

NEVER SILENT.  If the document cannot be written, the message says so and names a copy saved on this machine. If the
usage file cannot place the sprint, or a figure cannot be read, the recap says so in that place and shows no number.
Nothing is guessed: a count is shown only for pages that were read in full, and a page limit that was hit is said.

ONCE A SPRINT.  A record file holds each sprint reviewed (by the usage file's week start) with when and where, written
BEFORE the message is posted, so a review is never sent twice and one that cannot be recorded is not sent.

PROPOSES, NEVER APPLIES.  The review changes nothing: it files no ticket, labels nothing and edits no setting. The
model-written `/weekly-review --sprint` is the deeper version over the same period.

WHAT IT DOES NOT SHOW.  The acceptance check's screenshots are listed by name when the config names where they are
kept; they are not uploaded into the document. "Merged on its own" is GitHub's own `auto_merge` field on the pull
request, so it is only as true as that field.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import pipeline_bridge_transport as transport  # noqa: E402  (its HTTP call refuses a redirect)
import pipeline_sprint as sprint  # noqa: E402
import pipeline_status_local as status_job  # noqa: E402

CONFIG_SCHEMA = "pipeline-sprint-review-config/1"
RECORD_SCHEMA = "pipeline-sprint-review-record/1"
GITHUB_API = "https://api.github.com"
LINEAR_API = "https://api.linear.app/graphql"
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*\Z")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}\Z")
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
FINDING_RE = re.compile(r"^- \S+ \*\*(?:high|medium|low|critical)\*\* · _([a-z][a-z-]{0,30})_", re.M)
CATEGORIES = ("correctness", "security", "tests", "scope", "simplification", "efficiency", "test-coverage",
              "docs", "performance", "reuse")
KEYS = {"schema", "repos", "github_token_env", "linear_key_env", "linear_project_id", "weekly_budget_usd",
        "review_dir", "screenshots_dir", "max_pull_requests", "max_pages", "review_authors", "health_state_file"}
LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}(\[bot\])?\Z")
MARK_WITHIN = 200       # the review mark must be this near the start of the comment
EXAMPLE = {"schema": CONFIG_SCHEMA, "repos": ["acme/app"], "github_token_env": "SPRINT_REVIEW_GITHUB_TOKEN",
           "linear_key_env": "SPRINT_REVIEW_LINEAR_KEY", "linear_project_id": "00000000-0000-0000-0000-000000000000",
           "weekly_budget_usd": 100, "review_dir": "~/.stage-e/sprint-review", "screenshots_dir": "",
           "review_authors": ["the-account-stage-e-comments-as"], "health_state_file": "",
           "max_pull_requests": 60, "max_pages": 5}


class ReviewError(Exception):
    """Something that stops the review, said in words that quote no value."""


def _iso(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def _money(v):
    v = round(float(v), 2)
    return ("%d" % v) if v == int(v) else ("%.2f" % v)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


# --------------------------------------------------------------------------- #
# The config
# --------------------------------------------------------------------------- #
def validate_config(raw):
    """(config, problems): every fault at once, by key. Names of variables, never their values."""
    if not isinstance(raw, dict):
        return None, ["the sprint review's config must hold one JSON object"]
    bad = []
    extra = sorted(set(raw) - KEYS)
    if extra:
        bad.append("unknown key(s): %s" % ", ".join(str(k)[:40] for k in extra))
    if raw.get("schema") != CONFIG_SCHEMA:
        bad.append("schema must be %s" % CONFIG_SCHEMA)
    repos = raw.get("repos")
    if not isinstance(repos, list) or not repos or len(repos) > 10 or not all(
            isinstance(r, str) and REPO_RE.match(r) for r in repos) or len(set(repos)) != len(repos):
        bad.append("repos must list from 1 to 10 OWNER/REPO names, each once")
    for key in ("github_token_env", "linear_key_env"):
        if not isinstance(raw.get(key), str) or not ENV_RE.match(raw[key]):
            bad.append("%s must be the NAME of an environment variable" % key)
    if not isinstance(raw.get("linear_project_id"), str) or not UUID_RE.match(raw["linear_project_id"]):
        bad.append("linear_project_id must be the project's id (a UUID): the document is filed in it")
    authors = raw.get("review_authors")
    if not isinstance(authors, list) or not authors or len(authors) > 5 or not all(
            isinstance(a, str) and LOGIN_RE.match(a) for a in authors):
        bad.append("review_authors must list from 1 to 5 GitHub logins: only a comment from one of them counts as Stage E's review")
    budget = raw.get("weekly_budget_usd")
    if budget is not None and (not _num(budget) or budget <= 0):
        bad.append("weekly_budget_usd must be a number above 0, or null")
    for key in ("review_dir",):
        if not isinstance(raw.get(key), str) or not os.path.isabs(os.path.expanduser(raw.get(key) or "")):
            bad.append("%s must be an absolute path (~/ is allowed)" % key)
    shots = raw.get("screenshots_dir", "")
    if not isinstance(shots, str) or (shots and not os.path.isabs(os.path.expanduser(shots))):
        bad.append("screenshots_dir must be an absolute path (~/ is allowed), or empty")
    health = raw.get("health_state_file", "")
    if not isinstance(health, str) or (health and not os.path.isabs(os.path.expanduser(health))):
        bad.append("health_state_file must be an absolute path (~/ is allowed), or empty")
    for key, low, high, default in (("max_pull_requests", 1, 200, 60), ("max_pages", 1, 10, 5)):
        v = raw.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            bad.append("%s must be a whole number from %d to %d" % (key, low, high))
    if bad:
        return None, bad
    cfg = dict(raw)
    cfg.setdefault("screenshots_dir", "")
    cfg["max_pull_requests"] = raw.get("max_pull_requests", 60)
    cfg["max_pages"] = raw.get("max_pages", 5)
    cfg["review_dir"] = os.path.expanduser(raw["review_dir"])
    cfg["screenshots_dir"] = os.path.expanduser(cfg["screenshots_dir"]) if cfg["screenshots_dir"] else ""
    cfg["health_state_file"] = os.path.expanduser(cfg.get("health_state_file") or "")
    return cfg, []


def load_config(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return validate_config(json.load(fh))
    except (OSError, ValueError):
        return None, ["the sprint review's config at %s could not be read" % path]


# --------------------------------------------------------------------------- #
# The record: once a sprint
# --------------------------------------------------------------------------- #
def record_path(cfg):
    return os.path.join(cfg["review_dir"], "sprint-review-record.json")


def load_record(cfg):
    """({sprint id: {...}}, None), or (None, why) for a record that cannot be used (the review is not sent)."""
    try:
        with open(record_path(cfg), encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError):
        return None, "the sprint review's record could not be read"
    if not isinstance(doc, dict) or doc.get("schema") != RECORD_SCHEMA or not isinstance(doc.get("sprints"), dict):
        return None, "the sprint review's record is not one this version reads"
    return doc["sprints"], None


def save_record(cfg, sprints):
    os.makedirs(cfg["review_dir"], exist_ok=True)
    path = record_path(cfg)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"schema": RECORD_SCHEMA, "sprints": sprints}, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ReviewError("the sprint review's record could not be written (%s)" % (exc.strerror or "error"))


RESET_MARGIN = datetime.timedelta(hours=1)      # a review is due at least this long before the week resets


def due_at(view, afternoon):
    """When the review is due on the last working day: the afternoon time, or an hour before the plan's reset when
    that comes first (a reset at 13:00 and a stand-up at 16:00 must not lose the sprint)."""
    return min(afternoon, view["resets_at"] - RESET_MARGIN)


def due(view, now, afternoon, sprints):
    """True when today is the sprint's last working day, the due time (due_at) has come, and this sprint has not been
    reviewed. `view` is the sprint view for today; `now` and `afternoon` are aware local datetimes."""
    if not view.get("known") or view["day"] < 1 or view["days_left"] != 0:
        return False
    return now >= due_at(view, afternoon) and view_id(view) not in sprints


def print_period(settings_path, now=None):
    """The sprint's period for /weekly-review --sprint, from the stand-up's own settings and usage file (the same
    working days, the same freshness rule): 0 and one JSON line, or 1 and the reason. Never a guess."""
    import pipeline_standup as standup      # here, not at the top: the stand-up imports this module
    try:
        settings = standup.load_settings(settings_path)
    except standup.StandupError as exc:
        print("the sprint is not known: %s" % exc)
        return 1
    if not settings["sprint"] or not settings["usage_file"]:
        print("the sprint is not known: the stand-up's sprint setting is off, or it names no usage file")
        return 1
    now = now or datetime.datetime.now().astimezone()
    usage, problem = standup.read_dated(settings, "usage_file", now, standup.USAGE_SCHEMA, "usage file", False)
    if usage is None:
        print("the sprint is not known: %s" % problem)
        return 1
    view = sprint.sprint_view(usage, now, settings["days"])
    if not view["known"]:
        print("the sprint is not known: %s" % view["why"])
        return 1
    print(json.dumps({"since": _iso(view["starts_at"]), "until": _iso(view["resets_at"]), "first_day": view["first_day"].isoformat(),
                      "days_total": view["days_total"]}))
    return 0


def view_id(view):
    return _iso(view["starts_at"])


# --------------------------------------------------------------------------- #
# GitHub: the pull requests merged in the period, and what Stage E said of each
# --------------------------------------------------------------------------- #
GATHER_SECONDS = 60         # all the pull request pages together; what is left unread is said


def gather_pulls(cfg, since, until, http=None, environ=None, clock=time.monotonic, seconds=GATHER_SECONDS):
    """{"pulls": [{repo, number, title, merged_at, auto}], "partial": [why...], "reviews": {...}}. Every list is
    read page by page up to max_pages; a repository whose pages ran out is said, not counted as complete."""
    http = http or transport._http_json
    environ = os.environ if environ is None else environ
    token = environ.get(cfg["github_token_env"], "")
    if not token:
        raise ReviewError("the GitHub token (%s) is not set, so merged pull requests could not be read" % cfg["github_token_env"])
    headers = {"Authorization": "Bearer %s" % token, "Accept": "application/vnd.github+json",
               "User-Agent": "sprint-review"}
    pulls, partial = [], []
    stop = clock() + seconds
    for repo in cfg["repos"]:
        done = False
        for page in range(1, cfg["max_pages"] + 1):
            if clock() > stop:
                partial.append("%s was not read in full: the time allowed for reading GitHub ran out" % repo)
                done = True
                break
            url = "%s/repos/%s/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=%d" % (GITHUB_API, repo, page)
            try:
                rows = http(url, headers, None, 10)
            except Exception as exc:  # noqa: BLE001 -- any failure is said, never read as "none merged"
                partial.append("%s could not be read (%s)" % (repo, type(exc).__name__))
                done = True
                break
            if not isinstance(rows, list):
                partial.append("%s answered in a shape this review cannot read" % repo)
                done = True
                break
            for row in rows:
                merged = _parse(row.get("merged_at")) if isinstance(row, dict) else None
                if merged and since <= merged < until and isinstance(row.get("number"), int):
                    pulls.append({"repo": repo, "number": row["number"], "merged_at": row["merged_at"],
                                  "title": status_job.clean_title(row.get("title") or ""),
                                  "auto": row.get("auto_merge") is not None})
            oldest = _parse(rows[-1].get("updated_at")) if rows and isinstance(rows[-1], dict) else None
            if len(rows) < 100 or (oldest is not None and oldest < since):
                done = True
                break
        if not done:
            partial.append("%s has more recently updated pull requests than %d pages hold, so some merged ones may be missing"
                           % (repo, cfg["max_pages"]))
    pulls.sort(key=lambda p: (p["merged_at"], p["repo"], p["number"]))
    return {"pulls": pulls, "partial": partial}


COMMENTS_SECONDS = 60       # all the review-comment reads together; the rest are counted as unread, and said


def review_counts(cfg, pulls, http=None, environ=None, clock=time.monotonic, seconds=COMMENTS_SECONDS):
    """Stage E's review comment on each merged pull request, as counts: ({category: n}, reviewed, with findings,
    not read). Only the finding lines' categories are read, from a closed list; the comment's words are not kept."""
    http = http or transport._http_json
    environ = os.environ if environ is None else environ
    headers = {"Authorization": "Bearer %s" % environ.get(cfg["github_token_env"], ""),
               "Accept": "application/vnd.github+json", "User-Agent": "sprint-review"}
    cats, reviewed, flagged, unread = {}, 0, 0, 0
    stop = clock() + seconds
    for p in pulls[:cfg["max_pull_requests"]]:
        if clock() > stop:
            unread += 1
            continue
        try:
            rows = http("%s/repos/%s/issues/%d/comments?per_page=100" % (GITHUB_API, p["repo"], p["number"]), headers, None, 10)
        except Exception:  # noqa: BLE001
            unread += 1
            continue
        mine = [r for r in rows if isinstance(r, dict) and isinstance(r.get("body"), str)
                and "stage-e-review" in r["body"][:MARK_WITHIN] and isinstance(r.get("user"), dict)
                and r["user"].get("login") in cfg["review_authors"]] if isinstance(rows, list) else []
        if not mine:
            continue
        reviewed += 1
        found = [c if c in CATEGORIES else "other" for c in FINDING_RE.findall(mine[-1]["body"])]
        if found:
            flagged += 1
        for c in found:
            cats[c] = cats.get(c, 0) + 1
    unread += max(0, len(pulls) - cfg["max_pull_requests"])
    return cats, reviewed, flagged, unread


# --------------------------------------------------------------------------- #
# The recap
# --------------------------------------------------------------------------- #
def cost_section(usage, budget, since, until):
    """(lines, spent or None). Never a number the file did not give."""
    week = usage.get("week") if isinstance(usage, dict) and isinstance(usage.get("week"), dict) else {}
    if week.get("known") is not True or not _num(week.get("usd")):
        return ["The usage file did not give this week's spend, so no cost is shown."], None
    spent = float(week["usd"])
    lines = []
    if budget:
        lines.append("The sprint spent $%s of the $%s planned (%d%%)." % (_money(spent), _money(budget), round(100 * spent / budget)))
    else:
        lines.append("The sprint spent $%s. No weekly budget is set, so there is no plan to compare it with." % _money(spent))
    kinds = week.get("by_kind") if isinstance(week.get("by_kind"), dict) else {}
    rows = sorted(((k, v) for k, v in kinds.items() if isinstance(v, dict) and _num(v.get("usd"))),
                  key=lambda kv: (-kv[1]["usd"], kv[0]))
    if rows:
        lines += ["", "| Kind of work | Runs | Spent |", "|---|---|---|"]
        lines += ["| %s | %s | $%s |" % (re.sub(r"[^a-z_ ]", "", k)[:30].replace("_", " "),
                                          v.get("runs") if isinstance(v.get("runs"), int) else "?", _money(v["usd"]))
                  for k, v in rows]
    pieces = []
    for p in usage.get("pieces") or [] if isinstance(usage, dict) else []:
        if isinstance(p, dict) and _num(p.get("usd")) and isinstance(p.get("id"), str) and re.match(r"^[A-Za-z0-9_-]{1,40}$", p["id"]):
            at = _parse(p.get("last_at"))
            if at and since <= at < until:
                pieces.append(p)
    pieces.sort(key=lambda p: (-p["usd"], p["id"]))
    if pieces:
        lines += ["", "The dearest pieces: " + ", ".join("%s ($%s)" % (p["id"], _money(p["usd"])) for p in pieces[:5]) + "."]
    return lines, spent


HEALTH_MAX_BYTES = 2000000
DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def read_health(cfg):
    """(days, why): the health watch's own record of each day's lowest free memory and most swap (its state file's
    `memory_trend`, KIT-609). `days` maps a UTC day to (lowest free percent, most swap in GB); a day whose figures are
    not plain numbers is left out. `why` says it could not be read (a file that is not named, missing, too large or
    in another shape): the review then says so, and never shows a reading it did not get."""
    path = cfg.get("health_state_file") or ""
    if not path:
        return None, "the config names no health state file"
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read(HEALTH_MAX_BYTES + 1)
    except OSError:
        return None, "the health watch's state file could not be read"
    if len(text) > HEALTH_MAX_BYTES:
        return None, "the health watch's state file is too large to read"
    try:
        doc = json.loads(text)
    except ValueError:
        return None, "the health watch's state file is not JSON"
    trend = doc.get("memory_trend") if isinstance(doc, dict) else None
    if not isinstance(trend, dict):
        return None, "the health watch's state file keeps no memory trend"
    days = {}
    for day, row in trend.items():
        if (isinstance(day, str) and DAY_RE.match(day) and isinstance(row, dict) and _num(row.get("min_free_pct"))
                and row["min_free_pct"] <= 100 and _num(row.get("max_swap_gb")) and row["max_swap_gb"] <= 4096):
            days[day] = (float(row["min_free_pct"]), float(row["max_swap_gb"]))
    return days, None


def _dates(first, last):
    """Every calendar date from `first` to `last` inclusive, as YYYY-MM-DD."""
    out, d = [], first
    while d <= last:
        out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def machine_section(health, why, usage, budget, since, until, tz=None):
    """The lines under "The machine and the budget": the week's lowest free memory and most swap, and the burn
    against the reset. A figure that was not given is said, never shown as zero. `tz` is the zone the usage file's
    days are in (the machine's own when not given)."""
    lines = []
    utc_days = _dates(since.astimezone(datetime.timezone.utc).date(), (until - datetime.timedelta(seconds=1)).astimezone(
        datetime.timezone.utc).date())
    if why:
        lines.append("Memory and swap are not shown: %s." % why)
    else:
        seen = dict((d, v) for d, v in health.items() if d in utc_days)
        if not seen:
            lines.append("The health watch recorded no memory reading in this period.")
        else:
            low = min(sorted(seen), key=lambda d: seen[d][0])
            swap = max(sorted(seen), key=lambda d: seen[d][1])
            lines.append("Lowest free memory: %d%% (on %s). Most swap used: %.1f GB (on %s). The watch recorded %d of the "
                         "%d days (days are the watch's own, in UTC)." % (round(seen[low][0]), low, seen[swap][1], swap,
                                                                          len(seen), len(utc_days)))
    local_days = _dates(since.astimezone(tz).date(), (until - datetime.timedelta(seconds=1)).astimezone(tz).date())
    rows = dict((d["date"], d) for d in (usage.get("days") if isinstance(usage, dict) and isinstance(usage.get("days"), list)
                                         else []) if isinstance(d, dict) and isinstance(d.get("date"), str))
    lines += ["", "The burn against the reset" + (", as a share of the $%s plan against an even pace:" % _money(budget)
                                                   if budget else " (no weekly budget is set, so no share of a plan):"), ""]
    lines += ["| Day | Spent | So far" + (" | Share of plan | Even pace" if budget else "") + " |",
              "|---|---|---" + ("|---|---" if budget else "") + "|"]
    total, floor = 0.0, False
    for i, day in enumerate(local_days):
        row = rows.get(day)
        usd = row.get("usd") if row else None
        unpriced = row.get("unpriced") if row else None
        if not _num(usd):
            floor = True
            spent = "no figure"
        else:
            total += float(usd)
            if unpriced != 0:                 # runs counted but never priced (contract section 17): a lower bound
                floor = True
            spent = ("at least " if unpriced != 0 else "") + "$" + _money(usd)
        so_far = ("at least " if floor else "") + "$" + _money(total)
        line = "| %s | %s | %s" % (day, spent, so_far)
        if budget:
            midnight = datetime.datetime.combine(datetime.date.fromisoformat(day) + datetime.timedelta(days=1), datetime.time.min)
            end = midnight.replace(tzinfo=tz) if tz is not None else midnight.astimezone()     # the clock's own offset that day
            pace = min(1.0, (end - since).total_seconds() / (until - since).total_seconds())
            line += " | %s%d%% | %d%%" % ("at least " if floor else "", round(100 * total / budget), round(100 * pace))
        lines.append(line + " |")
    week = ((usage.get("status") or {}).get("windows") or {}).get("seven_day") if isinstance(usage, dict) and isinstance(
        usage.get("status"), dict) and isinstance((usage["status"]).get("windows"), dict) else None
    used = week.get("utilization") if isinstance(week, dict) else None
    seen_at = _parse(week.get("seen_at")) if isinstance(week, dict) else None
    lines.append("")
    if _num(used) and used <= 1.5 and seen_at:
        lines.append("The plan's weekly window read %d%% used when it was last seen (%s)." % (round(used * 100), seen_at.astimezone(tz).strftime("%a %d %b")))
    else:
        lines.append("The usage file did not give the plan's weekly window, so its use at the reset is unknown.")
    return lines


def build(view, usage, budget, pulls, partial, reviews, shots, now, machine=None):
    """(title, markdown, facts). `facts` are the numbers the DM message states."""
    since, until = view["starts_at"], view["resets_at"]
    local = lambda d: d.astimezone().strftime("%a %d %b")      # noqa: E731
    title = "Sprint review: %s to %s" % (local(since), local(until))
    own = [p for p in pulls if p["auto"]]
    cost_lines, spent = cost_section(usage, budget, since, until)
    cats, reviewed, flagged, unread = reviews
    lines = ["# %s" % title, "", "Built by code from the usage file, GitHub and the weekly budget. No model wrote it.", "",
             "## What shipped", ""]
    if pulls:
        for p in pulls:
            lines.append("- %s#%d %s (merged %s%s)" % (p["repo"], p["number"], p["title"] or "(no title)",
                                                       local(_parse(p["merged_at"])), ", on its own" if p["auto"] else ""))
    else:
        lines.append("No pull request merged in this period.")
    for why in partial:
        lines.append("")
        lines.append("Not complete: %s." % why)
    lines += ["", "## What merged on its own", "",
              ("%d of %d merged with auto-merge on: %s." % (len(own), len(pulls), ", ".join("%s#%d" % (p["repo"], p["number"]) for p in own))
               if own else "None merged on its own.") if pulls else "Nothing merged, so nothing merged on its own.",
              "", "## What it cost", ""] + cost_lines + (
                  ["", "## The machine and the budget", ""] + machine if machine is not None else []) + [
              "", "## What the reviews taught", ""]
    if reviewed:
        lines.append("Stage E reviewed %d of the %d merged pull requests and raised findings on %d."
                     % (reviewed, len(pulls), flagged))
        if cats:
            lines.append("By kind: " + ", ".join("%s %d" % (k, n) for k, n in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))) + ".")
    else:
        lines.append("No Stage E review comment was found on the merged pull requests.")
    if unread:
        lines.append("%d pull request(s) could not be checked for a review." % unread)
    lines += ["", "## Screenshots", ""]
    lines += (["Kept by the acceptance check this sprint (on this machine, not uploaded): " + ", ".join(shots) + "."]
              if shots else ["None kept in this period, or no folder is named."])
    lines += ["", "## The deeper review", "",
              "`/weekly-review --sprint` writes the model-written version over the same period. This recap proposes nothing and changes nothing."]
    facts = {"title": title, "pulls": len(pulls), "own": len(own), "spent": spent, "budget": budget,
             "reviewed": reviewed, "flagged": flagged, "partial": bool(partial)}
    return title, "\n".join(lines) + "\n", facts


def kept_screenshots(cfg, since, until):
    """Names of the image files in screenshots_dir last changed in the period (at most 20), else []."""
    if not cfg["screenshots_dir"]:
        return []
    out = []
    try:
        for name in sorted(os.listdir(cfg["screenshots_dir"])):
            if not re.match(r"^[A-Za-z0-9._-]{1,80}\.(png|jpg|jpeg|webp)\Z", name):
                continue
            st = os.lstat(os.path.join(cfg["screenshots_dir"], name))
            when = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc)
            if since <= when < until and not os.path.islink(os.path.join(cfg["screenshots_dir"], name)):
                out.append(name)
    except OSError:
        return []
    return out[:20]


# --------------------------------------------------------------------------- #
# Writing the document, and the message
# --------------------------------------------------------------------------- #
def write_document(cfg, title, markdown, http=None, environ=None):
    """The Linear document's URL. Raises ReviewError, in words, when it cannot be written."""
    http = http or transport._http_json
    environ = os.environ if environ is None else environ
    key = environ.get(cfg["linear_key_env"], "")
    if not key:
        raise ReviewError("the Linear key (%s) is not set" % cfg["linear_key_env"])
    query = ("mutation SprintReview($title: String!, $content: String!, $projectId: String!) { "
             "documentCreate(input: { title: $title, content: $content, projectId: $projectId }) "
             "{ success document { url } } }")
    try:
        doc = http(LINEAR_API, {"Authorization": key, "Content-Type": "application/json"},
                   {"query": query, "variables": {"title": title, "content": markdown, "projectId": cfg["linear_project_id"]}}, 15)
    except Exception as exc:  # noqa: BLE001
        raise ReviewError("Linear could not be reached (%s)" % type(exc).__name__)
    made = ((doc or {}).get("data") or {}).get("documentCreate") if isinstance(doc, dict) else None
    url = ((made or {}).get("document") or {}).get("url") if isinstance(made, dict) else None
    if not isinstance(made, dict) or made.get("success") is not True or not isinstance(url, str) \
            or not url.startswith("https://linear.app/"):
        raise ReviewError("Linear did not accept the document")
    return url


def save_local(cfg, view, markdown):
    """The recap saved on this machine, always: the path."""
    os.makedirs(cfg["review_dir"], exist_ok=True)
    path = os.path.join(cfg["review_dir"], "sprint-review-%s.md" % view_id(view)[:10])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    return path


def message(facts, url, local_path, failure):
    """The one DM message, fixed words with numbers put in. With no link it says why and where the copy is."""
    parts = ["*%s*" % facts["title"],
             "%d pull request%s merged%s." % (facts["pulls"], "" if facts["pulls"] == 1 else "s",
                                              " (%d on their own)" % facts["own"] if facts["own"] else "")]
    if facts["spent"] is not None:
        parts.append("The sprint spent $%s%s." % (_money(facts["spent"]), " of the $%s planned" % _money(facts["budget"]) if facts["budget"] else ""))
    else:
        parts.append("This week's spend could not be read from the usage file.")
    if facts["reviewed"]:
        parts.append("Stage E raised findings on %d of the %d it reviewed." % (facts["flagged"], facts["reviewed"]))
    if facts["partial"]:
        parts.append("Some of it could not be read in full, and the review says where.")
    parts.append("The full review: %s" % url if url else
                 "I could not write the full review to Linear (%s). A copy is saved on this machine at %s." % (failure, local_path))
    return "\n".join(parts)


def run_review(cfg, view, usage, now, http=None, environ=None, seconds=None):
    """Builds and writes the review, and returns (message text, record entry). The caller records BEFORE posting.
    A failure to write the document is the message; a failure to read GitHub is in the document."""
    since, until = view["starts_at"], view["resets_at"]
    half = GATHER_SECONDS if seconds is None else max(5, min(GATHER_SECONDS, int((seconds - 15) / 2)))
    try:
        got = gather_pulls(cfg, since, until, http, environ, seconds=half)
        reviews = review_counts(cfg, got["pulls"], http, environ, seconds=half)
        pulls, partial = got["pulls"], got["partial"]
    except ReviewError as exc:
        pulls, partial, reviews = [], [str(exc)], ({}, 0, 0, 0)
    health, why = read_health(cfg)
    title, markdown, facts = build(view, usage, cfg["weekly_budget_usd"], pulls, partial, reviews,
                                   kept_screenshots(cfg, since, until), now,
                                   machine_section(health, why, usage, cfg["weekly_budget_usd"], since, until))
    local_path = save_local(cfg, view, markdown)
    try:
        url, failure = write_document(cfg, title, markdown, http, environ), None
    except ReviewError as exc:
        url, failure = None, str(exc)
    return message(facts, url, local_path, failure), {"at": _iso(now), "url": url, "local": local_path}


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="The sprint review.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--example-config", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    per = sub.add_parser("period", help="print the current sprint's period as JSON, for /weekly-review --sprint")
    per.add_argument("--settings", default=None, help="the stand-up's settings file (default: its own)")
    args = ap.parse_args(argv)
    if args.cmd == "period":
        return print_period(args.settings)
    if args.example_config:
        print(json.dumps(EXAMPLE, indent=2))
        return 0
    if args.selftest:
        from scratch_run import contained
        return contained(_selftest, "sprint-review-selftest-run-")
    ap.print_usage()
    return 2


def _selftest():
    import copy
    import tempfile
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))
    tz = datetime.timezone(datetime.timedelta(hours=-5))
    days = ["mon", "tue", "wed", "thu", "fri", "sat"]
    usage = {"result": "ok", "week": {
        "known": True, "starts_at": "2026-11-10T18:00:00Z", "resets_at": "2026-11-17T18:00:00Z", "usd": 62.5, "runs": 40,
        "by_kind": {"coding": {"runs": 20, "usd": 40.0}, "review": {"runs": 15, "usd": 12.5}, "tech_lead": {"runs": 5, "usd": 10.0}}},
        "pieces": [{"id": "KIT-1", "kind": "coding", "usd": 9.0, "runs": 3, "last_at": "2026-11-12T10:00:00Z"},
                   {"id": "KIT-2", "kind": "coding", "usd": 3.0, "runs": 1, "last_at": "2026-11-02T10:00:00Z"},
                   {"id": "KIT-3", "kind": "coding", "usd": 4.0, "runs": 1, "last_at": "2026-11-13T10:00:00Z"}]}
    at = lambda d, h: datetime.datetime(2026, 11, d, h, tzinfo=tz)      # noqa: E731
    view = sprint.sprint_view(usage, at(17, 9), days, tz=tz)
    check("the test sprint's last working day is Tuesday 17 Nov", (view["known"], view["day"], view["days_left"]), (True, 6, 0))
    mid = sprint.sprint_view(usage, at(12, 9), days, tz=tz)
    # the config
    with tempfile.TemporaryDirectory() as tmp:
        raw = dict(EXAMPLE, review_dir=os.path.join(tmp, "rev"), screenshots_dir=os.path.join(tmp, "shots"))
        cfg, bad = validate_config(raw)
        check("the example config, with real folders, is valid", bad, [])
        check("the example config is valid in its own right", validate_config(EXAMPLE)[1], [])
        for label, over, needle in (("a repo that is not OWNER/REPO", {"repos": ["acme"]}, "repos"),
                                    ("a token that is not a name", {"github_token_env": "ghp_" + "x" * 30}, "github_token_env"),
                                    ("a Linear key given as a value", {"linear_key_env": "lin_api_abc"}, "linear_key_env"),
                                    ("a project that is not an id", {"linear_project_id": "p"}, "linear_project_id"),
                                    ("a budget of zero", {"weekly_budget_usd": 0}, "weekly_budget_usd"),
                                    ("a relative review folder", {"review_dir": "rev"}, "review_dir"),
                                    ("an unknown key", {"extra": 1}, "unknown key"),
                                    ("no review authors", {"review_authors": []}, "review_authors"),
                                    ("a review author that is not a login", {"review_authors": ["a b"]}, "review_authors"),
                                    ("a page cap of 99", {"max_pages": 99}, "max_pages")):
            check("config: %s is refused, by key" % label, any(needle in e for e in validate_config(dict(raw, **over))[1]), True)
        check("a key given as a value is never quoted back",
              "lin_api_abc" in " ".join(validate_config(dict(raw, linear_key_env="lin_api_abc"))[1]), False)
        # when it is due
        aft = at(17, 15)
        check("due: the last working day, after the afternoon time, not yet reviewed", due(view, at(17, 15), aft, {}), True)
        check("not due: before the due time", due(view, at(17, 11), aft, {}), False)
        check("due an hour before the reset (13:00) when that comes before the afternoon time (16:00): the sprint is not lost",
              (due(view, at(17, 11), at(17, 16), {}), due(view, at(17, 12), at(17, 16), {}), due(view, at(17, 12, ), at(17, 11), {})),
              (False, True, True))
        check("…and the afternoon time still rules when it comes first",
              (due(view, at(17, 9), at(17, 10), {}), due(view, at(17, 10), at(17, 10), {}), due_at(view, at(17, 10)) == at(17, 10)),
              (False, True, True))
        check("not due: a day that is not the last", due(mid, at(12, 16), at(12, 15), {}), False)
        check("not due: this sprint was reviewed", due(view, at(17, 16), aft, {view_id(view): {}}), False)
        check("due again for the next sprint", due(view, at(17, 16), aft, {"2026-11-03T18:00:00Z": {}}), True)
        check("not due: a sprint the usage file cannot place", due({"known": False}, at(17, 16), aft, {}), False)
        # GitHub, stood in for
        since, until = view["starts_at"], view["resets_at"]
        pr = lambda n, merged, auto=False, title="Fix it", upd=None: {          # noqa: E731
            "number": n, "title": title, "merged_at": merged, "updated_at": upd or merged or "2026-11-12T00:00:00Z",
            "auto_merge": {"merge_method": "squash"} if auto else None}
        AUTHOR = EXAMPLE["review_authors"][0]
        review = lambda *cats, **kw: {"user": {"login": kw.get("by", AUTHOR)},        # noqa: E731
                                      "body": kw.get("lead", "## Stage E review <!-- stage-e-review -->\n") + "\n".join(
                                          "- \U0001f7e1 **medium** · _%s_ `a.py:1` — x" % c for c in cats)}
        board = {
            GITHUB_API + "/repos/acme/app/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": [
                pr(3, "2026-11-13T12:00:00Z", auto=True, title="A <b>bold</b> & `title`"),
                pr(2, "2026-11-12T12:00:00Z"), pr(1, "2026-11-02T12:00:00Z"), pr(9, None)],
            GITHUB_API + "/repos/acme/app/issues/3/comments?per_page=100": [{"body": "hi"}, review("correctness", "tests", "weirdness")],
            GITHUB_API + "/repos/acme/app/issues/2/comments?per_page=100": [{"body": "no review here"}]}
        asked = []

        def http(url, headers, payload=None, timeout=20):
            asked.append((url, payload is not None))
            if url == LINEAR_API:
                if http.linear == "boom":
                    raise OSError("down")
                return http.linear
            if url in board:
                return board[url]
            raise OSError("no such page")
        http.linear = {"data": {"documentCreate": {"success": True, "document": {"url": "https://linear.app/x/document/sprint-1"}}}}
        env = {cfg["github_token_env"]: "t", cfg["linear_key_env"]: "k"}
        got = gather_pulls(cfg, since, until, http, env)
        check("only pull requests merged inside the period are counted, in order, with the auto-merge flag",
              [(p["number"], p["auto"]) for p in got["pulls"]], [(2, False), (3, True)])
        check("a title is cleaned as the status file cleans one", "<" in got["pulls"][1]["title"] or "`" in got["pulls"][1]["title"], False)
        check("a complete read says nothing is missing", got["partial"], [])
        page1 = [pr(100 + i, "2026-11-13T12:00:00Z") for i in range(100)]
        board2 = dict(board)
        board2[GITHUB_API + "/repos/acme/app/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1"] = page1
        board2[GITHUB_API + "/repos/acme/app/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=2"] = page1
        capped = gather_pulls(dict(cfg, max_pages=2), since, until, lambda u, h, payload=None, timeout=20: board2[u], env)
        check("a page limit hit while still inside the period is said, never counted as complete",
              (len(capped["pulls"]), "more recently updated pull requests than 2 pages" in " ".join(capped["partial"])), (200, True))
        down = gather_pulls(cfg, since, until, lambda *a, **k: (_ for _ in ()).throw(OSError("x")), env)
        check("a repository that could not be read is said, not read as none merged",
              (down["pulls"], "could not be read (OSError)" in " ".join(down["partial"])), ([], True))
        try:
            gather_pulls(cfg, since, until, http, {})
            tok = "read"
        except ReviewError as exc:
            tok = str(exc)
        check("no GitHub token is a refusal naming the variable, not a value", cfg["github_token_env"] in tok, True)
        cats, reviewed, flagged, unread = review_counts(cfg, got["pulls"], http, env)
        check("Stage E's findings are counted by a closed list of kinds; one that is not on it is 'other'",
              (cats, reviewed, flagged, unread), ({"correctness": 1, "tests": 1, "other": 1}, 1, 1, 0))
        ticks = iter([0, 0, 1000, 1000, 1000])
        slow = review_counts(cfg, got["pulls"] * 2, http, env, clock=lambda: next(ticks))
        check("comment reads past the time allowed are counted as unread, never as 'no review'", slow[3] >= 2, True)
        board_f = dict(board)
        board_f[GITHUB_API + "/repos/acme/app/issues/3/comments?per_page=100"] = [
            review("correctness", by="someone-else"), review("tests", lead="x" * 300 + "<!-- stage-e-review -->\n")]
        forged = review_counts(cfg, got["pulls"], lambda u, h, p_=None, t=20: board_f.get(u, []), env)
        check("a comment from another account, or with the mark far from the start, is not Stage E's review",
              forged[:3], ({}, 0, 0))
        slow_pulls = gather_pulls(cfg, since, until, http, env, clock=iter([0, 1000, 1000, 1000, 1000]).__next__)
        check("GitHub reads past the time allowed stop, and the repository is said as not read in full",
              (slow_pulls["pulls"], "the time allowed for reading GitHub ran out" in " ".join(slow_pulls["partial"])), ([], True))
        check("a comments page that cannot be read is counted as unread", review_counts(cfg, [{"repo": "acme/app", "number": 77}], http, env)[3], 1)
        # cost
        lines, spent = cost_section(usage, 100, since, until)
        check("the cost is against the plan, by kind, with the dearest pieces inside the period only",
              (spent, lines[0], "| coding | 20 | $40 |" in lines, lines[-1]),
              (62.5, "The sprint spent $62.50 of the $100 planned (62%).", True, "The dearest pieces: KIT-1 ($9), KIT-3 ($4)."))
        check("no budget says there is no plan to compare with", "No weekly budget is set" in cost_section(usage, None, since, until)[0][0], True)
        check("a week the file did not give is no number", cost_section({"week": {"known": False}}, 100, since, until), (
            ["The usage file did not give this week's spend, so no cost is shown."], None))
        # the recap and the message
        title, md, facts = build(view, usage, 100, got["pulls"], got["partial"], (cats, reviewed, flagged, unread), ["home.png"], at(17, 15))
        check("the recap has each section the review asks for",
              [h in md for h in ("## What shipped", "## What merged on its own", "## What it cost", "## What the reviews taught", "## Screenshots")],
              [True] * 5)
        check("it says which merged on their own, and which screenshots were kept",
              ("1 of 2 merged with auto-merge on: acme/app#3." in md, "home.png" in md), (True, True))
        check("no angle bracket from a title reaches the document", "<b>" in md, False)
        check("the facts the message states", (facts["pulls"], facts["own"], facts["spent"], facts["reviewed"], facts["flagged"]), (2, 1, 62.5, 1, 1))
        _t, md0, facts0 = build(view, usage, 100, [], [], ({}, 0, 0, 0), [], at(17, 15))
        check("a sprint with nothing merged says so in words", ("No pull request merged in this period." in md0, "None merged on its own." in md0
                                                                or "Nothing merged, so nothing merged on its own." in md0), (True, True))
        # the machine and the budget (KIT-602)
        hpath = os.path.join(tmp, "watch-state.json")

        def put_health(doc_):
            with open(hpath, "w", encoding="utf-8") as fh:
                fh.write(doc_ if isinstance(doc_, str) else json.dumps(doc_))
        hcfg = dict(cfg, health_state_file=hpath)
        put_health({"memory_trend": {"2026-11-09": {"min_free_pct": 3, "max_swap_gb": 9.9},        # before the sprint
                                     "2026-11-10": {"min_free_pct": 31, "max_swap_gb": 0.5},
                                     "2026-11-12": {"min_free_pct": 12.4, "max_swap_gb": 3.26},
                                     "2026-11-14": {"min_free_pct": 12.4, "max_swap_gb": 3.26},
                                     "2026-11-15": {"min_free_pct": True, "max_swap_gb": 1},      # not a number: left out
                                     "2026-11-16": {"min_free_pct": "5", "max_swap_gb": 1},
                                     "2026-11-11": {"min_free_pct": 40, "max_swap_gb": 99999},
                                     "2026-11-17": {"min_free_pct": 400, "max_swap_gb": 1},
                                     "../x": {"min_free_pct": 1, "max_swap_gb": 1},
                                     "2026-11-18": {"min_free_pct": 2, "max_swap_gb": 8}}})   # after the sprint
        days_h, why_h = read_health(hcfg)
        check("the trend keeps only plain numbers on real days", (sorted(days_h), why_h), (
            ["2026-11-09", "2026-11-10", "2026-11-12", "2026-11-14", "2026-11-18"], None))   # not the absurd swap
        for label, setup_, needle in (("no file named", None, "names no health state file"),
                                      ("a missing file", "missing", "could not be read"),
                                      ("a file that is not JSON", "{nope", "not JSON"),
                                      ("a file with no trend", {"kinds": {}}, "keeps no memory trend"),
                                      ("a trend that is not a table", {"memory_trend": [1]}, "keeps no memory trend"),
                                      ("a file too large to read", "{" + " " * (HEALTH_MAX_BYTES + 1) + "}", "too large")):
            if setup_ == "missing":
                forget_ = [hpath]
                os.unlink(hpath)
            elif setup_ is not None:
                put_health(setup_)
            days_u, why_u = read_health(dict(cfg, health_state_file="" if setup_ is None else hpath))
            check("health: %s is said, and no reading comes from it" % label, (days_u, needle in (why_u or "")), (None, True))
        put_health({"memory_trend": {"2026-11-10": {"min_free_pct": 31, "max_swap_gb": 0.5},
                                     "2026-11-12": {"min_free_pct": 12.4, "max_swap_gb": 3.26},
                                     "2026-11-14": {"min_free_pct": 12.4, "max_swap_gb": 3.26}}})
        usage_m = dict(usage, days=[{"date": "2026-11-%02d" % d, "usd": u, "unpriced": 0} for d, u in (
            (10, 5.0), (11, 10.0), (12, 20.0), (13, 7.5), (14, 0.0), (15, 20.0), (16, 0.0), (17, 2.0))],
            status={"windows": {"seven_day": {"utilization": 0.71, "seen_at": "2026-11-17T12:00:00Z"}}})
        days_m, why_m = read_health(hcfg)
        sec = "\n".join(machine_section(days_m, why_m, usage_m, 100, since, until, tz))
        check("memory: the lowest free and the most swap, each with its day, and how many days were recorded",
              ("Lowest free memory: 12% (on 2026-11-12). Most swap used: 3.3 GB (on 2026-11-12)." in sec, "3 of the 8 days" in sec),
              (True, True))
        check("memory: a day outside the sprint never counts", "2026-11-09" in sec or "2026-11-18" in sec, False)
        check("burn: each day's spend, the running total and the share of plan against an even pace",
              ("| 2026-11-12 | $20 | $35 | 35% | 35% |" in sec, "| 2026-11-16 | $0 | $62.50 | 62% | 92% |" in sec),
              (True, True))
        check("burn: the plan's own weekly window is stated with the day it was seen",
              "The plan's weekly window read 71% used when it was last seen (Tue 17 Nov)." in sec, True)
        sec_nb = "\n".join(machine_section(days_m, why_m, usage_m, None, since, until, tz))
        check("burn: with no budget there is no share and the table says why",
              ("no weekly budget is set" in sec_nb, "Share of plan" in sec_nb, "| 2026-11-12 | $20 | $35 |" in sec_nb,
               "\n|---|---|---|\n" in sec_nb),
              (True, False, True, True))
        no_seen = dict(usage_m, status={"windows": {"seven_day": {"utilization": 0.71}}})
        check("a weekly window with no time it was seen at is not stated",
              "so its use at the reset is unknown" in "\n".join(machine_section(days_m, why_m, no_seen, 100, since, until, tz)), True)
        gap = dict(usage_m, days=[d for d in usage_m["days"] if d["date"] != "2026-11-13"])
        sec_gap = "\n".join(machine_section(days_m, why_m, gap, 100, since, until, tz))
        check("burn: a day the usage file did not give is no figure, and every total after it is at least",
              ("| 2026-11-13 | no figure | at least $35 | at least 35% |" in sec_gap, "| 2026-11-14 | $0 | at least $35 |" in sec_gap),
              (True, True))
        unp = dict(usage_m, days=[dict(d, unpriced=2) if d["date"] == "2026-11-13" else d for d in usage_m["days"]])
        sec_unp = "\n".join(machine_section(days_m, why_m, unp, 100, since, until, tz))
        check("burn: a day with runs that could not be priced is a lower bound, and every total after it is at least",
              ("| 2026-11-13 | at least $7.50 | at least $42.50 | at least 42% |" in sec_unp,
               "| 2026-11-12 | $20 | $35 | 35% |" in sec_unp, "| 2026-11-14 | $0 | at least $42.50 |" in sec_unp), (True, True, True))
        zero = dict(usage_m, days=[dict(d, unpriced=0) for d in usage_m["days"]])
        odd = dict(usage_m, days=[dict(d, unpriced="two") if d["date"] == "2026-11-12" else d for d in usage_m["days"]])
        check("burn: unpriced of zero is exact, and one that is not a number is a lower bound",
              ("at least" in "\n".join(machine_section(days_m, why_m, zero, 100, since, until, tz)),
               "| 2026-11-12 | at least $20 |" in "\n".join(machine_section(days_m, why_m, odd, 100, since, until, tz))), (False, True))
        # the pace follows the clock across a change of daylight time (Stage E, #505)
        import zoneinfo
        ny = zoneinfo.ZoneInfo("America/New_York")
        dst_since = datetime.datetime(2026, 10, 29, 16, tzinfo=datetime.timezone.utc)
        dst_until = dst_since + datetime.timedelta(days=7)
        dst_usage = dict(usage_m, days=[{"date": "2026-10-%02d" % d, "usd": 1.0, "unpriced": 0} for d in (29, 30, 31)]
                         + [{"date": "2026-11-%02d" % d, "usd": 1.0, "unpriced": 0} for d in (1, 2, 3, 4, 5)])
        saved_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            sec_dst = machine_section(None, "x", dst_usage, 100, dst_since, dst_until)     # the machine's own zone
        finally:
            if saved_tz is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = saved_tz
            time.tzset()
        want_pace = []
        for d in [datetime.date(2026, 10, 29) + datetime.timedelta(days=i) for i in range(8)]:
            end = datetime.datetime.combine(d + datetime.timedelta(days=1), datetime.time.min, tzinfo=ny)
            want_pace.append("%d%%" % round(100 * min(1.0, (end - dst_since).total_seconds() / (dst_until - dst_since).total_seconds())))
        got_pace = [row.split("|")[-2].strip() for row in sec_dst if row.startswith("| 2026-")]
        check("burn: the even pace is the share of the week gone by each local midnight, across the end of daylight time",
              got_pace, want_pace)
        sec_none = "\n".join(machine_section(None, "the config names no health state file", {"week": {}}, 100, since, until, tz))
        check("an unreadable watch file and a bare usage file say so and invent nothing",
              ("Memory and swap are not shown: the config names no health state file." in sec_none,
               "Lowest free" in sec_none, "no figure" in sec_none, "so its use at the reset is unknown" in sec_none), (True, False, True, True))
        sec_empty = "\n".join(machine_section({"2026-11-01": (1.0, 1.0)}, None, usage_m, 100, since, until, tz))
        check("a watch that recorded nothing in the period says so, not zero",
              ("recorded no memory reading in this period" in sec_empty, "Lowest free" in sec_empty), (True, False))
        check("a week the usage file puts above one hundred and fifty percent is not stated",
              "weekly window read" in "\n".join(machine_section(
                  days_m, why_m, dict(usage_m, status={"windows": {"seven_day": {"utilization": 99, "seen_at": "2026-11-17T12:00:00Z"}}}),
                  100, since, until, tz)), False)
        _t2, md_m, _f2 = build(view, usage_m, 100, [], [], ({}, 0, 0, 0), [], at(17, 15), machine_section(days_m, why_m, usage_m, 100, since, until, tz))
        check("the section sits between the cost and the reviews, and a build without it has no heading",
              (md_m.index("## What it cost") < md_m.index("## The machine and the budget") < md_m.index("## What the reviews taught"),
               "## The machine and the budget" in md0), (True, False))
        check("config: the health file must be absolute, and a relative one is refused by key",
              (validate_config(dict(raw, health_state_file="state.json"))[1] != [],
               validate_config(dict(raw, health_state_file=hpath))[0]["health_state_file"], validate_config(raw)[0]["health_state_file"]),
              (True, hpath, ""))
        # the document
        url = write_document(cfg, title, md, http, env)
        check("the document is created in the project and its link returned", (url, [p for u, p in asked if u == LINEAR_API]), (
            "https://linear.app/x/document/sprint-1", [True]))
        for label, reply, needle in (("a refusal", {"data": {"documentCreate": {"success": False}}}, "did not accept"),
                                     ("a refusal that still names a link", {"data": {"documentCreate": {"success": False, "document": {"url": "https://linear.app/x/document/y"}}}}, "did not accept"),
                                     ("a link that is not Linear's", {"data": {"documentCreate": {"success": True, "document": {"url": "https://evil.example/x"}}}}, "did not accept"),
                                     ("an unreachable Linear", "boom", "could not be reached")):
            http.linear = reply
            try:
                write_document(cfg, title, md, http, env)
                got_ = "written"
            except ReviewError as exc:
                got_ = str(exc)
            check("a document Linear %s is a refusal in words" % label, needle in got_, True)
        try:
            write_document(cfg, title, md, http, {})
            got_ = "written"
        except ReviewError as exc:
            got_ = str(exc)
        check("no Linear key is a refusal naming the variable", cfg["linear_key_env"] in got_, True)
        # the whole run
        http.linear = {"data": {"documentCreate": {"success": True, "document": {"url": "https://linear.app/x/document/sprint-1"}}}}
        text, entry = run_review(cfg, view, usage, at(17, 15), http, env)
        check("the message links the document and states the week", ("https://linear.app/x/document/sprint-1" in text,
              "2 pull requests merged (1 on their own)." in text, "$62.50 of the $100 planned" in text,
              "Stage E raised findings on 1 of the 1 it reviewed." in text, entry["url"] is not None, os.path.exists(entry["local"])),
              (True,) * 6)
        with open(entry["local"], encoding="utf-8") as fh:
            saved = fh.read()
        check("the saved copy has the machine and budget section, with the watch file the config names",
              ("## The machine and the budget" in saved, "Lowest free memory: 12% (on 2026-11-12)" in saved), (True, False))
        text_h, entry_h = run_review(hcfg, view, usage_m, at(17, 15), http, env)
        with open(entry_h["local"], encoding="utf-8") as fh:
            saved_h = fh.read()
        check("…and the figures when the watch file is named",
              ("Lowest free memory: 12% (on 2026-11-12)" in saved_h, "Memory and swap are not shown" in saved), (True, True))
        http.linear = "boom"
        text2, entry2 = run_review(cfg, view, usage, at(17, 15), http, env)
        check("a document that cannot be written is said in the message, with the copy's path, never silently",
              ("I could not write the full review to Linear (Linear could not be reached (OSError))" in text2,
               entry2["local"] in text2, entry2["url"]), (True, True, None))
        text3, _e3 = run_review(cfg, view, usage, at(17, 15), http, {cfg["linear_key_env"]: "k"})
        check("no GitHub token still gives a review that says what could not be read",
              ("Some of it could not be read in full" in text3, "Not complete:" in open(_e3["local"]).read()), (True, True))
        banned = set()
        import pipeline_bridge as bridge
        for t in (text, text2, text3):
            banned |= set(w.lower() for w in bridge.BANNED_WORDS_RE.findall(t))
        check("no word the owner's DM keeps out", sorted(banned), [])
        import io
        import contextlib
        import pipeline_standup as standup
        up, sp_file = os.path.join(tmp, "usage.json"), os.path.join(tmp, "standup.json")
        now_l = datetime.datetime.now().astimezone()
        now_utc = now_l.astimezone(datetime.timezone.utc)
        week = {"known": True, "starts_at": _iso(now_utc - datetime.timedelta(days=3)), "resets_at": _iso(now_utc + datetime.timedelta(days=4))}

        def settings_file(**over):
            with open(sp_file, "w", encoding="utf-8") as fh:
                json.dump(dict({"schema": standup.SETTINGS_SCHEMA, "on": True, "usage_file": up, "sprint": True,
                                "days": list(standup.DAY_NAMES)}, **over), fh)

        def put_usage(week_, written=None):
            with open(up, "w", encoding="utf-8") as fh:
                json.dump({"schema": standup.USAGE_SCHEMA, "result": "ok", "week": week_,
                           "written_at": _iso(written or now_utc)}, fh)

        def period(**over):
            settings_file(**over)
            out_ = io.StringIO()
            with contextlib.redirect_stdout(out_):
                rc = print_period(sp_file, now_l)
            return rc, out_.getvalue()
        put_usage(week)
        rc, text_ = period()
        check("period: a fresh usage file that places the week gives the sprint's period, from the stand-up's own days",
              (rc, json.loads(text_)["since"] == week["starts_at"], json.loads(text_)["until"] == week["resets_at"]), (0, True, True))
        check("period: the stand-up's working days decide the count",
              json.loads(period(days=["mon"])[1])["days_total"] != json.loads(period()[1])["days_total"], True)
        put_usage({"known": False})
        check("period: a week the file cannot place is said", (period()[0], "not known" in period()[1]), (1, True))
        put_usage(week, written=now_utc - datetime.timedelta(hours=5))
        check("period: a stale usage file is said, never used", (period()[0], "was last written" in period()[1]), (1, True))
        put_usage(week)
        check("period: with the sprint setting off it says so", (period(sprint=False)[0], "sprint setting is off" in period(sprint=False)[1]), (1, True))
        os.unlink(up)
        check("period: a usage file that is not there is said", (period()[0], "there's no usage file" in period()[1]), (1, True))
        # once a sprint
        check("no record yet is an empty record", load_record(cfg), ({}, None))
        save_record(cfg, {view_id(view): entry})
        check("the record keeps the sprint, and the same sprint is then not due",
              (list(load_record(cfg)[0]), due(view, at(17, 16), at(17, 15), load_record(cfg)[0])), ([view_id(view)], False))
        with open(record_path(cfg), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        check("a record that cannot be read is said, and nothing is sent on a guess", load_record(cfg)[1], "the sprint review's record could not be read")
        check("the screenshots listed are images inside the period only",
              kept_screenshots(cfg, since, until), [])
        os.makedirs(cfg["screenshots_dir"], exist_ok=True)
        for name in ("home.png", "notes.txt", "old.png"):
            with open(os.path.join(cfg["screenshots_dir"], name), "w") as fh:
                fh.write("x")
        os.utime(os.path.join(cfg["screenshots_dir"], "home.png"), (since.timestamp() + 3600,) * 2)
        os.utime(os.path.join(cfg["screenshots_dir"], "old.png"), (since.timestamp() - 86400,) * 2)
        check("…and only those", kept_screenshots(cfg, since, until), ["home.png"])
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        above = fh.read().split("\n# Selftest\n", 1)[0]
    body = above.split('"""', 2)[2]
    check("its only write is one documentCreate with exactly title, content and projectId, and it posts to Linear in one place",
          (len(re.findall(r"mutation\s+\w+", body)), "documentCreate(input: { title: $title, content: $content, projectId: $projectId })" in body,
           body.count("LINEAR_API,")), (1, True, 1))
    check("no shell, no subprocess, no environment read of its own",
          [b for b in ("shell=True", "os.system", "subprocess", "os.getenv") if b in above], [])
    check("the only hosts it names are GitHub's and Linear's",
          sorted(set(re.findall(r"https://[a-z.]+", above.split('"""', 2)[2]))), ["https://api.github.com", "https://api.linear.app", "https://linear.app"])
    if failures:
        for line in failures:
            print(line)
        print("sprint review selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: sprint review selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
