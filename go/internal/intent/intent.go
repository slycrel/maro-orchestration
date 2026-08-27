// Package intent ports src/intent.py's routing half: classify an
// inbound goal as NOW (single tool-less LLM call, ~seconds) or AGENDA
// (decompose + loop, ~minutes). LLM classification with a heuristic
// keyword fallback, then CAPABILITY overrides that are mechanics, not
// opinions: a goal naming a file deliverable cannot be fulfilled by a
// lane that writes no files, and a live-data ask cannot be answered by
// a lane that fetches nothing.
//
// Ported lessons (each pinned):
//   - Capability overrides fire AFTER classification and win over it
//     (burn-in batch 3, 2026-07-02: a file-deliverable goal routed NOW,
//     answered inline, and the verdict correctly demoted the run —
//     honest negative, wrong lane).
//   - The live-data override is config-gated (now_lane.live_data_routing,
//     default ON) — flag OFF makes the path inert (DEFAULTS.md contract;
//     adversarial finding 2026-07-12: it once fired unconditionally).
//   - Boolean fields parse fail-OPEN to false with the is-true-or-"true"
//     shape (a sloppier model's "true" string is read correctly, and
//     anything else keeps today's behavior).
//   - Heuristic default is AGENDA 0.55 — won't miss work.
//
// Deliberately unported, NAMED (each returns with its subsystem):
//   - The link-triage NOW shortcut AND the URL exemption on the
//     live-data override. Both assume the NOW lane PRE-FETCHES carried
//     URLs (web_fetch enrichment, conversational-compute decree
//     2026-07-17). Go's NOW lane fetches nothing yet, so a carried URL
//     does not make a live-data ask answerable — honoring the exemption
//     here would route unanswerable asks to a lane that cannot answer
//     them. Go-stricter: live-data asks escalate regardless of URLs.
//   - check_goal_clarity + rewrite_imperative_goal (the clarification
//     gate) — needs the ask-the-user surface.
//   - introspects_self has no Go consumer yet (its Python consumer is
//     container-isolation routing); the field is carried so the stamp
//     is honest, and its consumer is named here.
package intent

