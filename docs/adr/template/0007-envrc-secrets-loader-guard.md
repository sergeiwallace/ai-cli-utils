# 0007. `.envrc` guards the secrets loader and has no opt-out

Governs: `.envrc`.

## Decision

`.envrc` calls `load_ai_core_secrets` only when it is defined, and otherwise prints a warning
that names the consequence. The warning cannot be silenced.

## Why

`load_ai_core_secrets` is a direnv stdlib extension function, not a program on PATH: only a
direnvrc defines it. On a machine without that direnvrc the call used to fail with
`command not found`, and direnv does not stop on that error, so every secret was left unset
with one unclear line as the only signal.

Rejected: an opt-out for the warning. The file cannot know whether this machine is meant
to have the loader, and an opt-out would go quiet on exactly the machine where a missing
loader is a real regression.
