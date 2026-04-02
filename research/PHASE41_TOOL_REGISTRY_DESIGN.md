# Phase 41: Tool Registry + Function Calling — Design Document

**Research date:** 2026-04-01
**Status:** Pre-implementation — read before building
**Source:** Claude Code source architecture analysis (claw-code, official docs, k1rallik thread)

---

## The Core Insight

Claude Code gates tools at **prompt composition time**, not at call time. A tool that doesn't appear in the system prompt cannot be invoked by the model — there's nothing to hallucinate. poe-orchestration currently does runtime role filtering; Phase 41 moves this to context-build time.

---

## 1. Tool Registration: Declarative Object Model

**Claude Code pattern:**

Every tool is a declarative object conforming to `Tool<Input, Output, Progress>`:
- `inputSchema` — Zod schema (validation + introspection; auto-generates JSON Schema)
- `execute(input)` — actual implementation
- `isEnabled(context)` — feature flag gate
- `renderProgress()` / `renderResult()` — UI rendering
- Permission metadata

**Assembly pipeline:**
```python
def get_tools(permission_context):
    all_tools = load_all_tools_from_registry()          # 1. Load everything
    allowed  = filter_by_deny_rules(all_tools, ctx)     # 2. Strip denied BEFORE model sees
    enabled  = [t for t in allowed if t.is_enabled()]   # 3. Strip by feature flag
    return sorted(enabled, key=lambda t: t.name)        # 4. Deterministic order (cache)
```

**For Phase 41:**
- `ToolRegistry` class with `register(tool_def)` and `get_tools(permission_context) -> List[ToolDef]`
- Each tool: `name`, `description`, `input_schema` (JSON Schema), `roles_allowed`, `is_enabled()`
- Filter at `_build_loop_context()` time, not in `step_exec.py` at execution time

---

## 2. Role-Based Tool Gating

**Claude Code uses a `PermissionContext` object threaded through the call stack:**

```python
@dataclass
class PermissionContext:
    mode: str           # "plan" | "auto" | "ask" | "bypassPermissions"
    deny_rules: List[str]   # e.g. ["Bash(rm -rf *)", "Edit(/etc/*)"]
    allow_auto_approve: bool
```

**Deny rule syntax:** `ToolName(glob_pattern)` — e.g. `Bash(git push*)` blocks git push.

**Key rule:** Filtering runs at prompt composition, not at execution. Denied tools never appear in system prompt.

**For Phase 41:**
```python
@dataclass
class PermissionContext:
    role: str           # "director" | "worker" | "verifier" | "inspector"
    deny_patterns: List[str]

    def allows(self, tool_name: str) -> bool: ...
```

Tool sets by role (current state in `step_exec.py` — formalize as registry):
- `director` — planning tools only; no shell exec
- `worker` — full EXECUTE_TOOLS set
- `verifier` — read + analysis only
- `inspector` — read-only

---

## 3. Skill Progressive Disclosure

**Claude Code's lazy loading pattern:**

Initial context contains **only** name + description (cheap). Full skill definition loads **only** when model decides to invoke.

```
system_prompt:
  - skill: "code-review" | "Reviews code for best practices"  ← tiny

[model invokes code-review]
  → load_full_skill_definition("code-review")               ← full prompt appended
```

**Token impact:** Prevents context bloat with large skill sets. 85% token reduction on deferred MCP tools.

**For Phase 41:**
- Skills directory: `skills/` with per-skill `SKILL.md` (YAML frontmatter + content)
- Frontmatter: `name`, `description`, `roles_allowed`, `triggers` (keyword list)
- `SkillLoader.load_summaries()` → list of (name, description) for prompt
- `SkillLoader.load_full(name)` → invoked on skill match
- Current `inject_skills_context()` in `_build_loop_context()` already does something like this — formalize with per-skill files

**SKILL.md format:**
```markdown
---
name: polymarket-research
description: Research Polymarket contracts, prices, and trader behavior
roles_allowed: [worker, director]
triggers: [polymarket, prediction market, contract]
---

When researching Polymarket:
1. Use polymarket-cli to fetch current contracts
2. Check top wallets via the wallet analysis scripts
...
```

---

## 4. Hook Lifecycle Architecture

**Claude Code: 25+ events, event-matcher-handler pipelines**

