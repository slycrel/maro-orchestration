// Package handlequeue is handle_queue.py: the seam between the task store
// and the things that actually run work.
//
// Three entry points, and they are not equally portable:
//
//   - EnqueueGoal / EnqueueGoals — the user-facing "drop goals here" API.
//     Fully here.
//   - DrainTaskStore — claim queued tasks and route each one. Fully here.
//   - HandleTask — the router. Its `loop_escalation` branch is fully here;
//     its `loop_continuation` and default branches reach into agent_loop,
//     recall, rerun_identity, navigator_shadow and handle() itself, none of
//     which this port has reached. Those two go through Options.Fallback,
//     and a nil Fallback answers ErrLaneUnported rather than silently doing
//     nothing — a drain that quietly completes a continuation it never ran
//     is worse than one that fails it loudly.
package handlequeue

import (
	"context"
	"errors"
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/director"
	"github.com/slycrel/maro-orchestration/go/internal/dispatch"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/notify"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/tasks"
)

// DefaultSources is drain_task_store's `sources` parameter default, in its
// declared order. Membership is Python's `in` over a TUPLE — `==` against
// each element, which never raises — so a numeric source matches nothing
// and a task with no `source` key is None and matches nothing either.
var DefaultSources = []string{"loop_continuation", "loop_escalation", "user_goal"}

// DefaultMaxTasks is the cap that keeps one drain from monopolizing a
// heartbeat tick.
const DefaultMaxTasks = 3

// ErrLaneUnported is HandleTask's answer for the two branches this port has
// not reached, when no Fallback is supplied.
var ErrLaneUnported = errors.New("handle_queue: lane not ported")

// ErrUnhashableJobID is Python's TypeError from `task.get("job_id") in
// job_ids`, where job_ids is a SET.
//
// It is its own type because it is the one failure in the whole drain that
// Python does NOT catch: the membership test lives in a list comprehension
// ABOVE the try, so a single task file with a list or dict job_id takes the
// entire drain down and the heartbeat is what sees it. The tuple membership
// three lines away never raises. Two membership tests, different failure
// semantics, and only one of them is guarded.
type ErrUnhashableJobID struct{ TypeName string }

func (e *ErrUnhashableJobID) Error() string {
	return fmt.Sprintf("cannot use '%s' as a set element (unhashable type: '%s')",
		e.TypeName, e.TypeName)
}

// PyClass names the CPython exception this stands for, so the port's
// `except` helpers can classify it (pyval.ClassOf).
func (e *ErrUnhashableJobID) PyClass() string { return "TypeError" }

// Statused and Resulted are the two attributes drain_task_store reads off
// whatever handle_task returned, both through getattr WITH a default.
//
// They are interfaces rather than struct fields because the duck typing is
// load-bearing: director.EscalationDecision has NEITHER attribute, so an
// escalation task always completes with result_status "done" and can never
// take the fail() path, no matter what the director decided. A struct with
// a Status field would have to invent a value there.
type Statused interface{ StatusAttr() string }

// Resulted is `getattr(_res, "result", "")`.
type Resulted interface{ ResultAttr() any }

// resultStatus is `getattr(_res, "status", "done") or "done"`. The trailing
// `or` matters: an object that HAS a status of "" still gets "done".
func resultStatus(v any) string {
	if s, ok := v.(Statused); ok {
		if got := s.StatusAttr(); got != "" {
			return got
		}
	}
	return "done"
}

// resultText is `str(getattr(_res, "result", "") or _res_status)[:500]`.
// pyval.Clip is that slice — rune-wise, like Python's — and not the
// context_budget honest-clip the same word names elsewhere.
func resultText(v any, status string) string {
	var raw any = ""
	if r, ok := v.(Resulted); ok {
		raw = r.ResultAttr()
	}
	if !pyval.Truthy(raw) {
		raw = status
	}
	return pyval.Clip(pyval.Str(raw), 500)
}

