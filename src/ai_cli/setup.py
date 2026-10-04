"""
ai setup — configure Claude Code session config based on detected environment.

Detects whether a managed AI platform (a CLAUDE.md at the configured projects root, by
default ~/projects) is present and switches CLAUDE.md to the appropriate variant:
  - managed platform detected: lean CLAUDE.md is already correct, no action needed
  - no managed platform: copy CLAUDE-full.md → CLAUDE.md and mark assume-unchanged in git
"""

import shutil
import subprocess
import sys
from pathlib import Path


def _managed_platform_config() -> Path:
    """Return where the shared, projects-wide CLAUDE.md would be on this machine.

    Resolved from ``[project] projects_dir`` rather than a literal ``~/projects``. On at least
    one managed host ``~/projects`` exists but is near-empty and the real root is elsewhere,
    which made this detection answer "no managed platform" there. That answer is destructive
    rather than merely wrong: the caller then copies CLAUDE-full.md over CLAUDE.md and marks it
    assume-unchanged, so the swap does not show up in git status afterwards.
    """
    from .config import _get_projects_dir

    return _get_projects_dir() / "CLAUDE.md"


def _is_managed_platform() -> bool:
    return _managed_platform_config().exists()


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

    # zsh, the interpreter a session prefers to run under. Provisioned here rather
    # than at launch because a package-manager solve is minutes, and a launch must
    # never wait on one (AI-CLI-s2q2). `ai setup` is an explicit command with a
    # human in front of it, so this is also the one place escalation is offered --
    # gated on stdin being a terminal, so a scripted or piped install can never
    # block on an unanswerable password prompt.
    try:
        from .native_deps import can_prompt_for_root
        from .zsh_setup import ensure_zsh

        ensure_zsh(allow_root=can_prompt_for_root())
    except Exception as exc:
        print(f"note: could not verify zsh ({exc}); sessions will run under bash", file=sys.stderr)

    claude_md = repo_root / "CLAUDE.md"
    claude_full_md = repo_root / "CLAUDE-full.md"

    shared_config = _managed_platform_config()

    if _is_managed_platform():
        print(f"managed platform detected ({shared_config} found)")
        print(f"✓ Using lean CLAUDE.md — {shared_config} provides shared AI orchestration rules")
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

    print(f"No managed platform detected ({shared_config} not found)")
    print("✓ Switched to standalone config: CLAUDE-full.md → CLAUDE.md")
    print("  Git will ignore local changes to CLAUDE.md (assume-unchanged)")
    return 0
