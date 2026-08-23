package skills

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Run-verdict skill attribution — Python memory_ledger.
// _maybe_record_skill_injection_outcomes (the 2026-07-29 measurement-honesty
// fix).
//
// When a FULL-trust closure verdict lands, it is credited to the skills that
// were ACTUALLY injected into the run's prompts — the run dir's
// source/skills_manifest.jsonl, written at each injection site — and not to
// the keyword-matched bystanders the legacy per-step counters credit.
//
// WHY THIS EXISTS AT ALL (adversarial r5, HIGH): both ends of this were
// already live in the Go port and nothing connected them. internal/loop
// wrote the manifest on every run and stamped the verdict on every run;
// RecordSkillInjectionOutcomes had zero non-test callers. So injected_runs
// sat at 0 forever, and the two consumers that gate on it both failed OPEN:
//
//   - MaybeAutoPromoteSkills vetoes a promotion only when InjectedRuns > 0,
//     so every Go promotion was evidence:"legacy-only" and the inflated
//     legacy counters were never checked — the exact inflation the veto
//     exists for. Measured: four FAILED verdicts on one injected skill,
//     identical seed both sides. Python held it at provisional; Go promoted
//     it to established. Two runtimes, one store, opposite decisions.
//   - FrontierSkills gates on InjectedRuns < minUses, so it returned an
//     empty frontier forever and the A/B variant subsystem got nothing to
//     split.
//
// That is verbatim the dead-`use_count` failure PORT.md already records —
// "its only writer was removed after it turned out to have no caller, so it
// sat at 0 for 312 of 314 live skills and silently starved the whole variant
// subsystem behind it". The port had swapped one dead gate for another.

// StampVerdictWithAttribution stamps a run's closure verdict onto its
// outcomes row and then credits that verdict to the skills the run actually
// injected. It is what production calls.
//
// The two halves are ONE function on purpose. In Python they are one
// function because stamp_outcome_verdict ends by calling the attributor;
// here record cannot call skills (skills imports record), so the composition
// moved up rather than being left to each caller to remember — "a correct
// primitive called from the wrong place" is the failure this port has
// already made twice. A source-level tripwire in internal/loop fails if the
// bare record.StampOutcomeVerdict is ever called there again.
//
// Attribution NEVER fails the stamp: it is telemetry riding on a verdict,
// and Python's own wrapper swallows everything for the same reason. What it
// does not do is fail silently — every refusal comes back as a warning, at
// the level Python logs it.
func StampVerdictWithAttribution(rec *record.Recorder, runDir, loopID string,
	achieved *bool, source string, confidence *float64) ([]string, error) {
	row, err := rec.StampOutcomeVerdict(loopID, achieved, source, confidence)
	if err != nil {
		return nil, err
	}
	return AttributeRunVerdict(rec.WorkspaceDir, runDir, loopID, row), nil
}

