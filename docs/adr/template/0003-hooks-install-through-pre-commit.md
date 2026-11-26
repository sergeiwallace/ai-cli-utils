# 0003. Git hooks install through pre-commit, and a failed install is loud

Governs: `.githooks/install.sh`, `setup.sh`, `.pre-commit-config.yaml`.

## Decision

`.githooks/install.sh` installs the pre-commit, commit-msg and pre-push hooks through the
pre-commit framework, and exits non-zero when it cannot.

## Why, and the constraints the code relies on

- A bootstrap that cannot install hooks must not report success. `install.sh` fails when
  `pre-commit` is missing, matching the loud-failure contract in ai-harness's
  `docs/designs/aih-393-shared-hook-distribution-model.md`. `setup.sh` handles that
  failure itself: it warns and carries on with the rest of the scaffold, because
  `install.sh` has already printed the remedy. So `install.sh`'s exit code stays truthful
  and a fresh scaffold stays usable.
- `pre-commit install` refuses to run while `core.hooksPath` is set, so `install.sh` first
  unsets a `core.hooksPath=.githooks` left by older versions.
- `--overwrite` keeps a re-run from moving an installed chain wrapper to
  `pre-push.legacy`.
- The install banner names `SKIP=hook-id`, pre-commit's own single-hook skip, and
  deliberately does not name the test gate's `SKIP_TESTS=1`. Fleet rules forbid that bypass
  outside repository bootstrap, and the banner prints on every install. Rejected: naming
  it, which teaches the escape hatch to everyone.
- Hook pins are updated with `pre-commit autoupdate`.
