# Memory-Import Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `memory-import` sub-skill that lets a user bring memory data from ChatGPT's memory export, Claude Code's local auto-memory files, or a legacy auto-skill project into deep-memory's hot (`knowledge-base/`) or cold (`cold-notes/`) store.

**Architecture:** One new skill, `skills/memory-import/`, containing a single stdlib-only script `import.py` with three adapters (`chatgpt`, `claude-local`, `autoskill`) dispatched via `--source`. Each adapter normalizes its input and hands off to a shared writer (cold-store append for chatgpt, hot-store category append for claude-local, structural safe-merge for autoskill). `deep-memory/SKILL.md`'s Step 0.5 gets one new passive-prompt paragraph pointing at this skill; no auto-detection or filesystem scanning is added anywhere.

**Tech Stack:** Python 3 standard library only (`json`, `re`, `os`, `sys`, `shutil`, `hashlib`, `datetime`, `argparse`). No new dependencies, no `.venv` required for this skill.

**Testing approach — deviation from the usual TDD/pytest default:** This repository has no automated test suite anywhere (`seed.py`, `write_cold.py`, `backup.py`, `restore.py` are all untested stdlib scripts, verified manually). The approved design spec (`docs/superpowers/specs/2026-07-01-memory-import-design.md`) explicitly keeps this convention for `memory-import` too. Every task below therefore replaces the "write failing test" step with "create a throwaway fixture under `.tmp-import-test/`, run the script, check the exact output/file content, then delete the fixture" — same rigor, no pytest.

---

### Task 1: `import.py` skeleton — CLI, dispatch, and the one hard-fail path

**Files:**
- Create: `skills/memory-import/scripts/import.py`

- [ ] **Step 1: Write the skeleton**

```python
# -*- coding: utf-8 -*-
"""
import.py
Imports memory data from external systems (ChatGPT memory export, Claude Code
local auto-memory files, legacy auto-skill projects) into deep-memory's
cold store (cold-notes/raw.jsonl) or hot store (knowledge-base/).
"""
import os
import sys
import re
import json
import shutil
import hashlib
import argparse
from datetime import datetime

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def parse_chatgpt(input_path):
    raise NotImplementedError


def write_to_cold(texts, workspace, dry_run):
    raise NotImplementedError


def parse_claude_local(input_dir):
    raise NotImplementedError


def write_claude_local_to_hot(memories, workspace, dry_run):
    raise NotImplementedError


def merge_autoskill(input_dir, workspace, force, dry_run):
    raise NotImplementedError


def main():
    parser = argparse.ArgumentParser(
        description="Import memory data from external systems into deep-memory"
    )
    parser.add_argument("--source", type=str, required=True,
                        choices=["chatgpt", "claude-local", "autoskill"],
                        help="External memory source type")
    parser.add_argument("--input", type=str, required=True,
                        help="Path to the source file (chatgpt) or directory (claude-local, autoskill)")
    parser.add_argument("--workspace", type=str, default=os.getcwd(),
                        help="Workspace root path (parent of knowledge-base/, cold-notes/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be imported without writing any files")
    parser.add_argument("--force", action="store_true",
                        help="autoskill only: overwrite categories that already exist locally")
    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace)
    input_path = os.path.abspath(args.input)

    if not os.path.exists(input_path):
        print(f"[ERROR] 輸入路徑不存在：{input_path}")
        sys.exit(1)

    if args.source == "chatgpt":
        texts = parse_chatgpt(input_path)
        write_to_cold(texts, workspace, args.dry_run)
    elif args.source == "claude-local":
        memories = parse_claude_local(input_path)
        write_claude_local_to_hot(memories, workspace, args.dry_run)
    elif args.source == "autoskill":
        merge_autoskill(input_path, workspace, args.force, args.dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the missing-path error**

Run:
```bash
python skills/memory-import/scripts/import.py --source chatgpt --input ./does-not-exist.json
```
Expected: prints `[ERROR] 輸入路徑不存在：...does-not-exist.json` and exits non-zero.

- [ ] **Step 3: Verify dispatch reaches the right stub**

Run:
```bash
python -c "import pathlib; pathlib.Path('.tmp-import-test').mkdir(exist_ok=True); pathlib.Path('.tmp-import-test/x.json').write_text('[]', encoding='utf-8')"
python skills/memory-import/scripts/import.py --source chatgpt --input .tmp-import-test/x.json
rm -rf .tmp-import-test
```
Expected: a `NotImplementedError` traceback from inside `parse_chatgpt` (proves argument parsing and dispatch both work before any adapter exists).

- [ ] **Step 4: Commit**

```bash
git add skills/memory-import/scripts/import.py
git commit -m "feat(memory-import): add CLI skeleton with source dispatch"
```

---

### Task 2: ChatGPT adapter → cold store

**Files:**
- Modify: `skills/memory-import/scripts/import.py` (replace `parse_chatgpt` and `write_to_cold` stubs)

- [ ] **Step 1: Implement `parse_chatgpt`**

Replace the `parse_chatgpt` stub with:

```python
def parse_chatgpt(input_path):
    """解析 ChatGPT 記憶匯出檔，回傳正規化後的文字清單。格式不確定，寬容偵測幾種常見形狀。"""
    with open(input_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[ERROR] 不是合法的 JSON：{e}")
            sys.exit(1)

    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("memories"), list):
        items = data["memories"]

    if items is None:
        sample_keys = list(data.keys())[:10] if isinstance(data, dict) else None
        print(f"[ERROR] 無法辨識 ChatGPT 匯出格式。偵測到的頂層型態：{type(data).__name__}")
        if sample_keys is not None:
            print(f"        頂層 key：{sample_keys}")
        print("        可以改用以下指令手動一條條寫入冷庫：")
        print('        python skills/chroma-hybrid-search/scripts/write_cold.py '
              '--topic "..." --content "..." --tags "chatgpt-memory" --skill memory-import')
        sys.exit(1)

    texts = []
    skipped = 0
    for item in items:
        text = None
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            for key in ("content", "text", "memory"):
                if isinstance(item.get(key), str):
                    text = item[key]
                    break
        if not text or not text.strip():
            skipped += 1
            continue
        texts.append(text.strip())

    if skipped:
        print(f"[WARN] 跳過 {skipped} 筆空白或無法辨識內容的項目")

    return texts
