# ai-cli-utils Roadmap

## Open

- [x] `[P1]` `[AI-CLI-1]` **Fix lint + test failures and ship v0.1.1** — 13 ruff lint issues + 7 test failures from professionalization refactor. Fix all, bump to v0.1.1, tag, push to trigger auto-publish to PyPI via Trusted Publishers. Delegated to c-r-ai-cli-1 session.
- [ ] `[P2]` `[AI-CLI-2]` **Full README rewrite** — PyOpenSci 13-section README structure. Includes: project description, installation, quick start, configuration, usage examples, architecture overview, contributing guide link, license. Based on R-35 research.
- [ ] `[P3]` `[AI-CLI-3]` **Terminal demo GIF** — Record terminal session demo with asciinema or vhs. Show: session creation, auto-resume, mosh transport, sync push/pull. Embed in README.
- [ ] `[P3]` `[AI-CLI-4]` **CONTRIBUTING.md** — Contribution guide: dev setup, testing, code style, PR process. Link from README.

## Completed

- [x] `[P1]` `[AI-CLI-5]` **Professionalize for open-source release** — T-00 through T-08: generalize code (remove all hardcoded personal references), CI/publish workflows, badges, CHANGELOG, community files (SECURITY.md, dependabot), make projects_dir configurable, Trusted Publisher + project-scoped PyPI token. Plan: `docs/plans/open-source-professionalization-plan.md`. Research: R-35.