// Options are the keyword arguments handle_task and drain_task_store share.
type Options struct {
	Adapter llm.Adapter
	DryRun  bool
	Verbose bool

	// Config and Notify are passed in for the same reason
	// director.EscalationOptions passes them: notify.Options with a nil Cfg
	// falls back to the OPERATOR's ~/.maro/config.yml, which on this box
	// registers a hook that messages Telegram and ssh's to another host.
	Config map[string]any
	Notify notify.Options

	// Channel rides through to the director's low-confidence advisory.
	Channel director.LowConfidenceNotifier

	// Fallback handles `loop_continuation` and the default lane, with the
	// same task and options HandleTask received.
	Fallback func(ctx context.Context, ws string, task pyval.Obj, o Options) (any, error)

	Log func(format string, args ...any)
}

func (o Options) logf(format string, args ...any) {
	if o.Log != nil {
		o.Log(format, args...)
	}
}

// --- the router ---------------------------------------------------------

// HandleTask routes one task_store task by its `source`.
//
// It returns whatever the branch returned, because that is what Python
// does — the caller reads attributes off the result through getattr, and
// the escalation branch's return value has none of them.
func HandleTask(ctx context.Context, ws string, task pyval.Obj, o Options) (any, error) {
	// RAW, all three. Python does `task.get(k, default)` with no coercion
	// and spells each one with str() at the point of use — and `source`
	// specifically rides into the emit payload below as `"source": source`,
	// so spelling it here would change a durable row for a numeric source.
	source := taskGet(task, "source", "")
	reason := taskGet(task, "reason", "")
	jobIDRaw := taskGet(task, "job_id", "unknown")

	// `int(...)` inside `except (TypeError, ValueError)`, which is the
	// NARROW tuple: a non-numeric depth is 0, but an infinity is an
	// OverflowError that leaves handle_task entirely — into the drain's
	// blanket except, which FAILS the task. Only the log lines in this
	// function read the value; the escalation branch re-derives depth from
	// the raw field, deliberately, and the two disagree for a float — this
	// one says 2 where the director says "2.0".
	depth, raised := pyval.IntCaught(taskGet(task, "continuation_depth", 0), 0)
	if raised != nil {
		return nil, raised
	}

	// Eq, not `Str(source) == ...`. The two cannot be told apart by any
	// value a task file can hold — no JSON scalar spells "loop_escalation"
	// under str() without BEING it — so the mutation is recorded as
	// unobservable rather than pinned. Eq is still the right operator:
	// Python compares the raw value, and the day the queue carries
	// something whose str() collides, only Eq is right.
	if pyval.Eq(source, "loop_escalation") {
		o.logf("handle_task routing escalation job_id=%s depth=%d",
			pyval.Str(jobIDRaw), depth)
		esc, err := director.HandleEscalation(ctx, ws, task,
			director.EscalationOptions{
				Adapter: o.Adapter, DryRun: o.DryRun, Verbose: o.Verbose,
				Channel: o.Channel, Config: o.Config, Notify: o.Notify,
				Log: o.Log,
			})
		// The call is NOT inside a try here. An escalation that raises —
		// the OverflowError from `int(confidence)` being the reachable
		// one — propagates out of handle_task and the drain fails the
		// task, which is why this branch returns the error rather than a
		// zero decision.
		if err != nil {
			return nil, err
		}
		// "surface" means "for operator review", and that review only
		// happens if the operator is told. continue/narrow/close are
		// internal dispositions and say nothing.
		if esc.Action == "surface" && !o.DryRun {
			surfaceEmit(ctx, ws, task, esc, reason, source, jobIDRaw, o)
		}
		return esc, nil
	}

	if pyval.Eq(source, "loop_continuation") {
		o.logf("handle_task routing continuation job_id=%s depth=%d",
			pyval.Str(jobIDRaw), depth)
	} else {
		// `source or "unknown"` — truthiness, so an empty string, a 0 and
		// a False all print "unknown" while a numeric source prints itself.
		o.logf("handle_task routing %s job_id=%s via handle()",
			pyval.Str(orUnknown(source)), pyval.Str(jobIDRaw))
	}
	if o.Fallback != nil {
		return o.Fallback(ctx, ws, task, o)
	}
	return nil, fmt.Errorf("%w: source=%v", ErrLaneUnported, pyval.Plain(source))
}

