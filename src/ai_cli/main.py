import sys
import os
import json
import shutil
import tempfile
import time
import subprocess
import re
import shlex
from pathlib import Path

import click

# Module aliases used by the call sites throughout this file. Tests can
# patch on the source module (``patch("ai_cli.config.load_config")``) and
# the dynamic attribute lookup here picks up the mock. The underscore
# prefix avoids clashing with the ``config: dict`` parameter name used by
# several helpers in this file.
from . import config as _config
from . import handoff as _handoff
from . import iterm2 as _iterm2
from . import process_manager as _process_manager
from . import session as _session
from . import session_script as _session_script
from . import transport as _transport
from . import tunnel as _tunnel

# Backwards-compat re-exports so historical ``patch("ai_cli.main.<name>")``
# call sites in the test suite keep working.
from .config import (  # noqa: E402,F401
    DEFAULT_CONFIG,
    WORKTREE_DIR,
    _find_project_dir,
    _get_handoff_queue_dir,
    _get_main_project_dir,
    _get_main_project_name,
    _get_project_prefix_by_name,
    _get_project_registry_path,
    _get_projects_dir,
    _migrate_xdg_dir,
    get_current_project_name,
    get_project_aliases,
    get_session_map,
    get_session_map_path,
    get_xdg_cache_home,
    get_xdg_config_home,
    get_xdg_state_home,
    load_config,
    load_project_registry,
    save_session_map,
    validate_registry_completeness,
)
from .iterm2 import (  # noqa: E402,F401
    _DEFAULT_ITERM2_CONFIG,
    _assign_iterm2_color_slot,
    _configure_tmux_for_iterm2,
    _emit_iterm2_profile_setup,
    _is_iterm2,
    _iterm2_palette,
    _iterm2_session_type,
    _iterm2_state_dir,
    _load_iterm2_config,
    _release_iterm2_color_slot,
    _resolve_iterm2_config,
)
from .handoff import (  # noqa: E402,F401
    _claim_handoff_for_signal,
    _find_best_handoff,
    _format_handoff_summary,
    _log_handoff_event,
    check_handoff,
    check_handoff_project,
    claim_handoff,
    complete_handoff,
    post_handoff,
)
from .session import (  # noqa: E402,F401
    _AI_SESSION_RE,
    _checkpoint_to_chat_uuid,
    _convert_checkpoint_to_chat,
    _find_latest_gemini_uuid,
    _get_chat_last_message_timestamp,
    _resolve_is_remote,
    _sweep_stale_iterm2_profiles,
    build_session_name,
    cleanup_stale_sessions,
    cleanup_worktree,
    create_worktree,
    detect_repo_root,
    find_next_index,
    find_recent_session,
    get_latest_gemini_session_id,
    get_project_prefix,
    resolve_session,
)
from .transport import (  # noqa: E402,F401
    _ensure_tailscale_up,
    _ensure_vpn_watcher,
    _is_vpn_active,
    _maybe_stop_vpn_watcher,
    _monotonic,
    _run_transport_loop,
    _write_transport_state,
)
from .process_manager import (  # noqa: E402,F401
    _cmd_signal_watch_start,
    _cmd_signal_watch_status,
    _cmd_signal_watch_stop,
    _ensure_circusd,
)
from .session_script import get_engine_script  # noqa: F401
from .tunnel import (  # noqa: E402,F401
    _cmd_cdp_start,
    _cmd_cdp_status,
    _cmd_cdp_stop,
    _cmd_tunnel_start,
    _cmd_tunnel_status,
    _cmd_tunnel_stop,
    _ensure_nats_tunnel,
    _find_chrome_binary,
)

# MAINTENANCE: when editing ai-cli, also update:
#   - docs/tools/ai-cli-usage.md (usage reference, session naming, transport, auto-resume)
#   - README.md (if CLI interface changes)
#   - Code comments in this file (especially around session naming, resume logic, mosh/transport)
#   - CLAUDE.md ai-cli deploy note (reinstall in 3 places: Mac uv tool, server uv tool, extra_venvs)


# --- Helpers ---


def _find_aicli_project_path(config: dict) -> "Path | None":
    """Locate the ai-cli-utils source tree regardless of cwd.

    Priority:
    1. [deploy] project_path from config
    2. Package __file__ location (dev-editable install)
    """
    cfg_path = config.get("deploy", {}).get("project_path", "")
    if cfg_path:
        return Path(cfg_path).expanduser()
    try:
        import importlib.util

        spec = importlib.util.find_spec("ai_cli")
        if spec and spec.origin:
            pkg_dir = Path(spec.origin).parent  # …/ai_cli/
            for candidate in (pkg_dir.parent, pkg_dir.parent.parent):
                pyproject = candidate / "pyproject.toml"
                if pyproject.exists() and "ai-cli-utils" in pyproject.read_text():
                    return candidate
    except Exception:
        pass
    # cwd fallback — valid when the user is already in the project directory.
    # Check the project name specifically to avoid matching projects that merely
    # depend on ai-cli-utils.
    cwd = Path.cwd()
    cwd_pyproject = cwd / "pyproject.toml"
    if cwd_pyproject.exists():
        content = cwd_pyproject.read_text()
        if re.search(r'^name\s*=\s*["\']ai-cli-utils["\']', content, re.MULTILINE):
            return cwd
    return None


def _deploy_cc_config_files(project_path: Path) -> None:
    """Copy bundled CC config files from the package data dir to ~/.claude/.

    Writes plain files, replacing any pre-existing symlinks. These files are
    owned by ai-cli-utils and must not be managed by ai sync or tracked in any
    project git repo.
    """
    data_dir = project_path / "src" / "ai_cli" / "data"
    cc_dir = Path.home() / ".claude"

    # Files to deploy: (source relative to data_dir, dest relative to ~/.claude/)
    deployable = [
        ("statusline-command.sh", "statusline-command.sh"),
    ]

    for src_name, dst_rel in deployable:
        src = data_dir / src_name
        if not src.exists():
            continue
        dst = cc_dir / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Unlink first so we replace any symlink with a plain file
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        import shutil as _shutil

        _shutil.copy2(src, dst)
        if src.suffix == ".sh":
            dst.chmod(dst.stat().st_mode | 0o755)


def _auto_update_if_stale(config: dict) -> None:
    """Run `ai update --force` if the project has new commits since the last update."""
    project_path = _find_aicli_project_path(config)
    if project_path is None or not (project_path / "pyproject.toml").exists():
        return
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_path, capture_output=True, text=True)
    if head.returncode != 0:
        return
    current_hash = head.stdout.strip()
    stamp_file = _config.get_xdg_state_home() / "last_update_commit.txt"
    if stamp_file.exists() and stamp_file.read_text().strip() == current_hash:
        return
    # Serialize concurrent workers with an exclusive create-only lockfile.
    # O_CREAT|O_EXCL is atomic on both POSIX and Windows.
    lock_path = stamp_file.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        return  # Another process already claimed the update
    try:
        # Re-check after acquiring lock — another process may have just updated.
        if stamp_file.exists() and stamp_file.read_text().strip() == current_hash:
            return
        # Write stamp before running update so any remaining concurrent readers skip.
        stamp_file.write_text(current_hash)
        print("ai-cli-utils has new commits — running ai update --force...")
        ai_bin = shutil.which("ai") or "ai"
        result = subprocess.run([ai_bin, "update", "--force"], cwd=project_path)
        if result.returncode != 0:
            print("Warning: auto-update failed, continuing with current version", file=sys.stderr)
    finally:
        lock_path.unlink(missing_ok=True)


