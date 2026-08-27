// Package runtrace ports src/run_trace.py — the durable edge trace, the
// transitions a run actually took.
//
// Python's module docstring carries the why (nodes were recorded, edges
// were not; reconstruction gets the shape right and the ordering wrong)
// and it is not repeated here. What IS repeated here is every place the
// two runtimes could disagree, because this module writes rows that the
// Python readers — scripts/run_atlas, the viz server — parse.
//
// # Three shapes that are the whole port
//
//  1. **Append order under flock is authoritative.** There is deliberately
//     no sequence number, so the row's POSITION is the fact. Appends go
//     through record.AppendRawLine, which takes the same advisory flock on
//     the same sibling `.lock` file Python's file_lock.locked_append does.
//
//  2. **A dropped edge is counted, and the first drop for a run tries to
//     leave a marker IN the file.** A silently dropped edge reads
//     downstream as "not traveled", which is a false negative that looks
//     like a fact.
//
//  3. **Unknown node ids are recorded anyway, and flagged.** Losing the row
//     would be worse than carrying an id the vocabulary does not know.
//
// # The empty run-dir
//
// Python's `_resolve_run_dir` opens with `if run_dir is not None: return
// Path(run_dir)`, and `Path("")` is `PosixPath('.')` — so `record_edge(...,
// run_dir="")` writes `./build/trace.jsonl` under whatever the process CWD
// happens to be. An empty path is not "no path". That is a live hazard in
// this repo's own history (a test created a linked worktree of the maro
// repo by handing an empty root to `git -C`), so the Go signature refuses
// to spell the two states with one `string`: RunDir is a `*string`, nil is
// Python's None, and a non-nil empty string is normalised to "." at the
// door — the same shape internal/runs.WithRunDir already uses, for the same
// reason, and pinned in the differential. See EdgeOpts.RunDir.
//
// # Not ported
//
// Python's `_log_warn` writes to the `run_trace` logger. This port has no
// logging hierarchy, so ReadTrace RETURNS its warnings (the convention
// internal/llm and internal/loop already use). The message texts are
// reproduced except for `{exc!r}`, which is a CPython exception repr and
// has no Go counterpart — see ReadTrace.
package runtrace

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/runs"
	"github.com/slycrel/maro-orchestration/go/internal/scrub"
)

// TraceFilename is Python TRACE_FILENAME.
const TraceFilename = "trace.jsonl"

// EvidenceCap is Python EVIDENCE_CAP: one announced cut for every string
// attribute, applied centrally in RecordEdge rather than as a bare slice at
// each call site. A trace row is evidence, so the cut goes through
// budget.Clip (context_budget.clip), which marks what it removed.
const EvidenceCap = 400

// --------------------------------------------------------------------------
// Node vocabulary. These ids are the shared contract between the recorder
// and every consumer, so the sets are ported member for member and
// TestTheNodeVocabularyMatchesCPython does the set difference in BOTH
// directions — a member this port is MISSING is a value the Python runtime
// writes and this one flags as unknown; a member this port INVENTS is a
// node this runtime writes without the flag that would have made it
// visible. Neither shape produces a crash.
// --------------------------------------------------------------------------

// PhaseNodes are the looptypes.LoopPhase transitions, recorded from the
// state machine itself.
var PhaseNodes = frozenset(
	"phase.init", "phase.decompose", "phase.pre_flight", "phase.parallel",
	"phase.prepare", "phase.execute", "phase.finalize",
)

var IntakeNodes = frozenset(
	"intake.arrive", "intake.cli", "intake.listener", "intake.queue",
	"intake.scheduler", "intake.navigator", "intake.nav_escalate",
	"intake.guard_refuse", "intake.open_run",
)

var RouteNodes = frozenset(
	"route.classify", "route.now", "route.now_verify", "route.agenda",
	"route.clarity", "route.clarify_stop", "route.rewrite", "route.persona",
	"route.recall", "route.scope_ok", "route.scope_fail", "route.scope_skip",
)