func orUnknown(v any) any {
	if pyval.Truthy(v) {
		return v
	}
	return "unknown"
}

// surfaceEmit is the whole `except Exception: pass` block around the
// escalation notify. EVERY step in it can fail the same way Python's can,
// and the failure is always the same: no event at all.
func surfaceEmit(ctx context.Context, ws string, task pyval.Obj,
	esc director.EscalationDecision, reason, source, jobIDRaw any, o Options) {

	// `task.get("origin") or {}` then `.get(...)`. A truthy NON-mapping
	// origin raises AttributeError on `.get`, and that raise is inside the
	// same swallowing try — so a task whose origin is the string "h-1"
	// emits nothing at all, rather than emitting with an empty handle_id.
	originRaw := taskGet(task, "origin", nil)
	var handleID string
	if pyval.Truthy(originRaw) {
		org, isMapping := asObj(originRaw)
		if !isMapping {
			o.logf("handle_task surface emit skipped: '%s' object has no "+
				"attribute 'get'", pyval.TypeName(originRaw))
			return
		}
		if v, _ := org.Get("parent_handle_id"); pyval.Truthy(v) {
			handleID = pyval.Str(v)
		}
	}

	// §9.6: the director's summary_for_user IS the ask when it exists. It
	// has its OWN inner try in Python, so a decision_line failure costs the
	// decision line and not the event — DecisionLine here cannot fail, so
	// that try has nothing to reproduce.
	decisionReason := esc.SummaryForUser
	if decisionReason == "" {
		decisionReason = esc.Reasoning
	}
	decision := notify.DecisionLine("director_escalation", decisionReason, "")

	// reason[:500] is a BARE Python slice on a value the queue holds raw.
	// A dict reason raises KeyError(slice(...)), an int raises TypeError,
	// and a LIST slices happily and rides in as a list. All three are
	// reachable: the escalation lane's own continue branch re-enqueues the
	// parent's raw `reason`.
	goal, sliceErr := pyval.SliceHead(reason, 500)
	if sliceErr != nil {
		o.logf("handle_task surface emit skipped: %s", sliceErr)
		return
	}

	notify.EmitOrdered(ctx, ws, "escalation", pyval.Obj{
		{Key: "handle_id", Val: handleID},
		{Key: "goal", Val: goal},
		{Key: "status", Val: "surfaced"},
		{Key: "summary", Val: esc.SummaryForUser},
		{Key: "reason", Val: esc.Reasoning},
		{Key: "job_id", Val: jobIDRaw},
		{Key: "source", Val: source},
		{Key: "point", Val: "director_escalation"},
		{Key: "decision", Val: decision},
	}, o.Notify)
}

// --- drain --------------------------------------------------------------

// DrainOptions are drain_task_store's own keyword arguments.
type DrainOptions struct {
	// MaxTasks is Python's `max_tasks: int = 3`. Zero means ZERO — Python
	// slices `queued[:0]` and processes nothing — so "not passed" needs its
	// own flag rather than borrowing the zero value. Silently promoting 0
	// to 3 would run three tasks for a caller that asked for none.
	MaxTasks    int
	HasMaxTasks bool

	// Sources nil is "not passed" and resolves to DefaultSources. An empty
	// non-nil slice matches nothing, which is what Python's `sources=()`
	// does.
	Sources []string

	// JobIDs is Python's `job_ids: Optional[set]`. nil is None — "drain
	// anything matching sources". A non-nil EMPTY set is an empty SET and
	// drains nothing, which is a different thing, and the substrate
	// dispatch contract depends on the difference: a dispatch must run
	// exactly what it enqueued, never an older queued task whose notify
	// event the substrate would misattribute. Go distinguishes the two
	// natively, so there is no companion flag here — one would be a choice
	// with no observable answer.
	//
	// BUILD IT WITH NewJobIDSet. Its keys are pyval.HashKey outputs, not
	// raw ids: a Python set holds 4242 and "4242" as different elements,
	// and the drain's membership test has to as well, so the id "job-1" is
	// stored under "s:job-1". A map literal of raw ids compiles, type-checks
	// and matches NOTHING — the drain would report zero queued tasks and
	// look like an empty queue rather than a lookup miss. There is no caller
	// yet, which is why the trap was still latent (adversarial r11 round 9,
	// LOW); the named type and constructor are here so the first one cannot
	// spell it wrong.
	JobIDs JobIDSet
}

