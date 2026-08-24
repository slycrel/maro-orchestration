package director

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The differential in escalation_diff_test.go pins what the two runtimes
// agree on. These pin the things a differential structurally cannot: a
// cadence driven by random.randint, an adapter that raises, a channel
// advisory that fires on some decisions and not others, and the ORDER of
// two writes whose failure mode is a message to the user about work that
// did not happen.

// --- the check-in cadence -----------------------------------------------

// The cadence lives on the `origin` dict that rides the ancestry from
// task to task, which means it has to survive a round trip through the
// queue — advancing it in place would advance a copy nobody reads.
func TestTheCheckinCadenceAdvancesOnTheAncestry(t *testing.T) {
	prev := checkinRandInt
	checkinRandInt = func(lo, hi int) int { return 5 }
	defer func() { checkinRandInt = prev }()

	for _, c := range []struct {
		name       string
		origin     pyval.Obj
		newDepth   int
		wantFire   bool
		wantNext   int
		wantSentTo int
	}{
		{"a fresh chain does not check in before the first depth",
			pyval.Obj{}, 1, false, 0, 0},
		{"the first check-in fires at the configured depth",
			pyval.Obj{}, 2, true, 7, 1},
		{"a chain past the first check-in waits for the jittered next",
			pyval.Obj{{Key: "next_checkin_depth", Val: 7}, {Key: "checkins_sent", Val: 1}},
			4, false, 7, 1},
		{"and fires when it arrives, counting up",
			pyval.Obj{{Key: "next_checkin_depth", Val: 7}, {Key: "checkins_sent", Val: 1}},
			7, true, 12, 2},
		{"a depth PAST the next check-in still fires exactly once",
			pyval.Obj{{Key: "next_checkin_depth", Val: 7}, {Key: "checkins_sent", Val: 1}},
			9, true, 14, 2},
	} {
		t.Run(c.name, func(t *testing.T) {
			// The fixture is handed to the port and then compared against.
			// Snapshot what it held FIRST: an aliased origin is mutated in
			// place (Obj.Set overwrites an existing key), so comparing the
			// task's origin against `c.origin` afterwards compares the same
			// slice with itself and always agrees. That is how the alias
			// mutant survived a second battery round.
			before := map[string]string{}
			for _, f := range c.origin {
				before[f.Key] = fmt.Sprint(pyval.Plain(f.Val))
			}

			task := pyval.Obj{{Key: "origin", Val: c.origin}}
			got, fired := AdvanceOriginWithCheckin(task, c.newDepth, nil)
			if fired != c.wantFire {
				t.Errorf("should_fire = %v, want %v", fired, c.wantFire)
			}
			if c.wantFire {
				if v := pyval.IntOf(mustGet(t, got, "next_checkin_depth")); v != c.wantNext {
					t.Errorf("next_checkin_depth = %d, want %d", v, c.wantNext)
				}
				if v := pyval.IntOf(mustGet(t, got, "checkins_sent")); v != c.wantSentTo {
					t.Errorf("checkins_sent = %d, want %d", v, c.wantSentTo)
				}
			} else if len(got) != len(c.origin) {
				t.Errorf("a non-firing advance changed the ancestry: %v", got)
			}
			// The task's own origin must be untouched — the returned object
			// is what gets enqueued, and the caller still holds the task.
			//
			// Compared by VALUE, not by length. An aliased origin whose
			// keys already exist gets them overwritten IN PLACE, which
			// leaves the length identical: the alias mutant survived the
			// first battery against a length check, on the very fixtures
			// below that carry next_checkin_depth already.
			orig, _ := task.Get("origin")
			in, ok := orig.(pyval.Obj)
			if !ok {
				t.Fatalf("the task's origin is no longer an object: %T", orig)
			}
			if len(in) != len(before) {
				t.Errorf("advancing the cadence added keys to the caller's task: %v", in)
			}
			for key, was := range before {
				v, present := in.Get(key)
				if !present {
					t.Errorf("the caller's task lost %s", key)
					continue
				}
				if now := fmt.Sprint(pyval.Plain(v)); now != was {
					t.Errorf("advancing the cadence rewrote the caller's %s: %s, was %s",
						key, now, was)
				}
			}
		})
	}
}

