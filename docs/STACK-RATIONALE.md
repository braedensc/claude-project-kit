# Stack Rationale

Every significant todoclaw stack choice, why it was made, and — the part that matters
for reuse — the principle that travels even when the tool doesn't. Tags: **T** =
transferable (adopt the principle anywhere), **S** = stack-specific (re-decide per
project). Sources: CLAUDE.md stack table + ADR-0001/0002/0003/0008/0009/0011/0015/0018.

## Frontend

| Choice | Why todoclaw chose it | Transferable principle | Tag |
|---|---|---|---|
| Vite + React 18 + TS strict | Pure SPA (no SSR need → Next.js rejected); CRA unmaintained | Pick the simplest tool that serves the actual rendering model | S |
| Pin majors; React 18 not 19, Tailwind 3 not 4 | Both next-majors were churn at build time | **Pin majors deliberately; upgrades are a scheduled task, not an accident of `npm install`** | T |
| Hand-written configs (no `npm create` scaffold) | Generator would clobber the Stage-0 security tooling already in package.json | Never let a scaffolder run after security/process tooling exists — "explicit over clever" | T |
| tsconfig project references (app vs node) | Browser code and build-tool code have different globals | Typecheck app and tooling as separate compilation units behind one `typecheck` command | T |
| TanStack Query for server state | Cache/loading/sync owned by one library, not hand-rolled | Server state and UI state are different problems — use a dedicated server-cache layer | T |
| `useState`/Context first; Zustand only if truly needed; **no Redux, ever** | Owner taste + boilerplate cost | Default to the lightest state tool that works; escalate on demonstrated need | T |
| Zod at every boundary | Inferred type = TS type — one source of truth; parse, don't trust | Validate at boundaries with schemas that double as types | T |
| Tailwind tokens over raw hex; single-source breakpoints mirrored JS↔CSS | Prevented drift (719px constant + `wide:` screen) | Define every threshold/token once; mirror, never re-declare | T |

## Backend & hosting

| Choice | Why | Transferable principle | Tag |
|---|---|---|---|
| Supabase (Postgres + Auth + RLS + Edge Functions) | Free tier, local Docker stack, RLS as the real access guard | Choose a backend whose **local instance is free, offline, and disposable** — local-first needs it | S |
| One prod project, no staging | Zero cost; the local stack is the safe place to break things | For 1–3 person projects, staging is usually cost without benefit — document the trade | T |
| Vercel (frontend) | Free Hobby tier; preview deploys; env-var scoping; security headers via `vercel.json` | Frontend host should give per-env config + headers-as-code | S |
| Anthropic API via server functions only | A Claude *subscription* can't power an app's AI; keys must stay server-side | AI keys never reach the client; all AI calls go through your server (docs/SECURITY.md) | T |
| RLS deny-by-default; no client hard-delete; append-only logs | Structural safety beats UI discipline | Make destructive ops impossible at the privilege layer, not just absent from the UI | T |

## Quality & testing

| Choice | Why | Transferable principle | Tag |
|---|---|---|---|
| ESLint (flat) + Prettier, `eslint-config-prettier` last | Exactly one tool owns formatting | Formatter owns formatting; linter owns correctness; never both | T |
| Markdown in `.prettierignore` | Doc reflow is pure diff noise | Hand-formatted docs are exempt from the formatter | T |
| Vitest + RTL, jsdom, `globals: false` | Explicit imports keep strict TS clean of ambient types | Test config should not leak ambient types into app code | T |
| Playwright, two separate configs (smoke vs golden) | CI must be structurally unable to run the DB suite | Split E2E by dependency weight; enforce the split by config file, not convention (docs/TESTING.md) | T |
| Local hooks mirror CI (layer 2 of 3) | Fast feedback, same rules as the unbypassable gate | Always have a bypassable local mirror of CI | T |
| husky + secretlint + lint-staged as the tools | Node-native, worktree-aware binary resolution proven in production | The tools are swappable — lefthook or pre-commit for the manager, gitleaks for the scanner — keep the layer | S |
| Sentry, DSN-gated init | No DSN ⇒ no-op: devs/CI/tests never send events; DSN is public, not a secret | Observability is opt-in by env presence; tag release SHA + true deploy environment | T |

## Process & tooling

| Choice | Why | Transferable principle | Tag |
|---|---|---|---|
| GitHub Actions for CI/CD/backup/keepalive | Free for public repos; `workflow_run` chaining; artifacts | Encode ship/backup/anti-pause as reviewed workflow code, not human memory | T |
| `gh` CLI for everything GitHub | Scriptable PRs/checks/protection; agent-drivable | Agents need a CLI surface for the forge, not a browser | T |
| Spike bake-offs for contested choices (drag-drop: @dnd-kit vs raw) | A day of throwaway code beat weeks of the wrong library; testability was the deciding row | Resolve library-vs-handroll with a timeboxed bake-off scored on testability + dependency count; delete the spike | T |
| Staged build: walking skeleton first, local-first, ship-last | Every layer proven thin before features widen it | Prove the pipeline end-to-end before building features on it | T |
| ADRs per decision + docs-as-scaffolding | Cold sessions reconstruct the system from written context | Written context is the coordination medium for isolated agent sessions | T |
| nvm + `.nvmrc` (Node 22) | Shell defaulted to Node 16; Vite 8 needs ≥20.19 | Pin the runtime in-repo; assume non-interactive shells resolve it wrong (docs/LESSONS.md) | T |

## Re-decide per project (the S rows, in one place)

The frontend framework, CSS system, backend platform, and hosts are **choices, not
kit policy** — BOOTSTRAP-PROMPT.md interviews you about each. What the kit insists on
is the shape around them: pinned majors, local-first backend, one formatter, layered
gates, split E2E, server-only keys, deny-by-default data access.