```
SETUP: Init, Maintenance
SESSION: SessionStart, UserPromptSubmit, SessionEnd
TOOL CYCLE:
  PreToolUse    [BLOCKING — can deny, modify input, or defer]
  PermissionRequest
  [Tool Executes]
  PostToolUse   [non-blocking feedback]
  PostToolUseFailure
CONVERSATION: CwdChanged, FileChanged, Notification
```

**Hook handler types:**
```json
{"type": "command", "command": "./validate.sh", "timeout": 30}
{"type": "http",    "url": "http://localhost:8080/hooks"}
{"type": "prompt",  "prompt": "Is this safe? $ARGUMENTS"}
{"type": "agent",   "prompt": "Verify these files are safe to edit"}
```

**Matcher syntax:** `"Bash|Edit"`, `"mcp__memory__.*"`, `"Edit(*.py)"`

**PreToolUse exit codes:**
- `0` → allow (parse JSON output for `permissionDecision`)
- `2` → deny (stderr is user feedback)
- other → warning, continue

**Current poe-orchestration hooks** (`src/hooks.py`): simpler — step-level callbacks, no event types, no matchers. Phase 41 should add:
- `PreStepExecution` (blocking) — constraint check lives here
- `PostStepExecution` (non-blocking) — checkpoint write, metric logging
- `SessionStart` / `SessionEnd` — memory injection / recording
- Matcher pattern on tool name / step type

---

## 5. Function Calling: Schema Management

**Current poe-orchestration:** Tools defined as Python strings in `EXECUTE_TOOLS` lists; schemas are inline JSON in system prompt text.

**Claude Code approach:**
- Zod schemas → auto-generate JSON Schema for API transmission
- `shouldDefer: true` marks tools for lazy loading
- Discovery via `ToolSearchTool` (semantic search, not grep)
- Prefix cache preserved by deterministic tool ordering

**For Phase 41:**
```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict          # JSON Schema
    roles_allowed: List[str]
    is_enabled: Callable[[], bool] = lambda: True
    should_defer: bool = False  # deferred = only name+desc in prompt initially
```

`ToolRegistry.generate_tool_block(permission_context)` → formats tool list for system prompt insertion. Tools with `should_defer=True` get stub entries; full schema loads on invocation.

---

## 6. Gap Table: Current State vs Target

| Aspect | Current poe-orchestration | Phase 41 target |
|--------|--------------------------|-----------------|
| Tool registration | Imperative lists in step_exec.py | Declarative `ToolDefinition` objects in registry |
| Visibility gating | Runtime, per-step | Prompt composition time (before model sees) |
| Role model | 3 hardcoded tool lists | `PermissionContext` with deny patterns |
| Skills | Python dataclasses in skills.json | `SKILL.md` files with YAML frontmatter |
| Skill loading | All summaries injected upfront | Progressive: summaries first, full on invoke |
| Hook lifecycle | Flat step callbacks | Event-matcher-handler pipelines |
| Schema | Inline strings in prompts | JSON Schema per tool, auto-generated |
| MCP | Not implemented | Deferred tools via ToolRegistry |

---

## 7. Implementation Order

1. **`ToolDefinition` + `ToolRegistry`** — base data model; migrate `EXECUTE_TOOLS` lists into it
2. **`PermissionContext`** — replaces implicit role param; filter at `_build_loop_context()` time
3. **`SKILL.md` format** — migrate `skills.json` entries to per-file YAML frontmatter; `SkillLoader`
4. **Progressive skill disclosure** — load summaries early, full definition on match
5. **Hook event model** — add `PreStepExecution` / `PostStepExecution` events with matchers
6. **Deferred tools** — `should_defer=True` stubs in prompt; `ToolSearch` on invocation
7. **MCP integration** — external tool servers as deferred registry entries

**Read before starting each step:** The Claude Code public source at github.com/anthropics/claude-code. The claw-code Python reimplementation (instructkr/claw-code) has the structural skeleton without production code.

---

## 8. References

- github.com/anthropics/claude-code — public TypeScript source
- github.com/instructkr/claw-code — Python clean-room reimplementation
- Claude Code docs: hooks reference, plugin creation
- WaveSpeedAI architecture deep dive
- Sathwick reverse-engineering blog post
- leehanchung Claude agent skills deep dive
