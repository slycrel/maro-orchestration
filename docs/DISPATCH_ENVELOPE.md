---
status: dormant-design
---

# Typed Dispatch Envelope — spec (2026-07-29)

## The problem this answers

When one agent dispatches work to Maro (Poe/Hermes over the SSH lane, or
any future orchestrator), the goal arrives as one undifferentiated string.
Everything in it — the user's actual ask, the operator's helpful framing,
recovered reference material, and behavior-steering scaffolding ("Do NOT
escalate or stop merely because...") — carries the same authority. The
2026-07-27 incident showed the cost: the lesson extractor generalized a
dispatch prompt's anti-escalation scaffolding into lesson `db37d525`, and
recall injected it into the next, unrelated run. Instruction text rewrote
persistent state.

The mint-side fix (the provenance gate, `src/lesson_provenance.py`) is
live and load-bearing. This spec is the channel-separation half: give the
dispatch boundary types, so authority is structural instead of inferred.
Prompt-injection security is the model (Jeremy's framing): the dispatch
prompt is untrusted input authored by another agent; the defenses are
channel separation (this envelope) plus least-privilege interpretation
(goal text cannot become lessons — already shipped).

## UX decree (Jeremy, 2026-07-28)

The envelope is **machine-to-machine only**. Never force a human to fill
it in — a person typing to Poe or to Maro keeps typing plain language.
"I kinda don't love the UX forcing the envelope and this seems to prove
we're going to want the direct ask and the prompt separately." The
operator (Poe, or Maro's own interface layer) constructs the envelope; the
user's words ride inside it verbatim, never rewritten into it.

## The envelope

A dispatch payload MAY be a JSON object with this shape (any prose string
remains a valid dispatch — see "untyped fallback"):

```json
{
  "envelope": "maro-dispatch/v1",
  "user_ask": "Please use maro to self-diagnose this gist, see if it can help us with our orchestration goals",
  "operator_context": "Poe recovered the gist contents below because the URL 404s from this box. Treat as reference material.",
  "attached_artifacts": [
    {"name": "karpathy-llm-wiki-gist.md", "content": "...", "source_url": "https://gist.github.com/...", "recovered_by": "poe/xurl 2026-07-27T04:58Z"}
  ],
  "operator_constraints": ["Do not fetch external URLs; use the attached copy."]
}
```

Field semantics — this is where the authority separation lives:

| Field | Authority | Persistence rules |
|---|---|---|
| `user_ask` | The goal. Verbatim from the human, never operator-rewritten. | Closure judges against THIS. Lessons may generalize from its outcome. Delivery loop can show "your ask vs. dispatched task". |
| `operator_context` | Advisory framing from the dispatching agent. | Labeled as operator-authored in the run prompt. Never learnable — the provenance gate treats it as instruction text. |
| `attached_artifacts` | Reference material with provenance. | Written to the run's `fetch-raw/` with the `source_url`/`recovered_by` stamps (artifacts-over-streams decree: artifacts travel with dispatch, context is a view over them). Never inlined as bare goal text. |
| `operator_constraints` | Scoped-to-run behavior bounds. | Applied for THIS run only. Structurally barred from lesson extraction; never injected into any other run. This is the typed home for what the tire dispatch smuggled into goal prose. |

## Untyped fallback

A prose-only dispatch keeps working, treated conservatively: the whole
string is the goal, and the provenance gate is the only defense against
scaffolding becoming lessons. Nothing breaks; typed dispatches just get
better separation. Detection is cheap and unambiguous — payload parses as
a JSON object with `"envelope": "maro-dispatch/v1"`.

## Where it lands (1+2 SHIPPED 2026-07-29; 3+4 not built)

1. **Maro intake seam — SHIPPED** (`src/dispatch_envelope.py`):
   `dispatch.py cmd_enqueue` validates at the boundary (malformed
   declared envelope → exit 2, nothing queued; dispatch record displays
   `user_ask` + envelope meta while the queue task carries the raw
   payload) and `handle_queue.handle_task` re-parses it — recall guard,
   navigator, `handle()`, closure, and lessons all key on `user_ask`.
   `operator_context`/`operator_constraints` enter the run prompt as one
   labeled advisory block via the new `operator_context` param on
   `handle()` (rides `ancestry_context_extra`, AGENDA lane only — same
   contract as `prior_context`). `attached_artifacts` land under
   `output/dispatch-artifacts/<job_id>/` with sha256+source provenance
   sidecars (run-dir landing is the artifacts-travel rider).
2. **Lesson-extraction seam — SHIPPED** by construction: extraction
   (`reflect_and_record`) receives only `user_ask` + outcome; operator
   fields ride a channel that never reaches it. Pinned by an interface
   test (test_dispatch_envelope.py::TestExtractionSeamPin) — this is
   what "cannot be prompted away" means.
3. **Poe side — SHIPPED 2026-08-01** — the maro-dispatch skill
   (deploy/hermes/mini2-maro-dispatch-SKILL.md v0.3.0) teaches envelope
   construction (shape, field authority semantics, the write-file +
   `dispatch $(cat ...)` invocation that survives SSH quoting, refuse-
   don't-fallback on malformed) and delivery-block rendering (lead with
   `you_asked` answered; `verbatim: false` → "as recorded", never quoted
   as exact). Poe-side prose guidance is complementary, never
   load-bearing — Poe self-patches its live skill.
4. **Delivery loop** — with `user_ask` captured verbatim, the interface
   can render "you asked / Poe dispatched" side by side, which is the
   observability Jeremy asked for when the tire framing surprised him.

Build order note: (1)+(2) shipped box-side 2026-07-29; (3) SHIPPED
2026-08-01 (repo copy is source of truth; install = the scp in the
skill header); artifacts-travel rider SHIPPED 2026-08-01 —
`runs.create_run_dir` copies the dispatch's stored attachments (with
provenance sidecars) into `<run_dir>/fetch-raw/dispatch/` when origin
carries the envelope marker (fail-soft: the operator block's
dispatch-side paths still work on the subprocess lane). Named residual:
a CONTAINERIZED worker still can't read either copy — the mount map
hard-excludes the workspace, and the run dir isn't mounted; if the
container lane flips on for dispatched runs, attachments need a landing
under the mounted cwd (BACKLOG). (4) box-side half SHIPPED 2026-07-29 —
`dispatch.py cmd_result` emits a `delivery` block (`you_asked` verbatim
+ `verbatim` flag + `dispatched_with` envelope meta) for envelope
dispatches only, and the dispatch record keeps `user_ask` untruncated
past the 500-char display copy. `verbatim: false` marks the
compatibility fallback (an envelope rec minted before `user_ask` was
stored falls back to the lossy display goal — the renderer can tell). The box never phrases the human message (machine-to-
machine decree); Poe's renderer consumes the block. BACKLOG holds the
remaining halves (Poe skill, artifacts-travel rider).

## Residual risk — echo-through-output (known gap, 2026-07-29 review)

The extraction seam guarantees operator text never enters
`reflect_and_record` as *input*. It does NOT guarantee operator text
never reaches a lesson at all: the model reads the labeled advisory
block, may echo its content into step output or `result_summary`, and
extraction legitimately reads those. The provenance gate
(`lesson_provenance.py`) quarantines prompt-authority PHRASING; a clean
echo ("the operator noted X") carries no such phrasing and passes. This
is accepted, not fixed: every context channel (prior_context, recall
lessons, playbook) shares the same echo path, and closing it would mean
filtering model output against context strings — a censorship seam with
its own failure modes. The mitigation stack is: labeled-advisory framing
(the model knows it's not the ask), provenance gate (catches authority
transfer), and lesson decay (echoes that don't help die in ~7 days).