def trigger_background_update():
    state_file = _config.get_xdg_state_home() / "update_check.json"
    now = time.time()
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            if now - state.get("last_checked", 0) < 3600 * 24:
                return
        except Exception:
            pass
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_checked": now}))
    subprocess.Popen(
        ["uv", "tool", "upgrade", "ai-cli-utils"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _pkg_version_string() -> str:
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("ai-cli-utils")
    except Exception:
        return "unknown"


# --- `ai internal` fast-path — machine-to-machine commands ---


def _handle_internal(argv: list[str]) -> None:
    """Handle `ai internal <action> [args...]` — invoked from bash hooks.

    Kept out of Click so sub-millisecond startup is preserved for hot paths.
    Always calls ``sys.exit`` — never returns.
    """
    if not argv:
        print("Usage: ai internal <action> [args...]", file=sys.stderr)
        sys.exit(1)
    action = argv[0]
    config = _config.load_config()

    if action == "get-latest-gemini-id":
        _ai_name_arg = argv[1] if len(argv) > 1 else None
        res = _session.get_latest_gemini_session_id(_ai_name_arg)
        if res:
            print(res)
        sys.exit(0)
    elif action == "update-session-map":
        if len(argv) < 4:
            print("Usage: ai internal update-session-map <engine> <ai_name> <uuid>", file=sys.stderr)
            sys.exit(1)
        engine, ai_name, uuid = argv[1], argv[2], argv[3]
        d = _config.get_session_map(engine)
        d[ai_name] = uuid
        _config.save_session_map(d, engine)
        sys.exit(0)
    elif action == "cleanup-worktree":
        if len(argv) < 2:
            print("Usage: ai internal cleanup-worktree <ai_name>", file=sys.stderr)
            sys.exit(1)
        _session.cleanup_worktree(argv[1])
        sys.exit(0)
    elif action == "release-color-slot":
        if len(argv) < 2:
            print("Usage: ai internal release-color-slot <ai_name>", file=sys.stderr)
            sys.exit(1)
        _iterm2._release_iterm2_color_slot(argv[1])
        sys.exit(0)
    elif action == "cleanup-session-files":
        if len(argv) < 2:
            print("Usage: ai internal cleanup-session-files <ai_name>", file=sys.stderr)
            sys.exit(1)
        from . import icon_generator as _ig_cs

        _ig_cs.cleanup_session_files(argv[1])
        sys.exit(0)
    elif action == "get-version":
        print(_pkg_version_string())
        sys.exit(0)
    elif action == "refresh-template":
        if len(argv) < 2:
            print("Usage: ai internal refresh-template <tmux_session>", file=sys.stderr)
            sys.exit(1)
        tmux_session = argv[1]
        meta_path = _config.get_xdg_state_home() / f"session-meta-{tmux_session}.json"
        if not meta_path.exists():
            print(f"No session metadata for {tmux_session}", file=sys.stderr)
            sys.exit(1)
        try:
            meta = json.loads(meta_path.read_text())
        except Exception as exc:
            print(f"Failed to read session metadata: {exc}", file=sys.stderr)
            sys.exit(1)
        from .session_script import get_engine_script

        script = get_engine_script(
            engine=meta["engine"],
            ai_name=meta["ai_name"],
            session=meta["session"],
            prefix=meta["prefix"],
            project_prefix=meta["project_prefix"],
            session_id_uuid=meta.get("session_id_uuid") or None,
            sandbox=meta.get("sandbox", False),
            worktree_dir=meta.get("worktree_dir") or None,
            notify=meta.get("notify", False),
            is_remote=meta.get("is_remote", False),
            project_name=meta.get("project_name", ""),
            iterm2_slot=meta.get("iterm2_slot") or None,
            iterm2_cfg=meta.get("iterm2_cfg") or None,
            config_reload_idle_secs=meta.get("config_reload_idle_secs", 90),
            gemini_cmd=meta.get("gemini_cmd", "gemini"),
        )
        import tempfile

        fd, tmp_path = tempfile.mkstemp(prefix=f"ai-refresh-{tmux_session}-", suffix=".sh")
        with os.fdopen(fd, "w") as fh:
            fh.write("#!/usr/bin/env bash\n")
            fh.write(f'rm -f "{tmp_path}"\n')  # self-delete on exec
            fh.write(script)
        print(tmp_path)
        sys.exit(0)
    elif action == "notify":
        if len(argv) < 3:
            print("Usage: ai internal notify <session_id> <message>", file=sys.stderr)
            sys.exit(1)
        from .notifications import NotificationManager

        NotificationManager(argv[1]).notify(argv[2])
        sys.exit(0)
    elif action == "publish-event":
        if len(argv) < 3:
            print("Usage: ai internal publish-event <session_id> <event_type>", file=sys.stderr)
            sys.exit(1)
        import asyncio
        from .messaging import NATSClient

        nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        client = NATSClient(servers=nats_servers)
        try:
            asyncio.run(client.publish_event(argv[1], argv[2]))
        except Exception:
            pass  # NATS unavailable — non-fatal
        sys.exit(0)
    elif action == "publish-heartbeat":
        if len(argv) < 3:
            print("Usage: ai internal publish-heartbeat <session_id> <data_json>", file=sys.stderr)
            sys.exit(1)
        import asyncio
        from .messaging import NATSClient

        try:
            data = json.loads(argv[2])
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)
        nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        client = NATSClient(servers=nats_servers)
        try:
            asyncio.run(client.publish_heartbeat(argv[1], data))
        except Exception:
            pass  # NATS unavailable — non-fatal
        sys.exit(0)
    elif action == "publish-session-event":
        if len(argv) < 3:
            print("Usage: ai internal publish-session-event <session_id> <started|stopped>", file=sys.stderr)
            sys.exit(1)
        import asyncio
        from .messaging import NATSClient

        session_id = argv[1]
        event_verb = argv[2]
        subject = f"session.{session_id}.{event_verb}"
        nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        client = NATSClient(servers=nats_servers)
        try:
            asyncio.run(client.publish(subject, {"session_id": session_id, "event": event_verb, "ts": time.time()}))
        except Exception:
            pass  # NATS unavailable — non-fatal
        sys.exit(0)
    elif action == "publish":
        if len(argv) < 3:
            print("Usage: ai internal publish <subject> <json_payload>", file=sys.stderr)
            sys.exit(1)
        import asyncio
        from .messaging import NATSClient

        subject = argv[1]
        try:
            payload = json.loads(argv[2])
        except (json.JSONDecodeError, IndexError):
            payload = {}
        nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        client = NATSClient(servers=nats_servers)
        try:
            asyncio.run(client.publish(subject, payload))
        except Exception:
            pass  # NATS unavailable — non-fatal
        sys.exit(0)
    elif action == "signal-watch":
        if len(argv) < 3:
            print("Usage: ai internal signal-watch <project> <session_id>", file=sys.stderr)
            sys.exit(1)
        _internal_signal_watch(argv[1], argv[2], config)
        sys.exit(0)
    elif action == "quota-subscriber":
        _internal_quota_subscriber(config)
        sys.exit(0)
    elif action == "handoff-drain":
        if len(argv) < 3:
            sys.exit(0)
        _internal_handoff_drain(argv[1], argv[2], config)
        sys.exit(0)
    else:
        print(f"Usage: ai internal <action> [args...] (unknown action: {action})", file=sys.stderr)
        sys.exit(1)


async def _on_handoff_signal_watch(
    data: dict,
    *,
    handoff_dir: "Path | None",
    pending_file: "Path",
    session_id: str,
    machine_id: str,
) -> None:
    """Process one inbound handoff message for signal-watch.

    Extracted from the ``_on_handoff`` closure inside ``_internal_signal_watch``
    so it can be imported and unit-tested independently of the NATS subscription.
    """
    handoff_id = data.get("id")
    title = data.get("title", "")
    priority = data.get("priority", "")
    message = data.get("message", "")
    for_machine = data.get("for_machine", "")
    if not for_machine or for_machine != machine_id:
        return
    print(f"\n[HANDOFF] {priority} #{handoff_id}: {title}", flush=True)
    if handoff_dir is None or not handoff_id:
        return
    content = data.get("content")
    filename = data.get("filename")
    if content and filename:
        pending_dir = handoff_dir / "pending"
        claimed_dir = handoff_dir / "claimed"
        local_file = pending_dir / filename
        if (claimed_dir / filename).exists():
            return
        if not local_file.exists():
            pending_dir.mkdir(parents=True, exist_ok=True)
            try:
                local_file.write_text(content)
            except OSError:
                pass
    claimed = _handoff._claim_handoff_for_signal(handoff_dir, int(handoff_id), session_id)
    if claimed is None:
        return
    _handoff._log_handoff_event(
        "handoff.claimed",
        handoff_id=handoff_id,
        session=session_id,
        layer="nats_realtime" if data.get("_source") != "startup_scan" else "startup_scan",
    )
    resume_msg = f"Auto-pickup: {priority} handoff #{handoff_id} — {title}. File: {claimed}\n\n{message}"
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text(resume_msg)
    signal_file = _config.get_xdg_state_home() / f"cc-exit-{session_id}"
    try:
        signal_file.touch()
    except OSError:
        pass


def _write_pending_if_claimed_drain(
    data: dict,
    *,
    handoff_dir: "Path | None",
    prompt_file: "Path",
    session: str,
    machine_id: str,
) -> bool:
    """Claim a handoff from drain data and write the resume prompt file.

    Extracted from the ``_write_pending_if_claimed`` closure inside
    ``_internal_handoff_drain`` so it can be unit-tested independently.
    Returns ``True`` if a handoff was claimed and the prompt file written.
    """
    handoff_id = data.get("id")
    title = data.get("title", "")
    priority = data.get("priority", "")
    message = data.get("message", "")
    for_machine = data.get("for_machine", "")
    if not for_machine or for_machine != machine_id:
        return False
    if handoff_dir is None or not handoff_id:
        return False
    content = data.get("content")
    filename = data.get("filename")
    if content and filename:
        pending_dir = handoff_dir / "pending"
        claimed_dir = handoff_dir / "claimed"
        local_file = pending_dir / filename
        if (claimed_dir / filename).exists():
            return False
        if not local_file.exists():
            pending_dir.mkdir(parents=True, exist_ok=True)
            try:
                local_file.write_text(content)
            except OSError:
                return False
    claimed = _handoff._claim_handoff_for_signal(handoff_dir, int(handoff_id), session)
    if claimed is None:
        return False
    _handoff._log_handoff_event(
        "handoff.claimed",
        handoff_id=handoff_id,
        session=session,
        layer="pre_launch_drain",
    )
    resume_msg = f"Auto-pickup: {priority} handoff #{handoff_id} — {title}. File: {claimed}\n\n{message}"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(resume_msg)
    return True


async def _on_quota_snapshot_handler(data: dict) -> None:
    """Process one inbound quota.snapshot message.

    Extracted from the ``_on_quota_snapshot_msg`` closure inside
    ``_internal_quota_subscriber`` so it can be unit-tested independently.
    """
    from .quota_db import record_quota_snapshot

    try:
        record_quota_snapshot(
            usage_percent=data["usage_percent"],
            session_pct=data.get("session_pct"),
            weekly_sonnet_pct=data.get("weekly_sonnet_pct"),
            extra_pct=data.get("extra_pct"),
            reset_at=data.get("reset_at"),
        )
    except Exception:
        pass


def _internal_signal_watch(sw_project: str, sw_session_id: str, config: dict) -> None:
    import asyncio
    from .messaging import NATSClient

    sw_handoff_dir = _config._get_handoff_queue_dir()
    sw_pending_file = _config.get_xdg_state_home() / f"handoff-pending-{sw_session_id}"
    nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    sw_client = NATSClient(servers=nats_servers)

    async def _on_handoff(data):
        await _on_handoff_signal_watch(
            data,
            handoff_dir=sw_handoff_dir,
            pending_file=sw_pending_file,
            session_id=sw_session_id,
            machine_id=os.environ.get("AI_CLI_HOST", ""),
        )

    # Startup scan: pick up any unclaimed files already in the pending queue
    if sw_handoff_dir is not None:
        pending_dir = sw_handoff_dir / "pending"
        if pending_dir.exists():
            for f in sorted(pending_dir.glob("*.md")):
                try:
                    fid = int(f.name.split("-")[0])
                except ValueError:
                    continue
                try:
                    raw = f.read_text()
                    fm_title = re.search(r'^title:\s*"?([^"\n]+)"?', raw, re.MULTILINE)
                    fm_priority = re.search(r"^priority:\s*(\S+)", raw, re.MULTILINE)
                    fm_for_machine = re.search(r"^for_machine:\s*(\S+)", raw, re.MULTILINE)
                    body = raw.split("---", 2)[-1].strip() if raw.count("---") >= 2 else ""
                    scan_title = fm_title.group(1).strip() if fm_title else f.stem
                    scan_priority = fm_priority.group(1) if fm_priority else ""
                    scan_for_machine = fm_for_machine.group(1) if fm_for_machine else ""
                except OSError:
                    scan_title, scan_priority, body, scan_for_machine = f.stem, "", "", ""
                asyncio.run(
                    _on_handoff(
                        {
                            "id": fid,
                            "title": scan_title,
                            "priority": scan_priority,
                            "message": body,
                            "for_machine": scan_for_machine,
                            "_source": "startup_scan",
                        }
                    )
                )

    consumer_name = f"{sw_session_id}-signal-watcher"

    async def _run_subscriptions() -> None:
        await sw_client.subscribe_durable(f"handoff.{sw_project}", consumer_name, _on_handoff)

    try:
        asyncio.run(_run_subscriptions())
    except Exception:
        # Not covered: _run_subscriptions blocks indefinitely on success; exception
        # path requires a live NATS server to fail mid-subscription.
        pass


def _internal_quota_subscriber(config: dict) -> None:
    # Persistent daemon: subscribes to quota.snapshot via JetStream durable consumer.
    # Runs as a Circus-managed process on Mac, independent of CC session lifecycle.
    # Missed messages during downtime are replayed on reconnect (JetStream durability).
    import asyncio
    from .messaging import NATSClient

    qs_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    qs_client = NATSClient(servers=qs_servers)

    try:
        asyncio.run(
            qs_client.subscribe_durable(
                "quota.snapshot",
                "quota-subscriber-mac",
                _on_quota_snapshot_handler,
            )
        )
    except Exception:
        # Not covered: subscribe_durable blocks indefinitely on success; exception
        # path requires a live NATS server to fail mid-subscription.
        pass


def _internal_handoff_drain(hd_project: str, hd_session: str, config: dict) -> None:
    # Synchronous: drain pending NATS messages + local file scan, then exit.
    # Called BEFORE CC launches so prompt_file is ready on first invocation.
    import asyncio
    from .messaging import NATSClient

    hd_handoff_dir = _config._get_handoff_queue_dir()
    hd_prompt_file = _config.get_xdg_state_home() / f"cc-resume-prompt-{hd_session}"
    nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    hd_client = NATSClient(servers=nats_servers)
    _handoff._log_handoff_event("handoff.drain.started", session=hd_session, project=hd_project)

    def _write_pending_if_claimed(data):
        return _write_pending_if_claimed_drain(
            data,
            handoff_dir=hd_handoff_dir,
            prompt_file=hd_prompt_file,
            session=hd_session,
            machine_id=os.environ.get("AI_CLI_HOST", ""),
        )

    # 1. Local file scan first (fast, no network)
    if hd_handoff_dir is not None:
        pending_dir = hd_handoff_dir / "pending"
        if pending_dir.exists():
            best = _handoff._find_best_handoff(pending_dir, project_filter=hd_project)
            if best is not None:
                try:
                    fid = int(best.name.split("-")[0])
                    raw = best.read_text()
                    fm_title = re.search(r'^title:\s*"?([^"\n]+)"?', raw, re.MULTILINE)
                    fm_priority = re.search(r"^priority:\s*(\S+)", raw, re.MULTILINE)
                    fm_for_machine = re.search(r"^for_machine:\s*(\S+)", raw, re.MULTILINE)
                    body = raw.split("---", 2)[-1].strip() if raw.count("---") >= 2 else ""
                    local_for_machine = fm_for_machine.group(1) if fm_for_machine else ""
                    _handoff._log_handoff_event(
                        "handoff.drain.local_found",
                        session=hd_session,
                        handoff_id=fid,
                        for_machine=local_for_machine,
                    )
                    _write_pending_if_claimed(
                        {
                            "id": fid,
                            "title": fm_title.group(1).strip() if fm_title else best.stem,
                            "priority": fm_priority.group(1) if fm_priority else "",
                            "message": body,
                            "for_machine": local_for_machine,
                        }
                    )
                except Exception:
                    # Not covered: requires filesystem error reading a pending
                    # handoff file that exists and was just discovered by glob.
                    pass

    # 2. NATS drain: pull pending JetStream messages (non-blocking, 2s timeout)
    if not hd_prompt_file.exists():
        _handoff._log_handoff_event("handoff.drain.nats_attempt", session=hd_session, project=hd_project)

        # Not covered: _drain() is an async closure that requires a live NATS
        # JetStream server. Inner branches (js is None, message decode error,
        # _write_pending_if_claimed returning True, fetch timeout, subscribe
        # failure) all require specific live-server or network-failure conditions.
        # See docs/test/unit-tests.md §Intentionally Uncovered Lines.
        async def _drain():
            try:
                await hd_client.connect()
            except Exception as e:
                _handoff._log_handoff_event("handoff.drain.nats_connect_failed", session=hd_session, error=str(e))
                return
            if not hd_client.js:
                _handoff._log_handoff_event("handoff.drain.nats_no_js", session=hd_session)
                return
            consumer_name = f"{hd_session}-pre-launch"
            subject = f"handoff.{hd_project}"
            try:
                await hd_client._ensure_stream(subject)
                sub = await hd_client.js.pull_subscribe(subject, durable=consumer_name)
                while True:
                    try:
                        msgs = await sub.fetch(1, timeout=2)
                        for msg in msgs:
                            try:
                                data = json.loads(msg.data.decode())
                            except Exception:
                                data = {}
                            await msg.ack()
                            if _write_pending_if_claimed(data):
                                return
                    except Exception:
                        break
            except Exception as e:
                _handoff._log_handoff_event("handoff.drain.nats_subscribe_failed", session=hd_session, error=str(e))
            finally:
                await hd_client.close()

        try:
            asyncio.run(_drain())
        except Exception as e:
            # Not covered: requires asyncio.run() itself to raise, which needs a
            # broken event loop or NATS server in a specific failure state.
            _handoff._log_handoff_event("handoff.drain.nats_run_failed", session=hd_session, error=str(e))


# --- Command implementations (invoked by Click handlers below) ---


def _do_update_or_deploy(force_reinstall: bool, config: dict) -> None:
    project_path = _find_aicli_project_path(config)
    if project_path is None:
        print(
            "Error: could not locate ai-cli-utils source. Set [deploy] project_path in config.",
            file=sys.stderr,
        )
        sys.exit(1)
    pyproject = project_path / "pyproject.toml"
    if not pyproject.exists():
        print(
            f"Error: pyproject.toml not found at {project_path}. Set [deploy] project_path in config.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Restore pyproject.toml before pull — it may be dirty from an interrupted previous update
    subprocess.run(["git", "checkout", "--", "pyproject.toml"], cwd=project_path, check=False)
    print("Pulling latest from origin...")
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=project_path, check=False)
    # Abort if pull left unresolved conflict markers — installing with conflicts produces a broken package
    src_dir = project_path / "src"
    conflict_files = []
    if src_dir.is_dir():
        for py_file in src_dir.rglob("*.py"):
            try:
                text = py_file.read_text(errors="replace")
                if any(ln.startswith("<<<<<<< ") or ln.startswith(">>>>>>> ") for ln in text.splitlines()):
                    conflict_files.append(py_file.relative_to(project_path))
            except OSError:
                pass
    if conflict_files:
        print(
            "Error: unresolved git conflict markers found — resolve before installing:",
            file=sys.stderr,
        )
        for f in conflict_files:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)
    # Read version after pull so the bump applies to the current remote state
    original = pyproject.read_text()
    m = re.search(r'^(version\s*=\s*")([^"]+)(")', original, re.MULTILINE)
    if not m:
        print("Error: could not find version in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    base = re.sub(r"\.post\d+$", "", m.group(2))
    new_version = f"{base}.post{int(time.strftime('%Y%m%d%H%M%S'))}"
    print(f"Updating {m.group(2)} → {new_version}")
    uv_bin = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    exit_code = 0
    try:
        pyproject.write_text(original[: m.start(2)] + new_version + original[m.end(2) :])
        uv_cmd = [uv_bin, "tool", "install", str(project_path), "--force"]
        if force_reinstall:
            uv_cmd.append("--reinstall")
        result = subprocess.run(uv_cmd, cwd=project_path)
        exit_code = result.returncode
    finally:
        pyproject.write_text(original)
    if exit_code == 0:
        # Install into any configured extra venvs (e.g. tool venvs that depend on ai-cli-utils)
        extra_venvs = config.get("update", {}).get("extra_venvs", [])
        for venv_path_str in extra_venvs:
            venv_path = Path(venv_path_str).expanduser()
            if venv_path.exists():
                pip_cmd = [uv_bin, "pip", "install", str(project_path)]
                if force_reinstall:
                    pip_cmd.append("--force-reinstall")
                subprocess.run(
                    pip_cmd,
                    env={**os.environ, "VIRTUAL_ENV": str(venv_path)},
                    check=False,
                )
        # Clear pycache
        subprocess.run(
            ["find", str(project_path), "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
            check=False,
        )
        # Record HEAD hash so session start can detect staleness
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_path, capture_output=True, text=True)
        if head.returncode == 0:
            stamp_file = _config.get_xdg_state_home() / "last_update_commit.txt"
            stamp_file.parent.mkdir(parents=True, exist_ok=True)
            stamp_file.write_text(head.stdout.strip())
        # Deploy bundled CC config files to ~/.claude/ — write as plain files so any
        # pre-existing symlinks are replaced. These files are owned by ai-cli-utils and
        # should not be managed by ai sync or tracked in any project git repo.
        _deploy_cc_config_files(project_path)
    sys.exit(exit_code)


def _do_reconnect(requested: "list[int] | None", config: dict) -> None:
    # List remote tmux sessions and print reconnect commands.
    remote_cfg = config.get("remote", {})
    host = remote_cfg.get("host", "")
    user = remote_cfg.get("user", "ubuntu")
    if not host:
        print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
        sys.exit(1)

    probe = subprocess.run(
        ["ssh", f"{user}@{host}", "tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        print("Error: could not list remote tmux sessions", file=sys.stderr)
        sys.exit(1)

    remote_sessions = [s.strip() for s in probe.stdout.splitlines() if s.strip().startswith("c-r-")]
    if not remote_sessions:
        print("No remote CC sessions found on server.")
        sys.exit(0)

    if requested:
        remote_sessions = [s for s in remote_sessions if any(s.endswith(f"-{n}") for n in requested)]

    if not remote_sessions:
        print(f"No matching remote sessions for: {requested}")
        sys.exit(0)

    aliases = _config.get_project_aliases()

    # Load active transport state files for annotation
    _state_dir = _config.get_xdg_state_home()
    _transport_by_session: dict[str, str] = {}
    for _tf in _state_dir.glob("transport-*.json"):
        try:
            _td = json.loads(_tf.read_text())
            _transport_by_session[_td.get("session", "")] = _td.get("transport", "")
        except Exception:
            pass

    print(f"Found {len(remote_sessions)} remote session(s). Run each in a separate terminal:\n")
    for session_name in sorted(remote_sessions):
        parts = session_name.split("-")
        if len(parts) >= 4:
            num = parts[-1]
            proj_prefix = "-".join(parts[2:-1])
        else:
            continue
        project_name = aliases.get(proj_prefix, proj_prefix)
        _transport_tag = ""
        _transport = _transport_by_session.get(session_name, "")
        if _transport:
            _transport_tag = f"  [{_transport} connected]"
        if project_name == proj_prefix:
            print(f"  ai c {num} -R{_transport_tag}")
        else:
            print(f"  ai c {num} -R -p {proj_prefix}{_transport_tag}")
    print()
    sys.exit(0)


def _do_attach(session_name: str) -> None:
    check = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True)
    if check.returncode != 0:
        print(f"No tmux session named '{session_name}'", file=sys.stderr)
        sys.exit(1)
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def _do_ls(show_all: bool) -> None:
    res = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print("No tmux sessions found (is tmux running?)", file=sys.stderr)
        sys.exit(0)

    now = int(time.time())
    sessions = []
    for line in res.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split()
        name = parts[0]
        try:
            activity = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            activity = 0
        if not show_all and not _AI_SESSION_RE.match(name):
            continue
        sessions.append((name, activity))

    if not sessions:
        msg = (
            "No tmux sessions found." if show_all else "No ai-cli sessions found. Use --all to show all tmux sessions."
        )
        print(msg)
        sys.exit(0)

    sessions.sort(key=lambda x: x[1], reverse=True)

    def _human_age(ts: int) -> str:
        delta = now - ts
        if delta < 60:
            return f"{delta}s"
        if delta < 3600:
            return f"{delta // 60}m"
        if delta < 86400:
            return f"{delta // 3600}h"
        return f"{delta // 86400}d"

    def _project_from_session(name: str) -> str:
        """Extract project prefix from session name: c-myproject-1 → myproject, c-r-myproject-1 → myproject."""
        parts = name.split("-")
        # Format: {c|g}[-r]-{project}-{index}
        if len(parts) >= 3 and parts[0] in ("c", "g"):
            start = 2 if parts[1] == "r" else 1
            # project is everything between start and last segment
            return "-".join(parts[start:-1]) if len(parts) > start + 1 else parts[start]
        return name

    fzf = shutil.which("fzf")
    if fzf is None:
        # Try to install fzf
        apt = shutil.which("apt")
        if apt:
            print("fzf not found — installing with apt...")
            subprocess.run(["apt", "install", "-y", "fzf"], check=False)
            fzf = shutil.which("fzf")

    if fzf:
        lines = [f"{name}\t{_project_from_session(name)}\t{_human_age(activity)}" for name, activity in sessions]
        result = subprocess.run(
            [
                fzf,
                "--ansi",
                "--reverse",
                "--prompt=session> ",
                "--delimiter=\t",
                "--with-nth=1,3",
                "--preview-window=hidden",
            ],
            input="\n".join(lines),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            sys.exit(0)
        selected = result.stdout.strip().split("\t")[0]
        os.execvp("tmux", ["tmux", "attach-session", "-t", selected])
    else:
        # Plain list fallback
        for i, (name, activity) in enumerate(sessions, 1):
            project = _project_from_session(name)
            print(f"  {i}. {name}  ({project})  {_human_age(activity)} ago")
        print("\nTo attach: ai attach <name>")
        sys.exit(0)


def _do_color(color_arg: str) -> None:
    _ai_name_env = os.environ.get("AI_TMUX_SESSION", "")
    if not _ai_name_env:
        print("ai color: not inside an ai session (AI_TMUX_SESSION not set)", file=sys.stderr)
        sys.exit(1)
    # Resolve color arg: named palette entry or raw hex
    _iterm2_cfg_c = _iterm2._load_iterm2_config()
    _palette_c = dict(_iterm2._iterm2_palette(_iterm2_cfg_c))
    if color_arg.startswith("#"):
        _new_hex = color_arg
    elif color_arg in _palette_c:
        _new_hex = f"#{_palette_c[color_arg]}"
    else:
        print(f"ai color: unknown color '{color_arg}'. Use a palette name or #RRGGBB.", file=sys.stderr)
        sys.exit(1)
    # Determine engine from session name convention
    _engine_c = "g" if _ai_name_env.startswith("g-") else "c"
    _session_type_c = _iterm2._iterm2_session_type(_engine_c)
    try:
        from . import icon_generator as _ig_c

        _icon_color_c = _iterm2._resolve_iterm2_config(_iterm2_cfg_c, _ai_name_env).get("icon_color")
        _ig_c.cleanup_session_files(_ai_name_env)
        _icon_path_c = _ig_c.generate_session_icon(_ai_name_env, _new_hex, _session_type_c, _icon_color_c)
        _ig_c.generate_dynamic_profile(_ai_name_env, _new_hex, _session_type_c, _icon_path_c)
    except Exception as e:
        print(f"ai color: icon generation failed: {e}", file=sys.stderr)
    _color_no_hash_c = _new_hex.lstrip("#")
    _profile_name_c = f"ai-cli:{_ai_name_env}"
    sys.stdout.write(f"\033]1337;SetProfile={_profile_name_c}\007")
    sys.stdout.write(f"\033]1337;SetColors=tab={_color_no_hash_c}\007")
    sys.stdout.flush()
    print(f"Color updated to {_new_hex}")
    sys.exit(0)


def _do_handoff_post(remote: bool, for_machine: str, post_args: "list[str]") -> None:
    if remote:
        remote_cfg = _config.load_config().get("remote", {})
        remote_host = remote_cfg.get("host", "")
        remote_user = remote_cfg.get("user", "ubuntu")
        if not remote_host:
            print("Error: [remote] host not set in config", file=sys.stderr)
            sys.exit(1)
        ssh_args = ["ssh", f"{remote_user}@{remote_host}", "ai", "handoff", "post"]
        # Preserve the --for-machine flag for the remote side.
        ssh_args += ["--for-machine", for_machine]
        ssh_args += list(post_args)
        os.execvp("ssh", ssh_args)
    if not for_machine:
        print("Error: --for-machine <machine> is required", file=sys.stderr)
        sys.exit(1)
    if len(post_args) < 4:
        print(
            "Usage: ai handoff post --for-machine <machine> <title> <priority> <project> <message>",
            file=sys.stderr,
        )
        sys.exit(1)
    _handoff.post_handoff(post_args[0], post_args[1], post_args[2], post_args[3], for_machine=for_machine)
    sys.exit(0)


# --- Session launch (default command — `ai c NAME`, `ai g NAME`) ---


def _do_session_launch(
    engine: str,
    name: str,
    resume: bool,
    once: bool,
    bare: bool,
    notify: bool,
    sandbox: bool,
    no_worktree: bool,
    remote: bool,
    project: str,
    is_remote: bool,
    project_prefix_override: str,
    extra_args: "list[str]",
    config: dict,
) -> None:
    # Auto-promote to remote mode when running directly on a non-Mac host so
    # the c-r- / g-r- prefix is applied even without an explicit --is-remote flag.
    is_remote = _session._resolve_is_remote(is_remote)
    if project_prefix_override:
        project_prefix = project_prefix_override
    elif project and not remote:
        # Local -p flag: derive prefix from the target project, not cwd
        _lp_aliases = _config.get_project_aliases()
        _lp_name = _lp_aliases.get(project, project)
        project_prefix = _config._get_project_prefix_by_name(_lp_name)
    else:
        project_prefix = _session.get_project_prefix()
    engine_short = "c" if engine == "c" else "g"
    remote_seg = "-r" if is_remote else ""
    prefix = f"{engine_short}{remote_seg}-{project_prefix}-"

    use_sandbox = sandbox
    sandbox_flag = "-s" if use_sandbox else "--no-sandbox"
    gemini_cmd = config.get("gemini", {}).get("command", "gemini")

    if not name and extra_args:
        name = extra_args[0]
        extra_args = extra_args[1:]

    if remote:
        remote_cfg = config.get("remote", {})
        host = remote_cfg.get("host", "")
        if not host:
            print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
            sys.exit(1)
        user = remote_cfg.get("user", "ubuntu")
        port = str(remote_cfg.get("port", 22))
        id_file = remote_cfg.get("identity_file", "")
        transport = remote_cfg.get("transport", "mosh")
        aliases = _config.get_project_aliases()
        raw_project = project or _config.get_current_project_name()
        if project and ("/" in project or "\\" in project):
            print("Error: --project name must not contain path separators", file=sys.stderr)
            sys.exit(1)
        remote_project = aliases.get(raw_project, raw_project)
        # When -p is provided, derive prefix from the target project's task_prefix
        if project:
            remote_prefix = _config._get_project_prefix_by_name(remote_project)
        else:
            remote_prefix = project_prefix
        # Prepend ~/.local/bin to PATH so `ai` is found on the remote side even
        # when the shell is a non-interactive login shell (zsh -l -c) that does
        # not source ~/.zshrc where the uv env PATH setup typically lives.
        remote_cmd = f'export PATH="$HOME/.local/bin:$PATH"; ai {engine} --is-remote --project-prefix {remote_prefix} --project {shlex.quote(remote_project)}'
        if resume:
            remote_cmd += " --resume"
        if name:
            remote_cmd += f" {shlex.quote(name)}"
        # Emit iTerm2 profile/color before mosh/ssh takes over the pane.
        # mosh blocks all \033]1337; sequences from the remote side, so this
        # is the only opportunity to set the profile and tab color.
        _r_engine_short = "c" if engine == "c" else "g"
        _r_ai_name = f"{_r_engine_short}-r-{remote_prefix}-{name or '1'}"
        _iterm2_remote_slot = _iterm2._assign_iterm2_color_slot(_r_ai_name, engine)
        _iterm2._emit_iterm2_profile_setup(_r_ai_name, engine, _r_ai_name, slot=_iterm2_remote_slot)

        _cleanup_cmd = ["ai", "internal", "cleanup-session-files", _r_ai_name]
        # vpn_host: direct-IP host used for SSH when VPN is active (bypasses Tailscale/WireGuard
        # which becomes unreachable when a split-tunneling VPN like Mullvad takes over routing).
        # Falls back to host when not set.
        vpn_host = remote_cfg.get("vpn_host", "") or host
        ssh_args = ["ssh", "-t", "-p", port]
        if id_file:
            ssh_args += ["-i", os.path.expanduser(id_file)]
        ssh_args.append(f"{user}@{vpn_host}")
        ssh_args.append(f"zsh -l -c {shlex.quote(remote_cmd)}")

        # Build mosh_args unconditionally — needed for both initial connection
        # and for reconnecting after a VPN drop while on SSH.
        # mosh always uses the primary host (Tailscale/LAN) since it only runs without VPN.
        # ConnectTimeout=10 on the SSH phase ensures mosh fails fast (error + exit) instead
        # of hanging silently for ~2 minutes when the host is unreachable (e.g. Tailscale down).
        mosh_args = ["mosh"]
        _mosh_ssh = "ssh -o ConnectTimeout=10"
        if port != "22":
            _mosh_ssh += f" -p {port}"
        if id_file:
            _mosh_ssh += f" -i {shlex.quote(os.path.expanduser(id_file))}"
        mosh_args += ["--ssh", _mosh_ssh]
        mosh_args.append(f"{user}@{host}")
        mosh_args += ["--", "zsh", "-l", "-c", remote_cmd]

        if transport == "mosh":
            _transport._ensure_vpn_watcher(config)
            import asyncio as _asyncio

            try:
                _asyncio.run(
                    _transport._run_transport_loop(
                        ssh_args, mosh_args, _cleanup_cmd, _r_ai_name, config, tailscale_host=host
                    )
                )
            finally:
                _transport._maybe_stop_vpn_watcher()
            sys.exit(0)
        else:
            # Pure SSH transport — no VPN switching.
            os.execvp("zsh", ["zsh", "-c", f"{shlex.join(ssh_args)}; {shlex.join(_cleanup_cmd)} 2>/dev/null"])

    # When running as the remote side of an --remote session, cd into the project directory
    # before creating the worktree so git commands work correctly.
    if is_remote:
        aliases = _config.get_project_aliases()
        raw_project = project or config.get("remote", {}).get("project") or _config._get_main_project_name()
        if raw_project:
            project_name = aliases.get(raw_project, raw_project)
            project_dir = _config._find_project_dir(project_name)
            if project_dir.exists():
                os.chdir(project_dir)
    elif project:
        # Local session with explicit -p PROJECT: cd to the project directory so that
        # git worktrees and Gemini chats directories resolve relative to the correct root.
        # Mirrors the is_remote path above.
        aliases = _config.get_project_aliases()
        _local_project = aliases.get(project, project)
        _local_project_dir = _config._find_project_dir(_local_project)
        if _local_project_dir.exists():
            os.chdir(_local_project_dir)

    if bare:
        if engine == "c":
            perms = [] if os.getuid() == 0 else ["--dangerously-skip-permissions"]
            os.execvp("claude", ["claude"] + perms + extra_args)
        else:
            _gcmd = shlex.split(gemini_cmd)
            os.execvp(_gcmd[0], _gcmd + ["-y", "-s" if use_sandbox else "--no-sandbox"] + extra_args)

    if resume:
        session = _session.resolve_session(prefix, name)
        if not session:
            print(f"No matching session found for '{prefix}{name or '*'}'")
            sys.exit(1)
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])

    # Registry validation: ensure all projects are registered before launching session
    if not _config.validate_registry_completeness(interactive=sys.stdin.isatty()):
        sys.exit(1)

    _session.cleanup_stale_sessions(config)
    current_project_name = _config.get_current_project_name()
    session_id, ai_name = _session.build_session_name(engine, project_prefix, name, config, is_remote=is_remote)

    # Worktree setup
    worktree_path = None
    if config.get("worktree", {}).get("enabled", True) and not no_worktree:
        worktree_path = _session.create_worktree(ai_name)
        if worktree_path:
            # Sync worktree with any changes that landed on main from other sessions
            subprocess.run(["git", "pull", "--rebase", "--autostash"], capture_output=True, cwd=worktree_path)

    d = _config.get_session_map(engine)
    uuid = d.get(ai_name)
    # For Gemini, always check the chats directory for the latest session — the
    # session map may be stale if the user exited and restarted directly via gemini CLI.
    if engine == "g":
        latest = _session._find_latest_gemini_uuid(ai_name)
        if latest and latest != uuid:
            uuid = latest
            d[ai_name] = uuid
            _config.save_session_map(d, engine)

    # Propagate iTerm2 env vars into the tmux session — tmux doesn't inherit these,
    # so _iterm2_fleet_setup inside the bash script would silently no-op without them.
    _iterm_env_flags: list[str] = []
    for _var in ("ITERM_SESSION_ID", "LC_TERMINAL", "TERM_PROGRAM"):
        if _val := os.environ.get(_var):
            _iterm_env_flags += ["-e", f"{_var}={_val}"]

    if once:
        cd_pref = f"cd {worktree_path} && " if worktree_path else ""
        if engine == "c":
            perms = "" if os.getuid() == 0 else "--dangerously-skip-permissions"
            os.execvp(
                "tmux",
                ["tmux", "new-session", "-s", session_id]
                + _iterm_env_flags
                + ["--", "zsh", "-c", f"{cd_pref}claude {perms} --name {ai_name}".strip()],
            )
        else:
            if uuid:
                os.execvp(
                    "tmux",
                    ["tmux", "new-session", "-s", session_id]
                    + _iterm_env_flags
                    + ["--", "zsh", "-c", f"{cd_pref}{gemini_cmd} -y {sandbox_flag} -r {uuid}"],
                )
            else:
                os.execvp(
                    "tmux",
                    ["tmux", "new-session", "-s", session_id]
                    + _iterm_env_flags
                    + ["--", "zsh", "-c", f"{cd_pref}{gemini_cmd} -y {sandbox_flag} -i '/resume load {ai_name}'"],
                )

    # Assign iTerm2 color slot before generating the script so both the pre-launch
    # emission and the embedded bash variables use the same slot.
    _iterm2_cfg = _iterm2._load_iterm2_config()
    _iterm2_slot = _iterm2._assign_iterm2_color_slot(ai_name, engine, project_name=current_project_name)

    _config_reload_idle_secs = int(config.get("session", {}).get("config_reload_idle_secs", 90))
    script = _session_script.get_engine_script(
        engine,
        ai_name,
        session_id,
        prefix,
        project_prefix,
        uuid,
        use_sandbox,
        str(worktree_path) if worktree_path else None,
        notify=notify,
        is_remote=is_remote,
        project_name=current_project_name,
        iterm2_slot=_iterm2_slot,
        iterm2_cfg=_iterm2_cfg,
        config_reload_idle_secs=_config_reload_idle_secs,
        gemini_cmd=gemini_cmd,
    )
    # Emit iTerm2 profile/color/title now, before tmux takes over the pane.
    # This fires in the current shell (no DCS wrapping needed) so it works
    # for new tabs, split panes, and re-attaches alike.
    _iterm2._emit_iterm2_profile_setup(
        ai_name, engine, session_id, slot=_iterm2_slot, project_name=current_project_name
    )

    # Check if session already exists (e.g., re-attaching after disconnect)
    existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True)
    if existing.returncode == 0 and sandbox:
        # Explicit sandbox flag — kill old session so it recreates with new settings
        subprocess.run(["tmux", "kill-session", "-t", session_id], capture_output=True)
        existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True)
    if existing.returncode == 0:
        # Session exists — configure for iTerm2, then attach (detach stale clients)
        _iterm2._configure_tmux_for_iterm2(session_id)
        os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])
    else:
        # New session: create detached so tmux options can be set before attaching.
        # tmux always allocates a PTY for the pane regardless of client attachment,
        # so Claude Code gets a proper PTY once we attach immediately after.
        #
        # Write the script to a temp file to avoid tmux's inline command-length limit
        # (~2 KB on macOS). The file is cleaned up by the session script itself via
        # a trap, so we don't need to unlink it here.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", prefix="ai-session-", delete=False
        ) as _tf:
            _tf.write(script)
            _script_path = _tf.name
        os.chmod(_script_path, 0o700)
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_id] + _iterm_env_flags + ["--", "zsh", _script_path],
            capture_output=True,
        )
        if result.returncode != 0:
            raw = result.stderr
            stderr = (raw.decode() if isinstance(raw, bytes) else raw).strip()
            # Mac tmux may not support `--` separator — retry without it
            result2 = subprocess.run(
                ["tmux", "new-session", "-d", "-s", session_id] + _iterm_env_flags + ["zsh", _script_path],
                capture_output=True,
            )
            if result2.returncode != 0:
                raw2 = result2.stderr
                stderr2 = (raw2.decode() if isinstance(raw2, bytes) else raw2).strip()
                os.unlink(_script_path)
                print(f"Error: failed to create tmux session '{session_id}'", file=sys.stderr)
                print(f"  (with --): {stderr}", file=sys.stderr)
                print(f"  (without --): {stderr2}", file=sys.stderr)
                sys.exit(1)
        _iterm2._configure_tmux_for_iterm2(session_id)
        os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])


