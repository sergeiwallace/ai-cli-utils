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


def publish_list(registry: reg.Registry, names: list[str], policy: dict, commit: str | None) -> None:
    record = {
        "schema": 1,
        "policy": policy,
        "policy_digest": name_list.policy_digest(policy),
        "policy_commit": commit,
        "count": len(names),
    }
    registry.transact(
        lambda snapshot: reg.Change(
            {
                reg.NAMES_TXT: name_list.render_names(sorted(names)).encode(),
                reg.GENERATION_JSON: name_list.render_record(record).encode(),
            },
            "session-names: generate (fixture)",
        ),
        exempt=True,
    )


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
        return registry_for(checkout, path=self.tmp / f"{name}.dolt" if self.backend == "dolt" else None)


def _spy_on_commits(monkeypatch, fleet: Fleet) -> None:
    """Record every change a database backend commits, as the git-tree store's history would show it."""
    cls = type(fleet.registry)
    original = cls.transact

    def transact(self, build, *, exempt: bool = False):
        change = original(self, build, exempt=exempt)
        if change is not None:
            fleet.commits.append(Commit(f"c{len(fleet.commits) + 1}", dict(change.writes), change.message))
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
        )
    return fleet


def quiet_reporter() -> tuple[LaunchReporter, io.StringIO]:
    stream = io.StringIO()
    return LaunchReporter(quiet=True, stream=stream), stream
