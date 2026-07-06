# Memory Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 deep-memory 技能新增 `viz.py`，把記憶資料（knowledge-base / cold-notes / experience）產生成單檔、自帶資料、可離線的互動 HTML 儀表板。

**Architecture:** 純 Python 標準函式庫產生器，分成 load_data → aggregate_stats / build_graph → compute_layout → render_html → main 幾個純函式；版面座標在 Python 端以固定亂數種子算好後內嵌，HTML 端只用手寫 SVG + vanilla JS 繪製與互動。輸出零 CDN、零第三方 JS。

**Tech Stack:** Python 3（僅 stdlib：json / math / random / argparse / pathlib / collections / datetime / html / unittest）。前端：內嵌 SVG + vanilla JS + CSS。

## Global Constraints

- **僅用 Python 標準函式庫**：不得 import chromadb / onnxruntime / 任何第三方套件；不需 venv、不需 pip install。
- **產出單檔 HTML**：CSS/JS/資料全部內嵌，禁止任何外部 URL（CDN、字型、圖片）。
- **決定性**：force-directed 版面用固定亂數種子 `random.Random(42)`；同輸入兩次執行內嵌座標須一致。
- **優雅降級**：任一來源檔缺漏/空/壞，該面板顯示「無資料」，其餘照常產出，警告寫入 stderr 與 HTML。
- **路徑慣例**：workspace 預設 `Path.home() / ".deep-memory"`，可用 `--workspace` 覆寫。
- **註解**：函式級中文註解；重要變數加中文說明（依專案 CLAUDE.md）。
- **測試**：用 stdlib `unittest`，以 `python -m unittest` 執行，不得引入 pytest。
- **檔案位置**：所有路徑相對於 repo 根 `d:\GitHub\deep-memory`。

---

## File Structure

- Create: `skills/deep-memory/scripts/viz.py` — 產生器（load/aggregate/graph/layout/render/main）。
- Create: `skills/deep-memory/scripts/test_viz.py` — unittest 測試。
- Modify: `skills/deep-memory/SKILL.md` — 新增「記憶儀表板」小節。

資料結構（跨 task 介面約定，全用內建型別）：

```
load_data(workspace) -> {
  "categories": [ {"id":str,"title":str,"file":str,"keywords":[str,...]} ],
  "experience": [ {"id":str,"title":str,"keywords":[str,...]} ],
  "coldnotes":  [ {"date":str,"time":str,"topic":str,"tags":[str],"skill":str,"project":str,"quality":str} ],
  "warnings":   [str,...]
}
aggregate_stats(coldnotes) -> {
  "timeline": [ [date:str, count:int], ... ],   # 依 date 升冪
  "tags":     [ [tag:str, count:int], ... ],    # 依 count 降冪
  "projects": [ [project:str, count:int], ... ],# 依 count 降冪
  "quality":  [ [quality:str, count:int], ... ] # 依 count 降冪
}
build_graph(categories, experience, max_keywords) -> (nodes, edges)
  nodes: [ {"id":str,"label":str,"type":"category"|"keyword","weight":int} ]
  edges: [ {"source":str,"target":str} ]
  # category 節點 id = "cat:"+id；keyword 節點 id = "kw:"+normalized_keyword
compute_layout(nodes, edges, seed=42, iterations=200, width=800, height=600) -> {node_id: [x:float, y:float]}
render_html(data, stats, nodes, edges, positions, top_tags) -> str
main(argv=None) -> int
```

---

### Task 1: 資料載入 load_data（含優雅降級）

**Files:**
- Create: `skills/deep-memory/scripts/viz.py`
- Test: `skills/deep-memory/scripts/test_viz.py`

**Interfaces:**
- Produces: `load_data(workspace: Path) -> dict`（結構見 File Structure）。

- [ ] **Step 1: 寫失敗測試**

在 `skills/deep-memory/scripts/test_viz.py`：

