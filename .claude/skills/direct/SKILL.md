---
name: direct
description: Run the next task using Claude + Gemini CLI directly (bypass orchestrator)
---

# direct

Bypass any orchestrator for this task. Use Claude's own reasoning + Gemini CLI MCP as a web-grounded second opinion.

**Usage:** `/direct <any instruction>`

## Instructions

- Do NOT use orchestrator tools or Claude Agent subprocesses for this task
- Use `mcp__gemini-cli__ask-gemini` with explicit `model` for web-grounded research or a second perspective
- Use `gemini-3-flash-preview` for reliability
- Use `gemini-3.1-pro-preview` only when deeper analysis is needed
- Synthesize Gemini's findings with Claude's own knowledge before presenting results
- Do not commit/push or create issues unless explicitly asked
