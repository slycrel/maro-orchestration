package persona

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// DispatchLogPath is _dispatch_log_path(): the persona dispatch ledger in
// the workspace memory directory.
//
// Python's fallback when `orch_items.memory_dir()` raises is a HARDCODED
// `~/.maro/workspace/memory/...`, which is the live workspace regardless of
// what MARO_WORKSPACE said. This port has no such branch — the workspace is
// an argument, so there is nothing to fall back FROM, and writing to the
// live tree because a resolver hiccuped is the 2026-08-16 incident shape.
func DispatchLogPath(ws string) string {
	return filepath.Join(orch.MemoryDir(ws), "persona-dispatch-log.jsonl")
}

// RecordDispatch appends one persona-dispatch event.
//
// Row shape and key order are the contract: goal_preview, persona_name,
// confidence, is_fallback, dispatched_at, and handle_id LAST and only when
// non-empty — Python adds it with `entry["handle_id"] = handle_id` after
// the literal, so it cannot appear in the middle.
//
//   - goal[:120] is a CODE POINT slice.
//   - round(confidence, 3) is Python's round: half-to-EVEN on the binary
//     value, not half-away-from-zero. pyval.Round is that.
//   - the line is `json.dumps(entry)` with NO arguments, so ensure_ascii is
//     ON and the separators carry spaces: `{"a": 1, "b": 2}`, not
//     `{"a":1,"b":2}`. pyval.DumpsCompactPy is exactly that spelling.
//   - the append takes the same advisory flock on the same `.lock` sibling
//     Python's file_lock.locked_append does, so both runtimes can write
//     this ledger concurrently.
//
// Python swallows EVERYTHING here — "dispatch logging must never block
// execution" — and returns None. This returns the error so a caller can
// decide, which is the port's no-silent-errors doctrine; the CALLER is
// where the swallow belongs, because only the caller knows whether it is on
// the hot path.
func RecordDispatch(ws, goal, personaName string, confidence float64,
	isFallback bool, handleID string) error {

	entry := pyval.Obj{
		{Key: "goal_preview", Val: pytext.Head(goal, 120)},
		{Key: "persona_name", Val: personaName},
		{Key: "confidence", Val: pyval.Round(confidence, 3)},
		{Key: "is_fallback", Val: isFallback},
		{Key: "dispatched_at", Val: pyval.NowISO(time.Now().UTC())},
	}
	if handleID != "" {
		entry.Set("handle_id", handleID)
	}
	line, err := pyval.DumpsCompactPy(entry)
	if err != nil {
		return err
	}
	p := DispatchLogPath(ws)
	if err := os.MkdirAll(filepath.Dir(p), record.NewDirMode); err != nil {
		return err
	}
	return record.AppendRawLine(p, []byte(line))
}

// Gap is one entry of scan_persona_gaps' result: the dict keys role_hint,
// fallback_count, sample_goals, suggested_slug.
type Gap struct {
	RoleHint      string
	FallbackCount int
	SampleGoals   []string
	SuggestedSlug string
}

// stopwords is _STOPWORDS. A set, so only membership matters.
var stopwords = map[string]bool{
	"the": true, "a": true, "an": true, "to": true, "for": true,
	"and": true, "or": true, "in": true, "of": true, "on": true, "with": true,
}

// roleVerb is one _ROLE_VERBS entry. It is a SLICE and not a map because
// the lookup is `for phrase, role in _ROLE_VERBS.items()` with a `return`
// on the first hit, and CPython dicts iterate in INSERTION order. The order
// is behaviour, and the case that proves it is in the table itself:
// "create" precedes "create issue", so "create issue for X" infers
// "builder" and the "pm" entry two rows later is unreachable for it.
// Measured.
type roleVerb struct{ Phrase, Role string }

var roleVerbs = []roleVerb{
	{"build", "builder"}, {"implement", "builder"}, {"create", "builder"}, {"write", "builder"},
	{"research", "researcher"}, {"analyze", "researcher"}, {"investigate", "researcher"},
	{"review", "critic"}, {"audit", "critic"}, {"evaluate", "critic"},
	{"deploy", "ops"}, {"monitor", "ops"}, {"setup", "ops"},
	{"draft", "writer"}, {"summarize", "writer"}, {"document", "writer"},
	{"plan", "planner"}, {"design", "planner"}, {"architect", "planner"},
	{"file", "pm"}, {"create issue", "pm"}, {"manage", "pm"}, {"track", "pm"},
}

// nonWordRE is `re.split(r"\W+", lower)`. Go's `\W` is the ASCII
// complement, so a goal written with a NO-BREAK SPACE or an accented letter
// splits differently: CPython keeps "café" whole and an ASCII `\W` cuts it
// into "caf" and "" — which changes which word is FIRST and therefore which
// role a whole cluster of fallbacks is filed under.
var nonWordRE = regexp.MustCompile(pytext.NotWordClass + "+")

// inferRole is _infer_role.
//
// The `len(w) > 2` filter counts CHARACTERS. A two-character CJK word is
// six bytes, and a byte-length port keeps it where CPython drops it —
// measured: "研究 the market" infers "market" in CPython precisely because
// 研究 is two characters long and is filtered out.
func inferRole(goalText string) string {
	lower := pytext.Lower(goalText)
	for _, rv := range roleVerbs {
		if strings.Contains(lower, rv.Phrase) {
			return rv.Role
		}
	}
	for _, w := range nonWordRE.Split(lower, -1) {
		if w == "" || stopwords[w] || len([]rune(w)) <= 2 {
			continue
		}
		return w
	}
	return "general"
}

