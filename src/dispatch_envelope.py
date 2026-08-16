"""Typed dispatch envelope — box-side intake (docs/DISPATCH_ENVELOPE.md).

Channel separation at the dispatch boundary. A dispatching agent (Hermes/Poe)
wraps the user's verbatim ask in a small JSON envelope so the box can keep
three things structurally apart:

  * ``user_ask``           — THE goal. Closure judges it; lessons may
                             generalize from its outcome. Nothing else in the
                             envelope is allowed to become the goal string.
  * operator fields        — advisory framing from the dispatching agent
                             (context, run-scoped constraints). These ride the
                             run's ancestry-context channel and never reach
                             ``reflect_and_record`` — lesson extraction sees
                             goal + outcome only, so operator prose is
                             excluded from learning by construction, not by
                             prompt (the db37d525 contamination class).
  * ``attached_artifacts`` — reference files, written to disk with provenance
                             sidecars before the run starts (artifacts over
                             streams: the file is the durable copy, the
                             envelope was just transport).

Machine-to-machine only (Jeremy UX decree): humans keep typing prose, and
untyped prose dispatches keep working unchanged (``parse_dispatch_payload``
returns None). A payload that DECLARES ``maro-dispatch/v1`` and then breaks
the shape fails LOUD (EnvelopeError) — the dispatcher is a machine, and
silence would run JSON soup as a goal.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

ENVELOPE_VERSION = "maro-dispatch/v1"


class EnvelopeError(ValueError):
    """A payload declared the envelope version but violated its shape."""


@dataclass(frozen=True)
class DispatchEnvelope:
    user_ask: str
    operator_context: str = ""
    operator_constraints: Tuple[str, ...] = ()
    attached_artifacts: Tuple[dict, ...] = ()


def parse_dispatch_payload(payload) -> Optional[DispatchEnvelope]:
    """Parse a dispatch payload; None means untyped prose (the fallback lane).

    Only raises EnvelopeError when the payload opts INTO the contract
    (parses as a JSON object with ``"envelope": "maro-dispatch/v1"``) and
    then breaks it. Anything else — prose, non-JSON, JSON without the
    version key — is a legacy prose dispatch and returns None untouched.
    """
    if not isinstance(payload, str):
        return None
    text = payload.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # A `{`-leading payload that names the contract version but fails to
        # parse is almost certainly a truncated/corrupted typed dispatch —
        # silently running it as prose would execute a mangled goal
        # (2026-07-29 review find). Machine-to-machine: fail loud.
        if ENVELOPE_VERSION in text:
            raise EnvelopeError(
                f"payload mentions {ENVELOPE_VERSION} but is not valid JSON "
                "(truncated or corrupted dispatch?)")
        return None
    if not isinstance(data, dict) or data.get("envelope") != ENVELOPE_VERSION:
        return None

    ask = data.get("user_ask")
    if not isinstance(ask, str) or not ask.strip():
        raise EnvelopeError(
            f"envelope declares {ENVELOPE_VERSION} but user_ask is missing or empty")
    context = data.get("operator_context", "")
    if not isinstance(context, str):
        raise EnvelopeError("operator_context must be a string")
    constraints = data.get("operator_constraints", [])
    if (not isinstance(constraints, list)
            or not all(isinstance(c, str) for c in constraints)):
        raise EnvelopeError("operator_constraints must be a list of strings")
    artifacts = data.get("attached_artifacts", [])
    if (not isinstance(artifacts, list)
            or not all(isinstance(a, dict) for a in artifacts)):
        raise EnvelopeError("attached_artifacts must be a list of objects")
    for art in artifacts:
        name = art.get("name")
        if not isinstance(name, str) or not name.strip():
            raise EnvelopeError("attached artifact missing a non-empty name")
        if not isinstance(art.get("content"), str):
            raise EnvelopeError(f"attached artifact {name!r} missing string content")

    return DispatchEnvelope(
        user_ask=ask.strip(),
        operator_context=context.strip(),
        operator_constraints=tuple(c.strip() for c in constraints if c.strip()),
        attached_artifacts=tuple(artifacts),
    )


def _safe_name(name: str) -> str:
    # Basename only — an artifact name is a label, never a path, so traversal
    # is impossible by construction rather than by validation.
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._") or "artifact"
    return base[:120]


def _safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(key)) or "dispatch"


def store_attachments(env: DispatchEnvelope, *, key: str) -> List[dict]:
    """Write attached artifacts under output/dispatch-artifacts/<key>/.

    Each artifact gets a provenance sidecar (<name>.provenance.json) carrying
    the dispatcher-claimed source plus sha256/byte-count of what actually
    landed. Returns [{name, path, source}] for the operator block. Raises on
    I/O failure — a dispatch that promised reference material must not run
    silently without it.
    """
    if not env.attached_artifacts:
        return []
    from config import output_dir
    dest = output_dir() / "dispatch-artifacts" / _safe_key(key)
    dest.mkdir(parents=True, exist_ok=True)
    stored: List[dict] = []
    taken: set = set()
    for art in env.attached_artifacts:
        base = _safe_name(str(art.get("name", "")))
        candidate, i = base, 1
        while candidate in taken:
            candidate = f"{i}-{base}"
            i += 1
        taken.add(candidate)
        path = dest / candidate
        content = str(art.get("content", ""))
        path.write_text(content, encoding="utf-8")
        source = str(art.get("source", "") or "")
        sidecar = {
            "name": art.get("name"),
            "source": source,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "bytes": len(content.encode("utf-8")),
            "stored_at": datetime.now(timezone.utc).isoformat(),
            "dispatch_key": str(key),
        }
        path.with_name(candidate + ".provenance.json").write_text(
            json.dumps(sidecar, indent=1), encoding="utf-8")
        stored.append({"name": str(art.get("name")), "path": str(path),
                       "source": source})
    return stored


# Operator attachments are a SEPARATE lane from dispatcher attachments, and
# the separation is the point. A dispatcher's payload is untrusted machine
# input, so store_attachments writes only TEXT it was handed. An operator
# attachment is a local file the person running the goal chose — different
# provenance, and it must support bytes (the case that opened this: a
# screenshot of a paper). Widening store_attachments to binary would have
# handed every dispatcher a binary write primitive to buy that; a sibling
# function keeps the trust boundary where the 2026-07-29 envelope review
# put it.
_MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024


def store_operator_attachments(paths, *, key: str) -> List[dict]:
    """Copy operator-chosen local files under output/operator-attachments/<key>/.

    Same provenance-sidecar contract as store_attachments (sha256 + byte
    count of what actually landed), but the source is a local path the
    operator named rather than content a dispatcher supplied. Raises on a
    named file that cannot be read: an operator who attached a file and got
    a run without it has been silently ignored, which is worse than a
    refusal.
    """
    out: List[dict] = []
    if not paths:
        return out
    from config import output_dir
    dest = output_dir() / "operator-attachments" / _safe_key(key)
    dest.mkdir(parents=True, exist_ok=True)
    for raw in paths:
        src = Path(str(raw)).expanduser()
        if not src.is_file():
            raise EnvelopeError(f"attachment not found or not a file: {src}")
        size = src.stat().st_size
        if size > _MAX_ATTACHMENT_BYTES:
            raise EnvelopeError(
                f"attachment {src.name} is {size} bytes, over the "
                f"{_MAX_ATTACHMENT_BYTES}-byte limit")
        data = src.read_bytes()
        base = _safe_name(src.name)
        target = dest / base
        n = 1
        while target.exists() and target.read_bytes() != data:
            n += 1
            target = dest / f"{Path(base).stem}-{n}{Path(base).suffix}"
        target.write_bytes(data)
        rec = {
            "name": base,
            "path": str(target),
            "source": f"operator:{src}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        target.with_name(target.name + ".provenance.json").write_text(
            json.dumps({**rec, "lane": "operator"}, indent=2), encoding="utf-8")
        out.append(rec)
    return out


def land_operator_attachments(run_dir, key: str) -> int:
    """Copy stored operator attachments into <run_dir>/fetch-raw/operator/.

    Why this exists rather than an absolute path in the goal: the container
    executor HARD-EXCLUDES the workspace root from its mount map, so a file
    under output/ is unreadable from inside a containerized worker. The run
    dir is the run's cwd and is mounted rw — landing here is what makes an
    attachment actually reachable by the step that must read it.
    """
    return _land(run_dir, "operator-attachments", key, "operator")


def _land(run_dir, area: str, key: str, sub: str) -> int:
    src = None
    try:
        from config import output_dir
        src = output_dir() / area / _safe_key(key)
        if not src.is_dir():
            return 0
        dest = Path(run_dir) / "fetch-raw" / sub
        dest.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in sorted(src.iterdir()):
            if not f.is_file():
                continue
            target = dest / f.name
            if target.exists():
                continue
            target.write_bytes(f.read_bytes())
            copied += 1
        return copied
    except Exception as exc:
        log.warning("%s artifacts did not land in run dir (%s): %s",
                    sub, src, exc)
        return 0


def operator_attachment_block(stored: Sequence[dict]) -> str:
    """The advisory block naming landed attachments for the run prompt.

    Names the RUN-DIR-relative path, because that is the one a containerized
    worker can open, and states what the file is and is not: operator-
    supplied context, not something the run retrieved. A claim read off an
    attachment is only as good as the attachment, and the run should say so
    rather than laundering it into a retrieved fact.
    """
    if not stored:
        return ""
    lines = ["Operator-attached files (supplied by the person who set this "
             "goal, NOT retrieved by this run):"]
    for rec in stored:
        lines.append(
            f"  - fetch-raw/operator/{rec['name']} "
            f"(from {rec['source']}, {rec['bytes']} bytes, "
            f"sha256 {rec['sha256'][:12]}…)")
    lines.append(
        "Read them from that path when a claim depends on their contents. "
        "Anything you take from one is operator-supplied evidence: cite it "
        "as the attachment, never as a source you retrieved, and say so if "
        "it is the only support for a claim.")
    return "\n".join(lines)


def land_in_run_dir(run_dir, job_id: str) -> int:
    """Copy this dispatch's stored attachments into the run dir
    (<run_dir>/fetch-raw/dispatch/, provenance sidecars included).

    The output/dispatch-artifacts/<job_id>/ copy is the dispatch-side
    record; runs are self-contained artifact trees (artifacts-over-streams
    decree), and the container executor hard-excludes the workspace root
    from its mount map — the run-dir copy is the one that travels with the
    run. Fail-soft by contract (unlike store_attachments): the operator
    block already references the dispatch-side paths, so on the subprocess
    lane a copy failure degrades self-containment rather than the run.
    Idempotent: existing files are left alone. Returns files copied.
    """
    src = None
    try:
        from config import output_dir
        src = output_dir() / "dispatch-artifacts" / _safe_key(job_id)
        if not src.is_dir():
            return 0
        dest = Path(run_dir) / "fetch-raw" / "dispatch"
        dest.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in sorted(src.iterdir()):
            if not f.is_file():
                continue
            target = dest / f.name
            if target.exists():
                continue
            target.write_bytes(f.read_bytes())
            copied += 1
        return copied
    except Exception as exc:
        log.warning("dispatch artifacts did not land in run dir (%s): %s",
                    src, exc)
        return 0


def operator_block(env: DispatchEnvelope, stored: Sequence[dict] = ()) -> str:
    """Render operator fields as one labeled advisory context block.

    Returns "" when the envelope carries nothing beyond the ask — callers
    treat that as "no operator channel". The authority level is stated
    in-band because the run prompt is the only place the model sees it:
    advisory framing, not part of the user's ask.
    """
    parts: List[str] = []
    if env.operator_context:
        parts.append(env.operator_context)
    if env.operator_constraints:
        parts.append("Operator constraints (apply to THIS run only):\n"
                     + "\n".join(f"- {c}" for c in env.operator_constraints))
    if stored:
        lines = []
        for rec in stored:
            src = f" (source: {rec['source']})" if rec.get("source") else ""
            lines.append(f"- {rec['name']} → {rec['path']}{src}")
        parts.append("Attached reference artifacts on disk:\n" + "\n".join(lines))
    if not parts:
        return ""
    return (
        "== Operator dispatch context (advisory — authored by the dispatching "
        "agent, NOT part of the user's ask) ==\n"
        + "\n\n".join(parts)
        + "\n== End operator context =="
    )
