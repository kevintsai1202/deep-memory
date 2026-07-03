# ONNX 冷啟動優化說明文件

> 本文件記錄 deep-memory RAG 管線的一次重大效能優化：**推論引擎從 sentence-transformers（PyTorch）改為 onnxruntime 直接推論**，並同步引入**增量索引**與**模型延遲載入**。冷啟動時間從約 21 秒降至 1–7 秒，全新安裝相依體積從約 3 GB 縮至約 200 MB，且檢索結果與門檻**完全不變**。
>
> 對應實作位置：[skills/chroma-hybrid-search/](../skills/chroma-hybrid-search/)
>
> - 輕量推論模組（本次新增）：`scripts/onnx_models.py`
> - 增量索引 + 自動遷移：`scripts/update_db.py`
> - 檢索腳本：`scripts/search.py`
> - 備份匯出：`skills/memory-backup/scripts/export_jsonl.py`
>
> RAG 整體架構請見 [rag-technology.md](rag-technology.md)，本文件只聚焦本次優化。

---

## 1. 優化成果總覽

| 情境 | 優化前 | 優化後 | 改善 |
|---|---|---|---|
| `update_db.py`：零變動（最常見） | 約 21 秒 | **約 1.0 秒** | 21× |
| `update_db.py`：有新條目 | 約 21–23 秒 | **約 2.5 秒** | 8× |
| `search.py`：完整 hybrid-rerank 查詢 | 約 20 秒起 | **約 6.4–7.0 秒** | 3× |
| `export_jsonl.py`：備份匯出 | 十餘秒 | **約 0.9 秒** | 15× |
| 全新安裝相依體積 | 約 3 GB（torch 生態系） | **約 200 MB** | 15× |

三項優化疊加造就上表數字：

1. **增量索引**：只重算「新增或文本變動」的條目，metadata-only 變動零向量成本，未變跳過。
2. **ONNX 直接推論**：甩掉 PyTorch，改用 onnxruntime 載入官方 ONNX 權重。
3. **延遲載入**：`update_db.py` 只在真的有條目要重算時才載入模型；零變動的執行連模型都不碰。

---

## 2. ONNX 是什麼？

### 2.1 一句話定義

**ONNX**（Open Neural Network Exchange，開放神經網路交換格式）是一種 **AI 模型的通用檔案格式**，由微軟與 Facebook 於 2017 年發起。

可以用文件格式類比：

| | 編輯格式 | 交換格式 |
|---|---|---|
| 文件界 | Word 檔（要用 Word 開） | **PDF**（任何閱讀器都能開） |
| 模型界 | PyTorch 權重（要用 PyTorch 跑） | **ONNX**（任何支援的執行引擎都能跑） |

### 2.2 為什麼 ONNX 能更快？

關鍵不在格式本身，而在**執行引擎的定位差異**：

| | PyTorch | ONNX Runtime（onnxruntime） |
|---|---|---|
| 設計定位 | 訓練 + 推論的完整框架 | **純推論引擎** |
| 內含功能 | 自動微分、GPU 管理、動態計算圖…… | 只有「把模型跑起來」需要的東西 |
| 安裝體積 | 約 2–3 GB | 約 50–200 MB |
| `import` 時間 | 數秒（Windows 上更明顯） | 1 秒內 |
| CPU 推論速度 | 基準 | 常見 2–3 倍快（靜態圖優化、量化支援） |

這是深度學習部署的標準模式：**用 PyTorch 訓練，轉 ONNX 部署**。訓練需要的彈性（動態圖、微分）和推論需要的效率是兩回事，分開處理各取所長。轉換過程是把模型的計算流程「凍結」成靜態計算圖——對 e5-small、bge-reranker 這種固定架構的推論用模型毫無損失。

### 2.3 本系統用到的 ONNX 權重從哪來？

**不需要自己轉換。** 兩個模型在 HuggingFace 上的官方 repo 都已附帶 ONNX 權重：

| 模型 | 角色 | ONNX 權重位置 |
|---|---|---|
| `intfloat/multilingual-e5-small` | Embedding（查詢與文件向量化） | repo 內 `onnx/model.onnx`（fp32） |
| `BAAI/bge-reranker-base` | Cross-Encoder 重排 | repo 內 `onnx/model.onnx`（fp32） |

