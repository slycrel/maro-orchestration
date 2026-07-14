"""maro-pack — produce, seal, and adopt curated, human-reviewed "learning packs".

Implements docs/PORTABLE_LEARNING_DESIGN.md §7 chunks 3+4 in full: export,
seal, import, and adopt. `maro-import` stays trust-neutral machine-migration;
`maro-pack` owns the trust-demoting curated lifecycle — two tools because
they answer different trust questions (§7 decision).

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
    hashes of the REVIEW.md and its canonical artifact metadata+payload set.
    The payload digest is embedded in REVIEW.md as a local consistency check.
    This is not a signature or proof of who authored the archive. Refuses
    without an explicit confirmation (interactive prompt or ``--yes``).

Import flow: import (trust demotion) -> adopt (explicit promotion).

  * ``import`` refuses unsealed packs (``--allow-unreviewed`` is the escape
    hatch for self-to-self transfers) and newer pack formats outright — never
    best-effort a format we don't understand on trust-bearing data (§6).
    Standing rules demote to ``Hypothesis`` with confirmations/contradictions
    reset to 0; lessons enter MEDIUM tier with score capped at 0.5 and
    ``sessions_validated=0``; skill stats move to ``imported.claimed_*``,
    local counters start fresh — imports are *contested-by-birth* (§3),
    exactly like a locally-observed pattern, and earn trust the same way.
    Content-hash-identical rows are skipped, not double-counted. Skills/
    personas (.md) never land live — always quarantined to
    ``imports/<label>/``; a same-name/different-content collision leaves
    local untouched and notes it in ``imports/<label>/CONFLICTS.md``.
  * ``adopt`` promotes quarantined skills/personas into the live workspace,
    stamping a provenance header into the frontmatter. Never overwrites an
    existing live file of the same name.

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
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _payload_sha256(artifacts: List[Dict[str, Any]], files: Dict[str, bytes]) -> str:
    """Canonical digest of reviewed artifact metadata, paths, and payloads."""
    h = hashlib.sha256()
    by_path = {str(a.get("path", "")): a for a in artifacts}
    for path in sorted(by_path):
        metadata = json.dumps(
            by_path[path], sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        raw = files[path]
        h.update(str(len(metadata)).encode("ascii"))
        h.update(b"\0")
        h.update(metadata)
        h.update(b"\0")
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(str(len(raw)).encode("ascii"))
        h.update(b"\0")
        h.update(raw)
        h.update(b"\0")
    return h.hexdigest()


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
            "review_payload_sha256": None,
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
    payload_sha = _payload_sha256(manifest.get("artifacts", []), artifact_bytes)
    marker = f"Reviewed payload SHA-256: `{payload_sha}`"
    old_marker = "\n\n---\n\nReviewed payload SHA-256: `"
    marker_at = review_text.rfind(old_marker)
    if marker_at >= 0 and review_text[marker_at + len(old_marker):].strip().endswith("`"):
        review_text = review_text[:marker_at]
    # The digest lives in the human-reviewed artifact as well as pack.json.
    # A payload+manifest swap that retains the reviewed copy therefore fails.
    review_text = review_text.rstrip() + f"\n\n---\n\n{marker}\n"

    manifest["review"] = {
        "human_reviewed": True,
        "reviewed_at": _now_iso(),
        "review_manifest_sha256": _sha256_text(review_text),
        "review_payload_sha256": payload_sha,
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
# Import — trust demotion per design doc §3 ("imports are contested-by-birth")
# ---------------------------------------------------------------------------

# Classes chunk 3 deliberately produces but chunk 4 does not merge into live
# trust-bearing stores — always quarantine, preserving their natural
# workspace-relative path. Distinct from *unrecognized* classes (below),
# which is the §6 forward-compat seam for future additive pack_format growth.
_QUARANTINE_ONLY_CLASSES = {"knowledge_nodes", "knowledge_edges", "playbook", "run_artifact"}


def _upgrade_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Apply per-version upgrade shims for older packs (design doc §6).

    No-op today — pack_format has only ever been 1. Wire the chain here
    (``_upgrade_1_to_2(manifest)`` etc.) the day format 2 ships.
    """
    return manifest


