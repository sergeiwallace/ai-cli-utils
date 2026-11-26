# Decision records for template-managed files

These records explain choices in files that project-template renders into this
repository. They replace the code comments those files used to carry. Edit them in
project-template's `template/docs/adr/template/`.

| Record | Governs |
|---|---|
| [0001](0001-pytest-memory-guard-watchdog.md) | `pytest_memory_guard.py`, `tests/test_pytest_memory_guard.py` |
| [0002](0002-template-files-stay-byte-identical.md) | `scripts/*.py`, `.githooks/`, `.pre-commit-config.yaml` |
| [0003](0003-hooks-install-through-pre-commit.md) | `.githooks/install.sh`, `setup.sh`, `.pre-commit-config.yaml` |
| [0004](0004-pre-commit-hook-set.md) | `.pre-commit-config.yaml` |
| [0005](0005-markdownlint-relaxations.md) | `.markdownlint.yaml`, `docs/research/.markdownlint.yaml` |
| [0006](0006-gitignore-literal-entries.md) | `.gitignore` |
| [0007](0007-envrc-secrets-loader-guard.md) | `.envrc` |
| [0008](0008-no-branch-name-enforcement.md) | `.claude/hooks/enforce-workflow.sh` |
| [0009](0009-github-status-field-alias.md) | `.github/project.yml` |
| [0010](0010-audit-risk-tiers-are-calibrated.md) | `config/audit_risk_tiers.yaml` |
| [0011](0011-setup-script-portability.md) | `setup.sh` |
