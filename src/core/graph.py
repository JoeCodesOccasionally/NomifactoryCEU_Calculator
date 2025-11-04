from __future__ import annotations

from pathlib import Path
from typing import Optional

import networkx as nx

try:
    import pygraphviz as pgv  # optional
except Exception:  # pragma: no cover - optional
    pgv = None  # type: ignore

from .models import Plan, PlanNode


def to_networkx(root: PlanNode) -> nx.DiGraph:
    g = nx.DiGraph()

    def walk(n: PlanNode):
        nid = id(n)
        label = n.item_display or n.item
        g.add_node(
            nid,
            label=label,
            machine=n.machine,
            tier=n.machine_tier,
            machines=n.machines_needed,
            eut=n.effective_eut,
        )
        for c in n.children:
            cid = id(c)
            g.add_edge(nid, cid)
            walk(c)

    walk(root)
    return g


def export_graphviz(root: PlanNode, out_path: str) -> None:
    if pgv is None:
        raise RuntimeError("pygraphviz is not installed; cannot export graph")
    G = to_networkx(root)
    A = nx.nx_agraph.to_agraph(G)
    A.graph_attr.update(rankdir="LR")
    A.layout("dot")
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    A.draw(str(p))

