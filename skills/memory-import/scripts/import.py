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
