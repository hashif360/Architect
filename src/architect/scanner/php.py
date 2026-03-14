"""PHP project scanner with Laravel, Symfony, and WordPress detection."""

from __future__ import annotations

import json
import re
from pathlib import Path

from rich.console import Console

from ..models import Edge, EdgeType, GraphData, Layer, Node, NodeType
from .base import BaseScanner

console = Console()

# Laravel route patterns
LARAVEL_ROUTE_PATTERNS = [
    re.compile(r'''Route\s*::\s*(get|post|put|patch|delete|any|match|options)\s*\(\s*['"]([^'"]+)['"]'''),
    re.compile(r'''Route\s*::\s*(?:resource|apiResource)\s*\(\s*['"]([^'"]+)['"]'''),
]

# Symfony route annotation/attribute
SYMFONY_ROUTE_PATTERN = re.compile(
    r'''#\[Route\s*\(\s*['"]([^'"]+)['"](?:.*?methods?\s*[:=]\s*\[([^\]]+)\])?\s*\)\s*\]''',
    re.MULTILINE,
)

SYMFONY_ANNOTATION_ROUTE = re.compile(
    r'''@Route\s*\(\s*['"]([^'"]+)['"]''',
)

# PHP class pattern
PHP_CLASS_PATTERN = re.compile(
    r'class\s+(\w+)\s*(?:extends\s+(\w+))?\s*(?:implements\s+([^{]+))?\s*\{',
    re.MULTILINE,
)

# Eloquent model relationships
ELOQUENT_RELATION_PATTERN = re.compile(
    r'''(?:return\s+)?\$this->(?:hasMany|hasOne|belongsTo|belongsToMany|morphTo|morphMany|morphOne|morphToMany)\s*\(\s*(\w+)::class''',
)

# PHP use/require imports
PHP_USE_PATTERN = re.compile(r'^use\s+([\w\\]+)\s*;', re.MULTILINE)
PHP_REQUIRE_PATTERN = re.compile(r'''(?:require|include)(?:_once)?\s*\(?['"]([^'"]+)['"]\)?''')

# Blade directive extends/includes
BLADE_EXTENDS_PATTERN = re.compile(r'''@extends\s*\(\s*['"]([^'"]+)['"]\s*\)''')
BLADE_INCLUDE_PATTERN = re.compile(r'''@(?:include|component)\s*\(\s*['"]([^'"]+)['"]\s*\)''')


