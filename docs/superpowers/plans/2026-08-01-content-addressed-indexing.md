# 索引內容定址化與可觀測性改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 deep-memory 的向量索引以語意內容定址，使刪改任一條目或調整顯示格式都不再引發全庫重算，並讓每次索引執行留下可判讀的日誌。

**Architecture:** 從 `update_db.py` 抽出 `indexer.py` 承擔 schema 偵測、遷移、增量比對、寫入與日誌；`kb_reader.py` 新增語意雜湊能力，冷庫 ID 由行號改為內容雜湊、行號降級為 metadata；`update_db.py` 退化為 CLI 薄殼，`search.py` 於查詢前呼叫 `ensure_fresh()` 自動補索引。

**Tech Stack:** Python 3.11、chromadb 1.5.9、onnxruntime 1.27.0、標準庫 `unittest`（本專案**不使用 pytest**）

## Global Constraints

- **測試框架一律用標準庫 `unittest`**。`requirements.txt` 刻意維持最小依賴（6 個套件、約 200 MB），不得為了測試新增 pytest 或任何套件。
- **`kb_reader.py` 必須維持零第三方依賴**（僅標準庫）。`write_cold.py`、`seed.py`、`backup.py`、`restore.py` 同樣不得引入第三方依賴——它們設計上要能在沒有 venv 的環境執行。
- **`requirements.txt` 只能是 ASCII**：Windows cp950 環境下 pip 讀檔遇非 ASCII 會 `UnicodeDecodeError`。
- **註解一律用繁體中文**，函式需有函式級別註解，重要變數需加註解。
- Windows PowerShell 環境。venv Python 為 `& "$HOME\.deep-memory\.venv\Scripts\python"`（必須用 `&` 呼叫運算子與 `$HOME`）。
- **`SCHEMA_VERSION = 2`**，定義於 `indexer.py`，為全計畫共用常數。
- 雜湊一律取 `sha1(...).hexdigest()[:12]`（12 位十六進位）。
- 冷庫新 ID 格式 `cold-notes#<hash12>`；舊格式前綴 `cold-notes/raw.jsonl#L` 為遷移偵測依據。
- Chroma metadata **不接受 `None` 值與空陣列**，欄位無值時須整個省略。
- 所有腳本輸出中文前需 `sys.stdout.reconfigure(encoding='utf-8')`（既有慣例）。

**測試執行指令**（全計畫共用）：

```powershell
# 跑 chroma-hybrid-search 的全部測試
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_*.py" -v

# 只跑單一測試檔（把 -p 換成該檔名）
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_kb_reader.py" -v
```

---

## File Structure

| 檔案 | 責任 | 動作 |
|---|---|---|
| `skills/chroma-hybrid-search/scripts/kb_reader.py` | 讀取與切段、語意雜湊 | 修改 |
| `skills/chroma-hybrid-search/scripts/indexer.py` | schema 偵測、遷移、增量比對、寫入、日誌 | **新建** |
| `skills/chroma-hybrid-search/scripts/update_db.py` | CLI 薄殼 | 改寫 |
| `skills/chroma-hybrid-search/scripts/search.py` | 查詢前自動補索引 | 修改 |
| `skills/chroma-hybrid-search/scripts/test_kb_reader.py` | 雜湊與讀取的單元測試 | **新建** |
| `skills/chroma-hybrid-search/scripts/test_indexer.py` | 偵測、比對、遷移、日誌的單元測試 | **新建** |
| `landing-page/index.html` | changelog v6 | 修改 |

---

### Task 1: kb_reader 的語意雜湊能力

**Files:**
- Modify: `skills/chroma-hybrid-search/scripts/kb_reader.py`
- Test: `skills/chroma-hybrid-search/scripts/test_kb_reader.py`（新建）

**Interfaces:**
- Consumes: 既有 `infer_memory_type(entry)`
- Produces:
  - `semantic_hash(entry: dict) -> str`（12 位十六進位）
  - `hot_entry_hash(text: str) -> str`（12 位十六進位）
  - `read_cold_notes()` / `read_knowledge_base()` 回傳的每個 doc 新增 `content_hash` 鍵

本任務**不改變 ID 格式**——冷庫 ID 仍是行號，系統行為不變。ID 切換留到 Task 3，屆時遷移能力已就位。

- [ ] **Step 1: 寫失敗測試**

建立 `skills/chroma-hybrid-search/scripts/test_kb_reader.py`：

```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_kb_reader.py" -v
```

預期：`AttributeError: module 'kb_reader' has no attribute 'semantic_hash'`

- [ ] **Step 3: 實作雜湊函式**

在 `kb_reader.py` 頂部 import 區加入 `import hashlib`，並在 `infer_memory_type()` 之後加入：

```python
def semantic_hash(entry):
    """計算冷庫條目的語意雜湊，作為向量庫 ID 與增量比對鍵。

    刻意只涵蓋影響檢索語意的欄位（topic/content/tags/skill/memory_type），
    排除 date/time/project/quality 與所有模板字串——這讓「調整 text 組裝格式」
    或「精煉時標記 reviewed」都不會被誤判為內容變動而觸發重算。
    """
    payload = json.dumps({
        "topic":       entry.get("topic", ""),
        "content":     entry.get("content", ""),
        "tags":        entry.get("tags", []),
        "skill":       entry.get("skill", "general"),
        "memory_type": infer_memory_type(entry),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def hot_entry_hash(text):
    """計算熱庫條目的內容雜湊。

    熱庫 text 即 markdown 原文、無模板組裝，故直接雜湊全文即可，
    不存在冷庫那種「格式演進導致全庫失效」的問題。
    """
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
```

- [ ] **Step 4: 讓讀取結果帶上 content_hash**

在 `read_knowledge_base()` 中，**兩處**建立 `doc` 的地方都加入 `content_hash`：

不符範本的整檔文件（原 `doc = {"path": rel_path, "text": content, ...}`）改為：

```python
            doc = {"path": rel_path, "text": content, "source": "hot",
                   "memory_type": memory_type, "content_hash": hot_entry_hash(content)}
```

切段後的條目（原 `doc = {"path": f"{rel_path}#{slug}", ...}`）改為：

```python
            doc = {
                "path": f"{rel_path}#{slug}",
                "text": entry_text,
                "source": "hot",
                "memory_type": memory_type,
                "content_hash": hot_entry_hash(entry_text)
            }
```

在 `read_cold_notes()` 中，`doc = {...}` 加入 `content_hash`（**此時 path 仍維持行號格式**）：

```python
                doc = {
                    "path": f"cold-notes/raw.jsonl#L{i+1}",
                    "text": text,
                    "source": "cold",
                    "tags": entry.get("tags", []),
                    "skill": entry.get("skill", "general"),
                    "memory_type": memory_type,
                    "content_hash": semantic_hash(entry)
                }
```

- [ ] **Step 5: 執行測試確認通過**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_kb_reader.py" -v
```

預期：`Ran 9 tests` 且 `OK`

- [ ] **Step 6: 提交**

```powershell
& git -C "d:\GitHub\deep-memory" add skills/chroma-hybrid-search/scripts/kb_reader.py skills/chroma-hybrid-search/scripts/test_kb_reader.py
& git -C "d:\GitHub\deep-memory" commit -m @'
feat(indexer): kb_reader 新增語意雜湊與 content_hash 欄位

