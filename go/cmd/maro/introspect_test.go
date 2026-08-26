package main

import (
	"bytes"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// runIntrospect's remaining two claims after the mapping moved into
// introspect.ExitStatus: the block goes to STDERR and not stdout, and the
// process exits 2. Neither can be checked in-process — os.Exit takes the
// test binary with it — so the test re-execs itself as a child.
//
// This exists because round 4 found the wrapper had no test at all. The
// differential next door asserted a COPY of the mapping that it had
// written itself, so `os.Exit(2)` could become `os.Exit(1)` and
// `ue.Stderr()` a bare "error\n" with the whole suite still green. A
// wrapper whose only job is a mapping needs the mapping executed, not
// re-implemented by its test.
func TestIntrospectUsageErrorExitsTwoOnStderr(t *testing.T) {
	if os.Getenv("MARO_TEST_INTROSPECT_CHILD") == "1" {
		// --bogus is an unrecognized option: argparse reports it at the
		// very end of parsing and exits 2.
		runIntrospect([]string{"--bogus"})
		// Only reached if the wrapper declined to exit, which is itself
		// the failure this test is about.
		os.Exit(0)
	}

	cmd := exec.Command(os.Args[0],
		"-test.run=^TestIntrospectUsageErrorExitsTwoOnStderr$")
	cmd.Env = append(os.Environ(), "MARO_TEST_INTROSPECT_CHILD=1")
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()

	code := 0
	if ee, ok := err.(*exec.ExitError); ok {
		code = ee.ExitCode()
	} else if err != nil {
		t.Fatalf("running child: %v (stderr %q)", err, errb.String())
	}
	if code != 2 {
		t.Errorf("exit code = %d, want 2 (argparse's usage-error code; 1 is "+
			"what a script reads as a runtime failure)\nstdout: %q\nstderr: %q",
			code, out.String(), errb.String())
	}

	// The usage block, the prog-prefixed message, and the token that was
	// refused — all three, because a wrapper that printed only the message
	// would still exit 2 and pass a check for "said something".
	got := errb.String()
	for _, want := range []string{
		"usage: maro-introspect [-h]",
		"maro-introspect: error: unrecognized arguments: --bogus",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("stderr missing %q\n--- got ---\n%s", want, got)
		}
	}
	if out.Len() != 0 {
		t.Errorf("a usage error wrote to STDOUT, which is where the "+
			"rendering goes: %q", out.String())
	}
}
