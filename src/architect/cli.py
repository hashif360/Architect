"""Architect CLI - Application architecture visualization and context graph tool."""

from __future__ import annotations

import json as json_module
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.tree import Tree

from .models import (
    ADR,
    ADRStatus,
    Edge,
    EdgeType,
    GraphData,
    Layer,
    Node,
    NodeStatus,
    NodeType,
)
from .storage import Storage

app = typer.Typer(
    name="architect",
    help="Application architecture visualization and context graph tool.",
    no_args_is_help=True,
)
node_app = typer.Typer(help="Manage graph nodes.", no_args_is_help=True)
edge_app = typer.Typer(help="Manage graph edges.", no_args_is_help=True)
context_app = typer.Typer(help="Manage node context documents.", no_args_is_help=True)
adr_app = typer.Typer(help="Manage Architecture Decision Records.", no_args_is_help=True)
git_app = typer.Typer(help="Git integration commands.", no_args_is_help=True)
workspace_app = typer.Typer(help="Manage monorepo workspaces.", no_args_is_help=True)

app.add_typer(node_app, name="node")
app.add_typer(edge_app, name="edge")
app.add_typer(context_app, name="context")
app.add_typer(adr_app, name="adr")
app.add_typer(git_app, name="git")
app.add_typer(workspace_app, name="workspace")

console = Console()


def _get_storage() -> Storage:
    return Storage(Path.cwd())


def _require_init(storage: Storage) -> None:
    if not storage.is_initialized:
        console.print(
            "[red]Error:[/red] Not an Architect project. Run [bold]architect init[/bold] first."
        )
        raise typer.Exit(1)


# ── Top-level commands ──────────────────────────────────────────────


@app.command()
def init(
    name: Optional[str] = typer.Option(None, help="Project name"),
    description: str = typer.Option("", help="Project description"),
) -> None:
    """Initialize .architect/ in the current project."""
    storage = _get_storage()
    if storage.is_initialized:
        console.print("[yellow]Already initialized.[/yellow]")
        from .cursor.rules import install_all_rules
        paths = install_all_rules(storage.project_root)
        for p in paths:
            console.print(f"[green]Rule updated:[/green] {p}")
        raise typer.Exit(0)
    storage.initialize(name=name or "", description=description)

    from .cursor.rules import install_all_rules
    paths = install_all_rules(storage.project_root)

    console.print(
        f"[green]Initialized Architect project:[/green] {storage.architect_dir}"
    )
    for p in paths:
        console.print(f"[green]Installed rule:[/green] {p}")


@app.command("install-rules")
def install_rules(
    cursor_only: bool = typer.Option(False, "--cursor-only", help="Only install Cursor rule"),
    vscode_only: bool = typer.Option(False, "--vscode-only", help="Only install VS Code / Copilot rule"),
) -> None:
    """Install or update the Architect AI rules for Cursor and VS Code.

    Creates .cursor/rules/architect.mdc (Cursor) and
    .github/copilot-instructions.md (VS Code / GitHub Copilot).
    Works whether or not .architect/ has been initialized -- safe to run anytime.
    """
    from .cursor.rules import install_all_rules, install_cursor_rule, install_vscode_rule

    project_root = Path.cwd()

    if vscode_only:
        path = install_vscode_rule(project_root)
        console.print(f"[green]VS Code rule installed:[/green] {path}")
    elif cursor_only:
        path = install_cursor_rule(project_root)
        console.print(f"[green]Cursor rule installed:[/green] {path}")
    else:
        paths = install_all_rules(project_root)
        for p in paths:
            console.print(f"[green]Rule installed:[/green] {p}")

    if not (project_root / ".architect").is_dir():
        console.print(
            "[dim]Tip: Run [bold]architect init[/bold] to initialize architecture tracking.[/dim]"
        )


