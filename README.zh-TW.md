# Deep‑Memory：AI 自進化知識積累與混合檢索系統

[English](README.md) | **繁體中文**

> 💡 **專案說明**：本專案是由原 **[auto-skill](https://github.com/toolsai/auto-skill)** 專案深度優化與更名演進而來。我們將其重構為極簡且模組化的開源技能包，解耦了「代碼與資料」，並整合了本地 ChromaDB 混合檢索與 BGE-Reranker 重排能力。

![Deep‑Memory Flow](assets/deep-memory-flow.svg)

這個技能是讓你的 AI Agent 不再是「用完即忘」的工具，而是越用越懂你的自進化「第二大腦」。

Deep‑Memory 是一個為 AI Assistant 設計的元技能（Meta‑Skill）。它作為背景運行的知識系統，能在對話過程中自動檢索過往經驗、捕捉最佳實踐，並在任務成功時主動將「成功經驗」寫入你的私人知識庫並建立索引，聰明地減少 Tokens 消耗。你只需要照常提出需求，Deep‑Memory 就會在背景自動運作。

---

## 核心亮點

### 1. 真正的「越用越強」

傳統的 Agent 對話結束即歸零。Deep‑Memory 透過核心循環（Core Loop），在每次對話中自動檢查關鍵字索引，若發現這是過去解決過的問題，會直接調用當時的「最佳解法」或「避坑指南」。

### 2. 跨技能經驗層（Cross‑Skill Memory）

當你呼叫其他特定 Skill（如 Coding、寫作、繪圖）時，Deep‑Memory 會自動檢查技能經驗庫。
例如：當你調用 `remotion-video-gen` 時，它會主動提醒：「上次我們在做這個時，發現設定 FPS 30 會導致音畫不同步，建議改為 60。」

### 3. 主動式經驗捕獲

你不需要手動整理筆記。當 AI 偵測到任務圓滿完成，或你表達滿意時，它會主動詢問：

> 「這次解決了 [問題]，我想把這個經驗記錄下來，下次遇到類似問題可以直接參考，你覺得可以嗎？」

### 4. 模組化技能封裝與 RAG 整合

- **職責分離**：完全移除了私有資料夾與二進位檔案，程式碼獨立打包發布。
- **本機混合檢索**：整合子技能 `chroma-hybrid-search`，提供本地語意 + BM25 混合檢索，並藉由 CPU 運行 BGE-Reranker-base 重排。
- **冷熱分層儲存**：高頻取用的精選知識存於熱庫（`knowledge-base/`、`experience/`），即時但未精煉的對話筆記先落地冷庫（`cold-notes/raw.jsonl`），累積到一定量再精煉升級為熱庫條目。

### 5. 跨裝置可攜與安全備份

整合子技能 `memory-backup`：一鍵將知識庫匯出並推送至你自己的 GitHub 私有 Repo；換新機器時透過 `restore.py` 安全還原（預設僅補齊缺少的檔案、不覆蓋既有內容）。推送前會自動偵測遠端是否領先，避免多裝置互相覆蓋彼此的備份。

---

## 運作邏輯（The Loop）

Deep‑Memory 在每一輪對話中執行嚴謹的 5 步循環：

1. **關鍵詞指紋 (Fingerprinting)**
   從對話中提取核心關鍵詞，生成話題指紋。
2. **話題切換偵測**
   智能判斷用戶是否開啟新話題，決定是否重讀知識庫。
3. **經驗讀取 (Skill Experience)**
   若使用了特定技能，強制檢查是否有過往的「踩坑紀錄」或「成功參數」。
4. **通用知識庫檢索 (Knowledge Base)**
   根據任務類型自動比對索引，載入最佳實踐。
5. **主動記錄 (Write Back)**
   在任務高完成度結束時，執行任務核心提取寫入。

**完整決策流程，拆成三張圖（啟動 → 檢索 → 記錄）：**

![流程圖一：每輪啟動](assets/flow-1-kickoff.zh.png)

![流程圖二：Step 4 知識庫檢索](assets/flow-2-retrieval.zh.png)

![流程圖三：Step 5 記錄流程](assets/flow-3-recording.zh.png)

*這些圖的 Mermaid 原始碼放在 `assets/diagrams/*.mmd`，重新產生指令：`mmdc -i assets/diagrams/flow-1-kickoff.zh.mmd -o assets/flow-1-kickoff.zh.png -b white -s 2 -w 1000`（依圖檔名稱替換）。*

---

## 檔案結構與格式

### 1) 技能安裝包 (GitHub Release Pack)

```text
skills/
├── deep-memory/
│   ├── SKILL.md                 # 主導協議與流程控制
│   ├── scripts/seed.py          # 預載種子知識庫安裝
│   └── resources/                # 記錄格式・冷熱庫規則（延伸文件，依需求載入）
├── chroma-hybrid-search/
│   ├── SKILL.md                 # 混合檢索子技能說明
│   ├── requirements.txt         # 本地 AI 依賴庫宣告
│   └── scripts/
│       ├── search.py            # RAG 檢索與 Rerank
│       ├── update_db.py         # 本地向量資料庫初始化／更新
│       └── write_cold.py        # 冷庫（cold-notes/）即時寫入
├── memory-backup/
│   ├── SKILL.md                 # GitHub 備份／還原子技能說明
│   └── scripts/
│       ├── backup.py            # 匯出並安全推送至 GitHub 私有 Repo
│       ├── restore.py           # 跨裝置從 GitHub 還原知識庫
│       └── export_jsonl.py      # ChromaDB → 可攜式 JSONL 匯出
└── memory-import/
    ├── SKILL.md                 # 外部記憶匯入子技能說明
    └── scripts/import.py        # 匯入 ChatGPT／Claude 本機／舊 auto-skill 資料
```

### 2) 私有資料庫（安裝後在使用者開發專案下建立）

```text
your-project/
├── knowledge-base/              # 熱庫：人工精選、關鍵詞索引
│   ├── _index.json              # 關鍵詞索引
│   └── backend-dev.md           # 您的領域知識手冊
├── experience/                  # 熱庫：技能專屬踩坑經驗
│   ├── _index.json              # 技能索引
│   └── skill-python-code.md     # 特定工具的踩坑經驗
├── cold-notes/
│   └── raw.jsonl                # 冷庫：即寫即用，累積到閾值後精煉升級至熱庫
├── chroma_hybrid_db/            # 本地編譯之 ChromaDB 二進位（熱庫＋冷庫皆會索引）
└── backup/                      # memory-backup 暫存區（獨立 git 倉庫，推送至 GitHub）
```

---

## 如何使用

### 安裝模型（擇一）


| 模型                 | 技能放哪                                                | 指令路徑                                                                | 適用                         |
| ---------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------ |
| **快速安裝（推薦）** | 用`npx skills add` 直接從 GitHub 抓進 `.claude/skills/` | 用`.claude/skills/...`（或工具實際放置的位置）                          | 想最快上手、不想手動複製檔案 |
| **專案內**           | 把`skills/` 複製到專案根目錄                            | 直接用`skills/...`（各 SKILL.md 的範例即如此）                          | 單一專案、想要可攜           |
| **全域**             | 複製到`~/.agents/skills/`（或你的 Agent 技能庫）        | 改成該全域路徑，並對每個腳本加上`--workspace "<你的專案>"` 指定資料目錄 | 多專案共用同一套技能         |

> 所有資料目錄（`knowledge-base/`、`experience/`、`chroma_hybrid_db/`）一律建立在「你的專案」下，與技能放哪無關——腳本透過 `--workspace`（預設為當前目錄）決定讀寫位置。

#### 用 `npx skills add` 快速安裝

[`skills`](https://github.com/vercel-labs/skills) 是一個小型 CLI，可以直接從公開的 GitHub Repo 安裝 Agent Skills，不需要手動 clone。因為本專案的技能放在 `skills/` 目錄下，這個工具會自動探索到它們：

```bash
# 安裝前先預覽有哪些技能
npx skills add kevintsai1202/deep-memory --list

# 全裝、不詢問（等同 --skill '*' --agent '*' -y 的簡寫）
npx skills add kevintsai1202/deep-memory --all

# 或明確指定只裝到 Claude Code，而不是用 --all
npx skills add kevintsai1202/deep-memory --skill '*' -a claude-code

# 或只安裝核心技能
npx skills add kevintsai1202/deep-memory --skill deep-memory -a claude-code

# 加上 -g 可改為安裝到全域技能庫，而不是目前專案
npx skills add kevintsai1202/deep-memory --skill '*' -a claude-code -g
```

> `--all` 會把技能裝進 CLI 認得的**每一個** Agent（Claude Code、Cursor、Codex 等），不只 Claude Code；如果只想裝進 `.claude/skills/`，請用 `--skill '*' -a claude-code`。

這只會放好技能檔案，下方的 Python 初始化步驟每個專案仍須執行一次。

### 初始化步驟

1. 依上表選擇安裝模型，放好 `skills/`（若你是用 `npx skills add` 安裝，這步可跳過）。
2. 初始化虛擬環境、安裝依賴套件、安裝種子知識庫、建立本機向量索引（**不需 activate，直接呼叫 venv 內的 Python**）：

   **Windows (PowerShell)**

   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -r skills/chroma-hybrid-search/requirements.txt
   .venv\Scripts\python skills/deep-memory/scripts/seed.py
   .venv\Scripts\python skills/chroma-hybrid-search/scripts/update_db.py
   ```
   **Linux / macOS**

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r skills/chroma-hybrid-search/requirements.txt
   .venv/bin/python skills/deep-memory/scripts/seed.py
   .venv/bin/python skills/chroma-hybrid-search/scripts/update_db.py
   ```
   `knowledge-base/` 由 `seed.py` 自動建立；`experience/` 與 `cold-notes/` 則在第一次真正寫入時自動建立，不需手動 `mkdir`。

   > 若你是用 `npx skills add` 安裝，請把上面的 `skills/...` 換成該工具實際放置的位置（專案安裝通常是 `.claude/skills/...`；加 `-g` 則是 `~/.claude/skills/...`）。
   >
