"""Explicit, read-only process inspection to enrol legacy session recipes."""

import json
import os
import re
import shlex
import subprocess
from collections.abc import Iterable
from pathlib import Path

import click
import portalocker
import psutil

from . import config, output
from . import session_registry as registry
from . import session_run_state as state
from .output import Untagged


def _pane_snapshot(name: str) -> tuple[str, list[str], int]:
    """Require one exact live pane; retain its PID to detect replacement."""
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
        return cwd, psutil.Process(int(pid)).cmdline(), int(pid)
    except (OSError, ValueError, subprocess.SubprocessError, psutil.Error) as exc:
        raise ValueError("live pane check failed: expected one readable pane and leader") from exc


def inspect_pane(name: str) -> tuple[str, list[str]]:
    """Require one live pane, returning its current directory and leader argv."""
    cwd, argv, _pid = _pane_snapshot(name)
    return cwd, argv


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
    """Inspect without the registry lock, then recheck and persist bookkeeping."""
    if not re.fullmatch(r"(?:cx|c|g|p)-[A-Za-z0-9_-]+", name):
        raise ValueError("session name check failed: expected a c-, g-, p- or cx- session name")
    engine = name.split("-", 1)[0]

    snapshot = registry.load_registry()
    matches = [r for r in snapshot["sessions"] if r.get("name") == name]
    if len(matches) > 1 or (matches and matches[0].get("kind") != "local"):
        raise ValueError("registry record check failed: expected one local record")
    record = matches[0] if matches else None
    if record is not None and state.valid_boot_recipe(record):
        output.emit(output.Tag.SESSIONS, "session", f"{name}: already has a valid recipe; unchanged")
        return
    cwd, leader_argv = inspect_pane(name)
    if record is not None:
        host = config.load_config().get("machine", {}).get("host_id") or snapshot["machine"]
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
        return
    # Boot identity lookup during record creation can invoke a subprocess on macOS.
    prepared_record = (
        record
        if record is not None
        else registry._new_record(
            kind="local",
            name=name,
            relaunch_argv=recipe["argv"],
            cwd=cwd,
            remote=None,
            iterm2=None,
            launcher_pid=None,
        )
    )

    def change(doc: dict) -> bool:
        current_matches = [r for r in doc["sessions"] if r.get("name") == name]
        if current_matches != matches or (current_matches and state.valid_boot_recipe(current_matches[0])):
            raise ValueError("registry record check failed: record changed during enrolment; retry")
        current_record = current_matches[0] if current_matches else prepared_record
        if not current_matches:
            doc["sessions"].append(current_record)
        current_record.update(boot_recipe=recipe, owning_host=recipe["owning_host"], relaunch="never")
        return True

    registry._mutate(change, timeout=2)


