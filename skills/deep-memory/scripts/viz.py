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
        except (OSError, ValueError) as e:
            # ValueError 涵蓋 UnicodeDecodeError（非 UTF-8 位元組），與 _read_index 一致降級處理
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


def build_graph(categories, experience, coldnotes=None, max_keywords=12, min_tag_count=2):
    """建三分圖：分類(category) ──keyword── 詞(term) ──tag── 專案(project)。

    - 分類節點分兩種來源：knowledge（知識庫）與 experience（經驗），供渲染分色。
    - 詞節點統一 keyword 與 tag：正規化後同字即同一節點；kind 標記其身分
      （keyword=僅出現在知識庫、tag=僅出現在 cold notes、both=兩者皆是＝橋接詞）。
    - 專案節點作為 tag 的錨點（tag 屬於 cold notes，每筆有 project）。
    - 詞的納入規則：知識庫關鍵字（每分類取前 max_keywords 個）一律納入；
      cold notes 的 tag 需出現次數 ≥ min_tag_count，或本身也是關鍵字（橋接詞）才納入，
      以控制節點數量。
    邊：分類→詞（keyword 關係）、專案→詞（tag 關係）。
    """
    coldnotes = coldnotes or []
    nodes = {}   # id -> node dict
    edges = []
    edge_seen = set()  # (source, target) 去重

    def _term_id(key):
        return "term:" + key

    # 先算出每分類「截斷後」的關鍵字清單，並收集關鍵字詞集合 KW
    cat_keywords = []   # [(cid, [(key, raw), ...]), ...]
    kw_set = set()      # 所有納入的關鍵字（正規化）
    kw_label = {}       # key -> 代表性原字串

    def _collect(items, source):
        for c in items:
            cid = "cat:" + source + ":" + c["id"]
            nodes[cid] = {"id": cid, "label": c["title"] or c["id"],
                          "type": "category", "source": source, "weight": 0}
            seen = set()  # 同分類內去重
            picked = []
            for raw in c.get("keywords", []):
                key = _norm_kw(raw)
                if not key or key in seen:
                    continue
                seen.add(key)
                if len(seen) > max_keywords:
                    break
                picked.append((key, raw.strip()))
                kw_set.add(key)
                kw_label.setdefault(key, raw.strip())
            cat_keywords.append((cid, picked))

    _collect(categories, "knowledge")
    _collect(experience, "experience")

    # 統計 cold notes 的 tag 次數（正規化）
    tag_count = Counter()
    tag_label = {}
    for n in coldnotes:
        for raw in n.get("tags", []):
            if not isinstance(raw, str):
                continue
            key = _norm_kw(raw)
            if not key:
                continue
            tag_count[key] += 1
            tag_label.setdefault(key, raw.strip())

    # 決定納入的 tag 詞：次數達門檻，或本身是關鍵字（橋接詞一律納入）
    tag_inc = {t for t, c in tag_count.items() if c >= min_tag_count or t in kw_set}

    # 建立詞節點（keyword 詞 ∪ 納入的 tag 詞），標記 kind
    term_keys = kw_set | tag_inc
    for key in term_keys:
        is_kw = key in kw_set
        is_tag = key in tag_count
        kind = "both" if (is_kw and is_tag) else ("keyword" if is_kw else "tag")
        nodes[_term_id(key)] = {"id": _term_id(key),
                                "label": kw_label.get(key) or tag_label.get(key) or key,
                                "type": "term", "kind": kind, "weight": 0}

    def _add_edge(src, tgt):
        pair = (src, tgt)
        if pair in edge_seen:
            return
        edge_seen.add(pair)
        edges.append({"source": src, "target": tgt})

    # 分類→詞（keyword 邊）
    for cid, picked in cat_keywords:
        for key, _raw in picked:
            _add_edge(cid, _term_id(key))

    # 專案→詞（tag 邊）：僅連納入的 tag 詞
    for n in coldnotes:
        proj = n.get("project") or "(未標)"
        pid = "proj:" + proj
        tags = {_norm_kw(t) for t in n.get("tags", []) if isinstance(t, str) and t.strip()}
        tags = {t for t in tags if t in tag_inc}
        if not tags:
            continue
        if pid not in nodes:
            nodes[pid] = {"id": pid, "label": proj, "type": "project", "weight": 0}
        for t in tags:
            _add_edge(pid, _term_id(t))

    # 權重＝節點的連接邊數（degree）
    deg = Counter()
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    for nid, node in nodes.items():
        node["weight"] = deg[nid]

    return list(nodes.values()), edges


