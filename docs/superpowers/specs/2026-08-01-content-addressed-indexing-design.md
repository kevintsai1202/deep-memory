# 索引內容定址化與可觀測性改造 — 設計文件

- **日期**：2026-08-01
- **狀態**：待審核
- **範圍**：`skills/chroma-hybrid-search/`、`skills/deep-memory/`、`~/.claude/skills/` 安裝副本、`landing-page/`
- **起因**：使用者回報「熱庫寫入很慢」，偶發等待達數分鐘

---

## 1. 背景

deep-memory 的向量索引採增量策略（`update_db.py`，commit e291505）：讀回 collection 現況比對，只對新增或變動的條目重算 embedding。設計意圖是「寫一筆只算一筆」，但實際使用中出現兩種慢：

- **日常**：每次執行 5~10 秒
- **偶發**：單次執行數分鐘

本文件記錄根因查證、方案取捨與定案設計。

## 2. 根因分析

### 2.1 實測基準（2026-08-01，本機 18 邏輯核心）

| 項目 | 實測值 |
|---|---|
| 冷庫條目 | 918 筆（平均 616 字元） |
| 熱庫條目 | 337 筆（平均 1023 字元，最大 8482） |
| 向量庫 | 1247 筆 / 27.2 MB |
| embedding 推論 | 約 303 ms/筆 |
| 全庫重建推估 | 約 6.3 分鐘 |
| `import chromadb` | 3.58 s |
| `import onnxruntime + tokenizers` | 0.83 s |
| ONNX 模型載入 | 2.04 s（最佳） |
| `get(include=["documents","metadatas"])` | 0.61 s |
| `get(include=["metadatas"])` | 0.12 s |
| `get(include=[])` | 0.01 s |

### 2.2 三個獨立成因

**成因 A（已實際發生，偶發數分鐘的主因）：安裝副本與 repo 版本不一致**

`~/.claude/skills/chroma-hybrid-search/scripts/kb_reader.py` 停留在 `memory_type` 功能之前的版本，組出的冷庫 text 少一行「記憶類型：」：

```diff
  text = (
      f"主題：{entry.get('topic', '')}\n"
      f"日期：{entry.get('date', '')}\n"
-     f"記憶類型：{memory_type}\n"      # 已安裝版缺少
      f"技能：{entry.get('skill', 'general')}\n"
      f"標籤：{tags_str}\n"
      f"內容：{entry.get('content', '')}"
  )
```

增量比對的判準是 `existing_docs[doc_id] != documents[i]`（`update_db.py:125`），比對的是**完整組裝後的字串**。兩個版本互相視對方的全部 918 筆為「已變動」，於是在兩者間切換執行時，每次都是全庫重算。

四個技能的安裝副本皆落後，且 `migrate_memory_type.py`、`refine_experience.py` 兩支腳本完全缺席。

**成因 B（尚未引爆的結構性隱患）：冷庫使用位置定址 ID**

```python
"path": f"cold-notes/raw.jsonl#L{i+1}"   # kb_reader.py:155
```

行號是位置衍生的識別碼。刪除或插入中間任一行，位移點之後的每一筆都同時觸發「新 ID 需重算」與「舊 ID 成為孤立向量」。

查證備份檔行數序列為 `142 → 146 → 440 → 466 → 625 → 664 → 733 → 769 → 919`，單調遞增，故此路徑至今未被觸發。`write_cold.py` 為純 append、`refine_experience.py` 只就地標記 `quality: reviewed` 而不刪行，兩者都安全。但只要使用者手動清理一筆錯誤記錄，即會引爆。

**成因 C（日常 5~10 秒）：固定成本**

`import chromadb` 3.58 s 為最大單項，且無法在非常駐架構下消除。模型載入已是惰性（零變動執行不載入）。

### 2.3 已排除的假設

| 假設 | 查證結果 |
|---|---|
| 精煉流程刪除已精煉的行導致雪崩 | 不成立。`refine_experience.py:248` 只就地寫回 `quality`，且該欄位不進 text |
| 單筆 888 ms，調 `intra_op_num_threads` 可改善 | 實測 303 ms/筆；`intra_op=18` 比預設**慢 19%**（超額訂閱） |
| 依長度分桶批次可省下短文件的 padding 陪跑 | 實測 0.89x（更慢）。熱庫條目普遍超過 512 token 上限，整批 padding 後 shape 即為 `(60, 512)`，無短文件可省 |
| ORT 預先圖優化模型另存可加速載入 | 實測 12.43 s vs 原始 2.04 s，**反效果**，且產生 448 MB 硬體特定模型 |