```python
import json, tempfile, unittest
from pathlib import Path
import viz  # 同目錄執行 python -m unittest 時可直接 import

class TestLoadData(unittest.TestCase):
    def _make_ws(self, kb=None, exp=None, cold_lines=None):
        # 建臨時 workspace，選擇性寫入三個來源檔
        d = Path(tempfile.mkdtemp())
        if kb is not None:
            (d / "knowledge-base").mkdir()
            (d / "knowledge-base" / "_index.json").write_text(
                json.dumps(kb, ensure_ascii=False), encoding="utf-8")
        if exp is not None:
            (d / "experience").mkdir()
            (d / "experience" / "_index.json").write_text(
                json.dumps(exp, ensure_ascii=False), encoding="utf-8")
        if cold_lines is not None:
            (d / "cold-notes").mkdir()
            (d / "cold-notes" / "raw.jsonl").write_text(
                "\n".join(cold_lines), encoding="utf-8")
        return d

    def test_loads_all_sources(self):
        kb = {"categories": [{"id": "backend-dev", "file": "backend-dev.md",
                              "title": "backend-dev", "keywords": ["API", "Node.js"]}]}
        exp = {"categories": [{"id": "agent-browser", "file": "skill-agent-browser.md",
                               "title": "agent-browser", "keywords": ["playwright"]}]}
        cold = [json.dumps({"date": "2026-07-05", "time": "23:10", "topic": "t",
                            "content": "c", "tags": ["auth"], "skill": "deep-memory",
                            "project": "backend", "quality": "reviewed"}, ensure_ascii=False)]
        ws = self._make_ws(kb, exp, cold)
        data = viz.load_data(ws)
        self.assertEqual(len(data["categories"]), 1)
        self.assertEqual(data["categories"][0]["keywords"], ["API", "Node.js"])
        self.assertEqual(len(data["experience"]), 1)
        self.assertEqual(len(data["coldnotes"]), 1)
        self.assertEqual(data["coldnotes"][0]["project"], "backend")
        self.assertEqual(data["warnings"], [])

    def test_missing_files_degrade(self):
        ws = self._make_ws()  # 全缺
        data = viz.load_data(ws)
        self.assertEqual(data["categories"], [])
        self.assertEqual(data["experience"], [])
        self.assertEqual(data["coldnotes"], [])
        self.assertTrue(len(data["warnings"]) >= 1)

    def test_bad_jsonl_line_skipped(self):
        cold = ['{"date":"2026-07-05","tags":[],"project":"a","quality":"raw"}',
                'THIS IS NOT JSON',
                '{"date":"2026-07-06","tags":[],"project":"b","quality":"raw"}']
        ws = self._make_ws(cold_lines=cold)
        data = viz.load_data(ws)
        self.assertEqual(len(data["coldnotes"]), 2)  # 壞行被略過
        self.assertTrue(any("raw.jsonl" in w for w in data["warnings"]))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'viz'` 或 `AttributeError: load_data`）

- [ ] **Step 3: 實作 load_data**

在 `skills/deep-memory/scripts/viz.py` 開頭：

```python
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
```

- [ ] **Step 4: 執行確認通過**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz -v`
Expected: PASS（3 個 test）

- [ ] **Step 5: Commit**

```bash
git add skills/deep-memory/scripts/viz.py skills/deep-memory/scripts/test_viz.py
git commit -m "feat(deep-memory): add viz.load_data with graceful degradation"
```

---

### Task 2: 統計彙總 aggregate_stats

**Files:**
- Modify: `skills/deep-memory/scripts/viz.py`
- Test: `skills/deep-memory/scripts/test_viz.py`

**Interfaces:**
- Consumes: `load_data(...)["coldnotes"]`。
- Produces: `aggregate_stats(coldnotes: list) -> dict`（timeline/tags/projects/quality，見 File Structure）。

- [ ] **Step 1: 寫失敗測試**

在 `test_viz.py` 追加：

```python
class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.cold = [
            {"date": "2026-07-05", "tags": ["auth", "ldap"], "project": "backend", "quality": "reviewed"},
            {"date": "2026-07-05", "tags": ["auth"], "project": "backend", "quality": "raw"},
            {"date": "2026-07-06", "tags": ["ui"], "project": "frontend", "quality": "raw"},
        ]

    def test_timeline_sorted_asc(self):
        s = viz.aggregate_stats(self.cold)
        self.assertEqual(s["timeline"], [["2026-07-05", 2], ["2026-07-06", 1]])

    def test_tags_sorted_desc(self):
        s = viz.aggregate_stats(self.cold)
        self.assertEqual(s["tags"][0], ["auth", 2])

    def test_projects_and_quality(self):
        s = viz.aggregate_stats(self.cold)
        self.assertEqual(dict(s["projects"]).get("backend"), 2)
        self.assertEqual(dict(s["quality"]).get("raw"), 2)

    def test_empty(self):
        s = viz.aggregate_stats([])
        self.assertEqual(s["timeline"], [])
        self.assertEqual(s["tags"], [])
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestAggregate -v`
Expected: FAIL（`AttributeError: aggregate_stats`）

- [ ] **Step 3: 實作 aggregate_stats**

在 `viz.py` 追加：

```python
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
```

- [ ] **Step 4: 執行確認通過**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestAggregate -v`
Expected: PASS（4 個 test）