# --- Click command tree ---


SESSION_CONTEXT = {
    "ignore_unknown_options": True,
    "allow_extra_args": True,
    "help_option_names": ["-h", "--help"],
}


def _session_command(engine: str):
    """Factory for the shared ``ai c`` / ``ai g`` session-launch command."""

    def _impl(
        ctx,
        name,
        resume,
        once,
        bare,
        notify,
        sandbox,
        no_worktree,
        remote,
        project,
        is_remote,
        project_prefix,
    ):
        # Startup hooks happen only when launching a new session.
        config = _config.load_config()
        trigger_background_update()
        _auto_update_if_stale(config)
        _tunnel._ensure_nats_tunnel(config)
        _do_session_launch(
            engine=engine,
            name=name,
            resume=resume,
            once=once,
            bare=bare,
            notify=notify,
            sandbox=sandbox,
            no_worktree=no_worktree,
            remote=remote,
            project=project,
            is_remote=is_remote,
            project_prefix_override=project_prefix,
            extra_args=list(ctx.args),
            config=config,
        )

    _impl.__name__ = f"cmd_session_{engine}"
    return _impl


@click.group(
    name="ai",
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("-V", "--version", is_flag=True, help="Show version and exit")
@click.pass_context
def _cli_group(ctx, version):
    """Unified AI CLI for Claude and Gemini."""
    if version:
        click.echo(_pkg_version_string())
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


def _session_options(func):
    """Decorator that adds the shared session-launch options."""
    func = click.pass_context(func)
    func = click.argument("name", required=False, default="")(func)
    func = click.option("-r", "--resume", is_flag=True, help="Resume an existing session")(func)
    func = click.option("-o", "--once", is_flag=True, help="Run once without tmux auto-resume loop")(func)
    func = click.option("-b", "--bare", is_flag=True, help="Run bare tool without tmux at all")(func)
    func = click.option("-n", "--notify", is_flag=True, help="Fire system notifications on task completion")(func)
    func = click.option("-s", "--sandbox", is_flag=True, help="Enable sandboxing (default: off)")(func)
    func = click.option("-W", "--no-worktree", is_flag=True, help="Disable git worktree isolation")(func)
    func = click.option("-R", "--remote", is_flag=True, help="Run session on remote server (configured in [remote])")(
        func
    )
    func = click.option(
        "-p",
        "--project",
        default="",
        help="Project to open on remote server (directory name, e.g. 'myproject', 'webapp')",
    )(func)
    func = click.option("--is-remote", is_flag=True, hidden=True)(func)
    func = click.option("--project-prefix", default="", hidden=True)(func)
    return func


@_cli_group.command("c", context_settings=SESSION_CONTEXT, help="Launch a Claude Code session")
@_session_options
def cmd_c(ctx, name, resume, once, bare, notify, sandbox, no_worktree, remote, project, is_remote, project_prefix):
    _session_command("c")(
        ctx, name, resume, once, bare, notify, sandbox, no_worktree, remote, project, is_remote, project_prefix
    )


@_cli_group.command("g", context_settings=SESSION_CONTEXT, help="Launch a Gemini CLI session")
@_session_options
def cmd_g(ctx, name, resume, once, bare, notify, sandbox, no_worktree, remote, project, is_remote, project_prefix):
    _session_command("g")(
        ctx, name, resume, once, bare, notify, sandbox, no_worktree, remote, project, is_remote, project_prefix
    )


@_cli_group.command("upgrade", help="Upgrade ai-cli-utils via uv tool upgrade")
def cmd_upgrade():
    print("Upgrading ai-cli-utils...", file=sys.stderr)
    os.execvp("uv", ["uv", "tool", "upgrade", "ai-cli-utils"])


@_cli_group.command("setup", help="Run interactive setup wizard")
def cmd_setup():
    from .setup import run_setup

    sys.exit(run_setup())


# --- handoff group ---


@_cli_group.group("handoff", help="Post and claim handoffs from the shared queue")
def cmd_handoff_group():
    pass


@cmd_handoff_group.command(
    "post",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    help="Post a new handoff (run ssh when --remote is passed)",
)
@click.option("-m", "--for-machine", default="", help="Machine the handoff targets (required)")
@click.option("-R", "--remote", is_flag=True, help="Post on the remote server instead of locally")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_handoff_post(for_machine, remote, args):
    _do_handoff_post(remote=remote, for_machine=for_machine, post_args=list(args))


@cmd_handoff_group.command("check", help="Check for handoffs targeted at this machine")
def cmd_handoff_check():
    _handoff.check_handoff()
    sys.exit(0)


@cmd_handoff_group.command("check-project", help="Check pending handoffs for a specific project")
@click.argument("project_name")
def cmd_handoff_check_project(project_name):
    _handoff.check_handoff_project(project_name)
    sys.exit(0)


@cmd_handoff_group.command("claim", help="Mark a handoff as claimed")
@click.argument("file_path")
def cmd_handoff_claim(file_path):
    _handoff.claim_handoff(file_path)
    sys.exit(0)


@cmd_handoff_group.command("complete", help="Mark a handoff as complete")
@click.argument("file_path")
def cmd_handoff_complete(file_path):
    _handoff.complete_handoff(file_path)
    sys.exit(0)


# --- memory group ---


@_cli_group.group("memory", help="Memory-file watchers and helpers")
def cmd_memory_group():
    pass


@cmd_memory_group.command("watch", help="Run the memory-file watcher daemon")
def cmd_memory_watch():
    from .memory import memory_watch

    sys.exit(memory_watch())


# --- quota group ---


@_cli_group.group("quota", help="Claude weekly-quota tracking and statusline helpers")
def cmd_quota_group():
    pass


@cmd_quota_group.command("watch", help="Watch quota updates (long-running)")
def cmd_quota_watch():
    from .quota import quota_watch

    sys.exit(quota_watch())


@cmd_quota_group.command("status", help="Print current quota snapshot")
def cmd_quota_status():
    from .quota import quota_status

    sys.exit(quota_status())


@cmd_quota_group.command("history", help="Print quota history")
def cmd_quota_history():
    from .quota import quota_history

    sys.exit(quota_history())


@cmd_quota_group.command("scrape", help="Scrape /usage from a fresh CC session")
def cmd_quota_scrape():
    from .quota import quota_scrape

    sys.exit(quota_scrape())


@cmd_quota_group.command("statusline-part", help="Emit the quota section of the CC statusline")
def cmd_quota_statusline_part():
    from .quota import quota_statusline_part

    sys.exit(quota_statusline_part())


@cmd_quota_group.command("sync", help="Sync quota snapshot from the remote server")
def cmd_quota_sync():
    from .quota import quota_sync_from_remote

    sys.exit(quota_sync_from_remote())


@cmd_quota_group.command("record", help="Record a single AI-usage event into the quota DB")
@click.argument("session_id")
@click.argument("machine_id")
@click.argument("model")
@click.argument("total_tokens", type=int)
@click.argument("cost_usd", type=float, required=False, default=None)
def cmd_quota_record(session_id, machine_id, model, total_tokens, cost_usd):
    from .quota import quota_record

    sys.exit(quota_record(session_id, machine_id, model, total_tokens, cost_usd))


# --- telemetry group ---


@_cli_group.group("telemetry", help="Telemetry writer daemon")
def cmd_telemetry_group():
    pass


@cmd_telemetry_group.command("writer", help="Run the telemetry writer daemon")
def cmd_telemetry_writer():
    from .telemetry import telemetry_writer

    sys.exit(telemetry_writer())


# --- gemini, spend, cc-usage, copier-update, layout, color ---


@_cli_group.command(
    "gemini",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "help_option_names": []},
    help="Run the ai-cli Gemini wrapper (pass-through to .gemini)",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_gemini(args):
    from .gemini import gemini_cli

    gemini_cli(list(args))
    sys.exit(0)


@_cli_group.group("spend", help="Usage-cost reports")
def cmd_spend_group():
    pass


@cmd_spend_group.command("gemini", help="Report Gemini CLI spend totals")
def cmd_spend_gemini_cli():
    from .spend import cmd_spend_gemini

    sys.exit(cmd_spend_gemini(_config.load_config()))


@_cli_group.group("cc-usage", help="Claude Code CLI per-call token usage")
def cmd_cc_usage_group():
    pass


@cmd_cc_usage_group.command("push", help="Scan JSONL session files and push new events")
@click.option("-d", "--dry-run", is_flag=True, help="Parse but do not push")
def cmd_cc_usage_push(dry_run):
    from .cc_usage import scan_and_push

    config = _config.load_config()
    result = scan_and_push(config=config, dry_run=dry_run)
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)
        sys.exit(1)
    if dry_run:
        print(f"Dry run: {result.new_events} new events across {result.scanned_sessions} sessions (not pushed)")
    else:
        print(
            f"Pushed {result.inserted} new events, {result.skipped} skipped "
            f"({result.scanned_sessions} sessions scanned)"
        )
    sys.exit(0)


