package skills

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// pyRewriteSrc drives skill_lifecycle.rewrite_skill with a stub adapter that
// records the prompt it was handed and replies with a fixture.
//
// The prompt is captured because it is most of what this function DOES: the
// peer ranking, the [:100] description slice, the "; " step preview, the
// numbering and the failure-note bullets are all only observable there, and a
// differential over the returned skill alone would agree while the model was
// being asked a different question.
//
// The SAVED store comes back too. rewrite_skill's in-place lane writes
// whether or not the caller uses the return value, so the bytes on disk are a
// separate claim from the object.
const pyRewriteSrc = `
import json, sys, uuid
import skills, skill_lifecycle
from skill_types import dict_to_skill

_argv = json.loads(sys.argv[1])
_seen = {"calls": []}

class _Resp:
    def __init__(self, content): self.content = content

class _Stub:
    def complete(self, messages, **kw):
        _seen["calls"].append({
            "roles": [m.role for m in messages],
            "contents": [m.content for m in messages],
            "kw": {k: v for k, v in sorted(kw.items())},
        })
        if _argv.get("raise_in_adapter"):
            raise RuntimeError("adapter down")
        return _Resp(_argv["reply"])

# str(uuid.uuid4())[:8] — patched so the challenger id is comparable.
_ids = iter(["ch%d" % i for i in range(50)])
uuid.uuid4 = lambda: next(_ids)

_adapter = None if _argv.get("no_adapter") else _Stub()
_skill = dict_to_skill(_argv["skill"])
try:
    out = skill_lifecycle.rewrite_skill(_skill, _adapter, verbose=False,
                                        in_place=_argv["in_place"])
    res = {"ok": True, "none": out is None}
    if out is not None:
        res["skill"] = {
            "id": out.id, "name": out.name, "description": out.description,
            "steps_template": out.steps_template,
            "trigger_patterns": out.trigger_patterns,
            "consecutive_failures": out.consecutive_failures,
            "consecutive_successes": out.consecutive_successes,
            "circuit_state": out.circuit_state,
            "failure_notes": out.failure_notes,
            "variant_of": out.variant_of,
            "content_hash": out.content_hash,
        }
except BaseException as e:
    res = {"ok": False, "cls": type(e).__name__, "msg": str(e)}
res["seen"] = _seen
p = skills._skills_path()
res["saved"] = p.read_text(encoding="utf-8") if p.exists() else ""
print(json.dumps(res, sort_keys=True))
`

type rewriteWant struct {
	OK    bool   `json:"ok"`
	None  bool   `json:"none"`
	Cls   string `json:"cls"`
	Msg   string `json:"msg"`
	Saved string `json:"saved"`
	Skill struct {
		ID                   string   `json:"id"`
		Name                 string   `json:"name"`
		Description          string   `json:"description"`
		StepsTemplate        []string `json:"steps_template"`
		TriggerPatterns      []string `json:"trigger_patterns"`
		ConsecutiveFailures  int      `json:"consecutive_failures"`
		ConsecutiveSuccesses int      `json:"consecutive_successes"`
		CircuitState         string   `json:"circuit_state"`
		FailureNotes         []string `json:"failure_notes"`
		VariantOf            *string  `json:"variant_of"`
		ContentHash          string   `json:"content_hash"`
	} `json:"skill"`
	Seen struct {
		Calls []struct {
			Roles    []string       `json:"roles"`
			Contents []string       `json:"contents"`
			KW       map[string]any `json:"kw"`
		} `json:"calls"`
	} `json:"seen"`
}

// raisingAdapter records the call the way llm.Fake does and then FAILS —
// the stub's `raise RuntimeError` in the probes. llm.Fake cannot stand in:
// its empty-script error carries a different message and fires only once,
// and a differential over an error path has to be able to make the error
// happen on the call it means.
type raisingAdapter struct{ *llm.Fake }

func (r raisingAdapter) Complete(ctx context.Context, msgs []llm.Message,
	opts llm.Options) (*llm.Response, error) {
	_, _ = r.Fake.Complete(ctx, msgs, opts)
	return nil, fmt.Errorf("adapter down")
}

// sentinelAdapter is raisingAdapter's SCRIPTED sibling: it fails on the
// calls whose scripted reply is the sentinel and answers normally on the
// rest. raisingAdapter cannot serve here because the sweep makes several
// calls and only one of them is meant to fail — an adapter that dies on
// every call measures the first failure and nothing after it.
const raiseSentinel = "__RAISE__"

type sentinelAdapter struct{ *llm.Fake }

func (s *sentinelAdapter) Complete(ctx context.Context, msgs []llm.Message,
	opts llm.Options) (*llm.Response, error) {
	resp, err := s.Fake.Complete(ctx, msgs, opts)
	if err != nil {
		return nil, err
	}
	if resp.Content == raiseSentinel {
		return nil, fmt.Errorf("adapter down")
	}
	return resp, nil
}

// rewriteRow is the target skill, as a stored row both runtimes load.
func rewriteRow(over map[string]any) map[string]any {
	row := map[string]any{
		"id": "s1", "name": "fetch_and_read", "description": "reads a page",
		"steps_template":       []any{"open the url", "read it"},
		"trigger_patterns":     []any{"fetch", "read"},
		"failure_notes":        []any{"timed out", "404", "rate limited"},
		"consecutive_failures": 4, "circuit_state": "open",
		"utility_score": 0.2, "use_count": 9,
		"created_at": "2026-01-01T00:00:00+00:00",
	}
	for k, v := range over {
		row[k] = v
	}
	return row
}

// peerRow is a healthy pool member — a rewrite prompt's ranked context is
// built from these, so their exact scores and text reach the model.
func peerRow(id, name, desc string, util float64, steps []any) map[string]any {
	return map[string]any{
		"id": id, "name": name, "description": desc,
		"steps_template": steps, "trigger_patterns": []any{"t"},
		"utility_score": util, "circuit_state": "closed", "use_count": 7,
		"created_at": "2026-01-02T00:00:00+00:00",
	}
}

