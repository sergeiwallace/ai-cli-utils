---
title: Airflow Watcher — AI Session Pattern for Monitoring Airflow Pipelines
category: procedures
tags: [airflow, watcher, ai-sessions, background-process, humanware]
status: active
source: session-config
---

# Airflow Watcher — AI Session Pattern

Pattern for AI sessions (Claude Code, Gemini CLI) to trigger Airflow DAG runs and get automatically notified on completion, success, or failure — without any in-conversation polling. Zero tokens consumed while the watcher sleeps.

---

## Why

Running Airflow tasks from an AI session naively (e.g. `ssh hetzner 'airflow tasks run ...'` as a foreground or background Bash command) is fragile:

- **SSH-dependent execution**: the Airflow task runs as a child of the SSH session. If the AI session dies, compacts, or restarts, the SSH session drops and the task gets `SIGTERM`.
- **No notification**: there is no automatic signal back to the AI session when the run completes — either the AI polls (burns tokens) or silently forgets.
- **Fragile restart recovery**: if the AI session restarts, it has no way to know a pipeline was in-flight.

The watcher pattern solves all three:

1. **Scheduler-owned execution** — use `airflow dags trigger` so the scheduler (a persistent systemd service) owns the run. The task keeps running even if the AI session dies.
2. **Background watcher** — a lightweight polling script runs as an AI background process; when the pipeline finishes, the background tool-call completes and the AI is notified with the exit code.
3. **Restart recovery via hooks** — a `UserPromptSubmit` hook detects stale watcher PID files on session startup and forces re-launch via a `PostToolUse` hook sentinel.

---

## Architecture

```text
┌───────────────────────┐   dags trigger   ┌────────────────────────┐
│ AI session (Mac/CC)   │ ───────────────> │ Airflow scheduler       │
│                       │                  │ (systemd, persistent)   │
│ airflow-trigger.sh    │ <────── run_id ──┤                         │
│      │                │                  │   • runs DAG via        │
│      │ sets sentinel  │                  │     LocalExecutor       │
│      │                │                  │   • totally independent │
│      ▼                │                  │     of AI session       │
│ airflow-watch.sh  ◄───┼─ poll every 60s ─┤                         │
│   (CC background)     │                  └────────────────────────┘
│      │                │
│      ▼                │
│  exit 0/1/2 ──────────┼──> AI reacts (next steps / diagnose / check UI)
└───────────────────────┘
```

**Key property**: the watcher is a pure notification layer. Killing the watcher does NOT affect the pipeline. The Airflow scheduler keeps running independently.

---

## Scripts

### `scripts/airflow-watch.sh`

Polling watcher. Runs as AI background process. Polls every 60s.

```bash
bash scripts/airflow-watch.sh <dag_id> <run_id> [task_id]
```

- `dag_id` — Airflow DAG ID (e.g. `citation_sweep`)
- `run_id` — DAG run ID (e.g. `manual__2026-04-23T05:37:30+00:00`)
- `task_id` — optional: watch a single task instead of the whole DAG run

**Exit codes:**

| Code | Meaning | AI should |
|------|---------|-----------|
| `0`  | Pipeline succeeded | proceed with next steps |
| `1`  | Pipeline failed    | diagnose → fix → re-trigger → re-launch watcher |
| `2`  | Timeout (12h)      | check Airflow UI manually |

