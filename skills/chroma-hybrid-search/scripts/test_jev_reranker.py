# -*- coding: utf-8 -*-
"""Jev 雲端重排器與重排器選擇的單元測試（標準庫 unittest，不連網）"""
import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import jev_reranker
import search

# 測試時要清掉的環境變數，避免開發者本機設定滲進測試
_ENV_KEYS = ("DEEP_MEMORY_RERANKER", "TYPESAFE_API_KEY", "JEV_API_KEY")


def _clean_env(**extra):
    """回傳一份去掉重排相關變數、再加上 extra 的環境變數字典"""
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    env.update(extra)
    return env


def _workspace(config=None, dotenv=None):
    """建立暫存工作目錄，可選擇寫入 config.json 與 .env"""
    ws = tempfile.mkdtemp()
    if config is not None:
        with open(os.path.join(ws, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f)
    if dotenv is not None:
        with open(os.path.join(ws, ".env"), "w", encoding="utf-8") as f:
            f.write(dotenv)
    return ws


JEV_CONFIG = {"reranker": {"provider": "jev", "jev": {"min_score": 0.6}}}


class TestLoadRerankerConfig(unittest.TestCase):
    """優先順序：--reranker > DEEP_MEMORY_RERANKER > config.json > 預設 local"""

    def test_default_is_local(self):
        with mock.patch.dict(os.environ, _clean_env(), clear=True):
            cfg = jev_reranker.load_reranker_config(_workspace())
        self.assertEqual(cfg["provider"], "local")

    def test_config_file_selects_jev_with_defaults_filled(self):
        with mock.patch.dict(os.environ, _clean_env(TYPESAFE_API_KEY="k"), clear=True):
            cfg = jev_reranker.load_reranker_config(_workspace(JEV_CONFIG))
        self.assertEqual(cfg["provider"], "jev")
        self.assertEqual(cfg["jev"]["min_score"], 0.6)
        self.assertEqual(cfg["jev"]["model"], "jev-1.14.0")
        self.assertEqual(cfg["jev"]["api_key"], "k")

    def test_env_overrides_config_file(self):
        with mock.patch.dict(os.environ, _clean_env(DEEP_MEMORY_RERANKER="local"), clear=True):
            cfg = jev_reranker.load_reranker_config(_workspace(JEV_CONFIG))
        self.assertEqual(cfg["provider"], "local")

    def test_cli_overrides_env(self):
        with mock.patch.dict(os.environ, _clean_env(DEEP_MEMORY_RERANKER="local", TYPESAFE_API_KEY="k"),
                             clear=True):
            cfg = jev_reranker.load_reranker_config(_workspace(), cli_provider="jev")
        self.assertEqual(cfg["provider"], "jev")

    def test_unknown_provider_or_bad_file_falls_back_to_local(self):
        ws = _workspace()
        with open(os.path.join(ws, "config.json"), "w", encoding="utf-8") as f:
            f.write("{不是 JSON")
        with mock.patch.dict(os.environ, _clean_env(), clear=True), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(jev_reranker.load_reranker_config(ws)["provider"], "local")
            self.assertEqual(jev_reranker.load_reranker_config(_workspace(), cli_provider="xyz")["provider"],
                             "local")

    def test_api_key_from_workspace_dotenv(self):
        with mock.patch.dict(os.environ, _clean_env(), clear=True):
            cfg = jev_reranker.load_reranker_config(_workspace(JEV_CONFIG, dotenv='TYPESAFE_API_KEY="abc"\n'))
        self.assertEqual(cfg["jev"]["api_key"], "abc")

    def test_missing_key_keeps_provider_but_no_key(self):
        # 缺金鑰不在載入設定時就改判 local：由 run_search 統一走退回流程並在輸出標明原因
        with mock.patch.dict(os.environ, _clean_env(), clear=True):
            cfg = jev_reranker.load_reranker_config(_workspace(JEV_CONFIG))
        self.assertEqual(cfg["provider"], "jev")
        self.assertIsNone(cfg["jev"]["api_key"])


def _ok(prob):
    """組一個成功的 System One 回應"""
    return {"model": "jev-1.14.0", "answers": {"helpful": {"type": "noul", "noul": prob}},
            "usage": {"input_tokens": 10, "output_tokens": 1}}


class TestJevReranker(unittest.TestCase):
    def _reranker(self):
        return jev_reranker.JevReranker(api_key="k", model="jev-1.14.0", timeout=5, concurrency=4)

    def test_predict_preserves_order_and_parses_noul(self):
        def fake_post(url, body, headers, timeout):
            state = json.loads(body)["state"]
            return _ok({"甲": 0.9, "乙": 0.2, "丙": 0.5}[state["candidate"]])

        with mock.patch.object(jev_reranker, "_post", side_effect=fake_post) as m:
            scores = self._reranker().predict([["q", "甲"], ["q", "乙"], ["q", "丙"]])
        self.assertEqual(scores, [0.9, 0.2, 0.5])
        # 送出的請求帶固定版本、Bearer 金鑰與評測時的題目
        body = json.loads(m.call_args_list[0].args[1])
        self.assertEqual(body["model"], "jev-1.14.0")
        self.assertEqual(body["questions"]["helpful"], jev_reranker.QUESTION_ZH)
        self.assertEqual(m.call_args_list[0].args[2]["Authorization"], "Bearer k")

    def test_retries_once_then_succeeds(self):
        calls = {"n": 0}

        def flaky(url, body, headers, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("逾時")
            return _ok(0.7)

        with mock.patch.object(jev_reranker, "_post", side_effect=flaky):
            self.assertEqual(self._reranker().predict([["q", "甲"]]), [0.7])
        self.assertEqual(calls["n"], 2)

    def test_any_unrecovered_failure_fails_whole_batch(self):
        def post(url, body, headers, timeout):
            if json.loads(body)["state"]["candidate"] == "壞":
                raise TimeoutError("逾時")
            return _ok(0.8)

        with mock.patch.object(jev_reranker, "_post", side_effect=post):
            with self.assertRaises(jev_reranker.JevError):
                self._reranker().predict([["q", "好"], ["q", "壞"]])

    def test_empty_pairs(self):
        with mock.patch.object(jev_reranker, "_post") as m:
            self.assertEqual(self._reranker().predict([]), [])
        m.assert_not_called()


class TestRunSearchWithJev(unittest.TestCase):
    """run_search 依 holder 內的設定選用 Jev，失敗時整輪退回本機 bge"""

    def _args(self):
        return argparse.Namespace(query="查詢", mode="hybrid-rerank", limit=3, candidate_limit=5,
                                  min_score=0.35, skill=None, tag=None, memory_type=None,
                                  rerank_max_chars=800)

    def _cfg(self, api_key="k"):
        return {"provider": "jev", "jev": {"model": "jev-1.14.0", "min_score": 0.6, "timeout": 5,
                                           "concurrency": 4, "api_key": api_key}}

    def _col(self, *id_lists):
        col = mock.MagicMock()
        col.query.side_effect = [{"ids": [ids]} for ids in id_lists]
        return col

    def test_jev_scores_and_jev_threshold_without_loading_local_model(self):
        docs = [{"path": "a", "text": "甲"}, {"path": "b", "text": "乙"}]
        holder = {"instance": None, "scores": {}, "config": self._cfg()}

        class FakeJev:
            def __init__(self, **kwargs):
                pass

            def predict(self, pairs):
                # 0.5 介於 bge 門檻 0.35 與 Jev 門檻 0.6 之間：Jev 門檻生效時必須被濾掉
                return [0.9 if t == "甲" else 0.5 for _, t in pairs]

        local = mock.MagicMock()
        with mock.patch.object(search, "JevReranker", FakeJev), mock.patch.object(search, "OnnxReranker", local):
            hits = search.run_search(docs, self._col(["a", "b"]), self._args(), [[0.1]], reranker_holder=holder)
        local.assert_not_called()
        self.assertEqual([h["path"] for h in hits], ["a"])
        self.assertEqual(holder["used"], "jev")

    def test_failure_falls_back_to_local_and_second_pass_skips_jev(self):
        docs = [{"path": "a", "text": "甲"}, {"path": "b", "text": "乙"}]
        holder = {"instance": None, "scores": {}, "config": self._cfg()}
        jev_calls = {"n": 0}

        class BrokenJev:
            def __init__(self, **kwargs):
                pass

            def predict(self, pairs):
                jev_calls["n"] += 1
                raise jev_reranker.JevError("timeout")

        class FakeLocal:
            def predict(self, pairs):
                # 0.5 在 bge 門檻 0.35 之上：退回本機時要用 --min-score，不能沿用 Jev 的 0.6
                return [0.5 for _ in pairs]

        err = io.StringIO()
        with mock.patch.object(search, "JevReranker", BrokenJev), \
                mock.patch.object(search, "OnnxReranker", FakeLocal), contextlib.redirect_stderr(err):
            col = self._col(["a"], ["a", "b"])
            first = search.run_search(docs[:1], col, self._args(), [[0.1]], reranker_holder=holder)
            second = search.run_search(docs, col, self._args(), [[0.1]], reranker_holder=holder)
        self.assertEqual([h["path"] for h in first], ["a"])
        self.assertEqual([h["path"] for h in second], ["a", "b"])
        self.assertEqual(jev_calls["n"], 1, "Jev 失敗後第二輪不應再呼叫 Jev")
        self.assertTrue(holder["jev_failed"])
        self.assertEqual(holder["used"], "local (jev fallback: timeout)")
        self.assertIn("Jev", err.getvalue())
        # 分數快取依重排器分開存放
        self.assertTrue(all(k[0] == "local" for k in holder["scores"]))

    def test_missing_key_falls_back_with_reason(self):
        docs = [{"path": "a", "text": "甲"}]
        holder = {"instance": None, "scores": {}, "config": self._cfg(api_key=None)}

        class FakeLocal:
            def predict(self, pairs):
                return [0.9 for _ in pairs]

        with mock.patch.object(search, "OnnxReranker", FakeLocal), contextlib.redirect_stderr(io.StringIO()):
            hits = search.run_search(docs, self._col(["a"]), self._args(), [[0.1]], reranker_holder=holder)
        self.assertEqual(len(hits), 1)
        self.assertEqual(holder["used"], "local (jev fallback: missing TYPESAFE_API_KEY)")


if __name__ == "__main__":
    unittest.main()
