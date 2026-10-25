# -*- coding: utf-8 -*-
"""
jev_reranker.py
可選的雲端重排器：用 TypeSafe Jev（System One）的 Noul 題替候選評分，以及重排器的選擇設定。

為什麼有這個選項：本機 bge-reranker-base 在 CPU 上載入約 9 秒、10 筆推論 10～17 秒，
是查詢最慢的一段；Jev 同樣 10 筆平行送出約 1.6 秒。2026-10-25 以 26 個查詢、149 筆
盲標評測：P@1 bge 0.77 → Jev 0.85、P@3 0.58 → 0.67；Jev 門檻 0.6 的精確率／召回率
0.67／0.98，優於 bge 門檻 0.35 的 0.56／0.93（評測腳本：scripts/bench/jev_rerank_eval.py）。

代價是查詢與每個候選的前 800 字會送到 TypeSafe 雲端，所以預設仍用本機，需明確開啟。
只用標準函式庫，不增加相依。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API_URL = "https://api.typesafe.ai/v1/systemone"
KEY_NAMES = ("TYPESAFE_API_KEY", "JEV_API_KEY")
PROVIDERS = ("local", "jev")

# Jev 的預設值：版本固定（別名 jev-latest 會在無預警下換版，門檻是在 1.14.0 上量的）
JEV_DEFAULTS = {"model": "jev-1.14.0", "min_score": 0.6, "timeout": 10, "concurrency": 10}

# 與評測時完全相同的題目；改題目要重跑評測、重新決定門檻
QUESTION_ZH = {
    "type": "noul",
    "instructions": "`candidate` 是一筆工作記憶筆記。它是否包含能直接幫助回答或處理 `query` 的資訊？",
    "criteria": {
        "true": "筆記的主題與查詢直接相關，提供可用的做法、結論、指令、參數或事實",
        "false": "筆記只是與查詢共用少數字詞，實際主題不同，或內容無法幫助回答查詢",
    },
}


class JevError(Exception):
    """Jev 評分失敗（逾時、HTTP 錯誤、回應格式不符）；訊息是給輸出看的簡短原因"""


def _warn(msg):
    """警告一律寫到 stderr：search.py 的 stdout 是給 Agent 解析的 JSON"""
    print(f"[WARN] {msg}", file=sys.stderr)


def load_api_key(workspace=None, env_file=None):
    """依序從環境變數、指定的 env_file、<workspace>/.env 找 TYPESAFE_API_KEY／JEV_API_KEY。

    找不到回傳 None。金鑰只回傳給呼叫端，不寫進任何輸出或日誌。
    """
    for name in KEY_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    candidates = [env_file] if env_file else []
    if workspace:
        candidates.append(os.path.join(str(workspace), ".env"))
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                for name in KEY_NAMES:
                    if line.startswith(name + "="):
                        value = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if value:
                            return value
    return None


def load_reranker_config(workspace, cli_provider=None):
    """決定這次查詢用哪個重排器，回傳 {"provider": ..., "jev": {...}}。

    優先順序：--reranker 參數 > DEEP_MEMORY_RERANKER 環境變數 > <workspace>/config.json > local。
    config.json 格式：{"reranker": {"provider": "jev", "jev": {"model": ..., "min_score": ...}}}
    設定檔壞掉或 provider 不認得時警告並用 local——重排器設定不該讓搜尋整個失效。
    缺金鑰不在這裡改判 local，而是交給 run_search 走退回流程，輸出才會標明原因。
    """
    file_cfg = {}
    path = os.path.join(str(workspace), "config.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                file_cfg = (json.load(f) or {}).get("reranker") or {}
        except (OSError, ValueError, AttributeError) as exc:
            _warn(f"{path} 無法解析，重排器改用本機：{exc}")
            file_cfg = {}

    provider = (cli_provider or os.environ.get("DEEP_MEMORY_RERANKER")
                or file_cfg.get("provider") or "local").strip().lower()
    if provider not in PROVIDERS:
        _warn(f"不認得的重排器 '{provider}'（可用：{', '.join(PROVIDERS)}），改用本機")
        provider = "local"

    jev = dict(JEV_DEFAULTS)
    jev.update({k: v for k, v in (file_cfg.get("jev") or {}).items() if k in JEV_DEFAULTS})
    jev["api_key"] = load_api_key(workspace) if provider == "jev" else None
    return {"provider": provider, "jev": jev}


def _post(url, body, headers, timeout):
    """送出一次 HTTP POST 並解析 JSON 回應（獨立成函式，測試只需 mock 這一處）"""
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_retryable(exc):
    """逾時、連線錯誤、429 與 5xx 值得重試一次；4xx（金鑰錯、格式錯）重試也沒用"""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, (TimeoutError, urllib.error.URLError, ConnectionError, OSError))


def _reason(exc):
    """把例外轉成輸出用的簡短原因"""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, (TimeoutError,)) or "timed out" in str(exc):
        return "timeout"
    return type(exc).__name__


class JevReranker:
    """以 Jev Noul 題評分的重排器，介面對齊 OnnxReranker.predict（回傳 0～1 分數，順序同輸入）。

    每組（查詢, 候選）各送一個請求、平行執行：把多個候選塞進同一請求的共用 state
    會讓 Jev 低估分數（transcript-review 實測 0.68 → 0.21）。
    """

    def __init__(self, api_key, model=JEV_DEFAULTS["model"], timeout=JEV_DEFAULTS["timeout"],
                 concurrency=JEV_DEFAULTS["concurrency"]):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.concurrency = max(1, int(concurrency))

    def _score_one(self, pair):
        """評一組（查詢, 候選）；可重試的錯誤重試一次，仍失敗就丟 JevError"""
        query, candidate = pair
        body = json.dumps({"model": self.model,
                           "state": {"query": query, "candidate": candidate},
                           "questions": {"helpful": QUESTION_ZH}}, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = None
        for attempt in range(2):
            try:
                data = _post(API_URL, body, headers, self.timeout)
                return float(data["answers"]["helpful"]["noul"])
            except (KeyError, TypeError, ValueError) as exc:
                raise JevError(f"unexpected response ({type(exc).__name__})") from exc
            except Exception as exc:  # 網路與 HTTP 錯誤
                last = exc
                if attempt == 0 and _is_retryable(exc):
                    continue
                break
        raise JevError(_reason(last)) from last

    def predict(self, pairs):
        """平行評分整批候選；任何一筆最終失敗就整批丟 JevError，避免同一輪混用兩種分數尺度"""
        pairs = [tuple(p) for p in pairs]
        if not pairs:
            return []
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(pairs))) as pool:
            return list(pool.map(self._score_one, pairs))
