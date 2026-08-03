"""Deterministic provenance guard — done != achieved check.

Extracted from handle.py (pure move, Tier 3 of docs/REFACTOR_PLAN.md):
a text-only verdict (the LLM judge, or the local validator) can't see whether
a claimed input was actually read or a claimed output actually landed. Live
find (shadow-eval n=42, 2026-06-24): "save the listing to
artifacts/skills-listing.txt" saved to a DIFFERENT path and narrated success —
local PASSed at conf 1.00, paid FAILed.

Three deterministic checks:
  * OUTPUT, dir-qualified  ("save … to artifacts/X")  → STRICT: must be at that
    exact path. The user said *where*; honor it.
  * OUTPUT, bare filename   ("save … to report.md")    → LENIENT: the basename
    must exist *somewhere* reasonable (location ambiguous → don't punish it).
  * INPUT, dir-qualified    ("read /nonexistent/X")     → STRICT: the input must
    exist (you can't read a file that isn't there). Remote (URLs) and transient
    (/tmp, scratchpad) inputs are skipped — can't/shouldn't verify them.
Same provenance-blindness root as the fabricated-input recovery guard; this is
the verdict-layer net for fabrication that reaches "done" without blocking.

A fourth, ADVISORY check lives at the bottom of this module: the same
fabrication shape aimed at MEMORY rather than at files (see the
"Memory-claim layer" banner). It marks; it never demotes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Tuple

log = logging.getLogger("maro.handle")

_OUTPUT_CLAIM_RE = re.compile(
    r"\b(?:sav\w*|writ\w*|creat\w*|output\w*|stor\w*|export\w*|generat\w*|dump\w*)\b"
    r"[^.\n]*?\b(?:to|into|at|as)\s+[`'\"(]?(?P<path>[^\s`'\")]+)",
    re.IGNORECASE,
)
_INPUT_CLAIM_RE = re.compile(
    r"\b(?:read|load|open|pars\w*|fetch|import|ingest)\w*\s+"
    r"(?:the\s+|a\s+|file\s+|from\s+|contents?\s+of\s+|in\s+)*"
    r"[`'\"(]?(?P<path>[^\s`'\")]+)",
    re.IGNORECASE,
)
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_REMOTE_PREFIXES = ("http://", "https://", "ftp://", "s3://", "gs://", "git@", "ssh://")
_TRANSIENT_SEGMENTS = ("/tmp/", "scratchpad", "/dev/", "/proc/", "/var/tmp/")


def _clean_path_token(tok: str) -> str:
    return tok.strip().strip("`'\"()").rstrip(".,;:")


def _path_shaped(tok: str) -> bool:
    """True when a slash-containing token plausibly names a filesystem path
    rather than prose that happens to contain a slash — "load range/index"
    (a tire spec) and "budget/mid-tier" both match the claim regexes' verb
    + token shape but name nothing on any disk (4d20b559: a goal's tire-spec
    phrase demoted a delivered run). Evidence accepted: an explicit anchor,
    a file extension on the final segment, or a first segment that exists as
    a directory under a provenance base. Prose slashes have none of these."""
    if tok.startswith(("/", "./", "../", "~/")):
        return True
    if _EXT_RE.search(tok):
        return True
    first = tok.split("/", 1)[0]
    if not first:
        return False
    for b in _output_provenance_bases():
        try:
            if (b / first).is_dir():
                return True
        except Exception:
            pass
    return False


def _claimed_output_paths(goal: str) -> List[str]:
    """Dir-qualified output paths the goal asks to be written (user said *where*)."""
    out: List[str] = []
    for m in _OUTPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        if ("/" in tok and tok not in ("/", "./", "../")
                and not tok.endswith("/") and _path_shaped(tok)):
            out.append(tok)
    return out


def _claimed_output_bare(goal: str) -> List[str]:
    """Bare output filenames (no dir, has an extension) — user said only *what*."""
    out: List[str] = []
    for m in _OUTPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        if "/" not in tok and _EXT_RE.search(tok):
            out.append(tok)
    return out


def _claimed_input_paths(goal: str) -> List[str]:
    """Dir-qualified, local, non-transient input paths the goal asks to read."""
    out: List[str] = []
    for m in _INPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        low = tok.lower()
        if low.startswith(_REMOTE_PREFIXES):
            continue                      # remote — can't cheaply verify
        if "/" not in tok:
            continue                      # bare name — ambiguous
        if any(seg in low for seg in _TRANSIENT_SEGMENTS):
            continue                      # may be gone by verdict time
        if not _path_shaped(tok):
            continue                      # prose slash ("load range/index")
        out.append(tok)
    return out


def _output_provenance_bases() -> List[Path]:
    """Candidate base dirs a relative path could legitimately resolve under.
    Generous on purpose — a false demotion is worse than a missed one."""
    bases: List[Path] = []
    for fn in (
        lambda: Path.cwd(),
        lambda: Path(__file__).resolve().parent.parent,
    ):
        try:
            bases.append(fn())
        except Exception:
            pass
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd:
            bases.append(Path(rd))
    except Exception:
        pass
    try:
        from config import workspace_root
        ws = Path(workspace_root())
        bases.extend([ws, ws / "output"])
    except Exception:
        pass
    return bases


def _exists_at_exact(rel: str, bases: List[Path]) -> bool:
    """True if a (possibly relative) path resolves to an existing file. For
    relative paths, also checks one level under workspace/projects/<slug>/."""
    p = Path(rel)
    if p.is_absolute():
        return p.exists()
    if any((b / rel).exists() for b in bases):
        return True
    try:
        from config import workspace_root
        ws_projects = Path(workspace_root()) / "projects"
        if ws_projects.is_dir():
            return any((d / rel).exists() for d in ws_projects.glob("*") if d.is_dir())
    except Exception:
        pass
    return False


def _bare_search_dirs() -> List[Path]:
    """Small, bounded landing spots to scan for a bare output basename."""
    dirs: List[Path] = []
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd:
            dirs.append(Path(rd))
    except Exception:
        pass
    try:
        from config import workspace_root
        ws = Path(workspace_root())
        dirs.extend([ws / "output", ws / "projects"])
    except Exception:
        pass
    return [d for d in dirs if d.is_dir()]


def _exists_bare_anywhere(name: str, bases: List[Path]) -> bool:
    """True if a bare basename exists under any base (direct) or any landing
    spot (one or two levels deep — where run/project/output files land)."""
    if any((b / name).exists() for b in bases):
        return True
    for d in _bare_search_dirs():
        try:
            if (d / name).exists():
                return True
            if any(d.glob(f"*/{name}")) or any(d.glob(f"*/*/{name}")):
                return True
        except Exception:
            pass
    return False


def _missing_claimed_outputs(goal: str) -> List[str]:
    """Dir-qualified output paths named in the goal that don't exist at that
    exact location. Empty = nothing claimed, or everything landed. Fails open."""
    claimed = _claimed_output_paths(goal)
    if not claimed:
        return []
    bases = _output_provenance_bases()
    return [rel for rel in claimed if not _exists_at_exact(rel, bases)]


def _missing_output_bare(goal: str) -> List[str]:
    """Bare output filenames whose basename exists nowhere reasonable (the
    output was never produced). Lenient: location is not part of the contract."""
    bare = _claimed_output_bare(goal)
    if not bare:
        return []
    bases = _output_provenance_bases()
    return [name for name in bare if not _exists_bare_anywhere(name, bases)]


def _missing_claimed_inputs(goal: str) -> List[str]:
    """Dir-qualified local input paths the goal asks to read that don't exist —
    you can't legitimately read a file that isn't there. Fails open."""
    claimed = _claimed_input_paths(goal)
    if not claimed:
        return []
    bases = _output_provenance_bases()
    return [rel for rel in claimed if not _exists_at_exact(rel, bases)]


# --- Tool-evidence layer ----------------------------------------------------
# The three checks above scan the GOAL text. This one scans the RESULT text for
# paths the run CLAIMS it wrote ("saved to X", "wrote report.md") and demotes
# unless that path exists AND was modified during this run's wall-clock window.
# The mtime gate is the actual evidence of a side effect: a pre-existing stale
# file with the right name does NOT prove the run wrote it. This is what catches
# fabrication when the GOAL named no path (the *claim* names it) and the n=42
# "narrated success, saved elsewhere/nowhere" case the text-only judge missed.
# Window is intentionally generous (buffer) — a missed fabrication is cheaper
# than a false demotion (fail-open).
# Residual it CANNOT catch (no execution transcript is available from `claude -p
# --output-format json` — only the final text): a run that fabricates a result
# with no file claim at all ("ran the tests: 142 passed" writing nothing). That
# needs tool-call evidence the backend doesn't expose. Documented, not solved.
_WINDOW_BUFFER_SECS = 120.0


def _run_window_start(elapsed_ms, wall_start: Optional[float] = None) -> Optional[float]:
    """Wall-clock instant before which a file mtime can't be evidence of THIS
    run. Prefer the run's recorded wall start — reconstructing it as
    now - elapsed - buffer is only valid when the check runs immediately at
    work end AND elapsed spans the whole run; run 123bf935 (2026-07-16,
    container-on day one) was falsely demoted when a slow post-loop closure
    pushed "now" ~8 min past loop end, sliding the reconstructed window past
    artifacts its own early steps had genuinely written. None (skip the
    gate) when neither anchor is known."""
    try:
        if wall_start and float(wall_start) > 0:
            return float(wall_start) - _WINDOW_BUFFER_SECS
        ems = float(elapsed_ms or 0)
        if ems <= 0:
            return None
        return time.time() - ems / 1000.0 - _WINDOW_BUFFER_SECS
    except Exception:
        return None


def _resolve_exact(rel: str, bases: List[Path]) -> List[Path]:
    """ALL existing candidates a (possibly relative) path could resolve to.

    Generic names (step_data.json, artifacts/step-N-output.txt) exist in many
    workspace projects; freshness must be judged across every candidate, not
    the first glob hit — run 75fe8b4e was falsely demoted to incomplete when
    its fresh output resolved to an older project's file of the same name.
    """
    # Glob-aware (2026-08-02, run 9d88acf2 false demotion): a step result
    # that honestly summarizes several writes as one pattern ("Saved
    # artifacts/OL*.json" — six real files on disk) used to get its glob
    # checked as a LITERAL filename, fail, and hand a FULL-trust
    # deterministic not-achieved to an honest run. A claim containing glob
    # metacharacters is satisfied by any matching file; freshness is still
    # judged per hit by the caller.
    if any(ch in rel for ch in "*?["):
        ghits: List[Path] = []
        for b in bases:
            try:
                ghits.extend(x for x in b.glob(rel) if x.exists())
            except (OSError, ValueError):
                pass
        try:
            from config import workspace_root
            ws_projects = Path(workspace_root()) / "projects"
            if ws_projects.is_dir():
                for d in ws_projects.glob("*"):
                    if d.is_dir():
                        try:
                            ghits.extend(x for x in d.glob(rel) if x.exists())
                        except (OSError, ValueError):
                            pass
        except Exception:
            pass
        return ghits

    p = Path(rel)
    if p.is_absolute():
        return [p] if p.exists() else []
    hits: List[Path] = []
    for b in bases:
        if (b / rel).exists():
            hits.append(b / rel)
    try:
        from config import workspace_root
        ws_projects = Path(workspace_root()) / "projects"
        if ws_projects.is_dir():
            for d in ws_projects.glob("*"):
                if d.is_dir() and (d / rel).exists():
                    hits.append(d / rel)
    except Exception:
        pass
    return hits


def _resolve_bare(name: str, bases: List[Path]) -> List[Path]:
    """ALL existing candidates for a bare output basename (see _resolve_exact)."""
    hits: List[Path] = []
    for b in bases:
        if (b / name).exists():
            hits.append(b / name)
    for d in _bare_search_dirs():
        try:
            if (d / name).exists():
                hits.append(d / name)
            hits.extend(d.glob(f"*/{name}"))
            hits.extend(d.glob(f"*/*/{name}"))
        except Exception:
            pass
    return hits


def _is_fresh(path: Path, window_start: float) -> bool:
    """True if the file was modified at/after the run window start. Can't stat →
    True (fail open — never punish on an inability to check)."""
    try:
        return path.stat().st_mtime >= window_start
    except Exception:
        return True


def _result_claimed_outputs(text: str) -> List[str]:
    """Output paths a result narration claims to have written — dir-qualified
    and bare — minus remote/transient (can't have been written locally now)."""
    out: List[str] = []
    for rel in _claimed_output_paths(text) + _claimed_output_bare(text):
        low = rel.lower()
        if low.startswith(_REMOTE_PREFIXES):
            continue
        if any(seg in low for seg in _TRANSIENT_SEGMENTS):
            continue
        out.append(rel)
    return out


def _missing_or_stale_result_outputs(result_text: str, window_start: float) -> List[str]:
    """Output paths the RESULT claims to have written that either don't exist or
    predate the run window (so the run did not actually write them). Fails open."""
    claimed = _result_claimed_outputs(result_text or "")
    if not claimed:
        return []
    bases = _output_provenance_bases()
    flagged: List[str] = []
    for rel in claimed:
        if "/" in rel and not rel.endswith("/"):
            candidates = _resolve_exact(rel, bases)
        else:
            candidates = _resolve_bare(rel, bases)
        if not candidates:
            flagged.append(f"{rel} (claimed written, not found)")
        elif not any(_is_fresh(c, window_start) for c in candidates):
            flagged.append(f"{rel} (claimed written, but predates this run)")
    return flagged


def _provenance_missing(goal: str, *, result_text: Optional[str] = None,
                        window_start: Optional[float] = None) -> List[str]:
    """Aggregate deterministic provenance failures, honoring config flags. Scans
    the GOAL (output/input claims) and, when result_text + window_start are
    given, the RESULT (tool-evidence: claimed-written paths must exist and be
    fresh). Empty = nothing to flag. Never raises (fails open)."""
    missing: List[str] = []
    try:
        from config import get as _cfg_get
        if _cfg_get("validate.output_provenance", True):
            missing.extend(_missing_claimed_outputs(goal))
            missing.extend(_missing_output_bare(goal))
        if _cfg_get("validate.input_provenance", True):
            missing.extend(_missing_claimed_inputs(goal))
        if (result_text and window_start is not None
                and _cfg_get("validate.result_provenance", True)):
            missing.extend(_missing_or_stale_result_outputs(result_text, window_start))
    except Exception as exc:
        log.debug("provenance check skipped: %s", exc)
    return list(dict.fromkeys(missing))  # dedup, preserve order


# --- Memory-claim layer (ADVISORY) ------------------------------------------
# Everything above verifies claims about FILES. This verifies claims about
# MEMORY, and of the two it is the one whose evidence is actually total: the
# durable stores are a handful of small JSONL ledgers, so "does a row matching
# this claim exist?" has a real answer rather than a search over plausible
# landing spots.
#
# Why it exists (live find, 2026-08-02): a run was handed a standing convention
# by a mid-run corrective interrupt, applied it correctly for the rest of that
# run, and then closed a step with "durable feedback memory already persisted
# and complete — no write required". Nothing had been written; the next run
# started blank. Same fabrication shape as "saved to artifacts/foo.md" with no
# file on disk — a confident completion claim about a write that never
# happened — except aimed where nothing was checking. It is also the most
# expensive place to fabricate, because the cost is invisible: it is paid later
# by a future run that silently never receives the knowledge.
#
# The asymmetry, inherited and taken seriously: the store lookup is
# deterministic and total (every row, no ranking, no LLM), the claim
# extraction is a regex over prose. EVERY observed error of the file
# guard came from the claim side (contested_by_closure, right below, records
# two from the same day). So the claim side here is tuned hard for precision —
# a claim counts only if it is in a completed tense, aimed at a durable-memory
# noun, free of hedge/reporting cues, AND names what it persisted. Prose that
# merely DISCUSSES memory is the standing false-positive population and is
# skipped by construction. Missing a real fabrication is the cheap error;
# telling an honest run it lied is not.
#
# No freshness gate here (deliberately unlike the file layer): "already
# persisted" is a legitimate claim shape, and the ledgers reinforce duplicates
# instead of appending them, so a matching row of ANY age is real evidence.
#
# Residual it CANNOT catch, documented rather than papered over: a claim that
# names no content ("memory is already up to date") is unverifiable — there is
# nothing to look up — so it is skipped. Verifying that class would mean
# matching the run's operator-interrupt prose against stored wording by fuzzy
# overlap, which is exactly the imprecise-claim-side machinery that produced
# the file guard's false demotions.

_MAX_RESULT_CHARS = 200_000      # bound the scan; step results can be huge
_MAX_CLAIMS = 5                  # findings are evidence, not a transcript
_MAX_SUBJECT_CHARS = 400
_MIN_SUBJECT_TOKENS = 4          # below this a subject is too generic to verify
_SUBJECT_COVERAGE = 0.8          # share of a claim's words one row must carry
_OBJECT_WINDOW = 160             # chars after the verb the memory noun may sit in
_MAX_STORE_ROWS = 20_000

# Completed tense ONLY. An infinitive or gerund ("persist the rule to memory",
# "persisting …") is a plan or an instruction, not a claim that it happened —
# and keeping them out also means no imperative can ever match.
_MEM_VERB_RE = re.compile(
    r"\b(?:persisted|saved|recorded|stored|wrote|written|added|appended"
    r"|captured|committed|logged|updated|memorized)\b",
    re.IGNORECASE,
)

# The claim has to be aimed at DURABLE memory. Bare "in memory" is excluded on
# purpose: it is the RAM idiom ("held in memory", "loaded into memory") and
# reading it as a persistence claim would flag ordinary engineering prose.
_MEM_OBJECT_RE = re.compile(
    r"(?:\b(?:durable|long[-\s]?term|persistent|permanent|standing|feedback)"
    r"\s+(?:\w+\s+){0,2}memor(?:y|ies)\b"
    r"|\bmemor(?:y|ies)\s+(?:store|ledger|system|bank|layer)\b"
    r"|\bto\s+(?:the\s+|my\s+|our\s+)?memory\b"
    r"|\blessons?\.jsonl\b"
    r"|\blessons?\s+(?:ledger|store)\b"
    r"|\bknowledge\s+(?:base|web|store|node)\b"
    r"|\bdecision\s+journal\b"
    r"|\bfor\s+future\s+(?:runs|sessions|agents|invocations))",
    re.IGNORECASE,
)

# Cues BEFORE the verb that mean this is not an assertion that it happened:
# negated, conditional, planned, or owed. Searched only in the sentence prefix,
# because the incident's own sentence carried "no write required" AFTER the
# verb and that phrasing is still a claim about the state of memory.
_CLAIM_HEDGE_RE = re.compile(
    r"\b(?:not|never|\w*n't|without|fail(?:s|ed|ing|ure)?|unable|cannot"
    r"|won't|should|would|could|will|shall|must|may|might|plan(?:s|ned|ning)?"
    r"|intend\w*|need(?:s|ed)?|if|whether|unless|until|before|once|when"
    r"|instead|rather|assum\w*|expect\w*)\b",
    re.IGNORECASE,
)

# Cues ANYWHERE in the sentence that mark prose ABOUT a claim rather than a
# claim — the forensic/self-inspection class that false-demoted ea4ebe4a on
# the file side. A run whose whole job is explaining that some other run lied
# about memory must not be read as lying about memory.
_CLAIM_REPORT_RE = re.compile(
    r"\b(?:claim\w*|alleg\w*|purport\w*|suppos\w*|fabricat\w*|hallucinat\w*"
    r"|pretend\w*|falsely|verif\w*|audit\w*)\b",
    re.IGNORECASE,
)

_QUOTED_RE = re.compile(r"[`\"“‘']([^`\"”’']{8,400})[`\"”’']")
_WORD_RE = re.compile(r"[a-z0-9]+")
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an|this|that|these|those|our|my|its)\s+", re.I)
_TRAILING_PREP_RE = re.compile(r"\s+(?:to|in|into|for|and|so|as|with|on|at|from|of)$", re.I)

