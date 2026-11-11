#!/usr/bin/env python3
"""The sprint review: a recap of the week, built by code, written as one Linear document and linked from one DM
message on the sprint's last working day (KIT-576).

    python3 scripts/pipeline_sprint_review.py period --usage FILE [--days mon,tue,wed,thu,fri,sat]
    python3 scripts/pipeline_sprint_review.py --example-config
    python3 scripts/pipeline_sprint_review.py --selftest

A sprint is the plan's week (scripts/pipeline_sprint.py). On its last working day, once the afternoon stand-up's time
has come, the stand-up calls this module. It reads three things and no model:
  - the usage file (contract section 17): what the week spent, by kind of work, and the dearest pieces;
  - GitHub: the pull requests merged in the sprint's period, which merged on their own (auto-merge was on), and what
    Stage E's review comment on each said (how many findings, of what kind);
  - the weekly budget the owner set, for the cost against the plan.
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
        "review_dir", "screenshots_dir", "max_pull_requests", "max_pages"}
EXAMPLE = {"schema": CONFIG_SCHEMA, "repos": ["acme/app"], "github_token_env": "SPRINT_REVIEW_GITHUB_TOKEN",
           "linear_key_env": "SPRINT_REVIEW_LINEAR_KEY", "linear_project_id": "00000000-0000-0000-0000-000000000000",
           "weekly_budget_usd": 100, "review_dir": "~/.stage-e/sprint-review", "screenshots_dir": "",
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
    budget = raw.get("weekly_budget_usd")
    if budget is not None and (not _num(budget) or budget <= 0):
        bad.append("weekly_budget_usd must be a number above 0, or null")
    for key in ("review_dir",):
        if not isinstance(raw.get(key), str) or not os.path.isabs(os.path.expanduser(raw.get(key) or "")):
            bad.append("%s must be an absolute path (~/ is allowed)" % key)
    shots = raw.get("screenshots_dir", "")
    if not isinstance(shots, str) or (shots and not os.path.isabs(os.path.expanduser(shots))):
        bad.append("screenshots_dir must be an absolute path (~/ is allowed), or empty")
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


def due(view, now, afternoon, sprints):
    """True when today is the sprint's last working day, the afternoon time has come, and this sprint has not been
    reviewed. `view` is the sprint view for today; `now` and `afternoon` are aware local datetimes."""
    if not view.get("known") or view["day"] < 1 or view["days_left"] != 0:
        return False
    return now >= afternoon and view_id(view) not in sprints


def print_period(usage_path, days):
    """The sprint's period for /weekly-review --sprint: 0 and one JSON line, or 1 and the reason. The usage file
    must be the job's own, fresh enough to name the current week."""
    try:
        with open(os.path.expanduser(usage_path), encoding="utf-8") as fh:
            usage = json.load(fh)
    except (OSError, ValueError):
        print("the sprint is not known: the usage file could not be read")
        return 1
    view = sprint.sprint_view(usage, datetime.datetime.now(datetime.timezone.utc), days)
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
def gather_pulls(cfg, since, until, http=None, environ=None):
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
    for repo in cfg["repos"]:
        done = False
        for page in range(1, cfg["max_pages"] + 1):
            url = "%s/repos/%s/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=%d" % (GITHUB_API, repo, page)
            try:
                rows = http(url, headers)
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


def review_counts(cfg, pulls, http=None, environ=None, clock=time.monotonic):
    """Stage E's review comment on each merged pull request, as counts: ({category: n}, reviewed, with findings,
    not read). Only the finding lines' categories are read, from a closed list; the comment's words are not kept."""
    http = http or transport._http_json
    environ = os.environ if environ is None else environ
    headers = {"Authorization": "Bearer %s" % environ.get(cfg["github_token_env"], ""),
               "Accept": "application/vnd.github+json", "User-Agent": "sprint-review"}
    cats, reviewed, flagged, unread = {}, 0, 0, 0
    stop = clock() + COMMENTS_SECONDS
    for p in pulls[:cfg["max_pull_requests"]]:
        if clock() > stop:
            unread += 1
            continue
        try:
            rows = http("%s/repos/%s/issues/%d/comments?per_page=100" % (GITHUB_API, p["repo"], p["number"]), headers)
        except Exception:  # noqa: BLE001
            unread += 1
            continue
        mine = [r for r in rows if isinstance(r, dict) and isinstance(r.get("body"), str) and "stage-e-review" in r["body"]] \
            if isinstance(rows, list) else []
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