// JobIDSet is the drain's `job_ids` set, keyed by pyval.HashKey rather than
// by the raw id. Named so it cannot be confused with an ordinary
// map[string]bool of ids at a call site.
type JobIDSet map[string]bool

// NewJobIDSet builds the set from raw job-id values — the ids as they
// appear in a task row, of whatever type they arrived as.
//
// An UNHASHABLE id (a list, a dict) is refused here rather than silently
// dropped, because Python refuses it too: `{[]}` is a TypeError at
// construction, and a set that quietly lost an element would make the drain
// skip exactly the task the caller asked for.
//
// The empty call is meaningful: NewJobIDSet() is a non-nil empty set, which
// drains nothing. Leave DrainOptions.JobIDs nil for "no filter".
func NewJobIDSet(ids ...any) (JobIDSet, error) {
	out := JobIDSet{}
	for _, id := range ids {
		key, hashable := pyval.HashKey(id)
		if !hashable {
			return nil, &ErrUnhashableJobID{TypeName: pyval.TypeName(id)}
		}
		out[key] = true
	}
	return out, nil
}

// NewJobIDSetOfStrings is NewJobIDSet for the ordinary case, where every id
// is a string and no error is possible.
func NewJobIDSetOfStrings(ids ...string) JobIDSet {
	out := JobIDSet{}
	for _, id := range ids {
		key, _ := pyval.HashKey(id) // a string is always hashable
		out[key] = true
	}
	return out
}

func (d DrainOptions) maxTasks() int {
	if !d.HasMaxTasks {
		return DefaultMaxTasks
	}
	return d.MaxTasks
}

func (d DrainOptions) sources() []string {
	if d.Sources == nil {
		return DefaultSources
	}
	return d.Sources
}