semantic_hash 只涵蓋 topic/content/tags/skill/memory_type，排除
date/time/project/quality 與模板字串，使顯示格式調整與精煉標記
不再被誤判為內容變動。此步不改變 ID 格式，系統行為不變。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
'@
```

---

### Task 2: indexer.py — schema 偵測與 hash 增量比對

**Files:**
- Create: `skills/chroma-hybrid-search/scripts/indexer.py`
- Test: `skills/chroma-hybrid-search/scripts/test_indexer.py`（新建）

**Interfaces:**
- Consumes: `kb_reader.semantic_hash`、`kb_reader.hot_entry_hash`
- Produces:
  - `SCHEMA_VERSION = 2`、`LEGACY_COLD_PREFIX = "cold-notes/raw.jsonl#L"`
  - `detect_schema(collection) -> int`
  - `build_metadata(doc) -> dict`
  - `IndexPlan`（具 `embed_ids`、`embed_docs`、`embed_metas`、`meta_ids`、`meta_metas`、`stale_ids`、`skipped`、`reasons`）
  - `build_plan(collection, docs) -> IndexPlan`

- [ ] **Step 1: 寫失敗測試**

建立 `skills/chroma-hybrid-search/scripts/test_indexer.py`：

```python
# -*- coding: utf-8 -*-
"""indexer 的 schema 偵測與增量比對測試（需 venv，會用到 chromadb 記憶體模式）"""
import unittest

import chromadb

import indexer


def make_collection(name="t"):
    """每個測試用獨立的記憶體 client，避免相互汙染"""
    return chromadb.EphemeralClient().create_collection(name)


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
```

- [ ] **Step 2: 執行測試確認失敗**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_indexer.py" -v
```

預期：`ModuleNotFoundError: No module named 'indexer'`

- [ ] **Step 3: 實作 indexer.py**

建立 `skills/chroma-hybrid-search/scripts/indexer.py`：

```python
# -*- coding: utf-8 -*-
"""
indexer.py
向量索引的 schema 偵測、遷移、增量比對、寫入與執行日誌。

從 update_db.py 抽出，讓 search.py 能共用同一套增量邏輯做自動補索引，
避免兩邊各寫一份而發散——兩邊對「什麼算變動」的認定必須永遠一致。
"""
import os

# 索引 schema 版本。變更 ID 格式或 metadata 必要欄位時才遞增。
SCHEMA_VERSION = 2

# 舊版冷庫 ID 前綴（行號定址）。存在此前綴即代表該庫尚未遷移。
LEGACY_COLD_PREFIX = "cold-notes/raw.jsonl#L"


def detect_schema(collection):
    """判斷向量庫的 schema 版本，決定要不要遷移。

    以「是否存在行號式 ID」與「metadata 的 schema_version」兩個訊號綜合判定，
    並取最舊的版本——遷移中斷造成的混合狀態必須被判為需要遷移，
    不能因為抽樣抽到已遷移的那筆就放過。
    """
    ids = collection.get(include=[])["ids"]
    if not ids:
        return SCHEMA_VERSION
    if any(i.startswith(LEGACY_COLD_PREFIX) for i in ids):
        return 1
    metas = collection.get(include=["metadatas"])["metadatas"]
    versions = {(m or {}).get("schema_version", 1) for m in metas}
    return min(versions) if versions else SCHEMA_VERSION


def build_metadata(doc):
    """把 kb_reader 的 doc 轉為 Chroma metadata。

    Chroma 不接受 None 與空陣列，因此無值欄位一律整個省略而非填預設值——
    這也讓 search.py 能區分「沒有 project 的舊條目」與「屬於某專案的新條目」。
    """
    meta = {
        "path": doc["path"],
        "source": doc.get("source", "hot"),
        "content_hash": doc["content_hash"],
        "schema_version": SCHEMA_VERSION,
    }
    for key in ("skill", "memory_type", "project"):
        if doc.get(key):
            meta[key] = doc[key]
    if doc.get("tags"):
        meta["tags"] = doc["tags"]
    if doc.get("line") is not None:
        meta["line"] = doc["line"]
    return meta


class IndexPlan:
    """一次索引執行的待辦清單：要重算的、只更新 metadata 的、要刪除的。

    先算出完整計畫再執行，好處是能在動用 embedding 模型之前就知道規模，
    據此決定要不要載入模型、要不要發出異常警告。
    """

    def __init__(self):
        self.embed_ids, self.embed_docs, self.embed_metas = [], [], []
        self.meta_ids, self.meta_metas = [], []
        self.stale_ids = []
        self.skipped = 0
        # 重算原因分類，寫入日誌供事後判讀「這次為什麼慢」
        self.reasons = {"new": 0, "text-changed": 0, "id-missing": 0}

    @property
    def is_empty(self):
        return not (self.embed_ids or self.meta_ids or self.stale_ids)


def build_plan(collection, docs):
    """比對來源文件與向量庫現況，產出增量計畫。

    只讀 metadatas 不讀 documents：比對鍵是 content_hash，
    毋須把整個知識庫的全文（數十 MB）搬進記憶體。
    """
    existing = collection.get(include=["metadatas"])
    existing_metas = dict(zip(existing["ids"], existing["metadatas"]))

    plan = IndexPlan()
    ids = [d["path"] for d in docs]
    plan.stale_ids = sorted(set(existing_metas) - set(ids))

    for doc in docs:
        doc_id = doc["path"]
        meta = build_metadata(doc)
        old = existing_metas.get(doc_id)

        if old is None:
            plan.embed_ids.append(doc_id)
            plan.embed_docs.append(doc["text"])
            plan.embed_metas.append(meta)
            plan.reasons["new"] += 1
        elif (old or {}).get("content_hash") != doc["content_hash"]:
            plan.embed_ids.append(doc_id)
            plan.embed_docs.append(doc["text"])
            plan.embed_metas.append(meta)
            plan.reasons["text-changed"] += 1
        elif old != meta:
            plan.meta_ids.append(doc_id)
            plan.meta_metas.append(meta)
        else:
            plan.skipped += 1

    # 「新 ID 出現」與「舊 ID 消失」成對發生的部分歸類為 id-missing。
    #
    # 內容定址下，冷庫條目一旦被編輯，其 ID 就會改變——表現正是「舊 ID 孤立
    # ＋ 新 ID 新增」，所以冷庫永遠不會走 text-changed 分支（那隻對 ID 穩定的
    # 熱庫條目發生）。單筆編輯與大規模位移在型態上相同、只差在量級，因此這裡
    # 不設門檻硬分，只如實記錄成對數量，交由 20% 的警告門檻判定是否異常。
    shifted = min(plan.reasons["new"], len(plan.stale_ids))
    plan.reasons["id-missing"] = shifted
    plan.reasons["new"] -= shifted

    return plan
```

- [ ] **Step 4: 執行測試確認通過**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_indexer.py" -v
```

預期：`Ran 13 tests` 且 `OK`

- [ ] **Step 5: 提交**

```powershell
& git -C "d:\GitHub\deep-memory" add skills/chroma-hybrid-search/scripts/indexer.py skills/chroma-hybrid-search/scripts/test_indexer.py
& git -C "d:\GitHub\deep-memory" commit -m @'
feat(indexer): 新增 indexer.py 承擔 schema 偵測與 hash 增量比對

