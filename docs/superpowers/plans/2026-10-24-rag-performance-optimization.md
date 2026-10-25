# Deep-Memory 檢索與索引效能優化計畫（7,800+ 條目規模實測改造）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 將 `deep-memory` 在 ~7,800 筆向量資料規模下的單次 RAG 檢索時間從 **36.8 秒降至約 12～15 秒（提速 2.5 倍以上）**，消除已精煉冷庫筆記（4,502 筆 `reviewed`）對向量庫與 BM25 的雙重污染，並為超過 50 KB 的巨型經驗檔（最高 433 KB）補齊 Context Window 防爆保護。

**Architecture:**
1. **`indexer.py` 單次快照化**：將 `sync()` 內部原本分散在 `assert_schema_supported`、`detect_schema`、`build_plan` 與筆數統計中的 **5 次 ChromaDB `collection.get()` 合併為 1 次快照讀取**，並將已讀取的 `docs` 隨 `sync()` / `ensure_fresh()` 回傳給 `search.py` 複用，消除雙重硬碟 I/O 與雜湊計算。
2. **`kb_reader.py` 冷庫 `reviewed` 過濾**：`read_cold_notes()` 新增 `include_reviewed=False` 預設參數，排除已升格進熱庫的 `quality == "reviewed"` 歷史冷庫筆記（佔冷庫 95.2%，共 4,502 筆），使檢索語料縮小 57%、消除熱冷庫重複命中。
3. **`search.py` & `onnx_models.py` 重排加速**：將 `OnnxReranker` 實例化移出 `run_search()` 避免兩段式檢索（專案優先 → 全庫 fallback）重複載入 1.1 GB 模型；同時在重排前對候選文本做字元上限截斷（`--rerank-max-chars 600`）與依長度排序分批推論，消除長文將整個 Batch pad 到 512 tokens 造成的 CPU 自注意力平方級減速。
4. **`SKILL.md` 經驗庫載入保護**：在 Step 3（Cross-Skill Experience Lookup）加入單檔 `≥ 50 KB` 自動改走 `search.py --skill <skill-id> --memory-type experience` 的保護規則，對齊 Step 4 Path 2 的知識庫防爆設計。

**Tech Stack:** Python 3.11、chromadb 1.5.9、onnxruntime 1.27.0、rank-bm25 0.2.2、標準庫 `unittest`（**不使用 pytest**）

---

## 一、背景與實測數據基準（Baseline Profiling）

### 1.1 目前 `~/.deep-memory` 真實資料規模（2026-10-24 實測）

| 資料層 | 檔案／條目數 | 總容量 | 備註 |
|---|---|---|---|
| **熱庫 (`knowledge-base/*.md`)** | 102 個分類檔 | 2,347.2 KB (~2.35 MB) | 最大單檔 `284.6 KB`；共 **11 個檔 ≥ 50 KB** |
| **經驗庫 (`experience/*.md`)** | 145 個技能檔 | 2,755.2 KB (~2.76 MB) | 最大單檔 `433.9 KB`；共 **14 個檔 ≥ 50 KB** |
| **熱庫切段總計 (Hot Chunks)** | **3,107 個 `## 🔧` 條目** | 5.10 MB | 條目級切段，平均每條目約 1.6 KB |
| **冷庫 (`cold-notes/raw.jsonl`)** | **4,729 行 JSON** | 5,999.8 KB (~6.00 MB) | 其中 **`reviewed`: 4,502 筆 (95.2%)**、**`raw`: 227 筆 (4.8%)** |
| **ChromaDB 向量庫總計** | **7,836 筆向量文件** | 141.36 MB | 較 `onnx-optimization.md` 撰寫時的 1,265 筆**成長 6.2 倍** |

### 1.2 為什麼目前規模「絕對需要 RAG」？
1. **分類數與單檔大小遠超閾值**：`knowledge-base` 已有 102 個分類（門檻為 25 個），且熱庫與經驗庫共有 **25 個檔案超過 50 KB**（例如 `skill-superpowers-subagent-driven-development.md` 達 168 KB、`skill-superpowers-test-driven-development.md` 達 156 KB）。若無 RAG 條目級切段（Entry-Level Chunking），單次對話直接載入整檔會消耗 4 萬～10 萬 Tokens，瞬間稀釋 Context Window。
2. **跨專案與語意容錯**：3,107 個熱庫條目與 227 筆待精煉冷庫筆記無法僅靠 `_index.json`（目前已達 154 KB）的靜態關鍵字涵蓋，必須依賴「BM25 精確詞彙 + E5 語意向量 + BGE Cross-Encoder 重排」過濾出 Top-3 條目（僅約 2 KB）。

