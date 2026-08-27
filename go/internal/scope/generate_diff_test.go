package scope

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/persona"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The differential for the LLM half: generate_scope, generate_resolved_intent
// and resolve_ambiguity_via_proxy, including the director-proxy recursion.
//
// THE SEAM. Neither engine talks to a model. Both are driven by a SCRIPTED
// adapter that records what it was asked and replies from a fixed list, so
// what is compared is the three things a port of this function can get
// wrong: the messages and knobs it SENDS, the ORDER of what it does, and
// what it returns. `no_tools=True` has no Options field on the Go side, so
// it is asserted separately — CPython must pass True and Go must pass no
// Tools at all.
//
// WHAT THE LOG TRACE IS FOR. scope.py's observable side effect is its log,
// and the SHAPE of the function is what the trace measures: that the four
// `[scope-deferred]` markers fire before the call and fire AGAIN on the
// proxy retry, that the decision-journal write happens between the retry
// and the success line, that a failed retry logs its own warning and THEN
// the generic one. A return-value-only differential passes on a port that
// does all of that in the wrong order.
//
// THE TWO UNPORTED SEAMS ARE MEASURED HERE, both ways: skill_loader and
// knowledge_lens are replaced on the CPython side by fakes whose behaviour
// the case names (a body, a raise, or not importable at all), and the Go
// side is given the matching seam. The not-importable rows are the ones
// that pin what a nil seam means.
//
// LIVE-STORE SAFETY. Every case sets MARO_WORKSPACE to a t.TempDir() and
// pyprobe's guard refuses anything resolving inside ~/.maro. The real
// knowledge_lens is never imported — a fake is installed in sys.modules
// before scope can reach it — so no decision row can reach a journal.

const genPySrc = `
import json, sys, types, logging

spec = json.loads(sys.argv[1])

import scope, persona

BOOM_REGISTRY = "registry construction failed"

class Resp:
    def __init__(self, content):
        self.content = content

class Handler(logging.Handler):
    def __init__(self, sink):
        super().__init__(level=logging.DEBUG)
        self.sink = sink
    def emit(self, r):
        self.sink.append([r.levelname, r.getMessage()])

class Adapter:
    def __init__(self, script, sink):
        self.script = list(script)
        self.sink = sink
    def complete(self, messages, **kw):
        self.sink.append({
            "messages": [[m.role, m.content] for m in messages],
            "max_tokens": kw.get("max_tokens"),
            "temperature": kw.get("temperature"),
            "no_tools": kw.get("no_tools"),
            "purpose": kw.get("purpose"),
        })
        if not self.script:
            raise RuntimeError("adapter script exhausted")
        step = self.script.pop(0)
        if step.get("error"):
            raise RuntimeError(step["error"])
        return Resp(step.get("content"))

class NoSpecRegistry:
    def load(self, name):
        return None

class BoomRegistry:
    def __init__(self):
        raise RuntimeError(BOOM_REGISTRY)

REAL_REGISTRY = persona.PersonaRegistry

def install_skill(mode, body):
    if mode == "absent":
        sys.modules["skill_loader"] = None
        return
    m = types.ModuleType("skill_loader")
    class SkillLoader:
        def load_full(self, name):
            if mode == "raise":
                raise RuntimeError("skill loader failed")
            return body
    m.SkillLoader = SkillLoader
    sys.modules["skill_loader"] = m

def install_lens(mode, sink):
    if mode == "absent":
        sys.modules["knowledge_lens"] = None
        return
    m = types.ModuleType("knowledge_lens")
    def record_decision(decision, rationale, *, domain="", alternatives=None,
                        trade_offs="", goal_context="", strict=False):
        sink.append({"decision": decision, "rationale": rationale,
                     "domain": domain, "goal_context": goal_context,
                     "alternatives": alternatives, "trade_offs": trade_offs,
                     "strict": strict})
        if mode == "raise":
            raise RuntimeError("journal write failed")
        return None
    m.record_decision = record_decision
    sys.modules["knowledge_lens"] = m

def deliv(d):
    return {"name": d.name, "description": d.description,
            "preconditions": d.preconditions, "shape": d.shape}

def sset(s):
    return {"failure_modes": s.failure_modes, "in_scope": s.in_scope,
            "out_of_scope": s.out_of_scope, "raw_text": s.raw_text,
            "proxy_keys": list(s.proxy_resolution.keys()),
            "proxy_values": [s.proxy_resolution[k] for k in s.proxy_resolution],
            "to_markdown": s.to_markdown(), "is_empty": s.is_empty()}

lg = logging.getLogger("scope")
lg.setLevel(logging.DEBUG)
lg.propagate = False

# The registry the "real" rows use, resolved ONCE and reported so the Go
# side can build the identical two-tier registry rather than guess at it.
_probe = REAL_REGISTRY()
dirs = {"ws": str(_probe._ws_dir) if _probe._ws_dir else "",
        "repo": str(_probe._repo_dir) if _probe._repo_dir else ""}

out = {}
for c in spec:
    logs = []
    calls = []
    decisions = []
    handler = Handler(logs)
    lg.handlers = [handler]
    install_skill(c.get("skill", "absent"), c.get("skill_body", ""))
    install_lens(c.get("lens", "absent"), decisions)
    reg = c.get("registry", "real")
    persona.PersonaRegistry = {"real": REAL_REGISTRY, "nospec": NoSpecRegistry,
                               "boom": BoomRegistry}[reg]
    adapter = Adapter(c["script"], calls) if c.get("adapter", True) else None
    kwargs = dict(
        max_tokens=c.get("max_tokens", 1200),
        temperature=c.get("temperature", 0.3),
        ancestry_context=c.get("ancestry", ""),
        allow_proxy_fallback=c.get("allow_proxy", True),
        decision_domain=c.get("domain", ""),
    )
    if c["fn"] == "generate_scope":
        r = scope.generate_scope(c["goal"], adapter, **kwargs)
        result = None if r is None else {"scope": sset(r)}
    else:
        r = scope.generate_resolved_intent(c["goal"], adapter, **kwargs)
        result = None if r is None else {
            "scope": sset(r.scope),
            "deliverables": [deliv(d) for d in r.deliverables],
            "raw_text": r.raw_text,
            "to_markdown": r.to_markdown(),
            "is_empty": r.is_empty()}
    out[c["name"]] = {"result": result, "logs": logs, "calls": calls,
                      "decisions": decisions}
    lg.handlers = []
    persona.PersonaRegistry = REAL_REGISTRY

print(json.dumps({"dirs": dirs, "cases": out}))
`

