"""ai copier-update — propagate project-template changes to downstream projects.

Two modes:

* **isolated** (default, AI-CLI-91) — for each target repo, create a temporary git
  worktree off fresh ``origin/main``, run ``copier update`` there, commit, push
  ``HEAD:main``, sync the main tree, and remove the worktree. All churn is isolated
  from the repo's main working tree, so this is safe to run while other CC sessions
  are actively working in a repo. Conflicts and semantic failures are saved under
  verified ``refs/backup/`` anchors and filed as repository issues before cleanup.
  A rejected push is anchored locally and remotely, filed as a recovery issue, and
  cleaned up. A later run protects any leftover whose state cannot be proved disposable.

* **direct** (``--no-isolate``) — legacy behaviour: run ``copier update`` straight in
  each repo's main working tree and leave the changes uncommitted for a manual
  commit+push. Fast, but races any active session in that repo. Use only when you
  know no session is working in the target(s).

For an isolated, no-push update, Python callers can pass ``inspect=True`` to
``run_copier_update()``. It returns a ``CopierUpdateInspection`` containing each
temporary commit hash and a copy of its changed files' final bytes. The temporary
worktree and branch are still removed before the function returns, so inspection
does not require a caller-managed cleanup step or leave resources behind.
"""

from __future__ import annotations

import base64
import fnmatch
import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import jinja2
import yaml

from . import output as _out
from .copier_recovery import (
    _anchor_failed_update,
    _check_recovery_target,
    _clean_anchored_pushfail,
    _file_recovery_issue,
    _push_backup_anchor,
)
from .git_repair import _creator_env, _git_env, repair_bare_worktree_config
from .output import Tag

EX_CONFIG = 78
EX_TEMPFAIL = 75
EX_PARTIAL_MUTATION = 3


@dataclass(frozen=True)
class DeliveredUpdate:
    """A committed isolated update preserved as in-memory inspection data.

    ``changed_files`` maps repository-relative paths to their final bytes. A value
    of ``None`` records a file removed by the update.
    """

    project_dir: Path
    commit_hash: str
    changed_files: dict[str, bytes | None]


@dataclass(frozen=True)
class CopierUpdateInspection:
    """Inspectable outcome returned by ``run_copier_update(inspect=True)``.

    The worktree and temporary branch are already cleaned up when this result is
    returned. ``exit_code`` has the same meaning as the legacy integer return.
    """

    exit_code: int
    delivered_updates: tuple[DeliveredUpdate, ...]


@dataclass(frozen=True)
class TemplateChanges:
    """Rendered template changes and update policies relevant to parity."""

    paths: set[str]
    hunks: set[tuple[str, tuple[str, ...], tuple[str, ...]]]
    skip_if_exists: set[str]
    deleted_paths: set[str]
    excluded_on_update: set[str] = field(default_factory=set)
    previous_paths: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class PreparedCopierSource:
    """Run-scoped source used instead of a mutable local template checkout."""

    source: str
    commit: str | None = None
    failure: str | None = None


@dataclass(frozen=True)
class RecoveredPushFailure:
    """A rejected main push preserved under a verified backup ref."""

    anchor_ref: str
    push_error: str


