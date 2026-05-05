# JP Morgan "Ask David" — LangChain Interrupt 2025

**What this is:** Verbatim transcript + synthesis notes from the LangChain Interrupt 2025 talk by David and Jane (JPM Private Bank investment research) on building **Ask D.A.V.I.D.** — a multi-agent QA system for investment research. Filed as reference material for the `arch/thread-navigator` re-assess.

**Filed:** 2026-05-04
**Source talk:** https://www.youtube.com/watch?v=yMalr0jiOAc
**Surfaced via:** @adamghowiba tweet 2026-05-03 (https://x.com/adamghowiba/status/2050886233921061281, ~1.7M views)
**Acronym:** D.A.V.I.D. = Data Analytics Visualization Insights and Decision-making assistant

---

## Findings

### What's converged (the unsurprising part)

Ask D.A.V.I.D. uses the same skeleton everyone is converging on: **supervisor → specialized subagents (NL2SQL / RAG / analytics) → reflection node (LLM-as-judge) → human-in-the-loop for last-mile accuracy**. This is the "showing up everywhere" pattern Adam Ghowiba called out. Poe's Director / Workers / Inspector / HITL has the same shape. There is no novelty claim to make on the skeleton itself — JPM, Poe, and the rest of the field are all building the same outline.

### Where JPM explicitly stops

The talk is honest about scope and limits, in ways the tweet doesn't surface:

1. **Domain-specific QA, not open-ended autonomy.** Jane's words: "Ask David is a domain specific QA agent." Two subgraphs (general QA / fund-specific). Bounded query shapes. They scale by *adding subgraphs per intention*, not by making one supervisor smarter.
2. **Static graph + human iteration as the improvement loop.** Lesson #1 is "start simple and refactor often" — the iteration is engineers refactoring the graph, not the graph hardening itself. There is no claim that the system improves autonomously over time.
3. **Last-mile is unsolved on purpose.** Their stated accuracy ladder: general LLM ~50% → engineering (chunking, search algos) ~80% → workflow chains / per-intention subgraphs ~90% → "the last mile may not be achievable" → therefore HITL. They explicitly do not bet on closing the 90→100 gap with more architecture.
4. **Evaluation-driven development is half the talk.** Long eval phase even when dev phase is short. Independent eval per subagent. Metric matched to agent type (conciseness for summarizers, trajectory for tool-use agents). LLM-as-judge + human review as the scaling mechanism. Without this, "iterate fast" is theater.

### Specific architectural deltas vs. Poe / `arch/thread-navigator`

Things that are concretely *different* from the converged skeleton, and where the difference lands:

- **Personalization node is separate from persona.** JPM personalizes the answer between retrieval and reflection — same retrieved facts, different shape per asker (advisor vs. due-diligence specialist). This is *recipient-side* shaping. Poe today has *agent-side* persona (researcher / ops / etc.), selected by goal. The thread-navigator doc's "navigator picks persona per turn" still treats persona as agent-side. JPM's separation is a distinct concept — worth noting that "persona" today may be conflating two things.
- **Reflection retries; it doesn't just judge.** Jane: "It uses LM judge to make sure the answer makes sense. If it doesn't, what do we do? We try again." Synchronous retry gate, in-flow. Poe's `claim_verifier.py` annotates `NOT_FOUND` and `inspector.py` is closer to an offline review loop. Different placement.
- **Two-tier delegation: planner picks subgraph, supervisor inside picks subagent.** Not one supervisor over everything. The planning node is a *router* into a bounded set of subgraphs, and only inside that subgraph does the supervisor / agent team operate.
- **Memory lives in the supervisor.** "Supervisor has access to short-term and long-term memories so it can customize the user experience." Single-orchestrator memory. Poe distributes memory across modules (memory.py, knowledge_web.py, captain's log, lat.md graph) and `arch/thread-navigator` proposes a unified `recall(thread_state)` seam. JPM's answer is simpler because their problem is simpler.

### Where the comparison runs out

The talk is bounded by what JPM is solving for: a domain-specific QA system inside a bank, with a clear accuracy metric (did it answer the fund question correctly?), bounded query shapes, and a human SME as the last-mile authority. Poe's problem space — open-ended autonomy, multi-day missions, self-improving navigation, cross-orchestrator portability — is a strictly larger problem with a fuzzier evaluation regime. The talk gives no information about that larger problem because JPM didn't take it on.

This is also probably the answer to "why hasn't this pattern caught on for autonomy." Nobody at LangChain Interrupt 2025-level publicly claims a self-improving agent graph that closes the 90→100 last-mile gap without humans. JPM's choice to bound the domain *is* their answer to that gap. Poe is betting that crystallization (Stages 1→5) + evolver loops can close more of it. That bet is still unvalidated.

### What the talk does *not* answer that the re-assess is looking for

The thread-navigator doc is searching for a primitive that unifies goals/loops/missions/tasks/plans into one shape ("thread") with a per-turn navigator that picks among extend/execute/fork/collate/close/escalate. JPM's talk has no analogue for this — their flow is a fixed graph per query, not a navigator picking shapes per turn. So the talk is useful as **negative reference** ("here is the static-graph version of the same primitives") but not as a source of the missing high-level pattern the re-assess is hunting for.

### Concrete reference points for the open-decisions list (`docs/THREAD_ARCHITECTURE.md`)

Listed as observations, not as proposed actions:

- **Open Decision #1** (Navigator prompt + decision schema): JPM's planner-then-supervisor two-tier delegation is a worked reference for how a sub-thread navigator might relate to its parent.
- **Open Decision #6** (How navigator improves): JPM's evaluation regime — independent per-subagent eval, metric matched to agent type, LLM-as-judge + human review — is the most concrete answer in the public record. Crystallization signal has to come from somewhere; this is one shape it could take.
- **Persona / personalization separation:** if the navigator is going to "pick persona per turn," it may be worth distinguishing agent-side persona (who is doing the work) from recipient-side personalization (who the answer is being shaped for) before pinning the schema.

---

## Verbatim transcript

Source: YouTube auto-captions (en), cleaned. Speakers: David (intro / framing) hands off to Jane (architecture / lessons) at the marked transition.

### David — intro and framing

Good morning everyone. I'm really thrilled to be here today at the interrupt conference. A big thank you to the organizers for putting together such a great event and for inviting us to be able to share our journey with you today. My name is David. I'm here with my colleague Jane from the JP Morgan Private Bank. So, at the private bank, we're part of the investment research team.

And this is the team that's responsible for curating and managing lists of investment products and opportunities for our clients. Now, when I talk about lists, we're not talking about a few dozen or a few hundred products. We're talking about thousands of products, each backed by many years of very valuable data. So, when we have such extensive lists of diverse products, questions are inevitable. And when there's a question, our small research team needs to go find some answers. So we go digging around databases of materials, files and we piece together answers to the questions that come across our desks each and every day.

Now not only is this a very manual and time consuming process but this limits our ability to scale and it really makes it difficult for us to really provide insights into the products that we have on our platform. So as a group we got together we challenged ourselves. We said, "Let's come up with a way to automate the investment research process, aiming to deliver precise and accurate results." Today, when you have a question, you come to me. You come to David and Dave will give you an answer. But tomorrow, you'll be able to go ask David, our AI powered solution designed to transform the way we answer investment questions. With Ask David, we're aiming to provide curated answers, insights, and analytics delivered to you as quickly as you can ask a question.

Now, I know you're all probably asking yourselves, David, are you going to just put yourself out of a job? Not quite. What we're doing is we're building a tool to make our jobs easier and much more efficient. The stakes here are high. Billions of dollars of assets are at risk and we're committed to building a tool that not only meets but also exceeds the expectations of all of our stakeholders. Looking to the future, we're really excited about all the possibilities that Ask David potentially brings to the table.

So now to dive deeper into the technical magic behind Ask David, I'll turn it over to Jane who'll walk you through the nuts and bolts of how we are making our vision a reality. And she might even let you in on what Ask David stands for because I promise you I didn't name this after myself. Thank you.

### Jane — architecture and lessons

Thank you, David. So, Ask David is a domain specific QA agent. So, let's start with terminology analysis.

First of all, we have decades of structured data. Those are the backbones of many up and running production systems. Prior to the introduction of an agent, users have access to the same data, but they have to navigate through different systems and manually syndicate information. An agent can introduce efficiency and integrated user experience. Next, we have unstructured data. As a bank, we manage a vast amount of documentations including emails, meeting notes, presentations.

With the rise of virtual meetings, we also have increasing amount of video and audio recordings. How do we make full use of that information? The advancement of agent[s] really bring tremendous opportunity in this area. Lastly, as a research team, we have proprietary models and analytics which are designed to really derive insights and visualization to help decision-making. Previously it would require a human expert to conduct these kind of analysis and offering white-glove service. With the help of agent we can scale the insight generation and we can make our service available to more of our clients.

Now imagine being a financial advisor in a client meeting and your client suddenly brings up a fund, asks you why it's terminated. Believe me, it's actually a very loaded question. So in the past you would reach out to our investments research team, talk with real David, and then you figure out what's the status change history of the fund and what's the reason behind it, what's the research about this fund, what are similar funds, how do I curate these answers specific for this client, and you will come up with a presentation yourself manually. With the help of agent we can get access to the same data, analytics, insights and visualization right in your meeting, enable the real-time decision-making — that is our vision of Ask David. And you probably guessed it: David stands for Data Analytics Visualization Insights and Decision-making assistant.

So this is our approach to build up Ask David, which is a multi-agent system. Starting from our supervisor agent which acts as orchestrator — it talks with our end user, understands their intention, and tries to delegate the task to one or more of sub-agents in the team. The supervisor agent has access to both short-term and long-term memories so that it can customize the user experience. It also knows when to invoke human-in-the-loop to ensure the highest level of accuracy and also reliability.

Next, we have our structured data agent. It will translate the natural language into either SQL queries or API calls and use a large language model to summarize the data on top. Unstructured data is a little bit different from structured data. Usually it requires some kind of pre-process but as long as you save it, vectorize it and put into a database, we can employ a RAG agent on top to effectively derive information. Lastly, we have the analytics agent. We talked about our proprietary models and APIs. They are usually in the format of APIs or programming libraries.

For a simple query that can be directly answered by API calls, we use a ReAct agent and use APIs as tools. But for more complex queries we'll use text-to-code generation capabilities and use human supervision for the execution. This graph is our end-to-end workflow. It's starting with a planning node and you probably noticed there are two subgraphs over here. One is a general QA flow — for any general questions, for example "how do I invest in gold?" — it will go to the left-hand side subgraph. And if the question is regarding a specific fund, you will go to the right-hand side flow. Each of the flows is equipped with one supervisor agent and a team of specialized agents. Once we retrieve the answer you probably notice there's one node to personalize the answer and another node to do the reflection check. I will explain it in detail in an example to follow. The whole flow ends with summarization.

So now back to our client question. Why was this fund terminated? This is how our agent can handle it. As you can see on the right-hand side, the agent answer was: the fund was terminated due to a performance issue. And you can actually click into the reference link to see more about the fund performance and the reason behind it. What really happened behind the scene? From that planning node we start to understand this user inquiry is related to a specific fund.

So it goes to the specific-fund flow and the supervisor agent inside will be able to extract the fund information as a context and understand that actually the doc-search agent is the right one to solve the problem. Once doc-search agent gets that information it starts to trigger the tools underneath to get the data from MongoDB. Once we retrieve that information, the data and information will be personalized. The same information can be presented in different ways depending on who is asking. For example, we are talking about the fund termination reason here. A due-diligence specialist may demand a very detailed answer, while an advisor maybe just needs a general answer.

So this personalization node will tailor the answer based on the user roles. Next we have the reflection node. It uses LLM-as-judge to make sure that the answer we generated makes sense. If it doesn't, what do we do? We try again. So the last one — the whole flow ends with summarization. In the summarization node we do several things: we summarize the conversation, we update the memory, and we return the final answer.

So it was quite a journey working on this multi-agent application and we are very excited to share the lessons learned. Number one: **start simple and refactor often.** I know I showed you a fairly complex diagram earlier but we didn't really focus on building that diagram from day one. Day one is a plain vanilla ReAct agent. We try to understand how it works, and from there we work on the specialized agent in picture two — which actually is a RAG agent. We start to customize the flow, but once we get very comfortable with the performance of the specialized agent we start to integrate that into our multi-agent flow in picture three with a supervisor. And in picture four — that's our current state — we actually have the subgraphs generated for specific kinds of intentions. Right now we only have two intentions but we can scale easily with this architecture.

So I talked about fast iterations, but how do we know every iteration we're moving towards the right direction? The answer is **evaluation-driven development.** Compared to traditional AI projects, GenAI projects actually have a shorter development phase, but we do have a long evaluation phase. Our suggestion is to start early — think about the metrics, what kind of goal you want to achieve. As we are in financial industry, obviously accuracy is one of the most important things, and the continuous evaluation helps you get that confidence you are improving day by day. There are additional tips here based on our own experience of evaluation. The dark blue bars are coming from the matrix of evaluation on our main flow, and the green one is one example of our sub-agent. Tip number one: make sure you **independently evaluate your sub-agents.** The key for your evaluation is to find places to improve — these help you figure out what's the weak link to improve your accuracy.

Second point: depending on how you design your agents, **make sure you pick the right matrix.** If you have a summarization, you may want to check whether your summarization is concise or not — so conciseness is one of the metrics you want to pick. If you are doing a tool call, maybe you can have the trajectory evaluation instead.

There is a common myth — especially if you're a developer talking about TDD, a lot of people are saying "I just don't do that, it's a lot of work." But it's not the same in evaluation. You actually can start evaluation with or without ground truth — it doesn't matter. There are so many metrics beyond just accuracy and each one of them will provide you some insight. And once you start doing evaluation you will have review; once you start doing review you're actually going to accumulate more ground-truth examples. Lastly, we have large language model itself as judge in combination of a human review — this automatic solution really helps us scale without adding too much burdens to our human SMEs to review large amounts of AI-generated answers.

Talking about SMEs, our last lesson learned is about **human-in-the-loop.** When you apply a general model to a specific domain, usually you will get less than 50% of accuracy. But you can do quick improvements like chunking strategies, you can change your search algos, and proper engineering can get you to that 80% mark. From 80 to 90 we use workflow chains — we are creating the subgraph so that we can fine-tune certain kinds of questions without impacting each other. Between 90% and 100% — that's what we call the last mile, and the last mile is always the hardest mile in terms of GenAI applications. It may not be achievable to get that 100% mark. So what do we do? Human-in-the-loop. It's very important to us because we have billions of dollars at stake and we cannot afford inaccuracy. In other words, **Ask David still consults with real David whenever needed.**

In conclusion, three takeaways for you: **iterate fast, evaluate early, keep humans in the loop.** Thank you so much.
