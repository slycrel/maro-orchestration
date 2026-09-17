// Package regression carries a run's regression obligations (LoopsBench
// item 2, arXiv:2608.00267 "Regression Pressure"): a step runs a test
// runner, sees it pass, is judged done — and a LATER step breaks what it
// proved. Nothing re-runs the verification a step already paid for unless
// the obligation is carried forward. This package is the deterministic
// half: which observed shell commands are obligations (a closed grammar and
// positive pass evidence, never prose), and how a closure re-run of one is
// classified (tally first, exit code second, family pass tally required).
//
// The grammar and the tally rules are the SHARED SPEC with the Python
// engine (src/regression_ledger.py, c4beb004): both engines admit the same
// commands and read the same runner summaries, so a checkpoint of either
// says the same thing about what a step proved. The re-run itself runs the
// recorded argv with no shell in the recorded directory — exactly what the
// step ran, nothing more — which is why a shell PROGRAM (pipeline, `||
// true`, heredoc, glob) is never an obligation.
//
// Stdlib only. No record types here: the run package commits what this
// package decides.
package regression

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"time"
)

// Command is one admitted test-runner invocation.
type Command struct {
	Argv   []string // the EXACT execution argv, wrapper included (`uv run pytest -q`)
	Family string   // pytest | go | cargo | npm | make | tox | bun
	Env    []string // leading NAME=value assignments, sorted
	CD     string   // `cd DIR &&` prefix, unresolved ("" when none)
}

// Outcome classifies a re-run.
type Outcome string

const (
	Pass         Outcome = "pass"
	Fail         Outcome = "fail"
	Inconclusive Outcome = "inconclusive" // the probe could not run: proves nothing either way
)

// Families with a known summary line: a zero exit without the passing
// tally is inconclusive, not a pass (a wrapper or make target can swallow
// the runner's status; a runner that printed nothing proved nothing).
var passTally = map[string]*regexp.Regexp{
	"pytest": regexp.MustCompile(`(?i)\b[1-9]\d*\s+passed\b`),
	"go":     regexp.MustCompile(`(?m)^(ok\b|PASS\b|\?\s+\S+\s+\[no test files\])|"Action":"pass"`),
	"cargo":  regexp.MustCompile(`\btest result: ok\b`),
}

// Terminal-summary shapes only: pytest/jest "N failed" / "N errors", mocha
// "N failing", pytest's short-summary "FAILED path::test" / "ERROR
// path::test", go's "FAIL" line / "--- FAIL:", cargo's "test result:
// FAILED", npm's "Tests failed". A bare "ERROR …" log line from the code
// under test is not a runner verdict.
var failureTally = regexp.MustCompile(`(?im)\b[1-9]\d*\s+(failed|failing|errors?)\b|^(FAILED|ERROR)\s+\S+::|^FAIL\b|^--- FAIL:|\bTests failed\b|\btest result: FAILED\b|"Action":"fail"`)

// nonExec lists, per family, the options under which the runner does not
// execute the suite (it lists, collects, prints the recipe, only builds).
// A word is matched whole, so pytest's `-q` (quiet) is not make's `-q`
// (question).
var nonExec = map[string]map[string]bool{
	"pytest": set("--collect-only", "--co", "--fixtures", "--markers", "--version"),
	"go":     set("-list", "--list", "-c", "-run=xxx"),
	"cargo":  set("--no-run", "--list"),
	"npm":    set("--dry-run"),
	"bun":    set("--dry-run"),
	"tox":    set("-l", "--listenvs", "-a", "--listenvs-all", "--showconfig", "--notest", "--version"),
	"make":   set("-n", "--just-print", "--dry-run", "--recon", "-q", "--question", "-t", "--touch", "-p", "--print-data-base", "-v"),
}

// abbreviates names the families whose option parser accepts an
// unambiguous prefix of a long option (getopt_long, argparse): `make --dry
// test` is `--dry-run`. A prefix of any non-executing long option is
// refused; an ambiguous one would have errored, and refusing it costs an
// obligation at most (the safe direction).
var abbreviates = set("make", "pytest", "tox")

func set(words ...string) map[string]bool {
	m := map[string]bool{}
	for _, w := range words {
		m[w] = true
	}
	return m
}

// nonExecAny are the words under which no runner executes its suite.
var nonExecAny = set("--help", "-h", "--version", "-V")