比對鍵從完整組裝文字改為 metadata 的 content_hash，get 只讀
metadatas 不讀 documents。重算原因分為 new/text-changed/id-missing，
其中 id-missing 是 ID 位移或版本不一致的指紋。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
'@
```

---

### Task 3: 冷庫內容定址 ID 與零重算遷移

**Files:**
- Modify: `skills/chroma-hybrid-search/scripts/kb_reader.py:154`（`read_cold_notes` 的 path）
- Modify: `skills/chroma-hybrid-search/scripts/indexer.py`
- Modify: `skills/chroma-hybrid-search/scripts/test_kb_reader.py`
- Modify: `skills/chroma-hybrid-search/scripts/test_indexer.py`

**Interfaces:**
- Consumes: Task 2 的 `SCHEMA_VERSION`、`LEGACY_COLD_PREFIX`、`build_metadata`
- Produces: `migrate_to_v2(client, collection, db_path, cold_docs) -> collection`

- [ ] **Step 1: 寫失敗測試**

在 `test_kb_reader.py` 的 `TestDocsCarryContentHash` 類別內追加：

```python
    def test_cold_doc_id_is_content_addressed(self):
        # 冷庫 ID 必須是內容定址，行號降級為 line 欄位
        docs = kb_reader.read_cold_notes(self._workspace())
        self.assertEqual(docs[0]["path"], f"cold-notes#{docs[0]['content_hash']}")
        self.assertEqual(docs[0]["line"], 1)
```

在 `test_indexer.py` 追加遷移測試類別：

```python
class TestMigrateToV2(unittest.TestCase):
    """遷移必須零重算：只換 ID 與補 metadata，向量原封搬移"""

    def _legacy_collection(self):
        client = chromadb.EphemeralClient()
        col = client.create_collection("legacy")
        col.upsert(
            ids=["cold-notes/raw.jsonl#L1", "cold-notes/raw.jsonl#L2", "kb.md#slug"],
            embeddings=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
            documents=["冷一", "冷二", "熱一"],
            metadatas=[{"path": "cold-notes/raw.jsonl#L1", "source": "cold"},
                       {"path": "cold-notes/raw.jsonl#L2", "source": "cold"},
                       {"path": "kb.md#slug", "source": "hot"}],
        )
        return client, col

    def _cold_docs(self):
        return [doc("cold-notes#111111111111", "冷一", "111111111111", line=1),
                doc("cold-notes#222222222222", "冷二", "222222222222", line=2)]

    def test_ids_are_replaced_and_count_preserved(self):
        client, col = self._legacy_collection()
        col = indexer.migrate_to_v2(client, col, None, self._cold_docs())
        self.assertEqual(col.count(), 3)
        ids = set(col.get(include=[])["ids"])
        self.assertEqual(ids, {"cold-notes#111111111111", "cold-notes#222222222222", "kb.md#slug"})

    def test_embeddings_are_preserved_bit_for_bit(self):
        client, col = self._legacy_collection()
        col = indexer.migrate_to_v2(client, col, None, self._cold_docs())
        got = col.get(ids=["cold-notes#111111111111"], include=["embeddings"])
        self.assertAlmostEqual(float(got["embeddings"][0][0]), 0.1, places=6)
        self.assertAlmostEqual(float(got["embeddings"][0][1]), 0.2, places=6)

    def test_line_number_kept_in_metadata(self):
        client, col = self._legacy_collection()
        col = indexer.migrate_to_v2(client, col, None, self._cold_docs())
        meta = col.get(ids=["cold-notes#222222222222"], include=["metadatas"])["metadatas"][0]
        self.assertEqual(meta["line"], 2)
        self.assertEqual(meta["schema_version"], indexer.SCHEMA_VERSION)

    def test_migration_is_idempotent(self):
        # 中途失敗後重跑不得損壞資料：第二次執行應為無操作
        client, col = self._legacy_collection()
        col = indexer.migrate_to_v2(client, col, None, self._cold_docs())
        col = indexer.migrate_to_v2(client, col, None, self._cold_docs())
        self.assertEqual(col.count(), 3)
        self.assertEqual(indexer.detect_schema(col), indexer.SCHEMA_VERSION)
```

- [ ] **Step 2: 執行測試確認失敗**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_*.py" -v
```

預期：`test_cold_doc_id_is_content_addressed` FAIL、`migrate_to_v2` 相關 `AttributeError`

- [ ] **Step 3: 冷庫 ID 改內容定址**

在 `kb_reader.py` 的 `read_cold_notes()` 中，把 doc 的組裝改為（`semantic_hash` 先算出來供 path 與 content_hash 共用）：

```python
                content_hash = semantic_hash(entry)
                doc = {
                    # 內容定址：行號會因刪改中間行而位移，位移會讓位移點之後
                    # 的每一筆都被誤判為「新增 + 孤立」而觸發全庫重算
                    "path": f"cold-notes#{content_hash}",
                    "text": text,
                    "source": "cold",
                    "line": i + 1,        # 行號降級為 metadata，供人閱讀來源引用
                    "tags": entry.get("tags", []),
                    "skill": entry.get("skill", "general"),
                    "memory_type": memory_type,
                    "content_hash": content_hash
                }
```

- [ ] **Step 4: 實作遷移**

在 `indexer.py` 的 `build_plan()` 之前加入：

```python
def _backup_db(db_path):
    """遷移前完整複製資料庫目錄，讓任何中斷都能原樣還原。

    27 MB 規模下實測約 0.06 秒，這個保險非常便宜。
    db_path 為 None 時（記憶體測試）跳過。
    """
    if not db_path or not os.path.isdir(db_path):
        return None
    import shutil
    from datetime import datetime
    dest = f"{db_path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copytree(db_path, dest)
    return dest


def migrate_to_v2(client, collection, db_path, cold_docs):
    """把行號定址的冷庫 ID 換成內容定址，並回填 content_hash 與 schema_version。

    關鍵性質是零重算：embedding 只跟文字有關、跟 ID 無關，既有向量原封搬到
    新 ID 即可。實測 910 筆 17.5 秒、0 次推論，相較全庫重建的 6.3 分鐘快 21 倍。

    以「舊文字 → 新 doc」建立對應，因為此時 raw.jsonl 的行號與庫內舊 ID 未必
    仍然對齊（若已有人刪改過行）；用文字比對才不會把向量接到錯的條目上。
    """
    legacy_ids = [i for i in collection.get(include=[])["ids"]
                  if i.startswith(LEGACY_COLD_PREFIX)]
    if not legacy_ids:
        return collection

    backup = _backup_db(db_path)
    if backup:
        print(f"[遷移] 已備份向量庫至 {backup}")

    data = collection.get(ids=legacy_ids, include=["embeddings", "documents", "metadatas"])
    by_text = {d["text"]: d for d in cold_docs}

    new_ids, embs, docs_out, metas, migrated_old = [], [], [], [], []
    for i, old_id in enumerate(data["ids"]):
        target = by_text.get(data["documents"][i])
        if target is None:
            # 來源已刪除的條目：不搬移，留給後續的孤立向量清理處理
            continue
        new_ids.append(target["path"])
        embs.append(data["embeddings"][i])
        docs_out.append(target["text"])
        metas.append(build_metadata(target))
        migrated_old.append(old_id)

    for s in range(0, len(new_ids), 500):
        collection.upsert(ids=new_ids[s:s+500], embeddings=embs[s:s+500],
                          documents=docs_out[s:s+500], metadatas=metas[s:s+500])

    # 先驗證新 ID 全數就位再刪舊的，任何不符就中止並保留原狀
    landed = set(collection.get(ids=new_ids, include=[])["ids"])
    if landed != set(new_ids):
        raise RuntimeError(
            f"[遷移] 新 ID 未全數就位（{len(landed)} != {len(set(new_ids))}），"
            f"已中止且未刪除舊 ID；如需還原請使用備份 {backup}")

    for s in range(0, len(legacy_ids), 500):
        collection.delete(ids=legacy_ids[s:s+500])

    print(f"[遷移] 冷庫 ID 改為內容定址，共 {len(new_ids)} 筆，未重算任何向量")
    return collection
```

