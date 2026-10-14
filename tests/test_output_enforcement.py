"""Every terminal write in ai_cli goes through ai_cli.output, enforced by an AST scan.

A line without a log type tag must not be possible to add. These tests read the
package source and fail on any way of reaching the terminal that bypasses
``ai_cli.output``: ``print``, ``click.echo``/``secho``/``confirm``/``prompt``, a
``sys.stdout``/``sys.stderr`` write, ``os.write`` to fd 1 or 2, a prompting
``input()``, a ``logging.StreamHandler``, or a child process left to inherit the
terminal. The allowlists below are the only exceptions, each with its reason.

Styling has the same shape (AI-CLI-yxt0): every element is styled by the one
renderer in ``ai_cli.output``, so ``click.style``, a Rich import or an ANSI escape
literal anywhere else is a line that bypasses it. Rich live displays (``Live``,
``Progress``, ``Status``) are allowed, but only inside the one owner module that
tears them down before anything else can own the terminal
(docs/research/rich-live-displays-in-wrapped-sessions.md, section 5).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "ai_cli"

FILE_ALLOWLIST = {
    "output.py": "the interface itself: the one module that writes to the terminal",
    "launch_logging.py": "wraps sys.stderr to mirror already-rendered lines into the launch log; originates none",
}

# (file, enclosing top-level function) -> why the child must own the terminal.
INHERITED_TERMINAL_ALLOWLIST = {
    (
        "process_manager.py",
        "_start_stale_session_reaper_for_launch",
    ): "the asynchronous child is this CLI's tagged session-reaper lifecycle command",
    ("transport.py", "run_ssh_with_reconnect"): "interactive ssh session: the remote shell owns the terminal",
    ("transport.py", "_run_transport_loop"): "interactive mosh/ssh session: the remote shell owns the terminal",
    ("native_deps.py", "_authenticate_root"): "sudo -v password prompt must reach the terminal, or the install hangs",
}

# Rich may be imported only by the renderer and by the live-display owner.
RICH_IMPORTERS = {
    "output.py": "the one renderer: Rich renders each record's elements",
    "live_display.py": "the live-display owner: the only module that may run a Rich Live, Progress or Status",
}
LIVE_DISPLAY_OWNER = "live_display.py"
LIVE_DISPLAY_MODULES = {"rich.live", "rich.progress", "rich.status"}

# (file, top-level function or assigned name) -> why it may carry an ANSI escape literal.
# A None name covers the whole file.
ESCAPE_LITERAL_ALLOWLIST = {
    ("transport.py", "_TERMINAL_RESTORE"): "terminal-control resets after a remote program, not a styled line",
    ("session_script.py", "_AGENT_TERMINAL_RESET"): "the pane's input-mode reset between agents, not a styled line",
    ("quota.py", None): "statusline segments Claude Code renders verbatim; the statusline path must not import Rich",
}
_ESCAPE_LITERAL = re.compile(r"\x1b\[|\\(?:033|x1b|e|u001b)\[")

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
            if file not in RICH_IMPORTERS and (
                (module or "").split(".")[0] == "rich" or any(name.split(".")[0] == "rich" for name in names)
            ):
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


def _named_owners(tree: ast.Module) -> dict[int, str]:
    """Each node's top-level function, or the name a top-level assignment binds."""
    owner = _enclosing_functions(tree)
    for top in tree.body:
        if isinstance(top, (ast.Assign, ast.AnnAssign)):
            targets = top.targets if isinstance(top, ast.Assign) else [top.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            for node in ast.walk(top):
                owner[id(node)] = names[0] if names else ""
    return owner


def scan_styling(source: str, file: str) -> list[Violation]:
    """Every way ``source`` styles text itself instead of through the renderer."""
    tree = ast.parse(source)
    owner = _named_owners(tree)
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _dotted(node) in {"click.style", "click.secho", "click.unstyle"}:
            found.append(Violation(file, node.lineno, f"{_dotted(node)} styles outside ai_cli.output"))
        elif isinstance(node, ast.ImportFrom) and node.module == "click":
            found.extend(
                Violation(file, node.lineno, f"imports click.{alias.name}, which styles outside ai_cli.output")
                for alias in node.names
                if alias.name in {"style", "secho", "unstyle"}
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _ESCAPE_LITERAL.search(node.value):
            name = owner.get(id(node), "")
            if (file, None) not in ESCAPE_LITERAL_ALLOWLIST and (file, name) not in ESCAPE_LITERAL_ALLOWLIST:
                found.append(Violation(file, node.lineno, "an ANSI escape literal styles outside ai_cli.output"))
    return found


def test_given_the_package_source_when_scanned_then_no_styling_bypasses_the_renderer():
    violations = [
        violation
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.name not in RICH_IMPORTERS
        for violation in scan_styling(path.read_text(encoding="utf-8"), path.name)
    ]
    assert not violations, "styling outside the renderer:\n" + "\n".join(map(str, violations))
    for file, name in ESCAPE_LITERAL_ALLOWLIST:
        tree = ast.parse((PACKAGE / file).read_text(encoding="utf-8"))
        assert name is None or name in set(_named_owners(tree).values()), (file, name)


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('import click\ndef report():\n    return click.style("[transport]", fg="cyan")', "click.style"),
        ("from click import style", "click.style"),
        ('LINE = "\\x1b[1;34m[transport]\\x1b[0m"', "ANSI escape literal"),
        ("RESET = '\\033[0m'", "ANSI escape literal"),
        ("from rich.text import Text", "rich"),
    ],
)
def test_given_a_styling_bypass_when_scanned_then_it_is_reported(snippet, expected):
    """Mutation control: a producer that styles its own text is caught, so the clean result means something."""
    violations = scan_styling(snippet, "transport.py") + scan_source(snippet, "transport.py")
    assert any(expected in v.what for v in violations), violations


