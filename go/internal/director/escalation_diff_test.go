package director

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/runs"
)

// handle_escalation is not a function that returns a value — it is a
// function that WRITES: a queue entry, a calibration row, a stop verdict
// in two stores, an operator artifact, and an events row. Four actions,
// each writing a different subset, and the subsets are the contract.
//
// So these tests drive BOTH runtimes through the same model reply in
// matched workspaces and diff the whole workspace afterwards, rather than
// asserting on the returned decision alone. The decision is the part
// easiest to get right.

func srcDir(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p, "director.py")); err != nil {
		t.Skipf("python source tree unavailable: %v", err)
	}
	return p
}

// runPyIn runs a probe with MARO_WORKSPACE pointed at ws, refusing to run
// if that resolves anywhere near the live workspace — the rule from the
// 2026-08-16 live-ledger overwrite. The assertion is made on the PYTHON
// side, where the resolution actually happens.
func runPyIn(t *testing.T, ws, src string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	guard := `
import os, sys
_ws = os.environ.get("MARO_WORKSPACE", "")
_home = os.path.realpath(os.path.expanduser("~/.maro"))
if not _ws or os.path.commonpath([os.path.realpath(_ws), _home]) == _home:
    raise SystemExit("refusing to run: MARO_WORKSPACE is %r" % _ws)
import orch_items, runs
for _p in (orch_items.memory_dir(), orch_items.projects_root(), runs.runs_root()):
    if not str(_p).startswith(os.path.realpath(_ws)) and not str(_p).startswith(_ws):
        raise SystemExit("refusing to run: %s resolved outside %s" % (_p, _ws))
`
	cmd := exec.Command("python3", append([]string{"-c", guard + src}, args...)...)
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDir(t), "MARO_WORKSPACE="+ws)
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("CPython probe failed: %v", err)
	}
	return string(out)
}

// The probe drives the real handle_escalation with a scripted adapter and
// a frozen jitter, then reports the decision plus every file the call
// wrote. Freezing the jitter is not a convenience: random.randint sits in
// the cadence path, and a differential that let it run would compare two
// different next_checkin_depth values and call the port broken.
const pyEscalationSrc = `
import json, os, sys, glob
from pathlib import Path

task, reply, freeze = json.loads(sys.argv[1])

import director
from llm import LLMResponse

class _Scripted:
    def complete(self, messages, **kwargs):
        _Scripted.messages = messages
        _Scripted.kwargs = kwargs
        if reply is None:
            raise RuntimeError("scripted adapter failure")
        return LLMResponse(content=reply)

director._checkin_jitter = lambda: freeze

# handle_escalation's excepts are NARROW in two places, so "it raised" is
# one of its observable outcomes and not a probe failure. A harness that
# died here could not compare the case that matters most: an escalation
# that raises has written a PREFIX of its artifacts, and which prefix is
# the contract.
d, raised = None, None
try:
    d = director.handle_escalation(task, adapter=_Scripted())
except BaseException as e:
    raised = {"cls": type(e).__name__, "msg": str(e)}

ws = Path(os.environ["MARO_WORKSPACE"])

def _read(p):
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None

files = {}
for p in sorted(ws.rglob("*")):
    if not p.is_file():
        continue
    # .lock sidecars are reported by NAME with empty content. Skipping
    # them by name is what let the port route memory/events.jsonl through
    # the locked appender for a whole tranche: the row bytes matched, and
    # the one artifact that said otherwise was the file the harness had
    # been told to ignore. Their CONTENT is a pid-ish scratch byte on
    # neither side's contract, so only existence is compared.
    files[str(p.relative_to(ws))] = "" if p.name.endswith(".lock") else _read(p)

# The REQUEST is a compared surface too. It was captured and never read
# for two rounds, which reads as though the prompt and the sampling
# arguments were being diffed when nothing was: a drifted system prompt,
# a changed max_tokens, or a lost no_tools would all have passed.
_msgs = [{"role": m.role, "content": m.content}
         for m in getattr(_Scripted, "messages", [])]
_kw = {k: v for k, v in getattr(_Scripted, "kwargs", {}).items()}

sys.stdout.write(json.dumps({
    "raised": raised,
    "action": d.action if d else "",
    "reasoning": d.reasoning if d else "",
    "followup_task_id": d.followup_task_id if d else None,
    "summary_for_user": d.summary_for_user if d else "",
    "decision_class": d.decision_class if d else "",
    "confidence": d.confidence if d else 0,
    "files": files,
    "messages": _msgs,
    "kwargs": _kw,
}))
`

type escalationCase struct {
	name  string
	task  map[string]any
	reply string
}

// reply builds a model reply. Spelled as raw text, not marshalled from a
// Go value, because the parse under test starts at "carve JSON out of
// whatever the model said".
func reply(fields string) string {
	return "Here is my call.\n\n```json\n{" + fields + "}\n```\n"
}