```

- [ ] **Step 2: Implement `write_to_cold`**

Replace the `write_to_cold` stub with:

```python
def write_to_cold(texts, workspace, dry_run):
    """把正規化後的文字清單寫進冷庫（cold-notes/raw.jsonl），依 import-id 標記去重"""
    cold_dir = os.path.join(workspace, "cold-notes")
    jsonl_path = os.path.join(cold_dir, "raw.jsonl")

    existing_ids = set()
    if os.path.exists(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for tag in entry.get("tags", []):
                    if tag.startswith("import-id:"):
                        existing_ids.add(tag)

    now = datetime.now()
    new_entries = []
    duplicate = 0
    for text in texts:
        import_id = "import-id:" + hashlib.sha1(text.encode("utf-8")).hexdigest()
        if import_id in existing_ids:
            duplicate += 1
            continue
        entry = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "topic": text[:60],
            "content": text,
            "tags": ["chatgpt-memory", "imported", import_id],
            "skill": "memory-import",
            "quality": "raw",
        }
        new_entries.append(entry)
        existing_ids.add(import_id)

    if dry_run:
        print(f"[DRY-RUN] 會寫入 {len(new_entries)} 筆到 {jsonl_path}（{duplicate} 筆重複已跳過）")
        for entry in new_entries[:5]:
            print(f"  - {entry['topic']}")
        if len(new_entries) > 5:
            print(f"  ...（其餘 {len(new_entries) - 5} 筆略）")
        return

    os.makedirs(cold_dir, exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        for entry in new_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"[OK] 已寫入 {len(new_entries)} 筆到冷庫（{duplicate} 筆重複已跳過）")
    if new_entries:
        print("[下一步] 執行 update_db.py 讓新內容可以被語意搜尋找到：")
        print("  python skills/chroma-hybrid-search/scripts/update_db.py --workspace " + workspace)
```

- [ ] **Step 3: Verify with a fixture — happy path + dry-run + malformed content**

```bash
mkdir -p .tmp-import-test
python -c "
import json, pathlib
data = ['User lives in Taipei and works in medical device software.',
        {'content': 'User prefers concise responses without emojis.'},
        '']
