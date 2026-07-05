# Contributing

Bootstrapped a project from this template and learned something transferable — a hook
improvement, a workflow gotcha, a doc fix? PRs welcome. The mechanics:

- **Branch** `<type>/<short-kebab-desc>` (feat|fix|chore|refactor|docs) off `main`;
  the repo's own hooks will insist.
- **The battery must stay green**: `npm run test:hooks` (also runs in CI). If you
  change a hook, **add a block/allow case in the same PR** — an untested guard is a
  future regression.
- **Hook scripts + `settings.json` are self-protected** — even your Claude can't edit
  them directly; changes go through the scratch-file + terminal flow described in
  `.claude/hooks/README.md`.
- **Conventional commits**, and PR bodies in the concise format the template enforces
  on itself (2–3 sentences, one-line bullets, one verification line, ≤ ~150 visible
  words — the PR template guides you).
- A decision that changes the kit's shape, a guard, or the security model gets a dated
  ADR (`docs/adr/YYYY-MM-DD-slug.md` — or just run `/new-adr`).

Maintained solo; `main` is the only supported version. Small, focused PRs merge fast.
