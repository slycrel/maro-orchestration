package orch

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The mission layer's STORE half: mission.json, feature_list.json,
// mission-log.jsonl and the drain lock — ports of mission.py's persistence
// surface. The DAG executor, the LLM decomposition and the validation
// gates are not here; they need the agent loop and an adapter.
//
// The formal hierarchy is Mission → Milestone → Feature → worker session.
//
// FOUR DIFFERENT WRITE DISCIPLINES, one per file, and they are not
// interchangeable — Python picked each deliberately and a shared store
// reads the difference:
//
//	mission.json       atomic_write            crash-safe full rewrite
//	feature_list.json  write_text on CREATE    never overwritten once written
//	feature_list.json  locked_rmw on PATCH     two grade completions raced here
//	mission-log.jsonl  locked_append           append-only, one line per result
//
// All four are `json.dumps` with Python's DEFAULT separators and
// ensure_ascii — see pyrender.go for the measured spelling.

// Feature is one unit of work; each gets a fresh context window.
type Feature struct {
	ID     string
	Title  string
	Status string // "pending" | "running" | "done" | "blocked"

	// Optional in the store: a JSON null and an absent key are the same
	// thing to Python's .get(), and both must come back as null on write.
	WorkerSessionID *string
	ResultSummary   *string

	ElapsedMS int

	// elapsedRaw is the literal this feature was LOADED with, kept so a
	// value Python round-trips does not get re-typed by a Go rewrite.
	// Python's load does no coercion at all — a stored `1.5` stays 1.5
	// across a save — so a typed int field alone would silently rewrite a
	// peer's row. Emitted only while the typed view still agrees with it.
	elapsedRaw any
}

// Milestone is a group of features with a validation gate.
type Milestone struct {
	ID                 string
	Title              string
	Features           []Feature
	ValidationCriteria []string
	Status             string // "pending"|"running"|"validating"|"done"|"failed"
	ValidationResult   *string

	// DependsOn is ORDERING only — it never gates on the predecessor's
	// outcome. Python normalizes this one field on load (non-strings and
	// empties dropped), so unlike the others it is genuinely a []string.
	DependsOn []string

	// criteriaRaw is validation_criteria as loaded, for the same reason
	// as elapsedRaw: Python stores whatever was there, including a bare
	// string where a list belongs.
	criteriaRaw any
}

// Mission is one multi-day goal.
type Mission struct {
	ID              string
	Goal            string
	Project         string
	Milestones      []Milestone
	Status          string // "pending"|"running"|"done"|"stuck"|"interrupted"
	CreatedAt       string
	CompletedAt     *string
	AncestryContext string
}

// MissionPath is <workspace>/projects/<slug>/mission.json.
func MissionPath(ws, project string) string {
	return filepath.Join(ProjectDir(ws, project), "mission.json")
}

// FeatureManifestPath is <workspace>/projects/<slug>/feature_list.json.
func FeatureManifestPath(ws, project string) string {
	return filepath.Join(ProjectDir(ws, project), "feature_list.json")
}

// MissionLogPath is <workspace>/memory/mission-log.jsonl.
//
// It CREATES memory/, because the Python it ports is
// `o.memory_dir() / "mission-log.jsonl"` and memory_dir mkdirs — see
// EnsureMemoryDir. Resolving this path is not a pure operation in the
// original, and the difference is observable twice: the directory exists
// afterwards, and a workspace where it cannot be created stops the caller
// here rather than letting it read an empty log.
func MissionLogPath(ws string) (string, error) {
	dir, err := EnsureMemoryDir(ws)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "mission-log.jsonl"), nil
}