def build(view, usage, budget, pulls, partial, reviews, shots, now):
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
              "", "## What it cost", ""] + cost_lines + ["", "## What the reviews taught", ""]
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
                   {"query": query, "variables": {"title": title, "content": markdown, "projectId": cfg["linear_project_id"]}})
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


def run_review(cfg, view, usage, now, http=None, environ=None):
    """Builds and writes the review, and returns (message text, record entry). The caller records BEFORE posting.
    A failure to write the document is the message; a failure to read GitHub is in the document."""
    since, until = view["starts_at"], view["resets_at"]
    try:
        got = gather_pulls(cfg, since, until, http, environ)
        reviews = review_counts(cfg, got["pulls"], http, environ)
        pulls, partial = got["pulls"], got["partial"]
    except ReviewError as exc:
        pulls, partial, reviews = [], [str(exc)], ({}, 0, 0, 0)
    title, markdown, facts = build(view, usage, cfg["weekly_budget_usd"], pulls, partial, reviews,
                                   kept_screenshots(cfg, since, until), now)
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
    per.add_argument("--usage", required=True)
    per.add_argument("--days", default="mon,tue,wed,thu,fri,sat")
    args = ap.parse_args(argv)
    if args.cmd == "period":
        return print_period(args.usage, [d for d in args.days.split(",") if d])
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
                                    ("a page cap of 99", {"max_pages": 99}, "max_pages")):
            check("config: %s is refused, by key" % label, any(needle in e for e in validate_config(dict(raw, **over))[1]), True)
        check("a key given as a value is never quoted back",
              "lin_api_abc" in " ".join(validate_config(dict(raw, linear_key_env="lin_api_abc"))[1]), False)
        # when it is due
        aft = at(17, 15)
        check("due: the last working day, after the afternoon time, not yet reviewed", due(view, at(17, 15), aft, {}), True)
        check("not due: before the afternoon time", due(view, at(17, 14), aft, {}), False)
        check("not due: a day that is not the last", due(mid, at(12, 16), at(12, 15), {}), False)
        check("not due: this sprint was reviewed", due(view, at(17, 16), aft, {view_id(view): {}}), False)
        check("due again for the next sprint", due(view, at(17, 16), aft, {"2026-11-03T18:00:00Z": {}}), True)
        check("not due: a sprint the usage file cannot place", due({"known": False}, at(17, 16), aft, {}), False)
        # GitHub, stood in for
        since, until = view["starts_at"], view["resets_at"]
        pr = lambda n, merged, auto=False, title="Fix it", upd=None: {          # noqa: E731
            "number": n, "title": title, "merged_at": merged, "updated_at": upd or merged or "2026-11-12T00:00:00Z",
            "auto_merge": {"merge_method": "squash"} if auto else None}
        review = lambda *cats: {"body": "## Stage E review <!-- stage-e-review -->\n" + "\n".join(
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
        up = os.path.join(tmp, "usage.json")
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        week = {"known": True, "starts_at": _iso(now_utc - datetime.timedelta(days=3)), "resets_at": _iso(now_utc + datetime.timedelta(days=4))}
        for label, doc, code in (("a week the file places", {"result": "ok", "week": week}, 0),
                                 ("a week it cannot", {"result": "ok", "week": {"known": False}}, 1)):
            with open(up, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            out_ = io.StringIO()
            with contextlib.redirect_stdout(out_):
                rc = print_period(up, days + ["sun"])
            check("period: %s" % label, (rc, out_.getvalue().startswith("{") if code == 0 else "not known" in out_.getvalue()), (code, True))
        out_ = io.StringIO()
        with contextlib.redirect_stdout(out_):
            rc = print_period(os.path.join(tmp, "none.json"), days)
        check("period: a usage file that is not there is said, not guessed", (rc, "could not be read" in out_.getvalue()), (1, True))
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