@app.command()
def status() -> None:
    """Show graph statistics."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()
    config = storage.load_config()

    console.print(Panel(f"[bold]{config.name}[/bold]\n{config.description}", title="Project"))

    table = Table(title="Nodes by Layer")
    table.add_column("Layer", style="cyan")
    table.add_column("Count", justify="right")
    for layer in Layer:
        count = len([n for n in graph.nodes if n.layer == layer])
        if count:
            table.add_row(layer.value, str(count))
    console.print(table)

    table = Table(title="Nodes by Type")
    table.add_column("Type", style="green")
    table.add_column("Count", justify="right")
    for nt in NodeType:
        count = len([n for n in graph.nodes if n.type == nt])
        if count:
            table.add_row(nt.value, str(count))
    console.print(table)

    table = Table(title="Nodes by Status")
    table.add_column("Status", style="magenta")
    table.add_column("Count", justify="right")
    for s in NodeStatus:
        count = len([n for n in graph.nodes if n.status == s])
        if count:
            table.add_row(s.value, str(count))
    console.print(table)

    stale = [n for n in graph.nodes if "stale" in n.tags]
    if stale:
        console.print(f"\n[yellow]Stale nodes:[/yellow] {len(stale)} (files no longer found)")

    workspaces = graph.workspaces()
    if workspaces:
        console.print(f"Workspaces: {', '.join(workspaces)}")

    adrs = storage.list_adrs()
    if adrs:
        console.print(f"ADRs: {len(adrs)}")

    console.print(f"\nTotal nodes: [bold]{len(graph.nodes)}[/bold]  |  Total edges: [bold]{len(graph.edges)}[/bold]")


@app.command()
def view(
    port: int = typer.Option(8742, help="Port for the web viewer"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open the browser automatically"),
) -> None:
    """Launch the web viewer in the browser."""
    storage = _get_storage()
    _require_init(storage)
    from .viewer.server import run_server

    run_server(storage, port=port, open_browser=not no_browser)


@app.command()
def scan(
    path: Optional[str] = typer.Argument(None, help="Path to scan (defaults to current directory)"),
    auto_remove: bool = typer.Option(False, "--auto-remove", help="Auto-remove stale nodes"),
    no_diff: bool = typer.Option(False, "--no-diff", help="Use legacy merge (no diff)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Assign nodes to this workspace"),
    git_only: bool = typer.Option(False, "--git-changed", help="Only scan files changed in git"),
    no_db: bool = typer.Option(False, "--no-db", help="Skip database credential discovery and introspection"),
    no_git: bool = typer.Option(False, "--no-git", help="Skip git history analysis (hotspots and co-change edges)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Manual database connection URL (overrides auto-discovery)"),
    no_lighthouse: bool = typer.Option(False, "--no-lighthouse", help="Skip Lighthouse performance auditing"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Dev server URL for Lighthouse (e.g. http://localhost:3000)"),
) -> None:
    """Scan the codebase and generate/update the architecture graph."""
    storage = _get_storage()
    _require_init(storage)
    scan_path = Path(path) if path else storage.project_root

    from .scanner.detector import detect_and_scan

    changed = None
    if git_only:
        from .git_integration import scan_changed_files_only
        changed = scan_changed_files_only(storage)
        if changed:
            console.print(f"[cyan]Git-changed files:[/cyan] {len(changed)}")
            for f in changed[:20]:
                console.print(f"  {f}")
            if len(changed) > 20:
                console.print(f"  ... and {len(changed) - 20} more")
        else:
            console.print("[green]No git-changed files detected. Nothing to scan.[/green]")
            return

    detect_and_scan(
        storage, scan_path,
        auto_remove=auto_remove,
        diff_mode=not no_diff,
        workspace=workspace,
        enable_db=not no_db,
        enable_git=not no_git,
        db_url=db_url,
        changed_files=changed,
        enable_lighthouse=not no_lighthouse,
        lighthouse_base_url=base_url,
    )


@app.command("find-node")
def find_node(
    file_path: str = typer.Argument(..., help="File path to look up"),
) -> None:
    """Find the graph node associated with a file path."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    normalized = file_path.replace("\\", "/")
    node = graph.find_node_by_file(normalized)
    if not node:
        for n in graph.nodes:
            if n.file_path and (normalized.endswith(n.file_path) or n.file_path.endswith(normalized)):
                node = n
                break

    if node:
        console.print(f"[green]Found:[/green] {node.id} | {node.name} ({node.type.value}, {node.layer.value})")
    else:
        console.print(f"[dim]No node found for:[/dim] {file_path}")
        console.print("[dim]Create one with: architect node add --name <name> --file <path>[/dim]")


