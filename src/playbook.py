"""Director's Playbook — evolving operational wisdom.

The playbook is a living markdown document at ~/.maro/workspace/playbook.md
that captures what the system has learned about doing its job well. Unlike
personas (identity) or skills (procedures), this is meta-level operational
knowledge: when to use which approach, what failure patterns to watch for,
and how to make better decisions.

Three sources feed the playbook:
  1. Standing rules — promoted from lessons (knowledge_lens.py)
  2. Evolver suggestions — when applied, the insight is captured here
  3. Manual edits — operator can directly edit the playbook

The playbook reaches prompts through recall() substrate #7 → the loop's
decompose/execution context (`RecallResult.as_loop_block`). The director's
compact context block (`as_context_block`) currently omits it — a
half-closed loop confirmed 2026-07-21 (wiring-inventory row 17; BACKLOG).
It's meant to be short, opinionated, and actionable.

Injection is RANKED, not positional (swarm-review chunk 2): learned
entries outrank seed entries, newest learned first, exact duplicates
dropped — so an entry appended at the file's tail can never be starved
by whatever sits above it (the 2026-07-16 spam incident buried every
learned entry below 40 duplicate lines, and the seed alone already
overflowed the 800-char window — battery V6).

Curation rides the consolidation dream cycle (`knowledge_web.
maybe_consolidate` → `curate_playbook`): a free deterministic dedup pass
plus a size-gated, adapter-gated LLM compression pass. Every rewrite
archives the previous version to `playbook_history/` first (append-only;
learning data is never destroyed).

Usage:
    from playbook import load_playbook, inject_playbook, append_to_playbook
    wisdom = load_playbook()              # Full text
    block = inject_playbook()  # Ranked selection for injection (default budget)
    append_to_playbook("Research tasks need gather→synthesize→verify steps.")
"""

from __future__ import annotations

import logging
import re
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _playbook_path() -> Path:
    from config import playbook_path
    return playbook_path()


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

# Operator surprise read 2026-08-02 (P1–P24 reactions, recorded in
# docs/history/2026-08-02-playbook-surprise-read.md): entries are priors
# the run weighs, not requirements it obeys — "usually, do this", never
# "it has to look like this" (otherwise the LLM does what we ask instead
# of what it's capable of). The ≤15-words→1-4-steps cap was certified
# inappropriate and removed outright ("we simply don't know what a run
# might take, even if it's just a few words").

# The same decree in prompt form, for the generators that MINT injected
# guidance. The 2026-08-02 rewrite fixed the live playbook by hand; without
# this the evolver's next append regresses it, which is the upgrade edge that
# surprise read left open. Sibling of memory._LESSON_FORM_RULES: that one
# governs what a lesson records, this one governs how guidance is worded.
GUIDANCE_FORM_RULES = textwrap.dedent("""\
    Guidance form — you are writing a prior the run weighs, not a rule it obeys.
    This text is injected verbatim into every director and decompose call.
    - Write "usually X", "X is often the cheaper first move", "X tends to
      matter when Y" — never "always X", "you must X", "require X", "reject
      any Y". A run that can see a better move must stay free to take it.
    - Say what tends to be true, and when. Don't prescribe the shape of the
      answer: "research goals benefit from gather → synthesize → verify",
      not "decompose research goals into exactly three phases".
    - No counts, caps, step budgets, or word limits as rules. We don't know
      what a given run will take.
    - Name the condition when the prior is narrow ("on goals with no named
      source, usually …") so a run can tell whether it applies.
""").strip()
_SEED_CONTENT = """\
# Director's Playbook

Operational wisdom accumulated by the orchestration system. This document
is maintained automatically (evolver, standing rules) and can be edited
manually by the operator. It's injected into director and decompose context.

Entry form (operator decree, 2026-08-02): guidance reads "usually, do
this", not "it has to look like this" — entries are priors the run weighs,
not requirements it obeys.

---

## Decomposition

- Research goals benefit from a gather → synthesize → verify structure.
- Wide/deep goals often work well with staged-pass decomposition.
- Atomic steps usually beat broad ones — often one file or one command per step.

## Execution

- If a step fails 3 times, the problem is usually the decomposition, not the execution.
- Build tasks usually need more room than research tasks (~2x tokens is a fair prior, not a cap).
- Always verify outputs before recording as done.

## Cost

- Execution floor is MID (2026-07-21 unification); POWER at orchestrator/planner/reviewer decision points; CHEAP only for non-agentic calls (classify, triage, curation).
- Enable extended thinking for decompose (high) and advisory calls (mid).
- Narrow goals can often skip multi-plan (saves 3 LLM calls).
- Cost signals are inputs to weigh, not verdicts — when cost and correctness pull apart, correctness usually wins.

## Quality

- The verification loop is usually the highest-leverage quality investment — though a good skill or foundational data can beat it.
- Inspector friction signals should be acted on, not just logged.
- Standing rules are zero-cost — promote when validated, and contest/retire is the exit when one turns out wrong.

---

*Last updated: {date}*
"""