# Function words only. Words that carry the MEANING of a convention
# ("always", "never", "must") stay in — dropping them would let "always use X"
# match a stored "never use X".
_SUBJECT_STOPWORDS = frozenset(
    "a an and the to of in on for with that this it is are was were be been "
    "being as at by or from into onto".split()
)


def _normalize_text(text: str) -> str:
    """Lowercase, punctuation → space, whitespace collapsed. Both sides of the
    lookup go through this, so backticks/hyphens/casing can't split a match."""
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _stem(token: str) -> str:
    """Plural-s only. "step summaries" and "step summary" are the same rule
    written twice, and a lookup that can't see that accuses a run that did
    persist it. Verb suffixes are deliberately NOT stripped — crude verb
    stemming invents roots that agree with nothing ("cited" → "cit")."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _content_tokens(text: str) -> List[str]:
    return [_stem(t) for t in _WORD_RE.findall((text or "").lower())
            if t not in _SUBJECT_STOPWORDS]


def _claim_sentences(text: str) -> Iterator[str]:
    """Sentences of a result narration, bullets included. Line-first so a
    bulleted step summary (no terminal punctuation) still yields one unit."""
    for line in (text or "")[:_MAX_RESULT_CHARS].splitlines():
        line = line.strip().lstrip("-*• \t")
        if not line:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            sentence = sentence.strip()
            if sentence:
                yield sentence


def _trim_subject(text: str) -> str:
    """Normalize a candidate subject and strip the claim's own boilerplate.

    Cutting at the memory noun matters: "recorded the convention that artifacts
    land in artifacts/ to durable memory" would otherwise carry "to durable
    memory" into the lookup, and those extra tokens make a MATCH less likely —
    i.e. they push toward accusing a run that did persist the rule. Every trim
    here moves in the forgiving direction on purpose.
    """
    s = " ".join((text or "").split())
    m = _MEM_OBJECT_RE.search(s)
    if m:
        s = s[:m.start()]
    s = s.strip(" \t`'\"“”‘’()[]{}.,;:!?—–-")
    s = _LEADING_ARTICLE_RE.sub("", s)
    s = _TRAILING_PREP_RE.sub("", s)
    return s[:_MAX_SUBJECT_CHARS]


def _subject_candidates(sentence: str, verb_end: int) -> Iterator[str]:
    """Ways a claim can name its own content, most explicit first: a quoted
    span (anywhere — passive voice puts it before the verb), a colon lead-in,
    then a `that`-clause."""
    tail = sentence[verb_end:]
    for m in _QUOTED_RE.finditer(sentence):
        yield m.group(1)
    if ":" in tail:
        yield tail.split(":", 1)[1]
    m = re.search(r"\bthat\b", tail, re.IGNORECASE)
    if m:
        yield tail[m.end():]


def _claim_subject(sentence: str, verb_end: int) -> str:
    """What a persistence claim says it persisted, or "" when it says nothing.

    This is the precision lever and the reason the guard can be trusted: a
    claim that never names its content is not checkable, so it is not checked.
    A subject under _MIN_SUBJECT_TOKENS content words is dropped for the same
    reason — "the rule" matches everything or nothing, arbitrarily.
    """
    for cand in _subject_candidates(sentence, verb_end):
        subject = _trim_subject(cand)
        if len(_content_tokens(subject)) >= _MIN_SUBJECT_TOKENS:
            return subject
    return ""


def _memory_claim_subjects(text: str) -> List[str]:
    """Contents a result narration claims to have persisted to durable memory.

    Four gates, ALL required — the conservatism is the feature (see banner):
      1. completed-tense persistence verb;
      2. a durable-memory noun after it, in the same sentence;
      3. no hedge cue in front of it and no reporting cue in the sentence;
      4. the claim names what it persisted.
    """
    subjects: List[str] = []
    for sentence in _claim_sentences(text):
        if sentence.endswith("?") or _CLAIM_REPORT_RE.search(sentence):
            continue
        for vm in _MEM_VERB_RE.finditer(sentence):
            if _CLAIM_HEDGE_RE.search(sentence[:vm.start()]):
                continue
            if not _MEM_OBJECT_RE.search(sentence, vm.end(), vm.end() + _OBJECT_WINDOW):
                continue
            subject = _claim_subject(sentence, vm.end())
            if subject:
                subjects.append(subject)
            break  # one claim per sentence is plenty of evidence
    return list(dict.fromkeys(subjects))[:_MAX_CLAIMS]


def _memory_store_paths() -> List[Tuple[Path, Tuple[str, ...]]]:
    """(jsonl path, text fields) for every durable store a claim can land in.

    Paths come from the OWNING module's own helper, so a store that moves
    takes this with it instead of leaving a silently-empty scan behind. Each
    is independently fail-open: a store we can't resolve contributes no rows
    (and, via `_memory_store_rows`, no accusation either).
    """
    stores: List[Tuple[Path, Tuple[str, ...]]] = []
    try:
        from memory_ledger import _lessons_path
        stores.append((_lessons_path(), ("lesson",)))
    except Exception:
        pass
    try:
        from knowledge_web import MemoryTier, _tiered_lessons_path
        for tier in (MemoryTier.MEDIUM, MemoryTier.LONG):
            stores.append((_tiered_lessons_path(tier), ("lesson",)))
        from knowledge_web import _knowledge_nodes_path
        stores.append((_knowledge_nodes_path(), ("title", "description")))
    except Exception:
        pass
    try:
        from knowledge_lens import _decisions_path
        stores.append((_decisions_path(), ("decision", "rationale")))
    except Exception:
        pass
    try:
        from rules import _rules_path
        stores.append((_rules_path(), ("name", "description")))
    except Exception:
        pass
    return stores


def _memory_store_rows() -> Tuple[List[Tuple[str, FrozenSet[str]]], bool]:
    """((normalized text, token set) per row, saw_a_store).

    `saw_a_store` is the fail-open switch: when not one store file exists we
    cannot tell "memory is empty" from "memory is somewhere else", and an
    accusation on that footing would be exactly the unverified confidence this
    guard exists to catch.
    """
    rows: List[Tuple[str, FrozenSet[str]]] = []
    saw_store = False
    for path, fields in _memory_store_paths():
        try:
            if not path.is_file():
                continue
            saw_store = True
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                text = " ".join(str(row.get(f, "") or "") for f in fields)
                norm = _normalize_text(text)
                if norm:
                    rows.append((norm, frozenset(_content_tokens(norm))))
                if len(rows) >= _MAX_STORE_ROWS:
                    return rows, saw_store
        except OSError:
            continue
    return rows, saw_store


def _memory_row_exists(subject: str,
                       rows: List[Tuple[str, FrozenSet[str]]]) -> bool:
    """True when some stored row carries this claim's content.

    Two deterministic predicates, both deliberately generous: normalized
    containment (the exact case), or one row holding at least
    _SUBJECT_COVERAGE of the claim's content words. No ranking, no scoring
    across rows, no LLM — the answer is reproducible, and the only direction
    the looseness runs is toward believing the run.

    The coverage tolerance is not a tuning knob, it is the false-accusation
    budget: a run that persisted "always cite the run id in step summaries"
    and narrated it as "always cite the ORIGINATING run id …" wrote the rule,
    and demanding every word would call that run a liar. It stays high enough
    that a genuinely different rule (2026-08-02: the incident's convention vs
    a same-shaped rule about file paths, 5/7 words shared) is still flagged.
    """
    subj_norm = _normalize_text(subject)
    subj_tokens = frozenset(_content_tokens(subject))
    if not subj_norm or not subj_tokens:
        return True  # nothing to look up — never accuse on an empty subject
    need = len(subj_tokens) * _SUBJECT_COVERAGE
    for row_norm, row_tokens in rows:
        if subj_norm in row_norm or len(subj_tokens & row_tokens) >= need:
            return True
    return False


def _memory_provenance_enabled() -> bool:
    """Killswitch (default ON). config.get hands back raw YAML nodes, so a
    quoted "false" arrives as a truthy string — normalize it the way the
    quality-gate killswitches do, or the killswitch can't kill."""
    from config import get as _cfg_get
    val = _cfg_get("validate.memory_provenance", True)
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(val)