## 3. 目標與非目標

### 目標

1. 消除因 ID 位移或 text 格式演進導致的全庫重算
2. 讓使用者不需手動執行 `update_db.py`
3. 任何一次索引變慢，事後可從日誌判定原因
4. 舊版使用者升級後自動完成遷移，無需人工介入
5. repo、安裝副本、知識庫、宣傳網頁四處保持一致

### 非目標

- 常駐 embedding 服務（YAGNI；以每日數次的使用頻率不值得維護 process 生命週期）
- 壓縮 `import chromadb` 的 3.58 s（非常駐架構下無解）
- 改寫既有 388 處 markdown 來源引用（目前正確，動它無收益）
- 更換 embedding 模型或調整 `--min-score 0.35` 門檻

## 4. 架構

現行 `update_db.py` 單檔承擔 CLI 解析、增量比對、遷移、寫入。`search.py` 若要自動補索引將被迫複製這段邏輯，故先抽離：

| 模組 | 職責 | 依賴 |
|---|---|---|
| `kb_reader.py` | 讀取與切段，產出 `{id, text, content_hash, metadata}` | 標準庫 |
| `indexer.py`（新增） | schema 偵測、遷移、增量比對、寫入、日誌 | chromadb + kb_reader |
| `update_db.py` | CLI 薄殼，呼叫 `indexer.sync()` | indexer |
| `search.py` | 查詢前呼叫 `indexer.ensure_fresh()` | indexer |

`kb_reader.py` 維持零第三方依賴，`semantic_hash()` 為純函式，可獨立測試。

## 5. 詳細設計

### 5.1 內容定址 ID

```
冷庫  cold-notes/raw.jsonl#L723  →  cold-notes#a3f19c2b8e04
熱庫  file.md#slug（不變，本即內容衍生）
```

```python
def semantic_hash(entry):
    """計算冷庫條目的語意雜湊，作為向量庫 ID 與增量比對鍵。

    刻意只涵蓋影響檢索語意的欄位，排除模板字串與顯示用欄位——
    未來調整 text 組裝格式或補顯示欄位時不會導致全庫重算。
    """
    payload = json.dumps({
        "topic":       entry.get("topic", ""),
        "content":     entry.get("content", ""),
        "tags":        entry.get("tags", []),
        "skill":       entry.get("skill", "general"),
        "memory_type": infer_memory_type(entry),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
```

**涵蓋**：`topic`、`content`、`tags`、`skill`、`memory_type`
**排除**：`date`、`time`、`project`、`quality`、所有模板字串

行號降級為 metadata 的 `line` 欄位，供人閱讀的來源引用照常產出。

實測 919 筆冷庫條目雜湊碰撞 0 筆。12 位十六進位（48 bit）在萬筆量級的碰撞機率可忽略；若真發生碰撞，兩筆語意欄位完全相同，本就應視為重複條目。

熱庫的 `content_hash` 為 `sha1(entry_text)[:12]`——熱庫 text 即 markdown 原文，無模板組裝，不存在格式演進問題。

### 5.2 比對鍵改為 hash

```python
# 現行：讀回 27 MB 全文逐字比對
existing = collection.get(include=["documents", "metadatas"])   # 0.61 s
if existing_docs[doc_id] != documents[i]: ...

# 改後：只讀 metadata 比 hash
existing = collection.get(include=["metadatas"])                # 0.12 s
if existing_metas[doc_id].get("content_hash") != content_hash: ...
```

省 0.5 s，並使 text 組裝格式與比對鍵解耦。

### 5.3 schema 版本與遷移標準化

`indexer.py` 定義 `SCHEMA_VERSION = 2`，寫入每筆 metadata。啟動時偵測：

| 偵測結果 | 動作 | embedding 成本 |
|---|---|---|
| 空庫 | 直接視為當前版本 | 無 |
| 存在 `cold-notes/raw.jsonl#L` 前綴 ID（v1） | 完整遷移：備份 → 換 ID → 回填 hash 與版本 → 驗筆數 → 刪舊 ID | **零** |
| 有 v2 ID 但 metadata 缺 `content_hash` | 僅回填 metadata，走 `collection.update()` | **零** |
| 版本一致 | 正常增量 | 僅變動條目 |
| metadata 版本高於 `SCHEMA_VERSION` | **中止**並提示升級 skill | 無 |

