package record

import (
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// Verdict-trust policy (Python memory_ledger.verdict_trust — VERIFY_LEARN_ARC
// §4): classify how much a run's goal-verdict may be trusted by learning
// consumers. The single policy function, consumed by the V2 cadence windows
// (internal/scans) and any future crystallization gate.
//
// Ported semantics, with ONE named divergence carried over from the
// inspector/evolver/recall twins (r3 fix, backport-candidate #9): a MALFORMED
// goal_achieved (present but not a bool) is graded judged-NOT-achieved, where
// fork-point Python's `is False` / `is None` checks let it read as unjudged.
// The safe direction for a trust policy is the same as for the fair-cap: a
// value someone wrote but nobody can read must not earn the trusted-good path.

const (
	// VerdictConfidenceFloor mirrors memory_ledger.VERDICT_CONFIDENCE_FLOOR.
	VerdictConfidenceFloor = 0.7

	VerdictTrustFull        = "full"
	VerdictTrustDirectional = "directional"
	VerdictTrustNeutral     = "neutral"
	VerdictTrustExcluded    = "excluded"
)

// GoalAchieved reads a row's goal_achieved with the hardened tri-state the
// port's readers share (inspector.triState/evolver.triState/recall):
// (judged, achieved). Absent or nil → unjudged. A bool → judged, its value.
// Any other type → judged, NOT achieved (malformed values never read good).
func GoalAchieved(row map[string]any) (judged, achieved bool) {
	v, present := row["goal_achieved"]
	if !present || v == nil {
		return false, false
	}
	if b, ok := v.(bool); ok {
		return true, b
	}
	return true, false
}

// VerdictTrust classifies one outcomes.jsonl row (ledger rehydration shape).
//
//	full        — judged, confidence >= floor (or no confidence attached:
//	              deterministic judges are authoritative), not env-capped.
//	directional — judged but an explicit confidence below the floor.
//	neutral     — verdict absent (not judged ≠ failed).
//	excluded    — closure_unverifiable: the verifier's own failure, excluded
//	              from ALL learning consumers.
func VerdictTrust(row map[string]any) string {
	if s, _ := row["goal_verdict_source"].(string); s == "closure_unverifiable" {
		return VerdictTrustExcluded
	}
	judged, _ := GoalAchieved(row)
	if !judged {
		return VerdictTrustNeutral
	}
	if conf, ok := coerceFloat(row["goal_verdict_confidence"]); ok && conf < VerdictConfidenceFloor {
		return VerdictTrustDirectional
	}
	return VerdictTrustFull
}

// coerceFloat accepts float64/int/json.Number-ish values the tolerant JSONL
// readers can produce, PLUS numeric strings — Python's `float(conf)` parses
// "0.5", and this is the single §4 policy site both runtimes consume, so a
// string-typed confidence must classify identically (r1 parity review: Go
// read "0.5" as unparseable → FULL where Python reads DIRECTIONAL). Only a
// NON-numeric value is (0, false) — that is Python's TypeError/ValueError
// pass, and an unreadable confidence never downgrades a judged verdict.
// Two more Python float() edges matched in r4 (both had flipped trust in
// the UNSAFE direction — malformed row earning the trusted path): bools
// coerce (float(False)=0.0 → directional), and out-of-range numeric
// strings coerce ("-1e999" → -inf → directional; Go's ParseFloat returns
// the same ±Inf/0 with ErrRange, which is a successful parse in Python's
// eyes, not an error).
func coerceFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case bool:
		if n {
			return 1, true
		}
		return 0, true
	case string:
		// Python's float() runs the text through
		// PyUnicode_TransformDecimalAndSpaceToASCII first, so EVERY Unicode
		// decimal digit is accepted. Measured on CPython 3.14.3: all 760 Nd
		// code points parse, 750 of them non-ASCII, and "٠.٥" is 0.5. Go's
		// ParseFloat takes ASCII only, so without this a confidence written
		// in Arabic-Indic digits was unparseable HERE and readable THERE —
		// and the failure direction is the unsafe one, because an
		// unparseable confidence never downgrades a judged verdict, so a
		// below-floor value read as FULL trust. (Adversarial r5, LOW; it
		// named 75 divergences, the measured figure is 750.)
		t := transformDecimals(strings.TrimSpace(n))
		// Go ParseFloat accepts hex floats ("0x1p-2"); Python float()
		// raises on them and classifies FULL via the error pass — reject
		// hex so both runtimes agree (r2 review LOW-1).
		low := strings.TrimLeft(strings.ToLower(t), "+-")
		if strings.HasPrefix(low, "0x") {
			return 0, false
		}
		f, err := strconv.ParseFloat(t, 64)
		if err != nil && errors.Is(err, strconv.ErrRange) {
			return f, true // over/underflow: Python float() yields ±inf / 0.0
		}
		return f, err == nil
	case interface{ Float64() (float64, error) }: // json.Number
		f, err := n.Float64()
		return f, err == nil
	}
	return 0, false
}

