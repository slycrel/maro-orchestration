---
name: repl_reading
description: "Read large files/corpora WITHOUT loading them into context: size-check first, outline, targeted slices, verified quotes, explicit read budget. The counter to re-read churn."
roles_allowed: [worker, researcher]
triggers: [large file, large document, corpus, read the report, read the artifacts, analyze the file, long document, big file, ledger, transcript, prior research, existing artifacts, evidence trail]
---

## Why this skill exists

Loading whole files into context is the single biggest token waste in this
system's measured history: 3.45M input tokens to answer one question from
LOCAL files (run 9ddd53f1, 2026-08-02), 1–2M-token steps re-reading
artifacts the run itself wrote. Accuracy ALSO degrades as context fills —
the technique this skill encodes (treat the document as an environment you
query, never a blob you swallow; cf. Recursive Language Models) reports
large accuracy gains on long-document QA precisely because the model only
ever reasons over small, relevant, verified slices.

**The rule in one line: the document stays on disk; only slices you can
justify enter your context.**

**Measured correction (A/B run e0bbc289, 2026-08-02): on this executor,
token cost scales with TOOL TURNS, not bytes read** — every tool
round-trip re-sends the growing step conversation, so 94 tiny reads cost
MORE than a few fat ones (tokens rose 24% while content-per-read fell).
The protocol below is therefore stated in turns: **minimize round-trips
first, bytes second.** One orientation command for the whole corpus, at
most one locate + one read per file, and small files are read whole in a
single command — running wc + grep + sed against a 25-line file is three
turns where one `cat` was strictly cheaper. The slice discipline is for
files where a whole read genuinely can't be justified (thousands of
lines), and the honesty/verification discipline applies always.

## The protocol

**0. Budget first.** Before reading anything, set your read budget for the
task: a total line/char ceiling you will not cross without narrowing
(default: ~500 lines total per question answered; a genuinely broad
synthesis may take more, but SAY so in your step result). If you notice
you are about to exceed it, stop and narrow — do not push through.

**1. ONE orientation pass for the whole corpus — not per file:**
```bash
find dir/ -type f | xargs wc -l | sort -rn | head -30   # every file's size, one turn
```
Files under ~300 lines: read them WHOLE, in one command — batch several
small files into a single `cat small1.md small2.md small3.md` (with
`==>` headers via `head -c0` trick or `tail -n +1 f1 f2`). The slice
protocol below is ONLY for the files this listing shows are genuinely
large.

**2. Outline, don't read.** Get the document's skeleton and choose targets:
```bash
grep -n "^#\|^##\|^===\|^---$" file.md | head -40      # markdown/report structure
head -30 file.md; tail -20 file.md                      # frame: intro + conclusions
python3 -c "import json;d=json.load(open('f.json'));print(type(d),list(d)[:20] if isinstance(d,dict) else len(d))"  # JSON shape, not content
```

**3. Locate, then read the located region — in as few turns as possible.**
```bash
grep -n -A5 -B2 "the thing" bigfile.md | head -60       # locate AND read, one turn
sed -n '120,180p;300,340p' bigfile.md                   # multiple regions, one turn
```
Combine locate+read into one command when you can; batch multiple regions
into one sed. A separate turn per probe is the measured cost driver.
Grep locates; it does not comprehend. After locating, READ the region —
a hit's surrounding lines change its meaning (tables, negations,
"however" one line up). And know grep's limit: paraphrase, tables, and
cross-references defeat keywords. If two reasonable term-sets both miss,
that is NOT evidence of absence — fall back to reading the outline's most
relevant section in full.

**4. Verify every quote against its slice.** Before your step result
claims a document says X:
```bash
grep -Fn "exact quoted text" file.md    # -F: no regex surprises
```
No match → you paraphrased or hallucinated; re-read the slice and quote
what is actually there, with its line number. Quotes carry
`file.md:LINE` provenance in your results.

**5. Map-reduce when the surface is genuinely large.** For a corpus that
cannot be outlined into one target (many files, thousands of lines):
process per-file/per-section with steps 2–4, writing one bounded summary
per unit to a scratch file, then answer from the summaries. Never
concatenate the corpus into one read.

**6. Honesty stamps.** Your step result states: files touched, lines
actually read (approximate), and what you did NOT read. "Answered from
sections 2 and 5; did not read appendices" is a good sentence. A result
that reads as if you absorbed everything when you sliced is a fabrication.

## Anti-patterns (each observed in real runs)

- `cat whole_file.md` into context "to get oriented" — that is what the
  outline is for.
- Re-reading a file you already read this run because the earlier content
  scrolled out — write the 5-line summary you need to a scratch file the
  first time.
- Re-fetching/re-reading an artifact you WROTE earlier in this run —
  you know what's in it; consult your own step results.
- Grep-miss treated as absence — see step 3; and check the corpus's
  vintage covers what you're looking for before any absence claim.
- Quoting from memory of a slice read 10 steps ago — re-verify (step 4);
  verification is one grep, being wrong is a contested claim.
