"""The session-name registry: the one database of available and held names.

A session name is unique across every machine that shares a registry, so the registry
is one git ref that every machine writes with a fast-forward push. Its tree is the
database:

``names/<token>.json``
    one file per held token: a session's name, a machine's spare, or the last token of
    a label whose name was dropped. A token is held exactly while its file exists.
``available/names.txt`` and ``available/generation.json``
    the generated list and how it was built, including the policy snapshot every
    allocation applies.
``available/manual/<name>.json``
    one file per name added by hand.
``migrations/<old-title>.json``
    one file per rename.

The ref lives in the first sibling repository under the projects directory that ships
``config/session-names.toml``, a file that names the ref and the remote, so this
package hardcodes neither. Every write is one commit built with plumbing on a freshly
fetched tip, never touching that repository's working tree, index or ``HEAD``, and
pushed without force. A push refused as a non-fast-forward means another machine
wrote first: the writer fetches the new tip and runs its whole guard again against it
(:meth:`Registry.transact`).

An install with no such repository keeps the same tree as plain files in a local
directory, guarded by a file lock, seeded with the list this package ships.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

import portalocker

from . import __version__, name_list
from . import config as _config
from .git_repair import _GIT_TARGETING_VARS

POLICY_FILENAME = "session-names.toml"
POLICY_MARKER = Path("config") / POLICY_FILENAME
FLEET_PROJECTS_MARKER = _config.FLEET_REGISTRY_MARKER
PRIVATE_REF = "refs/session-names/registry"

NAMES_DIR = "names"
NAMES_TXT = "available/names.txt"
GENERATION_JSON = "available/generation.json"
MANUAL_DIR = "available/manual"
MIGRATIONS_DIR = "migrations"

ROW_SCHEMA = 1
ROW_KINDS = ("session", "spare", "label")
ROW_FIELDS = (
    "schema",
    "kind",
    "name",
    "token",
    "title",
    "repo",
    "repo_alias",
    "host_alias",
    "label",
    "machine",
    "client",
    "source",
    "tier",
    "reserved_at",
    "activated_at",
    "worktree",
    "state",
    "recovery",
    "writer",
)
SPARE_ACTIVATION_FIELDS = frozenset(
    {"kind", "title", "repo", "repo_alias", "label", "client", "activated_at", "worktree"}
)
DEFAULT_TIMEOUT_S = 15
DEFAULT_ATTEMPTS = 5

_TOKEN_RE = re.compile(r"[a-z]+")
# A dropped label's last token may carry digits, but never only digits: that is an index.
_LABEL_TOKEN_RE = re.compile(r"[a-z0-9]*[a-z][a-z0-9]*")
_REJECTED_PUSH = re.compile(r"\[rejected\]|non-fast-forward|fetch first|stale info|\(cannot lock ref", re.IGNORECASE)
_MISSING_REMOTE_REF = re.compile(r"couldn't find remote ref", re.IGNORECASE)

# Every key a published policy snapshot may carry, and the keys each table may carry.
# A snapshot with anything else was written by a newer version, and guessing what an
# unknown key means is how two hosts would allocate under different rules.
_POLICY_TABLES: dict[str, frozenset[str] | None] = {
    "schema": None,
    "registry_ref": None,
    "registry_remote": None,
    "publish_timeout_s": None,
    "publish_attempts": None,
    "pick_floor": None,
    "low_water": None,
    "offline_spares": None,
    "denylist": None,
    "limits": frozenset({"min_name_length", "max_name_length"}),
    "source": frozenset({"url", "digest", "members"}),
    "confusable": frozenset(
        {"words_url", "words_digest", "words_member", "max_edit_distance", "extra_words", "keep_names"}
    ),
    "repos": None,
    "registry_backend": None,
    "registry_dolt_ref": None,
}
POLICY_SCHEMA = 1
# The policy schema a fleet on the Dolt database publishes; the git-tree store refuses it.
DOLT_POLICY_SCHEMA = 2


class RegistryError(RuntimeError):
    """The registry could not be used for this operation."""


class RegistryUnreachableError(RegistryError):
    """A fetch or push did not complete within the budget: offline, auth, or the remote is down."""


class RegistryContendedError(RegistryError):
    """Every attempt lost the race to another writer."""


class WriteRefusedError(RegistryError):
    """The operation's guard failed against the current tip; nothing was written."""


class PolicyUnknownError(RegistryError):
    """The published policy was written by a newer version of this package."""


class NoNameListError(RegistryError):
    """The registry has no usable available-name list."""


class NameStatusError(RegistryError):
    """The registry's name-status data is missing or unreadable, so no name counts as approved."""


NAME_STATUSES = ("unreviewed", "rejected", "approved")
# The values the earlier approval-status record used, as an import reads them.
LEGACY_STATUSES = {"excluded": "rejected", "needs-review": "unreviewed"}
SCOPE_KINDS = ("repo", "host", "session")
# A spare the cutover import holds for an old offline client; ordinary commands never release it.
QUARANTINED = "cutover-quarantined"
VERDICT_VALUES = ("approved", "rejected")


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def writer() -> str:
    return f"ai-cli-utils {__version__}"


