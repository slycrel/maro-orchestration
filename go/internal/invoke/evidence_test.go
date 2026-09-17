package invoke

import (
	"io"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// evidenceStore is a thought store in a fresh workspace, the way newShell
// builds one.
func evidenceStore(t *testing.T) *thought.Store {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, _ := workspace.Resolve()
	a, err := r.Announce(io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	a.Ensure()
	st, err := thought.Open(a)
	if err != nil {
		t.Fatal(err)
	}
	return st
}

// The evidence digest is the record, bounded, and the same bytes from the
// same records every time.

func TestDigestNoEffectsSaysSoAndNamesTheToolsPosture(t *testing.T) {
	toolless := Digest(false, nil, nil, TerminalComplete, "", nil, 0)
	if !strings.HasPrefix(toolless, "execute ended complete\n") || !strings.Contains(toolless, "tools: not offered") || !strings.Contains(toolless, "no recorded effects") {
		t.Fatalf("tool-less digest: %q", toolless)
	}
	withTools := Digest(true, nil, nil, TerminalPartial, "stream ended", nil, 0)
	if !strings.HasPrefix(withTools, "execute ended partial: stream ended\n") || !strings.Contains(withTools, "tools: offered") || !strings.Contains(withTools, "no recorded effects") {
		t.Fatalf("tools-offered digest: %q", withTools)
	}
	if toolless == withTools {
		t.Fatal("the two postures must read differently")
	}
}

func TestDigestRendersEachEffectWithItsDecodedOutput(t *testing.T) {
	store := evidenceStore(t)
	ok, err := store.Put(thought.StepResult, evidence("output", []byte("wrote 3 lines\nto out.md"), ""))
	if err != nil {
		t.Fatal(err)
	}
	bad, err := store.Put(thought.StepResult, evidence("output", []byte("permission denied"), ""))
	if err != nil {
		t.Fatal(err)
	}
	effects := []*ToolEffect{
		{Ordinal: 0, Op: "Write", Class: ClassOf("Write")},
		{Ordinal: 1, Op: "Bash", Class: ClassOf("Bash")},
		{Ordinal: 2, Op: "Read", Class: ClassOf("Read")},
		{Ordinal: 3, Op: "Edit", Class: ClassOf("Edit"), Refused: true},
	}
	results := map[int]*ToolEffectResult{
		0: {Ordinal: 0, Output: ok},
		1: {Ordinal: 1, Output: bad, IsError: true},
		// 2 is unanswered; 3 refused and unanswered
	}
	got := Digest(true, effects, results, TerminalComplete, "", store.Get, 0)
	for _, want := range []string{
		"#0 Write [", ": ok\n  | wrote 3 lines\n  | to out.md\n",
		"#1 Bash [", ": error\n  | permission denied\n",
		"#2 Read [", ": unanswered\n",
		"#3 Edit [", ", refused: unanswered\n",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("digest lacks %q:\n%s", want, got)
		}
	}
	again := Digest(true, effects, results, TerminalComplete, "", store.Get, 0)
	if again != got {
		t.Fatal("the digest of the same records is not the same bytes")
	}
}

func TestDigestIsBoundedAndSaysWhereItCut(t *testing.T) {
	store := evidenceStore(t)
	big, err := store.Put(thought.StepResult, evidence("output", []byte(strings.Repeat("x", 3*EvidencePerEffectBytes)), ""))
	if err != nil {
		t.Fatal(err)
	}
	var effects []*ToolEffect
	results := map[int]*ToolEffectResult{}
	for i := 0; i < 40; i++ {
		effects = append(effects, &ToolEffect{Ordinal: i, Op: "Bash", Class: ClassOf("Bash")})
		results[i] = &ToolEffectResult{Ordinal: i, Output: big}
	}
	one := Digest(true, effects[:1], results, TerminalComplete, "", store.Get, 0)
	// the output line is cut at the per-effect bound and says so; the
	// class name may itself contain an x, so measure the line, not the count
	var outLine string
	for _, l := range strings.Split(one, "\n") {
		if strings.HasPrefix(l, "  | x") {
			outLine = l
		}
	}
	if len(outLine) != len("  | ")+EvidencePerEffectBytes || !strings.Contains(one, "…(+"+"4096 bytes)") {
		t.Fatalf("one effect's output was not bounded per effect: line %d bytes, tail %q", len(outLine), one[max(0, len(one)-80):])
	}
	all := Digest(true, effects, results, TerminalComplete, "", store.Get, 0)
	if len(all) > EvidenceMaxBytes+200 || !strings.HasSuffix(all, "(evidence truncated at 16384 bytes)\n") {
		t.Fatalf("whole digest not bounded: %d bytes, tail %q", len(all), all[len(all)-60:])
	}
}

func TestDigestUnreadableOutputIsNamedNotGuessed(t *testing.T) {
	effects := []*ToolEffect{{Ordinal: 0, Op: "Read", Class: ClassOf("Read")}}
	results := map[int]*ToolEffectResult{0: {Ordinal: 0}}
	if got := Digest(true, effects, results, TerminalComplete, "", nil, 0); !strings.Contains(got, "(output not readable here)") {
		t.Fatalf("nil reader: %q", got)
	}
	failing := func(thought.Ref) ([]byte, error) { return nil, thought.ErrKind }
	if got := Digest(true, effects, results, TerminalComplete, "", failing, 0); !strings.Contains(got, "(output unreadable:") {
		t.Fatalf("failing reader: %q", got)
	}
}