func TestHandleEscalationMatchesCPython(t *testing.T) {
	cases := []escalationCase{
		{"close stamps a verdict", map[string]any{
			"job_id": "job-close01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 3,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "the completed work answers the core question",
			"summary_for_user": "closing at depth 3"`)},

		{"surface stamps nothing", map[string]any{
			"job_id": "job-surf01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 3,
		}, reply(`"action": "surface", "decision_class": "user_challenge",
			"confidence": 8, "reasoning": "contradictory signals",
			"summary_for_user": "needs a human"`)},

		{"a user_challenge overrides a close", map[string]any{
			"job_id": "job-chal01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 2,
		}, reply(`"action": "close", "decision_class": "user_challenge",
			"confidence": 9, "reasoning": "policy question",
			"summary_for_user": "operator decides"`)},

		{"low confidence overrides to surface and prefixes the summary", map[string]any{
			"job_id": "job-lowc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 2, "reasoning": "not sure",
			"summary_for_user": "unclear"`)},

		{"taste confidence only adds a caveat", map[string]any{
			"job_id": "job-tast01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 0,
		}, reply(`"action": "close", "decision_class": "taste",
			"confidence": 6, "reasoning": "judgment call",
			"summary_for_user": "closing"`)},

		{"continue enqueues at depth+1", map[string]any{
			"job_id": "job-cont01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 0,
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		// Every continue case above sits below the first check-in depth, so
		// FireCheckin — a fourteen-key payload with a three-sentence
		// redirect hint, i.e. exactly this port's recurring bug family —
		// had never been compared to anything. These three drive it: the
		// first check-in from a bare chain, one from a chain already
		// carrying ancestry (where the goal comes from parent_goal rather
		// than the escalation reason), and a narrow, whose branch fires the
		// same call from a different place.
		{"a continue past the first depth fires a check-in", map[string]any{
			"job_id": "job-chk001", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 3,
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		{"a check-in prefers the ancestry goal over the reason", map[string]any{
			"job_id": "job-chk002", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 6,
			"origin": map[string]any{
				"parent_goal":        "the café ask → the original one 😀",
				"parent_handle_id":   "h-77",
				"next_checkin_depth": 7,
				"checkins_sent":      2,
			},
		}, reply(`"action": "continue", "decision_class": "taste",
			"confidence": 6, "reasoning": "still converging",
			"summary_for_user": "another pass"`)},

		// The check-in's goal and reason are bounded at 400, and every
		// other check-in case here is far under that — so the bound was
		// decoration until this case: the mutant that dropped it survived
		// two battery rounds against cases whose goals were forty runes
		// long. Multi-byte, because the cut counts RUNES.
		{"a check-in goal past its 400 bound", map[string]any{
			"job_id": "job-chk004", "parent_job_id": "loop-parent-1",
			"reason":             strings.Repeat("the café path → 😀 ", 60),
			"continuation_depth": 3,
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		{"a narrow past the first depth fires the same check-in", map[string]any{
			"job_id": "job-chk003", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 4,
		}, reply(`"action": "narrow", "decision_class": "mechanical",
			"confidence": 8, "reasoning": "too broad",
			"revised_goal": "port only the close branch",
			"summary_for_user": "narrowing"`)},

		// The event write is wrapped in a blanket except on the Python
		// side, and `task.get("reason","")[:80]` raises inside it for a
		// non-string reason — so CPython writes NO events row at all. A
		// port that coerced with str() would write one, and the only
		// witness is a file that is present on one side and absent on the
		// other.
		{"a non-string reason swallows the whole event row", map[string]any{
			"job_id": "job-rawrs01", "parent_job_id": "loop-parent-1",
			"reason": map[string]any{"nested": "context"}, "continuation_depth": 1,
		}, reply(`"action": "surface", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "needs a human",
			"summary_for_user": "over to you"`)},

		{"a null reason does the same", map[string]any{
			"job_id": "job-rawrs02", "parent_job_id": "loop-parent-1",
			"reason": nil, "continuation_depth": 1,
		}, reply(`"action": "surface", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "needs a human",
			"summary_for_user": "over to you"`)},

		// project is the RAW parent_job_id, and it is NOT sliced — so a
		// non-string one reaches json.dumps and is written as whatever it
		// is, where the goal slice above would already have raised.
		{"a non-string parent_job_id rides into the row unsliced", map[string]any{
			"job_id": "job-rawpp01", "parent_job_id": 4242,
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "surface", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "needs a human",
			"summary_for_user": "over to you"`)},

		// A close with NO parent to stamp. The guard that returns early is
		// easy to read as redundant — both writers it calls guard on an
		// empty id themselves — but "returns early" and "calls two writers
		// that decline" differ in what they leave behind: the outcome
		// writer takes its lock BEFORE it can tell whether the ledger
		// holds a matching row, so the second shape drops an
		// outcomes.jsonl.lock into a workspace where CPython leaves none.
		{"a close with no parent stamps nothing and locks nothing", map[string]any{
			"job_id": "job-noprnt1", "parent_job_id": "",
			"reason": "audit the escalation lane", "continuation_depth": 3,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closed"`)},

		// `job_id[:8]` is a bare slice inside the artifact block's own
		// try/except, so a non-string job_id writes NO artifact — while
		// everything after that block still runs. The decision, the
		// calibration row and the event all survive; only the operator's
		// file is missing, which is the half a caller would never notice.
		{"a non-string job_id writes no artifact", map[string]any{
			"job_id": 991234, "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closed"`)},

		{"narrow without a revised goal falls back to surface", map[string]any{
			"job_id": "job-narw01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "narrow", "decision_class": "mechanical",
			"confidence": 8, "reasoning": "too broad",
			"summary_for_user": "narrowing"`)},

		{"narrow with a revised goal enqueues it", map[string]any{
			"job_id": "job-narw02", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "narrow", "decision_class": "mechanical",
			"confidence": 8, "reasoning": "too broad",
			"revised_goal": "port only the close branch",
			"summary_for_user": "narrowing"`)},

		// The four normalizations on the two vocabulary fields, each with
		// a case that fails if it is dropped. Every earlier case fed clean
		// lowercase values, so `strip().lower()` was decoration: mutants
		// removing either survived.
		{"a shouting action is lowercased", map[string]any{
			"job_id": "job-case001", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "CLOSE", "decision_class": "TASTE",
			"confidence": 9, "reasoning": "done", "summary_for_user": "closed"`)},

		{"a padded action is stripped", map[string]any{
			"job_id": "job-case002", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "  close \t", "decision_class": " taste ",
			"confidence": 9, "reasoning": "done", "summary_for_user": "closed"`)},

		// The DEFAULTS, which are only visible when the key is absent —
		// and an absent action defaulting to close instead of surface is
		// the difference between "nobody decided" and a recorded verdict.
		{"an absent action defaults to surface", map[string]any{
			"job_id": "job-dflt001", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"confidence": 9, "reasoning": "no action given",
			"summary_for_user": "unclear"`)},

		{"an absent decision class defaults to mechanical", map[string]any{
			"job_id": "job-dflt002", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "confidence": 9,
			"reasoning": "done", "summary_for_user": "closed"`)},

		// The LOW clamp. Every earlier out-of-range case was above 10, so
		// dropping `if confidence < 1` changed nothing anyone checked —
		// while a 0 reported as 0 rather than 1 is a calibration row that
		// says something no scale in this system defines.
		{"a zero confidence clamps up to one", map[string]any{
			"job_id": "job-clmp001", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 0, "reasoning": "no idea", "summary_for_user": "closing"`)},

		{"a negative confidence clamps up to one", map[string]any{
			"job_id": "job-clmp002", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": -7, "reasoning": "no idea", "summary_for_user": "closing"`)},

		{"an unknown action becomes surface", map[string]any{
			"job_id": "job-unkn01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "abandon", "decision_class": "wat",
			"confidence": 9, "reasoning": "off script",
			"summary_for_user": "?"`)},

		{"a confidence spelled as a string still parses", map[string]any{
			"job_id": "job-strc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": "9", "reasoning": "spelled",
			"summary_for_user": "closing"`)},

		{"an unreadable confidence takes the default", map[string]any{
			"job_id": "job-badc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": "high", "reasoning": "unreadable",
			"summary_for_user": "closing"`)},

		{"a float confidence truncates", map[string]any{
			"job_id": "job-fltc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 7.9, "reasoning": "truncated",
			"summary_for_user": "closing"`)},

		{"confidence past the range clamps", map[string]any{
			"job_id": "job-clmp01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 99, "reasoning": "clamped",
			"summary_for_user": "closing"`)},

		{"prose that breaks encoding/json rides through every store", map[string]any{
			"job_id": "job-pros01", "parent_job_id": "loop-parent-1",
			"reason":             "prefer a > b & not c < d in the café path → retry",
			"continuation_depth": 2,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 8,
			"reasoning": "answered a > b in the café path → not the ask",
			"summary_for_user": "closing on a → b"`)},

		{"an empty object is treated as no reply at all", map[string]any{
			"job_id": "job-empt01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, "{}"},

		{"a reply with no JSON at all surfaces", map[string]any{
			"job_id": "job-nojs01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, "I would rather not say."},

		{"a missing continuation_depth reads as zero", map[string]any{
			"job_id": "job-nodp01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane",
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 8, "reasoning": "no depth",
			"summary_for_user": "closing"`)},

		{"a missing job_id reads as unknown", map[string]any{
			"parent_job_id": "loop-parent-1",
			"reason":        "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "surface", "decision_class": "mechanical",
			"confidence": 8, "reasoning": "no job id",
			"summary_for_user": "surfacing"`)},

		// --- the check-in payload's own raw fields --------------------
		//
		// Every check-in case above uses STRING ids, and the two
		// non-string-id cases never fire a check-in — so four fields of a
		// fourteen-key payload had never been compared with anything but a
		// string in them. `job_id` and `parent_job_id` are raw in Python's
		// dict literal; `handle_id` is `str(x or "")`, which carries a
		// truthy non-string; and `parent_goal` is an `or` fallback on the
		// RAW value, so a truthy non-string one becomes the goal instead of
		// falling through to the escalation reason.
		{"a check-in carries non-string ids and ancestry", map[string]any{
			"job_id": 4242, "parent_job_id": 991,
			"reason": "audit the escalation lane", "continuation_depth": 3,
			"origin": map[string]any{
				"parent_handle_id": 77, "parent_goal": 55,
			},
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		// A FALSY parent_goal falls through to the reason; a falsy
		// parent_handle_id becomes "" rather than "0" or "False".
		{"a check-in with falsy ancestry falls through", map[string]any{
			"job_id": "job-fals01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 3,
			"origin": map[string]any{
				"parent_handle_id": 0, "parent_goal": "",
			},
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		// int(checkins_sent) is BARE — no local try — so the raise travels
		// to the blanket except around the whole check-in: the continuation
		// is still enqueued, and NO notification is written at all.
		{"an unreadable check-in count silences the notification", map[string]any{
			"job_id": "job-cnt001", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 3,
			"origin": map[string]any{"checkins_sent": "two", "next_checkin_depth": 2},
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		// --- the origin constructor's own refusals --------------------
		//
		// `Origin(x)` is `dict(x)`. A truthy non-mapping raises INSIDE the
		// spawn branch's try, so CPython enqueues nothing, fires no
		// check-in, and surfaces with the exception's own message in the
		// ledger row and the operator summary.
		{"a string origin refuses the whole enqueue", map[string]any{
			"job_id": "job-orgs01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
			"origin": "loop-parent-1",
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		{"a numeric origin refuses with a different message", map[string]any{
			"job_id": "job-orgn01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
			"origin": 7,
		}, reply(`"action": "narrow", "decision_class": "mechanical",
			"confidence": 8, "reasoning": "too broad",
			"revised_goal": "just the lock shapes",
			"summary_for_user": "narrowing"`)},

		// A list of PAIRS is a mapping to dict(), and this one carries the
		// cadence forward like any other origin.
		{"a list-of-pairs origin is a perfectly good origin", map[string]any{
			"job_id": "job-orgp01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 3,
			"origin": []any{[]any{"parent_goal", "the original ask"},
				[]any{"checkins_sent", 1}},
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		// A non-numeric cadence field raises from the comparison rather
		// than the constructor — same except, different message.
		{"an unreadable cadence threshold refuses the enqueue", map[string]any{
			"job_id": "job-orgc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
			"origin": map[string]any{"next_checkin_depth": "soon"},
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		// --- depth is raw, and a float depth writes ".0" everywhere ----
		//
		// Nothing raises for a float, which is why this one is invisible to
		// a test that only drives bad types: the calibration row, the event
		// detail, the operator artifact and the stop evidence in BOTH
		// outcomes.jsonl and metadata.json each carry "2.0".
		{"a float depth keeps its .0 in five places", map[string]any{
			"job_id": "job-flt001", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane",
			// json.Number, not a Go float: `json.Marshal(2.0)` emits `2`,
			// so a float64 fixture hands CPython an INT and the case
			// silently tests nothing. The literal has to survive the wire
			// for the value under test to reach the other runtime — the
			// same fixture artifact that made an r11 case report a port bug
			// that was not there.
			"continuation_depth": json.Number("2.0"),
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closed"`)},

		// …and it survives `depth + 1` as a float into the queue row and
		// the check-in payload.
		{"a float depth stays a float through the enqueue", map[string]any{
			"job_id": "job-flt002", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": json.Number("2.5"),
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		// A non-numeric depth raises at `depth + 1`, before the origin is
		// even built.
		{"a string depth refuses the enqueue at depth+1", map[string]any{
			"job_id": "job-flt003", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": "deep",
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},

		// A close whose parent_job_id is NULL. `if not loop_id: return` is
		// a truthiness gate, and a port that spelled the null "None" first
		// walks past it — creating memory/ and taking
		// memory/outcomes.jsonl.lock in a workspace where CPython leaves
		// neither. This is the r11 lock-shape finding one branch over, and
		// it is why the file SET is compared and not just the rows.
		{"a close with a null parent locks nothing", map[string]any{
			"job_id": "job-null01", "parent_job_id": nil,
			"reason": "audit the escalation lane", "continuation_depth": 2,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closed"`)},

		// The confidence read's except tuple is (TypeError, ValueError),
		// and `json.loads` admits the bare tokens NaN and Infinity — so one
		// model reply reaches BOTH sides of that tuple. These four are a
		// matched set on purpose: a port that answered only "int() failed"
		// gets the first right by accident and the second catastrophically
		// wrong, and the two that follow are the same read at the arbitrary-
		// precision boundary, where CPython succeeds and clamps.
		{"a NaN confidence is caught and defaults", map[string]any{
			"job_id": "job-nanc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": NaN, "reasoning": "the work answers it",
			"summary_for_user": "closing"`)},

		{"an Infinity confidence is NOT caught", map[string]any{
			"job_id": "job-infc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": Infinity, "reasoning": "the work answers it",
			"summary_for_user": "closing"`)},

		{"a confidence past int64 clamps high", map[string]any{
			"job_id": "job-bigc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 99999999999999999999,
			"reasoning": "the work answers it",
			"summary_for_user": "closing"`)},

		{"a confidence past int64 the other way clamps low", map[string]any{
			"job_id": "job-negc01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": -99999999999999999999,
			"reasoning": "the work answers it",
			"summary_for_user": "closing"`)},

		// `write_event(goal=task.get("reason", "")[:80])`. A queue row's
		// reason is whatever the enqueuing side put there, and this lane
		// enqueues its own: an escalation that carries a structured ask
		// writes a reason that is a dict, and `{...}[:80]` is a KeyError —
		// a dict lookup with a slice for a key. The event row is skipped
		// and everything else still happens.
		{"an unsliceable reason drops only the event row", map[string]any{
			"job_id": "job-dictr1", "parent_job_id": "loop-parent-1",
			"reason":             map[string]any{"ask": "why", "of": "the lane"},
			"continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closed"`)},

		// The check-in's `int(origin.get("checkins_sent", 0))` is BARE —
		// no local try — so a non-numeric count raises into the blanket
		// except around the whole emit: no notify, no ledger row, no hook,
		// and the escalation itself completes regardless.
		{"a non-numeric checkins_sent emits no check-in", map[string]any{
			"job_id": "job-badck01", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 3,
			"origin": map[string]any{"checkins_sent": "two"},
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded remaining work",
			"summary_for_user": "another pass"`)},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			const frozenJitter = 5
			goWS, pyWS := t.TempDir(), t.TempDir()

			// The Go side, driven by the same reply.
			prev := checkinRandInt
			checkinRandInt = func(lo, hi int) int { return frozenJitter }
			defer func() { checkinRandInt = prev }()

			fake := &llm.Fake{Script: []string{c.reply}}
			got, gotErr := HandleEscalation(context.Background(), goWS,
				objOf(c.task), EscalationOptions{Adapter: fake})

			arg, err := json.Marshal([]any{c.task, c.reply, frozenJitter})
			if err != nil {
				t.Fatal(err)
			}
			var want struct {
				Raised *struct {
					Cls string `json:"cls"`
					Msg string `json:"msg"`
				} `json:"raised"`
				Action         string            `json:"action"`
				Reasoning      string            `json:"reasoning"`
				FollowupTaskID *string           `json:"followup_task_id"`
				SummaryForUser string            `json:"summary_for_user"`
				DecisionClass  string            `json:"decision_class"`
				Confidence     int               `json:"confidence"`
				Files          map[string]string `json:"files"`
				Messages       []struct {
					Role    string `json:"role"`
					Content string `json:"content"`
				} `json:"messages"`
				Kwargs map[string]any `json:"kwargs"`
			}
			if err := json.Unmarshal([]byte(runPyIn(t, pyWS, pyEscalationSrc, string(arg))), &want); err != nil {
				t.Fatal(err)
			}

			// Whether it raised at all comes first: the fields below are
			// meaningless for a call that never returned one, and the FILES
			// still are not — a raise leaves a prefix of the writes behind,
			// and comparing that prefix is the point of the case.
			if (gotErr != nil) != (want.Raised != nil) {
				if gotErr != nil {
					t.Fatalf("raised %v; CPython returned a decision", gotErr)
				}
				t.Fatalf("returned a decision; CPython raises %s: %s",
					want.Raised.Cls, want.Raised.Msg)
			}
			if want.Raised != nil {
				pe, ok := gotErr.(*pyval.PyErr)
				if !ok {
					t.Errorf("raised %v, which carries no exception class; "+
						"CPython raises %s", gotErr, want.Raised.Cls)
				} else if pe.Class != want.Raised.Cls {
					t.Errorf("raised %s, CPython raises %s — the two are "+
						"caught by different excepts upstream",
						pe.Class, want.Raised.Cls)
				}
				compareWorkspaces(t, goWS, want.Files)
				return
			}

			if got.Action != want.Action {
				t.Errorf("action = %q, CPython says %q", got.Action, want.Action)
			}
			if got.Reasoning != want.Reasoning {
				t.Errorf("reasoning = %q, CPython says %q", got.Reasoning, want.Reasoning)
			}
			if got.SummaryForUser != want.SummaryForUser {
				t.Errorf("summary_for_user = %q, CPython says %q",
					got.SummaryForUser, want.SummaryForUser)
			}
			if got.DecisionClass != want.DecisionClass {
				t.Errorf("decision_class = %q, CPython says %q",
					got.DecisionClass, want.DecisionClass)
			}
			if got.Confidence != want.Confidence {
				t.Errorf("confidence = %d, CPython says %d", got.Confidence, want.Confidence)
			}
			// A followup id is a fresh uuid on both sides, so only its
			// PRESENCE can be compared — but presence is the whole
			// contract, since it is what tells a caller the chain is alive.
			gotFollowup := got.FollowupTaskID != ""
			wantFollowup := want.FollowupTaskID != nil && *want.FollowupTaskID != ""
			if gotFollowup != wantFollowup {
				t.Errorf("followup present = %v, CPython says %v (%q)",
					gotFollowup, wantFollowup, got.FollowupTaskID)
			}

			// The REQUEST, not just the response. Both sides drive a
			// scripted adapter, so the prompt and the sampling arguments
			// are as comparable as anything the run wrote to disk — and a
			// drifted system prompt is exactly the kind of divergence no
			// on-disk artifact would ever show.
			if len(fake.Msgs) != 1 {
				t.Errorf("%d LLM call(s), want exactly 1", len(fake.Msgs))
			} else if n := len(fake.Msgs[0]); n != len(want.Messages) {
				t.Errorf("%d message(s) in the call, CPython sent %d",
					n, len(want.Messages))
			} else {
				for i, m := range fake.Msgs[0] {
					if m.Role != want.Messages[i].Role {
						t.Errorf("message %d role = %q, CPython %q",
							i, m.Role, want.Messages[i].Role)
					}
					if m.Content != want.Messages[i].Content {
						t.Errorf("message %d content diverges:\n go: %q\n py: %q",
							i, m.Content, want.Messages[i].Content)
					}
				}
			}
			if len(fake.Opts) == 1 && want.Kwargs != nil {
				o := fake.Opts[0]
				if n, _ := want.Kwargs["max_tokens"].(float64); int(n) != o.MaxTokens {
					t.Errorf("max_tokens = %d, CPython %v", o.MaxTokens, want.Kwargs["max_tokens"])
				}
				if f, _ := want.Kwargs["temperature"].(float64); f != o.Temperature {
					t.Errorf("temperature = %v, CPython %v", o.Temperature, want.Kwargs["temperature"])
				}
				if p, _ := want.Kwargs["purpose"].(string); p != o.Purpose {
					t.Errorf("purpose = %q, CPython %q", o.Purpose, p)
				}
				// no_tools=True on the Python side is Tools being EMPTY here:
				// this port injects tools into the prompt rather than passing
				// a flag, so the flag's effect is what can be compared.
				if noTools, _ := want.Kwargs["no_tools"].(bool); noTools && len(o.Tools) != 0 {
					t.Errorf("CPython asked for no_tools and the port sent %d tool(s)",
						len(o.Tools))
				}
			}

			compareWorkspaces(t, goWS, want.Files)
		})
	}
}

// compareWorkspaces diffs the SET of files written and the CONTENT of the
// ones whose content is deterministic.
//
// The set is the half that catches the misplaced write: a close that
// wrote no artifact, a surface that stamped a verdict, a queue entry
// under the wrong lane directory. Content is compared everywhere the
// bytes are reproducible; the three files carrying a fresh uuid, a wall
// clock or a pid are compared field-wise instead by their own tests.
func compareWorkspaces(t *testing.T, ws string, want map[string]string) {
	t.Helper()
	got := map[string]string{}
	err := filepath.Walk(ws, func(p string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		rel, rerr := filepath.Rel(ws, p)
		if rerr != nil {
			return nil
		}
		// Lock sidecars ride in the SET with empty content, matching the
		// probe: which ledgers each runtime locks is a real contract (one
		// of them is documented as deliberately unlocked), and it is
		// invisible in the ledger bytes.
		if strings.HasSuffix(p, ".lock") {
			got[rel] = ""
			return nil
		}
		raw, rerr := os.ReadFile(p)
		if rerr != nil {
			return nil
		}
		got[rel] = string(raw)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	gotSet, wantSet := map[string]bool{}, map[string]bool{}
	for n := range got {
		gotSet[maskVolatileName(n)] = true
	}
	for n := range want {
		wantSet[maskVolatileName(n)] = true
	}
	for n := range wantSet {
		if !gotSet[n] {
			t.Errorf("CPython wrote %s and this port did not", n)
		}
	}
	for n := range gotSet {
		if !wantSet[n] {
			t.Errorf("this port wrote %s and CPython did not", n)
		}
	}

	// The operator artifact is fully deterministic — no id, no clock — so
	// it is compared byte for byte. It is also the only human-facing
	// output of a surface, which makes it the one a reader would notice.
	for name, body := range want {
		if !strings.HasSuffix(name, ".md") {
			continue
		}
		if got[name] != body {
			t.Errorf("%s is not CPython's:\n--- go ---\n%s\n--- py ---\n%s",
				name, got[name], body)
		}
	}

	// Every .json file the run left behind, key by key.
	//
	// This arm was missing and the omission was not visible: the file SET
	// comparison above sees `task-*.json`, so a queue entry whose every
	// field was wrong still reported present-and-correct. That is how a
	// continuation was enqueued with a Python-repr string as its goal and
	// a quoted number as its parent id through two review rounds
	// (adversarial r11 round 2, HIGH) — the differential's only content
	// check on a task file lived in a separate test whose two fixtures
	// both used string ids and a string reason. **An exclusion is a
	// hypothesis; this one said "task files carry nothing worth reading"
	// and nobody had tested it.**
	//
	// job_id / run_id / timestamps are masked by KEY, not dropped: whether
	// both runtimes wrote the key at all is still the claim.
	gotByMask := map[string]string{}
	for n, b := range got {
		gotByMask[maskVolatileName(n)] = b
	}
	for name, wantBody := range want {
		if !strings.HasSuffix(name, ".json") {
			continue
		}
		mn := maskVolatileName(name)
		gotBody, ok := gotByMask[mn]
		if !ok {
			continue // the set comparison above already reported it
		}
		gotRow, gerr := pyval.LoadsOrdered(gotBody)
		wantRow, werr := pyval.LoadsOrdered(wantBody)
		if gerr != nil || werr != nil {
			t.Errorf("%s: unreadable (%v / %v)", mn, gerr, werr)
			continue
		}
		go1, ok1 := gotRow.(pyval.Obj)
		wo1, ok2 := wantRow.(pyval.Obj)
		if !ok1 || !ok2 {
			t.Errorf("%s: not an object on one side", mn)
			continue
		}
		sameRow(t, mn, go1, wo1,
			"job_id", "run_id", "timestamps",
			"started_at", "ended_at", "updated_at", "handle_id")
	}

	// And every jsonl ledger, row by row, with the two volatile things
	// masked — the wall clock and a freshly minted task id.
	//
	// This half was missing for a whole round, and the r11 battery is how
	// that surfaced: EIGHT mutants that rewrote the check-in payload
	// (claiming it was blocking, dropping the ancestry goal, drifting the
	// redirect hint, doubling the check-in number) all SURVIVED. The
	// check-in cases had been added to this test believing they covered
	// the payload; without this loop they covered only the fact that a
	// file appeared.
	for name, wantBody := range want {
		if !strings.HasSuffix(name, ".jsonl") {
			continue
		}
		gotRows := parseRows(t, name, got[name])
		wantRows := parseRows(t, name, wantBody)
		if len(gotRows) != len(wantRows) {
			t.Errorf("%s has %d rows, CPython wrote %d:\n--- go ---\n%s\n--- py ---\n%s",
				name, len(gotRows), len(wantRows), got[name], wantBody)
			continue
		}
		for i := range wantRows {
			sameRow(t, name, gotRows[i], wantRows[i], "ts")
		}
	}
}

// parseRows reads a jsonl body into ordered rows, masking the one piece
// of text two runs cannot agree on: the freshly minted followup task id
// inside an event's detail line. The literal "none" is left alone —
// "a followup exists" versus "no followup" is the claim, and it survives.
func parseRows(t *testing.T, name, body string) []pyval.Obj {
	t.Helper()
	rows := []pyval.Obj{}
	for _, line := range strings.Split(strings.TrimSpace(body), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		v, err := pyval.LoadsOrdered(maskFollowup(line))
		if err != nil {
			t.Fatalf("%s: unreadable row %q: %v", name, line, err)
		}
		o, ok := v.(pyval.Obj)
		if !ok {
			t.Fatalf("%s: row is not an object: %q", name, line)
		}
		rows = append(rows, o)
	}
	return rows
}

// maskVolatileName collapses a filename that carries a freshly minted id
// or timestamp down to the part that IS the contract — which directory it
// landed in, and what kind of file it is.
//
// A queue entry is named task-<utc>-<uuid8>.json, so the two runs cannot
// produce the same name and comparing names would report a difference on
// every run that enqueues anything. The directory is the real claim (a
// task written under the wrong lane is a bug this still catches), and the
// entry's FIELDS get their own field-wise differential below.
func maskVolatileName(name string) string {
	base := filepath.Base(name)
	if strings.HasPrefix(base, "task-") {
		// Suffix included: the entry's own lock sidecar carries the same
		// volatile id, and masking only the .json half reported every
		// enqueueing run as a difference in both directions at once.
		return filepath.Join(filepath.Dir(name), "task-*"+filepath.Ext(base))
	}
	return name
}

// objOf turns a test's plain map into the ordered object the port reads,
// RECURSIVELY — a nested map left as a Go map would take the sorted
// fallback path inside the port instead of the ordered one a task loaded
// from disk takes, so the fixture would be exercising the wrong branch.
//
// The transport is JSON in both directions, so the Python side receives
// the same fixture through json.dumps and rebuilds a dict; sorting here
// is what makes the two sides' key order agree without threading an
// explicit order through the test table.
func objOf(m map[string]any) pyval.Obj {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make(pyval.Obj, 0, len(keys))
	for _, k := range keys {
		v := m[k]
		if nested, ok := v.(map[string]any); ok {
			v = objOf(nested)
		}
		out = append(out, pyval.Field{Key: k, Val: v})
	}
	return out
}

// --- the rows the file-set diff cannot compare by bytes -----------------

// copyTree duplicates a seeded workspace so both runtimes start from
// literally the same bytes. Seeding each side separately would stamp two
// different started_at values into metadata.json and report a difference
// that belongs to the fixture, not the writer.
func copyTree(t *testing.T, src, dst string) {
	t.Helper()
	err := filepath.Walk(src, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, rerr := filepath.Rel(src, p)
		if rerr != nil {
			return rerr
		}
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		raw, rerr := os.ReadFile(p)
		if rerr != nil {
			return rerr
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		return os.WriteFile(target, raw, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
}

// readRows parses a jsonl store into ordered objects, so a key-order
// difference is visible rather than laundered by a map.
func readRows(t *testing.T, path string) []pyval.Obj {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var out []pyval.Obj
	for _, line := range strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		v, perr := pyval.LoadsOrdered(line)
		if perr != nil {
			t.Fatalf("unparseable row in %s: %v\n%s", path, perr, line)
		}
		o, ok := v.(pyval.Obj)
		if !ok {
			t.Fatalf("row in %s is not an object: %s", path, line)
		}
		out = append(out, o)
	}
	return out
}

// sameRow compares two ordered objects INCLUDING key order, skipping the
// named volatile keys. Key order is compared because these rows are
// re-serialized by whichever runtime touches them next, and a map-based
// port alphabetizes silently — the store still parses, it just stops
// being one both runtimes wrote the same way.
func sameRow(t *testing.T, what string, got, want pyval.Obj, volatile ...string) {
	t.Helper()
	skip := map[string]bool{}
	for _, k := range volatile {
		skip[k] = true
	}
	var gk, wk []string
	for _, f := range got {
		gk = append(gk, f.Key)
	}
	for _, f := range want {
		wk = append(wk, f.Key)
	}
	if strings.Join(gk, ",") != strings.Join(wk, ",") {
		t.Errorf("%s key order is %v, CPython's is %v", what, gk, wk)
		return
	}
	for i, f := range want {
		if skip[f.Key] {
			continue
		}
		// DumpsCompactPy, NOT json.Marshal(pyval.Plain(v)).
		//
		// Plain maps json.Number("3") to int(3) and json.Number("3.0") to
		// float64(3), and encoding/json then writes `3` for both — so the
		// comparison DESTROYED the int/float distinction at the exact point
		// it was supposed to check it. Plain's own doc says why that
		// matters: str(42) is "42" while str(42.0) is "42.0". Every .json
		// and .jsonl file in this differential goes through here, and only
		// the .md files were compared byte for byte, so seeding a fixture
		// with `"attempt": 2.0` and letting the port write `3` PASSED
		// (adversarial r11 round 3, MEDIUM).
		//
		// It also fixes the nested half: json.Marshal over a map
		// alphabetizes, so a nested object's key ORDER was compared
		// nowhere, while the top-level order two lines up was compared
		// explicitly. DumpsCompactPy walks pyval.Obj in its own order.
		g, gerr := pyval.DumpsCompactPy(got[i].Val)
		w, werr := pyval.DumpsCompactPy(f.Val)
		if gerr != nil || werr != nil {
			t.Fatalf("%s[%s] could not be rendered for comparison: %v / %v",
				what, f.Key, gerr, werr)
		}
		if g != w {
			t.Errorf("%s[%s] = %s, CPython says %s", what, f.Key, g, w)
		}
	}
}

// The calibration row is the self-improvement lane's only record that a
// judgment was made at all, and its `ts` is a FLOAT of epoch seconds
// where every other store in this port writes an ISO string. Spelling it
// as a string would make rows the Python readers cannot compare against
// their own — and both are "a timestamp", so nothing else would notice.
// A table, not one fixture. TestHandleEscalationMatchesCPython compares
// the CONTENT of only the .md artifact, so these two ledgers are compared
// field-wise here and nowhere else — which means a shape absent from the
// fixture is a shape nothing checks. The three added below are the ones
// where the two runtimes can disagree on a TYPE rather than a value:
// `project` is written raw, so a non-string one stays non-string in
// Python and would become a string under any str() coercion; a followup
// id changes the detail line; and an over-long goal and detail take
// different cuts from each other.
func TestTheCalibrationAndEventRowsMatchCPython(t *testing.T) {
	closeReply := reply(`"action": "close", "decision_class": "taste",
		"confidence": 6, "reasoning": "answered a > b in the café path → not the ask",
		"summary_for_user": "closing on a → b"`)

	for _, c := range []escalationCase{
		{"a close with prose in every field", map[string]any{
			"job_id": "job-rows001", "parent_job_id": "loop-parent-9",
			"reason":             "prefer a > b & not c < d in the café path → retry",
			"continuation_depth": 2,
		}, closeReply},

		// project rides in RAW — no slice, no str(). An int stays a JSON
		// number on the Python side, and a port that spelled it would
		// write a JSON string that compares equal to nothing.
		{"a non-string project stays non-string", map[string]any{
			"job_id": "job-rows002", "parent_job_id": 4242,
			"reason": "audit the escalation lane", "continuation_depth": 2,
		}, closeReply},

		{"a null project stays null", map[string]any{
			"job_id": "job-rows003", "parent_job_id": nil,
			"reason": "audit the escalation lane", "continuation_depth": 2,
		}, closeReply},

		// goal is cut at 80 SILENTLY and detail at 200 LOUDLY, from the
		// same call. One fixture with both under their bounds cannot tell
		// a port that clips them the same way from one that does not.
		{"a goal and a detail past their different bounds", map[string]any{
			"job_id":             "job-rows004",
			"parent_job_id":      "loop-parent-9",
			"reason":             strings.Repeat("the café path → ", 40),
			"continuation_depth": 2,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "` + strings.Repeat("because ", 60) + `",
			"summary_for_user": "closing"`)},

		// A continue puts a real followup id into the detail line where
		// every close leaves the literal "none".
		{"a continue names its followup in the detail", map[string]any{
			"job_id": "job-rows005", "parent_job_id": "loop-parent-9",
			"reason": "audit the escalation lane", "continuation_depth": 0,
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded", "summary_for_user": "next"`)},

		// Every reply above answers each field with a scalar, so the ordered
		// parse of the LLM's JSON was only ever asked to preserve TOP-level
		// order. safe_str on a nested object is repr(), and repr walks the
		// nested dict in ITS insertion order — so a port that round-tripped
		// the decoded reply through an unordered map wrote
		// `{'b': [...], 'why': ...}` into escalations.jsonl and the
		// calibration store, and nothing could tell (adversarial r11 round 2,
		// MEDIUM 3). The keys here are deliberately NOT in sorted order.
		{"a nested reasoning object keeps the reply's own key order",
			map[string]any{
				"job_id": "job-rows006", "parent_job_id": "loop-parent-9",
				"reason": "audit the escalation lane", "continuation_depth": 2,
			}, reply(`"action": "close", "decision_class": "taste",
			"confidence": 6,
			"reasoning": {"why": "answered a > b", "b": [1, 2.0, null], "a": true},
			"summary_for_user": "closing"`)},
	} {
		t.Run(c.name, func(t *testing.T) {
			const frozenJitter = 5
			goWS, pyWS := t.TempDir(), t.TempDir()
			prev := checkinRandInt
			checkinRandInt = func(lo, hi int) int { return frozenJitter }
			defer func() { checkinRandInt = prev }()

			mustEscalate(t, context.Background(), goWS, objOf(c.task),
				EscalationOptions{Adapter: &llm.Fake{Script: []string{c.reply}}})

			arg, err := json.Marshal([]any{c.task, c.reply, frozenJitter})
			if err != nil {
				t.Fatal(err)
			}
			runPyIn(t, pyWS, pyEscalationSrc, string(arg))

			for _, f := range []struct {
				rel      string
				volatile []string
			}{
				{filepath.Join("memory", "calibration.jsonl"), []string{"ts"}},
				// detail carries the followup uuid on a continue, so the
				// FIELD is skipped there and its shape is asserted below.
				{filepath.Join("memory", "events.jsonl"), []string{"ts", "detail"}},
			} {
				gotRows := readRows(t, filepath.Join(goWS, f.rel))
				wantRows := readRows(t, filepath.Join(pyWS, f.rel))
				if len(gotRows) != len(wantRows) {
					t.Errorf("%s has %d rows, CPython wrote %d", f.rel, len(gotRows), len(wantRows))
					continue
				}
				for i := range wantRows {
					sameRow(t, f.rel, gotRows[i], wantRows[i], f.volatile...)
				}
			}

			// detail is skipped above only because of the uuid. Compare it
			// with the two ids masked, so everything else in the line —
			// the depth, the separators, the reasoning cut — still has to
			// match.
			gotEv := readRows(t, filepath.Join(goWS, "memory", "events.jsonl"))
			wantEv := readRows(t, filepath.Join(pyWS, "memory", "events.jsonl"))
			if len(gotEv) == len(wantEv) {
				for i := range wantEv {
					g := maskFollowup(gotEv[i].GetString("detail"))
					w := maskFollowup(wantEv[i].GetString("detail"))
					if g != w {
						t.Errorf("event detail = %q, CPython says %q", g, w)
					}
				}
			}

			// And the calibration ts really is a number, not a spelled
			// one. The key-order check above would pass either way.
			rows := readRows(t, filepath.Join(goWS, "memory", "calibration.jsonl"))
			if len(rows) != 1 {
				t.Fatalf("expected one calibration row, got %d", len(rows))
			}
			ts, _ := rows[0].Get("ts")
			if _, isStr := ts.(string); isStr {
				t.Errorf("the calibration ts is a string (%v); Python writes time.time(), a float", ts)
			}
			if pyval.FloatOf(ts) < 1.7e9 {
				t.Errorf("the calibration ts is %v, which is not epoch seconds", ts)
			}
		})
	}
}

// maskFollowup blanks the freshly minted task id in an event detail line,
// leaving the literal "none" alone — the difference between "a followup
// exists" and "no followup" is the claim, and it survives the mask.
func maskFollowup(detail string) string {
	const key = "followup="
	i := strings.Index(detail, key)
	if i < 0 {
		return detail
	}
	rest := detail[i+len(key):]
	end := strings.IndexAny(rest, " ")
	if end < 0 {
		end = len(rest)
	}
	if rest[:end] == "none" {
		return detail
	}
	return detail[:i+len(key)] + "TASK" + rest[end:]
}

// The enqueued continuation is the whole point of a "continue": it is
// what keeps the chain alive. Its id and clock differ between runs, so
// everything else is compared field-wise — including origin, which
// carries the check-in cadence state the NEXT escalation reads.
func TestTheEnqueuedTaskMatchesCPython(t *testing.T) {
	for _, c := range []escalationCase{
		{"continue", map[string]any{
			"job_id": "job-enq001", "parent_job_id": "loop-parent-9",
			"reason": "audit the escalation lane", "continuation_depth": 4,
			"origin": map[string]any{
				"parent_goal": "the original ask", "parent_handle_id": "h-1",
				"next_checkin_depth": 5, "checkins_sent": 1,
			},
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded", "summary_for_user": "next pass"`)},
		{"narrow", map[string]any{
			"job_id": "job-enq002", "parent_job_id": "loop-parent-9",
			"reason": "audit the escalation lane", "continuation_depth": 0,
		}, reply(`"action": "narrow", "decision_class": "mechanical",
			"confidence": 9, "revised_goal": "port only the close branch",
			"reasoning": "too broad", "summary_for_user": "narrowing"`)},

		// The two fields the enqueue passes through RAW. `reason` is only
		// ever read into an f-string before this point and `job_id` only
		// into log lines, so neither is spelled anywhere upstream — and the
		// row this writes is the continuation's whole inheritance. A port
		// that str()'d either wrote `"{'ask': 'audit the escalation lane'}"`
		// where CPython wrote an object, and `"4242"` where it wrote 4242;
		// every fixture above used strings for both, so both survived
		// (adversarial r11 round 2, HIGH 1).
		{"a reason and a job id that are not strings", map[string]any{
			"job_id": 4242, "parent_job_id": "loop-parent-9",
			"reason": map[string]any{
				"ask": "audit the escalation lane", "budget": 3,
			},
			"continuation_depth": 4,
			"origin": map[string]any{
				"parent_goal": "the original ask", "parent_handle_id": "h-1",
				"next_checkin_depth": 5, "checkins_sent": 1,
			},
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "bounded", "summary_for_user": "next pass"`)},
	} {
		t.Run(c.name, func(t *testing.T) {
			const frozenJitter = 6
			goWS, pyWS := t.TempDir(), t.TempDir()
			prev := checkinRandInt
			checkinRandInt = func(lo, hi int) int { return frozenJitter }
			defer func() { checkinRandInt = prev }()

			mustEscalate(t, context.Background(), goWS, objOf(c.task),
				EscalationOptions{Adapter: &llm.Fake{Script: []string{c.reply}}})
			arg, err := json.Marshal([]any{c.task, c.reply, frozenJitter})
			if err != nil {
				t.Fatal(err)
			}
			runPyIn(t, pyWS, pyEscalationSrc, string(arg))

			gotTask := soleTask(t, goWS)
			wantTask := soleTask(t, pyWS)
			sameRow(t, "the enqueued task", gotTask, wantTask,
				"job_id", "run_id", "timestamps")
		})
	}
}

// soleTask reads the one queue entry the escalation wrote.
func soleTask(t *testing.T, ws string) pyval.Obj {
	t.Helper()
	matches, err := filepath.Glob(filepath.Join(ws, "output", "queues", "tasks", "task-*.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 1 {
		t.Fatalf("expected one queue entry under %s, found %d", ws, len(matches))
	}
	raw, err := os.ReadFile(matches[0])
	if err != nil {
		t.Fatal(err)
	}
	v, err := pyval.LoadsOrdered(string(raw))
	if err != nil {
		t.Fatal(err)
	}
	o, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("queue entry is not an object: %s", raw)
	}
	return o
}

// --- the judged close, where a run and an outcome row actually exist ----

// Every case above ran against a workspace with no run dir and no outcome
// ledger, so `close` resolved nothing and stamped nothing. That is a real
// path — a chain whose run was pruned — but it is not the path the close
// branch exists for, and a port that never called the stamp at all would
// have passed all eighteen.
//
// This one seeds the run and the ledger row, copies the seeded workspace
// so both runtimes start from the same bytes, and then compares
// metadata.json byte for byte and the outcome row field for field.
func TestACloseStampsBothStoresAndASurfaceStampsNeither(t *testing.T) {
	for _, c := range []struct {
		name        string
		action      string
		wantStamped bool
	}{
		{"close stamps", "close", true},
		{"surface does not", "surface", false},
	} {
		t.Run(c.name, func(t *testing.T) {
			const (
				frozenJitter = 5
				loopID       = "loop-20260824T090000-stamped"
				handleID     = "20260824T090000-stamped1"
			)
			goWS, pyWS := t.TempDir(), t.TempDir()

			// A run that ended on its own iteration cap, which is what an
			// escalated run usually carries — so the close is a REFINEMENT
			// over a prior verdict, not a first stamp.
			rd, err := runs.Create(goWS, handleID, "audit the escalation lane")
			if err != nil {
				t.Fatal(err)
			}
			if err := runs.WriteMetadata(rd, pyval.Obj{
				{Key: "loop_id", Val: loopID},
				{Key: "status", Val: "stuck"},
				{Key: "stop_verdict", Val: "out-of-budget"},
				{Key: "stop_evidence", Val: "iteration cap reached at 7"},
			}); err != nil {
				t.Fatal(err)
			}
			seedLedger(t, goWS, []string{
				`{"ts": "2026-08-20T10:00:00Z", "loop_id": "` + loopID +
					`", "status": "stuck", "goal": "audit the escalation lane"}`,
			})
			copyTree(t, goWS, pyWS)

			task := map[string]any{
				"job_id": "job-stamp01", "parent_job_id": loopID,
				"reason": "audit the escalation lane", "continuation_depth": 3,
			}
			body := reply(`"action": "` + c.action + `", "decision_class": "mechanical",
				"confidence": 9, "reasoning": "the completed work answers the core question",
				"summary_for_user": "done enough"`)

			prev := checkinRandInt
			checkinRandInt = func(lo, hi int) int { return frozenJitter }
			defer func() { checkinRandInt = prev }()

			mustEscalate(t, context.Background(), goWS, objOf(task),
				EscalationOptions{Adapter: &llm.Fake{Script: []string{body}}})
			arg, err := json.Marshal([]any{task, body, frozenJitter})
			if err != nil {
				t.Fatal(err)
			}
			runPyIn(t, pyWS, pyEscalationSrc, string(arg))

			gotMeta, err := os.ReadFile(filepath.Join(rd, "metadata.json"))
			if err != nil {
				t.Fatal(err)
			}
			wantMeta, err := os.ReadFile(filepath.Join(pyWS, "runs", filepath.Base(rd), "metadata.json"))
			if err != nil {
				t.Fatal(err)
			}
			if string(gotMeta) != string(wantMeta) {
				t.Errorf("metadata.json is not CPython's:\n--- go ---\n%s\n--- py ---\n%s",
					gotMeta, wantMeta)
			}

			gotRows := readRows(t, filepath.Join(goWS, "memory", "outcomes.jsonl"))
			wantRows := readRows(t, filepath.Join(pyWS, "memory", "outcomes.jsonl"))
			if len(gotRows) != 1 || len(wantRows) != 1 {
				t.Fatalf("outcome rows: go %d, py %d", len(gotRows), len(wantRows))
			}
			sameRow(t, "the outcome row", gotRows[0], wantRows[0])

			// And the direction of the whole thing, stated once so a reader
			// does not have to infer it from two byte comparisons.
			_, stamped := gotRows[0].Get("stop_verdict")
			if stamped != c.wantStamped {
				t.Errorf("the outcome row carries a stop verdict = %v, want %v",
					stamped, c.wantStamped)
			}
			// The metadata comparison above passes if NEITHER side stamped,
			// so say what the file should hold rather than only that the
			// two agree.
			metaSaysClose := strings.Contains(string(gotMeta), `"reachable-but-not-worth-it"`)
			if metaSaysClose != c.wantStamped {
				t.Errorf("metadata.json carries the close verdict = %v, want %v:\n%s",
					metaSaysClose, c.wantStamped, gotMeta)
			}
			if c.wantStamped {
				if v := gotRows[0].GetString("stop_evidence"); !strings.Contains(v, "[refines: out-of-budget]") {
					t.Errorf("the ledger evidence lost the refine note: %q", v)
				}
				if !strings.Contains(string(gotMeta), `"escalation-close"`) {
					t.Errorf("metadata.json lost the reopen payload:\n%s", gotMeta)
				}
			}
		})
	}
}

// seedLedger writes an outcomes store VERBATIM, so the stamp is exercised
// as a mutation of bytes that were already there.
func seedLedger(t *testing.T, ws string, lines []string) {
	t.Helper()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := strings.Join(lines, "\n")
	if len(lines) > 0 {
		body += "\n"
	}
	if err := os.WriteFile(filepath.Join(dir, "outcomes.jsonl"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// TestTheCloseStampIsGovernedByTheRawParentIdAndAnIntDepth drives the four
// shapes where the close branch's two stores STOP agreeing with a port that
// spells its inputs.
//
// The test above seeds a run and a ledger row, and both its fixtures use a
// string parent id and an integer depth — so it pins that the stamp happens
// and nothing about WHEN. Three separate decisions live in that branch and
// none of them were covered:
//
//   - `resolve_run_dir(loop_id)` RAISES for a non-string (it builds
//     `f"{ref}-{nickname(ref)}"` and nickname calls `.encode`), so the whole
//     metadata half — the [refines: …] note included — is skipped.
//   - the ledger match is Python's `==`, and `4242 == "4242"` is False, so a
//     numeric parent id stamps a numeric row and skips a string one.
//   - `int(depth)` in the reopen payload is the ONE place depth is not raw,
//     and it is evaluated as an ARGUMENT: a non-numeric depth raises before
//     anything is written.
//
// Everything is compared cross-runtime rather than against expectations
// written here, because the expectations are the part I would get wrong.
func TestTheCloseStampIsGovernedByTheRawParentIdAndAnIntDepth(t *testing.T) {
	const (
		frozenJitter = 5
		loopID       = "loop-20260824T090000-stamped"
		handleID     = "20260824T090000-stamped1"
	)
	for _, c := range []struct {
		name        string
		parent      any
		depth       any
		seedLoopID  string // the raw JSON literal in the seeded row
		metaLoopID  string // the loop_id the seeded RUN carries
		wantStamped bool
		wantRefine  bool
	}{
		{"a numeric parent matches no string row and resolves no run",
			4242, 3, `"4242"`, loopID, false, false},
		{"a numeric parent DOES match a numeric row, with no metadata half",
			4242, 3, `4242`, loopID, true, false},
		// The two rows above leave the run unreachable under EITHER reading,
		// so a port that spelled the id before resolving found nothing and
		// looked correct. Here the run's own metadata says "4242": the
		// spelling WOULD resolve it, and CPython still does not, because
		// resolve_run_dir(4242) raises inside the try (adversarial r11
		// round 2, HIGH 2 — the half the first two rows could not see).
		{"a numeric parent does not resolve the run its spelling would",
			4242, 3, `4242`, "4242", true, false},
		{"a float depth is int()ed in the payload and not in the evidence",
			loopID, json.Number("2.0"), `"` + loopID + `"`, loopID, true, true},
		{"a string depth aborts the metadata half before it writes",
			loopID, "deep", `"` + loopID + `"`, loopID, true, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			goWS, pyWS := t.TempDir(), t.TempDir()
			rd, err := runs.Create(goWS, handleID, "audit the escalation lane")
			if err != nil {
				t.Fatal(err)
			}
			if err := runs.WriteMetadata(rd, pyval.Obj{
				{Key: "loop_id", Val: c.metaLoopID},
				{Key: "status", Val: "stuck"},
				{Key: "stop_verdict", Val: "out-of-budget"},
				{Key: "stop_evidence", Val: "iteration cap reached at 7"},
			}); err != nil {
				t.Fatal(err)
			}
			seedLedger(t, goWS, []string{
				`{"ts": "2026-08-20T10:00:00Z", "loop_id": ` + c.seedLoopID +
					`, "status": "stuck", "goal": "audit the escalation lane"}`,
			})
			copyTree(t, goWS, pyWS)

			task := map[string]any{
				"job_id": "job-stamp02", "parent_job_id": c.parent,
				"reason": "audit the escalation lane", "continuation_depth": c.depth,
			}
			body := reply(`"action": "close", "decision_class": "mechanical",
				"confidence": 9, "reasoning": "the completed work answers the core question",
				"summary_for_user": "done enough"`)

			prev := checkinRandInt
			checkinRandInt = func(lo, hi int) int { return frozenJitter }
			defer func() { checkinRandInt = prev }()

			mustEscalate(t, context.Background(), goWS, objOf(task),
				EscalationOptions{Adapter: &llm.Fake{Script: []string{body}}})
			arg, err := json.Marshal([]any{task, body, frozenJitter})
			if err != nil {
				t.Fatal(err)
			}
			runPyIn(t, pyWS, pyEscalationSrc, string(arg))

			gotMeta, err := os.ReadFile(filepath.Join(rd, "metadata.json"))
			if err != nil {
				t.Fatal(err)
			}
			wantMeta, err := os.ReadFile(
				filepath.Join(pyWS, "runs", filepath.Base(rd), "metadata.json"))
			if err != nil {
				t.Fatal(err)
			}
			if string(gotMeta) != string(wantMeta) {
				t.Errorf("metadata.json is not CPython's:\n--- go ---\n%s\n--- py ---\n%s",
					gotMeta, wantMeta)
			}

			gotRows := readRows(t, filepath.Join(goWS, "memory", "outcomes.jsonl"))
			wantRows := readRows(t, filepath.Join(pyWS, "memory", "outcomes.jsonl"))
			if len(gotRows) != 1 || len(wantRows) != 1 {
				t.Fatalf("outcome rows: go %d, py %d", len(gotRows), len(wantRows))
			}
			sameRow(t, "the outcome row", gotRows[0], wantRows[0])

			// Said out loud, because two agreeing byte comparisons also
			// agree when NEITHER side did anything, and three of these four
			// cases are about a store deliberately left alone.
			_, stamped := gotRows[0].Get("stop_verdict")
			if stamped != c.wantStamped {
				t.Errorf("the outcome row carries a stop verdict = %v, want %v: %v",
					stamped, c.wantStamped, gotRows[0])
			}
			refined := strings.Contains(gotRows[0].GetString("stop_evidence"),
				"[refines: out-of-budget]")
			if refined != c.wantRefine {
				t.Errorf("the ledger evidence carries the refine note = %v, want %v: %q",
					refined, c.wantRefine, gotRows[0].GetString("stop_evidence"))
			}
			// The refine note only exists when the metadata half ran, so the
			// two must move together — that IS the coupling under test.
			metaClosed := strings.Contains(string(gotMeta), `"escalation-close"`)
			if metaClosed != c.wantRefine {
				t.Errorf("metadata.json carries the reopen payload = %v, want %v:\n%s",
					metaClosed, c.wantRefine, gotMeta)
			}
			if metaClosed && !strings.Contains(string(gotMeta), `"depth": 2,`) {
				t.Errorf("the reopen payload's depth is not the int()ed one:\n%s", gotMeta)
			}
			if metaClosed && !strings.Contains(string(gotMeta), "at depth 2.0 ") {
				t.Errorf("the evidence string lost the raw depth:\n%s", gotMeta)
			}
		})
	}
}

// The log lines are a surface too. They are what an operator reads when a
// run went sideways, and Python spells two of them with %r — the escalation
// decision's reasoning and the narrow's revised goal.
//
// Go's %q is NOT Python's repr: repr prefers single quotes, leaves printable
// non-ASCII literal, and escapes a different set. A line reading
// reasoning='the café path → ok' on one runtime and "the caf\u00e9 path
// \u2192 ok" on the other is the same divergence class as a differing
// artifact, and nothing here compared logs at all.
const pyEscalationLogSrc = `
import json, os, sys, logging
from pathlib import Path

task, reply = json.loads(sys.argv[1])

import director
from llm import LLMResponse

class _Scripted:
    def complete(self, messages, **kwargs):
        return LLMResponse(content=reply)

lines = []

class _Cap(logging.Handler):
    # The try/handleError is NOT decoration: logging.Handler.emit is
    # DEFINED to swallow a formatting error and route it to handleError,
    # and every stock handler (StreamHandler, FileHandler) does exactly
    # that. A capture handler that lets the error escape measures the
    # HARNESS, not director.py — "%d" % "deep" would abort the probe
    # here while the deployed runtime logs nothing and carries on.
    def emit(self, record):
        try:
            lines.append(record.getMessage())
        except Exception:
            self.handleError(record)

_root = logging.getLogger()
_root.addHandler(_Cap())
_root.setLevel(logging.DEBUG)

director._checkin_jitter = lambda: 5
director.handle_escalation(task, adapter=_Scripted())

sys.stdout.write(json.dumps(lines))
`

func TestTheDecisionLogLineMatchesCPython(t *testing.T) {
	for _, c := range []escalationCase{
		// Non-ASCII and a quote, because those are the two places repr and
		// %q part company. A plain ASCII reasoning renders identically
		// under both and would pin nothing.
		{"a reasoning with non-ASCII and a quote", map[string]any{
			"job_id": "job-log001", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9,
			"reasoning": "the caf\u00e9 path \u2192 they said \"fine\"",
			"summary_for_user": "closing"`)},

		{"a revised goal with non-ASCII", map[string]any{
			"job_id": "job-log002", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "narrow", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "one thread left",
			"revised_goal": "chase the caf\u00e9 \u2192 na\u00efve case",
			"summary_for_user": "narrowing"`)},

		// `escalation_start` and `escalation_continue` are spelled with
		// %d, not f-strings — the only two depth sites in this function
		// that are. Every fixture above uses an INTEGER depth, which is
		// the one type where %d and str() agree by construction, so the
		// difference could not appear. A float depth is what any foreign
		// JSON writer produces.
		{"a float depth renders as an integer", map[string]any{
			"job_id": "job-log003", "parent_job_id": "loop-parent-1",
			"reason":             "audit the escalation lane",
			"continuation_depth": json.Number("2.0"),
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closing"`)},

		{"a fractional depth truncates toward zero", map[string]any{
			"job_id": "job-log004", "parent_job_id": "loop-parent-1",
			"reason":             "audit the escalation lane",
			"continuation_depth": json.Number("2.9"),
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closing"`)},

		// %d of a str, a None or a dict raises inside logging's own
		// formatter, which catches it and emits NO RECORD at all.
		{"a prose depth writes no start line", map[string]any{
			"job_id": "job-log005", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": "deep",
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closing"`)},

		{"a null depth writes no start line", map[string]any{
			"job_id": "job-log006", "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": nil,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closing"`)},

		// The CONTINUE branch has two more %d sites the close fixtures
		// never reach: `escalation_continue` and, past the check-in
		// depth, `recursion_checkin fired`. A float depth is carried
		// through `depth + 1` as a float, so both render it — and both
		// are spelled %d, where str() would print "6.0".
		{"a float depth through the continue branch", map[string]any{
			"job_id": "job-log007", "parent_job_id": "loop-parent-1",
			"reason":             "audit the escalation lane",
			"continuation_depth": json.Number("5.0"),
		}, reply(`"action": "continue", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "one more pass",
			"revised_goal": "a smaller slice",
			"summary_for_user": "continuing"`)},

		// The artifact write logs the EXCEPTION its slice raised, and an
		// int and a dict do not raise the same one.
		{"a numeric job_id logs its own slice failure", map[string]any{
			"job_id": json.Number("4242"), "parent_job_id": "loop-parent-1",
			"reason": "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closing"`)},

		{"a mapping job_id logs a KeyError, not a TypeError", map[string]any{
			"job_id":        map[string]any{"id": "x"},
			"parent_job_id": "loop-parent-1",
			"reason":        "audit the escalation lane", "continuation_depth": 1,
		}, reply(`"action": "close", "decision_class": "mechanical",
			"confidence": 9, "reasoning": "done here",
			"summary_for_user": "closing"`)},
	} {
		t.Run(c.name, func(t *testing.T) {
			goWS, pyWS := t.TempDir(), t.TempDir()

			prev := checkinRandInt
			checkinRandInt = func(lo, hi int) int { return 5 }
			defer func() { checkinRandInt = prev }()

			var got []string
			fake := &llm.Fake{Script: []string{c.reply}}
			if _, err := HandleEscalation(context.Background(), goWS,
				objOf(c.task), EscalationOptions{
					Adapter: fake,
					Log: func(format string, args ...any) {
						got = append(got, fmt.Sprintf(format, args...))
					},
				}); err != nil {
				t.Fatal(err)
			}

			arg, err := json.Marshal([]any{c.task, c.reply})
			if err != nil {
				t.Fatal(err)
			}
			var want []string
			if err := json.Unmarshal(
				[]byte(runPyIn(t, pyWS, pyEscalationLogSrc, string(arg))),
				&want); err != nil {
				t.Fatal(err)
			}

			// Two lines carry content no differential can compare: the
			// workspace each runtime was given, and the fresh uuid a
			// continuation gets. Both are masked rather than skipped — the
			// REST of those lines is exactly the rendering under test, and
			// skipping them by name would drop the narrow's %r goal, which
			// is one of the two sites this test exists for.
			norm := func(ws string) func(string) string {
				return func(line string) string {
					line = strings.ReplaceAll(line, ws, "<ws>")
					return taskIDRe.ReplaceAllString(line, "task-<id>")
				}
			}
			goNorm, pyNorm := norm(goWS), norm(pyWS)

			// Only the lines this port emits are compared: Python logs from
			// modules the port has not reached (task_store, runs), and a
			// set comparison would fail on those rather than on the
			// rendering under test. The PREFIX is the join key, because the
			// tail is exactly what differs when the rendering is wrong.
			for _, raw := range got {
				g := goNorm(raw)
				head, _, _ := strings.Cut(g, " ")
				var match string
				for _, w := range want {
					w = pyNorm(w)
					if wh, _, _ := strings.Cut(w, " "); wh == head {
						match = w
						break
					}
				}
				if match == "" {
					t.Errorf("the port logs %q and CPython logs no line "+
						"starting %q:\n  python: %q", g, head, want)
					continue
				}
				if match != g {
					t.Errorf("log line diverges:\n  go: %q\n  py: %q", g, match)
				}
			}
			if len(got) == 0 {
				t.Fatal("the port logged nothing; this test compares nothing")
			}
		})
	}
}

// taskIDRe matches the ids handle_escalation mints, which carry a uuid
// tail. Anchored on the whole shape rather than on "task-" alone, so a
// line that merely mentions a configured id keeps it.
var taskIDRe = regexp.MustCompile(`task-\d{8}T\d{6}Z-[0-9a-f]{8}`)