**Files written** (all on the AI session's local machine):

- `/tmp/airflow_watch.pid` — written at startup, removed on clean exit
- `/tmp/airflow_watcher_needed` — cleared at startup (written by trigger script)

**Environment knobs:**

- `AIRFLOW_WATCH_INTERVAL` (default `60`) — poll interval in seconds
- `HETZNER_HOST` (default `sergei@178.104.70.139`) — SSH target
- `AIRFLOW_BIN` (default `/home/sergei/airflow-venv/bin/airflow`) — remote airflow binary
- `AI_CLI_HOST` — if `hetzner`, runs airflow locally; otherwise SSHes

### `scripts/airflow-trigger.sh`

Wraps `airflow dags trigger` and arms the watcher sentinel so the AI session is forced to launch the watcher immediately.

```bash
bash scripts/airflow-trigger.sh <dag_id> [--conf <json>] [--task-id <task>]
```

Prints the exact watcher command to launch as a background Bash tool call.

---

## Hooks

Two hooks enforce the pattern so a watcher can never be silently forgotten:

### `PostToolUse` — `airflow-watcher-required.sh`

Fires after every `Bash` tool call. Checks for `/tmp/airflow_watcher_needed`. If present, exits `2` (blocks further tool calls) and prints the required watcher command. Fast no-op when no sentinel exists.

### `UserPromptSubmit` — `airflow-watcher-resume.sh`

Fires on every new user prompt. Checks for `/tmp/airflow_watch.pid`. If present but the PID is dead (i.e., the AI session restarted while a watcher was running), recreates `/tmp/airflow_watcher_needed` — so the next tool call hits the `PostToolUse` block and the AI session is forced to re-launch.

---

## Standard workflow

### 1. Trigger a DAG

```bash
bash scripts/airflow-trigger.sh my_dag --conf '{"param": "value"}'
```

Output ends with the exact watcher command.

### 2. Launch the watcher as a background tool call

In CC/Gemini: call the Bash tool with `run_in_background=true` and the printed command:

```bash
bash scripts/airflow-watch.sh my_dag manual__2026-...
```

The `PostToolUse` hook lifts the block the instant this script starts (it deletes the sentinel on startup).

### 3. Keep working

The AI session is free to do other work. The watcher polls silently in the background. Zero conversation tokens burned during polling.

### 4. React to the notification

When the watcher exits, the background task-notification lands in the session:

| Exit | Next action |
|------|-------------|
| `0`  | Pipeline succeeded → proceed with next steps |
| `1`  | Pipeline failed → pull task logs, diagnose root cause, fix, re-trigger, launch new watcher |
| `2`  | 12h timeout → inspect Airflow UI; decide whether to wait longer, kill, or re-trigger |

### 5. Session restart mid-pipeline

If the AI session ends (crash, compaction, user `/clear`) while a pipeline is in-flight:

- The pipeline keeps running — Airflow scheduler owns it.
- The watcher process dies with the session, leaving `/tmp/airflow_watch.pid` behind.
- Next session start → `UserPromptSubmit` hook detects dead PID → recreates sentinel.
- First `Bash` tool call → `PostToolUse` hook blocks with the watcher command.
- AI re-launches the watcher; polling resumes where it left off (fresh poll loop, same run_id).

---

## Operational rules for AI sessions

These belong in the global session config (`~/projects/CLAUDE.md` and `~/projects/GEMINI.md`). Summary:

- **Never run `airflow tasks run` as a foreground or background Bash command.** It creates an SSH-dependent process that dies when the session dies. Always use `airflow dags trigger` (or `airflow tasks clear` to force a scheduler-owned re-run of a specific task in an existing DAG run).
- **After any DAG trigger, immediately launch `airflow-watch.sh` as a `run_in_background=true` Bash call.** The `PostToolUse` hook will block you until you do.
- **Use the project's `scripts/airflow-trigger.sh` wrapper** for triggers — it sets the sentinel automatically.
- **Do not kill the watcher to save resources.** It burns zero tokens while sleeping. Killing it only loses the notification; the pipeline continues.
- **On watcher exit-code 1 (failed)**: the protocol is diagnose → fix → re-trigger → re-launch watcher. Do not report "task failed" and move on without re-running.
- **Airflow UI**: available at the host's web UI port (default `8080` on Hetzner). Use it for rich log inspection and manual state manipulation.

---

## Troubleshooting

**Watcher exits `1` immediately with "cannot reach Airflow"** — scheduler is down. Check `systemctl --user status airflow-scheduler airflow-webserver` on Hetzner.

**Watcher loops with `state=unknown`** — run_id is wrong, or the DAG hasn't registered yet. Verify with `airflow dags list-runs --dag-id <dag>`.

**Sentinel keeps appearing after watcher runs** — the hook may be firing before the watcher removes the sentinel. Check the watcher startup: it should `rm -f /tmp/airflow_watcher_needed` in the first few lines.

**`PostToolUse` hook blocks even though watcher is running** — check `/tmp/airflow_watch.pid` exists and its `PID` is alive (`kill -0 $PID`). If stale, `rm /tmp/airflow_watch.pid /tmp/airflow_watcher_needed` and relaunch.
