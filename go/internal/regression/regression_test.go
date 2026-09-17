package regression

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

// The grammar refuses every shell PROGRAM shape and every non-executing
// runner form (must-detect fixtures built to evade it), and admits the
// literal invocation forms — wrapper kept in the argv.
func TestParseRefusesProgramsAndNonExecForms(t *testing.T) {
	refused := []string{
		"", "pytest -q | tail -3", "pytest -q || true", "pytest -q; echo done", "pytest -q && echo ok",
		"pytest tests/*.py", "pytest $TESTS", "pytest `which x`", "pytest -q > out.txt", "pytest -q 2>&1",
		"cd ~/proj && pytest", "cd proj; pytest", "cd && pytest", "cd proj pytest",
		"pytest -q # smoke", "pytest --collect-only", "pytest -q --co", "cargo test --no-run", "npm test --dry-run",
		"bash -c 'pytest -q'", "sh run_tests.sh", "python script.py", "echo pytest", "npm run lint", "make build",
		"pytest -q\necho x", "pytest 'unterminated", "pytest -q \\",
		"pytest " + string(make([]byte, MaxCommandBytes)),
		strings.Repeat(" ", MaxCommandBytes-5) + "pytest", // the RAW input is over the limit
		"make -n test", "make --just-print test", "make -q test", "make --touch test", "tox -l", "tox --listenvs", "tox --showconfig", "pytest --co", "go test -list . ./...", "cargo test --list",
		"npm test --help", "pytest --version", "make -nk test", "make -kn test", "MAKEFLAGS=-n make test", "PYTEST_ADDOPTS=--co pytest", "GOFLAGS=-n go test ./...",
		"make -kh test", "make -v test", "make --dry test", "make --ver test", "pytest --collect", "pytest --co=1", "tox --listenv", "make --just test",
	}
	for _, c := range refused {
		if p, ok := Parse(c); ok {
			t.Errorf("%q admitted as %+v", c, p)
		}
	}
	admitted := map[string]Command{
		"pytest -q":                         {Argv: []string{"pytest", "-q"}, Family: "pytest"},
		"python3 -m pytest tests/test_x.py": {Argv: []string{"python3", "-m", "pytest", "tests/test_x.py"}, Family: "pytest"},
		"uv run pytest -q":                  {Argv: []string{"uv", "run", "pytest", "-q"}, Family: "pytest"},
		"cd go && go test ./...":            {Argv: []string{"go", "test", "./..."}, Family: "go", CD: "go"},
		"PYTHONPATH=src MARO_X=1 pytest -q": {Argv: []string{"pytest", "-q"}, Family: "pytest", Env: []string{"MARO_X=1", "PYTHONPATH=src"}},
		"cargo test":                        {Argv: []string{"cargo", "test"}, Family: "cargo"},
		"npm run test":                      {Argv: []string{"npm", "run", "test"}, Family: "npm"},
		"make test":                         {Argv: []string{"make", "test"}, Family: "make"},
		"tox -e py":                         {Argv: []string{"tox", "-e", "py"}, Family: "tox"},
		"bun test":                          {Argv: []string{"bun", "test"}, Family: "bun"},
		`pytest -k "a and b"`:               {Argv: []string{"pytest", "-k", "a and b"}, Family: "pytest"},
		"pytest -k 'x' 'tests/t y.py'":      {Argv: []string{"pytest", "-k", "x", "tests/t y.py"}, Family: "pytest"},
		"make -j4 test":                     {Argv: []string{"make", "-j4", "test"}, Family: "make"},
		"pytest --cov=src -q":               {Argv: []string{"pytest", "--cov=src", "-q"}, Family: "pytest"},
		"make --keep-going test":            {Argv: []string{"make", "--keep-going", "test"}, Family: "make"},
		strings.Repeat(" ", 10) + "pytest":  {Argv: []string{"pytest"}, Family: "pytest"},
		"/usr/local/bin/pytest tests/":      {Argv: []string{"/usr/local/bin/pytest", "tests/"}, Family: "pytest"},
	}
	for c, want := range admitted {
		p, ok := Parse(c)
		if !ok {
			t.Errorf("%q refused", c)
			continue
		}
		if !reflect.DeepEqual(p.Argv, want.Argv) || p.Family != want.Family || p.CD != want.CD || !reflect.DeepEqual(p.Env, want.Env) {
			t.Errorf("%q: %+v, want %+v", c, *p, want)
		}
	}
	// the shell's last assignment for a name wins, whatever the order
	if p, _ := Parse("A=2 A=1 pytest -q"); !reflect.DeepEqual(p.Env, []string{"A=1"}) {
		t.Fatalf("env: %v", p.Env)
	}
	// the wrapper is part of the identity: `uv run pytest` ≠ `pytest`
	a, _ := Parse("uv run pytest -q")
	b, _ := Parse("pytest -q")
	if Key(a, "/w") == Key(b, "/w") {
		t.Fatal("wrapper is not in the identity")
	}
	if Key(a, "/w") != Key(a, "/w") || Key(a, "/w") == Key(a, "/x") {
		t.Fatal("key ignores the dir or is unstable")
	}
	// one assignment with a space is not two assignments
	one, _ := Parse(`A="x B=y" make test`)
	two, _ := Parse("A=x B=y make test")
	if Key(one, "/w") == Key(two, "/w") {
		t.Fatal("env identity collides")
	}
}