var PlanNodes = frozenset(
	"plan.loop_created", "plan.fence", "plan.recall", "plan.skills",
	"plan.decompose", "plan.cuts", "plan.manifest", "plan.busy_refused",
	"plan.budget_gate", "plan.killswitch", "plan.cost_gate",
)

var ExecNodes = frozenset(
	"exec.step", "exec.session_reuse", "exec.inject", "exec.boundary",
	"exec.reanchor", "exec.validate", "exec.ralph", "exec.advisor",
	"exec.director", "exec.navigator", "exec.too_broad", "exec.scavenge",
	"exec.write_fence", "exec.fabrication", "exec.blocked", "exec.retry",
	"exec.redecompose", "exec.split", "exec.budget_ladder", "exec.timeout",
	"exec.missing_input", "exec.budget_break", "exec.nav_escalate",
	"exec.stuck", "exec.never_ran", "exec.parallel",
)

var FinNodes = frozenset(
	"fin.result", "fin.partial", "fin.checkpoint", "fin.diagnose",
	"fin.world_facts", "fin.auto_recovery", "fin.stop_verdict", "fin.pause",
)

var VerifyNodes = frozenset(
	"verify.plan", "verify.closure", "verify.audit", "verify.downgrade",
	"verify.restart", "verify.provenance", "verify.stamp", "verify.contested",
)

var GateNodes = frozenset(
	"gate.verdict", "gate.crossref", "gate.claims", "gate.pass",
	"gate.escalate", "gate.overruled", "gate.escalate_rerun",
)

var CloseNodes = frozenset(
	"close.curate", "close.learning", "close.no_verdict", "close.stranded",
	"close.terminal",
)

// TermNodes are the terminal outcomes. `close.terminal -> term.<class>`
// closes every run that reaches curation; runs that die earlier terminate
// at their own stop node.
var TermNodes = frozenset(
	// run_curation.classify_outcome's ladder
	"term.success", "term.partial", "term.failed", "term.done-unverified",
	"term.done-not-achieved", "term.achieved-not-done", "term.interrupted",
	"term.unknown",
	// `done-verdict-pending` is the answer-first early close: curation runs
	// before closure has produced a verdict, so the first of the two closes
	// legitimately terminates here and a later one supersedes it.
	"term.done-verdict-pending",
	// raw statuses, used when a run has no card (pre-2026-07) or dies early
	"term.clarification_needed", "term.stranded", "term.error",
	"term.incomplete", "term.done", "term.stuck", "term.refused_busy",
)

// MetaNodes is bookkeeping about the trace itself.
var MetaNodes = frozenset("trace.degraded")

// Nodes is the union, Python's `NODES`.
var Nodes = union(PhaseNodes, IntakeNodes, RouteNodes, PlanNodes, ExecNodes,
	FinNodes, VerifyNodes, GateNodes, CloseNodes, TermNodes, MetaNodes)

func frozenset(vals ...string) map[string]bool {
	out := make(map[string]bool, len(vals))
	for _, v := range vals {
		out[v] = true
	}
	return out
}

func union(sets ...map[string]bool) map[string]bool {
	out := map[string]bool{}
	for _, s := range sets {
		for k := range s {
			out[k] = true
		}
	}
	return out
}

// --------------------------------------------------------------------------

// The module-level drop bookkeeping. Python's `_lock` is a
// threading.Lock guarding two module dicts; this is the same state with
// the same lifetime (process-wide, never reset), because a consumer asking
// dropped_count() after the fact is asking about the process, not about a
// call.
var (
	mu             sync.Mutex
	dropped        = map[string]int{}
	degradedMarked = map[string]bool{}
)

// now is Python `_now()` — datetime.now(timezone.utc).isoformat().
var now = func() string { return pyval.NowISO(time.Now().UTC()) }

