"""A shared session-name registry for tests: an environment checkout and its bare remote.

``build_fleet`` makes a projects directory holding one checkout of an environment
repository, with a bare remote under ``tmp_path``, and publishes a name list on the
registry. The registry is the backend :data:`BACKEND` names, which the parametrized
``registry_backend`` fixture sets for each test that asks for it:

``git``
    the git-tree store on a ref of the bare remote;
``dolt``
    the Dolt database on ``refs/dolt/session-names`` of the bare remote, each machine
    with its own clone, run by the real ``dolt`` program;
``sqlite``
    a local SQLite file shared by every "machine", with no remote and no policy file
    in the environment checkout.

Nothing here reaches the network or a real remote.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import textwrap
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ai_cli import config as _config
from ai_cli import name_list, session_naming
from ai_cli import name_registry as reg
from ai_cli.launch_reporter import LaunchReporter

REF = "refs/heads/fleet-session-names/registry"
DOLT_REF = "refs/dolt/session-names"
HOST = "box-one"
OTHER_HOST = "box-two"
HOST_ALIAS = "box"
REPO = "myrepo"
STEM = f"{REPO}-{HOST_ALIAS}"
NAMES = ["ada", "bob", "cyd", "bert", "cleo", "dora", "hugo", "iris", "jude", "kurt", "lola", "milo", "nora"]
BACKEND = "git"

POLICY_TOML = textwrap.dedent(
    f"""\
    schema = 1
    registry_ref = "{REF}"
    registry_remote = "origin"
    publish_timeout_s = 15
    publish_attempts = 5
    pick_floor = 10
    low_water = 2
    offline_spares = 1
    denylist = []

    [limits]
    min_name_length = 3
    max_name_length = 4

    [source]
    url = "https://example.com/names.tar.gz"
    digest = "sha256:{"0" * 64}"
    members = ["names/dist.male.first"]

    [confusable]
    words_url = "https://example.com/words.tgz"
    words_digest = "sha512-AAAA"
    words_member = "package/index.dic"
    max_edit_distance = 1
    extra_words = []
    keep_names = []

    [defaults]
    name_with_label = "keep"
    """
)


def dolt_policy(policy_toml: str) -> str:
    """``policy_toml`` as a fleet on the Dolt database writes it: schema 2 and the Dolt ref."""
    text = re.sub(r"(?m)^schema = 1$", "schema = 2", policy_toml)
    return re.sub(r'(?m)^registry_ref = ".*"$', f'registry_backend = "dolt"\nregistry_dolt_ref = "{DOLT_REF}"', text)


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def clone(remote: Path, target: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(target)], check=True, capture_output=True)
    git(target, "config", "user.email", "test@example.com")
    git(target, "config", "user.name", "Test")
    return target


def registry_for(repo: Path, *, backend: str | None = None, path: Path | None = None) -> reg.Registry:
    backend = backend or BACKEND
    if backend == "dolt":
        from ai_cli.name_dolt import DoltRegistry

        return DoltRegistry(
            repo,
            dolt_ref=DOLT_REF,
            remote="origin",
            policy_file=repo / reg.POLICY_MARKER,
            timeout_s=15,
            attempts=5,
            path=path,
        )
    if backend == "sqlite":
        from ai_cli.name_sqlite import SqliteRegistry

        return SqliteRegistry(path or reg.local_registry_path(), seed=False)
    return reg.GitRegistry(
        repo, ref=REF, remote="origin", policy_file=repo / reg.POLICY_MARKER, timeout_s=15, attempts=5
    )


def policy_of(repo: Path, policy_toml: str | None = None) -> dict:
    if policy_toml is not None and not (repo / reg.POLICY_MARKER).is_file():
        return name_list.fleet_policy(tomllib.loads(policy_toml))
    return name_list.fleet_policy(reg.read_policy_file(repo / reg.POLICY_MARKER))


def policy_commit(repo: Path) -> str | None:
    return git(repo, "log", "-1", "--format=%H", "--", "config/session-names.toml") or None


def status_writes(statuses: dict[str, str]) -> dict[str, bytes | None]:
    """The writes that give each name its general status, as ``ai set-name-status`` records it."""
    from ai_cli import name_schema

    entry = {"actor": "fixture", "recorded_by": "fixture", "set_at": "2026-01-01T00:00:00Z", "reason": None}
    return {
        name_schema.status_path(name): name_schema.canonical({**entry, "status": status}).encode()
        for name, status in sorted(statuses.items())
    }


def publish_list(
    registry: reg.Registry,
    names: list[str],
    policy: dict,
    commit: str | None,
    statuses: dict[str, str] | None = None,
) -> None:
    """Publish ``names``; on a database registry each gets a status, approved unless ``statuses`` says otherwise."""
    record = {
        "schema": 1,
        "policy": policy,
        "policy_digest": name_list.policy_digest(policy),
        "policy_commit": commit,
        "count": len(names),
    }
    writes: dict[str, bytes | None] = {
        reg.NAMES_TXT: name_list.render_names(sorted(names)).encode(),
        reg.GENERATION_JSON: name_list.render_record(record).encode(),
    }
    if not isinstance(registry, reg.GitRegistry):
        wanted = dict.fromkeys(names, "approved") if statuses is None else dict(statuses)
        writes.update(status_writes({name: status for name, status in wanted.items() if status != "unreviewed"}))
    registry.transact(lambda snapshot: reg.Change(writes, "session-names: generate (fixture)"), exempt=True)


def unrelated_write(registry: reg.Registry) -> dict[str, bytes | None]:
    """A write that touches no name, hold or rename, so a race with it changes nothing a guard reads."""
    if isinstance(registry, reg.GitRegistry):
        return {"README.md": b"racer\n"}
    from ai_cli import name_schema

    return {name_schema.meta_path("fixture_racer"): str(id(registry)).encode()}


def render_file(snapshot: reg.Snapshot, path: str) -> str | None:
    """The text the git-tree store would hold at ``path`` for ``snapshot``."""
    parts = Path(path)
    if path == reg.NAMES_TXT:
        return name_list.render_names(snapshot.names) if snapshot.names else None
    if path == reg.GENERATION_JSON:
        return name_list.render_record(snapshot.generation) if snapshot.generation else None
    table = {reg.NAMES_DIR: snapshot.rows, reg.MANUAL_DIR: snapshot.manual, reg.MIGRATIONS_DIR: snapshot.migrations}
    records = table.get(parts.parent.as_posix())
    if records is None or parts.stem not in records:
        return None
    return reg.encode_json(records[parts.stem]).decode()


def logical_files(snapshot: reg.Snapshot) -> dict[str, str]:
    """Every record of ``snapshot`` keyed by the path the git-tree store keeps it at."""
    paths = [reg.NAMES_TXT, reg.GENERATION_JSON]
    paths += [reg.row_path(token) for token in snapshot.rows]
    paths += [reg.manual_path(name) for name in snapshot.manual]
    paths += [reg.migration_path(title) for title in snapshot.migrations]
    files = {path: render_file(snapshot, path) for path in paths}
    return {path: text for path, text in files.items() if text is not None}


@dataclass
class Commit:
    id: str
    writes: dict[str, bytes | None]
    message: str


@dataclass
class Fleet:
    tmp: Path
    projects: Path
    remote: Path
    repo: Path
    registry: reg.Registry
    backend: str = "git"
    commits: list[Commit] = field(default_factory=list)

    # -- reading the shared store
    def snapshot(self) -> reg.Snapshot:
        if self.backend == "git":
            return self.registry.snapshot()
        observer = registry_for(self.repo, backend=self.backend, path=self._observer_path())
        return observer.snapshot()

    def _observer_path(self) -> Path | None:
        return self.tmp / "observer.dolt" if self.backend == "dolt" else None

    def rows(self) -> dict[str, dict]:
        if self.backend == "git":
            listing = git(self.remote, "ls-tree", "--name-only", f"{REF}:names", check=False)
            return {
                Path(path).stem: json.loads(git(self.remote, "show", f"{REF}:names/{path}"))
                for path in listing.splitlines()
                if path
            }
        return self.snapshot().rows

    def file(self, path: str, at: str | None = None) -> str | None:
        if self.backend == "git":
            result = subprocess.run(
                ["git", "-C", str(self.remote), "show", f"{at or REF}:{path}"],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.stdout if result.returncode == 0 else None
        if at is None:
            return render_file(self.snapshot(), path)
        for commit in reversed(self.commits[: self._index(at) + 1]):
            if path in commit.writes:
                data = commit.writes[path]
                return None if data is None else data.decode()
        return None

    def _index(self, commit: str) -> int:
        return next(index for index, item in enumerate(self.commits) if item.id == commit)

    def rows_at(self, commit: str) -> dict[str, dict]:
        """The held rows as of ``commit``."""
        if self.backend == "git":
            listing = git(self.remote, "ls-tree", "--name-only", f"{commit}:names")
            return {
                path.removesuffix(".json"): json.loads(git(self.remote, "show", f"{commit}:names/{path}"))
                for path in listing.splitlines()
                if path
            }
        rows: dict[str, dict] = {}
        for item in self.commits[: self._index(commit) + 1]:
            for path, data in item.writes.items():
                if path.startswith(f"{reg.NAMES_DIR}/"):
                    token = path[len(reg.NAMES_DIR) + 1 :].removesuffix(".json")
                    if data is None:
                        rows.pop(token, None)
                    else:
                        rows[token] = json.loads(data)
        return rows

    def tip(self) -> str:
        if self.backend == "git":
            return git(self.remote, "rev-parse", REF)
        if self.backend == "dolt":
            return git(self.remote, "rev-parse", DOLT_REF)
        return str(len(self.commits))

    def history(self) -> list[str]:
        """Every registry commit, newest first."""
        if self.backend == "git":
            return git(self.remote, "log", "--format=%H", REF).splitlines()
        return [commit.id for commit in reversed(self.commits)]

    def message(self, commit: str | None = None) -> str:
        if self.backend == "git":
            return git(self.remote, "log", "-1", "--format=%s", commit or REF)
        return (self.commits[self._index(commit)] if commit else self.commits[-1]).message

    def changed(self, commit: str | None = None) -> set[str]:
        if self.backend == "git":
            target = commit or self.tip()
            return set(git(self.remote, "diff-tree", "--no-commit-id", "--name-only", "-r", target).splitlines())
        if commit is None:
            # A write from another process never reaches the spy: compare the store itself.
            replayed: dict[str, str] = {}
            for item in self.commits:
                for path, data in item.writes.items():
                    if data is None:
                        replayed.pop(path, None)
                    else:
                        replayed[path] = data.decode()
            current = logical_files(self.snapshot())
            differs = {path for path in set(current) | set(replayed) if current.get(path) != replayed.get(path)}
            if differs:
                return differs
        found = self.commits[self._index(commit)] if commit else self.commits[-1]
        return set(found.writes)

    # -- the network
    def offline(self) -> None:
        if self.backend == "sqlite":
            pytest.skip("a local SQLite registry has no remote to lose")
        git(self.repo, "remote", "set-url", "origin", str(self.tmp / "missing.git"))

    def online(self) -> None:
        git(self.repo, "remote", "set-url", "origin", str(self.remote))

    def other_machine(self, name: str = "other-machine") -> reg.Registry:
        if self.backend == "sqlite":
            return registry_for(self.repo, backend="sqlite")
        checkout = clone(self.remote, self.tmp / name)
        path = self.tmp / f"{name}.dolt" if self.backend == "dolt" else None
        return registry_for(checkout, backend=self.backend, path=path)


def _spy_on_commits(monkeypatch, fleet: Fleet) -> None:
    """Record every change a database backend commits, as the git-tree store's history would show it."""
    cls = type(fleet.registry)
    original = cls.transact
    current: dict[str, bytes | None] = {}

    def transact(self, build, *, exempt: bool = False):
        change = original(self, build, exempt=exempt)
        if change is not None:
            # As a tree diff would, leave out a record rewritten with the bytes it already had.
            writes = {path: data for path, data in change.writes.items() if current.get(path, b"") != data}
            current.update(change.writes)
            fleet.commits.append(Commit(f"c{len(fleet.commits) + 1}", writes, change.message))
        return change

    monkeypatch.setattr(cls, "transact", transact)


