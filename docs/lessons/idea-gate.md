# The idea gate, explained

*A lesson for someone who has never seen it. Written 2026-11-11.*

This is a teaching document, not a manual. It explains what the idea gate is, why it is shaped the way it is, and how to tell whether it is working. The guide and the runbook sit beside it for the day you need to operate or change it.

**The one line.** You write a rough idea as a ticket on your project's board and move it into a column called Plan it. A few minutes later a proposed epic and its child tickets are waiting for you to approve or throw away.

The last two sections say what has been watched working and what has not.

**Four words you need before anything else.**

- **The tracker** is where tickets live. A *ticket* is one unit of work with a title, a description and a list of acceptance criteria. Each project has a *team*: a board of its own tickets.
- **The session runner** is a program on a Mac. When a ticket is handed to its agent, it starts an AI session in a sandbox and gives it that ticket.
- **An epic** is a parent ticket. Its children are the pieces of work that add up to it.
- **The backlog** is the column where nothing starts. A ticket there is a proposal.

---

## The problem it solves

Breaking an idea into tickets is slow, and it is the part you are worst at last thing on a Friday. "Let sessions file follow-up findings" is one sentence. Turning it into six tickets someone can pick up cold takes an hour of reading code. Each needs acceptance criteria, a test plan and real file paths.

An AI session can do that reading. The catch is what it does next. A session that can write to the tracker can also decide its own work is approved. The whole design answers one question: **how do you let a machine propose work without letting it authorise any?**

The answer is a relay. Nobody in the chain holds both the ability to write a plan and the ability to bless one.

---

## One idea's journey

Follow a single idea from your keyboard to an approved epic. Eight stops.

### Stop 1: you write it on your project's board, and move it

You write a ticket on the project's own board, right beside its real work: *"Let a session file follow-up findings it meets while working."* Two sentences is plenty. Then you drag it into **Plan it**.

You do not hand it to the agent. On this board, handing a ticket to the agent means *build this*. Moving it to Plan it means *plan this*. Two gestures, two meanings.

### Stop 2: a small program notices, and asks two questions

A program wakes up every five minutes. It looks for tickets sitting in Plan it, and asks two questions about each.

*Who moved this here?* Only your move counts. If a colleague or an automation moved it, the program leaves a note and starts nothing. A planning run costs money and runs under your name.

*Is this really an idea?* Plan it sits one column away from Todo. A slip of the mouse could drag real work into it. So the program looks for signs of live work. Is someone already building it? Is a session running on it, or a pull request attached? Does it have a parent or children? Any of those, and it leaves one note and starts nothing. It starts at most three runs a pass, and ten in a day, whatever gets dragged.

One more look: has this idea already been planned? If an epic from an earlier run still stands, the program names it and starts nothing. You approve that plan or cancel it first.

### Stop 3: it writes a clean copy, addressed by hand

Here is the part that is easy to miss.

The session runner decides *which setup* runs a ticket by reading it. It reads the text first, then the labels, then the project, and the team last. A line in a ticket reading `[repo=acme-app]` does not describe anything; it *instructs*. It says: run this in the coding setup, the one that can edit files and run commands.

Ideas come from everywhere. You paste a paragraph from an email, or quote a chat message. Any of that can carry a line you did not read closely.

So your idea is never the ticket the session runner sees. The program writes a second ticket, a **planning ticket**, on the same board. Your words are inside it, quoted between markers that say "this is data, not instructions". Every such line is replaced by the visible mark `(removed-routing-tag)`. Then the program addresses the envelope itself, twice. One line at the top names the read-only planning setup, and one label has the same name.

The analogy: you hand the courier a photocopy, not the original. The "OPEN ME FIRST" stickers are gone, and you write the address on it yourself, twice.

### Stop 4: it checks the delivery receipt

Two addresses, because the session runner reads each one with a separate network call, and a call that fails is treated as "no address written". If both fail, it falls back to the board. On a work board, the board says *coding setup*.

That is rare. It is also the one way a planning ticket could land somewhere with hands. So the program does not trust the envelope; it checks the receipt.

The receipt is not a third address. It is the session runner's own note, posted as a session starts and before any AI has run, saying which setup answered. Seconds after filing, the program reads that note. If it names the planning setup, fine. If it names anything else, or no note arrives in two minutes, the program assumes the worst:

1. It writes down what it saw.
2. It cancels the ticket, which stops the session and deletes its working copy.
3. It pages you.
4. It **stops planning altogether**, on every project.

Planning stays stopped until the addressing is tested again and you sign that off. The installer runs the test when you say yes. A stopped gate is loud and cheap. A gate that quietly sends ideas to a coding session is neither.

### Stop 5: the session with no hands

In the normal case, the planning session starts. It can read the real codebase: search it, and open any file. It can also start small helper sessions to check its own work, each with the same fence.

It cannot do anything else. It cannot run a command. It cannot write a file, not even a scratch file. It cannot touch the tracker: the tools that would let it create, move, label or comment are removed by name before it starts.

Why so strict? Two reasons.

*A session that can write a file can rewrite its own rules.* The rules live in a settings file on the same machine, and the session runner reloads them while running. Give the session a pen and the fence becomes advisory.

*A rule in a prompt is a suggestion; a missing tool is a fact.* You can argue with an instruction. You cannot call a tool that is not there.

So the session reads, thinks, and writes its answer into the only place left: its own last message.

### Stop 6: the answer comes back

The session's final message ends with a block of structured text: one epic and its children. Each child has a title, a body, a size, and the children it waits for. If it could not decide something, it asks instead, in the same message, and the question reaches you.

