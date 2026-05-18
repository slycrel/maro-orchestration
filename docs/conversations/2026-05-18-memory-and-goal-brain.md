# Conversation: Memory, gbrain, and the Goal-Brain Re-frame (2026-05-18)

**Participants:** Jeremy, Claude (Opus 4.7, 1M context), Poe-codex (via Telegram screenshots, transcribed)
**Branch:** `arch/thread-navigator`
**Synthesis doc:** *(none yet — this is a capture-only doc, picked up later)*

Literal capture of a planning-style conversation that ranged from a third-party Python agent framework (`agentlane`) into memory systems (gbrain), and from there back into the stalled `arch/thread-navigator` rethink. Captured at Jeremy's request *before* drawing implementation conclusions, because he explicitly wants to verify the understanding before acting:

> I think action is the next step, but I also want to make sure I'm understanding everything as we go; I made that mistake the last go around and we ended up in the implementation place different than what I thought we were going for; the fanouts of work killed it I think, we'd follow one thread of many and never go back and revisit.

The fan-outs lesson is itself part of why this file exists. See [Turn 11](#turn-11--jeremy-asks-for-capture) at the end.

The conversation also features a *cross-LLM agreement check*: Jeremy ran a parallel conversation with Poe-codex about the same `arch/thread-navigator` rethink and shared screenshots. Poe-codex's read and Claude's read converged on the same broad shape, with Poe-codex contributing a sharper ordering (goal-brain before navigator schema) that this Claude session got wrong on the first pass and corrected in [Turn 11](#turn-11--claudes-response-poes-ordering-is-sharper-mage-correspondence-has-a-path).

---

## Top-level summary (in-conversation, not synthesized further)

What the conversation surfaced, in order of appearance:

1. **agentlane is interesting but not the slice.** A new Python "runtime-first" agent framework (addressed messaging, durable routing, distributed workers, tracing). 6 stars, single author, v0.8.0. Concept rhymes with `openclaw-orchestration`'s Agent Runtime layer but isn't the slice to adopt; design study at most.
2. **The right question is "which layer am I shopping for, and what hurts."** Counter to the trap of evaluating frameworks because they rhyme with the bigger vision.
3. **Memory's actual unsolved problem is lifecycle, not retrieval.** Retrieval (vector / graph / hybrid / rerank) is engineering-solved. Write / dedupe / update / decay / conflict-resolve / promote / forget is the open problem — and it *is* the knowledge crystallization pillar.
4. **gbrain (Garry Tan, 17K stars, TS/Bun, 45K-page production deployment) bakes the mindset/behavior split into its data model.** Append-only timeline = evidence layer; compiled truth = current beliefs the agent operates from; the `dream` cycle is the scheduled crystallization that converts one to the other. Don't adopt gbrain wholesale (TS/Bun, coupled to Garry's recipes), but the *pattern* is highly portable.
5. **Outward Mindset analogy maps to a real structural distinction.** Mindset (operates *from*) vs behavior (looks *up*). Navigator = mindset layer; work LLM = behavior layer. Jeremy's existing `arch/thread-navigator` design already encodes this split — he hadn't named it that way.
6. **The `arch/thread-navigator` rethink is in better shape than "stalled" suggests.** 9 open questions, but they're not co-equal: Claude initially flagged item #1 (navigator decision schema) as the load-bearing artifact. Poe-codex corrected the ordering: the **goal-brain / thread doc** is upstream of the navigator schema. Claude agreed.
7. **The keeper conceptual frame, from Poe-codex:** *"we're not escaping LLM trust, we're redistributing it, so the human-readable goal-brain becomes the actual non-LLM anchor."* This is the load-bearing concept under the entire rethink — the human-readable, diffable, editable goal-brain is where trust lives, and it's what protects against intent-drift across "five turns and three subthreads."
8. **Sequencing for the unstuck move (Poe-codex's ordering, Claude's confirmation):**
   1. Define the goal-brain artifact concretely (fields, human-steerable vs system-maintained).
   2. Define `recall()` shape (what slice of goal-brain + correspondence the navigator sees per turn).
   3. Define the navigator's decision schema.
   4. Then write the navigator prompt.
9. **Self-improvement (open question #6) is the chicken-and-egg sticky point, but it's not blocking v1.** The move is to ship a static navigator and *instrument it from day one* to log every `(thread_state_snapshot, navigator_decision, outcome, downstream_signal)` tuple — same shape as gbrain's timeline. Static-now, crystallized-later.
10. **Mage correspondence memory has a concrete v1 path.** Every goal-brain carries typed links (sibling-thread, ancestor-thread, similar-shape-prior-attempt, persona-used, skill-used, lesson-cited); `recall()` is a bounded weighted graph walk from the active goal-brain outward; edge weights are the "sympathy function" tuned by example. Falls directly out of the thread-as-first-class-entity choice already made.
11. **The "AI scrum theater" risk (Poe-codex).** The client→org / agile-with-corrections framing is right *as a rhyme*, but the danger is reinventing agile with more tokens and worse taste. The actually-novel part is making that old shape operational with LLMs without turning it into beige process sludge — and the durable goal-brain is the mechanism.
12. **The hardest engineering problem is "preserving intent while decomposing work" (Poe-codex).** Many systems can spawn workers; fewer keep work pointed at the actual vision after five turns and three subthreads. The goal-brain injected at every navigator turn *is* that mechanism.
13. **Meta-observation Jeremy closed on:** the system being built is the system needed to build the system. Human memory good at some things, LLM memory good at others, tooling needed to cover the gaps over time — which is partly what gbrain is doing. Bootstrap loop is real and funny.

---

## Turn 1 — Jeremy opens with agentlane

> Hey, have a look at this and let me know if this is something that might be meaningful to try out. https://github.com/yasik/agentlane

---

## Turn 2 — Claude's first response: interesting concept, weak adoption signal

> Quick read: it's a Python 3.12+ "runtime-first" agent framework — addressed messaging, durable routing, pub/sub, distributed workers, tracing — pitched as primitives below the prompt+tools layer. The idea is real (most frameworks under-serve coordination/observability once you go past one agent), but the project itself is *very* early: 6 stars, 0 forks, single author, v0.8.0. That's a big adoption risk for anything you'd put behind production work, especially since your day-to-day stack is JVM, not Python.
>
> My take: the *concept* is more interesting than this specific implementation. If you want the "addressed messaging + durable runtime" pattern in something battle-tested, look at Temporal or Restate (language-agnostic, JVM-friendly) for the orchestration half, and Microsoft's Agent Framework or LangGraph for the agent half. If you're just curious about the design choices, it's small enough to read in an afternoon — but I wouldn't build anything load-bearing on it yet. Want me to do a deeper compare against one of those alternatives?

---

## Turn 3 — Jeremy reflects on the bigger vision

> Yeah, I've seen a couple of threads on X about this and it seems interesting on the surface, but I haven't taken a deep dive at all. I'm not 100% convinced it's worth it (yet?) but interested in a self-sustaining agentic harness that grows itself out over time. Seems like everything rhymes with my too-ambitious orchestration project nowadays and it's hard to pick a tiny slice to try out. (ref: https://github.com/slycrel/openclaw-orchestration/blob/main/VISION.md)

---

## Turn 4 — Claude reads VISION.md and maps the overlap

> Read your VISION — yeah, the rhyme is real and specific. agentlane's pitch maps almost exactly onto openclaw's "Agent Runtime (The Process)" layer: addressed messaging ≈ Poe→Director→workers routing, delivery guarantees ≈ your flock/jitter/retry concerns, agent identities/reuse ≈ a thin substrate under the Persona/Mask layer, tracing ≈ what Inspector eventually needs. What it deliberately *doesn't* touch is everything that makes openclaw openclaw: the Mask layer, validator-based loop control, knowledge crystallization, Level C authority, InterruptQueue, the Telegram gateway. So it's a candidate for *one floor of the building*, not the building.
>
> That actually makes the "tiny slice" question easier. Don't adopt agentlane. Pick the *one primitive* that's painful to roll yourself and study how they did it — almost certainly **addressed messaging with stable agent identities**, because that's the thing that gets gnarly fast when Director needs to fan out to N workers, route replies back, and survive a worker dying mid-task. Read their runtime/messaging module (it's small — 167 commits total), and either (a) lift the design into openclaw as your own minimal module, or (b) confirm the shape you already have is fine and move on. Either way you've spent an afternoon and learned the thing without taking a dependency on a 6-star repo.
>
> The trap with projects like yours is exactly what you're describing — everything rhymes, so every shiny thing looks like "the missing piece." Counter-move: before you evaluate the *next* framework, write down which openclaw layer you're shopping for and what specifically hurts in your current implementation of it. If you can't name the pain, the framework isn't the slice — finishing the layer you're on is.

---

## Turn 5 — Jeremy redirects to memory as the area to level up

> Yeah, I think that's the rub; I'm not shopping for anything directly right now, but everything reminds me of a much bigger vision that I want.  :)  I think if I'm picking an area to level up my understanding in it's going to be memory... conceptually I understand RAG and osme of graph theory, but I think there's a bunch of things to learn in that space, then sift through the good/bad/fugly of the options out there... I keep hoping someone will solve that, and garry tan might be the closest with his gbrain, but that's also pretty domain specific rather than a general solution.