### 1.3 實測瓶頸拆解與優化目標對照表（Warm Cache，0 筆新資料）

| 階段 | 優化前實測耗時 (7,836 筆) | 根因分析 | 優化後目標耗時 |
|---|---|---|---|
| **① `ensure_fresh()` 索引檢查** | **7.21 秒** | `indexer.sync()` 內連續呼叫 **5 次 `collection.get()`**（其中 3 次全量拉取 7,836 筆 `metadatas`，各花 ~2.0 秒） | **~1.2 秒** |
| **② `search.py` 重複讀檔** | **1.37 秒** | `ensure_fresh()` 剛讀完 247 個 MD + 4,729 行 JSONL，`search.py` 第 166 行又重讀並重算一次 SHA1 | **0.00 秒**（直接複用） |
| **③ BM25 分詞與建索引** | **3.63 秒** | 對全部 7,836 筆（含 4,502 筆已 `reviewed` 歷史冷庫）即時執行正規表示式分詞與 `BM25Okapi` 建構 | **~1.3 秒**（排除 `reviewed` 冷庫後剩 ~3,334 筆） |
| **④ E5 Embedding 查詢** | **4.35 秒** | 載入 `multilingual-e5-small` ONNX (~4.32s) + 單句推論 (~0.03s) | **~4.0 秒** |
| **⑤ BGE Reranker 重排** | **20.14 秒** | 載入模型 (~7.09s，若專案輪無命中退回全庫輪還會**重複載入第二次**) + 10 筆全文未截斷推論 (**13.05s**，因單篇長文把整個 batch pad 到 512 tokens) | **~10.0 秒**（載入 1 次 ~7.0s + 截斷後推論 ~3.0s） |
| **⏱️ 單次完整查詢總計** | **36.70 秒** | （若發生兩輪 Rerank 載入則高達 **43.8 秒**） | **~16.5 秒**（`--mode hybrid` 則僅 **~6.5 秒**） |

---

## 二、Global Constraints（全域實作規範）

- **測試框架一律用標準庫 `unittest`**，不得引入 `pytest` 或任何新第三方套件。
- **`kb_reader.py` 必須維持零第三方依賴**（僅使用 Python 標準函式庫）。
- **註解規範**：所有新增／修改的函式必須包含**繁體中文函式級別註解（Docstring）**，重要變數與邏輯分支亦須加上繁體中文註解。
- **單一任務原則**：僅針對本計畫列出的效能與 Context 保護問題修改，不做無關的重構或過度開發。
- **向下相容與安全防呆**：
  - 過濾已 `reviewed` 冷庫條目導致向量庫大量清理舊冷庫向量時，**不得誤觸 `ANOMALY_RATIO`（20% 重算警告）**（因為刪除 `stale_ids` 的向量成本是 0，且 `build_plan` 的 `shifted = min(plan.reasons["new"], len(plan.stale_ids))` 只在同時有大量 `new` 時才會計入 `id-missing`）。
  - 所有腳本在 Windows PowerShell 7+ 環境下測試，Python 執行路徑為 `& "$HOME\.deep-memory\.venv\Scripts\python"`。

**共用測試指令**：

```powershell
# 執行 chroma-hybrid-search 全部單元測試
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_*.py" -v
```

---

## 三、File Structure（預計修改與新增檔案清單）