// TestRewriteSkillMatchesCPython pins the rewrite path, prompt included.
//
// The reply fixtures are chosen from the FUNCTION rather than from the diff:
// every branch of its hand-rolled fence strip, both sides of each sanity
// gate, the `.get` defaults that apply to an absent key only, and the two
// shapes that RAISE out of it — because the field reads sit outside its try
// and a raise is a different event for the caller than a None.
func TestRewriteSkillMatchesCPython(t *testing.T) {
	good := `{"description": "  fetches and reads a page  ",
	          "steps_template": ["  open  ", "read", "   "],
	          "trigger_patterns": [" fetch ", "read"]}`

	cases := []struct {
		name string
		row  map[string]any
		// poolRow, when set, is what the STORE holds for this id — which is
		// not always the row the caller passes. The in-place lane reloads
		// and rewrites the stored row, so the two can legitimately differ,
		// and a fixture that seeds the argument as the pool row cannot see
		// which one the code read.
		poolRow map[string]any
		pool    []map[string]any
		reply   string
		inPlace bool
		noAdapt bool
		raise   bool
	}{
		{name: "in place, a clean reply", row: rewriteRow(nil), reply: good, inPlace: true},
		{name: "frontier, a clean reply", row: rewriteRow(nil), reply: good},

		// No adapter is None in both lanes, before anything else happens.
		{name: "no adapter, in place", row: rewriteRow(nil), reply: good,
			inPlace: true, noAdapt: true},
		{name: "no adapter, frontier", row: rewriteRow(nil), reply: good,
			noAdapt: true},

		// The adapter raising is inside the try: None, no write.
		{name: "the adapter raises", row: rewriteRow(nil), reply: good,
			inPlace: true, raise: true},

		// THE PROMPT. Peers are ranked by compactness-adjusted score, not by
		// utility, so a verbose 0.95 must lose to a terse 0.90 — and only
		// the top TWO appear. A skill at exactly 0.5 utility is excluded
		// (the filter is `> 0.5`) and one with an open circuit is too.
		{name: "peers rank by compactness and cut at two",
			row: rewriteRow(nil), reply: good, inPlace: true,
			pool: []map[string]any{
				peerRow("p1", "verbose_peer", strings.Repeat("v", 300), 0.95,
					[]any{strings.Repeat("s", 200)}),
				peerRow("p2", "terse_peer", "short", 0.90, []any{"a", "b"}),
				peerRow("p3", "mid_peer", strings.Repeat("m", 120), 0.92,
					[]any{"m1"}),
				peerRow("p4", "excluded_at_half", "x", 0.5, []any{"x"}),
				func() map[string]any {
					r := peerRow("p5", "open_circuit", "x", 0.99, []any{"x"})
					r["circuit_state"] = "open"
					return r
				}(),
			}},
		// A peer's description is cut at 100 CODE POINTS and only its first
		// three steps preview. Wide characters make the byte count differ.
		{name: "a wide peer description cuts by code point",
			row: rewriteRow(nil), reply: good, inPlace: true,
			pool: []map[string]any{
				peerRow("p1", "wide", strings.Repeat("é", 150), 0.9,
					[]any{"s1", "s2", "s3", "s4"}),
			}},
		// THE FILTER'S OWN BOUNDARY. The case above carries a 0.5 peer, but
		// three better peers rank above it and only two are shown, so it is
		// excluded whichever way the comparison reads — `>` and `>=` give
		// the same prompt. A boundary fixture that another row can mask is
		// not a boundary fixture. These two make the 0.5 peer the ONLY
		// candidate, so the operator is the whole difference between a
		// prompt with a peer block and a prompt without one.
		{name: "a peer at exactly half is excluded",
			row: rewriteRow(nil), reply: good, inPlace: true,
			pool: []map[string]any{peerRow("p1", "at_half", "d", 0.5, []any{"s"})}},
		{name: "a peer just above half is included",
			row: rewriteRow(nil), reply: good, inPlace: true,
			pool: []map[string]any{peerRow("p1", "over_half", "d", 0.51, []any{"s"})}},
		// Ties keep pool order: sorted(reverse=True) is stable.
		{name: "equal-scoring peers keep pool order",
			row: rewriteRow(nil), reply: good, inPlace: true,
			pool: []map[string]any{
				peerRow("pB", "bee", "same", 0.9, []any{"s"}),
				peerRow("pA", "ay", "same", 0.9, []any{"s"}),
				peerRow("pC", "see", "same", 0.9, []any{"s"}),
			}},
		// No failure notes at all takes the parenthetical fallback.
		{name: "no failure notes",
			row:   rewriteRow(map[string]any{"failure_notes": []any{}}),
			reply: good, inPlace: true},
		// No steps template renders an empty numbered block.
		{name: "no steps template",
			row:   rewriteRow(map[string]any{"steps_template": []any{}}),
			reply: good, inPlace: true},

		// THE FENCE STRIP, which is rewrite_skill's own and not llm_parse's.
		{name: "a fenced reply", row: rewriteRow(nil), inPlace: true,
			reply: "```json\n" + good + "\n```"},
		{name: "a one-line fenced reply loses everything",
			row: rewriteRow(nil), inPlace: true, reply: "```json " + good + "```"},
		{name: "leading prose is fatal here", row: rewriteRow(nil), inPlace: true,
			reply: "Here you go:\n" + good},
		{name: "an unclosed fence", row: rewriteRow(nil), inPlace: true,
			reply: "```json\n" + good},
		{name: "a backtick run inside the json cuts at the LAST one",
			row: rewriteRow(nil), inPlace: true,
			reply: "```json\n" + `{"description": "uses ` + "```" + ` fences",
			 "steps_template": ["a"], "trigger_patterns": ["t"]}` + "\n```"},
		{name: "prose after the closing fence", row: rewriteRow(nil), inPlace: true,
			reply: "```json\n" + good + "\n```\nhope that helps"},

		// str.strip() before the fence test removes the separators Go's
		// TrimSpace leaves, which decides whether the reply LOOKS fenced —
		// and therefore whether the first line is thrown away. Written as an
		// ESCAPE: a raw U+001F is invisible in a diff, an editor may eat it,
		// and one already landed in this file by accident. The first draft of
		// this row carried the COMMENT and a plain fenced reply, so it agreed
		// under strings.TrimSpace and tested nothing (lens 1, in the row
		// written to catch lens 1).
		{name: "a separator-led fenced reply", row: rewriteRow(nil), inPlace: true,
			reply: "\u001f```json\n" + good + "\n```"},
		// The mirror: a separator at the END decides whether the CLOSING
		// fence is seen, so the strip changes the payload rather than the
		// framing.
		{name: "a separator-trailed fenced reply", row: rewriteRow(nil), inPlace: true,
			reply: "```json\n" + good + "\n```\u001c"},

		// THE `.get` DEFAULTS. Absent takes the skill's current value; a
		// PRESENT NULL does not — str(None) is "None", which is a
		// four-character description that passes every gate.
		{name: "an absent description keeps the current one",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"steps_template": ["a"], "trigger_patterns": ["t"]}`},
		{name: "a null description becomes the string None",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": null, "steps_template": ["a"],
			         "trigger_patterns": ["t"]}`},
		{name: "absent steps keep the current ones",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "trigger_patterns": ["t"]}`},
		{name: "a null steps_template is not iterable",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": null}`},

		// THE SANITY GATE, both sides of each bound.
		{name: "an all-blank steps template discards",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": ["  ", ""],
			         "trigger_patterns": ["t"]}`},
		{name: "a blank description discards",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "   ", "steps_template": ["a"],
			         "trigger_patterns": ["t"]}`},
		{name: "a 400-code-point description is kept",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "` + strings.Repeat("d", 400) + `",
			         "steps_template": ["a"], "trigger_patterns": ["t"]}`},
		{name: "a 401-code-point description discards",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "` + strings.Repeat("d", 401) + `",
			         "steps_template": ["a"], "trigger_patterns": ["t"]}`},
		// len() is code points on this bound too: 300 accented characters
		// are 600 bytes and 300 characters.
		{name: "a 300-accent description is under the bound",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "` + strings.Repeat("é", 300) + `",
			         "steps_template": ["a"], "trigger_patterns": ["t"]}`},
		{name: "ten steps are kept", row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": ` +
				stepsJSON(10) + `, "trigger_patterns": ["t"]}`},
		{name: "eleven steps discard", row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": ` +
				stepsJSON(11) + `, "trigger_patterns": ["t"]}`},
		// Eleven with one blank: the blank drops FIRST, so ten survive and
		// the skill is kept. The filter runs before the count.
		{name: "eleven steps with a blank survive as ten",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": ` +
				stepsJSONBlank(11) + `, "trigger_patterns": ["t"]}`},
		{name: "empty triggers are inherited, not discarded",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": ["a"],
			         "trigger_patterns": []}`},

		// ITERATION. A string yields characters and a mapping its keys —
		// both without complaint, both reaching the store.
		{name: "a string steps_template iterates as characters",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": "abc",
			         "trigger_patterns": ["t"]}`},
		{name: "a mapping trigger_patterns iterates as keys",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": ["a"],
			         "trigger_patterns": {"k1": 1, "k2": 2}}`},
		{name: "a numeric steps_template raises",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": 7}`},

		// A NON-MAPPING reply. `parsed.get` is an AttributeError, and it is
		// outside the try, so it leaves the function.
		{name: "a list reply raises AttributeError",
			row: rewriteRow(nil), inPlace: true, reply: `[1, 2, 3]`},
		{name: "a bare number reply raises AttributeError",
			row: rewriteRow(nil), inPlace: true, reply: `7`},
		{name: "a bare string reply raises AttributeError",
			row: rewriteRow(nil), inPlace: true, reply: `"nope"`},

		// THE SEPARATORS, in the VALUES rather than the framing — they decide
		// both the stored text and the content hash computed from it.
		//
		// Spelled as JSON \u escapes, not as raw bytes: a JSON string may not
		// contain a code point below U+0020, so a fixture carrying the raw
		// byte is REFUSED by both runtimes, which then agree perfectly about
		// the empty result and test nothing. That is exactly what this row
		// did on its first draft — mint_diff_test.go's own comment warns
		// about it four hundred lines away, and the warning did not travel.
		{name: "separators strip out of a step",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "` + fsep + `d` + usep + `",` +
				` "steps_template": ["` + fsep + `step` + usep + `", "` + gsep + `"],` +
				` "trigger_patterns": ["` + rsep + `t` + fsep + `"]}`},
		// A step that is ONLY separators is empty to Python and drops, which
		// is the difference between a nine-step rewrite and a ten-step one —
		// and therefore between passing the >10 gate and failing it.
		{name: "a step of only separators drops",
			row: rewriteRow(nil), inPlace: true,
			reply: `{"description": "d", "steps_template": ["a", "` +
				fsep + gsep + rsep + usep + `"], "trigger_patterns": ["t"]}`},

		// The in-place lane must find the skill in the POOL. A target that
		// is not there is None and no write.
		{name: "a skill absent from the pool is not rewritten",
			row:   rewriteRow(map[string]any{"id": "not-in-pool"}),
			pool:  []map[string]any{peerRow("p1", "peer", "d", 0.9, []any{"s"})},
			reply: good, inPlace: true},

		// failure_notes[-2:] on both lanes, from DIFFERENT sources: the
		// frontier lane slices the caller's copy and the in-place lane
		// slices the FRESH row. This fixture gives the pool row a longer
		// note list than the argument so the two answers differ.
		{name: "the in-place lane keeps the fresh row's last two notes",
			row: rewriteRow(map[string]any{"failure_notes": []any{"arg-only"}}),
			poolRow: rewriteRow(map[string]any{
				"failure_notes": []any{"pool-a", "pool-b", "pool-c"}}),
			reply: good, inPlace: true},
		// The same divergent fixture through the FRONTIER lane, where the
		// answer is the other one: the challenger slices the ARGUMENT's
		// notes and never looks at the store.
		{name: "the frontier lane keeps the argument's last two notes",
			row: rewriteRow(map[string]any{
				"failure_notes": []any{"arg-a", "arg-b", "arg-c"}}),
			poolRow: rewriteRow(map[string]any{"failure_notes": []any{"pool-only"}}),
			reply:   good},
		// A challenger's variant_of is CLEARED, not inherited. Every other
		// fixture leaves the target's variant_of absent, so the clear and
		// the copy produce the same nil and the line could be deleted
		// unnoticed — a guard that cannot fire is not evidence the danger
		// is gone. This row makes the parent a variant itself, which is the
		// chain shape RetireLosingVariants groups on: a challenger that
		// inherited variant_of would be filed under its GRANDparent and the
		// A/B pair would never be seen.
		{name: "a challenger clears an inherited variant_of",
			row:   rewriteRow(map[string]any{"variant_of": "grandparent"}),
			reply: good},
		{name: "the in-place lane keeps an existing variant_of",
			row:   rewriteRow(map[string]any{"variant_of": "grandparent"}),
			reply: good, inPlace: true},
		{name: "one failure note survives the slice",
			row:   rewriteRow(map[string]any{"failure_notes": []any{"only one"}}),
			reply: good},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			// The target row is ALWAYS in the pool unless the case says
			// otherwise — the in-place lane reloads and looks itself up.
			pool := append([]map[string]any{}, c.pool...)
			if c.row["id"] != "not-in-pool" {
				stored := c.poolRow
				if stored == nil {
					stored = c.row
				}
				pool = append(pool, stored)
			}
			seedStore(t, pyWS, pool, nil)
			seedStore(t, goWS, pool, nil)

			arg := map[string]any{
				"skill": c.row, "reply": c.reply, "in_place": c.inPlace,
				"no_adapter": c.noAdapt, "raise_in_adapter": c.raise,
			}
			var want rewriteWant
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyRewriteSrc, &want, pyprobe.Arg(t, arg))

			var adapter llm.Adapter
			fake := &llm.Fake{Script: []string{c.reply}}
			if !c.noAdapt {
				adapter = fake
				if c.raise {
					adapter = raisingAdapter{fake}
				}
			}
			ids := 0
			skill, err := DictToSkill(c.row)
			if err != nil {
				t.Fatalf("the fixture row must construct: %v", err)
			}
			got, _, gotErr := RewriteSkill(context.Background(), goWS, skill, adapter,
				RewriteOptions{InPlace: c.inPlace,
					NewID: func() string { ids++; return fmt.Sprintf("ch%d", ids-1) }})

			if want.OK != (gotErr == nil) {
				if gotErr != nil {
					t.Fatalf("raised %v; CPython returned none=%v", gotErr, want.None)
				}
				t.Fatalf("returned a result; CPython raises %s: %s", want.Cls, want.Msg)
			}
			if !want.OK {
				if cls := pyval.ClassOf(gotErr); cls != want.Cls {
					t.Errorf("raises %s, CPython raises %s", cls, want.Cls)
				}
				return
			}
			if want.None != (got == nil) {
				t.Fatalf("returned nil=%v, CPython returned None=%v", got == nil, want.None)
			}
			if got != nil {
				compareRewritten(t, *got, want)
			}
			// The STORE, which the in-place lane writes whether or not the
			// caller keeps the return value.
			pySaved := want.Saved
			goSaved := ""
			if b, rerr := os.ReadFile(skillsPath(goWS)); rerr == nil {
				goSaved = string(b)
			}
			if goSaved != pySaved {
				t.Errorf("skills.jsonl differs\n go: %q\n py: %q", goSaved, pySaved)
			}
			compareRewriteCalls(t, fake, want, c.noAdapt)
		})
	}
}

