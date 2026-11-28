"""Every bounded wait the launcher performs retries through one config-driven policy.

Measured on a loaded macOS host: the wake-sidecar ensure timed out against a fixed 3 s
budget about three seconds after the Dolt ensure and the launch gave up on it; a name
registry fetch failed after 51.7 s and printed a credential helper's twenty-line restore
help for what was an authentication refusal; and the launch-time reinstall spent 161 s
inside "checking installed version". The policy lives in ``[retry]`` of config.toml and is
read on every call, so each test writes the file it needs and runs the real ``ai`` entry
point, with the sidecar, the registry remote and the installer stubbed at their process
boundary: a real stub script, a real ``git upload-pack`` wrapper, and the uv command line.
"""

from __future__ import annotations

import contextlib
import http.server
import io
import itertools
import os
import re
import subprocess
import sys
import textwrap
import threading
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import run_cli
from name_fleet import HOST, HOST_ALIAS, REPO, build_fleet, git

from ai_cli import session_naming
from ai_cli.main import _do_update_or_deploy


def _write_config(tmp_path: Path, monkeypatch, text: str) -> None:
    base = tmp_path / "user-config"
    (base / "ai-cli-utils").mkdir(parents=True, exist_ok=True)
    (base / "ai-cli-utils" / "config.toml").write_text(textwrap.dedent(text), encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base))
    monkeypatch.setenv("APPDATA", str(base))


_SIDECAR = """\
import json, pathlib, sys, time
here = pathlib.Path(__file__).parent
counter = here / "calls"
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
plan = (here / "plan").read_text().split()
if plan[min(n, len(plan)) - 1] == "hang":
    time.sleep(30)
print(json.dumps({"action": "started", "reason": "unit-started", "unit": "example-sidecar.service"}))
"""


@pytest.fixture
def sidecar(tmp_path):
    script = tmp_path / "myhub" / "scripts" / "wake_sidecar_cli.py"
    script.parent.mkdir(parents=True)
    script.write_text(_SIDECAR, encoding="utf-8")

    def plan(*steps: str) -> Path:
        (script.parent / "plan").write_text(" ".join(steps), encoding="utf-8")
        (script.parent / "calls").unlink(missing_ok=True)
        return script

    def calls() -> int:
        counter = script.parent / "calls"
        return int(counter.read_text()) if counter.exists() else 0

    with ExitStack() as stack:
        stack.enter_context(patch("ai_cli.main._ensure_dolt_server"))
        stack.enter_context(patch("ai_cli.main._tunnel._ensure_nats_tunnel"))
        launched = stack.enter_context(patch("ai_cli.main._do_session_launch"))
        stack.enter_context(patch("ai_cli.process_manager._start_stale_session_reaper_for_launch"))
        stack.enter_context(patch("ai_cli.main._wake_sidecar_script", return_value=script))
        stack.enter_context(patch("ai_cli.main._sidecar_unit_manager_available", return_value=True))
        yield MagicMock(plan=plan, calls=calls, launched=launched)


_FAST_SIDECAR = """\
[retry.wake_sidecar]
attempts = {attempts}
timeout_seconds = 1
max_timeout_seconds = 1
delay_seconds = 0
"""


def test_given_a_sidecar_ensure_that_times_out_once_when_launching_then_it_is_retried_and_reports_success(
    sidecar, tmp_path, monkeypatch
):
    _write_config(tmp_path, monkeypatch, _FAST_SIDECAR.format(attempts=3))
    sidecar.plan("hang", "ok")

    code, _out, err = run_cli(["ai", "c", "session-1"])

    assert code == 0, err
    assert sidecar.calls() == 2
    assert "wake sidecar started" in err
    assert "wake sidecar failed" not in err
    sidecar.launched.assert_called_once()


def test_given_a_sidecar_ensure_that_always_times_out_when_launching_then_it_reports_attempts_and_last_cause(
    sidecar, tmp_path, monkeypatch
):
    _write_config(tmp_path, monkeypatch, _FAST_SIDECAR.format(attempts=2))
    sidecar.plan("hang")

    code, _out, err = run_cli(["ai", "c", "session-1"])

    assert code == 0, err
    assert sidecar.calls() == 2
    [line] = [line for line in err.splitlines() if "wake sidecar failed" in line]
    assert "after 2 attempts" in line
    assert "did not finish within 1 s" in line
    assert line.rstrip().endswith("launch continues")
    sidecar.launched.assert_called_once()