| 檔案路徑 | 職責與修改內容 | 動作 |
|---|---|---|
| `skills/chroma-hybrid-search/scripts/kb_reader.py` | `read_cold_notes()` 新增 `include_reviewed=False` 參數，預設跳過 `quality == "reviewed"` 的冷庫筆記 | 修改 |
| `skills/chroma-hybrid-search/scripts/indexer.py` | 合併 `collection.get()` 為單次讀取；`sync()` 與 `ensure_fresh()` 支援傳入／回傳 `docs` 避免重複讀檔 | 修改 |
| `skills/chroma-hybrid-search/scripts/search.py` | 複用 `ensure_fresh()` 的 `docs`；將 `OnnxReranker` 提至 `main()` 延遲載入一次；重排文本截斷 `--rerank-max-chars` | 修改 |
| `skills/chroma-hybrid-search/scripts/onnx_models.py` | `OnnxReranker.predict()` 改為依長度排序後分小批次（micro-batch）推論，避免單一長文把整批短文 pad 到 512 | 修改 |
| `skills/chroma-hybrid-search/scripts/test_kb_reader.py` | 新增 `read_cold_notes` 對 `reviewed` 條目過濾與 `include_reviewed=True` 的單元測試 | 修改 |
| `skills/chroma-hybrid-search/scripts/test_indexer.py` | 新增 `sync()` 單次 `collection.get` 呼叫次數驗證與回傳 `docs` 的單元測試 | 修改 |
| `skills/chroma-hybrid-search/scripts/test_search_perf.py` | 測試 Reranker 單例複用、長短文混合排序一致性與文本截斷行為 | **新建** |
| `skills/deep-memory/SKILL.md` | 更新 Step 3 跨技能經驗庫 `≥ 50 KB` 自動改走 RAG 條目檢索的規則 | 修改 |
| `docs/rag-technology.md` | 同步更新冷庫 `reviewed` 過濾規則與重排截斷說明 | 修改 |

---

## 四、Implementation Tasks（分階段實作與測試任務）

### Task 1: `kb_reader.py` 預設過濾已精煉（`quality == "reviewed"`）的冷庫筆記

**Files:**
- Modify: `skills/chroma-hybrid-search/scripts/kb_reader.py`
- Test: `skills/chroma-hybrid-search/scripts/test_kb_reader.py`

**設計說明：**
- 冷庫（`cold-notes/raw.jsonl`）的設計定位是「每回合無確認即時寫入的原始緩衝區」，經 `refine_prepare.py` + `refine_apply.py` 或 `refine_experience.py` 精煉升格至熱庫（`knowledge-base/*.md`、`experience/*.md`）後，條目會被標上 `"quality": "reviewed"`。
- 目前您的 `raw.jsonl` 有 4,729 筆，其中 4,502 筆已是 `"reviewed"`。繼續將這 4,502 筆放入向量庫與每次 `search.py` 的即時 BM25 語料中，不但讓每次搜尋多花 ~2.3 秒做無謂的分詞，更會讓未整理的原始對話與精煉後的熱庫條目搶佔 Top-K 名額。
- 修改 `read_cold_notes(base_dir, include_reviewed=False)`：
  - 預設 `include_reviewed=False`：當 `entry.get("quality") == "reviewed"` 時直接 `continue` 跳過（注意：**行號 `i + 1` 仍以檔案真實行號計算**，不受跳過影響，確保 `#L<行號>` 永遠對準 `raw.jsonl` 的實體行）。
  - 當 `include_reviewed=True` 時：保留載入全部冷庫條目的能力（供未來若有全歷史稽核需求時呼叫）。

- [x] **Step 1: 在 `test_kb_reader.py` 撰寫失敗測試**

在 `skills/chroma-hybrid-search/scripts/test_kb_reader.py` 的 `TestDocsCarryContentHash` 類別中新增測試方法：

```python
    def test_read_cold_notes_skips_reviewed_by_default_and_preserves_line_numbers(self):
        """預設應略過 quality=='reviewed' 的冷庫筆記，且未精煉筆記的 line 必須維持原始檔案行號"""
        ws = self._workspace()
        lines = [
            json.dumps({"date": "2026-08-01", "topic": "已精煉一", "content": "c1", "quality": "reviewed"}, ensure_ascii=False),
            json.dumps({"date": "2026-08-02", "topic": "待精煉二", "content": "c2", "quality": "raw"}, ensure_ascii=False),
            json.dumps({"date": "2026-08-03", "topic": "無標記三", "content": "c3"}, ensure_ascii=False),
        ]
        (ws / "cold-notes" / "raw.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 預設只讀出第 2、3 行，且行號必須是 2 與 3（不能因為第 1 行被跳過就變成 1 與 2）
        docs_default = kb_reader.read_cold_notes(ws)
        self.assertEqual(len(docs_default), 2)
        self.assertEqual([d["line"] for d in docs_default], [2, 3])

        # 明確指定 include_reviewed=True 時應讀出全部 3 行
        docs_all = kb_reader.read_cold_notes(ws, include_reviewed=True)
        self.assertEqual(len(docs_all), 3)
        self.assertEqual([d["line"] for d in docs_all], [1, 2, 3])
```

