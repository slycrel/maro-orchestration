package terrain

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

func srcDirTerrain(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// runProbe executes one python3 snippet against the real src/ tree and
// decodes its stdout as JSON into out.
func runProbe(t *testing.T, body string, args ...string) []byte {
	t.Helper()
	argv := append([]string{"-c",
		"import json,sys\nsys.path.insert(0, sys.argv[1])\nimport terrain\n" + body,
		srcDirTerrain(t)}, args...)
	out, err := exec.Command("python3", argv...).Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("the CPython probe could not run: %v", err)
	}
	return out
}

// ---------------------------------------------------------------------
// _host_of
// ---------------------------------------------------------------------

// The corpus is built from CODE POINTS where the subject is which
// separator a string carries: a fixture whose point is "that is a NO-BREAK
// SPACE, not a space" cannot be reviewed by looking at it.
var (
	nbsp  = string(rune(0x00A0)) // NO-BREAK SPACE — \s to Python, not to Go
	vtab  = string(rune(0x000B)) // LINE TABULATION — same split
	fs28  = string(rune(0x001C)) // FILE SEPARATOR — Python \s, Go not
	ypogr = string(rune(0x0345)) // COMBINING GREEK YPOGEGRAMMENI
)

var hostCorpus = []string{
	// The ordinary lanes, which already agree.
	"see https://example.com/path for details",
	"HTTPS://EXAMPLE.COM/PATH",
	"http://example.com",
	"no url here at all",
	"",
	"https://user:pw@example.com:8080/x",
	"https://EXAMPLE.COM/",   // .hostname lowercases
	"https://Example.Com:80", // ...with a port

	// THE `\s`-inside-a-negated-class divergence. _URL_RE is
	// `[^\s"'<>)\]}]+`, and Python's `\s` is 29 code points to Go's 5. A URL
	// followed by one of the other 24 ends there in CPython and SWALLOWS it
	// in a transcribed Go pattern — which changes the parsed HOST, not just
	// the matched span, because the swallowed text lands inside the netloc.
	"https://example.com" + nbsp + "and more",
	"https://example.com" + vtab + "and more",
	"https://example.com" + fs28 + "and more",
	"https://example.com and more", // the ASCII control: both stop here

	// ...and the same separators BEFORE the url, where both engines agree,
	// so the corpus carries the pair that isolates the position.
	"text" + nbsp + "https://example.com/",

	// The class-fold trap. `(?i)` over a NEGATED class: Go expands a
	// class's case-folds BEFORE negating, so folding pulls U+0345 into the
	// excluded set through its fold orbit with iota, while Python's re
	// never folds a class at all. The character has to be the one deciding
	// where the run STOPS.
	"https://example.com" + ypogr + "x",
	"https://example.com" + "x", // the control

	// The explicit terminators in the class, each on its own.
	`https://example.com"x`,
	"https://example.com'x",
	"https://example.com<x",
	"https://example.com>x",
	"https://example.com)x",
	"https://example.com]x",
	"https://example.com}x",

	// Bracketed IPv6, which _URL_RE can never deliver whole: `]` is in its
	// exclusion class, so the match stops INSIDE the brackets and urlparse
	// raises on the unbalanced remainder. Every row here answers "".
	//
	// They are kept as the ValueError path, and because they are the shape
	// that WOULD otherwise reach the outer .lower() in HostOf — the scope
	// zone is the one part of a hostname urllib leaves cased. That call is
	// recorded at its site as unobservable, and these rows are the
	// measurement behind the word "unobservable".
	"https://[fe80::1%25zİ]/x",
	"https://[fe80::1%zİ]/x",
	"https://[fe80::1%25zI]/x",
	"https://[fe80::1%25tESt]/x",
	"https://[::1]/x",

	// urlparse raising ValueError — _host_of catches it and returns "".
	"https://[not-an-ipv6]/x",
	"https://exa mple.com/x",
}