// controlEnv names the assignments that change what a runner does without
// touching its argv (`MAKEFLAGS=-n make test` is a dry run): a command
// under one is not an obligation (the safe direction: fewer obligations).
var controlEnv = set("MAKEFLAGS", "GNUMAKEFLAGS", "GOFLAGS", "PYTEST_ADDOPTS", "CARGO_BUILD_FLAGS", "NPM_CONFIG_DRY_RUN", "npm_config_dry_run", "TOX_OVERRIDE")

// nonExecuting reports whether any word of the runner argv is one of the
// family's non-executing options (`-list=x` and `--co=1` forms, make's
// clustered short options `-nk`/`-kh`, and the abbreviated long options
// getopt_long and argparse accept, `--dry` for `--dry-run`, included).
func nonExecuting(family string, runner []string) bool {
	table := nonExec[family]
	for _, w := range runner[1:] {
		if table[w] || nonExecAny[w] {
			return true
		}
		if i := strings.IndexByte(w, '='); i > 0 {
			w = w[:i]
			if table[w] || nonExecAny[w] {
				return true
			}
		}
		if family == "make" && len(w) > 1 && w[0] == '-' && w[1] != '-' && strings.ContainsAny(w[1:], "nqtphv") {
			return true // a cluster carrying a no-recipe flag (or help/version, which print and exit)
		}
		if abbreviates[family] && strings.HasPrefix(w, "--") && len(w) > 2 {
			for _, opts := range []map[string]bool{table, nonExecAny} {
				for opt := range opts {
					if strings.HasPrefix(opt, w) {
						return true
					}
				}
			}
		}
	}
	return false
}

var envAssign = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*=`)

var pythonName = regexp.MustCompile(`^python(3(\.\d+)?)?$`)

// programChars inside a word mean the string was a shell PROGRAM (an
// operator, redirect, substitution, or a glob the shell would expand) —
// not one argv. `*`/`?` are refused because the re-run will not glob.
const programChars = ";|&<>`$(){}*?~"

var runWrappers = map[string]bool{"uv": true, "poetry": true, "pipenv": true}

// MaxCommandBytes bounds an admitted command line (a longer one is a script,
// not an invocation).
const MaxCommandBytes = 400

// shellOps are the executor tool names that run a shell command, matched
// case-insensitively: Claude Code names it Bash; codex "shell"; generic
// "run_command". Loose on purpose — the grammar below is the gate.
var shellOps = map[string]bool{"bash": true, "shell": true, "run_command": true, "execute": true, "exec": true, "sh": true}

// IsShellOp reports whether a tool name is a shell call.
func IsShellOp(op string) bool { return shellOps[strings.ToLower(op)] }

// CommandOf extracts the shell command from a shell tool's input as the
// backend reported it: a JSON object's command / cmd / script string, or a
// bare JSON string; "" when the input names none.
func CommandOf(input []byte) string {
	var obj map[string]json.RawMessage
	if json.Unmarshal(input, &obj) == nil {
		for _, k := range []string{"command", "cmd", "script"} {
			var s string
			if raw, ok := obj[k]; ok && json.Unmarshal(raw, &s) == nil && strings.TrimSpace(s) != "" {
				return s
			}
		}
		return ""
	}
	var s string
	if json.Unmarshal(input, &s) == nil {
		return s
	}
	return ""
}

