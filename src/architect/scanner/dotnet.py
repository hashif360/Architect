"""ASP.NET / C# / .NET project scanner with framework detection."""

from __future__ import annotations

import re
from pathlib import Path

from rich.console import Console

from ..models import Edge, EdgeType, GraphData, Layer, Node, NodeType
from .base import BaseScanner

console = Console()

# ASP.NET controller route attributes
CONTROLLER_ROUTE_PATTERN = re.compile(
    r'''\[(?:Http(?:Get|Post|Put|Delete|Patch)|Route)\s*\(\s*['"]([^'"]*)['"]\s*\)\s*\]''',
    re.MULTILINE,
)

CONTROLLER_CLASS_PATTERN = re.compile(
    r'(?:public\s+)?class\s+(\w+Controller)\s*:\s*(\w+)',
    re.MULTILINE,
)

# Razor page model
RAZOR_PAGE_MODEL_PATTERN = re.compile(
    r'(?:public\s+)?class\s+(\w+Model)\s*:\s*PageModel',
    re.MULTILINE,
)

# Blazor component
BLAZOR_PAGE_DIRECTIVE = re.compile(r'^@page\s+["\']?(/[^\s"\']*)["\']?', re.MULTILINE)

# Entity Framework DbContext and DbSet
DBCONTEXT_PATTERN = re.compile(
    r'(?:public\s+)?class\s+(\w+)\s*:\s*DbContext',
    re.MULTILINE,
)
DBSET_PATTERN = re.compile(
    r'DbSet\s*<\s*(\w+)\s*>\s+(\w+)',
)

# DI / constructor injection
CONSTRUCTOR_INJECT_PATTERN = re.compile(
    r'''(?:private|readonly)\s+(?:readonly\s+)?(?:I)?(\w+(?:Service|Repository|Manager|Handler|Provider|Factory|Client))\s+_\w+'''
)

# C# using statements
USING_PATTERN = re.compile(r'^using\s+([\w.]+)\s*;', re.MULTILINE)

# Middleware registration
MIDDLEWARE_USE_PATTERN = re.compile(r'app\.Use(\w+)\s*\(')

# C# class pattern for generic scanning
CSHARP_CLASS_PATTERN = re.compile(
    r'(?:public|internal|private)?\s*(?:static\s+)?(?:abstract\s+)?(?:partial\s+)?class\s+(\w+)',
    re.MULTILINE,
)


