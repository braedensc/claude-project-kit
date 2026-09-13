# ADRs — one file per decision, date+slug, no numbers

The convention comes from a production build where numbered ADRs (`0001`–`0024`)
collided **three times** during parallel sessions: numbers are claimed at merge-to-main,
so two open branches draft the same number and the later merge renumbers (one ADR was
renumbered twice, 0019 → 0020 → landing as 0022). The structural fix:

## The convention

- **One file per decision** in `docs/adr/`.
- **Filename: `YYYY-MM-DD-short-slug.md`** — no sequence number. There is no shared
  counter to claim and no common file tail to append to, so parallel sessions add ADRs
  with zero coordination.
- **Slug on significant words only** — don't auto-truncate titles (an auto-generator
  once produced `…-branch-protection-the.md`); trim trailing stopwords.
- After adding a file, add **one row** to the project's `docs/ARCHITECTURE.md` index
  table (`| ADR | Date | Decision |`) — a single-row edit, the only shared touch.

## What goes inside

```markdown
# <Title>

**Date:** YYYY-MM-DD · **Status:** Accepted · **Context:** <stage / PR #>

## Decision
<what, in 2–4 sentences>

## Why
<forces, alternatives rejected by name, accepted tradeoffs + future hardening>

## Verified
<the proof it works — adversarial where possible: two-user tests, curl matrices,
run-twice idempotency. An ADR without evidence is an opinion.>
```

Conventions that proved their worth:
- **Amend in place** with dated `**Update (…)**` blocks rather than superseding files —
  cross-references stay stable.
- **Status stays truthful:** when a decision *is* replaced, the PR that supersedes an
  ADR flips the old one's `**Status:**` to `Superseded by <file>` *and* its index row,
  in the same change.
- **Deferrals get ADRs too**, with a named revisit trigger — and a dated re-decision
  ADR when the trigger fires. The log records what was *not* done and when.
- **Deviations from plan** are recorded with owner sign-off + date + the re-entry path.

## When to write one

- **Bootstrap phase:** liberally — roughly one per significant PR. The density is
  deliberate scaffolding (docs/LESSONS.md → "docs are scaffolding first").
- **Post-launch:** only for decisions that change architecture, a security boundary, or
  an external service. Routine features and fixes need none.

## Index

