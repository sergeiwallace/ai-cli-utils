# ai-cli-utils Roadmap

## Open

- [x] `[P3]` `[AI-CLI-13]` **Security hardening — /tmp predictable filenames + UUID validation** — From post-public Opus security audit. H1: validate `session_id_uuid` matches UUID regex before bash f-string interpolation in `get_engine_script`. H2/H3: move `/tmp/cc-exit-*`, `/tmp/cc-resume-prompt-*`, `/tmp/ai-watcher-lock-*`, `/tmp/iterm2-*` files from `/tmp/` to `~/.local/state/ai-cli/` (user-private, eliminates symlink attack surface). Matters for shared multi-user servers; acceptable on single-user workstations. Also: validate `--project` arg contains no path separators (S1 finding).
- [x] `[P2]` `[AI-CLI-15]` **Fix ai sync push/pull for worktree CC dirs** — `sync.py` currently skips any CC state dir containing `--worktrees-` in the name (line ~820). These dirs need to sync with cwd-path translation between Mac (`/Users/sergeiwallace/projects/foo/.worktrees/bar`) and Hetzner (`/home/sergei/projects/foo/.worktrees/bar`). Dir name translation also needed: `-Users-sergeiwallace-` ↔ `-home-sergei-`. Translate cwd fields in JSONL lines during push/pull.
- [ ] `[P3]` `[AI-CLI-11]` **Logo polish — increase visual weight** — All elements (nodes, edges, arc eye, chevron, underscore) appear pencil-thin vs other ecosystem logos (e.g. Python). Increase stroke widths and node radii so the logo holds up at small sizes and alongside other icons.
- [ ] `[P2]` `[AI-CLI-3]` **Terminal demo GIF** — `demo/demo.tape` exists. Split: cc session reviews/updates the tape script; human runs `vhs demo/demo.tape` on Mac (needs display) and embeds GIF in README.
- [ ] `[P3]` `[AI-CLI-7]` **Release Drafter** — Auto-draft GitHub Release notes from PR labels. Add when project gets regular external PRs.
- [x] `[P3]` `[AI-CLI-8]` **pyright type checking in CI** — pyright basic mode added to CI lint job. Fixed 11 type errors across gemini.py, main.py, memory.py, messaging.py, sync.py.

## Completed

- [x] `[P1]` `[AI-CLI-14]` **NATS cross-session signaling** — Dual-layer (NATS push + file queue durability). `ai internal signal-watch` subscribes durable, atomically claims tasks, writes pending-file for auto-pickup. SSH tunnel auto-opened on Mac. `ai handoff post --remote` SSHes to Hetzner. Bash template auto-launches signal-watch alongside sync/memory watch. Plan: `docs/plans/cross-session-signaling-plan.md`.
- [x] `[P2]` `[AI-CLI-12]` **Coverage push to 100%** — 587 tests, 100% line coverage. Added ai ls/attach features, test quality audit + fixes, CI isolation fixes. Plan: `docs/plans/going-public-plan.md` § AI-CLI-12.
- [x] `[P1]` `[AI-CLI-10]` **Create developer email + update SECURITY.md** — Create a dedicated developer email for security reports. Update SECURITY.md contact info. Also update pyproject.toml author email and any other references to personal email.
- [x] `[P1]` `[AI-CLI-9]` **Flip repo public** — Install Renovate App, set up Codecov token, flip to public, enable branch rulesets + CodeQL + secret scanning, social preview image, verify badges + external clone. 80% test coverage + Codecov badge added.
- [x] `[P1]` `[AI-CLI-6]` **Going public — repo automation & hardening** — CI matrix (3.11/3.12/3.13), Codecov, GH Release automation, pre-commit, Renovate (replace Dependabot), issue/PR templates, secret scan, GH Release v0.1.1. Plan: `docs/plans/going-public-plan.md`.
- [x] `[P1]` `[AI-CLI-1]` **Fix lint + test failures and ship v0.1.1** — 13 ruff lint issues + 7 test failures. v0.1.1 on PyPI.
- [x] `[P2]` `[AI-CLI-2]` **Full README rewrite** — PyOpenSci 13-section structure with badges, features table, usage reference, config example, quick start.
- [x] `[P3]` `[AI-CLI-4]` **CONTRIBUTING.md** — Dev setup, testing, code style, PR process, project structure.
- [x] `[P1]` `[AI-CLI-5]` **Professionalize for open-source release** — T-00 through T-08: generalize code (remove all hardcoded personal references), CI/publish workflows, badges, CHANGELOG, community files (SECURITY.md, dependabot), make projects_dir configurable, Trusted Publisher + project-scoped PyPI token. Plan: `docs/plans/open-source-professionalization-plan.md`. Research: R-35.
