#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refine_apply.py
把子代理依工作包產出的 *.drafts.md 套用到熱庫（只用標準函式庫）：
- TARGET：在熱庫檔尾端新增完整 `## 🔧` 條目（experience/skill-*.md 不存在時會建立並登記索引）
- APPEND-TO：把條列點插入指定既有條目（合併其 Source 行）
- NEW CATEGORY：建立 knowledge-base/<id>.md 並登記索引
- KEYWORDS：補索引關鍵字
- REVIEWED：把行號標為 quality=reviewed（含略過項）

寫入前全部驗證（行號須涵蓋 manifest、行內容雜湊須吻合、目標與既有條目須存在），任一失敗即不寫入任何檔案；
寫入前把會被修改的檔案備份到 <work-dir>/backup/。--dry-run 只回報計畫。
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import refine_prepare

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 區塊標記（只認行首完全吻合者；條目內的 `## 🔧` 不是區塊標記）
_BLOCK_RE = re.compile(r"^(### TARGET:|### APPEND-TO:|### NEW CATEGORY:|## KEYWORDS\s*$|## REVIEWED\s*$)")
_TARGET_PATH_RE = re.compile(r"^(knowledge-base|experience)/[^/\\]+\.md$")
_SOURCE_PREFIX = "**Source cold store entries:**"
_ENTRY_HEADING = "## 🔧"


class ApplyError(Exception):
    """草稿或冷庫狀態不符合套用條件；拋出時不會寫入任何檔案。"""


def _clean_body(lines):
    """去掉區塊前後空行與分隔線 `---`。"""
    body = "\n".join(lines).strip()
    body = re.sub(r"(\n\s*---\s*)+$", "", body).strip()
    return body


def parse_drafts(text):
    """解析單一草稿檔，回傳 targets / appends / new_categories / keywords / reviewed。"""
    result = {"targets": [], "appends": [], "new_categories": [], "keywords": {}, "reviewed": {}}
    blocks, current = [], None
    for line in text.splitlines():
        if _BLOCK_RE.match(line):
            current = [line, []]
            blocks.append(current)
        elif current is not None:
            current[1].append(line)
    for header, lines in blocks:
        if header.startswith("### TARGET:"):
            result["targets"].append((header[len("### TARGET:"):].strip(), _clean_body(lines)))
        elif header.startswith("### APPEND-TO:"):
            spec = header[len("### APPEND-TO:"):].strip()
            if "::" not in spec:
                raise ApplyError(f"APPEND-TO 缺少「:: 條目標題」：{header}")
            rel, title = [x.strip() for x in spec.split("::", 1)]
            result["appends"].append((rel, title, _clean_body(lines)))
        elif header.startswith("### NEW CATEGORY:"):
            parts = [x.strip() for x in header[len("### NEW CATEGORY:"):].split("|")]
            if len(parts) != 3 or not parts[0]:
                raise ApplyError(f"NEW CATEGORY 需為「id | 標題 | 關鍵字」：{header}")
            kws = [k.strip() for k in parts[2].split(",") if k.strip()]
            result["new_categories"].append((parts[0], parts[1], kws, _clean_body(lines)))
        elif header.startswith("## KEYWORDS"):
            for line in lines:
                m = re.match(r"^\s*-\s*([\w.\-:]+)\s*:\s*(.+)$", line)
                if m:
                    result["keywords"].setdefault(m.group(1), []).extend(
                        k.strip() for k in m.group(2).split(",") if k.strip())
        elif header.startswith("## REVIEWED"):
            for line in lines:
                m = re.match(r"^\s*L(\d+)\s*\|\s*(.+?)\s*$", line)
                if m:
                    result["reviewed"][int(m.group(1))] = m.group(2)
    return result


class _Store:
    """在記憶體中暫存要寫回的檔案內容，最後一次寫入並先備份。"""

    def __init__(self, workspace):
        self.ws = Path(workspace)
        self.files = {}      # 相對路徑 → 新內容
        self.created = set()  # 本次新建的相對路徑

    def exists(self, rel):
        return rel in self.files or (self.ws / rel).exists()

    def read(self, rel):
        if rel not in self.files:
            self.files[rel] = (self.ws / rel).read_text(encoding="utf-8")
        return self.files[rel]

    def create(self, rel, text):
        self.files[rel] = text
        self.created.add(rel)

    def set(self, rel, text):
        self.files[rel] = text

    def commit(self, backup_dir):
        """備份既有檔案後寫入全部變更。"""
        for rel in sorted(self.files):
            src = self.ws / rel
            if src.exists():
                dst = Path(backup_dir) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        for rel, text in self.files.items():
            path = self.ws / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)


def _append_entry(text, body):
    """在檔尾追加條目，保留一個空行分隔。"""
    return text.rstrip("\n") + "\n\n" + body.strip() + "\n"