@app.command()
def plan(
    description: str = typer.Argument(..., help="High-level project description"),
) -> None:
    """Create a plan skeleton as graph nodes for Cursor to populate."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    console.print(f"[bold]Creating plan:[/bold] {description}")

    sections = {
        "frontend": (Layer.FRONTEND, NodeType.PAGE, "pages/views"),
        "backend": (Layer.BACKEND, NodeType.API_ENDPOINT, "API endpoints"),
        "database": (Layer.DATABASE, NodeType.DB_TABLE, "database tables"),
        "services": (Layer.BACKEND, NodeType.SERVICE, "services/logic"),
    }

    for section_name, (layer, node_type, label) in sections.items():
        console.print(f"\n[cyan]Define {label} (enter names, empty line to finish):[/cyan]")
        while True:
            name = typer.prompt(f"  {section_name} node name", default="", show_default=False)
            if not name:
                break
            node = Node(
                name=name,
                type=node_type,
                layer=layer,
                summary=f"Part of plan: {description}",
            )
            graph.nodes.append(node)
            storage.create_default_context(node)
            console.print(f"    [green]+[/green] {node.id}: {name}")

    storage.save_graph(graph)
    console.print(f"\n[green]Plan created with {len(graph.nodes)} nodes.[/green]")
    console.print("Use [bold]architect context set <node-id>[/bold] to add details to each node.")


@app.command("export")
def export_graph(
    format: str = typer.Option("json", help="Export format: json, dot, mermaid, plantuml"),
    output: Optional[str] = typer.Option(None, "-o", help="Output file path"),
    direction: str = typer.Option("TD", "--direction", help="Mermaid direction: TD, LR, BT, RL"),
) -> None:
    """Export the graph in various formats."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    if format == "json":
        data = graph.model_dump(mode="json")
        text = json_module.dumps(data, indent=2, default=str)
    elif format == "dot":
        lines = ["digraph Architect {", '  rankdir=LR;', '  node [shape=box, style=rounded];']
        for node in graph.nodes:
            lines.append(f'  "{node.id}" [label="{node.name}\\n({node.type.value})"];')
        for edge in graph.edges:
            label = f' [label="{edge.label}"]' if edge.label else ""
            lines.append(f'  "{edge.source}" -> "{edge.target}"{label};')
        lines.append("}")
        text = "\n".join(lines)
    elif format == "mermaid":
        from .exporters import to_mermaid
        text = to_mermaid(graph, direction=direction)
    elif format == "plantuml":
        from .exporters import to_plantuml
        text = to_plantuml(graph)
    else:
        console.print(f"[red]Unsupported format:[/red] {format}  (supported: json, dot, mermaid, plantuml)")
        raise typer.Exit(1)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/green]")
    else:
        console.print(text)


@app.command("impact")
def impact_cmd(
    target: str = typer.Argument(..., help="Node ID or file path"),
    max_depth: int = typer.Option(10, "--depth", help="Maximum traversal depth"),
    forward: bool = typer.Option(False, "--forward", help="Include forward dependencies"),
) -> None:
    """Analyze change impact: what nodes are affected if this node changes."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    from .analysis import analyze_impact, analyze_file_impact

    node = graph.get_node(target)
    if node:
        result = analyze_impact(graph, target, max_depth=max_depth, include_forward=forward)
    else:
        result = analyze_file_impact(graph, target, max_depth=max_depth)

    if not result:
        console.print(f"[red]No node found for:[/red] {target}")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]{result.source_node.name}[/bold] ({result.source_node.id})\n"
        f"Type: {result.source_node.type.value}  |  Layer: {result.source_node.layer.value}",
        title="Impact Source",
    ))

    if not result.impacted:
        console.print("[green]No other nodes are impacted by changes to this node.[/green]")
        return

    console.print(f"\n[yellow]Potentially impacted nodes: {result.total_impacted}[/yellow]\n")

    tree = Tree(f"[bold]{result.source_node.name}[/bold]")
    depth_groups = result.by_depth()
    for depth in sorted(depth_groups.keys()):
        branch = tree.add(f"[cyan]Depth {depth}[/cyan]")
        for n in depth_groups[depth]:
            branch.add(f"{n.name} [dim]({n.id}, {n.type.value})[/dim]")

    console.print(tree)


# ── Node sub-commands ───────────────────────────────────────────────


@node_app.command("list")
def node_list(
    layer: Optional[Layer] = typer.Option(None, help="Filter by layer"),
    type: Optional[NodeType] = typer.Option(None, "--type", help="Filter by node type"),
    node_status: Optional[NodeStatus] = typer.Option(None, "--status", help="Filter by status"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Filter by tag"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Filter by workspace"),
) -> None:
    """List all nodes with optional filters."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()
    tags = [tag] if tag else None
    nodes = graph.find_nodes(layer=layer, node_type=type, status=node_status, tags=tags, workspace=workspace)

    if not nodes:
        console.print("[dim]No nodes found.[/dim]")
        return

    table = Table(title=f"Nodes ({len(nodes)})")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Type", style="green")
    table.add_column("Layer", style="cyan")
    table.add_column("Status", style="magenta")
    table.add_column("Tags", style="yellow")
    table.add_column("File", style="dim")
    for n in nodes:
        tags_str = ", ".join(n.tags) if n.tags else ""
        table.add_row(n.id, n.name, n.type.value, n.layer.value, n.status.value, tags_str, n.file_path or "")
    console.print(table)


