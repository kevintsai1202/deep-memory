# Cold Store, Vectorization & Refinement Rules

> deep-memory/SKILL.md Step 5 already tells you, every turn, to write to the cold store by default — that decision doesn't require this file. Load this file on demand for the exact skip condition and write conventions below, or when the user asks about cold-notes / vectorization / refinement.

## Contents

- [Cold Store Write Rules](#cold-store-write-rules-when-to-write-to-cold-notes)
- [Cold Store Vectorization](#cold-store-vectorization-automatic-when-to-run-update_dbpy-by-hand)
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
  --skill "backend-dev" \
  --memory-type "experience"
```

> **JSONL write and vectorization are separate steps, but you no longer have to bridge them by hand.**
> `write_cold.py` only appends the entry to `cold-notes/raw.jsonl` (text backup) — at that instant RAG cannot find it yet. But `search.py` tops up the index before every query, so the next retrieval picks it up automatically. Run `update_db.py` yourself only to close that gap immediately rather than at the next search.
>
> **Global store, project-tagged entries:** `write_cold.py`, `update_db.py`, and `search.py` all default `--workspace` to `~/.deep-memory` — every project's memory lives in one shared home-directory store now, not scattered per-project. Each cold-store entry still records which project it came from via an auto-filled `project` field (derived from the calling directory's name), so `search.py` can search the current project first and only fall back to the full store when nothing project-specific matches. Entries written before this change have no `project` field and are reachable only through that fallback pass — they were intentionally left as-is, not backfilled.
>
> **Knowledge vs. experience routing:** new cold-store entries should also carry `memory_type` (`knowledge`, `experience`, or `both`). Use `knowledge` for reusable rules/SOPs/domain facts, `experience` for skill/tool/repo-specific pitfalls and verified workflows, and `both` when refinement should split one raw event into two hot-store records.

---

## Cold Store Vectorization (Automatic; When to Run update_db.py by Hand)

`update_db.py` indexes both the hot store (MD files) and cold store (raw.jsonl). Indexing is **incremental**: it only re-embeds entries that are new or whose content hash changed, applies metadata-only changes without re-embedding, and skips unchanged entries. Embedding runs on a lightweight ONNX path (`onnx_models.py`, no torch), and the model loads only when something actually needs re-embedding — a no-change run finishes in about 1–2 seconds.

**`search.py` runs this same incremental sync before every query**, so the index is never stale at retrieval time and you do not need to remind the user to vectorize. Pass `--no-auto-index` to disable it. An indexing failure prints a warning and never blocks the search.

| Run `update_db.py` by hand when | Reason |
|---|---|
| You want new entries searchable **immediately**, not at the next query | e.g. right after a refinement pass, before reporting success |
| You want to see the index state and timings without issuing a query | The run prints the incremental breakdown and writes a log line |
| Vectors must correspond exactly to the latest text format (`--rebuild`) | Full re-embed, ~6 minutes for a 1,265-entry store; never needed for normal operation |

**Command:**
```bash
<PY> skills/chroma-hybrid-search/scripts/update_db.py
```

### Why deleting or editing a cold-store line is now cheap

Cold-store entries are keyed by a semantic hash (`cold-notes#<hash12>`) covering `topic` / `content` / `tags` / `skill` / `memory_type` — **not** by line number, and deliberately **excluding** `date` / `time` / `project` / `quality` and every template string. Consequences worth knowing:

- Deleting or inserting a line anywhere in `raw.jsonl` does **not** re-embed the entries after it. Measured on a 928-line store: removing the middle line re-embedded **0** entries — the 463 entries below it only had their `line` metadata refreshed, at zero embedding cost.
- Marking an entry `quality: reviewed` during refinement does **not** trigger a re-embed, because `quality` is outside the hash. It does drop the entry from search: `reviewed` cold notes are excluded from the index by default (refined ones already live in the hot store; skipped ones were judged not worth keeping), and the next sync deletes their vectors at zero embedding cost.
- Changing how the indexed text is assembled does **not** invalidate the whole store.

The line number survives as `line` in metadata and is returned with every cold-store search hit, so results stay traceable to their source line.

### Run log

Every indexing run appends one JSON line to `~/.deep-memory/logs/index-runs.jsonl` with per-stage timings and a breakdown of why entries were re-embedded (`new` / `text-changed` / `id-missing`). A run that re-embeds more than 20% of the store prints a warning. When the reason is `id-missing`, the usual cause is that the repo copy and the `~/.claude/skills` installed copy of `kb_reader.py` are on different versions — they assemble the indexed text differently, so each sees the other's entries as changed.

---

## Reading the Cold Store: Through RAG, Not Directly

Once `update_db.py` has vectorized the cold store, retrieve its content through `search.py` (deep-memory Step 4, Path 2) exactly like any other indexed document — do not read `cold-notes/raw.jsonl` directly as a retrieval technique. A direct read doesn't rank or filter by relevance to the current question, and it doesn't scale as the file grows; it duplicates what RAG already does correctly, now that search.py's document set covers the cold store too.

**The one exception is the refinement workflow below**, which reads `raw.jsonl` directly on purpose: it needs every entry's `tags`/`skill`/`date` fields to cluster them, and neither RAG's ranked-top-K-for-one-query model nor the vector index's metadata (which doesn't retain those fields) can support that.

Entries written since the last index run are no longer a staleness problem: `search.py` tops the index up before it queries, so a RAG query picks them up on its own. Either way, that was never a reason to fall back to reading `raw.jsonl` directly.

---

## Memory Refinement Process (Cold → Hot Distillation)

### Trigger Conditions
**Proactively prompt the user to refine when any of the following apply:**

| Trigger | Reason |
|---|---|
| `cold-notes/raw.jsonl` has ≥ **30 pending entries** (quality ≠ `reviewed`) | Enough raw material for a small, fast batch; `write_cold.py` prints the pending count and hints at this threshold — the total line count is not a trigger |
| A single `tags` or `skill` value appears ≥ **3 times** | High-frequency topic indicates a generalizable pattern worth promoting to the hot store |
| User explicitly says "refine the cold store" | Explicit refinement command |
| User explicitly says "refine experience" / 「精煉經驗」 / 「修正經驗庫」 | Route `memory_type=experience` notes into the cross-skill experience store |

### Refinement Workflow

Refinement is tool-assisted: two scripts do the deterministic work so the LLM only reads, judges, and writes drafts.
A 184-entry batch done the old way (one agent reading everything and grepping the hot store per cluster) took ~15 minutes;
the time went into tool round-trips, output drafting, and judging skips one by one — not into indexing (7 s).

1. **Prepare work packs (no LLM)** — run from the venv so it can find hot-store candidates:
   ```bash
   <PY> skills/deep-memory/scripts/refine_prepare.py            # optional: --since YYYY-MM-DD --project <name> --max-pack 25
   ```
   It reads pending entries (quality ≠ `reviewed`) with their line numbers, groups them by project + theme
   (skill, or the first tag when the skill is generic), puts near-duplicate notes into the same pack and flags them
   (`⚠️ 可能重複`), and lists each entry's top-3 existing hot-store entries from the hybrid index plus a suggested target file.
   Output: `<workspace>/refine-work/<run-id>/pack-NN.md` + `manifest.json` (every pending line with a content hash).
   Candidates are ranked by vector + BM25 fusion and scored by vector similarity (fast: ~1 minute of model loading plus
   well under a second per entry). `--rerank` re-orders the top 5 with the cross-encoder (truncated text) for sharper
   ranking at ~2–3 s per entry — full-text reranking measured 1–2 s per pair and is deliberately not used. When
   `<workspace>/config.json` selects the Jev re-ranker (see chroma-hybrid-search), `--rerank` uses Jev instead (first
   800 chars, strong-match threshold 0.6) and falls back to the local cross-encoder on a missing key or any error.
   Without the venv, add `--no-candidates` (standard library only; no duplicate-candidate lookup).
2. **Draft in parallel** — dispatch one subagent per pack **at the same time**. Each reads only its own `pack-NN.md`
   (the draft format contract is at the top of every pack) and writes `pack-NN.drafts.md` next to it:
   `### TARGET:` new entries, `### APPEND-TO: <file> :: <existing title>` additions, `### NEW CATEGORY:` only when nothing fits,
   optional `## KEYWORDS`, and a mandatory `## REVIEWED` list covering **every** line in the pack
   (`L123 | promoted→<file>` or `L124 | skipped: <reason>` — skips are one line, never prose).
   Hand subagents the pack path, not the cold store; they must not read `raw.jsonl`.
3. **Preview and ask for confirmation**:
   ```bash
   python skills/deep-memory/scripts/refine_apply.py --work-dir <workspace>/refine-work/<run-id> --dry-run
   ```
   Show the user the summary (entries added, appends, new categories, lines to mark) and ask before writing.
4. **Apply** (standard library only):
   ```bash
   python skills/deep-memory/scripts/refine_apply.py --work-dir <workspace>/refine-work/<run-id>
   ```
   It validates everything before writing anything: REVIEWED must cover every manifest line (`--allow-partial` to override),
   each line's content hash must still match (guards against line shifts), APPEND-TO titles and TARGET files must exist
   (knowledge-base files are created only via NEW CATEGORY; `experience/skill-*.md` may be created and is indexed automatically).
   Touched files are backed up to `<work-dir>/backup/` first. Refined **and skipped** entries are marked `"quality": "reviewed"`
   (never deleted — history is preserved).
5. **Refine small and often**: 30–50 pending entries per run keeps every pack small and the whole run to a few minutes.
6. **Rebuild the index and spot-check (verification — refinement is NOT complete without this)**:
   run `update_db.py` to re-index, then verify retrieval with a `search.py` query using
   keywords from at least one newly written hot-store entry:

   ```bash
   <PY> skills/chroma-hybrid-search/scripts/update_db.py
   <PY> skills/chroma-hybrid-search/scripts/search.py --query "keywords from a new hot-store entry" --limit 3 --min-score 0.35
   ```

   The new entry must appear in the results. If it doesn't, the index did not pick it up —
   check that the entry uses the `## 🔧` heading format, then re-run the rebuild. Do not
   report the refinement as done until the spot-check passes.

### Experience Refinement Shortcut

For skill/tool lessons, use the deterministic promotion script instead of hand-editing every `experience/skill-*.md` file:

```bash
# Preview pending experience entries
python skills/deep-memory/scripts/refine_experience.py --dry-run

# Promote pending entries and mark source cold notes reviewed
python skills/deep-memory/scripts/refine_experience.py --apply

# Refresh search metadata/vectors and verify
<PY> skills/chroma-hybrid-search/scripts/update_db.py
<PY> skills/chroma-hybrid-search/scripts/search.py --query "promoted experience keywords" --memory-type experience --limit 3 --min-score 0.35
```

The script reads `memory_type=experience` and `memory_type=both`, groups by `skill`, writes traceable `## 🔧` entries into `experience/skill-[skill-id].md`, updates `experience/_index.json`, and cites the original `cold-notes/raw.jsonl#L...` source. Run `--dry-run` first for broad refinements; use `--skill <id>` when correcting one skill's experience store.

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
