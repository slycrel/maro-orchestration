// Package looptypes ports src/loop_types.py — the core types and state
// machine for the agent loop.
//
// It holds the data types (StepOutcome, LoopResult), the phase constants
// (LoopPhase), the mutable per-run state bundle (LoopContext /
// LoopStateMachine) and the module-level helpers that nearly every other
// loop module needs. In Python it exists specifically so those helpers have
// exactly one home that every extracted module can import from without a
// load-order cycle back through agent_loop.py; here it exists for the same
// reason, and Go's static import graph makes the cycle a compile error
// rather than a discipline.
//
// # Absence is not zero, and Go's zero value is a trap here
//
// Sixty-five fields, and Python distinguishes `None`, `0`, `""` and `[]` at
// several of them. Every field whose Python default is `None` AND whose
// absence is read by a consumer is a POINTER here; every field whose Python
// default is a non-zero literal is set by NewLoopContext and cannot be got
// from a composite literal. `LoopContext{}` is NOT a valid context — its
// Status is "" where Python's is "done", its MaxIterations is 0 where
// Python's is 40, and its maps and ledgers are nil. Use NewLoopContext.
//
// Where Go genuinely has no null — an `int` field that Python could hold
// None in but never does — the comment says so at the field rather than
// silently choosing one.
//
// # default_factory is not decoration
//
// Python's `field(default_factory=list)` gives every instance its OWN list;
// a bare mutable default would share one across every instance ever
// constructed. `_new_terrain` and `_new_world_facts` exist for exactly that
// reason and are ported as separate functions rather than folded into the
// struct literal, so the distinction stays visible at the place it matters.
//
// # Deliberately NOT ported (named, not hidden)
//
//   - `_orch()` — a lazy import that "resolves sys.path issues". Go imports
//     are static and resolved at build time; there is nothing to defer and
//     nothing a runtime helper could do. Callers import internal/orch.
//   - `_configure_logging`'s HANDLER installation — this port has no
//     logging hierarchy (packages return warnings; see internal/llm). The
//     LEVEL RESOLUTION ladder, which is the part with observable behaviour
//     and five branches, IS ported: see ResolveLogLevel.
package looptypes

import (
	"strconv"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/stopverdicts"
	"github.com/slycrel/maro-orchestration/go/internal/terrain"
	"github.com/slycrel/maro-orchestration/go/internal/worldfacts"
)

// Ptr is the spelling for "this optional field is PRESENT and holds v".
// It exists so a call site can say `Ptr("")` — present and empty — which is
// a state several fields here distinguish from absent.
func Ptr[T any](v T) *T { return &v }

// NewTerrain is Python `_new_terrain()`.
//
// It is a named function, not a lambda inlined into the field, because that
// is what Python does and because the indirection is the whole point:
// `terrain: TerrainMemory = TerrainMemory()` would evaluate ONCE at class
// definition and every LoopContext in the process would share one
// accumulator. Go has the same hazard in a different spelling — a
// package-level `var sharedTerrain = terrain.New()` used as the default —
// so the factory is called per instance in NewLoopContext.
func NewTerrain() *terrain.TerrainMemory { return terrain.New() }

// NewWorldFacts is Python `_new_world_facts()`. Same contract as NewTerrain.
func NewWorldFacts() *worldfacts.WorldFactLedger { return worldfacts.New() }

// nowISO is the clock `step_from_decompose` reads for its ended_ts
// sentinel. It is a variable so a byte differential can pin it; production
// code never replaces it.
var nowISO = func() string { return pyval.NowISO(time.Now().UTC()) }

// SetNowForTest replaces the clock and returns a restore func.
func SetNowForTest(fn func() string) func() {
	prev := nowISO
	nowISO = fn
	return func() { nowISO = prev }
}

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

// Python logging levels, which are plain ints in the `logging` module and
// reach `setLevel` as such.
const (
	LevelDebug   = 10
	LevelInfo    = 20
	LevelWarning = 30
	LevelError   = 40
)

// LogEnv is the environment `_configure_logging` reads. It is a struct
// rather than three os.Getenv calls so the resolution ladder can be
// measured against CPython without a subprocess per case.
type LogEnv struct {
	// MaroLogLevel is $MARO_LOG_LEVEL, raw. Python upper-cases it, which is
	// str.upper() — Unicode, not ASCII.
	MaroLogLevel string
	// MaroDebug is $MARO_DEBUG. Only the exact string "1" counts.
	MaroDebug string
	// ConfigDebug is `config.get("debug", False)`, raw. Python wraps it in
	// bool(), the language's truthiness — so the string "false" is TRUE.
	ConfigDebug any
	// ConfigFailed is Python's `except Exception: debug_on = False` arm on
	// the config import. This port's config.Load cannot raise, so nothing
	// sets it in production; the differential drives it.
	ConfigFailed bool
}

