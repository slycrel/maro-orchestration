package skills

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// TestR4DriveProbe is the Go half of the cross-runtime differential for the
// two announcements r4 found missing. It is a PROBE, not a test: skipped
// unless the harness names a workspace, so `go test ./...` never runs it.
//
// The unit pins above assert the shape this runtime produces. Only Python
// can confirm that shape is the one Python produces — which is the whole
// distinction r4's method rests on, and the reason a store-level
// differential could not have caught either finding: the STORES agree.
func TestR4DriveProbe(t *testing.T) {
	ws := os.Getenv("MARO_R4_DRIVE_WS")
	if ws == "" {
		t.Skip("set MARO_R4_DRIVE_WS to run the cross-runtime probe")
	}
	if !strings.HasPrefix(ws, "/tmp/") {
		t.Fatalf("refusing to drive a workspace outside /tmp: %s", ws)
	}
	rec := record.New(ws)

	switch os.Getenv("MARO_R4_DRIVE_MODE") {
	case "mismatch":
		s := base("jina", "jina-web-fetch")
		s.Description = ""
		s.TriggerPatterns = []string{"fetch url", "web scrape"}
		s.Tier, s.CircuitState, s.UtilityScore = "established", "closed", 0.5
		s.ContentHash = ComputeSkillHash(s)
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
		var u UtilityUpdate
		var err error
		for i := 0; i < 3; i++ {
			u, err = UpdateSkillUtility(ws, "jina", false, "boom",
				"summarize the meeting notes from yesterday")
			if err != nil {
				t.Fatal(err)
			}
		}
		if err := LogCircuitTransition(rec, "jina", u, "boom"); err != nil {
			t.Fatal(err)
		}
		for _, row := range readLog(t, ws) {
			delete(row, "timestamp")
			delete(row, "entry_id")
			delete(row, "loop_id")
			// Emitted with HTML escaping OFF, like the store's own
			// writer: encoding/json's default turns "->" into
			// "-\u003e", which would diff against Python for a reason
			// that has nothing to do with what is being compared.
			var buf strings.Builder
			enc := json.NewEncoder(&buf)
			enc.SetEscapeHTML(false)
			if err := enc.Encode(row); err != nil {
				t.Fatal(err)
			}
			fmt.Print(buf.String())
		}

	case "promote":
		s := base("pm", "Promote Me")
		s.Description = "A provisional worth promoting since it performs"
		s.TriggerPatterns = []string{"promote", "elevate"}
		s.StepsTemplate = []string{"first do this", "then do that"}
		s.Tier, s.UtilityScore = "provisional", 0.9
		s.UseCount, s.SuccessRate = 7, 0.855
		s.ContentHash = ComputeSkillHash(s)
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
		rep, err := MaybeAutoPromoteSkills(ws, 10, rec)
		if err != nil {
			t.Fatal(err)
		}
		fmt.Fprintf(os.Stderr, "PROMOTED %v %v\n", rep.PromotedIDs, rep.Warnings)
		entries, err := os.ReadDir(filepath.Join(ws, "skills"))
		if err != nil {
			t.Fatal(err)
		}
		var names []string
		for _, e := range entries {
			if strings.HasSuffix(e.Name(), ".md") {
				names = append(names, e.Name())
			}
		}
		sort.Strings(names)
		for _, n := range names {
			raw, err := os.ReadFile(filepath.Join(ws, "skills", n))
			if err != nil {
				t.Fatal(err)
			}
			fmt.Printf("=== %s\n%s", n, raw)
		}

	default:
		t.Fatalf("set MARO_R4_DRIVE_MODE to mismatch|promote")
	}
}
