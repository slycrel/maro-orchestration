"""AST-based Python codebase call graph for ranked context injection.

Implements the SoulForge/bniwael steal: pre-build a ranked call graph before the
agent reads any file. Use betweenness/in-degree centrality to identify the most
important files and functions, then inject a compact summary into agent context.

This lets the agent navigate large codebases surgically — starting with the most
central files instead of guessing or reading from the top.

Usage:
    from codebase_graph import build_codebase_graph, format_graph_context
    graph = build_codebase_graph("/path/to/repo")
    context = format_graph_context(graph, goal="fix the N+1 query bug")

CLI:
    poe-codebase-graph [--path .] [--top 10] [--json] [--context]
"""

from __future__ import annotations

import ast
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FunctionNode:
    """A function or method in the codebase."""
    name: str
    module: str          # dotted module path
    file_path: str       # relative path from repo root
    line: int
    calls: List[str] = field(default_factory=list)      # names called by this function
    called_by: List[str] = field(default_factory=list)  # names that call this function

    @property
    def qualified_name(self) -> str:
        return f"{self.module}.{self.name}"


@dataclass
class FileNode:
    """A Python file in the codebase."""
    file_path: str        # relative path from repo root
    module: str           # dotted module path
    imports: List[str] = field(default_factory=list)     # imported modules
    imported_by: List[str] = field(default_factory=list) # modules that import this
    functions: List[str] = field(default_factory=list)   # function names defined here
    classes: List[str] = field(default_factory=list)     # class names defined here
    lines: int = 0
    in_degree: int = 0   # how many files import this
    centrality: float = 0.0  # normalized importance score


@dataclass
class CodebaseGraph:
    """Call graph of a Python codebase."""
    repo_path: str
    files: Dict[str, FileNode] = field(default_factory=dict)        # rel_path → FileNode
    functions: Dict[str, FunctionNode] = field(default_factory=dict) # qualified → FunctionNode
    ranked_files: List[str] = field(default_factory=list)            # rel_paths, most central first
    ranked_functions: List[str] = field(default_factory=list)        # qualified names, most central first
    total_files: int = 0
    total_functions: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# AST walker
# ---------------------------------------------------------------------------

def _module_path(rel_path: str) -> str:
    """Convert relative file path to dotted module name.
    e.g. "src/agent_loop.py" → "src.agent_loop"
    """
    p = Path(rel_path)
    parts = list(p.parts)
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else ""