- [ ] **Step 5: 執行測試確認通過**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_*.py" -v
```

預期：`Ran 27 tests` 且 `OK`（test_kb_reader 10 + test_indexer 17）

- [ ] **Step 6: 提交**

```powershell
& git -C "d:\GitHub\deep-memory" add skills/chroma-hybrid-search/scripts/kb_reader.py skills/chroma-hybrid-search/scripts/indexer.py skills/chroma-hybrid-search/scripts/test_kb_reader.py skills/chroma-hybrid-search/scripts/test_indexer.py
& git -C "d:\GitHub\deep-memory" commit -m @'
feat(indexer): 冷庫改內容定址 ID 並提供零重算遷移

冷庫 ID 從 cold-notes/raw.jsonl#L{n} 改為 cold-notes#<hash12>，
行號降級為 metadata 的 line 欄位。migrate_to_v2 以文字比對把既有
向量搬到新 ID，先驗證就位再刪舊 ID，遷移前完整備份資料庫目錄。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
'@
```

---

### Task 4: 索引執行日誌與異常警告

**Files:**
- Modify: `skills/chroma-hybrid-search/scripts/indexer.py`
- Modify: `skills/chroma-hybrid-search/scripts/test_indexer.py`

**Interfaces:**
- Consumes: Task 2 的 `IndexPlan`
- Produces:
  - `ANOMALY_RATIO = 0.2`
  - `write_run_log(base_dir, record) -> str`（回傳日誌檔路徑）
  - `format_anomaly_warning(plan, total) -> str | None`

- [ ] **Step 1: 寫失敗測試**

先在 `test_indexer.py` 的 import 區（檔案頂部）補上三個標準庫模組：

```python
import json
import tempfile
from pathlib import Path
```

再於檔案末尾追加兩個測試類別：

```python
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
        self.assertIn("id-missing", warning)

    def test_no_warning_when_collection_is_empty(self):
        # 首次建庫全部都是新條目，不該被當成異常
        plan = self._plan_with(100, {"new": 100, "text-changed": 0, "id-missing": 0})
        self.assertIsNone(indexer.format_anomaly_warning(plan, total=0))
```

- [ ] **Step 2: 執行測試確認失敗**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_indexer.py" -v
```

預期：`AttributeError: module 'indexer' has no attribute 'write_run_log'`

- [ ] **Step 3: 實作日誌與警告**

在 `indexer.py` 頂部 import 區加入 `import json` 與 `from datetime import datetime`，並在檔案末尾加入：

```python
# 單次重算超過庫內既有筆數的此比例即視為異常，主動提示而非默默跑完
ANOMALY_RATIO = 0.2


def write_run_log(base_dir, record):
    """把一次索引執行追加為一行 JSON 到 logs/index-runs.jsonl。

    採 JSONL 而非人類可讀格式，是為了讓「這半年索引變慢過幾次、各是什麼原因」
    能直接用程式彙總。單行約 300 bytes，每日十次執行一年約 1 MB，不需輪替。
    """
    log_dir = os.path.join(str(base_dir), "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "index-runs.jsonl")
    payload = {"ts": datetime.now().isoformat(timespec="seconds"),
               "schema_version": SCHEMA_VERSION}
    payload.update(record)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def format_anomaly_warning(plan, total):
    """重算規模異常時產生警告文字，沒有異常則回傳 None。

    total 為庫內既有筆數：首次建庫（total=0）全是新條目屬正常，不該報警。
    直接把原因分佈印在 console，使用者不必事後翻日誌才知道自己在等什麼。
    """
    if total <= 0 or not plan.embed_ids:
        return None
    ratio = len(plan.embed_ids) / total
    if ratio < ANOMALY_RATIO:
        return None

    reasons = ", ".join(f"{k}={v}" for k, v in plan.reasons.items() if v)
    lines = [
        f"[⚠️ 異常] 本次需重算 {len(plan.embed_ids)} 筆，佔庫內 {total} 筆的 {ratio:.0%}。",
        f"  重算原因分佈：{reasons}",
    ]
    if plan.reasons.get("id-missing"):
        lines.append("  出現 id-missing：通常代表 ID 位移，或執行中的腳本版本與"
                     "建立索引時不同（例如 repo 版與 ~/.claude/skills 安裝副本不一致）。")
        lines.append("  請先確認兩處的 kb_reader.py 一致再重跑。")
    lines.append(f"  樣本：{', '.join(plan.embed_ids[:3])}")
    return "\n".join(lines)
```

- [ ] **Step 4: 執行測試確認通過**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_indexer.py" -v
```

預期：`Ran 22 tests` 且 `OK`

- [ ] **Step 5: 提交**

```powershell
& git -C "d:\GitHub\deep-memory" add skills/chroma-hybrid-search/scripts/indexer.py skills/chroma-hybrid-search/scripts/test_indexer.py
& git -C "d:\GitHub\deep-memory" commit -m @'
feat(indexer): 新增索引執行日誌與重算異常警告

每次執行追加一行到 logs/index-runs.jsonl，記錄耗時與 reasons 分類。
重算比例超過庫內 20% 時於 console 印出警告與原因分佈；出現
id-missing 時直接提示檢查 repo 與安裝副本的腳本版本是否一致。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
'@
```

---

### Task 5: sync() 主流程與 update_db.py 薄殼化

**Files:**
- Modify: `skills/chroma-hybrid-search/scripts/indexer.py`
- Rewrite: `skills/chroma-hybrid-search/scripts/update_db.py`
- Modify: `skills/chroma-hybrid-search/scripts/test_indexer.py`

**Interfaces:**
- Consumes: Task 2–4 的 `detect_schema`、`build_plan`、`migrate_to_v2`、`write_run_log`、`format_anomaly_warning`
- Produces: `sync(base_dir, trigger="update_db", rebuild=False, verbose=True) -> dict`

回傳的 dict 含 `total`、`embedded`、`meta_only`、`skipped`、`stale`、`reasons`、`timing`。

- [ ] **Step 1: 寫失敗測試**

在 `test_indexer.py` 追加：

```python
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
```

同時在 `test_indexer.py` 的 import 區補上這三個標準庫模組：

```python
import contextlib
import io
from unittest import mock
```

- [ ] **Step 2: 執行測試確認失敗**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_indexer.py" -v
```

