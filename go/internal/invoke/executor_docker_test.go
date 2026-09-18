package invoke

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The one test that runs a real container. Everything else about the lane
// is argv, and argv is cheap to assert; what argv cannot tell you is
// whether docker ACCEPTS it and whether the two properties the lane exists
// to provide actually hold on the other side of the boundary:
//
//   - a file the worker writes in a writable bind appears on the HOST, at
//     the same absolute path, owned by us (the ask lane and the derived-
//     secrets hand-back are exactly this and nothing more);
//   - a secret named on the command line and held in this process's own
//     environment arrives INSIDE the container, without its value ever
//     being in an argv.
//
// Gated on the real preflight: no docker, no image, no auth volume ⇒
// skipped with the preflight's own sentence, which is also what an
// operator would be told (docker-gated e2e, the house convention).
func TestContainerReallyRunsTheCall(t *testing.T) {
	c := NewContainer("", "", "", "", "none")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := c.Preflight(ctx); err != nil {
		t.Skip("no executor here: ", err)
	}
	work, drop := t.TempDir(), t.TempDir()
	ask := filepath.Join(drop, "ask-operator.json")
	// sh, not claude: this proves the BOUNDARY, and spends no tokens and no
	// login. The image's own shell, by basename, exactly as Wrap does it.
	script := "printenv SMOKE_SECRET > " + ask + "; pwd > " + filepath.Join(work, "where")
	lch, err := c.Wrap(Launch{Bin: "/bin/sh", Args: []string{"-c", script}, Cwd: work,
		Env: []string{"SMOKE_SECRET=hunter2"}, Writable: []string{ask}})
	if err != nil {
		t.Fatal(err)
	}
	argv := lch.Argv
	if strings.Contains(strings.Join(argv, " "), "hunter2") {
		t.Fatal("a secret VALUE reached the argv")
	}
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Env, cmd.Dir = lch.Env, lch.Dir
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("docker run: %v\n%s\n%s", err, strings.Join(argv, " "), out)
	}
	got, err := os.ReadFile(ask)
	if err != nil {
		t.Fatalf("the container's write never reached the host: %v", err)
	}
	if strings.TrimSpace(string(got)) != "hunter2" {
		t.Fatalf("the secret arrived as %q", got)
	}
	st, err := os.Stat(ask)
	if err != nil {
		t.Fatal(err)
	}
	if !st.Mode().IsRegular() {
		t.Fatalf("%s is %s", ask, st.Mode())
	}
	where, err := os.ReadFile(filepath.Join(work, "where"))
	if err != nil {
		t.Fatalf("the work dir is not writable in the container: %v", err)
	}
	if strings.TrimSpace(string(where)) != work {
		t.Fatalf("the call ran in %q, not the run's own %q: an identity-mapped work dir is the whole point", where, work)
	}
}

// And the measured fact the kill path exists for: killing the docker CLIENT
// does NOT end the container, so a timed-out worker would go on acting on
// the machine after the engine recorded the attempt failed. Launched.Stop
// is what ends it — proven here against the real daemon, both halves.
func TestContainerOutlivesItsClientUntilStopped(t *testing.T) {
	c := NewContainer("", "", "", "", "none")
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	if err := c.Preflight(ctx); err != nil {
		t.Skip("no executor here: ", err)
	}
	lch, err := c.Wrap(Launch{Bin: "/bin/sh", Args: []string{"-c", "sleep 45"}})
	if err != nil {
		t.Fatal(err)
	}
	name := ""
	for i, a := range lch.Argv {
		if a == "--name" && i+1 < len(lch.Argv) {
			name = lch.Argv[i+1]
		}
	}
	cctx, ccancel := context.WithCancel(ctx)
	cmd := exec.CommandContext(cctx, lch.Argv[0], lch.Argv[1:]...)
	cmd.Env = lch.Env
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	up := func() bool {
		out, _ := exec.CommandContext(ctx, "docker", "ps", "--filter", "name="+name, "--format", "{{.Names}}").Output()
		return strings.TrimSpace(string(out)) == name
	}
	for i := 0; i < 50 && !up(); i++ {
		time.Sleep(200 * time.Millisecond)
	}
	if !up() {
		t.Fatal("the container never came up")
	}
	ccancel() // exactly what a timeout does to the call
	cmd.Wait()
	if !up() {
		t.Fatal("the client's death ended the container: the kill path would be unnecessary, and this test is now the wrong test")
	}
	if err := lch.Stop(ctx); err != nil {
		t.Fatalf("stop: %v", err)
	}
	for i := 0; i < 50 && up(); i++ {
		time.Sleep(200 * time.Millisecond)
	}
	if up() {
		t.Fatalf("container %s is still running after Stop", name)
	}
}
