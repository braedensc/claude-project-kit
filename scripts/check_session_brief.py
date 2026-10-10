#!/usr/bin/env python3
"""The session brief's two machine formats match the machines that read them (KIT-231).

    python3 scripts/check_session_brief.py              # check docs/SESSION-BRIEF.md
    python3 scripts/check_session_brief.py --selftest

docs/SESSION-BRIEF.md shows a session two lines a machine reads: the escalation mark the
notifier pages on, and the `pipeline-finding/1` block the finding job files. A session
copies them word for word, so a brief that drifts from either reader turns every real
escalation into silence. This reads the brief and judges each example with the readers'
OWN code, imported:

  * every `pipeline-escalation` mark the brief shows is one the notifier's MARK_RE reads,
    naming a mark it knows, and `agent:blocked` and `agent:needs-human` are both shown;
  * the brief's fenced finding example parses with the finding poller's `parse_finding`.

Exit 0 when both hold, 1 with every problem named.
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


def problems(text):
    out = []
    shown = _SHOWN_MARK_RE.findall(text)
    if not shown:
        out.append("the brief shows no escalation mark")
    for mark in shown:
        m = notify.MARK_RE.match(mark)
        if not m:
            out.append("the brief shows %r, which the notifier does not read" % mark)
    named = set(notify.MARK_RE.match(m).group(1) for m in shown if notify.MARK_RE.match(m))
    for needed in (notify.ESC_BLOCKED, notify.ESC_NEEDS_HUMAN):
        if needed not in named:
            out.append("the brief never shows the %s mark" % needed)
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
    if failures:
        print("FAIL: check_session_brief selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: check_session_brief selftest (4 cases)")
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