def memory_provenance_unverified(result_text: str) -> List[str]:
    """Memory-persistence claims in a result narration with no matching row.

    ADVISORY, by decree (Jeremy, 2026-08-02: "it's less about being correct up
    front and more about how well you recover when you're wrong"). Callers
    surface this as evidence — a metadata mark, a log line — and must NOT flip
    a verdict on it. Two runs were silently flipped to failed the same day by a
    layer that admitted no overrule; a guard whose claim side is a regex over
    prose has not earned that standing, however exact its lookup half is.

    Empty = no claim found, everything claimed is in the store, the store is
    unreachable, or the flag is off. Never raises (fails open).
    """
    try:
        if not _memory_provenance_enabled():
            return []
        subjects = _memory_claim_subjects(result_text or "")
        if not subjects:
            return []
        rows, saw_store = _memory_store_rows()
        if not saw_store:
            return []
        return [f"{s[:160]} (claimed persisted to memory, no matching row)"
                for s in subjects if not _memory_row_exists(s, rows)]
    except Exception as exc:
        log.debug("memory provenance check skipped: %s", exc)
        return []


# A claim that memory needs NO write is the founding incident's exact shape:
# run 9c8d0a43 closed a step with "durable feedback memory already persisted
# and complete — no write required", and the rule it was supposed to persist
# was never stored. It names no content, so there is nothing to look up and
# `memory_provenance_unverified` correctly skips it.
#
# The response is deliberately NOT a smarter accusation. Deciding whether
# "already complete" is true would mean fuzzy-matching operator prose against
# stored wording — exactly the imprecise claim-side machinery that produced two
# FULL-trust false demotions on 2026-08-02. Instead the claim is recorded as
# UNVERIFIABLE: a distinct, weaker category that asserts nothing about whether
# the run lied. Same discipline as the run-readout residue counter — the pile
# you cannot classify has to be countable rather than silent, because an
# unrecognized failure mode is otherwise indistinguishable from success.
_MEM_SUFFICIENCY_RE = re.compile(
    r"\b(?:already\s+(?:persist\w*|sav\w*|stor\w*|record\w*|captur\w*|"
    r"complete|current|up[-\s]?to[-\s]?date|present|there)"
    r"|no\s+(?:write|update|change|action|persistence)\s+(?:required|needed)"
    r"|nothing\s+(?:to|further\s+to)\s+(?:persist|save|store|record|write)"
    r"|remains?\s+(?:accurate|current|valid))\b",
    re.IGNORECASE,
)


