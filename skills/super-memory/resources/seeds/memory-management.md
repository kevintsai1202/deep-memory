# Core Principles of Memory Management

> **Source synthesis:** Mem0 memory framework, the MemGPT paper (2023), official Anthropic Claude memory guidance, OpenAI Memory Feature best practices

---

## 🔧 The Three Types of Memory (Mem0 Framework)

**Date:** 2025-01-01 (seed knowledge)
**Context:** Foundational classification for designing an AI memory system
**Best Practices:**

| Memory Type | Definition | Example |
|---|---|---|
| **Semantic Memory** | General facts and knowledge with no time marker | "Python's GIL limits multithreading" |
| **Episodic Memory** | A record of a specific event or conversation, with time and context | "In 2024-03, the user fixed a Redis session issue" |
| **Procedural Memory** | Operational skills, steps, and workflows | "The standard steps for deploying FastAPI" |

**Mapping to super-memory:**
- Semantic memory → general-purpose category MD files in `knowledge-base/`
- Episodic memory → raw records in the cold store, `cold-notes/raw.jsonl`
- Procedural memory → skill operating steps in `experience/skill-*.md`

---

## 🔧 The Principle of Selective Memory (Core Mem0 Design)

**Date:** 2025-01-01 (seed knowledge)
**Context:** Preventing unbounded growth of the memory store while maintaining precision
**Best Practices:**
- ✅ **Record only reusable knowledge**: ask "will this answer be used again next time?" — if not, don't record it
- ✅ **Deduplicate memories**: before adding a new entry, search for similar existing ones; if similarity exceeds 80%, merge instead of adding
- ✅ **Apply a decay mechanism**: entries not accessed for over 90 days get a lower indexing priority (marked stale)
- ❌ Avoid recording pure conceptual explanations (with no concrete operational steps)
- ❌ Avoid recording one-off, non-reproducible event details

---

## 🔧 MemGPT's Tiered Memory Architecture

**Date:** 2025-01-01 (seed knowledge)
**Context:** Understanding the industry-standard design for long-term AI memory management
**Best Practices:**

MemGPT (2023, Stanford) proposes a three-tier memory architecture:

```
┌──────────────────────────────────────┐
│  Main Context                         │  ← current conversation, fastest access
│  - system prompt + this turn          │
├──────────────────────────────────────┤
│  External Context                     │  ← can be actively searched and loaded
│  - knowledge base, docs, summaries    │
│    of past conversations              │
├──────────────────────────────────────┤
│  External Storage                     │  ← persistent, requires tool access
│  - vector database, structured DB     │
└──────────────────────────────────────┘
```

**Mapping to super-memory:**
- Main Context = the current conversation turn
- External Context = the hot store (`knowledge-base/*.md`, loaded after keyword matching)
- External Storage = the cold store (`cold-notes/raw.jsonl` + ChromaDB, retrieved via RAG)

---

## 🔧 Official Claude Memory Best Practices (Anthropic)

**Date:** 2025-01-01 (seed knowledge)
**Context:** Official recommendations for using memory features within the Anthropic ecosystem
**Best Practices:**
- **Minimization principle**: inject only the memories directly relevant to the current task, avoiding unnecessary context consumption
- **Structured injection**: memory content should be injected in a structured format (headings, lists) so the model can understand it easily
- **Time markers**: every memory entry should carry a date, so the model can judge whether the information is still valid
- **Source labeling**: mark whether a memory came from "user preference," "a past task," or "the knowledge base"
- **Conflict resolution**: when old and new memories conflict, explicitly tell the model to defer to the newer information

---

## 🔧 Design Takeaways from the OpenAI Memory Feature

**Date:** 2025-01-01 (seed knowledge)
**Context:** Lessons drawn from the design decisions behind OpenAI's ChatGPT memory feature
**Best Practices:**
- **Active vs. passive memory**: the system should proactively detect information worth recording, rather than waiting for the user to trigger it manually
- **User visibility**: users should be able to see "what was remembered," which builds trust
- **Clear memory boundaries**: memory entries should be stored as independent, atomic units rather than large blocks of text
- **Cross-conversation consistency**: memory should stay consistent across different conversation threads, avoiding memory "amnesia"

**How super-memory implements this:**
- Active memory → proactive prompt at the end of a task in Step 5
- User visibility → the reply explicitly states "I've read from the knowledge base: xxx.md"
- Atomic storage → one `## 🔧` heading per entry
- Cross-conversation consistency → persistent MD files in `knowledge-base/`
