"""Generate IDE rules for Architect integration in target projects (Cursor + VS Code)."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

# ── Cursor rule (MDC format with frontmatter) ─────────────────────

ARCHITECT_RULE = '''---
description: Architect - Intelligent project context graph integration
globs: ["**/*"]
alwaysApply: true
---

# Architect Context Graph Tool -- AI Integration

You have access to the **Architect** CLI which maintains a living architecture graph for this project. Your job is to use it **automatically and silently** so the user never needs to know CLI commands. You are the intelligent layer on top of this tool.

## COMMAND DETECTION (DO THIS FIRST)

Before running any architect command, determine which invocation works in this environment:

1. Try running `architect --help`
2. If that fails, try `python -m architect --help`
3. Use whichever succeeds as the command prefix for ALL subsequent commands.

In the examples below, `architect` is used as the command. If only `python -m architect` works in this environment, substitute that everywhere.

## CORE PRINCIPLE

The user should NEVER have to type or remember architect commands. You detect intent, suggest actions, confirm with the user in plain English, and execute everything yourself.

---

## 1. AUTOMATIC INITIALIZATION

**When there is NO `.architect/` directory in the project root:**

If the user says anything like "initialize", "set up architect", "start tracking", "set up the project", or you detect this is a new project without architecture tracking:

- Ask the user: "I can set up architecture tracking for this project. What\'s the project name and a brief description?"
- Once confirmed, run:
  ```
  architect init --name "<name>" --description "<description>"
  ```
- Then tell the user: "Architecture tracking is set up. I\'ll keep the context graph updated as we work."

## 2. AUTOMATIC SCANNING

**When the user says anything like:**
- "scan the project", "analyze the codebase", "map out the project"
- "what does this project look like", "show me the structure"
- "understand this codebase", "figure out what\'s here"

**Do this:**
1. Check if `.architect/` exists. If not, initialize first (ask for project name).
2. Run: `architect scan`
   - Use `architect scan --git-changed` for incremental scans (only re-scans files changed in git -- much faster for large projects).
   - Use `architect scan --auto-remove` to automatically remove stale nodes whose files no longer exist.
3. Then run: `architect status`
4. Summarize what was found in plain English: "I scanned the project and found X pages, Y API endpoints, Z database tables..."
5. Suggest: "Want me to open the visual graph in your browser? Or should I refine any of the discovered nodes?"

## 3. AUTOMATIC PLANNING

**When the user says anything like:**
- "plan a project", "let\'s architect this", "design the app"
- "I want to build...", "create a plan for...", "map out..."

**Do this:**
1. Discuss the architecture with the user -- ask about pages, APIs, database needs, services
2. Initialize if needed: `architect init --name "<name>" --description "<desc>"`
3. For EACH component discussed, automatically create nodes:
   ```
   architect node add --name "<name>" --type <type> --layer <layer> --summary "<summary>"
   ```
4. Create edges between related nodes:
   ```
   architect edge add <source-id> <target-id> --label "<relationship>" --type <edge-type>
   ```
5. For important nodes, set detailed context:
   ```
   architect context set <node-id> --text "<full markdown context>"
   ```
6. After creating the plan, summarize what was created and suggest viewing the graph.

**Node types you can use:** page, component, api_endpoint, service, db_table, db_column, middleware, utility, config, custom
**Layers:** frontend, backend, database, shared
**Edge types:** dependency, data_flow, foreign_key, renders, calls, inherits
**Node statuses:** planned, in_progress, implemented, needs_review, deprecated

## 4. AUTO-CONTEXT UPDATES AFTER CODE CHANGES (CRITICAL)

**After EVERY code change you make in agent mode, you MUST do this silently:**

1. Run `architect find-node "<relative/path/of/changed/file>"` to find the associated node
2. If a node exists, update its context:
   ```
   architect context append <node-id> --section "Implementation Notes" --text "<what you changed and why>"
   ```
   And update the node status if appropriate:
   ```
   architect node edit <node-id> --status in_progress
   ```
3. If NO node exists for the file, create one:
   ```
   architect node add --name "<descriptive name>" --type <type> --layer <layer> --file "<relative/path>" --summary "<what this file does>"
   ```
   Then set initial context:
   ```
   architect context append <node-id> --section "Implementation Notes" --text "<what was done>"
   ```

**Do this silently** -- don\'t ask the user, just do it after your code changes. This is background bookkeeping.

## 5. VIEWING THE GRAPH

**When the user says anything like:**
- "show me the graph", "view architecture", "open the viewer"
- "let me see the big picture", "show the diagram"
- "visualize", "open architect"

**Run:** `architect view`
**Tell the user:** "Opening the architecture graph in your browser. You can zoom, click nodes for details, and edit context directly in the viewer. The viewer supports live file tracking -- when you enable Live mode, the graph updates in real time as files change."

## 6. BEFORE IMPLEMENTING A FEATURE

**When the user asks you to implement something that matches a known node:**

1. First, silently run `architect node list` to check if there\'s an existing node for it
2. If found, run `architect context get <node-id>` to read the requirements and context
3. Tell the user: "I found existing context for this feature -- here\'s what was planned: [summary]. I\'ll implement it based on these requirements."
4. Mark the node as in-progress: `architect node edit <node-id> --status in_progress`
5. After implementation, mark it: `architect node edit <node-id> --status implemented`

## 7. SOFT-DELETE vs HARD-DELETE (IMPORTANT)

Nodes and edges have statuses. When deleting, use the correct approach:

- **Planned nodes** (no code exists): use `architect node remove <id>` for a hard delete.
- **Implemented / in-progress / needs-review nodes** (code exists): use `architect node edit <id> --status deprecated` for a soft delete (ghost). The node remains visible in the graph with a faded style, and connected edges are also marked deprecated.
- **Restoring a deprecated node:** use `architect node edit <id> --status planned` (or `in_progress`, `implemented`, etc.) to bring it back.
- **Edge deletion** follows the same logic: remove planned-only edges with `architect edge remove <id>`, soft-delete edges connected to existing code with the viewer UI.

Always confirm destructive (hard) deletes with the user.

## 8. COMMENTING AND COLLABORATION

**When the user says anything like:**
- "add a note to...", "leave a comment on...", "note that..."
- "remind the team that...", "document that..."

**Run:**
```
architect comment <node-id> --author "<user-name-if-known>" --text "<their comment>"
```

If the user hasn\'t specified which node, list the nodes and ask which one they mean.

## 9. IMPACT ANALYSIS

**When the user asks anything like:**
- "what would be affected if I change X", "impact of changing X"
- "what depends on X", "ripple effect"

**Run:** `architect impact <node-id-or-name>` (optionally with `--depth N` or `--forward`)
**Summarize** the impacted nodes and the depth of impact.

## 10. STATUS AND PROGRESS

**When the user asks anything like:**
- "what\'s the status", "how far along are we", "project progress"
- "what\'s been done", "what\'s left"

**Run:** `architect status`
**Then run:** `architect node list`
**Summarize** in plain English: "Here\'s where we stand: X nodes are implemented, Y are in progress, Z are still planned..."

## 11. ARCHITECTURE DECISION RECORDS (ADRs)

**When the user says anything like:**
- "record a decision", "add an ADR", "document why we chose X"
- "list decisions", "show ADRs"

**Use:**
- `architect adr add` -- create a new ADR (interactive prompts for title, context, decision, status)
- `architect adr list` -- list all ADRs (optionally `--status accepted`)
- `architect adr show <id>` -- show an ADR\'s full content
- `architect adr edit <id>` -- update an ADR
- `architect adr remove <id>` -- delete an ADR

## 12. GIT INTEGRATION

**Use these when the user asks about git history, branches, or change tracking:**

- `architect git snapshot` -- save the current graph state for the active branch
- `architect git branches` -- list saved branch snapshots
- `architect git restore <branch>` -- restore a graph snapshot from another branch
- `architect git blame <node-id>` -- enrich a node with git blame info (last author, commit frequency)
- `architect git changed` -- show files changed in git (optionally `--since <ref>`)

## 13. MONOREPO / WORKSPACE SUPPORT

**When the user works in a monorepo or asks about workspaces:**

- `architect workspace add` -- register a new workspace
- `architect workspace list` -- list registered workspaces
- `architect workspace scan [name]` -- scan a specific workspace (or all)
- `architect workspace remove <name>` -- unregister a workspace

Nodes can be filtered by workspace: `architect node list --workspace <name>`

## 14. EDGE MANAGEMENT

**Full edge lifecycle:**

- `architect edge add <source-id> <target-id> --label "<label>" --type <type>` -- create an edge
- `architect edge list` -- list all edges
- `architect edge remove <edge-id>` -- remove an edge (confirm with user first)

## 15. SUGGESTING ACTIONS (PROACTIVE)

Be proactive. When appropriate, suggest:
- "I notice this project doesn\'t have architecture tracking yet. Want me to scan it and create a context graph?"
- "I just finished implementing the Dashboard page. Want me to update its status to \'implemented\' in the architecture graph?"
- "There are 3 planned nodes that haven\'t been started yet. Want to pick one to work on next?"
- "Want me to open the visual graph so you can see the full picture?"
- "This node has code but is still marked \'planned\'. Should I update it to \'implemented\'?"
- "Want me to run an incremental scan (`--git-changed`) to pick up your recent changes?"

## 16. NATURAL LANGUAGE INTENT MAPPING

Here is how to map casual user language to actions:

| User says something like... | You do... |
|---|---|
| "set up the project" / "initialize" | `architect init` (ask for name first) |
| "scan" / "analyze" / "map out" | `architect scan` + summarize |
| "quick scan" / "rescan changes" | `architect scan --git-changed` |
| "plan" / "design" / "architect" | Interactive planning, create nodes/edges |
| "show graph" / "visualize" / "big picture" | `architect view` |
| "what\'s the status" / "progress" | `architect status` + `node list` + summarize |
| "add a page/api/table for X" | `architect node add` with right type/layer |
| "connect X to Y" / "X depends on Y" | `architect edge add` |
| "show edges" / "list relationships" | `architect edge list` |
| "remove edge" / "disconnect X from Y" | `architect edge remove` |
| "what\'s the context for X" | `architect context get` |
| "mark X as done" / "X is finished" | `architect node edit --status implemented` |
| "deprecate X" / "soft delete X" | `architect node edit --status deprecated` |
| "restore X" / "bring back X" | `architect node edit --status planned` (or appropriate status) |
| "what would break if I change X" | `architect impact` |
| "add a note" / "comment" | `architect comment` |
| "export" / "give me the architecture" | `architect export` |
| "record a decision" / "ADR" | `architect adr add` |
| "show decisions" | `architect adr list` |
| "save branch state" | `architect git snapshot` |
| "what changed in git" | `architect git changed` |
| "list workspaces" | `architect workspace list` |
| Working on a feature matching a node | Auto-read context first, auto-update after |

---

## REMEMBER

- The user should feel like they are just talking to an intelligent architect assistant
- YOU handle all the CLI commands behind the scenes
- Always confirm destructive actions (hard-deleting nodes/edges) with the user
- For implemented nodes, prefer soft-delete (`--status deprecated`) over hard-delete
- After creating/scanning, always offer to show the visual graph
- Keep context updates silent -- they are background bookkeeping
- When in doubt, run `architect node list` to see what exists
- Use `--git-changed` for fast incremental scans on large projects
'''


def _strip_mdc_frontmatter(text: str) -> str:
    """Remove the MDC YAML frontmatter (--- ... ---) from the rule content."""
    stripped = text.strip()
    if stripped.startswith("---"):
        end = stripped.find("---", 3)
        if end != -1:
            return stripped[end + 3:].strip()
    return stripped


ARCHITECT_RULE_MD = _strip_mdc_frontmatter(ARCHITECT_RULE)

_SECTION_START = "<!-- ARCHITECT-CONTEXT-GRAPH-START -->"
_SECTION_END = "<!-- ARCHITECT-CONTEXT-GRAPH-END -->"


def _is_frozen() -> bool:
    """Return True if running inside a PyInstaller-frozen executable."""
    return getattr(sys, "frozen", False)


def install_cursor_rule(project_root: Path) -> Path:
    """Install the Architect rule in a project's .cursor/rules/ directory."""
    rules_dir = project_root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / "architect.mdc"
    rule_path.write_text(ARCHITECT_RULE.strip() + "\n", encoding="utf-8")
    return rule_path