def test_given_a_changed_retry_config_when_the_next_launch_runs_then_the_new_value_applies_without_a_restart(
    sidecar, tmp_path, monkeypatch
):
    _write_config(tmp_path, monkeypatch, _FAST_SIDECAR.format(attempts=1))
    sidecar.plan("hang", "ok")
    _code, _out, first = run_cli(["ai", "c", "session-1"])

    _write_config(tmp_path, monkeypatch, _FAST_SIDECAR.format(attempts=2))
    sidecar.plan("hang", "ok")
    _code, _out, second = run_cli(["ai", "c", "session-2"])

    assert "wake sidecar failed: ensure did not finish within 1 s after 1 attempt" in first
    assert "wake sidecar started" in second
    assert "wake sidecar failed" not in second


_UPLOAD_PACK = """\
import pathlib, sys, time
here = pathlib.Path(__file__).parent
counter = here / "upload-pack-calls"
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
plan = (here / "upload-pack-plan").read_text().split()
step = plan[min(n, len(plan)) - 1]
if step == "hang":
    time.sleep(8)
elif step == "unresolvable":
    sys.stderr.write("fatal: unable to access 'https://example.com/env.git/': Could not resolve host: example.com\\n")
    sys.exit(128)
elif step == "auth":
    sys.stderr.write(
        "credential-helper: no token is stored for example.com\\n"
        "credential-helper: the personal token is not installed.\\n"
        "  Restore (about 60 seconds):\\n"
        "    1. open the token settings page\\n"
        "    2. paste the new token\\n"
        "fatal: could not read Username for 'https://example.com': terminal prompts disabled\\n"
    )
    sys.exit(128)
"""


@pytest.fixture
def launch(tmp_path, monkeypatch, register_repo):
    fleet = build_fleet(tmp_path, monkeypatch, backend="git")
    register_repo(fleet.repo, REPO)
    monkeypatch.chdir(fleet.repo)
    alias_file = tmp_path / session_naming.ALIAS_FILENAME
    alias_file.write_text(f'[aliases]\n{HOST} = "{HOST_ALIAS}"\n', encoding="utf-8")
    monkeypatch.setattr(session_naming, "alias_sources", lambda: [alias_file])
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")
    stub_dir = tmp_path / "upload-pack"
    stub_dir.mkdir()
    stub = stub_dir / "upload_pack.py"
    stub.write_text(_UPLOAD_PACK, encoding="utf-8")
    python = Path(sys.executable).as_posix()
    git(fleet.repo, "config", "remote.origin.uploadpack", f'"{python}" "{stub.as_posix()}" && git upload-pack')
    clock = itertools.count(start=0.0, step=3.0)

    def plan(*steps: str) -> None:
        (stub_dir / "upload-pack-plan").write_text(" ".join(steps), encoding="utf-8")
        (stub_dir / "upload-pack-calls").unlink(missing_ok=True)

    def calls() -> int:
        counter = stub_dir / "upload-pack-calls"
        return int(counter.read_text()) if counter.exists() else 0

    with (
        patch("ai_cli.launch_reporter.monotonic", side_effect=lambda: next(clock)),
        patch("ai_cli.host_env.refresh_host_env"),
        patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
        patch("ai_cli.tmux_setup.formats_expand", return_value=True),
        patch("ai_cli.tmux_setup.probe", return_value=MagicMock(versions_disagree=False)),
        patch("ai_cli.tmux_setup.report_lines", return_value=[]),
        patch(
            "ai_cli.compact_transport.resolve",
            return_value=MagicMock(must_refuse=False, platform_limited=False, detail="tmux"),
        ),
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.config.get_current_project_name", return_value=REPO),
        patch("ai_cli.direnv_setup.ensure_direnv"),
        patch("ai_cli.session.create_worktree", side_effect=AssertionError("dry run created a worktree")),
        patch("os.execvp", side_effect=AssertionError("dry run executed")),
    ):
        yield MagicMock(
            plan=plan,
            calls=calls,
            run=lambda *argv: run_cli(["ai", "c", *argv, "--project-prefix", REPO]),
            tmp=tmp_path,
        )


_FAST_FETCH = """\
[retry.registry_fetch]
attempts = 2
timeout_seconds = 3
max_timeout_seconds = 60
growth = 20
delay_seconds = 0
"""


def _fetch_outcome(err: str) -> str:
    [line] = [
        line for line in err.splitlines() if re.search(r"session: (name registry fetched|did not complete)", line)
    ]
    return line


def test_given_a_registry_fetch_that_times_out_once_when_launching_then_it_is_retried_and_completes(
    launch, monkeypatch
):
    _write_config(launch.tmp, monkeypatch, _FAST_FETCH)
    launch.plan("hang", "ok")

    code, out, err = launch.run("-d")

    assert code == 0, out + err
    assert launch.calls() == 2
    assert "name registry fetched" in _fetch_outcome(err)
    assert re.search(r"registry fetch attempt 1 of 2 .*did not finish within 3 s; retrying", err), err


