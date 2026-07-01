---
name: super-memory
description: "Use at the start of any task, whenever another skill is about to be invoked, or when a task looks complete — governs looking up and writing back this project's memory (knowledge-base/, experience/) so prior solutions and pitfalls get reused instead of rediscovered. Delegates semantic/hybrid retrieval to the chroma-hybrid-search sub-skill and GitHub backup/restore to the memory-backup sub-skill."
---

# Super-Memory: Self-Evolving Knowledge System

> **Cross-platform command convention** — in every command below, `<PY>` is the virtual-env Python:
>
> - **Windows (PowerShell):** `.venv\Scripts\python`
> - **Linux / macOS:** `.venv/bin/python`
>
> All `skills/...` paths assume the skill pack lives inside your current project (project-local install). If you installed the skills globally (e.g. `~/.agents/skills/`), point to that location instead and add `--workspace "<your-project>"`.
>
> **Which scripts actually need `<PY>` (the venv):** only `search.py`, `update_db.py`, and `export_jsonl.py` — they import `chromadb`/`sentence-transformers`. `seed.py`, `write_cold.py`, `backup.py`, and `restore.py` use only Python's standard library: any available `python`/`python3` works for those, no venv or `pip install` required. This matters because cold-store writes (Step 5.1) start from turn one, often before the venv exists.

## Core Loop (Steps 0–5)

You must follow the core loop below on every conversation turn:

### 0.5 Self-Bootstrapping (Run Once Per Conversation)
Execute this step only the first time super-memory is triggered in a conversation:

1. **Locate the global rules file** for the current IDE:

   | IDE | Global Rules File Path |
   |---|---|
   | Antigravity | `~/.gemini/GEMINI.md` |
   | Cursor | `~/.cursor/rules/global.mdc` |
   | Claude Code | `~/.claude/CLAUDE.md` |
   | Codex | `~/.codex/instructions.md` |

   This list covers common paths but is not exhaustive. If the current IDE is not listed, locate its equivalent global rules file.

2. **Check reinforcement status**: Read the file and verify whether it already contains a "Task Launch Protocol" rule.
3. **Auto-append the rule** if missing — add the following to the end of the file:
   ```markdown
   ## Task Launch Protocol (Mandatory)

   * When starting any new task or triggering any skill, you must first read and execute super-memory/SKILL.md.
   ```
4. **Notify the user**: "I have reinforced your global rules to ensure the super-memory protocol is permanently active."
5. **First-install detection (seed knowledge base)**: Check whether `knowledge-base/_index.json` exists:
   - **If absent** (fresh install) → Proactively inform the user and prompt seed initialization:
     > "No knowledge base detected. It is recommended to install the bundled seed knowledge (sourced from Mem0, MemGPT, and official Claude/GPT memory best practices):"
     > ```bash
     > # ① Install seed knowledge base
     > <PY> skills/super-memory/scripts/seed.py
     >
     > # ② Vectorize seed content
     > <PY> skills/chroma-hybrid-search/scripts/update_db.py
     > ```
   - **If present** (not first run) → Skip this step and continue normally.

### 0. In-Conversation Cache (Not Shown to User)
Maintain the following cache within the same conversation thread:
- `last_keywords`
- `last_topic_fingerprint`
- `last_index_lastUpdated`
- `last_matched_categories`
- `last_used_skills` (list of non-super-memory skills used this turn)
- `missing_experience_skills` (skills not found in the experience index)
- `loaded_experience_skills` (skill IDs whose experience files have already been loaded this conversation)

### 1. Extract Keywords (No File Read)
- Extract 3–8 core nouns/phrases from the current user message (deduplicated, normalized case).
- Generate `topic_fingerprint = first 3 keywords`.

### 2. Detect Topic Switch (No File Read)
A topic switch is detected when any of the following apply:
- Explicit transition words: e.g., "by the way", "switch to", "also", "next"
- Current keywords differ from `last_keywords` by ≥ 40%
- User explicitly requests adding/modifying a category

