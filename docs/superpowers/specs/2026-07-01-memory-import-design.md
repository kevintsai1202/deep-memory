# memory-import：從外部記憶系統匯入資料

**日期**：2026-07-01
**狀態**：已核准設計，待寫實作計畫

## 背景與動機

deep-memory 目前只能吃兩種資料來源：

- `seed.py` 複製技能內建的種子知識庫
- `memory-backup/scripts/restore.py` 從自己專屬的 GitHub 備份 repo 還原

沒有任何路徑可以把「別的記憶系統」的資料搬進來。使用者實際手上會用到的來源有三個：

1. **ChatGPT 官方記憶匯出**——帳號設定裡匯出的個人化記憶資料
2. **Claude Code 本機 auto memory 檔案**——即 `~/.claude/projects/*/memory/` 底下 `MEMORY.md` + 個別 `.md` frontmatter 的格式
3. **auto-skill 舊格式**——deep-memory 前身專案留下的 `knowledge-base/_index.json` + `experience/_index.json` 資料

Mem0、MemGPT/Letta 目前沒有實際檔案要匯入，但架構要留擴充空間，之後加一個解析器就能接上。

## 架構

新增獨立子技能 `skills/memory-import/`，跟 `chroma-hybrid-search`、`memory-backup` 同層並列：

```text
skills/memory-import/
├── SKILL.md
└── scripts/
    └── import.py          # 純標準庫，不需要 .venv，第一輪對話即可用
```

單一入口 `import.py --source {chatgpt|claude-local|autoskill} --input <path> [--workspace <path>] [--dry-run] [--force]`。內部依 `--source` 分派到對應的解析函式（adapter），每個 adapter 把來源資料正規化後，交給共用的寫入邏輯。之後要加 Mem0／Letta，只需新增一個解析函式並在 `--source` 的合法值裡註冊，不動其他 adapter 或共用邏輯。

**為什麼獨立成新技能而不是塞進 `memory-backup` 或 `deep-memory`**：`memory-backup` 的心智模型是「自己資料的跨裝置備份/還原」，跟「從外部系統搬資料進來」是不同性質的操作，混在一起會讓技能說明變模糊。`deep-memory` 的 SKILL.md 是每輪對話都會載入的核心迴圈文件，不適合塞進一個偶爾才用一次的搬遷功能。獨立技能維持現有「一個技能一個關注點」的架構風格。

## 三個來源的資料流

### ChatGPT 官方記憶匯出 → 冷庫（`cold-notes/raw.jsonl`）

匯出格式目前無法確定官方會提供什麼結構，因此解析器設計成**寬容偵測**：依序嘗試幾種常見形狀——

- 純字串陣列
- 帶 `content` / `text` / `memory` 欄位的物件陣列
- 外層包一層 `{"memories": [...]}`

若都對不上，**整批報錯**（不是部分成功——這代表解析器完全不認得這個結構），並印出偵測到的實際 JSON 結構（頂層型態、範例 key），同時提示退路：使用者可以直接手動呼叫既有的

```bash
skills/chroma-hybrid-search/scripts/write_cold.py --topic "..." --content "..." --tags "chatgpt-memory" --skill memory-import
```

一條條寫入冷庫，不受解析器限制。

若格式辨識成功但個別項目內容為空或明顯無效，**跳過該筆並印警告**，其餘項目照常匯入（不中止整批）。

每筆寫入冷庫的內容格式：

- `topic`：原始文字的前 60 字元（超出截斷，不足則原樣使用）
- `content`：原始文字
- `tags`：`["chatgpt-memory", "imported", "import-id:<sha1(text)>"]`
- `skill`：`"memory-import"`
- `quality`：`"raw"`

`import-id:<hash>` 標記用於去重——寫入前先掃描既有 `raw.jsonl` 的 `tags`，若已存在相同 hash 就跳過，重跑同一份匯出檔不會產生重複記錄。

**理由**：ChatGPT 匯出的內容通常是零散、未分類的事實陳述，沒有既有的分類資訊，適合先進冷庫，之後照現有的冷→熱精煉流程（`resources/cold-store-and-vectorization.md`）自然篩選出有價值的條目升級到熱庫。

### Claude Code 本機 auto memory 檔案 → 熱庫（新分類檔）

Frontmatter 格式已知且固定：

```markdown
---
name: {{slug}}
description: {{summary}}
metadata:
  type: {{user, feedback, project, reference}}
---
{{body}}
```

Adapter 掃描 `--input` 指定目錄下所有 `*.md`，**跳過 `MEMORY.md`**（它只是索引，不是記憶內容本身）。每個檔案轉成一筆 `## 🔧` 條目，全部寫進單一新分類檔 `knowledge-base/imported-claude-memory.md`，並在條目內嵌入原始 `type`（作為條目內文的一部分，方便閱讀時分辨原始分類）。同步更新 `knowledge-base/_index.json`，新增一筆分類（id：`imported-claude-memory`，keywords 從各條目的 `name`/`description` 簡單斷詞聚合而來）。

