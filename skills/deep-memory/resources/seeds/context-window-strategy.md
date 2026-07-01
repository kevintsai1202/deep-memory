# Context Window Management Strategy

> **Source synthesis:** the MemGPT paper (2023), LangChain token-budget management, Anthropic's best practices for Claude system-prompt design, GPT-4 token-usage optimization guidance

---

## 🔧 Partitioning the Context Window

**Date:** 2025-01-01 (seed knowledge)
**Context:** How to allocate context space when designing a conversational system
**Best Practices:**

A typical context-space allocation (using a 200K-token window as an example):

| Segment | Recommended share | Purpose |
|---|---|---|
| System prompt + skill rules | ~5% | SKILL.md, conventions, persona setup |
| Injected knowledge-base content | ~15% | Hot-store MD or RAG results |
| Conversation history | ~40% | The full record of the current conversation |
| Reserved generation headroom | ~40% | The model's generated output |

**Key principle:** Knowledge injection should not consume more than 20% of the context — otherwise conversation history gets squeezed out, hurting coherence.

---

## 🔧 Compression Tactics for Knowledge Injection

**Date:** 2025-01-01 (seed knowledge)
**Context:** How to handle oversized hot-store MD files or injecting multiple categories at once
**Best Practices:**
- **Inject only the relevant section** rather than the entire MD file (applies once a single MD file exceeds 30KB)
- **Prefer RAG snippets over full text**: if the knowledge base is large overall, favor RAG snippets rather than reading the MD directly
- **Summaries instead of full text**: for entries longer than 500 words, you can inject just the heading plus the top 3 key points
- **Strip formatting symbols**: Markdown characters like `#`, `---`, `**` consume tokens on injection without contributing meaning

---

## 🔧 Conversation History Compression (Conversation Summarization)

**Date:** 2025-01-01 (seed knowledge)
**Context:** Strategy for handling conversation history that exceeds the context window in long conversations
**Best Practices:**

**MemGPT's memory-paging mechanism:**
1. When conversation history nears the context limit, trigger "summarization compression"
2. Compress earlier turns into 1-3 structured summaries and move them out of the context
3. Store the summaries in external storage (cold store), retrieving them via RAG when needed

**When to trigger compression (recommended):**
- The conversation has exceeded 30 turns and the earlier content is unrelated to the current topic
- The user explicitly says "okay, we're done with this topic, let's move on"
- A single turn's output is about to exceed 1000 tokens and more knowledge needs to be loaded

**When NOT to compress:**
- The problem currently being solved is not yet finished
- The conversation contains decisions that have not yet been confirmed

---

## 🔧 Token Budget Management (LangChain-inspired)

**Date:** 2025-01-01 (seed knowledge)
**Context:** Precisely controlling knowledge-injection token usage at the code level
**Best Practices:**

```python
# Conceptual example: estimate token usage before injection
MAX_KNOWLEDGE_TOKENS = 8000  # Inject at most 8K tokens of knowledge-base content

injected = []
total_tokens = 0
for chunk in rag_results:
    chunk_tokens = estimate_tokens(chunk["text"])
    if total_tokens + chunk_tokens > MAX_KNOWLEDGE_TOKENS:
        break
    injected.append(chunk["text"])
    total_tokens += chunk_tokens
```

**Token estimation rules of thumb (quick estimates):**
- English: on average, 1 token ≈ 4 characters
- Chinese: on average, 1 token ≈ 1.5-2 Chinese characters
- 1 KB of Chinese MD ≈ roughly 300-400 tokens

---

## 🔧 System Prompt Conciseness Principles (Anthropic's recommendations)

**Date:** 2025-01-01 (seed knowledge)
**Context:** How to keep skill rules complete yet context-efficient when designing SKILL.md
**Best Practices:**
- **Rules should be executable, not explanatory**: "If the category matches → read the full MD text" rather than "you should try to understand the user's need and attempt to match a category"
- **Use tables and lists**: they save 20-40% more tokens than prose paragraphs
- **Move examples out to a separate file**: keep only the rules in SKILL.md; store examples under `resources/`
- **Conditional loading**: move infrequently used rules into sub-documents, loaded only in specific situations