import (
	"context"
	"errors"
	"math"
	"regexp"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Result is Python's ClassifyResult: named routing facts, read by
// field, never unpacked positionally.
type Result struct {
	Lane       string // "now" | "agenda"
	Confidence float64
	Reason     string
	// NeedsLiveData: a correct answer depends on live/local data the
	// NOW lane cannot fetch.
	NeedsLiveData bool
	// IntrospectsSelf: the goal asks about THIS system's own runs/
	// behavior/source (decree 2026-07-18). Fails open to false on the
	// heuristic path — isolation is the safe default. No Go consumer
	// yet (see package doc).
	IntrospectsSelf bool
	// Usage from the LLM classify call, for the caller's run totals.
	TokensIn, TokensOut int
}

const classifySystem = `You are a routing agent. Classify the user's request as either:

NOW: Completable in a single step with one LLM call. Examples:
- Factual questions ("what time is it?", "what does HTTP 429 mean?")
- Simple generation ("write a haiku", "summarize this paragraph")
- Short transforms ("translate this to Spanish")

AGENDA: Requires multiple steps, research, iteration, or planning. Examples:
- Research tasks ("research winning polymarket strategies")
- Build tasks ("build a research report on X")
- Analysis tasks ("analyze competitor pricing and recommend action")
- Ongoing projects ("set up monitoring for Y")
- Live-data lookups ("what is the current BTC price?", "gas stations near
  Manti, Utah") — these need needs_live_data=true (below); NOW cannot fetch
  live data, so a live-data ask is AGENDA even though it reads like a quick
  question.

Also decide needs_live_data: true when a correct answer requires information
that changes over time or is locally situated — current prices/availability/
hours, "near me"/named-place inventory, weather, schedules, recent events —
i.e. anything you cannot know reliably from training data. false otherwise.

Also decide introspects_self: true when the request asks about THIS system's
own behavior — its past runs, failures, decisions, logs, configuration, or
source code ("why did the last run fail?", "diagnose your step retries",
"what did you work on yesterday?", "audit your own planner"). false for
ordinary tasks about the outside world, even technical ones about other
software.

Respond ONLY with a JSON object:
{"lane": "now" or "agenda", "confidence": 0.0-1.0, "reason": "one sentence", "needs_live_data": true or false, "introspects_self": true or false}`

// The Python prompt carries a link-read EXCEPTION paragraph between the
// AGENDA examples and the needs_live_data instruction; it is omitted
// here on purpose — Go's NOW lane does not pre-fetch links, so teaching
// the classifier the exemption would misroute (see package doc).

// fileOutputRe ports _FILE_OUTPUT_RE: the goal explicitly asks for
// output on disk (path, artifacts/, "to a file").
//
// Every `\b`, `\s`, `\w` and `\S` here is rebuilt from pytext: Go's are
// ASCII and five-code-point where Python's are Unicode and 29. The `\S`
// joined that list on 2026-08-27 — this sentence named three escapes when
// the pattern had four, and an enumeration that is wrong at birth is not
// caught by keeping it up to date. Measured, the
// verbatim transcription missed `save the summary to café.md` — CPython
// forces lane="agenda" ("Names a file deliverable") and Go left it "now",
// which is a different execution path, not a different field
// (adversarial mission-r6 MEDIUM).
//
// The middle arm's two INTERIOR boundaries are folded into the {0,40}
// window with NotWordClassPlus, because a consuming WordEnd there ate the
// window's first character and broke "write to out.json" outright
// (adversarial mission-r7 HIGH -- r6's own fix, on its most ordinary
// input). See pytext.WordStart's doc for the translation.
var fileOutWindow = pytext.NotWordClassPlus(".;\n")

// PyFoldI: `artifacts` carries an `i`, and re.IGNORECASE also matches
// U+0130/U+0131 there while Go's (?i) does not. Measured both engines --
// CPython matches "save to art\u0131facts/x.md" and "SAVE TO
// ART\u0130FACTS/X.MD"; the port did not, which routes an
// artifact-producing goal to a different LANE.
var fileOutputRe = regexp.MustCompile(pytext.PyFoldI(`(?i)(` + pytext.WordStart + `artifacts?/|` +
	pytext.WordStart + `(?:save|write|output|export)` +
	`(?:` + fileOutWindow + `|` + fileOutWindow + `[^.;` + "\n" + `]{0,38}` +
	fileOutWindow + `)` + `to` + pytext.SpaceClass +
	// `(?-i:` because this pattern sets `(?i)` and a raw class spliced
	// from WordClassBody fold-grows by U+0345 — see pytext.WordClass.
	//
	// NotClass("") rather than `\S`, and it is the SAME finding as r6 one
	// escape to the left. Go's `\S` is the complement of five code points
	// where Python's is the complement of 29, so U+00A0 and U+000B are
	// NON-space to Go and it runs the greedy stem straight through them.
	// Measured, both engines: `write to a<U+00A0>b.md` is False in CPython
	// and was True here — Go forcing lane=agenda where CPython leaves it
	// now, which is r6's divergence in the opposite direction.
	//
	// r6 rebuilt every `\b`, `\s` and `\w` in this pattern and left the
	// one `\S`. The doc above enumerated the three it fixed, which is why
	// nothing caught it for two rounds; the census guard in
	// pytext/foldinvariance_test.go is what catches the next one.
	`+` + pytext.NotClass("") + `*(?-i:[` + pytext.WordClassBody + `-])+\.[a-z]{1,6}` + pytext.WordEnd + `|` +
	pytext.WordStart + `to` + pytext.SpaceClass + `+(?:a` + pytext.SpaceClass +
	`+)?file` + pytext.WordEnd + `|` +
	pytext.WordStart + `as` + pytext.SpaceClass + `+(?:its` + pytext.SpaceClass +
	`+own` + pytext.SpaceClass + `+)?(?:markdown|csv|json|yaml|text)` +
	pytext.SpaceClass + `+files?` + pytext.WordEnd + `)`))

// RequiresFileOutput is exported for the NOW lane's own honesty checks.
func RequiresFileOutput(message string) bool {
	return fileOutputRe.MatchString(message)
}

// Classify routes one message. A nil adapter (or dryRun) uses the
// heuristic path only, Python parity.
func Classify(ctx context.Context, a llm.Adapter, message string, dryRun bool) Result {
	// Loaded ONCE and threaded down — warnings already surfaced by the
	// CLI's own boundary Load; a second read inside the heuristic could
	// disagree with this one mid-classification.
	cfg, _ := config.Load()
	var r Result
	if dryRun || a == nil {
		lane, conf, reason := heuristicClassify(message, cfg)
		r = Result{Lane: lane, Confidence: conf, Reason: reason}
	} else {
		var ok bool
		r, ok = llmClassify(ctx, a, message)
		if !ok {
			lane, conf, reason := heuristicClassify(message, cfg)
			// Usage from the failed/unparseable call still counts.
			r = Result{Lane: lane, Confidence: conf, Reason: reason,
				TokensIn: r.TokensIn, TokensOut: r.TokensOut}
		}
	}

	// Capability override, not a classification opinion: the NOW lane
	// answers inline and cannot write files.
	if r.Lane == "now" && RequiresFileOutput(message) {
		r.Lane = "agenda"
		r.Confidence = math.Max(r.Confidence, 0.8)
		r.Reason = "Names a file deliverable — NOW lane cannot write files"
		return r
	}
	// Live-data override, config-gated. Go-stricter than Python: no URL
	// exemption, because nothing pre-fetches here (see package doc).
	if r.Lane == "now" && r.NeedsLiveData &&
		config.Get(cfg, "now_lane.live_data_routing", true) {
		r.Lane = "agenda"
		r.Confidence = math.Max(r.Confidence, 0.8)
		r.Reason = "Needs live external data — NOW lane cannot fetch it"
		return r
	}
	return r
}

// truthyField ports the fail-open bool shape: true, or a trimmed
// case-insensitive "true" string; everything else (absent, malformed,
// "false", numbers) is false — the no-override default.
func truthyField(v any) bool {
	switch t := v.(type) {
	case bool:
		return t
	case string:
		// Python is `raw.strip().lower() == "true"`, and str.strip()
		// covers U+001C..U+001F where strings.TrimSpace does not. A
		// `"needs_live_data": "true\u001c"` was true on CPython and
		// false here — and this field gates the live-data override and
		// the container-isolation decree, so the two runtimes executed
		// the same goal with different capabilities (mission-r5 LOW).
		return pytext.Lower(pytext.Strip(t)) == "true"
	}
	return false
}

func llmClassify(ctx context.Context, a llm.Adapter, message string) (Result, bool) {
	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: classifySystem},
		{Role: "user", Content: "Request: " + message},
	}, llm.Options{MaxTokens: 128, Temperature: 0.1, Purpose: "routing"})
	if err != nil || resp == nil {
		// A refused-but-billed classify call still spent tokens —
		// salvage them so the heuristic-fallback Result carries the
		// real cost (llm.ResultError doctrine; exec.go/loop.go do the
		// same on their error branches).
		var re *llm.ResultError
		if errors.As(err, &re) {
			return Result{TokensIn: re.TokensIn, TokensOut: re.TokensOut}, false
		}
		return Result{}, false
	}
	r := Result{TokensIn: resp.TokensIn, TokensOut: resp.TokensOut}
	obj, jerr := jsonx.Object(resp.Content)
	// len(obj) == 0, not obj == nil. Python guards on `if data:` and an
	// empty dict is FALSY, so `{}` falls through to _heuristic_classify;
	// jsonx.Object returns a non-nil empty map for the same input, and Go
	// took the well-formed-verdict path with lane="agenda" 0.7 where
	// CPython returns ('now', 0.65, ...). Same lane-flip blast radius as
	// the regex classes above (adversarial mission-r6 MEDIUM).
	// mission_dag.go:360 already carries the correct port of this exact
	// Python idiom — this site was its unswept sibling.
	if jerr != nil || len(obj) == 0 {
		return r, false
	}
	lane := "agenda"
	if s, ok := obj["lane"].(string); ok {
		// Python is safe_str(...).lower(), and safe_str strips the
		// 29-point set: `{"lane": "now\u001c"}` is "now" there and
		// "now\x1c" here, which matches neither arm and silently fell
		// back to "agenda" — the wrong lane from a well-formed verdict
		// (adversarial mission-r5 LOW).
		if l := pytext.Lower(pytext.Strip(s)); l == "now" || l == "agenda" {
			lane = l
		}
	}
	// safe_float parity, the closure tranche's rule: numeric strings
	// coerce; non-finite refused; clamp [0,1]; default 0.7.
	// One implementation, not a fourth hand-rolled one: this arm had the
	// same missing non-finite guard as closure.go's (mission-r5 HIGH).
	// It does not reach disk here — Result.Confidence only reaches a
	// terminal print — but two ports of one Python function drifting
	// apart is the defect regardless of which one is currently load-
	// bearing, and that is exactly how the r4 and r5 HIGHs both started.
	conf := pyval.SafeFloatUnit(obj["confidence"], 0.7)
	conf = math.Min(math.Max(conf, 0), 1)
	// safe_str, not a bare assertion: Python strips and coerces, so
	// {"reason": 42} is "42" there and "" here. Result.Reason is
	// consumed only by the terminal print today and cannot reach disk
	// — fixed anyway because "cannot reach disk" is a property of
	// today's callers, and the confidence field one line up carries
	// the same argument while being the r5 HIGH's twin (mission-r6 LOW).
	reason := pyval.SafeStr(obj["reason"], "")
	r.Lane = lane
	r.Confidence = conf
	r.Reason = reason
	r.NeedsLiveData = truthyField(obj["needs_live_data"])
	r.IntrospectsSelf = truthyField(obj["introspects_self"])
	return r, true
}