func compareRewritten(t *testing.T, got Skill, want rewriteWant) {
	t.Helper()
	if got.ID != want.Skill.ID {
		t.Errorf("id %q, CPython %q", got.ID, want.Skill.ID)
	}
	if got.Description != want.Skill.Description {
		t.Errorf("description\n go: %q\n py: %q", got.Description, want.Skill.Description)
	}
	if !eqStrs(got.StepsTemplate, want.Skill.StepsTemplate) {
		t.Errorf("steps\n go: %#v\n py: %#v", got.StepsTemplate, want.Skill.StepsTemplate)
	}
	if !eqStrs(got.TriggerPatterns, want.Skill.TriggerPatterns) {
		t.Errorf("triggers\n go: %#v\n py: %#v",
			got.TriggerPatterns, want.Skill.TriggerPatterns)
	}
	if !eqStrs(got.FailureNotes, want.Skill.FailureNotes) {
		t.Errorf("failure_notes\n go: %#v\n py: %#v",
			got.FailureNotes, want.Skill.FailureNotes)
	}
	if got.CircuitState != want.Skill.CircuitState {
		t.Errorf("circuit_state %q, CPython %q", got.CircuitState, want.Skill.CircuitState)
	}
	if got.ConsecutiveFailures != want.Skill.ConsecutiveFailures ||
		got.ConsecutiveSuccesses != want.Skill.ConsecutiveSuccesses {
		t.Errorf("counters (%d,%d), CPython (%d,%d)",
			got.ConsecutiveFailures, got.ConsecutiveSuccesses,
			want.Skill.ConsecutiveFailures, want.Skill.ConsecutiveSuccesses)
	}
	if got.ContentHash != want.Skill.ContentHash {
		t.Errorf("content_hash %q, CPython %q", got.ContentHash, want.Skill.ContentHash)
	}
	gotVar, wantVar := "<nil>", "<nil>"
	if got.VariantOf != nil {
		gotVar = *got.VariantOf
	}
	if want.Skill.VariantOf != nil {
		wantVar = *want.Skill.VariantOf
	}
	if gotVar != wantVar {
		t.Errorf("variant_of %s, CPython %s", gotVar, wantVar)
	}
}

