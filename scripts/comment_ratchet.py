#!/usr/bin/env python3
# fmt: off
"""Comment ratchet: no git-tracked file may gain a code comment.

Each covered file's comment count is compared with ``comment-baseline.json`` at the
repository root, a JSON object mapping a repo-relative path to its count. A file over
its baseline fails. A file under its baseline also fails until the baseline is lowered,
so the count only goes down. A file absent from the baseline is allowed zero comments.

Usage::

    python3 scripts/comment_ratchet.py            check every file
    python3 scripts/comment_ratchet.py FILE ...   check these files only; the pre-commit
                                                  hook passes the staged ones
    python3 scripts/comment_ratchet.py --update   lower the baseline; never raises it
    python3 scripts/comment_ratchet.py --init     record every count, only into an
                                                  empty or missing baseline
    python3 scripts/comment_ratchet.py --list [PATH ...]
                                                  print each counted comment and the
                                                  lines deleting them all would shed

Languages are keyed by file extension, with a trailing ``.jinja`` stripped first:
Python (stdlib ``tokenize``), shell (a lexer that respects quotes, command
substitution and heredocs), JavaScript and TypeScript including JSX (a lexer that
respects strings, template literals and regex literals), YAML (respects quoted and
block scalars), TOML (respects every string form) and ``.gitignore``. An extensionless
file is classified by its shebang. Docstrings are not comment tokens, and a JSDoc
``/** ... */`` block is lexed but not counted. Tool directives (``noqa``,
``shellcheck``, ``eslint-disable`` and the rest in ``DIRECTIVES``) are not counted,
nor is any comment starting with a marker listed one per line in the optional
``comment-directives.txt`` at the repository root. Files under ``.beads/`` are
written by bd and are not checked, nor is any path listed one per line in the
copier-rendered ``comment-skip.txt`` or the repository's own optional
``comment-skip.local.txt`` (a line ending in ``/`` skips a directory). A skipped
path's baseline entry is ignored, and ``--update`` drops it.

A file that cannot be lexed fails the check: an uncounted file is not a passing one.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

try:
    from py_interpreter_bootstrap import ensure_supported_python
except ModuleNotFoundError:

    def ensure_supported_python(minimum=(3, 11)):
        """Refuse a too-old interpreter when the bootstrap sibling is absent."""
        if sys.version_info < minimum:
            sys.exit(
                f"comment_ratchet.py needs Python {minimum[0]}.{minimum[1]} or newer; "
                f"this is {sys.version.split()[0]}"
            )


ensure_supported_python()

BASELINE = "comment-baseline.json"
EXTRA_DIRECTIVES = "comment-directives.txt"
SKIP_FILES = ("comment-skip.txt", "comment-skip.local.txt")
COMMAND = "python3 scripts/comment_ratchet.py"
SKIPPED = (".beads/",)

EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".js": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "js",
    ".mts": "js",
    ".cts": "js",
    ".jsx": "jsx",
    ".tsx": "jsx",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
}
NAMES = {".gitignore": "gitignore", ".envrc": "shell"}
SHEBANGS = (
    (re.compile(r"\b(ba|z|da|k)?sh\b"), "shell"),
    (re.compile(r"\bpython[0-9.]*\b"), "python"),
    (re.compile(r"\b(node|deno|bun)\b"), "js"),
)

DIRECTIVES = {
    "python": re.compile(
        r"\bnoqa\b|^type:|\btype:\s*ignore\b|\bpragma:|^fmt:\s*(on|off|skip)\b"
        r"|\bfmt:\s*skip\b|^pylint:|^ruff:"
    ),
    "shell": re.compile(r"^shellcheck\s"),
    "js": re.compile(
        r"^eslint(-disable(-next-line|-line)?|-enable|-env)?\b|^globals?\s"
        r"|^@ts-(ignore|expect-error|nocheck|check)\b|^prettier-ignore\b"
        r"|^/\s*<(reference|amd-module)\b|^(istanbul|c8|v8)\s+ignore\b|^biome-ignore\b"
        r"|^@(vitest|jest)-environment\b|^!|@license\b|@preserve\b"
    ),
    "yaml": re.compile(
        r"^yaml-language-server:|^yamllint\s+(disable|enable)\b|\bpragma:"
    ),
    "toml": re.compile(r"^:schema\s"),
    "gitignore": re.compile(r"(?!)"),
}
DIRECTIVES["jsx"] = DIRECTIVES["js"]
PY_ENCODING = re.compile(r"coding[:=]\s*[-\w.]+")


class LexError(Exception):
    """A file the lexer cannot read safely."""


@dataclass(frozen=True)
class Comment:
    line: int
    end_line: int
    text: str
    own_line: bool
    jsdoc: bool = False


def language_of(path: str, head: str | None = None) -> str | None:
    name = path.rsplit("/", 1)[-1].removesuffix(".jinja")
    if name in NAMES:
        return NAMES[name]
    dot = name.rfind(".")
    if dot > 0:
        return EXTENSIONS.get(name[dot:])
    if head is not None and head.startswith("#!"):
        for pattern, language in SHEBANGS:
            if pattern.search(head):
                return language
    return None


def _own_line(lines: list[str], line: int, column: int) -> bool:
    return lines[line - 1][:column].strip() == ""


def python_comments(source: str) -> list[Comment]:
    lines = source.splitlines()
    found = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            row, column = token.start
            text = token.string[1:]
            own = _own_line(lines, row, column)
            if row == 1 and text.startswith("!"):
                continue
            if row <= 2 and own and PY_ENCODING.search(text):
                continue
            found.append(Comment(row, row, text.strip(), own))
    except (tokenize.TokenError, SyntaxError) as error:
        raise LexError(str(error)) from error
    return found


class _Scanner:
    def __init__(self, source: str):
        self.src = source
        self.lines = source.splitlines()
        self.i = 0
        self.n = len(source)
        self.line = 1
        self.found: list[Comment] = []

    def peek(self, offset: int = 0) -> str:
        index = self.i + offset
        return self.src[index] if index < self.n else ""

    def startswith(self, text: str) -> bool:
        return self.src.startswith(text, self.i)

    def advance(self, count: int = 1) -> None:
        stop = min(self.i + count, self.n)
        self.line += self.src.count("\n", self.i, stop)
        self.i = self.i + count

    def column(self) -> int:
        return self.i - (self.src.rfind("\n", 0, self.i) + 1)

    def line_end(self) -> int:
        end = self.src.find("\n", self.i)
        return self.n if end == -1 else end

    def skip_shebang(self) -> None:
        if self.startswith("#!"):
            self.i = self.line_end()

    def line_comment(self, marker: int) -> None:
        row, column = self.line, self.column()
        end = self.line_end()
        text = self.src[self.i + marker : end].strip()
        self.found.append(Comment(row, row, text, _own_line(self.lines, row, column)))
        self.i = end


_SHELL_BREAK = set(" \t\n;&|<>()")
_HEREDOC = re.compile(
    r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z_][\w.-]*))"
)
_SHELL_OPEN = (("$((", "arith"), ("$(", "cmd"), ("${", "param"), ("$'", "ansi"))


def shell_comments(source: str) -> list[Comment]:
    s = _Scanner(source)
    s.skip_shebang()
    stack: list[list] = [["top", 0]]
    heredocs: list[tuple[str, bool]] = []
    word_start = True

    def open_substitution() -> bool:
        nonlocal word_start
        for opener, kind in _SHELL_OPEN:
            if s.startswith(opener):
                stack.append([kind, 0])
                s.advance(len(opener))
                word_start = kind == "cmd"
                return True
        return False

    def read_heredocs() -> None:
        while heredocs:
            delimiter, strip_tabs = heredocs.pop(0)
            while s.i < s.n:
                end = s.line_end()
                body = s.src[s.i : end]
                s.advance(end - s.i + 1)
                if (body.lstrip("\t") if strip_tabs else body) == delimiter:
                    break

    while s.i < s.n:
        mode = stack[-1]
        kind = mode[0]
        c = s.peek()
        if kind in ("top", "cmd"):
            if c == "\n":
                s.advance()
                word_start = True
                read_heredocs()
                continue
            if c == "#" and word_start:
                s.line_comment(1)
                continue
            if c == "\\":
                s.advance(2)
                word_start = False
                continue
            if c == "$" and open_substitution():
                continue
            if word_start and s.startswith("(("):
                stack.append(["arith", 0])
                s.advance(2)
                continue
            if s.startswith("<<<"):
                s.advance(3)
                word_start = True
                continue
            if s.startswith("<<"):
                match = _HEREDOC.match(s.src, s.i)
                if match:
                    delimiter = next(g for g in match.groups()[1:] if g is not None)
                    heredocs.append((delimiter, match.group(1) == "-"))
                    s.advance(match.end() - s.i)
                    word_start = False
                    continue
            if c == "'":
                stack.append(["sq", 0])
            elif c == '"':
                stack.append(["dq", 0])
            elif c == "`":
                stack.append(["bt", 0])
            elif kind == "cmd" and c == "(":
                mode[1] += 1
            elif kind == "cmd" and c == ")":
                if mode[1] == 0:
                    stack.pop()
                    s.advance()
                    word_start = False
                    continue
                mode[1] -= 1
            word_start = c in _SHELL_BREAK
            s.advance()
            continue
        if kind == "sq":
            if c == "'":
                stack.pop()
        elif kind == "ansi":
            if c == "\\":
                s.advance()
            elif c == "'":
                stack.pop()
        elif kind in ("dq", "bt", "param"):
            closer = {"dq": '"', "bt": "`", "param": "}"}[kind]
            if c == "\\":
                s.advance()
            elif c == closer:
                stack.pop()
            elif c == "$" and open_substitution():
                continue
            elif kind != "dq" and c == "'":
                stack.append(["sq", 0])
            elif kind == "param" and c == '"':
                stack.append(["dq", 0])
            elif kind == "dq" and c == "`":
                stack.append(["bt", 0])
        elif kind == "arith":
            if c == "(":
                mode[1] += 1
            elif c == ")" and mode[1] == 0 and s.peek(1) == ")":
                stack.pop()
                s.advance(2)
                word_start = False
                continue
            elif c == ")":
                mode[1] -= 1
        s.advance()
    if len(stack) > 1:
        raise LexError(f"unterminated {stack[-1][0]} at end of file")
    return s.found


_JS_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_JS_REGEX_WORDS = {
    "return",
    "typeof",
    "instanceof",
    "in",
    "of",
    "new",
    "delete",
    "void",
    "throw",
    "case",
    "do",
    "else",
    "yield",
    "await",
}
_JS_WORD = re.compile(r"[\w$]+")
_JSX_OPEN = re.compile(r"<(>|[A-Za-z][\w.:-]*(?!\s*(,|extends\b)))")


def js_comments(source: str, jsx: bool = False) -> list[Comment]:
    s = _Scanner(source)
    s.skip_shebang()
    stack: list[list] = [["code", 0]]
    prev = ""
    prev_word = ""

    def expression_position() -> bool:
        return prev == "" or prev in _JS_REGEX_AFTER or prev_word in _JS_REGEX_WORDS

    def block_comment() -> None:
        row, column = s.line, s.column()
        end = s.src.find("*/", s.i + 2)
        if end == -1:
            raise LexError(f"unterminated block comment at line {row}")
        body = s.src[s.i + 2 : end]
        jsdoc = body.startswith("*") and body != "*"
        s.advance(end + 2 - s.i)
        rest = s.src[s.i : s.line_end()]
        own = _own_line(s.lines, row, column) and rest.strip() == ""
        text = " ".join((body[1:] if jsdoc else body).split())
        s.found.append(Comment(row, s.line, text, own, jsdoc))

    def element_closed() -> None:
        nonlocal prev, prev_word
        if stack[-1][0] != "text":
            prev, prev_word = "a", ""

    while s.i < s.n:
        mode = stack[-1]
        kind = mode[0]
        c = s.peek()
        if kind == "code":
            if s.startswith("//"):
                s.line_comment(2)
                continue
            if s.startswith("/*"):
                block_comment()
                continue
            if c in " \t\r\n":
                s.advance()
                continue
            word = _JS_WORD.match(s.src, s.i)
            if word:
                prev, prev_word = "a", word.group()
                s.advance(word.end() - s.i)
                continue
            if c in "'\"":
                stack.append(["str", c])
            elif c == "`":
                stack.append(["tpl"])
            elif c == "/" and expression_position():
                stack.append(["regex", False])
            elif (
                jsx
                and c == "<"
                and expression_position()
                and _JSX_OPEN.match(s.src, s.i)
            ):
                stack.append(["tag", False, 0])
            elif c == "{":
                mode[1] += 1
            elif c == "}" and mode[1] == 0 and len(stack) > 1:
                stack.pop()
                s.advance()
                prev, prev_word = ")", ""
                continue
            elif c == "}":
                mode[1] -= 1
            prev, prev_word = c, ""
            s.advance()
            continue
        if kind == "str":
            if c == "\\":
                s.advance()
            elif c == "\n":
                raise LexError(f"unterminated string at line {s.line}")
            elif c == mode[1]:
                stack.pop()
                prev, prev_word = "a", ""
        elif kind == "tpl":
            if c == "\\":
                s.advance()
            elif c == "`":
                stack.pop()
                prev, prev_word = "a", ""
            elif s.startswith("${"):
                stack.append(["code", 0])
                prev, prev_word = "{", ""
                s.advance(2)
                continue
        elif kind == "regex":
            if c == "\\":
                s.advance()
            elif c == "\n":
                raise LexError(f"unterminated regex at line {s.line}")
            elif c == "[":
                mode[1] = True
            elif c == "]":
                mode[1] = False
            elif c == "/" and not mode[1]:
                stack.pop()
                prev, prev_word = "a", ""
        elif kind == "tag":
            if s.startswith("//"):
                s.line_comment(2)
                continue
            if s.startswith("/*"):
                block_comment()
                continue
            if c in "'\"":
                end = s.src.find(c, s.i + 1)
                if end == -1:
                    raise LexError(f"unterminated JSX attribute at line {s.line}")
                s.advance(end + 1 - s.i)
                continue
            if c == "{":
                stack.append(["code", 0])
                prev, prev_word = "{", ""
            elif c == "<":
                mode[2] += 1
            elif c == ">" and mode[2]:
                mode[2] -= 1
            elif s.startswith("/>"):
                stack.pop()
                s.advance(2)
                element_closed()
                continue
            elif c == ">":
                closing = mode[1]
                stack.pop()
                s.advance()
                if closing:
                    stack.pop()
                    element_closed()
                else:
                    stack.append(["text"])
                continue
        elif kind == "text":
            if c == "{":
                stack.append(["code", 0])
                prev, prev_word = "{", ""
            elif s.startswith("</"):
                stack.append(["tag", True, 0])
                s.advance(2)
                continue
            elif c == "<":
                stack.append(["tag", False, 0])
        s.advance()
    if len(stack) > 1:
        raise LexError(f"unterminated {stack[-1][0]} at end of file")
    return s.found


_YAML_BLOCK = re.compile(r"(?:^\s*|[:\-?]\s+)[|>][-+0-9]*\s*$")


def yaml_comments(source: str) -> list[Comment]:
    found = []
    block_indent: int | None = None
    quote = ""
    for row, raw in enumerate(source.splitlines(), start=1):
        indent = len(raw) - len(raw.lstrip(" "))
        if block_indent is not None:
            if raw.strip() == "" or indent > block_indent:
                continue
            block_indent = None
        code_end = len(raw)
        scalar_start = True
        i = 0
        while i < len(raw):
            c = raw[i]
            if quote:
                if c == "\\" and quote == '"':
                    i += 2
                    continue
                if c == quote and quote == "'" and raw[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                if c == quote:
                    quote = ""
                i += 1
                continue
            if c == "#" and (i == 0 or raw[i - 1] in " \t"):
                code_end = i
                text, before = raw[i + 1 :].strip(), raw[:i].strip()
                found.append(Comment(row, row, text, before == ""))
                break
            if c in "'\"" and scalar_start:
                quote = c
            if c in "[{," or (c in "-?:" and raw[i + 1 : i + 2] in ("", " ", "\t")):
                scalar_start = True
            elif c not in " \t":
                scalar_start = False
            i += 1
        if not quote and _YAML_BLOCK.search(raw[:code_end]):
            block_indent = indent
    if quote:
        raise LexError("unterminated quoted scalar at end of file")
    return found


def toml_comments(source: str) -> list[Comment]:
    s = _Scanner(source)
    while s.i < s.n:
        if s.peek() == "#":
            s.line_comment(1)
            continue
        opener = next((q for q in ('"""', "'''", '"', "'") if s.startswith(q)), None)
        if opener is None:
            s.advance()
            continue
        s.advance(len(opener))
        while not s.startswith(opener):
            if s.i >= s.n or (len(opener) == 1 and s.peek() == "\n"):
                raise LexError(f"unterminated string at line {s.line}")
            s.advance(2 if opener[0] == '"' and s.peek() == "\\" else 1)
        s.advance(len(opener))
        while len(opener) == 3 and s.peek() == opener[0]:
            s.advance()
    return s.found