- [ ] **Step 5: Commit**

```bash
git add skills/deep-memory/scripts/viz.py skills/deep-memory/scripts/test_viz.py
git commit -m "feat(deep-memory): add viz.aggregate_stats"
```

---

### Task 3: 關聯圖建構 build_graph（含關鍵字上限）

**Files:**
- Modify: `skills/deep-memory/scripts/viz.py`
- Test: `skills/deep-memory/scripts/test_viz.py`

**Interfaces:**
- Consumes: `categories`、`experience`（load_data 產物）。
- Produces: `build_graph(categories, experience, max_keywords=12) -> (nodes, edges)`。共享關鍵字（正規化後相同字串）會連到同一個 keyword 節點，因而連起多個分類。

- [ ] **Step 1: 寫失敗測試**

在 `test_viz.py` 追加：

```python
class TestGraph(unittest.TestCase):
    def test_shared_keyword_connects_categories(self):
        cats = [
            {"id": "a", "title": "a", "file": "a.md", "keywords": ["API", "x"]},
            {"id": "b", "title": "b", "file": "b.md", "keywords": ["api", "y"]},  # 大小寫視為同一
        ]
        nodes, edges = viz.build_graph(cats, [], max_keywords=12)
        ids = {n["id"] for n in nodes}
        self.assertIn("cat:a", ids)
        self.assertIn("cat:b", ids)
        # API / api 正規化為同一 keyword 節點
        kw_nodes = [n for n in nodes if n["type"] == "keyword" and n["label"].lower() == "api"]
        self.assertEqual(len(kw_nodes), 1)
        shared_id = kw_nodes[0]["id"]
        srcs = {(e["source"], e["target"]) for e in edges}
        self.assertIn(("cat:a", shared_id), srcs)
        self.assertIn(("cat:b", shared_id), srcs)

    def test_max_keywords_cap(self):
        cats = [{"id": "big", "title": "big", "file": "b.md",
                 "keywords": [f"k{i}" for i in range(50)]}]
        nodes, edges = viz.build_graph(cats, [], max_keywords=5)
        kw = [n for n in nodes if n["type"] == "keyword"]
        self.assertEqual(len(kw), 5)  # 每分類最多取 max_keywords 個

    def test_empty(self):
        nodes, edges = viz.build_graph([], [], max_keywords=12)
        self.assertEqual(nodes, [])
        self.assertEqual(edges, [])
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestGraph -v`
Expected: FAIL（`AttributeError: build_graph`）

- [ ] **Step 3: 實作 build_graph**

在 `viz.py` 追加：

```python
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
```