def compute_layout(nodes, edges, seed=42, iterations=200, width=800, height=600):
    """以簡化 Fruchterman–Reingold 力導向算出各節點座標，回傳 {id:[x,y]}。

    使用固定亂數種子確保決定性；斥力使節點分散、引力沿邊拉近，逐步降溫後
    夾限於畫布範圍內。節點少時仍穩定。
    """
    if not nodes:
        return {}

    rng = random.Random(seed)
    # 節點順序即決定性來源：沿用 build_graph 的插入序，勿改用 set 等無序容器
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
        --accent:#4f46e5;
        --cat:#4f46e5;      /* 知識分類 藍 */
        --exp:#db2777;      /* 經驗分類 洋紅 */
        --proj:#f59e0b;     /* 專案 琥珀 */
        --kw:#10b981;       /* 詞：僅 keyword 綠 */
        --tag:#06b6d4;      /* 詞：僅 tag 青 */
        --bridge:#a855f7;   /* 詞：橋接(keyword+tag) 紫 */ }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0f1115; --card:#1a1d24; --fg:#e6e8eb; --muted:#9aa2ad; --line:#2a2f3a;
           --accent:#818cf8; --cat:#818cf8; --exp:#f472b6; --proj:#fbbf24;
           --kw:#34d399; --tag:#22d3ee; --bridge:#c084fc; }}
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
svg {{ width:100%; height:560px; display:block; touch-action:none; cursor:grab;
       background:var(--bg); border-radius:8px; }}
.node-cat {{ fill:var(--cat); }}
.node-exp {{ fill:var(--exp); }}
.node-proj {{ fill:var(--proj); }}
.node-term-keyword {{ fill:var(--kw); }}
.node-term-tag {{ fill:var(--tag); }}
.node-term-both {{ fill:var(--bridge); stroke:var(--bridge); stroke-width:2;
                   stroke-opacity:0.4; }}
.edge {{ stroke:var(--line); stroke-width:1; }}
.node-label {{ font-size:10px; fill:var(--fg); pointer-events:none; }}
.empty {{ color:var(--muted); font-size:13px; padding:24px; text-align:center; }}
.warn {{ color:#b45309; font-size:12px; padding:0 24px 8px; }}
/* 關聯圖控制列 */
.g-controls {{ display:flex; flex-wrap:wrap; gap:10px 16px; align-items:center;
               margin-bottom:10px; font-size:12px; }}
.g-controls input[type=search] {{ padding:5px 9px; border:1px solid var(--line);
    border-radius:6px; background:var(--bg); color:var(--fg); min-width:160px; }}
.g-controls button {{ padding:5px 10px; border:1px solid var(--line); border-radius:6px;
    background:var(--bg); color:var(--fg); cursor:pointer; }}
.g-controls button:hover {{ border-color:var(--accent); }}
.legend {{ display:flex; flex-wrap:wrap; gap:6px 14px; align-items:center; }}
.legend label {{ display:inline-flex; align-items:center; gap:5px; cursor:pointer;
    user-select:none; color:var(--muted); }}
.legend .dot {{ width:11px; height:11px; border-radius:50%; display:inline-block; }}
.legend input {{ accent-color:var(--accent); }}
.g-hint {{ color:var(--muted); font-size:11px; margin-top:6px; }}
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
  const max = Math.max.apply(null, [1].concat(rows.map(r => r[1])));
  rows.forEach(([label, val]) => {{
    const row = document.createElement("div"); row.className = "bar-row";
    const l = document.createElement("div"); l.className = "label"; l.textContent = label; l.title = label;
    const bar = document.createElement("div"); bar.className = "bar";
    bar.style.width = Math.max(2, (val / max) * 180) + "px";
    const v = document.createElement("div"); v.className = "val"; v.textContent = val;
    row.append(l, bar, v); container.appendChild(row);
  }});
}}

// 三分圖分組定義：知識/經驗/專案/keyword/tag/橋接，供上色、圖例與開關共用
const GROUPS = [
  {{key: "knowledge",  label: "知識分類",     color: "var(--cat)",    cls: "node-cat"}},
  {{key: "experience", label: "經驗分類",     color: "var(--exp)",    cls: "node-exp"}},
  {{key: "project",    label: "專案",         color: "var(--proj)",   cls: "node-proj"}},
  {{key: "keyword",    label: "keyword",     color: "var(--kw)",     cls: "node-term-keyword"}},
  {{key: "tag",        label: "tag",         color: "var(--tag)",    cls: "node-term-tag"}},
  {{key: "both",       label: "橋接(kw+tag)", color: "var(--bridge)", cls: "node-term-both"}},
];
// 節點 -> 分組 key
function groupOf(n) {{
  if (n.type === "category") return n.source === "experience" ? "experience" : "knowledge";
  if (n.type === "project") return "project";
  if (n.type === "term") return n.kind || "keyword";
  return "keyword";
}}

