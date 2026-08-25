package graduation

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func memoryDir(ws string) string { return filepath.Join(ws, "memory") }
func diagnosesPath(ws string) string {
	return filepath.Join(memoryDir(ws), "diagnoses.jsonl")
}
func suggestionsPath(ws string) string {
	return filepath.Join(memoryDir(ws), "suggestions.jsonl")
}
func verificationStatePath(ws string) string {
	return filepath.Join(memoryDir(ws), "graduation-verification-state.json")
}

// nowISO is `datetime.now(timezone.utc).isoformat()`, which is
// pyval.NowISO — not a local format string.
//
// The local copy this replaced spelled the layout by hand as
// "2006-01-02T15:04:05.000000-07:00" — offset and width right, but
// ".000000" for a whole second, where isoformat omits the fraction
// entirely. One call in a million lands there, which is exactly the kind
// of divergence a hand-written layout survives for years.
//
// It was one of FOUR byte-identical copies of that layout (graduation,
// pack/export, scans, skills/stats), which is why the answer was a census
// and not four fixes: a helper you did not look for is a helper you will
// write again, and the defect was the count. The line that used to stand
// here said "the FIFTH copy" — in all four files, and in inspector, which
// had no copy at all. Five claims to be the fifth is a paste, not a
// measurement (adversarial r2, L4).
func nowISO() string { return pyval.NowISO(time.Now().UTC()) }

// tailBytes is the byte bound on graduation's ledger reads — the same 8MB
// cap scans.readJSONLTail enforces, for the same reason: these files are
// shared with (and growable by) any co-resident process, so an unbounded
// os.ReadFile is an OOM lever (r1 security review: a 9MB suggestions.jsonl
// came back whole). Python reads these files unbounded; the cap is the
// port's read_jsonl_tail lesson applied consistently.
const tailBytes = 8 << 20

// tailLines returns the last n non-empty trimmed lines of a file, reading
// at most the final tailBytes bytes (a partial first line from mid-file
// entry is dropped).
func tailLines(path string, n int) []string {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil
	}
	offset := int64(0)
	if info.Size() > tailBytes {
		offset = info.Size() - tailBytes
	}
	if _, err := f.Seek(offset, 0); err != nil {
		return nil
	}
	raw, err := io.ReadAll(f)
	if err != nil {
		return nil
	}
	if offset > 0 {
		if i := strings.IndexByte(string(raw), '\n'); i >= 0 {
			raw = raw[i+1:] // drop the torn first line
		}
	}
	// read_text DECODES STRICTLY, and both callers wrap it in a bare
	// `except`: scan_candidates returns [] and _already_proposed returns
	// False. So one bad byte anywhere in the diagnoses ledger means Python
	// proposes NOTHING, where `string(raw)` reads on and encoding/json
	// substitutes U+FFFD per bad byte, yielding a candidate built from
	// content nobody wrote. Refusing is the behaviour, not a courtesy.
	//
	// The torn-line drop above happens on BYTES and before this on purpose:
	// the 8MB tail is a Go-only deviation (Python reads the whole file), and
	// decoding a window that begins mid-rune would raise where Python never
	// even looked. Dropping to the first \n first puts us on a line
	// boundary. Residual: a >8MB file written with lone-\r endings has no \n
	// to drop to, so its torn head survives — a deviation inside a
	// deviation, named rather than chased.
	//
	// read_text's OTHER rule, universal newlines, is deliberately NOT
	// applied here and is not missing: pyWindowLines splits immediately,
	// and SplitLines already treats "\r\n" and a lone "\r" as one break
	// each, so TranslateNewlines cannot change this function's result. A
	// call would be a guard that cannot fire — and one of those is not
	// evidence the danger is gone, it is a place a later reader stops
	// looking. A caller that KEEPS the text needs pyval.ReadText, whole.
	text, derr := pyval.DecodeUTF8Strict(raw)
	if derr != nil {
		return nil
	}
	return pyWindowLines(text, n)
}

