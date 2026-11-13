"""Moving a fleet onto the Dolt registry: the one-shot import and the verdict seed.

:func:`import_tree` copies an old git-tree registry (its pool, manual names, held rows,
renames and policy snapshot) into the Dolt database in one commit, after checking every
held row with the row validator. Running it again finds nothing to import.

:func:`seed_verdicts` applies the owner's approvals from a seed manifest kept in the
fleet repository, but only after the manifest equals an owner-approved oracle part for
part. The oracle is read at a pinned commit of the fleet repository, and its canonical
digest must equal the one a separate cutover record stores, so neither the manifest
nor anything inside it can vouch for itself.

Seed manifest and oracle (TOML), each part kept on its own::

    preapproved = ["ada", ...]                    # general approvals
    addition = ["eve"]                            # the separate addition: general approval
    original_table = [{session = "myproject-1", name = "bea"}, ...]
    corrected_table = [{session = "myproject-1", name = "cal"}, ...]   # session approvals
    superseded = "bea"                            # the substituted table entry
    replacement = "cal"
    exclusions = ["dee"]
    answers = [{name = "dee", verdict = "approved", scope_kind = "general", scope_value = ""}]
    verdicts = [{name = "fay", scope_kind = "host", scope_value = "host-a", verdict = "rejected"}]

The manifest adds ``revision``, ``actor`` and ``reason``. The cutover record
(``config/session-name-cutover.toml``) holds ``oracle_commit`` and ``oracle_sha256``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import name_registry as reg
from . import name_schema as schema

ORACLE_PATH = "config/session-name-approvals.oracle.toml"
CUTOVER_RECORD = Path("config") / "session-name-cutover.toml"
PARTS = (
    "preapproved",
    "addition",
    "original_table",
    "corrected_table",
    "superseded",
    "replacement",
    "exclusions",
    "answers",
    "verdicts",
)


class SeedValidationError(reg.WriteRefusedError):
    """The seed does not match the owner-approved oracle, or cannot be applied as recorded."""


# --- import --------------------------------------------------------------------------------


@dataclass
class ImportReport:
    names: int = 0
    manual: int = 0
    held: dict[str, int] = field(default_factory=dict)
    migrations: dict[str, int] = field(default_factory=dict)
    written: bool = False

    def lines(self) -> list[str]:
        held = ", ".join(f"{kind} {count}" for kind, count in sorted(self.held.items())) or "none"
        renames = ", ".join(f"{state} {count}" for state, count in sorted(self.migrations.items())) or "none"
        return [
            f"names: {self.names}",
            f"manual names: {self.manual}",
            f"held rows: {held}",
            f"migrations: {renames}",
        ]


def tree_counts(tree: reg.Snapshot) -> ImportReport:
    report = ImportReport(names=len(tree.names or []), manual=len(tree.manual))
    for row in tree.rows.values():
        report.held[row["kind"]] = report.held.get(row["kind"], 0) + 1
    for record in tree.migrations.values():
        state = str(record.get("status"))
        report.migrations[state] = report.migrations.get(state, 0) + 1
    return report


def _import_writes(tree: reg.Snapshot, current: reg.Snapshot) -> dict[str, bytes | None]:
    """Every record of ``tree`` the database lacks or holds differently; nothing it has is removed."""
    writes: dict[str, bytes | None] = {}
    if set(tree.names or []) - set(current.names or []) - set(current.manual):
        listed = sorted({*(tree.names or []), *(current.names or [])})
        writes[reg.NAMES_TXT] = "".join(f"{name}\n" for name in listed).encode()
    if tree.generation is not None and tree.generation != current.generation:
        writes[reg.GENERATION_JSON] = reg.encode_json(tree.generation)
    for name, record in sorted(tree.manual.items()):
        if current.manual.get(name) != record:
            writes[reg.manual_path(name)] = reg.encode_json(record)
    for token, row in sorted(tree.rows.items()):
        if current.rows.get(token) != row:
            writes[reg.row_path(token)] = reg.encode_json(row)
    for title, record in sorted(tree.migrations.items()):
        if current.migrations.get(title) != record:
            writes[reg.migration_path(title)] = reg.encode_json(record)
    return writes


def import_tree(target: reg.Registry, tree: reg.Snapshot, *, dry_run: bool = False) -> ImportReport:
    """Copy ``tree`` into ``target`` in one commit; refuses the whole import on any invalid held row."""
    for token, row in sorted(tree.rows.items()):
        problem = reg.row_problem(row)
        if problem:
            raise reg.WriteRefusedError(
                f"the held row for {token!r} fails the row validator ({problem}); nothing was imported"
            )
    report = tree_counts(tree)
    if dry_run:
        report.written = bool(_import_writes(tree, target.snapshot()))
        return report

    def build(snapshot: reg.Snapshot) -> reg.Change | None:
        writes = _import_writes(tree, snapshot)
        if not writes:
            return None
        return reg.Change(writes, f"session-names: import {tree.tip or 'a registry tree'}")

    report.written = target.transact(build, exempt=True) is not None
    return report


# --- the seed ------------------------------------------------------------------------------


def _read_toml(text: str, source: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SeedValidationError(f"cannot read {source}: {exc}") from exc


def normalized_parts(document: dict) -> dict[str, object]:
    """Each oracle part as sorted normalized rows, so equal content compares and hashes equal."""

    def names(value: object) -> list[str]:
        if isinstance(value, str):
            value = [value]
        return sorted({str(item).strip().lower() for item in (value or [])})

    def table(value: object) -> list[dict]:
        rows = [
            {"session": str(entry.get("session") or ""), "name": str(entry["name"]).lower()} for entry in value or []
        ]
        return sorted(rows, key=lambda row: (row["name"], row["session"]))

    def answers(value: object) -> list[dict]:
        rows = [
            {
                "name": str(entry["name"]).lower(),
                "verdict": str(entry["verdict"]),
                "scope_kind": str(entry.get("scope_kind") or "general"),
                "scope_value": str(entry.get("scope_value") or ""),
            }
            for entry in value or []
        ]
        return sorted(rows, key=lambda row: (row["name"], row["scope_kind"], row["scope_value"]))

    def verdicts(value: object) -> list[dict]:
        rows = [
            {
                "name": str(entry["name"]).lower(),
                "scope_kind": str(entry["scope_kind"]),
                "scope_value": str(entry["scope_value"]),
                "verdict": str(entry["verdict"]),
            }
            for entry in value or []
        ]
        return sorted(rows, key=lambda row: (row["name"], row["scope_kind"], row["scope_value"]))

    try:
        return {
            "preapproved": names(document.get("preapproved")),
            "addition": names(document.get("addition")),
            "original_table": table(document.get("original_table")),
            "corrected_table": table(document.get("corrected_table")),
            "superseded": str(document.get("superseded") or "").lower(),
            "replacement": str(document.get("replacement") or "").lower(),
            "exclusions": names(document.get("exclusions")),
            "answers": answers(document.get("answers")),
            "verdicts": verdicts(document.get("verdicts")),
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise SeedValidationError(f"a seed part is malformed: {exc!r}") from exc


def canonical_digest(parts: dict[str, object]) -> str:
    return hashlib.sha256(schema.canonical(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Oracle:
    parts: dict[str, object]
    commit: str
    digest: str


def load_oracle(repo: Path) -> Oracle:
    """The owner-approved oracle at the commit the cutover record pins, checked against its stored digest."""
    record_file = repo / CUTOVER_RECORD
    if not record_file.is_file():
        raise SeedValidationError(f"the cutover record {CUTOVER_RECORD.as_posix()} is missing; nothing was seeded")
    record = _read_toml(record_file.read_text(encoding="utf-8"), CUTOVER_RECORD.as_posix())
    commit, expected = record.get("oracle_commit"), record.get("oracle_sha256")
    if not commit or not expected:
        raise SeedValidationError(
            f"{CUTOVER_RECORD.as_posix()} must store oracle_commit and oracle_sha256; nothing was seeded"
        )
    shown = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{ORACLE_PATH}"], capture_output=True, text=True, check=False
    )
    if shown.returncode != 0:
        raise SeedValidationError(
            f"cannot read {ORACLE_PATH} at the pinned commit {commit}: {shown.stderr.strip()}; nothing was seeded"
        )
    parts = normalized_parts(_read_toml(shown.stdout, f"{ORACLE_PATH} at {commit}"))
    digest = canonical_digest(parts)
    if digest != expected:
        raise SeedValidationError(
            f"the oracle at {commit} has digest {digest}, not the {expected} the cutover record stores; nothing was seeded"
        )
    return Oracle(parts, str(commit), digest)


def _session_of(table: list[dict], name: str) -> str | None:
    return next((row["session"] for row in table if row["name"] == name), None)


def validate_seed(candidate: dict[str, object], oracle: Oracle, denylist: frozenset[str]) -> None:
    """Refuse unless every part equals the oracle's, the substitution applies as recorded and no conflict is open."""
    differing = [part for part in PARTS if candidate[part] != oracle.parts[part]]
    if differing:
        raise SeedValidationError(
            f"the seed differs from the owner-approved oracle in {', '.join(differing)}; nothing was seeded"
        )
    original, corrected = candidate["original_table"], candidate["corrected_table"]
    superseded, replacement = candidate["superseded"], candidate["replacement"]
    general = {*candidate["preapproved"], *candidate["addition"]}  # type: ignore[misc]
    problems = []
    if any(not row["session"] for row in corrected):  # type: ignore[union-attr]
        problems.append("a corrected-table entry has no target session")
    if superseded and _session_of(original, superseded) is None:  # type: ignore[arg-type]
        problems.append(f"{superseded} is not in the original conversion table")
    if superseded and _session_of(corrected, superseded) is not None:  # type: ignore[arg-type]
        problems.append(f"{superseded} is still in the corrected table")
    if superseded and superseded in general:
        problems.append(f"{superseded} is in the pre-approved list or the separate addition")
    if superseded and _session_of(corrected, replacement) is None:  # type: ignore[arg-type]
        problems.append(f"{replacement} is not in the corrected table")
    elif superseded and _session_of(corrected, replacement) != _session_of(original, superseded):  # type: ignore[arg-type]
        problems.append(f"{replacement} is proposed for another session than {superseded} was")
    if problems:
        raise SeedValidationError(
            "the substitution cannot be applied as recorded: " + "; ".join(problems) + "; nothing was seeded"
        )
    approved = general | {row["name"] for row in corrected}  # type: ignore[union-attr]
    answered = {row["name"] for row in candidate["answers"]}  # type: ignore[union-attr]
    conflicts = sorted((approved & ({*candidate["exclusions"]} | denylist)) - answered)  # type: ignore[misc]
    if conflicts:
        raise SeedValidationError(
            f"{', '.join(conflicts)} is approved but also excluded or denylisted, and the oracle records no owner "
            "answer for it; nothing was seeded"
        )


