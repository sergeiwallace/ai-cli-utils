import json
import os

import pytest
from conftest import run_cli

from ai_cli.main import _cc_project_dir

TITLE = "test-session"
OLD_ID = "11111111-1111-4111-8111-111111111111"
LATEST_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def transcripts(tmp_path):
    cwd = tmp_path / "myproject"
    cwd.mkdir()
    project = _cc_project_dir(cwd)
    project.mkdir(parents=True)
    old = project / f"{OLD_ID}.jsonl"
    latest = project / f"{LATEST_ID}.jsonl"
    for path, stamp in ((old, 1), (latest, 2)):
        path.write_text(json.dumps({"type": "custom-title", "customTitle": TITLE}) + "\n", encoding="utf-8")
        os.utime(path, (stamp, stamp))
    registry = project.parent.parent / "sessions"
    registry.mkdir()
    (registry / "9001.json").write_text(json.dumps({"sessionId": LATEST_ID, "kind": "bg"}), encoding="utf-8")
    return cwd, old, latest


def _record_exit(cwd, transcript, *, stamped, title=TITLE):
    root = transcript.parent.parent.parent / "resume"
    launch = root / "launch"
    stamps = root / "boot-stamps"
    launch.mkdir(parents=True)
    identity = {"key": title, "boot_id": "test-boot", "pid": 9000, "pane_id": "%1"}
    ended_at = "2026-01-01T00:00:00Z"
    end = {**identity, "ended_at": ended_at, "session_id": transcript.stem, "reason": "prompt_input_exit"}
    (launch / f"{title}.end.json").write_text(json.dumps(end), encoding="utf-8")
    if stamped is not None:
        stamps.mkdir()
        stamp = {**identity, "cwd": str(cwd), "transcript_path": str(stamped), "ended_at": ended_at}
        (stamps / f"{title}.json").write_text(json.dumps(stamp), encoding="utf-8")


def _resolve(cwd, *args):
    return run_cli(["ai", "internal", "resolve-continue-target", str(cwd), TITLE, *args])


def test_given_last_written_transcript_is_registered_bg_when_restarting_then_resumes_it(transcripts):
    cwd, _, latest = transcripts
    _record_exit(cwd, latest, stamped=latest)

    code, out, err = _resolve(cwd, "--restart")

    assert code == 0, err
    assert out.strip() == str(latest)


def test_given_exit_record_for_latest_bg_transcript_without_stamp_when_restarting_then_resumes_it(transcripts):
    cwd, _, latest = transcripts
    _record_exit(cwd, latest, stamped=None)

    code, out, err = _resolve(cwd, "--restart")

    assert code == 0, err
    assert out.strip() == str(latest)


def test_given_exit_record_for_differently_titled_transcript_without_stamp_when_restarting_then_refuses(transcripts):
    cwd, _, latest = transcripts
    with latest.open("a", encoding="utf-8") as transcript:
        transcript.write(json.dumps({"type": "custom-title", "customTitle": "other-session"}) + "\n")
    _record_exit(cwd, latest, stamped=None)

    code, out, err = _resolve(cwd, "--restart")

    assert code == 1
    assert not out
    assert OLD_ID in err and LATEST_ID in err


def test_given_no_exit_record_when_restart_candidates_disagree_then_refuses_with_both_ids(transcripts):
    cwd, _, _ = transcripts

    code, out, err = _resolve(cwd, "--restart")

    assert code != 0
    assert not out
    assert OLD_ID in err and LATEST_ID in err


def test_given_exit_and_launch_records_disagree_when_restarting_then_refuses_with_both_ids(transcripts):
    cwd, old, latest = transcripts
    _record_exit(cwd, latest, stamped=old)

    code, out, err = _resolve(cwd, "--restart")

    assert code != 0
    assert not out
    assert OLD_ID in err and LATEST_ID in err


def test_given_same_titled_bg_transcript_when_resolving_by_name_then_keeps_interactive_match(transcripts):
    cwd, old, _ = transcripts

    code, out, err = _resolve(cwd)

    assert code == 0, err
    assert out.strip() == str(old)