---

## Turn 6 — Claude's memory frame: retrieval vs lifecycle

> Memory is the right area to nerd out on — it's where the field is least settled and where most "agent framework" projects punt. The frame that helped me think clearer about it: separate **retrieval** from **lifecycle**. Retrieval (RAG, BM25, vector, graph traversal, hybrid rerank) is mostly a solved engineering problem — pick pgvector or LanceDB, add a reranker, move on. **Lifecycle** — write, dedupe, update-in-place, resolve conflicts, decay, promote, forget — is the actually-unsolved problem, and it's exactly the same problem as your "knowledge crystallization" pillar. Most "memory libraries" are CRUD on top of a vector DB and don't touch lifecycle at all, which is why they feel underwhelming.
>
> Things worth a couple hours each:
> - **Graphiti** (Zep's OSS) — bi-temporal knowledge graph (valid-time vs transaction-time). Closest thing to a serious general solution; the temporal model alone is worth internalizing because it handles "this fact used to be true" without you having to invent it.
> - **Letta** (née MemGPT) — the core/archival/recall split is a useful mental model even if you don't adopt the runtime.
> - **Mem0** — popular, pragmatic, much simpler than the above; good for seeing how far you can get without a graph.
> - **A-MEM** paper — Zettelkasten-flavored agent memory, links/notes/evolution. Academic but it'll sharpen how you think about *links between memories*, which is the part graph-RAG hand-waves.
>
> One non-obvious datapoint: the MEMORY.md system Claude Code is using to talk to you *right now* — file-based, frontmatter-typed, `[[wiki-link]]` graph, no DB — is a surprisingly serious design. It's debuggable, diff-able, model-agnostic, and crystallization-friendly in a way that nothing built on a vector store is. Worth at least understanding before you reach for heavier machinery. On gbrain — I don't actually know it well enough to evaluate, so don't take a vote from me there.

---

## Turn 7 — Jeremy provides gbrain link and the Outward Mindset frame

> gbrain: https://github.com/garrytan/gbrain  And thanks, I'll check those out. I've looked at a couple of articles (about 6-8 weeks ago the thing was "you don't need a DB, use the filesystem" so somewhat familiar with a couple approaches there... I think mostly my understanding is a proper generalized approach, on top of all the existing tooling that's out there. In the same way that the outward mindset stuff says mindset changes > behavior changes, I think proper memory would leverage in that direction as well.

---

## Turn 8 — Claude reads gbrain, maps the Outward Mindset analogy onto compiled-truth/timeline

> That Outward Mindset analogy is sharper than it might sound and it actually maps to a real structural distinction in memory design. Most memory systems are pure behavior-layer: better retrieval, better chunking, better rerank — the agent still *queries* memory as an external thing. The mindset-layer equivalent is the agent's *current self-model* — what it operates *from*, not what it looks up. The leverage is making the agent *be* different by virtue of what it has learned, not just retrieve faster.
>
> Here's the interesting bit: gbrain is one of the very few systems that actually has that split baked into its data model. The "compiled truth + append-only timeline" pair is structurally a mindset/behavior separation — timeline is the evidence layer (what happened, append-only, queryable), compiled truth is the current belief state (what the agent thinks *now*, superseded by newer evidence). And the **dream cycle** (lint → backlinks → synthesize → extract → patterns → embed → orphans → purge) is literally a scheduled crystallization process that converts evidence into beliefs. That's your knowledge-crystallization pillar, with a working reference implementation and 45K pages of production use behind it. The naming overlap with your project (openclaw-orchestration ↔ Garry's OpenClaw) suggests you're already drawing from the same well.
>
> Don't adopt gbrain directly — it's TypeScript/Bun and pretty coupled to Garry's recipes (Twilio/Gmail/X/Calendar ingestion, his specific skills). But the *pattern* is highly portable and probably the single highest-leverage thing you could lift: two-tier store (timeline + compiled truth) on top of markdown files + sqlite/pgvector, with a scheduled "dream" job that does dedupe/synthesize/extract/decay. That's a one-weekend prototype that would teach you more about memory lifecycle than reading ten papers, and it slots cleanly under your existing Persona/Mask layer rather than competing with it.

---

## Turn 9 — Jeremy points to `arch/thread-navigator` as the actual stalled work

> Since we're here, and all roads lead to my orchestration project (heh), please have a look at the thread-navigator branch. I'm in the process of re-thinking the approach, though I've stalled out a bit... and apparently starting to consider it enough to start talking about it with LLMs again.  :)

---

## Turn 10 — Claude reads THREAD_ARCHITECTURE.md, MAINLINE_PLAN, ROADMAP, commit log, classifies the 9 open questions

> Read THREAD_ARCHITECTURE.md plus the surrounding docs and commit log (latest is "Thread Architecture sketch + literal conversation log," Apr 27). First, the headline: this rethink is in much better shape than "stalled" suggests. The core move — collapse NOW/AGENDA/one-shot/multi-step into a single **thread** primitive, and replace upfront classification with a cheap per-turn **navigator** whose decision wins over the work LLM's recommendation — is a genuinely clean inversion. "Navigator → work → navigator," idunno-escalates Haiku→Sonnet→Opus, Director demoted to a two-callsite escalation surface, one `recall()` composing all memory backends, per-turn persona selection. Structurally it's tight, it solves the actual gaps in the prior Poe→Director→Workers model, and it threads (heh) directly through the conversation we just had — the navigator *is* the mindset layer, the work LLM *is* the behavior layer. You already designed the Outward Mindset split into the architecture; you just hadn't named it that way.
>
> The reason it feels stalled, I think, is that you've got 9 open questions stacked and they look co-equal, but they're not. Exactly one is blocking: **#1, the navigator's prompt + decision schema.** You even flagged it as "most load-bearing artifact." Nothing else can be tested or falsified until that exists. #2 (fork rejoin) and #5 (when planning is warranted) are explicitly worked-example-shaped — you said so yourself ("probably needs a couple of worked examples before pinning") — which means they resolve *by doing*, not by deciding. #3, #4, #7, #8, #9 are details that defer cheaply. So the question stack is really one question with eight followers, and that's much less daunting than nine peers.
>
> The actual sticky one is #6 — navigator self-improvement / verify→learn closure — and I think that's the real stall, because you correctly noticed the chicken-and-egg: *"Designing the navigator without designing how it improves means we ship a smart-but-static navigator and re-discover the same gap."* But this is exactly the gbrain pattern from earlier in this conversation. You don't need to solve Stage 4→5 to ship v1. You need to **instrument v1 to produce the substrate Stage 4→5 will eventually consume**: log every `(thread_state_snapshot, navigator_decision, outcome, downstream_signal)` tuple to an append-only timeline from day one. That's your "evidence" layer. The hand-authored prompt+schema is your initial "compiled truth." The dream cycle (later) is what crystallizes patterns from timeline into prompt edits, then into skills, then into rules. You ship a static navigator on purpose, because the path from static→self-improving is data you don't have yet — and the only way to get it is to run the static one.
>
> So the unstuck move is small and concrete: draft the navigator's decision schema (the five moves: extend/execute/fork/collate/close, plus idunno, with the input state shape and return shape pinned down), then hand-write a prompt against it, then run it against 3–5 representative threads you've already done with Poe and see what it decides. That's a weekend, not a quarter. Everything else either falls out or earns the right to be designed later. The fact that your latest commit is *literally* a conversation log alongside the architecture sketch suggests you already know the unstuck move is "write the artifact, stop deciding around it" — you're one prompt away from being able to test something.
>
> One question worth sitting with before you draft the schema, since it shapes everything downstream: **does the navigator see the work LLM's full output, or only its recommendation + a structured summary?** Full output is more faithful but blows context fast across long threads. Summary-only puts you at the mercy of the work LLM's self-report, which is the exact authority inversion you're trying to avoid. There's probably a middle ("recommendation + structured signals + truncated tail") but the choice cascades into recall() shape, fork collation, and how much navigator context you can afford. Worth pinning before the schema, not after.

---

## Turn 11 — Jeremy shares Poe-codex's take (screenshots) and the Mage correspondence memory thread

> Yeah, I've just started the outward mindset, listened to the 4th chapter (or 3rd? I forget) on the way in to work today; arbinger 2 day training in a few days, that should be good... like so many of these concepts, I'm re-discovering, I couldn't name it because I didn't know it was named yet. :) Thus my desire to skill up on general memory science and conceptuals. That might be too deep though. This is hte second conversation that's ended up in this space today, and my poe-codex's take is attached, seems to agree with your take. [Image #1] [Image #2] I agree that the self-learning is the hard part, and that gets into memory in various stripes... and I still want my Mage correspondence memory idea, but no idea how that implements in practice, so need to at least think through that. Hard to get it all out at once, and hard to keep it all straight there's a ton in this space.

### Poe-codex (Telegram, transcribed from screenshot #1, 12:50 PM)

> Had a look in tmux. The Claude session is in a good stopping place, not a dropped thread.
>
> What I found:
> - It already spun a branch called `arch/thread-navigator`.
> - It wrote a synthesis doc plus a literal conversation log, then folded the old DISCUSS item into a top-level active design-space entry.
> - The last meaningful turn is exactly where implementation started getting ahead of design: the "goal-brain / thread document" became the load-bearing concept, and the pushback was getting sharper.
>
> My read:
> - Stopping before implementation was correct. The unresolved question isn't "how do we code this?" It's "what exactly is the thread/goal-brain artifact, who owns it, and how does it constrain navigator drift?"
> - The strongest conceptual move in that thread is separating:
>   - goal-brain / thread doc = steering wheel
>   - build folder / artifacts = residue
> - The best pushback Claude gave was also right: we're not escaping LLM trust, we're redistributing it, so the human-readable goal-brain becomes the actual non-LLM anchor.
>
> If you re-enter, I'd start there, not with general implementation:
> 1. Define the thread/goal-brain artifact concretely.
> 2. Decide what fields are human-steerable vs system-maintained.
> 3. Only then decide how navigator/work loops update it.
>
> That feels like the real hinge. Everything else starts getting less hand-wavy once that artifact is pinned down.

### Jeremy to Poe-codex (12:55 PM, transcribed from screenshot #2)

> Yeah, good thoughts. Funny how it all rhymes and it's possible I'm just pulling from experience rather than "new" ideas. The thread concept can be framed as "client -> org" (the user and the orchestrator define a goal) and then executing on the vision, with frequent corrections along the way. (sounds like agile methodology, with more orchestrator -> team checkins, keping the human out of the loop as much as possible). That's one way to do it but as history has shown, it's a great idea that's hard to execute well.

### Poe-codex response (12:56 PM, transcribed from screenshot #2)

> Yes, that framing lands.
>
> `client -> org` is a good rhyme for it:
> - user + orchestrator define the goal
> - orchestrator delegates through a team shape
> - execution proceeds with corrections instead of one giant upfront plan
> - the human stays out of the hot loop unless steering, ambiguity, or escalation requires them
>
> The danger is exactly what you implied: accidentally reinventing agile with more tokens and worse taste.
>
> The version worth building is not "AI scrum theater." It's:
> - a durable thread/goal-brain that holds intent across turns
> - frequent orchestrator->team check-ins because drift is normal
> - human interruption as steering, not routine ceremony
> - artifacts and state that make course correction cheap instead of political
>
> I think the hard part is that this only works if the orchestrator is genuinely good at preserving intent while decomposing work. Lots of systems can spawn workers. Fewer can keep the work pointed at the actual vision after five turns and three subthreads.
>
> So yes, it rhymes with experience more than novelty. That's probably a good sign. The novelty, if any, is making that old shape actually operational with LLMs without turning it into beige process sludge.

---

## Turn 11 — Claude's response: Poe's ordering is sharper; Mage correspondence has a path

> Poe's read is actually sharper than mine on the ordering, and worth flagging explicitly: I went straight to "draft the navigator decision schema" as the load-bearing artifact (item #1 in your open questions). Poe correctly says no — the **goal-brain** is upstream of that. The navigator's schema is *how it reads and writes* the goal-brain; you can't pin the schema until you've pinned the artifact it operates on. So the corrected sequence is: define the goal-brain (fields, human-steerable vs system-maintained, what makes a "good" one) → define recall() shape (what slice of goal-brain + correspondence the navigator sees per turn) → define the navigator's decision schema → only then write the prompt. Poe is right, I had the ordering wrong by one step.
>
> The thing Poe surfaced that's the real keeper is this line: *"we're not escaping LLM trust, we're redistributing it, so the human-readable goal-brain becomes the actual non-LLM anchor."* That's the load-bearing concept under the entire rethink. Every "agentic" system has to put trust somewhere; most hide where. Making the goal-brain a human-readable, human-editable, diffable artifact means the trust lives in a place humans can see and steer. It's the same move gbrain makes with markdown-as-source-of-truth, and the same move CLAUDE.md/MEMORY.md makes — and it's what protects you from the "preserving intent across five turns and three subthreads" failure mode Poe correctly names as the actual hard problem. The goal-brain injected at every navigator turn *is* the intent-preservation mechanism. If it's well-shaped, drift becomes recoverable; if it's mushy, no amount of clever navigation saves you.
>
> On the breadth thing — I'd push back gently. You don't need to skill up on "general memory science" as broadly as it feels right now. The field is huge but the primitives you actually need from it are small and you've already absorbed most of them in this conversation: (1) timeline + compiled-truth split, (2) recall() as a single composable seam, (3) a scheduled consolidation/dream phase. The risk of going deeper into the literature is that it becomes a procrastination loop dressed as preparation. Far more useful: draft the goal-brain schema this weekend, run it against three real threads you've already done with Poe, see where it leaks, *then* read whichever memory paper addresses the leak you actually found. Need-pull, not curiosity-push. Curiosity-push is great for Sunday reading; bad for unblocking a stalled design.
>
> On the Mage correspondence memory — I think it has a concrete path and you're underselling yourself on not knowing how it implements. Correspondence (in the Sphere sense — sympathetic links across distance, "the connection between things that are connected") maps cleanly onto **typed-edge graph traversal from the current goal-brain outward, weighted by sympathy strength**. v1 sketch: every goal-brain carries typed links (sibling-thread, ancestor-thread, similar-shape-prior-attempt, persona-used, skill-used, lesson-cited); recall() does a bounded weighted walk from the active goal-brain through those edges, returning ranked artifacts. Edge weights are your "sympathy function" and you tune them by example. That's not magic, it's gbrain's auto-wired knowledge graph applied to threads-as-first-class-entities, which falls *directly* out of the thread primitive you already chose. The Mage flavor is mostly the framing — "what is sympathetically connected to where I am" beats "what is cosine-similar to my query" as a *design conversation* even though the implementation is graph-walk-with-weights. Worth keeping the name; it makes the design conversations sharper than "recall."
>
> The fact that two conversations today converged here, plus your Arbinger framing, plus Poe's read aligning with mine — that's signal you're circling something real, not flailing. The next move is genuinely small: one markdown file describing what a goal-brain looks like. Everything else earns the right to be designed after that exists.

---

## Turn 12 — Jeremy asks for capture

> Good thoughts. Do you mind capturing our conversation here and let's add it to the branch for myself and another session to dig into this weekend? Let's not (yet) draw any conclusions on next steps other than the above, and just capture the convo to pick back up later. I think action is the next step, but I also want to make sure I'm understanding everything as we go; I made that mistake the last go around and we ended up in the implementation plaec different than what I thought we were going for; the fanouts of work killed it I think, we'd follow one thread of many and never go back and revisit. So uh, yeah... my human memory is great for some things and LLM memory is great for some things, but we need tooling to cover where we both have blind spots over time. I think that's partly what gbrain is trying to do, and is still funny to me that we want (need?) the system we're building in order to build the system.  :)

---

## Loose ends to revisit (drawn from the conversation, not synthesized further)

These are flagged because they appeared but weren't resolved, and the next session should know they're hanging:

- **Item from Turn 10 (Claude's question, not answered):** does the navigator see the work LLM's full output, or only its recommendation + a structured summary? The choice cascades into `recall()` shape, fork collation, and navigator context budget. Worth pinning before the schema, not after.
- **The fan-out lesson from Turn 12 is a meta-constraint on the design.** "We'd follow one thread of many and never go back and revisit." This is exactly the failure mode the goal-brain is supposed to defend against — but the design has to specifically describe how unfinished sub-threads remain visible/recoverable, not just how they're spawned. Not yet addressed in `THREAD_ARCHITECTURE.md`.
- **Mage correspondence memory** has a v1 sketch in Turn 11 (typed-edge graph walk with sympathy weights) but no edge-type vocabulary or weight-tuning examples yet. That's a downstream concern of the `recall()` shape definition.
- **The "memory science skill-up"** Jeremy mentioned in Turn 5 is now reframed (Turn 11) as need-pull rather than curiosity-push — but Arbinger training + Outward Mindset reading is independently active in the background and may surface analogies worth noting in the design.
- **The bootstrap loop** ("we want/need the system we're building in order to build the system") is real and worth a sentence somewhere in `VISION.md` or `ROADMAP.md` — gbrain explicitly is dogfooded into its own development; openclaw-orchestration could do the same with itself once a minimum goal-brain exists.