def encode_json(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


# --- rows -----------------------------------------------------------------------------


def _blank_row(kind: str, token: str) -> dict:
    row: dict = dict.fromkeys(ROW_FIELDS)
    row.update({"schema": ROW_SCHEMA, "kind": kind, "token": token, "state": "active", "writer": writer()})
    return row


def session_row(
    *,
    name: str,
    title: str,
    repo: str,
    repo_alias: str,
    host_alias: str,
    label: str | None,
    machine: str,
    client: str,
    source: str,
    tier: str | None,
    worktree: str | None,
    now: str | None = None,
) -> dict:
    stamp = now or utc_now()
    row = _blank_row("session", name)
    row.update(
        {
            "name": name,
            "title": title,
            "repo": repo,
            "repo_alias": repo_alias,
            "host_alias": host_alias,
            "label": label,
            "machine": machine,
            "client": client,
            "source": source,
            "tier": tier,
            "reserved_at": stamp,
            "activated_at": stamp,
            "worktree": worktree,
        }
    )
    return row


def spare_row(*, name: str, host_alias: str, machine: str, now: str | None = None) -> dict:
    row = _blank_row("spare", name)
    row.update(
        {"name": name, "host_alias": host_alias, "machine": machine, "source": "spare", "reserved_at": now or utc_now()}
    )
    return row


def label_row(
    *,
    token: str,
    title: str,
    repo: str,
    repo_alias: str,
    host_alias: str,
    label: str,
    machine: str,
    client: str,
    worktree: str | None,
    now: str | None = None,
) -> dict:
    stamp = now or utc_now()
    row = _blank_row("label", token)
    row.update(
        {
            "title": title,
            "repo": repo,
            "repo_alias": repo_alias,
            "host_alias": host_alias,
            "label": label,
            "machine": machine,
            "client": client,
            # The label was typed by the person launching, which is what "custom" records.
            "source": "custom",
            "reserved_at": stamp,
            "activated_at": stamp,
            "worktree": worktree,
        }
    )
    return row


def activate_spare(
    spare: dict,
    *,
    title: str,
    repo: str,
    repo_alias: str,
    label: str | None,
    client: str,
    worktree: str | None,
    now: str | None = None,
) -> dict:
    """The row a spare becomes when a session takes it: only the activation fields change."""
    row = dict(spare)
    row.update(
        {
            "kind": "session",
            "title": title,
            "repo": repo,
            "repo_alias": repo_alias,
            "label": label,
            "client": client,
            "activated_at": now or utc_now(),
            "worktree": worktree,
        }
    )
    return row


_NON_NULL = {
    "session": ("name", "title", "repo", "repo_alias", "host_alias", "machine", "client", "source", "activated_at"),
    "spare": ("name", "host_alias", "machine", "source"),
    "label": ("title", "repo", "repo_alias", "host_alias", "label", "machine", "client", "activated_at"),
}
_NULL = {
    "session": (),
    "spare": ("title", "repo", "repo_alias", "label", "client", "tier", "activated_at", "worktree", "recovery"),
    "label": ("name", "tier"),
}


def _is_absolute(value: str) -> bool:
    return value.startswith("~") or PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def row_problem(row: dict) -> str | None:
    """Why ``row`` breaks the per-kind invariants, or None when it holds them."""
    kind = row.get("kind")
    if kind not in ROW_KINDS:
        return f"kind {kind!r} is not one of {', '.join(ROW_KINDS)}"
    if set(row) != set(ROW_FIELDS):
        return f"fields {sorted(set(row) ^ set(ROW_FIELDS))} are missing or unknown"
    if row["schema"] != ROW_SCHEMA:
        return f"schema {row['schema']!r} is not {ROW_SCHEMA}"
    token = row.get("token")
    token_re = _LABEL_TOKEN_RE if kind == "label" else _TOKEN_RE
    if not isinstance(token, str) or not token_re.fullmatch(token):
        return f"token {token!r} is not a valid {kind} token"
    for key in _NON_NULL[kind]:
        if row.get(key) is None:
            return f"a {kind} row needs {key}"
    for key in _NULL[kind]:
        if row.get(key) is not None:
            return f"a {kind} row must have no {key}"
    if kind in ("session", "spare") and row["name"] != token:
        return f"name {row['name']!r} differs from token {token!r}"
    if kind == "spare" and row["source"] != "spare":
        return "a spare row's source is spare"
    if row["state"] not in ("active", "migration-incomplete"):
        return f"state {row['state']!r} is not active or migration-incomplete"
    if (row["state"] == "migration-incomplete") != (row["recovery"] is not None):
        return "recovery is set exactly while the state is migration-incomplete"
    if not row.get("reserved_at"):
        return "reserved_at is required"
    for key, value in row.items():
        if isinstance(value, str) and _is_absolute(value):
            return f"{key} holds an absolute path; the registry never stores one"
    return None


def checked_row(row: dict) -> bytes:
    problem = row_problem(row)
    if problem:
        raise WriteRefusedError(f"refusing to write the row for {row.get('token')!r}: {problem}")
    return encode_json(row)


# --- policy ---------------------------------------------------------------------------


def unknown_policy_keys(policy: dict) -> list[str]:
    """Keys of ``policy`` this version does not know, as dotted paths."""
    unknown: list[str] = []
    for key, value in policy.items():
        if key not in _POLICY_TABLES:
            unknown.append(key)
            continue
        allowed = _POLICY_TABLES[key]
        if allowed is not None and isinstance(value, dict):
            unknown += [f"{key}.{inner}" for inner in value if inner not in allowed]
        if key == "repos" and isinstance(value, dict):
            for alias, table in value.items():
                if isinstance(table, dict):
                    unknown += [f"repos.{alias}.{inner}" for inner in table if inner != "letters"]
    return unknown


def require_known_policy(policy: dict, schemas: tuple[int, ...] = (POLICY_SCHEMA,)) -> dict:
    """``policy`` itself, or :class:`PolicyUnknownError` naming what this version cannot apply.

    ``schemas`` are the policy schemas the registry backend in use applies: a schema
    another backend publishes refuses here exactly as an unknown one does.
    """
    schema = policy.get("schema", POLICY_SCHEMA)
    if schema not in schemas:
        raise PolicyUnknownError(
            f"the published naming policy has schema {schema!r}, which this version does not know: update ai-cli-utils"
        )
    unknown = unknown_policy_keys(policy)
    if unknown:
        raise PolicyUnknownError(
            f"the published naming policy has keys this version does not know ({', '.join(unknown)}): "
            "update ai-cli-utils"
        )
    return policy


# --- snapshots and changes -------------------------------------------------------------


@dataclass
class Snapshot:
    """The registry's content at one tip."""

    tip: str | None
    rows: dict[str, dict] = field(default_factory=dict)
    blobs: dict[str, str] = field(default_factory=dict)
    manual: dict[str, dict] = field(default_factory=dict)
    migrations: dict[str, dict] = field(default_factory=dict)
    names: list[str] | None = None
    generation: dict | None = None
    fetched: bool = True
    # The claim value each hold carries; only the database backends record one.
    claimants: dict[str, str] = field(default_factory=dict)
    # Each pool name's general status and each scoped verdict; None on the git-tree store,
    # which has no status model.
    statuses: dict[str, dict] | None = None
    verdicts: dict[tuple[str, str, str], dict] | None = None
    meta: dict[str, str] = field(default_factory=dict)
    policy_schemas: tuple[int, ...] = (POLICY_SCHEMA,)

    @property
    def policy(self) -> dict:
        """The published policy every allocation applies; refuses when none is usable."""
        if self.generation is None or not isinstance(self.generation.get("policy"), dict):
            raise NoNameListError(f"the registry has no {GENERATION_JSON}; run `ai sync-names` to publish one")
        return require_known_policy(self.generation["policy"], self.policy_schemas)

    @property
    def available(self) -> frozenset[str]:
        """Every name that may be used: the generated list plus every name added by hand."""
        return frozenset(self.names or ()) | frozenset(self.manual)

    def _require_statuses(self) -> dict[str, dict]:
        if self.statuses is None:
            raise NameStatusError(f"the registry at {self.tip or 'this host'} keeps no name status")
        missing = sorted(name for name in self.available if name not in self.statuses)
        if missing:
            raise NameStatusError(
                f"the registry has no status for {', '.join(missing[:5])}{' and more' if len(missing) > 5 else ''}, "
                "so no name is treated as approved"
            )
        unreadable = sorted(name for name, entry in self.statuses.items() if entry.get("status") not in NAME_STATUSES)
        if unreadable:
            raise NameStatusError(
                f"cannot read the status of {', '.join(unreadable[:5])} (not one of {', '.join(NAME_STATUSES)}), "
                "so no name is treated as approved: update ai-cli-utils"
            )
        return self.statuses

    def status_of(self, name: str) -> str:
        """``name``'s general status; a name outside the pool is unreviewed."""
        return self._require_statuses().get(name, {}).get("status", "unreviewed")

    def approved(self) -> frozenset[str] | None:
        """Every name approved in general; None on a store with no status model (the git tree)."""
        if self.statuses is None:
            return None
        return frozenset(name for name, entry in self._require_statuses().items() if entry["status"] == "approved")

    def verdicts_for(self, name: str) -> dict[tuple[str, str], dict]:
        return {(kind, value): entry for (held, kind, value), entry in (self.verdicts or {}).items() if held == name}

    def generated(self) -> frozenset[str]:
        if not self.names:
            raise NoNameListError(f"the registry's {NAMES_TXT} is missing or empty; run `ai sync-names`")
        return frozenset(self.names)

    def holder(self, token: str) -> str | None:
        """``title@machine`` of the row holding ``token``, or the spare's machine; None when free."""
        row = self.rows.get(token)
        if row is None:
            return None
        return f"{row.get('title') or 'a spare'}@{row.get('machine')}"

    def rows_of(self, machine: str, kind: str | None = None) -> list[dict]:
        return [
            row for row in self.rows.values() if row.get("machine") == machine and (kind is None or row["kind"] == kind)
        ]


def parse_tree(files: dict[str, bytes], blobs: dict[str, str], tip: str | None, *, fetched: bool = True) -> Snapshot:
    """A :class:`Snapshot` from the registry's files, keyed by their tree path."""
    snapshot = Snapshot(tip=tip, fetched=fetched)
    for path, data in files.items():
        parts = PurePosixPath(path)
        if path == NAMES_TXT:
            snapshot.names = [line.strip() for line in data.decode("utf-8").splitlines() if line.strip()]
        elif path == GENERATION_JSON:
            snapshot.generation = json.loads(data)
        elif str(parts.parent) == NAMES_DIR and parts.suffix == ".json":
            snapshot.rows[parts.stem] = json.loads(data)
            snapshot.blobs[parts.stem] = blobs.get(path, "")
        elif str(parts.parent) == MANUAL_DIR and parts.suffix == ".json":
            snapshot.manual[parts.stem] = json.loads(data)
        elif str(parts.parent) == MIGRATIONS_DIR and parts.suffix == ".json":
            snapshot.migrations[parts.stem] = json.loads(data)
    return snapshot


def row_path(token: str) -> str:
    return f"{NAMES_DIR}/{token}.json"


def manual_path(name: str) -> str:
    return f"{MANUAL_DIR}/{name}.json"


def migration_path(old_title: str) -> str:
    return f"{MIGRATIONS_DIR}/{old_title}.json"


@dataclass
class Change:
    """One commit's worth of writes: a path's new bytes, or None to delete it."""

    writes: dict[str, bytes | None]
    message: str
    result: object = None


Build = Callable[[Snapshot], "Change | None"]


# --- the registry ----------------------------------------------------------------------


class Registry:
    """A registry backend. Subclasses supply reading, writing and the write lock."""

    kind = ""
    location = ""
    policy_schemas: tuple[int, ...] = (POLICY_SCHEMA,)

    def __init__(self, *, timeout_s: float = DEFAULT_TIMEOUT_S, attempts: int = DEFAULT_ATTEMPTS) -> None:
        self.timeout_s = timeout_s
        self.attempts = attempts
        self.last_claimants: dict[str, str] = {}

    def snapshot(self, *, fetch: bool = True) -> Snapshot:
        raise NotImplementedError

    def transact(self, build: Build, *, exempt: bool = False) -> Change | None:
        """Run ``build`` against the current tip and publish its change, retrying on a lost race.

        ``build`` is the operation's complete guard plus the writes it implies. It runs
        again from scratch against every new tip, so a precondition another writer broke
        in the meantime is seen and either refused or, for a picked name, picked again.
        Returns the published change, or None when ``build`` decided nothing needs writing.
        ``exempt`` lets an import or seed write while a database's writes are turned off.
        """
        raise NotImplementedError

    def find_names(self, filters: dict[str, str | None]) -> list[dict]:
        """The pool names matching every filter, from one query; only a database registry has one."""
        raise WriteRefusedError("searching names needs the database registry; this one is a git tree")

    def history(self, name: str) -> list[dict]:
        """Every past value of ``name``'s status, verdicts and hold; only the fleet's Dolt registry keeps it."""
        raise WriteRefusedError("name history needs the fleet registry on Dolt; this registry keeps no history")


class FleetRegistry(Registry):
    """A registry shared through a sibling repository that ships ``config/session-names.toml``."""

    kind = "fleet"

    def __init__(self, repo: Path, *, remote: str, policy_file: Path, timeout_s: float, attempts: int) -> None:
        super().__init__(timeout_s=timeout_s, attempts=attempts)
        self.repo = repo
        self.remote = remote
        self.policy_file = policy_file

    # -- git plumbing
    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in _GIT_TARGETING_VARS}
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        if extra:
            env.update(extra)
        return env

    def _git(
        self,
        *args: str,
        input: bytes | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["git", "-C", str(self.repo), "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args]
        try:
            result = subprocess.run(
                command, input=input, capture_output=True, timeout=timeout, env=self._env(env), check=False
            )
        except subprocess.TimeoutExpired as exc:
            self._clear_own_lock()
            raise RegistryUnreachableError(
                f"`git {args[0]}` against {self.remote} did not finish within {timeout:g} s"
            ) from exc
        if check and result.returncode != 0:
            raise RegistryError(f"git {' '.join(args[:2])} failed: {result.stderr.decode(errors='replace').strip()}")
        return result

    def _git_dir(self) -> Path:
        common = self._git("rev-parse", "--git-common-dir").stdout.decode().strip()
        path = Path(common)
        return path if path.is_absolute() else self.repo / path

    def _clear_own_lock(self) -> None:
        """Remove the lock a killed fetch left on the private ref, and only that lock."""
        with contextlib.suppress(OSError, RegistryError):
            (self._git_dir() / f"{PRIVATE_REF}.lock").unlink(missing_ok=True)

    def fetch_timeout_s(self) -> float:
        """The fetch budget from config.toml, read on every fetch."""
        try:
            return _config.read_registry_fetch_timeout(_config.load_config(read_only=True))
        except ValueError as exc:
            raise RegistryError(str(exc)) from exc

    def push_timeout_s(self) -> float:
        """The push budget from config.toml, read on every push."""
        try:
            return _config.read_registry_push_timeout(_config.load_config(read_only=True))
        except ValueError as exc:
            raise RegistryError(str(exc)) from exc


class GitRegistry(FleetRegistry):
    """The shared registry as a git tree: one ref of a sibling repository, written with plumbing."""

    def __init__(
        self, repo: Path, *, ref: str, remote: str, policy_file: Path, timeout_s: float, attempts: int
    ) -> None:
        super().__init__(repo, remote=remote, policy_file=policy_file, timeout_s=timeout_s, attempts=attempts)
        self.ref = ref
        self.location = f"{remote} {ref} ({repo})"

    def _fetch(self) -> str | None:
        # Offering only the last fetched tip keeps negotiation from walking every local ref,
        # which in a checkout with thousands of refs measured at over a third of the fetch's time.
        negotiation = [f"--negotiation-tip={PRIVATE_REF}"] if self._last_fetched() else []
        result = self._git(
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--quiet",
            *negotiation,
            self.remote,
            f"+{self.ref}:{PRIVATE_REF}",
            timeout=self.fetch_timeout_s(),
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            if _MISSING_REMOTE_REF.search(stderr):
                self._git("update-ref", "-d", PRIVATE_REF, check=False)
                return None
            raise RegistryUnreachableError(f"could not fetch {self.ref} from {self.remote}: {stderr.strip()}")
        return self._last_fetched()

    def _last_fetched(self) -> str | None:
        result = self._git("rev-parse", "--verify", "--quiet", f"{PRIVATE_REF}^{{commit}}", check=False)
        return result.stdout.decode().strip() or None

    def _read(self, tip: str | None) -> tuple[dict[str, bytes], dict[str, str]]:
        if tip is None:
            return {}, {}
        listing = self._git("ls-tree", "-r", "-z", "--full-tree", tip).stdout.decode()
        entries = []
        for record in filter(None, listing.split("\0")):
            meta, path = record.split("\t", 1)
            _mode, kind, oid = meta.split()
            if kind == "blob":
                entries.append((path, oid))
        if not entries:
            return {}, {}
        batch = self._git("cat-file", "--batch", input="".join(f"{oid}\n" for _path, oid in entries).encode()).stdout
        files: dict[str, bytes] = {}
        offset = 0
        for path, _oid in entries:
            header_end = batch.index(b"\n", offset)
            size = int(batch[offset:header_end].split()[2])
            start = header_end + 1
            files[path] = batch[start : start + size]
            offset = start + size + 1
        return files, dict(entries)

    def snapshot(self, *, fetch: bool = True) -> Snapshot:
        tip = self._fetch() if fetch else self._last_fetched()
        files, blobs = self._read(tip)
        return parse_tree(files, blobs, tip, fetched=fetch)

    def _identity(self) -> dict[str, str]:
        configured = self._git("config", "--get", "user.email", check=False).stdout.strip()
        if configured:
            return {}
        return {
            "GIT_AUTHOR_NAME": "ai-cli-utils",
            "GIT_AUTHOR_EMAIL": "ai-cli-utils@localhost",
            "GIT_COMMITTER_NAME": "ai-cli-utils",
            "GIT_COMMITTER_EMAIL": "ai-cli-utils@localhost",
        }

    def _commit(self, tip: str | None, change: Change) -> str:
        with tempfile.TemporaryDirectory(prefix="ai-cli-session-names-") as scratch:
            env = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
            self._git("read-tree", *([tip] if tip else ["--empty"]), env=env)
            lines = []
            for path, data in sorted(change.writes.items()):
                if data is None:
                    lines.append(f"0 {'0' * 40}\t{path}\n")
                else:
                    oid = self._git("hash-object", "-w", "--stdin", input=data).stdout.decode().strip()
                    lines.append(f"100644 {oid}\t{path}\n")
            self._git("update-index", "--index-info", input="".join(lines).encode(), env=env)
            tree = self._git("write-tree", env=env).stdout.decode().strip()
        parents = ["-p", tip] if tip else []
        return (
            self._git("commit-tree", tree, *parents, "-m", change.message, env=self._identity()).stdout.decode().strip()
        )

    def _push(self, commit: str) -> bool:
        """True when the push landed; False when another writer won the race."""
        result = self._git(
            "push",
            "--porcelain",
            "--quiet",
            self.remote,
            f"{commit}:{self.ref}",
            timeout=self.push_timeout_s(),
            check=False,
        )
        if result.returncode == 0:
            self._git("update-ref", PRIVATE_REF, commit, check=False)
            return True
        output = (result.stdout + result.stderr).decode(errors="replace")
        if _REJECTED_PUSH.search(output):
            return False
        raise RegistryUnreachableError(f"could not push {self.ref} to {self.remote}: {output.strip()}")

    def transact(self, build: Build, *, exempt: bool = False) -> Change | None:
        del exempt
        for _attempt in range(self.attempts):
            snapshot = self.snapshot()
            change = build(snapshot)
            if change is None or not change.writes:
                return None
            if self._push(self._commit(snapshot.tip, change)):
                return change
        raise RegistryContendedError(
            f"the registry write lost the race to other writers {self.attempts} times in a row; nothing was written"
        )


# --- discovery -------------------------------------------------------------------------


@dataclass(frozen=True)
class Discovery:
    """Which registry this machine uses, or why it keeps index names."""

    registry: Registry | None
    reason: str = ""


def _sibling_repos() -> list[Path]:
    from .session_naming import _projects_roots

    environment = _config.get_environment_repo()
    repos: list[Path] = []
    for root in _projects_roots():
        if environment and (root / environment).is_dir():
            repos.append(root / environment)
    for root in _projects_roots():
        if root.is_dir():
            repos += sorted((entry for entry in root.iterdir() if entry.is_dir()), key=lambda entry: entry.name)
    seen: set[Path] = set()
    unique = []
    for repo in repos:
        resolved = repo.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(repo)
    return unique


def read_policy_file(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError(f"cannot read {path}: {exc}") from exc


def local_registry_path() -> Path:
    return _config.get_xdg_data_home() / "session-names.sqlite3"


def discover() -> Discovery:
    """The registry for this machine: the shared ref, the local directory, or none.

    None when a sibling ships the project registry but not ``config/session-names.toml``:
    that repository's checkout predates human names, and a human-named session there
    could be read as scratch by its own tooling, so index names stay.
    """
    repos = _sibling_repos()
    for repo in repos:
        policy_file = repo / POLICY_MARKER
        if policy_file.is_file():
            policy = read_policy_file(policy_file)
            remote = policy.get("registry_remote", "origin")
            if policy.get("registry_backend") == "dolt":
                return Discovery(_dolt_registry(repo, policy, policy_file))
            if policy.get("registry_backend") not in (None, "git"):
                raise RegistryError(f"{policy_file} names registry_backend {policy.get('registry_backend')!r}")
            ref = policy.get("registry_ref")
            if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
                raise RegistryError(f"{policy_file} names no registry_ref under refs/heads/")
            return Discovery(
                GitRegistry(
                    repo,
                    ref=ref,
                    remote=str(remote),
                    policy_file=policy_file,
                    timeout_s=float(policy.get("publish_timeout_s", DEFAULT_TIMEOUT_S)),
                    attempts=int(policy.get("publish_attempts", DEFAULT_ATTEMPTS)),
                )
            )
    if any((repo / FLEET_PROJECTS_MARKER).is_file() for repo in repos):
        return Discovery(
            None,
            f"the repository that ships {FLEET_PROJECTS_MARKER.as_posix()} has no {POLICY_MARKER.as_posix()}: "
            "its checkout predates human session names, so index names are kept",
        )
    from .name_sqlite import SqliteRegistry

    return Discovery(SqliteRegistry(local_registry_path()))


def _dolt_registry(repo: Path, policy: dict, policy_file: Path) -> Registry:
    from .name_dolt import DEFAULT_DOLT_REF, DoltRegistry

    if policy.get("schema") != DOLT_POLICY_SCHEMA:
        raise RegistryError(f"{policy_file} selects the Dolt registry, which needs schema = {DOLT_POLICY_SCHEMA}")
    ref = policy.get("registry_dolt_ref", DEFAULT_DOLT_REF)
    if not isinstance(ref, str) or not ref.startswith("refs/dolt/") or ref == "refs/dolt/data":
        raise RegistryError(
            f"{policy_file} names registry_dolt_ref {ref!r}: it must be its own ref under refs/dolt/, "
            "never refs/dolt/data"
        )
    return DoltRegistry(
        repo,
        dolt_ref=ref,
        remote=str(policy.get("registry_remote", "origin")),
        policy_file=policy_file,
        timeout_s=float(policy.get("publish_timeout_s", DEFAULT_TIMEOUT_S)),
        attempts=int(policy.get("publish_attempts", DEFAULT_ATTEMPTS)),
    )


# --- allocation ------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """What a new session's title is built from, and who will hold its row."""

    repo: str
    repo_alias: str
    host_alias: str
    label: str | None
    machine: str
    client: str
    worktree: bool = True

    @property
    def stem(self) -> str:
        return f"{self.repo_alias}-{self.host_alias}"

    @property
    def title_prefix(self) -> str:
        return f"{self.stem}-{self.label}-" if self.label else f"{self.stem}-"

    def title_for(self, name: str) -> str:
        return f"{self.title_prefix}{name}"

    def worktree_for(self, title: str) -> str | None:
        return f"{_config.WORKTREE_DIR}/{title}" if self.worktree else None


@dataclass
class Reservation:
    """A name now held for a session, and what the pick saw."""

    name: str
    title: str
    row: dict
    pick: object = None
    spares_added: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    offline: bool = False
    added_to_database: bool = False


class HandChosenRefusedError(WriteRefusedError):
    """A name chosen by hand cannot be reserved; ``verdict`` says which rule refused it."""

    def __init__(self, verdict: str, reason: str) -> None:
        super().__init__(reason)
        self.verdict = verdict


class SparesExhaustedError(RegistryError):
    """The registry is unreachable and this machine has no unactivated spare."""


def alias_words() -> frozenset[str]:
    """Every repo alias and host alias this machine knows of: never a session name."""
    from . import session_naming

    words: set[str] = set()
    try:
        fleet = _config.get_fleet_registry_path()
    except _config.ProjectPrefixError:
        fleet = None
    if fleet is not None and fleet.is_file():
        with contextlib.suppress(_config.ProjectPrefixError):
            words |= {entry["prefix"].lower() for entry in _config._projects_table_entries(fleet).values()}
    for source in session_naming.alias_sources():
        with contextlib.suppress(session_naming.HostAliasError):
            data = session_naming._read_registry(source) or {}
            aliases = data.get("aliases", {})
            if isinstance(aliases, dict):
                words |= {str(alias).lower() for alias in aliases.values()}
    return frozenset(words)


_WORDS_CACHE: dict[str, frozenset[str]] = {}


def words_cache_dir() -> Path:
    return _config.get_xdg_cache_home() / "session-names"


def common_words(policy: dict, *, fetch: Callable[[str], bytes] | None = None) -> frozenset[str]:
    """The lowercase entries of the policy's pinned dictionary, downloaded once and cached by digest.

    The cached archive is checked against the pinned digest on every read, so a
    corrupted or replaced cache file is downloaded again rather than trusted.
    """
    confusable = policy["confusable"]
    digest = confusable["words_digest"]
    if digest in _WORDS_CACHE:
        return _WORDS_CACHE[digest]
    cached = words_cache_dir() / f"words-{hashlib.sha256(digest.encode()).hexdigest()[:16]}.tgz"
    data = cached.read_bytes() if cached.is_file() else b""
    if not data or name_list.digest_of(data, digest) != digest:
        try:
            data = (fetch or name_list.download)(confusable["words_url"])
            name_list.verify(data, digest, confusable["words_url"])
        except name_list.NameListError as exc:
            raise RegistryError(f"cannot check common words: {exc}") from exc
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    member = name_list.read_member(data, confusable["words_member"], confusable["words_url"])
    words = frozenset(name_list.parse_common_words(member.decode("utf-8")))
    _WORDS_CACHE[digest] = words
    return words


def is_common_word(name: str, policy: dict, words: frozenset[str]) -> bool:
    confusable = policy.get("confusable", {})
    if name in confusable.get("keep_names", []):
        return False
    return name in words or name in confusable.get("extra_words", [])


def passes_static(name: str, policy: dict, words: frozenset[str]) -> bool:
    """The generator's static filters, applied to one name under ``policy``."""
    low, high = name_list.limits(policy)
    return (
        bool(_TOKEN_RE.fullmatch(name))
        and low <= len(name) <= high
        and name not in policy.get("denylist", [])
        and name not in name_list.read_non_names()
        and not is_common_word(name, policy, words)
    )


def held_tokens(snapshot: Snapshot, spool: dict[str, dict] | None = None) -> frozenset[str]:
    return frozenset(snapshot.rows) | frozenset(spool or {})


def held_titles(snapshot: Snapshot, spool: dict[str, dict] | None = None) -> frozenset[str]:
    titles = {row["title"] for row in snapshot.rows.values() if row.get("title")}
    titles |= {entry["title"] for entry in (spool or {}).values() if entry.get("title")}
    return frozenset(titles)


def policy_source(registry: Registry) -> str:
    return str(registry.policy_file) if isinstance(registry, FleetRegistry) else "the local config"


def rejecting_scope(snapshot: Snapshot, name: str, slot: Slot, session: str | None = None) -> str | None:
    """The scope a verdict rejects ``name`` in for this launch (``host box-one``), or None.

    The launch's scopes are its repository, its host and its session: the session a
    migration renames (``session``), or the title the new session would get.
    """
    sessions = {slot.title_for(name), *([session] if session else [])}
    for (kind, value), entry in sorted(snapshot.verdicts_for(name).items()):
        if entry.get("verdict") != "rejected":
            continue
        if (kind == "repo" and value == slot.repo) or (kind == "host" and value == slot.machine):
            return f"{kind} {value}"
        if kind == "session" and value in sessions:
            return f"session {value}"
    return None


def session_approves(snapshot: Snapshot, name: str, slot: Slot, session: str | None = None) -> bool:
    """Whether a verdict approves ``name`` for the launching session."""
    sessions = {slot.title_for(name), *([session] if session else [])}
    return any(
        kind == "session" and value in sessions and entry.get("verdict") == "approved"
        for (kind, value), entry in snapshot.verdicts_for(name).items()
    )


def scoped_rejections(snapshot: Snapshot, slot: Slot, session: str | None = None) -> frozenset[str]:
    """Every pool name a verdict rejects for this launch."""
    names = {held for held, _kind, _value in (snapshot.verdicts or {})}
    return frozenset(name for name in names if rejecting_scope(snapshot, name, slot, session))


def hand_choice_refusal(
    snapshot: Snapshot, name: str, slot: Slot, session: str | None = None
) -> tuple[str, str] | None:
    """``("rejected", reason)`` when the status model refuses a hand-chosen ``name``, else None.

    A scoped rejection for the launch's repository, host or session refuses first; a
    general rejection refuses unless a verdict approves the name for this session. An
    unreviewed name may be chosen by hand. A store with no status model refuses nothing.
    """
    if snapshot.statuses is None:
        return None
    scope = rejecting_scope(snapshot, name, slot, session)
    if scope:
        return "rejected", f"{name} is rejected for {scope} (`ai find-names -k KIND` lists scoped verdicts)"
    if snapshot.status_of(name) == "rejected" and not session_approves(snapshot, name, slot, session):
        return "rejected", f"{name} is rejected as a session name (`ai list-names --pool` lists each name's status)"
    return None


def pick_request(
    registry: Registry,
    snapshot: Snapshot,
    slot: Slot,
    *,
    spool: dict[str, dict] | None = None,
    max_length: int | None = None,
    words: frozenset[str] | None = None,
    extra_held: frozenset[str] = frozenset(),
    session: str | None = None,
):
    """The picker's request for ``slot`` under the published policy, and any warning it raised.

    Only approved names are candidates, and none a verdict rejects for this launch;
    ``session`` is the session a migration renames. Unusable status data refuses.
    """
    from . import session_naming

    policy = snapshot.policy
    low, high = name_list.limits(policy)
    include = policy.get("repos", {}).get(slot.repo_alias, {}).get("letters")
    letters, warning = session_naming.resolve_letters(slot.repo_alias, include, policy_source(registry))
    confusable = policy.get("confusable", {})
    request = session_naming.PickRequest(
        generated=snapshot.generated(),
        held=held_tokens(snapshot, spool) | extra_held,
        held_titles=held_titles(snapshot, spool),
        stem=slot.stem,
        title_prefix=slot.title_prefix,
        repo_alias=slot.repo_alias,
        min_length=low,
        max_length=min(high, max_length) if max_length else high,
        letters=letters,
        pick_floor=int(policy.get("pick_floor", 10)),
        reserved_words=alias_words() | {slot.repo_alias, slot.host_alias},
        denylist=frozenset(policy.get("denylist", [])),
        manual=frozenset(snapshot.manual),
        passes_static=(lambda name: passes_static(name, policy, words)) if words is not None else None,
        max_edit_distance=int(confusable.get("max_edit_distance", 1)),
        approved=snapshot.approved(),
        rejected=scoped_rejections(snapshot, slot, session),
    )
    return request, [warning] if warning else []


def _spare_writes(
    snapshot: Snapshot, slot: Slot, held: frozenset[str], words: frozenset[str] | None, rng
) -> tuple[dict[str, bytes | None], list[str]]:
    """Rows that bring this machine's spares up to ``offline_spares``, each passing the picker's guard."""
    from . import session_naming

    policy = snapshot.policy
    approved = snapshot.approved()
    host_rejected = frozenset(
        held for held, kind, value in (snapshot.verdicts or {})
        if kind == "host" and value == slot.machine and snapshot.verdicts[(held, kind, value)].get("verdict") == "rejected"
    )  # fmt: skip
    shortfall = int(policy.get("offline_spares", 3)) - len(snapshot.rows_of(slot.machine, "spare"))
    writes: dict[str, bytes | None] = {}
    added: list[str] = []
    low, high = name_list.limits(policy)
    for _ in range(max(shortfall, 0)):
        request = session_naming.PickRequest(
            generated=snapshot.generated(),
            held=held | frozenset(added),
            held_titles=frozenset(),
            stem=slot.stem,
            title_prefix=f"{slot.stem}-",
            repo_alias="",
            min_length=low,
            max_length=high,
            letters=frozenset(),
            pick_floor=1,
            reserved_words=alias_words() | {slot.repo_alias, slot.host_alias},
            denylist=frozenset(policy.get("denylist", [])),
            manual=frozenset(snapshot.manual),
            passes_static=(lambda name: passes_static(name, policy, words)) if words is not None else None,
            max_edit_distance=int(policy.get("confusable", {}).get("max_edit_distance", 1)),
            approved=approved,
            rejected=host_rejected,
        )
        try:
            name = session_naming.pick_name(request, rng).name
        except session_naming.NamePoolExhaustedError:
            break
        writes[row_path(name)] = checked_row(spare_row(name=name, host_alias=slot.host_alias, machine=slot.machine))
        added.append(name)
    return writes, added


def reserve_picked(
    registry: Registry,
    slot: Slot,
    *,
    spool: dict[str, dict] | None = None,
    max_length: int | None = None,
    rng=None,
    words: frozenset[str] | None = None,
) -> Reservation:
    """Pick a name for ``slot`` and reserve it, topping up this machine's spares in the same commit."""
    from . import session_naming

    def build(snapshot: Snapshot) -> Change:
        request, warnings = pick_request(registry, snapshot, slot, spool=spool, max_length=max_length, words=words)
        pick = session_naming.pick_name(request, rng)
        title = slot.title_for(pick.name)
        row = session_row(
            name=pick.name,
            title=title,
            repo=slot.repo,
            repo_alias=slot.repo_alias,
            host_alias=slot.host_alias,
            label=slot.label,
            machine=slot.machine,
            client=slot.client,
            source="picked",
            tier=pick.tier,
            worktree=slot.worktree_for(title),
        )
        writes: dict[str, bytes | None] = {row_path(pick.name): checked_row(row)}
        spares, added = _spare_writes(snapshot, slot, request.held | {pick.name}, words, rng)
        writes.update(spares)
        reservation = Reservation(pick.name, title, row, pick=pick, spares_added=added, warnings=warnings)
        return Change(writes, f"session-names: reserve {pick.name} ({title}@{slot.machine})", result=reservation)

    change = registry.transact(build)
    assert change is not None
    return change.result  # type: ignore[return-value]


def check_hand_chosen(
    snapshot: Snapshot,
    name: str,
    slot: Slot,
    words: frozenset[str],
    *,
    spool: dict[str, dict] | None = None,
    session: str | None = None,
) -> tuple[str, str]:
    """``(verdict, reason)`` for a name chosen by hand, under the snapshot's published policy.

    A held name that is an unactivated spare of the slot's own machine, and not in that
    machine's offline ``spool`` (read from disk when None), is ``spare``: the reservation
    takes it by activating the row. Any other held name is ``held``. Then ``rejected``
    (:func:`hand_choice_refusal`; ``session`` is the session a migration renames), then come
    ``confusable`` (one edit from a held name, or a strict-prefix title pair), then
    ``word``. A name in the database is then ``ok`` inside the limits and ``limits``
    outside them; a name not in the database is ``unknown`` whatever its length,
    because the flow asks whether to add it before it checks the limits.
    """
    from . import session_naming

    policy = snapshot.policy
    holder = snapshot.holder(name)
    if holder:
        if _is_own_spare(snapshot, name, slot, read_spool() if spool is None else spool):
            return "spare", f"{name} is this machine's spare"
        return "held", f"{name} is held by {holder}"
    refusal = hand_choice_refusal(snapshot, name, slot, session)
    if refusal:
        return refusal
    if _TOKEN_RE.fullmatch(name):
        held = held_tokens(snapshot)
        if int(policy.get("confusable", {}).get("max_edit_distance", 1)):
            near = sorted(token for token in held if session_naming.one_edit_apart(name, token))
            if near:
                return "confusable", f"{name} is one edit from the held name {near[0]} ({snapshot.holder(near[0])})"
        title = slot.title_for(name)
        for other in sorted(held_titles(snapshot)):
            if (
                other.startswith(f"{slot.stem}-")
                and other != title
                and (other.startswith(title) or title.startswith(other))
            ):
                return (
                    "confusable",
                    f"the title {title} and the held title {other} ({snapshot.holder(other.rsplit('-', 1)[-1]) or 'held'}) are a prefix pair",
                )
        if is_common_word(name, policy, words):
            return "word", (
                f"{name} is a common English word; to allow it as a name, add it to keep_names in "
                "config/session-names.toml"
            )
    if name not in snapshot.available:
        return "unknown", f"{name} is not in the name database"
    reason = limits_reason(name, policy)
    return ("limits", reason) if reason else ("ok", "")


def _is_own_spare(snapshot: Snapshot, name: str, slot: Slot, spool: dict[str, dict]) -> bool:
    row = snapshot.rows.get(name)
    return row is not None and row["kind"] == "spare" and row.get("machine") == slot.machine and name not in spool


def _take_own_spare(
    snapshot: Snapshot, name: str, slot: Slot, spool: dict[str, dict], words: frozenset[str] | None
) -> tuple[dict, dict[str, bytes | None], list[str]]:
    """The session row this machine's spare ``name`` becomes, the writes that activate it and
    refill the pool to ``offline_spares``, and the spares added."""
    title = slot.title_for(name)
    row = activate_spare(
        snapshot.rows[name],
        title=title,
        repo=slot.repo,
        repo_alias=slot.repo_alias,
        label=slot.label,
        client=slot.client,
        worktree=slot.worktree_for(title),
    )
    writes: dict[str, bytes | None] = {row_path(name): checked_row(row)}
    remaining = replace(snapshot, rows={token: r for token, r in snapshot.rows.items() if token != name})
    spares, added = _spare_writes(remaining, slot, held_tokens(snapshot, spool), words, None)
    writes.update(spares)
    return row, writes, added


class _SpoolLockNeeded(Exception):
    """A build found this machine's own spare while ``spool.lock`` was not held."""


def _transact_hand_chosen(registry: Registry, build_for: Callable[[bool], Callable[[Snapshot], Change]]) -> Change:
    """Run a hand-chosen build, again under ``spool.lock`` when it would take this machine's own spare.

    Only the own-spare path takes the lock, as an offline activation does, so an offline
    launch on this machine cannot take the same spare between the spool read and the push.
    """
    try:
        change = registry.transact(build_for(False))
    except _SpoolLockNeeded:
        with spool_lock(lock_wait(registry)):
            change = registry.transact(build_for(True))
    assert change is not None
    return change


def limits_text(policy: dict) -> str:
    low, high = name_list.limits(policy)
    return f"the configured limit is {low} to {high} letters (config/session-names.toml [limits])"


def limits_reason(name: str, policy: dict) -> str | None:
    """Why ``name`` is outside the charset or the limits, or None when it is inside both."""
    low, high = name_list.limits(policy)
    if not _TOKEN_RE.fullmatch(name):
        return f'"{name}" is not lowercase letters only; {limits_text(policy)}.'
    if len(name) > high:
        return f'"{name}" is {len(name)} letters; {limits_text(policy)}.'
    if len(name) < low:
        return f'"{name}" is {len(name)} letters, shorter than the minimum; {limits_text(policy)}.'
    return None


def reserve_hand_chosen(
    registry: Registry, name: str, slot: Slot, *, add: bool, via: str, added_by: str, words: frozenset[str]
) -> Reservation:
    """Reserve a name chosen by hand, adding it to the database in the same commit when ``add``.

    The checks run again on every fetched tip; a name another writer made unusable in
    the meantime raises :class:`HandChosenRefusedError` with the reason the new tip gave.
    A name that is this machine's own spare is activated in place, under ``spool.lock``
    so an offline launch cannot take it meanwhile.
    """

    def build_for(locked: bool) -> Callable[[Snapshot], Change]:
        return lambda snapshot: build(snapshot, locked)

    def build(snapshot: Snapshot, locked: bool) -> Change:
        spool = read_spool()
        verdict, reason = check_hand_chosen(snapshot, name, slot, words, spool=spool)
        if verdict == "spare":
            if not locked:
                raise _SpoolLockNeeded
            row, writes, added = _take_own_spare(snapshot, name, slot, spool, words)
            reservation = Reservation(name, row["title"], row, spares_added=added)
            return Change(writes, f"session-names: activate {name} ({row['title']}@{slot.machine})", result=reservation)
        if verdict not in ("ok", "unknown") or (verdict == "unknown" and not add):
            raise HandChosenRefusedError(verdict, reason)
        if verdict == "unknown":
            out_of_limits = limits_reason(name, snapshot.policy)
            if out_of_limits:
                raise HandChosenRefusedError("limits", out_of_limits)
        title = slot.title_for(name)
        source = "manual" if verdict == "unknown" else "custom"
        row = session_row(
            name=name,
            title=title,
            repo=slot.repo,
            repo_alias=slot.repo_alias,
            host_alias=slot.host_alias,
            label=slot.label,
            machine=slot.machine,
            client=slot.client,
            source=source,
            tier=None,
            worktree=slot.worktree_for(title),
        )
        writes: dict[str, bytes | None] = {row_path(name): checked_row(row)}
        if verdict == "unknown":
            writes[manual_path(name)] = encode_json(_manual_record(name, added_by, via, None))
        reservation = Reservation(name, title, row, added_to_database=verdict == "unknown")
        return Change(writes, f"session-names: reserve {name} ({title}@{slot.machine})", result=reservation)

    return _transact_hand_chosen(registry, build_for).result  # type: ignore[return-value]


def _manual_record(name: str, added_by: str, via: str, reason: str | None) -> dict:
    return {"schema": 1, "name": name, "added_by": added_by, "added_at": utc_now(), "via": via, "reason": reason}


def add_name(
    registry: Registry,
    name: str,
    *,
    added_by: str,
    reason: str | None,
    words_for: Callable[[dict], frozenset[str]] | None = None,
) -> bool:
    """Add ``name`` to the database without reserving it; False when it was already there."""
    words_for = words_for or common_words

    def build(snapshot: Snapshot) -> Change | None:
        policy = snapshot.policy
        out_of_limits = limits_reason(name, policy)
        if out_of_limits:
            raise HandChosenRefusedError("limits", out_of_limits)
        if is_common_word(name, policy, words_for(policy)):
            raise HandChosenRefusedError(
                "word",
                f"{name} is a common English word; to allow it as a name, add it to keep_names in "
                "config/session-names.toml",
            )
        if name in snapshot.available:
            return None
        return Change(
            {manual_path(name): encode_json(_manual_record(name, added_by, "add-name", reason))},
            f"session-names: add-name {name} ({added_by})",
        )

    return registry.transact(build) is not None


def remove_name(registry: Registry, name: str) -> None:
    """Remove a name added by hand; never one that is held or one the generator produced."""

    def build(snapshot: Snapshot) -> Change:
        holder = snapshot.holder(name)
        if holder:
            raise WriteRefusedError(f"{name} is held by {holder}; it can be removed once it is released")
        if name in snapshot.manual:
            return Change({manual_path(name): None}, f"session-names: remove-name {name}")
        if name in (snapshot.names or ()):
            raise WriteRefusedError(
                f"{name} is a generated name; generated names change through the limits and filters in "
                "config/session-names.toml, not by hand"
            )
        raise WriteRefusedError(f"{name} is not in the name database")

    registry.transact(build)


def _unknown_names(snapshot: Snapshot, names: list[str]) -> list[str]:
    return sorted({name for name in names if name not in snapshot.available})


def set_name_status(
    registry: Registry,
    names: list[str],
    status: str,
    *,
    actor: str,
    recorded_by: str,
    reason: str | None = None,
    legacy: bool = False,
) -> list[str]:
    """Give every name in ``names`` the general ``status`` in one commit; returns the names it changed.

    Refuses the whole call when any name is outside the pool. ``legacy`` accepts the
    earlier record's ``excluded`` and ``needs-review``, stored as ``rejected`` and
    ``unreviewed``; the public command passes only the three canonical values.
    """
    from . import name_schema

    if legacy:
        status = LEGACY_STATUSES.get(status, status)
    if status not in NAME_STATUSES:
        raise WriteRefusedError(f"{status!r} is not a name status; use one of {', '.join(NAME_STATUSES)}")

    def build(snapshot: Snapshot) -> Change | None:
        if snapshot.statuses is None:
            raise WriteRefusedError("this registry keeps no name status; it arrives with the database registry")
        unknown = _unknown_names(snapshot, names)
        if unknown:
            raise WriteRefusedError(
                "; ".join(f"{name} is not in the name database" for name in unknown)
                + "; add each with `ai add-name NAME` first. Nothing was written"
            )
        stamp = utc_now()
        entry = {"status": status, "actor": actor, "recorded_by": recorded_by, "set_at": stamp, "reason": reason}
        changed = sorted({name for name in names if snapshot.statuses.get(name, {}).get("status") != status})
        if not changed:
            return None
        writes: dict[str, bytes | None] = {
            name_schema.status_path(name): name_schema.canonical(entry).encode() for name in changed
        }
        return Change(writes, f"session-names: set-name-status {status} {' '.join(changed)} ({actor})", result=changed)

    change = registry.transact(build)
    return change.result if change is not None else []  # type: ignore[return-value]


def set_name_verdict(
    registry: Registry,
    names: list[str],
    verdict: str,
    *,
    scope_kind: str,
    scope_value: str,
    actor: str,
    recorded_by: str,
    reason: str | None = None,
    delete: bool = False,
) -> list[str]:
    """Write one scoped verdict per name, keyed by name and scope, in one commit; or remove them.

    With ``delete``, only a verdict whose value is ``verdict`` is removed. Refuses the
    whole call when the scope kind is unknown or any name is outside the pool.
    """
    from . import name_schema

    if scope_kind not in SCOPE_KINDS:
        raise WriteRefusedError(f"{scope_kind!r} is not a verdict scope; use one of {', '.join(SCOPE_KINDS)}")
    if verdict not in VERDICT_VALUES:
        raise WriteRefusedError(f"{verdict!r} is not a verdict; use one of {', '.join(VERDICT_VALUES)}")
    if not scope_value:
        raise WriteRefusedError("a verdict needs the repository, host or session it applies to")

    def build(snapshot: Snapshot) -> Change | None:
        if snapshot.verdicts is None:
            raise WriteRefusedError("this registry keeps no verdicts; they arrive with the database registry")
        unknown = _unknown_names(snapshot, names)
        if unknown:
            raise WriteRefusedError(
                "; ".join(f"{name} is not in the name database" for name in unknown) + ". Nothing was written"
            )
        writes: dict[str, bytes | None] = {}
        entry = {"verdict": verdict, "actor": actor, "recorded_by": recorded_by, "set_at": utc_now(), "reason": reason}
        for name in sorted(set(names)):
            current = snapshot.verdicts.get((name, scope_kind, scope_value))
            path = name_schema.verdict_path(name, scope_kind, scope_value)
            if delete:
                if current is not None and current.get("verdict") == verdict:
                    writes[path] = None
            elif current is None or current.get("verdict") != verdict or current.get("reason") != reason:
                writes[path] = name_schema.canonical(entry).encode()
        if not writes:
            return None
        action = "remove" if delete else "set"
        return Change(
            writes,
            f"session-names: {action} {verdict} for {scope_kind} {scope_value}: {' '.join(sorted(set(names)))} ({actor})",
            result=sorted(name_schema.parse_verdict_path(path)[0] for path in writes),
        )

    change = registry.transact(build)
    return change.result if change is not None else []  # type: ignore[return-value]


def last_known_snapshot(registry: Registry) -> Snapshot:
    """The last fetched tip, fetched once first when this checkout has never fetched the registry.

    A fresh clone carries no local registry ref, because ``git clone`` copies only
    branches and tags. Raises :class:`RegistryUnreachableError` when that first fetch
    cannot finish, so the caller takes its unreachable path rather than reading an
    empty registry. Never call it under a lock others wait on.
    """
    snapshot = registry.snapshot(fetch=False)
    if isinstance(registry, FleetRegistry) and snapshot.tip is None:
        return registry.snapshot()
    return snapshot


def known_names(registry: Registry | None) -> frozenset[str]:
    """The parser's name set, read without the network: the last known list and held names.

    It is the generated list and the names added by hand, plus the tokens of
    ``session`` and ``spare`` rows. A dropped label's token is held but is not a name.
    The shipped seed is always included, so a title can be read on a machine that
    has never fetched the registry.
    """
    names: set[str] = set(name_list.read_names(name_list.DATA_DIR / name_list.NAMES_FILE))
    if registry is None:
        return frozenset(names)
    try:
        snapshot = registry.snapshot(fetch=False)
    except RegistryError:
        return frozenset(names)
    names |= snapshot.available
    names |= {token for token, row in snapshot.rows.items() if row.get("kind") in ("session", "spare")}
    return frozenset(names)


def reserve_label(registry: Registry, slot: Slot, label: str) -> Reservation:
    """Reserve a drop-policy title ``{stem}-{label}`` by its label's last token, as a ``label`` row."""
    token = label.rsplit("-", 1)[-1]
    title = f"{slot.stem}-{label}"

    def build(snapshot: Snapshot) -> Change:
        holder = snapshot.holder(token)
        if holder:
            raise HandChosenRefusedError("held", f"the label's last token {token} is held by {holder}")
        row = label_row(
            token=token,
            title=title,
            repo=slot.repo,
            repo_alias=slot.repo_alias,
            host_alias=slot.host_alias,
            label=label,
            machine=slot.machine,
            client=slot.client,
            worktree=slot.worktree_for(title),
        )
        return Change(
            {row_path(token): checked_row(row)},
            f"session-names: reserve {token} ({title}@{slot.machine})",
            result=Reservation(token, title, row),
        )

    change = registry.transact(build)
    assert change is not None
    return change.result  # type: ignore[return-value]


# --- spares and the offline spool ------------------------------------------------------


def spool_root() -> Path:
    return _config.get_xdg_state_home() / "session-names"


def spool_dir() -> Path:
    return spool_root() / "pending"


@contextlib.contextmanager
def spool_lock(wait_s: float) -> Iterator[None]:
    """The host-local lock held from reading the spool to writing an activation, or through a release."""
    root = spool_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with portalocker.Lock(str(root / "spool.lock"), mode="a", timeout=wait_s):
            yield
    except portalocker.exceptions.LockException as exc:
        raise SparesExhaustedError(
            f"the spare pool stayed locked for {wait_s:g} s by another launch or a reconcile; nothing was taken"
        ) from exc


def read_spool() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    directory = spool_dir()
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            with contextlib.suppress(OSError, json.JSONDecodeError):
                entries[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return entries


def _write_spool_entry(entry: dict) -> None:
    directory = spool_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{entry['token']}.json"
    fd, staged = tempfile.mkstemp(prefix=f".{target.name}.", dir=directory)
    with os.fdopen(fd, "wb") as handle:
        handle.write(encode_json(entry))
        handle.flush()
        os.fsync(handle.fileno())
    Path(staged).replace(target)
    with contextlib.suppress(OSError, AttributeError):
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def lock_wait(registry: Registry) -> float:
    return registry.timeout_s * registry.attempts


def take_spare(registry: Registry, slot: Slot, *, cause: str | None = None) -> Reservation:
    """Activate this machine's shortest unactivated spare for ``slot`` while the registry is unreachable.

    Reads the last fetched tip, never the network. Holds ``spool.lock`` from reading the
    spool through the durable write of the activation, so two offline launches on one
    machine can never take the same spare. ``cause`` is the failure that sent the caller
    here; a refusal for want of a spare quotes it.
    """
    with spool_lock(lock_wait(registry)):
        snapshot = registry.snapshot(fetch=False)
        approved = snapshot.approved()
        spool = read_spool()
        free = sorted(
            (
                row
                for row in snapshot.rows_of(slot.machine, "spare")
                if row["token"] not in spool
                and (approved is None or row["token"] in approved)
                and not rejecting_scope(snapshot, row["token"], slot)
            ),
            key=lambda row: (len(row["token"]), row["token"]),
        )
        if not free:
            because = f" ({cause})" if cause else ""
            raise SparesExhaustedError(
                f"the registry is unreachable{because} and this machine has no unactivated spare name left "
                "that is approved; "
                "once online, "
                "run `ai reconcile-names` to publish the activations and replenish the spares"
            )
        spare = free[0]
        title = slot.title_for(spare["token"])
        row = activate_spare(
            spare,
            title=title,
            repo=slot.repo,
            repo_alias=slot.repo_alias,
            label=slot.label,
            client=slot.client,
            worktree=slot.worktree_for(title),
        )
        checked_row(row)
        _write_spool_entry({**row, "op": "activate"})
    return Reservation(spare["token"], title, row, offline=True)


def publish_spool(registry: Registry, machine: str) -> list[str]:
    """Publish every activation in this machine's spool, removing each entry once it is on the ref.

    Callers hold ``spool.lock``. An entry whose spare row changed in a way only another
    writer could have caused is left in the spool and reported, never overwritten.
    """
    published: list[str] = []
    for token, entry in read_spool().items():
        activation = {key: value for key, value in entry.items() if key != "op"}

        def build(snapshot: Snapshot, token=token, activation=activation) -> Change | None:
            row = snapshot.rows.get(token)
            if row == activation:
                return None
            if row is None or row["kind"] != "spare" or row["machine"] != machine:
                raise WriteRefusedError(
                    f"the spool activates {token}, but the registry's row for it is no longer this machine's spare"
                )
            changed = {key for key in ROW_FIELDS if row[key] != activation[key]}
            if not changed <= SPARE_ACTIVATION_FIELDS:
                raise WriteRefusedError(
                    f"the spool entry for {token} changes {sorted(changed)}, not only the activation"
                )
            return Change(
                {row_path(token): checked_row(activation)},
                f"session-names: activate {token} ({activation['title']}@{machine})",
            )

        registry.transact(build)
        (spool_dir() / f"{token}.json").unlink(missing_ok=True)
        published.append(token)
    return published


# --- regeneration ------------------------------------------------------------------------


ANCESTRY_STATES = ("same", "local-newer", "local-older", "incomparable")


@dataclass
class SyncResult:
    state: str
    published: bool
    message: str
    count: int | None = None


def policy_revision(registry: FleetRegistry) -> tuple[str | None, str | None]:
    """The commit that last changed the policy file in this checkout, and its committer time."""
    relative = registry.policy_file.relative_to(registry.repo).as_posix()
    result = registry._git("log", "-1", "--format=%H%x00%cI", "HEAD", "--", relative, check=False)
    text = result.stdout.decode().strip()
    if result.returncode != 0 or not text:
        return None, None
    commit, committed_at = text.split("\0", 1)
    return commit, committed_at


def ancestry_state(registry: FleetRegistry, ref_commit: str | None, local_commit: str | None) -> str:
    """One of :data:`ANCESTRY_STATES`, comparing the ref's recorded policy revision with this checkout's."""
    if not local_commit or not ref_commit:
        return "incomparable"
    if ref_commit == local_commit:
        return "same"
    if registry._git("cat-file", "-e", f"{ref_commit}^{{commit}}", check=False).returncode != 0:
        return "incomparable"
    if registry._git("merge-base", "--is-ancestor", ref_commit, local_commit, check=False).returncode == 0:
        return "local-newer"
    if registry._git("merge-base", "--is-ancestor", local_commit, ref_commit, check=False).returncode == 0:
        return "local-older"
    return "incomparable"


def sync_names(
    registry: Registry,
    *,
    local_policy: dict,
    dry_run: bool = False,
    fetch: Callable[[str], bytes] | None = None,
) -> SyncResult:
    """Regenerate and publish the available list when this checkout's policy is the newer side.

    For the shared registry the decision is the four-state ancestry test between the
    ref's recorded ``policy_commit`` and the last commit that changed this checkout's
    policy file, after fetching that repository's remote. Only ``local-newer`` with a
    different policy digest downloads and publishes, in one commit touching only
    ``available/names.txt`` and ``available/generation.json``. A local registry
    regenerates whenever its policy digest differs.
    """
    digest = name_list.policy_digest(local_policy)
    commit = committed_at = None
    if isinstance(registry, FleetRegistry):
        with contextlib.suppress(RegistryError):
            registry._git(
                "fetch", "--no-tags", "--quiet", registry.remote, timeout=registry.fetch_timeout_s(), check=False
            )
        commit, committed_at = policy_revision(registry)
    generated: dict[str, name_list.Generation] = {}

    def decide(snapshot: Snapshot) -> tuple[str, bool]:
        if snapshot.generation is None:
            state = "local-newer"
        elif isinstance(registry, FleetRegistry):
            state = ancestry_state(registry, snapshot.generation.get("policy_commit"), commit)
        else:
            state = "local-newer"
        differs = snapshot.generation is None or snapshot.generation.get("policy_digest") != digest
        return state, state == "local-newer" and differs

    if dry_run:
        state, regenerate = decide(registry.snapshot())
        would = "would download and publish a new list" if regenerate else "would publish nothing"
        return SyncResult(state, False, f"dry run: policy state {state}; {would}")

    outcome: dict[str, str] = {}

    def build(snapshot: Snapshot) -> Change | None:
        state, regenerate = decide(snapshot)
        outcome["state"] = state
        if not regenerate:
            return None
        if digest not in generated:
            generated[digest] = name_list.generate(
                local_policy, fetch=fetch, policy_commit=commit, policy_committed_at=committed_at
            )
        generation = generated[digest]
        return Change(
            {
                NAMES_TXT: name_list.render_names(generation.names).encode("utf-8"),
                GENERATION_JSON: name_list.render_record(generation.record).encode("utf-8"),
            },
            f"session-names: generate {generation.record['count']} names",
            result=generation.record["count"],
        )

    change = registry.transact(build)
    state = outcome.get("state", "same")
    if change is not None:
        return SyncResult(state, True, f"published {change.result} names (policy {state})", count=change.result)  # type: ignore[arg-type]
    messages = {
        "same": "the published list already reflects this checkout's policy",
        "local-newer": "the published policy digest already matches this checkout's",
        "local-older": "this checkout's policy is older than the published one; allocation applies the published policy",
        "incomparable": (
            f"this checkout's policy revision ({commit or 'uncommitted'}) and the published one cannot be ordered; "
            "nothing was published"
        ),
    }
    return SyncResult(state, False, messages[state])


def local_policy(registry: Registry) -> dict:
    """The policy this machine would publish: the shared policy file's, or the defaults with local limits.

    On a shared registry the two length limits come only from that file. A local
    install takes them from ``[session_names]`` in config.toml, then the defaults.
    """
    if isinstance(registry, FleetRegistry):
        return name_list.fleet_policy(read_policy_file(registry.policy_file))
    policy = json.loads(json.dumps(name_list.DEFAULT_POLICY))
    table = _config.load_config().get("session_names", {})
    for key in ("min_name_length", "max_name_length"):
        value = table.get(key) if isinstance(table, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            policy["limits"][key] = value
    return policy


def dry_run_snapshot(registry: Registry, snapshot: Snapshot) -> tuple[Snapshot, list[str]]:
    """``snapshot`` for a read-only plan, and what to tell the reader about it.

    With no published generation it plans with the name list this version ships,
    under the policy ``ai sync-names`` would publish (:func:`local_policy`), and
    says so. A real run keeps refusing through :attr:`Snapshot.policy`.
    """
    if snapshot.generation is not None:
        return snapshot, []
    policy = local_policy(registry)
    source = policy_source(registry)
    notes = [
        f"the registry has no {GENERATION_JSON}, so this plan uses the name list this version ships, under the "
        f"policy in {source}; a real run needs `ai sync-names` to publish a list first"
    ]
    shipped = json.loads((name_list.DATA_DIR / name_list.GENERATION_FILE).read_text(encoding="utf-8"))
    if shipped.get("policy_digest") != name_list.policy_digest(policy):
        notes.append(
            f"the shipped list was generated under a different policy than {source}, so the names "
            "`ai sync-names` publishes may differ from this plan's"
        )
    names = name_list.read_names(name_list.DATA_DIR / name_list.NAMES_FILE)
    statuses = snapshot.statuses
    if statuses is not None:
        # The plan treats the shipped names as a local install does: approved.
        statuses = {**{name: {"status": "approved"} for name in names}, **statuses}
    return replace(snapshot, names=names, generation={"policy": policy}, statuses=statuses), notes


# --- release ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Absence:
    """Both release tests for one row: each ``present``, ``absent`` or ``unknown``, with why."""

    worktree: str
    worktree_reason: str
    transcript: str
    transcript_reason: str

    @property
    def releasable(self) -> bool:
        return self.worktree == "absent" and self.transcript == "absent"

    def held_because(self) -> str:
        reasons = []
        if self.worktree != "absent":
            reasons.append(f"worktree {self.worktree}: {self.worktree_reason}")
        if self.transcript != "absent":
            reasons.append(f"transcript {self.transcript}: {self.transcript_reason}")
        return "; ".join(reasons)


def _registered_worktree_paths(repo: Path) -> set[str] | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    return {line[len("worktree ") :] for line in result.stdout.splitlines() if line.startswith("worktree ")}


def worktree_path(row: dict, projects_root: Path) -> Path | None:
    """The worktree a row names, rebuilt on this machine from its relative path."""
    if not row.get("worktree") or not row.get("repo"):
        return None
    return projects_root / row["repo"] / row["worktree"]


def probe_absence(row: dict, projects_root: Path, adapters: dict[str, object]) -> Absence:
    """Run both release tests for ``row``, each on its own: the worktree, then the client transcript."""
    from .session_clients import UNKNOWN

    path = worktree_path(row, projects_root)
    repo = projects_root / str(row.get("repo") or "")
    if path is None:
        worktree, worktree_reason = "absent", "the session never had a worktree"
    elif path.exists():
        worktree, worktree_reason = "present", str(path)
    else:
        registered = _registered_worktree_paths(repo) if repo.is_dir() else set()
        if registered is None:
            worktree, worktree_reason = "unknown", f"`git worktree list` failed in {repo}"
        elif any(Path(item).resolve(strict=False) == path.resolve(strict=False) for item in registered):
            worktree, worktree_reason = "present", f"{path} is still registered with git"
        else:
            worktree, worktree_reason = "absent", f"{path} is gone from disk and from git"
    client = adapters.get(str(row.get("client")))
    if client is None:
        transcript, transcript_reason = UNKNOWN, f"no store adapter for client {row.get('client')!r}"
    else:
        target = path.resolve(strict=False) if path is not None else None
        probe = client.probe_transcript(target, str(row.get("title")))  # type: ignore[attr-defined]
        transcript, transcript_reason = probe.state, probe.reason
    return Absence(worktree, worktree_reason, transcript, transcript_reason)


@dataclass
class ReconcileReport:
    published: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)
    spares_released: list[str] = field(default_factory=list)
    spares_added: list[str] = field(default_factory=list)
    held: dict[str, str] = field(default_factory=dict)
    recovered: list[str] = field(default_factory=list)
    top_up_skipped: str | None = None


def _renaming(snapshot: Snapshot, row: dict) -> bool:
    """Whether a rename recorded as ``intent`` still needs this row, as its old or its new title."""
    return any(
        record.get("status") == "intent" and row.get("title") in (record.get("old_title"), record.get("new_title"))
        for record in snapshot.migrations.values()
    )


def _over_limits(name: str, policy: dict) -> bool:
    low, high = name_list.limits(policy)
    return not low <= len(name) <= high


def reconcile(
    registry: Registry,
    machine: str,
    *,
    projects_root: Path,
    adapters: dict[str, object],
    host_alias: str,
    title: str | None = None,
    dry_run: bool = False,
    recover: Callable[[], list[str]] | None = None,
) -> ReconcileReport:
    """This machine's reconcile pass: publish the spool, recover migrations, release, top up spares.

    The spool is published and migrations recovered under ``spool.lock``, which is then
    let go: the worktree and transcript probes read client stores and can take minutes,
    and an offline launch waiting on the same lock must not wait on them. Each row the
    probes find releasable is remembered with the blob it was tested at, and the
    release deletes it only if the tip still holds that exact blob, so a row that
    changed after its test is held until a later pass tests it again.

    ``spool.lock`` is taken again from before the spool is read for the release until
    the release push lands or gives up, so an offline launch can never activate a spare
    between the final check and its release. Only rows whose ``machine`` is this host
    are ever edited or deleted.
    """
    report = ReconcileReport()
    if not dry_run:
        with spool_lock(lock_wait(registry)):
            report.published = publish_spool(registry, machine)
            if recover is not None:
                report.recovered = recover()
    tested = registry.snapshot()
    candidates: dict[str, str] = {}
    for token, row in tested.rows.items():
        if row.get("machine") != machine or row["kind"] == "spare":
            continue
        if title is not None and row.get("title") != title:
            continue
        if row.get("state") == "migration-incomplete":
            report.held[token] = "migration-incomplete: run the same `ai migrate-session` command again"
            continue
        if _renaming(tested, row):
            report.held[token] = "a recorded rename of this session has not completed"
            continue
        absence = probe_absence(row, projects_root, adapters)
        if absence.releasable:
            candidates[token] = tested.blobs.get(token, "")
        else:
            report.held[token] = absence.held_because()
    with spool_lock(lock_wait(registry)):
        snapshot = registry.snapshot()
        spool = read_spool()
        for token, blob in list(candidates.items()):
            if snapshot.blobs.get(token) != blob:
                del candidates[token]
                report.held[token] = "the row changed or was removed after its worktree and transcript were tested"
        spares: dict[str, str] = {}
        if title is None:
            policy = snapshot.policy
            for row in snapshot.rows_of(machine, "spare"):
                # A cutover-quarantined spare stays held until the cutover reconciles it.
                if row.get("state") == QUARANTINED:
                    continue
                if row["token"] not in spool and _over_limits(row["token"], policy):
                    spares[row["token"]] = snapshot.blobs.get(row["token"], "")
        if dry_run:
            report.released = sorted(candidates)
            report.spares_released = sorted(spares)
            return report
        if not candidates and not spares and title is not None:
            return report

        def build(tip: Snapshot) -> Change | None:
            writes: dict[str, bytes | None] = {}
            current_spool = read_spool()
            released, spares_out = [], []
            for token, blob in candidates.items():
                row = tip.rows.get(token)
                if (
                    row is not None
                    and tip.blobs.get(token) == blob
                    and row.get("machine") == machine
                    and row.get("state") != "migration-incomplete"
                    and not _renaming(tip, row)
                ):
                    writes[row_path(token)] = None
                    released.append(token)
            policy = tip.policy
            for token, blob in spares.items():
                row = tip.rows.get(token)
                if (
                    row is not None
                    and row["kind"] == "spare"
                    and row.get("state") != QUARANTINED
                    and tip.blobs.get(token) == blob
                    and row.get("machine") == machine
                    and token not in current_spool
                    and _over_limits(token, policy)
                ):
                    writes[row_path(token)] = None
                    spares_out.append(token)
            held = held_tokens(tip, current_spool) - set(released) - set(spares_out)
            remaining = replace(
                tip, rows={t: r for t, r in tip.rows.items() if t not in released and t not in spares_out}
            )
            slot = Slot(repo="", repo_alias="", host_alias=host_alias, label=None, machine=machine, client="")
            try:
                top_up, added = _spare_writes(remaining, slot, frozenset(held), None, None)
            except NameStatusError as exc:
                top_up, added = {}, []
                report.top_up_skipped = f"{exc}; no unverified spare was created"
            writes.update(top_up)
            if not writes:
                return None
            report.released, report.spares_released, report.spares_added = released, spares_out, added
            summary = ", ".join([*released, *spares_out]) or "none"
            return Change(writes, f"session-names: release {summary} ({machine})")

        registry.transact(build)
    return report


def _release_spare(registry: Registry, name: str, machine: str) -> None:
    """Release an unactivated spare by its own test: no offline launch has taken it.

    A spare has no repository, worktree, client or transcript, so the worktree and transcript
    tests have nothing to probe and would refuse every spare. What can still use a spare is an
    offline launch that took it into the host-local spool before the registry was reachable;
    that is checked under the spool lock, the lock a launch holds while it takes one.
    """
    with spool_lock(lock_wait(registry)):
        if name in read_spool():
            raise WriteRefusedError(
                f"{name} was taken by an offline launch that has not been published yet; "
                "run `ai reconcile-names` to publish it, then release the session it became"
            )
        seen = registry.snapshot()
        blob = seen.blobs.get(name)

        def build(tip: Snapshot) -> Change:
            current = tip.rows.get(name)
            if (
                current is None
                or current.get("kind") != "spare"
                or current.get("machine") != machine
                or tip.blobs.get(name) != blob
            ):
                raise WriteRefusedError(f"the row for {name} changed since it was tested; nothing was released")
            return Change({row_path(name): None}, f"session-names: release spare {name} ({machine})")

        registry.transact(build)


def release_name(
    registry: Registry, name: str, machine: str, *, projects_root: Path, adapters: dict[str, object]
) -> None:
    """Release one row by hand, after the same two absence tests a reconcile runs. There is no bypass."""
    snapshot = registry.snapshot()
    row = snapshot.rows.get(name)
    if row is None:
        raise WriteRefusedError(f"no session holds {name}")
    if row.get("machine") != machine:
        raise WriteRefusedError(
            f"{name} is held by {snapshot.holder(name)}; only {row.get('machine')} can test its worktree and transcript"
        )
    if row.get("state") == "migration-incomplete":
        raise WriteRefusedError(f"{name} is migration-incomplete; run the same `ai migrate-session` command again")
    if row.get("kind") == "spare":
        _release_spare(registry, name, machine)
        return
    absence = probe_absence(row, projects_root, adapters)
    if not absence.releasable:
        raise WriteRefusedError(f"{name} is still held: {absence.held_because()}")
    blob = snapshot.blobs.get(name)

    def build(tip: Snapshot) -> Change:
        current = tip.rows.get(name)
        if current is None or tip.blobs.get(name) != blob:
            raise WriteRefusedError(f"the row for {name} changed since it was tested; nothing was released")
        return Change({row_path(name): None}, f"session-names: release {name} ({row.get('title')}@{machine})")

    registry.transact(build)


def reassign_name(registry: Registry, name: str, to: str, reason: str, *, known_hosts: frozenset[str]) -> None:
    """Move a row's reconciliation to another host id. The name stays held; the reason is in the commit."""
    if to not in known_hosts:
        raise WriteRefusedError(
            f"{to!r} is not a host id in the machine host alias registry ({', '.join(sorted(known_hosts)) or 'none'})"
        )
    seen = registry.snapshot()
    if name not in seen.rows:
        raise WriteRefusedError(f"no session holds {name}")
    blob = seen.blobs.get(name)
    owner = seen.rows[name].get("machine")

    def build(tip: Snapshot) -> Change:
        row = tip.rows.get(name)
        if row is None or tip.blobs.get(name) != blob or row.get("machine") != owner:
            raise WriteRefusedError(f"the row for {name} changed since it was read; nothing was reassigned")
        return Change(
            {row_path(name): checked_row({**row, "machine": to})},
            f"session-names: reassign {name} from {owner} to {to}: {reason}",
        )

    registry.transact(build)


# --- the over-limit check ------------------------------------------------------------------


@dataclass
class CheckReport:
    over_limit: list[dict] = field(default_factory=list)
    spares_over_limit: list[dict] = field(default_factory=list)
    grandfathered: list[dict] = field(default_factory=list)
    incomplete: list[tuple[dict, dict | None]] = field(default_factory=list)
    stale_intents: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def failing(self) -> bool:
        return bool(self.over_limit or self.spares_over_limit)


STALE_INTENT_SECONDS = 30 * 60


def check_names(snapshot: Snapshot, *, now: datetime | None = None) -> CheckReport:
    """Every held name outside the published limits, grouped the way ``ai check-names`` prints them."""
    policy = snapshot.policy
    low, high = name_list.limits(policy)
    report = CheckReport()
    for row in sorted(snapshot.rows.values(), key=lambda r: (str(r.get("machine")), r["token"])):
        machine = str(row.get("machine"))
        report.counts[machine] = report.counts.get(machine, 0) + 1
        token = row["token"]
        if row["kind"] == "session":
            if len(token) > high:
                report.over_limit.append(row)
            elif len(token) < low:
                report.grandfathered.append(row)
            if row.get("state") == "migration-incomplete":
                intent = next((m for m in snapshot.migrations.values() if m.get("new_title") == row.get("title")), None)
                report.incomplete.append((row, intent))
        elif row["kind"] == "spare" and len(token) > high:
            report.spares_over_limit.append(row)
    moment = now or datetime.now(UTC)
    incomplete_titles = {row.get("title") for row, _intent in report.incomplete}
    for record in snapshot.migrations.values():
        if record.get("status") != "intent" or record.get("new_title") in incomplete_titles:
            continue
        try:
            started = datetime.strptime(str(record.get("started_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            started = moment
        if (moment - started).total_seconds() > STALE_INTENT_SECONDS:
            report.stale_intents.append(record)
    return report


# --- the rename ledger ----------------------------------------------------------------------


def migration_record(old_title: str, new_title: str, machine: str, client: str) -> dict:
    return {
        "schema": 1,
        "old_title": old_title,
        "new_title": new_title,
        "machine": machine,
        "client": client,
        "status": "intent",
        "started_at": utc_now(),
        "completed_at": None,
        "writer": writer(),
    }


def reserve_for_migration(
    registry: Registry,
    slot: Slot,
    old_title: str,
    *,
    name: str | None,
    max_length: int | None,
    words: frozenset[str],
    rng=None,
) -> Reservation:
    """Reserve the migration's new name and declare the rename as ``intent``, in one commit."""
    from . import session_naming

    def build_for(locked: bool) -> Callable[[Snapshot], Change]:
        return lambda snapshot: build(snapshot, locked)

    def build(snapshot: Snapshot, locked: bool) -> Change:
        if name is None:
            request, warnings = pick_request(
                registry, snapshot, slot, max_length=max_length, words=words, session=old_title
            )
            pick = session_naming.pick_name(request, rng)
            chosen, tier = pick.name, pick.tier
        else:
            spool = read_spool()
            verdict, reason = check_hand_chosen(snapshot, name, slot, words, spool=spool, session=old_title)
            if verdict == "spare":
                if not locked:
                    raise _SpoolLockNeeded
                row, writes, added = _take_own_spare(snapshot, name, slot, spool, words)
                writes[migration_path(old_title)] = encode_json(
                    migration_record(old_title, row["title"], slot.machine, slot.client)
                )
                return Change(
                    writes,
                    f"session-names: activate {name} ({row['title']}@{slot.machine}) for the rename of {old_title}",
                    result=Reservation(name, row["title"], row, spares_added=added),
                )
            if verdict != "ok":
                raise HandChosenRefusedError(
                    verdict, reason if verdict != "unknown" else f"{reason}; add it with `ai add-name {name}` first"
                )
            chosen, tier, warnings = name, None, []
        title = slot.title_for(chosen)
        row = session_row(
            name=chosen,
            title=title,
            repo=slot.repo,
            repo_alias=slot.repo_alias,
            host_alias=slot.host_alias,
            label=slot.label,
            machine=slot.machine,
            client=slot.client,
            source="migrated",
            tier=tier,
            worktree=slot.worktree_for(title),
        )
        record = migration_record(old_title, title, slot.machine, slot.client)
        return Change(
            {row_path(chosen): checked_row(row), migration_path(old_title): encode_json(record)},
            f"session-names: reserve {chosen} ({title}@{slot.machine}) for the rename of {old_title}",
            result=Reservation(chosen, title, row, warnings=warnings),
        )

    return _transact_hand_chosen(registry, build_for).result  # type: ignore[return-value]


def complete_migration(registry: Registry, old_title: str, machine: str) -> bool:
    """Mark the rename ``complete``, set its row active, and release a human-named old row, in one commit.

    Writes nothing when the record is already ``complete``; returns whether it wrote.
    """

    def build(snapshot: Snapshot) -> Change | None:
        record = snapshot.migrations.get(old_title)
        if record is None:
            raise WriteRefusedError(f"no rename of {old_title} is recorded")
        if record.get("status") == "complete":
            return None
        if record.get("machine") != machine:
            raise WriteRefusedError(f"the rename of {old_title} belongs to {record.get('machine')}")
        new_token = str(record["new_title"]).rsplit("-", 1)[-1]
        row = snapshot.rows.get(new_token)
        if row is None or row.get("title") != record["new_title"]:
            raise WriteRefusedError(f"the row for {record['new_title']} is not as this migration wrote it")
        writes: dict[str, bytes | None] = {
            migration_path(old_title): encode_json({**record, "status": "complete", "completed_at": utc_now()}),
            row_path(new_token): checked_row({**row, "state": "active", "recovery": None}),
        }
        old_token = old_title.rsplit("-", 1)[-1]
        old_row = snapshot.rows.get(old_token)
        if old_row is not None and old_row.get("title") == old_title and old_row.get("machine") == machine:
            writes[row_path(old_token)] = None
        return Change(writes, f"session-names: complete the rename of {old_title} to {record['new_title']}")

    return registry.transact(build) is not None


def release_failed_migration(registry: Registry, old_title: str, machine: str) -> None:
    """After a failed rename whose rollback is proved, release the new row and delete its intent record."""

    def build(snapshot: Snapshot) -> Change:
        record = snapshot.migrations.get(old_title)
        if record is None or record.get("status") != "intent" or record.get("machine") != machine:
            raise WriteRefusedError(f"the intent record for {old_title} is not as this migration wrote it")
        new_token = str(record["new_title"]).rsplit("-", 1)[-1]
        writes: dict[str, bytes | None] = {migration_path(old_title): None}
        row = snapshot.rows.get(new_token)
        if row is not None and row.get("state") == "migration-incomplete":
            raise WriteRefusedError(
                f"{new_token} is migration-incomplete: its recorded survivors are under {record['new_title']}, so it "
                "is not released; run the same `ai migrate-session` command again"
            )
        if row is not None and row.get("title") == record["new_title"] and row.get("machine") == machine:
            writes[row_path(new_token)] = None
        return Change(writes, f"session-names: release {new_token} after the failed rename of {old_title}")

    registry.transact(build)


def mark_migration_incomplete(registry: Registry, old_title: str, machine: str, survivors: list[dict]) -> None:
    """Hold the new row as ``migration-incomplete`` with what survived under the new title."""

    def build(snapshot: Snapshot) -> Change:
        record = snapshot.migrations.get(old_title)
        if record is None or record.get("machine") != machine:
            raise WriteRefusedError(f"no rename of {old_title} is recorded for {machine}")
        new_token = str(record["new_title"]).rsplit("-", 1)[-1]
        row = snapshot.rows.get(new_token)
        if row is None:
            raise WriteRefusedError(f"the row for {record['new_title']} is gone")
        updated = {**row, "state": "migration-incomplete", "recovery": survivors}
        writes: dict[str, bytes | None] = {row_path(new_token): checked_row(updated)}
        if record.get("status") == "complete":
            writes[migration_path(old_title)] = encode_json({**record, "status": "intent", "completed_at": None})
        return Change(
            writes,
            f"session-names: hold {new_token} as migration-incomplete ({record['new_title']}@{machine})",
        )

    registry.transact(build)
