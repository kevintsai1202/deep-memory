#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refine_prepare.py
冷庫精煉的前置準備（不呼叫 LLM）：
1. 讀出待精煉（quality 不是 reviewed）的冷庫條目，保留行號
2. 依「專案 + 主題」分群並切成大小受限的工作包；近似重複的條目併入同一包並標註
3. 以既有混合檢索（向量 + BM25 + RRF；--rerank 時再經重排模型）替每筆找出最相近的熱庫條目，作為去重與目標分類依據
4. 輸出 refine-work/<run-id>/pack-NN.md 與 manifest.json，供平行子代理起草、refine_apply.py 套用

檢索需要 chroma-hybrid-search 的虛擬環境（chromadb、onnxruntime）；加 --no-candidates 可只用標準函式庫。
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 不具主題意義的 skill 值：遇到時改用第一個 tag 當主題
GENERIC_SKILLS = {"", "general", "none", "deep-memory"}
# 近似重複判定：主題相似度門檻（difflib ratio）
DUPLICATE_TOPIC_RATIO = 0.85
# 候選條目的重排分數（--rerank）達此值才視為「強相關」，用來推定目標檔案
STRONG_CANDIDATE_SCORE = 0.5
# 預設快速模式以向量相似度計分：實測相關條目約 0.90~0.94、無關約 0.86，取 0.89 為強相關門檻
STRONG_VECTOR_SIMILARITY = 0.89
# 重排時文件只取前 N 字：全文重排每對約 0.5~1 秒，截斷後排名前段幾乎不變
RERANK_TEXT_CHARS = 400
# 改用 Jev 雲端重排時的強相關門檻與截斷字數：與 search.py 相同，取自 2026-10-25 的 149 筆盲標評測
STRONG_JEV_SCORE = 0.6
JEV_TEXT_CHARS = 800
# 無法判定目標時的標記
UNKNOWN_TARGET = "(新分類？)"
# 無主題時的標記
UNCATEGORIZED = "(未分類)"

# 子代理草稿格式契約：寫在每個工作包開頭，refine_apply.py 依此解析
DRAFT_FORMAT = """## 你的任務

你是冷庫精煉子代理，只處理本工作包列出的條目，不要讀取其他冷庫內容。
對每一筆判斷：值得保留（可重用做法、高成本踩坑與修正、關鍵參數、使用者偏好、多次嘗試才成功的解法、可重用範本）→ 精煉；
一次性問答、純狀態更新、純概念說明、無法重現、已被下列「既有熱庫候選」涵蓋 → 略過。
標註「可能重複」的條目請一併判斷，只保留一份精煉內容。多筆同主題請合併成一條，不要逐筆照抄。

## 輸出格式（寫到與本檔同名的 `.drafts.md`，例如 pack-01.md → pack-01.drafts.md）

新增完整條目（knowledge-base 用 Best Practices 格式；experience 另加 Skill、keywords）：

    ### TARGET: knowledge-base/<分類>.md
    ## 🔧 <短標題>
    **Date:** YYYY-MM-DD
    **Context:** 一句話說明情境
    **Best Practices:**
    - 重點
    **Source cold store entries:** cold-notes/raw.jsonl#L<行號>, L<行號>

追加條列點到既有條目（標題需與熱庫中 `## 🔧` 後的文字完全一致）：

    ### APPEND-TO: knowledge-base/<分類>.md :: <既有條目標題>
    - 新增重點
    **Source cold store entries:** cold-notes/raw.jsonl#L<行號>

建立新分類（僅在沒有任何既有分類適合時；其後接一或多個完整條目）：

    ### NEW CATEGORY: <id> | <標題> | 關鍵字1, 關鍵字2
    ## 🔧 <短標題>
    ...

補索引關鍵字（可省略）：

    ## KEYWORDS
    - <分類 id>: 關鍵字1, 關鍵字2

最後必須列出本包「每一筆」的處理結果（略過只寫原因，不要寫長文）：

    ## REVIEWED
    L<行號> | promoted→<目標檔>
    L<行號> | skipped: <原因>
"""


