---
title: Codex managed skill recovery prerequisites
category: analysis
tags: [codex, skills, sessions]
status: complete
template_version: analysis-1.0.0
---

# Codex managed skill recovery prerequisites

<!-- doc:region name="analysis" kind="replaceable" -->

Numbered Codex sessions currently hand the terminal directly to the client. The
launcher records the exact conversation through a native `SessionStart` hook.
Its control bridge exposes the confirmed thread identity. See
[`codex_session.py`](../../src/ai_cli/codex_session.py) and
[`codex_control.py`](../../src/ai_cli/codex_control.py).

## Package verification

Launch-scoped `skills.config` entries can disable a standalone skill by its
canonical path. Applying them safely requires an independently supplied package
fingerprint, the selected skill paths, and confirmation that the matching plugin
is enabled and discovered in the launch workspace. The package provider currently
builds a local archive and can stage a marketplace. Its discovery verifier takes
an in-process build receipt. There is no launcher handoff for that receipt today.
The launcher should consume a read-only verification result and leave plugin
installation, enablement, and shared skill links to their existing owners.

## Recovery and version coverage

The native Codex 0.171.0 fixture demonstrates that the same terminal UI can
reconnect to a fresh backend on the same conversation after a finished turn.
That fixture disables approvals. Safe automatic recovery needs a way to stop
accepting new turns while existing work and approval requests finish, then
resume the confirmed conversation with the original preferences. An idle
status check alone leaves a window in which the client can send a new turn.

The existing `PROVEN_VERSIONS` set describes SessionStart hook evidence. Use it
as a prerequisite for the managed route and require recovery evidence for the
selected client. Keep the ordinary launch available until both package
verification and safe recovery are established.

<!-- /doc:region name="analysis" -->