預期：`AttributeError: module 'indexer' has no attribute 'assert_schema_supported'`（以及 `ensure_fresh` 同樣不存在）

- [ ] **Step 3: 實作 assert_schema_supported 與 sync**

在 `indexer.py` 的 `detect_schema()` 之後加入：

```python
def assert_schema_supported(collection):
    """庫的 schema 版本高於本腳本時中止執行。

    舊腳本寫進新格式的庫會產生無法自動修復的混合狀態——
    寧可明確擋下並要求升級 skill，也不要留下一個壞掉的索引。
    """
    metas = collection.get(include=["metadatas"])["metadatas"]
    versions = {(m or {}).get("schema_version", 1) for m in metas}
    if versions and max(versions) > SCHEMA_VERSION:
        raise RuntimeError(
            f"向量庫 schema 版本為 {max(versions)}，高於本腳本的 {SCHEMA_VERSION}。"
            f"請先升級 chroma-hybrid-search 技能（repo 與 ~/.claude/skills 兩處都要），"
            f"再重新執行。")
```

在 `indexer.py` 末尾加入主流程：

```python
def sync(base_dir, trigger="update_db", rebuild=False, verbose=True):
    """執行一次完整的索引同步，回傳統計結果。

    trigger 記錄呼叫來源（update_db / search-auto），寫進日誌以便區分
    「使用者手動跑」與「搜尋時自動補」兩種情境的耗時分佈。
    rebuild=True 時強制重算全部條目（遷移後想讓向量與新文字格式完全對應時使用）。
    """
    import time
    import chromadb
    from kb_reader import read_knowledge_base, read_cold_notes

    timing = {}
    t0 = time.time()

    base_dir = os.path.abspath(str(base_dir))
    db_path = os.path.join(base_dir, "chroma_hybrid_db")

    t = time.time()
    hot_docs = read_knowledge_base(base_dir)
    cold_docs = read_cold_notes(base_dir)
    docs = hot_docs + cold_docs
    timing["read"] = round(time.time() - t, 2)

    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection("hybrid_docs")
    assert_schema_supported(collection)

    if detect_schema(collection) < SCHEMA_VERSION:
        t = time.time()
        collection = migrate_to_v2(client, collection, db_path, cold_docs)
        timing["migrate"] = round(time.time() - t, 2)

    t = time.time()
    plan = build_plan(collection, docs)
    timing["get"] = round(time.time() - t, 2)

    total_existing = len(collection.get(include=[])["ids"])

    if rebuild:
        # 強制全量：把所有條目都放進待重算清單
        plan.embed_ids = [d["path"] for d in docs]
        plan.embed_docs = [d["text"] for d in docs]
        plan.embed_metas = [build_metadata(d) for d in docs]
        plan.meta_ids, plan.meta_metas = [], []
        plan.skipped = 0

    warning = None if rebuild else format_anomaly_warning(plan, total_existing)
    if warning and verbose:
        print(warning)

    if plan.stale_ids:
        collection.delete(ids=plan.stale_ids)
        if verbose:
            print(f"[清理] 移除 {len(plan.stale_ids)} 筆孤立向量（來源已刪除或改名）")

    timing["model_load"] = 0.0
    timing["embed"] = 0.0
    if plan.embed_ids:
        # 到這裡才載入模型：零變動或 metadata-only 的執行完全不付模型載入成本
        t = time.time()
        from onnx_models import E5OnnxEmbeddingFunction
        ef = E5OnnxEmbeddingFunction()
        timing["model_load"] = round(time.time() - t, 2)

        t = time.time()
        embeddings = ef(plan.embed_docs)
        timing["embed"] = round(time.time() - t, 2)

        collection.upsert(ids=plan.embed_ids, embeddings=embeddings,
                          documents=plan.embed_docs, metadatas=plan.embed_metas)
    if plan.meta_ids:
        collection.update(ids=plan.meta_ids, metadatas=plan.meta_metas)

    timing["total"] = round(time.time() - t0, 2)
    result = {
        "trigger": trigger,
        "total": len(docs),
        "embedded": len(plan.embed_ids),
        "meta_only": len(plan.meta_ids),
        "skipped": plan.skipped,
        "stale": len(plan.stale_ids),
        "reasons": plan.reasons,
        "timing": timing,
    }
    write_run_log(base_dir, result)

    if verbose:
        print(f"[增量] 重算 {result['embedded']} 筆、僅更新 metadata "
              f"{result['meta_only']} 筆、未變跳過 {result['skipped']} 筆"
              f"（耗時 {timing['total']}s）")
    return result


def ensure_fresh(base_dir, verbose=False):
    """供 search.py 於查詢前呼叫的自動補索引。

    索引是為了服務檢索，不該反過來讓檢索失效——任何失敗都只印警告後放行，
    使用者最多少撈到最新幾筆，而不是整個搜尋不能用。
    """
    try:
        return sync(base_dir, trigger="search-auto", verbose=verbose)
    except Exception as exc:
        print(f"[WARN] 自動補索引失敗，改用現有索引繼續查詢：{exc}")
        return None
```

- [ ] **Step 4: 改寫 update_db.py 為薄殼**

把 `update_db.py` 整檔replace為：

```python
# -*- coding: utf-8 -*-
"""
update_db.py
向量索引更新的 CLI 進入點。實際邏輯全在 indexer.py，
好讓 search.py 的自動補索引與這裡走完全相同的一套判斷。
"""
import os
import sys
import argparse

import indexer

# Force UTF-8 stdout encoding on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description="ChromaDB Indexing Script")
    # 優先從環境變數 DEEP_MEMORY_WORKSPACE 取得工作目錄，否則預設為使用者家目錄下的 .deep-memory
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE")
    if not default_ws:
        default_ws = os.path.join(os.path.expanduser("~"), ".deep-memory")

    parser.add_argument("--workspace", type=str, default=default_ws, help="Workspace root path")
    parser.add_argument("--rebuild", action="store_true",
                        help="強制重算全部條目的向量（約需數分鐘），"
                             "僅在需要讓向量與最新文字格式完全對應時使用")
    args = parser.parse_args()

    result = indexer.sync(os.path.abspath(args.workspace),
                          trigger="update_db", rebuild=args.rebuild)
    if result["embedded"] == 0 and result["meta_only"] == 0 and result["stale"] == 0:
        print("[增量] 向量庫已是最新，無需任何寫入。")
    print("Database build/update finished successfully!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 執行測試並實跑驗證**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_*.py" -v
```

預期：`Ran 36 tests` 且 `OK`（test_kb_reader 10 + test_indexer 26）

接著實跑一次真實遷移（**這會修改正式庫，但 `migrate_to_v2` 會先自動備份**）：

```powershell
$env:PYTHONIOENCODING='utf-8'
& "$HOME\.deep-memory\.venv\Scripts\python" "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts\update_db.py"
```