// The jitter is random.randint, which is INCLUSIVE at both ends. Go's
// rand.Intn is not, so a port that reached for it directly would never
// produce the configured maximum — a 4–7 cadence that is really 4–6, and
// nothing would ever report it.
func TestTheJitterCoversItsWholeRange(t *testing.T) {
	seen := map[int]bool{}
	for i := 0; i < 400; i++ {
		seen[CheckinJitter(nil)] = true
	}
	for want := 4; want <= 7; want++ {
		if !seen[want] {
			t.Errorf("400 draws never produced %d; the range is exclusive somewhere", want)
		}
	}
	for got := range seen {
		if got < 4 || got > 7 {
			t.Errorf("the jitter produced %d, outside the configured 4–7", got)
		}
	}
}

// Config drives both ends, and a reversed pair must not produce an empty
// range (Go's rand.Intn panics on a non-positive argument, so this is the
// difference between a clamp and a crash in the escalation path).
func TestTheCadenceReadsConfigAndSurvivesABadRange(t *testing.T) {
	cfg := map[string]any{"recursion": map[string]any{
		"checkin_first_depth": 4,
		"checkin_jitter_min":  9,
		"checkin_jitter_max":  3,
	}}
	if got := CheckinFirstDepth(cfg); got != 4 {
		t.Errorf("CheckinFirstDepth = %d, want 4", got)
	}
	// COVERAGE, not containment. A port that dropped the swap and only
	// clamped `hi` up to `lo` produces the constant 9 — which is inside
	// 3–9, so a containment check passes and reports a cadence that never
	// varies as a working jitter. (It did: the mutant survived the first
	// battery against exactly that assertion.)
	seen := map[int]bool{}
	for i := 0; i < 600; i++ {
		got := CheckinJitter(cfg)
		if got < 3 || got > 9 {
			t.Fatalf("a reversed range produced %d, outside the swapped 3–9", got)
		}
		seen[got] = true
	}
	for want := 3; want <= 9; want++ {
		if !seen[want] {
			t.Errorf("600 draws from a reversed 9–3 never produced %d; "+
				"the range was clamped rather than swapped", want)
		}
	}
	// A first depth below 1 clamps up: depth 0 would fire a check-in on
	// the very first continuation, which is what the cadence exists to
	// avoid.
	zero := map[string]any{"recursion": map[string]any{"checkin_first_depth": 0}}
	if got := CheckinFirstDepth(zero); got != 1 {
		t.Errorf("CheckinFirstDepth(0) = %d, want it clamped to 1", got)
	}
}

// --- the shape of `origin` ----------------------------------------------

// A task loaded from the queue decodes as an ordered object; a task
// assembled in Go arrives as a plain map. Both are real, and the second
// is the one that broke: an earlier cut type-asserted only pyval.Obj, so
// a map origin was silently DISCARDED and the enqueued continuation lost
// its whole ancestry — parent_goal, parent_handle_id and the cadence
// count — while every assertion about the cadence still passed, because
// the cadence starts from defaults when the ancestry is empty.
func TestAPlainMapOriginKeepsItsFields(t *testing.T) {
	prev := checkinRandInt
	checkinRandInt = func(lo, hi int) int { return 5 }
	defer func() { checkinRandInt = prev }()

	task := pyval.Obj{{Key: "origin", Val: map[string]any{
		"parent_goal":        "the original ask",
		"parent_handle_id":   "h-1",
		"next_checkin_depth": 11,
		"checkins_sent":      1,
	}}}
	got, fired := AdvanceOriginWithCheckin(task, 4, nil)
	if fired {
		t.Error("depth 4 fired against a next_checkin_depth of 11")
	}
	for _, want := range []struct {
		key string
		val any
	}{
		{"parent_goal", "the original ask"},
		{"parent_handle_id", "h-1"},
		{"next_checkin_depth", 11},
		{"checkins_sent", 1},
	} {
		v, ok := got.Get(want.key)
		if !ok {
			t.Errorf("%s was dropped: %v", want.key, got)
			continue
		}
		if fmt.Sprint(pyval.Plain(v)) != fmt.Sprint(want.val) {
			t.Errorf("%s = %v, want %v", want.key, v, want.val)
		}
	}
	// A Go map has no order to keep, so the keys are SORTED rather than
	// arbitrary: an origin that serialized differently on two runs of the
	// same input would make every downstream byte comparison flaky.
	keys := []string{}
	for _, f := range got {
		keys = append(keys, f.Key)
	}
	want := []string{"checkins_sent", "next_checkin_depth", "parent_goal", "parent_handle_id"}
	if strings.Join(keys, ",") != strings.Join(want, ",") {
		t.Errorf("origin keys %v, want them sorted %v", keys, want)
	}
}

