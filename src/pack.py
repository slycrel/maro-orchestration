"""maro-pack — produce and seal curated, human-reviewed "learning packs".

Implements the export/seal half of docs/PORTABLE_LEARNING_DESIGN.md (§7
chunk 3 — "sharing is produce-only after this chunk"; import/adopt is
chunk 4, a separate tool surface by design: `maro-import` stays trust-neutral
machine-migration, `maro-pack` owns the trust-demoting curated lifecycle).

A pack = Class C (compiled truth: standing rules, hypotheses, long-tier
lessons, skill records) + Class A (authored artifacts: skills/*.md,
personas/*.md), scrubbed and packaged for a human to review before sealing.
Raw runs (Class E) are excluded by default (`--include-runs <id>` opt-in
per-run); medium-tier lessons and the knowledge web are opt-in
(`--include-medium` / `--include-knowledge`); the playbook is opt-in and
always quarantine-only on the receiving side.

Export flow: export -> scrub -> human review -> seal.

  * ``export`` gathers artifacts from a workspace, applies
    ``secret_scrub.scrub()`` (secret-shaped strings) + ``scrub_identifiers()``
    ($HOME/username/hostname + a config+environment deny-list) to every
    string, and writes an UNSEALED ``<name>.maropack.tar.gz`` (pack.json +
    REVIEW.md + artifacts/) plus a loose ``<name>.REVIEW.md`` companion for
    a human to actually read.
  * ``seal`` stamps ``review.human_reviewed: true`` + ``reviewed_at`` +
    ``review_manifest_sha256`` (the sha256 of the REVIEW.md a human read —
    from the loose companion if present, so edits before sealing count)
    into pack.json and rewrites the archive. Refuses without an explicit
    confirmation (interactive prompt or ``--yes``).

Honesty framing (preserve verbatim in any UI/CLI copy — design doc §4): the
sharing guarantee is mechanical scrub for secret-shaped strings + mechanical
redaction of known local identifiers + a mandatory human review gate. We do
not claim mechanical anonymization. A pack is a letter — you proofread
letters.

Physical form: a single ``<name>.maropack.tar.gz`` containing:
  pack.json, REVIEW.md, artifacts/<workspace-relative-path...>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from secret_scrub import scrub, scrub_identifiers

PACK_FORMAT = 1
SCRUBBER_VERSION = 1
ARCHIVE_SUFFIX = ".maropack.tar.gz"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _maro_version() -> str:
    try:
        import importlib.metadata as _md
        return _md.version("maro-orchestration")
    except Exception:
        pass
    try:
        repo_root = Path(__file__).resolve().parent.parent
        for line in (repo_root / "pyproject.toml").read_text().splitlines():
            line = line.strip()
            if line.startswith("version "):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "unknown"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_workspace(workspace: Optional[Path]) -> Path:
    if workspace is not None:
        return Path(workspace).expanduser().resolve()
    from config import workspace_root
    return workspace_root()


def default_denylist() -> List[str]:
    """Assemble the identifier deny-list from config + environment.

    Never hardcode identifiers here (design doc §4) — this is the one seam
    that gathers them, from a workspace/user config key and well-known
    environment/git identity variables.
    """
    items = set()
    for key in ("EMAIL", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        v = os.environ.get(key)
        if v:
            items.add(v)
    try:
        out = subprocess.run(
            ["git", "config", "--get", "user.email"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            items.add(out.stdout.strip())
    except Exception:
        pass
    try:
        from config import get as _cfg_get
        extra = _cfg_get("pack.export_denylist", [])
        if isinstance(extra, list):
            items.update(str(x) for x in extra if x)
    except Exception:
        pass
    return sorted(items)


def _read_jsonl_rows(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def build_manifest(*, name: str, label: str, artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "pack_format": PACK_FORMAT,
        "name": name,
        "created_at": _now_iso(),
        "origin": {
            "label": label,
            "maro_version": _maro_version(),
            "scrubber_version": SCRUBBER_VERSION,
        },
        "artifacts": artifacts,
        "review": {
            "human_reviewed": False,
            "reviewed_at": None,
            "review_manifest_sha256": None,
        },
        "trust_policy": "demote-to-hypothesis",
    }


# ---------------------------------------------------------------------------
# REVIEW.md
# ---------------------------------------------------------------------------

_REDACTION_MARKERS = ("[REDACTED]", "[HOME]", "[USER]", "[HOST]")


def _review_section(cls: str, rel_path: str, content: str) -> str:
    flagged = [ln for ln in content.splitlines() if any(m in ln for m in _REDACTION_MARKERS)]
    section = f"## {rel_path}  (class: {cls})\n\n```\n{content.rstrip(chr(10))}\n```\n"
    if flagged:
        section += "\n**Redacted lines:**\n" + "\n".join(f"- `{ln.strip()}`" for ln in flagged) + "\n"
    return section


def _build_review_md(manifest: Dict[str, Any], sections: List[str]) -> str:
    header = (
        f"# Review — {manifest['name']}\n\n"
        f"Created: {manifest['created_at']}\n"
        f"Label: {manifest['origin']['label']}\n\n"
        "This is a mechanical scrub of secret-shaped strings and known local "
        "identifiers, not anonymization. We do not claim mechanical "
        "anonymization. A pack is a letter — proofread it before sealing. "
        "Everything below is the artifact's real content exactly as it will "
        "ship; lines flagged \"Redacted lines\" were changed by the "
        "scrubber.\n\n---\n\n"
    )
    if not sections:
        return header + "*(no artifacts in this pack)*\n"
    return header + "\n---\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# Archive I/O
# ---------------------------------------------------------------------------

def _add_tar_text(tar: tarfile.TarFile, arcname: str, text: str) -> None:
    data = text.encode("utf-8")
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = 0  # deterministic contents
    tar.addfile(info, io.BytesIO(data))


def _review_companion_path(pack_path: Path) -> Path:
    name = pack_path.name
    if name.endswith(ARCHIVE_SUFFIX):
        name = name[: -len(ARCHIVE_SUFFIX)]
    return pack_path.with_name(name + ".REVIEW.md")


def _write_pack_archive(pack_path: Path, manifest: Dict[str, Any], review_md: str,
                         files: Dict[str, str]) -> None:
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(pack_path, "w:gz") as tar:
        _add_tar_text(tar, "pack.json", json.dumps(manifest, indent=2) + "\n")
        _add_tar_text(tar, "REVIEW.md", review_md)
        for rel, content in files.items():
            _add_tar_text(tar, rel, content)
    _review_companion_path(pack_path).write_text(review_md, encoding="utf-8")


def read_pack_manifest(pack_path: Path) -> Dict[str, Any]:
    with tarfile.open(pack_path, "r:gz") as tar:
        f = tar.extractfile("pack.json")
        return json.loads(f.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_pack(
    *,
    name: str,
    label: str,
    workspace: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    include_medium: bool = False,
    include_knowledge: bool = False,
    include_playbook: bool = False,
    include_runs: Optional[List[str]] = None,
    denylist: Optional[List[str]] = None,
    home: Optional[str] = None,
    hostname: Optional[str] = None,
) -> Dict[str, Any]:
    """Gather Class C + A artifacts from ``workspace`` into an unsealed pack.

    ``home``/``hostname``/``denylist`` are pass-throughs to
    ``scrub_identifiers()`` — left unset in real use (auto-derived from this
    machine); tests pin them for deterministic assertions.
    """
    ws = _resolve_workspace(workspace)
    out = Path(out_dir).expanduser().resolve() if out_dir is not None else (ws / "output" / "packs")
    include_runs = list(include_runs or [])
    denylist = denylist if denylist is not None else default_denylist()

    def _scrub_text(s: str) -> str:
        return scrub_identifiers(scrub(s), home=home, hostname=hostname, denylist=denylist)

    def _scrub_jsonl_line(line: str) -> str:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return _scrub_text(line)
        return json.dumps(scrub_identifiers(scrub(obj), home=home, hostname=hostname, denylist=denylist))

    artifacts: List[Dict[str, Any]] = []
    files: Dict[str, str] = {}
    review_sections: List[str] = []

    def _add_text_artifact(cls: str, rel: str, raw_text: str) -> None:
        scrubbed = _scrub_text(raw_text)
        artifacts.append({"class": cls, "path": f"artifacts/{rel}", "sha256": _sha256_text(scrubbed)})
        files[f"artifacts/{rel}"] = scrubbed
        review_sections.append(_review_section(cls, rel, scrubbed))

    def _add_jsonl_artifact(cls: str, rel: str, path: Path) -> None:
        scrubbed_lines = [_scrub_jsonl_line(ln) for ln in _read_jsonl_rows(path)]
        if not scrubbed_lines:
            return  # skip empty artifacts — a young workspace has no rules yet
        content = "".join(ln + "\n" for ln in scrubbed_lines)
        artifacts.append({
            "class": cls, "path": f"artifacts/{rel}",
            "rows": len(scrubbed_lines), "sha256": _sha256_text(content),
        })
        files[f"artifacts/{rel}"] = content
        review_sections.append(_review_section(cls, rel, content))

    mem_dir = ws / "memory"
    skills_dir = ws / "skills"
    personas_dir = ws / "personas"

    for f in sorted(skills_dir.glob("*.md")) if skills_dir.is_dir() else []:
        if f.is_file():
            _add_text_artifact("skill_md", f"skills/{f.name}", f.read_text(encoding="utf-8"))
    for f in sorted(personas_dir.glob("*.md")) if personas_dir.is_dir() else []:
        if f.is_file():
            _add_text_artifact("persona_md", f"personas/{f.name}", f.read_text(encoding="utf-8"))

    _add_jsonl_artifact("skill_records", "memory/skills.jsonl", mem_dir / "skills.jsonl")
    _add_jsonl_artifact("rules", "memory/standing_rules.jsonl", mem_dir / "standing_rules.jsonl")
    _add_jsonl_artifact("hypotheses", "memory/hypotheses.jsonl", mem_dir / "hypotheses.jsonl")
    _add_jsonl_artifact("lessons", "memory/long/lessons.jsonl", mem_dir / "long" / "lessons.jsonl")

    if include_medium:
        _add_jsonl_artifact("lessons_medium", "memory/medium/lessons.jsonl", mem_dir / "medium" / "lessons.jsonl")

    if include_knowledge:
        _add_jsonl_artifact("knowledge_nodes", "memory/knowledge_nodes.jsonl", mem_dir / "knowledge_nodes.jsonl")
        _add_jsonl_artifact("knowledge_edges", "memory/knowledge_edges.jsonl", mem_dir / "knowledge_edges.jsonl")

    if include_playbook:
        pb = ws / "playbook.md"
        if pb.exists():
            _add_text_artifact("playbook", "playbook.md", pb.read_text(encoding="utf-8"))

    for run_id in include_runs:
        run_dir = ws / "runs" / run_id
        if not run_dir.is_dir():
            raise SystemExit(f"--include-runs: no such run {run_id!r} under {run_dir.parent}")
        for f in sorted(run_dir.rglob("*")):
            if not f.is_file() or f.name.endswith(".lock"):
                continue
            try:
                raw = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # binary run artifacts skipped — pack is text-first by design
            _add_text_artifact("run_artifact", f"runs/{run_id}/{f.relative_to(run_dir)}", raw)

    manifest = build_manifest(name=name, label=label, artifacts=artifacts)
    review_md = _build_review_md(manifest, review_sections)

    pack_path = out / f"{name}{ARCHIVE_SUFFIX}"
    _write_pack_archive(pack_path, manifest, review_md, files)

    return {
        "pack_path": str(pack_path),
        "review_path": str(_review_companion_path(pack_path)),
        "manifest": manifest,
    }


# ---------------------------------------------------------------------------
# Seal
# ---------------------------------------------------------------------------

def seal_pack(pack_path: Path, *, confirmed: bool) -> Dict[str, Any]:
    """Stamp review.human_reviewed=true. Refuses without an explicit confirmation.

    The sha256 stamped is of the REVIEW.md a human actually read — the loose
    companion file if present (so pre-seal edits count), else the archived
    copy. Any post-seal tampering of the archive's REVIEW.md is then
    detectable by re-hashing and comparing against the stamped value.
    """
    pack_path = Path(pack_path)
    if not pack_path.exists():
        raise SystemExit(f"no such pack: {pack_path}")
    if not confirmed:
        raise SystemExit(
            "seal refused: human review not confirmed — read REVIEW.md, then "
            "pass --yes (or confirm interactively)"
        )

    with tarfile.open(pack_path, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("pack.json").read().decode("utf-8"))
        archived_review = tar.extractfile("REVIEW.md").read().decode("utf-8")
        member_names = [n for n in tar.getnames() if n not in ("pack.json", "REVIEW.md")]
        artifact_bytes = {n: tar.extractfile(n).read() for n in member_names}

    companion = _review_companion_path(pack_path)
    review_text = companion.read_text(encoding="utf-8") if companion.exists() else archived_review

    manifest["review"] = {
        "human_reviewed": True,
        "reviewed_at": _now_iso(),
        "review_manifest_sha256": _sha256_text(review_text),
    }

    with tarfile.open(pack_path, "w:gz") as tar:
        _add_tar_text(tar, "pack.json", json.dumps(manifest, indent=2) + "\n")
        _add_tar_text(tar, "REVIEW.md", review_text)
        for n, data in artifact_bytes.items():
            info = tarfile.TarInfo(name=n)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    companion.write_text(review_text, encoding="utf-8")

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _confirm_review(pack_path: Path) -> bool:
    try:
        ans = input(f"Have you read {_review_companion_path(pack_path)} and confirm "
                     f"this pack is safe to seal? [y/N] ")
    except (EOFError, OSError):
        # No usable stdin (non-interactive shell, captured test run, piped
        # invocation) — conservatively refuse rather than guess.
        return False
    return ans.strip().lower() in ("y", "yes")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="maro-pack",
        description="Produce and seal curated, human-reviewed learning packs (docs/PORTABLE_LEARNING_DESIGN.md).",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="gather Class C+A artifacts into an unsealed pack")
    exp.add_argument("name", help="pack name, e.g. polymarket-research-starter")
    exp.add_argument("--label", required=True, help="human-facing origin label recorded in pack.json")
    exp.add_argument("--workspace", type=Path, default=None, help="source workspace (default: this machine's active workspace)")
    exp.add_argument("--out-dir", type=Path, default=None, help="default: <workspace>/output/packs/")
    exp.add_argument("--include-medium", action="store_true", help="also include medium-tier lessons")
    exp.add_argument("--include-knowledge", action="store_true", help="also include the knowledge web (nodes+edges)")
    exp.add_argument("--include-playbook", action="store_true", help="also include playbook.md")
    exp.add_argument("--include-runs", action="append", default=[], metavar="RUN_ID", help="also include this run's artifacts (repeatable)")
    exp.add_argument("--seal", action="store_true", help="seal immediately after a review confirmation")
    exp.add_argument("--yes", action="store_true", help="skip the interactive confirmation prompt (use with --seal for scripted/CI export)")

    seal_p = sub.add_parser("seal", help="stamp human_reviewed=true after reading REVIEW.md")
    seal_p.add_argument("pack", type=Path)
    seal_p.add_argument("--yes", action="store_true", help="skip the interactive confirmation prompt")

    insp = sub.add_parser("inspect", help="print a pack's manifest")
    insp.add_argument("pack", type=Path)

    args = ap.parse_args(argv)

    if args.command == "export":
        report = export_pack(
            name=args.name, label=args.label, workspace=args.workspace, out_dir=args.out_dir,
            include_medium=args.include_medium, include_knowledge=args.include_knowledge,
            include_playbook=args.include_playbook, include_runs=args.include_runs,
        )
        print(f"wrote {report['pack_path']}")
        print(f"review at {report['review_path']} — read it before sealing")
        if args.seal:
            pack_path = Path(report["pack_path"])
            confirmed = args.yes or _confirm_review(pack_path)
            manifest = seal_pack(pack_path, confirmed=confirmed)
            print(f"sealed: human_reviewed={manifest['review']['human_reviewed']}")
        return 0

    if args.command == "seal":
        confirmed = args.yes or _confirm_review(args.pack)
        manifest = seal_pack(args.pack, confirmed=confirmed)
        print(f"sealed: human_reviewed={manifest['review']['human_reviewed']}")
        return 0

    if args.command == "inspect":
        manifest = read_pack_manifest(args.pack)
        json.dump(manifest, sys.stdout, indent=2)
        print()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
