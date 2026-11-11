# Deployment documents: keep them in a private repository

*Written 2026-11-10. For anyone running this kit on a real machine. The kit is public. The facts of your deployment must never go in it.*

This kit is a template. Anyone can clone it, so it must name no project, machine, account or repository. But running it produces writing that is entirely about one deployment: which job runs under which account, which board holds the review tickets, what to type on a fresh machine. That writing needs a home that is not the kit, and not one laptop's download folder.

**The rule.** Deployment-specific documents live in a **private repository of their own**. They never go in the kit.

---

## What goes where

| The document is about | It goes in |
| --- | --- |
| How a part works and installs, on any machine | The kit's `docs/` |
| This machine's values: accounts, board names, ports, the exact commands you type | Your private documents repository |
| A published page or a tracker document | The original stays where it is published. The repository keeps its source and a link |
| A lesson a second person would need, true on any machine | The kit's `docs/LESSONS.md` |
| What a session learned about this one machine | Your private repository, or the session's own memory |

Good: a runbook step with your real job label in your private repository.
Not that: the same step in the kit with the names filed off. It helps nobody, and it breaks the kit's source-agnostic rule.

---

## What every document carries

Two things, without exception.

1. **A date, in its first three lines.** A document with no date cannot be trusted against a machine that changed yesterday.
2. **A "what is not proven" section.** List every claim that has not been watched working in production. End each entry with a ticket id, or with the exact words *no ticket yet*.

A limitation written only in prose gets rediscovered, expensively. Two real gaps were once recorded honestly in a file that never shipped, and were found again months later by an audit that cost money.

Good: "The conflict loop has never fired in production (a ticket id)."
Not that: "The conflict loop handles a pull request that goes conflicting after its session ends." True of the code, false of the world, and the reader cannot tell which.

---

## Commit rules for the private repository

- **It may have no pipeline and no pull requests on purpose.** Then sessions commit straight to `main`. That is safe only with a secret gate.
- **The gate:** a pre-commit hook runs a secret scanner and a token-shape scan over the staged files, and a stricter scan runs by hand before every push. A hit means redact and scan again. It never means push anyway.
- **Check the hook copies renames.** A hook that filters out renamed files lets a renamed file with a secret appended reach no scanner. Check the file list your hook uses.
- **Never commit:** an environment file, a key, a credential file, a copy of a role account's home, a session transcript, or a token value, even briefly.
- **Settings files** that hold no secrets may be committed. Read each one in full before copying it.

---

## Keeping it honest

- **Update the index in the same commit** as the document it points at. An index that lags is worse than none.
- **Say which copy is the truth.** For a published page or a tracker document, the original is the truth and the repository holds a snapshot. Record the page's version when you refresh it.
- **Refresh snapshots on a schedule or by an owner.** A copy of session memory that nothing refreshes stops being useful. Name who refreshes it, or accept that it ages.
- **Mark a document "behind" the moment the kit changes under it.** Do not wait for it to be wrong.

---

## Can a dispatched session read it?

Not by default. A dispatched session's repository token is scoped to named repositories at setup, and a private documents repository is not on that list unless you add it. This has not been measured. Treat it as **not readable** until you test it, and write down what you find. If a session needs a fact from it, the fact belongs in the ticket or in the kit, in a form with no deployment detail.

---

## What is not proven

- That sessions follow this rule without a reminder. It is a convention; nothing blocks a session from writing a deployment fact into the kit. The kit's provenance scan catches absolute home paths and real emails only. No ticket yet.
- That a dispatched session can or cannot read a private documents repository. Unmeasured. No ticket yet.
- That a refresh cadence for snapshots will hold. No ticket yet.