// pyWindowLines is `text.splitlines()[-n:]`, then `line.strip()` with the
// blanks dropped — the window rule that scan_candidates and
// _already_proposed both spell inline, in that order.
//
// It exists because the two Go spellings of it had already drifted, and the
// drift was in the ORDER: this file's other copy filtered blanks FIRST and
// sliced the survivors, so blank lines cost it no window budget and it
// reached further back into the ledger than the Python predicate it claimed
// to replay. That is invisible in any test whose blank lines sit next to
// each other, which is why the divergence survived a review round.
//
// splitlines() breaks on ten separators; a "\n" split breaks on one. A
// stored row carrying \x0b or \x1c is TWO lines to Python and one to a "\n"
// split, so the two runtimes disagree about how many lines the window holds
// — and the window decides WHICH diagnoses reach a gate whose job is to
// propose permanent changes to the system's own behaviour. Not a rendering
// difference.
//
// And strings.TrimSpace does not know U+001C-U+001F, so a row wearing a
// trailing one is non-empty there, survives the filter, and then fails to
// parse — where Python strips it and reads the row.
//
// n <= 0 means no window, matching `lines[-0:]`. Negative n is not Python's
// behaviour (`lines[-(-5):]` drops the FIRST five) and is not reachable:
// every caller coerces. Named so the next reader does not have to re-derive
// that it is unreachable rather than wrong.
func pyWindowLines(text string, n int) []string {
	all := pytext.SplitLines(text)
	if n > 0 && len(all) > n {
		all = all[len(all)-n:]
	}
	var out []string
	for _, line := range all {
		if s := pytext.Strip(line); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// Candidate mirrors graduation.GraduationCandidate.
type Candidate struct {
	FailureClass    string
	Count           int
	LoopIDs         []string
	EvidenceSamples []string // up to 3 unique evidence strings
}

// ScanCandidates ports scan_candidates: repeated failure classes in the last
// `lookback` diagnoses, count >= minCount, ordered count-descending.
// 'healthy' and classes without a template are excluded.
func ScanCandidates(ws string, minCount, lookback int) []Candidate {
	if minCount <= 0 {
		minCount = 3
	}
	if lookback <= 0 {
		lookback = 100
	}
	templates := LoadTemplates(ws)
	byClass := map[string][]map[string]any{}
	var classOrder []string
	for _, line := range tailLines(diagnosesPath(ws), lookback) {
		var d map[string]any
		if json.Unmarshal([]byte(line), &d) != nil {
			continue
		}
		fc, _ := d["failure_class"].(string)
		if fc == "" || fc == "healthy" {
			continue
		}
		if _, known := templates[fc]; !known {
			continue
		}
		if _, present := byClass[fc]; !present {
			classOrder = append(classOrder, fc)
		}
		byClass[fc] = append(byClass[fc], d)
	}

	var candidates []Candidate
	// First-seen class order (Python dict-grouping insertion order), so the
	// count-desc stable sort below tie-breaks exactly as Python does.
	for _, fc := range classOrder {
		diags := byClass[fc]
		if len(diags) < minCount {
			continue
		}
		recent := diags
		if len(recent) > 5 {
			recent = recent[len(recent)-5:]
		}
		var loopIDs []string
		for _, d := range recent {
			lid, _ := d["loop_id"].(string)
			if lid == "" {
				lid = "?"
			}
			loopIDs = append(loopIDs, lid)
		}
		var evidence []string
		seen := map[string]bool{}
	evidenceLoop:
		for _, d := range diags {
			ev, _ := d["evidence"].([]any)
			for _, e := range ev {
				s, _ := e.(string)
				if s != "" && !seen[s] {
					evidence = append(evidence, s)
					seen[s] = true
				}
				if len(evidence) >= 3 {
					break evidenceLoop
				}
			}
		}
		candidates = append(candidates, Candidate{
			FailureClass:    fc,
			Count:           len(diags),
			LoopIDs:         loopIDs,
			EvidenceSamples: evidence,
		})
	}
	sort.SliceStable(candidates, func(i, j int) bool {
		return candidates[i].Count > candidates[j].Count
	})
	return candidates
}

// AlreadyProposed ports _already_proposed: a graduation suggestion for this
// failure class exists in the recent suggestions ledger. Substring match on
// failure_pattern, Python parity ("graduation:<fc>" tag).
// proposeDedupWindow is the propose-dedup window — Python
// _already_proposed's default, used by BOTH the pre-check and the in-lock
// re-check. It is deliberately NOT the caller's diagnoses-scan lookback:
// r3 review found the in-lock window keyed to that argument, which
// resurrected the r2 whole-file-suppression bug for `maro graduate
// -lookback N` with N>200 and fully for N<=0 (lastLines(tail, 0) = every
// line of the 8MB tail).
const proposeDedupWindow = 200

func AlreadyProposed(ws, failureClass string, lookback int) bool {
	if lookback <= 0 {
		lookback = 200
	}
	return proposedIn(tailLines(suggestionsPath(ws), lookback), failureClass)
}

// proposedIn is the ONE dedup predicate — windowed to the lines given,
// field-scoped to failure_pattern (Python _already_proposed). The in-lock
// re-check MUST replay exactly this: r1's Contains(old, fp) over the whole
// file silently suppressed every class ever proposed, forever, where
// Python re-proposes once the prior row ages past the lookback window
// (r2 review HIGH-1).
func proposedIn(lines []string, failureClass string) bool {
	needle := "graduation:" + failureClass
	for _, line := range lines {
		var d map[string]any
		if json.Unmarshal([]byte(line), &d) != nil {
			continue
		}
		if fp, _ := d["failure_pattern"].(string); strings.Contains(fp, needle) {
			return true
		}
	}
	return false
}

// truthy delegates to the one implementation of Python's bool().
//
// The private copy this replaces ended `return v != nil`, which is not
// Python's rule for an unrecognized type (Python says TRUE) and is not
// even Go's, since a typed nil in an interface is non-nil. It also had
// no `case int` (mission-r9).
func truthy(v any) bool { return pyval.Truthy(v) }

// lastLines is tailLines over a tail already in memory: the in-lock dedup
// re-check must decide on the SAME window as the pre-check, or the two
// disagree about whether a class was proposed and the lock protects nothing.
//
// It now shares pyWindowLines with tailLines rather than restating it. The
// restatement had drifted — it sliced AFTER dropping blanks, so blank rows
// cost it no window budget and it saw proposals the pre-check had aged out,
// suppressing a write CPython performs. The comment that used to sit here
// asserted the two "replay the SAME predicate"; that was a claim about the
// code rather than a property of it, and it had gone stale.
//
// The strict decode is applied here too. record.LockedTailAppend hands back
// bytes, and a lenient read would substitute U+FFFD and let a corrupted row
// parse into a suppression; refusing yields an empty window, which allows
// the write — the same direction as Python's `except: pass` → False.
func lastLines(text string, n int) []string {
	clean, derr := pyval.DecodeUTF8Strict([]byte(text))
	if derr != nil {
		return nil
	}
	return pyWindowLines(clean, n)
}

func clipRunes(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

// RunGraduation ports run_graduation: propose a pending suggestion per
// repeated failure class not already proposed. Rows remain pending until a
// human applies them (`maro evolve -apply`); once applied they flow into
// the V2/V3 cadence verify→demote loop like any other applied change.
// Returns the number of new suggestions written (0 on dryRun).
func RunGraduation(ws string, rec *record.Recorder, minCount, lookback int,
	dryRun, verbose bool) int {
	runID := record.NewID()
	candidates := ScanCandidates(ws, minCount, lookback)
	if len(candidates) == 0 {
		return 0
	}
	templates := LoadTemplates(ws)

	var newRows []map[string]any
	for _, c := range candidates {
		fc := c.FailureClass
		if AlreadyProposed(ws, fc, proposeDedupWindow) {
			if verbose {
				fmt.Fprintf(os.Stderr, "[graduation] %s: already proposed, skipping\n", fc)
			}
			continue
		}
		tmpl, known := templates[fc]
		if !known {
			continue
		}

		evidenceStr := strings.Join(firstN(c.EvidenceSamples, 2), "; ")
		if evidenceStr == "" {
			evidenceStr = "no specific evidence"
		}
		loopIDsStr := strings.Join(lastN(c.LoopIDs, 3), ", ")

		text := tmpl.Suggestion
		text = strings.ReplaceAll(text, "{count}", fmt.Sprintf("%d", c.Count))
		text = strings.ReplaceAll(text, "{loop_ids}", loopIDsStr)
		text = strings.ReplaceAll(text, "{evidence}", clipRunes(evidenceStr, 200))

		entry := map[string]any{
			"suggestion_id":     fmt.Sprintf("grad-%s-%s", runID, clipRunes(fc, 12)),
			"category":          tmpl.Category,
			"target":            "all",
			"suggestion":        clipRunes(text, 500),
			"failure_pattern":   "graduation:" + fc,
			"confidence":        tmpl.Confidence,
			"outcomes_analyzed": c.Count,
			"generated_at":      nowISO(),
			"applied":           false,
		}
		if tmpl.VerifyPattern != "" {
			// Recorded for display/parity; execution never reads it back from
			// the row (package doc — provenance is the producer's bit).
			entry["verify_pattern"] = tmpl.VerifyPattern
		}
		if len(tmpl.ExpectedSignal) > 0 {
			entry["expected_signal"] = tmpl.ExpectedSignal
		}
		newRows = append(newRows, entry)
		if verbose {
			fmt.Fprintf(os.Stderr, "[graduation] new: %s (%dx) → %s confidence=%g\n",
				fc, c.Count, tmpl.Category, tmpl.Confidence)
		}
	}
	if len(newRows) == 0 {
		return 0
	}
	if dryRun {
		if verbose {
			fmt.Fprintf(os.Stderr, "[graduation] dry_run: would write %d suggestions\n", len(newRows))
		}
		return 0
	}

	path := suggestionsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "[graduation] cannot create memory dir: %v\n", err)
		return 0
	}
	// The dedup re-check and the appends happen atomically under one lock
	// (two concurrent cadences both saw not-proposed and both appended —
	// r1 QA review), the tail read is byte-bounded (a whole-file RMW read
	// here was the OOM lever r1 closed elsewhere in this same file — r2
	// MED-2), and the re-check replays the SAME windowed field-scoped
	// predicate as the pre-check (r2 HIGH-1). landed collects the rows the
	// write actually persisted — events fire for exactly those (r1 F8).
	var landed []map[string]any
	err := record.LockedTailAppend(path, tailBytes, func(tail string) [][]byte {
		landed = landed[:0]
		window := lastLines(tail, proposeDedupWindow)
		var out [][]byte
		for _, row := range newRows {
			fp, _ := row["failure_pattern"].(string)
			fc := strings.TrimPrefix(fp, "graduation:")
			if proposedIn(window, fc) {
				continue // a concurrent cadence proposed this class first
			}
			// Python's json.dumps. A shared store: the Python cadence
			// reads these rows back (adversarial mission-r8).
			line, merr := pyval.DumpsCompactPy(pyval.FromPlain(row))
			raw := []byte(line)
			if merr != nil {
				fmt.Fprintf(os.Stderr, "[graduation] row marshal failed: %v\n", merr)
				continue
			}
			out = append(out, raw)
			landed = append(landed, row)
		}
		return out
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "[graduation] suggestion write failed: %v\n", err)
		return 0
	}
	if rec != nil {
		for _, row := range landed {
			sug, _ := row["suggestion"].(string)
			fp, _ := row["failure_pattern"].(string)
			_ = rec.Event("GRADUATION_PROPOSED", fp,
				"Graduation proposed: "+clipRunes(sug, 120),
				map[string]any{"category": row["category"], "confidence": row["confidence"]}, "")
		}
	}
	return len(landed)
}

func firstN(s []string, n int) []string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func lastN(s []string, n int) []string {
	if len(s) > n {
		return s[len(s)-n:]
	}
	return s
}

// VerifyResult is one row of verify_graduation_rules' output.
type VerifyResult struct {
	SuggestionID    string `json:"suggestion_id"`
	FailureClass    string `json:"failure_class"`
	Category        string `json:"category"`
	AppliedManually bool   `json:"applied_manually"`
	AppliedAt       string `json:"applied_at"`
	VerifyPattern   string `json:"verify_pattern"`
	Passed          bool   `json:"passed"`
	Output          string `json:"output"`
	StructuralOnly  bool   `json:"structural_only"`
}

// VerifyGraduationRules ports verify_graduation_rules: run the structural
// check for each APPLIED graduation suggestion. Newest record per failure
// class wins — reverted/held/pending rows are never described as live rule
// verification. Passed = exit 0 AND non-empty stdout.
//
// repoRoot is where the shipped grep patterns make sense (the maro source
// tree). Empty repoRoot → nil: with no tree to check against, structural
// verification is honestly unavailable, not failing (resolve it via
// MARO_REPO_ROOT or config graduation.repo_root at the call site).
//
// The executed pattern comes from the EMBEDDED template for the row's
// class — never from the row (package doc: the Python behavior of shelling
// out whatever suggestions.jsonl carries is the named backport correction).
func VerifyGraduationRules(ws, repoRoot string, lookback int) []VerifyResult {
	if repoRoot == "" {
		return nil
	}
	if lookback <= 0 {
		lookback = 200
	}
	lines := tailLines(suggestionsPath(ws), lookback)
	var results []VerifyResult
	seen := map[string]bool{}
	for i := len(lines) - 1; i >= 0; i-- { // newest first
		var d map[string]any
		if json.Unmarshal([]byte(lines[i]), &d) != nil {
			continue
		}
		fp, _ := d["failure_pattern"].(string)
		rowPattern, _ := d["verify_pattern"].(string)
		applied, _ := d["applied"].(bool)
		if rowPattern == "" || !strings.HasPrefix(fp, "graduation:") || !applied {
			continue
		}
		fc := strings.TrimPrefix(fp, "graduation:")
		if seen[fc] {
			continue
		}
		seen[fc] = true

		pattern := executablePattern(fc)
		if pattern == "" {
			fmt.Fprintf(os.Stderr,
				"[graduation] %s: row carries a verify_pattern but the shipped "+
					"set has none for this class — not executed\n", fc)
			continue
		}

		passed, output := runPattern(repoRoot, pattern)
		sid, _ := d["suggestion_id"].(string)
		category, _ := d["category"].(string)
		manual := truthy(d["applied_manually"]) // Python bool() — display field
		appliedAt, _ := d["applied_at"].(string)
		results = append(results, VerifyResult{
			SuggestionID:    sid,
			FailureClass:    fc,
			Category:        category,
			AppliedManually: manual,
			AppliedAt:       appliedAt,
			VerifyPattern:   pattern,
			Passed:          passed,
			Output:          output,
			StructuralOnly:  true,
		})
	}
	return results
}

func runPattern(repoRoot, pattern string) (bool, string) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "sh", "-c", pattern)
	cmd.Dir = repoRoot
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	out := strings.TrimSpace(stdout.String())
	passed := err == nil && out != ""
	output := clipRunes(out, 200)
	if output == "" {
		output = clipRunes(strings.TrimSpace(stderr.String()), 100)
	}
	if err != nil && output == "" {
		output = clipRunes(err.Error(), 100)
	}
	return passed, output
}