// coerceInt is Python's int() for the shapes YAML can produce, and it is
// NOT coerceFloat with a truncation bolted on: int("10.5") raises where
// float("10.5") does not, so a config value that would silently become 10
// under a float-then-truncate reading must instead fall back to the
// default the way Python's except branch does.
//
// Underscores are handled by hand because Go's ParseInt accepts them only
// in base 0, and base 0 would also accept "0x10" and "0o17", which Python's
// one-argument int() rejects.
func coerceInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		if n > math.MaxInt || n < math.MinInt {
			return 0, false
		}
		return int(n), true
	case float64:
		// Python's int(float) truncates toward zero and raises on nan/inf.
		if math.IsNaN(n) || math.IsInf(n, 0) {
			return 0, false
		}
		if n >= math.MaxInt || n <= math.MinInt {
			return 0, false // Python yields a big int; refusing beats wrapping
		}
		return int(math.Trunc(n)), true
	case bool:
		if n {
			return 1, true
		}
		return 0, true
	case string:
		// Same decimal fold as float(): int() runs the text through
		// PyUnicode_TransformDecimalAndSpaceToASCII too, so Arabic-Indic
		// digits parse here exactly as they do there.
		t := transformDecimals(strings.TrimSpace(n))
		if strings.Contains(t, "_") {
			// Python allows single underscores BETWEEN digits only.
			if strings.HasPrefix(t, "_") || strings.HasSuffix(t, "_") ||
				strings.Contains(t, "__") {
				return 0, false
			}
			body := strings.TrimLeft(t, "+-")
			if strings.HasPrefix(body, "_") {
				return 0, false
			}
			t = strings.ReplaceAll(t, "_", "")
		}
		i, err := strconv.ParseInt(t, 10, 64)
		if err != nil || i > math.MaxInt || i < math.MinInt {
			return 0, false
		}
		return int(i), true
	case interface{ Float64() (float64, error) }: // json.Number
		f, err := n.Float64()
		if err != nil || f != math.Trunc(f) {
			return 0, false
		}
		return int(f), true
	}
	return 0, false
}

// transformDecimals folds every Unicode decimal digit to its ASCII
// equivalent, which is what CPython's PyUnicode_TransformDecimalAndSpace-
// ToASCII does before float() ever sees the text. Leading/trailing spaces
// are already handled by strings.TrimSpace at the call site: Go's
// unicode.White_Space set and the set float() itself strips were measured
// to be the same 25 code points, so the trim is exact.
//
// That set is NOT str.isspace(), which holds 29 — it also carries
// U+001C..U+001F, and float("\x1c1") raises. The name here used to say
// Py_UNICODE_ISSPACE, which is the 29-point one, and the wrong name
// invites the wrong repair: routing this trim through pytext.Strip (which
// implements str.strip, correctly, at 29) would make float() accept four
// separators CPython rejects. Two different sets, two different callers
// (adversarial r7 LOW).
//
// The digit VALUE is recovered by walking back to the start of the rune's
// run and taking the offset mod 10. Measured over Go's OWN digit table:
// 64 maximal runs, 63 of length 10 and one of length 50 (U+1D7CE–U+1D7FF),
// and in all 680 cases the digit value equals (offset from run start) mod
// 10 — zero mismatches. The walk-back is therefore exact, not approximate,
// and the modulo is load-bearing for the length-50 run.
//
// The digit VALUE fold moved to pytext.DecimalDigit / pytext.FoldDecimals
// when playbook's alarm dates turned out to need the same table: CPython's
// strptime accepts all 760 decimal digits for %Y/%m/%d and maps each by
// its unicodedata.decimal value. Two callers, one measured table — the
// pyRepr triplication in this port is what a second copy turns into.

func transformDecimals(s string) string {
	if !strings.ContainsFunc(s, func(r rune) bool {
		if r <= unicode.MaxASCII {
			return false
		}
		_, ok := pytext.DecimalDigit(r)
		return ok
	}) {
		return s
	}
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if r > unicode.MaxASCII {
			if d, ok := pytext.DecimalDigit(r); ok {
				b.WriteByte(d)
				continue
			}
		}
		b.WriteRune(r)
	}
	return b.String()
}
