---
name: memory-backup
description: "Exports the local knowledge base (knowledge-base/, experience/) and ChromaDB vector data as JSONL, then backs it up to a dedicated private GitHub repository. Also supports cross-device restore from GitHub. Typically invoked by super-memory after new knowledge-base or experience entries are written in a session, but can also be run directly by the user at any time to back up or restore."
---

# Memory Backup Skill — Knowledge Base GitHub Backup & Restore

This skill provides two core functions:
- **Backup**: Push a knowledge base snapshot to a GitHub private repository
- **Restore**: Pull and restore the knowledge base from GitHub on a new device

---

> **Cross-platform command convention** — in every command below, `<PY>` is the virtual-env Python:
>
> - **Windows (PowerShell):** `.venv\Scripts\python`
> - **Linux / macOS:** `.venv/bin/python`
>
> All `skills/...` paths assume the skill pack lives inside your current project (project-local install). If you installed the skills globally (e.g. `~/.agents/skills/`), point to that location instead and add `--workspace "<your-project>"` so the scripts read/write the project's knowledge base.

## ⚙️ Prerequisites

### 1. Python Environment
The `.venv` and `requirements.txt` from the `chroma-hybrid-search` skill must be installed first — this skill shares the same virtual environment.

### 2. Git
`git` must be installed and on your PATH — this skill drives `git init / add / commit / push` directly. Verify with `git --version`.

### 3. GitHub CLI (`gh`)
```bash
# Download and install: https://cli.github.com/

# Log in (one-time)
gh auth login
```

---

## 📦 What Gets Backed Up

| Item | Description |
|---|---|
| `knowledge-base/*.md` | All knowledge base category files (full content) |
| `knowledge-base/_index.json` | Knowledge base keyword index |
| `experience/*.md` | All cross-skill experience records |
| `experience/_index.json` | Experience store index |
| `backup/chroma_export.jsonl` | Portable JSONL export of ChromaDB entries (no binary vectors — only id, text, metadata) |

> **⚠️ Not backed up:** `.venv/`, `chroma_hybrid_db/` — these are machine-generated and can be rebuilt after restore.

---

## 🚀 Backup Commands

`--repo` defaults to **`super-memory-knowledge`** — omit it entirely unless you specifically want a different repo name. This is deliberate: a fixed default means the same repo name every time, on every device, with no risk of a typo or a forgotten name silently creating a second, disconnected backup repo. Pass `--repo <name>` only to override.

### First Backup (Auto-creates GitHub private repo)
```bash
<PY> skills/memory-backup/scripts/backup.py
```

### Incremental Backup (Run regularly)
```bash
<PY> skills/memory-backup/scripts/backup.py --message "feat: add spring session notes"
```

### Public Backup (Share your knowledge base)
```bash
<PY> skills/memory-backup/scripts/backup.py --visibility public
```

### Using a Different Repo Name
```bash
<PY> skills/memory-backup/scripts/backup.py --repo my-custom-name
```

> **🛡️ Safe push (no force-overwrite):** Before pushing, the script fetches the remote and checks whether it contains commits the local backup does not have (e.g. a backup pushed from another device). If the remote is ahead, the push is **aborted with a warning** rather than force-overwriting — so one machine can never silently clobber another machine's backup. To reconcile, run `restore.py` first, then back up again.

---

## 🔄 Cross-Device Restore

### Scenario: Restoring the knowledge base from GitHub on a new machine

#### Step 1: Install the skill environment
Complete the super-memory skill package installation on the new device. Refer to `skills/chroma-hybrid-search/SKILL.md`.

#### Step 2: Run the restore script

`--repo` defaults to the same **`super-memory-knowledge`** as `backup.py` — omit it unless you backed up under a custom name, in which case pass the same `--repo <name>` here too.

**Safe mode (recommended) — only copies files that exist on GitHub but are missing locally; does not overwrite existing files:**
```bash
<PY> skills/memory-backup/scripts/restore.py
```

**Overwrite mode — completely replaces the local knowledge base with the GitHub backup (use on a fresh device or for intentional sync):**
```bash
<PY> skills/memory-backup/scripts/restore.py --overwrite
```

**Restore to a specific directory (not the current directory):**
```bash
<PY> skills/memory-backup/scripts/restore.py --workspace "D:\Projects\my-project"
```

#### Step 3: Rebuild the local vector index
After restore, the script will automatically prompt you to run:
```bash
<PY> skills/chroma-hybrid-search/scripts/update_db.py
```

---

## 🔀 Restore Mode Comparison

| Mode | Flag | Behavior | When to Use |
|---|---|---|---|
| **Safe mode** (default) | (no flag) | Only copies files missing locally; **existing files are not overwritten** | New computer, multi-device sync |
| **Overwrite mode** | `--overwrite` | Deletes local knowledge base and replaces entirely with GitHub version | Fresh environment, intentionally rolling back to a backup |

---

## 📋 Full Workflow Diagram

```
【Backup Flow】
export_jsonl.py → Export ChromaDB to JSONL
     ↓
Copy knowledge-base/ + experience/ to backup/
     ↓
gh CLI: confirm / create remote repo
     ↓
git add → commit → push → GitHub ✅

【Restore Flow (New Device)】
gh auth login (one-time)
     ↓
restore.py → git clone / pull backup repo
     ↓
Copy knowledge-base/ + experience/ to working directory
     ↓
update_db.py → Rebuild local ChromaDB vector index ✅
```

---

## 🔄 Agent Auto-Reminder Rules

After completing any knowledge base write this session, the Agent must remind the user before ending the conversation:

```bash
# ① Rebuild the local vector index
<PY> skills/chroma-hybrid-search/scripts/update_db.py

# ② Back up to GitHub
<PY> skills/memory-backup/scripts/backup.py
```
