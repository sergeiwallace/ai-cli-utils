"""Tests for process_hygiene.py — ai ps subcommand."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from ai_cli.process_hygiene import (
    ORPHAN_THRESHOLD,
    SUSPECT_THRESHOLD,
    ProcessInfo,
    _fmt_age,
    _load_cache,
    _parse_etime,
    _parse_remote_ps,
    _run_lsof_udp,
    _run_ps,
    _save_cache,
    _verdict_for,
    auto_clean_orphans,
    cmd_ps,
    collect_local_processes,
    collect_remote_processes,
    format_ps_table,
    score_mosh_server,
    score_signal_watch,
    score_watcher,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proc(**kwargs) -> ProcessInfo:
    defaults = dict(
        pid=1234,
        name="mosh-server",
        age_seconds=3600.0,
        score=0,
        verdict="active",
        detail="active",
        machine="local",
        args="mosh-server 60001",
    )
    defaults.update(kwargs)
    return ProcessInfo(**defaults)


# ---------------------------------------------------------------------------
# _parse_etime
# ---------------------------------------------------------------------------


class TestParseEtime:
    def test_given_mm_ss_when_parsed_then_returns_seconds(self):
        assert _parse_etime("01:30") == 90.0

    def test_given_hh_mm_ss_when_parsed_then_returns_seconds(self):
        assert _parse_etime("02:30:00") == 9000.0

    def test_given_dd_hh_mm_ss_when_parsed_then_returns_seconds(self):
        assert _parse_etime("1-00:00:00") == 86400.0

    def test_given_multiday_when_parsed_then_returns_seconds(self):
        assert _parse_etime("27-00:00:00") == 27 * 86400.0

    def test_given_invalid_string_when_parsed_then_returns_zero(self):
        assert _parse_etime("invalid") == 0.0

    def test_given_empty_string_when_parsed_then_returns_zero(self):
        assert _parse_etime("") == 0.0


# ---------------------------------------------------------------------------
# _verdict_for
# ---------------------------------------------------------------------------


class TestVerdictFor:
    def test_given_score_below_suspect_then_active(self):
        assert _verdict_for(SUSPECT_THRESHOLD - 1) == "active"

    def test_given_score_at_suspect_threshold_then_suspect(self):
        assert _verdict_for(SUSPECT_THRESHOLD) == "suspect"

    def test_given_score_at_orphan_threshold_then_orphaned(self):
        assert _verdict_for(ORPHAN_THRESHOLD) == "orphaned"

    def test_given_score_above_orphan_threshold_then_orphaned(self):
        assert _verdict_for(100) == "orphaned"


# ---------------------------------------------------------------------------
# score_mosh_server
# ---------------------------------------------------------------------------


class TestScoreMoshServer:
    def test_given_no_client_and_old_process_then_orphaned(self):
        # no client (+50), age > 24h (+20), no tmux session (+10) = 80 → orphaned
        score, detail = score_mosh_server(
            pid=1234,
            age_seconds=25 * 3600,
            args="mosh-server 60001",
            udp_ports=set(),
            tmux_sessions={},
        )
        assert score >= ORPHAN_THRESHOLD
        assert "no active client" in detail

    def test_given_client_connected_then_active(self):
        score, detail = score_mosh_server(
            pid=1234,
            age_seconds=3600.0,
            args="mosh-server 60001",
            udp_ports={"60001"},
            tmux_sessions={},
        )
        assert score < SUSPECT_THRESHOLD

    def test_given_lsof_unavailable_and_age_over_48h_then_orphaned(self):
        score, detail = score_mosh_server(
            pid=1234,
            age_seconds=49 * 3600,
            args="mosh-server",
            udp_ports=set(),
            tmux_sessions={},
            lsof_available=False,
        )
        assert score >= ORPHAN_THRESHOLD
        assert "lsof unavailable" in detail

    def test_given_lsof_unavailable_and_age_under_48h_then_below_orphan(self):
        score, detail = score_mosh_server(
            pid=1234,
            age_seconds=24 * 3600,
            args="mosh-server",
            udp_ports=set(),
            tmux_sessions={},
            lsof_available=False,
        )
        assert score < ORPHAN_THRESHOLD

    def test_given_attached_tmux_session_then_score_reduced(self):
        score_no_tmux, _ = score_mosh_server(
            pid=1234,
            age_seconds=7 * 3600,
            args="mosh-server myproject 60001",
            udp_ports=set(),
            tmux_sessions={},
        )
        score_attached, _ = score_mosh_server(
            pid=1234,
            age_seconds=7 * 3600,
            args="mosh-server myproject 60001",
            udp_ports=set(),
            tmux_sessions={"myproject": True},
        )
        assert score_attached < score_no_tmux

    def test_given_unattached_tmux_long_running_then_score_higher(self):
        score_short, _ = score_mosh_server(
            pid=1234,
            age_seconds=30 * 60,  # 30 minutes — not > 2h
            args="mosh-server myproject 60001",
            udp_ports=set(),
            tmux_sessions={"myproject": False},
        )
        score_long, _ = score_mosh_server(
            pid=1234,
            age_seconds=3 * 3600,  # 3 hours — > 2h
            args="mosh-server myproject 60001",
            udp_ports=set(),
            tmux_sessions={"myproject": False},
        )
        assert score_long > score_short

    def test_given_age_over_24h_then_extra_points(self):
        score_6h, _ = score_mosh_server(
            pid=1, age_seconds=7 * 3600, args="mosh-server", udp_ports=set(), tmux_sessions={}
        )
        score_24h, _ = score_mosh_server(
            pid=1, age_seconds=25 * 3600, args="mosh-server", udp_ports=set(), tmux_sessions={}
        )
        assert score_24h > score_6h


# ---------------------------------------------------------------------------
# score_signal_watch
# ---------------------------------------------------------------------------


class TestScoreSignalWatch:
    def test_given_tmux_session_gone_then_suspect(self):
        # tmux session gone (+30) + age > 12h (+20) = 50 → suspect
        score, detail = score_signal_watch(
            pid=1234,
            age_seconds=13 * 3600.0,
            args="ai internal signal-watch myproject mysession",
            tmux_sessions={},
        )
        assert score >= SUSPECT_THRESHOLD
        assert "mysession" in detail

    def test_given_tmux_session_alive_then_active(self):
        score, detail = score_signal_watch(
            pid=1234,
            age_seconds=3600.0,
            args="ai internal signal-watch myproject mysession",
            tmux_sessions={"mysession": True},
        )
        assert score < SUSPECT_THRESHOLD

    def test_given_age_over_12h_then_extra_points(self):
        score_short, _ = score_signal_watch(1234, 3600.0, "ai internal signal-watch p s", {})
        score_long, _ = score_signal_watch(1234, 13 * 3600.0, "ai internal signal-watch p s", {})
        assert score_long > score_short


# ---------------------------------------------------------------------------
# score_watcher
# ---------------------------------------------------------------------------


class TestScoreWatcher:
    def test_given_no_tmux_sessions_then_suspect(self):
        score, detail = score_watcher(1234, 3600.0, "ai memory watch", {})
        assert score >= SUSPECT_THRESHOLD

    def test_given_tmux_sessions_and_young_process_then_active(self):
        score, detail = score_watcher(1234, 3600.0, "ai memory watch", {"some-session": True})
        assert score < SUSPECT_THRESHOLD

    def test_given_old_process_then_higher_score(self):
        score_short, _ = score_watcher(1234, 3600.0, "ai memory watch", {"s": True})
        score_long, _ = score_watcher(1234, 25 * 3600.0, "ai memory watch", {"s": True})
        assert score_long > score_short


# ---------------------------------------------------------------------------
# _run_ps
# ---------------------------------------------------------------------------


class TestRunPs:
    def test_given_valid_ps_output_when_called_then_returns_rows(self):
        ps_out = "1234 mosh-server 02:30:00 mosh-server --port 60001\n5678 bash 00:01:00 bash\n"
        rows = _run_ps(ps_fn=lambda: (ps_out, 0))
        assert len(rows) == 2
        assert rows[0]["pid"] == 1234
        assert rows[0]["name"] == "mosh-server"
        assert rows[0]["etime"] == "02:30:00"

    def test_given_non_zero_rc_when_called_then_returns_empty(self):
        rows = _run_ps(ps_fn=lambda: ("error", 1))
        assert rows == []

    def test_given_invalid_pid_line_when_called_then_skipped(self):
        rows = _run_ps(ps_fn=lambda: ("notpid comm 00:01:00 args\n", 0))
        assert rows == []


# ---------------------------------------------------------------------------
# _run_lsof_udp
# ---------------------------------------------------------------------------


class TestRunLsofUdp:
    def test_given_mosh_client_in_output_when_called_then_returns_port(self):
        lsof_out = (
            "mosh-client 1234 user  10u  IPv4 12345  0t0  UDP *:60001\n"
            "other-proc  5678 user  10u  IPv4 12346  0t0  UDP *:8080\n"
        )
        ports = _run_lsof_udp(lsof_fn=lambda: (lsof_out, 0))
        assert "60001" in ports
        assert "8080" not in ports

    def test_given_non_zero_rc_when_called_then_returns_empty(self):
        ports = _run_lsof_udp(lsof_fn=lambda: ("", 1))
        assert ports == set()

    def test_given_no_mosh_client_when_called_then_returns_empty(self):
        ports = _run_lsof_udp(lsof_fn=lambda: ("other-proc 1234 user ...\n", 0))
        assert ports == set()


# ---------------------------------------------------------------------------
# collect_local_processes
# ---------------------------------------------------------------------------


class TestCollectLocalProcesses:
    def _ps_out(self, *lines):
        return "\n".join(lines) + "\n"

    def test_given_mosh_server_without_client_when_collected_then_suspect_or_orphaned(self):
        ps_out = self._ps_out("1234 mosh-server 07:00:00 mosh-server 60001")
        procs = collect_local_processes(
            ps_fn=lambda: (ps_out, 0),
            lsof_fn=lambda: ("", 0),
            tmux_fn=lambda: ("", 0),
        )
        assert len(procs) == 1
        assert procs[0].name == "mosh-server"
        assert procs[0].verdict in ("suspect", "orphaned")

    def test_given_mosh_server_with_client_when_collected_then_active(self):
        ps_out = self._ps_out("1234 mosh-server 00:30:00 mosh-server 60001")
        lsof_out = "mosh-client 5678 user 10u IPv4 0 0t0 UDP *:60001\n"
        procs = collect_local_processes(
            ps_fn=lambda: (ps_out, 0),
            lsof_fn=lambda: (lsof_out, 0),
            tmux_fn=lambda: ("", 0),
        )
        assert len(procs) == 1
        assert procs[0].verdict == "active"

    def test_given_signal_watch_with_gone_session_when_collected_then_suspect(self):
        # session gone (+30) + age > 12h (+20) = 50 → suspect
        ps_out = self._ps_out("2345 python3 13:00:00 ai internal signal-watch myproject mysession")
        procs = collect_local_processes(
            ps_fn=lambda: (ps_out, 0),
            lsof_fn=lambda: ("", 0),
            tmux_fn=lambda: ("", 0),
        )
        assert len(procs) == 1
        assert procs[0].verdict in ("suspect", "orphaned")

    def test_given_autossh_when_collected_then_active_and_persistent(self):
        ps_out = self._ps_out("3456 autossh 10:00:00 autossh -R 9222:localhost:9222 host")
        procs = collect_local_processes(
            ps_fn=lambda: (ps_out, 0),
            lsof_fn=lambda: ("", 0),
            tmux_fn=lambda: ("", 0),
        )
        assert len(procs) == 1
        assert procs[0].verdict == "active"
        assert procs[0].detail == "persistent"

    def test_given_unrelated_process_when_collected_then_not_included(self):
        ps_out = self._ps_out("9999 bash 00:01:00 bash --login")
        procs = collect_local_processes(
            ps_fn=lambda: (ps_out, 0),
            lsof_fn=lambda: ("", 0),
            tmux_fn=lambda: ("", 0),
        )
        assert procs == []

    def test_given_ps_failure_when_collected_then_returns_empty(self):
        procs = collect_local_processes(
            ps_fn=lambda: ("", 1),
            lsof_fn=lambda: ("", 0),
            tmux_fn=lambda: ("", 0),
        )
        assert procs == []


# ---------------------------------------------------------------------------
# Cache read/write
# ---------------------------------------------------------------------------


class TestCacheReadWrite:
    def test_given_saved_cache_when_loaded_then_matches_original(self, tmp_path):
        proc = _proc(pid=9000, verdict="orphaned", score=90)
        path = tmp_path / "cache.json"
        _save_cache(path, [proc])
        result = _load_cache(path)
        assert result is not None
        loaded, age = result
        assert len(loaded) == 1
        assert loaded[0].pid == 9000
        assert age < 2.0  # loaded immediately after save

    def test_given_missing_cache_when_loaded_then_returns_none(self, tmp_path):
        assert _load_cache(tmp_path / "nonexistent.json") is None

    def test_given_corrupt_cache_when_loaded_then_returns_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        assert _load_cache(path) is None

    def test_given_save_error_when_saving_then_no_exception(self, tmp_path):
        # Read-only path — should not raise
        path = tmp_path / "subdir" / "cache.json"
        # Make parent read-only
        parent = tmp_path / "subdir"
        parent.mkdir()
        parent.chmod(0o555)
        try:
            _save_cache(path, [])  # should not raise
        finally:
            parent.chmod(0o755)


# ---------------------------------------------------------------------------
# _parse_remote_ps
# ---------------------------------------------------------------------------


class TestParseRemotePs:
    def test_given_mosh_server_line_when_parsed_then_included(self):
        stdout = "1234 mosh-server 27-00:00:00 mosh-server\n"
        procs = _parse_remote_ps(stdout)
        assert len(procs) == 1
        assert procs[0].machine == "remote"

    def test_given_nats_server_when_parsed_then_active(self):
        stdout = "5678 nats-server 04:00:00 nats-server -c /etc/nats.conf\n"
        procs = _parse_remote_ps(stdout)
        assert len(procs) == 1
        assert procs[0].verdict == "active"
        assert procs[0].detail == "persistent"

    def test_given_unrelated_process_when_parsed_then_excluded(self):
        stdout = "9999 bash 00:01:00 bash\n"
        procs = _parse_remote_ps(stdout)
        assert procs == []


# ---------------------------------------------------------------------------
# collect_remote_processes
# ---------------------------------------------------------------------------


class TestCollectRemoteProcesses:
    def test_given_fresh_ssh_response_when_collected_then_returns_procs(self, tmp_path):
        ps_out = "1234 mosh-server 27-00:00:00 mosh-server\n"

        def fake_ssh(user, host):
            return ps_out, 0

        with patch("ai_cli.process_hygiene._cache_path", return_value=tmp_path / "c.json"):
            procs, cache_age = collect_remote_processes(
                host="192.0.2.1",
                user="user",
                use_cache=False,
                ssh_fn=fake_ssh,
            )
        assert len(procs) == 1
        assert cache_age is None  # freshly fetched

    def test_given_cached_results_when_within_ttl_then_uses_cache(self, tmp_path):
        cached_proc = _proc(pid=5678, machine="remote")
        cache_file = tmp_path / "cache.json"
        _save_cache(cache_file, [cached_proc])

        ssh_called = []

        def fake_ssh(user, host):
            ssh_called.append(True)
            return "", 0

        with patch("ai_cli.process_hygiene._cache_path", return_value=cache_file):
            procs, cache_age = collect_remote_processes(
                host="192.0.2.1",
                user="user",
                cache_ttl=3600,
                ssh_fn=fake_ssh,
            )
        assert not ssh_called  # cache was used
        assert procs[0].pid == 5678
        assert cache_age is not None

    def test_given_force_refresh_when_called_then_ssh_always_used(self, tmp_path):
        cached_proc = _proc(pid=5678, machine="remote")
        cache_file = tmp_path / "cache.json"
        _save_cache(cache_file, [cached_proc])

        ssh_called = []

        def fake_ssh(user, host):
            ssh_called.append(True)
            return "1234 mosh-server 01:00:00 mosh-server\n", 0

        with patch("ai_cli.process_hygiene._cache_path", return_value=cache_file):
            collect_remote_processes(
                host="192.0.2.1",
                user="user",
                force_refresh=True,
                ssh_fn=fake_ssh,
            )
        assert ssh_called  # SSH was called despite cache

    def test_given_ssh_failure_when_cache_exists_then_returns_cache(self, tmp_path):
        cached_proc = _proc(pid=5678, machine="remote")
        cache_file = tmp_path / "cache.json"
        _save_cache(cache_file, [cached_proc])

        with patch("ai_cli.process_hygiene._cache_path", return_value=cache_file):
            procs, cache_age = collect_remote_processes(
                host="192.0.2.1",
                user="user",
                force_refresh=True,
                ssh_fn=lambda u, h: ("", 1),
            )
        assert procs[0].pid == 5678

    def test_given_ssh_failure_and_no_cache_when_called_then_returns_empty(self, tmp_path):
        with patch("ai_cli.process_hygiene._cache_path", return_value=tmp_path / "none.json"):
            procs, cache_age = collect_remote_processes(
                host="192.0.2.1",
                user="user",
                force_refresh=True,
                ssh_fn=lambda u, h: ("", 1),
            )
        assert procs == []


# ---------------------------------------------------------------------------
# auto_clean_orphans
# ---------------------------------------------------------------------------


class TestAutoCleanOrphans:
    def _mock_terminate(self, side_effect=None):
        """Return a context-manager that mocks psutil.Process.terminate()."""
        mock_proc = MagicMock()
        if side_effect is not None:
            mock_proc.terminate.side_effect = side_effect
        return patch("psutil.Process", return_value=mock_proc), mock_proc

    def test_given_orphaned_local_process_when_cleaned_then_terminate_called(self, tmp_path):
        proc = _proc(pid=9999, verdict="orphaned", score=90, machine="local")
        ctx, mock_proc = self._mock_terminate()
        with ctx:
            killed = auto_clean_orphans([proc], log_path=tmp_path / "log.txt")
        mock_proc.terminate.assert_called_once()
        assert len(killed) == 1

    def test_given_remote_orphaned_process_when_cleaned_then_skipped(self, tmp_path):
        proc = _proc(pid=9999, verdict="orphaned", score=90, machine="remote")
        ctx, mock_proc = self._mock_terminate()
        with ctx:
            killed = auto_clean_orphans([proc], log_path=tmp_path / "log.txt")
        mock_proc.terminate.assert_not_called()
        assert killed == []

    def test_given_suspect_process_when_cleaned_then_skipped(self, tmp_path):
        proc = _proc(pid=9999, verdict="suspect", score=55, machine="local")
        ctx, mock_proc = self._mock_terminate()
        with ctx:
            killed = auto_clean_orphans([proc], log_path=tmp_path / "log.txt")
        mock_proc.terminate.assert_not_called()
        assert killed == []

    def test_given_already_dead_process_when_cleaned_then_counted_as_killed(self, tmp_path):
        import psutil as _psutil

        proc = _proc(pid=9999, verdict="orphaned", score=90, machine="local")
        ctx, _ = self._mock_terminate(side_effect=_psutil.NoSuchProcess(9999))
        with ctx:
            killed = auto_clean_orphans([proc], log_path=tmp_path / "log.txt")
        assert len(killed) == 1  # already dead — counts as cleaned

    def test_given_permission_error_when_cleaned_then_not_in_killed(self, tmp_path):
        import psutil as _psutil

        proc = _proc(pid=9999, verdict="orphaned", score=90, machine="local")
        ctx, _ = self._mock_terminate(side_effect=_psutil.AccessDenied(9999))
        with ctx:
            killed = auto_clean_orphans([proc], log_path=tmp_path / "log.txt")
        assert killed == []

    def test_given_killed_process_when_log_path_writable_then_log_written(self, tmp_path):
        proc = _proc(pid=9999, verdict="orphaned", score=90, machine="local")
        log = tmp_path / "log.txt"
        ctx, _ = self._mock_terminate()
        with ctx:
            auto_clean_orphans([proc], log_path=log)
        assert log.exists()
        assert "9999" in log.read_text()

    def test_given_dry_run_when_called_then_no_terminate_called(self, tmp_path):
        proc = _proc(pid=9999, verdict="orphaned", score=90, machine="local")
        ctx, mock_proc = self._mock_terminate()
        with ctx:
            killed = auto_clean_orphans([proc], log_path=tmp_path / "log.txt", dry_run=True)
        mock_proc.terminate.assert_not_called()
        assert len(killed) == 1

    def test_given_no_orphans_when_cleaned_then_no_log_written(self, tmp_path):
        proc = _proc(pid=9999, verdict="active", score=0, machine="local")
        log = tmp_path / "log.txt"
        auto_clean_orphans([proc], log_path=log)
        assert not log.exists()


# ---------------------------------------------------------------------------
# format_ps_table
# ---------------------------------------------------------------------------


class TestFormatPsTable:
    def test_given_no_processes_when_formatted_then_shows_not_found(self):
        output = format_ps_table([], [])
        assert "No managed processes found" in output

    def test_given_orphaned_local_process_when_formatted_then_shows_warning(self):
        proc = _proc(pid=1234, verdict="orphaned", score=90, machine="local", detail="no active client")
        output = format_ps_table([proc], [])
        assert "⚠" in output
        assert "orphaned" in output
        assert "LOCAL" in output

    def test_given_cached_remote_when_formatted_then_shows_cache_age(self):
        proc = _proc(pid=5678, verdict="active", score=0, machine="remote")
        output = format_ps_table([], [proc], cache_age=120.0)
        assert "cached" in output
        assert "ai ps --refresh" in output

    def test_given_fresh_remote_when_formatted_then_no_cache_note(self):
        proc = _proc(pid=5678, verdict="active", score=0, machine="remote")
        output = format_ps_table([], [proc], cache_age=None)
        assert "cached" not in output

    def test_given_suspect_process_when_formatted_then_shows_tilde(self):
        proc = _proc(pid=1234, verdict="suspect", score=55, machine="local")
        output = format_ps_table([proc], [])
        assert "~" in output


# ---------------------------------------------------------------------------
# _fmt_age
# ---------------------------------------------------------------------------


class TestFmtAge:
    def test_seconds_shows_minutes(self):
        assert _fmt_age(90) == "1m"

    def test_hours_shows_hours(self):
        assert _fmt_age(3700) == "1h"

    def test_days_shows_days(self):
        assert _fmt_age(86401) == "1d"


# ---------------------------------------------------------------------------
# cmd_ps (CLI dispatch)
# ---------------------------------------------------------------------------


class TestCmdPs:
    def _make_config(self, **overrides):
        cfg: dict = {}
        cfg.update(overrides)
        return cfg

    def test_given_ps_default_when_called_then_prints_table(self, tmp_path):
        orphan = _proc(pid=1234, verdict="orphaned", score=90, machine="local")
        output: list[str] = []

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[orphan]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
        ):
            rc = cmd_ps([], self._make_config(), stdout_fn=output.append)

        assert rc == 0
        full = "\n".join(output)
        assert "orphaned" in full

    def test_given_ps_clean_with_orphan_when_confirmed_then_kills(self, tmp_path):
        orphan = _proc(pid=9999, verdict="orphaned", score=90, machine="local")
        output: list[str] = []

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[orphan]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
            patch("ai_cli.process_hygiene.auto_clean_orphans", return_value=[orphan]) as mock_clean,
            patch("builtins.input", return_value="y"),
        ):
            rc = cmd_ps(["clean"], self._make_config(), stdout_fn=output.append)

        assert rc == 0
        mock_clean.assert_called_once()

    def test_given_ps_clean_when_user_declines_then_no_kill(self, tmp_path):
        orphan = _proc(pid=9999, verdict="orphaned", score=90, machine="local")

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[orphan]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
            patch("ai_cli.process_hygiene.auto_clean_orphans") as mock_clean,
            patch("builtins.input", return_value="n"),
        ):
            rc = cmd_ps(["clean"], self._make_config())

        assert rc == 0
        mock_clean.assert_not_called()

    def test_given_ps_clean_force_when_called_then_kills_without_prompt(self):
        orphan = _proc(pid=9999, verdict="orphaned", score=90, machine="local")

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[orphan]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
            patch("ai_cli.process_hygiene.auto_clean_orphans", return_value=[orphan]) as mock_clean,
            patch("builtins.input") as mock_input,
        ):
            rc = cmd_ps(["clean", "--force"], self._make_config())

        assert rc == 0
        mock_input.assert_not_called()  # no prompt with --force
        mock_clean.assert_called_once()

    def test_given_ps_clean_force_short_flag_when_called_then_kills(self):
        orphan = _proc(pid=9999, verdict="orphaned", score=90, machine="local")

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[orphan]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
            patch("ai_cli.process_hygiene.auto_clean_orphans", return_value=[orphan]) as mock_clean,
            patch("builtins.input") as mock_input,
        ):
            cmd_ps(["clean", "-f"], self._make_config())

        mock_input.assert_not_called()
        mock_clean.assert_called_once()

    def test_given_ps_clean_when_no_orphans_then_prints_none_found(self):
        output: list[str] = []

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
        ):
            rc = cmd_ps(["clean"], self._make_config(), stdout_fn=output.append)

        assert rc == 0
        assert any("No orphaned" in line for line in output)

    def test_given_ps_refresh_when_called_then_force_refresh_passed(self):
        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)) as mock_remote,
        ):
            cmd_ps(
                ["--refresh"],
                self._make_config(remote={"host": "192.0.2.1", "user": "user"}),
            )

        call_kwargs = mock_remote.call_args
        assert call_kwargs.kwargs.get("force_refresh") is True

    def test_given_ps_cron_when_orphans_found_then_prints_summary(self):
        orphan = _proc(pid=9999, verdict="orphaned", score=90, machine="local")
        output: list[str] = []

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[orphan]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
            patch("ai_cli.process_hygiene.auto_clean_orphans", return_value=[orphan]),
        ):
            rc = cmd_ps(["cron"], self._make_config(), stdout_fn=output.append)

        assert rc == 0
        full = "\n".join(output)
        assert "Cleaned 1" in full

    def test_given_ps_cron_when_no_orphans_then_no_output(self):
        output: list[str] = []

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
            patch("ai_cli.process_hygiene.auto_clean_orphans", return_value=[]),
        ):
            rc = cmd_ps(["cron"], self._make_config(), stdout_fn=output.append)

        assert rc == 0
        assert output == []

    def test_given_no_remote_config_when_ps_called_then_no_remote_fetch(self):
        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[]),
            patch("ai_cli.process_hygiene.collect_remote_processes") as mock_remote,
        ):
            cmd_ps([], self._make_config())

        mock_remote.assert_not_called()

    def test_given_ps_clean_force_includes_suspect(self):
        suspect = _proc(pid=9998, verdict="suspect", score=55, machine="local")
        orphan = _proc(pid=9999, verdict="orphaned", score=90, machine="local")
        killed: list[ProcessInfo] = []

        def fake_clean(procs, log_path, dry_run=False):
            killed.extend(procs)
            return procs

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[orphan, suspect]),
            patch("ai_cli.process_hygiene.collect_remote_processes", return_value=([], None)),
            patch("ai_cli.process_hygiene.auto_clean_orphans", side_effect=fake_clean),
            patch("builtins.input"),
        ):
            cmd_ps(["clean", "--force"], self._make_config())

        # Both orphaned and suspect should be passed to auto_clean_orphans
        assert any(p.pid == 9998 for p in killed)
        assert any(p.pid == 9999 for p in killed)

    def test_given_vpn_active_when_ps_called_then_uses_vpn_host(self):
        """When mullvad reports Connected and vpn_host is configured, cmd_ps must SSH to vpn_host."""
        config = {
            "remote": {
                "host": "100.64.0.1",
                "vpn_host": "192.0.2.1",
                "user": "user",
            }
        }
        captured_host: list[str] = []

        def fake_collect_remote(host, user, **kwargs):
            captured_host.append(host)
            return [], None

        mullvad_result = type("R", (), {"stdout": "Connected\n", "returncode": 0})()

        with (
            patch("ai_cli.process_hygiene.collect_local_processes", return_value=[]),
            patch("ai_cli.process_hygiene.collect_remote_processes", side_effect=fake_collect_remote),
            patch("subprocess.run", return_value=mullvad_result),
        ):
            cmd_ps([], config)

        assert captured_host == ["192.0.2.1"]


class TestCollectRemoteProcessesConnectTimeout:
    def test_given_no_ssh_fn_when_called_then_ssh_includes_connect_timeout(self):
        """collect_remote_processes must pass ConnectTimeout=5 to prevent hangs on unreachable hosts."""
        ssh_calls: list[list] = []

        def fake_run(cmd, **kwargs):
            ssh_calls.append(cmd)
            result = type("R", (), {"stdout": "", "returncode": 0})()
            return result

        with (
            patch("ai_cli.process_hygiene._cache_path", return_value=None.__class__.__new__(type(None))),
            patch("subprocess.run", side_effect=fake_run),
            patch("ai_cli.process_hygiene._load_cache", return_value=None),
            patch("ai_cli.process_hygiene._save_cache"),
        ):
            collect_remote_processes(host="192.0.2.1", user="user", use_cache=False)

        assert ssh_calls, "subprocess.run was not called"
        ssh_cmd = ssh_calls[0]
        assert "ConnectTimeout=5" in " ".join(ssh_cmd)