// ResolveLogLevel is the level half of Python `_configure_logging(verbose)`.
//
// Level resolution, first match wins:
//
//  1. MARO_LOG_LEVEL env var (DEBUG, INFO, WARNING, ERROR)
//  2. MARO_DEBUG=1 env var -> DEBUG
//  3. config `debug: true` -> DEBUG
//  4. verbose=True -> DEBUG
//  5. default -> WARNING (quiet)
//
// Two things a paraphrase of that list loses, and both are ported:
//
//   - the config lookup is SKIPPED ENTIRELY when either MARO_DEBUG=1 or
//     MARO_LOG_LEVEL is non-empty (`if not debug_on and not env_level`), so
//     an unparseable config cannot affect a run that named its level.
//   - the level test is `hasattr(logging, env_level)`, NOT a lookup in the
//     four names the docstring lists. `MARO_LOG_LEVEL=CRITICAL` resolves to
//     50 and works, and so does the deprecated `WARN` alias. And
//     `MARO_LOG_LEVEL=_styles` passes hasattr and hands setLevel a DICT,
//     which raises "Level not an integer or a valid string" out of
//     _configure_logging. badAttr reports that third case: it names the
//     attribute Python found that is not a level, and level is meaningless
//     when it is non-empty.
//
// The attribute table is the `logging` module's, CENSUSED rather than
// transcribed — see the differential, which sweeps every name in
// dir(logging) and fails in BOTH directions. Transcribing it by hand got
// both hard cases wrong on 2026-08-27: WARN was listed as absent under a
// comment claiming to have measured it, and _STYLES was in neither table.
// The census is also the tripwire for the interpreter upgrade that finally
// removes WARN.
//
// str.upper() is Unicode, and here that is LOAD-BEARING rather than
// merely faithful. It EXPANDS: U+FB05 and U+FB06 (the two ST ligatures)
// upper-case to the two characters "ST", so `MARO_LOG_LEVEL=_ﬅyles`
// reaches `_STYLES` and aborts the run, where strings.ToUpper leaves the
// ligature alone and resolves quietly.
//
// The comment that stood here said the opposite — that no input could
// observe the difference — and argued it from the supplement's shape. It
// was wrong about the very entry added in the same commit, and it survived
// a mutation of this call site, because reasoning about a table is not
// reading it. The differential now SWEEPS all 1.1M code points for
// expansions that land inside a level name and requires each to have a
// fixture, so a new table entry or a new supplement expansion fails on the
// day it lands.
func ResolveLogLevel(env LogEnv, verbose bool) (level int, badAttr string) {
	envLevel := pytext.Upper(env.MaroLogLevel)
	debugOn := env.MaroDebug == "1"
	if !debugOn && envLevel == "" {
		if env.ConfigFailed {
			debugOn = false
		} else {
			debugOn = pyval.Truthy(env.ConfigDebug)
		}
	}
	if envLevel != "" {
		if lv, ok := loggingLevelAttr(envLevel); ok {
			return lv, ""
		}
		if loggingHasAttr(envLevel) {
			return 0, envLevel
		}
	}
	if debugOn || verbose {
		return LevelDebug, ""
	}
	return LevelWarning, ""
}

// loggingLevelAttr is the subset of `dir(logging)` that names an INT.
// Anything here can be handed to setLevel; anything in loggingNonLevelAttrs
// passes hasattr and then raises.
var loggingLevelAttr = func(name string) (int, bool) {
	v, ok := loggingIntAttrs[name]
	return v, ok
}

var loggingIntAttrs = map[string]int{
	"CRITICAL": 50, "FATAL": 50, "ERROR": 40, "WARNING": 30,
	"INFO": 20, "DEBUG": 10, "NOTSET": 0,
	// WARN is the deprecated WARNING alias. It is still in the namespace on
	// this box's 3.14 and hasattr does not care that it is deprecated, so
	// `MARO_LOG_LEVEL=WARN` resolves to 30. Leaving it out was invisible
	// unless the run ALSO asked for debug: without it the fall-through
	// answer is WARNING too, and only --verbose or MARO_DEBUG=1 makes the
	// two disagree.
	"WARN": 30,
	// NOT `raiseExceptions`. A previous version listed it here as one of
	// "the three non-level ints in the module namespace" — it is a bool,
	// there were never three, and no input can reach it, because the env
	// value is upper()ed and "RAISEEXCEPTIONS" is not an attribute. The
	// census fails on any entry that is not its own upper-case, which is
	// what found it.
}

// loggingHasAttr is `hasattr(logging, name)` for the names that are NOT
// ints. The set is measured from dir(logging) by the differential rather
// than transcribed from documentation, and the census there fails if the
// module's namespace moves.
// The int branch is unreachable FROM ResolveLogLevel, which consults
// loggingLevelAttr first and returns; it is here because this function
// answers `hasattr`, and hasattr does not care which table a name is in.
// The census calls it directly for every name in dir(logging), so the
// branch is exercised rather than merely defended in prose — removing it
// fails that test.
func loggingHasAttr(name string) bool {
	if _, ok := loggingIntAttrs[name]; ok {
		return true
	}
	return loggingOtherAttrs[name]
}

// loggingOtherAttrs is every UPPER-CASED name in dir(logging) that is not
// an int level. Only upper-cased names can arrive, because the env value is
// `.upper()`ed first — so a lower-case attribute like `getLogger` can never
// be matched and is not listed.
var loggingOtherAttrs = map[string]bool{
	// A str: setLevel calls it an "Unknown level".
	"BASIC_FORMAT": true,
	// A dict of the three format styles. Its name is already upper-case, so
	// `MARO_LOG_LEVEL=_styles` reaches it and setLevel raises TypeError —
	// which _configure_logging does NOT catch, so the process dies at
	// startup rather than logging quietly. It was in neither table.
	"_STYLES": true,
}

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

// StepOutcome is Python's dataclass of the same name.
//
// The five leading fields have NO default in Python and must be supplied;
// the other seventeen default to a zero value that Go's zero value already
// matches, which is why this one type is safe to build as a composite
// literal. StepFromDecompose is the factory with the non-zero defaults.
type StepOutcome struct {
	Index     int
	Text      string
	Status    string // "done" | "blocked" | "skipped"
	Result    string // LLM's text output for this step
	Iteration int    // which loop iteration produced this

	TokensIn  int
	TokensOut int
	// CacheReadTokens is the subset of TokensIn served from cache (~0.1x cost).
	CacheReadTokens int
	ProviderCostUSD float64
	ElapsedMS       int
	// Confidence is "strong" | "weak" | "inferred" | "unverified" | "".
	Confidence string
	// InjectedSteps are steps added mid-plan by this step. Python's
	// default_factory gives each outcome its own list; a nil slice here is
	// the same empty, and appending to it allocates rather than writing into
	// a shared array.
	InjectedSteps []string
	// CallRecord is the path to the byte-level record of this step's LLM
	// call (<run-dir>/build/calls/call-NNNNN.json) when record-mode captured
	// it.
	CallRecord string
	// EndedTS is the ISO UTC timestamp when this step finished — it lets the
	// run report position steps on a timeline as [EndedTS - ElapsedMS,
	// EndedTS] instead of a cumulative-sum approximation that silently
	// absorbs inter-step overhead (ralph verify, hooks, replans) into the
	// wrong step's segment.
	EndedTS                string
	ExecutorSessionID      string
	ExecutorSessionResumed bool
	// StartedTS pairs with EndedTS so a timeline is measured rather than
	// derived: with only EndedTS, ONE step missing it degraded the whole run
	// to a cumulative-sum estimate that silently folded replans,
	// verification and hooks into the preceding step's duration. The gap
	// BETWEEN StartedTS and the previous EndedTS is where that work actually
	// lives, and it was invisible by construction.
	StartedTS string
	// Model / ModelTier / TierEscalatedFrom: which model actually produced
	// this step, and the tier it was resolved from. Cost was recorded but the
	// tier that produced it was not, so the cheap->mid->power retry ladder
	// could not be evaluated at all.
	Model             string
	ModelTier         string
	TierEscalatedFrom string
	// Venue is "container:<name>" | "host" | "" (unknown/not an executor
	// call). Config records container INTENT; the per-call decision can still
	// degrade to the host (docker down, auth breaker, suppressed clone), so
	// intent was never an answer to "did this step actually run isolated".
	Venue string
	// ArtifactCheck is "judged" | "unjudged" | "" (check not run — gate off,
	// step not done, or no result). Persisted so the fail-open denominator is
	// measurable from run records; the judged bit used to live only in a
	// log.info and history was unmeasurable.
	ArtifactCheck string
}

