package metrics

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The WRITER half of the step-cost ledger, plus the two things that read
// across runs rather than across steps.
//
// `stepcosts.go` is all readers. This file is what puts rows in front of
// them, which makes it the half where a mistake is durable: a reader that
// is wrong gives a wrong answer once, and a writer that is wrong writes a
// wrong answer into a store the other runtime reads back forever.

// StepCostRow is the entry `record_step_cost` builds and returns.
//
// FIELD ORDER IS THE WIRE FORMAT. Python builds a dict literal and
// json.dumps preserves insertion order, so the row on disk carries these
// keys in exactly this sequence — and the file is appended to by both
// runtimes. Reordering the struct reorders the JSON.
//
// `estimated_cost_usd` is the one conditional key: Python adds it only when
// a provider figure displaced the estimate, so the two lanes stay
// distinguishable in the store rather than by inference. The KEY'S PRESENCE
// is the marker, which is why the field is a POINTER — see its declaration.
type StepCostRow struct {
	ID              string  `json:"id"`
	RecordedAt      string  `json:"recorded_at"`
	StepType        string  `json:"step_type"`
	StepTextPreview string  `json:"step_text_preview"`
	TokensIn        int     `json:"tokens_in"`
	TokensOut       int     `json:"tokens_out"`
	CacheReadTokens int     `json:"cache_read_tokens"`
	TotalTokens     int     `json:"total_tokens"`
	CostUSD         float64 `json:"cost_usd"`
	CostSource      string  `json:"cost_source"`
	Status          string  `json:"status"`
	GoalPreview     string  `json:"goal_preview"`
	Model           string  `json:"model"`
	ElapsedMS       int     `json:"elapsed_ms"`
	// The join key to the run that spent it. Python's comment: previews
	// only join fuzzily, and burn-in adjudication needs cost-per-goal.
	LoopID string `json:"loop_id"`
	// Present ONLY on a provider-priced row: `if provider > 0: entry[
	// "estimated_cost_usd"] = round(est_usd, 8)`. The KEY'S PRESENCE is what
	// separates the two lanes — tests/test_metrics.py:593 asserts
	// `"estimated_cost_usd" not in e` on an estimate row, not that it is zero.
	//
	// This was `float64` with `omitempty`, under a comment claiming a
	// zero-token step carrying a positive provider cost "is not a shape any
	// caller produces". It is: llm.py:834 passes `input_tokens or 0`, so a
	// provider that priced a call without reporting token counts writes
	// exactly that row — estimate 0.0, provider positive. Python emitted
	// fifteen keys with `"estimated_cost_usd": 0.0`; omitempty emitted
	// fourteen, and a reader keyed on presence read the row as an ESTIMATE
	// row, i.e. as unpriced spend. A pointer makes presence explicit and
	// makes the zero writable (adversarial metrics r1, HIGH).
	EstimatedCostUSD *float64 `json:"estimated_cost_usd,omitempty"`
}

// StepCostInput is record_step_cost's parameter list. It is a struct
// because Python's is ten arguments deep with seven defaulted, and a Go
// call site with ten positional arguments is where the wrong zero goes in
// the wrong slot.
type StepCostInput struct {
	StepText        string
	TokensIn        int
	TokensOut       int
	Status          string
	Goal            string
	Model           string
	ElapsedMS       int
	CacheReadTokens int
	LoopID          string
	ProviderCostUSD float64
}

// newRowID is `str(uuid.uuid4())[:12]`.
//
// That slice is taken from the HYPHENATED spelling, so the id is eight hex
// digits, a hyphen, and three more — not twelve hex digits. A generator
// that emitted twelve hex characters would produce ids that never collide
// with Python's and never match a reader keyed on the shape.
func newRowID() string {
	var b [6]byte
	if _, err := rand.Read(b[:]); err != nil {
		// Same posture as record.NewID: a cost row must not fail to be
		// written because the CSPRNG hiccuped. UNREACHABLE on go 1.24, where
		// crypto/rand.Read is documented to always succeed — kept anyway,
		// because the alternative is discarding a row over an error that
		// cannot happen, and because the posture should not differ between
		// two writers into the same store.
		//
		// The fallback used to be `"t%07x-%03x"`, which said which path made
		// it and did NOT keep the shape: the leading 't' is not hex, so the
		// id failed the very regex this file's own test uses to recognise
		// one, and a reader keyed on the shape would have dropped the row
		// entirely. When the two goals conflict the shape wins — a row nobody
		// can read is worse than a row nobody can attribute (metrics r1, LOW).
		return rowIDFallback(time.Now().UnixNano())
	}
	h := hex.EncodeToString(b[:])
	return h[:8] + "-" + h[8:11]
}

