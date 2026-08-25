package skills

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyExtractSrc drives skills.extract_skills with a stub adapter that records
// the PROMPT it was handed and replies with a fixture the caller chose.
//
// The prompt is captured because it is half the behaviour: the summary
// slice, the chained `.get` default, the f-string's str() of a raw value and
// the [:20] ordering are all only observable there. A differential that
// compared the returned skills alone would agree while the model was being
// asked a different question.
//
// The stub also pins the CALL: max_tokens, temperature and purpose reach the
// adapter as keyword arguments, and a port that changed one would look
// identical from the outside.
const pyExtractSrc = `
import json, os, sys, uuid
import skills

_argv = json.loads(sys.argv[1])
_seen = {}

class _Resp:
    def __init__(self, content):
        self.content = content

class _Stub:
    def complete(self, messages, **kw):
        _seen["system"] = messages[0].content
        _seen["user"] = messages[1].content
        _seen["kw"] = {k: v for k, v in sorted(kw.items())}
        _seen["n_messages"] = len(messages)
        if _argv.get("raise_in_adapter"):
            raise RuntimeError("adapter down")
        return _Resp(_argv["reply"])

# Deterministic ids and clock: the port takes them as parameters, so the
# comparison is of everything EXCEPT the two values neither runtime can
# make the other produce.
_ids = iter(["id%d" % i for i in range(50)])
uuid.uuid4 = lambda: next(_ids)
skills.datetime = _FrozenDT = type("_FrozenDT", (), {
    "now": staticmethod(lambda tz=None: type("_T", (), {
        "isoformat": staticmethod(lambda: "2026-01-01T00:00:00+00:00")})())})

try:
    out = skills.extract_skills(_argv["outcomes"], _Stub())
    res = {"ok": True,
           "skills": [
               {"id": s.id, "name": s.name, "description": s.description,
                "trigger_patterns": s.trigger_patterns,
                "steps_template": s.steps_template,
                "source_loop_ids": s.source_loop_ids,
                "created_at": s.created_at, "origin": s.origin,
                "domain": s.domain, "tags": s.tags}
               for s in out]}
except BaseException as e:
    res = {"ok": False, "cls": type(e).__name__, "msg": str(e)}

res["seen"] = _seen
# The SAVED rows, because extract_skills' visible effect is a file and a
# port could return the right objects while writing different bytes.
p = skills._skills_path()
res["saved"] = p.read_text(encoding="utf-8") if p.exists() else ""
print(json.dumps(res, sort_keys=True))
`

type extractCase struct {
	name     string
	outcomes []map[string]any
	reply    string
}

// The four code points Python's str.strip() removes and Go's unicode.IsSpace
// does not: FILE, GROUP, RECORD and UNIT SEPARATOR.
//
// Spelled as JSON ESCAPES, and built here rather than written into the
// fixture literals, because a raw control byte is not legal inside a JSON
// string. The first attempt at these fixtures embedded the bytes directly;
// both runtimes then rejected the whole reply as unparseable and agreed
// perfectly about the empty result, testing nothing (lens 1). The escape is
// what a real model reply carries, and it is what exercises the strip.
var (
	fsep = jsonEscape(0x1c)
	gsep = jsonEscape(0x1d)
	rsep = jsonEscape(0x1e)
	usep = jsonEscape(0x1f)
)

func jsonEscape(r rune) string { return fmt.Sprintf(`\u%04x`, r) }

