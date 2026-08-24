package closure

import (
	"regexp"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// Probe-modality classification, ported from closure_verify.py
// (_classify_probe_modality and friends). Returns one of: browser, ws,
// http, process, static. "static" is the residual — code inspection and
// compile-level checks that never touch the running artifact.
//
// Per-segment classification is Jeremy's 2026-07-16 call: the command is
// split on top-level shell operators and the most behavioral segment
// wins. The old whole-string pass let the static hints beat the process
// patterns, so the single most common probe idiom — `<run the artifact>
// && grep <its output>` — counted static even though it executed the
// deliverable end-to-end (Python run d2f4e2f4: {"static": 8} on a run
// that exercised its deliverable twice).

var modalityPatterns = []struct {
	label string
	re    *regexp.Regexp
}{
	{"browser", regexp.MustCompile(`(?i)\b(playwright|puppeteer|selenium|chromium|chrome --headless|firefox --headless)\b`)},
	{"ws", regexp.MustCompile(`(?i)\b(wscat|websocat|wss?://)\b`)},
	{"http", regexp.MustCompile(`(?i)\b(curl|wget|httpie|http [A-Z]+|https?://)\b`)},
	// "process" = runs a built binary or a script that likely exercises
	// the artifact without network. First char after `./` must be
	// alphanumeric/underscore/dash — rules out the go wildcard `./...`
	// (as in `go build ./...`), a package pattern, not a binary.
	// The three `\s`/`\S` here are pytext's, not Go's: this pattern
	// decides a check's MODALITY, which is recorded on every check_results
	// row and read by the behavioral-gap branch, so a command separated by
	// a non-breaking space classified differently on the two runtimes
	// (adversarial mission-r6, priced in the sibling sweep).
	{"process", regexp.MustCompile(`(?i)(^|[` + pytext.SpaceClassBody + `;&|])\./[A-Za-z0-9_-][A-Za-z0-9_./-]*|(^|[` +
		pytext.SpaceClassBody + `;&|])(go run|node |python[0-9.]* |timeout [0-9]+` +
		pytext.SpaceClass + `+` + pytext.NotClass("") + `+` + pytext.SpaceClass + `*&)`)},
}

// Test runners execute the artifact — classifying them "static" made
// Python's pass-audit fire its "nothing executed" refutation on
// genuinely-executed passes (adversarial review 2026-08-10 over there).
// Non-executing invocation forms (collect/list/no-run/dry-run) classify
// static — but ONLY for recognized runners: the flags are runner
// semantics, and a generic `python3 smoke.py --dry-run` still executes
// the program.
var testRunnerRe = regexp.MustCompile(`(?i)\b(pytest|go test|cargo test|(npm|pnpm|yarn) (run )?test|make test|tox)\b`)
var nonExecRunnerFlagsRe = regexp.MustCompile(`(?i)(^|` + pytext.SpaceClass +
	`)--?(no-run|collect-only|co|list-?tests?|dry-run|list)\b`)

var staticHintsRe = regexp.MustCompile(`(?i)\b(grep|rg|test -[efdrs]|cat|head|tail|wc -[lc]|ls |find |jq |go build|go vet|tsc --noEmit|ruff|flake8|mypy)\b`)

var modalityRank = map[string]int{"static": 0, "process": 1, "http": 2, "ws": 3, "browser": 4}

// SplitProbeSegments splits a shell command on top-level `&&`/`||`/`;`/
// `|` — quote-aware. Operators inside single/double quotes (or
// backslash-escaped) do not split: `grep -q 'a && ./x' file` must not
// shed a fake `./x` process segment. A single `&` (backgrounding, 2>&1)
// never splits. Subshell/group nesting is not tracked — a split inside
// `$( )` produces fragments that still classify individually, harmless
// for most-behavioral aggregation. Byte-for-byte the Python walker.
func SplitProbeSegments(cmd string) []string {
	var out []string
	var buf []rune
	quote := rune(0)
	escaped := false
	r := []rune(cmd)
	flush := func() {
		out = append(out, string(buf))
		buf = buf[:0]
	}
	for i := 0; i < len(r); {
		ch := r[i]
		if escaped {
			buf = append(buf, ch)
			escaped = false
			i++
			continue
		}
		if ch == '\\' && quote != '\'' {
			buf = append(buf, ch)
			escaped = true
			i++
			continue
		}
		if quote != 0 {
			if ch == quote {
				quote = 0
			}
			buf = append(buf, ch)
			i++
			continue
		}
		if ch == '\'' || ch == '"' {
			quote = ch
			buf = append(buf, ch)
			i++
			continue
		}
		if ch == '&' && i+1 < len(r) && r[i+1] == '&' {
			flush()
			i += 2
			continue
		}
		if ch == '|' {
			flush()
			if i+1 < len(r) && r[i+1] == '|' {
				i += 2
			} else {
				i++
			}
			continue
		}
		if ch == ';' {
			flush()
			i++
			continue
		}
		buf = append(buf, ch)
		i++
	}
	out = append(out, string(buf))
	trimmed := out[:0]
	for _, s := range out {
		if t := strings.TrimSpace(s); t != "" {
			trimmed = append(trimmed, t)
		}
	}
	return trimmed
}

// classifySegment classifies ONE shell segment (no top-level operators).
func classifySegment(seg string) string {
	if seg == "" {
		return "static"
	}
	// Browser / ws / http are the strongest behavioral signals — they win
	// even when mixed with static tools inside the segment.
	for _, p := range modalityPatterns[:3] {
		if p.re.MatchString(seg) {
			return p.label
		}
	}
	// Before checking "process", defer to explicit static hints: `go build
	// ./cmd/foo` otherwise matches "process" via `./cmd/...` even though
	// the verb is a compile-only check (hint-before-runner precedence,
	// kept from the original classifier design).
	if staticHintsRe.MatchString(seg) {
		return "static"
	}
	// Recognized test runners: executing forms are process, non-executing
	// forms (collect/list/no-run/dry-run) are static.
	if testRunnerRe.MatchString(seg) {
		if nonExecRunnerFlagsRe.MatchString(seg) {
			return "static"
		}
		return "process"
	}
	for _, p := range modalityPatterns[3:] {
		if p.re.MatchString(seg) {
			return p.label
		}
	}
	return "static"
}

// ClassifyProbeModality classifies a closure probe command by what it
// actually exercises; the most behavioral segment names the whole probe.
func ClassifyProbeModality(cmd string) string {
	if cmd == "" {
		return "static"
	}
	best := "static"
	for _, seg := range SplitProbeSegments(cmd) {
		label := classifySegment(seg)
		if modalityRank[label] > modalityRank[best] {
			best = label
		}
	}
	return best
}

// runtimeGapAdmissionRe — what the LLM says when it knows it didn't
// actually exercise the thing. Drawn from a real run's own closure
// summary ("Gap: runtime validation (server startup + browser
// connection) was not performed."). Matching against the LLM's OWN
// words, not an external taxonomy of goal types — the
// inference-not-prompting doctrine.
var runtimeGapAdmissionRe = regexp.MustCompile(`(?i)\b(runtime (validation|check|verification|test)|` +
	`(?:not|never|wasn'?t|weren'?t) (?:run|tested|performed|exercised|executed|verified|started|booted)|` +
	`no \w+(?:\s+\w+){0,3} (?:was |were )?(?:run|tested|performed|exercised|executed|verified|started|booted)|` +
	`unexercised runtime|no behavioral|no runtime probe|` +
	`browser connection (?:was )?not|server (?:startup|boot) (?:was )?not)\b`)

var behavioralModalities = []string{"http", "ws", "browser", "process"}

// DetectBehavioralGap is Signal 1 of Python's _detect_behavioral_gap_ex:
// the verdict claims complete=true, the LLM's own summary/gap text
// admits runtime behavior wasn't exercised, and the modality
// distribution shows zero behavioral probes — self-contradiction
// detection, reading what the system already generated. Returns the
// downgrade reason, or "" when no gap. Signals 2 and 3 (scope
// failure-mode hints, declared [shape: runtime] deliverables) need the
// scope and resolved-intent subsystems and are deliberately unported
// with them (see PORT.md).
func DetectBehavioralGap(complete bool, summary string, gaps []string, modalityDist map[string]int) string {
	if !complete {
		return ""
	}
	for _, m := range behavioralModalities {
		if modalityDist[m] > 0 {
			return ""
		}
	}
	combined := summary + "\n" + strings.Join(gaps, "\n")
	if m := runtimeGapAdmissionRe.FindString(combined); m != "" {
		return "LLM summary admits runtime was not exercised: " + strconvQuote(m)
	}
	return ""
}

// strconvQuote mirrors Python's %r-ish rendering used in the downgrade
// reason without importing strconv at every call site.
func strconvQuote(s string) string {
	return "'" + s + "'"
}
