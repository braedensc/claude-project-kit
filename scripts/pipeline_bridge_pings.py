#!/usr/bin/env python3
"""The Slack bridge answers a dev's ping (KIT-224): a member's reply under one, passed on.

    python3 scripts/pipeline_bridge_pings.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports every name it had, so `pipeline_bridge.X` is still the very same
object. Its cases run in the bridge's own selftest, end to end through one pass; this
module's selftest pins what it may reach.

WHAT IS HERE
  `answer_ping`  a verified member's reply under a recorded ping becomes one comment in the
                 session thread recorded with it, as the owner, which resumes the session,
                 under the notifier's own relay rules.
  Refused, and said  a reply under a ping the bridge holds no record of, under a page that
                 waits on no answer, or while the notifier records no pings.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
from pipeline_bridge_config import (EXIT_USAGE, iso, key_of, load_dm_posts,  # noqa: E402
                                    ts_float)
from pipeline_bridge_requests import account_refusal, message_refusal  # noqa: E402

# A reply under a ping the bridge holds no record of, in a channel or a DM. Its last
# sentence says where to answer instead, so it is the bind, never written by the model.
UNRECORDED_PING_SAYS = ("I can't pass that on to a session: this ping was not recorded for "
                        "the bridge, so I can't tell which session is waiting. Pings sent "
                        "before BRIDGE_PINGS=yes are not recorded, and neither is one the "
                        "notifier failed to record (it pages that one again).")
UNRECORDED_PING_BIND = "Answer under the newer ping, or in the tracker."
UNRECORDED_PING = UNRECORDED_PING_SAYS + " " + UNRECORDED_PING_BIND
# KIT-332: a reply under a dev's question in a DM while the notifier records no pings, so
# no answer can reach any session. Its fix is a setting, said by name.
PINGS_OFF_SAYS = ("I can't pass that on to the dev: answering a dev from this DM isn't "
                  "switched on here. The notifier needs BRIDGE_PINGS=yes in its settings "
                  "before I can tell which session is waiting.")
PINGS_OFF_BIND = "Until then, answer in the tracker."


class PingsMixin(object):
    """Pass's answers to a dev's ping (KIT-312)."""

    def answer_ping(self, channel, msg, ping):
        """A reply under a recorded agent:blocked ping: the member's answer to the session
        that asked, passed on as one comment in that session's own thread (KIT-224)."""
        thread, ticket = msg["thread_ts"], ping.get("ticket_id") or "the ticket"

        def refuse(why, what, how=""):
            # `how`, when given, says where the answer goes instead: the bind, after a space.
            if how:
                self.post(channel, thread, "I can't pass that on to %s's session: %s."
                          % (ticket, why), kind="ping-refused", bind=how, join=" ",
                          facts={"ticket": ticket})
            else:
                self.post_refusal(channel, thread, "I can't pass that on to %s's session: %s."
                                  % (ticket, why), why, "ping-refused", ticket)
            self.mark(channel, msg, "refused answer: %s" % what)
            self.counts["refused"] += 1
            self.say("  refused an answer for %s: %s" % (ticket, what))

        why = message_refusal(msg, self.me)
        if why is None:
            why = account_refusal(self.account(msg["user"]), self.me, self.cfg)
        if why:
            refuse(why, why)
            return
        if not self.cfg["act"]:
            refuse("acting is switched off here, so it was not sent", "acting is off",
                   how="Answer in the tracker.")
            return
        if not self.cfg["agent_user_name"].strip():
            refuse("this bridge names no agent user (agent_user_name), so it cannot check "
                   "the session thread", "no agent user")
            return
        typed = notify.decode_chat_entities(msg.get("text") or "")
        if len(typed) > self.cfg["relay_max_chars"]:
            refuse("it is %d characters, over the limit of %d"
                   % (len(typed), self.cfg["relay_max_chars"]), "too long",
                   how="A long answer belongs in the tracker.")
            return
        text = notify.clean_relay_text(msg.get("text") or "")
        if not text:
            refuse("it is empty once its angle brackets are removed", "empty")
            return
        risk = notify.bracket_risk(text)
        if risk:
            refuse("the cleaned text %s" % risk, "bracket risk")
            return
        leaked = notify.secret_hits(text)
        if leaked:
            refuse("it carries a credential shape (%s), which would land in a session's "
                   "prompt" % ", ".join(leaked), "credential shape")
            return
        hour_ago = self.now - 3600
        recent = [t for t in self.state["relays"] if t > hour_ago]
        if len(recent) >= self.cfg["relay_max_per_hour"]:
            refuse("I have passed on %d answers in the last hour, which is my limit"
                   % len(recent), "rate limit")
            return
        if not ping.get("thread_id"):
            refuse("its mark was not written inside a session's thread, so no session is "
                   "waiting on this answer", "no thread")
            return
        # The pings file names the ticket; the bridge acts only on its own teams, as for
        # every other action. The notifier's team list may differ from this one.
        issue = self.tracker.issue(ticket)
        team = ((issue or {}).get("team") or {}).get("key")
        if not issue:
            refuse("I can't find %s in the tracker" % ticket, "not found")
            return
        if team not in self.cfg["team_keys"]:
            refuse("%s is on %s, and the bridge answers sessions only on %s"
                   % (ticket, team or "no team", ", ".join(self.cfg["team_keys"])),
                   "team not answered")
            return
        agent_id, why = self.tracker.agent_user(self.cfg["agent_user_name"])
        if not agent_id:
            refuse(why, "agent user")
            return
        issue_uuid, thread_id = self.tracker.recorded_thread(ticket, ping["thread_id"],
                                                             agent_id)
        if not issue_uuid or not thread_id:
            refuse("the session thread recorded with this ping is gone, or is no longer the "
                   "agent's", "thread gone")
            return
        if self.dry:
            self.say("  [dry-run] would pass an answer on to %s" % ticket)
            return
        uid = msg["user"]
        # The name is the member's own to set: the same cleaner as the answer, so a routing
        # tag or a bounce marker in it reaches the session inert.
        name = notify.clean_relay_text(self.name_of(uid)) or uid
        body = ("This is an answer from **%s** (Slack user %s), passed on by the Slack bridge "
                "from %s; they sent it at %s.\n\n%s"
                % (name, uid, "their DM with it" if channel in self.dm_channels
                   else "the pings channel", iso(ts_float(msg.get("ts"))), text))
        # AT MOST ONCE: recorded and saved as pending before the post, so a post whose
        # answer never came back is never sent twice; it is named instead.
        key = key_of(channel, msg.get("ts"))
        self.state["relay_pending"][key] = {"ticket": ticket, "at": self.now,
                                            "thread": thread, "channel": channel}
        self.state["relays"].append(self.now)
        self.mark(channel, msg, "answer pending: %s" % ticket)
        self.save()
        try:
            self.tracker.reply_in_thread(issue_uuid, thread_id, body)
        except Exception as exc:                                   # noqa: BLE001
            # It says where to answer inside its last sentence, so all of it is the bind.
            self.post(channel, thread, "", kind="ping-unconfirmed", bind="I could not confirm "
                      "that your answer reached %s's session. I won't send it again: check the "
                      "ticket, and answer there if it is missing." % ticket)
            self.say("  UNCONFIRMED answer for %s: %s" % (ticket, str(exc)[:160]))
            return
        self.state["relay_pending"].pop(key, None)
        self.mark(channel, msg, "answered: %s" % ticket)
        self.post(channel, thread, "Sent to %s's session as <@%s>'s answer. It carries on "
                  "when the dispatcher picks it up." % (ticket, uid), kind="ping-sent",
                  facts={"ticket": ticket})
        self.counts["relayed"] += 1
        self.say("  answered: %s, from %s" % (ticket, uid))

    def unrecorded_ping(self, channel, msg, thread):
        """A verified member's reply under a post of the notifier's own bot that the pings
        file does not hold: refused and said, never silently ignored. True when handled."""
        if channel in self.dm_channels:
            return self.unwaited_page(channel, msg, thread)
        notifier = self.cfg["notifier_bot_user_id"]
        root = next((m for m in self.threads.get((channel, thread), [])
                     if m.get("ts") == thread), None)
        if not notifier or not root or root.get("user") != notifier:
            return False
        if message_refusal(msg, self.me) or account_refusal(self.account(msg["user"]),
                                                            self.me, self.cfg):
            return False
        self.post(channel, thread, UNRECORDED_PING_SAYS, kind="ping-unrecorded",
                  bind=UNRECORDED_PING_BIND, join=" ")
        self.mark(channel, msg, "refused answer: ping not recorded")
        self.counts["refused"] += 1
        self.say("  refused an answer under an unrecorded ping, thread %s" % thread)
        return True

    def unwaited_page(self, channel, msg, thread):
        """In a DM the notifier posts as this app (KIT-295), so its pages are told apart by
        its ledger, not by a bot of its own. A verified member's reply under a page it
        recorded that the pings file does not hold reaches no session, and neither does one
        under a post of this app that neither job recorded (a page the notifier failed to
        record): refused and said, never silently ignored. True when handled."""
        root = next((m for m in self.threads.get((channel, thread), [])
                     if m.get("ts") == thread), None)
        if not root or root.get("user") != self.me["user_id"]:
            return False
        # Read as each reply is handled, as the pings file is: a page the notifier records
        # while this pass runs is still found.
        self.dm_posts = load_dm_posts(self.cfg)
        paged = key_of(channel, thread) in self.dm_posts
        if not paged and key_of(channel, thread) in self.state["posted"]:
            return False
        if message_refusal(msg, self.me) or account_refusal(self.account(msg["user"]),
                                                            self.me, self.cfg):
            return False
        if not paged:
            self.post(channel, None, UNRECORDED_PING_SAYS, kind="ping-unrecorded",
                      bind=UNRECORDED_PING_BIND, join=" ")
            self.mark(channel, msg, "refused answer: ping not recorded")
            self.counts["refused"] += 1
            self.say("  refused an answer under an unrecorded post, thread %s" % thread)
            return True
        if (self.dm_posts.get(key_of(channel, thread)) or {}).get("mark") == notify.ESC_BLOCKED:
            # A dev's question the pings file does not hold (KIT-332). Telling the owner to
            # reply under that same kind of message would send them round in a circle.
            if not os.path.exists(self.cfg["pings_file"]):
                self.post(channel, None, PINGS_OFF_SAYS, kind="pings-off",
                          bind=PINGS_OFF_BIND, join=" ")
                self.mark(channel, msg, "refused answer: answering is off (no pings file)")
            else:
                self.post(channel, None, UNRECORDED_PING_SAYS, kind="ping-unrecorded",
                          bind=UNRECORDED_PING_BIND, join=" ")
                self.mark(channel, msg, "refused answer: ping not recorded")
            self.counts["refused"] += 1
            self.say("  refused an answer under a dev's question it can't relay, thread %s"
                     % thread)
            return True
        self.post(channel, None, "I can't pass that on to a session: that message is not "
                  "waiting on an answer.", kind="page-not-waiting", bind="To answer a dev, "
                  "reply under the message saying it stopped to ask you something, or answer "
                  "in the tracker.", join=" ")
        self.mark(channel, msg, "refused answer: page not waiting on an answer")
        self.counts["refused"] += 1
        self.say("  refused an answer under a page not waiting on one, thread %s" % thread)
        return True


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline. This module's cases run in the bridge's own selftest, end to end
# through one pass; this one pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__))


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    print("usage: pipeline_bridge_pings.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
