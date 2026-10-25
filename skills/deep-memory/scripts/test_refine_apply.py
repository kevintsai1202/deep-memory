# refine_apply.py 測試：解析子代理草稿並套用到熱庫、更新索引、標記冷庫 reviewed；
# 驗證 dry-run 不寫入、寫入前備份、行號與雜湊防呆、REVIEWED 必須涵蓋 manifest 全部行號。
import json
import tempfile
import unittest
from pathlib import Path

import refine_apply as ra
import refine_prepare as rp


EXISTING_KB = """# spring-boot-devops

## 🔧 既有條目 A
**Date:** 2026-01-01
**Context:** 舊情境
**Best Practices:**
- 舊重點
**Source cold store entries:** cold-notes/raw.jsonl#L1

## 🔧 既有條目 B
**Date:** 2026-01-02
**Context:** 另一個
**Best Practices:**
- B 重點
"""


def _setup(notes, drafts, kb_extra=None):
    """建立 workspace（raw.jsonl、熱庫、索引）與 refine-work 目錄（manifest + drafts），回傳 (ws, work_dir)。"""
    ws = Path(tempfile.mkdtemp())
    (ws / "cold-notes").mkdir()
    raw_lines = [json.dumps({"topic": "已精煉", "content": "x", "quality": "reviewed"}, ensure_ascii=False)]
    for n in notes:
        raw_lines.append(json.dumps({k: v for k, v in n.items() if k != "_line_no"}, ensure_ascii=False))
    (ws / "cold-notes" / "raw.jsonl").write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
    (ws / "knowledge-base").mkdir()
    (ws / "knowledge-base" / "spring-boot-devops.md").write_text(EXISTING_KB, encoding="utf-8")
    (ws / "knowledge-base" / "_index.json").write_text(json.dumps({"categories": [
        {"id": "spring-boot-devops", "file": "spring-boot-devops.md", "title": "x", "keywords": ["flyway"]}]},
        ensure_ascii=False), encoding="utf-8")
    (ws / "experience").mkdir()
    (ws / "experience" / "_index.json").write_text(json.dumps({"categories": []}), encoding="utf-8")
    loaded = rp.load_pending(ws / "cold-notes" / "raw.jsonl")
    work = rp.prepare(ws, loaded, candidates={}, run_id="t")
    for name, text in drafts.items():
        (work / name).write_text(text, encoding="utf-8")
    return ws, work


NOTES = [{"topic": "A 主題", "content": "A 內容", "quality": "raw", "project": "p", "skill": "general", "tags": ["x"]},
         {"topic": "B 主題", "content": "B 內容", "quality": "raw", "project": "p", "skill": "general", "tags": ["x"]}]

FULL_DRAFT = """### TARGET: knowledge-base/spring-boot-devops.md
## 🔧 新條目
**Date:** 2026-09-23
**Context:** 新情境
**Best Practices:**
- 新重點
**Source cold store entries:** cold-notes/raw.jsonl#L2

### APPEND-TO: knowledge-base/spring-boot-devops.md :: 既有條目 A
- 追加重點
**Source cold store entries:** cold-notes/raw.jsonl#L3

## KEYWORDS
- spring-boot-devops: autoconfiguration, flyway

## REVIEWED
L2 | promoted→knowledge-base/spring-boot-devops.md
L3 | skipped: 已被既有條目 A 涵蓋
"""


class TestParse(unittest.TestCase):
    def test_parses_all_block_types(self):
        d = ra.parse_drafts(FULL_DRAFT + "\n### NEW CATEGORY: ai-crm-dev | AI CRM 開發 | rbac, rfm\n"
                            "## 🔧 啟動\n**Date:** 2026-09-23\n")
        self.assertEqual(d["targets"][0][0], "knowledge-base/spring-boot-devops.md")
        self.assertIn("## 🔧 新條目", d["targets"][0][1])
        self.assertEqual(d["appends"][0][:2], ("knowledge-base/spring-boot-devops.md", "既有條目 A"))
        self.assertEqual(d["keywords"]["spring-boot-devops"], ["autoconfiguration", "flyway"])
        self.assertEqual(d["reviewed"], {2: "promoted→knowledge-base/spring-boot-devops.md",
                                         3: "skipped: 已被既有條目 A 涵蓋"})
        self.assertEqual(d["new_categories"][0][:3], ("ai-crm-dev", "AI CRM 開發", ["rbac", "rfm"]))


