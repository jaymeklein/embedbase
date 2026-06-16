"""Unit tests for the pure ``build_graph`` transform."""

from typing import Any

from api.services.graph import build_graph


def _tag(tid: str, name: str, color: str | None = None) -> dict[str, Any]:
    return {"id": tid, "name": name, "color": color}


def _file(fid: str, *tags: dict[str, Any], file_type: str = "txt") -> dict[str, Any]:
    return {"id": fid, "label": f"{fid}.{file_type}", "file_type": file_type, "tags": list(tags)}


def test_empty_input_yields_empty_graph() -> None:
    g = build_graph([], ["tags"])
    assert g.nodes == []
    assert g.edges == []
    assert g.tag_counts == {}
    assert g.max_heat == 0


def test_tag_on_n_files_is_one_hub_with_degree_n() -> None:
    py = _tag("t_py", "python")
    files = [_file("d1", py), _file("d2", py), _file("d3", py)]

    g = build_graph(files, ["tags"])

    tag_nodes = [n for n in g.nodes if n.kind == "tag"]
    assert len(tag_nodes) == 1
    hub = tag_nodes[0]
    assert hub.id == "t_py"
    assert hub.heat == 3
    assert hub.degree == 3
    assert g.max_heat == 3
    assert g.tag_counts == {"python": 3}


def test_heat_pct_is_normalized_to_max() -> None:
    hot = _tag("t_hot", "hot")
    cold = _tag("t_cold", "cold")
    files = [_file("d1", hot, cold), _file("d2", hot), _file("d3", hot), _file("d4", hot)]

    g = build_graph(files, ["tags"])
    by_id = {n.id: n for n in g.nodes}

    assert g.max_heat == 4
    assert by_id["t_hot"].heat_pct == 1.0
    assert by_id["t_cold"].heat_pct == 0.25
    assert all(0.0 <= n.heat_pct <= 1.0 for n in g.nodes)


def test_file_node_degree_equals_its_tag_count() -> None:
    files = [_file("d1", _tag("a", "a"), _tag("b", "b"))]

    g = build_graph(files, ["tags"])
    file_node = next(n for n in g.nodes if n.kind == "file")

    assert file_node.degree == 2
    assert file_node.heat == 0
    assert file_node.meta == {"file_type": "txt"}


def test_edge_count_is_sum_of_per_file_tags() -> None:
    files = [_file("d1", _tag("a", "a"), _tag("b", "b")), _file("d2", _tag("a", "a"))]

    g = build_graph(files, ["tags"])

    assert len(g.edges) == 3
    assert {(e.source, e.target) for e in g.edges} == {
        ("d1", "a"),
        ("d1", "b"),
        ("d2", "a"),
    }


def test_unknown_link_type_yields_files_only() -> None:
    files = [_file("d1", _tag("a", "a"))]

    g = build_graph(files, ["language"])

    assert [n.kind for n in g.nodes] == ["file"]
    assert g.edges == []
    assert g.tag_counts == {}
    assert g.max_heat == 0


def test_untagged_files_produce_no_edges_or_hubs() -> None:
    g = build_graph([_file("d1"), _file("d2")], ["tags"])

    assert {n.kind for n in g.nodes} == {"file"}
    assert g.edges == []
    assert g.max_heat == 0
