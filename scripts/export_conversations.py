#!/usr/bin/env python3
"""
Export Claude Code conversation JSONL files to human-readable markdown.

Usage:
    python3 scripts/export_conversations.py [--project PROJECT]

Output:
    docs/conversations/          — real conversations (5+ user turns)
    docs/conversations/stubs/    — short/stub conversations (< 5 user turns)

Run ad hoc when you want to review past conversations.
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
OUTPUT_DIR = Path(__file__).parent.parent / "docs" / "conversations"
STUB_THRESHOLD = 5  # fewer than this many user turns → stubs/


def decode_project_name(dir_name: str) -> str:
    """Extract project name from Claude's encoded project directory name.

    Handles both macOS (-Users-foo-projects-myapp) and Linux (-home-foo-projects-myapp)
    path encodings, plus worktree suffixes (--worktrees-sw-1).
    """
    # Strip leading path up to and including "projects-" (case-insensitive)
    name = re.sub(r"^.*-(?:P|p)rojects-", "", dir_name)
    # Strip worktree suffix --worktrees-*
    name = re.sub(r"--worktrees-.*$", "", name)
    return name or dir_name


def safe_filename(s: str) -> str:
    """Make a string safe for use in a filename."""
    return re.sub(r"[^\w\-]", "-", s).strip("-")


def extract_text(content) -> str:
    """Extract plain text from a message content field (str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    # Compact summary
                    summary_parts = []
                    for key in (
                        "command",
                        "file_path",
                        "path",
                        "pattern",
                        "query",
                        "prompt",
                    ):
                        if key in inp:
                            val = str(inp[key])
                            if len(val) > 80:
                                val = val[:77] + "..."
                            summary_parts.append(val)
                            break
                    summary = f" → {summary_parts[0]}" if summary_parts else ""
                    parts.append(f"> `[Tool: {name}{summary}]`")
                elif block.get("type") == "tool_result":
                    # Skip tool results — they're noise in export
                    pass
                # Skip: thinking, image, etc.
        return "\n".join(parts)
    return ""


def parse_jsonl(path: Path) -> dict:
    """Parse a JSONL file into a structured conversation dict."""
    records = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return None

    title = None
    session_id = None
    turns = []
    first_ts = None

    for rec in records:
        rtype = rec.get("type")

        if rtype == "custom-title":
            title = rec.get("customTitle")
            session_id = rec.get("sessionId")

        elif rtype == "user":
            ts = rec.get("timestamp")
            if ts and first_ts is None:
                first_ts = ts
            content = rec.get("message", {}).get("content", "")
            text = extract_text(content)
            if text.strip():
                turns.append({"role": "user", "text": text, "ts": ts})

        elif rtype == "assistant":
            ts = rec.get("timestamp")
            if ts and first_ts is None:
                first_ts = ts
            content = rec.get("message", {}).get("content", [])
            text = extract_text(content)
            if text.strip():
                turns.append({"role": "assistant", "text": text, "ts": ts})

    return {
        "title": title,
        "session_id": session_id or path.stem,
        "first_ts": first_ts,
        "turns": turns,
        "path": path,
    }


def render_markdown(conv: dict, project: str) -> str:
    """Render a conversation dict to markdown."""
    title = conv["title"] or "untitled"
    session_id = conv["session_id"]
    first_ts = conv["first_ts"] or ""

    lines = [
        f"# {title}",
        "",
        f"**Project:** {project}  ",
        f"**Session:** {session_id}  ",
        f"**Date:** {first_ts}  ",
        f"**Turns:** {len(conv['turns'])}  ",
        "",
        "---",
        "",
    ]

    for turn in conv["turns"]:
        role = turn["role"]
        ts = turn["ts"] or ""
        text = turn["text"]

        if role == "user":
            lines.append("## User  ")
            if ts:
                lines.append(f"*{ts}*  ")
        else:
            lines.append("## Claude  ")
            if ts:
                lines.append(f"*{ts}*  ")

        lines.append("")
        lines.append(text)
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _output_subdir_and_stem(conv: dict, project: str, base_dir: Path) -> tuple[Path, str]:
    """Return (subdir, base_stem) for a conversation — shared by output_path and find_existing_output."""
    title = conv["title"] or "untitled"
    first_ts = conv["first_ts"] or ""
    user_turns = sum(1 for t in conv["turns"] if t["role"] == "user")
    is_stub = user_turns < STUB_THRESHOLD

    try:
        dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        date_str = dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        date_str = "0000-00-00"

    stem = f"{date_str}_{safe_filename(project)}_{safe_filename(title)}"
    subdir = base_dir / "stubs" if is_stub else base_dir
    return subdir, stem


def find_existing_output(conv: dict, project: str, base_dir: Path) -> Path | None:
    """Return the existing output file for this session if one exists, else None."""
    subdir, stem = _output_subdir_and_stem(conv, project, base_dir)
    session_suffix = conv["session_id"][:8]
    for candidate in [subdir / f"{stem}.md", subdir / f"{stem}_{session_suffix}.md"]:
        if candidate.exists():
            return candidate
    return None


def output_path(conv: dict, project: str, base_dir: Path) -> Path:
    """Compute output file path, reusing existing file if present."""
    existing = find_existing_output(conv, project, base_dir)
    if existing:
        return existing

    subdir, stem = _output_subdir_and_stem(conv, project, base_dir)
    # No existing file — check if base name is taken by a different session
    base_candidate = subdir / f"{stem}.md"
    if base_candidate.exists():
        # Collision with a different session — use session ID suffix
        return subdir / f"{stem}_{conv['session_id'][:8]}.md"
    return base_candidate


def main():
    parser = argparse.ArgumentParser(description="Export Claude conversations to markdown")
    parser.add_argument("--project", help="Filter by project name (e.g. myproject)")
    parser.add_argument("--force", action="store_true", help="Re-export all files, ignoring mtime")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "stubs").mkdir(exist_ok=True)

    project_dirs = sorted(CLAUDE_PROJECTS_DIR.iterdir())
    if args.project:
        project_dirs = [d for d in project_dirs if args.project.lower() in decode_project_name(d.name).lower()]

    exported = 0
    skipped_empty = 0
    skipped_fresh = 0
    stubs = 0

    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue
        project = decode_project_name(project_dir.name)
        jsonl_files = sorted(project_dir.glob("*.jsonl"))

        for jsonl_path in jsonl_files:
            # Incremental: skip if output exists and is newer than source
            if not args.force:
                existing = find_existing_output(
                    {"title": None, "session_id": jsonl_path.stem, "first_ts": None, "turns": []}, project, OUTPUT_DIR
                )
                if existing and existing.stat().st_mtime >= jsonl_path.stat().st_mtime:
                    skipped_fresh += 1
                    continue

            conv = parse_jsonl(jsonl_path)
            if conv is None or not conv["turns"]:
                skipped_empty += 1
                continue

            # Re-check with full conv data (title/date may differ from stub check above)
            if not args.force:
                existing = find_existing_output(conv, project, OUTPUT_DIR)
                if existing and existing.stat().st_mtime >= jsonl_path.stat().st_mtime:
                    skipped_fresh += 1
                    continue

            out_path = output_path(conv, project, OUTPUT_DIR)
            user_turns = sum(1 for t in conv["turns"] if t["role"] == "user")
            is_stub = user_turns < STUB_THRESHOLD

            md = render_markdown(conv, project)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(md)

            if is_stub:
                stubs += 1
            else:
                exported += 1

            label = "stub" if is_stub else "conv"
            print(f"[{label}] {out_path.name}")

    print(
        f"\nDone: {exported} exported, {stubs} stubs, {skipped_fresh} skipped (up to date), {skipped_empty} skipped (empty)"
    )


if __name__ == "__main__":
    main()
