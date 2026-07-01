---
name: chroma-hybrid-search
description: "Performs high-precision hybrid retrieval (BM25 + ChromaDB vector search + BGE-Reranker semantic re-ranking) over the local hot store (knowledge-base/, experience/) and cold store (cold-notes/raw.jsonl). Use this skill when high-accuracy code or solution retrieval is needed and AI hallucination must be minimized. Running for the first time in a directory will automatically trigger initialization. Typically invoked by deep-memory's knowledge-base retrieval step when keyword matching is insufficient, but can also be called directly for ad-hoc searches."
---

# Chroma Hybrid Search Skill

This skill provides local RAG retrieval by combining vector search (semantic) with BM25 keyword search (exact match), followed by Cross-Encoder re-ranking using the BGE-Reranker model. It searches both the hot store (`knowledge-base/*.md`, `experience/*.md`) and the cold store (`cold-notes/raw.jsonl`) — the document set it reads must match what `update_db.py` indexed, or vectorized cold-store entries would be dropped from results.

> **Cross-platform command convention** — in every command below, `<PY>` is the virtual-env Python:
>
> - **Windows (PowerShell):** `.venv\Scripts\python`
> - **Linux / macOS:** `.venv/bin/python`
>
> > All `skills/...` paths assume the skill pack lives inside your current project (project-local install).
>
> **Workspace Storage Path Resolution:**
> By default, deep-memory uses the user's global directory `~/.deep-memory` (which resolves to `C:\Users\<username>\.deep-memory` on Windows) to store all knowledge bases, cold notes, and database files. This unifies memories across all your project workspaces.
> - If you want to use a specific directory, set the `DEEP_MEMORY_WORKSPACE` environment variable (e.g., `DEEP_MEMORY_WORKSPACE="."` or `DEEP_MEMORY_WORKSPACE="D:\my-memories"`).
> - You can also pass `--workspace <path>` to any script to override the workspace path for that specific command.

## ⚙️ First-Time Initialization (Bootstrap)

When this skill is first invoked by the Agent or user, confirm that the `.venv` virtual environment and the `chroma_hybrid_db` vector database have been created and all required packages are installed. If not, execute the following steps:

```bash
# 1. Create virtual environment (if it doesn't exist)
python -m venv .venv

# 2. Install required packages (no activation needed — call the venv Python directly)
#    Windows:        .venv\Scripts\python -m pip install -r skills/chroma-hybrid-search/requirements.txt
#    Linux / macOS:  .venv/bin/python   -m pip install -r skills/chroma-hybrid-search/requirements.txt
<PY> -m pip install -r skills/chroma-hybrid-search/requirements.txt

# 3. Initialize and build the local vector index database
<PY> skills/chroma-hybrid-search/scripts/update_db.py
```

> **Notes:**
> 1. Do NOT commit `.venv` or `chroma_hybrid_db/` to GitHub — they must be compiled and generated locally using the steps above.
> 2. Whenever `knowledge-base/` or `experience/` Markdown content is updated, re-run Step 4 to rebuild the vector index.

---

## 🔍 Usage

The Agent can execute `scripts/search.py` directly from the terminal to perform precise retrieval over the knowledge base and experience store.

### 1. Hybrid Search + Semantic Re-ranking (Default — Recommended)
Best for complex questions requiring deep semantic understanding:
```bash
<PY> skills/chroma-hybrid-search/scripts/search.py --query "spring animation tuning guidelines"
```

### 2. Vector Similarity Search Only
Best for cross-language or fuzzy semantic search:
```bash
<PY> skills/chroma-hybrid-search/scripts/search.py --query "Spring Boot 404" --mode vector
```

### 3. BM25 Keyword Search Only
Best for finding exact proper nouns, variable names, or code snippets:
```bash
<PY> skills/chroma-hybrid-search/scripts/search.py --query "pxmin pxmax" --mode bm25
```

### 4. Scoped to One Skill or Tag
Cold-store entries carry `skill` and `tags` metadata; `experience/skill-[skill-id].md` files carry `skill` derived from their filename. Use `--skill` for an exact skill-id match, `--tag` to check the tags array (Chroma's native `$contains`, requires chromadb ≥1.5.0 — already pinned in requirements.txt). Both narrow the BM25 corpus and the vector `where` clause identically, so every retrieval method sees the same filtered candidate set:
```bash
<PY> skills/chroma-hybrid-search/scripts/search.py --query "session timeout" --skill backend-dev
<PY> skills/chroma-hybrid-search/scripts/search.py --query "config drift"     --tag redis
```
Plain `knowledge-base/*.md` category files have no single skill/tag (they mix many topics), so they're never excluded by these filters — only `experience/*.md` and cold-store entries carry this metadata.

---

## 📋 Output Format

The script outputs a standard JSON array. `path` for hot-store hits is `file.md#entry-slug` — one `## 🔧` entry, not the whole file (see "Entry-Level Chunking" below). **Use the `text` field directly as context — do not separately open the file at `path` with the Read tool.** Stripping the `#entry-slug` and reading the whole source file re-introduces exactly the whole-file dilution chunking exists to avoid; `path` is for citation (telling the user where a fact came from), not an instruction to read further.

```json
[
  {
    "path": "experience/skill-remotion-best-practices.md#fps-30-causes-av-desync",
    "rerank_score": 0.9402,
    "text": "## 🔧 FPS 30 causes A/V desync\n**Date:** ...\n(just this one entry, not the rest of the file)"
  }
]
```

## ✂️ Entry-Level Chunking
`knowledge-base/*.md` and `experience/*.md` are indexed per `## 🔧` entry, not per file — `kb_reader.py`'s `split_entries()` splits on that heading and gives each entry a stable slug (from its title, de-duplicated within the file) so results point to the specific matching entry. A file with no `## 🔧` headings at all falls back to whole-file indexing for backward compatibility. `update_db.py` and `search.py` both import this splitting logic from the same `kb_reader.py` — if you ever need to change how entries are parsed, change it there once, not in both scripts.

## 🛡️ Anti-Hallucination RAG Routing Guide
- **Filename-first matching**: If the user's question explicitly mentions a filename, read that file's full content directly via Python `glob` or fast text search first.
- **Tiered RAG**: Prioritize filename matching; fall back to `search.py` hybrid search + rerank only if no match is found.
- **Score threshold filtering**: Do not pass any chunks or files with a Rerank Score below `0.35` to the model — this prevents noise from degrading response quality.