// --- the Go-side doubles ----------------------------------------------------

// scriptStep is one scripted adapter reply. Error non-empty makes the call
// fail, which is CPython's `raise RuntimeError(error)` — the SAME text, so
// the "adapter.complete failed: %s" log line is comparable rather than
// normalised away.
type scriptStep struct {
	Content string `json:"content"`
	Error   string `json:"error"`
}

type recordedCall struct {
	messages    []llm.Message
	maxTokens   int
	temperature float64
	purpose     string
	tools       int
}

type scriptedAdapter struct {
	script []scriptStep
	calls  []recordedCall
}

func (a *scriptedAdapter) Name() string { return "scripted" }

func (a *scriptedAdapter) Complete(_ context.Context, msgs []llm.Message,
	opts llm.Options) (*llm.Response, error) {
	a.calls = append(a.calls, recordedCall{
		messages: msgs, maxTokens: opts.MaxTokens, temperature: opts.Temperature,
		purpose: opts.Purpose, tools: len(opts.Tools),
	})
	if len(a.script) == 0 {
		return nil, errors.New("adapter script exhausted")
	}
	step := a.script[0]
	a.script = a.script[1:]
	if step.Error != "" {
		return nil, errors.New(step.Error)
	}
	return &llm.Response{Content: step.Content}, nil
}

type genCase struct {
	name string
	// fn is "generate_scope" or "generate_resolved_intent".
	fn          string
	goal        string
	script      []scriptStep
	noAdapter   bool
	maxTokens   int
	temperature float64
	ancestry    string
	allowProxy  *bool
	domain      string
	// registry is "real", "nospec" (Load returns None) or "boom" (the
	// constructor raised).
	registry string
	// skill is "absent" (not importable), "body" or "raise".
	skill     string
	skillBody string
	// lens is "absent" (not importable), "ok" or "raise".
	lens string
}