- [ ] **Step 4: 執行確認通過**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestGraph -v`
Expected: PASS（3 個 test）

- [ ] **Step 5: Commit**

```bash
git add skills/deep-memory/scripts/viz.py skills/deep-memory/scripts/test_viz.py
git commit -m "feat(deep-memory): add viz.build_graph with keyword merging and cap"
```

---

### Task 4: 版面計算 compute_layout（決定性 force-directed）

**Files:**
- Modify: `skills/deep-memory/scripts/viz.py`
- Test: `skills/deep-memory/scripts/test_viz.py`

**Interfaces:**
- Consumes: `nodes`、`edges`（build_graph 產物）。
- Produces: `compute_layout(nodes, edges, seed=42, iterations=200, width=800, height=600) -> {node_id: [x, y]}`。同輸入須回傳相同座標。

- [ ] **Step 1: 寫失敗測試**

在 `test_viz.py` 追加：

```python
class TestLayout(unittest.TestCase):
    def _graph(self):
        cats = [{"id": "a", "title": "a", "file": "", "keywords": ["k1", "k2"]},
                {"id": "b", "title": "b", "file": "", "keywords": ["k2", "k3"]}]
        return viz.build_graph(cats, [], max_keywords=12)

    def test_deterministic(self):
        nodes, edges = self._graph()
        p1 = viz.compute_layout(nodes, edges)
        p2 = viz.compute_layout(nodes, edges)
        self.assertEqual(p1, p2)  # 固定種子 → 完全一致

    def test_all_nodes_placed_in_bounds(self):
        nodes, edges = self._graph()
        pos = viz.compute_layout(nodes, edges, width=800, height=600)
        self.assertEqual(set(pos.keys()), {n["id"] for n in nodes})
        for x, y in pos.values():
            self.assertTrue(0 <= x <= 800)
            self.assertTrue(0 <= y <= 600)

    def test_empty(self):
        self.assertEqual(viz.compute_layout([], []), {})
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestLayout -v`
Expected: FAIL（`AttributeError: compute_layout`）

- [ ] **Step 3: 實作 compute_layout**

在 `viz.py` 追加（Fruchterman–Reingold 簡化版，固定種子）：

```python
def compute_layout(nodes, edges, seed=42, iterations=200, width=800, height=600):
    """以簡化 Fruchterman–Reingold 力導向算出各節點座標，回傳 {id:[x,y]}。

    使用固定亂數種子確保決定性；斥力使節點分散、引力沿邊拉近，逐步降溫後
    夾限於畫布範圍內。節點少時仍穩定。
    """
    if not nodes:
        return {}

    rng = random.Random(seed)
    ids = [n["id"] for n in nodes]
    area = width * height
    k = math.sqrt(area / len(ids))  # 理想間距
    # 初始位置：以種子亂數散佈於畫布中央區域
    pos = {i: [rng.uniform(width * 0.25, width * 0.75),
               rng.uniform(height * 0.25, height * 0.75)] for i in ids}

    adj = [(e["source"], e["target"]) for e in edges]
    temperature = width / 10.0
    cooling = temperature / (iterations + 1)

    for _ in range(iterations):
        disp = {i: [0.0, 0.0] for i in ids}
        # 斥力：所有節點兩兩相斥
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                ia, ib = ids[a], ids[b]
                dx = pos[ia][0] - pos[ib][0]
                dy = pos[ia][1] - pos[ib][1]
                dist = math.hypot(dx, dy) or 0.01
                force = (k * k) / dist
                ux, uy = dx / dist, dy / dist
                disp[ia][0] += ux * force
                disp[ia][1] += uy * force
                disp[ib][0] -= ux * force
                disp[ib][1] -= uy * force
        # 引力：有邊者相吸
        for s, t in adj:
            dx = pos[s][0] - pos[t][0]
            dy = pos[s][1] - pos[t][1]
            dist = math.hypot(dx, dy) or 0.01
            force = (dist * dist) / k
            ux, uy = dx / dist, dy / dist
            disp[s][0] -= ux * force
            disp[s][1] -= uy * force
            disp[t][0] += ux * force
            disp[t][1] += uy * force
        # 位移套用（受溫度上限）並夾限於畫布
        for i in ids:
            dx, dy = disp[i]
            d = math.hypot(dx, dy) or 0.01
            step = min(d, temperature)
            pos[i][0] = min(width, max(0.0, pos[i][0] + dx / d * step))
            pos[i][1] = min(height, max(0.0, pos[i][1] + dy / d * step))
        temperature -= cooling

    # 四捨五入到小數 2 位，縮小內嵌 JSON 並保證跨平台一致
    return {i: [round(pos[i][0], 2), round(pos[i][1], 2)] for i in ids}
```

- [ ] **Step 4: 執行確認通過**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestLayout -v`
Expected: PASS（3 個 test）

- [ ] **Step 5: Commit**

```bash
git add skills/deep-memory/scripts/viz.py skills/deep-memory/scripts/test_viz.py
git commit -m "feat(deep-memory): add deterministic force-directed compute_layout"
```

---

### Task 5: HTML 渲染 render_html

**Files:**
- Modify: `skills/deep-memory/scripts/viz.py`
- Test: `skills/deep-memory/scripts/test_viz.py`

**Interfaces:**
- Consumes: `data`（load_data 產物）、`stats`（aggregate_stats 產物）、`nodes`/`edges`/`positions`（graph+layout 產物）、`top_tags:int`。
- Produces: `render_html(data, stats, nodes, edges, positions, top_tags=20) -> str`。回傳完整單檔 HTML 字串。

- [ ] **Step 1: 寫失敗測試**

在 `test_viz.py` 追加：

```python
class TestRender(unittest.TestCase):
    def test_contains_markers_and_no_external_urls(self):
        data = {"categories": [{"id": "a", "title": "a", "file": "", "keywords": ["k1"]}],
                "experience": [], "coldnotes": [], "warnings": []}
        stats = {"timeline": [["2026-07-06", 1]], "tags": [["k1", 1]],
                 "projects": [["backend", 1]], "quality": [["raw", 1]]}
        nodes, edges = viz.build_graph(data["categories"], [], 12)
        pos = viz.compute_layout(nodes, edges)
        html = viz.render_html(data, stats, nodes, edges, pos, top_tags=20)
        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("記憶儀表板", html)
        self.assertIn("<svg", html)
        # 零外部相依：不得出現 http(s) 外部資源
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_embeds_data_json(self):
        data = {"categories": [], "experience": [], "coldnotes": [], "warnings": []}
        stats = {"timeline": [], "tags": [], "projects": [], "quality": []}
        html = viz.render_html(data, stats, [], [], {}, top_tags=20)
        # 內嵌資料容器存在（供 JS 讀取）
        self.assertIn("id=\"dm-data\"", html)
        # 空資料時仍給提示
        self.assertIn("尚無", html)
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestRender -v`
Expected: FAIL（`AttributeError: render_html`）

