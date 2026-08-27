// Package worldfacts ports src/world_facts.py — the run-scoped world-fact
// ledger, the plan-native home for non-action findings.
//
// The Python design notes are the contract and hold here unchanged:
// declared rather than scanned (the declarer says which kind it is; no LLM
// classification pass), run-scoped with checkpoint carry, hypothesis
// quarantine (guesses render in their own labelled section and never mix
// with facts), observation only, and an EMPTY render when nothing is known
// — the ContributionLedger's hard contract is that zero contributions leave
// prompts byte-identical.
//
// # Deliberately NOT ported (named, not hidden)
//
// `land_facts()` — the slice-2 landing that routes a run's facts into
// durable stores at finalize. It depends on five Python modules this port
// does not have: knowledge_bridge (upsert_knowledge_from_candidate),
// knowledge_web (load_knowledge_nodes), knowledge_lens (observe_pattern),
// mint_grounding (ground_lessons_for_run) and captains_log's
// WORLD_FACTS_LANDED event, plus runs.stamp_run_metadata. Porting it
// against stubs would be a landing that lands nothing while reporting
// counts, which is worse than an absence. Everything land_facts READS —
// the SOURCE_* allowlist, the strength sort, the per-key idempotency
// contract — is preserved in the fields and constants here so the landing
// can be added without changing this file.
//
// # What is different in Go, and why
//
//   - **The ledger is a pointer.** Python's dataclass is held by reference
//     on LoopContext and every declarer mutates the SAME object.
//   - **The fact map keeps its insertion order.** Python's dict does, and
//     `to_list()`, `anecdotal()`, `hypotheses()` and `_planner_twin()` all
//     iterate it. `_planner_twin` returns the FIRST near-match, so the order
//     decides which planner row a restatement is folded into.
//   - **`int()` raises, and that is behaviour.** from_list's per-row try
//     exists so one corrupt row costs only itself; a "forgiving" integer
//     coercion would ADMIT rows CPython drops. See pyIntStrict.
package worldfacts

