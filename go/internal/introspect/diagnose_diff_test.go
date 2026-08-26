package introspect

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyDiagnoseSrc drives introspect.diagnose_loop over an events.jsonl the
// test writes byte for byte.
//
// emit_log_event=False: the captain's-log write is a side effect of
// diagnosing, not part of the answer, and a probe that trips it would be
// measuring the log writer too.
//
// It returns the PERSISTED row (json.dumps of to_dict(), so key order and
// separators are compared) and summary(), which is a second rendering of
// the same state and drifts independently.
const pyDiagnoseSrc = `
import json, sys
import introspect

_argv = json.loads(sys.argv[1])
diag = introspect.diagnose_loop(_argv["loop_id"], _argv["project"],
                                emit_log_event=False)
print(json.dumps({
    "row": json.dumps(diag.to_dict()),
    "summary": diag.summary(),
    "token_profile": diag.token_profile,
    "timing_profile": diag.timing_profile,
}))
`

type diagProbe struct {
	Row           string           `json:"row"`
	Summary       string           `json:"summary"`
	TokenProfile  []map[string]any `json:"token_profile"`
	TimingProfile []map[string]any `json:"timing_profile"`
}

// seedEvents writes the lines into a workspace's memory/events.jsonl and
// returns the workspace. The workspace is a t.TempDir, and pyprobe refuses
// to run a writing probe whose MARO_WORKSPACE resolves inside ~/.maro.
func seedEvents(t *testing.T, lines []string) string {
	t.Helper()
	ws := t.TempDir()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := ""
	if len(lines) > 0 {
		body = strings.Join(lines, "\n") + "\n"
	}
	if err := os.WriteFile(filepath.Join(dir, "events.jsonl"),
		[]byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return ws
}

func decodeEvents(t *testing.T, lines []string) []pyval.Obj {
	t.Helper()
	var out []pyval.Obj
	for _, ln := range lines {
		o, err := jsonx.ObjectOrdered(ln)
		if err != nil {
			t.Fatalf("fixture line is not a JSON object: %v (%s)", err, ln)
		}
		out = append(out, o)
	}
	return out
}

// step builds a step_done/step_stuck line. Written as a formatter rather
// than as Go literals because the fields reach diagnose_loop through
// json.loads on both sides, and a fixture built from literals arrives with
// types the reader never produces.
func step(idx int, status, text string, tokensIn, tokensOut, elapsed int) string {
	return `{"event_type": "step_done", "loop_id": "L1", "step_idx": ` +
		itoa(idx) + `, "step": ` + quote(text) + `, "status": ` + quote(status) +
		`, "tokens_in": ` + itoa(tokensIn) + `, "tokens_out": ` + itoa(tokensOut) +
		`, "elapsed_ms": ` + itoa(elapsed) + `}`
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		return "-" + string(b)
	}
	return string(b)
}

func quote(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
	return b.String()
}

