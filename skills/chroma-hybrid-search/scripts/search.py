# -*- coding: utf-8 -*-
import os
import sys
import argparse
import json
import re
import chromadb
from rank_bm25 import BM25Okapi
from kb_reader import read_knowledge_base, read_cold_notes
from onnx_models import OnnxReranker, get_shared_e5
from jev_reranker import JevError, JevReranker, load_reranker_config

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

def _make_hit(doc, **extra):
    """組裝單筆檢索結果。

    冷庫條目附上行號，讓人能從檢索結果直接跳回 raw.jsonl 的來源行——
    內容定址後 path 只剩雜湊，少了這個欄位就失去可追溯性。
    熱庫條目沒有 line，欄位整個省略而非填 None。
    """
    hit = {"path": doc["path"], "text": doc["text"], **extra}
    if doc.get("line") is not None:
        hit["line"] = doc["line"]
    return hit

def _mark_jev_failed(holder, reason):
    """記下 Jev 失敗原因並警告（stderr）；之後的檢索輪直接用本機，不再付一次逾時成本"""
    if not holder.get("jev_failed"):
        print(f"[WARN] Jev 重排失敗（{reason}），改用本機 bge-reranker", file=sys.stderr)
    holder["jev_failed"] = reason


def run_search(docs, collection, args, query_embedding=None, extra_where=None, reranker_holder=None):
    """對給定的 docs 子集執行一次完整的 vector + BM25 + RRF + rerank 檢索，回傳 retrieved_docs 列表。

    reranker_holder 是呼叫端持有的 dict，兩段式檢索（專案輪 → 全庫輪）共用：
    - "config"：load_reranker_config() 的結果，決定用本機 bge 還是 Jev；沒有就用本機
    - "instance"／"jev"：延遲建立的重排器，同一行程只載入一次（bge 模型約 1.1 GB）
    - "scores"：已評過分的候選，不再重算
    - "jev_failed"／"used"：Jev 失敗原因與這輪實際用的重排器，供輸出標示分數尺度
    未傳入時每次呼叫各自載入本機模型，行為同舊版。
    """
    if not docs:
        return []

    where_clauses = []
    if args.skill:
        where_clauses.append({"skill": args.skill})
    if args.tag:
        where_clauses.append({"tags": {"$contains": args.tag}})
    if args.memory_type:
        where_clauses.append({"memory_type": args.memory_type})
    if extra_where:
        where_clauses.append(extra_where)
    where = where_clauses[0] if len(where_clauses) == 1 else ({"$and": where_clauses} if where_clauses else None)

    # 1. Vector Search
    vector_ranked = []
    if args.mode in ["hybrid-rerank", "hybrid", "vector"]:
        vector_res = collection.query(
            query_embeddings=query_embedding,
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
    else:  # hybrid / hybrid-rerank
        rrf_scores = rrf(vector_ranked, bm25_ranked)
        sorted_candidates = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
        candidates = [item[0] for item in sorted_candidates[:args.candidate_limit]]

    # Map candidate paths to full text
    doc_map = {doc["path"]: doc for doc in docs}
    retrieved_docs = []

    # 4. Reranking (optional)
    if args.mode == "hybrid-rerank" and candidates:
        if reranker_holder is None:
            reranker_holder = {}
        # 分數快取：(重排器, 查詢, path) → 分數。逐筆評分、分數與同批其他候選無關，
        # 專案輪評過的候選在全庫輪可直接沿用；鍵含重排器，因為 Jev 與 bge 的分數尺度不同，
        # 專案輪用 Jev、全庫輪退回 bge 時不能混用
        score_cache = reranker_holder.setdefault("scores", {})
        # 只截斷送進重排器的文字（序列越短推論越快），回傳結果仍用完整原文；0 代表不截斷
        max_chars = getattr(args, "rerank_max_chars", 800)
        paths = [c for c in candidates if c in doc_map]

        def rerank_text(p):
            text = doc_map[p]["text"]
            return text[:max_chars] if max_chars and max_chars > 0 else text

        cfg = reranker_holder.get("config") or {"provider": "local"}
        use_jev = cfg.get("provider") == "jev" and not reranker_holder.get("jev_failed")
        if use_jev and not cfg["jev"].get("api_key"):
            _mark_jev_failed(reranker_holder, "missing TYPESAFE_API_KEY")
            use_jev = False

        if use_jev:
            todo = [p for p in paths if ("jev", args.query, p) not in score_cache]
            if todo:
                try:
                    if reranker_holder.get("jev") is None:
                        jc = cfg["jev"]
                        reranker_holder["jev"] = JevReranker(api_key=jc["api_key"], model=jc["model"],
                                                             timeout=jc["timeout"], concurrency=jc["concurrency"])
                    scores = reranker_holder["jev"].predict([[args.query, rerank_text(p)] for p in todo])
                    for p, score in zip(todo, scores):
                        score_cache[("jev", args.query, p)] = float(score)
                except Exception as exc:  # 任何失敗都整輪退回本機，搜尋不能因雲端問題中斷
                    _mark_jev_failed(reranker_holder, str(exc) if isinstance(exc, JevError) else type(exc).__name__)
                    use_jev = False

        provider = "jev" if use_jev else "local"
        if not use_jev:
            todo = [p for p in paths if ("local", args.query, p) not in score_cache]
            if todo:
                # 延遲到確定有候選要評分才載入模型
                if reranker_holder.get("instance") is None:
                    reranker_holder["instance"] = OnnxReranker()
                scores = reranker_holder["instance"].predict([[args.query, rerank_text(p)] for p in todo])
                for p, score in zip(todo, scores):
                    score_cache[("local", args.query, p)] = float(score)

        # 門檻在確定這輪實際用哪個重排器之後才決定：--min-score 是 bge 尺度，Jev 用設定檔的 min_score
        min_score = cfg["jev"]["min_score"] if use_jev else args.min_score
        reason = reranker_holder.get("jev_failed")
        reranker_holder["used"] = provider if use_jev or not reason else f"local (jev fallback: {reason})"

        if paths:
            scored_candidates = []
            for path in paths:
                score = score_cache[(provider, args.query, path)]
                if score >= min_score:
                    scored_candidates.append((path, score))

            scored_candidates.sort(key=lambda x: x[1], reverse=True)
            for path, score in scored_candidates[:args.limit]:
                retrieved_docs.append(_make_hit(doc_map[path], rerank_score=score))
    else:
        for path in candidates[:args.limit]:
            if path in doc_map:
                retrieved_docs.append(_make_hit(
                    doc_map[path],
                    score=bm25_scores.get(path, 0.0) if args.mode == "bm25" else None))

    return retrieved_docs


def main():
    parser = argparse.ArgumentParser(description="ChromaDB Hybrid Search CLI")
    parser.add_argument("--query", type=str, required=True, help="Search query")
    parser.add_argument("--mode", type=str, default="hybrid-rerank",
                        choices=["hybrid-rerank", "hybrid", "vector", "bm25"],
                        help="Search mode")
    parser.add_argument("--limit", type=int, default=4, help="Maximum results to return")
    parser.add_argument("--candidate-limit", type=int, default=10, help="Candidates to fetch before reranking")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="本機 bge 重排分數門檻；用 Jev 時改用 config.json 的 reranker.jev.min_score（預設 0.6）")
    parser.add_argument("--reranker", type=str, default=None, choices=["local", "jev"],
                        help="重排器；未指定時依 DEEP_MEMORY_RERANKER 環境變數、<workspace>/config.json，預設 local")
    parser.add_argument("--rerank-max-chars", type=int, default=800,
                        help="送進重排模型的每筆文字上限字元數（0 代表不截斷）；只影響排序，回傳全文不受影響")
    # 優先從環境變數 DEEP_MEMORY_WORKSPACE 取得工作目錄，否則預設為使用者家目錄下的 .deep-memory
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE")
    if not default_ws:
        default_ws = os.path.join(os.path.expanduser("~"), ".deep-memory")

    parser.add_argument("--workspace", type=str, default=default_ws, help="Workspace root path")
    parser.add_argument("--skill", type=str, default=None, help="Filter to entries tagged with this skill-id (exact match)")
    parser.add_argument("--tag", type=str, default=None, help="Filter to entries whose tags array contains this value")
    parser.add_argument("--memory-type", type=str, default=None,
                        choices=["knowledge", "experience", "both"],
                        help="Filter cold-store entries by memory_type")
    parser.add_argument("--project", type=str, default=None,
                        help="限定搜尋範圍為此專案；未指定時自動取當前工作目錄的資料夾名稱。傳 'all' 或空字串可停用專案篩選、直接搜全部")
    parser.add_argument("--no-auto-index", action="store_true",
                        help="停用查詢前的自動補索引（預設會先把新寫入的條目補進索引）")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.workspace)

    # 查詢前自動把新寫入的條目補進索引，使用者不必手動跑 update_db.py。
    # 這裡的成本幾乎全被本來就要付的 import 與模型載入吸收；
    # ensure_fresh 內部已做例外處理，失敗只會警告不會中斷查詢。
    # 補索引時已讀過全部來源檔，直接複用它回傳的 docs，不再讀第二次。
    all_docs = None
    if not args.no_auto_index:
        import indexer
        _, all_docs = indexer.ensure_fresh(base_dir, return_docs=True)

    # 停用自動補索引或補索引失敗時才自行讀檔
    if all_docs is None:
        all_docs = read_knowledge_base(base_dir) + read_cold_notes(base_dir)

    # 套用 --skill / --tag 過濾，讓 BM25 端跟向量端（見下方 where 子句）看到一致的候選集合
    if args.skill:
        all_docs = [d for d in all_docs if d.get("skill") == args.skill]
    if args.tag:
        all_docs = [d for d in all_docs if args.tag in d.get("tags", [])]
    if args.memory_type:
        all_docs = [d for d in all_docs if d.get("memory_type") == args.memory_type]

    if not all_docs:
        print(json.dumps({"error": f"No documents found matching the given filters at {base_dir}."}))
        return

    db_path = os.path.join(base_dir, "chroma_hybrid_db")

    # Initialize ChromaDB
    # 不把 embedding function 綁在 collection 上（避免與既有 collection 持久化的
    # EF 設定衝突）；query 向量在此先算好，兩段式檢索（專案優先→全庫）共用同一份
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection("hybrid_docs")
    query_embedding = None
    if args.mode in ["hybrid-rerank", "hybrid", "vector"]:
        # embedding 走輕量 ONNX 路徑（不載入 torch，見 onnx_models.py）；
        # 與 ensure_fresh 共用同一個實例，補索引時已載入就不再載第二次
        query_embedding = get_shared_e5()([args.query])

    # 專案篩選：先縮小到當前專案的條目再搜，找不到符合的結果才退回全庫搜尋
    # （沒有 project 欄位的舊條目或熱庫條目視為「不屬於任何專案」，只會出現在退回全庫的那次搜尋）
    project = args.project if args.project is not None else os.path.basename(os.getcwd())
    scope = "all"
    retrieved_docs = []
    # 兩輪檢索共用的重排器設定、實例與分數快取：重排器只建立一次、同一候選只評一次分
    reranker_holder = {"instance": None, "scores": {}}
    if args.mode == "hybrid-rerank":
        reranker_holder["config"] = load_reranker_config(base_dir, args.reranker)

    if project and project.lower() != "all":
        project_docs = [d for d in all_docs if d.get("project") == project]
        if project_docs:
            retrieved_docs = run_search(project_docs, collection, args, query_embedding,
                                        extra_where={"project": project}, reranker_holder=reranker_holder)
            if retrieved_docs:
                scope = f"project:{project}"

    if not retrieved_docs:
        retrieved_docs = run_search(all_docs, collection, args, query_embedding, reranker_holder=reranker_holder)
        scope = "all" if scope == "all" else f"{scope} -> all (fallback, no project match)"

    # reranker 標示 rerank_score 的尺度：jev／local／local (jev fallback: 原因)；未重排為 none
    reranker = reranker_holder.get("used", "none")
    print(json.dumps({"scope": scope, "reranker": reranker, "results": retrieved_docs},
                     ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
