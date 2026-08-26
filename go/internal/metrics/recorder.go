package metrics

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
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
// distinguishable in the store rather than by inference. `omitempty` is the
// right spelling for it only because the value it guards is meaningful
// exactly when non-zero — see the comment at its declaration.
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
	// Present ONLY on a provider-priced row. Python writes it with
	// `if provider > 0`, and the estimate on such a row is never legitimately
	// 0.0 (estimate_cost of a zero-token step is, but a zero-token step
	// carrying a positive provider cost is not a shape any caller produces).
	// So omitempty and the Python condition agree at every reachable input;
	// where they would not, the Python condition is the spec.
	EstimatedCostUSD float64 `json:"estimated_cost_usd,omitempty"`
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
		// written because the CSPRNG hiccuped, but the fallback says which
		// path made it while keeping the shape.
		return fmt.Sprintf("t%07x-%03x", time.Now().UnixNano()&0xFFFFFFF,
			time.Now().UnixNano()&0xFFF)
	}
	h := hex.EncodeToString(b[:])
	return h[:8] + "-" + h[8:11]
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
		row.EstimatedCostUSD = pyval.Round(estUSD, 8)
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

type runCostEntry struct {
	at     time.Time
	val    float64
	ok     bool
	cached bool
}

// SuccessfulRunCostP90 is metrics.successful_run_cost_p90. The bool is
// Python's Optional: false means "history too thin to have an opinion",
// which is NOT the same as 0.0 and must not collapse into it — a caller
// reading 0.0 as a threshold would gate every run.
//
// Never raises, and caches ~15 minutes per process: the distribution moves
// per run rather than per step, and the gate runs once per loop.
func SuccessfulRunCostP90(ws string, limit int) (float64, bool) {
	now := time.Now()
	runCostMu.Lock()
	hit, present := runCostCache[limit]
	runCostMu.Unlock()
	// `if _hit and _now - _hit[0] < TTL` — a MISSING entry and a FRESH one
	// are the two cases; a stale entry falls through and is recomputed.
	// Note Python's `if _hit` also treats a cached (0.0, None) tuple as
	// truthy, because the tuple itself is non-empty. So a cached "no
	// opinion" IS served from cache, and the `cached` flag here is what
	// keeps that true.
	if present && hit.cached && now.Sub(hit.at) < runCostCacheTTL {
		return hit.val, hit.ok
	}

	val, ok := computeRunCostP90(ws, limit)
	runCostMu.Lock()
	runCostCache[limit] = runCostEntry{at: now, val: val, ok: ok, cached: true}
	runCostMu.Unlock()
	return val, ok
}

func computeRunCostP90(ws string, limit int) (float64, bool) {
	root := runsRoot(ws)
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return 0, false
	}
	cards, err := filepath.Glob(filepath.Join(root, "*", "run_card.json"))
	if err != nil {
		return 0, false
	}
	// `sorted(..., key=mtime, reverse=True)[:limit]` — newest first, then
	// truncated. A truncation before the sort would sample the directory's
	// iteration order instead of recency.
	type carded struct {
		path  string
		mtime time.Time
	}
	rows := make([]carded, 0, len(cards))
	for _, p := range cards {
		st, serr := os.Stat(p)
		if serr != nil {
			// Python's key function would RAISE here and the whole call
			// returns None through the outer except. A card deleted between
			// the glob and the stat is the realistic way that happens, and
			// answering "no opinion" for the next 15 minutes because one
			// run was cleaned up is worse than skipping it. Deliberate
			// divergence, and the only one in this function.
			continue
		}
		rows = append(rows, carded{p, st.ModTime()})
	}
	sort.SliceStable(rows, func(i, j int) bool {
		return rows[i].mtime.After(rows[j].mtime)
	})
	if len(rows) > limit {
		rows = rows[:limit]
	}

	var vals []float64
	for _, r := range rows {
		raw, rerr := os.ReadFile(r.path)
		if rerr != nil {
			continue
		}
		var card map[string]any
		if json.Unmarshal(raw, &card) != nil {
			continue
		}
		// `if cost and card.get("success_class") in CLASSES` — `cost` is a
		// TRUTHINESS test, so a card recording 0.0 spend is excluded from
		// the distribution entirely rather than dragging the p90 down. A
		// missing key is None and falsy, which is the same branch.
		f, ok := pyval.Float(card["total_cost_usd"])
		if !ok || f == 0 {
			continue
		}
		cls, _ := card["success_class"].(string)
		if !runCostSuccessClasses[cls] {
			continue
		}
		vals = append(vals, f)
	}
	if len(vals) < RunCostMinSamples {
		return 0, false
	}
	sort.Float64s(vals)
	// `vals[int(0.9 * (len(vals) - 1))]` — int() TRUNCATES toward zero, so
	// this is a floor and not a round, and it indexes the sorted list
	// directly rather than interpolating between neighbours. At n=8 that is
	// index 6, not 7: an off-by-one here moves the auto kill-line by a
	// whole sample.
	return vals[int(0.9*float64(len(vals)-1))], true
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