func TestPassedRefusesSilenceAndFailure(t *testing.T) {
	py, _ := Parse("pytest -q")
	mk, _ := Parse("make test")
	cases := []struct {
		name string
		cmd  *Command
		seen bool
		err  bool
		out  string
		want bool
	}{
		{"no result seen", py, false, false, "3 passed in 0.1s", false},
		{"is_error", py, true, true, "3 passed", false},
		{"empty output", py, true, false, "  \n", false},
		{"failure tally beats pass tally", py, true, false, "2 passed, 1 failed", false},
		{"short-summary FAILED line", py, true, false, "FAILED tests/test_x.py::test_a\n1 passed", false},
		{"pytest without its tally", py, true, false, "collected 0 items", false},
		{"pytest positive", py, true, false, "===== 3 passed in 0.12s =====", true},
		{"make has no tally: non-empty clean output passes", mk, true, false, "ran the suite", true},
		{"go: no test files is a compile proof", &Command{Family: "go"}, true, false, "?\tpkg\t[no test files]", true},
		{"go -json pass", &Command{Family: "go"}, true, false, `{"Action":"pass","Package":"pkg"}`, true},
		{"go -json fail", &Command{Family: "go"}, true, false, `{"Action":"fail","Package":"pkg"}`, false},
		{"make with a runner failure line inside", mk, true, false, "--- FAIL: TestX\nFAIL", false},
		{"bare ERROR log line is not a verdict", mk, true, false, "ERROR something happened\nok", true},
	}
	for _, c := range cases {
		if got := Passed(c.cmd, c.seen, c.err, []byte(c.out)); got != c.want {
			t.Errorf("%s: %v", c.name, got)
		}
	}
}

func TestClassifyIsTallyFirst(t *testing.T) {
	if Classify("pytest", 0, []byte("1 failed, 2 passed")) != Fail {
		t.Fatal("failure tally under exit 0 is not a fail")
	}
	if Classify("pytest", 1, []byte("3 passed")) != Fail {
		t.Fatal("non-zero exit is not a fail")
	}
	if Classify("pytest", 0, []byte("no tests ran")) != Inconclusive {
		t.Fatal("exit 0 without the pytest tally is not inconclusive")
	}
	if Classify("go", 0, []byte("ok\tpkg\t0.1s")) != Pass || Classify("cargo", 0, []byte("test result: ok. 3 passed")) != Pass {
		t.Fatal("family pass tallies")
	}
	if Classify("make", 0, []byte("  \n")) != Inconclusive {
		t.Fatal("silence under exit 0 is not a pass")
	}
	if Classify("make", 0, []byte("ran")) != Pass {
		t.Fatal("a family without a tally passes on exit 0 with output")
	}
	// the streams are classified on their own line boundaries
	if Classify("make", 0, Output([]byte("wrapper note"), []byte("FAIL\n"))) != Fail {
		t.Fatal("a FAIL line at the head of stderr was glued to stdout")
	}
	if string(Output([]byte("a\n"), []byte("b"))) != "a\nb" || string(Output([]byte("a"), nil)) != "a" || string(Output(nil, []byte("b"))) != "b" {
		t.Fatal("output join")
	}
}

