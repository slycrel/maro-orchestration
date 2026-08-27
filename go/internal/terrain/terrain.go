// Package terrain ports src/terrain.py — run-scoped terrain memory, which
// remembers blocked hosts WITHIN a run so step N+1 does not rediscover step
// N's 403.
//
// The design notes in the Python docstring are the contract and hold here
// unchanged: deterministic (no LLM call), run-scoped (dies with the run),
// observation only (nothing changes step status or routing), and
// conservative by construction — only hard, unambiguous blocks count,
// because a false "blocked" belief suppresses a source that actually works.
//
// # What is different in Go, and why
//
//   - **The accumulator is a pointer.** Python's `TerrainMemory` is a
//     dataclass held by reference on LoopContext, and every contributor
//     mutates the SAME object. A Go value type would be copied at each
//     assignment and half the observations would land on a dead copy, so
//     the constructor returns *TerrainMemory and nothing here takes a value
//     receiver that mutates.
//
//   - **The fact map keeps its insertion order.** Python's dict does, and
//     `promotable()` iterates `self.facts.values()` — so the order in which
//     hosts were first observed is observable in the promotion evidence. A
//     bare Go map would randomise it.
//
//   - **Every regex is rebuilt, not transcribed.** Python's `\s` and `\b`
//     are Unicode and Go's are ASCII, and `re.IGNORECASE` folds the Turkish
//     dotless i onto ASCII `i` where Go's `(?i)` does not. Six of the nine
//     block signals carry a literal `i`. See blockSignals.
package terrain