// AttributeRunVerdict credits one stamped verdict to the run's injected
// skills, returning warnings and never an error.
//
// runDir is passed in rather than resolved from loopID: Python needs
// runs.resolve_run_dir because stamp_outcome_verdict does not have the run
// dir, and its own comment insists on the DURABLE resolution rather than the
// ambient ContextVar. Every Go caller is holding the run dir already, so the
// durable path is the only one available here and there is nothing to get
// wrong.
func AttributeRunVerdict(ws, runDir, loopID string, row map[string]any) []string {
	// Gate 1: goal_achieved must be a real bool. Unjudged is not a verdict.
	achievedRaw, present := row["goal_achieved"]
	achieved, isBool := achievedRaw.(bool)
	if !present || !isBool {
		return nil
	}
	// Gate 2: the era-10 single-gate law, the same one the contradiction
	// emitter beside this uses — learning counters must not be fed by
	// low-confidence verdicts or by the verifier's own failure.
	if record.VerdictTrust(row) != record.VerdictTrustFull {
		return nil
	}
	if runDir == "" {
		return nil
	}
	manifest := filepath.Join(runDir, "source", "skills_manifest.jsonl")
	st, err := os.Stat(manifest)
	if err != nil || st.IsDir() {
		// Absent means the recorder never ran; present-and-empty means
		// nothing was injected. Neither is an error, and the loop's own
		// comment at the writer says the distinction is load-bearing.
		return nil
	}

	skillIDs, malformed, readWarns := manifestSkillIDs(manifest)
	warns := readWarns
	if malformed > 0 {
		// A manifest id must BE a string. str() coercion once minted stats
		// identities "True" and "7" out of malformed rows — laundered
		// evidence, not admission. Excluded and ANNOUNCED, never coerced.
		warns = append(warns, fmt.Sprintf("skills manifest for %s: %d entr(ies) "+
			"without a string id excluded from attribution (%s)",
			loopID, malformed, manifest))
	}
	if len(skillIDs) == 0 {
		return warns
	}

	marker := filepath.Join(runDir, "source", "skill_attribution.json")
	statsPath := skillStatsPath(ws)
	if err := os.MkdirAll(filepath.Dir(statsPath), record.NewDirMode); err != nil {
		return append(warns, "skill-injection attribution failed for "+loopID+
			" — verdict NOT recorded for this run's skills: "+err.Error())
	}

	// ONE critical section spans marker-check → batch → marker write. With
	// the check outside any lock, two live stampers both saw no marker,
	// both committed the batch, and both reported success — double
	// attribution with no crash involved. The stats lock is the natural
	// boundary; record.Locked is NOT reentrant (flock is per open file
	// description), so the batch runs through the lock-free inner form.
	lockErr := record.Locked(statsPath, func() error {
		if _, err := os.Stat(marker); err == nil {
			warns = append(warns, markerVerdictWarnings(marker, loopID, achieved, skillIDs)...)
			return nil
		}
		batchWarns, err := recordSkillInjectionOutcomesLocked(ws, statsPath, skillIDs, achieved)
		warns = append(warns, batchWarns...)
		if err != nil {
			return err
		}
		payload, perr := pyval.DumpsCompactPy(pyval.Obj{
			{Key: "loop_id", Val: loopID},
			{Key: "goal_achieved", Val: achieved},
			{Key: "skill_ids", Val: stringsToPyList(skillIDs)},
			// pyval.NowISO, not this package's nowISO: Python writes a
			// bare datetime.isoformat() here, which OMITS the fractional
			// part when the microsecond is 0, where the package-wide
			// stamp always writes six digits for lexicographic sorting.
			// Nothing sorts a marker, so the faithful spelling wins.
			{Key: "attributed_at", Val: pyval.NowISO(time.Now().UTC())},
		})
		if perr == nil {
			perr = record.AtomicWrite(marker, []byte(payload))
		}
		if perr != nil {
			// The batch COMMITTED; only the marker failed. Reporting this
			// as "NOT recorded" would be a lie — say what the store holds.
			warns = append(warns, fmt.Sprintf("skill-injection stats commit "+
				"for %s SUCCEEDED but the attribution marker was NOT written "+
				"(%s) — a later verdict stamp may re-apply this batch: %v",
				loopID, marker, perr))
		}
		return nil
	})
	if lockErr != nil {
		// Operator-visible, not debug: at debug a lock outage was
		// indistinguishable from ordinary missing telemetry.
		warns = append(warns, "skill-injection attribution failed for "+loopID+
			" — verdict NOT recorded for this run's skills: "+lockErr.Error())
	}
	return warns
}

