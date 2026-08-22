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
	"math"
	"regexp"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
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
var fileOutputRe = regexp.MustCompile(`(?i)(\bartifacts?/|` +
	`\b(?:save|write|output|export)\b[^.;` + "\n" + `]{0,40}\bto\s+\S*[\w-]+\.[a-z]{1,6}\b|` +
	`\bto\s+(?:a\s+)?file\b|` +
	`\bas\s+(?:its\s+own\s+)?(?:markdown|csv|json|yaml|text)\s+files?\b)`)

// RequiresFileOutput is exported for the NOW lane's own honesty checks.
func RequiresFileOutput(message string) bool {
	return fileOutputRe.MatchString(message)
}

// Classify routes one message. A nil adapter (or dryRun) uses the
// heuristic path only, Python parity.
func Classify(ctx context.Context, a llm.Adapter, message string, dryRun bool) Result {
	cfg, _ := config.Load()
	var r Result
	if dryRun || a == nil {
		lane, conf, reason := heuristicClassify(message)
		r = Result{Lane: lane, Confidence: conf, Reason: reason}
	} else {
		var ok bool
		r, ok = llmClassify(ctx, a, message)
		if !ok {
			lane, conf, reason := heuristicClassify(message)
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
		return strings.EqualFold(strings.TrimSpace(t), "true")
	}
	return false
}

func llmClassify(ctx context.Context, a llm.Adapter, message string) (Result, bool) {
	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: classifySystem},
		{Role: "user", Content: "Request: " + message},
	}, llm.Options{MaxTokens: 128, Temperature: 0.1, Purpose: "routing"})
	if err != nil || resp == nil {
		return Result{}, false
	}
	r := Result{TokensIn: resp.TokensIn, TokensOut: resp.TokensOut}
	obj, jerr := jsonx.Object(resp.Content)
	if jerr != nil || obj == nil {
		return r, false
	}
	lane := "agenda"
	if s, ok := obj["lane"].(string); ok {
		if l := strings.ToLower(strings.TrimSpace(s)); l == "now" || l == "agenda" {
			lane = l
		}
	}
	// safe_float parity, the closure tranche's rule: numeric strings
	// coerce; non-finite refused; clamp [0,1]; default 0.7.
	conf := 0.7
	switch f := obj["confidence"].(type) {
	case float64:
		conf = f
	case string:
		if pf, perr := strconv.ParseFloat(strings.TrimSpace(f), 64); perr == nil &&
			!math.IsNaN(pf) && !math.IsInf(pf, 0) {
			conf = pf
		}
	}
	conf = math.Min(math.Max(conf, 0), 1)
	reason, _ := obj["reason"].(string)
	r.Lane = lane
	r.Confidence = conf
	r.Reason = reason
	r.NeedsLiveData = truthyField(obj["needs_live_data"])
	r.IntrospectsSelf = truthyField(obj["introspects_self"])
	return r, true
}

// --- heuristic fallback (Python regexes verbatim) -----------------------

var nowPatterns = []*regexp.Regexp{
	regexp.MustCompile(`\b(what|who|when|where|how much|how many)\b.{0,60}\?`),
	regexp.MustCompile(`\b(write a? (haiku|poem|joke|summary|headline|tweet|caption))\b`),
	regexp.MustCompile(`\b(translate|convert|format|calculate)\b`),
	regexp.MustCompile(`\b(summarize|tldr|give me a summary)\b`),
	regexp.MustCompile(`\b(quick(ly)?|fast|one-?line|brief)\b`),
}

var agendaPatterns = []*regexp.Regexp{
	regexp.MustCompile(`\b(research|investigate|analyze|study|explore)\b`),
	regexp.MustCompile(`\b(build|create|develop|implement|design|architect)\b`),
	regexp.MustCompile(`\b(report|analysis|strategy|plan|roadmap)\b`),
	regexp.MustCompile(`\b(monitor|track|watch|follow)\b`),
	regexp.MustCompile(`\b(compare|evaluate|benchmark|assess)\b`),
	regexp.MustCompile(`\b(deep (dive|research|analysis))\b`),
	regexp.MustCompile(`\b(step[- ]by[- ]step|multi[- ]step|phase)\b`),
	regexp.MustCompile(`\b(and then|first.*then|multiple|several)\b`),
}

// liveDataRe is the deliberately-narrow lexical approximation of
// needs_live_data for the no-LLM path ("what's the current/latest/
// today's …"). Named-place availability asks fall through to NOW here —
// an ACCEPTED residual of the fallback, confirmed by three independent
// reviewers 2026-07-12, not a gap to chase (the real signal is the LLM
// path's needs_live_data).
var liveDataRe = regexp.MustCompile(`(?i)\b(what('s| is) (the |a |an )?(current|latest|today'?s?))\b`)

const shortThreshold = 8 // words — very short messages tend to be NOW

func heuristicClassify(message string) (lane string, confidence float64, reason string) {
	msg := strings.ToLower(strings.TrimSpace(message))
	wordCount := len(strings.Fields(msg))

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
		cfg, _ := config.Load()
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
