# fmt: off
"""The comment ratchet, run the way the pre-commit hook runs it.

Each test builds a throwaway git repository holding a copy of
``scripts/comment_ratchet.py`` and runs ``python3 scripts/comment_ratchet.py`` in it.
The last test runs the ratchet on this repository itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "comment_ratchet.py"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "comment_ratchet.py")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)


def ratchet(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/comment_ratchet.py", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def total(repo: Path, path: str) -> int:
    result = ratchet(repo, "--list", path)
    assert result.returncode == 0, result.stderr
    summary = result.stdout.strip().splitlines()[-1]
    assert summary.startswith("total: "), result.stdout
    return int(summary.split()[1])


def baseline(repo: Path) -> dict[str, int]:
    return json.loads((repo / "comment-baseline.json").read_text())


def test_given_a_new_comment_when_the_hook_runs_then_it_fails_naming_the_line(repo):
    write(repo, "app.py", "x = 1\n# explains x\n")

    result = ratchet(repo)

    assert result.returncode == 1
    assert "app.py:2: explains x" in result.stderr


def test_given_no_comments_and_no_baseline_when_the_hook_runs_then_it_passes(repo):
    write(repo, "app.py", "x = 1\n")

    assert ratchet(repo).returncode == 0


def test_given_a_baselined_comment_when_the_hook_runs_then_it_passes(repo):
    write(repo, "app.py", "x = 1  # explains x\n")

    assert ratchet(repo, "--init").returncode == 0
    assert baseline(repo) == {"app.py": 1}
    assert ratchet(repo).returncode == 0


def test_given_a_baselined_file_when_it_gains_a_comment_then_the_hook_fails(repo):
    write(repo, "app.py", "x = 1  # explains x\n")
    ratchet(repo, "--init")
    write(repo, "app.py", "x = 1  # explains x\n# and another\n")

    result = ratchet(repo)

    assert result.returncode == 1
    assert "app.py: 2 comments, baseline 1" in result.stderr


def test_given_staged_files_when_the_hook_runs_then_only_they_are_checked(repo):
    write(repo, "clean.py", "x = 1\n")
    write(repo, "other.py", "y = 2  # new\n")

    assert ratchet(repo, "clean.py").returncode == 0
    staged = ratchet(repo, "clean.py", "other.py")
    assert staged.returncode == 1
    assert "other.py:1: new" in staged.stderr


def test_given_a_deleted_baselined_comment_when_hook_runs_then_fails_until_update(repo):
    write(repo, "app.py", "x = 1  # one\ny = 2  # two\n")
    ratchet(repo, "--init")
    write(repo, "app.py", "x = 1\ny = 2  # two\n")

    stale = ratchet(repo)
    assert stale.returncode == 1
    assert "--update" in stale.stderr
    assert ratchet(repo, "app.py").returncode == 1

    assert ratchet(repo, "--update").returncode == 0
    assert baseline(repo) == {"app.py": 1}
    assert ratchet(repo).returncode == 0


def test_given_every_comment_deleted_when_update_runs_then_entry_leaves(repo):
    write(repo, "app.py", "x = 1  # one\n")
    ratchet(repo, "--init")
    write(repo, "app.py", "x = 1\n")

    assert ratchet(repo, "--update").returncode == 0
    assert baseline(repo) == {}


def test_given_a_file_over_baseline_when_update_runs_then_refuses(repo):
    write(repo, "app.py", "x = 1  # one\n")
    ratchet(repo, "--init")
    write(repo, "app.py", "x = 1  # one\ny = 2  # two\n")

    result = ratchet(repo, "--update")

    assert result.returncode == 1
    assert "refusing to raise" in result.stderr
    assert baseline(repo) == {"app.py": 1}


def test_given_a_filled_baseline_when_init_runs_then_it_refuses(repo):
    write(repo, "app.py", "x = 1  # one\n")
    ratchet(repo, "--init")
    write(repo, "app.py", "x = 1  # one\ny = 2  # two\n")

    assert ratchet(repo, "--init").returncode == 1
    assert baseline(repo) == {"app.py": 1}


def test_given_a_file_the_lexer_cannot_read_when_the_hook_runs_then_it_fails(repo):
    write(repo, "broken.py", 'x = """never closed\n')

    result = ratchet(repo)

    assert result.returncode == 1
    assert "broken.py: cannot be lexed" in result.stderr


def test_given_a_bd_store_file_when_the_hook_runs_then_it_is_not_checked(repo):
    write(repo, ".beads/config.yaml", "# written by bd\nkey: value\n")

    assert ratchet(repo).returncode == 0


def test_given_a_path_in_the_skip_file_when_the_hook_runs_then_only_others_count(repo):
    write(repo, "comment-skip.txt", ".githooks/reference-transaction\nvendor/\n")
    write(repo, ".githooks/reference-transaction", "#!/bin/sh\n# installed\n")
    write(repo, "vendor/lib.py", "x = 1  # vendored\n")
    write(repo, ".githooks/pre-push-local", "#!/bin/sh\n# repo-owned\n")

    listed = ratchet(repo, ".githooks/reference-transaction", "vendor/lib.py")
    every_file = ratchet(repo)

    assert listed.returncode == 0, listed.stderr
    assert every_file.returncode == 1
    assert ".githooks/pre-push-local:2: repo-owned" in every_file.stderr
    assert "reference-transaction" not in every_file.stderr
    assert "vendor/lib.py" not in every_file.stderr