- [x] **Step 2: 執行測試確認失敗**

Run:
```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_kb_reader.py" -v
```
Expected: `FAIL: test_read_cold_notes_skips_reviewed_by_default_and_preserves_line_numbers`（目前讀出 3 筆而非 2 筆）。

- [x] **Step 3: 修改 `kb_reader.py` 的 `read_cold_notes` 實作**

修改 `skills/chroma-hybrid-search/scripts/kb_reader.py` 第 159–175 行：

```python
def read_cold_notes(base_dir, include_reviewed=False):
    """讀取冷庫（cold-notes/raw.jsonl）中的條目並轉換為可索引格式。

    預設（include_reviewed=False）只讀取尚未精煉（quality != 'reviewed'）的冷庫條目，
    避免已升格至熱庫的歷史筆記重複佔用向量庫與每次查詢的 BM25 分詞時間；
    行號（line）維持 raw.jsonl 的真實物理行號，不因跳過 reviewed 行而偏移。
    """
    jsonl_path = os.path.join(base_dir, "cold-notes", "raw.jsonl")
    if not os.path.exists(jsonl_path):
        return []

    docs = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue
                # 已精煉升格至熱庫的冷庫筆記預設不再重複索引
                if not include_reviewed and entry.get("quality") == "reviewed":
                    continue
                tags_str = ", ".join(entry.get("tags", []))
```

- [x] **Step 4: 執行測試確認通過**

Run:
```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_kb_reader.py" -v
```
Expected: All tests `OK`.

---

### Task 2: `indexer.py` 消除重複的 `collection.get()` 並回傳 `docs` 供檢索複用

**Files:**
- Modify: `skills/chroma-hybrid-search/scripts/indexer.py`
- Test: `skills/chroma-hybrid-search/scripts/test_indexer.py`

**設計說明：**
1. **消除重複的 ChromaDB SQLite 讀取**：
   在目前 7,836 筆規模下，每次 `collection.get(include=["metadatas"])` 需要從 SQLite 解析 7,836 份 JSON metadata，耗時約 **1.7～2.2 秒**。
   原本 `sync()` 依序呼叫：
   - `assert_schema_supported(collection)` → 內部 `collection.get(include=["metadatas"])` (第 1 次)
   - `detect_schema(collection)` → 內部 `collection.get(include=[])` + `collection.get(include=["metadatas"])` (第 2、3 次)
   - `build_plan(collection, docs)` → 內部 `collection.get(include=["metadatas"])` (第 4 次)
   - `len(collection.get(include=[])["ids"])` (第 5 次)
   改造方式：讓 `detect_schema(collection, existing=None)`、`assert_schema_supported(collection, existing=None)`、`build_plan(collection, docs, existing=None)` 皆支援選填的預取快照 `existing = {"ids": [...], "metadatas": [...]}`。
   - 若呼叫端未傳入 `existing`（例如既有單元測試直接呼叫 `indexer.detect_schema(col)`），函式內部才自己呼叫 `collection.get(include=["metadatas"])`，**100% 維持既有單元測試介面相容**。
   - 在 `sync()` 中，一開始只執行一次 `existing = collection.get(include=["metadatas"])`，傳給三個函式共用（若觸發了 `migrate_to_v2` 遷移，遷移完成後才重新取一次 `existing`），且 `total_existing = len(existing["ids"])`，完全省去第 2～5 次 SQLite 查詢！
2. **消除 `search.py` 的第二次硬碟讀檔**：
   - `sync(..., return_docs=False)` 與 `ensure_fresh(base_dir, verbose=False, return_docs=False)`：當 `return_docs=True` 時，回傳 `(result, docs)`（若 `ensure_fresh` 發生例外失敗，則回傳 `(None, None)`，由 `search.py` fallback 自行讀取）。

- [x] **Step 1: 在 `test_indexer.py` 撰寫失敗測試**

在 `skills/chroma-hybrid-search/scripts/test_indexer.py` 新增測試類別 `TestSyncSingleGetOptimization`：

