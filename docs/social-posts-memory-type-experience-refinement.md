# Deep-Memory 社群貼文資料：memory_type 與經驗熱庫修正

## 核心訊息

Deep-Memory 之前其實有記錄很多任務過程，但大多停在 `cold-notes/raw.jsonl`，沒有被整理到 `experience/skill-*.md`，所以跨技能經驗層看起來很薄。這次更新補上兩段流程：

- `memory_type`：寫入 cold notes 時先標記 `knowledge` / `experience` / `both`
- `refine_experience.py`：把 `memory_type=experience` 的 cold notes 自動提升到 `experience/skill-[skill-id].md`

## 中文短版

我修正了 Deep-Memory 之前「明明有紀錄，卻像沒有經驗」的問題。

原因是很多任務紀錄都留在 cold notes，沒有進入跨技能經驗熱庫。

這版新增：
- `memory_type=knowledge|experience|both`
- `search.py --memory-type experience`
- `refine_experience.py --dry-run / --apply`

現在 cold notes 可以自動分流，技能踩坑也能被提升到 `experience/skill-*.md`，下次觸發同一技能時才真的能用上。

## 中文長版

這次 Deep-Memory 更新重點不是再加一個 RAG，而是修正記憶系統最容易被忽略的問題：

「有記錄」不等於「有可用經驗」。

我發現之前的 cold notes 裡其實累積了不少真實任務經驗，例如 agent-browser、systematic-debugging、GitHub Pages 部署、landing-page React handler 修復等。但這些紀錄大多只是存在 `cold-notes/raw.jsonl`，沒有被整理進 `experience/skill-*.md`，所以跨技能經驗查詢時看起來很薄。

這版做了兩個修正：

1. cold notes 新增 `memory_type`
   - `knowledge`：通用知識、規則、SOP
   - `experience`：某個技能/工具/專案的踩坑與驗證經驗
   - `both`：同一事件同時包含通用規則與技能經驗

2. 新增 `refine_experience.py`
   - 掃描 `memory_type=experience` / `both`
   - 依 skill 分組
   - 自動寫入 `experience/skill-[skill-id].md`
   - 更新 `experience/_index.json`
   - 標記來源 cold notes 為 reviewed
   - 重建索引後可用 `search.py --memory-type experience` 驗證

這讓 Deep-Memory 從「把東西記下來」往前走到「把經驗整理成下次真的會被載入的技能記憶」。

## Threads / X

Deep-Memory update:

I fixed the gap where the system had many raw notes but the skill experience store still looked empty.

New flow:
- cold notes get `memory_type=knowledge|experience|both`
- `search.py --memory-type experience`
- `refine_experience.py --dry-run / --apply`

Now real skill/tool lessons can be promoted from `cold-notes/raw.jsonl` into `experience/skill-*.md`, so the next run can actually reuse them.

## LinkedIn

I shipped a refinement update for Deep-Memory, my local-first memory system for AI coding agents.

The problem I found was subtle: the system had plenty of raw notes, but many of them stayed in `cold-notes/raw.jsonl`. That meant the cross-skill experience store still looked thin, even though useful lessons had been captured.

The new flow adds:

- `memory_type=knowledge|experience|both` on cold notes
- `search.py --memory-type experience`
- `refine_experience.py` to promote skill/tool lessons into `experience/skill-[skill-id].md`
- automatic `experience/_index.json` updates
- reviewed source tracking back to the original cold note

The key lesson: persistent memory is not just about storing more text. It needs a promotion path from raw logs to curated, reusable experience.

## 發文搭配圖文重點

- 標題：有記錄，不代表有經驗
- 副標：Deep-Memory 新增 `memory_type` 與經驗熱庫提升流程
- 三個重點：
  - Cold notes 先分類：knowledge / experience / both
  - Experience 可自動提升到 `experience/skill-*.md`
  - 重新索引後可用 `--memory-type experience` 驗證

## Hashtags

`#AIAgents` `#Codex` `#DeveloperTools` `#RAG` `#KnowledgeManagement` `#LocalFirst` `#AgentMemory`
