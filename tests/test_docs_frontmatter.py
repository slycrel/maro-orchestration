"""Docs status-frontmatter contract (2026-07-04 docs refactor).

Three species of doc, made explicit so nothing undated can claim to be current:
  living         — kept current; stale facts are bugs
  dormant-design — design intent, not current state
  record         — point-in-time snapshot, lives in docs/history/

Every flat docs/*.md declares its species in YAML frontmatter; docs/history/*.md
are records. Subdirs (conversations/, research/, knowledge-layer/) are source
material kept as-written and exempt. Root workflow files (GOAL_BRAIN, MILESTONES,
BACKLOG, …) are living by definition and exempt.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
HISTORY = DOCS / "history"

ALLOWED = {"living", "dormant-design", "record"}

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _frontmatter(path: Path) -> dict:
    m = _FM_RE.match(path.read_text(encoding="utf-8"))
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def test_flat_docs_declare_status():
    missing, bad = [], []
    for p in sorted(DOCS.glob("*.md")):
        fm = _frontmatter(p)
        if "status" not in fm:
            missing.append(p.name)
        elif fm["status"] not in ALLOWED:
            bad.append(f"{p.name}: {fm['status']}")
    assert not missing, f"docs without status frontmatter: {missing}"
    assert not bad, f"docs with invalid status: {bad}"


def test_history_docs_are_records():
    wrong = []
    for p in sorted(HISTORY.glob("*.md")):
        if p.name == "README.md":
            continue
        fm = _frontmatter(p)
        if fm.get("status") != "record":
            wrong.append(p.name)
    assert not wrong, f"history docs must have status: record — {wrong}"


def test_history_naming_dated_or_rolling_log():
    # Dated snapshot (YYYY-MM-DD-name.md) or a known rolling log.
    rolling = {"CHANGELOG.md", "ROADMAP_ARCHIVE.md", "README.md"}
    dated = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$")
    stray = [
        p.name
        for p in sorted(HISTORY.glob("*.md"))
        if p.name not in rolling and not dated.match(p.name)
    ]
    assert not stray, f"history files must be dated or a declared rolling log: {stray}"


def test_superseded_by_paths_exist():
    broken = []
    for p in sorted(DOCS.glob("*.md")) + sorted(HISTORY.glob("*.md")):
        target = _frontmatter(p).get("superseded-by", "")
        if target and not (REPO / target).exists():
            broken.append(f"{p.name} -> {target}")
    assert not broken, f"superseded-by points at missing files: {broken}"


def test_roadmap_stub_stays_inert():
    # ROADMAP.md must never grow `- [ ]` checkboxes: convo_miner treats them as
    # a work queue, and MILESTONES/BACKLOG are the only queues by design.
    text = (REPO / "ROADMAP.md").read_text(encoding="utf-8")
    assert not re.search(r"^- \[ \]", text, re.MULTILINE), (
        "ROADMAP.md is a stub by design (2026-07-04); add work items to "
        "MILESTONES.md or BACKLOG.md instead"
    )
