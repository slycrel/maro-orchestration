"""Phase 48: Conversation Mining — Idea Archaeology.

Scans Claude Code session logs, OpenClaw workspace docs, and git history for
orchestration-related ideas, deferred concepts, and "what if" musings that
were noted but never pursued — or ideas whose time has come now that the
foundation is stronger.

No LLM calls — pure text extraction + keyword matching. Outputs a ranked
markdown report for human review + optional BACKLOG.md injection.

Sources scanned:
- ~/.claude/projects/*/  JSONL session logs (user messages only)
- ~/.openclaw/workspace/{MEMORY,TASKS,GOALS,SOUL}.md
- git log of the mainline repo (commit messages)
- Poe conversation history (output/telegram/ artifacts)

Usage:
    poe-mine                        # scan all sources, print report
    poe-mine --since 2026-03-01     # limit to messages after date
    poe-mine --output research/     # write report to file
    poe-mine --inject-backlog       # append high-confidence ideas to BACKLOG.md
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("poe.convo_miner")

# ---------------------------------------------------------------------------
# Idea scoring keywords
# ---------------------------------------------------------------------------

# High-signal phrases: user expressing a concrete desire or deferred idea
_HIGH_SIGNAL: List[re.Pattern] = [
    re.compile(r"\bwe should\b", re.I),
    re.compile(r"\blet's (?:add|build|implement|wire|create|make)\b", re.I),
    re.compile(r"\bwould (?:be (?:nice|good|great|useful|interesting)|love)\b", re.I),
    re.compile(r"\bideally\b", re.I),
    re.compile(r"\bI (?:want|wish|wonder|think we)\b", re.I),
    re.compile(r"\bwhat if\b", re.I),
    re.compile(r"\bmaybe (?:we|poe|the)\b", re.I),
    re.compile(r"\beventually\b", re.I),
    re.compile(r"\btodo\b", re.I),
    re.compile(r"\bdeferred\b", re.I),
    re.compile(r"\blater\b.*\bwhen\b", re.I),
    re.compile(r"- \[ \]", re.I),   # unchecked markdown checkboxes
]

# Domain keywords: sentence must mention at least one of these to be relevant
_DOMAIN_KEYWORDS: List[str] = [
    "agent", "loop", "memory", "skill", "persona", "evolver", "heartbeat",
    "mission", "director", "worker", "inspector", "orchestrat", "interrupt",
    "token", "model", "llm", "decompos", "context", "lesson", "graveyard",
    "phase", "backlog", "roadmap", "kill switch", "poe-", "telegram", "slack",
    "openrouter", "local model", "self-improv", "sub-goal", "sub-loop",
    "phase 4", "phase 5", "researcher", "knowledge", "graduation",
]

_DOMAIN_RE = re.compile(
    "|".join(re.escape(kw) for kw in _DOMAIN_KEYWORDS), re.I
)

# Noise filters — skip lines dominated by these (code output, logs, etc.)
_NOISE_RE = re.compile(
    r"^(?:>>|ERROR|INFO|DEBUG|WARNING|\s*\$|\s*\d+→|\s*\.\.\.|```|---)", re.I
)

_MIN_LEN = 30   # minimum line length to consider
_MAX_LEN = 400  # cap extracted snippet length


# ---------------------------------------------------------------------------
# Idea dataclass
# ---------------------------------------------------------------------------

class Idea:
    __slots__ = ("text", "source", "timestamp", "confidence", "signals")

    def __init__(
        self,
        text: str,
        source: str,
        timestamp: Optional[str] = None,
        confidence: float = 0.5,
        signals: Optional[List[str]] = None,
    ) -> None:
        self.text = text.strip()
        self.source = source
        self.timestamp = timestamp
        self.confidence = confidence
        self.signals = signals or []

    def __repr__(self) -> str:
        return f"Idea(conf={self.confidence:.2f}, src={self.source!r}, text={self.text[:60]!r})"


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def _score_line(line: str) -> Tuple[float, List[str]]:
    """Return (confidence, matched_signals) for a single line."""
    if len(line) < _MIN_LEN or _NOISE_RE.match(line):
        return 0.0, []
    if not _DOMAIN_RE.search(line):
        return 0.0, []

    signals = []
    for pat in _HIGH_SIGNAL:
        m = pat.search(line)
        if m:
            signals.append(m.group(0).lower())

    if not signals:
        return 0.0, []

    # Base confidence: 0.4 per signal hit, capped at 0.95
    confidence = min(0.95, 0.4 + 0.2 * (len(signals) - 1))
    return confidence, signals


def _extract_ideas_from_text(text: str, source: str, timestamp: Optional[str] = None) -> List[Idea]:
    """Extract orchestration ideas from a block of text."""
    ideas: List[Idea] = []
    for line in text.splitlines():
        line = line.strip()
        conf, signals = _score_line(line)
        if conf > 0:
            ideas.append(Idea(
                text=line[:_MAX_LEN],
                source=source,
                timestamp=timestamp,
                confidence=conf,
                signals=signals,
            ))
    return ideas


# ---------------------------------------------------------------------------
# Source scanners
# ---------------------------------------------------------------------------

def scan_session_logs(
    projects_dir: Optional[Path] = None,
    since: Optional[datetime] = None,
) -> List[Idea]:
    """Scan ~/.claude/projects/ JSONL session logs for user messages."""
    if projects_dir is None:
        projects_dir = Path.home() / ".claude" / "projects"

    ideas: List[Idea] = []
    jsonl_files = sorted(projects_dir.rglob("*.jsonl"))

    for jf in jsonl_files:
        try:
            with open(jf, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") != "user":
                        continue

                    ts = entry.get("timestamp", "")
                    if since and ts:
                        try:
                            entry_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if entry_dt < since.replace(tzinfo=timezone.utc):
                                continue
                        except Exception:
                            pass

                    # Extract text from message content
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                parts.append(block.get("text", ""))
                        text = "\n".join(parts)
                    else:
                        text = str(content)

                    source = f"session:{jf.stem[:8]}"
                    ideas.extend(_extract_ideas_from_text(text, source, ts))

        except Exception as exc:
            log.debug("scan_session_logs: skipping %s: %s", jf.name, exc)

    return ideas


def scan_openclaw_docs(workspace: Optional[Path] = None) -> List[Idea]:
    """Scan OpenClaw workspace docs for deferred items."""
    if workspace is None:
        workspace = Path.home() / ".openclaw" / "workspace"

    ideas: List[Idea] = []
    targets = ["MEMORY.md", "TASKS.md", "GOALS.md", "SOUL.md", "BACKLOG.md"]

    for fname in targets:
        fpath = workspace / fname
        if not fpath.exists():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            source = f"openclaw:{fname}"
            ideas.extend(_extract_ideas_from_text(text, source))
        except Exception as exc:
            log.debug("scan_openclaw_docs: skipping %s: %s", fname, exc)

    return ideas


def scan_git_log(repo: Optional[Path] = None, max_commits: int = 500) -> List[Idea]:
    """Scan git commit messages for embedded ideas/TODOs."""
    if repo is None:
        try:
            from orch_items import orch_root
            repo = orch_root()
        except Exception:
            repo = Path.cwd()

    ideas: List[Idea] = []
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={max_commits}", "--pretty=format:%H %ai %s%n%b"],
            cwd=repo, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return ideas
        ideas.extend(_extract_ideas_from_text(result.stdout, "git:commits"))
    except Exception as exc:
        log.debug("scan_git_log: %s", exc)

    return ideas


def scan_poe_memory(workspace: Optional[Path] = None) -> List[Idea]:
    """Scan the orchestration repo's own BACKLOG/ROADMAP for open items."""
    if workspace is None:
        # Use the repo root (parent of src/) — orch_root() points to runtime
        # workspace, not the mainline repo.
        workspace = Path(__file__).parent.parent

    ideas: List[Idea] = []
    for fname in ("BACKLOG.md", "ROADMAP.md"):
        fpath = workspace / fname
        if not fpath.exists():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            # In BACKLOG/ROADMAP, unchecked items are concrete pending work
            source = f"repo:{fname}"
            for line in text.splitlines():
                line = line.strip()
                # Unchecked markdown items that mention domain keywords
                if re.match(r"- \[ \]", line) and _DOMAIN_RE.search(line):
                    ideas.append(Idea(
                        text=line[:_MAX_LEN],
                        source=source,
                        confidence=0.85,
                        signals=["unchecked-todo"],
                    ))
        except Exception as exc:
            log.debug("scan_poe_memory: skipping %s: %s", fname, exc)

    return ideas


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _deduplicate(ideas: List[Idea], similarity_threshold: float = 0.6) -> List[Idea]:
    """Remove near-duplicate ideas using token Jaccard similarity."""
    def _tokens(text: str) -> set:
        return set(re.findall(r"\b\w{4,}\b", text.lower()))

    kept: List[Idea] = []
    seen_token_sets: List[set] = []

    for idea in sorted(ideas, key=lambda x: -x.confidence):
        tok = _tokens(idea.text)
        if not tok:
            continue
        duplicate = False
        for seen in seen_token_sets:
            if not seen:
                continue
            jaccard = len(tok & seen) / len(tok | seen)
            if jaccard >= similarity_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(idea)
            seen_token_sets.append(tok)

    return kept


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(ideas: List[Idea], title: str = "Conversation Mining Report") -> str:
    """Generate a markdown report from a list of ideas."""
    ideas = _deduplicate(ideas)
    high = [i for i in ideas if i.confidence >= 0.7]
    mid = [i for i in ideas if 0.5 <= i.confidence < 0.7]
    low = [i for i in ideas if i.confidence < 0.5]

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {title}",
        f"",
        f"*Generated {ts} — {len(ideas)} unique ideas extracted from conversation history.*",
        f"",
        f"---",
        f"",
    ]

    def _section(label: str, bucket: List[Idea]) -> None:
        if not bucket:
            return
        lines.append(f"## {label} ({len(bucket)} ideas)")
        lines.append("")
        for idea in bucket[:50]:  # cap per section
            sig = ", ".join(idea.signals[:2]) if idea.signals else ""
            src = idea.source
            lines.append(f"- **[{src}]** {idea.text}")
            if sig:
                lines[-1] += f"  *(signals: {sig})*"
        lines.append("")

    _section("High Confidence (≥0.70) — Likely Actionable", high)
    _section("Medium Confidence (0.50–0.69) — Worth Reviewing", mid)
    _section("Low Confidence (<0.50) — Weak Signal", low)

    # Source breakdown
    sources: Dict[str, int] = {}
    for idea in ideas:
        sources[idea.source] = sources.get(idea.source, 0) + 1
    if sources:
        lines += [
            "---",
            "",
            "## Source Breakdown",
            "",
        ]
        for src, count in sorted(sources.items(), key=lambda x: -x[1]):
            lines.append(f"- `{src}`: {count} ideas")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        prog="poe-mine",
        description="Mine conversation history for orchestration ideas (Phase 48).",
    )
    parser.add_argument(
        "--since",
        metavar="DATE",
        help="Only include messages after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="PATH",
        help="Write report to file (default: print to stdout)",
    )
    parser.add_argument(
        "--no-sessions",
        action="store_true",
        help="Skip Claude Code session log scanning (faster)",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Skip git log scanning",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max ideas per source before dedup (default: 200)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.4,
        help="Minimum confidence to include in report (default: 0.4)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    since: Optional[datetime] = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since)
        except ValueError:
            print(f"error: --since must be YYYY-MM-DD, got {args.since!r}", file=sys.stderr)
            return 1

    all_ideas: List[Idea] = []

    # 1. Session logs
    if not args.no_sessions:
        if args.verbose:
            print("Scanning Claude Code session logs...", file=sys.stderr)
        ideas = scan_session_logs(since=since)
        if args.verbose:
            print(f"  → {len(ideas)} raw ideas from sessions", file=sys.stderr)
        all_ideas.extend(ideas)

    # 2. OpenClaw workspace docs
    if args.verbose:
        print("Scanning OpenClaw workspace docs...", file=sys.stderr)
    ideas = scan_openclaw_docs()
    if args.verbose:
        print(f"  → {len(ideas)} raw ideas from openclaw docs", file=sys.stderr)
    all_ideas.extend(ideas)

    # 3. Git log
    if not args.no_git:
        if args.verbose:
            print("Scanning git log...", file=sys.stderr)
        ideas = scan_git_log()
        if args.verbose:
            print(f"  → {len(ideas)} raw ideas from git log", file=sys.stderr)
        all_ideas.extend(ideas)

    # 4. Repo BACKLOG / ROADMAP open items
    if args.verbose:
        print("Scanning repo BACKLOG/ROADMAP...", file=sys.stderr)
    ideas = scan_poe_memory()
    if args.verbose:
        print(f"  → {len(ideas)} open items from repo", file=sys.stderr)
    all_ideas.extend(ideas)

    # Filter by minimum confidence
    all_ideas = [i for i in all_ideas if i.confidence >= args.min_confidence]

    if args.verbose:
        print(f"Total before dedup: {len(all_ideas)}", file=sys.stderr)

    report = generate_report(all_ideas)

    if args.output:
        out = Path(args.output)
        if out.is_dir():
            ts = datetime.now().strftime("%Y-%m-%d")
            out = out / f"conversation-mining-{ts}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"Report written to {out}", file=sys.stderr)
    else:
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
