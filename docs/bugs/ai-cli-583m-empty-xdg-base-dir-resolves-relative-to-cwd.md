---
title: "A set-but-empty base-directory variable resolves state paths relative to the cwd"
status: fixed
kind: bug
issue: AI-CLI-583m
related:
  - AI-CLI-4y6p
---

# A set-but-empty base-directory variable resolves state paths relative to the cwd

## Symptom

A tracked file `ai-cli-utils/remote-ps-cache.json` sat inside this repository's root, committed
by `9579497` ("chore(sync-git): synchronize working tree"). It held a captured 13-process
listing including full command lines and account-specific absolute home paths. This is a public
package, so that content is a standards violation; `AI-CLI-4y6p` tracks the artifact itself.

The artifact was the visible half. The question that mattered was why a cache configured to live
at `$XDG_STATE_HOME/ai-cli-utils/` had been written into a repository working tree at all.

## Causal chain (reproduced, not inferred)

`process_hygiene._get_state_dir()` read the base directory with the **default-argument** form of
`os.environ.get`:

```python
base = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
```

`os.environ.get` returns its default only when the key is **absent**. A key that is present but
empty returns `""`. The `Path.home()` fallback therefore never fires for `XDG_STATE_HOME=""`, and

```python
Path("") / "ai-cli-utils" == Path("ai-cli-utils")
```

is a **relative** path, which resolves against the process cwd. Run the CLI from a repository
checkout and the cache lands in that checkout.

Measured directly with `XDG_STATE_HOME=""`:

| idiom | result | `is_absolute()` |
|---|---|---|
| `os.environ.get(var, default)` | `ai-cli-utils/remote-ps-cache.json` | `False` |
| `os.environ.get(var) or default` | `/home/<user>/.local/state/ai-cli-utils/remote-ps-cache.json` | `True` |

The relative result is byte-for-byte the tracked path. That equality is what makes this the
confirmed cause rather than a plausible one.

The XDG Base Directory specification independently requires the same handling: "If an
implementation encounters a relative path in any of these variables it should consider the path
invalid and ignore it." An empty value is the degenerate relative case, so a bare relative value
such as `XDG_STATE_HOME=foo` is the same defect and was equally unhandled — including by the
`or` idiom, which only rescues the empty case.

## Why it was worth more than an untrack

Three properties made this more than cosmetic:

1. **It aims writes at whatever repository is current.** Nothing about the mechanism is specific
   to this repo; any checkout that happens to be the cwd receives the file.
2. **One site aims a *deletion* at the cwd.** `quota._cc_staging_dir()` feeds
   `reap_cc_update_staging()`, which deletes entries under the resolved path. With a relative
   base that reaper targets `<cwd>/claude/staging` rather than the real cache.
3. **`cc_usage` binds its state dir at import time**, as a module-level constant, so the bad
   value is captured before any caller can intervene.

## Rejected hypotheses

- *A stray `cd` in a script, or a tool run with the wrong cwd.* Rejected: that explains a wrong
  cwd but not a **relative** cache path. The resolver is supposed to produce an absolute path
  regardless of cwd, so cwd alone cannot place the file.
- *`sync-git` copied the file in from elsewhere.* Rejected: `9579497` is the only commit touching
  the path, and the content's timestamp field is consistent with a live local write, not a copy.
- *The `or` idiom used elsewhere was also broken.* Rejected by measurement: it handles the empty
  case correctly. It is nonetheless incomplete for the relative case, which is why the fix
  replaced all sites rather than only the two obviously-defective ones.

## Scope

Defective (default-argument form, returns `""` for a set-but-empty key):

- `src/ai_cli/process_hygiene.py:40` — `XDG_STATE_HOME`
- `src/ai_cli/cc_usage.py:33` — `XDG_STATE_HOME`, bound at import

Partially correct (`or` form: empty handled, relative not):

- `src/ai_cli/config.py` — `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`,
  `XDG_DATA_HOME`, plus the `APPDATA` / `LOCALAPPDATA` Windows branches
- `src/ai_cli/quota.py:423` — `XDG_CACHE_HOME` (the deletion path above)

## The fix

One shared resolver in `config.py`, `resolve_base_dir(env_var, fallback)`, which ignores a value
that is empty or non-absolute and returns the fallback. It also rejects a **relative fallback**
with `ValueError`, because a caller passing one would reinstate the defect in the single place it
would be invisible.

Every base-directory read now routes through it. The two `import os` statements left unused by
the change were removed.

Deliberately **not** done: `process_hygiene` and `cc_usage` were not switched to
`config.get_xdg_state_home()`, which would have removed more duplication. That getter also runs
`_migrate_xdg_dir()`, a directory rename — and for `cc_usage` it would execute at **import
time**. Adding an import-time filesystem mutation is a behaviour change, not a bug fix, so the
two sites call the shared resolver directly and keep their existing no-migration semantics.

## Regression test

`tests/test_xdg_base_resolution.py`. Confirmed RED before the fix, failing on the real assertion
(`PosixPath('relative-dir/ai-cli-utils').is_absolute()` is `False`) rather than on an import or
a mock.

It works at a real boundary — `monkeypatch.setenv` plus `monkeypatch.chdir` into a directory
outside `$HOME`, so a leaked relative path is observable as a path under that directory instead
of landing somewhere plausible-looking inside the home tree. Coverage:

- each resolver against empty, bare-relative, `./`-relative, `../`-relative and whitespace-only
  values;
- an absolute override is still honoured (guards against over-correcting into ignoring real
  configuration);
- the absent-key case as a positive control, since it was always correct and must stay so;
- `process_hygiene._cache_path()` and a reloaded `cc_usage`, the two sites that actually failed;
- the Windows branches by `sys.platform` simulation;
- a relative *fallback* raising `ValueError`.

Plus a **mechanical guard**: an AST sweep of `src/ai_cli/` that fails if any module reads a
base-directory variable directly again, whitelisting only the resolver's own body. The
behavioural tests alone would not catch a newly added call site. The guard ships with a negative
control (a synthetic offender it must flag) and a positive control (an unrelated `os.environ.get`
it must not), because a scanner that silently matches nothing is indistinguishable from a
passing one.

## Prevention lesson

`os.environ.get(key, default)` and `os.environ.get(key) or default` are not interchangeable, and
the difference only shows up for a set-but-empty variable — an input no one writes deliberately
and which shells produce easily. When the value becomes a filesystem path the failure is silent
and misdirected rather than loud: the program keeps working, just against the wrong directory.

The generalisable rule is to make the invariant explicit at the boundary. A path derived from
the environment should be asserted absolute at the point of resolution, once, rather than trusted
at each of seven call sites.

## Follow-ups filed separately

`process_hygiene` and `cc_usage` consult `XDG_STATE_HOME` unconditionally, with no Windows
`LOCALAPPDATA` branch, unlike the four `config.py` getters. That inconsistency predates this bug
and is orthogonal to it: changing which directory those two use on Windows is a relocation with
a migration question attached, not a cwd-leak fix. Left untouched and recorded here rather than
folded in.
