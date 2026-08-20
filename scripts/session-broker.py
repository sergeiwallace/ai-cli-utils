#!/usr/bin/env python3
"""Session broker — generates cross-project context for cc session startup.

Called by ai-lib.sh before launching Claude. Writes session-context.md
to .claude/signals/ with relevant cross-project intelligence.

Usage: python scripts/session-broker.py [--project PROJECT_NAME] [--config CONFIG_PATH]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path


def generate_session_context(config_path: Path | None = None, project: str | None = None) -> str:
    """Generate cross-project context for session startup.

    Returns markdown content for session-context.md.
    """
    try:
        from ai_core.config import load_config
        from ai_core.db import init_db, set_db_path, sync_all_project_roadmaps
        from ai_core.services.curation import get_curated_queue, run_curation
        from ai_core.services.intelligence import get_daily_digest
        from ai_core.services.memory import index_project_memories
        from ai_core.services.tasks import get_cross_project_priorities

        cfg = load_config(config_path)
        set_db_path(cfg.db_path)
        init_db()
        sync_all_project_roadmaps(cfg.projects, era=cfg.current_era)
        index_project_memories(cfg.projects)
    except Exception as e:
        return f"# Session Context\n\n> Failed to generate: {e}\n"

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    sections = [f"# Session Context\n\n> Generated: {now}\n> Project: {project or 'all'}\n"]

    # P0/P1 priorities across all projects
    try:
        priorities = get_cross_project_priorities("P1", include_done=False)
        if priorities.get("total", 0) > 0:
            sections.append("## P0/P1 Priorities\n")
            for proj_name, tasks in priorities.get("projects", {}).items():
                if tasks:
                    sections.append(f"### {proj_name}")
                    for t in tasks[:5]:
                        due = f" (due: {t.get('due_date')})" if t.get("due_date") else ""
                        display = t.get("display_id", "")
                        prefix = f"**{display}** " if display else ""
                        sections.append(f"- [{t['priority']}] {prefix}{t['title']}{due}")
                    sections.append("")
    except Exception:
        pass

    # Daily digest (top signals)
    try:
        digest = get_daily_digest(top_n=5, current_project=project)
        if digest:
            sections.append("## Top Signals\n")
            for item in digest:
                line = (
                    f"- **{item['signal_type']}** ({item['source_project']}): "
                    f"{item['content']} [score: {item['score']:.2f}]"
                )
                sections.append(line)
            sections.append("")
    except Exception:
        pass

    # Curated queue
    try:
        run_curation()
        queue = get_curated_queue(project=project)
        has_items = any(queue.get(cat) for cat in ("OVERDUE", "RECOMMENDED", "QUICK_WINS", "STALE", "NEWLY_GENERATED"))
        if has_items:
            sections.append("## Curated Queue\n")
            for cat in (
                "OVERDUE",
                "RECOMMENDED",
                "QUICK_WINS",
                "STALE",
                "NEWLY_GENERATED",
            ):
                items = queue.get(cat, [])
                if items:
                    sections.append(f"### {cat.replace('_', ' ').title()}")
                    for t in items[:5]:
                        priority = t.get("priority", "")
                        tag = f"[{priority}] " if priority else ""
                        sections.append(f"- {tag}{t['title']}")
                    sections.append("")
            snoozed = queue.get("SNOOZED", [])
            if snoozed:
                sections.append(f"### Snoozed ({len(snoozed)} items)\n")
    except Exception:
        pass

    # Priority Guidance
    try:
        from ai_core.services.guidance import (
            compute_priority_guidance,
            format_daily_focus,
        )

        guidance = compute_priority_guidance(project=project)
        focus_text = format_daily_focus(guidance)
        if focus_text:
            sections.append("## Priority Guidance\n")
            sections.append(focus_text)
            sections.append("")
    except Exception:
        pass

    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser(description="Generate session context for AI startup")
    parser.add_argument("--project", help="Current project name")
    parser.add_argument("--config", help="Path to platform config file")
    parser.add_argument("--engine", choices=["c", "g"], help="AI engine (c for Claude, g for Gemini)")
    parser.add_argument("--output", help="Output file path (default based on engine)")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None
    content = generate_session_context(config_path, args.project)

    if args.output:
        output = Path(args.output)
    elif args.engine == "g":
        output = Path(".gemini/signals/session-context.md")
    else:
        output = Path(".claude/signals/session-context.md")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    print(f"Session context written to {output}")


if __name__ == "__main__":
    main()
