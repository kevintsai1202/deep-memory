# -*- coding: utf-8 -*-
"""indexer 的 schema 偵測與增量比對測試（需 venv，會用到 chromadb 記憶體模式）"""
import os
import tempfile
import unittest

import chromadb

import indexer


def make_collection(name="col"):
    """
    創建一個新的空 Chroma 集合用於測試。

    EphemeralClient() 會落到共享的 in-memory System 快取（key 為固定的 "ephemeral"），
    因此同一 process 內多次呼叫拿到的是同一個 in-memory database。
    為隔離測試，在創建同名新集合前先刪除舊集合（若存在）。
    """
    client = chromadb.EphemeralClient()
    try:
        client.delete_collection(name)
    except chromadb.errors.NotFoundError:
        # 集合不存在是預期情況，只在第一個測試時發生
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


class TestBackupDb(unittest.TestCase):
    """_backup_db 保護的是使用者無法重建的正式資料庫，三種行為都必須有測試釘住"""

    def test_real_directory_is_copied_with_identical_content(self):
        with tempfile.TemporaryDirectory() as parent:
            src = os.path.join(parent, "db")
            os.mkdir(src)
            with open(os.path.join(src, "data.bin"), "w", encoding="utf-8") as f:
                f.write("hello")
            dest = indexer._backup_db(src)
            self.assertTrue(os.path.isdir(dest))
            with open(os.path.join(dest, "data.bin"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "hello")

    def test_none_path_skips_backup(self):
        # 記憶體測試（db_path=None）不該嘗試備份，也不該報錯
        self.assertIsNone(indexer._backup_db(None))

    def test_non_directory_path_raises(self):
        # 路徑打錯或資料庫還沒建立時必須 raise，不能無聲跳過保護
        with tempfile.TemporaryDirectory() as parent:
            missing = os.path.join(parent, "does-not-exist")
            with self.assertRaises(RuntimeError):
                indexer._backup_db(missing)


class TestMigrateToV2(unittest.TestCase):
    """遷移必須零重算：只換 ID 與補 metadata，向量原封搬移"""

    def _legacy_collection(self):
        """建立一個含行號式 ID 的舊格式 collection。

        必須沿用 make_collection 的 delete-then-create 隔離手法：chromadb 1.5.9 的
        EphemeralClient 落在 key 寫死為 "ephemeral" 的 class-level 快取
        （chromadb/api/shared_system_client.py:65-67），同一 process 內每次呼叫拿到的
        是同一個 in-memory system——不先清掉同名 collection，第二個測試就會撞名失敗。
        """
        col = make_collection("legacy")
        col.upsert(
            ids=["cold-notes/raw.jsonl#L1", "cold-notes/raw.jsonl#L2", "kb.md#slug"],
            embeddings=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
            documents=["冷一", "冷二", "熱一"],
            metadatas=[{"path": "cold-notes/raw.jsonl#L1", "source": "cold"},
                       {"path": "cold-notes/raw.jsonl#L2", "source": "cold"},
                       {"path": "kb.md#slug", "source": "hot"}],
        )
        return col

    def _cold_docs(self):
        return [doc("cold-notes#111111111111", "冷一", "111111111111", line=1),
                doc("cold-notes#222222222222", "冷二", "222222222222", line=2)]

    def test_ids_are_replaced_and_count_preserved(self):
        col = indexer.migrate_to_v2(self._legacy_collection(), None, self._cold_docs())
        self.assertEqual(col.count(), 3)
        ids = set(col.get(include=[])["ids"])
        self.assertEqual(ids, {"cold-notes#111111111111", "cold-notes#222222222222", "kb.md#slug"})

    def test_embeddings_are_preserved_bit_for_bit(self):
        col = indexer.migrate_to_v2(self._legacy_collection(), None, self._cold_docs())
        got = col.get(ids=["cold-notes#111111111111"], include=["embeddings"])
        self.assertAlmostEqual(float(got["embeddings"][0][0]), 0.1, places=6)
        self.assertAlmostEqual(float(got["embeddings"][0][1]), 0.2, places=6)

    def test_line_number_kept_in_metadata(self):
        col = indexer.migrate_to_v2(self._legacy_collection(), None, self._cold_docs())
        meta = col.get(ids=["cold-notes#222222222222"], include=["metadatas"])["metadatas"][0]
        self.assertEqual(meta["line"], 2)
        self.assertEqual(meta["schema_version"], indexer.SCHEMA_VERSION)

    def test_migration_is_idempotent(self):
        # 中途失敗後重跑不得損壞資料：第二次執行應為無操作
        col = indexer.migrate_to_v2(self._legacy_collection(), None, self._cold_docs())
        col = indexer.migrate_to_v2(col, None, self._cold_docs())
        self.assertEqual(col.count(), 3)
        # 驗證的是「不再有行號式 ID」，而不是「整庫已達 SCHEMA_VERSION」——
        # migrate_to_v2 的職責只到冷庫 ID 換址為止，熱庫條目的 schema_version
        # 是後續 build_plan 走 metadata-only 路徑（零 embedding 成本）補上的。
        # 用 detect_schema 斷言會誤把「遷移未完成整個 sync 流程」判成遷移失敗。
        remaining = [i for i in col.get(include=[])["ids"]
                     if i.startswith(indexer.LEGACY_COLD_PREFIX)]
        self.assertEqual(remaining, [])

    def test_entries_with_deleted_source_are_dropped(self):
        # 來源已被刪除的舊條目沒有對應的新 doc，遷移不搬它；
        # 其舊 ID 一併清除，不留孤兒——留著會讓 detect_schema 永遠停在舊版本，
        # 導致每次 sync 都重新備份並重跑一次遷移
        col = indexer.migrate_to_v2(self._legacy_collection(), None,
                                    [doc("cold-notes#111111111111", "冷一", "111111111111", line=1)])
        ids = set(col.get(include=[])["ids"])
        self.assertIn("cold-notes#111111111111", ids)
        self.assertNotIn("cold-notes/raw.jsonl#L2", ids)

    def test_low_match_rate_aborts_without_deleting(self):
        # 冷庫 text 組裝格式若變動，文字比對會大量落空。此時明確中止且不刪任何
        # legacy ID——把問題留在原地讓人檢查，好過默默丟掉已算好的向量再花數分鐘重算。
        col = self._legacy_collection()
        with self.assertRaises(RuntimeError) as ctx:
            indexer.migrate_to_v2(col, None,
                                  [doc("cold-notes#999999999999", "完全不同的文字",
                                       "999999999999", line=1)])
        self.assertIn("配對", str(ctx.exception))
        ids = set(col.get(include=[])["ids"])
        self.assertIn("cold-notes/raw.jsonl#L1", ids)
        self.assertIn("cold-notes/raw.jsonl#L2", ids)

    def test_duplicate_target_id_aborts_without_deleting(self):
        # 兩筆 legacy 條目對應到同一個新 ID（語意欄位相同但 text 不同，例如只差日期）：
        # 必須在任何寫入之前中止，訊息要指出是 raw.jsonl 需要去重。
        # 注意此情境的配對率是 100%，不會被 match-rate 守衛攔下，走的確實是重複偵測那條路。
        col = self._legacy_collection()
        dup = [doc("cold-notes#aaaaaaaaaaaa", "冷一", "aaaaaaaaaaaa", line=1),
               doc("cold-notes#aaaaaaaaaaaa", "冷二", "aaaaaaaaaaaa", line=2)]
        with self.assertRaises(RuntimeError) as ctx:
            indexer.migrate_to_v2(col, None, dup)
        self.assertIn("cold-notes#aaaaaaaaaaaa", str(ctx.exception))
        ids = set(col.get(include=[])["ids"])
        self.assertIn("cold-notes/raw.jsonl#L1", ids)


if __name__ == "__main__":
    unittest.main()