def memory_claims_unverifiable(result_text: str) -> List[str]:
    """Assertions that durable memory needs no write, naming nothing checkable.

    NOT an accusation and never a verdict input — the run may be perfectly
    right. This exists so the founding incident's shape stops being invisible:
    a step can currently declare memory complete, be wrong, and leave no trace,
    with the bill paid by a future run that never receives the knowledge.

    Deliberately separate from `memory_provenance_unverified`: that one says
    "you claimed X and X is not in any store", which is checkable. This one
    says "you claimed nothing needed saving and did not say about what", which
    is not. Collapsing the two would launder an unverifiable claim into an
    accusation. Never raises (fails open).
    """
    try:
        if not _memory_provenance_enabled():
            return []
        found: List[str] = []
        for sentence in _claim_sentences(result_text or ""):
            if sentence.endswith("?") or _CLAIM_REPORT_RE.search(sentence):
                continue
            if not _MEM_SUFFICIENCY_RE.search(sentence):
                continue
            if not _MEM_OBJECT_RE.search(sentence):
                continue
            # A sufficiency claim that DOES name content is checkable, so it
            # belongs to the accusing guard, not here — never both.
            if _memory_claim_subjects(sentence):
                continue
            found.append(f"{' '.join(sentence.split())[:160]} "
                         f"(asserts memory needs no write; names nothing checkable)")
        return list(dict.fromkeys(found))[:_MAX_CLAIMS]
    except Exception as exc:
        log.debug("memory sufficiency check skipped: %s", exc)
        return []


