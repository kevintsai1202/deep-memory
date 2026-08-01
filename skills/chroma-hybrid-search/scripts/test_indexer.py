# -*- coding: utf-8 -*-
"""indexer 的 schema 偵測與增量比對測試（需 venv，會用到 chromadb 記憶體模式）"""
import unittest

import chromadb

import indexer


def make_collection(name="col"):
    """每個測試用獨立的記憶體 client，避免相互汙染"""
    client = chromadb.EphemeralClient()
    # 使用 get_or_create 而非 create，避免重複創建時的衝突
    # 每次測試開始前手動清理集合內容
    try:
        client.delete_collection(name)
    except:
        pass
    return client.create_collection(name)


def doc(path, text, content_hash, source="cold", **extra):
    d = {"path": path, "text": text, "content_hash": content_hash, "source": source}
    d.update(extra)
    return d


class TestDetectSchema(unittest.TestCase):
    def test_empty_collection_is_current_version(self):
        self.assertEqual(indexer.detect_schema(make_collection()), indexer.SCHEMA_VERSION)

    def test_line_number_ids_detected_as_v1(self):
        col = make_collection()
        col.upsert(ids=["cold-notes/raw.jsonl#L1"], embeddings=[[0.1, 0.2]],
                   documents=["a"], metadatas=[{"path": "cold-notes/raw.jsonl#L1"}])
        self.assertEqual(indexer.detect_schema(col), 1)

    def test_missing_schema_version_metadata_is_v1(self):
        col = make_collection()
        col.upsert(ids=["kb.md#slug"], embeddings=[[0.1, 0.2]],
                   documents=["a"], metadatas=[{"path": "kb.md#slug"}])
        self.assertEqual(indexer.detect_schema(col), 1)

    def test_fully_migrated_collection_is_v2(self):
        col = make_collection()
        col.upsert(ids=["cold-notes#abc123abc123"], embeddings=[[0.1, 0.2]],
                   documents=["a"],
                   metadatas=[{"path": "cold-notes#abc123abc123",
                               "content_hash": "abc123abc123",
                               "schema_version": indexer.SCHEMA_VERSION}])
        self.assertEqual(indexer.detect_schema(col), indexer.SCHEMA_VERSION)

    def test_mixed_state_reports_oldest_version(self):
        # 遷移中斷留下的混合狀態必須被判為需要遷移，不能因抽樣抽到新的就放過
        col = make_collection()
        col.upsert(ids=["cold-notes#abc123abc123", "kb.md#slug"],
                   embeddings=[[0.1, 0.2], [0.3, 0.4]], documents=["a", "b"],
                   metadatas=[{"path": "cold-notes#abc123abc123",
                               "schema_version": indexer.SCHEMA_VERSION},
                              {"path": "kb.md#slug"}])
        self.assertEqual(indexer.detect_schema(col), 1)


class TestBuildPlan(unittest.TestCase):
    def _seeded(self):
        """預先放入一筆 v2 條目，作為比對基準"""
        col = make_collection()
        col.upsert(ids=["cold-notes#aaaaaaaaaaaa"], embeddings=[[0.1, 0.2]],
                   documents=["舊文字"],
                   metadatas=[{"path": "cold-notes#aaaaaaaaaaaa",
                               "source": "cold", "content_hash": "aaaaaaaaaaaa",
                               "schema_version": indexer.SCHEMA_VERSION}])
        return col

    def test_unchanged_entry_is_skipped(self):
        plan = indexer.build_plan(self._seeded(),
                                  [doc("cold-notes#aaaaaaaaaaaa", "舊文字", "aaaaaaaaaaaa")])
        self.assertEqual(plan.embed_ids, [])
        self.assertEqual(plan.skipped, 1)

    def test_new_entry_is_classified_as_new(self):
        plan = indexer.build_plan(self._seeded(), [
            doc("cold-notes#aaaaaaaaaaaa", "舊文字", "aaaaaaaaaaaa"),
            doc("cold-notes#bbbbbbbbbbbb", "新文字", "bbbbbbbbbbbb"),
        ])
        self.assertEqual(plan.embed_ids, ["cold-notes#bbbbbbbbbbbb"])
        self.assertEqual(plan.reasons["new"], 1)
        self.assertEqual(plan.reasons["text-changed"], 0)

    def test_changed_hash_is_classified_as_text_changed(self):
        # 同一個熱庫 ID 但 content_hash 不同 → 內容真的被改過
        col = make_collection()
        col.upsert(ids=["kb.md#slug"], embeddings=[[0.1, 0.2]], documents=["舊"],
                   metadatas=[{"path": "kb.md#slug", "source": "hot",
                               "content_hash": "old000000000",
                               "schema_version": indexer.SCHEMA_VERSION}])
        plan = indexer.build_plan(col, [doc("kb.md#slug", "新", "new000000000", source="hot")])
        self.assertEqual(plan.embed_ids, ["kb.md#slug"])
        self.assertEqual(plan.reasons["text-changed"], 1)

    def test_vanished_id_counts_as_id_missing_and_stale(self):
        # 舊 ID 消失 + 新 ID 出現 = 位移或版本不一致的指紋
        plan = indexer.build_plan(self._seeded(),
                                  [doc("cold-notes#cccccccccccc", "文字", "cccccccccccc")])
        self.assertEqual(plan.stale_ids, ["cold-notes#aaaaaaaaaaaa"])
        self.assertEqual(plan.reasons["id-missing"], 1)

    def test_metadata_only_change_avoids_embedding(self):
        # content_hash 相同、其他 metadata 變動 → 走 update，零 embedding 成本
        plan = indexer.build_plan(
            self._seeded(),
            [doc("cold-notes#aaaaaaaaaaaa", "舊文字", "aaaaaaaaaaaa", project="newproj")])
        self.assertEqual(plan.embed_ids, [])
        self.assertEqual(plan.meta_ids, ["cold-notes#aaaaaaaaaaaa"])


class TestBuildMetadata(unittest.TestCase):
    def test_omits_empty_fields(self):
        # Chroma 不接受 None 與空陣列，無值欄位必須整個省略
        meta = indexer.build_metadata(doc("cold-notes#a", "t", "a", tags=[], skill=None))
        self.assertNotIn("tags", meta)
        self.assertNotIn("skill", meta)

    def test_includes_schema_version_and_hash(self):
        meta = indexer.build_metadata(doc("cold-notes#a", "t", "aaaaaaaaaaaa"))
        self.assertEqual(meta["schema_version"], indexer.SCHEMA_VERSION)
        self.assertEqual(meta["content_hash"], "aaaaaaaaaaaa")

    def test_line_number_preserved_for_cold_entries(self):
        meta = indexer.build_metadata(doc("cold-notes#a", "t", "a", line=723))
        self.assertEqual(meta["line"], 723)


if __name__ == "__main__":
    unittest.main()
