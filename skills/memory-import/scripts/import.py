# -*- coding: utf-8 -*-
"""
import.py
Imports memory data from external systems (ChatGPT memory export, Claude Code
local auto-memory files, legacy auto-skill projects) into deep-memory's
cold store (cold-notes/raw.jsonl) or hot store (knowledge-base/).
"""
import os
import sys
import re
import json
import shutil
import hashlib
import argparse
from datetime import datetime

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def parse_chatgpt(input_path):
    raise NotImplementedError


def write_to_cold(texts, workspace, dry_run):
    raise NotImplementedError


def parse_claude_local(input_dir):
    raise NotImplementedError


def write_claude_local_to_hot(memories, workspace, dry_run):
    raise NotImplementedError


def merge_autoskill(input_dir, workspace, force, dry_run):
    raise NotImplementedError


def main():
    parser = argparse.ArgumentParser(
        description="Import memory data from external systems into deep-memory"
    )
    parser.add_argument("--source", type=str, required=True,
                        choices=["chatgpt", "claude-local", "autoskill"],
                        help="External memory source type")
    parser.add_argument("--input", type=str, required=True,
                        help="Path to the source file (chatgpt) or directory (claude-local, autoskill)")
    parser.add_argument("--workspace", type=str, default=os.getcwd(),
                        help="Workspace root path (parent of knowledge-base/, cold-notes/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be imported without writing any files")
    parser.add_argument("--force", action="store_true",
                        help="autoskill only: overwrite categories that already exist locally")
    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace)
    input_path = os.path.abspath(args.input)

    if not os.path.exists(input_path):
        print(f"[ERROR] 輸入路徑不存在：{input_path}")
        sys.exit(1)

    if args.source == "chatgpt":
        texts = parse_chatgpt(input_path)
        write_to_cold(texts, workspace, args.dry_run)
    elif args.source == "claude-local":
        memories = parse_claude_local(input_path)
        write_claude_local_to_hot(memories, workspace, args.dry_run)
    elif args.source == "autoskill":
        merge_autoskill(input_path, workspace, args.force, args.dry_run)


if __name__ == "__main__":
    main()