```python
class TestSyncSingleGetOptimization(unittest.TestCase):
    """驗證 sync() 在正常（已遷移至最新 schema）狀態下只呼叫一次 collection.get，並支援回傳 docs"""

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
        orig_get = chromadb.api.models.Collection.Collection.get
        call_count = {"n": 0}

        def counted_get(self_col, *args, **kwargs):
            call_count["n"] += 1
            return orig_get(self_col, *args, **kwargs)

        with mock.patch.object(chromadb.api.models.Collection.Collection, "get", counted_get):
            result, docs = indexer.ensure_fresh(workspace, verbose=False, return_docs=True)

        self.assertEqual(call_count["n"], 1, f"預期只呼叫 1 次 collection.get，實際呼叫了 {call_count['n']} 次")
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["path"], hot_doc["path"])
```

- [x] **Step 2: 執行測試確認失敗**

Run:
```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_indexer.py" -v
```
Expected: `FAIL` or `TypeError: ensure_fresh() got an unexpected keyword argument 'return_docs'`（且原本會呼叫 5 次 `collection.get`）。

- [x] **Step 3: 修改 `indexer.py` 實作單次快照與 `return_docs`**

修改 `skills/chroma-hybrid-search/scripts/indexer.py`：
1. `detect_schema(collection, existing=None)`：
   ```python
   def detect_schema(collection, existing=None):
       """判斷向量庫的 schema 版本，決定要不要遷移。

       支援傳入預先取得的 existing = collection.get(include=["metadatas"]) 快照，
       避免 sync() 流程中重複讀取整個 SQLite metadata 表。
       """
       if existing is None:
           existing = collection.get(include=["metadatas"])
       ids = existing["ids"]
       if not ids:
           return SCHEMA_VERSION
       if any(i.startswith(LEGACY_COLD_PREFIX) for i in ids):
           return 1
       metas = existing["metadatas"]
       versions = {(m or {}).get("schema_version", 1) for m in metas}
       return min(versions) if versions else SCHEMA_VERSION
   ```
2. `assert_schema_supported(collection, existing=None)`：
   ```python
   def assert_schema_supported(collection, existing=None):
       """庫的 schema 版本高於本腳本時中止執行。"""
       if existing is None:
           existing = collection.get(include=["metadatas"])
       metas = existing["metadatas"]
       versions = {(m or {}).get("schema_version", 1) for m in metas}
       if versions and max(versions) > SCHEMA_VERSION:
           raise RuntimeError(
               f"向量庫 schema 版本為 {max(versions)}，高於本腳本的 {SCHEMA_VERSION}。"
               f"請先升級 chroma-hybrid-search 技能（repo 與 ~/.claude/skills 兩處都要），"
               f"再重新執行。")
   ```
3. `build_plan(collection, docs, existing=None)`：
   ```python
   def build_plan(collection, docs, existing=None):
       """比對來源文件與向量庫現況，產出增量計畫。"""
       if existing is None:
           existing = collection.get(include=["metadatas"])
       existing_metas = dict(zip(existing["ids"], existing["metadatas"]))
   ```
4. 在 `sync(base_dir, trigger="update_db", rebuild=False, verbose=True, return_docs=False)` 中：
   ```python
       t = time.time()
       existing = collection.get(include=["metadatas"])
       assert_schema_supported(collection, existing=existing)

       backfilled = 0
       if detect_schema(collection, existing=existing) < SCHEMA_VERSION:
           t_mig = time.time()
           collection = migrate_to_v2(collection, db_path, cold_docs)
           backfilled = backfill_hot_entries(collection)
           timing["migrate"] = round(time.time() - t_mig, 2)
           # 遷移改變了庫內 ID 與 metadata，重新拉取一次快照
           t = time.time()
           existing = collection.get(include=["metadatas"])

       plan = build_plan(collection, docs, existing=existing)
       timing["get"] = round(time.time() - t, 2)

       total_existing = len(existing["ids"])
   ```
   並在 `sync()` 結尾：
   ```python
       return (result, docs) if return_docs else result
   ```
5. 在 `ensure_fresh(base_dir, verbose=False, return_docs=False)` 中：
   ```python
   def ensure_fresh(base_dir, verbose=False, return_docs=False):
       """供 search.py 於查詢前呼叫的自動補索引。

       當 return_docs=True 時回傳 (result, docs)，讓 search.py 直接複用已讀取的語料，
       省去第二次全庫檔案讀取與切段；失敗時回傳 (None, None) 或 None。
       """
       try:
           return sync(base_dir, trigger="search-auto", verbose=verbose, return_docs=return_docs)
       except Exception as exc:
           print(f"[WARN] 自動補索引失敗，改用現有索引繼續查詢：{exc}")
           return (None, None) if return_docs else None
   ```

