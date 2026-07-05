# PLACEHOLDERS

Every `{{…}}` token in the kit, so nothing ships half-filled. CI enforces this table
(`scripts/check_placeholders.py`: tokens used == tokens documented). At bootstrap,
Phase 4 replaces them all, deletes this file, and re-runs the script with
`--bootstrapped` (asserts zero remain).

| Token | File | Fill with |
|---|---|---|
| `{{EDGE_FUNCTION_NAMES}}` | `templates/workflows/deploy-on-green.yml` | Space-separated function names to deploy (e.g. `api-status send-report`) |
| `{{SMOKE_FUNCTION_NAME}}` | `templates/workflows/deploy-on-green.yml` | One deployed function for the post-deploy 401 smoke (an auth-required, no-key endpoint) |
| `{{KEEPALIVE_TABLE}}` | `templates/workflows/keepalive.yml` | Any real table name — a denied anon read still resets the pause timer |
| `{{PROJECT_ONE_PARAGRAPH}}` | `docs/CLAUDE-template.md` | What the app is + the invariant that must always hold |
| `{{REFERENCE_MATERIAL_LIST_OR_DELETE}}` | `docs/CLAUDE-template.md` | Per-file list of gitignored reference material, or delete the block |
| `{{FRONTEND_STACK}}` | `docs/CLAUDE-template.md` | e.g. `Vite + React 18 + TypeScript (strict) + Tailwind 3` |
| `{{SERVER_STATE_LIB}}` | `docs/CLAUDE-template.md` | e.g. `TanStack Query — owns cache/loading/sync` |
| `{{UI_STATE_APPROACH}}` | `docs/CLAUDE-template.md` | e.g. `useState/useContext first; no Redux` |
| `{{BACKEND_PLATFORM}}` | `docs/CLAUDE-template.md` | e.g. `Supabase — Postgres, Auth, RLS, Edge Functions` |
| `{{HOSTING}}` | `docs/CLAUDE-template.md` | e.g. `Vercel (frontend) + managed backend` |
| `{{AI_PROVIDER}}` | `docs/CLAUDE-template.md` | Provider + "server-side only" note, or delete the row |
| `{{TEST_STACK}}` | `docs/CLAUDE-template.md` | e.g. `Vitest + RTL (unit/component), Playwright (E2E)` |
| `{{DEV_URL}}` | `docs/CLAUDE-template.md` | Local dev URL (e.g. `http://localhost:5173`) |
| `{{LOCAL_BACKEND_START}}` | `docs/CLAUDE-template.md` | Command that starts the local backing stack, or delete |
| `{{GOLDEN_SUITE_CMD}}` | `docs/CLAUDE-template.md` | The DB-backed local-only E2E command, or delete |
| `{{MIGRATION_COMMANDS}}` | `docs/CLAUDE-template.md` | Migration new/apply commands, or delete |
| `{{DIRECTORY_TREE_WITH_ONE_LINE_PER_ENTRY}}` | `docs/CLAUDE-template.md` | Annotated `src/` (+ backend) layout — one folder per feature/system |
| `{{NAMING_CONVENTIONS}}` | `docs/CLAUDE-template.md` | File/component/variable casing rules |
| `{{EXAMPLE_KEY_NAME}}` | `docs/CLAUDE-template.md` | A real env-var name to model "reference by name only" |
| `{{ADMIN_KEY_NAME}}` | `docs/CLAUDE-template.md` | The server-only admin credential's name (e.g. service-role key) |
| `{{PROJECT_INVARIANT_RULE_OR_DELETE}}` | `docs/CLAUDE-template.md` | A numbered Hard Rule for the product invariant, or delete |
| `{{DATA_LAYER_LINE}}` | `docs/CLAUDE-template.md` | One line on the data-access guard (e.g. RLS on every table), or delete |

## Beyond `{{…}}` tokens (also filled at bootstrap)

- `.env.example` — replace the example vars with your project's public-env contract.
- `planning/` — the reference-dir name, if you chose a different one, must change in
  `.gitignore` + `.husky/pre-commit` + the hook's `git add` guard + the app CI's
  forbidden-paths grep + CLAUDE.md Rule 1 (every enforcement layer must agree).
- The hook's fenced STACK-SPECIFIC section + matching battery cases in `test_hooks.py`.
- npm script names in `templates/workflows/ci.yml` (`lint` / `format:check` /
  `typecheck` / `test` / `test:e2e`) if your toolchain differs.
