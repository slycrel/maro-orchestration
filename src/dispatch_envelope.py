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
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

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
    safe_key = re.sub(r"[^A-Za-z0-9._-]", "_", str(key)) or "dispatch"
    dest = output_dir() / "dispatch-artifacts" / safe_key
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
