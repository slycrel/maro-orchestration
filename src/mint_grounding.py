"""Mint-time grounding — evidence receipts on learning-store mints.

docs/MINT_GROUNDING_DESIGN.md (decided 2026-08-06: annotation with
event-log receipts, fail-open, **no new judge**). The claims-vs-events
family: minted artifacts carry method/provenance claims ("authenticated
fetch", "confirmed this session") that the minting run's own event log
contradicts or cannot support, while the data stays real. Five logged
specimens across two model tiers (LT-4 B3/B1w/B3w, LT-5 step-8 fetch +
the reviewer false-dismissal) — model-independent, so the fix is
structural: join each claim against the run's existing byte-level
ground truth (call records' tool_events) and stamp receipts ON the
minted item.

Slice 1 (this module + the memory.py wiring): lesson mints. Zero LLM
calls — claim detection is a verb-pattern lexicon over the observed
family; the join is deterministic. Three statuses, honest by
construction:

  supported   — a receipt was found (an event of the family the claim
                requires, non-error)
  unsupported — the event log affirmatively lacks that family
  unprobed    — probe-shaped claim we can't tie to a specific event
                (the honest default; never guessed)

Fail-open: a stamp never blocks a mint. Consumers weigh it — injection
surfaces render a marker for unsupported claims, the seed-reader skips
unsupported-claim rows as style exemplars. The republish gate (slice 3)
is the only fail-closed point and does not live here yet.

Claim-shape discipline: only PAST-TENSE method assertions are claims
("fetched via CDN", "verified this session"). Imperative advice — the
dominant lesson shape ("verify mounts before running") — asserts
nothing about what happened and is never stamped.

Known v1 edges, on purpose (cuts-first; the >30%-unprobed falsifier in
the design doc is the widen trigger, not silent regex growth):
  - error'd tool events never support a claim (an attempted fetch is
    not evidence the fetch happened) — but a curl that returned an
    HTTP 403 body exits 0 and still counts as a fetch event; HTTP-level
    failure detection is out of v1.
  - the join is run-scoped: "this step" locality is not narrowed
    (run-narrative grounding is slice 4).

claim_probe.py stays the *contestation* lane (fresh shell probes for
reviewer disputes); this is the *mint* lane (receipts from events that
already happened). Shared vocabulary, different verbs.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("maro.mint_grounding")

# Bounds memory on pathological runs; a real 12-step benchmark run logged
# ~200 events, so this is a backstop, not a working limit.
_MAX_EVENTS = 4000
# Event input/output text kept for the keyword join. Receipts point at the
# full record on disk, so the trim only affects tie quality, never truth.
_EVENT_TEXT_CAP = 2000
_MAX_RECEIPTS = 3
_CLAIM_EXCERPT = 160

# --- claim lexicon -----------------------------------------------------------
# Narrow on purpose: past-tense method verbs from the five logged specimens.
# Present-tense/imperative forms are excluded by construction — advice is not
# a claim. Widening waits for the design doc's >30%-unprobed falsifier.
_CLAIM_FAMILIES = (
    # (family, sentence pattern). A sentence can mint one claim per family —
    # "authenticated fetch" is an auth claim AND a fetch claim, and the two
    # can ground differently (B3: fetch supported, auth unsupported).
    ("auth", re.compile(
        r"\bauthenticat\w+\b|\blogged[- ]in\b|\bwith credentials\b", re.I)),
    ("fetch", re.compile(
        r"\bfetched\b|\bdownloaded\b|\bretrieved\b|\bscraped\b", re.I)),
    ("test", re.compile(
        r"\btested\b|\btests? (?:passed|ran)\b|\bpytest\b", re.I)),
    ("execute", re.compile(r"\bexecuted\b|\bran\b", re.I)),
    ("probe", re.compile(
        r"\bverified\b|\bconfirmed\b|\bprobed\b|\bvalidated\b"
        r"|\bmeasured\b|\bchecked\b", re.I)),
)

_NET_CMD = re.compile(r"\bcurl\b|\bwget\b|\bhttps?://", re.I)
# Credential-shaped markers only. Bare "token" is NOT enough: the live
# smoke against LT-5's own specimen run found `token=a` in the public
# syndication-CDN URL — a dummy parameter, no credential — and the loose
# regex marked the "authenticated fetch" claim supported, recreating the
# exact B3 false-support this module exists to refuse. key/token matches
# now require an assigned value of credential-plausible length.
_AUTH_MARK = re.compile(
    r"authorization\s*:|bearer\s+\S|x-api-key"
    r"|api[_-]?key['\"]?\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{8,}"
    r"|token['\"]?\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{8,}"
    r"|--user\b|-u\s+\S+:\S+|--cookie\b|-b\s+\S"
    r"|\blogin\b|passw|credential", re.I)
_TEST_CMD = re.compile(
    r"\bpytest\b|test[-_]safe|\bnpm test\b|\bgo test\b|\bunittest\b"
    r"|\bmake test\b", re.I)

_EXEC_TOOLS = frozenset({"bash", "shell", "run_command"})
_READ_TOOLS = frozenset({"read", "grep", "glob", "ls"})


def _is_exec(ev: Dict[str, Any]) -> bool:
    return ev["name"].lower() in _EXEC_TOOLS


def _is_fetch(ev: Dict[str, Any]) -> bool:
    name = ev["name"].lower()
    if "fetch" in name or "search" in name:
        return True
    return _is_exec(ev) and bool(_NET_CMD.search(ev["input"]))


def _is_auth(ev: Dict[str, Any]) -> bool:
    # Auth is fetch-with-auth-material, not its own tool: an "authenticated
    # fetch" claim needs a network event that carried credentials (B3's
    # unauthenticated r.jina.ai render is exactly what this refuses).
    return _is_fetch(ev) and bool(_AUTH_MARK.search(ev["input"]))


def _is_test(ev: Dict[str, Any]) -> bool:
    return _is_exec(ev) and bool(_TEST_CMD.search(ev["input"]))


def _is_probe(ev: Dict[str, Any]) -> bool:
    # Any active look at the world: command, network, or file inspection.
    return _is_exec(ev) or _is_fetch(ev) or ev["name"].lower() in _READ_TOOLS


# family -> (event predicate, status when candidates exist but none tie).
# The specific families (a fetch event IS what "fetched" asserts, at run
# scope) stay supported on family-level presence; the vague probe verbs
# ("verified", "confirmed") only reach supported through a keyword tie —
# B1w's "blocks confirmed this session" must not be supported by an
# unrelated Bash event.
_FAMILY_RULES = {
    "auth": (_is_auth, "supported"),
    "fetch": (_is_fetch, "supported"),
    "test": (_is_test, "supported"),
    "execute": (_is_exec, "supported"),
    "probe": (_is_probe, "unprobed"),
}

_STOPWORDS = frozenset(
    "this that with from into over then when what were been have has had "
    "the and for not was are all its each also because before after "
    "session step run runs task goal lesson".split())
# Lexicon verbs never count as tie tokens — every candidate would match.
_VERB_TOKENS = frozenset(
    "authenticated fetched downloaded retrieved scraped tested pytest "
    "executed verified confirmed probed validated measured checked "
    "passed logged credentials".split())


def _tie_tokens(sentence: str) -> List[str]:
    toks = re.findall(r"[a-z0-9_.\-/]{4,}", sentence.lower())
    return [t for t in toks
            if t not in _STOPWORDS and t not in _VERB_TOKENS]


def collect_run_tool_events(run_dir) -> Optional[List[Dict[str, Any]]]:
    """Load every tool event from a run dir's call records, with receipt refs.

    Returns None when the run has no readable ``build/calls/`` — no ground
    truth means no stamps (absent, not unsupported-everything). Returns []
    when call records exist but logged zero tool events — that IS ground
    truth (an LLM-only run affirmatively did not probe anything).
    """
    calls_dir = Path(run_dir) / "build" / "calls"
    if not calls_dir.is_dir():
        return None
    events: List[Dict[str, Any]] = []
    for f in sorted(calls_dir.glob("call-*.json")):
        try:
            record = json.loads(f.read_text(errors="replace"))
        except Exception:
            continue
        for i, ev in enumerate(record.get("tool_events") or []):
            if not isinstance(ev, dict):
                continue
            events.append({
                "ref": f"build/calls/{f.name}#tool_events[{i}]",
                "name": str(ev.get("name", "")),
                "input": str(ev.get("input", ""))[:_EVENT_TEXT_CAP],
                "output": str(ev.get("output", ""))[:_EVENT_TEXT_CAP],
                "is_error": ev.get("is_error") is True
                            or str(ev.get("is_error", "")).lower() == "true",
            })
            if len(events) >= _MAX_EVENTS:
                log.debug("collect_run_tool_events: capped at %d events "
                          "for %s", _MAX_EVENTS, run_dir)
                return events
    return events


def extract_claims(text: str) -> List[Dict[str, str]]:
    """Past-tense method claims in ``text``, one per (sentence, family)."""
    claims: List[Dict[str, str]] = []
    for sentence in re.split(r"(?<=[.;!?])\s+|\n+", text or ""):
        sentence = sentence.strip()
        if not sentence:
            continue
        for family, pat in _CLAIM_FAMILIES:
            if pat.search(sentence):
                claims.append({"claim": sentence[:_CLAIM_EXCERPT],
                               "family": family, "_sentence": sentence})
    return claims


def ground_text(text: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Join each claim in ``text`` against ``events``; return receipt stamps.

    Returns [] when the text makes no parseable claims — the absent-key
    discipline upstream keeps stampless rows byte-identical to before.
    """
    stamps: List[Dict[str, Any]] = []
    for c in extract_claims(text):
        pred, untied_status = _FAMILY_RULES[c["family"]]
        # An error'd event is evidence of an attempt, not of the method
        # the claim asserts.
        cands = [e for e in events if not e["is_error"] and pred(e)]
        if not cands:
            stamps.append({
                "claim": c["claim"], "family": c["family"],
                "status": "unsupported", "receipts": [],
                "note": f"no {c['family']}-family tool events in the "
                        f"minting run",
            })
            continue
        toks = _tie_tokens(c["_sentence"])
        scored = []
        for e in cands:
            ev_text = f"{e['name']} {e['input']} {e['output']}".lower()
            score = sum(1 for t in toks if t in ev_text)
            if score:
                scored.append((score, e))
        scored.sort(key=lambda se: -se[0])
        if scored:
            stamps.append({
                "claim": c["claim"], "family": c["family"],
                "status": "supported",
                "receipts": [e["ref"] for _, e in scored[:_MAX_RECEIPTS]],
            })
        elif untied_status == "supported":
            stamps.append({
                "claim": c["claim"], "family": c["family"],
                "status": "supported", "receipts": [cands[0]["ref"]],
                "note": "family-level match; no keyword tie",
            })
        else:
            stamps.append({
                "claim": c["claim"], "family": c["family"],
                "status": "unprobed", "receipts": [],
                "note": f"{len(cands)} probe-family events in the run; "
                        f"none tie to this claim",
            })
    return stamps