@cmd_cc_usage_group.command("status", help="Print cc-usage cursor summary")
def cmd_cc_usage_status():
    from .cc_usage import get_cursor_summary

    summary = get_cursor_summary()
    print(f"Sessions tracked: {summary['sessions_tracked']}")
    print(f"Last push:        {summary['last_push'] or 'never'}")
    sys.exit(0)


@_cli_group.command("copier-update", help="Propagate project-template changes to downstream projects")
@click.option("-d", "--dry-run", is_flag=True, help="Show diffs without applying")
@click.option("-p", "--project", default=None, help="Only update the given project")
def cmd_copier_update(dry_run, project):
    from .copier_update import run_copier_update

    sys.exit(run_copier_update(dry_run=dry_run, project_filter=project))


@_cli_group.command(
    "layout",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "help_option_names": []},
    help="Apply a tmux pane layout",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_layout(args):
    from .layout import run_layout_command

    sys.exit(run_layout_command(list(args)))


@_cli_group.command("color", help="Reassign iTerm2 color for the current ai session")
@click.argument("color_arg")
def cmd_color(color_arg):
    _do_color(color_arg)


# --- tunnel group ---


@_cli_group.group("tunnel", help="autossh SSH tunnels to the remote server")
def cmd_tunnel_group():
    pass