def scan_live_displays(source: str, file: str) -> list[Violation]:
    """Every Rich live display ``source`` starts outside the owner module."""
    if file == LIVE_DISPLAY_OWNER:
        return []
    tree = ast.parse(source)
    found: list[Violation] = []
    imports_rich = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        imports_rich = imports_rich or any(module.split(".")[0] == "rich" for module in modules)
        if any(module in LIVE_DISPLAY_MODULES for module in modules):
            found.append(Violation(file, node.lineno, f"a Rich live display outside {LIVE_DISPLAY_OWNER}"))
    if imports_rich:
        # Console.status starts a Live; with Rich importable only here, any .status() call is suspect.
        found.extend(
            Violation(file, node.lineno, f"Console.status starts a live display outside {LIVE_DISPLAY_OWNER}")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "status"
        )
    return found


def test_given_the_package_source_when_scanned_then_no_rich_live_display_is_used():
    violations = [
        violation
        for path in sorted(PACKAGE.rglob("*.py"))
        for violation in scan_live_displays(path.read_text(encoding="utf-8"), path.name)
    ]
    assert not violations, "live display outside its owner:\n" + "\n".join(map(str, violations))
    assert (PACKAGE / LIVE_DISPLAY_OWNER).is_file()


@pytest.mark.parametrize(
    "snippet",
    [
        "from rich.live import Live",
        "import rich.progress",
        "from rich import status",
        "from rich.progress import Progress, SpinnerColumn",
        'from rich.console import Console\nwith Console().status("working"):\n    pass',
    ],
)
def test_given_a_live_display_outside_its_owner_when_scanned_then_it_is_reported(snippet):
    """Mutation control: each way of starting a live display is caught outside the owner, and only there."""
    assert scan_live_displays(snippet, "output.py")
    assert scan_live_displays(snippet, LIVE_DISPLAY_OWNER) == []


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


