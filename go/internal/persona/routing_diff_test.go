package persona

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

var (
	nbspR = string(rune(0x00A0))
	vtabR = string(rune(0x000B))
	fsR   = string(rune(0x001C))
)

// The routing corpus. Every non-obvious row names the mechanism it is
// there for, because the whole table looks like prose otherwise.
var routingCorpus = []string{
	"",
	"check my inbox and calendar",
	"analyze the csv dataset with sql",
	"build and implement the api endpoint",
	"research and investigate the literature",
	"review the code quality",
	"simplify and delete dead code",
	"monitor the systemd service health",
	// "health" is in TWO rows (health-researcher at .85, ops at .75) and
	// the earlier row wins on a strict `>`, so this pins the tie rule.
	"health check",
	// A TRUE tie: "medical" is one hit for health-researcher and "contract"
	// one hit for legal-researcher, both rows based at 0.85, so the two
	// scores are bit-identical and only the strict `>` decides. "health
	// check" above is NOT this case — 0.85 vs 0.75 is not a tie, so on its
	// own it leaves the tie rule unmeasured.
	"medical contract",
	// Case: str.lower() runs before every match.
	"SCRAPE the HTML",
	"Scrape the HTML and CRAWL it",
	// Multi-word keywords take the SUBSTRING path, not the \b path.
	"what's on my plate today",
	"follow-up on the to-do list",
	"system 1 thinking",
	// hits > 1, so the 1.05^(n-1)-shaped scaling and the min(1.0, ...)
	// clamp are both exercised.
	"prioritize the roadmap and okr and kpi and vision and planning and alignment",
	"chart the correlation and pivot the dataframe and plot statistics",
	"MARKET trading polymarket odds bet finance investment portfolio price token crypto",
	// THE \b DIVERGENCE. Go's \b is ASCII, so it fires between a non-ASCII
	// letter and an ASCII one; CPython's does not, because ü and 研 are
	// word characters there and there is no boundary at all.
	"fümarket",
	"marketü",
	"研究market",
	"market研究",
	"naïvebuild",
	// The ASCII controls for the same shape: a boundary both engines see,
	// and a substring neither treats as a word.
	"fu market",
	"marketing",
	"submarket",
	// Punctuation boundaries, which both engines take.
	"the market's price",
	"re-market",
	"a market. market! market?",
	// SEPARATOR characters Python's \W includes and Go's does not — these
	// sit either side of the keyword, so they decide whether the boundary
	// exists.
	"x" + nbspR + "market" + nbspR + "y",
	"x" + vtabR + "market" + vtabR + "y",
	"x" + fsR + "market" + fsR + "y",
	// str.lower() skew: U+0130 lowercases to TWO code points in Python and
	// to one in strings.ToLower.
	"İstanbul market",
	"İnbox",
	"MARKETİNG",
	// Non-ASCII that matches nothing, so the default+0.5 floor is reached.
	"ＭＡＲＫＥＴ",
	"café research",
	"研究 research",
	"what does the paper say",
	"consolidate and synthesize the final report",
}

const routingProbeSrc = `
import json, sys
import persona
print(json.dumps([list(persona.persona_for_goal(g))
                  for g in json.loads(sys.argv[1])], ensure_ascii=False))
`

type pyRoute struct {
	Name string
	Conf float64
}

func runRoutingProbe(t *testing.T, corpus []string) []pyRoute {
	t.Helper()
	var raw [][]any
	personaProbe(t).RunJSON(t, routingProbeSrc, &raw, pyprobe.Arg(t, corpus))
	if len(raw) != len(corpus) {
		t.Fatalf("probe returned %d rows for %d goals", len(raw), len(corpus))
	}
	out := make([]pyRoute, len(raw))
	for i, r := range raw {
		out[i] = pyRoute{r[0].(string), r[1].(float64)}
	}
	return out
}

