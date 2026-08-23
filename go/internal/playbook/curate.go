package playbook

import (
	"context"
	"fmt"
	"math"
	"os"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Curation: playbook.py's curate_playbook and the two helpers only it
// uses. Where Append edits one entry, this rewrites the whole document —
// so every guard here exists because the failure it prevents destroys an
// operator's file rather than adding an odd line to it.

// On the two numeric gates in validCompression, recorded here because the
// safe-looking rewrite of each is the wrong one. Both are FLOAT comparisons and must stay float: `len(new) >
// len(old) * 1.1` and `new >= max(1, ceil(old * 0.6))`. Swept 0..20000
// against CPython — `math.Ceil(float64(n)*0.6)` agrees with the exact
// rational ceil at every n, and the growth gate agrees with `10*new >
// 11*old` at every pair — so the integer reformulations happen to be
// equivalent HERE. They are still not what Python evaluates, and the
// equivalence is a measured accident of these two constants, not a
// property of the shape. Change a constant and the sweep must be re-run.

// attribFindRE is _valid_compression's attribution counter. Unlike
// attribRE it is unanchored and has no `\s` runs — it is Python's
// `re.findall(r"\*\(from [^)]*\)\*", …)` transcribed directly, which is
// safe precisely because the pattern contains no character class Go reads
// differently.
var attribFindRE = regexp.MustCompile(`\*\(from [^)]*\)\*`)

// compressPrompt is playbook.py's _COMPRESS_PROMPT. It is sent verbatim to
// a model, so its wording is behaviour: the rules it states are the same
// rules validCompression enforces, and a rewrite that softens one without
// the other makes the validator reject work the prompt asked for.
const compressPrompt = `You maintain an autonomous orchestration system's operational playbook (markdown).
Rewrite it TIGHTER: merge near-duplicate bullets, trim verbosity, keep it opinionated and actionable.

Hard rules:
- Keep every ` + "`## Section`" + ` header that exists in the input.
- Keep every source attribution ` + "`*(from ...)*`" + ` verbatim (a merged bullet carries all its sources).
- Do NOT invent advice that is not in the input. Do NOT drop factual content.
- Return ONLY the full rewritten markdown document, no commentary.

Playbook:
%s
`

const defaultCurationMinChars = 4000

// dedupText drops exact-duplicate bullets (by normalized core), keeping
// the first occurrence. Non-bullet lines pass through untouched.
//
// Two spellings here are load-bearing and both look like details:
//
//   - The split is on the literal "\n", NOT splitlines. A playbook
//     containing a lone U+2028 keeps it inside one line in both runtimes;
//     "improving" this to pytext.SplitLines would split there and then
//     rejoin with "\n", silently rewriting the operator's document.
//   - The bullet test is `line.lstrip().startswith("- ")` — Python's
//     lstrip, which strips 29 code points where Go's unicode.IsSpace
//     strips 25. A bullet indented with U+001F is a bullet there and prose
//     here.
func dedupText(text string) (string, int) {
	seen := map[string]bool{}
	out := make([]string, 0, 32)
	removed := 0
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(pytext.TrimLeft(line), "- ") {
			core := entryCore(line)
			if core != "" && seen[core] {
				removed++
				continue
			}
			if core != "" {
				seen[core] = true
			}
		}
		out = append(out, line)
	}
	return strings.Join(out, "\n"), removed
}

// validCompression rejects an LLM rewrite that lost structure,
// attributions, or grew.
//
// These are structural checks, not substring sniffs, and the difference
// was a review finding on the Python side: header LINES must survive
// occurrence-counted, so `## Cost` is not preserved by `## Costly` and a
// duplicated section may not collapse into one. The bullet floor rounds
// UP — three bullets require two, never one.
//
// Every length is a CODE-POINT length. The growth ceiling compares
// document sizes, and a rewrite of a CJK or emoji-heavy playbook has
// roughly three bytes per character: a byte-length ceiling would reject
// valid compressions of exactly the documents that most need compressing.
func validCompression(old, candidate string) bool {
	newText := pytext.Strip(candidate)
	if newText == "" {
		return false
	}
	if float64(utf8.RuneCountInString(newText)) >
		float64(utf8.RuneCountInString(old))*1.1 {
		return false
	}

	oldHeaders := countHeaders(old)
	newHeaders := countHeaders(newText)
	for h, n := range oldHeaders {
		if newHeaders[h] < n {
			return false
		}
	}

	oldAttribs := countMatches(attribFindRE, old)
	newAttribs := countMatches(attribFindRE, newText)
	for a, n := range oldAttribs {
		if newAttribs[a] < n {
			return false
		}
	}

	oldBullets := countBullets(old)
	newBullets := countBullets(newText)
	floor := int(math.Ceil(float64(oldBullets) * 0.6))
	if floor < 1 {
		floor = 1
	}
	return newBullets >= floor
}