// resolveRunDir is Python `_resolve_run_dir(run_dir, handle_id)`.
//
// found=false is Python's None. The three arms are in Python's order and
// the first one is the hazard: `run_dir is not None` short-circuits before
// anything else, INCLUDING before the empty string is examined, so a caller
// that passes "" gets `Path("")` == "." and never reaches the run context.
// EdgeOpts.RunDir carries that distinction; the normalisation to "." is
// done here, once, at the only place that reads the pointer.
//
// Python's `runs.run_dir(handle_id)` is a pure path construction
// (`runs_root() / f"{handle_id}-{nickname(handle_id)}"`), which is
// runs.Dir; it cannot return None, so Python's `rd is not None` guard is
// dead and only the `.exists()` half is live. Kept in the same shape so the
// two files read alike.
func resolveRunDir(ctx context.Context, ws string, runDir *string, handleID string) (string, bool) {
	if runDir != nil {
		if *runDir == "" {
			return ".", true
		}
		return *runDir, true
	}
	// Python wraps the rest in try/except Exception -> None. The Go
	// equivalents return values rather than raising, so there is nothing to
	// catch; the arm is noted rather than faked.
	if handleID != "" {
		rd := runs.Dir(ws, handleID)
		if _, err := os.Stat(rd); err == nil {
			return rd, true
		}
	}
	if cur := runs.CurrentRunDir(ctx); cur != "" {
		return cur, true
	}
	return "", false
}

// Enabled is Python `_enabled()` — `bool(config.get("trace.enabled", True))`.
//
// pyval.Truthy, NOT config.GetBool: Python spells this `bool(...)`, which is
// the LANGUAGE's truthiness, where config.get_bool consults a set of
// recognized strings. They disagree on the ordinary spelling an operator
// would reach for — `trace.enabled: "false"` is a non-empty string, so
// bool() says TRUE and get_bool says false. The gate here is bool().
//
// A config failure must not silence the record; recording is the default
// and the safer direction. This port's config.Load returns warnings instead
// of raising, so the "except -> True" arm has no live path — an unreadable
// or unparseable file yields an empty cfg, and an absent key returns the
// default, which is the same True.
func Enabled(cfg map[string]any) bool {
	return pyval.Truthy(config.GetRaw(cfg, "trace.enabled", true))
}

// clipAttr is Python `_clip(v)`: bound one attribute value, announcing the
// cut, never a bare slice.
//
// `len(v) <= EVIDENCE_CAP` is a CODE-POINT count in Python, so the pass-
// through boundary is measured in runes here too. A non-string passes
// through untouched — including a 10,000-element list, which this cap does
// not claim to bound.
func clipAttr(v any) any {
	s, ok := v.(string)
	if !ok {
		return v
	}
	if len([]rune(s)) <= EvidenceCap {
		return s
	}
	return budget.Clip(s, EvidenceCap)
}

// noteDrop is Python `_note_drop(rd)`: count a lost edge and, once per run,
// try to say so in the file itself.
//
// found=false is Python's `rd is None`, whose key is the literal
// "<no-run-dir>" — a real key in the same dict, so a process that never
// pinned a run still has a countable drop total.
func noteDrop(rd string, found bool) {
	key := "<no-run-dir>"
	if found {
		key = rd
	}
	mu.Lock()
	dropped[key] = dropped[key] + 1
	first := !degradedMarked[key]
	degradedMarked[key] = true
	mu.Unlock()
	if !first || !found {
		return
	}
	// Best-effort, and Python's bare `except Exception: pass` is the point:
	// the marker is a courtesy to the reader, not a second thing that can
	// fail a run. Note this row carries NO loop_id key at all — Python
	// builds a different literal here, not record_edge's row minus a field,
	// and a reader keying on loop_id must handle its absence.
	line, err := dumps(pyval.Obj{
		{Key: "ts", Val: now()},
		{Key: "from", Val: "trace.degraded"},
		{Key: "to", Val: "trace.degraded"},
		{Key: "attrs", Val: pyval.Obj{{Key: "reason", Val: "an edge failed to record; this " +
			"trace is incomplete"}}},
	})
	if err != nil {
		return
	}
	_ = record.AppendRawLine(filepath.Join(rd, "build", TraceFilename), []byte(line))
}