// TestPersonaForGoalMatchesCPython compares the registry-less path, where
// persona_for_goal is pure keyword routing.
//
// The confidence is compared EXACTLY, not to a tolerance: it is rounded to
// three places and written into persona-dispatch-log.jsonl, which the gap
// scanner and any later analysis read from both runtimes.
func TestPersonaForGoalMatchesCPython(t *testing.T) {
	want := runRoutingProbe(t, routingCorpus)

	// CLAIMS about CPython, checked before the port is compared.
	byGoal := map[string]pyRoute{}
	for i, g := range routingCorpus {
		byGoal[g] = want[i]
	}
	if byGoal["fümarket"].Name != DefaultPersona {
		t.Fatalf("CLAIM moved: CPython routed \"fümarket\" to %q. Its \\b is "+
			"supposed to see NO boundary between ü and m, so the row cannot "+
			"separate the two engines any more.", byGoal["fümarket"].Name)
	}
	if byGoal["fu market"].Name != "finance-analyst" {
		t.Fatalf("CLAIM moved: the ASCII control \"fu market\" routed to %q, "+
			"not finance-analyst", byGoal["fu market"].Name)
	}
	if byGoal["health check"].Name != "health-researcher" {
		t.Fatalf("CLAIM moved: \"health check\" routed to %q — the "+
			"first-row-wins tie rule is no longer being measured",
			byGoal["health check"].Name)
	}
	if byGoal["medical contract"].Name != "health-researcher" {
		t.Fatalf("CLAIM moved: the 0.85-vs-0.85 tie routed to %q — the strict "+
			"`>` that keeps the EARLIER row is no longer being measured",
			byGoal["medical contract"].Name)
	}
	if byGoal["medical contract"].Conf != byGoal["health check"].Conf {
		t.Fatalf("CLAIM moved: \"medical contract\" scored %v and \"health "+
			"check\" %v — they must be bit-identical for the tie to be a tie",
			byGoal["medical contract"].Conf, byGoal["health check"].Conf)
	}
	if byGoal[""].Conf != 0.5 {
		t.Fatalf("CLAIM moved: an empty goal scored %v, not the 0.5 floor",
			byGoal[""].Conf)
	}
	nbspGoal := "x" + nbspR + "market" + nbspR + "y"
	if byGoal[nbspGoal].Name != "finance-analyst" {
		t.Fatalf("CLAIM moved: CPython's \\b no longer fires either side of a "+
			"NO-BREAK SPACE (routed to %q)", byGoal[nbspGoal].Name)
	}

	var distinctNames, clamped, floors int
	seen := map[string]bool{}
	for i, goal := range routingCorpus {
		gotName, gotConf := ForGoal(context.Background(), goal, nil, 0.70, false, nil)
		if gotName != want[i].Name || gotConf != want[i].Conf {
			t.Errorf("persona_for_goal(%q)\n  go %q %v\n  py %q %v",
				goal, gotName, gotConf, want[i].Name, want[i].Conf)
		}
		if !seen[want[i].Name] {
			seen[want[i].Name] = true
			distinctNames++
		}
		if want[i].Conf == 1.0 {
			clamped++
		}
		if want[i].Conf == 0.5 {
			floors++
		}
	}
	// Vacuity floors: a corpus that routes everything to one persona, or
	// never reaches the clamp or the 0.5 floor, is not measuring the
	// scoring at all.
	if distinctNames < 6 {
		t.Fatalf("the corpus reaches only %d distinct personas", distinctNames)
	}
	if clamped == 0 {
		t.Fatal("no row reaches the min(1.0, ...) clamp")
	}
	if floors == 0 {
		t.Fatal("no row reaches the max(conf, 0.5) floor")
	}
}

// The registry-validation branch: a keyword winner that is NOT installed
// falls to its named alternative with a 0.9 penalty, or — when the
// alternative is missing too — to the default at a FLAT 0.5, which is a
// replacement and not a penalty.
const routingWithRegistryProbeSrc = `
import json, sys
import persona
from pathlib import Path
d, goals = json.loads(sys.argv[1])
reg = persona.PersonaRegistry(personas_dir=Path(d))
print(json.dumps([list(persona.persona_for_goal(g, reg)) for g in goals],
                 ensure_ascii=False))
`