- [x] **Step 4: 執行測試確認通過**

Run:
```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_indexer.py" -v
```
Expected: All tests in `test_indexer.py` pass (`OK`).

---

### Task 3: `onnx_models.py` 與 `search.py` 重排（Reranker）推論與載入優化

**Files:**
- Modify: `skills/chroma-hybrid-search/scripts/onnx_models.py`
- Modify: `skills/chroma-hybrid-search/scripts/search.py`
- Create: `skills/chroma-hybrid-search/scripts/test_search_perf.py`

**設計說明：**
1. **為什麼目前 `OnnxReranker.predict(pairs)` 跑 10 筆要 13.05 秒？**
   - `OnnxReranker.__init__` 呼叫了 `self.tok.enable_padding(pad_id=1, pad_token="<pad>")`。當 `encode_batch` 一次處理 10 個 `[query, doc]` pair 時，只要其中有 1 筆 `doc` 是長條目（達 512 tokens），其餘 9 筆短條目（例如只有 80 tokens 的冷庫筆記或短經驗）也會全部被 pad 成 `512` tokens！
   - Transformer 自注意力層在 CPU 上的計算量與序列長度的平方 $O(B \times L^2)$ 成正比，導致 10 筆全部以 512 長度做矩陣運算。
   - 此外，像 `refine_prepare.py` 已經實證：重排時對文件取前 N 字（例如 600～800 字元，涵蓋 `## 🔧 標題`、`Context`、核心 `Best Practices` 與 `keywords`），排名前段幾乎不變，但能大幅縮短序列長度。
2. **兩項改造**：
   - **改造 A（`onnx_models.py` 長度分組／逐筆無 Padding 推論或 Micro-batch）**：
     在 `OnnxReranker.predict(pairs, batch_size=4)` 中，先依編碼後的 token 長度排序，或以小批次／逐筆執行（在 CPU 上 `batch_size=1` 或依長度排序後的 `batch_size=2` 完全沒有 padding 浪費！我們將在 Step 1 實測驗證 `batch_size=1` 無 padding vs `batch_size=10` 全 padding 的精確耗時與數值一致性）。
   - **改造 B（`search.py` 單例化 `OnnxReranker` + `--rerank-max-chars` 截斷 + 複用 `ensure_fresh` 的 `docs`）**：
     - 將 `reranker` 改為延遲初始化的單例（或在 `main()` 中建立、透過參數傳入 `run_search`），避免「專案輪（Project Pass）有候選但分數皆 `< 0.35` → 退回全庫輪（Fallback Pass）」時**把 1.1 GB 的 BGE Reranker 模型載入兩次（白白多花 7 秒）**。
     - 新增 `--rerank-max-chars` 參數（預設 `800` 字元，傳 `0` 代表不截斷），重排送入 `reranker.predict` 時取 `doc_map[path]["text"][:args.rerank_max_chars]`，而最終回傳給 Agent 的 `_make_hit()` **仍然保留完整未截斷的 `doc_map[path]["text"]`**。
     - `search.py` 呼叫 `_, all_docs = indexer.ensure_fresh(base_dir, return_docs=True)`，當 `all_docs is None`（例如傳了 `--no-auto-index` 或索引失敗）時才呼叫 `read_knowledge_base(base_dir) + read_cold_notes(base_dir)`。

- [x] **Step 1: 建立 `test_search_perf.py` 撰寫測試**

建立 `skills/chroma-hybrid-search/scripts/test_search_perf.py`：