// AsDict renders a StepOutcome under the DATACLASS's own field names —
// `dataclasses.asdict(outcome)`, key for key.
//
// It is exported because more than one package's CPython differential
// needs it, and the second copy of a 24-field mapping is how two
// differentials start disagreeing about which fields exist (L14). A field
// added to the struct and forgotten here shows up as a length mismatch in
// every differential that compares against asdict, which is the point.
func (o StepOutcome) AsDict() map[string]any {
	return map[string]any{
		"index": o.Index, "text": o.Text, "status": o.Status,
		"result": o.Result, "iteration": o.Iteration,
		"tokens_in": o.TokensIn, "tokens_out": o.TokensOut,
		"cache_read_tokens": o.CacheReadTokens,
		"provider_cost_usd": o.ProviderCostUSD, "elapsed_ms": o.ElapsedMS,
		"confidence": o.Confidence, "injected_steps": o.InjectedSteps,
		"call_record": o.CallRecord, "ended_ts": o.EndedTS,
		"executor_session_id":      o.ExecutorSessionID,
		"executor_session_resumed": o.ExecutorSessionResumed,
		"started_ts":               o.StartedTS, "model": o.Model,
		"model_tier": o.ModelTier, "tier_escalated_from": o.TierEscalatedFrom,
		"venue": o.Venue, "artifact_check": o.ArtifactCheck,
	}
}

// StepOpts is `step_from_decompose`'s keyword-only tail.
//
// Three fields are POINTERS and the rest are plain, and that split is the
// whole safety property: a zero StepOpts is exactly Python's
// `step_from_decompose(text, index)` with nothing passed. The three are the
// ones whose Python default is not the Go zero value.
type StepOpts struct {
	// Status: nil is Python's default "pending". Note this is NOT one of
	// StepOutcome's documented statuses — the factory mints a step that has
	// not run yet, and the "done"|"blocked"|"skipped" vocabulary is what a
	// step becomes.
	Status *string
	Result string
	// Iteration has no Python default other than 0, so the zero value is it.
	Iteration       int
	TokensIn        int
	TokensOut       int
	CacheReadTokens int
	ProviderCostUSD float64
	ElapsedMS       int
	// Confidence: nil is Python's default "unverified", which differs from
	// StepOutcome's own field default of "". A step minted here is
	// unverified until something verifies it; a step built as a literal
	// carries no claim at all.
	Confidence *string
	// InjectedSteps: nil is Python's `None`, which becomes a FRESH empty
	// list. A non-nil slice is used as-is and is SHARED with the caller,
	// exactly as Python shares the list object it was handed.
	InjectedSteps          []string
	CallRecord             string
	ExecutorSessionID      string
	ExecutorSessionResumed bool
	// EndedTS is the sentinel this factory exists for. nil is Python's
	// `None`, which defaults to "now" (UTC) — correct at every call site
	// that constructs the outcome immediately after the step actually
	// finished (the sequential main loop, blocked-step retries). Passing
	// Ptr("") instead opts OUT of that default and leaves it genuinely
	// empty, for the two classes of call site where "now" would be a
	// FABRICATED timestamp: bulk reconstruction well after the fact
	// (checkpoint resume) and parallel/fan-out batch processing, where
	// per-step ElapsedMS/EndedTS are not real individual timings.
	// loop_report's _step_windows already falls back to an
	// explicitly-flagged approximate timeline when EndedTS is empty; this
	// sentinel is what lets a caller deliberately REQUEST that fallback
	// instead of it only ever firing for pre-field-existing data.
	EndedTS           *string
	StartedTS         string
	Model             string
	ModelTier         string
	TierEscalatedFrom string
	Venue             string
	ArtifactCheck     string
}

// StepFromDecompose is Python `step_from_decompose(text, index, **kw)` —
// the factory that centralises defaults so inline construction sites stay
// DRY.
//
// Note the argument order: Python takes (text, index) and assigns
// `index=index, text=text` into the dataclass, whose own order is (index,
// text). The Go signature keeps Python's, not the struct's.
func StepFromDecompose(text string, index int, o StepOpts) StepOutcome {
	status := "pending"
	if o.Status != nil {
		status = *o.Status
	}
	confidence := "unverified"
	if o.Confidence != nil {
		confidence = *o.Confidence
	}
	injected := o.InjectedSteps
	// EQUIVALENT MUTANT: `len(injected) == 0` cannot be told apart from
	// this. It differs only for a NON-NIL EMPTY slice, which it would
	// replace with a fresh one — and a Go caller cannot observe that,
	// because appending to a zero-length slice never writes through to the
	// header the outcome already holds. Python CAN observe it (its list
	// grows in place), which is why the nil test is the faithful spelling
	// even though nothing here can fail without it.
	if injected == nil {
		injected = []string{}
	}
	ended := ""
	if o.EndedTS != nil {
		ended = *o.EndedTS
	} else {
		ended = nowISO()
	}
	return StepOutcome{
		Index:                  index,
		Text:                   text,
		Status:                 status,
		Result:                 o.Result,
		Iteration:              o.Iteration,
		TokensIn:               o.TokensIn,
		TokensOut:              o.TokensOut,
		CacheReadTokens:        o.CacheReadTokens,
		ProviderCostUSD:        o.ProviderCostUSD,
		ElapsedMS:              o.ElapsedMS,
		Confidence:             confidence,
		InjectedSteps:          injected,
		CallRecord:             o.CallRecord,
		ExecutorSessionID:      o.ExecutorSessionID,
		ExecutorSessionResumed: o.ExecutorSessionResumed,
		EndedTS:                ended,
		StartedTS:              o.StartedTS,
		Model:                  o.Model,
		ModelTier:              o.ModelTier,
		TierEscalatedFrom:      o.TierEscalatedFrom,
		Venue:                  o.Venue,
		ArtifactCheck:          o.ArtifactCheck,
	}
}