def gitignore_comments(source: str) -> list[Comment]:
    return [
        Comment(row, row, raw[1:].strip(), True)
        for row, raw in enumerate(source.splitlines(), start=1)
        if raw.startswith("#")
    ]


LEXERS = {
    "python": python_comments,
    "shell": shell_comments,
    "js": js_comments,
    "jsx": lambda source: js_comments(source, jsx=True),
    "yaml": yaml_comments,
    "toml": toml_comments,
    "gitignore": gitignore_comments,
}


def counted(language: str, source: str, markers: tuple[str, ...] = ()) -> list[Comment]:
    directive = DIRECTIVES[language]
    return [
        comment
        for comment in LEXERS[language](source)
        if not comment.jsdoc
        and not directive.search(comment.text)
        and not (markers and comment.text.startswith(markers))
    ]


def repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"comment-ratchet: not inside a git repository: {start}")
    return Path(result.stdout.strip())


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "."],
        capture_output=True,
        check=True,
    )
    return sorted(p for p in result.stdout.decode().split("\0") if p)


def listed_lines(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    return tuple(line.strip() for line in path.read_text().splitlines() if line.strip())


def skipped(root: Path):
    entries = [line for name in SKIP_FILES for line in listed_lines(root / name)]
    directories = SKIPPED + tuple(e for e in entries if e.endswith("/"))
    files = {e for e in entries if not e.endswith("/")}
    return lambda path: path in files or path.startswith(directories)


def scan(root: Path, paths: list[str] | None = None):
    markers = listed_lines(root / EXTRA_DIRECTIVES)
    is_skipped = skipped(root)
    prefixes = [p.rstrip("/") for p in paths or []]
    results: dict[str, list[Comment]] = {}
    errors: dict[str, str] = {}
    for path in tracked_files(root):
        if is_skipped(path):
            continue
        under = any(path == p or path.startswith(p + "/") for p in prefixes)
        if prefixes and not under:
            continue
        full = root / path
        if full.is_symlink() or not full.is_file():
            continue
        language = language_of(path)
        if language is None and "." not in path.rsplit("/", 1)[-1]:
            with full.open("rb") as handle:
                head = handle.readline(200).decode("utf-8", "replace")
            language = language_of(path, head)
        if language is None:
            continue
        try:
            results[path] = counted(language, full.read_text(encoding="utf-8"), markers)
        except (UnicodeDecodeError, LexError) as error:
            errors[path] = str(error)
    return results, errors


def load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not all(
        isinstance(v, int) and v >= 0 for v in data.values()
    ):
        sys.exit(f"comment-ratchet: {path.name} must map each path to a count")
    return data


def write_baseline(path: Path, counts: dict[str, int]) -> None:
    kept = {p: n for p, n in sorted(counts.items()) if n > 0}
    path.write_text(json.dumps(kept, indent=2) + "\n")


def report_errors(errors: dict[str, str]) -> None:
    for path, error in sorted(errors.items()):
        print(f"  {path}: cannot be lexed ({error})", file=sys.stderr)


def list_comments(root: Path, paths: list[str]) -> int:
    results, errors = scan(root, paths)
    total = shed = 0
    for path, comments in sorted(results.items()):
        for comment in comments:
            total += 1
            if comment.own_line:
                shed += comment.end_line - comment.line + 1
            print(f"{path}:{comment.line}: {comment.text}")
    files = sum(1 for comments in results.values() if comments)
    print(f"total: {total} comments in {files} files; {shed} comment-only lines")
    report_errors(errors)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, help="repository root (default: git top)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--update", action="store_true", help="lower the baseline")
    action.add_argument("--init", action="store_true", help="fill an empty baseline")
    action.add_argument("--list", nargs="*", metavar="PATH", help="print comments")
    parser.add_argument("files", nargs="*", help="check only these files (the hook)")
    args = parser.parse_args(argv)

    root = args.root.resolve() if args.root else repo_root(Path.cwd())
    if args.list is not None:
        return list_comments(root, args.list)

    only = set() if args.update or args.init else set(args.files)
    results, errors = scan(root, sorted(only))
    if errors:
        print("comment-ratchet: these files cannot be lexed:", file=sys.stderr)
        report_errors(errors)
        return 1
    counts = {path: len(comments) for path, comments in results.items()}
    baseline_path = root / BASELINE
    is_skipped = skipped(root)
    baseline = {
        p: n for p, n in load_baseline(baseline_path).items() if not is_skipped(p)
    }

    if args.init:
        if baseline:
            print(
                f"comment-ratchet: {BASELINE} is not empty; --init only fills an empty "
                f"one. Lower it with: {COMMAND} --update",
                file=sys.stderr,
            )
            return 1
        write_baseline(baseline_path, counts)
        files = sum(1 for n in counts.values() if n)
        total = sum(counts.values())
        print(f"comment-ratchet: recorded {total} comments in {files} files")
        return 0

    over = {p: n for p, n in counts.items() if n > baseline.get(p, 0)}
    under = {
        p: counts.get(p, 0)
        for p in set(baseline) | set(counts)
        if counts.get(p, 0) < baseline.get(p, 0) and (not only or p in only)
    }

    if args.update:
        if over:
            print(
                "comment-ratchet: refusing to raise the baseline; these files gained "
                "comments:",
                file=sys.stderr,
            )
            for path in sorted(over):
                was = baseline.get(path, 0)
                print(f"  {path}: {over[path]} > {was}", file=sys.stderr)
            return 1
        write_baseline(baseline_path, counts)
        print(f"comment-ratchet: baseline lowered for {len(under)} files")
        return 0

    if not over and not under:
        return 0
    print(
        f"comment-ratchet: {len(over) + len(under)} files out of line with {BASELINE}",
        file=sys.stderr,
    )
    for path in sorted(over):
        print(
            f"  {path}: {over[path]} comments, baseline {baseline.get(path, 0)}. "
            "Delete the new comments (see the delete-comment skill):",
            file=sys.stderr,
        )
        for comment in results[path]:
            print(f"    {path}:{comment.line}: {comment.text}", file=sys.stderr)
    for path in sorted(under):
        print(
            f"  {path}: {under[path]} comments, baseline {baseline.get(path, 0)}. "
            f"Lower the baseline: {COMMAND} --update",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