def contested_by_closure(closure: Any, missing: List[str]) -> Dict[str, Any]:
    """Metadata fields recording that closure DISAGREED with a provenance demotion.

    Purely additive: this does not change who wins. The provenance guard still
    demotes, because it exists to catch the false_pass a text-only verdict
    can't see (shadow-eval n=42, 2026-06-24). What it changes is that the
    disagreement stops being invisible.

    Why (two live false demotions, both at high closure confidence):

    * `9d88acf2` (2026-08-02) — an honest run summarized six real fetches as
      "saved artifacts/OL*.json"; the guard checked the glob as a literal
      filename. Closure had independently judged complete=True @ 0.75. The
      demotion stood, and three lessons were minted on the false premise.
    * `ea4ebe4a` (2026-08-02) — a forensic run whose whole deliverable was
      explaining that `artifacts/comm-examples.md` had NEVER been written was
      flagged for "claiming" that very file, plus `metadata.json/run_card.json`
      (prose split on a slash). Closure: complete=True @ 0.92, **5/5 checks**.

    The pattern is not that the filesystem check is unreliable — that part is
    exact. It is that the guard conflates two very different confidences:
    *"does this path exist?"* (deterministic) and *"did the run CLAIM to write
    this path?"* (a regex over prose). It inherits FULL trust from the first
    while every observed error comes from the second. Goals that legitimately
    DISCUSS file paths — forensics, self-inspection, code review, anything
    reporting on another run — are the standing false-positive population, and
    no amount of matcher tuning removes that class.

    So, per the recovery-over-correctness posture (Jeremy, 2026-08-02): a
    verdict layer that admits no overrule must earn that standing. This is the
    smallest honest step — record the conflict so a human or a later pass can
    find it, instead of a silent flip that only surfaces when someone goes
    digging weeks later.
    """
    fields: Dict[str, Any] = {}
    if closure is None or not getattr(closure, "complete", False):
        return fields
    fields["goal_verdict_contested"] = True
    fields["goal_verdict_contested_by"] = "closure"
    fields["closure_complete"] = True
    try:
        fields["closure_confidence"] = float(getattr(closure, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        fields["closure_confidence"] = 0.0
    fields["provenance_missing_claims"] = list(missing or [])[:10]
    return fields
