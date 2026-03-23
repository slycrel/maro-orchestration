"""Tests for ancestry.py — goal ancestry chain (§18 poe_orchestration_spec.md)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ancestry import (
    AncestryNode,
    ProjectAncestry,
    build_ancestry_prompt,
    create_child_ancestry,
    get_project_ancestry,
    list_child_projects,
    orch_ancestry,
    orch_impact,
    set_project_ancestry,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

def test_ancestry_node_fields():
    n = AncestryNode(id="m1", title="Mission One")
    assert n.id == "m1"
    assert n.title == "Mission One"


def test_project_ancestry_depth_empty():
    a = ProjectAncestry(parent_id=None)
    assert a.depth() == 0


def test_project_ancestry_depth_with_nodes():
    a = ProjectAncestry(
        parent_id="p1",
        ancestry=[AncestryNode("m", "M"), AncestryNode("p1", "P1")],
    )
    assert a.depth() == 2


def test_project_ancestry_to_dict():
    a = ProjectAncestry(
        parent_id="p1",
        ancestry=[AncestryNode("m", "Mission")],
    )
    d = a.to_dict()
    assert d["parent_id"] == "p1"
    assert d["ancestry"] == [{"id": "m", "title": "Mission"}]


def test_project_ancestry_from_dict_roundtrip():
    original = ProjectAncestry(
        parent_id="parent-slug",
        ancestry=[
            AncestryNode("mission-001", "Build self-leveling assistant"),
            AncestryNode("obj-abc", "Add autonomous loop"),
        ],
    )
    restored = ProjectAncestry.from_dict(original.to_dict())
    assert restored.parent_id == original.parent_id
    assert len(restored.ancestry) == 2
    assert restored.ancestry[0].title == "Build self-leveling assistant"


def test_project_ancestry_from_dict_empty():
    a = ProjectAncestry.from_dict({})
    assert a.parent_id is None
    assert a.ancestry == []


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def test_get_project_ancestry_no_file(tmp_path):
    assert get_project_ancestry(tmp_path / "empty") is None


def test_set_and_get_ancestry_roundtrip(tmp_path):
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    ancestry = ProjectAncestry(
        parent_id="root-project",
        ancestry=[AncestryNode("root-project", "Top Level Goal")],
    )
    set_project_ancestry(project_dir, ancestry)
    loaded = get_project_ancestry(project_dir)
    assert loaded is not None
    assert loaded.parent_id == "root-project"
    assert loaded.ancestry[0].id == "root-project"


def test_get_project_ancestry_corrupt_file(tmp_path):
    project_dir = tmp_path / "bad"
    project_dir.mkdir()
    (project_dir / "ancestry.json").write_text("not json", encoding="utf-8")
    assert get_project_ancestry(project_dir) is None


# ---------------------------------------------------------------------------
# create_child_ancestry
# ---------------------------------------------------------------------------

def test_create_child_ancestry_from_root(tmp_path):
    parent_dir = tmp_path / "parent"
    parent_dir.mkdir()
    # Parent has no ancestry.json (it's a root project)
    child = create_child_ancestry("parent", "Parent Goal", parent_dir)
    assert child.parent_id == "parent"
    assert len(child.ancestry) == 1
    assert child.ancestry[0].id == "parent"
    assert child.ancestry[0].title == "Parent Goal"


def test_create_child_ancestry_inherits_chain(tmp_path):
    grandparent_dir = tmp_path / "gp"
    grandparent_dir.mkdir()
    parent_dir = tmp_path / "parent"
    parent_dir.mkdir()

    # Set up grandparent as root
    gp_ancestry = ProjectAncestry(
        parent_id=None,
        ancestry=[],
    )
    set_project_ancestry(grandparent_dir, gp_ancestry)

    # Set up parent with grandparent as its ancestry
    parent_ancestry = ProjectAncestry(
        parent_id="gp",
        ancestry=[AncestryNode("gp", "Top Mission")],
    )
    set_project_ancestry(parent_dir, parent_ancestry)

    # Create child of parent
    child = create_child_ancestry("parent", "Sub-goal", parent_dir)
    assert child.parent_id == "parent"
    assert len(child.ancestry) == 2
    assert child.ancestry[0].id == "gp"      # inherited from parent
    assert child.ancestry[1].id == "parent"  # parent appended


# ---------------------------------------------------------------------------
# build_ancestry_prompt
# ---------------------------------------------------------------------------

def test_build_ancestry_prompt_empty():
    assert build_ancestry_prompt(None) == ""
    assert build_ancestry_prompt(ProjectAncestry(parent_id=None)) == ""


def test_build_ancestry_prompt_basic():
    a = ProjectAncestry(
        parent_id="p",
        ancestry=[
            AncestryNode("mission", "Build self-leveling assistant"),
            AncestryNode("p", "Add autonomous loop"),
        ],
    )
    prompt = build_ancestry_prompt(a, current_task="Write WebSocket handler")
    assert "Goal Ancestry" in prompt
    assert "Build self-leveling assistant" in prompt
    assert "Add autonomous loop" in prompt
    assert "Write WebSocket handler" in prompt


def test_build_ancestry_prompt_truncates_deep_chain():
    nodes = [AncestryNode(f"g{i}", f"Goal {i}") for i in range(15)]
    a = ProjectAncestry(parent_id="g14", ancestry=nodes)
    prompt = build_ancestry_prompt(a)
    # Should not include all 15 raw lines — truncation kicks in
    assert "..." in prompt


def test_build_ancestry_prompt_no_current_task():
    a = ProjectAncestry(
        parent_id="p",
        ancestry=[AncestryNode("p", "Top Mission")],
    )
    prompt = build_ancestry_prompt(a)
    assert "Current Task" not in prompt


# ---------------------------------------------------------------------------
# list_child_projects
# ---------------------------------------------------------------------------

def test_list_child_projects_none(tmp_path):
    children = list_child_projects("parent-slug", tmp_path)
    assert children == []


def test_list_child_projects_finds_children(tmp_path):
    # Create two child projects and one unrelated
    for slug in ["child-a", "child-b", "unrelated"]:
        d = tmp_path / slug
        d.mkdir()

    ancestry_a = ProjectAncestry(parent_id="parent-slug", ancestry=[])
    ancestry_b = ProjectAncestry(parent_id="parent-slug", ancestry=[])
    ancestry_u = ProjectAncestry(parent_id="other-parent", ancestry=[])

    set_project_ancestry(tmp_path / "child-a", ancestry_a)
    set_project_ancestry(tmp_path / "child-b", ancestry_b)
    set_project_ancestry(tmp_path / "unrelated", ancestry_u)

    children = list_child_projects("parent-slug", tmp_path)
    assert set(children) == {"child-a", "child-b"}


# ---------------------------------------------------------------------------
# orch_ancestry CLI helper
# ---------------------------------------------------------------------------

def test_orch_ancestry_no_file(tmp_path):
    d = tmp_path / "myproject"
    d.mkdir()
    lines = orch_ancestry("myproject", d)
    assert len(lines) == 1
    assert "root" in lines[0]


def test_orch_ancestry_with_chain(tmp_path):
    d = tmp_path / "leaf"
    d.mkdir()
    a = ProjectAncestry(
        parent_id="mid",
        ancestry=[
            AncestryNode("top", "Top Mission"),
            AncestryNode("mid", "Middle Goal"),
        ],
    )
    set_project_ancestry(d, a)
    lines = orch_ancestry("leaf", d)
    assert any("Top Mission" in l for l in lines)
    assert any("Middle Goal" in l for l in lines)
    assert lines[-1] == "→ leaf (current)"


# ---------------------------------------------------------------------------
# orch_impact CLI helper
# ---------------------------------------------------------------------------

def test_orch_impact_no_descendants(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    result = orch_impact("root", tmp_path)
    assert result == []


def test_orch_impact_finds_descendants(tmp_path):
    # root → child-a, child-b → grandchild (child of child-a)
    for slug in ["root", "child-a", "child-b", "grandchild"]:
        (tmp_path / slug).mkdir()

    set_project_ancestry(tmp_path / "child-a", ProjectAncestry(parent_id="root", ancestry=[]))
    set_project_ancestry(tmp_path / "child-b", ProjectAncestry(parent_id="root", ancestry=[]))
    set_project_ancestry(tmp_path / "grandchild", ProjectAncestry(parent_id="child-a", ancestry=[]))

    result = orch_impact("root", tmp_path)
    # Should find child-a and child-b (depth 1), then grandchild (depth 2)
    assert "child-a" in result
    assert "child-b" in result
    assert "grandchild" in result
    assert "root" not in result


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def _make_project(tmp_path, slug, mission=None):
    """Create a minimal project dir (fake orch_root structure)."""
    import subprocess
    d = tmp_path / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "NEXT.md").write_text(f"# {slug}\n\n- [ ] first task\n", encoding="utf-8")
    (d / "DECISIONS.md").write_text("", encoding="utf-8")
    (d / "config.json").write_text(
        json.dumps({"slug": slug, "mission": mission or slug, "priority": 5}),
        encoding="utf-8",
    )
    return d


def test_cli_ancestry_not_found(tmp_path, monkeypatch, capsys):
    import cli
    monkeypatch.setattr(cli, "project_dir", lambda slug: tmp_path / "nonexistent" / slug)
    rc = cli.main(["ancestry", "nonexistent"])
    assert rc != 0


def test_cli_ancestry_root_project(tmp_path, monkeypatch, capsys):
    import cli
    d = _make_project(tmp_path, "myproject")
    monkeypatch.setattr(cli, "project_dir", lambda slug: d if slug == "myproject" else tmp_path / slug)
    rc = cli.main(["ancestry", "myproject"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "root" in out


def test_cli_impact_no_descendants(tmp_path, monkeypatch, capsys):
    import cli
    d = _make_project(tmp_path, "lonely")
    monkeypatch.setattr(cli, "project_dir", lambda slug: d if slug == "lonely" else tmp_path / slug)
    rc = cli.main(["impact", "lonely"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "none" in out.lower()
