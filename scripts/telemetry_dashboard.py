#!/usr/bin/env python3
"""Pipeline dashboard — telemetry tables → one summary object → one HTML page.

ONE SUMMARY, TWO CONSUMERS. This file computes a single structured summary and
renders it. `--json` prints that same object. `/weekly-review` reads the JSON;
the human reads the HTML. Neither reads the other, and nothing recomputes a
number the other one already has — so the model and the person are provably
reasoning about the same figures. A review that scraped the rendered page (or
recomputed its own totals from raw rows) could disagree with what the human is
looking at, and neither party would know which of them was wrong.

That property is the reason `summarize()` is the only place a metric is defined,
and why `render_html()` takes the summary rather than the rows.

EXPECT TO REVISE THESE METRIC CHOICES. The first version of a metric set is a
guess about which numbers will turn out to matter, and real data usually
disagrees. So the metrics are declared as data in `METRICS` below — one entry per
headline number, each naming the function that computes it — and adding,
removing or redefining one is a single edit in a single place. Nothing in the
renderer knows what a metric *means*; it lays out whatever `METRICS` declares.

THE METRIC THAT ACTUALLY MATTERS is cost per merged PR. Spend alone says how
much was consumed, and throughput alone says how much arrived; only the ratio
says whether the pipeline is worth running. It is deliberately the largest tile
on the page. Its companion is the human-share of cycle time: if a pipeline is
cheap per PR but every PR sits for two days waiting on a person, the bottleneck
is not the model and no amount of budget fixes it.

REPORTING, NOT AUTHORITY (§4). Every number here descends from agent-authored
telemetry. Nothing on this page may gate a budget, an approval or a merge —
`budgets.dailyUsd` is metered against the dispatcher's own ledger (§9). The page
is for deciding what to change, by people who can change it.

SELF-CONTAINED BY CONSTRUCTION. No external stylesheet, script, font or image;
charts are inline SVG. The file opens from a local disk with no network at all,
which is what makes it safe to email, archive next to a retro, or read on a
plane. Theme follows `prefers-color-scheme`.

Usage:
    telemetry_dashboard.py --config PATH [--days N] [--from-json PATH]
                           [--out PATH] [--json]
    telemetry_dashboard.py --selftest

Exit: 0 = rendered, 1 = the store was unreachable, 2 = usage error.
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telemetry_scrape import (  # noqa: E402
    SUPPORTED_VERSION, _connect, telemetry_config,
)

# `meta` excludes a ticket from throughput metrics (§6: "the pipeline working on
# itself … so pipeline overhead never reads as delivery"). The scrape does not
# record labels, so the exclusion is applied by whoever passes `--exclude`.
DEFAULT_DAYS = 7


# --------------------------------------------------------------------------- #
# Reading — the single query path. Both consumers come through here.
# --------------------------------------------------------------------------- #
SELECTS = {
    "runs": """
        SELECT run_id, ticket_id, team_key, stage, model, auth_mode,
               started_at, ended_at, tokens_in, tokens_out, cost_usd, turns,
               outcome, error_class, files_changed, lines_added, lines_removed,
               pr_number
          FROM {s}.runs
         WHERE COALESCE(started_at, ingested_at) >= %s
    """,
    "ticket_events": """
        SELECT ticket_id, event, at, actor
          FROM {s}.ticket_events
         WHERE at >= %s
    """,
    "review_findings": """
        SELECT finding_id, ticket_id, pr_number, severity, category, file, line,
               summary, at
          FROM {s}.review_findings
         WHERE COALESCE(at, ingested_at) >= %s
    """,
}


def collect(dsn, schema, since):
    """The one read. Returns {table: [row dicts]} for the window."""
    conn = _connect(dsn)
    out = {}
    try:
        cur = conn.cursor()
        for table, sql in SELECTS.items():
            cur.execute(sql.format(s=schema), (since,))
            cols = [d[0] for d in cur.description]
            out[table] = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return out


def collect_from_json(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {t: list(data.get(t) or []) for t in SELECTS}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def as_dt(value):
    """Tolerate datetimes from the driver and ISO strings from a JSON fixture."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def hours(a, b):
    if not a or not b or b < a:
        return None
    return (b - a).total_seconds() / 3600.0


def fmt_usd(v):
    return "${:,.2f}".format(num(v))


def fmt_hours(v):
    if v is None:
        return "—"
    if v < 1:
        return "%d min" % round(v * 60)
    if v < 48:
        return "%.1f h" % v
    return "%.1f d" % (v / 24.0)


# --------------------------------------------------------------------------- #
# Metric definitions — the one place to change what this page reports.
#
# Each entry: key, label, the function that computes {value, display, note},
# and `emphasis` (a metric rendered as the hero tile). The renderer reads this
# list and knows nothing else about any metric.
# --------------------------------------------------------------------------- #
def m_spend(d):
    spent = d["_spend"]
    budget = d["_budget"]
    pct = (spent / budget * 100.0) if budget else None
    return {
        "value": round(spent, 4),
        "budget": round(budget, 2) if budget else None,
        "pct_of_budget": round(pct, 1) if pct is not None else None,
        "display": fmt_usd(spent),
        "note": ("%.0f%% of the %s period budget" % (pct, fmt_usd(budget)))
                if pct is not None else "no budgets.dailyUsd configured",
    }


