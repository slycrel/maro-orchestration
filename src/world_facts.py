#!/usr/bin/env python3
"""Run-scoped world-fact ledger — plan-native home for non-action findings.

WORLD_FACTS_DESIGN slice 1 (Jeremy 2026-08-02: *"we need to add non-action
types to the planner. I think 2 types of world facts: anecdotal/accidentally
found and hypothesis type findings (pattern recognition/ideas)"*; §7
decisions taken 2026-08-06).

A run that stumbles onto a fact ("archive X is blocked", "the dataset has a
second sheet") or forms a pattern hypothesis ("these failures cluster by
transport") previously had no plan-native place to put it — it leaked into
step prose or died with the step.

Design notes (mirrors terrain.py deliberately — same seam, same discipline):
- **Declared, not scanned.** Facts arrive via a `world_facts` side-channel
  on the executor's completion tool (mirroring the `decisions` directive):
  the declarer says what kind it is; no LLM classification pass.
- **Run-scoped with checkpoint carry.** The ledger lives on LoopContext and
  rides the run checkpoint, so a resume or replan sees the facts, not just
  the surviving steps (the plan-native half of the ask).
- **Hypothesis quarantine (§7.1, decided).** `render()` emits ANECDOTAL
  facts only. An unconfirmed pattern guess injected as fact is exactly the
  self-referential-hedging failure §4c routed around; hypotheses are
  ledgered (forward-compatible, slice-2 lands them in `observe_pattern`)
  but never enter step prompts until confirmed.
- **Observation only.** Nothing here changes step status or routing; the
  render is one advisory contribution through the §6 ledger seam. Empty
  render when nothing is known — the ContributionLedger's hard contract is
  that zero contributions leave prompts byte-identical.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger("maro.world_facts")

KIND_ANECDOTAL = "anecdotal"
KIND_HYPOTHESIS = "hypothesis"
KINDS = (KIND_ANECDOTAL, KIND_HYPOTHESIS)

# Per-step declaration cap (§7.3: build-time tuning). Malformed entries must
# not consume it — cap applied AFTER validation, same rule the decisions
# directive learned in chunk-3 review.
MAX_FACTS_PER_STEP = 3
# Cap the rendered block: facts are a hint, not a wall of text (§4
# injection-regression falsifier watches this).
MAX_RENDERED_FACTS = 10
# Guesses render behind a smaller cap than facts — they're the risky half
# of the block (§7.1 as amended 2026-08-11).
MAX_RENDERED_HYPOTHESES = 3
MAX_FACT_CHARS = 300
MAX_EVIDENCE_CHARS = 300
# Per-run landing caps (§7.3, Jeremy 2026-08-11: "more shallow (2-3 per
# run) for the moment"): strongest facts land first; the remainder stays
# run-scoped (the kept checkpoint is the audit copy — retention decree).
MAX_LANDED_ANECDOTAL = 3
MAX_LANDED_HYPOTHESES = 2


# Fact provenance (slice 3). "step" = discovered by execution; "planner" =
# declared at decompose from INJECTED context — i.e. derived from what the
# stores already said. Provenance is sticky: a step restating a rendered
# planner fact saw it as "treat as known" first, so the restatement is
# laundering, not independent corroboration (the portable-learning
# provenance-stamp lesson). land_facts() skips planner-sourced facts.
SOURCE_STEP = "step"
SOURCE_PLANNER = "planner"


@dataclass
class WorldFact:
    """One declared fact, with the evidence that says so."""
    kind: str
    fact: str
    evidence: str
    first_step: int
    hits: int = 1
    steps: List[int] = field(default_factory=list)
    source: str = SOURCE_STEP

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "fact": self.fact, "evidence": self.evidence,
            "first_step": self.first_step, "hits": self.hits,
            "steps": list(self.steps), "source": self.source,
        }


def _scan_clean(text: str) -> bool:
    """Deterministic injection scan, fail-closed (guard unavailable → not
    clean). Round-2 review: the scan must live at LEDGER INGRESS — the
    completion tool is one producer, but a hand-built parallel outcome or a
    corrupted checkpoint reaches observe()/from_list() without ever passing
    clean_declared, and rendered facts carry "treat as known" authority."""
    try:
        from injection_guard import scan_content
        return scan_content(text, source="world_facts").is_clean
    except Exception:
        return False


@dataclass
class WorldFactLedger:
    """Run-scoped accumulator. Lives on LoopContext; rides the checkpoint."""
    facts: Dict[str, WorldFact] = field(default_factory=dict)

    @staticmethod
    def _key(kind: str, fact: str) -> str:
        # Keyed by kind + normalized text: the same finding restated in a
        # later step bumps hits instead of duplicating a ledger row.
        return f"{kind}:{' '.join(fact.lower().split())}"

    def _planner_twin(self, kind: str, fact_norm: str) -> Optional[WorldFact]:
        """A planner-sourced same-kind row this text is a near-restatement of.

        Laundering guard (2026-08-09 adversarial review): exact-key matching
        let "Archive X is blocked." re-declare planner-seeded "archive x is
        blocked" as a fresh, LANDABLE step fact — provenance stickiness has
        to survive at least surface restatements. Lexical only; a true
        semantic paraphrase still slips this (named residual).
        """
        import difflib
        for f in self.facts.values():
            if f.kind != kind or f.source != SOURCE_PLANNER:
                continue
            other = " ".join(f.fact.lower().split())
            if difflib.SequenceMatcher(None, fact_norm, other).ratio() >= 0.85:
                return f
        return None

    def observe(self, kind: str, fact: str, evidence: str,
                step_idx: int, *, source: str = SOURCE_STEP) -> bool:
        """Record one declared fact. Returns True if newly ledgered.

        First evidence wins, matching terrain's first-reason-wins: the
        original observation is the one later corroborations point back to.
        Provenance is first-writer-wins and sticky (see SOURCE_* note).
        """
        fact = (fact or "").strip()
        if not fact or kind not in KINDS:
            return False
        if not _scan_clean(f"{fact}\n{evidence or ''}"):
            return False
        key = self._key(kind, fact)
        existing = self.facts.get(key)
        if existing is None:
            existing = self._planner_twin(kind, " ".join(fact.lower().split()))
        if existing is None:
            self.facts[key] = WorldFact(
                kind=kind, fact=fact[:MAX_FACT_CHARS],
                evidence=(evidence or "").strip()[:MAX_EVIDENCE_CHARS],
                first_step=step_idx, steps=[step_idx],
                # Unknown source strings are PRESERVED, not coerced to
                # "step" (review: coercion made any future producer's facts
                # silently landable — the landing side allowlists instead).
                source=str(source) if source else SOURCE_STEP)
            return True
        # Corroboration counts distinct steps, not repeated declarations —
        # one step restating a fact three times in a payload must not
        # outrank a genuinely re-observed fact in the landing sort
        # (2026-08-09 adversarial review).
        if step_idx not in existing.steps:
            existing.hits += 1
            existing.steps.append(step_idx)
        return False

    def anecdotal(self) -> List[WorldFact]:
        return [f for f in self.facts.values() if f.kind == KIND_ANECDOTAL]

    def hypotheses(self) -> List[WorldFact]:
        """Slice-2 seam: finalize routes these into observe_pattern()."""
        return [f for f in self.facts.values() if f.kind == KIND_HYPOTHESIS]

    def render(self) -> str:
        """Advisory known-this-run block.

        Anecdotal facts render as established; hypothesis facts render in a
        separately-labeled guesses section (§7.1 as amended by Jeremy
        2026-08-11: prompting them in is fine "as long as they're honest
        about what they are and aren't" — the label IS the honesty, and the
        two sections must never mix).

        Empty render is load-bearing: zero contributions must leave prompts
        byte-identical.
        """
        lines: List[str] = []
        rows = sorted(self.anecdotal(), key=lambda f: (-f.hits, f.fact))
        if rows:
            lines.append(
                "Facts already established THIS RUN — treat as known; do "
                "not re-derive them:"
            )
            for f in rows[:MAX_RENDERED_FACTS]:
                line = f"  - {f.fact}"
                if f.evidence:
                    line += f" (evidence: {f.evidence})"
                if f.hits > 1:
                    line += f" [{f.hits}× since step {f.first_step}]"
                lines.append(line)
            if len(rows) > MAX_RENDERED_FACTS:
                lines.append(f"  …and {len(rows) - MAX_RENDERED_FACTS} more.")
        guesses = sorted(self.hypotheses(), key=lambda f: (-f.hits, f.fact))
        if guesses:
            lines.append(
                "Unconfirmed guesses declared THIS RUN — assumptions, NOT "
                "established facts; verify before relying on one and never "
                "restate one as fact:"
            )
            for f in guesses[:MAX_RENDERED_HYPOTHESES]:
                line = f"  - {f.fact}"
                if f.evidence:
                    line += f" (basis: {f.evidence})"
                lines.append(line)
            if len(guesses) > MAX_RENDERED_HYPOTHESES:
                lines.append(
                    f"  …and {len(guesses) - MAX_RENDERED_HYPOTHESES} more."
                )
        return "\n".join(lines)

    # -- checkpoint carry -------------------------------------------------

    def to_list(self) -> List[Dict[str, Any]]:
        return [f.to_dict() for f in self.facts.values()]

    @classmethod
    def from_list(cls, rows: Optional[Iterable[Any]]) -> "WorldFactLedger":
        """Rebuild from checkpoint rows. Malformed rows are dropped, never
        raised — a corrupt checkpoint must not block a resume."""
        ledger = cls()
        for row in rows or []:
            # Per-row try: one corrupt row (e.g. a non-numeric first_step)
            # must cost only itself, never the valid rows around it
            # (2026-08-08 adversarial review — int() used to raise out of
            # the whole restore).
            try:
                if not isinstance(row, dict):
                    continue
                kind = row.get("kind", "")
                fact = str(row.get("fact", "")).strip()
                if kind not in KINDS or not fact:
                    continue
                evidence_raw = str(row.get("evidence", "")).strip()
                # Restore is an ingress too: a corrupted checkpoint must not
                # smuggle an instruction-shaped fact past the declaration
                # scan (round-2 review).
                if not _scan_clean(f"{fact}\n{evidence_raw}"):
                    continue
                wf = WorldFact(
                    kind=kind, fact=fact[:MAX_FACT_CHARS],
                    evidence=evidence_raw[:MAX_EVIDENCE_CHARS],
                    first_step=int(row.get("first_step", 0) or 0),
                    hits=max(1, int(row.get("hits", 1) or 1)),
                    steps=[int(s) for s in row.get("steps", [])
                           if isinstance(s, (int, float))],
                    # ABSENT provenance restores as "step" (pre-slice-3
                    # checkpoints have no source key; all their facts were
                    # step-declared). A PRESENT unknown string is kept as-is
                    # — the landing allowlist decides what it may do.
                    source=str(row.get("source") or SOURCE_STEP),
                )
                ledger.facts[cls._key(kind, wf.fact)] = wf
            except Exception:
                continue
        return ledger


def land_facts(ledger: Optional["WorldFactLedger"], *, loop_id: str,
               project: str = "", dry_run: bool = False) -> Dict[str, int]:
    """Slice-2 landing: route the run's facts into durable stores at finalize.

    Split by kind, no new lifecycle (WORLD_FACTS_DESIGN §3c):
    - **anecdotal** → knowledge-web CANDIDATE nodes via the existing bridge
      upsert. Born invisible; the V3 promotion sweep (re-observation, or
      age+content with a judged-valid verdict) is the generalizability
      filter — a run-specific fact that never re-observes stays candidate
      and never reaches an injection surface.
    - **hypothesis** → ``observe_pattern()``: minted as Hypothesis, earns
      StandingRule status only through cross-run re-declaration, dies
      quietly if never confirmed. Pattern-guesses get confirmation
      machinery, not belief.

    Landing is verdict-independent by design: "archive X is blocked" is
    exactly the kind of fact a FAILED run discovers, so this runs at
    finalize, not behind the deferred-learning verdict gate.

    Idempotent per (run dir, fact key), loop-id-INDEPENDENT: a run demoted
    done→incomplete resumes under a FRESH loop_id (loop_init always mints
    one) with the same restored ledger — a loop-id-equality stamp guarded
    nothing real (2026-08-09 adversarial review, 3-lens). The run dir's
    metadata instead records the ledger KEYS that landed successfully;
    already-landed keys are skipped, so a restored hypothesis cannot
    confirm ITSELF to the standing-rule threshold
    (RULE_PROMOTE_CONFIRMATIONS=2), while facts declared only in the
    resumed portion still land. Per-key recording also means a fact whose
    store write failed transiently retries on the next finalize instead of
    being buried under an all-or-nothing stamp. Named residuals: a crash
    between a store write and the key stamp can double-land that one fact;
    topologies with no run dir land fail-open (they finalize once).

    Never raises; every store write is per-fact best-effort. Returns counts
    for logging/tests.
    """
    counts = {"anecdotal": 0, "hypotheses": 0}
    if ledger is None or not ledger.facts or dry_run:
        return counts
    if not world_facts_enabled():
        # Kill switch gates landing like it gates capture and render
        # (2026-08-08 review: the switch must silence every surface).
        return counts

    landed_keys: set = set()
    try:
        import json as _json
        from runs import current_run_dir
        _rd = current_run_dir()
        if _rd is not None:
            _meta = _json.loads(
                (_rd / "metadata.json").read_text(encoding="utf-8"))
            _prior = _meta.get("world_facts_landed_keys")
            if isinstance(_prior, list):
                landed_keys = {str(k) for k in _prior}
    except Exception:
        pass  # unreadable metadata → land (fail-open; stamp below re-arms)

    def _strength(f: "WorldFact"):
        # Re-observed-this-run first, then earliest-seen.
        return (-f.hits, f.first_step)

    def _landable(kind_rows: List["WorldFact"]) -> List["WorldFact"]:
        # Allowlist, not denylist (review: a future producer's unknown
        # source label must not be silently landable): only
        # execution-discovered facts land. Planner-declared facts came FROM
        # injected context — landing them would launder what the stores
        # already say into fresh confidence bumps/confirmations (SOURCE_*
        # note). Already-landed keys skip (idempotency, above).
        return [f for f in kind_rows
                if f.source == SOURCE_STEP
                and WorldFactLedger._key(f.kind, f.fact) not in landed_keys]

    sources = [f"loop:{loop_id}"] if loop_id else []
    newly_landed: List[str] = []

    for f in sorted(_landable(ledger.anecdotal()),
                    key=_strength)[:MAX_LANDED_ANECDOTAL]:
        try:
            from knowledge_bridge import upsert_knowledge_from_candidate
            from knowledge_web import load_knowledge_nodes
            desc = f.fact
            if f.evidence:
                desc += f" Evidence: {f.evidence}"
            desc += (f" (declared during run {loop_id or '?'}, "
                     f"step {f.first_step}, {f.hits}×)")
            # Mint-time grounding (slice-2a review r1, 2026-08-16: this
            # was a missed CREATE sibling — world facts are declared
            # method/observation claims, exactly the shape R1-4 exists to
            # stamp, and loop_id was already in hand). Fail-open.
            _grounding = None
            if loop_id:
                try:
                    from mint_grounding import ground_lessons_for_run
                    _grounding = ground_lessons_for_run([desc], loop_id)[0]
                except Exception as exc:
                    log.debug("world_facts: mint grounding unavailable: %s",
                              exc)
            _node, _is_new = upsert_knowledge_from_candidate(
                title=f.fact[:100],
                description=desc,
                node_type="insight",
                domain=project or "orchestration",
                sources=sources,
                existing_nodes=load_knowledge_nodes(status=None),
                grounding=_grounding,
            )
            counts["anecdotal"] += 1
            newly_landed.append(WorldFactLedger._key(f.kind, f.fact))
        except Exception:
            continue

    for f in sorted(_landable(ledger.hypotheses()),
                    key=_strength)[:MAX_LANDED_HYPOTHESES]:
        try:
            from knowledge_lens import observe_pattern
            observe_pattern(
                f.fact, project or "",
                source_lesson_id=f"world_fact:{loop_id}" if loop_id else "")
            counts["hypotheses"] += 1
            newly_landed.append(WorldFactLedger._key(f.kind, f.fact))
        except Exception:
            continue

    if newly_landed:
        try:
            from runs import stamp_run_metadata
            stamp_run_metadata({"world_facts_landed_keys":
                                sorted(landed_keys | set(newly_landed))})
        except Exception:
            pass
    if counts["anecdotal"] or counts["hypotheses"]:
        try:
            from captains_log import log_event, WORLD_FACTS_LANDED
            log_event(
                event_type=WORLD_FACTS_LANDED,
                subject=loop_id or "unknown-loop",
                summary=(f"Landed {counts['anecdotal']} anecdotal fact(s) as "
                         f"candidate nodes, {counts['hypotheses']} "
                         f"hypothesis(es) into observe_pattern"),
                context={"loop_id": loop_id, "project": project, **counts},
            )
        except Exception:
            pass
    return counts


def clean_declared(raw: Any) -> List[Dict[str, str]]:
    """Validate one step's declared world_facts payload.

    Returns up to MAX_FACTS_PER_STEP validated {kind, fact, evidence} dicts.
    Cap applied AFTER validation so malformed entries don't consume the
    budget. Unknown kinds are dropped (the declarer says which kind it is —
    no reclassification, no guessing). Never raises.
    """
    cleaned: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return cleaned
    for entry in raw:
        if len(cleaned) >= MAX_FACTS_PER_STEP:
            break
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        fact = str(entry.get("fact", "")).strip()[:MAX_FACT_CHARS]
        evidence = str(entry.get("evidence", "")).strip()[:MAX_EVIDENCE_CHARS]
        if kind not in KINDS or not fact:
            continue
        # Injection scan (2026-08-08 review): a declared fact re-enters
        # later prompts verbatim under "treat as known" framing — stronger
        # authority than step prose gets. A flagged declaration is dropped,
        # not sanitized (fail-closed, like synthesize_skill), and never
        # consumes the cap. observe()/from_list() re-scan at ledger ingress
        # for producers that bypass this site (round-2 review).
        if not _scan_clean(f"{fact}\n{evidence}"):
            continue
        cleaned.append({"kind": kind, "fact": fact, "evidence": evidence})
    return cleaned


def world_facts_enabled() -> bool:
    """Single kill switch for capture (render of an empty ledger is already
    a no-op, so gating capture gates everything)."""
    try:
        from config import get as _cfg_get
        return bool(_cfg_get("world_facts.enabled", True))
    except Exception:
        return True
