---
title: "[Project Name] — Architecture & Design Philosophy"
category: design
tags: [architecture, platform, design-philosophy]
status: active
source: <!-- claude-opus / human / etc. -->
---

# [Project Name] — Architecture & Design Philosophy

> One-line tagline · Core technologies

<!-- 2-3 sentences: what the system is, what problem it solves, who uses it. -->

---

## 1. Design Philosophy

<!-- Core principles that govern all architectural decisions. Reference the platform-wide
     philosophy in ~/projects/CLAUDE.md if this is an ai-core project. -->

### 1.1 [Principle Name]

<!-- Explain the principle and why it matters for this system. -->

### 1.2 [Principle Name]

<!-- Repeat as needed. -->

---

## 2. System Overview

<!-- High-level ASCII diagram showing the major layers/components and how they connect.
     Example structure: Interaction Surfaces → Service Layer → Data Layer → File Layer -->

```text
+------------------------------------------------------------------+
|  [SURFACE LAYER]                                                  |
|  CLI · Web UI · Agent APIs                                        |
+------------------------------------------------------------------+
|  [SERVICE LAYER]                                                  |
|  Core business logic, queries, mutations                          |
+------------------------------------------------------------------+
|  [DATA LAYER]                                                     |
|  Storage, caching, state                                          |
+------------------------------------------------------------------+
|  [FILE LAYER]                                                     |
|  Config, markdown docs, version-controlled content               |
+------------------------------------------------------------------+
```

---

## 3. Core Components

### 3.1 [Component Name]

**Responsibility:** <!-- What this component owns. One sentence. -->

**Key interfaces:**

- <!-- Entry points / public API -->

**Design notes:**

- <!-- Non-obvious constraints, invariants, or decisions. -->

### 3.2 [Component Name]

<!-- Repeat for each major component. -->

---

## 4. Data Model

<!-- Key data structures, schema, or storage layout. Use tables, code blocks, or diagrams. -->

### 4.1 [Entity / Table Name]

| Field | Type | Notes |
|-------|------|-------|
| | | |

---

## 5. Integration Points

<!-- How this system connects to other projects, services, or external APIs.
     Link to relevant design docs for each integration. -->

| System | Direction | Protocol | Notes |
|--------|-----------|----------|-------|
| | | | |

---

## 6. Key Design Decisions

<!-- Record architectural choices that are non-obvious or were contested.
     Link to full decision records in design docs (docs/designs/) if they exist. -->

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | | | |
| 2 | | | |

---

## 7. Operational Characteristics

### 7.1 Performance

<!-- Latency targets, throughput expectations, known bottlenecks. -->

### 7.2 Reliability

<!-- Failure modes, recovery behavior, graceful degradation. -->

### 7.3 Observability

<!-- Logging, metrics, alerting approach. -->

---

## 8. Open Questions

1. <!-- Unresolved architectural question -->
2. <!-- Unresolved architectural question -->

---

## Revision History

| Date | Change | Author |
|------|--------|--------|
| YYYY-MM-DD | Initial draft | |
