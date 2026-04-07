
---
name: next
description: What's next? Surface current priorities, autonomous batches, and unblocking reviews.
---

# next

Surface the most critical tasks, recommended autonomous work, and documents awaiting review to minimize context-switching fatigue and maintain project momentum.

**Usage:** `/next`

## Workflow

### 1. Compile Priorities
- Read `docs/roadmap/master-roadmap.md` for this project's open tasks.
- Sort strictly by priority level (`P0` > `P1` > `P2` > `P3`).
- Include `Due Date` and recent `Progress Notes` for each item.
- Do **not** query cross-project tasks — scope is this project only.

### 2. Identify Autonomous Batches
- Filter for tasks tagged with `@autonomous` (or `autonomous_eligible=True` in DB).
- Group 3-5 related tasks into a "Recommended Batch" based on shared project context or file overlap.
- Explain the rationale for why this batch is the most efficient next step for an AI agent.
- List all available autonomous tasks in a secondary list.

### 3. Surface Unblocking Reviews
- Scan `docs/` for documents in `pending_review` or `draft` status (check YAML frontmatter `status` field).
- List "Human Review Queue" items: docs or tasks that require user approval to unblock further implementation.

### 4. Output Format
Always present the information in a structured, high-signal table or list following the "Fleet Commander" template.

---

## Template

### **Human Review Queue (Unblocking)**

| Item | Type | Project | Status |
| :--- | :--- | :--- | :--- |
| `path/to/doc.md` | Design | project | `pending_review` |

### **Recommended Autonomous Batch**
> **Rationale:** These tasks all relate to [context] and can be completed without human intervention.
- [ ] `[ID]` **Task Title**
- [ ] `[ID]` **Task Title**

### **Strategic Roadmap**

| Group | Task | Priority | Due / Note |
| :--- | :--- | :--- | :--- |
| Group Name | `[ID]` **Title** | `P0` | **Date** / Progress |