// `Origin(task.get("origin") or {})` — the `or` is a TRUTHINESS gate, so
// None, an empty dict, and anything that is not a dict at all become a
// fresh empty object rather than raising or riding through.
func TestANonDictOriginBecomesAFreshObject(t *testing.T) {
	for _, c := range []struct {
		name string
		val  any
	}{
		{"absent", nil},
		{"null", nil},
		{"a string", "loop-parent-1"},
		{"a list", []any{"a", "b"}},
		{"a number", 7},
		{"a bool", false},
	} {
		t.Run(c.name, func(t *testing.T) {
			task := pyval.Obj{{Key: "origin", Val: c.val}}
			got, fired := AdvanceOriginWithCheckin(task, 1, nil)
			if fired {
				t.Error("depth 1 fired the first check-in, which sits at 2")
			}
			if len(got) != 0 {
				t.Errorf("a non-dict origin rode through as %v", got)
			}
		})
	}
}

// --- the enqueue-then-notify order --------------------------------------

// A failed enqueue must NOT fire the check-in. The check-in says "still
// running"; if the continuation was never queued, the chain is dead and
// that message is a lie the operator has no way to catch.
//
// Both spawn branches also fall back to `surface` on a failed enqueue,
// because a warning log is not an operator signal.
func TestAFailedEnqueueSurfacesAndFiresNoCheckin(t *testing.T) {
	for _, action := range []string{"continue", "narrow"} {
		t.Run(action, func(t *testing.T) {
			ws := t.TempDir()
			// Park a FILE where the queue's task directory belongs, so the
			// enqueue fails for a real filesystem reason rather than a
			// stubbed one.
			queueDir := filepath.Join(ws, "output", "queues", "tasks")
			if err := os.MkdirAll(filepath.Dir(queueDir), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(queueDir, []byte("not a directory"), 0o644); err != nil {
				t.Fatal(err)
			}

			body := reply(fmt.Sprintf(`"action": %q, "decision_class": "mechanical",
				"confidence": 9, "revised_goal": "a smaller slice",
				"reasoning": "bounded", "summary_for_user": "next pass"`, action))
			got := HandleEscalation(context.Background(), ws, objOf(map[string]any{
				"job_id": "job-fail001", "parent_job_id": "loop-parent-1",
				"reason": "audit the escalation lane", "continuation_depth": 5,
			}), EscalationOptions{Adapter: &llm.Fake{Script: []string{body}}})

			if got.Action != "surface" {
				t.Errorf("action = %q; a dead chain must surface", got.Action)
			}
			if got.FollowupTaskID != "" {
				t.Errorf("a failed enqueue reported a followup task %q", got.FollowupTaskID)
			}
			if !strings.Contains(got.SummaryForUser, "No follow-up task exists") {
				t.Errorf("the summary does not tell the operator the chain stopped: %q",
					got.SummaryForUser)
			}
			if !strings.HasPrefix(got.Reasoning, "enqueue failed:") {
				t.Errorf("reasoning = %q, want it naming the enqueue failure", got.Reasoning)
			}
			// Depth 5 is past the first check-in depth, so a port that fired
			// before confirming the enqueue would have written this row.
			if rows := readRows(t, filepath.Join(ws, "memory", "events.jsonl")); len(rows) > 0 {
				for _, r := range rows {
					if r.GetString("event_type") == "recursion_checkin" {
						t.Error("a check-in fired for a continuation that was never enqueued")
					}
				}
			}
		})
	}
}

// --- the dry-run and the failed call ------------------------------------

// A dry-run close is not a judged close: it returns before the branch
// that stamps, so nothing is recorded. A port that shared one close path
// would write a reachable-but-not-worth-it verdict every time someone ran
// --dry-run.
func TestADryRunClosesWithoutWritingAnything(t *testing.T) {
	for _, c := range []struct {
		name string
		opts EscalationOptions
	}{
		{"dry_run", EscalationOptions{DryRun: true, Adapter: &llm.Fake{Script: []string{"{}"}}}},
		{"no adapter at all", EscalationOptions{}},
	} {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			got := HandleEscalation(context.Background(), ws, objOf(map[string]any{
				"job_id": "job-dry0001", "parent_job_id": "loop-parent-1",
				"reason": "audit the escalation lane", "continuation_depth": 2,
			}), c.opts)
			if got.Action != "close" {
				t.Errorf("action = %q, want close", got.Action)
			}
			if !strings.Contains(got.Reasoning, "[dry-run]") {
				t.Errorf("reasoning = %q, want it marked as a dry run", got.Reasoning)
			}
			if entries, _ := os.ReadDir(ws); len(entries) != 0 {
				names := []string{}
				for _, e := range entries {
					names = append(names, e.Name())
				}
				t.Errorf("a dry run wrote %v into the workspace", names)
			}
		})
	}
}