// Parse admits one simple test-runner invocation:
//
//	[cd DIR &&] [NAME=value ...] [uv|poetry|pipenv run] RUNNER ARGS
//
// where every word is a literal (no operator, redirect, substitution,
// glob or tilde) and the whole string is one line. Anything else — a
// pipeline, `|| true`, a heredoc, a second command, an unquoted `#`
// comment — is a shell program and is refused. The wrapper stays in the
// execution argv (it is part of what ran) and is stripped only to
// classify the family.
func Parse(cmd string) (*Command, bool) {
	if len(cmd) > MaxCommandBytes {
		return nil, false
	}
	text := strings.TrimSpace(cmd)
	if text == "" || strings.ContainsAny(text, "\n\r") {
		return nil, false
	}
	words, ok := split(text)
	if !ok || len(words) == 0 {
		return nil, false
	}
	cd := ""
	if words[0] == "cd" {
		// `cd DIR && ...` exactly; `cd` alone, `cd; ...` or `cd DIR; ...`
		// are programs (the `;` form arrives as a word containing ';')
		if len(words) < 4 || words[2] != "&&" {
			return nil, false
		}
		cd = words[1]
		if strings.ContainsAny(cd, programChars) {
			return nil, false
		}
		words = words[3:]
	}
	for _, w := range words {
		if w == "" || strings.ContainsAny(w, programChars) {
			return nil, false
		}
	}
	// assignments: the shell's last value for a name wins, so the set is
	// one per name before it is sorted (an exec env keeps the last
	// duplicate — sorting duplicates would let the order pick the winner)
	byName := map[string]string{}
	for len(words) > 0 && envAssign.MatchString(words[0]) {
		name := words[0][:strings.IndexByte(words[0], '=')]
		if controlEnv[name] {
			return nil, false
		}
		byName[name] = words[0]
		words = words[1:]
	}
	var env []string
	for _, v := range byName {
		env = append(env, v)
	}
	for _, w := range words {
		if strings.HasPrefix(w, "#") {
			return nil, false
		}
	}
	runner := words
	if len(runner) >= 2 && runWrappers[filepath.Base(runner[0])] && runner[1] == "run" {
		runner = runner[2:]
	}
	family := familyOf(runner)
	if family == "" || nonExecuting(family, runner) {
		return nil, false
	}
	sort.Strings(env)
	return &Command{Argv: append([]string(nil), words...), Family: family, Env: env, CD: cd}, true
}

func familyOf(argv []string) string {
	if len(argv) == 0 {
		return ""
	}
	base := filepath.Base(argv[0])
	rest := argv[1:]
	first := func(want ...string) bool {
		if len(rest) < len(want) {
			return false
		}
		for i, w := range want {
			if rest[i] != w {
				return false
			}
		}
		return true
	}
	switch {
	case base == "pytest" || base == "py.test":
		return "pytest"
	case pythonName.MatchString(base) && first("-m", "pytest"):
		return "pytest"
	case base == "tox":
		return "tox"
	case base == "go" && first("test"):
		return "go"
	case base == "cargo" && first("test"):
		return "cargo"
	case base == "npm" || base == "pnpm" || base == "yarn":
		if first("test") || first("run", "test") {
			return "npm"
		}
	case base == "bun" && first("test"):
		return "bun"
	case base == "make":
		for _, w := range rest {
			if w == "test" {
				return "make"
			}
		}
	}
	return ""
}

// split is a POSIX word splitter (shlex.split, comments off): single
// quotes literal, double quotes with backslash escapes for \ " $ ` and
// newline, backslash escapes outside quotes; an unterminated quote or a
// trailing backslash is not a word list.
func split(s string) ([]string, bool) {
	var words []string
	var cur strings.Builder
	inWord := false
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c == '\\':
			if i+1 >= len(s) {
				return nil, false
			}
			i++
			cur.WriteByte(s[i])
			inWord = true
		case c == '\'':
			j := strings.IndexByte(s[i+1:], '\'')
			if j < 0 {
				return nil, false
			}
			cur.WriteString(s[i+1 : i+1+j])
			i += j + 1
			inWord = true
		case c == '"':
			i++
			closed := false
			for ; i < len(s); i++ {
				if s[i] == '"' {
					closed = true
					break
				}
				if s[i] == '\\' && i+1 < len(s) && strings.IndexByte("\\\"$`\n", s[i+1]) >= 0 {
					i++
				}
				cur.WriteByte(s[i])
			}
			if !closed {
				return nil, false
			}
			inWord = true
		case c == ' ' || c == '\t':
			if inWord {
				words = append(words, cur.String())
				cur.Reset()
				inWord = false
			}
		default:
			cur.WriteByte(c)
			inWord = true
		}
	}
	if inWord {
		words = append(words, cur.String())
	}
	return words, true
}

// HasFailureTally reports the runner's own failure verdict in an output.
func HasFailureTally(out []byte) bool { return failureTally.Match(out) }

// Passed is the positive-evidence rule for an OBSERVED run of cmd: a
// result was seen (the caller passes false when the tool_use had no
// matching result — silence is not success), it was not an error, its
// output is non-empty, carries no failure tally, and carries the family's
// passing tally when the family has one.
func Passed(cmd *Command, resultSeen, isError bool, out []byte) bool {
	return resultSeen && !isError && Classify(cmd.Family, 0, out) == Pass
}