pathlib.Path('.tmp-import-test/chatgpt-export.json').write_text(json.dumps(data), encoding='utf-8')
"

# Dry-run first
python skills/memory-import/scripts/import.py --source chatgpt \
  --input .tmp-import-test/chatgpt-export.json --workspace .tmp-import-test/ws --dry-run
```
Expected: `[WARN] 跳過 1 筆空白或無法辨識內容的項目` then `[DRY-RUN] 會寫入 2 筆到 .../cold-notes/raw.jsonl（0 筆重複已跳過）` with both topics listed.

```bash
# Real run
python skills/memory-import/scripts/import.py --source chatgpt \
  --input .tmp-import-test/chatgpt-export.json --workspace .tmp-import-test/ws
cat .tmp-import-test/ws/cold-notes/raw.jsonl
```
Expected: `[OK] 已寫入 2 筆到冷庫（0 筆重複已跳過）` and the file has exactly 2 JSON lines, each with `"skill": "memory-import"` and a `tags` array containing an `import-id:` entry.

```bash
# Rerun same file — must dedup, not duplicate
python skills/memory-import/scripts/import.py --source chatgpt \
  --input .tmp-import-test/chatgpt-export.json --workspace .tmp-import-test/ws
```
Expected: `[OK] 已寫入 0 筆到冷庫（2 筆重複已跳過）`.

```bash
# Malformed format — must hard-fail with a helpful message
python -c "import pathlib; pathlib.Path('.tmp-import-test/bad.json').write_text('{\"foo\": 1}', encoding='utf-8')"
python skills/memory-import/scripts/import.py --source chatgpt \
  --input .tmp-import-test/bad.json --workspace .tmp-import-test/ws
echo "exit code: $?"
```
Expected: `[ERROR] 無法辨識 ChatGPT 匯出格式...`, mentions the `write_cold.py` fallback, exit code non-zero.

```bash
rm -rf .tmp-import-test
```

- [ ] **Step 4: Commit**

```bash
git add skills/memory-import/scripts/import.py
git commit -m "feat(memory-import): add chatgpt adapter writing to cold store"
```

---

### Task 3: Claude Code local memory adapter → hot store

**Files:**
- Modify: `skills/memory-import/scripts/import.py` (replace `parse_claude_local` and `write_claude_local_to_hot` stubs; add `parse_frontmatter` and `update_kb_index` helpers)

- [ ] **Step 1: Implement the frontmatter parser**

Add this helper above `parse_claude_local`:

```python
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def parse_frontmatter(raw_text):
    """
    解析簡化版 YAML frontmatter（name / description / metadata.type），回傳 (fields, body)。
    只處理本專案 memory 檔案實際會用到的兩層結構，不是通用 YAML parser。
    """
    raw_text = raw_text.replace("\r\n", "\n")
    m = _FRONTMATTER_RE.match(raw_text)
    if not m:
        return {}, raw_text

    header, body = m.group(1), m.group(2)
    fields = {}
    current_key = None
    for line in header.splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current_key == "metadata":
            sub_match = re.match(r"\s*(\w+):\s*(.*)", line)
            if sub_match:
                fields[f"metadata.{sub_match.group(1)}"] = sub_match.group(2).strip().strip('"').strip("'")
            continue
        key_match = re.match(r"(\w+):\s*(.*)", line)
        if key_match:
            key, value = key_match.group(1), key_match.group(2).strip()
            fields[key] = value.strip('"').strip("'")
            current_key = key

    return fields, body.strip()
