package playbook

import (
	"context"
	"errors"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// The LLM compression branch was, until adversarial r9, unreachable in
// every test: curateWorkspace pinned curation_min_chars to 1<<30 and
// every call site passed a nil adapter. `compress` sat at 0% coverage,
// three mutants against it survived, and the `a != nil` guard that keeps
// a nil adapter out of it was never load-bearing under test — while in
// production it is the only thing between Curate and a nil dereference on
// a path documented as never failing.
//
// Both runtimes accept an injected adapter, so these are differentials
// like everything else here, not Go-side reconstructions.

// pyStubAdapter is a CPython object shaped like the adapter
// curate_playbook calls: one `complete` returning an object with
// `.content`. It also records the kwargs, so the call's SHAPE is
// comparable and not just its result.
const pyStubAdapter = `
class _R:
    def __init__(self, c): self.content = c
class _Stub:
    def __init__(self, out): self.out = out; self.seen = []
    def complete(self, msgs, **kw):
        self.seen.append({'prompt': msgs[0].content, 'kw': {
            k: kw.get(k) for k in ('max_tokens','temperature','no_tools','purpose')}})
        return _R(self.out)
_stub = _Stub(json.loads(sys.argv[2]))
`

const pyCurateWithStub = pyStubAdapter + `
doc=json.loads(sys.argv[1])
import pathlib
p=playbook._playbook_path()
st=playbook.curate_playbook(force=True, adapter=_stub)
if st is not None:
    st=dict(st); st['archived']=pathlib.Path(st['archived']).name
d=playbook._history_dir()
arch=sorted(x.name for x in d.iterdir()) if d.exists() else []
print(json.dumps({'stats':st,
                  'file':p.read_text(encoding='utf-8'),
                  'archives':[(d/a).read_text(encoding='utf-8') for a in arch],
                  'calls':_stub.seen}))
`

type pyCurateWithStubResult struct {
	pyCurateResult
	Calls []struct {
		Prompt string `json:"prompt"`
		Kw     struct {
			MaxTokens   int     `json:"max_tokens"`
			Temperature float64 `json:"temperature"`
			NoTools     bool    `json:"no_tools"`
			Purpose     string  `json:"purpose"`
		} `json:"kw"`
	} `json:"calls"`
}

// recorder keeps the raw messages and options. llm.Fake records
// BuildPrompt's rendering rather than the message content, and the
// contract with the model is the CONTENT — comparing the rendering would
// pin Go's prompt assembly against CPython's raw string and fail for a
// reason that has nothing to do with this package.
type recorder struct {
	reply string
	err   error
	msgs  [][]llm.Message
	opts  []llm.Options
}

func (r *recorder) Name() string { return "recorder" }

func (r *recorder) Complete(_ context.Context, msgs []llm.Message,
	o llm.Options) (*llm.Response, error) {
	r.msgs = append(r.msgs, msgs)
	r.opts = append(r.opts, o)
	if r.err != nil {
		return nil, r.err
	}
	return &llm.Response{Content: r.reply}, nil
}

// A document big enough to cross a small gate, with real duplicates and
// attributions so the validator has something to check.
func compressibleDoc() string {
	var b strings.Builder
	b.WriteString("# Director's Playbook\n\n## Cost\n\n")
	for i := 0; i < 12; i++ {
		b.WriteString("- a fairly verbose bullet about caching and reuse " +
			string(rune('a'+i)) + " *(from run-" + string(rune('a'+i)) + ")*\n")
	}
	b.WriteString("- a fairly verbose bullet about caching and reuse a *(from run-a)*\n")
	b.WriteString("\n*Last updated: 2020-01-01*\n")
	return b.String()
}

func TestTheCompressionPassMatchesPythons(t *testing.T) {
	doc := compressibleDoc()

	// A candidate that PASSES validation, and one that fails it — the
	// accepted branch and the rejected branch are different code and the
	// rejected one must keep the deterministic result.
	//
	// The floor is load-bearing on the fixture, not just on the code: the
	// deduped original has 12 bullets, so ceil(12*0.6) = 8 must survive.
	// A four-bullet "tightening" is rejected by CPython, and the first
	// version of this fixture was — the label self-check caught it.
	tight := "# Director's Playbook\n\n## Cost\n\n" +
		"- cache *(from run-a)*\n- reuse *(from run-b)*\n" +
		"- batch *(from run-c)*\n- retry *(from run-d)*\n" +
		"- cap *(from run-e)*\n- log *(from run-f)*\n" +
		"- pin *(from run-g)*\n- trim *(from run-h)*\n" +
		"- fold *(from run-i)*\n- skip *(from run-j)*\n" +
		"- warn *(from run-k)*\n- stop *(from run-l)*\n" +
		"\n*Last updated: 2020-01-01*\n"
	lossy := "# Director's Playbook\n\n## Cost\n\n- cache\n\n*Last updated: 2020-01-01*\n"

	for _, tc := range []struct {
		name      string
		candidate string
		wantUsed  bool
	}{
		{"a valid rewrite is adopted", tight, true},
		{"a rewrite that drops attributions is rejected", lossy, false},
		{"an empty reply is rejected", "", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			pyWS := curateWorkspaceRaw(t, doc,
				"playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: 10\n")
			var want pyCurateWithStubResult
			runPython(t, pyWS, pyCurateWithStub, &want, doc, tc.candidate)

			if len(want.Calls) != 1 {
				t.Fatalf("CPython made %d adapter calls, not 1 — the gate is "+
					"not being crossed and this case proves nothing", len(want.Calls))
			}
			if want.Stats == nil {
				t.Fatal("CPython reported no change; this case proves nothing")
			}
			if want.Stats.LLMCompressed != tc.wantUsed {
				t.Fatalf("the fixture's own label is wrong: CPython "+
					"llm_compressed=%v, table says %v",
					want.Stats.LLMCompressed, tc.wantUsed)
			}

			goWS := curateWorkspaceRaw(t, doc,
				"playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: 10\n")
			fake := &recorder{reply: tc.candidate}
			got := Curate(context.Background(), goWS, fake, nil, true)

			assertCurateAgrees(t, goWS, got, want.pyCurateResult)

			// The call SHAPE, not just its result. Python passes
			// no_tools=True; the Go equivalent is AgentTools staying
			// false, which is the subprocess adapter's utility lane.
			if len(fake.opts) != 1 {
				t.Fatalf("Go made %d adapter calls, CPython made 1", len(fake.opts))
			}
			o := fake.opts[0]
			k := want.Calls[0].Kw
			if o.MaxTokens != k.MaxTokens {
				t.Errorf("max_tokens: go %d, py %d", o.MaxTokens, k.MaxTokens)
			}
			if o.Temperature != k.Temperature {
				t.Errorf("temperature: go %v, py %v", o.Temperature, k.Temperature)
			}
			if o.Purpose != k.Purpose {
				t.Errorf("purpose: go %q, py %q", o.Purpose, k.Purpose)
			}
			if !k.NoTools {
				t.Error("CPython no longer passes no_tools=True")
			}
			if o.AgentTools || len(o.Tools) != 0 {
				t.Errorf("no_tools=True must mean no tools of either kind: "+
					"AgentTools=%v Tools=%d", o.AgentTools, len(o.Tools))
			}
			// The prompt is the whole contract with the model.
			if len(fake.msgs[0]) != 1 || fake.msgs[0][0].Role != "user" {
				t.Fatalf("want exactly one user message, got %+v", fake.msgs[0])
			}
			if fake.msgs[0][0].Content != want.Calls[0].Prompt {
				t.Errorf("the compression prompt differs\n go: %q\n py: %q",
					fake.msgs[0][0].Content, want.Calls[0].Prompt)
			}
		})
	}
}