預期輸出包含「已備份向量庫至 ...bak-」與「冷庫 ID 改為內容定址，共 9xx 筆，未重算任何向量」，且**重算筆數為個位數**。若出現數百筆重算，立即停止並檢查 `semantic_hash` 的欄位涵蓋範圍。

再跑第二次，驗證冪等：

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts\update_db.py"
```

預期：「向量庫已是最新，無需任何寫入。」

- [ ] **Step 6: 提交**

```powershell
& git -C "d:\GitHub\deep-memory" add skills/chroma-hybrid-search/scripts/indexer.py skills/chroma-hybrid-search/scripts/update_db.py skills/chroma-hybrid-search/scripts/test_indexer.py
& git -C "d:\GitHub\deep-memory" commit -m @'
feat(indexer): 新增 sync 主流程，update_db.py 改為 CLI 薄殼

sync 串起 schema 守衛、自動遷移、增量比對、惰性載入模型與日誌寫入。
assert_schema_supported 在庫版本高於腳本時中止，避免舊腳本破壞新庫。
update_db.py 新增 --rebuild 供需要向量與新文字格式完全對應時使用。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
'@
```

---

### Task 6: search.py 自動補索引

**Files:**
- Modify: `skills/chroma-hybrid-search/scripts/search.py`

**Interfaces:**
- Consumes: Task 5 的 `indexer.ensure_fresh(base_dir, verbose=False)`
- Produces: `search.py` 新增 `--no-auto-index` 旗標

**現況定位**（`search.py` 於本計畫撰寫時的行號，實作前先確認未偏移）：

- `146`：最後一個參數 `--project` 的定義結束
- `147`：`args = parser.parse_args()`
- `149`：`base_dir = os.path.abspath(args.workspace)`
- `169`：`client = chromadb.PersistentClient(path=db_path)`

自動補索引必須插在 149 與 169 之間——要在 `base_dir` 已知之後，且在外層建立 client **之前**，否則 `sync()` 內部另開的 client 會與外層持有同一份 sqlite 檔而可能相互卡住。

- [ ] **Step 1: 加入 --no-auto-index 參數**

在第 146 行（`--project` 定義結束）之後、第 147 行 `args = parser.parse_args()` 之前插入：

```python
    parser.add_argument("--no-auto-index", action="store_true",
                        help="停用查詢前的自動補索引（預設會先把新寫入的條目補進索引）")
```

- [ ] **Step 2: 在建立 client 之前呼叫 ensure_fresh**

在第 149 行 `base_dir = os.path.abspath(args.workspace)` 之後插入：

```python
    # 查詢前自動把新寫入的條目補進索引，使用者不必手動跑 update_db.py。
    # 這裡的成本幾乎全被本來就要付的 import 與模型載入吸收；
    # ensure_fresh 內部已做例外處理，失敗只會警告不會中斷查詢。
    if not args.no_auto_index:
        import indexer
        indexer.ensure_fresh(base_dir)
```

- [ ] **Step 3: 確認既有測試未被破壞**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_*.py" -v
```

預期：`Ran 36 tests` 且 `OK`（`ensure_fresh` 的 fail-safe 行為已於 Task 5 測過）

- [ ] **Step 4: 實跑驗證自動補索引生效**

先寫一筆新的冷庫條目，**不手動跑 update_db.py**，直接搜尋它：

```powershell
$env:PYTHONIOENCODING='utf-8'
& python "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts\write_cold.py" --topic "自動補索引驗證用臨時條目" --content "這是一筆用來驗證 search.py 自動補索引的臨時條目，關鍵字 zzz-autoindex-probe" --tags "test" --skill "chroma-hybrid-search" --project deep-memory
& "$HOME\.deep-memory\.venv\Scripts\python" "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts\search.py" --query "zzz-autoindex-probe" --limit 3 --min-score 0.35
```

預期：搜尋結果**包含**剛寫入的條目（證明未手動索引也能撈到）。

再驗證 fail-safe——用一個不存在的 workspace 觸發索引失敗，搜尋不得整個掛掉：

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts\search.py" --query "zzz-autoindex-probe" --limit 3 --min-score 0.35 --no-auto-index
```

預期：正常回傳結果（`--no-auto-index` 路徑可用）。

- [ ] **Step 5: 清除驗證用的臨時條目**

```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -c @'
import os, json
p = os.path.join(os.path.expanduser("~"), ".deep-memory", "cold-notes", "raw.jsonl")
lines = [l for l in open(p, encoding="utf-8") if "zzz-autoindex-probe" not in l]
open(p, "w", encoding="utf-8").writelines(lines)
print(f"剩餘 {len(lines)} 行")
'@
& "$HOME\.deep-memory\.venv\Scripts\python" "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts\update_db.py"
```

**這一步同時是驗收標準 3 的實測**：刪掉冷庫中間一行後，預期輸出為「重算 0 筆」且「移除 1 筆孤立向量」。若出現數百筆重算，代表內容定址沒有生效，必須回頭檢查。

- [ ] **Step 6: 提交**

```powershell
& git -C "d:\GitHub\deep-memory" add skills/chroma-hybrid-search/scripts/search.py
& git -C "d:\GitHub\deep-memory" commit -m @'
feat(search): 查詢前自動補索引，日常不需手動跑 update_db.py

ensure_fresh 在建立 client 之前執行，成本被本來就要付的 import 與
模型載入吸收；索引失敗只印警告不中斷查詢。新增 --no-auto-index 可停用。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
'@
```

---

### Task 7: 安裝副本同步與驗證

**Files:**
- Modify: `~/.claude/skills/deep-memory/`、`chroma-hybrid-search/`、`memory-backup/`、`memory-import/`（全量覆寫）

本任務不改 repo 程式碼，只把 repo 的四個技能同步到安裝副本，並驗證同步確實消除了版本落差。

- [ ] **Step 1: 記錄同步前的落差**

```powershell
foreach ($s in 'deep-memory','chroma-hybrid-search','memory-backup','memory-import') {
  $r = "d:\GitHub\deep-memory\skills\$s"; $i = "$HOME\.claude\skills\$s"
  $diff = @()
  foreach ($f in (Get-ChildItem $r -Recurse -File | Where-Object { $_.FullName -notmatch '__pycache__' })) {
    $rel = $f.FullName.Substring($r.Length+1); $ip = Join-Path $i $rel
    if (-not (Test-Path $ip)) { $diff += "缺 $rel"; continue }
    $a = (Get-Content $f.FullName -Raw -Encoding UTF8) -replace "`r`n","`n"
    $b = (Get-Content $ip -Raw -Encoding UTF8) -replace "`r`n","`n"
    if ($a -ne $b) { $diff += "異 $rel" }
  }
  "== $s ==" ; if ($diff) { $diff } else { "一致" }
}
```

- [ ] **Step 2: 執行同步**

以 repo 為來源全量覆寫，排除 `__pycache__`：

```powershell
foreach ($s in 'deep-memory','chroma-hybrid-search','memory-backup','memory-import') {
  $r = "d:\GitHub\deep-memory\skills\$s"; $i = "$HOME\.claude\skills\$s"
  robocopy $r $i /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
  "同步完成：$s"
}
```

> `robocopy /MIR` 會讓目標完全鏡射來源，包含刪除來源已不存在的檔案。`__pycache__` 以 `/XD` 排除。robocopy 的退出碼 0-7 皆為成功，非錯誤。

- [ ] **Step 3: 驗證零差異**

重跑 Step 1 的比對腳本，預期四個技能全部輸出「一致」。

**不可改用二進位比對**（如 `fc /b`）：repo 為 LF、安裝副本經 git 檢出為 CRLF，會產生全檔差異的假警報。

- [ ] **Step 4: 驗證版本落差已消除**

用**安裝副本**的腳本跑一次索引：

```powershell
$env:PYTHONIOENCODING='utf-8'
& "$HOME\.deep-memory\.venv\Scripts\python" "$HOME\.claude\skills\chroma-hybrid-search\scripts\update_db.py"
```

預期：「向量庫已是最新，無需任何寫入。」

**這是本任務的核心驗收**——同步前用安裝副本跑會觸發 918 筆重算，同步後應為 0 筆。若仍出現大量重算，代表同步未完成或 `semantic_hash` 有版本差異。

- [ ] **Step 5: 確認新增檔案已就位**

```powershell
foreach ($f in 'deep-memory\scripts\migrate_memory_type.py','deep-memory\scripts\refine_experience.py','chroma-hybrid-search\scripts\indexer.py') {
  "{0,-55} {1}" -f $f, (Test-Path "$HOME\.claude\skills\$f")
}
```

預期：三個皆為 `True`。

---

### Task 8: 文件更新

**Files:**
- Modify: `skills/chroma-hybrid-search/SKILL.md`
- Modify: `skills/deep-memory/SKILL.md`
- Modify: `skills/deep-memory/resources/cold-store-and-vectorization.md`
- Modify: `docs/rag-technology.md`
- Modify: `C:\Users\kevintsai\.deep-memory\knowledge-base\deep-memory-ops.md`

- [ ] **Step 1: 更新 chroma-hybrid-search/SKILL.md**

在說明 `update_db.py` 的段落補上：

```markdown
### 索引維護

