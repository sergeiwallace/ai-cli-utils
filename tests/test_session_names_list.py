"""The available-name generator and the shipped seed (T-3.1).

Every artifact here is a fixture the test builds: a census-format first-name tarball
and a Hunspell dictionary tarball, each pinned by the digest the test computes. No
test reaches the network.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import io
import json
import re
import tarfile
from pathlib import Path

import pytest

from ai_cli import name_list

SOURCE_URL = "https://example.com/names-fixture.tar.gz"
WORDS_URL = "https://example.com/dictionary-fixture.tgz"
MALE = "names-fixture/names/dist.male.first"
FEMALE = "names-fixture/names/dist.female.first"
DIC = "package/index.dic"

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tarball(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, text in members.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _census(*names: str) -> str:
    return "".join(f"{name.upper():<15}0.100  0.100  {rank}\n" for rank, name in enumerate(names, start=1))


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sri512(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


@pytest.fixture
def artifacts():
    """A fixture source and dictionary, and a policy pinning both."""
    source = _tarball(
        {
            MALE: _census("ada", "bob", "cy", "eve", "otto", "robert", "art", "ben", "zed", "max"),
            FEMALE: _census("ida", "ann", "anna", "may", "eve", "flo", "liz"),
        }
    )
    dictionary = _tarball(
        {
            DIC: "\n".join(
                [
                    "7",  # the entry count line, never a word
                    "art/MS",  # a word with affix flags
                    "may\tpo:verb",  # a tab-suffixed word
                    "Ada/M",  # capitalised: a proper name, kept as a name
                    "max",  # a lowercase common word
                    "ben/S",  # a word kept back by keep_names
                    "flo's",  # not letters only, never a word
                ]
            )
        }
    )
    policy = copy.deepcopy(name_list.DEFAULT_POLICY)
    policy["source"] = {"url": SOURCE_URL, "digest": _sha256(source), "members": [MALE, FEMALE]}
    policy["confusable"].update(
        {"words_url": WORDS_URL, "words_digest": _sri512(dictionary), "words_member": DIC, "keep_names": ["ben"]}
    )
    policy["denylist"] = ["zed"]
    policy["confusable"]["extra_words"] = ["flo"]
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return {SOURCE_URL: source, WORDS_URL: dictionary}[url]

    return policy, fetch, fetched


def test_given_pinned_fixtures_when_generated_then_names_pass_every_static_filter(artifacts, tmp_path):
    policy, fetch, _ = artifacts

    generation = name_list.generate(
        policy, fetch=fetch, non_names={"bob"}, policy_commit="abc123", policy_committed_at="2026-01-01T00:00:00Z"
    )
    name_list.write_outputs(tmp_path, generation)

    lines = (tmp_path / "names.txt").read_text().splitlines()
    # cy is too short, robert too long; bob is a non-name, zed denylisted, art/may/max are
    # lowercase words, flo an extra word; ben is a word kept by keep_names; ada is a
    # capitalised dictionary entry, so a name; eve is in both files and appears once.
    assert lines == ["ada", "ann", "anna", "ben", "eve", "ida", "liz", "otto"]
    assert lines == sorted(set(lines))
    assert all(re.fullmatch(r"[a-z]{3,4}", line) for line in lines)


def test_given_pinned_fixtures_when_generated_then_the_record_carries_every_field(artifacts, tmp_path):
    policy, fetch, _ = artifacts

    generation = name_list.generate(
        policy, fetch=fetch, non_names=set(), policy_commit="abc123", policy_committed_at="2026-01-01T00:00:00Z"
    )
    name_list.write_outputs(tmp_path, generation)
    record = json.loads((tmp_path / "generation.json").read_text())

    assert record["source"] == {"url": SOURCE_URL, "digest": policy["source"]["digest"]}
    assert record["words"] == {"url": WORDS_URL, "digest": policy["confusable"]["words_digest"]}
    assert record["policy"] == policy
    assert record["policy_digest"] == name_list.policy_digest(policy)
    assert record["policy_commit"] == "abc123"
    assert record["policy_committed_at"] == "2026-01-01T00:00:00Z"
    assert record["count"] == len(generation.names) == 9
    assert record["count_by_length"] == {"3": 7, "4": 2}
    assert record["writer"].startswith("ai-cli-utils ")


def test_given_wider_limits_when_generated_then_the_length_bounds_follow_the_policy(artifacts):
    policy, fetch, _ = artifacts
    policy["limits"] = {"min_name_length": 2, "max_name_length": 6}

    names = name_list.generate(policy, fetch=fetch, non_names=set()).names

    assert "cy" in names
    assert "robert" in names
    assert all(2 <= len(name) <= 6 for name in names)


def test_given_a_dictionary_fixture_when_parsed_then_each_parsing_rule_holds():
    text = "5\nart/MS\nmay\tpo:verb\nAda/M\nmax\n"

    words = name_list.parse_common_words(text)

    assert words == {"art", "may", "max"}
    assert "5" not in words
    assert "ada" not in words


def _load_refresh_script():
    spec = importlib.util.spec_from_file_location(
        "refresh_session_names", REPO_ROOT / "scripts" / "refresh_session_names.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def refresh(artifacts, tmp_path, monkeypatch):
    """The refresh script, writing into a copy of a previously generated seed."""
    policy, fetch, _ = artifacts
    seed = tmp_path / "seed"
    name_list.write_outputs(seed, name_list.generate(policy, fetch=fetch, non_names=set()))
    before = {path.name: path.read_bytes() for path in seed.iterdir()}
    monkeypatch.setattr(name_list, "DATA_DIR", seed)
    monkeypatch.setattr(name_list, "DEFAULT_POLICY", policy)
    monkeypatch.setattr(name_list, "read_non_names", lambda path=None: set())
    return _load_refresh_script(), seed, before, policy, fetch


@pytest.mark.parametrize("which", ["source", "words"])
def test_given_a_digest_mismatch_when_refreshed_then_it_fails_and_every_output_is_byte_identical(
    refresh, monkeypatch, capsys, which
):
    script, seed, before, policy, fetch = refresh
    tampered = SOURCE_URL if which == "source" else WORDS_URL
    monkeypatch.setattr(name_list, "download", lambda url: fetch(url) + (b"x" if url == tampered else b""))

    assert script.main() == 1

    assert "digest mismatch" in capsys.readouterr().err
    assert {path.name: path.read_bytes() for path in seed.iterdir()} == before


def test_given_a_pinned_member_missing_when_refreshed_then_it_fails_and_every_output_is_byte_identical(
    refresh, monkeypatch, capsys
):
    script, seed, before, policy, fetch = refresh
    policy["source"]["members"] = [MALE, "names-fixture/names/missing.first"]
    monkeypatch.setattr(name_list, "download", fetch)

    assert script.main() == 1

    assert "missing.first" in capsys.readouterr().err
    assert {path.name: path.read_bytes() for path in seed.iterdir()} == before


def test_given_verified_downloads_when_refreshed_then_both_outputs_are_rewritten(refresh, monkeypatch, capsys):
    script, seed, before, policy, fetch = refresh
    policy["limits"] = {"min_name_length": 3, "max_name_length": 3}
    monkeypatch.setattr(name_list, "download", fetch)

    assert script.main() == 0

    assert (seed / "names.txt").read_bytes() != before["names.txt"]
    assert all(len(name) == 3 for name in name_list.read_names(seed / "names.txt"))
    assert "one-edit-free subset" in capsys.readouterr().out


def test_given_the_shipped_seed_when_read_then_it_matches_its_record_at_the_template_defaults():
    names = name_list.read_names(name_list.DATA_DIR / name_list.NAMES_FILE)
    record = json.loads((name_list.DATA_DIR / name_list.GENERATION_FILE).read_text())
    non_names = name_list.read_non_names()

    assert record["policy"] == name_list.DEFAULT_POLICY
    assert record["policy_digest"] == name_list.policy_digest(name_list.DEFAULT_POLICY)
    assert record["policy_commit"] is None
    assert names == sorted(set(names))
    assert all(re.fullmatch(r"[a-z]{3,4}", name) for name in names)
    assert not set(names) & non_names
    assert record["count"] == len(names)
    assert sum(record["count_by_length"].values()) == len(names)
    assert record["count_by_length"] == {
        str(length): sum(1 for name in names if len(name) == length) for length in (3, 4)
    }


def test_given_a_fleet_policy_file_when_snapshotted_then_only_per_machine_tables_are_left_out():
    config = {"limits": {"min_name_length": 3}, "denylist": [], "hosts": {"box": {"enabled": True}}, "defaults": {}}

    assert name_list.fleet_policy(config) == {"limits": {"min_name_length": 3}, "denylist": []}