// dumps renders a value the way a bare `json.dumps(...)` does — default
// separators `", "` and `": "`, ensure_ascii, Python float spelling — while
// keeping a pyval.Obj in its INSERTION order.
//
// pyjson.Ordered is the same renderer for one level; it takes a Go map, and
// a Go map cannot hold the nested `attrs` order. pyjson.Value's own nested
// arm names the sorted-keys divergence it accepts for that reason. This
// walk keeps the order at every level instead, so an attrs dict written by
// this runtime is byte-identical to CPython's rather than merely equal as a
// parsed value.
//
// SCOPE, named rather than faked: Python's call passes `default=str`, which
// stringifies anything json cannot encode. There is no Go counterpart to
// `str(obj)` for an arbitrary object, so a value pyjson refuses (a struct, a
// non-finite float) makes the whole write fail here and be counted as a
// drop, where CPython would have written a stringified row. Attrs values are
// meant to be JSON-ish; see EdgeOpts.Attrs.
func dumps(v any) (string, error) {
	switch t := v.(type) {
	case pyval.Obj:
		var sb strings.Builder
		sb.WriteByte('{')
		for i, kv := range t {
			if i > 0 {
				sb.WriteString(", ")
			}
			k, err := pyjson.String(kv.Key)
			if err != nil {
				return "", err
			}
			val, err := dumps(kv.Val)
			if err != nil {
				return "", err
			}
			sb.WriteString(k)
			sb.WriteString(": ")
			sb.WriteString(val)
		}
		sb.WriteByte('}')
		return sb.String(), nil
	case pyval.List:
		return dumpsSeq(len(t), func(i int) any { return t[i] })
	case []any:
		return dumpsSeq(len(t), func(i int) any { return t[i] })
	case []string:
		return dumpsSeq(len(t), func(i int) any { return t[i] })
	default:
		return pyjson.Value(v)
	}
}

func dumpsSeq(n int, at func(int) any) (string, error) {
	var sb strings.Builder
	sb.WriteByte('[')
	for i := 0; i < n; i++ {
		if i > 0 {
			sb.WriteString(", ")
		}
		one, err := dumps(at(i))
		if err != nil {
			return "", err
		}
		sb.WriteString(one)
	}
	sb.WriteByte(']')
	return sb.String(), nil
}

// DroppedCount is Python `dropped_count(run_dir=None)` — edges this process
// failed to record for a run (0 when healthy).
//
// It RESOLVES its argument the same way RecordEdge does, which is worth
// saying out loud: calling it with no run-dir does not read the
// "<no-run-dir>" bucket unless the context has no run pinned either.
func DroppedCount(ctx context.Context, ws string, runDir *string) int {
	rd, found := resolveRunDir(ctx, ws, runDir, "")
	key := "<no-run-dir>"
	if found {
		key = rd
	}
	mu.Lock()
	defer mu.Unlock()
	return dropped[key]
}

// EdgeOpts is Python record_edge's keyword-only tail.
type EdgeOpts struct {
	// LoopID is Python `loop_id: Optional[str]`. It is only ever read as
	// `loop_id or ""`, so None and "" are indistinguishable and one Go
	// string carries both.
	LoopID string
	// RunDir is Python `run_dir=None`. nil is None. A non-nil pointer to ""
	// is Python's `Path("")`, which is ".", NOT "no run dir" — see the
	// package doc. Pass nil to mean absent.
	RunDir *string
	// HandleID is Python `handle_id: Optional[str]`. Read as `if handle_id:`,
	// so None and "" are again indistinguishable.
	HandleID string
	// Attrs is Python's `**attrs`, whose iteration order is the CALLER's
	// keyword order and is observable in the emitted row. pyval.Obj is the
	// ordered spelling; a Go map has no order and would sort.
	//
	// Values must be JSON-ish (string, bool, int, float64, nil, []string,
	// []any, pyval.List, pyval.Obj). See dumps for the `default=str` scope
	// limit that follows from Go having no `str(obj)`.
	Attrs pyval.Obj
}