def load_pending(raw_path, since=None, project=None):
    """讀取待精煉的冷庫條目並附上行號；可依日期下限與專案過濾。"""
    raw_path = Path(raw_path)
    if not raw_path.exists():
        return []
    notes = []
    for line_no, line in enumerate(raw_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("quality") == "reviewed":
            continue
        if since and (entry.get("date") or "") < since:
            continue
        if project and entry.get("project") != project:
            continue
        entry["_line_no"] = line_no
        notes.append(entry)
    return notes


def note_hash(note):
    """以主題與內容計算穩定雜湊，供 refine_apply 確認行號沒有錯位。"""
    raw = f"{note.get('topic', '')}\n{note.get('content', '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def theme_of(note):
    """取條目主題：優先用 skill，泛用 skill 時改用第一個不等於專案名的 tag。"""
    skill = (note.get("skill") or "").strip()
    if skill.lower() not in GENERIC_SKILLS:
        return skill
    project = (note.get("project") or "").lower()
    for tag in note.get("tags") or []:
        tag = str(tag).strip()
        if tag and tag.lower() != project:
            return tag
    return UNCATEGORIZED


def _group_key(note):
    """分群鍵：（專案, 主題）。"""
    return (note.get("project") or "(無專案)", theme_of(note))


def _normalize_topic(topic):
    """比對主題前去掉空白與標點，避免只差標點被當成不同。"""
    return re.sub(r"[\s，。！？、：；,.!?:;()（）「」『』\"'`]+", "", topic or "").lower()


def find_duplicates(notes):
    """找出近似重複：主題相似度 ≥ 門檻或內容完全相同；回傳 {較晚行號: [較早行號...]}。"""
    ordered = sorted(notes, key=lambda n: n["_line_no"])
    content_seen = {}
    duplicates = defaultdict(list)
    for i, note in enumerate(ordered):
        line = note["_line_no"]
        content_key = hashlib.sha1((note.get("content") or "").encode("utf-8")).hexdigest()
        if content_key in content_seen and (note.get("content") or "").strip():
            duplicates[line].append(content_seen[content_key])
        else:
            content_seen.setdefault(content_key, line)
        topic = _normalize_topic(note.get("topic"))
        for earlier in ordered[:i]:
            if earlier["_line_no"] in duplicates[line]:
                continue
            other = _normalize_topic(earlier.get("topic"))
            if topic and other and difflib.SequenceMatcher(None, topic, other).ratio() >= DUPLICATE_TOPIC_RATIO:
                duplicates[line].append(earlier["_line_no"])
    return {line: sorted(set(v)) for line, v in duplicates.items() if v}


def build_packs(notes, max_pack=25, duplicates=None):
    """依分群鍵分組並切成最多 max_pack 筆的工作包；重複條目併入其最早原始條目所在的群。"""
    duplicates = duplicates or {}
    by_line = {n["_line_no"]: n for n in notes}
    key_of = {}
    for note in sorted(notes, key=lambda n: n["_line_no"]):
        line = note["_line_no"]
        originals = [l for l in duplicates.get(line, []) if l in key_of]
        key_of[line] = key_of[originals[0]] if originals else _group_key(note)
    groups = defaultdict(list)
    for line, key in key_of.items():
        groups[key].append(by_line[line])
    packs = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda n: n["_line_no"])
        for start in range(0, len(members), max_pack):
            packs.append({"key": key, "notes": members[start:start + max_pack]})
    return packs


def _norm_path(path):
    """統一路徑分隔符並去掉條目錨點，得到熱庫檔的相對路徑。"""
    return path.replace("\\", "/").split("#", 1)[0]


def load_keyword_map(workspace):
    """讀兩個索引，回傳 {熱庫相對路徑: [關鍵字...]}。"""
    kw_map = {}
    for folder in ("knowledge-base", "experience"):
        index = Path(workspace) / folder / "_index.json"
        if not index.exists():
            continue
        data = json.loads(index.read_text(encoding="utf-8"))
        for cat in data.get("categories", []):
            if cat.get("file"):
                kw_map[f"{folder}/{cat['file']}"] = [str(k) for k in cat.get("keywords", [])]
    return kw_map