`search.py` 在查詢前會自動把新寫入的條目補進索引，**日常不需手動執行 `update_db.py`**。
索引失敗只會印出警告，不會中斷查詢。以 `--no-auto-index` 可停用此行為。

仍需手動執行 `update_db.py` 的情況：精煉後想立即同步、或想在搜尋前確認索引狀態。

`update_db.py --rebuild` 會強制重算全部條目的向量（約數分鐘），僅在需要讓向量與
最新文字格式完全對應時使用。

**索引 ID 為內容定址**：冷庫條目的向量庫 ID 是 `cold-notes#<語意雜湊>` 而非行號，
因此刪除或插入 `raw.jsonl` 的任一行都不會導致其他條目重算。行號保留在 metadata
的 `line` 欄位供來源引用顯示。

每次索引執行會追加一行到 `~/.deep-memory/logs/index-runs.jsonl`，記錄各階段耗時與
重算原因分類（`new` / `text-changed` / `id-missing`）。單次重算超過庫內 20% 時會
主動印出警告——若原因是 `id-missing`，通常代表 repo 版與 `~/.claude/skills` 安裝
副本的 `kb_reader.py` 版本不一致。
```

- [ ] **Step 2: 更新 deep-memory/SKILL.md**

找到 Step 5.2 第 5 點「Remind user to sync」的區塊，把「必須手動重建索引」改為：

```markdown
5. **Remind user to sync**: After writing, prompt the user to run:
   ```bash
   # Back up to GitHub (preserve a portable JSONL snapshot; --repo defaults to deep-memory-knowledge)
   <PY> skills/memory-backup/scripts/backup.py --workspace "$HOME/.deep-memory"
   ```

   The vector index no longer needs a manual rebuild — `search.py` tops it up
   automatically before each query. Run `update_db.py` explicitly only when you
   want the new entries indexed immediately rather than at next search.
```

- [ ] **Step 3: 更新 cold-store-and-vectorization.md**

把索引重建規則段落改為說明「自動補索引 + 手動時機」，並補上內容定址的說明：

```markdown
## 索引重建規則

`search.py` 於查詢前自動補索引，因此精煉流程的第 7 步（重建後抽查驗證）可直接
用 `search.py` 完成，不必先手動跑 `update_db.py`。

冷庫條目的向量庫 ID 是內容定址（`cold-notes#<語意雜湊>`），雜湊只涵蓋
topic / content / tags / skill / memory_type。因此：

- 精煉時標記 `quality: reviewed` **不會**觸發重算（該欄位不在雜湊範圍內）
- 刪除或插入 `raw.jsonl` 中間任一行**不會**影響其他條目
- 調整條目的顯示格式**不會**觸發全庫重建
```

- [ ] **Step 4: 更新 docs/rag-technology.md**

該文件的 §3.4「重建時機（節錄）」（約 79-88 行）目前寫著「**不建議每次寫入就觸發**」與「未跑 `update_db.py` 前，RAG 查不到這些新條目」——自動補索引後這兩句都不再成立，必須整段替換為：

```markdown
### 3.4 重建時機

`search.py` 於查詢前會自動補索引（`indexer.ensure_fresh()`），因此**日常不需手動執行 `update_db.py`**。索引失敗只印警告、不中斷查詢；以 `--no-auto-index` 可停用。

`update_db.py` 為**增量索引**：只對新增或文本變動的條目重算向量，metadata-only 變動不重算，零變動的執行約 1 秒完成（此時完全不載入模型）。仍需手動執行的情況只剩兩種：

- 精煉後想立即同步，而不是等到下次查詢時才補。
- 想在查詢前先確認索引狀態與耗時。

`update_db.py --rebuild` 強制重算全部條目（1255 筆規模約 6.3 分鐘），僅在需要讓向量與最新文字格式完全對應時使用。

### 3.5 內容定址與索引日誌

冷庫條目的向量庫 ID 為 `cold-notes#<語意雜湊>`（`kb_reader.semantic_hash()`），雜湊只涵蓋 topic / content / tags / skill / memory_type，排除 date / time / project / quality 與模板字串；行號降級為 metadata 的 `line` 欄位。

這個設計消除兩類全庫重算：

- **刪改 `raw.jsonl` 中間任一行**不再讓後續條目位移。舊設計以 `#L{行號}` 為 ID，位移點之後的每一筆都會同時觸發「新 ID 重算」與「舊 ID 孤立」。
- **調整 text 組裝格式**不再讓全庫失效。舊設計的增量比對直接比對完整組裝字串，光是多一行「記憶類型：」就會讓 918 筆冷庫條目全數被判為已變動。