def load_playbook() -> str:
    """Load the full playbook text. Creates seed file if missing."""
    path = _playbook_path()
    if not path.exists():
        seed_playbook()
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _section_span(text: str, section: str):
    """(header_end, section_end) char offsets of the named section's body,
    or None when the file has no such header LINE.

    LINE-anchored on purpose, and the ONE section-boundary implementation
    (used by both append_to_playbook's insertion and section_text's
    membership reads, so the two cannot drift): the original substring
    lookup treated `## Canon` embedded ANYWHERE — including inside an
    entry's own text — as the section header, so crafted entry content
    could spoof section membership (2026-08-15 review, executed probe;
    entry text is LLM/pipeline-derived, not operator-only)."""
    m = re.search(rf"(?m)^##[ \t]+{re.escape(section)}[ \t]*$", text)
    if m is None:
        return None
    nxt = re.search(r"(?m)^## ", text[m.end():])
    end = m.end() + nxt.start() if nxt else len(text)
    return m.end(), end


def section_text(section: str) -> str:
    """The named section's text from the live playbook ('' when absent).

    Exists so a caller verifying that an append LANDED IN a section can
    scope its membership check (2026-08-13 review: the canon door checked
    its marker against the WHOLE playbook, so a same-marker entry in any
    other section false-verified a deduped append). Boundary contract is
    _section_span — shared with append_to_playbook, line-anchored."""
    text = load_playbook()
    span = _section_span(text, section)
    return "" if span is None else text[span[0]:span[1]]