@pytest.mark.usefixtures("isolated_home")
def test_given_a_path_in_the_local_skip_file_when_the_hook_runs_then_it_is_skipped(
    repo,
):
    write(repo, "comment-skip.txt", ".githooks/reference-transaction\n")
    write(repo, "comment-skip.local.txt", "vendor/bundle.js\nthird_party/\n")
    write(repo, ".githooks/reference-transaction", "#!/bin/sh\n# installed\n")
    write(repo, "vendor/bundle.js", "// Do not edit\nexport const x = 1;\n")
    write(repo, "third_party/lib.py", "x = 1  # vendored\n")
    write(repo, "src/app.py", "x = 1  # repo-owned\n")

    listed = ratchet(repo, "vendor/bundle.js", "third_party/lib.py")
    every_file = ratchet(repo)

    assert listed.returncode == 0, listed.stderr
    assert every_file.returncode == 1
    assert "src/app.py:1: repo-owned" in every_file.stderr
    assert "vendor/bundle.js" not in every_file.stderr
    assert "third_party/lib.py" not in every_file.stderr
    assert "reference-transaction" not in every_file.stderr


@pytest.mark.usefixtures("isolated_home")
def test_given_a_baselined_path_newly_skipped_locally_when_updated_then_dropped(
    repo,
):
    write(repo, "vendor/bundle.js", "// Do not edit\nexport const x = 1;\n")
    assert ratchet(repo, "--init").returncode == 0
    assert baseline(repo) == {"vendor/bundle.js": 1}
    write(repo, "comment-skip.local.txt", "vendor/bundle.js\n")
    refreshed = "// Do not edit\n// refreshed\nexport const x = 1;\n"
    write(repo, "vendor/bundle.js", refreshed)

    result = ratchet(repo)

    assert result.returncode == 0, result.stderr
    assert ratchet(repo, "--update").returncode == 0
    assert baseline(repo) == {}


def test_given_a_baselined_path_newly_skipped_when_hook_runs_then_update_drops_it(
    repo,
):
    write(repo, ".githooks/reference-transaction", "#!/bin/sh\n# installed\n")
    assert ratchet(repo, "--init").returncode == 0
    write(repo, "comment-skip.txt", ".githooks/reference-transaction\n")
    write(repo, ".githooks/reference-transaction", "#!/bin/sh\n")

    result = ratchet(repo)

    assert result.returncode == 0, result.stderr
    assert ratchet(repo, "--update").returncode == 0
    assert baseline(repo) == {}


DIRECTIVES = {
    "python.py": (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "import os  # noqa: F401\n"
        "x = []  # type: list[int]\n"
        "y = os.sep  # type: ignore[attr-defined]\n"
        "if x:  # pragma: no cover\n"
        "    pass\n"
        "# fmt: off\n"
        "z = [1,2]  # fmt: skip\n"
        "# fmt: on\n"
        "# pylint: disable=invalid-name\n"
        "# ruff: noqa: E501\n"
    ),
    "shell.sh": (
        "#!/usr/bin/env bash\n"
        "# shellcheck disable=SC2034\n"
        "# shellcheck source=/dev/null\n"
        "x=1\n"
    ),
    "web.ts": (
        "#!/usr/bin/env node\n"
        "/// <reference types=\"node\" />\n"
        "/* eslint-disable no-console */\n"
        "// eslint-disable-next-line no-undef\n"
        "// @ts-expect-error reason\n"
        "// @ts-ignore\n"
        "// prettier-ignore\n"
        "/* istanbul ignore next */\n"
        "// c8 ignore next\n"
        "// biome-ignore lint: reason\n"
        "/*! license header */\n"
        "/** @license MIT */\n"
        "/**\n"
        " * A JSDoc block, out of scope for this pass.\n"
        " */\n"
        "export const x = 1;\n"
    ),
    "config.yaml": (
        "# yaml-language-server: $schema=https://example.invalid/schema.json\n"
        "# yamllint disable rule:line-length\n"
        "key: value\n"
    ),
    "pyproject.toml": (
        "#:schema https://example.invalid/schema.json\n"
        "[tool]\n"
    ),
}


@pytest.mark.parametrize("path", sorted(DIRECTIVES))
def test_given_only_directives_when_the_hook_runs_then_it_passes(repo, path):
    write(repo, path, DIRECTIVES[path])

    result = ratchet(repo)

    assert result.returncode == 0, result.stderr
    assert total(repo, path) == 0


def test_given_a_repo_marker_listed_when_the_hook_runs_then_marked_comments_pass(repo):
    write(repo, "hook.sh", "x=1\n# focus-ok: the user asked for the window\n")
    assert ratchet(repo).returncode == 1

    write(repo, "comment-directives.txt", "focus-ok:\n")

    assert ratchet(repo).returncode == 0