// An adapter that raises surfaces — it does not close, and it does not
// guess. The distinction matters because close is the one action that
// records a judgment, and a failed call has made none.
func TestAFailedCallSurfacesRatherThanClosing(t *testing.T) {
	ws := t.TempDir()
	got := HandleEscalation(context.Background(), ws, objOf(map[string]any{
		"job_id": "job-err00001", "parent_job_id": "loop-parent-1",
		"reason": "audit the escalation lane", "continuation_depth": 2,
	}), EscalationOptions{Adapter: erroringAdapter{}})

	if got.Action != "surface" {
		t.Errorf("action = %q, want surface", got.Action)
	}
	if got.Reasoning != "LLM call failed during escalation processing" {
		t.Errorf("reasoning = %q", got.Reasoning)
	}
	if got.Confidence != 5 {
		t.Errorf("confidence = %d, want the default 5", got.Confidence)
	}
	// And it writes NOTHING — no artifact, no calibration row, no event.
	// Python returns before the whole recording block, so a surface born
	// from a failed call is invisible everywhere a judged surface is
	// visible. That is a real gap on the Python side (the one surface an
	// operator most needs to see is the one nobody records), and it is
	// pinned here as-is rather than fixed: the port's job is to match, and
	// a Go-only artifact would show up as a phantom file in every
	// differential from here on. Owed upstream, noted in PORT.md.
	if entries, _ := os.ReadDir(ws); len(entries) != 0 {
		names := []string{}
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Errorf("the failure path wrote %v; CPython writes nothing", names)
	}
}

type erroringAdapter struct{}

func (erroringAdapter) Name() string             { return "erroring" }
func (erroringAdapter) SupportsAgentTools() bool { return false }
func (erroringAdapter) Complete(context.Context, []llm.Message, llm.Options) (*llm.Response, error) {
	return nil, errors.New("scripted adapter failure")
}

// --- the low-confidence advisory ----------------------------------------

type recordingChannel struct {
	calls []string
	panic bool
}

func (c *recordingChannel) NotifyLowConfidence(decision string, confidence float64, reasoning string) {
	c.calls = append(c.calls, fmt.Sprintf("%s|%.2f|%s", decision, confidence, reasoning))
	if c.panic {
		panic("channel exploded")
	}
}

