# -*- coding: utf-8 -*-
"""indexer 的 schema 偵測與增量比對測試（需 venv，會用到 chromadb 記憶體模式）"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chromadb
from chromadb.api.models.Collection import Collection

import indexer
import kb_reader


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


class TestBackfillHotEntries(unittest.TestCase):
    """回填熱庫 content_hash：2026-08-01 對正式庫遷移時，因為少了這一步，
    337 筆文字完全沒變的熱庫條目被 build_plan 誤判為 text-changed 而
    重新計算 embedding，多花 126 秒。這裡釘住修復後的行為——回填之後，
    文字未變的 v1 熱庫條目不會再落入 embed_ids。
    """

    def test_v1_hot_entry_is_not_reembedded_after_backfill(self):
        text = "這是一筆熱庫條目的全文，內容從未變動過。"
        col = make_collection()
        # v1 庫的熱庫 metadata 真實樣貌：只有 memory_type / path / source，
        # 沒有 content_hash，也沒有 schema_version（見 backfill_hot_entries docstring）。
        col.upsert(ids=["knowledge-base/foo.md#bar"], embeddings=[[0.1, 0.2]],
                   documents=[text],
                   metadatas=[{"path": "knowledge-base/foo.md#bar",
                               "source": "hot", "memory_type": "knowledge"}])

        indexer.backfill_hot_entries(col)

        # 回填後應該補上 content_hash 與 schema_version，且雜湊要與
        # kb_reader 實際使用的演算法一致（不是隨便塞一個值就通過）
        meta = col.get(ids=["knowledge-base/foo.md#bar"], include=["metadatas"])["metadatas"][0]
        self.assertEqual(meta["content_hash"], kb_reader.hot_entry_hash(text))
        self.assertEqual(meta["schema_version"], indexer.SCHEMA_VERSION)

        # 文字完全沒變的情況下重跑 build_plan：不該落入 embed_ids，
        # 而應直接被判定為 skipped（既非 text-changed 也非 meta_only）
        plan = indexer.build_plan(
            col, [doc("knowledge-base/foo.md#bar", text, kb_reader.hot_entry_hash(text),
                      source="hot", memory_type="knowledge")])
        self.assertNotIn("knowledge-base/foo.md#bar", plan.embed_ids)
        self.assertEqual(plan.reasons["text-changed"], 0)
        self.assertEqual(plan.skipped, 1)

    def test_cold_entries_are_left_untouched(self):
        # 冷庫的 content_hash 算不出來：它來自 raw.jsonl 的原始欄位
        # （topic/content/tags/skill/memory_type），collection 內只有
        # 組裝後給人看的顯示文字，反推不出正確雜湊。backfill_hot_entries
        # 必須完全跳過冷庫 ID（不論新舊形式），否則會寫入錯誤的 content_hash。
        col = make_collection()
        col.upsert(
            ids=["cold-notes/raw.jsonl#L1", "cold-notes#deadbeef0001"],
            embeddings=[[0.1, 0.2], [0.3, 0.4]],
            documents=["冷庫組裝後的文字一", "冷庫組裝後的文字二"],
            metadatas=[{"path": "cold-notes/raw.jsonl#L1", "source": "cold"},
                       {"path": "cold-notes#deadbeef0001", "source": "cold"}],
        )
        indexer.backfill_hot_entries(col)
        metas = col.get(ids=["cold-notes/raw.jsonl#L1", "cold-notes#deadbeef0001"],
                        include=["metadatas"])["metadatas"]
        for meta in metas:
            self.assertNotIn("content_hash", meta)

    def test_entry_with_existing_hash_is_left_alone(self):
        # 已有 content_hash 的條目不是 v1 遺留條目，不該被本函式改動
        col = make_collection()
        col.upsert(ids=["knowledge-base/foo.md#bar"], embeddings=[[0.1, 0.2]],
                   documents=["文字"],
                   metadatas=[{"path": "knowledge-base/foo.md#bar", "source": "hot",
                               "content_hash": "keepme000001",
                               "schema_version": indexer.SCHEMA_VERSION}])
        fixed = indexer.backfill_hot_entries(col)
        self.assertEqual(fixed, 0)
        meta = col.get(ids=["knowledge-base/foo.md#bar"], include=["metadatas"])["metadatas"][0]
        self.assertEqual(meta["content_hash"], "keepme000001")


class TestSyncPartialMigrationWiring(unittest.TestCase):
    """守住 backfill_hot_entries 與 migrate_to_v2 在 sync() 裡的呼叫順序契約。

    TestBackfillHotEntries 的三個測試都直接呼叫
    `indexer.backfill_hot_entries(col)`，從未經過 sync() 的
    `if detect_schema(collection) < SCHEMA_VERSION:` 判斷；TestMigrateToV2
    也從未在遷移後接著呼叫回填。這讓一個關鍵不變量沒有任何測試守著：
    backfill_hot_entries 必須跟 migrate_to_v2 平行掛在 sync() 的同一個 if
    區塊下，不能塞進 migrate_to_v2 內部——因為 migrate_to_v2 在「已無行號式
    冷庫 ID」時會提早 return。若有人把回填邏輯「順手」搬進 migrate_to_v2
    內部（看起來像合理的整理，畢竟兩者都在處理遷移），冷庫已遷移、熱庫仍是
    v1 形狀的部分遷移庫就會靜默跳過回填，337 筆熱庫誤重算的回歸會重演，
    而其餘 47 個測試一個都測不出來。

    這裡刻意直接呼叫真正的 sync()，而不是分別呼叫 migrate_to_v2 /
    backfill_hot_entries 模擬呼叫順序——手動模擬測的是「兩個函式接續呼叫
    可以運作」，測不出「回填被搬進 migrate_to_v2 內部」這種重構造成的
    退化，只有真正跑一次 sync() 才能守住它內部的實際接法。

    刻意讓來源檔案與預先塞入 collection 的內容完全一致（沒有任何新增或
    真正變動的條目），使 plan.embed_ids 維持空清單，sync() 就不會進入需要
    載入真正 ONNX 模型的分支——測試因此不依賴模型檔案是否存在、也跑得快。

    用 tempfile.mkdtemp()（而非 tempfile.TemporaryDirectory() 情境管理器）
    建立工作目錄：實測 chromadb 的 PersistentClient 在 Windows 上會讓
    sqlite 資料檔維持在被佔用狀態，結束 with 區塊時刪除目錄會拋
    PermissionError（WinError 32）。這與 TestRunLog 已經接受的取捨一致，
    不在測試層級處理清理。
    """

    def test_hot_entries_not_reembedded_when_cold_already_migrated(self):
        workspace = tempfile.mkdtemp()

        # 建最小熱庫來源檔：kb_reader 會跳過小於 200 bytes 的檔案，
        # 內容需湊到門檻以上
        kb_dir = os.path.join(workspace, "knowledge-base")
        os.makedirs(kb_dir)
        entry_body = "## 🔧 測試條目標題\n\n" + ("湊字數用的示範內容。" * 20)
        with open(os.path.join(kb_dir, "demo.md"), "w", encoding="utf-8") as f:
            f.write("# 熱庫示範檔\n\n" + entry_body)

        # 建最小冷庫來源檔
        cold_dir = os.path.join(workspace, "cold-notes")
        os.makedirs(cold_dir)
        cold_entry = {"topic": "測試主題", "date": "2026-08-01", "skill": "general",
                      "tags": ["demo"], "content": "測試內容", "memory_type": "knowledge"}
        with open(os.path.join(cold_dir, "raw.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(cold_entry, ensure_ascii=False) + "\n")

        # 直接用 production 的讀取邏輯算出真正的 id / hash / text，讓預先
        # 塞進 collection 的內容與 sync() 之後自己重新讀到的來源完全一致
        hot_doc = kb_reader.read_knowledge_base(workspace)[0]
        cold_doc = kb_reader.read_cold_notes(workspace)[0]

        # 預先塞入「部分遷移」狀態：冷庫已是內容定址新 ID、完整 v2
        # metadata；熱庫仍是 v1 形狀（只有 path/source/memory_type，
        # 沒有 content_hash、沒有 schema_version）
        db_path = os.path.join(workspace, "chroma_hybrid_db")
        client = chromadb.PersistentClient(path=db_path)
        collection = client.get_or_create_collection("hybrid_docs")
        collection.upsert(
            ids=[cold_doc["path"], hot_doc["path"]],
            embeddings=[[0.1, 0.2], [0.3, 0.4]],
            documents=[cold_doc["text"], hot_doc["text"]],
            metadatas=[indexer.build_metadata(cold_doc),
                       {"path": hot_doc["path"], "source": "hot",
                        "memory_type": hot_doc.get("memory_type")}],
        )
        # 確認前提成立：這確實是需要遷移的部分遷移狀態，不是已經是 v2
        self.assertLess(indexer.detect_schema(collection), indexer.SCHEMA_VERSION)

        result = indexer.sync(workspace, trigger="test", verbose=False)

        # 核心斷言：熱庫條目不能落入 text-changed、也不需要任何重算
        self.assertEqual(result["reasons"]["text-changed"], 0)
        self.assertEqual(result["embedded"], 0)
        self.assertEqual(result["backfilled"], 1)
        self.assertEqual(result["skipped"], 2)


class TestSyncSingleGetOptimization(unittest.TestCase):
    """sync() 在已是最新 schema 時只能讀一次 collection 快照，並可回傳 docs 供 search.py 複用。

    7,800 筆規模下每次 collection.get(include=["metadatas"]) 約 2 秒，
    原本一次 sync 會讀 5 次；這裡用計數守住「只讀 1 次」，避免日後有人
    在 assert/detect/build_plan 任一處又自己補一次 get。
    """

    def test_sync_calls_collection_get_only_once_when_up_to_date(self):
        workspace = tempfile.mkdtemp()
        kb_dir = os.path.join(workspace, "knowledge-base")
        os.makedirs(kb_dir)
        with open(os.path.join(kb_dir, "demo.md"), "w", encoding="utf-8") as f:
            f.write("# 標題\n\n## 🔧 條目一\n" + ("內容測試文字。" * 25))

        hot_doc = kb_reader.read_knowledge_base(workspace)[0]
        db_path = os.path.join(workspace, "chroma_hybrid_db")
        client = chromadb.PersistentClient(path=db_path)
        col = client.get_or_create_collection("hybrid_docs")
        col.upsert(
            ids=[hot_doc["path"]],
            embeddings=[[0.1, 0.2]],
            documents=[hot_doc["text"]],
            metadatas=[indexer.build_metadata(hot_doc)],
        )

        # 監控 collection.get 被呼叫的次數
        orig_get = Collection.get
        call_count = {"n": 0}

        def counted_get(self_col, *args, **kwargs):
            call_count["n"] += 1
            return orig_get(self_col, *args, **kwargs)

        with mock.patch.object(Collection, "get", counted_get):
            result, docs = indexer.ensure_fresh(workspace, verbose=False, return_docs=True)

        self.assertEqual(call_count["n"], 1, f"預期只呼叫 1 次 collection.get，實際呼叫了 {call_count['n']} 次")
        self.assertEqual(result["skipped"], 1)
        self.assertEqual([d["path"] for d in docs], [hot_doc["path"]])

    def test_ensure_fresh_returns_none_pair_on_failure_when_return_docs(self):
        # search.py 以 docs is None 判斷要不要自己讀檔，失敗時必須回傳可拆包的 (None, None)
        with mock.patch.object(indexer, "sync", side_effect=RuntimeError("模擬索引失敗")):
            with contextlib.redirect_stdout(io.StringIO()):
                result, docs = indexer.ensure_fresh("/nonexistent", return_docs=True)
        self.assertIsNone(result)
        self.assertIsNone(docs)


class TestSyncMigrationKeepsReviewedMatches(unittest.TestCase):
    """v1 遷移必須以「含 reviewed 的全量冷庫」配對舊向量。

    read_cold_notes 預設略過 reviewed；若遷移也用過濾後的清單，精煉過的
    舊庫（reviewed 常佔九成以上）配對率會跌破五成而中止遷移。
    """

    def test_legacy_reviewed_entry_migrates_then_gets_cleaned(self):
        workspace = tempfile.mkdtemp()
        cold_dir = os.path.join(workspace, "cold-notes")
        os.makedirs(cold_dir)
        entry = {"topic": "已精煉", "date": "2026-08-01", "skill": "general",
                 "tags": [], "content": "內容", "memory_type": "knowledge",
                 "quality": "reviewed"}
        with open(os.path.join(cold_dir, "raw.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        reviewed_doc = kb_reader.read_cold_notes(workspace, include_reviewed=True)[0]
        db_path = os.path.join(workspace, "chroma_hybrid_db")
        col = chromadb.PersistentClient(path=db_path).get_or_create_collection("hybrid_docs")
        col.upsert(ids=["cold-notes/raw.jsonl#L1"], embeddings=[[0.1, 0.2]],
                   documents=[reviewed_doc["text"]],
                   metadatas=[{"path": "cold-notes/raw.jsonl#L1", "source": "cold"}])

        with contextlib.redirect_stdout(io.StringIO()):
            result = indexer.sync(workspace, trigger="test", verbose=False)

        # 遷移成功（未拋出），且 reviewed 條目在遷移後被當成孤立向量清掉
        self.assertEqual(result["stale"], 1)
        self.assertEqual(result["embedded"], 0)
        self.assertEqual(col.count(), 0)


class TestRunLog(unittest.TestCase):
    def test_appends_one_json_line_per_run(self):
        base = Path(tempfile.mkdtemp())
        indexer.write_run_log(base, {"trigger": "update_db", "total": 10, "embedded": 1})
        indexer.write_run_log(base, {"trigger": "search-auto", "total": 10, "embedded": 0})
        lines = (base / "logs" / "index-runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["trigger"], "search-auto")

    def test_log_record_carries_timestamp_and_schema_version(self):
        base = Path(tempfile.mkdtemp())
        indexer.write_run_log(base, {"trigger": "update_db", "total": 1, "embedded": 0})
        record = json.loads((base / "logs" / "index-runs.jsonl").read_text(encoding="utf-8").strip())
        self.assertIn("ts", record)
        self.assertEqual(record["schema_version"], indexer.SCHEMA_VERSION)

    def test_io_error_returns_none_without_raising(self):
        # 無效的路徑（含禁用的路徑分隔符號 | 會導致 OSError）應該回傳 None 而非拋例外
        # 這個測試確保日誌失敗時不會中止已完成的索引
        invalid_path = "invalid|path"  # | 在所有作業系統都不允許在檔案名稱中
        result = indexer.write_run_log(invalid_path, {"trigger": "test", "total": 1, "embedded": 0})
        self.assertIsNone(result)


class TestAnomalyWarning(unittest.TestCase):
    def _plan_with(self, embed_count, reasons):
        plan = indexer.IndexPlan()
        plan.embed_ids = [f"id-{i}" for i in range(embed_count)]
        plan.reasons = reasons
        return plan

    def test_no_warning_below_threshold(self):
        plan = self._plan_with(5, {"new": 5, "text-changed": 0, "id-missing": 0})
        self.assertIsNone(indexer.format_anomaly_warning(plan, total=100))

    def test_warns_above_threshold(self):
        plan = self._plan_with(30, {"new": 0, "text-changed": 0, "id-missing": 30})
        warning = indexer.format_anomaly_warning(plan, total=100)
        self.assertIsNotNone(warning)
        self.assertIn("kb_reader.py", warning)

    def test_warns_at_threshold_boundary(self):
        # 剛好 20% 應該觸發警告（ratio >= ANOMALY_RATIO）
        plan = self._plan_with(20, {"new": 0, "text-changed": 0, "id-missing": 20})
        warning = indexer.format_anomaly_warning(plan, total=100)
        self.assertIsNotNone(warning)
        self.assertIn("20%", warning)

    def test_no_warning_when_collection_is_empty(self):
        # 首次建庫全部都是新條目，不該被當成異常
        plan = self._plan_with(100, {"new": 100, "text-changed": 0, "id-missing": 0})
        self.assertIsNone(indexer.format_anomaly_warning(plan, total=0))


class TestSchemaGuard(unittest.TestCase):
    def test_refuses_to_run_against_newer_schema(self):
        # 腳本比庫舊時必須中止，不讓舊腳本把新格式的庫改壞
        col = make_collection()
        col.upsert(ids=["cold-notes#abc123abc123"], embeddings=[[0.1, 0.2]],
                   documents=["a"],
                   metadatas=[{"path": "cold-notes#abc123abc123",
                               "content_hash": "abc123abc123",
                               "schema_version": indexer.SCHEMA_VERSION + 1}])
        with self.assertRaises(RuntimeError) as ctx:
            indexer.assert_schema_supported(col)
        self.assertIn("升級", str(ctx.exception))

    def test_allows_current_and_older_schema(self):
        col = make_collection()
        col.upsert(ids=["cold-notes/raw.jsonl#L1"], embeddings=[[0.1, 0.2]],
                   documents=["a"], metadatas=[{"path": "cold-notes/raw.jsonl#L1"}])
        indexer.assert_schema_supported(col)   # 不應拋出


class TestEnsureFreshFailSafe(unittest.TestCase):
    """索引失敗不得阻斷檢索——檢索是讀取路徑，不該被寫入路徑的問題癱瘓"""

    def test_returns_none_and_warns_when_sync_fails(self):
        with mock.patch.object(indexer, "sync", side_effect=RuntimeError("模擬索引失敗")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = indexer.ensure_fresh("/nonexistent")
        self.assertIsNone(result)
        self.assertIn("WARN", buf.getvalue())
        self.assertIn("模擬索引失敗", buf.getvalue())

    def test_passes_through_result_on_success(self):
        with mock.patch.object(indexer, "sync", return_value={"embedded": 3}) as m:
            result = indexer.ensure_fresh("/some/path")
        self.assertEqual(result, {"embedded": 3})
        self.assertEqual(m.call_args.kwargs["trigger"], "search-auto")


if __name__ == "__main__":
    unittest.main()