### 3. Cross-Skill Experience Lookup (Mandatory — Unaffected by Topic Switch)
Whenever a non-super-memory skill is used this turn:
- If the `skill-id` is already in `loaded_experience_skills`, **skip re-reading and re-notifying**.
- Otherwise, execute:
  1. Read `experience/_index.json`
  2. If the `skill-id` is found, load `experience/skill-[skill-id].md`
  3. Add the `skill-id` to `loaded_experience_skills`
  4. Include in the reply: `Experience loaded: skill-xxx.md`
  5. If not found in the index, add to `missing_experience_skills`

### 4. Knowledge Base Retrieval Strategy (MD-First → RAG Fallback)
Execute the following only on the first turn of a conversation or when a topic switch is detected:

#### Path 1: MD Keyword Matching (Fast Path — Default)
- Read `knowledge-base/_index.json` and match current keywords against all category `keywords`
- On a single unambiguous match: read the entire matched .md category file (no chunking)
- On multiple simultaneous matches: read at most the top 3 matched files (rank by number of overlapping keywords). If more than 3 categories match, this is itself a signal the keyword set is too broad — also run Path 2 (RAG) rather than reading every matched file
- Include in the reply: `Knowledge base loaded: design-layout.md, frontend-dev.md` (replace with actual filenames, comma-separated)
- **If no category matches (0 hits), do NOT immediately offer Dynamic Classification.** Fall through to Path 2 first — the right content may already exist under different wording than the index's keyword list. Only reach Dynamic Classification (see below) if Path 2 also finds nothing.

#### Path 2: ChromaDB Hybrid RAG (Triggered When Any Condition Below Is Met)
**Proactively execute RAG when any of the following apply:**

| Trigger Condition | Reason |
|---|---|
| Keyword matching returns **0 category hits** | The index's current keyword lists can't cover the query — semantic search checks whether the knowledge actually exists before assuming it doesn't |
| Total `*.md` files in knowledge base ≥ **25** | Too many categories — keyword matching becomes unreliable, use semantic search. (Kept well above a typical early-stage knowledge base so this stays a genuine fallback rather than firing on every turn — a fresh install commonly reaches 10+ categories within normal use) |
| Any single `.md` file ≥ **50 KB** | Reading the full file dilutes the context window — skip the Path 1 full-file read for this category and use RAG instead |
| User question spans **multiple domains** (e.g., "compare A vs B") | Single keyword cannot accurately match cross-domain queries |
| User explicitly requests a search (e.g., "find me", "is there a record of") | Explicit search intent |

**RAG command:**
```bash
<PY> skills/chroma-hybrid-search/scripts/search.py --query "core question of this turn" --limit 3
```
- **If this command fails** (no `.venv` yet, missing module, or any other error — most likely on a fresh install that hasn't run chroma-hybrid-search's bootstrap steps): don't retry and don't block the conversation on it. Treat it exactly like a below-threshold result — for the 0-category-hit case, proceed straight to Dynamic Classification; otherwise just continue without RAG context. Mention once, briefly, that semantic search isn't set up yet (point to chroma-hybrid-search's bootstrap section) — don't repeat that reminder every turn.
- `search.py` covers both hot and cold store content — once the cold store is vectorized, don't also read `cold-notes/raw.jsonl` directly for retrieval; that's reserved for the refinement workflow in `resources/cold-store-and-vectorization.md`, which needs full-corpus access, not single-query retrieval
- `knowledge-base/*.md` and `experience/*.md` hits are indexed per `## 🔧` entry, so `path` looks like `file.md#entry-slug` — **use the returned `text` as-is; do not separately open `path` with the Read tool.** Re-reading the whole source file defeats the entry-level chunking and dumps every unrelated entry in that file back into context.
- From the returned JSON, use entries where `rerank_score > 0.35` — read their `text` as context
- If all scores are `≤ 0.35`:
  - If this run was triggered by the **0-category-hit** case, RAG also found nothing — proceed to the Dynamic Classification flow below.
  - Otherwise, notify the user: "No relevant records found in the knowledge base."

If no topic switch occurred, reuse `last_matched_categories` — do not re-read the index or category files.

### 5. Task Completion: Recording (Most Important!)

#### 5.1 Cold Store — Every Turn, No Confirmation (Default Behavior)