def _safe_relpath(relpath: str, *, what: str) -> str:
    """Reject a manifest-supplied relative path that could escape its
    intended root (quarantine dir or live workspace dir). The manifest is
    untrusted input — a sealed pack only proves a human read REVIEW.md, not
    that pack.json's artifact paths are well-formed (boundary-discipline:
    validate at the boundary, trust nothing about the payload's shape)."""
    p = PurePosixPath(relpath)
    if relpath.startswith("/") or p.is_absolute() or ".." in p.parts or not relpath:
        raise SystemExit(f"import refused: unsafe {what} path in manifest: {relpath!r}")
    return relpath


def _safe_label(label: str) -> str:
    if not label or label.startswith("/") or ".." in PurePosixPath(label).parts or "/" in label:
        raise SystemExit(f"import refused: unsafe label: {label!r}")
    return label


def _artifact_relpath(artifact: Dict[str, Any]) -> str:
    p = artifact.get("path", "")
    relpath = p[len("artifacts/"):] if p.startswith("artifacts/") else p
    return _safe_relpath(relpath, what="artifact")


def _import_rules_as_hypotheses(content: str, *, pack_name: str, label: str, pack_tag: str,
                                 now: str, dry_run: bool) -> List[Dict[str, Any]]:
    """Standing rules demote to Hypothesis on arrival (§3 arrival-trust table)."""
    from knowledge_lens import Hypothesis, load_hypotheses, load_standing_rules, _hypotheses_path
    from file_lock import locked_append

    existing_hyp_ids = {h.hyp_id for h in load_hypotheses()}
    existing_texts = {h.lesson for h in load_hypotheses()} | {r.rule for r in load_standing_rules()}
    results: List[Dict[str, Any]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        original_id = row.get("rule_id", "")
        try:
            rule_text = row.get("rule", "")
            hyp_id = f"imported-{pack_name}-{original_id}"
            if hyp_id in existing_hyp_ids:
                results.append({"rule_id": original_id, "outcome": "already_imported"})
                continue
            if rule_text and rule_text in existing_texts:
                results.append({"rule_id": original_id, "outcome": "skipped_identical"})
                continue
            imported = {
                "imported_from": label, "pack": pack_tag,
                "original_id": original_id, "original_class": "rules",
                "original_confirmations": row.get("confirmations"),
                "original_contradictions": row.get("contradictions"),
                "imported_at": now,
            }
            if row.get("imported"):
                imported["original_provenance"] = row["imported"]
            hyp = Hypothesis(
                hyp_id=hyp_id,
                lesson=rule_text,
                domain=row.get("domain", ""),
                confirmations=0,
                contradictions=0,
                source_lesson_ids=[f"imported:{pack_name}/{original_id}"],
                first_seen=now,
                last_seen=now,
                imported=imported,
            )
            if not dry_run:
                locked_append(_hypotheses_path(), json.dumps(hyp.to_dict()))
            existing_hyp_ids.add(hyp_id)
            results.append({"rule_id": original_id, "hyp_id": hyp_id, "outcome": "demoted_to_hypothesis"})
        except Exception as e:
            # A single malformed row must not abort the import (and lose the
            # audit row for everything already written) — quarantine the
            # failure to this row and keep going (fix-root-causes: the root
            # cause of a partial/unaudited import is per-row writes with no
            # per-row fault isolation).
            results.append({"rule_id": original_id, "outcome": "malformed_skipped", "error": str(e)})
    return results


def _import_hypotheses(content: str, *, pack_name: str, label: str, pack_tag: str,
                        now: str, dry_run: bool) -> List[Dict[str, Any]]:
    """Already-hypothesis rows still reset trust on arrival — contested-by-birth
    applies uniformly, not just to the rules-demotion path."""
    from knowledge_lens import Hypothesis, load_hypotheses, load_standing_rules, _hypotheses_path
    from file_lock import locked_append

    existing_hyp_ids = {h.hyp_id for h in load_hypotheses()}
    existing_texts = {h.lesson for h in load_hypotheses()} | {r.rule for r in load_standing_rules()}
    results: List[Dict[str, Any]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        original_id = row.get("hyp_id", "")
        try:
            lesson_text = row.get("lesson", "")
            hyp_id = f"imported-{pack_name}-{original_id}"
            if hyp_id in existing_hyp_ids:
                results.append({"hyp_id": original_id, "outcome": "already_imported"})
                continue
            if lesson_text and lesson_text in existing_texts:
                results.append({"hyp_id": original_id, "outcome": "skipped_identical"})
                continue
            imported = {
                "imported_from": label, "pack": pack_tag,
                "original_id": original_id, "original_class": "hypotheses",
                "original_confirmations": row.get("confirmations"),
                "original_contradictions": row.get("contradictions"),
                "imported_at": now,
            }
            if row.get("imported"):
                imported["original_provenance"] = row["imported"]
            hyp = Hypothesis(
                hyp_id=hyp_id,
                lesson=lesson_text,
                domain=row.get("domain", ""),
                confirmations=0,
                contradictions=0,
                source_lesson_ids=[f"imported:{pack_name}/{original_id}"],
                first_seen=now,
                last_seen=now,
                imported=imported,
            )
            if not dry_run:
                locked_append(_hypotheses_path(), json.dumps(hyp.to_dict()))
            existing_hyp_ids.add(hyp_id)
            results.append({"hyp_id": original_id, "new_hyp_id": hyp_id, "outcome": "imported"})
        except Exception as e:
            results.append({"hyp_id": original_id, "outcome": "malformed_skipped", "error": str(e)})
    return results


def _import_lessons(content: str, *, pack_name: str, label: str, pack_tag: str,
                     now: str, dry_run: bool) -> List[Dict[str, Any]]:
    """Lessons enter MEDIUM tier regardless of origin tier, score capped at 0.5
    (§3 arrival-trust table). Unreinforced, they self-compost under GC decay —
    the border demotes, decay-trust-never-data does the rest."""
    from knowledge_web import TieredLesson, MemoryTier, load_tiered_lessons, _append_tiered_lesson

    existing = (load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
                + load_tiered_lessons(tier=MemoryTier.LONG, limit=None, raw=True))
    existing_ids = {l.lesson_id for l in existing}
    existing_texts = {l.lesson for l in existing}
    results: List[Dict[str, Any]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        original_id = row.get("lesson_id", "")
        try:
            lesson_text = row.get("lesson", "")
            new_id = f"imported-{pack_name}-{original_id}"
            if new_id in existing_ids:
                results.append({"lesson_id": original_id, "outcome": "already_imported"})
                continue
            if lesson_text and lesson_text in existing_texts:
                results.append({"lesson_id": original_id, "outcome": "skipped_identical"})
                continue
            original_score = float(row.get("score", 1.0))
            imported = {
                "imported_from": label, "pack": pack_tag,
                "original_id": original_id, "original_tier": row.get("tier", ""),
                "original_trust": original_score, "imported_at": now,
            }
            if row.get("imported"):
                imported["original_provenance"] = row["imported"]
            tl = TieredLesson(
                lesson_id=new_id,
                task_type=row.get("task_type", ""),
                outcome=row.get("outcome", ""),
                lesson=lesson_text,
                source_goal=row.get("source_goal", ""),
                confidence=row.get("confidence", 0.5),
                tier=MemoryTier.MEDIUM,
                score=min(original_score, 0.5),
                # Transaction time: decay math (knowledge_web._days_since) reads
                # last_reinforced, so this is what stops a 3-month-old import from
                # arriving half-decayed — it gets a fair local hearing starting now.
                last_reinforced=now[:10],
                sessions_validated=0,
                times_applied=0,
                times_reinforced=0,
                recorded_at=now,
                evidence_sources=row.get("evidence_sources", []),
                lesson_type=row.get("lesson_type", "") if row.get("lesson_type") in
                {"execution", "planning", "recovery", "verification", "cost"} else "",
                imported=imported,
            )
            if not dry_run:
                _append_tiered_lesson(tl, tier=MemoryTier.MEDIUM)
            existing_ids.add(new_id)
            results.append({"lesson_id": original_id, "new_id": new_id, "outcome": "imported_medium"})
        except Exception as e:
            results.append({"lesson_id": original_id, "outcome": "malformed_skipped", "error": str(e)})
    return results


def _import_skill_records(content: str, *, pack_name: str, label: str, pack_tag: str,
                           now: str, dry_run: bool) -> List[Dict[str, Any]]:
    """Skill records import with stats moved to imported.claimed_*; local
    counters start fresh (§3 arrival-trust table)."""
    from skills import load_skills, save_skill
    from skill_types import Skill, compute_skill_hash

    existing = load_skills()
    existing_ids = {s.id for s in existing}
    existing_hashes = {s.content_hash for s in existing if s.content_hash}
    results: List[Dict[str, Any]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        original_id = row.get("id", "")
        try:
            new_id = f"imported-{pack_name}-{original_id}"
            if new_id in existing_ids:
                results.append({"id": original_id, "outcome": "already_imported"})
                continue
            imported = {
                "imported_from": label, "pack": pack_tag,
                "original_id": original_id, "original_tier": row.get("tier", ""),
                "claimed_use_count": row.get("use_count", 0),
                "claimed_success_rate": row.get("success_rate", 1.0),
                "imported_at": now,
            }
            if row.get("imported"):
                imported["original_provenance"] = row["imported"]
            sk = Skill(
                id=new_id,
                name=row.get("name", ""),
                description=row.get("description", ""),
                trigger_patterns=row.get("trigger_patterns", []),
                steps_template=row.get("steps_template", []),
                source_loop_ids=row.get("source_loop_ids", []),
                created_at=now,
                use_count=0,
                success_rate=1.0,
                # Always provisional on arrival — an origin "established" tier
                # is a local promotion-history claim, and imports are
                # contested-by-birth just like everything else in §3. The
                # claimed origin tier survives under imported.original_tier.
                tier="provisional",
                utility_score=1.0,
                consecutive_failures=0,
                consecutive_successes=0,
                circuit_state="closed",
                optimization_objective=row.get("optimization_objective", ""),
                island=row.get("island", ""),
                project=row.get("project", ""),
                imported=imported,
            )
            if compute_skill_hash(sk) in existing_hashes:
                results.append({"id": original_id, "outcome": "skipped_identical"})
                continue
            if not dry_run:
                save_skill(sk)
            existing_ids.add(new_id)
            results.append({"id": original_id, "new_id": new_id, "outcome": "imported"})
        except Exception as e:
            results.append({"id": original_id, "outcome": "malformed_skipped", "error": str(e)})
    return results


@contextlib.contextmanager
def _memory_dir_override(ws: Path):
    """Route trust-bearing writers (knowledge_lens/knowledge_web/skills — all
    of which resolve their paths via the global orch_items.memory_dir(), not
    a parameter) at ``ws``, so ``import_pack(..., target=ws)`` actually
    writes hypotheses/lessons/skill records into ``ws`` instead of silently
    falling back to this process's active workspace. No-op if ``ws`` is
    already the active workspace."""
    target_memory = str((ws / "memory").resolve())
    prev = os.environ.get("MARO_MEMORY_DIR")
    if prev is not None and str(Path(prev).expanduser().resolve()) == target_memory:
        yield
        return
    os.environ["MARO_MEMORY_DIR"] = target_memory
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("MARO_MEMORY_DIR", None)
        else:
            os.environ["MARO_MEMORY_DIR"] = prev


def _quarantine_dir(ws: Path, label: str) -> Path:
    return ws / "imports" / label


def _append_conflicts_note(ws: Path, label: str, kind: str, name: str, now: str) -> None:
    path = _quarantine_dir(ws, label) / "CONFLICTS.md"
    line = f"- `{kind}/{name}` — local version differs; import kept in quarantine, local wins ({now})"
    if path.exists():
        if line in path.read_text(encoding="utf-8"):
            return
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# Conflicts — {label}\n\n"
            "Same-name, different-content collisions between this pack's skills/"
            "personas and local ones. Local always wins; these stay in quarantine "
            "— adopt is editorial, not automatic.\n\n"
        )
        path.write_text(header + line + "\n", encoding="utf-8")


def _import_authored_md(ws: Path, kind: str, relpath: str, content: str, *,
                         label: str, now: str, dry_run: bool) -> Dict[str, Any]:
    """Class A (.md) never lands live — always quarantine (§3). Same-name/
    different-content vs. a local file: local wins, note it in CONFLICTS.md."""
    name = Path(relpath).name
    live_path = ws / kind / name
    quarantine_path = _quarantine_dir(ws, label) / kind / name

    if live_path.exists():
        if live_path.read_text(encoding="utf-8") == content:
            return {"name": name, "outcome": "skipped_identical"}
        if not dry_run:
            quarantine_path.parent.mkdir(parents=True, exist_ok=True)
            quarantine_path.write_text(content, encoding="utf-8")
            _append_conflicts_note(ws, label, kind, name, now)
        return {"name": name, "outcome": "conflict_quarantined"}

    if quarantine_path.exists() and quarantine_path.read_text(encoding="utf-8") == content:
        return {"name": name, "outcome": "already_quarantined"}

    if not dry_run:
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        quarantine_path.write_text(content, encoding="utf-8")
    return {"name": name, "outcome": "quarantined"}


def _quarantine_single(ws: Path, label: str, relpath: str, content: str, dry_run: bool) -> Dict[str, Any]:
    path = _quarantine_dir(ws, label) / relpath
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return {"path": relpath, "outcome": "already_quarantined"}
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {"path": relpath, "outcome": "quarantined"}


def import_pack(
    pack_path: Path,
    *,
    label: str,
    target: Optional[Path] = None,
    allow_unreviewed: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Import a pack, demoting trust per §3's arrival-trust table.

    Refuses unsealed packs by default (``allow_unreviewed=True`` is the
    escape hatch for self-to-self transfers). Refuses newer pack formats
    outright — never best-effort a format we don't understand on
    trust-bearing data. Skills/personas always quarantine to
    ``imports/<label>/``; adopting them into the live workspace is a
    separate, explicit step (``adopt``).
    """
    pack_path = Path(pack_path)
    if not pack_path.exists():
        raise SystemExit(f"no such pack: {pack_path}")
    label = _safe_label(label)
    ws = _resolve_workspace(target)

    manifest = read_pack_manifest(pack_path)
    fmt = manifest.get("pack_format", 1)
    if fmt > PACK_FORMAT:
        raise SystemExit(f"pack format {fmt} > supported {PACK_FORMAT} — upgrade maro")
    manifest = _upgrade_manifest(manifest)

    review = manifest.get("review", {}) or {}
    if not review.get("human_reviewed") and not allow_unreviewed:
        raise SystemExit(
            "import refused: pack is not sealed (review.human_reviewed=false) — "
            "read REVIEW.md and run `maro-pack seal`, or pass --allow-unreviewed "
            "for a self-to-self transfer"
        )

    with tarfile.open(pack_path, "r:gz") as tar:
        archived_review = tar.extractfile("REVIEW.md").read().decode("utf-8")
        artifact_bytes = {
            a["path"]: tar.extractfile(a["path"]).read().decode("utf-8")
            for a in manifest.get("artifacts", [])
        }

    if review.get("human_reviewed"):
        expected_hash = review.get("review_manifest_sha256")
        if not expected_hash or _sha256_text(archived_review) != expected_hash:
            raise SystemExit(
                "import refused: REVIEW.md in the archive does not match the sealed "
                "hash — possible post-seal tampering"
            )

        expected_payload = review.get("review_payload_sha256")
        marker = f"Reviewed payload SHA-256: `{expected_payload}`" if expected_payload else ""
        actual_payload = _payload_sha256(
            manifest.get("artifacts", []),
            {k: v.encode("utf-8") for k, v in artifact_bytes.items()},
        )
        if (not expected_payload or marker not in archived_review
                or actual_payload != expected_payload):
            raise SystemExit(
                "import refused: archived artifacts do not match the payload digest "
                "embedded in the human-reviewed REVIEW.md"
            )

    # Independently verify every artifact's declared sha256 too. Fail closed
    # on missing or mismatched hashes even for --allow-unreviewed imports.
    for a in manifest.get("artifacts", []):
        p = a.get("path", "")
        declared = a.get("sha256")
        actual = _sha256_text(artifact_bytes.get(p, ""))
        if not declared or actual != declared:
            raise SystemExit(
                f"import refused: artifact {p!r} does not match its manifest "
                "sha256 — possible post-seal tampering"
            )

    pack_name = manifest.get("name", pack_path.stem)
    pack_tag = f"{pack_name}@{_sha256_file(pack_path)[:8]}"
    now = _now_iso()

    report: Dict[str, Any] = {
        "pack": pack_name, "pack_tag": pack_tag, "label": label,
        "imported_at": now, "dry_run": dry_run,
        "rules_demoted_to_hypotheses": [], "hypotheses_imported": [],
        "lessons_imported": [], "skill_records_imported": [],
        "skills_md": [], "personas_md": [],
        "quarantined": [], "quarantined_unknown": [],
    }

    with _memory_dir_override(ws):
        for artifact in manifest.get("artifacts", []):
            cls = artifact.get("class", "")
            relpath = _artifact_relpath(artifact)
            content = artifact_bytes.get(artifact.get("path", ""), "")
            if cls == "rules":
                report["rules_demoted_to_hypotheses"].extend(_import_rules_as_hypotheses(
                    content, pack_name=pack_name, label=label, pack_tag=pack_tag, now=now, dry_run=dry_run))
            elif cls == "hypotheses":
                report["hypotheses_imported"].extend(_import_hypotheses(
                    content, pack_name=pack_name, label=label, pack_tag=pack_tag, now=now, dry_run=dry_run))
            elif cls in ("lessons", "lessons_medium"):
                report["lessons_imported"].extend(_import_lessons(
                    content, pack_name=pack_name, label=label, pack_tag=pack_tag, now=now, dry_run=dry_run))
            elif cls == "skill_records":
                report["skill_records_imported"].extend(_import_skill_records(
                    content, pack_name=pack_name, label=label, pack_tag=pack_tag, now=now, dry_run=dry_run))
            elif cls == "skill_md":
                report["skills_md"].append(_import_authored_md(
                    ws, "skills", relpath, content, label=label, now=now, dry_run=dry_run))
            elif cls == "persona_md":
                report["personas_md"].append(_import_authored_md(
                    ws, "personas", relpath, content, label=label, now=now, dry_run=dry_run))
            elif cls in _QUARANTINE_ONLY_CLASSES:
                report["quarantined"].append({
                    "class": cls, **_quarantine_single(ws, label, relpath, content, dry_run)})
            else:
                report["quarantined_unknown"].append({
                    "class": cls, **_quarantine_single(ws, label, f"unknown/{relpath}", content, dry_run)})

    if not dry_run:
        from file_lock import locked_append
        audit = ws / "memory" / "imports.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        locked_append(audit, json.dumps({**report, "action": "pack_import"}))

    return report


# ---------------------------------------------------------------------------
# Adopt — the explicit gate from quarantine to live (§3 "Adoption")
# ---------------------------------------------------------------------------

def _stamp_provenance_frontmatter(content: str, *, label: str, now: str) -> str:
    provenance = f"imported_from: {label}\nadopted_at: {now}"
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        if end != -1:
            return f"{content[:end]}\n{provenance}{content[end:]}"
    return f"---\n{provenance}\n---\n{content}"


def adopt(
    label: str,
    *,
    items: Optional[List[str]] = None,
    all_items: bool = False,
    target: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Promote quarantined skills/personas into the live workspace.

    Never overwrites a live file of the same name — that case was already
    flagged as a conflict at import time (local wins, stays in quarantine).
    """
    label = _safe_label(label)
    ws = _resolve_workspace(target)
    quarantine_dir = _quarantine_dir(ws, label)
    if not quarantine_dir.is_dir():
        raise SystemExit(f"no quarantined imports for label {label!r} under {quarantine_dir}")

    candidates: List[tuple] = []
    for kind in ("skills", "personas"):
        d = quarantine_dir / kind
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                candidates.append((kind, f.name, f))

    if all_items:
        selected = candidates
    else:
        names = set(items or [])
        if not names:
            raise SystemExit("adopt: specify skill/persona names, or pass --all")
        selected = []
        found = set()
        for kind, name, path in candidates:
            stem = Path(name).stem
            if name in names or stem in names:
                selected.append((kind, name, path))
                found.add(name if name in names else stem)
        missing = names - found
        if missing:
            raise SystemExit(f"adopt: not found in {quarantine_dir}: {sorted(missing)}")

    now = _now_iso()
    adopted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for kind, name, src_path in selected:
        dest = ws / kind / name
        stamped = _stamp_provenance_frontmatter(src_path.read_text(encoding="utf-8"), label=label, now=now)
        if dry_run:
            # No side-effecting probe available in dry-run; a plain exists()
            # check is inherently racy, but dry-run never writes so the race
            # has no consequence — it's report-only.
            if dest.exists():
                skipped.append({"kind": kind, "name": name, "reason": "already exists locally"})
            else:
                adopted.append({"kind": kind, "name": name})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            skipped.append({"kind": kind, "name": name, "reason": "already exists locally"})
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(stamped)
        adopted.append({"kind": kind, "name": name})

    report = {"label": label, "adopted": adopted, "skipped": skipped, "adopted_at": now, "dry_run": dry_run}
    if not dry_run and adopted:
        from file_lock import locked_append
        audit = ws / "memory" / "imports.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        locked_append(audit, json.dumps({**report, "action": "adopt"}))
    return report


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

    imp = sub.add_parser("import", help="import a sealed pack (trust demotion, quarantine)")
    imp.add_argument("pack", type=Path)
    imp.add_argument("--label", required=True, help="provenance label for this import")
    imp.add_argument("--target", type=Path, default=None, help="target workspace (default: this machine's active workspace)")
    imp.add_argument("--allow-unreviewed", action="store_true", help="import an unsealed pack (self-to-self transfers only)")
    imp.add_argument("--dry-run", action="store_true", help="report what would be imported without writing")

    adopt_p = sub.add_parser("adopt", help="promote quarantined skills/personas into the live workspace")
    adopt_p.add_argument("label")
    adopt_p.add_argument("items", nargs="*", help="skill/persona filenames to adopt, e.g. foo.md")
    adopt_p.add_argument("--all", action="store_true", dest="all_items", help="adopt everything quarantined under this label")
    adopt_p.add_argument("--target", type=Path, default=None)
    adopt_p.add_argument("--dry-run", action="store_true")

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

    if args.command == "import":
        report = import_pack(
            args.pack, label=args.label, target=args.target,
            allow_unreviewed=args.allow_unreviewed, dry_run=args.dry_run,
        )
        json.dump(report, sys.stdout, indent=2)
        print()
        return 0

    if args.command == "adopt":
        report = adopt(
            args.label, items=args.items, all_items=args.all_items,
            target=args.target, dry_run=args.dry_run,
        )
        json.dump(report, sys.stdout, indent=2)
        print()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