// TestExtractSkillsMatchesCPython pins the mint path against the
// interpreter, prompt included.
//
// The rows are chosen from the FUNCTION: every operator whose Go spelling
// differs from the obvious one, and both sides of the try boundary. The two
// that decide whether this port is worth anything are the summary subscript
// (which raises OUT of the function, because outcomes_text is built before
// the try) and the `.get` chain's default (which applies to an ABSENT
// summary only, so a present null is the value and is what raises).
func TestExtractSkillsMatchesCPython(t *testing.T) {
	goodReply := `{"skills": [{"name": "  Fetch And Summarize  ",
		"description": " reads a page ", "trigger_patterns": ["fetch", " ", "summarize"],
		"steps_template": ["get", "read", " "], "domain": "  WEB-Research  ",
		"tags": ["a", "B", "a"]}]}`

	cases := []extractCase{
		{"a plain success", []map[string]any{
			{"status": "done", "goal": "g1", "task_type": "research",
				"summary": "did the thing", "outcome_id": "abcdefghijkl"},
		}, goodReply},

		// ORDERING. Judged-True first, stable within each group — and
		// `is not True` is identity, so the numeric 1 sorts with the
		// unjudged rows rather than with the judged one.
		{"judged-true sorts first, stably", []map[string]any{
			{"status": "done", "goal": "unjudged-a", "outcome_id": "u1"},
			{"status": "done", "goal": "numeric-one", "goal_achieved": 1,
				"outcome_id": "n1"},
			{"status": "done", "goal": "judged", "goal_achieved": true,
				"outcome_id": "j1"},
			{"status": "done", "goal": "unjudged-b", "outcome_id": "u2"},
		}, goodReply},

		// The chained default. result_summary is consulted only when
		// `summary` is ABSENT.
		{"result_summary is the fallback", []map[string]any{
			{"status": "done", "goal": "g", "result_summary": "from the fallback"},
		}, goodReply},
		{"a present null summary does NOT fall back", []map[string]any{
			{"status": "done", "goal": "g", "summary": nil,
				"result_summary": "never read"},
		}, goodReply},
		{"an empty summary is a value, not an absence", []map[string]any{
			{"status": "done", "goal": "g", "summary": "",
				"result_summary": "never read"},
		}, goodReply},

		// The subscript, on the shapes a foreign writer produces. A list
		// SLICES; a number does not subscript at all; a dict is a key
		// lookup with a slice object. All three land outside the try.
		{"a list summary slices", []map[string]any{
			{"status": "done", "goal": "g", "summary": []any{"a", "b"}},
		}, goodReply},
		{"a numeric summary raises", []map[string]any{
			{"status": "done", "goal": "g", "summary": 42},
		}, goodReply},
		{"a dict summary raises", []map[string]any{
			{"status": "done", "goal": "g", "summary": map[string]any{"a": 1}},
		}, goodReply},
		{"a long summary is cut at three hundred", []map[string]any{
			{"status": "done", "goal": "g",
				"summary": strings.Repeat("x", 400)},
		}, goodReply},
		// len() is code points, so a wide string cuts by runes.
		{"a wide summary cuts by code point", []map[string]any{
			{"status": "done", "goal": "g", "summary": strings.Repeat("é", 400)},
		}, goodReply},

		// The f-string renders the RAW goal and task_type through str().
		{"a non-string goal is str()d", []map[string]any{
			{"status": "done", "goal": 42, "task_type": nil, "summary": "s"},
		}, goodReply},
		{"a list goal renders as a Python list", []map[string]any{
			{"status": "done", "goal": []any{"a", 1}, "summary": "s"},
		}, goodReply},

		// source_ids: truthiness filter, then str()[:8], capped at ten.
		{"a falsy outcome_id is skipped", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s", "outcome_id": 0},
			{"status": "done", "goal": "g", "summary": "s", "outcome_id": ""},
			{"status": "done", "goal": "g", "summary": "s", "outcome_id": "0"},
			{"status": "done", "goal": "g", "summary": "s", "outcome_id": 12345678901},
		}, goodReply},

		// The [:20] cap, with a twenty-FIRST row that must not appear in
		// the prompt. Twenty-two rows and only one judged, so the sort
		// moves it to the front and the tail is what falls off.
		{"more than twenty outcomes", manyOutcomes(22), goodReply},
		// The same cap with the judged row LAST in the input: it must ride
		// to the front and survive, and one unjudged row must fall off.
		{"the twenty cap keeps the judged row", judgedLast(21), goodReply},

		// An ABSENT goal takes the "" default, where a present null renders
		// "None". Nothing else in this table omits the key, so without this
		// row the default could be dropped and every case still agree.
		{"an absent goal is the empty string", []map[string]any{
			{"status": "done", "summary": "s"},
		}, goodReply},

		// Nothing learnable at all.
		{"no learnable outcomes", []map[string]any{
			{"status": "failed"}, {"status": "done", "goal_achieved": false},
		}, goodReply},

		// The reply side, all inside the try and all answering empty.
		{"prose instead of JSON", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}}, "no json here"},
		{"an empty object", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}}, `{}`},
		{"skills present and null", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}}, `{"skills": null}`},
		{"skills absent", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}}, `{"other": 1}`},
		{"skills is an object", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}}, `{"skills": {"a": 1}}`},
		{"skills is a string", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}}, `{"skills": "abc"}`},
		{"a fenced reply is carved", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			"```json\n" + goodReply + "\n```"},

		// Only the first three survive.
		{"more than three skills", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name":"a","steps_template":["s"]},
			 {"name":"b","steps_template":["s"]},
			 {"name":"c","steps_template":["s"]},
			 {"name":"d","steps_template":["s"]}]}`},

		// The name default applies to an ABSENT key. A present null becomes
		// the STRING "None", which is truthy and therefore saved.
		{"an absent name becomes unnamed", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"steps_template": ["s"]}]}`},
		{"a null name becomes the string None", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": null, "steps_template": ["s"]}]}`},
		{"a whitespace name is dropped", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "   ", "steps_template": ["s"]}]}`},

		// THE FOUR CODE POINTS GO DOES NOT CALL SPACE.
		//
		// str.strip() removes 29 code points; Go's unicode.IsSpace knows 25
		// and misses U+001C..U+001F, the FILE/GROUP/RECORD/UNIT separators.
		// Every fixture above strips ASCII spaces, which the two agree on,
		// so strings.TrimSpace passed this whole table while writing a
		// different content_hash for any reply carrying a separator (round
		// 10 HIGH). These rows are why the port calls pyStrip.
		//
		// The first pins the VALUE (and, through `saved`, the hash computed
		// from it). The second pins the DROP: a name that is only separators
		// is empty to Python and the skill vanishes silently, where an
		// unstripped name stays truthy, reaches save_skill, and is refused
		// by validate_skill_row — which returns the empty list and discards
		// the rows already written. One byte, two different stores.
		{"separators strip out of a name", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "` + fsep + `Fetch And Sum` + usep + `",
			  "description": "` + gsep + ` desc ` + rsep + `",
			  "steps_template": ["s"]}]}`},
		{"a name of only separators is dropped", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "` + fsep + gsep + rsep + usep + `",
			  "steps_template": ["s"]}]}`},
		{"separators strip out of a steps template", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n",
			  "steps_template": ["` + fsep + `step one` + usep + `", "` + rsep + `"],
			  "trigger_patterns": ["` + gsep + `trig` + fsep + `"]}]}`},

		// str.lower() applies FULL case mapping: U+0130 becomes TWO runes,
		// "i" + U+0307, where Go's simple mapping gives one. The domain is
		// lowered and then clipped to 40, so the divergence moves the cut as
		// well as changing the string. Measured: "İSTANBUL".lower() is nine
		// characters in CPython and eight under strings.ToLower.
		{"a dotted capital I in the domain lowercases to two runes",
			[]map[string]any{
				{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": ["s"],
			  "domain": "` + fsep + `İSTANBUL` + usep + `"}]}`},
		{"an empty template is dropped", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": []}]}`},
		{"a whitespace-only template is dropped", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": ["  ", ""]}]}`},

		// Iterating a STRING yields its characters, and a dict its keys —
		// Python does both without complaint.
		{"a string steps_template iterates as characters", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": "abc"}]}`},
		{"a dict trigger_patterns iterates as keys", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": ["s"],
			  "trigger_patterns": {"k1": 1, "k2": 2}}]}`},
		{"a numeric steps_template is not iterable", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": 7}]}`},
		// A non-mapping ELEMENT abandons the whole loop, so the skill
		// before it is discarded too.
		{"a non-mapping element discards the batch", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name":"kept","steps_template":["s"]}, "nope"]}`},

		// normalize_tags caps at SIX. Eight in, six out, duplicates kept
		// (it lowercases and strips, it does not dedupe).
		{"more than six tags are cut at six", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": ["s"],
			  "tags": ["T1","t2","T3","t4","t5","t6","t7","t8"]}]}`},
		{"blank tags are dropped before the cap", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": ["s"],
			  "tags": ["a","  ","b","","c","d","e","f","g"]}]}`},
		// A string tags value is NOT iterated into characters here — the
		// normalizer is list-only, which is the whole reason it exists.
		{"a string tags value normalizes to nothing", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": ["s"], "tags": "abc"}]}`},

		// strip THEN lower THEN [:40] — a 40-char cut applied first would
		// keep the trailing spaces the strip removes.
		{"the domain is stripped, lowered, then cut", []map[string]any{
			{"status": "done", "goal": "g", "summary": "s"}},
			`{"skills": [{"name": "n", "steps_template": ["s"],
			  "domain": "   ` + strings.Repeat("D", 45) + `   "}]}`},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			pyWS := t.TempDir()
			goWS := t.TempDir()

			arg := map[string]any{"outcomes": c.outcomes, "reply": c.reply}
			var want struct {
				OK     bool              `json:"ok"`
				Cls    string            `json:"cls"`
				Msg    string            `json:"msg"`
				Skills []json.RawMessage `json:"skills"`
				Saved  string            `json:"saved"`
				Seen   struct {
					System    string         `json:"system"`
					User      string         `json:"user"`
					KW        map[string]any `json:"kw"`
					NMessages int            `json:"n_messages"`
				} `json:"seen"`
			}
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyExtractSrc, &want, pyprobe.Arg(t, arg))

			ids := 0
			fake := &llm.Fake{Script: []string{c.reply}}
			got, gotErr := ExtractSkills(context.Background(), goWS,
				objsOf(t, c.outcomes), fake, MintOptions{
					NewID: func() string { ids++; return fmt.Sprintf("id%d", ids-1) },
					Now:   func() string { return "2026-01-01T00:00:00+00:00" },
				})

			if want.OK != (gotErr == nil) {
				if gotErr != nil {
					t.Fatalf("raised %v; CPython answered %d skills",
						gotErr, len(want.Skills))
				}
				t.Fatalf("answered %d skills; CPython raises %s: %s",
					len(got), want.Cls, want.Msg)
			}
			if !want.OK {
				if cls := pyval.ClassOf(gotErr); cls != want.Cls {
					t.Errorf("raises %s, CPython raises %s", cls, want.Cls)
				}
				if msg := gotErr.Error(); msg != want.Msg {
					t.Errorf("message\n go: %q\n py: %q", msg, want.Msg)
				}
				return
			}

			// The PROMPT, which is half of what this function does.
			if want.Seen.NMessages != 0 {
				var lastMsgs []llm.Message
				var lastOpts llm.Options
				if len(fake.Msgs) > 0 {
					lastMsgs = fake.Msgs[len(fake.Msgs)-1]
					lastOpts = fake.Opts[len(fake.Opts)-1]
				}
				if lastMsgs == nil {
					t.Fatal("the Go adapter was never called; CPython's was")
				}
				if len(lastMsgs) != want.Seen.NMessages {
					t.Errorf("sent %d messages, CPython sent %d",
						len(lastMsgs), want.Seen.NMessages)
				}
				if lastMsgs[0].Content != want.Seen.System {
					t.Errorf("system prompt\n go: %q\n py: %q",
						lastMsgs[0].Content, want.Seen.System)
				}
				if lastMsgs[1].Content != want.Seen.User {
					t.Errorf("user prompt\n go: %q\n py: %q",
						lastMsgs[1].Content, want.Seen.User)
				}
				if lastOpts.MaxTokens != intOf(want.Seen.KW["max_tokens"]) ||
					lastOpts.Temperature != floatOf(want.Seen.KW["temperature"]) ||
					lastOpts.Purpose != strOf(want.Seen.KW["purpose"]) {
					t.Errorf("call options\n go: %+v\n py: %v",
						lastOpts, want.Seen.KW)
				}
			}

			// The SAVED BYTES. extract_skills' visible effect is a file
			// that the OTHER runtime reads, so returning the right objects
			// while writing different rows is the failure that matters
			// most here — "lessons are data" is the whole premise. The
			// probe captured this from the first run; comparing it is what
			// makes the capture worth anything.
			saved, rerr := os.ReadFile(skillsPath(goWS))
			if rerr != nil && !os.IsNotExist(rerr) {
				t.Fatal(rerr)
			}
			if string(saved) != want.Saved {
				t.Errorf("skills.jsonl\n go: %q\n py: %q", saved, want.Saved)
			}

			if len(got) != len(want.Skills) {
				t.Fatalf("returned %d skills, CPython returned %d",
					len(got), len(want.Skills))
			}
			for i := range got {
				gotRow, err := json.Marshal(map[string]any{
					"id": got[i].ID, "name": got[i].Name,
					"description":      got[i].Description,
					"trigger_patterns": got[i].TriggerPatterns,
					"steps_template":   got[i].StepsTemplate,
					"source_loop_ids":  got[i].SourceLoopIDs,
					"created_at":       got[i].CreatedAt, "origin": got[i].Origin,
					"domain": got[i].Domain, "tags": got[i].Tags,
				})
				if err != nil {
					t.Fatal(err)
				}
				if !sameJSON(t, gotRow, want.Skills[i]) {
					t.Errorf("skill %d\n go: %s\n py: %s", i, gotRow,
						want.Skills[i])
				}
			}
		})
	}
}

