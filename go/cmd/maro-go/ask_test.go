package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/secrets"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// fakeAskingClaudeSh saves its prompt, and on its FIRST tool-bearing call
// asks the operator through $MARO_ASK (marker file = already asked); every
// later call just answers, so the follow-up run completes.
const fakeAskingClaudeSh = `#!/bin/sh
cat >> "$FAKE_PROMPT_OUT"; printf "\n----\n" >> "$FAKE_PROMPT_OUT"
if [ -n "$MARO_ASK" ] && [ ! -f "$FAKE_ASKED" ]; then
  printf '{"question":"What is the 6-digit code Yahoo just texted you?","why":"the login challenge wants it","no_input_alternative":"tried the app password; the store has none","tried":true}' > "$MARO_ASK"
  : > "$FAKE_ASKED"
  printf '{"type":"result","subtype":"success","is_error":false,"result":"asked the operator for the code","usage":{"input_tokens":1,"output_tokens":1}}\n'
else
  printf '{"type":"result","subtype":"success","is_error":false,"result":"logged in with the code","usage":{"input_tokens":1,"output_tokens":1}}\n'
fi
`

func askFixture(t *testing.T) (promptOut string) {
	t.Helper()
	bin := t.TempDir()
	if err := os.WriteFile(filepath.Join(bin, "claude"), []byte(fakeAskingClaudeSh), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv(secrets.EnvDir, t.TempDir()) // no store: nothing injected, never the live one
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	promptOut = filepath.Join(t.TempDir(), "prompt.txt")
	t.Setenv("FAKE_PROMPT_OUT", promptOut)
	t.Setenv("FAKE_ASKED", filepath.Join(t.TempDir(), "asked.marker"))
	return promptOut
}

var handleRe = regexp.MustCompile(`(?m)^\? (\S+) pending`)

// The lane end to end on a NOW run: the frame carries the asking
// instructions and the env the path; the worker's ask ends the attempt on
// "needs answer" and archives the file; `asks` lists it pending with the
// no-input alternative; `answer` commits the reply and the follow-up run
// carries it as operator context; a second answer is refused; `runs show`
// prints the question and the answer.
func TestCLIAskAnswerLoop(t *testing.T) {
	promptOut := askFixture(t)
	var out, errw bytes.Buffer
	if code := run([]string{"now", "--backend", "subprocess", "--fresh", "log in to the yahoo mailbox"}, &out, &errw); code != 0 {
		t.Fatalf("now exit %d: %s %s", code, out.String(), errw.String())
	}
	prompt, _ := os.ReadFile(promptOut)
	if !strings.Contains(string(prompt), "## Asking the operator") || !strings.Contains(string(prompt), "write ONE JSON object to ") || !strings.Contains(string(prompt), "rare exception") {
		t.Fatalf("frame lacks the ask instructions:\n%s", prompt)
	}
	if !strings.Contains(out.String()+errw.String(), "needs answer: What is the 6-digit code") {
		t.Fatalf("the attempt must end on the question:\n%s%s", out.String(), errw.String())
	}
	ws := os.Getenv(workspace.EnvOverride)
	if _, err := os.Stat(filepath.Join(ws, "drop", "ask-operator.json")); err == nil {
		t.Fatal("ask file not archived")
	}
	archived, _ := filepath.Glob(filepath.Join(ws, "drop", "ask-operator.*.asked.json"))
	if len(archived) != 1 {
		t.Fatalf("archived copies: %v", archived)
	}
	out.Reset()
	errw.Reset()
	if code := run([]string{"asks"}, &out, &errw); code != 0 {
		t.Fatalf("asks exit %d: %s", code, errw.String())
	}
	m := handleRe.FindStringSubmatch(out.String())
	if m == nil || !strings.Contains(out.String(), "tried without the operator (true): tried the app password") || !strings.Contains(out.String(), "answer with: maro-go answer "+m[1]) {
		t.Fatalf("asks:\n%s", out.String())
	}
	handle := m[1]
	out.Reset()
	errw.Reset()
	if code := run([]string{"asks", "--json"}, &out, &errw); code != 0 {
		t.Fatalf("asks --json exit %d: %s", code, errw.String())
	}
	var rows []askRow
	if err := json.Unmarshal(out.Bytes(), &rows); err != nil || len(rows) != 1 || rows[0].Status != "pending" || rows[0].Handle != handle || !rows[0].Tried || rows[0].Deadline.Sub(rows[0].Asked).Hours() != 24 {
		t.Fatalf("asks json %v: %s", err, out.String())
	}
	// a wrong handle, then the real answer
	out.Reset()
	errw.Reset()
	if code := run([]string{"answer", "nope-1", "123456"}, &out, &errw); code == 0 || !strings.Contains(errw.String(), "no run nope-1") {
		t.Fatalf("unknown handle: %d %s", code, errw.String())
	}
	out.Reset()
	errw.Reset()
	if code := run([]string{"answer", handle, "--backend", "subprocess", "123456"}, &out, &errw); code != 0 {
		t.Fatalf("answer exit %d: %s %s", code, out.String(), errw.String())
	}
	if !strings.Contains(out.String(), "answered "+handle+": What is the 6-digit code") {
		t.Fatalf("answer output:\n%s", out.String())
	}
	// the follow-up's EXECUTE prompt (the tail's lens calls come after it)
	prompt, _ = os.ReadFile(promptOut)
	var followUp string
	for _, call := range strings.Split(string(prompt), "\n----\n") {
		if strings.Contains(call, "== Operator answer ==") {
			followUp = call
		}
	}
	if followUp == "" {
		t.Fatalf("no follow-up execute carried the answer:\n%s", prompt)
	}
	for _, want := range []string{"The run paused to ask the operator: What is the 6-digit code Yahoo just texted you?", "The operator answered: 123456", "do not ask it again", "log in to the yahoo mailbox", "## Asking the operator"} {
		if !strings.Contains(followUp, want) {
			t.Fatalf("follow-up prompt lacks %q:\n%s", want, followUp)
		}
	}
	if n := strings.Count(string(prompt), "== Operator answer =="); n != 1 {
		t.Fatalf("the answer rode into %d calls, want exactly the follow-up execute", n)
	}
	if strings.Contains(out.String()+errw.String(), "needs answer") {
		t.Fatalf("the follow-up must not end on a question:\n%s", errw.String())
	}
	out.Reset()
	errw.Reset()
	if code := run([]string{"answer", handle, "again"}, &out, &errw); code == 0 || !strings.Contains(errw.String(), "already answered (cli): 123456") {
		t.Fatalf("second answer must be refused: %d %s", code, errw.String())
	}
	out.Reset()
	errw.Reset()
	if code := run([]string{"asks"}, &out, &errw); code != 0 || !strings.Contains(out.String(), "✓ "+handle+" answered") || !strings.Contains(out.String(), "answer (cli): 123456") {
		t.Fatalf("asks after answer (%d):\n%s", code, out.String())
	}
	out.Reset()
	errw.Reset()
	if code := run([]string{"runs", "show", handle}, &out, &errw); code != 0 || !strings.Contains(out.String(), "question (attempt 1, step 0, until ") || !strings.Contains(out.String(), "answer (cli): 123456") {
		t.Fatalf("runs show (%d):\n%s%s", code, out.String(), errw.String())
	}
	// the follow-up is its own run, after the asked one; it asked nothing
	out.Reset()
	errw.Reset()
	if code := run([]string{"asks", "--json"}, &out, &errw); code != 0 {
		t.Fatalf("asks exit %d", code)
	}
	rows = nil
	if err := json.Unmarshal(out.Bytes(), &rows); err != nil || len(rows) != 1 || rows[0].Status != "answered" || rows[0].Source != "cli" || rows[0].Late {
		t.Fatalf("ledger after answer %v: %s", err, out.String())
	}
}

// A run that asked nothing cannot be answered.
func TestCLIAnswerRefusesUnasked(t *testing.T) {
	askFixture(t)
	os.WriteFile(os.Getenv("FAKE_ASKED"), nil, 0o600) // the fake has "already asked": it never writes $MARO_ASK
	var out, errw bytes.Buffer
	if code := run([]string{"now", "--backend", "subprocess", "--fresh", "say hello"}, &out, &errw); code != 0 {
		t.Fatalf("now exit %d: %s", code, errw.String())
	}
	m := regexp.MustCompile(`(?m)^event (\S+) run=`).FindStringSubmatch(errw.String())
	if m == nil {
		t.Fatalf("no handle in events:\n%s", errw.String())
	}
	out.Reset()
	errw.Reset()
	if code := run([]string{"asks"}, &out, &errw); code != 0 || !strings.Contains(out.String(), "no questions asked") {
		t.Fatalf("asks (%d):\n%s", code, out.String())
	}
	out.Reset()
	errw.Reset()
	if code := run([]string{"answer", m[1], "hello back"}, &out, &errw); code == 0 || !strings.Contains(errw.String(), "asked nothing") {
		t.Fatalf("answering an unasked run must fail: %d %s", code, errw.String())
	}
}
