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
        # 非 JSON 的行應被略過而非崩潰
        cold = ['{"date":"2026-07-05","tags":[],"project":"a","quality":"raw"}',
                'THIS IS NOT JSON',
                '{"date":"2026-07-06","tags":[],"project":"b","quality":"raw"}']
        ws = self._make_ws(cold_lines=cold)
        data = viz.load_data(ws)
        self.assertEqual(len(data["coldnotes"]), 2)  # 壞行被略過
        self.assertTrue(any("raw.jsonl" in w for w in data["warnings"]))

    def test_non_dict_jsonl_line_counted_bad(self):
        # 合法 JSON 但非物件的行（如 42）應計為壞行而非崩潰
        cold = ['{"date":"2026-07-05","tags":[],"project":"a","quality":"raw"}',
                '42', '"foo"', '[1,2]']
        ws = self._make_ws(cold_lines=cold)
        data = viz.load_data(ws)
        self.assertEqual(len(data["coldnotes"]), 1)
        self.assertTrue(any("raw.jsonl" in w for w in data["warnings"]))

    def test_non_dict_index_root_degrades(self):
        # _index.json 根節點非物件（如陣列）應降級為空清單 + 警告，不崩潰
        d = Path(tempfile.mkdtemp())
        (d / "knowledge-base").mkdir()
        (d / "knowledge-base" / "_index.json").write_text("[]", encoding="utf-8")
        data = viz.load_data(d)
        self.assertEqual(data["categories"], [])
        self.assertTrue(any("knowledge-base" in w for w in data["warnings"]))

class TestAggregate(unittest.TestCase):
    def setUp(self):
        # 準備三筆 cold notes 樣本，涵蓋跨日期、重複標籤、多專案與品質
        self.cold = [
            {"date": "2026-07-05", "tags": ["auth", "ldap"], "project": "backend", "quality": "reviewed"},
            {"date": "2026-07-05", "tags": ["auth"], "project": "backend", "quality": "raw"},
            {"date": "2026-07-06", "tags": ["ui"], "project": "frontend", "quality": "raw"},
        ]

    def test_timeline_sorted_asc(self):
        # 時間軸應依日期升冪，並正確計數
        s = viz.aggregate_stats(self.cold)
        self.assertEqual(s["timeline"], [["2026-07-05", 2], ["2026-07-06", 1]])

    def test_tags_sorted_desc(self):
        # 標籤依次數降冪，auth 出現 2 次應居首
        s = viz.aggregate_stats(self.cold)
        self.assertEqual(s["tags"][0], ["auth", 2])

    def test_projects_and_quality(self):
        # 專案與品質次數彙總正確
        s = viz.aggregate_stats(self.cold)
        self.assertEqual(dict(s["projects"]).get("backend"), 2)
        self.assertEqual(dict(s["quality"]).get("raw"), 2)

    def test_empty(self):
        # 空輸入回傳全空清單，不崩潰
        s = viz.aggregate_stats([])
        self.assertEqual(s["timeline"], [])
        self.assertEqual(s["tags"], [])

class TestGraph(unittest.TestCase):
    def test_shared_keyword_connects_categories(self):
        # 兩分類共享關鍵字（大小寫視為同一）應連到同一 keyword 節點
        cats = [
            {"id": "a", "title": "a", "file": "a.md", "keywords": ["API", "x"]},
            {"id": "b", "title": "b", "file": "b.md", "keywords": ["api", "y"]},
        ]
        nodes, edges = viz.build_graph(cats, [], max_keywords=12)
        ids = {n["id"] for n in nodes}
        self.assertIn("cat:a", ids)
        self.assertIn("cat:b", ids)
        kw_nodes = [n for n in nodes if n["type"] == "keyword" and n["label"].lower() == "api"]
        self.assertEqual(len(kw_nodes), 1)
        shared_id = kw_nodes[0]["id"]
        srcs = {(e["source"], e["target"]) for e in edges}
        self.assertIn(("cat:a", shared_id), srcs)
        self.assertIn(("cat:b", shared_id), srcs)

    def test_max_keywords_cap(self):
        # 每分類最多取 max_keywords 個關鍵字
        cats = [{"id": "big", "title": "big", "file": "b.md",
                 "keywords": [f"k{i}" for i in range(50)]}]
        nodes, edges = viz.build_graph(cats, [], max_keywords=5)
        kw = [n for n in nodes if n["type"] == "keyword"]
        self.assertEqual(len(kw), 5)

    def test_empty(self):
        # 空輸入回傳空 nodes/edges
        nodes, edges = viz.build_graph([], [], max_keywords=12)
        self.assertEqual(nodes, [])
        self.assertEqual(edges, [])

