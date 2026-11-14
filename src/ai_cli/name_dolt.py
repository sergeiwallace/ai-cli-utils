"""The fleet's session-name registry as a Dolt database on its own ref of the fleet repository.

Each host keeps a clone under the package's data directory and reaches it only through
the ``dolt`` program, one process per call, never a ``dolt sql-server``. Every process
gets ``DOLT_REMOTE_INFO_BRANCH`` set to empty, so the registry pushes no visible branch,
and a ``DOLT_ROOT_PATH`` whose global config turns usage metrics off.

Every write follows the claim protocol (:meth:`DoltRegistry.transact`): under the host
file lock, fetch and reset to the remote tip; run the change as guarded statements and
check each affected exactly one row; commit; confirm the commit; push without force; on
a rejected push reset and start again; after a push, fetch and read the write back
from the remote tip before it counts.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import portalocker

from . import config as _config
from . import name_registry as reg
from . import name_schema as schema
from .git_repair import _GIT_TARGETING_VARS

DEFAULT_DOLT_REF = "refs/dolt/session-names"
INSTALL_HINT = "install Dolt (https://docs.dolthub.com/introduction/installation) and run the command again"
_REJECTED = re.compile(r"\[rejected\]|non-fast-forward|fetch first", re.IGNORECASE)
_VERSION = re.compile(r"dolt version (\d+)\.(\d+)\.(\d+)")
ROOT_CONFIG = {
    "metrics.disabled": "true",
    "versioncheck.disabled": "true",
}
# Dolt reads a git remote through a bare git cache inside the clone. After each fetch git
# starts `git maintenance run --auto --detach`, whose repack deletes packs from that cache
# while Dolt's next `git cat-file` is still reading them ("packfile ... index unavailable").
# The host lock cannot cover a detached process, so the git that Dolt runs does no housekeeping.
GIT_HOUSEKEEPING_OFF = (("gc.auto", "0"), ("maintenance.auto", "false"))


def dolt_remote_url(url: str) -> str:
    """The Dolt git-remote URL for a git remote URL: ``git+`` and a scheme Dolt reads."""
    if url.startswith("git+"):
        return url
    if re.match(r"^[a-z][a-z0-9+.-]*://", url):
        return f"git+{url}"
    scp = re.match(r"^([^@/]+@)?([^:/]+):(.+)$", url)
    if scp and not re.match(r"^[A-Za-z]:[\\/]", url):
        return f"git+ssh://{scp.group(1) or ''}{scp.group(2)}/{scp.group(3)}"
    return f"git+{Path(url).resolve().as_uri()}"


def data_dir() -> Path:
    return _config.get_xdg_data_home()


class DoltRegistry(reg.FleetRegistry):
    """The fleet registry on Dolt."""

    policy_schemas = (2,)

    def __init__(
        self,
        repo: Path,
        *,
        dolt_ref: str = DEFAULT_DOLT_REF,
        remote: str,
        policy_file: Path,
        timeout_s: float,
        attempts: int,
        path: Path | None = None,
    ) -> None:
        super().__init__(repo, remote=remote, policy_file=policy_file, timeout_s=timeout_s, attempts=attempts)
        self.dolt_ref = dolt_ref
        self.path = path or data_dir() / "session-names.dolt"
        self.root = self.path.parent / f"{self.path.name}-root"
        self.location = f"{remote} {dolt_ref} ({self.path})"
        self.last_claimants: dict[str, str] = {}
        self._version_checked = False
        self._root_ready = False

    # -- processes
    def dolt_env(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in _GIT_TARGETING_VARS}
        env.update({"DOLT_REMOTE_INFO_BRANCH": "", "DOLT_ROOT_PATH": str(self.root), "GIT_TERMINAL_PROMPT": "0"})
        try:
            count = max(0, int(env.get("GIT_CONFIG_COUNT") or 0))
        except ValueError:
            count = 0
        for key, value in GIT_HOUSEKEEPING_OFF:
            env[f"GIT_CONFIG_KEY_{count}"] = key
            env[f"GIT_CONFIG_VALUE_{count}"] = value
            count += 1
        env["GIT_CONFIG_COUNT"] = str(count)
        return env

    def _ensure_root(self) -> None:
        """Write the global config before any ``dolt`` process can run without it."""
        if self._root_ready:
            return
        config_file = self.root / ".dolt" / "config_global.json"
        try:
            current = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        wanted = {**ROOT_CONFIG, **self._identity_config()}
        if any(current.get(key) != value for key, value in wanted.items()):
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(json.dumps({**current, **wanted}, sort_keys=True), encoding="utf-8")
        self._root_ready = True

    def _identity_config(self) -> dict[str, str]:
        name = self._git("config", "--get", "user.name", check=False).stdout.decode().strip()
        email = self._git("config", "--get", "user.email", check=False).stdout.decode().strip()
        return {"user.name": name or "ai-cli-utils", "user.email": email or "ai-cli-utils@localhost"}

    def dolt(
        self,
        *args: str,
        input: str | None = None,
        timeout: float | None = None,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self._ensure_root()
        budget = timeout if timeout is not None else self.timeout_s
        try:
            result = subprocess.run(
                ["dolt", *args],
                input=input,
                capture_output=True,
                text=True,
                timeout=budget,
                cwd=str(cwd or self.path),
                env=self.dolt_env(),
                check=False,
            )
        except FileNotFoundError as exc:
            raise reg.RegistryUnreachableError(f"the dolt program is not installed: {INSTALL_HINT}") from exc
        except subprocess.TimeoutExpired as exc:
            raise reg.RegistryUnreachableError(f"`dolt {args[0]}` did not finish within {budget:g} s") from exc
        if check and result.returncode != 0:
            raise reg.RegistryError(f"dolt {' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()}")
        return result

    def check_version(self) -> None:
        if self._version_checked:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        output = self.dolt("version", cwd=self.root).stdout
        match = _VERSION.search(output)
        if not match:
            raise reg.RegistryError(f"cannot read the Dolt version from {output.strip()!r}: {INSTALL_HINT}")
        found = tuple(int(part) for part in match.groups())
        if found < schema.MIN_DOLT_VERSION:
            floor = ".".join(str(part) for part in schema.MIN_DOLT_VERSION)
            raise reg.RegistryError(
                f"Dolt {'.'.join(match.groups())} is older than {floor}, the oldest this registry supports: "
                "upgrade Dolt"
            )
        self._version_checked = True

    # -- the clone
    def remote_url(self) -> str:
        result = self._git("remote", "get-url", self.remote, check=False)
        url = result.stdout.decode().strip()
        if result.returncode != 0 or not url:
            raise reg.RegistryError(f"{self.repo} has no git remote {self.remote!r}")
        return dolt_remote_url(url)

    def _url_file(self) -> Path:
        return self.path.parent / f"{self.path.name}.remote"

    def has_clone(self) -> bool:
        return (self.path / ".dolt").is_dir()

    def _ensure_clone(self) -> None:
        self.check_version()
        url = self.remote_url()
        if not self.has_clone():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            result = self.dolt(
                "clone", "--ref", self.dolt_ref, url, str(self.path),
                cwd=self.path.parent, timeout=self.fetch_timeout_s(), check=False,
            )  # fmt: skip
            if result.returncode != 0:
                raise reg.RegistryUnreachableError(
                    f"could not clone the registry database {self.dolt_ref} from {url}: "
                    f"{(result.stderr or result.stdout).strip()}"
                )
            self._url_file().write_text(url, encoding="utf-8")
            return
        recorded = self._url_file().read_text(encoding="utf-8").strip() if self._url_file().is_file() else ""
        if recorded != url:
            self.dolt("remote", "remove", "origin", check=False)
            self.dolt("remote", "add", "--ref", self.dolt_ref, "origin", url)
            self._url_file().write_text(url, encoding="utf-8")

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """The host file lock: one package process opens the database at a time."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with portalocker.Lock(
                str(self.path.parent / f"{self.path.name}.lock"), mode="a", timeout=self.timeout_s * self.attempts
            ):
                yield
        except portalocker.exceptions.LockException as exc:
            raise reg.RegistryContendedError(
                f"the registry database {self.path} stayed locked; nothing was written"
            ) from exc

    def initialize(self, *, writes_enabled: bool = False) -> None:
        """Create the database on the remote ref: the schema and its meta rows, in one pushed commit."""
        with self.locked():
            self.check_version()
            if self.has_clone():
                raise reg.WriteRefusedError(f"{self.path} already holds a registry database")
            self.path.mkdir(parents=True, exist_ok=True)
            self.dolt("init", "-b", "main")
            script = ";\n".join(schema.ddl("dolt")) + ";\n"
            script += "".join(
                f"insert into meta (k, v) values ({literal(k)}, {literal(v)});\n"
                for k, v in schema.initial_meta(writes_enabled=writes_enabled).items()
            )
            script += "call dolt_add('-A');\ncall dolt_commit('-m', 'session-names: create the registry database');\n"
            self.dolt("sql", input=script)
            url = self.remote_url()
            self.dolt("remote", "add", "--ref", self.dolt_ref, "origin", url)
            self._url_file().write_text(url, encoding="utf-8")
            pushed = self.dolt("push", "origin", "main", timeout=self.push_timeout_s(), check=False)
            if pushed.returncode != 0:
                raise reg.RegistryUnreachableError(
                    f"could not create {self.dolt_ref}: {(pushed.stderr or pushed.stdout).strip()}"
                )

    # -- reading
    def sql_rows(self, script: str) -> list[list[dict]]:
        """Every result set of ``script``, in order; raises with Dolt's message on an error."""
        result = self.dolt("sql", "-r", "json", input=script, check=False)
        if result.returncode != 0:
            raise reg.WriteRefusedError(f"the registry database refused the statement: {result.stderr.strip()}")
        sets = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                sets.append(json.loads(line).get("rows", []))
        return sets

    def _read(self, ref: str) -> tuple[schema.Tables, str | None]:
        script = f"select hashof({literal(ref)}) as h;\n" + "".join(
            f"select * from {table.name} as of {literal(ref)};\n" for table in schema.TABLES
        )
        try:
            sets = self.sql_rows(script)
        except reg.WriteRefusedError as exc:
            raise reg.RegistryError(f"cannot read the registry database {self.path}: {exc}") from exc
        tip = sets[0][0]["h"] if sets and sets[0] else None
        rows = {table.name: sets[index + 1] for index, table in enumerate(schema.TABLES)}
        tables = schema.Tables.from_rows(rows)
        self._check_reader(tables)
        return tables, tip

    def _check_reader(self, tables: schema.Tables) -> None:
        needed = int(tables.meta.get("min_reader_version", "0") or 0)
        if needed > schema.READER_VERSION:
            raise reg.RegistryError(
                f"the registry database needs reader version {needed}, above this package's "
                f"{schema.READER_VERSION}: update ai-cli-utils"
            )

    def _fetch(self) -> None:
        result = self.dolt("fetch", "origin", timeout=self.fetch_timeout_s(), check=False)
        if result.returncode != 0:
            raise reg.RegistryUnreachableError(
                f"could not fetch {self.dolt_ref} from {self.remote}: {(result.stderr or result.stdout).strip()}"
            )

    def snapshot(self, *, fetch: bool = True) -> reg.Snapshot:
        with self.locked():
            if not fetch and not self.has_clone():
                return reg.Snapshot(
                    tip=None, fetched=False, statuses={}, verdicts={}, policy_schemas=self.policy_schemas
                )
            self._ensure_clone()
            if fetch:
                self._fetch()
                self._reset()
            # The local branch is the last fetched tip: every fetch is followed by a reset to it,
            # and it survives a remote re-added for a new URL, which drops the tracking ref.
            tables, tip = self._read("main")
            return tables.snapshot(tip, fetched=fetch, policy_schemas=self.policy_schemas)

    # -- writing
    def _reset(self) -> None:
        self.dolt("reset", "--hard", "origin/main", check=False)

    def _apply(self, compiled: schema.Compiled, message: str) -> str:
        """Run the guarded statements, check every count, commit; the new commit's hash."""
        script = "".join(f"{render(statement)};\nselect row_count() as n;\n" for statement in compiled.statements)
        try:
            counts = [rows[0]["n"] for rows in self.sql_rows(script)] if script else []
        except reg.WriteRefusedError:
            self._reset()
            raise
        for statement, count in zip(compiled.statements, counts, strict=True):
            if count != 1:
                self._reset()
                raise reg.WriteRefusedError(
                    f"the registry changed under this write ({statement.key} matched {count} rows, expected 1); "
                    "nothing was written"
                )
        committed = self.sql_rows(
            f"call dolt_add('-A');\ncall dolt_commit('-m', {literal(message)});\n"
            "select commit_hash from dolt_log limit 1;\n"
        )
        made = committed[1][0]["hash"] if len(committed) > 1 and committed[1] else None
        head = committed[-1][0]["commit_hash"] if committed and committed[-1] else None
        if not made or made != head:
            self._reset()
            raise reg.RegistryError("the registry write made no commit; nothing was pushed")
        return made

    def _push(self) -> bool:
        result = self.dolt("push", "origin", "main", timeout=self.push_timeout_s(), check=False)
        if result.returncode == 0:
            return True
        output = result.stdout + result.stderr
        if _REJECTED.search(output):
            return False
        raise reg.RegistryUnreachableError(f"could not push {self.dolt_ref} to {self.remote}: {output.strip()}")

    def transact(self, build: reg.Build, *, exempt: bool = False) -> reg.Change | None:
        """One write under the claim protocol; ``exempt`` lets import and seed write while writes are off."""
        with self.locked():
            self._ensure_clone()
            for _attempt in range(self.attempts):
                self._fetch()
                self._reset()
                tables, tip = self._read("main")
                if tables.meta.get("writes_enabled") != "1" and not exempt:
                    raise reg.WriteRefusedError(
                        "the registry database has writes turned off (a cutover is in progress); nothing was written"
                    )
                change = build(tables.snapshot(tip, fetched=True, policy_schemas=self.policy_schemas))
                if change is None or not change.writes:
                    return None
                compiled = schema.compile_change(tables, change, "dolt")
                self._apply(compiled, change.message)
                if not self._push():
                    self._reset()
                    continue
                self._fetch()
                after, _tip = self._read("origin/main")
                problems = schema.postcondition_problems(
                    compiled, after.snapshot(_tip, fetched=True, policy_schemas=self.policy_schemas)
                )
                if problems:
                    raise reg.RegistryContendedError(
                        f"the push landed but the registry tip does not show this write: {'; '.join(problems)}"
                    )
                self.last_claimants = compiled.claimants
                return change
        raise reg.RegistryContendedError(
            f"the registry write lost the race to other writers {self.attempts} times in a row; nothing was written"
        )

    def _query(self, statement: schema.Statement) -> list[dict]:
        """``statement``'s rows at the freshly fetched remote tip."""
        with self.locked():
            self._ensure_clone()
            self._fetch()
            self._reset()
            try:
                return self.sql_rows(f"{render(statement)};\n")[0]
            except reg.WriteRefusedError as exc:
                raise reg.RegistryError(f"cannot query the registry database: {exc}") from exc

    def find_names(self, filters: dict[str, str | None]) -> list[dict]:
        return schema.found_names(self._query(schema.find_query(filters)))

    def history(self, name: str) -> list[dict]:
        return schema.changes_only(self._query(schema.history_query(name)))


def literal(value: object) -> str:
    """``value`` as a Dolt SQL literal; the CLI binds no parameters."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, dict | list):
        value = schema.canonical(value)
    text = str(value)
    if "\0" in text:
        raise reg.WriteRefusedError("a registry value cannot contain a NUL character")
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def render(statement: schema.Statement) -> str:
    """``statement`` with each ``?`` replaced by its value's literal."""
    pieces = statement.sql.split("?")
    if len(pieces) != len(statement.params) + 1:
        raise ValueError(f"{len(pieces) - 1} placeholders for {len(statement.params)} values in {statement.sql!r}")
    out = [pieces[0]]
    for value, piece in zip(statement.params, pieces[1:], strict=True):
        out += [literal(value), piece]
    return "".join(out)
