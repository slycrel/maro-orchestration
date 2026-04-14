"""Tests for codebase_graph.py — AST-based Python call graph."""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from codebase_graph import (
    CodebaseGraph,
    FileNode,
    FunctionNode,
    build_codebase_graph,
    format_graph_context,
    find_files_for_goal,
    _module_path,
    _collect_imports,
    _collect_calls,
    _collect_top_level_defs,
)
import ast


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

class TestModulePath:
    def test_simple_file(self):
        assert _module_path("src/llm.py") == "src.llm"

    def test_nested_file(self):
        assert _module_path("src/utils/helper.py") == "src.utils.helper"

    def test_init_stripped(self):
        assert _module_path("src/__init__.py") == "src"

    def test_top_level_file(self):
        assert _module_path("main.py") == "main"


class TestCollectImports:
    def test_regular_import(self):
        tree = ast.parse("import os\nimport sys")
        imports = _collect_imports(tree)
        assert "os" in imports
        assert "sys" in imports

    def test_from_import(self):
        tree = ast.parse("from pathlib import Path\nfrom typing import Optional")
        imports = _collect_imports(tree)
        assert "pathlib" in imports
        assert "typing" in imports

    def test_empty_file(self):
        tree = ast.parse("")
        assert _collect_imports(tree) == []


class TestCollectCalls:
    def test_simple_call(self):
        tree = ast.parse("def f():\n    os.getcwd()\n    print('x')")
        func = tree.body[0]
        calls = _collect_calls(func)
        assert "getcwd" in calls or "os.getcwd" in calls
        assert "print" in calls

    def test_no_calls(self):
        tree = ast.parse("def f():\n    x = 1 + 2")
        func = tree.body[0]
        calls = _collect_calls(func)
        assert calls == []


class TestCollectTopLevelDefs:
    def test_functions_and_classes(self):
        src = "def foo():\n    pass\ndef bar():\n    pass\nclass MyClass:\n    def method(self):\n        pass\n"
        tree = ast.parse(src)
        funcs, classes = _collect_top_level_defs(tree)
        assert "foo" in funcs
        assert "bar" in funcs
        assert "MyClass.method" in funcs
        assert "MyClass" in classes

    def test_empty_module(self):
        tree = ast.parse("")
        funcs, classes = _collect_top_level_defs(tree)
        assert funcs == []
        assert classes == []


# ---------------------------------------------------------------------------
# build_codebase_graph
# ---------------------------------------------------------------------------

class TestBuildCodebaseGraphMissing:
    def test_missing_path_returns_error(self):
        g = build_codebase_graph("/tmp/definitely-does-not-exist-xyzzy")
        assert g.error != ""
        assert g.total_files == 0


class TestBuildCodebaseGraphSimple:
    def test_single_file_scanned(self, tmp_path):
        (tmp_path / "main.py").write_text("def hello():\n    pass\n")
        g = build_codebase_graph(str(tmp_path))
        assert g.error == ""
        assert g.total_files >= 1
        assert "main.py" in g.files

    def test_import_resolution_increments_in_degree(self, tmp_path):
        (tmp_path / "llm.py").write_text("def complete(): pass\n")
        (tmp_path / "agent.py").write_text("from llm import complete\ndef run(): complete()\n")
        g = build_codebase_graph(str(tmp_path))
        assert g.files["llm.py"].in_degree >= 1

    def test_multiple_importers_count_correctly(self, tmp_path):
        (tmp_path / "config.py").write_text("DEBUG = True\n")
        (tmp_path / "a.py").write_text("from config import DEBUG\n")
        (tmp_path / "b.py").write_text("import config\n")
        (tmp_path / "c.py").write_text("from config import DEBUG\n")
        g = build_codebase_graph(str(tmp_path))
        assert g.files["config.py"].in_degree >= 2

    def test_centrality_ordered_by_in_degree(self, tmp_path):
        (tmp_path / "hub.py").write_text("X = 1\n")
        (tmp_path / "leaf.py").write_text("Y = 2\n")
        # hub imported by 3 files, leaf by 0
        for i in range(3):
            (tmp_path / f"user{i}.py").write_text(f"from hub import X\n")
        g = build_codebase_graph(str(tmp_path))
        top_file = g.ranked_files[0]
        assert "hub" in top_file

    def test_syntax_error_file_skipped(self, tmp_path):
        (tmp_path / "good.py").write_text("x = 1\n")
        (tmp_path / "bad.py").write_text("def f(:\n    pass\n")  # syntax error
        g = build_codebase_graph(str(tmp_path))
        assert "good.py" in g.files
        # bad.py may or may not be in files, but must not crash
        assert g.error == ""

    def test_classes_detected(self, tmp_path):
        (tmp_path / "models.py").write_text("class Recipe:\n    pass\nclass Review:\n    pass\n")
        g = build_codebase_graph(str(tmp_path))
        assert "Recipe" in g.files["models.py"].classes
        assert "Review" in g.files["models.py"].classes

    def test_functions_detected(self, tmp_path):
        (tmp_path / "utils.py").write_text("def foo(): pass\ndef bar(): pass\n")
        g = build_codebase_graph(str(tmp_path))
        assert "foo" in g.files["utils.py"].functions
        assert "bar" in g.files["utils.py"].functions

    def test_max_files_cap_respected(self, tmp_path):
        for i in range(20):
            (tmp_path / f"file{i}.py").write_text(f"x{i} = {i}\n")
        g = build_codebase_graph(str(tmp_path), max_files=5)
        assert g.total_files <= 5

    def test_exclude_dirs_respected(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "site.py").write_text("x = 1\n")
        (tmp_path / "main.py").write_text("x = 1\n")
        g = build_codebase_graph(str(tmp_path))
        # venv/site.py should be excluded
        assert all("venv" not in r for r in g.files)

    def test_line_counts_populated(self, tmp_path):
        code = "x = 1\n" * 50
        (tmp_path / "big.py").write_text(code)
        g = build_codebase_graph(str(tmp_path))
        assert g.files["big.py"].lines >= 50

    def test_ranked_files_nonempty_for_valid_repo(self, tmp_path):
        (tmp_path / "main.py").write_text("def main(): pass\n")
        g = build_codebase_graph(str(tmp_path))
        assert len(g.ranked_files) >= 1

    def test_total_functions_counts_all(self, tmp_path):
        (tmp_path / "a.py").write_text("def f1(): pass\ndef f2(): pass\n")
        (tmp_path / "b.py").write_text("def g1(): pass\n")
        g = build_codebase_graph(str(tmp_path))
        assert g.total_functions >= 3