// compareRewriteCalls pins the CALL, not just the answer: how many times the
// adapter was reached, with which roles, which prompt text and which keyword
// arguments. A port that asked a different question, or spent a different
// number of calls, would otherwise look identical from the outside.
func compareRewriteCalls(t *testing.T, fake *llm.Fake, want rewriteWant, noAdapt bool) {
	t.Helper()
	if noAdapt {
		if len(want.Seen.Calls) != 0 {
			t.Fatalf("CPython called a None adapter %d times", len(want.Seen.Calls))
		}
		return
	}
	if len(fake.Msgs) != len(want.Seen.Calls) {
		t.Fatalf("made %d adapter calls, CPython made %d",
			len(fake.Msgs), len(want.Seen.Calls))
	}
	for i, call := range want.Seen.Calls {
		if len(fake.Msgs[i]) != len(call.Roles) {
			t.Errorf("call %d: %d messages, CPython %d",
				i, len(fake.Msgs[i]), len(call.Roles))
			continue
		}
		for j := range call.Roles {
			if fake.Msgs[i][j].Role != call.Roles[j] {
				t.Errorf("call %d msg %d: role %q, CPython %q",
					i, j, fake.Msgs[i][j].Role, call.Roles[j])
			}
			if fake.Msgs[i][j].Content != call.Contents[j] {
				t.Errorf("call %d msg %d: prompt differs\n go: %q\n py: %q",
					i, j, fake.Msgs[i][j].Content, call.Contents[j])
			}
		}
		if mt, ok := call.KW["max_tokens"]; ok {
			if int(mt.(float64)) != fake.Opts[i].MaxTokens {
				t.Errorf("call %d: max_tokens %d, CPython %v",
					i, fake.Opts[i].MaxTokens, mt)
			}
		}
		if p, ok := call.KW["purpose"]; ok && p != fake.Opts[i].Purpose {
			t.Errorf("call %d: purpose %q, CPython %q", i, fake.Opts[i].Purpose, p)
		}
		if tp, ok := call.KW["temperature"]; ok {
			if tp.(float64) != fake.Opts[i].Temperature {
				t.Errorf("call %d: temperature %v, CPython %v",
					i, fake.Opts[i].Temperature, tp)
			}
		}
	}
}