// sameJSON compares two encodings structurally: both sides marshal through
// different encoders and neither key order nor int-vs-float spelling is part
// of the contract being tested here (the SAVED bytes are pinned separately).
func sameJSON(t *testing.T, a, b []byte) bool {
	t.Helper()
	var x, y any
	if err := json.Unmarshal(a, &x); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &y); err != nil {
		t.Fatal(err)
	}
	ax, _ := json.Marshal(x)
	by, _ := json.Marshal(y)
	return string(ax) == string(by)
}

func objsOf(t *testing.T, ms []map[string]any) []pyval.Obj {
	t.Helper()
	order := []string{"status", "goal", "task_type", "summary",
		"result_summary", "outcome_id", "goal_achieved", "success_class",
		"stop_verdict", "lesson_extraction_status"}
	out := make([]pyval.Obj, 0, len(ms))
	for _, m := range ms {
		o := pyval.Obj{}
		for _, k := range order {
			if v, ok := m[k]; ok {
				o = append(o, pyval.Field{Key: k, Val: pyval.FromPlain(v)})
			}
		}
		if len(o) != len(m) {
			t.Fatalf("objsOf dropped a key from %v", m)
		}
		out = append(out, o)
	}
	return out
}

func intOf(v any) int {
	f, _ := v.(float64)
	return int(f)
}
func floatOf(v any) float64 { f, _ := v.(float64); return f }
func strOf(v any) string    { s, _ := v.(string); return s }

// manyOutcomes builds n learnable rows, each identifiable in the prompt, with
// exactly one judged so the stable sort has something to move.
func manyOutcomes(n int) []map[string]any {
	out := make([]map[string]any, 0, n)
	for i := 0; i < n; i++ {
		row := map[string]any{"status": "done",
			"goal":    fmt.Sprintf("goal-%02d", i),
			"summary": fmt.Sprintf("summary-%02d", i)}
		if i == 5 {
			row["goal_achieved"] = true
		}
		out = append(out, row)
	}
	return out
}

// judgedLast puts the one judged row at the END of the input, so a port that
// dropped the sort would cut it off with the [:20] instead of keeping it.
func judgedLast(n int) []map[string]any {
	out := manyOutcomes(n)
	for _, r := range out {
		delete(r, "goal_achieved")
	}
	out[len(out)-1]["goal_achieved"] = true
	return out
}
