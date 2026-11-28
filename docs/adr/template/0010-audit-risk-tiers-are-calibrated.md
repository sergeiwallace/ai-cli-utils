# 0010. Audit risk tier values are calibrated policy

Governs: `config/audit_risk_tiers.yaml`.

## Decision

The budgets and limits in this file are policy calibrated from fleet receipt telemetry.
Change them from new telemetry, not by hand-tuning one repository.
