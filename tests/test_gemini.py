"""Tests for gemini module — 3-tier auth fallback."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.gemini import (
    AttemptLog,
    GeminiResult,
    _log,
    _log_to_file,
    _try_gemini_api,
    _try_gemini_cli,
    gemini_cli,
    run_gemini,
)


# --- GeminiResult / AttemptLog dataclass tests ---


class TestDataclasses:
    def test_gemini_result_when_default_then_not_successful(self):
        r = GeminiResult()
        assert r.success is False
        assert r.content == ""
        assert r.attempts == []

    def test_attempt_log_when_created_then_stores_fields(self):
        a = AttemptLog(tier=1, tier_name="gemini-cli (OAuth)", model="flash", success=True)
        assert a.tier == 1
        assert a.model == "flash"
        assert a.success is True
        assert a.error == ""


# --- _log tests ---


class TestLog:
    def test_log_when_default_then_prints_to_stderr(self, capsys):
        _log("hello")
        assert "hello" in capsys.readouterr().err

    def test_log_when_quiet_then_suppresses(self, capsys):
        _log("secret", quiet=True)
        assert capsys.readouterr().err == ""

    def test_log_when_verbose_message_without_verbose_flag_then_suppresses(self, capsys):
        _log("debug info", is_verbose=True, verbose=False)
        assert capsys.readouterr().err == ""

    def test_log_when_verbose_message_with_verbose_flag_then_prints(self, capsys):
        _log("debug info", is_verbose=True, verbose=True)
        assert "debug info" in capsys.readouterr().err


# --- _log_to_file tests ---


class TestLogToFile:
    def test_log_to_file_when_called_then_writes_jsonl(self, tmp_path):
        with patch("ai_cli.gemini.LOG_DIR", tmp_path):
            result = GeminiResult(
                content="response text",
                model="flash",
                tier=1,
                tier_name="gemini-cli (OAuth)",
                success=True,
                duration_ms=500,
            )
            _log_to_file(result, "test prompt", "/tmp/output.md")

        log_files = list(tmp_path.glob("*.jsonl"))
        assert len(log_files) == 1
        entry = json.loads(log_files[0].read_text().strip())
        assert entry["model"] == "flash"
        assert entry["success"] is True
        assert entry["prompt_chars"] == len("test prompt")
        assert entry["response_chars"] == len("response text")
        assert entry["output_path"] == "/tmp/output.md"

    def test_log_to_file_when_has_attempts_then_includes_them(self, tmp_path):
        with patch("ai_cli.gemini.LOG_DIR", tmp_path):
            attempt = AttemptLog(tier=1, tier_name="cli", model="flash", success=False, error="timeout")
            result = GeminiResult(model="flash", attempts=[attempt])
            _log_to_file(result, "p", None)

        log_files = list(tmp_path.glob("*.jsonl"))
        entry = json.loads(log_files[0].read_text().strip())
        assert len(entry["attempts"]) == 1
        assert entry["attempts"][0]["error"] == "timeout"


# --- _try_gemini_cli tests ---


class TestTryGeminiCli:
    def test_cli_when_not_found_then_error(self):
        with patch("shutil.which", return_value=None):
            r = _try_gemini_cli("hello", "flash", 30, False)
        assert r.success is False
        assert "not found" in r.error

    def test_cli_when_success_then_returns_content(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "response from gemini\n"
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/gemini"):
            with patch("subprocess.run", return_value=mock_result):
                r = _try_gemini_cli("hello", "flash", 30, False)
        assert r.success is True
        assert r.content == "response from gemini"
        assert r.tier == 1

    def test_cli_when_yolo_warning_in_stdout_then_strips_it(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "YOLO mode is enabled\nactual response\n"
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/gemini"):
            with patch("subprocess.run", return_value=mock_result):
                r = _try_gemini_cli("hello", "flash", 30, False)
        assert "YOLO" not in r.content
        assert "actual response" in r.content

    def test_cli_when_429_in_stderr_then_capacity_error(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: 429 Resource Exhausted"

        with patch("shutil.which", return_value="/usr/bin/gemini"):
            with patch("subprocess.run", return_value=mock_result):
                r = _try_gemini_cli("hello", "flash", 30, False)
        assert r.success is False
        assert "capacity" in r.error

    def test_cli_when_nonzero_exit_then_error(self):
        mock_result = MagicMock()
        mock_result.returncode = 2
        mock_result.stdout = ""
        mock_result.stderr = "some error"

        with patch("shutil.which", return_value="/usr/bin/gemini"):
            with patch("subprocess.run", return_value=mock_result):
                r = _try_gemini_cli("hello", "flash", 30, False)
        assert r.success is False
        assert "exit code 2" in r.error

    def test_cli_when_timeout_then_error(self):
        import subprocess

        with patch("shutil.which", return_value="/usr/bin/gemini"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gemini", 30)):
                r = _try_gemini_cli("hello", "flash", 30, False)
        assert r.success is False
        assert "timeout" in r.error

    def test_cli_when_empty_stdout_then_error(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/gemini"):
            with patch("subprocess.run", return_value=mock_result):
                r = _try_gemini_cli("hello", "flash", 30, False)
        assert r.success is False

    def test_cli_when_verbose_then_logs_command(self, capsys):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok\n"
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/gemini"):
            with patch("subprocess.run", return_value=mock_result):
                _try_gemini_cli("hello", "flash", 30, True)
        assert "[tier 1]" in capsys.readouterr().err


# --- _try_gemini_api tests ---


def _make_google_mocks(response_text="api response", response_usage=None, side_effect=None):
    """Build properly structured google.genai mock modules."""
    mock_types = MagicMock()

    mock_response = MagicMock()
    mock_response.text = response_text
    mock_response.usage_metadata = response_usage

    mock_client = MagicMock()
    if side_effect:
        mock_client.models.generate_content.side_effect = side_effect
    else:
        mock_client.models.generate_content.return_value = mock_response

    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client
    mock_genai.types = mock_types

    mock_google = MagicMock()
    mock_google.genai = mock_genai

    modules = {
        "google": mock_google,
        "google.genai": mock_genai,
        "google.genai.types": mock_types,
    }
    return modules, mock_genai, mock_types, mock_client


class TestTryGeminiApi:
    def test_api_tier2_when_no_key_then_error(self):
        with patch.dict("os.environ", {}, clear=True):
            r = _try_gemini_api("hello", "flash", 30, 2, False)
        assert r.success is False
        assert "GOOGLE_API_KEY_FREE_TIER not set" in r.error

    def test_api_tier3_when_no_key_then_error(self):
        with patch.dict("os.environ", {}, clear=True):
            r = _try_gemini_api("hello", "flash", 30, 3, False)
        assert r.success is False
        assert "GOOGLE_API_KEY_TIER_1 not set" in r.error

    def test_api_when_google_genai_not_installed_then_error(self):
        orig_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def fake_import(name, *args, **kwargs):
            if name == "google" or name.startswith("google."):
                raise ImportError("no google")
            return orig_import(name, *args, **kwargs)

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch("builtins.__import__", side_effect=fake_import):
                r = _try_gemini_api("hello", "flash", 30, 2, False)
        assert r.success is False
        assert "not installed" in r.error

    def test_api_when_success_then_returns_content(self):
        modules, mock_genai, mock_types, mock_client = _make_google_mocks("api response")

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch.dict("sys.modules", modules):
                r = _try_gemini_api("hello", "flash", 30, 2, False)
        assert r.success is True
        assert r.content == "api response"

    def test_api_when_thread_timeout_then_error(self):
        def slow_call(*args, **kwargs):
            import time as _time

            _time.sleep(10)

        modules, mock_genai, mock_types, mock_client = _make_google_mocks(side_effect=slow_call)

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch.dict("sys.modules", modules):
                r = _try_gemini_api("hello", "flash", 1, 2, False)
        assert r.success is False
        assert "timeout" in r.error

    def test_api_when_429_exception_then_capacity_error(self):
        modules, mock_genai, mock_types, mock_client = _make_google_mocks(
            side_effect=Exception("429 resource exhausted")
        )

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch.dict("sys.modules", modules):
                r = _try_gemini_api("hello", "flash", 30, 2, False)
        assert r.success is False
        assert "capacity" in r.error

    def test_api_when_generic_exception_then_error(self):
        modules, mock_genai, mock_types, mock_client = _make_google_mocks(side_effect=Exception("something broke"))

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch.dict("sys.modules", modules):
                r = _try_gemini_api("hello", "flash", 30, 2, False)
        assert r.success is False
        assert "something broke" in r.error

    def test_api_when_usage_metadata_present_then_extracts_tokens(self):
        mock_usage = MagicMock()
        mock_usage.prompt_token_count = 10
        mock_usage.candidates_token_count = 20
        mock_usage.total_token_count = 30

        modules, mock_genai, mock_types, mock_client = _make_google_mocks("response", response_usage=mock_usage)

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch.dict("sys.modules", modules):
                r = _try_gemini_api("hello", "flash", 30, 2, False)
        assert r.input_tokens == 10
        assert r.output_tokens == 20
        assert r.total_tokens == 30

    def test_api_tier3_when_gemini_api_key_fallback_then_uses_it(self):
        modules, mock_genai, mock_types, mock_client = _make_google_mocks("tier3 response")

        with patch.dict("os.environ", {"GEMINI_API_KEY": "fallback-key"}, clear=True):
            with patch.dict("sys.modules", modules):
                r = _try_gemini_api("hello", "flash", 30, 3, False)
        assert r.success is True

    def test_api_when_deep_think_model_then_uses_thinking_config(self):
        modules, mock_genai, mock_types, mock_client = _make_google_mocks("deep thought")

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch.dict("sys.modules", modules):
                r = _try_gemini_api("hello", "deep-think", 30, 2, False)
        assert r.success is True
        mock_types.ThinkingConfig.assert_called_once_with(thinking_level=mock_types.ThinkingLevel.HIGH)


# --- run_gemini tests ---


class TestRunGemini:
    def test_run_gemini_when_tier1_succeeds_then_returns_tier1(self, tmp_path):
        tier1_result = GeminiResult(
            content="tier1 ok", model="flash", tier=1, tier_name="gemini-cli (OAuth)", success=True, duration_ms=100
        )

        with patch("ai_cli.gemini._try_gemini_cli", return_value=tier1_result):
            with patch("ai_cli.gemini.LOG_DIR", tmp_path):
                r = run_gemini("hello", model="flash", output=str(tmp_path / "out.md"), quiet=True)
        assert r.success is True
        assert r.tier == 1
        assert r.content == "tier1 ok"
        assert (tmp_path / "out.md").read_text() == "tier1 ok"

    def test_run_gemini_when_tier1_fails_then_tries_tier2(self, tmp_path):
        tier1_fail = GeminiResult(
            model="flash", tier=1, tier_name="gemini-cli (OAuth)", success=False, error="not found"
        )
        tier2_ok = GeminiResult(
            content="tier2 ok", model="flash", tier=2, tier_name="API free-tier", success=True, duration_ms=200
        )

        with patch("ai_cli.gemini._try_gemini_cli", return_value=tier1_fail):
            with patch("ai_cli.gemini._try_gemini_api", return_value=tier2_ok):
                with patch("ai_cli.gemini.LOG_DIR", tmp_path):
                    r = run_gemini("hello", model="flash", output=str(tmp_path / "out.md"), quiet=True)
        assert r.success is True
        assert r.tier == 2

    def test_run_gemini_when_all_tiers_fail_then_error(self, tmp_path):
        fail = GeminiResult(model="flash", success=False, error="not configured", tier=1, tier_name="x")

        with patch("ai_cli.gemini._try_gemini_cli", return_value=fail):
            with patch("ai_cli.gemini._try_gemini_api", return_value=fail):
                with patch("ai_cli.gemini.LOG_DIR", tmp_path):
                    r = run_gemini("hello", model="flash", quiet=True)
        assert r.success is False
        assert r.error == "all tiers failed"
        assert len(r.attempts) == 3

    def test_run_gemini_when_capacity_error_then_falls_through(self, tmp_path):
        capacity_fail = GeminiResult(
            model="flash", success=False, error="capacity exhausted (429)", tier=1, tier_name="cli"
        )
        tier2_ok = GeminiResult(
            content="tier2", model="flash", tier=2, tier_name="API free-tier", success=True, duration_ms=50
        )

        with patch("ai_cli.gemini._try_gemini_cli", return_value=capacity_fail):
            with patch("ai_cli.gemini._try_gemini_api", return_value=tier2_ok):
                with patch("ai_cli.gemini.LOG_DIR", tmp_path):
                    r = run_gemini("hello", model="flash", quiet=True)
        assert r.success is True

    def test_run_gemini_when_no_output_and_success_then_auto_generates_path(self, tmp_path):
        ok = GeminiResult(content="auto output", model="flash", tier=1, tier_name="cli", success=True, duration_ms=50)

        with patch("ai_cli.gemini._try_gemini_cli", return_value=ok):
            with patch("ai_cli.gemini.LOG_DIR", tmp_path):
                with patch("ai_cli.gemini.DEFAULT_OUTPUT_DIR", tmp_path / "auto"):
                    r = run_gemini("hello", model="flash", quiet=True)
        assert r.success is True
        auto_files = list((tmp_path / "auto").glob("*.md"))
        assert len(auto_files) == 1
        assert auto_files[0].read_text() == "auto output"


# --- gemini_cli tests ---


class TestGeminiCli:
    def test_gemini_cli_when_prompt_given_then_runs(self, tmp_path):
        ok = GeminiResult(content="cli ok", model="flash", success=True)
        with patch("ai_cli.gemini.run_gemini", return_value=ok) as mock_run:
            with pytest.raises(SystemExit) as exc:
                gemini_cli(["test prompt", "-m", "flash"])
        assert exc.value.code == 0
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == "test prompt"

    def test_gemini_cli_when_failure_then_exits_1(self, tmp_path):
        fail = GeminiResult(model="flash", success=False, error="all tiers failed")
        with patch("ai_cli.gemini.run_gemini", return_value=fail):
            with pytest.raises(SystemExit) as exc:
                gemini_cli(["test prompt"])
        assert exc.value.code == 1

    def test_gemini_cli_when_no_prompt_and_tty_then_error(self):
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with pytest.raises(SystemExit) as exc:
                gemini_cli([])
        assert exc.value.code != 0

    def test_gemini_cli_when_empty_prompt_then_error(self):
        with pytest.raises(SystemExit) as exc:
            gemini_cli(["   "])
        assert exc.value.code != 0

    def test_gemini_cli_when_no_file_flag_then_passes_dev_null(self):
        ok = GeminiResult(content="ok", model="flash", success=True)
        with patch("ai_cli.gemini.run_gemini", return_value=ok) as mock_run:
            with pytest.raises(SystemExit):
                gemini_cli(["prompt", "--no-file"])
        assert mock_run.call_args[1]["output"] == "/dev/null"

    def test_gemini_cli_when_stdin_pipe_then_reads_stdin(self):
        ok = GeminiResult(content="ok", model="flash", success=True)
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.read.return_value = "piped prompt"
        with patch("sys.stdin", mock_stdin):
            with patch("ai_cli.gemini.run_gemini", return_value=ok) as mock_run:
                with pytest.raises(SystemExit):
                    gemini_cli([])
        assert mock_run.call_args[0][0] == "piped prompt"

    def test_gemini_cli_when_output_flag_then_passes_path(self):
        ok = GeminiResult(content="ok", model="flash", success=True)
        with patch("ai_cli.gemini.run_gemini", return_value=ok) as mock_run:
            with pytest.raises(SystemExit):
                gemini_cli(["prompt", "-o", "/tmp/test.md"])
        assert mock_run.call_args[1]["output"] == "/tmp/test.md"

    def test_gemini_cli_when_quiet_flag_then_passes_quiet(self):
        ok = GeminiResult(content="ok", model="flash", success=True)
        with patch("ai_cli.gemini.run_gemini", return_value=ok) as mock_run:
            with pytest.raises(SystemExit):
                gemini_cli(["prompt", "--quiet"])
        assert mock_run.call_args[1]["quiet"] is True

    def test_gemini_cli_when_timeout_flag_then_passes_timeout(self):
        ok = GeminiResult(content="ok", model="flash", success=True)
        with patch("ai_cli.gemini.run_gemini", return_value=ok) as mock_run:
            with pytest.raises(SystemExit):
                gemini_cli(["prompt", "--timeout", "120"])
        assert mock_run.call_args[1]["timeout_s"] == 120


# --- Coverage gap tests ---


class TestTryGeminiApiVerboseLogging:
    def test_api_when_verbose_then_logs_tier_info(self, capsys):
        """Covers line 290: verbose logging in REST API path."""
        modules, mock_genai, mock_types, mock_client = _make_google_mocks("verbose response")

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch.dict("sys.modules", modules):
                r = _try_gemini_api("hello", "flash", 30, 2, True)
        assert r.success is True
        err = capsys.readouterr().err
        assert "tier 2" in err or "REST API" in err


class TestTryGeminiApiUnknownTier:
    def test_try_gemini_api_when_unknown_tier_then_returns_error(self):
        """Covers line 254: else branch for tier not in {2, 3}."""
        result = _try_gemini_api("prompt", "flash", 30, tier=99, verbose=False)
        assert result.success is False
        assert "unknown tier" in result.error
        assert result.tier == 99


class TestTryGeminiApiNoResponse:
    def test_try_gemini_api_when_thread_completes_without_response_then_returns_error(self):
        """Covers line 338: 'no response received' when thread exits without populating containers."""

        class NoOpThread:
            def __init__(self, target, daemon):
                pass  # never runs target — neither container gets populated

            def start(self):
                pass

            def join(self, timeout):
                pass

            def is_alive(self):
                return False

        with patch.dict("os.environ", {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            modules = {
                "google": MagicMock(),
                "google.genai": MagicMock(),
                "google.genai.types": MagicMock(),
            }
            with patch.dict("sys.modules", modules):
                with patch("threading.Thread", NoOpThread):
                    result = _try_gemini_api("prompt", "flash", 5, tier=2, verbose=False)

        assert result.success is False
        assert "no response received" in result.error


class TestRunGeminiNoFilePrintsToStdout:
    def test_run_gemini_when_success_and_not_quiet_then_prints_content(self, capsys, tmp_path):
        """Covers line 460: print(final_result.content) when not quiet."""
        ok = GeminiResult(content="output text", model="flash", success=True, tier=1, tier_name="cli")

        with patch("ai_cli.gemini._try_gemini_cli", return_value=ok):
            with patch("ai_cli.gemini._log_to_file"):
                result = run_gemini("test", output="/dev/null", quiet=False)
        assert result.success is True
        out = capsys.readouterr().out
        assert "output text" in out


class TestLoadDopplerSecrets:
    """Tests for _load_doppler_secrets (lines 140-169)."""

    def _get_fn(self):
        from ai_cli.gemini import _load_doppler_secrets

        return _load_doppler_secrets

    def test_when_all_keys_present_then_skips_doppler(self):
        """When both API keys are in env, doppler is never called."""
        env = {"GOOGLE_API_KEY_FREE_TIER": "key1", "GOOGLE_API_KEY_TIER_1": "key2"}
        with patch.dict("os.environ", env, clear=False):
            with patch("shutil.which") as mock_which:
                self._get_fn()()
        mock_which.assert_not_called()

    def test_when_doppler_not_in_path_then_noop(self):
        """When doppler binary not found, nothing is injected (line 151)."""
        # Clear the two keys that trigger the early-exit check at line 146
        clean_env = {
            k: v for k, v in os.environ.items() if k not in ("GOOGLE_API_KEY_FREE_TIER", "GOOGLE_API_KEY_TIER_1")
        }
        with patch.dict("os.environ", clean_env, clear=True):
            with patch("shutil.which", return_value=None):
                with patch("subprocess.run") as mock_run:
                    self._get_fn()()
        mock_run.assert_not_called()

    def test_when_doppler_returns_nonzero_then_noop(self):
        """Non-zero returncode from doppler → no keys injected."""
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("GOOGLE_API_KEY_FREE_TIER", "GOOGLE_API_KEY_TIER_1", "GEMINI_API_KEY")
        }
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        with patch.dict("os.environ", clean_env, clear=True):
            with patch("shutil.which", return_value="/usr/bin/doppler"):
                with patch("subprocess.run", return_value=fake_result):
                    self._get_fn()()
            assert "GOOGLE_API_KEY_FREE_TIER" not in os.environ

    def test_when_doppler_succeeds_then_injects_missing_keys(self):
        """Successful doppler run injects keys that were absent from env."""
        import os as _os

        env_before = {
            k: v for k, v in _os.environ.items() if k not in ("GOOGLE_API_KEY_FREE_TIER", "GOOGLE_API_KEY_TIER_1")
        }
        fake_stdout = "GOOGLE_API_KEY_FREE_TIER=injected-key\nOTHER_VAR=foo\n"
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = fake_stdout
        with patch.dict("os.environ", env_before, clear=True):
            with patch("shutil.which", return_value="/usr/bin/doppler"):
                with patch("subprocess.run", return_value=fake_result):
                    self._get_fn()()
            assert _os.environ.get("GOOGLE_API_KEY_FREE_TIER") == "injected-key"

    def test_when_subprocess_raises_then_noop(self):
        """Exception during subprocess.run is silently swallowed (lines 168-169)."""
        clean_env = {
            k: v for k, v in os.environ.items() if k not in ("GOOGLE_API_KEY_FREE_TIER", "GOOGLE_API_KEY_TIER_1")
        }
        with patch.dict("os.environ", clean_env, clear=True):
            with patch("shutil.which", return_value="/usr/bin/doppler"):
                with patch("subprocess.run", side_effect=OSError("spawn error")):
                    self._get_fn()()  # must not raise

    def test_when_doppler_output_has_lines_without_equals_then_skips_them(self):
        """line 164: lines without '=' in doppler output are skipped."""
        clean_env = {
            k: v for k, v in os.environ.items() if k not in ("GOOGLE_API_KEY_FREE_TIER", "GOOGLE_API_KEY_TIER_1")
        }
        # Include a header line (no '=') before the key=value line
        fake_stdout = "Doppler output:\nGOOGLE_API_KEY_FREE_TIER=injected-key\n"
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = fake_stdout
        with patch.dict("os.environ", clean_env, clear=True):
            with patch("shutil.which", return_value="/usr/bin/doppler"):
                with patch("subprocess.run", return_value=fake_result):
                    self._get_fn()()
            assert os.environ.get("GOOGLE_API_KEY_FREE_TIER") == "injected-key"
