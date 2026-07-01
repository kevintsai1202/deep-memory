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