```

- [ ] **Step 2: Implement `parse_claude_local`**

Replace the stub with:

```python
def parse_claude_local(input_dir):
    """掃描 Claude Code 本機 memory/ 目錄，回傳 [{name, description, type, body}, ...]"""
    results = []
    skipped = 0
    for filename in sorted(os.listdir(input_dir)):
        if not filename.endswith(".md") or filename == "MEMORY.md":
            continue
        path = os.path.join(input_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        fields, body = parse_frontmatter(raw)
        name = fields.get("name")
        description = fields.get("description")
        if not name or not description:
            print(f"[WARN] 跳過 {filename}：缺少 name 或 description")
            skipped += 1
            continue
        results.append({
            "name": name,
            "description": description,
            "type": fields.get("metadata.type", "unknown"),
            "body": body,
        })
    if skipped:
        print(f"[WARN] 共跳過 {skipped} 個檔案（缺少必要欄位）")
    return results
```

- [ ] **Step 3: Implement the hot-store writer and index updater**

Replace the `write_claude_local_to_hot` stub with:

```python
def update_kb_index(kb_dir, cat_id, cat_file, title, new_keywords):
    """更新 knowledge-base/_index.json：分類已存在則合併 keywords，否則新增一筆"""
    index_path = os.path.join(kb_dir, "_index.json")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {"_version": 1, "categories": []}

    for cat in index["categories"]:
        if cat["id"] == cat_id:
            cat["keywords"] = sorted(set(cat.get("keywords", [])) | set(new_keywords))
            break
    else:
        index["categories"].append({
            "id": cat_id,
            "file": cat_file,
            "title": title,
            "keywords": sorted(new_keywords),
        })

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def write_claude_local_to_hot(memories, workspace, dry_run):
    """把 Claude 本機記憶寫進熱庫的 imported-claude-memory.md 分類，以隱藏標記去重"""
    kb_dir = os.path.join(workspace, "knowledge-base")
    category_file = "imported-claude-memory.md"
    category_path = os.path.join(kb_dir, category_file)

    existing_content = ""
    if os.path.exists(category_path):
        with open(category_path, "r", encoding="utf-8") as f:
            existing_content = f.read()

    today = datetime.now().strftime("%Y-%m-%d")
    new_blocks = []
    skipped = 0
    all_keywords = set()

    for mem in memories:
        marker = f"<!-- imported-from: claude-local:{mem['name']} -->"
        if marker in existing_content:
            skipped += 1
            continue
        block = (
            f"## 🔧 {mem['description']}\n"
            f"**Date:** {today}\n"
            f"**Context:** Imported from Claude Code local memory (type: {mem['type']})\n"
            f"**Best Practices:**\n"
            f"{mem['body']}\n"
            f"{marker}\n"
        )
        new_blocks.append(block)
        all_keywords.update(part for part in mem["name"].split("-") if part)
        all_keywords.update(
            word.strip(".,!?;:()[]\"'").lower()
            for word in mem["description"].split()
            if len(word.strip(".,!?;:()[]\"'")) > 2
        )

    if dry_run:
        print(f"[DRY-RUN] 會寫入 {len(new_blocks)} 筆到 {category_path}（{skipped} 筆已存在跳過）")
        for mem in memories:
            print(f"  - {mem['name']}: {mem['description']}")
        return

    if not new_blocks:
        print(f"[OK] 沒有新內容需要寫入（{skipped} 筆已存在）")
        return

    os.makedirs(kb_dir, exist_ok=True)
    with open(category_path, "a", encoding="utf-8") as f:
        if not existing_content:
            f.write("# Imported from Claude Code Local Memory\n\n")
        for block in new_blocks:
            f.write("\n" + block)

    update_kb_index(kb_dir, "imported-claude-memory", category_file,
                     "Imported from Claude Code Local Memory", all_keywords)

    print(f"[OK] 已寫入 {len(new_blocks)} 筆到熱庫（{skipped} 筆已存在跳過）")
    print("[下一步] 執行 update_db.py 讓新內容可以被語意搜尋找到：")
    print("  python skills/chroma-hybrid-search/scripts/update_db.py --workspace " + workspace)
```

- [ ] **Step 4: Verify with a fixture — happy path, MEMORY.md skip, missing-field skip, rerun dedup**

```bash
mkdir -p .tmp-import-test/memory
python -c "
import pathlib
pathlib.Path('.tmp-import-test/memory/MEMORY.md').write_text('- [x](x.md) — index only, must be skipped', encoding='utf-8')
pathlib.Path('.tmp-import-test/memory/testing-preference.md').write_text('''---
name: testing-preference
description: User prefers pytest with a real database, not mocks
metadata:
  type: feedback
---

Reason: a past incident where mocked tests passed but the prod migration failed.
''', encoding='utf-8')
pathlib.Path('.tmp-import-test/memory/broken.md').write_text('---\nname: broken\n---\nno description field', encoding='utf-8')
"

python skills/memory-import/scripts/import.py --source claude-local \
  --input .tmp-import-test/memory --workspace .tmp-import-test/ws --dry-run
```
Expected: `[WARN] 跳過 broken.md：缺少 name 或 description`, then `[WARN] 共跳過 1 個檔案（缺少必要欄位）`, then `[DRY-RUN] 會寫入 1 筆到 ...` listing only `testing-preference`. `MEMORY.md` never mentioned (silently skipped).

```bash
python skills/memory-import/scripts/import.py --source claude-local \
  --input .tmp-import-test/memory --workspace .tmp-import-test/ws
cat .tmp-import-test/ws/knowledge-base/imported-claude-memory.md
cat .tmp-import-test/ws/knowledge-base/_index.json
```
Expected: `[OK] 已寫入 1 筆到熱庫（0 筆已存在跳過）`; the `.md` file contains a `## 🔧 User prefers pytest with a real database, not mocks` block ending with `<!-- imported-from: claude-local:testing-preference -->`; `_index.json` has one category with `"id": "imported-claude-memory"` and keywords including `testing` and `preference`.

```bash
# Rerun — must dedup via the marker
python skills/memory-import/scripts/import.py --source claude-local \
  --input .tmp-import-test/memory --workspace .tmp-import-test/ws
```
Expected: `[OK] 沒有新內容需要寫入（1 筆已存在）`.

```bash
rm -rf .tmp-import-test
```

- [ ] **Step 5: Commit**

```bash
git add skills/memory-import/scripts/import.py
git commit -m "feat(memory-import): add claude-local adapter writing to hot store"
```

---

### Task 4: auto-skill adapter → hot store safe merge

**Files:**
- Modify: `skills/memory-import/scripts/import.py` (replace `merge_autoskill` stub; add `_merge_autoskill_half` helper)

- [ ] **Step 1: Implement the merge logic**

Replace the `merge_autoskill` stub and add its helper:

```python
def _merge_autoskill_half(input_dir, workspace, subdir, force, dry_run):
    """
    合併舊 auto-skill 專案的 knowledge-base/ 或 experience/ 半邊到本機。
    來源索引的分類清單可能放在 "categories" 或 "skills" key 下（舊格式從未明確規範過），
    兩者都嘗試讀取；寫回本機一律正規化成 "categories"，跟 deep-memory 現有的
    knowledge-base/_index.json 結構一致。
    """
    src_dir = os.path.join(input_dir, subdir)
    src_index_path = os.path.join(src_dir, "_index.json")
    if not os.path.exists(src_index_path):
        print(f"[WARN] 來源缺少 {subdir}/_index.json，跳過這一半")
        return 0, 0

    with open(src_index_path, "r", encoding="utf-8") as f:
        src_index = json.load(f)
    src_categories = src_index.get("categories") or src_index.get("skills") or []

    dest_dir = os.path.join(workspace, subdir)
    dest_index_path = os.path.join(dest_dir, "_index.json")
    if os.path.exists(dest_index_path):
        with open(dest_index_path, "r", encoding="utf-8") as f:
            dest_index = json.load(f)
    else:
        dest_index = {"_version": 1, "categories": []}
    dest_by_id = {cat["id"]: cat for cat in dest_index["categories"]}

    added, skipped = 0, 0
    for cat in src_categories:
        if "id" in cat:
            cat_id = cat.get("id")
        else:
            cat_id = cat.get("skill_id") or cat.get("skill-id")
        cat_file = cat.get("file")
        if not cat_id or not cat_file:
            print(f"[WARN] {subdir}: 略過缺少 id/file 的索引項目：{cat}")
            continue

        exists = cat_id in dest_by_id
        if exists and not force:
            skipped += 1
            continue

        if dry_run:
            action = "覆蓋" if exists else "新增"
            print(f"[DRY-RUN] {subdir}: {action} {cat_id} ({cat_file})")
            added += 1
            continue

        src_file = os.path.join(src_dir, cat_file)
        if not os.path.exists(src_file):
            print(f"[WARN] {subdir}: 來源檔案不存在：{src_file}，跳過 {cat_id}")
            continue

        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(src_file, os.path.join(dest_dir, cat_file))

        normalized = {
            "id": cat_id,
            "file": cat_file,
            "title": cat.get("title", cat_id),
            "keywords": cat.get("keywords", []),
        }
        if exists:
            dest_by_id[cat_id].update(normalized)
        else:
            dest_index["categories"].append(normalized)
            dest_by_id[cat_id] = normalized
        added += 1

    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)
        with open(dest_index_path, "w", encoding="utf-8") as f:
            json.dump(dest_index, f, ensure_ascii=False, indent=2)

    return added, skipped


def merge_autoskill(input_dir, workspace, force, dry_run):
    """安全合併舊 auto-skill 格式的 knowledge-base/ 與 experience/ 到本機（id 存在則預設跳過）"""
    total_added = 0
    for subdir in ("knowledge-base", "experience"):
        added, skipped = _merge_autoskill_half(input_dir, workspace, subdir, force, dry_run)
        total_added += added
        label = "DRY-RUN" if dry_run else "OK"
        verb = "會處理" if dry_run else "已合併"
        print(f"[{label}] {subdir}: {verb} {added} 筆，跳過 {skipped} 筆（已存在，未加 --force）")

    if not dry_run and total_added:
        print("[下一步] 執行 update_db.py 讓新內容可以被語意搜尋找到：")
        print("  python skills/chroma-hybrid-search/scripts/update_db.py --workspace " + workspace)
```

- [ ] **Step 2: Verify with a fixture — new categories, existing skip, `--force` overwrite, missing half**

```bash
mkdir -p .tmp-import-test/old-project/knowledge-base
python -c "
import json, pathlib
pathlib.Path('.tmp-import-test/old-project/knowledge-base/_index.json').write_text(json.dumps({
    '_version': 1,
    'categories': [{'id': 'backend-dev', 'file': 'backend-dev.md', 'title': 'Backend Dev Notes', 'keywords': ['fastapi', 'session']}]
}), encoding='utf-8')
pathlib.Path('.tmp-import-test/old-project/knowledge-base/backend-dev.md').write_text('## 🔧 Old note\n**Date:** 2025-01-01\n**Context:** test\n**Best Practices:**\n- example\n', encoding='utf-8')
"
# no experience/ dir on purpose — must be skipped with a warning, not a crash

python skills/memory-import/scripts/import.py --source autoskill \
  --input .tmp-import-test/old-project --workspace .tmp-import-test/ws --dry-run
```
Expected: `[DRY-RUN] knowledge-base: 新增 backend-dev (backend-dev.md)` then `[DRY-RUN] knowledge-base: 會處理 1 筆，跳過 0 筆...`, then `[WARN] 來源缺少 experience/_index.json，跳過這一半` then `[DRY-RUN] experience: 會處理 0 筆，跳過 0 筆...`.

```bash
python skills/memory-import/scripts/import.py --source autoskill \
  --input .tmp-import-test/old-project --workspace .tmp-import-test/ws
cat .tmp-import-test/ws/knowledge-base/_index.json
ls .tmp-import-test/ws/knowledge-base/
```
Expected: `[OK] knowledge-base: 已合併 1 筆，跳過 0 筆...`; `_index.json` contains the `backend-dev` category; `backend-dev.md` was copied.

```bash
# Rerun without --force — must skip, not duplicate/overwrite
python skills/memory-import/scripts/import.py --source autoskill \
  --input .tmp-import-test/old-project --workspace .tmp-import-test/ws
```
Expected: `[OK] knowledge-base: 已合併 0 筆，跳過 1 筆（已存在，未加 --force）`.

```bash
# Rerun with --force — must overwrite
python skills/memory-import/scripts/import.py --source autoskill \
  --input .tmp-import-test/old-project --workspace .tmp-import-test/ws --force
```
Expected: `[OK] knowledge-base: 已合併 1 筆，跳過 0 筆...`.

```bash
rm -rf .tmp-import-test
```

- [ ] **Step 3: Commit**

```bash
git add skills/memory-import/scripts/import.py
git commit -m "feat(memory-import): add autoskill adapter with safe merge into hot store"
```

---

### Task 5: Passive prompt in `deep-memory`'s bootstrap step

**Files:**
- Modify: `skills/deep-memory/SKILL.md:15` and `skills/deep-memory/SKILL.md:43-53`

- [ ] **Step 1: Add `import.py` to the stdlib-only script list**

In `skills/deep-memory/SKILL.md`, line 15, change:

```markdown
> **Which scripts actually need `<PY>` (the venv):** only `search.py`, `update_db.py`, and `export_jsonl.py` — they import `chromadb`/`sentence-transformers`. `seed.py`, `write_cold.py`, `backup.py`, and `restore.py` use only Python's standard library: any available `python`/`python3` works for those, no venv or `pip install` required. This matters because cold-store writes (Step 5.1) start from turn one, often before the venv exists.
```

to:

```markdown
> **Which scripts actually need `<PY>` (the venv):** only `search.py`, `update_db.py`, and `export_jsonl.py` — they import `chromadb`/`sentence-transformers`. `seed.py`, `write_cold.py`, `backup.py`, `restore.py`, and `memory-import/scripts/import.py` use only Python's standard library: any available `python`/`python3` works for those, no venv or `pip install` required. This matters because cold-store writes (Step 5.1) start from turn one, often before the venv exists.
```

- [ ] **Step 2: Add the passive import prompt**

In the same file, inside the "5. **First-install detection**" bullet, immediately after the existing seed-install code block and before the `- **If present**` line, insert a new blockquote paragraph. The bullet currently reads:

```markdown
5. **First-install detection (seed knowledge base)**: Check whether `knowledge-base/_index.json` exists:
   - **If absent** (fresh install) → Proactively inform the user and prompt seed initialization:
     > "No knowledge base detected. It is recommended to install the bundled seed knowledge (sourced from Mem0, MemGPT, and official Claude/GPT memory best practices):"
     > ```bash
     > # ① Install seed knowledge base
     > <PY> skills/deep-memory/scripts/seed.py
     >
     > # ② Vectorize seed content
     > <PY> skills/chroma-hybrid-search/scripts/update_db.py
     > ```
   - **If present** (not first run) → Skip this step and continue normally.
```

Change it to:

```markdown
5. **First-install detection (seed knowledge base)**: Check whether `knowledge-base/_index.json` exists:
   - **If absent** (fresh install) → Proactively inform the user and prompt seed initialization:
     > "No knowledge base detected. It is recommended to install the bundled seed knowledge (sourced from Mem0, MemGPT, and official Claude/GPT memory best practices):"
     > ```bash
     > # ① Install seed knowledge base
     > <PY> skills/deep-memory/scripts/seed.py
     >
     > # ② Vectorize seed content
     > <PY> skills/chroma-hybrid-search/scripts/update_db.py
     > ```
     > "If you already have memory data from somewhere else — a ChatGPT memory export, Claude Code's own local memory files, or an old auto-skill project — you can bring it in with the `memory-import` skill instead of starting from scratch:"
     > ```bash
     > python skills/memory-import/scripts/import.py --source chatgpt --input <export.json> --dry-run
     > python skills/memory-import/scripts/import.py --source claude-local --input <path-to-memory-dir> --dry-run
     > python skills/memory-import/scripts/import.py --source autoskill --input <path-to-old-project> --dry-run
     > ```
     > "Drop `--dry-run` once the preview looks right. This is entirely optional and only runs when you point it at a path yourself — nothing is scanned automatically."
   - **If present** (not first run) → Skip this step and continue normally.
```

- [ ] **Step 3: Verify**

Run:
```bash
grep -n "memory-import" skills/deep-memory/SKILL.md
```
Expected: at least 5 matches (the stdlib-list mention plus the 3 command lines plus the intro sentence).

- [ ] **Step 4: Commit**

```bash
git add skills/deep-memory/SKILL.md
git commit -m "docs(deep-memory): passively point fresh installs at memory-import"
```

---

### Task 6: `skills/memory-import/SKILL.md`

**Files:**
- Create: `skills/memory-import/SKILL.md`

- [ ] **Step 1: Write the skill manifest**

```markdown
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

Pass `--workspace <path>` if the target knowledge base isn't in the current directory (same convention as every other script in this skill pack).

## Dedup Behavior

Every source is safe to rerun on the same input without duplicating data:

- **chatgpt**: each cold-store entry carries a `tags` entry `import-id:<sha1-of-text>`; a matching hash already present in `raw.jsonl` is skipped.
- **claude-local**: each hot-store entry ends with a hidden `<!-- imported-from: claude-local:<name> -->` marker; a matching marker already present in the category file is skipped.
- **autoskill**: a category `id` already present in the local `_index.json` is skipped unless `--force` is passed.

## Error Handling

- Input path doesn't exist → hard error, exits non-zero, nothing is written.
- `chatgpt`: unrecognized top-level JSON shape → hard error for the whole file (the parser doesn't understand the structure at all), but it tells you the fallback: write entries one at a time with `chroma-hybrid-search/scripts/write_cold.py` instead. Individual empty/unreadable items within a recognized shape are skipped with a warning; the rest still import.
- `claude-local`: a `.md` file missing `name` or `description` is skipped with a warning; the rest still import. `MEMORY.md` itself is always skipped (it's the index, not a memory entry).
- `autoskill`: a missing `knowledge-base/_index.json` or `experience/_index.json` in the source skips just that half with a warning; the other half still imports.

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
```

- [ ] **Step 2: Verify**

Run:
```bash
python -c "import re,pathlib; content=pathlib.Path('skills/memory-import/SKILL.md').read_text(encoding='utf-8'); assert re.match(r'^---\n', content); print('frontmatter OK')"
```
Expected: `frontmatter OK`.

- [ ] **Step 3: Commit**

```bash
git add skills/memory-import/SKILL.md
git commit -m "docs(memory-import): add skill manifest"
```

---

### Task 7: Update both READMEs to list the new skill

**Files:**
- Modify: `README.md`
- Modify: `README.zh-TW.md`

- [ ] **Step 1: Update `README.md`'s file-structure listing**

Find the file-structure code block (introduced by `### 1) Skill Install Package (GitHub Release Pack)`). Change:

```text
└── memory-backup/
    ├── SKILL.md                 # GitHub backup/restore sub-skill spec
    └── scripts/
        ├── backup.py            # Export and safely push to a private GitHub repo
        ├── restore.py           # Restore the knowledge base from GitHub on any device
        └── export_jsonl.py      # ChromaDB → portable JSONL export
```

to:

```text
├── memory-backup/
│   ├── SKILL.md                 # GitHub backup/restore sub-skill spec
│   └── scripts/
│       ├── backup.py            # Export and safely push to a private GitHub repo
│       ├── restore.py           # Restore the knowledge base from GitHub on any device
│       └── export_jsonl.py      # ChromaDB → portable JSONL export
└── memory-import/
    ├── SKILL.md                 # External memory import sub-skill spec
    └── scripts/import.py        # Imports ChatGPT / Claude local / legacy auto-skill data
```

- [ ] **Step 2: Apply the same file-structure change to `README.zh-TW.md`**

Change:

```text
└── memory-backup/
    ├── SKILL.md                 # GitHub 備份／還原子技能說明
    └── scripts/
        ├── backup.py            # 匯出並安全推送至 GitHub 私有 Repo
        ├── restore.py           # 跨裝置從 GitHub 還原知識庫
        └── export_jsonl.py      # ChromaDB → 可攜式 JSONL 匯出
```

to:

```text
├── memory-backup/
│   ├── SKILL.md                 # GitHub 備份／還原子技能說明
│   └── scripts/
│       ├── backup.py            # 匯出並安全推送至 GitHub 私有 Repo
│       ├── restore.py           # 跨裝置從 GitHub 還原知識庫
│       └── export_jsonl.py      # ChromaDB → 可攜式 JSONL 匯出
└── memory-import/
    ├── SKILL.md                 # 外部記憶匯入子技能說明
    └── scripts/import.py        # 匯入 ChatGPT／Claude 本機／舊 auto-skill 資料
```

> **Note on scope:** As of this plan's writing, neither README's `npx skills add` example commands enumerate the 3 existing skill names in a comment (an earlier session already generalized those comments to talk about `--all` instead). Don't add a skill-name enumeration there — just leave those commands alone; `--skill '*'`/`--all` already cover the new skill automatically once it exists on disk.

- [ ] **Step 3: Verify**

```bash
grep -n "memory-import" README.md README.zh-TW.md
```
Expected: 1 match in each file (the file-structure block only).

- [ ] **Step 4: Commit**

```bash
git add README.md README.zh-TW.md
git commit -m "docs: list memory-import in the skill install package structure"
```