// SaveMission writes mission.json.
//
// The key order below is Python's dict-literal order, which is the order
// json.dumps emits — a rewrite by either runtime has to read the same way.
func SaveMission(ws string, m *Mission, project string) error {
	path := MissionPath(ws, project)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	milestones := pyList{}
	for _, ms := range m.Milestones {
		features := pyList{}
		for _, f := range ms.Features {
			features = append(features, pyObj{
				{Key: "id", Val: f.ID},
				{Key: "title", Val: f.Title},
				{Key: "status", Val: f.Status},
				{Key: "worker_session_id", Val: strOrNil(f.WorkerSessionID)},
				{Key: "result_summary", Val: strOrNil(f.ResultSummary)},
				{Key: "elapsed_ms", Val: carriedInt(f.ElapsedMS, f.elapsedRaw)},
			})
		}
		milestones = append(milestones, pyObj{
			{Key: "id", Val: ms.ID},
			{Key: "title", Val: ms.Title},
			{Key: "features", Val: features},
			{Key: "validation_criteria", Val: carriedStrings(ms.ValidationCriteria, ms.criteriaRaw)},
			{Key: "status", Val: ms.Status},
			{Key: "validation_result", Val: strOrNil(ms.ValidationResult)},
			{Key: "depends_on", Val: stringsToList(ms.DependsOn)},
		})
	}
	payload := pyObj{
		{Key: "id", Val: m.ID},
		{Key: "goal", Val: m.Goal},
		{Key: "project", Val: m.Project},
		{Key: "milestones", Val: milestones},
		{Key: "status", Val: m.Status},
		{Key: "created_at", Val: m.CreatedAt},
		{Key: "completed_at", Val: strOrNil(m.CompletedAt)},
		{Key: "ancestry_context", Val: m.AncestryContext},
	}
	text, err := DumpsIndent2(payload)
	if err != nil {
		return err
	}
	return record.AtomicWrite(path, []byte(text))
}