def distance_to_similarity(distance):
    """Chroma 預設 L2（平方）距離轉餘弦相似度；E5 向量已正規化，故 cos = 1 - d / 2。"""
    return round(1.0 - float(distance) / 2.0, 4)


def _is_strong(candidate):
    """依分數種類（vector 相似度、本機 rerank 分數或 Jev 分數）套用對應門檻。"""
    kind = candidate.get("kind")
    threshold = {"vector": STRONG_VECTOR_SIMILARITY, "jev": STRONG_JEV_SCORE}.get(kind, STRONG_CANDIDATE_SCORE)
    return (candidate.get("score") or 0) >= threshold


def suggest_target(note, candidates, keyword_map):
    """建議目標熱庫檔：強相關候選最多的檔案優先，否則依索引關鍵字命中數，都沒有則標示新分類。"""
    strong = [c for c in candidates if _is_strong(c)]
    if strong:
        counts = Counter(_norm_path(c["path"]) for c in strong)
        best = max(counts.values())
        for c in strong:  # 同票時取分數最高者所在檔案（strong 依輸入順序，呼叫端已按分數排序）
            if counts[_norm_path(c["path"])] == best:
                return _norm_path(c["path"])
    haystack = " ".join([note.get("topic") or "", note.get("content") or "",
                         " ".join(map(str, note.get("tags") or []))]).lower()
    scored = []
    for path, keywords in keyword_map.items():
        hits = sum(1 for kw in keywords if kw and kw.lower() in haystack)
        if hits:
            scored.append((hits, path))
    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][1]
    return UNKNOWN_TARGET


def render_pack(pack_no, pack, candidates, duplicates, keyword_map):
    """產生單一工作包的 Markdown：格式契約 + 每筆條目的內容、重複標註、建議目標與熱庫候選。"""
    project, theme = pack["key"]
    out = [f"# 精煉工作包 pack-{pack_no:02d}（專案：{project}｜主題：{theme}｜{len(pack['notes'])} 筆）", "",
           DRAFT_FORMAT, "## 條目", ""]
    for note in pack["notes"]:
        line = note["_line_no"]
        cands = candidates.get(line, [])
        out.append(f"### L{line}｜{note.get('date', '')}｜skill: {note.get('skill', '')}｜"
                   f"memory_type: {note.get('memory_type', '')}")
        out.append(f"**{note.get('topic', '')}**")
        out.append(f"- tags：{', '.join(map(str, note.get('tags') or [])) or '（無）'}")
        if duplicates.get(line):
            out.append(f"- ⚠️ 可能重複：{', '.join(f'L{l}' for l in duplicates[line])}")
        out.append(f"- 建議目標：{suggest_target(note, cands, keyword_map)}")
        if cands:
            out.append("- 既有熱庫候選（先確認是否已涵蓋）：")
            for c in cands:
                snippet = (c.get("snippet") or "").replace("\n", " ")
                score = "BM25" if c.get("score") is None else f"{c['score']:.2f}"
                out.append(f"  - `{c['path']}`（{score}）{snippet}")
        out.append("")
        out.append("```text")
        out.append((note.get("content") or "").strip())
        out.append("```")
        out.append("")
    return "\n".join(out)


