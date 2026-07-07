#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""將 cold-notes 中的 experience 條目提升到 experience 熱庫。"""
import argparse
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


VALID_MEMORY_TYPES = {"experience", "both"}


def slugify(text, max_len=80):
    """將標題或 skill ID 轉成適合檔名/slug 的穩定字串。"""
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w一-鿿.-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return (text[:max_len] or "entry").strip(".-") or "entry"


def normalize_skill_id(skill):
    """標準化 skill ID；空值一律歸為 general。"""
    skill = (skill or "").strip()
    return skill or "general"


def skill_filename(skill_id):
    """產生可跨平台使用的 skill experience 檔名。"""
    return "skill-" + slugify(skill_id.replace(":", "-")) + ".md"


def load_cold_notes(raw_path):
    """讀取 cold-notes/raw.jsonl，保留行號供來源追蹤。"""
    if not raw_path.exists():
        return []

    notes = []
    for line_no, line in enumerate(raw_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        entry["_line_no"] = line_no
        notes.append(entry)
    return notes


def select_experience_notes(notes, include_reviewed=False):
    """挑出可提升的 experience/both 冷庫條目。"""
    selected = []
    for entry in notes:
        if entry.get("memory_type") not in VALID_MEMORY_TYPES:
            continue
        if not include_reviewed and entry.get("quality") == "reviewed":
            continue
        selected.append(entry)
    return selected


def load_index(index_path):
    """讀取 experience/_index.json；缺檔時建立空 index。"""
    if not index_path.exists():
        return {"_version": 1, "categories": []}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except ValueError:
        return {"_version": 1, "categories": []}
    if not isinstance(data, dict):
        return {"_version": 1, "categories": []}
    data.setdefault("_version", 1)
    data.setdefault("categories", [])
    return data


def update_index(index_data, skill_id, filename, keywords):
    """新增或更新 experience index 中的 skill category。"""
    categories = index_data.setdefault("categories", [])
    category = None
    for item in categories:
        if item.get("id") == skill_id:
            category = item
            break
    if category is None:
        category = {"id": skill_id, "file": filename, "title": skill_id, "keywords": []}
        categories.append(category)

    category["file"] = filename
    category["title"] = category.get("title") or skill_id
    existing = [k for k in category.get("keywords", []) if isinstance(k, str)]
    seen = {k.lower(): k for k in existing}
    for keyword in keywords:
        key = keyword.strip()
        if not key:
            continue
        seen.setdefault(key.lower(), key)
    category["keywords"] = list(seen.values())[:40]
    categories.sort(key=lambda x: x.get("id", ""))


def existing_sources(markdown):
    """擷取已提升過的 cold note 來源，避免重複寫入。"""
    return set(re.findall(r"\*\*Source cold note:\*\*\s*(cold-notes/raw\.jsonl#L\d+)", markdown))


def split_sentences(text):
    """粗略切分中英混合文字，供條目內容拆成可讀 bullets。"""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = re.split(r"(?<=[。！？.!?])\s+|；|;|\n+", text)
    out = []
    for part in parts:
        part = part.strip()
        if part:
            out.append(part)
    return out


def build_entry(note):
    """將單筆 cold note 轉成 experience 熱庫 markdown 條目。"""
    title = note.get("topic") or "experience note"
    skill_id = normalize_skill_id(note.get("skill"))
    date = note.get("date") or datetime.now().strftime("%Y-%m-%d")
    project = note.get("project") or "(unknown)"
    tags = [t for t in note.get("tags", []) if isinstance(t, str) and t.strip()]
    source = f"cold-notes/raw.jsonl#L{note.get('_line_no')}"
    bullets = split_sentences(note.get("content", ""))
    if not bullets:
        bullets = ["保留原始 cold note 內容，供下次使用此技能時快速回顧。"]

    lines = [
        f"## 🔧 {title}",
        f"**Date:** {date}",
        f"**Skill:** {skill_id}",
        f"**Project:** {project}",
        f"**Context:** {title}",
        "**Solution / Lesson:**",
    ]
    lines.extend(f"- {item}" for item in bullets[:8])
    if len(bullets) > 8:
        lines.append(f"- ...（另有 {len(bullets) - 8} 個細節保留於來源 cold note）")
    lines.extend([
        "**Source cold note:** " + source,
        "**keywords:** " + ", ".join(tags or [skill_id, project]),
        "",
    ])
    return "\n".join(lines)


def mark_reviewed(raw_path, selected_line_numbers):
    """將已提升的 cold notes 標為 reviewed，並備份原檔。"""
    if not selected_line_numbers:
        return None, 0

    lines = raw_path.read_text(encoding="utf-8").splitlines()
    changed = 0
    updated = []
    for line_no, line in enumerate(lines, start=1):
        if line_no not in selected_line_numbers or not line.strip():
            updated.append(line)
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            updated.append(line)
            continue
        if isinstance(entry, dict) and entry.get("quality") != "reviewed":
            entry["quality"] = "reviewed"
            changed += 1
            updated.append(json.dumps(entry, ensure_ascii=False))
        else:
            updated.append(line)

    if changed:
        backup = raw_path.with_suffix(raw_path.suffix + "." + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
        shutil.copy2(raw_path, backup)
        raw_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        return backup, changed
    return None, 0


def build_plan(workspace, include_reviewed=False, skill_filter=None):
    """建立提升計畫，不修改任何檔案。"""
    raw_path = workspace / "cold-notes" / "raw.jsonl"
    exp_dir = workspace / "experience"
    index_path = exp_dir / "_index.json"
    notes = select_experience_notes(load_cold_notes(raw_path), include_reviewed=include_reviewed)
    if skill_filter:
        wanted = {normalize_skill_id(s) for s in skill_filter}
        notes = [n for n in notes if normalize_skill_id(n.get("skill")) in wanted]

    groups = defaultdict(list)
    for note in notes:
        groups[normalize_skill_id(note.get("skill"))].append(note)

    index_data = load_index(index_path)
    plan = []
    for skill_id in sorted(groups):
        filename = skill_filename(skill_id)
        target = exp_dir / filename
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        sources = existing_sources(current)
        pending = []
        for note in groups[skill_id]:
            source = f"cold-notes/raw.jsonl#L{note.get('_line_no')}"
            if source not in sources:
                pending.append(note)
        if pending:
            plan.append({
                "skill_id": skill_id,
                "file": filename,
                "target": target,
                "entries": pending,
            })
    return raw_path, index_path, index_data, plan


def apply_plan(index_path, index_data, plan, mark_raw_path=None):
    """套用提升計畫：寫入 experience md、更新 index，並選擇性標記 cold notes。"""
    total_entries = 0
    reviewed_lines = set()
    for item in plan:
        target = item["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        entries = [build_entry(note) for note in item["entries"]]
        text = existing.rstrip() + ("\n\n" if existing.strip() else "") + "\n".join(entries)
        target.write_text(text.rstrip() + "\n", encoding="utf-8")

        keywords = [item["skill_id"]]
        for note in item["entries"]:
            keywords.extend([t for t in note.get("tags", []) if isinstance(t, str)])
            if note.get("project"):
                keywords.append(note["project"])
            reviewed_lines.add(note["_line_no"])
        update_index(index_data, item["skill_id"], item["file"], keywords)
        total_entries += len(item["entries"])

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    backup = None
    reviewed = 0
    if mark_raw_path:
        backup, reviewed = mark_reviewed(mark_raw_path, reviewed_lines)
    return total_entries, reviewed, backup


def print_plan(plan):
    """輸出 dry-run 計畫摘要。"""
    if not plan:
        print("[OK] 沒有待提升的 experience cold notes。")
        return
    total = sum(len(item["entries"]) for item in plan)
    print(f"[PLAN] 將提升 {total} 筆 experience cold notes 到 {len(plan)} 個 skill 檔案：")
    for item in plan:
        lines = ", ".join(f"L{note['_line_no']}" for note in item["entries"][:10])
        if len(item["entries"]) > 10:
            lines += f", ... +{len(item['entries']) - 10}"
        print(f"  - {item['skill_id']} -> {item['file']} ({len(item['entries'])} 筆: {lines})")


def main(argv=None):
    """命令列進入點。"""
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".deep-memory")
    parser = argparse.ArgumentParser(description="Promote cold-store experience notes into experience/*.md")
    parser.add_argument("--workspace", default=default_ws, help="記憶資料根目錄，預設 ~/.deep-memory")
    parser.add_argument("--skill", action="append", help="只提升指定 skill，可重複指定")
    parser.add_argument("--include-reviewed", action="store_true", help="包含已 reviewed 的 cold notes；預設略過")
    parser.add_argument("--dry-run", action="store_true", help="只顯示提升計畫，不寫入；預設行為也是 dry-run")
    parser.add_argument("--apply", action="store_true", help="實際寫入 experience 熱庫；未指定時只 dry-run")
    parser.add_argument("--no-mark-reviewed", action="store_true", help="apply 後不把來源 cold notes 標為 reviewed")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).expanduser().resolve()
    raw_path, index_path, index_data, plan = build_plan(
        workspace,
        include_reviewed=args.include_reviewed,
        skill_filter=args.skill,
    )
    print_plan(plan)
    if not args.apply:
        print("[DRY-RUN] 未寫入任何檔案。加上 --apply 才會更新 experience 熱庫。")
        return 0

    total, reviewed, backup = apply_plan(
        index_path,
        index_data,
        plan,
        mark_raw_path=None if args.no_mark_reviewed else raw_path,
    )
    print(f"[OK] 已提升 {total} 筆 experience 條目。")
    if reviewed:
        print(f"[OK] 已標記 {reviewed} 筆 cold notes 為 reviewed。")
    if backup:
        print(f"[OK] cold notes 原檔備份：{backup}")
    print("[NEXT] 請執行 chroma-hybrid-search/scripts/update_db.py 重建/更新向量索引。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