@node_app.command("add")
def node_add(
    name: str = typer.Option(..., prompt=True, help="Node name"),
    type: NodeType = typer.Option(NodeType.CUSTOM, "--type", help="Node type"),
    layer: Layer = typer.Option(Layer.SHARED, help="Node layer"),
    summary: str = typer.Option("", help="Brief summary"),
    file_path: Optional[str] = typer.Option(None, "--file", help="Associated file path"),
    tags: Optional[str] = typer.Option(None, help="Comma-separated tags"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace name"),
    meta: Optional[str] = typer.Option(None, "--meta", help="JSON metadata string"),
) -> None:
    """Add a new node to the graph."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    metadata = json_module.loads(meta) if meta else {}

    node = Node(
        name=name,
        type=type,
        layer=layer,
        summary=summary,
        file_path=file_path,
        tags=tag_list,
        metadata=metadata,
        workspace=workspace,
    )
    graph.nodes.append(node)
    storage.save_graph(graph)
    storage.create_default_context(node)

    console.print(f"[green]Added node:[/green] {node.id} ({node.name})")


@node_app.command("edit")
def node_edit(
    node_id: str = typer.Argument(..., help="Node ID"),
    name: Optional[str] = typer.Option(None, help="New name"),
    summary: Optional[str] = typer.Option(None, help="New summary"),
    node_status: Optional[NodeStatus] = typer.Option(None, "--status", help="New status"),
    layer: Optional[Layer] = typer.Option(None, help="New layer"),
    file_path: Optional[str] = typer.Option(None, "--file", help="Associated file path"),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags (replaces existing)"),
    add_tag: Optional[str] = typer.Option(None, "--add-tag", help="Add a single tag"),
    remove_tag: Optional[str] = typer.Option(None, "--remove-tag", help="Remove a single tag"),
    meta: Optional[str] = typer.Option(None, "--meta", help="JSON metadata to merge"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace name"),
) -> None:
    """Edit an existing node's metadata."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()
    node = graph.get_node(node_id)

    if not node:
        console.print(f"[red]Node not found:[/red] {node_id}")
        raise typer.Exit(1)

    if name is not None:
        node.name = name
    if summary is not None:
        node.summary = summary
    if node_status is not None:
        node.status = node_status
    if layer is not None:
        node.layer = layer
    if file_path is not None:
        node.file_path = file_path
    if tags is not None:
        node.tags = [t.strip() for t in tags.split(",") if t.strip()]
    if add_tag and add_tag not in node.tags:
        node.tags.append(add_tag)
    if remove_tag and remove_tag in node.tags:
        node.tags.remove(remove_tag)
    if meta is not None:
        node.metadata.update(json_module.loads(meta))
    if workspace is not None:
        node.workspace = workspace

    node.touch()
    storage.save_graph(graph)
    console.print(f"[green]Updated node:[/green] {node.id} ({node.name})")


@node_app.command("remove")
def node_remove(
    node_id: str = typer.Argument(..., help="Node ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a node and its connected edges."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()
    node = graph.get_node(node_id)

    if not node:
        console.print(f"[red]Node not found:[/red] {node_id}")
        raise typer.Exit(1)

    if not force:
        typer.confirm(f"Remove node '{node.name}' ({node_id})?", abort=True)

    graph.remove_node(node_id)
    storage.save_graph(graph)
    storage.delete_context(node_id)
    console.print(f"[green]Removed node:[/green] {node_id}")


# ── Edge sub-commands ───────────────────────────────────────────────


@edge_app.command("add")
def edge_add(
    source: str = typer.Argument(..., help="Source node ID"),
    target: str = typer.Argument(..., help="Target node ID"),
    label: str = typer.Option("", help="Edge label"),
    type: EdgeType = typer.Option(EdgeType.DEPENDENCY, "--type", help="Edge type"),
) -> None:
    """Add a connection between two nodes."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    if not graph.get_node(source):
        console.print(f"[red]Source node not found:[/red] {source}")
        raise typer.Exit(1)
    if not graph.get_node(target):
        console.print(f"[red]Target node not found:[/red] {target}")
        raise typer.Exit(1)

    edge = Edge(source=source, target=target, label=label, type=type)
    graph.edges.append(edge)
    storage.save_graph(graph)
    console.print(f"[green]Added edge:[/green] {edge.id} ({source} -> {target})")


@edge_app.command("list")
def edge_list() -> None:
    """List all edges."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    if not graph.edges:
        console.print("[dim]No edges found.[/dim]")
        return

    table = Table(title=f"Edges ({len(graph.edges)})")
    table.add_column("ID", style="dim")
    table.add_column("Source", style="cyan")
    table.add_column("Target", style="cyan")
    table.add_column("Label")
    table.add_column("Type", style="green")

    for e in graph.edges:
        src_node = graph.get_node(e.source)
        tgt_node = graph.get_node(e.target)
        src_label = f"{src_node.name}" if src_node else e.source
        tgt_label = f"{tgt_node.name}" if tgt_node else e.target
        table.add_row(e.id, src_label, tgt_label, e.label, e.type.value)
    console.print(table)


@edge_app.command("remove")
def edge_remove(
    edge_id: str = typer.Argument(..., help="Edge ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove an edge."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()
    edge = graph.get_edge(edge_id)

    if not edge:
        console.print(f"[red]Edge not found:[/red] {edge_id}")
        raise typer.Exit(1)

    if not force:
        typer.confirm(f"Remove edge {edge_id} ({edge.source} -> {edge.target})?", abort=True)

    graph.remove_edge(edge_id)
    storage.save_graph(graph)
    console.print(f"[green]Removed edge:[/green] {edge_id}")


# ── Context sub-commands ────────────────────────────────────────────


@context_app.command("get")
def context_get(
    node_id: str = typer.Argument(..., help="Node ID"),
) -> None:
    """Display the context document for a node."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()
    node = graph.get_node(node_id)

    if not node:
        console.print(f"[red]Node not found:[/red] {node_id}")
        raise typer.Exit(1)

    content = storage.load_context(node_id)
    if not content:
        console.print(f"[dim]No context for node {node_id}. Use 'architect context set {node_id}' to add one.[/dim]")
        return

    console.print(Panel(Markdown(content), title=f"Context: {node.name} ({node_id})"))


@context_app.command("set")
def context_set(
    node_id: str = typer.Argument(..., help="Node ID"),
    text: Optional[str] = typer.Option(None, "--text", "-t", help="Context content (markdown). If omitted, reads from stdin."),
) -> None:
    """Set/replace the context document for a node."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    if not graph.get_node(node_id):
        console.print(f"[red]Node not found:[/red] {node_id}")
        raise typer.Exit(1)

    if text is None:
        console.print("[dim]Enter context markdown (Ctrl+Z then Enter on Windows, Ctrl+D on Unix to finish):[/dim]")
        text = sys.stdin.read()

    storage.save_context(node_id, text)
    node = graph.get_node(node_id)
    node.touch()
    storage.save_graph(graph)
    console.print(f"[green]Context updated for:[/green] {node_id}")


@context_app.command("append")
def context_append(
    node_id: str = typer.Argument(..., help="Node ID"),
    section: str = typer.Option("Implementation Notes", "--section", "-s", help="Section to append to"),
    text: str = typer.Option(..., "--text", "-t", help="Text to append"),
) -> None:
    """Append text to a section in a node's context document."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    if not graph.get_node(node_id):
        console.print(f"[red]Node not found:[/red] {node_id}")
        raise typer.Exit(1)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    entry = f"- [{timestamp}] {text}"
    storage.append_to_context_section(node_id, section, entry)
    node = graph.get_node(node_id)
    node.touch()
    storage.save_graph(graph)
    console.print(f"[green]Appended to {section} in:[/green] {node_id}")


@app.command()
def comment(
    node_id: str = typer.Argument(..., help="Node ID"),
    text: str = typer.Option(..., "--text", "-t", help="Comment text"),
    author: str = typer.Option("anonymous", "--author", "-a", help="Your name"),
) -> None:
    """Add a comment to a node's context (shortcut for context append)."""
    storage = _get_storage()
    _require_init(storage)
    graph = storage.load_graph()

    if not graph.get_node(node_id):
        console.print(f"[red]Node not found:[/red] {node_id}")
        raise typer.Exit(1)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    entry = f"- [{timestamp} @{author}] {text}"
    storage.append_to_context_section(node_id, "Comments", entry)
    node = graph.get_node(node_id)
    node.touch()
    storage.save_graph(graph)
    console.print(f"[green]Comment added to:[/green] {node.name} ({node_id})")


# ── ADR sub-commands ────────────────────────────────────────────────


@adr_app.command("add")
def adr_add(
    title: str = typer.Option(..., "--title", prompt=True, help="ADR title"),
    adr_status: ADRStatus = typer.Option(ADRStatus.PROPOSED, "--status", help="ADR status"),
    context_text: str = typer.Option("", "--context", help="Context/background"),
    decision: str = typer.Option("", "--decision", help="The decision made"),
    consequences: str = typer.Option("", "--consequences", help="Consequences"),
    link: Optional[str] = typer.Option(None, "--link", help="Comma-separated node IDs to link"),
) -> None:
    """Add an Architecture Decision Record."""
    storage = _get_storage()
    _require_init(storage)

    linked = [s.strip() for s in link.split(",")] if link else []
    adr = ADR(
        title=title,
        status=adr_status,
        context=context_text,
        decision=decision,
        consequences=consequences,
        linked_nodes=linked,
    )
    storage.save_adr(adr)
    console.print(f"[green]Created ADR:[/green] {adr.id} -- {adr.title}")


@adr_app.command("list")
def adr_list(
    adr_status: Optional[ADRStatus] = typer.Option(None, "--status", help="Filter by status"),
) -> None:
    """List all ADRs."""
    storage = _get_storage()
    _require_init(storage)
    adrs = storage.list_adrs()
    if adr_status:
        adrs = [a for a in adrs if a.status == adr_status]

    if not adrs:
        console.print("[dim]No ADRs found.[/dim]")
        return

    table = Table(title=f"ADRs ({len(adrs)})")
    table.add_column("ID", style="dim")
    table.add_column("Title", style="bold")
    table.add_column("Status", style="magenta")
    table.add_column("Linked Nodes", style="cyan")
    table.add_column("Created", style="dim")
    for a in adrs:
        table.add_row(
            a.id, a.title, a.status.value,
            ", ".join(a.linked_nodes) if a.linked_nodes else "",
            a.created_at.strftime("%Y-%m-%d"),
        )
    console.print(table)


@adr_app.command("show")
def adr_show(
    adr_id: str = typer.Argument(..., help="ADR ID"),
) -> None:
    """Show an ADR in detail."""
    storage = _get_storage()
    _require_init(storage)
    adr = storage.load_adr(adr_id)
    if not adr:
        console.print(f"[red]ADR not found:[/red] {adr_id}")
        raise typer.Exit(1)

    md = f"# {adr.title}\n\n**Status:** {adr.status.value}\n\n"
    if adr.context:
        md += f"## Context\n{adr.context}\n\n"
    if adr.decision:
        md += f"## Decision\n{adr.decision}\n\n"
    if adr.consequences:
        md += f"## Consequences\n{adr.consequences}\n\n"
    if adr.linked_nodes:
        graph = storage.load_graph()
        md += "## Linked Nodes\n"
        for nid in adr.linked_nodes:
            node = graph.get_node(nid)
            label = f"{node.name} ({nid})" if node else nid
            md += f"- {label}\n"

    console.print(Panel(Markdown(md), title=f"ADR: {adr.id}"))


@adr_app.command("edit")
def adr_edit(
    adr_id: str = typer.Argument(..., help="ADR ID"),
    title: Optional[str] = typer.Option(None, "--title", help="New title"),
    adr_status: Optional[ADRStatus] = typer.Option(None, "--status", help="New status"),
    context_text: Optional[str] = typer.Option(None, "--context", help="New context"),
    decision: Optional[str] = typer.Option(None, "--decision", help="New decision"),
    consequences: Optional[str] = typer.Option(None, "--consequences", help="New consequences"),
    link: Optional[str] = typer.Option(None, "--link", help="Comma-separated node IDs to add"),
    unlink: Optional[str] = typer.Option(None, "--unlink", help="Comma-separated node IDs to remove"),
) -> None:
    """Edit an existing ADR."""
    storage = _get_storage()
    _require_init(storage)
    adr = storage.load_adr(adr_id)
    if not adr:
        console.print(f"[red]ADR not found:[/red] {adr_id}")
        raise typer.Exit(1)

    if title is not None:
        adr.title = title
    if adr_status is not None:
        adr.status = adr_status
    if context_text is not None:
        adr.context = context_text
    if decision is not None:
        adr.decision = decision
    if consequences is not None:
        adr.consequences = consequences
    if link:
        for nid in link.split(","):
            nid = nid.strip()
            if nid and nid not in adr.linked_nodes:
                adr.linked_nodes.append(nid)
    if unlink:
        for nid in unlink.split(","):
            nid = nid.strip()
            if nid in adr.linked_nodes:
                adr.linked_nodes.remove(nid)

    adr.touch()
    storage.save_adr(adr)
    console.print(f"[green]Updated ADR:[/green] {adr.id} -- {adr.title}")


@adr_app.command("remove")
def adr_remove(
    adr_id: str = typer.Argument(..., help="ADR ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove an ADR."""
    storage = _get_storage()
    _require_init(storage)
    adr = storage.load_adr(adr_id)
    if not adr:
        console.print(f"[red]ADR not found:[/red] {adr_id}")
        raise typer.Exit(1)

    if not force:
        typer.confirm(f"Remove ADR '{adr.title}' ({adr_id})?", abort=True)

    storage.delete_adr(adr_id)
    console.print(f"[green]Removed ADR:[/green] {adr_id}")


# ── Git sub-commands ────────────────────────────────────────────────


@git_app.command("snapshot")
def git_snapshot() -> None:
    """Save a graph snapshot for the current git branch."""
    storage = _get_storage()
    _require_init(storage)

    from .git_integration import snapshot_branch
    branch = snapshot_branch(storage)
    if branch:
        console.print(f"[green]Snapshot saved for branch:[/green] {branch}")
    else:
        console.print("[yellow]Could not determine git branch.[/yellow]")


@git_app.command("branches")
def git_branches() -> None:
    """List saved branch snapshots."""
    storage = _get_storage()
    _require_init(storage)

    branches = storage.list_branch_snapshots()
    if not branches:
        console.print("[dim]No branch snapshots found.[/dim]")
        return

    for b in branches:
        console.print(f"  {b}")


@git_app.command("restore")
def git_restore(
    branch: str = typer.Argument(..., help="Branch name to restore"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Restore a graph from a branch snapshot."""
    storage = _get_storage()
    _require_init(storage)

    snap = storage.load_branch_snapshot(branch)
    if not snap:
        console.print(f"[red]No snapshot found for branch:[/red] {branch}")
        raise typer.Exit(1)

    if not force:
        typer.confirm(f"Replace current graph with snapshot from '{branch}'?", abort=True)

    storage.save_graph(snap)
    console.print(f"[green]Restored graph from branch:[/green] {branch}")


@git_app.command("blame")
def git_blame(
    node_id: str = typer.Argument(..., help="Node ID to enrich with blame info"),
) -> None:
    """Enrich a node with git blame/author info."""
    storage = _get_storage()
    _require_init(storage)

    from .git_integration import enrich_node_blame
    info = enrich_node_blame(storage, node_id)
    if info:
        console.print(f"[green]Enriched:[/green] last author = {info['author']} ({info['email']})")
    else:
        console.print("[yellow]Could not get blame info (not a git repo or node has no file path).[/yellow]")


@git_app.command("changed")
def git_changed(
    since: Optional[str] = typer.Option(None, help="Git ref to compare against"),
) -> None:
    """Show files changed in git."""
    storage = _get_storage()
    _require_init(storage)

    from .git_integration import get_changed_files
    files = get_changed_files(storage.project_root, since=since)
    if not files:
        console.print("[dim]No changed files found.[/dim]")
        return

    graph = storage.load_graph()
    table = Table(title=f"Changed Files ({len(files)})")
    table.add_column("File", style="cyan")
    table.add_column("Node", style="bold")
    table.add_column("Status", style="magenta")
    for f in files:
        node = graph.find_node_by_file(f)
        table.add_row(f, node.name if node else "[dim]--[/dim]", node.status.value if node else "")
    console.print(table)


# ── Workspace sub-commands (Monorepo) ───────────────────────────────


@workspace_app.command("add")
def workspace_add(
    name: str = typer.Option(..., "--name", prompt=True, help="Workspace name"),
    path: str = typer.Option(..., "--path", prompt=True, help="Relative path"),
) -> None:
    """Register a monorepo workspace/sub-project."""
    storage = _get_storage()
    _require_init(storage)
    config = storage.load_config()
    config.workspaces[name] = path
    storage.save_config(config)
    console.print(f"[green]Added workspace:[/green] {name} -> {path}")


@workspace_app.command("list")
def workspace_list() -> None:
    """List registered workspaces."""
    storage = _get_storage()
    _require_init(storage)
    config = storage.load_config()

    if not config.workspaces:
        console.print("[dim]No workspaces registered.[/dim]")
        return

    graph = storage.load_graph()
    table = Table(title="Workspaces")
    table.add_column("Name", style="bold")
    table.add_column("Path", style="cyan")
    table.add_column("Nodes", justify="right")
    for name, path in config.workspaces.items():
        count = len(graph.find_nodes(workspace=name))
        table.add_row(name, path, str(count))
    console.print(table)


@workspace_app.command("scan")
def workspace_scan(
    name: Optional[str] = typer.Argument(None, help="Workspace to scan (or all if omitted)"),
    auto_remove: bool = typer.Option(False, "--auto-remove", help="Auto-remove stale nodes"),
    no_db: bool = typer.Option(False, "--no-db", help="Skip database credential discovery and introspection"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Manual database connection URL"),
) -> None:
    """Scan one or all workspaces."""
    storage = _get_storage()
    _require_init(storage)
    config = storage.load_config()

    if not config.workspaces:
        console.print("[yellow]No workspaces registered. Use 'architect workspace add' first.[/yellow]")
        return

    from .scanner.detector import detect_and_scan

    targets = {name: config.workspaces[name]} if name else config.workspaces
    for ws_name, ws_path in targets.items():
        full_path = storage.project_root / ws_path
        if not full_path.is_dir():
            console.print(f"[yellow]Workspace path not found:[/yellow] {ws_path}")
            continue
        console.print(f"\n[bold]Scanning workspace:[/bold] {ws_name} ({ws_path})")
        detect_and_scan(
            storage, full_path,
            auto_remove=auto_remove,
            workspace=ws_name,
            enable_db=not no_db,
            db_url=db_url,
        )


@workspace_app.command("remove")
def workspace_remove(
    name: str = typer.Argument(..., help="Workspace name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a workspace registration (nodes are kept)."""
    storage = _get_storage()
    _require_init(storage)
    config = storage.load_config()

    if name not in config.workspaces:
        console.print(f"[red]Workspace not found:[/red] {name}")
        raise typer.Exit(1)

    if not force:
        typer.confirm(f"Remove workspace '{name}'?", abort=True)

    del config.workspaces[name]
    storage.save_config(config)
    console.print(f"[green]Removed workspace:[/green] {name}")


if __name__ == "__main__":
    app()