// LoopResult is Python's dataclass of the same name.
//
// AuditLearningAllowed defaults to TRUE, so a zero LoopResult is wrong in
// the direction that silently disables learning. NewLoopResult sets it.
type LoopResult struct {
	LoopID  string
	Project string
	Goal    string
	// Status is "done" | "stuck" | "error" | "interrupted" | "restart".
	Status string
	Steps  []StepOutcome
	// StuckReason: nil is Python's None. Summary tests it for truthiness, so
	// nil and Ptr("") take the same branch — but a consumer distinguishing
	// "no reason" from "an empty reason" can, which is why it is a pointer.
	StuckReason *string
	// StopVerdict / StopEvidence: the typed stop verdict (stopverdicts) and
	// its evidence. Empty = none recorded. A verdict can coexist with
	// Status "done": the landing synthesis ends an out-of-budget run "done",
	// and the verdict is what keeps that cap-hit visible.
	StopVerdict  string
	StopEvidence string
	// PauseReason is the typed pause reason (stopverdicts PAUSE_*). Empty =
	// not paused, or paused without a typed reason (pre-stamping writers).
	PauseReason    string
	TotalTokensIn  int
	TotalTokensOut int
	ElapsedMS      int
	// LogPath: nil is Python's None. Summary omits the line for nil AND for
	// Ptr("") — `if self.log_path` is truthiness, not a presence test.
	LogPath           *string
	InterruptsApplied int
	// Injections is the structured record of every operator interrupt
	// applied mid-run: {ts, interrupt_id, source, intent, message,
	// context_only, goal_before, goal_after}. Injected content must stay
	// delineated from the original prompt/goal in run records —
	// goal_before/goal_after is non-None only when a corrective interrupt
	// changed the goal. pyval.Obj rather than a Go map because these rows
	// are serialized and a dict's key order is part of the record.
	Injections []pyval.Obj
	// MarchOfNinesAlert is the chain_success < 0.5 alert.
	MarchOfNinesAlert bool
	// PreFlightReview is the PlanReview if pre-flight ran; nil is None. Typed
	// `any` because the review type is not ported yet — Python's own
	// annotation is Optional[Any].
	PreFlightReview any
	// HadNoMatchingSkill is carried out so deferred (post-closure) skill
	// synthesis knows whether this run started with no matching skill — the
	// synthesis trigger.
	HadNoMatchingSkill bool
	// AuditLearningAllowed / AuditIncompleteWarning: direct CLI closure runs
	// after loop finalization. These declared fields carry its audit decision
	// to output/learning without an untyped side channel and without making
	// human-facing warning text the policy predicate.
	AuditLearningAllowed   bool
	AuditIncompleteWarning string
}

// NewLoopResult applies the dataclass defaults for the four required
// positional fields plus AuditLearningAllowed, which is the one default
// that is not a zero value.
func NewLoopResult(loopID, project, goal, status string) *LoopResult {
	return &LoopResult{LoopID: loopID, Project: project, Goal: goal,
		Status: status, AuditLearningAllowed: true}
}

// Summary is Python `LoopResult.summary()`. The output string is a contract
// — it reaches operator-facing surfaces — so every branch here is byte-for-
// byte, including:
//
//   - `{self.goal!r}` and `{self.stuck_reason!r}` are Python REPRs, which
//     choose their quote character based on the content and escape
//     non-printables by code point. pytext.Repr is the measured port.
//   - the two conditional lines are LIST SPLATS gated on truthiness, so
//     `interrupts_applied=0` omits the line entirely rather than printing 0,
//     and `march_of_nines_alert=False` omits it rather than printing False.
//     Their literal text carries a LEADING SPACE inside the f-string
//     (`f" interrupts_applied=..."`) — no it does not: the space is between
//     the `[` and the string in the source and belongs to neither. Measured,
//     not read: the differential compares whole outputs.
//   - the ordering is fixed: interrupts before the alert, both after
//     elapsed_ms, then stuck_reason, stop_verdict, log.
func (r *LoopResult) Summary() string {
	done, blocked := 0, 0
	for _, s := range r.Steps {
		if s.Status == "done" {
			done++
		}
	}
	for _, s := range r.Steps {
		if s.Status == "blocked" {
			blocked++
		}
	}
	lines := []string{
		"loop_id=" + r.LoopID,
		"project=" + r.Project,
		"goal=" + pytext.Repr(r.Goal),
		"status=" + r.Status,
		"steps_done=" + strconv.Itoa(done) + "/" + strconv.Itoa(len(r.Steps)) +
			" blocked=" + strconv.Itoa(blocked),
		"tokens=" + strconv.Itoa(r.TotalTokensIn) + "in+" +
			strconv.Itoa(r.TotalTokensOut) + "out",
		"elapsed_ms=" + strconv.Itoa(r.ElapsedMS),
	}
	if r.InterruptsApplied != 0 {
		lines = append(lines, "interrupts_applied="+strconv.Itoa(r.InterruptsApplied))
	}
	if r.MarchOfNinesAlert {
		lines = append(lines, "march_of_nines_alert=True")
	}
	if r.StuckReason != nil && *r.StuckReason != "" {
		lines = append(lines, "stuck_reason="+pytext.Repr(*r.StuckReason))
	}
	if r.StopVerdict != "" {
		lines = append(lines, "stop_verdict="+r.StopVerdict)
	}
	if r.LogPath != nil && *r.LogPath != "" {
		lines = append(lines, "log="+*r.LogPath)
	}
	return strings.Join(lines, "\n")
}