// rowIDFallback is the clock-derived arm of newRowID, split out for one
// reason: it is otherwise UNTESTABLE, and the test that claimed to cover it
// did so by re-typing this format string into itself. That test passed for
// any implementation, including the broken "t%07x" one it was written to
// prevent — a mutation of the real literal could not fail a test holding its
// own copy (metrics r1 battery, M88).
//
// A seam exists here so the assertion can run against the code that ships.
func rowIDFallback(n int64) string {
	return fmt.Sprintf("%08x-%03x", uint32(n), uint16(n>>32)&0xFFF)
}

// RecordStepCost is metrics.record_step_cost.
//
// NEVER RAISES, and that is load-bearing rather than defensive: cost
// recording sits inside the agent loop, and a write failure that propagated
// would take down a run over telemetry. The returned row is complete
// whether or not the append succeeded — Python returns the entry it built
// regardless, and callers use it for the outcome record.
func RecordStepCost(ws string, in StepCostInput) StepCostRow {
	estUSD := EstimateCost(in.TokensIn, in.TokensOut, in.Model, in.CacheReadTokens)
	// `max(0.0, float(provider_cost_usd or 0.0))`. Clamping BEFORE the
	// test is what makes a negative provider figure fall through to the
	// estimate rather than displace it with a negative cost.
	//
	// The two languages' max() disagree on NaN and it does not matter here,
	// which is worth one line so the next reader does not re-derive it:
	// CPython replaces its running maximum only when `item > current`, and
	// `nan > 0.0` is False, so `max(0.0, nan)` is 0.0; Go's math.Max
	// propagates the NaN. Both then fail `provider > 0` and take the
	// estimate lane, and `provider` is read nowhere else — so the
	// divergence is real, unobservable, and normalising it would be a line
	// no input could exercise.
	provider := math.Max(0, in.ProviderCostUSD)
	costUSD := estUSD
	source := "estimate"
	if provider > 0 {
		costUSD, source = provider, "provider"
	}

	row := StepCostRow{
		ID:              newRowID(),
		RecordedAt:      pyval.NowISO(time.Now().UTC()),
		StepType:        ClassifyStepType(in.StepText),
		StepTextPreview: pyval.Clip(in.StepText, 120),
		TokensIn:        in.TokensIn,
		TokensOut:       in.TokensOut,
		CacheReadTokens: in.CacheReadTokens,
		TotalTokens:     in.TokensIn + in.TokensOut,
		CostUSD:         pyval.Round(costUSD, 8),
		CostSource:      source,
		Status:          in.Status,
		GoalPreview:     pyval.Clip(in.Goal, 80),
		Model:           in.Model,
		ElapsedMS:       in.ElapsedMS,
		LoopID:          in.LoopID,
	}
	if provider > 0 {
		est := pyval.Round(estUSD, 8)
		row.EstimatedCostUSD = &est
	}

	// Everything from here is inside Python's bare `except: pass`.
	line, err := pyval.DumpsStruct(row)
	if err != nil {
		return row
	}
	path := StepCostsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return row
	}
	_ = record.AppendRawLine(path, []byte(line))
	return row
}

// Cross-run cost history. These read run CARDS, not step rows: the p90 the
// budget gate compares against is a distribution over whole runs.
const (
	// RunCostCardLimit is RUN_COST_CARD_LIMIT.
	RunCostCardLimit = 200
	// RunCostMinSamples is RUN_COST_MIN_SAMPLES: below this the answer is
	// "no opinion" rather than a small-sample p90, and callers fall back to
	// the static floors.
	RunCostMinSamples = 8
	runCostCacheTTL   = 900 * time.Second
)

// runCostSuccessClasses is RUN_COST_SUCCESS_CLASSES. The stamps match
// run_curation's, and the membership test is what keeps a failed run's
// spend out of the "typical successful run" line.
var runCostSuccessClasses = map[string]bool{
	"success": true, "done-unverified": true,
}

