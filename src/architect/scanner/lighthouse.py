"""Lighthouse performance auditing for page nodes."""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError

from rich.console import Console

from ..models import GraphData, NodeType

console = Console()

COMMON_PORTS = [3000, 5173, 8080, 4200, 8000, 4000]

DEV_SCRIPT_PRIORITY = ["dev", "start", "serve"]


def _find_open_port(ports: list[int]) -> Optional[int]:
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return port
    return None


def _wait_for_server(base_url: str, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urlopen(base_url, timeout=2)
            return True
        except (URLError, OSError):
            time.sleep(1)
    return False


def _extract_port_from_script(script_cmd: str) -> Optional[int]:
    m = re.search(r"--port[=\s]+(\d+)", script_cmd)
    if m:
        return int(m.group(1))
    m = re.search(r"-p[=\s]+(\d+)", script_cmd)
    if m:
        return int(m.group(1))
    return None


def _extract_route(node_name: str, node_summary: str) -> str:
    """Extract a URL route from a page node's name or summary."""
    for text in [node_name, node_summary]:
        m = re.search(r":\s*(/\S*)", text)
        if m:
            route = m.group(1)
            if route.startswith("/"):
                return route

    name_clean = node_name.replace("Page: ", "").replace("Page:", "").strip()
    if name_clean.startswith("/"):
        return name_clean

    return "/"


def _find_lighthouse_cmd() -> Optional[list[str]]:
    if shutil.which("npx"):
        return ["npx", "lighthouse"]
    if shutil.which("lighthouse"):
        return ["lighthouse"]
    return None


class LighthouseScanner:
    """Run Lighthouse audits against page nodes and store scores as metadata."""

    def __init__(
        self,
        project_root: Path,
        base_url: Optional[str] = None,
    ):
        self.project_root = project_root
        self.base_url = base_url
        self._server_proc: Optional[subprocess.Popen] = None

    def audit(self, graph: GraphData) -> dict:
        """Run Lighthouse audits on all page nodes. Returns stats dict."""
        stats = {"audited": 0, "skipped": 0, "avg_performance": 0}

        lh_cmd = _find_lighthouse_cmd()
        if not lh_cmd:
            console.print(
                "[yellow]Lighthouse not found -- skipping performance audit. "
                "Install with: npm i -g lighthouse[/yellow]"
            )
            return stats

        page_nodes = [n for n in graph.nodes if n.type == NodeType.PAGE]
        if not page_nodes:
            console.print("  No page nodes found -- skipping Lighthouse audit")
            return stats

        base_url = self._resolve_base_url()
        if not base_url:
            console.print(
                "[yellow]Could not determine dev server URL -- skipping Lighthouse audit. "
                "Use --base-url to provide one manually.[/yellow]"
            )
            return stats

        console.print(f"\n[bold]Running Lighthouse audits...[/bold] ({base_url})")

        perf_scores = []

        for node in page_nodes:
            route = _extract_route(node.name, node.summary)
            url = base_url.rstrip("/") + route

            console.print(f"  Auditing {url}...")

            scores = self._run_audit(lh_cmd, url)
            if scores:
                node.metadata["lighthouse"] = {
                    "performance": scores.get("performance", 0),
                    "accessibility": scores.get("accessibility", 0),
                    "best_practices": scores.get("best-practices", 0),
                    "seo": scores.get("seo", 0),
                    "url": url,
                    "audited_at": datetime.now(timezone.utc).isoformat(),
                }
                perf_scores.append(scores.get("performance", 0))
                stats["audited"] += 1
                console.print(
                    f"    Perf: {scores.get('performance', '?')} | "
                    f"A11y: {scores.get('accessibility', '?')} | "
                    f"BP: {scores.get('best-practices', '?')} | "
                    f"SEO: {scores.get('seo', '?')}"
                )
            else:
                stats["skipped"] += 1

        self._stop_server()

        if perf_scores:
            stats["avg_performance"] = round(sum(perf_scores) / len(perf_scores))

        console.print(
            f"  [green]Lighthouse: Audited {stats['audited']} pages "
            f"(avg performance: {stats['avg_performance']})[/green]"
        )

        return stats

    def _resolve_base_url(self) -> Optional[str]:
        if self.base_url:
            if _wait_for_server(self.base_url, timeout=5):
                return self.base_url
            console.print(f"[yellow]Provided base URL {self.base_url} is not responding[/yellow]")
            return None

        existing_port = _find_open_port(COMMON_PORTS)
        if existing_port:
            url = f"http://localhost:{existing_port}"
            console.print(f"  Found running server on port {existing_port}")
            return url

        return self._start_server()

    def _start_server(self) -> Optional[str]:
        pkg_path = self.project_root / "package.json"
        if not pkg_path.exists():
            return None

        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        scripts = pkg.get("scripts", {})
        chosen_script = None
        for name in DEV_SCRIPT_PRIORITY:
            if name in scripts:
                chosen_script = name
                break

        if not chosen_script:
            return None

        script_cmd = scripts[chosen_script]
        port = _extract_port_from_script(script_cmd)

        console.print(f"  Starting dev server: npm run {chosen_script}")

        try:
            self._server_proc = subprocess.Popen(
                ["npm", "run", chosen_script],
                cwd=str(self.project_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=True,
            )
        except Exception as e:
            console.print(f"[yellow]Failed to start dev server: {e}[/yellow]")
            return None

        ports_to_check = [port] if port else COMMON_PORTS
        deadline = time.monotonic() + 30

        while time.monotonic() < deadline:
            found = _find_open_port(ports_to_check)
            if found:
                url = f"http://localhost:{found}"
                if _wait_for_server(url, timeout=5):
                    console.print(f"  Dev server ready on port {found}")
                    return url
            time.sleep(1)

        console.print("[yellow]Dev server did not start within 30 seconds[/yellow]")
        self._stop_server()
        return None

    def _stop_server(self) -> None:
        if self._server_proc:
            try:
                self._server_proc.terminate()
                self._server_proc.wait(timeout=5)
            except Exception:
                try:
                    self._server_proc.kill()
                except Exception:
                    pass
            self._server_proc = None

    def _run_audit(self, lh_cmd: list[str], url: str) -> Optional[dict[str, int]]:
        cmd = lh_cmd + [
            url,
            "--output=json",
            "--chrome-flags=--headless --no-sandbox",
            "--only-categories=performance,accessibility,best-practices,seo",
            "--quiet",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=90,
                cwd=str(self.project_root),
                shell=True,
            )
        except subprocess.TimeoutExpired:
            console.print(f"    [yellow]Timeout auditing {url}[/yellow]")
            return None
        except Exception as e:
            console.print(f"    [yellow]Lighthouse error: {e}[/yellow]")
            return None

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")[:200]
            if stderr:
                console.print(f"    [yellow]Lighthouse failed: {stderr.strip()}[/yellow]")
            return None

        try:
            report = json.loads(result.stdout.decode("utf-8", errors="ignore"))
            categories = report.get("categories", {})
            scores = {}
            for key in ["performance", "accessibility", "best-practices", "seo"]:
                cat = categories.get(key, {})
                raw = cat.get("score")
                scores[key] = round(raw * 100) if raw is not None else 0
            return scores
        except (json.JSONDecodeError, KeyError) as e:
            console.print(f"    [yellow]Failed to parse Lighthouse output: {e}[/yellow]")
            return None