// ---------------------------------------------------------------------------
// Injection seam — typed "context for the next step" contributions
// ---------------------------------------------------------------------------

// ContextContribution is one provenance-stamped piece of context bound for
// the next step's prompt.
//
// It replaces a flat-string accumulator: every in-loop injector (budget
// pressure, reorientation, prereq knowledge, hook output, escalate replies,
// blocked-retry hints, user notes) appends one of these instead of
// string-concatenating, so the prompt can label each piece with where it
// came from.
type ContextContribution struct {
	// Source is who contributed: "budget" | "reorientation" | "prereq" |
	// "hook" | "escalate_reply" | "blocked_retry" | "user_note" | ...
	Source string
	// Kind is what it is: "context" | "reply" | "note".
	Kind string
	Text string
}

// RenderContributions is Python `render_contributions(records)` — render
// contributions as one provenance-labeled block.
//
// HARD CONTRACT: an empty list renders to "". Zero contributions must leave
// prompts BYTE-IDENTICAL to the pre-seam behavior — tests pin exact prompt
// shapes, and the memory.worker_slice A/B contract depends on byte-identity
// discipline. Only the non-empty rendering may differ from the old flat
// concatenation (the [source] labels are the point).
//
// Note the separator is a BLANK LINE, not a newline, and the label carries
// no Kind: `kind` is ledger metadata, not prompt text.
func RenderContributions(records []ContextContribution) string {
	// EQUIVALENT MUTANT: deleting this guard changes nothing, because
	// strings.Join over zero parts is already "". It stays because the
	// empty case is a stated CONTRACT rather than an accident of the
	// joiner — a later rewrite that added a header or a trailing newline
	// would have to step over this line to break it.
	if len(records) == 0 {
		return ""
	}
	parts := make([]string, 0, len(records))
	for _, r := range records {
		parts = append(parts, "["+r.Source+"] "+r.Text)
	}
	return strings.Join(parts, "\n\n")
}

// MaxContributionTextChars is ContributionLedger.MAX_TEXT_CHARS — the
// per-record ceiling.
//
// Contributions render straight into the next step's prompt, and nothing
// upstream caps operator-supplied text (a 10MB `--intent note` would render
// 10MB). 32K chars is far above any legitimate contributor today.
//
// Python spells it as a CLASS attribute read through `self`, so a subclass
// could lower it. Nothing does; a package const is the Go spelling and the
// subclass hook is not ported.
const MaxContributionTextChars = 32_000

// ContributionLedger is the pending next-step context — the one injection
// seam.
//
// Consume semantics, explicit by design (an escalate-reply clobber came
// from an assignment that silently doubled as consume/clear):
//
//   - Contributors APPEND records. Never assign, never clear on behalf of
//     another contributor.
//   - The merge point (sequential: loop_execute merge into execute_step;
//     parallel: the batch boundary in loop_parallel) DRAINS the pending
//     list exactly once per delivery. Drain returns the delivered batch so
//     retry paths can explicitly re-arm (Extend) what the failed step
//     already saw.
type ContributionLedger struct {
	pending []ContextContribution
	// warnings records the truncation notices Python sends to log.warning.
	// This port has no logging hierarchy, and a silent truncation is exactly
	// what the announced-cut discipline forbids, so they are kept and
	// readable rather than dropped.
	warnings []string
}

// NewContributionLedger is Python `ContributionLedger()` — and the
// `field(default_factory=ContributionLedger)` on LoopContext, which is why
// it must be called per instance.
func NewContributionLedger() *ContributionLedger { return &ContributionLedger{} }

// Append adds one contribution. Empty/whitespace text is dropped; oversized
// text is truncated (with a marker) at MaxContributionTextChars.
//
// The truncation marker is NOT budget.Clip's. Python writes its own
// `"\n…[truncated N chars]"` here, where N is the number of characters
// REMOVED — budget.Clip's marker states the kept count and the original
// length instead. Two different announced cuts, and this is the one this
// ledger emits.
//
// `len(text) > MAX_TEXT_CHARS` and `text[:MAX_TEXT_CHARS]` are CODE POINTS.
func (l *ContributionLedger) Append(source, kind, text string) {
	text = pytext.Strip(text)
	if text == "" {
		return
	}
	r := []rune(text)
	if len(r) > MaxContributionTextChars {
		dropped := len(r) - MaxContributionTextChars
		l.warnings = append(l.warnings,
			"contribution from "+pytext.Repr(source)+" truncated: "+
				strconv.Itoa(dropped)+" chars over the "+
				strconv.Itoa(MaxContributionTextChars)+" cap")
		text = string(r[:MaxContributionTextChars]) +
			"\n…[truncated " + strconv.Itoa(dropped) + " chars]"
	}
	l.pending = append(l.pending, ContextContribution{Source: source, Kind: kind, Text: text})
}

// Extend re-arms previously drained records (e.g. a blocked-step retry).
func (l *ContributionLedger) Extend(records []ContextContribution) {
	l.pending = append(l.pending, records...)
}

// DropSource removes every pending record from `source` and returns the
// count.
//
// It exists for wall-clock-sensitive contributors (the "time" line): the
// re-arm paths (compound-step split, blocked retry) replay delivered
// batches verbatim — correct for every other source, but a replayed
// elapsed-time claim is stale or duplicate by the next delivery. The merge
// points call this unconditionally before appending a freshly computed time
// line: recompute, never replay.
func (l *ContributionLedger) DropSource(source string) int {
	kept := make([]ContextContribution, 0, len(l.pending))
	for _, r := range l.pending {
		if r.Source != source {
			kept = append(kept, r)
		}
	}
	droppedN := len(l.pending) - len(kept)
	l.pending = kept
	return droppedN
}

// Drain consumes: it returns all pending records and clears the ledger.
//
// The returned batch is the ledger's OWN slice and the ledger takes a fresh
// one, which is Python's `batch = self._pending; self._pending = []`. A
// caller that holds the batch and re-arms it later gets the same records
// back; a caller that mutates it cannot reach the ledger.
func (l *ContributionLedger) Drain() []ContextContribution {
	batch := l.pending
	l.pending = nil
	return batch
}