def _insert_into_entry(text, title, body, rel):
    """把追加草稿的條列點插入指定條目：有 Source 行則插在其前並合併來源，否則接在條目最後。"""
    m = re.search(rf"^{re.escape(_ENTRY_HEADING)} {re.escape(title)}\s*$", text, flags=re.M)
    if not m:
        raise ApplyError(f"{rel} 找不到既有條目「{title}」")
    start = m.end()
    nxt = re.search(r"^## ", text[start:], flags=re.M)
    end = start + nxt.start() if nxt else len(text)
    entry = text[start:end]

    add_lines = [l for l in body.splitlines() if l.strip() and not l.startswith(_SOURCE_PREFIX)]
    add_refs = []
    for l in body.splitlines():
        if l.startswith(_SOURCE_PREFIX):
            add_refs.extend(re.findall(r"L\d+", l))
    src = re.search(rf"^{re.escape(_SOURCE_PREFIX)}.*$", entry, flags=re.M)
    if src:
        existing_refs = set(re.findall(r"L\d+", src.group(0)))
        new_refs = [r for r in add_refs if r not in existing_refs]
        source_line = src.group(0) + ("".join(f", {r}" for r in new_refs))
        new_entry = entry[:src.start()] + "\n".join(add_lines) + "\n" + source_line + entry[src.end():]
    else:
        trailing = entry[len(entry.rstrip("\n")):]
        extra = add_lines + ([f"{_SOURCE_PREFIX} cold-notes/raw.jsonl#{', '.join(add_refs)}"] if add_refs else [])
        new_entry = entry.rstrip("\n") + "\n" + "\n".join(extra) + (trailing or "\n")
    return text[:start] + new_entry + text[end:]


def _load_index(store, folder):
    """讀取索引為 dict（透過 store 以便一起備份與寫回）。"""
    rel = f"{folder}/_index.json"
    return rel, json.loads(store.read(rel)) if store.exists(rel) else {"categories": []}


