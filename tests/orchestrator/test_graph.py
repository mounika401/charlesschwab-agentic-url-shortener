import pytest

from orchestrator.graph import Graph, GraphError, in_scope, scopes_overlap
from orchestrator.models import NodeSpec


def node(nid, *deps, scope=()):
    return NodeSpec(nid, "stage", "agent", depends_on=list(deps), write_scope=list(scope))


def test_topological_order_and_levels():
    g = Graph([node("a"), node("b", "a"), node("c", "a"), node("d", "b", "c")])
    order = g.topological_order()
    assert order.index("a") < order.index("b") < order.index("d")
    assert g.levels() == [["a"], ["b", "c"], ["d"]]


def test_cycle_and_unknown_dependency_are_rejected():
    with pytest.raises(GraphError, match="cycle"):
        Graph([node("a", "b"), node("b", "a")])
    with pytest.raises(GraphError, match="unknown"):
        Graph([node("a", "ghost")])


def test_critical_path_uses_weights():
    g = Graph([node("a"), node("b", "a"), node("c", "a"), node("d", "b", "c")])
    assert g.critical_path({"a": 1, "b": 5, "c": 1, "d": 1}) == ["a", "b", "d"]


def test_descendants_and_ancestors():
    g = Graph([node("a"), node("b", "a"), node("c", "b")])
    assert g.descendants("a") == {"b", "c"}
    assert g.ancestors("c") == {"a", "b"}


def test_runtime_mutation_keeps_graph_valid():
    g = Graph([node("a"), node("b", "a")])
    g.add(node("c", "b"))
    g.remove("b")
    assert g.nodes["c"].depends_on == []
    with pytest.raises(GraphError):
        g.add(node("d", "missing"))


@pytest.mark.parametrize("a,b,expected", [
    (["shortener/app.py"], ["shortener/app.py"], True),
    (["docs/**"], ["docs/API.md"], True),
    (["shortener/app.py"], ["shortener/db.py"], False),
    (["docs/design/**"], ["docs/security/**"], False),
    (["shortener/*.py"], ["shortener/app.py"], True),
])
def test_scope_overlap(a, b, expected):
    assert scopes_overlap(a, b) is expected


def test_in_scope():
    assert in_scope("docs/adr/ADR-1.md", ["docs/adr/**"])
    assert not in_scope("shortener/app.py", ["docs/**"])


def test_mermaid_uses_transitive_reduction():
    g = Graph([node("a"), node("b", "a"), node("c", "a", "b")])
    mermaid = g.to_mermaid()
    assert "a --> b" in mermaid and "b --> c" in mermaid
    assert "a --> c" not in mermaid