func stepsJSON(n int) string {
	parts := make([]string, n)
	for i := range parts {
		parts[i] = fmt.Sprintf(`"step %d"`, i)
	}
	return "[" + strings.Join(parts, ",") + "]"
}

func stepsJSONBlank(n int) string {
	parts := make([]string, n)
	for i := range parts {
		parts[i] = fmt.Sprintf(`"step %d"`, i)
	}
	parts[0] = `"   "`
	return "[" + strings.Join(parts, ",") + "]"
}

func eqStrs(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// pyValidateSrc drives skills.validate_skill_for_promotion.
const pyValidateSrc = `
import json, sys
import skills
from skill_types import dict_to_skill

_argv = json.loads(sys.argv[1])
_seen = {"calls": []}

class _Resp:
    def __init__(self, content): self.content = content

class _Stub:
    def complete(self, messages, **kw):
        _seen["calls"].append({
            "roles": [m.role for m in messages],
            "contents": [m.content for m in messages],
            "kw": {k: v for k, v in sorted(kw.items())},
        })
        if _argv.get("raise_in_adapter"):
            raise RuntimeError("adapter down")
        return _Resp(_argv["reply"])

_adapter = None if _argv.get("no_adapter") else _Stub()
out = skills.validate_skill_for_promotion(dict_to_skill(_argv["skill"]), _adapter)
print(json.dumps({"out": out, "seen": _seen}, sort_keys=True))
`

// TestValidateSkillForPromotionMatchesCPython pins the quality gate.
//
// The row that matters most is a garbled reply. extract_json answers `{}` on
// any parse failure, `{}` is still a dict, and `bool({}.get("valid", False))`
// is False — so an unparseable reply is a FAILED validation that goes to the
// repair loop, NOT the fail-open pass. A port that mapped its parse error to
// fail-open would promote every skill whose validation the model fumbled.
func TestValidateSkillForPromotionMatchesCPython(t *testing.T) {
	cases := []struct {
		name    string
		row     map[string]any
		reply   string
		noAdapt bool
		raise   bool
	}{
		{name: "a clean pass", row: rewriteRow(nil),
			reply: `{"valid": true, "reason": "clear and concrete", "repair_hint": ""}`},
		{name: "a clean fail", row: rewriteRow(nil),
			reply: `{"valid": false, "reason": "vague", "repair_hint": "name the steps"}`},
		// TRUTHINESS, not a bool cast.
		{name: "the string false is truthy", row: rewriteRow(nil),
			reply: `{"valid": "false", "reason": "r"}`},
		{name: "zero is falsy", row: rewriteRow(nil), reply: `{"valid": 0, "reason": "r"}`},
		{name: "an empty list is falsy", row: rewriteRow(nil),
			reply: `{"valid": [], "reason": "r"}`},
		{name: "a non-empty list is truthy", row: rewriteRow(nil),
			reply: `{"valid": [0], "reason": "r"}`},
		// str() of the other two fields — a present null renders "None".
		{name: "a null reason renders None", row: rewriteRow(nil),
			reply: `{"valid": true, "reason": null}`},
		{name: "an absent reason is empty", row: rewriteRow(nil),
			reply: `{"valid": true}`},
		{name: "a numeric repair hint is str()d", row: rewriteRow(nil),
			reply: `{"valid": false, "reason": "r", "repair_hint": 5}`},
		{name: "a float repair hint keeps Python's repr", row: rewriteRow(nil),
			reply: `{"valid": false, "reason": "r", "repair_hint": 1.50}`},
		// THE PARSE-FAILURE ROW: not fail-open.
		{name: "prose is a FAILED validation, not a free pass",
			row: rewriteRow(nil), reply: "sorry, I can't do that"},
		{name: "an empty reply is a failed validation",
			row: rewriteRow(nil), reply: ""},
		{name: "a fenced reply is carved", row: rewriteRow(nil),
			reply: "```json\n{\"valid\": true, \"reason\": \"ok\"}\n```"},
		{name: "prose around the object is carved", row: rewriteRow(nil),
			reply: "Sure!\n{\"valid\": true, \"reason\": \"ok\"}\nhope that helps"},
		{name: "a list reply is not a dict", row: rewriteRow(nil),
			reply: `[{"valid": true}]`},
		// THE FAIL-OPEN ROW: only an exception reaches it.
		{name: "an adapter that raises fails open", row: rewriteRow(nil),
			reply: `{"valid": false}`, raise: true},
		{name: "a None adapter fails open", row: rewriteRow(nil),
			reply: `{"valid": false}`, noAdapt: true},
		// THE PROMPT: five triggers, six steps, "  - " bullets.
		// BOTH SIDES OF EACH CUT. A seven-element fixture alone cannot see
		// the comparison: `len > 5` and `len > 6` both slice a seven to
		// five, so moving the operator changes nothing. Six is the only
		// count where the trigger cut is observable, and seven the only one
		// where the step cut is — one element past the limit, not two.
		{name: "exactly five triggers are not cut",
			row: rewriteRow(map[string]any{
				"trigger_patterns": []any{"t1", "t2", "t3", "t4", "t5"}}),
			reply: `{"valid": true}`},
		{name: "exactly six triggers cut to five",
			row: rewriteRow(map[string]any{
				"trigger_patterns": []any{"t1", "t2", "t3", "t4", "t5", "t6"}}),
			reply: `{"valid": true}`},
		{name: "seven triggers still cut to five",
			row: rewriteRow(map[string]any{
				"trigger_patterns": []any{"t1", "t2", "t3", "t4", "t5", "t6", "t7"}}),
			reply: `{"valid": true}`},
		{name: "exactly six steps are not cut",
			row: rewriteRow(map[string]any{
				"steps_template": []any{"a", "b", "c", "d", "e", "f"}}),
			reply: `{"valid": true}`},
		{name: "exactly seven steps cut to six",
			row: rewriteRow(map[string]any{
				"steps_template": []any{"a", "b", "c", "d", "e", "f", "g"}}),
			reply: `{"valid": true}`},
		{name: "no triggers and no steps", row: rewriteRow(map[string]any{
			"trigger_patterns": []any{}, "steps_template": []any{}}),
			reply: `{"valid": true}`},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var want struct {
				Out struct {
					Valid      bool   `json:"valid"`
					Reason     string `json:"reason"`
					RepairHint string `json:"repair_hint"`
					Judged     bool   `json:"judged"`
				} `json:"out"`
				Seen struct {
					Calls []struct {
						Roles    []string       `json:"roles"`
						Contents []string       `json:"contents"`
						KW       map[string]any `json:"kw"`
					} `json:"calls"`
				} `json:"seen"`
			}
			arg := map[string]any{"skill": c.row, "reply": c.reply,
				"no_adapter": c.noAdapt, "raise_in_adapter": c.raise}
			pyprobe.Probe{Marker: "skills.py", Workspace: t.TempDir()}.RunJSON(
				t, pyValidateSrc, &want, pyprobe.Arg(t, arg))

			var adapter llm.Adapter
			fake := &llm.Fake{Script: []string{c.reply}}
			if !c.noAdapt {
				adapter = fake
				if c.raise {
					adapter = raisingAdapter{fake}
				}
			}
			skill, err := DictToSkill(c.row)
			if err != nil {
				t.Fatalf("the fixture row must construct: %v", err)
			}
			got := ValidateSkillForPromotion(context.Background(), skill, adapter)

			if got.Valid != want.Out.Valid {
				t.Errorf("valid=%v, CPython %v", got.Valid, want.Out.Valid)
			}
			if got.Judged != want.Out.Judged {
				t.Errorf("judged=%v, CPython %v", got.Judged, want.Out.Judged)
			}
			if got.Reason != want.Out.Reason {
				t.Errorf("reason\n go: %q\n py: %q", got.Reason, want.Out.Reason)
			}
			if got.RepairHint != want.Out.RepairHint {
				t.Errorf("repair_hint\n go: %q\n py: %q", got.RepairHint, want.Out.RepairHint)
			}
			if len(fake.Msgs) != len(want.Seen.Calls) {
				t.Fatalf("made %d adapter calls, CPython made %d",
					len(fake.Msgs), len(want.Seen.Calls))
			}
			for i, call := range want.Seen.Calls {
				for j := range call.Roles {
					if fake.Msgs[i][j].Role != call.Roles[j] ||
						fake.Msgs[i][j].Content != call.Contents[j] {
						t.Errorf("call %d msg %d\n go: %q %q\n py: %q %q", i, j,
							fake.Msgs[i][j].Role, fake.Msgs[i][j].Content,
							call.Roles[j], call.Contents[j])
					}
				}
				if mt, ok := call.KW["max_tokens"]; ok &&
					int(mt.(float64)) != fake.Opts[i].MaxTokens {
					t.Errorf("call %d: max_tokens %d, CPython %v",
						i, fake.Opts[i].MaxTokens, mt)
				}
				if tp, ok := call.KW["temperature"]; ok &&
					tp.(float64) != fake.Opts[i].Temperature {
					t.Errorf("call %d: temperature %v, CPython %v",
						i, fake.Opts[i].Temperature, tp)
				}
				if p, ok := call.KW["purpose"]; ok && p != fake.Opts[i].Purpose {
					t.Errorf("call %d: purpose %q, CPython %q",
						i, fake.Opts[i].Purpose, p)
				}
			}
		})
	}
}