@cmd_tunnel_group.command("start", help="Start an SSH tunnel")
@click.argument("local_port", type=int)
@click.argument("remote_port", type=int, required=False, default=None)
@click.option("-L", "--forward", is_flag=True, help="Create a forward tunnel (default: reverse)")
def cmd_tunnel_start(local_port, remote_port, forward):
    config = _config.load_config()
    if remote_port is None:
        remote_port = local_port
    _tunnel._cmd_tunnel_start(local_port, remote_port, forward=forward, config=config)
    sys.exit(0)


@cmd_tunnel_group.command("stop", help="Stop an SSH tunnel")
@click.argument("port", type=int)
def cmd_tunnel_stop(port):
    _tunnel._cmd_tunnel_stop(port)
    sys.exit(0)


@cmd_tunnel_group.command("status", help="Show running SSH tunnels")
def cmd_tunnel_status():
    _tunnel._cmd_tunnel_status()
    sys.exit(0)


# --- signal-watch group ---


@_cli_group.group("signal-watch", help="Handoff signal-watch Circus daemon management")
def cmd_signal_watch_group():
    pass


@cmd_signal_watch_group.command("start", help="Start signal-watch for a session")
@click.argument("project")
@click.argument("session")
def cmd_signal_watch_start(project, session):
    _process_manager._cmd_signal_watch_start(project, session)
    sys.exit(0)


