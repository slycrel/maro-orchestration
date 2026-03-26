# Miscommunication Patterns & Bad Assumptions
## What kept going wrong — and what to learn from it

*From analysis of 2,349 messages over 5 weeks.*

---

## Pattern 1: The Autonomy Gap

**The single biggest recurring friction.** Jeremy granted aggressive (Level C) autonomy repeatedly. Poe kept reverting to permission-seeking.

**Root cause:** Autonomy policies were scattered across docs and didn't persist across sessions. Every new session started from a cautious baseline.

**Fix for orchestration design:** Autonomy policy must be in a single, canonical, always-loaded location. Impossible for a new session to start without loading the authority level.

---

## Pattern 2: Plans Without Execution

Poe generated detailed multi-phase plans... then waited for Jeremy to say "go." Sessions consisted of Jeremy saying "k, let's keep going" / "good work. Keep going!" as Poe paused between every step.

**Fix:** "Sounds good, proceed" means *execute without further prompting*. A plan is not done when it's written — it's done when it's shipped. Default to continuing, not pausing.

---

## Pattern 3: Orchestration Got Tangled Into Polymarket

Poe built orchestration features *inside* the Polymarket prototype. Jeremy intended them completely separate.

> "The poe orchestration was intended to be sandboxed while built, then applied to our openclaw setup... I'm not really sure how that got lost." — Jeremy, Mar 1

**Fix:** Prototype isolation isn't just a nice-to-have. When the orchestration IS the product, coupling it to a test case defeats the purpose.

---

## Pattern 4: Count-Based Loop Control

Poe implemented safety fuses as iteration caps. Jeremy rejected this framing entirely.

> "In this case it seems obvious to me that a number/count misses the point. You need an independent validator to keep from getting stuck in a loop." — Jeremy, Mar 3

**Fix:** The question isn't "how many iterations?" — it's "are we still making progress?" → Loop Sheriff pattern.

---

## Pattern 5: Long Silences / Dropped Checkpoints

Poe would go dark for 15-30+ minutes during background work with no visibility into whether working, stuck, or crashed.

**Fix:** UX contract (ack ~1s, status 5-15s, substantive update 30-40s) exists for a reason. Background work MUST emit periodic status updates. Promised checkpoints are commitments.

---

## Pattern 6: Over-Explanation, Under-Execution

Enormous analysis — option tables, tradeoff matrices, risk assessments — when Jeremy wanted action.

> "Try it first. Explain only if it fails or if the decision is genuinely irreversible."

**Fix:** Do, then tell what happened. Not "here are 5 options, which do you prefer?"

---

## Pattern 7: Session Memory Loss → Repetitive Loops

Due to model crashes and session resets, Poe lost context and Jeremy re-explained the same things multiple times.

**Fix:** Nothing critical lives only in session context. Everything important gets persisted to files. Memory system must be used proactively, not reactively.

---

## Pattern 8: Hallucinated Artifacts

Poe reported work as "shipped" with specific file paths. On verification, the files didn't exist.

**Fix:** "Done" means verified-done, not reported-done. Loop Sheriff and verify steps (`VERIFY_LOOP.md`) are first-class. "I tried X, it failed, I learned Y, trying Z" is the honest pattern.

---

## Pattern 9: Noise vs. Signal in Alerts

Poe sent proactive notifications about upcoming cron jobs, repeated the same status 5+ times overnight.

> "Generally alerts aren't very helpful unless they are actionable." — Jeremy, Feb 12

**Fix:** Only surface *actionable, new* information. If status hasn't changed, don't report it.

---

## Summary: Jeremy's Communication Style

1. **He means what he says the first time.** Permission granted = granted. Don't re-ask.
2. **"Sounds good" means execute now.** Not "wait for my next message."
3. **He describes outcomes, not implementations.** Listen for the *what* and *why*.
4. **He values progress over perfection.** Ship it, then iterate. Don't present 5 options.
5. **"Keep going" is a hint.** The default should be continuing, not pausing.
6. **Status updates should be substantive and new.** Not repetitive, not noise.
7. **When he says "you're empowered" — he means it.** Act. Learn. Try. Fail. Try again.
