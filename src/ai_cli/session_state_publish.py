"""Publish a durable registry snapshot with Git plumbing and no checkout changes."""

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import portalocker

from . import session_registry as registry
from . import session_run_state as state

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishResult:
    published: bool
    reason: str


def render(doc: dict, host: str) -> str:
    """Render TOML using JSON-compatible quoted strings, including quoted table keys."""

    def quote(value):
        return json.dumps(value, ensure_ascii=False)

    lines = [
        f"host = {quote(host)}",
        f"boot_id = {quote(registry.current_boot_id() or '')}",
        f"published_at = {quote(registry._now())}",
    ]
    for record in sorted(doc["sessions"], key=lambda r: r["name"]):
        if record.get("kind") != "local":
            continue
        fields = {
            "launch": record.get("boot_recipe", {}).get("argv", record.get("relaunch_argv", [])),
            "cwd": record.get("cwd") or "",
            "run_state": state.run_state(record),
            "cause": state.cause(record) if state.run_state(record) == "exited" else "",
            "changed_at": record.get("changed_at") or record.get("launched_at") or "",
            "relaunch": record.get("relaunch", "auto"),
        }
        lines += ["", f"[sessions.{quote(record['name'])}]"]
        lines += [f"{key} = {quote(value)}" for key, value in fields.items()]
    return "\n".join(lines) + "\n"


class Publisher:
    """One bounded attempt, retrying once only when the remote ref moved."""

    def __init__(self, cfg: dict):
        self.repo = Path(cfg["repo"]).expanduser()
        self.remote = cfg["remote"]
        self.ref = cfg["ref_prefix"] + cfg["host"]
        self.read_ref = "refs/session-state/" + cfg["host"]
        self.host = cfg["host"]
        self.deadline = time.monotonic() + float(cfg["debounce_seconds"]) / 4

    def git(self, *args: str, stdin: str | None = None) -> str:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("publication deadline exceeded")
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=remaining,
        )
        if result.returncode:
            # Git stderr can contain credential-bearing URLs. Never forward it to logs.
            raise RuntimeError(f"git {args[0]} failed")
        return result.stdout if args[0] == "ls-tree" else result.stdout.strip()

    def tip(self) -> str:
        listed = self.git("ls-remote", "--", self.remote, self.ref)
        if not listed:
            return ""
        self.git("fetch", "--quiet", "--no-tags", "--", self.remote, f"+{self.ref}:{self.read_ref}")
        return self.git("rev-parse", self.read_ref)

    def tree(self, blobs: dict[str, str]) -> str:
        entries = []
        directories = {}
        for path, oid in blobs.items():
            name, sep, rest = path.partition("/")
            if sep:
                directories.setdefault(name, {})[rest] = oid
            else:
                entries.append(f"100644 blob {oid}\t{name}\0")
        for name, children in directories.items():
            entries.append(f"040000 tree {self.tree(children)}\t{name}\0")
        return self.git("mktree", "-z", stdin="".join(sorted(entries)))

    def push(self, text: str) -> None:
        parent = self.tip()
        for attempt in range(2):
            blobs = {}
            if parent:
                for entry in self.git("ls-tree", "-r", "-z", parent).split("\0"):
                    if entry:
                        meta, path = entry.split("\t", 1)
                        mode, kind, oid = meta.split()
                        if mode != "100644" or kind != "blob":
                            raise ValueError("unexpected publication tree entry")
                        blobs[path] = oid
            blobs[f"sessions/{self.host}.toml"] = self.git("hash-object", "-w", "--stdin", stdin=text)
            tree = self.tree(blobs)
            args = ["commit-tree", tree]
            if parent:
                args += ["-p", parent]
            commit = self.git(*args, "-m", "Update session run state")
            try:
                self.git("push", "--quiet", "--", self.remote, f"{commit}:{self.ref}")
            except RuntimeError:
                moved = self.tip()
                if attempt == 0 and moved != parent:
                    parent = moved
                    continue
                raise
            self.git("update-ref", self.read_ref, commit)
            return


def publish(*, now: bool = False, tick: bool = False) -> PublishResult:
    """Snapshot and acknowledge only the revision pushed; writers never call this."""
    del tick  # Explicit entry point for installed timers; the same durable rules apply.
    try:
        cfg = state.settings()["publish"]
        if not cfg["repo"]:
            return _result(False, "publishing disabled: no repo configured")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", cfg["host"]):
            return _result(False, "publication requires a valid host")
        if float(cfg["debounce_seconds"]) <= 0:
            return _result(False, "debounce_seconds must be positive")
        path = registry.registry_path().with_name("session-publish.lock")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with portalocker.Lock(path, mode="a", timeout=0):
            state.reconcile_safety()
            doc = registry.load_registry()
            revision = doc.get("dirty_revision", 0)
            if revision <= doc.get("published_revision", 0):
                return _result(False, "no dirty session state")
            moment = time.time()
            if not now and moment - doc.get("publish_attempt_at", 0) < float(cfg["debounce_seconds"]):
                return _result(False, "publication debounced")

            def reserve(current):
                current["publish_attempt_at"] = moment
                return True

            registry._mutate(reserve, dirty=False, timeout=2)
            Publisher(cfg).push(render(doc, cfg["host"]))

            def acknowledge(current):
                current["published_revision"] = max(current.get("published_revision", 0), revision)
                return True

            registry._mutate(acknowledge, dirty=False, timeout=2)
            return _result(True, "session state published")
    except Exception as exc:
        # Only exception types are safe: runner errors can include credentials.
        detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
        return _result(False, f"session publication failed ({detail})")


def _result(published: bool, reason: str) -> PublishResult:
    LOGGER.info(reason) if published else LOGGER.warning(reason)
    return PublishResult(published, reason)