class PHPScanner(BaseScanner):
    name = "php"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.framework: str = "generic"
        self.composer: dict = {}

    def scan(self) -> GraphData:
        graph = GraphData()
        self._detect_framework()
        console.print(f"[cyan]Detected PHP framework:[/cyan] {self.framework}")

        if self.framework == "laravel":
            self._scan_laravel(graph)
        elif self.framework == "symfony":
            self._scan_symfony(graph)
        elif self.framework == "wordpress":
            self._scan_wordpress(graph)
        else:
            self._scan_generic_php(graph)

        self._infer_import_edges(graph)

        return graph

    def _detect_framework(self) -> None:
        composer_path = self.root / "composer.json"
        if composer_path.exists():
            try:
                self.composer = json.loads(composer_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        all_deps = {
            **self.composer.get("require", {}),
            **self.composer.get("require-dev", {}),
        }

        if (self.root / "artisan").exists() or "laravel/framework" in all_deps:
            self.framework = "laravel"
        elif "symfony/framework-bundle" in all_deps or "symfony/symfony" in all_deps:
            self.framework = "symfony"
        elif (self.root / "wp-config.php").exists() or (self.root / "wp-content").is_dir():
            self.framework = "wordpress"

    # ── Laravel ──────────────────────────────────────────────────────

    def _scan_laravel(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Laravel project...[/cyan]")
        self._scan_laravel_routes(graph)
        self._scan_laravel_controllers(graph)
        self._scan_laravel_models(graph)
        self._scan_laravel_views(graph)
        self._scan_laravel_middleware(graph)
        self._scan_laravel_migrations(graph)
        self._scan_laravel_services(graph)

    def _scan_laravel_routes(self, graph: GraphData) -> None:
        route_dir = self.root / "routes"
        if not route_dir.is_dir():
            return

        count = 0
        for f in self._find_files("*.php", root_dir=route_dir):
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel = self.relative(f)
            for pattern in LARAVEL_ROUTE_PATTERNS:
                for match in pattern.finditer(content):
                    if match.lastindex and match.lastindex >= 2:
                        method = match.group(1).upper()
                        route = match.group(2)
                        name = f"{method} /{route}"
                    else:
                        route = match.group(1)
                        name = f"Resource: /{route}"

                    node = Node(
                        name=name,
                        type=NodeType.API_ENDPOINT,
                        layer=Layer.BACKEND,
                        summary=f"Laravel route: {name} in {rel}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    self.storage.create_default_context(node)
                    count += 1

        console.print(f"  Found {count} routes")

    def _scan_laravel_controllers(self, graph: GraphData) -> None:
        ctrl_dir = self.root / "app" / "Http" / "Controllers"
        if not ctrl_dir.is_dir():
            return

        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for f in self._find_files("*.php", root_dir=ctrl_dir):
            rel = self.relative(f)
            if rel in existing:
                continue

            node = Node(
                name=f.stem,
                type=NodeType.API_ENDPOINT,
                layer=Layer.BACKEND,
                summary=f"Laravel controller: {f.stem} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            existing.add(rel)
            count += 1

        console.print(f"  Found {count} controllers")

    def _scan_laravel_models(self, graph: GraphData) -> None:
        model_dirs = [self.root / "app" / "Models", self.root / "app"]
        existing = {n.file_path for n in graph.nodes if n.file_path}
        model_nodes: dict[str, str] = {}
        count = 0

        for model_dir in model_dirs:
            if not model_dir.is_dir():
                continue

            for f in self._find_files("*.php", root_dir=model_dir):
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                if "extends Model" not in content and "extends Authenticatable" not in content:
                    continue

                rel = self.relative(f)
                if rel in existing:
                    continue

                for match in PHP_CLASS_PATTERN.finditer(content):
                    class_name = match.group(1)
                    parent = match.group(2) or ""
                    if parent in ("Model", "Authenticatable", "Pivot"):
                        node = Node(
                            name=f"Model: {class_name}",
                            type=NodeType.DB_TABLE,
                            layer=Layer.DATABASE,
                            summary=f"Eloquent model: {class_name} ({rel})",
                            file_path=rel,
                        )
                        graph.nodes.append(node)
                        model_nodes[class_name] = node.id
                        self.storage.create_default_context(node)
                        existing.add(rel)
                        count += 1

                for rel_match in ELOQUENT_RELATION_PATTERN.finditer(content):
                    related = rel_match.group(1)
                    source_class = None
                    for cm in PHP_CLASS_PATTERN.finditer(content):
                        if cm.start() < rel_match.start():
                            source_class = cm.group(1)
                    if source_class and source_class in model_nodes and related in model_nodes:
                        graph.edges.append(Edge(
                            source=model_nodes[source_class],
                            target=model_nodes[related],
                            label=f"{source_class} -> {related}",
                            type=EdgeType.FOREIGN_KEY,
                        ))

        console.print(f"  Found {count} Eloquent models")

    def _scan_laravel_views(self, graph: GraphData) -> None:
        view_dir = self.root / "resources" / "views"
        if not view_dir.is_dir():
            return

        count = 0
        for f in self._find_files("*.php", "*.blade.php", root_dir=view_dir):
            rel = self.relative(f)
            name = f.name.replace(".blade.php", "").replace(".php", "")
            node = Node(
                name=f"View: {name}",
                type=NodeType.PAGE,
                layer=Layer.FRONTEND,
                summary=f"Blade template: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        console.print(f"  Found {count} Blade views")

    def _scan_laravel_middleware(self, graph: GraphData) -> None:
        mw_dir = self.root / "app" / "Http" / "Middleware"
        if not mw_dir.is_dir():
            return

        count = 0
        for f in self._find_files("*.php", root_dir=mw_dir):
            rel = self.relative(f)
            node = Node(
                name=f.stem,
                type=NodeType.MIDDLEWARE,
                layer=Layer.BACKEND,
                summary=f"Laravel middleware: {f.stem} ({rel})",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        if count:
            console.print(f"  Found {count} middleware")

    def _scan_laravel_migrations(self, graph: GraphData) -> None:
        mig_dir = self.root / "database" / "migrations"
        if not mig_dir.is_dir():
            return

        count = 0
        for f in self._find_files("*.php", root_dir=mig_dir):
            rel = self.relative(f)
            node = Node(
                name=f"Migration: {f.stem}",
                type=NodeType.DB_TABLE,
                layer=Layer.DATABASE,
                summary=f"Database migration: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        if count:
            console.print(f"  Found {count} migrations")

    def _scan_laravel_services(self, graph: GraphData) -> None:
        service_dirs = [
            self.root / "app" / "Services",
            self.root / "app" / "Repositories",
            self.root / "app" / "Actions",
        ]

        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for sd in service_dirs:
            if not sd.is_dir():
                continue
            for f in self._find_files("*.php", root_dir=sd):
                rel = self.relative(f)
                if rel in existing:
                    continue

                node = Node(
                    name=f.stem,
                    type=NodeType.SERVICE,
                    layer=Layer.BACKEND,
                    summary=f"Service: {f.stem} ({rel})",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                existing.add(rel)
                count += 1

        if count:
            console.print(f"  Found {count} services/repositories")

    # ── Symfony ───────────────────────────────────────────────────────

    def _scan_symfony(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Symfony project...[/cyan]")
        self._scan_symfony_controllers(graph)
        self._scan_symfony_entities(graph)
        self._scan_symfony_templates(graph)
        self._scan_symfony_services(graph)
        self._scan_generic_php(graph)

    def _scan_symfony_controllers(self, graph: GraphData) -> None:
        ctrl_dirs = [
            self.root / "src" / "Controller",
            self.root / "src" / "Controllers",
        ]
        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for ctrl_dir in ctrl_dirs:
            if not ctrl_dir.is_dir():
                continue
            for f in self._find_files("*.php", root_dir=ctrl_dir):
                rel = self.relative(f)
                if rel in existing:
                    continue

                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                routes = []
                for match in SYMFONY_ROUTE_PATTERN.finditer(content):
                    routes.append(match.group(1))
                for match in SYMFONY_ANNOTATION_ROUTE.finditer(content):
                    routes.append(match.group(1))

                route_summary = ", ".join(routes[:5]) if routes else "controller"
                node = Node(
                    name=f.stem,
                    type=NodeType.API_ENDPOINT,
                    layer=Layer.BACKEND,
                    summary=f"Symfony controller: {f.stem} ({route_summary})",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                existing.add(rel)
                count += 1

        console.print(f"  Found {count} Symfony controllers")

    def _scan_symfony_entities(self, graph: GraphData) -> None:
        entity_dirs = [
            self.root / "src" / "Entity",
            self.root / "src" / "Entities",
        ]
        count = 0

        for entity_dir in entity_dirs:
            if not entity_dir.is_dir():
                continue
            for f in self._find_files("*.php", root_dir=entity_dir):
                rel = self.relative(f)
                node = Node(
                    name=f"Entity: {f.stem}",
                    type=NodeType.DB_TABLE,
                    layer=Layer.DATABASE,
                    summary=f"Doctrine entity: {f.stem} ({rel})",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                count += 1

        if count:
            console.print(f"  Found {count} Doctrine entities")

    def _scan_symfony_templates(self, graph: GraphData) -> None:
        tpl_dir = self.root / "templates"
        if not tpl_dir.is_dir():
            return

        count = 0
        for f in self._find_files("*.twig", "*.html", "*.htm", root_dir=tpl_dir):
            rel = self.relative(f)
            node = Node(
                name=f"Template: {f.stem}",
                type=NodeType.PAGE,
                layer=Layer.FRONTEND,
                summary=f"Twig template: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        if count:
            console.print(f"  Found {count} Twig templates")

    def _scan_symfony_services(self, graph: GraphData) -> None:
        svc_dirs = [
            self.root / "src" / "Service",
            self.root / "src" / "Services",
            self.root / "src" / "Repository",
        ]
        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for svc_dir in svc_dirs:
            if not svc_dir.is_dir():
                continue
            for f in self._find_files("*.php", root_dir=svc_dir):
                rel = self.relative(f)
                if rel in existing:
                    continue

                node = Node(
                    name=f.stem,
                    type=NodeType.SERVICE,
                    layer=Layer.BACKEND,
                    summary=f"Symfony service: {f.stem} ({rel})",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                existing.add(rel)
                count += 1

        if count:
            console.print(f"  Found {count} services")

    # ── WordPress ────────────────────────────────────────────────────

    def _scan_wordpress(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning WordPress project...[/cyan]")

        theme_dir = self.root / "wp-content" / "themes"
        plugin_dir = self.root / "wp-content" / "plugins"

        if theme_dir.is_dir():
            for theme in sorted(theme_dir.iterdir()):
                if not theme.is_dir() or self._should_skip(theme):
                    continue
                self._scan_wp_directory(graph, theme, "Theme", Layer.FRONTEND)

        if plugin_dir.is_dir():
            for plugin in sorted(plugin_dir.iterdir()):
                if not plugin.is_dir() or self._should_skip(plugin):
                    continue
                self._scan_wp_directory(graph, plugin, "Plugin", Layer.BACKEND)

        if not theme_dir.is_dir() and not plugin_dir.is_dir():
            self._scan_generic_php(graph)

    def _scan_wp_directory(self, graph: GraphData, directory: Path, prefix: str, layer: Layer) -> None:
        count = 0
        for f in self._find_files("*.php", root_dir=directory):
            rel = self.relative(f)
            node = Node(
                name=f"{prefix}: {f.stem}",
                type=NodeType.COMPONENT if layer == Layer.FRONTEND else NodeType.SERVICE,
                layer=layer,
                summary=f"WordPress {prefix.lower()}: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        console.print(f"  Found {count} {prefix.lower()} files in {directory.name}/")

    # ── Generic PHP ──────────────────────────────────────────────────

    def _scan_generic_php(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning PHP source files...[/cyan]")
        existing = {n.file_path for n in graph.nodes if n.file_path}
        php_files = self._find_files("*.php")
        count = 0

        for f in php_files:
            rel = self.relative(f)
            if rel in existing:
                continue

            layer = self._guess_layer(rel)
            node_type = self._guess_type(rel)

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
            console.print(f"  Found {count} additional PHP files")

    # ── Import edge inference ────────────────────────────────────────

    def _infer_import_edges(self, graph: GraphData) -> None:
        file_node_map: dict[str, str] = {}
        for node in graph.nodes:
            if node.file_path:
                file_node_map[node.file_path] = node.id

        name_node_map: dict[str, str] = {}
        for node in graph.nodes:
            stem = node.name.split(": ")[-1] if ": " in node.name else node.name
            name_node_map[stem.lower()] = node.id

        existing_pairs: set[tuple[str, str]] = {(e.source, e.target) for e in graph.edges}
        edge_count = 0

        console.print("[cyan]Analyzing PHP imports...[/cyan]")

        for node in graph.nodes:
            if not node.file_path or not node.file_path.endswith(".php"):
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

            for match in PHP_USE_PATTERN.finditer(content):
                fqcn = match.group(1)
                class_name = fqcn.split("\\")[-1]
                target_id = name_node_map.get(class_name.lower())
                if target_id and target_id != source_id and (source_id, target_id) not in existing_pairs:
                    graph.edges.append(Edge(
                        source=source_id,
                        target=target_id,
                        label=f"uses {class_name}",
                        type=EdgeType.DEPENDENCY,
                    ))
                    existing_pairs.add((source_id, target_id))
                    edge_count += 1

        console.print(f"  Found {edge_count} import relationships")

    # ── Helpers ──────────────────────────────────────────────────────

    def _guess_layer(self, rel: str) -> Layer:
        rel_lower = rel.lower()
        if any(p in rel_lower for p in ["views/", "templates/", "resources/views/", "public/", "frontend/"]):
            return Layer.FRONTEND
        if any(p in rel_lower for p in ["models/", "entities/", "migrations/", "database/"]):
            return Layer.DATABASE
        if any(p in rel_lower for p in [
            "controllers/", "api/", "routes/", "services/", "middleware/",
            "handlers/", "http/", "console/",
        ]):
            return Layer.BACKEND
        if any(p in rel_lower for p in ["utils/", "helpers/", "lib/", "common/", "config/"]):
            return Layer.SHARED
        return Layer.BACKEND

    def _guess_type(self, rel: str) -> NodeType:
        rel_lower = rel.lower()
        if any(p in rel_lower for p in ["controllers/", "controller."]):
            return NodeType.API_ENDPOINT
        if any(p in rel_lower for p in ["models/", "entities/"]):
            return NodeType.DB_TABLE
        if any(p in rel_lower for p in ["services/", "repositories/"]):
            return NodeType.SERVICE
        if any(p in rel_lower for p in ["middleware/"]):
            return NodeType.MIDDLEWARE
        if any(p in rel_lower for p in ["views/", "templates/"]):
            return NodeType.PAGE
        if any(p in rel_lower for p in ["config/", "config."]):
            return NodeType.CONFIG
        if any(p in rel_lower for p in ["utils/", "helpers/", "lib/"]):
            return NodeType.UTILITY
        if any(p in rel_lower for p in ["migrations/"]):
            return NodeType.DB_TABLE
        return NodeType.UTILITY