// RecordEdge is Python `record_edge` — append one traversed edge to the
// run's `build/trace.jsonl`.
//
// Returns true when the row was written. Never returns an error: a trace
// failure must not change what a run does, which is Python's bare
// `except Exception`. Unknown node ids are still recorded (losing the row
// would be worse) but flagged in an `unknown_node` list.
//
// Statement order is Python's, and two details in it are load-bearing:
//
//   - the `unknown` list is built by a comprehension over the TUPLE
//     `(frm, to)`, so an edge from an unknown node to ITSELF lists that id
//     TWICE. A set would not, and `trace.degraded -> trace.degraded` is a
//     real self-edge this module writes.
//   - `if attrs:` is Python truthiness on the kwargs dict, so an EMPTY
//     attrs map omits the key entirely rather than writing `"attrs": {}`.
func RecordEdge(ctx context.Context, cfg map[string]any, ws string, frm, to string, o EdgeOpts) bool {
	if !Enabled(cfg) {
		return false
	}
	rd, found := resolveRunDir(ctx, ws, o.RunDir, o.HandleID)
	if !found {
		// No run context — nothing to attach the edge to. Counted, not
		// raised: some call sites legitimately run outside a run.
		noteDrop("", false)
		return false
	}
	row := pyval.Obj{
		{Key: "ts", Val: now()},
		{Key: "loop_id", Val: o.LoopID},
		{Key: "from", Val: frm},
		{Key: "to", Val: to},
	}
	var unknown []string
	for _, n := range []string{frm, to} {
		if !Nodes[n] {
			unknown = append(unknown, n)
		}
	}
	if unknown != nil {
		row = append(row, pyval.Field{Key: "unknown_node", Val: unknown})
	}
	if len(o.Attrs) > 0 {
		clipped := make(pyval.Obj, 0, len(o.Attrs))
		for _, kv := range o.Attrs {
			clipped = append(clipped, pyval.Field{Key: kv.Key, Val: clipAttr(kv.Val)})
		}
		row = append(row, pyval.Field{Key: "attrs", Val: clipped})
	}
	// scrub.Walk over the ORDERED spelling: its pyval.Obj arm reproduces
	// Python's dict-comprehension collapse (a key keeps the ordinal of its
	// FIRST appearance and the value of its LAST), which two attrs keys
	// scrubbing to the same "[REDACTED]" would otherwise turn into a JSON
	// object with a duplicate key. Walking a Go map instead would also lose
	// the attrs order the caller's keyword order set.
	scrubbed, ok := scrub.Walk(row, scrub.Secrets).(pyval.Obj)
	if !ok {
		noteDrop(rd, true)
		return false
	}
	line, err := dumps(scrubbed)
	if err != nil {
		noteDrop(rd, true)
		return false
	}
	if err := record.AppendRawLine(filepath.Join(rd, "build", TraceFilename),
		[]byte(line)); err != nil {
		noteDrop(rd, true)
		return false
	}
	return true
}

// RecordPath is Python `record_path(nodes, ...)` — record a straight run of
// edges (a -> b -> c). Returns rows written.
//
// `[n for n in nodes if n]` drops FALSY entries first, so an empty string in
// the middle of a path joins its neighbours into one edge rather than
// producing two broken ones. The sum is over a generator with an `if`, which
// still CALLS RecordEdge for every pair — the return value filters the
// count, not the write.
func RecordPath(ctx context.Context, cfg map[string]any, ws string, nodes []string, o EdgeOpts) int {
	var seq []string
	for _, n := range nodes {
		if n != "" {
			seq = append(seq, n)
		}
	}
	written := 0
	for i := 0; i+1 < len(seq); i++ {
		if RecordEdge(ctx, cfg, ws, seq[i], seq[i+1], o) {
			written++
		}
	}
	return written
}