class TestLayout(unittest.TestCase):
    def _graph(self):
        # 建一個小圖：兩分類共享 k2
        cats = [{"id": "a", "title": "a", "file": "", "keywords": ["k1", "k2"]},
                {"id": "b", "title": "b", "file": "", "keywords": ["k2", "k3"]}]
        return viz.build_graph(cats, [], max_keywords=12)

    def test_deterministic(self):
        # 固定種子 → 兩次計算座標完全一致
        nodes, edges = self._graph()
        p1 = viz.compute_layout(nodes, edges)
        p2 = viz.compute_layout(nodes, edges)
        self.assertEqual(p1, p2)

    def test_all_nodes_placed_in_bounds(self):
        # 每個節點都有座標且落在畫布範圍內
        nodes, edges = self._graph()
        pos = viz.compute_layout(nodes, edges, width=800, height=600)
        self.assertEqual(set(pos.keys()), {n["id"] for n in nodes})
        for x, y in pos.values():
            self.assertTrue(0 <= x <= 800)
            self.assertTrue(0 <= y <= 600)

    def test_empty(self):
        # 空圖回傳空字典
        self.assertEqual(viz.compute_layout([], []), {})

class TestRender(unittest.TestCase):
    def test_contains_markers_and_no_external_urls(self):
        # 產出應含基本標記，且不得載入任何外部資源
        data = {"categories": [{"id": "a", "title": "a", "file": "", "keywords": ["k1"]}],
                "experience": [], "coldnotes": [], "warnings": []}
        stats = {"timeline": [["2026-07-06", 1]], "tags": [["k1", 1]],
                 "projects": [["backend", 1]], "quality": [["raw", 1]]}
        nodes, edges = viz.build_graph(data["categories"], [], 12)
        pos = viz.compute_layout(nodes, edges)
        html = viz.render_html(data, stats, nodes, edges, pos, top_tags=20)
        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("記憶儀表板", html)
        self.assertIn("createElementNS", html)   # 網絡圖由 JS 動態建立 SVG
        self.assertIn("networkGraph", html)      # 6 面板之一的網絡圖渲染函式
        # 零外部相依：不得出現外部資源載入
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("href=\"http", html)
        self.assertNotIn("@import", html)

    def test_embeds_data_json(self):
        # 空資料時仍內嵌資料容器並顯示提示
        data = {"categories": [], "experience": [], "coldnotes": [], "warnings": []}
        stats = {"timeline": [], "tags": [], "projects": [], "quality": []}
        html = viz.render_html(data, stats, [], [], {}, top_tags=20)
        self.assertIn("id=\"dm-data\"", html)
        self.assertIn("尚無", html)

class TestMain(unittest.TestCase):
    def test_end_to_end_writes_html(self):
        # 端到端：給定 kb + cold notes，main 應寫出非空 HTML 並回傳 0
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
        # 空 workspace（三來源檔全缺）仍應產出 HTML 且回傳 0，不崩潰
        d = Path(tempfile.mkdtemp())
        out = d / "dash.html"
        rc = viz.main(["--workspace", str(d), "--output", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())

if __name__ == "__main__":
    unittest.main()