// Classify a re-run by the SAME evidence the harvest demanded: a failure
// tally is a fail whatever the exit code; a non-zero exit is a fail; a
// zero exit without the family's passing tally is inconclusive.
func Classify(family string, exit int, out []byte) Outcome {
	return Decide(family, exit, false, out)
}

// Decide is Classify over a capture that may have lost its head
// (truncated): a failure tally the kept tail carries, or a non-zero exit,
// is still a Fail — the bytes and the exit are not laundered by the
// flag — but a pass tally in the tail proves nothing, since the dropped
// head may have carried the failure.
func Decide(family string, exit int, truncated bool, out []byte) Outcome {
	if failureTally.Match(out) || exit != 0 {
		return Fail
	}
	if truncated {
		return Inconclusive
	}
	if len(bytes.TrimSpace(out)) == 0 {
		return Inconclusive // silence proves nothing (a target turned into a no-op)
	}
	if re := passTally[family]; re != nil && !re.Match(out) {
		return Inconclusive
	}
	return Pass
}

// Output is the bytes a re-run is classified over: both streams, each
// on its own line boundary so a tally line cannot be glued to the other
// stream's tail (the driver stores and the fold re-reads the same two).
func Output(stdout, stderr []byte) []byte {
	out := append([]byte{}, stdout...)
	if len(out) > 0 && len(stderr) > 0 && out[len(out)-1] != '\n' {
		out = append(out, '\n')
	}
	return append(out, stderr...)
}

// Dir is the absolute directory a command ran in: the invocation's working
// directory plus any `cd` prefix.
func Dir(base, cd string) string {
	if base == "" {
		base, _ = os.Getwd()
	}
	if filepath.IsAbs(cd) {
		base = cd
	} else if cd != "" {
		base = filepath.Join(base, cd)
	}
	abs, err := filepath.Abs(base)
	if err != nil {
		return base
	}
	return abs
}

// Key is the dedup identity of an obligation: dir + env + argv, quoted so
// spacing cannot collide.
func Key(cmd *Command, dir string) string {
	quote := func(ws []string) string {
		q := make([]string, len(ws))
		for i, w := range ws {
			q[i] = fmt.Sprintf("%q", w)
		}
		return strings.Join(q, " ")
	}
	return dir + "\x00" + quote(cmd.Env) + "\x00" + quote(cmd.Argv)
}

// Result is what a re-run observed.
type Result struct {
	Exit      int
	TimedOut  bool
	Stdout    []byte
	Stderr    []byte
	Truncated bool // a stream exceeded MaxCapture: its head was dropped, the tail kept; the outcome is then inconclusive
	Outcome   Outcome
	Why       string // the inconclusive class, or ""
}

// MaxCapture bounds what a re-run's stream keeps in memory: the LAST
// MaxCapture bytes (runner tallies close the output). Not a spend cap —
// the runner runs to its end; only the copy of its chatter is bounded.
const MaxCapture = 4 << 20

// tail keeps the last MaxCapture bytes written.
type tail struct {
	b         []byte
	truncated bool
}

func (t *tail) Write(p []byte) (int, error) {
	t.b = append(t.b, p...)
	if len(t.b) > MaxCapture {
		t.b = append([]byte{}, t.b[len(t.b)-MaxCapture:]...)
		t.truncated = true
	}
	return len(p), nil
}

// lookPath resolves a bare runner name the way the shell that ran it
// did: through the PATH the command's own assignments set (exec's own
// lookup happens before Cmd.Env applies), relative entries from dir. A
// command that set no PATH resolves through the process's own (ok=true,
// the name unchanged); one whose recorded PATH holds no such executable
// is not resolved at all (ok=false) — never through the host's PATH. An
// EMPTY recorded PATH is a set one: the shell searched the dir.
func lookPath(argv0 string, cmdEnv []string, dir string) (string, bool) {
	if strings.Contains(argv0, "/") {
		return argv0, true
	}
	path, set := "", false
	for _, e := range cmdEnv {
		if v, ok := strings.CutPrefix(e, "PATH="); ok {
			path, set = v, true
		}
	}
	if !set {
		return argv0, true
	}
	entries := filepath.SplitList(path)
	if path == "" {
		entries = []string{""}
	}
	for _, d := range entries {
		if d == "" {
			d = "."
		}
		if !filepath.IsAbs(d) {
			d = filepath.Join(dir, d)
		}
		cand := filepath.Join(d, argv0)
		if st, err := os.Stat(cand); err == nil && !st.IsDir() && st.Mode()&0o111 != 0 {
			return cand, true
		}
	}
	return "", false
}

