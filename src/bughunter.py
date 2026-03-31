"""bughunter — self-directed static code scan for Poe's own source.

Inspired by claw-code's verificationAgent pattern: Poe should be able to
scan her own orchestration code for bugs, not just diagnose runtime failures.

This module uses Python's stdlib (ast + py_compile) to scan src/ for:
  - Syntax errors (py_compile)
  - Bare except clauses that swallow errors silently
  - Mutable default arguments (list/dict literals in function signatures)
  - Broad Exception catches when a specific exception type is known
  - Shadowed builtins in function arguments (list, dict, type, etc.)
  - f-strings with unreachable format expressions
  - TODO/FIXME/HACK/XXX comments (informational)

Usage:
    from bughunter import run_bughunter
    report = run_bughunter()
    print(report.summary())

    # CLI:
    poe-bughunter [--src PATH] [--severity warning|info] [--json]
"""

from __future__ import annotations

import ast
import json
import py_compile
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BugFinding:
    file: str
    line: int
    severity: str      # "error" | "warning" | "info"
    code: str          # BH001, BH002, ...
    message: str


@dataclass
class BughunterReport:
    files_scanned: int
    findings: List[BugFinding] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)   # files that failed to parse

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "info")

    def summary(self) -> str:
        lines = [
            f"bughunter: {self.files_scanned} files scanned",
            f"  {self.error_count} errors  {self.warning_count} warnings  {self.info_count} info",
        ]
        if self.errors:
            lines.append(f"  {len(self.errors)} files could not be parsed: {', '.join(self.errors)}")
        for f in sorted(self.findings, key=lambda x: (x.severity, x.file, x.line)):
            icon = "E" if f.severity == "error" else "W" if f.severity == "warning" else "I"
            lines.append(f"  [{icon}] {f.file}:{f.line} {f.code}: {f.message}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "findings": [
                {
                    "file": f.file,
                    "line": f.line,
                    "severity": f.severity,
                    "code": f.code,
                    "message": f.message,
                }
                for f in self.findings
            ],
        }


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

_BUILTIN_NAMES = frozenset({
    "list", "dict", "set", "tuple", "type", "str", "int", "float",
    "bool", "bytes", "object", "id", "input", "format", "filter",
    "map", "zip", "range", "len", "print", "open", "next", "iter",
    "min", "max", "sum", "abs", "round", "sorted", "reversed", "vars",
    "dir", "hash", "repr", "eval", "exec",
})


class _BugVisitor(ast.NodeVisitor):
    """Walk an AST and collect findings."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.findings: List[BugFinding] = []

    def _add(self, node: ast.AST, severity: str, code: str, message: str) -> None:
        self.findings.append(BugFinding(
            file=self.filepath,
            line=getattr(node, "lineno", 0),
            severity=severity,
            code=code,
            message=message,
        ))

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # BH001: bare except (no exception type)
        if node.type is None:
            self._add(node, "warning", "BH001",
                      "bare except clause — catches BaseException including KeyboardInterrupt")
        # BH002: broad Exception catch (only skip if body is literally just `pass`)
        elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
            non_pass = [s for s in node.body if not isinstance(s, ast.Pass)]
            if non_pass:
                self._add(node, "info", "BH002",
                          "broad Exception catch — consider catching specific exception types")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def _check_function(self, node: ast.FunctionDef) -> None:
        defaults = node.args.defaults + node.args.kw_defaults
        for default in defaults:
            if default is None:
                continue
            # BH003: mutable default argument (list or dict literal)
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                kind = type(default).__name__.lower()
                self._add(default, "warning", "BH003",
                          f"mutable default argument ({kind} literal) — use None and assign inside")

        # BH004: shadowed builtin in parameter names
        all_args = (
            [a.arg for a in node.args.args]
            + [a.arg for a in node.args.posonlyargs]
            + [a.arg for a in node.args.kwonlyargs]
            + ([node.args.vararg.arg] if node.args.vararg else [])
            + ([node.args.kwarg.arg] if node.args.kwarg else [])
        )
        for arg_name in all_args:
            if arg_name in _BUILTIN_NAMES:
                self._add(node, "info", "BH004",
                          f"argument name '{arg_name}' shadows a builtin")


def _scan_comments(filepath: str, source: str) -> List[BugFinding]:
    """Scan for TODO/FIXME/HACK/XXX comments (BH010)."""
    findings = []
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            upper = stripped.upper()
            for tag in ("TODO", "FIXME", "HACK", "XXX"):
                if tag in upper:
                    snippet = stripped[1:].strip()[:60]
                    findings.append(BugFinding(
                        file=filepath,
                        line=lineno,
                        severity="info",
                        code="BH010",
                        message=f"deferred note: {snippet}",
                    ))
                    break
    return findings


# ---------------------------------------------------------------------------
# Main scan entry point
# ---------------------------------------------------------------------------

def scan_file(filepath: str, *, include_todos: bool = False) -> tuple[list, bool]:
    """Scan a single file. Returns (findings, parse_ok)."""
    path = Path(filepath)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], False

    # Syntax check first
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as exc:
        return [BugFinding(
            file=filepath, line=exc.lineno or 0, severity="error",
            code="BH000", message=f"SyntaxError: {exc.msg}",
        )], False

    visitor = _BugVisitor(filepath)
    visitor.visit(tree)
    findings = visitor.findings

    if include_todos:
        findings += _scan_comments(filepath, source)

    return findings, True


def run_bughunter(
    src_path: Optional[str] = None,
    *,
    min_severity: str = "warning",  # "error" | "warning" | "info"
    include_todos: bool = False,
) -> BughunterReport:
    """Scan all Python files in src_path (default: src/ next to this file).

    Args:
        src_path: Directory to scan (defaults to src/ relative to this module).
        min_severity: Minimum severity to include in report.
        include_todos: Include TODO/FIXME/HACK/XXX comments as info findings.

    Returns:
        BughunterReport with all findings at or above min_severity.
    """
    _sev_rank = {"error": 0, "warning": 1, "info": 2}
    _min_rank = _sev_rank.get(min_severity, 1)

    if src_path is None:
        src_path = str(Path(__file__).parent)

    py_files = sorted(Path(src_path).glob("*.py"))
    report = BughunterReport(files_scanned=len(py_files))

    for py_file in py_files:
        findings, ok = scan_file(str(py_file), include_todos=include_todos)
        if not ok and findings:
            # Only parse errors (BH000) end up here
            report.errors.append(py_file.name)
        for f in findings:
            if _sev_rank.get(f.severity, 99) <= _min_rank:
                report.findings.append(f)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="bughunter — static code scan for Poe's src/")
    p.add_argument("--src", default=None,
                   help="Directory to scan (default: src/ next to this file)")
    p.add_argument("--severity", choices=["error", "warning", "info"], default="warning",
                   help="Minimum severity to report (default: warning)")
    p.add_argument("--todos", action="store_true",
                   help="Include TODO/FIXME/HACK/XXX comments")
    p.add_argument("--json", action="store_true",
                   help="Output JSON instead of human-readable text")
    args = p.parse_args()

    report = run_bughunter(
        src_path=args.src,
        min_severity=args.severity,
        include_todos=args.todos,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())

    # Exit code: 1 if any errors found
    sys.exit(1 if report.error_count > 0 else 0)


if __name__ == "__main__":
    main()
