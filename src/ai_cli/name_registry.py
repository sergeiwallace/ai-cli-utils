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
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
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
}
POLICY_SCHEMA = 1


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


def require_known_policy(policy: dict) -> dict:
    """``policy`` itself, or :class:`PolicyUnknownError` naming what this version cannot apply."""
    schema = policy.get("schema", POLICY_SCHEMA)
    if schema != POLICY_SCHEMA:
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

    @property
    def policy(self) -> dict:
        """The published policy every allocation applies; refuses when none is usable."""
        if self.generation is None or not isinstance(self.generation.get("policy"), dict):
            raise NoNameListError(f"the registry has no {GENERATION_JSON}; run `ai sync-names` to publish one")
        return require_known_policy(self.generation["policy"])

    @property
    def available(self) -> frozenset[str]:
        """Every name that may be used: the generated list plus every name added by hand."""
        return frozenset(self.names or ()) | frozenset(self.manual)

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

    def __init__(self, *, timeout_s: float = DEFAULT_TIMEOUT_S, attempts: int = DEFAULT_ATTEMPTS) -> None:
        self.timeout_s = timeout_s
        self.attempts = attempts

    def snapshot(self, *, fetch: bool = True) -> Snapshot:
        raise NotImplementedError

    def transact(self, build: Build) -> Change | None:
        """Run ``build`` against the current tip and publish its change, retrying on a lost race.

        ``build`` is the operation's complete guard plus the writes it implies. It runs
        again from scratch against every new tip, so a precondition another writer broke
        in the meantime is seen and either refused or, for a picked name, picked again.
        Returns the published change, or None when ``build`` decided nothing needs writing.
        """
        raise NotImplementedError


class GitRegistry(Registry):
    """The shared registry: one ref of a sibling repository, written with plumbing."""

    kind = "fleet"

    def __init__(
        self, repo: Path, *, ref: str, remote: str, policy_file: Path, timeout_s: float, attempts: int
    ) -> None:
        super().__init__(timeout_s=timeout_s, attempts=attempts)
        self.repo = repo
        self.ref = ref
        self.remote = remote
        self.policy_file = policy_file
        self.location = f"{remote} {ref} ({repo})"

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

    def _fetch(self) -> str | None:
        result = self._git(
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--quiet",
            self.remote,
            f"+{self.ref}:{PRIVATE_REF}",
            timeout=self.timeout_s,
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
            "push", "--porcelain", "--quiet", self.remote, f"{commit}:{self.ref}", timeout=self.timeout_s, check=False
        )
        if result.returncode == 0:
            self._git("update-ref", PRIVATE_REF, commit, check=False)
            return True
        output = (result.stdout + result.stderr).decode(errors="replace")
        if _REJECTED_PUSH.search(output):
            return False
        raise RegistryUnreachableError(f"could not push {self.ref} to {self.remote}: {output.strip()}")

    def transact(self, build: Build) -> Change | None:
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


class LocalRegistry(Registry):
    """The registry of an install with no shared one: the same tree as files, under a lock."""

    kind = "local"

    def __init__(self, root: Path, *, timeout_s: float = DEFAULT_TIMEOUT_S, attempts: int = DEFAULT_ATTEMPTS) -> None:
        super().__init__(timeout_s=timeout_s, attempts=attempts)
        self.root = root
        self.location = str(root)

    def _seed(self) -> None:
        available = self.root / NAMES_TXT
        if available.exists():
            return
        available.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(name_list.DATA_DIR / name_list.NAMES_FILE, available)
        shutil.copyfile(name_list.DATA_DIR / name_list.GENERATION_FILE, self.root / GENERATION_JSON)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with portalocker.Lock(str(self.root / "registry.lock"), mode="a", timeout=self.timeout_s * self.attempts):
                yield
        except portalocker.exceptions.LockException as exc:
            raise RegistryContendedError(f"the local registry {self.root} stayed locked; nothing was written") from exc

    def _read(self) -> Snapshot:
        files: dict[str, bytes] = {}
        if self.root.is_dir():
            for path in self.root.rglob("*"):
                relative = path.relative_to(self.root).as_posix()
                if path.is_file() and not relative.endswith(".lock") and not path.name.startswith("."):
                    files[relative] = path.read_bytes()
        blobs = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
        return parse_tree(files, blobs, tip=None)

    def snapshot(self, *, fetch: bool = True) -> Snapshot:
        del fetch
        with self._locked():
            self._seed()
            return self._read()

    def transact(self, build: Build) -> Change | None:
        with self._locked():
            self._seed()
            change = build(self._read())
            if change is None or not change.writes:
                return None
            for relative, data in change.writes.items():
                target = self.root / relative
                if data is None:
                    target.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, staged = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                Path(staged).replace(target)
            return change


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


def local_registry_root() -> Path:
    return _config.get_xdg_data_home() / "session-names"


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
            ref = policy.get("registry_ref")
            remote = policy.get("registry_remote", "origin")
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
    return Discovery(LocalRegistry(local_registry_root()))


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
    return str(registry.policy_file) if isinstance(registry, GitRegistry) else "the local config"