// Rerun executes cmd's argv with no shell in dir, with the process
// environment plus cmd's assignments, bounded by timeout. Verifier
// failures — the recorded directory gone, the runner missing or not
// executable, the timeout — are inconclusive with the class named: an
// environment-blind probe proves nothing either way.
func Rerun(ctx context.Context, cmd *Command, dir string, extra []string, timeout time.Duration) *Result {
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return &Result{Exit: -1, Outcome: Inconclusive, Why: "recorded directory is gone: " + dir}
	}
	if timeout <= 0 {
		timeout = 10 * time.Minute
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	// the environment the shell gave the runner: the process's own, the
	// extra the caller names (what the agent's tool calls got), then the
	// command's assignments (last wins) and the dir as PWD
	env := append(append(append(os.Environ(), extra...), "PWD="+dir), cmd.Env...)
	bin, ok := lookPath(cmd.Argv[0], cmd.Env, dir)
	if !ok {
		return &Result{Exit: -1, Outcome: Inconclusive, Why: "runner not on the recorded PATH: " + cmd.Argv[0]}
	}
	c := exec.CommandContext(cctx, bin, cmd.Argv[1:]...)
	c.Dir = dir
	c.Env = env
	c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} // its own process group: the deadline kills the whole tree, not just the runner
	c.Cancel = func() error { return syscall.Kill(-c.Process.Pid, syscall.SIGKILL) }
	c.WaitDelay = 2 * time.Second // a child that outlives the runner must not hold the pipes open past the kill
	var so, se tail
	c.Stdout, c.Stderr = &so, &se
	err := c.Run()
	quiesced := true
	if c.Process != nil {
		// whatever the runner left behind in its group dies with it: a
		// re-run mutates nothing after it has been classified — checked,
		// not assumed: the result waits (bounded) until nothing in the
		// group answers a signal 0
		_ = syscall.Kill(-c.Process.Pid, syscall.SIGKILL)
		quiesced = false
		for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); time.Sleep(5 * time.Millisecond) {
			if syscall.Kill(-c.Process.Pid, 0) == syscall.ESRCH {
				quiesced = true
				break
			}
		}
	}
	r := &Result{Stdout: so.b, Stderr: se.b, Truncated: so.truncated || se.truncated}
	switch cctx.Err() {
	case context.DeadlineExceeded:
		r.Exit, r.TimedOut, r.Outcome, r.Why = -1, true, Inconclusive, fmt.Sprintf("timed out after %s", timeout)
		return r
	case context.Canceled:
		r.Exit, r.Outcome, r.Why = -1, Inconclusive, "cancelled before the runner finished"
		return r
	}
	var ee *exec.ExitError
	switch {
	case err == nil:
		r.Exit = 0
	case errors.As(err, &ee):
		r.Exit = ee.ExitCode()
		if r.Exit < 0 {
			// killed by a signal (OOM, an operator's kill): nothing was decided
			r.Outcome, r.Why = Inconclusive, "runner killed by a signal: "+ee.String()
			return r
		}
	default:
		// not started: runner missing, not executable, permission
		r.Exit, r.Outcome, r.Why = -1, Inconclusive, "runner could not start: "+err.Error()
		return r
	}
	r.Outcome = Decide(cmd.Family, r.Exit, r.Truncated, Output(r.Stdout, r.Stderr))
	switch {
	case r.Outcome != Inconclusive:
	case r.Truncated:
		// a pass tally the dropped head may have contradicted is not re-derivable
		r.Why = fmt.Sprintf("output exceeded the %d-byte capture: the kept tail decides nothing", MaxCapture)
	default:
		r.Why = "exit 0 without the " + cmd.Family + " passing evidence"
	}
	if !quiesced {
		// something in the runner's group did not die: a Pass over a tree
		// still mutating the workspace proves nothing (a Fail stands)
		if r.Outcome == Pass {
			r.Outcome, r.Why = Inconclusive, "the runner's process group did not exit after the kill"
		}
	}
	return r
}
