# uv Disk Usage Troubleshooting

A reference for understanding where uv stores data and how to safely reclaim disk space.

## Directory Structure

uv uses **three separate directories** for different purposes:

1. **Cache** (`uv cache dir`) — Downloaded packages, wheels, and build artifacts
2. **Tools** (`uv tool dir`) — Installed CLI tools (e.g. `ai`, `copier`, `pre-commit`, `ruff`)
3. **Python interpreters** (`~/.local/share/uv/python/`) — CPython versions managed by uv

These directories serve different purposes and have different consequences if deleted.

## Locating the uv Cache

**Never assume the cache location from environment variables alone.** The cache can be relocated via either:

- Environment variable: `UV_CACHE_DIR`
- Config file: `~/.config/uv/uv.toml` with `cache-dir = "/path/to/cache"`

**The only reliable way to locate the cache:**

```bash
uv cache dir
```

This command resolves the cache location using uv's own resolution logic (env var > config file > platform default).

### Why Grepping for `UV_CACHE_DIR` is Insufficient

Non-interactive shells and hook subprocesses **do not source `~/.bashrc`**, so an env-var-only relocation leaves those contexts still writing to the default location. A config file (`~/.config/uv/uv.toml`) applies universally.

**Consequence:** `UV_CACHE_DIR` absent from your environment does **not** mean the cache was never relocated. Always verify with `uv cache dir`.

## What Lives Where

### Cache Directory (`uv cache dir`)

- Downloaded source distributions and wheels
- Built wheels from source
- Git clones of VCS dependencies
- HTTP response cache

**Safe to delete?** Yes. uv will re-download and rebuild as needed. This is the **primary disk reclamation target**.

**Prune the cache:**

```bash
uv cache prune          # Remove unused entries
uv cache clean          # Remove everything (will re-download on next install)
```

### Tools Directory (`uv tool dir`)

Default: `~/.local/share/uv/tools/` (Unix) or `%LOCALAPPDATA%\uv\tools` (Windows)

Contains **installed CLI tools** — each tool gets its own isolated venv with its dependencies. For example:

- `~/.local/share/uv/tools/ai-cli-utils/`
- `~/.local/share/uv/tools/copier/`
- `~/.local/share/uv/tools/pre-commit/`
- `~/.local/share/uv/tools/ruff/`

The zero-byte shims in `~/.local/bin` (e.g. `ai`, `copier`, `ruff`) are symlinks or wrappers pointing into these tool directories.

**Safe to delete?** **NO.** Deleting or relocating `~/.local/share/uv/tools` **uninstalls all your CLI tools**. Every tool installed via `uv tool install` will stop working.

To reinstall a tool after accidental deletion:

```bash
uv tool install <package-name>
```

### Python Interpreters (`~/.local/share/uv/python/`)

Contains CPython interpreters downloaded by uv (e.g. `cpython-3.11.8-linux-x86_64-gnu/`).

**Safe to delete?** Partially. uv will re-download interpreters on demand, but any project using one will break until you re-run `uv sync` or `uv venv`.

## Disk Reclamation Checklist

If running low on disk space:

1. **Identify large directories:**
   ```bash
   du -sh ~/.local/share/uv/*
   du -sh $(uv cache dir)
   ```

2. **Prune the cache** (safe, reversible):
   ```bash
   uv cache prune
   ```

3. **Check for old Python interpreters** (if not actively used):
   ```bash
   ls -lh ~/.local/share/uv/python/
   # Remove unused versions manually if needed
   ```

4. **Do NOT delete** `~/.local/share/uv/tools` or `uv tool dir` — that uninstalls your CLI tools.

## Common Misconceptions

### ❌ "~/.local/share/uv is the cache"

**Wrong.** `~/.local/share/uv` is a **parent directory** containing both tools and Python interpreters. The cache may live there (`~/.local/share/uv/cache`), or it may be relocated elsewhere entirely.

### ❌ "If `UV_CACHE_DIR` is unset, the cache was never relocated"

**Wrong.** The cache can be relocated via `~/.config/uv/uv.toml`, which applies to all contexts (interactive shells, hooks, agents, cron jobs). Check `uv cache dir`, not the environment.

### ❌ "Deleting ~/.local/share/uv frees up cache space"

**Partially correct, but destructive.** That directory may contain the cache, but it **definitely** contains your installed tools. You'll lose every CLI tool installed via `uv tool install` — `copier`, `pre-commit`, `ruff`, and any application-specific tools.

Use `uv cache clean` or `uv cache prune` instead.

## See Also

- [BUG-008: uv hardlink fallback warning](../bugs/uv-hardlink-fallback-warning.md) — explains the cache vs. tools filesystem split and how `ai update` detects it