@cmd_signal_watch_group.command("stop", help="Stop signal-watch for a session")
@click.argument("session")
def cmd_signal_watch_stop(session):
    _process_manager._cmd_signal_watch_stop(session)
    sys.exit(0)


@cmd_signal_watch_group.command("status", help="Show signal-watch status")
def cmd_signal_watch_status():
    _process_manager._cmd_signal_watch_status()
    sys.exit(0)


# --- cdp group ---


@_cli_group.group("cdp", help="Chrome DevTools Protocol browser management")
def cmd_cdp_group():
    pass


@cmd_cdp_group.command("start", help="Launch Chrome with a CDP port")
@click.option("-p", "--port", type=int, default=None, help="CDP port (default: [cdp].port in config)")
@click.option("-I", "--no-incognito", is_flag=True, help="Use a normal (persistent) profile")
@click.option("-t", "--tunnel", is_flag=True, help="Start an SSH tunnel alongside Chrome")
@click.option("-L", "--forward", is_flag=True, help="Use forward tunnel (default: reverse)")
def cmd_cdp_start(port, no_incognito, tunnel, forward):
    config = _config.load_config()
    _default_port = config.get("cdp", {}).get("port", 9222)
    if port is None:
        port = _default_port
    _tunnel._cmd_cdp_start(port, not no_incognito, config, tunnel=tunnel, forward=forward)
    sys.exit(0)