// The cache is keyed by LIMIT, exactly as Python's dict is, because a
// caller asking for a different window is asking a different question.
//
// Python caches on time.monotonic(); the Go equivalent is time.Since on a
// monotonic reading, which `time.Now()` carries. A wall-clock subtraction
// would let an NTP step expire — or immortalize — the entry.
var (
	runCostMu    sync.Mutex
	runCostCache = map[int]runCostEntry{}
)

// runCostNow is a TEST SEAM, and the only kind of seam that can pin a TTL.
//
// Expiry is invisible to a differential: every fixture runs in well under
// fifteen minutes, so a cache that never expires and one that expires on
// schedule return the same answers, and a battery that shortens the TTL from
// 900s to 300s survives untouched. Sleeping past a real TTL is not a test, it
// is a fifteen-minute test. Advancing the clock is.
var runCostNow = time.Now

type runCostEntry struct {
	at  time.Time
	val float64
	ok  bool
}

// SuccessfulRunCostP90 is metrics.successful_run_cost_p90. The bool is
// Python's Optional: false means "history too thin to have an opinion",
// which is NOT the same as 0.0 and must not collapse into it — a caller
// reading 0.0 as a threshold would gate every run.
//
// Never raises, and caches ~15 minutes per process: the distribution moves
// per run rather than per step, and the gate runs once per loop.
func SuccessfulRunCostP90(ws string, limit int) (float64, bool) {
	now := runCostNow()
	runCostMu.Lock()
	hit, present := runCostCache[limit]
	runCostMu.Unlock()
	// `if _hit and _now - _hit[0] < TTL` — a MISSING entry and a FRESH one
	// are the two cases; a stale entry falls through and is recomputed.
	// Note Python's `if _hit` also treats a cached `(t, None)` tuple as
	// truthy, because the tuple itself is non-empty: a cached "no opinion"
	// IS served from cache. Presence is therefore the whole test, which is
	// why the entry carries no separate flag — the two answers Python never
	// caches never reach this map at all.
	if present && now.Sub(hit.at) < runCostCacheTTL {
		return hit.val, hit.ok
	}

	val, ok, cacheable := computeRunCostP90(ws, limit)
	if cacheable {
		runCostMu.Lock()
		runCostCache[limit] = runCostEntry{at: now, val: val, ok: ok}
		runCostMu.Unlock()
	}
	return val, ok
}

