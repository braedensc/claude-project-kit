#!/usr/bin/env python3
"""The sprint: one week, from one reset of the plan's weekly usage window to the next (KIT-574).

    python3 scripts/pipeline_sprint.py --selftest

The owner suggested on 5 Nov that a sprint is naturally one week, because the plan's usage limit
resets weekly. This is the small reader every later piece builds on (planning at the reset, the
review at its end). It reads the usage file (contract section 17) and says where the sprint
stands. It writes nothing, asks nothing of the tracker, and holds no model.

THE BOUNDARY is the usage file's `week.starts_at` and `week.resets_at`. Nothing here names a
weekday or an hour, so the sprint moves when the plan's reset moves.

A WORKING DAY of the sprint is one of the stand-up's `days`. A local date belongs to the sprint
that holds its midday (12:00 local): a reset at 20:00 on a Monday belongs to the sprint that is
ending, and the next one's first day is Tuesday. A sprint with no working day in it is unknown.

A WEEK THE FILE CANNOT PLACE is never a guess and never a zero: `sprint_view` returns
`known: False` and a `why` in words (the week is not known, the file is unreadable, stale or
failed). The line the stand-up shows then says so.

    view_(doc, now, days)   -> a dict, see below
    sprint_line(view, now)        -> one fixed sentence
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _parse(value):
    """A UTC time as the usage file writes it (2026-11-03T18:05:00Z), or None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unknown(why):
    return {"known": False, "why": why, "starts_at": None, "resets_at": None, "day": None,
            "first_day": None, "days_total": None, "days_left": None, "utilization": None}


def _local(dt, tz):
    """`dt` in the local zone AT THAT INSTANT: the system's own zone, so a sprint that crosses a
    daylight-saving change shows each time as the owner's clock does. `tz` pins a fixed zone, for
    the selftest."""
    return dt.astimezone(tz) if tz is not None else dt.astimezone()


def _midday(day, tz):
    naive = datetime(day.year, day.month, day.day, 12)
    return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()


def sprint_view(doc, now, days, tz=None):
    """Where the sprint stands, from a usage file already read and judged fresh, or why not.
    `now` is an aware datetime; local dates are its own timezone's. `days` are the working days.
      known         True when the file places the week and the sprint has a working day
      why           in words, when it is not known
      starts_at, resets_at   aware datetimes in `now`'s timezone
      day           which working day of the sprint today is (1-based), 0 before the first:
                    the week has reset, and today's midday was before it
      first_day     the sprint's first working day (a date)
      days_total    working days in the sprint
      days_left     working days after today
      utilization   how full the weekly window is (0 to 1), or None
    """
    if not isinstance(doc, dict):
        return _unknown("the usage file could not be read")
    if doc.get("result") == "failed":
        return _unknown("the usage job's last pass failed")
    week = doc.get("week") if isinstance(doc.get("week"), dict) else {}
    start, reset = _parse(week.get("starts_at")), _parse(week.get("resets_at"))
    if week.get("known") is not True or start is None or reset is None or not start < reset:
        return _unknown("the usage file can't place the plan's week yet")
    start, reset, now = _local(start, tz), _local(reset, tz), _local(now, tz)
    if not start <= now < reset:
        return _unknown("the usage file's week is not the current one")
    working = [DAY_NAMES.index(d) for d in days if d in DAY_NAMES]
    sprint_days = []
    cursor = start.date()
    while cursor <= reset.date():
        midday = _midday(cursor, tz)
        if start <= midday < reset and cursor.weekday() in working:
            sprint_days.append(cursor)
        cursor += timedelta(days=1)
    if not sprint_days:
        return _unknown("the sprint has no working day in it")
    today = now.date()
    status = doc.get("status") if isinstance(doc.get("status"), dict) else {}
    window = (status.get("windows") or {}).get("seven_day")
    window = window if isinstance(window, dict) else {}
    used = window.get("utilization")
    return {"known": True, "why": "", "starts_at": start, "resets_at": reset,
            "day": len([d for d in sprint_days if d <= today]), "first_day": sprint_days[0],
            "days_total": len(sprint_days),
            "days_left": len([d for d in sprint_days if d > today]),
            "utilization": used if _num(used) and 0 <= used <= 1.5 else None}


