"""Tests for research depth orchestration module."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.gemini import GeminiResult
from ai_cli.research import (
    DepthPreset,
    _run_grounded_search,
    checkpoint_path,
    generate_queries,
    load_checkpoint,
    load_depth_preset,
    make_run_id,
    run_concurrent_searches,
    run_standard,
    save_checkpoint,
    synthesize,
)


# ---------------------------------------------------------------------------
# TestLoadDepthPreset
# ---------------------------------------------------------------------------


class TestLoadDepthPreset:
    def test_quick_preset_when_default_then_no_search(self):
        preset = load_depth_preset("quick")
        assert preset.search_provider is None
        assert preset.search_rounds == 0

    def test_standard_preset_when_default_then_has_search_provider(self):
        preset = load_depth_preset("standard")
        assert preset.search_provider == "gemini"
        assert preset.search_rounds == 1
        assert preset.planning_model == "deep-think"

    def test_deep_preset_when_default_then_has_reflection(self):
        preset = load_depth_preset("deep")
        assert preset.reflection_rounds == 1
        assert preset.search_rounds == 2

    def test_unknown_depth_when_called_then_raises(self):
        with pytest.raises(ValueError, match="Unknown depth preset"):
            load_depth_preset("ultra")

    def test_planning_model_override_when_provided_then_overrides(self):
        preset = load_depth_preset("standard", planning_model_override="flash")
        assert preset.planning_model == "flash"

    def test_user_config_when_present_then_overrides_defaults(self, tmp_path):
        config_yaml = """
depth_presets:
  standard:
    model: pro
    search_provider: gemini
    search_rounds: 2
    planning_model: flash
    reflection_rounds: 0