// The gate is a CODE-POINT length in Python. A byte-length gate sends a
// CJK playbook to a paid model that Python would leave alone — which is
// the same class as every other length bug in this port, on the one path
// that costs money.
func TestTheCompressionSizeGateCountsCodePoints(t *testing.T) {
	// 60 CJK code points of body: well over 60 bytes, well under 200.
	var b strings.Builder
	b.WriteString("# P\n\n## Cost\n\n")
	for i := 0; i < 6; i++ {
		b.WriteString("- 日本語テキストです\n")
	}
	b.WriteString("- 日本語テキストです\n\n*Last updated: 2020-01-01*\n")
	doc := b.String()

	runes := utf8.RuneCountInString(doc)
	nbytes := len(doc)
	// The gate is DERIVED from the fixture rather than hand-picked, so a
	// later edit to the document cannot quietly move the boundary out
	// from under it — which is exactly how five hand-picked Inject
	// budgets stopped discriminating earlier in this port.
	gate := (runes + nbytes) / 2
	if !(runes < gate && gate < nbytes) {
		t.Fatalf("fixture does not straddle the gate: %d runes, %d bytes",
			runes, nbytes)
	}
	cfg := "playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: " +
		itoa(gate) + "\n"

	pyWS := curateWorkspaceRaw(t, doc, cfg)
	var want pyCurateWithStubResult
	runPython(t, pyWS, pyCurateWithStub, &want, doc, "# P\n\n## Cost\n\n- x\n")
	if len(want.Calls) != 0 {
		t.Fatalf("CPython called the model %d times; the fixture no longer "+
			"straddles the gate", len(want.Calls))
	}

	goWS := curateWorkspaceRaw(t, doc, cfg)
	fake := &recorder{reply: "# P\n\n## Cost\n\n- x\n"}
	Curate(context.Background(), goWS, fake, nil, true)
	if len(fake.opts) != 0 {
		t.Errorf("Go called the model %d times where CPython called it 0 — "+
			"the size gate is counting bytes", len(fake.opts))
	}
}