def apply(workspace, work_dir, dry_run=False, allow_partial=False):
    """驗證並套用工作目錄內所有草稿；回傳報告 dict。"""
    ws, work = Path(workspace), Path(work_dir)
    manifest_path = work / "manifest.json"
    if not manifest_path.exists():
        raise ApplyError(f"找不到 {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {e["line"]: e["hash"] for e in manifest["entries"]}

    drafts = sorted(work.glob("*.drafts.md"))
    if not drafts:
        raise ApplyError(f"{work} 內沒有任何 *.drafts.md")
    merged = {"targets": [], "appends": [], "new_categories": [], "keywords": {}, "reviewed": {}}
    for path in drafts:
        parsed = parse_drafts(path.read_text(encoding="utf-8"))
        for key in ("targets", "appends", "new_categories"):
            merged[key].extend(parsed[key])
        for cid, kws in parsed["keywords"].items():
            merged["keywords"].setdefault(cid, []).extend(kws)
        for line, status in parsed["reviewed"].items():
            if line in merged["reviewed"]:
                raise ApplyError(f"L{line} 在多個草稿的 REVIEWED 重複出現")
            merged["reviewed"][line] = status

    # ---- 驗證：行號範圍 ----
    unknown = sorted(set(merged["reviewed"]) - set(expected))
    if unknown:
        raise ApplyError(f"REVIEWED 含不在本批 manifest 的行號：{', '.join(f'L{l}' for l in unknown)}")
    missing = sorted(set(expected) - set(merged["reviewed"]))
    if missing and not allow_partial:
        raise ApplyError(f"REVIEWED 缺少 {len(missing)} 筆：{', '.join(f'L{l}' for l in missing)}"
                         "（確認後可加 --allow-partial 只標已列出者）")

    # ---- 驗證：冷庫行內容與 manifest 雜湊一致（避免行號錯位標錯） ----
    raw_rel = "cold-notes/raw.jsonl"
    raw_path = ws / raw_rel
    raw_lines = raw_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for line in merged["reviewed"]:
        if line > len(raw_lines):
            raise ApplyError(f"L{line} 超出 raw.jsonl 行數")
        try:
            note = json.loads(raw_lines[line - 1])
        except ValueError:
            raise ApplyError(f"L{line} 不是有效的 JSON")
        if refine_prepare.note_hash(note) != expected[line]:
            raise ApplyError(f"L{line} 內容與準備時不同（雜湊不符），請重新執行 refine_prepare")

    store = _Store(ws)
    report = {"entries_added": 0, "appends": 0, "new_categories": [], "keywords_added": 0,
              "lines_marked": 0, "files_touched": [], "warnings": []}
    kb_rel, kb_index = _load_index(store, "knowledge-base")
    exp_rel, exp_index = _load_index(store, "experience")
    index_changed = {kb_rel: False, exp_rel: False}

    # ---- 新分類 ----
    for cid, title, kws, body in merged["new_categories"]:
        rel = f"knowledge-base/{cid}.md"
        if store.exists(rel):
            raise ApplyError(f"新分類 {rel} 已存在，請改用 TARGET")
        store.create(rel, f"# {title}\n")
        if body:
            if _ENTRY_HEADING not in body:
                raise ApplyError(f"新分類 {cid} 的內容缺少「{_ENTRY_HEADING}」條目標題")
            store.set(rel, _append_entry(store.read(rel), body))
            report["entries_added"] += body.count(_ENTRY_HEADING)
        kb_index["categories"].append({"id": cid, "file": f"{cid}.md", "title": title, "keywords": kws})
        index_changed[kb_rel] = True
        report["new_categories"].append(cid)

    # ---- 新增完整條目 ----
    for rel, body in merged["targets"]:
        rel = rel.replace("\\", "/")
        if not _TARGET_PATH_RE.match(rel):
            raise ApplyError(f"TARGET 路徑不合法：{rel}")
        if _ENTRY_HEADING not in body:
            raise ApplyError(f"TARGET {rel} 的內容缺少「{_ENTRY_HEADING}」條目標題")
        if store.exists(rel):
            store.set(rel, _append_entry(store.read(rel), body))
        elif rel.startswith("experience/skill-"):
            store.create(rel, body.strip() + "\n")
            file = rel.split("/", 1)[1]
            sid = file[len("skill-"):-len(".md")]
            kw_line = re.search(r"^\*\*keywords:\*\*\s*(.+)$", body, flags=re.M)
            kws = [k.strip() for k in kw_line.group(1).split(",")] if kw_line else []
            exp_index["categories"].append({"id": sid, "file": file, "title": sid, "keywords": kws})
            index_changed[exp_rel] = True
        else:
            raise ApplyError(f"{rel} 不存在；knowledge-base 新檔案請用 NEW CATEGORY 建立")
        report["entries_added"] += body.count(_ENTRY_HEADING)

    # ---- 追加到既有條目 ----
    for rel, title, body in merged["appends"]:
        rel = rel.replace("\\", "/")
        if not store.exists(rel):
            raise ApplyError(f"APPEND-TO 目標不存在：{rel}")
        store.set(rel, _insert_into_entry(store.read(rel), title, body, rel))
        report["appends"] += 1

    # ---- 索引關鍵字 ----
    for cid, kws in merged["keywords"].items():
        found = False
        for rel, index in ((kb_rel, kb_index), (exp_rel, exp_index)):
            for cat in index["categories"]:
                if cat.get("id") == cid:
                    found = True
                    for kw in kws:
                        if kw not in cat.setdefault("keywords", []):
                            cat["keywords"].append(kw)
                            report["keywords_added"] += 1
                            index_changed[rel] = True
        if not found:
            report["warnings"].append(f"KEYWORDS 的分類 id 不存在：{cid}")

    today = datetime.now().strftime("%Y-%m-%d")
    for rel, index in ((kb_rel, kb_index), (exp_rel, exp_index)):
        if index_changed[rel]:
            index["lastUpdated"] = today
            store.set(rel, json.dumps(index, ensure_ascii=False, indent=2) + "\n")

    # ---- 標記冷庫 reviewed（只改被列出的行，其餘位元組不動） ----
    new_raw = list(raw_lines)
    for line in merged["reviewed"]:
        note = json.loads(raw_lines[line - 1])
        if note.get("quality") != "reviewed":
            note["quality"] = "reviewed"
            ending = raw_lines[line - 1][len(raw_lines[line - 1].rstrip("\r\n")):] or "\n"
            new_raw[line - 1] = json.dumps(note, ensure_ascii=False) + ending
        report["lines_marked"] += 1
    store.set(raw_rel, "".join(new_raw))

    report["files_touched"] = sorted(store.files)
    if not dry_run:
        store.commit(work / "backup")
    return report


def main():
    """CLI：套用精煉草稿。"""
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".deep-memory")
    parser = argparse.ArgumentParser(description="套用冷庫精煉草稿到熱庫並標記 reviewed")
    parser.add_argument("--workspace", default=default_ws, help="deep-memory 工作目錄")
    parser.add_argument("--work-dir", required=True, help="refine_prepare.py 產生的 refine-work/<run-id> 目錄")
    parser.add_argument("--dry-run", action="store_true", help="只驗證並回報，不寫入")
    parser.add_argument("--allow-partial", action="store_true", help="允許 REVIEWED 未涵蓋全部行號（只標已列出者）")
    args = parser.parse_args()
    try:
        report = apply(Path(args.workspace).resolve(), Path(args.work_dir).resolve(),
                       dry_run=args.dry_run, allow_partial=args.allow_partial)
    except ApplyError as e:
        print(f"[ERROR] {e}（未寫入任何檔案）")
        sys.exit(1)
    mode = "預覽（未寫入）" if args.dry_run else "已套用"
    print(f"[OK] {mode}：新增條目 {report['entries_added']}、追加 {report['appends']}、"
          f"新分類 {len(report['new_categories'])}、關鍵字 {report['keywords_added']}、標記 reviewed {report['lines_marked']} 筆")
    for rel in report["files_touched"]:
        print(f"  - {rel}")
    for w in report["warnings"]:
        print(f"[WARN] {w}")
    if not args.dry_run:
        print(f"備份：{Path(args.work_dir).resolve() / 'backup'}")
        print("下一步：執行 update_db.py 並用 search.py 抽查新條目。")


if __name__ == "__main__":
    main()
