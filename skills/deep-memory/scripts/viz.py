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
        cats = obj.get("categories", [])
        # 正規化每個 category，補齊缺漏欄位
        out = []
        for c in cats:
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
        for line in cold_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                bad += 1
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