import (
	"encoding/json"
	"errors"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/guard"
	"github.com/slycrel/maro-orchestration/go/internal/pydifflib"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The declared kinds. KINDS is a TUPLE in Python and membership is all any
// caller uses, so the Go spelling is a set plus the ordered list for the
// vocabulary differential.
const (
	KindAnecdotal  = "anecdotal"
	KindHypothesis = "hypothesis"
)

// Kinds is Python KINDS, in its declared order.
var Kinds = []string{KindAnecdotal, KindHypothesis}

func isKind(s string) bool { return s == KindAnecdotal || s == KindHypothesis }

const (
	// MaxFactsPerStep is the per-step declaration cap. Malformed entries
	// must not consume it — the cap is applied AFTER validation.
	MaxFactsPerStep = 3
	// MaxRenderedFacts caps the rendered block: facts are a hint, not a
	// wall of text.
	MaxRenderedFacts = 10
	// MaxRenderedHypotheses is smaller on purpose — guesses are the risky
	// half of the block.
	MaxRenderedHypotheses = 3
	MaxFactChars          = 300
	MaxEvidenceChars      = 300
	// Per-run landing caps: strongest facts land first; the remainder stays
	// run-scoped. Kept even though land_facts is not ported, because they
	// are part of the vocabulary the differential compares.
	MaxLandedAnecdotal  = 3
	MaxLandedHypotheses = 2
)

// Fact provenance. "step" = discovered by execution; "planner" = declared
// at decompose from INJECTED context — i.e. derived from what the stores
// already said. Provenance is sticky: a step restating a rendered planner
// fact saw it as "treat as known" first, so the restatement is laundering,
// not independent corroboration. land_facts() skips planner-sourced facts.
const (
	SourceStep    = "step"
	SourcePlanner = "planner"
)

// WorldFact is one declared fact, with the evidence that says so.
//
// Hits defaults to 1 and Steps to a FRESH list per instance. A bare
// WorldFact{} has Hits 0, which no Python instance ever has, and Source ""
// where Python's field default is "step" — use NewFact.
type WorldFact struct {
	Kind      string
	Fact      string
	Evidence  string
	FirstStep int
	Hits      int
	Steps     []int
	Source    string
}

// NewFact applies the dataclass field defaults (hits=1, source="step") and
// copies steps, so two facts cannot share one backing array.
func NewFact(kind, fact, evidence string, firstStep int, steps []int, source string) *WorldFact {
	if source == "" {
		source = SourceStep
	}
	return &WorldFact{Kind: kind, Fact: fact, Evidence: evidence,
		FirstStep: firstStep, Hits: 1, Steps: append([]int(nil), steps...),
		Source: source}
}

// ToDict is Python `WorldFact.to_dict()`.
//
// The key ORDER is part of it: this dict rides the run checkpoint as JSON,
// and Python emits a dict in insertion order. pyval.Obj is the ordered
// spelling; `list(self.steps)` is a COPY, so a consumer mutating the
// returned steps cannot reach back into the ledger.
func (f *WorldFact) ToDict() pyval.Obj {
	steps := make([]any, 0, len(f.Steps))
	for _, s := range f.Steps {
		steps = append(steps, s)
	}
	return pyval.Obj{
		{Key: "kind", Val: f.Kind},
		{Key: "fact", Val: f.Fact},
		{Key: "evidence", Val: f.Evidence},
		{Key: "first_step", Val: f.FirstStep},
		{Key: "hits", Val: f.Hits},
		{Key: "steps", Val: steps},
		{Key: "source", Val: f.Source},
	}
}

// scanClean is Python `_scan_clean(text)`: a deterministic injection scan,
// FAIL-CLOSED (guard unavailable -> not clean).
//
// It lives at LEDGER INGRESS, not only at the completion tool: a hand-built
// parallel outcome or a corrupted checkpoint reaches Observe/FromList
// without ever passing CleanDeclared, and rendered facts carry "treat as
// known" authority.
//
// Python's `except Exception: return False` has no live Go path —
// guard.ScanContent returns a report rather than raising — so the
// fail-closed direction survives as this comment plus the differential that
// pins both engines on the same corpus.
func scanClean(text string) bool {
	return guard.ScanContent(text, "world_facts").IsClean
}

// WorldFactLedger is the run-scoped accumulator. It lives on LoopContext
// and rides the checkpoint.
type WorldFactLedger struct {
	facts map[string]*WorldFact
	order []string
}

// New is Python `WorldFactLedger()` — and, through looptypes, the
// `_new_world_facts()` default factory.
func New() *WorldFactLedger {
	return &WorldFactLedger{facts: map[string]*WorldFact{}}
}

// Key is Python `WorldFactLedger._key(kind, fact)`.
//
// Keyed by kind + normalized text: the same finding restated in a later
// step bumps hits instead of duplicating a ledger row. `' '.join(s.lower()
// .split())` is lower-THEN-split, and Python's bare `.split()` splits on
// its 29-code-point whitespace set and drops empty fields — pytext.Split is
// that, where strings.Fields is the ASCII half.
func Key(kind, fact string) string {
	return kind + ":" + normalize(fact)
}

func normalize(s string) string {
	return strings.Join(pytext.Split(pytext.Lower(s)), " ")
}

// plannerTwin is Python `_planner_twin(kind, fact_norm)`: a planner-sourced
// same-kind row this text is a near-restatement of.
//
// Laundering guard: exact-key matching let "Archive X is blocked."
// re-declare planner-seeded "archive x is blocked" as a fresh, LANDABLE
// step fact — provenance stickiness has to survive at least surface
// restatements. Lexical only; a true semantic paraphrase still slips this
// (named residual, upstream's).
//
// The FIRST match in dict insertion order wins, so which planner row a
// restatement folds into depends on declaration order, not on similarity
// rank.
func (l *WorldFactLedger) plannerTwin(kind, factNorm string) *WorldFact {
	for _, f := range l.Facts() {
		if f.Kind != kind || f.Source != SourcePlanner {
			continue
		}
		other := normalize(f.Fact)
		if pydifflib.Ratio(factNorm, other) >= 0.85 {
			return f
		}
	}
	return nil
}

// Observe is Python `observe(kind, fact, evidence, step_idx, source=...)`:
// record one declared fact. It reports whether the fact was newly ledgered.
//
// First evidence wins, matching terrain's first-reason-wins: the original
// observation is the one later corroborations point back to. Provenance is
// first-writer-wins and sticky.
//
// TWO ORDERING DETAILS that a tidier port loses:
//
//   - the KEY is built from the STRIPPED-BUT-UNTRUNCATED fact, while the
//     STORED fact is truncated to MaxFactChars. For a declaration longer
//     than 300 characters those are different strings, so `Key(kind,
//     stored.Fact)` — which is what FromList and the landing allowlist
//     compute — does NOT round-trip to the key this method used.
//   - `hits` only rises on a NEW step index, unlike terrain.Observe, which
//     counts every repeat. One step restating a fact three times in a
//     payload must not outrank a genuinely re-observed fact in the landing
//     sort.
func (l *WorldFactLedger) Observe(kind, fact, evidence string, stepIdx int, source string) bool {
	fact = pytext.Strip(fact)
	if fact == "" || !isKind(kind) {
		return false
	}
	if !scanClean(fact + "\n" + evidence) {
		return false
	}
	key := Key(kind, fact)
	existing := l.facts[key]
	if existing == nil {
		existing = l.plannerTwin(kind, normalize(fact))
	}
	if existing == nil {
		// Unknown source strings are PRESERVED, not coerced to "step" —
		// coercion made any future producer's facts silently landable, and
		// the landing side allowlists instead.
		l.put(key, NewFact(kind, runeClip(fact, MaxFactChars),
			runeClip(pytext.Strip(evidence), MaxEvidenceChars),
			stepIdx, []int{stepIdx}, source))
		return true
	}
	if !containsInt(existing.Steps, stepIdx) {
		existing.Hits++
		existing.Steps = append(existing.Steps, stepIdx)
	}
	return false
}

func (l *WorldFactLedger) put(key string, f *WorldFact) {
	if _, seen := l.facts[key]; !seen {
		l.order = append(l.order, key)
	}
	l.facts[key] = f
}

func containsInt(xs []int, n int) bool {
	for _, x := range xs {
		if x == n {
			return true
		}
	}
	return false
}

// runeClip is Python's `s[:n]` — a CODE-POINT slice. A byte slice would cut
// a multi-byte character in half and produce a fact neither runtime wrote.
func runeClip(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

// Facts returns every ledgered fact in Python's dict INSERTION order.
func (l *WorldFactLedger) Facts() []*WorldFact {
	out := make([]*WorldFact, 0, len(l.order))
	for _, k := range l.order {
		out = append(out, l.facts[k])
	}
	return out
}

// Fact looks one row up by its ledger key.
func (l *WorldFactLedger) Fact(key string) (*WorldFact, bool) {
	f, ok := l.facts[key]
	return f, ok
}

// Len is `len(ledger.facts)`; the zero ledger is falsy in Python.
func (l *WorldFactLedger) Len() int { return len(l.facts) }

// Anecdotal is Python `anecdotal()` — insertion order.
func (l *WorldFactLedger) Anecdotal() []*WorldFact { return l.ofKind(KindAnecdotal) }

// Hypotheses is Python `hypotheses()` — the slice-2 seam finalize routes
// into observe_pattern(). Insertion order.
func (l *WorldFactLedger) Hypotheses() []*WorldFact { return l.ofKind(KindHypothesis) }

func (l *WorldFactLedger) ofKind(kind string) []*WorldFact {
	var out []*WorldFact
	for _, f := range l.Facts() {
		if f.Kind == kind {
			out = append(out, f)
		}
	}
	return out
}

// Render is the advisory known-this-run block.
//
// Anecdotal facts render as established; hypothesis facts render in a
// separately-labeled guesses section — prompting them in is fine "as long
// as they're honest about what they are and aren't", the LABEL is the
// honesty, and the two sections must never mix.
//
// The two sections are NOT symmetrical and the asymmetry is deliberate: a
// fact carries `[N× since step M]` when it was re-observed, a guess never
// does. Corroboration count is a claim about evidence, and a guess has
// none to count.
func (l *WorldFactLedger) Render() string {
	var lines []string
	rows := sortByStrength(l.Anecdotal())
	if len(rows) > 0 {
		lines = append(lines,
			"Facts already established THIS RUN — treat as known; do "+
				"not re-derive them:")
		for _, f := range rows[:minInt(len(rows), MaxRenderedFacts)] {
			line := "  - " + f.Fact
			if f.Evidence != "" {
				line += " (evidence: " + f.Evidence + ")"
			}
			if f.Hits > 1 {
				line += " [" + strconv.Itoa(f.Hits) + "× since step " +
					strconv.Itoa(f.FirstStep) + "]"
			}
			lines = append(lines, line)
		}
		if len(rows) > MaxRenderedFacts {
			lines = append(lines, "  …and "+
				strconv.Itoa(len(rows)-MaxRenderedFacts)+" more.")
		}
	}
	guesses := sortByStrength(l.Hypotheses())
	if len(guesses) > 0 {
		lines = append(lines,
			"Unconfirmed guesses declared THIS RUN — assumptions, NOT "+
				"established facts; verify before relying on one and never "+
				"restate one as fact:")
		for _, f := range guesses[:minInt(len(guesses), MaxRenderedHypotheses)] {
			line := "  - " + f.Fact
			if f.Evidence != "" {
				line += " (basis: " + f.Evidence + ")"
			}
			lines = append(lines, line)
		}
		if len(guesses) > MaxRenderedHypotheses {
			lines = append(lines, "  …and "+
				strconv.Itoa(len(guesses)-MaxRenderedHypotheses)+" more.")
		}
	}
	return strings.Join(lines, "\n")
}

// sortByStrength is `sorted(rows, key=lambda f: (-f.hits, f.fact))`.
// Python's sort is STABLE, so rows tying on both keys keep their insertion
// order; SliceStable is what preserves that.
func sortByStrength(rows []*WorldFact) []*WorldFact {
	out := append([]*WorldFact(nil), rows...)
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Hits != out[j].Hits {
			return out[i].Hits > out[j].Hits
		}
		return out[i].Fact < out[j].Fact
	})
	return out
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// -- checkpoint carry -------------------------------------------------

// ToList is Python `to_list()` — every row's to_dict(), in insertion order.
func (l *WorldFactLedger) ToList() []pyval.Obj {
	out := make([]pyval.Obj, 0, len(l.order))
	for _, f := range l.Facts() {
		out = append(out, f.ToDict())
	}
	return out
}

// FromList is Python `from_list(rows)` — rebuild from checkpoint rows.
//
// Malformed rows are dropped, never raised: a corrupt checkpoint must not
// block a resume. The per-row try is what makes that per-ROW — one corrupt
// row (a non-numeric first_step) must cost only itself, never the valid
// rows around it.
//
// Three details that decide which rows survive:
//
//   - the restore is an INGRESS: a corrupted checkpoint must not smuggle an
//     instruction-shaped fact past the declaration scan, so scanClean runs
//     again here.
//   - ABSENT provenance restores as "step" (pre-slice-3 checkpoints have no
//     source key; all their facts were step-declared). A PRESENT unknown
//     string is kept as-is — the landing allowlist decides what it may do.
//   - the ledger key is computed from the TRUNCATED fact here, where
//     Observe computes it from the untruncated one. Over 300 characters
//     those disagree, and this is the spelling a restored ledger carries.
func FromList(rows []any) *WorldFactLedger {
	ledger := New()
	for _, row := range rows {
		d, ok := asDict(row)
		if !ok {
			continue
		}
		kindRaw, _ := d["kind"]
		kind, kindIsStr := kindRaw.(string)
		fact := pytext.Strip(pyStr(dictGet(d, "fact", "")))
		if !kindIsStr || !isKind(kind) || fact == "" {
			continue
		}
		evidenceRaw := pytext.Strip(pyStr(dictGet(d, "evidence", "")))
		if !scanClean(fact + "\n" + evidenceRaw) {
			continue
		}
		firstStep, err := pyIntStrict(orDefault(dictGet(d, "first_step", 0), 0))
		if err != nil {
			continue
		}
		hits, err := pyIntStrict(orDefault(dictGet(d, "hits", 1), 1))
		if err != nil {
			continue
		}
		if hits < 1 {
			hits = 1
		}
		stepsRaw, listOK := asList(dictGet(d, "steps", []any{}))
		if !listOK {
			// Python iterates `row.get("steps", [])`; a non-iterable raises
			// TypeError and the per-row except drops the whole row. A STRING
			// is iterable in Python — see asList.
			continue
		}
		steps := make([]int, 0, len(stepsRaw))
		bad := false
		for _, s := range stepsRaw {
			if !isPyNumber(s) {
				continue
			}
			n, ierr := pyIntStrict(s)
			if ierr != nil {
				bad = true
				break
			}
			steps = append(steps, n)
		}
		if bad {
			continue
		}
		src := ""
		if v, present := d["source"]; present && pyval.Truthy(v) {
			src = pyStr(v)
		} else {
			src = SourceStep
		}
		wf := &WorldFact{
			Kind: kind, Fact: runeClip(fact, MaxFactChars),
			Evidence:  runeClip(evidenceRaw, MaxEvidenceChars),
			FirstStep: firstStep, Hits: hits, Steps: steps, Source: src,
		}
		ledger.put(Key(kind, wf.Fact), wf)
	}
	return ledger
}

// orDefault is Python's `x or default` — the truthiness collapse that runs
// BEFORE int(). `first_step: 0` and `first_step: ""` and `first_step: null`
// all become the literal default, so int() never sees them and never raises
// on them.
func orDefault(v any, def any) any {
	if pyval.Truthy(v) {
		return v
	}
	return def
}

// Declared is one validated entry from CleanDeclared. Python returns a dict
// with keys in the order kind, fact, evidence; a caller that serializes
// these must emit them in that order.
type Declared struct {
	Kind     string
	Fact     string
	Evidence string
}

// CleanDeclared is Python `clean_declared(raw)` — validate one step's
// declared world_facts payload.
//
// Returns up to MaxFactsPerStep validated entries. The cap is applied AFTER
// validation so malformed entries don't consume the budget — which is why
// the length check sits at the TOP of the loop rather than at the append.
// Unknown kinds are dropped (the declarer says which kind it is — no
// reclassification, no guessing). Never fails.
//
// The injection scan is here as well as at ledger ingress: a declared fact
// re-enters later prompts verbatim under "treat as known" framing, which is
// stronger authority than step prose gets. A flagged declaration is
// DROPPED, not sanitized, and never consumes the cap.
//
// `isinstance(raw, list)` is the entry gate, so a non-list — including a
// Python tuple or a dict — yields an empty result rather than an error.
// ok=false is that arm.
func CleanDeclared(raw any) []Declared {
	cleaned := []Declared{}
	// Python's `isinstance(raw, list)`, spelled narrowly on purpose.
	//
	// NOT asList: that helper serves from_list's `steps`, where Python
	// ITERATES the value, so it answers true for a string (which iterates
	// to characters) and for a dict. `isinstance` answers false for both,
	// and the two questions only happen to agree here because an empty
	// iteration and a refusal both produce no declarations.
	//
	// The bare `raw.([]any)` this replaces was a silent total failure:
	// pyval.List is a NAMED type, so it does not assert to []any, and the
	// port's own JSON loader returns pyval.List — so every declared world
	// fact from a model reply was dropped, with clean_declared reporting a
	// well-formed empty result. Found by differential, not by reading.
	var entries []any
	switch t := raw.(type) {
	case pyval.List:
		entries = []any(t)
	case []any:
		entries = t
	default:
		return cleaned
	}
	for _, entry := range entries {
		// EQUIVALENT MUTANT, recorded so it is not re-derived: moving this
		// check below the asDict guard changes nothing observable. Once the
		// cap is reached nothing can be appended on any later iteration
		// either way, so the only difference is how many entries are
		// inspected before the loop stops. Kept above the guard because
		// that is where Python writes it, and because the ORDER is what
		// makes "cap applied AFTER validation" true — a malformed entry
		// must not consume the budget, which the cap-then-validate reading
		// would break if the two ever stopped being interchangeable.
		if len(cleaned) >= MaxFactsPerStep {
			break
		}
		d, isDict := asDict(entry)
		if !isDict {
			continue
		}
		kind := pytext.Lower(pytext.Strip(pyStr(dictGet(d, "kind", ""))))
		fact := runeClip(pytext.Strip(pyStr(dictGet(d, "fact", ""))), MaxFactChars)
		evidence := runeClip(pytext.Strip(pyStr(dictGet(d, "evidence", ""))), MaxEvidenceChars)
		if !isKind(kind) || fact == "" {
			continue
		}
		if !scanClean(fact + "\n" + evidence) {
			continue
		}
		cleaned = append(cleaned, Declared{Kind: kind, Fact: fact, Evidence: evidence})
	}
	return cleaned
}

// Enabled is Python `world_facts_enabled()` — the single kill switch for
// capture (render of an empty ledger is already a no-op, so gating capture
// gates everything).
//
// pyval.Truthy, NOT config.GetBool: Python spells this `bool(...)`, the
// LANGUAGE's truthiness, which says `world_facts.enabled: "false"` is TRUE
// because the string is non-empty. config.get_bool would say false. The
// gate here is bool().
func Enabled(cfg map[string]any) bool {
	return pyval.Truthy(config.GetRaw(cfg, "world_facts.enabled", true))
}

// -- Python value semantics --------------------------------------------

func asDict(v any) (map[string]any, bool) {
	switch t := v.(type) {
	case map[string]any:
		return t, true
	case pyval.Obj:
		out := make(map[string]any, len(t))
		for _, kv := range t {
			out[kv.Key] = kv.Val
		}
		return out, true
	}
	return nil, false
}

func dictGet(d map[string]any, key string, def any) any {
	if v, ok := d[key]; ok {
		return v
	}
	return def
}

// asList models `for s in row.get("steps", [])`. ok=false is the TypeError
// arm — a value Python cannot iterate. A STRING is iterable in Python and
// yields its characters, none of which pass `isinstance(s, (int, float))`,
// so it iterates to an empty steps list rather than raising: that is the
// `[]any{}` return, not the false one.
func asList(v any) ([]any, bool) {
	switch t := v.(type) {
	case []any:
		return t, true
	case pyval.List:
		return []any(t), true
	case []int:
		out := make([]any, len(t))
		for i, n := range t {
			out[i] = n
		}
		return out, true
	case string:
		return []any{}, true
	case map[string]any, pyval.Obj:
		// Iterating a dict yields its KEYS, which are strings here and fail
		// the isinstance filter. Same empty answer as a string, not a raise.
		return []any{}, true
	}
	return nil, false
}

// isPyNumber is `isinstance(s, (int, float))`.
//
// A Python BOOL passes it — bool subclasses int — and `int(True)` is 1, so
// `steps: [true]` restores as step 1 rather than being filtered. That is
// upstream's behaviour, not a Go accident.
func isPyNumber(v any) bool {
	switch v.(type) {
	case int, int64, float64, bool, json.Number:
		return true
	}
	return false
}

var errNotInt = errors.New("int() argument is not a number or a numeric string")

// pyIntStrict is Python's `int(v)`, INCLUDING the raise.
//
// It is deliberately not pyval.IntOf, which is documented as forgiving and
// answers 0 where CPython raises. The difference is the whole point of
// from_list's per-row try: a row whose first_step is "abc" is DROPPED by
// CPython, and a forgiving coercion would restore it with first_step 0 —
// admitting a row the Python runtime discards, into a ledger both runtimes
// read.
//
// The float arm truncates toward zero (int(-2.9) is -2, not -3), and NaN
// and the infinities raise.
func pyIntStrict(v any) (int, error) {
	switch t := v.(type) {
	case int:
		return t, nil
	case int64:
		return int(t), nil
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	case float64:
		if math.IsNaN(t) || math.IsInf(t, 0) {
			return 0, errNotInt
		}
		return int(math.Trunc(t)), nil
	case json.Number:
		if n, err := t.Int64(); err == nil {
			return int(n), nil
		}
		f, err := t.Float64()
		if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
			return 0, errNotInt
		}
		return int(math.Trunc(f)), nil
	case string:
		// int("  12  ") is 12; int("1_0") is 10; int("") raises. Python
		// strips its own whitespace set first.
		s := pytext.Strip(t)
		n, err := strconv.ParseInt(strings.ReplaceAll(s, "_", ""), 10, 64)
		if err != nil {
			return 0, errNotInt
		}
		return int(n), nil
	case nil:
		return 0, errNotInt
	}
	return 0, errNotInt
}

// pyStr is Python's `str(v)`. It is NOT pyval.Repr: `str("x")` is `x` where
// `repr("x")` is `'x'`, and these strings are compared and stored.
func pyStr(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return pyval.Repr(v)
}