// Curate is documented as never failing: callers sit on exit paths, and
// Go has no recover(). A nil adapter is the documented way to skip
// compression, so it must reach the gate and stop there — not reach
// compress and dereference.
func TestANilAdapterSkipsCompressionInsteadOfPanicking(t *testing.T) {
	doc := compressibleDoc()
	ws := curateWorkspaceRaw(t, doc,
		"playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: 10\n")

	got := Curate(context.Background(), ws, nil, nil, true)
	if got == nil {
		t.Fatal("a nil adapter abandoned the deterministic pass entirely")
	}
	if got.LLMCompressed {
		t.Error("a nil adapter reported an LLM compression")
	}
	if got.RemovedDuplicates != 1 {
		t.Errorf("the deterministic pass did not run: removed=%d",
			got.RemovedDuplicates)
	}

	// And the gate really was crossed, so the nil guard is what stopped
	// it rather than the size check.
	if n := utf8.RuneCountInString(doc); n <= 10 {
		t.Fatalf("fixture is under the 10-char gate (%d); the nil guard was "+
			"never reached and this test proves nothing", n)
	}
}

// A transport error must also keep the deterministic result rather than
// abandoning the pass — Python's `except Exception` around the call does
// exactly that, and it is a different branch from a rejected rewrite.
func TestAnAdapterErrorKeepsTheDeterministicResult(t *testing.T) {
	doc := compressibleDoc()

	// An empty script makes Fake return an error rather than a reply.
	ws := curateWorkspaceRaw(t, doc,
		"playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: 10\n")
	fake := &recorder{err: errBrokenTransport}
	got := Curate(context.Background(), ws, fake, nil, true)

	if len(fake.opts) != 1 {
		t.Fatalf("the adapter was called %d times; the error path was not "+
			"exercised", len(fake.opts))
	}
	if got == nil {
		t.Fatal("an adapter error abandoned the whole curation pass")
	}
	if got.LLMCompressed {
		t.Error("an errored call was reported as a compression")
	}
	if got.RemovedDuplicates != 1 {
		t.Errorf("the deterministic result was lost: removed=%d",
			got.RemovedDuplicates)
	}
}

var errBrokenTransport = errors.New("transport is down")
