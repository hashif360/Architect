"""Export the architecture graph to various diagram formats."""

from __future__ import annotations

from .models import GraphData, Layer


def _safe_id(node_id: str) -> str:
    """Make an ID safe for Mermaid/PlantUML (alphanumeric + underscore)."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in node_id)


def _escape_label(label: str) -> str:
    return label.replace('"', '\\"').replace("\n", " ")


MERMAID_SHAPE = {
    "page": ('["{label}"]', ""),
    "component": ('("{label}")', ""),
    "api_endpoint": ('{{"{label}"}}', ""),
    "service": ('[/"{label}"\\]', ""),
    "db_table": ('[("{label}")]', ""),
    "db_column": ('("{label}")', ""),
    "middleware": ('>"{label}"]', ""),
    "utility": ('["{label}"]', ""),
    "config": ('["{label}"]', ""),
    "custom": ('["{label}"]', ""),
}

LAYER_ORDER = [Layer.FRONTEND, Layer.BACKEND, Layer.DATABASE, Layer.SHARED]


def to_mermaid(graph: GraphData, direction: str = "TD") -> str:
    """Export graph as a Mermaid flowchart diagram string."""
    lines = [f"flowchart {direction}"]

    grouped: dict[str, list] = {}
    for node in graph.nodes:
        grouped.setdefault(node.layer.value, []).append(node)

    for layer in LAYER_ORDER:
        nodes = grouped.get(layer.value, [])
        if not nodes:
            continue
        lines.append(f"    subgraph {layer.value}[\"{layer.value.title()}\"]")
        for node in nodes:
            sid = _safe_id(node.id)
            label = _escape_label(node.name)
            shape_tmpl = MERMAID_SHAPE.get(node.type.value, ('["{label}"]', ""))
            shape = shape_tmpl[0].format(label=label)
            lines.append(f"        {sid}{shape}")
        lines.append("    end")

    for edge in graph.edges:
        src = _safe_id(edge.source)
        tgt = _safe_id(edge.target)
        if edge.label:
            lines.append(f"    {src} -->|\"{_escape_label(edge.label)}\"| {tgt}")
        else:
            lines.append(f"    {src} --> {tgt}")

    style_map = {
        "page": "fill:#6c8cff,color:#fff,stroke:#4a6cd4",
        "component": "fill:#22d3ee,color:#000,stroke:#1ba8be",
        "api_endpoint": "fill:#4ade80,color:#000,stroke:#38b866",
        "service": "fill:#a78bfa,color:#fff,stroke:#8b6fd4",
        "db_table": "fill:#fb923c,color:#000,stroke:#d47a2f",
        "middleware": "fill:#f87171,color:#fff,stroke:#d45b5b",
        "utility": "fill:#8b90a0,color:#fff,stroke:#6b6f7f",
        "config": "fill:#facc15,color:#000,stroke:#d4ac10",
    }

    for node in graph.nodes:
        style = style_map.get(node.type.value)
        if style:
            lines.append(f"    style {_safe_id(node.id)} {style}")

    return "\n".join(lines)


PLANTUML_STEREO = {
    "page": "<<page>>",
    "component": "<<component>>",
    "api_endpoint": "<<api>>",
    "service": "<<service>>",
    "db_table": "<<table>>",
    "db_column": "<<column>>",
    "middleware": "<<middleware>>",
    "utility": "<<utility>>",
    "config": "<<config>>",
    "custom": "<<custom>>",
}

PLANTUML_COLOR = {
    "page": "#6c8cff",
    "component": "#22d3ee",
    "api_endpoint": "#4ade80",
    "service": "#a78bfa",
    "db_table": "#fb923c",
    "middleware": "#f87171",
    "utility": "#8b90a0",
    "config": "#facc15",
}


def to_plantuml(graph: GraphData) -> str:
    """Export graph as a PlantUML component diagram string."""
    lines = ["@startuml", "skinparam componentStyle rectangle", ""]

    grouped: dict[str, list] = {}
    for node in graph.nodes:
        grouped.setdefault(node.layer.value, []).append(node)

    for layer in LAYER_ORDER:
        nodes = grouped.get(layer.value, [])
        if not nodes:
            continue
        lines.append(f'package "{layer.value.title()}" {{')
        for node in nodes:
            sid = _safe_id(node.id)
            stereo = PLANTUML_STEREO.get(node.type.value, "")
            color = PLANTUML_COLOR.get(node.type.value, "")
            color_str = f" {color}" if color else ""
            lines.append(f'    [{node.name}] as {sid} {stereo}{color_str}')
        lines.append("}")
        lines.append("")

    for edge in graph.edges:
        src = _safe_id(edge.source)
        tgt = _safe_id(edge.target)
        label = f' : "{edge.label}"' if edge.label else ""
        lines.append(f"{src} --> {tgt}{label}")

    lines.append("")
    lines.append("@enduml")
    return "\n".join(lines)
