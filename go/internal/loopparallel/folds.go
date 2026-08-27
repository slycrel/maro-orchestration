package loopparallel

// The two FOLDS of loop_parallel.py: `_run_parallel_batch` and
// `_run_parallel_path`. The schedulers above decide what runs; these
// decide what the run MEANT — they turn a scheduler's outcome dicts into
// StepOutcomes, a LoopResult, ledger writes and the lines an operator
// reads.
//
// They were slice 2 because they reach loop_post_step, orch and metrics.
// None of those have a Go twin yet, so each arrives as a Deps func and the
// boundary is a declaration rather than an omission — the same shape
// internal/loopfinalize uses.
//
// # The exception discipline is not uniform, and it is load-bearing
//
// Python wraps four calls in `try/except Exception` and leaves two bare.
// The bare ones are the DECISION fan-out — `record_step_decisions` and
// `record_step_world_facts` — added by a chunk-3 review finding because
// the parallel paths bypassed `_process_done_step` and silently dropped
// decisions. Silently dropping them again through an `except` would undo
// that fix, so those two propagate and everything else degrades to a log
// line. The Go signatures say which is which: a Deps func whose error is
// swallowed and one whose error is returned are different contracts.

import (
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Slice limits, all Python code-point slices rather than byte cuts.
const (
	// BatchExcerptCap is `_b_result[:800]`, the completed-context excerpt
	// a later step sees.
	BatchExcerptCap = 800
	// StepLabelCap is `_batch_text[:80]`, used in the excerpt header and
	// in every verbose line.
	StepLabelCap = 80
	// CallbackResultCap is `_oc.get("result", "")[:120]` on the fan-out
	// path's step_callback.
	CallbackResultCap = 120
	// InjectCap is `_batch_injected[:6]` — the most steps one batch may
	// add to the plan.
	InjectCap = 6
	// InjectLedgerSentinel is the item index an unmirrored injection
	// carries. It renders as `ledger #-1`.
	InjectLedgerSentinel = -1
)

// StepCostIn is metrics.record_step_cost's keyword set.
type StepCostIn struct {
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

// BatchIn is `_run_parallel_batch`'s inputs. The four slices are
// MUTATED IN PLACE, exactly as Python mutates the lists it is handed —
// which is why they are pointers and not values.
type BatchIn struct {
	StepText string
	Peers    []string

	StepOutcomes     *[]looptypes.StepOutcome
	CompletedContext *[]string
	RemainingSteps   *[]string
	RemainingIndices *[]int

	ParallelFanOut  int
	ProjArtifactDir string
	Iteration       int
	StepIdx         int

	// BatchItemIndices is Python's `Optional[List[int]]`, carrying the
	// NEXT.md item index for the lead step plus each peer. nil is None —
	// and an EMPTY slice is falsy in Python too, so both fall to the -1
	// sentinel for every member.
	BatchItemIndices []int
}

// BatchOut is the 6-tuple `_run_parallel_batch` returns. Iteration and
// StepIdx are returned rather than mutated because Python returns them:
// they are ints, and rebinding one inside the function does not reach the
// caller's name.
type BatchOut struct {
	Iteration    int
	StepIdx      int
	TokensIn     int
	TokensOut    int
	CacheRead    int
	ProviderCost float64
}

// BatchDeps is everything `_run_parallel_batch` reaches outside itself.
//
// Which errors are swallowed is part of the contract, not an
// implementation detail — see the package comment. RecordStepDecisions
// and RecordStepWorldFacts are the two whose errors come back out.
type BatchDeps struct {
	// Monotonic is time.monotonic(), read once for the batch start and
	// again per step.
	Monotonic func() float64
	// ResolveTools is `resolve_tools_fn()`; its result is handed to the
	// scheduler as `[LLMTool(**t) for t in ...]`.
	ResolveTools func() []pyval.Obj
	// RunStepsParallel is `_run_steps_parallel`. Its error propagates:
	// Python does not wrap the call.
	RunStepsParallel func(steps []string, maxWorkers int,
		projectDir, ancestry, incremental string, tools []pyval.Obj) ([]pyval.Obj, error)

	// Swallowed to a debug line.
	RecordStepCost func(StepCostIn) error
	// Swallowed to a debug line. Called only for a done step whose item
	// index is >= 0.
	MarkItemDone func(project string, index int) error
	// Swallowed to a WARNING, and the -1 sentinel stands.
	AppendNextItems func(project string, items []string) ([]int, error)

	// PROPAGATED. See the package comment.
	RecordStepDecisions  func(stepIdx string, oc pyval.Obj) error
	RecordStepWorldFacts func(stepIdx string, oc pyval.Obj) error

	// ShapeSteps is loop_planning._shape_steps. Not wrapped.
	ShapeSteps func(steps []string, label string) []string

	// Stderr receives the `[maro] ...` verbose lines, without a newline.
	Stderr func(line string)
	// Info / Debug / Warning are the logger at those levels.
	Info    func(msg string)
	Debug   func(msg string)
	Warning func(msg string)
}

// BatchCtx is the LoopContext fields these folds read.
type BatchCtx struct {
	Goal    string
	Project string
	LoopID  string
	Verbose bool
	// ModelKey is `getattr(ctx.adapter, "model_key", "")`.
	ModelKey string
	// Ledger and Ancestry feed DrainPendingContext.
	Ledger   *looptypes.ContributionLedger
	Ancestry string
}

// pyFloat is `float(x)`. It takes a number or a NUMERIC STRING and
// refuses anything else, because that is what Python does — and
// `provider_cost_usd` arrives from a step body this package does not own.
// A forgiving conversion here would turn a crash into a silently free
// step.
func pyFloat(v any) (float64, error) {
	switch t := v.(type) {
	case nil:
		return 0, fmt.Errorf("float() argument must be a string or a real number, not 'NoneType'")
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	case string:
		if f, ok := pyval.ParseFloat(strings.TrimSpace(t)); ok {
			return f, nil
		}
		return 0, fmt.Errorf("could not convert string to float: %s", pyval.Repr(t))
	}
	return pyval.FloatOf(v), nil
}

// costOf is `float(oc.get("provider_cost_usd", 0.0) or 0.0)`.
//
// The `or 0.0` runs BEFORE the float(), so a falsy value — None, 0, "",
// an empty list — becomes 0.0 without ever reaching the conversion. Only
// a TRUTHY non-number raises.
func costOf(oc pyval.Obj) (float64, error) {
	v, ok := oc.Get("provider_cost_usd")
	if !ok {
		v = 0.0
	}
	if !pyval.Truthy(v) {
		return 0.0, nil
	}
	return pyFloat(v)
}

func intField(oc pyval.Obj, key string) int {
	v, ok := oc.Get(key)
	if !ok {
		return 0
	}
	return pyval.IntOf(v)
}

func strField(oc pyval.Obj, key, def string) string {
	v, ok := oc.Get(key)
	if !ok {
		return def
	}
	s, isStr := v.(string)
	if !isStr {
		return def
	}
	return s
}

// injectList is `oc.get("inject_steps", [])`, kept only when it IS a list
// (`isinstance(_bi_inject, list)`), then `str(s).strip()` filtered for
// emptiness.
func injectList(oc pyval.Obj) []string {
	v, ok := oc.Get("inject_steps")
	if !ok {
		return nil
	}
	items, isList := v.([]any)
	if !isList {
		return nil
	}
	var out []string
	for _, it := range items {
		s := strings.TrimSpace(pyval.Str(it))
		if s != "" {
			out = append(out, s)
		}
	}
	return out
}

// RunParallelBatch is `_run_parallel_batch`: run this step plus its peers
// as one batch, then fold the outcomes into the loop's state.
//
// Two arithmetic details that a reader will assume are per-step and are
// not:
//
//   - `iteration += len(_batch_steps)` runs BEFORE the fold, so EVERY
//     outcome in the batch is stamped with the same, final iteration
//     number. Nothing counts up through the batch.
//   - `_b_elapsed` is measured from the batch's own start for every
//     member, so a four-step batch records four near-identical durations
//     that are really the batch's elapsed time. That is why each outcome
//     passes `ended_ts=""` — it opts out of step_from_decompose's "now"
//     default so the report's timeline stays in its approximate mode
//     rather than rendering fabricated precision (2026-07-08 review).
func RunParallelBatch(ctx BatchCtx, in BatchIn, d BatchDeps) (BatchOut, error) {
	batchSteps := append([]string{in.StepText}, in.Peers...)
	iteration := in.Iteration + len(batchSteps)
	stepIdx := in.StepIdx
	batchStart := d.Monotonic()
	if ctx.Verbose {
		d.Stderr(fmt.Sprintf("[maro] parallel batch: %d steps at level",
			len(batchSteps)))
	}

	// One delivery boundary: the pending contributions are drained ONCE
	// and every step in the batch sees the same rendering.
	incremental, ancestry := DrainPendingContext(ctx.Ledger, ctx.Ancestry)

	maxWorkers := in.ParallelFanOut
	if len(batchSteps) < maxWorkers {
		maxWorkers = len(batchSteps)
	}
	// resolve_tools_fn() is evaluated as an ARGUMENT to the scheduler, so
	// it runs after the drain and after the verbose line, and a raise
	// there never reaches the scheduler at all.
	outcomes, err := d.RunStepsParallel(batchSteps, maxWorkers,
		in.ProjArtifactDir, ancestry, incremental, d.ResolveTools())
	if err != nil {
		return BatchOut{}, err
	}

	out := BatchOut{}
	var injected []string

	// zip() stops at the SHORTER sequence. A scheduler that returned
	// fewer outcomes than it was given steps leaves the tail unprocessed
	// and unreported — no error, no blocked outcome, the steps simply do
	// not appear in step_outcomes.
	n := len(batchSteps)
	if len(outcomes) < n {
		n = len(outcomes)
	}
	for bi := 0; bi < n; bi++ {
		batchText, oc := batchSteps[bi], outcomes[bi]
		stepIdx++
		status := strField(oc, "status", "blocked")
		elapsed := int((d.Monotonic() - batchStart) * 1000)
		out.TokensIn += intField(oc, "tokens_in")
		out.TokensOut += intField(oc, "tokens_out")
		out.CacheRead += intField(oc, "cache_read_tokens")
		cost, err := costOf(oc)
		if err != nil {
			return BatchOut{}, err
		}
		out.ProviderCost += cost

		// Ledger parity with the sequential path: batch steps used to skip
		// record_step_cost entirely, so the run card's cost silently
		// excluded them — azure-finch 2026-07-17 showed $0.406 on the card
		// against $2.41 at the loop's own budget breaker.
		if err := d.RecordStepCost(StepCostIn{
			StepText: batchText, TokensIn: intField(oc, "tokens_in"),
			TokensOut: intField(oc, "tokens_out"), Status: status,
			Goal: ctx.Goal, Model: ctx.ModelKey, ElapsedMS: elapsed,
			CacheReadTokens: intField(oc, "cache_read_tokens"),
			LoopID:          ctx.LoopID, ProviderCostUSD: cost,
		}); err != nil {
			d.Debug(fmt.Sprintf(
				"batch record_step_cost failed (non-critical): %s", err))
		}

		itemIdx := InjectLedgerSentinel
		// Python guards this with `batch_item_indices and _bi < len(...)`,
		// where the first term is the None/empty truthiness test. In Go the
		// bounds check SUBSUMES it — a nil or empty slice has length 0 and
		// no index is below it — so spelling the first term out is a
		// redundancy a reader would have to re-derive. It was written and
		// then removed: the mutation that deletes it cannot change an
		// answer (L8).
		if bi < len(in.BatchItemIndices) {
			itemIdx = in.BatchItemIndices[bi]
		}

		*in.StepOutcomes = append(*in.StepOutcomes,
			looptypes.StepFromDecompose(batchText, itemIdx, looptypes.StepOpts{
				Status:        &status,
				Result:        strField(oc, "result", ""),
				Iteration:     iteration,
				TokensIn:      intField(oc, "tokens_in"),
				TokensOut:     intField(oc, "tokens_out"),
				ElapsedMS:     elapsed,
				Confidence:    ptrString(strField(oc, "confidence", "unverified")),
				InjectedSteps: rawInjectedSteps(oc),
				CallRecord:    strField(oc, "call_record", ""),
				EndedTS:       ptrString(""),
			}))

		switch status {
		case "done":
			if itemIdx >= 0 {
				if err := d.MarkItemDone(ctx.Project, itemIdx); err != nil {
					d.Debug(fmt.Sprintf(
						"parallel batch mark_item failed: %s", err))
				}
			}
			result := strField(oc, "result", "")
			excerpt := ""
			if result != "" {
				excerpt = pyval.Clip(result, BatchExcerptCap)
			}
			*in.CompletedContext = append(*in.CompletedContext,
				fmt.Sprintf("Step %d (%s):\n%s", stepIdx,
					pyval.Clip(batchText, StepLabelCap), excerpt))
			// NOT swallowed. The parallel paths used to bypass
			// _process_done_step and drop decisions on the floor; an
			// `except` here would restore that bug quietly.
			if err := d.RecordStepDecisions(fmt.Sprint(stepIdx), oc); err != nil {
				return BatchOut{}, err
			}
			if err := d.RecordStepWorldFacts(fmt.Sprint(stepIdx), oc); err != nil {
				return BatchOut{}, err
			}
			if ctx.Verbose {
				d.Stderr(fmt.Sprintf("[maro] step %d done (parallel): %s",
					stepIdx, pyval.Clip(strField(oc, "summary", ""), StepLabelCap)))
			}
			injected = append(injected, injectList(oc)...)
		case "blocked":
			if ctx.Verbose {
				d.Stderr(fmt.Sprintf("[maro] step %d blocked (parallel): %s",
					stepIdx, pyval.Clip(strField(oc, "stuck_reason", ""), StepLabelCap)))
			}
		}
	}

	if len(injected) > 0 {
		capped := d.ShapeSteps(injected[:pyval.SliceStop(len(injected), InjectCap)],
			"parallel-inject")
		// Ledger parity with the sequential inject path (2026-08-09): an
		// unmirrored injection renders as `ledger #-1` and skews the plan
		// header's progress count. A mirror failure degrades to the
		// sentinel rather than refusing the injection.
		idxs := make([]int, len(capped))
		for i := range idxs {
			idxs[i] = InjectLedgerSentinel
		}
		if ctx.Project != "" {
			if got, err := d.AppendNextItems(ctx.Project, capped); err != nil {
				d.Warning(fmt.Sprintf(
					"parallel inject ledger mirror failed for %s: %s",
					ctx.Project, err))
			} else {
				// Python rebinds the whole list, so a mirror that answered
				// the wrong LENGTH is carried through as-is rather than
				// reconciled against `capped`.
				idxs = got
			}
		}
		*in.RemainingSteps = append(append([]string{}, capped...), *in.RemainingSteps...)
		*in.RemainingIndices = append(append([]int{}, idxs...), *in.RemainingIndices...)
		d.Info(fmt.Sprintf(
			"parallel batch: injected %d step(s) from batch into plan",
			len(capped)))
		if ctx.Verbose {
			for _, s := range capped {
				d.Stderr(fmt.Sprintf(
					"[maro] injected step (from parallel batch): %s",
					pyval.Clip(s, StepLabelCap)))
			}
		}
	}

	// The cost line sums over ALL outcomes, including any the zip above
	// never folded.
	batchTokens := 0
	for _, oc := range outcomes {
		batchTokens += intField(oc, "tokens_in") + intField(oc, "tokens_out")
	}
	d.Info(fmt.Sprintf("parallel batch done: %d steps, %d tokens, %dms",
		len(batchSteps), batchTokens,
		int((d.Monotonic()-batchStart)*1000)))

	out.Iteration, out.StepIdx = iteration, stepIdx
	return out, nil
}

func ptrString(s string) *string { return &s }

// rawInjectedSteps is the list handed to step_from_decompose, which is
// `_batch_oc.get("inject_steps", [])` — the RAW value, not the stripped
// and filtered copy the injection path builds. A non-list value reaches
// the factory unchanged in Python; here anything that is not a list of
// strings becomes nil, which the factory turns into a fresh empty list.
func rawInjectedSteps(oc pyval.Obj) []string {
	v, ok := oc.Get("inject_steps")
	if !ok {
		return nil
	}
	items, isList := v.([]any)
	if !isList {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, it := range items {
		s, isStr := it.(string)
		if !isStr {
			return nil
		}
		out = append(out, s)
	}
	return out
}

// PathIn is `_run_parallel_path`'s inputs.
type PathIn struct {
	Steps      []string
	CleanSteps []string
	Deps       map[int][]int
	// Levels is Python's `Optional[List[Any]]` and only its LENGTH is
	// read, inside the dag verbose line. nil IS None, and `len(None)`
	// raises — but only when verbose is on and only on the dag path, so a
	// quiet run with levels=None finishes and a verbose one dies. The
	// pointer keeps None distinguishable from an empty list, which a Go
	// slice alone cannot do.
	Levels         *[]any
	ParallelLevels []any
	ParallelFanOut int
	ProjFanoutDir  string
	UseDAG         bool
}

// PathDeps is everything `_run_parallel_path` reaches outside itself.
type PathDeps struct {
	Monotonic    func() float64
	ResolveTools func() []pyval.Obj
	// The two schedulers. Errors propagate: neither call is wrapped.
	RunStepsParallel func(steps []string, maxWorkers int,
		projectDir, ancestry, incremental string, tools []pyval.Obj) ([]pyval.Obj, error)
	RunStepsDAG func(steps []string, deps map[int][]int, maxWorkers int,
		projectDir, ancestry, incremental string, tools []pyval.Obj) ([]pyval.Obj, error)

	// PROPAGATED, for the same reason as on the batch path.
	RecordStepDecisions  func(stepIdx string, oc pyval.Obj) error
	RecordStepWorldFacts func(stepIdx string, oc pyval.Obj) error

	Stderr func(line string)
	Debug  func(msg string)
}

// PathCtx is the LoopContext fields the fan-out fold reads.
type PathCtx struct {
	Goal    string
	Project string
	LoopID  string
	Verbose bool
	Ledger  *looptypes.ContributionLedger

	Ancestry string
	// StartedAt is ctx.started_at, a monotonic reading taken when the loop
	// began — so elapsed here spans the WHOLE run, not the fan-out.
	StartedAt float64
	// StepCallback is Optional[Callable]; nil is None.
	StepCallback func(stepNum int, stepText, result, status string) error
}

// RunParallelPath is `_run_parallel_path`: run every step at once (or
// dep-aware), then build the LoopResult the loop returns.
//
// Three things here that the batch path does differently, all of them
// observable:
//
//   - The item index handed to step_from_decompose is the ENUMERATION
//     COUNTER, 1..n. The batch path threads real NEXT.md indices through
//     `batch_item_indices` and falls back to -1 when it has none; this
//     path claims 1..n unconditionally, whatever the project's ledger
//     actually holds. Filed rather than fixed — see BACKLOG.md.
//   - No elapsed_ms is tracked per worker at all, so every outcome
//     records 0. `ended_ts=""` again opts the timeline into its
//     approximate mode rather than rendering these as real zero-duration
//     steps.
//   - `stuck_reason` is assigned, not accumulated, so the LAST blocked
//     step's reason is the one the run reports. Earlier blocks are
//     visible only in the per-step outcomes.
func RunParallelPath(ctx PathCtx, in PathIn, d PathDeps) (*looptypes.LoopResult, error) {
	// One delivery boundary for the whole fan-out — empty in practice
	// today, since nothing appends before this phase, but structurally
	// closed.
	incremental, ancestry := DrainPendingContext(ctx.Ledger, ctx.Ancestry)

	var outcomes []pyval.Obj
	var stepTexts []string
	var err error
	if in.UseDAG {
		if ctx.Verbose {
			// Python interpolates `len(levels)` directly, and `levels` is
			// typed Optional — so this line, and only this line, is where
			// a None levels kills the run.
			if in.Levels == nil {
				return nil, fmt.Errorf(
					"object of type 'NoneType' has no len()")
			}
			d.Stderr(fmt.Sprintf(
				"[maro] dag: running %d steps with dep-aware scheduling "+
					"(max_workers=%d, levels=%d, parallel_levels=%d)",
				len(in.CleanSteps), in.ParallelFanOut,
				len(*in.Levels), len(in.ParallelLevels)))
		}
		outcomes, err = d.RunStepsDAG(in.CleanSteps, in.Deps, in.ParallelFanOut,
			in.ProjFanoutDir, ancestry, incremental, d.ResolveTools())
		stepTexts = in.CleanSteps
	} else {
		if ctx.Verbose {
			d.Stderr(fmt.Sprintf(
				"[maro] fan-out: running %d steps in parallel (max_workers=%d)",
				len(in.Steps), in.ParallelFanOut))
		}
		outcomes, err = d.RunStepsParallel(in.Steps, in.ParallelFanOut,
			in.ProjFanoutDir, ancestry, incremental, d.ResolveTools())
		stepTexts = in.Steps
	}
	if err != nil {
		return nil, err
	}

	var stepOutcomes []looptypes.StepOutcome
	tokensIn, tokensOut := 0, 0
	loopStatus := "done"
	var stuckReason *string

	n := len(stepTexts)
	if len(outcomes) < n {
		n = len(outcomes)
	}
	for i := 1; i <= n; i++ {
		stepText, oc := stepTexts[i-1], outcomes[i-1]
		st := strField(oc, "status", "blocked")
		stepOutcomes = append(stepOutcomes,
			looptypes.StepFromDecompose(stepText, i, looptypes.StepOpts{
				Status:        &st,
				Result:        strField(oc, "result", ""),
				Iteration:     i,
				TokensIn:      intField(oc, "tokens_in"),
				TokensOut:     intField(oc, "tokens_out"),
				Confidence:    ptrString(strField(oc, "confidence", "unverified")),
				InjectedSteps: rawInjectedSteps(oc),
				CallRecord:    strField(oc, "call_record", ""),
				EndedTS:       ptrString(""),
			}))
		if st == "done" {
			if err := d.RecordStepDecisions(fmt.Sprint(i), oc); err != nil {
				return nil, err
			}
			if err := d.RecordStepWorldFacts(fmt.Sprint(i), oc); err != nil {
				return nil, err
			}
		}
		tokensIn += intField(oc, "tokens_in")
		tokensOut += intField(oc, "tokens_out")
		if st == "blocked" {
			loopStatus = "stuck"
			// `.get("stuck_reason", "step %d blocked")` — a key PRESENT
			// and null yields null, which is a different stop from the
			// default sentence. Both reach the operator.
			if v, ok := oc.Get("stuck_reason"); ok {
				if s, isStr := v.(string); isStr {
					stuckReason = &s
				} else if v == nil {
					stuckReason = nil
				} else {
					s := pyval.Str(v)
					stuckReason = &s
				}
			} else {
				s := fmt.Sprintf("step %d blocked", i)
				stuckReason = &s
			}
		}
		if ctx.StepCallback != nil {
			if err := ctx.StepCallback(i, stepText,
				pyval.Clip(strField(oc, "result", ""), CallbackResultCap),
				st); err != nil {
				d.Debug(fmt.Sprintf(
					"step_callback raised on parallel step %d: %s", i, err))
			}
		}
	}

	res := looptypes.NewLoopResult(ctx.LoopID, ctx.Project, ctx.Goal, loopStatus)
	res.Steps = stepOutcomes
	res.TotalTokensIn = tokensIn
	res.TotalTokensOut = tokensOut
	res.ElapsedMS = int((d.Monotonic() - ctx.StartedAt) * 1000)
	res.StuckReason = stuckReason
	return res, nil
}
