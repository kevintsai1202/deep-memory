---
name: memory-import
description: "Imports memory data from external systems — ChatGPT memory export, Claude Code local auto-memory files, or a legacy auto-skill project's knowledge-base/experience data — into this project's cold or hot store. Supports --dry-run preview and a safe merge that never overwrites without --force. Typically run once by the user right after a fresh deep-memory install if they have existing memory data to bring over (deep-memory's Step 0.5 mentions it), but can be run any time. Never scans the filesystem on its own — the user must point it at an explicit --input path."
---

# Memory Import — Bring External Memory Data Into deep-memory

Migrates memory data from three sources into deep-memory's existing hot/cold stores. Pure Python standard library — no `.venv`, no `pip install`, works from turn one.

## Supported Sources

| `--source` | Input | Destination | Why |
|---|---|---|---|
| `chatgpt` | ChatGPT memory export (`.json`) | Cold store (`cold-notes/raw.jsonl`) | Flat, uncategorized facts — let the existing cold→hot refinement workflow (`deep-memory/resources/cold-store-and-vectorization.md`) promote the valuable ones later |
| `claude-local` | Claude Code local memory directory (`~/.claude/projects/*/memory/`) | Hot store, new category `knowledge-base/imported-claude-memory.md` | Already structured (frontmatter has `name`/`description`/`type`) — safe to go straight into the hot store |
| `autoskill` | A legacy auto-skill project's root directory | Hot store, direct safe merge into `knowledge-base/` and `experience/` | Same schema as deep-memory's own hot store — this is a structural merge, not a transform |

Mem0 and MemGPT/Letta are not implemented yet — no real export files were available to validate the format against. The adapter dispatch in `import.py` is built so adding either later is a new parser function plus a new `--source` choice, nothing else changes.

## Commands

Always preview first with `--dry-run`, then drop it once the list looks right:

```bash
# ChatGPT memory export → cold store
python skills/memory-import/scripts/import.py --source chatgpt --input path/to/export.json --dry-run
python skills/memory-import/scripts/import.py --source chatgpt --input path/to/export.json

# Claude Code local memory directory → hot store
python skills/memory-import/scripts/import.py --source claude-local --input "C:\Users\<you>\.claude\projects\<hash>\memory" --dry-run
python skills/memory-import/scripts/import.py --source claude-local --input "C:\Users\<you>\.claude\projects\<hash>\memory"

# Legacy auto-skill project → hot store safe merge
python skills/memory-import/scripts/import.py --source autoskill --input path/to/old-project --dry-run
python skills/memory-import/scripts/import.py --source autoskill --input path/to/old-project

# Force-overwrite categories that already exist locally (autoskill only)
python skills/memory-import/scripts/import.py --source autoskill --input path/to/old-project --force
```

**Workspace Storage Path Resolution:**
By default, deep-memory uses the user's global directory `~/.deep-memory` (which resolves to `C:\Users\<username>\.deep-memory` on Windows) to store all knowledge bases, cold notes, and database files.
- If you want to use a specific directory, set the `DEEP_MEMORY_WORKSPACE` environment variable (e.g., `DEEP_MEMORY_WORKSPACE="."` or `DEEP_MEMORY_WORKSPACE="D:\my-memories"`).
- You can also pass `--workspace <path>` to any script to override the workspace path for that specific command (e.g., pass `--workspace <path>` if you want to import into a specific project's directory instead of the global store).

## Dedup Behavior

Every source is safe to rerun on the same input without duplicating data:

- **chatgpt**: each cold-store entry carries a `tags` entry `import-id:<sha1-of-text>`; a matching hash already present in `raw.jsonl` is skipped.
- **claude-local**: each hot-store entry ends with a hidden `<!-- imported-from: claude-local:<name> -->` marker; a matching marker already present in the category file is skipped.
- **autoskill**: a category `id` already present in the local `_index.json` is skipped unless `--force` is passed.

## Error Handling

- Input path doesn't exist → hard error, exits non-zero, nothing is written.
- `chatgpt`: unrecognized top-level JSON shape → hard error for the whole file (the parser doesn't understand the structure at all), but it tells you the fallback: write entries one at a time with `chroma-hybrid-search/scripts/write_cold.py` instead. Individual empty/unreadable items within a recognized shape are skipped with a warning; the rest still import.
- `claude-local`: a `.md` file missing `name` or `description` is skipped with a warning; the rest still import. `MEMORY.md` itself is always skipped (it's the index, not a memory entry).
- `autoskill`: a missing `knowledge-base/_index.json` or `experience/_index.json` in the source skips just that half with a warning; the other half still imports. The merge is also safe to interrupt mid-run and rerun — dedup is keyed off the destination index, not the filesystem, so a partial copy from an earlier interrupted run is correctly re-adopted rather than causing a stuck or inconsistent state.

## After Importing

```bash
# Make the new content searchable
python skills/chroma-hybrid-search/scripts/update_db.py

# If you imported into the hot store, consider backing it up
python skills/memory-backup/scripts/backup.py
```

## What's Not In Scope

- Mem0 / MemGPT (Letta) adapters — architecture supports adding them, no real files to validate against yet
- Any automatic detection or scanning of the user's filesystem — you always pass an explicit `--input` path
- Scheduled/recurring import — this is a manual, one-shot migration tool
