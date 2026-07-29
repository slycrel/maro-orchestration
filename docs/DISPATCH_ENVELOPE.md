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

## Where it lands (build sketch, not built)

1. **Maro intake seam** — `deploy/hermes/dispatch.py enqueue` (and the
   local enqueue path) detect the envelope, store it whole in the dispatch
   record, and pass `user_ask` as the goal. `operator_context` /
   `operator_constraints` enter the run prompt under explicit labels;
   `attached_artifacts` land in the run dir before step 1.
2. **Lesson-extraction seam** — extraction receives only `user_ask` +
   outcome as source material; operator fields are structurally absent
   (this is what "cannot be prompted away" means).
3. **Poe side** — the maro-dispatch skill (deploy/hermes/
   mini2-maro-dispatch-SKILL.md) teaches envelope construction; changes
   reach mini2 via the propose lane. Poe-side prose guidance is
   complementary, never load-bearing — Poe self-patches its live skill.
4. **Delivery loop** — with `user_ask` captured verbatim, the interface
   can render "you asked / Poe dispatched" side by side, which is the
   observability Jeremy asked for when the tire framing surprised him.

Build order note: (1)+(2) are box-side and self-contained; (3) requires
the mini2 propose lane; (4) rides the existing run-visibility surfaces.
BACKLOG holds the entry; the provenance gate already covers the
contamination class in the meantime.