首次執行時自動下載並快取到 `~/.cache/huggingface/`，之後完全離線。因為是 **fp32 完整精度**權重（非量化版），輸出與 PyTorch 版**數值一致**——實測 embedding cosine similarity = 1.0、rerank 分數差 < 1e-6，所以：

- **既有向量索引不需重建**（新舊向量可直接混用比對）
- **`--min-score 0.35` 門檻不需調整**

---

## 3. 一個重要的失敗嘗試：sentence-transformers 的 `backend="onnx"`

sentence-transformers 3.x 起提供 `SentenceTransformer(..., backend="onnx")` 參數，看似一行就能切換。**實測結果反而更慢**：

| 載入方式（暖快取） | e5-small 載入時間 |
|---|---|
| sentence-transformers（torch backend） | 12.7 秒 |
| sentence-transformers（**onnx backend**） | **18.3 秒**（更慢！） |
| onnxruntime 直接載入（本次方案） | **1.9 秒** |

原因：**sentence-transformers 是 torch-first 框架，不管選哪個 backend 都會 `import torch`**。選 ONNX backend 只是在 torch 之上再疊 optimum + onnxruntime 的載入成本——省不到啟動時間，只加速推論本身（而本系統每次只算 1–2 筆向量，推論從來不是瓶頸）。

> **教訓**：「用了 ONNX」≠「甩掉 PyTorch」。要拿到 ONNX 的輕量紅利，必須讓**整條 import 鏈**都不碰 torch。

冷啟動 20 秒的實際組成（優化前）：

| 階段 | 耗時 | 說明 |
|---|---|---|
| `import chromadb` | 約 0.8 秒 | 很輕 |
| `import sentence_transformers`（含 torch） | 約 5.9 秒 | PyTorch 固定 import 稅 |
| 建構 SentenceTransformer 模型物件 | 約 8.5 秒 | tokenizer、config、權重載入與初始化 |
| （search.py 另加）CrossEncoder reranker | 數秒 | 第二個模型 |

權重讀取（HuggingFace 進度條那段）其實不到 1 秒——真正的成本在 torch import 與模型物件建構，這正是 ONNX 直接推論能砍掉的部分。

---

## 4. 最終方案：`onnx_models.py` 輕量推論模組

### 4.1 設計

繞過 sentence-transformers，直接組合三個輕量套件（全部在 1 秒內 import 完）：

```text
huggingface_hub  →  下載／定位 onnx/model.onnx 與 tokenizer.json（優先讀本地快取）
tokenizers       →  Rust 實作的分詞器，載入 tokenizer.json（截斷 512、批次 padding）
onnxruntime      →  InferenceSession 載入 ONNX 權重，CPU 推論
```

模組提供兩個類別，介面刻意對齊原本的用法：

| 類別 | 取代 | 自行實作的部分 |
|---|---|---|
| `E5OnnxEmbeddingFunction` | Chroma 的 `SentenceTransformerEmbeddingFunction` | mean pooling（只平均非 padding 位置）+ L2 normalize，對齊 e5 的 ST 管線（Transformer → Pooling → Normalize） |
| `OnnxReranker` | `sentence_transformers.CrossEncoder` | query-doc 成對編碼 + 對 logit 取 sigmoid（同 CrossEncoder 單標籤預設行為） |

### 4.2 數值一致性驗證

替換推論引擎最大的風險是「分數悄悄變了」。上線前以三類文本（中文短句、英文技術句、超過 512 token 的長文）比對：

| 驗證項目 | 結果 |
|---|---|
| embedding cosine similarity（自製 vs ST） | 1.0000（三筆皆然，含截斷情境） |
| rerank 分數最大差異 | 0.00000003 |

因此索引相容、門檻不變、無需任何資料遷移（collection 設定遷移除外，見第 6 節）。

---

## 5. 增量索引與延遲載入

`update_db.py` 原本每次執行都把全部條目重新向量化。現在改為三段式比對（以既有向量庫內容為基準）：

| 條目狀態 | 處理方式 | 向量成本 |
|---|---|---|
| 新增，或文本有變動 | `upsert`（重算 embedding） | 有 |
| 文本相同、僅 metadata 變動（如精煉後標記 `reviewed`） | `collection.update(metadatas=)` | **零** |
| 完全未變 | 跳過 | 零 |
| 來源已刪除／改名的孤立向量 | 主動刪除（原有邏輯保留） | 零 |