// pyHarnessSrc drives skills.maybe_auto_promote_skills WITH an adapter, so
// the validate/repair loop runs.
//
// The adapter replies from a SCRIPT rather than a single fixture: the loop's
// shape is only visible when the second call can answer differently from the
// first, and the number of calls is itself the finding (the last attempt
// spends a rewrite whose result nothing validates).
const pyHarnessSrc = `
import json, sys, uuid
import skills

_argv = json.loads(sys.argv[1])
_seen = {"purposes": [], "contents": []}
_script = list(_argv["replies"])

class _Resp:
    def __init__(self, content): self.content = content

_i = [0]

class _Stub:
    def complete(self, messages, **kw):
        # REPEAT-LAST when the script runs out, because that is what the Go
        # llm.Fake does. A stub that answered "" instead would make the two
        # harnesses diverge on any case whose call count this test is trying
        # to measure — the measurement would be of the stubs, not the code.
        _seen["purposes"].append(kw.get("purpose"))
        # The full text of every call, not just its purpose. Purposes alone
        # cannot see WHICH skill the loop re-validated: dropping the repaired
        # candidate and validating the original again produces the same
        # sequence of purposes and a different second question.
        _seen["contents"].append([m.content for m in messages])
        if not _script:
            return _Resp("")
        j = min(_i[0], len(_script) - 1)
        _i[0] += 1
        reply = _script[j]
        # The sentinel is an ADAPTER FAILURE, not a reply. It is the only way
        # to reach validate_skill_for_promotion's fail-open arm from inside
        # the sweep, and therefore the only way to produce a promotion whose
        # stored validation word is "unjudged" rather than "passed".
        if reply == "__RAISE__":
            raise RuntimeError("adapter down")
        return _Resp(reply)

_ids = iter(["ch%d" % i for i in range(50)])
uuid.uuid4 = lambda: next(_ids)

promoted = skills.maybe_auto_promote_skills(
    _Stub(), _argv["max_repair_attempts"], limit=_argv["limit"])
p = skills._skills_path()
log = []
try:
    lp = skills._skills_path().parent / "captains_log.jsonl"
    if lp.exists():
        for line in lp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                log.append({"event_type": row.get("event_type"),
                            "validation": (row.get("context") or {}).get("validation")})
except Exception as e:
    log = [{"error": str(e)}]
print(json.dumps({"promoted": promoted, "seen": _seen,
                  "saved": p.read_text(encoding="utf-8") if p.exists() else "",
                  "log": log}, sort_keys=True))
`