def m_merged(d):
    n = len(d["_merged_tickets"])
    return {"value": n, "display": str(n),
            "note": "ticket(s) reaching `merged` in the window"}


def m_cost_per_merged_pr(d):
    n = len(d["_merged_tickets"])
    spent = d["_spend"]
    value = round(spent / n, 2) if n else None
    return {
        "value": value,
        "display": fmt_usd(value) if value is not None else "—",
        "note": ("%s across %d merged ticket(s)" % (fmt_usd(spent), n)) if n
                else "nothing merged in this window — spend bought no delivery",
    }


def m_bounce_rate(d):
    dispatched = d["_tickets_dispatched"]
    bounced = d["_tickets_bounced"]
    rate = (len(bounced) / len(dispatched) * 100.0) if dispatched else None
    return {
        "value": round(rate, 1) if rate is not None else None,
        "bounced": len(bounced), "dispatched": len(dispatched),
        "display": ("%.0f%%" % rate) if rate is not None else "—",
        "note": "%d of %d dispatched ticket(s) needed a fix round"
                % (len(bounced), len(dispatched)),
    }


def m_wasted(d):
    runs = d["_wasted_runs"]
    cost = round(sum(num(r.get("cost_usd")) for r in runs), 2)
    return {
        "value": len(runs), "cost_usd": cost,
        "display": str(len(runs)),
        "note": ("%s spent on runs that produced no PR" % fmt_usd(cost)) if runs
                else "every run that spent tokens produced a PR",
    }


def m_human_share(d):
    cycle = d["_cycle"]
    share = cycle.get("human_share_pct")
    return {
        "value": share,
        "display": ("%.0f%%" % share) if share is not None else "—",
        "note": "of end-to-end cycle time is spent waiting on a person",
    }


METRICS = [
    {"key": "cost_per_merged_pr", "label": "Cost per merged PR", "fn": m_cost_per_merged_pr,
     "emphasis": True,
     "why": "Spend alone says how much was consumed; throughput alone says how much "
            "arrived. Only the ratio says whether the pipeline is worth running."},
    {"key": "spend", "label": "Spend this period", "fn": m_spend, "emphasis": False,
     "why": "Self-reported by sessions, for dashboards only — the enforced cap is "
            "metered by the dispatcher (contract §1, §9)."},
    {"key": "merged", "label": "Tickets merged", "fn": m_merged, "emphasis": False,
     "why": "Counted from `merged` ticket events, whose actor is never an agent."},
    {"key": "bounce_rate", "label": "Bounce rate", "fn": m_bounce_rate, "emphasis": False,
     "why": "A bounce is one review-or-CI round trip that needed a fresh fix session."},
    {"key": "human_share", "label": "Waiting on a human", "fn": m_human_share,
     "emphasis": False,
     "why": "If this dominates, the bottleneck is not the model and no budget fixes it."},
    {"key": "wasted_runs", "label": "Runs with no PR", "fn": m_wasted, "emphasis": False,
     "why": "Runs that spent tokens and opened nothing. The cheapest thing to fix first."},
]

# Cycle-time phases, in order. Each is (key, label, start events, end events).
# The first matching event of each list wins, so a ticket that skips a milestone
# still yields a phase rather than a gap.
PHASES = [
    ("waiting_on_human", "Waiting on a human", ["created"], ["approved", "dispatched"]),
    ("working", "Working", ["approved", "dispatched"], ["pr_opened"]),
    ("review", "Review", ["pr_opened"], ["review_posted", "ci_green", "merged"]),
    ("waiting_to_merge", "Waiting to merge", ["review_posted", "ci_green"], ["merged"]),
]
HUMAN_PHASES = ("waiting_on_human", "waiting_to_merge")


# --------------------------------------------------------------------------- #
# Summarize
# --------------------------------------------------------------------------- #
def cycle_times(events_by_ticket):
    """Median hours per phase, plus the human share of the total.

    Median, not mean: one ticket parked over a long weekend would otherwise
    dominate every phase it touched and make the chart say nothing about the
    ordinary case.
    """
    buckets = {key: [] for key, _, _, _ in PHASES}
    complete = 0
    for _, events in events_by_ticket.items():
        first = {}
        for e in events:
            at = as_dt(e.get("at"))
            name = e.get("event")
            if at and (name not in first or at < first[name]):
                first[name] = at
        got_any = False
        for key, _, starts, ends in PHASES:
            start = next((first[s] for s in starts if s in first), None)
            end = next((first[e] for e in ends if e in first), None)
            span = hours(start, end)
            if span is not None:
                buckets[key].append(span)
                got_any = True
        if got_any and "merged" in first and "created" in first:
            complete += 1

    def median(xs):
        if not xs:
            return None
        xs = sorted(xs)
        mid = len(xs) // 2
        return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0

    phases = []
    for key, label, _, _ in PHASES:
        phases.append({
            "key": key, "label": label,
            "median_hours": round(median(buckets[key]), 2) if buckets[key] else None,
            "samples": len(buckets[key]),
            "human": key in HUMAN_PHASES,
        })
    total = sum(p["median_hours"] or 0 for p in phases)
    human = sum(p["median_hours"] or 0 for p in phases if p["human"])
    return {
        "phases": phases,
        "total_median_hours": round(total, 2) if total else None,
        "human_median_hours": round(human, 2) if human else None,
        "human_share_pct": round(human / total * 100.0, 1) if total else None,
        "tickets_with_full_cycle": complete,
    }


