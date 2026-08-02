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

## The protocol

**0. Budget first.** Before reading anything, set your read budget for the
task: a total line/char ceiling you will not cross without narrowing
(default: ~500 lines total per question answered; a genuinely broad
synthesis may take more, but SAY so in your step result). If you notice
you are about to exceed it, stop and narrow — do not push through.

**1. Size-check before any read.** Never open a file blind:
```bash
wc -lc path/file.md                  # lines + bytes
ls -la dir/ | head -20               # corpus shape
```
Under ~200 lines? Just read it — this protocol is for everything bigger.

**2. Outline, don't read.** Get the document's skeleton and choose targets:
```bash
grep -n "^#\|^##\|^===\|^---$" file.md | head -40      # markdown/report structure
head -30 file.md; tail -20 file.md                      # frame: intro + conclusions
python3 -c "import json;d=json.load(open('f.json'));print(type(d),list(d)[:20] if isinstance(d,dict) else len(d))"  # JSON shape, not content
```

**3. Locate, then read the located region — never the whole file.**
```bash
grep -n "the thing" file.md                             # find line numbers
sed -n '120,160p' file.md                               # read THAT region ±context
grep -n -A3 -B3 "term" file.md | head -40               # bounded context read
```
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