// computeRunCostP90 returns the p90 and, SEPARATELY, whether the answer is
// cacheable. Python has two ways of saying "no opinion" and only one of
// them is remembered:
//
//	if not root.is_dir():
//	    return None                       # early — never reaches the cache
//	...
//	_run_cost_p90_cache[limit] = (_now, result)
//	return result                         # every other answer, None included
//
// A missing runs directory is a workspace that has not run anything YET,
// so caching it would answer "no opinion" for fifteen minutes after the
// first run lands. A too-thin sample is cached, because more cards are
// what would change it and cards arrive per run, not per step.
//
// The port had one return and cached whatever came back, which made a
// fresh workspace's budget gate blind for the first quarter hour of its
// life. Two exits in the original, two here.
func computeRunCostP90(ws string, limit int) (val float64, ok, cacheable bool) {
	root := runsRoot(ws)
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return 0, false, false
	}
	// `root.glob("*/run_card.json")` yields in DIRECTORY order, not sorted
	// order — pathlib scandirs each component and never sorts. filepath.Glob
	// sorts lexically, and the difference is not cosmetic: the mtime sort
	// below is stable, so whatever order arrives here is the tiebreak, and
	// the tiebreak decides which of two same-mtime cards survives `[:limit]`.
	// Sixteen cards written in the same second at limit=8 answered 700 here
	// and 1300 in CPython. os.File.ReadDir(-1) is Go's unsorted read.
	cards, cerr := globRunCards(root)
	if cerr != nil {
		// Python's scandir raising propagates to the outer except: None, and
		// NOT cached, because the next call may find a readable directory.
		return 0, false, false
	}
	type carded struct {
		path  string
		mtime time.Time
	}
	rows := make([]carded, 0, len(cards))
	for _, p := range cards {
		st, serr := os.Stat(p)
		if serr != nil {
			// `key=lambda p: p.stat().st_mtime` RAISES here — a card deleted
			// between the glob and the stat — and the whole call returns None
			// through the outer except WITHOUT caching.
			//
			// The port used to skip the card instead, arguing that answering
			// "no opinion" for fifteen minutes because one run was cleaned up
			// is worse than losing the sample. That argument was true only
			// because the flattened single exit cached this answer. It does
			// not any more, so the divergence has no reason to exist and the
			// port now says what CPython says.
			return 0, false, false
		}
		rows = append(rows, carded{p, st.ModTime()})
	}
	// `sorted(..., key=mtime, reverse=True)[:limit]` — newest first, then
	// truncated. A truncation before the sort would sample the directory's
	// iteration order instead of recency. reverse=True is STABLE in CPython
	// (it reverses, sorts, and reverses back), so ties keep the order they
	// arrived in rather than flipping — SliceStable, never Slice.
	sort.SliceStable(rows, func(i, j int) bool {
		return rows[i].mtime.After(rows[j].mtime)
	})
	rows = rows[:pyval.SliceStop(len(rows), limit)]

	var vals []float64
	for _, r := range rows {
		raw, rerr := os.ReadFile(r.path)
		if rerr != nil {
			continue
		}
		// `card_path.read_text(encoding="utf-8")` is a STRICT decode, and it
		// runs BEFORE json.loads. One non-UTF-8 byte anywhere in the card —
		// including inside a field nothing here reads, like `goal` — raises
		// UnicodeDecodeError, which the `except Exception: continue` catches,
		// and the card is not a sample.
		//
		// Go's JSON decoder instead substitutes U+FFFD for invalid bytes and
		// returns a perfectly good map, so without this the torn card is
		// ADMITTED. That fails OPEN in the direction that matters: a junk card
		// with a large total_cost_usd becomes a sample, which raises the p90,
		// which raises both the warn line and the 4×p90 auto kill-line.
		// (SpendForLoops learned this in r1; the fix was never carried here.)
		text, derr := pyval.DecodeUTF8Strict(raw)
		if derr != nil {
			continue
		}
		// pyval.LoadsMap, not encoding/json: `json.loads` admits the bare
		// tokens NaN, Infinity and -Infinity, and Go's decoder rejects them.
		// A card carrying `"total_cost_usd": NaN` is DROPPED by encoding/json
		// and ADMITTED by CPython, where it makes the p90 itself nan and every
		// downstream budget comparison false. The port has one Python-shaped
		// reader and this is the file that needs it.
		card, jerr := pyval.LoadsMap(text)
		if jerr != nil {
			continue
		}
		// `cost = card.get("total_cost_usd")` then `if cost and ...` — the
		// truthiness test is on the RAW value, BEFORE float(). The string
		// "0" is truthy and floats to a real 0.0 sample; the float 0.0 is
		// falsy and is not a sample at all. Testing `f == 0` after the
		// conversion collapses those two into one.
		cost := card["total_cost_usd"]
		if !pyval.Truthy(cost) {
			continue
		}
		// `card.get("success_class") in RUN_COST_SUCCESS_CLASSES`.
		//
		// RUN_COST_SUCCESS_CLASSES is a TUPLE (metrics.py:249), not a set,
		// and that distinction is the whole behaviour of this line: tuple
		// membership walks the elements comparing with `==`, so an
		// UNHASHABLE class is simply not in it and the card is skipped. It
		// does NOT raise. A set would have raised TypeError and taken the
		// whole distribution out through the outer except.
		//
		// This port asserted the set reading for one round, complete with a
		// confident comment, and answered None where CPython answers a
		// number. Read the container, not the operator.
		cls := card["success_class"]
		s, isStr := cls.(string)
		if !isStr || !runCostSuccessClasses[s] {
			continue
		}
		// `vals.append(float(cost))` has no try of its own, so a truthy
		// non-numeric — "abc", a list — raises out of the LOOP and out of
		// the FUNCTION through the outer except: None, uncached. Skipping
		// the card instead would answer from the remaining ones, which is a
		// different number and not a safer one.
		f, fok := pyval.Float(cost)
		if !fok {
			return 0, false, false
		}
		vals = append(vals, f)
	}
	if len(vals) < RunCostMinSamples {
		// Cached: more cards are what would change this, and cards arrive
		// per run rather than per step.
		return 0, false, true
	}
	sort.Float64s(vals)
	// `vals[int(0.9 * (len(vals) - 1))]` — int() TRUNCATES toward zero, so
	// this is a floor and not a round, and it indexes the sorted list
	// directly rather than interpolating between neighbours. At n=8 that is
	// index 6, not 7: an off-by-one here moves the auto kill-line by a
	// whole sample.
	return vals[int(0.9*float64(len(vals)-1))], true, true
}

