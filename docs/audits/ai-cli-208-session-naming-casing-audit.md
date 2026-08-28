---
title: AI-CLI-208 — new session/worktree/CC-title lowercasing fix — audit
category: audit
tags: [audit, ai-cli-208, session, worktree, casing]
status: converged-with-follow-up
date: 2026-08-10
source: "independent-audit"
template_version: "audit-1.0.0"
---

# AI-CLI-208 — new session/worktree/CC-title lowercasing fix — audit

**Status:** Round 2 complete — converged with one follow-up (AI-CLI-209, pre-existing, out of scope)

**Created:** 2026-08-10

**Auditor:** Independent principal-engineer audit

**Target commit:** `2b3a6b2896b927889efebf0e3fe3b7b3ce3db797`

<!-- doc:region name="scope" kind="replaceable" -->

## Table of Contents

- [What Was Audited](#what-was-audited)
- [Scope](#scope)
- [Methodology](#methodology)
- [Status Summary](#status-summary)
- [Round 1 — Main Audit](#round-1--main-audit)
- [Round 2 — Verification Pass](#round-2--verification-pass-append-only)
  - [R2 Summary](#r2-summary)
  - [R2.1 Round 1 IC/JA/DV verification](#r21-round-1-icjadv-verification)
  - [R2.2 Round 1 F-N verification](#r22-round-1-f-n-verification)
  - [R2.3 AD-N decisions verification](#r23-ad-n-decisions-verification)
  - [R2.4 NEW issues surfaced](#r24-new-issues-surfaced)
  - [R2.5 Verification Matrix](#r25-verification-matrix)
  - [R2 Recommendations](#r2-recommendations)
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
- [Outstanding Issues to Fix](#outstanding-issues-to-fix)
- [Already-Correct Items](#already-correct-items)
- [Anti-Patterns to Watch For](#anti-patterns-to-watch-for)
- [Sign-Off Checklist](#sign-off-checklist)
- [Audit Log](#audit-log)
- [Appendix: Files Read](#appendix-files-read)
- [Appendix: Commands Run](#appendix-commands-run)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 2 Reviewer Prompt (Re-audit)](#round-2-reviewer-prompt-re-audit)

## What Was Audited

Commit `2b3a6b2` changes the session-name builder and two test modules so a raw
uppercase project prefix produces lowercase new session artifacts. The audit
verified that change against the current source tree, the AI-CLI-206 behavior at
`2519721`, the full launch-to-worktree-to-iTerm2/CC consumer chain, and the
acceptance criteria reproduced in the reviewer prompt.

The current `src/` and `tests/` tree is byte-for-byte unchanged from the target
commit (`git diff --exit-code 2b3a6b2..HEAD -- src tests` returned 0). HEAD is the
later audit-scaffold commit `30c86bd`; this is not a stale-source audit.

## Scope

### In scope

- `src/ai_cli/session.py`, `tests/test_session.py`, and
  `tests/test_session_launch_integration.py` at `2b3a6b2`.
- Raw-prefix preservation by `resolve_project_prefix()` and
  `get_project_prefix()`.
- Every new-name path for tmux session IDs, `ai_name`, worktree directories,
  Claude `--name`/`customTitle`, and iTerm2 profile/title setup.
- AI-CLI-206's explicit-slot matching and existing-name casing preservation at
  `2519721`.
- Casing assumptions in `_prefix_from_session_name()`, `resolve_session()`,
  `find_recent_session()`, `main.py`, `iterm2.py`, `session_script.py`, and other
  call sites surfaced by symbol searches.
- Test quality and public-package standards in files encountered during the
  audit.

### Out of scope

- Lowercasing task IDs or other consumers of the raw registered prefix.
- Changing the docstrings or semantics of `resolve_project_prefix()` for
  non-session consumers.
- Implementing source or test fixes; this audit invocation is write-scoped to
  this audit document only.
- A live external tracker read. `bd show AI-CLI-ms2i` was attempted but the local
  embedded database could not acquire its read lock under the sandbox. The scope
  and AC text reproduced in the Round 1 reviewer prompt is therefore the
  authoritative requirement set used here.

<!-- /doc:region name="scope" -->

## Methodology

**Approach:** Round 1 was an independent, ground-truth audit. The canonical audit
template was read first. Every required file was then read in full; referenced
symbols were searched across `src/`; target and predecessor diffs and history
were inspected; all naming consumers were traced; and every finding was
reproduced with a read-only command.

The audit distinguishes **CONFIRMED** findings (all eight below) from
**PLAUSIBLE** hypotheses (none). The normal pytest harness could not run because
its autouse fixture and real-tmux fixture require a writable temporary directory,
which this audit sandbox intentionally denies. The affected production functions
and the AI-CLI-206 test bodies that do not require filesystem fixtures were run
directly with mocked subprocess boundaries; the pytest limitation is not counted
as a product failure or a false pass.

## Status Summary

**Latest round:** Round 2

**Outstanding by severity / verdict (across all rounds):**

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| CRITICAL / P0 | 0 | 0 | 0 |
| MAJOR / P1 | 1 | 0 | 1 (AI-CLI-209) |
| MINOR / P2 | 0 | 0 | 0 |
| Cosmetic / P3 | 1 | 1 | 0 |
| **Total** | **2** | **1** | **1** |

**Ship-readiness verdict:** **READY TO SHIP**, with one finding explicitly
deferred to a tracked follow-up.

Seven Round 1 findings are verified fixed. N-2 (P3, malformed module
docstring) is fixed directly in this pass. N-1 (P1, remote named-session
preview identity diverges from the server-allocated name) is a real,
independently-verified finding — but verified (`git show 2519721^:src/ai_cli/main.py`)
to be a **pre-existing bug that predates AI-CLI-206/208 entirely**: the
remote pre-transport preview never allocated an index for a named session,
with or without casing. AI-CLI-208's scope is casing (worktree/tmux/title
always lowercase); N-1 is an orthogonal index-allocation-across-a-network-
boundary problem whose correct fix (a remote preflight/handshake, per its
own Round 2 recommendation) is a small protocol change, not a casing fix.
Deferred to **AI-CLI-209** (P1, filed and linked as `related` to this
issue) rather than expanding this fix's scope or blocking it indefinitely.
Not a silent drop — it is a new, tracked, P1 bd issue with the audit's full
evidence carried into its description.

The required `uv run` checks could not initialize under the audit sandbox;
independently re-run by the orchestrating session outside that sandbox
after both Round 1 and Round 2 (real tmux, writable temp dir available):
`ruff check` and `ruff format --check` both clean, full `pytest` suite
green (2309 passed after the Round 1 fix commit; one unrelated, known-flaky
timing test — `test_when_session_age_is_seconds_then_displays_s_suffix` in
`tests/test_cli.py`, unrelated to this fix — failed once under full-suite
load and passed cleanly in isolation, consistent with prior flakes of the
same test this session).

<!-- doc:region name="round_1_findings" kind="replaceable" -->

## Round 1 — Main Audit

**Round 1 auditor:** Independent principal-engineer audit

**Round 1 date:** 2026-08-10

**Round 1 scope:** Full IC, JA, DV, and open-scope review of the target commit,
the AI-CLI-206 predecessor behavior, and all naming/title consumers surfaced by
source-wide symbol searches.

### R1 Summary

Eight confirmed findings: 3 P1, 3 P2, and 2 P3. The target correctly lowercases
the registered prefix in the main new-allocation builder and preserves raw prefix
resolution plus AI-CLI-206 explicit-slot reuse. It does not cover two adjacent
launch paths, does not lowercase user-supplied name components, and does not
provide the exact fleet-registry-to-real-worktree regression test required by the
AC. No inline fixes were made because every finding is in read-only source/tests,
not in this audit document.

### R1 Findings

#### Internal Consistency (IC-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | FAIL — CONFIRMED, P2 | `tests/test_session.py:87-96` establishes that a generated full name is valid input and must be stripped, but `src/ai_cli/session.py:589-605` builds lowercase output prefixes while stripping only raw-case prefixes. `c-app-1` round-trips as `c-app-c-app-1-1` when the registry says `APP`. |

#### Spec / AC Compliance (JA-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| JA-1 | FAIL — CONFIRMED, P1 | `src/ai_cli/main.py:1752-1754,1896-1901` passes raw `c-APP-` into `resolve_session()`, whose comparisons are case-sensitive at `src/ai_cli/session.py:405-425,534-546`; a newly created `c-app-1` is not found. |
| JA-2 | FAIL — CONFIRMED, P2 | `src/ai_cli/session.py:593,607-626` preserves ASCII uppercase in a caller-supplied name component. `build_session_name("c", "APP", "Planning")` returns `c-app-Planning-1` / `app-Planning-1`. |
| JA-3 | PARTIAL — CONFIRMED, P2 | `tests/test_session_launch_integration.py:275-311` mocks both prefix resolution and worktree creation. The production fleet fixture at `tests/test_worktree_container_collision.py:214-243` starts from a pre-existing worktree and conditionally skips, so no test meets the complete new-launch AC. |

#### Domain Validity (DV-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| DV-1 | FAIL — CONFIRMED, P1 | `src/ai_cli/main.py:1788-1803` independently constructs `_r_ai_name` from raw `remote_prefix`; `src/ai_cli/iterm2.py:394,424-429` uses it verbatim as profile and OSC 1 title. This is the missed constructor explicitly called out by the AC. |

#### Independent Findings (F-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| F-1 | FAIL — CONFIRMED, P1 | `src/ai_cli/main.py:1792` inserts `remote_prefix` into a shell program without `shlex.quote()`, while `src/ai_cli/config.py:519-531` accepts any non-empty registry string. A semicolon in the prefix becomes remote shell syntax. |
| F-2 | FAIL — CONFIRMED, P3 | `tests/test_config.py:384-390` is named as though the raw prefix is used for worktrees and titles, but asserts only that resolution returns `PROJECT`. The label describes pre-fix behavior and passes without testing its claim. |
| F-3 | FAIL — CONFIRMED, P3 | `tests/test_project.py:81` embeds a personal-name macOS account path, contradicting `AGENTS.md`'s generic-placeholder and OS-portability requirements. |

#### JA-1: `--resume` cannot reattach to the lowercase session created by this fix — `MAJOR` / `P1`

**Status:** CONFIRMED

**Location:** `src/ai_cli/main.py:1752-1754,1896-1901`;
`src/ai_cli/session.py:405-425,534-546`

**Evidence:**

```python
# src/ai_cli/main.py:1752-1754
engine_short = "c" if engine == "c" else "g"
remote_seg = "-r" if is_remote else ""
prefix = f"{engine_short}{remote_seg}-{project_prefix}-"

# src/ai_cli/main.py:1896-1901
if resume and not bare:
    session = _session.resolve_session(prefix, name)
    if not session:
        print(f"No matching session found for '{prefix}{name or '*'}'")
        sys.exit(1)
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])
```

```python
# src/ai_cli/session.py:419-425
if len(parts) >= 2 and parts[0].startswith(prefix):
    with contextlib.suppress(ValueError):
        sessions.append((parts[0], int(parts[1])))
if not sessions:
    return ""
sessions.sort(key=lambda x: x[1], reverse=True)
return sessions[0][0]

# src/ai_cli/session.py:543-546
res = subprocess.run(["tmux", "has-session", "-t", f"{prefix}{name}"], capture_output=True, check=False)
if res.returncode == 0:
    return f"{prefix}{name}"
return find_recent_session(f"{prefix}{name}-")
```

**Why it matters:** A user can create `c-app-1` successfully and then immediately
receive “No matching session found for 'c-APP-1'” from `ai c --resume 1` in the
same registered repository. The explicit-slot AI-CLI-206 path is case-insensitive,
but the separate reattach path is not.

**Verification command:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c 'from unittest.mock import patch; from ai_cli.session import resolve_session; R=type("R",(),{}); a=R(); a.returncode=1; a.stdout=""; b=R(); b.returncode=0; b.stdout="c-app-1 100"+chr(10); p=patch("ai_cli.session.subprocess.run",side_effect=[a,b]); p.start(); print(repr(resolve_session("c-APP-","1"))); p.stop()'
```

**Verification note:** Reproduced on `2b3a6b2`; actual output was `''` with exit
0.

**Recommendation:** Make `resolve_session()` and `find_recent_session()` compare
session names with `casefold()` and return the exact tmux-reported candidate so a
legacy mixed-case name is preserved. For numeric names, reuse
`_matching_tmux_sessions()` rather than constructing an exact raw-case target.
Cover named and unnamed `--resume` with raw `APP` and existing `c-app-*` plus
mixed-case legacy candidates.

#### DV-1: Remote client iTerm2 setup bypasses the canonical builder — `MAJOR` / `P1`

**Status:** CONFIRMED

**Location:** `src/ai_cli/main.py:1788-1805`;
`src/ai_cli/iterm2.py:394,424-429`

**Evidence:**

```python
# src/ai_cli/main.py:1788-1805
remote_prefix = project_prefix
# ...
_r_engine_short = "c" if engine == "c" else "g"
_r_ai_name = f"{_r_engine_short}-r-{remote_prefix}-{name or '1'}"
_iterm2_remote_slot = _iterm2._assign_iterm2_color_slot(_r_ai_name, engine)
_iterm2._emit_iterm2_profile_setup(_r_ai_name, engine, _r_ai_name, slot=_iterm2_remote_slot)

_cleanup_cmd = ["ai", "internal", "cleanup-session-files", _r_ai_name]
```

```python
# src/ai_cli/iterm2.py:394,424-429
session_name = session or ai_name
# ...
profile_name = f"ai-cli:{ai_name}"
# ...
sys.stdout.write(f"\033]1;{session_name}\007")
```

**Why it matters:** A remote launch with registry prefix `APP` emits and leases
`c-r-APP-1` locally even though the remote server constructs `c-r-app-1`. The
comment at `main.py:1797-1799` says this pre-transport emission is the only chance
to set the remote pane profile/title, so no later canonical value corrects it.

**Verification command:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -c 'import tempfile
tempfile.gettempdir=lambda:"/nonexistent"
from unittest.mock import patch
from ai_cli.main import _do_session_launch
cfg={"remote":{"host":"example.com","transport":"ssh"}}
with patch("ai_cli.main.shutil.which",return_value="/usr/bin/tmux"),patch("ai_cli.main._session._resolve_is_remote",return_value=False),patch("ai_cli.main._config.validate_registry_completeness",return_value=True),patch("ai_cli.main._config.get_current_project_name",return_value="myproject"),patch("ai_cli.main._iterm2._assign_iterm2_color_slot",return_value=None),patch("ai_cli.main._iterm2._emit_iterm2_profile_setup") as emit,patch("ai_cli.main.os.execvp",side_effect=SystemExit(0)):
 try:
  _do_session_launch(engine="c",name="",resume=False,once=False,bare=False,notify=False,sandbox=True,no_worktree=False,remote=True,project="",is_remote=False,project_prefix_override="APP",extra_args=[],config=cfg)
 except SystemExit:
  pass
 print(emit.call_args.args[:3])'
```

**Verification note:** Reproduced on `2b3a6b2`; the mocked emitter received
`('c-r-APP-1', 'c', 'c-r-APP-1')`.

**Recommendation:** Extract a pure “new session display name” canonicalizer used
by both `build_session_name()` and this pre-transport preview. Normalize the raw
prefix and requested name component there, while leaving the prefix embedded in
the remote command raw for non-session consumers. Add local-client remote tests
for uppercase registry prefix, uppercase requested name, profile/title emission,
color lease, cleanup name, and transport-loop name.

#### F-1: Raw registry prefixes are interpolated into a remote shell program — `MAJOR` / `P1`

**Status:** CONFIRMED

**Location:** `src/ai_cli/main.py:1792`;
`src/ai_cli/config.py:519-531`

**Evidence:**

```python
# src/ai_cli/main.py:1792
remote_cmd = f'export PATH="$HOME/.local/bin:$PATH"; ai {engine} --is-remote --project-prefix {remote_prefix} --project {shlex.quote(remote_project)}'
```

```python
# src/ai_cli/config.py:519-531
prefix = project.get("task_prefix")
if not isinstance(name, str) or not name.strip() or not isinstance(prefix, str) or not prefix.strip():
    raise ProjectPrefixError(f"Project registry entry in {path} is missing name or task_prefix: {project!r}")
# ...
entries[key] = {"prefix": prefix.strip(), "type": str(project.get("type", "tool"))}
```

**Why it matters:** A version-controlled registry or locally registered prefix
containing `;`, command substitution, or another shell metacharacter becomes
executable syntax on the remote host when a user launches `--remote`. Prefixes
are usually simple identifiers, but the production parser does not enforce that
assumption and the shell boundary supplies no defense.

**Verification command:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -c 'import tempfile
tempfile.gettempdir=lambda:"/nonexistent"
from unittest.mock import patch
from ai_cli.main import _do_session_launch
cfg={"remote":{"host":"example.com","transport":"ssh"}}
with patch("ai_cli.main.shutil.which",return_value="/usr/bin/tmux"),patch("ai_cli.main._session._resolve_is_remote",return_value=False),patch("ai_cli.main._config.validate_registry_completeness",return_value=True),patch("ai_cli.main._config.get_current_project_name",return_value="myproject"),patch("ai_cli.main._iterm2._assign_iterm2_color_slot",return_value=None),patch("ai_cli.main._iterm2._emit_iterm2_profile_setup"),patch("ai_cli.main.os.execvp",side_effect=SystemExit(0)) as execute:
 try:
  _do_session_launch(engine="c",name="",resume=False,once=False,bare=False,notify=False,sandbox=True,no_worktree=False,remote=True,project="",is_remote=False,project_prefix_override="APP; printf INJECTED",extra_args=[],config=cfg)
 except SystemExit:
  pass
 print(execute.call_args.args[1][-1])'
```

**Verification note:** Reproduced on `2b3a6b2`. The captured production command
contained the executable sequence
`--project-prefix APP; printf INJECTED --project myproject` inside the remote
`zsh -l -c` program.

**Recommendation:** Apply `shlex.quote(str(remote_prefix))` at the `remote_cmd`
shell boundary and add a remote-launch regression with spaces and shell
metacharacters that asserts one literal argument reaches the remote `ai` command.
This closes the injection without inventing a narrower prefix grammar.

#### IC-1: The builder cannot consume its own canonical full-name output — `MINOR` / `P2`

**Status:** CONFIRMED

**Location:** `src/ai_cli/session.py:589-605`;
`tests/test_session.py:87-96`

**Evidence:**

```python
# src/ai_cli/session.py:589-605
naming_prefix = project_prefix.lower()
tmux_base = f"{engine_short}{remote_seg}-{naming_prefix}-"
ai_base = f"{naming_prefix}-"

clean_name = name
prefixes_to_strip = [
    f"c-r-{project_prefix}-",
    f"c-{project_prefix}-",
    # ...
]
for p in sorted(prefixes_to_strip, key=len, reverse=True):
    if clean_name.startswith(p):
        clean_name = clean_name[len(p) :]
```

```python
# tests/test_session.py:87-96
def test_build_session_name_with_new_full_name_and_index_when_called_then_strips_all():
    # ...
    session_id, ai_name = build_session_name("c", "sw", "c-sw-1")

    assert session_id == "c-sw-1"
    assert ai_name == "sw-1"
```

**Why it matters:** Full generated names are a documented/tested accepted input,
but after this fix a canonical `c-app-1` no longer matches the raw-case strip
prefix `c-APP-`. Relaunching by the tool's own printed name allocates the unrelated
`c-app-c-app-1-1` namespace.

**Verification command:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c 'from unittest.mock import patch; from ai_cli.session import build_session_name; p=patch("ai_cli.session._matching_tmux_sessions",return_value=[]); p.start(); print(build_session_name("c","APP","c-app-1")); p.stop()'
```

**Verification note:** Reproduced on `2b3a6b2`; actual output was
`('c-app-c-app-1-1', 'app-c-app-1-1')`.

**Recommendation:** Strip accepted full-name prefixes case-insensitively (or
generate strip prefixes from the normalized prefix) before deciding whether the
remainder is an index. Preserve the original candidate only when an existing
session/worktree is returned by the AI-CLI-206 matching helpers. Add a raw `APP`
round-trip test using `c-app-1`, `c-r-app-1`, and `app-1` inputs.

#### JA-2: Uppercase requested names still create uppercase artifacts — `MINOR` / `P2`

**Status:** CONFIRMED

**Location:** `src/ai_cli/session.py:593,607-626`

**Evidence:**

```python
clean_name = name
# ...
clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", clean_name)
clean_name = re.sub(r"-+", "-", clean_name)
clean_name = clean_name.strip("-")
# ...
tmux_named = f"{tmux_base}{clean_name}-"
ai_named = f"{ai_base}{clean_name}-"
idx = find_next_index(tmux_named, use_tmux=use_tmux)
return f"{tmux_named}{idx}", f"{ai_named}{idx}"
```

**Why it matters:** The AC says newly created worktree, tmux, `ai_name`, and title
values are always lowercase, not merely that the registry-derived substring is
lowercase. `ai c Planning` violates that public invariant across every downstream
consumer.

**Verification command:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c 'from unittest.mock import patch; from ai_cli.session import build_session_name; p=patch("ai_cli.session._matching_tmux_sessions",return_value=[]); p.start(); print(build_session_name("c","APP","Planning")); p.stop()'
```

**Verification note:** Reproduced on `2b3a6b2`; actual output was
`('c-app-Planning-1', 'app-Planning-1')`.

**Recommendation:** Lowercase the sanitized `clean_name` only on new allocation.
Keep the early AI-CLI-206 existing-candidate return unchanged so mixed-case legacy
artifacts retain their exact names. Add local, bare, and remote named-launch tests
with uppercase and mixed-case requested names.

#### JA-3: The required fleet-registry new-launch regression test does not exist — `MINOR` / `P2`

**Status:** CONFIRMED

**Location:** `tests/test_session_launch_integration.py:275-311`;
`tests/test_session.py:1504-1517`;
`tests/test_worktree_container_collision.py:214-243`

**Evidence:**

```python
# tests/test_session_launch_integration.py:282-293
with (
    patch("ai_cli.session.cleanup_stale_sessions"),
    patch("ai_cli.session.resolve_project_prefix", return_value="MYPROJECT"),
# ...
    patch("ai_cli.session.create_worktree", return_value=(worktree, True)) as create_worktree,
```

```python
# tests/test_worktree_container_collision.py:231-239
lower = create_worktree("app-1")
assert lower is not None
requested = lower.parent / "APP-1"
if not requested.exists():
    pytest.skip("filesystem does not support case-alias paths")

prefix = resolve_project_prefix(repo)
_session_id, ai_name = build_session_name("c", prefix, "1", use_tmux=False)
assert ai_name == "app-1"
```

**Why it matters:** The integration test proves wiring from a mocked resolver into
a mocked worktree call and real tmux, but not the required production
fleet-registry-to-new-directory chain. The only production fleet test deliberately
creates the worktree before resolution and skips on case-sensitive filesystems,
so the exact regression can return without detection.

**Verification command:**

```bash
sed -n '275,311p' tests/test_session_launch_integration.py
sed -n '214,243p' tests/test_worktree_container_collision.py
sed -n '1504,1517p' tests/test_session.py
```

**Verification note:** Reproduced on `2b3a6b2`. The output contains the quoted
resolver/worktree patches, the pre-creation `create_worktree("app-1")`, and the
conditional `pytest.skip`; no single test registers an uppercase fleet entry,
launches an empty slot, and observes a real new worktree plus tmux/title outputs.

**Recommendation:** Add one end-to-end fixture that writes an uppercase
`[[projects]]` fleet entry, points production discovery at it, starts with no
slot artifacts, invokes `_do_session_launch()`, and asserts the real created
worktree basename plus real tmux session and captured iTerm/CC title arguments.
Do not patch `resolve_project_prefix()` or `create_worktree()` in that test.

#### F-2: A config test still claims the pre-fix title/worktree behavior — `Cosmetic` / `P3`

**Status:** CONFIRMED

**Location:** `tests/test_config.py:384-390`

**Evidence:**

```python
def test_given_registered_repo_when_resolving_then_returns_same_prefix_for_worktree_and_titles(self, tmp_path):
    # ...
    assert resolve_project_prefix(repo / ".worktrees" / "session-1") == "PROJECT"
```

**Why it matters:** The label claims `PROJECT` remains the worktree/title prefix,
which is precisely what AI-CLI-208 changes. Because the body checks only resolver
output, the stale behavioral claim continues to pass and can mislead future
reviewers.

**Verification command:**

```bash
rg -n 'same_prefix_for_worktree_and_titles' tests/test_config.py
```

**Verification note:** Reproduced on `2b3a6b2`; output was
`384:    def test_given_registered_repo_when_resolving_then_returns_same_prefix_for_worktree_and_titles(self, tmp_path):`.

**Recommendation:** Rename the test to state only that resolution preserves the
raw registered prefix. Keep naming/title assertions in the builder and launch
tests where those behaviors are actually exercised.

#### F-3: A test embeds a personal, platform-specific account path — `Cosmetic` / `P3`

**Status:** CONFIRMED

**Location:** `tests/test_project.py:81`; `AGENTS.md § Public Open-Source Package Standards`

**Evidence:**

```python
with patch("pathlib.Path.cwd", return_value=Path("/Users/bob/Projects/myapp/.worktrees/feature-1")):
```

The repository standard says verbatim: “**No personal identifiers** — no
personal names (first or last), usernames, private server IPs/hostnames, or
account-specific paths. Use generic placeholders: `user`, `myproject`,
`example.com`, `192.0.2.x`.” It also requires OS portability.

**Why it matters:** This is a public-package hygiene violation and bakes a macOS
account layout into a platform-neutral path parser test. It was encountered while
tracing project-name/worktree behavior and must not be allowed to accumulate.

**Verification command:**

```bash
rg -n '/Users/bob' tests/test_project.py
```

**Verification note:** Reproduced on `2b3a6b2`; output was
`81:    with patch("pathlib.Path.cwd", return_value=Path("/Users/bob/Projects/myapp/.worktrees/feature-1")):`.

**Recommendation:** Replace the literal with a generic, platform-neutral path
such as `/home/user/projects/myapp/.worktrees/feature-1`; the parser assertion is
unchanged.

### R1 Resolution Pass

| Finding | Status | How resolved |
|---------|--------|--------------|
| JA-1 | OPEN — implementation required | Source is read-only in this audit. Required `resolve_session()` / `find_recent_session()` case-insensitive lookup is specified above. |
| DV-1 | OPEN — implementation required | Source is read-only. Required canonical remote pre-transport naming path is specified above. |
| F-1 | OPEN — implementation required | Source is read-only. Required remote shell-boundary quoting is specified above. |
| IC-1 | OPEN — implementation required | Source is read-only. Required case-insensitive canonical full-name stripping is specified above. |
| JA-2 | OPEN — implementation required | Source is read-only. Required new-label normalization is specified above. |
| JA-3 | OPEN — test required | Tests are read-only. Required production fleet-registry new-launch regression is specified above. |
| F-2 | OPEN — test cleanup required | Tests are read-only. Rename recommendation is unambiguous; no team input is needed. |
| F-3 | OPEN — test cleanup required | Tests are read-only. Generic portable replacement is unambiguous; no team input is needed. |

No audit-document typo, stale label, or broken cross-reference required an inline
fix. Therefore there are no `FAIL — fixed inline` rows and no fix commit hashes to
record in this round.

### R1 Verification Matrix

| Finding | Command | Expected | Actual | Pass? |
|---------|---------|----------|--------|-------|
| JA-1 | Direct `resolve_session("c-APP-", "1")` reproduction (full command in finding) | Existing `c-app-1` is returned | `''` | ✅ Reproduces |
| DV-1 | Mocked remote `_do_session_launch()` (full command/equivalent in finding) | Emitter receives lowercase canonical values | `('c-r-APP-1', 'c', 'c-r-APP-1')` | ✅ Reproduces |
| F-1 | Mocked remote launch with prefix `APP; printf INJECTED` | Prefix reaches remote `ai` as one literal argument | Captured remote shell program contains `--project-prefix APP; printf INJECTED --project myproject` | ✅ Reproduces |
| IC-1 | Direct `build_session_name("c", "APP", "c-app-1")` reproduction | Round-trips to `('c-app-1', 'app-1')` | `('c-app-c-app-1-1', 'app-c-app-1-1')` | ✅ Reproduces |
| JA-2 | Direct `build_session_name("c", "APP", "Planning")` reproduction | Both outputs lowercase | `('c-app-Planning-1', 'app-Planning-1')` | ✅ Reproduces |
| JA-3 | `sed` the three candidate regression tests | One unmocked fleet → new launch → real worktree/tmux/title test | Integration test patches resolver/worktree; fleet test pre-creates and may skip; unit test uses XDG registration | ✅ Reproduces |
| F-2 | `rg -n 'same_prefix_for_worktree_and_titles' tests/test_config.py` | No stale pre-fix claim | `384: def test_given_registered_repo_when_resolving_then_returns_same_prefix_for_worktree_and_titles...` | ✅ Reproduces |
| F-3 | `rg -n '/Users/bob' tests/test_project.py` | No account-specific path | Match at line 81 | ✅ Reproduces |

**Verified: 8/8 findings reproduce on commit `2b3a6b2`.**

<!-- /doc:region name="round_1_findings" -->

## Round 2 — Verification Pass (append-only)

**Round 2 auditor:** Independent principal-engineer verification pass (fresh
invocation)

**Round 2 date:** 2026-08-10

**Round 2 scope:** Verify every Round 1 IC-N, JA-N, DV-N, and F-N finding
against implementation commit `7a1fe7b`; verify the absence or application of
AD-N decisions; inspect the Round 1 fix hunks for newly introduced N-N issues;
and independently run the three requested repository checks. No source or test
file was edited.

### R2 Summary

Seven Round 1 findings PASS and one is PARTIAL (DV-1). The case-insensitive
resume lookup, full-name round trip, requested-name lowercasing, shell quoting,
fleet-registry regression, stale test rename, and portable path cleanup are
present at `7a1fe7b`. DV-1 lowercases the remote preview, but the new helper does
not implement the server builder's allocation semantics for named sessions.
Two new confirmed issues were found: N-1 (P1) is that remaining remote identity
split; N-2 (P3) is a malformed module docstring introduced by the JA-3 test
edit. Round 1 declared no AD-N decisions, so there was no chosen option to
verify (`Decisions Requiring Team Input` section).

The three exact `uv run` commands were attempted. All stopped before invoking
Ruff or pytest because the sandbox denied uv's cache initialization; the final
diagnostic was `Operation not permitted (os error 1)`, so no tool summary line
was produced. Read-only fallbacks through the existing virtual environment
reported `All checks passed!` for Ruff check and `94 files already formatted`
for Ruff format. The pytest fallback also could not start: its final diagnostic
was `FileNotFoundError: [Errno 2] No usable temporary directory found`, so there
is no pytest pass/fail summary to quote. This sandbox cannot create or bind the
temporary local tmux socket required by `tests/test_session_launch_integration.py:57-88`;
tmux-dependent tests are therefore unverified here, not reported as either a
product pass or a product failure.

### R2.1 Round 1 IC/JA/DV verification

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | PASS — CONFIRMED | Case-insensitive prefix stripping is implemented by `clean_name.casefold().startswith(p.casefold())` at `src/ai_cli/session.py:610-626`; the three raw-`APP` canonical round trips are asserted at `tests/test_session.py:99-105`. Direct reproduction returned `('c-app-1', 'app-1')`. |
| JA-1 | PASS — CONFIRMED | `find_recent_session()` compares with `casefold()` and returns the tmux-reported spelling (`src/ai_cli/session.py:405-425`); `resolve_session()` applies the same rule for current and numeric explicit sessions (`src/ai_cli/session.py:534-558`). Tests cover numeric, current, and recent lookup at `tests/test_session.py:775-805,1450-1455`. Direct reproduction returned `'c-app-1'` for raw prefix `c-APP-`. |
| JA-2 | PASS — CONFIRMED | New-name sanitization now ends with `.lower()` and all new allocation branches use the lowercase helper (`src/ai_cli/session.py:624-643`). The new assertion at `tests/test_session.py:120-125` expects `c-app-planning-1` / `app-planning-1`; direct reproduction matched it. Existing numeric-slot candidates still return before new allocation at `src/ai_cli/session.py:628-636`. |
| JA-3 | PASS — CONFIRMED by source inspection; runtime sandbox-blocked | The new fixture writes a real uppercase fleet entry and clone (`tests/test_session_launch_integration.py:191-219`); the test does not patch prefix resolution or `create_worktree()`, starts from an empty isolated server, then asserts the real `app-1` worktree, `c-app-1` tmux session, and emitted title inputs (`tests/test_session_launch_integration.py:347-409`). Runtime execution could not be independently repeated because no writable temporary directory/socket is available. |
| DV-1 | PARTIAL — CONFIRMED | Present: the remote preview calls `_new_session_display_name()` at `src/ai_cli/main.py:1797-1805`, and the helper lowercases prefix/name at `src/ai_cli/session.py:579-582`. Missing: it does not sanitize/strip or allocate the named-session index performed at `src/ai_cli/session.py:610-643`. Direct comparison produced preview `c-r-app-planning` versus server `c-r-app-planning-1`; the new test codifies the index-less preview at `tests/test_session_launch_integration.py:412-447`. See N-1. |

### R2.2 Round 1 F-N verification

| ID | Verdict | Evidence |
|----|---------|----------|
| F-1 | PASS — CONFIRMED | The remote shell boundary now uses `shlex.quote(remote_prefix)` at `src/ai_cli/main.py:1789-1796`. The regression supplies `APP; printf INJECTED` and asserts its quoted representation is present at `tests/test_session_launch_integration.py:412-447`; direct capture confirmed the assertion is true. |
| F-2 | PASS — CONFIRMED | The stale title/worktree claim is gone. The test is now named `test_given_registered_repo_when_resolving_then_returns_raw_registered_prefix` and asserts only raw resolver behavior (`tests/test_config.py:383-390`). A targeted search returned only the new name. |
| F-3 | PASS — CONFIRMED | The account-specific macOS literal is gone; the fixture now uses `/home/user/projects/myapp/.worktrees/feature-1` (`tests/test_project.py:80-82`). A targeted search found no `/Users/bob` occurrence. |

### R2.3 AD-N decisions verification

| ID | Verdict | Evidence |
|----|---------|----------|
| None | PASS — not applicable | Round 1 explicitly records “None” and says all eight fixes require no product-policy choice (`Decisions Requiring Team Input` section). No AD-N option or approval exists to implement. |

### R2.4 NEW issues surfaced

#### N-1: Remote named-session preview still differs from the server's canonical identity — `MAJOR` / `P1`

**Status:** CONFIRMED

**Location:** `src/ai_cli/main.py:1797-1805`;
`src/ai_cli/session.py:579-582,610-643`;
`tests/test_session_launch_integration.py:412-447`

**What the Round 1 Resolution Pass claimed:** DV-1's row says, “Required
canonical remote pre-transport naming path is specified above.” Its detailed
recommendation requires a shared new-session canonicalizer and tests for an
uppercase requested name, profile/title emission, color lease, cleanup name,
and transport-loop name (Round 1 DV-1 recommendation).

**Actual state:** `main.py` now shares `_new_session_display_name()`, but that
helper only concatenates lowercase strings:

```python
# src/ai_cli/session.py:579-582
def _new_session_display_name(engine_short: str, project_prefix: str, name: str, is_remote: bool) -> str:
    """Build a lowercase tmux name for a newly allocated session."""
    remote_seg = "-r" if is_remote else ""
    return f"{engine_short}{remote_seg}-{project_prefix.lower()}-{name.lower()}"
```

The authoritative builder sanitizes/strips the requested name and calls
`find_next_index()` before returning `...-{clean_name}-{idx}`
(`src/ai_cli/session.py:610-643`). For `APP` + `Planning`, direct execution
returned:

```text
preview c-r-app-planning
server c-r-app-planning-1
```

The added remote test asserts the preview's divergent index-less value rather
than comparing it with the remote builder (`tests/test_session_launch_integration.py:441-447`).

**Why it matters:** The local client leases a color, emits a profile/title, and
constructs cleanup state for `c-r-app-planning`, while the remote host creates
`c-r-app-planning-1` (`src/ai_cli/main.py:1801-1805`; `src/ai_cli/session.py:640-643`).
Named remote sessions therefore retain the split identity DV-1 was meant to
remove; cleanup and profile/color bookkeeping target a name that is not the
actual session.

**Verification command:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./.venv/bin/python -c 'from unittest.mock import patch; from ai_cli.session import _new_session_display_name,build_session_name; p=patch("ai_cli.session._matching_tmux_sessions",return_value=[]); p.start(); print("preview",_new_session_display_name("c","APP","Planning",True)); print("server",build_session_name("c","APP","Planning",is_remote=True)[0]); p.stop()'
```

**Verification note:** CONFIRMED at `7a1fe7b`; output is quoted above. This is a
missing part of the DV-1 resolution, not a judgment-only hypothesis.

**Recommended fix (Round 3):** Replace the client-side guessed identity with a
remote preflight/handshake that returns the exact canonical allocated
`session_id` and `ai_name` before local profile/color/cleanup setup, or otherwise
make named allocation deterministic across the transport boundary. Regress
unnamed, numeric, named, sanitized-name, occupied-index, and legacy-case reuse;
each test must compare the client preview/cleanup identity with the server
builder's exact returned identity.

#### N-2: JA-3 test edit left a malformed module docstring — `Cosmetic` / `P3`

**Status:** CONFIRMED

**Location:** `tests/test_session_launch_integration.py:1-7`

**What the Round 1 Resolution Pass claimed:** JA-3 required a production
fleet-registry-to-new-worktree regression test (Round 1 JA-3 resolution row).

**Actual state:** The test was added, but its accompanying module-docstring edit
left an unmatched parenthesis and a missing noun phrase:

```python
# tests/test_session_launch_integration.py:3-7
These tests run against a real ``tmux`` server on an isolated socket so we
exercise actual tmux ``new-session`` / ``has-session`` / ``kill-session``
behavior — not a mock. Everything downstream of tmux (the engine binary,
is still mocked except where a test explicitly exercises production worktree
creation or registry resolution.
```

**Why it matters:** The test module's scope statement is no longer grammatical
and does not clearly identify which boundaries remain mocked, making the new
integration coverage easier to overstate.

**Verification command:**

```bash
sed -n '1,8p' tests/test_session_launch_integration.py
```

**Verification note:** CONFIRMED at `7a1fe7b` by the exact quoted source. The
malformed text appears in the JA-3 fix hunk and was introduced by Round 1's
resolution commit.

**Recommended fix (Round 3):** Restore a complete sentence that explicitly says
the engine remains mocked and that production worktree creation/registry
resolution are exercised only by the designated regression test.

### R2.5 Verification Matrix

| Finding | Command / check | Expected | Actual | Pass? |
|---------|-----------------|----------|--------|-------|
| IC-1 | Direct raw-`APP` canonical full-name round trip | `('c-app-1', 'app-1')` | `('c-app-1', 'app-1')` | ✅ |
| JA-1 | Direct `resolve_session("c-APP-", "1")` and `find_recent_session("c-APP-")` | Exact existing `c-app-1` | Both returned `'c-app-1'` | ✅ |
| JA-2 | Direct uppercase requested-name build | Lowercase `c-app-planning-1` / `app-planning-1` | Exact expected tuple | ✅ |
| JA-3 | Source-trace fleet fixture through unpatched resolver/worktree and real-server assertion | Registry `APP` → worktree `app-1`, tmux `c-app-1`, lowercase title args | All assertions present at `tests/test_session_launch_integration.py:347-409`; runtime blocked by temp/socket policy | ✅ source / ⚠ runtime unverified |
| DV-1 | Compare `_new_session_display_name()` with `build_session_name(..., is_remote=True)` for a named launch | Exact same canonical identity | `c-r-app-planning` vs `c-r-app-planning-1` | ❌ PARTIAL |
| F-1 | Capture remote program with shell metacharacters in prefix | Quoted prefix remains one shell argument | `shlex.quote(...) in remote_exec` was `True` | ✅ |
| F-2 | Search stale and replacement test names | No stale name; replacement present | Only replacement at `tests/test_config.py:384` | ✅ |
| F-3 | Search old and replacement fixture paths | No account-specific path; generic path present | Only generic path at `tests/test_project.py:81` | ✅ |

**Verified: 7/8 Round 1 fixes PASS at `7a1fe7b`; DV-1 is PARTIAL. Two new
findings reproduce. JA-3 runtime remains unverified because the sandbox cannot
create its temporary tmux socket.**

### R2 Recommendations

**MUST be fixed before AI-CLI-208 ships:**

- N-1: make the remote client's profile/color/cleanup identity exactly match
  the server's canonical allocated identity for named and occupied-index cases,
  then run the full tmux-backed regression outside this sandbox.

**SHOULD be fixed before the next verification gate:**

- Re-run `uv run ruff check src/ tests/`, `uv run ruff format --check src/
  tests/`, and `uv run pytest -q` in an environment with a writable uv cache,
  temporary directory, and local tmux socket; record all three final summary
  lines.

**Can be folded into a follow-up:**

- N-2: repair the integration-test module docstring while applying N-1's test
  changes.

## Decisions Requiring Team Input

None. All eight corrections follow directly from the stated acceptance criteria
or repository standards. No AD-N choice is needed, and inventing one would turn
clear correctness work into unnecessary product-policy debate.

## Outstanding Issues to Fix

| ID | Priority | Issue | Linked finding(s) | Owner | Target |
|----|----------|-------|-------------------|-------|--------|
| I-09 | P1 | Make remote named-session profile/color/cleanup identity exactly match the server's canonical allocated identity. | DV-1, N-1 | **Deferred — AI-CLI-209** (bd, P1, `related` to this issue) | Separate protocol-level fix; verified pre-existing, out of AI-CLI-208's casing scope |
| I-10 | P3 | Repair the malformed integration-test module docstring. | N-2 | Orchestrating session | **Fixed** in `tests/test_session_launch_integration.py` |

## Already-Correct Items

- ✅ Raw prefix resolution is preserved: `_fleet_registry_prefix()` returns the
  literal registry value (`src/ai_cli/config.py:535-541`),
  `resolve_project_prefix()` returns it unchanged (`config.py:645-676`), and
  `get_project_prefix()` delegates unchanged (`session.py:237-245`). Direct
  reproduction printed `raw-prefix APP`.
- ✅ Unnamed and numeric **new** allocations lowercase the registered prefix at
  `src/ai_cli/session.py:589-622`; target tests assert `PROJECT` remains raw while
  outputs become `c-project-1` / `project-1`
  (`tests/test_session.py:1504-1521`).
- ✅ AI-CLI-206 explicit tmux matching remains case-insensitive and returns the
  exact existing name (`src/ai_cli/session.py:279-302,611-618`). Direct
  reproduction returned `('c-r-App-7', 'App-7')` from raw `APP`.
- ✅ AI-CLI-206 bare matching remains case-insensitive and returns the exact
  existing directory casing (`src/ai_cli/session.py:305-327,611-618`). Direct
  reproduction returned `('c-App-7', 'App-7')`.
- ✅ AI-CLI-206 auto-index matching still treats a legacy remote lowercase
  session as occupying the raw-uppercase local slot
  (`src/ai_cli/session.py:330-348`); direct reproduction returned `next 2`.
- ✅ The four non-filesystem AI-CLI-206 regression test bodies ran directly: the
  explicit tmux reuse test passed for both engines, the ambiguity test passed,
  `find_next_index` passed, and the launch reuse test passed. The target commit
  does not modify the six AI-CLI-206 matching/index functions.
- ✅ `_prefix_from_session_name()` already normalizes its extracted result and has
  no production call site (`src/ai_cli/session.py:220-234`; source-wide `rg`).
- ✅ The normal local consumer chain uses the builder's returned values verbatim:
  `create_worktree(ai_name)` at `src/ai_cli/main.py:1929`, iTerm setup at
  `main.py:2166-2194`, and tmux creation from `session_id` at
  `main.py:2196-2223`. No raw-prefix comparison occurs in that chain.
- ✅ iTerm2 configuration, profile generation, icon naming, and OSC title emission
  consume `ai_name`/`session` verbatim and introduce no additional casing
  transformation or raw-prefix comparison (`src/ai_cli/iterm2.py:143-245,370-429`;
  `src/ai_cli/icon_generator.py:175-243`).
- ✅ Claude `customTitle` lookup and assignment use the same `ai_name` exact value:
  `--name "$ai_name"` and `ct==t` appear together at
  `src/ai_cli/session_script.py:482-514`. Lowercased new values do not violate an
  independent raw-prefix comparison there.
- ✅ The `get_project_prefix()` docstring now accurately distinguishes raw
  registry casing from normalized session artifacts
  (`src/ai_cli/session.py:237-245`).

## Anti-Patterns to Watch For

- Do not audit only `build_session_name()`. The remote client preview at
  `main.py:1801` is a second constructor and is operationally authoritative for
  remote iTerm titles.
- Do not equate explicit-slot reuse with `--resume`. They are separate code paths;
  the former is case-insensitive while the latter still used `startswith()` and
  an exact `has-session` target.
- Do not call a test “fleet-registry integration” when it patches the resolver,
  or “worktree creation” when it patches `create_worktree()` and only asserts the
  argument.
- Do not accept a green test whose name claims behavior its assertions never
  exercise. `tests/test_config.py:384` is exactly that failure mode.
- Do not report pytest success from this sandbox. Its mandatory temp-backed
  autouse fixture failed before test execution; production-level direct
  reproductions are recorded separately.
- Do not infer absence from one grep. Every candidate gap above was spot-checked
  in neighboring tests and consumer code before it became a finding.
- Do not treat a shared lowercasing helper as a shared canonical identity
  builder. A transport preview must be compared with the server builder for
  named, sanitized, and occupied-index cases; `tests/test_session_launch_integration.py:441-447`
  instead asserted the preview's divergent value.

## Sign-Off Checklist

- [x] No CRITICAL / P0 findings exist.
- [ ] All MAJOR / P1 findings fixed OR explicitly deferred with rationale in Outstanding Issues.
- [x] All MINOR / P2 / P3 findings logged to a follow-up implementation tracker.
- [x] No AD-N decisions are pending; none were required.
- [x] Verification Matrix run on all findings; 8/8 reproduce recorded.
- [x] At least one verification round (Round 2+) completed after fixes.
- [ ] Re-grep verification done in the final resolution round.
- [x] Inline-fix accounting complete: no inline target fixes were made.
- [x] Already-Correct Items populated with specific evidence.
- [x] Anti-Patterns section records the methodology gaps exposed by this audit.
- [ ] User reviewed and approved final sign-off.

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Action | Notes |
|------|--------|-------|
| 2026-08-10 | Round 1 audit pass complete | 8 confirmed findings: 3 P1, 3 P2, 2 P3; 8/8 reproduced; no inline source/test edits; no AD-N decisions. |
| 2026-08-10 | Round 2 verification pass complete | Fresh verification at `7a1fe7b`: 7 PASS, DV-1 PARTIAL, 2 new confirmed findings (1 P1, 1 P3), no source/test edits. Exact `uv run` checks were sandbox-blocked; Ruff fallbacks passed, while pytest and isolated tmux sockets could not start without writable temporary storage. |

<!-- /doc:region name="audit_log" -->

## Appendix: Files Read

**Audit format and repository instructions:**

- Canonical `docs/audits/TEMPLATE.md` fallback — full 917-line read before any
  task action. The repo-local and prompt-named project-template copies were
  absent; the canonical sibling-workspace copy was used.
- `docs/audits/ai-cli-208-session-naming-casing-audit.md` — full scaffold and
  immutable reviewer prompts.
- `docs/audits/README.md` — full read; audit-document conventions.
- `AGENTS.md` — full read; public-package, portability, CLI, and test standards.

**Primary source (full reads):**

- `src/ai_cli/session.py` — all 1,157 lines; every named target function,
  worktree construction, prefix parsing, and adjacent cleanup behavior.
- `src/ai_cli/main.py` — all 3,397 lines; launch prefix resolution, local/remote
  constructors, worktree, CC, iTerm2, session-map, and tmux flows.
- `src/ai_cli/config.py` — all 983 lines; every registry tier, raw-value returns,
  validation, registration, and project aliases.
- `src/ai_cli/iterm2.py` — all 441 lines; configuration, color leasing, profile,
  icon, and title consumers.
- `src/ai_cli/session_script.py` — all 597 lines; `ai_name`, `project_prefix`,
  customTitle lookup, Claude `--name`, and in-session iTerm behavior.
- `src/ai_cli/icon_generator.py` — all 278 lines; profile/icon naming consumers.
- `src/ai_cli/session_adopt.py` — full file; second `create_worktree()` caller and
  exact-title/worktree consumers surfaced by source-wide search.

**Required tests (full reads):**

- `tests/test_session.py` — all 1,573 lines.
- `tests/test_session_launch_integration.py` — all 376 lines.
- `tests/test_bare_worktree.py` — all 439 lines.
- `tests/test_worktree_container_collision.py` — all 319 lines.
- `tests/test_main.py` — all 1,330 lines.
- `tests/test_iterm2.py` — all 677 lines; expanded consumer-chain coverage.

**Additional targeted reads surfaced by searches:**

- `tests/test_config.py:365-405` — stale raw-prefix test description.
- `tests/test_project.py:1-220,420-579` — project/worktree path parsing and
  registry-related cases.
- `tests/test_cli.py:350-930` — remote launch, resume, and session CLI cases.
- `tests/conftest.py:220-270` — temp-backed autouse fixture that blocked pytest in
  the read-only sandbox.

**History and external requirement artifacts:**

- Full target diff for `2b3a6b2` and full AI-CLI-206 diff for `2519721`, including
  every modified source/test hunk and commit metadata.
- `git log` for the target files between `2519721` and `2b3a6b2`, and current-tree
  diff against the target.
- AI-CLI-208 external issue read attempted through `bd`; unavailable because the
  embedded database lock could not be opened. The full AC text in the reviewer
  prompt was read and used instead.

**Round 2 verification reads:**

- Canonical sibling-workspace audit `TEMPLATE.md` — full 917-line read; Round 2
  structure, N-N taxonomy, verification matrix, and exact AD-N skeleton checked
  before source inspection.
- `docs/audits/ai-cli-208-session-naming-casing-audit.md` — all 1,044 pre-Round-2
  lines; every Round 1 finding, Resolution Pass row, decision statement,
  verification result, and immutable reviewer prompt.
- `src/ai_cli/session.py` — all 1,174 current lines; complete post-fix builder,
  lookup, legacy-case reuse, worktree, and cleanup paths.
- `tests/test_session.py` — all 1,612 current lines; complete post-fix unit
  coverage, including every added Round 1 regression.
- `tests/test_session_launch_integration.py` — all 512 current lines; complete
  fixture and test-boundary review, including real registry/worktree assertions
  and remote preview expectations.
- `src/ai_cli/main.py:1738-1820,1880-1945,2145-2235` — raw prefix, remote
  transport preview/cleanup, canonical server builder call, worktree, iTerm2,
  and tmux consumers.
- `src/ai_cli/config.py:440-690,734-765` — fleet discovery, raw prefix return,
  permissive prefix parsing, and projects-directory lookup used by JA-3/F-1.
- `tests/test_config.py:375-400`, `tests/test_project.py:70-90`, and
  `tests/conftest.py:210-285` — F-2/F-3 replacements and the autouse temporary
  storage requirement that blocked pytest.
- Commit `7a1fe7b` metadata, full six-file fix diff, current `git status`, and
  current-tree diff/stat against `2b3a6b2`.

## Appendix: Commands Run

```bash
# Template and file discovery / full reads
rg --files docs/audits
rg -n '^## |^### |^#### ' <canonical-audit-template>
sed -n '<successive non-overlapping ranges>' <each full-read file>

# Ground-truth target and predecessor
git status --short
git log -1 --format='%H %s'
git show --format='%H%n%P%n%s' --no-patch 2b3a6b2
git show --format='%H%n%P%n%s' --no-patch 2519721
git diff --exit-code 2b3a6b2..HEAD -- src tests
git show --format= --unified=80 2519721..2b3a6b2 -- \
  src/ai_cli/session.py tests/test_session.py tests/test_session_launch_integration.py

# Source-wide call-site and constructor inventory (repeated for every audited symbol)
rg -n --glob '*.py' '<symbol>' src
rg -n --glob '*.py' '(tmux.*session|session.*tmux|customTitle|SetProfile|OSC 1|ai_name|project_prefix)' src/ai_cli

# Finding reproductions (full commands appear in each detailed finding)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c '<JA-1 resolve_session reproduction>'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -c '<DV-1 remote launch reproduction>'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c '<IC-1 and JA-2 build_session_name reproductions>'
sed -n '275,311p' tests/test_session_launch_integration.py
sed -n '214,243p' tests/test_worktree_container_collision.py
sed -n '1504,1517p' tests/test_session.py
rg -n 'same_prefix_for_worktree_and_titles' tests/test_config.py
rg -n '/Users/bob' tests/test_project.py

# Preserved-behavior reproductions
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c '<tmux/bare legacy-case and raw-prefix checks>'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python -c '<direct AI-CLI-206 test-body checks>'

# Test runner attempt: blocked before test execution because the sandbox denies
# the writable temp root required by tests/conftest.py::_isolate_quota_state.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -c '<pytest.main focused AI-CLI-206 nodes>'

# External issue lookup (failed: embedded database lock unavailable)
bd show AI-CLI-ms2i
```

Round 2 commands:

```bash
# Current target and Round 1 resolution commit
git status --short
git log -8 --oneline --decorate
git rev-parse HEAD
git diff 7a1fe7b^ 7a1fe7b --unified=8 -- \
  src/ai_cli/main.py src/ai_cli/session.py tests/test_config.py \
  tests/test_project.py tests/test_session.py tests/test_session_launch_integration.py

# Required checks — all three blocked before tool startup by sandbox-denied uv cache access
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q

# No-cache uv retries — blocked by sandbox-denied temporary storage
UV_NO_CACHE=1 uv run --no-sync ruff check src/ tests/
UV_NO_CACHE=1 uv run --no-sync ruff format --check src/ tests/

# Read-only existing-environment fallbacks
./.venv/bin/ruff check --no-cache src/ tests/
./.venv/bin/ruff format --check src/ tests/
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/pytest -q -p no:cacheprovider

# Finding verification and fix-presence checks
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./.venv/bin/python -c \
  '<IC-1, JA-1, JA-2, DV-1, and F-1 direct reproductions>'
rg -n 'same_prefix_for_worktree_and_titles|returns_raw_registered_prefix' tests/test_config.py
rg -n '/Users/bob|/home/user/projects/myapp/.worktrees/feature-1' tests/test_project.py
sed -n '1,8p' tests/test_session_launch_integration.py
git diff --check 7a1fe7b^ 7a1fe7b
```

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** Codex audit role (`cx audit`, effort: high)

**Date:** 2026-08-10

```text
You are a principal staff engineer specializing in developer-experience tooling and CLI session
management. You have shipped production systems in this domain and you know the gap between what
looks rigorous on paper and what actually holds up. You call out that gap directly. When you cannot
verify a claim, you say so explicitly rather than waving past it. Your judgment is the product, not
a summary.

You are READ-ONLY on source code, docs, and configuration EXCEPT for the audit doc itself (which
you write to) and INLINE FIXES IN THE TARGET DOC for the narrow class of stale-label / typo /
cross-reference errors where the correct value is unambiguous.

Inline fix discipline: if you fix something inline, record it in the Round 1 Resolution Pass table
as `FAIL — fixed inline` with the commit hash of your fix.

## Your Task

Audit the AI-CLI-208 fix (files: `src/ai_cli/session.py`, `tests/test_session.py`,
`tests/test_session_launch_integration.py`) at commit `2b3a6b2` in
`/Users/user/projects/ai-cli-utils/.worktrees/ai-cli-1` against the following scope on
these validation dimensions:

SCOPE: newly allocated tmux session names, `ai_name` (worktree directory name), and the CC
session's `customTitle` (iTerm2 tab/pane title, set from `ai_name`) must always be lowercase,
regardless of the fleet-registry prefix's registered casing (e.g. a registry prefix of "APP" must
never produce a new worktree/session/title containing "APP" — only "app"). Meanwhile
`resolve_project_prefix()` and `get_project_prefix()` must keep returning the registry's raw,
unmodified value for any other consumer (their own docstrings and any bd-task-id-prefix use are
out of scope for lowercasing). AI-CLI-206's case-insensitive RESUME matching (commit `2519721`,
same file: `_resolve_explicit_tmux_slot`, `_resolve_explicit_bare_slot`, `_matching_tmux_sessions`,
`_matching_worktrees`, `find_next_index`, `_find_next_index_from_worktrees`) — which intentionally
preserves whatever casing an *existing* worktree/session already has when reusing it — must be
completely unaffected by this fix.

  1. Internal Consistency (IC-N): does the target contradict itself? Cross-reference every section
     against every other.
  2. Spec / AC Compliance (JA-N): does it satisfy every AC below?
  3. Domain Validity (DV-N): are the engineering choices defensible against the live code (e.g. is
     lowercasing applied at every code path that constructs a NEW name, and nowhere it shouldn't
     be)?
  4. Independent Findings (F-N, open scope): surface anything else that matters — missing edge
     cases, undocumented assumptions, contradictions with existing code, test gaps.

Acceptance criteria to verify:
- A newly created worktree directory name is always lowercase, regardless of the fleet-registry
  prefix's registered casing.
- A newly created tmux session name is always lowercase (except the engine tag c/g and the -r-
  remote marker, which are already lowercase).
- The CC session's customTitle (iTerm2 tab/pane title, set from ai_name) is always lowercase.
- resolve_project_prefix()/get_project_prefix()'s return value is UNCHANGED (still returns the
  registry's literal casing).
- No regression to AI-CLI-206's fix (case-insensitive resume matching for a legacy
  uppercase-cased worktree/session must keep working exactly as it does today).
- A regression test exists: a fleet-registry prefix registered uppercase; a brand new session
  launch (no pre-existing worktree/session for that slot); asserts the resulting worktree dir,
  tmux session name, and ai_name/title are all lowercase.
- Check for any OTHER place in the codebase that constructs a worktree name, tmux session name, or
  CC title from a project prefix outside of `build_session_name()` — if one exists and was missed,
  that's a finding.
- Check `_prefix_from_session_name()`, `resolve_session()`, `find_recent_session()`, and any
  iterm2.py consumer of `ai_name` for a casing assumption that this fix's lowercasing could now
  violate (e.g. a comparison against a raw, still-uppercase `project_prefix` that no longer
  matches the now-lowercased `ai_name`).

For findings that require team input (you cannot decide alone), do NOT apply a fix. Move them to
the "Decisions Requiring Team Input" section as AD-N with two or three options, pros / cons /
recommendation (each option its own subsection; bullets one per line).

For each finding, supply:
  - File:line reference.
  - Exact quoted evidence (verbatim — paraphrasing is a failure mode).
  - Why it matters (1-2 sentences on user-visible impact or architectural risk).
  - A bash verification command that demonstrates the finding.
  - A specific recommended fix.

You MUST run a Verification Matrix on at least 5-10 of your own findings (or all of them if fewer
than 5): re-run the verification command and record the actual output. A finding without a
reproduced verification command is a hypothesis, not a fact.

## Code-review scope (lean toward over-reading)

Read all source code and tests the target references, modifies, or makes claims about. This is a
completeness requirement, not a sampling exercise. Bias toward reading too much code rather than
too little.

For every symbol / function the target references, run `grep -r <symbol> <src-roots>` to surface
every call site. Add anything that surfaces to your read list before producing findings.

Record every file you read in `## Appendix: Files Read`, grouped by category.

## Files to read (read in full, do not skim — and expand this list during the run)

### Audit format (read FIRST — this is how to WRITE the audit)

0. `docs/audits/TEMPLATE.md` in this repo if present; otherwise
   `~/projects/project-template/template/docs/audits/TEMPLATE.md`. Conform your output to it.

### Primary subject

1. `src/ai_cli/session.py` — read in full, especially `get_project_prefix()`,
   `build_session_name()`, `find_next_index()`, `_find_next_index_from_worktrees()`,
   `_resolve_explicit_tmux_slot()`, `_resolve_explicit_bare_slot()`, `_matching_tmux_sessions()`,
   `_matching_worktrees()`, `_prefix_from_session_name()`, `resolve_session()`.
2. `tests/test_session.py` — `TestGetProjectPrefix`, `TestFindNextIndex`, and every test touching
   `build_session_name`.
3. `tests/test_session_launch_integration.py` — every test using `build_session_name` /
   `_do_session_launch` with a registered prefix.
4. `tests/test_bare_worktree.py`, `tests/test_worktree_container_collision.py`,
   `tests/test_main.py` — AI-CLI-206's regression tests; verify none of them silently now assert
   stale (pre-lowercasing) expectations that happen to still pass for the wrong reason.

### Consumers of ai_name / session naming (pattern-consistency review)

5. `src/ai_cli/main.py` — every call site of `build_session_name`, and where `ai_name`/`session_id`
   flow into `_iterm2._emit_iterm2_profile_setup`, `create_worktree`, session-map writes.
6. `src/ai_cli/iterm2.py` — `_emit_iterm2_profile_setup`, `_resolve_iterm2_config`,
   `_assign_iterm2_color_slot`, and anywhere `ai_name` is used for a profile name, icon, or title.
7. `src/ai_cli/config.py` — `resolve_project_prefix()`, `get_project_prefix()`'s docstring claim
   ("Worktree names and custom session titles are both built from this value") — verify it's still
   accurate post-fix or needs updating.

### bd issue

8. AI-CLI-208 (external ref; hash id AI-CLI-ms2i) — full description and acceptance criteria (see
   Scope above; also `bd show AI-CLI-ms2i` if `bd` is reachable in your environment — otherwise
   rely on the Scope section above, which reproduces it).

## Output

Write findings into this audit doc following the Round 1 section structure:
  R1 Summary → R1 Findings (IC / JA / DV / F tables + detailed F-N subsections) → R1 Resolution
  Pass → R1 Verification Matrix → AD-N entries in "Decisions Requiring Team Input" if any →
  Already-Correct Items.

Append a row to the Audit Log when done. Update the Status Summary's cross-round counts and
ship-readiness verdict.

Never fabricate evidence to satisfy a section. Empty findings sections are honest if nothing was
found; faked findings are not. Cite file:line for every codebase claim.

## Anti-patterns (avoid)

- Code-only check that ignores test file changes.
- Skipping the iterm2.py / main.py consumer chain and trusting session.py alone.
- Inline fixes without commit hashes recorded in Resolution Pass.
- Empty Already-Correct Items list (the audit's credibility depends on it).
- Verification commands that aren't actually run.
- Under-reading the codebase — read sibling / neighbor code for pattern-consistency.
- Treating the run as done because the doc exists, is large, has a fresh mtime, or the command
  exited 0. An UNFILLED template passes all four — grep for a finding heading, or run
  ai-harness/scripts/check_audit_doc_filled.py.
```

### Round 2 Reviewer Prompt (Re-audit)

**Model:** Codex audit role (`cx audit`, effort: high — fresh invocation for independent
verification)

**Date:** 2026-08-10 (post-Round-1)

```text
You are a principal staff engineer specializing in developer-experience tooling and CLI session
management (same domain as Round 1, a fresh invocation for independent verification). You are
reading the Round 1 audit of the AI-CLI-208 fix — see the Round 1 Reviewer Prompt above in this
same audit doc. This is the Round 2 verification pass.

Your task is to verify that EVERY Round 1 finding (IC-N / JA-N / DV-N / F-N) and EVERY AD-N
decision has been correctly applied to the target. You will also surface NEW issues (N-N) that the
Round 1 fixes themselves introduced.

This is NOT an exhaustive re-audit. It is a verification pass. The Round 1 auditor already did the
broad coverage; you are confirming the Resolution Pass table's claims are actually true in the
target.

## Constraints

- APPEND-ONLY: do not edit the target code in this round. If a fix is missing or incorrect,
  surface it as an N-N finding for the next round to apply.
- READ-ONLY on Round 1 findings: do not rewrite IC-1's wording or change F-3's severity. Verify,
  report PASS / FAIL / PARTIAL with quoted evidence.

## Verification methodology

For each Round 1 finding:
  1. Read the Resolution Pass row's "How resolved" claim.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).

For AD-N decisions: locate the chosen option's implementation in the target and verify it matches
the chosen option.

For NEW issues: re-read the target sections Round 1 modified. Look for stale cross-references
introduced by Round 1, Resolution Pass claims that didn't actually land, Round 1 fixes that
introduced new contradictions, and draft-author scaffolding left over from the Round 1 edit pass.

Also independently re-run: `uv run ruff check src/ tests/`, `uv run ruff format --check src/
tests/`, and `uv run pytest -q` (the FULL suite) in
`/Users/user/projects/ai-cli-utils/.worktrees/ai-cli-1`, and quote the final summary line.
If your sandbox cannot bind local tmux sockets, say so explicitly rather than reporting a false
pass or a false fail on tmux-dependent tests.

## Output

Write into the Round 2 section of this audit doc:
  R2 Summary → R2.1 IC/JA/DV verification table (PASS/FAIL/PARTIAL + evidence) → R2.2 F-N
  verification table → R2.3 AD-N verification table → R2.4 NEW issues (N-N) detailed subsections →
  R2 Recommendations (MUST / SHOULD / can-defer).

Append a row to the Audit Log. Update the Status Summary cross-round counts. Never fabricate; cite
file:line for every claim.

## Files to read

0. `docs/audits/TEMPLATE.md` in this repo, or (if not present)
   `~/projects/project-template/template/docs/audits/TEMPLATE.md`.
1. `src/ai_cli/session.py`, `tests/test_session.py`, `tests/test_session_launch_integration.py` —
   the files Round 1 covered.
2. THIS AUDIT DOC — the Round 1 section is your verification checklist.
```

<!-- /doc:region name="appendix_reviewer_prompt" -->
