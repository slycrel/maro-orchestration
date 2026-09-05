package learn

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/projector"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

type notAView struct{}

func (notAView) Name() string { return "nothing" }

// The B7 flat store is a whole-file view: one row per item's CURRENT
// lesson revision, shaped exactly as the Python `Lesson` dataclass, and
// only what a Python reader may inject — selectable rows, plus
// quarantined rows stamped `contested`. Unproven (candidate; observed is
// the same class and needs tenure's evidence, covered in tail), retired
// (tombstone) and policy rows are not in the store at all.
func TestLessonsViewIsB7Exact(t *testing.T) {
	j, st := openJ(t)
	r, _ := workspace.Resolve()
	a, _ := r.Announce(io.Discard)
	at := func(text, family string, stage Stage, source string) *LearnedRevision {
		item := LearnedID(record.NewID())
		x := rev(item, "", Lesson, ScopeWorkspace, family, lessonRef(st, text))
		x.Provenance = Provenance{Source: source, Why: source + " why"}
		if source == SourceImport {
			x.Provenance.Origin = "pack:" + string(record.NewID())
		}
		recs := []record.Record{x}
		from := Candidate
		for _, to := range ladder(stage) {
			recs = append(recs, tr(item, x.ID, from, to))
			from = to
		}
		if err := submit(t, j, "add/"+string(item), recs...); err != nil {
			t.Fatal(err)
		}
		return x
	}
	cand := at("candidate text", "", Candidate, "operator")
	prov := at("provisional text", "qa", Provisional, "tail")
	eff := at("effective text", "", Effective, "operator")
	canon := at("canon text\nwith a second line", "research", Canon, "operator")
	cont := at("contested text", "", Contested, "operator")
	quar := at("quarantined text", "", Quarantined, "operator")
	tomb := at("tombstoned text", "", Tombstone, "operator")
	imp := at("imported text", "", Provisional, SourceImport)
	polItem := LearnedID(record.NewID())
	if err := submit(t, j, "pol", rev(polItem, "", Policy, ScopeWorkspace, "", thought.Ref{})); err != nil {
		t.Fatal(err)
	}
	// a superseded revision: the row is the CURRENT revision's text and standing
	old := at("old wording", "", Canon, "operator")
	newer := rev(old.Item, old.ID, Lesson, ScopeWorkspace, "", lessonRef(st, "new wording"))
	if err := submit(t, j, "revise", newer, tr(old.Item, newer.ID, Candidate, Provisional)); err != nil {
		t.Fatal(err)
	}
	// one exposure of the effective revision
	inv := record.NewID()
	if err := submit(t, j, "app", &Application{Header: record.Header{ID: record.NewID(), RunID: "r", Attempt: 1, Subject: record.Ref{Kind: "invocation", ID: string(inv)}, At: time.Now().UTC()},
		Item: eff.Item, Revision: eff.ID, Invocation: inv, Representation: lessonRef(st, "- effective text\n")}); err != nil {
		t.Fatal(err)
	}

	if _, err := projector.New(j, notAView{}); err == nil || !strings.Contains(err.Error(), "exactly one of") {
		t.Fatalf("a view that renders nothing was accepted: %v", err)
	}
	p, err := projector.New(j, LessonsView{Store: st})
	if err != nil {
		t.Fatal(err)
	}
	head := j.Head()
	if w, err := p.Publish(); err != nil || w != head {
		t.Fatalf("publish: %d %v (head %d)", w, err, head)
	}
	raw, err := os.ReadFile(projector.Current(a) + "/" + LessonsFile)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
	rows := map[string]map[string]any{}
	order := []string{}
	for _, ln := range lines {
		if !strings.HasPrefix(ln, `{"lesson_id":`) {
			t.Fatalf("row does not lead with lesson_id (Python dataclass order): %s", ln)
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(ln), &m); err != nil {
			t.Fatal(err)
		}
		rows[m["lesson"].(string)] = m
		order = append(order, m["lesson_id"].(string))
	}
	want := map[string]struct {
		id        LearnedID
		conf      float64
		task      string
		applied   float64
		minted    string
		contested bool
		imported  bool
	}{
		"provisional text":               {prov.Item, 0.7, "qa", 0, "outcome", false, false},
		"effective text":                 {eff.Item, 0.8, "", 1, "", false, false},
		"canon text\nwith a second line": {canon.Item, 0.9, "research", 0, "", false, false},
		"contested text":                 {cont.Item, 0.5, "", 0, "", false, false},
		"quarantined text":               {quar.Item, 0.3, "", 0, "", true, false},
		"imported text":                  {imp.Item, 0.7, "", 0, "", false, true},
		"new wording":                    {old.Item, 0.7, "", 0, "", false, false},
	}
	if len(rows) != len(want) {
		t.Fatalf("rows: got %d want %d\n%s", len(rows), len(want), raw)
	}
	for _, absent := range []string{"candidate text", "tombstoned text", "old wording"} {
		if _, ok := rows[absent]; ok {
			t.Fatalf("%q is in the injectable store", absent)
		}
	}
	for text, w := range want {
		m, ok := rows[text]
		if !ok {
			t.Fatalf("no row for %q", text)
		}
		if m["lesson_id"] != LessonHandle(w.id) || m["confidence"] != w.conf || m["task_type"] != w.task || m["times_applied"] != w.applied || m["times_reinforced"] != 0.0 || m["outcome"] != "" || m["source_goal"] != "" {
			t.Fatalf("%q: %v", text, m)
		}
		if _, ok := m["minted_from"]; ok != (w.minted != "") || (w.minted != "" && m["minted_from"] != w.minted) {
			t.Fatalf("%q minted_from: %v", text, m["minted_from"])
		}
		c, hasC := m["contested"].(map[string]any)
		if hasC != w.contested || (hasC && (c["reason"] != "item_harmful" || c["source"] != "maro-go" || c["contested_at"] == "")) {
			t.Fatalf("%q contested: %v", text, m["contested"])
		}
		im, hasI := m["imported"].(map[string]any)
		if hasI != w.imported || (hasI && im["why"] != "import why") {
			t.Fatalf("%q imported: %v", text, m["imported"])
		}
		if _, ok := m["provisional"]; ok {
			t.Fatalf("%q carries the tiered `provisional` key in the flat store", text)
		}
		if !regexp.MustCompile(`^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00$`).MatchString(m["recorded_at"].(string)) {
			t.Fatalf("%q recorded_at is not Python isoformat: %v", text, m["recorded_at"])
		}
	}
	// file order is by item id (deterministic, content-independent)
	items := []string{}
	for _, w := range want {
		items = append(items, string(w.id))
	}
	sort.Strings(items)
	for i, id := range items {
		if order[i] != LessonHandle(LearnedID(id)) {
			t.Fatalf("row %d is %s, want the handle of item %s", i, order[i], id)
		}
	}
	_ = cand
	_ = tomb
	_ = polItem

	// The view is pinned at the announced head: a lesson landing after the
	// projector chose its head does not leak into that generation.
	var again bytes.Buffer
	at("late text", "", Canon, "operator")
	if err := (LessonsView{Store: st}).Render(j, head, &again); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(again.String(), "late text") || strings.Count(again.String(), "\n") != len(want) {
		t.Fatalf("render past the announced head:\n%s", again.String())
	}
	if w, err := p.Publish(); err != nil || w != head+3 { // revision + effective + canon
		t.Fatalf("republish: %d %v", w, err)
	}
	raw2, _ := os.ReadFile(projector.Current(a) + "/" + LessonsFile)
	if !strings.Contains(string(raw2), "late text") {
		t.Fatal("republish did not carry the late lesson")
	}
}

// ladder is the legal transition path from candidate to stage.
func ladder(stage Stage) []Stage {
	switch stage {
	case Candidate:
		return nil
	case Provisional, Effective, Quarantined, Tombstone:
		return []Stage{stage}
	case Canon:
		return []Stage{Effective, Canon}
	case Contested:
		return []Stage{Provisional, Contested}
	}
	panic(stage)
}
