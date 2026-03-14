"""JavaScript / TypeScript project scanner with framework detection."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from rich.console import Console

from ..models import Edge, EdgeType, GraphData, Layer, Node, NodeStatus, NodeType
from .base import BaseScanner

console = Console()

JS_IMPORT_PATTERNS = [
    re.compile(r'''from\s+['"]([^'"]+)['"]'''),
    re.compile(r'''require\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
    re.compile(r'''import\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
]


def _strip_json_comments(text: str) -> str:
    """Strip ``//`` and ``/* */`` comments plus trailing commas from JSON."""
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return text


class JavaScriptScanner(BaseScanner):
    name = "javascript"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frameworks: set[str] = set()
        self.pkg: dict = {}
        self._path_aliases: dict[str, str] = {}

    @property
    def framework(self) -> str:
        """Primary framework for backward compatibility."""
        if self.frameworks:
            return next(iter(self.frameworks))
        return "generic"

    def scan(self) -> GraphData:
        graph = GraphData()
        self._detect_framework()
        self._path_aliases = self._load_path_aliases()
        console.print(f"[cyan]Detected framework(s):[/cyan] {', '.join(sorted(self.frameworks))}")

        if self.frameworks & {"nextjs", "nextjs-app"}:
            self._scan_nextjs(graph)
        else:
            has_frontend = False
            if "express" in self.frameworks:
                self._scan_express(graph)
            if "angular" in self.frameworks:
                self._scan_angular(graph)
                has_frontend = True
            if "react" in self.frameworks:
                self._scan_react(graph)
                has_frontend = True
            if self.frameworks & {"vue", "svelte"}:
                self._scan_generic_js(graph)
                has_frontend = True
            if not has_frontend and "express" not in self.frameworks:
                self._scan_generic_js(graph)

        self._scan_prisma(graph)
        self._scan_sequelize_or_typeorm(graph)
        self._scan_components(graph)

        self._infer_import_edges(graph)

        return graph

    def _detect_framework(self) -> None:
        pkg_path = self.root / "package.json"
        if not pkg_path.exists():
            self.frameworks = {"generic"}
            return

        try:
            self.pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except Exception:
            self.frameworks = {"generic"}
            return

        all_deps = {
            **self.pkg.get("dependencies", {}),
            **self.pkg.get("devDependencies", {}),
        }

        detected: set[str] = set()

        if "next" in all_deps:
            app_dir = self.root / "app"
            src_app = self.root / "src" / "app"
            if app_dir.is_dir() or src_app.is_dir():
                detected.add("nextjs-app")
            else:
                detected.add("nextjs")
        else:
            if "express" in all_deps:
                detected.add("express")
            if "@angular/core" in all_deps:
                detected.add("angular")
            if "react" in all_deps:
                detected.add("react")
            if "vue" in all_deps:
                detected.add("vue")
            if "svelte" in all_deps or "@sveltejs/kit" in all_deps:
                detected.add("svelte")

        self.frameworks = detected if detected else {"generic"}

    # ── Path-alias resolution (tsconfig / jsconfig) ───────────────

    def _load_path_aliases(self) -> dict[str, str]:
        """Parse ``compilerOptions.paths`` from tsconfig/jsconfig."""
        for config_name in ("tsconfig.json", "jsconfig.json"):
            config_path = self.root / config_name
            if not config_path.exists():
                continue
            try:
                raw = config_path.read_text(encoding="utf-8")
                config = json.loads(_strip_json_comments(raw))
                compiler_opts = config.get("compilerOptions", {})
                base_url = compiler_opts.get("baseUrl", ".")
                paths = compiler_opts.get("paths", {})

                aliases: dict[str, str] = {}
                for alias, targets in paths.items():
                    if not targets:
                        continue
                    alias_prefix = alias.replace("/*", "/").replace("*", "")
                    target_prefix = targets[0].replace("/*", "/").replace("*", "")
                    resolved = str((self.root / base_url / target_prefix).resolve())
                    aliases[alias_prefix] = resolved
                return aliases
            except Exception:
                continue
        return {}

    def _resolve_alias_import(self, import_path: str) -> Optional[Path]:
        """Resolve an aliased import like ``@/components/Button``."""
        for alias_prefix, resolved_dir in self._path_aliases.items():
            if import_path.startswith(alias_prefix):
                remainder = import_path[len(alias_prefix):]
                candidate = Path(resolved_dir) / remainder
                for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs"):
                    check = candidate.with_suffix(ext) if ext else candidate
                    if check.is_file():
                        return check
                if candidate.is_dir():
                    for idx in ("index.ts", "index.tsx", "index.js", "index.jsx"):
                        check = candidate / idx
                        if check.is_file():
                            return check
        return None

    # ── Next.js scanning ──────────────────────────────────────────

    def _scan_nextjs(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Next.js routes...[/cyan]")
        pages_dirs = [
            self.root / "pages",
            self.root / "src" / "pages",
            self.root / "app",
            self.root / "src" / "app",
        ]

        page_nodes: dict[str, str] = {}
        api_nodes: dict[str, str] = {}

        for pages_dir in pages_dirs:
            if not pages_dir.is_dir():
                continue

            for f in self._find_files("*.ts", "*.tsx", "*.js", "*.jsx", root_dir=pages_dir):
                if f.name.startswith("_"):
                    continue

                rel = self.relative(f)
                route = self._file_to_route(f, pages_dir)

                if "/api/" in rel or rel.startswith("api/"):
                    node = Node(
                        name=f"API: {route}",
                        type=NodeType.API_ENDPOINT,
                        layer=Layer.BACKEND,
                        summary=f"Next.js API route: {route}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    api_nodes[rel] = node.id
                    self.storage.create_default_context(node)
                else:
                    is_layout = f.stem in ("layout", "loading", "error", "not-found")
                    node = Node(
                        name=f"Page: {route}" if not is_layout else f"Layout: {route}",
                        type=NodeType.PAGE if not is_layout else NodeType.COMPONENT,
                        layer=Layer.FRONTEND,
                        summary=f"Next.js {'layout' if is_layout else 'page'}: {route}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    page_nodes[rel] = node.id
                    self.storage.create_default_context(node)

        console.print(f"  Found {len(page_nodes)} pages, {len(api_nodes)} API routes")

    # ── Express scanning ──────────────────────────────────────────

    def _scan_express(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Express routes...[/cyan]")
        route_pattern = re.compile(
            r'''(?:router|app)\.(get|post|put|delete|patch|all)\s*\(\s*['"](/[^'"]*?)['"]'''
        )

        route_files = self._find_files("*.ts", "*.js")
        count = 0

        for f in route_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            matches = route_pattern.findall(content)
            for method, path in matches:
                rel = self.relative(f)
                node = Node(
                    name=f"{method.upper()} {path}",
                    type=NodeType.API_ENDPOINT,
                    layer=Layer.BACKEND,
                    summary=f"Express route: {method.upper()} {path}",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                count += 1

        console.print(f"  Found {count} Express routes")

    def _scan_generic_js(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning JS/TS source files...[/cyan]")
        src_dirs = [self.root / "src", self.root / "lib", self.root]
        files_added: set[str] = set()
        count = 0

        for src in src_dirs:
            if not src.is_dir():
                continue
            for f in self._find_files("*.ts", "*.tsx", "*.js", "*.jsx", root_dir=src):
                rel = self.relative(f)
                if rel in files_added:
                    continue
                files_added.add(rel)

                layer = self._guess_layer_js(rel)
                node_type = self._guess_type_js(f, rel)

                node = Node(
                    name=f.stem,
                    type=node_type,
                    layer=layer,
                    summary=f"Source: {rel}",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                count += 1

        console.print(f"  Found {count} source files")

    # ── React scanning ─────────────────────────────────────────────

    def _scan_react(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning React project...[/cyan]")
        src_dirs = [self.root / "src", self.root]
        existing_files = {n.file_path for n in graph.nodes if n.file_path}
        files_added: set[str] = set()
        page_count = 0
        comp_count = 0
        other_count = 0

        for src in src_dirs:
            if not src.is_dir():
                continue
            for f in self._find_files("*.ts", "*.tsx", "*.js", "*.jsx", root_dir=src):
                rel = self.relative(f)
                if rel in files_added or rel in existing_files:
                    continue
                files_added.add(rel)

                rel_lower = rel.lower()

                if any(p in rel_lower for p in ["pages/", "views/", "screens/"]):
                    node = Node(
                        name=f"Page: {f.stem}",
                        type=NodeType.PAGE,
                        layer=Layer.FRONTEND,
                        summary=f"React page: {rel}",
                        file_path=rel,
                    )
                    page_count += 1
                elif any(p in rel_lower for p in ["components/", "ui/", "common/"]):
                    node = Node(
                        name=f.stem,
                        type=NodeType.COMPONENT,
                        layer=Layer.FRONTEND,
                        summary=f"React component: {rel}",
                        file_path=rel,
                    )
                    comp_count += 1
                elif f.suffix in (".tsx", ".jsx"):
                    node = Node(
                        name=f.stem,
                        type=NodeType.COMPONENT,
                        layer=Layer.FRONTEND,
                        summary=f"React component: {rel}",
                        file_path=rel,
                    )
                    comp_count += 1
                else:
                    layer = self._guess_layer_js(rel)
                    node_type = self._guess_type_js(f, rel)
                    node = Node(
                        name=f.stem,
                        type=node_type,
                        layer=layer,
                        summary=f"Source: {rel}",
                        file_path=rel,
                    )
                    other_count += 1

                graph.nodes.append(node)
                self.storage.create_default_context(node)

        console.print(f"  Found {page_count} pages, {comp_count} components, {other_count} utilities")

        self._scan_react_routes(graph)

    def _scan_react_routes(self, graph: GraphData) -> None:
        """Detect React Router route definitions."""
        route_pattern = re.compile(
            r'''<Route\s+[^>]*path\s*=\s*['"]([^'"]+)['"][^>]*(?:component|element)\s*=\s*\{?\s*<?(\w+)''',
            re.DOTALL,
        )
        route_obj_pattern = re.compile(
            r'''path\s*:\s*['"]([^'"]+)['"]\s*,\s*(?:component|element)\s*:\s*(\w+)'''
        )

        count = 0
        for f in self._find_files("*.tsx", "*.jsx"):
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            if "Route" not in content and "createBrowserRouter" not in content:
                continue

            for pattern in [route_pattern, route_obj_pattern]:
                for match in pattern.finditer(content):
                    count += 1

        if count:
            console.print(f"  Detected {count} React Router route definitions")

    # ── Angular scanning ─────────────────────────────────────────────

    def _scan_angular(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Angular project...[/cyan]")
        self._scan_angular_components(graph)
        self._scan_angular_services(graph)
        self._scan_angular_modules(graph)
        self._scan_angular_pipes_directives(graph)
        self._scan_angular_guards(graph)
        self._scan_angular_routes(graph)

    def _scan_angular_components(self, graph: GraphData) -> None:
        comp_files = self._find_files("*.component.ts")
        count = 0

        for f in comp_files:
            rel = self.relative(f)
            comp_name = f.stem.replace(".component", "")

            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                content = ""

            selector_match = re.search(r'''selector\s*:\s*['"]([^'"]+)['"]''', content)
            selector = selector_match.group(1) if selector_match else comp_name

            is_page = any(p in rel.lower() for p in ["pages/", "views/", "screens/"])

            node = Node(
                name=comp_name,
                type=NodeType.PAGE if is_page else NodeType.COMPONENT,
                layer=Layer.FRONTEND,
                summary=f"Angular component: <{selector}> ({rel})",
                file_path=rel,
                metadata={"selector": selector},
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

            html_file = f.with_suffix("").with_suffix(".html")
            if html_file.exists():
                html_rel = self.relative(html_file)
                html_node = Node(
                    name=f"Template: {comp_name}",
                    type=NodeType.COMPONENT,
                    layer=Layer.FRONTEND,
                    summary=f"Angular template: {html_rel}",
                    file_path=html_rel,
                )
                graph.nodes.append(html_node)
                self.storage.create_default_context(html_node)
                graph.edges.append(Edge(
                    source=node.id,
                    target=html_node.id,
                    label="template",
                    type=EdgeType.RENDERS,
                ))

        console.print(f"  Found {count} Angular components")

    def _scan_angular_services(self, graph: GraphData) -> None:
        svc_files = self._find_files("*.service.ts")
        count = 0

        for f in svc_files:
            rel = self.relative(f)
            svc_name = f.stem.replace(".service", "")

            node = Node(
                name=f"{svc_name}Service",
                type=NodeType.SERVICE,
                layer=Layer.FRONTEND,
                summary=f"Angular service: {svc_name} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        if count:
            console.print(f"  Found {count} Angular services")

    def _scan_angular_modules(self, graph: GraphData) -> None:
        mod_files = self._find_files("*.module.ts")
        count = 0

        for f in mod_files:
            rel = self.relative(f)
            mod_name = f.stem.replace(".module", "")

            node = Node(
                name=f"{mod_name}Module",
                type=NodeType.CONFIG,
                layer=Layer.FRONTEND,
                summary=f"Angular module: {mod_name} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        if count:
            console.print(f"  Found {count} Angular modules")

    def _scan_angular_pipes_directives(self, graph: GraphData) -> None:
        pipe_files = self._find_files("*.pipe.ts")
        directive_files = self._find_files("*.directive.ts")
        count = 0

        for f in pipe_files:
            rel = self.relative(f)
            name = f.stem.replace(".pipe", "")
            node = Node(
                name=f"{name}Pipe",
                type=NodeType.UTILITY,
                layer=Layer.FRONTEND,
                summary=f"Angular pipe: {name} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        for f in directive_files:
            rel = self.relative(f)
            name = f.stem.replace(".directive", "")
            node = Node(
                name=f"{name}Directive",
                type=NodeType.UTILITY,
                layer=Layer.FRONTEND,
                summary=f"Angular directive: {name} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        if count:
            console.print(f"  Found {count} pipes/directives")

    def _scan_angular_guards(self, graph: GraphData) -> None:
        guard_files = self._find_files("*.guard.ts")
        count = 0

        for f in guard_files:
            rel = self.relative(f)
            name = f.stem.replace(".guard", "")
            node = Node(
                name=f"{name}Guard",
                type=NodeType.MIDDLEWARE,
                layer=Layer.FRONTEND,
                summary=f"Angular guard: {name} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        if count:
            console.print(f"  Found {count} route guards")

    def _scan_angular_routes(self, graph: GraphData) -> None:
        """Detect Angular routing modules and route definitions."""
        routing_files = self._find_files("*-routing.module.ts", "*routes.ts")
        route_pattern = re.compile(r'''path\s*:\s*['"]([^'"]+)['"]''')
        count = 0

        for f in routing_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for match in route_pattern.finditer(content):
                count += 1

        if count:
            console.print(f"  Detected {count} Angular route definitions")

    def _scan_prisma(self, graph: GraphData) -> None:
        schema_path = self.root / "prisma" / "schema.prisma"
        if not schema_path.exists():
            schema_path = self.root / "schema.prisma"
        if not schema_path.exists():
            return

        console.print("[cyan]Scanning Prisma schema...[/cyan]")
        try:
            content = schema_path.read_text(encoding="utf-8")
        except Exception:
            return

        model_pattern = re.compile(r"model\s+(\w+)\s*\{([^}]+)\}", re.MULTILINE)
        relation_pattern = re.compile(r"(\w+)\s+(\w+)[\[\]?]*\s+@relation")

        model_nodes: dict[str, str] = {}

        for match in model_pattern.finditer(content):
            model_name = match.group(1)
            node = Node(
                name=f"Table: {model_name}",
                type=NodeType.DB_TABLE,
                layer=Layer.DATABASE,
                summary=f"Prisma model: {model_name}",
                file_path=self.relative(schema_path),
            )
            graph.nodes.append(node)
            model_nodes[model_name] = node.id
            self.storage.create_default_context(node)

        for match in model_pattern.finditer(content):
            model_name = match.group(1)
            body = match.group(2)
            source_id = model_nodes.get(model_name)
            if not source_id:
                continue

            for rel_match in relation_pattern.finditer(body):
                related_model = rel_match.group(2)
                target_id = model_nodes.get(related_model)
                if target_id:
                    edge = Edge(
                        source=source_id,
                        target=target_id,
                        label=f"{model_name} -> {related_model}",
                        type=EdgeType.FOREIGN_KEY,
                    )
                    graph.edges.append(edge)

        console.print(f"  Found {len(model_nodes)} Prisma models")

    def _scan_sequelize_or_typeorm(self, graph: GraphData) -> None:
        model_dirs = [
            self.root / "src" / "models",
            self.root / "models",
            self.root / "src" / "entities",
            self.root / "entities",
        ]

        for model_dir in model_dirs:
            if not model_dir.is_dir():
                continue

            console.print(f"[cyan]Scanning models in {self.relative(model_dir)}/...[/cyan]")
            for f in self._find_files("*.ts", "*.js", root_dir=model_dir):
                if f.stem in ("index", "__init__"):
                    continue

                rel = self.relative(f)
                node = Node(
                    name=f"Table: {f.stem}",
                    type=NodeType.DB_TABLE,
                    layer=Layer.DATABASE,
                    summary=f"Database model: {rel}",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)

    def _scan_components(self, graph: GraphData) -> None:
        comp_dirs = [
            self.root / "src" / "components",
            self.root / "components",
            self.root / "src" / "ui",
        ]

        existing_files = {n.file_path for n in graph.nodes if n.file_path}

        for comp_dir in comp_dirs:
            if not comp_dir.is_dir():
                continue

            console.print(f"[cyan]Scanning components in {self.relative(comp_dir)}/...[/cyan]")
            for f in self._find_files("*.tsx", "*.jsx", "*.vue", "*.svelte", root_dir=comp_dir):
                rel = self.relative(f)
                if rel in existing_files:
                    continue

                node = Node(
                    name=f.stem,
                    type=NodeType.COMPONENT,
                    layer=Layer.FRONTEND,
                    summary=f"Component: {rel}",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)

    def _file_to_route(self, file_path: Path, pages_dir: Path) -> str:
        rel = file_path.relative_to(pages_dir)
        route = "/" + str(rel).replace("\\", "/")
        route = re.sub(r"\.(tsx|ts|jsx|js)$", "", route)
        route = re.sub(r"/index$", "", route) or "/"
        route = re.sub(r"/page$", "", route) or "/"
        route = re.sub(r"\[([^\]]+)\]", r":\1", route)
        return route

    def _guess_layer_js(self, rel: str) -> Layer:
        rel_lower = rel.lower()
        if any(p in rel_lower for p in ["api/", "routes/", "controllers/", "server/", "backend/", "handlers/"]):
            return Layer.BACKEND
        if any(p in rel_lower for p in ["models/", "entities/", "prisma/", "db/", "database/", "migrations/"]):
            return Layer.DATABASE
        if any(p in rel_lower for p in ["pages/", "components/", "views/", "app/", "frontend/", "client/"]):
            return Layer.FRONTEND
        return Layer.SHARED

    def _guess_type_js(self, file_path: Path, rel: str) -> NodeType:
        rel_lower = rel.lower()
        if any(p in rel_lower for p in ["api/", "routes/", "controllers/", "handlers/"]):
            return NodeType.API_ENDPOINT
        if any(p in rel_lower for p in ["models/", "entities/"]):
            return NodeType.DB_TABLE
        if any(p in rel_lower for p in ["services/"]):
            return NodeType.SERVICE
        if any(p in rel_lower for p in ["middleware/"]):
            return NodeType.MIDDLEWARE
        if file_path.suffix in (".tsx", ".jsx", ".vue", ".svelte"):
            return NodeType.COMPONENT
        if any(p in rel_lower for p in ["utils/", "lib/", "helpers/"]):
            return NodeType.UTILITY
        if any(p in rel_lower for p in ["config"]):
            return NodeType.CONFIG
        return NodeType.UTILITY

    # ── Import-based edge inference ──────────────────────────────────

    def _infer_import_edges(self, graph: GraphData) -> None:
        """Resolve JS/TS imports between scanned nodes to create dependency edges."""
        file_node_map: dict[str, str] = {}
        for node in graph.nodes:
            if node.file_path:
                file_node_map[node.file_path] = node.id

        existing_pairs: set[tuple[str, str]] = {(e.source, e.target) for e in graph.edges}
        edge_count = 0

        console.print("[cyan]Analyzing JS/TS imports...[/cyan]")

        for node in graph.nodes:
            if not node.file_path:
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

            source_id = file_node_map.get(node.file_path)
            if not source_id:
                continue

            for pattern in JS_IMPORT_PATTERNS:
                for match in pattern.finditer(content):
                    import_path = match.group(1)

                    if import_path.startswith("."):
                        resolved = self._resolve_import(file_path, import_path)
                    else:
                        resolved = self._resolve_alias_import(import_path)

                    if resolved:
                        target_rel = self.relative(resolved)
                        target_id = file_node_map.get(target_rel)
                        if target_id and target_id != source_id and (source_id, target_id) not in existing_pairs:
                            graph.edges.append(Edge(
                                source=source_id,
                                target=target_id,
                                label="imports",
                                type=EdgeType.DEPENDENCY,
                            ))
                            existing_pairs.add((source_id, target_id))
                            edge_count += 1

        console.print(f"  Found {edge_count} import relationships")

    def _resolve_import(self, source: Path, import_path: str) -> Path | None:
        """Resolve a relative JS/TS import to a file path."""
        source_dir = source.parent
        candidate = (source_dir / import_path).resolve()
        extensions = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs"]
        for ext in extensions:
            check = candidate.with_suffix(ext) if ext else candidate
            if check.is_file():
                return check
        if candidate.is_dir():
            for idx in ("index.ts", "index.tsx", "index.js", "index.jsx"):
                check = candidate / idx
                if check.is_file():
                    return check
        return None