func TestCommandOfReadsTheToolInput(t *testing.T) {
	for in, want := range map[string]string{
		`{"command": "pytest -q", "description": "run tests"}`: "pytest -q",
		`{"cmd": "go test ./..."}`:                             "go test ./...",
		`"make test"`:                                          "make test",
		`{"path": "x"}`:                                        "",
		`{"command": "   "}`:                                   "",
		`not json`:                                             "",
	} {
		if got := CommandOf([]byte(in)); got != want {
			t.Errorf("%s: %q", in, got)
		}
	}
	if !IsShellOp("Bash") || !IsShellOp("shell") || IsShellOp("Read") || IsShellOp("Write") {
		t.Fatal("shell ops")
	}
}

// The re-run executes the argv with no shell in the recorded directory:
// a `make test` whose Makefile changed between the step and closure fails;
// the same target restored passes; a runner that is not there and a
// directory that is gone are inconclusive, never a fail.
func TestRerunRunsArgvInTheRecordedDirWithoutAShell(t *testing.T) {
	dir := t.TempDir()
	mk := filepath.Join(dir, "Makefile")
	if err := os.WriteFile(mk, []byte("test:\n\t@echo suite ok\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cmd, _ := Parse("make test")
	ctx := context.Background()
	if r := Rerun(ctx, cmd, dir, nil, time.Minute); r.Outcome != Pass || r.Exit != 0 || string(r.Stdout) != "suite ok\n" {
		t.Fatalf("%+v", *r)
	}
	if err := os.WriteFile(mk, []byte("test:\n\t@echo 1 failed\n\t@false\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, cmd, dir, nil, time.Minute); r.Outcome != Fail || r.Exit == 0 {
		t.Fatalf("%+v", *r)
	}
	// an operator inside an argv word is data, not a shell (no shell ran it)
	echo, _ := Parse("make test ECHO=x")
	if r := Rerun(ctx, echo, dir, nil, time.Minute); r.Outcome != Fail {
		t.Fatalf("%+v", *r)
	}
	missing := &Command{Argv: []string{"no-such-runner-zz", "test"}, Family: "make"}
	if r := Rerun(ctx, missing, dir, nil, time.Minute); r.Outcome != Inconclusive || r.Why == "" {
		t.Fatalf("%+v", *r)
	}
	if r := Rerun(ctx, cmd, filepath.Join(dir, "gone"), nil, time.Minute); r.Outcome != Inconclusive || r.Why == "" {
		t.Fatalf("%+v", *r)
	}
	if err := os.WriteFile(mk, []byte("test:\n\t@sleep 5\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, cmd, dir, nil, 200*time.Millisecond); !r.TimedOut || r.Outcome != Inconclusive {
		t.Fatalf("%+v", *r)
	}
	// output beyond MaxCapture keeps the tail (where the tally is) and says so
	if err := os.WriteFile(mk, []byte("test:\n\t@head -c 5000000 /dev/zero | tr '\\0' x; echo; echo '2 passed'\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, &Command{Argv: []string{"make", "test"}, Family: "pytest"}, dir, nil, time.Minute); !r.Truncated || r.Outcome != Inconclusive || len(r.Stdout) != MaxCapture || !strings.Contains(r.Why, "capture") {
		t.Fatalf("truncated=%v outcome=%s len=%d", r.Truncated, r.Outcome, len(r.Stdout))
	}
	// ...but a failure tally in the kept tail is still a failure: the flag
	// launders nothing
	if err := os.WriteFile(mk, []byte("test:\n\t@head -c 5000000 /dev/zero | tr '\\0' x; echo; echo '1 failed, 2 passed'\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, &Command{Argv: []string{"make", "test"}, Family: "pytest"}, dir, nil, time.Minute); !r.Truncated || r.Outcome != Fail || r.Why != "" {
		t.Fatalf("truncated=%v outcome=%s why=%q", r.Truncated, r.Outcome, r.Why)
	}
	if Decide("pytest", 0, true, []byte("2 passed")) != Inconclusive || Decide("pytest", 1, true, []byte("2 passed")) != Fail || Decide("pytest", 0, true, []byte("1 failed")) != Fail || Decide("make", 0, true, nil) != Inconclusive {
		t.Fatal("Decide under truncation")
	}
	if Dir("/w", "sub") != "/w/sub" || Dir("/w", "") != "/w" || Dir("/w", "/abs") != "/abs" {
		t.Fatal("dir")
	}
	// a target turned into a silent no-op proves nothing at closure
	if err := os.WriteFile(mk, []byte("test:\n\t@true\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, cmd, dir, nil, time.Minute); r.Outcome != Inconclusive || r.Exit != 0 {
		t.Fatalf("%+v", *r)
	}
	// a recorded PATH selects the runner (exec's own lookup runs before
	// the env applies), PWD is the dir, the caller's extra env is there,
	// and the pytest family is proved by its tally
	bin := filepath.Join(dir, "bin")
	if err := os.MkdirAll(bin, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(bin, "pytest"), []byte("#!/bin/sh\necho \"pwd=$PWD extra=$MARO_X\"\necho '3 passed in 0.1s'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	py, _ := Parse("PATH=bin pytest -q")
	if r := Rerun(ctx, py, dir, []string{"MARO_X=1"}, time.Minute); r.Outcome != Pass || !strings.Contains(string(r.Stdout), "pwd="+dir+" extra=1") {
		t.Fatalf("%+v", *r)
	}
	// the recorded runner gone from the recorded PATH is not looked up on
	// the host's PATH (a same-named host runner would answer for it)
	if err := os.Remove(filepath.Join(bin, "pytest")); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, py, dir, nil, time.Minute); r.Outcome != Inconclusive || !strings.Contains(r.Why, "recorded PATH") {
		t.Fatalf("%+v", *r)
	}
	// an EMPTY recorded PATH is a set one (the shell searched the dir):
	// the runner in the dir answers, and once gone, no host runner does
	if err := os.WriteFile(filepath.Join(dir, "pytest"), []byte("#!/bin/sh\necho 'here 1 passed in 0.1s'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	empty, _ := Parse("PATH= pytest -q")
	if r := Rerun(ctx, empty, dir, nil, time.Minute); r.Outcome != Pass || !strings.HasPrefix(string(r.Stdout), "here") {
		t.Fatalf("%+v", *r)
	}
	if err := os.Remove(filepath.Join(dir, "pytest")); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, empty, dir, nil, time.Minute); r.Outcome != Inconclusive || !strings.Contains(r.Why, "recorded PATH") {
		t.Fatalf("%+v", *r)
	}
	// a runner that exits 0 after leaving a background writer behind: the
	// writer dies with the runner's group, before the result is returned
	if err := os.WriteFile(mk, []byte("test:\n\t@sh -c 'sleep 1; echo late > late.txt' </dev/null >/dev/null 2>&1 &\n\t@echo ran\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, cmd, dir, nil, time.Minute); r.Outcome != Pass {
		t.Fatalf("%+v", *r)
	}
	time.Sleep(1500 * time.Millisecond)
	if _, err := os.Stat(filepath.Join(dir, "late.txt")); err == nil {
		t.Fatal("a descendant outlived the re-run and wrote to the work dir")
	}
	// a wrapper that prints a note then FAILs on stderr with exit 0
	if err := os.WriteFile(mk, []byte("test:\n\t@printf 'wrapper note'\n\t@echo FAIL >&2\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, cmd, dir, nil, time.Minute); r.Outcome != Fail {
		t.Fatalf("%+v", *r)
	}
	// a cancelled context is not a timeout
	cctx, cancel := context.WithCancel(ctx)
	cancel()
	if r := Rerun(cctx, cmd, dir, nil, time.Minute); r.Outcome != Inconclusive || r.TimedOut || !strings.Contains(r.Why, "cancelled") {
		t.Fatalf("%+v", *r)
	}
	// the deadline kills the runner's whole tree: a grandchild sleeping in
	// the background is gone after the timeout
	if err := os.WriteFile(mk, []byte("test:\n\t@sh -c 'sleep 30 & echo $$! > child.pid; wait'\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := Rerun(ctx, cmd, dir, nil, 300*time.Millisecond); !r.TimedOut {
		t.Fatalf("%+v", *r)
	}
	if pid, err := os.ReadFile(filepath.Join(dir, "child.pid")); err == nil {
		time.Sleep(100 * time.Millisecond)
		if _, err := os.Stat("/proc/" + strings.TrimSpace(string(pid))); err == nil {
			t.Fatalf("grandchild %s survived the deadline", strings.TrimSpace(string(pid)))
		}
	} else {
		t.Fatal("the recipe did not run")
	}
	// a runner killed by a signal decided nothing
	killed := &Command{Argv: []string{"sh", "-c", "kill -9 $$"}, Family: "make"}
	if r := Rerun(ctx, killed, dir, nil, time.Minute); r.Outcome != Inconclusive || !strings.Contains(r.Why, "signal") || r.Exit >= 0 {
		t.Fatalf("%+v", *r)
	}
}
