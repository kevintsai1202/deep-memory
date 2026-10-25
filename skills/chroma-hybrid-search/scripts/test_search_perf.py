# -*- coding: utf-8 -*-
"""search.py 重排效能相關行為的單元測試（標準庫 unittest，需 venv）"""
import argparse
import unittest
from unittest import mock

import search


class TestSearchRerankerReuseAndTruncation(unittest.TestCase):
    """兩段式檢索不得重複載入 OnnxReranker，且重排截斷不影響回傳全文"""

    def _args(self, **overrides):
        defaults = dict(
            query="測試查詢",
            mode="hybrid-rerank",
            limit=3,
            candidate_limit=5,
            min_score=0.35,
            skill=None,
            tag=None,
            memory_type=None,
            rerank_max_chars=100,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_reranker_instantiated_once_across_project_and_fallback_passes(self):
        """專案輪分數皆低於 min_score 而退回全庫輪時，OnnxReranker 只能被建立一次，
        且專案輪已評過分的候選在全庫輪直接沿用分數、不再送進重排器"""
        fake_col = mock.MagicMock()
        # 專案輪只找到 doc1；全庫輪找到 doc1 與 doc2
        fake_col.query.side_effect = [{"ids": [["doc1"]]}, {"ids": [["doc1", "doc2"]]}]

        short_text = "專案筆記"
        long_text = "前段關鍵重點。" * 50  # 350 字元，超過 rerank_max_chars=100
        doc1 = {"path": "doc1", "text": short_text, "project": "my-proj"}
        doc2 = {"path": "doc2", "text": long_text}

        init_count = {"n": 0}
        seen_pairs = []

        class FakeReranker:
            def __init__(self):
                init_count["n"] += 1

            def predict(self, pairs):
                seen_pairs.extend(pairs)
                # doc1 低於 0.35（觸發全庫輪），doc2 高分
                return [0.1 if text == short_text else 0.9 for _, text in pairs]

        args = self._args()
        with mock.patch.object(search, "OnnxReranker", FakeReranker):
            reranker_holder = {}
            # 第一輪（模擬專案輪）
            res1 = search.run_search([doc1], fake_col, args, query_embedding=[[0.1]], reranker_holder=reranker_holder)
            self.assertEqual(res1, [])
            # 第二輪（模擬全庫 fallback 輪）
            res2 = search.run_search([doc1, doc2], fake_col, args, query_embedding=[[0.1]], reranker_holder=reranker_holder)

        self.assertEqual(init_count["n"], 1, "OnnxReranker 不應在兩輪搜尋中被重複載入")
        # doc1 只評一次分：全庫輪沿用專案輪的分數
        self.assertEqual([text for _, text in seen_pairs].count(short_text), 1)
        # 送進 reranker 的 doc2 文字必須被截斷至 rerank_max_chars (100)
        self.assertEqual(len(seen_pairs[-1][1]), 100)
        # 但最終回傳給呼叫端的 text 必須是完整原文（350 字元）
        self.assertEqual([h["path"] for h in res2], ["doc2"])
        self.assertEqual(res2[0]["text"], long_text)

    def test_rerank_max_chars_zero_disables_truncation(self):
        """--rerank-max-chars 0 代表不截斷，送進重排器的是完整原文"""
        fake_col = mock.MagicMock()
        fake_col.query.return_value = {"ids": [["doc1"]]}
        long_text = "前段關鍵重點。" * 50
        docs = [{"path": "doc1", "text": long_text}]
        seen_pairs = []

        class FakeReranker:
            def predict(self, pairs):
                seen_pairs.extend(pairs)
                return [0.9]

        with mock.patch.object(search, "OnnxReranker", FakeReranker):
            search.run_search(docs, fake_col, self._args(rerank_max_chars=0), query_embedding=[[0.1]])
        self.assertEqual(seen_pairs[0][1], long_text)


class TestSharedE5(unittest.TestCase):
    """補索引與查詢在同一行程內共用一個 E5 模型實例，不重複載入（約 6 秒）"""

    def setUp(self):
        import onnx_models
        self.onnx_models = onnx_models
        onnx_models._SHARED_E5 = None
        self.addCleanup(setattr, onnx_models, "_SHARED_E5", None)

    def test_get_shared_e5_loads_once(self):
        init_count = {"n": 0}

        class FakeE5:
            def __init__(self):
                init_count["n"] += 1

        with mock.patch.object(self.onnx_models, "E5OnnxEmbeddingFunction", FakeE5):
            a = self.onnx_models.get_shared_e5()
            b = self.onnx_models.get_shared_e5()
        self.assertIs(a, b)
        self.assertEqual(init_count["n"], 1)

    def test_sync_embeds_with_shared_instance(self):
        """indexer.sync 有新條目要算向量時，必須透過 get_shared_e5 取得模型"""
        import json
        import os
        import tempfile
        import indexer

        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "cold-notes"))
        with open(os.path.join(ws, "cold-notes", "raw.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"topic": "t", "content": "c"}, ensure_ascii=False) + "\n")

        fake_ef = mock.MagicMock(side_effect=lambda texts: [[0.1, 0.2] for _ in texts])
        with mock.patch.object(self.onnx_models, "get_shared_e5", return_value=fake_ef) as m:
            result = indexer.sync(ws, trigger="test", verbose=False)
        m.assert_called_once()
        self.assertEqual(result["embedded"], 1)


if __name__ == "__main__":
    unittest.main()