import (
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyurl"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// blockSignal is one entry of Python's `_BLOCK_SIGNALS` tuple.
type blockSignal struct {
	pattern *regexp.Regexp
	reason  string
}

// pat compiles one ported `re.compile(..., re.I)` pattern.
//
// PyFoldI is applied to EVERY pattern here, not only the ones that need it
// today. Six of the nine carry a literal `i` (forbidden, unauthorized,
// limit, attention required, action, required/continue, disallow) and would
// silently match a different language than the Python without it; the other
// three are unaffected, so the wrapper is a no-op for them. Applying it
// uniformly means a later edit that introduces an `i` cannot quietly become
// the seventh — and the pytext fold census, whose predicate IS PyFoldI,
// agrees with that by construction.
//
// The `\b` in the three status-code patterns is rendered with
// pytext.WordStart/WordEnd, which CONSUME the boundary character. That is
// safe here and only here because every one of these regexes is used as a
// BOOLEAN `search()` — scan_tool_events asks whether the pattern matched,
// never where or what. WordStart/WordEnd would be wrong if any caller took
// offsets or the matched text (see pytext.WordStart's rule).
func pat(p string) *regexp.Regexp { return regexp.MustCompile(pytext.PyFoldI(p)) }

// blockSignals is Python `_BLOCK_SIGNALS`, in order. Each maps a signal to
// the short reason rendered to later steps.
//
// Hard blocks only. Transient failures (500/502/503, timeouts, connection
// resets) are deliberately absent: they warrant a retry, and recording them
// as terrain would teach the run to abandon a working source.
//
// ORDER IS BEHAVIOUR. scan_tool_events breaks at the FIRST pattern that
// matches, so a blob that is both a 403 and a cloudflare challenge is
// recorded as "403 forbidden".
var blockSignals = []blockSignal{
	{pat(`(?i)(?:` + pytext.WordStart + `403` + pytext.WordEnd + `)|forbidden`), "403 forbidden"},
	{pat(`(?i)(?:` + pytext.WordStart + `401` + pytext.WordEnd + `)|unauthorized`), "401 unauthorized"},
	{pat(`(?i)(?:` + pytext.WordStart + `429` + pytext.WordEnd + `)|rate.?limit|too many requests`), "429 rate-limited"},
	{pat(`(?i)cloudflare|just a moment|attention required`), "cloudflare challenge"},
	// AWS WAF answers a bot challenge with HTTP *202* and an empty body — a
	// 2xx, so nothing above sees it. Anchored to the BLOCKING values on
	// purpose: the header name alone also appears in
	// `access-control-expose-headers: x-amzn-waf-action` on *successful*
	// responses, and `x-amzn-waf-action: allow` is a pass — matching the bare
	// name would mark every AWS-fronted host blocked. Bare `202` is never
	// matched either; 202 Accepted is a legitimate success.
	{pat(`(?i)x-amzn-waf-action:` + pytext.SpaceClass + `*(?:challenge|captcha|block)`),
		"aws waf challenge"},
	{pat(`(?i)(?:` + pytext.WordStart + `451` + pytext.WordEnd + `)`), "451 legally unavailable"},
	{pat(`(?i)quota (?:exceeded|exhausted)|out of quota`), "quota exhausted"},
	{pat(`(?i)paywall|subscription required|log ?in to continue`), "paywalled"},
	{pat(`(?i)robots\.txt disallow|blocked by robots`), "robots.txt disallow"},
}

// urlRe is Python `_URL_RE`. The `\s` inside the negated class is Python's
// Unicode whitespace, which is 29 code points to Go's 5 — a URL followed by
// a NO-BREAK SPACE ends at the space in CPython and swallows it in a
// transcribed Go pattern, which changes the parsed HOST.
var urlRe = regexp.MustCompile(`(?i)https?://` + pytext.NotClass(`"'<>)]}`) + `+`)

// MaxRenderedHosts is Python MAX_RENDERED_HOSTS — cap the rendered block:
// terrain is a hint, not a wall of text.
const MaxRenderedHosts = 12

// TerrainFact is one host observed to hard-block, with the evidence that
// says so.
//
// Hits defaults to 1 and Steps to a FRESH list per instance
// (`field(default_factory=list)`); NewFact is the only constructor that
// gets both right, and a bare `TerrainFact{}` has Hits 0, which no Python
// instance ever has.
type TerrainFact struct {
	Host      string
	Reason    string
	FirstStep int
	Hits      int
	Steps     []int
}

// NewFact builds a fact the way Python's dataclass does at the one call
// site that constructs one: hits=1 (the field default) and steps seeded
// with the observing step.
func NewFact(host, reason string, firstStep int, steps []int) *TerrainFact {
	return &TerrainFact{Host: host, Reason: reason, FirstStep: firstStep,
		Hits: 1, Steps: append([]int(nil), steps...)}
}

// TerrainMemory is Python's run-scoped accumulator. It lives on
// LoopContext and dies with the run.
type TerrainMemory struct {
	facts map[string]*TerrainFact
	// order is the dict's insertion order, which Promotable exposes.
	order []string
}

// New is Python `TerrainMemory()` — and, through looptypes, the
// `_new_terrain()` default factory. The map is allocated here rather than
// lazily so a zero-value *TerrainMemory can never be mistaken for a usable
// one.
func New() *TerrainMemory {
	return &TerrainMemory{facts: map[string]*TerrainFact{}}
}

// Observe records one hard block. It reports whether this host is NEWLY
// blocked.
//
// First reason wins: a host that 403s then later shows a cloudflare
// challenge is still "the first thing that blocked us", which is the more
// actionable fact.
//
// Note what increments and when, because the sibling ledger in
// internal/worldfacts deliberately does the opposite: `hits` goes up on
// EVERY repeat observation here, including several within one step, while
// `steps` only gains distinct step indices. So a host hit five times in
// step 3 renders "5× since step 3" and is still not promotable.
func (m *TerrainMemory) Observe(host, reason string, stepIdx int) bool {
	// pytext.Strip and pytext.Lower, not strings.TrimSpace/ToLower: Python
	// strips 29 whitespace code points (U+001C..U+001F among them, which Go
	// does not) and str.lower() is full case mapping, where U+0130 lowers to
	// TWO code points. A host key that differs between the runtimes splits
	// one blocked host into two ledger rows.
	host = pytext.Lower(pytext.Strip(host))
	if host == "" {
		return false
	}
	existing := m.facts[host]
	if existing == nil {
		m.facts[host] = NewFact(host, reason, stepIdx, []int{stepIdx})
		m.order = append(m.order, host)
		return true
	}
	existing.Hits++
	if !containsInt(existing.Steps, stepIdx) {
		existing.Steps = append(existing.Steps, stepIdx)
	}
	return false
}

func containsInt(xs []int, n int) bool {
	for _, x := range xs {
		if x == n {
			return true
		}
	}
	return false
}

// Facts returns the accumulated facts in Python's dict INSERTION order —
// the order hosts were first observed. Callers that want the rendered
// ranking want Render or the sort inside it, not this.
func (m *TerrainMemory) Facts() []*TerrainFact {
	out := make([]*TerrainFact, 0, len(m.order))
	for _, h := range m.order {
		out = append(out, m.facts[h])
	}
	return out
}

// Fact looks one host up by its normalised key.
func (m *TerrainMemory) Fact(host string) (*TerrainFact, bool) {
	f, ok := m.facts[host]
	return f, ok
}

// Len is `len(memory.facts)`.
func (m *TerrainMemory) Len() int { return len(m.facts) }

// Render emits one advisory line per blocked host, or "" when nothing is
// known.
//
// The empty render is load-bearing: the ContributionLedger's hard contract
// is that zero contributions leave prompts byte-identical, so a
// TerrainMemory with no facts must produce no bytes at all — not a header
// with nothing under it.
func (m *TerrainMemory) Render() string {
	if len(m.facts) == 0 {
		return ""
	}
	rows := m.Facts()
	// sorted() is STABLE and the key is (-hits, host). Host is the dict key
	// so it is unique and the tiebreak never fires — but SliceStable is used
	// anyway, because the day a caller reaches in and adds a second row for
	// one host is not the day to discover that Go's sort is not stable.
	sort.SliceStable(rows, func(i, j int) bool {
		if rows[i].Hits != rows[j].Hits {
			return rows[i].Hits > rows[j].Hits
		}
		// Python compares str by CODE POINT and Go compares by BYTE; UTF-8
		// preserves code-point order, so the two agree for every string a Go
		// `string` can hold that is valid UTF-8.
		return rows[i].Host < rows[j].Host
	})
	lines := []string{
		"Known blocked from this box THIS RUN — do not retry these; " +
			"state the gap honestly instead:",
	}
	for _, f := range rows[:min(len(rows), MaxRenderedHosts)] {
		seen := strconv.Itoa(f.Hits) + "× since step " + strconv.Itoa(f.FirstStep)
		lines = append(lines, "  - "+f.Host+": "+f.Reason+" ("+seen+")")
	}
	if len(rows) > MaxRenderedHosts {
		lines = append(lines, "  …and "+
			strconv.Itoa(len(rows)-MaxRenderedHosts)+" more.")
	}
	return strings.Join(lines, "\n")
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// Promotable returns the facts corroborated across >= minSteps distinct
// steps, in dict insertion order.
//
// The evidence gate for a durable `terrain` teaching: one blocked response
// could be a hiccup; the same host blocking across separate steps is an
// environment fact. Python's default is minSteps=2; Go has no default
// arguments, so every caller states it.
func (m *TerrainMemory) Promotable(minSteps int) []*TerrainFact {
	var out []*TerrainFact
	for _, f := range m.Facts() {
		if len(f.Steps) >= minSteps {
			out = append(out, f)
		}
	}
	return out
}

// HostOf is Python `_host_of(text)`.
//
// `urlparse(...).hostname` already lowercases everything before a `%` zone
// id; the trailing `.lower()` is what also lowers the zone. Both halves are
// kept — dropping the second one is invisible for every host without a zone
// id, which is every host in the corpus that motivated the module.
//
// Only ValueError is caught in Python, and pyurl.Hostname returns exactly
// the ValueErrors CPython raises, so `err != nil` is that except-arm.
func HostOf(text string) string {
	m := urlRe.FindString(text)
	if m == "" {
		return ""
	}
	host, found, err := pyurl.Hostname(m)
	if err != nil {
		return ""
	}
	if !found {
		// `(None or "")` — hostname is None for an empty host, and the
		// empty string is what the caller's `if not host` then tests.
		return ""
	}
	return pytext.Lower(host)
}

// ScanToolEvents is Python `scan_tool_events(events, step_idx, memory)`:
// record hard blocks visible in one step's tool transcript.
//
// It returns the list of newly-blocked hosts (for logging) and never fails —
// terrain memory is an optimization, and a parse failure must not touch the
// step's outcome. Python's `except Exception: return newly` returns the
// PARTIAL list built so far rather than an empty one, so a transcript that
// goes bad halfway still teaches what it taught before that.
//
// events is Python's `Optional[Iterable[Any]]`; nil is None, and an empty
// slice is the falsy `[]` — both take the early return, which is why this
// takes a slice rather than a channel or an iterator.
func ScanToolEvents(events []any, stepIdx int, memory *TerrainMemory) []string {
	var newly []string
	if len(events) == 0 {
		return newly
	}
	for _, ev := range events {
		d, ok := asDict(ev)
		if !ok {
			continue
		}
		// The host comes from the REQUEST side (input), the failure from the
		// RESPONSE side (output/error) — a URL in an error string is not
		// reliably the URL that was requested.
		host := HostOf(pyStr(dictGet(d, "input", "")))
		if host == "" {
			continue
		}
		blob := pyStr(dictGet(d, "output", "")) + " " + pyStr(dictGet(d, "error", ""))
		if pyval.Truthy(dictGet(d, "is_error", nil)) {
			blob += " error"
		}
		for _, sig := range blockSignals {
			if sig.pattern.MatchString(blob) {
				if memory.Observe(host, sig.reason, stepIdx) {
					newly = append(newly, host+" ("+sig.reason+")")
				}
				break
			}
		}
	}
	return newly
}

// asDict is Python's `isinstance(ev, dict)`. Both spellings a decoded JSON
// value can take here are accepted: encoding/json produces map[string]any
// and this port's order-preserving reader produces pyval.Obj, and both are
// one dict on the Python side.
func asDict(v any) (map[string]any, bool) {
	switch t := v.(type) {
	case map[string]any:
		return t, true
	case pyval.Obj:
		out := make(map[string]any, len(t))
		for _, kv := range t {
			// A dict cannot hold a key twice and Python's later value wins.
			out[kv.Key] = kv.Val
		}
		return out, true
	}
	return nil, false
}

func dictGet(d map[string]any, key string, def any) any {
	if v, ok := d[key]; ok {
		return v
	}
	return def
}

// pyStr is Python's `str(v)` / `f"{v}"` for the value shapes a decoded tool
// event can hold.
//
// It is NOT pyval.Repr: `str("x")` is `x` and `repr("x")` is `'x'`, and the
// blob these build is matched against patterns, so the quotes would change
// what matches. For every non-string, `str` and `repr` agree on the shapes
// json produces, so Repr is the right delegate there.
func pyStr(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return pyval.Repr(v)
}
