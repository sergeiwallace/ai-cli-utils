"""
ai setup — configure Claude Code session config based on detected environment.

Detects whether a managed AI platform (~/projects/CLAUDE.md) is present and
switches CLAUDE.md to the appropriate variant:
  - managed platform detected: lean CLAUDE.md is already correct, no action needed
  - no managed platform: copy CLAUDE-full.md → CLAUDE.md and mark assume-unchanged in git
"""

import shutil
import subprocess
import sys
from pathlib import Path


def _is_managed_platform() -> bool:
    return (Path.home() / "projects" / "CLAUDE.md").exists()


def _repo_root_from(cwd: Path) -> Path | None:
    res = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    return Path(res.stdout.strip()) if res.returncode == 0 else None


def run_setup(cwd: Path | None = None) -> int:
    """
    Detect environment and configure CLAUDE.md accordingly.

    Returns an exit code (0 = success, 1 = error).
    """
    working_dir = cwd or Path.cwd()
    repo_root = _repo_root_from(working_dir)
    if repo_root is None:
        print("Error: not inside a git repository", file=sys.stderr)
        return 1

    # Install-time native-dependency bootstrap. This is the cross-platform entry
    # point -- setup.sh covers the same ground but is bash-only, so it never runs
    # for a PowerShell user. Non-fatal by design: run_setup's job is CLAUDE.md,
    # and a host without direnv still gets a correct config.
    from .direnv_setup import ensure_direnv

    ensure_direnv(repo_root)

    # tmux, same contract, one extra requirement: verify it EXECUTES, never that
    # `which tmux` answers (AI-CLI-i2ih AC-1). A tmux on PATH that cannot load
    # its own shared libraries silently disables the launcher's whole
    # detach/reattach story, and an install that only checked for the file
    # reported success throughout. ensure_tmux repairs the loader path where it
    # can, attempts one unattended install otherwise, and prints the soname plus
    # a concrete remedy when neither works (AC-2).
    #
    # Wrapped because this is an optional enhancement and the install is not:
    # ensure_tmux is non-raising by contract, and an install that died inside its
    # own tmux preflight would be a strictly worse outcome than a missing tmux.
    # On Windows there is no native tmux to provision, and ensure_tmux's empty
    # candidate list is what makes that a quiet no-op rather than an error (AC-4).
    try:
        from .tmux_setup import ensure_tmux

        ensure_tmux()
    except Exception as exc:
        print(f"note: could not verify tmux ({exc}); sessions will fall back to bare mode", file=sys.stderr)

    claude_md = repo_root / "CLAUDE.md"
    claude_full_md = repo_root / "CLAUDE-full.md"

    if _is_managed_platform():
        print("managed platform detected (~/projects/CLAUDE.md found)")
        print("✓ Using lean CLAUDE.md — ~/projects/CLAUDE.md provides shared AI orchestration rules")
        return 0

    # Not on managed platform — switch to self-contained config
    if not claude_full_md.exists():
        print(f"Error: CLAUDE-full.md not found in {repo_root}", file=sys.stderr)
        print("  Re-clone the repository or restore CLAUDE-full.md from git history", file=sys.stderr)
        return 1

    if not claude_md.exists():
        print(f"Error: CLAUDE.md not found in {repo_root}", file=sys.stderr)
        return 1

    shutil.copy2(claude_full_md, claude_md)

    # Prevent git from showing CLAUDE.md as locally modified after the swap
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "CLAUDE.md"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )

    print("No managed platform detected (~/projects/CLAUDE.md not found)")
    print("✓ Switched to standalone config: CLAUDE-full.md → CLAUDE.md")
    print("  Git will ignore local changes to CLAUDE.md (assume-unchanged)")
    return 0