def prepare(workspace, notes, candidates=None, max_pack=25, run_id=None):
    """產生工作目錄 refine-work/<run_id>/，寫入工作包與 manifest，回傳目錄路徑。"""
    workspace = Path(workspace)
    candidates = candidates or {}
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    out = workspace / "refine-work" / run_id
    out.mkdir(parents=True, exist_ok=True)
    duplicates = find_duplicates(notes)
    packs = build_packs(notes, max_pack=max_pack, duplicates=duplicates)
    keyword_map = load_keyword_map(workspace)
    manifest = {"run_id": run_id, "created": datetime.now().isoformat(timespec="seconds"),
                "workspace": str(workspace), "packs": [], "entries": []}
    for i, pack in enumerate(packs, start=1):
        name = f"pack-{i:02d}.md"
        (out / name).write_text(render_pack(i, pack, candidates, duplicates, keyword_map), encoding="utf-8")
        lines = [n["_line_no"] for n in pack["notes"]]
        manifest["packs"].append({"file": name, "key": list(pack["key"]), "lines": lines})
        for n in pack["notes"]:
            manifest["entries"].append({"line": n["_line_no"], "hash": note_hash(n), "pack": name})
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _search_scripts_dir():
    """找 chroma-hybrid-search/scripts（repo 與安裝版都與本技能同層）。"""
    here = Path(__file__).resolve().parent
    return here.parent.parent / "chroma-hybrid-search" / "scripts"


class RerankerChoice:
    """--rerank 用的重排器：依 <workspace>/config.json（與 search.py 同一份設定）選 Jev 或本機 bge。

    Jev 缺金鑰或任何一次評分失敗，就印 stderr 警告並改用本機，之後不再嘗試——
    精煉一次要評上百筆，不能每筆都再付一次逾時成本。
    """

    def __init__(self, workspace):
        sys.path.insert(0, str(_search_scripts_dir()))
        from jev_reranker import JevReranker, load_reranker_config
        cfg = load_reranker_config(str(workspace))
        self.jev = None      # Jev 重排器；None 代表用本機
        self.local = None    # 本機 bge，第一次需要時才載入（約 1.1 GB）
        self._local_factory = None  # 測試可注入替身，避免載入真模型
        if cfg["provider"] == "jev":
            jc = cfg["jev"]
            if jc.get("api_key"):
                self.jev = JevReranker(api_key=jc["api_key"], model=jc["model"],
                                       timeout=jc["timeout"], concurrency=jc["concurrency"])
            else:
                print("[WARN] 設定為 Jev 重排但找不到 TYPESAFE_API_KEY，改用本機 bge-reranker", file=sys.stderr)

    def score(self, query, texts):
        """替一組候選文字評分，回傳 (分數列表, 分數種類 "jev" 或 "rerank")"""
        if self.jev is not None:
            try:
                return [float(s) for s in self.jev.predict([[query, t[:JEV_TEXT_CHARS]] for t in texts])], "jev"
            except Exception as exc:
                print(f"[WARN] Jev 重排失敗（{exc}），改用本機 bge-reranker", file=sys.stderr)
                self.jev = None
        if self.local is None:
            if self._local_factory is None:
                from onnx_models import OnnxReranker
                self._local_factory = OnnxReranker
            self.local = self._local_factory()
        scores = self.local.predict([[query, t[:RERANK_TEXT_CHARS]] for t in texts])
        return [float(s) for s in scores], "rerank"


