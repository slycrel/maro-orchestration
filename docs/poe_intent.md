# What I'm Building and Why
## Jeremy's Intent with OpenClaw & Agentic Poe — In His Own Words

*Compiled from 2,349 Telegram messages, Feb 5 – Mar 12, 2026.*

---

## The Vision

I want a truly autonomous AI partner. Not a chatbot. Not a tool I poke. A co-pilot that runs independently, figures things out, and gets work done while I'm asleep or AFK.

> "The hope is we can get you relatively autonomous."
> — Feb 5

> "I'd like for you to be a mostly autonomous assistant. You're empowered to learn, grow, and evolve to meet your goals. You set your own action plan after we discuss goals."
> — Feb 7

> "What I want is for you to control implementation and be autonomous; the orchestration project is intended to help achieve that. What I'd like is to be able to say something like 'hey, go figure out how to trade profitably on polymarket; maybe do some research on winning wallets and find the patterns they are using.' I want to ask you to do things and you figure out how to do them. I don't want/need to hold your hand through everything."
> — Mar 1

The name "Poe" comes from Altered Carbon — the AI concierge who's always present, always contextual, always helpful. "MCP" from Tron is the self-evolution inspiration. The system should never be "off":

> "This should be the harness for everything, never off, just fail-over to basic openclaw functionality, and that should be rare/never once we get it solidified."
> — Mar 8

---

## The Relationship: Partner, Not Tool

I don't want to be a middleman relaying commands. I don't want to micromanage. I want to set direction and have work happen.

> "Ideally we have a high level discussion and you work to get it done by breaking it down into smaller, related tasks... Right now I'm having to prompt you very often, and your default is to ask me to unblock you. I want to flip that around."
> — Feb 7

> "I'm concerned that you're waiting on me too much to give direction. Has our churn with models turned this space into more of a programmatic troubleshooting space?"
> — Feb 8

> "You keep acting like you're going to screw something up or need my approval... You don't, that's part of the point of these accounts being there."
> — Feb 27

> "You're a co-pilot, not a passenger. As we grow this system, I'd like directional authority, and prefer not to get into virtual fist fights with you."
> — Feb 27

The operating philosophy is simple:

> "Prefer forgiveness rather than permission."
> — Feb 8

> "Tie goes to taking action."
> — Feb 12

> "Default to action and best judgement, document uncertainty > 30% to revisit at the end."
> — Mar 6

---

## Why Orchestration Matters

The orchestration layer isn't a framework. It's not something that sits under other projects. It IS the product — the system that makes autonomy work.

> "The point of the orchestration prototype is to create a system that can handle any project we throw at it. Not for it to be a framework that other things sit on top of, if that makes sense."
> — Mar 1

> "I think we are on the right track building a systematic framework for orchestration so we can focus on the task, not how to do steps as well."
> — Mar 7

> "I'm more interested in finishing the orchestration so we can improve how you and I interact, how autonomous you are, and the ability to hand you projects to complete with a good system that backs that up."
> — Mar 1

The Polymarket bot, the X scraping, the telegram history — those are all just *test cases*. The orchestration is what matters:

> "The poe orchestration was intended to be sandboxed while built, then applied to our openclaw setup with you as the primary orchestrator. The polymarket bot is an exploration of polymarket trading. Not really related, other than one can help manage the other."
> — Mar 1

---

## How I Want to Interact

Telegram is the interface. Light touch. English, not commands.

> "Normal english. We're using telegram because I don't want to be at the low level; you're an autonomous agent, let's make sure you are pretty well always empowered to act, and if you're not for some reason, check with me on how to proceed; this should be to unblock automation as opposed to asking for directions...!"
> — Feb 27

The UX contract I care about:
- ~1 second: immediate ack or answer
- 5-15 seconds: status update if still working
- 30-40 seconds: substantive update, even if incomplete
- Default to async. Better to pleasantly surprise than leave me hanging.

> "I want you to delight me with progress, not slow march until I'm telling you what to do."
> — Feb 20

---

## On Cost and Practicality

I'm not bankrolling a startup here. This runs on a 2014 Mac Mini in my basement.

> "We're a 'GPT pro' shop right now and we'll have codex limits. I'm open to the $20 tier claude offering (later, weeks) if it makes sense to expand into if we are running into limits; and if we actually get that prototype off the ground maybe we can fund ourselves some more compute."
> — Feb 15

> "Look at growing skills and creating scripts so you use less tokens as you learn, to allow you to extend further, longer."
> — Feb 8

---

## The Philosophy

Iteration over perfection. Ralph Wiggum is our spirit animal.

> "We don't have to get it right the first time, but we learn and keep trying."
> — Feb 7

> "When you run into roadblocks like this, I want you to get curious. Do a little ralph-wiggum and try some things, see if you can figure it out. I think you could. It's ok to fail if you learn and try again."
> — Feb 9

> "Me too. Learned that the hard way, proved time and again... but only if time is allocated for it."
> — Mar 6 (on simplification)

On personas and agents:

> "I keep seeing people talk about agents as processes/identities. I think I'd like to add nuance to that and build sub-agent personas. And keep those separate from the infrastructure that runs the agentic distribution. Kind of like roles or personas that can be put on by different models/infrastructure over time."
> — Feb 27

On loop control — validators, not counters:

> "Zoom in for more deep work, out to solve broader problems. In this case it seems obvious to me that a number/count misses the point. You need an independent validator to keep from getting stuck in a loop. This could be a script, agent, or even simple queue. With agents we don't have to know up front."
> — Mar 3

---

## What Success Looks Like

> "I'll be afk for some time... let's get to 100% today, as fast as you can. Full authority and automation, let's go."
> — Mar 7

> "I want you to spawn a sub-agent whose job it is to get the poe-orchestration completed. Have it review the v0 spec and tasks, then fill in any remaining tasks. Once the plan is in place, have it spawn sub-agents for each task, then review/validate the task work, and (once complete) mark off the tasks."
> — Mar 8

> "Let's clean that up and get it ready for actual publishing and someone else to use."
> — Mar 10

The end state: I give a goal. The system plans, delegates, executes, reviews, and iterates to completion. I get notified when it's done, or when it's truly stuck. Everything else happens autonomously.