def summarize(data, config, since, until, exclude=()):
    """rows → the one summary object. Both the HTML and /weekly-review read this."""
    exclude = set(exclude or ())
    runs = [r for r in data.get("runs") or [] if (r.get("ticket_id") or "") not in exclude]
    events = [e for e in data.get("ticket_events") or [] if (e.get("ticket_id") or "") not in exclude]
    findings = [f for f in data.get("review_findings") or [] if (f.get("ticket_id") or "") not in exclude]

    days = max(1, round((until - since).total_seconds() / 86400.0))
    daily = num(((config or {}).get("budgets") or {}).get("dailyUsd"))

    events_by_ticket = {}
    for e in events:
        events_by_ticket.setdefault(e.get("ticket_id"), []).append(e)

    merged = sorted({e.get("ticket_id") for e in events
                     if e.get("event") == "merged" and e.get("ticket_id")})
    dispatched = sorted({r.get("ticket_id") for r in runs
                         if r.get("stage") in ("dev", "bounce") and r.get("ticket_id")})
    bounced = sorted({r.get("ticket_id") for r in runs
                      if r.get("stage") == "bounce" and r.get("ticket_id")})
    wasted = [r for r in runs
              if r.get("stage") in ("dev", "bounce")
              and int(r.get("tokens_out") or 0) > 0
              and not r.get("pr_number")]

    by_ticket = {}
    for r in runs:
        tid = r.get("ticket_id") or "(no ticket)"
        slot = by_ticket.setdefault(tid, {"ticket_id": tid, "cost_usd": 0.0, "runs": 0,
                                          "tokens_out": 0, "merged": False})
        slot["cost_usd"] += num(r.get("cost_usd"))
        slot["runs"] += 1
        slot["tokens_out"] += int(r.get("tokens_out") or 0)
    for tid in merged:
        if tid in by_ticket:
            by_ticket[tid]["merged"] = True
    for slot in by_ticket.values():
        slot["cost_usd"] = round(slot["cost_usd"], 4)

    derived = {
        "_spend": round(sum(num(r.get("cost_usd")) for r in runs), 4),
        "_budget": round(daily * days, 2) if daily else 0.0,
        "_merged_tickets": merged,
        "_tickets_dispatched": dispatched,
        "_tickets_bounced": bounced,
        "_wasted_runs": wasted,
        "_cycle": cycle_times(events_by_ticket),
    }

    by_category = {}
    for f in findings:
        cat = (f.get("category") or "uncategorized").strip() or "uncategorized"
        slot = by_category.setdefault(cat, {"category": cat, "total": 0, "by_severity": {}})
        slot["total"] += 1
        sev = (f.get("severity") or "unknown")
        slot["by_severity"][sev] = slot["by_severity"].get(sev, 0) + 1

    outcomes = {}
    for r in runs:
        outcomes[r.get("outcome") or "unknown"] = outcomes.get(r.get("outcome") or "unknown", 0) + 1

    summary = {
        "schema": "pipeline-dashboard/1",
        "period": {
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days": days,
        },
        "totals": {
            "runs": len(runs),
            "tickets_touched": len({r.get("ticket_id") for r in runs if r.get("ticket_id")}),
            "tokens_in": sum(int(r.get("tokens_in") or 0) for r in runs),
            "tokens_out": sum(int(r.get("tokens_out") or 0) for r in runs),
            "turns": sum(int(r.get("turns") or 0) for r in runs),
            "events": len(events),
            "findings": len(findings),
        },
        "metrics": {},
        "cycle_time": derived["_cycle"],
        "findings_by_category": sorted(by_category.values(),
                                       key=lambda c: (-c["total"], c["category"])),
        "run_outcomes": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])),
        "no_pr_runs": [
            {"run_id": r.get("run_id"), "ticket_id": r.get("ticket_id"),
             "stage": r.get("stage"), "outcome": r.get("outcome"),
             "error_class": r.get("error_class"),
             "cost_usd": round(num(r.get("cost_usd")), 4),
             "tokens_out": int(r.get("tokens_out") or 0)}
            for r in sorted(wasted, key=lambda r: -num(r.get("cost_usd")))[:20]
        ],
        "most_expensive_tickets": sorted(
            by_ticket.values(), key=lambda t: -t["cost_usd"])[:3],
        "excluded_tickets": sorted(exclude),
    }
    for spec in METRICS:
        summary["metrics"][spec["key"]] = dict(spec["fn"](derived), label=spec["label"],
                                               why=spec["why"])
    return summary