搭配**延遲載入**：embedding 模型在「確定有條目要重算」之後才建構。零變動的執行從頭到尾不碰模型，約 1 秒完成——這讓「跑一下 update_db 確認索引是新的」變成幾乎免費的操作。

---

## 6. Chroma 的隱藏地雷：持久化 embedding function

本次優化揭露了一個不明顯的 ChromaDB（1.5.9）行為，值得記錄：

1. collection 建立時若綁了 embedding function（EF），**EF 的名稱與設定會持久化在 collection 裡**。
2. 之後 `get_or_create_collection` 若帶入不同名稱的 EF，直接報錯（`Embedding function conflict`）。
3. 更隱蔽的是：**即使呼叫 `upsert` 時自帶算好的 `embeddings=`，Chroma 仍會在第一次寫入時實例化持久化的 EF**——舊 collection 綁的 `sentence_transformer` EF 會偷偷 `import torch`（實測讓單筆 upsert 花 10.7 秒）；在沒裝 sentence-transformers 的新最小環境更會直接 `ModuleNotFoundError`。

**解法（官方建議路線）**：不把 EF 綁在 collection 上，向量一律自行計算後以 `embeddings=`／`query_embeddings=` 傳入。對既有 collection 則做一次性遷移：

```text
把資料（含向量）get 出來 → 建立不綁 EF 的新 collection → 分批 upsert
→ 刪除舊 collection → modify(name=) 換回原名
```

`update_db.py` 已內建 `migrate_legacy_collection()`：偵測到持久化設定為 `sentence_transformer` 時自動執行上述遷移（含筆數核對，不符即中止並保留原資料），既有使用者升級後第一次執行即無感完成。

---

## 7. 相依變化與升級指引

### 7.1 requirements.txt（最小化）

```text
chromadb==1.5.9
rank-bm25==0.2.2
onnxruntime==1.27.0
tokenizers==0.22.2
huggingface_hub==1.21.0
numpy==2.4.6
```

移除了 `sentence-transformers`（及其連帶的 torch、transformers 等），全新安裝從約 3 GB 縮到約 200 MB，已用乾淨 venv 實測安裝與端到端檢索。

> **注意**：requirements.txt 必須保持 **ASCII-only**。pip 在 Windows 上以系統地區編碼（繁中為 cp950）讀取該檔，非 ASCII 註解會直接 `UnicodeDecodeError`。

### 7.2 既有使用者升級步驟

1. 更新技能檔案（`onnx_models.py`、`update_db.py`、`search.py`、`export_jsonl.py`、`requirements.txt`）。
2. 執行一次 `update_db.py` —— 自動完成 collection 遷移與增量索引。
3. （可選）刪除 `~/.deep-memory/.venv` 後以新 requirements.txt 重建，可釋放約 3 GB 磁碟空間；不重建也能正常運作，只是舊套件閒置佔空間。

### 7.3 無法再縮短的部分

`search.py` 每次查詢必須載入 e5（算 query 向量）與 bge-reranker（重排），約 6–7 秒是這兩個 fp32 ONNX 模型（合計約 1.5 GB 權重檔）的載入底線。若未來仍嫌慢，候選方向：

- **量化權重**：改用 repo 附帶的 int8 量化版（如 `onnx/model_qint8_avx512_vnni.onnx`），載入與推論更快，但輸出數值會些微偏移，需重新驗證 0.35 門檻。
- **常駐程序**：模型載一次後常駐服務，冷啟動成本只付一次，代價是多管理一個背景程序。

---

## 8. 本次修改檔案清單

| 檔案 | 變更 |
|---|---|
| `skills/chroma-hybrid-search/scripts/onnx_models.py` | **新增**：輕量 ONNX 推論模組 |
| `skills/chroma-hybrid-search/scripts/update_db.py` | 增量索引、延遲載入、自帶向量寫入、`migrate_legacy_collection()` |
| `skills/chroma-hybrid-search/scripts/search.py` | 改用 `OnnxReranker` 與 `query_embeddings=`（query 向量算一次、兩段式檢索共用） |
| `skills/memory-backup/scripts/export_jsonl.py` | 移除不必要的 EF 綁定（純讀取） |
| `skills/chroma-hybrid-search/requirements.txt` | 最小化為 6 個相依、ASCII-only |
| `docs/rag-technology.md` | 依賴與環境章節同步更新 |
| `skills/deep-memory/resources/cold-store-and-vectorization.md` | 增量索引描述、精煉流程第 7 步（重建後抽查驗證） |
