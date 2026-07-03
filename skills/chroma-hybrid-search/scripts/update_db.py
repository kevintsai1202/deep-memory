# -*- coding: utf-8 -*-
import os
import sys
import chromadb
from kb_reader import read_knowledge_base, read_cold_notes
from onnx_models import E5OnnxEmbeddingFunction

# Force UTF-8 stdout encoding on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def migrate_legacy_collection(client, collection):
    """一次性遷移：舊版 collection 持久化了 sentence_transformer embedding function，
    Chroma 在寫入時會惰性實例化它（import torch，新版最小環境沒裝會直接失敗）。
    偵測到舊設定就把資料（含向量）搬到不綁 EF 的新 collection 後原名換回。"""
    try:
        ef_name = ((collection.configuration_json or {}).get("embedding_function") or {}).get("name")
    except Exception:
        ef_name = None
    if ef_name != "sentence_transformer":
        return collection

    print("[遷移] 偵測到舊版 sentence_transformer EF 設定，搬移資料到無 EF 綁定的 collection ...")
    data = collection.get(include=["embeddings", "documents", "metadatas"])
    try:
        client.delete_collection("hybrid_docs_migrating")
    except Exception:
        pass
    new_col = client.create_collection("hybrid_docs_migrating")
    n = len(data["ids"])
    for i in range(0, n, 500):
        new_col.upsert(
            ids=data["ids"][i:i+500],
            embeddings=data["embeddings"][i:i+500],
            documents=data["documents"][i:i+500],
            metadatas=data["metadatas"][i:i+500],
        )
    if new_col.count() != n:
        raise RuntimeError(f"[遷移] 筆數不符（{new_col.count()} != {n}），中止且保留原 collection")
    client.delete_collection("hybrid_docs")
    new_col.modify(name="hybrid_docs")
    print(f"[遷移] 完成，共 {n} 筆")
    return new_col


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

    # 初始化 ChromaDB。不把 embedding function 綁在 collection 上（避免與既有
    # collection 持久化的 EF 設定衝突），向量一律在下方自行計算後以 embeddings= 傳入
    print("Initializing ChromaDB collection...")
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection("hybrid_docs")
    collection = migrate_legacy_collection(client, collection)

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
        # 舊條目（改動前寫入的冷庫記錄）沒有 project，省略此欄位，
        # 讓 search.py 篩選專案時能與「無專案」的舊資料區分開來
        if doc.get("project"):
            meta["project"] = doc["project"]
        metadatas.append(meta)

    # 讀回向量庫現況（含文本與 metadata），供增量比對使用
    existing = collection.get(include=["documents", "metadatas"])
    existing_docs = dict(zip(existing["ids"], existing["documents"]))
    existing_metas = dict(zip(existing["ids"], existing["metadatas"]))

    # 清除孤立向量：來源 .md／raw.jsonl 條目被刪除或改名後，upsert 不會自動移除舊向量，
    # 需主動比對「目前應存在的 ids」與「向量庫實際存在的 ids」，刪除差集
    stale_ids = set(existing_docs) - set(ids)
    if stale_ids:
        collection.delete(ids=list(stale_ids))
        print(f"[CLEANUP] Removed {len(stale_ids)} orphaned vector(s) for deleted/renamed source files.")

    # 增量索引：只對「新增」或「文本變動」的條目重新 embedding；
    # 文本相同但 metadata 變動的條目走 update（不重算向量，零 embedding 成本）
    embed_ids, embed_docs, embed_metas = [], [], []
    meta_ids, meta_metas = [], []
    skipped = 0
    for i, doc_id in enumerate(ids):
        if doc_id not in existing_docs or existing_docs[doc_id] != documents[i]:
            embed_ids.append(doc_id)
            embed_docs.append(documents[i])
            embed_metas.append(metadatas[i])
        elif existing_metas.get(doc_id) != metadatas[i]:
            meta_ids.append(doc_id)
            meta_metas.append(metadatas[i])
        else:
            skipped += 1

    print(f"[增量] 需重新向量化 {len(embed_ids)} 筆、僅更新 metadata {len(meta_ids)} 筆、內容未變跳過 {skipped} 筆")

    if embed_ids:
        print(f"Upserting {len(embed_ids)} documents into vector database at {db_path}...")
        # 到這裡才載入 embedding 模型：零變動或 metadata-only 的執行完全不付模型載入成本
        embeddings = E5OnnxEmbeddingFunction()(embed_docs)
        collection.upsert(ids=embed_ids, embeddings=embeddings, documents=embed_docs, metadatas=embed_metas)
    if meta_ids:
        collection.update(ids=meta_ids, metadatas=meta_metas)
    if not embed_ids and not meta_ids and not stale_ids:
        print("[增量] 向量庫已是最新，無需任何寫入。")
    print("Database build/update finished successfully!")

if __name__ == "__main__":
    main()