The program from stop 2 is still watching. When the session finishes, it takes that final message, from the session whose receipt it checked and never another. It passes the message on with a fresh copy of the code.

If the run ended without a plan, for a reason another try can fix, the program can try again by itself. Each new try is told how the old ones failed. You hear only the result.

### Stop 7: the filing clerk

The filing program is not an AI. It is ordinary code that does the same things in the same order every time. It is the only part of the chain holding a key that can write to the tracker.

It checks, in order:

- **Is this proposal for the ticket I asked about, on the board I file to?** It compares against the id it was handed, never the one written inside the proposal.
- **Does anything here look like a password or a key?** If so, nothing is filed, and it does not repeat the text.
- **Does every child pass the readiness check?** Required sections, checkable criteria, a test plan naming a command, real file paths, one size and one kind. If *one* child fails, the whole plan is refused, with reasons. A half-built epic looks finished and is not.
- **Does any of this already exist?** It compares the proposed titles with recent tickets on the board, leaving out your idea and the planning tickets, and lists anything that looks alike. It never refuses a plan for that. Whether two tickets are the same job is your judgement.

Then it files the tree and forces every field that carries authority. A project and the epic go into the backlog, the epic is marked machine-written, and each child's parent is the epic it just made. A plan cannot slip its children under an epic you already approved.

### Stop 8: you approve

The epic and its children sit in the backlog. Nothing starts.

You read the summary, starting with the duplicate lines. If you like the plan, you move the **epic** to your ready state. That move is your approval. Then each child goes to the agent to build, one after the ones it waits on. If you do not like the plan, leave it, or cancel the epic and its children.

The machine cannot make that move. The session's tools to move a ticket were taken away at stop 5. The filing program never moves anything; it only creates, and only into the backlog.

---

## Why the chain is shaped this way

Look at what each part holds:

| Who | Can write a plan | Can write to the tracker | Can approve |
| --- | --- | --- | --- |
| You | if you want to | yes | **yes** |
| The watching program | no | its own planning tickets and notes | no |
| The planning session | **yes** | no | no |
| The filing program | no | **creates, in the backlog only** | no |

Nobody holds two of the last three columns. That is the design. If you ever add a capability to one of these parts, check this table first. The moment one row holds both "writes the plan" and "writes to the tracker", the approval stops meaning anything.

And one more rule sits across the whole table: **the watching program checks where every planning ticket went.** Living on your project's board is cheaper than a board of its own. The cost is that a mis-delivered ticket lands next to tools that can act. The receipt check is what pays for that.

---

## How to tell whether it is working

Four signals, in the order you would look.

**The idea got a note.** Within five minutes, a moved idea should get a note naming its planning ticket. No note means the program is not running, cannot reach the tracker, or has stopped planning.

**The program's heartbeat is fresh.** It writes a small file every time it finishes a pass, saying what it decided. A stale timestamp means it is not running. A fresh timestamp with a bad result means it ran and could not plan. Those look identical from a distance and have opposite fixes.

**No stop file.** When planning has stopped, a file says why, and holds what the program saw. While it exists, nothing is planned.

**The plan arrived, or a reason did.** A run that produced nothing leaves a note saying so. A refused plan leaves the reasons. Silence is the one answer the system tries never to give.

---

## The honest limits

- **A mis-delivered ticket runs for a few seconds.** The receipt check catches it quickly; nothing prevents it. What runs works from the clean copy, and cannot merge anything.
- **The board's own instructions reach the planner.** The tracker lets a board carry standing instructions for AI sessions, and they reach every session on that board. Keep them neutral. The tracker's interface cannot read them, so a person checks them once.
- **An upgrade to the session runner pauses planning.** The program only plans on the version the addressing was last tested on. That is deliberate: an upgrade could change how envelopes are read.
- **Cost.** Every planning run costs money. The daily limit caps a bad day, not a bad habit.
- **Only the Claude runtime carries the guards.** A label can select a different runtime, and a person adding one to a planning ticket by hand would still choose it.
- **Approval is a move, not a signature.** Where the optional approve step is off, which is the default, moving the epic starts nothing. You release each child yourself.

---

## Words

- **Agent**: the session runner's user in the tracker. Handing a ticket to it starts a session.
- **Backlog**: the column where nothing starts.
- **Board, team**: one project's tickets. Ideas and their planning tickets live on the project's own board.
- **Clean copy, planning ticket**: the rewritten ticket the session runner is given, with instructions stripped out of your text.
- **Epic**: a parent ticket. Moving it to ready is your approval.
- **Fence**: the list of tools removed from a planning session before it starts.
- **Filing program**: the ordinary code that checks a proposal and files it. Holds the only key.
- **Heartbeat**: a small file a background job writes on every pass, so "dead" and "failing" are different.
- **Helper session**: a short sub-session a planning session starts for one review pass. It inherits the same fence.
- **Plan it**: the column you move an idea into. The only trigger.
- **Planning setup**: the read-only setup a planning ticket should reach.
- **Routing note**: the note the session runner posts at the start of a session, saying which setup answered. The receipt.
- **Routing tag and label**: the two addresses on a planning ticket, both naming the planning setup.
- **Stop file**: the file that says planning has stopped, and why.

---

## What is not proven

- **A real idea carried through all eight stops with a person watching is not recorded** (KIT-164).
- **A helper session inheriting the fence** is read from the runner's source, not seen live (KIT-261).
- **A mis-delivered ticket running for seconds** is expected from the design and not measured (KIT-184).
- **That a planning stop reaches you as a message** has not been seen (no ticket yet).