# --------------------------------------------------------------------------- #
# Render — self-contained HTML. No external assets of any kind.
# --------------------------------------------------------------------------- #
CSS = """
:root {
  --bg: #ffffff; --fg: #16181d; --muted: #5c6370; --line: #e3e6ea;
  --card: #f7f8fa; --accent: #2f5bd8; --warn: #b4531a; --good: #1d7a4c;
  --human: #b4531a; --agent: #2f5bd8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --fg: #e8eaed; --muted: #9aa1ac; --line: #2a2e35;
    --card: #1c1f25; --accent: #7aa2f7; --warn: #e0a05a; --good: #6cc292;
    --human: #e0a05a; --agent: #7aa2f7;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--fg);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1040px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
h2 { font-size: 1.05rem; margin: 2.5rem 0 .75rem; letter-spacing: -.01em; }
.sub { color: var(--muted); margin: 0 0 2rem; font-size: .9rem; }
.grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 1rem; }
.card .label { color: var(--muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }
.card .value { font-size: 1.9rem; font-weight: 620; margin: .35rem 0 .15rem; letter-spacing: -.02em; }
.card .note { color: var(--muted); font-size: .82rem; }
.card.hero { grid-column: 1 / -1; border-color: var(--accent); }
.card.hero .value { font-size: 3rem; color: var(--accent); }
.card .why { color: var(--muted); font-size: .78rem; margin-top: .6rem; border-top: 1px solid var(--line); padding-top: .5rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.scroll { overflow-x: auto; }
.bar { display: flex; height: 26px; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); }
.bar span { display: block; }
.legend { display: flex; flex-wrap: wrap; gap: 1rem; margin-top: .6rem; font-size: .84rem; color: var(--muted); }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: .4rem; }
.empty { color: var(--muted); font-style: italic; padding: .75rem 0; }
.pill { display: inline-block; padding: .1rem .45rem; border-radius: 999px; font-size: .75rem; border: 1px solid var(--line); }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line); color: var(--muted); font-size: .8rem; }
"""


def esc(v):
    return html.escape("" if v is None else str(v))


def card(metric, hero=False):
    return (
        '<div class="card{cls}"><div class="label">{label}</div>'
        '<div class="value">{value}</div><div class="note">{note}</div>'
        '<div class="why">{why}</div></div>'
    ).format(
        cls=" hero" if hero else "", label=esc(metric["label"]),
        value=esc(metric["display"]), note=esc(metric["note"]), why=esc(metric["why"]),
    )


def cycle_bar(cycle):
    phases = [p for p in cycle["phases"] if p["median_hours"]]
    if not phases:
        return '<p class="empty">Not enough lifecycle events yet to split cycle time.</p>'
    total = sum(p["median_hours"] for p in phases)
    segments, legend = [], []
    for p in phases:
        pct = p["median_hours"] / total * 100.0
        colour = "var(--human)" if p["human"] else "var(--agent)"
        segments.append(
            '<span style="width:%.4f%%;background:%s" title="%s: %s"></span>'
            % (pct, colour, esc(p["label"]), esc(fmt_hours(p["median_hours"])))
        )
        legend.append(
            '<span><i style="background:%s"></i>%s — %s (%.0f%%, n=%d)</span>'
            % (colour, esc(p["label"]), esc(fmt_hours(p["median_hours"])), pct, p["samples"])
        )
    share = cycle.get("human_share_pct")
    head = (
        '<p class="sub" style="margin:0 0 .6rem">Median end-to-end '
        "%s, of which <strong>%s is waiting on a person</strong>.</p>"
        % (esc(fmt_hours(cycle.get("total_median_hours"))),
           esc(("%.0f%%" % share) if share is not None else "an unknown share"))
    )
    return head + '<div class="bar">%s</div><div class="legend">%s</div>' % (
        "".join(segments), "".join(legend))