// countHeaders counts `## `-prefixed lines by their STRIPPED text, the way
// Python's Counter over `ln.strip()` does. Stripping before the prefix
// test is why an indented header still counts.
func countHeaders(text string) map[string]int {
	c := map[string]int{}
	for _, ln := range strings.Split(text, "\n") {
		s := pytext.Strip(ln)
		if strings.HasPrefix(s, "## ") {
			c[s]++
		}
	}
	return c
}

func countBullets(text string) int {
	n := 0
	for _, ln := range strings.Split(text, "\n") {
		if strings.HasPrefix(pytext.TrimLeft(ln), "- ") {
			n++
		}
	}
	return n
}

func countMatches(re *regexp.Regexp, text string) map[string]int {
	c := map[string]int{}
	for _, m := range re.FindAllString(text, -1) {
		c[m]++
	}
	return c
}

// CurateStats is curate_playbook's return dict. It is written verbatim
// into the captain's-log row's context, so its KEYS are part of the store
// shape a Python reader sees — not an internal struct.
type CurateStats struct {
	RemovedDuplicates int
	ExpiredAlarms     []string
	LLMCompressed     bool
	Archived          string
	CharsBefore       int
	CharsAfter        int
}

// context renders the stats the way Python's dict does: the same six keys,
// and expired_alarms as a list that is EMPTY rather than absent when
// nothing expired (Python's comprehension yields []).
//
// It does NOT re-normalize a nil slice here, and that is deliberate: the
// first version did, and the mutation battery showed the check was dead
// code — droppedKeys never returns nil, so the branch was unreachable and
// deleting it changed nothing. An unreachable guard reads as protection
// and provides none.
//
// No test in this package can fail on that normalization either way; see
// droppedKeys for the measurement of why, and pyjson for the pin on the
// encoder behaviour the argument depends on.
func (s *CurateStats) context() map[string]any {
	return map[string]any{
		"removed_duplicates": s.RemovedDuplicates,
		"expired_alarms":     s.ExpiredAlarms,
		"llm_compressed":     s.LLMCompressed,
		"archived":           s.Archived,
		"chars_before":       s.CharsBefore,
		"chars_after":        s.CharsAfter,
	}
}

// afterSnapshot runs immediately after the snapshot is taken and before
// anything is computed from it. Nothing in production sets it.
//
// It exists because the compare-and-swap below defends against a TIMING
// window, and the honest way to pin a timing window is to stop racing for
// it: a goroutine-based pin tests the scheduler, and passes on a machine
// where the guard is missing but the interleaving never occurs.
var afterSnapshot = func() {}

