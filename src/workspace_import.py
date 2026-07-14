"""Import run metadata + memory ledgers from another Maro workspace.

`maro-import --source <workspace> --label <origin>` merges the learning data
a satellite install accumulated (a Docker trial, a second box, a Hermes-hosted
install) into this workspace, so learning compounds instead of dying with the
container.

Merge semantics — deliberately a whitelist, not "copy everything":

* ``runs/<id>/``      copy-if-absent (run ids are uuid-prefixed; collisions
                      mean "already imported"). Each copied run gets a
                      ``imported_from.json`` provenance marker.
* ``memory/**/*.jsonl`` append rows the target has never seen (exact-line
                      dedup), under the target's file locks. Idempotent:
                      re-running an import is a no-op.
* ``memory/<date>.md``  copy-if-absent; if the target has the same daily file,
                      append the source content once under a provenance
                      heading (marker-guarded, so re-import is a no-op).
* curated state       (MEMORY.md, playbook.md, skills/, personas/) is NEVER
                      merged into live files — consolidation is an editorial
                      act, not a concat. ``--include-curated`` copies them to
                      ``imports/<label>/`` for manual (or evolver) review.
* machine state       (config.yml, jobs.json, task store, heartbeat, secrets,
                      locks, correspondence.db) is never touched.

Every import appends an audit row to ``memory/imports.jsonl`` in the target.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from file_lock import locked_append, locked_write

# Ledger rows are deduped by exact line content. Files matching these names
# under memory/ (any depth) are treated as append-only ledgers.
_LEDGER_GLOB = "*.jsonl"
# Curated files: merged only into imports/<label>/ quarantine, never live.
_CURATED = ["MEMORY.md", "playbook.md", "skills", "personas"]
_DAILY_MD_PREFIX = "20"  # memory/2026-07-09.md style daily logs


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_workspace(path: Path) -> bool:
    return (path / "memory").is_dir() or (path / "runs").is_dir()


def _iter_run_dirs(runs_dir: Path) -> List[Path]:
    if not runs_dir.is_dir():
        return []
    return sorted(
        p for p in runs_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def import_runs(source: Path, target: Path, label: str, dry_run: bool) -> Dict:
    copied, skipped = [], []
    target_runs = target / "runs"
    for run_dir in _iter_run_dirs(source / "runs"):
        dest = target_runs / run_dir.name
        if dest.exists():
            skipped.append(run_dir.name)
            continue
        if not dry_run:
            shutil.copytree(
                run_dir, dest,
                ignore=shutil.ignore_patterns("*.lock"),
            )
            marker = {
                "imported_from": label,
                "source_path": str(run_dir),
                "imported_at": _now_iso(),
            }
            (dest / "imported_from.json").write_text(
                json.dumps(marker, indent=2) + "\n"
            )
            # A target may already have completed its one-time legacy index
            # migration; publish copied run references explicitly.
            from runs import index_run_dir
            index_run_dir(dest)
        copied.append(run_dir.name)
    return {"copied": copied, "skipped_existing": skipped}


def import_ledgers(source: Path, target: Path, dry_run: bool) -> Dict:
    results = {}
    src_mem = source / "memory"
    if not src_mem.is_dir():
        return results
    for src_file in sorted(src_mem.rglob(_LEDGER_GLOB)):
        if src_file.name.endswith(".lock") or src_file.name == "imports.jsonl":
            continue
        rel = src_file.relative_to(src_mem)
        dest = target / "memory" / rel
        src_lines = [
            ln for ln in src_file.read_text().splitlines() if ln.strip()
        ]
        if not src_lines:
            continue
        existing = set()
        if dest.exists():
            existing = {
                ln for ln in dest.read_text().splitlines() if ln.strip()
            }
        new_lines = [ln for ln in src_lines if ln not in existing]
        if new_lines and not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with locked_write(dest):
                with open(dest, "a") as f:
                    for ln in new_lines:
                        f.write(ln + "\n")
        results[str(rel)] = {
            "appended": len(new_lines),
            "duplicates": len(src_lines) - len(new_lines),
        }
    return results


def import_daily_logs(source: Path, target: Path, label: str, dry_run: bool) -> Dict:
    copied, appended, skipped = [], [], []
    src_mem = source / "memory"
    if not src_mem.is_dir():
        return {}
    for src_file in sorted(src_mem.glob(f"{_DAILY_MD_PREFIX}*.md")):
        dest = target / "memory" / src_file.name
        content = src_file.read_text()
        if not content.strip():
            continue
        marker = f"<!-- imported from {label} -->"
        if not dest.exists():
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(f"{marker}\n{content}")
            copied.append(src_file.name)
        elif marker in dest.read_text():
            skipped.append(src_file.name)
        else:
            if not dry_run:
                with locked_write(dest):
                    with open(dest, "a") as f:
                        f.write(f"\n\n{marker}\n## Imported from {label}\n\n{content}")
            appended.append(src_file.name)
    return {"copied": copied, "appended": appended, "already_imported": skipped}


def quarantine_curated(source: Path, target: Path, label: str, dry_run: bool) -> List[str]:
    saved = []
    quarantine = target / "imports" / label
    for name in _CURATED:
        src = source / name
        if not src.exists():
            continue
        dest = quarantine / name
        if dest.exists():
            continue
        if not dry_run:
            quarantine.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.lock"))
            else:
                shutil.copy2(src, dest)
        saved.append(name)
    return saved


def run_import(
    source: Path,
    target: Path,
    label: str,
    dry_run: bool = False,
    include_curated: bool = False,
) -> Dict:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise SystemExit("source and target are the same workspace")
    if not _is_workspace(source):
        raise SystemExit(f"not a maro workspace (no memory/ or runs/): {source}")
    if not _is_workspace(target):
        raise SystemExit(f"not a maro workspace (no memory/ or runs/): {target}")

    report = {
        "label": label,
        "source": str(source),
        "imported_at": _now_iso(),
        "dry_run": dry_run,
        "runs": import_runs(source, target, label, dry_run),
        "ledgers": import_ledgers(source, target, dry_run),
        "daily_logs": import_daily_logs(source, target, label, dry_run),
    }
    if include_curated:
        report["curated_quarantined"] = quarantine_curated(
            source, target, label, dry_run
        )
    if not dry_run:
        audit = target / "memory" / "imports.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        locked_append(audit, json.dumps({**report, "action": "workspace_import"}))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="maro-import",
        description="Merge runs + memory ledgers from another Maro workspace into this one.",
    )
    ap.add_argument("--source", required=True, type=Path,
                    help="path to the source workspace (e.g. a container's .maro/workspace)")
    ap.add_argument("--target", type=Path, default=None,
                    help="target workspace (default: this machine's active workspace)")
    ap.add_argument("--label", required=True,
                    help="provenance label, e.g. hermes-docker-trial-2026-07-09")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be imported without writing")
    ap.add_argument("--include-curated", action="store_true",
                    help="also quarantine curated files (MEMORY.md, playbook, skills, personas) under imports/<label>/ for review")
    args = ap.parse_args(argv)

    target = args.target
    if target is None:
        from config import workspace_root
        target = workspace_root()

    report = run_import(
        args.source, target, args.label,
        dry_run=args.dry_run, include_curated=args.include_curated,
    )
    json.dump(report, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
