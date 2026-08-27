#!/usr/bin/env python3
"""Shared Go-source scanning for the mutation tools.

This module exists because the same paren walker is now needed by more
than one tool, and on 2026-08-27 a bug in it (no case for comments, so
the apostrophe in "Go's" opened a rune literal that ate nine lines and
the closing paren) caused mutate-wraps.py to silently skip a site while
still printing "all 1 site(s) killed at least one test". Fixing that in
one copy and not the other is how the same bug comes back wearing a
different tool's name -- so there is one copy (P16, and L13 one level
up: a fix at the site that has the fixture is not a fix for the class).

Also carries the restore machinery. A tool that rewrites the working
tree to measure it must not be able to damage the tree when interrupted:
a per-site try/finally covers an exception but NOT SIGTERM, because
Python does not run finally blocks for a default-disposition signal.
Killing an early mutate-wraps run left internal/provenance/provenance.go
mutated on disk.
"""

import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GO_ROOT = os.path.dirname(HERE)

# Every file the current run has rewritten, mapped to its pristine copy.
_PENDING = {}


def restore_all(*_):
    for path, backup in list(_PENDING.items()):
        try:
            shutil.copyfile(backup, path)
            os.unlink(backup)
        except OSError:
            pass
        _PENDING.pop(path, None)


def install_restore_handlers():
    atexit.register(restore_all)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda s, f: (restore_all(), sys.exit(128 + s)))


def stage(path):
    """Snapshot path so restore_all() can put it back."""
    backup = tempfile.mktemp(suffix=".go")
    shutil.copyfile(path, backup)
    _PENDING[path] = backup


def go_files(include_tests=False):
    for dirpath, dirnames, files in os.walk(GO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "testdata")]
        for fn in sorted(files):
            if not fn.endswith(".go"):
                continue
            if not include_tests and fn.endswith("_test.go"):
                continue
            yield os.path.join(dirpath, fn)


def match_close(src, open_idx):
    """Index of the ')' matching the '(' at open_idx, or -1.

    Go raw strings are delimited by backticks and can contain anything;
    interpreted strings honour backslash escapes. A paren inside either
    is not a paren, and getting that wrong is how a rewriter corrupts a
    file it was only supposed to measure.

    COMMENTS ARE PART OF THAT. A multi-line argument in this port is
    routinely interleaved with prose, and prose contains apostrophes.
    Line and block comments are consumed as comments, and a rune scan
    may not cross a newline.
    """
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            if end < 0:
                return -1
            i = end + 1
        elif c == "`":
            i = src.find("`", i + 1)
            if i < 0:
                return -1
        elif c == '"':
            i += 1
            while i < n and src[i] != '"' and src[i] != "\n":
                if src[i] == "\\":
                    i += 1
                i += 1
        elif c == "'":
            # A Go rune literal is one character or one escape and never
            # spans a line. Anything else is prose that survived the
            # comment scan, and must not eat the file.
            j = i + 1
            while j < n and src[j] != "'" and src[j] != "\n":
                if src[j] == "\\":
                    j += 1
                j += 1
            if j < n and src[j] == "'":
                i = j
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_args(src, open_idx, close_idx):
    """(start, end) of each top-level argument between the parens.

    Same skipping rules as match_close, plus brackets and braces, so a
    composite literal or an index expression containing a comma is one
    argument and not two.
    """
    out = []
    depth = 0
    start = open_idx + 1
    i = start
    n = close_idx
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 1
        elif c == "`":
            j = src.find("`", i + 1)
            i = n if j < 0 else j
        elif c == '"':
            i += 1
            while i < n and src[i] != '"' and src[i] != "\n":
                if src[i] == "\\":
                    i += 1
                i += 1
        elif c == "'":
            j = i + 1
            while j < n and src[j] != "'" and src[j] != "\n":
                if src[j] == "\\":
                    j += 1
                j += 1
            if j < n and src[j] == "'":
                i = j
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append((start, i))
            start = i + 1
        i += 1
    tail = src[start:close_idx]
    if tail.strip():
        out.append((start, close_idx))
    return out


def call_sites(path, callee):
    """(start, open_idx, close_idx) for each `callee(...)`, plus skips.

    Returns (src, sites, unparsed_lines). An unparsed site is an ERROR
    for the caller, never a warning: a site the tool cannot parse is a
    site it does not mutate, and any coverage number printed afterwards
    is quantified over a denominator that is quietly short.
    """
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    out, skipped = [], []
    pat = re.compile(r"(?<![\w.])" + re.escape(callee) + r"\(")
    for m in pat.finditer(src):
        close = match_close(src, m.end() - 1)
        if close < 0:
            skipped.append(src.count("\n", 0, m.start()) + 1)
            continue
        out.append((m.start(), m.end() - 1, close))
    return src, out, skipped


def line_of(src, idx):
    return src.count("\n", 0, idx) + 1


def pkg_of(path):
    """The `go test` pattern for the package owning path."""
    rel = os.path.relpath(os.path.dirname(path), GO_ROOT)
    return "./" + rel + "/" if rel != "." else "./"


def run_tests(pkg, quick=False, extra_env=None):
    env = dict(os.environ, MARO_PYPROBE_REQUIRED="1")
    if extra_env:
        env.update(extra_env)
    cmd = ["go", "test", pkg, "-count=1"]
    if quick:
        cmd.append("-short")
    p = subprocess.run(cmd, cwd=GO_ROOT, env=env,
                       capture_output=True, text=True)
    killers = sorted(set(re.findall(r"--- FAIL: (\S+)", p.stdout)))
    panicked = "panic:" in p.stdout or "panic:" in p.stderr
    return p.returncode == 0, killers, panicked