def build_fleet(
    tmp_path: Path,
    monkeypatch,
    *,
    names: list[str] | None = None,
    policy_toml: str = POLICY_TOML,
    publish: bool = True,
    backend: str | None = None,
    statuses: dict[str, str] | None = None,
) -> Fleet:
    backend = backend or BACKEND
    if backend == "dolt":
        policy_toml = dolt_policy(policy_toml)
    projects = tmp_path / "projects"
    projects.mkdir()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test")
    if backend == "sqlite":
        (seed / "README").write_text("an environment repository with no shared registry\n")
    else:
        (seed / "config").mkdir()
        (seed / "config" / "session-names.toml").write_text(policy_toml)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "policy")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "main")
    repo = clone(remote, projects / "envrepo")
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr(_config, "_get_projects_dir", lambda: projects)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setattr(session_naming, "host_id", lambda: HOST)
    monkeypatch.setattr(reg, "alias_words", lambda: frozenset())
    monkeypatch.setattr(reg, "common_words", lambda policy, **_: frozenset({"may", "art", "sun"}))
    registry = registry_for(repo, backend=backend)
    fleet = Fleet(tmp=tmp_path, projects=projects, remote=remote, repo=repo, registry=registry, backend=backend)
    if backend == "dolt":
        registry.initialize(writes_enabled=True)  # type: ignore[attr-defined]
    if backend != "git":
        _spy_on_commits(monkeypatch, fleet)
    if publish:
        publish_list(
            registry,
            list(names if names is not None else NAMES),
            policy_of(repo, policy_toml),
            policy_commit(repo) if backend != "sqlite" else None,
            statuses,
        )
    return fleet


def quiet_reporter() -> tuple[LaunchReporter, io.StringIO]:
    stream = io.StringIO()
    return LaunchReporter(quiet=True, stream=stream), stream
