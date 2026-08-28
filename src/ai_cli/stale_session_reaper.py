"""Fail-closed evaluation of stale tmux sessions.

This module deliberately has no launch-path imports.  It can inspect and, only in
explicit ``reap`` mode, terminate a session after two complete corroborated
snapshots.  Any unavailable input is evidence to preserve the session.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import math
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeGuard

import portalocker
import psutil

from .process_probe import ProcessIdentity, ProcessProbe, probe_for

LOGGER = logging.getLogger(__name__)
HEARTBEAT_VERSION = 1
DEFAULT_MODE = "observe"
DEFAULT_STALE_AFTER_SECONDS = 600
_TMUX_FORMAT = "#{session_id}\t#{session_name}\t#{@ai_cli_session_generation}\t#{pane_id}\t#{pane_pid}"


@dataclass(frozen=True)
class ReaperConfig:
    """Validated reaper configuration, or no configuration when invalid."""

    mode: str
    stale_after_seconds: int


@dataclass(frozen=True)
class Pane:
    """One pane leader from a captured tmux session snapshot."""

    pane_id: str
    pid: int


@dataclass(frozen=True)
class SessionCandidate:
    """A token-marked tmux session and its complete current pane mapping."""

    session_id: str
    session_name: str
    generation_token: str
    panes: tuple[Pane, ...]


@dataclass(frozen=True)
class ProcessObservation:
    """An ended pane process observation used for exact revalidation."""

    pane: Pane
    state: str
    identity: ProcessIdentity | None


class TmuxAdapter(Protocol):
    """Small tmux seam used by the evaluator and its controlled integration tests."""

    def sessions(self) -> Sequence[SessionCandidate]:
        """Return token-marked sessions with their complete pane mappings."""
        ...

    def kill_session(self, session_id: str) -> bool:
        """Kill one opaque tmux session ID, never a user-facing name."""
        ...


class SubprocessTmuxAdapter:
    """Tmux adapter using delimiter-safe ``list-panes`` output."""

    def sessions(self) -> Sequence[SessionCandidate]:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", _TMUX_FORMAT],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("tmux_read_failed")
        grouped: dict[tuple[str, str, str], list[Pane]] = {}
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 5:
                raise ValueError("tmux_read_failed")
            session_id, session_name, generation_token, pane_id, raw_pid = parts
            # The tmux option is the sole managed-session marker.  Tokenless
            # sessions are intentionally ignored rather than classified by name.
            if not generation_token:
                continue
            if not all((session_id, session_name, pane_id)):
                raise ValueError("generation_mismatch")
            try:
                pid = int(raw_pid)
            except ValueError as exc:
                raise ValueError("pid_invalid") from exc
            if pid <= 0:
                raise ValueError("pid_invalid")
            grouped.setdefault((session_id, session_name, generation_token), []).append(Pane(pane_id, pid))
        return tuple(
            SessionCandidate(session_id, session_name, generation_token, tuple(panes))
            for (session_id, session_name, generation_token), panes in grouped.items()
        )

    def kill_session(self, session_id: str) -> bool:
        result = subprocess.run(["tmux", "kill-session", "-t", session_id], capture_output=True, text=True, check=False)
        return result.returncode == 0


def encode_ledger_component(value: str) -> str:
    """Return a reversible filename-safe representation of ``value``."""
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def heartbeat_path(state_home: Path, session_name: str, generation_token: str) -> Path:
    """Path for exactly one session-name and generation-token heartbeat record."""
    return (
        state_home
        / "session-heartbeats"
        / (f"{encode_ledger_component(session_name)}-{encode_ledger_component(generation_token)}.json")
    )


def generation_lease_path(state_home: Path, session_name: str, generation_token: str) -> Path:
    """Path for the generation-bound supervisor/reaper exclusion lease."""
    return (
        state_home
        / "session-leases"
        / (f"{encode_ledger_component(session_name)}-{encode_ledger_component(generation_token)}.lock")
    )


def current_boot_generation() -> str | None:
    """Return a stable current-boot identity, or ``None`` when unavailable."""
    try:
        boot_time = psutil.boot_time()
    except (OSError, psutil.Error):
        return None
    if not isinstance(boot_time, (int, float)) or isinstance(boot_time, bool) or not math.isfinite(boot_time):
        return None
    return f"psutil:{boot_time:.6f}"


def write_heartbeat(
    state_home: Path,
    session_name: str,
    generation_token: str,
    *,
    boot_generation: str | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> bool:
    """Atomically write a local heartbeat record for one exact generation.

    A different-generation record is never replaced.  Callers must have already
    verified their lifetime lease and tmux generation marker.
    """
    if (
        not isinstance(session_name, str)
        or not session_name
        or not isinstance(generation_token, str)
        or not generation_token
    ):
        return False
    boot = boot_generation if boot_generation is not None else current_boot_generation()
    try:
        monotonic_recorded_at = monotonic_clock()
        recorded_at = wall_clock()
    except Exception:
        return False
    if not isinstance(boot, str) or not boot or not _finite_number(monotonic_recorded_at):
        return False
    if not _finite_number(recorded_at):
        return False
    path = heartbeat_path(state_home, session_name, generation_token)
    record = {
        "version": HEARTBEAT_VERSION,
        "session_name": session_name,
        "generation_token": generation_token,
        "boot_generation": boot,
        "monotonic_recorded_at": monotonic_recorded_at,
        "recorded_at": int(recorded_at),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = _read_json(path)
            if not isinstance(existing, dict) or existing.get("generation_token") != generation_token:
                return False
        fd, temporary_name = tempfile.mkstemp(prefix=".heartbeat-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(record, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            Path(temporary_name).replace(path)
        except Exception:
            with contextlib.suppress(OSError):
                Path(temporary_name).unlink()
            raise
    except Exception:
        return False
    return True


def remove_heartbeat(state_home: Path, session_name: str, generation_token: str) -> bool:
    """Remove a record only after proving it still belongs to ``generation_token``."""
    path = heartbeat_path(state_home, session_name, generation_token)
    try:
        record = _read_json(path)
        if not isinstance(record, dict) or record.get("generation_token") != generation_token:
            return False
        path.unlink()
    except (OSError, ValueError, TypeError):
        return False
    return True


def read_reaper_config(config: Mapping[str, object]) -> ReaperConfig | None:
    """Validate reaper settings; malformed explicit configuration disables action."""
    raw = config.get("stale_session_reaper")
    if raw is None:
        return ReaperConfig(DEFAULT_MODE, DEFAULT_STALE_AFTER_SECONDS)
    if not isinstance(raw, Mapping):
        return None
    mode = raw.get("mode", DEFAULT_MODE)
    threshold = raw.get("stale_after_seconds", DEFAULT_STALE_AFTER_SECONDS)
    if mode not in {"observe", "reap"}:
        return None
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
        return None
    return ReaperConfig(mode, threshold)


class StaleSessionReaper:
    """Evaluate managed sessions using two snapshots and a generation lease fence."""

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        state_home: Path,
        tmux: TmuxAdapter | None = None,
        process_probe: ProcessProbe | None = None,
        boot_generation: Callable[[], str | None] = current_boot_generation,
        monotonic_clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self._settings = read_reaper_config(config)
        self._state_home = state_home
        self._tmux = tmux or SubprocessTmuxAdapter()
        self._probe = process_probe or probe_for()
        self._boot_generation = boot_generation
        self._monotonic_clock = monotonic_clock
        self._logger = logger

    def evaluate_once(self) -> list[str]:
        """Evaluate every current candidate, returning IDs actually killed.

        Candidate failures are deliberately local: they cannot invalidate a prior
        authorized kill or make a later candidate eligible.
        """
        if self._settings is None:
            self._log("configuration_invalid")
            return []
        try:
            candidates = self._tmux.sessions()
        except Exception:
            self._log("tmux_read_failed")
            return []
        killed: list[str] = []
        for candidate in candidates:
            try:
                if self._evaluate_candidate(candidate):
                    killed.append(candidate.session_id)
            except Exception:
                self._log("candidate_error", candidate)
        return killed

    def _evaluate_candidate(self, candidate: SessionCandidate) -> bool:
        if not _valid_candidate(candidate):
            self._log("tmux_read_failed", candidate)
            return False
        initial = self._ended_snapshot(candidate)
        if initial is None or not self._heartbeat_is_stale(candidate):
            return False
        assert self._settings is not None
        if self._settings.mode == "observe":
            self._log("mode_observe", candidate)
            return False
        with self._exclusive_lease(candidate) as held:
            if not held:
                self._log("lease_unavailable", candidate)
                return False
            refreshed = self._find_exact_current_candidate(candidate)
            if refreshed is None:
                self._log("revalidation_failed", candidate)
                return False
            final = self._ended_snapshot(refreshed)
            if final is None or not self._heartbeat_is_stale(refreshed) or not _same_snapshot(initial, final):
                self._log("revalidation_failed", candidate)
                return False
            if not self._tmux.kill_session(candidate.session_id):
                self._log("revalidation_failed", candidate)
                return False
        self._remove_after_confirmed_kill(candidate)
        return True

    def _find_exact_current_candidate(self, original: SessionCandidate) -> SessionCandidate | None:
        current = self._tmux.sessions()
        matches = [candidate for candidate in current if candidate.session_id == original.session_id]
        if len(matches) != 1:
            return None
        candidate = matches[0]
        if candidate.session_name != original.session_name or candidate.generation_token != original.generation_token:
            return None
        return candidate

    def _ended_snapshot(self, candidate: SessionCandidate) -> tuple[ProcessObservation, ...] | None:
        observations: list[ProcessObservation] = []
        for pane in candidate.panes:
            if pane.pid <= 0:
                self._log("pid_invalid", candidate)
                return None
            try:
                if not self._probe.is_present(pane.pid):
                    observations.append(ProcessObservation(pane, "GONE", None))
                    continue
                state = self._probe.state(pane.pid)
                if state not in self._probe.ended_states:
                    self._log("process_live" if state is not None else "process_unknown", candidate)
                    return None
                identity = self._probe.capture_identity(pane.pid)
            except Exception:
                self._log("process_unknown", candidate)
                return None
            if identity is None:
                self._log("process_unknown", candidate)
                return None
            observations.append(ProcessObservation(pane, state, identity))
        return tuple(observations)

    def _heartbeat_is_stale(self, candidate: SessionCandidate) -> bool:
        try:
            record = _read_json(heartbeat_path(self._state_home, candidate.session_name, candidate.generation_token))
            boot = self._boot_generation()
            now = self._monotonic_clock()
        except Exception:
            self._log("heartbeat_missing", candidate)
            return False
        if not isinstance(record, dict) or not isinstance(boot, str) or not boot or not _finite_number(now):
            self._log("heartbeat_invalid", candidate)
            return False
        if record.get("version") != HEARTBEAT_VERSION:
            self._log("heartbeat_invalid", candidate)
            return False
        if (
            record.get("session_name") != candidate.session_name
            or record.get("generation_token") != candidate.generation_token
        ):
            self._log("generation_mismatch", candidate)
            return False
        if record.get("boot_generation") != boot:
            self._log("heartbeat_boot_mismatch", candidate)
            return False
        stamp = record.get("monotonic_recorded_at")
        if not _finite_number(stamp) or stamp > now:
            self._log("heartbeat_invalid", candidate)
            return False
        assert self._settings is not None
        if now - stamp <= self._settings.stale_after_seconds:
            self._log("heartbeat_not_stale", candidate)
            return False
        return True

    @contextlib.contextmanager
    def _exclusive_lease(self, candidate: SessionCandidate) -> Iterator[bool]:
        path = generation_lease_path(self._state_home, candidate.session_name, candidate.generation_token)
        lock: portalocker.Lock | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = portalocker.Lock(str(path), mode="a+", timeout=0, flags=portalocker.LOCK_EX | portalocker.LOCK_NB)
            lock.acquire()
        except Exception:
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(Exception):
                lock.release()

    def _remove_after_confirmed_kill(self, candidate: SessionCandidate) -> None:
        try:
            if any(session.session_id == candidate.session_id for session in self._tmux.sessions()):
                return
            remove_heartbeat(self._state_home, candidate.session_name, candidate.generation_token)
        except Exception:
            self._log("tmux_read_failed", candidate)

    def _log(self, reason: str, candidate: SessionCandidate | None = None) -> None:
        if candidate is None:
            self._logger.warning("stale_session_reaper reason=%s", reason)
        else:
            self._logger.warning(
                "stale_session_reaper reason=%s session_id=%s session_name=%s generation=%s",
                reason,
                candidate.session_id,
                candidate.session_name,
                candidate.generation_token,
            )


def _valid_candidate(candidate: SessionCandidate) -> bool:
    if not all((candidate.session_id, candidate.session_name, candidate.generation_token, candidate.panes)):
        return False
    pane_ids = [pane.pane_id for pane in candidate.panes]
    return all(pane_ids) and len(pane_ids) == len(set(pane_ids))


def _same_snapshot(initial: tuple[ProcessObservation, ...], final: tuple[ProcessObservation, ...]) -> bool:
    if len(initial) != len(final):
        return False
    for before, after in zip(initial, final, strict=True):
        if before.pane != after.pane or before.state != after.state:
            return False
        if before.state == "GONE":
            if after.identity is not None:
                return False
        elif before.identity is None or after.identity is None or before.identity != after.identity:
            return False
    return True


def _finite_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _read_json(path: Path) -> object:
    with path.open(encoding="utf-8") as source:
        return json.load(source)
