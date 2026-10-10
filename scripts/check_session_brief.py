#!/usr/bin/env python3
"""The session brief's two machine formats match the machines that read them (KIT-231).

    python3 scripts/check_session_brief.py              # check docs/SESSION-BRIEF.md
    python3 scripts/check_session_brief.py --selftest

docs/SESSION-BRIEF.md shows a session two lines a machine reads: the escalation mark the
notifier pages on, and the `pipeline-finding/1` block the finding job files. A session
copies them word for word, so a brief that drifts from either reader turns every real
escalation into silence. This reads the brief and judges each example with the readers'
OWN code, imported:

  * every `pipeline-escalation` mark the brief shows is one the notifier's `find_mark`
    reads, and one its `is_authorised` accepts from a session (some marks it takes only
    from the plan executor or the heartbeat monitor); `agent:blocked` and
    `agent:needs-human` are both shown;
  * the brief's worked example comment carries its mark where `find_mark` looks for it:
    the comment's first line;
  * the brief's fenced finding example parses with the finding poller's `parse_finding`.

Exit 0 when all three hold, 1 with every problem named.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
import pipeline_finding_poller as finding  # noqa: E402

BRIEF = os.path.join(os.path.dirname(HERE), "docs", "SESSION-BRIEF.md")
_SHOWN_MARK_RE = re.compile(r"<!--\s*pipeline-escalation:[^>]*-->")
_FINDING_FENCE_RE = re.compile(r"````markdown\n(```json\n.*?\n```)\n````", re.S)
# A worked example comment: a ```markdown block. Only one showing a mark is judged as an
# escalation, so the finding example and any plain comment example are left alone.
_EXAMPLE_RE = re.compile(r"```markdown\n(.*?)\n```", re.S)
# The notifier takes some marks only from the plan executor or the heartbeat monitor. A
# session is neither, so its marks are judged as coming from an author on neither list.
SESSION_AUTHOR = "a-session"
_OTHER_ACTORS = {"executor_actor_ids": ["the-plan-executor"],
                 "monitor_actor_ids": ["the-heartbeat-monitor"]}


def problems(text):
    out = []
    shown = _SHOWN_MARK_RE.findall(text)
    if not shown:
        out.append("the brief shows no escalation mark")
    named = set()
    for mark in shown:
        name = notify.find_mark(mark)
        if not name:
            out.append("the brief shows %r, which the notifier does not read" % mark)
        elif not notify.is_authorised(name, SESSION_AUTHOR, _OTHER_ACTORS):
            out.append("the brief tells a session to write %r, which the notifier refuses "
                       "from a session" % mark)
        else:
            named.add(name)
    for needed in (notify.ESC_BLOCKED, notify.ESC_NEEDS_HUMAN):
        if needed not in named:
            out.append("the brief never shows the %s mark" % needed)
    examples = [b for b in _EXAMPLE_RE.findall(text) if "pipeline-escalation" in b]
    if not examples:
        out.append("the brief shows no example comment carrying an escalation mark")
    for body in examples:
        if not notify.find_mark(body):
            out.append("the brief's example comment does not carry its mark on the first "
                       "line, so the notifier would not read it")
    fences = _FINDING_FENCE_RE.findall(text)
    if not fences:
        out.append("the brief shows no fenced pipeline-finding/1 example")
    for block in fences:
        if finding.parse_finding(block) is None:
            out.append("the brief's finding example does not parse with the finding job's "
                       "own parser")
    return out


def selftest():
    failures = []
    text = open(BRIEF, encoding="utf-8").read()
    if problems(text):
        failures.append("the committed brief: %s" % "; ".join(problems(text)))
    reworded = text.replace("pipeline-escalation: agent:blocked", "pipeline-escalation: blocked")
    if not problems(reworded):
        failures.append("a reworded mark passed")
    no_human = text.replace("agent:needs-human -->", "agent:blocked -->")
    if not any("needs-human" in p for p in problems(no_human)):
        failures.append("a brief without the needs-human mark passed")
    broken = text.replace('{"schema": "pipeline-finding/1"', '{"schema": "pipeline-finding/2"')
    if not problems(broken):
        failures.append("a finding example of the wrong schema passed")

    # The two drifts a pattern check alone misses. Each silences every copied escalation.
    # 1. The worked example's mark moved off the first line: the notifier reads only that.
    opening = "\n```markdown\n"
    if opening not in text:
        failures.append("could not find the brief's example comment to build a case on")
    else:
        start = text.index(opening) + len(opening)
        end = text.index("\n```", start)
        first, rest = text[start:end].split("\n", 1)
        off_first = text[:start] + rest + "\n" + first + text[end:]
        if not any("first line" in p for p in problems(off_first)):
            failures.append("an example comment with its mark off the first line passed")
    # 2. A row telling a session to write a mark the notifier takes only from the executor.
    risky_row = "| The change would touch a path the project marks as risky |"
    executor_row = ("| You need the owner's input on the plan | "
                    "`<!-- pipeline-escalation: planning-needs-input -->` |\n")
    if risky_row not in text:
        failures.append("could not find the brief's mark table to build a case on")
    elif not any("refuses from a session" in p
                 for p in problems(text.replace(risky_row, executor_row + risky_row, 1))):
        failures.append("a mark the notifier refuses from a session passed")
    # 3. An example the check can no longer find is a check that judges nothing.
    renamed = text.replace(opening, "\n```md\n", 1)
    if not any("no example comment" in p for p in problems(renamed)):
        failures.append("a brief whose example comment the check cannot find passed")
    # …while an example that is not an escalation (a plain comment) is not judged as one.
    plain = text + "\n```markdown\nOpened PR #12 for this ticket.\n```\n"
    if problems(plain):
        failures.append("an example comment with no mark was judged as an escalation: %s"
                        % "; ".join(problems(plain)))

    if failures:
        print("FAIL: check_session_brief selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: check_session_brief selftest (8 cases)")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    found = problems(open(BRIEF, encoding="utf-8").read())
    if found:
        print("FAIL: docs/SESSION-BRIEF.md and the machines that read it disagree:")
        for p in found:
            print("  - %s" % p)
        return 1
    print("OK: the session brief's marks and finding block match their readers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