class DotNetScanner(BaseScanner):
    name = "dotnet"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.framework: str = "generic"

    def scan(self) -> GraphData:
        graph = GraphData()
        self._detect_framework()
        console.print(f"[cyan]Detected .NET framework:[/cyan] {self.framework}")

        if self.framework == "aspnet-webapi":
            self._scan_controllers(graph)
        elif self.framework == "aspnet-razor":
            self._scan_razor_pages(graph)
        elif self.framework == "blazor":
            self._scan_blazor(graph)

        if self.framework != "generic":
            self._scan_controllers(graph)

        self._scan_ef_models(graph)
        self._scan_services(graph)
        self._scan_middleware(graph)
        self._scan_generic_cs(graph)
        self._infer_di_edges(graph)

        return graph

    def _detect_framework(self) -> None:
        csproj_files = self._find_files("*.csproj")
        program_cs = self.root / "Program.cs"
        startup_cs = self.root / "Startup.cs"

        all_text = ""
        for f in csproj_files:
            try:
                all_text += f.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                pass

        for f in [program_cs, startup_cs]:
            if f.exists():
                try:
                    all_text += f.read_text(encoding="utf-8", errors="ignore").lower()
                except Exception:
                    pass

        if "microsoft.aspnetcore.components" in all_text or self._find_files("*.razor"):
            self.framework = "blazor"
        elif "addrazorpages" in all_text or self._find_files("*.cshtml"):
            self.framework = "aspnet-razor"
        elif "addcontrollers" in all_text or "addmvc" in all_text:
            self.framework = "aspnet-webapi"
        elif any("microsoft.aspnetcore" in all_text for _ in [1]):
            self.framework = "aspnet-webapi"

    # ── Controllers (Web API / MVC) ──────────────────────────────────

    def _scan_controllers(self, graph: GraphData) -> None:
        controller_dirs = [
            self.root / "Controllers",
            self.root / "controllers",
        ]
        controller_files: list[Path] = []
        for d in controller_dirs:
            if d.is_dir():
                controller_files.extend(self._find_files("*.cs", root_dir=d))

        for f in self._find_files("*Controller.cs"):
            if f not in controller_files:
                controller_files.append(f)

        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for f in sorted(set(controller_files)):
            rel = self.relative(f)
            if rel in existing:
                continue

            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for match in CONTROLLER_CLASS_PATTERN.finditer(content):
                ctrl_name = match.group(1)
                routes = CONTROLLER_ROUTE_PATTERN.findall(content)
                route_summary = ", ".join(routes[:5]) if routes else "controller"

                node = Node(
                    name=ctrl_name,
                    type=NodeType.API_ENDPOINT,
                    layer=Layer.BACKEND,
                    summary=f"ASP.NET Controller: {ctrl_name} ({route_summary})",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                existing.add(rel)
                count += 1

        console.print(f"  Found {count} controllers")

    # ── Razor Pages ──────────────────────────────────────────────────

    def _scan_razor_pages(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Razor Pages...[/cyan]")
        cshtml_files = self._find_files("*.cshtml")
        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for f in cshtml_files:
            rel = self.relative(f)
            if rel in existing:
                continue
            if f.name.startswith("_"):
                continue

            node = Node(
                name=f"Page: {f.stem}",
                type=NodeType.PAGE,
                layer=Layer.FRONTEND,
                summary=f"Razor Page: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            existing.add(rel)
            count += 1

            code_behind = f.with_suffix(".cshtml.cs")
            if code_behind.exists():
                cb_rel = self.relative(code_behind)
                if cb_rel not in existing:
                    cb_node = Node(
                        name=f"PageModel: {f.stem}",
                        type=NodeType.API_ENDPOINT,
                        layer=Layer.BACKEND,
                        summary=f"Razor PageModel: {cb_rel}",
                        file_path=cb_rel,
                    )
                    graph.nodes.append(cb_node)
                    self.storage.create_default_context(cb_node)
                    existing.add(cb_rel)
                    graph.edges.append(Edge(
                        source=node.id,
                        target=cb_node.id,
                        label="code-behind",
                        type=EdgeType.RENDERS,
                    ))

        console.print(f"  Found {count} Razor pages")

    # ── Blazor Components ────────────────────────────────────────────

    def _scan_blazor(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Blazor components...[/cyan]")
        razor_files = self._find_files("*.razor")
        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for f in razor_files:
            rel = self.relative(f)
            if rel in existing:
                continue

            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                content = ""

            page_match = BLAZOR_PAGE_DIRECTIVE.search(content)
            if page_match:
                route = page_match.group(1)
                node = Node(
                    name=f"Page: {route}",
                    type=NodeType.PAGE,
                    layer=Layer.FRONTEND,
                    summary=f"Blazor page: {route} ({rel})",
                    file_path=rel,
                )
            else:
                node = Node(
                    name=f.stem,
                    type=NodeType.COMPONENT,
                    layer=Layer.FRONTEND,
                    summary=f"Blazor component: {rel}",
                    file_path=rel,
                )

            graph.nodes.append(node)
            self.storage.create_default_context(node)
            existing.add(rel)
            count += 1

        console.print(f"  Found {count} Blazor components")

    # ── Entity Framework Models ──────────────────────────────────────

    def _scan_ef_models(self, graph: GraphData) -> None:
        model_dirs = ["Models", "models", "Entities", "entities", "Data", "data"]
        model_files: list[Path] = []

        for d_name in model_dirs:
            d = self.root / d_name
            if d.is_dir():
                model_files.extend(self._find_files("*.cs", root_dir=d))

        for f in self._find_files("*.cs"):
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if DBCONTEXT_PATTERN.search(content):
                if f not in model_files:
                    model_files.append(f)

        existing = {n.file_path for n in graph.nodes if n.file_path}
        model_nodes: dict[str, str] = {}
        count = 0

        for f in sorted(set(model_files)):
            rel = self.relative(f)

            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for match in DBSET_PATTERN.finditer(content):
                entity_name = match.group(1)
                if entity_name not in model_nodes:
                    node = Node(
                        name=f"Entity: {entity_name}",
                        type=NodeType.DB_TABLE,
                        layer=Layer.DATABASE,
                        summary=f"EF Entity: {entity_name} in {rel}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    model_nodes[entity_name] = node.id
                    self.storage.create_default_context(node)
                    count += 1

            for match in CSHARP_CLASS_PATTERN.finditer(content):
                class_name = match.group(1)
                if class_name.endswith("Context") or class_name in model_nodes:
                    continue
                if rel not in existing and any(d in rel.lower() for d in ["models/", "entities/"]):
                    node = Node(
                        name=f"Entity: {class_name}",
                        type=NodeType.DB_TABLE,
                        layer=Layer.DATABASE,
                        summary=f"Data model: {class_name} in {rel}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    model_nodes[class_name] = node.id
                    self.storage.create_default_context(node)
                    existing.add(rel)
                    count += 1

        if count:
            console.print(f"  Found {count} Entity Framework models")

    # ── Services ─────────────────────────────────────────────────────

    def _scan_services(self, graph: GraphData) -> None:
        service_dirs = ["Services", "services"]
        service_files: list[Path] = []

        for d_name in service_dirs:
            d = self.root / d_name
            if d.is_dir():
                service_files.extend(self._find_files("*.cs", root_dir=d))

        for f in self._find_files("*Service.cs"):
            if f not in service_files:
                service_files.append(f)
        for f in self._find_files("*Repository.cs"):
            if f not in service_files:
                service_files.append(f)

        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for f in sorted(set(service_files)):
            rel = self.relative(f)
            if rel in existing:
                continue

            is_interface = f.stem.startswith("I") and f.stem[1:2].isupper()

            node = Node(
                name=f.stem,
                type=NodeType.SERVICE,
                layer=Layer.BACKEND,
                summary=f"{'Interface' if is_interface else 'Service'}: {f.stem} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            existing.add(rel)
            count += 1

        if count:
            console.print(f"  Found {count} services/repositories")

    # ── Middleware ────────────────────────────────────────────────────

    def _scan_middleware(self, graph: GraphData) -> None:
        mw_dirs = ["Middleware", "middleware"]
        mw_files: list[Path] = []

        for d_name in mw_dirs:
            d = self.root / d_name
            if d.is_dir():
                mw_files.extend(self._find_files("*.cs", root_dir=d))

        for f in self._find_files("*Middleware.cs"):
            if f not in mw_files:
                mw_files.append(f)

        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for f in sorted(set(mw_files)):
            rel = self.relative(f)
            if rel in existing:
                continue

            node = Node(
                name=f.stem,
                type=NodeType.MIDDLEWARE,
                layer=Layer.BACKEND,
                summary=f"Middleware: {f.stem} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            existing.add(rel)
            count += 1

        if count:
            console.print(f"  Found {count} middleware")

    # ── Generic C# files ─────────────────────────────────────────────

    def _scan_generic_cs(self, graph: GraphData) -> None:
        existing = {n.file_path for n in graph.nodes if n.file_path}
        cs_files = self._find_files("*.cs")
        count = 0

        for f in cs_files:
            rel = self.relative(f)
            if rel in existing:
                continue

            layer = self._guess_layer(rel)
            node_type = self._guess_type(rel, f)

            node = Node(
                name=f.stem,
                type=node_type,
                layer=layer,
                summary=f"Source: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            existing.add(rel)
            count += 1

        if count:
            console.print(f"  Found {count} additional C# files")

    # ── Dependency injection edges ───────────────────────────────────

    def _infer_di_edges(self, graph: GraphData) -> None:
        node_by_name: dict[str, Node] = {}
        for n in graph.nodes:
            stem = n.name.split("/")[-1].split(".")[0]
            node_by_name[stem.lower()] = n

        existing_pairs: set[tuple[str, str]] = {(e.source, e.target) for e in graph.edges}
        edge_count = 0

        for node in graph.nodes:
            if not node.file_path or not node.file_path.endswith(".cs"):
                continue
            file_path = self.root / node.file_path.replace("/", "\\")
            if not file_path.is_file():
                file_path = self.root / node.file_path
            if not file_path.is_file():
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for match in CONSTRUCTOR_INJECT_PATTERN.finditer(content):
                service_name = match.group(1)
                target = node_by_name.get(service_name.lower())
                if not target:
                    target = node_by_name.get(f"i{service_name.lower()}")
                if target and target.id != node.id and (node.id, target.id) not in existing_pairs:
                    graph.edges.append(Edge(
                        source=node.id,
                        target=target.id,
                        label=f"injects {service_name}",
                        type=EdgeType.DEPENDENCY,
                    ))
                    existing_pairs.add((node.id, target.id))
                    edge_count += 1

        if edge_count:
            console.print(f"  Found {edge_count} DI relationships")

    # ── Helpers ──────────────────────────────────────────────────────

    def _guess_layer(self, rel: str) -> Layer:
        rel_lower = rel.lower()
        if any(p in rel_lower for p in ["pages/", "views/", "components/", "shared/", "wwwroot/", "blazor/"]):
            return Layer.FRONTEND
        if any(p in rel_lower for p in ["models/", "entities/", "data/", "migrations/"]):
            return Layer.DATABASE
        if any(p in rel_lower for p in [
            "controllers/", "api/", "services/", "handlers/", "middleware/",
            "hubs/", "endpoints/",
        ]):
            return Layer.BACKEND
        return Layer.SHARED

    def _guess_type(self, rel: str, file_path: Path) -> NodeType:
        rel_lower = rel.lower()
        stem = file_path.stem

        if "controller" in rel_lower or stem.endswith("Controller"):
            return NodeType.API_ENDPOINT
        if "service" in rel_lower or stem.endswith("Service"):
            return NodeType.SERVICE
        if "repository" in rel_lower or stem.endswith("Repository"):
            return NodeType.SERVICE
        if "middleware" in rel_lower or stem.endswith("Middleware"):
            return NodeType.MIDDLEWARE
        if any(p in rel_lower for p in ["models/", "entities/", "data/"]):
            return NodeType.DB_TABLE
        if "hub" in rel_lower or stem.endswith("Hub"):
            return NodeType.SERVICE
        if any(p in rel_lower for p in ["config", "startup", "program"]):
            return NodeType.CONFIG
        if any(p in rel_lower for p in ["pages/", "views/"]):
            return NodeType.PAGE
        return NodeType.UTILITY