// TestPromotionHarnessMatchesCPython pins the validate/repair loop inside the
// sweep — the part MaybeAutoPromoteSkills' doc comment called a named gap.
//
// The interesting assertion is the CALL COUNT. On a skill the model never
// validates, CPython spends max_repair_attempts validations and the SAME
// number of rewrites, because the rewrite sits at the bottom of the loop and
// the last one is never checked. A port that stopped after the last
// validation would be cheaper, agree on every promoted id, and be wrong about
// spend on every failing skill in the pool.
func TestPromotionHarnessMatchesCPython(t *testing.T) {
	promotable := func(id string) map[string]any {
		return map[string]any{
			"id": id, "name": "skill_" + id, "description": "does a thing",
			"steps_template": []any{"one", "two"}, "trigger_patterns": []any{"go"},
			"tier": "provisional", "utility_score": 0.9, "use_count": 9,
			"circuit_state": "closed", "created_at": "2026-01-01T00:00:00+00:00",
		}
	}
	okRewrite := `{"description": "repaired", "steps_template": ["a", "b"],
	               "trigger_patterns": ["go"]}`

	cases := []struct {
		name     string
		pool     []map[string]any
		replies  []string
		attempts int
		limit    int
	}{
		{name: "validated on the first attempt",
			pool:     []map[string]any{promotable("s1")},
			replies:  []string{`{"valid": true, "reason": "fine"}`},
			attempts: 3, limit: 10},

		{name: "repaired once, then validated",
			pool: []map[string]any{promotable("s1")},
			replies: []string{`{"valid": false, "reason": "vague"}`, okRewrite,
				`{"valid": true, "reason": "better"}`},
			attempts: 3, limit: 10},

		// NEVER validates: three validations, three rewrites, held.
		{name: "never validates and the last rewrite is still spent",
			pool: []map[string]any{promotable("s1")},
			replies: []string{`{"valid": false, "reason": "no"}`, okRewrite,
				`{"valid": false, "reason": "no"}`, okRewrite,
				`{"valid": false, "reason": "no"}`, okRewrite},
			attempts: 3, limit: 10},

		// A rewrite that returns None stops the loop early.
		{name: "a discarded rewrite stops the loop",
			pool: []map[string]any{promotable("s1")},
			replies: []string{`{"valid": false, "reason": "no"}`,
				`{"description": "d", "steps_template": []}`},
			attempts: 3, limit: 10},

		// A rewrite that RAISES stops it too — a list reply is an
		// AttributeError out of rewrite_skill, caught by the bare except.
		{name: "a raising rewrite stops the loop",
			pool:     []map[string]any{promotable("s1")},
			replies:  []string{`{"valid": false, "reason": "no"}`, `[1,2,3]`},
			attempts: 3, limit: 10},

		// Zero attempts with an adapter present promotes NOTHING and spends
		// nothing: the loop body never runs.
		{name: "zero attempts promotes nothing",
			pool:     []map[string]any{promotable("s1")},
			replies:  []string{`{"valid": true}`},
			attempts: 0, limit: 10},

		// An unparseable validation reply is a FAILED validation, so this
		// skill goes to repair and is eventually held — not promoted with
		// validation "unjudged".
		{name: "an unparseable validation reply repairs rather than passes",
			pool:     []map[string]any{promotable("s1")},
			replies:  []string{"nope", okRewrite, "nope", okRewrite, "nope", okRewrite},
			attempts: 3, limit: 10},

		// Two candidates, the limit at one: the second is never examined and
		// never costs a call.
		{name: "the limit caps candidates before validation",
			pool:     []map[string]any{promotable("s1"), promotable("s2")},
			replies:  []string{`{"valid": true}`, `{"valid": true}`},
			attempts: 3, limit: 1},

		// A validation that RAISES is fail-open, and fail-open is UNJUDGED:
		// the skill promotes on a verdict nobody gave, and the captain's log
		// says so. Without this case the "unjudged" branch never runs, and
		// collapsing it into "passed" is invisible — every other fixture here
		// reaches the same word by the judged path.
		{name: "a raising validation promotes as unjudged",
			pool:     []map[string]any{promotable("s1")},
			replies:  []string{"__RAISE__"},
			attempts: 3, limit: 10},

		// The repaired candidate is what gets re-validated, not the original.
		// The second validation prompt carries "repaired" in its description;
		// if the loop dropped the rewrite's return value the prompt would
		// still say "does a thing" and every other assertion would agree.
		{name: "the repaired candidate is what is re-validated",
			pool: []map[string]any{promotable("s1")},
			replies: []string{`{"valid": false, "reason": "vague"}`,
				`{"description": "a markedly different description",
				  "steps_template": ["alpha", "beta", "gamma"],
				  "trigger_patterns": ["zulu"]}`,
				`{"valid": true, "reason": "better"}`},
			attempts: 3, limit: 10},

		// One held, one promoted — the held one still consumed its share of
		// the limit.
		{name: "a held skill still consumes the limit",
			pool: []map[string]any{promotable("s1"), promotable("s2")},
			replies: []string{`{"valid": false, "reason": "no"}`, okRewrite,
				`{"valid": true}`},
			attempts: 1, limit: 2},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			seedStore(t, pyWS, c.pool, nil)
			seedStore(t, goWS, c.pool, nil)

			var want struct {
				Promoted []string `json:"promoted"`
				Saved    string   `json:"saved"`
				Log      []struct {
					EventType  string `json:"event_type"`
					Validation string `json:"validation"`
				} `json:"log"`
				Seen struct {
					Purposes []string   `json:"purposes"`
					Contents [][]string `json:"contents"`
				} `json:"seen"`
			}
			arg := map[string]any{"replies": c.replies,
				"max_repair_attempts": c.attempts, "limit": c.limit}
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyHarnessSrc, &want, pyprobe.Arg(t, arg))

			fake := &llm.Fake{Script: c.replies}
			rep, err := MaybeAutoPromoteSkillsWithAdapter(context.Background(), goWS,
				c.limit, c.attempts, record.New(goWS), &sentinelAdapter{Fake: fake})
			if err != nil {
				t.Fatal(err)
			}
			if !eqStrs(rep.PromotedIDs, want.Promoted) {
				t.Errorf("promoted\n go: %#v\n py: %#v", rep.PromotedIDs, want.Promoted)
			}
			// The CALL SEQUENCE, by purpose — this is what proves the loop
			// shape rather than only its verdict.
			gotPurposes := make([]string, len(fake.Opts))
			for i, o := range fake.Opts {
				gotPurposes[i] = o.Purpose
			}
			if !eqStrs(gotPurposes, want.Seen.Purposes) {
				t.Errorf("adapter calls\n go: %#v\n py: %#v",
					gotPurposes, want.Seen.Purposes)
			}
			// And the QUESTIONS, not only their labels. The repair loop feeds
			// the rewritten skill back into the next validation, and a port
			// that fed the original back instead would keep every purpose,
			// every count and every promoted id identical.
			if len(fake.Msgs) != len(want.Seen.Contents) {
				t.Fatalf("adapter call count go=%d py=%d",
					len(fake.Msgs), len(want.Seen.Contents))
			}
			for i, msgs := range fake.Msgs {
				var got []string
				for _, m := range msgs {
					got = append(got, m.Content)
				}
				if !eqStrs(got, want.Seen.Contents[i]) {
					t.Errorf("call %d contents\n go: %#v\n py: %#v",
						i, got, want.Seen.Contents[i])
				}
			}
			// The stored `validation` word on each promotion event.
			var wantValidation []string
			for _, row := range want.Log {
				if row.EventType == "SKILL_PROMOTED" {
					wantValidation = append(wantValidation, row.Validation)
				}
			}
			var gotValidation []string
			for _, row := range logRows(t, goWS) {
				if row["event_type"] == "SKILL_PROMOTED" {
					ctx, _ := row["context"].(map[string]any)
					v, _ := ctx["validation"].(string)
					gotValidation = append(gotValidation, v)
				}
			}
			if !eqStrs(gotValidation, wantValidation) {
				t.Errorf("promotion validation words\n go: %#v\n py: %#v",
					gotValidation, wantValidation)
			}
			goSaved := ""
			if b, rerr := os.ReadFile(skillsPath(goWS)); rerr == nil {
				goSaved = string(b)
			}
			if goSaved != want.Saved {
				t.Errorf("skills.jsonl differs\n go: %q\n py: %q", goSaved, want.Saved)
			}
		})
	}
}

