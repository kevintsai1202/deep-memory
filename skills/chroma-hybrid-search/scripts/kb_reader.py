# -*- coding: utf-8 -*-
"""
kb_reader.py
熱庫（knowledge-base/、experience/）與冷庫（cold-notes/raw.jsonl）的共用讀取／切段邏輯。
search.py 與 update_db.py 都從這裡 import，確保兩邊看到的文件集合（id、文字、metadata）
永遠一致——避免向量庫已索引的內容，查詢腳本卻讀不到對應文字而被悄悄濾掉。
"""
import os
import re
import json

# 條目切段邊界：每個 "## 🔧" 標題開始一個新條目
_ENTRY_HEADING = re.compile(r"(?=^## 🔧 )", re.MULTILINE)
_ENTRY_TITLE = re.compile(r"## 🔧\s*(.+)")
_KEYWORDS_LINE = re.compile(r"\*\*keywords[:：]\*\*\s*(.+)", re.IGNORECASE)
_SKILL_LINE = re.compile(r"\*\*(?:Skill|技能)[:：]\*\*\s*(.+)", re.IGNORECASE)
_SKILL_FROM_FILENAME = re.compile(r"skill-(.+)\.md$")


def slugify(text, max_len=50):
    """將條目標題轉為穩定、可讀的 id 片段（供切段後的文件 id 使用）"""
    text = text.strip().lower()
    text = re.sub(r"[^\w一-鿿]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "entry"


def split_entries(content):
    """
    依 "## 🔧" 標題切分條目，回傳 [(title, entry_text), ...]。
    標題前的內容（檔案 H1 標題／來源引用區塊）不當作獨立條目索引。
    若檔案完全沒有 "## 🔧" 標題（不符範本的舊檔案），回傳空列表，由呼叫端決定如何相容處理。
    """
    parts = _ENTRY_HEADING.split(content)
    entries = []
    for part in parts:
        part = part.strip()
        if not part.startswith("## 🔧"):
            continue
        title_match = _ENTRY_TITLE.match(part.splitlines()[0])
        title = title_match.group(1).strip() if title_match else "entry"
        entries.append((title, part))
    return entries


def extract_tags(entry_text):
    """從條目文字擷取 experience 範本的 `**keywords:**` 那一行；knowledge-base 條目沒有這行，回傳空列表"""
    m = _KEYWORDS_LINE.search(entry_text)
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def extract_skill(entry_text):
    """從 experience 條目文字擷取精確 skill ID，支援檔名不能表示的字元如冒號。"""
    m = _SKILL_LINE.search(entry_text)
    if not m:
        return None
    return m.group(1).strip() or None


def read_knowledge_base(base_dir):
    """讀取熱庫（knowledge-base/ 與 experience/），依條目切段，回傳 [{path, text, source, skill?, tags?}, ...]"""
    kb_dir = os.path.join(base_dir, "knowledge-base")
    exp_dir = os.path.join(base_dir, "experience")

    import glob
    files = glob.glob(os.path.join(kb_dir, "*.md")) + glob.glob(os.path.join(exp_dir, "*.md"))
    docs = []
    for f in files:
        if os.path.basename(f) == "_index.json":
            continue
        if os.path.getsize(f) < 200:
            continue

        rel_path = os.path.relpath(f, base_dir)
        with open(f, "r", encoding="utf-8") as file:
            content = file.read()

        # experience/skill-[skill-id].md 的 skill-id 從檔名取得，套用到該檔案切出的每個條目
        skill_match = _SKILL_FROM_FILENAME.match(os.path.basename(f))
        skill = skill_match.group(1) if skill_match else None
        memory_type = "experience" if skill else "knowledge"

        entries = split_entries(content)
        if not entries:
            # 不符 "## 🔧" 範本的舊檔案／自訂檔案：整份當一份文件索引，維持向下相容
            doc = {"path": rel_path, "text": content, "source": "hot", "memory_type": memory_type}
            if skill:
                doc["skill"] = skill
            docs.append(doc)
            continue

        used_slugs = {}
        for title, entry_text in entries:
            slug = slugify(title)
            count = used_slugs.get(slug, 0) + 1
            used_slugs[slug] = count
            if count > 1:
                slug = f"{slug}-{count}"
            entry_skill = extract_skill(entry_text) or skill

            doc = {
                "path": f"{rel_path}#{slug}",
                "text": entry_text,
                "source": "hot",
                "memory_type": memory_type
            }
            if entry_skill:
                doc["skill"] = entry_skill
            tags = extract_tags(entry_text)
            if tags:
                doc["tags"] = tags
            docs.append(doc)
    return docs


def infer_memory_type(entry):
    """從舊版冷庫條目推斷記憶類型，讓未補欄位的歷史資料可維持可搜尋與可精煉。"""
    memory_type = entry.get("memory_type")
    if memory_type in {"knowledge", "experience", "both"}:
        return memory_type

    skill = (entry.get("skill") or "").strip().lower()
    if skill in {"", "general", "none", "deep-memory"}:
        return "knowledge"
    return "experience"


def read_cold_notes(base_dir):
    """讀取冷庫（cold-notes/raw.jsonl）中的所有條目，轉換為可索引格式"""
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
                tags_str = ", ".join(entry.get("tags", []))
                memory_type = infer_memory_type(entry)
                text = (
                    f"主題：{entry.get('topic', '')}\n"
                    f"日期：{entry.get('date', '')}\n"
                    f"記憶類型：{memory_type}\n"
                    f"技能：{entry.get('skill', 'general')}\n"
                    f"標籤：{tags_str}\n"
                    f"內容：{entry.get('content', '')}"
                )
                doc = {
                    "path": f"cold-notes/raw.jsonl#L{i+1}",
                    "text": text,
                    "source": "cold",
                    "tags": entry.get("tags", []),
                    "skill": entry.get("skill", "general"),
                    "memory_type": memory_type
                }
                # project 欄位是後補的：寫入時間早於此改動的舊條目沒有這個 key，
                # 這裡刻意用 entry.get() 而不給預設值，讓舊條目維持「不屬於任何專案」，
                # 之後才能被 search.py 的「查無專案結果 → 退回全域搜尋」邏輯正確判斷
                if entry.get("project"):
                    doc["project"] = entry["project"]
                docs.append(doc)
            except json.JSONDecodeError:
                continue
    return docs
