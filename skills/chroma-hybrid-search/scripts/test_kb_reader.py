# -*- coding: utf-8 -*-
"""kb_reader 語意雜湊與文件讀取的單元測試（標準庫 unittest，不需 venv）"""
import json
import tempfile
import unittest
from pathlib import Path

import kb_reader


class TestSemanticHash(unittest.TestCase):
    """語意雜湊必須只對「影響檢索語意的欄位」敏感"""

    def _entry(self, **overrides):
        base = {
            "date": "2026-08-01", "time": "10:30",
            "topic": "主題A", "content": "內容B",
            "tags": ["x", "y"], "skill": "chroma-hybrid-search",
            "memory_type": "knowledge", "project": "deep-memory",
            "quality": "raw",
        }
        base.update(overrides)
        return base

    def test_hash_is_12_hex_chars(self):
        h = kb_reader.semantic_hash(self._entry())
        self.assertEqual(len(h), 12)
        self.assertRegex(h, r"^[0-9a-f]{12}$")

    def test_same_content_same_hash_regardless_of_position(self):
        # 冷庫條目搬到不同行號後雜湊不變，這是取代行號 ID 的前提
        self.assertEqual(
            kb_reader.semantic_hash(self._entry()),
            kb_reader.semantic_hash(self._entry()),
        )

    def test_display_only_fields_do_not_affect_hash(self):
        # date/time/project/quality 不進雜湊：精煉標記 reviewed 不得觸發重算
        baseline = kb_reader.semantic_hash(self._entry())
        for field, value in (("date", "2099-12-31"), ("time", "23:59"),
                             ("project", "other-project"), ("quality", "reviewed")):
            with self.subTest(field=field):
                self.assertEqual(baseline, kb_reader.semantic_hash(self._entry(**{field: value})))

    def test_semantic_fields_change_hash(self):
        baseline = kb_reader.semantic_hash(self._entry())
        for field, value in (("topic", "主題Z"), ("content", "內容Z"),
                             ("tags", ["z"]), ("skill", "other-skill"),
                             ("memory_type", "experience")):
            with self.subTest(field=field):
                self.assertNotEqual(baseline, kb_reader.semantic_hash(self._entry(**{field: value})))

    def test_missing_memory_type_uses_inferred_value(self):
        # 舊條目沒有 memory_type，雜湊須採 infer_memory_type 的推斷結果，
        # 否則 migrate_memory_type.py 回填後會被判為內容變動
        without = self._entry()
        del without["memory_type"]
        with_inferred = self._entry(memory_type=kb_reader.infer_memory_type(without))
        self.assertEqual(kb_reader.semantic_hash(without), kb_reader.semantic_hash(with_inferred))


class TestHotEntryHash(unittest.TestCase):
    def test_hash_is_12_hex_chars(self):
        h = kb_reader.hot_entry_hash("## 🔧 標題\n內容")
        self.assertEqual(len(h), 12)
        self.assertRegex(h, r"^[0-9a-f]{12}$")

    def test_different_text_different_hash(self):
        self.assertNotEqual(kb_reader.hot_entry_hash("A"), kb_reader.hot_entry_hash("B"))


class TestDocsCarryContentHash(unittest.TestCase):
    """讀取結果必須帶 content_hash，供 indexer 做增量比對"""

    def _workspace(self):
        d = Path(tempfile.mkdtemp())
        (d / "knowledge-base").mkdir()
        (d / "experience").mkdir()
        (d / "cold-notes").mkdir()
        # 檔案需 >= 200 bytes 才會被 read_knowledge_base 採用
        (d / "knowledge-base" / "demo.md").write_text(
            "# 標題\n\n## 🔧 條目一\n" + "內容甲" * 40 + "\n\n## 🔧 條目二\n" + "內容乙" * 40,
            encoding="utf-8")
        (d / "cold-notes" / "raw.jsonl").write_text(
            json.dumps({"date": "2026-08-01", "topic": "t", "content": "c",
                        "tags": ["a"], "skill": "general",
                        "memory_type": "knowledge"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return d

    def test_cold_docs_have_content_hash(self):
        docs = kb_reader.read_cold_notes(self._workspace())
        self.assertEqual(len(docs), 1)
        self.assertRegex(docs[0]["content_hash"], r"^[0-9a-f]{12}$")

    def test_hot_docs_have_content_hash(self):
        docs = kb_reader.read_knowledge_base(self._workspace())
        self.assertEqual(len(docs), 2)
        for doc in docs:
            self.assertRegex(doc["content_hash"], r"^[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
