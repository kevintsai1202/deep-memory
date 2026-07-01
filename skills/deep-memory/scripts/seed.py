# -*- coding: utf-8 -*-
"""
seed.py
首次安裝 deep-memory 後，將預載種子知識庫（resources/seeds/）
複製到使用者的 knowledge-base/ 目錄，作為初始知識基礎。

種子來源：
  - 記憶管理核心原則（Mem0、MemGPT、Claude、GPT 官方指引）
  - 知識檢索策略與 RAG 模式（LangChain、ChromaDB、BGE）
  - 知識組織與分類架構（Zettelkasten、PKM）
  - Context Window 管理策略（MemGPT、Anthropic、LangChain）
"""
import os
import sys
import json
import shutil
import argparse

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# 種子資料所在目錄（相對於本腳本）
SEEDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resources", "seeds")

def load_seed_index():
    """載入種子索引 _index.json"""
    index_path = os.path.join(SEEDS_DIR, "_index.json")
    if not os.path.exists(index_path):
        print(f"[ERROR] 找不到種子索引：{index_path}")
        sys.exit(1)
    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_existing_index(kb_index_path):
    """載入使用者現有的 knowledge-base/_index.json（若存在）"""
    if not os.path.exists(kb_index_path):
        return {"_version": 1, "categories": []}
    with open(kb_index_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_index(index_data, kb_index_path):
    """儲存更新後的 _index.json"""
    with open(kb_index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

def main():
    parser = argparse.ArgumentParser(description="Seed initial knowledge base from deep-memory bundled seeds")
    parser.add_argument("--workspace", type=str, default=os.getcwd(),
                        help="工作目錄（knowledge-base/ 的父目錄），預設為當前目錄")
    parser.add_argument("--force", action="store_true",
                        help="強制覆蓋：若種子分類已存在，以種子版本覆蓋（預設為跳過已存在的分類）")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.workspace)
    kb_dir = os.path.join(base_dir, "knowledge-base")
    kb_index_path = os.path.join(kb_dir, "_index.json")

    os.makedirs(kb_dir, exist_ok=True)

    # 載入種子索引
    seed_index = load_seed_index()
    seed_categories = seed_index.get("categories", [])

    # 載入現有使用者索引
    user_index = load_existing_index(kb_index_path)
    existing_ids = {cat["id"] for cat in user_index.get("categories", [])}

    print(f"[INFO] 種子知識庫包含 {len(seed_categories)} 個分類")
    print(f"[INFO] 使用者現有分類：{len(existing_ids)} 個")
    print()

    added = 0
    skipped = 0

    for cat in seed_categories:
        cat_id = cat["id"]
        cat_file = cat["file"]
        src_path = os.path.join(SEEDS_DIR, cat_file)
        dest_path = os.path.join(kb_dir, cat_file)

        if cat_id in existing_ids and not args.force:
            print(f"  [SKIP] {cat_file}（分類 '{cat_id}' 已存在，使用 --force 可覆蓋）")
            skipped += 1
            continue

        # 複製 MD 檔案
        if not os.path.exists(src_path):
            print(f"  [WARN] 找不到種子檔案：{src_path}，略過")
            continue

        shutil.copy2(src_path, dest_path)
        print(f"  [OK] 安裝種子：{cat_file} — {cat['title']}")

        # 更新 _index.json（若尚未存在則新增）
        if cat_id not in existing_ids:
            user_index["categories"].append({
                "id": cat_id,
                "file": cat_file,
                "title": cat["title"],
                "keywords": cat.get("keywords", []),
                "_seed": True  # 標記為種子條目，便於未來識別
            })
            existing_ids.add(cat_id)
        else:
            # force 模式：更新現有條目的 keywords
            for existing_cat in user_index["categories"]:
                if existing_cat["id"] == cat_id:
                    existing_cat["keywords"] = cat.get("keywords", existing_cat.get("keywords", []))
                    break

        added += 1

    # 儲存更新後的 _index.json
    save_index(user_index, kb_index_path)

    print()
    print(f"✅ 種子安裝完成：新增 {added} 個分類，跳過 {skipped} 個")
    print()
    print("【下一步】請執行以下指令將種子知識庫向量化，讓 RAG 能搜尋到這些內容：")
    print()
    print("  Windows:      .venv\\Scripts\\python skills/chroma-hybrid-search/scripts/update_db.py")
    print("  Linux/macOS:  .venv/bin/python skills/chroma-hybrid-search/scripts/update_db.py")

if __name__ == "__main__":
    main()