DECOYS = {
    "python.py": (
        "s = 'a # not a comment'\n"
        't = """\n'
        "# not a comment either\n"
        '"""\n'
        "def f():\n"
        '    """Docstring # with a hash."""\n'
        "    return s  # real 1\n"
        "# real 2\n"
    ),
    "shell.sh": (
        "#!/bin/sh\n"
        "a='# not a comment'\n"
        'b="# nor this $(echo "# nested") ${#a}"\n'
        "n=${#a} m=$((1 << 2)) p=${a#x}\n"
        "c=x#y\n"
        "cat <<EOF\n"
        "# heredoc body\n"
        "EOF\n"
        "cat <<-'QUOTED'\n"
        "\t# quoted heredoc body\n"
        "\tQUOTED\n"
        "out=$(cat <<'INNER'\n"
        "# heredoc inside a command substitution\n"
        "INNER\n"
        ")\n"
        "tr a b <<<\"# here-string $a\"\n"
        "echo done  # real 1\n"
        "  # real 2\n"
    ),
    "web.js": (
        "const a = '// not a comment';\n"
        'const b = "/* nor this */";\n'
        "const c = `// template ${a + '//'} /* literal */ ${`nested // ${b}`}`;\n"
        "const r = /\\/\\/[/*]/g;\n"
        "const d = 4 / 2 / 1;\n"
        "// real 1\n"
        "const e = 1; /* real 2 */\n"
        "/* real 3\n"
        "   spanning lines */\n"
    ),
    "view.tsx": (
        "const f = <T,>(x: T) => x;\n"
        "const g = (n: Array<string>) => n.length < 2;\n"
        "export const V = () => (\n"
        '  <div title="// not a comment" data-x={a // real 1\n'
        "  }>\n"
        "    It's http://example.invalid text {/* real 2 */}\n"
        "    <br />\n"
        "    <Menu<Option>\n"
        "      // real 3, the card's own attribute comment\n"
        "      items={x}\n"
        "    />\n"
        "  </div>\n"
        ");\n"
        "// real 4\n"
    ),
    "ci.yml": (
        "name: 'quoted # not a comment'\n"
        'other: "double # not a comment"\n'
        "plain: don't # real 1\n"
        "url: http://example.invalid/#anchor\n"
        "steps:\n"
        "  - run: |\n"
        "      # shell comment inside a block scalar\n"
        "      echo hi\n"
        "  - run: >-\n"
        "      # folded block scalar\n"
        "# real 2\n"
    ),
    "pyproject.toml": (
        'a = "# not a comment"\n'
        "b = '# literal'\n"
        'c = """\n'
        "# multi-line basic\n"
        '"""\n'
        "d = '''\n"
        "# multi-line literal\n"
        "'''\n"
        "e = 1  # real 1\n"
        "# real 2\n"
    ),
    ".gitignore": (
        "# real 1\n"
        "\\#not-a-comment\n"
        "*.log\n"
        "  # a pattern, not a comment\n"
    ),
}
EXPECTED = {
    "python.py": 2,
    "shell.sh": 2,
    "web.js": 3,
    "view.tsx": 4,
    "ci.yml": 2,
    "pyproject.toml": 2,
    ".gitignore": 1,
}


@pytest.mark.parametrize("path", sorted(DECOYS))
def test_given_hashes_in_strings_when_listed_then_only_comments_count(
    repo, path
):
    write(repo, path, DECOYS[path])

    listed = ratchet(repo, "--list", path)

    assert listed.returncode == 0, listed.stderr
    lines = listed.stdout.splitlines()
    found = [line for line in lines if not line.startswith("total:")]
    assert len(found) == EXPECTED[path], listed.stdout
    assert all("real" in line for line in found), listed.stdout


def test_given_a_jinja_template_when_listed_then_lexed_by_inner_extension(repo):
    write(repo, "ci.yml.jinja", "run: |\n  # block scalar\nkey: {{ value }}  # real\n")
    write(repo, ".gitignore.jinja", "# real\n*.log\n")
    write(repo, "notes.txt", "# not a covered format\n")

    assert total(repo, "ci.yml.jinja") == 1
    assert total(repo, ".gitignore.jinja") == 1
    assert total(repo, "notes.txt") == 0


def test_given_an_extensionless_script_when_listed_then_shebang_picks_lexer(repo):
    write(repo, "bin/tool", "#!/usr/bin/env bash\na='#'\n# real\n")

    assert total(repo, "bin/tool") == 1


def test_given_own_line_and_trailing_comments_when_listed_then_own_lines_shed(repo):
    write(repo, "app.py", "# own line\nx = 1  # trailing\n")
    write(repo, "app.js", "/* two\n   lines */\nconst y = 1; // trailing\n")

    listed = ratchet(repo, "--list")

    assert listed.stdout.strip().splitlines()[-1] == (
        "total: 4 comments in 2 files; 3 comment-only lines"
    )


def test_given_this_repository_when_its_ratchet_runs_then_it_passes():
    root = Path(__file__).resolve().parent.parent

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