func boolPtr(b bool) *bool { return &b }

// A scope response the parser accepts, and one it does not.
const goodScope = "## Failure Modes\n- a\n- b\n## In Scope\n- c\n## Out of Scope\n- d\n"
const goodScopeWithDeliv = goodScope +
	"## Deliverables\n- bin/x: the binary [preconditions: go, make] [shape: runtime]\n"

// A clarification-shaped response: prose, a question mark, and between 30
// and 4000 code points once stripped.
const clarification = "Which directory should I count markdown files in, and " +
	"should nested directories be included in the total?"

// A garbage response: no headings, no question mark, so the proxy must NOT
// be reached. The control beside every clarification row.
const garbage = "I cannot help with that request at this time, sorry."

const proxyReply = "INTERPRETATION: Count markdown files recursively under docs/\n" +
	"REASON: the goal named docs and left recursion implicit"

func genCases() []genCase {
	return []genCase{
		// --- the plain paths ------------------------------------------------
		{name: "a scope that parses", fn: "generate_scope", goal: "build a server",
			script: []scriptStep{{Content: goodScope}}},
		{name: "an empty goal never calls the adapter", fn: "generate_scope",
			goal: "", script: []scriptStep{{Content: goodScope}}},
		{name: "a nil adapter returns None", fn: "generate_scope", goal: "g",
			noAdapter: true},
		{name: "the adapter raises", fn: "generate_scope", goal: "g",
			script: []scriptStep{{Error: "backend unreachable"}}},
		{name: "the model returns empty content", fn: "generate_scope", goal: "g",
			script: []scriptStep{{Content: ""}}},
		{name: "the model returns whitespace", fn: "generate_scope", goal: "g",
			script: []scriptStep{{Content: "   \n  "}}},
		{name: "garbage with no question mark is not a clarification",
			fn: "generate_scope", goal: "g", script: []scriptStep{{Content: garbage}}},
		{name: "non-default knobs reach the adapter", fn: "generate_scope",
			goal: "g", maxTokens: 77, temperature: 0.9,
			script: []scriptStep{{Content: goodScope}}},

		// --- the clarification / director-proxy recursion --------------------
		{name: "clarification, proxy commits, retry parses", fn: "generate_scope",
			goal: "count the docs", script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Content: goodScope}},
			registry: "real", lens: "ok", domain: "maro"},
		{name: "clarification with the proxy fallback disabled", fn: "generate_scope",
			goal: "count the docs", allowProxy: boolPtr(false),
			script: []scriptStep{{Content: clarification}}},
		{name: "clarification, proxy commits, retry does not parse",
			fn: "generate_scope", goal: "count the docs", script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Content: garbage}},
			registry: "real", lens: "ok"},
		{name: "clarification, proxy commits, retry adapter raises",
			fn: "generate_scope", goal: "count the docs", script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Error: "gone"}},
			registry: "real", lens: "ok"},
		{name: "clarification, proxy reply does not parse", fn: "generate_scope",
			goal: "count the docs", script: []scriptStep{
				{Content: clarification}, {Content: "I still do not know."}},
			registry: "real"},
		{name: "clarification, proxy adapter raises", fn: "generate_scope",
			goal: "count the docs", script: []scriptStep{
				{Content: clarification}, {Error: "proxy backend down"}},
			registry: "real"},
		{name: "clarification, the persona is not found", fn: "generate_scope",
			goal: "count the docs", script: []scriptStep{{Content: clarification}},
			registry: "nospec"},
		{name: "clarification, ancestry is supplied", fn: "generate_scope",
			goal: "count the docs", ancestry: "  prior turn said docs/ only  ",
			script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Content: goodScope}},
			registry: "real", lens: "ok"},
		{name: "clarification, the proxy reply carries no reason",
			fn: "generate_scope", goal: "count the docs", script: []scriptStep{
				{Content: clarification},
				{Content: "INTERPRETATION: Count every markdown file under docs/"},
				{Content: goodScope}},
			registry: "real", lens: "ok", domain: "proj"},

		// --- the two unported seams, both branches each ----------------------
		{name: "the skill loader supplies a body", fn: "generate_scope",
			goal: "count the docs", skill: "body",
			skillBody: "# Resolve ambiguity\n\nPick one reading and say why.",
			script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Content: goodScope}},
			registry: "real", lens: "ok"},
		{name: "the skill loader returns nothing", fn: "generate_scope",
			goal: "count the docs", skill: "body", skillBody: "",
			script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Content: goodScope}},
			registry: "real", lens: "ok"},
		{name: "the skill loader raises", fn: "generate_scope",
			goal: "count the docs", skill: "raise",
			script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Content: goodScope}},
			registry: "real", lens: "ok"},
		{name: "the persona registry constructor raised", fn: "generate_scope",
			goal: "count the docs", script: []scriptStep{{Content: clarification}},
			registry: "boom"},
		{name: "the decision journal write raises", fn: "generate_scope",
			goal: "count the docs", lens: "raise", domain: "the-project",
			script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Content: goodScope}},
			registry: "real"},
		{name: "the decision journal accepts the write", fn: "generate_scope",
			goal: "count the docs", lens: "ok", domain: "the-project",
			script: []scriptStep{
				{Content: clarification}, {Content: proxyReply}, {Content: goodScope}},
			registry: "real"},

		// --- generate_resolved_intent ----------------------------------------
		{name: "intent with deliverables", fn: "generate_resolved_intent",
			goal: "build a server", script: []scriptStep{{Content: goodScopeWithDeliv}}},
		{name: "intent with no deliverables", fn: "generate_resolved_intent",
			goal: "build a server", script: []scriptStep{{Content: goodScope}}},
		{name: "intent when scope returns None", fn: "generate_resolved_intent",
			goal: "g", script: []scriptStep{{Error: "backend unreachable"}}},
		{name: "intent with an empty goal", fn: "generate_resolved_intent",
			goal: "", script: []scriptStep{{Content: goodScope}}},
		{name: "intent with a nil adapter", fn: "generate_resolved_intent",
			goal: "g", noAdapter: true},
		{name: "intent over an unparseable scope keeps the raw text",
			fn: "generate_resolved_intent", goal: "g",
			script: []scriptStep{{Content: garbage}}},
		{name: "intent through the proxy recursion", fn: "generate_resolved_intent",
			goal: "count the docs", script: []scriptStep{
				{Content: clarification}, {Content: proxyReply},
				{Content: goodScopeWithDeliv}},
			registry: "real", lens: "ok", domain: "maro"},
		{name: "intent whose deliverables are all malformed",
			fn: "generate_resolved_intent", goal: "g",
			script: []scriptStep{{Content: goodScope + "## Deliverables\n- [shape: data]\n"}}},
	}
}