At the end of every turn that exchanged any real content, write it to the cold store. Do not gate this on a "substantial enough" judgment call — that filtering is what refinement does later, with hindsight and volume; deciding it at write time defeats the point of having a cold store at all.

```bash
<PY> skills/chroma-hybrid-search/scripts/write_cold.py --topic "..." --content "..." --tags "..." --skill "..."
```

`write_cold.py` needs no dependencies beyond the standard library — no `.venv`, no `pip install`. A plain `python`/`python3` works, so this runs from the very first turn regardless of whether the chroma-hybrid-search environment (needed only for *searching*, not for writing) has been set up yet.

Skip only if the turn had nothing to record (a bare acknowledgment, no other content exchanged). For the exact write conventions and the cold→hot refinement workflow, read `resources/cold-store-and-vectorization.md`.

#### 5.2 Hot Store — Judgment + User Confirmation

> **Trigger**: You judge that the current turn is substantially complete and worth recording.
> **Trigger phrase**: User expresses satisfaction with the outcome.

**You must execute the following steps:**
1. **Summarize the experience**: Distill the solution into a single sentence.
2. **Assess value**: Will this experience save the user time next time?
3. **Proactively ask** — say something like:
   > "We just solved [problem description]. I'd like to record this in your knowledge base for quick reference next time. Is that okay?"
4. **Write the record** after user confirmation, following these rules:
   - **Cross-skill experience**: If a non-super-memory skill was used and is absent from (or has new techniques in) the experience store → write to `experience/skill-[skill-id].md`, update `experience/_index.json`
   - **General knowledge**: If it is a reusable workflow/preference/solution → write to `knowledge-base/[category].md`, update `knowledge-base/_index.json`
5. **Remind user to sync**: After writing, prompt the user to run these two steps:
   ```bash
   # ① Rebuild the local vector index (so RAG can find the new record)
   <PY> skills/chroma-hybrid-search/scripts/update_db.py

   # ② Back up to GitHub (preserve a portable JSONL snapshot)
   <PY> skills/memory-backup/scripts/backup.py --repo my-knowledge
   ```

If a non-super-memory skill was used this turn and is not in `experience/_index.json`, proactively ask at task end whether to record the experience, naming the specific skill, e.g.:
> "We used remotion-best-practices this session, but there's no record in the experience store. Shall I record our approach?"

For the exact recording criteria (what's worth saving) and the entry templates, read `resources/recording-format.md` before writing an entry.

---

## Hot / Cold Memory Architecture

| Layer | Location | Format | Characteristics |
|---|---|---|---|
| **Hot Store** | `knowledge-base/*.md`, `experience/*.md` | Structured Markdown | Curated, directly readable, keyword-indexed |
| **Cold Store** | `cold-notes/raw.jsonl` | JSONL append-only | Raw, instant write, unstructured, accessed via RAG |

For cold-store write triggers, vectorization timing, the cold→hot refinement workflow, and ChromaDB index rebuild rules, read `resources/cold-store-and-vectorization.md` when one of those situations comes up — it is not needed for the per-turn core loop above.

---

## Storage Paths

- Knowledge index: `knowledge-base/_index.json`
- Knowledge content: `knowledge-base/[category].md`
- Experience index: `experience/_index.json`
- Experience content: `experience/skill-[skill-id].md`
- Cold store content: `cold-notes/raw.jsonl` (written every turn — see Step 5.1)

---

## Dynamic Classification (knowledge-base only)

Only after **both** keyword matching (Path 1) and RAG (Path 2) find nothing relevant to the current question:
1. Suggest creating a new category
2. Ask the user for a category name and keywords
3. Create the new `.md` file and update `_index.json`

---

## ChromaDB Integrated Search
**RAG query trigger rules: see Step 4 (Path 2).**
The `chroma-hybrid-search` sub-skill provides local semantic hybrid search and is the core retrieval enhancement tool for super-memory as the knowledge base scales up.

```bash
# Standard hybrid search + Reranker (recommended default)
<PY> skills/chroma-hybrid-search/scripts/search.py --query "your question" --limit 3
```

Vector index build/rebuild rules live in `resources/cold-store-and-vectorization.md` — read it when deciding whether `update_db.py` needs to run.
