# -*- coding: utf-8 -*-
"""
export_jsonl.py
匯出 ChromaDB 向量庫中的所有條目為 JSONL 格式，
作為可攜式文字備份，避免直接備份不可 diff 的二進位 SQLite 檔案。
"""
import os
import sys
import json
import argparse
import chromadb
from chromadb.utils import embedding_functions

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def main():
    parser = argparse.ArgumentParser(description="Export ChromaDB to JSONL backup")
    parser.add_argument("--workspace", type=str, default=os.getcwd(), help="Workspace root path")
    parser.add_argument("--output", type=str, default="backup/chroma_export.jsonl", help="Output JSONL file path")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.workspace)
    db_path = os.path.join(base_dir, "chroma_hybrid_db")
    output_path = os.path.join(base_dir, args.output)

    if not os.path.exists(db_path):
        print(f"[ERROR] ChromaDB not found at {db_path}. Run update_db.py first.")
        sys.exit(1)

    # 確保輸出目錄存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 初始化 ChromaDB（不帶 embedding function，僅讀取已存在的向量庫資料）
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="intfloat/multilingual-e5-small",
        device="cpu"
    )
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection("hybrid_docs", embedding_function=embedding_fn)

    # 取出所有條目（不含向量本身，節省儲存空間）
    results = collection.get(include=["documents", "metadatas"])

    if not results or not results.get("ids"):
        print("[WARN] No entries found in ChromaDB.")
        return

    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for doc_id, doc_text, doc_meta in zip(
            results["ids"], results["documents"], results["metadatas"]
        ):
            entry = {
                "id": doc_id,
                "text": doc_text,
                "metadata": doc_meta
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            count += 1

    print(f"[OK] Exported {count} entries to {output_path}")

if __name__ == "__main__":
    main()