// RunGraduationVerification ports run_graduation_verification — the
// structural-observability layer only: GRADUATION_VERIFIED events (is the
// recommended code fix present in the tree?), never a revert or demote (a
// grep miss ≠ the applied lesson failed; the behavioral verdict is
// scans.VerifyAppliedSuggestions' job).
//
// Cadence may be driven concurrently: each event is CLAIMED under the
// shared lock, delivered outside it, then acknowledged. A failed delivery
// clears its claim for the next cadence; a crashed claimant's lease
// expires after five minutes. Named divergence: Python's Telegram notify
// half is unported — Go takes no notify claims, so notify_delivered
// remains false on failing rows until a runtime with a deliverer picks
// them up (the state file is shared data; Python can).
func RunGraduationVerification(ws, repoRoot string, rec *record.Recorder) []VerifyResult {
	// No repoRoot → structural verification is honestly UNAVAILABLE, and an
	// unavailable pass must not touch shared state: falling through with
	// empty results rewrites the state file to {} and erases the dedup
	// baseline a Python runtime built, making it re-emit GRADUATION_VERIFIED
	// per class (r1 security review — the wipe-on-empty shape is fork-point-
	// shared, but only Go routinely runs with no repoRoot resolved).
	if repoRoot == "" {
		return nil
	}
	results := VerifyGraduationRules(ws, repoRoot, 200)
	statePath := verificationStatePath(ws)
	if len(results) == 0 {
		if _, err := os.Stat(statePath); err != nil {
			return results
		}
	}

	type claim struct {
		result VerifyResult
		token  string
	}
	var eventClaims []claim
	claimToken := record.NewID() + record.NewID() // 16 hex — token, not an id
	nowEpoch := float64(time.Now().Unix())

	update := func(oldText string) string {
		old := map[string]any{}
		if strings.TrimSpace(oldText) != "" {
			var parsed any
			if json.Unmarshal([]byte(oldText), &parsed) == nil {
				if m, ok := parsed.(map[string]any); ok {
					old = m
				}
			}
		}
		current := map[string]any{}
		for _, result := range results {
			fc := result.FailureClass
			before, _ := old[fc].(map[string]any)
			identity := map[string]any{
				"suggestion_id": result.SuggestionID,
				"applied_at":    result.AppliedAt,
				"passed":        result.Passed,
			}
			same := before != nil
			for k, v := range identity {
				if same && before[k] != v {
					same = false
				}
			}
			var after map[string]any
			if same {
				after = map[string]any{}
				for k, v := range before {
					after[k] = v
				}
			} else {
				after = map[string]any{
					"suggestion_id":    identity["suggestion_id"],
					"applied_at":       identity["applied_at"],
					"passed":           identity["passed"],
					"event_delivered":  false,
					"notify_delivered": result.Passed,
				}
			}
			after["checked_at"] = nowISO()

			claimable := func(kind string) bool {
				if b, _ := after[kind+"_delivered"].(bool); b {
					return false
				}
				claimedAt := 0.0
				if f, ok := after[kind+"_claimed_at"].(float64); ok {
					claimedAt = f
				}
				hasClaim := false
				if c, _ := after[kind+"_claim"].(string); c != "" {
					hasClaim = true
				}
				return !hasClaim || nowEpoch-claimedAt >= 300
			}
			if claimable("event") {
				after["event_claim"] = claimToken
				after["event_claimed_at"] = nowEpoch
				eventClaims = append(eventClaims, claim{result, claimToken})
			}
			// Notify claims deliberately not taken (Telegram unported).
			current[fc] = after
		}
		// json.dumps(..., indent=2), not MarshalIndent: same file, both
		// runtimes (adversarial mission-r8).
		enc, err := pyval.DumpsIndent2(pyval.FromPlain(current))
		if err != nil {
			return oldText
		}
		return enc + "\n"
	}
	if err := record.LockedRMW(statePath, update); err != nil {
		// Results remain available to the caller, but without durable dedup
		// state the event side effects are suppressed (Python parity).
		fmt.Fprintf(os.Stderr, "[graduation] verification state update failed: %v\n", err)
		return results
	}

	delivered := map[string]bool{} // failure_class → success (single token per pass)
	for _, c := range eventClaims {
		status := "failed"
		if c.result.Passed {
			status = "passed"
		}
		var evErr error
		if rec != nil {
			// Struct → event context. The obvious spelling —
			// Marshal-then-Unmarshal into map[string]any — is the INVERSE of
			// the r8 bug and just as wrong: encoding/json decodes every
			// number as float64, so an int field (a count, a sample size)
			// arrived here as 3.0 and the recorder, which correctly renders
			// through pyval, then wrote `3.0` where Python's asdict() keeps
			// an int and writes `3`. A round-trip through encoding/json is
			// never type-preserving in either direction.
			//
			// pyval.FromStruct walks the struct instead, so an int stays an
			// int and a float keeps its `.0` (mission-r8).
			ctxMap := map[string]any{}
			if obj, err := pyval.FromStruct(c.result); err == nil {
				for _, f := range obj {
					ctxMap[f.Key] = f.Val
				}
			}
			evErr = rec.Event("GRADUATION_VERIFIED",
				"graduation:"+c.result.FailureClass,
				fmt.Sprintf("Applied graduation structural check %s: %s",
					status, c.result.FailureClass),
				ctxMap, "")
		} else {
			evErr = fmt.Errorf("no recorder")
		}
		if evErr == nil {
			delivered[c.result.FailureClass] = true
		} else {
			fmt.Fprintf(os.Stderr, "[graduation] verification event delivery failed: %v\n", evErr)
		}
	}

	ack := func(oldText string) string {
		var parsed any
		if json.Unmarshal([]byte(oldText), &parsed) != nil {
			return oldText
		}
		state, ok := parsed.(map[string]any)
		if !ok {
			return oldText
		}
		for _, c := range eventClaims {
			row, _ := state[c.result.FailureClass].(map[string]any)
			if row == nil {
				continue
			}
			if tok, _ := row["event_claim"].(string); tok == c.token {
				delete(row, "event_claim")
				delete(row, "event_claimed_at")
				if delivered[c.result.FailureClass] {
					row["event_delivered"] = true
				}
			}
		}
		enc, err := pyval.DumpsIndent2(pyval.FromPlain(state))
		if err != nil {
			return oldText
		}
		return enc + "\n"
	}
	if err := record.LockedRMW(statePath, ack); err != nil {
		fmt.Fprintf(os.Stderr, "[graduation] verification delivery acknowledgement failed: %v\n", err)
	}
	return results
}
