"""Explicit, read-only process inspection to enrol legacy session recipes."""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import click
import psutil

from . import config, output
from . import session_registry as registry
from . import session_run_state as state
from .output import Untagged


def inspect_pane(name: str) -> tuple[str, list[str]]:
    """Require one live pane, returning its current directory and leader argv."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", f"={name}", "-F", "#{session_name}\t#{pane_pid}\t#{pane_current_path}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        rows = result.stdout.splitlines()
        if len(rows) != 1:
            raise ValueError
        session, pid, cwd = rows[0].split("\t")
        if session != name or not Path(cwd).is_absolute() or int(pid) <= 1:
            raise ValueError
        return cwd, psutil.Process(int(pid)).cmdline()
    except (OSError, ValueError, subprocess.SubprocessError, psutil.Error) as exc:
        raise ValueError("live pane check failed: expected one readable pane and leader") from exc


def _script_recipe(name: str, engine: str, cwd: str, argv: list[str], script: Path) -> dict:
    # Match the complete invocation, never an occurrence inside shell command text.
    if argv != [str(script)] and not (
        len(argv) == 2 and Path(argv[0]).name in ("bash", "zsh", "sh") and argv[1] == str(script)
    ):
        raise ValueError("pane leader argv check failed: not the exact generated session script")
    try:
        source = script.read_text(encoding="utf-8")
        lines = [line.strip() for line in source.splitlines()]
        metadata = [line for line in lines if line.startswith("printf '%s' ") and "session-meta-" in line]
        if len(metadata) != 1:
            raise ValueError
        tokens = shlex.split(metadata[0])
        if tokens[:2] != ["printf", "%s"] or tokens[3:] != [">", "$_ai_state_dir/session-meta-$tmux_session.json"]:
            raise ValueError
        meta = json.loads(tokens[2])
        if not isinstance(meta, dict) or engine != "c" or not name.startswith("c-r-"):
            raise ValueError
        if meta.get("session") != name or meta.get("engine") != engine or meta.get("is_remote") is not True:
            raise ValueError
        if meta.get("ai_name") != name.removeprefix("c-r-"):
            raise ValueError
        for key, value in (("tmux_session", name), ("engine", engine), ("ai_name", meta.get("ai_name"))):
            if not isinstance(value, str) or f"{key}={shlex.quote(value)}" not in lines:
                raise ValueError
        launches = [shlex.split(line) for line in lines if line.startswith("run_agent claude ")]
        expected = ["run_agent", "claude", "$claude_perms_flag"]
        if not launches or any(
            launch
            not in [
                expected + resume + ["--name", "$ai_name"] + prompt
                for resume in ([], ["--resume", "$session_id"])
                for prompt in ([], ["$resume_msg"])
            ]
            for launch in launches
        ):
            raise ValueError
        if 'claude_perms_flag="--dangerously-skip-permissions"' not in lines:
            raise ValueError
        if not isinstance(meta.get("sandbox"), bool) or not isinstance(meta.get("project_name"), str):
            raise ValueError
        prefix = meta.get("project_prefix")
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", prefix):
            raise ValueError
    except (OSError, UnicodeError, ValueError, IndexError, TypeError) as exc:
        raise ValueError("generated script check failed: missing or unparseable launch metadata or flags") from exc
    host = os.environ.get("AI_HOST", "").strip()
    if not host:
        raise ValueError("owning host check failed: AI_HOST is empty")
    replay = ["ai", engine, name, "--is-remote", "--project-prefix", prefix]
    if meta["project_name"]:
        replay += ["-p", meta["project_name"]]
    if meta["sandbox"]:
        replay.append("--sandbox")
    return {
        "session_name": name,
        "owning_host": host,
        "cwd": cwd,
        "project": meta["project_name"],
        "engine": engine,
        "options": {"is_remote": True, "sandbox": meta["sandbox"], "notify": meta.get("notify", False)},
        "extra_args": [],
        "shell": os.environ.get("SHELL", ""),
        "argv": replay,
    }


def _record_recipe(record: dict, engine: str, host: str) -> dict:
    from .main import _cli_group

    argv = record.get("relaunch_argv")
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        raise ValueError("valid_boot_recipe check failed: relaunch_argv must be a string list")
    if argv[:2] != ["ai", engine] or any(arg in ("-h", "--help") for arg in argv):
        raise ValueError("valid_boot_recipe check failed: relaunch_argv is not an engine launch")
    try:
        # Parse with the shipped command, without invoking a launcher or its callbacks.
        with _cli_group.commands[engine].make_context(engine, list(argv[2:])) as ctx:
            parsed, extra_args = ctx.params, list(ctx.args)
    except click.ClickException as exc:
        raise ValueError("valid_boot_recipe check failed: relaunch_argv does not parse") from exc
    return {
        "session_name": record["name"],
        "owning_host": host,
        "cwd": record.get("cwd"),
        "project": parsed.get("project") or "",
        "engine": engine,
        "options": {
            key: parsed.get(key, False)
            for key in ("once", "bare", "sandbox", "no_worktree", "no_direnv", "is_remote", "remote", "notify")
        },
        "extra_args": extra_args,
        "shell": os.environ.get("SHELL", ""),
        "argv": argv,
    }


def enrol_session(name: str, *, dry_run: bool = False) -> None:
    """Validate and persist bookkeeping under the registry's existing mutation lock."""
    if not re.fullmatch(r"(?:cx|c|g|p)-[A-Za-z0-9_-]+", name):
        raise ValueError("session name check failed: expected a c-, g-, p- or cx- session name")
    engine = name.split("-", 1)[0]

    def change(doc: dict) -> bool:
        matches = [r for r in doc["sessions"] if r.get("name") == name]
        if len(matches) > 1 or (matches and matches[0].get("kind") != "local"):
            raise ValueError("registry record check failed: expected one local record")
        record = matches[0] if matches else None
        if record is not None and state.valid_boot_recipe(record):
            output.emit(output.Tag.SESSIONS, "session", f"{name}: already has a valid recipe; unchanged")
            return False
        cwd, leader_argv = inspect_pane(name)
        if record is not None:
            host = config.load_config().get("machine", {}).get("host_id") or doc["machine"]
            if record.get("owning_host", host) != host:
                raise ValueError("owning host check failed: registry record belongs to another host")
            recipe = _record_recipe(record, engine, host)
        else:
            script = config.get_xdg_state_home() / "sessions" / f"{name}.sh"
            recipe = _script_recipe(name, engine, cwd, leader_argv, script)
        candidate = {"name": name, "owning_host": recipe["owning_host"], "boot_recipe": recipe}
        if not state.valid_boot_recipe(candidate):
            raise ValueError("valid_boot_recipe check failed: unsupported or incomplete recipe")
        if Path(recipe["cwd"]).resolve() != Path(cwd).resolve():
            raise ValueError("working directory check failed: recipe cwd differs from live pane cwd")
        output.raw(Untagged.JSON, json.dumps(recipe, indent=2))
        if dry_run:
            return False
        if record is None:
            record = registry._new_record(
                kind="local",
                name=name,
                relaunch_argv=recipe["argv"],
                cwd=cwd,
                remote=None,
                iterm2=None,
                launcher_pid=None,
            )
            doc["sessions"].append(record)
        record.update(boot_recipe=recipe, owning_host=recipe["owning_host"], relaunch="never")
        return True

    if dry_run:
        change(registry.load_registry())
    else:
        registry._mutate(change, timeout=2)


@click.command("enrol")
@click.argument("name")
@click.option("-d", "--dry-run", is_flag=True, help="Print the recipe without changing the registry")
def enrol(name: str, dry_run: bool) -> None:
    """Give an existing live session a validated restart recipe."""
    try:
        enrol_session(name, dry_run=dry_run)
    except (ValueError, OSError, registry.RegistryError) as exc:
        raise click.ClickException(str(exc)) from exc
