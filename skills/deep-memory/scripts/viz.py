#!/usr/bin/env python3
"""deep-memory 記憶儀表板產生器。

讀取全域 ~/.deep-memory 的 knowledge-base / cold-notes / experience，
產出單檔、自帶資料、可離線的互動 HTML 儀表板。僅用 Python 標準函式庫。
"""
import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path


def _read_index(path):
    """讀取 _index.json 的 categories 陣列；缺檔或格式錯回傳空清單與警告字串。"""
    if not path.exists():
        return [], f"缺少來源檔：{path}"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        # 根節點必須是物件；否則視為格式錯，優雅降級
        if not isinstance(obj, dict):
            return [], f"格式非物件已略過 {path}"
        cats = obj.get("categories", [])
        out = []
        for c in cats:
            if not isinstance(c, dict):
                continue  # 略過非物件的 category 項
            out.append({
                "id": c.get("id", ""),
                "title": c.get("title", c.get("id", "")),
                "file": c.get("file", ""),
                "keywords": [k for k in c.get("keywords", []) if isinstance(k, str)],
            })
        return out, None
    except (ValueError, OSError) as e:
        return [], f"解析失敗 {path}：{e}"


def load_data(workspace):
    """載入三個來源檔，回傳結構化 dict；任一檔缺漏/壞不中斷，改記入 warnings。"""
    workspace = Path(workspace)
    warnings = []

    categories, w = _read_index(workspace / "knowledge-base" / "_index.json")
    if w:
        warnings.append(w)
    experience, w = _read_index(workspace / "experience" / "_index.json")
    if w:
        warnings.append(w)

    # cold-notes 為 JSONL，逐行解析，壞行略過並計數
    coldnotes = []
    cold_path = workspace / "cold-notes" / "raw.jsonl"
    if not cold_path.exists():
        warnings.append(f"缺少來源檔：{cold_path}")
    else:
        bad = 0
        try:
            raw_lines = cold_path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            warnings.append(f"讀取失敗 {cold_path}：{e}")
            raw_lines = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(o, dict):
                bad += 1  # 合法 JSON 但非物件，視為壞行
                continue
            coldnotes.append({
                "date": o.get("date", ""),
                "time": o.get("time", ""),
                "topic": o.get("topic", ""),
                "tags": [t for t in o.get("tags", []) if isinstance(t, str)],
                "skill": o.get("skill", ""),
                "project": o.get("project", ""),
                "quality": o.get("quality", ""),
            })
        if bad:
            warnings.append(f"raw.jsonl 有 {bad} 行解析失敗已略過")

    return {"categories": categories, "experience": experience,
            "coldnotes": coldnotes, "warnings": warnings}


def aggregate_stats(coldnotes):
    """彙總 cold notes：時間趨勢(升冪)、標籤/專案/品質次數(降冪)。空值欄位以 '(未標)' 計。"""
    timeline = Counter()
    tags = Counter()
    projects = Counter()
    quality = Counter()
    for n in coldnotes:
        if n.get("date"):
            timeline[n["date"]] += 1
        for t in n.get("tags", []):
            tags[t] += 1
        projects[n.get("project") or "(未標)"] += 1
        quality[n.get("quality") or "(未標)"] += 1

    def _desc(counter):
        # 依次數降冪、同次數再依鍵名升冪，確保決定性
        return [[k, v] for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]

    return {
        "timeline": [[k, timeline[k]] for k in sorted(timeline)],
        "tags": _desc(tags),
        "projects": _desc(projects),
        "quality": _desc(quality),
    }


def _norm_kw(keyword):
    """關鍵字正規化：去頭尾空白、轉小寫，作為 keyword 節點的合併鍵。"""
    return keyword.strip().lower()


def build_graph(categories, experience, max_keywords=12):
    """由分類與經驗建關聯圖。

    節點：每個分類一個 category 節點；關鍵字合併為 keyword 節點（正規化後相同即同一節點）。
    邊：分類 → 其關鍵字。每分類最多取前 max_keywords 個關鍵字以控制雜訊。
    experience 併入視為 category 節點（type 仍為 category，來源不同不另分型別）。
    """
    nodes = {}   # id -> node dict
    edges = []
    kw_weight = Counter()  # 每個 keyword 節點被多少分類引用

    def _add_source(items):
        for c in items:
            cid = "cat:" + c["id"]
            nodes[cid] = {"id": cid, "label": c["title"] or c["id"],
                          "type": "category", "weight": 0}
            seen = set()  # 同分類內去重，避免重複邊
            for raw in c.get("keywords", []):
                key = _norm_kw(raw)
                if not key or key in seen:
                    continue
                seen.add(key)
                if len(seen) > max_keywords:
                    break
                kid = "kw:" + key
                if kid not in nodes:
                    nodes[kid] = {"id": kid, "label": raw.strip(),
                                  "type": "keyword", "weight": 0}
                kw_weight[kid] += 1
                edges.append({"source": cid, "target": kid})

    _add_source(categories)
    _add_source(experience)

    # keyword 節點權重＝被幾個分類引用；category 節點權重＝其出邊數
    cat_deg = Counter(e["source"] for e in edges)
    for nid, node in nodes.items():
        if node["type"] == "keyword":
            node["weight"] = kw_weight[nid]
        else:
            node["weight"] = cat_deg[nid]

    return list(nodes.values()), edges
