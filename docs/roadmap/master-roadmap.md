# ai-cli-utils Roadmap

## Open

- [x] `[P1]` `[AI-CLI-6]` **Going public — repo automation & hardening** — CI matrix (3.11/3.12/3.13), Codecov, GH Release automation, pre-commit, Renovate (replace Dependabot), issue/PR templates, secret scan, GH Release v0.1.1. Branch protection deferred (requires public repo or Pro). Plan: `docs/plans/going-public-plan.md`. Research: R-2.
- [ ] `[P1]` `[AI-CLI-9]` **Flip repo public** — Manual steps: (1) Install Renovate GitHub App on repo, (2) Set up Codecov — add `CODECOV_TOKEN` repo secret, (3) Flip repo to public in Settings, (4) Enable branch protection rulesets (required checks: `lint`, `test (3.12)`; linear history; no force push), (5) Enable CodeQL default setup in Security settings, (6) Enable secret scanning + push protection, (7) Generate social preview image (socialify.git.ci or custom 1280x640px), (8) Verify badges render, test external clone+install.
- [ ] `[P3]` `[AI-CLI-3]` **Terminal demo GIF** — Record terminal session demo with vhs (`demo/demo.tape` ready). Install vhs, run `vhs demo/demo.tape`, embed in README.
- [ ] `[P3]` `[AI-CLI-7]` **Release Drafter** — Auto-draft GitHub Release notes from PR labels. Add when project gets regular external PRs.
- [ ] `[P3]` `[AI-CLI-8]` **pyright type checking in CI** — Add pyright basic mode to CI. Requires type annotation audit first.

## Completed

- [x] `[P1]` `[AI-CLI-1]` **Fix lint + test failures and ship v0.1.1** — 13 ruff lint issues + 7 test failures. v0.1.1 on PyPI.

- [x] `[P2]` `[AI-CLI-2]` **Full README rewrite** — PyOpenSci 13-section structure with badges, features table, usage reference, config example, quick start.
- [x] `[P3]` `[AI-CLI-4]` **CONTRIBUTING.md** — Dev setup, testing, code style, PR process, project structure.
- [x] `[P1]` `[AI-CLI-5]` **Professionalize for open-source release** — T-00 through T-08: generalize code (remove all hardcoded personal references), CI/publish workflows, badges, CHANGELOG, community files (SECURITY.md, dependabot), make projects_dir configurable, Trusted Publisher + project-scoped PyPI token. Plan: `docs/plans/open-source-professionalization-plan.md`. Research: R-35.
