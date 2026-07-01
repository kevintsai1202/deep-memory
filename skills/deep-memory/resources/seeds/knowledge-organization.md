# Knowledge Organization & Classification Architecture

> **Source integration:** Zettelkasten knowledge management method, Personal Knowledge Management (PKM) best practices, LangChain Memory Module design, Notion knowledge-base architecture principles

---

## 🔧 Atomic Note Principle (Zettelkasten-inspired)

**Date:** 2025-01-01 (seed knowledge)
**Context:** Core guiding principle when designing the knowledge-base entry format
**Best Practices:**
- **One entry, one concept**: Each `## 🔧` entry records only a single, complete, self-contained piece of knowledge
- **No dependence on context**: An entry must be readable on its own and should not assume the reader remembers other entries
- **Title as summary**: The title should let someone roughly understand the content without reading the body
- **Concrete beats abstract**: "FastAPI uses `@app.middleware` to add request logging" is better than "middleware can be used for logging"

---

## 🔧 Category Granularity Design

**Date:** 2025-01-01 (seed knowledge)
**Context:** Deciding how many categories to create in the knowledge-base and how much scope each category should cover
**Best Practices:**

| Granularity | Suitable Scenario | Problem |
|---|---|---|
| **Too fine** (one MD file per technology) | Highly specialized knowledge | Too many files; keyword indexing breaks down |
| **Just right** (one MD file per domain) | General-purpose scenarios ✅ | - |
| **Too coarse** (one MD file for everything) | Small amounts of knowledge | Files become bloated; RAG semantic dilution |

**Recommended granularity:**
- A single MD file with **10-50 entries** is optimal (roughly 10-50 KB)
- Over 50 KB → consider splitting into sub-categories
- Fewer than 5 entries → consider merging into a neighboring category

---

## 🔧 Index Keyword Design Principles

**Date:** 2025-01-01 (seed knowledge)
**Context:** Designing the `keywords` field for each category in `_index.json`
**Best Practices:**
- **8-15 keywords** is optimal (too few gives insufficient coverage; too many loses discriminative power)
- **Include synonyms and English variants**: e.g. `["後端", "backend", "server", "API", "後端開發"]`
- **Include technology names**: frameworks, languages, tool names (e.g. `FastAPI`, `Flask`, `Django`)
- **Avoid overly generic terms**: words like "system," "development," or "program" are too broad to serve as category keywords
- **Include common verbs**: e.g. "deploy," "install," "configure," "debug" paired with domain nouns

---

## 🔧 Category Naming Convention

**Date:** 2025-01-01 (seed knowledge)
**Context:** Keeping category names clear and consistent across time and across users
**Best Practices:**

| Naming Pattern | Example | Description |
|---|---|---|
| `[domain]-[type]` | `backend-dev.md` | Backend development category |
| `[tool]-[purpose]` | `docker-deploy.md` | Docker deployment related |
| `[problem-type]` | `debugging-tips.md` | Collection of debugging techniques |
| `[workflow]` | `code-review-process.md` | Process category |

**Names to avoid:**
- `misc.md`, `others.md` (not identifiable)
- `notes.md` (too generic)
- Using non-English paths (poor cross-system compatibility)

---

## 🔧 Example knowledge-base Directory Structure

**Date:** 2025-01-01 (seed knowledge)
**Context:** Reference template for setting up the knowledge-base in a new project
**Best Practices:**

```
knowledge-base/
├── _index.json             # Category index (required)
├── backend-dev.md          # Backend development best practices
├── frontend-dev.md         # Frontend development best practices
├── devops-deploy.md        # Deployment & CI/CD
├── database.md             # Database design & queries
├── debugging-tips.md       # Debugging techniques
├── architecture.md         # System architecture decisions
└── workflow-preferences.md # User workflow preferences & style

experience/
├── _index.json             # Skill experience index (required)
├── skill-deep-memory.md   # Usage experience for deep-memory itself
└── skill-[other-skill].md

cold-notes/
└── raw.jsonl               # Raw cold-storage records
```
