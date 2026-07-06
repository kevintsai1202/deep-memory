# 設計文件：deep-memory 記憶儀表板（memory-dashboard）

- 日期：2026-07-06
- 技能：`skills/deep-memory`
- 狀態：待實作

## 1. 目標

在 deep-memory 技能中新增一個功能：把使用者累積的**記憶資料**視覺化成一份
**單檔、自帶資料、可離線開啟的互動 HTML 儀表板**。

範圍僅限 deep-memory 自身的記憶（knowledge-base / cold-notes / experience）；
**不含** codebase-memory 的程式碼知識圖譜。

### 設計主軸：AI 環境無關

把「重活」放在確定性的 Python，AI 只負責「時機與引導」。因此：

- 產生器為**純 Python 標準函式庫**腳本，與 `seed.py`、`write_cold.py` 同一類，
  **不需 venv、不需 pip install、不吃 chromadb/onnxruntime**。
- 產出為**單檔 HTML**，CSS/JS/資料全部內嵌，**零 CDN、零外部相依、免開 server**。
- 不使用任何 IDE/AI 專屬能力（不用 Artifact、不用 MCP、不靠模型即時生圖）。
  換 Claude Code / Cursor / Codex / Antigravity 都只是「執行同一行指令」。

## 2. 資料來源（皆讀全域 `~/.deep-memory`）

| 檔案 | 內容 | 供給的維度 |
|---|---|---|
| `knowledge-base/_index.json` | 25 分類，每個 `{id, file, title, keywords[]}` | 關鍵字↔分類關聯、分類規模 |
| `cold-notes/raw.jsonl` | 136 筆，每筆 `{date, time, topic, content, tags[], skill, project, quality}` | 時間趨勢、標籤熱度、專案分布、品質佔比 |
| `experience/_index.json` | 技能經驗分類（同 index 結構） | 併入關聯網作為經驗節點（次要） |

- 路徑解析沿用技能慣例：預設 `~/.deep-memory`，可用 `--workspace` 覆寫。
- 檔案缺漏時**優雅降級**：該面板顯示「無資料」，不讓整份儀表板失敗。

## 3. 面板（6 張）

**A 區：關聯**
1. 🕸️ **關鍵字 ↔ 分類 關聯網絡圖** — force-directed；節點＝分類與關鍵字，
   邊＝分類含有該關鍵字；支援縮放、拖曳、hover 高亮鄰接。

**B 區：統計**
2. 📊 各分類筆數 / 知識庫規模（長條）— 以每分類 keywords 數 / 檔案大小衡量。
3. 📈 cold-notes 時間趨勢（依 `date` 彙總的面積圖）。
4. 🏷️ 標籤熱度 Top N（長條；彙總 `tags[]`，N 預設 20）。
5. 📁 專案分布（甜甜圈；彙總 `project`）。
6. 🔄 品質佔比 raw vs reviewed（甜甜圈；彙總 `quality`，代表冷→熱精煉率）。

## 4. 架構與模組邊界

```
scripts/viz.py  (純 stdlib：json / math / html / argparse / pathlib / collections / datetime)
├─ load_data(workspace)        讀三個來源檔，回傳結構化 dict；缺檔則該區為空
├─ compute_layout(nodes,edges) 用 math 算 force-directed 靜態座標（固定亂數種子→可重現）
├─ aggregate_stats(coldnotes)  彙總時間/標籤/專案/品質
├─ render_html(data) -> str    把資料 + 版面座標序列化成 JSON 內嵌，組出單檔 HTML
└─ main()                      argparse：--workspace / --output / --top-tags；寫檔並印出路徑
```

- **版面在 Python 端算好座標內嵌**：HTML 端只繪製與互動，瀏覽器不跑物理迴圈 → 穩定、可重現、好維護。
- **圖表全手寫 SVG + vanilla JS**，不內嵌 D3。資料量小（25 分類、136 筆）足夠。
- 決定性：force-directed 用**固定亂數種子**，同資料每次產出座標一致（利於版控與比對）。

### 可重現性與離線

- 無網路存取、無外部字型/CDN；深淺色以 `prefers-color-scheme` 自適應。
- 純 stdlib，任何有 Python 3 的環境可跑。

## 5. 使用者介面（CLI）

```bash
# 預設讀 ~/.deep-memory，輸出到 ~/.deep-memory/memory-dashboard.html
python skills/deep-memory/scripts/viz.py

# 自訂
python skills/deep-memory/scripts/viz.py --workspace <dir> --output <file.html> --top-tags 30
```

腳本結束時印出產出檔絕對路徑，方便使用者/AI 直接開啟。

## 6. 技能掛載（SKILL.md）

在 `skills/deep-memory/SKILL.md` 新增一小節「記憶儀表板 / Memory Dashboard」：

- **觸發詞**：「產生記憶儀表板」「畫記憶圖表」「memory dashboard」「視覺化我的記憶」。
- **指令**：上述 `viz.py` 指令（歸在 stdlib 腳本，明確標示不需 `<PY>` venv）。
- **產出**：`memory-dashboard.html` 位置與「用瀏覽器開啟」說明。
- 用自然語言描述，不寫任何 IDE 專屬 API，維持跨環境。

## 7. 錯誤處理

- 來源檔不存在 / 空 / JSON 壞：該面板標「無資料」，其餘照常產出；stderr 印出警告。
- `raw.jsonl` 個別行解析失敗：略過該行並計數，不中斷。
- 無任何資料：仍產出一份含「尚無記憶資料」提示的 HTML。

## 8. 測試

- **煙霧測試**：對現有 `~/.deep-memory` 執行，確認產出 HTML 且檔案 > 0、可於瀏覽器開啟。
- **降級測試**：指向空目錄 / 缺檔的 `--workspace`，確認不崩潰且面板顯示無資料。
- **決定性測試**：同輸入連跑兩次，內嵌的版面座標 JSON 一致。
- **手動視覺驗證**：瀏覽器開啟，6 面板皆顯示、網絡圖可縮放/hover、深淺色正常。

## 9. 明確不做（YAGNI）

- 不做 codebase-memory 程式碼圖譜（本次範圍外）。
- 不內嵌 D3 或任何第三方 JS 函式庫。
- 不做即時更新 / server / 自動排程；每次手動重跑產生最新快照。
- 不動既有 core loop、cold/hot 流程、備份流程。

## 10. 交付物

1. `skills/deep-memory/scripts/viz.py`（中文函式級註解）。
2. `skills/deep-memory/SKILL.md` 新增「記憶儀表板」小節。
3. （可選）`~/.deep-memory/memory-dashboard.html` 為執行產物，不進版控。
