---
title: "Machine facts baked in at authoring time: a lease hostname as durable identity, and a literal projects root"
status: fixed
kind: bug
issue: AI-CLI-987p
related:
  - AI-CLI-hgna
---

# Machine facts baked in at authoring time

Two independent facts about the host were decided when the code was written rather than resolved
when it runs: which machine this is, and where this machine keeps its projects. Both have a seam
already in the package, and both had call sites that went around it.

## Symptom

Nothing failed loudly. That is the shape of this class of bug: every symptom is a correct-looking
answer about the wrong machine.

### Host identity

```text
$ env -u AI_HOST python -c "from ai_cli.config import detect_machine_profile; print(detect_machine_profile())"
{'host_id': 'ip-100-120-40-17', 'os_type': 'linux'}

$ cat /etc/ai-harness/machine
sem-kg-ec2
```

`ip-100-120-40-17` is the EC2 lease's DNS name. It changes when the instance is replaced. The
marker file sitting beside it, written by whatever provisioned the machine, held the stable
answer and nothing read it.

### Projects root

With `[project] projects_dir` pointing somewhere other than `~/projects`:

```text
$ ai copier-update
Error: projects directory not found: /home/<user>/projects
```

and, worse, `ai setup` decided there was no managed platform, copied `CLAUDE-full.md` over
`CLAUDE.md`, and marked the result `assume-unchanged` so the swap did not appear in
`git status`.

## Mechanism

### Host identity

```python
host_id = os.environ.get("AI_HOST") or socket.gethostname()
```

Two tiers, and the gap between them is the bug. `AI_HOST` is exported by an interactive login
profile, so it is present in a terminal and absent in a git hook, `ai update`, a cron job or an
agent shell. Those are disproportionately the contexts that register a machine for the first
time, and the registration is a one-way door: `ensure_machine_profile_registered` writes
`host_id` only when the key is missing, so the first answer is the machine's identity from then
on.

What consumes it decides how much that matters:

- the `[machine] host_id` key persisted in `config.toml`, never re-detected;
- `machine_name` in a chief-of-staff registration, which is how other machines name this one;
- `telemetry._get_machine_id`, whose docstring promised "a stable machine identifier" while
  returning `socket.gethostname()` directly.

All three outlive the lease whose name they would have recorded.

### Projects root

`config._get_projects_dir()` reads `[project] projects_dir` and falls back to
`Path.home() / "projects"`. Four call sites wrote the fallback out by hand instead of calling it:

| Call site | Consequence |
| --- | --- |
| `setup._is_managed_platform` | wrong answer is destructive (see Symptom) |
| `copier_update.run_copier_update` default | refuses with "projects directory not found" |
| `main.cmd_trust_backfill` `--root` default | a click default is evaluated at import, so it cannot read configuration at all |
| `sync._sync_pull` memories-only branch | disagrees with its own sibling branch and hands the answer to the transcript repather |

## Hypotheses rejected

**That the hostname fallback is harmless because `AI_HOST` is always set.** Measured false in
this repository's own Bash tool calls on the affected host: `AI_HOST` was unset while
`/etc/ai-harness/machine` held the answer. The fallback is reached exactly where it is least
observed.

**That `~/user-default-efs` in `canonical_worktrees` is the same bug.** It is not, and this was
checked rather than assumed. `AI-CLI-hgna` already fixed it: the path is accepted only when
`_is_network_backed` confirms a network mount, so the directory `credo` creates on this EC2 host
no longer satisfies it. Measured: `findmnt -t nfs,nfs4` returns nothing here and the registry
resolves to the XDG data directory. Left unchanged.

**That every `Path.home() / "projects"` in the tree is a defect.** Three of them
(`transcript_repath`, `session_audit`, `cc_migrate`) are `~/.claude/projects`, Claude Code's own
fixed layout, which is not the user's projects root and is not configurable. A grep that treats
them as the same string is the reason the first pass over-counted.

**That `native_deps` assumes Homebrew.** `/opt/homebrew/lib` appears only in a comment
illustrating a dyld message; the pattern beside it is `Library not loaded:\s*(\S+)` and matches
any prefix.

## RED test

`tests/test_machine_agnostic_paths.py`, run with the production code at the base commit. Every
test runs under a different home directory, machine identity and projects root than the machine
running the suite, because on the author's machine a hardcoded `~/projects` and a configured
`projects_dir` that happens to be `~/projects` return the same value.

```text
11 failed, 1 passed

assert 'ip-100-120-40-17' == 'sem-kg-ec2'
assert False is True
 +  where False = _is_managed_platform()
assert 78 == 0
Error: projects directory not found: <tmp>/home/projects
At index 0 diff: PosixPath('~/projects') != PosixPath('<tmp>/real-projects')
assert PosixPath('<tmp>/home/projects') == PosixPath('<tmp>/real-projects')
```

The one passing test is the arm asserting an explicit `--root` still wins, so the fix cannot
satisfy the suite by ignoring the option instead.

## Patch

`detect_machine_profile` reads `AI_HOST`, then `MACHINE_MARKER_FILE`, then the hostname, and
returns `host_id_source` naming the tier that answered. `ensure_machine_profile_registered`
reports that tier, and when the hostname had to stand in it says so and names the absent marker,
because that is the call that makes the guess durable. `telemetry._get_machine_id` resolves
through the same function.

The four projects-root call sites call `_get_projects_dir()`. `cmd_trust_backfill` resolves its
default at invocation rather than declaring it, so configuration can reach it.

## GREEN result

```text
$ uv run pytest -q tests/test_machine_agnostic_paths.py
12 passed
```

## Prevention

A test that does not move the home directory, the machine identity and the projects root cannot
tell a resolved value from a baked-in one, because on the author's machine they agree. That is
why six of these tests existed nowhere before: the behaviour was never wrong *here*.

Two narrower lessons:

- A two-tier resolver with no third tier is making a claim about where it runs. `AI_HOST or
  gethostname()` reads as "the variable, or a sensible default" and is really "the variable, or a
  different kind of answer entirely".
- A `click` option default cannot read configuration, because it is evaluated at import. Any
  default that should follow configuration has to be `None` plus a resolution at invocation.

One sibling case is deliberately still open: `sync.get_source_machine` and
`messaging.NATSClient._open_ssh_tunnel` derive this machine's role in the fleet from
`sys.platform` and the literal name `"mac"`. Fixing them needs a configured notion of role, and
the tunnel case changes which machines open an outbound SSH connection, so the options and a
recommendation live on the issue rather than in a commit.
