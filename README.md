# Architect

A context graph tool for application architecture. Visualize pages, APIs, database tables, and services as interactive graphs -- with rich context (requirements, decisions, notes) on every node. Built for team collaboration with deep Cursor AI integration by Hashif Habeeb.



## How It Works

**You just talk to Cursor.** The AI handles everything behind the scenes.

| You say... | Cursor does... |
|---|---|
| "Initialize the project" | Sets up architecture tracking, asks for project name |
| "Scan the project" | Analyzes your codebase, builds the full architecture graph |
| "Plan a new app for..." | Asks you about pages, APIs, DB -- creates the full graph |
| "Show me the graph" | Opens an interactive visual graph in your browser |
| "What's the status?" | Summarizes progress across all nodes |
| "Add a note to the login page" | Appends your comment to the right node |
| "Mark the dashboard as done" | Updates the node status |
| *(you write code)* | Silently updates context in the background |

You never need to remember a single command. Cursor reads the architecture rule and acts as your intelligent assistant.

## Installation

### Option A -- Standalone executable (no Python required)

Download the pre-built binary for your platform from the [GitHub Releases](../../releases/latest) page:

| Platform | File to download |
|----------|-----------------|
| Windows  | `architect-windows.exe` |
| macOS    | `architect-macos` |
| Linux    | `architect-linux` |

**Windows** -- download `architect-windows.exe`, then either:
```powershell
# Run from where you downloaded it
.\architect-windows.exe --help

# Or add it to PATH permanently (run as Administrator)
Move-Item .\architect-windows.exe C:\Windows\System32\architect.exe
architect --help
```

**macOS / Linux** -- download the binary, then:
```bash
chmod +x architect-macos        # or architect-linux

# Run directly
./architect-macos --help

# Or add to PATH for global access
sudo mv architect-macos /usr/local/bin/architect
architect --help
```

**After installing**, open any project in Cursor and run:
```bash
architect init --name "My Project" --description "Description here"
```
This creates `.architect/` and installs the Cursor rule automatically. You can also install just the Cursor rule without initializing:
```bash
architect install-rules
```

### Option B -- From source (requires Python 3.10+)

```bash
git clone <this-repo>
cd Architect
pip install -e .
python -m architect --help
```

### Option C -- Build your own executable

If you want to build the binary yourself (e.g. on an unsupported platform):

```bash
git clone <this-repo>
cd Architect
pip install -e .
python build.py
# Output: dist/architect  (or dist/architect.exe on Windows)
```

## Quick Start (with Cursor)

1. **Open any project in Cursor**
2. **Say:** "Initialize this project for architecture tracking"
3. **Say:** "Scan the project" -- Cursor auto-detects your tech stack and builds the graph
4. **Say:** "Show me the graph" -- opens the interactive viewer in your browser
5. **Start coding** -- Cursor silently keeps the architecture graph updated

That's it. No commands to memorize.

## What Happens Automatically

When the Cursor rule is active (`.cursor/rules/architect.mdc`):

- **Before implementing a feature**: Cursor checks if there's an existing node with requirements/context and follows them
- **After every code change**: Cursor silently updates the node's implementation notes
- **New files**: Cursor auto-creates graph nodes for files it creates
- **Status tracking**: Cursor marks nodes as in-progress when you start and implemented when you finish
- **Suggestions**: Cursor proactively suggests scanning, viewing, or updating the graph

## The Visual Viewer

Run `architect view` (or just say "show me the graph"):

- **Interactive graph** with zoom, pan, and click-to-inspect
- **Layer toggles** -- show/hide frontend, backend, database, shared
- **Color-coded nodes** -- pages (blue), APIs (green), DB tables (orange), services (purple)
- **Status indicators** -- planned (dashed), in-progress (bold), implemented (solid)
- **Detail panel** -- click any node to see its full context, requirements, and comments
- **Inline editing** -- edit context, metadata, and comments directly in the browser
- **Search** -- filter nodes by name, type, or tags

## Collaboration Workflow

1. **Architect** creates the plan: "Plan a project for an e-commerce platform"
   - Cursor creates nodes for pages, APIs, tables, and services with requirements
2. **Developer A** opens the project, says "What should I work on?"
   - Cursor lists planned nodes and their context
3. **Developer A** clicks a node (e.g., "Product Catalog API") and sees requirements
   - Starts implementing -- Cursor auto-tracks progress
4. **Developer B** opens the same project later
   - Says "What's the status?" -- sees what's done and what's left
   - Picks up a planned node with full context

All context lives in `.architect/` -- commit it to git and everyone shares the architecture.

## Data Storage

```
.architect/
  config.json          # Project metadata
  graph.json           # All nodes and edges
  contexts/
    <node-id>.md       # Rich context per node (markdown)
```

Context files are plain markdown -- human-readable, git-diff friendly, and editable in any tool.

## Node Types

| Type | Description | Viewer shape |
|------|-------------|--------------|
| `page` | A page or route | Circle (blue) |
| `component` | UI component | Circle (cyan) |
| `api_endpoint` | API route/handler | Diamond (green) |
| `service` | Business logic | Hexagon (purple) |
| `db_table` | Database table | Barrel (orange) |
| `middleware` | Middleware | Triangle (red) |
| `utility` | Utility/helper | Circle (gray) |
| `config` | Configuration | Circle (yellow) |

## Supported Scanners

- **JavaScript/TypeScript**: Next.js (pages + app router), Express routes, React components, Prisma schemas, Sequelize/TypeORM models
- **General**: File structure analysis, import-based dependency detection

## CLI Reference (for advanced users)

The CLI exists if you prefer direct commands, but Cursor handles all of this for you.

If installed as a standalone binary, replace `python -m architect` with just `architect`:

| Command | Description |
|---------|-------------|
| `architect init` | Initialize architecture tracking |
| `architect install-rules` | Install/update Cursor rule in current project |
| `architect scan` | Scan codebase and build graph |
| `architect view` | Open interactive web viewer |
| `architect status` | Show graph statistics |
| `architect plan` | Interactive plan creation |
| `architect find-node <file>` | Find node for a file |
| `architect comment <id>` | Add a comment to a node |
| `architect export` | Export graph as JSON or DOT |
| `architect node list/add/edit/remove` | Manage nodes |
| `architect edge add/list/remove` | Manage connections |
| `architect context get/set/append` | Manage node context |

> If using the Python source install, prefix every command with `python -m `: e.g. `python -m architect init`

## Architecture

```
src/architect/
  cli.py                  # Typer CLI commands
  models.py               # Pydantic data models
  storage.py              # .architect/ file I/O
  scanner/
    detector.py           # Project type detection
    general.py            # General file scanner
    javascript.py         # JS/TS framework scanner
  viewer/
    server.py             # FastAPI + REST API
    static/               # Web viewer (Cytoscape.js)
  cursor/
    rules.py              # Cursor rule generator
```

Author - Hashif Habeeb
