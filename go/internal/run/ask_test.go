package run

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The ask file contract: absent = no ask; the Python aliases and loose
// booleans are accepted; an unusable file is an error, not an ask; the
// consumed file is archived, never deleted.
func TestReadAndArchiveAsk(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, AskName)
	if a, err := ReadAsk(p); a != nil || err != nil {
		t.Fatalf("absent: %v %v", a, err)
	}
	if a, err := ReadAsk(""); a != nil || err != nil {
		t.Fatalf("no path: %v %v", a, err)
	}
	os.WriteFile(p, []byte(`{"question":" Which mailbox? ","alternative":"tried the only one named","tried":"yes"}`), 0o600)
	a, err := ReadAsk(p)
	if err != nil || a.Question != "Which mailbox?" || a.NoInputAlternative != "tried the only one named" || !a.Tried {
		t.Fatalf("aliases: %+v %v", a, err)
	}
	os.WriteFile(p, []byte(`{"why":"no question here"}`), 0o600)
	if _, err := ReadAsk(p); err == nil || !strings.Contains(err.Error(), "no question") {
		t.Fatalf("questionless file must be an error: %v", err)
	}
	os.WriteFile(p, []byte(`not json`), 0o600)
	if _, err := ReadAsk(p); err == nil {
		t.Fatal("bad json must be an error")
	}
	long := strings.Repeat("q", 900)
	os.WriteFile(p, []byte(`{"question":"`+long+`","tried":false}`), 0o600)
	if a, err := ReadAsk(p); err != nil || len(a.Question) != 800 || a.Tried {
		t.Fatalf("cap: %d %v", len(a.Question), err)
	}
	moved, err := ArchiveAsk(p)
	if err != nil || moved == "" {
		t.Fatalf("archive: %q %v", moved, err)
	}
	if _, err := os.Stat(p); err == nil {
		t.Fatal("ask file still present after archive")
	}
	if b, err := os.ReadFile(moved); err != nil || !strings.Contains(string(b), long[:20]) {
		t.Fatalf("archived copy lost: %v", err)
	}
	if !strings.HasPrefix(filepath.Base(moved), "ask-operator.") || !strings.HasSuffix(moved, ".asked.json") {
		t.Fatalf("archive name %s", moved)
	}
	if moved2, err := ArchiveAsk(p); err != nil || moved2 != "" {
		t.Fatalf("archiving nothing must be a no-op: %q %v", moved2, err)
	}
}

// The records' wire vocabulary: a question is run-scoped with a deadline;
// an answer targets the run it is committed against.
func TestQuestionAndAnswerWire(t *testing.T) {
	run := record.RunID(record.NewID())
	q := &Question{Header: header(runRef(run), run, 1, "question/1"), Question: "which one?", Deadline: now().Add(AskTimebox)}
	if err := q.ValidateWire(); err != nil {
		t.Fatal(err)
	}
	if bad := *q; true {
		bad.Deadline = time.Time{}
		if err := bad.ValidateWire(); err == nil {
			t.Fatal("no deadline must fail")
		}
	}
	if bad := *q; true {
		bad.Question = " "
		if err := bad.ValidateWire(); err == nil {
			t.Fatal("empty question must fail")
		}
	}
	if bad := *q; true {
		bad.Subject = record.Ref{Kind: "goal", ID: "x"}
		if err := bad.ValidateWire(); err == nil {
			t.Fatal("off-run subject must fail")
		}
	}
	a := &Answer{Header: header(runRef(run), run, 1, "answer/1"), Target: run, Question: q.ID, Text: "the second", Source: "cli"}
	if err := a.ValidateWire(); err != nil {
		t.Fatal(err)
	}
	for _, mut := range []func(*Answer){
		func(x *Answer) { x.Text = "" },
		func(x *Answer) { x.Source = "" },
		func(x *Answer) { x.Target = record.RunID(record.NewID()) },
		func(x *Answer) { x.RunID = record.RunID(record.NewID()) },
	} {
		bad := *a
		mut(&bad)
		if err := bad.ValidateWire(); err == nil {
			t.Fatal("mutated answer must fail wire validation")
		}
	}
	if !IsNeedsAnswer(NeedsAnswer(q)) || IsNeedsAnswer("needs clarification: x") || !strings.HasSuffix(NeedsAnswer(q), "which one?") {
		t.Fatalf("reason %q", NeedsAnswer(q))
	}
	if !strings.Contains(AskInstructions("/x/ask.json"), "write ONE JSON object to /x/ask.json") || !strings.Contains(AskInstructions("/x"), "rare exception") {
		t.Fatal("instructions must name the path and the posture")
	}
	if got := string(AnswerContext("which one?", "the second")); !strings.Contains(got, "The operator answered: the second") || !strings.Contains(got, "do not ask it again") {
		t.Fatalf("context %q", got)
	}
}