def pick_request(
    registry: Registry,
    snapshot: Snapshot,
    slot: Slot,
    *,
    spool: dict[str, dict] | None = None,
    max_length: int | None = None,
    words: frozenset[str] | None = None,
    extra_held: frozenset[str] = frozenset(),
):
    """The picker's request for ``slot`` under the published policy, and any warning it raised."""
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
    )
    return request, [warning] if warning else []


def _spare_writes(
    snapshot: Snapshot, slot: Slot, held: frozenset[str], words: frozenset[str] | None, rng
) -> tuple[dict[str, bytes | None], list[str]]:
    """Rows that bring this machine's spares up to ``offline_spares``, each passing the picker's guard."""
    from . import session_naming

    policy = snapshot.policy
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


def check_hand_chosen(snapshot: Snapshot, name: str, slot: Slot, words: frozenset[str]) -> tuple[str, str]:
    """``(verdict, reason)`` for a name chosen by hand, under the snapshot's published policy.

    The verdict is ``ok`` (known, free and in limits), ``unknown`` (free but not in the
    database), or the rule that refuses it: ``limits``, ``held``, ``confusable`` or ``word``.
    """
    from . import session_naming

    policy = snapshot.policy
    low, high = name_list.limits(policy)
    limits_text = f"the configured limit is {low} to {high} letters (config/session-names.toml [limits])"
    if not _TOKEN_RE.fullmatch(name):
        return "limits", f'"{name}" is not lowercase letters only; {limits_text}.'
    holder = snapshot.holder(name)
    if holder:
        return "held", f"{name} is held by {holder}"
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
            return "confusable", f"the title {title} and the held title {other} are a prefix pair"
    if is_common_word(name, policy, words):
        return "word", (
            f"{name} is a common English word; to allow it as a name, add it to keep_names in config/session-names.toml"
        )
    if not low <= len(name) <= high:
        return "limits", f'"{name}" is {len(name)} letters; {limits_text}.'
    if name in snapshot.available:
        return "ok", ""
    return "unknown", f"{name} is not in the name database"


def reserve_hand_chosen(
    registry: Registry, name: str, slot: Slot, *, add: bool, via: str, added_by: str, words: frozenset[str]
) -> Reservation:
    """Reserve a name chosen by hand, adding it to the database in the same commit when ``add``.

    The checks run again on every fetched tip; a name another writer made unusable in
    the meantime raises :class:`HandChosenRefusedError` with the reason the new tip gave.
    """

    def build(snapshot: Snapshot) -> Change:
        verdict, reason = check_hand_chosen(snapshot, name, slot, words)
        if verdict not in ("ok", "unknown") or (verdict == "unknown" and not add):
            raise HandChosenRefusedError(verdict, reason)
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

    change = registry.transact(build)
    assert change is not None
    return change.result  # type: ignore[return-value]


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
        low, high = name_list.limits(policy)
        if not _TOKEN_RE.fullmatch(name) or not low <= len(name) <= high:
            raise HandChosenRefusedError(
                "limits",
                f'"{name}" is {len(name)} letters; the configured limit is {low} to {high} letters '
                "(config/session-names.toml [limits]), lowercase letters only.",
            )
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


def take_spare(registry: Registry, slot: Slot) -> Reservation:
    """Activate this machine's shortest unactivated spare for ``slot`` while the registry is unreachable.

    Reads the last fetched tip, never the network. Holds ``spool.lock`` from reading the
    spool through the durable write of the activation, so two offline launches on one
    machine can never take the same spare.
    """
    with spool_lock(lock_wait(registry)):
        snapshot = registry.snapshot(fetch=False)
        spool = read_spool()
        free = sorted(
            (row for row in snapshot.rows_of(slot.machine, "spare") if row["token"] not in spool),
            key=lambda row: (len(row["token"]), row["token"]),
        )
        if not free:
            raise SparesExhaustedError(
                "the registry is unreachable and this machine has no unactivated spare name left; once online, "
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


def policy_revision(registry: GitRegistry) -> tuple[str | None, str | None]:
    """The commit that last changed the policy file in this checkout, and its committer time."""
    relative = registry.policy_file.relative_to(registry.repo).as_posix()
    result = registry._git("log", "-1", "--format=%H%x00%cI", "HEAD", "--", relative, check=False)
    text = result.stdout.decode().strip()
    if result.returncode != 0 or not text:
        return None, None
    commit, committed_at = text.split("\0", 1)
    return commit, committed_at


def ancestry_state(registry: GitRegistry, ref_commit: str | None, local_commit: str | None) -> str:
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
    if isinstance(registry, GitRegistry):
        with contextlib.suppress(RegistryError):
            registry._git("fetch", "--no-tags", "--quiet", registry.remote, timeout=registry.timeout_s, check=False)
        commit, committed_at = policy_revision(registry)
    generated: dict[str, name_list.Generation] = {}

    def decide(snapshot: Snapshot) -> tuple[str, bool]:
        if snapshot.generation is None:
            state = "local-newer"
        elif isinstance(registry, GitRegistry):
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
    if isinstance(registry, GitRegistry):
        return name_list.fleet_policy(read_policy_file(registry.policy_file))
    policy = json.loads(json.dumps(name_list.DEFAULT_POLICY))
    table = _config.load_config().get("session_names", {})
    for key in ("min_name_length", "max_name_length"):
        value = table.get(key) if isinstance(table, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            policy["limits"][key] = value
    return policy