def _collect_imports(tree: ast.AST) -> List[str]:
    """Extract all imported module names from an AST."""
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def _collect_calls(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> List[str]:
    """Extract function/method call names from a function AST node."""
    calls = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            # func.attr() → "func.attr"
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    calls.append(f"{node.func.value.id}.{node.func.attr}")
                else:
                    calls.append(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.append(node.func.id)
    return calls


def _collect_top_level_defs(tree: ast.AST) -> Tuple[List[str], List[str]]:
    """Return (function_names, class_names) defined at top level."""
    funcs = []
    classes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
            # Also collect methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.append(f"{node.name}.{item.name}")
    return funcs, classes


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_codebase_graph(
    repo_path: str,
    *,
    max_files: int = 200,
    max_depth: int = 3,
    exclude_dirs: Optional[Set[str]] = None,
) -> CodebaseGraph:
    """Build a call graph from a Python codebase.

    Args:
        repo_path:    Path to the repository root.
        max_files:    Cap on number of Python files to scan (avoid huge repos).
        max_depth:    Directory depth to scan.
        exclude_dirs: Directory names to skip.

    Returns:
        CodebaseGraph with ranked files and functions.
    """
    root = Path(repo_path).expanduser().resolve()
    if not root.exists():
        return CodebaseGraph(repo_path=str(root), error=f"path not found: {root}")

    if exclude_dirs is None:
        exclude_dirs = {
            "node_modules", "__pycache__", ".git", "venv", ".venv",
            "dist", "build", "target", ".tox", ".pytest_cache",
        }

    graph = CodebaseGraph(repo_path=str(root))
    file_nodes: Dict[str, FileNode] = {}
    func_nodes: Dict[str, FunctionNode] = {}

    # --- Pass 1: collect all .py files ---
    py_files: List[Path] = []

    def _walk(path: Path, depth: int = 0):
        if depth > max_depth or len(py_files) >= max_files:
            return
        try:
            for entry in sorted(path.iterdir()):
                if len(py_files) >= max_files:
                    return
                if entry.name.startswith(".") and entry.name not in (".github",):
                    continue
                if entry.is_file() and entry.suffix == ".py":
                    py_files.append(entry)
                elif entry.is_dir() and entry.name not in exclude_dirs:
                    _walk(entry, depth + 1)
        except (PermissionError, OSError):
            pass

    _walk(root)

    # --- Pass 2: parse each file ---
    module_to_rel: Dict[str, str] = {}  # module dotted path → rel_path

    for py_path in py_files:
        rel = str(py_path.relative_to(root))
        module = _module_path(rel)
        module_to_rel[module] = rel

        try:
            source = py_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(py_path))
        except SyntaxError:
            continue

        lines = source.count("\n") + 1
        imports = _collect_imports(tree)
        funcs, classes = _collect_top_level_defs(tree)

        fn = FileNode(
            file_path=rel,
            module=module,
            imports=imports,
            functions=funcs,
            classes=classes,
            lines=lines,
        )
        file_nodes[rel] = fn

        # Register function nodes
        for func_body in ast.walk(tree):
            if isinstance(func_body, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{module}.{func_body.name}"
                calls = _collect_calls(func_body)
                func_nodes[qname] = FunctionNode(
                    name=func_body.name,
                    module=module,
                    file_path=rel,
                    line=func_body.lineno,
                    calls=calls,
                )

    # --- Pass 3: resolve imports → in-degree edges ---
    # Build basename → rel_path for bare-name imports (e.g. "from llm import ..." → "src/llm.py")
    basename_to_rel: Dict[str, List[str]] = defaultdict(list)
    for m, r in module_to_rel.items():
        # Last component of dotted path: "src.llm" → "llm"
        basename = m.rsplit(".", 1)[-1]
        basename_to_rel[basename].append(r)

    def _resolve_import(imp: str, current_rel: str) -> Optional[str]:
        """Try to resolve an import name to a file path."""
        # 1. Exact match on full dotted module
        if imp in module_to_rel:
            return module_to_rel[imp]
        # 2. Basename match (most common: "from llm import ...")
        bare = imp.rsplit(".", 1)[-1]
        candidates = basename_to_rel.get(bare, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # Prefer same directory
            current_dir = str(Path(current_rel).parent)
            for c in candidates:
                if str(Path(c).parent) == current_dir:
                    return c
            return candidates[0]
        # 3. Prefix match: "src.agent_loop" startswith "src"
        for m, r in module_to_rel.items():
            if m.startswith(imp + ".") or imp.startswith(m + "."):
                return r
        return None

    for rel, fn in file_nodes.items():
        seen_targets: Set[str] = set()
        for imp in fn.imports:
            target_rel = _resolve_import(imp, rel)
            if target_rel and target_rel != rel and target_rel not in seen_targets:
                seen_targets.add(target_rel)
                file_nodes[target_rel].in_degree += 1
                file_nodes[target_rel].imported_by.append(fn.module)

    # --- Pass 4: compute centrality scores ---
    # Combine in-degree (import count) + file size signal
    max_in_degree = max((fn.in_degree for fn in file_nodes.values()), default=1) or 1
    max_lines = max((fn.lines for fn in file_nodes.values()), default=1) or 1

    for fn in file_nodes.values():
        # Normalized in-degree (0-1) × 0.7 + normalized size (0-1) × 0.3
        # In-degree dominates: a file imported everywhere is always central
        import_score = fn.in_degree / max_in_degree
        size_score = min(fn.lines / max_lines, 1.0)
        fn.centrality = import_score * 0.7 + size_score * 0.3

    # --- Pass 5: rank ---
    graph.files = file_nodes
    graph.functions = func_nodes
    graph.total_files = len(file_nodes)
    graph.total_functions = len(func_nodes)

    graph.ranked_files = sorted(
        file_nodes.keys(),
        key=lambda r: file_nodes[r].centrality,
        reverse=True,
    )

    # Function ranking: by in-degree of their file × call count (crude proxy for importance)
    func_with_score: List[Tuple[str, float]] = []
    for qname, f in func_nodes.items():
        file_centrality = file_nodes.get(f.file_path, FileNode(f.file_path, f.module)).centrality
        call_score = min(len(f.calls) / 10.0, 1.0)
        func_with_score.append((qname, file_centrality * 0.8 + call_score * 0.2))
    func_with_score.sort(key=lambda x: x[1], reverse=True)
    graph.ranked_functions = [q for q, _ in func_with_score]

    return graph


# ---------------------------------------------------------------------------
# Context formatter
# ---------------------------------------------------------------------------

def format_graph_context(
    graph: CodebaseGraph,
    *,
    goal: str = "",
    top_files: int = 8,
    top_functions: int = 10,
) -> str:
    """Format codebase graph as a compact injection block for agent context.

    When a goal is provided, filters toward files with names related to the goal.

    Returns a string suitable for injecting into decompose or execute context.
    """
    if graph.error:
        return ""

    lines = ["CODEBASE GRAPH:"]

    # Goal-biased file selection: exact path fragment matches go first
    top = graph.ranked_files[:top_files * 3]  # wider set, then filter
    if goal:
        goal_words = {w.lower().strip(".,") for w in goal.split() if len(w) > 3}
        biased = [r for r in top if any(w in r.lower() for w in goal_words)]
        rest = [r for r in top if r not in set(biased)]
        top = (biased + rest)[:top_files]
    else:
        top = top[:top_files]

    if top:
        lines.append("Top files by import centrality:")
        for rel in top:
            fn = graph.files[rel]
            funcs_preview = ", ".join(fn.functions[:4])
            if len(fn.functions) > 4:
                funcs_preview += f" (+{len(fn.functions)-4})"
            in_deg = f"imported_by={fn.in_degree}" if fn.in_degree else ""
            parts = [f"  {rel} ({fn.lines}L)"]
            if in_deg:
                parts.append(in_deg)
            if funcs_preview:
                parts.append(f"defines: {funcs_preview}")
            lines.append(" | ".join(parts))

    # Top functions (just names + locations — surgical context)
    top_funcs = graph.ranked_functions[:top_functions]
    if top_funcs:
        lines.append("Key functions (most central):")
        for qname in top_funcs[:6]:  # cap at 6 for brevity
            fn = graph.functions[qname]
            lines.append(f"  {fn.file_path}:{fn.line} {fn.name}()")

    lines.append(f"({graph.total_files} files, {graph.total_functions} functions scanned)")
    return "\n".join(lines)


def find_files_for_goal(graph: CodebaseGraph, goal: str, *, limit: int = 5) -> List[str]:
    """Return file paths most relevant to a goal (heuristic keyword match)."""
    if not goal or graph.error:
        return graph.ranked_files[:limit]

    goal_words = {w.lower().strip(".,") for w in goal.split() if len(w) > 3}
    scored: List[Tuple[str, float]] = []
    for rel, fn in graph.files.items():
        # Score = centrality + keyword overlap in path + function names
        path_hits = sum(1 for w in goal_words if w in rel.lower())
        func_hits = sum(1 for f in fn.functions for w in goal_words if w in f.lower())
        score = fn.centrality + path_hits * 0.3 + func_hits * 0.2
        scored.append((rel, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [r for r, _ in scored[:limit]]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import json as _json
    import dataclasses

    parser = argparse.ArgumentParser(
        prog="poe-codebase-graph",
        description="Build AST-based call graph for a Python codebase",
    )
    parser.add_argument("path", nargs="?", default=".", help="Repo path (default: .)")
    parser.add_argument("--top", type=int, default=10, help="Number of top files to show")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--context", action="store_true", help="Output agent context block")
    parser.add_argument("--goal", default="", help="Goal for relevance-biased ranking")
    parser.add_argument("--max-files", type=int, default=200, help="Max files to scan")
    args = parser.parse_args()

    graph = build_codebase_graph(args.path, max_files=args.max_files)

    if graph.error:
        print(f"Error: {graph.error}")
        return

    if args.json:
        # Minimal JSON — just ranked files + top functions
        out = {
            "repo_path": graph.repo_path,
            "total_files": graph.total_files,
            "total_functions": graph.total_functions,
            "ranked_files": [
                {
                    "path": r,
                    "module": graph.files[r].module,
                    "lines": graph.files[r].lines,
                    "in_degree": graph.files[r].in_degree,
                    "centrality": round(graph.files[r].centrality, 3),
                    "functions": graph.files[r].functions[:5],
                    "classes": graph.files[r].classes[:3],
                }
                for r in graph.ranked_files[:args.top]
            ],
            "ranked_functions": [
                {
                    "qualified_name": q,
                    "file": graph.functions[q].file_path,
                    "line": graph.functions[q].line,
                }
                for q in graph.ranked_functions[:20]
            ],
        }
        print(_json.dumps(out, indent=2))
        return

    if args.context:
        print(format_graph_context(graph, goal=args.goal, top_files=args.top))
        return

    print(f"Codebase: {graph.repo_path}")
    print(f"Scanned: {graph.total_files} files, {graph.total_functions} functions")
    print()
    print(f"Top {args.top} files by centrality:")
    for rel in graph.ranked_files[:args.top]:
        fn = graph.files[rel]
        print(
            f"  {fn.centrality:.2f}  {rel} ({fn.lines}L, in_degree={fn.in_degree})"
        )

    if args.goal:
        print(f"\nFiles relevant to: {args.goal!r}")
        for rel in find_files_for_goal(graph, args.goal, limit=5):
            print(f"  {rel}")


if __name__ == "__main__":
    main()
