# Cold Store, Vectorization & Refinement Rules

> super-memory/SKILL.md Step 5 already tells you, every turn, to write to the cold store by default — that decision doesn't require this file. Load this file on demand for the exact skip condition and write conventions below, or when the user asks about cold-notes / vectorization / refinement.

## Contents

- [Cold Store Write Rules](#cold-store-write-rules-when-to-write-to-cold-notes)
- [Cold Store Vectorization](#cold-store-vectorization-when-to-run-update_dbpy)
- [Reading the Cold Store: Through RAG, Not Directly](#reading-the-cold-store-through-rag-not-directly)
- [Memory Refinement Process (Cold → Hot Distillation)](#memory-refinement-process-cold--hot-distillation)
- [ChromaDB Vector Index Build & Rebuild Rules](#chromadb-vector-index-build--rebuild-rules)

## Cold Store Write Rules (When to Write to cold-notes)

**Cold store writes require no user confirmation, and are the default, not the exception.** A "refine later" pipeline only works if there's enough raw material to refine from — deciding at write-time whether a turn "seems important enough" pre-filters the very thing refinement is supposed to judge later, with the benefit of hindsight and volume. Write to the cold store at the end of every turn that exchanged any real content.

**Always write — this list is illustrative, not exhaustive:**

| Situation | Note |
|---|---|
| A conversation with substantive problem-solving ends | The clearest case — write even if the solution feels unpolished |
| User uses phrasing like "note this for later", "jot it down", "organize later" | Explicit staging intent |
| User declines a hot store write, but the turn still has content | "Never mind, don't save that" ≠ "nothing happened" — write to cold store instead of discarding entirely |
| Solution is still being explored with no definitive conclusion | Preserve in-progress notes rather than waiting for a clean ending |
| Any other turn where information was exchanged | Default behavior — don't withhold a write just because the turn doesn't look important in the moment |

**Skip only when:** the turn has nothing to record at all — a bare "thanks!" or "ok" with no other content exchanged.

**Cold store write command (text backup only — NOT yet searchable via RAG):**
```bash
<PY> skills/chroma-hybrid-search/scripts/write_cold.py \
  --topic "FastAPI session lost across requests" \
  --content "Root cause: default StatelessSession. Fix: use RedisSessionMiddleware. Steps: 1. pip install redis-py 2. Set SESSION_SECRET_KEY" \
  --tags "fastapi,session,redis,middleware" \
  --skill "backend-dev"
```

> ⚠️ **Important distinction: JSONL write ≠ vectorization**
> - `write_cold.py` only appends the entry to `cold-notes/raw.jsonl` (text backup). **RAG cannot search this data yet.**
> - You must separately run `update_db.py` to vectorize the cold store and make it RAG-searchable.

---

## Cold Store Vectorization (When to Run update_db.py)

`update_db.py` indexes both the hot store (MD files) and cold store (raw.jsonl). It has a non-trivial compute cost (re-vectorizes all documents), so **it should NOT be triggered after every write**. Use the following rules:

| When to Vectorize | Reason |
|---|---|
| **Hot store has new or modified content** (required) | RAG cannot find new hot store entries until the index is rebuilt |
| **Cold store was written this session AND a RAG query is about to occur** | Lazy strategy — vectorize just before querying, avoid unnecessary compute |
| **Cold store has accumulated ≥ 5 new entries** before the first query | Batch vectorization to reduce trigger frequency |
| **Before a manual refinement or backup is requested** | Ensure the index and backup contents are consistent |

**Situations where update_db.py is NOT needed:**
- Cold store JSONL was written, but no RAG query is needed this session
- Running backup.py with no new knowledge written

**Vectorization command:**
```bash
<PY> skills/chroma-hybrid-search/scripts/update_db.py
```

**Agent reminder template (after cold store write, before next RAG query):**
> "I've written this session's notes to the cold store. To make this data searchable via RAG, please run the vectorization first:"
> ```bash
> <PY> skills/chroma-hybrid-search/scripts/update_db.py
> ```

---

## Reading the Cold Store: Through RAG, Not Directly

Once `update_db.py` has vectorized the cold store, retrieve its content through `search.py` (super-memory Step 4, Path 2) exactly like any other indexed document — do not read `cold-notes/raw.jsonl` directly as a retrieval technique. A direct read doesn't rank or filter by relevance to the current question, and it doesn't scale as the file grows; it duplicates what RAG already does correctly, now that search.py's document set covers the cold store too.

**The one exception is the refinement workflow below**, which reads `raw.jsonl` directly on purpose: it needs every entry's `tags`/`skill`/`date` fields to cluster them, and neither RAG's ranked-top-K-for-one-query model nor the vector index's metadata (which doesn't retain those fields) can support that.

If the cold store has entries written since the last `update_db.py` run, a RAG query simply won't find them yet — that's a staleness gap to close by running `update_db.py` (see above), not a reason to fall back to reading `raw.jsonl` directly.

---

## Memory Refinement Process (Cold → Hot Distillation)

### Trigger Conditions
**Proactively prompt the user to refine when any of the following apply:**

| Trigger | Reason |
|---|---|
| `cold-notes/raw.jsonl` has ≥ **20 entries** | Enough raw material has accumulated for meaningful distillation |
| A single `tags` or `skill` value appears ≥ **3 times** | High-frequency topic indicates a generalizable pattern worth promoting to the hot store |
| User explicitly says "refine the cold store" | Explicit refinement command |

### Refinement Workflow

1. **Read the cold store**: Agent reads all entries from `cold-notes/raw.jsonl`
2. **Cluster analysis**: Group by `tags` and `skill` fields to identify recurring themes
3. **Draft hot store entries**: For each high-frequency cluster, generate a structured MD draft:
   ```markdown
   ## 🔧 [Distilled pattern title]
   **Date:** YYYY-MM-DD (refinement date)
   **Context:** One sentence describing the applicable scenario
   **Best Practices:**
   - [Key takeaway distilled from multiple cold store entries]
   **Source cold store entries:** cold-notes/raw.jsonl#L3, L7, L12
   ```
4. **Ask for user confirmation**:
   > "I've organized [N] entries on [topic] from the cold store. Here's the distilled draft — shall I write this to the hot store?"
5. **Write to hot store** after confirmation — update the corresponding MD file and `_index.json`
6. **Mark cold store entries**: Tag refined entries with `"quality": "reviewed"` (do not delete — preserve history)

---

## ChromaDB Vector Index Build & Rebuild Rules

`chroma_hybrid_db/` is a binary vector index generated locally by `update_db.py` scanning the knowledge base. **Rebuilding is required in the following situations:**

### ① First Build (Required)
After completing skill installation and creating `knowledge-base/`, run the initial build:
```bash
<PY> skills/chroma-hybrid-search/scripts/update_db.py
```

### ② After Knowledge Base Changes (Trigger Conditions)
**When any of the following apply, the Agent should remind the user to rebuild after the current session:**

| Trigger | Reason |
|---|---|
| Any new `.md` category file is added | New file is not vectorized — RAG cannot find it |
| An existing `.md` file has more than **1 paragraph** added or modified | Old vectors become inconsistent with new content, degrading semantic accuracy |
| Any `.md` category file is deleted or renamed | Orphaned index entries remain in the vector store — RAG may point to non-existent chunks |
| `keywords` field in `_index.json` is updated | Doesn't directly affect vectors, but a rebuild is recommended to maintain consistency |

> **⚠️ Agent reminder template:**
> "I've written new content to `backend-dev.md`. Please run the following to update the local vector index so RAG can find the new record:"
> ```bash
> <PY> skills/chroma-hybrid-search/scripts/update_db.py
> ```

### ③ Situations Where Rebuilding Is NOT Needed
- Only formatting changes (blank lines, punctuation, indentation) — no substantive new content
- Only non-knowledge-base files modified (e.g., `README.md`, `SKILL.md`)
- Query-only session — no writes to `knowledge-base/` or `experience/`
