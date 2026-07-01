# -*- coding: utf-8 -*-
"""
backup.py
協助使用者將知識庫（knowledge-base/ + experience/ + chroma_export.jsonl）
備份至指定的私有 GitHub Repository。
步驟：
  1. 執行 export_jsonl.py 匯出 ChromaDB 為 JSONL
  2. 若遠端 Repo 不存在，用 gh CLI 建立私有 Repo
  3. 在 backup/ 目錄下初始化 git（若未初始化）並設定 remote
  4. git add → commit → push
"""
import os
import sys
import argparse
import subprocess
import shutil
from datetime import datetime

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
        print(result.stderr)
        sys.exit(1)
    return result.stdout.strip()

def check_gh_cli():
    """確認 gh CLI 已安裝"""
    result = subprocess.run("gh --version", shell=True, capture_output=True)
    if result.returncode != 0:
        print("[ERROR] GitHub CLI (gh) 未安裝。")
        print("  請至 https://cli.github.com/ 下載並安裝，再執行 `gh auth login`。")
        sys.exit(1)

def ensure_remote_repo(repo_name: str, visibility: str):
    """若遠端 Repo 不存在則建立，若已存在則直接返回其 URL"""
    # 取得目前登入的 GitHub 帳號
    user = run("gh api user --jq .login")
    remote_url = f"https://github.com/{user}/{repo_name}.git"

    # 檢查 repo 是否已存在
    result = subprocess.run(
        f"gh repo view {user}/{repo_name}", shell=True, capture_output=True
    )
    if result.returncode == 0:
        print(f"[OK] Remote repo already exists: {remote_url}")
    else:
        print(f"[INFO] Creating remote repo: {user}/{repo_name} ({visibility})...")
        run(f'gh repo create {repo_name} --{visibility} --description "Deep-Memory knowledge base backup" --confirm', check=False)
        # 新版 gh CLI 已移除 --confirm 旗標（呼叫會直接報錯，check=False 讓它安靜失敗）；
        # 新版在名稱／可見度等必要參數齊全時本就不會互動詢問，因此重試時直接省略該旗標即可
        result2 = subprocess.run(
            f"gh repo view {user}/{repo_name}", shell=True, capture_output=True
        )
        if result2.returncode != 0:
            run(f'gh repo create {user}/{repo_name} --{visibility} --description "Deep-Memory knowledge base backup"')
        print(f"[OK] Created remote repo: {remote_url}")

    return remote_url

def copy_knowledge_to_backup(base_dir: str, backup_dir: str):
    """複製 knowledge-base/ 與 experience/ 至 backup 目錄"""
    for folder in ["knowledge-base", "experience"]:
        src = os.path.join(base_dir, folder)
        dest = os.path.join(backup_dir, folder)
        if os.path.exists(src):
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            print(f"[OK] Copied {folder}/ to backup/")
        else:
            print(f"[WARN] {folder}/ not found at {src}, skipping.")