遷移步驟（`indexer.migrate_to_v2()`），沿用既有 `migrate_legacy_collection()`（`update_db.py:13-44`）的安全模式：

1. 複製 `chroma_hybrid_db` 至 `chroma_hybrid_db.bak-<YYYYMMDD-HHMMSS>`（27 MB，實測 0.06 s）
2. `get(include=["embeddings","documents","metadatas"])`
3. 以新 ID `upsert`，帶原向量、當前格式 text、含 `content_hash` 與 `schema_version` 的 metadata
4. **驗證筆數**，不符則中止並保留原狀
5. 刪除舊 ID

實測 910 筆遷移耗時 17.5 s，全程 0 次 embedding 推論，抽驗向量與原值位元級一致。相較全庫重建的 6.3 分鐘快 21 倍。

遷移可重入：中途失敗後重跑會跳過已完成的部分，因為判準是 ID 前綴而非執行狀態。

### 5.4 search.py 自動補索引

```
search.py 啟動
  → indexer.ensure_fresh()
      ├─ 無待補     → 多付 0.12 s（一次 get metadatas）
      ├─ 有待補     → 補算，成本藏在本就要付的 import 與模型載入中
      └─ 失敗       → 印警告，以現有索引繼續查詢（絕不阻擋搜尋）
```

以 `--no-auto-index` 關閉。使用者不再需要手動執行 `update_db.py`；該指令保留，供精煉後立即同步等場景使用。

失敗必須 fail-safe 的理由：檢索是讀取路徑，索引不新鮮只會少撈到最新幾筆，而讓索引錯誤阻斷整個檢索會使系統完全不可用。

### 5.5 索引執行日誌

每次索引執行追加一行至 `~/.deep-memory/logs/index-runs.jsonl`：

```json
{"ts":"2026-08-01T10:42:11","trigger":"search-auto","schema_version":2,
 "total":1255,"embedded":3,"meta_only":0,"skipped":1252,"stale":0,
 "reasons":{"new":3,"text-changed":0,"id-missing":0},
 "timing":{"read":0.08,"get":0.12,"model_load":2.04,"embed":0.91,"write":0.15,"total":3.30}}
```

`reasons` 直接回答「這次為什麼慢」：

| 分類 | 意義 |
|---|---|
| `new` | ID 不存在於庫中且來源為新條目——正常 |
| `text-changed` | ID 存在但 `content_hash` 不同——內容確實被改過 |
| `id-missing` | ID 不存在且同時產生等量孤立向量——**位移或版本不一致的指紋** |

**異常防呆**：單次重算超過總量 20% 時，於 console 印出警告，列出 `reasons` 分佈與前若干筆可疑 ID，並提示檢查 skill 版本一致性。使用者不必事後翻日誌才知道發生什麼事。

日誌不做輪替：單行約 300 bytes，每日十次執行一年約 1 MB，未達需要處理的量級。

## 6. 已知取捨

**遷移時 documents 更新為當前格式，embedding 仍為舊格式所算**

遷移的 `upsert` 會把 text 一併更新為當前 `kb_reader` 的組裝格式，但沿用既有向量。兩者因此短暫不完全對應——差異僅為「記憶類型：knowledge」一行，對 e5 向量的影響遠小於 0.35 檢索門檻的裕度，且 BM25 使用 documents 欄位，更新後反而與來源一致。

若要求完全對應，提供 `update_db.py --rebuild` 強制全庫重算（約 6.3 分鐘）。預設不執行。

**`memory_type` 納入語意雜湊**

該欄位可被 `search.py --memory-type` 篩選，屬檢索語意的一部分，故納入。代價是「補回 `memory_type` 的遷移」仍會觸發重算——但這正是 `migrate_memory_type.py` 的既定行為，且為一次性。

## 7. 交付範圍

### 7.1 核心改造

- `kb_reader.py`：新增 `semantic_hash()`、`content_hash` 欄位，冷庫 ID 改內容定址，行號降級為 `line` metadata
- `indexer.py`（新檔）：schema 偵測、遷移、增量比對、寫入、日誌
- `update_db.py`：改為 CLI 薄殼，新增 `--rebuild`
- `search.py`：查詢前呼叫 `ensure_fresh()`，新增 `--no-auto-index`