```python
# -*- coding: utf-8 -*-
"""search.py 與 OnnxReranker 的效能與行為單元測試（標準庫 unittest）"""
import argparse
import unittest
from unittest import mock

import search


class TestSearchRerankerReuseAndTruncation(unittest.TestCase):
    """驗證兩段式檢索不會重複實例化 OnnxReranker，且重排截斷不影響回傳全文"""

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
        """當專案輪重排分數皆低於 min_score 而退回全庫輪時，OnnxReranker 只能被建立一次"""
        fake_col = mock.MagicMock()
        fake_col.query.return_value = {"ids": [["doc1"]]}

        long_text = "前段關鍵重點。" * 50  # 350 字元，超過 rerank_max_chars=100
        docs = [{"path": "doc1", "text": long_text, "project": "my-proj"}]

        init_count = {"n": 0}
        seen_pairs = []

        class FakeReranker:
            def __init__(self):
                init_count["n"] += 1

            def predict(self, pairs):
                seen_pairs.extend(pairs)
                # 第一輪回傳 0.1（低於 0.35 觸發第二輪），第二輪回傳 0.9
                return [0.1] if len(seen_pairs) == 1 else [0.9]

        args = self._args()
        with mock.patch.object(search, "OnnxReranker", FakeReranker):
            reranker_holder = {}
            # 第一輪（模擬專案輪）
            res1 = search.run_search(docs, fake_col, args, query_embedding=[[0.1]], reranker_holder=reranker_holder)
            self.assertEqual(res1, [])
            # 第二輪（模擬全庫 fallback 輪）
            res2 = search.run_search(docs, fake_col, args, query_embedding=[[0.1]], reranker_holder=reranker_holder)

        self.assertEqual(init_count["n"], 1, "OnnxReranker 不應在兩輪搜尋中被重複載入")
        # 送進 reranker 的文字必須被截斷至 rerank_max_chars (100)
        self.assertEqual(len(seen_pairs[0][1]), 100)
        # 但最終回傳給呼叫端的 text 必須是完整原文（350 字元）
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0]["text"], long_text)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: 執行測試確認失敗**

Run:
```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_search_perf.py" -v
```
Expected: `FAIL` / `TypeError: run_search() got an unexpected keyword argument 'reranker_holder'`.

- [x] **Step 3: 修改 `onnx_models.py` 與 `search.py`**

1. 修改 `skills/chroma-hybrid-search/scripts/onnx_models.py` 的 `OnnxReranker`：
   - 移除 `__init__` 中的固定整批 padding，改在 `predict()` 中逐筆（或依長度排序分小批）推論，避免 1 篇 512-token 長文把同批 9 篇 60-token 短文全部 pad 到 512 tokens：
   ```python
   class OnnxReranker:
       """bge-reranker-base 的 cross-encoder 重排器，介面對齊 CrossEncoder.predict"""

       def __init__(self):
           self.sess, self.tok = _load("BAAI/bge-reranker-base")
           # 不開全域 batch padding：CPU 上對不同長度的候選逐筆推論，
           # 可避免單一 512-token 長文將整批短文 pad 到最大長度而拖慢 2~3 倍
           self.tok.no_padding()

       def predict(self, pairs):
           """輸入 [query, doc] 列表，回傳 sigmoid 後的相關性分數（同 CrossEncoder 預設行為）"""
           if not pairs:
               return np.array([], dtype=np.float64)
           encs = self.tok.encode_batch([(q, d) for q, d in pairs])
           logits = []
           for enc in encs:
               ids = np.array([enc.ids], dtype=np.int64)
               mask = np.array([enc.attention_mask], dtype=np.int64)
               out = _run(self.sess, ids, mask).reshape(-1)
               logits.append(float(out[0]))
           logits_arr = np.array(logits, dtype=np.float64)
           return 1.0 / (1.0 + np.exp(-logits_arr))
   ```
2. 修改 `skills/chroma-hybrid-search/scripts/search.py`：
   - `run_search(docs, collection, args, query_embedding=None, extra_where=None, reranker_holder=None)`：
     - 透過 `reranker_holder` 字典（例如 `{"instance": None}`）延遲載入並快取 `OnnxReranker()`，確保同一行程內最多只載入一次。
     - 組裝 `pairs` 時，依 `max_chars = getattr(args, "rerank_max_chars", 800)` 截斷送入重排器的文本（`text[:max_chars] if max_chars and max_chars > 0 else text`），而 `_make_hit(doc_map[path], ...)` 仍回傳完整原文。
   - `main()`：
     - 新增 `--rerank-max-chars` 參數（預設 `800`）。
     - 透過 `_, all_docs = indexer.ensure_fresh(base_dir, return_docs=True)` 直接取得 `all_docs`，不再重複執行第二次 `read_knowledge_base(base_dir) + read_cold_notes(base_dir)`。

- [x] **Step 4: 執行全部單元測試驗證通過**

Run:
```powershell
& "$HOME\.deep-memory\.venv\Scripts\python" -m unittest discover -s "d:\GitHub\deep-memory\skills\chroma-hybrid-search\scripts" -p "test_*.py" -v
```
Expected: All tests pass (`OK`).

---

### Task 4: 更新 `SKILL.md` 與 `docs/rag-technology.md`（補齊巨型經驗檔 > 50 KB 的 RAG 保護）

**Files:**
- Modify: `skills/deep-memory/SKILL.md`
- Modify: `docs/rag-technology.md`

**設計說明：**
- 在 `skills/deep-memory/SKILL.md` 的 **Step 3: Cross-Skill Experience Lookup** 中，目前規定只要觸發非 deep-memory 技能，就直接讀取 `experience/skill-[skill-id].md` 全檔。
- 由於您的 `experience/` 目錄中已有 14 個檔案超過 50 KB（最大達 433 KB），必須在 Step 3 補上與 Step 4 相同的 **50 KB 門檻保護**：
  - 若 `experience/skill-[skill-id].md` 檔案大小 **< 50 KB**：維持直接讀取整檔。
  - 若 `experience/skill-[skill-id].md` 檔案大小 **≥ 50 KB**：禁止直接讀整檔稀釋 Context Window，改執行：
    ```bash
    <PY> skills/chroma-hybrid-search/scripts/search.py --query "core question or task of this turn" --skill [skill-id] --memory-type experience --limit 3 --min-score 0.35
    ```
    只將最相關的前 3 筆 `## 🔧` 經驗條目載入 Context。