每次索引執行會追加一行到 `logs/index-runs.jsonl`，含各階段耗時與重算原因分類（`new` / `text-changed` / `id-missing`）。單次重算超過庫內 20% 時主動警告；若原因為 `id-missing`，通常代表 repo 版與 `~/.claude/skills` 安裝副本的 `kb_reader.py` 不同版。
```

替換後檢查 §3.2 與 §3.3 是否另有描述冷庫 ID 為行號的段落，一併改為內容定址的說法。文件開頭（第 5-10 行）的「對應實作位置」清單也要加入 `scripts/indexer.py`。

- [ ] **Step 5: 把診斷結論寫進知識庫**

在 `C:\Users\kevintsai\.deep-memory\knowledge-base\deep-memory-ops.md` 末尾追加：

```markdown
## 🔧 索引突然全庫重算：先查 repo 版與安裝副本的 kb_reader 是否同版
**Date:** 2026-08-01
**Context:** 「熱庫寫入偶爾要等好幾分鐘」的實測根因，不是行號 ID 位移（備份行數序列 142→146→440→466→625→664→733→769→919 單調遞增，從未刪行），而是 `~/.claude/skills` 的安裝副本停在 memory_type 之前的版本——它組出的冷庫 text 少一行「記憶類型：」，與 repo 版互相視對方全部 918 筆為已變動。
**Best Practices:**
- 診斷索引效能問題的第一步是確認「使用者實際執行的是哪一份腳本」：repo 的 `skills/` 與 `~/.claude/skills/` 可能不同版，量測錯對象會得到完全錯誤的結論。
- 比對兩處差異必須正規化換行後再比：repo 是 LF、安裝副本是 CRLF，二進位比對（`fc /b`）會全檔報異的假警報。
- 索引 ID 與比對鍵都改為語意內容雜湊後，此類版本差異不再引發雪崩；`logs/index-runs.jsonl` 的 `reasons.id-missing` 即為此症狀的指紋。
- 實測基準（1255 筆規模）：embedding 約 303 ms/筆、全庫重建約 6.3 分鐘、零重算遷移 17.5 秒、`import chromadb` 3.58 秒為固定成本最大單項。
- 三項曾被提案但實測為負向的優化，不要再試：`intra_op_num_threads=18` 比預設慢 19%（超額訂閱）、長度排序分桶 0.89x（熱庫條目普遍超過 512 token，整批 padding 後即 512，無短文件可省）、ONNX 預先圖優化模型另存 12.43s vs 原始 2.04s。
**Source cold store entries:** 2026-08-01 索引內容定址改造
```

- [ ] **Step 6: 提交**

```powershell
& git -C "d:\GitHub\deep-memory" add skills/chroma-hybrid-search/SKILL.md skills/deep-memory/SKILL.md skills/deep-memory/resources/cold-store-and-vectorization.md docs/rag-technology.md
& git -C "d:\GitHub\deep-memory" commit -m @'
docs: 更新索引維護說明為自動補索引與內容定址

SKILL.md 說明 search.py 會自動補索引、--no-auto-index 與 --rebuild
的使用時機，以及 logs/index-runs.jsonl 的判讀方式。知識庫補上
「索引突然全庫重算先查腳本版本」的診斷條目。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
'@
```

- [ ] **Step 7: 同步文件到安裝副本**

文件也在技能目錄內，需重跑 Task 7 Step 2 的同步指令，並以 Step 3 的比對驗證一致。

---

### Task 9: 宣傳網頁 changelog v6

**Files:**
- Modify: `landing-page/index.html`（HTML 模板約 882 行、中文文案約 1013 行、英文文案約 1113 行）

- [ ] **Step 1: 在 HTML 模板加入 v6 一列**

在 `landing-page/index.html` 找到 `<div style="display: flex; flex-direction: column; gap: 12px;">`（約 882 行），在其後、`{{t.changelog_v5_date}}` 那一列**之前**插入：

```html
          <div style="display: flex; gap: 16px; font-size: 13.5px; line-height: 1.6;">
            <span class="mono" style="color: var(--text-muted); flex-shrink: 0; width: 88px;">{{t.changelog_v6_date}}</span>
            <span style="color: var(--text-secondary);">{{t.changelog_v6_desc}}</span>
          </div>
```

- [ ] **Step 2: 加入中文文案**

在中文語系物件中，`changelog_badge: "更新紀錄",` 之後、`changelog_v5_date` 之前插入：

```javascript
    changelog_v6_date: "2026-08-01",
    changelog_v6_desc: "索引改為內容定址：冷庫條目的向量庫 ID 從行號改為語意雜湊，刪改任一筆不再讓後續條目全數重算；增量比對改比 `content_hash` 而非完整組裝文字，調整顯示格式也不再觸發全庫重建。`search.py` 於查詢前自動補索引（失敗不阻擋查詢），日常不需再手動執行 `update_db.py`。每次索引執行寫入 `logs/index-runs.jsonl`，記錄各階段耗時與重算原因分類（new / text-changed / id-missing），重算比例異常時主動警告。既有索引自動遷移，實測 910 筆 17.5 秒且不重算任何向量。",
```

- [ ] **Step 3: 加入英文文案**

在英文語系物件中，`changelog_badge: "Changelog",` 之後、`changelog_v5_date` 之前插入：

```javascript
    changelog_v6_date: "2026-08-01",
    changelog_v6_desc: "Indexing is now content-addressed: cold-store entries are keyed by a semantic hash instead of a line number, so editing or deleting one entry no longer forces every entry after it to be re-embedded. Incremental comparison comes from `content_hash` rather than the full assembled text, so changing display formatting no longer triggers a full rebuild. `search.py` tops up the index before querying (failures never block the search), so `update_db.py` is no longer part of the daily loop. Every indexing run appends to `logs/index-runs.jsonl` with per-stage timings and a reason breakdown (new / text-changed / id-missing), and warns when an unusual share of entries is being re-embedded. Existing indexes migrate automatically — measured at 17.5s for 910 entries with zero re-embedding.",
```

- [ ] **Step 4: 本機驗證雙語渲染**

```powershell
Start-Process powershell -ArgumentList '-NoExit','-Command','cd "d:\GitHub\deep-memory\landing-page"; python -m http.server 8899'
Start-Sleep -Seconds 2
Start-Process "http://localhost:8899/"
```

在瀏覽器確認：更新紀錄區塊最上方為 2026-08-01 一列、切換語言後英文文案正常顯示、頁面無 JavaScript 錯誤。確認後關閉該 PowerShell 視窗。

- [ ] **Step 5: 提交**

```powershell
& git -C "d:\GitHub\deep-memory" add landing-page/index.html
& git -C "d:\GitHub\deep-memory" commit -m @'
docs(landing-page): changelog 新增 2026-08-01 內容定址索引

中英雙語各一列，說明語意雜湊 ID、content_hash 增量比對、
search.py 自動補索引與 index-runs.jsonl 執行日誌。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
'@
```

> **不含發布**：推送到 `gh-pages` 分支屬對外動作，需使用者明確指示後才執行，不在本計畫範圍內。

---

## 完成後驗收

依 spec 第 10 節逐項確認：

- [ ] 同步安裝副本後執行 `update_db.py`，重算筆數為 0（Task 7 Step 4）
- [ ] 遷移 20 秒內完成、0 次 embedding 推論、筆數與向量不變（Task 5 Step 5）
- [ ] 刪除冷庫中間一行後：重算 0 筆、清除孤立向量 1 筆（Task 6 Step 5）
- [ ] `search.py` 未手動索引即可撈到最新冷庫條目（Task 6 Step 4）
- [ ] `logs/index-runs.jsonl` 每次執行皆有完整記錄且 `reasons` 分類正確
- [ ] 宣傳網頁 changelog v6 中英雙語顯示正常（Task 9 Step 4）