def seed_playbook() -> None:
    """Create the initial playbook with seed content."""
    path = _playbook_path()
    if path.exists():
        return  # Don't overwrite
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _SEED_CONTENT.format(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    path.write_text(content, encoding="utf-8")
    log.info("playbook: seeded at %s", path)


# ---------------------------------------------------------------------------
# Entry parsing + ranked injection (swarm-review chunk 2)
# ---------------------------------------------------------------------------

# Trailing source attribution appended by append_to_playbook.
_ATTRIB_RE = re.compile(r"\s*\*\(from [^)]*\)\*\s*$")

# Sections shipped by _SEED_CONTENT; anything else is operator/system-grown.
_SEED_SECTIONS = frozenset({"Decomposition", "Execution", "Cost", "Quality"})

# Sections that stay in the file but never reach a prompt. "Signals" held
# proposed autonomous goals "for human review" — as a non-seed section they
# ranked as learned, so unreviewed proposals outranked the curated seed in
# every director and decompose call. The evolver no longer writes them there
# (they hold in `maro evolver --list` now); this keeps any that a live
# playbook still carries out of the injected window without deleting them.
_NO_INJECT_SECTIONS = frozenset({"Signals"})


def _entry_core(line: str) -> str:
    """Dedup key: bullet text without dash prefix, attribution, or case."""
    core = _ATTRIB_RE.sub("", line.strip())
    return core.lstrip("- ").strip().casefold()


# ---------------------------------------------------------------------------
# Alarms — entries that report a live reading, not a durable insight
# ---------------------------------------------------------------------------
#
# The operator surprise read (2026-08-02) found four frozen cost alarms from
# different eras all telling every run to "consider rolling back" changes that
# may long since have rolled back, and calibration warnings accreting as
# near-dups instead of updating in place. Nothing had ever exited the playbook
# because nothing could: an insight is durable, but a reading is only true
# while it keeps being taken, and the file had no way to tell them apart.
#
# An alarm carries its check's identity and the date it was last re-read.
# Re-reading replaces it; not re-reading expires it. Two entries accreted in
# the two days AFTER the hand-fix, which is what says this needed a mechanism
# and not another cleanup.
_ALARM_MARKER = "alarm"
_ALARM_RE = re.compile(
    r"·\s*" + _ALARM_MARKER + r"\s+(?P<key>[^\s)·]+)\s+@(?P<date>\d{4}-\d{2}-\d{2})"
)
_DEFAULT_ALARM_TTL_DAYS = 14


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _stamp_updated(text: str) -> str:
    """Refresh the document's Last-updated line, if it has one."""
    if "*Last updated:" not in text:
        return text
    return re.sub(r"\*Last updated:.*\*", f"*Last updated: {_today()}*", text)


def alarm_key(line: str) -> str:
    """The check this line reports on, "" if it isn't an alarm."""
    m = _ALARM_RE.search(line)
    return m.group("key") if m else ""


def _replace_alarm(text: str, key: str, new_line: str) -> tuple:
    """Swap the existing entry for `key` in place. Returns (replaced?, text)."""
    out = []
    replaced = False
    for line in text.split("\n"):
        if not replaced and alarm_key(line) == key:
            out.append(new_line)
            replaced = True
        else:
            out.append(line)
    return replaced, "\n".join(out)


def _expire_text(text: str, max_age_days: int) -> tuple:
    """(text without expired alarms, [(key, line), …]). Pure — no I/O."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    kept, dropped = [], []
    for line in text.split("\n"):
        m = _ALARM_RE.search(line)
        if m:
            try:
                stamped = datetime.strptime(m.group("date"), "%Y-%m-%d").replace(
                    tzinfo=timezone.utc)
            except ValueError:
                stamped = None  # unreadable stamp → keep, don't guess
            if stamped is not None and stamped < cutoff:
                dropped.append((m.group("key"), line))
                continue
        kept.append(line)
    return "\n".join(kept), dropped


def expire_stale_alarms(max_age_days: int = _DEFAULT_ALARM_TTL_DAYS) -> int:
    """Drop alarms whose check has stopped re-firing. Returns how many.

    An alarm is only as true as its last reading. When the condition clears,
    the scanner simply stops emitting it — there is no "resolved" event to
    listen for — so silence has to be what retires it. Archives before
    rewriting, like every other playbook mutation: the entry leaves the
    injected context, not the record.
    """
    path = _playbook_path()
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0

    updated, dropped = _expire_text(text, max_age_days)
    if not dropped:
        return 0
    if archive_playbook(text, reason="alarm-expiry") is None:
        return 0  # can't restore → don't rewrite (same rule as curation)

    from file_lock import atomic_write
    atomic_write(path, _stamp_updated(updated))
    for key, line in dropped:
        log.info("playbook: alarm %s expired (no reading in %dd): %s",
                 key, max_age_days, line[:80])
    return len(dropped)


def parse_entries(text: str) -> List[dict]:
    """Parse playbook bullets into entries with section + provenance.

    An entry is *learned* if it carries a source attribution or lives in
    a non-seed section (evolver output and operator additions); bare
    bullets inside seed sections are seed content.
    """
    entries: List[dict] = []
    section = ""
    for pos, line in enumerate(text.split("\n")):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.lstrip().startswith("- "):
            continue
        core = _entry_core(line)
        if not core:
            continue
        learned = bool(_ATTRIB_RE.search(line)) or (
            bool(section) and section not in _SEED_SECTIONS)
        entries.append({
            "section": section or "Notes",
            "line": line.strip(),
            "core": core,
            "learned": learned,
            "position": pos,
        })
    return entries


def inject_playbook(*, max_chars: int = 800) -> str:
    """Ranked playbook selection for context injection.

    Budget note (caps sweep review 2026-08-21): with recall.py's redundant
    override removed, this default is the SINGLE owner of the playbook
    window — and 800 is unmeasured (the V6 battery showed the seed alone
    overflowed it; the live playbook runs ~2.6k chars). Selection is ranked,
    so 800 keeps the top entries rather than silently cutting mid-entry,
    but the value wants its own distribution pass — BACKLOG'd under the
    truncation-audit arc. Do not re-add call-site literals; re-measure HERE.

    Priority: learned entries first (newest first — file position is
    append order), then seed entries in file order; exact duplicates
    (normalized core) dropped. Fills the budget greedily and renders
    grouped by section for readability. Replaces the head-window scheme
    whose fixed cursor let anything above the fold starve everything
    below it (the injection-horizon bug, confirmed 2026-07-21).
    """
    text = load_playbook()
    if not text:
        return ""

    entries = [e for e in parse_entries(text)
               if e["section"] not in _NO_INJECT_SECTIONS]
    if not entries:
        return ""

    learned = [e for e in entries if e["learned"]]
    learned.reverse()  # newest (last-appended) first
    seed = [e for e in entries if not e["learned"]]
    ranked = learned + seed

    # Dedup in rank order — the highest-ranked copy survives: a learned
    # entry beats a seed line with the same core, a newer learned entry
    # beats an older one (chunk-2 review F3: first-occurrence-wins
    # discarded the attributed copy).
    seen: set = set()
    unique: List[dict] = []
    for e in ranked:
        if e["core"] in seen:
            continue
        seen.add(e["core"])
        unique.append(e)

    # Greedy fill: budget covers EVERYTHING emitted — top header, section
    # headers (once each), bullet lines — so len(result) <= max_chars is a
    # real contract (chunk-2 review F4).
    top = "## Operational Playbook"
    selected: List[dict] = []
    included_sections: set = set()
    chars = len(top) + 1
    for e in unique:
        cost = len(e["line"]) + 1
        if e["section"] not in included_sections:
            cost += len(e["section"]) + 4  # "## <section>\n"
        if chars + cost > max_chars:
            continue
        selected.append(e)
        included_sections.add(e["section"])
        chars += cost

    if not selected:
        return ""

    # Render: sections in file order (stable document shape); bullets
    # within a section in rank order — newest learned first — so the
    # ranking is visible to the consumer, not just to selection
    # (chunk-2 review F6).
    out_lines: List[str] = []
    for sec in dict.fromkeys(e["section"] for e in sorted(selected, key=lambda x: x["position"])):
        out_lines.append(f"## {sec}")
        out_lines.extend(e["line"] for e in selected if e["section"] == sec)
    return top + "\n" + "\n".join(out_lines)


def append_to_playbook(
    entry: str,
    *,
    section: str = "Learned",
    source: str = "",
    key: str = "",
) -> None:
    """Append an operational insight to the playbook.

    Called by the evolver when a suggestion is applied, or by graduation
    when a standing rule is promoted. The entry is added under the specified
    section header.

    Args:
        entry: The insight text (one line, starts with "- ").
        section: Which section to append under (created if missing).
        source: Where this insight came from (e.g., "evolver:suggestion-id").
        key: Marks this entry as a re-readable ALARM, identified by the check
            it reports (e.g. "calibration:observation"). A later reading of
            the same check replaces this entry in place instead of appending
            beside it, and the entry expires if the check stops re-firing
            (see `expire_stale_alarms`). Without a key an entry is a durable
            insight: appended once, never auto-removed.
    """
    # Validate entry — reject empty or whitespace-only entries
    entry = (entry or "").strip()
    if not entry:
        log.warning("playbook: rejected empty entry (section=%s, source=%s)", section, source)
        return
    # An entry is ONE playbook line by contract (see docstring). Collapse
    # embedded newlines instead of writing raw lines into the file: a
    # multiline entry (or source/key marker) could otherwise land a
    # `## <section>` header line of its own and spoof section membership
    # for every later line-anchored read (2026-08-15 review, executed
    # probe — entry text is LLM/pipeline-derived, not operator-only).
    entry = " ".join(entry.split())
    source = " ".join(str(source or "").split())
    key = " ".join(str(key or "").split())
    if len(entry) > 500:
        entry = entry[:500] + "…"

    from file_lock import locked_write

    path = _playbook_path()
    if not path.exists():
        seed_playbook()

    entry_line = None  # set under lock if we actually append

    with locked_write(path):
        text = path.read_text(encoding="utf-8")
        entry_line = entry if entry.startswith("- ") else f"- {entry}"

        # Add source attribution if provided
        if source or key:
            _bits = [f"from {source}"] if source else []
            if key:
                _bits.append(f"{_ALARM_MARKER} {key} @{_today()}")
            entry_line += " *(" + " · ".join(_bits) + ")*"

        if key:
            # Same check, newer reading: replace in place. Keeping the original
            # position is deliberate — an alarm that keeps re-firing shouldn't
            # leapfrog the rest of the playbook every time it fires, and this is
            # what the 2026-08-02 operator rewrite did by hand when it collapsed
            # three calibration near-dups into one trend-carrying entry.
            replaced, updated_text = _replace_alarm(text, key, entry_line)
            if replaced:
                from file_lock import atomic_write
                atomic_write(path, _stamp_updated(updated_text))
                log.info("playbook: alarm %s re-read: %s", key, entry_line[:80])
                return

        # Dedup: skip if the core entry text already exists in the playbook
        _core = entry.lstrip("- ").strip()
        if _core and _core in text:
            log.debug("playbook: skipping duplicate entry: %s", _core[:80])
            return

        section_header = f"## {section}"
        # Line-anchored boundary via the SHARED helper (2026-08-15 review:
        # the substring lookup here was the footgun section_text originally
        # copied — an embedded "## <section>" inside entry text matched as
        # the header; one implementation means one contract).
        span = _section_span(text, section)

        if span is not None:
            header_end, section_end = span
            if section_end == len(text):
                # Last section: insert before the "Last updated" stamp.
                last_updated = text.find("\n*Last updated:", header_end)
                if last_updated >= 0:
                    section_end = last_updated
            updated = (
                text[:header_end] +
                text[header_end:section_end].rstrip() +
                "\n" + entry_line + "\n" +
                text[section_end:]
            )
        else:
            # Create new section before "Last updated" or at end
            last_updated = text.find("\n*Last updated:")
            if last_updated >= 0:
                updated = (
                    text[:last_updated].rstrip() + "\n\n" +
                    section_header + "\n\n" + entry_line + "\n" +
                    text[last_updated:]
                )
            else:
                updated = text.rstrip() + f"\n\n{section_header}\n\n{entry_line}\n"

        updated = _stamp_updated(updated)

        from file_lock import atomic_write
        atomic_write(path, updated)
        log.info("playbook: appended to [%s]: %s", section, entry_line[:80])

    # Captain's log (outside lock — doesn't need file exclusivity)
    if entry_line:
        try:
            from captains_log import log_event, PLAYBOOK_UPDATED
            log_event(
                event_type=PLAYBOOK_UPDATED,
                subject=section,
                summary=entry_line[:200],
                context={"source": source, "section": section},
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Curation verb — the dream-cycle rewrite pass (swarm-review chunk 2)
# ---------------------------------------------------------------------------

def _history_dir() -> Path:
    return _playbook_path().parent / "playbook_history"


def archive_playbook(text: str, *, reason: str = "curation") -> Optional[Path]:
    """Append-only archive of a playbook version. Never deletes.

    Data-retention rule: learning data is never destroyed — every rewrite
    preserves the pre-rewrite version here first.
    """
    try:
        d = _history_dir()
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        p = d / f"playbook-{ts}-{reason}.md"
        # Never clobber an existing archive (same-second rewrites).
        n = 0
        while p.exists():
            n += 1
            p = d / f"playbook-{ts}-{reason}-{n}.md"
        p.write_text(text, encoding="utf-8")
        return p
    except Exception as exc:
        log.warning("playbook: archive failed (curation aborts): %s", exc)
        return None


def _dedup_text(text: str) -> tuple:
    """Drop exact-duplicate bullets (normalized core), keep first occurrence.

    Non-bullet lines pass through untouched. Returns (new_text, removed).
    """
    seen: set = set()
    out: List[str] = []
    removed = 0
    for line in text.split("\n"):
        if line.lstrip().startswith("- "):
            core = _entry_core(line)
            if core and core in seen:
                removed += 1
                continue
            if core:
                seen.add(core)
        out.append(line)
    return "\n".join(out), removed


_COMPRESS_PROMPT = """\
You maintain an autonomous orchestration system's operational playbook (markdown).
Rewrite it TIGHTER: merge near-duplicate bullets, trim verbosity, keep it opinionated and actionable.

Hard rules:
- Keep every `## Section` header that exists in the input.
- Keep every source attribution `*(from ...)*` verbatim (a merged bullet carries all its sources).
- Do NOT invent advice that is not in the input. Do NOT drop factual content.
- Return ONLY the full rewritten markdown document, no commentary.

Playbook:
{text}
"""


def _valid_compression(old: str, new: str) -> bool:
    """Reject an LLM rewrite that lost structure, attributions, or grew.

    Structural checks, not substring sniffs (chunk-2 review F2): header
    LINES must survive exactly and occurrence-counted (`## Cost` is not
    preserved by `## Costly`, a duplicated section may not collapse);
    every `*(from ...)*` attribution must survive occurrence-counted;
    the bullet floor rounds UP (3 bullets require 2, never 1).
    """
    import math
    from collections import Counter

    new = (new or "").strip()
    if not new:
        return False
    if len(new) > len(old) * 1.1:
        return False
    old_headers = Counter(
        ln.strip() for ln in old.split("\n") if ln.strip().startswith("## "))
    new_headers = Counter(
        ln.strip() for ln in new.split("\n") if ln.strip().startswith("## "))
    if any(new_headers[h] < n for h, n in old_headers.items()):
        return False
    old_attribs = Counter(re.findall(r"\*\(from [^)]*\)\*", old))
    new_attribs = Counter(re.findall(r"\*\(from [^)]*\)\*", new))
    if any(new_attribs[a] < n for a, n in old_attribs.items()):
        return False
    old_bullets = sum(1 for ln in old.split("\n") if ln.lstrip().startswith("- "))
    new_bullets = sum(1 for ln in new.split("\n") if ln.lstrip().startswith("- "))
    return new_bullets >= max(1, math.ceil(old_bullets * 0.6))


def curate_playbook(*, force: bool = False, adapter=None) -> Optional[dict]:
    """Dedup and (when oversized) LLM-compress the playbook.

    Rides the consolidation dream cycle (knowledge_web.maybe_consolidate),
    so it runs at most once per consolidation interval. Two passes:

      1. Deterministic dedup — free, always (the session-17 guard applied
         retroactively: spam that predates the append-time guard, or
         re-accretes around it, gets collapsed here).
      2. LLM compression — only when the file exceeds
         ``playbook.curation_min_chars`` (default 4000): a curation-class
         call (CHEAP by decree — non-agentic). A rewrite that loses a
         section header, an attribution, >40% of bullets, or grows is
         rejected and the deterministic result kept.

    The pre-curation version is archived to playbook_history/ before any
    write; if archiving fails, curation aborts (never rewrite what you
    can't restore). Config gate: ``playbook.curation_enabled`` (default
    True). Returns a stats dict if the file changed, None otherwise.
    Never raises — callers sit on app exit paths.
    """
    try:
        try:
            from config import get as _cfg_get
        except Exception:
            _cfg_get = lambda k, d=None: d  # noqa: E731
        if not force and not _cfg_get("playbook.curation_enabled", True):
            return None

        path = _playbook_path()
        if not path.exists():
            return None

        from file_lock import atomic_write, locked_write

        # Snapshot under lock; compute OUTSIDE it. Holding the write lock
        # across an LLM round trip starves concurrent append_to_playbook
        # writers into FileLockTimeout (chunk-2 review F1) — external work
        # doesn't belong in a file critical section.
        with locked_write(path):
            original = path.read_text(encoding="utf-8")

        # Alarms first: an expired reading shouldn't survive into the
        # compression pass and get rephrased into something that reads durable.
        text, _expired = _expire_text(
            original, int(_cfg_get("playbook.alarm_ttl_days",
                                   _DEFAULT_ALARM_TTL_DAYS)))
        text, removed = _dedup_text(text)
        llm_compressed = False

        min_chars = int(_cfg_get("playbook.curation_min_chars", 4000))
        if len(text) > min_chars:
            try:
                if adapter is None:
                    from llm import build_adapter
                    from conductor import assign_model_by_role
                    adapter = build_adapter(
                        model=assign_model_by_role("cheap_worker"))
                from llm import LLMMessage
                resp = adapter.complete(
                    [LLMMessage("user", _COMPRESS_PROMPT.format(text=text))],
                    max_tokens=2000,
                    temperature=0.2,
                    no_tools=True,
                    purpose="playbook-curation",
                )
                candidate = (getattr(resp, "content", "") or "").strip()
                if _valid_compression(text, candidate):
                    text = candidate
                    llm_compressed = True
                else:
                    log.info("playbook: LLM compression rejected by "
                             "validation — keeping deterministic result")
            except Exception as exc:
                log.debug("playbook: LLM compression skipped: %s", exc)

        if text == original:
            return None

        # Compare-and-swap: if another writer appended while we were
        # computing, discard this pass rather than clobber their entry —
        # the next dream cycle re-curates from the fresh file.
        with locked_write(path):
            current = path.read_text(encoding="utf-8")
            if current != original:
                log.info("playbook: changed during curation — "
                         "skipping this cycle")
                return None

            archived = archive_playbook(original, reason="curation")
            if archived is None:
                return None  # can't restore → don't rewrite

            now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if "*Last updated:" in text:
                text = re.sub(r"\*Last updated:.*\*",
                              f"*Last updated: {now}*", text)
            atomic_write(path, text)

        stats = {
            "removed_duplicates": removed,
            "expired_alarms": [k for k, _ in _expired],
            "llm_compressed": llm_compressed,
            "archived": str(archived),
            "chars_before": len(original),
            "chars_after": len(text),
        }
        try:
            from captains_log import log_event, PLAYBOOK_CURATED
            log_event(
                event_type=PLAYBOOK_CURATED,
                subject="playbook",
                summary=(f"curated: -{removed} dup(s), "
                         f"llm={'yes' if llm_compressed else 'no'}, "
                         f"{len(original)}→{len(text)} chars"),
                context=stats,
            )
        except Exception:
            pass
        log.info("playbook: curated (-%d dups, llm=%s, %d→%d chars)",
                 removed, llm_compressed, len(original), len(text))
        return stats
    except Exception as exc:
        log.warning("playbook: curation failed (non-fatal): %s", exc)
        return None