- [x] **Step 1: 修改 `skills/deep-memory/SKILL.md` Step 3 與 `docs/rag-technology.md`**
- [x] **Step 2: 執行真實環境端到端基準實測（End-to-End Benchmark）**

Run:
```powershell
# 1. 先執行一次 update_db.py 讓向量庫清掉已 reviewed 的 4,502 筆舊冷庫向量（零重算，僅 delete stale_ids）
& "$HOME\.deep-memory\.venv\Scripts\python" skills/chroma-hybrid-search/scripts/update_db.py

# 2. 實測優化後的 search.py 端到端查詢耗時
Measure-Command {
    & "$HOME\.deep-memory\.venv\Scripts\python" skills/chroma-hybrid-search/scripts/search.py --query "RAG 效能優化與索引機制" --limit 3 --min-score 0.35
}
```
Expected:
- 向量庫總筆數從 `7,836` 筆精簡為約 `3,334` 筆（3,107 筆熱庫 + 227 筆待精煉 `raw` 冷庫）。
- `ensure_fresh` + `search.py` 總耗時由原本的 **36.8 秒** 降至 **12～16 秒** 內。

---

## 五、實作結果（2026-10-26）

已合併至 main（`3e404f1`、`16f584f`、`6d1580e`，merge `69b4bbf`）。

| 項目 | 計畫目標 | 實測 |
|---|---|---|
| 向量庫筆數 | 約 3,334 | 3,351（刪 4,502 筆、零重算、未觸發異常警告） |
| `ensure_fresh`（零變動） | 約 1.2 秒 | 1.3～2.0 秒 |
| 單次查詢（索引最新） | 12～16 秒 | 本機約 29～40 秒；開啟 Jev 約 20～22 秒 |

- **基準不同**：實作當天同一台機器的基準是 57.6 秒（計畫寫 36.8 秒），且背景 CPU 約 65%、時脈 1.2 GHz，絕對秒數偏高；未達 12～16 秒的主因是本機重排推論在此負載下 10 筆要 10～17 秒。
- **與計畫不同之處**：
  - v1 遷移改以 `include_reviewed=True` 的全量冷庫配對（否則精煉過的舊庫配對率跌破五成而中止）；stale 刪除分批 500。
  - 截斷預設採 800 字（計畫架構段寫 600）；400 字實測 60 筆中 18 筆跨 0.35 門檻，不採用。
  - 追加：重排分數快取（鍵含重排器）、E5 以 `get_shared_e5()` 在補索引與查詢間共用、`refine_prepare` 共用 E5 與重排設定。
  - 追加：可選的 TypeSafe Jev 雲端重排（`jev_reranker.py`、`config.json` 開關、門檻 0.6、失敗退回本機）；26 查詢 149 筆盲標 P@1 0.77 → 0.85、P@3 0.58 → 0.67。
- **評估後不採用**：reranker int8 量化（分數最大偏移 0.28）、ORT 優化圖快取與背景載入（0～1.5 秒）、調整執行緒數（預設最快）、常駐服務（約 2.3 GB，查詢中位間隔 41 分鐘）。
