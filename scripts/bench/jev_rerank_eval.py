# -*- coding: utf-8 -*-
r"""Jev（TypeSafe System One）取代本機 bge-reranker 的可行性實測。

流程：對每個查詢以現行 hybrid（向量 + BM25 + RRF）取前 N 名候選，
分別用本機 bge-reranker-base（800 字截斷，與正式設定相同）與 Jev 的 Noul 題評分，
比較排序一致性、0.35 門檻下的接受集合差異、延遲與 token 用量。

注意：本腳本會把候選記憶內容送到 TypeSafe 雲端，只在使用者同意的評估時執行。
結果含記憶原文片段，預設寫到 ~/.deep-memory/logs/，不寫進 repo。

用法（PowerShell 7）：
  & "$HOME\.deep-memory\.venv\Scripts\python" scripts/bench/jev_rerank_eval.py --env-file D:\GitHub\transcript-review\.env
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# 讓本腳本能 import chroma-hybrid-search 的模組
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                           "skills", "chroma-hybrid-search", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API_URL = "https://api.typesafe.ai/v1/systemone"
# 每百萬輸入 token 美元（輸出免費），2026-09 起的公開價格
PRICE_PER_M_INPUT = 0.042

# 預設評估查詢：涵蓋 deep-memory 自身、測試流程、指令操作、領域知識、框架錯誤、AI 工具
DEFAULT_QUERIES = [
    "RAG 檢索效能優化 indexer reranker",
    "Playwright e2e 腳本放置與重跑",
    "PowerShell 長時指令 tee log 判斷卡死",
    "Agile PLM BOM 查詢 SQL",
    "Spring Boot Flyway migration 失敗",
    "Jev 中文斷句 Noul 切點",
]

# 題目變體：語言 × 模型。題意固定為「這筆筆記能不能幫上這個查詢」
QUESTION_ZH = {
    "type": "noul",
    "instructions": "`candidate` 是一筆工作記憶筆記。它是否包含能直接幫助回答或處理 `query` 的資訊？",
    "criteria": {
        "true": "筆記的主題與查詢直接相關，提供可用的做法、結論、指令、參數或事實",
        "false": "筆記只是與查詢共用少數字詞，實際主題不同，或內容無法幫助回答查詢",
    },
}
QUESTION_EN = {
    "type": "noul",
    "instructions": ("`candidate` is a work-memory note (often written in Chinese). "
                     "Does it contain information that directly helps answer or act on `query`?"),
    "criteria": {
        "true": "The note is about the same topic as the query and gives usable steps, conclusions, commands, parameters, or facts",
        "false": "The note only shares a few words with the query, is about a different topic, or would not help answer it",
    },
}
# 可用變體：「模型/題目語言」→ (模型, 語言, 題目)。2026-10-25 起 jev-latest 與 jev-preview 都指向 1.14.0
ALL_VARIANTS = {
    "jev-latest/zh": ("jev-latest", "zh", QUESTION_ZH),
    "jev-latest/en": ("jev-latest", "en", QUESTION_EN),
    "jev-preview/zh": ("jev-preview", "zh", QUESTION_ZH),
    "jev-1.14.0/zh": ("jev-1.14.0", "zh", QUESTION_ZH),
}
DEFAULT_VARIANTS = "jev-latest/zh,jev-latest/en,jev-preview/zh"


def load_api_key(env_file):
    """讀取金鑰（與 search.py 共用 jev_reranker.load_api_key），找不到就中止，不輸出金鑰內容"""
    import jev_reranker
    key = jev_reranker.load_api_key(env_file=env_file)
    if not key:
        raise SystemExit("找不到 TYPESAFE_API_KEY：請設環境變數或用 --env-file 指定 .env")
    return key


def ask_jev(api_key, model, question, query, candidate, timeout=60):
    """對一組（查詢, 候選）送一次 Noul 請求，回傳 (機率, 延遲秒數, 輸入 token, 實際模型, 錯誤訊息)"""
    body = json.dumps({
        "model": model,
        "state": {"query": query, "candidate": candidate},
        "questions": {"helpful": question},
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (float(data["answers"]["helpful"]["noul"]), time.time() - t,
                data.get("usage", {}).get("input_tokens", 0), data.get("model"), None)
    except urllib.error.HTTPError as e:
        return None, time.time() - t, 0, None, f"HTTP {e.code}: {e.read()[:200]!r}"
    except Exception as e:  # 網路錯誤、逾時
        return None, time.time() - t, 0, None, repr(e)


def spearman(a, b):
    """兩組分數的 Spearman 等級相關（無並列修正，候選數少時足夠）"""
    import numpy as np
    ra = np.argsort(np.argsort(-np.asarray(a)))
    rb = np.argsort(np.argsort(-np.asarray(b)))
    n = len(a)
    if n < 2:
        return None
    return float(1 - 6 * ((ra - rb) ** 2).sum() / (n * (n * n - 1)))


def write_label_sheet(records, variants, path, bge_tau, jev_tau, seed=7):
    """輸出盲標用的標註表（CSV，Excel 可直接開）。

    每個查詢只收「需要標的候選」：bge 與各 Jev 變體的前 3 名聯集，加上兩邊接受與否
    不一致的候選（bge ≥ bge_tau 與 Jev ≥ jev_tau 結果不同）。表內不放任何分數，
    同一查詢內的順序打亂，避免標註時被排名帶著走。label 欄填 1（有幫助）或 0。
    """
    import csv
    import random
    import numpy as np
    rnd = random.Random(seed)
    rows = []
    for qi, rec in enumerate(records):
        picked = set(np.argsort(-np.array(rec["bge"]))[:3].tolist())
        for key in variants:
            s = [x if x is not None else -1 for x in rec["jev"][key]["scores"]]
            picked |= set(np.argsort(-np.array(s))[:3].tolist())
            picked |= {i for i in range(len(s)) if (rec["bge"][i] >= bge_tau) != (s[i] >= jev_tau)}
        idx = sorted(picked)
        rnd.shuffle(idx)
        for i in idx:
            snippet = " ".join(rec["texts"][i].split())[:300]
            rows.append({"query_id": qi + 1, "query": rec["query"], "path": rec["paths"][i],
                         "title": rec["titles"][i], "snippet": snippet, "label": ""})
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["query_id", "query", "path", "title", "snippet", "label"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def score_labels(results_path, labels_path, bge_tau, jev_tau):
    """以人工標註計算各重排器的 P@3 與門檻過濾的精確率／召回率，印出比較表"""
    import csv
    import numpy as np
    with open(results_path, encoding="utf-8") as f:
        data = json.load(f)
    labels = {}
    with open(labels_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["label"].strip() in ("0", "1"):
                labels[(row["query"], row["path"])] = int(row["label"].strip())
    variants = list(data["records"][0]["jev"].keys())
    methods = {"local-bge": (lambda rec: rec["bge"], bge_tau)}
    for key in variants:
        methods[key] = ((lambda rec, k=key: [x if x is not None else -1 for x in rec["jev"][k]["scores"]]), jev_tau)
    print(f"已標註 {len(labels)} 筆；門檻 bge={bge_tau}、Jev={jev_tau}")
    print(f"{'方法':16s} {'P@1':>6s} {'P@3':>6s} {'接受精確率':>10s} {'接受召回率':>10s} {'接受數':>6s}")
    for name, (get, tau) in methods.items():
        p1, p3, tp, acc, rel = [], [], 0, 0, 0
        for rec in data["records"]:
            s = get(rec)
            lab = [labels.get((rec["query"], p)) for p in rec["paths"]]
            order = np.argsort(-np.array(s))
            top = [lab[i] for i in order[:3] if lab[i] is not None]
            if top:
                p3.append(sum(top) / len(top))
            if lab[order[0]] is not None:
                p1.append(lab[order[0]])
            for i, l in enumerate(lab):
                if l is None:
                    continue
                rel += l
                if s[i] >= tau:
                    acc += 1
                    tp += l
        print(f"{name:16s} {np.mean(p1):6.2f} {np.mean(p3):6.2f} {tp / max(acc, 1):10.2f} {tp / max(rel, 1):10.2f} {acc:6d}")


def main():
    """取候選 → 本機重排 → 各 Jev 變體平行評分 → 輸出比較報告與標註表"""
    import argparse as _ap
    import chromadb
    import numpy as np
    import onnx_models
    import search
    from kb_reader import read_knowledge_base, read_cold_notes

    parser = argparse.ArgumentParser(description="Jev 取代本機 reranker 的實測")
    parser.add_argument("--env-file", default=None, help="含 TYPESAFE_API_KEY 的 .env 路徑")
    parser.add_argument("--workspace", default=os.path.join(os.path.expanduser("~"), ".deep-memory"))
    parser.add_argument("--candidates", type=int, default=10, help="每個查詢評分的候選數")
    parser.add_argument("--max-chars", type=int, default=800, help="送進兩種重排器的字元上限")
    parser.add_argument("--concurrency", type=int, default=10, help="同一查詢的 Jev 平行請求數")
    parser.add_argument("--threshold", type=float, default=0.35, help="本機重排的接受門檻")
    parser.add_argument("--query", action="append", help="自訂查詢（可重複）；未給則用預設 6 個")
    parser.add_argument("--out", default=None, help="結果 JSON 路徑，預設 <workspace>/logs/jev-rerank-eval-<時間>.json")
    parser.add_argument("--dry-run", action="store_true", help="不連網、不讀金鑰，Jev 分數一律 0.5，只驗證流程")
    parser.add_argument("--variants", default=DEFAULT_VARIANTS,
                        help=f"逗號分隔的變體，可選：{', '.join(ALL_VARIANTS)}")
    parser.add_argument("--queries-file", default=None, help="查詢清單檔（UTF-8，一行一個，# 開頭為註解）")
    parser.add_argument("--jev-threshold", type=float, default=0.25, help="Jev 的接受門檻（標註表與評分用）")
    parser.add_argument("--score-labels", nargs=2, metavar=("RESULTS_JSON", "LABELS_CSV"),
                        help="不跑評測，改用已標註的 CSV 計算各方法的 P@3 與門檻精確率／召回率")
    args = parser.parse_args()

    if args.score_labels:
        score_labels(args.score_labels[0], args.score_labels[1], args.threshold, args.jev_threshold)
        return

    variant_keys = [v.strip() for v in args.variants.split(",") if v.strip()]
    VARIANTS = [ALL_VARIANTS[k] for k in variant_keys]

    global ask_jev
    if args.dry_run:
        api_key = None
        # 乾跑：不送出任何請求，固定回傳 0.5
        ask_jev = lambda *_a, **_k: (0.5, 0.0, 0, "dry-run", None)  # noqa: E731
    else:
        api_key = load_api_key(args.env_file)
    queries = args.query or DEFAULT_QUERIES
    if args.queries_file:
        with open(args.queries_file, encoding="utf-8") as f:
            queries = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]

    ws = args.workspace
    docs = read_knowledge_base(ws) + read_cold_notes(ws)
    collection = chromadb.PersistentClient(path=os.path.join(ws, "chroma_hybrid_db")).get_collection("hybrid_docs")
    e5 = onnx_models.get_shared_e5()
    bge = onnx_models.OnnxReranker()

    records = []  # 每個查詢的完整評分紀錄
    for q in queries:
        ns = _ap.Namespace(query=q, mode="hybrid", limit=args.candidates, candidate_limit=args.candidates,
                           min_score=0, skill=None, tag=None, memory_type=None)
        hits = search.run_search(docs, collection, ns, e5([q]))
        texts = [h["text"][:args.max_chars] for h in hits]

        t = time.time()
        bge_scores = [float(s) for s in bge.predict([[q, x] for x in texts])]
        bge_time = time.time() - t

        rec = {"query": q, "paths": [h["path"] for h in hits],
               "titles": [h["text"].splitlines()[0][:60] for h in hits],
               "texts": texts,
               "bge": bge_scores, "bge_time": round(bge_time, 2), "jev": {}}
        for model, lang, question in VARIANTS:
            t = time.time()
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                results = list(pool.map(lambda x: ask_jev(api_key, model, question, q, x), texts))
            wall = time.time() - t
            rec["jev"][f"{model}/{lang}"] = {
                "scores": [r[0] for r in results],
                "latency": [round(r[1], 2) for r in results],
                "input_tokens": sum(r[2] for r in results),
                "model": next((r[3] for r in results if r[3]), None),
                "errors": [r[4] for r in results if r[4]],
                "wall": round(wall, 2),
            }
        records.append(rec)
        print(f"[完成] {q}（bge {bge_time:.1f}s）")

    # 彙總：對每個變體算排序相關、前 3 名重疊、與 bge 接受集合最一致的 Jev 門檻
    summary = {}
    for model, lang, _ in VARIANTS:
        key = f"{model}/{lang}"
        rhos, top3, tokens, walls, lat, errs = [], [], 0, [], [], 0
        pairs = []  # (bge, jev) 全部配對，用來掃門檻
        for rec in records:
            j = rec["jev"][key]
            errs += len(j["errors"])
            ok = [i for i, s in enumerate(j["scores"]) if s is not None]
            if len(ok) < 2:
                continue
            b = [rec["bge"][i] for i in ok]
            s = [j["scores"][i] for i in ok]
            rhos.append(spearman(b, s))
            top3.append(len(set(np.argsort(-np.array(b))[:3]) & set(np.argsort(-np.array(s))[:3])))
            tokens += j["input_tokens"]
            walls.append(j["wall"])
            lat += j["latency"]
            pairs += list(zip(b, s))
        best = None
        for tau in [x / 20 for x in range(1, 20)]:
            agree = sum((bb >= args.threshold) == (ss >= tau) for bb, ss in pairs)
            if best is None or agree > best[1]:
                best = (tau, agree)
        summary[key] = {
            "spearman_mean": round(float(np.mean(rhos)), 3) if rhos else None,
            "top3_overlap_mean": round(float(np.mean(top3)), 2) if top3 else None,
            "best_tau_vs_bge_0.35": best[0] if best else None,
            "accept_agreement": f"{best[1]}/{len(pairs)}" if best else None,
            "wall_per_query_median": round(float(np.median(walls)), 2) if walls else None,
            "request_latency_median": round(float(np.median(lat)), 2) if lat else None,
            "input_tokens_total": tokens,
            "cost_usd_total": round(tokens / 1e6 * PRICE_PER_M_INPUT, 5),
            "errors": errs,
        }
    bge_times = [r["bge_time"] for r in records]
    summary["local-bge"] = {"predict_per_query_median": round(float(np.median(bge_times)), 2)}

    out = args.out or os.path.join(ws, "logs", f"jev-rerank-eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "records": records}, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"詳細結果：{out}")

    sheet = out[:-5] + ".labels.csv"
    n = write_label_sheet(records, variant_keys, sheet, args.threshold, args.jev_threshold)
    print(f"標註表（{n} 筆，label 欄填 1／0）：{sheet}")


if __name__ == "__main__":
    main()
