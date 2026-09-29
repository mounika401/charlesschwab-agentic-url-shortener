"""Dependency graph: validation, scheduling queries and runtime mutation."""

from __future__ import annotations

import fnmatch
from collections import defaultdict, deque

from .models import NodeSpec


class GraphError(ValueError):
    pass


class Graph:
    def __init__(self, nodes: list[NodeSpec] | None = None) -> None:
        self.nodes: dict[str, NodeSpec] = {}
        self.version = 0
        for node in nodes or []:
            self.add(node, validate=False)
        self.validate()

    # ---- mutation -----------------------------------------------------------------
    def add(self, node: NodeSpec, validate: bool = True) -> None:
        if node.id in self.nodes:
            raise GraphError(f"duplicate node id {node.id!r}")
        self.nodes[node.id] = node
        self.version += 1
        if validate:
            self.validate()

    def remove(self, node_id: str) -> None:
        self.nodes.pop(node_id)
        for node in self.nodes.values():
            if node_id in node.depends_on:
                node.depends_on.remove(node_id)
        self.version += 1

    def add_dependency(self, node_id: str, depends_on: str) -> None:
        node = self.nodes[node_id]
        if depends_on not in node.depends_on:
            node.depends_on.append(depends_on)
            self.version += 1
        self.validate()

    # ---- validation ---------------------------------------------------------------
    def validate(self) -> None:
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise GraphError(f"node {node.id!r} depends on unknown node {dep!r}")
        self.topological_order()  # raises on cycles

    def topological_order(self) -> list[str]:
        indegree = {nid: len(n.depends_on) for nid, n in self.nodes.items()}
        dependents = self.dependents_map()
        queue = deque(sorted(nid for nid, d in indegree.items() if d == 0))
        order: list[str] = []
        while queue:
            nid = queue.popleft()
            order.append(nid)
            for child in sorted(dependents[nid]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(self.nodes):
            cyclic = sorted(set(self.nodes) - set(order))
            raise GraphError(f"dependency cycle among {cyclic}")
        return order

    # ---- queries ------------------------------------------------------------------
    def dependents_map(self) -> dict[str, set[str]]:
        dependents: dict[str, set[str]] = defaultdict(set)
        for node in self.nodes.values():
            for dep in node.depends_on:
                dependents[dep].add(node.id)
        return dependents

    def descendants(self, node_id: str) -> set[str]:
        dependents = self.dependents_map()
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            for child in dependents[stack.pop()]:
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def levels(self) -> list[list[str]]:
        """Group nodes into parallelisable waves (longest-path layering)."""
        depth: dict[str, int] = {}
        for nid in self.topological_order():
            deps = self.nodes[nid].depends_on
            depth[nid] = 1 + max((depth[d] for d in deps), default=-1)
        waves: dict[int, list[str]] = defaultdict(list)
        for nid, d in depth.items():
            waves[d].append(nid)
        return [sorted(waves[i]) for i in sorted(waves)]

    def critical_path(self, weights: dict[str, float] | None = None) -> list[str]:
        weights = weights or {}
        best: dict[str, tuple[float, list[str]]] = {}
        for nid in self.topological_order():
            w = weights.get(nid, 1.0)
            prev = max((best[d] for d in self.nodes[nid].depends_on), default=(0.0, []), key=lambda x: x[0])
            best[nid] = (prev[0] + w, prev[1] + [nid])
        return max(best.values(), key=lambda x: x[0])[1] if best else []

    def to_mermaid(self, statuses: dict[str, str] | None = None) -> str:
        statuses = statuses or {}
        lines = ["graph LR"]
        for nid in self.topological_order():
            label = nid.replace('"', "'")
            status = statuses.get(nid)
            lines.append(f'    {safe_id(nid)}["{label}{"<br/>" + status if status else ""}"]')
        for node in self.nodes.values():
            # Transitive reduction for readability: skip dep->node when another
            # dependency of the node already (transitively) depends on dep.
            for dep in node.depends_on:
                implied = any(dep in self.ancestors(other) for other in node.depends_on if other != dep)
                if not implied:
                    lines.append(f"    {safe_id(dep)} --> {safe_id(node.id)}")
        return "\n".join(lines)

    def ancestors(self, node_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.nodes[node_id].depends_on)
        while stack:
            dep = stack.pop()
            if dep not in seen:
                seen.add(dep)
                stack.extend(self.nodes[dep].depends_on)
        return seen


def safe_id(node_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in node_id)


def scopes_overlap(a: list[str], b: list[str]) -> bool:
    """Conservative overlap check between two lists of path globs."""
    for pa in a:
        for pb in b:
            if pa == pb or fnmatch.fnmatch(pa, pb) or fnmatch.fnmatch(pb, pa):
                return True
            # Directory globs like "docs/**" overlap anything under docs/.
            ra, rb = pa.split("*", 1)[0], pb.split("*", 1)[0]
            if ra and rb and (ra.startswith(rb) or rb.startswith(ra)) and ("*" in pa or "*" in pb):
                return True
    return False


def in_scope(path: str, scope: list[str]) -> bool:
    return any(path == pattern or fnmatch.fnmatch(path, pattern) for pattern in scope)