@cmd_cdp_group.command("stop", help="Stop a CDP-launched Chrome instance")
@click.option("-p", "--port", type=int, default=None, help="CDP port")
@click.option("-t", "--tunnel", is_flag=True, help="Also stop the SSH tunnel for this port")
def cmd_cdp_stop(port, tunnel):
    config = _config.load_config()
    _default_port = config.get("cdp", {}).get("port", 9222)
    if port is None:
        port = _default_port
    _tunnel._cmd_cdp_stop(port, tunnel=tunnel)
    sys.exit(0)


@cmd_cdp_group.command("status", help="Show CDP Chrome status")
def cmd_cdp_status():
    _tunnel._cmd_cdp_status()
    sys.exit(0)


# --- vpn-watch / ps / sync / reconnect / update / deploy / attach / ls ---


@_cli_group.command("vpn-watch", help="Run the VPN-watch daemon")
def cmd_vpn_watch():
    from .vpn_watch import run_vpn_watch

    run_vpn_watch(_config.load_config())
    sys.exit(0)


@_cli_group.command(
    "ps",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "help_option_names": []},
    help="Process hygiene — show/kill stale ai-cli processes",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_ps(args):
    from .process_hygiene import cmd_ps as _cmd_ps

    sys.exit(_cmd_ps(list(args), _config.load_config()))


