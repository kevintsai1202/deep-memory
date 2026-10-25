---
name: chroma-hybrid-search
description: "Performs high-precision hybrid retrieval (BM25 + ChromaDB vector search + BGE-Reranker semantic re-ranking) over the local hot store (knowledge-base/, experience/) and cold store (cold-notes/raw.jsonl). Use this skill when high-accuracy code or solution retrieval is needed and AI hallucination must be minimized. Running for the first time in a directory will automatically trigger initialization. Typically invoked by deep-memory's knowledge-base retrieval step when keyword matching is insufficient, but can also be called directly for ad-hoc searches."
---

# Chroma Hybrid Search Skill

This skill provides local RAG retrieval by combining vector search (semantic) with BM25 keyword search (exact match), followed by Cross-Encoder re-ranking using the BGE-Reranker model. It searches both the hot store (`knowledge-base/*.md`, `experience/*.md`) and the cold store (`cold-notes/raw.jsonl`) — the document set it reads must match what `update_db.py` indexed, or vectorized cold-store entries would be dropped from results.

> **Cross-platform command convention** — in every command below, `<PY>` is the virtual-env Python:
>
> - **Windows (PowerShell):** `& "$HOME\.deep-memory\.venv\Scripts\python"`
> - **Linux / macOS:** `~/.deep-memory/.venv/bin/python`
>
> The venv lives at a fixed absolute path inside the global workspace (`~/.deep-memory/.venv`), next to the data — commands work from any project directory; no project-local `.venv` is assumed. On PowerShell keep the `&` call operator and `$HOME` (PowerShell does not expand `~` in arguments to native commands); in Git Bash on Windows the same venv is `~/.deep-memory/.venv/Scripts/python`.
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
# 1. Create virtual environment (if it doesn't exist) — fixed location in the global workspace, shared by all projects
#    Windows (PowerShell): python -m venv "$HOME\.deep-memory\.venv"
#    Linux / macOS:        python3 -m venv ~/.deep-memory/.venv

# 2. Install required packages (no activation needed — call the venv Python directly)
<PY> -m pip install -r skills/chroma-hybrid-search/requirements.txt

# 3. Initialize and build the local vector index database
<PY> skills/chroma-hybrid-search/scripts/update_db.py
```

> **Notes:**
> 1. Do NOT commit `.venv` or `chroma_hybrid_db/` to GitHub — both live under `~/.deep-memory` (outside any repo) and must be generated locally using the steps above.
> 2. Step 3 is only needed once, to create the index. After that `search.py` tops it up automatically before every query — see Index Maintenance below.

---

## 🔄 Index Maintenance

`search.py` tops up the index before every query, so **`update_db.py` is no longer part of the daily loop**. An indexing failure prints a warning and never blocks the search; `--no-auto-index` disables the behaviour.

Run `update_db.py` explicitly only to index new entries immediately rather than at the next search — e.g. right after a refinement pass. `update_db.py --rebuild` forces every entry to be re-embedded (~6 minutes for a 1,265-entry store); it is only for making vectors correspond exactly to the latest text format, never required for normal operation.

### Content-addressed IDs

Cold-store entries are keyed by a semantic hash — `cold-notes#<hash12>` — not by line number. The hash covers `topic` / `content` / `tags` / `skill` / `memory_type` and deliberately excludes `date` / `time` / `project` / `quality` and every template string. So:

- Deleting or inserting a line anywhere in `raw.jsonl` does **not** re-embed the entries after it. Measured on a 928-line store: removing the middle line re-embedded **0** entries — the 463 entries after it only had their `line` metadata refreshed, at zero embedding cost.
- Marking an entry `quality: reviewed` during refinement does **not** trigger a re-embed. It removes the entry from the index instead: `read_cold_notes()` skips `reviewed` entries by default (their content already lives in the hot store), so the next sync deletes their vectors at zero embedding cost. `read_cold_notes(..., include_reviewed=True)` still reads the full history.
- Changing how the indexed text is assembled does **not** invalidate the store.

The line number survives in metadata as `line` and is returned with every cold-store hit, so results stay traceable to their source line.

### Run log

Each indexing run appends one JSON line to `~/.deep-memory/logs/index-runs.jsonl` with per-stage timings and why entries were re-embedded:

| Reason | Meaning |
|---|---|
| `new` | Genuinely a new entry — normal |
| `text-changed` | The entry's content actually changed |
| `id-missing` | An old ID vanished as a new one appeared — the signature of an ID shift or a version mismatch |

A run that re-embeds more than 20% of the store prints a warning with the reason breakdown. When the reason is `id-missing`, the usual cause is that the repo copy and the `~/.claude/skills` installed copy of `kb_reader.py` are on different versions — they assemble the indexed text differently, so each sees the other's entries as changed.

---

## 🔍 Usage

The Agent can execute `scripts/search.py` directly from the terminal to perform precise retrieval over the knowledge base and experience store.

### 1. Hybrid Search + Semantic Re-ranking (Default — Recommended)
Best for complex questions requiring deep semantic understanding:
```bash
<PY> skills/chroma-hybrid-search/scripts/search.py --query "spring animation tuning guidelines" --min-score 0.35
```

Always pass `--min-score 0.35` in this mode — besides filtering noise, it lets the project-first pass fall back to the full cross-project store when the current project only has low-relevance hits (without it, a near-zero-score project hit still counts as "found something" and blocks the fallback).

