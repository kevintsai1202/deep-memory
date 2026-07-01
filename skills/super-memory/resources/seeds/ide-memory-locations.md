# AI Coding Tool Memory Location Reference

> **Source:** Claude Code official documentation (claude.com), OpenAI Codex CLI official documentation (openai.com), Antigravity hands-on verification, Cursor official documentation (cursor.com)

---

## 🔧 Claude Code Memory Location (Anthropic)

**Date:** 2025-07-01
**Context:** When you need to read, back up, or integrate Claude Code's auto-memory
**Best Practices:**

### Auto-Memory — **this is the actual "memory"**
```
~/.claude/projects/<project-hash>/memory/
```
- `<project-hash>` is automatically derived from the Git repo path
- All worktrees of the same repo share the same memory directory
- Windows path: `%USERPROFILE%\.claude\projects\<project-hash>\memory\`
- The location can be changed via `autoMemoryDirectory` in `settings.json`
- To disable: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, or `"autoMemoryEnabled": false` in settings.json

### Other persistent files (rules/instructions, not memory)
| File | Purpose |
|---|---|
| `~/.claude/CLAUDE.md` | Global user preferences (applied to all projects) |
| `./CLAUDE.md` or `./.claude/CLAUDE.md` | Project-level instructions |
| `./.claude/rules/*.md` | Modular topic-specific rules |

---

## 🔧 OpenAI Codex CLI Memory Location

**Date:** 2025-07-01
**Context:** When you need to read, back up, or integrate Codex CLI's memory
**Best Practices:**

### Memories directory — **this is the actual "memory"**
```
~/.codex/memories/
```
- Stores AI-generated conversation summaries and persistent context
- Windows path: `%USERPROFILE%\.codex\memories\`
- The root directory can be overridden via the `CODEX_HOME` environment variable

### Other data locations
| Path | Content |
|---|---|
| `~/.codex/config.toml` | Global settings |
| `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | Conversation session logs (JSONL) |
| `~/.codex/history.jsonl` | Command history |
| `~/.codex/logs/codex-tui.log` | Execution logs |
| `AGENTS.md` / `AGENTS.override.md` | Global instructions (not memory) |

---

## 🔧 Antigravity (Google DeepMind) Memory Location

**Date:** 2025-07-01
**Context:** When you need to read, back up, or integrate Antigravity's Knowledge Items
**Best Practices:**

### Knowledge Items (KI) — **this is the actual "memory"**
```
%APPDATA%\antigravity-ide\knowledge\
```
- Full Windows path: `C:\Users\<username>\AppData\Roaming\antigravity-ide\knowledge\` (or `~/.gemini/antigravity-ide/knowledge/`)
- Each KI is a subdirectory containing a `metadata.json` file and an `artifacts/` folder
- KIs are automatically distilled by the AI from conversations and persist across conversations

### Conversation Brain (Artifacts & Logs)
```
~/.gemini/antigravity-ide/brain/<conversation-id>/
```
- Each conversation has its own brain directory
- Contains planning documents such as `walkthrough.md`, `implementation_plan.md`, and `task.md`
- `.system_generated/logs/transcript.jsonl` — the full conversation transcript

### Other configuration locations
| Path | Content |
|---|---|
| `~/.gemini/config/AGENTS.md` | Global rules (applied to all conversations) |
| `~/.gemini/config/skills/` | Global skills directory |
| `<project>/.agents/AGENTS.md` | Project-level rules |
| `<project>/.agents/skills/` | Project skills directory |

---

## 🔧 Cursor IDE Memory Location

**Date:** 2025-07-01
**Context:** When you need to read or back up Cursor's conversation history
**Best Practices:**

### Conversation history (SQLite) — **this is the actual "memory"**
```
# Windows
%APPDATA%\Cursor\User\workspaceStorage\<workspace-hash>\state.vscdb

# macOS
~/Library/Application Support/Cursor/User/workspaceStorage/<workspace-hash>/state.vscdb

# Linux
~/.config/Cursor/User/workspaceStorage/<workspace-hash>/state.vscdb
```
- `<workspace-hash>` is generated from the project folder path; moving or renaming the project will make history appear to "disappear" (the data still exists, but the hash no longer matches)
- Global metadata: `globalStorage/state.vscdb`
- The format is SQLite and can be opened with DB Browser for SQLite

---

## 🔧 Integration Strategy: Importing Cross-Tool Memory into super-memory

**Date:** 2025-07-01
**Context:** Integrating each tool's raw memory data into the super-memory knowledge base
**Best Practices:**

| Tool | Memory Format | Import Strategy |
|---|---|---|
| Claude Code | Plain-text MD | Read the `memory/` directory directly; manually curate before writing to the hot or cold store |
| Codex CLI | Plain text / JSON | Read the `memories/` directory; filter entry by entry and write to the cold-store JSONL |
| Antigravity | JSON (KI metadata + artifacts) | Read the KIs in the `knowledge/` directory; use them directly as hot-store seeds |
| Cursor | SQLite (state.vscdb) | Requires export via DB Browser or a script, then manual curation before writing to the cold store |

> **Recommended priority order**: Antigravity KI > Claude Code memory > Codex memories > Cursor (highest export cost)
