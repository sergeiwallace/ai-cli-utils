# ai-cli-utils Roadmap

## Open

- [ ] `[P3]` `[AI-CLI-11]` **Logo polish — increase visual weight** — All elements (nodes, edges, arc eye, chevron, underscore) appear pencil-thin vs other ecosystem logos (e.g. Python). Increase stroke widths and node radii so the logo holds up at small sizes and alongside other icons.
- [ ] `[P3]` `[AI-CLI-3]` **Terminal demo GIF** — Record terminal session demo with vhs (`demo/demo.tape` ready). Install vhs, run `vhs demo/demo.tape`, embed in README.
- [ ] `[P3]` `[AI-CLI-7]` **Release Drafter** — Auto-draft GitHub Release notes from PR labels. Add when project gets regular external PRs.
- [ ] `[P3]` `[AI-CLI-8]` **pyright type checking in CI** — Add pyright basic mode to CI. Requires type annotation audit first.

## Completed

- [x] `[P1]` `[AI-CLI-10]` **Create developer email + update SECURITY.md** — Create a dedicated developer email for security reports. Update SECURITY.md contact info. Also update pyproject.toml author email and any other references to personal email.
- [x] `[P1]` `[AI-CLI-9]` **Flip repo public** — Install Renovate App, set up Codecov token, flip to public, enable branch rulesets + CodeQL + secret scanning, social preview image, verify badges + external clone. 80% test coverage + Codecov badge added.
- [x] `[P1]` `[AI-CLI-6]` **Going public — repo automation & hardening** — CI matrix (3.11/3.12/3.13), Codecov, GH Release automation, pre-commit, Renovate (replace Dependabot), issue/PR templates, secret scan, GH Release v0.1.1. Plan: `docs/plans/going-public-plan.md`.
- [x] `[P1]` `[AI-CLI-1]` **Fix lint + test failures and ship v0.1.1** — 13 ruff lint issues + 7 test failures. v0.1.1 on PyPI.
- [x] `[P2]` `[AI-CLI-2]` **Full README rewrite** — PyOpenSci 13-section structure with badges, features table, usage reference, config example, quick start.
- [x] `[P3]` `[AI-CLI-4]` **CONTRIBUTING.md** — Dev setup, testing, code style, PR process, project structure.
- [x] `[P1]` `[AI-CLI-5]` **Professionalize for open-source release** — T-00 through T-08: generalize code (remove all hardcoded personal references), CI/publish workflows, badges, CHANGELOG, community files (SECURITY.md, dependabot), make projects_dir configurable, Trusted Publisher + project-scoped PyPI token. Plan: `docs/plans/open-source-professionalization-plan.md`. Research: R-35.