_AI_EMIT_BODY = re.compile(r"\n(\s*)_ai_emit\(\) \{\{\n.*?\n\1\}\}\n", re.DOTALL)
_AI_EMIT_CALL = re.compile(r"(?<![\w-])_ai_emit (\w+) ")


def test_given_the_session_script_source_when_scanned_then_every_message_uses_ai_emit():
    source = (PACKAGE / "session_script.py").read_text(encoding="utf-8")
    assert _AI_EMIT_BODY.search(source), "the _ai_emit shell function is missing"
    outside = _AI_EMIT_BODY.sub("\n", source)
    levels = _AI_EMIT_CALL.findall(outside)
    assert len(levels) >= 10, "the scan matched too few messages to prove anything"
    assert set(levels) <= {"info", "warning", "error"}, levels
    assert [m for m in _script_messages(outside) if m] == []


def test_given_a_bare_echo_when_the_session_script_is_scanned_then_it_is_reported():
    """Mutation control: a message printed outside _ai_emit is caught, so the clean result means something."""
    script = '    _ai_emit() {{\n      printf \'%s\\n\' "[session] $*" >&2\n    }}\n    echo "[session] bypass"\n'
    assert _script_messages(_AI_EMIT_BODY.sub("\n", "\n" + script)) == ["[session] bypass"]


def test_given_an_untagged_echo_when_the_session_script_is_scanned_then_it_is_reported():
    script = 'echo "[session] fine"\nprintf \'%s\\n\' "Session ended." >&2\necho "x" > "$file"\n'
    assert _untagged(_script_messages(script)) == ["Session ended."]


# The one function allowed to exec (AI-CLI-8h3u): it closes any open line and
# flushes both streams first, because an exec skips atexit and stdio buffers.
EXEC_HELPER = ("main.py", "_exec_handoff")
_OS_EXEC = re.compile(r"^(?:os\.)?exec(?:l|le|lp|lpe|v|ve|vp|vpe)$")


def scan_execs(source: str, file: str) -> list[Violation]:
    """Every ``os.exec*`` call or import in ``source`` outside the flushing helper."""
    tree = ast.parse(source)
    owner = _enclosing_functions(tree)
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            found.extend(
                Violation(file, node.lineno, f"imports os.{alias.name}; exec through main._exec_handoff")
                for alias in node.names
                if _OS_EXEC.match(alias.name)
            )
        elif isinstance(node, ast.Call):
            func = _dotted(node.func)
            if func.startswith("os.") and _OS_EXEC.match(func) and (file, owner.get(id(node), "")) != EXEC_HELPER:
                found.append(Violation(file, node.lineno, f"{func} outside main._exec_handoff"))
    return found


def test_given_the_package_source_when_scanned_then_every_exec_goes_through_the_flushing_helper():
    violations = [
        violation
        for path in sorted(PACKAGE.rglob("*.py"))
        for violation in scan_execs(path.read_text(encoding="utf-8"), path.name)
    ]
    helper = ast.parse((PACKAGE / EXEC_HELPER[0]).read_text(encoding="utf-8"))
    body = next(n for n in helper.body if isinstance(n, ast.FunctionDef) and n.name == EXEC_HELPER[1])
    calls = [_dotted(n.func) for n in ast.walk(body) if isinstance(n, ast.Call)]
    assert not violations, "exec outside the flushing helper:\n" + "\n".join(map(str, violations))
    assert calls.index("_out.flush_sinks") < calls.index("os.execvp"), calls


@pytest.mark.parametrize(
    "snippet",
    [
        'import os\ndef launch():\n    os.execvp("tmux", ["tmux"])',
        'import os\nos.execv("/bin/sh", ["sh"])',
        "from os import execvpe",
    ],
)
def test_given_an_exec_outside_the_helper_when_scanned_then_it_is_reported(snippet):
    """Mutation control: a direct exec anywhere else is caught, so the clean result means something."""
    assert scan_execs(snippet, "main.py")
