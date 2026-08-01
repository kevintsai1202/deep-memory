# -*- coding: utf-8 -*-
"""
update_db.py
向量索引更新的 CLI 進入點。實際邏輯全在 indexer.py，
好讓 search.py 的自動補索引與這裡走完全相同的一套判斷。
"""
import os
import sys
import argparse

import indexer

# Force UTF-8 stdout encoding on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description="ChromaDB Indexing Script")
    # 優先從環境變數 DEEP_MEMORY_WORKSPACE 取得工作目錄，否則預設為使用者家目錄下的 .deep-memory
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE")
    if not default_ws:
        default_ws = os.path.join(os.path.expanduser("~"), ".deep-memory")

    parser.add_argument("--workspace", type=str, default=default_ws, help="Workspace root path")
    parser.add_argument("--rebuild", action="store_true",
                        help="強制重算全部條目的向量（約需數分鐘），"
                             "僅在需要讓向量與最新文字格式完全對應時使用")
    args = parser.parse_args()

    result = indexer.sync(os.path.abspath(args.workspace),
                          trigger="update_db", rebuild=args.rebuild)
    if result["embedded"] == 0 and result["meta_only"] == 0 and result["stale"] == 0:
        print("[增量] 向量庫已是最新，無需任何寫入。")
    print("Database build/update finished successfully!")


if __name__ == "__main__":
    main()