def _live_recipe(name: str, cwd: str, argv: list[str], host: str) -> dict:
    """Validate generated launch evidence without executing or rewriting it."""
    from .main import _engine_script_from_meta

    script = config.get_xdg_state_home() / "sessions" / f"{name}.sh"
    if argv != [str(script)] and not (
        len(argv) == 2 and Path(argv[0]).name in ("bash", "zsh", "sh") and argv[1] == str(script)
    ):
        raise ValueError("pane leader is not the exact generated session script")
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
        engine, ai_name = meta["engine"], meta["ai_name"]
        remote = meta.get("is_remote", False)
        if meta["session"] != name or name != f"{engine}-{'r-' if remote else ''}{ai_name}":
            raise ValueError
        for key, value in (("tmux_session", name), ("engine", engine), ("ai_name", ai_name)):
            if f"{key}={shlex.quote(value)}" not in lines:
                raise ValueError
        prefix = meta["project_prefix"]
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", prefix):
            raise ValueError
        options = {key: meta.get(key, False) for key in ("sandbox", "is_remote", "notify")}
        if any(not isinstance(value, bool) for value in options.values()):
            raise ValueError
        directory = meta.get("worktree_dir") or cwd
        if (
            not isinstance(directory, str)
            or not Path(directory).is_absolute()
            or Path(directory).resolve() != Path(cwd).resolve()
        ):
            raise ValueError
        saved = config.get_xdg_state_home() / f"session-meta-{name}.json"
        if saved.exists():
            persisted = json.loads(saved.read_text(encoding="utf-8"))
            if (
                not isinstance(persisted, dict)
                or any(
                    persisted.get(key) != meta.get(key) for key in ("engine", "session", "ai_name", "project_prefix")
                )
                or any(value != meta.get(key) for key, value in persisted.items())
            ):
                raise ValueError

        # Compare launch statements, not the supervisor implementation: an old
        # supervisor need not have leases, replaceable children or generations.
        def launches(text):
            return {
                match.group(0)
                for line in text.splitlines()
                if not line.lstrip().startswith("#")
                for match in re.finditer(r"\brun_agent [^\n]+", line)
            }

        actual = launches(source)
        if not actual or not actual <= launches(_engine_script_from_meta(meta)):
            raise ValueError
    except (OSError, UnicodeError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("generated launch metadata, identity, directory or flags could not be validated") from exc
    replay = ["ai", engine, name, "--project-prefix", prefix]
    project = meta.get("project_name", "")
    if project:
        replay += ["-p", project]
    replay += ["--" + key.replace("_", "-") for key, enabled in options.items() if enabled]
    recipe = {
        "session_name": name,
        "owning_host": host,
        "cwd": directory,
        "project": project,
        "engine": engine,
        "options": options,
        "extra_args": [],
        "argv": replay,
    }
    if not state.valid_boot_recipe({"name": name, "owning_host": host, "boot_recipe": recipe}):
        raise ValueError("unsupported or incomplete recovery recipe")
    return recipe


def _enrol_live(name: str, *, dry_run: bool) -> str:
    if not re.fullmatch(r"(?:cx|c|g|p)-[A-Za-z0-9_-]+", name):
        raise ValueError("not a supported generated session name")
    snapshot = registry.load_registry()
    host = config.load_config(read_only=True).get("machine", {}).get("host_id") or snapshot["machine"]

    def local_records(doc):
        return [r for r in doc["sessions"] if r.get("name") == name and r.get("kind") == "local"]

    def registered(matches):
        if len(matches) > 1 or any(r.get("owning_host", host) != host for r in matches):
            raise ValueError("conflicting local registration or owning host")
        return bool(matches and state.valid_boot_recipe(matches[0]))

    matches = local_records(snapshot)
    if registered(matches):
        return "already-registered"
    pane = _pane_snapshot(name)
    script = config.get_xdg_state_home() / "sessions" / f"{name}.sh"
    source = script.read_bytes() if script.exists() else None
    recipe = _live_recipe(name, pane[0], pane[1], host)
    if matches:
        # A recorded launch may carry engine arguments absent from old metadata.
        recipe = _record_recipe(matches[0], recipe["engine"], host)
        if not state.valid_boot_recipe({"name": name, "owning_host": host, "boot_recipe": recipe}):
            raise ValueError("unsupported or incomplete recorded launch recipe")
        if Path(recipe["cwd"]).resolve() != Path(pane[0]).resolve():
            raise ValueError("recorded launch directory differs from live pane")
    if dry_run:
        return "registered"
    prepared = (
        matches[0]
        if matches
        else registry._new_record(
            kind="local",
            name=name,
            relaunch_argv=recipe["argv"],
            cwd=recipe["cwd"],
            remote=None,
            iterm2=None,
            launcher_pid=None,
        )
    )
    # Recheck live evidence outside the registry lock. Enrolment never invokes a
    # terminal command or changes a supervisor generation inside the transaction.
    if _pane_snapshot(name) != pane or script.read_bytes() != source:
        raise ValueError("live pane changed during enrolment; retry")
    status = "registered"

    def change(doc):
        nonlocal status
        current = local_records(doc)
        if registered(current):
            status = "already-registered"
            return False
        if current != matches:
            raise ValueError("local registration changed during enrolment; retry")
        record = current[0] if current else prepared
        if not current:
            doc["sessions"].append(record)
        record.update(boot_recipe=recipe, owning_host=host)
        record.setdefault("relaunch", "never")
        return True

    registry._mutate(change, timeout=2)
    return status


def enrol_live_sessions(*, dry_run: bool = False, names: Iterable[str] | None = None) -> list[dict]:
    """Discover this host's live sessions; retain a result for every candidate."""
    if names is None:
        try:
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("live tmux discovery is unavailable") from exc
        if result.returncode != 0:
            # tmux returns 1 when no server exists. Other failures remain observable.
            no_server = any(s in result.stderr for s in ("no server running", "no sessions")) or (
                "error connecting" in result.stderr and "No such file or directory" in result.stderr
            )
            if result.returncode == 1 and no_server:
                return []
            raise ValueError("live tmux discovery failed")
        names = result.stdout.splitlines()
    reports = []
    for name in sorted(set(names)):
        try:
            status = _enrol_live(name, dry_run=dry_run)
            reason = "dry-run: would register" if dry_run and status == "registered" else ""
        except (ValueError, OSError, registry.RegistryError, portalocker.exceptions.LockException) as exc:
            status, reason = "skipped", str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        reports.append({"name": name, "status": status, "reason": reason})
    return reports


def heal_live_sessions(*, dry_run: bool = False, names: Iterable[str] | None = None) -> None:
    """Best-effort host recovery must not prevent an unrelated session starting."""
    try:
        for report in enrol_live_sessions(dry_run=dry_run, names=names):
            state.LOGGER.info("live enrolment: %s: %s %s", report["name"], report["status"], report["reason"])
    except Exception as exc:
        # These callers also serve machine replies (a generation or script path).
        # Keep best-effort diagnostics in logs; explicit enrol reports discovery
        # failures as CLI errors and prints every per-session skip.
        state.LOGGER.info("live enrolment unavailable (%s); run ai sessions enrol --all", type(exc).__name__)


@click.command("enrol")
@click.argument("name", required=False)
@click.option("-a", "--all", "all_sessions", is_flag=True, help="Enrol every validated live session on this host")
@click.option("-d", "--dry-run", is_flag=True, help="Print the recipe without changing the registry")
@click.option("-j", "--json", "as_json", is_flag=True, help="Print per-session results as JSON (requires --all)")
def enrol(name: str | None, all_sessions: bool, dry_run: bool, as_json: bool) -> None:
    """Give an existing live session a validated restart recipe."""
    try:
        if all_sessions == (name is not None) or (as_json and not all_sessions):
            raise click.UsageError("provide either NAME or --all; --json requires --all")
        if all_sessions:
            reports = enrol_live_sessions(dry_run=dry_run)
            if as_json:
                output.raw(Untagged.JSON, json.dumps(reports))
            else:
                for report in reports:
                    output.emit(
                        output.Tag.SESSIONS,
                        "enrol",
                        f"{report['name']}: {report['status']} {report['reason']}".rstrip(),
                    )
            return
        assert name is not None
        enrol_session(name, dry_run=dry_run)
    except (ValueError, OSError, registry.RegistryError) as exc:
        raise click.ClickException(str(exc)) from exc
