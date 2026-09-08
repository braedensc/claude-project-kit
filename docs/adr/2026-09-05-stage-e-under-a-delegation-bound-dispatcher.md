# Stage E under a delegation-bound dispatcher

**Date:** 2026-09-05 · **Status:** Accepted · **Context:** KIT-89 (epic), branch
`feat/kit-89-stage-e-review-and-bounce`. **Supersedes the `local-daemon` backend half of
[Where the review session runs](2026-08-26-where-the-review-session-runs.md)** — that ADR
decided review moves to the dispatcher's machine, then assumed the machine still wrote a
pin and that a local watcher would post a trigger comment. The dispatcher chosen since,
Cyrus, writes no pin and its comment lane is disqualified for a reviewer. This ADR
redesigns Stage E for what actually runs.

> **Update (2026-09-06) — option 4: the reviewer is a dispatcher session; the poller
> only speaks.** Reading the dispatcher's *source* (v0.2.69) and the Linear SDK typings,
> rather than their docs, changed four of the six decisions below. The text of decisions
> 1, 3, 5 and 6 and of the Verified section is amended in place; decision 2 (the basis)
> and decision 4 (comment, never approval; loud decline) stand.
>
> - **Trigger (1).** The poller no longer launches anything. For each newly-opened
>   pipeline PR it creates **and delegates, in one `issueCreate{…, delegateId}`**, a
>   review ticket in a dedicated *Reviews* team, with the diff inlined in the description.
>   The dispatcher's ordinary Linear lane starts the reviewer. **No code path launches a
>   Claude session as the owner** — the owner-account `claude -p` launcher that shipped in
>   the merged reviewer core is removed, and the reasons are recorded under
>   [Trigger](#1-trigger) as *option 3, rejected*.
> - **Where it runs (1).** The poller and the bounce driver run as **the dispatcher's own
>   role account**, under a **system LaunchDaemon**, not in the owner's login account. A
>   user-domain LaunchAgent runs only while the owner is logged in, so a reboot to the
>   login window would bring the dispatcher back and leave the review layer down —
>   silently, which is the failure mode this whole design is shaped against. A daemon with
>   `UserName` set to the role account starts at boot with nobody logged in, launchd runs
>   the missed interval on wake, and each pass is one shot (scan → act → exit) under its
>   own wall clock, with a heartbeat file, so a hung run cannot wedge the next one. Three
>   rules make the placement safe: the credentials live in the poller's **own** env file
>   under that account's home (mode 600); **never** in the dispatcher's own env file, which
>   is copied unscrubbed into every session (`session-env.ts:51-53`); **never** under the
>   dispatcher's state root, where session readability is unmeasured. State lives beside
>   the env file. The residual is recorded below and is accepted, not overlooked.
> - **How it finds work (1).** Discovery is **Linear-driven, with no repository list.** The
>   poller pages the workspace's agent sessions, takes each session's issue, and reads the
>   GitHub pull request off that issue's **attachments** — the integration attaches one
>   whenever the branch carries the issue id, which the kit's branch rule guarantees; the
>   repository comes out of the PR URL. A second signal covers a missing attachment: a PR
>   URL in the coding session's own final `response`. A repository list is a second place
>   to keep the truth and a guaranteed source of drift, so `repos` becomes **optional** — a
>   restriction, or the fallback for a workspace without the integration. Two consequences:
>   a **human-authored PR is not auto-reviewed** (no agent session, no discovery; the owner
>   can still delegate a review ticket by hand), and **workspace facts are resolved by
>   name** at the start of each run — the Reviews team by key, the dispatcher's agent by
>   display name, the model label by name — with ids accepted only as overrides, because a
>   UUID pasted from a URL bar rots silently and reads as noise.
> - **Independence (3).** Enforced by the operating system, not by a prompt: the
>   dispatcher runs every session as a dedicated non-admin account inside a sandbox that
>   allows writes only in the worktree and `TMPDIR` and denies reads of `~`
>   (`RunnerConfigBuilder.ts:558-579`), and the per-repository-entry **`disallowedTools`**
>   list removes `Bash`, `Edit`, `Write` and the fetch tools from the reviewer. Two
>   corrections, to this ADR and to the 2026-08-26 one: **`allowedTools` restricts
>   nothing** — the dispatcher's permission callback allows every tool except
>   `AskUserQuestion` (`ClaudeRunner.ts:321-328`); and **`promptTemplatePath` is
>   stripped** by the CLI config loader before it is ever read (`ConfigService.ts:47-58`).
>   The reviewer's brief travels in the per-entry `appendInstruction`
>   (`PromptBuilder.ts:958-966`).
> - **Bounce (5).** A bounce is a **re-prompt of the original coding session**: a comment
>   in that ticket's agent-session thread resumes the same Claude session, in the same
>   worktree, on the same branch (`EdgeWorker.ts:7256-7443`,
>   `RunnerConfigBuilder.ts:431-437`). E cuts no fix worktree of its own. A fix ticket
>   whose description pins the base branch is the fallback for a session that cannot be
>   resumed.
> - **Coupling (6).** The seam is no longer one fact. It is a **named set of dispatcher
>   knobs** — per-entry `disallowedTools`, `appendInstruction`, `teamKeys` routing, the
>   description-tag base-branch override, label-based model selection, and the
>   agent-session re-prompt — and the poller touches none of them: it creates and
>   delegates tickets, reads activities, posts comments.
> - **Accepted risks (owner, 2026-09-06), monitored not closed:**
>   1. **The poller shares a uid with the sessions it reviews.** It runs as the
>      dispatcher's role account, so the owner-scoped delegation key is kept from every
>      session by the sandbox's `denyRead ['~/']` over that account's home
>      (`RunnerConfigBuilder.ts:558-579`) — and by nothing else. That is a sandbox rule,
>      not a permission boundary. **Revisit trigger:** a non-Claude runner label appears (a
>      runner outside that sandbox), or the session Linear token is tightened to read-only
>      (which makes a separate role account cheap). Either one, and the poller moves to a
>      role account of its own.
>   2. The Linear MCP tools stay available to every session, the reviewer included.
>   3. The re-prompt access check tests the *delegator*, not the commenter
>      (`EdgeWorker.ts:5281-5290, 6726-6745`) — so one session's identity can re-prompt
>      another session, which live test 7 in the operator doc measures deliberately.
>   4. The poller's Linear key is owner-scoped although the process holding it is not the
>      owner's, and it stays out of the dispatcher's environment because the dispatcher
>      copies its whole process env into every session unscrubbed (`session-env.ts:51-53`).
>   5. The mid-work criteria-edit gap of decision 2 is unchanged.
> - **A bug in the merged reviewer.** `pr_metadata()` requests `isCrossRepository` and
>   nothing ever reads it, so a fork PR would have been reviewed and commented on. Fixed
>   in the reviewer-core change that accompanies this update: cross-repository ⇒ decline,
>   distinct reason, selftest.
> - **Every dispatched session and every reviewer is briefed.** Nobody watches an
>   autonomous session, so it must never try to ask an interactive user. The committed,
>   generic brief is `docs/SESSION-BRIEF.md`; `CLAUDE.md` points at it.
>
> Operator steps: `docs/STAGE-E-OPERATOR.md`.

> **Update (2026-09-08) — the reviewer is routed to the repository it is judging.**
> Routing by the Reviews team key alone landed *every* review in whichever single entry
> claimed that key, and that entry's `repositoryPath` was copied from the first entry in
> the dispatcher's config that had one. So a reviewer judging a diff from one repository
> sat in a clone of another. It keeps `Read`, `Grep` and `Glob` — removing them is the
> worse fix, since a worktree cut from the *correct* default branch is genuinely good
> review context — so it opens a file named in the diff and finds nothing, or finds a
> same-named file from the wrong codebase and reasons about it confidently. A wrong
> finding can bounce the coding session.
>
> - **N entries, one team.** One dispatcher entry per reviewed repository, named
>   `reviews-<repo name>`, each with that repository's own clone and base branch. A
>   Reviews team *per repository* was rejected on the owner's constraint: tracker teams
>   are capped by subscription tier, so they cost money and have to be remembered at every
>   new repository.
> - **The poller writes the routing tag.** `[repo=reviews-<name>]` on the review ticket's
>   first line, in the poller's own trusted header, outside every fence. Description-tag
>   routing is **priority 1** in `RepositoryRouter` (verified against cyrus-edge-worker
>   0.2.69), ahead of labels, projects and team keys.
> - **This does not weaken the sanitizer, and the two are checked against each other.**
>   Everything inside `<untrusted-ticket-data>` and `<untrusted-diff>` still has its
>   directives stripped; the poller refuses to file a description carrying any directive
>   but its own one. The tag names the **review entry**, never the repository: a tag
>   matches by `githubUrl` tail, name or id, the router starts a session in *every* match,
>   and `[repo=<the repository>]` would also match the coding entry — which has Bash and
>   Write. The installer refuses to write an entry whose name another entry could answer
>   to.
> - **The fallback stays where it was.** Exactly one review entry keeps `teamKeys`, so a
>   ticket that somehow arrives with no tag degrades to the old behaviour — a read-only
>   reviewer, possibly in the wrong clone — rather than falling through to catch-all
>   routing. The others carry a routing label that must never exist, which is what keeps
>   them from *becoming* the catch-all.
> - **Which clone is which is read, not inferred.** The installer matches a repository to
>   an entry with `git -C <path> remote get-url origin`. A path basename is not evidence
>   of identity, and that class of guess is what this update removes.

## Decision

**Stage E is a poller running as the dispatcher's own role account that speaks to the
dispatcher only through Linear, plus a review lane the dispatcher runs under its own
sandbox.** Six decisions:

1. **Trigger** *(amended 2026-09-06)* — a **PR poller running as the dispatcher's role
   account, under a system LaunchDaemon**, asks Linear which pipeline PRs the dispatcher
   worked on and, for each new one, **creates and delegates one review ticket** in a
   dedicated Reviews team, diff inlined, in a single `issueCreate{delegateId}`. The
   dispatcher starts the reviewer exactly as it starts any delegated session. The GitHub
   webhook side of Cyrus stays **unwired**. The poller *is* the thing that speaks first —
   and it only ever speaks; **nothing launches a Claude session as the owner.**
2. **Basis** — the reviewer compares the diff against the ticket's acceptance criteria and
   out-of-scope **as of delegation time**, resolved from a source the coding session
   cannot write, and **declines loudly** when it cannot establish one.
3. **Independence** *(amended 2026-09-06)* — **OS-enforced** by the dispatcher's sandbox
   (dedicated non-admin account; writes only in the worktree and `TMPDIR`; `~` unreadable;
   shell network only through an allowlisting proxy) and by the per-entry
   **`disallowedTools`** list, which is the *only* tool fence the dispatcher honours. The
   reviewer has no `Bash`, no `Edit`/`Write`, no fetch; its worktree is a checkout of the
   **default branch**, never the PR head; the diff, the criteria and the schema arrive in
   the ticket body. Same host and Claude credential family are accepted. The Linear MCP
   tools remain available to it — an accepted, monitored risk.
4. **Verdict** — a **PR comment** (and a Linear telemetry comment), **never an approval**;
   a review that *could not run* is a visibly different, loud outcome from one that ran and
   found nothing.
5. **Bounce** *(amended 2026-09-06)* — the poller notices red CI or threshold findings and
   **re-prompts the original coding session** in its agent-session thread with fenced
   findings and a fixed instruction; the session resumes in its own worktree on its own
   branch. `maxBounces` is read from `delivery.json` **on the committed default branch**
   (absent ⇒ the bounce tier is **off, and says so**), and the count lives in a
   **driver-owned append-only ledger** the session cannot write — under the role account's
   home, outside every worktree — appended *before* the bounce is sent. A fix ticket
   pinned to the PR branch is the fallback when the session cannot be resumed.
6. **Coupling** *(amended 2026-09-06)* — the poller reads **GitHub and Linear and nothing
   inside Cyrus**: it creates and delegates tickets, reads agent-session activities, and
   posts comments. Everything dispatcher-specific is **configuration on the dispatcher's
   side of Linear**, named exhaustively in [Coupling](#6-coupling), so a replacement
   dispatcher needs those knobs ported and the poller untouched.

**Mechanism ships in the kit** as tested `scripts/` (the poller-side publisher, decline
logic, findings validator, sanitizer, bounce ledger) — the same seam the rest of the
pipeline uses. **Activation** (the Reviews team, the dispatcher's second repository entry,
the two system LaunchDaemons under the role account, their env file) is an **operator step
for the owner**, written up generically in `docs/STAGE-E-OPERATOR.md`; no session installs
or edits a service, and deployment-specific values stay out of this repository.

**The GitHub webhook side of Cyrus is not wired by this ADR, and this ADR does not
authorize wiring it.** That remains refused for the reason the audit gave — see
[Trigger](#1-trigger) — and reopening it needs its own written decision and Braeden's
confirmation.

The first slice — the reviewer core plus the publish-or-decline path
(`scripts/pipeline_review_local.py`) — is built with this ADR and proven on a real PR
(KIT-90). Everything else is filed as KIT-91 … KIT-95 under the KIT-89 epic.

This is an **epic-level** ADR: it records the *direction* for all six decisions so the
children are coherent, at `Status: Accepted` because that direction is Braeden's call and
is settled. It does not pre-empt each child's implementation review — KIT-91 (trigger),
KIT-93 (bounce) and KIT-95 (the daemon lift) are each built and reviewed on their own
diff, and this ADR is amended if one of them forces a change of direction.

## Why

The kit put review in GitHub Actions when the dispatcher was also in Actions: one trust
domain, the pin sitting next to the reviewer as a build artifact, and a fresh runner
giving isolation for free. The 2026-08-26 ADR moved review to the dispatcher's machine but
still assumed two things that are now false: that the dispatcher writes a pin, and that a
local watcher can post a mention comment to trigger a Cyrus review session. Cyrus does
neither usefully. So the design is redone from the events and credentials that exist.

### The constraints that are not negotiable here

- **No pin.** Cyrus binds a session by *a named person delegating in Linear* and writes no
  file (field guide §04; build record §04, §17). Every consumer the pin fed — budgets,
  telemetry, the scope fence, and review's dispatch-time snapshot — lost its input. KIT-18
  is the umbrella; this ADR solves only the review-basis slice of it.
- **Cyrus has no PR-opened trigger.** Its GitHub transport accepts exactly
  `issue_comment`, `pull_request_review_comment`, `pull_request_review` and `push`; a PR
  opening starts nothing (2026-08-26 ADR, read from source `@85aeaaa`). Something must
  speak first.
- **Cyrus's comment lane is the wrong shape for a reviewer.** Its GitHub sessions carry a
  hardcoded *"make changes and push"* system prompt and reuse a worktree by branch name,
  so a reviewer started that way would be told to edit, and could land inside the coding
  session's own tree (2026-08-26 ADR). Prompt-versus-prompt is not a boundary.
- **The GitHub side is deliberately unwired for security.** Both repos are public, so a
  fork's branch name and a stranger's comment are attacker-reachable text, and the audit's
  primary mitigation — private repos only — is unavailable (KIT-25, Urgent, open).
- **Sessions are sandboxed** under a dedicated service account with a short egress
  allowlist, and `gh` cannot verify TLS inside the sandbox; a REST fallback exists
  (`scripts/gh_fallback.py`, KIT-75) and every GitHub write E makes goes through it.

### 1. Trigger

**Decision (amended 2026-09-06): a poller running as the dispatcher's role account that
creates and delegates a review ticket per PR; the dispatcher runs the reviewer; GitHub
webhook unwired.**

**How it finds work.** Not from a repository list — that is a second place to keep the
truth, and it drifts. The poller asks **Linear** what the dispatcher actually worked on: it
pages the workspace's agent sessions, takes each session's issue, and looks for a GitHub
pull request among that issue's **attachments**. Linear's GitHub integration attaches a PR
to an issue whenever the branch name carries the issue id, which the kit's branch rule
(`<type>/<team>-<n>-<slug>`) guarantees; the repository is then read out of the PR URL. A
second signal covers an issue whose attachment is missing — a PR URL in the coding
session's own final `response` activity — bounded, and it says on the log how many issues
it could not probe. `repos` therefore becomes **optional**: a *restriction* when you want
one, and the branch-scanning fallback for a workspace with no GitHub integration. Two
consequences are worth stating rather than discovering. A **human-authored PR is not
auto-reviewed**: no agent session, no discovery, no review ticket — the owner can still
request one by delegating a review ticket by hand. And **workspace facts are resolved by
name once per run** — the Reviews team by its key, the dispatcher's agent user by its
display name, the model label by its name — with UUIDs accepted only as explicit
overrides; a name that resolves to nothing is a config error (exit 2, nothing touched),
never a per-PR decline that would spend each PR's single review on a deployment typo.

It then selects same-repository, non-draft PRs it has not reviewed, skipping forks — and
skipping a PR whose cross-repository flag is absent, because unknown is not "not a fork".
For each it fetches the diff, resolves the basis (decision 2), **sanitizes** every string
it is about to copy — stripping the dispatcher's routing and model tags (`[repo=`,
`repo=`, `repos=`, `[model=`, `[agent=`) and neutralizing the `<untrusted-ticket-data>`
fence tags; it writes exactly one routing tag of its own, in its trusted header
(amended 2026-09-08) — and then creates **and** delegates, in one `issueCreate{…, delegateId}`
carrying an owner-scoped Linear key, a review ticket in a dedicated Reviews team: title
`Review PR #<n> — <TICKET-ID>`, description = the review brief with the diff inlined under
a size cap (above the cap ⇒ decline, reason *diff too large to deliver*), **never
parented** (a sub-issue would be based on its parent's branch), never linked to the PR by
branch, title or body. The dispatcher sees a delegation by the owner and starts a session
in the Reviews entry, exactly as it starts any other. The poller then reads that ticket's
agent-session activities until a `response` (or `error`) activity appears or a timeout
passes, extracts the first fenced JSON block whose `schema` is `pipeline-review/1`,
re-reads the ticket description and compares its hash to the one it stored at creation
(a mismatch is *basis tampered* ⇒ decline), and hands the document to the deterministic
publisher (decision 4). It then moves the **review** ticket to Done, which deletes only
that ticket's worktree; the original ticket is never moved by E.

Its seen-set, hashes, outcomes and ledger are files in a state directory **under the role
account's home**, beside the mode-600 env file — a home the sandbox denies to every
session, which is the point — so a PR is reviewed once and a restart does not re-review the
backlog. It preserves the cloud template's `opened`-only rule (never re-review on every
push) so review cost does not multiply with bounce pushes; a deliberate re-review after a
bounce is the bounce driver's call, and bounded.

**Linear is the source of truth; that state directory is a rebuildable cache.** Before
creating a review ticket the poller *searches the Reviews team* for one that already exists
for this PR and reuses it, so a crash between `issueCreate` and the write-through, a
restored-from-nothing state directory, or a brand-new machine can never open a second paid
reviewer session for a PR that already has one. The search must **succeed** before a
create: "could not ask" and "asked, nothing there" differ by exactly one duplicate paid
session (§13).

**Where the poller runs, and why it is not the owner's account.** As the **dispatcher's own
role account**, under a **system LaunchDaemon** — `UserName` set to that account,
`StartInterval`, `RunAtLoad`, and deliberately no `KeepAlive`, because each pass is one
shot: scan → collect → decide → exit, under its own wall clock, leaving a heartbeat file on
every terminal path. A user-domain LaunchAgent would run only while the owner is logged in,
so a reboot to the login window would restore the dispatcher and not the review layer, and
PRs would open with nothing reviewing them and nothing saying so. A system daemon starts at
boot with nobody logged in; launchd runs the missed interval on wake, so a sleeping machine
catches up where a webhook would have been lost; and a network outage is one non-zero exit
and a retry next interval. The three credential rules that make this placement safe — the
poller's own env file under that home, never the dispatcher's env file, never its state
root — are stated in the Update block, and the residual they leave is accepted risk 1.

**Why the delegation must be the owner's, and why the poller must not be a session.** The
dispatcher admits a delegation only when the webhook signature checks out *and* the
delegating user is on the entry's `allowedUsers` list; a delegation made through the
Linear MCP token a session holds arrives with no creator and is **blocked**
(`UserAccessControl.ts:31-43`). So a coding session cannot delegate its own review — the
2026-08-26 ADR's vector #5 is closed by the dispatcher itself — and the poller must carry
the owner's identity. **The key is owner-scoped; the process holding it is not.** That is
the point of the three placement rules: an owner-scoped key inside the dispatcher's own
environment would be handed to every session it starts, so it lives in the poller's own
mode-600 env file under the role account's home, which the session sandbox denies.

**Rejected — a GitHub Action that posts a mention comment** (the 2026-08-26 ADR's own
"Building it" path). It works and is the cheapest wiring, but it (a) keeps a cloud
dependency in the critical path, the incoherence the move off Actions was resolving, and
(b) if it triggered a *Cyrus* session, that session is the "make changes and push" shape
with a reused worktree — wrong for review. Not chosen.

**Rejected — wiring Cyrus's GitHub webhook** so a `pull_request` event starts a review.
This is the refused surface: on public repos a fork branch name or a stranger comment is
attacker-reachable, and KIT-25's mitigation is unavailable. **Reopening it is gated on**
either making the repos private (KIT-25) or an author-association gate on the event plus a
signed-payload check, **and Braeden's written confirmation.** Not done here.

**Rejected — the coding session requesting its own review as its last act.** This is the
2026-08-26 ADR's vector #5 with the safety filed off: the reviewed party authoring the
reviewer's trigger. It is self-approval-adjacent by construction — the party under review
does not get to set the terms of its review — and is rejected on the same grounds the pin
doctrine exists.

**Rejected (2026-09-06) — option 3, the owner-account launcher.** The 2026-09-05 text of
this section refused to route review through the dispatcher's Linear lane and had E launch
its own `claude -p` session instead, on three grounds: (a) coupling to dispatcher internals,
(b) a full toolset plus the dispatcher's Linear token, (c) death on dispatcher replacement.
Read against the source, (a) and (c) reduce to *configuration* — the lane is shaped
entirely by per-entry config the poller never touches (decision 6) — and (b) is answered
by `disallowedTools`, which the dispatcher's runner applies as a hard tool fence
(decision 3); the Linear token half of (b) is accepted as a risk rather than answered. The
launcher, meanwhile, fails on **four grounds, each resting on a verified fact:**

1. **No sandbox.** The dedicated account, the Seatbelt profile that allows writes only in
   the worktree and `TMPDIR`, the `~` read-deny and the egress proxy are all applied by the
   dispatcher's runner (`RunnerConfigBuilder.ts:558-579`). A session the poller launched
   would have none of them — and the reviewer's input is diff text from a **public**
   repository, i.e. attacker-reachable, read with the owner's whole home and network.
2. **The wrong credentials in the wrong process.** A child inherits its parent's
   environment. The poller holds the owner-scoped Linear key and a GitHub token — the two
   credentials the design puts outside every session. The dispatcher's own unscrubbed env
   copy (`session-env.ts:51-53`) is the same fact seen from the other side, and is why
   those keys stay out of the dispatcher's environment too.
3. **The wrong identity on the record.** A session launched by the poller inherits the
   poller's credentials, and the Linear key it holds is the **owner's**. Its PR comment
   and any Linear write would therefore carry the identity of the person whose merge is
   the control, and the record could not tell reviewer from approver. (Moving the poller
   to the dispatcher's role account, 2026-09-06, does not change this: the uid moved, the
   key's identity did not.) This is the human-signal defect class the kit is shaped
   against, and no prompt fixes it.
4. **A second dispatcher for one lane.** Worktree lifecycle, tool fencing, model
   selection, session resume and deletion-on-close already exist as configuration in the
   dispatcher that is running. The 2026-09-05 text accepted rebuilding them as "a real
   operational cost"; with the fence verified, the cost buys nothing.

So the rule is stated as an invariant, and the reviewer core's selftest holds it: **no
daemon may ever launch a Claude session as the owner.** The poller creates tickets, reads
activities and posts comments — that is its whole vocabulary.

### 2. What the reviewer compares against — the snapshot problem

**This is the hardest question and it has no clean answer under Cyrus.** Stated plainly,
so the design does not pretend otherwise.

The review's entire value is catching a change that quietly did more than the ticket
asked. That judgement needs *what the ticket asked, as of when work started*. Under the
old design the pin froze the acceptance criteria at dispatch, outside the worktree, where
the session could not touch them. There is no pin now, and worse: a Cyrus session holds a
**workspace-scoped Linear write token** (`docs/AUTONOMY.md`) and can therefore **edit its
own acceptance criteria mid-flight**. So "read the live ticket at review time" is not
merely weaker — it lets the reviewed party rewrite the very standard it is judged against.

Three candidate sources, none free:

| Tier | Source | Not session-writable? | Cost |
|---|---|---|---|
| 1 | Linear's **server-side description history** read as-of the delegation timestamp | **Yes** — history is server-authoritative; a later self-edit does not rewrite the past record | Needs the Linear API to reliably return description-as-of-timestamp; **unverified** |
| 2 | An **independent reconciler** that snapshots criteria at delegation to a daemon-owned store outside every worktree | **Yes** — the reconciler is trusted, the store is outside the sandbox | A second daemon; duplicates KIT-47's polling; snapshot keyed by a ticket id the branch only *hints* at |
| 3 | The **live ticket**, with post-delegation edits **flagged** | **No** — but the flag surfaces the exact drift as a finding | Weakest basis; only as good as the flag |

**Decision.** The basis resolver tries tiers in that order and **declines loudly if none
is available** — it never silently reviews against a basis it cannot trust (the TOD-112
failure: a green check that reviewed nothing). Whatever tier it uses, it *also* reads the
live ticket and includes whether the criteria changed after delegation, so a mid-flight
self-edit is a finding rather than a silent pass. The shipped default is decided by a
spike (KIT-92): if the Linear API cleanly returns description-as-of-timestamp, tier 1 is
the default and needs no new infrastructure and no pin — it talks only to Linear, which is
decision-6-clean. If it does not, tier 2 is the buildable fallback and tier 3 the floor.

**The honest two bad options, if tier 1 fails the spike:** build a second reconciler
daemon (tier 2) — more infrastructure, and the branch→ticket resolution it keys on is a
session-choosable hint, though claiming another ticket's snapshot produces *more* scope
findings, not fewer, so the attack is not profitable; **or** accept tier 3 and lean
entirely on the edit-flag — simplest, and genuinely weak against a session that edits its
criteria before the first reconciler poll would even have run. Neither is as strong as the
pin was. This is a cost of choosing a dispatcher that writes no pin, recorded as such
rather than papered over. The clean fix is the pin (KIT-18); until then, review runs on
the best basis available and says which one it used.

**Not used — Cyrus's worktree-creation hook (`cyrus-setup.sh`) as a snapshot writer.**
Disqualified by [Budgets belong to whatever writes the pin](2026-08-25-external-daemon-budget-enforcement.md)
and KIT-18: it runs inside the worktree, from branch contents a prior session controls,
with daemon privileges, and cannot refuse to start a session. A snapshot it wrote would be
a snapshot a session could plant.

### 3. Independence

**Decision (amended 2026-09-06): independence is enforced by the operating system and by
the dispatcher's one real tool fence, and the reviewer is fed everything it needs because
it can fetch nothing.**

**What is actually achieved:**

- **Its own worktree, keyed on its own ticket.** The dispatcher cuts one worktree per
  issue identifier, from `origin/<baseBranch>` (`GitService.ts:734-736, 833-899`); the
  review ticket is a different ticket from the coding ticket, so the reviewer can never
  land in the coding session's tree — closing the dirty-worktree inheritance vector (#4 of
  the 2026-08-26 ADR) without E cutting anything itself. Two conditions keep that true and
  are the poller's to hold: the review ticket is **never parented** under the coding
  ticket (a sub-issue is based on its parent's branch if one exists,
  `GitService.ts:452-477`), and every string copied into the ticket has the
  `[repo=<name>#<branch>]` base-branch override stripped (`RepositoryRouter.ts:451-452`),
  or the reviewed party could steer the reviewer's checkout onto the PR head.
- **The checkout is the default branch, not the PR head.** The Reviews entry's
  `baseBranch` is the default branch, so any `CLAUDE.md` the reviewer's runtime loads is
  the committed one (vector #3), and the rubric and `schemas/review-findings.schema.json`
  it could read are the committed ones (vectors #1, #2). The diff arrives **inline in the
  ticket body**, inside an `<untrusted-diff>` fence with a treat-as-data preamble, because
  the reviewer has no tool that could fetch it.
- **A tool fence the dispatcher actually enforces.** The Reviews entry's
  **`disallowedTools`** removes `Bash`, `Edit`, `Write`, `NotebookEdit`, `WebFetch`,
  `WebSearch`, `Task`, `EnterWorktree` and `ExitWorktree`; the dispatcher passes that list
  through to the runner (`RunnerConfigBuilder.ts:439`, `ClaudeRunner.ts:563, 701`). Two
  facts corrected here, and in the 2026-08-26 ADR's "Building it" step 3 which relied on
  an allowlist: **`allowedTools` restricts nothing** — the permission callback allows
  every tool except `AskUserQuestion` (`ClaudeRunner.ts:321-328`) — and
  **`promptTemplatePath` is stripped** by the CLI config loader before use
  (`ConfigService.ts:47-58`), so the reviewer's brief goes in the entry's
  **`appendInstruction`** (`PromptBuilder.ts:958-966`). The reviewer has no tool with
  which to approve, label, push, or merge, and no shell from which `WebFetch` — which
  **bypasses** the egress proxy — could be reached either.
- **OS-level sandbox.** Dedicated non-admin account; Seatbelt profile allowing writes only
  in the worktree and `TMPDIR`, denying reads of `~` (`RunnerConfigBuilder.ts:558-579`);
  shell network only through the allowlisting proxy. The poller's state directory sits
  inside the **role account's** home precisely because that home is unreadable from here —
  and note what that sentence now costs: the poller and the sessions share a uid, so the
  deny-read is the only thing between a session and the delegation key (accepted risk 1).
- **Fresh context** — a new session with no memory of the work; a stranger to it by
  construction. Its whole deliverable is one fenced `pipeline-review/1` block in its final
  message, which the dispatcher posts as a `response` activity
  (`AgentSessionManager.ts:1269-1281`) and the poller reads back.
- **A cheaper model**, chosen by the poller through a **label** on the review ticket —
  model selection on the Linear lane is label-driven — because review is a bounded read,
  not open-ended construction.

**What is accepted as lost, or as risk:**

- **Structural token scoping.** The cloud reviewer's `pull-requests: read` token *could
  not* approve. Here "cannot approve" is a config list the dispatcher enforces — weaker,
  because config can be edited by a human in a hurry where a token scope cannot. Accepted,
  as before.
- **The Linear MCP tools stay in the reviewer** (owner decision, 2026-09-06). They are
  not added to `disallowedTools`, so the reviewer *could* read other tickets and comment
  across sessions. The reviewer's brief tells it not to; the brief is not a boundary.
  Accepted "for now" and monitored: the review ticket's activity log is what to watch, and
  a single `mcp__linear` entry in `disallowedTools` is the closure if it is ever needed.
- **A separate trust domain.** Same host as the dispatcher and the coding sessions;
  isolation is account + sandbox + per-ticket worktree + tool fence, not a separate
  machine.
- **An independent credential.** Same Claude credential family. `auth.review: api-key`
  remains available for anyone who would rather pay than contend for the subscription
  window; this ADR does not force it, and recommends the reviewer run on a cheap model so
  an unattended review cannot drain the interactive window.

### 4. How the verdict lands, and how a decline looks

**Decision: a PR comment, never an approval, and a decline that is loud and distinct.**

The reviewer's whole deliverable is one document in the §14 shape — under option 4, the
**last** fenced `pipeline-review/1` JSON block in its final message, read back from the
review ticket's `response` activity, since a reviewer with no `Write` produces no file.
Last, not first: a reviewer that restates the template early and writes its real finding
later must not have the template published. That rule is safe only because the input is one
activity, written by one author. Over a whole thread, "last" would mean whoever commented
most recently, and any session holding tracker tools can comment there. A
**deterministic publisher** — not the model — validates it whole against the schema,
computes `usable` / `max_severity` / `meets_threshold`, and posts **one** PR comment
through `scripts/gh_fallback.py` (which has no merge endpoint by construction). This reuses
the one piece of the cloud design that survives unchanged: the poster is deterministic and
separate from the reviewing model.

Three outcomes, three visibly different results, per contract §13:

| Outcome | PR comment | Exit |
|---|---|---|
| Reviewed, findings | the findings, highest severity, whether a fix pass will start | 0 |
| Reviewed, clean | explicit "no findings against correctness, security, test assertions, or scope" | 0 |
| **Could not review** (no basis / unusable findings / reviewer failed) | **"This PR was NOT reviewed — treat as unreviewed, not clean."** | **non-zero** |

A decline is never exit 0 and never a silent green — the exact inversion of the TOD-112
defect, where a declining cloud run exited green and nothing distinguished "reviewed and
found nothing" from "could not review at all." A malformed findings file is **unusable, not
silently smaller**: the whole document conforms or the review is reported unreviewed
(§14). The comment is **not a required check**, so a review can never wedge the merge
queue.

### 5. Bounce

**Decision (amended 2026-09-06): a bounce is a re-prompt of the original coding session;
the bound and the count live where the session cannot reach; a fix ticket is the
fallback.**

- **Who notices, who starts it:** the poller watches open pipeline PRs' check-runs and
  review outcomes; a terminally-red required check, or review findings at or above the
  threshold, is a bounce candidate. Before acting it reads the **original** ticket's state
  through Linear: terminal (Done/Canceled) ⇒ skip, with the reason logged — the
  dispatcher deletes a worktree only when its issue reaches Done/Canceled/deleted
  (`EdgeWorker.ts:3488-3530` → `GitService.ts:1001-1083`), so a terminal ticket's tree is
  gone and nothing can resume there.
- **The primary bounce is a comment, not a session.** The poller posts, as the owner, one
  comment in the original ticket's agent-session thread (`issue.agentSessions` → the
  session's root comment; `commentCreate{issueId, parentId, body}` — the exact parent
  shape is marked for the live test). The dispatcher treats a new comment in that thread
  as a `prompted` event and **resumes the same Claude session in the same worktree on the
  same branch** (`EdgeWorker.ts:7256-7443`, `RunnerConfigBuilder.ts:431-437`); the resumed
  session's prompt is only the new comment, wrapped as `<new_comment>` with author and
  timestamp. The comment carries the findings inside an `<untrusted-review-findings>`
  fence and a fixed instruction block: *Bounce n of max. Fix the findings at or above the
  threshold with the smallest change; stay inside the ticket scope; push to the same
  branch; do not open a new PR; do not edit the PR title or body; never merge or approve;
  if a finding is wrong or out of scope, say so in this thread and stop.* After a daemon
  restart the session record is reloaded from state; a missing Claude session id degrades
  to a fresh session with just the comment, worktree still reused.
- **The ledger row is appended first**, then the comment is sent — a crash between the two
  costs one unsent bounce, never an under-count.
- **Where the bound lives:** `maxBounces` and `reviewSeverityThreshold` are read from
  `delivery.json` **on the repo's committed default branch**, fetched fresh — the same
  "config from the default branch" anchor the rest of the pipeline already trusts, and not
  writable by the PR head, the branch, the session, or the dispatcher. **Absent ⇒ the
  bounce tier is off, and the poller says so** (contract §13); present but unreadable ⇒
  broken, and it refuses loudly.
- **How it is counted:** from a **driver-owned append-only ledger keyed by PR number**
  under the role account's home, outside every worktree, so the fix session has no write
  path to the number that decides whether to spend another one. The count is never taken
  from Linear — a session holds tracker tools and could delete the very comments a
  Linear-counted budget would read. Each re-prompt is nevertheless *stamped* with a
  visible record line so a person reading the ticket sees the same number the ledger
  holds; that cross-check runs one way only, able to make the driver refuse and never to
  grant a bounce the ledger has not recorded. On exhaustion the driver posts one
  comment on the PR and one on the original ticket saying the budget is spent and a person
  is needed; **it may apply `agent:needs-human`** — the one label E ever writes, written by
  a dispatcher-side component and never by a session — and stops.
- **Fallback — a fix ticket.** Only when the original ticket has no agent session, or the
  re-prompt cannot be delivered: the poller creates and delegates a fix ticket in the
  Reviews team whose description carries `[repo=<repo-name>#<pr-head-branch>]` so the
  dispatcher cuts the worktree from the PR branch, with the instruction to push to that
  branch and open no PR. Implemented minimally and marked clearly as the fallback path.
- **The re-prompt access check tests the delegator, not the commenter**
  (`EdgeWorker.ts:5281-5290, 6726-6745`) — accepted by the owner for now, and one reason
  the original ticket's delegation must be the owner's.
- **KIT-51, restated:** E cuts no worktree of its own, so the only trees in play are the
  dispatcher's, deleted on *their own* ticket's terminal transition. **E never moves the
  original ticket to Done**, never merges, never enables auto-merge, so E never triggers
  that deletion — the human's merge does, and the timing hazard is KIT-51's to own.
  Closing the *review* ticket deletes only the reviewer's tree.

### 6. Coupling

**Decision (amended 2026-09-06): the poller talks to GitHub and Linear and reaches into
the dispatcher for nothing; everything dispatcher-specific is configuration, named here in
full.** No dispatcher import, no read of its state directory, no assumption about its
worktree layout — and note that sharing the dispatcher's *role account* (see decision 1)
is a deployment placement, not a coupling: nothing in the poller reads a dispatcher file.
The poller's whole vocabulary is: list agent sessions and their issues' attachments,
create-and-delegate a ticket, read a ticket's agent-session activities, read a ticket's
state, post a comment, and (on exhaustion) apply one label. A replacement dispatcher that
honours a Linear delegation, records an agent session on the ticket it works, and posts
its result as a `response` activity needs the poller changed nowhere.

**The coupling seam is exactly these dispatcher knobs**, all of them set by the operator
on the dispatcher's side and none of them read or written by the poller:

| Knob | What E relies on it for |
|---|---|
| per-entry `disallowedTools` | the reviewer's tool fence (decision 3) |
| per-entry `appendInstruction` | the reviewer's brief — `promptTemplatePath` is stripped |
| `teamKeys` routing | the fallback when a review ticket carries no routing tag (amended 2026-09-08) |
| description-tag routing, ahead of team keys | which repository's clone the reviewer reads (amended 2026-09-08) |
| description-tag base-branch override (`[repo=<name>#<branch>]`) | the fix-ticket fallback's checkout; and the reason the poller strips the tag from everything it copies |
| label-based model selection | the reviewer's cheap model |
| agent-session re-prompt on a thread comment | the primary bounce (decision 5) |
| delegator check on `allowedUsers`; app-actor delegation blocked | why the poller's key is owner-scoped, and why a session cannot trigger its own review |
| worktree per issue id, deleted only on terminal state | the reviewer's tree is its own; closing the review ticket deletes only that tree |
| an **agent session recorded on the ticket** it worked | Linear-driven discovery: no session on an issue means no PR is found through it (added 2026-09-06 with the repo-list removal) |

Change dispatchers and this table is the porting checklist. The one Cyrus fact the old
text isolated — *worktree force-deletion on ticket close* (KIT-51) — is the last row, now
one of eight rather than the only one, and E's answer is unchanged: never move the
original ticket, never merge.

## Where Stage E lives

E is split the way the whole pipeline already splits, and the split answers "does E belong
in the kit or in Phase 2?": **both, by role.**

- **Mechanism → the kit.** The poller-side publisher, decline logic, findings validator,
  sanitizer and bounce ledger are `scripts/`, source-agnostic and CI-tested, beside
  `gh_fallback.py` and `check_schemas.py`. The cloud templates E supersedes already live in
  `templates/workflows/`; their rubric, severity logic, findings shape and bounce
  accounting are mined, and their transport discarded.
- **Activation → the deployment.** The Reviews team, the dispatcher's second repository
  entry, the two system LaunchDaemons that run the poller and the bounce driver as the
  dispatcher's role account, and their env file are operator steps, written generically in
  `docs/STAGE-E-OPERATOR.md` and filled in privately per deployment (KIT-95). No session installs or edits a service. This is the same
  mechanism-in-repo / activation-per-deployment seam `docs/AUTONOMY.md` already documents,
  and it is what keeps E liftable to a replacement dispatcher unchanged.

## What is accepted as lost, versus the original Stage E

- The **dispatch-time snapshot** as a hard, unforgeable, pin-carried fact. Replaced by a
  best-available basis with a loud decline, and an explicit edit-flag. Weaker; honest.
- **Structural non-approval** via a scoped token. Replaced by a dispatcher-enforced tool
  fence (`disallowedTools`) plus the sandbox. Weaker; a config edit can widen it where a
  token scope cannot.
- **CI as a free fresh, isolated runner.** Replaced by a same-host sandboxed session in
  its own per-ticket worktree. Weaker isolation; accepted with the reasoning the
  2026-08-26 ADR recorded.
- **Budgets, WIP and the attempt counter on the review lane.** Not restored here — those
  are the rest of KIT-18. The review lane is bounded by its toolset and a cheap model, not
  by a pinned budget.

## Verified

**Added 2026-09-06 — read from source, not docs.** Every dispatcher claim in the Update
block and in decisions 1, 3, 5 and 6 is `file:line` against the published dispatcher
package **v0.2.69** and the Linear SDK **64.0.0** typings, both extracted read-only to a
scratch directory outside this repository:

- **Session start:** webhook signature checked; `agentSession.creator` (the delegator)
  must be in `userAccessControl.allowedUsers`; routing by team key to a repository entry;
  `git fetch origin` then `git worktree add --track -b <linear-branch> <home>/worktrees/<ISSUE-ID> origin/<baseBranch>`
  (`GitService.ts:734-736, 833-899`). The worktree *path* is keyed on the issue
  identifier; the branch is Linear's suggested name and no API field sets it. A sub-issue
  bases on its parent's branch when one exists (`GitService.ts:452-477`); a
  `[repo=<name>#<branch>]` description tag overrides the base branch and routes to every
  entry it matches — **and it matches on three things, not one** (re-read 2026-09-08 in
  v0.2.69's shipped `dist/RepositoryRouter.js:253-269`, correcting an earlier reading of
  this line that said `githubUrl` only): a `githubUrl` ending in `/<tag>` or `/<tag>.git`,
  then `repo.name` compared case-insensitively, then `repo.id` exactly. Description-tag
  routing is **priority 1**, ahead of labels, projects and team keys (`:110-161`). The
  review entries carry no `githubUrl` at all, so the NAME match is the one that puts a
  reviewer in the right clone; a reading of this line as githubUrl-only would have made
  that mechanism inert.
- **Sandbox:** dedicated non-admin account; Seatbelt `allowWrite [worktree, TMPDIR]`,
  `denyRead ['~/']` (`RunnerConfigBuilder.ts:558-579`); shell network only through the
  allowlisting proxy; `WebFetch` bypasses the proxy. The whole process env is copied into
  every session unscrubbed (`session-env.ts:51-53`).
- **Tools:** the permission callback allows every tool but `AskUserQuestion`
  (`ClaudeRunner.ts:321-328`), so `allowedTools` restricts nothing; `disallowedTools`
  reaches the runner (`RunnerConfigBuilder.ts:439`, `ClaudeRunner.ts:563, 701`);
  `promptTemplatePath` is stripped (`ConfigService.ts:47-58`); `appendInstruction` is
  appended inside a `<repository-specific-instruction>` element
  (`PromptBuilder.ts:958-966`). Model selection on the Linear lane is by ticket label.
- **Result and resume:** the final result is posted as a `response` activity
  (`AgentSessionManager.ts:1269-1281`); no finishing state is written to Linear; there is
  no review or CI loop. A new comment in the agent-session thread (`prompted`) resumes the
  same session — same worktree, same branch, `cwd = session.workspace.path`,
  `resume = claudeSessionId` — with only the new comment as the prompt
  (`EdgeWorker.ts:7256-7443`, `RunnerConfigBuilder.ts:431-437`). The `prompted` access
  check tests `agentSession.creator`, not the commenter (`EdgeWorker.ts:5281-5290,
  6726-6745`). App-actor delegation arrives with no creator and is blocked
  (`UserAccessControl.ts:31-43`).
- **Deletion:** worktrees are removed only when the issue reaches Done/Canceled/deleted
  (`EdgeWorker.ts:3488-3530` → `GitService.ts:1001-1083`).
- **Linear's GitHub integration** links a PR to an issue by branch name, PR title, or
  magic words in the PR *description* — not by PR comments — and by default moves the
  linked issue to In Progress on open and Done on merge. Hence the Reviews team runs with
  those automations **off**, and the review ticket appears nowhere a link could form.
- **The SDK typings** carry `IssueCreateInput.delegateId`, `CommentCreateInput.parentId`,
  `AgentSession.comment` (the thread root) and `AgentSession.activities`. Whether a
  `commentCreate` under that root is what produces the `prompted` event end-to-end is
  **not confirmable from typings** and is live test 5 in `docs/STAGE-E-OPERATOR.md`.
- **Linear-driven discovery** rests on the GitHub integration attaching a PR to the issue
  whose id the branch carries — observed on this project's own board, where the pipeline
  tickets carry their PRs as attachments. The dispatcher's part is only that it records an
  agent session on the ticket it works. Neither the paging window nor the attachment
  `sourceType` filter can be settled from typings; both are live test 2.
- **The placement of the poller (owner decision, 2026-09-06)** is a deployment fact, not a
  source reading: a user LaunchAgent runs only while its user is logged in, and a system
  LaunchDaemon with `UserName` starts at boot. The *missed-interval-on-wake* behaviour of
  `StartInterval` is the one platform claim here worth confirming on the machine before it
  is relied on; the heartbeat files make its absence visible either way.
- **Unverified until the live tests run:** every row of that numbered list — the
  owner-key delegation passing the delegator check, discovery finding the PR through
  Linear, the sandboxed reviewer receiving the inlined diff and returning the block, the
  poller reading the `response` activity back, `disallowedTools` removing `Bash` from the
  model's tool list, a re-prompt resuming the original session and pushing to the same
  branch, whether a comment made with the *app user's* identity resumes another session
  (live test 7, which measures accepted risk 3 rather than confirming a design claim), and
  closing the review ticket deleting only its own worktree. This ADR records the design as
  verified against source and the deployment as **not yet exercised**.
- **The fork-guard bug** is a finding *about* the merged reviewer, made while reading it
  for this update: `isCrossRepository` requested, never checked. It is fixed with a selftest
  in the accompanying reviewer-core change, and recorded here because a review lane that
  reviews fork PRs on a public repository is the one thing the webhook-unwired decision
  exists to prevent.

**From 2026-09-05:**

- **The reviewer core is built and proven on a real PR (#63).** `scripts/pipeline_review_local.py`
  resolves a PR, gathers `base...head`, normalizes the findings against
  `schemas/review-findings.schema.json`, and posts one comment through `gh_fallback.py`.
  On PR #63 it posted, run with no basis, a **distinct decline comment** (`## 🛑 Stage E —
  … was NOT reviewed`, exit 3), and, run with KIT-90's acceptance criteria as the basis, a
  **genuine review comment** (4 findings, highest `high`, exit 0). *(At that point it also
  ran the review itself through an owner-account `claude -p`; option 4 removes that path —
  see Trigger — and the core becomes the poller-side publisher, ingesting a findings
  document instead of producing one.)*
- **The reviewer earned its keep by reviewing its own PR.** That genuine review found a real
  §13 hole in this very slice: `pr_diff()` and `post_comment()` were unguarded where
  `pr_metadata()` was not, so a GitHub hiccup would have crashed with a traceback and posted
  *nothing* — a silent could-not-review, exactly what the file exists to prevent. It was
  fixed in the same PR (every "could not review" path now routes through one `decline()` that
  posts a distinct comment and returns a documented exit code), and a driver-level
  integration test was added so `--selftest` exercises `review()` and not only its helpers.
  Catching, on its first run, the class of bug it was built to catch is the strongest
  evidence the design works.
- **The decline is not green.** `python3 scripts/pipeline_review_local.py --selftest`
  asserts the three outcomes have three different exit codes, that a malformed findings
  file is reported unusable rather than silently reduced, and that the script constructs no
  approve / label / merge API path (the same guarantee `gh_fallback.py` makes, asserted
  the same way).
- **Cyrus's four accepted event types and the absent `pull_request` trigger** are taken
  from the 2026-08-26 ADR, which read them from source `@85aeaaa`; not re-verified here,
  and not upgraded to a fresh measurement.
- **The webhook-unwired decision is unchanged from the audit**, not reopened. Nothing in
  this ADR wires it, and the gate to reopen it is stated above.
- No `delivery.json` ships in the kit, so every pipeline script — including the new one —
  is inert here; its `--selftest` is what has teeth, and it runs in CI.
