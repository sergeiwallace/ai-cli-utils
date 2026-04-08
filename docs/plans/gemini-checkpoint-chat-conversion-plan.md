---
title: "Implementation Plan — Gemini Checkpoint-to-Chat Conversion"
category: plan
tags: [ai-cli, gemini, session-resume, checkpoint, chat-files]
status: approved
source: session-2026-04-08
---

# Implementation Plan — Gemini Checkpoint-to-Chat Conversion

## Table of Contents

- [Problem](#problem)
- [Background: Two Resume Systems](#background-two-resume-systems)
- [Options](#options)
- [Recommended Design](#recommended-design)
- [Implementation Steps](#implementation-steps)
- [Edge Cases](#edge-cases)
- [Test Plan](#test-plan)
- [Approval Log](#approval-log)

## Problem

Gemini CLI 0.36.0 introduced two parallel session-resume systems:

1. **Checkpoint system** (`/resume load {name}` / `/resume save {name}`): manual save/load via named JSON files in `~/.gemini/tmp/{name}/checkpoint-{name}.json`. Sessions loaded this way do **not** auto-save messages — the `ChatRecordingService` write guard returns early when `messages.length === 0` for a checkpoint-loaded session.

2. **Chat file system** (`gemini -r {uuid}`): auto-saves every message to `~/.gemini/tmp/{name}/chats/session-{timestamp}-{uuid8}.json`. Reliable, automatic, no manual save required.

`ai g {n}` currently injects `/resume load {name}` via `tmux send-keys` after a ~3s delay. This means:
- Sessions never write to `chats/` → no auto-save
- If the user exits without `/resume save`, all conversation since the last checkpoint is lost
- The restart loop falls back to `/resume load` even after long sessions with many turns

The result: the art-1 session lost 8 user turns (Apr 7 22:49 → Apr 8 06:18) because it was always loaded via checkpoint, never via `-r`.

## Background: Two Resume Systems

### Checkpoint format (`checkpoint-{name}.json`)
```json
{
  "history": [
    {"role": "user", "parts": [{"text": "..."}]},
    {"role": "model", "parts": [{"text": "..."}]}
  ],
  "authType": "oauth-personal"
}
```

### Chat file format (`chats/session-{ts}-{uuid8}.json`)
```json
{
  "sessionId": "uuid-v4",
  "projectHash": "sha256(projectRoot)",
  "startTime": "ISO8601",
  "lastUpdated": "ISO8601",
  "messages": [
    {
      "id": "uuid-v4",
      "timestamp": "ISO8601",
      "type": "user",
      "content": [{"text": "..."}]
    },
    {
      "id": "uuid-v4",
      "timestamp": "ISO8601",
      "type": "gemini",
      "content": [{"text": "..."}]
    }
  ],
  "kind": "main"
}
```

Key differences:
- `role: "model"` → `type: "gemini"`
- `parts[{text}]` → `content: [{text}]`
- Each message needs `id` (UUID v4) and `timestamp`
- File needs `sessionId`, `projectHash`, `startTime`, `lastUpdated`, `kind`
- `projectHash = sha256(projectRoot)` where `projectRoot` is read from `~/.gemini/tmp/{name}/.project_root`

---

## Options

### Option 1 — Convert checkpoint on-demand in `_find_latest_gemini_uuid` (recommended)

When `_find_latest_gemini_uuid(ai_name)` finds no chat files (or a stale checkpoint), auto-convert the checkpoint to a chat file and return the new UUID. The restart loop then uses `gemini -r {uuid}` instead of the injected `/resume load`.

**Pros:**
- Fully automatic — no user action needed
- Handles accidental exits and clean restarts identically
- Once converted, Gemini auto-saves all future messages natively
- Idempotent: stable UUID derived from checkpoint content hash prevents duplicate files
- Eliminates the ~3s delay + injected `/resume load`

**Cons:**
- Conversion happens at session start (adds ~100ms, negligible)
- If checkpoint is updated by a manual `/resume save` after a chat-file session, we need to detect and reconvert

> **Feedback:** Approved

---

### Option 2 — Standalone `ai internal checkpoint-to-chat` command only

Add a manual command but don't wire it into the restart loop.

**Pros:** Explicit, no magic
**Cons:** Requires user to remember to run it; doesn't fix accidental-exit scenario

---

### Option 3 — Patch Gemini CLI source to write chat files from checkpoint sessions

Would require maintaining a fork of gemini-cli.

**Cons:** Maintenance burden, breaks on every upstream update. Not recommended.

---

## Recommended Design

### Core function: `_convert_checkpoint_to_chat`

```python
def _convert_checkpoint_to_chat(ai_name: str, gemini_tmp: Path) -> str | None:
    """Convert a checkpoint JSON to a chat session file. Returns the new UUID."""
```

**Algorithm:**
1. Load `checkpoint-{ai_name}.json`
2. Read project root from `.project_root` file; compute `sha256(project_root)` for `projectHash`
3. Derive a stable `sessionId` from `sha256(checkpoint_content)[:8]` expanded to UUID format — same checkpoint always produces the same UUID, preventing duplicates
4. Check if a chat file with this UUID already exists in `chats/` and its mtime ≥ checkpoint mtime → skip (already converted and up to date)
5. Map history entries: `role: "model"` → `type: "gemini"`, `parts[0].text` → `content[0].text`
6. Assign fake monotonic timestamps anchored to checkpoint file mtime, spaced 1s apart
7. Write to `chats/session-{ts}-{uuid8}.json`
8. Set the new file's mtime to match the checkpoint's mtime (so ordering is correct relative to native chat files)
9. Return the `sessionId`

### Update `_find_latest_gemini_uuid`

```
def _find_latest_gemini_uuid(ai_name):
    gemini_tmp = ~/.gemini/tmp/{ai_name}
    chats_dir = gemini_tmp / "chats"

    # Find newest native chat file (by mtime — for initial ordering only)
    latest_chat = max(chats_dir.glob("session-*.json"), key=mtime, default=None)

    # Check if checkpoint exists and is newer than the last message in the chat file.
    # Compare checkpoint mtime vs chat file's last message timestamp — NOT vs chat file
    # mtime. Gemini-cli does not update the chat file's mtime on auto-save writes, so
    # mtime vs mtime comparison fails when /resume save is run mid-session:
    # the checkpoint would appear newer even though auto-save has more recent messages.
    checkpoint = gemini_tmp / f"checkpoint-{ai_name}.json"
    if checkpoint.exists():
        chk_mtime = checkpoint.stat().st_mtime
        chat_last_ts = _get_chat_last_message_timestamp(latest_chat) if latest_chat else 0.0
        if chk_mtime > chat_last_ts:
            # Checkpoint is newer — convert it
            uuid = _convert_checkpoint_to_chat(ai_name, gemini_tmp)
            if uuid:
                return uuid

    # Use latest native chat file
    if latest_chat:
        return _extract_uuid_from_chat_file(latest_chat)

    return None  # No checkpoint, no chat files → fresh session
```

**Key design note — mtime vs last message timestamp:**

The original design compared `checkpoint.mtime > chat_file.mtime`. This breaks because gemini-cli does not update the chat file's mtime on auto-save writes (observed in production: file content grew from 884KB to 899KB with no mtime change). The fix: compare `checkpoint.mtime` against the `timestamp` field of the last message in the chat file. This correctly handles the `/resume save` mid-session edge case:

| Scenario | checkpoint mtime | chat last msg ts | Result |
|---|---|---|---|
| Normal restart, no `/resume save` | 16:03 (old) | 20:02 (auto-saved) | chat wins → use it |
| `/resume save` then exit immediately | 20:10 | 20:08 (last auto-save) | checkpoint wins → reconvert |
| `/resume save` then keep talking | 20:10 | 20:15 (auto-saved after) | chat wins → use it |
| No chat file | any | 0.0 | convert checkpoint |

### Remove `/resume load` injection

Once `_find_latest_gemini_uuid` reliably returns a UUID, the Gemini engine script no longer needs to inject `/resume load {name}` via `tmux send-keys`. The `-r {uuid}` flag passed directly to `gemini` at launch handles it natively — no delay, no injected command.

### Checkpoint map (state tracking)

Track conversion state in `~/.local/state/ai-cli/gemini_checkpoint_map.json`:
```json
{
  "art-1": {
    "checkpoint_mtime": 1744043640.0,
    "converted_uuid": "abc12345-0000-0000-0000-000000000000"
  }
}
```

This allows idempotent re-conversion: if checkpoint mtime matches the stored value, skip.

---

## Implementation Steps

1. Add `_convert_checkpoint_to_chat(ai_name, gemini_tmp)` to `main.py`
2. Add `_extract_uuid_from_chat_file(path)` helper
3. Update `_find_latest_gemini_uuid` to call conversion before returning `None`
4. Update `get_engine_script` / Gemini restart loop: remove the `/resume load` injection; pass `-r {uuid}` as a CLI flag to `gemini` when a UUID is available
5. Convert all existing checkpoints on first run (one-time migration, non-destructive)
6. Add `ai internal checkpoint-to-chat {name}` as an explicit command for manual use
7. Update `docs/tools/ai-cli-usage.md` with new behavior description
8. Update inline comments in `main.py` for changed functions

---

## Edge Cases

| Scenario | Behavior |
|---|---|
| chats/ empty, checkpoint exists | Convert checkpoint → chat file, resume via `-r` |
| chat file exists, checkpoint mtime newer than chat last message ts | Reconvert checkpoint → new/updated chat file, resume via `-r` (checkpoint wins) |
| chat file exists, chat last message ts newer than checkpoint mtime | Use existing chat file directly |
| `/resume save` mid-session, then more auto-saves | Chat last message ts > checkpoint mtime → chat file wins (no data loss) |
| `/resume save` then immediate exit | Checkpoint mtime > chat last message ts → reconvert from checkpoint |
| No checkpoint, no chat files | Fresh session (current behavior) |
| Checkpoint exists, `.project_root` missing | Fall back to cwd for projectHash computation |
| Conversion fails (corrupt checkpoint) | Log warning, fall back to `/resume load` injection as before |
| Multiple checkpoints (different ai_names) | Each converted independently |
| Checkpoint mtime matches map entry | Skip conversion (idempotent) |

---

## Test Plan

- `test_convert_checkpoint_to_chat_when_checkpoint_exists_then_creates_chat_file`
- `test_convert_checkpoint_to_chat_when_called_twice_then_idempotent`
- `test_convert_checkpoint_to_chat_maps_model_role_to_gemini_type`
- `test_convert_checkpoint_to_chat_project_hash_matches_sha256_of_project_root`
- `test_find_latest_gemini_uuid_when_checkpoint_newer_than_chat_then_converts`
- `test_find_latest_gemini_uuid_when_chat_newer_than_checkpoint_then_uses_chat`
- `test_find_latest_gemini_uuid_when_no_files_then_returns_none`
- `test_find_latest_gemini_uuid_when_conversion_fails_then_returns_none`
- `test_find_latest_gemini_uuid_when_resume_save_mid_session_then_uses_chat_with_later_messages`
- `test_get_chat_last_message_timestamp_when_messages_exist_then_returns_last_timestamp`
- `test_get_chat_last_message_timestamp_when_empty_messages_then_returns_zero`
- `test_get_chat_last_message_timestamp_when_invalid_json_then_returns_zero`

---

## Approval Log

- 2026-04-08, Round 1: Plan approved by user. Proceed to implementation.
- 2026-04-08, Round 2: Design updated — mtime vs mtime comparison replaced with checkpoint mtime vs chat last message timestamp. Handles mid-session `/resume save` edge case where gemini-cli freezes chat file mtime on writes. Added `_get_chat_last_message_timestamp` helper.
