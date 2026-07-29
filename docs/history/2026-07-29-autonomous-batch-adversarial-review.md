---
status: record
---

# Autonomous-batch adversarial review (2026-07-29)

**Scope.** Combined post-land review of the 2026-07-29 autonomous batch
(Jeremy's "implement the work items available without me"): dispatch-envelope
box-side intake (trio item A), SSRF resolve-then-pin (E), errand re-measure
(D), wiring-claims docket (C), HOUSE_STYLE.md v1 (F), NODE_CANDIDATE
invisibility pin (H), plus the five earlier chunks (always-wrap, now_escalated,
stage-3 declare-blocked, ClassifyResult, phase-61 pins). Diff reviewed:
`5c8dce7..44a1e8a`.

**Mechanics.** 3 Codex lenses (skeptic / architect / minimalist) per the
adversarial-review skill — opposite model family, `codex exec`, sequential
with `timeout 600`. Ops lesson recorded: 144KB prompts exceed the 128KB
per-argument limit (`MAX_ARG_STRLEN`) — pass via stdin
(`codex exec ... - < prompt.md`); exit 126 "Argument list too long"
otherwise. Every finding verified against the tree before any fix
(verify-before-fix rule; historical reviewer-hallucination band 30–78%).
**This round: 0 fabricated code claims** — every cited line existed as
described; the two rejections are threat-model/already-tracked judgments,
not fabrications. Codex's post-provenance-gate accuracy streak continues.

## Verdict: CONTESTED — 3 verified HIGHs, all fixed same session

## Findings

| # | Sev | Finding | Verdict | Disposition |
|---|-----|---------|---------|-------------|
| F1 | HIGH | `build_adapter(api_key=...)` returned **bare** adapters (llm.py explicit-key branch), bypassing the FailoverAdapter record/meter/cap-warning seam — the one escape left after always-wrap | CONFIRMED | **Fixed**: both returns wrapped in `FailoverAdapter([...])`; tests inverted to assert the wrap |
| F9 | HIGH | `_pinned_opener()` built without `ProxyHandler({})` — ambient `http_proxy`/`https_proxy` env would route via the proxy, pinning/vetting the PROXY host while the target resolves unvetted (SSRF pin bypass) | CONFIRMED | **Fixed**: `ProxyHandler({})` first in `build_opener`; regression test proves an env-reading handler appears in the regressed shape and not in the fixed one |
| F2 | HIGH | Truncated/corrupted **declared** envelope (names `maro-dispatch/v1`, fails `json.loads`) silently fell to the prose lane — a mangled goal would execute | ACCEPTED | **Fixed**: JSONDecodeError branch raises `EnvelopeError` when the payload names the contract version; prose without the version string stays on the fallback lane (+2 tests) |
| F4 | MED | `thinkback.py` called `build_adapter("cheap")` / `build_adapter(args.model)` — positional arg is **backend**, not model tier → silent degrade to dry-run adapter (3/3 lenses) | CONFIRMED | **Fixed**: both sites use `model=` keyword (`MODEL_CHEAP` / `args.model`); grep sweep confirms no other positional-tier sites remain |
| F6 | MED | `enqueue_goal` skipped envelope validation — a malformed typed payload would sit queued for hours until `handle_task` hit it, far from its sender | ACCEPTED | **Fixed**: boundary `parse_dispatch_payload(reason or goal)` at the top, same contract as `dispatch.py cmd_enqueue` (+2 tests, nothing-queued asserted) |
| F3 | MED | Operator context can still reach lessons via **echo**: model reads the advisory block, echoes content into output/`result_summary`, extraction legitimately reads those; provenance gate catches authority PHRASING, not clean echoes | REAL, residual | **Documented, not fixed** — DISPATCH_ENVELOPE.md "Residual risk" section. Every context channel shares this path; filtering model output against context strings is a censorship seam with worse failure modes. Mitigation stack: advisory framing + provenance gate + lesson decay |
| F5 | LOW | No construction-time test on the pinned opener's handler set | ACCEPTED (light) | **Added**: `TestPinnedOpenerConstruction` (env-proxy absence + pinned/redirect handlers installed) |
| F10 | LOW | V3 structural scan too narrow (`promote`×`node` in 2 modules) — an `activate_candidate` in knowledge.py would slip past | ACCEPTED in part | **Broadened**: verbs (promote, activate) × nouns (node, candidate) × modules (knowledge, knowledge_bridge, knowledge_web); verified zero false positives ("active" ≠ "activate") |
| F7 | MED→ | Proxy fetch tiers (Jina/CF) get literal-gate checks only — no resolver vetting | REJECTED | The proxy tiers egress from the **provider's** network; a name resolving privately there cannot reach our LAN. The asymmetry IS the threat model. One-sentence policy note added to `is_safe_public_url` docstring so the next reviewer doesn't re-find it |
| F8 | LOW | Dispatch artifacts don't land in the run dir | REJECTED | Already tracked — the artifacts-travel rider is an explicit BACKLOG item; re-reporting tracked work isn't a finding |

## What went well

- The extraction-exclusion-by-construction design (goal-only
  `reflect_and_record` interface + pin) drew zero challenges from any lens —
  the seam held under adversarial reading.
- The envelope's fail-loud/fall-back split (opt-in via version key) was
  called out as the right shape by two lenses; F2 was a gap in it, not an
  argument against it.
- 0 hallucinated code claims across ~10 findings, three lenses.

## Lead judgment

Accepted 6, rejected 2, documented 1, split 1 (F10 in part). The three
HIGHs were all real and all of the same species: **a correct mechanism with
one unwired entry point** (always-wrap missing the api_key branch; the SSRF
pin missing the ambient-proxy path; the envelope contract missing the
truncation case). That's the pattern the wiring docket already named —
verification effort should concentrate at seams where a new invariant meets
pre-existing entry points. F3 is the honest cost of context injection at
all; recording it beats pretending a filter would fix it.

Fixes landed same session with tests; affected files green by exit code
before land.