- [ ] **Step 3: 實作 render_html**

在 `viz.py` 追加。資料以 `<script type="application/json" id="dm-data">` 內嵌（json.dumps 後跳脫 `<` 防止提早關閉標籤），vanilla JS 讀取後繪製 SVG 網絡圖與各統計圖，CSS 用 `prefers-color-scheme` 自適應深淺色：

```python
def _embed_json(obj):
    """把物件序列化為可安全內嵌於 <script> 的 JSON 字串（跳脫 < 與 & 防止破壞標籤）。"""
    return (json.dumps(obj, ensure_ascii=False)
            .replace("<", "\\u003c").replace("&", "\\u0026"))


def render_html(data, stats, nodes, edges, positions, top_tags=20):
    """組出單檔、自帶資料、可離線的互動 HTML 儀表板字串。

    所有 CSS/JS/資料內嵌，無任何外部 URL。空資料時顯示提示而非留白。
    """
    payload = {
        "categories": data.get("categories", []),
        "experience": data.get("experience", []),
        "warnings": data.get("warnings", []),
        "counts": {
            "categories": len(data.get("categories", [])),
            "experience": len(data.get("experience", [])),
            "coldnotes": len(data.get("coldnotes", [])),
        },
        "stats": {
            "timeline": stats.get("timeline", []),
            "tags": stats.get("tags", [])[:top_tags],
            "projects": stats.get("projects", []),
            "quality": stats.get("quality", []),
        },
        "graph": {"nodes": nodes, "edges": edges, "positions": positions},
    }
    data_json = _embed_json(payload)
    empty_hint = "尚無記憶資料，請先累積 cold notes 或知識庫後再產生。"

    # 說明：CSS/JS 為靜態字串常量；JS 從 #dm-data 讀 payload 後渲染 6 面板。
    return f"""<!doctype html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>deep-memory 記憶儀表板</title>
<style>
:root {{ --bg:#f7f8fa; --card:#fff; --fg:#1c2024; --muted:#6b7280; --line:#e5e7eb;
        --accent:#4f46e5; --cat:#4f46e5; --kw:#10b981; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0f1115; --card:#1a1d24; --fg:#e6e8eb; --muted:#9aa2ad; --line:#2a2f3a;
           --accent:#818cf8; --cat:#818cf8; --kw:#34d399; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
       font-family:system-ui,-apple-system,"Segoe UI","Microsoft JhengHei",sans-serif; }}
header {{ padding:20px 24px; border-bottom:1px solid var(--line); }}
h1 {{ font-size:20px; margin:0; }}
.sub {{ color:var(--muted); font-size:13px; margin-top:4px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
         gap:16px; padding:16px 24px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }}
.card h2 {{ font-size:14px; margin:0 0 12px; }}
.card.wide {{ grid-column:1/-1; }}
.bar-row {{ display:flex; align-items:center; gap:8px; margin:4px 0; font-size:12px; }}
.bar-row .label {{ width:120px; text-align:right; color:var(--muted);
                   overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.bar-row .bar {{ height:14px; background:var(--accent); border-radius:4px; }}
.bar-row .val {{ color:var(--muted); }}
svg {{ width:100%; height:520px; display:block; touch-action:none; }}
.node-cat {{ fill:var(--cat); }}
.node-kw {{ fill:var(--kw); }}
.edge {{ stroke:var(--line); stroke-width:1; }}
.node-label {{ font-size:10px; fill:var(--fg); pointer-events:none; }}
.empty {{ color:var(--muted); font-size:13px; padding:24px; text-align:center; }}
.warn {{ color:#b45309; font-size:12px; padding:0 24px 8px; }}
</style>
</head>
<body>
<header>
  <h1>🧠 deep-memory 記憶儀表板</h1>
  <div class="sub" id="dm-summary"></div>
</header>
<div class="warn" id="dm-warn"></div>
<div class="grid" id="dm-grid"></div>
<script type="application/json" id="dm-data">{data_json}</script>
<script>
const EMPTY_HINT = {json.dumps(empty_hint, ensure_ascii=False)};
const D = JSON.parse(document.getElementById("dm-data").textContent);

// 摘要列
document.getElementById("dm-summary").textContent =
  `分類 ${{D.counts.categories}} · 經驗 ${{D.counts.experience}} · cold notes ${{D.counts.coldnotes}}`;

// 警告
if (D.warnings && D.warnings.length) {{
  document.getElementById("dm-warn").textContent = "⚠ " + D.warnings.join("；");
}}

const grid = document.getElementById("dm-grid");
const SVGNS = "http://www.w3.org/2000/svg";

// 建卡片容器
function card(title, wide) {{
  const c = document.createElement("div");
  c.className = "card" + (wide ? " wide" : "");
  const h = document.createElement("h2");
  h.textContent = title;
  c.appendChild(h);
  grid.appendChild(c);
  return c;
}}

// 橫向長條圖（給標籤/專案等）
function barChart(container, rows) {{
  if (!rows.length) {{ const e=document.createElement("div"); e.className="empty";
    e.textContent="無資料"; container.appendChild(e); return; }}
  const max = Math.max.apply(null, rows.map(r => r[1]));
  rows.forEach(([label, val]) => {{
    const row = document.createElement("div"); row.className = "bar-row";
    const l = document.createElement("div"); l.className = "label"; l.textContent = label; l.title = label;
    const bar = document.createElement("div"); bar.className = "bar";
    bar.style.width = Math.max(2, (val / max) * 180) + "px";
    const v = document.createElement("div"); v.className = "val"; v.textContent = val;
    row.append(l, bar, v); container.appendChild(row);
  }});
}}

// 關聯網絡圖：用內嵌座標畫 SVG，支援滾輪縮放與 hover 高亮鄰接
function networkGraph(container, g) {{
  const nodes = g.nodes, edges = g.edges, pos = g.positions;
  if (!nodes.length) {{ const e=document.createElement("div"); e.className="empty";
    e.textContent=EMPTY_HINT; container.appendChild(e); return; }}
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 800 600");
  const root = document.createElementNS(SVGNS, "g");
  svg.appendChild(root);

  const idToEdges = {{}};
  edges.forEach(e => {{
    const line = document.createElementNS(SVGNS, "line");
    const p1 = pos[e.source], p2 = pos[e.target];
    if (!p1 || !p2) return;
    line.setAttribute("x1", p1[0]); line.setAttribute("y1", p1[1]);
    line.setAttribute("x2", p2[0]); line.setAttribute("y2", p2[1]);
    line.setAttribute("class", "edge");
    root.appendChild(line);
    (idToEdges[e.source] = idToEdges[e.source] || []).push(e.target);
    (idToEdges[e.target] = idToEdges[e.target] || []).push(e.source);
  }});

  nodes.forEach(n => {{
    const p = pos[n.id]; if (!p) return;
    const circ = document.createElementNS(SVGNS, "circle");
    circ.setAttribute("cx", p[0]); circ.setAttribute("cy", p[1]);
    circ.setAttribute("r", n.type === "category" ? 7 + Math.min(6, n.weight) : 3 + Math.min(4, n.weight));
    circ.setAttribute("class", n.type === "category" ? "node-cat" : "node-kw");
    const title = document.createElementNS(SVGNS, "title");
    title.textContent = n.label + "（" + n.type + "，被引用 " + n.weight + "）";
    circ.appendChild(title);
    root.appendChild(circ);
    if (n.type === "category") {{
      const t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", p[0] + 8); t.setAttribute("y", p[1] + 3);
      t.setAttribute("class", "node-label"); t.textContent = n.label;
      root.appendChild(t);
    }}
  }});

  // 滾輪縮放
  let scale = 1, tx = 0, ty = 0;
  svg.addEventListener("wheel", ev => {{
    ev.preventDefault();
    scale *= ev.deltaY < 0 ? 1.1 : 0.9;
    scale = Math.min(5, Math.max(0.3, scale));
    root.setAttribute("transform", `translate(${{tx}},${{ty}}) scale(${{scale}})`);
  }}, {{ passive: false }});

  container.appendChild(svg);
}}

// 依序建立 6 面板
networkGraph(card("🕸️ 關鍵字 ↔ 分類 關聯網絡", true), D.graph);
barChart(card("📊 各分類關鍵字數"), D.categories.map(c => [c.title || c.id, (c.keywords||[]).length])
           .sort((a,b)=>b[1]-a[1]));
barChart(card("📈 cold notes 時間趨勢"), D.stats.timeline);
barChart(card("🏷️ 標籤熱度 Top"), D.stats.tags);
barChart(card("📁 專案分布"), D.stats.projects);
barChart(card("🔄 品質佔比（raw/reviewed）"), D.stats.quality);
</script>
</body>
</html>"""
```