"""
        config_file = tmp_path / "research.yaml"
        config_file.write_text(config_yaml)
        with patch("ai_cli.research.RESEARCH_CONFIG_PATH", config_file):
            preset = load_depth_preset("standard")
        assert preset.model == "pro"
        assert preset.search_rounds == 2
        assert preset.planning_model == "flash"


# ---------------------------------------------------------------------------
# TestMakeRunId
# ---------------------------------------------------------------------------


class TestMakeRunId:
    def test_make_run_id_when_called_then_timestamp_format(self):
        run_id = make_run_id()
        parts = run_id.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS
        assert len(parts[2]) == 6  # hex suffix

    def test_make_run_id_when_called_twice_then_unique(self):
        assert make_run_id() != make_run_id()


# ---------------------------------------------------------------------------
# TestCheckpointing
# ---------------------------------------------------------------------------


class TestCheckpointing:
    def test_save_checkpoint_when_called_then_writes_json(self, tmp_path):
        with patch("ai_cli.research.RESEARCH_RUNS_DIR", tmp_path):
            save_checkpoint("run-123", "step_01", {"queries": ["q1", "q2"]})
        saved = tmp_path / "run-123" / "step_01.json"
        assert saved.exists()
        data = json.loads(saved.read_text())
        assert data["queries"] == ["q1", "q2"]
        assert data["completed"] is True

    def test_load_checkpoint_when_complete_then_returns_data(self, tmp_path):
        with patch("ai_cli.research.RESEARCH_RUNS_DIR", tmp_path):
            save_checkpoint("run-abc", "step_02", {"results": [{"query": "q", "success": True}]})
            result = load_checkpoint("run-abc", "step_02")
        assert result is not None
        assert result["results"][0]["query"] == "q"

    def test_load_checkpoint_when_missing_then_returns_none(self, tmp_path):
        with patch("ai_cli.research.RESEARCH_RUNS_DIR", tmp_path):
            result = load_checkpoint("nonexistent-run", "step_01")
        assert result is None

    def test_load_checkpoint_when_incomplete_then_returns_none(self, tmp_path):
        path = tmp_path / "run-x" / "step_01.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"queries": ["q1"], "completed": False}))
        with patch("ai_cli.research.RESEARCH_RUNS_DIR", tmp_path):
            result = load_checkpoint("run-x", "step_01")
        assert result is None

    def test_checkpoint_path_when_called_then_correct_location(self, tmp_path):
        with patch("ai_cli.research.RESEARCH_RUNS_DIR", tmp_path):
            p = checkpoint_path("run-123", "step_02")
        assert str(p).endswith("run-123/step_02.json")


# ---------------------------------------------------------------------------
# TestGenerateQueries
# ---------------------------------------------------------------------------


class TestGenerateQueries:
    def _ok_result(self, content: str) -> GeminiResult:
        return GeminiResult(content=content, model="deep-think", success=True)

    def test_when_model_returns_clean_json_then_returns_list(self):
        payload = json.dumps({"queries": ["q1", "q2", "q3"]})
        with patch("ai_cli.gemini.run_gemini", return_value=self._ok_result(payload)):
            result = generate_queries("test prompt", planning_model="deep-think", n=3, quiet=True)
        assert result == ["q1", "q2", "q3"]

    def test_when_json_wrapped_in_fences_then_strips_fences(self):
        fenced = '```json\n{"queries": ["q1", "q2"]}\n```'
        with patch("ai_cli.gemini.run_gemini", return_value=self._ok_result(fenced)):
            result = generate_queries("test", planning_model="deep-think", n=2, quiet=True)
        assert result == ["q1", "q2"]

    def test_when_model_fails_then_raises_runtime_error(self):
        fail = GeminiResult(model="deep-think", success=False, error="timeout")
        with patch("ai_cli.gemini.run_gemini", return_value=fail):
            with pytest.raises(RuntimeError, match="Query generation failed"):
                generate_queries("test", planning_model="deep-think", n=3, quiet=True)

    def test_when_response_not_json_then_raises_value_error(self):
        with patch("ai_cli.gemini.run_gemini", return_value=self._ok_result("not json at all")):
            with pytest.raises(ValueError):
                generate_queries("test", planning_model="deep-think", n=3, quiet=True)

    def test_when_queries_list_empty_then_raises_value_error(self):
        payload = json.dumps({"queries": []})
        with patch("ai_cli.gemini.run_gemini", return_value=self._ok_result(payload)):
            with pytest.raises(ValueError, match="empty"):
                generate_queries("test", planning_model="deep-think", n=3, quiet=True)

    def test_when_embedded_json_in_prose_then_extracts_it(self):
        content = 'Here are queries: {"queries": ["q1", "q2"]} - done.'
        with patch("ai_cli.gemini.run_gemini", return_value=self._ok_result(content)):
            result = generate_queries("test", planning_model="deep-think", n=2, quiet=True)
        assert result == ["q1", "q2"]

    def test_when_more_queries_returned_than_n_then_truncates(self):
        payload = json.dumps({"queries": ["q1", "q2", "q3", "q4", "q5"]})
        with patch("ai_cli.gemini.run_gemini", return_value=self._ok_result(payload)):
            result = generate_queries("test", planning_model="deep-think", n=3, quiet=True)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# TestRunGroundedSearch
# ---------------------------------------------------------------------------


class TestRunGroundedSearch:
    def test_when_no_api_keys_then_returns_failure(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("ai_cli.gemini._load_doppler_secrets"):
                result = _run_grounded_search("test query")
        assert result["success"] is False
        assert "not configured" in result["error"]

    def test_when_google_genai_not_installed_then_returns_error(self):
        with patch.dict(os.environ, {"GOOGLE_API_KEY_FREE_TIER": "key123"}):
            with patch("ai_cli.gemini._load_doppler_secrets"):
                with patch.dict("sys.modules", {"google": None, "google.genai": None, "google.genai.types": None}):
                    result = _run_grounded_search("test query")
        assert result["success"] is False
        assert "not installed" in result["error"]

    def test_when_api_succeeds_then_returns_content(self):
        mock_response = MagicMock()
        mock_response.text = "search result content"

        mock_types = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        mock_google = MagicMock()
        mock_google.genai = mock_genai
        modules = {
            "google": mock_google,
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        }

        with patch.dict(os.environ, {"GOOGLE_API_KEY_FREE_TIER": "test-key"}):
            with patch("ai_cli.gemini._load_doppler_secrets"):
                with patch.dict("sys.modules", modules):
                    result = _run_grounded_search("what is Python?")

        assert result["success"] is True
        assert result["content"] == "search result content"

    def test_when_first_key_returns_429_then_tries_second_key(self):
        call_count = 0

        def generate_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("429 quota exhausted")
            mock_resp = MagicMock()
            mock_resp.text = "tier2 result"
            return mock_resp

        mock_types = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.side_effect = generate_side_effect

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        mock_google = MagicMock()
        mock_google.genai = mock_genai
        modules = {
            "google": mock_google,
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        }

        env = {"GOOGLE_API_KEY_FREE_TIER": "free-key", "GOOGLE_API_KEY_TIER_1": "paid-key"}
        with patch.dict(os.environ, env):
            with patch("ai_cli.gemini._load_doppler_secrets"):
                with patch.dict("sys.modules", modules):
                    result = _run_grounded_search("test")

        assert result["success"] is True


# ---------------------------------------------------------------------------
# TestRunConcurrentSearches
# ---------------------------------------------------------------------------


class TestRunConcurrentSearches:
    def test_when_all_succeed_then_returns_all_results(self):
        def mock_search(query, *, timeout_s=120):
            return {"query": query, "content": f"result for {query}", "success": True, "error": ""}

        with patch("ai_cli.research._run_grounded_search", side_effect=mock_search):
            results = run_concurrent_searches(["q1", "q2", "q3"], quiet=True)

        assert len(results) == 3
        assert all(r["success"] for r in results)

    def test_when_some_fail_then_returns_mix(self):
        def mock_search(query, *, timeout_s=120):
            if query == "bad":
                return {"query": query, "content": "", "success": False, "error": "timeout"}
            return {"query": query, "content": "ok", "success": True, "error": ""}

        with patch("ai_cli.research._run_grounded_search", side_effect=mock_search):
            results = run_concurrent_searches(["good", "bad"], quiet=True)

        assert len(results) == 2
        successes = sum(1 for r in results if r["success"])
        assert successes == 1


# ---------------------------------------------------------------------------
# TestSynthesize
# ---------------------------------------------------------------------------


class TestSynthesize:
    def test_when_no_successful_results_then_returns_failure(self):
        bad_results = [{"query": "q", "content": "", "success": False, "error": "timeout"}]
        result = synthesize("test", bad_results, synthesis_model="deep-think", quiet=True)
        assert result.success is False
        assert "No successful" in result.error

    def test_when_success_then_returns_synthesis_content(self):
        search_results = [{"query": "q1", "content": "relevant info", "success": True, "error": ""}]
        ok = GeminiResult(content="synthesized answer", model="deep-think", success=True)
        with patch("ai_cli.gemini.run_gemini", return_value=ok):
            result = synthesize("test question", search_results, synthesis_model="deep-think", quiet=True)
        assert result.success is True
        assert result.content == "synthesized answer"


# ---------------------------------------------------------------------------
# TestRunStandard
# ---------------------------------------------------------------------------


class TestRunStandard:
    def _preset(self) -> DepthPreset:
        return DepthPreset(
            model="deep-think",
            search_provider="gemini",
            search_rounds=1,
            planning_model="deep-think",
            max_search_queries=3,
            max_concurrent_searches=2,
        )

    def test_when_full_run_then_saves_all_three_checkpoints(self, tmp_path):
        preset = self._preset()
        queries = ["q1", "q2"]
        search_results = [{"query": "q1", "content": "info", "success": True, "error": ""}]
        synthesis = GeminiResult(content="final answer", model="deep-think", success=True)

        with patch("ai_cli.research.RESEARCH_RUNS_DIR", tmp_path / "runs"):
            with patch("ai_cli.research.generate_queries", return_value=queries):
                with patch("ai_cli.research.run_concurrent_searches", return_value=search_results):
                    with patch("ai_cli.research.synthesize", return_value=synthesis):
                        with patch("ai_cli.gemini._log_to_file"):
                            with patch("ai_cli.gemini.DEFAULT_OUTPUT_DIR", tmp_path / "output"):
                                result = run_standard("test prompt", preset, quiet=True)

        assert result.success is True
        assert result.content == "final answer"
        run_dirs = list((tmp_path / "runs").iterdir())
        assert len(run_dirs) == 1
        files = {f.name for f in run_dirs[0].iterdir()}
        assert "step_01_query_generation.json" in files
        assert "step_02_search_results.json" in files
        assert "step_03_synthesis.json" in files

    def test_when_resume_then_skips_completed_steps(self, tmp_path):
        preset = self._preset()
        run_id = "20260101-120000-abc123"

        step1_path = tmp_path / run_id / "step_01_query_generation.json"
        step1_path.parent.mkdir(parents=True)
        step1_path.write_text(json.dumps({"queries": ["q1", "q2"], "completed": True}))

        step2_path = tmp_path / run_id / "step_02_search_results.json"
        step2_path.write_text(
            json.dumps({"results": [{"query": "q1", "content": "found", "success": True}], "completed": True})
        )

        synthesis = GeminiResult(content="resumed answer", model="deep-think", success=True)
        mock_gen = MagicMock()
        mock_search = MagicMock()

        with patch("ai_cli.research.RESEARCH_RUNS_DIR", tmp_path):
            with patch("ai_cli.research.generate_queries", mock_gen):
                with patch("ai_cli.research.run_concurrent_searches", mock_search):
                    with patch("ai_cli.research.synthesize", return_value=synthesis):
                        with patch("ai_cli.gemini._log_to_file"):
                            with patch("ai_cli.gemini.DEFAULT_OUTPUT_DIR", tmp_path / "output"):
                                result = run_standard("test prompt", preset, resume=run_id, quiet=True)

        mock_gen.assert_not_called()
        mock_search.assert_not_called()
        assert result.success is True
        assert result.content == "resumed answer"

    def test_when_synthesis_fails_then_returns_failure(self, tmp_path):
        preset = self._preset()
        queries = ["q1"]
        search_results = [{"query": "q1", "content": "info", "success": True, "error": ""}]
        fail = GeminiResult(success=False, model="deep-think", error="synthesis timeout")

        with patch("ai_cli.research.RESEARCH_RUNS_DIR", tmp_path / "runs"):
            with patch("ai_cli.research.generate_queries", return_value=queries):
                with patch("ai_cli.research.run_concurrent_searches", return_value=search_results):
                    with patch("ai_cli.research.synthesize", return_value=fail):
                        with patch("ai_cli.gemini._log_to_file"):
                            result = run_standard("test prompt", preset, quiet=True)

        assert result.success is False


# ---------------------------------------------------------------------------
# TestGeminiCliDepthRouting
# ---------------------------------------------------------------------------


class TestGeminiCliDepthRouting:
    def test_gemini_cli_when_depth_standard_then_routes_to_run_standard(self):
        ok = GeminiResult(content="standard result", model="deep-think", success=True)
        with patch("ai_cli.research.run_standard", return_value=ok) as mock_std:
            with patch("ai_cli.research.load_depth_preset") as mock_preset:
                mock_preset.return_value = DepthPreset()
                with pytest.raises(SystemExit) as exc:
                    from ai_cli.gemini import gemini_cli

                    gemini_cli(["test prompt", "-d", "standard"])
        assert exc.value.code == 0
        mock_std.assert_called_once()

    def test_gemini_cli_when_resume_and_quick_then_upgrades_to_standard(self):
        ok = GeminiResult(content="resumed", model="deep-think", success=True)
        with patch("ai_cli.research.run_standard", return_value=ok):
            with patch("ai_cli.research.load_depth_preset") as mock_preset:
                mock_preset.return_value = DepthPreset()
                with pytest.raises(SystemExit) as exc:
                    from ai_cli.gemini import gemini_cli

                    gemini_cli(["test prompt", "--resume", "20260101-120000-abc123"])
        assert exc.value.code == 0
        mock_preset.assert_called_once_with("standard", planning_model_override=None)

    def test_gemini_cli_when_depth_quick_then_delegates_to_aido(self):
        with patch("subprocess.call", return_value=0) as mock_call:
            with pytest.raises(SystemExit) as exc:
                from ai_cli.gemini import gemini_cli

                gemini_cli(["test prompt", "-d", "quick"])
        assert exc.value.code == 0
        cmd = mock_call.call_args[0][0]
        assert ["aido", "research", "-d", "quick"] == cmd[:4]
        assert "test prompt" in cmd