func TestHostOfMatchesCPython(t *testing.T) {
	in, err := json.Marshal(hostCorpus)
	if err != nil {
		t.Fatal(err)
	}
	out := runProbe(t,
		"print(json.dumps([terrain._host_of(s) for s in json.loads(sys.argv[2])]))",
		string(in))
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var found, empty, nonASCII int
	for i, s := range hostCorpus {
		got := HostOf(s)
		if got != want[i] {
			t.Errorf("the HOST parsed out of a tool transcript disagrees — "+
				"one runtime marks a site blocked and the other does not\n"+
				"  in %q\n  go %q\n  py %q", s, got, want[i])
		}
		if want[i] != "" {
			found++
			if !isASCII(s) {
				nonASCII++
			}
		} else {
			empty++
		}
	}
	if found == 0 || empty == 0 {
		t.Fatalf("corpus reaches only one answer: found=%d empty=%d", found, empty)
	}
	if nonASCII == 0 {
		t.Fatal("no MATCHING case carries a non-ASCII code point, so the " +
			"corpus cannot separate Python's `\\s` from Go's")
	}
}

func isASCII(s string) bool {
	for _, r := range s {
		if r > 127 {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------
// _BLOCK_SIGNALS
// ---------------------------------------------------------------------

// One row per signal, spelled so the NAMED pattern is the only one that can
// match — the L9 shape this port keeps repeating is fixturing the one
// alternative a finding happened to name. Each pattern carrying a foldable
// `i` gets its dotless twin AND its ASCII control, because
// re.IGNORECASE folds U+0130/U+0131 onto `i` and Go's `(?i)` does not.
var signalCorpus = []string{
	// 403: the \b arm and the word arm, separately.
	"got 403 back", "got forbidden back", "got 4030 back", "x403x",
	// 401 / unauthorized — `unauthorized` carries an i.
	"code 401 here", "unauthorized here", "unauthorızed here",
	// 429 / rate limit / too many requests. `rate.?limit` has an i, and the
	// `.?` is one optional ANY character.
	"code 429 here", "rate limit hit", "rate-limit hit", "ratelimit hit",
	"rate_limit hit", "rate  limit hit", "rate lımit hit",
	"too many requests",
	// cloudflare / just a moment / attention required — `required` has an i.
	"cloudflare says no", "just a moment please", "attention required",
	"attention requıred", "ATTENTION REQUİRED",
	// AWS WAF. `action` has an i, and the `\s*` is Python whitespace.
	"x-amzn-waf-action: challenge", "x-amzn-waf-action:challenge",
	"x-amzn-waf-action:" + nbsp + "challenge",
	"x-amzn-waf-action:" + fs28 + "challenge",
	"x-amzn-waf-actıon: challenge",
	"x-amzn-waf-action: allow",                         // a PASS
	"access-control-expose-headers: x-amzn-waf-action", // also a pass
	"x-amzn-waf-action: captcha", "x-amzn-waf-action: block",
	// 451
	"code 451 here", "x451x",
	// quota
	"quota exceeded", "quota exhausted", "out of quota", "quota",
	// paywall / subscription required / log in to continue — two carry an i.
	"paywall ahead", "subscription required", "subscrıption required",
	"log in to continue", "login to continue", "log ın to continue",
	// robots — `disallow` carries an i.
	"robots.txt disallow", "robots_txt disallow", "blocked by robots",
	"robots.txt dısallow",
	// ORDER: a blob that is both a 403 and a cloudflare challenge must be
	// recorded as the FIRST match, not the most specific one.
	"403 cloudflare just a moment",
	"cloudflare 403",
	"429 rate limit and 403 forbidden",
	// Nothing at all, and the transient failures that must NOT count.
	"500 internal server error", "timed out", "connection reset",
	"202 accepted", "", "all fine",
}

func TestBlockSignalsMatchCPython(t *testing.T) {
	in, err := json.Marshal(signalCorpus)
	if err != nil {
		t.Fatal(err)
	}
	// The probe returns, per blob, the INDEX of the first matching signal
	// and its reason — the same break-at-first that scan_tool_events does,
	// so the order is part of what is compared and not just the set.
	out := runProbe(t, ""+
		"res=[]\n"+
		"for blob in json.loads(sys.argv[2]):\n"+
		"    hit=[-1,'']\n"+
		"    for i,(pat,reason) in enumerate(terrain._BLOCK_SIGNALS):\n"+
		"        if pat.search(blob):\n"+
		"            hit=[i,reason]; break\n"+
		"    res.append(hit)\n"+
		"print(json.dumps(res))",
		string(in))
	var want [][]any
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if len(want) != len(signalCorpus) {
		t.Fatalf("probe returned %d rows for %d blobs", len(want), len(signalCorpus))
	}

	var hits, misses, reasons = 0, 0, map[string]bool{}
	for i, blob := range signalCorpus {
		gotIdx, gotReason := -1, ""
		for j, sig := range blockSignals {
			if sig.pattern.MatchString(blob) {
				gotIdx, gotReason = j, sig.reason
				break
			}
		}
		wantIdx := int(want[i][0].(float64))
		wantReason := want[i][1].(string)
		if gotIdx != wantIdx || gotReason != wantReason {
			t.Errorf("the FIRST block signal to match disagrees — one runtime "+
				"marks the host blocked (or blames a different cause) and the "+
				"other does not\n  in %q\n  go [%d %q]\n  py [%d %q]",
				blob, gotIdx, gotReason, wantIdx, wantReason)
		}
		if wantIdx >= 0 {
			hits++
			reasons[wantReason] = true
		} else {
			misses++
		}
	}
	if hits == 0 || misses == 0 {
		t.Fatalf("corpus reaches only one answer: hits=%d misses=%d", hits, misses)
	}
	// Every signal must be REACHED. A corpus that exercises three of nine
	// patterns cannot notice a transcription error in the other six, and
	// the count is asserted rather than trusted because rows get deleted.
	if len(reasons) != len(blockSignals) {
		var missing []string
		for _, sig := range blockSignals {
			if !reasons[sig.reason] {
				missing = append(missing, sig.reason)
			}
		}
		t.Errorf("only %d of %d signals are reached by the corpus; never "+
			"matched: %s", len(reasons), len(blockSignals),
			strings.Join(missing, ", "))
	}
	// ...and the fold half specifically: at least one MATCHING row must
	// carry a character the two engines' IGNORECASE disagree about, or the
	// PyFoldI wrappers in blockSignals are untested here.
	var foldRows int
	for i, blob := range signalCorpus {
		if int(want[i][0].(float64)) >= 0 && pytext.Lower(blob) != strings.ToLower(blob) {
			foldRows++
		}
	}
	if foldRows == 0 {
		t.Fatal("no MATCHING row carries a Turkish i, so the corpus cannot " +
			"tell re.IGNORECASE from Go's (?i)")
	}
}

// ---------------------------------------------------------------------
// TerrainMemory: observe / render / promotable
// ---------------------------------------------------------------------

type obs struct {
	Host string `json:"host"`
	Why  string `json:"reason"`
	Step int    `json:"step"`
}

type scenario struct {
	name string
	ops  []obs
}

// Each scenario is a whole run's worth of observations, compared on ALL
// four observable surfaces at once — the per-call return, the rendered
// advisory, promotable at two thresholds, and the count. Comparing them
// together is the point: `hits` and `steps` move on different rules, and a
// port that gets one right and the other wrong renders correctly while
// promoting the wrong facts.
var scenarios = []scenario{
	{"nothing observed", nil},
	{"one host once", []obs{{"a.com", "403 forbidden", 1}}},
	{"one host, same step twice — hits 2, steps 1, NOT promotable",
		[]obs{{"a.com", "403 forbidden", 1}, {"a.com", "403 forbidden", 1}}},
	{"one host, two steps — promotable",
		[]obs{{"a.com", "403 forbidden", 1}, {"a.com", "403 forbidden", 2}}},
	{"first reason wins",
		[]obs{{"a.com", "403 forbidden", 1}, {"a.com", "cloudflare challenge", 2}}},
	{"steps out of order stay in ARRIVAL order",
		[]obs{{"a.com", "r", 5}, {"a.com", "r", 2}, {"a.com", "r", 5}, {"a.com", "r", 9}}},

	// The render SORT is (-hits, host). Both keys need a tie to be tested.
	{"sorted by hits descending", []obs{
		{"a.com", "r", 1}, {"b.com", "r", 1}, {"b.com", "r", 2}, {"b.com", "r", 3}}},
	{"ties on hits fall back to host", []obs{
		{"z.com", "r", 1}, {"m.com", "r", 1}, {"a.com", "r", 1}}},
	// ...and the tie-break with non-ASCII hosts, where CPython compares
	// CODE POINTS and Go compares BYTES. UTF-8 makes those the same order,
	// which is a fact about the encoding and not an accident worth
	// assuming — so it is measured.
	{"non-ASCII hosts in the tie-break", []obs{
		{"é.com", "r", 1}, {"z.com", "r", 1}, {"ä.com", "r", 1}, {"研究.com", "r", 1}}},

	// The host normalisation: strip() then lower(), Python's versions of
	// both. U+001C is whitespace to Python's strip and not to Go's
	// TrimSpace; "İ" lowercases to TWO code points.
	{"host is stripped and lowered", []obs{
		{"  A.COM  ", "r", 1}, {"a.com", "r", 2}}},
	{"stripped with Python-only whitespace", []obs{
		{fs28 + "a.com" + vtab, "r", 1}, {"a.com", "r", 2}}},
	{"a dotted capital I in the host", []obs{
		{"İ.com", "r", 1}, {"i̇.com", "r", 2}}},
	{"a dotless i in the host is NOT the same host", []obs{
		{"ı.com", "r", 1}, {"i.com", "r", 2}}},
	{"empty and whitespace-only hosts are refused", []obs{
		{"", "r", 1}, {"   ", "r", 1}, {fs28, "r", 1}, {"a.com", "r", 1}}},

	// The MAX_RENDERED_HOSTS cap, either side of it, and the "…and N more."
	// line — which is a different string from the row it replaces.
	{"exactly the cap", manyHosts(12)},
	{"one over the cap", manyHosts(13)},
	{"well over the cap", manyHosts(30)},
}

func manyHosts(n int) []obs {
	var out []obs
	for i := 0; i < n; i++ {
		// Distinct hit counts so the sort is total and the cap always drops
		// the SAME hosts in both engines — a cap tested under a tie would
		// compare two orderings that are both correct.
		host := string(rune('a'+i%26)) + itoaT(i) + ".com"
		for h := 0; h <= i; h++ {
			out = append(out, obs{host, "r", h})
		}
	}
	return out
}

func itoaT(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

type memResult struct {
	Newly       []bool   `json:"newly"`
	Render      string   `json:"render"`
	Len         int      `json:"len"`
	Promotable1 []string `json:"promotable1"`
	Promotable2 []string `json:"promotable2"`
	Promotable3 []string `json:"promotable3"`
	Facts       [][]any  `json:"facts"`
}

func TestTerrainMemoryMatchesCPython(t *testing.T) {
	type payload struct {
		Name string `json:"name"`
		Ops  []obs  `json:"ops"`
	}
	var in []payload
	for _, s := range scenarios {
		in = append(in, payload{s.name, s.ops})
	}
	blob, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}

	out := runProbe(t, ""+
		"res=[]\n"+
		"for sc in json.loads(sys.argv[2]):\n"+
		"    m = terrain.TerrainMemory()\n"+
		"    newly = [m.observe(o['host'], o['reason'], o['step']) for o in (sc['ops'] or [])]\n"+
		"    facts = [[f.host, f.reason, f.first_step, f.hits, f.steps]\n"+
		"             for f in m.facts.values()]\n"+
		"    res.append({\n"+
		"        'newly': newly,\n"+
		"        'render': m.render(),\n"+
		"        'len': len(m.facts),\n"+
		"        'promotable1': [f.host for f in m.promotable(1)],\n"+
		"        'promotable2': [f.host for f in m.promotable(2)],\n"+
		"        'promotable3': [f.host for f in m.promotable(3)],\n"+
		"        'facts': facts})\n"+
		"print(json.dumps(res))",
		string(blob))
	var want []memResult
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if len(want) != len(scenarios) {
		t.Fatalf("probe returned %d scenarios for %d", len(want), len(scenarios))
	}

	var rendered, blank, capped int
	for i, sc := range scenarios {
		m := New()
		var newly []bool
		for _, o := range sc.ops {
			newly = append(newly, m.Observe(o.Host, o.Why, o.Step))
		}
		w := want[i]

		for j := range sc.ops {
			if newly[j] != w.Newly[j] {
				t.Errorf("[%s] op %d (%q): NEWLY-blocked differs — this is the "+
					"value scan_tool_events logs\n  go %v\n  py %v",
					sc.name, j, sc.ops[j].Host, newly[j], w.Newly[j])
			}
		}
		if got := m.Render(); got != w.Render {
			t.Errorf("[%s] the rendered advisory is not byte-identical — it "+
				"goes into a PROMPT\n  go %q\n  py %q", sc.name, got, w.Render)
		}
		if got := m.Len(); got != w.Len {
			t.Errorf("[%s] fact count: go %d py %d", sc.name, got, w.Len)
		}
		// Promotable returns dict.values() order in CPython, which is
		// INSERTION order — not sorted, not the render order. Compared as a
		// sequence on purpose.
		for k, wantList := range [][]string{w.Promotable1, w.Promotable2, w.Promotable3} {
			var got []string
			for _, f := range m.Promotable(k + 1) {
				got = append(got, f.Host)
			}
			if !sameSeq(got, wantList) {
				t.Errorf("[%s] promotable(%d) — the evidence gate for a DURABLE "+
					"terrain teaching\n  go %v\n  py %v", sc.name, k+1, got, wantList)
			}
		}
		// Facts() is the same insertion order, with every field.
		gotFacts := m.Facts()
		if len(gotFacts) != len(w.Facts) {
			t.Errorf("[%s] Facts() length: go %d py %d", sc.name, len(gotFacts), len(w.Facts))
		} else {
			for j, f := range gotFacts {
				wf := w.Facts[j]
				var wSteps []int
				for _, s := range wf[4].([]any) {
					wSteps = append(wSteps, int(s.(float64)))
				}
				if f.Host != wf[0].(string) || f.Reason != wf[1].(string) ||
					f.FirstStep != int(wf[2].(float64)) || f.Hits != int(wf[3].(float64)) ||
					!sameInts(f.Steps, wSteps) {
					t.Errorf("[%s] fact %d\n  go %+v\n  py %v", sc.name, j, *f, wf)
				}
			}
		}

		if w.Render == "" {
			blank++
		} else {
			rendered++
		}
		if strings.Contains(w.Render, "more.") {
			capped++
		}
	}

	// Vacuity floors. Each names the class it protects, because a corpus
	// that reaches only one of these passes every assertion above.
	if rendered == 0 || blank == 0 {
		t.Fatalf("scenarios reach only one render answer: rendered=%d blank=%d",
			rendered, blank)
	}
	if capped == 0 {
		t.Fatal("no scenario exceeds MAX_RENDERED_HOSTS, so the cap and its " +
			"'…and N more.' line are untested")
	}
}

func sameSeq(a, b []string) bool {
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

func sameInts(a, b []int) bool {
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

// ---------------------------------------------------------------------
// scan_tool_events
// ---------------------------------------------------------------------

// The events arrive as decoded JSON, so a field can be any type, and
// CPython runs `str()` over it before the URL regex ever sees it. That is a
// whole divergence surface of its own — `str(None)` is "None", `str(True)`
// is "True", `str([1,2])` is "[1, 2]" with a space CPython puts there and a
// naive Go join does not — and a URL sitting inside one of those is a HOST
// that gets marked blocked in one runtime and not the other.
//
// Written as raw JSON so the fixtures say exactly what crosses the boundary
// rather than what a Go literal makes of it.
const eventScenarios = `[
  {"name": "no events at all",       "events": null},
  {"name": "empty list",             "events": []},
  {"name": "a non-dict event",       "events": ["https://a.com 403", 42, null]},
  {"name": "the ordinary case",      "events": [
      {"input": "https://a.com/x", "output": "403 forbidden"}]},
  {"name": "host from INPUT, not from the error text", "events": [
      {"input": "https://a.com/x", "error": "https://b.com refused: 403"}]},
  {"name": "no url in input",        "events": [
      {"input": "a.com", "output": "403 forbidden"}]},
  {"name": "no signal in the blob",  "events": [
      {"input": "https://a.com/x", "output": "200 ok"}]},
  {"name": "is_error appends the word 'error'", "events": [
      {"input": "https://a.com/x", "output": "", "is_error": true}]},
  {"name": "is_error truthiness: falsy values do NOT append", "events": [
      {"input": "https://a.com/x", "output": "", "is_error": 0},
      {"input": "https://b.com/x", "output": "", "is_error": ""},
      {"input": "https://c.com/x", "output": "", "is_error": []},
      {"input": "https://d.com/x", "output": "", "is_error": {}},
      {"input": "https://e.com/x", "output": "", "is_error": null},
      {"input": "https://f.com/x", "output": "", "is_error": false}]},
  {"name": "is_error truthiness: truthy non-bools DO append", "events": [
      {"input": "https://a.com/x", "output": "", "is_error": 1},
      {"input": "https://b.com/x", "output": "", "is_error": "no"},
      {"input": "https://c.com/x", "output": "", "is_error": [0]},
      {"input": "https://d.com/x", "output": "", "is_error": {"k": 0}},
      {"input": "https://e.com/x", "output": "", "is_error": 0.5}]},
  {"name": "str() of a non-string input", "events": [
      {"input": ["https://a.com/x"], "output": "403"},
      {"input": {"url": "https://b.com/x"}, "output": "403"},
      {"input": null, "output": "403"},
      {"input": true, "output": "403"},
      {"input": 12, "output": "403"},
      {"input": 1.5, "output": "403"}]},
  {"name": "str() of a non-string OUTPUT carrying the signal", "events": [
      {"input": "https://a.com/x", "output": ["403 forbidden"]},
      {"input": "https://b.com/x", "output": 403},
      {"input": "https://c.com/x", "output": null},
      {"input": "https://d.com/x", "output": true}]},
  {"name": "the missing-key default is the empty string", "events": [
      {"input": "https://a.com/x"}]},
  {"name": "only the FIRST sighting is reported newly", "events": [
      {"input": "https://a.com/x", "output": "403"},
      {"input": "https://a.com/y", "output": "403"},
      {"input": "https://a.com/z", "output": "cloudflare"}]},
  {"name": "break at the first signal, not the most specific", "events": [
      {"input": "https://a.com/x", "output": "403 cloudflare just a moment"}]},
  {"name": "output and error are joined with ONE space", "events": [
      {"input": "https://a.com/x", "output": "40", "error": "3"},
      {"input": "https://b.com/x", "output": "rate", "error": "limit"}]},
  {"name": "several hosts in one step", "events": [
      {"input": "https://a.com/x", "output": "403"},
      {"input": "https://b.com/x", "output": "429"},
      {"input": "https://c.com/x", "output": "200 ok"}]}
]`

func TestScanToolEventsMatchesCPython(t *testing.T) {
	out := runProbe(t, ""+
		"res=[]\n"+
		"for sc in json.loads(sys.argv[2]):\n"+
		"    m = terrain.TerrainMemory()\n"+
		"    newly = terrain.scan_tool_events(sc['events'], 7, m)\n"+
		"    res.append({'newly': newly, 'render': m.render(),\n"+
		"                'hosts': [f.host for f in m.facts.values()]})\n"+
		"print(json.dumps(res))",
		eventScenarios)
	var want []struct {
		Newly  []string `json:"newly"`
		Render string   `json:"render"`
		Hosts  []string `json:"hosts"`
	}
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	// Decoded with the PORT's loader, not encoding/json, because that is
	// how JSON reaches this code in the port and the two differ where it
	// matters: encoding/json yields map[string]any and float64, so a
	// nested dict reprs as pyval's refusal sentinel and the integer 12
	// reprs as "12.0". CPython's str() renders the dict and writes "12",
	// and str() is what the URL regex reads. Decoding the fixtures the
	// wrong way made this test report a divergence the port does not have.
	raw, lerr := pyval.LoadsOrdered(eventScenarios)
	if lerr != nil {
		t.Fatal(lerr)
	}
	scsList, ok := raw.(pyval.List)
	if !ok {
		t.Fatalf("the scenario blob decoded as %T, not a list", raw)
	}
	if len(scsList) != len(want) {
		t.Fatalf("probe returned %d rows for %d scenarios", len(want), len(scsList))
	}

	var withNewly, withoutNewly int
	for i, item := range scsList {
		sc, ok := item.(pyval.Obj)
		if !ok {
			t.Fatalf("scenario %d decoded as %T, not an object", i, item)
		}
		nameV, _ := sc.Get("name")
		name, _ := nameV.(string)
		var events []any
		evV, _ := sc.Get("events")
		if lst, ok := evV.(pyval.List); ok {
			events = []any(lst)
		}
		m := New()
		got := ScanToolEvents(events, 7, m)
		if !sameSeq(got, want[i].Newly) {
			t.Errorf("[%s] the NEWLY-blocked list differs — this is what the "+
				"run logs, and its emptiness is what the caller branches on"+
				"\n  go %v\n  py %v", name, got, want[i].Newly)
		}
		var hosts []string
		for _, f := range m.Facts() {
			hosts = append(hosts, f.Host)
		}
		if !sameSeq(hosts, want[i].Hosts) {
			t.Errorf("[%s] the recorded hosts differ\n  go %v\n  py %v",
				name, hosts, want[i].Hosts)
		}
		if r := m.Render(); r != want[i].Render {
			t.Errorf("[%s] render after the scan\n  go %q\n  py %q",
				name, r, want[i].Render)
		}
		if len(want[i].Newly) > 0 {
			withNewly++
		} else {
			withoutNewly++
		}
	}
	if withNewly == 0 || withoutNewly == 0 {
		t.Fatalf("scenarios reach only one answer: newly=%d quiet=%d",
			withNewly, withoutNewly)
	}
}