// Pending is a read-only view for consumers that need to inspect without
// consuming. Python exposes `_pending` by convention; nothing outside the
// class reads it, and this exists for the differential.
func (l *ContributionLedger) Pending() []ContextContribution {
	return append([]ContextContribution(nil), l.pending...)
}

// Len is Python `__len__`; `bool(ledger)` is `Len() != 0`, because
// `__bool__` returns `bool(self._pending)` and a list's truth is its
// length.
func (l *ContributionLedger) Len() int { return len(l.pending) }

// Warnings returns the truncation notices Python logs, oldest first.
func (l *ContributionLedger) Warnings() []string {
	return append([]string(nil), l.warnings...)
}

// ---------------------------------------------------------------------------
// Loop state machine types
// ---------------------------------------------------------------------------

// MaxRestartDepth is the shared ceiling for every quality-loop "try again"
// mechanism: director restart and closure restart (handle.py, gate
// `continuation_depth < MAX_RESTART_DEPTH`), continuation-pass respawn
// (`MARO_MAX_CONTINUATION_DEPTH` env default), and in-loop director replan
// (LoopContext.DirectorBudgetCeiling).
//
// These are distinct counters on distinct mechanisms (cross-loop restart
// chains vs an in-loop replan budget) but were three independently-drifted
// magic numbers — 4 / <3 / 2 — that did not share a constant. One number,
// so an operator reasoning about "how many times will Maro retry before
// giving up" only has to know one value.
//
// Only handle.py's two restart gates are hard-locked to this constant.
// `MARO_MAX_CONTINUATION_DEPTH` remains an intentional env override: the
// unification made the *default* shared, not the override mechanism itself.
const MaxRestartDepth = 3

// LoopPhase names the major phases of run_agent_loop. Python spells it as a
// class of constants; the values are the contract (run_trace's PHASE_NODES
// are `"phase." + value`), so they are ported as strings and the
// differential compares the set.
const (
	PhaseInit      = "init"       // Phase A: setup, adapter, project
	PhaseDecompose = "decompose"  // Phase B: goal -> steps
	PhasePreFlight = "pre_flight" // Phase C: gates, resume, cost estimate
	PhaseParallel  = "parallel"   // Phase D: parallel fan-out (early return)
	PhasePrepare   = "prepare"    // Phase E: shape steps, NEXT.md
	PhaseExecute   = "execute"    // Phase F: main while loop
	PhaseFinalize  = "finalize"   // Phase G: reflection, recovery, return
)

// InvalidTransitionError is raised when an invalid LoopPhase transition is
// attempted.
//
// Both the TYPE and the MESSAGE are contract. Callers catch this
// specifically (Python `except InvalidTransitionError`), so a Go caller
// uses errors.As on *InvalidTransitionError; and the text reaches logs, so
// the reprs and the U+2192 arrow are reproduced exactly.
type InvalidTransitionError struct {
	From string
	To   string
}

func (e *InvalidTransitionError) Error() string {
	return "Invalid loop phase transition: " + pytext.Repr(e.From) +
		" → " + pytext.Repr(e.To)
}

// allowedTransitions is LoopStateMachine._ALLOWED.
//
//	INIT       -> DECOMPOSE
//	DECOMPOSE  -> PRE_FLIGHT
//	PRE_FLIGHT -> PARALLEL | PREPARE
//	PARALLEL   -> PREPARE
//	PREPARE    -> EXECUTE
//	EXECUTE    -> FINALIZE
//
// All phases may also advance to FINALIZE for early-exit paths, and
// FINALIZE goes nowhere — its entry is an EMPTY set, not an absent key, and
// the difference is invisible only because `_ALLOWED.get(phase, set())`
// gives an unknown phase the same empty answer. Both are ported so the
// table reads like Python's.
var allowedTransitions = map[string]map[string]bool{
	PhaseInit:      {PhaseDecompose: true, PhaseFinalize: true},
	PhaseDecompose: {PhasePreFlight: true, PhaseFinalize: true},
	PhasePreFlight: {PhaseParallel: true, PhasePrepare: true, PhaseFinalize: true},
	PhaseParallel:  {PhasePrepare: true, PhaseFinalize: true},
	PhasePrepare:   {PhaseExecute: true, PhaseFinalize: true},
	PhaseExecute:   {PhaseFinalize: true},
	PhaseFinalize:  {},
}

// AllowedFrom reports the legal next phases from `phase`, for a consumer
// that wants to ask rather than to try. An unknown phase answers an empty
// set, which is `_ALLOWED.get(self.phase, set())`.
func AllowedFrom(phase string) map[string]bool {
	out := map[string]bool{}
	for k := range allowedTransitions[phase] {
		out[k] = true
	}
	return out
}