def write_copier_update_inspection(inspection: CopierUpdateInspection, output_path: Path) -> None:
    """Write an inspection result as JSON for a subprocess caller.

    File contents that are valid UTF-8 are emitted as strings with
    ``encoding: "utf-8"``. All other bytes use base64 with ``encoding:
    "base64"``. A null file value records a deletion.
    """

    def encode_content(content: bytes | None) -> dict[str, str] | None:
        if content is None:
            return None
        try:
            return {"encoding": "utf-8", "content": content.decode("utf-8")}
        except UnicodeDecodeError:
            return {"encoding": "base64", "content": base64.b64encode(content).decode("ascii")}

    payload = {
        "exit_code": inspection.exit_code,
        "delivered_updates": [
            {
                "project_name": update.project_dir.name,
                "project_path": str(update.project_dir),
                "commit_hash": update.commit_hash,
                "changed_files": {path: encode_content(content) for path, content in update.changed_files.items()},
            }
            for update in inspection.delivered_updates
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _find_copier_projects(projects_dir: Path) -> list[Path]:
    """Return project dirs under projects_dir that use project-template via copier."""
    result = []
    for answers_file in sorted(projects_dir.glob("*/.copier-answers.yml")):
        try:
            with answers_file.open() as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue
        if "project-template" in str(data.get("_src_path", "")):
            result.append(answers_file.parent)
    return result


_EXCLUDE_DIRS = (".venv", "node_modules", "target", ".worktrees", ".git", "__pycache__")


def _changed_paths(porcelain: str, base: Path) -> list[str]:
    """Absolute paths of files reported changed by `git status --porcelain`.

    Handles rename entries (`R  old -> new` keeps the new path) and quoted paths.
    """
    paths = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        rest = line[3:]  # strip the 2-char status field + separating space
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        rest = rest.strip().strip('"')
        if rest:
            paths.append(str(base / rest))
    return paths


_CONFLICT_MARKER = r"^<<<<<<<( |$)"


def _conflict_files(project_dir: Path, paths: list[str] | None = None) -> list[str]:
    """Return files containing git conflict markers.

    When `paths` is given, only those files are scanned — this is the correct scope
    after a copier run, because a *real* conflict marker only ever appears in a file
    copier itself modified. Scanning the whole tree (paths=None) also matches files
    that start a line with `<<<<<<<` as content (test fixtures, conflict-handling code,
    docs about merges) and reports them as false-positive conflicts. Only a line that
    *starts* with the marker counts, the shape Git and Copier both write, so a comment that
    merely mentions it mid-line is not a conflict.
    """
    if paths is not None:
        existing = [p for p in paths if Path(p).exists()]
        if not existing:
            return []
        cmd = ["grep", "-lE", "--binary-files=without-match", _CONFLICT_MARKER, *existing]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()
        return []

    cmd = [
        "grep",
        "-rlE",
        "--binary-files=without-match",
        _CONFLICT_MARKER,
        str(project_dir),
    ]
    for d in _EXCLUDE_DIRS:
        cmd += ["--exclude-dir", d]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()
    return []


def _load_answers(answers_file: Path) -> dict[str, object] | None:
    """Load Copier answers, returning None for missing or malformed files."""
    try:
        with answers_file.open() as file:
            data = yaml.safe_load(file)
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


_SSH_GITHUB_SOURCE = re.compile(r"^(?:git@github\.com:|ssh://git@github\.com/)(?P<path>[^/]+/[^/]+?)(?:\.git)?/?$")


@functools.cache
def _reachable_source(source: str) -> str:
    """A GitHub SSH template source this host cannot reach is read over HTTPS instead.

    A project stores whichever spelling the machine that generated it used. A host with SSH keys
    keeps the stored SSH URL; a host that authenticates over HTTPS (framework-fedora) cannot clone
    it, so every update of such a project failed before Copier ran. The stored answer is untouched:
    only this run's source changes, and the answers file is restored afterwards.
    """
    match = _SSH_GITHUB_SOURCE.match(source)
    if match is None:
        return source
    env = {**_git_env(), "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=10", "GIT_TERMINAL_PROMPT": "0"}
    try:
        probe = subprocess.run(
            ["git", "ls-remote", "--exit-code", source, "HEAD"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return f"https://github.com/{match['path']}.git"
    return source if probe.returncode == 0 else f"https://github.com/{match['path']}.git"


def _resolve_copier_source(project_dir: Path, answers: dict[str, object]) -> str | None:
    """Resolve a local Copier source before changing the working directory."""
    source = answers.get("_src_path")
    if not isinstance(source, str) or not source:
        return None
    if source.startswith(("git@", "http://", "https://", "ssh://", "file://")):
        return _reachable_source(source)
    source_path = Path(source)
    if not source_path.is_absolute():
        # A bare name such as ``project-template`` names a sibling checkout in the fleet layout.
        candidates = [project_dir / source_path, project_dir.parent / source_path]
        source_path = next((path for path in candidates if path.exists()), candidates[0])
    return str(source_path.resolve())


_DEFAULT_BRANCH = re.compile(r"^ref:\s+(?P<ref>refs/heads/.+)\s+HEAD$")


def _prepare_local_source(checkout: Path, temporary_root: Path, index: int) -> PreparedCopierSource:
    """Fetch and snapshot a local checkout's origin default branch without changing its worktree."""
    fetch = subprocess.run(
        ["git", "-C", str(checkout), "fetch", "--quiet", "origin"],
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    if fetch.returncode != 0:
        return PreparedCopierSource(str(checkout), failure="local template fetch origin failed")

    remote_head = subprocess.run(
        ["git", "-C", str(checkout), "ls-remote", "--symref", "origin", "HEAD"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if remote_head.returncode != 0:
        return PreparedCopierSource(str(checkout), failure="local template origin default branch could not be resolved")
    default_ref = next(
        (match["ref"] for line in remote_head.stdout.splitlines() if (match := _DEFAULT_BRANCH.match(line))),
        None,
    )
    if default_ref is None:
        return PreparedCopierSource(str(checkout), failure="local template origin has no resolvable default branch")

    remote_ref = f"refs/remotes/origin/{default_ref.removeprefix('refs/heads/')}"
    resolved = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--verify", f"{remote_ref}^{{commit}}"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    commit = resolved.stdout.strip()
    if resolved.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
        return PreparedCopierSource(
            str(checkout), failure="local template origin default branch commit was unavailable after fetch"
        )

    snapshot = temporary_root / f"source-{index}"
    clone = subprocess.run(
        ["git", "clone", "--quiet", "--shared", "--no-checkout", str(checkout), str(snapshot)],
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    if clone.returncode != 0:
        return PreparedCopierSource(str(checkout), failure="local template snapshot clone failed")
    detach = subprocess.run(
        ["git", "-C", str(snapshot), "checkout", "--quiet", "--detach", commit],
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    if detach.returncode != 0:
        return PreparedCopierSource(str(checkout), failure="local template snapshot checkout failed")
    return PreparedCopierSource(str(snapshot), commit=commit)


@contextmanager
def _prepared_copier_sources(projects: list[Path]) -> Iterator[dict[Path, PreparedCopierSource]]:
    """Prepare each distinct local checkout once and clean all snapshots after the run."""
    with tempfile.TemporaryDirectory(prefix="ai-copier-sources-") as temporary_dir:
        temporary_root = Path(temporary_dir)
        prepared_by_checkout: dict[Path, PreparedCopierSource] = {}
        prepared_by_project: dict[Path, PreparedCopierSource] = {}
        for project_dir in projects:
            answers = _load_answers(project_dir / ".copier-answers.yml")
            if answers is None:
                continue
            source = _resolve_copier_source(project_dir, answers)
            if source is None:
                continue
            raw_source = answers.get("_src_path")
            if not isinstance(raw_source, str) or raw_source.startswith(
                ("git@", "http://", "https://", "ssh://", "file://")
            ):
                prepared_by_project[project_dir] = PreparedCopierSource(source)
                continue
            checkout = Path(source)
            # Preserve the existing downstream error for a missing/non-directory path. A real
            # local checkout is the only source whose origin can be prepared here.
            if not checkout.is_dir():
                prepared_by_project[project_dir] = PreparedCopierSource(source)
                continue
            checkout = checkout.resolve()
            prepared = prepared_by_checkout.get(checkout)
            if prepared is None:
                prepared = _prepare_local_source(checkout, temporary_root, len(prepared_by_checkout))
                prepared_by_checkout[checkout] = prepared
            prepared_by_project[project_dir] = prepared
        yield prepared_by_project


def _template_commit_note(prepared: PreparedCopierSource | None) -> str:
    """Human-readable commit suffix for a project result."""
    return f" (template {prepared.commit[:12]})" if prepared is not None and prepared.commit is not None else ""


def _restore_answer_source(
    answers_file: Path, source: object, original_answers: dict[str, object], original_text: str
) -> bool:
    """Keep the stored source spelling stable after using an absolute local source."""
    answers = _load_answers(answers_file)
    if answers is None:
        return False
    answers["_src_path"] = source
    if answers == original_answers:
        answers_file.write_text(original_text)
    else:
        answers_file.write_text(yaml.safe_dump(answers, sort_keys=False, allow_unicode=True))
    return True


def _answer_values_preserved(before: dict[str, object], after: dict[str, object]) -> bool:
    """All previously stored project answers must survive an update unchanged."""
    return all(after.get(key) == value for key, value in before.items() if not key.startswith("_"))


def _is_semantic_update_failure(error: str) -> bool:
    """Whether an error means Copier changed files but the result is unsafe to commit."""
    return error == "stored Copier answers were not preserved"


def _path_candidates(path: str) -> set[str]:
    """Return Copier template and rendered destination spellings for a path."""
    candidates = {path}
    if path.endswith(".jinja"):
        candidates.add(path.removesuffix(".jinja"))
    return candidates


def _parse_diff_hunks(diff: str) -> tuple[set[str], set[tuple[str, tuple[str, ...], tuple[str, ...]]]]:
    """Extract changed paths and zero-context hunks from a Git diff."""
    paths: set[str] = set()
    hunks: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    path: str | None = None
    removed: list[str] = []
    added: list[str] = []

    def finish_hunk() -> None:
        if path is not None and (removed or added):
            hunks.add((path, tuple(removed), tuple(added)))

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            finish_hunk()
            removed = []
            added = []
            parts = line.split(" ", 3)
            path = parts[3].removeprefix("b/") if len(parts) == 4 else None
            if path:
                paths.add(path)
        elif line.startswith("@@ "):
            finish_hunk()
            removed = []
            added = []
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    finish_hunk()
    return paths, hunks


def _template_config(template_dir: Path) -> dict[str, object]:
    """Return the current Copier configuration, or an empty mapping on failure."""
    for config_name in ("copier.yml", "copier.yaml"):
        result = subprocess.run(
            ["git", "-C", str(template_dir), "show", f"HEAD:{config_name}"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        if result.returncode != 0:
            continue
        if not isinstance(result.stdout, str):
            return {}
        try:
            config = yaml.safe_load(result.stdout) or {}
        except yaml.YAMLError:
            return {}
        if not isinstance(config, dict):
            return {}
        return config
    return {}


def _template_subdirectory(config: dict[str, object]) -> str:
    """Return the current Copier render subdirectory, if one is configured."""
    subdirectory = config.get("_subdirectory")
    return subdirectory.strip("/") if isinstance(subdirectory, str) else ""


def _rendered_template_path(path: str, subdirectory: str) -> str | None:
    """Convert a source-template path to its static Copier destination path."""
    if subdirectory:
        prefix = f"{subdirectory}/"
        if not path.startswith(prefix):
            return None
        path = path.removeprefix(prefix)
    if any("{{" in segment or "{%" in segment for segment in path.split("/")):
        return None
    return path.removesuffix(".jinja")


def _question_defaults(config: dict[str, object]) -> dict[str, object]:
    """Literal defaults of the template's questions, so an answer the project never stored still renders."""
    defaults: dict[str, object] = {}
    for name, spec in config.items():
        if name.startswith("_") or not isinstance(spec, dict) or "default" not in spec:
            continue
        default = spec["default"]
        if isinstance(default, str) and ("{{" in default or "{%" in default):
            continue
        defaults[name] = default
    return defaults


def _excluded_on_update(config: dict[str, object], answers: dict[str, object] | None = None) -> set[str]:
    """Return the paths Copier excludes on an update, rendered with the project's answers.

    An exclusion such as ``{% if 'python' not in tech_stack %}pytest_memory_guard.py{% endif %}`` drops
    the file for a TypeScript project, so a template change to it can never land there. An entry that
    cannot be rendered (an answer is unknown) or is a glob is left out, so parity stays strict for it.
    """
    exclusions = config.get("_exclude", [])
    if not isinstance(exclusions, list):
        return set()
    context = {**_question_defaults(config), **(answers or {}), "_copier_operation": "update"}
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    result = set()
    for exclusion in exclusions:
        if not isinstance(exclusion, str):
            continue
        try:
            path = environment.from_string(exclusion).render(**context).strip()
        except jinja2.TemplateError:
            continue
        if path and not any(char in path for char in "*?[!"):
            result.add(path.removesuffix(".jinja"))
    return result


def _template_diff(
    source: str, previous_commit: str, answers: dict[str, object] | None = None
) -> TemplateChanges | None:
    """Return the template changes Copier should apply from previous_commit to HEAD."""
    source_path = Path(source)
    with tempfile.TemporaryDirectory(prefix="ai-copier-template-") as temporary_dir:
        if source_path.is_dir():
            template_dir = source_path
        else:
            template_dir = Path(temporary_dir) / "template"
            clone = subprocess.run(
                ["git", "clone", "--quiet", source, str(template_dir)],
                capture_output=True,
                text=True,
                env=_git_env(),
                check=False,
            )
            if clone.returncode != 0:
                return None
        result = subprocess.run(
            ["git", "-C", str(template_dir), "diff", "--no-ext-diff", "--unified=0", previous_commit, "HEAD", "--"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        config = _template_config(template_dir)
        subdirectory = _template_subdirectory(config)
        head_files = subprocess.run(
            ["git", "-C", str(template_dir), "ls-tree", "-r", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        previous_files = subprocess.run(
            ["git", "-C", str(template_dir), "ls-tree", "-r", "--name-only", previous_commit],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
    if result.returncode != 0 or head_files.returncode != 0 or previous_files.returncode != 0:
        return None
    paths, hunks = _parse_diff_hunks(result.stdout)
    rendered_paths = {
        rendered_path for path in paths if (rendered_path := _rendered_template_path(path, subdirectory)) is not None
    }
    rendered_hunks = {
        (rendered_path, removed, added)
        for path, removed, added in hunks
        if (rendered_path := _rendered_template_path(path, subdirectory)) is not None
    }
    current_paths = set(head_files.stdout.splitlines())
    deleted_paths = {
        rendered_path
        for path in paths
        if path not in current_paths and (rendered_path := _rendered_template_path(path, subdirectory)) is not None
    }
    skip_if_exists_raw = config.get("_skip_if_exists", [])
    skip_if_exists = {
        path.removesuffix(".jinja")
        for path in (skip_if_exists_raw if isinstance(skip_if_exists_raw, list) else [])
        if isinstance(path, str) and "{{" not in path and "{%" not in path
    }
    previous_paths = {
        rendered_path
        for path in previous_files.stdout.splitlines()
        if (rendered_path := _rendered_template_path(path, subdirectory)) is not None
    }
    return TemplateChanges(
        rendered_paths,
        rendered_hunks,
        skip_if_exists,
        deleted_paths,
        _excluded_on_update(config, answers),
        previous_paths,
    )


def _matches_skip_pattern(path: str, patterns: set[str]) -> bool:
    """Whether a template path is covered by a ``_skip_if_exists`` entry, the way Copier reads it.

    Entries are gitignore-style: one without a slash matches a name at any depth (``README.md``
    covers ``docs/audits/README.md``), one with a slash matches the whole path.
    """
    parts = PurePosixPath(path).parts
    for pattern in patterns:
        anchored = pattern.strip("/")
        if "/" in anchored:
            if fnmatch.fnmatchcase(path, anchored):
                return True
        elif any(fnmatch.fnmatchcase(part, anchored) for part in parts):
            return True
    return False


def _verify_update_parity(
    project_dir: Path,
    porcelain: str,
    template_paths: set[str],
    template_hunks: set[tuple[str, tuple[str, ...], tuple[str, ...]]],
    skip_if_exists: set[str] | None = None,
    deleted_template_paths: set[str] | None = None,
    excluded_on_update: set[str] | None = None,
    previous_template_paths: set[str] | None = None,
) -> str | None:
    """Return a parity error when Copier omitted a template file or static hunk."""
    changed_paths = {
        Path(path).relative_to(project_dir).as_posix()
        for path in _changed_paths(porcelain, project_dir)
        if Path(path).is_relative_to(project_dir)
    }
    skip_if_exists = skip_if_exists or set()
    deleted_template_paths = deleted_template_paths or set()
    excluded_on_update = excluded_on_update or set()
    ignored_paths = {
        path
        for path in {*skip_if_exists, *template_paths}
        if _matches_skip_pattern(path, skip_if_exists)
        and any((project_dir / candidate).exists() for candidate in _path_candidates(path))
    }
    ignored_paths.update(
        path
        for path in deleted_template_paths
        if not any((project_dir / candidate).exists() for candidate in _path_candidates(path))
    )
    ignored_paths.update(excluded_on_update)
    # A file the old template shipped and the project no longer has was removed on purpose, and
    # Copier keeps a removed file removed, so a later template change to it is not a missing file.
    ignored_paths.update(
        path
        for path in previous_template_paths or set()
        if not any((project_dir / candidate).exists() for candidate in _path_candidates(path))
    )
    expected_paths = {
        path for path in template_paths if path not in {"copier.yml", "copier.yaml"} and not path.startswith(".copier-")
    }
    expected_paths -= ignored_paths
    missing_paths = sorted(path for path in expected_paths if not (_path_candidates(path) & changed_paths))
    if missing_paths:
        return f"template parity failed: missing changed file(s): {', '.join(missing_paths)}"

    actual = subprocess.run(
        ["git", "-C", str(project_dir), "diff", "--no-ext-diff", "--unified=0", "--"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if actual.returncode != 0:
        return "template parity failed: could not inspect applied changes"
    _, actual_hunks = _parse_diff_hunks(actual.stdout)
    untracked_paths = {line[3:].strip().strip('"') for line in porcelain.splitlines() if line.startswith("?? ")}

    def matches_untracked_file(hunk: tuple[str, tuple[str, ...], tuple[str, ...]]) -> bool:
        for path in _path_candidates(hunk[0]) & untracked_paths:
            target = project_dir / path
            if target.is_file():
                contents = target.read_text()
                if all(line in contents.splitlines() for line in hunk[2]):
                    return True
        return False

    expected_static_hunks = {
        hunk
        for hunk in template_hunks
        if hunk[0] not in ignored_paths
        and "{{" not in "\n".join((*hunk[1], *hunk[2]))
        and "{%" not in "\n".join((*hunk[1], *hunk[2]))
    }
    missing_hunks = [
        hunk
        for hunk in expected_static_hunks
        if not any(
            _path_candidates(hunk[0]) & _path_candidates(actual_hunk[0]) and hunk[1:] == actual_hunk[1:]
            for actual_hunk in actual_hunks
        )
        and not matches_untracked_file(hunk)
    ]
    if missing_hunks:
        return "template parity failed: one or more static template hunks were not applied"
    return None


def _hide_answers_change(project_dir: Path, answers_file: Path, hide: bool = True) -> bool:
    """Set or clear skip-worktree on the answers file; return whether the flag change took."""
    flag = "--skip-worktree" if hide else "--no-skip-worktree"
    result = subprocess.run(
        ["git", "-C", str(project_dir), "update-index", flag, "--", str(answers_file)],
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    return result.returncode == 0


def _run_copier_update(
    project_dir: Path, copier_bin: str, resolved_source: str | None = None
) -> tuple[str | None, dict[str, object] | None, TemplateChanges | None]:
    """Run Copier with stored answers and return any safety failure."""
    answers_file = project_dir / ".copier-answers.yml"
    answers_before = _load_answers(answers_file)
    if answers_before is None:
        return "could not read .copier-answers.yml", None, None
    original_answers = answers_before.copy()
    original_text = answers_file.read_text()
    source = resolved_source or _resolve_copier_source(project_dir, answers_before)
    previous_commit = answers_before.get("_commit")
    if source is None or not isinstance(previous_commit, str) or not previous_commit:
        return "could not resolve Copier source and previous template commit", None, None
    template_changes = _template_diff(source, previous_commit, answers_before)
    if template_changes is None:
        return "could not inspect template diff for parity verification", None, None

    original_source = original_answers["_src_path"]
    update_answers = original_answers.copy()
    update_answers["_src_path"] = source
    source_rewritten = update_answers != original_answers
    if source_rewritten:
        answers_file.write_text(yaml.safe_dump(update_answers, sort_keys=False, allow_unicode=True))
    else:
        answers_file.write_text(original_text)
    failure: str | None = None
    # Copier refuses to update a dirty destination, and the source rewrite above is a change in
    # the checkout, so hide it from ``git status`` for the length of the run.
    hidden = source_rewritten and _hide_answers_change(project_dir, answers_file)
    try:
        result = subprocess.run(
            [
                copier_bin,
                "update",
                "--defaults",
                "--data-file",
                str(answers_file),
                "--trust",
                "--vcs-ref",
                "HEAD",
            ],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            failure = result.stderr.strip() or "copier update failed"
        else:
            answers_after = _load_answers(answers_file)
            if answers_after is None or not _answer_values_preserved(original_answers, answers_after):
                failure = "stored Copier answers were not preserved"
    finally:
        if hidden:
            _hide_answers_change(project_dir, answers_file, hide=False)
        if not _restore_answer_source(answers_file, original_source, original_answers, original_text):
            failure = "could not restore stored Copier source"
    if failure is not None:
        return failure, None, None
    return None, original_answers, template_changes


# ---------------------------------------------------------------------------
# Isolated worktree flow (AI-CLI-91)
# ---------------------------------------------------------------------------

# Temp worktree lives under the repo's own (globally-gitignored) .worktrees/ dir so
# it can never collide with a session worktree (`session-N`) or leak into git status.
_WT_NAME = "copier-update"
_WT_BRANCH = "copier-update-tmp"
_COMMIT_MSG = "chore: copier update from project-template"


def _repo_root(path: Path) -> Path | None:
    """Return the git top-level dir for path, or None if path is not in a git repo."""
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip())


def _cleanup_worktree(root: Path, wt_dir: Path, branch: str) -> None:
    """Remove the temp worktree and its branch (best-effort, idempotent).

    Targets only this worktree: a repo-wide ``git worktree prune`` here would drop
    every other registration whose directory is missing, with its index and HEAD.
    """
    # Repair backstop (AI-CLI-99) before AND after the worktree teardown — root
    # is the repo's normal main working tree and must never end up bare / with
    # a stale core.worktree, regardless of what a leaked GIT_* env would do.
    repair_bare_worktree_config(root)
    subprocess.run(
        ["git", "-C", str(root), "worktree", "remove", "--force", str(wt_dir)],
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    subprocess.run(["git", "-C", str(root), "branch", "-D", branch], capture_output=True, env=_git_env(), check=False)
    repair_bare_worktree_config(root)


_GIT_READ_TIMEOUT = 30
_IN_PROGRESS_MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG")


def _git_read(args: list[str]) -> str | None:
    """Run a read-only git command; None when it errors or times out."""
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
            timeout=_GIT_READ_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _leftover_hold_reason(root: Path, wt_dir: Path, branch: str) -> str | None:
    """Why a prior run's temp worktree/branch must be kept, or None when it is disposable.

    A conflict or pushfail deliberately leaves the worktree for a human, so the
    pre-clean removes only what it can prove holds nothing: no worktree and no
    branch, or a clean checkout and branch at the base commit. Any state it cannot
    read is kept.
    """
    listing = _git_read(["-C", str(root), "worktree", "list", "--porcelain"])
    if listing is None:
        return "could not read the worktree list"
    target = wt_dir.resolve()
    registered = any(
        Path(line[len("worktree ") :].strip()).resolve() == target
        for line in listing.splitlines()
        if line.startswith("worktree ")
    )
    exists = os.path.lexists(wt_dir)
    branch_tip = _git_read(["-C", str(root), "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}"])
    if branch_tip is None:
        return f"could not read branch {branch}"
    has_branch = bool(branch_tip.strip())

    if not registered and not exists and not has_branch:
        return None
    if exists and not registered:
        return "directory exists but is not a registered worktree, so its state cannot be read"
    if registered and not exists:
        return "registered but its directory is missing, so its state cannot be read"

    base_name = (
        "origin/main" if _git_read(["-C", str(root), "rev-parse", "--verify", "--quiet", "origin/main"]) else "HEAD"
    )
    base = _git_read(["-C", str(root), "rev-parse", "--verify", base_name])
    if base is None:
        return f"could not read base {base_name}"
    base = base.strip()

    def commits_ahead(rev_args: list[str], tip: str) -> int | None:
        out = _git_read([*rev_args, "rev-list", "--count", f"{base}..{tip}"])
        return int(out.strip()) if out is not None and out.strip().isdigit() else None

    if registered:
        git_dir = _git_read(["-C", str(wt_dir), "rev-parse", "--absolute-git-dir"])
        if git_dir is None:
            return "could not read its git directory"
        in_progress = [m for m in _IN_PROGRESS_MARKERS if os.path.lexists(Path(git_dir.strip()) / m)]
        if in_progress:
            return f"operation in progress ({', '.join(in_progress)})"
        status = _git_read(["-C", str(wt_dir), "status", "--porcelain"])
        if status is None:
            return "could not read its status"
        if status.strip():
            return "uncommitted or staged changes"
        ahead = commits_ahead(["-C", str(wt_dir)], "HEAD")
        if ahead is None:
            return "could not count its commits"
        if ahead:
            return f"{ahead} commit(s) not on {base_name}"

    if has_branch:
        ahead = commits_ahead(["-C", str(root)], f"refs/heads/{branch}")
        if ahead is None:
            return f"could not count commits on {branch}"
        if ahead:
            return f"branch {branch} has {ahead} commit(s) not on {base_name}"
    return None


def _snapshot_staged_files(worktree_dir: Path) -> dict[str, bytes | None] | None:
    """Copy final bytes for every path staged for an isolated update commit."""
    staged = subprocess.run(
        ["git", "-C", str(worktree_dir), "diff", "--cached", "--name-only", "-z"],
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    if staged.returncode != 0:
        return None

    changed_files: dict[str, bytes | None] = {}
    for raw_path in staged.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative_path = raw_path.decode(errors="surrogateescape")
        path = worktree_dir / relative_path
        if path.is_file():
            changed_files[relative_path] = path.read_bytes()
        elif path.is_symlink():
            changed_files[relative_path] = str(path.readlink()).encode()
        else:
            changed_files[relative_path] = None
    return changed_files


def _hooks_rewrote_files(wt_dir: Path) -> bool:
    """Whether tracked files differ from the index, which is what a failed formatter hook leaves."""
    return (
        subprocess.run(
            ["git", "-C", str(wt_dir), "diff", "--quiet"], capture_output=True, env=_git_env(), check=False
        ).returncode
        == 1
    )


def _do_update_in_worktree(
    wt_dir: Path,
    root: Path,
    copier_bin: str,
    push: bool,
    resolved_source: str | None = None,
    inspect: bool = False,
) -> tuple[str, str | list[str] | DeliveredUpdate]:
    """Run copier + commit/push inside an already-created worktree.

    Returns (status, detail) where status is one of:
      ok        — updated, committed, (pushed) — detail unused unless inspect=True,
                  when it is a DeliveredUpdate
      nochange  — copier produced no changes — detail unused
      conflict  — merge conflicts — detail = list[str] of relative paths
      parityfail — a template file or hunk did not land — detail = str (message)
      pushfail  — committed but push rejected — detail = str (git stderr)
      failed    — copier or commit failed — detail = str (message)
    """
    update_error, _, template_changes = _run_copier_update(wt_dir, copier_bin, resolved_source)
    if update_error is not None:
        if _is_semantic_update_failure(update_error):
            return "parityfail", update_error
        # A copier invocation failure is retried as transient; the isolated target is discarded.
        return "failed", update_error

    # --untracked-files=all: a wholly new directory otherwise collapses to one `?? dir/` entry and the
    # parity check reports every file Copier added inside it as missing.
    status = subprocess.run(
        ["git", "-C", str(wt_dir), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if not status.stdout.strip():
        return "nochange", ""

    # Only files copier actually touched can hold a real conflict marker — scoping the
    # scan to them avoids false positives from files that merely reference `<<<<<<<`.
    conflicts = _conflict_files(wt_dir, _changed_paths(status.stdout, wt_dir))
    if conflicts:
        rels = [str(Path(c).relative_to(wt_dir)) for c in conflicts]
        return "conflict", rels

    assert template_changes is not None
    parity_error = _verify_update_parity(
        wt_dir,
        status.stdout,
        template_changes.paths,
        template_changes.hunks,
        template_changes.skip_if_exists,
        template_changes.deleted_paths,
        template_changes.excluded_on_update,
        template_changes.previous_paths,
    )
    if parity_error is not None:
        return "parityfail", parity_error

    # A pre-commit hook that rewrites files (a formatter) aborts the commit but leaves the fixes in the
    # tree. That is not a failed update: stage the fixes and commit once more, still through the hooks.
    for attempt in range(2):
        subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True, env=_git_env(), check=False)
        changed_files = _snapshot_staged_files(wt_dir) if inspect else None
        if inspect and changed_files is None:
            return "failed", "could not capture delivered content"
        commit = subprocess.run(
            ["git", "-C", str(wt_dir), "commit", "-m", _COMMIT_MSG],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        if commit.returncode == 0 or attempt == 1 or not _hooks_rewrote_files(wt_dir):
            break
    if commit.returncode != 0:
        # A commit failure is retried as transient; the isolated target is discarded.
        return "failed", (commit.stderr.strip() or commit.stdout.strip() or "commit failed")

    if inspect:
        commit_hash = subprocess.run(
            ["git", "-C", str(wt_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        if commit_hash.returncode != 0 or not commit_hash.stdout.strip():
            return "failed", "could not determine temporary commit hash"
        assert changed_files is not None  # guaranteed by check at line 650
        delivered_update = DeliveredUpdate(wt_dir, commit_hash.stdout.strip(), changed_files)

    if push:
        pr = subprocess.run(
            ["git", "-C", str(wt_dir), "push", "origin", "HEAD:main"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        if pr.returncode != 0:
            return "pushfail", (pr.stderr.strip() or "push failed")
        # Keep the repo's main working tree in sync with what we just shipped.
        subprocess.run(["git", "-C", str(root), "pull", "--rebase"], capture_output=True, env=_git_env(), check=False)
        repair_bare_worktree_config(root)

    return "ok", delivered_update if inspect else ""


def _update_one_isolated(
    project_dir: Path,
    copier_bin: str,
    push: bool = True,
    inspect: bool = False,
    resolved_source: str | None = None,
) -> tuple[str, str | list[str] | DeliveredUpdate | RecoveredPushFailure]:
    """Update one repo in an isolated temp worktree. Returns (status, detail).

    On ok/nochange/failed the temp worktree is removed. Conflicts and semantic
    failures and rejected pushes are anchored and removed. Recovery failures keep
    the checkout and return ``recoveryfail``. A
    leftover from a prior run that is not provably disposable returns ``kept`` with
    its path and reason, and nothing is removed. When inspect=True, an ok result
    contains copied content before that cleanup.
    """
    root = _repo_root(project_dir)
    if root is None:
        # This is a deterministic prerequisite failure: the project is not a Git repository.
        return "failed", "not a git repository"

    project_answers = _load_answers(project_dir / ".copier-answers.yml")
    if project_answers is None:
        return "failed", "could not read .copier-answers.yml"
    resolved_source = resolved_source or _resolve_copier_source(project_dir, project_answers)
    if resolved_source is None:
        return "failed", "could not resolve Copier source before creating worktree"

    wt_dir = root / ".worktrees" / _WT_NAME
    branch = _WT_BRANCH

    # Base the worktree on fresh origin/main so we propagate onto the shipped tip,
    # not whatever the local main tree happens to be at. Fall back to HEAD offline.
    # Fetched before the leftover check so a commit a human has since pushed by hand
    # counts as on the base.
    subprocess.run(
        ["git", "-C", str(root), "fetch", "origin", "main"], capture_output=True, env=_git_env(), check=False
    )

    # Clear a leftover temp worktree/branch from a prior interrupted run, but keep
    # one a conflicted or rejected run left for a human to resolve.
    hold = _leftover_hold_reason(root, wt_dir, branch)
    if hold is not None:
        return "kept", f"{wt_dir}: {hold}"
    _cleanup_worktree(root, wt_dir, branch)

    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "origin/main"],
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    base = "origin/main" if probe.returncode == 0 else "HEAD"

    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    repair_bare_worktree_config(root)
    # This temp worktree is flat under `<repo>/.worktrees/`, which ai-harness's
    # post-checkout backstop undoes for anything but a trusted creator, so the add
    # identifies itself. No canonical leaf: `copier-update` is not a session slot, and the
    # backstop matches a declared leaf only for the `ai-c` creator.
    add = subprocess.run(
        ["git", "-C", str(root), "worktree", "add", str(wt_dir), "-b", branch, base],
        capture_output=True,
        text=True,
        env=_creator_env("ai-copier-update"),
        check=False,
    )
    repair_bare_worktree_config(root)
    if add.returncode != 0:
        _cleanup_worktree(root, wt_dir, branch)
        # The required isolated worktree cannot be created, so retrying without repair will not help.
        return "failed", f"worktree add failed: {add.stderr.strip()}"

    if inspect:
        status, detail = _do_update_in_worktree(wt_dir, root, copier_bin, push, resolved_source, inspect=True)
    else:
        status, detail = _do_update_in_worktree(wt_dir, root, copier_bin, push, resolved_source)

    if inspect and status == "ok":
        assert isinstance(detail, DeliveredUpdate)
        detail = DeliveredUpdate(project_dir, detail.commit_hash, detail.changed_files)

    if status in ("conflict", "parityfail", "pushfail"):
        dirty = _git_read(["-C", str(wt_dir), "status", "--porcelain"])
        if dirty is None:
            return "recoveryfail", f"{wt_dir}: backup anchor: could not inspect failed update; nothing removed"
        # A rejected push may retain only an actual committed update ahead of the
        # original base. Resolve HEAD in root, not in the linked checkout.
        keep_commit = False
        if status == "pushfail":
            base_tip = _git_read(["-C", str(root), "rev-parse", base])
            ahead = (
                _git_read(["-C", str(wt_dir), "rev-list", "--count", f"{base_tip.strip()}..HEAD"]) if base_tip else None
            )
            if ahead is None or not ahead.strip().isdigit():
                return "recoveryfail", f"{wt_dir}: could not verify unpushed commit; nothing removed"
            keep_commit = int(ahead.strip()) > 0
        try:
            ref, template_commit = _anchor_failed_update(wt_dir, root.name, resolved_source)
            _out.emit(Tag.COPIER, "update", f"    backup anchor: {ref}")
            filing_error = None
            if status in ("conflict", "parityfail"):
                conflicts = detail if isinstance(detail, list) else []
                try:
                    _file_recovery_issue(root, ref, conflicts, f"{project_answers.get('_commit')}..{template_commit}")
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    filing_error = str(exc)
            elif status == "pushfail":
                assert isinstance(detail, str)
                _push_backup_anchor(root, ref)
                try:
                    _file_recovery_issue(
                        root,
                        ref,
                        [],
                        f"{project_answers.get('_commit')}..{template_commit}",
                        detail,
                    )
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    filing_error = str(exc)
            if keep_commit:
                _clean_anchored_pushfail(root, wt_dir, branch, ref)
            else:
                _check_recovery_target(root, wt_dir, branch, ref)
            _cleanup_worktree(root, wt_dir, branch)
            remaining = _git_read(["-C", str(root), "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}"])
            listing = _git_read(["-C", str(root), "worktree", "list", "--porcelain"])
            if (
                os.path.lexists(wt_dir)
                or remaining is None
                or remaining.strip()
                or listing is None
                or any(
                    Path(line[len("worktree ") :]).resolve() == wt_dir.resolve()
                    for line in listing.splitlines()
                    if line.startswith("worktree ")
                )
            ):
                raise RuntimeError(f"backup anchor {ref}: worktree or branch cleanup failed")
            if filing_error:
                return "recoveryfail", f"backup anchor {ref}: issue filing failed: {filing_error}"
            if status == "pushfail" and not keep_commit:
                return "recoveryfail", f"backup anchor {ref}: push failed without an unpushed commit; checkout removed"
            if status == "pushfail":
                assert isinstance(detail, str)
                detail = RecoveredPushFailure(ref, detail)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return "recoveryfail", f"{wt_dir}: recovery failed: {exc}"

    # Clean updates and transient failures retain their existing cleanup policy.
    if status in ("ok", "nochange", "failed"):
        _cleanup_worktree(root, wt_dir, branch)

    return status, detail


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_copier_update(
    projects_dir: Path | None = None,
    project_filter: str | None = None,
    dry_run: bool = False,
    isolate: bool = True,
    push: bool = True,
    inspect: bool = False,
) -> int | CopierUpdateInspection:
    """Run copier update across all matching projects.

    isolate=True (default) runs each repo in a throwaway worktree and ships to main;
    isolate=False runs copier directly in each repo's main tree (legacy, unsafe while
    sessions are active).

    ``inspect=True`` requires ``isolate=True`` and ``push=False``. It returns a
    ``CopierUpdateInspection`` instead of an integer, with committed file content
    copied before the isolated worktree and branch are cleaned up.
    """
    if inspect and (not isolate or push):
        raise ValueError("inspect=True requires isolate=True and push=False")

    if projects_dir is None:
        # The configured root, not a literal ~/projects: a machine whose projects live
        # elsewhere reported "projects directory not found" and updated nothing.
        from .config import _get_projects_dir

        projects_dir = _get_projects_dir()

    if not projects_dir.exists():
        _out.error(Tag.COPIER, "update", f"projects directory not found: {projects_dir}")
        return EX_CONFIG

    copier_bin = shutil.which("copier")
    if copier_bin is None:
        _out.error(Tag.COPIER, "update", "copier not found in PATH. Install with: uv tool install copier")
        return EX_CONFIG

    projects = _find_copier_projects(projects_dir)
    if not projects:
        _out.emit(Tag.COPIER, "update", "No project-template-based projects found.")
        return 0

    if project_filter:
        projects = [p for p in projects if p.name == project_filter]
        if not projects:
            _out.error(Tag.COPIER, "update", f"project '{project_filter}' not found or not copier-managed.")
            return EX_CONFIG

    if dry_run:
        mode = "isolated worktree → main" if isolate else "direct (main tree)"
        _out.emit(Tag.COPIER, "update", f"Would update {len(projects)} project(s) [{mode}]:")
        for p in projects:
            _out.emit(Tag.COPIER, "update", f"  {p.name}")
        _out.emit(Tag.COPIER, "update", "\n(dry-run: no changes made)")
        return 0

    if isolate:
        if inspect:
            return _run_isolated(projects, copier_bin, push, inspect=True)
        return _run_isolated(projects, copier_bin, push)
    return _run_direct(projects, copier_bin)


def _run_isolated(
    projects: list[Path], copier_bin: str, push: bool, inspect: bool = False
) -> int | CopierUpdateInspection:
    """Isolated-worktree flow (AI-CLI-91). Returns exit code."""
    with _prepared_copier_sources(projects) as prepared_sources:
        return _run_isolated_prepared(projects, copier_bin, push, inspect, prepared_sources)


def _run_isolated_prepared(
    projects: list[Path],
    copier_bin: str,
    push: bool,
    inspect: bool,
    prepared_sources: dict[Path, PreparedCopierSource],
) -> int | CopierUpdateInspection:
    """Run isolated updates while their run-scoped template snapshots exist."""
    _out.emit(Tag.COPIER, "update", f"Updating {len(projects)} project(s) [isolated worktree → main]:\n")
    failed = 0
    changed = 0
    has_partial_mutation = False
    has_config_failure = False
    has_transient_failure = False
    delivered_updates: list[DeliveredUpdate] = []
    for project_dir in projects:
        _out.emit(Tag.COPIER, "update", f"  {project_dir.name}... ", nl=False)
        prepared = prepared_sources.get(project_dir)
        note = _template_commit_note(prepared)
        if prepared is not None and prepared.failure is not None:
            status, detail = "failed", prepared.failure
        elif inspect:
            if prepared is None:
                status, detail = _update_one_isolated(project_dir, copier_bin, push=push, inspect=True)
            else:
                status, detail = _update_one_isolated(
                    project_dir, copier_bin, push=push, inspect=True, resolved_source=prepared.source
                )
        else:
            if prepared is None:
                status, detail = _update_one_isolated(project_dir, copier_bin, push=push)
            else:
                status, detail = _update_one_isolated(
                    project_dir, copier_bin, push=push, resolved_source=prepared.source
                )
        if status == "ok":
            result = "✓ updated + pushed" if push else "✓ updated (committed, not pushed)"
            _out.cont(Tag.COPIER, "update", result + note)
            changed += 1
            if inspect:
                assert isinstance(detail, DeliveredUpdate)
                delivered_updates.append(detail)
        elif status == "nochange":
            _out.cont(Tag.COPIER, "update", "· no changes" + note)
        elif status == "conflict":
            assert isinstance(detail, list)  # conflict status returns list[str] of paths
            _out.cont(
                Tag.COPIER,
                "update",
                f"✗ CONFLICTS ({len(detail)} file(s)) — saved in backup anchor; see recovery issue{note}:",
            )
            for rel in detail:
                _out.emit(Tag.COPIER, "update", f"    conflict: {rel}")
            failed += 1
            has_partial_mutation = True
        elif status == "parityfail":
            _out.cont(
                Tag.COPIER,
                "update",
                "✗ TEMPLATE PARITY FAILED — saved in backup anchor; see recovery issue" + note,
            )
            _out.emit(Tag.COPIER, "update", f"    {detail}")
            failed += 1
            has_partial_mutation = True
        elif status == "pushfail":
            assert isinstance(detail, RecoveredPushFailure)
            _out.cont(
                Tag.COPIER,
                "update",
                f"✗ PUSH FAILED — saved in backup anchor {detail.anchor_ref}; see recovery issue" + note,
            )
            for line in detail.push_error.splitlines():
                _out.emit(Tag.COPIER, "update", f"    {line}")
            failed += 1
            has_partial_mutation = True
        elif status == "kept":
            _out.cont(
                Tag.COPIER,
                "update",
                "✗ LEFTOVER KEPT — a prior run's temp worktree holds state; resolve or remove it, then re-run:" + note,
            )
            _out.emit(Tag.COPIER, "update", f"    {detail}")
            failed += 1
            has_partial_mutation = True
        elif status == "recoveryfail":
            _out.cont(
                Tag.COPIER, "update", "✗ RECOVERY FAILED — inspect the reported anchor or retained worktree" + note
            )
            _out.emit(Tag.COPIER, "update", f"    {detail}")
            failed += 1
            has_partial_mutation = True
        else:  # failed
            _out.cont(Tag.COPIER, "update", "✗ FAILED" + note)
            _out.emit(Tag.COPIER, "update", f"    {detail}")
            failed += 1
            if detail == "not a git repository" or (
                isinstance(detail, str) and detail.startswith("worktree add failed:")
            ):
                has_config_failure = True
            else:
                has_transient_failure = True

    if failed:
        _out.emit(Tag.COPIER, "update", f"{failed} project(s) had errors or conflicts — resolve before continuing.")
    else:
        _out.emit(Tag.COPIER, "update", f"All projects up to date ({changed} updated).")
    if has_partial_mutation:
        exit_code = EX_PARTIAL_MUTATION
    elif has_config_failure:
        exit_code = EX_CONFIG
    elif has_transient_failure:
        exit_code = EX_TEMPFAIL
    else:
        exit_code = 0
    if inspect:
        return CopierUpdateInspection(exit_code, tuple(delivered_updates))
    return exit_code


def _run_direct(projects: list[Path], copier_bin: str) -> int:
    """Legacy direct-to-main-tree flow (--no-isolate). Returns exit code."""
    with _prepared_copier_sources(projects) as prepared_sources:
        return _run_direct_prepared(projects, copier_bin, prepared_sources)


def _run_direct_prepared(
    projects: list[Path], copier_bin: str, prepared_sources: dict[Path, PreparedCopierSource]
) -> int:
    """Run direct updates while their run-scoped template snapshots exist."""
    _out.emit(Tag.COPIER, "update", f"Updating {len(projects)} project(s) [direct — main tree]:\n")
    failed = 0
    has_partial_mutation = False
    has_transient_failure = False
    for project_dir in projects:
        _out.emit(Tag.COPIER, "update", f"  {project_dir.name}... ", nl=False)
        prepared = prepared_sources.get(project_dir)
        note = _template_commit_note(prepared)
        if prepared is not None and prepared.failure is not None:
            update_error, template_changes = prepared.failure, None
        else:
            update_error, _, template_changes = _run_copier_update(
                project_dir, copier_bin, prepared.source if prepared is not None else None
            )
        if update_error is not None:
            semantic_failure = _is_semantic_update_failure(update_error)
            if semantic_failure:
                _out.cont(Tag.COPIER, "update", "✗ TEMPLATE PARITY FAILED" + note)
                has_partial_mutation = True
            else:
                _out.cont(Tag.COPIER, "update", "✗ FAILED" + note)
            if update_error:
                for line in update_error.splitlines():
                    _out.emit(Tag.COPIER, "update", f"    {line}", err=True)
            failed += 1
            if not semantic_failure:
                has_transient_failure = True
            continue

        porcelain = subprocess.run(
            ["git", "-C", str(project_dir), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        conflicts = _conflict_files(project_dir, _changed_paths(porcelain.stdout, project_dir))
        if conflicts:
            # In --no-isolate mode, unresolved markers mutate the live main tree, not a temp worktree.
            _out.cont(Tag.COPIER, "update", f"✗ CONFLICTS ({len(conflicts)} file(s))" + note)
            for c in conflicts:
                _out.emit(Tag.COPIER, "update", f"    conflict: {c}")
            failed += 1
            has_partial_mutation = True
        else:
            assert template_changes is not None
            parity_error = _verify_update_parity(
                project_dir,
                porcelain.stdout,
                template_changes.paths,
                template_changes.hunks,
                template_changes.skip_if_exists,
                template_changes.deleted_paths,
                template_changes.excluded_on_update,
                template_changes.previous_paths,
            )
            if parity_error is not None:
                _out.cont(Tag.COPIER, "update", "✗ TEMPLATE PARITY FAILED" + note)
                _out.emit(Tag.COPIER, "update", f"    {parity_error}")
                failed += 1
                has_partial_mutation = True
            else:
                _out.cont(Tag.COPIER, "update", "✓" + note)

    if failed:
        _out.emit(Tag.COPIER, "update", f"{failed} project(s) had errors or conflicts — resolve before continuing.")
    else:
        _out.emit(Tag.COPIER, "update", "All projects updated successfully.")
    if has_partial_mutation:
        return EX_PARTIAL_MUTATION
    if has_transient_failure:
        return EX_TEMPFAIL
    return 0