def find_candidates(workspace, notes, top_k=3, candidate_limit=10, rerank=False):
    """以混合檢索替每筆找熱庫候選；模型與 BM25 只建一次。回傳 {行號: [{path, score, kind, snippet}]}。

    預設快速模式：向量 + BM25 以 RRF 融合排序，分數為向量相似度（只由 BM25 找到的候選分數為 None）。
    --rerank：再以重排模型對前 5 名（文件截斷至 RERANK_TEXT_CHARS 字）重新排序，約每筆多 2~3 秒。
    """
    sys.path.insert(0, str(_search_scripts_dir()))
    import chromadb  # noqa: E402  需要虛擬環境
    from rank_bm25 import BM25Okapi
    import indexer
    import kb_reader
    import search
    from onnx_models import get_shared_e5

    indexer.ensure_fresh(str(workspace))
    hot = kb_reader.read_knowledge_base(str(workspace))
    if not hot or not notes:
        return {}
    doc_map = {d["path"]: d for d in hot}
    paths = [d["path"] for d in hot]
    bm25 = BM25Okapi([search.tokenize(d["text"]) for d in hot])
    collection = chromadb.PersistentClient(path=str(Path(workspace) / "chroma_hybrid_db")) \
        .get_or_create_collection("hybrid_docs")
    queries = [f"{n.get('topic', '')}\n{(n.get('content') or '')[:500]}" for n in notes]
    # 與 ensure_fresh 補索引共用同一個 E5 實例，有待補條目時不必再載入一次
    embeddings = get_shared_e5()(queries)
    # 依 config.json 選 Jev 或本機 bge（與 search.py 同一份設定）
    reranker = RerankerChoice(workspace) if rerank else None
    result = {}
    for note, query, emb in zip(notes, queries, embeddings):
        vec = collection.query(query_embeddings=[emb], n_results=min(candidate_limit, len(hot)),
                               where={"source": "hot"})
        ids = vec["ids"][0] if vec and vec["ids"] else []
        dists = vec["distances"][0] if vec and vec.get("distances") else [None] * len(ids)
        similarity = {p: distance_to_similarity(d) for p, d in zip(ids, dists) if p in doc_map and d is not None}
        vector_ranked = [p for p in ids if p in doc_map]
        scores = bm25.get_scores(search.tokenize(query))
        bm25_ranked = [paths[i] for i in sorted(range(len(paths)), key=lambda i: scores[i], reverse=True)
                       if scores[i] > 0][:candidate_limit]
        fused = [p for p, _ in sorted(search.rrf(vector_ranked, bm25_ranked).items(),
                                      key=lambda x: x[1], reverse=True)][:candidate_limit]
        if not fused:
            continue
        if reranker is not None:
            pool = fused[:5]
            rr, kind = reranker.score(query, [doc_map[p]["text"] for p in pool])
            ranked = sorted(zip(pool, rr), key=lambda x: x[1], reverse=True)[:top_k]
            picked = [{"path": p, "score": float(sc), "kind": kind} for p, sc in ranked]
        else:
            picked = [{"path": p, "score": similarity.get(p), "kind": "vector"} for p in fused[:top_k]]
        for c in picked:
            c["snippet"] = doc_map[c["path"]]["text"][:160]
        result[note["_line_no"]] = picked
    return result


def main():
    """CLI：產生精煉工作包。"""
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".deep-memory")
    parser = argparse.ArgumentParser(description="冷庫精煉前置準備：分包、去重候選、目標建議")
    parser.add_argument("--workspace", default=default_ws, help="deep-memory 工作目錄")
    parser.add_argument("--since", default=None, help="只處理此日期（YYYY-MM-DD）以後的條目")
    parser.add_argument("--project", default=None, help="只處理指定專案")
    parser.add_argument("--max-pack", type=int, default=25, help="每個工作包最多筆數")
    parser.add_argument("--top-k", type=int, default=3, help="每筆列出的熱庫候選數")
    parser.add_argument("--no-candidates", action="store_true", help="不做檢索（不需虛擬環境）")
    parser.add_argument("--rerank", action="store_true", help="候選再經重排模型排序（較準但每筆多 2~3 秒；config.json 設 Jev 時改用 Jev）")
    parser.add_argument("--run-id", default=None, help="工作目錄名稱，預設為時間戳")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    notes = load_pending(workspace / "cold-notes" / "raw.jsonl", since=args.since, project=args.project)
    if not notes:
        print("[INFO] 沒有待精煉的冷庫條目。")
        return
    candidates = {} if args.no_candidates else find_candidates(workspace, notes, top_k=args.top_k,
                                                                rerank=args.rerank)
    out = prepare(workspace, notes, candidates=candidates, max_pack=args.max_pack, run_id=args.run_id)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    print(f"[OK] 待精煉 {len(notes)} 筆 → {len(manifest['packs'])} 個工作包：{out}")
    for pack in manifest["packs"]:
        print(f"  - {pack['file']}：{pack['key'][0]} / {pack['key'][1]}（{len(pack['lines'])} 筆）")
    print("下一步：每個工作包派一個子代理（可同時進行）產出同名 .drafts.md，")
    print(f"      再執行 refine_apply.py --work-dir \"{out}\" --dry-run 預覽。")


if __name__ == "__main__":
    main()