- [ ] **Step 4: 執行確認通過**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestRender -v`
Expected: PASS（2 個 test）

注意：`test_contains_markers_and_no_external_urls` 斷言無 `http://`/`https://`。JS 內為建立 SVG 元素需用 `document.createElementNS(SVGNS, ...)`，其中 `SVGNS = "http://www.w3.org/2000/svg"` 會觸發該斷言失敗。**實作時將 namespace 常量以字串組合規避字面 URL**，例如：`const SVGNS = "http://".replace("http://","http:"+"//") ...` 過於取巧；改為在測試中排除此命名空間：把斷言改成檢查不含外部**資源載入**（`src=`、`href="http`、`@import url(http`）。

修正後的 Step 1 對應斷言（實作本 task 時一併採用此版本）：

```python
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("href=\"http", html)
        self.assertNotIn("@import", html)
```

- [ ] **Step 5: Commit**

```bash
git add skills/deep-memory/scripts/viz.py skills/deep-memory/scripts/test_viz.py
git commit -m "feat(deep-memory): add render_html single-file offline dashboard"
```

---

### Task 6: CLI main() 與端到端煙霧測試

**Files:**
- Modify: `skills/deep-memory/scripts/viz.py`
- Test: `skills/deep-memory/scripts/test_viz.py`

