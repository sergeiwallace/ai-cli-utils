#!/usr/bin/env python3
"""Pre-commit guard: hook commands in .claude/settings.json must reference
scripts that actually exist.

Why this exists
---------------
`.claude/settings.json` is copier `_skip_if_exists` (rendered once, then owned by
the project), while the hook scripts it points at (under `.claude/hooks/`) are
copier-managed and can be *removed* by an `_exclude` rule on `copier update`
(e.g. a template change that drops a hook script). That pairing
can strand a hook command pointing at a deleted script — which then fails at
runtime with "No such file or directory" on every matching hook event.

Because settings.json is skip_if_exists, copier cannot re-render it to drop the
stale reference, so nothing catches the mismatch automatically. This guard does:
it runs at commit time and fails if any hook command references a repo-local
script that is missing.

Scope
-----
Only *repo-local* script references are validated — those anchored at the repo
root via `$CLAUDE_PROJECT_DIR/…` / `${CLAUDE_PROJECT_DIR}/…` or written as a plain
relative path. References to user/global scripts (`~/…`, `$HOME/…`, absolute
`/…`, or other shell vars) are ignored, since a repo cannot govern their
presence. Non-script commands (`echo …`) are ignored.

Usage
-----
    validate-claude-settings.py [REPO_ROOT]

REPO_ROOT defaults to `git rev-parse --show-toplevel`, falling back to CWD.
Exit 0 (silent) when clean; exit 1 listing offenders otherwise.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPT_EXTS = (".sh", ".py", ".js", ".ts", ".bash", ".zsh")
PROJECT_DIR_VARS = ("$CLAUDE_PROJECT_DIR/", "${CLAUDE_PROJECT_DIR}/")
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")


def repo_root() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()


def iter_commands(settings: dict):
    """Yield (event, command) for every command-type hook in a settings dict."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []) or []:
                if isinstance(hook, dict) and hook.get("type") == "command":
                    cmd = hook.get("command", "")
                    if isinstance(cmd, str) and cmd:
                        yield event, cmd


def local_script_refs(command: str):
    """Yield repo-root-relative script paths a hook command depends on."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for tok in tokens:
        raw = tok
        anchored = False
        for var in PROJECT_DIR_VARS:
            if raw.startswith(var):
                raw = raw[len(var) :]
                anchored = True
                break
        # Ignore refs the repo can't govern: home-relative, absolute, or other
        # shell vars — unless they were $CLAUDE_PROJECT_DIR-anchored above.
        if not anchored and raw.startswith(("~", "/", "$")):
            continue
        if raw.startswith(("~", "/", "$")):
            continue
        if raw.lower().endswith(SCRIPT_EXTS):
            yield raw


def main() -> int:
    root = repo_root()
    problems: list[str] = []
    for fname in SETTINGS_FILES:
        path = root / fname
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{fname}: invalid JSON ({exc})")
            continue
        for event, cmd in iter_commands(data):
            for ref in local_script_refs(cmd):
                if not (root / ref).is_file():
                    problems.append(
                        f"{fname}: {event} hook references missing script '{ref}'\n      command: {cmd}"
                    )
    if problems:
        sys.stderr.write(
            "✖ .claude/settings.json hook validation failed — hook command(s) point at scripts that do not exist:\n\n"
        )
        for problem in problems:
            sys.stderr.write(f"  • {problem}\n")
        sys.stderr.write(
            "\nFix: remove the stale hook entry, or restore the script. Common "
            "cause: a copier _exclude removed the script but the _skip_if_exists "
            "settings.json still references it.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
