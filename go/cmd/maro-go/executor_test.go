package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/secrets"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// The isolation flags are the operator's instrument for the one setting
// that decides what a worker can reach, so a typo at that boundary must not
// resolve to the least safe lane, and a policy nothing can keep must fail
// before a journal is touched.
func TestCLIExecutorFlagsRefuseWhatTheyCannotHonour(t *testing.T) {
	t.Run("a flag with no value is an error, not a default", func(t *testing.T) {
		for _, args := range [][]string{
			{"--executor"},
			{"--executor-image"},
			{"--executor", "--work", "/tmp"},   // another flag is not a value
			{"--executor-image", "--executor"}, //
		} {
			var e executorFlags
			i := 0
			ok, err := e.parse(args, &i)
			if !ok {
				t.Fatalf("%v: not consumed", args)
			}
			if err == nil || !strings.Contains(err.Error(), "needs a value") {
				t.Fatalf("%v: %v", args, err)
			}
		}
		// and a value that IS a value is taken
		var e executorFlags
		i := 0
		if ok, err := e.parse([]string{"--executor", "require"}, &i); !ok || err != nil || e.policy != "require" || i != 1 {
			t.Fatalf("ok=%v err=%v policy=%q i=%d", ok, err, e.policy, i)
		}
	})
	t.Run("a policy nothing can keep is refused", func(t *testing.T) {
		var errw bytes.Buffer
		// no subprocess backend to isolate: `runs resume` without the CLI,
		// `now --backend scripted`, an experiment arm with no subprocess
		for _, pol := range []string{"on", "require"} {
			e := executorFlags{policy: pol}
			if err := e.wire(nil, nil, &errw); err == nil || !strings.Contains(err.Error(), "no subprocess backend") {
				t.Fatalf("%s: %v", pol, err)
			}
		}
		// off is the absence of a request, so it is never a refusal
		e := executorFlags{}
		if err := e.wire(nil, nil, &errw); err != nil {
			t.Fatalf("off with no backend: %v", err)
		}
		// an image with the lane off is a contradiction, said out loud
		e = executorFlags{image: "maro-executor:2.1.210-r3"}
		if err := e.wire(nil, nil, &errw); err == nil || !strings.Contains(err.Error(), "--executor is off") {
			t.Fatalf("image with off: %v", err)
		}
		// out of vocabulary names the vocabulary
		e = executorFlags{policy: "sandbox"}
		if err := e.wire(nil, nil, &errw); err == nil || !strings.Contains(err.Error(), "off|on|require") {
			t.Fatalf("bad policy: %v", err)
		}
	})
	t.Run("the follow-up parser refuses a missing value too", func(t *testing.T) {
		// `answer` has its own passthrough loop, and it USED to drop a
		// trailing flag silently: `answer <h> "text" --executor` committed
		// the answer and ran the follow-up on the host (review r2). This
		// goes through the real entry point, so a parse loop that swallows
		// the error is caught here.
		t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
		for _, args := range [][]string{
			{"answer", "abc1234", "the answer", "--executor"},
			{"answer", "abc1234", "the answer", "--executor-image"},
			{"answer", "abc1234", "the answer", "--executor", "--work"},
			{"answer", "abc1234", "the answer", "--source"},
		} {
			var out, errw bytes.Buffer
			if code := run(args, &out, &errw); code == 0 {
				t.Fatalf("%v: exit 0\n%s%s", args, out.String(), errw.String())
			}
			if !strings.Contains(errw.String()+out.String(), "needs a value") {
				t.Fatalf("%v: %s%s", args, out.String(), errw.String())
			}
		}
	})
	t.Run("a relative secrets store is resolved, not left to slip through", func(t *testing.T) {
		// A relative MARO_SECRETS_DIR cannot be compared with a resolved
		// bind source (filepath.Rel refuses a mixed pair), so the sealed
		// check read "not contained" for the store it was protecting. The
		// roots are made absolute here, once, where this process's cwd still
		// means something (review r3).
		t.Setenv(secrets.EnvDir, "private/secrets")
		var errw bytes.Buffer
		sp := &invoke.Subprocess{Bin: "/usr/bin/claude"}
		e := executorFlags{policy: "on"}
		if err := e.wire(sp, nil, &errw); err != nil {
			t.Fatal(err)
		}
		if len(sp.Container.Sealed) != 1 || !filepath.IsAbs(sp.Container.Sealed[0]) {
			t.Fatalf("sealed roots %v", sp.Container.Sealed)
		}
		wd, _ := os.Getwd()
		if sp.Container.Sealed[0] != filepath.Join(wd, "private", "secrets") {
			t.Fatalf("sealed root %q (cwd %q)", sp.Container.Sealed[0], wd)
		}
		for _, r := range sp.Container.Forbidden {
			if !filepath.IsAbs(r) {
				t.Fatalf("forbidden root %q is not absolute", r)
			}
		}
	})
	t.Run("the roots fail closed when they cannot be resolved", func(t *testing.T) {
		// A box where the home directory cannot be resolved is a box where
		// every home-based protection silently shortens to nothing. An
		// isolation that protects less than it says is worse than none, so
		// the wiring refuses instead (review r2).
		t.Setenv("HOME", "")
		t.Setenv(secrets.EnvDir, "")
		var errw bytes.Buffer
		s := &invoke.Subprocess{Bin: "/usr/bin/claude"}
		e := executorFlags{policy: "require"}
		if err := e.wire(s, nil, &errw); err == nil || !strings.Contains(err.Error(), "cannot be resolved") {
			t.Fatalf("%v", err)
		}
		if s.Container != nil || s.ExecutorPolicy() != "" {
			t.Fatalf("the backend was wired anyway: %+v %q", s.Container, s.ExecutorPolicy())
		}
	})
	t.Run("the wired backend owns the policy, and the workspace is never mountable", func(t *testing.T) {
		ws := filepath.Join(t.TempDir(), "ws")
		t.Setenv(workspace.EnvOverride, ws)
		r, err := workspace.Resolve()
		if err != nil {
			t.Fatal(err)
		}
		var announce, errw bytes.Buffer
		a, err := r.Announce(&announce)
		if err != nil {
			t.Fatal(err)
		}
		s := &invoke.Subprocess{Bin: "/usr/bin/claude", Model: "haiku"}
		e := executorFlags{policy: "require", image: "img:1"}
		if err := e.wire(s, a, &errw); err != nil {
			t.Fatal(err)
		}
		// the backend is the policy's one owner: every driver that runs it
		// records this by asking (invoke.Isolated)
		if s.ExecutorPolicy() != invoke.ExecutorRequire {
			t.Fatalf("policy %q", s.ExecutorPolicy())
		}
		if s.Container == nil || s.Container.Image != "img:1" {
			t.Fatalf("container %+v", s.Container)
		}
		if !strings.Contains(errw.String(), "executor: require, image img:1") {
			t.Fatalf("the operator was not told: %q", errw.String())
		}
		// the roots that must never reach a container: this workspace, the
		// home it sits in, the filesystem root
		roots := strings.Join(s.Container.Forbidden, " ")
		for _, want := range []string{ws, string(filepath.Separator)} {
			if !strings.Contains(roots, want) {
				t.Fatalf("forbidden roots %q want %q", roots, want)
			}
		}
		// and the one tree nothing inside may be bound: the secrets store,
		// which is a DESCENDANT of the forbidden ~/.maro and so was allowed
		// by the contains-rule alone (review r2)
		if len(s.Container.Sealed) != 1 || s.Container.Sealed[0] != secrets.Open().Dir {
			t.Fatalf("sealed roots %v (want the secrets store %q)", s.Container.Sealed, secrets.Open().Dir)
		}
	})
}