// ScanGaps scans the dispatch log for recurring fallback patterns.
//
// minFallbacks and windowDays are Python's keyword defaults 3 and 30;
// logPath "" resolves through DispatchLogPath.
//
// The cutoff comparison is a STRING comparison of ISO timestamps, and the
// two ways a row can be malformed are NOT handled alike upstream:
//
//   - `dispatched_at` present but not a string makes `>=` raise TypeError
//     inside the per-line `try`, so that row is SKIPPED. Absent is
//     different again — `d.get(key, "")` defaults to "", which sorts below
//     every real cutoff, so the row is dropped by the comparison.
//   - `goal_preview` not a string reaches `_infer_role`, which calls
//     `.lower()` OUTSIDE any try, so CPython raises AttributeError out of
//     scan_persona_gaps entirely and the caller gets no gaps at all.
//     Reproduced as a returned error: a Go panic is not a port of an
//     exception a caller can catch.
//
// Both measured.
func ScanGaps(ws, logPath string, minFallbacks, windowDays int) ([]Gap, error) {
	p := logPath
	if p == "" {
		p = DispatchLogPath(ws)
	}
	// `if not p.exists()` — Path.exists() FOLLOWS symlinks and answers
	// False for a dangling one, which os.Stat does too.
	if _, err := os.Stat(p); err != nil {
		return nil, nil
	}

	cutoff := pyval.NowISO(time.Now().UTC().Add(-time.Duration(windowDays) * 24 * time.Hour))

	raw, err := os.ReadFile(p)
	// The whole read is inside Python's outer `try: ... except: return []`,
	// so an unreadable file and a byte-tainted one both yield NO gaps
	// rather than a partial scan. utf8.Valid is the `encoding="utf-8"`
	// strict decode.
	if err != nil || !utf8.Valid(raw) {
		return nil, nil
	}

	var entries []pyval.Obj
	// read_text is universal-newlines and splitlines() breaks on TEN
	// separators — a form feed or a U+2028 inside a goal_preview splits a
	// row in CPython where strings.Split(s, "\n") would not.
	for _, line := range pytext.SplitLines(pytext.TranslateNewlines(string(raw))) {
		line = pytext.Strip(line)
		if line == "" {
			continue
		}
		d, derr := pyval.LoadsOrdered(line)
		if derr != nil {
			continue
		}
		obj, ok := d.(pyval.Obj)
		if !ok {
			// A JSON scalar or array: `d.get` raises AttributeError inside
			// the per-line try, so the row is skipped.
			continue
		}
		ts, present := obj.Get("dispatched_at")
		if !present {
			ts = "" // d.get(key, "")
		}
		tsStr, isStr := ts.(string)
		if !isStr {
			// EQUIVALENT MUTANT (recorded, not guarded): assigning "" here
			// instead of skipping answers the same, because "" sorts below
			// every ISO cutoff. The `continue` is kept because it is the
			// TypeError Python actually takes, and because the equivalence
			// depends on cutoff never being "".
			continue // `None >= "..."` / `5 >= "..."` raises -> skipped
		}
		// UNREACHABLE MUTANT (recorded, not guarded): `<` vs `<=` differs
		// only when tsStr is byte-identical to the cutoff, and the cutoff is
		// now-minus-window at microsecond resolution, computed inside this
		// call. No fixture can name it, so no test pins it.
		if tsStr < cutoff {
			continue
		}
		fb, _ := obj.Get("is_fallback")
		if !pyval.Truthy(fb) {
			continue
		}
		entries = append(entries, obj)
	}

	if len(entries) == 0 {
		return nil, nil
	}

	// defaultdict(list) iterates in first-insertion order, and that order
	// survives the stable sort below as the tie-break between two roles
	// with the same count.
	var order []string
	roleGoals := map[string][]string{}
	for _, e := range entries {
		gp, present := e.Get("goal_preview")
		if !present {
			gp = ""
		}
		gs, ok := gp.(string)
		if !ok {
			return nil, fmt.Errorf(
				"AttributeError: %s object has no attribute 'lower'",
				pytext.Repr(pyval.TypeName(gp)))
		}
		role := inferRole(gs)
		if _, seen := roleGoals[role]; !seen {
			order = append(order, role)
		}
		roleGoals[role] = append(roleGoals[role], gs)
	}

	gaps := []Gap{}
	for _, role := range order {
		goals := roleGoals[role]
		if len(goals) < minFallbacks {
			continue
		}
		gaps = append(gaps, Gap{
			RoleHint:      role,
			FallbackCount: len(goals),
			SampleGoals:   goals[:minInt(3, len(goals))],
			// `role.lower().replace(" ", "-")` — role is already lowered by
			// _infer_role on the word branch and is an ASCII constant on
			// the verb branch, so the call cannot change anything today.
			// Kept because the next verb someone adds could carry a space.
			SuggestedSlug: strings.ReplaceAll(pytext.Lower(role), " ", "-"),
		})
	}
	// `gaps.sort(key=count, reverse=True)` — list.sort is STABLE and
	// reverse=True does not reverse equal elements, so ties keep the order
	// their role was first SEEN in the log.
	sort.SliceStable(gaps, func(i, j int) bool {
		return gaps[i].FallbackCount > gaps[j].FallbackCount
	})
	return gaps, nil
}
