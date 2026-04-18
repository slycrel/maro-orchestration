# Coding notes — shape for a heavily-iterating codebase

**Audience:** any contributor (human or model) working in this repo during
active iteration. Not a style guide. Not a linter config. Principles that
make rework cheap while the system's shape is still emerging.

Jeremy's framing: experienced devs lean toward simple + maintainable
because they'll own the rework cost. LLMs don't feel that cost the same
way and tend to ship code that works-right-now but is hostile to the next
edit. These notes are the compromise — they make the rework-pain
questions explicit so the cost shows up before the diff lands.

## Mental model

Treat every chunk of work as if a blind collaborator (including
future-you) will pick it up within the next 3 runs. That reader can't see
the session that produced this code. Everything they need to understand
it must be in the code itself or in one short doc you pointed at.

## Principles

1. **At chunk boundaries, ask: "will this need widening within 3 runs?"**
   If yes, widen now with a registry, a protocol, or a config point.
   Don't pile up `elif`s hoping the next chunk won't mind.

2. **Three similar blocks is fine. Four wants extraction.** The moment
   you're about to copy-paste the third similar block, pause and look
   for the seam. Don't pre-extract (YAGNI); don't ignore the third copy.

3. **Prefer explicit registries to dispatch-by-string.** A `HANDLERS =
   {"ralph": handle_ralph, "verify": handle_verify}` dict beats a chain
   of `if prefix == "ralph"`. It also makes "what modifiers exist?" a
   one-line question for the next reader.

4. **When adding a flag or prefix, ask: is this a registry entry?**
   Magic-string modifiers are registries hiding inside branches. If we
   already have three, it's probably time.

5. **Seams get tests, internals don't.** Test the module boundary (does
   `handle()` route correctly?), not the private helper (`_parse_line`
   returned a list of tuples). The first survives refactor; the second
   breaks the moment someone renames the helper.

6. **Don't refactor mid-feature.** If you notice a seam while shipping
   something else, add a follow-up backlog item and keep moving.
   Mid-feature cleanups double the diff and lose the thread.

7. **Ship with seams visible, not buried.** A file that's grown past
   ~800 lines wants a split. A function past ~80 lines wants
   decomposition. You don't have to do it that turn, but name it —
   either in BACKLOG or as a comment at the top of the file:
   `# TODO(refactor): splitting by phase candidate.`

8. **Expect pivots.** If a new constraint could twist the goal later,
   favor designs where prior artifacts still apply. Persistent
   workspaces, resolved-intent artifacts, captain's log events —
   these outlast the specific run they were written for.

9. **Ask before you delete or move.** Investigate first. Unfamiliar
   files/branches/config may be the user's in-progress work. Only
   destructive operations are truly expensive; investigation is cheap.

10. **Code reads like a walkthrough.** Name variables after domain
    concepts (`scope_verdict`, not `sv`). Keep function bodies short
    enough that a reader can scan the happy path without scrolling.
    Comments explain *why*, never *what* — the code already says *what*.

## Anti-patterns to catch on sight

- **A new if/elif branch on a string prefix** — probably a registry
  entry in disguise.
- **A function that takes >5 parameters** — probably a dataclass
  collapsing itself into a signature.
- **Copy-pasted adapter code** — probably a protocol waiting to be
  extracted.
- **A mock in a unit test that specifies internal call order** — probably
  testing the implementation instead of the contract.
- **A `try: ... except Exception: pass`** — probably a real bug being
  swallowed. Narrow the except; log what you're skipping.
- **A TODO with no owner or date** — will outlive its author's memory.
  Either fix it now or turn it into a backlog entry.

## What NOT to do under these principles

- **Don't pre-extract abstractions for hypothetical future uses.** Three
  blocks is fine. Extract at four, not at one.
- **Don't refactor unrelated code while shipping a feature.** The PR
  becomes unreviewable.
- **Don't rewrite tests "cleaner" when they're already passing.** They're
  earning their keep as regression guards; the ugliness is the price.
- **Don't add abstractions when a data literal works.** A dict of handlers
  beats a class hierarchy for a dozen lines of dispatch.

## When these principles don't apply

Exploration code — prototype scripts, spikes, scratch notebooks — can
violate all of this. Mark it with a path (`prototypes/`, `scratch/`, or a
comment) so the next reader knows not to apply the principles to it.
Production paths get the principles; exploration paths don't.

## On "rework is inevitable"

Jeremy's posture: refactor when the shape is known, not before. These
principles aren't a bet against that — they're the minimum overhead that
keeps the codebase honest *during* the exploration so the eventual
refactor doesn't require an archaeological dig. Seams visible beats
seams buried.