@dataclass
class SeedReport:
    general: list[str] = field(default_factory=list)
    session: list[tuple[str, str]] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    holder_approvals: list[tuple[str, str]] = field(default_factory=list)
    written: bool = False


def _status(status: str, actor: str, recorded_by: str, stamp: str, reason: str | None) -> bytes:
    entry = {"status": status, "actor": actor, "recorded_by": recorded_by, "set_at": stamp, "reason": reason}
    return schema.canonical(entry).encode()


def _verdict(verdict: str, actor: str, recorded_by: str, stamp: str, reason: str | None) -> bytes:
    entry = {"verdict": verdict, "actor": actor, "recorded_by": recorded_by, "set_at": stamp, "reason": reason}
    return schema.canonical(entry).encode()


def seed_verdicts(
    registry: reg.Registry,
    manifest_text: str,
    *,
    oracle: Oracle,
    denylist: frozenset[str],
    recorded_by: str,
) -> SeedReport:
    """Validate the manifest against ``oracle``, then apply every approval at its scope in one commit."""
    manifest = _read_toml(manifest_text, "the seed file")
    actor, revision = str(manifest.get("actor") or ""), str(manifest.get("revision") or "")
    if not actor or not revision:
        raise SeedValidationError("the seed file must name its actor and revision; nothing was seeded")
    reason = manifest.get("reason")
    candidate = normalized_parts(manifest)
    validate_seed(candidate, oracle, denylist)
    content = canonical_digest({**candidate, "actor": actor, "reason": reason})
    report = SeedReport()

    def build(snapshot: reg.Snapshot) -> reg.Change | None:
        if snapshot.statuses is None:
            raise SeedValidationError("seeding needs the database registry; nothing was seeded")
        applied = snapshot.meta.get("seed_revision")
        if applied == revision:
            if snapshot.meta.get("seed_sha256") == content:
                return None
            raise SeedValidationError(
                f"seed revision {revision} was already applied with different content; nothing was seeded"
            )
        stamp = reg.utc_now()
        general = sorted({*candidate["preapproved"], *candidate["addition"]})  # type: ignore[misc]
        corrected = candidate["corrected_table"]
        answers = {row["name"]: row for row in candidate["answers"]}  # type: ignore[union-attr]
        rejections = sorted({*candidate["exclusions"], *denylist})  # type: ignore[misc]
        named_only = {row["name"] for row in candidate["verdicts"]} | set(rejections)  # type: ignore[union-attr]
        outside = sorted(name for name in named_only if name not in snapshot.available)
        if outside:
            raise SeedValidationError(
                f"the seed rejects or scopes {', '.join(outside)}, which the pool does not hold; nothing was seeded"
            )
        approved_entries = set(general) | {row["name"] for row in corrected}  # type: ignore[union-attr]
        writes: dict[str, bytes | None] = {}
        for name in sorted(approved_entries - set(snapshot.available)):
            record = {"schema": 1, "name": name, "added_by": actor, "added_at": stamp, "via": "seed", "reason": reason}
            writes[reg.manual_path(name)] = reg.encode_json(record)
            report.added.append(name)
        for name in general:
            writes[schema.status_path(name)] = _status("approved", actor, recorded_by, stamp, reason)
            report.general.append(name)
        for row in corrected:  # type: ignore[union-attr]
            writes[schema.verdict_path(row["name"], "session", row["session"])] = _verdict(
                "approved", actor, recorded_by, stamp, reason
            )
            report.session.append((row["name"], row["session"]))
        for name in rejections:
            answer = answers.get(name)
            if answer is None or answer["verdict"] != "approved":
                writes[schema.status_path(name)] = _status("rejected", actor, recorded_by, stamp, reason)
                report.rejected.append(name)
            holder = snapshot.rows.get(name)
            if name in denylist and holder is not None and holder.get("title"):
                writes[schema.verdict_path(name, "session", holder["title"])] = _verdict(
                    "approved", actor, recorded_by, stamp, "held when it was denylisted"
                )
                report.holder_approvals.append((name, holder["title"]))
        for name, answer in sorted(answers.items()):
            if answer["scope_kind"] == "general":
                writes[schema.status_path(name)] = _status(answer["verdict"], actor, recorded_by, stamp, reason)
            else:
                writes[schema.verdict_path(name, answer["scope_kind"], answer["scope_value"])] = _verdict(
                    answer["verdict"], actor, recorded_by, stamp, reason
                )
        for row in candidate["verdicts"]:  # type: ignore[union-attr]
            writes[schema.verdict_path(row["name"], row["scope_kind"], row["scope_value"])] = _verdict(
                row["verdict"], actor, recorded_by, stamp, reason
            )
        writes[schema.meta_path("seed_revision")] = revision.encode()
        writes[schema.meta_path("seed_sha256")] = content.encode()
        writes[schema.meta_path("seed_oracle")] = f"{oracle.commit} {oracle.digest}".encode()
        return reg.Change(writes, f"session-names: seed verdicts {revision} ({actor})")

    report.written = registry.transact(build, exempt=True) is not None
    return report


def oracle_digest_of(text: str) -> str:
    """The canonical digest the cutover record stores for an oracle file's ``text``."""
    return canonical_digest(normalized_parts(_read_toml(text, "the oracle")))


def describe(report: SeedReport) -> list[str]:
    lines = [
        f"general approvals: {len(report.general)}",
        f"session approvals: {len(report.session)}",
        f"rejected: {len(report.rejected)}",
    ]
    if report.added:
        lines.append(f"added to the pool: {', '.join(report.added)}")
    if report.holder_approvals:
        lines.append("kept for their holders: " + ", ".join(f"{n} ({t})" for n, t in report.holder_approvals))
    return lines


def to_json(report: SeedReport) -> str:
    return json.dumps(
        {
            "general": report.general,
            "session": [list(item) for item in report.session],
            "rejected": report.rejected,
            "added": report.added,
            "holder_approvals": [list(item) for item in report.holder_approvals],
            "written": report.written,
        },
        sort_keys=True,
    )
