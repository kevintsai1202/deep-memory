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

    # 統計目前冷庫條目數
    with open(jsonl_path, "r", encoding="utf-8") as f:
        count = sum(1 for line in f if line.strip())

    print(f"[OK] 冷庫已寫入：{args.topic}（專案：{project}）")
    print(f"[INFO] 記憶類型：{memory_type}")
    print(f"[INFO] 冷庫目前共 {count} 筆條目")

    # 精煉提醒閾值：≥ 20 筆時提醒（憑經驗選擇的中間值——夠小才不會讓冷庫無限累積
    # 未精煉的雜訊，夠大才不會每寫幾筆就打斷一次；非精確調校過的數字，可依實際使用調整）
    REFINE_THRESHOLD = 20
    if count >= REFINE_THRESHOLD:
        print()
        print(f"[⚠️ 精煉提示] 冷庫已累積 {count} 筆原始記錄（≥ {REFINE_THRESHOLD} 筆）。")
        print("  建議執行精煉流程，依 memory_type 將高頻/高價值條目分流到 knowledge-base/ 或 experience/。")
        print("  請告知 Agent：「幫我精煉冷庫」")

if __name__ == "__main__":
    main()