// 關聯網絡圖：三分圖 + 即時力導向模擬。
// 支援：節點拖曳、畫布平移、滾輪縮放、重置、hover/點擊聚焦鄰域、搜尋、類型開關。
// Python 內嵌座標僅作決定性起點，實際版面由物理模擬即時收斂。
function networkGraph(container, g) {{
  const W = 800, H = 600, CX = W / 2, CY = H / 2;
  const rawNodes = g.nodes, edges = g.edges, pos = g.positions;
  if (!rawNodes.length) {{ const e=document.createElement("div"); e.className="empty";
    e.textContent=EMPTY_HINT; container.appendChild(e); return; }}

  // ---- 控制列：搜尋 + 重置 + 圖例(兼類型開關) ----
  const groups = {{}};   // key -> 是否顯示
  GROUPS.forEach(x => groups[x.key] = true);
  let query = "";        // 搜尋字串（小寫）
  let focusId = null;    // 點擊聚焦的節點

  const ctrl = document.createElement("div"); ctrl.className = "g-controls";
  const search = document.createElement("input");
  search.type = "search"; search.placeholder = "搜尋節點…";
  search.addEventListener("input", () => {{ query = search.value.trim().toLowerCase(); refresh(); }});
  const resetBtn = document.createElement("button"); resetBtn.textContent = "重置視圖";
  const legend = document.createElement("div"); legend.className = "legend";
  GROUPS.forEach(gr => {{
    const lab = document.createElement("label");
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = true;
    cb.addEventListener("change", () => {{ groups[gr.key] = cb.checked; refresh(); }});
    const dot = document.createElement("span"); dot.className = "dot"; dot.style.background = gr.color;
    const txt = document.createElement("span"); txt.textContent = gr.label;
    lab.append(cb, dot, txt); legend.appendChild(lab);
  }});
  ctrl.append(search, resetBtn, legend);
  container.appendChild(ctrl);

  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  const root = document.createElementNS(SVGNS, "g");   // 承載縮放/平移
  svg.appendChild(root);

  // 建立模擬節點：x/y 起始於 Python 座標
  const sim = {{}}; const idToEdges = {{}};
  rawNodes.forEach((n, i) => {{
    const p = pos[n.id] || [CX + (i % 10) - 5, CY + Math.floor(i / 10) - 5];
    const big = (n.type === "category" || n.type === "project");
    const r = big ? 7 + Math.min(8, n.weight) : 3 + Math.min(5, n.weight / 2);
    sim[n.id] = {{n: n, x: p[0], y: p[1], vx: 0, vy: 0, r: r, fixed: false,
                 grp: groupOf(n), always: big}};
  }});

  // 邊 + 鄰接表
  const lineEls = [];
  edges.forEach(e => {{
    if (!sim[e.source] || !sim[e.target]) return;
    const line = document.createElementNS(SVGNS, "line");
    line.setAttribute("class", "edge");
    root.appendChild(line);
    lineEls.push({{el: line, a: e.source, b: e.target}});
    (idToEdges[e.source] = idToEdges[e.source] || []).push(e.target);
    (idToEdges[e.target] = idToEdges[e.target] || []).push(e.source);
  }});

  // 節點 circle + 標籤（分類/專案常駐標籤；詞節點僅在聚焦/hover/搜尋時顯示）
  const nodeEls = {{}}; const labelEls = [];
  const order = Object.keys(sim);
  order.forEach(id => {{
    const s = sim[id], n = s.n;
    const gr = GROUPS.find(x => x.key === s.grp);
    const circ = document.createElementNS(SVGNS, "circle");
    circ.setAttribute("r", s.r);
    circ.setAttribute("class", gr ? gr.cls : "node-term-keyword");
    circ.style.cursor = "grab";
    const title = document.createElementNS(SVGNS, "title");
    const kindTxt = n.type === "term" ? ("詞/" + (n.kind === "both" ? "橋接" : n.kind))
                  : (n.type === "category" ? (n.source === "experience" ? "經驗" : "知識") : "專案");
    title.textContent = n.label + "（" + kindTxt + "，連結 " + n.weight + "）";
    circ.appendChild(title);
    root.appendChild(circ);
    nodeEls[id] = circ;

    const t = document.createElementNS(SVGNS, "text");
    t.setAttribute("class", "node-label"); t.textContent = n.label;
    root.appendChild(t);
    labelEls.push({{el: t, id: id}});

    // hover：暫時聚焦鄰域（不覆蓋已點擊聚焦）
    circ.addEventListener("mouseenter", () => {{ if (!dragging && !focusId) {{ hoverId = id; refresh(); }} }});
    circ.addEventListener("mouseleave", () => {{ if (hoverId === id) {{ hoverId = null; refresh(); }} }});
    // 點擊：切換聚焦鄰域
    circ.addEventListener("click", ev => {{ ev.stopPropagation();
      if (dragMoved) {{ dragMoved = false; return; }}   // 拖曳結束的 click 不切換聚焦
      focusId = (focusId === id) ? null : id; hoverId = null; refresh(); }});
    // 拖曳節點
    circ.addEventListener("pointerdown", ev => {{
      ev.preventDefault(); ev.stopPropagation();   // 阻止觸發畫布平移
      dragging = sim[id]; dragging.fixed = true; dragMoved = false;
      circ.style.cursor = "grabbing";
      try {{ circ.setPointerCapture(ev.pointerId); }} catch (e) {{}}
      alpha = Math.max(alpha, 0.5); startSim();      // 冷卻後仍能重啟迴圈以反映拖曳
    }});
    circ.addEventListener("pointermove", ev => {{
      if (dragging !== sim[id]) return;
      const loc = toLocal(ev); dragging.x = loc.x; dragging.y = loc.y;
      dragging.vx = 0; dragging.vy = 0; dragMoved = true;
    }});
    const release = () => {{ if (dragging === sim[id]) {{ dragging.fixed = false;
      dragging = null; circ.style.cursor = "grab"; }} }};
    circ.addEventListener("pointerup", release);
    circ.addEventListener("pointercancel", release);
  }});

  function toLocal(ev) {{
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    return pt.matrixTransform(root.getScreenCTM().inverse());
  }}

  // ---- 顯示狀態刷新：套用類型開關、搜尋、聚焦/hover 的強調 ----
  let hoverId = null;
  function emphasisSet() {{
    // 回傳需強調的節點 id 集合；null 代表全部強調（無聚焦/搜尋）
    if (focusId) return new Set([focusId].concat(idToEdges[focusId] || []));
    if (query) {{ const s = new Set();
      order.forEach(id => {{ if (sim[id].n.label.toLowerCase().includes(query)) s.add(id); }});
      return s; }}
    if (hoverId) return new Set([hoverId].concat(idToEdges[hoverId] || []));
    return null;
  }}
  function refresh() {{
    const emp = emphasisSet();
    order.forEach(id => {{
      const s = sim[id];
      const gvis = groups[s.grp];                 // 類型開關
      const circ = nodeEls[id], lab = labelEls.find(l => l.id === id).el;
      circ.style.display = gvis ? "" : "none";
      if (!gvis) {{ lab.style.display = "none"; return; }}
      const on = !emp || emp.has(id);
      circ.style.opacity = on ? "1" : "0.12";
      // 標籤：常駐(分類/專案) 或 被強調的詞 才顯示
      const showLab = on && (s.always || (emp && emp.has(id)));
      lab.style.display = showLab ? "" : "none";
      lab.style.opacity = on ? "1" : "0.12";
    }});
    lineEls.forEach(le => {{
      const gvis = groups[sim[le.a].grp] && groups[sim[le.b].grp];
      le.el.style.display = gvis ? "" : "none";
      if (!gvis) return;
      const on = !emp || (emp.has(le.a) && emp.has(le.b));
      le.el.style.opacity = on ? "0.9" : "0.05";
    }});
  }}

  // ---- 力導向模擬 ----
  const REPULSION = 1400, SPRING = 0.02, LINK_LEN = 55, GRAVITY = 0.015, DAMPING = 0.85;
  let alpha = 1.0, dragging = null, running = false, dragMoved = false;
  // 啟動模擬迴圈（若已停止則重啟）；冷卻停止後再次拖曳可靠此重啟
  function startSim() {{ if (!running) {{ running = true; requestAnimationFrame(tick); }} }}
  function tick() {{
    for (let i = 0; i < order.length; i++) {{
      const a = sim[order[i]];
      for (let j = i + 1; j < order.length; j++) {{
        const b = sim[order[j]];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const d = Math.sqrt(d2), f = (REPULSION * alpha) / d2;
        const ux = dx / d, uy = dy / d;
        a.vx += ux * f; a.vy += uy * f; b.vx -= ux * f; b.vy -= uy * f;
      }}
    }}
    lineEls.forEach(le => {{
      const a = sim[le.a], b = sim[le.b];
      let dx = b.x - a.x, dy = b.y - a.y;
      let d = Math.hypot(dx, dy) || 0.01;
      const f = SPRING * (d - LINK_LEN) * alpha, ux = dx / d, uy = dy / d;
      a.vx += ux * f; a.vy += uy * f; b.vx -= ux * f; b.vy -= uy * f;
    }});
    order.forEach(id => {{
      const s = sim[id]; if (s.fixed) return;
      s.vx += (CX - s.x) * GRAVITY * alpha; s.vy += (CY - s.y) * GRAVITY * alpha;
      s.vx *= DAMPING; s.vy *= DAMPING; s.x += s.vx; s.y += s.vy;
    }});
    draw();
    alpha *= 0.985;
    if (alpha > 0.02 || dragging) {{ requestAnimationFrame(tick); }} else {{ running = false; }}
  }}
  function draw() {{
    lineEls.forEach(le => {{
      const a = sim[le.a], b = sim[le.b];
      le.el.setAttribute("x1", a.x); le.el.setAttribute("y1", a.y);
      le.el.setAttribute("x2", b.x); le.el.setAttribute("y2", b.y);
    }});
    for (const id in nodeEls) {{
      nodeEls[id].setAttribute("cx", sim[id].x); nodeEls[id].setAttribute("cy", sim[id].y);
    }}
    labelEls.forEach(l => {{
      l.el.setAttribute("x", sim[l.id].x + sim[l.id].r + 2);
      l.el.setAttribute("y", sim[l.id].y + 3);
    }});
  }}

  // ---- 視圖變換：縮放 + 平移 ----
  let scale = 1, tx = 0, ty = 0;
  function applyView() {{ root.setAttribute("transform",
    "translate(" + tx + "," + ty + ") scale(" + scale + ")"); }}
  svg.addEventListener("wheel", ev => {{
    ev.preventDefault();
    scale = Math.min(5, Math.max(0.3, scale * (ev.deltaY < 0 ? 1.1 : 0.9)));
    applyView();
  }}, {{ passive: false }});
  // 畫布平移：在空白處按住拖曳
  let panning = null;
  svg.addEventListener("pointerdown", ev => {{
    if (dragging) return;
    panning = {{x: ev.clientX, y: ev.clientY, tx: tx, ty: ty}};
    svg.style.cursor = "grabbing";
  }});
  svg.addEventListener("pointermove", ev => {{
    if (!panning) return;
    tx = panning.tx + (ev.clientX - panning.x);
    ty = panning.ty + (ev.clientY - panning.y);
    applyView();
  }});
  const endPan = () => {{ panning = null; svg.style.cursor = "grab"; }};
  svg.addEventListener("pointerup", endPan);
  svg.addEventListener("pointerleave", endPan);
  // 點空白處清除聚焦
  svg.addEventListener("click", () => {{ if (focusId) {{ focusId = null; refresh(); }} }});
  resetBtn.addEventListener("click", () => {{
    scale = 1; tx = 0; ty = 0; applyView();
    focusId = null; query = ""; search.value = "";
    GROUPS.forEach(x => groups[x.key] = true);
    legend.querySelectorAll("input").forEach(cb => cb.checked = true);
    alpha = Math.max(alpha, 0.6); refresh(); startSim();
  }});

  container.appendChild(svg);
  const hint = document.createElement("div"); hint.className = "g-hint";
  hint.textContent = "拖曳節點可固定位置 · 空白處拖曳平移 · 滾輪縮放 · 點節點聚焦鄰域 · hover 顯示名稱";
  container.appendChild(hint);

  draw(); refresh();
  startSim();
}}

// 依序建立面板
networkGraph(card("🕸️ 知識 · 詞 · 專案 關聯網絡", true), D.graph);
barChart(card("📊 各分類關鍵字數"), D.categories.map(c => [c.title || c.id, (c.keywords||[]).length])
           .sort((a,b)=>b[1]-a[1]));
barChart(card("📈 cold notes 時間趨勢"), D.stats.timeline);
barChart(card("🏷️ 標籤熱度 Top"), D.stats.tags);
barChart(card("📁 專案分布"), D.stats.projects);
barChart(card("🔄 品質佔比（raw/reviewed）"), D.stats.quality);
</script>
</body>
</html>"""


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
    nodes, edges = build_graph(data["categories"], data["experience"],
                               data["coldnotes"], args.max_keywords)
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