// DrainTaskStore claims and processes queued tasks, returning how many were
// processed.
//
// Two errors leave here, and both leave Python too: the unhashable-job-id
// TypeError from the comprehension, and whatever list_tasks itself raised.
// Python's only guard at the top is `except ImportError` around the module
// IMPORT — list_tasks is CALLED one line below it, outside every try — so a
// queue holding one non-mapping row aborts the drain and the heartbeat sees
// it. Swallowing that would turn a broken queue into a silently idle one.
//
// Everything else is best-effort and logged, exactly as Python has it —
// including the case where complete() itself fails, which still counts the
// task as processed because `processed += 1` sits AFTER that inner try
// rather than inside it.
func DrainTaskStore(ctx context.Context, ws string, o Options, d DrainOptions) (int, error) {
	all, err := tasks.List(ws, "queued")
	if err != nil {
		return 0, err
	}

	queued := []pyval.Obj{}
	for _, raw := range all {
		// list_tasks with a status filter has already called `.get` on
		// every row it returned, so a non-mapping cannot reach here — it
		// raised one frame up. The check is the assertion of that, not a
		// second policy: if it ever fires, the filter contract changed.
		t, ok := asObj(raw)
		if !ok {
			return 0, &pyval.PyErr{Class: "AttributeError",
				Msg: fmt.Sprintf("'%s' object has no attribute 'get'",
					pyval.TypeName(raw))}
		}
		src, _ := t.Get("source") // no default: a missing key is None
		if !inTuple(src, d.sources()) {
			continue
		}
		// `and` short-circuits, so this membership is never reached for a
		// row whose source did not match — which is the difference between
		// a hostile job_id aborting the drain and being skipped.
		if d.JobIDs != nil {
			jid, _ := t.Get("job_id")
			key, hashable := pyval.HashKey(jid)
			if !hashable {
				return 0, &ErrUnhashableJobID{TypeName: pyval.TypeName(jid)}
			}
			if !d.JobIDs[key] {
				continue
			}
		}
		queued = append(queued, t)
	}
	if len(queued) == 0 {
		return 0, nil
	}

	o.logf("drain_task_store: %d queued task(s) to process", len(queued))
	processed := 0

	for _, task := range queued[:listBound(len(queued), d.maxTasks())] {
		jobIDRaw := taskGet(task, "job_id", "unknown")
		// `claim(job_id)` and `fail(job_id)` are handed the RAW value, and
		// both use it only to spell a path with an f-string. Str is that
		// same spelling, so the file both runtimes open is the same one.
		//
		// `complete(job_id)` is NOT in that set: it also compares the value
		// to other tasks' blocked_by entries, so it gets the raw value.
		jobID := pyval.Str(jobIDRaw)
		if _, err := tasks.Claim(ws, jobID, 0); err != nil {
			o.logf("drain_task_store: failed to claim %s: %s", jobID, err)
			continue
		}

		res, err := HandleTask(ctx, ws, task, o)
		if err != nil {
			o.logf("drain_task_store: task %s failed: %s", jobID, err)
			if _, ferr := tasks.Fail(ws, jobID, err.Error()); ferr != nil {
				// Python's inner `except Exception: pass` — no log at all.
				_ = ferr
			}
			continue
		}

		status := resultStatus(res)
		// Same terminal semantics as the hermes dispatch worker: "done" =
		// drained, plus a result_status annotation; a handle-level error is
		// a drain that produced no work, so it fails.
		var cerr error
		if status == "error" {
			_, cerr = tasks.Fail(ws, jobID, resultText(res, status))
		} else {
			_, cerr = tasks.Complete(ws, jobIDRaw, nil, status)
		}
		if cerr != nil {
			o.logf("drain_task_store: failed to mark %s complete: %s", jobID, cerr)
		}
		// AFTER the inner try, so a task whose complete() failed still
		// counts as processed. That is Python's placement, not an accident
		// of translation — and it is unpinned, because nothing a fixture
		// can seed makes complete() fail: the row was just claimed, and
		// "claimed" is exactly what it accepts. A concurrent deletion is
		// the only live route, which no differential can stage.
		processed++
		o.logf("drain_task_store: completed %s", jobID)
		drainEvent(ws, task, jobIDRaw)
	}

	return processed, nil
}

// drainEvent is the observable event the dashboard tails, inside its own
// swallowing try.
func drainEvent(ws string, task pyval.Obj, jobIDRaw any) {
	reason := taskGet(task, "reason", "")
	goal, sliceErr := pyval.SliceHead(reason, 80)
	if sliceErr != nil {
		// `task.get("reason", "")[:80]` raised at the CALL SITE, so
		// write_event is never reached and no row is written. That is a
		// different site from write_event's own internal slice, which
		// produces the same observable from one frame down — Python
		// double-slices too, and today both bounds are 80, so removing
		// this guard changes NOTHING that any fixture can see. Recorded
		// as an unobservable mutation rather than deleted, for the reason
		// escalation.go states at its own copy: the two guards answer
		// different questions, and the day a caller's bound differs from
		// write_event's, only this one is right.
		return
	}
	notify.WriteEvent(ws, "task_drained", notify.EventFields{
		Goal: goal,
		// RAW, all three: write_event touches none of them, so a task
		// whose parent_job_id is null writes a null and one whose source
		// is a number writes a number.
		Project: taskGet(task, "parent_job_id", ""),
		LoopID:  jobIDRaw,
		Status:  taskGet(task, "source", ""),
		// Stated, not omitted. An `any` field's Go zero is nil and spells
		// `null`, where write_event's declared default is "" — and the
		// zero value cannot be taught to mean the default, because the
		// three fields above must stay able to write a genuine null. So
		// the convention is that every raw field is spelled at every call
		// site, and the row-shape differential is what enforces it: this
		// line was missing on the first cut and the drain fixtures failed
		// on `"model": null` against CPython's `""` immediately.
		Model: "",
		// `f"depth={task.get('continuation_depth', 0)}"` is an f-string on
		// the RAW value, so a float depth is "2.0" and a string depth is
		// itself — NOT the int() the router computed three frames up.
		Detail: "depth=" + pyval.Str(taskGet(task, "continuation_depth", 0)),
	})
}