func TestPersonaForGoalRegistryFallbackMatchesCPython(t *testing.T) {
	goals := []string{
		"market trading",       // finance-analyst -> not installed
		"check my inbox",       // assistant -> "reporter"
		"scrape the html",      // no fallbacks entry -> [DefaultPersona]
		"research the paper",   // already the default: the branch is SKIPPED
		"review the code",      // critic -> no entry -> default
		"analyze the csv data", // data-analyst -> default
	}

	type scenario struct {
		name    string
		present []string
	}
	scenarios := []scenario{
		// The default IS installed, so every fallback chain terminates in
		// it and takes the 0.9 penalty.
		{"default_installed", []string{DefaultPersona, "reporter"}},
		// NOTHING is installed: every chain exhausts and lands on the flat
		// 0.5, which is where the for/else lives.
		{"nothing_installed", []string{"unrelated"}},
		// The winners themselves are installed, so the branch is entered
		// and immediately satisfied.
		{"winners_installed", []string{"finance-analyst", "assistant",
			"scrapling-adaptive-web-recon", "critic", "data-analyst",
			DefaultPersona}},
	}

	for _, sc := range scenarios {
		t.Run(sc.name, func(t *testing.T) {
			dir := t.TempDir()
			for _, n := range sc.present {
				if err := os.WriteFile(filepath.Join(dir, n+".md"),
					[]byte("---\nname: "+n+"\n---\nbody"), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			var raw [][]any
			personaProbe(t).RunJSON(t, routingWithRegistryProbeSrc, &raw,
				pyprobe.Arg(t, []any{dir, goals}))
			reg := NewFromDir(dir)
			for i, g := range goals {
				wantName := raw[i][0].(string)
				wantConf := raw[i][1].(float64)
				gotName, gotConf := ForGoal(context.Background(), g, reg, 0.70, false, nil)
				if gotName != wantName || gotConf != wantConf {
					t.Errorf("%s: persona_for_goal(%q, reg)\n  go %q %v\n  py %q %v",
						sc.name, g, gotName, gotConf, wantName, wantConf)
				}
			}
			// Each scenario has to reach a DIFFERENT arm, or the three of
			// them are one test run three times.
			switch sc.name {
			case "default_installed":
				if raw[0][1].(float64) == 0.5 {
					t.Fatalf("CLAIM moved: with the default installed the "+
						"penalty arm should not reach the 0.5 floor (%v)", raw[0])
				}
				if raw[1][0].(string) != "reporter" {
					t.Fatalf("CLAIM moved: assistant no longer falls back to "+
						"reporter (%v)", raw[1])
				}
			case "nothing_installed":
				if raw[0][1].(float64) != 0.5 || raw[0][0].(string) != DefaultPersona {
					t.Fatalf("CLAIM moved: an exhausted fallback chain no "+
						"longer lands on (default, 0.5) — got %v", raw[0])
				}
			case "winners_installed":
				if raw[0][0].(string) != "finance-analyst" {
					t.Fatalf("CLAIM moved: an INSTALLED winner is being "+
						"replaced (%v)", raw[0])
				}
			}
		})
	}
}

// llmSelectStub is a scripted adapter for the LLM fallback path.
type llmSelectStub struct {
	reply string
	calls int
	// seen is the prompt the REAL code built. Rebuilding the prompt in the
	// test instead leaves every decision llmSelect makes unmeasured --
	// measured: dropping `+ [_DEFAULT_PERSONA]` from the registry-less name
	// list survived a version of this test that compared a rebuilt list.
	seen string
	opts llm.Options
}

func (s *llmSelectStub) Name() string { return "stub" }
func (s *llmSelectStub) Complete(_ context.Context, msgs []llm.Message,
	opts llm.Options) (*llm.Response, error) {
	s.calls++
	if len(msgs) > 0 {
		s.seen = msgs[0].Content
	}
	s.opts = opts
	return &llm.Response{Content: s.reply}, nil
}

// The LLM fallback is only entered when the keyword score is BELOW the
// threshold, and it only wins when the reply's first whitespace-delimited
// token, lowercased, is one of the available names.
func TestLLMFallbackMatchesCPython(t *testing.T) {
	var py struct {
		Names     []string `json:"names"`
		Prompt    string   `json:"prompt"`
		LowScore  []any    `json:"low_score"`
		HighScore []any    `json:"high_score"`
	}
	personaProbe(t).RunJSON(t, `
import json
import persona
names = list(n for _, n, _ in persona._PERSONA_ROUTING) + [persona._DEFAULT_PERSONA]

class Resp:
    def __init__(self, c): self.content = c
class Ad:
    def __init__(self, c): self.c = c; self.seen = None
    def complete(self, msgs, **kw):
        self.seen = msgs[0].content
        return Resp(self.c)

a = Ad("  REPORTER \n extra words ")
low = list(persona.persona_for_goal("zzz nothing matches here", None,
                                    allow_llm_fallback=True, adapter=a))
b = Ad("reporter")
high = list(persona.persona_for_goal("check my inbox and calendar", None,
                                     allow_llm_fallback=True, adapter=b))
print(json.dumps({"names": names, "prompt": a.seen,
                  "low_score": low, "high_score": high,
                  }, ensure_ascii=False))
`, &py)

	// CLAIM: the two arms really are different arms.
	if py.LowScore[0].(string) != "reporter" || py.LowScore[1].(float64) != 0.80 {
		t.Fatalf("CLAIM moved: a below-threshold goal no longer takes the LLM "+
			"answer (%v)", py.LowScore)
	}
	if py.HighScore[0].(string) == "reporter" {
		t.Fatalf("CLAIM moved: an above-threshold goal consulted the LLM (%v)",
			py.HighScore)
	}

	// The prompt text is compared because it is a named agentic seam whose
	// wording decides what the model answers.
	stub := &llmSelectStub{reply: "  REPORTER \n extra words "}
	gotName, gotConf := ForGoal(context.Background(), "zzz nothing matches here",
		nil, 0.70, true, stub)
	if gotName != py.LowScore[0].(string) || gotConf != py.LowScore[1].(float64) {
		t.Errorf("LLM fallback\n  go %q %v\n  py %v", gotName, gotConf, py.LowScore)
	}
	if stub.calls != 1 {
		t.Errorf("the adapter was called %d times, want 1", stub.calls)
	}
	// The prompt the port actually sent, against the one CPython sent. This
	// is what makes the available-names list, the 300-code-point goal clip
	// and the wording load-bearing instead of decorative.
	if stub.seen != py.Prompt {
		t.Errorf("selection prompt\n  go %q\n  py %q", stub.seen, py.Prompt)
	}
	if stub.opts.MaxTokens != 30 || stub.opts.Purpose != "persona selection" {
		t.Errorf("adapter options: max_tokens=%d purpose=%q, want 30 / "+
			"\"persona selection\"", stub.opts.MaxTokens, stub.opts.Purpose)
	}

	// The registry-less available_names list, checked a second way: the
	// prompt comparison above already covers it through the real call path,
	// and this names the list explicitly for a reader.
	var built []string
	for _, row := range personaRouting {
		built = append(built, row.Persona)
	}
	built = append(built, DefaultPersona)
	if strings.Join(built, ",") != strings.Join(py.Names, ",") {
		t.Errorf("available_names with registry=None\n  go %v\n  py %v", built, py.Names)
	}

	// An above-threshold goal must not call the adapter at all.
	quiet := &llmSelectStub{reply: "reporter"}
	name2, conf2 := ForGoal(context.Background(), "check my inbox and calendar",
		nil, 0.70, true, quiet)
	if quiet.calls != 0 {
		t.Errorf("the adapter was consulted for an above-threshold goal")
	}
	if name2 != py.HighScore[0].(string) || conf2 != py.HighScore[1].(float64) {
		t.Errorf("above-threshold\n  go %q %v\n  py %v", name2, conf2, py.HighScore)
	}

	// A reply naming nothing on the list falls through to the keyword
	// answer, and an empty reply does too.
	for _, reply := range []string{"", "   ", "not-a-persona", "\n\n"} {
		s := &llmSelectStub{reply: reply}
		n, c := ForGoal(context.Background(), "zzz nothing matches here",
			nil, 0.70, true, s)
		if n != DefaultPersona || c != 0.5 {
			t.Errorf("reply %q: got (%q, %v), want the keyword answer "+
				"(%q, 0.5)", reply, n, c, DefaultPersona)
		}
	}
}

// The prompt is built with a CODE POINT slice of the goal. A byte slice of
// a 300+ character non-ASCII goal cuts mid-rune.
func TestLLMFallbackPromptSlicesByCodePoint(t *testing.T) {
	goal := strings.Repeat("研", 400)
	var py string
	personaProbe(t).RunJSON(t, `
import json, sys
import persona
class Resp:
    def __init__(self, c): self.content = c
class Ad:
    def __init__(self): self.seen = None
    def complete(self, msgs, **kw):
        self.seen = msgs[0].content
        return Resp("")
a = Ad()
persona.persona_for_goal(json.loads(sys.argv[1]), None,
                         allow_llm_fallback=True, adapter=a)
print(json.dumps(a.seen, ensure_ascii=False))
`, &py, pyprobe.Arg(t, goal))

	stub := &llmSelectStub{reply: ""}
	ForGoal(context.Background(), goal, nil, 0.70, true, stub)
	built := "Available personas: " + strings.Join(append(routingNames(), DefaultPersona), ", ") +
		"\n\nGoal: " + clipRunes(goal, 300) +
		"\n\nWhich single persona best fits this goal? Reply with ONLY the persona name, nothing else."
	if built != py {
		t.Errorf("selection prompt\n  go %q\n  py %q", built, py)
	}
	if strings.Contains(py, "�") {
		t.Fatal("CLAIM moved: CPython's own prompt now carries U+FFFD")
	}
}

func routingNames() []string {
	var out []string
	for _, row := range personaRouting {
		out = append(out, row.Persona)
	}
	return out
}