def main():
    parser = argparse.ArgumentParser(description="Backup knowledge base to GitHub")
    parser.add_argument("--workspace", type=str, default=os.getcwd(), help="Workspace root path")
    # 固定預設 repo 名稱：多數使用者永遠不用打 --repo，天然避免跨裝置/跨時間打錯字造成備份分裂成兩個倉庫；
    # 仍保留覆蓋能力給真的需要自訂名稱的情境
    parser.add_argument("--repo", type=str, default="deep-memory-knowledge", help="GitHub repo name (default: deep-memory-knowledge)")
    parser.add_argument("--visibility", type=str, default="private",
                        choices=["private", "public"], help="Repo visibility")
    parser.add_argument("--message", type=str, default="", help="Custom commit message")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.workspace)
    backup_dir = os.path.join(base_dir, "backup")
    os.makedirs(backup_dir, exist_ok=True)

    # 取得技能目錄（此腳本所在位置的上兩層 → skills/memory-backup/scripts/ 的上上層）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    export_script = os.path.join(script_dir, "export_jsonl.py")
    venv_python = os.path.join(base_dir, ".venv", "Scripts", "python")
    if not os.path.exists(venv_python + ".exe"):
        venv_python = sys.executable

    # Step 1：匯出 ChromaDB → JSONL
    print("[Step 1] Exporting ChromaDB to JSONL...")
    run(f'"{venv_python}" "{export_script}" --workspace "{base_dir}" --output "backup/chroma_export.jsonl"', cwd=base_dir)

    # Step 2：複製 knowledge-base/ 與 experience/ 至 backup/
    print("[Step 2] Copying knowledge files to backup/...")
    copy_knowledge_to_backup(base_dir, backup_dir)

    # Step 3：確認 gh CLI 並建立遠端 Repo（若不存在）
    print("[Step 3] Checking GitHub CLI and remote repo...")
    check_gh_cli()
    remote_url = ensure_remote_repo(args.repo, args.visibility)

    # Step 4：在 backup/ 目錄下初始化 git 並推送
    print("[Step 4] Committing and pushing to GitHub...")

    # 初始化 git（若尚未初始化）
    if not os.path.exists(os.path.join(backup_dir, ".git")):
        run("git init", cwd=backup_dir)
        run("git branch -M main", cwd=backup_dir)
        run(f'git remote add origin "{remote_url}"', cwd=backup_dir)
    else:
        # 若 remote 已設定則跳過；若未設定則補上
        remotes = run("git remote", cwd=backup_dir, check=False)
        if "origin" not in remotes:
            run(f'git remote add origin "{remote_url}"', cwd=backup_dir)

    # 建立 .gitignore 避免意外 commit 大型二進位檔
    gitignore_path = os.path.join(backup_dir, ".gitignore")
    if not os.path.exists(gitignore_path):
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write("# Ignore ChromaDB binary\nchroma_hybrid_db/\n.venv/\n")

    commit_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = args.message if args.message else f"chore: knowledge backup {commit_time}"

    run("git add .", cwd=backup_dir)
    result = run("git status --short", cwd=backup_dir, check=False)
    if not result.strip():
        print("[INFO] Nothing to commit, backup is up to date.")
        return

    run(f'git commit -m "{commit_msg}"', cwd=backup_dir)

    # 安全推送：先抓遠端，偵測遠端是否有本機沒有的提交（可能來自其他裝置），
    # 若領先則中止並警告，避免 --force 無聲覆蓋其他裝置的備份歷史。
    run("git fetch origin main", cwd=backup_dir, check=False)
    ahead = run("git rev-list --count HEAD..origin/main", cwd=backup_dir, check=False)
    if ahead.strip().isdigit() and int(ahead.strip()) > 0:
        print("[WARN] 遠端備份含有本機沒有的提交（可能來自其他裝置或重建過 backup/）。")
        print("       為避免覆蓋他人備份，已停止推送。")
        print("       請先執行 restore.py 取回遠端內容並整合後，再重新備份：")
        print(f"         python skills/memory-backup/scripts/restore.py --repo {args.repo}")
        sys.exit(1)

    # 遠端落後或一致 → 安全快進推送（不使用 --force）
    push_result = subprocess.run(
        "git push -u origin main", shell=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace", cwd=backup_dir
    )
    if push_result.returncode != 0:
        print("[ERROR] 推送失敗。")
        print(push_result.stderr)
        print("  若 backup/ 曾被刪除重建，本機歷史可能與遠端不相干（unrelated histories）。")
        print(f"  解法：先執行 restore.py --overwrite 取回遠端歷史後再備份：")
        print(f"    python skills/memory-backup/scripts/restore.py --repo {args.repo} --overwrite")
        sys.exit(1)

    print(f"\n✅ Backup complete! Remote: {remote_url}")

if __name__ == "__main__":
    main()