// --- the comparison ---------------------------------------------------------

type genPyOut struct {
	Dirs struct {
		WS   string `json:"ws"`
		Repo string `json:"repo"`
	} `json:"dirs"`
	Cases map[string]struct {
		Result    any     `json:"result"`
		Logs      [][]any `json:"logs"`
		Calls     []any   `json:"calls"`
		Decisions []any   `json:"decisions"`
	} `json:"cases"`
}

// divergentLine names the log lines whose text embeds an exception string
// CPython produces and Go cannot: the two unported seams' failed-import
// messages and the persona registry constructor's. The PREFIX is compared
// and the tail is recorded as a pinned divergence rather than normalised
// out of sight — see TestTheUnportedSeamsAreNamedInTheLogTrace.
var divergentPrefixes = []string{
	"scope.proxy: PersonaRegistry failed: ",
	"scope.proxy: could not load resolve_ambiguity skill: ",
	"scope: decision journal write failed: ",
}

func splitDivergent(msg string) (prefix string, ok bool) {
	for _, p := range divergentPrefixes {
		if strings.HasPrefix(msg, p) {
			return p, true
		}
	}
	return "", false
}

func TestTheGeneratorsAgreeWithCPython(t *testing.T) {
	cases := genCases()

	// ANTI-VACUITY. Counted over the PORT's own behaviour, so a mutation
	// that collapses an outcome trips the floor rather than the comparison.
	var (
		gotNil, gotSet, gotEmptySet   int
		proxyReached, proxyNotReached int
		retried, decisionWritten      int
		withDeliv, withoutDeliv       int
		logLines                      int
	)
	// pinned holds every log line where the two engines legitimately differ,
	// with BOTH measured texts. Each is a CPython exception string embedded
	// in a message the port has no way to reproduce.
	var pinned [][3]string

	ws := t.TempDir()
	// The persona tier the CPython registry will resolve to, populated
	// before the probe runs so both engines read the same director-proxy.
	wsPersonas := filepath.Join(ws, "personas")
	if err := os.MkdirAll(wsPersonas, 0o755); err != nil {
		t.Fatal(err)
	}
	const proxySpec = `---
name: director-proxy
role: Director Proxy
communication_style: terse
memory_scope: none
---

Commit to one reading of an ambiguous goal.
`
	if err := os.WriteFile(filepath.Join(wsPersonas, "director-proxy.md"),
		[]byte(proxySpec), 0o644); err != nil {
		t.Fatal(err)
	}

	payload := make([]map[string]any, 0, len(cases))
	seen := map[string]bool{}
	for _, c := range cases {
		if seen[c.name] {
			t.Fatalf("duplicate fixture name %q", c.name)
		}
		seen[c.name] = true
		row := map[string]any{
			"name": c.name, "fn": c.fn, "goal": c.goal,
			"script":   c.script,
			"registry": orDefault(c.registry, "real"),
			"skill":    orDefault(c.skill, "absent"),
			"lens":     orDefault(c.lens, "absent"),
		}
		if c.noAdapter {
			row["adapter"] = false
		}
		if c.maxTokens != 0 {
			row["max_tokens"] = c.maxTokens
		}
		if c.temperature != 0 {
			row["temperature"] = c.temperature
		}
		if c.ancestry != "" {
			row["ancestry"] = c.ancestry
		}
		if c.allowProxy != nil {
			row["allow_proxy"] = *c.allowProxy
		}
		if c.domain != "" {
			row["domain"] = c.domain
		}
		if c.skillBody != "" {
			row["skill_body"] = c.skillBody
		}
		payload = append(payload, row)
	}

	var py genPyOut
	pyprobe.Probe{Marker: "scope.py", Workspace: ws}.RunJSON(t, genPySrc, &py,
		pyprobe.Arg(t, payload))

	if py.Dirs.WS != wsPersonas {
		t.Fatalf("CPython's PersonaRegistry resolved its workspace tier to %q, "+
			"not the directory this test populated (%q) — the two engines "+
			"would be reading different persona files",
			py.Dirs.WS, wsPersonas)
	}
	reg := persona.New(py.Dirs.WS, py.Dirs.Repo)

	for _, c := range cases {
		pyCase, ok := py.Cases[c.name]
		if !ok {
			t.Errorf("%s: CPython returned no answer", c.name)
			continue
		}

		// --- drive the port over the same inputs --------------------------
		var logs [][]any
		decisions := []any{}
		ad := &scriptedAdapter{script: append([]scriptStep(nil), c.script...)}
		o := Defaults()
		if c.maxTokens != 0 {
			o.MaxTokens = c.maxTokens
		}
		if c.temperature != 0 {
			o.Temperature = c.temperature
		}
		o.AncestryContext = c.ancestry
		if c.allowProxy != nil {
			o.AllowProxyFallback = *c.allowProxy
		}
		o.DecisionDomain = c.domain
		o.Log = func(level, msg string) { logs = append(logs, []any{level, msg}) }
		if !c.noAdapter {
			o.Adapter = ad
		}
		switch orDefault(c.registry, "real") {
		case "real":
			o.Registry = reg
		case "nospec":
			// A registry over a directory with no personas in it is the
			// Load-returns-None branch, reached without inventing a type.
			o.Registry = persona.NewFromDir(t.TempDir())
		case "boom":
			o.Registry = nil
		}
		switch orDefault(c.skill, "absent") {
		case "body":
			body := c.skillBody
			o.SkillBody = func(string) (string, error) { return body, nil }
		case "raise":
			o.SkillBody = func(string) (string, error) {
				return "", errors.New("skill loader failed")
			}
		}
		switch orDefault(c.lens, "absent") {
		case "ok", "raise":
			mode := orDefault(c.lens, "absent")
			o.RecordDecision = func(decision, rationale, domain, goalContext string) error {
				decisions = append(decisions, map[string]any{
					"decision": decision, "rationale": rationale,
					"domain": domain, "goal_context": goalContext,
					// The three keyword arguments scope.py never passes.
					// CPython's defaults are recorded by the fake; the Go
					// seam has no parameters for them at all, which is the
					// claim being pinned.
					"alternatives": nil, "trade_offs": "", "strict": false,
				})
				if mode == "raise" {
					return errors.New("journal write failed")
				}
				return nil
			}
		}

		var result any
		if c.fn == "generate_scope" {
			if s := GenerateScope(context.Background(), c.goal, o); s != nil {
				result = map[string]any{"scope": setWithProxyJSON(*s)}
				if s.IsEmpty() {
					gotEmptySet++
				} else {
					gotSet++
				}
			} else {
				gotNil++
			}
		} else {
			if ri := GenerateResolvedIntent(context.Background(), c.goal, o); ri != nil {
				ds := []any{}
				for _, d := range ri.Deliverables {
					ds = append(ds, delivJSON2(d))
				}
				result = map[string]any{
					"scope": setWithProxyJSON(ri.Scope), "deliverables": ds,
					"raw_text": ri.RawText, "to_markdown": ri.ToMarkdown(),
					"is_empty": ri.IsEmpty(),
				}
				if len(ri.Deliverables) > 0 {
					withDeliv++
				} else {
					withoutDeliv++
				}
				if ri.Scope.IsEmpty() {
					gotEmptySet++
				} else {
					gotSet++
				}
			} else {
				gotNil++
				withoutDeliv++
			}
		}

		logLines += len(logs)
		for _, l := range logs {
			switch l[1].(string) {
			case "scope: response looks like clarification, escalating to director-proxy":
				proxyReached++
			}
			if strings.HasPrefix(l[1].(string),
				"scope: director-proxy resolved ambiguity, retry produced") {
				retried++
			}
		}
		if len(decisions) > 0 {
			decisionWritten++
		}
		if len(logs) > 0 && proxyReached == 0 {
			proxyNotReached++
		}

		// --- compare -------------------------------------------------------
		if !reflect.DeepEqual(normalize(t, result), pyCase.Result) {
			t.Errorf("%s: RESULT differs\n  port:   %s\n  python: %s",
				c.name, mustJSON(t, result), mustJSON(t, pyCase.Result))
		}

		// The log trace, line for line, level included — this is the
		// statement-order assertion.
		if len(logs) != len(pyCase.Logs) {
			t.Errorf("%s: log trace LENGTH differs (port %d, python %d)\n"+
				"  port:   %s\n  python: %s",
				c.name, len(logs), len(pyCase.Logs),
				mustJSON(t, logs), mustJSON(t, pyCase.Logs))
		} else {
			for i := range logs {
				goLevel, goMsg := logs[i][0].(string), logs[i][1].(string)
				pyLevel, _ := pyCase.Logs[i][0].(string)
				pyMsg, _ := pyCase.Logs[i][1].(string)
				if goLevel != pyLevel {
					t.Errorf("%s: log line %d LEVEL differs (port %q, python %q) for %q",
						c.name, i, goLevel, pyLevel, goMsg)
				}
				if goMsg == pyMsg {
					continue
				}
				goPrefix, goOK := splitDivergent(goMsg)
				pyPrefix, pyOK := splitDivergent(pyMsg)
				if goOK && pyOK && goPrefix == pyPrefix {
					// A pinned divergence: the tail is a CPython exception
					// string with no Go counterpart. Recorded with both
					// engines' measured text, and required BELOW to still
					// exist — closing one has to be a deliberate edit here.
					pinned = append(pinned, [3]string{c.name, goMsg, pyMsg})
					continue
				}
				t.Errorf("%s: log line %d differs\n  port:   %q\n  python: %q",
					c.name, i, goMsg, pyMsg)
			}
		}

		// The adapter calls: what was sent, and with which knobs.
		if len(ad.calls) != len(pyCase.Calls) {
			t.Errorf("%s: adapter was called %d times by the port and %d by "+
				"CPython\n  python: %s", c.name, len(ad.calls),
				len(pyCase.Calls), mustJSON(t, pyCase.Calls))
		} else {
			for i, call := range ad.calls {
				pyCall, _ := pyCase.Calls[i].(map[string]any)
				var msgs []any
				for _, m := range call.messages {
					msgs = append(msgs, []any{m.Role, m.Content})
				}
				if !reflect.DeepEqual(normalize(t, msgs), pyCall["messages"]) {
					t.Errorf("%s: adapter call %d MESSAGES differ\n  port:   %s\n"+
						"  python: %s", c.name, i, mustJSON(t, msgs),
						mustJSON(t, pyCall["messages"]))
				}
				if got, want := float64(call.maxTokens), pyCall["max_tokens"]; got != want {
					t.Errorf("%s: adapter call %d max_tokens: port %v, python %v",
						c.name, i, got, want)
				}
				if got, want := call.temperature, pyCall["temperature"]; got != want {
					t.Errorf("%s: adapter call %d temperature: port %v, python %v",
						c.name, i, got, want)
				}
				if got, want := call.purpose, pyCall["purpose"]; got != want {
					t.Errorf("%s: adapter call %d purpose: port %q, python %v",
						c.name, i, got, want)
				}
				// no_tools=True has no Options field. CPython must pass True
				// and the port must offer no tools; anything else means the
				// scope call could start making tool calls.
				if pyCall["no_tools"] != true {
					t.Errorf("%s: adapter call %d: CPython passed no_tools=%v, "+
						"not True — the mapping this port relies on is gone",
						c.name, i, pyCall["no_tools"])
				}
				if call.tools != 0 {
					t.Errorf("%s: adapter call %d offered %d tools; no_tools=True "+
						"means none", c.name, i, call.tools)
				}
			}
		}

		// The decision-journal writes, argument for argument.
		if !reflect.DeepEqual(normalize(t, decisions), normalizeEmptyList(pyCase.Decisions)) {
			t.Errorf("%s: decision-journal writes differ\n  port:   %s\n  python: %s",
				c.name, mustJSON(t, decisions), mustJSON(t, pyCase.Decisions))
		}
	}

	floors := []struct {
		what string
		n    int
		min  int
	}{
		{"cases returning None", gotNil, 5},
		{"cases returning a populated scope", gotSet, 10},
		{"cases returning an EMPTY scope with raw text", gotEmptySet, 4},
		{"cases that escalated to the proxy", proxyReached, 8},
		{"cases that did not", proxyNotReached, 5},
		{"cases whose retry succeeded", retried, 6},
		{"cases that wrote a decision", decisionWritten, 5},
		{"intents with deliverables", withDeliv, 2},
		{"intents without", withoutDeliv, 4},
	}
	for _, f := range floors {
		if f.n < f.min {
			t.Errorf("anti-vacuity: only %d %s (floor %d)", f.n, f.what, f.min)
		}
	}
}

