# -*- coding: utf-8 -*-
"""
restore.py
從指定的 GitHub 備份 Repo 還原知識庫至本機工作目錄。
步驟：
  1. 確認 gh CLI 已登入
  2. Clone 備份 Repo（若已存在則 pull 更新）
  3. 複製 knowledge-base/ 與 experience/ 至工作目錄
  4. 提示使用者執行 update_db.py 重建本機向量庫
"""
import os
import sys
import argparse
import subprocess
import shutil

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def run(cmd, cwd=None, check=True):
    """執行系統指令並回傳輸出，失敗時可選擇是否中止"""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=cwd
    )
    if check and result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}")
        print(result.stderr.strip())
        sys.exit(1)
    return result.stdout.strip()


def check_gh_cli():
    """確認 gh CLI 已安裝並已登入"""
    result = subprocess.run("gh --version", shell=True, capture_output=True)
    if result.returncode != 0:
        print("[ERROR] GitHub CLI (gh) 未安裝。")
        print("  請至 https://cli.github.com/ 下載並安裝，再執行 `gh auth login`。")
        sys.exit(1)

    result2 = subprocess.run("gh auth status", shell=True, capture_output=True)
    if result2.returncode != 0:
        print("[ERROR] 尚未登入 GitHub CLI，請先執行 `gh auth login`。")
        sys.exit(1)


def get_repo_url(repo_name: str) -> str:
    """取得完整的 GitHub Repo HTTPS URL"""
    user = run("gh api user --jq .login")
    return f"https://github.com/{user}/{repo_name}.git", user


def clone_or_pull(repo_url: str, clone_dir: str):
    """若 Repo 尚未 Clone 則 Clone，已存在則 pull 最新"""
    if os.path.exists(os.path.join(clone_dir, ".git")):
        print(f"[INFO] Repo 已存在於 {clone_dir}，執行 git pull 更新...")
        run("git pull origin main", cwd=clone_dir)
        print("[OK] 已更新至最新版本。")
    else:
        print(f"[INFO] Clone backup repo from {repo_url}...")
        os.makedirs(clone_dir, exist_ok=True)
        # Clone 到指定目錄
        parent = os.path.dirname(clone_dir)
        dir_name = os.path.basename(clone_dir)
        run(f'git clone "{repo_url}" "{dir_name}"', cwd=parent)
        print(f"[OK] Clone 完成至 {clone_dir}")


def restore_folder(src_folder: str, dest_folder: str, overwrite: bool):
    """
    將備份資料夾複製至工作目錄。
    overwrite=True：完全覆蓋；False：僅補入不存在的檔案（安全模式）
    """
    if not os.path.exists(src_folder):
        print(f"[WARN] 備份中未找到 {os.path.basename(src_folder)}，略過。")
        return

    if overwrite and os.path.exists(dest_folder):
        shutil.rmtree(dest_folder)

    if not os.path.exists(dest_folder):
        shutil.copytree(src_folder, dest_folder)
        print(f"[OK] 還原 {os.path.basename(dest_folder)}/ 完成（全新安裝）。")
        return

    # 安全模式：逐檔補入，不覆蓋既有檔案
    copied = 0
    for fname in os.listdir(src_folder):
        src_file = os.path.join(src_folder, fname)
        dest_file = os.path.join(dest_folder, fname)
        if not os.path.exists(dest_file):
            shutil.copy2(src_file, dest_file)
            copied += 1
    print(f"[OK] 安全補入 {os.path.basename(dest_folder)}/：新增 {copied} 個檔案（已存在的檔案未覆蓋）。")


def main():
    parser = argparse.ArgumentParser(description="Restore knowledge base from GitHub backup repo")
    # 優先從環境變數 DEEP_MEMORY_WORKSPACE 取得工作目錄，否則預設為使用者家目錄下的 .deep-memory
    default_ws = os.environ.get("DEEP_MEMORY_WORKSPACE")
    if not default_ws:
        default_ws = os.path.join(os.path.expanduser("~"), ".deep-memory")

    parser.add_argument("--workspace", type=str, default=default_ws,
                        help="工作目錄（知識庫還原的目標根目錄），預設為全域目錄")
    # 固定預設值需與 backup.py 一致，否則還原時抓錯 repo
    parser.add_argument("--repo", type=str, default="deep-memory-knowledge",
                        help="GitHub backup repo 名稱（預設：deep-memory-knowledge）")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆蓋模式：完全取代本機的 knowledge-base/ 與 experience/。預設為安全模式（僅補入缺少的檔案）")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.workspace)
    # 備份 Repo 暫存在工作目錄下的 .backup_clone/ 目錄
    clone_dir = os.path.join(base_dir, ".backup_clone")

    # Step 1：確認 gh CLI
    print("[Step 1] 確認 GitHub CLI 狀態...")
    check_gh_cli()

    # Step 2：取得 Repo URL 並 Clone / Pull
    repo_url, user = get_repo_url(args.repo)
    print(f"[Step 2] 同步備份 Repo：{repo_url}")
    clone_or_pull(repo_url, clone_dir)

    # Step 3：還原 knowledge-base/ 和 experience/
    print(f"[Step 3] 還原知識庫至 {base_dir}...")
    mode = "覆蓋模式" if args.overwrite else "安全模式（僅補入缺少的檔案）"
    print(f"  [模式] {mode}")

    # 備份 Repo 的內容存放在 clone_dir/knowledge-base 與 clone_dir/experience
    restore_folder(
        src_folder=os.path.join(clone_dir, "knowledge-base"),
        dest_folder=os.path.join(base_dir, "knowledge-base"),
        overwrite=args.overwrite
    )
    restore_folder(
        src_folder=os.path.join(clone_dir, "experience"),
        dest_folder=os.path.join(base_dir, "experience"),
        overwrite=args.overwrite
    )

    # Step 4：提示重建向量庫
    print("\n✅ 知識庫還原完成！")
    print("=" * 60)
    print("【下一步】請執行以下指令重建本機 ChromaDB 向量庫：")
    print()
    print("  Windows:      .venv\\Scripts\\python skills/chroma-hybrid-search/scripts/update_db.py")
    print("  Linux/macOS:  .venv/bin/python skills/chroma-hybrid-search/scripts/update_db.py")
    print()
    print("（若尚未安裝 .venv，請先參考 chroma-hybrid-search/SKILL.md 完成環境安裝）")
    print("=" * 60)


if __name__ == "__main__":
    main()