// --- the goal queue -----------------------------------------------------

// EnqueueGoal enqueues a user goal for the director to process
// sequentially, returning the job_id.
//
// A malformed typed envelope is refused HERE rather than at drain time: a
// bad payload should bounce to its sender, not sit queued until handle_task
// hits it hours later.
func EnqueueGoal(ws, goal, reason string, blockedBy []string, o Options) (string, error) {
	// `reason or goal` — the falsy-string fallback, not a nil check.
	payload := reason
	if payload == "" {
		payload = goal
	}
	if _, err := dispatch.ParseDispatchPayload(payload); err != nil {
		return "", err
	}
	task, err := tasks.Enqueue(ws, tasks.Options{
		Lane: "agenda", Source: "user_goal",
		Reason: payload, BlockedBy: blockedBy,
	})
	if err != nil {
		return "", err
	}
	// `task["job_id"]` is a SUBSCRIPT, not a `.get` — a task without the
	// key is a KeyError, not an empty id that then names a file called
	// ".json". make_task always writes it, so this arm is unreachable
	// today; it is spelled out rather than defaulted because the default
	// is the one answer that would be silently wrong.
	jobIDRaw, ok := task.Get("job_id")
	if !ok {
		return "", &pyval.PyErr{Class: "KeyError", Msg: "'job_id'"}
	}
	jobID := pyval.Str(jobIDRaw)
	// `goal[:80]` — a bare slice on a declared str, so Clip's rune-wise
	// prefix is the whole of it.
	o.logf("enqueue_goal: queued %s — %s", jobID, pyval.Clip(goal, 80))
	return jobID, nil
}

// EnqueueGoals enqueues several goals. With sequential set, each is
// blocked_by the previous.
//
// Python returns the ids accumulated SO FAR only by virtue of raising out
// of the loop; here the error carries the partial list, because a caller
// that enqueued three of five needs to know which three.
func EnqueueGoals(ws string, goals []string, sequential bool, o Options) ([]string, error) {
	jobIDs := []string{}
	for _, goal := range goals {
		var blocked []string
		if sequential && len(jobIDs) > 0 {
			blocked = []string{jobIDs[len(jobIDs)-1]}
		}
		jid, err := EnqueueGoal(ws, goal, "", blocked, o)
		if err != nil {
			return jobIDs, err
		}
		jobIDs = append(jobIDs, jid)
	}
	return jobIDs, nil
}

// --- helpers ------------------------------------------------------------

func taskGet(o pyval.Obj, key string, def any) any {
	if v, ok := o.Get(key); ok {
		return v
	}
	return def
}

// inTuple is `x in (a, b, c)` — `==` against each element, which NEVER
// raises, whatever x is. The set membership in the same comprehension does.
func inTuple(v any, options []string) bool {
	for _, s := range options {
		if pyval.Eq(v, s) {
			return true
		}
	}
	return false
}

// asObj is "does this support .get" — a mapping, and nothing else.
func asObj(v any) (pyval.Obj, bool) {
	if t, ok := v.(pyval.Obj); ok {
		return t, true
	}
	// There was a `map[string]any` arm here and it was DEAD: both callers
	// read their value out of a task row that pyval decoded, and that
	// decoder produces Obj — never a Go-native map. Worse than dead, it was
	// wrong if it ever fired: ranging a Go map yields keys in random order,
	// and the origin it converts is emitted as a payload whose field order
	// is part of what the differential compares, so the arm would have made
	// a passing test flap rather than fail.
	//
	// Nothing replaces it. A Go-native map now falls through to "not a
	// mapping", and both callers turn that into `'dict' object has no
	// attribute 'get'` — a sentence absurd enough on its face to read as
	// the port bug it would be, which the silent random-order conversion
	// was not.
	return nil, false
}

// listBound is Python's `xs[:n]` for a list, including the negative case —
// `queued[:-1]` drops the LAST element rather than clamping to zero, and a
// caller that passes a negative max_tasks gets exactly that.
func listBound(length, n int) int {
	if n < 0 {
		if length+n < 0 {
			return 0
		}
		return length + n
	}
	if n > length {
		return length
	}
	return n
}