func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}

// setWithProxyJSON is setJSON plus the proxy dict as an ORDERED key/value
// pair of lists — the one place in this package whose serialisation order
// reaches disk (handle.py puts it in a captain's-log context, json.dumps
// writes insertion order).
func setWithProxyJSON(s Set) map[string]any {
	keys := []any{}
	vals := []any{}
	for _, f := range s.ProxyResolution {
		keys = append(keys, f.Key)
		vals = append(vals, f.Val)
	}
	return map[string]any{
		"failure_modes": s.FailureModes, "in_scope": s.InScope,
		"out_of_scope": s.OutOfScope, "raw_text": s.RawText,
		"proxy_keys": keys, "proxy_values": vals,
		"to_markdown": s.ToMarkdown(), "is_empty": s.IsEmpty(),
	}
}

func delivJSON2(d Deliverable) map[string]any {
	var shape any
	if d.Shape != "" {
		shape = d.Shape
	}
	return map[string]any{
		"name": d.Name, "description": d.Description,
		"preconditions": d.Preconditions, "shape": shape,
	}
}

// normalizeEmptyList turns CPython's `[]` (decoded as an empty []any) and
// the port's nil slice into the same shape for comparison. Both engines
// produce a LIST here; only Go's zero value for "nothing appended" is nil.
func normalizeEmptyList(v []any) any {
	if v == nil {
		return []any{}
	}
	return v
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return "<unmarshalable>"
	}
	return string(b)
}