class TestApply(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        ws, work = _setup(NOTES, {"pack-01.drafts.md": FULL_DRAFT})
        before_kb = (ws / "knowledge-base" / "spring-boot-devops.md").read_text(encoding="utf-8")
        before_raw = (ws / "cold-notes" / "raw.jsonl").read_text(encoding="utf-8")
        report = ra.apply(ws, work, dry_run=True)
        self.assertEqual(report["entries_added"], 1)
        self.assertEqual(report["lines_marked"], 2)
        self.assertEqual((ws / "knowledge-base" / "spring-boot-devops.md").read_text(encoding="utf-8"), before_kb)
        self.assertEqual((ws / "cold-notes" / "raw.jsonl").read_text(encoding="utf-8"), before_raw)
        self.assertFalse((work / "backup").exists())

    def test_apply_adds_entry_appends_bullets_keywords_and_marks_reviewed(self):
        ws, work = _setup(NOTES, {"pack-01.drafts.md": FULL_DRAFT})
        ra.apply(ws, work)
        kb = (ws / "knowledge-base" / "spring-boot-devops.md").read_text(encoding="utf-8")
        self.assertIn("## 🔧 新條目", kb)
        # 追加點插在條目 A 的 Source 行之前，且 Source 行合併了新來源
        a_block = kb.split("## 🔧 既有條目 A", 1)[1].split("## 🔧", 1)[0]
        self.assertLess(a_block.index("- 追加重點"), a_block.index("**Source cold store entries:**"))
        self.assertIn("L1", a_block)
        self.assertIn("L3", a_block)
        idx = json.loads((ws / "knowledge-base" / "_index.json").read_text(encoding="utf-8"))
        self.assertEqual(idx["categories"][0]["keywords"], ["flyway", "autoconfiguration"])
        raw = [json.loads(l) for l in (ws / "cold-notes" / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["quality"] for r in raw], ["reviewed", "reviewed", "reviewed"])
        self.assertTrue((work / "backup" / "knowledge-base" / "spring-boot-devops.md").exists())
        self.assertTrue((work / "backup" / "cold-notes" / "raw.jsonl").exists())

    def test_append_to_entry_without_source_line_goes_to_end_of_entry(self):
        draft = ("### APPEND-TO: knowledge-base/spring-boot-devops.md :: 既有條目 B\n- B 追加\n"
                 "**Source cold store entries:** cold-notes/raw.jsonl#L2\n\n"
                 "## REVIEWED\nL2 | promoted→x\nL3 | skipped: 一次性\n")
        ws, work = _setup(NOTES, {"pack-01.drafts.md": draft})
        ra.apply(ws, work)
        kb = (ws / "knowledge-base" / "spring-boot-devops.md").read_text(encoding="utf-8")
        b_block = kb.split("## 🔧 既有條目 B", 1)[1]
        self.assertIn("- B 重點\n- B 追加", b_block)
        self.assertIn("**Source cold store entries:** cold-notes/raw.jsonl#L2", b_block)

    def test_new_category_and_new_experience_file_are_indexed(self):
        draft = ("### NEW CATEGORY: ai-crm-dev | AI CRM 開發 | rbac, rfm\n## 🔧 啟動\n**Date:** 2026-09-23\n\n"
                 "### TARGET: experience/skill-foo.md\n## 🔧 foo 技巧\n**Skill:** foo\n**keywords:** a, b\n\n"
                 "## REVIEWED\nL2 | promoted→knowledge-base/ai-crm-dev.md\nL3 | promoted→experience/skill-foo.md\n")
        ws, work = _setup(NOTES, {"pack-01.drafts.md": draft})
        ra.apply(ws, work)
        self.assertIn("# AI CRM 開發", (ws / "knowledge-base" / "ai-crm-dev.md").read_text(encoding="utf-8"))
        kb_idx = json.loads((ws / "knowledge-base" / "_index.json").read_text(encoding="utf-8"))
        self.assertIn({"id": "ai-crm-dev", "file": "ai-crm-dev.md", "title": "AI CRM 開發",
                       "keywords": ["rbac", "rfm"]}, kb_idx["categories"])
        exp_idx = json.loads((ws / "experience" / "_index.json").read_text(encoding="utf-8"))
        self.assertEqual(exp_idx["categories"][0]["file"], "skill-foo.md")
        self.assertTrue((ws / "experience" / "skill-foo.md").read_text(encoding="utf-8").startswith("## 🔧 foo 技巧"))


class TestValidation(unittest.TestCase):
    def _assert_rejected_without_writes(self, drafts, **kwargs):
        ws, work = _setup(NOTES, drafts)
        before = (ws / "knowledge-base" / "spring-boot-devops.md").read_text(encoding="utf-8")
        with self.assertRaises(ra.ApplyError):
            ra.apply(ws, work, **kwargs)
        self.assertEqual((ws / "knowledge-base" / "spring-boot-devops.md").read_text(encoding="utf-8"), before)
        return ws, work

    def test_missing_reviewed_line_is_rejected_unless_partial(self):
        draft = FULL_DRAFT.replace("L3 | skipped: 已被既有條目 A 涵蓋\n", "")
        self._assert_rejected_without_writes({"pack-01.drafts.md": draft})
        ws, work = _setup(NOTES, {"pack-01.drafts.md": draft})
        report = ra.apply(ws, work, allow_partial=True)
        self.assertEqual(report["lines_marked"], 1)

    def test_reviewed_line_not_in_manifest_is_rejected(self):
        self._assert_rejected_without_writes({"pack-01.drafts.md": FULL_DRAFT + "L99 | skipped: 不在本批\n"})

    def test_unknown_append_title_is_rejected(self):
        self._assert_rejected_without_writes(
            {"pack-01.drafts.md": FULL_DRAFT.replace(":: 既有條目 A", ":: 不存在的條目")})

    def test_target_without_entry_heading_is_rejected(self):
        self._assert_rejected_without_writes(
            {"pack-01.drafts.md": FULL_DRAFT.replace("## 🔧 新條目", "新條目（少了標題）")})

    def test_unknown_knowledge_base_file_must_use_new_category(self):
        self._assert_rejected_without_writes(
            {"pack-01.drafts.md": FULL_DRAFT.replace("### TARGET: knowledge-base/spring-boot-devops.md",
                                                     "### TARGET: knowledge-base/not-exist.md")})

    def test_hash_mismatch_is_rejected(self):
        ws, work = _setup(NOTES, {"pack-01.drafts.md": FULL_DRAFT})
        raw = ws / "cold-notes" / "raw.jsonl"
        lines = raw.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace("A 內容", "被改掉的內容")
        raw.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(ra.ApplyError):
            ra.apply(ws, work)


if __name__ == "__main__":
    unittest.main()