// TestCompactnessScoreIsSharedWithTheCull is an anti-duplication pin.
//
// _compactness_adjusted_score has TWO Python callers — the island cull and
// this peer ranking — and the port has one function. If someone adds a second
// copy for the ranking side, one of them will eventually get the code-point
// count wrong, and the two will disagree about which skill is more compact
// while both look right in isolation.
func TestCompactnessScoreIsSharedWithTheCull(t *testing.T) {
	const src = `
import json, sys
import math
d = json.loads(sys.argv[1])
chars = len(d["description"]) + sum(len(s) for s in (d["steps"] or []))
penalty = math.log(1.0 + chars / 200.0)
print(json.dumps({"score": d["utility"] / max(penalty, 1.0)}))
`
	for _, s := range []Skill{
		{Description: "short", StepsTemplate: []string{"a"}, UtilityScore: 0.9},
		{Description: strings.Repeat("é", 400), StepsTemplate: []string{"a"},
			UtilityScore: 0.9},
		{Description: strings.Repeat("d", 400), StepsTemplate: []string{"a"},
			UtilityScore: 0.9},
		{Description: "", StepsTemplate: nil, UtilityScore: 1.0},
	} {
		var want struct {
			Score float64 `json:"score"`
		}
		pyprobe.Probe{Stdlib: true}.RunJSON(t, src, &want, pyprobe.Arg(t,
			map[string]any{"description": s.Description, "steps": s.StepsTemplate,
				"utility": s.UtilityScore}))
		if got := compactnessAdjustedScore(s); got != want.Score {
			t.Errorf("score %v, CPython %v (desc %d runes)",
				got, want.Score, len([]rune(s.Description)))
		}
	}
}
