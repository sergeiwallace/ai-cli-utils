"""Every terminal write in ai_cli goes through ai_cli.output, enforced by an AST scan.

A line without a log type tag must not be possible to add. These tests read the
package source and fail on any way of reaching the terminal that bypasses
``ai_cli.output``: ``print``, ``click.echo``/``secho``/``confirm``/``prompt``, a
``sys.stdout``/``sys.stderr`` write, ``os.write`` to fd 1 or 2, a prompting
``input()``, a ``logging.StreamHandler``, or a child process left to inherit the
terminal. The allowlists below are the only exceptions, each with its reason.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_cli.output import Tag

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "ai_cli"

FILE_ALLOWLIST = {
    "output.py": "the interface itself: the one module that writes to the terminal",
    "launch_logging.py": "wraps sys.stderr to mirror already-rendered lines into the launch log; originates none",
}

# (file, enclosing top-level function) -> why the child must own the terminal.
INHERITED_TERMINAL_ALLOWLIST = {
    ("transport.py", "run_ssh_with_reconnect"): "interactive ssh session: the remote shell owns the terminal",
    ("transport.py", "_run_transport_loop"): "interactive mosh/ssh session: the remote shell owns the terminal",
    ("native_deps.py", "_authenticate_root"): "sudo -v password prompt must reach the terminal, or the install hangs",
}

STD_STREAMS = {"sys.stdout", "sys.stderr", "sys.__stdout__", "sys.__stderr__"}
SAFE_STREAM_ATTRS = {"isatty", "fileno", "flush", "encoding"}
CLICK_WRITERS = {"echo", "secho", "echo_via_pager", "confirm", "prompt", "progressbar", "edit", "pause", "clear"}
SUBPROCESS_SPAWNERS = {"run", "Popen", "call", "check_call"}
SILENT = {"subprocess.PIPE", "subprocess.DEVNULL", "PIPE", "DEVNULL"}
SILENT_STDERR = SILENT | {"subprocess.STDOUT", "STDOUT"}


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    what: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.what}"


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    owner: dict[int, str] = {}
    for top in tree.body:
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(top):
                owner[id(node)] = top.name
    return owner


def _inherits_terminal(call: ast.Call) -> bool:
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    capture = keywords.get("capture_output")
    if isinstance(capture, ast.Constant) and capture.value is True:
        return False
    stdout = keywords.get("stdout")
    stderr = keywords.get("stderr")
    stdout_silent = stdout is not None and ast.unparse(stdout) in SILENT
    stderr_silent = stderr is not None and ast.unparse(stderr) in SILENT_STDERR
    return not (stdout_silent and stderr_silent)


def scan_source(source: str, file: str) -> list[Violation]:
    """Every terminal write in ``source`` that bypasses ``ai_cli.output``."""
    tree = ast.parse(source)
    owner = _enclosing_functions(tree)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    found: list[Violation] = []

    def flag(node: ast.AST, what: str) -> None:
        found.append(Violation(file, getattr(node, "lineno", 0), what))

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "print" and isinstance(node.ctx, ast.Load):
            flag(node, "print (use ai_cli.output.emit with a Tag)")
        elif isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted in {f"click.{name}" for name in CLICK_WRITERS}:
                flag(node, f"{dotted} (use ai_cli.output)")
            elif dotted in STD_STREAMS:
                parent = parents.get(id(node))
                safe = isinstance(parent, ast.Attribute) and parent.attr in SAFE_STREAM_ATTRS
                if not safe:
                    flag(node, f"{dotted} used as a writable stream (use ai_cli.output)")
            elif dotted in {"logging.StreamHandler", "logging.basicConfig"}:
                flag(node, f"{dotted} writes untagged log records to a terminal")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            names = [alias.name for alias in node.names]
            if (module or "").split(".")[0] == "rich" or any(name.split(".")[0] == "rich" for name in names):
                flag(node, "rich bypasses the tagged interface")
            if module == "click" and any(name in CLICK_WRITERS for name in names):
                flag(node, "importing a click writer by name bypasses the tagged interface")
        if not isinstance(node, ast.Call):
            continue
        func = _dotted(node.func)
        if func == "input" and (node.args or node.keywords):
            flag(node, "input() with a prompt (use ai_cli.output.ask)")
        elif (
            func == "os.write" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in (1, 2)
        ):
            flag(node, "os.write to stdout/stderr")
        elif func.endswith(".raw") and func.split(".")[0] in {"_out", "output"}:
            reason = node.args[0] if node.args else None
            if not (isinstance(reason, ast.Attribute) and _dotted(reason.value) == "Untagged"):
                flag(node, "raw() reason must be a literal Untagged member so the exception is reviewable")
        elif (
            func.startswith("subprocess.")
            and func.split(".", 1)[1] in SUBPROCESS_SPAWNERS
            and _inherits_terminal(node)
            and (file, owner.get(id(node), "")) not in INHERITED_TERMINAL_ALLOWLIST
        ):
            flag(node, f"{func} lets the child write to the terminal untagged (use ai_cli.output.run_relayed)")
    return found


def _package_violations() -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in FILE_ALLOWLIST:
            continue
        violations.extend(scan_source(path.read_text(encoding="utf-8"), path.name))
    return violations


def test_given_the_package_source_when_scanned_then_no_terminal_write_bypasses_the_tagged_interface():
    violations = _package_violations()
    assert not violations, "untagged terminal output paths:\n" + "\n".join(map(str, violations))


def test_given_every_allowlist_entry_when_checked_then_it_names_a_real_target():
    """A stale allowlist entry is a hole waiting for code; each must still point at something."""
    for name in FILE_ALLOWLIST:
        assert (PACKAGE / name).is_file(), name
    for file, function in INHERITED_TERMINAL_ALLOWLIST:
        tree = ast.parse((PACKAGE / file).read_text(encoding="utf-8"))
        assert function in {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}, (
            file,
            function,
        )


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('print("hello")', "print"),
        ("out = stdout_fn or print", "print"),
        ('import click\nclick.echo("x")', "click.echo"),
        ("import click\nask = click.confirm", "click.confirm"),
        ('import sys\nsys.stdout.write("x")', "sys.stdout"),
        ("import sys\nstream = sys.stderr", "sys.stderr"),
        ('import os\nos.write(2, b"x")', "os.write"),
        ('input("name? ")', "input()"),
        ("import logging\nlogging.StreamHandler()", "logging.StreamHandler"),
        ("from rich.console import Console", "rich"),
        ('import subprocess\nsubprocess.run(["ls"], check=False)', "subprocess.run"),
        ('import subprocess\nsubprocess.run(["ls"], capture_output=quiet, check=False)', "subprocess.run"),
        ('from . import output as _out\n_out.raw(reason, "x")', "raw() reason"),
    ],
)
def test_given_a_bypassing_write_when_scanned_then_it_is_reported(snippet, expected):
    """Negative arm: each forbidden shape is caught, so the clean package result means something."""
    violations = scan_source(snippet, "probe.py")
    assert any(expected in v.what for v in violations), violations


@pytest.mark.parametrize(
    "snippet",
    [
        "import sys\nsys.stdout.isatty()",
        "import sys\nsys.stderr.flush()",
        'import subprocess\nsubprocess.run(["ls"], capture_output=True, check=False)',
        'import subprocess\nsubprocess.run(["ls"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)',
        'from . import output as _out\nfrom .output import Tag, Untagged\n_out.emit(Tag.LAUNCH, "x")\n'
        '_out.raw(Untagged.JSON, "{}")',
        "answer = input()",
    ],
)
def test_given_a_write_through_the_interface_when_scanned_then_it_passes(snippet):
    assert scan_source(snippet, "probe.py") == []


# The generated session script runs in the tmux pane: its own messages are lines too.
_SHELL_MESSAGE = re.compile(r"""(?<![\w-])(?:echo|printf '%s\\{1,2}n')\s+"([^"$]*)""")


def _script_messages(script: str) -> list[str]:
    messages = []
    for line in script.splitlines():
        if ">" in line.split("#", 1)[0] and not re.search(r">&[23]\s*(?:;|$)", line):
            continue  # written to a file, not the terminal
        stripped = line.strip()
        for match in _SHELL_MESSAGE.finditer(stripped):
            if "$(" in stripped[: match.start()]:
                continue  # a command substitution's value, never shown
            messages.append(match.group(1))
    return messages


def _untagged(messages: list[str]) -> list[str]:
    return [m for m in messages if m and not re.match(r"^\[[a-z0-9_-]+\] ", m)]


def test_given_the_session_script_source_when_scanned_then_every_message_it_prints_is_tagged():
    messages = _script_messages((PACKAGE / "session_script.py").read_text(encoding="utf-8"))
    assert len([m for m in messages if m]) >= 10, "the scan matched too few messages to prove anything"
    assert not _untagged(messages)
    used = {re.match(r"^\[([a-z0-9_-]+)\] ", m).group(1) for m in messages if m}  # type: ignore[union-attr]
    assert used <= {tag.value for tag in Tag}, used - {tag.value for tag in Tag}


def test_given_an_untagged_echo_when_the_session_script_is_scanned_then_it_is_reported():
    script = 'echo "[session] fine"\nprintf \'%s\\n\' "Session ended." >&2\necho "x" > "$file"\n'
    assert _untagged(_script_messages(script)) == ["Session ended."]