// LoopContext is the mutable state bundle for run_agent_loop.
//
// Instead of 30+ local variables threaded through 1,800 lines, all mutable
// loop state lives here and is passed to the extracted phase methods.
//
// BUILD IT WITH NewLoopContext. Four fields have non-zero Python defaults
// (LoopStatus, Phase, MaxIterations, DirectorBudgetCeiling) and five are
// default_factory allocations (PendingContext, Terrain, WorldFacts,
// StepRetries, StepTierOverrides, MilestoneExpanded) that are nil in a
// composite literal.
type LoopContext struct {
	// -- Identity --
	LoopID  string
	Project string
	Goal    string
	// MeasurementClass and HandleID are explicit top-level request
	// provenance. They travel WITH the loop rather than being rediscovered
	// from ambient run-dir context at finalize.
	MeasurementClass string
	HandleID         string

	// -- Execution state --
	StepOutcomes     []StepOutcome
	RemainingSteps   []string
	RemainingIndices []int
	CompletedContext []string
	Iteration        int
	StepIdx          int

	// -- Status --
	// LoopStatus is "done" | "stuck" | "interrupted" | "error", defaulting
	// to "done".
	LoopStatus string
	// StuckReason: nil is Python's None. The prose half of a stop; the
	// verdict below is the machine-readable half.
	StuckReason *string
	// StopVerdict / StopEvidence: the typed stop verdict (stopverdicts
	// vocabulary) and its evidence, set at the break site that decides the
	// run's ending. Empty = no verdict (clean success, or a stop seam not yet
	// wired). Rides BESIDE StuckReason.
	StopVerdict  string
	StopEvidence string
	// PauseReason is the typed pause reason, set by StampPause at the site
	// that parks the run. Empty = not paused, or reason untyped.
	PauseReason string
	Phase       string

	// -- Token/cost tracking --
	TotalTokensIn  int
	TotalTokensOut int

	// -- Stuck detection --
	StuckStreak int
	// LastAction: nil is Python's None, which is distinct from an empty
	// action string at the sites that compare consecutive actions.
	LastAction *string

	// -- Budget --
	// CostBudget / TokenBudget: nil is Python's None — NO BUDGET, which is
	// not the same as a budget of zero. A zero budget stops the run
	// immediately; an absent one never stops it.
	CostBudget  *float64
	TokenBudget *int
	// CostWarned is the per-run cost-approaching-budget warn-once flag.
	CostWarned bool
	// CostWarnUsd is the early-warn line set by the budget gate (auto:
	// max($2.50, p90 of successful runs)). nil = the gate never ran (legacy
	// 80%-of-budget warn); Ptr(0.0) = explicitly DISABLED via
	// `budget.warn_usd: 0`. Three states, and a float64 could only hold two.
	CostWarnUsd *float64

	// -- Retry state --
	StepRetries            map[string]int
	StepTierOverrides      map[string]string
	SessionVerifyFailures  int
	SessionTierFloor       string
	FailureChain           []string
	RecoveryStepCount      int
	ConsecutiveMaxTimeouts int

	// -- Hooks & interrupts --
	// PendingContext holds the typed contributions for the next step's
	// prompt. Contributors append; the merge point drains.
	PendingContext *ContributionLedger
	// Terrain is the run-scoped terrain memory: hosts observed to HARD block
	// this run, so step N+1 does not rediscover step N's 403. Populated
	// deterministically from tool transcripts; rendered as one `terrain`
	// contribution per step. Dies with the run — promotion to a durable
	// terrain teaching reads this as its evidence source.
	Terrain *terrain.TerrainMemory
	// WorldFacts is the run-scoped world-fact ledger: non-action findings
	// declared by the executor via the completion tool's `world_facts`
	// channel. Anecdotal facts render as one `world_facts` contribution per
	// step; hypothesis-kind facts are ledgered but quarantined from prompts.
	// Rides the run checkpoint so resume/replan sees the facts, not just the
	// surviving steps.
	WorldFacts        *worldfacts.WorldFactLedger
	InterruptsApplied int
	// LastBoundaryInterrupts are human-readable descriptions of interrupts
	// applied at the most recent boundary poll — consumed by the
	// injection-trigger director evaluation. Reset each poll; empty when
	// nothing was applied.
	LastBoundaryInterrupts []string
	// Injections is the cumulative structured record of ALL interrupts
	// applied this run — carried into LoopResult.Injections / the loop log /
	// the run report so injected content stays delineated from the original
	// goal after the fact.
	Injections []pyval.Obj

	// -- Flags --
	MarchOfNinesAlert bool
	// MilestoneExpanded is Python's untyped `set`. Every live member is a
	// STEP INDEX (loop_execute adds `_would_be_step_idx`) and only `add`,
	// `in` and `len` are used, so int is the honest element type.
	MilestoneExpanded map[int]bool

	// -- Configuration (set during init, read-only after) --
	// Adapter is Python's `Any`; the llm.Adapter interface is not imported
	// here to keep this module's import graph as small as Python's.
	Adapter           any
	Verbose           bool
	DryRun            bool
	MaxIterations     int
	ContinuationDepth int
	RalphVerify       bool
	// DeferLearning: the caller (handle.py) runs closure after this loop and
	// promises to call finalize_deferred_learning() — finalize skips lesson
	// extraction and skill crystallization so they can run verdict-aware
	// instead of blind.
	DeferLearning bool
	// DeferMaintenance: the caller promises to drain
	// handle._POST_NOTIFY_MAINTENANCE after the run_completed notify, so
	// finalize registers the maintenance tail there instead of running it
	// inline. EXPLICITLY distinct from DeferLearning: the direct-CLI lanes
	// (maro run/resume) set DeferLearning=true but finalize learning
	// themselves and drain no registry — inferring maintenance deferral from
	// DeferLearning silently dropped their whole maintenance tail. Only
	// handle.py sets this.
	DeferMaintenance bool

	// -- Adaptive execution --
	StepsSinceLastCheck   int
	DirectorReplanCount   int
	DirectorBudgetCeiling int

	// StepCallback is Python's Optional[Callable]; nil is None.
	//
	// The signature is agent_loop.py:161's, spelled out:
	// `callable(step_num, step_text, summary, status)`. It was declared
	// here as `func(StepOutcome)` -- a shape no caller in the tree uses,
	// and nothing caught it because the field has no Go caller yet
	// either. A field is TWO claims and this one had no reader to
	// contradict the writer (L16).
	//
	// The THIRD argument is not a summary at every site, which is worth
	// knowing before a Go caller picks one:
	//
	//	loop_post_step.py:1123  step_summary          (the docstring's name)
	//	loop_blocked.py:540     stuck_reason or "blocked"
	//	loop_parallel.py:388    result[:120]          (the raw step output)
	//
	// The consumer is handle.py's channel live update, which renders it
	// as a summary line either way. PYTHON-side inconsistency, filed not
	// fixed: a parallel step's live line shows raw result text where a
	// sequential step's shows a summary.
	StepCallback func(stepNum int, stepText, summary, status string)
	// Channel is an optional ConversationChannel for mid-loop escalation.
	Channel        any
	InterruptQueue any
	HookRegistry   any
	PermCtx        any

	// -- Computed during init --
	AncestryContext string
	// StartedAt is a monotonic-ish wall clock in SECONDS (Python
	// `time.time()`), defaulting to 0.0 — which is a real sentinel meaning
	// "not started", not a timestamp.
	StartedAt float64
	StartTS   string
	// LoopTimeoutSecs: nil is Python's None — NO TIMEOUT. Ptr(0) would be a
	// zero-length timeout, which is a different run.
	LoopTimeoutSecs *float64
	// RepoPath is an optional target repo path for stack context injection.
	// EMPTY IS NOT A PATH: "" reaches subprocess and filesystem call sites
	// where `git -C ""` silently runs in the caller's cwd, so consumers must
	// test for "" before using it. Python has the same hole; this comment is
	// the port's answer, since changing it here would be a behaviour change
	// rather than a port.
	RepoPath string
	// The four held resources, released or merged at finalize. nil is None.
	ProjectSlot    any // interrupt.ProjectSlot; released at finalize
	RunLease       any // run_lease.RunLease; released at finalize
	RunWorktree    any // worktree.Worktree (busy_policy=worktree); merged at finalize
	ContainerClone any // worktree.ScratchClone (containerized self-dev); merged at finalize

	// warnings collects the off-vocabulary drops StampStop / StampPause send
	// to log.warning. Same reason ContributionLedger keeps its own.
	warnings []string
}