def _in(delta):
    hours = int(delta.total_seconds() // 3600)
    if hours >= 24:
        n = hours // 24
        return "in %d day%s" % (n, "" if n == 1 else "s")
    return "in %d hour%s" % (max(hours, 0), "" if hours == 1 else "s")


def sprint_line(view, now):
    """One fixed sentence. Numbers and day names only; no model's words."""
    if not view["known"]:
        return "The sprint is not known: %s." % view["why"]
    if view["day"] == 0:      # reset since today's midday: the new sprint has not had a day yet
        head = "The new sprint's first working day is %s." % view["first_day"].strftime("%a")
    else:
        head = "Sprint day %d of %d." % (view["day"], view["days_total"])
    parts = [head, "The week resets %s, %s." % (view["resets_at"].strftime("%a %H:%M"),
                                                _in(view["resets_at"] - now))]
    if view["utilization"] is not None:
        parts.append("The weekly window is %d%% full." % round(view["utilization"] * 100))
    else:
        parts.append("How full the weekly window is isn't known.")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def _selftest():
    checks, failures = [0], []

    def check(name, got, want):
        checks[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    tz = timezone(timedelta(hours=-5))
    view_ = lambda doc, now, days: sprint_view(doc, now, days, tz=tz)     # noqa: E731
    mon_to_mon = {"result": "ok",
                  "week": {"known": True, "starts_at": "2026-11-04T01:00:00Z",
                           "resets_at": "2026-11-11T01:00:00Z"},
                  "status": {"windows": {"seven_day": {"utilization": 0.38}}}}
    # In -05:00 the sprint is Tue 3 Nov 20:00 to Tue 10 Nov 20:00. Midday dates: Wed 4 to Tue 10.
    days = ["mon", "tue", "wed", "thu", "fri", "sat"]
    at = lambda d, h: datetime(2026, 11, d, h, tzinfo=tz)
    v = view_(mon_to_mon, at(5, 9), days)
    check("known, with its boundary from the file", (v["known"], v["starts_at"], v["resets_at"]),
          (True, at(3, 20), at(10, 20)))
    check("thursday 5 Nov is day 2 of 6 (Wed to Tue, Sunday off)",
          (v["day"], v["days_total"], v["days_left"]), (2, 6, 4))
    check("the line says the day, the reset and how full the window is",
          sprint_line(v, at(5, 9)),
          "Sprint day 2 of 6. The week resets Tue 20:00, in 5 days. The weekly window is 38% full.")
    v = view_(mon_to_mon, at(10, 9), days)
    check("the reset's own morning is the last day", (v["day"], v["days_left"]), (6, 0))
    check("under a day, the line counts hours",
          sprint_line(v, at(10, 9)).split(". ")[1], "The week resets Tue 20:00, in 11 hours")
    v = view_(mon_to_mon, at(4, 7), days)
    check("the first morning is day 1", (v["day"], v["days_left"]), (1, 5))
    check("a different working week gives a different count",
          view_(mon_to_mon, at(5, 9), ["mon", "tue", "wed", "thu", "fri"])["days_total"], 5)

    # The boundary comes from the file: move the reset a day and everything moves.
    later = dict(mon_to_mon, week=dict(mon_to_mon["week"], starts_at="2026-11-05T01:00:00Z",
                                       resets_at="2026-11-12T01:00:00Z"))
    v = view_(later, at(5, 9), days)
    check("the sprint moves when the reset moves", (v["starts_at"], v["resets_at"], v["day"]),
          (at(4, 20), at(11, 20), 1))

    # Stage E's review of #455: a message after the reset, before the new sprint's first day, says
    # so, instead of "day 1"; and times are the local zone's at each instant.
    fresh = dict(mon_to_mon, week=dict(mon_to_mon["week"], starts_at="2026-11-10T18:00:00Z",
                                       resets_at="2026-11-17T18:00:00Z"))
    v = view_(fresh, at(10, 15), days)
    check("after the reset, before the first working day, the day is 0", (v["known"], v["day"],
          v["first_day"].strftime("%a"), v["days_total"]), (True, 0, "Wed", 6))
    check("…and the line says the new sprint has not begun",
          sprint_line(v, at(10, 15)),
          "The new sprint's first working day is Wed. The week resets Tue 13:00, in 6 days. "
          "The weekly window is 38% full.")
    # A fixed offset would show the reset an hour off across a daylight-saving change. Where the
    # machine's zone has no such change in the window, the system zone and a fixed one agree.
    sys_now = datetime(2026, 11, 5, 9).astimezone()
    sys_doc = dict(mon_to_mon, week=dict(
        mon_to_mon["week"],
        starts_at=(sys_now - timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        resets_at=(sys_now + timedelta(days=6)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
    v = sprint_view(sys_doc, sys_now, days)
    check("with no zone given, the reset is shown on the system clock at that instant",
          v["resets_at"].strftime("%H:%M"), (sys_now + timedelta(days=6)).astimezone().strftime("%H:%M"))

    # Never a guess, never a zero.
    unknown_week = dict(mon_to_mon, week={"known": False, "starts_at": None, "resets_at": None})
    for name, doc, why in (
            ("a week the file cannot place", unknown_week,
             "the usage file can't place the plan's week yet"),
            ("a week the job marked not known, times and all", dict(mon_to_mon, week=dict(
                mon_to_mon["week"], known=False)), "the usage file can't place the plan's week yet"),
            ("a failed usage pass", {"result": "failed"}, "the usage job's last pass failed"),
            ("no document", None, "the usage file could not be read"),
            ("a week that is over", dict(mon_to_mon, week=dict(
                mon_to_mon["week"], starts_at="2026-10-20T01:00:00Z",
                resets_at="2026-10-27T01:00:00Z")), "the usage file's week is not the current one"),
            ("a garbled time", dict(mon_to_mon, week=dict(mon_to_mon["week"], starts_at="soon")),
             "the usage file can't place the plan's week yet")):
        v = view_(doc, at(5, 9), days)
        check("%s is not known, and says why" % name, (v["known"], v["why"], v["day"]),
              (False, why, None))
        check("%s: the line says so" % name, sprint_line(v, at(5, 9)),
              "The sprint is not known: %s." % why)
    check("a sprint with no working day is not known",
          view_(mon_to_mon, at(5, 9), [])["why"], "the sprint has no working day in it")
    no_status = {k: v_ for k, v_ in mon_to_mon.items() if k != "status"}
    v = view_(no_status, at(5, 9), days)
    check("an unknown window fill is said, never shown as 0%", sprint_line(v, at(5, 9)).endswith(
        "How full the weekly window is isn't known."), True)
    check("a bool is not a utilization",
          view_(dict(mon_to_mon, status={"windows": {"seven_day": {"utilization": True}}}),
                      at(5, 9), days)["utilization"], None)
    if failures:
        print("FAIL: pipeline_sprint selftest (%d of %d checks failed)" % (len(failures), checks[0]))
        for f in failures:
            print("  - " + f)
        return 1
    print("ok: %d pipeline_sprint checks passed" % checks[0])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    parser.print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
