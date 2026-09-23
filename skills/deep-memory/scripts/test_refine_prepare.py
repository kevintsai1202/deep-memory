# refine_prepare.py 測試：待精煉條目讀取、分群分包、重複標註、目標建議、工作包與 manifest 輸出。
# 檢索（find_candidates）需要 chromadb / ONNX，測試以注入的候選資料取代，只驗證純邏輯。
import json
import tempfile
import unittest
from pathlib import Path

import refine_prepare as rp


def _note(line, topic, project="p1", skill="general", tags=None, content="內容", date="2026-09-20",
          quality="raw", memory_type="knowledge"):
    """建立一筆冷庫條目（含行號）。"""
    return {"_line_no": line, "topic": topic, "content": content, "project": project, "skill": skill,
            "tags": tags or [], "date": date, "quality": quality, "memory_type": memory_type}


def _workspace(notes, kb_categories=None, exp_categories=None):
    """建立含 raw.jsonl 與兩個索引的臨時 workspace。"""
    d = Path(tempfile.mkdtemp())
    (d / "cold-notes").mkdir()
    lines = []
    for n in sorted(notes, key=lambda x: x["_line_no"]):
        while len(lines) < n["_line_no"] - 1:
            lines.append(json.dumps({"topic": "filler", "content": "x", "quality": "reviewed"}))
        clean = {k: v for k, v in n.items() if k != "_line_no"}
        lines.append(json.dumps(clean, ensure_ascii=False))
    (d / "cold-notes" / "raw.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "knowledge-base").mkdir()
    (d / "knowledge-base" / "_index.json").write_text(json.dumps(
        {"categories": kb_categories or []}, ensure_ascii=False), encoding="utf-8")
    (d / "experience").mkdir()
    (d / "experience" / "_index.json").write_text(json.dumps(
        {"categories": exp_categories or []}, ensure_ascii=False), encoding="utf-8")
    return d


class TestLoadPending(unittest.TestCase):
    def test_only_unreviewed_with_line_numbers(self):
        ws = _workspace([_note(1, "a", quality="reviewed"), _note(2, "b"), _note(4, "c")])
        notes = rp.load_pending(ws / "cold-notes" / "raw.jsonl")
        self.assertEqual([n["_line_no"] for n in notes], [2, 4])

    def test_since_and_project_filters(self):
        ws = _workspace([_note(1, "old", date="2026-09-01"), _note(2, "new", date="2026-09-22"),
                         _note(3, "other", project="p2", date="2026-09-22")])
        notes = rp.load_pending(ws / "cold-notes" / "raw.jsonl", since="2026-09-10", project="p1")
        self.assertEqual([n["topic"] for n in notes], ["new"])


class TestClusterAndPacks(unittest.TestCase):
    def test_groups_by_project_then_theme_deterministically(self):
        notes = [_note(1, "x", project="a", skill="agent-browser-cli"),
                 _note(2, "y", project="b", tags=["deploy"]),
                 _note(3, "z", project="a", skill="agent-browser-cli"),
                 _note(4, "w", project="a", tags=["jwt"])]
        packs = rp.build_packs(notes, max_pack=25)
        keys = [p["key"] for p in packs]
        self.assertEqual(keys, sorted(keys))
        by_key = {p["key"]: [n["_line_no"] for n in p["notes"]] for p in packs}
        self.assertEqual(by_key[("a", "agent-browser-cli")], [1, 3])
        self.assertEqual(by_key[("a", "jwt")], [4])
        self.assertEqual(by_key[("b", "deploy")], [2])

    def test_generic_skill_falls_back_to_first_tag(self):
        self.assertEqual(rp.theme_of(_note(1, "t", skill="deep-memory", tags=["taidoc", "jwt"])), "taidoc")
        self.assertEqual(rp.theme_of(_note(1, "t", skill="general", tags=[])), "(未分類)")

    def test_large_group_is_split_into_capped_packs(self):
        notes = [_note(i, f"t{i}", project="a", tags=["x"]) for i in range(1, 8)]
        packs = rp.build_packs(notes, max_pack=3)
        self.assertEqual([len(p["notes"]) for p in packs], [3, 3, 1])
        self.assertEqual(sorted(n["_line_no"] for p in packs for n in p["notes"]), list(range(1, 8)))


class TestDuplicates(unittest.TestCase):
    def test_flags_near_identical_topics_and_identical_content(self):
        notes = [_note(1, "refresh token 遺失 AD 工號 bug 修正並部署 206", content="修正 AuthController"),
                 _note(2, "refresh token 遺失 AD 工號 bug 修正並部署到 206", content="部署後驗證"),
                 _note(3, "完全不同的主題", content="同一段內容"),
                 _note(4, "另一個標題", content="同一段內容"),
                 _note(5, "無關", content="其他內容")]
        dups = rp.find_duplicates(notes)
        self.assertEqual(dups[2], [1])
        self.assertEqual(dups[4], [3])
        self.assertNotIn(1, dups)
        self.assertNotIn(5, dups)


class TestDuplicatesShareAPack(unittest.TestCase):
    def test_duplicate_joins_the_pack_of_its_earliest_original(self):
        # 同一件事在不同 skill 下各寫一筆：主題分群會分開，但使用者決定（方案 A）要併進同一包交給子代理判斷
        notes = [_note(1, "206 部署對話記憶功能完成", project="p", skill="deep-memory", tags=["deploy"]),
                 _note(2, "206 部署對話記憶功能完成！", project="p", skill="agent-browser-cli")]
        dups = rp.find_duplicates(notes)
        packs = rp.build_packs(notes, max_pack=25, duplicates=dups)
        self.assertEqual(len(packs), 1)
        self.assertEqual([n["_line_no"] for n in packs[0]["notes"]], [1, 2])


class TestSuggestTarget(unittest.TestCase):
    def test_prefers_file_of_strong_candidates(self):
        cands = [{"path": "knowledge-base\\spring-boot-devops.md#x", "score": 0.9},
                 {"path": "knowledge-base/spring-boot-devops.md#y", "score": 0.7},
                 {"path": "experience/skill-agent-browser-cli.md#z", "score": 0.5}]
        self.assertEqual(rp.suggest_target(_note(1, "t"), cands, {}), "knowledge-base/spring-boot-devops.md")

    def test_falls_back_to_keyword_match_when_candidates_weak(self):
        kw = {"knowledge-base/taidoc-helpdesk-auth.md": ["refresh token", "employeeId"],
              "knowledge-base/workflow.md": ["git"]}
        note = _note(1, "refresh token 換發後 employeeId 遺失")
        self.assertEqual(rp.suggest_target(note, [{"path": "x.md#a", "score": 0.1}], kw),
                         "knowledge-base/taidoc-helpdesk-auth.md")

    def test_vector_similarity_uses_its_own_threshold(self):
        # 預設快速模式的分數是向量相似度（相關約 0.90~0.94、無關約 0.86），門檻不能沿用重排分數的 0.5
        strong = [{"path": "knowledge-base/taidoc-helpdesk-auth.md#a", "score": 0.93, "kind": "vector"}]
        weak = [{"path": "knowledge-base/taidoc-helpdesk-auth.md#a", "score": 0.86, "kind": "vector"}]
        self.assertEqual(rp.suggest_target(_note(1, "t"), strong, {}), "knowledge-base/taidoc-helpdesk-auth.md")
        self.assertEqual(rp.suggest_target(_note(1, "t"), weak, {}), rp.UNKNOWN_TARGET)

    def test_distance_to_similarity(self):
        self.assertAlmostEqual(rp.distance_to_similarity(0.14), 0.93)
        self.assertAlmostEqual(rp.distance_to_similarity(0.0), 1.0)

    def test_unknown_when_nothing_matches(self):
        self.assertEqual(rp.suggest_target(_note(1, "zzz"), [], {}), "(新分類？)")


class TestWriteWorkdir(unittest.TestCase):
    def test_writes_packs_and_manifest_covering_every_line_once(self):
        notes = [_note(2, "a", project="p", tags=["x"]), _note(5, "b", project="q", tags=["y"]),
                 _note(9, "c", project="p", tags=["x"])]
        ws = _workspace(notes, kb_categories=[{"id": "workflow", "file": "workflow.md", "keywords": ["git"]}])
        cands = {2: [{"path": "knowledge-base/workflow.md#g", "score": 0.8, "snippet": "既有條目"}]}
        out = rp.prepare(ws, notes, candidates=cands, max_pack=25, run_id="test-run")
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        lines = [e["line"] for e in manifest["entries"]]
        self.assertEqual(sorted(lines), [2, 5, 9])
        self.assertEqual(len(lines), len(set(lines)))
        self.assertTrue(all(len(e["hash"]) == 16 for e in manifest["entries"]))
        pack_files = sorted(out.glob("pack-*.md"))
        self.assertEqual(len(pack_files), len(manifest["packs"]))
        first = pack_files[0].read_text(encoding="utf-8")
        self.assertIn("L2", first)
        self.assertIn("knowledge-base/workflow.md#g", first)
        self.assertIn("### TARGET:", first)       # 草稿格式契約寫在工作包開頭
        self.assertIn("## REVIEWED", first)

    def test_note_hash_is_stable_and_content_based(self):
        a = rp.note_hash(_note(1, "t", content="c"))
        self.assertEqual(a, rp.note_hash(_note(99, "t", content="c", date="2020-01-01")))
        self.assertNotEqual(a, rp.note_hash(_note(1, "t", content="c2")))


if __name__ == "__main__":
    unittest.main()
