# -*- coding: utf-8 -*-
"""
write_cold.py
將本次對話的原始摘要或暫時性筆記，
追加寫入冷庫（cold-notes/raw.jsonl）。
不需用戶確認，每次對話結束後由 Agent 自動呼叫。
"""
import os
import sys
import json
import argparse
from datetime import datetime

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def resolve_memory_type(memory_type, skill):
    """依使用者指定或技能 ID 推斷冷庫條目的記憶類型。"""
    if memory_type != "auto":
        return memory_type

    normalized_skill = (skill or "").strip().lower()
    general_skills = {"", "general", "none", "deep-memory"}
    if normalized_skill in general_skills:
        return "knowledge"
    return "experience"


# 精煉提醒閾值：「待精煉（quality 不是 reviewed）」≥ 30 筆才提醒。
# 以總筆數判斷會在冷庫累積數千筆、且多數已精煉後每寫一筆都提醒（2026-09-23 修正）；
# 30 筆約是一次小批精煉的適當量，夠小才能讓子代理快速處理，夠大才不會頻繁打斷。
REFINE_THRESHOLD = 30


def count_notes(jsonl_path):
    """統計冷庫總筆數與待精煉筆數；無法解析的行計入總數但不算待精煉。"""
    total = 0
    pending = 0
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            total += 1
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("quality") != "reviewed":
                pending += 1
    return total, pending


def refine_hint(total, pending, threshold=None):
    """待精煉筆數達門檻時回傳提示文字行；未達門檻回傳空清單。"""
    threshold = REFINE_THRESHOLD if threshold is None else threshold
    if pending < threshold:
        return []
    return [
        "",
        f"[⚠️ 精煉提示] 冷庫待精煉 {pending} 筆（≥ {threshold} 筆；冷庫總數 {total} 筆）。",
        "  建議執行精煉流程：refine_prepare.py 分包 → 平行子代理起草 → refine_apply.py 套用。",
        "  請告知 Agent：「幫我精煉冷庫」",
    ]


def main():
    parser = argparse.ArgumentParser(description="Append a raw note to cold store (cold-notes/raw.jsonl)")
    # 優先從環境變數 DEEP_MEMORY_WORKSPACE 取得工作目錄，否則預設為使用者家目錄下的 .deep-memory
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE")
    if not default_ws:
        default_ws = os.path.join(os.path.expanduser("~"), ".deep-memory")

    parser.add_argument("--workspace",  type=str, default=default_ws, help="工作目錄根路徑")
    parser.add_argument("--project",    type=str, default=None, help="本條記錄所屬的專案名稱；未指定時自動取當前工作目錄（os.getcwd()）的資料夾名稱")
    parser.add_argument("--topic",      type=str, required=True,  help="本條記錄的主題（一句話）")
    parser.add_argument("--content",    type=str, required=True,  help="詳細內容（可包含步驟、程式碼片段等）")
    parser.add_argument("--tags",       type=str, default="",     help="逗號分隔的標籤（如 backend,fastapi,session）")
    parser.add_argument("--skill",      type=str, default="general", help="觸發本條記錄的技能 ID（如 chroma-hybrid-search）")
    parser.add_argument("--memory-type", type=str, default="auto",
                        choices=["auto", "knowledge", "experience", "both"],
                        help="記憶類型：knowledge=通用知識、experience=技能/工具經驗、both=精煉時需拆成兩筆；auto=依 skill 粗略推斷")
    parser.add_argument("--quality",    type=str, default="raw",
                        choices=["raw", "reviewed"],
                        help="品質標記：raw=原始、reviewed=已人工確認（精煉後使用）")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.workspace)
    cold_dir = os.path.join(base_dir, "cold-notes")
    os.makedirs(cold_dir, exist_ok=True)

    jsonl_path = os.path.join(cold_dir, "raw.jsonl")

    # 專案名稱：未顯式指定時，以「實際觸發指令當下的工作目錄」推斷（而非 base_dir，
    # 因為 base_dir 現在固定指向 home 的全域儲存區，不同專案都寫進同一份檔案）
    project = args.project or os.path.basename(os.getcwd())
    memory_type = resolve_memory_type(args.memory_type, args.skill)

    # 建立條目
    entry = {
        "date":    datetime.now().strftime("%Y-%m-%d"),
        "time":    datetime.now().strftime("%H:%M"),
        "topic":   args.topic,
        "content": args.content,
        "tags":    [t.strip() for t in args.tags.split(",") if t.strip()],
        "skill":   args.skill,
        "memory_type": memory_type,
        "project": project,
        "quality": args.quality
    }

    # 追加到 JSONL（每行一筆 JSON）
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # 統計目前冷庫條目數（總數與待精煉數）
    total, pending = count_notes(jsonl_path)

    print(f"[OK] 冷庫已寫入：{args.topic}（專案：{project}）")
    print(f"[INFO] 記憶類型：{memory_type}")
    print(f"[INFO] 冷庫目前共 {total} 筆條目，其中待精煉 {pending} 筆")

    for line in refine_hint(total, pending):
        print(line)


if __name__ == "__main__":
    main()
