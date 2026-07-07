#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""替既有 cold-notes/raw.jsonl 補齊 memory_type 欄位。"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def infer_memory_type(entry):
    """依既有條目的 skill 欄位推斷 knowledge / experience。"""
    current = entry.get("memory_type")
    if current in {"knowledge", "experience", "both"}:
        return current

    skill = (entry.get("skill") or "").strip().lower()
    if skill in {"", "general", "none", "deep-memory"}:
        return "knowledge"
    return "experience"


def migrate_file(raw_path, dry_run=False):
    """讀取 JSONL、補齊缺漏欄位、必要時備份並覆寫原檔。"""
    if not raw_path.exists():
        print(f"[SKIP] 找不到冷庫檔案：{raw_path}")
        return 0

    lines = raw_path.read_text(encoding="utf-8").splitlines()
    updated_lines = []
    changed = 0
    skipped_bad = 0

    for line in lines:
        if not line.strip():
            updated_lines.append(line)
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            skipped_bad += 1
            updated_lines.append(line)
            continue

        if not isinstance(entry, dict):
            skipped_bad += 1
            updated_lines.append(line)
            continue

        memory_type = infer_memory_type(entry)
        if entry.get("memory_type") != memory_type:
            entry["memory_type"] = memory_type
            changed += 1
        updated_lines.append(json.dumps(entry, ensure_ascii=False))

    if dry_run:
        print(f"[DRY-RUN] 將更新 {changed} 筆；壞行略過 {skipped_bad} 筆。")
        return changed

    if changed:
        backup = raw_path.with_suffix(raw_path.suffix + "." + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
        shutil.copy2(raw_path, backup)
        raw_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
        print(f"[OK] 已更新 {changed} 筆 memory_type。")
        print(f"[OK] 原檔備份：{backup}")
    else:
        print("[OK] 所有 cold notes 都已有有效 memory_type，無需更新。")

    if skipped_bad:
        print(f"[WARN] 有 {skipped_bad} 行不是合法 JSON 物件，已原樣保留。")
    return changed


def main(argv=None):
    """命令列進入點：解析 workspace 並遷移冷庫。"""
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".deep-memory")
    parser = argparse.ArgumentParser(description="Backfill memory_type in cold-notes/raw.jsonl")
    parser.add_argument("--workspace", default=default_ws, help="記憶資料根目錄，預設 ~/.deep-memory")
    parser.add_argument("--dry-run", action="store_true", help="只顯示將更新筆數，不寫入")
    args = parser.parse_args(argv)

    raw_path = Path(args.workspace).expanduser().resolve() / "cold-notes" / "raw.jsonl"
    migrate_file(raw_path, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
