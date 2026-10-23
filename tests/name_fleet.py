"""A shared session-name registry for tests: an environment checkout and its bare remote.

``build_fleet`` makes a projects directory holding one checkout of an environment
repository that ships ``config/session-names.toml``, with a bare remote under
``tmp_path`` carrying the registry ref, and publishes a name list on it. Nothing here
reaches the network or a real remote.
"""

from __future__ import annotations

import io
import json
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from ai_cli import config as _config
from ai_cli import name_list, session_naming
from ai_cli import name_registry as reg
from ai_cli.launch_reporter import LaunchReporter

REF = "refs/heads/fleet-session-names/registry"
HOST = "box-one"
OTHER_HOST = "box-two"
HOST_ALIAS = "box"
REPO = "myrepo"
STEM = f"{REPO}-{HOST_ALIAS}"
NAMES = ["ada", "bob", "cyd", "bert", "cleo", "dora", "hugo", "iris", "jude", "kurt", "lola", "milo", "nora"]

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


def registry_for(repo: Path) -> reg.GitRegistry:
    return reg.GitRegistry(
        repo, ref=REF, remote="origin", policy_file=repo / reg.POLICY_MARKER, timeout_s=15, attempts=5
    )


def policy_of(repo: Path) -> dict:
    return name_list.fleet_policy(reg.read_policy_file(repo / reg.POLICY_MARKER))


def policy_commit(repo: Path) -> str:
    return git(repo, "log", "-1", "--format=%H", "--", "config/session-names.toml")


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
        )
    )


@dataclass
class Fleet:
    tmp: Path
    projects: Path
    remote: Path
    repo: Path
    registry: reg.GitRegistry

    def rows(self) -> dict[str, dict]:
        listing = git(self.remote, "ls-tree", "--name-only", f"{REF}:names", check=False)
        return {
            Path(path).stem: json.loads(git(self.remote, "show", f"{REF}:names/{path}"))
            for path in listing.splitlines()
            if path
        }

    def file(self, path: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.remote), "show", f"{REF}:{path}"], capture_output=True, text=True, check=False
        )
        return result.stdout if result.returncode == 0 else None

    def tip(self) -> str:
        return git(self.remote, "rev-parse", REF)

    def changed(self, commit: str | None = None) -> set[str]:
        target = commit or self.tip()
        return set(git(self.remote, "diff-tree", "--no-commit-id", "--name-only", "-r", target).splitlines())

    def offline(self) -> None:
        git(self.repo, "remote", "set-url", "origin", str(self.tmp / "missing.git"))

    def online(self) -> None:
        git(self.repo, "remote", "set-url", "origin", str(self.remote))

    def other_machine(self, name: str = "other-machine") -> reg.GitRegistry:
        return registry_for(clone(self.remote, self.tmp / name))


def build_fleet(
    tmp_path: Path, monkeypatch, *, names: list[str] | None = None, policy_toml: str = POLICY_TOML
) -> Fleet:
    projects = tmp_path / "projects"
    projects.mkdir()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test")
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
    registry = registry_for(repo)
    publish_list(registry, list(names if names is not None else NAMES), policy_of(repo), policy_commit(repo))
    return Fleet(tmp=tmp_path, projects=projects, remote=remote, repo=repo, registry=registry)


def quiet_reporter() -> tuple[LaunchReporter, io.StringIO]:
    stream = io.StringIO()
    return LaunchReporter(quiet=True, stream=stream), stream