// The advisory fires for a risky call that is ACTUALLY GOING TO HAPPEN.
// A surfaced decision needs none — the operator is already in the loop —
// and a confident one is not risky. Both exclusions are easy to drop, and
// dropping either turns the advisory into noise that gets ignored.
func TestTheLowConfidenceAdvisoryFiresOnlyForActedRiskyCalls(t *testing.T) {
	// wantAdvisory is the exact "decision|confidence|reasoning" triple the
	// channel should receive. The confidence is a FRACTION
	// (confidence/10.0) where every other surface in this lane carries the
	// 1–10 integer, and the decision embeds the summary AFTER its
	// confidence prefix was applied — both are easy to get half-right, and
	// a test that only counted the calls would notice neither.
	for _, c := range []struct {
		name         string
		action       string
		class        string
		confidence   int
		wantAdvisory string
	}{
		{"a taste close at 6 files an advisory", "close", "taste", 6,
			"close: [Confidence 6/10] so|0.60|because"},
		{"the boundary at 7 still files", "close", "mechanical", 7,
			"close: so|0.70|because"},
		{"a confident close does not", "close", "mechanical", 8, ""},
		{"a surface does not, however unsure", "surface", "mechanical", 5, ""},
		{"and neither does a call that was overridden TO surface",
			"close", "user_challenge", 6, ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			ch := &recordingChannel{}
			body := reply(fmt.Sprintf(`"action": %q, "decision_class": %q,
				"confidence": %d, "reasoning": "because", "summary_for_user": "so"`,
				c.action, c.class, c.confidence))
			HandleEscalation(context.Background(), ws, objOf(map[string]any{
				"job_id": "job-chan0001", "parent_job_id": "loop-parent-1",
				"reason": "audit the escalation lane", "continuation_depth": 1,
			}), EscalationOptions{Adapter: &llm.Fake{Script: []string{body}}, Channel: ch})

			if c.wantAdvisory == "" {
				if len(ch.calls) != 0 {
					t.Errorf("the channel got an advisory it should not have: %v", ch.calls)
				}
				return
			}
			if len(ch.calls) != 1 {
				t.Fatalf("the channel got %d advisories, want 1: %v", len(ch.calls), ch.calls)
			}
			if ch.calls[0] != c.wantAdvisory {
				t.Errorf("advisory = %q, want %q", ch.calls[0], c.wantAdvisory)
			}
		})
	}
}

// Both text arguments are bounded, and at different lengths: the decision
// carries summary_for_user[:120] and the reasoning reasoning[:200]. A
// channel is a phone notification at the far end, so an unbounded model
// paragraph is the failure this bound exists for — and the two bounds are
// easy to collapse into one.
func TestTheAdvisoryArgumentsAreBounded(t *testing.T) {
	ws := t.TempDir()
	ch := &recordingChannel{}
	body := reply(fmt.Sprintf(`"action": "close", "decision_class": "taste",
		"confidence": 7, "reasoning": %q, "summary_for_user": %q`,
		strings.Repeat("r", 400), strings.Repeat("s", 400)))
	HandleEscalation(context.Background(), ws, objOf(map[string]any{
		"job_id": "job-bound001", "parent_job_id": "loop-parent-1",
		"reason": "audit the escalation lane", "continuation_depth": 1,
	}), EscalationOptions{Adapter: &llm.Fake{Script: []string{body}}, Channel: ch})

	if len(ch.calls) != 1 {
		t.Fatalf("got %d advisories, want 1", len(ch.calls))
	}
	parts := strings.SplitN(ch.calls[0], "|", 3)
	if len(parts) != 3 {
		t.Fatalf("malformed advisory: %q", ch.calls[0])
	}
	// "close: " plus 120 of summary.
	if got := len(parts[0]); got != len("close: ")+120 {
		t.Errorf("decision is %d chars, want %d", got, len("close: ")+120)
	}
	if got := len(parts[2]); got != 200 {
		t.Errorf("reasoning is %d chars, want 200", got)
	}
}