`--rerank-max-chars` (default `800`, `0` = no truncation) caps how much of each candidate is sent to the re-ranker; returned `text` is always the full entry. Keep the default: 400 pushed 18 of 60 measured candidates across the 0.35 threshold.

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

### 4. Scoped to One Skill, Tag, or Memory Type
Cold-store entries carry `skill`, `tags`, and `memory_type` metadata; `experience/skill-[skill-id].md` files carry `skill` derived from their filename, and hot-store entries are tagged as `knowledge` or `experience` by source folder. Use `--skill` for an exact skill-id match, `--tag` to check the tags array (Chroma's native `$contains`, requires chromadb ≥1.5.0 — already pinned in requirements.txt), and `--memory-type` to separate general knowledge from skill/tool experience. These filters narrow the BM25 corpus and the vector `where` clause identically, so every retrieval method sees the same filtered candidate set:
```bash
<PY> skills/chroma-hybrid-search/scripts/search.py --query "session timeout" --skill backend-dev
<PY> skills/chroma-hybrid-search/scripts/search.py --query "config drift"     --tag redis
<PY> skills/chroma-hybrid-search/scripts/search.py --query "agent browser fill fallback" --memory-type experience
```
Plain `knowledge-base/*.md` category files have no single skill/tag (they mix many topics), so they're never included by `--skill` or `--tag` filters unless the indexed entry explicitly has that metadata; they do participate in `--memory-type knowledge`.

### 5. Optional Cloud Re-ranker (TypeSafe Jev)
The default re-ranker is local `bge-reranker-base`. You can switch to TypeSafe Jev, which scores each candidate with a yes/no question in about 1.6 s per query, compared with 10–17 s plus ~9 s model load locally. In a 26-query, 149-label evaluation, Jev scored P@1 0.85 vs 0.77 and P@3 0.67 vs 0.58. **Trade-off:** the query and the first `--rerank-max-chars` characters of each candidate (≤10 per pass) are sent to `api.typesafe.ai`. Leave it off for stores that must not leave the machine.

| Setting | Where | Notes |
|---|---|---|
| Provider | `<workspace>/config.json` → `{"reranker": {"provider": "jev", "jev": {"model": "jev-1.14.0", "min_score": 0.6, "timeout": 10, "concurrency": 10}}}` | Persistent; `jev` keys are optional (those are the defaults) |
| Per-shell override | `DEEP_MEMORY_RERANKER=local\|jev` | Beats the config file |
| Per-call override | `--reranker local\|jev` | Beats everything |
| API key | User environment variable `TYPESAFE_API_KEY` (or `JEV_API_KEY`), or a `<workspace>/.env` line | Never in `config.json` or the repo; `.env` and `config.json` sit outside the backup scope |

- With Jev active, the threshold is `jev.min_score` (default **0.6**, the evaluated equivalent of bge's 0.35); `--min-score` applies to the local re-ranker only. Keep passing `--min-score 0.35`; it is what a fallback uses.
- Any Jev failure (missing key, timeout, HTTP error) logs a warning to stderr and re-scores that pass locally; later passes in the same query skip Jev. Search never fails because of Jev.
- The model version is pinned (`jev-1.14.0`) because the `jev-latest` alias moves without notice and the threshold was measured on 1.14.0. Re-run `scripts/bench/jev_rerank_eval.py` before changing the model or question.

---

## 📋 Output Format

The script outputs `{"scope": ..., "reranker": ..., "results": [...]}`. `reranker` tells you which scale `rerank_score` is on: `jev`, `local`, `local (jev fallback: <reason>)`, or `none` (non-rerank modes). `path` for hot-store hits is `file.md#entry-slug` — one `## 🔧` entry, not the whole file (see "Entry-Level Chunking" below). **Use the `text` field directly as context — do not separately open the file at `path` with the Read tool.** Stripping the `#entry-slug` and reading the whole source file re-introduces exactly the whole-file dilution chunking exists to avoid; `path` is for citation (telling the user where a fact came from), not an instruction to read further.

```json
{
  "scope": "project:deep-memory",
  "reranker": "jev",
  "results": [
    {
      "path": "experience/skill-remotion-best-practices.md#fps-30-causes-av-desync",
      "rerank_score": 0.9402,
      "text": "## 🔧 FPS 30 causes A/V desync\n**Date:** ...\n(just this one entry, not the rest of the file)"
    }
  ]
}
```

## ✂️ Entry-Level Chunking
`knowledge-base/*.md` and `experience/*.md` are indexed per `## 🔧` entry, not per file — `kb_reader.py`'s `split_entries()` splits on that heading and gives each entry a stable slug (from its title, de-duplicated within the file) so results point to the specific matching entry. A file with no `## 🔧` headings at all falls back to whole-file indexing for backward compatibility. `update_db.py` and `search.py` both import this splitting logic from the same `kb_reader.py` — if you ever need to change how entries are parsed, change it there once, not in both scripts.

## 🛡️ Anti-Hallucination RAG Routing Guide
- **Filename-first matching**: If the user's question explicitly mentions a filename, read that file's full content directly via Python `glob` or fast text search first.
- **Tiered RAG**: Prioritize filename matching; fall back to `search.py` hybrid search + rerank only if no match is found.
- **Score threshold filtering**: Do not pass any chunks or files with a Rerank Score below `0.35` to the model — this prevents noise from degrading response quality. Apply it search-side with `--min-score 0.35` rather than filtering the output afterwards: the search-side filter is also what allows the project-first pass to fall back to the cross-project store when project hits are all low-relevance.