### 7.2 安裝副本同步

repo → `~/.claude/skills/` 全量同步四個技能（`deep-memory`、`chroma-hybrid-search`、`memory-backup`、`memory-import`），含缺漏的 `migrate_memory_type.py`、`refine_experience.py`。

同步方式：以檔案複製覆寫，來源為 repo，排除 `__pycache__`。同步後以「正規化換行後逐檔比對」驗證零差異——不可用二進位比對，因 repo 為 LF、安裝副本為 CRLF，會產生全檔差異的假警報。

驗證：同步後執行一次 `update_db.py`，遷移完成後的增量重算筆數應為 0（無新條目時）或等於實際新增條目數。若出現數百筆，代表仍有版本未對齊。

### 7.3 文件更新

- `skills/chroma-hybrid-search/SKILL.md`：自動補索引行為、`--no-auto-index`、`--rebuild`
- `skills/deep-memory/SKILL.md`：移除「寫入後須手動執行 `update_db.py`」的提示
- `skills/deep-memory/resources/cold-store-and-vectorization.md`：更新索引重建規則
- `docs/rag-technology.md`：更新增量索引說明
- 知識庫 `deep-memory-ops.md`：記錄安裝副本落後導致全庫重算的根因與判別法

### 7.4 宣傳網頁

`landing-page/index.html` 的 changelog 現有 v1~v5 雙語結構（zh 位於 1013-1023、en 位於 1113-1123）。新增 v6 一列插於 v5 之前，中英各一份，說明內容定址 ID、自動補索引與索引執行日誌。

**部署範圍**：本次交付僅修改 repo 內的 `landing-page/index.html`。實際發布到 GitHub Pages（`.worktrees/gh-pages` worktree → `gh-pages` 分支）屬對外動作，需使用者明確指示後才執行，不隨本計畫自動進行。

## 8. 測試策略

依 TDD，測試先行。

| 對象 | 測試 |
|---|---|
| `semantic_hash()` | 同內容不同行號 → 同 hash；僅改 `date`/`time`/`project`/`quality` → hash 不變；改 `content`/`topic`/`tags`/`skill`/`memory_type` → hash 改變 |
| schema 偵測 | 空庫 / v1 庫 / v2 庫 / 版本過新 四種輸入各自回傳正確決策 |
| 遷移 | 於資料庫複本執行：筆數不變、向量位元級一致、舊 ID 全數清除、可重入 |
| 增量比對 | 臨時 collection 驗證 `new` / `text-changed` / `id-missing` 三條路徑分類正確 |
| `ensure_fresh()` fail-safe | 注入索引失敗，驗證搜尋仍回傳結果且印出警告 |
| 日誌 | 驗證欄位完整、`reasons` 分類正確、超過 20% 觸發警告 |

## 9. 風險與回滾

| 風險 | 緩解 |
|---|---|
| 遷移中斷造成索引損壞 | 遷移前複製整個 `chroma_hybrid_db`；筆數驗證未過即中止並保留原狀 |
| 新舊腳本並存再度引發全庫重算 | schema 版本偵測；腳本版本低於庫版本時直接中止並提示升級 |
| `ensure_fresh()` 拖慢搜尋 | 無待補時僅多 0.12 s；有待補時成本本就必須付；失敗不阻擋查詢 |
| 安裝副本同步遺漏檔案 | 同步後以正規化換行的逐檔比對驗證，並跑一次 `update_db.py` 確認增量為個位數 |

回滾：還原 `chroma_hybrid_db.bak-*` 目錄並將四個技能副本切回前一版即可，資料無損。

## 10. 驗收標準

1. 同步安裝副本後執行 `update_db.py`，遷移完成後的重算筆數等於實際新增條目數（無新增時為 0）
2. 於 v1 格式的資料庫複本執行遷移，20 秒內完成且 0 次 embedding 推論，筆數與向量值皆不變
3. 刪除冷庫中間任一行後執行索引：重算 0 筆、清除孤立向量 1 筆（現行為位移點之後全部重算，最壞 900+ 筆）
4. `search.py` 在未手動執行 `update_db.py` 的情況下能撈到最新寫入的冷庫條目
5. `logs/index-runs.jsonl` 每次執行皆有一行完整記錄，`reasons` 分類正確
6. 宣傳網頁 changelog v6 中英雙語顯示正常