// --- heuristic fallback (Python regexes verbatim) -----------------------

// Every `\b` below is pytext.WordStart/WordEnd, not Go's ASCII `\b`.
// These decide the LANE, and Go's boundary fires between a non-ASCII
// letter and an ASCII keyword where Python's does not: measured,
// "研究plan for the week" is ('now', 0.65) in CPython and agenda 0.65
// here (adversarial mission-r6 MEDIUM). The `\s` inside "how much" etc.
// is a literal space in the Python source, so it stays a literal space.
var nowPatterns = []*regexp.Regexp{
	// The boundary after the keyword is INTERIOR -- `.{0,60}\?` follows it
	// -- so it is folded into the window rather than consumed. A consuming
	// WordEnd ate the `?` and "what?" stopped matching (mission-r7 HIGH).
	// The window is optional because an EMPTY one leaves `?` immediately
	// after the keyword, which is itself a word boundary.
	regexp.MustCompile(pytext.WordStart + `(what|who|when|where|how much|how many)` +
		`(?:` + pytext.NotWordClassPlus("\n") + `.{0,59})?\?`),
	regexp.MustCompile(pytext.WordStart +
		`(write a? (haiku|poem|joke|summary|headline|tweet|caption))` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(translate|convert|format|calculate)` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(summarize|tldr|give me a summary)` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(quick(ly)?|fast|one-?line|brief)` + pytext.WordEnd),
}

var agendaPatterns = []*regexp.Regexp{
	regexp.MustCompile(pytext.WordStart + `(research|investigate|analyze|study|explore)` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(build|create|develop|implement|design|architect)` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(report|analysis|strategy|plan|roadmap)` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(monitor|track|watch|follow)` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(compare|evaluate|benchmark|assess)` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(deep (dive|research|analysis))` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(step[- ]by[- ]step|multi[- ]step|phase)` + pytext.WordEnd),
	regexp.MustCompile(pytext.WordStart + `(and then|first.*then|multiple|several)` + pytext.WordEnd),
}

// liveDataRe is the deliberately-narrow lexical approximation of
// needs_live_data for the no-LLM path ("what's the current/latest/
// today's …"). Named-place availability asks fall through to NOW here —
// an ACCEPTED residual of the fallback, confirmed by three independent
// reviewers 2026-07-12, not a gap to chase (the real signal is the LLM
// path's needs_live_data).
// PyFoldI, for the ONE `i` in this pattern: the ` is` alternative.
// `current`, `latest` and `today` carry none, so "what \u0131s the
// current price" is the whole exposure and the apostrophe form is
// unaffected -- which is why the fixtures below drive ` is` and not
// `latest`.
var liveDataRe = regexp.MustCompile(pytext.PyFoldI(`(?i)` + pytext.WordStart +
	`(what('s| is) (the |a |an )?(current|latest|today'?s?))` + pytext.WordEnd))

const shortThreshold = 8 // words — very short messages tend to be NOW

func heuristicClassify(message string, cfg map[string]any) (lane string, confidence float64, reason string) {
	// pytext.Lower/Strip/Split, not the stdlib three. Python is
	// `message.lower().strip()` then `.split()`, and all three differ:
	// str.lower() maps U+0130 to two code points where strings.ToLower
	// maps it to one, str.strip() covers U+001C..U+001F, and str.split()
	// splits on 29 code points to strings.Fields' 25.
	//
	// This is not a field difference, it is a different EXECUTION LANE.
	// Measured end to end on "BUİLD a new dashboard" (U+0130):
	//
	//	CPython .lower() -> "bui̇ld a new dashboard"  -> agenda regex MISSES
	//	  -> agenda_score 0, 4 words <= 8 -> now_score 1 -> lane "now"
	//	Go      ToLower  -> "build a new dashboard"  -> agenda regex HITS
	//	  -> lane "agenda"
	//
	// One runtime writes a task_type:"now" outcome row; the other writes
	// an agenda run dir and a mission. Reachable whenever the LLM
	// classify call fails or returns unparseable JSON (adversarial
	// mission-r5 MEDIUM).
	msg := pytext.Strip(pytext.Lower(message))
	wordCount := len(pytext.Split(msg))

	nowScore, agendaScore := 0, 0
	for _, p := range nowPatterns {
		if p.MatchString(msg) {
			nowScore++
		}
	}
	for _, p := range agendaPatterns {
		if p.MatchString(msg) {
			agendaScore++
		}
	}
	if liveDataRe.MatchString(msg) {
		if config.Get(cfg, "now_lane.live_data_routing", true) {
			agendaScore++
		} else {
			nowScore++
		}
	}
	if wordCount <= shortThreshold && agendaScore == 0 {
		nowScore++
	}
	if nowScore > agendaScore {
		return "now", math.Min(0.5+float64(nowScore)*0.15, 0.9),
			"Short or simple request; single-call execution sufficient"
	}
	if agendaScore > 0 {
		return "agenda", math.Min(0.5+float64(agendaScore)*0.15, 0.9),
			"Multi-step or research task; loop execution required"
	}
	// Default: AGENDA is safer (won't miss work).
	return "agenda", 0.55, "Defaulting to AGENDA lane for thoroughness"
}
