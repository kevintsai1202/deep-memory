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
