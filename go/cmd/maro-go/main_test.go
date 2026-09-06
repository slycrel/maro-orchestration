package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// The CLI is the operator's instrument: the report prints its warning count
// on success (never silent), check fails on drift, workspace announces
// before anything else.
func TestCLIReportPrintsCountsAndExitCodes(t *testing.T) {
	dir, _ := filepath.Abs(filepath.Join("..", "..", "contracts"))
	var out, errw bytes.Buffer
	if code := run([]string{"contracts", "report", dir}, &out, &errw); code != 0 {
		t.Fatalf("report exit %d: %s %s", code, out.String(), errw.String())
	}
	if !strings.Contains(out.String(), "report: 0 error(s),") {
		t.Fatalf("report must print its counts on success:\n%s", out.String())
	}
	out.Reset()
	if code := run([]string{"contracts", "check", dir}, &out, &errw); code != 0 || !strings.Contains(out.String(), "no drift") {
		t.Fatalf("check: %d %s %s", code, out.String(), errw.String())
	}
	out.Reset()
	if code := run([]string{"contracts", "bogus", dir}, &out, &errw); code != 1 {
		t.Fatalf("unknown subcommand exit %d", code)
	}
	if code := run(nil, &out, &errw); code != 2 {
		t.Fatalf("usage exit %d", code)
	}
}

func TestCLIWorkspaceAnnouncesFirst(t *testing.T) {
	ws := filepath.Join(t.TempDir(), "ws")
	t.Setenv(workspace.EnvOverride, ws)
	var out, errw bytes.Buffer
	if code := run([]string{"workspace"}, &out, &errw); code != 0 {
		t.Fatalf("exit %d: %s", code, errw.String())
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if !strings.HasPrefix(lines[0], "workspace: "+ws) {
		t.Fatalf("first line must announce the root: %q", lines[0])
	}
	if !strings.Contains(out.String(), "lease: none") {
		t.Fatalf("expected no lease: %s", out.String())
	}
	if _, err := os.Stat(filepath.Join(ws, "thoughts")); err != nil {
		t.Fatalf("Ensure did not create thoughts/: %v", err)
	}
}

func TestCLIJournalStatusAndPublish(t *testing.T) {
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	var out, errw bytes.Buffer
	if code := run([]string{"journal", "status"}, &out, &errw); code != 0 {
		t.Fatalf("exit %d: %s", code, errw.String())
	}
	if !strings.Contains(out.String(), "journal: head=0 frames=0") {
		t.Fatalf("%s", out.String())
	}
	out.Reset()
	if code := run([]string{"journal", "publish"}, &out, &errw); code != 0 || !strings.Contains(out.String(), "published: 0") {
		t.Fatalf("publish: %d %s %s", code, out.String(), errw.String())
	}
}

// runs show --json is the run for another program: the summary with the
// delivered payload as result, parseable, with the handle the CLI printed.
func TestCLIRunsShowJSON(t *testing.T) {
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	var out, errw bytes.Buffer
	if code := run([]string{"now", "--backend", "scripted", "--fresh", "say hi"}, &out, &errw); code != 0 {
		t.Fatalf("now exit %d: %s %s", code, out.String(), errw.String())
	}
	var handle string
	for _, l := range strings.Split(out.String(), "\n") {
		if strings.HasPrefix(l, "run ") {
			handle = strings.Fields(l)[1]
		}
	}
	if handle == "" {
		t.Fatalf("no run line:\n%s", out.String())
	}
	out.Reset()
	if code := run([]string{"runs", "show", "--json", handle}, &out, &errw); code != 0 {
		t.Fatalf("show exit %d: %s %s", code, out.String(), errw.String())
	}
	var s struct {
		Handle  string `json:"handle"`
		Outcome string `json:"outcome"`
		Result  string `json:"result"`
		Usage   struct {
			Calls int `json:"calls"`
		} `json:"usage"`
		Landscape *struct{} `json:"landscape"`
	}
	// the workspace announcement comes first (every command); the JSON
	// starts at the first line that is an object — the consumer's contract
	body := out.String()[strings.Index(out.String(), "\n{")+1:]
	if err := json.Unmarshal([]byte(body), &s); err != nil {
		t.Fatalf("not json: %v\n%s", err, out.String())
	}
	if s.Handle != handle || s.Outcome != "delivered" || !strings.Contains(s.Result, "scripted response to: say hi") || s.Usage.Calls < 1 || s.Landscape == nil {
		t.Fatalf("summary: %+v", s)
	}
	out.Reset()
	if code := run([]string{"runs", "show", "--json", "deadbeef"}, &out, &errw); code == 0 {
		t.Fatalf("unknown handle must fail")
	}
}