// strOrNil renders an absent optional as JSON null, which is what Python
// writes for None — NOT an omitted key.
func strOrNil(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

func stringsToList(ss []string) pyList {
	out := pyList{}
	for _, s := range ss {
		out = append(out, s)
	}
	return out
}

// carriedInt emits the loaded literal while the typed view still agrees
// with it, and the typed value once the program has changed it. This is
// the same carry-unless-named posture the skills pool rewrite uses: a
// field this runtime did not touch is given back exactly as it arrived.
func carriedInt(v int, raw any) any {
	if raw != nil && pyIntOf(raw) == v {
		return raw
	}
	return v
}

func carriedStrings(v []string, raw any) any {
	if raw != nil && sameStrings(coerceStrings(raw), v) {
		return raw
	}
	return stringsToList(v)
}

func sameStrings(a, b []string) bool {
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

// LoadMission reads mission.json, or nil when there is none and nil when
// the file cannot be understood.
//
// The tri-state matters and Python collapses it: `except Exception: return
// None` means a missing file, a torn write and a schema-drifted row are
// one answer. This port keeps that answer — a caller that treated
// "unreadable" as "no mission yet" and created a fresh one would overwrite
// the unreadable file, and matching Python is what keeps the two runtimes
// from disagreeing about whether a project HAS a mission.
//
// NAMED DIVERGENCE, narrow: Python does not type-check the required
// fields, so a mission.json whose `id` is the number 7 loads there and is
// re-emitted as 7. This port treats a non-string required field as
// unreadable. Every other tolerance is preserved (see elapsedRaw).
func LoadMission(ws, project string) *Mission {
	raw, err := os.ReadFile(MissionPath(ws, project))
	if err != nil {
		return nil
	}
	v, err := LoadsOrdered(string(raw))
	if err != nil {
		return nil
	}
	data, ok := v.(pyObj)
	if !ok {
		return nil
	}
	// Python indexes these with [], so a missing one raises KeyError and
	// the bare except turns the whole load into None.
	id, ok1 := requiredString(data, "id")
	goal, ok2 := requiredString(data, "goal")
	proj, ok3 := requiredString(data, "project")
	status, ok4 := requiredString(data, "status")
	createdAt, ok5 := requiredString(data, "created_at")
	if !(ok1 && ok2 && ok3 && ok4 && ok5) {
		return nil
	}

	var milestones []Milestone
	rawMilestones, _ := data.Get("milestones")
	if rawMilestones != nil {
		list, ok := rawMilestones.(pyList)
		if !ok {
			// Python iterates whatever this is; a string yields characters
			// and md["id"] then raises TypeError into the same bare except.
			return nil
		}
		for _, mv := range list {
			md, ok := mv.(pyObj)
			if !ok {
				return nil
			}
			msID, okA := requiredString(md, "id")
			msTitle, okB := requiredString(md, "title")
			msStatus, okC := requiredString(md, "status")
			if !(okA && okB && okC) {
				return nil
			}
			var features []Feature
			if rawFeatures, present := md.Get("features"); present && rawFeatures != nil {
				fl, ok := rawFeatures.(pyList)
				if !ok {
					return nil
				}
				for _, fv := range fl {
					fd, ok := fv.(pyObj)
					if !ok {
						return nil
					}
					fID, okX := requiredString(fd, "id")
					fTitle, okY := requiredString(fd, "title")
					fStatus, okZ := requiredString(fd, "status")
					if !(okX && okY && okZ) {
						return nil
					}
					elapsed, _ := fd.Get("elapsed_ms")
					features = append(features, Feature{
						ID:              fID,
						Title:           fTitle,
						Status:          fStatus,
						WorkerSessionID: optString(fd, "worker_session_id"),
						ResultSummary:   optString(fd, "result_summary"),
						ElapsedMS:       pyIntOf(elapsed),
						elapsedRaw:      elapsed,
					})
				}
			}
			criteria, _ := md.Get("validation_criteria")
			deps, depsPresent := md.Get("depends_on")
			ms := Milestone{
				ID:                 msID,
				Title:              msTitle,
				Features:           features,
				ValidationCriteria: coerceStrings(criteria),
				Status:             msStatus,
				ValidationResult:   optString(md, "validation_result"),
				criteriaRaw:        criteria,
			}
			if depsList, isList := deps.(pyList); isList && depsPresent {
				// Python's own normalizer: non-strings and empty strings
				// are dropped. This is the one field it does type-check.
				for _, d := range depsList {
					if s, ok := d.(string); ok && s != "" {
						ms.DependsOn = append(ms.DependsOn, s)
					}
				}
				if ms.DependsOn == nil {
					ms.DependsOn = []string{}
				}
			} else {
				// Legacy mission.json predates the DAG: reconstruct the
				// sequential chain, each milestone following the previous.
				ms.DependsOn = []string{}
				if len(milestones) > 0 {
					ms.DependsOn = []string{milestones[len(milestones)-1].ID}
				}
			}
			milestones = append(milestones, ms)
		}
	}
	out := &Mission{
		ID: id, Goal: goal, Project: proj, Milestones: milestones,
		Status: status, CreatedAt: createdAt,
		CompletedAt:     optString(data, "completed_at"),
		AncestryContext: stringOr(data, "ancestry_context", ""),
	}
	return out
}

func requiredString(o pyObj, key string) (string, bool) {
	v, present := o.Get(key)
	if !present {
		return "", false
	}
	s, ok := v.(string)
	return s, ok
}

// optString is Python's .get(key) for an Optional[str]: absent, JSON null
// and a wrong type all become None.
func optString(o pyObj, key string) *string {
	v, present := o.Get(key)
	if !present || v == nil {
		return nil
	}
	s, ok := v.(string)
	if !ok {
		return nil
	}
	return &s
}

func stringOr(o pyObj, key, def string) string {
	v, present := o.Get(key)
	if !present || v == nil {
		return def
	}
	if s, ok := v.(string); ok {
		return s
	}
	return def
}

// coerceStrings is the TYPED VIEW of a field Python leaves alone. It is
// deliberately not a normalizer: the stored literal is what gets written
// back (carriedStrings), and this only decides what Go code reads.
func coerceStrings(v any) []string {
	list, ok := v.(pyList)
	if !ok {
		return []string{}
	}
	out := make([]string, 0, len(list))
	for _, item := range list {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// ---------------------------------------------------------------------------
// Feature manifest (Phase 19): the immutable record of what was promised
// ---------------------------------------------------------------------------

// ContractGrade is the grading result a feature is marked passing with.
// A nil *ContractGrade is Python's "neither an object with .passed nor a
// dict" branch: not passing, score 0, no contract id.
type ContractGrade struct {
	Passed     bool
	Score      float64
	ContractID string
}

// GenerateFeatureManifest writes feature_list.json at mission start.
//
// NEVER overwritten: an existing manifest is read and returned as-is. That
// is the whole point of the file — it is the promise the run is graded
// against, so a re-decomposition partway through must not be able to
// quietly change what "done" meant.
func GenerateFeatureManifest(ws string, m *Mission, project string) (pyObj, error) {
	path := FeatureManifestPath(ws, project)
	if raw, err := os.ReadFile(path); err == nil {
		if v, err := LoadsOrdered(string(raw)); err == nil {
			if obj, ok := v.(pyObj); ok {
				return obj, nil
			}
		}
		// Present but unreadable: Python falls through and REBUILDS,
		// overwriting it. Ported as-is — the alternative (refusing) would
		// wedge a mission on a torn write with no repair path, and the
		// manifest is regenerable from the mission it came from.
	}
	features := pyList{}
	for _, ms := range m.Milestones {
		for _, f := range ms.Features {
			features = append(features, pyObj{
				{Key: "id", Val: f.ID},
				{Key: "title", Val: f.Title},
				{Key: "milestone_id", Val: ms.ID},
				{Key: "passes", Val: false},
				{Key: "contract_id", Val: nil},
				{Key: "grade_score", Val: nil},
			})
		}
	}
	manifest := pyObj{{Key: "features", Val: features}}
	text, err := DumpsIndent2(manifest)
	if err != nil {
		return manifest, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return manifest, nil // Python swallows the write failure
	}
	// write_text, NOT atomic_write: this is Python's shape here, and the
	// difference is observable — a crash mid-write leaves a truncated
	// manifest that the read above then rebuilds from the mission.
	_ = os.WriteFile(path, []byte(text), 0o644)
	return manifest, nil
}

// LoadFeatureManifest reads feature_list.json, or nil when absent or
// unparseable.
func LoadFeatureManifest(ws, project string) pyObj {
	raw, err := os.ReadFile(FeatureManifestPath(ws, project))
	if err != nil {
		return nil
	}
	v, err := LoadsOrdered(string(raw))
	if err != nil {
		return nil
	}
	obj, ok := v.(pyObj)
	if !ok {
		return nil
	}
	return obj
}

// ErrManifestMonotonicity is the one failure MarkFeaturePassing reports.
//
// Every other failure — a missing manifest, a lock timeout, a disk error,
// an unparseable file — is deliberately silent, matching Python, because
// they were all `except Exception: pass` before the lock went in and
// turning them into errors now would change which callers abort. A
// monotonicity breach is different in kind: it is a caller trying to erase
// a passing grade, and it has always propagated.
var ErrManifestMonotonicity = errors.New("feature manifest: monotonicity violation")

// MarkFeaturePassing records a grade against one feature.
//
// Read-modify-write under ONE lock. It used to be an unlocked read
// followed by an unlocked write, so two grade completions landing together
// lost one update.
func MarkFeaturePassing(ws, project, featureID string, grade *ContractGrade) error {
	path := FeatureManifestPath(ws, project)
	if _, err := os.Stat(path); err != nil {
		return nil // manifest not created yet — silent no-op
	}
	passed, score, contractID := false, 0.0, ""
	if grade != nil {
		passed, score, contractID = grade.Passed, grade.Score, grade.ContractID
	}

	var breach error
	mutate := func(old string) string {
		v, err := LoadsOrdered(old)
		if err != nil {
			return old // unparseable — no-op rewrite
		}
		manifest, ok := v.(pyObj)
		if !ok {
			return old
		}
		featuresVal, _ := manifest.Get("features")
		features, ok := featuresVal.(pyList)
		if !ok {
			return old
		}
		for _, fv := range features {
			feat, ok := fv.(pyObj)
			if !ok {
				continue
			}
			if s, _ := feat.Get("id"); s != featureID {
				continue
			}
			// `feat.get("passes") is True` — an identity check against the
			// singleton, so only a real JSON true blocks the downgrade.
			if prior, _ := feat.Get("passes"); pyBool(prior) && !passed {
				breach = fmt.Errorf("%w: feature %s already passes=true; "+
					"cannot downgrade to passes=false", ErrManifestMonotonicity,
					pytext.Repr(featureID))
				return old
			}
			feat.Set("passes", passed)
			feat.Set("grade_score", score)
			// Falsy check: an empty contract id leaves the stored one
			// alone rather than blanking it.
			if contractID != "" {
				feat.Set("contract_id", contractID)
			}
			text, err := DumpsIndent2(manifest)
			if err != nil {
				return old
			}
			return text
		}
		return old // feature not found — no-op rewrite
	}

	if err := record.LockedRMW(path, mutate); err != nil {
		if breach != nil {
			return breach
		}
		return nil // lock timeout, disk error: silent, matching Python
	}
	return breach
}

// ---------------------------------------------------------------------------
// Read-side summaries
// ---------------------------------------------------------------------------

// MissionSummary is one row of list_missions.
//
// It is built from the RAW json with .get() defaults, NOT through
// LoadMission — so a mission.json that LoadMission refuses still appears
// here, with "?" for the fields it could not read. The two functions
// disagreeing is Python's behaviour, not an oversight: this one answers
// "what is on disk" and LoadMission answers "what can be executed".
type MissionSummary struct {
	Project         string
	MissionID       string
	Goal            string
	Status          string
	MilestonesTotal int
	MilestonesDone  int
	FeaturesDone    int
	FeaturesTotal   int
	CreatedAt       string
}

// ListMissions scans every project for a mission.json.
func ListMissions(ws string) []MissionSummary {
	results := []MissionSummary{}
	entries, err := os.ReadDir(ProjectsRoot(ws))
	if err != nil {
		return results
	}
	// os.ReadDir returns entries sorted by filename, which is the order
	// Python's sorted(iterdir()) produces: the paths share a parent, so
	// comparing the full path strings orders them by name.
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(ProjectsRoot(ws), e.Name(), "mission.json"))
		if err != nil {
			continue
		}
		v, err := LoadsOrdered(string(raw))
		if err != nil {
			continue
		}
		data, ok := v.(pyObj)
		if !ok {
			continue
		}
		milestonesVal, _ := data.Get("milestones")
		milestones, _ := milestonesVal.(pyList)
		featuresDone, featuresTotal, milestonesDone := 0, 0, 0
		for _, mv := range milestones {
			md, ok := mv.(pyObj)
			if !ok {
				continue
			}
			if s, _ := md.Get("status"); s == "done" {
				milestonesDone++
			}
			fv, _ := md.Get("features")
			fl, _ := fv.(pyList)
			featuresTotal += len(fl)
			for _, f := range fl {
				fd, ok := f.(pyObj)
				if !ok {
					continue
				}
				if s, _ := fd.Get("status"); s == "done" {
					featuresDone++
				}
			}
		}
		results = append(results, MissionSummary{
			Project:         e.Name(),
			MissionID:       stringOr(data, "id", "?"),
			Goal:            stringOr(data, "goal", ""),
			Status:          stringOr(data, "status", "?"),
			MilestonesTotal: len(milestones),
			MilestonesDone:  milestonesDone,
			FeaturesDone:    featuresDone,
			FeaturesTotal:   featuresTotal,
			CreatedAt:       stringOr(data, "created_at", ""),
		})
	}
	return results
}

// PendingMission is a summary plus the count of milestones still open.
type PendingMission struct {
	MissionSummary
	MilestonesPending int
}

// PendingMissions returns missions with work remaining. The heartbeat
// reads this to decide whether an autonomous drain should trigger.
//
// Note it re-reads each mission through LoadMission, so a mission.json
// that ListMissions could summarize but LoadMission refuses is silently
// absent here — a broken mission never triggers a drain.
func PendingMissions(ws string) []PendingMission {
	out := []PendingMission{}
	for _, m := range ListMissions(ws) {
		if m.Status == "done" {
			continue
		}
		loaded := LoadMission(ws, m.Project)
		if loaded == nil {
			continue
		}
		pending := 0
		for _, ms := range loaded.Milestones {
			if ms.Status != "done" {
				pending++
			}
		}
		if pending > 0 {
			out = append(out, PendingMission{MissionSummary: m, MilestonesPending: pending})
		}
	}
	return out
}

// MorningBriefing renders the overnight status line Telegram and the CLI
// both show.
func MorningBriefing(ws string, maxMissions int) string {
	return morningBriefingAt(ws, maxMissions, time.Now().UTC())
}

func morningBriefingAt(ws string, maxMissions int, now time.Time) string {
	if maxMissions <= 0 {
		maxMissions = 5
	}
	var done, inProgress, pending []MissionSummary
	for _, m := range ListMissions(ws) {
		switch m.Status {
		case "done":
			done = append(done, m)
		case "running", "blocked":
			inProgress = append(inProgress, m)
		default:
			pending = append(pending, m)
		}
	}
	// The goal is clipped to 60 CODE POINTS. Python slices a str, so an
	// accented or emoji goal keeps 60 characters here and would keep
	// fewer — and could split a rune — on a byte slice.
	clip := func(s string) string { return pyClip(s, 60) }
	head := func(list []MissionSummary) []MissionSummary {
		if len(list) > maxMissions {
			return list[:maxMissions]
		}
		return list
	}

	lines := []string{"Morning briefing — " + now.Format("2006-01-02 15:04") + " UTC"}
	// Ported verbatim: Python appends an empty element AND prefixes the
	// next section header with "\n", so a briefing with any section
	// carries a double blank line. It reads like a bug and it is what the
	// operator's Telegram history has looked like for months.
	lines = append(lines, "")
	if len(done) > 0 {
		lines = append(lines, fmt.Sprintf("Completed (%d):", len(done)))
		for _, m := range head(done) {
			lines = append(lines, fmt.Sprintf("  ✓ [%s] %s (%d/%d milestones)",
				m.Project, clip(m.Goal), m.MilestonesDone, m.MilestonesTotal))
		}
	}
	if len(inProgress) > 0 {
		lines = append(lines, fmt.Sprintf("\nIn progress (%d):", len(inProgress)))
		for _, m := range head(inProgress) {
			lines = append(lines, fmt.Sprintf("  → [%s] %s (%d/%d milestones)",
				m.Project, clip(m.Goal), m.MilestonesDone, m.MilestonesTotal))
		}
	}
	if len(pending) > 0 {
		lines = append(lines, fmt.Sprintf("\nQueued (%d):", len(pending)))
		for _, m := range head(pending) {
			lines = append(lines, fmt.Sprintf("  ○ [%s] %s", m.Project, clip(m.Goal)))
		}
	}
	if len(done) == 0 && len(inProgress) == 0 && len(pending) == 0 {
		lines = append(lines, "No active missions.")
	}
	return strings.Join(lines, "\n")
}

// ---------------------------------------------------------------------------
// Mission log
// ---------------------------------------------------------------------------

// MissionResult is one mission run's outcome.
type MissionResult struct {
	MissionID       string
	Project         string
	Goal            string
	Status          string
	MilestonesDone  int
	MilestonesTotal int
	FeaturesDone    int
	FeaturesTotal   int
	ElapsedMS       int
}

// Summary is the human-readable block the CLI prints. `goal=` carries
// Python's repr quoting, which is why it is not a plain %q — Go escapes a
// different set and prefers double quotes where repr prefers single.
func (r MissionResult) Summary() string {
	return strings.Join([]string{
		"mission_id=" + r.MissionID,
		"project=" + r.Project,
		"goal=" + pytext.Repr(r.Goal),
		"status=" + r.Status,
		fmt.Sprintf("milestones=%d/%d", r.MilestonesDone, r.MilestonesTotal),
		fmt.Sprintf("features=%d/%d", r.FeaturesDone, r.FeaturesTotal),
		fmt.Sprintf("elapsed_ms=%d", r.ElapsedMS),
	}, "\n")
}

// WriteMissionLog appends one result to mission-log.jsonl.
func WriteMissionLog(ws string, r MissionResult, m *Mission) error {
	row := pyObj{
		{Key: "mission_id", Val: r.MissionID},
		{Key: "project", Val: r.Project},
		{Key: "goal", Val: r.Goal},
		{Key: "status", Val: r.Status},
		{Key: "milestones_done", Val: r.MilestonesDone},
		{Key: "milestones_total", Val: r.MilestonesTotal},
		{Key: "features_done", Val: r.FeaturesDone},
		{Key: "features_total", Val: r.FeaturesTotal},
		{Key: "elapsed_ms", Val: r.ElapsedMS},
		{Key: "created_at", Val: m.CreatedAt},
		{Key: "completed_at", Val: strOrNil(m.CompletedAt)},
	}
	line, err := DumpsCompactPy(row)
	if err != nil {
		return err
	}
	path, perr := MissionLogPath(ws)
	if perr != nil {
		return perr
	}
	// The by-hand mkdir that used to sit here moved into record.Locked,
	// where Python has it (file_lock.py:144) and where every other direct
	// AppendRawLine caller now gets it too. The note it carried said it
	// belonged down there; it does, and it is there.
	return record.AppendRawLine(path, []byte(line))
}

// ---------------------------------------------------------------------------
// Drain lock
// ---------------------------------------------------------------------------

const drainLockFile = "mission-drain.lock"

// DrainLockPath is <workspace>/memory/mission-drain.lock, and like
// MissionLogPath it CREATES memory/ — `_drain_lock_path()` is
// `o.memory_dir() / _DRAIN_LOCK_FILE`.
func DrainLockPath(ws string) (string, error) {
	dir, err := EnsureMemoryDir(ws)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, drainLockFile), nil
}

// IsDrainRunning reports whether a mission drain holds the lock.
//
// This is a PRESENCE check on a plain file, not an flock — so it is
// advisory across processes and a crashed drain leaves it held. That is
// Python's design and the heartbeat depends on the file surviving a
// process exit; making it an flock here would release it on crash and let
// two drains run. The staleness is the known cost, and the recorded
// started_at is what an operator reads to judge it.
// RESIDUAL, named: when memory/ cannot be created, Python's
// is_drain_running RAISES (orch_items relocates first, and its last-resort
// mkdir is unguarded) and this answers false. A bool has no third state,
// and a lock predicate answering false lets a SECOND drain start — so this
// is a fork, not a rounding. Reachable only on a workspace whose memory/
// cannot be created; pinned in knowngap_test rather than left unsaid.
func IsDrainRunning(ws string) bool {
	path, perr := DrainLockPath(ws)
	if perr != nil {
		return false
	}
	_, err := os.Stat(path)
	return err == nil
}

// AcquireDrainLock writes the lock file, or reports false if it is held.
//
// The check and the write are NOT atomic — two drains starting in the same
// instant can both see it absent. Python has the same race and the same
// mitigation: a drain is heartbeat-triggered and heartbeats are serialized.
// Named rather than fixed, because an O_EXCL create here would change the
// crashed-drain recovery story that IsDrainRunning documents.
func AcquireDrainLock(ws, missionID string) bool {
	// DrainLockPath creates memory/ on the way, which is where the
	// by-hand MkdirAll that used to sit here went. It also fixes its MODE:
	// that call passed 0o755, while `Path.mkdir()` passes 0o777 and lets
	// the umask narrow it — 0o775 on this box, and the group bit is the
	// difference between two accounts sharing a workspace and not.
	path, perr := DrainLockPath(ws)
	if perr != nil {
		return false
	}
	if _, err := os.Stat(path); err == nil {
		return false
	}
	line, err := DumpsCompactPy(pyObj{
		{Key: "mission_id", Val: missionID},
		{Key: "started_at", Val: nowISOPy(time.Now().UTC())},
	})
	if err != nil {
		return false
	}
	if err := os.WriteFile(path, []byte(line), 0o644); err != nil {
		return false
	}
	return true
}

// ReleaseDrainLock removes the lock file. A missing file is not an error —
// Python's unlink(missing_ok=True).
func ReleaseDrainLock(ws string) {
	// Python wraps the whole body — path resolution included — in a bare
	// try/except, so a memory_dir failure is swallowed here and only here.
	path, err := DrainLockPath(ws)
	if err != nil {
		return
	}
	_ = os.Remove(path)
}