// Curate dedups and (when oversized) LLM-compresses the playbook,
// returning stats if the file changed and nil otherwise.
//
// Two passes, matching Python:
//
//  1. Deterministic dedup — free, always. It is the retroactive half of
//     the append-time guard: spam that predates that guard, or
//     re-accretes around it, gets collapsed here.
//  2. LLM compression — only when the document exceeds
//     playbook.curation_min_chars. A rewrite that loses a section header,
//     an attribution, more than 40% of bullets, or that grows, is
//     rejected and the deterministic result kept.
//
// Three orderings in here are correctness, not style:
//
//   - Alarms expire BEFORE compression. An expired reading that survives
//     into the prompt comes back rephrased as durable advice, and there is
//     then nothing left to identify it as an alarm.
//   - The snapshot and the write take the lock; the LLM round trip happens
//     OUTSIDE it. Holding a write lock across a network call starves
//     concurrent Append writers into lock timeouts.
//   - Because the lock is dropped, the write re-reads and compares against
//     the snapshot. If another writer appended meanwhile, this pass is
//     discarded rather than clobbering their entry — the next cycle
//     re-curates from the fresh file.
//
// The pre-curation version is archived before any write, and curation
// ABORTS if archiving fails: never rewrite what you cannot restore.
//
// a may be nil, and then the compression pass is skipped with the
// deterministic result kept. That is a NAMED DIVERGENCE, not parity:
// Python builds its own cheap-worker adapter from the role table when the
// caller passes none, and neither conductor.assign_model_by_role nor
// llm.build_adapter is ported yet. A nil adapter here therefore behaves
// like Python's adapter-construction failure — same outcome, reached for a
// different reason. When the role table lands, this should build one.
//
// Like Python's, this never returns an error: callers sit on exit paths.
func Curate(ctx context.Context, ws string, a llm.Adapter, rec *record.Recorder,
	force bool) *CurateStats {

	cfg, _ := config.Load()
	if !force && !config.Get(cfg, "playbook.curation_enabled", true) {
		return nil
	}

	// Snapshot UNDER the lock, compute outside it. Python takes
	// locked_write here for a read, which looks redundant and is not: a
	// concurrent Append's atomic rename can land between this read and the
	// comparison below, and an unlocked reader can also observe a partial
	// file on a platform where the rename is not atomic. The snapshot is
	// the thing the compare-and-swap later tests against, so a torn one
	// makes the CAS certify a document that never existed.
	path := Path(ws)
	var original string
	if err := record.Locked(path, func() error {
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		original = string(raw)
		return nil
	}); err != nil {
		return nil
	}

	afterSnapshot()

	ttl := config.Get(cfg, "playbook.alarm_ttl_days", defaultAlarmTTLDays)
	text, expired := expireText(original, ttl)
	text, removed := dedupText(text)
	compressed := false

	minChars := config.Get(cfg, "playbook.curation_min_chars",
		defaultCurationMinChars)
	if utf8.RuneCountInString(text) > minChars && a != nil {
		if out, ok := compress(ctx, a, text); ok {
			text, compressed = out, true
		}
	}

	if text == original {
		return nil
	}

	stats := &CurateStats{
		RemovedDuplicates: removed,
		ExpiredAlarms:     droppedKeys(expired),
		LLMCompressed:     compressed,
		CharsBefore:       utf8.RuneCountInString(original),
	}

	skipped := false
	err := record.Locked(path, func() error {
		current, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if string(current) != original {
			// Another writer appended while we were computing.
			skipped = true
			return nil
		}
		archived, err := Archive(ws, original, "curation")
		if err != nil {
			return err // can't restore → don't rewrite
		}
		stats.Archived = archived
		text = stampUpdated(text)
		return atomicWrite(path, []byte(text))
	})
	if err != nil || skipped {
		return nil
	}

	stats.CharsAfter = utf8.RuneCountInString(text)

	if rec != nil {
		_ = rec.Event("PLAYBOOK_CURATED", "playbook", curateSummary(stats),
			stats.context(), "")
	}
	return stats
}

// curateSummary is the captain's-log summary line. It is PROSE that lands
// in a file a Python reader renders, and the arrow is U+2192 — not "->".
func curateSummary(s *CurateStats) string {
	llmWord := "no"
	if s.LLMCompressed {
		llmWord = "yes"
	}
	return "curated: -" + strconv.Itoa(s.RemovedDuplicates) + " dup(s), llm=" +
		llmWord + ", " + strconv.Itoa(s.CharsBefore) + "→" +
		strconv.Itoa(s.CharsAfter) + " chars"
}

// droppedKeys lists the expired alarms' keys, and returns a non-nil empty
// slice at every length.
//
// Measured, because the obvious reason to do this is not the true one:
// making it nil does NOT change the captain's-log row today. pyjson's
// []string case renders a nil slice as `[]` exactly like an empty one,
// so the row is Python-shaped either way and the invariant is held by
// the ENCODER, not here:
//
//	pyjson.Ordered({"expired_alarms": []string(nil)}) → {"expired_alarms":[]}
//	json.Marshal(same map)                            → {"expired_alarms":null}
//
// It is kept non-nil anyway because CurateStats is a PUBLIC struct and
// nothing binds a future consumer to pyjson — a caller marshalling it
// with encoding/json emits `null` where Python emits `[]`, and a Python
// reader iterating that field gets a type error rather than an empty
// loop. So a mutant that makes this nil is EQUIVALENT through the writer
// this package uses, and not equivalent through any other.
func droppedKeys(d []Dropped) []string {
	out := make([]string, 0, len(d))
	for _, x := range d {
		out = append(out, x.Key)
	}
	return out
}

// compress runs the one LLM call, returning the candidate only if it
// passes validation. Every failure path — transport error, empty reply,
// rejected rewrite — keeps the deterministic result, which is why this
// returns a bool rather than an error: to the caller there is exactly one
// interesting outcome.
func compress(ctx context.Context, a llm.Adapter, text string) (string, bool) {
	resp, err := a.Complete(ctx,
		[]llm.Message{{Role: "user", Content: fmt.Sprintf(compressPrompt, text)}},
		llm.Options{
			MaxTokens:   2000,
			Temperature: 0.2,
			Purpose:     "playbook-curation",
		})
	if err != nil || resp == nil {
		return "", false
	}
	candidate := pytext.Strip(resp.Content)
	if !validCompression(text, candidate) {
		return "", false
	}
	return candidate, true
}