def test_given_a_registry_remote_that_cannot_be_resolved_once_when_launching_then_it_is_retried_and_completes(
    launch, monkeypatch
):
    _write_config(launch.tmp, monkeypatch, _FAST_FETCH)
    launch.plan("unresolvable", "ok")

    code, out, err = launch.run("-d")

    assert code == 0, out + err
    assert launch.calls() == 2
    assert "name registry fetched" in _fetch_outcome(err)


def test_given_a_registry_fetch_refused_for_authentication_when_launching_then_it_is_reported_once_without_help(
    launch, monkeypatch
):
    _write_config(launch.tmp, monkeypatch, _FAST_FETCH)
    launch.plan("auth")

    code, out, err = launch.run("-d")

    assert code == 0, out + err
    assert launch.calls() == 1, "an authentication refusal is a definite answer, never retried"
    outcome = _fetch_outcome(err)
    assert "authentication refused" in outcome
    assert "could not read Username" in outcome
    assert "Restore (about 60 seconds)" not in err
    assert "paste the new token" not in err


_PYPROJECT = '[project]\nname = "myproject"\nversion = "0.1.0"\n'


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "myproject"
    (root / "src" / "myproject").mkdir(parents=True)
    (root / "pyproject.toml").write_text(_PYPROJECT)
    (root / "src" / "myproject" / "__init__.py").write_text("")
    return root


def _update(checkout: Path, state: Path) -> tuple[int, list[list[str]], str]:
    commands: list[list[str]] = []

    def run(cmd, **_kwargs):
        commands.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    out = io.StringIO()
    with (
        patch("ai_cli.config.get_xdg_state_home", return_value=state),
        patch("subprocess.run", side_effect=run),
        contextlib.redirect_stdout(out),
        pytest.raises(SystemExit) as exited,
    ):
        _do_update_or_deploy(
            force_reinstall=True,
            config={"deploy": {"project_path": str(checkout)}},
            quiet=True,
            package_only=True,
        )
    return exited.value.code, commands, out.getvalue()


def test_given_a_cache_bypassing_reinstall_when_it_runs_then_only_the_package_itself_is_reinstalled(checkout, tmp_path):
    code, commands, _out = _update(checkout, tmp_path / "state")

    assert code == 0
    [install] = [c for c in commands if Path(c[0]).stem == "uv" and "install" in c]
    assert install[install.index("--reinstall-package") + 1] == "myproject"
    assert "--reinstall" not in install
    assert "--force-reinstall" not in install


def test_given_sibling_worktrees_with_bytecode_when_the_reinstall_clears_caches_then_only_the_package_source_is_swept(
    checkout, tmp_path
):
    own = checkout / "src" / "myproject" / "__pycache__"
    own.mkdir()
    (own / "x.cpython-314.pyc").write_bytes(b"\0")
    sibling = checkout / ".worktrees" / "session-1" / ".venv" / "lib" / "dep" / "__pycache__"
    sibling.mkdir(parents=True)
    (sibling / "dep.cpython-314.pyc").write_bytes(b"\0")

    code, _commands, _out = _update(checkout, tmp_path / "state")

    assert code == 0
    assert not own.exists()
    assert (sibling / "dep.cpython-314.pyc").exists(), "another worktree's bytecode is not this install's to clear"


def test_given_a_quiet_reinstall_when_it_reports_then_the_one_line_times_each_step(checkout, tmp_path):
    _code, _commands, out = _update(checkout, tmp_path / "state")

    [line] = out.strip().splitlines()
    assert "installed (cache-bypassing reinstall)" in line
    assert re.search(r"pull \d+\.\ds, install \d+\.\ds, post-install \d+\.\ds", line), line


def test_given_no_user_retry_table_when_a_site_reads_its_policy_then_the_shipped_template_retries_it(
    tmp_path, monkeypatch
):
    from ai_cli import retry

    _write_config(tmp_path, monkeypatch, "")

    for site in ("wake_sidecar", "registry_fetch"):
        assert retry.policy(site).attempts >= 2, site


_HELPER = """\
import pathlib, sys
here = pathlib.Path(__file__).parent
if sys.argv[1:] != ["get"]:
    sys.exit(0)
sys.stdin.read()
counter = here / "helper-calls"
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
plan = (here / "helper-plan").read_text().split()
step = plan[min(n, len(plan)) - 1]
if step == "transient":
    sys.stderr.write("example-helper: transient: timed out\\n")
    sys.exit(1)
if step == "refuse":
    sys.stderr.write("example-helper: no credential is stored for this host\\n")
    sys.exit(1)
print("username=user")
print("password=secret")
"""


