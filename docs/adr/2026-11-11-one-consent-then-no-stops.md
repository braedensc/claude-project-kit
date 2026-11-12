# One consent, then no stops: the install asks once, up front

- **Status:** Accepted, 11 Nov 2026 (KIT-664, for KIT-654). The owner chose this flow on 11 Nov,
  answering the master review's decisions (KIT-277). The front door is built (the first screen, the
  one consent and the root-owned record: KIT-717). The installers are next.
- **Date:** 2026-11-11
- **Replaces:** the master review's earlier plan, which kept one sign-off per part and had the
  person type each first-pass number at switch-on.

## Context

A new user installs the pipeline through twelve installers. Counted on 11 Nov 2026, they stop for a
person 186 times. About 80 of those stops truly need a person: making an account, signing in,
pasting a key. The rest are a yes that could be asked once, a command an installer prints for the
person to copy, a settings line typed by hand, or a sign-off.

A sign-off is a card the person signs with their initials. Seven cards make the person read a
first-pass number before a job is loaded: how many pages, reviews or plans the job's first pass
would make. The aim was that no job starts with a surprise.

The owner's answer: the sign-off-as-you-go flow is wrong, and an install should just work. At the
start, offer Manual or Automatic, and one consent that says what may happen. No sign-offs during the
install. Settings live on the machine, never in a repository.

No installer reads an answer given in advance today. The only ways in are flags, the settings file,
the role account's stored keys, and one environment variable.

## Decision

### 1. One choice, then one consent

- The one command's first screen asks one question: **Automatic** ("things just work") or
  **Manual** ("show me each change and ask"). The choice is kept in the Mac's settings, and the
  next run offers it as the default.
- Then one consent page, in plain words. It is drawn from the parts list (`pipeline-parts.json`),
  so a later app window can draw the same page. It says:
  - what will be installed: background jobs, under a separate account;
  - what may be restarted;
  - what may be created in the tracker, the code host and the chat product;
  - what it may spend: the subscription only;
  - what it never does: merge, approve, change a guard, or read a key back.
- The person agrees once, at a terminal.

### 2. In Automatic, nothing is asked after the consent

- Every value only a person has is asked before the consent: the names, and each key at a hidden
  prompt.
- After the consent, no part asks a question or prints a card to sign.
- A step only a person can do (create an account, sign in, click install) is carried past. It
  appears on the short list at the end.
- In Manual, each change is shown and asked, as today.

### 3. A ceiling replaces the signed first-pass number

- Each part declares a ceiling for its first pass in the parts list: at most so many pages,
  reviews or plans.
- The installer measures the first pass, as today's dry runs do.
- At or below the ceiling, the part is switched on with no question.
- Above it, the part is installed and left off. The end list says why, with the number and the
  ceiling. It never becomes a question in the middle.

### 4. Installers load their own jobs after the consent

This is what changes about loading jobs.

- Today several installers write a job and print the lines that load it, for the person to copy.
  One never loads its job at all.
- Now, in Automatic, the installer loads the job itself, after the consent, and only when its part
  is on.
- A job written while its part is off must not start at the next boot. The system loads every job
  file in its folders at boot, so an off job is disabled, not merely left unloaded.
- Privileged steps still ask for the Mac password, once per run.

### 5. A record a session cannot write

- At apply time the command writes a root-owned record: the mode, the consent page it showed, and
  each consent-relevant setting it applied. That is each part on or off, each ceiling and each
  job's switch.
- On a later run, a consent-relevant setting that differs from the record is shown, and needs
  consent again.
- So an edit a session makes to a settings file is never applied silently on the person's next run.
- Where the record lives is itself a setting.

### 6. What does not change

- The command refuses inside a Claude session, as root, and with no terminal, before it reads
  anything. Consent comes from a terminal, never from a file, a flag or an environment variable.
- Merging, approving and labelling stay a person's actions. The consent never covers them.
- What a machine can check, the machine checks: the routing probe and the tool lists stay.
- A judgment no interface can read, such as a team's guidance for its agents, becomes a line in the
  consent or on the end list. Its part stays off until then.

## Why

- **The stops were the problem.** 186 stops is far from asking once. About eleven questions at the
  start replace about seventy of them. The rest are carried past and listed once, at the end.
- **A ceiling is enforced; a typed number is not.** The ceiling is reviewed with the code, shown in
  the consent and enforced by the program. A typed number proves only that someone typed it.
- **The record keeps one yes honest.** With no sign-offs, the risk moves to a setting changed
  between runs. The root-owned record makes any such change visible, and asks again.

**Rejected**

- *A sign-off per part, with each first-pass number typed at switch-on.* It keeps the stops the
  owner asked to remove.
- *A separate private settings repository that no session works in.* It would protect the settings
  from a session. The owner ruled that settings live on the machine and need no repository. The
  root-owned record does the same job there.
- *A file of answers that the installers read.* A session can write a file, so the file could plant
  a yes. Answers reach an installer through a pipe from the calling program, after the consent.

**Accepted trade-offs**

- At or below its ceiling, a first pass runs with no person reading its number.
- In Automatic, a part that needs a person's step stays off until it is done. The end list says so.
- The refusal inside a Claude session reads the session's environment markers. A program that
  clears them can reach the prompt. It still cannot write the root-owned record without the Mac
  password.

## How it will be proven

- `npm run test:install` covers each path: Automatic, Manual, a ceiling met, a ceiling passed, a
  setting changed since the record, and each refusal. A mutant per rule turns a case red (KIT-654).
- Each installer's own selftest covers applying with its answers given, one installer at a time
  (KIT-655, one child ticket per installer).

## What is not proven

- **That a ceiling is as safe as a number a person reads.** It is the owner's choice of flow, and no
  test can settle it. No ticket yet.
- **The ceilings themselves.** No part has one yet; each needs a sensible default. KIT-655.
- **That the root-owned record stops a planted setting.** It is a design, not yet built or tried.
  KIT-654.
- **That an off job cannot start at boot.** Required above; not yet built. KIT-655.
- **Consent in the packaged app.** There, macOS's own password box would prove a person agreed. That
  belongs to the app's own design. KIT-278.