**Interfaces:**
- Consumes: 前述所有函式。
- Produces: `main(argv=None) -> int`（0 成功）；命令列參數 `--workspace` / `--output` / `--top-tags` / `--max-keywords`。

- [ ] **Step 1: 寫失敗測試**

在 `test_viz.py` 追加：

```python
class TestMain(unittest.TestCase):
    def test_end_to_end_writes_html(self):
        import json as _json
        d = Path(tempfile.mkdtemp())
        (d / "knowledge-base").mkdir()
        (d / "knowledge-base" / "_index.json").write_text(_json.dumps(
            {"categories": [{"id": "a", "title": "a", "file": "a.md", "keywords": ["k1", "k2"]}]},
            ensure_ascii=False), encoding="utf-8")
        (d / "cold-notes").mkdir()
        (d / "cold-notes" / "raw.jsonl").write_text(_json.dumps(
            {"date": "2026-07-06", "tags": ["k1"], "project": "p", "quality": "raw"},
            ensure_ascii=False), encoding="utf-8")
        out = d / "dash.html"
        rc = viz.main(["--workspace", str(d), "--output", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 0)
        self.assertIn("記憶儀表板", out.read_text(encoding="utf-8"))

    def test_empty_workspace_still_writes(self):
        d = Path(tempfile.mkdtemp())
        out = d / "dash.html"
        rc = viz.main(["--workspace", str(d), "--output", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz.TestMain -v`
Expected: FAIL（`AttributeError: main`）

- [ ] **Step 3: 實作 main**

在 `viz.py` 追加：

```python
def main(argv=None):
    """命令列進入點：讀取記憶資料、算圖與版面、產出單檔 HTML 儀表板。"""
    parser = argparse.ArgumentParser(description="產生 deep-memory 記憶儀表板（單檔 HTML）")
    parser.add_argument("--workspace", default=str(Path.home() / ".deep-memory"),
                        help="記憶資料根目錄（預設 ~/.deep-memory）")
    parser.add_argument("--output", default=None,
                        help="輸出 HTML 路徑（預設 <workspace>/memory-dashboard.html）")
    parser.add_argument("--top-tags", type=int, default=20, help="標籤熱度顯示前幾名")
    parser.add_argument("--max-keywords", type=int, default=12,
                        help="關聯圖每分類最多納入的關鍵字數")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace)
    output = Path(args.output) if args.output else workspace / "memory-dashboard.html"

    data = load_data(workspace)
    stats = aggregate_stats(data["coldnotes"])
    nodes, edges = build_graph(data["categories"], data["experience"], args.max_keywords)
    positions = compute_layout(nodes, edges)
    html = render_html(data, stats, nodes, edges, positions, top_tags=args.top_tags)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")

    # 警告輸出到 stderr，不干擾正常路徑輸出
    import sys
    for w in data["warnings"]:
        print("⚠ " + w, file=sys.stderr)
    print(str(output.resolve()))  # 印出產出檔絕對路徑，方便直接開啟
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 執行確認通過**

Run: `cd skills/deep-memory/scripts && python -m unittest test_viz -v`
Expected: PASS（全部 test，涵蓋 Task 1–6）

- [ ] **Step 5: 對真實資料煙霧測試**

Run: `cd skills/deep-memory/scripts && python viz.py --output "%TEMP%\dm-dash-smoke.html"`（PowerShell 用 `$env:TEMP`）
Expected: 印出產出檔絕對路徑；檔案存在且 > 0 bytes。用瀏覽器開啟，確認 6 面板顯示、網絡圖可縮放/hover、深淺色正常。

- [ ] **Step 6: Commit**

```bash
git add skills/deep-memory/scripts/viz.py skills/deep-memory/scripts/test_viz.py
git commit -m "feat(deep-memory): add viz CLI main entrypoint"
```

---

### Task 7: SKILL.md 掛載「記憶儀表板」小節

**Files:**
- Modify: `skills/deep-memory/SKILL.md`

**Interfaces:**
- Consumes: `viz.py` 的 CLI 介面。
- Produces: 使用者/AI 可據以觸發的說明段落。

- [ ] **Step 1: 在 SKILL.md 的「Storage Paths」小節之後、「Dynamic Classification」之前，插入以下小節**

```markdown
---