// ReadTrace is Python `read_trace(run_dir, counted=...)` — read a run's
// trace in recorded order.
//
// Uses record.LoadsClean (jsonl_utils.loads_clean) so a byte-tainted row is
// skipped rather than laundered into legitimate-looking content. A skipped
// row is counted and named in a warning, never silently swallowed: a trace
// that quietly returns 40 of 41 edges is indistinguishable from a run that
// took 40.
//
// DIVERGENCE, pinned in the differential: Python's warnings interpolate
// `{exc!r}`, the repr of a CPython exception object
// (`JSONDecodeError('Expecting value: line 1 column 1 (char 0)')`). Go's
// errors have no such repr and this port does not invent one, so the
// message TEXT of the two warnings carrying an exception differs after the
// colon. The third warning — the count line — is byte-identical.
//
// The three-value return replaces Python's `counted` flag: a caller that
// wants Python's uncounted shape ignores the second value.
func ReadTrace(runDir string) (rows []map[string]any, skipped int, warnings []string) {
	p := filepath.Join(runDir, "build", TraceFilename)
	// jsonl_utils.store_text is `path.read_bytes().decode("utf-8",
	// errors="surrogateescape")`. Go strings hold arbitrary bytes, so the
	// read IS the decode; the surrogate-escaped bytes reach LoadsClean, which
	// refuses that line exactly as Python's does.
	raw, err := os.ReadFile(p)
	if err != nil {
		if os.IsNotExist(err) {
			// FileNotFoundError -> ([], 0), no warning.
			return nil, 0, nil
		}
		return nil, 0, []string{"run_trace: cannot read " + p + ": " + err.Error()}
	}
	// pytext.SplitLines, not strings.Split: Python's str.splitlines() breaks
	// on ten separators (\v \f \x1c \x1d \x1e \x85 U+2028 U+2029 among them),
	// and a JSON string value containing one of them is split by CPython into
	// two fragments — neither of which parses — where a "\n" split would keep
	// the row whole and admit it.
	for _, line := range pytext.SplitLines(string(raw)) {
		line = pytext.Strip(line)
		if line == "" {
			continue
		}
		row, lerr := record.LoadsClean(line)
		if lerr != nil {
			skipped++
			warnings = append(warnings,
				"run_trace: skipped an unreadable row in "+p+": "+lerr.Error())
			continue
		}
		rows = append(rows, row)
	}
	if skipped > 0 {
		warnings = append(warnings, "run_trace: "+p+" has "+
			itoa(skipped)+" unreadable row(s); returning "+
			itoa(len(rows))+" edge(s) — the trace is incomplete")
	}
	return rows, skipped, warnings
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

// ResetDropsForTest clears the process-wide drop bookkeeping.
//
// It exists because the state is deliberately process-wide (see the vars)
// and a differential that runs several scenarios in one binary would
// otherwise see the FIRST scenario's marker suppress every later one — the
// `first` flag is once per key for the life of the process, in both
// runtimes, and a fresh CPython subprocess gets that for free where a Go
// test does not.
func ResetDropsForTest() {
	mu.Lock()
	defer mu.Unlock()
	dropped = map[string]int{}
	degradedMarked = map[string]bool{}
}

// SetNowForTest replaces the timestamp source and returns a restore func.
// A trace row's first field is a wall-clock instant; a byte differential
// against CPython cannot compare rows without pinning it on both sides.
func SetNowForTest(fn func() string) func() {
	prev := now
	now = fn
	return func() { now = prev }
}