// NewLoopContext applies every dataclass default, including the five
// default_factory allocations and the four non-zero literals.
func NewLoopContext() *LoopContext {
	return &LoopContext{
		LoopStatus:            "done",
		Phase:                 PhaseInit,
		StepRetries:           map[string]int{},
		StepTierOverrides:     map[string]string{},
		PendingContext:        NewContributionLedger(),
		Terrain:               NewTerrain(),
		WorldFacts:            NewWorldFacts(),
		MilestoneExpanded:     map[int]bool{},
		MaxIterations:         40,
		DirectorBudgetCeiling: MaxRestartDepth,
	}
}

// Warnings returns the off-vocabulary notices Python logs, oldest first.
func (c *LoopContext) Warnings() []string {
	return append([]string(nil), c.warnings...)
}

// StampStop records the typed stop verdict for this run's ending.
//
// FIRST WRITE WINS: the break site that decided the stop knows the specific
// cause; later wrap-up machinery (terminal blocked-exit, finalize) must not
// overwrite it with a generic one. Evidence is BOUNDED — it is a citation,
// not a transcript. Off-vocabulary values are DROPPED (fail to unstamped,
// so status fallbacks still apply): a typo'd verdict persisting silently
// would drift past every string-matching consumer.
//
// The evidence bound is budget.Clip (context_budget.clip) at 800, which
// marks what it removed rather than slicing silently.
func (c *LoopContext) StampStop(verdict, evidence string) {
	if c.StopVerdict != "" {
		return
	}
	if !stopverdicts.IsValidStopValue(verdict) {
		c.warnings = append(c.warnings,
			"stamp_stop: off-vocabulary verdict "+pytext.Repr(verdict)+" dropped")
		return
	}
	c.StopVerdict = verdict
	c.StopEvidence = budget.Clip(evidence, 800)
}

// StampPause records the typed pause reason. First write wins, same
// contract as StampStop: the break site knows the specific cause.
// Off-vocabulary values are dropped to unstamped so the status-derived
// fallback in classify_outcome still applies.
//
// Note the asymmetry with StampStop and that it is upstream's: the pause
// reason is NOT clipped, because it is a vocabulary member rather than
// free text, and there is no evidence field beside it.
//
// EQUIVALENT MUTANT: adding a Clip here cannot change any answer, since
// every reason that reaches this line has already passed the vocabulary
// gate and the longest member is far under any sane cap. The differential
// asserts that headroom rather than assuming it, so a vocabulary that grew
// an 800-character member would say so.
func (c *LoopContext) StampPause(reason string) {
	if c.PauseReason != "" {
		return
	}
	if !stopverdicts.ValidPauseReasons[reason] {
		c.warnings = append(c.warnings,
			"stamp_pause: off-vocabulary reason "+pytext.Repr(reason)+" dropped")
		return
	}
	c.PauseReason = reason
}

// LoopStateMachine is LoopContext + phase transition enforcement.
//
// Python spells it as a dataclass SUBCLASS of LoopContext, so every context
// field is reachable through it and `ctx.set_phase(X)` validates and
// transitions in one call. Go's embedded struct is the same reachability
// with the same field set; the difference is that a *LoopContext is not a
// *LoopStateMachine, where Python's isinstance would say it is. Nothing in
// the ported surface tests that.
type LoopStateMachine struct {
	LoopContext
}

// NewLoopStateMachine applies the same defaults NewLoopContext does. It is
// a separate constructor rather than a cast, because the embedded value
// must be initialised through the one function that knows the factories.
func NewLoopStateMachine() *LoopStateMachine {
	return &LoopStateMachine{LoopContext: *NewLoopContext()}
}

// TraceFunc is the edge recorder SetPhase calls. Python imports
// `run_trace.record_edge` inside the method and swallows anything it
// raises; Go cannot import ambiently, so the recorder is injected and a nil
// one is the "import failed" arm.
type TraceFunc func(from, to, loopID string)

// SetPhase advances Phase to newPhase, returning *InvalidTransitionError on
// a bad transition.
//
// Two details are load-bearing and easy to lose:
//
//   - the phase is mutated BEFORE the edge is recorded, and `prior` is
//     captured before that. A recorder that read ctx.Phase would see the new
//     one.
//   - the record is wrapped in Python's bare `except Exception: pass`. This
//     is the one place every legal transition passes through, so recording
//     it here covers all of them at once and cannot drift from the state
//     machine — but a trace failure must never fail a transition. A nil
//     trace is that arm, and any panic from the recorder is recovered.
//
// Python passes `loop_id=getattr(self, "loop_id", None) or None`, whose
// `or None` turns an empty loop_id into None — which record_edge then reads
// as `loop_id or ""`. Both paths end at "", so the Go call passes LoopID
// straight through.
func (sm *LoopStateMachine) SetPhase(newPhase string, trace TraceFunc) error {
	allowed := allowedTransitions[sm.Phase]
	if !allowed[newPhase] {
		return &InvalidTransitionError{From: sm.Phase, To: newPhase}
	}
	prior := sm.Phase
	sm.Phase = newPhase
	if trace == nil {
		return nil
	}
	func() {
		defer func() { _ = recover() }()
		trace("phase."+prior, "phase."+newPhase, sm.LoopID)
	}()
	return nil
}