| ADR | Date | Decision |
|---|---|---|
| [Kit shape & conventions](2026-07-03-kit-shape-and-conventions.md) | 2026-07-03 | The kit's own key choices: inert templates dir, self-hosted hooks from PR #1, battery-as-permanent-test, PR-flow-from-commit-1 |
| [Ecosystem parity](2026-07-04-ecosystem-parity.md) | 2026-07-04 | Skills (/ship, /new-adr), native permissions.deny, inert @claude Action + .mcp.json.example, minimal devcontainer, advisory SessionStart hook |
| [Publish readiness](2026-07-04-publish-readiness.md) | 2026-07-04 | MIT license; no shipped PATH override; fail-closed on missing python3; enumerated deny list (.env.example readable); stranger-proofed docs |
| [Hook hardening + session root](2026-08-23-hook-hardening-and-session-root.md) | 2026-08-23 | stderr block reasons; path-target secret guard (+ SSH/cloud credential files); egress guard; anchored rm flags; `settings.local.json` + widened mutation net self-protected; fail-closed dispatch; session root anchored on `CLAUDE_PROJECT_DIR` (subagent worktree fix) |
| [Untrusted-ticket-data fence](2026-08-24-untrusted-ticket-data-fence.md) | 2026-08-24 | Tracker text is third-party data, never instruction: one fence everywhere, tag neutralized inside the payload, length capped; the pin is authority and `session-start.py`'s output explicitly is not |
| [Attempt-counter durable home](2026-08-24-attempt-counter-durable-home.md) | 2026-08-24 | The attempt count lives in a dispatcher-owned `pipeline-dispatcher-state/1` record bound per backend by `dispatch.statePath`; ticket-comment and `pinsRoot` transports rejected by name; bounded-not-exact, with `agent:needs-human` as the durable backstop |
| [Pipeline guards, dispatcher-anchored](2026-08-24-pipeline-guards-dispatcher-anchored.md) | 2026-08-24 | Six delivery-pipeline guards, inert unless `delivery.json` exists; pin file outside the worktree + config from the default branch as the trust anchors; per-guard fail direction |
| [Config anchor + pin expiry](2026-08-25-config-anchor-and-pin-expiry.md) | 2026-08-25 | An expired pin is *broken*, not absent (ticket mode fails closed); the git ref store and `origin` are human-only so the config anchor isn't model-movable, with `pinsRoot` containment capping the blast radius; no in-session `raw` → `ready` path at all, and `autoApproveProvenance` is the out-of-session tier's knob only; `lifecycle-label` implemented in the hook |
| [Autonomy tiers + telemetry](2026-08-24-autonomy-tiers-and-telemetry.md) | 2026-08-24 | Three autonomy rungs, each off by default above the first; `epic/*`-only auto-approval with the epic verified; the merge capability held by GitHub's native auto-merge and never by an agent; telemetry swept on natural keys into §10 tables; one summary object feeding both the dashboard and `/weekly-review`, whose three limits are enforced by a script |
| [Protected-label guard](2026-08-25-protected-label-guard.md) | 2026-08-25 | An acknowledgement is the human's to give: the §6 label set is hook-blocked in every `gh` spelling that writes it (print the command for the human instead); CI checks **who** applied the label, not just that it is there; the gate runs from the base sha and `scripts/check_*.py` joins the gated floor so a head cannot weaken its own judge |
| [Label scope + read-path drift](2026-08-25-label-scope-and-read-path-drift.md) | 2026-08-25 | Every §6 label is workspace-scoped (`teamId` omitted) and scope cannot be converted; a deleted ID fails loudly only on a **write**, so every read path detects staleness itself — resolution still by ID, the name as diagnostic only, severity keyed on `labels.required`, one shared `resolve_label_keys`, and the dispatcher's supervision labels flipped fail-open → fail-closed per ticket |
| [Budgets belong to whatever writes the pin](2026-08-25-external-daemon-budget-enforcement.md) | 2026-08-25 | No enforcement script goes in an external agent daemon's pre-session hook: it has no veto (failures are caught and the session starts anyway) and it is a worktree file running with daemon privileges, so it trades zero enforcement for real escalation. Teardown-as-observer, daemon-state concurrency and in-session bounding all rejected by name; `AUTONOMY.md` states which budgets hold under which dispatcher, and a workspace-scoped spend cap is the out-of-band backstop |
| [Grader floor gates supervision, not names](2026-08-25-grader-floor-gates-supervision-not-names.md) | 2026-08-25 | The gated floor is a capability test, not a naming convention: `scripts/pipeline_*.py`, `scripts/jsonschema_mini.py` and `templates/workflows/pipeline-*.yml` join it, telemetry stays out on §4's zero-authority grounds, and a selftest holds `FLOOR` + an `UNGATED` ledger to covering both directories exhaustively so a new file cannot escape by being named something the globs miss |
| [Staging mirrors are on the floor](2026-08-25-staging-mirrors-are-on-the-floor.md) | 2026-08-25 | Protection must attach where bytes are **decided**, not where a file becomes visible: `templates/workflows/**` and `templates/hooks/**` are the inert copies that become `.github/workflows/**` and `.claude/hooks/**`, so a pinned session editing them chose what CI later runs and the activation is a bare `git mv`. Stated as a mirror relation — every floored path's staging copy is floored with it — across the hook floor, `REQUIRED_RISK_PATHS` (which gains the active workflows too, since the pair is inseparable), the auto-approve floor and the exhaustiveness ledger; the CI gate's stack-template exclusions stand on the amended ADR's own capability test |
| [Where the review session runs](2026-08-26-where-the-review-session-runs.md) | 2026-08-26 | Review moves to the dispatcher's machine for the `local-daemon` backend; cloud templates retained for `github-actions`. Nothing built — Cyrus has no PR-opened trigger and hardcodes a *"commit and push"* system prompt, so this is construction, not configuration. Read-from-default-branch closes the rubric vector but not ambient context, worktree reuse, trigger authorship or self-approval; each has a closure, so none disqualifies. Weaker independence and a contended subscription window accepted, with the reasoning recorded. **Local-daemon half superseded 2026-09-05.** |
| [Stage E under a delegation-bound dispatcher](2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md) | 2026-09-05 · **amended 2026-09-06 (option 4), 2026-09-08 (one review entry per repository), 2026-09-12 (three daemons; the shipped basis tier; conclusion moves the ticket; re-review after a bounce; exercised in production)** | Stage E for what the dispatcher actually does, read from source: a poller running as **the dispatcher's own role account** under a system LaunchDaemon (a LaunchAgent would be down after a reboot to the login window; credentials in its own env file, never the dispatcher's — which is copied into every session) discovers pipeline PRs **from Linear** rather than a repo list, and creates **and delegates** one review ticket each (diff inlined, sanitized, never parented, never linked; GitHub webhook stays unwired); the dispatcher runs the reviewer in its own sandbox behind a `disallowedTools` fence — `allowedTools` restricts nothing and `promptTemplatePath` is stripped, corrections to this and the 2026-08-26 ADR; the basis is the criteria *as of delegation* or a loud decline; the verdict is a PR comment, never an approval; a bounce is a **re-prompt of the original coding session** in its thread, counted from a poller-owned ledger appended first, with `maxBounces` from the committed default branch (absent ⇒ off, and said) and a fix-ticket fallback. **No code path launches a session as the owner** — the option-3 launcher is rejected on four verified grounds. Coupling is a named table of dispatcher knobs; the poller only creates tickets, reads activities and posts comments. **Amended 2026-09-08:** one review entry per reviewed repository, chosen by a `[repo=reviews-<name>]` tag the poller writes into its own trusted header — team-key routing alone put every reviewer in one clone, whichever repository it was judging. Accepted risks recorded (poller and sessions share a uid, so the sandbox's home deny-read is the whole boundary — with a stated revisit trigger; Linear MCP in all sessions; delegator-only re-prompt check); fork-guard bug found in the merged reviewer; every session reads `docs/SESSION-BRIEF.md`. **Amended 2026-09-12:** a third daemon joins review and bounce (the finding poller, #94); the basis spike resolved to the `live` tier, whose criteria-edit flag reports *unknown* rather than false (#82); review now **concludes** by moving the coding ticket into a needs-approval lane, the one state Stage E writes on a coding ticket (#88); every delivered bounce leaves a re-review request the poller honours when the head moves (#87, #93); and the deployment is exercised — the loop has run end to end on live pull requests since 2026-09-08 (#99), with the unproven remainder named |
| [Stage A triggered from Linear](2026-09-06-stage-a-triggered-from-linear.md) | 2026-09-06 | **Proposed** (KIT-105) — the ticket factory startable from Linear by Stage E's transport pointed at planning: a human delegates an `idea` ticket into a dedicated Planning team, the dispatcher runs a sandboxed session whose *plan-not-code* identity is fixed by that team's dispatcher entry (tool-fence + brief), not by a label the session reads (KIT-41). The session holds no create; it emits a proposed epic-tree as a §8 `ticket-create` **extended to carry children**, and an owner-scoped executor DoR-gates it and materialises it — epic in intake as `provenance:agent`, children under the *fresh* epic as `provenance:epic`; **the owner moving the epic out of intake is the intended sole release.** Security argument's load-bearing finding (from an adversarial review): §8 binds only the safe-outputs channel, so the guarantee rests on the **tool-fence** removing the direct `save_issue` surface — plus fixes to `check_auto_approve` (gate on a specific approval state) and the tracker guard (planning-mode + update). Build gated on Stage E, KIT-102, KIT-96's executor, KIT-104; design + security argument only |
| [Approve tier gates epic approval on `ready`](2026-09-06-approve-tier-epic-approval-state.md) | 2026-09-06 | Build-fix A of KIT-105: `check_auto_approve.check_epic` approved on **any** non-`raw` epic state (`working`/`review`/`done` all counted), so a human parking an epic or a board automation nudging it forward released the whole `epic/*` child tree off a signal nobody deliberately gave. Tightened to require **exactly `ready`** — the contract's approved state and the one children release into — kept over a dedicated `approved` state (no sixth state to provision in a template); §5 rule 2 / §11 / `AUTONOMY.md` amended from "out of intake" to `ready`. Closes the PreToolUse guard's practical gap for free (writing any non-`ready` state now buys nothing). Actor check (was the mover the agent?) deferred honestly — it needs history data the offline gate never receives; shipping it inert would be a §13 absence-vs-failure defect. Latent when written: no `delivery.json`, no live `provenance:epic` producer (KIT-104) |
| [Human-action notifier + reply relay](2026-09-06-human-action-notifier-and-reply-relay.md) | 2026-09-06 | **Accepted** (all six decisions ratified 2026-09-11). The events that need a person (blocked-session question, PR ready to merge, review declined, bounce budget spent, epic awaiting approval) are pushed to a dedicated private chat channel by a dispatcher-side job beside the Stage E poller — never by a session, so no allowlist widening and no credential in a session. Replies re-enter only as one comment in one session thread, sender-pinned and reply-to addressed, with no command grammar; approve/merge/label/state never transit the channel. Channel chosen on its reply API being a **poll**, not a socket or a public URL: **Slack** for the reference deployment, in a private channel because on the free plan membership is the only access control there is. Ships as three pieces — notifier, then the conversational lane, then the relay. Makes the notifier the `agent:blocked` writer under a pin-less dispatcher |