// TestDiagnoseMatchesCPython is the differential over diagnose_loop's
// heuristics.
//
// Every assertion is on the PERSISTED ROW and on summary() — two renderings
// of the same state that drift independently — rather than on the failure
// class alone. The class is the easy half: it is a fixed string on both
// sides. The evidence is where a port drifts, because each line runs
// Python's f-string formatting over numbers (`:,` grouping, `.1f` and
// `.2f` rounding) and RUNE slices over step text.
func TestDiagnoseMatchesCPython(t *testing.T) {
	long := strings.Repeat("漢", 200) // 200 CJK runes = 600 bytes

	cases := []struct {
		name  string
		lines []string
	}{
		{name: "no events at all", lines: nil},
		{name: "a healthy loop", lines: []string{
			step(1, "done", "first", 100, 50, 1000),
			step(2, "done", "second", 200, 60, 1200),
		}},

		// --- setup_failure, and its own boundary --------------------------
		{name: "step 1 blocked with no tokens, fast", lines: []string{
			step(1, "blocked", "the very first step", 0, 0, 400),
			step(2, "done", "second", 100, 10, 900),
		}},
		// 5000ms is the boundary: `< 5000` excludes it, and the class stays
		// healthy rather than becoming setup_failure.
		{name: "step 1 blocked at exactly the 5s boundary", lines: []string{
			step(1, "blocked", "the very first step", 0, 0, 5000),
			step(2, "done", "second", 100, 10, 900),
		}},
		{name: "step 1 blocked just under the 5s boundary", lines: []string{
			step(1, "blocked", "the very first step", 0, 0, 4999),
			step(2, "done", "second", 100, 10, 900),
		}},
		// The step text is clipped at 100 RUNES. 200 CJK characters is 600
		// bytes, so a byte slice would cut mid-rune.
		{name: "setup_failure clips the step text at 100 runes", lines: []string{
			step(1, "blocked", long, 0, 0, 100),
		}},

		// --- adapter_timeout OVERWRITES setup_failure ---------------------
		// Check 2 carries no `if failure_class == "healthy"` guard, so a
		// loop that is both gets adapter_timeout AND both evidence lines.
		{name: "a setup failure that is also an adapter timeout", lines: []string{
			step(1, "blocked", "first", 0, 0, 400),
			step(2, "blocked", "second", 0, 0, 61000),
		}},
		{name: "adapter timeout at exactly 60s does not fire", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "blocked", "second", 0, 0, 60000),
		}},

		// --- constraint_false_positive ------------------------------------
		{name: "two fast zero-token blocks", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "blocked", "second", 0, 0, 500),
			step(3, "blocked", "third", 0, 0, 700),
		}},
		// Only the first THREE get an evidence line, and each is clipped at
		// 80 runes.
		{name: "five fast blocks show three, clipped at 80 runes", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "blocked", long, 0, 0, 100),
			step(3, "blocked", long+"b", 0, 0, 200),
			step(4, "blocked", long+"c", 0, 0, 300),
			step(5, "blocked", long+"d", 0, 0, 400),
			step(6, "blocked", long+"e", 0, 0, 500),
		}},
		// 1000ms is check 3's own boundary: `< 1000` excludes it, so these
		// two are not a constraint false positive.
		{name: "two blocks at exactly the 1s boundary", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "blocked", "second", 0, 0, 1000),
			step(3, "blocked", "third", 0, 0, 1000),
		}},
		{name: "two blocks just under the 1s boundary", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "blocked", "second", 0, 0, 999),
			step(3, "blocked", "third", 0, 0, 999),
		}},
		// One is not enough.
		{name: "a single fast block stays healthy", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "blocked", "second", 0, 0, 500),
		}},

		// --- decomposition_too_broad --------------------------------------
		{name: "a step over the fresh-token limit", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "done", "a very large step", 250000, 1000, 5000),
		}},
		// FRESH tokens: the same volume served from cache is not too broad.
		{name: "the same volume served from cache is not too broad", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 2, ` +
				`"step": "cached", "status": "done", "tokens_in": 250000, ` +
				`"tokens_out": 1000, "cache_read_tokens": 250000, "elapsed_ms": 5000}`,
		}},
		// The token limit's own boundary: `>` excludes equality.
		{name: "fresh tokens at exactly the limit", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "done", "at the limit", 200000, 0, 5000),
		}},
		{name: "fresh tokens one over the limit", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "done", "one over", 200001, 0, 5000),
		}},
		// The time arm needs BOTH the elapsed limit and the token floor.
		{name: "slow but cheap is not too broad", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "done", "slow and cheap", 1000, 100, 130000),
		}},
		{name: "slow and expensive is too broad", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			step(2, "done", "slow and dear", 60000, 100, 130000),
		}},

		// --- token_explosion ----------------------------------------------
		{name: "fresh-token growth over the ratio", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "build a parser"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// The ratio's own boundary: `>` excludes exactly 3x.
		{name: "growth at exactly the ratio does not fire", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "build a parser"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 6000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// The `prev_tok > 1000` floor.
		{name: "growth from a tiny base does not fire", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "build a parser"}`,
			step(1, "done", "first", 1000, 0, 900),
			step(2, "done", "second", 90000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// The research exemption NOTES the growth and stays healthy — and
		// it BREAKS, so a later real explosion is never reached.
		{name: "a research loop notes the growth and stays healthy", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "Research the competitors"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// ...but only while nothing blocked.
		{name: "a research loop with a blocked step is not exempt", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "Research the competitors"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "blocked", "third", 100, 0, 900),
		}},
		// ...and only up to 6x.
		{name: "a research loop past 6x is not exempt", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "Research the competitors"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 13000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// The 6x exemption ceiling is its own boundary: `< prev*6` excludes
		// exactly six times, so this one is NOT exempt.
		{name: "a research loop at exactly 6x is not exempt", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "Research the competitors"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 12000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		{name: "a research loop just under 6x is exempt", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "Research the competitors"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 11999, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// The exemption BREAKS, so a later real explosion in the same loop
		// is never reached and the loop stays healthy. A port that used
		// `continue` here would diagnose the second growth.
		{name: "an exempt growth hides a later real explosion", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "Research the competitors"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "done", "third", 2000, 0, 900),
			step(4, "done", "fourth", 90000, 0, 900),
		}},
		// Every keyword, one loop each: an enumeration is not a class, and
		// a keyword dropped from the port classifies as token_explosion
		// rather than failing anything else.
		{name: "the summarize keyword exempts", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "summarize the notes"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		{name: "the fetch keyword exempts", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "fetch the manifests"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		{name: "the analyze keyword exempts", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "analyze the corpus"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		{name: "the extract keyword exempts", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "extract the tables"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// The keyword match is a SUBSTRING on the lowered goal.
		{name: "the research keyword matches inside a word", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "RESEARCHING things"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 9000, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// The ratio renders through `.1f`, so a value that rounds half-way
		// is the interesting one.
		{name: "a growth ratio that rounds at the half", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "build"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 6900, 0, 900),
			step(3, "done", "third", 100, 0, 900),
		}},
		// Fewer than three steps: the whole check is skipped.
		{name: "two steps never explode", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "build"}`,
			step(1, "done", "first", 2000, 0, 900),
			step(2, "done", "second", 90000, 0, 900),
		}},

		// --- cost_spike ----------------------------------------------------
		// A large but CHEAP-per-token cached read on a pricey model: the
		// case the growth alarms miss. The evidence renders three counts
		// through `{:,}` and a dollar figure through `.2f`.
		{name: "a single expensive step", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "big", "status": "done", "tokens_in": 4000000, ` +
				`"tokens_out": 12000, "cache_read_tokens": 3900000, ` +
				`"model": "claude-opus-4-6", "elapsed_ms": 5000}`,
		}},
		// The whole-loop arm, with no single step over its own threshold.
		{name: "an expensive loop of cheap steps", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100000, ` +
				`"tokens_out": 2000, "model": "sonnet", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 2, ` +
				`"step": "b", "status": "done", "tokens_in": 100000, ` +
				`"tokens_out": 2000, "model": "sonnet", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 3, ` +
				`"step": "c", "status": "done", "tokens_in": 100000, ` +
				`"tokens_out": 2000, "model": "sonnet", "elapsed_ms": 900}`,
		}},
		// The step threshold's OWN boundary — and the reason it is spelled
		// with a big cache read rather than with raw input tokens.
		//
		// Cost check 5b is gated on the class still being healthy, and
		// check 4 fires at 200,000 FRESH tokens. So any fixture priced by
		// input volume alone (625,000 haiku tokens is exactly $0.50) is
		// classified decomposition_too_broad four checks earlier and never
		// reaches the dollar threshold at all: a first draft of these three
		// cases did exactly that, and the mutant that flips `>=` to `>` on
		// both dollar thresholds survived the whole battery because of it.
		//
		// grok-4.5 input is $2.00/M and cache reads bill at 0.1x, so
		// 700,000 in with 500,000 of them cached is 200,000 fresh —
		// exactly AT check 4's limit, which is `>`, so it passes through —
		// and 200,000*2/1e6 + 500,000*2*0.1/1e6 = $0.40 + $0.10, which is
		// exactly $0.50 in IEEE doubles. `>= 0.50` includes it.
		{name: "a step at exactly the cost threshold", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "at the line", "status": "done", "tokens_in": 700000, ` +
				`"tokens_out": 0, "cache_read_tokens": 500000, ` +
				`"model": "grok-4.5", "elapsed_ms": 900}`,
		}},
		{name: "a step one token under the cost threshold", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "under the line", "status": "done", "tokens_in": 699999, ` +
				`"tokens_out": 0, "cache_read_tokens": 500000, ` +
				`"model": "grok-4.5", "elapsed_ms": 900}`,
		}},
		// The LOOP threshold's own boundary: five grok-4.5 steps at $0.40
		// each sum to exactly $2.00, and none is over the step threshold on
		// its own, so only the loop arm of the evidence is emitted. 200,000
		// tokens each is again exactly AT check 4's limit.
		{name: "a loop at exactly the loop cost threshold", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 2, ` +
				`"step": "b", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 3, ` +
				`"step": "c", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 4, ` +
				`"step": "d", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 5, ` +
				`"step": "e", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
		}},
		// One step under the loop line, to pin that four of them is $1.60
		// and says nothing at all.
		{name: "a loop just under the loop cost threshold", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 2, ` +
				`"step": "b", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 3, ` +
				`"step": "c", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 4, ` +
				`"step": "d", "status": "done", "tokens_in": 200000, ` +
				`"tokens_out": 0, "model": "grok-4.5", "elapsed_ms": 900}`,
		}},
		// `max(profiles, key=...)` keeps the FIRST maximum on a tie. Two
		// steps at an identical cost with different indices is the only
		// fixture that can tell first from last — and the tie has to clear
		// the STEP threshold, because the step index is only rendered by
		// the `costliest.cost_usd >= 0.50` arm.
		{name: "two steps tied for costliest report the first", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 7, ` +
				`"step": "a", "status": "done", "tokens_in": 700000, ` +
				`"tokens_out": 0, "cache_read_tokens": 500000, ` +
				`"model": "grok-4.5", "elapsed_ms": 900}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 9, ` +
				`"step": "b", "status": "done", "tokens_in": 700000, ` +
				`"tokens_out": 0, "cache_read_tokens": 500000, ` +
				`"model": "grok-4.5", "elapsed_ms": 900}`,
		}},
		// An unknown model prices at the Sonnet fallback and renders the
		// literal word "default" in the evidence.
		{name: "an expensive step on an unknown model", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "big", "status": "done", "tokens_in": 300000, ` +
				`"tokens_out": 5000, "elapsed_ms": 5000}`,
		}},

		// --- empty_model_output -------------------------------------------
		{name: "two blocked steps that spent tokens", lines: []string{
			step(1, "blocked", "first", 500, 100, 2000),
			step(2, "blocked", "second", 600, 100, 2000),
			step(3, "done", "third", 100, 10, 900),
		}},
		// 30000ms is check 6's own boundary: `< 30000` excludes it.
		{name: "blocked with tokens at exactly the 30s boundary", lines: []string{
			step(1, "blocked", "first", 500, 100, 30000),
			step(2, "blocked", "second", 600, 100, 30000),
			step(3, "done", "third", 100, 10, 900),
		}},
		{name: "blocked with tokens just under the 30s boundary", lines: []string{
			step(1, "blocked", "first", 500, 100, 29999),
			step(2, "blocked", "second", 600, 100, 29999),
			step(3, "done", "third", 100, 10, 900),
		}},
		{name: "blocked with tokens but slow is not empty output", lines: []string{
			step(1, "blocked", "first", 500, 100, 30000),
			step(2, "blocked", "second", 600, 100, 31000),
			step(3, "done", "third", 100, 10, 900),
		}},

		// --- budget_exhaustion --------------------------------------------
		{name: "the loop hit max_iterations", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			`{"event_type": "loop_done", "loop_id": "L1", "status": "stuck", ` +
				`"detail": "max_iterations reached with 2 steps undone"}`,
		}},
		// NOT gated on healthy: the evidence rides along under another
		// class, and the severity does NOT move to warning.
		{name: "max_iterations under a setup failure", lines: []string{
			step(1, "blocked", "first", 0, 0, 400),
			`{"event_type": "loop_done", "loop_id": "L1", "status": "stuck", ` +
				`"detail": "max_iterations reached"}`,
		}},
		// A loop_done whose detail says something else.
		{name: "a loop_done with an unrelated detail", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			`{"event_type": "loop_done", "loop_id": "L1", "status": "done", ` +
				`"detail": "all steps complete"}`,
		}},

		// --- retry_churn ---------------------------------------------------
		// The key is the first 60 RUNES of the step text, so two steps that
		// differ only past rune 60 are the same step.
		// Reaching retry_churn at all is the hard part, and TWO drafts of
		// these cases never did. Churn is check 8 and it is gated on the
		// class still being healthy, so the fixture has to thread every
		// earlier check:
		//
		//   check 1 (setup_failure) reads profiles[0] ONLY, and fires on a
		//     first step that is blocked with 0 tokens in under 5s — which
		//     is the exact shape of a churned step. So step 1 here is a
		//     plain DONE step and the churn starts at step 2. The draft
		//     that opened with a blocked step was classified setup_failure
		//     every time, which is why the mutants on the churn limit, the
		//     key clip and the evidence ordering all survived silently.
		//   check 3 (constraint_false_positive) fires on two blocked steps
		//     with 0 tokens in under 1s each, so these take 2000ms.
		//   check 6 (empty_model_output) fires on blocked steps that SPENT
		//     tokens, so these spend zero.
		{name: "the same blocked step twice", lines: []string{
			step(1, "done", "warm up", 100, 10, 900),
			step(2, "blocked", "fetch the manifest", 0, 0, 2000),
			step(3, "blocked", "fetch the manifest", 0, 0, 2000),
		}},
		// Exactly at the churn limit — `>= 2`, so two IS churn, and three
		// is the case that a `> 2` off-by-one still reports.
		{name: "a step blocked three times", lines: []string{
			step(1, "done", "warm up", 100, 10, 900),
			step(2, "blocked", "fetch the manifest", 0, 0, 2000),
			step(3, "blocked", "fetch the manifest", 0, 0, 2000),
			step(4, "blocked", "fetch the manifest", 0, 0, 2000),
		}},
		{name: "a step blocked once is not churn", lines: []string{
			step(1, "done", "warm up", 100, 10, 900),
			step(2, "blocked", "fetch the manifest", 0, 0, 2000),
			step(3, "blocked", "something else entirely", 0, 0, 2000),
		}},
		// The key is the first 60 RUNES. These two differ only at rune 61,
		// so they are the SAME step and this is churn. 60 e-acutes is 120
		// bytes, so a byte clip would cut at rune 30 and agree by accident
		// on a shorter prefix — which is why the discriminating character
		// sits exactly at the boundary. The key is also rendered verbatim
		// into the evidence, so a byte clip is visible twice over: it
		// collides on the wrong prefix AND prints a truncated rune.
		{name: "two blocked steps differing past rune 60", lines: []string{
			step(1, "done", "warm up", 100, 10, 900),
			step(2, "blocked", strings.Repeat("é", 60)+"A", 0, 0, 2000),
			step(3, "blocked", strings.Repeat("é", 60)+"B", 0, 0, 2000),
		}},
		// ...and these differ at rune 60, INSIDE the key, so they are two
		// different steps and there is no churn.
		{name: "two blocked steps differing at rune 60", lines: []string{
			step(1, "done", "warm up", 100, 10, 900),
			step(2, "blocked", strings.Repeat("é", 59)+"A", 0, 0, 2000),
			step(3, "blocked", strings.Repeat("é", 59)+"B", 0, 0, 2000),
		}},
		// A key that is longer than 60 runes but differs INSIDE the first
		// 80: churn under the real clip, two distinct steps under a clip
		// widened to 80.
		{name: "two blocked steps differing at rune 70", lines: []string{
			step(1, "done", "warm up", 100, 10, 900),
			step(2, "blocked", strings.Repeat("é", 69)+"A"+strings.Repeat("z", 20), 0, 0, 2000),
			step(3, "blocked", strings.Repeat("é", 69)+"B"+strings.Repeat("z", 20), 0, 0, 2000),
		}},
		// FIVE different churned steps: the evidence order is the order the
		// keys were first seen, which is Python's dict insertion order and
		// not a Go map's. Reverse-alphabetical insertion so a sorted order
		// is visible, and five keys rather than two so a randomised map
		// order is caught ~119 times out of 120 instead of half the time.
		{name: "five different churned steps keep insertion order", lines: []string{
			step(1, "done", "warm up", 100, 10, 900),
			step(2, "blocked", "zebra", 0, 0, 2000),
			step(3, "blocked", "yak", 0, 0, 2000),
			step(4, "blocked", "xerus", 0, 0, 2000),
			step(5, "blocked", "walrus", 0, 0, 2000),
			step(6, "blocked", "vole", 0, 0, 2000),
			step(7, "blocked", "zebra", 0, 0, 2000),
			step(8, "blocked", "yak", 0, 0, 2000),
			step(9, "blocked", "xerus", 0, 0, 2000),
			step(10, "blocked", "walrus", 0, 0, 2000),
			step(11, "blocked", "vole", 0, 0, 2000),
		}},

		// --- tool pathologies ----------------------------------------------
		{name: "a hallucination claims the diagnosis", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100, "tokens_out": 10, ` +
				`"elapsed_ms": 900, "tool_pathologies": [` +
				`{"cls": "tool_arg_malformed", "evidence": "grep errored with 'usage:' signature: usage"},` +
				`{"cls": "tool_hallucination", "evidence": "call to 'bash' answered 'No such tool available'"}]}`,
		}},
		// Order of the claim is the ORDER LIST, not the transcript.
		{name: "neglect outranks recovery failure", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100, "tokens_out": 10, ` +
				`"elapsed_ms": 900, "tool_pathologies": [` +
				`{"cls": "tool_recovery_failure", "evidence": "3 consecutive tool errors (a, b, c)"},` +
				`{"cls": "tool_feedback_neglect", "evidence": "final tool call errored (w: boom) but step reported done"}]}`,
		}},
		// Hallucination outranks neglect, which is the FIRST pair in the
		// order list — without this, permuting those two entries changes
		// nothing any fixture can see.
		{name: "hallucination outranks neglect", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100, "tokens_out": 10, ` +
				`"elapsed_ms": 900, "tool_pathologies": [` +
				`{"cls": "tool_feedback_neglect", "evidence": "final tool call errored (w: boom) but step reported done"},` +
				`{"cls": "tool_hallucination", "evidence": "call to 'bash' answered"}]}`,
		}},
		// ...and recovery_failure outranks arg_malformed, the last pair.
		{name: "recovery failure outranks malformed args", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100, "tokens_out": 10, ` +
				`"elapsed_ms": 900, "tool_pathologies": [` +
				`{"cls": "tool_arg_malformed", "evidence": "grep errored with 'usage:' signature: usage"},` +
				`{"cls": "tool_recovery_failure", "evidence": "3 consecutive tool errors (a, b, c)"}]}`,
		}},
		// A class nobody has heard of falls through to tool_arg_malformed —
		// which is how a class added on the Python side reaches a port that
		// has not learned it.
		{name: "an unknown pathology class falls through", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100, "tokens_out": 10, ` +
				`"elapsed_ms": 900, "tool_pathologies": [` +
				`{"cls": "tool_something_new", "evidence": "who knows"}]}`,
		}},
		// The evidence is appended even when a structural class won, and
		// the recommendation stays the structural one.
		{name: "pathology evidence rides under budget exhaustion", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100, "tokens_out": 10, ` +
				`"elapsed_ms": 900, "tool_pathologies": [` +
				`{"cls": "tool_hallucination", "evidence": "call to 'bash' answered"}]}`,
			`{"event_type": "loop_done", "loop_id": "L1", "status": "stuck", ` +
				`"detail": "max_iterations reached"}`,
		}},
		// A pathology row missing its keys takes the '?' and '' defaults.
		{name: "a pathology row with no keys", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100, "tokens_out": 10, ` +
				`"elapsed_ms": 900, "tool_pathologies": [{}]}`,
		}},
		// A present null is falsy, so `or []` makes it an empty list.
		{name: "a null tool_pathologies is empty", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 100, "tokens_out": 10, ` +
				`"elapsed_ms": 900, "tool_pathologies": null}`,
		}},

		// fresh_tokens is `max(0, tokens - cache_read)`. A stamp claiming
		// more cache reads than the step spent must not go negative — and
		// the only place that shows is the evidence, where a negative
		// count would render.
		{name: "more cache reads than tokens", lines: []string{
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "a", "status": "done", "tokens_in": 1000, ` +
				`"tokens_out": 0, "cache_read_tokens": 5000, "elapsed_ms": 130000}`,
			`{"event_type": "step_done", "loop_id": "L1", "step_idx": 2, ` +
				`"step": "b", "status": "done", "tokens_in": 300000, ` +
				`"tokens_out": 0, "elapsed_ms": 900}`,
			step(3, "done", "c", 100, 10, 900),
		}},
		// Two loop_done rows: Python takes `loop_done[0]`, the FIRST.
		{name: "two loop_done rows use the first", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			`{"event_type": "loop_done", "loop_id": "L1", "status": "stuck", ` +
				`"detail": "max_iterations reached"}`,
			`{"event_type": "loop_done", "loop_id": "L1", "status": "done", ` +
				`"detail": "all steps complete"}`,
		}},
		{name: "two loop_done rows, the churn one second", lines: []string{
			step(1, "done", "first", 100, 10, 900),
			`{"event_type": "loop_done", "loop_id": "L1", "status": "done", ` +
				`"detail": "all steps complete"}`,
			`{"event_type": "loop_done", "loop_id": "L1", "status": "stuck", ` +
				`"detail": "max_iterations reached"}`,
		}},
		// --- shapes that are not steps ------------------------------------
		{name: "events that are not steps are ignored", lines: []string{
			`{"event_type": "loop_start", "loop_id": "L1", "goal": "build"}`,
			`{"event_type": "note", "loop_id": "L1", "summary": "hello"}`,
			step(1, "done", "first", 100, 10, 900),
		}},
		// step_stuck is a profile too, and "stuck" counts as blocked.
		{name: "a step_stuck event", lines: []string{
			`{"event_type": "step_stuck", "loop_id": "L1", "step_idx": 1, ` +
				`"step": "s", "status": "stuck", "tokens_in": 0, "tokens_out": 0, ` +
				`"elapsed_ms": 61000}`,
		}},
		// A status the code does not name is neither done nor blocked, so
		// it counts toward steps_total only.
		{name: "an unrecognised status", lines: []string{
			step(1, "running", "first", 100, 10, 900),
		}},
	}

	p := pyprobe.Probe{Marker: "introspect.py"}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ws := seedEvents(t, tc.lines)
			probe := pyprobe.Probe{Marker: p.Marker, Workspace: ws}
			var want diagProbe
			probe.RunJSON(t, pyDiagnoseSrc, &want, pyprobe.Arg(t, map[string]any{
				"loop_id": "L1", "project": "proj",
			}))

			got := Diagnose(decodeEvents(t, tc.lines), "L1", "proj")
			row, err := got.MarshalRow()
			if err != nil {
				t.Fatalf("MarshalRow: %v", err)
			}
			if row != want.Row {
				t.Errorf("persisted row differs\ncpython: %s\n     go: %s",
					want.Row, row)
			}
			if s := got.Summary(); s != want.Summary {
				t.Errorf("summary differs\ncpython: %s\n     go: %s", want.Summary, s)
			}
			if len(got.TokenProfile) != len(want.TokenProfile) {
				t.Fatalf("token_profile length: cpython %d, go %d",
					len(want.TokenProfile), len(got.TokenProfile))
			}
			for i, w := range want.TokenProfile {
				checkProfileRow(t, "token", i, got.TokenProfile[i], w,
					"step", "tokens", "status")
			}
			for i, w := range want.TimingProfile {
				checkProfileRow(t, "timing", i, got.TimingProfile[i], w,
					"step", "elapsed_ms", "status")
			}
		})
	}
}

func checkProfileRow(t *testing.T, which string, i int, got pyval.Obj,
	want map[string]any, keys ...string) {
	t.Helper()
	for _, k := range keys {
		g, _ := got.Get(k)
		w := want[k]
		// The probe's numbers come back through encoding/json as float64;
		// compare the RENDERED value so an int/float difference in the port
		// is still visible while the transport's own retyping is not.
		if pyval.Str(pyval.Plain(g)) != normNum(w) {
			t.Errorf("%s_profile[%d].%s: cpython %v, go %v", which, i, k, w, g)
		}
	}
}

func normNum(v any) string {
	if f, ok := v.(float64); ok && f == float64(int64(f)) {
		return pyval.Str(int(f))
	}
	return pyval.Str(v)
}

// pyProfilePropsSrc reads the two derived properties off a StepProfile
// built directly, with no events file and no diagnose_loop underneath.
const pyProfilePropsSrc = `
import json, sys
import introspect

out = []
for c in json.loads(sys.argv[1]):
    p = introspect.StepProfile(
        step_idx=0, text="", status="done",
        tokens=c["tokens"], elapsed_ms=0,
        cache_read_tokens=c["cache"], tokens_in=c["tokens_in"],
        tokens_out=c["tokens_out"], model=c["model"])
    out.append({"fresh": p.fresh_tokens, "cost": repr(p.cost_usd)})
print(json.dumps(out))
`

// TestStepProfilePropertiesMatchCPython pins fresh_tokens and cost_usd
// directly, because Diagnose cannot see either one at its interesting
// values.
//
// fresh_tokens is `max(0, tokens - cache_read_tokens)`, and every use of it
// inside diagnose_loop is a `>` against a positive threshold (200,000 /
// 50,000 / 1,000) or a print of a value that already cleared one. A stamp
// claiming more cache reads than total tokens therefore reads 0 or reads
// negative and answers identically either way — the mutant that deletes the
// clamp survived a 45-mutant battery, not because the clamp is dead but
// because the ONLY caller that can see it is a caller of the property.
// That caller is this test.
func TestStepProfilePropertiesMatchCPython(t *testing.T) {
	type kase struct {
		Tokens    int    `json:"tokens"`
		Cache     int    `json:"cache"`
		TokensIn  int    `json:"tokens_in"`
		TokensOut int    `json:"tokens_out"`
		Model     string `json:"model"`
	}
	cases := []kase{
		{0, 0, 0, 0, ""},
		{1000, 0, 900, 100, "sonnet"},
		{1000, 1000, 1000, 0, "sonnet"},
		// Cache reads exceeding the total: the clamped arm.
		{1000, 5000, 1000, 0, "sonnet"},
		{0, 1, 0, 0, "haiku"},
		{1, 2, 1, 0, "claude-opus-4-6"},
		// ...and exceeding tokens_in too, which estimate_cost clamps on its
		// own side with `max(0, min(cache, tokens_in))`.
		{100, 9_000_000, 100, 0, "grok-4.5"},
		{4_012_000, 3_900_000, 4_000_000, 12_000, "claude-opus-4-6"},
		{700_000, 500_000, 700_000, 0, "grok-4.5"},
		{200_000, 0, 200_000, 0, "an unknown model"},
		// Negative stamps: nothing writes them, but the store is a text
		// file and the clamp is what decides the answer if one appears.
		{-5, 0, -5, 0, "sonnet"},
		{100, -100, 100, 0, "sonnet"},
	}
	var want []struct {
		Fresh int    `json:"fresh"`
		Cost  string `json:"cost"`
	}
	probe := pyprobe.Probe{Marker: "introspect.py"}
	probe.RunJSON(t, pyProfilePropsSrc, &want, pyprobe.Arg(t, cases))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}
	for i, c := range cases {
		p := StepProfile{
			Tokens: c.Tokens, CacheReadTokens: c.Cache,
			TokensIn: c.TokensIn, TokensOut: c.TokensOut, Model: c.Model,
		}
		if got := p.FreshTokens(); got != want[i].Fresh {
			t.Errorf("case %d fresh_tokens: cpython %d, go %d",
				i, want[i].Fresh, got)
		}
		if got := pyval.Repr(p.CostUSD()); got != want[i].Cost {
			t.Errorf("case %d cost_usd: cpython %s, go %s",
				i, want[i].Cost, got)
		}
	}
}