def install_vscode_rule(project_root: Path) -> Path:
    """Install the Architect rule as GitHub Copilot instructions for VS Code.

    Creates/updates .github/copilot-instructions.md with the Architect
    integration instructions wrapped in section markers so repeated
    installs update the section in-place.
    """
    github_dir = project_root / ".github"
    github_dir.mkdir(parents=True, exist_ok=True)
    instructions_path = github_dir / "copilot-instructions.md"

    section = f"{_SECTION_START}\n{ARCHITECT_RULE_MD}\n{_SECTION_END}"

    if instructions_path.exists():
        existing = instructions_path.read_text(encoding="utf-8")
        if _SECTION_START in existing:
            existing = re.sub(
                re.escape(_SECTION_START) + r".*?" + re.escape(_SECTION_END),
                section,
                existing,
                flags=re.DOTALL,
            )
            instructions_path.write_text(existing, encoding="utf-8")
        else:
            instructions_path.write_text(
                existing.rstrip() + "\n\n" + section + "\n",
                encoding="utf-8",
            )
    else:
        instructions_path.write_text(section + "\n", encoding="utf-8")

    return instructions_path


def install_all_rules(project_root: Path) -> list[Path]:
    """Install rules for all supported IDEs (Cursor + VS Code)."""
    return [
        install_cursor_rule(project_root),
        install_vscode_rule(project_root),
    ]
