"""A test process never inherits credentials, so no failure report can render them."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import _credential_environment_names

REPO_ROOT = Path(__file__).resolve().parents[1]
PLANTED_CREDENTIALS = {
    "PLANTED_SERVICE_TOKEN": "planted-token-value",
    "PLANTED_CLIENT_SECRET": "planted-secret-value",
    "PLANTED_DB_PASSWORD": "planted-password-value",
    "PLANTED_API_KEY": "planted-api-key-value",
    "PLANTED_AI_CORE_DATABASE_URL": "postgresql+psycopg://u:planted-dsn-value@host/db",
    "DOPPLER_PLANTED_CONFIG": "planted-doppler-value",
    "AWS_PLANTED_PROFILE": "planted-aws-value",
}
PLANTED_BENIGN = {
    "PLANTED_BENIGN_SETTING": "kept",
    "PLANTED_TOKENIZERS_PARALLELISM": "kept",
    "SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.sock",
}


def test_given_credential_names_when_classified_then_only_credentials_are_named():
    names = _credential_environment_names({**PLANTED_CREDENTIALS, **PLANTED_BENIGN, "PATH": "/usr/bin"})
    assert names == sorted(PLANTED_CREDENTIALS)


@pytest.mark.skipif(os.environ.get("AI_CLI_SCRUB_INNER") != "1", reason="reports from the inner session only")
def test_inner_session_reports_surviving_names():
    print("SURVIVORS=" + json.dumps(_credential_environment_names(os.environ)))
    print("KEPT=" + json.dumps(sorted(name for name in PLANTED_BENIGN if name in os.environ)))


def test_given_credentials_in_the_launching_environment_when_a_session_starts_then_none_reaches_it():
    environment = {**os.environ, **PLANTED_CREDENTIALS, **PLANTED_BENIGN, "AI_CLI_SCRUB_INNER": "1"}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_credential_environment_scrub.py::test_inner_session_reports_surviving_names",
            "-s",
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    reported = [line for line in completed.stdout.splitlines() if line.startswith("SURVIVORS=")]
    assert len(reported) == 1, completed.stdout[-2000:]
    kept = [line for line in completed.stdout.splitlines() if line.startswith("KEPT=")]
    assert len(kept) == 1, completed.stdout[-2000:]
    assert json.loads(reported[0].removeprefix("SURVIVORS=")) == []
    assert json.loads(kept[0].removeprefix("KEPT=")) == sorted(PLANTED_BENIGN)
    assert not any(value in completed.stdout + completed.stderr for value in PLANTED_CREDENTIALS.values())