class TestBuildCodebaseGraphDepth:
    def test_nested_files_found_within_depth(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.py").write_text("def run(): pass\n")
        g = build_codebase_graph(str(tmp_path), max_depth=2)
        assert "src/app.py" in g.files

    def test_deep_files_excluded_beyond_depth(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("x = 1\n")
        g = build_codebase_graph(str(tmp_path), max_depth=1)
        assert all("d/deep.py" not in r for r in g.files)


# ---------------------------------------------------------------------------
# format_graph_context
# ---------------------------------------------------------------------------

class TestFormatGraphContext:
    def _make_graph(self, tmp_path) -> CodebaseGraph:
        (tmp_path / "core.py").write_text("def process(): pass\n")
        (tmp_path / "helper.py").write_text("from core import process\n")
        return build_codebase_graph(str(tmp_path))

    def test_returns_empty_on_error(self):
        g = CodebaseGraph(repo_path="/tmp/x", error="not found")
        assert format_graph_context(g) == ""

    def test_includes_codebase_graph_header(self, tmp_path):
        g = self._make_graph(tmp_path)
        ctx = format_graph_context(g)
        assert "CODEBASE GRAPH" in ctx

    def test_includes_top_files(self, tmp_path):
        g = self._make_graph(tmp_path)
        ctx = format_graph_context(g, top_files=2)
        assert "core.py" in ctx or "helper.py" in ctx

    def test_includes_file_count(self, tmp_path):
        g = self._make_graph(tmp_path)
        ctx = format_graph_context(g)
        assert "files" in ctx.lower() or str(g.total_files) in ctx

    def test_goal_biased_ranking(self, tmp_path):
        (tmp_path / "reviews.py").write_text("def get_reviews(): pass\n")
        (tmp_path / "unrelated.py").write_text("def unrelated(): pass\n")
        g = build_codebase_graph(str(tmp_path))
        ctx = format_graph_context(g, goal="fix the reviews endpoint", top_files=3)
        # reviews.py should appear early since goal mentions "reviews"
        assert "reviews.py" in ctx

    def test_top_files_param_limits_output(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"x = {i}\n")
        g = build_codebase_graph(str(tmp_path))
        ctx = format_graph_context(g, top_files=2)
        # Only 2 files in the top files section
        file_lines = [l for l in ctx.splitlines() if ".py" in l and "L)" in l]
        assert len(file_lines) <= 3  # allow slight flex


# ---------------------------------------------------------------------------
# find_files_for_goal
# ---------------------------------------------------------------------------

class TestFindFilesForGoal:
    def test_empty_goal_returns_top_ranked(self, tmp_path):
        (tmp_path / "hub.py").write_text("x = 1\n")
        (tmp_path / "leaf.py").write_text("from hub import x\n")
        g = build_codebase_graph(str(tmp_path))
        result = find_files_for_goal(g, "", limit=1)
        assert len(result) == 1

    def test_goal_keyword_boosts_matching_file(self, tmp_path):
        (tmp_path / "reviews_api.py").write_text("def get_reviews(): pass\n")
        (tmp_path / "main.py").write_text("from reviews_api import get_reviews\ndef run(): pass\n")
        g = build_codebase_graph(str(tmp_path))
        result = find_files_for_goal(g, "fix the review aggregation bug", limit=3)
        assert any("reviews" in r for r in result)

    def test_limit_respected(self, tmp_path):
        for i in range(10):
            (tmp_path / f"m{i}.py").write_text(f"x = {i}\n")
        g = build_codebase_graph(str(tmp_path))
        result = find_files_for_goal(g, "some goal", limit=3)
        assert len(result) <= 3

    def test_error_graph_returns_empty_for_goal(self):
        g = CodebaseGraph(repo_path="/tmp/x", error="not found")
        result = find_files_for_goal(g, "some goal")
        assert result == []


# ---------------------------------------------------------------------------
# Self-scan (integration smoke test)
# ---------------------------------------------------------------------------

class TestSelfScan:
    """Scan this repo and verify known structural facts."""

    def test_llm_py_is_central(self):
        root = str(Path(__file__).parent.parent)
        g = build_codebase_graph(root, max_files=150)
        assert g.error == ""
        top10 = g.ranked_files[:10]
        assert any("llm.py" in r for r in top10), f"llm.py not in top10: {top10}"

    def test_agent_loop_ranked_high(self):
        root = str(Path(__file__).parent.parent)
        g = build_codebase_graph(root, max_files=150)
        top20 = g.ranked_files[:20]
        assert any("agent_loop.py" in r for r in top20)

    def test_total_functions_reasonable(self):
        root = str(Path(__file__).parent.parent)
        g = build_codebase_graph(root, max_files=150)
        assert g.total_functions > 100  # large codebase
