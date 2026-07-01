# -*- coding: utf-8 -*-
import os
import sys
import argparse
import json
import re
import chromadb
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder
from kb_reader import read_knowledge_base, read_cold_notes

# Force UTF-8 stdout encoding on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# General tokenizer for Chinese/English
def tokenize(text):
    tokens = []
    # Chinese characters
    chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
    tokens.extend(chinese_chars)
    # English words, numbers, identifiers
    english_words = re.findall(r'[a-zA-Z0-9_-]+', text)
    tokens.extend([w.lower() for w in english_words])
    return tokens

# k=60 is the standard Reciprocal Rank Fusion constant from Cormack et al. — it
# dampens the influence of low ranks so results found by only one method don't
# dominate; the RRF literature default, not tuned specifically for this corpus.
def rrf(vector_ranked, bm25_ranked, k=60):
    rrf_scores = {}
    for rank, doc_id in enumerate(vector_ranked):
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + (rank + 1))
    for rank, doc_id in enumerate(bm25_ranked):
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + (rank + 1))
    return rrf_scores

def main():
    parser = argparse.ArgumentParser(description="ChromaDB Hybrid Search CLI")
    parser.add_argument("--query", type=str, required=True, help="Search query")
    parser.add_argument("--mode", type=str, default="hybrid-rerank", 
                        choices=["hybrid-rerank", "hybrid", "vector", "bm25"], 
                        help="Search mode")
    parser.add_argument("--limit", type=int, default=4, help="Maximum results to return")
    parser.add_argument("--candidate-limit", type=int, default=10, help="Candidates to fetch before reranking")
    parser.add_argument("--min-score", type=float, default=0.0, help="Minimum rerank score filter")
    # 優先從環境變數 DEEP_MEMORY_WORKSPACE 取得工作目錄，否則預設為使用者家目錄下的 .deep-memory
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE")
    if not default_ws:
        default_ws = os.path.join(os.path.expanduser("~"), ".deep-memory")

    parser.add_argument("--workspace", type=str, default=default_ws, help="Workspace root path")
    parser.add_argument("--skill", type=str, default=None, help="Filter to entries tagged with this skill-id (exact match)")
    parser.add_argument("--tag", type=str, default=None, help="Filter to entries whose tags array contains this value")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.workspace)
    docs = read_knowledge_base(base_dir) + read_cold_notes(base_dir)

    # 套用 --skill / --tag 過濾，讓 BM25 端跟向量端（見下方 where 子句）看到一致的候選集合
    if args.skill:
        docs = [d for d in docs if d.get("skill") == args.skill]
    if args.tag:
        docs = [d for d in docs if args.tag in d.get("tags", [])]

    if not docs:
        print(json.dumps({"error": f"No documents found matching the given filters at {base_dir}."}))
        return

    db_path = os.path.join(base_dir, "chroma_hybrid_db")

    # Initialize ChromaDB
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="intfloat/multilingual-e5-small",
        device="cpu"
    )
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection("hybrid_docs", embedding_function=embedding_fn)

    # 1. Vector Search
    # 用 Chroma 原生 where 子句過濾，$contains 需要 chromadb>=1.5.0（見 requirements.txt 釘的 1.5.9）
    where_clauses = []
    if args.skill:
        where_clauses.append({"skill": args.skill})
    if args.tag:
        where_clauses.append({"tags": {"$contains": args.tag}})
    where = where_clauses[0] if len(where_clauses) == 1 else ({"$and": where_clauses} if where_clauses else None)

    vector_ranked = []
    if args.mode in ["hybrid-rerank", "hybrid", "vector"]:
        vector_res = collection.query(
            query_texts=[args.query],
            n_results=min(args.candidate_limit, len(docs)),
            where=where
        )
        if vector_res and vector_res["ids"]:
            vector_ranked = vector_res["ids"][0]

    # 2. BM25 Search
    bm25_ranked = []
    bm25_scores = {}
    if args.mode in ["hybrid-rerank", "hybrid", "bm25"]:
        corpus = [doc["text"] for doc in docs]
        tokenized_corpus = [tokenize(text) for text in corpus]
        bm25 = BM25Okapi(tokenized_corpus)
        query_tokens = tokenize(args.query)
        doc_scores = bm25.get_scores(query_tokens)
        
        bm25_ranked_indices = sorted(range(len(doc_scores)), key=lambda i: doc_scores[i], reverse=True)
        bm25_ranked = [docs[idx]["path"] for idx in bm25_ranked_indices if doc_scores[idx] > 0]
        bm25_scores = {docs[idx]["path"]: doc_scores[idx] for idx in range(len(docs))}

    # 3. Retrieval / RRF Fusion
    candidates = []
    if args.mode == "vector":
        candidates = vector_ranked[:args.limit]
    elif args.mode == "bm25":
        candidates = bm25_ranked[:args.limit]
    else: # hybrid / hybrid-rerank
        rrf_scores = rrf(vector_ranked, bm25_ranked)
        sorted_candidates = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
        candidates = [item[0] for item in sorted_candidates[:args.candidate_limit]]

    # Map candidate paths to full text
    doc_map = {doc["path"]: doc for doc in docs}
    retrieved_docs = []
    
    # 4. Reranking (optional)
    if args.mode == "hybrid-rerank" and candidates:
        reranker = CrossEncoder("BAAI/bge-reranker-base", device="cpu")
        pairs = [[args.query, doc_map[path]["text"]] for path in candidates if path in doc_map]
        
        if pairs:
            scores = reranker.predict(pairs)
            scored_candidates = []
            for path, score in zip([c for c in candidates if c in doc_map], scores):
                if score >= args.min_score:
                    scored_candidates.append((path, float(score)))
            
            # Sort by rerank score descending
            scored_candidates.sort(key=lambda x: x[1], reverse=True)
            for path, score in scored_candidates[:args.limit]:
                retrieved_docs.append({
                    "path": path,
                    "rerank_score": score,
                    "text": doc_map[path]["text"]
                })
    else:
        # Standard hybrid or vector retrieval (no rerank)
        for path in candidates[:args.limit]:
            if path in doc_map:
                retrieved_docs.append({
                    "path": path,
                    "score": bm25_scores.get(path, 0.0) if args.mode == "bm25" else None,
                    "text": doc_map[path]["text"]
                })

    # Print final JSON
    print(json.dumps(retrieved_docs, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
