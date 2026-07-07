# Recording Criteria & Entry Format

> Loaded on demand by deep-memory/SKILL.md Step 5 when actually writing a knowledge-base or experience entry. Not needed for the core per-turn loop.

## Recording Criteria

**Step 1 — Which bucket?** One litmus test: does this only make sense in the context of a specific skill (`skill-id`)? → `experience`. Does it generalize across skills/domains, with no dependency on any particular skill being invoked? → `knowledge-base`. If one solved event contains both a general rule and a skill-specific tactic, mark the cold note as `both` and split it into two hot-store entries during refinement. (Example: a Spring Boot testing gotcha that only applies while using a specific testing skill is `experience`; the same lesson restated as a general JUnit/Spring pattern that holds regardless of which skill is active is `knowledge-base`.)

### Cold Store `memory_type`

Every cold-store write should include a `memory_type` so later refinement can route entries correctly:

| memory_type | Use when | Hot-store target |
|---|---|---|
| `knowledge` | Reusable facts, project rules, architecture decisions, SOPs, domain rules, or user preferences | `knowledge-base/[category].md` |
| `experience` | A reproducible lesson from a specific skill/tool/repo, including pitfalls, exact errors, commands, parameters, and validation evidence | `experience/skill-[skill-id].md` |
| `both` | The same event contains a general rule and a skill-specific workflow | Split into one `knowledge-base` entry and one `experience` entry |

**Step 2 — Is it worth recording at all?** Core question: Will this save the user time next time?

### General Knowledge (knowledge-base)

**Should record:**
- ✅ Reusable workflows and decision steps (cross-domain operation sequences / decision trees)
- ✅ High-cost mistakes and correction paths (errors that waste significant time)
- ✅ Key parameters / configuration / prerequisites (elements that affect outcomes when changed)
- ✅ User preferences and style rules (tone, format, design style, output structure)
- ✅ Solutions that only succeeded after multiple attempts (including failure reasons and success conditions)
- ✅ Reusable templates / checklists / formats (output styles used repeatedly)
- ✅ External dependency or resource locations (file paths, tools, assets)

**Should NOT record:**
- ❌ One-off Q&A with no reusable workflow
- ❌ Pure conceptual explanations (no concrete steps or decision criteria)
- ❌ Conclusions without specific context that cannot be reproduced

### Experience (non-deep-memory skills)

**Should record:**
- ✅ Pitfalls and solutions encountered while using the skill (including error messages / how to locate them)
- ✅ Key parameters or configurations that affect results (e.g., spring params, fps, duration)
- ✅ Reusable templates / prompts / workflows (directly applicable)
- ✅ Dependency and asset paths (fonts, images, project entry points, module locations)
- ✅ Steps that require a specific order or technique to succeed (e.g., initialize before overriding)

**Should NOT record:**
- ❌ Pure theoretical or conceptual explanations (keep in knowledge-base instead)
- ❌ Conclusions without reproducible steps
- ❌ One-off, non-reusable operations

---

## Entry Format

### knowledge-base Entry Format
```markdown
## 🔧 [Short title]
**Date:** YYYY-MM-DD
**Context:** One sentence describing the use case
**Best Practices:**
- [Key point 1]
- [Key point 2] — parameter notes and tuning guide
```

### experience Entry Format
```markdown
## 🔧 [Problem / technique title]
**Date:** YYYY-MM-DD
**Skill:** [skill-id]
**Context:** One sentence describing the problem this turn
**Solution:**
- Concrete step 1
- Concrete step 2
**Key Files / Paths:**
- /path/to/file
**keywords:** keyword1, keyword2, keyword3
```