class _GitHttpHandler(http.server.BaseHTTPRequestHandler):
    """Smart-HTTP git behind Basic auth: no Authorization header gets a 401, as a real host's does."""

    project_root = ""

    def log_message(self, *_args) -> None:
        return

    def _serve(self) -> None:
        if not self.headers.get("Authorization", "").startswith("Basic "):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="example"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = {
            **os.environ,
            "GIT_PROJECT_ROOT": self.project_root,
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
            "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
            "REMOTE_USER": "user",
        }
        output = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True, check=False).stdout
        head, _, payload = output.partition(b"\r\n\r\n")
        if not payload and b"\n\n" in output:
            head, _, payload = output.partition(b"\n\n")
        status = 200
        headers = []
        for line in head.decode("latin-1").splitlines():
            name, _, value = line.partition(":")
            if name.lower() == "status":
                status = int(value.split()[0])
            elif name:
                headers.append((name, value.strip()))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _serve


@pytest.fixture
def helper_remote(launch, monkeypatch):
    """The fleet's registry remote served over HTTP behind auth, with a planned credential helper."""
    handler = type("Handler", (_GitHttpHandler,), {"project_root": str(launch.tmp)})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    helper_dir = launch.tmp / "helper"
    helper_dir.mkdir()
    helper = helper_dir / "helper.py"
    helper.write_text(_HELPER, encoding="utf-8")
    repo = Path.cwd()
    git(repo, "remote", "set-url", "origin", f"http://127.0.0.1:{server.server_port}/remote.git")
    git(repo, "config", "--add", "credential.helper", "")
    git(repo, "config", "--add", "credential.helper", f'!"{Path(sys.executable).as_posix()}" "{helper.as_posix()}"')
    for name in ("GIT_ASKPASS", "SSH_ASKPASS"):
        monkeypatch.delenv(name, raising=False)

    def plan(*steps: str) -> None:
        (helper_dir / "helper-plan").write_text(" ".join(steps), encoding="utf-8")
        (helper_dir / "helper-calls").unlink(missing_ok=True)

    def calls() -> int:
        counter = helper_dir / "helper-calls"
        return int(counter.read_text()) if counter.exists() else 0

    try:
        yield MagicMock(plan=plan, calls=calls, run=launch.run, tmp=launch.tmp)
    finally:
        server.shutdown()
        server.server_close()


def test_given_a_helper_that_reports_a_transient_failure_once_when_fetching_then_it_is_retried_and_completes(
    helper_remote, monkeypatch
):
    _write_config(helper_remote.tmp, monkeypatch, _FAST_FETCH)
    helper_remote.plan("transient", "ok")

    code, out, err = helper_remote.run("-d")

    assert code == 0, out + err
    assert helper_remote.calls() == 2
    assert "name registry fetched" in _fetch_outcome(err)
    assert "authentication refused" not in err


def test_given_a_helper_that_reports_a_transient_failure_every_time_when_fetching_then_it_names_attempts_and_cause(
    helper_remote, monkeypatch
):
    _write_config(helper_remote.tmp, monkeypatch, _FAST_FETCH)
    helper_remote.plan("transient")

    code, out, err = helper_remote.run("-d")

    assert code == 0, out + err
    assert helper_remote.calls() == 2
    outcome = _fetch_outcome(err)
    assert "did not complete" in outcome
    assert "example-helper: transient: timed out" in outcome
    assert "after 2 attempts" in outcome
    assert "authentication refused" not in outcome


def test_given_a_helper_that_refuses_without_the_marker_when_fetching_then_it_is_reported_once_as_refused(
    helper_remote, monkeypatch
):
    _write_config(helper_remote.tmp, monkeypatch, _FAST_FETCH)
    helper_remote.plan("refuse")

    code, out, err = helper_remote.run("-d")

    assert code == 0, out + err
    assert helper_remote.calls() == 1, "a refusal is a definite answer, never retried"
    assert "authentication refused" in _fetch_outcome(err)


def test_given_a_helper_that_answers_when_fetching_over_http_then_the_fetch_completes(helper_remote, monkeypatch):
    """The control: the HTTP remote and its helper serve a real fetch, so the cases above measure the marker."""
    _write_config(helper_remote.tmp, monkeypatch, _FAST_FETCH)
    helper_remote.plan("ok")

    code, out, err = helper_remote.run("-d")

    assert code == 0, out + err
    assert helper_remote.calls() == 1
    assert "name registry fetched" in _fetch_outcome(err)