def findings_table(rows):
    if not rows:
        return '<p class="empty">No review findings recorded in this period.</p>'
    severities = ["critical", "high", "medium", "low"]
    head = "".join('<th class="num">%s</th>' % s for s in severities)
    body = []
    for r in rows:
        cells = "".join(
            '<td class="num">%d</td>' % r["by_severity"].get(s, 0) for s in severities)
        body.append('<tr><td>%s</td><td class="num">%d</td>%s</tr>'
                    % (esc(r["category"]), r["total"], cells))
    return ('<div class="scroll"><table><thead><tr><th>Category</th>'
            '<th class="num">Total</th>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (head, "".join(body)))


def no_pr_table(rows):
    if not rows:
        return '<p class="empty">Every run that spent tokens produced a PR.</p>'
    body = "".join(
        '<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td>'
        '<td class="num">%s</td><td class="num">%s</td></tr>'
        % (esc(r["run_id"]), esc(r["ticket_id"] or "—"), esc(r["stage"]),
           esc(r["outcome"] + (" / " + r["error_class"] if r["error_class"] else "")),
           esc("{:,}".format(r["tokens_out"])), esc(fmt_usd(r["cost_usd"])))
        for r in rows)
    return ('<div class="scroll"><table><thead><tr><th>Run</th><th>Ticket</th>'
            '<th>Stage</th><th>Outcome</th><th class="num">Tokens out</th>'
            '<th class="num">Cost</th></tr></thead><tbody>%s</tbody></table></div>' % body)


def expensive_table(rows):
    if not rows:
        return '<p class="empty">No runs recorded in this period.</p>'
    body = "".join(
        '<tr><td>%s</td><td class="num">%s</td><td class="num">%d</td>'
        '<td><span class="pill">%s</span></td></tr>'
        % (esc(r["ticket_id"]), esc(fmt_usd(r["cost_usd"])), r["runs"],
           "merged" if r["merged"] else "not merged")
        for r in rows)
    return ('<div class="scroll"><table><thead><tr><th>Ticket</th>'
            '<th class="num">Cost</th><th class="num">Runs</th><th>State</th>'
            '</tr></thead><tbody>%s</tbody></table></div>' % body)


def outcomes_table(outcomes):
    if not outcomes:
        return '<p class="empty">No runs recorded in this period.</p>'
    body = "".join('<tr><td>%s</td><td class="num">%d</td></tr>' % (esc(k), v)
                   for k, v in outcomes.items())
    return ('<div class="scroll"><table><thead><tr><th>Outcome</th>'
            '<th class="num">Runs</th></tr></thead><tbody>%s</tbody></table></div>' % body)


def render_html(summary):
    """summary → one self-contained page. Reads nothing but the summary."""
    p = summary["period"]
    t = summary["totals"]
    metrics = summary["metrics"]
    hero = [s for s in METRICS if s.get("emphasis")]
    rest = [s for s in METRICS if not s.get("emphasis")]
    cards = "".join(card(metrics[s["key"]], hero=True) for s in hero if s["key"] in metrics)
    cards += "".join(card(metrics[s["key"]]) for s in rest if s["key"] in metrics)

    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline dashboard — {since} to {until}</title>
<style>{css}</style></head>
<body><div class="wrap">
<h1>Delivery pipeline</h1>
<p class="sub">{since} → {until} ({days} day period) · {runs} run(s) across
{tickets} ticket(s) · {tin} tokens in / {tout} out · {turns} turns</p>

<div class="grid">{cards}</div>

<h2>Cycle time</h2>
{cycle}

<h2>Review findings by category</h2>
{findings}

<h2>Runs that spent tokens and produced no PR</h2>
{nopr}

<h2>Three most expensive tickets</h2>
{expensive}

<h2>Run outcomes</h2>
{outcomes}

<footer>
Generated from the <code>runs</code>, <code>ticket_events</code> and
<code>review_findings</code> tables (PIPELINE-CONTRACT §4, §10). Every figure
descends from agent-authored telemetry and is <strong>reporting, not
authority</strong>: nothing here gates a budget, an approval or a merge. The
enforced spend cap is metered by the dispatcher's own ledger (§9).
{excluded}
</footer>
</div></body></html>
""".format(
        css=CSS, since=esc(p["since"][:10]), until=esc(p["until"][:10]), days=p["days"],
        runs=t["runs"], tickets=t["tickets_touched"],
        tin="{:,}".format(t["tokens_in"]), tout="{:,}".format(t["tokens_out"]),
        turns=t["turns"], cards=cards,
        cycle=cycle_bar(summary["cycle_time"]),
        findings=findings_table(summary["findings_by_category"]),
        nopr=no_pr_table(summary["no_pr_runs"]),
        expensive=expensive_table(summary["most_expensive_tickets"]),
        outcomes=outcomes_table(summary["run_outcomes"]),
        excluded=(" Excluded from throughput: %s."
                  % esc(", ".join(summary["excluded_tickets"])))
        if summary["excluded_tickets"] else "",
    )


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def fixture():
    return {
        "runs": [
            {"run_id": "r_1", "ticket_id": "ENG-1", "stage": "dev", "outcome": "completed",
             "cost_usd": 2.0, "tokens_in": 100, "tokens_out": 50, "turns": 10,
             "started_at": "2026-08-18T09:00:00Z", "pr_number": 11},
            {"run_id": "r_2", "ticket_id": "ENG-2", "stage": "dev", "outcome": "completed",
             "cost_usd": 5.0, "tokens_in": 200, "tokens_out": 90, "turns": 30,
             "started_at": "2026-08-19T09:00:00Z", "pr_number": 12},
            {"run_id": "r_3", "ticket_id": "ENG-2", "stage": "bounce", "outcome": "completed",
             "cost_usd": 1.5, "tokens_in": 60, "tokens_out": 20, "turns": 8,
             "started_at": "2026-08-19T15:00:00Z", "pr_number": 12},
            {"run_id": "r_4", "ticket_id": "ENG-3", "stage": "dev", "outcome": "blocked",
             "error_class": "needs_clarification", "cost_usd": 0.75,
             "tokens_in": 40, "tokens_out": 15, "turns": 4,
             "started_at": "2026-08-20T09:00:00Z", "pr_number": None},
            {"run_id": "r_5", "ticket_id": "ENG-9", "stage": "review", "outcome": "completed",
             "cost_usd": 0.4, "tokens_in": 30, "tokens_out": 10, "turns": 2,
             "started_at": "2026-08-20T10:00:00Z", "pr_number": 12},
        ],
        "ticket_events": [
            {"ticket_id": "ENG-1", "event": "created", "at": "2026-08-17T09:00:00Z", "actor": "human"},
            {"ticket_id": "ENG-1", "event": "approved", "at": "2026-08-18T09:00:00Z", "actor": "human"},
            {"ticket_id": "ENG-1", "event": "pr_opened", "at": "2026-08-18T11:00:00Z", "actor": "agent"},
            {"ticket_id": "ENG-1", "event": "ci_green", "at": "2026-08-18T11:30:00Z", "actor": "system"},
            {"ticket_id": "ENG-1", "event": "merged", "at": "2026-08-18T17:30:00Z", "actor": "human"},
            {"ticket_id": "ENG-2", "event": "created", "at": "2026-08-18T09:00:00Z", "actor": "human"},
            {"ticket_id": "ENG-2", "event": "approved", "at": "2026-08-19T09:00:00Z", "actor": "human"},
            {"ticket_id": "ENG-2", "event": "pr_opened", "at": "2026-08-19T12:00:00Z", "actor": "agent"},
            {"ticket_id": "ENG-2", "event": "review_posted", "at": "2026-08-19T13:00:00Z", "actor": "agent"},
            {"ticket_id": "ENG-2", "event": "merged", "at": "2026-08-20T13:00:00Z", "actor": "human"},
        ],
        "review_findings": [
            {"finding_id": "f_1", "ticket_id": "ENG-2", "pr_number": 12, "severity": "medium",
             "category": "correctness", "summary": "off-by-one", "at": "2026-08-19T13:00:00Z"},
            {"finding_id": "f_2", "ticket_id": "ENG-2", "pr_number": 12, "severity": "low",
             "category": "tests", "summary": "weak assertion", "at": "2026-08-19T13:00:00Z"},
            {"finding_id": "f_3", "ticket_id": "ENG-1", "pr_number": 11, "severity": "high",
             "category": "correctness", "summary": "unhandled null", "at": "2026-08-18T11:00:00Z"},
        ],
    }


def selftest():
    failures = []
    cases = [0]

    def check(label, cond, detail=""):
        cases[0] += 1
        if not cond:
            failures.append("%s%s" % (label, (": " + detail) if detail else ""))

    since = datetime(2026, 8, 17, tzinfo=timezone.utc)
    until = datetime(2026, 8, 24, tzinfo=timezone.utc)
    config = {"version": 1, "budgets": {"dailyUsd": 10.0}}
    s = summarize(fixture(), config, since, until)

    # ── Totals and spend ────────────────────────────────────────────────────
    check("period is 7 days", s["period"]["days"] == 7, str(s["period"]))
    check("spend sums every run", s["metrics"]["spend"]["value"] == 9.65,
          str(s["metrics"]["spend"]["value"]))
    check("budget is dailyUsd x days", s["metrics"]["spend"]["budget"] == 70.0)
    check("budget percentage is computed", s["metrics"]["spend"]["pct_of_budget"] == 13.8,
          str(s["metrics"]["spend"]["pct_of_budget"]))

    # ── The headline metric ─────────────────────────────────────────────────
    check("two tickets merged", s["metrics"]["merged"]["value"] == 2)
    check("cost per merged PR is spend / merged",
          s["metrics"]["cost_per_merged_pr"]["value"] == 4.83,
          str(s["metrics"]["cost_per_merged_pr"]["value"]))

    # ── Bounce rate ─────────────────────────────────────────────────────────
    br = s["metrics"]["bounce_rate"]
    check("one of three dispatched tickets bounced",
          br["bounced"] == 1 and br["dispatched"] == 3, json.dumps(br))
    check("bounce rate is a percentage", br["value"] == 33.3, str(br["value"]))

    # ── Runs that produced nothing ──────────────────────────────────────────
    w = s["metrics"]["wasted_runs"]
    check("one run spent tokens and opened no PR", w["value"] == 1, json.dumps(w))
    check("its cost is called out", w["cost_usd"] == 0.75)
    check("the run is listed", s["no_pr_runs"][0]["run_id"] == "r_4")
    check("a review-stage run is not counted as wasted",
          all(r["run_id"] != "r_5" for r in s["no_pr_runs"]))

    # ── Cycle time ──────────────────────────────────────────────────────────
    c = s["cycle_time"]
    named = {p["key"]: p for p in c["phases"]}
    check("all four phases are present", len(c["phases"]) == 4)
    check("waiting-on-human is 24h median", named["waiting_on_human"]["median_hours"] == 24.0,
          str(named["waiting_on_human"]))
    check("working phase is measured", named["working"]["median_hours"] == 2.5,
          str(named["working"]))
    check("human phases are flagged as human",
          named["waiting_on_human"]["human"] and named["waiting_to_merge"]["human"])
    check("agent phases are not", not named["working"]["human"] and not named["review"]["human"])
    check("human share is computed", c["human_share_pct"] is not None)
    check("both tickets completed a full cycle", c["tickets_with_full_cycle"] == 2,
          str(c["tickets_with_full_cycle"]))

    # ── Findings ────────────────────────────────────────────────────────────
    cats = {r["category"]: r for r in s["findings_by_category"]}
    check("findings group by category", set(cats) == {"correctness", "tests"}, str(list(cats)))
    check("the busiest category sorts first",
          s["findings_by_category"][0]["category"] == "correctness")
    check("severities are broken out within a category",
          cats["correctness"]["by_severity"] == {"medium": 1, "high": 1},
          json.dumps(cats["correctness"]))

    # ── Most expensive ──────────────────────────────────────────────────────
    top = s["most_expensive_tickets"]
    check("at most three expensive tickets", len(top) == 3)
    check("the priciest ticket sums its runs",
          top[0]["ticket_id"] == "ENG-2" and top[0]["cost_usd"] == 6.5, json.dumps(top[0]))
    check("merged state is carried", top[0]["merged"] is True)

    # ── Exclusions (the `meta` label case) ──────────────────────────────────
    ex = summarize(fixture(), config, since, until, exclude=["ENG-9"])
    check("excluding a ticket drops its runs", ex["totals"]["runs"] == 4)
    check("excluding a ticket lowers spend", ex["metrics"]["spend"]["value"] == 9.25,
          str(ex["metrics"]["spend"]["value"]))
    check("the exclusion is disclosed", ex["excluded_tickets"] == ["ENG-9"])

    # ── Empty data must render, not crash ───────────────────────────────────
    empty = summarize({"runs": [], "ticket_events": [], "review_findings": []},
                      config, since, until)
    check("no data yields no cost-per-PR", empty["metrics"]["cost_per_merged_pr"]["value"] is None)
    check("no data yields a dash, not an error",
          empty["metrics"]["cost_per_merged_pr"]["display"] == "—")
    check("no data yields no bounce rate", empty["metrics"]["bounce_rate"]["value"] is None)
    empty_html = render_html(empty)
    check("an empty dashboard still renders", "<html" in empty_html and len(empty_html) > 800)
    check("empty sections say so", "Not enough lifecycle events" in empty_html)

    # ── The page is genuinely self-contained ────────────────────────────────
    page = render_html(s)
    check("page renders", page.startswith("<!DOCTYPE html>") and page.rstrip().endswith("</html>"))
    for pattern, why in (
        (r"<script", "no scripts"),
        (r"https?://", "no absolute URLs"),
        (r"<link\b", "no external stylesheets"),
        (r"<img\b", "no images"),
        (r"@import", "no CSS imports"),
        (r"url\(", "no url() references"),
    ):
        check("self-contained: %s" % why, re.search(pattern, page, re.I) is None,
              "found %s" % pattern)
    check("the hero metric is the cost per merged PR",
          page.index("Cost per merged PR") < page.index("Spend this period"))
    check("theme handling is present", "prefers-color-scheme" in page)

    # ── Summary → HTML has no second source of truth ────────────────────────
    check("the rendered page shows the summary's own figure",
          s["metrics"]["cost_per_merged_pr"]["display"] in page,
          s["metrics"]["cost_per_merged_pr"]["display"])
    check("the summary is JSON-serializable (the /weekly-review contract)",
          json.loads(json.dumps(s))["schema"] == "pipeline-dashboard/1")

    # ── The scrape writes what this reads. Assert it, don't assume it. ──────
    # Two files, one table shape: the collector's column tuples and this file's
    # SELECT lists are exactly the "second shape for the same structure" the
    # contract exists to prevent, and nothing else would catch a rename.
    import telemetry_scrape as scrape

    written = {
        "runs": set(scrape.RUN_COLUMNS),
        "ticket_events": set(scrape.EVENT_COLUMNS),
        "review_findings": set(scrape.FINDING_COLUMNS),
    }
    for table, sql in SELECTS.items():
        selected = {
            c.strip() for c in
            re.search(r"SELECT(.*?)FROM", sql, re.S).group(1).replace("\n", " ").split(",")
        }
        missing = sorted(selected - written[table])
        check("dashboard reads only columns the scrape writes (%s)" % table,
              not missing, "not written: %s" % ", ".join(missing))
        ddl = scrape.DDL.format(s="pipeline")
        undeclared = sorted(c for c in selected
                            if not re.search(r"\b%s\b" % re.escape(c), ddl))
        check("every column the dashboard selects exists in the DDL (%s)" % table,
              not undeclared, "not in DDL: %s" % ", ".join(undeclared))

    # ── End to end, with no database: comment → parsed rows → summary ───────
    doc = {
        "schema": scrape.TELEMETRY_SCHEMA,
        "runs": [dict(scrape.GOOD_RUN, cost_usd=3.0, pr_number=7)],
        "ticket_events": [
            {"ticket_id": "ENG-123", "event": "created", "at": "2026-08-18T09:00:00Z",
             "actor": "human"},
            {"ticket_id": "ENG-123", "event": "merged", "at": "2026-08-19T09:00:00Z",
             "actor": "human"}],
        "review_findings": [
            {"severity": "high", "category": "security", "summary": "token logged"}],
    }
    sink = scrape.DrySink("pipeline")
    swept = scrape.sweep([scrape.block_comment(doc)], sink)
    check("end-to-end: the comment parses cleanly", swept["stats"]["skipped"] == 0,
          "; ".join(swept["skipped"]))
    rows = {t: [dict(zip(cols, v)) for v in sink.rows[t]]
            for t, cols in (("runs", scrape.RUN_COLUMNS),
                            ("ticket_events", scrape.EVENT_COLUMNS),
                            ("review_findings", scrape.FINDING_COLUMNS))}
    e2e = summarize(rows, config, since, until)
    check("end-to-end: spend arrives from the parsed row",
          e2e["metrics"]["spend"]["value"] == 3.0, str(e2e["metrics"]["spend"]["value"]))
    check("end-to-end: the merged event is counted",
          e2e["metrics"]["merged"]["value"] == 1)
    check("end-to-end: cost per merged PR computes",
          e2e["metrics"]["cost_per_merged_pr"]["value"] == 3.0)
    check("end-to-end: the finding is grouped by category",
          [c["category"] for c in e2e["findings_by_category"]] == ["security"],
          json.dumps(e2e["findings_by_category"]))
    check("end-to-end: the page renders from real parsed rows",
          "<html" in render_html(e2e))

    # ── Every declared metric is computed ───────────────────────────────────
    for spec in METRICS:
        check("METRICS entry %s is computed" % spec["key"], spec["key"] in s["metrics"])
        check("METRICS entry %s carries a display string" % spec["key"],
              isinstance(s["metrics"][spec["key"]].get("display"), str))

    if failures:
        print("FAIL: %d of %d dashboard case(s) failed:" % (len(failures), cases[0]))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: %d dashboard case(s) passed" % cases[0])
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="path to delivery.json")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="period length (default: 7)")
    ap.add_argument("--from-json", help="read rows from a JSON file instead of the store")
    ap.add_argument("--exclude", nargs="*", default=[], help="ticket IDs to leave out (e.g. `meta`)")
    ap.add_argument("--out", help="write the HTML page here")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="print the summary object — what /weekly-review reads")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    config_path = args.config or "delivery.json"
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as fh:
                config = json.load(fh)
        except (OSError, ValueError) as e:
            print("FAIL: %s is present but unreadable: %s" % (config_path, e), file=sys.stderr)
            return 2
        if config.get("version") != SUPPORTED_VERSION:
            print("FAIL: %s declares version %r; this reader implements contract version %d"
                  % (config_path, config.get("version"), SUPPORTED_VERSION), file=sys.stderr)
            return 2

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days)

    if args.from_json:
        try:
            data = collect_from_json(args.from_json)
        except (OSError, ValueError) as e:
            print("FAIL: cannot read %s: %s" % (args.from_json, e), file=sys.stderr)
            return 2
    else:
        settings, reason = telemetry_config(config)
        if settings is None:
            print("Telemetry is not configured: %s" % reason)
            return 0
        dsn = os.environ.get(settings["dsn_env"])
        if not dsn:
            print("FAIL: $%s is not set — telemetry.dsnEnv names it" % settings["dsn_env"],
                  file=sys.stderr)
            return 1
        try:
            data = collect(dsn, settings["schema"], since)
        except Exception as e:
            print("FAIL: could not read the telemetry store: %s" % e, file=sys.stderr)
            return 1

    summary = summarize(data, config, since, until, args.exclude)

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(render_html(summary))
        except OSError as e:
            print("FAIL: cannot write %s: %s" % (args.out, e), file=sys.stderr)
            return 2
        print("Wrote %s (%d run(s), %d merged, %s per merged PR)"
              % (args.out, summary["totals"]["runs"],
                 summary["metrics"]["merged"]["value"],
                 summary["metrics"]["cost_per_merged_pr"]["display"]))
    if args.as_json or not args.out:
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