// globRunCards is `root.glob("*/run_card.json")`, in directory order.
//
// pathlib walks the pattern one component at a time: it scandirs `root`,
// keeps the entries that are directories, and for each one tests whether the
// literal child `run_card.json` is THERE. Neither step sorts.
//
// The two tests are not the same test, and the port had them the same way:
//
//   - The DIRECTORY test follows symlinks — `is_dir()` does by default — so a
//     symlinked run directory counts. os.Stat, not os.Lstat.
//   - The CARD test does NOT. Measured on 3.14.3: a `run_card.json` that is a
//     DANGLING symlink is still yielded by glob, because the check is about
//     the name being present, not the target being readable. os.Lstat.
//
// Using os.Stat for the card silently dropped it, which looked like the safe
// reading and was a divergence with teeth: CPython yields the dead link, then
// `key=lambda p: p.stat().st_mtime` raises FileNotFoundError, and the WHOLE
// distribution comes back None — uncached. Skipping it instead answered a
// confident p90 from the surviving cards and cached that for fifteen minutes.
// One dangling link is the difference between "no opinion" and a budget
// breaker computed from a silently truncated sample.
//
// os.Lstat also gets the directory-named-run_card.json case right for free:
// pathlib yields it, the mtime works, and the read fails into `continue`.
func globRunCards(root string) ([]string, error) {
	f, err := os.Open(root)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	ents, err := f.ReadDir(-1)
	if err != nil {
		return nil, err
	}
	var out []string
	for _, e := range ents {
		dir := filepath.Join(root, e.Name())
		if st, serr := os.Stat(dir); serr != nil || !st.IsDir() {
			continue
		}
		card := filepath.Join(dir, "run_card.json")
		if _, serr := os.Lstat(card); serr != nil {
			continue
		}
		out = append(out, card)
	}
	return out, nil
}

// runsRoot is runs.runs_root() resolved against an explicit workspace.
//
// Python reads MARO_WORKSPACE (falling back to OPENCLAW_WORKSPACE, then
// ~/.maro/workspace) inside the function. The port takes the workspace as
// an argument everywhere for the reason L39 names — a verb that takes a
// workspace argument and then reads ambient config is two answers — and
// the resolution itself lives in config.Workspace() at the call site.
func runsRoot(ws string) string { return filepath.Join(ws, "runs") }

// TailCostScope is metrics.tail_cost_scope: the loop id and phase that
// LLM calls made underneath should be attributed to.
//
// Python uses a ContextVar, which propagates implicitly down the call
// stack and — the reason the comment there insists on it over a global —
// does NOT leak across concurrent runs' tails. Go has no implicit
// propagation, so the equivalent is a context.Context value, which gives
// the same two properties: it flows down and it does not flow sideways.
//
// The cost of the difference is that a Go caller must thread the ctx.
// That is visible where Python's was invisible, and it is the honest
// spelling: a tail phase that forgets to pass the ctx silently records
// nothing in Python too, it just has no place for the reader to notice.
type TailCostScope struct {
	LoopID string
	Phase  string
}

type tailScopeKey struct{}

// WithTailCostScope wraps a context the way `with tail_cost_scope(...)`
// wraps a block. An empty loop id produces the no-op shape Python
// describes: the scope is set, but tail_cost_scope_active() reports
// nothing, so there is no attribution to a run that cannot be joined to.
//
// `phase or "tail"` is Python's default and applies to an empty string,
// not just a missing argument.
func WithTailCostScope(ctx context.Context, loopID, phase string) context.Context {
	if phase == "" {
		phase = "tail"
	}
	return context.WithValue(ctx, tailScopeKey{},
		TailCostScope{LoopID: loopID, Phase: phase})
}

// TailCostScopeActive is metrics.tail_cost_scope_active(): the scope, or
// false when there is none to join to.
func TailCostScopeActive(ctx context.Context) (TailCostScope, bool) {
	s, ok := ctx.Value(tailScopeKey{}).(TailCostScope)
	if !ok || s.LoopID == "" {
		return TailCostScope{}, false
	}
	return s, true
}

// clearRunCostCache empties the per-process p90 cache. Python's tests reach
// into `_run_cost_p90_cache` directly; this is the same door, named, so a
// test does not have to be in on the cache's shape.
func clearRunCostCache() {
	runCostMu.Lock()
	runCostCache = map[int]runCostEntry{}
	runCostMu.Unlock()
}
