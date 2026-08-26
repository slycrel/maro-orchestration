package introspect

import (
	"math/big"
	"regexp"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The three patterns from _format_decomp_too_broad_note, with Python's
// character classes rather than Go's.
//
// Go's `regexp` reads `\d` as `[0-9]` and `\s` as five ASCII code points;
// Python's `re` on a str pattern reads `\d` as every Unicode decimal digit
// (760 on this box) and `\s` as its full 29-point whitespace set. These
// come from pytext, which measures both against CPython — a pattern
// transcribed character-for-character from Python matches a different
// language in Go, and this one runs over evidence text read out of a store
// both runtimes write.
var (
	// `Step (\d+).*?(\d+)\s*(?:ms|tokens).*?(\d+)\s*(?:tokens|ms)`
	//
	// Only whether it MATCHES is used — Python captures three groups and
	// then throws them away, keeping the whole evidence line. Worth
	// keeping the groups in the port anyway: they are what makes the
	// pattern's shape readable, and dropping them would make the next
	// reader wonder which numbers it was supposed to pick out.
	decompStepRe = regexp.MustCompile(
		`Step (` + pytext.DigitClass + `+).*?(` + pytext.DigitClass + `+)` +
			pytext.SpaceClass + `*(?:ms|tokens).*?(` + pytext.DigitClass +
			`+)` + pytext.SpaceClass + `*(?:tokens|ms)`)

	// `(\d{4,})ms` — FOUR or more digits, so 999ms is left alone and
	// 1000ms becomes 1s.
	decompMSRe = regexp.MustCompile(`(` + pytext.DigitClass + `{4,})ms`)

	// `(\d{4,})\s*tokens`
	decompTokensRe = regexp.MustCompile(
		`(` + pytext.DigitClass + `{4,})` + pytext.SpaceClass + `*tokens`)
)

// divThousand is `int(s) // 1000` for a run of decimal digits.
//
// Two Python behaviours a `strconv.Atoi` would lose. `int()` accepts any
// Unicode decimal digit, so `int("١٢٣٤")` is 1234 — pytext.FoldDecimals
// maps those to ASCII first. And Python's ints are unbounded, so a
// forty-digit run in a hostile evidence line divides cleanly there and
// overflows a fixed-width parse here. big.Int costs nothing on the path
// that matters and removes the whole class.
func divThousand(s string) string {
	n, ok := new(big.Int).SetString(pytext.FoldDecimals(s), 10)
	if !ok {
		// Unreachable from the regexes above, which match digits only.
		// Returning the input unchanged is the answer that cannot invent
		// a number.
		return s
	}
	return n.Quo(n, big.NewInt(1000)).String()
}

func replaceGroup1(re *regexp.Regexp, s string, f func(string) string) string {
	locs := re.FindAllStringSubmatchIndex(s, -1)
	if locs == nil {
		return s
	}
	var b strings.Builder
	prev := 0
	for _, m := range locs {
		b.WriteString(s[prev:m[0]])
		b.WriteString(f(s[m[2]:m[3]]))
		prev = m[1]
	}
	b.WriteString(s[prev:])
	return b.String()
}

// projectTag is Python's
// `f" (project={diag.project})" if diag.project else ""`, which appears
// once in each note formatter.
//
// One spelling, because it is an operator-facing line that both formatters
// emit and the port keeps finding that two private copies of one rendering
// are how two surfaces start describing the same thing differently. It was
// two copies until a mutation battery could not name a single site for it.
func projectTag(project string) string {
	if project == "" {
		return ""
	}
	return " (project=" + project + ")"
}

// FormatDecompTooBroadNote is introspect._format_decomp_too_broad_note.
//
// It pulls the worst-offender evidence line out of a decomposition_too_broad
// diagnosis and compresses its numbers, so the next planner sees "Step 8
// took 534s with 277K tok" rather than generic "decompose further" advice.
//
// The fallback chain is Python's exactly: the first evidence line matching
// the step pattern, else the FIRST evidence line whatever it says, else the
// empty string. A note with an empty detail still renders — the brackets,
// the project tag and the advice are all still there — which is why the
// empty case is a fixture and not an early return.
func FormatDecompTooBroadNote(d LoopDiagnosis) string {
	detail := ""
	for _, ev := range d.Evidence {
		if decompStepRe.MatchString(ev) {
			detail = ev
			break
		}
	}
	if detail == "" && len(d.Evidence) > 0 {
		// NAMED DIVERGENCE, and a narrow one: Python's fallback is `best
		// if best else evidence[0]`, a TRUTHINESS test on the matched
		// line. An evidence line that is the EMPTY STRING can never match
		// the step pattern, so the two spellings only part on a line that
		// matched and is empty — impossible, since the pattern requires
		// the literal "Step". Written as a `== ""` check because that is
		// what the code means; noted so the next reader does not have to
		// re-derive that it is safe.
		detail = d.Evidence[0]
	}
	// The two substitutions COMMUTE, and that is worth writing down so
	// nobody "fixes" the order later. Their match regions can never
	// overlap: one needs a digit run immediately before "ms" and the other
	// a digit run before optional whitespace and "tokens", and a single
	// run cannot be immediately before both. Neither replacement's OUTPUT
	// can create a match for the other either — one appends "s" and the
	// other "K tok". A battery mutant that swapped them was unkillable for
	// exactly this reason, and was retired rather than counted as a gap.
	detail = replaceGroup1(decompMSRe, detail, func(g string) string {
		return divThousand(g) + "s"
	})
	detail = replaceGroup1(decompTokensRe, detail, func(g string) string {
		return divThousand(g) + "K tok"
	})
	return "[decomposition_too_broad]" + projectTag(d.Project) + " " + detail +
		" — bias toward narrower steps (cap ≤120s/200K tok per step; " +
		"split if a step touches >3 files)"
}

// noteStopwords is _STOPWORDS, verbatim and in the same twenty entries.
// It is a SET in Python, so the order here is presentation only.
var noteStopwords = map[string]bool{
	"a": true, "an": true, "the": true, "to": true, "in": true, "of": true,
	"for": true, "and": true, "or": true, "with": true, "is": true,
	"are": true, "was": true, "were": true, "my": true, "me": true,
	"i": true, "on": true, "at": true, "by": true,
}

// tokenSet is `set(text.lower().split()) - _STOPWORDS`.
//
// `.split()` with no argument is not `strings.Fields`-with-Go-whitespace:
// it splits on Python's 29-code-point whitespace set and drops empty
// runs. pytext.Split is that. `.lower()` is pytext.Lower, which is
// str.lower() including the final-sigma rule — not unicode.ToLower per
// rune.
func tokenSet(text string) map[string]bool {
	out := map[string]bool{}
	for _, w := range pytext.Split(pytext.Lower(text)) {
		if !noteStopwords[w] {
			out[w] = true
		}
	}
	return out
}

// FindRelevantFailureNotes is introspect.find_relevant_failure_notes:
// recent non-healthy diagnoses that look relevant to `goal`, rendered as
// one-line "known patterns to avoid" for injection before decomposition.
//
// Zero LLM cost — the relevance test is token overlap between the goal and
// each diagnosis's evidence text.
//
// SAME-PROJECT ENTRIES ALWAYS LEAD, regardless of overlap, and they are
// excluded from scoring entirely rather than scored and boosted: a
// same-project diagnosis with zero goal overlap still ranks above a
// perfectly-overlapping one from elsewhere. That is deliberate in Python
// (a prior decomposition warning on the same project is far more
// actionable) and it is the part a port is most likely to "improve" into a
// single weighted sort.
//
// The scored sort is STABLE and keys on overlap ALONE. Python's
// `list.sort` is stable, so two diagnoses with the same overlap stay in
// the order load_diagnoses produced them, which is newest-first. A Go
// `sort.Slice` would be free to swap them and the top-`limit` slice would
// silently pick a different diagnosis on every run.
func FindRelevantFailureNotes(ws, goal string, limit, lookback int, project string) []string {
	var nonHealthy []LoopDiagnosis
	for _, d := range LoadDiagnoses(ws, lookback) {
		if d.FailureClass != "healthy" {
			nonHealthy = append(nonHealthy, d)
		}
	}
	if len(nonHealthy) == 0 {
		return nil
	}

	goalTokens := tokenSet(goal)

	var sameProject []LoopDiagnosis
	type scoredDiag struct {
		overlap int
		diag    LoopDiagnosis
	}
	var scored []scoredDiag
	for _, d := range nonHealthy {
		if project != "" && d.Project == project {
			sameProject = append(sameProject, d)
			continue
		}
		evTokens := tokenSet(strings.Join(d.Evidence, " "))
		overlap := 0
		for t := range goalTokens {
			if evTokens[t] {
				overlap++
			}
		}
		if overlap > 0 {
			scored = append(scored, scoredDiag{overlap, d})
		}
	}
	sort.SliceStable(scored, func(i, j int) bool {
		return scored[i].overlap > scored[j].overlap
	})

	ordered := sameProject
	for _, s := range scored {
		ordered = append(ordered, s.diag)
	}
	if len(ordered) > limit {
		// `ordered[:limit]`. A NEGATIVE limit slices from the end in
		// Python — `ordered[:-1]` drops the last entry — and this
		// comparison lets a negative limit through to return everything
		// instead. Not reachable from any caller today (limit is a
		// keyword with a default of 3) and named rather than guessed at:
		// the same negative-index hazard pyval.Clip carries a whole
		// paragraph about.
		ordered = ordered[:limit]
	}
	if len(ordered) == 0 {
		return nil
	}

	var notes []string
	for _, d := range ordered {
		if d.FailureClass == "decomposition_too_broad" {
			notes = append(notes, FormatDecompTooBroadNote(d))
			continue
		}
		// `diag.recommendation[:120].replace("\n", " ")` — clipped FIRST,
		// at 120 RUNES, and only then are newlines flattened. Doing it the
		// other way round changes nothing about length here but would on
		// any clip whose bound the replacement could cross, and the order
		// is free to keep.
		rec := strings.ReplaceAll(pyval.Clip(d.Recommendation, 120), "\n", " ")
		notes = append(notes, "["+d.FailureClass+"]"+projectTag(d.Project)+" "+rec)
	}
	return notes
}