def ground_lessons_for_run(lesson_texts: List[str],
                           run_ref: str) -> List[List[Dict[str, Any]]]:
    """Grounding stamps for each lesson, joined against ``run_ref``'s events.

    ``run_ref`` is a loop_id or handle_id (runs.resolve_run_dir accepts
    both). Any failure — unresolvable run, unreadable records — returns
    empty stamp lists: fail-open, the mint proceeds unstamped.
    """
    empty: List[List[Dict[str, Any]]] = [[] for _ in lesson_texts]
    if not run_ref or not lesson_texts:
        return empty
    try:
        from runs import resolve_run_dir
        rd = resolve_run_dir(run_ref)
        if rd is None:
            return empty
        events = collect_run_tool_events(rd)
        if events is None:
            return empty
        return [ground_text(t, events) for t in lesson_texts]
    except Exception as exc:  # annotation must never block a mint
        log.debug("ground_lessons_for_run failed for %s: %s", run_ref, exc)
        return empty


def has_unsupported(grounding: Optional[List[Dict[str, Any]]]) -> bool:
    return any(g.get("status") == "unsupported" for g in grounding or [])


def grounding_summary(grounding: Optional[List[Dict[str, Any]]]) -> str:
    """One-glance readout tag: ``2✓/1✗/1?`` (supported/unsupported/unprobed).

    "" when no stamps — pre-grounding rows render byte-identically. This
    is also the census surface for the design doc's >30%-unprobed
    falsifier (the v2-LLM-extraction trigger).
    """
    if not grounding:
        return ""
    n = {"supported": 0, "unsupported": 0, "unprobed": 0}
    for g in grounding:
        s = str(g.get("status", ""))
        if s in n:
            n[s] += 1
    return (f" [claims: {n['supported']}✓/{n['unsupported']}✗/"
            f"{n['unprobed']}?]")


def grounding_marker(grounding: Optional[List[Dict[str, Any]]]) -> str:
    """Compact inline marker for injection surfaces.

    Only unsupported claims earn prompt space — a supported stamp is the
    quiet case, and unprobed is honest uncertainty, not a warning.
    """
    bad = [g for g in grounding or [] if g.get("status") == "unsupported"]
    if not bad:
        return ""
    heads = "; ".join(str(b.get("claim", ""))[:40] for b in bad[:2])
    plural = "s" if len(bad) != 1 else ""
    return (f' [mint-grounding: {len(bad)} claim{plural} unsupported by '
            f'the minting run\'s event log: "{heads}"]')