// markerVerdictWarnings decides what an EXISTING marker means.
//
// A marker is only proof when it says what completion would have said: a
// zero-byte or copied marker once silently suppressed a whole run's
// verdicts. An invalid marker is UNKNOWN — warn and do NOT auto-re-apply,
// because the batch may already be in the stats store.
//
// The verdict is compared by VALUE, not just shape. "is a bool" accepted a
// marker whose verdict a later stamp had legitimately CORRECTED (re-stamping
// is by design), so the correction never reached skill stats and nothing was
// logged. A flipped verdict still must NOT auto-re-apply — a committed batch
// cannot be decremented here — but it is announced, not absorbed.
func markerVerdictWarnings(marker, loopID string, achieved bool, skillIDs []string) []string {
	structural, recorded := false, any(nil)
	if raw, err := os.ReadFile(marker); err == nil {
		var m map[string]any
		if json.Unmarshal(raw, &m) == nil {
			gotLoop, _ := m["loop_id"].(string)
			recorded = m["goal_achieved"]
			_, isBool := m["goal_achieved"].(bool)
			ids, idsOK := m["skill_ids"].([]any)
			structural = gotLoop == loopID && isBool && idsOK &&
				sameIDSet(ids, skillIDs)
		}
	}
	switch {
	case structural && recorded == achieved:
		return nil
	case structural:
		return []string{fmt.Sprintf("attribution for %s was recorded with "+
			"goal_achieved=%v but this stamp says %v — a corrected verdict "+
			"does NOT auto-adjust skill stats (the batch is already "+
			"committed); adjust manually if the correction matters (%s)",
			loopID, recorded, achieved, marker)}
	default:
		return []string{fmt.Sprintf("attribution marker for %s is unreadable "+
			"or does not match this run (%s) — attribution state UNKNOWN; "+
			"NOT re-applying; inspect the marker", loopID, marker)}
	}
}

// sameIDSet compares as SETS, matching Python's `set(m["skill_ids"]) ==
// set(skill_ids)`: a marker written from a differently-ordered manifest is
// still the same attribution.
func sameIDSet(stored []any, want []string) bool {
	got := map[string]bool{}
	for _, v := range stored {
		s, ok := v.(string)
		if !ok {
			return false
		}
		got[s] = true
	}
	wantSet := map[string]bool{}
	for _, s := range want {
		wantSet[s] = true
	}
	if len(got) != len(wantSet) {
		return false
	}
	for s := range wantSet {
		if !got[s] {
			return false
		}
	}
	return true
}

// manifestSkillIDs reads the run's injection manifest and returns the
// first-seen-ordered, de-duplicated skill ids, the count of entries whose id
// was not a non-empty string, and any read warnings.
//
// A torn manifest line is SKIPPED and announced rather than failing the
// attribution: this is telemetry riding on a verdict, and Python's
// _read_store has the same announced-skip posture. That is the opposite of
// internal/tasks' deliberate fail-closed read, and the difference is which
// runtime behaviour is being ported, not a house preference.
func manifestSkillIDs(path string) ([]string, int, []string) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, []string{"skills manifest unreadable (" + path + "): " + err.Error()}
	}
	defer f.Close()

	var ids []string
	seen := map[string]bool{}
	malformed, torn := 0, 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var rec map[string]any
		if json.Unmarshal([]byte(line), &rec) != nil {
			torn++
			continue
		}
		entries, _ := rec["skills"].([]any)
		for _, e := range entries {
			m, ok := e.(map[string]any)
			if !ok {
				malformed++
				continue
			}
			sid, isStr := m["id"].(string)
			if !isStr || sid == "" {
				malformed++
				continue
			}
			if !seen[sid] {
				seen[sid] = true
				ids = append(ids, sid)
			}
		}
	}
	var warns []string
	if err := sc.Err(); err != nil {
		warns = append(warns, "skills manifest read failed ("+path+"): "+err.Error())
	}
	if torn > 0 {
		warns = append(warns, fmt.Sprintf("skills manifest (%s): %d unparseable "+
			"line(s) skipped", path, torn))
	}
	return ids, malformed, warns
}

func stringsToPyList(ss []string) pyval.List {
	out := pyval.List{}
	for _, s := range ss {
		out = append(out, s)
	}
	return out
}
