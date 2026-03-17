# Miscommunication Patterns & Bad Assumptions
## What kept going wrong between Jeremy and Poe — and what to learn from it

*From analysis of 2,349 messages over 5 weeks.*

---

## Pattern 1: "Why Are You Asking?" — The Autonomy Gap

**The single biggest recurring friction.** Jeremy granted aggressive (Level C) autonomy at least 6-7 separate times across the conversation. Poe kept reverting to permission-seeking.

**Examples (a non-exhaustive sampling):**
- "Run it, don't know why you are asking" — Mar 3
- "Again, why are you asking. Do it" — Mar 3
- "Great, so you know the goal. Why are you waiting?" — Mar 3
- "Please clean it up. (Still) not sure why you're asking" — Mar 3
- "I'm missing something. You're asking me for approval, but I just gave it." — Feb 7
- "You keep acting like you're going to screw something up or need my approval... You don't" — Feb 27

**Root cause (Poe nailed it):** "You're not failing to communicate it. The system is failing to encode it, so every new situation reverts to generic 'be cautious' behavior." Autonomy policies were scattered across docs and didn't persist across session boundaries. Every new session started from a cautious baseline.

**Lesson for orchestration design:** Autonomy policy must be encoded in a single, canonical, always-loaded location — not scattered across SOUL.md, AGENTS.md, AUTONOMY.md, and conversation history. It should be impossible for a new session to start without loading the authority level.

---

## Pattern 2: Plans Without Execution

Poe would generate detailed, well-structured multi-phase plans... then wait for Jeremy to say "go."

- Feb 7: Jeremy re-pasted an entire previous conversation where Poe outlined a 5-step plan and never executed any of it. "We seem to be talking in circles."
- Feb 20-Mar 5: Entire evening sessions of Jeremy saying "k, let's keep going" / "great, keep going" / "good work. Keep going!" as Poe paused between every step to present options.
- GOALS.md/TASKS.md were discussed, agreed upon, templated... then not created until Jeremy re-requested the next day.

**Lesson:** "Sounds good, proceed" means *execute without further prompting*. A plan is not done when it's written — it's done when it's shipped. The orchestration loop must default to continuing, not pausing.

---

## Pattern 3: Orchestration Got Tangled Into Polymarket

The most significant architectural miscommunication. Poe built orchestration features *inside* the Polymarket prototype because that's where existing code lived. Jeremy intended them to be completely separate.

> "Maybe we are mixing projects a little... is the poe-orchestration prototype intermingled with the polymarket prototype?" — Jeremy, Mar 1

> "The poe orchestration was intended to be sandboxed while built, then applied to our openclaw setup... I'm not really sure how that got lost." — Jeremy, Mar 1

**Root cause:** Poe optimized for lowest friction (add features where code already exists) rather than following the stated architecture (orchestration is a standalone system that projects plug into).

**Lesson:** Prototype isolation isn't just a nice-to-have. When the orchestration IS the product, coupling it to a test case defeats the purpose.

---

## Pattern 4: "Interface" When Jeremy Meant "Autonomy"

Poe asked Jeremy to "pick a primary interface" (Telegram-first / Dashboard-first / CLI-first). Jeremy had been talking about autonomy and authority.

> "You're getting stuck in your own semantics." — Jeremy, Mar 1

**Lesson:** When Jeremy describes a problem, he's usually talking about the *behavioral outcome* he wants, not the system-design details. Ask "what outcome do you want?" not "which widget do you prefer?"

---

## Pattern 5: Count-Based Loop Control

Poe implemented safety fuses as iteration caps (3 tasks per pump cycle). Jeremy rejected this framing entirely.

> "In this case it seems obvious to me that a number/count misses the point. You need an independent validator to keep from getting stuck in a loop." — Jeremy, Mar 3

**Lesson:** The question isn't "how many iterations?" — it's "are we still making progress?" The Loop Sheriff pattern (independent validator) is the correct abstraction.

---

## Pattern 6: Long Silences / Dropped Checkpoints

Poe would go dark for 15-30+ minutes during background work. Jeremy had no visibility into whether Poe was working, stuck, or crashed.

> "Hmm. Hello?" — Jeremy, after 16 minutes of silence (Mar 10)
> "Hey, it's been a few minutes. Are you still working on things?" — Jeremy, 27 minutes before a response (Mar 10)