@_cli_group.command(
    "sync",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "help_option_names": []},
    help="Sync CC session data (JSONL + memory files) across machines",
)
@click.argument("action", required=False, default="")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_sync(action, args):
    if not action:
        print(
            "Usage: ai sync [push|pull|conflicts|watch|cleanup|repair-worktree] "
            "[-m|--memories-only] [-n|--dry-run] [-v|--verbose] [-f|--force]"
        )
        sys.exit(1)
    from .sync import (
        sync_push,
        sync_pull,
        sync_conflicts,
        sync_watch,
        repair_worktree_cc_dir,
        clean_worktree_cc_dirs,
        _cc_projects_dir,
        get_local_prefix,
    )

    flags = list(args)
    if action == "push":
        sys.exit(sync_push(flags))
    elif action == "pull":
        sys.exit(sync_pull(flags))
    elif action == "conflicts":
        sys.exit(sync_conflicts(flags))
    elif action == "watch":
        sys.exit(sync_watch(flags))
    elif action == "repair-worktree":
        positional = [a for a in flags if not a.startswith("-")]
        if len(positional) < 2:
            print(
                "Usage: ai sync repair-worktree <project> <worktree> [-n|--dry-run] [-v|--verbose]\n"
                "Example: ai sync repair-worktree myproject wt-1\n"
                "Copies all conversations from the main project CC dir into the worktree CC dir\n"
                "so they are accessible from the worktree session.",
                file=sys.stderr,
            )
            sys.exit(1)
        project_name = positional[0]
        wt_name = positional[1]
        dry_run = "-n" in flags or "--dry-run" in flags
        verbose = "-v" in flags or "--verbose" in flags
        copied = repair_worktree_cc_dir(
            project_name=project_name,
            wt_name=wt_name,
            cc_projects_dir=_cc_projects_dir(),
            local_prefix=get_local_prefix(),
            dry_run=dry_run,
            verbose=verbose,
        )
        sys.exit(0 if copied >= 0 else 1)
    elif action == "cleanup":
        dry_run = "-n" in flags or "--dry-run" in flags
        verbose = "-v" in flags or "--verbose" in flags
        removed_jsonl, removed_lock = clean_worktree_cc_dirs(
            _cc_projects_dir(),
            get_local_prefix(),
            dry_run=dry_run,
            verbose=verbose,
        )
        verb = "Would remove" if dry_run else "Removed"
        print(f"{verb}: {removed_jsonl} stale JSONL copies, {removed_lock} orphan lock dirs")
        sys.exit(0)
    else:
        print(
            f"Unknown sync action: {action}. Use push, pull, conflicts, watch, cleanup, or repair-worktree.",
            file=sys.stderr,
        )
        sys.exit(1)


@_cli_group.command("reconnect", help="Print reconnect commands for remote tmux sessions")
@click.argument("sessions", nargs=-1)
def cmd_reconnect(sessions):
    config = _config.load_config()
    requested = [int(x) for x in sessions if x.isdigit()] if sessions else None
    _do_reconnect(requested, config)


@_cli_group.command("update", help="Update ai-cli-utils from the source tree (git pull + uv tool install)")
@click.option("-f", "--force", is_flag=True, help="Pass --reinstall to uv tool install")
def cmd_update(force):
    config = _config.load_config()
    _do_update_or_deploy(force_reinstall=force, config=config)


@_cli_group.command("deploy", help="Alias for update (historical)")
@click.option("-f", "--force", is_flag=True, help="Pass --reinstall to uv tool install")
def cmd_deploy(force):
    config = _config.load_config()
    _do_update_or_deploy(force_reinstall=force, config=config)


@_cli_group.command("attach", help="Attach to a tmux session by name")
@click.argument("session_name")
def cmd_attach(session_name):
    _do_attach(session_name)


@_cli_group.command("ls", help="List tmux sessions (fzf picker when available)")
@click.option("-a", "--all", "show_all", is_flag=True, help="Show all tmux sessions (not just ai-cli ones)")
def cmd_ls(show_all):
    _do_ls(show_all)


# --- Public entry points ---


def cli() -> None:
    """Top-level CLI entry point.

    Handles the ``ai internal`` machine-to-machine fast path directly (sub-
    millisecond startup for bash-hook callers) and delegates everything else
    to the Click command group. Click usage errors exit with code 1 so the
    exit contract matches the argparse/sys.argv dispatcher it replaces.
    """
    if len(sys.argv) > 1 and sys.argv[1] == "internal":
        _handle_internal(sys.argv[2:])
        return
    try:
        _cli_group(standalone_mode=False)
    except click.exceptions.UsageError as exc:
        exc.show(file=sys.stderr)
        sys.exit(1)
    except click.exceptions.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except click.exceptions.Abort:
        sys.exit(1)
    sys.exit(0)


def main() -> None:
    """uv-tool console-script entry. Alias for :func:`cli`."""
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
