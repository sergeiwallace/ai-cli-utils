"""ai copier-update — run copier update across all project-template-based projects."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def _find_copier_projects(projects_dir: Path) -> list[Path]:
    """Return project dirs under projects_dir that use project-template via copier."""
    result = []
    for answers_file in sorted(projects_dir.glob("*/.copier-answers.yml")):
        try:
            with open(answers_file) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue
        if "project-template" in str(data.get("_src_path", "")):
            result.append(answers_file.parent)
    return result


def _conflict_files(project_dir: Path) -> list[str]:
    """Return list of files containing git conflict markers in project_dir."""
    result = subprocess.run(
        ["grep", "-rl", "<<<<<<<", str(project_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()
    return []


def run_copier_update(
    projects_dir: Path | None = None,
    project_filter: str | None = None,
    dry_run: bool = False,
) -> int:
    """Run copier update across all matching projects. Returns exit code (0 = success)."""
    if projects_dir is None:
        projects_dir = Path.home() / "projects"

    if not projects_dir.exists():
        print(f"Error: projects directory not found: {projects_dir}", file=sys.stderr)
        return 1

    copier_bin = shutil.which("copier")
    if copier_bin is None:
        print(
            "Error: copier not found in PATH. Install with: uv tool install copier",
            file=sys.stderr,
        )
        return 1

    projects = _find_copier_projects(projects_dir)
    if not projects:
        print("No project-template-based projects found.")
        return 0

    if project_filter:
        projects = [p for p in projects if p.name == project_filter]
        if not projects:
            print(
                f"Error: project '{project_filter}' not found or not copier-managed.",
                file=sys.stderr,
            )
            return 1

    if dry_run:
        print(f"Would update {len(projects)} project(s):")
        for p in projects:
            print(f"  {p.name}")
        print("\n(dry-run: no changes made)")
        return 0

    print(f"Updating {len(projects)} project(s):\n")
    failed = 0
    for project_dir in projects:
        print(f"  {project_dir.name}... ", end="", flush=True)
        result = subprocess.run(
            [copier_bin, "update", "--defaults", "--trust"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("✗ FAILED")
            if result.stderr.strip():
                for line in result.stderr.strip().splitlines():
                    print(f"    {line}", file=sys.stderr)
            failed += 1
            continue

        conflicts = _conflict_files(project_dir)
        if conflicts:
            print(f"✗ CONFLICTS ({len(conflicts)} file(s))")
            for c in conflicts:
                print(f"    conflict: {c}")
            failed += 1
        else:
            print("✓")

    print()
    if failed:
        print(f"{failed} project(s) had errors or conflicts — resolve before continuing.")
    else:
        print("All projects updated successfully.")
    return 1 if failed else 0