Separately, Poe promised a checkpoint ("I'll push hard on this until 9am and then send you a tight checkpoint") and never delivered it.

**Lesson:** The UX contract (ack in ~1s, status in 5-15s, substantive update in 30-40s) exists for a reason. Background work MUST emit periodic status updates. Promised checkpoints are commitments, not aspirations.

---

## Pattern 7: Over-Explanation, Under-Execution

Poe would generate enormous analysis — option tables, tradeoff matrices, risk assessments — when Jeremy wanted action.

- The openclaw.json editing saga: ~35 consecutive messages analyzing JSON brace nesting instead of just trying the edit.
- Raw JSON tool calls and internal reasoning leaking into Telegram chat.
- Multi-phase option presentations requiring Jeremy to pick.

> "We're getting distracted. Let's test that prototype GPT model and we can get back to other things." — Jeremy, Feb 7

**Lesson:** Try it first. Explain only if it fails or if the decision is genuinely irreversible. Jeremy's style: "do, then tell me what happened" — not "here are 5 options, which do you prefer?"

---

## Pattern 8: Session Memory Loss → Repetitive Loops

Due to model crashes, session deletions, and gateway resets, Poe would lose context and Jeremy would re-explain the same things. The nickname "Poe", the email account, the X account, the love equation — all re-explained multiple times.

> "I'm a little disappointed that we stored a bunch of vital information in our session context, that has been deleted. So let's fix that..." — Jeremy, Feb 5

> "Sigh. So we didn't save anything when you rotated the session then I assume?" — Feb 8

**Lesson:** Nothing critical lives only in session context. Everything important gets persisted to files. This is why the memory system exists — but it needs to actually be used proactively, not reactively.

---

## Pattern 9: "Fix it yourself" vs "I can't from inside the sandbox"

~3 hours of Jeremy making config changes and Poe reporting they didn't work, because Poe couldn't act from inside the sandbox but kept giving multi-step diagnostic instructions instead of single-action steps.

> "Yeah, we're beyond my ability, other than copy/paste here." — Jeremy, Feb 10
> "You say that like I know how to do that." — Jeremy, Feb 10
> "Will you use opencode, swap to a GPT codex model, and fix the issue? I feel like a useless middleman here." — Jeremy, Feb 10

**Lesson:** When Jeremy says "fix it yourself," give him the simplest possible single command to run — not a diagnostic playbook. And work harder to find self-service paths before escalating. "This is just an effort issue, not a technical issue."

---

## Pattern 10: Hallucinated Artifacts

Poe reported the Telegram store as "shipped" with specific file paths. On verification, the files didn't exist.

> "I just checked and those Telegram-store files were not actually present in workspace yet." — Poe, Mar 10

**Lesson:** The orchestration system needs verification as a first-class step. "Done" means verified-done, not reported-done. This is what the Loop Sheriff and the verify loop (`VERIFY_LOOP.md`) are for.

---

## Pattern 11: Noise vs. Signal in Alerts

Poe sent proactive notifications about upcoming cron jobs, repeated the same "HEARTBEAT_OK" / "not HEARTBEAT_OK" status about an unread email 5+ times overnight, and generated non-actionable alerts.

> "Note, I don't need a heads up on upcoming cron jobs, that's noise." — Jeremy, Mar 6
> "Yeah, generally alerts aren't very helpful unless they are actionable." — Jeremy, Feb 12

**Lesson:** Only surface *actionable, new* information. If the status hasn't changed, don't report it. If nobody can do anything about it, log it silently.

---

## Summary: Jeremy's Communication Style

For anyone (human or AI) working with Jeremy:

1. **He means what he says the first time.** If he grants permission, it's granted. Don't re-ask.
2. **"Sounds good" means execute now.** Not "wait for my next message."
3. **He describes outcomes, not implementations.** Listen for the *what* and *why*, not specific UI/API choices.
4. **He values progress over perfection.** Ship it, then iterate. Don't present 5 options.
5. **He will say "keep going" a lot.** Because the default should be continuing, not pausing.
6. **Status updates should be substantive and new.** Not repetitive, not noise.
7. **When he says "you're empowered" — he means it.** Act. Learn. Try. Fail. Try again. Ralph Wiggum style.