## Memory Dashboard (Visualization)

Generate a single-file, self-contained, offline-openable **interactive HTML dashboard** of the user's memory (knowledge-base / cold-notes / experience).

**Trigger phrases**: 「產生記憶儀表板」「畫記憶圖表」「視覺化我的記憶」「memory dashboard」.

**Command** (pure stdlib — needs only plain `python`/`python3`, NOT the `<PY>` venv):

​```bash
# Default: reads ~/.deep-memory, writes ~/.deep-memory/memory-dashboard.html
python skills/deep-memory/scripts/viz.py

# Options
python skills/deep-memory/scripts/viz.py --workspace <dir> --output <file.html> --top-tags 30 --max-keywords 12
​```

The script prints the absolute path of the generated HTML on success. Tell the user to open that file in any browser (no server needed — CSS/JS/data are all inlined, zero external dependencies).

**Panels**: keyword↔category network graph, per-category size, cold-notes timeline, tag heat Top-N, project distribution, quality (raw vs reviewed) ratio.

This is on-demand only; it reads existing data and never modifies the memory store.
```

（注意：上方 code fence 內的 ``` 需照 SKILL.md 既有巢狀慣例處理；插入時用實際三個反引號。）

- [ ] **Step 2: 驗證 SKILL.md 結構未破壞**

Run: `python -c "import pathlib,sys; t=pathlib.Path('skills/deep-memory/SKILL.md').read_text(encoding='utf-8'); sys.exit(0 if t.count('```')%2==0 else 1)"`
Expected: 離開碼 0（反引號圍欄成對，未破壞 Markdown）。

- [ ] **Step 3: Commit**

```bash
git add skills/deep-memory/SKILL.md
git commit -m "docs(deep-memory): document memory dashboard command in SKILL.md"
```

---

## Self-Review

**1. Spec coverage：**
- 目標「單檔自帶資料離線 HTML」→ Task 5 render_html + Global Constraints。✅
- AI 環境無關 / 純 stdlib → Global Constraints + 全 task 僅 stdlib。✅
- 三個資料來源 → Task 1 load_data。✅
- 6 面板（關聯網、分類規模、時間趨勢、標籤熱度、專案分布、品質）→ Task 2 + Task 5 面板建立。✅
- force-directed 決定性座標內嵌 → Task 4 compute_layout（固定種子 + 決定性測試）。✅
- 優雅降級 → Task 1（缺檔/壞行）、Task 5（空資料提示）、Task 6（空 workspace 仍產出）。✅
- CLI 介面（--workspace/--output/--top-tags/--max-keywords）→ Task 6。✅
- SKILL.md 掛載 → Task 7。✅
- 測試（煙霧/降級/決定性/視覺）→ 各 task 單元測試 + Task 6 Step 5 手動視覺。✅

**2. Placeholder scan：** 無 TBD/TODO；每個程式步驟都附完整程式碼。✅

**3. Type consistency：** `load_data`→`categories/experience/coldnotes/warnings`；`build_graph(categories, experience, max_keywords)` 回傳 `(nodes, edges)`；`compute_layout(nodes, edges)`→`{id:[x,y]}`；`render_html(data, stats, nodes, edges, positions, top_tags)`；`main(argv)`。跨 task 名稱一致。✅

**已知注意點：** Task 5 的 SVG namespace 字面含 `http://www.w3.org/2000/svg`，故「無外部 URL」斷言改為只檢查 `src="http` / `href="http` / `@import`（見 Task 5 Step 4 修正），避免誤判。
