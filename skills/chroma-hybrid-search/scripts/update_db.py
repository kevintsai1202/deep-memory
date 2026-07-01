# -*- coding: utf-8 -*-
import os
import sys
import chromadb
from chromadb.utils import embedding_functions
from kb_reader import read_knowledge_base, read_cold_notes

# Force UTF-8 stdout encoding on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ChromaDB Indexing Script")
    # 優先從環境變數 DEEP_MEMORY_WORKSPACE 取得工作目錄，否則預設為使用者家目錄下的 .deep-memory
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE")
    if not default_ws:
        default_ws = os.path.join(os.path.expanduser("~"), ".deep-memory")

    parser.add_argument("--workspace", type=str, default=default_ws, help="Workspace root path")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.workspace)
    db_path = os.path.join(base_dir, "chroma_hybrid_db")

    # 讀取熱庫（依 "## 🔧" 條目切段，而非整份檔案一份文件）
    print(f"[熱庫] 讀取 knowledge-base/ 與 experience/ ...")
    hot_docs = read_knowledge_base(base_dir)
    print(f"[熱庫] 共 {len(hot_docs)} 個條目（已依 ## 🔧 切段）")

    # 讀取冷庫（raw.jsonl 條目）
    print(f"[冷庫] 讀取 cold-notes/raw.jsonl ...")
    cold_docs = read_cold_notes(base_dir)
    print(f"[冷庫] 共 {len(cold_docs)} 筆條目")

    docs = hot_docs + cold_docs
    if not docs:
        print(f"[WARN] 無任何文件可索引（熱庫與冷庫皆為空），請先建立知識庫。")
        return

    print(f"[總計] 共 {len(docs)} 筆資料準備寫入向量庫")

    # 初始化 ChromaDB
    print("Initializing ChromaDB collection...")
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="intfloat/multilingual-e5-small",
        device="cpu"
    )
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection("hybrid_docs", embedding_function=embedding_fn)

    # 準備批次寫入資料
    ids = []
    documents = []
    metadatas = []

    for doc in docs:
        ids.append(doc["path"])
        documents.append(doc["text"])
        meta = {"path": doc["path"], "source": doc.get("source", "hot")}
        if doc.get("skill"):
            meta["skill"] = doc["skill"]
        # Chroma 的陣列型 metadata 不允許空陣列，沒有標籤就整個欄位省略
        if doc.get("tags"):
            meta["tags"] = doc["tags"]
        metadatas.append(meta)

    # 清除孤立向量：來源 .md／raw.jsonl 條目被刪除或改名後，upsert 不會自動移除舊向量，
    # 需主動比對「目前應存在的 ids」與「向量庫實際存在的 ids」，刪除差集
    existing_ids = set(collection.get(include=[])["ids"])
    stale_ids = existing_ids - set(ids)
    if stale_ids:
        collection.delete(ids=list(stale_ids))
        print(f"[CLEANUP] Removed {len(stale_ids)} orphaned vector(s) for deleted/renamed source files.")

    print(f"Upserting {len(ids)} documents into vector database at {db_path}...")
    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )
    print("Database build/update finished successfully!")

if __name__ == "__main__":
    main()