每筆條目內嵌一個隱藏標記：

```markdown
<!-- imported-from: claude-local:<name> -->
```

寫入前先讀取目標分類檔既有內容，若已存在相同標記就跳過該檔（去重），不會重複匯入同一份記憶。

單個檔案缺少必要欄位（`name` 或 `description`）時，印警告並跳過該檔，其他檔案繼續處理，不中止整批。

### auto-skill 舊格式 → 熱庫（直接安全合併）

這個格式跟 deep-memory 現在的熱庫幾乎一致（`knowledge-base/_index.json` + `knowledge-base/*.md`、`experience/_index.json` + `experience/skill-*.md`），因此直接沿用 `seed.py` / `restore.py` 已有的**安全合併**邏輯：

- 讀取來源路徑（`--input`）下的 `knowledge-base/_index.json`、`experience/_index.json`
- 對每個分類／技能經驗，若 id 在本機索引中**不存在** → 複製對應 `.md` 檔、在本機索引新增一筆
- id **已存在** → 預設跳過；加 `--force` 才覆蓋本機版本
- 若來源路徑下 `knowledge-base/` 或 `experience/` 兩者之一缺 `_index.json`，跳過那一半並印警告，另一半照常匯入

不需要額外的去重機制——id 是否已存在本身就是判斷依據，跟 `seed.py` 的行為一致，降低使用者需要記住的規則種類。

## 通用機制

**`--dry-run`**：三個來源都支援。只列出「會匯入哪些項目、會寫進哪個檔案／哪個分類」，不實際寫檔。因為這是會修改本機知識庫檔案的操作，先預覽一次再決定是否套用，尤其對格式不確定的 ChatGPT 來源更重要。

**輸入路徑不存在或不可讀**：直接報錯、非 0 狀態碼結束，不做任何寫入——這跟「格式讀到但內容有瑕疵」的部分容錯不同，是連讀取都做不到，沒有部分成功的餘地。

**完成後提醒**：不論寫進冷庫或熱庫，執行完都印出提醒：

```bash
<PY> skills/chroma-hybrid-search/scripts/update_db.py
```

讓新內容可以被語意搜尋找到；熱庫寫入的情況下，額外提示可考慮執行 `memory-backup/scripts/backup.py` 備份。

## deep-memory 的整合點（Step 0.5 被動提示）

修改 `skills/deep-memory/SKILL.md` 的「0.5 Self-Bootstrapping」：在偵測到全新安裝（`knowledge-base/_index.json` 不存在）時，除了現有的 seed.py 安裝提示，額外加一句被動提示，說明使用者若持有 ChatGPT／Claude 本機記憶／舊 auto-skill 的資料，可以用 `memory-import` 匯入，附上三個來源各自的指令範例。

**明確排除**：不做任何主動掃描或偵測（例如自動檢查 `~/.claude/projects/*/memory/` 是否存在）。掃描使用者機器上其他專案的目錄本身就是一個需要使用者明確同意的動作，即使只是「有沒有存在」的偵測；且加在 `deep-memory` 這個每輪對話都會載入的核心 SKILL.md 裡會增加維護負擔。使用者需自己知道匯出檔／目錄位置並手動下指令。

## 明確排除的範圍（Out of Scope）

- Mem0、MemGPT/Letta 的實際解析器——架構預留擴充點，但本次不實作，因為目前沒有實際檔案可以驗證格式假設
- 主動偵測／自動掃描使用者機器上的記憶檔案位置
- 任何自動化排程匯入（例如定期同步）——目前只支援使用者手動觸發的一次性匯入
- 自動化測試套件——本專案其他腳本（`seed.py`、`write_cold.py`、`backup.py`、`restore.py`）都沒有自動化測試，本次沿用相同慣例，改用手動建立 fixture 檔案驗證（見下方「驗證方式」）

## 驗證方式

沒有既有的自動化測試框架，延續本專案慣例，以手動 fixture 驗證：

1. 建立三份最小 fixture：一份 ChatGPT 匯出格式的假 JSON、一個含 1–2 個 `.md` 檔案的假 `memory/` 目錄、一個含最小 `knowledge-base/_index.json` + 一個分類檔的假 auto-skill 專案
2. 針對每個來源先以 `--dry-run` 執行，確認預覽清單正確
3. 再不帶 `--dry-run` 實際執行，檢查目標檔案（`cold-notes/raw.jsonl` 或 `knowledge-base/imported-claude-memory.md` 或合併後的 `knowledge-base/_index.json`）內容正確
4. 對同一份 fixture 重跑一次，確認去重機制生效（不重複寫入）
5. 針對錯誤情境（格式不認得、路徑不存在、單筆內容缺欄位）個別驗證錯誤訊息與容錯行為符合上述設計
