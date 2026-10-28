# The catch-up poller — a delegation the dispatcher missed is started late, not lost

*Written 2026-10-27 (KIT-47). Script: `scripts/pipeline_catchup_poller.py`.*

The dispatcher hears about work only from the tracker's webhooks. It never polls. If you
delegate a ticket while the dispatcher's machine is asleep or off, the tracker retries the
delivery three times — after 1 minute, 1 hour and 6 hours — and then gives up. A machine
that sleeps longer than that loses the delegation for good, and nothing says so.

The catch-up poller is the polling the dispatcher lacks. It runs every 15 minutes as the
dispatcher's role account, finds a delegation the dispatcher never answered, and starts it
by replying once in that delegation's own thread.

---

## What it does on each pass

1. **Checks the route.** It replies only if the dispatcher answers on this machine and,
   when configured, the public front door answers too. A machine half-awake in the night
   may have a network but no running dispatcher, and a reply sent then would reach nothing.
   While the route is down it still looks, so nothing it reported goes quiet.
2. **Checks the webhook.** It asks the tracker whether the dispatcher's webhook is still
   enabled. The tracker can switch off a webhook that keeps failing, and then every later
   delegation is lost too.
3. **Finds delegations nobody answered.** A delegation counts only if all of this is true:
   - it belongs to the dispatcher's agent, and the agent never wrote anything in it — no
     thought, action, answer, error or question;
   - it never started, ended or was dismissed;
   - it is the agent's newest session on that ticket. If you restarted a lost delegation
     yourself, only your new one counts;
   - its ticket is on one of the configured teams, is not a planning ticket, is neither done
     nor canceled, and is still delegated to the agent;
   - it has a person who delegated it, and a thread to reply in;
   - it is older than **7 hours 30 minutes**, and younger than a week.
4. **Replies once in the thread**, with the owner's tracker key: "This delegation was made
   while the dispatcher could not be reached… treat it as a new delegation." The tracker
   delivers that reply to the dispatcher, which starts the work.

At most three replies per pass, a minute apart. More are left for the next pass and named
in the log, never dropped.

---

## Why it waits 7 hours 30 minutes

The tracker's own retries cover 7 hours. If this job replied sooner, and the tracker's own
retry then arrived, the dispatcher would start the same ticket twice: it has no duplicate
check. So the job waits until the tracker has given up. A shorter outage needs nothing from
it — the tracker's retry does the job.

The script refuses any wait inside the tracker's 7-hour retry window. Making it shorter
needs a live test first (see *What is not proven*).

---

## One reply per delegation, ever

- **The ledger** (`~/.stage-e/catchup/ledger.json`) records each delegation as `owed`
  before the reply and `posted` after it. A damaged ledger stops the job: it is the record
  that prevents a second reply.
- **The thread is the backstop.** Every reply ends with a plain line,
  `Catch-up receipt: <session id>`. Before replying, the job reads the thread for that line
  in a reply written by its own key. A reply that landed but lost its answer, or a lost
  ledger, is never sent twice. A line someone else pasted does not count.
- **One pass at a time.** A pass started by hand beside the scheduled one does nothing.
- **A reply that fails** stays owed, fails the pass loudly, and goes to the back of the
  line. It never holds up the others.
- **A delegation still silent an hour after its reply is stuck.** It is never replied to
  again. It is reported on every pass until a person acts on it: answers it, ends it,
  removes the delegation or closes the ticket. The dispatcher sometimes stays silent on
  purpose — no repository matched, the person who delegated is blocked — and only a person
  can tell which.

---

## How it tells you something is wrong

It writes a heartbeat (`~/.stage-e/catchup/heartbeat.json`) on every real pass, including a
pass that failed or crashed. The heartbeat monitor reads it like the other Stage E jobs
(`docs/HEARTBEAT-MONITOR.md`):

| What it found | What you see |
| --- | --- |
| A disabled webhook | One page, naming how to switch it back on |
| A delegation still silent after its reply | One page per ticket, naming it |
| The session list was cut short (`max_pages`) | One page: a lost delegation could be beyond it |
| The route down for 8 passes in a row | One page: a dead dispatcher, or a wrong URL in the config |
| A pass that could not finish | A page saying why, in the job's own words |
| The webhook could not be checked | Nothing pages; the monitor's row says it could not check |
| The route down for a short while | Nothing pages here; the health watch, if you installed it, pages that outage |
| The job itself stopped | The monitor pages it as stale |

To switch a disabled webhook back on: in the tracker, open Settings → API → OAuth
applications → the dispatcher's app → Webhooks, and enable it. Then delegate a throwaway
ticket to prove it.

---

## Install it

The Stage E installer installs it as a fourth Stage E daemon, from `stage-e.conf`:

| Key | Default | What it is |
| --- | --- | --- |
| `CATCHUP_INTERVAL_SECONDS` | `900` | How often a pass runs |
| `CATCHUP_DISPATCHER_URL` | `http://127.0.0.1:3456` | The dispatcher's own address on this machine |
| `CATCHUP_FRONT_DOOR_URL` | empty | The public status address; empty judges by the local answer alone |
| `CATCHUP_WEBHOOK_ID` | empty | Set only when the job says it sees several app webhooks; it names them |

The teams are the installer's `MANAGED_TEAM_KEYS`. The review team is never caught up: the
review poller times its own sessions out. It uses the tracker key Stage E already stores, so
it needs no new credential. `--example-config` prints every key of its own config file.

**Two things can stop the installer before the job first runs:**

- If the dry run cannot reach the dispatcher, the URL is wrong. Fix it and run again.
- **Card `CK-11`.** The first real pass replies to every unanswered delegation from the
  past week, and each reply starts a paid session. The installer lists them. Remove the
  delegation from any you do not want started, run again, and sign the count:
  `attest A-CATCHUP-COUNT`.

To see what it would do, as the role account:

```bash
sudo -u <ROLE_ACCOUNT> -H /bin/sh -c 'cd / && set -a; . ~/.stage-e/env; set +a; /usr/bin/python3 ~/.stage-e/kit/scripts/pipeline_catchup_poller.py run --config ~/.stage-e/catchup/poller.json --dry-run'
```

A dry run replies to nothing and writes nothing, not even its heartbeat.

---

## What it never does

Its only tracker write is a reply under a delegation's own first comment. It creates no
ticket, moves no state, sets no label, delegates nothing, never asks for a webhook's secret,
and never touches a pull request or a chat token. The selftest checks this against the
script's own source.

Chat messages are out of scope. Replaying a missed chat message would need the chat app's
signing secret, which could forge any message.

---

## What is not proven

- A real lost delegation has never been recovered. The test: stop the dispatcher for more
  than 7.5 hours, delegate a throwaway ticket, start it again, and watch the job reply and
  the dispatcher start the work. No ticket yet.
- That a late tracker retry, arriving after the job's reply, does not start a second run.
  The wait is chosen so it cannot happen; it has never been tested. No ticket yet.
- That the tracker lists the dispatcher's webhook to the owner's key. If it does not, the
  webhook check always reads "could not check". The first dry run says which. No ticket yet.
- What status the tracker gives a delegation nobody answered. The job does not depend on it;
  it judges by activity and timestamps. No ticket yet.
