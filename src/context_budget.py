"""Bounded accumulating context for multi-step prompts.

The `completed_context += <this step's result>` pattern is the one place
in the truncation audit (BACKLOG "Arbitrary-truncation audit", 2026-08-03)
where a bound genuinely earns its keep. Everywhere else the cuts guarded a
runaway the executor's own `max_tokens=4096` already prevents; here the
growth is real and **quadratic in step count**, because every step re-sends
everything before it.

Measured over 268 recorded runs (1,760 step results):

    whole-run accumulation   median 7,464 ch   p90 16,292   p99 28,547   max 74,288
    re-sent volume (quadratic) median 29,828   p99 263,560  max 1,253,083
    one step result          median 1,168 ch   p99 4,671    max 20,534
    longest run              34 steps

The old call sites answered this with a per-entry cut and NO total bound —
`factory_thin` at 200 chars/step, `director` at 2,000 — which is backwards
on both axes: far too tight to be useful evidence, and unbounded in the
dimension that actually grows. A next step planning against 200 characters
of what just happened is the quiet-quality-loss failure mode.

So: a generous per-entry cap, a real total budget, and **oldest-first
eviction** — the immediately preceding step is what the next one builds on,
so recency is what to protect. Eviction is announced in the rendered text
rather than silent, the same contract the judge windows got: a model reading
this must be able to tell a complete history from a trimmed one.

Note the subprocess backend bills re-sent context as cache reads at 0.1x, so
the dollar impact of the quadratic term is ~10x softer than the token volume
suggests. The volume is still real, and the budget is set for volume.
"""
from __future__ import annotations

from typing import List

# ~99% of single step results fit (p99 is 4,671 chars); one pathological
# step cannot eat the whole budget. Same ceiling the judge windows use.
DEFAULT_ENTRY_CAP = 4000

# ~6,000 tokens. Covers the median (7,464) and p90 (16,292) whole-run
# accumulations intact and trims only the worst few percent. Chosen from the
# distribution, not from taste — the point of the audit.
DEFAULT_TOTAL_BUDGET = 24000

# ---------------------------------------------------------------------------
# STORE profile — for evidence that gets PERSISTED, not just prompted.
#
# The defaults above are priced in tokens, which are cheap and per-call. These
# are priced in disk, which is forever and re-read on every load. The outcomes
# ledger is the live case: `memory_ledger.load_outcomes` parses the WHOLE file
# to return the last 20 rows, so every byte added to a row is paid again on
# every read, for the life of the file.
#
# Breadth over depth, deliberately. The consumer is a later extractor asking
# "what did this run DO" — knowing all six steps at 500 chars each beats
# knowing two of them at 800. (Depth is already covered at finalize time,
# where the wide prompt-grade view runs and its lessons land on the row; the
# stored copy exists for the deferred post-verdict re-extraction, which has
# nothing else to read.) So: a total that fits a median run WHOLE — 6 steps,
# median step result 1,180 chars — and a per-entry cap that makes that fit.
#
# The trade, priced 2026-08-06 rather than guessed at: 1,493 rows / 868 KB
# today (~580 B per row), full parse 12 ms. At ~3 KB per row the ledger goes
# to ~4.3 MB and the parse to ~62 ms — and `load_outcomes` parses the WHOLE
# file to return the last 20, so that cost is paid on every call. 50 ms
# against a run that costs dollars and minutes is worth 17x the evidence.
# What eventually pays it down is `compress_old_outcomes` (still on the STORE
# worklist), not a tighter cut here.
STORE_ENTRY_CAP = 500
STORE_TOTAL_BUDGET = 4000

# STORE-grade cap for VERDICT/RATIONALE prose — the single fields that say
# WHY a run got its verdict: goal_verdict_summary and its stamp siblings,
# the stored closure summary, judge/audit reasons, the dispatch navigator's
# recorded reasoning, the NOW answer excerpt. These are recalled by future
# re-attempts and audited by humans; a mid-word cut here is a rationale the
# next run half-reads. Measured 2026-08-13 over the box workspace (156
# metadata stamps, 50 closure rows): the old 300-char cut bit 70% of
# verdict summaries and the 500-char store cap bit 90% — every censored
# max observed was <= 500, so 2,000 (~500 tokens) clears everything real
# by 4x while still bounding the pathological case. Always applied via
# clip(), never bare slicing — the reader must see when it bound.
VERDICT_PROSE_CAP = 2000

# STORE-grade cap for ONE lesson's text in a decision-prior brief. Measured
# 2026-08-13 over the live lesson store (n=459): median 254, p99 478, max
# 573 — the old 200-char cut fell below the MEDIAN (95% of stored entries
# were cut mid-word). 800 holds every lesson yet observed, whole.
LESSON_ENTRY_CAP = 800


def clip(text, cap: int) -> str:
    """Cut text at cap and say so — never silently.

    The audit's universal idiom for SINGLE-VALUE evidence sites (the one
    `step_exec.verify_step` already had): a model reading the prompt must
    be able to tell complete evidence from trimmed evidence. Multi-entry
    sites want ContextBudget instead; this is the scalar counterpart.
    Returns the text unchanged when it fits.
    """
    text = str(text or "")
    if len(text) <= cap:
        return text
    return (f"{text[:cap]} … [truncated: first {cap} of "
            f"{len(text)} characters]")


class ContextBudget:
    """Accumulate step results for a prompt, bounded and honest about it.

    Entries are kept whole where possible. When the total exceeds budget the
    OLDEST are dropped, and ``render()`` says how many went.
    """

    def __init__(self, *, total_budget: int = DEFAULT_TOTAL_BUDGET,
                 entry_cap: int = DEFAULT_ENTRY_CAP,
                 separator: str = "\n\n") -> None:
        self.total_budget = int(total_budget)
        self.entry_cap = int(entry_cap)
        self.separator = separator
        self._entries: List[str] = []

    def add(self, entry: str) -> None:
        """Append one entry, capping it (visibly) if it is oversized."""
        entry = str(entry or "")
        if not entry:
            return
        if len(entry) > self.entry_cap:
            entry = (f"{entry[:self.entry_cap]}\n"
                     f"… [entry truncated: first {self.entry_cap} of "
                     f"{len(entry)} characters]")
        self._entries.append(entry)

    def render(self) -> str:
        """The context block, oldest entries evicted to fit the budget."""
        if not self._entries:
            return ""
        kept: List[str] = []
        used = 0
        # walk newest -> oldest so recency survives
        for entry in reversed(self._entries):
            cost = len(entry) + len(self.separator)
            if kept and used + cost > self.total_budget:
                break
            kept.append(entry)
            used += cost
        kept.reverse()
        dropped = len(self._entries) - len(kept)
        body = self.separator.join(kept)
        if dropped:
            return (f"[{dropped} earlier entr"
                    f"{'y' if dropped == 1 else 'ies'} elided to stay within "
                    f"the context budget; the most recent {len(kept)} "
                    f"follow]{self.separator}{body}")
        return body

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