// A channel that explodes must not take the escalation with it. Python
// wraps the call in a bare except with the comment "channel notifications
// must never block escalation logic"; a Go port that let the panic through
// would lose the calibration row, the artifact and the event, all because
// a dashboard was down.
func TestAnExplodingChannelDoesNotStopTheEscalation(t *testing.T) {
	ws := t.TempDir()
	body := reply(`"action": "close", "decision_class": "taste",
		"confidence": 6, "reasoning": "because", "summary_for_user": "so"`)
	got := HandleEscalation(context.Background(), ws, objOf(map[string]any{
		"job_id": "job-boom0001", "parent_job_id": "loop-parent-1",
		"reason": "audit the escalation lane", "continuation_depth": 1,
	}), EscalationOptions{
		Adapter: &llm.Fake{Script: []string{body}},
		Channel: &recordingChannel{panic: true},
	})
	if got.Action != "close" {
		t.Errorf("action = %q, want close", got.Action)
	}
	if rows := readRows(t, filepath.Join(ws, "memory", "calibration.jsonl")); len(rows) != 1 {
		t.Errorf("the calibration row was lost with the channel: %d rows", len(rows))
	}
	if matches, _ := filepath.Glob(filepath.Join(ws, "projects", "*", "artifacts", "*.md")); len(matches) != 1 {
		t.Errorf("the operator artifact was lost with the channel")
	}
}

// --- int(str) -----------------------------------------------------------

// The confidence field arrives from a model, so it arrives as whatever
// the model felt like. Python's `int(...)` inside a try/except is the
// gate, and it is NOT the same as pyval.IntOf: int("high") RAISES and
// falls back to the caller's default of 5, where IntOf answers 0 — which
// then clamps to 1, i.e. maximum uncertainty, from a reply that said
// nothing about confidence at all.
func TestPyIntOrIsIntInsideATryNotIntOf(t *testing.T) {
	for _, c := range []struct {
		in   any
		want int
	}{
		{7, 7},
		{"7", 7},
		{"  7  ", 7}, // int() strips
		{"+7", 7},    // and takes a sign
		{"-7", -7},   //
		{"1_0", 10},  // PEP 515 underscores between digits
		{7.9, 7},     // int(float) truncates toward zero
		{-7.9, -7},   //
		{true, 1},    // int(True) is 1, not an error
		{false, 0},   //
		{"high", 5},  // ValueError -> the caller's default
		{"7.5", 5},   // int("7.5") is a ValueError, unlike float()
		{"7e2", 5},   // so is an exponent
		{"_7", 5},    // a leading underscore
		{"7_", 5},    // a trailing one
		{"7__0", 5},  // and a doubled one
		{"", 5},      //
		{"  ", 5},    //
		{nil, 5},     // int(None) is a TypeError
		{[]any{}, 5}, //
		{"０７", 5},    // full-width digits: CPython accepts these, this
		{"٣", 5},     // port does not — a NAMED divergence, below
		{map[string]any{}, 5},
	} {
		if got := pyIntOr(c.in, 5); got != c.want {
			t.Errorf("pyIntOr(%#v) = %d, want %d", c.in, got, c.want)
		}
	}
}

// The two full-width cases above are a divergence, not a match, and it is
// recorded here rather than left for a reader to trip over: CPython's
// int() accepts every Unicode decimal digit, so int("０７") is 7. This
// port refuses them and takes the default.
//
// The consequence is bounded to one field — a model answering a
// confidence in Eastern Arabic numerals gets 5 here and 3 in CPython —
// and widening the parser would mean carrying a Unicode decimal table for
// a case no model has produced. Named, with the cost stated, so a later
// reader can price it rather than rediscover it.
func TestTheFullWidthDigitDivergenceIsDeliberate(t *testing.T) {
	if _, ok := pyIntFromString("٣"); ok {
		t.Error("this port now accepts Unicode decimal digits; update the note above")
	}
}

func mustGet(t *testing.T, o pyval.Obj, key string) any {
	t.Helper()
	v, ok := o.Get(key)
	if !ok {
		t.Fatalf("%s is absent from %v", key, o)
	}
	return v
}
