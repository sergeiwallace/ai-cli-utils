"""The shipped config template stays complete and in step with the keys the code reads.

``docs/reference/config.toml`` is the commented ``config.toml`` that ``ai`` writes for a new
user (``DEFAULT_CONFIG``). The checks read the source with ``ast``, so a new key read in
``config.py`` without a template line fails here, before any user sees an undocumented key.

What counts as a read: a string-literal ``.get("key")`` or ``["key"]`` chain on the user
config (a name ``config``, ``cfg`` or ``_cfg``, or a ``load_config()`` call), or on a local
name bound to such a chain; a module-level ``*_DEFAULTS`` dict merged with a section
(``NAME | section``); and a literal key passed to a ``config.py`` helper that reads its key
argument from a section.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from ai_cli.config import DEFAULT_CONFIG

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "src" / "ai_cli"
TEMPLATE = REPO / "docs" / "reference" / "config.toml"
ROOTS = {"config", "cfg", "_cfg"}


def _template_keys(text: str) -> set[str]:
    """Every table and key the template names, commented or not, with parent tables."""
    keys: set[str] = set()
    table = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("##"):
            continue
        header = re.fullmatch(r"#?\s*\[([^\]]+)\]\s*", stripped)
        if header:
            table = header.group(1).strip()
            parts = table.split(".")
            keys |= {".".join(parts[:i]) for i in range(1, len(parts) + 1)}
            continue
        body = stripped[1:].strip() if stripped.startswith("#") else stripped
        key = re.match(r'([A-Za-z0-9_\-"]+)\s*=', body)
        if key:
            name = key.group(1).strip('"')
            keys.add(f"{table}.{name}" if table else name)
    return keys


def _chain(node: ast.AST) -> tuple[ast.AST, list[str]]:
    keys: list[str] = []
    while True:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.append(node.args[0].value)
            node = node.func.value
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.append(node.slice.value)
            node = node.value
        else:
            return node, keys[::-1]


def _is_root(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in ROOTS
    return isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "load_config"


def _module_dicts(tree: ast.Module) -> dict[str, dict]:
    """Module-level ``NAME = {...}`` and ``NAME: type = {...}`` literal dicts."""
    found = {}
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if isinstance(target, ast.Name) and isinstance(getattr(node, "value", None), ast.Dict):
            try:
                found[target.id] = ast.literal_eval(node.value)
            except ValueError:
                continue  # built from names, not a literal: no keys to read off
    return found


def _reads(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defaults = {name: value for name, value in _module_dicts(tree).items() if name.endswith("_DEFAULTS")}
    reads: set[str] = set()
    key_params: dict[str, tuple[int, str]] = {}
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        aliases: dict[str, list[str]] = {}
        params = [arg.arg for arg in function.args.args]
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                base, keys = _chain(node.value)
                if keys and (_is_root(base) or (isinstance(base, ast.Name) and base.id in aliases)):
                    prefix = aliases.get(getattr(base, "id", ""), [])
                    aliases[node.targets[0].id] = prefix + keys
        for node in ast.walk(function):
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.BitOr)
                and isinstance(node.left, ast.Name)
                and node.left.id in defaults
                and isinstance(node.right, ast.Name)
                and aliases.get(node.right.id)
            ):
                section = aliases[node.right.id]
                reads |= {".".join([*section, key]) for key in defaults[node.left.id]}
            if isinstance(node, (ast.Call, ast.Subscript)):
                base, keys = _chain(node)
                if keys and isinstance(base, ast.Name) and base.id in aliases:
                    reads.add(".".join(aliases[base.id] + keys))
                elif keys and _is_root(base):
                    reads.add(".".join(keys))
            # `section.get(key, ...)` where `key` is this function's parameter.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in aliases
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in params
            ):
                key_params[function.name] = (params.index(node.args[0].id), ".".join(aliases[node.func.value.id]))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in key_params:
            index, section = key_params[node.func.id]
            if index < len(node.args) and isinstance(node.args[index], ast.Constant):
                reads.add(f"{section}.{node.args[index].value}")
    return reads


def _covered(read: str, template: set[str]) -> bool:
    """A read is documented when it, or a table above it with a placeholder child, is named."""
    parts = read.split(".")
    return any(".".join(parts[:i]) in template for i in range(len(parts), 0, -1)) and (
        read in template or len(parts) > 2 or ".".join(parts[:2]) in template
    )


def test_given_the_shipped_template_when_read_then_it_is_the_config_ai_writes_for_a_new_user():
    assert TEMPLATE.read_text(encoding="utf-8") == DEFAULT_CONFIG


def test_given_every_key_config_py_reads_when_checked_then_the_template_names_it():
    template = _template_keys(DEFAULT_CONFIG)

    missing = sorted(read for read in _reads(SOURCE / "config.py") if not _covered(read, template))

    assert missing == [], f"config.py reads keys the template does not list: {missing}"


def test_given_a_template_table_when_any_module_reads_a_key_in_it_then_the_template_names_that_key():
    template = _template_keys(DEFAULT_CONFIG)
    tables = {key for key in template if "." not in key}

    missing = sorted(
        f"{path.name}: {read}"
        for path in sorted(SOURCE.glob("*.py"))
        for read in _reads(path)
        if read.split(".")[0] in tables and not _covered(read, template)
    )

    assert missing == [], f"keys read in a documented table but missing from the template: {missing}"


def test_given_a_table_config_py_validates_with_defaults_when_checked_then_its_keys_and_the_template_agree():
    """Both ways, for each ``*_DEFAULTS`` table: no default undocumented, no stale template key."""
    tree = ast.parse((SOURCE / "config.py").read_text(encoding="utf-8"))
    template = _template_keys(DEFAULT_CONFIG)
    package_reads = {read for path in SOURCE.glob("*.py") for read in _reads(path)}
    owned = {
        "RESTART_SESSION_DEFAULTS": "restart_session",
        "NAMING_DEFAULTS": "naming",
        "RELAUNCH_DEFAULTS": "relaunch",
        "MESSAGING_DEFAULTS": "messaging",
    }
    declared = {name: set(value) for name, value in _module_dicts(tree).items() if name in owned}
    assert set(declared) == set(owned), f"a *_DEFAULTS dict this test checks is gone: {set(owned) - set(declared)}"

    for name, table in owned.items():
        listed = {key.split(".", 1)[1] for key in template if key.startswith(f"{table}.")}
        undocumented = declared[name] - listed
        unread = {key for key in listed - declared[name] if f"{table}.{key}" not in package_reads}
        assert undocumented == set(), f"{name} keys missing from [{table}]: {sorted(undocumented)}"
        assert unread == set(), f"[{table}] lists keys nothing reads: {sorted(unread)}"


def test_given_the_shipped_retry_policy_when_the_template_documents_it_then_every_value_matches():
    """The template's commented ``[retry]`` lines are the shipped ``data/retry.toml``, uncommented."""
    from ai_cli import retry

    lines = DEFAULT_CONFIG[DEFAULT_CONFIG.index("\n[retry]\n") : DEFAULT_CONFIG.index("\n[session_relaunch]\n")]
    uncommented = "\n".join(
        line[2:] if line.startswith("# ") else line for line in lines.splitlines() if not line.startswith("##")
    )

    assert tomllib.loads(uncommented)["retry"] == retry.shipped_table()


def test_given_a_config_written_from_the_template_when_the_shipped_retry_default_changes_then_a_site_follows_it(
    tmp_path, monkeypatch
):
    """A new user's file is the template, so an active ``[retry]`` value there would pin the old default."""
    from ai_cli import retry

    (tmp_path / "ai-cli-utils").mkdir()
    (tmp_path / "ai-cli-utils" / "config.toml").write_text(DEFAULT_CONFIG, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    shipped = retry.shipped_table()
    changed = {**shipped, "wake_sidecar": {**shipped["wake_sidecar"], "attempts": 7}}
    monkeypatch.setattr(retry, "shipped_table", lambda: changed)

    assert retry.policy("wake_sidecar").attempts == 7
