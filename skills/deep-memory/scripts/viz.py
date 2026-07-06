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
